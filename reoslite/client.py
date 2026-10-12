"""Async BLE client for Reos Lite lights, built on bleak (Linux/macOS/Windows)."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Dict, List, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from . import protocol as p

log = logging.getLogger(__name__)

# The app waits ~50 ms between the fragments of a long packet.
_FRAGMENT_DELAY = 0.05


def _is_reos(name: Optional[str]) -> bool:
    return bool(name) and name.strip().upper() in p.DEVICE_NAMES


async def scan(timeout: float = 8.0) -> List[BLEDevice]:
    """Find lights advertising as LITE / LITE1100 / LITESTRIP."""
    found = await BleakScanner.discover(timeout=timeout)
    return [d for d in found if _is_reos(d.name)]


class ReosLite:
    """Connection to one light.

        async with ReosLite("AA:BB:CC:DD:EE:FF") as lite:
            await lite.color(255, 80, 0)
    """

    def __init__(self, address: str, timeout: float = 15.0):
        self.address = address
        self._client = BleakClient(address, timeout=timeout)
        self._reasm = p.Reassembler()
        self._waiters: Dict[int, asyncio.Future] = {}
        self._first_fragment: Dict[int, bytes] = {}
        self._write_response = True
        self._lock = asyncio.Lock()

    # -- connection -------------------------------------------------------

    async def __aenter__(self) -> "ReosLite":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        await self._client.connect()
        cmd_char = self._client.services.get_characteristic(p.CHAR_COMMAND)
        if cmd_char is None:
            raise RuntimeError("0xFFF3 command characteristic not found - is this a Reos Lite?")
        self._write_response = "write" in cmd_char.properties
        try:
            await self._client.start_notify(p.CHAR_NOTIFY, self._on_notify)
        except Exception as e:  # replies are optional for most commands
            log.warning("could not enable notifications on 0xFFF4: %s", e)

    async def disconnect(self) -> None:
        if self._client.is_connected:
            await self._client.disconnect()

    # -- transport --------------------------------------------------------

    def _on_notify(self, _char, data: bytearray) -> None:
        data = bytes(data)
        log.debug("notify %s", data.hex(" "))
        if len(data) >= 3 and data[0] == p.HEADER:
            self._first_fragment[data[2]] = data
        for pkt in self._reasm.feed(data):
            fut = self._waiters.pop(pkt.cmd, None)
            if fut and not fut.done():
                fut.set_result(pkt)

    async def send(self, packet: bytes) -> None:
        """Write a framed packet, split into 20-byte writes like the app."""
        async with self._lock:
            parts = p.chunks(packet)
            for i, part in enumerate(parts):
                log.debug("write %s", part.hex(" "))
                await self._client.write_gatt_char(p.CHAR_COMMAND, part, response=self._write_response)
                if i < len(parts) - 1:
                    await asyncio.sleep(_FRAGMENT_DELAY)

    async def request(self, packet: bytes, reply_cmd: int, timeout: float = 3.0) -> p.Packet:
        fut = asyncio.get_running_loop().create_future()
        self._waiters[reply_cmd] = fut
        self._first_fragment.pop(reply_cmd, None)
        await self.send(packet)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._waiters.pop(reply_cmd, None)
            # Some firmware replies omit/garble the tail; fall back to the
            # first fragment if it carries the payload we need.
            frag = self._first_fragment.get(reply_cmd)
            if frag:
                return p.Packet(cmd=reply_cmd, payload=frag[4:], raw=frag, checksum_ok=False)
            raise

    # -- high level -------------------------------------------------------

    async def sync_time(self, now: Optional[dt.datetime] = None) -> None:
        await self.send(p.sync_time(now))

    async def color(self, r: int, g: int, b: int, brightness: int = p.MAX_BRIGHTNESS,
                    gamma: bool = True) -> None:
        await self.send(p.set_color(r, g, b, brightness, apply_gamma=gamma))

    async def on(self, r: int = 255, g: int = 255, b: int = 255,
                 brightness: int = p.MAX_BRIGHTNESS, speed: int = 5) -> None:
        await self.send(p.turn_on(r, g, b, brightness, speed))

    async def off(self, speed: int = 5) -> None:
        await self.send(p.turn_off(speed))

    async def fade(self, r: int, g: int, b: int, brightness: int = p.MAX_BRIGHTNESS,
                   speed: int = 5) -> None:
        await self.send(p.fade(r, g, b, brightness, speed))

    async def effect(self, kind: p.Effect, r: int = 0, g: int = 0, b: int = 0,
                     brightness: int = 0) -> None:
        await self.send(p.effect(kind, r, g, b, brightness))

    async def flash(self, r: int, g: int, b: int, brightness: int = p.MAX_BRIGHTNESS,
                    mode: int = 5, count: int = 1) -> None:
        await self.send(p.flash(r, g, b, brightness, mode, count))

    async def power_on_behavior(self, last_color: bool) -> None:
        await self.send(p.power_on_behavior(last_color))

    async def timer(self, action: p.TimerAction, at: dt.datetime, r: int = 255, g: int = 255,
                    b: int = 255, brightness: int = p.MAX_BRIGHTNESS) -> None:
        await self.sync_time()
        await asyncio.sleep(_FRAGMENT_DELAY)
        await self.send(p.timer(action, at, r, g, b, brightness))

    async def schedules(self) -> List[p.ScheduleRule]:
        pkt = await self.request(p.schedule_read(), p.Cmd.SCHEDULE_READ, timeout=4.0)
        return p.parse_schedules(pkt)

    async def add_schedule(self, turn_on: bool, hour: int, minute: int, days: int,
                           r: int = 255, g: int = 255, b: int = 255,
                           brightness: int = p.MAX_BRIGHTNESS) -> None:
        await self.sync_time()
        await asyncio.sleep(_FRAGMENT_DELAY)
        await self.send(p.schedule_write(p.ScheduleOp.ADD, 0, True, turn_on, days,
                                         hour, minute, r, g, b, brightness))

    async def update_schedule(self, rule: p.ScheduleRule) -> None:
        r, g, b = rule.color
        await self.send(p.schedule_write(p.ScheduleOp.UPDATE, rule.rule_id, rule.enabled,
                                         rule.turn_on, rule.days, rule.hour, rule.minute,
                                         r, g, b, rule.brightness or p.MAX_BRIGHTNESS))

    async def delete_schedule(self, rule_id: int = 0) -> None:
        await self.send(p.schedule_delete(rule_id))

    async def serial(self) -> str:
        return p.parse_serial(await self.request(p.read_serial(), p.Cmd.READ_SERIAL))

    async def info(self) -> bytes:
        return bytes(await self._client.read_gatt_char(p.CHAR_INFO))

    async def version(self) -> str:
        return p.parse_version(await self.info())

    async def factory_reset(self) -> None:
        await self.send(p.factory_reset())
