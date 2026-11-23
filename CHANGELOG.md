# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-11-23

### Added

- DualShock 4 support over USB and Bluetooth, including v1, v2, and the official USB Wireless Adapter.
- Automatic Bluetooth extended mode activation and CRC-32 packet verification.
- Virtual Xbox 360 (XInput) output through Nefarius ViGEmBus with differential state updates.
- Configurable deadzones, anti-deadzone, sensitivity, response curves, Y-axis inversion, and stick swap.
- Twelve lightbar modes with per-profile color, speed, and brightness.
- XInput rumble passthrough with independent heavy/light scaling and optional trigger haptics.
- Optional touchpad-to-mouse mode with glide, scroll, tap-to-click, and click-and-drag.
- Named profiles stored as JSON, with four built-in presets.
- Dark high-DPI desktop interface with live stick, trigger, touchpad, and sensor readouts.
- Windows system tray integration with quick settings and minimize-to-tray.
- Simulator mode for previewing lightbar and rumble behavior without hardware.
- Automated test suite covering protocol parsing, math, output reports, profiles, tray menus, and offline safety.

[1.0.0]: https://github.com/waterzyy/OpenDS4/releases/tag/v1.0.0
