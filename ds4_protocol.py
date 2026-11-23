"""DualShock 4 HID input/output protocol and stick math."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import struct
import zlib

SONY_VENDOR_ID = 0x054C
DS4_PRODUCT_IDS: dict[int, str] = {
    0x05C4: "DualShock 4 v1",
    0x09CC: "DualShock 4 v2",
    0x0BA0: "DS4 Wireless Adapter",
}

DS4_INPUT_REPORT_USB = 0x01
DS4_INPUT_REPORT_USB_SIZE = 64
DS4_INPUT_REPORT_BT_MINIMAL_SIZE = 10
DS4_INPUT_REPORT_BT = 0x11
DS4_INPUT_REPORT_BT_SIZE = 78

DS4_OUTPUT_REPORT_USB = 0x05
DS4_OUTPUT_REPORT_USB_SIZE = 32
DS4_OUTPUT_REPORT_BT = 0x11
DS4_OUTPUT_REPORT_BT_SIZE = 78

DS4_FEATURE_REPORT_CALIBRATION = 0x02
DS4_FEATURE_REPORT_CALIBRATION_SIZE = 37

PS_INPUT_CRC32_SEED = b"\xA1"
PS_OUTPUT_CRC32_SEED = b"\xA2"

DS4_OUTPUT_HWCTL_CRC32 = 0x40
DS4_OUTPUT_HWCTL_HID = 0x80

DS4_OUTPUT_VALID_MOTOR = 0x01
DS4_OUTPUT_VALID_LED = 0x02
DS4_OUTPUT_VALID_LED_BLINK = 0x04

DS4_TOUCHPAD_WIDTH = 1920
DS4_TOUCHPAD_HEIGHT = 942


@dataclass(slots=True)
class TouchFinger:
    active: bool = False
    tracking_id: int = 0
    x: int = 0
    y: int = 0


@dataclass(slots=True)
class DS4InputState:
    transport: str = "USB"

    lx: int = 128
    ly: int = 128
    rx: int = 128
    ry: int = 128

    l2: int = 0
    r2: int = 0

    dpad_up: bool = False
    dpad_down: bool = False
    dpad_left: bool = False
    dpad_right: bool = False

    cross: bool = False
    circle: bool = False
    square: bool = False
    triangle: bool = False
    l1: bool = False
    r1: bool = False
    l2_btn: bool = False
    r2_btn: bool = False
    share: bool = False
    options: bool = False
    l3: bool = False
    r3: bool = False
    ps: bool = False
    touch_click: bool = False

    battery_percent: int = 100
    cable_connected: bool = False
    battery_full: bool = False
    dongle_disconnected: bool = False

    gyro_x: int = 0
    gyro_y: int = 0
    gyro_z: int = 0
    accel_x: int = 0
    accel_y: int = 0
    accel_z: int = 0
    sensor_timestamp: int = 0

    touch_1: TouchFinger = field(default_factory=TouchFinger)
    touch_2: TouchFinger = field(default_factory=TouchFinger)


@dataclass(slots=True)
class Xbox360OutputState:
    thumb_lx: int = 0
    thumb_ly: int = 0
    thumb_rx: int = 0
    thumb_ry: int = 0
    left_trigger: int = 0
    right_trigger: int = 0
    buttons: dict[str, bool] = field(default_factory=dict)


def compute_ps_crc32(seed_byte: bytes, payload: bytes | bytearray | memoryview) -> int:
    crc = zlib.crc32(seed_byte)
    return zlib.crc32(payload, crc) & 0xFFFFFFFF


def expected_bt_input_size(report_id: int) -> int:
    if report_id == 0x11:
        return 78
    if 0x12 <= report_id <= 0x18:
        return 78 + (report_id - 0x11) * 64
    if report_id == 0x19:
        return 547
    return 0


def verify_bt_input_crc32(raw: bytes | bytearray) -> bool:
    if not raw:
        return False
    expected_len = expected_bt_input_size(raw[0])
    if expected_len <= 0 or len(raw) < expected_len:
        return False
    packet = memoryview(raw)[:expected_len]
    received_crc = struct.unpack_from("<I", packet, expected_len - 4)[0]
    calculated_crc = compute_ps_crc32(PS_INPUT_CRC32_SEED, packet[:-4])
    return received_crc == calculated_crc


def _decode_dpad(hat: int) -> tuple[bool, bool, bool, bool]:
    up = hat in (0, 1, 7)
    right = hat in (1, 2, 3)
    down = hat in (3, 4, 5)
    left = hat in (5, 6, 7)
    return up, down, left, right


def _decode_touch_finger(buf: memoryview, offset: int) -> TouchFinger:
    if offset + 4 > len(buf):
        return TouchFinger()
    b0 = buf[offset]
    active = (b0 & 0x80) == 0
    tracking_id = b0 & 0x7F
    if not active:
        return TouchFinger(active=False, tracking_id=tracking_id, x=0, y=0)
    b1 = buf[offset + 1]
    b2 = buf[offset + 2]
    b3 = buf[offset + 3]
    x = b1 | ((b2 & 0x0F) << 8)
    y = ((b2 & 0xF0) >> 4) | (b3 << 4)
    return TouchFinger(active=True, tracking_id=tracking_id, x=x, y=y)


def _decode_battery(status0: int) -> tuple[int, bool, bool]:
    cable = bool(status0 & 0x10)
    raw_level = status0 & 0x0F
    if cable:
        if raw_level == 11:
            return 100, True, True
        if raw_level <= 10:
            return min(100, raw_level * 10), True, False
        return 0, True, False
    percent = min(100, (raw_level + 1) * 10)
    return percent, False, percent >= 100


def parse_ds4_input_report(
    raw: bytes | bytearray,
    *,
    is_bluetooth_hint: bool = False,
    strict_bt_crc: bool = True,
) -> DS4InputState | None:
    """Parse USB 0x01 (64B), minimal BT 0x01 (10B), or extended BT 0x11..0x19 reports."""
    if not raw or len(raw) < 10:
        return None

    buf = memoryview(raw)
    report_id = buf[0]

    if 0x11 <= report_id <= 0x19:
        expected_len = expected_bt_input_size(report_id)
        if len(buf) < expected_len:
            return None
        if (buf[1] & 0x80) == 0:
            return None
        if strict_bt_crc and not verify_bt_input_crc32(raw):
            return None
        common_offset = 3
        touch_count_offset = 35
        touch_first_offset = 37
        transport = "Bluetooth"
        has_extended = True
    elif report_id == DS4_INPUT_REPORT_USB:
        common_offset = 1
        has_extended = len(buf) >= DS4_INPUT_REPORT_USB_SIZE
        transport = "Bluetooth" if is_bluetooth_hint else "USB"
        touch_count_offset = 33
        touch_first_offset = 35
    else:
        return None

    c = common_offset
    btn0 = buf[c + 4]
    btn1 = buf[c + 5]
    btn2 = buf[c + 6]
    dpad_up, dpad_down, dpad_left, dpad_right = _decode_dpad(btn0 & 0x0F)

    state = DS4InputState(
        transport=transport,
        lx=buf[c + 0],
        ly=buf[c + 1],
        rx=buf[c + 2],
        ry=buf[c + 3],
        dpad_up=dpad_up,
        dpad_down=dpad_down,
        dpad_left=dpad_left,
        dpad_right=dpad_right,
        square=bool(btn0 & 0x10),
        cross=bool(btn0 & 0x20),
        circle=bool(btn0 & 0x40),
        triangle=bool(btn0 & 0x80),
        l1=bool(btn1 & 0x01),
        r1=bool(btn1 & 0x02),
        l2_btn=bool(btn1 & 0x04),
        r2_btn=bool(btn1 & 0x08),
        share=bool(btn1 & 0x10),
        options=bool(btn1 & 0x20),
        l3=bool(btn1 & 0x40),
        r3=bool(btn1 & 0x80),
        ps=bool(btn2 & 0x01),
        touch_click=bool(btn2 & 0x02),
        l2=buf[c + 7],
        r2=buf[c + 8],
    )

    if has_extended and len(buf) >= c + 32:
        state.sensor_timestamp = struct.unpack_from("<H", buf, c + 9)[0]
        gx, gy, gz, ax, ay, az = struct.unpack_from("<hhhhhh", buf, c + 12)
        state.gyro_x, state.gyro_y, state.gyro_z = gx, gy, gz
        state.accel_x, state.accel_y, state.accel_z = ax, ay, az

        status0 = buf[c + 29]
        status1 = buf[c + 30]
        batt_pct, cable, full = _decode_battery(status0)
        state.battery_percent = batt_pct
        state.cable_connected = cable
        state.battery_full = full
        state.dongle_disconnected = bool(status1 & 0x04)

        if len(buf) >= touch_first_offset + 8 and buf[touch_count_offset] > 0:
            state.touch_1 = _decode_touch_finger(buf, touch_first_offset)
            state.touch_2 = _decode_touch_finger(buf, touch_first_offset + 4)

    return state


def apply_curve(magnitude: float, curve_mode: str) -> float:
    m = max(0.0, min(1.0, magnitude))
    if curve_mode == "precision":
        return math.pow(m, 1.65)
    if curve_mode == "aggressive":
        return math.pow(m, 0.72)
    return m


def process_stick_pair(
    raw_x: int,
    raw_y: int,
    *,
    deadzone: float = 0.08,
    anti_deadzone: float = 0.0,
    sensitivity: float = 1.0,
    curve_mode: str = "linear",
    invert_x: bool = False,
    invert_y: bool = False,
) -> tuple[int, int]:
    """Convert raw DS4 stick bytes (0..255) into signed 16-bit XInput axes (-32768..32767)."""
    nx = (raw_x - 128.0) / 127.0 if raw_x >= 128 else (raw_x - 128.0) / 128.0
    ny = -((raw_y - 128.0) / 127.0 if raw_y >= 128 else (raw_y - 128.0) / 128.0)

    nx = max(-1.0, min(1.0, nx))
    ny = max(-1.0, min(1.0, ny))

    if invert_x:
        nx = -nx
    if invert_y:
        ny = -ny

    dz = max(0.0, min(0.85, deadzone))
    anti_dz = max(0.0, min(0.50, anti_deadzone))
    sens = max(0.20, min(2.50, sensitivity))

    mag = math.hypot(nx, ny)
    if mag <= dz or mag == 0.0:
        return 0, 0

    scaled = min(1.0, (mag - dz) / (1.0 - dz))
    curved = apply_curve(scaled, curve_mode) * sens
    if anti_dz > 0.0 and curved > 0.0:
        curved = anti_dz + curved * (1.0 - anti_dz)
    final_mag = min(1.0, curved)

    out_x = (nx / mag) * final_mag
    out_y = (ny / mag) * final_mag

    def _to_int16(val: float) -> int:
        if val >= 0.0:
            return min(32767, int(round(val * 32767.0)))
        return max(-32768, int(round(val * 32768.0)))

    return _to_int16(out_x), _to_int16(out_y)


def process_trigger(raw_val: int, deadzone: float = 0.02) -> int:
    dz_int = int(round(max(0.0, min(0.80, deadzone)) * 255.0))
    if raw_val <= dz_int or dz_int >= 255:
        return 0
    scaled = (raw_val - dz_int) / (255.0 - dz_int)
    return max(0, min(255, int(round(scaled * 255.0))))


def build_usb_output_report(
    *,
    red: int = 0,
    green: int = 64,
    blue: int = 255,
    small_rumble: int = 0,
    large_rumble: int = 0,
    flash_on: int = 0,
    flash_off: int = 0,
) -> bytes:
    """Build 32-byte USB output report 0x05 matching Sony hid-playstation layout."""
    buf = bytearray(DS4_OUTPUT_REPORT_USB_SIZE)
    buf[0] = DS4_OUTPUT_REPORT_USB
    buf[1] = DS4_OUTPUT_VALID_MOTOR | DS4_OUTPUT_VALID_LED | DS4_OUTPUT_VALID_LED_BLINK
    buf[2] = 0x00
    buf[3] = 0x00
    buf[4] = max(0, min(255, int(small_rumble)))
    buf[5] = max(0, min(255, int(large_rumble)))
    buf[6] = max(0, min(255, int(red)))
    buf[7] = max(0, min(255, int(green)))
    buf[8] = max(0, min(255, int(blue)))
    buf[9] = max(0, min(255, int(flash_on)))
    buf[10] = max(0, min(255, int(flash_off)))
    return bytes(buf)


def build_bt_output_report(
    *,
    red: int = 0,
    green: int = 64,
    blue: int = 255,
    small_rumble: int = 0,
    large_rumble: int = 0,
    flash_on: int = 0,
    flash_off: int = 0,
    bt_poll_ms: int = 4,
) -> bytes:
    """Build 78-byte Bluetooth output report 0x11 signed with trailing CRC-32 (seed 0xA2)."""
    poll_bits = max(0, min(0x3E, int(bt_poll_ms)))
    buf = bytearray(DS4_OUTPUT_REPORT_BT_SIZE)
    buf[0] = DS4_OUTPUT_REPORT_BT
    buf[1] = DS4_OUTPUT_HWCTL_HID | DS4_OUTPUT_HWCTL_CRC32 | poll_bits
    buf[2] = 0x00

    buf[3] = DS4_OUTPUT_VALID_MOTOR | DS4_OUTPUT_VALID_LED | DS4_OUTPUT_VALID_LED_BLINK
    buf[4] = 0x00
    buf[5] = 0x00
    buf[6] = max(0, min(255, int(small_rumble)))
    buf[7] = max(0, min(255, int(large_rumble)))
    buf[8] = max(0, min(255, int(red)))
    buf[9] = max(0, min(255, int(green)))
    buf[10] = max(0, min(255, int(blue)))
    buf[11] = max(0, min(255, int(flash_on)))
    buf[12] = max(0, min(255, int(flash_off)))

    crc = compute_ps_crc32(PS_OUTPUT_CRC32_SEED, memoryview(buf)[:74])
    struct.pack_into("<I", buf, 74, crc)
    return bytes(buf)
