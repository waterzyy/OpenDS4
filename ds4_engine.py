"""Background HID reader, decoupled feedback writer, and XInput mapper."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import ctypes
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import sys
import threading
import time

from ds4_profile import ControllerProfile
from ds4_protocol import (
    DS4_FEATURE_REPORT_CALIBRATION,
    DS4_FEATURE_REPORT_CALIBRATION_SIZE,
    DS4_PRODUCT_IDS,
    DS4InputState,
    SONY_VENDOR_ID,
    TouchFinger,
    Xbox360OutputState,
    build_bt_output_report,
    build_usb_output_report,
    parse_ds4_input_report,
)
from ds4_touchpad import TouchpadMouseController

try:
    import hid  # type: ignore
except (ImportError, OSError):
    hid = None

os.environ.setdefault("VGAMEPAD_SKIP_VIGEMBUS_INSTALL", "true")
try:
    import vgamepad as vg  # type: ignore

    _VG_IMPORT_ERROR: str | None = None
except ImportError:
    vg = None
    _VG_IMPORT_ERROR = "vgamepad not installed"
except Exception as _vg_exc:
    vg = None
    _VG_IMPORT_ERROR = f"ViGEmBus missing ({_vg_exc})"

DS4_HID_READ_TIMEOUT_MS = 4
DEMO_TARGET_PERIOD_SEC = 0.004
DS4_LED_ANIM_INTERVAL_SEC = 0.040
DS4_MOTOR_KEEPALIVE_SEC = 0.180


def _is_bluetooth_hid_path(path_str: str) -> bool:
    lowered = path_str.lower()
    return (
        "bthenum" in lowered
        or "00001124-0000-1000-8000-00805f9b34fb" in lowered
        or "bthle" in lowered
    )


def detect_hidhide_installed() -> bool:
    if sys.platform != "win32":
        return False
    roots = {
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramW6432", r"C:\Program Files"),
    }
    rel_candidates = (
        Path("Nefarius Software Solutions/HidHide/x64/HidHideClient.exe"),
        Path("Nefarius Software Solutions/HidHide/x64/HidHideCLI.exe"),
    )
    for root in roots:
        if not root:
            continue
        base = Path(root)
        for rel in rel_candidates:
            try:
                if (base / rel).is_file():
                    return True
            except OSError:
                continue
    return False


class _Win32HidOutputFallback:
    """Lazy HidD_SetOutputReport fallback for Windows BT stacks that reject WriteFile."""

    def __init__(self, device_path: bytes | str) -> None:
        self._raw_path = device_path
        self._handle: int | None = None
        self._attempted = False

    def _ensure_open(self) -> bool:
        if sys.platform != "win32":
            return False
        if self._handle is not None:
            return True
        if self._attempted:
            return False
        self._attempted = True
        try:
            path_str = (
                self._raw_path.decode("utf-8", errors="ignore")
                if isinstance(self._raw_path, (bytes, bytearray))
                else str(self._raw_path)
            )
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            generic_rw = 0x80000000 | 0x40000000
            share_rw = 0x00000001 | 0x00000002
            open_existing = 3
            kernel32.CreateFileW.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
            ]
            kernel32.CreateFileW.restype = ctypes.c_void_p
            handle = kernel32.CreateFileW(
                path_str,
                generic_rw,
                share_rw,
                None,
                open_existing,
                0,
                None,
            )
            invalid = ctypes.c_void_p(-1).value
            if handle and handle != invalid:
                self._handle = int(handle)
                return True
        except Exception:
            self._handle = None
        return False

    def send_output_report(self, payload: bytes) -> bool:
        if not self._ensure_open() or not self._handle:
            return False
        try:
            hid_dll = ctypes.WinDLL("hid", use_last_error=True)  # type: ignore[attr-defined]
            hid_dll.HidD_SetOutputReport.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
            ]
            hid_dll.HidD_SetOutputReport.restype = ctypes.c_ubyte
            buf = ctypes.create_string_buffer(payload, len(payload))
            ok = hid_dll.HidD_SetOutputReport(
                ctypes.c_void_p(self._handle),
                buf,
                ctypes.c_uint32(len(payload)),
            )
            return bool(ok)
        except Exception:
            return False

    def close(self) -> None:
        if sys.platform == "win32" and self._handle:
            try:
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
                kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel32.CloseHandle.restype = ctypes.c_int32
                kernel32.CloseHandle(ctypes.c_void_p(self._handle))
            except Exception:
                pass
            self._handle = None


@dataclass(slots=True)
class EngineTelemetry:
    running: bool = False
    connected: bool = False
    demo_mode: bool = False
    device_name: str = "Disconnected"
    transport: str = "None"
    vigem_ready: bool = False
    vigem_status: str = "Inactive"
    hidhide_installed: bool = False
    poll_hz: float = 0.0
    frame_ms: float = 0.0
    packets_ok: int = 0
    crc_dropped: int = 0
    rumble_large: int = 0
    rumble_small: int = 0
    lightbar_rgb: tuple[int, int, int] = (0, 90, 255)
    ds4: DS4InputState = field(default_factory=DS4InputState)
    xbox: Xbox360OutputState = field(default_factory=Xbox360OutputState)


class DS4MapperEngine:
    def __init__(
        self,
        profile: ControllerProfile,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._profile = profile
        self._on_log = on_log

        self._stop_event = threading.Event()
        self._output_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._demo_mode = False

        self._game_large_motor = 0
        self._game_small_motor = 0
        self._test_mode = "both"
        self._test_start = 0.0
        self._test_until = 0.0
        self._force_output_sync = True
        self._rumble_cb = None

        self._active_dev = None
        self._active_fallback: _Win32HidOutputFallback | None = None
        self._active_is_bt = False

        self._xusb_button_map: dict[str, object] = {}
        self._last_xbox_state: Xbox360OutputState | None = None
        self._touchpad_mouse = TouchpadMouseController()

        self._hidhide_detected = detect_hidhide_installed()
        self._telemetry = EngineTelemetry(hidhide_installed=self._hidhide_detected)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def update_profile(self, profile: ControllerProfile, *, sync_lightbar: bool = True) -> None:
        with self._lock:
            self._profile = profile
            tp_enabled = profile.touchpad_mouse
            if sync_lightbar:
                self._force_output_sync = True
        if not tp_enabled:
            self._touchpad_mouse.reset()
        if sync_lightbar:
            self._output_event.set()

    def get_telemetry(self) -> EngineTelemetry:
        with self._lock:
            return EngineTelemetry(
                running=self._telemetry.running,
                connected=self._telemetry.connected,
                demo_mode=self._telemetry.demo_mode,
                device_name=self._telemetry.device_name,
                transport=self._telemetry.transport,
                vigem_ready=self._telemetry.vigem_ready,
                vigem_status=self._telemetry.vigem_status,
                hidhide_installed=self._telemetry.hidhide_installed,
                poll_hz=self._telemetry.poll_hz,
                frame_ms=self._telemetry.frame_ms,
                packets_ok=self._telemetry.packets_ok,
                crc_dropped=self._telemetry.crc_dropped,
                rumble_large=self._telemetry.rumble_large,
                rumble_small=self._telemetry.rumble_small,
                lightbar_rgb=self._telemetry.lightbar_rgb,
                ds4=self._telemetry.ds4,
                xbox=Xbox360OutputState(
                    thumb_lx=self._telemetry.xbox.thumb_lx,
                    thumb_ly=self._telemetry.xbox.thumb_ly,
                    thumb_rx=self._telemetry.xbox.thumb_rx,
                    thumb_ry=self._telemetry.xbox.thumb_ry,
                    left_trigger=self._telemetry.xbox.left_trigger,
                    right_trigger=self._telemetry.xbox.right_trigger,
                    buttons=dict(self._telemetry.xbox.buttons),
                ),
            )

    def trigger_test_rumble(self, duration_sec: float = 0.65, mode: str = "both") -> None:
        now = time.monotonic()
        dur = max(0.15, min(3.0, float(duration_sec)))
        with self._lock:
            self._test_mode = mode if mode in ("both", "heavy", "light", "pulse") else "both"
            self._test_start = now
            self._test_until = now + dur
            self._force_output_sync = True
        self._output_event.set()
        labels = {
            "both": "Dual motors",
            "heavy": "Left heavy motor",
            "light": "Right light motor",
            "pulse": "Pulse pattern",
        }
        self._log(f"Rumble test: {labels.get(mode, 'Dual motors')}")

    def stop_rumble(self) -> None:
        with self._lock:
            self._test_until = 0.0
            self._game_large_motor = 0
            self._game_small_motor = 0
            self._force_output_sync = True
        self._output_event.set()

    def _compute_test_motors(self, now: float) -> tuple[int, int]:
        if now >= self._test_until:
            return 0, 0
        if self._test_mode == "heavy":
            return 225, 0
        if self._test_mode == "light":
            return 0, 225
        if self._test_mode == "pulse":
            elapsed = now - self._test_start
            slot = int(elapsed * 8.0) % 4
            if slot == 0:
                return 230, 60
            if slot == 2:
                return 60, 230
            return 0, 0
        return 220, 200

    def start(self, *, demo_mode: bool = False) -> None:
        if self.is_running:
            self.stop()
        self._demo_mode = demo_mode
        self._stop_event.clear()
        self._output_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="OpenDS4-Input",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._output_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None
        with self._lock:
            self._telemetry.running = False
            self._telemetry.connected = False
            self._telemetry.poll_hz = 0.0
            self._telemetry.frame_ms = 0.0
            self._telemetry.rumble_large = 0
            self._telemetry.rumble_small = 0

    def _log(self, message: str) -> None:
        if self._on_log:
            timestamp = time.strftime("%H:%M:%S")
            self._on_log(f"[{timestamp}] {message}")

    @staticmethod
    def _build_xusb_button_map(vg_module=None) -> dict[str, object]:
        mod = vg_module if vg_module is not None else vg
        if mod is None:
            return {}
        btn_enum = mod.XUSB_BUTTON
        return {
            "A": btn_enum.XUSB_GAMEPAD_A,
            "B": btn_enum.XUSB_GAMEPAD_B,
            "X": btn_enum.XUSB_GAMEPAD_X,
            "Y": btn_enum.XUSB_GAMEPAD_Y,
            "LB": btn_enum.XUSB_GAMEPAD_LEFT_SHOULDER,
            "RB": btn_enum.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "LS": btn_enum.XUSB_GAMEPAD_LEFT_THUMB,
            "RS": btn_enum.XUSB_GAMEPAD_RIGHT_THUMB,
            "Back": btn_enum.XUSB_GAMEPAD_BACK,
            "Start": btn_enum.XUSB_GAMEPAD_START,
            "Guide": btn_enum.XUSB_GAMEPAD_GUIDE,
            "DPadUp": btn_enum.XUSB_GAMEPAD_DPAD_UP,
            "DPadDown": btn_enum.XUSB_GAMEPAD_DPAD_DOWN,
            "DPadLeft": btn_enum.XUSB_GAMEPAD_DPAD_LEFT,
            "DPadRight": btn_enum.XUSB_GAMEPAD_DPAD_RIGHT,
        }

    def _init_virtual_xbox(self):
        self._last_xbox_state = None
        self._xusb_button_map = self._build_xusb_button_map()

        if vg is None:
            reason = _VG_IMPORT_ERROR or "vgamepad not installed"
            if "ViGEmBus" in reason or "VIGEM" in reason.upper():
                self._log("ViGEmBus driver not found. Install Nefarius ViGEmBus for XInput emulation.")
                return None, "ViGEmBus driver missing"
            return None, reason
        try:
            pad = vg.VX360Gamepad()

            def _on_rumble(client, target, large_motor, small_motor, led_number, user_data):
                with self._lock:
                    self._game_large_motor = int(large_motor) & 0xFF
                    self._game_small_motor = int(small_motor) & 0xFF
                    self._force_output_sync = True
                self._output_event.set()

            self._rumble_cb = _on_rumble
            try:
                pad.register_notification(callback_function=self._rumble_cb)
            except Exception as exc:
                self._log(f"Rumble notification warning: {exc}")

            self._log("Virtual Xbox 360 controller ready (ViGEmBus).")
            return pad, "Ready (XInput)"
        except Exception as exc:
            msg = f"ViGEmBus missing ({exc.__class__.__name__})"
            self._log("ViGEmBus driver not found. Install Nefarius ViGEmBus for XInput emulation.")
            return None, msg

    def _reset_virtual_xbox(self, pad) -> None:
        self._last_xbox_state = None
        if pad is None:
            return
        try:
            pad.reset()
            pad.update()
        except Exception:
            pass

    def _apply_to_virtual_xbox(self, pad, xbox: Xbox360OutputState) -> bool:
        if pad is None:
            return False

        flag_map = self._xusb_button_map
        if not flag_map:
            flag_map = self._build_xusb_button_map()
            self._xusb_button_map = flag_map
        if not flag_map:
            return False

        prev = self._last_xbox_state
        if prev is None:
            pad.reset()
            for name, flag in flag_map.items():
                if xbox.buttons.get(name, False):
                    pad.press_button(button=flag)
            pad.left_trigger(value=xbox.left_trigger)
            pad.right_trigger(value=xbox.right_trigger)
            pad.left_joystick(x_value=xbox.thumb_lx, y_value=xbox.thumb_ly)
            pad.right_joystick(x_value=xbox.thumb_rx, y_value=xbox.thumb_ry)
            pad.update()
            self._last_xbox_state = Xbox360OutputState(
                thumb_lx=xbox.thumb_lx,
                thumb_ly=xbox.thumb_ly,
                thumb_rx=xbox.thumb_rx,
                thumb_ry=xbox.thumb_ry,
                left_trigger=xbox.left_trigger,
                right_trigger=xbox.right_trigger,
                buttons={name: bool(xbox.buttons.get(name, False)) for name in flag_map},
            )
            return True

        changed = False

        for name, flag in flag_map.items():
            now_down = bool(xbox.buttons.get(name, False))
            was_down = prev.buttons.get(name, False)
            if now_down != was_down:
                if now_down:
                    pad.press_button(button=flag)
                else:
                    pad.release_button(button=flag)
                prev.buttons[name] = now_down
                changed = True

        if xbox.left_trigger != prev.left_trigger:
            pad.left_trigger(value=xbox.left_trigger)
            prev.left_trigger = xbox.left_trigger
            changed = True

        if xbox.right_trigger != prev.right_trigger:
            pad.right_trigger(value=xbox.right_trigger)
            prev.right_trigger = xbox.right_trigger
            changed = True

        if xbox.thumb_lx != prev.thumb_lx or xbox.thumb_ly != prev.thumb_ly:
            pad.left_joystick(x_value=xbox.thumb_lx, y_value=xbox.thumb_ly)
            prev.thumb_lx = xbox.thumb_lx
            prev.thumb_ly = xbox.thumb_ly
            changed = True

        if xbox.thumb_rx != prev.thumb_rx or xbox.thumb_ry != prev.thumb_ry:
            pad.right_joystick(x_value=xbox.thumb_rx, y_value=xbox.thumb_ry)
            prev.thumb_rx = xbox.thumb_rx
            prev.thumb_ry = xbox.thumb_ry
            changed = True

        if changed:
            pad.update()
        return changed

    def _discover_ds4_device(self):
        if hid is None:
            return None

        candidates: list[tuple[int, dict]] = []
        try:
            all_devs = hid.enumerate(SONY_VENDOR_ID, 0)
        except Exception:
            return None

        for info in all_devs:
            pid = int(info.get("product_id", 0))
            if pid not in DS4_PRODUCT_IDS:
                continue
            up = int(info.get("usage_page", 0))
            u = int(info.get("usage", 0))
            if up not in (0, 0x0001):
                continue
            if up == 0x0001 and u not in (0, 0x0004, 0x0005):
                continue
            priority = 0 if (up == 0x0001 and u == 0x0005) else 1
            candidates.append((priority, info))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _open_hid_device(self, info: dict):
        raw_path = info["path"]
        path_str = (
            raw_path.decode("utf-8", errors="ignore")
            if isinstance(raw_path, (bytes, bytearray))
            else str(raw_path)
        )
        is_bt = _is_bluetooth_hid_path(path_str)
        pid = int(info.get("product_id", 0))
        model_name = DS4_PRODUCT_IDS.get(pid, "DualShock 4")

        if hasattr(hid, "device"):
            dev = hid.device()
            dev.open_path(raw_path)
            dev.set_nonblocking(0)
        else:
            dev = hid.Device(path=raw_path)

        # Reading feature report 0x02 switches BT DS4 from 10-byte basic mode to full 78-byte reports.
        if is_bt:
            try:
                dev.get_feature_report(
                    DS4_FEATURE_REPORT_CALIBRATION,
                    DS4_FEATURE_REPORT_CALIBRATION_SIZE,
                )
            except Exception as exc:
                self._log(f"BT feature report 0x02 note: {exc}")

        win_fallback = _Win32HidOutputFallback(raw_path)
        return dev, win_fallback, model_name, is_bt

    def _send_ds4_feedback(
        self,
        dev,
        win_fallback: _Win32HidOutputFallback | None,
        *,
        is_bt: bool,
        red: int,
        green: int,
        blue: int,
        flash_on: int,
        flash_off: int,
        small_rumble: int,
        large_rumble: int,
        bt_poll_ms: int,
    ) -> None:
        if is_bt:
            packet = build_bt_output_report(
                red=red,
                green=green,
                blue=blue,
                small_rumble=small_rumble,
                large_rumble=large_rumble,
                flash_on=flash_on,
                flash_off=flash_off,
                bt_poll_ms=bt_poll_ms,
            )
        else:
            packet = build_usb_output_report(
                red=red,
                green=green,
                blue=blue,
                small_rumble=small_rumble,
                large_rumble=large_rumble,
                flash_on=flash_on,
                flash_off=flash_off,
            )

        try:
            written = dev.write(packet)
            if isinstance(written, int) and written < 0 and is_bt and win_fallback is not None:
                win_fallback.send_output_report(packet)
        except Exception:
            if is_bt and win_fallback is not None:
                win_fallback.send_output_report(packet)

    def _run_output_worker(self) -> None:
        """Dedicated writer thread so USB/BT writes never stall stick/button reads."""
        last_motor_sig: tuple[int, ...] | None = None
        last_rgb_sig: tuple[int, int, int] | None = None
        last_send_time = 0.0

        while not self._stop_event.is_set():
            now_mono = time.monotonic()
            with self._lock:
                dev = self._active_dev
                fallback = self._active_fallback
                is_bt = self._active_is_bt
                profile = self._profile
                cur_ds4 = self._telemetry.ds4
                test_l, test_s = self._compute_test_motors(now_mono)
                large_m, small_m = profile.compute_motors(
                    game_large=self._game_large_motor,
                    game_small=self._game_small_motor,
                    ds4=cur_ds4,
                    test_large=test_l,
                    test_small=test_s,
                )
                self._telemetry.rumble_large = large_m
                self._telemetry.rumble_small = small_m
                force_sync = self._force_output_sync
                self._force_output_sync = False

            r, g, b, flash_on, flash_off = profile.effective_lightbar_bytes(
                battery_percent=cur_ds4.battery_percent,
                cable_connected=cur_ds4.cable_connected,
                now=now_mono,
                ds4=cur_ds4,
                rumble_large=large_m,
                rumble_small=small_m,
            )
            with self._lock:
                self._telemetry.lightbar_rgb = (r, g, b)

            if dev is None:
                last_motor_sig = None
                last_rgb_sig = None
                idle_wait = 0.05 if (profile.is_animated_lightbar or now_mono < self._test_until) else 0.45
                self._output_event.wait(timeout=idle_wait)
                self._output_event.clear()
                continue

            motor_sig = (flash_on, flash_off, small_m, large_m, profile.bt_poll_ms, int(is_bt))
            rgb_sig = (r, g, b)
            dt = now_mono - last_send_time

            motors_active = large_m > 0 or small_m > 0
            urgent = force_sync or (motor_sig != last_motor_sig)
            anim_due = (rgb_sig != last_rgb_sig) and (dt >= DS4_LED_ANIM_INTERVAL_SEC)
            motor_keepalive = motors_active and (dt >= DS4_MOTOR_KEEPALIVE_SEC)
            idle_keepalive = dt >= 2.0

            if urgent or anim_due or motor_keepalive or idle_keepalive:
                self._send_ds4_feedback(
                    dev,
                    fallback,
                    is_bt=is_bt,
                    red=r,
                    green=g,
                    blue=b,
                    flash_on=flash_on,
                    flash_off=flash_off,
                    small_rumble=small_m,
                    large_rumble=large_m,
                    bt_poll_ms=profile.bt_poll_ms,
                )
                last_motor_sig = motor_sig
                last_rgb_sig = rgb_sig
                last_send_time = time.monotonic()

            wait_sec = 0.035 if (profile.is_animated_lightbar or motors_active or now_mono < self._test_until) else 0.25
            self._output_event.wait(timeout=wait_sec)
            self._output_event.clear()

    def _generate_demo_state(self, t: float) -> DS4InputState:
        lx = int(round(128 + 115 * math.cos(t * 1.5)))
        ly = int(round(128 + 115 * math.sin(t * 1.5)))
        rx = int(round(128 + 90 * math.sin(t * 0.9)))
        ry = int(round(128 + 90 * math.cos(t * 1.8)))
        l2 = int(round((math.sin(t * 1.2) * 0.5 + 0.5) * 255))
        r2 = int(round((math.cos(t * 1.2) * 0.5 + 0.5) * 255))
        phase = int(t * 2.0) % 8

        return DS4InputState(
            transport="Simulation",
            lx=max(0, min(255, lx)),
            ly=max(0, min(255, ly)),
            rx=max(0, min(255, rx)),
            ry=max(0, min(255, ry)),
            l2=l2,
            r2=r2,
            cross=(phase == 0),
            circle=(phase in (0, 1)),
            square=(phase == 2),
            triangle=(phase == 3),
            l1=(phase == 4),
            r1=(phase == 5),
            dpad_up=(phase == 6),
            dpad_right=(phase == 7),
            battery_percent=85,
            cable_connected=False,
            gyro_x=int(300 * math.sin(t * 2.2)),
            gyro_y=int(300 * math.cos(t * 1.7)),
            gyro_z=int(180 * math.sin(t * 1.1)),
            accel_z=8192,
            touch_1=TouchFinger(
                active=True,
                tracking_id=1,
                x=int(960 + 650 * math.cos(t * 1.2)),
                y=int(471 + 320 * math.sin(t * 1.9)),
            ),
        )

    def _run_loop(self) -> None:
        vpad, vigem_msg = self._init_virtual_xbox()
        self._hidhide_detected = detect_hidhide_installed()
        with self._lock:
            self._telemetry.running = True
            self._telemetry.demo_mode = self._demo_mode
            self._telemetry.vigem_ready = vpad is not None
            self._telemetry.vigem_status = vigem_msg
            self._telemetry.hidhide_installed = self._hidhide_detected

        try:
            if self._demo_mode:
                self._run_demo_loop(vpad)
            else:
                self._run_hardware_loop(vpad)
        finally:
            self._reset_virtual_xbox(vpad)
            if vpad is not None:
                try:
                    vpad.unregister_notification()
                except Exception:
                    pass
            self._rumble_cb = None

    def _run_demo_loop(self, vpad) -> None:
        self._log("Simulator active (250 Hz).")
        t0 = time.perf_counter()
        next_deadline = t0
        timestamps: deque[float] = deque(maxlen=120)
        packets = 0

        while not self._stop_event.is_set():
            now = time.perf_counter()
            timestamps.append(now)
            packets += 1

            ds4 = self._generate_demo_state(now - t0)
            now_mono = time.monotonic()
            with self._lock:
                profile = self._profile
                test_l, test_s = self._compute_test_motors(now_mono)
                large, small = profile.compute_motors(
                    game_large=self._game_large_motor,
                    game_small=self._game_small_motor,
                    ds4=ds4,
                    test_large=test_l,
                    test_small=test_s,
                )

            r, g, b, _, _ = profile.effective_lightbar_bytes(
                battery_percent=ds4.battery_percent,
                cable_connected=ds4.cable_connected,
                now=now_mono,
                ds4=ds4,
                rumble_large=large,
                rumble_small=small,
            )
            xbox = profile.map_to_xbox360(ds4)
            self._apply_to_virtual_xbox(vpad, xbox)

            hz = 0.0
            frame_ms = 4.0
            if len(timestamps) >= 2:
                span = timestamps[-1] - timestamps[0]
                if span > 0:
                    hz = (len(timestamps) - 1) / span
                    frame_ms = 1000.0 / hz

            with self._lock:
                self._telemetry.connected = True
                self._telemetry.device_name = "DualShock 4 Simulator"
                self._telemetry.transport = "Simulated"
                self._telemetry.poll_hz = hz
                self._telemetry.frame_ms = frame_ms
                self._telemetry.packets_ok = packets
                self._telemetry.rumble_large = large
                self._telemetry.rumble_small = small
                self._telemetry.lightbar_rgb = (r, g, b)
                self._telemetry.ds4 = ds4
                self._telemetry.xbox = xbox

            next_deadline += DEMO_TARGET_PERIOD_SEC
            remaining = next_deadline - time.perf_counter()
            if remaining > 0.0:
                if self._stop_event.wait(timeout=remaining):
                    break
            elif remaining < -0.050:
                next_deadline = time.perf_counter()

    def _run_hardware_loop(self, vpad) -> None:
        if hid is None:
            self._log("hidapi is not installed. Run: pip install hidapi vgamepad")
            with self._lock:
                self._telemetry.device_name = "hidapi missing"
            self._stop_event.wait()
            return

        self._log("Waiting for DualShock 4 over USB or Bluetooth...")
        dev = None
        win_fallback: _Win32HidOutputFallback | None = None
        is_bt = False
        model_name = "DualShock 4"
        timestamps: deque[float] = deque(maxlen=200)
        packets_ok = 0
        crc_dropped = 0

        out_thread = threading.Thread(
            target=self._run_output_worker,
            name="OpenDS4-Output",
            daemon=True,
        )
        out_thread.start()

        try:
            while not self._stop_event.is_set():
                if dev is None:
                    with self._lock:
                        self._active_dev = None
                        self._active_fallback = None
                        self._telemetry.connected = False
                        self._telemetry.device_name = "Searching for controller..."
                        self._telemetry.transport = "None"
                        self._telemetry.poll_hz = 0.0
                        self._telemetry.frame_ms = 0.0

                    info = self._discover_ds4_device()
                    if info is None:
                        if self._stop_event.wait(timeout=0.5):
                            break
                        continue

                    try:
                        dev, win_fallback, model_name, is_bt = self._open_hid_device(info)
                        timestamps.clear()
                        self._last_xbox_state = None
                        with self._lock:
                            self._active_dev = dev
                            self._active_fallback = win_fallback
                            self._active_is_bt = is_bt
                            self._force_output_sync = True
                        self._output_event.set()
                        self._log(f"Connected: {model_name} ({'Bluetooth' if is_bt else 'USB'}).")
                    except Exception as exc:
                        self._log(f"Could not open controller: {exc}")
                        dev = None
                        if self._stop_event.wait(timeout=1.0):
                            break
                        continue

                try:
                    raw = dev.read(547, DS4_HID_READ_TIMEOUT_MS)
                except TypeError:
                    raw = dev.read(547)
                except Exception as exc:
                    self._log(f"Controller disconnected ({exc}).")
                    self._touchpad_mouse.reset()
                    self._reset_virtual_xbox(vpad)
                    with self._lock:
                        self._active_dev = None
                        self._active_fallback = None
                    try:
                        dev.close()
                    except Exception:
                        pass
                    if win_fallback:
                        win_fallback.close()
                    dev = None
                    win_fallback = None
                    continue

                if not raw:
                    continue

                raw_bytes = bytes(raw)
                if 0x11 <= raw_bytes[0] <= 0x19 and not is_bt:
                    is_bt = True
                    with self._lock:
                        self._active_is_bt = True

                ds4 = parse_ds4_input_report(raw_bytes, is_bluetooth_hint=is_bt)
                if ds4 is None:
                    crc_dropped += 1
                    continue

                if ds4.dongle_disconnected:
                    self._touchpad_mouse.reset()
                    if self._last_xbox_state is not None:
                        self._reset_virtual_xbox(vpad)
                    timestamps.clear()
                    with self._lock:
                        self._telemetry.connected = False
                        self._telemetry.device_name = f"{model_name} (idle)"
                        self._telemetry.transport = "Adapter"
                        self._telemetry.poll_hz = 0.0
                        self._telemetry.frame_ms = 0.0
                        self._telemetry.ds4 = DS4InputState()
                        self._telemetry.xbox = Xbox360OutputState()
                    continue

                now = time.perf_counter()
                timestamps.append(now)
                packets_ok += 1

                with self._lock:
                    profile = self._profile
                    trig_haptics = profile.trigger_rumble

                self._touchpad_mouse.process(
                    ds4,
                    enabled=profile.touchpad_mouse,
                    sensitivity=profile.touchpad_sensitivity,
                    tap_to_click=profile.touchpad_tap_click,
                )
                xbox = profile.map_to_xbox360(ds4)
                self._apply_to_virtual_xbox(vpad, xbox)

                hz = 0.0
                frame_ms = 0.0
                if len(timestamps) >= 2:
                    span = timestamps[-1] - timestamps[0]
                    if span > 0:
                        hz = (len(timestamps) - 1) / span
                        frame_ms = 1000.0 / hz

                with self._lock:
                    self._telemetry.connected = True
                    self._telemetry.device_name = model_name
                    self._telemetry.transport = ds4.transport
                    self._telemetry.poll_hz = hz
                    self._telemetry.frame_ms = frame_ms
                    self._telemetry.packets_ok = packets_ok
                    self._telemetry.crc_dropped = crc_dropped
                    self._telemetry.ds4 = ds4
                    self._telemetry.xbox = xbox

                if trig_haptics and (ds4.l2 > 18 or ds4.r2 > 18):
                    self._output_event.set()
        finally:
            self._stop_event.set()
            self._output_event.set()
            self._touchpad_mouse.reset()
            out_thread.join(timeout=0.5)
            with self._lock:
                self._active_dev = None
                self._active_fallback = None
            if dev is not None:
                try:
                    self._send_ds4_feedback(
                        dev,
                        win_fallback,
                        is_bt=is_bt,
                        red=0,
                        green=40,
                        blue=120,
                        flash_on=0,
                        flash_off=0,
                        small_rumble=0,
                        large_rumble=0,
                        bt_poll_ms=4,
                    )
                except Exception:
                    pass
                try:
                    dev.close()
                except Exception:
                    pass
            if win_fallback is not None:
                win_fallback.close()
