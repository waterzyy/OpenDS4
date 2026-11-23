# Troubleshooting

Run the app with `run_windows.bat --console` to keep a log visible. Most problems announce themselves there.

## The controller is not detected

- **Try a different USB cable and port.** Some cables are charge-only and carry no data.
- **Check the transport.** The status bar should show `USB` or `Bluetooth`. If it stays on `Searching`, OpenDS4 is still enumerating; it retries every 500 ms.
- **Bluetooth pairing.** Pair the pad from Windows Settings first, then press the PS button. OpenDS4 does not manage pairing.
- **Wireless Adapter.** Plug the adapter in and let Windows install it. Product ID `0BA0` is supported.
- **HidHide.** If HidHide is hiding the controller from everything, add the `.venv\Scripts\pythonw.exe` path to its **Applications** list. Use **Copy Python Path** on the Mapping page.

## The game sees no controller

1. Confirm ViGEmBus is installed from the [official releases](https://github.com/nefarius/ViGEmBus/releases).
2. Restart Windows after installing the driver.
3. Press **Win+R**, run `joy.cpl`, and check that a **Controller** (Xbox 360) appears while OpenDS4 is running.
4. Some games need to be started after the virtual controller exists. Launch OpenDS4 first, then the game.
5. If the virtual controller is missing, check the log for `ViGEmBus missing`. Reinstall the driver and reboot.

## Every press registers twice

The game is reading the physical pad as DirectInput and the virtual pad as XInput. Install [HidHide](https://github.com/nefarius/HidHide/releases) and follow the steps in the README: add OpenDS4's interpreter to **Applications**, hide **Sony Wireless Controller** under **Devices**, and enable device hiding.

## Bluetooth problems

- **Only basic 10-byte reports arrive.** OpenDS4 reads feature report `0x02` on connect to switch the pad into 78-byte extended mode. If your pad stays in basic mode, re-pair it and try again.
- **Packets are dropped.** Bluetooth input reports are validated with CRC-32. Corrupted packets are discarded rather than acted on; this shows up as `CRC` drops in the log.
- **Stutter over Bluetooth.** Raise `bt_poll_ms` from `4` to `8` or `16` in the profile to reduce traffic at the cost of latency.
- **Lag while rumbling.** Output reports are sent from a dedicated thread, so rumble should not stall input. If it still does, lower the rumble strength or test over USB.

## The lightbar does not change

- USB and Bluetooth use different output report layouts. OpenDS4 selects the right one automatically; a wrong transport hint is corrected when the first extended report arrives.
- Try **Static Color** first. If that works, the animated mode may be too dim: raise **Brightness**.
- Some third-party controllers ignore LED packets entirely.

## Rumble does not work

- Confirm **Controller Vibration** is enabled in the tray menu and **rumble** is enabled on the Vibration page.
- Use the **Test Left** and **Test Right** buttons. If the motors spin there, the pad and output path are fine and the game is not sending force feedback.
- Over Bluetooth, rumble can be weaker. Try USB to compare.
- Check that the game's own vibration setting is on.

## Touchpad mouse behaves oddly

- It is disabled by default. Enable it from the Monitor page, the Mapping page, or the tray menu.
- If taps fire accidentally, turn off **Tap to click**.
- The touchpad is 1920 x 942. Windows cursor movement is relative, so screen resolution does not matter.
- Physical presses map to left or right button depending on which side of the pad you press. The split is at roughly 70% of the width.

## The system tray icon is missing

- The tray icon requires Windows Explorer to be running. Restart Explorer or reboot.
- OpenDS4 creates the icon with `Shell_NotifyIconW` and no external assets. If Explorer restarts, the icon is re-added automatically.
- On non-Windows platforms the tray is a no-op by design.

## The app will not start

- **`Python 3.10 or newer was not found`** — install Python from [python.org](https://www.python.org/downloads/) and enable **Add python.exe to PATH**.
- **`Your Python installation is missing Tcl/Tk`** — rerun the Python installer, choose **Modify**, and enable **tcl/tk and IDLE**.
- **`Could not download or install packages`** — the first launch needs internet access for `pip`. Check the connection, then run the launcher again.
- **`Could not create .venv`** — move the project out of a read-only or synced folder, or run it from a local directory.
- **Antivirus warning** — the app is plain Python with no binary payload. If a scanner blocks `pythonw.exe`, add an exclusion for the project folder. OpenDS4 never opens a network socket, so a firewall alert from it would be unexpected.
- **Stale environment** — delete `.venv` and run the launcher again.

## High CPU or stutter

- The UI redraws every 16 ms only while the window is visible. Minimized to the tray it drops to 250 ms.
- If the event log grows unbounded in a long session, restart the app. The log is in-memory only.
- Bluetooth with `bt_poll_ms=1` sends more traffic; use `4` unless you need the lowest latency.
- Check Task Manager for the Python process. A modern machine should sit near 1–3% with one controller connected.

## Resetting everything

Close the app, then delete:

- `.venv\` — recreated on the next launch
- `profiles\` — recreated with the four built-in presets

Your OS-level Bluetooth pairing and the ViGEmBus driver are untouched.

## Collecting a useful log

1. Close any running OpenDS4 instance.
2. Run `run_windows.bat --console`.
3. Reproduce the problem.
4. Copy the console output, plus the Windows version, Python version, controller model, and connection type.
5. Attach it to a bug report.
