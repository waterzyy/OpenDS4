<div align="center">

<img src="assets/logo.svg" alt="OpenDS4 logo" width="108" height="108">

# OpenDS4

**DualShock 4 to Xbox 360 controller mapper for Windows.**

Open source, fully offline, and readable from top to bottom.

[![CI](https://github.com/waterzyy/OpenDS4/actions/workflows/ci.yml/badge.svg)](https://github.com/waterzyy/OpenDS4/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/badge/code%20style-ruff-4c8bf5)](https://docs.astral.sh/ruff/)
[![License: MIT](https://img.shields.io/badge/license-MIT-4c8bf5)](LICENSE)

[Quick start](#quick-start) · [Features](#features) · [Documentation](docs/) · [Contributing](CONTRIBUTING.md)

</div>

---

OpenDS4 turns a Sony DualShock 4 into a virtual Xbox 360 controller on Windows. It is built for people who want a controller mapper they can actually read: no binaries, no installers, no accounts, and no network traffic. The whole application is a handful of Python files you can audit in one sitting.

It ships as source code on purpose. You can see exactly which HID reports are parsed, how the sticks are scaled, and what gets sent to the virtual controller. An automated test suite verifies that the application contains no network calls, no telemetry, no auto-updater, and no `subprocess` or `eval` usage.

## Features

| Area | What you get |
| --- | --- |
| **Hardware** | DualShock 4 v1 (`054C:05C4`), v2 (`054C:09CC`), and the official USB Wireless Adapter (`054C:0BA0`), over USB or Bluetooth. Bluetooth extended mode and IEEE CRC-32 packet verification are handled automatically. |
| **Input** | Radial deadzones, anti-deadzone compensation, Linear / Precision / Fast response curves, Y-axis inversion, stick swap, and per-trigger deadzones. |
| **Output** | Virtual Xbox 360 controller through Nefarius ViGEmBus, with differential state updates so only changed buttons are re-sent. |
| **Lightbar** | 12 modes: Static Color, Spectrum Cycle, Cyberpunk, Breathing, Color Pulse, Ember Flame, Aurora Wave, Synthwave, Strobe, Trigger Reactive, Rumble Flash, and Battery Meter. |
| **Rumble** | XInput force-feedback passthrough, independent heavy/light motor scaling, optional proportional L2/R2 trigger haptics, and built-in motor test patterns. |
| **Touchpad** | Optional mouse mode: 1-finger glide, 2-finger scroll, tap to click, and physical click-and-drag. Off by default so games are untouched. |
| **Desktop** | Dark high-DPI interface, live stick radars and trigger bars, sensor readout, event log, and a system tray icon with quick settings. |
| **Profiles** | Any number of named profiles stored as plain JSON, with four built-in presets to start from. |
| **Privacy** | Zero network access by design, enforced by the test suite. No accounts, no cloud, no telemetry. |

## Requirements

- **Windows 10 or Windows 11** (x64)
- **Python 3.10 or newer** with Tcl/Tk enabled
- **[Nefarius ViGEmBus](https://github.com/nefarius/ViGEmBus/releases)** — the virtual Xbox 360 driver
- A **DualShock 4** controller, connected by USB cable or paired over Bluetooth
- Optional: **[Nefarius HidHide](https://github.com/nefarius/HidHide/releases)** to hide the physical pad from games that read both DirectInput and XInput

## Quick start

1. **Install ViGEmBus.** Download and run `ViGEmBus_1.22.0_x64_x86_arm64.exe` from the [official release page](https://github.com/nefarius/ViGEmBus/releases/tag/v1.22.0). This is a one-time system driver install.

2. **Install Python 3.10+** from [python.org](https://www.python.org/downloads/). Keep **tcl/tk and IDLE** enabled during setup, and enable **Add python.exe to PATH**.

3. **Get the source.**

   ```bash
   git clone https://github.com/waterzyy/OpenDS4.git
   cd OpenDS4
   ```

   You can also download the repository as a ZIP from GitHub and extract it anywhere writable.

4. **Run `run_windows.bat`.** The first launch creates a local `.venv` folder and installs `hidapi` and `vgamepad`. Later launches verify both packages in milliseconds and open the app immediately.

5. **Connect the controller.** Mapping starts automatically and keeps retrying until a DualShock 4 appears. The header shows the transport, poll rate, and battery level; use **Stop** to pause and **Start** to resume.

### Command line

```text
run_windows.bat               Launch normally
run_windows.bat --minimized   Start in the system tray
run_windows.bat --console     Keep a console window open for debugging
```

You can also run the app directly with the virtual environment's interpreter:

```powershell
.venv\Scripts\python.exe app.py --minimized
```

### What the launcher does

- Finds `py -3` or `python` and checks that it is Python 3.10 or newer.
- Checks that `tkinter` is importable.
- Creates `.venv` on first run and installs `requirements.txt` into it.
- Warns if ViGEmBus is not detected in Program Files.
- Starts the app with `pythonw.exe` (no console window) or `python.exe` as a fallback.

## Interface

| Page | Purpose |
| --- | --- |
| **Monitor** | Live stick radars, trigger bars, button state, touchpad preview, battery and sensor telemetry. |
| **Sticks** | Deadzones, anti-deadzone, sensitivity, response curves, Y inversion, and stick swap, with a live curve graph. |
| **Lightbar** | Mode, color, speed, brightness, and low-battery warning behavior. |
| **Vibration** | Master strength, heavy/light motor balance, trigger haptics, and test buttons for each motor. |
| **Mapping & Profiles** | Remap every DS4 button to an Xbox target, switch profiles, save, delete, and copy the Python path for HidHide. |

The header always shows the connection state, transport, poll rate, and battery. **Simulator** animates a virtual controller so you can preview lightbar and rumble settings without hardware, and **Stop** pauses mapping without closing the window.

## Profiles

Profiles live in the `profiles/` folder next to `ds4_profile.py`. They are plain UTF-8 JSON and are created automatically on first launch.

```json
{
  "name": "Shooter Precision",
  "left_deadzone": 0.06,
  "right_deadzone": 0.05,
  "right_curve": "precision",
  "trigger_rumble": true,
  "lightbar_mode": "triggers"
}
```

Four presets are included: **Default**, **Shooter Precision**, **Cyberpunk**, and **Racing Haptics**. Copy any JSON file to back it up, or drop a file into `profiles/` to import it. See [docs/profiles.md](docs/profiles.md) for the full settings reference and accepted ranges.

> `profiles/` is ignored by Git because it contains personal settings. Nothing in it is required to run the app.

## Touchpad mouse

Touchpad mouse is **disabled by default**, so the physical touchpad keeps acting as the Xbox **Back** button. Enable it from the Monitor page, the Mapping page, or the tray menu.

| Gesture | Action |
| --- | --- |
| 1-finger glide | Move the cursor |
| 2-finger vertical slide | Scroll |
| 1-finger tap | Left click |
| 2-finger tap | Right click |
| Physical press (left side) | Hold left button for click-and-drag |
| Physical press (right side) | Hold right button for click-and-drag |

Cursor speed and tap-to-click are configurable per profile. Mouse input is sent with Win32 `SendInput`, so no additional packages are needed.

## System tray

Minimizing the window (or clicking **Minimize**) sends OpenDS4 to the notification area. The tray icon shows the current lightbar color and offers right-click access to:

- Restore the window
- Switch the active profile
- Switch the lightbar mode
- Toggle touchpad mouse, vibration, and trigger haptics
- Pause or resume the mapper
- Quit

While minimized, the UI stops redrawing and the process stays idle, so the tray costs almost no CPU.

## Avoiding double input

Some games and split-screen tools read both raw DirectInput and the virtual XInput device, which makes one press register twice. To prevent it:

1. Install **[Nefarius HidHide](https://github.com/nefarius/HidHide/releases)**.
2. Open **Mapping & Profiles** in OpenDS4 and click **Copy Python Path**.
3. In the HidHide Configuration Client, add that `.venv\Scripts\pythonw.exe` (or `python.exe`) path under **Applications**, so OpenDS4 keeps access to the physical pad.
4. Under **Devices**, check **Sony Wireless Controller** and enable **Enable device hiding**.

## Troubleshooting

Start with [docs/troubleshooting.md](docs/troubleshooting.md). The most common cases:

- **The game sees no controller.** Install ViGEmBus, restart the PC, and check that the virtual Xbox 360 pad appears in `joy.cpl`.
- **The controller is not detected.** Try another USB cable or port, or re-pair Bluetooth. OpenDS4 auto-detects both transports and retries every 500 ms.
- **Every press registers twice.** Configure HidHide as described above.
- **Bluetooth reports look truncated.** OpenDS4 reads feature report `0x02` to switch the pad to full 78-byte extended reports and validates every packet with CRC-32.
- **The window does not open.** Run `run_windows.bat --console` and read the log. Tcl/Tk missing is the usual cause.

## Development

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
python -m pip install -e ".[dev]"

python -m pytest -v               # unit and safety tests
python -m ruff check .            # lint
python -m ruff format --check .   # formatting
```

The tests cover USB and Bluetooth packet parsing, CRC-32 validation, deadzone and curve math, output report builders, rumble mixing, profile persistence, tray menu generation, and offline safety across every Python file. They run on Windows in CI, where tkinter is available.

### Project layout

| Path | Description |
| --- | --- |
| `app.py` | Dark high-DPI desktop interface built on standard `tkinter`. |
| `ds4_engine.py` | Non-blocking HID input loop, decoupled output worker, ViGEmBus bridge, 250 Hz simulator. |
| `ds4_profile.py` | Profile model, button remapping, rumble mixer, lightbar modes, atomic JSON storage. |
| `ds4_protocol.py` | HID report parser, CRC-32 validation and signing, stick and trigger math, output report builders. |
| `ds4_touchpad.py` | Optional touchpad-to-mouse gesture processor using Win32 `SendInput`. |
| `ds4_tray.py` | Pure-ctypes system tray icon (`Shell_NotifyIconW`), icon renderer, and right-click menu. |
| `tests/test_ds4.py` | Unit and security test suite. |
| `docs/` | Architecture, profile reference, and troubleshooting. |

See [docs/architecture.md](docs/architecture.md) for the data flow, threading model, and protocol details.

## Security and privacy

OpenDS4 never opens a socket. The test suite parses every Python file with `ast` and fails if it imports networking modules (`socket`, `urllib`, `http`, `requests`, and so on), calls `subprocess`, or uses `eval`, `exec`, `compile`, or `__import__`. There is no updater to disable and nothing to opt out of.

The only outbound connection in the project is the one-time `pip install` performed by `run_windows.bat` on first launch.

## Contributing

Bug reports, small fixes, and well-scoped features are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request, and please keep the offline guarantee intact: changes that introduce network access, subprocess execution, or dynamic code evaluation will not be merged.

## License

Released under the [MIT License](LICENSE).

## Acknowledgements

- **[ViGEmBus](https://github.com/nefarius/ViGEmBus)** by Nefarius Software Solutions — virtual Xbox 360 controller emulation.
- **[HidHide](https://github.com/nefarius/HidHide)** by Nefarius Software Solutions — device hiding for clean single-input setups.
- **[hidapi](https://github.com/libusb/hidapi)** and its Python bindings — cross-platform HID access.
- **[vgamepad](https://github.com/yannbouteiller/vgamepad)** — XInput virtual gamepad client.

DualShock is a trademark of Sony Interactive Entertainment. OpenDS4 is an independent project and is not affiliated with, endorsed by, or sponsored by Sony.
