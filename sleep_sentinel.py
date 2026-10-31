"""Read-only Tapo power monitor and local audible reminder. Python 3.11+."""

import argparse
import asyncio
import errno
import getpass
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from alarm_audio import AlarmPlayer, preview_alarm
from alarm_controls import SnoozeAlarm


def credentials_path() -> Path:
    """Credentials belong to this user's machine, never the project folder."""
    return Path.home() / ".sleep-sentinel" / "credentials.json"


def load_credentials() -> dict[str, str]:
    path = credentials_path()
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {}
    except (ValueError, UnicodeError):
        raise ValueError(
            "Saved credentials are invalid; run --save-credentials to replace them."
        ) from None
    if not isinstance(data, dict) or any(
        not isinstance(data.get(key), str) or not data[key].strip()
        for key in ("username", "password")
    ):
        raise ValueError(
            "Saved credentials are invalid; run --save-credentials to replace them."
        )
    return data


def save_credentials():
    username = input("Tapo account email: ").strip()
    password = getpass.getpass("Tapo account password (hidden): ")
    if not username or not password.strip():
        raise ValueError("Email and password must both be provided; nothing was saved.")
    path = credentials_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
    # mkstemp creates a private file; atomic replacement avoids partial writes.
    fd, temporary = tempfile.mkstemp(prefix=".credentials-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"username": username, "password": password}, stream)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(
        f"Credentials saved locally to {path}. Future runs will use them automatically."
    )


def get_credentials() -> tuple[str, str]:
    username = os.environ.get("KASA_USERNAME")
    password = os.environ.get("KASA_PASSWORD")
    if not (username and password):
        saved = load_credentials()
        username = username or saved.get("username")
        password = password or saved.get("password")
    username = username or input("Tapo account email: ").strip()
    password = password or getpass.getpass("Tapo account password (hidden): ")
    return username, password


@dataclass
class Settings:
    host: str = ""
    running_watts: float = 15.0
    stopped_watts: float = 8.0
    arm_seconds: float = 30.0
    alarm_after_seconds: float = 20.0
    poll_seconds: float = 5.0
    request_timeout_seconds: float = 10.0
    connection_alarm_seconds: float = 30.0
    repeat_alarm_seconds: float = 10.0
    snooze_seconds: float = 300.0
    calibrated: bool = False

    def validate(self) -> None:
        for name, value in vars(self).items():
            if name in ("host", "calibrated"):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.running_watts <= self.stopped_watts:
            raise ValueError("running_watts must exceed stopped_watts")
        for name in (
            "poll_seconds",
            "request_timeout_seconds",
            "repeat_alarm_seconds",
            "snooze_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not isinstance(self.host, str) or not isinstance(self.calibrated, bool):
            raise ValueError("host must be text and calibrated must be true/false")


class Detector:
    """Arm after sustained running; alarm after sustained low power.

    Intermediate readings reset pending timers but do not clear a latched alarm.
    A sustained return to running clears the alarm. Missing readings cannot
    advance either timer and never count as zero watts.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.armed = False
        self.alarming = False
        self.high_since = None
        self.low_since = None

    def missing(self) -> None:
        self.high_since = self.low_since = None

    def update(self, watts: float, now: float) -> str:
        if not math.isfinite(watts) or watts < 0:
            raise ValueError("Invalid power reading")
        if watts >= self.settings.running_watts:
            self.low_since = None
            if self.high_since is None:
                self.high_since = now
            if now - self.high_since >= self.settings.arm_seconds:
                self.armed = True
                self.alarming = False
        else:
            self.high_since = None
            if self.armed and watts <= self.settings.stopped_watts:
                if self.low_since is None:
                    self.low_since = now
                if now - self.low_since >= self.settings.alarm_after_seconds:
                    self.alarming = True
            else:
                self.low_since = None
        return self.state

    @property
    def state(self):
        if self.alarming:
            return "ALARM: sustained low power"
        return "ARMED" if self.armed else "WAITING for sustained running power"


def announce(message: str) -> None:
    print(
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}  {message}",
        flush=True,
    )


async def read_power(device) -> tuple[float, float | None]:
    from kasa import Module

    energy = device.modules.get(Module.Energy)
    if energy is None:
        raise ValueError(
            "This plug has no energy monitoring (US P125M is unsupported)."
        )
    # Explicit live query avoids the library's periodic module-update cache.
    status = await energy.get_status()
    try:
        watts = status["power"]
    except (KeyError, TypeError, ValueError):
        raise ValueError("Plug returned no valid power measurement") from None
    if (
        isinstance(watts, bool)
        or not isinstance(watts, (int, float))
        or not math.isfinite(watts)
        or watts < 0
    ):
        raise ValueError("Plug returned no valid power measurement")
    try:
        voltage = status["voltage"]
    except (KeyError, TypeError, ValueError):
        voltage = None
    # Voltage is informational; a bad optional value must not stop detection.
    if (
        isinstance(voltage, bool)
        or not isinstance(voltage, (int, float))
        or not math.isfinite(voltage)
        or voltage < 0
    ):
        voltage = None
    return watts, voltage


async def close_device(device):
    if device is not None:
        try:
            await asyncio.wait_for(device.disconnect(), timeout=3)
        except Exception:
            # Cleanup must not hide the original failure or delay shutdown.
            pass


async def connect_plug(settings, username, password):
    from kasa import Credentials, Device, DeviceConfig, DeviceConnectionParameters

    # This adapter targets P110M plugs using KLAP v2, without UDP discovery.
    # Device.connect loads capabilities before the caller checks Module.Energy.
    config = DeviceConfig(
        host=settings.host,
        credentials=Credentials(username, password),
        timeout=settings.request_timeout_seconds,
        connection_type=DeviceConnectionParameters.from_values(
            "SMART.TAPOPLUG", "KLAP", login_version=2, https=False, http_port=80
        ),
    )
    return await Device.connect(config=config)


def connection_error_detail(error):
    """Expose exception types and OS codes, never messages or request data."""
    parts = []
    codes = set()
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        part = type(error).__name__
        code = getattr(error, "errno", None)
        if isinstance(code, int):
            codes.add(code)
            part += f" errno={code} ({errno.errorcode.get(code, 'UNKNOWN')})"
        parts.append(part)
        error = error.__cause__ or error.__context__
    detail = " -> ".join(parts)
    if sys.platform == "darwin" and codes.intersection(
        {errno.EPERM, errno.EACCES, errno.EHOSTUNREACH, errno.ENETUNREACH}
    ):
        detail += (
            ". On macOS, check System Settings > Privacy & Security > Local Network "
            "for the terminal or IDE running this command; also check VPN/routing"
        )
    return detail


async def monitor(settings: Settings, enable_alarm: bool) -> None:
    from kasa import Module
    from kasa.exceptions import AuthenticationError, UnsupportedDeviceError

    if not settings.host:
        raise ValueError("Set host to the plug's IP address in config.toml first.")
    username, password = get_credentials()
    detector = Detector(settings)
    alarm = SnoozeAlarm(settings.snooze_seconds, announce, player=AlarmPlayer())
    device = None
    failed_since = None
    last_alarm = float("-inf")
    previous_sample = None
    announce("MONITOR with sound" if enable_alarm else "READINGS ONLY (no sound)")
    if not settings.calibrated:
        announce(
            "PLACEHOLDER thresholds: calibrate running/standby readings before relying on detection."
        )
    try:
        await alarm.start(enable_alarm)
        while True:
            try:
                stage = "connecting/authenticating and loading device capabilities"
                if device is None:
                    announce("Connecting directly to the plug (KLAP v2)...")
                    # Initial authentication and capability queries take several requests.
                    async with asyncio.timeout(3 * settings.request_timeout_seconds):
                        device = await connect_plug(settings, username, password)
                    if Module.Energy not in device.modules:
                        raise ValueError(
                            "This plug does not support energy monitoring."
                        )
                    announce("Connected; energy monitoring available")
                stage = "reading live power"
                async with asyncio.timeout(settings.request_timeout_seconds):
                    watts, voltage = await read_power(device)
            except UnsupportedDeviceError:
                raise ValueError(
                    "The plug's connection protocol is unsupported by the installed python-kasa. "
                    "In the Tapo app check Me > Third-Party Services > Third-Party Compatibility "
                    "(older apps: Me > Tapo Lab), then retry. If already enabled, report this "
                    "compatibility error. This error does not mean your password is wrong."
                ) from None
            except AuthenticationError:
                raise ValueError(
                    "The plug rejected authentication. To replace saved credentials, run --save-credentials. Verify the TP-Link/Tapo account email "
                    "and password and the app's Third-Party Compatibility setting."
                ) from None
            except ValueError:
                raise
            except Exception as error:
                now = time.monotonic()
                detector.missing()
                previous_sample = None
                if failed_since is None:
                    failed_since = now
                # Do not print exception bodies: authentication failures may contain sensitive data.
                announce(
                    f"READING UNAVAILABLE while {stage} ({connection_error_detail(error)}); retrying. Check Wi-Fi and the plug IP address."
                )
                reason = "CONNECTION ALARM: readings unavailable"
                should_sound = (
                    now - failed_since >= settings.connection_alarm_seconds
                    or detector.alarming
                )
                await close_device(device)
                device = None
            else:
                now = time.monotonic()
                if previous_sample is not None and now - previous_sample > max(
                    3 * settings.poll_seconds, 15
                ):
                    detector.missing()  # Computer sleep/stall cannot count toward a sustained reading.
                previous_sample = now
                if failed_since is not None:
                    announce("Connection restored")
                failed_since = None
                state = detector.update(watts, now)
                voltage_text = f" | {voltage:.1f} V" if voltage is not None else ""
                announce(f"{watts:.2f} W{voltage_text} | {state}")
                should_sound = detector.alarming
                reason = state
            await alarm.set_active(should_sound and enable_alarm)
            if (
                should_sound
                and enable_alarm
                and not alarm.snoozed
                and now - last_alarm >= settings.repeat_alarm_seconds
            ):
                announce(reason + "; Space to snooze; Ctrl+C to stop")
                last_alarm = now
            elif not should_sound:
                last_alarm = float("-inf")
            await asyncio.sleep(settings.poll_seconds)
    finally:
        try:
            await alarm.stop()
        finally:
            await close_device(device)


def demo(settings: Settings) -> None:
    detector = Detector(settings)
    timeline = [
        (0, settings.stopped_watts),
        (5, settings.running_watts + 5),
        (5 + settings.arm_seconds, settings.running_watts + 5),
        (10 + settings.arm_seconds, settings.stopped_watts),
        (
            10 + settings.arm_seconds + settings.alarm_after_seconds,
            settings.stopped_watts,
        ),
    ]
    for seconds, watts in timeline:
        print(
            f"SIMULATED t={seconds:g}s {watts:g} W: {detector.update(watts, seconds)}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("config.toml")
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--monitor",
        action="store_true",
        help="Enable audible low-power and connection alarms",
    )
    mode.add_argument(
        "--demo", action="store_true", help="Simulated readings; no device or sound"
    )
    mode.add_argument(
        "--test-alarm",
        action="store_true",
        help="Preview the siren for five seconds; no device",
    )
    mode.add_argument(
        "--save-credentials",
        action="store_true",
        help="Save or replace Tapo credentials in your local home folder",
    )
    mode.add_argument(
        "--forget-credentials",
        action="store_true",
        help="Delete locally saved Tapo credentials",
    )
    args = parser.parse_args()
    try:
        if args.save_credentials:
            save_credentials()
            return
        if args.forget_credentials:
            credentials_path().unlink(missing_ok=True)
            print(
                "Locally saved credentials removed. KASA environment variables, if set, still apply."
            )
            return
        if args.test_alarm:
            asyncio.run(preview_alarm())
            return
        with args.config.open("rb") as stream:
            settings = Settings(**tomllib.load(stream))
        settings.validate()
        if args.demo:
            demo(settings)
        else:
            asyncio.run(monitor(settings, args.monitor))
    except KeyboardInterrupt:
        print("\nStopped.")
    except (
        ValueError,
        TypeError,
        OSError,
        ImportError,
        subprocess.SubprocessError,
    ) as error:
        parser.exit(1, f"Setup error: {error}\n")


if __name__ == "__main__":
    main()
