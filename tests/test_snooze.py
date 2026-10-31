import asyncio
import os
import unittest
from unittest.mock import AsyncMock, Mock

from alarm_controls import Keyboard, SnoozeAlarm
from sleep_sentinel import Settings


class SnoozeTests(unittest.IsolatedAsyncioTestCase):
    async def test_silences_immediately_extends_and_resumes(self):
        now = [100.0]
        player = Mock(set_active=AsyncMock(), stop=AsyncMock())
        alarm = SnoozeAlarm(300, Mock(), player=player, clock=lambda: now[0])
        await alarm.set_active(True)
        await alarm.snooze()
        player.set_active.assert_awaited_with(False)
        now[0] = 399
        await alarm.set_active(True)
        player.set_active.assert_awaited_with(False)
        await alarm.snooze()
        now[0] = 400
        await alarm.refresh()
        player.set_active.assert_awaited_with(False)
        now[0] = 699
        await alarm.refresh()
        player.set_active.assert_awaited_with(True)
        await alarm.stop()

    async def test_recovery_during_snooze_prevents_restart(self):
        now = [100.0]
        player = Mock(set_active=AsyncMock(), stop=AsyncMock())
        alarm = SnoozeAlarm(300, Mock(), player=player, clock=lambda: now[0])
        await alarm.set_active(True)
        await alarm.snooze()
        await alarm.set_active(False)
        now[0] = 400
        await alarm.refresh()
        player.set_active.assert_awaited_with(False)

    async def test_keyboard_snoozes_without_waiting_for_monitor_poll(self):
        keyboard = Mock(
            open=Mock(return_value=True), space_pressed=Mock(return_value=False)
        )
        player = Mock(set_active=AsyncMock(), stop=AsyncMock())
        alarm = SnoozeAlarm(300, Mock(), player=player, keyboard=keyboard)
        await alarm.set_active(True)
        await alarm.start(True)
        try:
            keyboard.space_pressed.return_value = True
            async with asyncio.timeout(1):
                while not alarm.snoozed:
                    await asyncio.sleep(0.01)
            player.set_active.assert_awaited_with(False)
        finally:
            await alarm.stop()
        keyboard.close.assert_called_once()
        player.stop.assert_awaited_once()

    async def test_redirected_input_does_not_start_keyboard_task(self):
        keyboard = Mock(open=Mock(return_value=False))
        alarm = SnoozeAlarm(300, Mock(), keyboard=keyboard)
        await alarm.start(True)
        self.assertIsNone(alarm.task)
        await alarm.stop()

    def test_snooze_must_be_positive(self):
        for value in (0, -1, float("nan"), True):
            with self.assertRaises(ValueError):
                Settings(snooze_seconds=value).validate()


@unittest.skipIf(os.name == "nt", "POSIX terminal test")
class KeyboardTests(unittest.TestCase):
    def test_space_without_enter_and_terminal_restoration(self):
        import pty
        import termios

        master, slave = pty.openpty()
        try:
            with os.fdopen(os.dup(slave), "r") as stream:
                original = termios.tcgetattr(slave)
                keyboard = Keyboard(stream)
                try:
                    self.assertTrue(keyboard.open())
                    self.assertTrue(termios.tcgetattr(slave)[3] & termios.ISIG)
                    os.write(master, b" ")
                    import select

                    self.assertTrue(select.select([slave], [], [], 1)[0])
                    self.assertTrue(keyboard.space_pressed())
                    self.assertFalse(keyboard.space_pressed())
                finally:
                    keyboard.close()
                restored = termios.tcgetattr(slave)
                # macOS may set the kernel-managed retype-pending flag.
                restored[3] &= ~getattr(termios, "PENDIN", 0)
                original[3] &= ~getattr(termios, "PENDIN", 0)
                self.assertEqual(restored, original)
        finally:
            os.close(master)
            os.close(slave)
