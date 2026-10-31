"""Responsive terminal snooze controls, independent of network requests."""

import asyncio
import os
import select
import sys
import time
from contextlib import suppress

from alarm_audio import AlarmPlayer


class Keyboard:
    def __init__(self, stream=None):
        self.stream = sys.stdin if stream is None else stream
        self.saved = None
        self.fd = None

    def open(self):
        if not self.stream.isatty():
            return False
        if os.name != "nt":
            import termios

            self.fd = self.stream.fileno()
            self.saved = termios.tcgetattr(self.fd)
            mode = termios.tcgetattr(self.fd)
            # Preserve signals (Ctrl+C) and output processing, but read each key.
            mode[3] &= ~(termios.ICANON | termios.ECHO)
            mode[6][termios.VMIN] = 1
            mode[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, mode)
        return True

    def space_pressed(self):
        if os.name == "nt":
            import msvcrt

            pressed = False
            while msvcrt.kbhit():
                key = msvcrt.getwch()
                if key in ("\x00", "\xe0"):
                    msvcrt.getwch()
                else:
                    pressed |= key == " "
            return pressed
        pressed = False
        while select.select([self.fd], [], [], 0)[0]:
            data = os.read(self.fd, 1024)
            if not data:
                break
            pressed |= b" " in data
        return pressed

    def close(self):
        if self.saved is not None:
            import termios

            termios.tcsetattr(self.fd, termios.TCSANOW, self.saved)
            self.saved = None


class SnoozeAlarm:
    def __init__(
        self, seconds, announce, player=None, clock=time.monotonic, keyboard=None
    ):
        self.seconds = seconds
        self.announce = announce
        self.player = player if player is not None else AlarmPlayer()
        self.clock = clock
        self.keyboard = keyboard if keyboard is not None else Keyboard()
        self.requested = False
        self.until = 0.0
        self.task = None
        self.lock = asyncio.Lock()

    @property
    def snoozed(self):
        return self.clock() < self.until

    async def start(self, enabled):
        if enabled:
            if self.keyboard.open():
                self.announce(
                    f"Space: snooze for {self.seconds / 60:g} minutes; Ctrl+C: stop"
                )
                self.task = asyncio.create_task(self.watch_keyboard())
            else:
                self.announce(
                    "Space-bar snooze unavailable: run in an interactive terminal."
                )

    async def watch_keyboard(self):
        while True:
            if self.keyboard.space_pressed():
                await self.snooze()
            await self.refresh()
            await asyncio.sleep(0.05)

    async def snooze(self):
        async with self.lock:
            self.until = self.clock() + self.seconds
            await self.player.set_active(False)
        self.announce(
            f"SNOOZED for {self.seconds / 60:g} minutes; monitoring continues"
        )

    async def refresh(self):
        async with self.lock:
            await self.player.set_active(self.requested and not self.snoozed)

    async def set_active(self, active):
        if self.task is not None and self.task.done():
            self.task.result()
        self.requested = active
        await self.refresh()

    async def stop(self):
        try:
            if self.task is not None:
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
        finally:
            try:
                self.keyboard.close()
            finally:
                await self.player.stop()
