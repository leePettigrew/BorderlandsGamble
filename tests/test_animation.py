import random
import unittest

from borderlands_gamble.animation import MIN_SCROLL_STEPS, display_strip, plan_spin
from borderlands_gamble.machines import MACHINES
from borderlands_gamble.slots import Reel, Symbol

S = Symbol


class DisplayStripTests(unittest.TestCase):
    def test_contains_every_symbol(self) -> None:
        for machine in MACHINES.values():
            for reel in machine.reels:
                strip = display_strip(reel)
                self.assertEqual(set(strip), set(reel.symbols))
                self.assertGreaterEqual(len(strip), 24)

    def test_rare_symbols_still_appear(self) -> None:
        strip = display_strip(Reel.of({S.SKULL: 1000, S.VAULT: 1}))
        self.assertIn(S.VAULT, strip)

    def test_common_symbols_are_spread_out(self) -> None:
        strip = display_strip(Reel.of({S.SKULL: 1, S.CASH: 1, S.VAULT: 1}), length=12)
        for a, b in zip(strip, strip[1:] + strip[:1], strict=True):
            self.assertNotEqual(a, b)


class PlanSpinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = MACHINES["cash"]
        self.strips = [display_strip(reel) for reel in self.machine.reels]

    def test_lands_on_the_line(self) -> None:
        rng = random.Random(5)
        symbols = list(Symbol)
        for _ in range(200):
            line = tuple(rng.choice(symbols) for _ in range(3))
            starts = [rng.randrange(100) for _ in range(3)]
            anim = plan_spin(self.strips, line, rng, start_time=10.0, start_indices=starts)
            self.assertEqual(tuple(w[1] for w in anim.windows(anim.end_time)), line)
            self.assertEqual(
                tuple(s[i] for s, i in zip(self.strips, anim.final_indices, strict=True)),
                line,
            )

    def test_reels_stop_left_to_right(self) -> None:
        anim = plan_spin(
            self.strips, (S.CASH,) * 3, random.Random(0), start_time=0.0, first_stop=1.0, stagger=0.5
        )
        stops = [reel.stop_time for reel in anim.reels]
        self.assertEqual(stops, [1.0, 1.5, 2.0])
        self.assertEqual(anim.stopped(1.2), (True, False, False))
        self.assertFalse(anim.done(1.9))
        self.assertTrue(anim.done(2.0))

    def test_scrolls_forward_smoothly(self) -> None:
        anim = plan_spin(self.strips, (S.VAULT,) * 3, random.Random(1), start_time=0.0)
        for reel in anim.reels:
            self.assertGreaterEqual(reel.steps, MIN_SCROLL_STEPS)
            positions = [reel.position(t / 100) for t in range(300)]
            self.assertEqual(positions, sorted(positions))
            self.assertEqual(positions[0], 0)
            self.assertEqual(positions[-1], reel.steps)

    def test_windows_are_strip_neighbours(self) -> None:
        anim = plan_spin(self.strips, (S.EPIC,) * 3, random.Random(2), start_time=0.0)
        for t in (0.0, 0.3, 0.7, 5.0):
            for reel, (above, mid, below) in zip(anim.reels, anim.windows(t), strict=True):
                idx = reel.start_index + reel.position(t)
                n = len(reel.strip)
                self.assertEqual(
                    (above, mid, below),
                    (reel.strip[(idx - 1) % n], reel.strip[idx % n], reel.strip[(idx + 1) % n]),
                )

    def test_zero_duration_is_instant(self) -> None:
        anim = plan_spin(self.strips, (S.SKULL,) * 3, random.Random(0), start_time=3.0, first_stop=0)
        self.assertTrue(anim.done(3.0))
        self.assertEqual(tuple(w[1] for w in anim.windows(3.0)), (S.SKULL,) * 3)

    def test_rejects_symbols_missing_from_strip(self) -> None:
        strips = [(S.SKULL, S.CASH)] * 3
        with self.assertRaises(ValueError):
            plan_spin(strips, (S.VAULT,) * 3, random.Random(0), start_time=0.0)
        with self.assertRaises(ValueError):
            plan_spin(strips, (S.SKULL,) * 2, random.Random(0), start_time=0.0)


if __name__ == "__main__":
    unittest.main()
