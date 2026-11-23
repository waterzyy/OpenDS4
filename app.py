"""OpenDS4 desktop interface."""

from __future__ import annotations

from collections.abc import Callable
import ctypes
from pathlib import Path
import queue
import site
import sys
import time
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk
import traceback


def _attach_local_venv() -> None:
    """Allow 'python app.py' from CMD to find packages installed inside local .venv."""
    if sys.prefix != sys.base_prefix:
        return
    venv_dir = Path(__file__).resolve().parent / ".venv"
    cfg_path = venv_dir / "pyvenv.cfg"
    if not cfg_path.is_file():
        return
    try:
        expected_ver = f"{sys.version_info.major}.{sys.version_info.minor}."
        cfg_text = cfg_path.read_text(encoding="utf-8", errors="ignore")
        version_lines = [
            ln.split("=", 1)[1].strip()
            for ln in cfg_text.splitlines()
            if ln.strip().lower().startswith("version") and "=" in ln
        ]
        if version_lines and not any(v.startswith(expected_ver) for v in version_lines):
            return
    except OSError:
        return

    candidates = [
        venv_dir / "Lib" / "site-packages",
        venv_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
    ]
    for site_dir in candidates:
        if site_dir.is_dir():
            site.addsitedir(str(site_dir))
            break


_attach_local_venv()

from ds4_engine import DS4MapperEngine, EngineTelemetry
from ds4_profile import (
    CURVE_MODES,
    DEFAULT_BUTTON_MAP,
    DS4_BUTTON_LABELS,
    LIGHTBAR_MODES,
    XBOX_TARGETS,
    ControllerProfile,
    ProfileStore,
)
from ds4_protocol import DS4_TOUCHPAD_HEIGHT, DS4_TOUCHPAD_WIDTH, apply_curve
from ds4_tray import DEFAULT_ICON_TEAL, SystemTrayIcon, TraySnapshot, create_tk_window_icon

# Palette
BG_WINDOW = "#111318"
BG_SIDEBAR = "#151820"
BG_SURFACE = "#191d26"
BG_ELEVATED = "#222734"
BG_HOVER = "#2a3142"
BG_INPUT = "#14171f"
BORDER = "#262c3a"
FG_TEXT = "#eceff4"
FG_SUB = "#949db0"
FG_MUTED = "#626b7f"

ACCENT = "#4c8bf5"
ACCENT_HOVER = "#639bf7"
GREEN = "#3ecf8e"
AMBER = "#f0a43a"
RED = "#e55353"
CYAN = "#38bdf8"

TRIGGER_BAR_HEIGHT = 14

STATIC_SWATCHES: list[tuple[str, int, int, int]] = [
    ("Blue", 0, 90, 255),
    ("Crimson", 255, 25, 40),
    ("Emerald", 0, 215, 115),
    ("Violet", 155, 45, 255),
    ("Amber", 255, 155, 0),
    ("Ice", 0, 210, 255),
    ("White", 235, 240, 255),
    ("Off", 0, 0, 0),
]


def _blend_hex(hex_a: str, hex_b: str, t: float) -> str:
    """Linearly interpolate two #RRGGBB colors."""
    k = max(0.0, min(1.0, float(t)))
    ra, ga, ba = int(hex_a[1:3], 16), int(hex_a[3:5], 16), int(hex_a[5:7], 16)
    rb, gb, bb = int(hex_b[1:3], 16), int(hex_b[3:5], 16), int(hex_b[5:7], 16)
    r = int(round(ra + (rb - ra) * k))
    g = int(round(ga + (gb - ga) * k))
    b = int(round(ba + (bb - ba) * k))
    return f"#{r:02x}{g:02x}{b:02x}"


def _enable_windows_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)  # type: ignore[attr-defined]
        shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
            user32.SetProcessDPIAware()
        except Exception:
            pass


def _apply_windows_dark_frame(root: tk.Tk) -> None:
    """Apply dark titlebar and border colors via Win32 DWM (eliminates top white bar)."""
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)  # type: ignore[attr-defined]

        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        dwmapi.DwmSetWindowAttribute.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_int32

        child_hwnd = int(root.winfo_id())
        top_hwnd = user32.GetAncestor(ctypes.c_void_p(child_hwnd), 2)  # GA_ROOT = 2
        if not top_hwnd:
            top_hwnd = user32.GetParent(ctypes.c_void_p(child_hwnd)) or child_hwnd

        dark_val = ctypes.c_int32(1)
        # Immersive dark mode (Windows 10 2004+ = 20, older Windows 10 = 19)
        if dwmapi.DwmSetWindowAttribute(top_hwnd, 20, ctypes.byref(dark_val), 4) != 0:
            dwmapi.DwmSetWindowAttribute(top_hwnd, 19, ctypes.byref(dark_val), 4)

        # Windows 11 exact caption (#151820), text (#eceff4), and border (#262c3a) in COLORREF 0x00BBGGRR
        caption_col = ctypes.c_uint32(0x00201815)
        text_col = ctypes.c_uint32(0x00F4EFEC)
        border_col = ctypes.c_uint32(0x003A2C26)
        dwmapi.DwmSetWindowAttribute(top_hwnd, 35, ctypes.byref(caption_col), 4)
        dwmapi.DwmSetWindowAttribute(top_hwnd, 36, ctypes.byref(text_col), 4)
        dwmapi.DwmSetWindowAttribute(top_hwnd, 34, ctypes.byref(border_col), 4)
    except Exception:
        pass


class ModernSlider(tk.Frame):
    """Lightweight Canvas slider with O(1) drag updates."""

    def __init__(
        self,
        parent: tk.Widget,
        label: str,
        *,
        min_val: float,
        max_val: float,
        initial: float,
        fmt: Callable[[float], str],
        on_change: Callable[[float], None],
        accent: str = ACCENT,
        bg: str = BG_SURFACE,
    ) -> None:
        super().__init__(parent, bg=bg)
        self._min = float(min_val)
        self._max = float(max_val)
        self._val = max(self._min, min(self._max, float(initial)))
        self._fmt = fmt
        self._on_change = on_change
        self._accent = accent
        self._silent = False

        top = tk.Frame(self, bg=bg)
        top.pack(fill="x")
        tk.Label(
            top,
            text=label,
            bg=bg,
            fg=FG_SUB,
            font=("Segoe UI", 9),
        ).pack(side="left")
        self._val_lbl = tk.Label(
            top,
            text=self._fmt(self._val),
            bg=bg,
            fg=FG_TEXT,
            font=("Segoe UI Semibold", 9),
        )
        self._val_lbl.pack(side="right")

        self._cv = tk.Canvas(
            self,
            width=120,
            height=22,
            bg=bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._cv.pack(fill="x", pady=(1, 0))

        self._track_bg = self._cv.create_line(8, 11, 100, 11, fill=BG_ELEVATED, width=5, capstyle="round")
        self._track_fg = self._cv.create_line(8, 11, 8, 11, fill=accent, width=5, capstyle="round")
        self._knob = self._cv.create_oval(2, 5, 14, 17, fill=FG_TEXT, outline=accent, width=2)

        self._cv.bind("<Configure>", lambda _e: self._redraw())
        self._cv.bind("<Button-1>", self._on_pointer)
        self._cv.bind("<B1-Motion>", self._on_pointer)

    @property
    def value(self) -> float:
        return self._val

    def set_value(self, value: float, *, notify: bool = False) -> None:
        self._val = max(self._min, min(self._max, float(value)))
        self._val_lbl.configure(text=self._fmt(self._val))
        self._redraw()
        if notify and not self._silent:
            self._on_change(self._val)

    def _on_pointer(self, event: tk.Event) -> None:
        w = max(24, self._cv.winfo_width())
        pad = 8.0
        usable = max(1.0, w - 2.0 * pad)
        ratio = max(0.0, min(1.0, (event.x - pad) / usable))
        new_val = self._min + ratio * (self._max - self._min)
        if abs(new_val - self._val) < 1e-6:
            return
        self._val = new_val
        self._val_lbl.configure(text=self._fmt(self._val))
        self._redraw()
        if not self._silent:
            self._on_change(self._val)

    def _redraw(self) -> None:
        w = max(24, self._cv.winfo_width())
        pad = 8.0
        usable = max(1.0, w - 2.0 * pad)
        span = max(1e-9, self._max - self._min)
        ratio = (self._val - self._min) / span
        kx = pad + ratio * usable
        self._cv.coords(self._track_bg, pad, 11, w - pad, 11)
        self._cv.coords(self._track_fg, pad, 11, max(pad + 0.5, kx), 11)
        self._cv.coords(self._knob, kx - 6, 5, kx + 6, 17)


class ToggleSwitch(tk.Frame):
    """Smooth animated pill toggle switch (zero timers when settled)."""

    def __init__(
        self,
        parent: tk.Widget,
        label: str,
        *,
        initial: bool,
        on_change: Callable[[bool], None],
        bg: str = BG_SURFACE,
    ) -> None:
        super().__init__(parent, bg=bg)
        self._state = bool(initial)
        self._pos = 1.0 if self._state else 0.0
        self._anim_job: str | None = None
        self._hovered = False
        self._on_change = on_change

        self._cv = tk.Canvas(
            self,
            width=36,
            height=20,
            bg=bg,
            highlightthickness=0,
            bd=0,
            relief="flat",
            cursor="hand2",
        )
        self._cv.pack(side="left", padx=(0, 8))
        self._track = self._cv.create_line(10, 10, 26, 10, fill=BG_ELEVATED, width=18, capstyle="round")
        self._knob = self._cv.create_oval(4, 4, 16, 16, fill="#ffffff", outline="")

        self._lbl = tk.Label(
            self,
            text=label,
            bg=bg,
            fg=FG_TEXT,
            font=("Segoe UI", 9),
            cursor="hand2",
        )
        self._lbl.pack(side="left")

        for w in (self._cv, self._lbl):
            w.bind("<Button-1>", self._toggle)
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
        self._render()

    @property
    def value(self) -> bool:
        return self._state

    def set_value(self, state: bool, *, notify: bool = False, animate: bool = False) -> None:
        self._state = bool(state)
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except tk.TclError:
                pass
            self._anim_job = None

        target = 1.0 if self._state else 0.0
        if animate and abs(target - self._pos) > 0.02:
            self._step_anim()
        else:
            self._pos = target
            self._render()
        if notify:
            self._on_change(self._state)

    def _toggle(self, _event=None) -> None:
        self.set_value(not self._state, notify=True, animate=True)

    def _on_enter(self, _event=None) -> None:
        self._hovered = True
        self._render()

    def _on_leave(self, _event=None) -> None:
        self._hovered = False
        self._render()

    def _step_anim(self) -> None:
        self._anim_job = None
        target = 1.0 if self._state else 0.0
        diff = target - self._pos
        if abs(diff) <= 0.04:
            self._pos = target
            self._render()
            return
        self._pos += diff * 0.42
        self._render()
        try:
            self._anim_job = self.after(16, self._step_anim)
        except tk.TclError:
            self._anim_job = None

    def _render(self) -> None:
        off_col = BG_HOVER if self._hovered else BG_ELEVATED
        on_col = ACCENT_HOVER if self._hovered else ACCENT
        track_col = _blend_hex(off_col, on_col, self._pos)
        kx = 10.0 + 16.0 * self._pos
        self._cv.itemconfig(self._track, fill=track_col)
        self._cv.coords(self._knob, kx - 6.0, 4.0, kx + 6.0, 16.0)


class SegmentedBar(tk.Frame):
    """Segmented pill button group for fast single-click selection."""

    def __init__(
        self,
        parent: tk.Widget,
        options: list[tuple[str, str]],
        *,
        initial: str,
        on_change: Callable[[str], None],
        bg: str = BG_SURFACE,
    ) -> None:
        super().__init__(
            parent,
            bg=BG_INPUT,
            padx=2,
            pady=2,
            bd=0,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self._choices = options
        self._selected = initial
        self._on_change = on_change
        self._buttons: dict[str, tk.Label] = {}

        for idx, (key, title) in enumerate(options):
            self.columnconfigure(idx, weight=1)
            btn = tk.Label(
                self,
                text=title,
                bg=ACCENT if key == initial else BG_INPUT,
                fg="#ffffff" if key == initial else FG_SUB,
                pady=4,
                padx=8,
                font=("Segoe UI Semibold", 8),
                cursor="hand2",
            )
            btn.grid(row=0, column=idx, sticky="ew", padx=1)
            btn.bind("<Button-1>", lambda _e, k=key: self.select(k, notify=True))
            btn.bind("<Enter>", lambda _e, k=key, b=btn: b.configure(bg=BG_ELEVATED) if k != self._selected else None)
            btn.bind("<Leave>", lambda _e, k=key, b=btn: b.configure(bg=BG_INPUT) if k != self._selected else None)
            self._buttons[key] = btn

    @property
    def value(self) -> str:
        return self._selected

    def select(self, key: str, *, notify: bool = False) -> None:
        if key not in self._buttons:
            return
        self._selected = key
        for k, btn in self._buttons.items():
            if k == key:
                btn.configure(bg=ACCENT, fg="#ffffff")
            else:
                btn.configure(bg=BG_INPUT, fg=FG_SUB)
        if notify:
            self._on_change(key)


class OpenDS4App:
    def __init__(self, root: tk.Tk, *, start_minimized: bool = False) -> None:
        self.root = root
        self.root.title("OpenDS4")
        self.root.geometry("1160x740")
        self.root.minsize(1020, 660)
        self.root.configure(bg=BG_WINDOW, bd=0, highlightthickness=0)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.store = ProfileStore()
        names = self.store.list_names()
        initial_name = "Default" if "Default" in names else (names[0] if names else "Default")
        self.profile: ControllerProfile = self.store.load(initial_name)

        self._loading_profile = False
        self._demo_active = False
        self._after_id: str | None = None
        self._nav_anim_id: str | None = None
        self._current_page = "monitor"
        self._cfg_cache: dict[str, tuple] = {}
        self._bar_smooth: dict[str, float] = {}
        self._ui_tick = 0

        self._in_tray = False
        self._hiding_to_tray = False
        self._tray_balloon_shown = False
        self._window_icon: tk.PhotoImage | None = None
        self._header_logo: tk.PhotoImage | None = None

        try:
            self._window_icon = create_tk_window_icon(
                self.root,
                DEFAULT_ICON_TEAL,
                size=32,
                bg_rgb=(21, 24, 32),
                transparent_bg=True,
            )
            self.root.iconphoto(True, self._window_icon)
            self._header_logo = create_tk_window_icon(
                self.root,
                DEFAULT_ICON_TEAL,
                size=26,
                bg_rgb=(21, 24, 32),
                transparent_bg=False,
            )
        except Exception:
            self._window_icon = None
            self._header_logo = None

        self.engine = DS4MapperEngine(self.profile, on_log=self._enqueue_log)
        self.tray = SystemTrayIcon(initial_snapshot=self._build_tray_snapshot())
        self.tray.start(DEFAULT_ICON_TEAL)

        self._configure_ttk()
        self._build_shell()
        self._load_profile_into_ui(self.profile)
        self._switch_page("monitor")
        _apply_windows_dark_frame(self.root)
        self.root.after(60, lambda: _apply_windows_dark_frame(self.root))

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Unmap>", self._on_root_unmap)

        self.engine.start(demo_mode=False)
        self.tray.set_snapshot(self._build_tray_snapshot())
        if start_minimized:
            self.root.after(40, lambda: self.hide_to_tray(silent=True))
        self._schedule_ui_refresh()

    def _set_cached(self, widget: tk.Widget, cache_key: str, **kwargs) -> None:
        """Apply widget.configure only when values actually changed to prevent GDI/Tcl stutter."""
        sig = tuple(kwargs.items())
        if self._cfg_cache.get(cache_key) == sig:
            return
        self._cfg_cache[cache_key] = sig
        widget.configure(**kwargs)

    def _configure_ttk(self) -> None:
        self.root.option_add("*TCombobox*Listbox*Background", BG_ELEVATED)
        self.root.option_add("*TCombobox*Listbox*Foreground", FG_TEXT)
        self.root.option_add("*TCombobox*Listbox*selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox*selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox*relief", "flat")
        self.root.option_add("*TCombobox*Listbox*borderWidth", 0)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TCombobox",
            fieldbackground=BG_ELEVATED,
            background=BG_ELEVATED,
            foreground=FG_TEXT,
            arrowcolor=FG_TEXT,
            bordercolor=BORDER,
            lightcolor=BG_ELEVATED,
            darkcolor=BG_ELEVATED,
            borderwidth=1,
            padding=3,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", BG_ELEVATED)],
            background=[("readonly", BG_ELEVATED)],
            foreground=[("readonly", FG_TEXT)],
            selectbackground=[("readonly", BG_ELEVATED)],
            selectforeground=[("readonly", FG_TEXT)],
            bordercolor=[("readonly", BORDER)],
        )

    def _card(self, parent: tk.Widget, title: str | None = None, *, padx: int = 16, pady: int = 14) -> tk.Frame:
        frame = tk.Frame(
            parent,
            bg=BG_SURFACE,
            bd=0,
            highlightbackground=BORDER,
            highlightthickness=1,
            padx=padx,
            pady=pady,
        )
        if title:
            tk.Label(
                frame,
                text=title,
                bg=BG_SURFACE,
                fg=FG_TEXT,
                font=("Segoe UI Semibold", 10),
            ).pack(anchor="w", pady=(0, 10))
        return frame

    def _flat_btn(
        self,
        parent: tk.Widget,
        text: str,
        command: Callable[[], None],
        *,
        bg: str = BG_ELEVATED,
        fg: str = FG_TEXT,
        padx: int = 12,
        pady: int = 5,
    ) -> tk.Button:
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=ACCENT_HOVER,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=padx,
            pady=pady,
            cursor="hand2",
            font=("Segoe UI Semibold", 9),
        )
        btn._base_bg = bg  # type: ignore[attr-defined]
        btn._base_fg = fg  # type: ignore[attr-defined]

        def _enter(_e=None) -> None:
            cur_bg = btn.cget("bg")
            if cur_bg == BG_ELEVATED:
                btn.configure(bg=BG_HOVER, fg=FG_TEXT)
            elif cur_bg == ACCENT:
                btn.configure(bg=ACCENT_HOVER)

        def _leave(_e=None) -> None:
            cur_bg = btn.cget("bg")
            if cur_bg == BG_HOVER:
                btn.configure(bg=BG_ELEVATED, fg=btn._base_fg if btn._base_bg == BG_ELEVATED else FG_TEXT)  # type: ignore[attr-defined]
            elif cur_bg == ACCENT_HOVER:
                btn.configure(bg=ACCENT)

        btn.bind("<Enter>", _enter)
        btn.bind("<Leave>", _leave)
        return btn

    def _build_shell(self) -> None:
        # Top status bar (borderless top edge, single 1px bottom hairline)
        top = tk.Frame(
            self.root,
            bg=BG_SIDEBAR,
            bd=0,
            highlightthickness=0,
            padx=18,
            pady=10,
        )
        top.pack(fill="x")
        tk.Frame(self.root, bg=BORDER, height=1, bd=0, highlightthickness=0).pack(fill="x")

        # Pack right controls first so action buttons are never clipped on narrow windows
        actions = tk.Frame(top, bg=BG_SIDEBAR)
        actions.pack(side="right", padx=(14, 0))

        self.btn_tray = self._flat_btn(
            actions,
            "Minimize",
            self._on_tray_button_clicked,
            bg=BG_ELEVATED,
            fg=FG_SUB,
            padx=10,
        )
        self.btn_tray.pack(side="left", padx=(0, 6))

        self.btn_sim = self._flat_btn(
            actions,
            "Simulator: Off",
            self._toggle_demo_mode,
            bg=BG_ELEVATED,
            fg=FG_SUB,
            padx=10,
        )
        self.btn_sim.pack(side="left", padx=(0, 6))

        self.btn_engine = self._flat_btn(
            actions,
            "Stop",
            self._toggle_engine,
            bg=RED,
            fg="#ffffff",
            padx=12,
        )
        self.btn_engine.pack(side="left")

        brand = tk.Frame(top, bg=BG_SIDEBAR)
        brand.pack(side="left")
        if self._header_logo is not None:
            tk.Label(
                brand,
                image=self._header_logo,
                bg=BG_SIDEBAR,
                bd=0,
                highlightthickness=0,
            ).pack(side="left", padx=(0, 8))

        tk.Label(
            brand,
            text="OpenDS4",
            bg=BG_SIDEBAR,
            fg=FG_TEXT,
            font=("Segoe UI Semibold", 13),
        ).pack(side="left", padx=(0, 12))

        self.lbl_conn = tk.Label(
            brand,
            text="Searching...",
            bg=BG_ELEVATED,
            fg=AMBER,
            padx=8,
            pady=3,
            font=("Segoe UI Semibold", 9),
        )
        self.lbl_conn.pack(side="left", padx=2)

        self.lbl_vigem = tk.Label(
            brand,
            text="XInput: Checking",
            bg=BG_ELEVATED,
            fg=FG_SUB,
            padx=8,
            pady=3,
            font=("Segoe UI", 9),
        )
        self.lbl_vigem.pack(side="left", padx=2)

        self.lbl_battery = tk.Label(
            brand,
            text="Battery --",
            bg=BG_ELEVATED,
            fg=FG_SUB,
            padx=8,
            pady=3,
            font=("Segoe UI", 9),
        )
        self.lbl_battery.pack(side="left", padx=2)

        self.lbl_rate = tk.Label(
            brand,
            text="0 Hz",
            bg=BG_ELEVATED,
            fg=CYAN,
            padx=8,
            pady=3,
            font=("Consolas", 9),
        )
        self.lbl_rate.pack(side="left", padx=2)

        self.header_led_dot = tk.Frame(
            brand,
            bg="#005aff",
            width=22,
            height=22,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.header_led_dot.pack(side="left", padx=(6, 0))

        # Main body: Sidebar + Page container
        body = tk.Frame(self.root, bg=BG_WINDOW, bd=0, highlightthickness=0)
        body.pack(fill="both", expand=True)

        sidebar = tk.Frame(
            body,
            bg=BG_SIDEBAR,
            width=195,
            bd=0,
            highlightthickness=0,
            pady=12,
        )
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Frame(body, bg=BORDER, width=1, bd=0, highlightthickness=0).pack(side="left", fill="y")

        nav_items = [
            ("monitor", "Live Monitor"),
            ("sticks", "Sticks & Touchpad"),
            ("lightbar", "Lightbar RGB"),
            ("vibration", "Vibration"),
            ("profiles", "Mapping & Profiles"),
        ]
        self._nav_buttons: dict[str, tuple[tk.Frame, tk.Frame, tk.Label]] = {}
        for key, title in nav_items:
            row = tk.Frame(sidebar, bg=BG_SIDEBAR, bd=0, highlightthickness=0, cursor="hand2")
            row.pack(fill="x", padx=10, pady=2)
            bar = tk.Frame(row, bg=BG_SIDEBAR, width=3, height=32, bd=0, highlightthickness=0)
            bar.pack(side="left", fill="y")
            lbl = tk.Label(
                row,
                text=title,
                bg=BG_SIDEBAR,
                fg=FG_SUB,
                anchor="w",
                padx=12,
                pady=7,
                font=("Segoe UI Semibold", 10),
                cursor="hand2",
            )
            lbl.pack(side="left", fill="x", expand=True)
            for w in (row, lbl):
                w.bind("<Button-1>", lambda _e, k=key: self._switch_page(k))
                w.bind("<Enter>", lambda _e, k=key: self._on_nav_hover(k, True))
                w.bind("<Leave>", lambda _e, k=key: self._on_nav_hover(k, False))
            self._nav_buttons[key] = (row, bar, lbl)

        # Quick active profile footer inside sidebar
        prof_footer = tk.Frame(sidebar, bg=BG_SIDEBAR, padx=12, pady=10)
        prof_footer.pack(side="bottom", fill="x")
        tk.Label(
            prof_footer,
            text="ACTIVE PROFILE",
            bg=BG_SIDEBAR,
            fg=FG_MUTED,
            font=("Segoe UI Semibold", 8),
        ).pack(anchor="w", pady=(0, 4))
        self.cmb_quick_profile = ttk.Combobox(
            prof_footer,
            state="readonly",
            values=self.store.list_names(),
        )
        self.cmb_quick_profile.pack(fill="x", pady=(0, 6))
        self.cmb_quick_profile.bind("<<ComboboxSelected>>", self._on_quick_profile_picked)

        self._flat_btn(
            prof_footer,
            "Save Changes",
            self._save_current_profile,
            bg=ACCENT,
            fg="#ffffff",
            pady=4,
        ).pack(fill="x")

        # Content stack
        self.page_host = tk.Frame(body, bg=BG_WINDOW, padx=18, pady=16, bd=0, highlightthickness=0)
        self.page_host.pack(side="left", fill="both", expand=True)

        self._pages: dict[str, tk.Frame] = {}
        for key, _ in nav_items:
            frame = tk.Frame(self.page_host, bg=BG_WINDOW, bd=0, highlightthickness=0)
            self._pages[key] = frame

        self._build_page_monitor(self._pages["monitor"])
        self._build_page_sticks(self._pages["sticks"])
        self._build_page_lightbar(self._pages["lightbar"])
        self._build_page_vibration(self._pages["vibration"])
        self._build_page_profiles(self._pages["profiles"])

    def _on_nav_hover(self, key: str, entering: bool) -> None:
        if key == self._current_page or key not in self._nav_buttons:
            return
        row, bar, lbl = self._nav_buttons[key]
        bg = "#1c202c" if entering else BG_SIDEBAR
        fg = FG_TEXT if entering else FG_SUB
        row.configure(bg=bg)
        bar.configure(bg=bg)
        lbl.configure(bg=bg, fg=fg)

    def _switch_page(self, key: str) -> None:
        if key not in self._pages:
            return
        prev_page = self._current_page
        self._current_page = key
        for k, frame in self._pages.items():
            if k == key:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

        if self._nav_anim_id is not None:
            try:
                self.root.after_cancel(self._nav_anim_id)
            except tk.TclError:
                pass
            self._nav_anim_id = None

        for k, (row, bar, lbl) in self._nav_buttons.items():
            if k != key:
                row.configure(bg=BG_SIDEBAR)
                bar.configure(bg=BG_SIDEBAR)
                lbl.configure(bg=BG_SIDEBAR, fg=FG_SUB)

        row, bar, lbl = self._nav_buttons[key]
        bar.configure(bg=ACCENT)
        if prev_page == key:
            row.configure(bg=BG_ELEVATED)
            lbl.configure(bg=BG_ELEVATED, fg=FG_TEXT)
            return

        # Lightweight 4-frame fade into active tab (stops immediately on step 4)
        def _fade_step(step: int) -> None:
            self._nav_anim_id = None
            if self._current_page != key:
                return
            t = step / 4.0
            bg = _blend_hex(BG_SIDEBAR, BG_ELEVATED, t)
            row.configure(bg=bg)
            lbl.configure(bg=bg, fg=FG_TEXT)
            if step < 4:
                try:
                    self._nav_anim_id = self.root.after(16, lambda: _fade_step(step + 1))
                except tk.TclError:
                    self._nav_anim_id = None

        _fade_step(1)

    # -------------------- Page 1: Live Monitor --------------------

    def _build_page_monitor(self, page: tk.Frame) -> None:
        page.columnconfigure(0, weight=13, uniform="mon")
        page.columnconfigure(1, weight=7, uniform="mon")
        page.rowconfigure(0, weight=1)

        left_card = self._card(page, "Analog Sticks & Triggers")
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        radars = tk.Frame(left_card, bg=BG_SURFACE)
        radars.pack(fill="x", pady=(4, 14))
        radars.columnconfigure(0, weight=1)
        radars.columnconfigure(1, weight=1)

        self.left_radar = self._create_stick_radar(radars, 0, "Left Stick")
        self.right_radar = self._create_stick_radar(radars, 1, "Right Stick")

        trigs = tk.Frame(left_card, bg=BG_SURFACE)
        trigs.pack(fill="x", pady=(4, 16))
        trigs.columnconfigure(0, weight=1)
        trigs.columnconfigure(1, weight=1)

        self.l2_bar = self._create_trigger_bar(trigs, 0, "L2 Trigger")
        self.r2_bar = self._create_trigger_bar(trigs, 1, "R2 Trigger")

        tk.Label(
            left_card,
            text="Controller Buttons",
            bg=BG_SURFACE,
            fg=FG_SUB,
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w", pady=(0, 6))

        btn_grid = tk.Frame(left_card, bg=BG_SURFACE)
        btn_grid.pack(fill="x")
        for col in range(8):
            btn_grid.columnconfigure(col, weight=1)

        specs = [
            ("cross", "Cross"),
            ("circle", "Circle"),
            ("square", "Square"),
            ("triangle", "Triangle"),
            ("l1", "L1"),
            ("r1", "R1"),
            ("l3", "L3"),
            ("r3", "R3"),
            ("dpad_up", "Up"),
            ("dpad_down", "Down"),
            ("dpad_left", "Left"),
            ("dpad_right", "Right"),
            ("share", "Share"),
            ("options", "Options"),
            ("ps", "PS"),
            ("touch_click", "Touch"),
        ]
        self.button_pills: dict[str, tk.Label] = {}
        for idx, (key, label) in enumerate(specs):
            r, c = divmod(idx, 8)
            pill = tk.Label(
                btn_grid,
                text=label,
                bg=BG_INPUT,
                fg=FG_MUTED,
                pady=6,
                font=("Segoe UI Semibold", 8),
                highlightbackground=BORDER,
                highlightthickness=1,
            )
            pill.grid(row=r, column=c, sticky="ew", padx=2, pady=2)
            self.button_pills[key] = pill

        # Right column: Touchpad + Motion & Motors
        right_col = tk.Frame(page, bg=BG_WINDOW)
        right_col.grid(row=0, column=1, sticky="nsew")
        right_col.rowconfigure(1, weight=1)
        right_col.columnconfigure(0, weight=1)

        tp_card = self._card(right_col)
        tp_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        tp_hdr = tk.Frame(tp_card, bg=BG_SURFACE)
        tp_hdr.pack(fill="x", pady=(0, 10))
        tk.Label(
            tp_hdr,
            text="Touchpad (1920 x 942)",
            bg=BG_SURFACE,
            fg=FG_TEXT,
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")
        self.btn_tp_quick = self._flat_btn(
            tp_hdr,
            "Mouse: Off",
            self._toggle_touchpad_mouse,
            bg=BG_ELEVATED,
            fg=FG_SUB,
            padx=8,
            pady=2,
        )
        self.btn_tp_quick.pack(side="right")

        self.tp_w, self.tp_h = 248, 122
        self.tp_canvas = tk.Canvas(
            tp_card,
            width=self.tp_w,
            height=self.tp_h,
            bg=BG_INPUT,
            bd=0,
            relief="flat",
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.tp_canvas.pack()
        self.tp_canvas.create_line(self.tp_w // 2, 0, self.tp_w // 2, self.tp_h, fill="#1c2130")
        self.tp_canvas.create_line(0, self.tp_h // 2, self.tp_w, self.tp_h // 2, fill="#1c2130")
        self.tp_dot1 = self.tp_canvas.create_oval(-12, -12, -2, -2, fill=CYAN, outline="#ffffff", width=1)
        self.tp_dot2 = self.tp_canvas.create_oval(-12, -12, -2, -2, fill=AMBER, outline="#ffffff", width=1)
        self.tp_text = self.tp_canvas.create_text(
            10,
            self.tp_h - 10,
            anchor="w",
            text="No touch",
            fill=FG_MUTED,
            font=("Consolas", 8),
        )

        imu_card = self._card(right_col, "Sensors & Telemetry")
        imu_card.grid(row=1, column=0, sticky="nsew")

        self.lbl_telemetry = tk.Label(
            imu_card,
            text="",
            bg=BG_INPUT,
            fg=FG_SUB,
            justify="left",
            anchor="nw",
            padx=12,
            pady=12,
            font=("Consolas", 9),
        )
        self.lbl_telemetry.pack(fill="both", expand=True)

    def _create_stick_radar(self, parent: tk.Frame, col: int, title: str) -> dict:
        box = tk.Frame(parent, bg=BG_SURFACE)
        box.grid(row=0, column=col, sticky="nsew", padx=6)

        tk.Label(box, text=title, bg=BG_SURFACE, fg=FG_TEXT, font=("Segoe UI Semibold", 9)).pack()
        size = 190
        c = size // 2
        radius = 80

        cv = tk.Canvas(
            box,
            width=size,
            height=size,
            bg=BG_INPUT,
            bd=0,
            relief="flat",
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        cv.pack(pady=6)

        cv.create_oval(c - radius, c - radius, c + radius, c + radius, outline="#2b3344", width=1.5)
        cv.create_line(c - radius, c, c + radius, c, fill="#1d2230")
        cv.create_line(c, c - radius, c, c + radius, fill="#1d2230")

        dz_oval = cv.create_oval(c - 6, c - 6, c + 6, c + 6, outline=AMBER, dash=(3, 2), width=1.2)
        raw_dot = cv.create_oval(c - 3, c - 3, c + 3, c + 3, fill=FG_MUTED, outline="")
        out_line = cv.create_line(c, c, c, c, fill=ACCENT, width=2)
        out_dot = cv.create_oval(c - 5, c - 5, c + 5, c + 5, fill=CYAN, outline="#ffffff")

        lbl = tk.Label(box, text="0, 0", bg=BG_SURFACE, fg=FG_MUTED, font=("Consolas", 8))
        lbl.pack()

        return {
            "canvas": cv,
            "center": c,
            "radius": radius,
            "dz_oval": dz_oval,
            "raw_dot": raw_dot,
            "out_line": out_line,
            "out_dot": out_dot,
            "label": lbl,
        }

    def _create_trigger_bar(self, parent: tk.Frame, col: int, title: str) -> dict:
        box = tk.Frame(parent, bg=BG_SURFACE)
        box.grid(row=0, column=col, sticky="ew", padx=6)

        top = tk.Frame(box, bg=BG_SURFACE)
        top.pack(fill="x")
        tk.Label(top, text=title, bg=BG_SURFACE, fg=FG_SUB, font=("Segoe UI Semibold", 9)).pack(side="left")
        val_lbl = tk.Label(top, text="0", bg=BG_SURFACE, fg=FG_TEXT, font=("Consolas", 8))
        val_lbl.pack(side="right")

        cv = tk.Canvas(
            box,
            height=TRIGGER_BAR_HEIGHT,
            bg=BG_INPUT,
            bd=0,
            relief="flat",
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        cv.pack(fill="x", pady=(4, 0))
        rect = cv.create_rectangle(0, 0, 0, TRIGGER_BAR_HEIGHT, fill=ACCENT, outline="")
        return {"canvas": cv, "rect": rect, "label": val_lbl, "height": TRIGGER_BAR_HEIGHT}

    # -------------------- Page 2: Sticks & Triggers --------------------

    def _build_page_sticks(self, page: tk.Frame) -> None:
        page.columnconfigure(0, weight=1, uniform="stk")
        page.columnconfigure(1, weight=1, uniform="stk")

        l_card = self._card(page, "Left Stick")
        l_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 10))

        self.sl_l_dz = ModernSlider(
            l_card,
            "Deadzone",
            min_val=0,
            max_val=40,
            initial=8,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("left_deadzone", v / 100.0),
        )
        self.sl_l_dz.pack(fill="x", pady=4)

        self.sl_l_sens = ModernSlider(
            l_card,
            "Sensitivity",
            min_val=50,
            max_val=160,
            initial=100,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("left_sensitivity", v / 100.0),
        )
        self.sl_l_sens.pack(fill="x", pady=4)

        tk.Label(l_card, text="Response Curve", bg=BG_SURFACE, fg=FG_SUB, font=("Segoe UI", 9)).pack(
            anchor="w", pady=(8, 4)
        )
        self.seg_l_curve = SegmentedBar(
            l_card,
            list(CURVE_MODES.items()),
            initial="linear",
            on_change=lambda k: self._update_field("left_curve", k),
        )
        self.seg_l_curve.pack(fill="x", pady=(0, 10))

        self.sw_inv_ly = ToggleSwitch(
            l_card,
            "Invert vertical axis (Y)",
            initial=False,
            on_change=lambda b: self._update_field("invert_left_y", b),
        )
        self.sw_inv_ly.pack(anchor="w", pady=(4, 0))

        # Right stick card
        r_card = self._card(page, "Right Stick (Aim)")
        r_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 10))

        self.sl_r_dz = ModernSlider(
            r_card,
            "Deadzone",
            min_val=0,
            max_val=40,
            initial=8,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("right_deadzone", v / 100.0, redraw_curve=True),
        )
        self.sl_r_dz.pack(fill="x", pady=4)

        self.sl_r_sens = ModernSlider(
            r_card,
            "Sensitivity",
            min_val=50,
            max_val=160,
            initial=100,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("right_sensitivity", v / 100.0, redraw_curve=True),
        )
        self.sl_r_sens.pack(fill="x", pady=4)

        tk.Label(r_card, text="Response Curve", bg=BG_SURFACE, fg=FG_SUB, font=("Segoe UI", 9)).pack(
            anchor="w", pady=(8, 4)
        )
        self.seg_r_curve = SegmentedBar(
            r_card,
            list(CURVE_MODES.items()),
            initial="linear",
            on_change=lambda k: self._update_field("right_curve", k, redraw_curve=True),
        )
        self.seg_r_curve.pack(fill="x", pady=(0, 10))

        self.sw_inv_ry = ToggleSwitch(
            r_card,
            "Invert vertical axis (Y)",
            initial=False,
            on_change=lambda b: self._update_field("invert_right_y", b),
        )
        self.sw_inv_ry.pack(anchor="w", pady=(4, 0))

        # Bottom left: Triggers + Aim Curve
        trig_card = self._card(page, "Triggers & Stick Options")
        trig_card.grid(row=1, column=0, sticky="nsew", padx=(0, 8))

        split = tk.Frame(trig_card, bg=BG_SURFACE)
        split.pack(fill="both", expand=True)
        split.columnconfigure(0, weight=1)

        left_half = tk.Frame(split, bg=BG_SURFACE)
        left_half.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        self.sl_l2_dz = ModernSlider(
            left_half,
            "L2 Deadzone",
            min_val=0,
            max_val=35,
            initial=2,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("l2_deadzone", v / 100.0),
        )
        self.sl_l2_dz.pack(fill="x", pady=3)

        self.sl_r2_dz = ModernSlider(
            left_half,
            "R2 Deadzone",
            min_val=0,
            max_val=35,
            initial=2,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("r2_deadzone", v / 100.0),
        )
        self.sl_r2_dz.pack(fill="x", pady=3)

        self.sl_anti_dz = ModernSlider(
            left_half,
            "Anti-Deadzone",
            min_val=0,
            max_val=25,
            initial=0,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("anti_deadzone", v / 100.0, redraw_curve=True),
        )
        self.sl_anti_dz.pack(fill="x", pady=3)

        self.sw_swap = ToggleSwitch(
            left_half,
            "Swap Left and Right sticks",
            initial=False,
            on_change=lambda b: self._update_field("swap_sticks", b),
        )
        self.sw_swap.pack(anchor="w", pady=(8, 0))

        # Persistent curve graph (updates coordinates only, never deletes/rebuilds)
        curve_box = tk.Frame(split, bg=BG_SURFACE)
        curve_box.grid(row=0, column=1, sticky="ns")
        tk.Label(curve_box, text="Aim Curve", bg=BG_SURFACE, fg=FG_SUB, font=("Segoe UI", 8)).pack(
            pady=(0, 4)
        )
        w, h = 112, 112
        self.curve_canvas = tk.Canvas(
            curve_box,
            width=w,
            height=h,
            bg=BG_INPUT,
            bd=0,
            relief="flat",
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.curve_canvas.pack()
        pad = 12
        self.curve_canvas.create_line(pad, h - pad, w - pad, h - pad, fill="#2c3548")
        self.curve_canvas.create_line(pad, h - pad, pad, pad, fill="#2c3548")
        self.curve_canvas.create_line(pad, h - pad, w - pad, pad, fill="#1f2636", dash=(2, 2))
        self._curve_line = self.curve_canvas.create_line(pad, h - pad, w - pad, pad, fill=CYAN, width=2)

        # Bottom right: Optional Touchpad Mouse settings
        tp_opt_card = self._card(page, "Touchpad (Optional)")
        tp_opt_card.grid(row=1, column=1, sticky="nsew", padx=(8, 0))

        self.sw_tp_mouse = ToggleSwitch(
            tp_opt_card,
            "Use touchpad as Windows mouse",
            initial=False,
            on_change=self._on_touchpad_mouse_toggled,
        )
        self.sw_tp_mouse.pack(anchor="w", pady=(0, 8))

        self.sw_tp_tap = ToggleSwitch(
            tp_opt_card,
            "Tap to click (1 finger = Left, 2 = Right)",
            initial=True,
            on_change=lambda b: self._update_field("touchpad_tap_click", b),
        )
        self.sw_tp_tap.pack(anchor="w", pady=(0, 10))

        self.sl_tp_sens = ModernSlider(
            tp_opt_card,
            "Cursor Speed",
            min_val=30,
            max_val=250,
            initial=100,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("touchpad_sensitivity", v / 100.0),
        )
        self.sl_tp_sens.pack(fill="x", pady=(0, 10))

        tk.Label(
            tp_opt_card,
            text=(
                "1 finger moves cursor, 2 fingers scroll, right edge right-clicks.\n"
                "When disabled, touchpad click maps normally to Xbox buttons."
            ),
            bg=BG_SURFACE,
            fg=FG_MUTED,
            justify="left",
            font=("Segoe UI", 8),
        ).pack(anchor="w")

    # -------------------- Page 3: Lightbar RGB --------------------

    def _build_page_lightbar(self, page: tk.Frame) -> None:
        top_card = self._card(page, "Lightbar Mode & Preview")
        top_card.pack(fill="x", pady=(0, 10))

        bar_row = tk.Frame(top_card, bg=BG_SURFACE)
        bar_row.pack(fill="x", pady=(0, 12))

        self.led_preview = tk.Frame(
            bar_row,
            bg="#005aff",
            height=34,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.led_preview.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.led_preview.pack_propagate(False)

        self.lbl_led_info = tk.Label(
            self.led_preview,
            text="#005AFF  ·  Solid Color",
            bg="#005aff",
            fg="#ffffff",
            font=("Consolas", 9, "bold"),
        )
        self.lbl_led_info.pack(expand=True)

        self._flat_btn(
            bar_row,
            "Color Picker...",
            self._pick_color_dialog,
            bg=BG_ELEVATED,
        ).pack(side="right")

        # 4x2 clickable mode grid
        mode_grid = tk.Frame(top_card, bg=BG_SURFACE)
        mode_grid.pack(fill="x")
        for c in range(4):
            mode_grid.columnconfigure(c, weight=1)

        self._mode_cards: dict[str, tk.Label] = {}
        for idx, (mode_key, mode_title) in enumerate(LIGHTBAR_MODES.items()):
            r, c = divmod(idx, 4)
            btn = tk.Label(
                mode_grid,
                text=mode_title,
                bg=BG_INPUT,
                fg=FG_SUB,
                pady=8,
                padx=8,
                font=("Segoe UI Semibold", 9),
                bd=0,
                highlightbackground=BORDER,
                highlightthickness=1,
                cursor="hand2",
            )
            btn.grid(row=r, column=c, sticky="ew", padx=3, pady=3)
            btn.bind("<Button-1>", lambda _e, mk=mode_key: self._select_lightbar_mode(mk))
            btn.bind(
                "<Enter>",
                lambda _e, mk=mode_key, b=btn: b.configure(bg=BG_ELEVATED, fg=FG_TEXT)
                if mk != self.profile.lightbar_mode
                else None,
            )
            btn.bind(
                "<Leave>",
                lambda _e, mk=mode_key, b=btn: b.configure(bg=BG_INPUT, fg=FG_SUB)
                if mk != self.profile.lightbar_mode
                else None,
            )
            self._mode_cards[mode_key] = btn

        # Bottom controls: Color & Animation speed
        ctrl_card = self._card(page, "Color & Brightness")
        ctrl_card.pack(fill="both", expand=True)

        swatches = tk.Frame(ctrl_card, bg=BG_SURFACE)
        swatches.pack(fill="x", pady=(0, 10))
        tk.Label(swatches, text="Presets:", bg=BG_SURFACE, fg=FG_SUB, font=("Segoe UI", 9)).pack(
            side="left", padx=(0, 8)
        )
        for name, r, g, b in STATIC_SWATCHES:
            hex_c = f"#{r:02x}{g:02x}{b:02x}"
            fg_c = "#111318" if (r * 0.299 + g * 0.587 + b * 0.114) > 150 else "#ffffff"
            b_widget = tk.Button(
                swatches,
                text=name,
                bg=hex_c if (r or g or b) else BG_ELEVATED,
                fg=fg_c,
                relief="flat",
                bd=0,
                padx=10,
                pady=3,
                font=("Segoe UI Semibold", 8),
                cursor="hand2",
                command=lambda rr=r, gg=g, bb=b: self._apply_led_swatch(rr, gg, bb),
            )
            b_widget.pack(side="left", padx=3)

        cols = tk.Frame(ctrl_card, bg=BG_SURFACE)
        cols.pack(fill="x")
        cols.columnconfigure(0, weight=1)
        cols.columnconfigure(1, weight=1)

        left_s = tk.Frame(cols, bg=BG_SURFACE)
        left_s.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right_s = tk.Frame(cols, bg=BG_SURFACE)
        right_s.grid(row=0, column=1, sticky="nsew", padx=(12, 0))

        self.sl_led_r = ModernSlider(
            left_s,
            "Red",
            min_val=0,
            max_val=255,
            initial=0,
            fmt=lambda v: f"{int(round(v))}",
            on_change=lambda v: self._update_field("lightbar_r", int(round(v)), sync_led=True),
            accent="#ef4444",
        )
        self.sl_led_r.pack(fill="x", pady=3)

        self.sl_led_g = ModernSlider(
            left_s,
            "Green",
            min_val=0,
            max_val=255,
            initial=90,
            fmt=lambda v: f"{int(round(v))}",
            on_change=lambda v: self._update_field("lightbar_g", int(round(v)), sync_led=True),
            accent="#22c55e",
        )
        self.sl_led_g.pack(fill="x", pady=3)

        self.sl_led_b = ModernSlider(
            left_s,
            "Blue",
            min_val=0,
            max_val=255,
            initial=255,
            fmt=lambda v: f"{int(round(v))}",
            on_change=lambda v: self._update_field("lightbar_b", int(round(v)), sync_led=True),
            accent="#3b82f6",
        )
        self.sl_led_b.pack(fill="x", pady=3)

        self.sl_led_bright = ModernSlider(
            right_s,
            "Brightness",
            min_val=0,
            max_val=100,
            initial=80,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("lightbar_brightness", v / 100.0, sync_led=True),
        )
        self.sl_led_bright.pack(fill="x", pady=3)

        self.sl_led_speed = ModernSlider(
            right_s,
            "Effect Speed",
            min_val=20,
            max_val=300,
            initial=100,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("lightbar_speed", v / 100.0, sync_led=True),
        )
        self.sl_led_speed.pack(fill="x", pady=3)

        self.sw_low_batt = ToggleSwitch(
            right_s,
            "Blink red when wireless battery drops to 20%",
            initial=True,
            on_change=lambda b: self._update_field("low_battery_warning", b, sync_led=True),
        )
        self.sw_low_batt.pack(anchor="w", pady=(10, 0))

    # -------------------- Page 4: Vibration --------------------

    def _build_page_vibration(self, page: tk.Frame) -> None:
        page.columnconfigure(0, weight=1, uniform="vib")
        page.columnconfigure(1, weight=1, uniform="vib")

        motors_card = self._card(page, "Game Vibration (XInput)")
        motors_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 10))

        self.sw_rumble = ToggleSwitch(
            motors_card,
            "Enable controller vibration",
            initial=True,
            on_change=lambda b: self._update_field("rumble_enabled", b, sync_led=True),
        )
        self.sw_rumble.pack(anchor="w", pady=(0, 10))

        tk.Label(motors_card, text="Quick Feel", bg=BG_SURFACE, fg=FG_SUB, font=("Segoe UI", 9)).pack(
            anchor="w", pady=(0, 4)
        )
        self.seg_rumble_preset = SegmentedBar(
            motors_card,
            [
                ("soft", "Soft"),
                ("balanced", "Balanced"),
                ("heavy", "Heavy Bass"),
                ("max", "Strong"),
            ],
            initial="balanced",
            on_change=self._apply_rumble_preset,
        )
        self.seg_rumble_preset.pack(fill="x", pady=(0, 10))

        self.sl_rumble_master = ModernSlider(
            motors_card,
            "Master Strength",
            min_val=0,
            max_val=150,
            initial=100,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("rumble_strength", v / 100.0, sync_led=True),
        )
        self.sl_rumble_master.pack(fill="x", pady=3)

        self.sl_rumble_heavy = ModernSlider(
            motors_card,
            "Left Motor (Heavy / Low-Freq)",
            min_val=0,
            max_val=150,
            initial=100,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("rumble_heavy_scale", v / 100.0, sync_led=True),
        )
        self.sl_rumble_heavy.pack(fill="x", pady=3)

        self.sl_rumble_light = ModernSlider(
            motors_card,
            "Right Motor (Light / High-Freq)",
            min_val=0,
            max_val=150,
            initial=100,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("rumble_light_scale", v / 100.0, sync_led=True),
        )
        self.sl_rumble_light.pack(fill="x", pady=3)

        # Right card: Trigger Haptics & Test Bench
        haptics_card = self._card(page, "Trigger Haptics & Motor Test")
        haptics_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 10))

        self.sw_trig_rumble = ToggleSwitch(
            haptics_card,
            "L2 / R2 Analog Trigger Haptics",
            initial=False,
            on_change=lambda b: self._update_field("trigger_rumble", b, sync_led=True),
        )
        self.sw_trig_rumble.pack(anchor="w", pady=(0, 4))

        tk.Label(
            haptics_card,
            text="Spins motors proportionally when pulling L2 or R2 in any game.",
            bg=BG_SURFACE,
            fg=FG_MUTED,
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(0, 8))

        self.sl_trig_rumble = ModernSlider(
            haptics_card,
            "Trigger Feedback Intensity",
            min_val=0,
            max_val=100,
            initial=45,
            fmt=lambda v: f"{v:.0f}%",
            on_change=lambda v: self._update_field("trigger_rumble_scale", v / 100.0, sync_led=True),
        )
        self.sl_trig_rumble.pack(fill="x", pady=(0, 16))

        tk.Label(
            haptics_card,
            text="Test Motors",
            bg=BG_SURFACE,
            fg=FG_TEXT,
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w", pady=(0, 6))

        test_row = tk.Frame(haptics_card, bg=BG_SURFACE)
        test_row.pack(fill="x", pady=(0, 14))

        self._flat_btn(
            test_row,
            "Both",
            lambda: self.engine.trigger_test_rumble(0.65, "both"),
            bg=ACCENT,
            fg="#ffffff",
            padx=10,
        ).pack(side="left", padx=(0, 5))
        self._flat_btn(
            test_row,
            "Heavy",
            lambda: self.engine.trigger_test_rumble(0.65, "heavy"),
            padx=10,
        ).pack(side="left", padx=(0, 5))
        self._flat_btn(
            test_row,
            "Light",
            lambda: self.engine.trigger_test_rumble(0.65, "light"),
            padx=10,
        ).pack(side="left", padx=(0, 5))
        self._flat_btn(
            test_row,
            "Pulse",
            lambda: self.engine.trigger_test_rumble(1.1, "pulse"),
            padx=10,
        ).pack(side="left", padx=(0, 5))
        self._flat_btn(
            test_row,
            "Stop",
            self.engine.stop_rumble,
            bg=BG_INPUT,
            fg=FG_SUB,
            padx=10,
        ).pack(side="left")

        # Live motor output meters
        meters = tk.Frame(haptics_card, bg=BG_SURFACE)
        meters.pack(fill="x", pady=(6, 0))
        meters.columnconfigure(0, weight=1)
        meters.columnconfigure(1, weight=1)
        self.heavy_meter = self._create_trigger_bar(meters, 0, "Left Heavy Output")
        self.light_meter = self._create_trigger_bar(meters, 1, "Right Light Output")

    # -------------------- Page 5: Mapping & Profiles --------------------

    def _build_page_profiles(self, page: tk.Frame) -> None:
        page.columnconfigure(0, weight=11)
        page.columnconfigure(1, weight=9)
        page.rowconfigure(0, weight=1)

        # Left: Button remapping
        map_card = self._card(page, "Button Mapping (DualShock 4 -> Xbox 360)")
        map_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        hdr = tk.Frame(map_card, bg=BG_SURFACE)
        hdr.pack(fill="x", pady=(0, 10))
        tk.Label(
            hdr,
            text="Remap any input to an XInput button.",
            bg=BG_SURFACE,
            fg=FG_MUTED,
            font=("Segoe UI", 8),
        ).pack(side="left")
        self._flat_btn(hdr, "Reset Defaults", self._reset_button_mapping, padx=10, pady=3).pack(side="right")

        grid = tk.Frame(map_card, bg=BG_SURFACE)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=1, uniform="map")
        grid.columnconfigure(1, weight=1, uniform="map")

        target_labels = list(XBOX_TARGETS.values())
        self._rev_targets = {label: code for code, label in XBOX_TARGETS.items()}
        self.btn_combos: dict[str, ttk.Combobox] = {}

        keys = list(DS4_BUTTON_LABELS.keys())
        half = (len(keys) + 1) // 2
        for idx, ds4_key in enumerate(keys):
            col = 0 if idx < half else 1
            row = idx if idx < half else idx - half
            cell = tk.Frame(grid, bg=BG_SURFACE, pady=4)
            cell.grid(row=row, column=col, sticky="ew", padx=(0, 14 if col == 0 else 0))

            tk.Label(
                cell,
                text=DS4_BUTTON_LABELS[ds4_key],
                bg=BG_SURFACE,
                fg=FG_TEXT,
                anchor="w",
                font=("Segoe UI", 9),
            ).pack(side="left")

            cmb = ttk.Combobox(cell, state="readonly", values=target_labels, width=13)
            cmb.pack(side="right")
            cmb.bind("<<ComboboxSelected>>", lambda _e, k=ds4_key: self._on_button_map_changed(k))
            self.btn_combos[ds4_key] = cmb

        # Right: Profiles, Bluetooth rate, HidHide helper, Log
        right_col = tk.Frame(page, bg=BG_WINDOW)
        right_col.grid(row=0, column=1, sticky="nsew")
        right_col.rowconfigure(2, weight=1)
        right_col.columnconfigure(0, weight=1)

        prof_card = self._card(right_col, "Profiles & Wireless")
        prof_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        name_row = tk.Frame(prof_card, bg=BG_SURFACE)
        name_row.pack(fill="x", pady=(0, 8))
        self.ent_profile_name = tk.Entry(
            name_row,
            bg=BG_INPUT,
            fg=FG_TEXT,
            insertbackground=FG_TEXT,
            relief="flat",
            highlightbackground=BORDER,
            highlightthickness=1,
            font=("Segoe UI", 9),
        )
        self.ent_profile_name.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
        self._flat_btn(name_row, "Save", self._save_current_profile, bg=ACCENT, fg="#ffffff").pack(
            side="left", padx=(0, 4)
        )
        self._flat_btn(name_row, "Delete", self._delete_selected_profile, fg=RED).pack(side="left")

        tk.Label(prof_card, text="Bluetooth Poll Interval", bg=BG_SURFACE, fg=FG_SUB, font=("Segoe UI", 9)).pack(
            anchor="w", pady=(4, 4)
        )
        self.seg_bt_poll = SegmentedBar(
            prof_card,
            [("1", "1 ms (1000 Hz)"), ("4", "4 ms (Standard)"), ("8", "8 ms (Battery)")],
            initial="4",
            on_change=lambda k: self._update_field("bt_poll_ms", int(k), sync_led=True),
        )
        self.seg_bt_poll.pack(fill="x")

        self.sw_min_tray = ToggleSwitch(
            prof_card,
            "Minimize to system tray when minimized",
            initial=True,
            on_change=lambda b: self._update_field("minimize_to_tray", b),
        )
        self.sw_min_tray.pack(anchor="w", pady=(10, 0))

        hh_card = self._card(right_col, "Double-Input Prevention (HidHide)")
        hh_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        hh_row = tk.Frame(hh_card, bg=BG_SURFACE)
        hh_row.pack(fill="x")
        self.lbl_hidhide = tk.Label(
            hh_row,
            text="HidHide: Optional",
            bg=BG_SURFACE,
            fg=FG_SUB,
            font=("Segoe UI Semibold", 9),
        )
        self.lbl_hidhide.pack(side="left")
        self._flat_btn(
            hh_row,
            "Copy Python Path",
            self._copy_python_path_for_hidhide,
            padx=10,
            pady=3,
        ).pack(side="right")

        tk.Label(
            hh_card,
            text="Whitelist Python in HidHide -> Applications, then hide Sony Wireless Controller.",
            bg=BG_SURFACE,
            fg=FG_MUTED,
            justify="left",
            wraplength=340,
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(6, 0))

        log_card = self._card(right_col, "Session Log")
        log_card.grid(row=2, column=0, sticky="nsew")

        self.txt_log = tk.Text(
            log_card,
            bg=BG_INPUT,
            fg=FG_SUB,
            height=8,
            relief="flat",
            highlightbackground=BORDER,
            highlightthickness=1,
            font=("Consolas", 8),
            state="disabled",
            padx=8,
            pady=6,
        )
        self.txt_log.pack(fill="both", expand=True)

    # -------------------- State & Profile Sync --------------------

    def _update_field(
        self,
        attr: str,
        value: object,
        *,
        sync_led: bool = False,
        redraw_curve: bool = False,
    ) -> None:
        if self._loading_profile:
            return
        setattr(self.profile, attr, value)
        self.engine.update_profile(self.profile, sync_lightbar=sync_led)
        if redraw_curve:
            self._redraw_curve_graph()

    def _select_lightbar_mode(self, mode_key: str) -> None:
        if mode_key not in LIGHTBAR_MODES:
            return
        self.profile.lightbar_mode = mode_key
        for k, card in self._mode_cards.items():
            if k == mode_key:
                card.configure(bg=ACCENT, fg="#ffffff")
            else:
                card.configure(bg=BG_INPUT, fg=FG_SUB)
        if not self._loading_profile:
            if self.profile.lightbar_brightness < 0.10:
                self.profile.lightbar_brightness = 0.80
                self.sl_led_bright.set_value(80.0, notify=False)
            self.engine.update_profile(self.profile, sync_lightbar=True)

    def _apply_led_swatch(self, r: int, g: int, b: int) -> None:
        self.profile.lightbar_r = r
        self.profile.lightbar_g = g
        self.profile.lightbar_b = b
        self.sl_led_r.set_value(r, notify=False)
        self.sl_led_g.set_value(g, notify=False)
        self.sl_led_b.set_value(b, notify=False)
        if self.profile.lightbar_mode not in ("static", "breathing", "color_pulse"):
            self._select_lightbar_mode("static")
        else:
            self.engine.update_profile(self.profile, sync_lightbar=True)

    def _pick_color_dialog(self) -> None:
        initial = f"#{self.profile.lightbar_r:02x}{self.profile.lightbar_g:02x}{self.profile.lightbar_b:02x}"
        rgb, _ = colorchooser.askcolor(color=initial, title="Select Lightbar Color")
        if rgb:
            self._apply_led_swatch(int(rgb[0]), int(rgb[1]), int(rgb[2]))

    def _apply_rumble_preset(self, key: str) -> None:
        presets = {
            "soft": (0.65, 0.85, 0.75),
            "balanced": (1.00, 1.00, 1.00),
            "heavy": (1.10, 1.40, 0.75),
            "max": (1.35, 1.35, 1.35),
        }
        if key not in presets:
            return
        master, heavy, light = presets[key]
        self.profile.rumble_enabled = True
        self.profile.rumble_strength = master
        self.profile.rumble_heavy_scale = heavy
        self.profile.rumble_light_scale = light
        self.sw_rumble.set_value(True, notify=False)
        self.sl_rumble_master.set_value(master * 100.0, notify=False)
        self.sl_rumble_heavy.set_value(heavy * 100.0, notify=False)
        self.sl_rumble_light.set_value(light * 100.0, notify=False)
        self.engine.update_profile(self.profile, sync_lightbar=True)

    def _on_button_map_changed(self, ds4_key: str) -> None:
        if self._loading_profile:
            return
        label = self.btn_combos[ds4_key].get()
        self.profile.button_map[ds4_key] = self._rev_targets.get(label, DEFAULT_BUTTON_MAP[ds4_key])
        self.engine.update_profile(self.profile, sync_lightbar=False)

    def _reset_button_mapping(self) -> None:
        self.profile.button_map = dict(DEFAULT_BUTTON_MAP)
        self._load_profile_into_ui(self.profile)
        self.engine.update_profile(self.profile, sync_lightbar=False)
        self._enqueue_log("[Mapping] Reset to standard Xbox 360 layout.")

    def _load_profile_into_ui(self, p: ControllerProfile) -> None:
        self._loading_profile = True
        try:
            self.sl_l_dz.set_value(p.left_deadzone * 100.0)
            self.sl_l_sens.set_value(p.left_sensitivity * 100.0)
            self.seg_l_curve.select(p.left_curve)
            self.sw_inv_ly.set_value(p.invert_left_y)

            self.sl_r_dz.set_value(p.right_deadzone * 100.0)
            self.sl_r_sens.set_value(p.right_sensitivity * 100.0)
            self.seg_r_curve.select(p.right_curve)
            self.sw_inv_ry.set_value(p.invert_right_y)

            self.sl_l2_dz.set_value(p.l2_deadzone * 100.0)
            self.sl_r2_dz.set_value(p.r2_deadzone * 100.0)
            self.sl_anti_dz.set_value(p.anti_deadzone * 100.0)
            self.sw_swap.set_value(p.swap_sticks)

            self.sw_tp_mouse.set_value(p.touchpad_mouse)
            self.sw_tp_tap.set_value(p.touchpad_tap_click)
            self.sl_tp_sens.set_value(p.touchpad_sensitivity * 100.0)
            self._sync_touchpad_badge()

            self._select_lightbar_mode(p.lightbar_mode)
            self.sl_led_r.set_value(p.lightbar_r)
            self.sl_led_g.set_value(p.lightbar_g)
            self.sl_led_b.set_value(p.lightbar_b)
            self.sl_led_bright.set_value(p.lightbar_brightness * 100.0)
            self.sl_led_speed.set_value(p.lightbar_speed * 100.0)
            self.sw_low_batt.set_value(p.low_battery_warning)

            self.sw_rumble.set_value(p.rumble_enabled)
            self.sl_rumble_master.set_value(p.rumble_strength * 100.0)
            self.sl_rumble_heavy.set_value(p.rumble_heavy_scale * 100.0)
            self.sl_rumble_light.set_value(p.rumble_light_scale * 100.0)
            self.sw_trig_rumble.set_value(p.trigger_rumble)
            self.sl_trig_rumble.set_value(p.trigger_rumble_scale * 100.0)

            self.seg_bt_poll.select(str(p.bt_poll_ms) if str(p.bt_poll_ms) in ("1", "4", "8") else "4")
            self.sw_min_tray.set_value(p.minimize_to_tray)

            for ds4_key, cmb in self.btn_combos.items():
                code = p.button_map.get(ds4_key, DEFAULT_BUTTON_MAP[ds4_key])
                cmb.set(XBOX_TARGETS.get(code, XBOX_TARGETS["A"]))

            self.ent_profile_name.delete(0, "end")
            self.ent_profile_name.insert(0, p.name)
            names = self.store.list_names()
            self.cmb_quick_profile.configure(values=names)
            if p.name in names:
                self.cmb_quick_profile.set(p.name)

            self._redraw_curve_graph()
        finally:
            self._loading_profile = False

    def _sync_touchpad_badge(self) -> None:
        if self.profile.touchpad_mouse:
            self.btn_tp_quick.configure(
                text="Mouse: On",
                bg=ACCENT,
                fg="#ffffff",
                highlightbackground=ACCENT,
            )
        else:
            self.btn_tp_quick.configure(
                text="Mouse: Off",
                bg=BG_ELEVATED,
                fg=FG_SUB,
                highlightbackground=BORDER,
            )

    def _on_touchpad_mouse_toggled(self, enabled: bool) -> None:
        if self._loading_profile:
            return
        self.profile.touchpad_mouse = enabled
        self._sync_touchpad_badge()
        self.engine.update_profile(self.profile, sync_lightbar=False)
        state_str = "enabled" if enabled else "disabled"
        self._enqueue_log(f"[Touchpad] Mouse mode {state_str}.")

    def _toggle_touchpad_mouse(self) -> None:
        new_val = not self.profile.touchpad_mouse
        self.sw_tp_mouse.set_value(new_val, notify=False)
        self._on_touchpad_mouse_toggled(new_val)

    def _redraw_curve_graph(self) -> None:
        w, h = 112, 112
        pad = 12
        gw = w - 2 * pad
        gh = h - 2 * pad

        dz = self.profile.right_deadzone
        anti = self.profile.anti_deadzone
        sens = self.profile.right_sensitivity
        mode = self.profile.right_curve

        pts: list[float] = []
        steps = 36
        for i in range(steps + 1):
            x = i / steps
            if x <= dz:
                y = 0.0
            else:
                scaled = (x - dz) / max(1e-6, 1.0 - dz)
                y = apply_curve(scaled, mode) * sens
                if anti > 0.0 and y > 0.0:
                    y = anti + y * (1.0 - anti)
                y = min(1.0, y)
            pts.extend((pad + x * gw, (h - pad) - y * gh))

        self.curve_canvas.coords(self._curve_line, *pts)

    def _on_quick_profile_picked(self, _event=None) -> None:
        selected = self.cmb_quick_profile.get().strip()
        if not selected:
            return
        self.profile = self.store.load(selected)
        self._load_profile_into_ui(self.profile)
        self.engine.update_profile(self.profile, sync_lightbar=True)
        self._enqueue_log(f"[Profile] Loaded '{self.profile.name}'.")

    def _save_current_profile(self) -> None:
        typed_name = self.ent_profile_name.get().strip()
        if typed_name:
            self.profile.name = typed_name
        self.store.save(self.profile)
        names = self.store.list_names()
        self.cmb_quick_profile.configure(values=names)
        self.cmb_quick_profile.set(self.profile.name)
        self._enqueue_log(f"[Profile] Saved '{self.profile.name}'.")

    def _delete_selected_profile(self) -> None:
        names = self.store.list_names()
        if len(names) <= 1:
            messagebox.showinfo("OpenDS4", "Cannot delete the last remaining profile.")
            return
        target = self.profile.name
        if self.store.delete(target):
            remaining = self.store.list_names()
            self.cmb_quick_profile.configure(values=remaining)
            self.profile = self.store.load(remaining[0])
            self._load_profile_into_ui(self.profile)
            self.engine.update_profile(self.profile, sync_lightbar=True)
            self._enqueue_log(f"[Profile] Deleted '{target}'.")

    def _copy_python_path_for_hidhide(self) -> None:
        exe_path = sys.executable
        self.root.clipboard_clear()
        self.root.clipboard_append(exe_path)
        self.root.update_idletasks()
        self._enqueue_log(f"[HidHide] Copied interpreter path: {exe_path}")

    def _toggle_demo_mode(self) -> None:
        self._demo_active = not self._demo_active
        if self._demo_active:
            self.btn_sim.configure(text="Simulator: On", bg=ACCENT, fg="#ffffff")
            self.engine.start(demo_mode=True)
        else:
            self.btn_sim.configure(text="Simulator: Off", bg=BG_ELEVATED, fg=FG_SUB)
            self.engine.start(demo_mode=False)
        self.btn_engine.configure(text="Stop", bg=RED, fg="#ffffff")

    def _toggle_engine(self) -> None:
        if self.engine.is_running:
            self.engine.stop()
            self.btn_engine.configure(text="Start", bg=GREEN, fg="#082419")
            self._enqueue_log("[Engine] Stopped.")
        else:
            self.engine.start(demo_mode=self._demo_active)
            self.btn_engine.configure(text="Stop", bg=RED, fg="#ffffff")

    # -------------------- 60 FPS Diff-Cached Telemetry Loop --------------------

    def _enqueue_log(self, line: str) -> None:
        self.log_queue.put(line)

    def _drain_logs(self) -> None:
        added = False
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if not added:
                self.txt_log.configure(state="normal")
                added = True
            self.txt_log.insert("end", line + "\n")
        if added:
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")

    def _update_radar(
        self,
        radar: dict,
        key: str,
        raw_x: int,
        raw_y: int,
        out_x: int,
        out_y: int,
        deadzone: float,
    ) -> None:
        cv: tk.Canvas = radar["canvas"]
        c: int = radar["center"]
        r: int = radar["radius"]

        dz_px = max(2.0, deadzone * r)
        cv.coords(radar["dz_oval"], c - dz_px, c - dz_px, c + dz_px, c + dz_px)

        rx_px = c + ((raw_x - 128.0) / 128.0) * r
        ry_px = c + ((raw_y - 128.0) / 128.0) * r
        cv.coords(radar["raw_dot"], rx_px - 3, ry_px - 3, rx_px + 3, ry_px + 3)

        ox_px = c + (out_x / 32767.0) * r
        oy_px = c - (out_y / 32767.0) * r
        cv.coords(radar["out_line"], c, c, ox_px, oy_px)
        cv.coords(radar["out_dot"], ox_px - 5, oy_px - 5, ox_px + 5, oy_px + 5)

        self._set_cached(radar["label"], f"radar_{key}", text=f"Raw {raw_x:3d},{raw_y:3d}  |  XInput {out_x:6d},{out_y:6d}")

    def _update_trigger_widget(self, widget: dict, raw_v: int, out_v: int, key: str = "") -> None:
        cv: tk.Canvas = widget["canvas"]
        w = max(1, cv.winfo_width())
        h: int = widget["height"]
        bar_id = key or str(id(widget))
        target = float(out_v)
        cur = self._bar_smooth.get(bar_id, target)
        cur = target if abs(target - cur) < 0.75 else (cur + (target - cur) * 0.48)
        self._bar_smooth[bar_id] = cur
        fill_w = (cur / 255.0) * w
        cv.coords(widget["rect"], 0, 0, fill_w, h)
        self._set_cached(widget["label"], f"trig_{bar_id}", text=f"{out_v:3d} / 255")

    def _render_telemetry(self, t: EngineTelemetry) -> None:
        # Always-visible header status (diff-cached so idle frames cost ~0)
        if not t.running:
            self._set_cached(self.lbl_conn, "conn", text="Stopped", fg=FG_MUTED)
        elif t.connected:
            col = CYAN if t.demo_mode else GREEN
            self._set_cached(self.lbl_conn, "conn", text=f"{t.device_name}  ·  {t.transport}", fg=col)
        else:
            self._set_cached(self.lbl_conn, "conn", text=t.device_name, fg=AMBER)

        self._set_cached(
            self.lbl_vigem,
            "vigem",
            text=f"XInput: {t.vigem_status}",
            fg=GREEN if t.vigem_ready else AMBER,
        )

        if t.connected:
            chg = " (USB)" if t.ds4.cable_connected else ""
            b_col = RED if t.ds4.battery_percent <= 20 else FG_TEXT
            self._set_cached(self.lbl_battery, "batt", text=f"Battery {t.ds4.battery_percent}%{chg}", fg=b_col)
            self._set_cached(self.lbl_rate, "rate", text=f"{t.poll_hz:4.0f} Hz · {t.frame_ms:3.1f} ms")
            lr, lg, lb = t.lightbar_rgb
        else:
            self._set_cached(self.lbl_battery, "batt", text="Battery --", fg=FG_MUTED)
            self._set_cached(self.lbl_rate, "rate", text="0 Hz")
            lr, lg, lb, _, _ = self.profile.effective_lightbar_bytes(
                battery_percent=100,
                cable_connected=True,
                now=time.monotonic(),
            )

        hex_col = f"#{lr:02x}{lg:02x}{lb:02x}"
        self._set_cached(self.header_led_dot, "hdr_led", bg=hex_col if (lr or lg or lb) else BG_ELEVATED)

        # Page-specific rendering so hidden pages never consume GDI / Canvas time
        page = self._current_page
        if page == "monitor":
            self._update_radar(
                self.left_radar, "L", t.ds4.lx, t.ds4.ly, t.xbox.thumb_lx, t.xbox.thumb_ly, self.profile.left_deadzone
            )
            self._update_radar(
                self.right_radar, "R", t.ds4.rx, t.ds4.ry, t.xbox.thumb_rx, t.xbox.thumb_ry, self.profile.right_deadzone
            )
            self._update_trigger_widget(self.l2_bar, t.ds4.l2, t.xbox.left_trigger, "l2")
            self._update_trigger_widget(self.r2_bar, t.ds4.r2, t.xbox.right_trigger, "r2")

            for bkey, pill in self.button_pills.items():
                pressed = bool(getattr(t.ds4, bkey, False))
                self._set_cached(
                    pill,
                    f"btn_{bkey}",
                    bg=ACCENT if pressed else BG_INPUT,
                    fg="#ffffff" if pressed else FG_MUTED,
                )

            touches = []
            for finger, dot_id, tag in ((t.ds4.touch_1, self.tp_dot1, "T1"), (t.ds4.touch_2, self.tp_dot2, "T2")):
                if finger.active:
                    px = (finger.x / DS4_TOUCHPAD_WIDTH) * self.tp_w
                    py = (finger.y / DS4_TOUCHPAD_HEIGHT) * self.tp_h
                    self.tp_canvas.coords(dot_id, px - 5, py - 5, px + 5, py + 5)
                    touches.append(f"{tag} ({finger.x}, {finger.y})")
                else:
                    self.tp_canvas.coords(dot_id, -12, -12, -4, -4)
            self.tp_canvas.itemconfig(self.tp_text, text="   ".join(touches) if touches else "No touch")

            self._set_cached(
                self.lbl_telemetry,
                "imu_box",
                text=(
                    f"Gyro     {t.ds4.gyro_x}, {t.ds4.gyro_y}, {t.ds4.gyro_z}\n"
                    f"Accel    {t.ds4.accel_x}, {t.ds4.accel_y}, {t.ds4.accel_z}\n\n"
                    f"Heavy L  {t.rumble_large:3d} / 255\n"
                    f"Light R  {t.rumble_small:3d} / 255\n\n"
                    f"Packets  {t.packets_ok}\n"
                    f"CRC Err  {t.crc_dropped}"
                ),
            )
        elif page == "lightbar":
            fg_info = "#111318" if (lr * 0.299 + lg * 0.587 + lb * 0.114) > 150 else "#ffffff"
            mode_label = LIGHTBAR_MODES.get(self.profile.lightbar_mode, "Solid Color")
            self._set_cached(self.led_preview, "led_prev_bg", bg=hex_col)
            self._set_cached(
                self.lbl_led_info,
                "led_prev_txt",
                bg=hex_col,
                fg=fg_info,
                text=f"{hex_col.upper()}  ·  {mode_label}",
            )
        elif page == "vibration":
            self._update_trigger_widget(self.heavy_meter, t.rumble_large, t.rumble_large, "m_heavy")
            self._update_trigger_widget(self.light_meter, t.rumble_small, t.rumble_small, "m_light")
        elif page == "profiles":
            self._set_cached(
                self.lbl_hidhide,
                "hh_lbl",
                text="HidHide: Installed" if t.hidhide_installed else "HidHide: Not installed (optional)",
                fg=GREEN if t.hidhide_installed else FG_SUB,
            )

    # -------------------- System Tray & Minimize Menu --------------------

    def _build_tray_snapshot(self) -> TraySnapshot:
        t = self.engine.get_telemetry()
        if not t.running:
            status = "Mapper stopped"
            tip = "OpenDS4 - Stopped"
            rgb = (90, 98, 118)
        elif t.connected:
            chg = " (USB)" if t.ds4.cable_connected else ""
            status = f"{t.device_name} ({t.transport})  ·  {t.ds4.battery_percent}%{chg}"
            tip = f"OpenDS4 - {t.transport} · Battery {t.ds4.battery_percent}%{chg}"
            rgb = t.lightbar_rgb
        else:
            status = "Searching for DualShock 4..."
            tip = f"OpenDS4 - Profile: {self.profile.name}"
            rgb = (self.profile.lightbar_r, self.profile.lightbar_g, self.profile.lightbar_b)

        return TraySnapshot(
            status_line=status,
            tooltip=tip,
            active_profile=self.profile.name,
            profile_names=self.store.list_names(),
            active_lightbar_mode=self.profile.lightbar_mode,
            lightbar_modes=dict(LIGHTBAR_MODES),
            rumble_enabled=self.profile.rumble_enabled,
            trigger_rumble=self.profile.trigger_rumble,
            touchpad_mouse=self.profile.touchpad_mouse,
            engine_running=self.engine.is_running,
            accent_rgb=rgb,
        )

    def _handle_tray_action(self, action_id: str) -> None:
        if action_id == "restore":
            self.restore_from_tray()
        elif action_id == "hide_tray":
            self.hide_to_tray()
        elif action_id == "minimize_taskbar":
            self._minimize_to_taskbar()
        elif action_id.startswith("profile:"):
            target_name = action_id.split(":", 1)[1]
            self.profile = self.store.load(target_name)
            self._load_profile_into_ui(self.profile)
            self.engine.update_profile(self.profile, sync_lightbar=True)
            self._enqueue_log(f"[Profile] Loaded '{self.profile.name}'.")
        elif action_id.startswith("lightbar:"):
            mode_key = action_id.split(":", 1)[1]
            self._select_lightbar_mode(mode_key)
        elif action_id == "toggle_touchpad":
            self._toggle_touchpad_mouse()
        elif action_id == "toggle_rumble":
            new_val = not self.profile.rumble_enabled
            self.profile.rumble_enabled = new_val
            self.sw_rumble.set_value(new_val, notify=False)
            self.engine.update_profile(self.profile, sync_lightbar=True)
        elif action_id == "toggle_trigger_haptics":
            new_val = not self.profile.trigger_rumble
            self.profile.trigger_rumble = new_val
            self.sw_trig_rumble.set_value(new_val, notify=False)
            self.engine.update_profile(self.profile, sync_lightbar=True)
        elif action_id == "toggle_engine":
            self._toggle_engine()
        elif action_id == "quit":
            self._on_close()

    def _on_tray_button_clicked(self) -> None:
        snap = self._build_tray_snapshot()
        menu_kw = {
            "tearoff": False,
            "bg": BG_ELEVATED,
            "fg": FG_TEXT,
            "activebackground": ACCENT,
            "activeforeground": "#ffffff",
            "bd": 1,
            "relief": "solid",
            "font": ("Segoe UI", 9),
        }
        menu = tk.Menu(self.root, **menu_kw)

        if self.tray.available:
            menu.add_command(label="Hide to System Tray", command=self.hide_to_tray)
        menu.add_command(label="Minimize to Taskbar", command=self._minimize_to_taskbar)
        menu.add_separator()

        prof_menu = tk.Menu(menu, **menu_kw)
        for name in snap.profile_names:
            mark = "* " if name == snap.active_profile else "   "
            prof_menu.add_command(
                label=f"{mark}{name}",
                command=lambda n=name: self._handle_tray_action(f"profile:{n}"),
            )
        menu.add_cascade(label="Active Profile", menu=prof_menu)

        led_menu = tk.Menu(menu, **menu_kw)
        for mode_key, title in snap.lightbar_modes.items():
            mark = "* " if mode_key == snap.active_lightbar_mode else "   "
            led_menu.add_command(
                label=f"{mark}{title}",
                command=lambda m=mode_key: self._handle_tray_action(f"lightbar:{m}"),
            )
        menu.add_cascade(label="Lightbar Mode", menu=led_menu)

        tp_mark = "[On] " if snap.touchpad_mouse else "[Off]"
        menu.add_command(
            label=f"Touchpad Mouse {tp_mark}",
            command=lambda: self._handle_tray_action("toggle_touchpad"),
        )
        vib_mark = "[On] " if snap.rumble_enabled else "[Off]"
        menu.add_command(
            label=f"Vibration {vib_mark}",
            command=lambda: self._handle_tray_action("toggle_rumble"),
        )
        menu.add_separator()
        menu.add_command(label="Quit OpenDS4", command=self._on_close)

        bx = self.btn_tray.winfo_rootx()
        by = self.btn_tray.winfo_rooty() + self.btn_tray.winfo_height() + 2
        try:
            menu.tk_popup(bx, by)
        finally:
            menu.grab_release()

    def _minimize_to_taskbar(self) -> None:
        self._hiding_to_tray = True
        try:
            self.root.iconify()
        finally:
            self.root.after(120, lambda: setattr(self, "_hiding_to_tray", False))

    def _on_root_unmap(self, event: tk.Event) -> None:
        if event.widget is not self.root or self._hiding_to_tray:
            return
        if not self.profile.minimize_to_tray or not self.tray.available:
            return
        try:
            if self.root.state() == "iconic":
                self.root.after(15, self.hide_to_tray)
        except tk.TclError:
            pass

    def hide_to_tray(self, *, silent: bool = False) -> None:
        if not self.tray.available:
            self._minimize_to_taskbar()
            return
        self._hiding_to_tray = True
        self._in_tray = True
        self.root.withdraw()
        self.tray.set_snapshot(self._build_tray_snapshot())
        if not silent and not self._tray_balloon_shown:
            self._tray_balloon_shown = True
            self.tray.show_balloon(
                "OpenDS4",
                "Minimized to system tray. Left-click to reopen or right-click for quick settings.",
            )
        self.root.after(80, lambda: setattr(self, "_hiding_to_tray", False))

    def restore_from_tray(self) -> None:
        self._in_tray = False
        self.root.deiconify()
        try:
            self.root.state("normal")
        except tk.TclError:
            pass
        _apply_windows_dark_frame(self.root)
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass

    def _drain_tray_actions(self) -> None:
        while True:
            act = self.tray.pop_action()
            if act is None:
                break
            self._handle_tray_action(act)
            if self.tray.available:
                self.tray.set_snapshot(self._build_tray_snapshot())

    def _schedule_ui_refresh(self) -> None:
        self._drain_tray_actions()
        if not self.root.winfo_exists():
            return

        self._ui_tick += 1

        if self._in_tray:
            # Ultra-low-overhead tray mode: skip all UI/Canvas/telemetry rendering.
            # Sync tray snapshot only every ~3.5 seconds (and set_snapshot only wakes
            # the Win32 tray thread when tooltip text actually changes).
            if self.tray.available and (self._ui_tick % 25 == 0):
                self.tray.set_snapshot(self._build_tray_snapshot())
            self._after_id = self.root.after(140, self._schedule_ui_refresh)
            return

        self._drain_logs()
        t = self.engine.get_telemetry()
        self._render_telemetry(t)
        if self.tray.available and (self._ui_tick % 30 == 0):
            self.tray.set_snapshot(self._build_tray_snapshot())

        interval_ms = 16 if (t.connected or t.demo_mode) else 100
        self._after_id = self.root.after(interval_ms, self._schedule_ui_refresh)

    def _on_close(self) -> None:
        for job_attr in ("_after_id", "_nav_anim_id"):
            job = getattr(self, job_attr, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, job_attr, None)
        self.tray.stop()
        self.engine.stop()
        self.root.destroy()


def _run_self_test() -> int:
    """Fast preflight check used by run_windows.bat before detaching pythonw.exe."""
    from ds4_touchpad import INPUT, TouchpadMouseController
    from ds4_tray import MSG, NOTIFYICONDATAW, WNDCLASSEXW, render_gamepad_icon_rgba

    assert ctypes.sizeof(INPUT) > 0
    assert ctypes.sizeof(MSG) > 0
    assert ctypes.sizeof(WNDCLASSEXW) > 0
    assert ctypes.sizeof(NOTIFYICONDATAW) > 0
    assert len(render_gamepad_icon_rgba()) == 32 * 32 * 4
    _ = TouchpadMouseController()
    _ = ControllerProfile()
    return 0


def _report_fatal_error(exc: BaseException) -> None:
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log_path = Path(__file__).resolve().parent / "opends4_error.log"
    try:
        log_path.write_text(details, encoding="utf-8")
    except OSError:
        pass

    if sys.stderr is not None:
        try:
            sys.stderr.write(details + "\n")
            sys.stderr.flush()
        except Exception:
            pass

    if sys.platform == "win32":
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
            user32.MessageBoxW.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.c_uint32,
            ]
            user32.MessageBoxW.restype = ctypes.c_int32
            msg = f"OpenDS4 failed to start:\n\n{exc}\n\nFull traceback saved to:\n{log_path}"
            user32.MessageBoxW(None, msg, "OpenDS4 Startup Error", 0x00000010)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in args:
        return _run_self_test()

    start_minimized = ("--minimized" in args) or ("-m" in args)
    _enable_windows_dpi_awareness()
    root = tk.Tk()
    OpenDS4App(root, start_minimized=start_minimized)
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as _fatal:
        _report_fatal_error(_fatal)
        raise SystemExit(1)
