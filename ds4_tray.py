"""Dedicated-thread Windows system tray icon and minimalist gamepad icon renderer.

Runs its own Win32 GetMessageW loop on a lightweight background thread and
communicates back to the UI exclusively through a thread-safe SimpleQueue.
This avoids re-entering Tcl/Tk from Win32 WndProc callbacks or TrackPopupMenu
modal loops (which otherwise freezes or crashes Tkinter on Windows) while
keeping idle CPU usage at 0.0% because GetMessageW sleeps in the OS kernel.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import math
import queue
import sys
import threading
import tkinter as tk

IS_WINDOWS = sys.platform == "win32"

# Explicit fixed-width Win32 ABI types (32-bit & 64-bit safe, no ctypes.wintypes)
BYTE = ctypes.c_ubyte
UINT16 = ctypes.c_uint16
INT32 = ctypes.c_int32
UINT32 = ctypes.c_uint32
BOOL = ctypes.c_int32
WCHAR = ctypes.c_wchar
LPCWSTR = ctypes.c_wchar_p
HANDLE = ctypes.c_void_p
UINT_PTR = ctypes.c_size_t
LONG_PTR = ctypes.c_ssize_t
WPARAM = UINT_PTR
LPARAM = LONG_PTR
LRESULT = LONG_PTR

_FUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
WNDPROC = _FUNCTYPE(LRESULT, HANDLE, UINT32, WPARAM, LPARAM)

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

# WM_APP range (0x8000..0xBFFF) is reserved for private window messages
WM_TRAY_CALLBACK = 0x8001
WM_TRAY_OPEN_MENU = 0x8002
WM_TRAY_SYNC = 0x8003
WM_TRAY_QUIT = 0x8004

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

DEFAULT_ICON_TEAL: tuple[int, int, int] = (28, 132, 152)


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", INT32),
        ("y", INT32),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", HANDLE),
        ("message", UINT32),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", UINT32),
        ("pt", POINT),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", UINT32),
        ("Data2", UINT16),
        ("Data3", UINT16),
        ("Data4", BYTE * 8),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", UINT32),
        ("style", UINT32),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", INT32),
        ("cbWndExtra", INT32),
        ("hInstance", HANDLE),
        ("hIcon", HANDLE),
        ("hCursor", HANDLE),
        ("hbrBackground", HANDLE),
        ("lpszMenuName", LPCWSTR),
        ("lpszClassName", LPCWSTR),
        ("hIconSm", HANDLE),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", UINT32),
        ("hWnd", HANDLE),
        ("uID", UINT32),
        ("uFlags", UINT32),
        ("uCallbackMessage", UINT32),
        ("hIcon", HANDLE),
        ("szTip", WCHAR * 128),
        ("dwState", UINT32),
        ("dwStateMask", UINT32),
        ("szInfo", WCHAR * 256),
        ("uTimeoutOrVersion", UINT32),
        ("szInfoTitle", WCHAR * 64),
        ("dwInfoFlags", UINT32),
        ("guidItem", GUID),
        ("hBalloonIcon", HANDLE),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", UINT32),
        ("biWidth", INT32),
        ("biHeight", INT32),
        ("biPlanes", UINT16),
        ("biBitCount", UINT16),
        ("biCompression", UINT32),
        ("biSizeImage", UINT32),
        ("biXPelsPerMeter", INT32),
        ("biYPelsPerMeter", INT32),
        ("biClrUsed", UINT32),
        ("biClrImportant", UINT32),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", BOOL),
        ("xHotspot", UINT32),
        ("yHotspot", UINT32),
        ("hbmMask", HANDLE),
        ("hbmColor", HANDLE),
    ]


@dataclass(frozen=True)
class TrayMenuItem:
    kind: str  # "action", "separator", "label", "submenu"
    label: str = ""
    action_id: str = ""
    checked: bool = False
    default: bool = False
    enabled: bool = True
    children: tuple["TrayMenuItem", ...] = field(default_factory=tuple)


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
    accent_rgb: tuple[int, int, int] = DEFAULT_ICON_TEAL


def _smin(a: float, b: float, k: float) -> float:
    h = max(k - abs(a - b), 0.0) / k
    return min(a, b) - h * h * k * 0.25


def _sd_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    pax, pay = px - ax, py - ay
    bax, bay = bx - ax, by - ay
    h = max(0.0, min(1.0, (pax * bax + pay * bay) / (bax * bax + bay * bay)))
    return math.hypot(pax - bax * h, pay - bay * h)


def render_gamepad_icon_rgba(
    accent_rgb: tuple[int, int, int] = DEFAULT_ICON_TEAL,
    size: int = 32,
) -> bytearray:
    """Render an anti-aliased minimalist outline gamepad icon (D-pad + 4 buttons)."""
    n = max(16, int(size))
    buf = bytearray(n * n * 4)

    ar, ag, ab = (
        max(0, min(255, int(accent_rgb[0]))),
        max(0, min(255, int(accent_rgb[1]))),
        max(0, min(255, int(accent_rgb[2]))),
    )
    if max(ar, ag, ab) < 45:
        ar, ag, ab = DEFAULT_ICON_TEAL

    px_aa = 1.15 / n

    for py in range(n):
        ny = (py + 0.5) / n
        for px in range(n):
            nx = (px + 0.5) / n
            dx = abs(nx - 0.5)

            # Smooth-union rounded controller shell with angled grips & bottom cutout
            arch_y = 0.435 + 0.14 * (dx * dx)
            d_bridge = _sd_segment(dx, ny, 0.0, arch_y, 0.21, 0.445) - 0.175
            d_grip = _sd_segment(dx, ny, 0.21, 0.43, 0.285, 0.595) - 0.145
            d_body = _smin(d_bridge, d_grip, 0.09)
            d_cut = math.hypot(dx / 0.255, (ny - 0.815) / 0.215) - 1.0
            dist_outer = -_smin(-d_body, d_cut * 0.20, 0.065)

            # Hollow constant-width stroke
            a_out = max(0.0, min(1.0, (-dist_outer) / px_aa + 0.5))
            a_in = max(0.0, min(1.0, (dist_outer + 0.065) / px_aa + 0.5))
            shell_a = min(a_out, a_in)

            # Left plus D-Pad (+)
            dpx = abs(nx - 0.335)
            dpy = abs(ny - 0.475)
            d_dpad = min(max(dpx - 0.074, dpy - 0.023), max(dpx - 0.023, dpy - 0.074))
            dpad_a = max(0.0, min(1.0, (-d_dpad) / px_aa + 0.5))

            # Right 4-button diamond
            btn_a = 0.0
            for bx, by in (
                (0.665, 0.412),
                (0.665, 0.538),
                (0.602, 0.475),
                (0.728, 0.475),
            ):
                bd = math.hypot(nx - bx, ny - by) - 0.025
                ba = max(0.0, min(1.0, (-bd) / px_aa + 0.5))
                if ba > btn_a:
                    btn_a = ba

            fg_a = max(shell_a, dpad_a, btn_a)
            if fg_a <= 0.01:
                continue

            idx = (py * n + px) * 4
            buf[idx] = ar
            buf[idx + 1] = ag
            buf[idx + 2] = ab
            buf[idx + 3] = int(round(fg_a * 255))

    return buf


def render_tk_ppm_icon(
    accent_rgb: tuple[int, int, int] = DEFAULT_ICON_TEAL,
    size: int = 32,
    bg_rgb: tuple[int, int, int] = (21, 24, 32),
) -> bytes:
    """Build binary PPM (P6) image bytes with alpha blended over bg_rgb."""
    n = max(16, int(size))
    rgba = render_gamepad_icon_rgba(accent_rgb=accent_rgb, size=n)
    br, bg, bb = bg_rgb
    rgb = bytearray(n * n * 3)
    for i in range(n * n):
        a = rgba[i * 4 + 3] / 255.0
        inv = 1.0 - a
        rgb[i * 3] = int(round(rgba[i * 4] * a + br * inv))
        rgb[i * 3 + 1] = int(round(rgba[i * 4 + 1] * a + bg * inv))
        rgb[i * 3 + 2] = int(round(rgba[i * 4 + 2] * a + bb * inv))
    header = f"P6\n{n} {n}\n255\n".encode("ascii")
    return header + bytes(rgb)


def create_tk_window_icon(
    master: tk.Misc,
    accent_rgb: tuple[int, int, int] = DEFAULT_ICON_TEAL,
    size: int = 32,
    bg_rgb: tuple[int, int, int] = (21, 24, 32),
    transparent_bg: bool = True,
) -> tk.PhotoImage:
    """Build an anti-aliased tk.PhotoImage using pure ASCII hex rows."""
    n = max(16, int(size))
    rgba = render_gamepad_icon_rgba(accent_rgb=accent_rgb, size=n)
    br, bg, bb = bg_rgb
    rows: list[str] = []
    transparent_coords: list[tuple[int, int]] = []

    for py in range(n):
        row_tokens: list[str] = []
        base = py * n * 4
        for px in range(n):
            idx = base + px * 4
            alpha = rgba[idx + 3]
            if alpha <= 8:
                row_tokens.append(f"#{br:02x}{bg:02x}{bb:02x}")
                if transparent_bg:
                    transparent_coords.append((px, py))
            else:
                a = alpha / 255.0
                inv = 1.0 - a
                r = int(round(rgba[idx] * a + br * inv))
                g = int(round(rgba[idx + 1] * a + bg * inv))
                b = int(round(rgba[idx + 2] * a + bb * inv))
                row_tokens.append(f"#{r:02x}{g:02x}{b:02x}")
        rows.append("{" + " ".join(row_tokens) + "}")

    img = tk.PhotoImage(master=master, width=n, height=n)
    img.put(" ".join(rows))
    if transparent_bg and hasattr(img, "transparency_set"):
        try:
            for px, py in transparent_coords:
                img.transparency_set(px, py, True)
        except tk.TclError:
            pass
    return img


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
        items.append(TrayMenuItem(kind="submenu", label="Profile", children=prof_children))
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


class _Win32TrayBindings:
    """Isolated WinDLL function bindings initialized lazily on Windows."""

    def __init__(self) -> None:
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)  # type: ignore[attr-defined]
        self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)  # type: ignore[attr-defined]

        self.kernel32.GetModuleHandleW.argtypes = [LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = HANDLE

        self.user32.RegisterWindowMessageW.argtypes = [LPCWSTR]
        self.user32.RegisterWindowMessageW.restype = UINT32
        self.user32.DefWindowProcW.argtypes = [HANDLE, UINT32, WPARAM, LPARAM]
        self.user32.DefWindowProcW.restype = LRESULT
        self.user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        self.user32.RegisterClassExW.restype = UINT16
        self.user32.UnregisterClassW.argtypes = [LPCWSTR, HANDLE]
        self.user32.UnregisterClassW.restype = BOOL
        self.user32.CreateWindowExW.argtypes = [
            UINT32,
            LPCWSTR,
            LPCWSTR,
            UINT32,
            INT32,
            INT32,
            INT32,
            INT32,
            HANDLE,
            HANDLE,
            HANDLE,
            HANDLE,
        ]
        self.user32.CreateWindowExW.restype = HANDLE
        self.user32.DestroyWindow.argtypes = [HANDLE]
        self.user32.DestroyWindow.restype = BOOL
        self.user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), HANDLE, UINT32, UINT32]
        self.user32.GetMessageW.restype = BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        self.user32.TranslateMessage.restype = BOOL
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        self.user32.DispatchMessageW.restype = LRESULT
        self.user32.PostQuitMessage.argtypes = [INT32]
        self.user32.PostQuitMessage.restype = None
        self.user32.PostMessageW.argtypes = [HANDLE, UINT32, WPARAM, LPARAM]
        self.user32.PostMessageW.restype = BOOL
        self.user32.LoadIconW.argtypes = [HANDLE, HANDLE]
        self.user32.LoadIconW.restype = HANDLE
        self.user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
        self.user32.CreateIconIndirect.restype = HANDLE
        self.user32.DestroyIcon.argtypes = [HANDLE]
        self.user32.DestroyIcon.restype = BOOL
        self.user32.CreatePopupMenu.argtypes = []
        self.user32.CreatePopupMenu.restype = HANDLE
        self.user32.DestroyMenu.argtypes = [HANDLE]
        self.user32.DestroyMenu.restype = BOOL
        self.user32.AppendMenuW.argtypes = [HANDLE, UINT32, UINT_PTR, LPCWSTR]
        self.user32.AppendMenuW.restype = BOOL
        self.user32.SetMenuDefaultItem.argtypes = [HANDLE, UINT32, UINT32]
        self.user32.SetMenuDefaultItem.restype = BOOL
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
        self.user32.GetCursorPos.restype = BOOL
        self.user32.SetForegroundWindow.argtypes = [HANDLE]
        self.user32.SetForegroundWindow.restype = BOOL
        self.user32.TrackPopupMenu.argtypes = [
            HANDLE,
            UINT32,
            INT32,
            INT32,
            INT32,
            HANDLE,
            HANDLE,
        ]
        self.user32.TrackPopupMenu.restype = BOOL
        self.user32.GetDC.argtypes = [HANDLE]
        self.user32.GetDC.restype = HANDLE
        self.user32.ReleaseDC.argtypes = [HANDLE, HANDLE]
        self.user32.ReleaseDC.restype = INT32

        self.gdi32.CreateDIBSection.argtypes = [
            HANDLE,
            ctypes.POINTER(BITMAPINFOHEADER),
            UINT32,
            ctypes.POINTER(HANDLE),
            HANDLE,
            UINT32,
        ]
        self.gdi32.CreateDIBSection.restype = HANDLE
        self.gdi32.CreateBitmap.argtypes = [INT32, INT32, UINT32, UINT32, HANDLE]
        self.gdi32.CreateBitmap.restype = HANDLE
        self.gdi32.DeleteObject.argtypes = [HANDLE]
        self.gdi32.DeleteObject.restype = BOOL

        self.shell32.Shell_NotifyIconW.argtypes = [UINT32, ctypes.POINTER(NOTIFYICONDATAW)]
        self.shell32.Shell_NotifyIconW.restype = BOOL


_WIN32_API: _Win32TrayBindings | None = None


def _get_win32_api() -> _Win32TrayBindings | None:
    global _WIN32_API
    if not IS_WINDOWS:
        return None
    if _WIN32_API is None:
        _WIN32_API = _Win32TrayBindings()
    return _WIN32_API


class SystemTrayIcon:
    """Thread-isolated Windows notification area icon and popup menu."""

    def __init__(
        self,
        *,
        initial_snapshot: TraySnapshot | None = None,
        win32_api: object | None = None,
    ) -> None:
        self._injected_api = win32_api
        self._lock = threading.Lock()
        self._snapshot: TraySnapshot = initial_snapshot or TraySnapshot()
        self._pending_tooltip: str | None = None
        self._pending_balloon: tuple[str, str] | None = None
        self._action_queue: queue.SimpleQueue[str] = queue.SimpleQueue()

        self.available: bool = False
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._api = None
        self._hwnd: int | None = None
        self._hinstance: int | None = None
        self._class_name = f"OpenDS4Tray_{id(self)}"
        self._hicon: int | None = None
        self._owns_hicon: bool = False
        self._wndproc_ref = None
        self._wm_taskbar_created: int = 0
        self._applied_tooltip: str = ""

    def pop_action(self) -> str | None:
        """Non-blocking poll from the main UI thread."""
        try:
            return self._action_queue.get_nowait()
        except queue.Empty:
            return None

    def start(self, initial_rgb: tuple[int, int, int] = DEFAULT_ICON_TEAL) -> bool:
        if self.available:
            return True

        api = self._injected_api if self._injected_api is not None else _get_win32_api()
        if api is None:
            return False

        self._api = api
        self._ready_event.clear()
        self._thread = threading.Thread(
            target=self._run_tray_thread,
            args=(initial_rgb,),
            name="OpenDS4-Tray",
            daemon=True,
        )
        self._thread.start()
        self._ready_event.wait(timeout=1.5)
        return self.available

    def stop(self) -> None:
        api = self._api
        with self._lock:
            hwnd = self._hwnd
        if api is not None and hwnd:
            try:
                api.user32.PostMessageW(hwnd, WM_TRAY_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self.available = False

    def set_snapshot(self, snap: TraySnapshot) -> None:
        """Publish latest controller/menu state without waking the tray thread unless tooltip changed."""
        tip = snap.tooltip[:120]
        need_wake = False
        with self._lock:
            self._snapshot = snap
            if tip != self._applied_tooltip and tip != self._pending_tooltip:
                self._pending_tooltip = tip
                need_wake = True
            hwnd = self._hwnd

        if need_wake and self.available and hwnd and self._api is not None:
            try:
                self._api.user32.PostMessageW(hwnd, WM_TRAY_SYNC, 0, 0)
            except Exception:
                pass

    def show_balloon(self, title: str, message: str) -> None:
        with self._lock:
            if not self.available or not self._hwnd or self._api is None:
                return
            self._pending_balloon = (title[:60], message[:250])
            hwnd = self._hwnd
            api = self._api
        try:
            api.user32.PostMessageW(hwnd, WM_TRAY_SYNC, 0, 0)
        except Exception:
            pass

    # -------------------- Dedicated Tray Thread --------------------

    def _run_tray_thread(self, initial_rgb: tuple[int, int, int]) -> None:
        api = self._api
        if api is None:
            self._ready_event.set()
            return

        hwnd = None
        try:
            self._hinstance = api.kernel32.GetModuleHandleW(None)
            self._wm_taskbar_created = int(api.user32.RegisterWindowMessageW("TaskbarCreated") or 0)
            self._wndproc_ref = WNDPROC(self._wnd_proc)

            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = self._hinstance
            wc.lpszClassName = self._class_name
            api.user32.RegisterClassExW(ctypes.byref(wc))

            created = api.user32.CreateWindowExW(
                0,
                self._class_name,
                "OpenDS4 Tray",
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
            if not created:
                self._ready_event.set()
                return
            hwnd = int(created)
            with self._lock:
                self._hwnd = hwnd

            self._hicon, self._owns_hicon = self._create_win32_icon(DEFAULT_ICON_TEAL)
            if not self._add_notify_icon("OpenDS4"):
                api.user32.DestroyWindow(hwnd)
                with self._lock:
                    self._hwnd = None
                self._ready_event.set()
                return

            self.available = True
            self._ready_event.set()

            msg = MSG()
            while True:
                ret = int(api.user32.GetMessageW(ctypes.byref(msg), None, 0, 0))
                if ret <= 0:
                    break
                api.user32.TranslateMessage(ctypes.byref(msg))
                api.user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            self.available = False
            self._ready_event.set()
        finally:
            self._cleanup_on_tray_thread()

    def _cleanup_on_tray_thread(self) -> None:
        api = self._api
        with self._lock:
            hwnd = self._hwnd
            self._hwnd = None
        self.available = False
        if api is None:
            return

        if hwnd:
            try:
                nid = NOTIFYICONDATAW()
                nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                nid.hWnd = hwnd
                nid.uID = 1
                api.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            except Exception:
                pass

        if self._hicon and self._owns_hicon:
            try:
                api.user32.DestroyIcon(self._hicon)
            except Exception:
                pass
        self._hicon = None
        self._owns_hicon = False

        if hwnd:
            try:
                api.user32.DestroyWindow(hwnd)
            except Exception:
                pass
        if self._hinstance:
            try:
                api.user32.UnregisterClassW(self._class_name, self._hinstance)
            except Exception:
                pass
            self._hinstance = None

    def _add_notify_icon(self, tip: str) -> bool:
        api = self._api
        if api is None or not self._hwnd:
            return False
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY_CALLBACK
        nid.hIcon = self._hicon
        nid.szTip = tip[:120]
        with self._lock:
            self._applied_tooltip = tip[:120]
        return bool(api.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _apply_pending_sync(self) -> None:
        api = self._api
        with self._lock:
            hwnd = self._hwnd
            tip = self._pending_tooltip
            balloon = self._pending_balloon
            self._pending_tooltip = None
            self._pending_balloon = None
            if tip is not None:
                self._applied_tooltip = tip

        if api is None or not hwnd:
            return

        if tip is not None:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = hwnd
            nid.uID = 1
            nid.uFlags = NIF_TIP
            nid.szTip = tip
            api.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

        if balloon is not None:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = hwnd
            nid.uID = 1
            nid.uFlags = NIF_INFO
            nid.szInfoTitle = balloon[0]
            nid.szInfo = balloon[1]
            nid.dwInfoFlags = NIIF_NONE
            api.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _create_win32_icon(self, accent_rgb: tuple[int, int, int]) -> tuple[int | None, bool]:
        api = self._api
        if api is None:
            return None, False

        n = 32
        rgba = render_gamepad_icon_rgba(accent_rgb=accent_rgb, size=n)

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = n
        bmi.biHeight = -n  # Top-down 32-bpp ARGB DIB
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = DIB_RGB_COLORS

        hdc = api.user32.GetDC(None)
        bits_ptr = HANDLE()
        hbm_color = api.gdi32.CreateDIBSection(
            hdc,
            ctypes.byref(bmi),
            DIB_RGB_COLORS,
            ctypes.byref(bits_ptr),
            None,
            0,
        )
        api.user32.ReleaseDC(None, hdc)

        if hbm_color and bits_ptr.value:
            bgra = bytearray(n * n * 4)
            row_bytes = (n + 7) // 8
            # WORD-aligned scanlines for CreateBitmap 1-bpp AND mask
            stride = ((row_bytes + 1) // 2) * 2
            and_mask = bytearray(stride * n)

            for py in range(n):
                for px in range(n):
                    i = py * n + px
                    a = rgba[i * 4 + 3]
                    # Premultiplied BGRA for crisp Windows DWM alpha blending
                    bgra[i * 4] = (rgba[i * 4 + 2] * a) // 255
                    bgra[i * 4 + 1] = (rgba[i * 4 + 1] * a) // 255
                    bgra[i * 4 + 2] = (rgba[i * 4] * a) // 255
                    bgra[i * 4 + 3] = a
                    if a == 0:
                        and_mask[py * stride + (px >> 3)] |= 0x80 >> (px & 7)

            ctypes.memmove(bits_ptr.value, bytes(bgra), len(bgra))

            mask_buf = (BYTE * len(and_mask))(*and_mask)
            hbm_mask = api.gdi32.CreateBitmap(n, n, 1, 1, mask_buf)
            if hbm_mask:
                info = ICONINFO()
                info.fIcon = 1
                info.hbmMask = hbm_mask
                info.hbmColor = hbm_color
                hicon = api.user32.CreateIconIndirect(ctypes.byref(info))
                api.gdi32.DeleteObject(hbm_mask)
                api.gdi32.DeleteObject(hbm_color)
                if hicon:
                    return int(hicon), True
            api.gdi32.DeleteObject(hbm_color)

        fallback = api.user32.LoadIconW(None, IDI_APPLICATION_PTR)
        return (int(fallback) if fallback else None), False

    def _populate_hmenu(
        self,
        hmenu: int,
        items: tuple[TrayMenuItem, ...] | list[TrayMenuItem],
        cmd_map: dict[int, str],
        submenus: list[int],
        next_id: list[int],
    ) -> None:
        api = self._api
        if api is None:
            return
        for item in items:
            if item.kind == "separator":
                api.user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            elif item.kind == "label":
                api.user32.AppendMenuW(hmenu, MF_STRING | MF_GRAYED | MF_DISABLED, 0, item.label)
            elif item.kind == "submenu":
                sub = api.user32.CreatePopupMenu()
                if sub:
                    sub_int = int(sub)
                    submenus.append(sub_int)
                    self._populate_hmenu(sub_int, item.children, cmd_map, submenus, next_id)
                    api.user32.AppendMenuW(hmenu, MF_POPUP, sub_int, item.label)
            elif item.kind == "action":
                cid = next_id[0]
                next_id[0] += 1
                flags = MF_STRING
                if item.checked:
                    flags |= MF_CHECKED
                if not item.enabled:
                    flags |= MF_GRAYED | MF_DISABLED
                api.user32.AppendMenuW(hmenu, flags, cid, item.label)
                cmd_map[cid] = item.action_id
                if item.default:
                    api.user32.SetMenuDefaultItem(hmenu, cid, 0)

    def _show_context_menu(self) -> None:
        api = self._api
        with self._lock:
            hwnd = self._hwnd
            snap = self._snapshot
        if not hwnd or api is None:
            return

        tree = build_tray_menu_tree(snap)
        hmenu = api.user32.CreatePopupMenu()
        if not hmenu:
            return
        hmenu_int = int(hmenu)

        cmd_map: dict[int, str] = {}
        submenus: list[int] = []
        self._populate_hmenu(hmenu_int, tree, cmd_map, submenus, [100])

        pt = POINT()
        api.user32.GetCursorPos(ctypes.byref(pt))
        api.user32.SetForegroundWindow(hwnd)
        chosen = int(
            api.user32.TrackPopupMenu(
                hmenu_int,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                pt.x,
                pt.y,
                0,
                hwnd,
                None,
            )
        )
        api.user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        api.user32.DestroyMenu(hmenu_int)

        action_id = cmd_map.get(chosen)
        if action_id:
            self._action_queue.put(action_id)

    def _wnd_proc(self, hwnd: int | None, msg: int, wparam: int, lparam: int) -> int:
        try:
            api = self._api
            if msg == WM_TRAY_CALLBACK:
                event_code = lparam & 0xFFFF
                if event_code in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._action_queue.put("restore")
                    return 0
                if event_code in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    if api is not None and hwnd:
                        api.user32.PostMessageW(hwnd, WM_TRAY_OPEN_MENU, 0, 0)
                    return 0
                return 0

            if msg == WM_TRAY_OPEN_MENU:
                self._show_context_menu()
                return 0

            if msg == WM_TRAY_SYNC:
                self._apply_pending_sync()
                return 0

            if msg in (WM_TRAY_QUIT, WM_CLOSE):
                if api is not None:
                    api.user32.PostQuitMessage(0)
                return 0

            if self._wm_taskbar_created and msg == self._wm_taskbar_created:
                with self._lock:
                    tip = self._applied_tooltip or "OpenDS4"
                self._add_notify_icon(tip)
                return 0

            if msg == WM_DESTROY:
                return 0

            if api is None:
                return 0
            return int(api.user32.DefWindowProcW(hwnd, msg, wparam, lparam))
        except Exception:
            return 0
