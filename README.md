# Sleep Sentinel

A local power monitor for a Tapo P110M smart plug. It reads power without sending
outlet-switching commands and can sound a continuous reminder after a sustained
drop from running to low power. Network outages trigger a separate alarm.

This is an experimental supplemental reminder, not a medical alarm. Power use
cannot reliably identify mask removal: the blower may keep running. The computer
must remain awake, connected to the network, and able to play audio.

## Requirements

- Python 3.11 or newer. The uv launchers select Python 3.13.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) for the commands below.
- A Tapo P110M with energy monitoring and KLAP v2 support, paired in the Tapo app
  and reachable on the local network. Other models and firmware are not verified.
- Your Tapo account credentials and Third-Party Compatibility enabled in the app.
- macOS (`afplay`), Windows (`winsound`), or Linux with `paplay` or `aplay` installed.

The only direct Python dependency is
[python-kasa](https://github.com/python-kasa/python-kasa), declared in
`pyproject.toml`. `uv.lock` pins it and its dependencies.

## Installation

Download or clone the repository, open a terminal in its directory, and run:

```sh
# macOS / Linux
sh ./uv-local.sh sync --locked
cp config.example.toml config.toml
```

```powershell
# Windows PowerShell
.\uv-local.cmd sync --locked
Copy-Item config.example.toml config.toml
```

For Windows cmd, use `copy config.example.toml config.toml`. Do not overwrite an
existing configuration. Set `host` in `config.toml` to your plug's IP address.
A DHCP reservation can keep it stable. `config.toml` is ignored by Git because it
contains local network details and equipment-specific settings.

The wrappers store virtual environments outside the project:

- macOS: `~/Library/Application Support/Sleep-Sentinel/venv`
- Windows: `%LOCALAPPDATA%\Sleep-Sentinel\venv`
- Linux: `$XDG_DATA_HOME/Sleep-Sentinel/venv`, or `~/.local/share/Sleep-Sentinel/venv`

They work from another directory when called by their full path. Use the wrappers
consistently when syncing the project between operating systems. All copies on
one computer use the same environment. An ordinary local `uv sync --locked` and
`uv run --locked python sleep_sentinel.py` also work, but use a project `.venv`.

## Usage

| Action | macOS / Linux | Windows |
| --- | --- | --- |
| Read power without sound | `sh ./run.sh` | `.\run.cmd` |
| Enable alarms | `sh ./run.sh --monitor` | `.\run.cmd --monitor` |
| Silent offline demo | `sh ./run.sh --demo` | `.\run.cmd --demo` |
| Five-second siren preview | `sh ./run.sh --test-alarm` | `.\run.cmd --test-alarm` |
| Save credentials locally | `sh ./run.sh --save-credentials` | `.\run.cmd --save-credentials` |
| Delete saved credentials | `sh ./run.sh --forget-credentials` | `.\run.cmd --forget-credentials` |
| Show options | `sh ./run.sh --help` | `.\run.cmd --help` |

Use `--config path/to/settings.toml` to select another TOML file. Otherwise the
program reads `config.toml` beside the script. Unspecified settings use the
Python defaults. Demo mode reads configuration but does not connect or play sound;
preview and credential commands do not need a configuration file.

In monitor mode, press Space in the focused terminal to snooze both alarms.
Pressing it again restarts the snooze timer. Readings continue during snooze, and
sound resumes on expiry if an alarm remains active. Keyboard snooze requires an
interactive terminal. Ctrl+C stops monitoring and audio.

The preview uses a strong audio signal; start with moderate system volume and
without headphones. The program does not change system volume or unmute audio.
On macOS, `caffeinate -i sh ./run.sh --monitor` prevents idle system sleep while
the process runs; keep the laptop lid open. No background service is installed.

## Credentials

The program prompts for an account email and hidden password if needed.
`--save-credentials` stores them in `~/.sleep-sentinel/credentials.json`
(`%USERPROFILE%\.sleep-sentinel\credentials.json` on Windows). This is plaintext,
not a password vault. On POSIX, the directory has mode 700 and the file mode 600;
Windows uses inherited home-directory permissions. Keep this file private.

`KASA_USERNAME` and `KASA_PASSWORD` override their respective saved values.
Missing values come from the saved file or interactive prompts. `.env` files are
not loaded automatically. No environment variables are required.

## Configuration and detection

The example thresholds are placeholders. Observe startup, normal running,
standby, and mask removal before choosing values for your equipment. Recheck
after changing equipment or pressure/heater settings. `calibrated = true` only
suppresses the placeholder warning; it does not enable alarms or validate settings.

| Setting | Default | Meaning |
| --- | --- | --- |
| `host` | empty | Plug IP address or hostname |
| `running_watts` | 15 | Minimum power considered running |
| `stopped_watts` | 8 | Maximum power considered stopped; below `running_watts` |
| `arm_seconds` | 30 | Sustained running needed to arm or clear a low-power alarm |
| `alarm_after_seconds` | 20 | Sustained low power needed to alarm once armed |
| `poll_seconds` | 5 | Delay after each reading attempt |
| `request_timeout_seconds` | 10 | Read timeout; initial connection allows three times this |
| `connection_alarm_seconds` | 30 | Outage duration before sounding in monitor mode |
| `repeat_alarm_seconds` | 10 | Interval between console reminders during an alarm |
| `snooze_seconds` | 300 | Duration of each keyboard snooze |
| `calibrated` | false | Whether you have adjusted the thresholds |

An idle load at startup does not trigger a low-power alarm. Intermediate readings
reset pending timers but do not clear a latched alarm. Missing readings and long
sampling gaps reset timers without being treated as zero watts. A sustained
return to running clears the low-power alarm; restarting resets detector state.
Voltage, when available, is informational only.

Connection failures are retried. Authentication errors, unsupported devices, and
invalid power measurements stop monitoring. A connection alarm clears when
readings resume, unless a low-power alarm remains latched. Timing depends on
polling, network latency, and the plug's measurement refresh rate.

## Troubleshooting

Connections use KLAP v2 over port 80 without UDP discovery. Check the configured
address, local network access, and Tapo's Third-Party Compatibility setting under
Me > Third-Party Services (older apps: Me > Tapo Lab).

On macOS, check System Settings > Privacy & Security > Local Network for the
terminal or IDE running the program. Permissions can differ between apps. Check
VPN and routing settings too. Network diagnostics report exception types and OS
error codes without including exception messages or authentication data.

Playback failures stop the program with an error. On Linux, install `paplay` or
`aplay` and check the selected output device. Keep any plug schedules or automatic
shutoff features that could interrupt the monitored equipment disabled.

## Development

```sh
sh ./uv-local.sh run --locked python -m unittest discover -s tests -v
```

On Windows, replace `sh ./uv-local.sh` with `.\uv-local.cmd`. Tests simulate the
plug, audio players, and keyboard; they do not connect to hardware or play sound.
If Ruff is installed, run `ruff check .` and `ruff format --check .`.

The project uses three modules: `sleep_sentinel.py` for configuration and power
monitoring, `alarm_audio.py` for siren playback, and `alarm_controls.py` for snooze
and terminal handling. It does not require a build step.

## Licensing

A project license has not been selected yet. The direct dependency, python-kasa,
is GPL-3.0-or-later. Review its license and those of bundled dependencies before
redistributing an application or environment. The siren is generated locally;
there are no bundled recordings or external media assets.
