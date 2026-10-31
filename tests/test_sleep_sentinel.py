import unittest
from unittest.mock import AsyncMock

from sleep_sentinel import Detector, Settings, read_power


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.detector = Detector(Settings())

    def arm(self):
        self.detector.update(20, 0)
        self.detector.update(20, 30)
        self.assertTrue(self.detector.armed)

    def test_idle_start_does_not_alarm(self):
        for now in (0, 30, 1000):
            self.detector.update(2, now)
        self.assertFalse(self.detector.alarming)
        self.assertFalse(self.detector.armed)

    def test_sustained_drop_and_recovery(self):
        self.arm()
        self.detector.update(2, 35)
        self.detector.update(2, 54)
        self.assertFalse(self.detector.alarming)
        self.detector.update(2, 55)
        self.assertTrue(self.detector.alarming)
        self.detector.update(20, 60)
        self.detector.update(20, 89)
        self.assertTrue(self.detector.alarming)
        self.detector.update(20, 90)
        self.assertFalse(self.detector.alarming)

    def test_brief_dip_and_middle_band_reset_timer(self):
        self.arm()
        self.detector.update(2, 35)
        self.detector.update(10, 50)
        self.detector.update(2, 55)
        self.detector.update(2, 60)
        self.assertFalse(self.detector.alarming)

    def test_outage_cannot_advance_low_timer(self):
        self.arm()
        self.detector.update(2, 35)
        self.detector.missing()
        self.detector.update(2, 1000)
        self.assertTrue(self.detector.armed)
        self.assertFalse(self.detector.alarming)
        self.detector.update(2, 1020)
        self.assertTrue(self.detector.alarming)
        self.detector.missing()
        self.assertTrue(self.detector.alarming)

    def test_interrupted_running_cannot_arm(self):
        self.detector.update(20, 0)
        self.detector.update(10, 20)
        self.detector.update(20, 30)
        self.assertFalse(self.detector.armed)
        self.detector.missing()
        self.detector.update(20, 1000)
        self.assertFalse(self.detector.armed)

    def test_invalid_settings(self):
        for changes in (
            {"running_watts": 8},
            {"poll_seconds": 0},
            {"arm_seconds": -1},
            {"stopped_watts": float("nan")},
        ):
            with self.assertRaises(ValueError):
                Settings(**changes).validate()


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_read_without_switching(self):
        from kasa import Module
        from kasa.emeterstatus import EmeterStatus

        energy = type("Energy", (), {})()
        energy.get_status = AsyncMock(
            return_value=EmeterStatus({"power_mw": 12340, "voltage_mv": 120000})
        )
        # The fake deliberately has no switching methods.
        device = type("Device", (), {"modules": {Module.Energy: energy}})()
        self.assertEqual(await read_power(device), (12.34, 120))
        energy.get_status.assert_awaited_once()

    async def test_voltage_optional(self):
        from kasa import Module
        from kasa.emeterstatus import EmeterStatus

        energy = type("Energy", (), {})()
        energy.get_status = AsyncMock(return_value=EmeterStatus({"power_mw": 2000}))
        device = type("Device", (), {"modules": {Module.Energy: energy}})()
        self.assertEqual(await read_power(device), (2, None))

    async def test_unsupported_plug(self):
        device = type("Device", (), {"modules": {}})()
        with self.assertRaisesRegex(ValueError, "no energy monitoring"):
            await read_power(device)


class ReadingValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_power_stops_with_safe_error(self):
        from unittest.mock import Mock

        from kasa import Module
        from kasa.emeterstatus import EmeterStatus

        for data in (
            {},
            {"power": None},
            {"power": True},
            {"power": -1},
            {"power": float("nan")},
            {"power": float("inf")},
            {"power": "private data"},
            {"power_mw": "private data"},
        ):
            with self.subTest(data=data):
                energy = Mock(get_status=AsyncMock(return_value=EmeterStatus(data)))
                device = Mock(modules={Module.Energy: energy})
                with self.assertRaisesRegex(ValueError, "no valid power") as caught:
                    await read_power(device)
                self.assertNotIn("private data", str(caught.exception))

    async def test_invalid_optional_voltage_does_not_interrupt_power(self):
        from unittest.mock import Mock

        from kasa import Module
        from kasa.emeterstatus import EmeterStatus

        for value in (None, True, -1, float("nan"), float("inf"), "bad voltage"):
            with self.subTest(value=value):
                energy = Mock(
                    get_status=AsyncMock(
                        return_value=EmeterStatus({"power": 20, "voltage": value})
                    )
                )
                device = Mock(modules={Module.Energy: energy})
                self.assertEqual(await read_power(device), (20, None))


class DemoTests(unittest.TestCase):
    def test_demo_reaches_alarm_with_low_custom_threshold(self):
        from contextlib import redirect_stdout
        from io import StringIO

        from sleep_sentinel import demo

        output = StringIO()
        with redirect_stdout(output):
            demo(Settings(stopped_watts=0, running_watts=1))
        self.assertIn("ALARM: sustained low power", output.getvalue().splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
