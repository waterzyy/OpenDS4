"""System tray icon and quick-settings menu for OpenDS4 on Windows.

Uses standard Win32 Shell_NotifyIconW via ctypes so no third-party tray
packages or binary icon assets are required. On non-Windows platforms this
module degrades cleanly into a no-op while still providing programmatic
window icon generation and menu structure helpers.
"""

from __future__ import annotations

import ctypes
import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class TrayMenuItem:
    kind: str  # "action", "separator", "label", "submenu"
    label: str = ""
    action_id: str = ""
    checked: bool = False
    default: bool = False
    enabled: bool = True
    children: tuple[TrayMenuItem, ...] = field(default_factory=tuple)


@dataclass
class TraySnapshot:
    status_line: str = "Searching for DualShock 4..."
    tooltip: str = "OpenDS4"
    active_profile: str = "Default"
    profile_names: list[str] = field(default_factory=lambda: ["Default"])
    active_lightbar_mode: str = "static"
    lightbar_modes: dict[str, str] = field(default_factory=dict)
    rumble_enabled: bool = True
    trigger_rumble: bool = False
    touchpad_mouse: bool = False
    engine_running: bool = True
    accent_rgb: tuple[int, int, int] = (59, 130, 246)


def _clamp_u8(val: float) -> int:
    return max(0, min(255, int(round(val))))


def render_gamepad_icon_rgba(
    accent_rgb: tuple[int, int, int] = (59, 130, 246),
    size: int = 32,
) -> bytearray:
    """Render a crisp RGBA controller icon with a colored lightbar accent."""
    n = max(16, int(size))
    buf = bytearray(n * n * 4)

    ar, ag, ab = (
        max(0, min(255, int(accent_rgb[0]))),
        max(0, min(255, int(accent_rgb[1]))),
        max(0, min(255, int(accent_rgb[2]))),
    )
    if ar < 28 and ag < 28 and ab < 28:
        ar, ag, ab = 75, 95, 130

    body_col = (30, 35, 48)
    rim_col = (68, 78, 102)
    stick_col = (16, 19, 26)
    ring_col = (145, 160, 188)

    for py in range(n):
        ny = (py + 0.5) / n
        for px in range(n):
            nx = (px + 0.5) / n
            dx = abs(nx - 0.5)

            # Controller silhouette in normalized coordinates:
            # Central bridge + two ergonomic grips sloping downward
            in_bridge = (dx <= 0.28) and (0.24 <= ny <= 0.63)

            grip_cx = 0.27 + (ny - 0.34) * 0.20
            grip_rx = 0.145 - max(0.0, ny - 0.56) * 0.11
            grip_dy = (ny - 0.53) / 0.31
            grip_dx = (dx - grip_cx) / max(0.04, grip_rx)
            grip_dist = math.hypot(grip_dx, grip_dy)
            in_grip = (ny >= 0.27) and (grip_dist <= 1.0)

            # Top shoulder caps (L1 / R1)
            sh_dist = math.hypot((dx - 0.25) / 0.13, (ny - 0.26) / 0.08)
            in_shoulder = sh_dist <= 1.0

            if not (in_bridge or in_grip or in_shoulder):
                continue

            # Edge highlight detection
            on_rim = (
                (in_bridge and (ny < 0.275 or (ny > 0.595 and dx < 0.18)))
                or (in_grip and grip_dist > 0.84)
                or (in_shoulder and sh_dist > 0.80 and ny < 0.25)
            )
            r, g, b = rim_col if on_rim else body_col

            # Top DualShock lightbar strip
            if dx <= 0.19 and 0.245 <= ny <= 0.325:
                glow = 1.0 - (dx / 0.22) * 0.25
                r = _clamp_u8(ar * glow + 35 * (1.0 - glow))
                g = _clamp_u8(ag * glow + 40 * (1.0 - glow))
                b = _clamp_u8(ab * glow + 50 * (1.0 - glow))

            # Analog stick wells and subtle rings
            stick_d = math.hypot(dx - 0.155, ny - 0.515)
            if stick_d <= 0.082:
                if stick_d >= 0.058:
                    r, g, b = ring_col
                else:
                    r, g, b = stick_col

            # Touchpad rectangle in upper middle
            elif dx <= 0.14 and 0.34 <= ny <= 0.435:
                r, g, b = (21, 25, 34)

            idx = (py * n + px) * 4
            buf[idx] = r
            buf[idx + 1] = g
            buf[idx + 2] = b
            buf[idx + 3] = 255

    return buf


def render_tk_ppm_icon(
    accent_rgb: tuple[int, int, int] = (59, 130, 246),
    size: int = 32,
    bg_rgb: tuple[int, int, int] = (17, 19, 24),
) -> bytes:
    """Build binary PPM (P6) image bytes accepted natively by tk.PhotoImage."""
    n = max(16, int(size))
    rgba = render_gamepad_icon_rgba(accent_rgb=accent_rgb, size=n)
    br, bg, bb = bg_rgb
    rgb = bytearray(n * n * 3)
    for i in range(n * n):
        a = rgba[i * 4 + 3]
        if a:
            rgb[i * 3] = rgba[i * 4]
            rgb[i * 3 + 1] = rgba[i * 4 + 1]
            rgb[i * 3 + 2] = rgba[i * 4 + 2]
        else:
            rgb[i * 3] = br
            rgb[i * 3 + 1] = bg
            rgb[i * 3 + 2] = bb
    header = f"P6\n{n} {n}\n255\n".encode("ascii")
    return header + bytes(rgb)


def build_tray_menu_tree(snap: TraySnapshot) -> list[TrayMenuItem]:
    """Construct the structured context menu descriptor from a TraySnapshot."""
    prof_children = tuple(
        TrayMenuItem(
            kind="action",
            label=name,
            action_id=f"profile:{name}",
            checked=(name == snap.active_profile),
        )
        for name in snap.profile_names
    )

    led_children = tuple(
        TrayMenuItem(
            kind="action",
            label=title,
            action_id=f"lightbar:{mode_key}",
            checked=(mode_key == snap.active_lightbar_mode),
        )
        for mode_key, title in snap.lightbar_modes.items()
    )

    items: list[TrayMenuItem] = [
        TrayMenuItem(kind="action", label="Open OpenDS4", action_id="restore", default=True),
        TrayMenuItem(kind="separator"),
        TrayMenuItem(kind="label", label=snap.status_line, enabled=False),
    ]
    if prof_children:
        items.append(TrayMenuItem(kind="submenu", label="Active Profile", children=prof_children))
    if led_children:
        items.append(TrayMenuItem(kind="submenu", label="Lightbar Mode", children=led_children))

    items.extend(
        [
            TrayMenuItem(kind="separator"),
            TrayMenuItem(
                kind="action",
                label="Touchpad Mouse",
                action_id="toggle_touchpad",
                checked=snap.touchpad_mouse,
            ),
            TrayMenuItem(
                kind="action",
                label="Controller Vibration",
                action_id="toggle_rumble",
                checked=snap.rumble_enabled,
            ),
            TrayMenuItem(
                kind="action",
                label="L2/R2 Trigger Haptics",
                action_id="toggle_trigger_haptics",
                checked=snap.trigger_rumble,
            ),
            TrayMenuItem(
                kind="action",
                label="Mapper Active",
                action_id="toggle_engine",
                checked=snap.engine_running,
            ),
            TrayMenuItem(kind="separator"),
            TrayMenuItem(kind="action", label="Quit OpenDS4", action_id="quit"),
        ]
    )
    return items


if IS_WINDOWS:
    from ctypes import wintypes

    # ctypes.wintypes omits HCURSOR on some Python versions; it is a plain handle.
    HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)

    LRESULT = ctypes.c_ssize_t
    WPARAM = ctypes.c_size_t
    LPARAM = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)

    WM_NULL = 0x0000
    WM_DESTROY = 0x0002
    WM_CONTEXTMENU = 0x007B
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205
    WM_TRAY_CALLBACK = 0x0400 + 44

    NIM_ADD = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002

    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004
    NIF_INFO = 0x00000010
    NIIF_NONE = 0x00000000

    MF_STRING = 0x00000000
    MF_GRAYED = 0x00000001
    MF_DISABLED = 0x00000002
    MF_CHECKED = 0x00000008
    MF_POPUP = 0x00000010
    MF_SEPARATOR = 0x00000800

    TPM_RIGHTBUTTON = 0x0002
    TPM_NONOTIFY = 0x0080
    TPM_RETURNCMD = 0x0100

    IDI_APPLICATION_PTR = ctypes.c_void_p(32512)
    DIB_RGB_COLORS = 0

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", HCURSOR),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL),
            ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask", wintypes.HBITMAP),
            ("hbmColor", wintypes.HBITMAP),
        ]

    _kernel32 = ctypes.windll.kernel32
    _user32 = ctypes.windll.user32
    _gdi32 = ctypes.windll.gdi32
    _shell32 = ctypes.windll.shell32

    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    _user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterWindowMessageW.restype = wintypes.UINT
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    _user32.DefWindowProcW.restype = LRESULT
    _user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    _user32.RegisterClassExW.restype = wintypes.ATOM
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    _user32.UnregisterClassW.restype = wintypes.BOOL
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    _user32.LoadIconW.restype = wintypes.HICON
    _user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
    _user32.CreateIconIndirect.restype = wintypes.HICON
    _user32.DestroyIcon.argtypes = [wintypes.HICON]
    _user32.DestroyIcon.restype = wintypes.BOOL
    _user32.CreatePopupMenu.argtypes = []
    _user32.CreatePopupMenu.restype = wintypes.HMENU
    _user32.DestroyMenu.argtypes = [wintypes.HMENU]
    _user32.DestroyMenu.restype = wintypes.BOOL
    _user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    _user32.AppendMenuW.restype = wintypes.BOOL
    _user32.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
    _user32.SetMenuDefaultItem.restype = wintypes.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.TrackPopupMenu.argtypes = [
        wintypes.HMENU,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        ctypes.c_void_p,
    ]
    _user32.TrackPopupMenu.restype = wintypes.BOOL
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.GetDC.restype = wintypes.HDC
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _user32.ReleaseDC.restype = ctypes.c_int

    _gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(BITMAPINFOHEADER),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    _gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    _gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
    _gdi32.CreateBitmap.restype = wintypes.HBITMAP
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _gdi32.DeleteObject.restype = wintypes.BOOL

    _shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL


class SystemTrayIcon:
    """Manages a Windows notification area icon and right-click menu."""

    def __init__(
        self,
        *,
        get_snapshot: Callable[[], TraySnapshot],
        on_action: Callable[[str], None],
        schedule_ui: Callable[[Callable[[], None]], None],
    ) -> None:
        self._get_snapshot = get_snapshot
        self._on_action = on_action
        self._schedule_ui = schedule_ui

        self.available: bool = False
        self._hwnd: int | None = None
        self._hinstance: int | None = None
        self._class_name = f"OpenDS4TrayHost_{id(self)}"
        self._hicon: int | None = None
        self._owns_hicon: bool = False
        self._wndproc_ref = None
        self._wm_taskbar_created: int = 0

        self._last_tooltip: str = ""
        self._last_color_bucket: tuple[int, int, int] = (-1, -1, -1)
        self._last_icon_ts: float = 0.0

    def start(self, initial_rgb: tuple[int, int, int] = (59, 130, 246)) -> bool:
        if not IS_WINDOWS or self.available:
            return self.available

        try:
            self._hinstance = _kernel32.GetModuleHandleW(None)
            self._wm_taskbar_created = _user32.RegisterWindowMessageW("TaskbarCreated")
            self._wndproc_ref = WNDPROC(self._wnd_proc)

            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = self._hinstance
            wc.lpszClassName = self._class_name
            _user32.RegisterClassExW(ctypes.byref(wc))

            hwnd = _user32.CreateWindowExW(
                0,
                self._class_name,
                "OpenDS4 Tray Host",
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                self._hinstance,
                None,
            )
            if not hwnd:
                return False
            self._hwnd = hwnd

            self._hicon, self._owns_hicon = self._create_win32_icon(initial_rgb)
            self._last_color_bucket = (initial_rgb[0] >> 4, initial_rgb[1] >> 4, initial_rgb[2] >> 4)
            self._last_icon_ts = time.monotonic()

            if not self._add_notify_icon("OpenDS4"):
                _user32.DestroyWindow(hwnd)
                self._hwnd = None
                return False

            self.available = True
            return True
        except Exception:
            self.available = False
            return False

    def stop(self) -> None:
        if not IS_WINDOWS:
            return
        if self.available and self._hwnd:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self._hwnd
            nid.uID = 1
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self.available = False

        if self._hicon and self._owns_hicon:
            _user32.DestroyIcon(self._hicon)
        self._hicon = None
        self._owns_hicon = False

        if self._hwnd:
            _user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        if self._hinstance:
            _user32.UnregisterClassW(self._class_name, self._hinstance)
            self._hinstance = None

    def update_state(self, tooltip: str, accent_rgb: tuple[int, int, int]) -> None:
        if not IS_WINDOWS or not self.available or not self._hwnd:
            return

        now = time.monotonic()
        tip_trimmed = tooltip[:120]
        bucket = (accent_rgb[0] >> 4, accent_rgb[1] >> 4, accent_rgb[2] >> 4)

        need_tip = tip_trimmed != self._last_tooltip
        need_icon = (bucket != self._last_color_bucket) and ((now - self._last_icon_ts) >= 0.85)
        if not need_tip and not need_icon:
            return

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        flags = 0

        if need_tip:
            nid.szTip = tip_trimmed
            flags |= NIF_TIP
            self._last_tooltip = tip_trimmed

        old_icon = None
        if need_icon:
            new_icon, owns_new = self._create_win32_icon(accent_rgb)
            if new_icon:
                if self._owns_hicon and self._hicon:
                    old_icon = self._hicon
                self._hicon = new_icon
                self._owns_hicon = owns_new
                nid.hIcon = self._hicon
                flags |= NIF_ICON
                self._last_color_bucket = bucket
                self._last_icon_ts = now

        if flags:
            nid.uFlags = flags
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

        if old_icon:
            _user32.DestroyIcon(old_icon)

    def show_balloon(self, title: str, message: str) -> None:
        if not IS_WINDOWS or not self.available or not self._hwnd:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = title[:60]
        nid.szInfo = message[:250]
        nid.dwInfoFlags = NIIF_NONE
        _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    # -------------------- Internal Win32 Helpers --------------------

    def _add_notify_icon(self, tip: str) -> bool:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY_CALLBACK
        nid.hIcon = self._hicon
        nid.szTip = tip[:120]
        self._last_tooltip = tip[:120]
        return bool(_shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _create_win32_icon(self, accent_rgb: tuple[int, int, int]) -> tuple[int, bool]:
        n = 32
        rgba = render_gamepad_icon_rgba(accent_rgb=accent_rgb, size=n)

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = n
        bmi.biHeight = -n  # Top-down DIB
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = DIB_RGB_COLORS

        hdc = _user32.GetDC(None)
        bits_ptr = ctypes.c_void_p()
        hbm_color = _gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits_ptr), None, 0)
        _user32.ReleaseDC(None, hdc)

        if hbm_color and bits_ptr.value:
            bgra = bytearray(n * n * 4)
            for i in range(n * n):
                bgra[i * 4] = rgba[i * 4 + 2]  # B
                bgra[i * 4 + 1] = rgba[i * 4 + 1]  # G
                bgra[i * 4 + 2] = rgba[i * 4]  # R
                bgra[i * 4 + 3] = rgba[i * 4 + 3]  # A
            ctypes.memmove(bits_ptr.value, bytes(bgra), len(bgra))

            mask_bytes = bytes((n * n) // 8)
            hbm_mask = _gdi32.CreateBitmap(n, n, 1, 1, mask_bytes)
            if hbm_mask:
                info = ICONINFO()
                info.fIcon = True
                info.hbmMask = hbm_mask
                info.hbmColor = hbm_color
                hicon = _user32.CreateIconIndirect(ctypes.byref(info))
                _gdi32.DeleteObject(hbm_mask)
                _gdi32.DeleteObject(hbm_color)
                if hicon:
                    return int(hicon), True
            _gdi32.DeleteObject(hbm_color)

        fallback = _user32.LoadIconW(None, IDI_APPLICATION_PTR)
        return int(fallback or 0), False

    def _populate_hmenu(
        self,
        hmenu: int,
        items: tuple[TrayMenuItem, ...] | list[TrayMenuItem],
        cmd_map: dict[int, str],
        submenus: list[int],
        next_id: list[int],
    ) -> None:
        for item in items:
            if item.kind == "separator":
                _user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            elif item.kind == "label":
                _user32.AppendMenuW(hmenu, MF_STRING | MF_GRAYED | MF_DISABLED, 0, item.label)
            elif item.kind == "submenu":
                sub = _user32.CreatePopupMenu()
                if sub:
                    submenus.append(sub)
                    self._populate_hmenu(sub, item.children, cmd_map, submenus, next_id)
                    _user32.AppendMenuW(hmenu, MF_POPUP, int(sub), item.label)
            elif item.kind == "action":
                cid = next_id[0]
                next_id[0] += 1
                flags = MF_STRING
                if item.checked:
                    flags |= MF_CHECKED
                if not item.enabled:
                    flags |= MF_GRAYED | MF_DISABLED
                _user32.AppendMenuW(hmenu, flags, cid, item.label)
                cmd_map[cid] = item.action_id
                if item.default:
                    _user32.SetMenuDefaultItem(hmenu, cid, 0)

    def _show_context_menu(self) -> None:
        if not self._hwnd:
            return
        snap = self._get_snapshot()
        tree = build_tray_menu_tree(snap)

        hmenu = _user32.CreatePopupMenu()
        if not hmenu:
            return

        cmd_map: dict[int, str] = {}
        submenus: list[int] = []
        self._populate_hmenu(hmenu, tree, cmd_map, submenus, [100])

        pt = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        _user32.SetForegroundWindow(self._hwnd)
        chosen = int(
            _user32.TrackPopupMenu(
                hmenu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                pt.x,
                pt.y,
                0,
                self._hwnd,
                None,
            )
        )
        _user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        _user32.DestroyMenu(hmenu)

        action_id = cmd_map.get(chosen)
        if action_id:
            self._schedule_ui(lambda act=action_id: self._on_action(act))

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_TRAY_CALLBACK:
            event_code = lparam & 0xFFFF
            if event_code in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._schedule_ui(lambda: self._on_action("restore"))
                return 0
            if event_code in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_context_menu()
                return 0
            return 0

        if self._wm_taskbar_created and msg == self._wm_taskbar_created:
            self._add_notify_icon(self._last_tooltip or "OpenDS4")
            return 0

        if msg == WM_DESTROY:
            return 0

        return int(_user32.DefWindowProcW(hwnd, msg, wparam, lparam))
