"""Unit tests for OpenDS4 protocol, profiles, engine, UI, and offline safety."""

from __future__ import annotations

import ast
import ctypes
from enum import Enum
import inspect
from pathlib import Path
import struct
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as opends4_app
from ds4_engine import DS4MapperEngine
from ds4_profile import LIGHTBAR_MODES, ControllerProfile, ProfileStore
from ds4_protocol import (
    DS4InputState,
    PS_INPUT_CRC32_SEED,
    PS_OUTPUT_CRC32_SEED,
    TouchFinger,
    Xbox360OutputState,
    build_bt_output_report,
    build_usb_output_report,
    compute_ps_crc32,
    parse_ds4_input_report,
    process_stick_pair,
    process_trigger,
    verify_bt_input_crc32,
)
from ds4_touchpad import (
    INPUT,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MOVE,
    MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP,
    MOUSEEVENTF_WHEEL,
    MouseAction,
    TouchpadMouseController,
)
from ds4_tray import (
    BITMAPINFOHEADER,
    MSG,
    NOTIFYICONDATAW,
    WM_LBUTTONUP,
    WM_RBUTTONUP,
    WM_TRAY_CALLBACK,
    WNDCLASSEXW,
    SystemTrayIcon,
    TraySnapshot,
    build_tray_menu_tree,
    render_gamepad_icon_rgba,
    render_tk_ppm_icon,
)


def _encode_touch(active: bool, tid: int, x: int, y: int) -> bytes:
    b0 = (0x00 if active else 0x80) | (tid & 0x7F)
    b1 = x & 0xFF
    b2 = ((x >> 8) & 0x0F) | ((y & 0x0F) << 4)
    b3 = (y >> 4) & 0xFF
    return bytes((b0, b1, b2, b3))


def test_usb_input_report_parsing() -> None:
    pkt = bytearray(64)
    pkt[0] = 0x01
    pkt[1] = 255
    pkt[2] = 0
    pkt[3] = 128
    pkt[4] = 255

    pkt[5] = 0x01 | 0x20 | 0x80
    pkt[6] = 0x01 | 0x20 | 0x80
    pkt[7] = 0x01 | 0x02
    pkt[8] = 200
    pkt[9] = 150

    struct.pack_into("<hhhhhh", pkt, 13, 120, -240, 35, 10, -20, 8192)
    pkt[30] = 0x10 | 0x08

    pkt[33] = 1
    pkt[35:39] = _encode_touch(True, 5, 1450, 620)
    pkt[39:43] = _encode_touch(False, 6, 0, 0)

    state = parse_ds4_input_report(pkt)
    assert state is not None
    assert state.transport == "USB"
    assert (state.lx, state.ly, state.rx, state.ry) == (255, 0, 128, 255)
    assert state.dpad_up and state.dpad_right and not state.dpad_down and not state.dpad_left
    assert state.cross and state.triangle and not state.circle and not state.square
    assert state.l1 and state.options and state.r3 and not state.r1
    assert state.ps and state.touch_click
    assert (state.l2, state.r2) == (200, 150)
    assert (state.gyro_x, state.gyro_y, state.gyro_z) == (120, -240, 35)
    assert state.accel_z == 8192
    assert state.cable_connected is True
    assert state.battery_percent == 80
    assert state.touch_1.active and (state.touch_1.x, state.touch_1.y) == (1450, 620)
    assert not state.touch_2.active


def test_bt_extended_report_and_crc32() -> None:
    pkt = bytearray(78)
    pkt[0] = 0x11
    pkt[1] = 0xC0
    pkt[2] = 0x00
    pkt[3] = 0
    pkt[4] = 128
    pkt[5] = 128
    pkt[6] = 128
    pkt[7] = 0x08 | 0x40
    pkt[10] = 255
    pkt[32] = 0x04

    crc = compute_ps_crc32(PS_INPUT_CRC32_SEED, pkt[:74])
    struct.pack_into("<I", pkt, 74, crc)

    assert verify_bt_input_crc32(pkt) is True
    state = parse_ds4_input_report(pkt, is_bluetooth_hint=True)
    assert state is not None
    assert state.transport == "Bluetooth"
    assert state.lx == 0
    assert state.circle is True
    assert state.l2 == 255
    assert state.cable_connected is False
    assert state.battery_percent == 50

    corrupted = bytearray(pkt)
    corrupted[10] ^= 0x01
    assert verify_bt_input_crc32(corrupted) is False
    assert parse_ds4_input_report(corrupted, is_bluetooth_hint=True) is None


def test_stick_deadzone_and_y_inversion() -> None:
    assert process_stick_pair(133, 124, deadzone=0.10) == (0, 0)

    x_up, y_up = process_stick_pair(128, 0, deadzone=0.08)
    assert (x_up, y_up) == (0, 32767)

    x_dn, y_dn = process_stick_pair(128, 255, deadzone=0.08)
    assert (x_dn, y_dn) == (0, -32768)

    assert process_trigger(3, deadzone=0.05) == 0
    assert process_trigger(255, deadzone=0.05) == 255


def test_output_reports_usb_and_bt() -> None:
    usb_out = build_usb_output_report(red=10, green=20, blue=240, small_rumble=55, large_rumble=180)
    assert len(usb_out) == 32
    assert usb_out[0] == 0x05
    assert usb_out[1] == 0x07
    assert usb_out[2] == 0x00
    assert (usb_out[4], usb_out[5]) == (55, 180)
    assert (usb_out[6], usb_out[7], usb_out[8]) == (10, 20, 240)

    bt_out = build_bt_output_report(red=255, green=0, blue=64, small_rumble=90, large_rumble=140, bt_poll_ms=4)
    assert len(bt_out) == 78
    assert bt_out[0] == 0x11
    assert bt_out[1] == (0x80 | 0x40 | 0x04)
    assert bt_out[2] == 0x00
    assert bt_out[4] == 0x00
    assert (bt_out[6], bt_out[7]) == (90, 140)
    assert (bt_out[8], bt_out[9], bt_out[10]) == (255, 0, 64)

    expected_crc = compute_ps_crc32(PS_OUTPUT_CRC32_SEED, bt_out[:74])
    actual_crc = struct.unpack_from("<I", bt_out, 74)[0]
    assert actual_crc == expected_crc


def test_profile_remapping_rumble_and_persistence(tmp_path: Path) -> None:
    prof_dir = tmp_path / "profiles"
    prof_dir.mkdir(parents=True)
    # Seed a legacy Cyberpunk.json to verify automatic cleanup on startup
    (prof_dir / "Cyberpunk.json").write_text('{"name": "Cyberpunk"}', encoding="utf-8")

    store = ProfileStore(prof_dir)
    names = store.list_names()
    assert names == ["Default", "Racing", "Shooter"]
    assert not (prof_dir / "Cyberpunk.json").exists()

    prof = ControllerProfile(
        name="Custom Rig",
        rumble_strength=1.2,
        rumble_heavy_scale=1.3,
        rumble_light_scale=0.5,
        trigger_rumble=True,
        trigger_rumble_scale=0.8,
        button_map={"cross": "B", "circle": "A", "touch_click": "Disabled"},
    )
    store.save(prof)
    loaded = store.load("Custom Rig")
    assert loaded.button_map["cross"] == "B"
    assert loaded.button_map["touch_click"] == "Disabled"
    assert loaded.trigger_rumble is True

    # Legacy removed lightbar mode keys gracefully fall back to 'static'
    legacy = ControllerProfile.from_dict({"name": "OldCfg", "lightbar_mode": "cyberpunk"})
    assert legacy.lightbar_mode == "static"

    # Verify independent motor scaling and trigger haptics mixing
    ds4_pull = DS4InputState(l2=240, r2=255)
    heavy, light = loaded.compute_motors(game_large=100, game_small=100, ds4=ds4_pull)
    assert heavy > 150
    assert light > 150

    # Test override takes priority even when rumble_enabled is False
    muted = ControllerProfile(rumble_enabled=False)
    assert muted.compute_motors(game_large=200, game_small=200) == (0, 0)
    assert muted.compute_motors(game_large=0, game_small=0, test_large=220, test_small=180) == (220, 180)


def test_demo_engine_and_rumble_modes() -> None:
    engine = DS4MapperEngine(ControllerProfile())
    engine.start(demo_mode=True)
    try:
        engine.trigger_test_rumble(0.4, "heavy")
        time.sleep(0.06)
        tel = engine.get_telemetry()
        assert tel.running is True
        assert tel.connected is True
        assert tel.packets_ok > 0
        assert tel.rumble_large > 0 and tel.rumble_small == 0

        engine.stop_rumble()
        time.sleep(0.02)
        assert engine.get_telemetry().rumble_large == 0
    finally:
        t_stop = time.perf_counter()
        engine.stop()
        elapsed = time.perf_counter() - t_stop
    assert engine.is_running is False
    assert elapsed < 0.05


def test_virtual_xbox_individual_buttons_and_differential_updates() -> None:
    class StrictXUSBButton(Enum):
        XUSB_GAMEPAD_DPAD_UP = 0x0001
        XUSB_GAMEPAD_DPAD_DOWN = 0x0002
        XUSB_GAMEPAD_DPAD_LEFT = 0x0004
        XUSB_GAMEPAD_DPAD_RIGHT = 0x0008
        XUSB_GAMEPAD_START = 0x0010
        XUSB_GAMEPAD_BACK = 0x0020
        XUSB_GAMEPAD_LEFT_THUMB = 0x0040
        XUSB_GAMEPAD_RIGHT_THUMB = 0x0080
        XUSB_GAMEPAD_LEFT_SHOULDER = 0x0100
        XUSB_GAMEPAD_RIGHT_SHOULDER = 0x0200
        XUSB_GAMEPAD_GUIDE = 0x0400
        XUSB_GAMEPAD_A = 0x1000
        XUSB_GAMEPAD_B = 0x2000
        XUSB_GAMEPAD_X = 0x4000
        XUSB_GAMEPAD_Y = 0x8000

    class DummyVGModule:
        XUSB_BUTTON = StrictXUSBButton

    class MockVX360Pad:
        def __init__(self) -> None:
            self.resets = 0
            self.updates = 0
            self.pressed: list[StrictXUSBButton] = []
            self.released: list[StrictXUSBButton] = []
            self.held: set[StrictXUSBButton] = set()

        def reset(self) -> None:
            self.resets += 1
            self.held.clear()

        def press_button(self, *, button: StrictXUSBButton) -> None:
            assert isinstance(button, StrictXUSBButton)
            self.pressed.append(button)
            self.held.add(button)

        def release_button(self, *, button: StrictXUSBButton) -> None:
            assert isinstance(button, StrictXUSBButton)
            self.released.append(button)
            self.held.discard(button)

        def left_trigger(self, *, value: int) -> None:
            pass

        def right_trigger(self, *, value: int) -> None:
            pass

        def left_joystick(self, *, x_value: int, y_value: int) -> None:
            pass

        def right_joystick(self, *, x_value: int, y_value: int) -> None:
            pass

        def update(self) -> None:
            self.updates += 1

    engine = DS4MapperEngine(ControllerProfile())
    engine._xusb_button_map = engine._build_xusb_button_map(DummyVGModule)
    pad = MockVX360Pad()

    frame1 = Xbox360OutputState(
        thumb_lx=1200,
        thumb_ly=-800,
        left_trigger=120,
        buttons={"A": True, "RB": True, "DPadUp": True, "B": False},
    )
    assert engine._apply_to_virtual_xbox(pad, frame1) is True
    assert pad.resets == 1
    assert pad.updates == 1
    assert len(pad.pressed) == 3

    assert engine._apply_to_virtual_xbox(pad, frame1) is False
    assert pad.resets == 1
    assert pad.updates == 1

    frame3 = Xbox360OutputState(
        thumb_lx=1200,
        thumb_ly=-800,
        left_trigger=120,
        buttons={"A": False, "RB": True, "DPadUp": True, "B": False},
    )
    assert engine._apply_to_virtual_xbox(pad, frame3) is True
    assert pad.resets == 1
    assert pad.updates == 2
    assert pad.released == [StrictXUSBButton.XUSB_GAMEPAD_A]


def test_no_blocking_time_sleep_and_decoupled_io() -> None:
    engine_src = (PROJECT_ROOT / "ds4_engine.py").read_text(encoding="utf-8")
    tree = ast.parse(engine_src, filename="ds4_engine.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert not (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "time"
                and node.func.attr == "sleep"
            )

    # Ensure hardware input loop delegates feedback writes to _run_output_worker
    hw_src = inspect.getsource(DS4MapperEngine._run_hardware_loop)
    assert "_run_output_worker" in hw_src

    engine = DS4MapperEngine(ControllerProfile())
    engine.start(demo_mode=False)
    time.sleep(0.02)
    t0 = time.perf_counter()
    engine.stop()
    assert (time.perf_counter() - t0) < 0.05


def test_trigger_bar_height_consistency() -> None:
    assert opends4_app.TRIGGER_BAR_HEIGHT == 14
    src_create = inspect.getsource(opends4_app.OpenDS4App._create_trigger_bar)
    src_update = inspect.getsource(opends4_app.OpenDS4App._update_trigger_widget)
    assert "TRIGGER_BAR_HEIGHT" in src_create
    assert 'widget["height"]' in src_update
    assert ", 16)" not in src_update


def test_all_8_rgb_lightbar_modes(tmp_path: Path) -> None:
    assert len(LIGHTBAR_MODES) == 8
    assert "cyberpunk" not in LIGHTBAR_MODES
    ds4_idle = DS4InputState(lx=128, ly=128, rx=128, ry=128, l2=0, r2=0)
    ds4_active = DS4InputState(lx=255, ly=0, rx=128, ry=128, l2=255, r2=255)

    for mode_key in LIGHTBAR_MODES:
        prof = ControllerProfile(
            lightbar_mode=mode_key,
            lightbar_speed=1.5,
            lightbar_brightness=1.0,
            lightbar_r=20,
            lightbar_g=120,
            lightbar_b=255,
            low_battery_warning=False,
        )
        samples = set()
        for step in range(12):
            r, g, b, f_on, f_off = prof.effective_lightbar_bytes(
                battery_percent=85,
                cable_connected=False,
                now=step * 0.23,
                ds4=ds4_idle if step < 6 else ds4_active,
                rumble_large=0 if step < 6 else 240,
                rumble_small=0 if step < 6 else 180,
            )
            assert 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255
            assert f_on == 0 and f_off == 0
            samples.add((r, g, b))
        if mode_key not in ("static", "battery_gauge"):
            assert len(samples) >= 2

    store = ProfileStore(tmp_path / "rgb_profiles")
    store.save(ControllerProfile(name="OceanPad", lightbar_mode="ocean", lightbar_speed=1.6))
    loaded = store.load("OceanPad")
    assert loaded.lightbar_mode == "ocean"
    assert abs(loaded.lightbar_speed - 1.6) < 1e-6


def test_tray_icon_menu_win32_simulation_and_launcher(tmp_path: Path) -> None:
    import queue as _q

    # Verify Win32 ABI structures load without ctypes.wintypes on every OS
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(INPUT) == 40
        assert ctypes.sizeof(MSG) == 48
        assert ctypes.sizeof(WNDCLASSEXW) == 80
    assert ctypes.sizeof(BITMAPINFOHEADER) == 40
    assert ctypes.sizeof(NOTIFYICONDATAW) > 900

    for mod_name in ("ds4_tray.py", "ds4_touchpad.py"):
        mod_txt = (PROJECT_ROOT / mod_name).read_text(encoding="utf-8")
        assert "wintypes." not in mod_txt, f"Unsafe wintypes attribute in {mod_name}"

    rgba_teal = render_gamepad_icon_rgba((36, 148, 172), size=32)
    rgba_red = render_gamepad_icon_rgba((255, 20, 40), size=32)
    assert len(rgba_teal) == 32 * 32 * 4
    assert rgba_teal != rgba_red
    assert any(rgba_teal[i + 3] == 255 for i in range(0, len(rgba_teal), 4))

    ppm = render_tk_ppm_icon((36, 148, 172), size=32)
    assert ppm.startswith(b"P6\n32 32\n255\n")
    assert len(ppm) == len(b"P6\n32 32\n255\n") + 32 * 32 * 3

    snap = TraySnapshot(
        status_line="DualShock 4 (USB) · 90%",
        tooltip="OpenDS4 - USB · Battery 90%",
        active_profile="Shooter",
        profile_names=["Default", "Shooter", "Racing"],
        active_lightbar_mode="ocean",
        lightbar_modes=dict(LIGHTBAR_MODES),
        rumble_enabled=True,
        trigger_rumble=False,
        touchpad_mouse=True,
        engine_running=True,
        accent_rgb=(36, 148, 172),
    )
    tree = build_tray_menu_tree(snap)
    assert tree[0].action_id == "restore" and tree[0].default is True
    prof_sub = next(item for item in tree if item.kind == "submenu" and item.label == "Profile")
    assert [c.label for c in prof_sub.children if c.checked] == ["Shooter"]
    led_sub = next(item for item in tree if item.kind == "submenu" and item.label == "Lightbar Mode")
    assert len(led_sub.children) == 8
    assert [c.action_id for c in led_sub.children if c.checked] == ["lightbar:ocean"]

    # Simulate dedicated Win32 tray thread message loop (GetMessageW -> DispatchMessageW)
    class MockKernel32:
        def GetModuleHandleW(self, _name):
            return 0x100000

    class MockGdi32:
        def __init__(self) -> None:
            self.dib_buf = ctypes.create_string_buffer(32 * 32 * 4)
            self.deleted = 0

        def CreateDIBSection(self, _hdc, _bmi, _usage, bits_ptr_ref, _section, _offset):
            bits_ptr_ref._obj.value = ctypes.addressof(self.dib_buf)
            return 0x2001

        def CreateBitmap(self, _w, _h, _planes, _bpp, _bits):
            return 0x2002

        def DeleteObject(self, _obj):
            self.deleted += 1
            return 1

    class MockUser32:
        def __init__(self) -> None:
            self.msg_q: _q.Queue[tuple[int, int, int, int] | None] = _q.Queue()
            self.wndproc = None
            self.menu_items: list[tuple[int, str | None]] = []
            self.next_menu = 0x5000
            self.next_icon = 0x7000
            self.destroyed_icons: list[int] = []
            self.post_count = 0
            self.chosen_cmd = 102  # Second profile item ("Shooter")

        def RegisterWindowMessageW(self, _msg):
            return 0xC099

        def RegisterClassExW(self, wc_ptr):
            self.wndproc = wc_ptr._obj.lpfnWndProc
            return 1

        def UnregisterClassW(self, _cls, _hinst):
            return 1

        def CreateWindowExW(self, *_args):
            return 0x00007FF600112233

        def DestroyWindow(self, _hwnd):
            return 1

        def GetMessageW(self, msg_ptr, _hwnd, _min, _max):
            item = self.msg_q.get(timeout=2.0)
            if item is None:
                return 0
            hwnd, m, wp, lp = item
            msg_ptr._obj.hwnd = hwnd
            msg_ptr._obj.message = m
            msg_ptr._obj.wParam = wp
            msg_ptr._obj.lParam = lp
            return 1

        def TranslateMessage(self, _msg_ptr):
            return 1

        def DispatchMessageW(self, msg_ptr):
            if self.wndproc is not None:
                m = msg_ptr._obj
                return self.wndproc(m.hwnd, m.message, m.wParam, m.lParam)
            return 0

        def PostQuitMessage(self, _code):
            self.msg_q.put(None)

        def PostMessageW(self, hwnd, msg, wp, lp):
            self.post_count += 1
            self.msg_q.put((int(hwnd or 0), int(msg), int(wp), int(lp)))
            return 1

        def GetDC(self, _hwnd):
            return 0x3001

        def ReleaseDC(self, _hwnd, _hdc):
            return 1

        def CreateIconIndirect(self, _info):
            self.next_icon += 1
            return self.next_icon

        def LoadIconW(self, _hinst, _res):
            return 0x7999

        def DestroyIcon(self, hicon):
            self.destroyed_icons.append(int(hicon))
            return 1

        def CreatePopupMenu(self):
            self.next_menu += 1
            return self.next_menu

        def AppendMenuW(self, _hmenu, _flags, uid, label):
            self.menu_items.append((int(uid), label))
            return 1

        def SetMenuDefaultItem(self, _hmenu, _uid, _bypos):
            return 1

        def GetCursorPos(self, pt_ref):
            pt_ref._obj.x = 400
            pt_ref._obj.y = 700
            return 1

        def SetForegroundWindow(self, _hwnd):
            return 1

        def TrackPopupMenu(self, *_args):
            return self.chosen_cmd

        def DestroyMenu(self, _hmenu):
            return 1

        def DefWindowProcW(self, _hwnd, _msg, _wp, _lp):
            return 0

    class MockShell32:
        def __init__(self) -> None:
            self.ops: list[int] = []

        def Shell_NotifyIconW(self, msg, _nid_ref):
            self.ops.append(int(msg))
            return 1

    class MockWin32:
        def __init__(self) -> None:
            self.kernel32 = MockKernel32()
            self.gdi32 = MockGdi32()
            self.user32 = MockUser32()
            self.shell32 = MockShell32()

    mock_win32 = MockWin32()
    win_tray = SystemTrayIcon(initial_snapshot=snap, win32_api=mock_win32)
    assert win_tray.start((36, 148, 172)) is True
    assert win_tray.available is True

    # Identical tooltip should NOT wake the tray thread on subsequent calls
    win_tray.set_snapshot(snap)
    posts_after_first = mock_win32.user32.post_count
    win_tray.set_snapshot(snap)
    assert mock_win32.user32.post_count == posts_after_first

    # Simulate left-click & right-click on tray icon from Explorer
    mock_win32.user32.PostMessageW(win_tray._hwnd, WM_TRAY_CALLBACK, 0, WM_LBUTTONUP)
    mock_win32.user32.PostMessageW(win_tray._hwnd, WM_TRAY_CALLBACK, 0, WM_RBUTTONUP)
    mock_win32.user32.PostMessageW(win_tray._hwnd, 0xC099, 0, 0)  # TaskbarCreated
    win_tray.show_balloon("OpenDS4", "Minimized to tray")

    # Wait briefly for dedicated tray thread to process messages into SimpleQueue
    deadline = time.monotonic() + 1.0
    collected: list[str] = []
    while len(collected) < 2 and time.monotonic() < deadline:
        act = win_tray.pop_action()
        if act is not None:
            collected.append(act)
        else:
            time.sleep(0.01)

    win_tray.stop()
    assert win_tray.available is False
    assert collected == ["restore", "profile:Shooter"]
    assert len(mock_win32.user32.destroyed_icons) == 1

    # Profile persistence for minimize_to_tray
    store = ProfileStore(tmp_path / "tray_profiles")
    p = ControllerProfile(name="TrayPref", minimize_to_tray=False)
    store.save(p)
    assert store.load("TrayPref").minimize_to_tray is False

    # Verify --self-test CLI mode and CRLF batch launcher
    assert opends4_app.main(["--self-test"]) == 0

    bat_bytes = (PROJECT_ROOT / "run_windows.bat").read_bytes()
    assert b"\r\n" in bat_bytes
    assert b"find_spec('hid')" in bat_bytes
    assert b"--self-test" in bat_bytes
    assert (PROJECT_ROOT / "LICENSE").is_file()


def test_optional_touchpad_mouse_gestures_and_toggle(tmp_path: Path) -> None:
    events: list[MouseAction] = []
    tp = TouchpadMouseController(dispatcher=events.extend)

    # Disabled by default in ControllerProfile
    prof = ControllerProfile()
    assert prof.touchpad_mouse is False

    # While disabled: zero mouse events, and physical touch_click maps to Xbox Back
    s_click = DS4InputState(
        touch_click=True,
        touch_1=TouchFinger(active=True, tracking_id=1, x=600, y=400),
    )
    tp.process(s_click, enabled=prof.touchpad_mouse, now=1.0)
    assert events == []
    assert prof.map_to_xbox360(s_click).buttons["Back"] is True

    # Enable touchpad mouse: touch_click is suppressed on virtual Xbox pad
    prof.touchpad_mouse = True
    assert prof.map_to_xbox360(s_click).buttons["Back"] is False

    # 1. Single-finger glide -> relative cursor movement
    tp.process(DS4InputState(touch_1=TouchFinger(True, 2, 500, 300)), enabled=True, now=2.00)
    tp.process(DS4InputState(touch_1=TouchFinger(True, 2, 540, 320)), enabled=True, now=2.02)
    assert any((e.flags & MOUSEEVENTF_MOVE) and e.dx > 0 and e.dy > 0 for e in events)
    tp.process(DS4InputState(), enabled=True, now=2.40)  # Lift after >190 ms so not a tap
    events.clear()

    # 2. Quick 1-finger tap -> Left Click
    tp.process(DS4InputState(touch_1=TouchFinger(True, 3, 800, 450)), enabled=True, now=3.00)
    tp.process(DS4InputState(), enabled=True, now=3.06)
    assert [e.flags for e in events] == [MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP]
    events.clear()

    # 3. Quick 2-finger tap -> Right Click
    tp.process(
        DS4InputState(
            touch_1=TouchFinger(True, 4, 700, 450),
            touch_2=TouchFinger(True, 5, 1050, 460),
        ),
        enabled=True,
        now=4.00,
    )
    tp.process(DS4InputState(), enabled=True, now=4.07)
    assert [e.flags for e in events] == [MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP]
    events.clear()

    # 4. Two-finger vertical slide -> Wheel scroll
    tp.process(
        DS4InputState(
            touch_1=TouchFinger(True, 6, 700, 500),
            touch_2=TouchFinger(True, 7, 1050, 500),
        ),
        enabled=True,
        now=5.00,
    )
    tp.process(
        DS4InputState(
            touch_1=TouchFinger(True, 6, 700, 450),
            touch_2=TouchFinger(True, 7, 1050, 450),
        ),
        enabled=True,
        now=5.04,
    )
    assert any((e.flags & MOUSEEVENTF_WHEEL) and e.wheel != 0 for e in events)
    tp.process(DS4InputState(), enabled=True, now=5.40)
    events.clear()

    # 5. Physical press in right zone -> Right button hold, and disabling mid-hold releases cleanly
    tp.process(
        DS4InputState(
            touch_click=True,
            touch_1=TouchFinger(True, 8, 1600, 500),
        ),
        enabled=True,
        now=6.00,
    )
    assert [e.flags for e in events] == [MOUSEEVENTF_RIGHTDOWN]
    events.clear()

    tp.process(DS4InputState(touch_click=True), enabled=False, now=6.05)
    assert [e.flags for e in events] == [MOUSEEVENTF_RIGHTUP]

    # Persistence check
    store = ProfileStore(tmp_path / "tp_profiles")
    saved = ControllerProfile(name="CouchPC", touchpad_mouse=True, touchpad_sensitivity=1.4, touchpad_tap_click=False)
    store.save(saved)
    loaded = store.load("CouchPC")
    assert loaded.touchpad_mouse is True
    assert abs(loaded.touchpad_sensitivity - 1.4) < 1e-6
    assert loaded.touchpad_tap_click is False


def test_security_zero_network_and_zero_exec() -> None:
    forbidden_modules = {
        "socket",
        "urllib",
        "http",
        "requests",
        "aiohttp",
        "httpx",
        "ftplib",
        "smtplib",
        "telnetlib",
        "xmlrpc",
        "webbrowser",
        "subprocess",
    }
    forbidden_calls = {"eval", "exec", "compile", "__import__"}

    source_files = sorted(PROJECT_ROOT.glob("*.py"))
    assert len(source_files) >= 5

    for src in source_files:
        text = src.read_text(encoding="utf-8")
        # Verify zero Cyrillic characters and zero emoji clutter remain in the codebase
        assert not any("\u0400" <= ch <= "\u04ff" for ch in text), f"Non-English text in {src.name}"
        assert not any(ord(ch) > 0x2600 for ch in text), f"Emoji character found in {src.name}"

        tree = ast.parse(text, filename=str(src))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden_modules
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden_modules
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls
