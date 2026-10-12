"""Packets built by reoslite.protocol must match the byte arrays the original
Android app (Reos Lite 2.8, com.cube26.iota.smartlite) constructs.

Each `java(...)` call below is a literal copy of a `new byte[]{...}` from the
decompiled app, with the checksum filled in the same way the app does it.
"""

import datetime as dt
import unittest

from reoslite import protocol as p


def java(arr, chk_index):
    """Mimic the app: sum signed bytes [2, chk_index) starting from 1."""
    b = [x & 0xFF for x in arr]
    s = 1
    for i in range(2, chk_index):
        s += arr[i]
    b[chk_index] = s & 0xFF
    return bytes(b)


NOW = dt.datetime(2017, 6, 15, 21, 34, 56)


class TestFraming(unittest.TestCase):
    def test_read_serial_matches_app(self):
        # util/a.java c(): {15, 5, 17, 0, 0, 0, 18, -1, -1}
        self.assertEqual(p.read_serial(), bytes([15, 5, 17, 0, 0, 0, 18, 255, 255]))

    def test_roundtrip(self):
        pkt = p.parse_packet(p.set_color(10, 20, 30))
        self.assertEqual(pkt.cmd, p.Cmd.SET_COLOR)
        self.assertTrue(pkt.checksum_ok)

    def test_chunks(self):
        sched = p.schedule_write(p.ScheduleOp.ADD, 0, True, True, p.EVERY_DAY, 7, 0)
        self.assertEqual([len(c) for c in p.chunks(sched)], [20, 3])


class TestCommandsMatchApp(unittest.TestCase):
    def test_sync_time(self):
        c = NOW
        exp = java([15, 12, 1, 0, c.second, c.minute, c.hour, c.day, c.month,
                    c.year // 256, c.year % 256, 0, 0, 0, -1, -1], 13)
        self.assertEqual(p.sync_time(NOW), exp)

    def test_wheel_color(self):
        r, g, b, bri = 255, 128, 3, 150
        exp = java([15, 13, 3, 0, p.GAMMA[r], p.GAMMA[g], p.GAMMA[b], bri,
                    0, 0, 1, 0, 0, 0, 0, -1, -1], 14)
        self.assertEqual(p.set_color(r, g, b, bri), exp)

    def test_off(self):
        exp = java([15, 10, 13, 0, 0, 0, 0, 0, 5, 0, 0, 0, -1, -1], 11)
        self.assertEqual(p.turn_off(), exp)

    def test_on(self):
        exp = java([15, 10, 13, 0, 255, 255, 255, -56, 5, 0, 0, 0, -1, -1], 11)
        self.assertEqual(p.turn_on(), exp)

    def test_effects(self):
        self.assertEqual(p.effect(p.Effect.CANDLE),
                         java([15, 11, 18, 0, 4, 2, 0, 0, 0, 0, 0, 0, 0, -1, -1], 12))
        self.assertEqual(p.effect(p.Effect.KALEIDOSCOPE),
                         java([15, 11, 18, 0, 2, 2, 0, 0, 0, 0, 0, 0, 0, -1, -1], 12))
        self.assertEqual(p.effect(p.Effect.STROBE, 255, 255, 255, 200),
                         java([15, 11, 18, 0, 3, 2, -1, -1, -1, -56, 0, 0, 0, -1, -1], 12))
        self.assertEqual(p.effect(p.Effect.STOP),
                         java([15, 11, 18, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, -1, -1], 12))

    def test_flash(self):
        r, g, b = 200, 10, 90
        exp = java([15, 12, 12, 0, 5, 0, 1, p.GAMMA[r], p.GAMMA[g], p.GAMMA[b], 200,
                    0, 0, 0, -1, -1], 13)
        self.assertEqual(p.flash(r, g, b, 200, mode=5, count=1), exp)

    def test_power_on_behavior(self):
        self.assertEqual(p.power_on_behavior(last_color=False),
                         java([15, 7, 5, 0, 0, 1, 1, 1, 0, -1, -1], 8))
        self.assertEqual(p.power_on_behavior(last_color=True),
                         java([15, 7, 5, 0, 0, 1, 0, 1, 0, -1, -1], 8))

    def test_timer(self):
        c = NOW
        exp = java([15, 15, 9, 0, 2, c.second, c.minute, c.hour, c.day, c.month,
                    c.year // 256, c.year % 256, 1, 2, 3, -56, 0, -1, -1], 16)
        self.assertEqual(p.timer(p.TimerAction.OFF, NOW, 1, 2, 3), exp)

    def test_schedule_add_on(self):
        d = NOW.date()
        days = p.MONDAY | p.FRIDAY
        exp = java([15, 19, 19, 0, 0, 0, 1, 1, days, d.year % 2000, d.month, d.day,
                    7, 30, 0, 0, 255, 100, 0, -56, 0, -1, -1], 20)
        self.assertEqual(p.schedule_write(p.ScheduleOp.ADD, 0, True, True, days, 7, 30,
                                          255, 100, 0, created=d), exp)

    def test_schedule_add_off(self):
        d = NOW.date()
        exp = java([15, 19, 19, 0, 0, 0, 1, 0, 0x7F, d.year % 2000, d.month, d.day,
                    23, 5, 0, 0, 0, 0, 0, 0, 0, -1, -1], 20)
        self.assertEqual(p.schedule_write(p.ScheduleOp.ADD, 0, True, False, 0x7F, 23, 5,
                                          created=d), exp)

    def test_schedule_delete(self):
        exp = java([15, 19, 19, 0, 2, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    -56, 0, -1, -1], 20)
        self.assertEqual(p.schedule_delete(), exp)

    def test_schedule_read(self):
        self.assertEqual(p.schedule_read(0), java([15, 6, 20, 0, 0, 0, 0, 0, -1, -1], 7))

    def test_check_and_reset(self):
        self.assertEqual(p.check(), java([15, 5, 10, 0, 0, 0, 0, -1, -1], 6))
        self.assertEqual(p.factory_reset(), java([15, 5, 11, 0, 0, 0, 0, -1, -1], 6))


class TestReplies(unittest.TestCase):
    def test_schedule_list(self):
        rec1 = [3, 1, 1, 0x3E, 17, 6, 15, 7, 30, 0, 0, 255, 128, 0, 200]
        rec2 = [4, 0, 0, 0x41, 17, 6, 15, 23, 0, 0, 0, 0, 0, 0, 0]
        body = [0x14, 0, 2] + rec1 + rec2
        raw = bytes([15, len(body) + 1] + body + [p.checksum(body), 255, 255])
        self.assertEqual(len(raw), 2 * 15 + 8)  # what the app expects

        r = p.Reassembler()
        got = []
        for frag in p.chunks(raw):
            got += r.feed(frag)
        self.assertEqual(len(got), 1)
        rules = p.parse_schedules(got[0])
        self.assertEqual(rules[0].rule_id, 3)
        self.assertEqual((rules[0].hour, rules[0].minute), (7, 30))
        self.assertEqual(p.days_from_mask(rules[0].days), ["mo", "tu", "we", "th", "fr"])
        self.assertEqual(rules[0].color, (255, 128, 0))
        self.assertFalse(rules[1].turn_on)

    def test_version(self):
        info = bytes(11) + bytes([1, 4]) + bytes(7)
        self.assertEqual(p.parse_version(info), "1.04")

    def test_days_mask(self):
        self.assertEqual(p.days_mask(["Sunday", "sat"]), 0x41)


if __name__ == "__main__":
    unittest.main()
