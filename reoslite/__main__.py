"""Command line: python -m reoslite --help"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import re
import sys
from typing import List, Tuple

from . import protocol as p

NAMED = {
    "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255), "white": (255, 255, 255),
    "yellow": (255, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
    "orange": (255, 165, 0), "purple": (128, 0, 128), "pink": (255, 105, 180),
    "warm": (255, 180, 80),
}


def parse_color(s: str) -> Tuple[int, int, int]:
    s = s.strip().lower()
    if s in NAMED:
        return NAMED[s]
    m = re.fullmatch(r"#?([0-9a-f]{6})", s)
    if m:
        v = int(m.group(1), 16)
        return (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
    parts = [int(x) for x in re.split(r"[ ,]+", s) if x]
    if len(parts) == 3 and all(0 <= x <= 255 for x in parts):
        return tuple(parts)  # type: ignore[return-value]
    raise argparse.ArgumentTypeError(f"bad colour {s!r} (use name, #rrggbb or r,g,b)")


def parse_hhmm(s: str) -> Tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def parse_when(s: str) -> dt.datetime:
    """'HH:MM' (next occurrence) or a duration like '90s', '30m', '1h30m'."""
    now = dt.datetime.now().replace(microsecond=0)
    if ":" in s:
        h, m = parse_hhmm(s)
        t = now.replace(hour=h, minute=m, second=0)
        return t if t > now else t + dt.timedelta(days=1)
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", s)
    if not m or not any(m.groups()):
        raise argparse.ArgumentTypeError(f"bad time {s!r}")
    h, mi, se = (int(x or 0) for x in m.groups())
    return now + dt.timedelta(hours=h, minutes=mi, seconds=se)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="reoslite", description="Control iota / Cube26 Reos Lite BLE lights")
    ap.add_argument("-a", "--address", action="append", default=[],
                    help="light MAC (or CoreBluetooth UUID on macOS); repeatable. Default: first light found")
    ap.add_argument("--all", action="store_true", help="act on every light found by a scan")
    ap.add_argument("-v", "--verbose", action="store_true", help="log raw packets")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="list nearby lights")

    s = sub.add_parser("on", help="fade on")
    s.add_argument("color", nargs="?", type=parse_color, default=(255, 255, 255))
    s.add_argument("-b", "--brightness", type=int, default=p.MAX_BRIGHTNESS, help="0-200")

    sub.add_parser("off", help="fade off")

    s = sub.add_parser("color", help="set a static colour")
    s.add_argument("color", type=parse_color)
    s.add_argument("-b", "--brightness", type=int, default=p.MAX_BRIGHTNESS, help="0-200")
    s.add_argument("--no-gamma", action="store_true", help="send raw RGB (app gamma-corrects)")

    s = sub.add_parser("effect", help="run a built-in effect")
    s.add_argument("name", choices=[e.name.lower() for e in p.Effect])

    s = sub.add_parser("flash", help="notification flash")
    s.add_argument("color", type=parse_color)
    s.add_argument("--mode", type=int, default=5, help="1=single flash, 5=flash alert")
    s.add_argument("--count", type=int, default=1)

    s = sub.add_parser("power-on", help="what to show when mains power returns")
    s.add_argument("mode", choices=["last", "white"])

    s = sub.add_parser("timer", help="one-shot on/off timer, e.g. 'timer off 30m' or 'timer on 07:00'")
    s.add_argument("action", choices=["on", "off", "cancel"])
    s.add_argument("when", nargs="?", type=parse_when, default=None)
    s.add_argument("-c", "--color", type=parse_color, default=(255, 255, 255))

    s = sub.add_parser("schedule", help="repeating on/off rules stored in the light (max 4)")
    ss = s.add_subparsers(dest="scmd", required=True)
    ss.add_parser("list")
    sa = ss.add_parser("add")
    sa.add_argument("action", choices=["on", "off"])
    sa.add_argument("time", type=parse_hhmm, help="HH:MM")
    sa.add_argument("-d", "--days", default="su,mo,tu,we,th,fr,sa", help="comma list, e.g. mo,tu,we")
    sa.add_argument("-c", "--color", type=parse_color, default=(255, 255, 255))
    sa.add_argument("-b", "--brightness", type=int, default=p.MAX_BRIGHTNESS)
    for name in ("enable", "disable"):
        se = ss.add_parser(name)
        se.add_argument("id", type=int)
    sd = ss.add_parser("delete")
    sd.add_argument("id", type=int, nargs="?", default=0)

    sub.add_parser("info", help="firmware version, serial number, raw 0xFFF1")
    sub.add_parser("sync-time", help="set the light's clock to now")
    sub.add_parser("factory-reset", help="reset the light")

    s = sub.add_parser("raw", help="send any command: raw CMD [payload bytes...] (hex)")
    s.add_argument("command")
    s.add_argument("payload", nargs="*")
    s.add_argument("--wait", action="store_true", help="wait for a reply with the same command")
    return ap


async def run_one(addr: str, args) -> None:
    from .client import ReosLite

    async with ReosLite(addr) as lite:
        c = args.cmd
        if c == "on":
            await lite.on(*args.color, brightness=args.brightness)
        elif c == "off":
            await lite.off()
        elif c == "color":
            await lite.color(*args.color, brightness=args.brightness, gamma=not args.no_gamma)
        elif c == "effect":
            kind = p.Effect[args.name.upper()]
            if kind == p.Effect.STROBE:
                await lite.effect(kind, 255, 255, 255, 200)
            else:
                await lite.effect(kind)
        elif c == "flash":
            await lite.flash(*args.color, mode=args.mode, count=args.count)
        elif c == "power-on":
            await lite.power_on_behavior(args.mode == "last")
        elif c == "timer":
            action = p.TimerAction[args.action.upper()]
            when = args.when or dt.datetime.now()
            await lite.timer(action, when, *args.color)
            print(f"{addr}: {args.action} at {when:%Y-%m-%d %H:%M:%S}")
        elif c == "schedule":
            await schedule_cmd(lite, args)
        elif c == "info":
            info = await lite.info()
            print(f"{addr}: FFF1 = {info.hex(' ')}")
            if len(info) >= 13:
                print(f"  firmware: {p.parse_version(info)}")
            try:
                print(f"  serial:   {await lite.serial()}")
            except asyncio.TimeoutError:
                print("  serial:   (no reply)")
        elif c == "sync-time":
            await lite.sync_time()
        elif c == "factory-reset":
            await lite.factory_reset()
        elif c == "raw":
            cmd = int(args.command, 16)
            pkt = p.build_packet(cmd, [int(x, 16) for x in args.payload])
            print("->", pkt.hex(" "))
            if args.wait:
                reply = await lite.request(pkt, cmd)
                print("<-", reply.raw.hex(" "))
            else:
                await lite.send(pkt)
        await asyncio.sleep(0.2)  # let the last write flush before disconnecting


async def schedule_cmd(lite, args) -> None:
    if args.scmd == "list":
        rules = await lite.schedules()
        print(f"{lite.address}: {len(rules)} rule(s)")
        for r in rules:
            print("  ", r)
    elif args.scmd == "add":
        h, m = args.time
        days = p.days_mask(args.days.split(","))
        await lite.add_schedule(args.action == "on", h, m, days, *args.color, brightness=args.brightness)
        await asyncio.sleep(0.3)
        for r in await lite.schedules():
            print("  ", r)
    elif args.scmd in ("enable", "disable"):
        rules = {r.rule_id: r for r in await lite.schedules()}
        if args.id not in rules:
            sys.exit(f"no rule #{args.id}")
        rule = rules[args.id]
        rule.enabled = args.scmd == "enable"
        await lite.update_schedule(rule)
    elif args.scmd == "delete":
        await lite.delete_schedule(args.id)


async def main_async(args) -> int:
    from .client import scan

    addrs: List[str] = list(args.address)
    if args.cmd == "scan" or not addrs:
        found = await scan()
        if args.cmd == "scan":
            for d in found:
                print(f"{d.address}  {d.name}")
            if not found:
                print("no lights found (are they powered and not connected to another phone?)")
            return 0
        if not found:
            print("no lights found", file=sys.stderr)
            return 1
        addrs = [d.address for d in found] if args.all else [found[0].address]

    rc = 0
    for addr in addrs:
        try:
            await run_one(addr, args)
        except Exception as e:  # keep going with the other lights
            print(f"{addr}: {type(e).__name__}: {e}", file=sys.stderr)
            rc = 1
    return rc


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if not args.verbose:
        logging.getLogger("bleak").setLevel(logging.WARNING)
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
