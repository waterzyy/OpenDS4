"""Optional DualShock 4 touchpad-to-mouse translation for Windows.

Translates 1-finger glides into smooth relative cursor movement, 2-finger
vertical slides into mouse wheel scrolling, physical touchpad presses into
left/right click-and-drag, and optional 1-finger / 2-finger taps into
left/right clicks. Uses Win32 SendInput via ctypes with explicit fixed-width
ABI types and zero external dependencies.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import math
import sys
import time
from typing import Callable

from ds4_protocol import DS4InputState

IS_WINDOWS = sys.platform == "win32"

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800

RIGHT_CLICK_ZONE_X = 1360  # Right ~30% of the 1920-wide touchpad

INT32 = ctypes.c_int32
UINT32 = ctypes.c_uint32
UINT16 = ctypes.c_uint16
ULONG_PTR = ctypes.c_size_t


@dataclass(frozen=True, slots=True)
class MouseAction:
    dx: int = 0
    dy: int = 0
    flags: int = 0
    wheel: int = 0


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", INT32),
        ("dy", INT32),
        ("mouseData", UINT32),
        ("dwFlags", UINT32),
        ("time", UINT32),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", UINT16),
        ("wScan", UINT16),
        ("dwFlags", UINT32),
        ("time", UINT32),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", UINT32),
        ("wParamL", UINT16),
        ("wParamH", UINT16),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", UINT32),
        ("u", _INPUT_UNION),
    ]


_SEND_INPUT_FN = None


def _get_send_input():
    global _SEND_INPUT_FN
    if not IS_WINDOWS:
        return None
    if _SEND_INPUT_FN is None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        user32.SendInput.argtypes = [UINT32, ctypes.POINTER(INPUT), ctypes.c_int]
        user32.SendInput.restype = UINT32
        _SEND_INPUT_FN = user32.SendInput
    return _SEND_INPUT_FN


def send_win32_mouse_actions(actions: list[MouseAction]) -> None:
    """Dispatch queued MouseAction items to Windows via a single SendInput call."""
    if not IS_WINDOWS or not actions:
        return
    send_fn = _get_send_input()
    if send_fn is None:
        return
    arr = (INPUT * len(actions))()
    for idx, act in enumerate(actions):
        arr[idx].type = INPUT_MOUSE
        arr[idx].u.mi.dx = int(act.dx)
        arr[idx].u.mi.dy = int(act.dy)
        arr[idx].u.mi.mouseData = ctypes.c_uint32(int(act.wheel)).value
        arr[idx].u.mi.dwFlags = int(act.flags)
        arr[idx].u.mi.time = 0
        arr[idx].u.mi.dwExtraInfo = 0
    send_fn(len(actions), arr, ctypes.sizeof(INPUT))


class TouchpadMouseController:
    """Stateful gesture and cursor processor for the DualShock 4 touchpad."""

    def __init__(
        self,
        dispatcher: Callable[[list[MouseAction]], None] | None = None,
    ) -> None:
        self._dispatch = dispatcher or send_win32_mouse_actions

        self._was_enabled = False
        self._prev_t1_active = False
        self._prev_t1_id = -1
        self._prev_t1_x = 0
        self._prev_t1_y = 0

        self._prev_t2_active = False
        self._prev_t2_id = -1
        self._prev_t2_x = 0
        self._prev_t2_y = 0

        self._rem_x = 0.0
        self._rem_y = 0.0
        self._scroll_rem = 0.0

        self._gesture_start_ts = 0.0
        self._start_t1_x = 0
        self._start_t1_y = 0
        self._start_t2_x = 0
        self._start_t2_y = 0
        self._max_travel_1 = 0.0
        self._max_travel_2 = 0.0
        self._saw_two_fingers = False
        self._physical_clicked_in_gesture = False

        self._prev_click = False
        self._held_button: str | None = None  # "left" or "right"

    def reset(self) -> None:
        """Clear finger tracking and release any held mouse button."""
        out: list[MouseAction] = []
        if self._held_button == "left":
            out.append(MouseAction(flags=MOUSEEVENTF_LEFTUP))
        elif self._held_button == "right":
            out.append(MouseAction(flags=MOUSEEVENTF_RIGHTUP))

        self._prev_t1_active = False
        self._prev_t1_id = -1
        self._prev_t2_active = False
        self._prev_t2_id = -1
        self._rem_x = 0.0
        self._rem_y = 0.0
        self._scroll_rem = 0.0
        self._max_travel_1 = 0.0
        self._max_travel_2 = 0.0
        self._saw_two_fingers = False
        self._physical_clicked_in_gesture = False
        self._prev_click = False
        self._held_button = None

        if out:
            self._dispatch(out)

    def process(
        self,
        ds4: DS4InputState,
        *,
        enabled: bool,
        sensitivity: float = 1.0,
        tap_to_click: bool = True,
        now: float | None = None,
    ) -> list[MouseAction]:
        if not enabled:
            if self._was_enabled:
                self.reset()
                self._was_enabled = False
            return []

        self._was_enabled = True
        ts = time.monotonic() if now is None else now
        sens = max(0.25, min(3.0, float(sensitivity)))
        actions: list[MouseAction] = []

        t1 = ds4.touch_1
        t2 = ds4.touch_2
        any_now = t1.active or t2.active
        any_prev = self._prev_t1_active or self._prev_t2_active

        if any_now and not any_prev:
            self._gesture_start_ts = ts
            self._max_travel_1 = 0.0
            self._max_travel_2 = 0.0
            self._saw_two_fingers = False
            self._physical_clicked_in_gesture = ds4.touch_click
            self._rem_x = 0.0
            self._rem_y = 0.0
            self._scroll_rem = 0.0
            if t1.active:
                self._start_t1_x, self._start_t1_y = t1.x, t1.y
            if t2.active:
                self._start_t2_x, self._start_t2_y = t2.x, t2.y

        if t2.active and not self._prev_t2_active:
            self._start_t2_x, self._start_t2_y = t2.x, t2.y

        if t1.active and t2.active:
            self._saw_two_fingers = True

        if ds4.touch_click:
            self._physical_clicked_in_gesture = True

        if t1.active:
            if not self._prev_t1_active or t1.tracking_id != self._prev_t1_id:
                self._start_t1_x, self._start_t1_y = t1.x, t1.y
            else:
                d1 = math.hypot(t1.x - self._start_t1_x, t1.y - self._start_t1_y)
                if d1 > self._max_travel_1:
                    self._max_travel_1 = d1

        if t2.active:
            if not self._prev_t2_active or t2.tracking_id != self._prev_t2_id:
                self._start_t2_x, self._start_t2_y = t2.x, t2.y
            else:
                d2 = math.hypot(t2.x - self._start_t2_x, t2.y - self._start_t2_y)
                if d2 > self._max_travel_2:
                    self._max_travel_2 = d2

        # Two-finger vertical scroll
        if (
            t1.active
            and t2.active
            and self._prev_t1_active
            and self._prev_t2_active
            and t1.tracking_id == self._prev_t1_id
            and t2.tracking_id == self._prev_t2_id
            and self._held_button is None
        ):
            dy1 = t1.y - self._prev_t1_y
            dy2 = t2.y - self._prev_t2_y
            if abs(dy1) < 320 and abs(dy2) < 320:
                avg_dy = (dy1 + dy2) * 0.5
                self._scroll_rem += -avg_dy * 2.1 * sens
                if abs(self._scroll_rem) >= 24.0:
                    wheel_step = int(self._scroll_rem)
                    self._scroll_rem -= wheel_step
                    actions.append(MouseAction(flags=MOUSEEVENTF_WHEEL, wheel=wheel_step))

        # Single-finger cursor glide
        elif (
            t1.active
            and not t2.active
            and self._prev_t1_active
            and t1.tracking_id == self._prev_t1_id
        ):
            raw_dx = t1.x - self._prev_t1_x
            raw_dy = t1.y - self._prev_t1_y
            if abs(raw_dx) < 340 and abs(raw_dy) < 340:
                speed = math.hypot(raw_dx, raw_dy)
                gain = sens * (0.58 + min(0.85, speed * 0.018))
                self._rem_x += raw_dx * gain
                self._rem_y += raw_dy * gain
                ix = int(self._rem_x)
                iy = int(self._rem_y)
                if ix != 0 or iy != 0:
                    self._rem_x -= ix
                    self._rem_y -= iy
                    actions.append(MouseAction(dx=ix, dy=iy, flags=MOUSEEVENTF_MOVE))

        # Physical mechanical touchpad press -> click & drag
        if ds4.touch_click and not self._prev_click:
            use_right = t2.active or (t1.active and t1.x >= RIGHT_CLICK_ZONE_X)
            if use_right:
                self._held_button = "right"
                actions.append(MouseAction(flags=MOUSEEVENTF_RIGHTDOWN))
            else:
                self._held_button = "left"
                actions.append(MouseAction(flags=MOUSEEVENTF_LEFTDOWN))
        elif not ds4.touch_click and self._prev_click:
            if self._held_button == "left":
                actions.append(MouseAction(flags=MOUSEEVENTF_LEFTUP))
            elif self._held_button == "right":
                actions.append(MouseAction(flags=MOUSEEVENTF_RIGHTUP))
            self._held_button = None

        # Tap-to-click when fingers lift cleanly without a physical press
        if tap_to_click and any_prev and not any_now and not self._physical_clicked_in_gesture:
            dt = ts - self._gesture_start_ts
            if self._saw_two_fingers:
                if 0.010 <= dt <= 0.230 and self._max_travel_1 <= 36.0 and self._max_travel_2 <= 36.0:
                    actions.append(MouseAction(flags=MOUSEEVENTF_RIGHTDOWN))
                    actions.append(MouseAction(flags=MOUSEEVENTF_RIGHTUP))
            else:
                if 0.010 <= dt <= 0.190 and self._max_travel_1 <= 26.0:
                    actions.append(MouseAction(flags=MOUSEEVENTF_LEFTDOWN))
                    actions.append(MouseAction(flags=MOUSEEVENTF_LEFTUP))

        self._prev_t1_active = t1.active
        self._prev_t1_id = t1.tracking_id if t1.active else -1
        self._prev_t1_x = t1.x
        self._prev_t1_y = t1.y

        self._prev_t2_active = t2.active
        self._prev_t2_id = t2.tracking_id if t2.active else -1
        self._prev_t2_x = t2.x
        self._prev_t2_y = t2.y

        self._prev_click = ds4.touch_click

        if actions:
            self._dispatch(actions)
        return actions
