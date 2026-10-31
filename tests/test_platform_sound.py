import asyncio
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import alarm_audio as audio
from sleep_sentinel import Settings, monitor


class WaveTests(unittest.TestCase):
    def test_wave_is_four_seconds_with_strong_unclipped_signal(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "siren.wav"
            audio.write_siren(path)
            with wave.open(str(path), "rb") as stream:
                self.assertEqual(
                    (
                        stream.getnchannels(),
                        stream.getsampwidth(),
                        stream.getframerate(),
                    ),
                    (1, 2, 44100),
                )
                self.assertEqual(stream.getnframes(), 44100 * 4)
                samples = array("h", stream.readframes(stream.getnframes()))
            if sys.byteorder != "little":
                samples.byteswap()
            self.assertGreater(max(samples), 20000)
            self.assertLess(max(abs(x) for x in samples), 32767)
            self.assertEqual(samples[0], 0)
            self.assertEqual(samples[-1], 0)

            # Zero crossings distinguish the two alternating pitches.
            def crossings(start, end):
                section = samples[start:end]
                return sum(a <= 0 < b for a, b in zip(section, section[1:]))

            self.assertAlmostEqual(crossings(0, 11025), 225, delta=2)
            self.assertAlmostEqual(crossings(11025, 22050), 375, delta=2)


class AudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_does_not_block_or_duplicate_and_stop_cleans_up(self):
        started, stopped = asyncio.Event(), asyncio.Event()

        async def play():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        with patch.object(audio, "sound_alarm", side_effect=play) as sound:
            player = audio.AlarmPlayer()
            await player.set_active(True)
            await asyncio.wait_for(started.wait(), 1)
            await player.set_active(True)
            sound.assert_called_once()
            await player.set_active(False)
            self.assertTrue(stopped.is_set())
            self.assertIsNone(player.task)
            await player.stop()

    async def test_playback_failure_is_surfaced(self):
        with patch.object(audio, "sound_alarm", side_effect=OSError("audio failure")):
            player = audio.AlarmPlayer()
            await player.set_active(True)
            await asyncio.sleep(0)
            with self.assertRaisesRegex(OSError, "audio failure"):
                await player.set_active(True)
            with self.assertRaises(OSError):
                await player.stop()

    async def test_mac_cancellation_terminates_process_and_removes_wave(self):
        started = asyncio.Event()
        process = Mock(returncode=None)

        async def wait():
            if process.returncode is not None:
                return process.returncode
            started.set()
            await asyncio.Future()

        process.wait = AsyncMock(side_effect=wait)
        process.terminate.side_effect = lambda: setattr(process, "returncode", -15)
        with (
            patch.object(audio.sys, "platform", "darwin"),
            patch.object(
                audio.asyncio, "create_subprocess_exec", return_value=process
            ) as launch,
        ):
            task = asyncio.create_task(audio.sound_alarm())
            await asyncio.wait_for(started.wait(), 2)
            path = Path(launch.call_args.args[-1])
            self.assertTrue(path.exists())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            process.terminate.assert_called_once()
            self.assertFalse(path.exists())

    async def test_mac_nonzero_exit_fails(self):
        process = Mock(returncode=1, wait=AsyncMock(return_value=1))
        with (
            patch.object(audio.sys, "platform", "darwin"),
            patch.object(audio.asyncio, "create_subprocess_exec", return_value=process),
        ):
            with self.assertRaisesRegex(OSError, "playback failed"):
                await audio.sound_alarm()

    async def test_windows_uses_looping_sound_and_stops_on_cancel(self):
        sound = Mock(SND_FILENAME=1, SND_ASYNC=2, SND_LOOP=4)
        started = asyncio.Event()
        sound.PlaySound.side_effect = lambda *args: started.set()
        # Build the temporary Path on the host OS before mocking os.name.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "siren.wav"
        with (
            patch.object(audio, "Path", return_value=path.parent),
            patch.object(audio.os, "name", "nt"),
            patch.dict("sys.modules", winsound=sound),
        ):
            task = asyncio.create_task(audio.sound_alarm())
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(sound.PlaySound.call_args_list[0].args, (str(path), 7))
        self.assertEqual(sound.PlaySound.call_args_list[-1].args, (None, 0))

    async def test_monitor_continues_reading_during_alarm_and_stops_on_recovery(self):
        from kasa import Module

        device = Mock(modules={Module.Energy: object()}, disconnect=AsyncMock())
        with (
            patch("sleep_sentinel.get_credentials", return_value=("test", "test")),
            patch("sleep_sentinel.connect_plug", return_value=device),
            patch(
                "sleep_sentinel.read_power",
                side_effect=[(20, None), (2, None), (2, None), (20, None)],
            ) as read,
            patch(
                "sleep_sentinel.asyncio.sleep",
                side_effect=[None, None, None, asyncio.CancelledError],
            ),
            patch("sleep_sentinel.announce"),
            patch("sleep_sentinel.AlarmPlayer", autospec=True) as player,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await monitor(
                    Settings(host="192.0.2.1", arm_seconds=0, alarm_after_seconds=0),
                    True,
                )
        self.assertEqual(read.await_count, 4)
        self.assertEqual(
            [call.args[0] for call in player.return_value.set_active.await_args_list],
            [False, True, True, False],
        )
        player.return_value.stop.assert_awaited_once()
        device.disconnect.assert_awaited_once()
