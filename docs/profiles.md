# Profile reference

A profile is a JSON file in the `profiles/` directory next to `ds4_profile.py`. The directory is created on first launch and seeded with four presets. Changes made in the UI are saved when you click **Save** on the **Mapping & Profiles** page.

Values outside the ranges below are clamped when the file is loaded, so a hand-edited profile cannot crash the app.

## Sticks

| Key | Default | Range | Description |
| --- | --- | --- | --- |
| `left_deadzone` | `0.08` | `0.00` – `0.50` | Radial deadzone for the left stick, as a fraction of full deflection. |
| `right_deadzone` | `0.08` | `0.00` – `0.50` | Radial deadzone for the right stick. |
| `anti_deadzone` | `0.00` | `0.00` – `0.35` | Minimum output once the stick leaves the deadzone. Useful on worn sticks. |
| `left_sensitivity` | `1.00` | `0.40` – `2.00` | Output multiplier for the left stick. |
| `right_sensitivity` | `1.00` | `0.40` – `2.00` | Output multiplier for the right stick. |
| `left_curve` | `linear` | `linear`, `precision`, `aggressive` | Response curve for the left stick. |
| `right_curve` | `linear` | `linear`, `precision`, `aggressive` | Response curve for the right stick. |
| `invert_left_y` | `false` | boolean | Invert the left stick's vertical axis. |
| `invert_right_y` | `false` | boolean | Invert the right stick's vertical axis. |
| `swap_sticks` | `false` | boolean | Swap left and right stick output. |

Curves:

- `linear` — output matches input.
- `precision` — `x^1.65`, softer near the center for aiming.
- `aggressive` — `x^0.72`, faster near the center. Labeled **Fast** in the UI.

## Triggers

| Key | Default | Range | Description |
| --- | --- | --- | --- |
| `l2_deadzone` | `0.02` | `0.00` – `0.50` | Deadzone for L2. |
| `r2_deadzone` | `0.02` | `0.00` – `0.50` | Deadzone for R2. |

Triggers are rescaled across the remaining travel after the deadzone, so the full 0–255 range is still available.

## Lightbar

| Key | Default | Range | Description |
| --- | --- | --- | --- |
| `lightbar_mode` | `static` | one of the 12 modes | Animation or behavior. |
| `lightbar_speed` | `1.00` | `0.20` – `3.00` | Animation speed multiplier. |
| `lightbar_r` | `0` | `0` – `255` | Static color, red channel. |
| `lightbar_g` | `90` | `0` – `255` | Static color, green channel. |
| `lightbar_b` | `255` | `0` – `255` | Static color, blue channel. |
| `lightbar_brightness` | `0.80` | `0.00` – `1.00` | Master brightness. |
| `low_battery_warning` | `true` | boolean | Blink red when the battery drops below 20%. |

Modes:

| Key | UI label |
| --- | --- |
| `static` | Static Color |
| `rainbow` | Spectrum Cycle |
| `cyberpunk` | Cyberpunk |
| `breathing` | Breathing |
| `color_pulse` | Color Pulse |
| `fire` | Ember Flame |
| `ocean` | Aurora Wave |
| `sunset` | Synthwave |
| `police` | Strobe |
| `triggers` | Trigger Reactive |
| `rumble_reactive` | Rumble Flash |
| `battery_gauge` | Battery Meter |

## Vibration

| Key | Default | Range | Description |
| --- | --- | --- | --- |
| `rumble_enabled` | `true` | boolean | Master switch for physical rumble. |
| `rumble_strength` | `1.00` | `0.00` – `1.50` | Overall gain applied to both motors. |
| `rumble_heavy_scale` | `1.00` | `0.00` – `1.50` | Gain for the heavy (left) motor. |
| `rumble_light_scale` | `1.00` | `0.00` – `1.50` | Gain for the light (right) motor. |
| `trigger_rumble` | `false` | boolean | Add rumble proportional to L2/R2 pull. |
| `trigger_rumble_scale` | `0.45` | `0.00` – `1.00` | Strength of the trigger effect. |

The test buttons on the **Vibration** page temporarily override game feedback so you can hear each motor on its own.

## Bluetooth

| Key | Default | Range | Description |
| --- | --- | --- | --- |
| `bt_poll_ms` | `4` | `1`, `4`, `8`, `16` | Poll interval encoded in Bluetooth output reports. Lower is snappier but uses more battery. |

## Desktop behavior

| Key | Default | Range | Description |
| --- | --- | --- | --- |
| `minimize_to_tray` | `true` | boolean | Minimize to the notification area instead of the taskbar. |
| `touchpad_mouse` | `false` | boolean | Use the touchpad as a trackpad. |
| `touchpad_sensitivity` | `1.00` | `0.25` – `3.00` | Cursor speed multiplier. |
| `touchpad_tap_click` | `true` | boolean | Tap to click. Turn off to avoid accidental clicks. |

## Button mapping

`button_map` maps a DS4 input to an Xbox target. Defaults:

| DS4 input | Xbox target |
| --- | --- |
| `cross` | `A` |
| `circle` | `B` |
| `square` | `X` |
| `triangle` | `Y` |
| `l1` | `LB` |
| `r1` | `RB` |
| `l3` | `LS` |
| `r3` | `RS` |
| `share` | `Back` |
| `options` | `Start` |
| `ps` | `Guide` |
| `touch_click` | `Back` |
| `dpad_up` | `DPadUp` |
| `dpad_down` | `DPadDown` |
| `dpad_left` | `DPadLeft` |
| `dpad_right` | `DPadRight` |

Valid targets are `A`, `B`, `X`, `Y`, `LB`, `RB`, `LS`, `RS`, `Back`, `Start`, `Guide`, `DPadUp`, `DPadDown`, `DPadLeft`, `DPadRight`, and `Disabled`.

When `touchpad_mouse` is enabled, the physical touchpad click is consumed by the mouse layer and is not sent as **Back**, even if `touch_click` is mapped.

## Built-in presets

| Name | Intended for |
| --- | --- |
| **Default** | General use, 8% deadzone, static blue lightbar. |
| **Shooter Precision** | Tight right-stick deadzone, precision curve, trigger haptics, trigger-reactive lightbar. |
| **Cyberpunk** | Animated lightbar, slightly reduced deadzone. |
| **Racing Haptics** | Precision left curve, zero trigger deadzone, strong trigger haptics, spectrum lightbar. |

## Sharing and backing up

Profiles are portable: copy the `.json` file to another machine's `profiles/` folder and select it from the profile picker. Delete the `profiles/` folder to restore the four built-in presets on the next launch.
