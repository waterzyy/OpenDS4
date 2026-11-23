"""Controller profiles, button mapping, lightbar shaders, and rumble mixing."""

from __future__ import annotations

import colorsys
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
import re
import time

from ds4_protocol import (
    DS4InputState,
    Xbox360OutputState,
    process_stick_pair,
    process_trigger,
)

DS4_BUTTON_LABELS: dict[str, str] = {
    "cross": "Cross",
    "circle": "Circle",
    "square": "Square",
    "triangle": "Triangle",
    "l1": "L1",
    "r1": "R1",
    "l3": "L3 Click",
    "r3": "R3 Click",
    "share": "Share",
    "options": "Options",
    "ps": "PS Button",
    "touch_click": "Touchpad",
    "dpad_up": "D-Pad Up",
    "dpad_down": "D-Pad Down",
    "dpad_left": "D-Pad Left",
    "dpad_right": "D-Pad Right",
}

XBOX_TARGETS: dict[str, str] = {
    "A": "Xbox A",
    "B": "Xbox B",
    "X": "Xbox X",
    "Y": "Xbox Y",
    "LB": "LB",
    "RB": "RB",
    "LS": "Left Stick",
    "RS": "Right Stick",
    "Back": "Back / View",
    "Start": "Start / Menu",
    "Guide": "Xbox Guide",
    "DPadUp": "D-Pad Up",
    "DPadDown": "D-Pad Down",
    "DPadLeft": "D-Pad Left",
    "DPadRight": "D-Pad Right",
    "Disabled": "Unbound",
}

DEFAULT_BUTTON_MAP: dict[str, str] = {
    "cross": "A",
    "circle": "B",
    "square": "X",
    "triangle": "Y",
    "l1": "LB",
    "r1": "RB",
    "l3": "LS",
    "r3": "RS",
    "share": "Back",
    "options": "Start",
    "ps": "Guide",
    "touch_click": "Back",
    "dpad_up": "DPadUp",
    "dpad_down": "DPadDown",
    "dpad_left": "DPadLeft",
    "dpad_right": "DPadRight",
}

CURVE_MODES: dict[str, str] = {
    "linear": "Linear",
    "precision": "Precision",
    "aggressive": "Fast",
}

LIGHTBAR_MODES: dict[str, str] = {
    "static": "Solid Color",
    "breathing": "Breathing",
    "color_pulse": "Smooth Pulse",
    "rainbow": "Color Cycle",
    "ocean": "Cool Wave",
    "warm": "Warm Glow",
    "triggers": "Trigger Pressure",
    "battery_gauge": "Battery Level",
}


def _lerp_cyclic_palette(
    palette: tuple[tuple[int, int, int], ...],
    phase: float,
) -> tuple[float, float, float]:
    n = len(palette)
    scaled = (phase % 1.0) * n
    idx = int(scaled) % n
    nxt = (idx + 1) % n
    frac = scaled - int(scaled)
    smooth = 0.5 - 0.5 * math.cos(math.pi * frac)
    c0 = palette[idx]
    c1 = palette[nxt]
    return (
        c0[0] + (c1[0] - c0[0]) * smooth,
        c0[1] + (c1[1] - c0[1]) * smooth,
        c0[2] + (c1[2] - c0[2]) * smooth,
    )


def _hsv_bytes(h: float, s: float = 1.0, v: float = 1.0) -> tuple[float, float, float]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return r * 255.0, g * 255.0, b * 255.0


@dataclass
class ControllerProfile:
    name: str = "Default"

    left_deadzone: float = 0.08
    right_deadzone: float = 0.08
    anti_deadzone: float = 0.00
    left_sensitivity: float = 1.00
    right_sensitivity: float = 1.00
    left_curve: str = "linear"
    right_curve: str = "linear"
    invert_left_y: bool = False
    invert_right_y: bool = False
    swap_sticks: bool = False

    l2_deadzone: float = 0.02
    r2_deadzone: float = 0.02

    lightbar_mode: str = "static"
    lightbar_speed: float = 1.00
    lightbar_r: int = 0
    lightbar_g: int = 90
    lightbar_b: int = 255
    lightbar_brightness: float = 0.80
    low_battery_warning: bool = True

    rumble_enabled: bool = True
    rumble_strength: float = 1.00
    rumble_heavy_scale: float = 1.00
    rumble_light_scale: float = 1.00
    trigger_rumble: bool = False
    trigger_rumble_scale: float = 0.45

    bt_poll_ms: int = 4
    minimize_to_tray: bool = True

    touchpad_mouse: bool = False
    touchpad_sensitivity: float = 1.00
    touchpad_tap_click: bool = True

    button_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_BUTTON_MAP))

    @property
    def is_animated_lightbar(self) -> bool:
        return self.lightbar_mode != "static"

    def compute_motors(
        self,
        *,
        game_large: int,
        game_small: int,
        ds4: DS4InputState | None = None,
        test_large: int = 0,
        test_small: int = 0,
    ) -> tuple[int, int]:
        """Compute (large_heavy_motor, small_light_motor) in 0..255."""
        if test_large > 0 or test_small > 0:
            return max(0, min(255, int(test_large))), max(0, min(255, int(test_small)))

        if not self.rumble_enabled:
            return 0, 0

        master = max(0.0, min(1.5, float(self.rumble_strength)))
        heavy_gain = max(0.0, min(1.5, float(self.rumble_heavy_scale))) * master
        light_gain = max(0.0, min(1.5, float(self.rumble_light_scale))) * master

        large_val = float(game_large) * heavy_gain
        small_val = float(game_small) * light_gain

        if self.trigger_rumble and ds4 is not None:
            trig_scale = max(0.0, min(1.0, float(self.trigger_rumble_scale)))
            if ds4.l2 > 18:
                norm_l2 = (ds4.l2 - 18.0) / 237.0
                large_val = max(large_val, (45.0 + 195.0 * (norm_l2 * norm_l2)) * trig_scale)
            if ds4.r2 > 18:
                norm_r2 = (ds4.r2 - 18.0) / 237.0
                small_val = max(small_val, (40.0 + 215.0 * (norm_r2 * norm_r2)) * trig_scale)

        return (
            max(0, min(255, int(round(large_val)))),
            max(0, min(255, int(round(small_val)))),
        )

    def _compute_mode_rgb(
        self,
        *,
        t: float,
        battery_percent: int,
        cable_connected: bool,
        ds4: DS4InputState | None,
        rumble_large: int,
        rumble_small: int,
    ) -> tuple[float, float, float]:
        mode = self.lightbar_mode if self.lightbar_mode in LIGHTBAR_MODES else "static"
        speed = max(0.20, min(3.00, float(self.lightbar_speed)))
        phase_t = t * speed

        base_r = float(self.lightbar_r)
        base_g = float(self.lightbar_g)
        base_b = float(self.lightbar_b)
        if base_r == 0.0 and base_g == 0.0 and base_b == 0.0 and mode == "breathing":
            base_r, base_g, base_b = 0.0, 120.0, 215.0

        if mode == "static":
            return base_r, base_g, base_b

        if mode == "rainbow":
            return _hsv_bytes(phase_t * 0.16, 0.95, 1.0)

        if mode == "breathing":
            wave = 0.5 - 0.5 * math.cos(phase_t * 2.2)
            env = 0.06 + 0.94 * math.pow(wave, 1.4)
            return base_r * env, base_g * env, base_b * env

        if mode == "color_pulse":
            cycle_pos = phase_t * 0.32
            cycle_idx = int(cycle_pos)
            frac = cycle_pos - cycle_idx
            hue = (cycle_idx * 0.61803398875) % 1.0
            amp = math.pow(math.sin(math.pi * frac), 1.45)
            return _hsv_bytes(hue, 0.90, 0.05 + 0.95 * amp)

        if mode == "ocean":
            palette = (
                (0, 85, 220),
                (0, 185, 235),
                (25, 210, 165),
                (20, 120, 230),
            )
            return _lerp_cyclic_palette(palette, phase_t * 0.12)

        if mode == "warm":
            palette = (
                (255, 155, 35),
                (245, 95, 40),
                (255, 190, 75),
                (225, 115, 30),
            )
            return _lerp_cyclic_palette(palette, phase_t * 0.11)

        if mode == "triggers":
            trig_intensity = 0.0
            stick_intensity = 0.0
            if ds4 is not None:
                trig_intensity = max(ds4.l2, ds4.r2) / 255.0
                sl = math.hypot(ds4.lx - 128.0, ds4.ly - 128.0) / 128.0
                sr = math.hypot(ds4.rx - 128.0, ds4.ry - 128.0) / 128.0
                stick_intensity = min(1.0, max(sl, sr))

            power = min(1.0, max(trig_intensity, stick_intensity * 0.55))
            hue = (0.54 - 0.52 * power) % 1.0
            val = 0.40 + 0.60 * power
            return _hsv_bytes(hue, 0.95, val)

        if mode == "battery_gauge":
            pct = max(0, min(100, int(battery_percent))) / 100.0
            hue = 0.35 * pct
            pulse = (0.55 + 0.45 * (0.5 - 0.5 * math.cos(phase_t * 2.6))) if cable_connected else 1.0
            return _hsv_bytes(hue, 0.95, pulse)

        return base_r, base_g, base_b

    def effective_lightbar_bytes(
        self,
        *,
        battery_percent: int = 100,
        cable_connected: bool = False,
        now: float | None = None,
        ds4: DS4InputState | None = None,
        rumble_large: int = 0,
        rumble_small: int = 0,
    ) -> tuple[int, int, int, int, int]:
        if self.low_battery_warning and not cable_connected and battery_percent <= 20:
            return 255, 0, 0, 35, 35

        timestamp = time.monotonic() if now is None else float(now)
        raw_r, raw_g, raw_b = self._compute_mode_rgb(
            t=timestamp,
            battery_percent=battery_percent,
            cable_connected=cable_connected,
            ds4=ds4,
            rumble_large=rumble_large,
            rumble_small=rumble_small,
        )

        scale = max(0.0, min(1.0, float(self.lightbar_brightness)))
        r = max(0, min(255, int(round(raw_r * scale))))
        g = max(0, min(255, int(round(raw_g * scale))))
        b = max(0, min(255, int(round(raw_b * scale))))
        return r, g, b, 0, 0

    def map_to_xbox360(self, ds4: DS4InputState) -> Xbox360OutputState:
        src_lx, src_ly = (ds4.rx, ds4.ry) if self.swap_sticks else (ds4.lx, ds4.ly)
        src_rx, src_ry = (ds4.lx, ds4.ly) if self.swap_sticks else (ds4.rx, ds4.ry)

        lx, ly = process_stick_pair(
            src_lx,
            src_ly,
            deadzone=self.left_deadzone,
            anti_deadzone=self.anti_deadzone,
            sensitivity=self.left_sensitivity,
            curve_mode=self.left_curve,
            invert_y=self.invert_left_y,
        )
        rx, ry = process_stick_pair(
            src_rx,
            src_ry,
            deadzone=self.right_deadzone,
            anti_deadzone=self.anti_deadzone,
            sensitivity=self.right_sensitivity,
            curve_mode=self.right_curve,
            invert_y=self.invert_right_y,
        )

        lt = process_trigger(ds4.l2, deadzone=self.l2_deadzone)
        rt = process_trigger(ds4.r2, deadzone=self.r2_deadzone)

        buttons: dict[str, bool] = {target: False for target in XBOX_TARGETS if target != "Disabled"}

        for ds4_btn, default_target in DEFAULT_BUTTON_MAP.items():
            if ds4_btn == "touch_click" and self.touchpad_mouse:
                continue
            if not getattr(ds4, ds4_btn, False):
                continue
            target = self.button_map.get(ds4_btn, default_target)
            if target and target != "Disabled" and target in buttons:
                buttons[target] = True

        return Xbox360OutputState(
            thumb_lx=lx,
            thumb_ly=ly,
            thumb_rx=rx,
            thumb_ry=ry,
            left_trigger=lt,
            right_trigger=rt,
            buttons=buttons,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ControllerProfile:
        clean_map = dict(DEFAULT_BUTTON_MAP)
        raw_map = data.get("button_map")
        if isinstance(raw_map, dict):
            for k, v in raw_map.items():
                if k in DS4_BUTTON_LABELS and v in XBOX_TARGETS:
                    clean_map[k] = v

        left_curve = data.get("left_curve", "linear")
        right_curve = data.get("right_curve", "linear")
        if left_curve not in CURVE_MODES:
            left_curve = "linear"
        if right_curve not in CURVE_MODES:
            right_curve = "linear"

        lightbar_mode = str(data.get("lightbar_mode", "static"))
        if lightbar_mode not in LIGHTBAR_MODES:
            lightbar_mode = "static"

        return cls(
            name=str(data.get("name", "Default"))[:64],
            left_deadzone=max(0.0, min(0.50, float(data.get("left_deadzone", 0.08)))),
            right_deadzone=max(0.0, min(0.50, float(data.get("right_deadzone", 0.08)))),
            anti_deadzone=max(0.0, min(0.35, float(data.get("anti_deadzone", 0.00)))),
            left_sensitivity=max(0.40, min(2.00, float(data.get("left_sensitivity", 1.00)))),
            right_sensitivity=max(0.40, min(2.00, float(data.get("right_sensitivity", 1.00)))),
            left_curve=left_curve,
            right_curve=right_curve,
            invert_left_y=bool(data.get("invert_left_y", False)),
            invert_right_y=bool(data.get("invert_right_y", False)),
            swap_sticks=bool(data.get("swap_sticks", False)),
            l2_deadzone=max(0.0, min(0.50, float(data.get("l2_deadzone", 0.02)))),
            r2_deadzone=max(0.0, min(0.50, float(data.get("r2_deadzone", 0.02)))),
            lightbar_mode=lightbar_mode,
            lightbar_speed=max(0.20, min(3.00, float(data.get("lightbar_speed", 1.00)))),
            lightbar_r=max(0, min(255, int(data.get("lightbar_r", 0)))),
            lightbar_g=max(0, min(255, int(data.get("lightbar_g", 90)))),
            lightbar_b=max(0, min(255, int(data.get("lightbar_b", 255)))),
            lightbar_brightness=max(0.0, min(1.0, float(data.get("lightbar_brightness", 0.80)))),
            low_battery_warning=bool(data.get("low_battery_warning", True)),
            rumble_enabled=bool(data.get("rumble_enabled", True)),
            rumble_strength=max(0.0, min(1.50, float(data.get("rumble_strength", 1.00)))),
            rumble_heavy_scale=max(0.0, min(1.50, float(data.get("rumble_heavy_scale", 1.00)))),
            rumble_light_scale=max(0.0, min(1.50, float(data.get("rumble_light_scale", 1.00)))),
            trigger_rumble=bool(data.get("trigger_rumble", False)),
            trigger_rumble_scale=max(0.0, min(1.00, float(data.get("trigger_rumble_scale", 0.45)))),
            bt_poll_ms=int(data.get("bt_poll_ms", 4)) if int(data.get("bt_poll_ms", 4)) in (1, 4, 8, 16) else 4,
            minimize_to_tray=bool(data.get("minimize_to_tray", True)),
            touchpad_mouse=bool(data.get("touchpad_mouse", False)),
            touchpad_sensitivity=max(0.25, min(3.00, float(data.get("touchpad_sensitivity", 1.00)))),
            touchpad_tap_click=bool(data.get("touchpad_tap_click", True)),
            button_map=clean_map,
        )


def builtin_presets() -> list[ControllerProfile]:
    return [
        ControllerProfile(
            name="Default",
            left_deadzone=0.08,
            right_deadzone=0.08,
            lightbar_mode="static",
            lightbar_r=36,
            lightbar_g=148,
            lightbar_b=172,
        ),
        ControllerProfile(
            name="Shooter",
            left_deadzone=0.06,
            right_deadzone=0.05,
            anti_deadzone=0.02,
            right_curve="precision",
            l2_deadzone=0.01,
            r2_deadzone=0.01,
            lightbar_mode="static",
            lightbar_r=0,
            lightbar_g=120,
            lightbar_b=215,
            bt_poll_ms=1,
        ),
        ControllerProfile(
            name="Racing",
            left_deadzone=0.05,
            right_deadzone=0.08,
            left_curve="precision",
            l2_deadzone=0.00,
            r2_deadzone=0.00,
            lightbar_mode="triggers",
            trigger_rumble=True,
            trigger_rumble_scale=0.55,
        ),
    ]


class ProfileStore:
    def __init__(self, directory: Path | str | None = None) -> None:
        base = Path(directory) if directory else Path(__file__).resolve().parent / "profiles"
        self.directory = base
        self.directory.mkdir(parents=True, exist_ok=True)
        self._ensure_defaults()

    def _safe_filename(self, profile_name: str) -> Path:
        slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", profile_name.strip())
        slug = slug.strip(". ") or "profile"
        return self.directory / f"{slug[:64]}.json"

    def _ensure_defaults(self) -> None:
        # Clean up retired preset files from older builds if present
        removed_legacy = False
        for old_name in ("Cyberpunk.json", "Precision FPS.json"):
            old_path = self.directory / old_name
            if old_path.is_file():
                try:
                    old_path.unlink(missing_ok=True)
                    removed_legacy = True
                except OSError:
                    pass

        if not any(self.directory.glob("*.json")):
            for preset in builtin_presets():
                self.save(preset)
        elif removed_legacy:
            existing = set(self.list_names())
            for preset in builtin_presets():
                if preset.name not in existing:
                    self.save(preset)

    def list_names(self) -> list[str]:
        names: list[str] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                names.append(str(data.get("name", path.stem)))
            except (OSError, ValueError, TypeError):
                continue
        return names

    def load(self, name: str) -> ControllerProfile:
        for path in self.directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("name") == name or path.stem == name:
                    return ControllerProfile.from_dict(data)
            except (OSError, ValueError, TypeError):
                continue
        return ControllerProfile(name=name)

    def save(self, profile: ControllerProfile) -> Path:
        target = self._safe_filename(profile.name)
        tmp = target.with_suffix(".json.tmp")
        payload = json.dumps(profile.to_dict(), ensure_ascii=False, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(target)
        return target

    def delete(self, name: str) -> bool:
        for path in self.directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("name") == name or path.stem == name:
                    path.unlink(missing_ok=True)
                    return True
            except (OSError, ValueError):
                continue
        return False
