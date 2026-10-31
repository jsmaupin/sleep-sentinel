import unittest
from unittest.mock import AsyncMock, patch

from kasa.exceptions import AuthenticationError, UnsupportedDeviceError

from sleep_sentinel import Settings, monitor


class ConnectionErrorTests(unittest.IsolatedAsyncioTestCase):
    async def check_error(self, error, expected):
        with (
            patch.dict(
                "os.environ", {"KASA_USERNAME": "test", "KASA_PASSWORD": "secret"}
            ),
            patch("kasa.Device.connect", new_callable=AsyncMock) as discover,
            patch("sleep_sentinel.announce"),
            patch("sleep_sentinel.AlarmPlayer", autospec=True) as sound,
        ):
            discover.side_effect = error
            with self.assertRaisesRegex(ValueError, expected) as caught:
                await monitor(Settings(host="192.0.2.1"), True)
            self.assertNotIn("secret", str(caught.exception))
            discover.assert_awaited_once()
            sound.return_value.set_active.assert_not_awaited()
            sound.return_value.stop.assert_awaited_once()

    async def test_unsupported_stops_without_retry_or_alarm(self):
        await self.check_error(
            UnsupportedDeviceError("secret"), "protocol is unsupported"
        )

    async def test_authentication_stops_without_retry_or_alarm(self):
        await self.check_error(AuthenticationError("secret"), "rejected authentication")
