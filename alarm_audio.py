"""Continuous local siren, with cancellable playback independent of polling."""

import asyncio
import math
import os
import shutil
import sys
import tempfile
import wave
from array import array
from contextlib import suppress
from pathlib import Path


def write_siren(path: Path) -> None:
    """Four-second, near-full-scale alternating 900/1500 Hz harmonic siren."""
    rate = 44100
    samples = array("h")
    phase = 0.0
    for i in range(rate * 4):
        frequency = 900 if (i // (rate // 4)) % 2 == 0 else 1500
        phase += 2 * math.pi * frequency / rate
        # Harmonics make the sound harsher; a short edge ramp prevents clicks.
        envelope = min(1.0, i / (rate * 0.005), (rate * 4 - 1 - i) / (rate * 0.005))
        value = (math.sin(phase) + 0.3 * math.sin(3 * phase)) / 1.3
        samples.append(round(32767 * 0.95 * envelope * value))
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())


async def sound_alarm() -> None:
    """Play continuously until cancelled; release the audio device on exit."""
    with tempfile.TemporaryDirectory(prefix="sleep-sentinel-alarm-") as folder:
        path = Path(folder) / "siren.wav"
        write_siren(path)
        if os.name == "nt":
            import winsound

            try:
                winsound.PlaySound(
                    str(path),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
                )
                await asyncio.Future()
            finally:
                winsound.PlaySound(None, 0)
        else:
            if sys.platform == "darwin":
                command = ["/usr/bin/afplay", "-v", "1", str(path)]
            else:
                player = shutil.which("paplay") or shutil.which("aplay")
                if not player:
                    raise OSError(
                        "Siren requires paplay or aplay on Linux. Install an audio player first."
                    )
                command = [player, str(path)]
            while True:
                process = await asyncio.create_subprocess_exec(*command)
                try:
                    code = await process.wait()
                    if code:
                        raise OSError(
                            f"Siren playback failed (exit {code}); check your audio output."
                        )
                finally:
                    if process.returncode is None:
                        with suppress(ProcessLookupError):
                            process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), timeout=2)
                        except TimeoutError:
                            with suppress(ProcessLookupError):
                                process.kill()
                            await process.wait()


class AlarmPlayer:
    def __init__(self):
        self.task: asyncio.Task[None] | None = None

    async def set_active(self, active):
        if self.task is not None and self.task.done():
            self.task.result()  # Surface playback failures rather than failing silently.
            self.task = None
        if active and self.task is None:
            self.task = asyncio.create_task(sound_alarm())
        elif not active:
            await self.stop()

    async def stop(self):
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def preview_alarm():
    print(
        "Five-second siren preview. Check output volume first; Ctrl+C stops it.",
        flush=True,
    )
    try:
        async with asyncio.timeout(5):
            await sound_alarm()
    except TimeoutError:
        pass
