"""Pure packet encoding/decoding for the Cube26 / iota Reos Lite BLE protocol.

No Bluetooth dependency lives here, so this module can be unit-tested and
reused from any BLE stack. See docs/PROTOCOL.md for the full write-up.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, List, Optional, Sequence

# --------------------------------------------------------------------------
# GATT layout (TI CC254x "SimpleBLEPeripheral" style 0xFFF0 service)
# --------------------------------------------------------------------------


def _uuid16(short: int) -> str:
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


SERVICE_UUID = _uuid16(0xFFF0)
CHAR_DEVICE_NAME = _uuid16(0x2A00)
CHAR_INFO = _uuid16(0xFFF1)  # read: firmware version block; write: OTA data
CHAR_COMMAND = _uuid16(0xFFF3)  # write: all command packets
CHAR_NOTIFY = _uuid16(0xFFF4)  # notify: replies from the light

# Advertised names the app accepts (case-insensitive).
DEVICE_NAMES = ("LITE", "LITE1100", "LITESTRIP")

# The app always splits writes into 20-byte chunks (default ATT MTU).
CHUNK_SIZE = 20

HEADER = 0x0F
TRAILER = b"\xff\xff"

# Brightness byte used everywhere by the app; 200 (0xC8) is "full".
MAX_BRIGHTNESS = 200


class Cmd(IntEnum):
    SYNC_TIME = 0x01
    SET_COLOR = 0x03
    COLOR_REPORT = 0x04  # notification only
    POWER_ON_BEHAVIOR = 0x05
    TIMER = 0x09
    CHECK = 0x0A
    FACTORY_RESET = 0x0B
    FLASH = 0x0C
    FADE = 0x0D
    READ_SERIAL = 0x11
    EFFECT = 0x12
    SCHEDULE_WRITE = 0x13
    SCHEDULE_READ = 0x14
    FIRMWARE = 0xFF


class Effect(IntEnum):
    STOP = 0
    KALEIDOSCOPE = 2
    STROBE = 3
    CANDLE = 4


class ScheduleOp(IntEnum):
    ADD = 0
    UPDATE = 1
    DELETE = 2


class TimerAction(IntEnum):
    CANCEL = 0
    ON = 1
    OFF = 2


# Days-of-week bitmask used by schedules (bit0 = Sunday ... bit6 = Saturday).
SUNDAY, MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY = (1 << i for i in range(7))
EVERY_DAY = 0x7F
_DAY_NAMES = ["su", "mo", "tu", "we", "th", "fr", "sa"]


def days_mask(days: Iterable[str]) -> int:
    """Turn e.g. ["mo", "wed", "Friday"] into the schedule bitmask."""
    mask = 0
    for d in days:
        key = d.strip().lower()[:2]
        if key not in _DAY_NAMES:
            raise ValueError(f"unknown day: {d!r}")
        mask |= 1 << _DAY_NAMES.index(key)
    return mask


def days_from_mask(mask: int) -> List[str]:
    return [_DAY_NAMES[i] for i in range(7) if mask & (1 << i)]


# Gamma table the app applies to R/G/B before sending a "colour wheel" colour
# (com.cube26.iota.smartlite.util.g.Y).
GAMMA = (
    [0] * 28 + [1] * 13 + [2] * 8 + [3] * 7 + [4] * 5 + [5] * 4 + [6] * 4 + [7] * 4
    + [8, 8, 8, 9, 9, 9, 10, 10, 10, 11, 11, 11, 12, 12, 13, 13, 13, 14, 14, 15, 15,
       16, 16, 17, 17, 18, 18, 19, 19, 20, 20, 21, 21, 22, 22, 23, 24, 24, 25, 25, 26,
       27, 27, 28, 29, 29, 30, 31, 32, 32, 33, 34, 35, 35, 36, 37, 38, 39, 39, 40, 41,
       42, 43, 44, 45, 46, 47, 48, 49, 50, 50, 51, 52, 54, 55, 56, 57, 58, 59, 60, 61,
       62, 63, 64, 66, 67, 68, 69, 70, 72, 73, 74, 75, 77, 78, 79, 81, 82, 83, 85, 86,
       87, 89, 90, 92, 93, 95, 96, 98, 99, 101, 102, 104, 105, 107, 109, 110, 112, 114,
       115, 117, 119, 120, 122, 124, 126, 127, 129, 131, 133, 135, 137, 138, 140, 142,
       144, 146, 148, 150, 152, 154, 156, 158, 160, 162, 164, 167, 169, 171, 173, 175,
       177, 180, 182, 184, 186, 189, 191, 193, 196, 198, 200, 203, 205, 208, 210, 213,
       215, 218, 220, 223, 225, 228, 231, 233, 236, 239, 241, 244, 247, 249, 252, 255]
)
assert len(GAMMA) == 256


def gamma(v: int) -> int:
    return GAMMA[_u8(v)]


# --------------------------------------------------------------------------
# Framing
# --------------------------------------------------------------------------


def _u8(v: int) -> int:
    if not 0 <= int(v) <= 255:
        raise ValueError(f"byte out of range: {v}")
    return int(v)


def checksum(body: Sequence[int]) -> int:
    """body = cmd, 0x00, payload...  ->  (1 + sum(body)) & 0xFF"""
    return (1 + sum(body)) & 0xFF


def build_packet(cmd: int, payload: Sequence[int] = ()) -> bytes:
    """Frame: 0F LEN CMD 00 PAYLOAD... CHK FF FF

    LEN counts CMD through CHK inclusive (i.e. total length - 4).
    """
    body = [_u8(cmd), 0x00] + [_u8(b) for b in payload]
    pkt = bytes([HEADER, len(body) + 1]) + bytes(body) + bytes([checksum(body)]) + TRAILER
    return pkt


def chunks(packet: bytes, size: int = CHUNK_SIZE) -> List[bytes]:
    return [packet[i:i + size] for i in range(0, len(packet), size)]


@dataclass
class Packet:
    cmd: int
    payload: bytes
    raw: bytes
    checksum_ok: bool


def parse_packet(raw: bytes) -> Packet:
    if len(raw) < 7 or raw[0] != HEADER:
        raise ValueError(f"not a Reos packet: {raw.hex()}")
    total = raw[1] + 4
    if len(raw) < total:
        raise ValueError(f"truncated packet ({len(raw)}/{total}): {raw.hex()}")
    raw = raw[:total]
    body = raw[2:total - 3]
    chk = raw[total - 3]
    return Packet(cmd=body[0], payload=bytes(body[2:]), raw=bytes(raw),
                  checksum_ok=checksum(body) == chk)


class Reassembler:
    """Joins notification fragments back into whole packets.

    The light sends replies longer than 20 bytes (serial number, schedule
    list) as several notifications; the header's LEN byte tells us when the
    packet is complete.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> List[Packet]:
        if not self._buf and (not data or data[0] != HEADER):
            return []  # stray fragment
        self._buf += data
        out: List[Packet] = []
        while len(self._buf) >= 2:
            total = self._buf[1] + 4
            if len(self._buf) < total:
                break
            out.append(parse_packet(bytes(self._buf[:total])))
            del self._buf[:total]
            if self._buf and self._buf[0] != HEADER:
                self._buf.clear()
        return out


# --------------------------------------------------------------------------
# Command builders (byte layouts mirror com.cube26.iota.smartlite.util.g)
# --------------------------------------------------------------------------


def _date_bytes(t: _dt.datetime) -> List[int]:
    return [t.second, t.minute, t.hour, t.day, t.month, t.year >> 8, t.year & 0xFF]


def sync_time(now: Optional[_dt.datetime] = None) -> bytes:
    """Set the light's RTC. The app sends this before timers/schedules."""
    now = now or _dt.datetime.now()
    return build_packet(Cmd.SYNC_TIME, _date_bytes(now) + [0, 0])


def set_color(r: int, g: int, b: int, brightness: int = MAX_BRIGHTNESS,
              apply_gamma: bool = True) -> bytes:
    """Static colour, as sent by the colour wheel / preset swatches.

    brightness: 0..200. The app gamma-corrects R/G/B through GAMMA first.
    """
    if apply_gamma:
        r, g, b = gamma(r), gamma(g), gamma(b)
    return build_packet(Cmd.SET_COLOR, [r, g, b, brightness, 0, 0, 1, 0, 0, 0])


def set_color_raw(r: int, g: int, b: int, brightness: int, b8: int = 0,
                  b9: int = 0, b10: int = 1) -> bytes:
    """SET_COLOR with the three trailing flag bytes exposed.

    Seen in the app: (0,0,1) colour wheel; (0,0,0) SMS alert;
    (100,1,1) white in some modes; (n,1,1) from an unused helper.
    """
    return build_packet(Cmd.SET_COLOR, [r, g, b, brightness, b8, b9, b10, 0, 0, 0])


def fade(r: int, g: int, b: int, brightness: int = MAX_BRIGHTNESS, speed: int = 5) -> bytes:
    """Smooth transition to a colour. The app uses this for on/off (speed 5)
    and for restoring colour after an alert (speed 2)."""
    return build_packet(Cmd.FADE, [r, g, b, brightness, speed, 0, 0])


def turn_off(speed: int = 5) -> bytes:
    return fade(0, 0, 0, 0, speed)


def turn_on(r: int = 255, g: int = 255, b: int = 255,
            brightness: int = MAX_BRIGHTNESS, speed: int = 5) -> bytes:
    return fade(r, g, b, brightness, speed)


def effect(kind: Effect, r: int = 0, g: int = 0, b: int = 0, brightness: int = 0) -> bytes:
    """Built-in firmware effects. Effect.STOP cancels a running one.

    The app sends strobe as (255,255,255,200) and kaleidoscope/candle as zeros.
    """
    return build_packet(Cmd.EFFECT, [int(kind), 2, r, g, b, brightness, 0, 0])


def flash(r: int, g: int, b: int, brightness: int = MAX_BRIGHTNESS,
          mode: int = 5, count: int = 1) -> bytes:
    """Notification flash. App uses (mode=1,count=1) "single flash",
    (mode=5,count=1) "flash alert", (mode=5,count=5) the drawer preview.
    The app gamma-corrects the colour first."""
    return build_packet(Cmd.FLASH, [mode, 0, count, gamma(r), gamma(g), gamma(b), brightness, 0, 0])


def power_on_behavior(last_color: bool) -> bytes:
    """What the light shows when mains power is applied:
    last colour set (True) or default white (False)."""
    return build_packet(Cmd.POWER_ON_BEHAVIOR, [0, 1, 0 if last_color else 1, 1])


def timer(action: TimerAction, at: _dt.datetime, r: int = 0, g: int = 0, b: int = 0,
          brightness: int = MAX_BRIGHTNESS) -> bytes:
    """One-shot sleep/wake timer at an absolute time (light's clock).

    The app sends the last-used colour + brightness 200 with it; for
    TimerAction.ON that is the colour the light comes on with.
    """
    return build_packet(Cmd.TIMER, [int(action)] + _date_bytes(at) + [r, g, b, brightness])


def schedule_write(op: ScheduleOp, rule_id: int, enabled: bool, turn_on: bool, days: int,
                   hour: int, minute: int, r: int = 0, g: int = 0, b: int = 0,
                   brightness: int = MAX_BRIGHTNESS,
                   created: Optional[_dt.date] = None) -> bytes:
    """Add / update / delete a repeating on/off rule (max 4 per light).

    For ADD the app passes rule_id=0 and the light assigns one (read it back
    with schedule_read()). For an "off" rule the app zeroes colour+brightness.
    days: bitmask, bit0=Sunday..bit6=Saturday.
    """
    created = created or _dt.date.today()
    if not turn_on:
        r = g = b = brightness = 0
    return build_packet(Cmd.SCHEDULE_WRITE, [
        int(op), rule_id, int(enabled), int(turn_on), days & 0xFF,
        created.year % 2000, created.month, created.day,
        hour, minute, 0, 0, r, g, b, brightness,
    ])


def schedule_delete(rule_id: int = 0) -> bytes:
    """The app always sends rule_id=0 here (see PROTOCOL.md)."""
    return build_packet(Cmd.SCHEDULE_WRITE, [ScheduleOp.DELETE, rule_id, 1, 1] + [0] * 11 + [MAX_BRIGHTNESS])


def schedule_read(arg: int = 0) -> bytes:
    return build_packet(Cmd.SCHEDULE_READ, [arg, 0, 0])


def read_serial() -> bytes:
    return build_packet(Cmd.READ_SERIAL, [0, 0])


def check() -> bytes:
    return build_packet(Cmd.CHECK, [0, 0])


def factory_reset() -> bytes:
    return build_packet(Cmd.FACTORY_RESET, [0, 0])


# --------------------------------------------------------------------------
# Reply decoding
# --------------------------------------------------------------------------


@dataclass
class ScheduleRule:
    rule_id: int
    enabled: bool
    turn_on: bool
    days: int
    created: Optional[_dt.date]
    hour: int
    minute: int
    color: tuple
    brightness: int

    def __str__(self) -> str:
        what = f"ON rgb{self.color} bri={self.brightness}" if self.turn_on else "OFF"
        days = ",".join(days_from_mask(self.days)) or "once"
        state = "enabled" if self.enabled else "disabled"
        return f"#{self.rule_id} {self.hour:02d}:{self.minute:02d} {days} -> {what} ({state})"


def parse_schedules(pkt: Packet) -> List[ScheduleRule]:
    p = pkt.payload
    count = p[0] if p else 0
    rules = []
    for i in range(count):
        rec = p[1 + 15 * i: 1 + 15 * (i + 1)]
        if len(rec) < 15:
            break
        try:
            created = _dt.date(2000 + rec[4], rec[5], rec[6])
        except ValueError:
            created = None
        rules.append(ScheduleRule(rule_id=rec[0], enabled=bool(rec[1]), turn_on=bool(rec[2]),
                                  days=rec[3], created=created, hour=rec[7], minute=rec[8],
                                  color=(rec[11], rec[12], rec[13]), brightness=rec[14]))
    return rules


def parse_serial(pkt: Packet) -> str:
    return pkt.payload[:16].decode("ascii", "replace").rstrip("\x00 ")


def parse_color_report(pkt: Packet) -> tuple:
    return tuple(pkt.payload[:3])


def parse_version(info: bytes) -> str:
    """FFF1 read value: firmware version is bytes 11 (major) and 12 (minor)."""
    return "%d.%02d" % (info[11], info[12])
