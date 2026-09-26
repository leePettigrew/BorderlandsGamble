import random
import unittest

from borderlands_gamble.casino import Tone
from borderlands_gamble.machines import LOOT_SLOTS
from borderlands_gamble.slots import SpinResult, Symbol, scale_prize
from borderlands_gamble.spectate import RESULT_SECONDS, SPIN_SECONDS, STAGGER_SECONDS, Spectator

S = Symbol
SPIN_TIME = SPIN_SECONDS + 2 * STAGGER_SECONDS


def result(line: tuple[Symbol, Symbol, Symbol], *, stake: int = 2600, bet: int = 1) -> SpinResult:
    prize = LOOT_SLOTS.evaluate(line)
    payout, eridium, loot = scale_prize(prize, stake, bet)
    return SpinResult("cash", LOOT_SLOTS.currency, line, prize, bet, stake, payout, eridium, loot)


class Clock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


class SpectatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.spectator = Spectator(rng=random.Random(0), clock=self.clock)

    def test_spin_then_result_then_gone(self) -> None:
        self.assertFalse(self.spectator.active)
        self.spectator.watch(257, "Zane", result((S.CASH,) * 3))

        [view] = self.spectator.views()
        self.assertEqual((view.player_id, view.name, view.machine), (257, "Zane", "LOOT SLOTS"))
        self.assertEqual((view.status, view.tone, view.stopped), ("Spinning...", Tone.INFO, False))

        self.clock.now += SPIN_TIME + 0.01
        [view] = self.spectator.views()
        self.assertTrue(view.stopped)
        self.assertEqual(view.status, "Cash out!  +$52,000")
        self.assertEqual(view.tone, Tone.BIG_WIN)
        self.assertEqual(tuple(reel.payline for reel in view.reels), (S.CASH,) * 3)
        self.assertTrue(all(reel.stopped for reel in view.reels))

        self.clock.now += RESULT_SECONDS
        self.assertEqual(self.spectator.views(), [])
        self.assertFalse(self.spectator.active)

    def test_reels_stop_left_to_right(self) -> None:
        self.spectator.watch(1, "Amara", result((S.SKULL, S.EPIC, S.VAULT)))
        self.clock.now += SPIN_SECONDS + 0.01
        [view] = self.spectator.views()
        self.assertEqual([reel.stopped for reel in view.reels], [True, False, False])

    def test_players_are_separate_and_new_spins_replace_old(self) -> None:
        self.spectator.watch(1, "Amara", result((S.SKULL,) * 3))
        self.spectator.watch(2, "Rafa", result((S.VAULT,) * 3))
        self.spectator.watch(1, "Amara", result((S.CASH,) * 3))
        self.clock.now += SPIN_TIME + 0.01
        views = {view.player_id: view for view in self.spectator.views()}
        self.assertEqual(set(views), {1, 2})
        self.assertEqual(views[1].status, "Cash out!  +$52,000")
        self.assertEqual(views[2].tone, Tone.JACKPOT)

    def test_loot_is_named(self) -> None:
        self.spectator.watch(3, "Vex", result((S.RARE,) * 3, stake=3900), "shotguns")
        self.clock.now += SPIN_TIME + 0.01
        [view] = self.spectator.views()
        self.assertEqual(view.status, "Rare loot!  1 rare shotgun")

    def test_clear(self) -> None:
        self.spectator.watch(3, "Vex", result((S.RARE,) * 3))
        self.spectator.clear()
        self.assertFalse(self.spectator.active)


if __name__ == "__main__":
    unittest.main()
