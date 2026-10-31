import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from kasa import Module

from sleep_sentinel import Settings, monitor


class DirectConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_then_read_and_close_without_discovery_or_switching(self):
        device = type("ReadOnlyDevice", (), {})()
        device.modules = {Module.Energy: object()}
        device.disconnect = AsyncMock()
        with (
            patch.dict(
                "os.environ", {"KASA_USERNAME": "test", "KASA_PASSWORD": "secret"}
            ),
            patch(
                "kasa.Device.connect", new_callable=AsyncMock, return_value=device
            ) as connect,
            patch("kasa.Discover.discover_single", new_callable=AsyncMock) as discover,
            patch(
                "sleep_sentinel.read_power",
                new_callable=AsyncMock,
                return_value=(20.0, 120.0),
            ) as read,
            patch(
                "sleep_sentinel.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ),
            patch("sleep_sentinel.announce") as announce,
            patch("sleep_sentinel.AlarmPlayer", autospec=True) as sound,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await monitor(Settings(host="192.0.2.1"), False)
            connect.assert_awaited_once()
            config = connect.call_args.kwargs["config"]
            self.assertEqual(config.host, "192.0.2.1")
            self.assertEqual(config.connection_type.encryption_type.value, "KLAP")
            self.assertEqual(config.connection_type.login_version, 2)
            self.assertEqual(config.credentials.username, "test")
            discover.assert_not_called()
            read.assert_awaited_once_with(device)
            device.disconnect.assert_awaited_once()
            sound.return_value.set_active.assert_awaited_once_with(False)
            sound.return_value.stop.assert_awaited_once()
            self.assertTrue(
                any("20.00 W" in call.args[0] for call in announce.call_args_list)
            )
