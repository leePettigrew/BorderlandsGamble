import itertools
import random
import unittest
from collections import Counter

from borderlands_gamble.machines import LUCK_PRESETS, MACHINES
from borderlands_gamble.slots import (
    MAX_ITEMS_PER_SPIN,
    SYMBOL_COLORS,
    SYMBOL_LABELS,
    Currency,
    Machine,
    Pattern,
    Prize,
    Reel,
    Symbol,
    Tier,
    exact_odds,
    format_amount,
    scale_prize,
    spin,
)

S = Symbol


def machine_with(paytable: tuple[tuple[Pattern, Prize], ...], reel: Reel | None = None) -> Machine:
    reel = reel or Reel.of(dict.fromkeys(Symbol, 1))
    return Machine("test", "Test", Currency.CASH, (reel, reel, reel), paytable)


class ReelTests(unittest.TestCase):
    def test_rejects_bad_weights(self) -> None:
        with self.assertRaises(ValueError):
            Reel(())
        with self.assertRaises(ValueError):
            Reel.of({S.SKULL: 0})
        with self.assertRaises(ValueError):
            Reel(((S.SKULL, 1), (S.SKULL, 2)))

    def test_probabilities_sum_to_one(self) -> None:
        reel = Reel.of({S.SKULL: 3, S.CASH: 1})
        self.assertAlmostEqual(sum(reel.probabilities().values()), 1.0)
        self.assertAlmostEqual(reel.probabilities()[S.SKULL], 0.75)

    def test_luck_only_scales_lucky_symbols(self) -> None:
        reel = Reel.of({S.SKULL: 2, S.LEGENDARY: 2}).with_luck(3)
        self.assertEqual(dict(reel.weights), {S.SKULL: 2, S.LEGENDARY: 6})
        with self.assertRaises(ValueError):
            reel.with_luck(0)

    def test_draw_follows_weights(self) -> None:
        reel = Reel.of({S.SKULL: 9, S.CASH: 1})
        rng = random.Random(1234)
        counts = Counter(reel.draw(rng) for _ in range(20_000))
        self.assertAlmostEqual(counts[S.SKULL] / 20_000, 0.9, delta=0.01)


class PatternTests(unittest.TestCase):
    def test_three(self) -> None:
        self.assertTrue(Pattern.three(S.VAULT).matches((S.VAULT, S.VAULT, S.VAULT)))
        self.assertFalse(Pattern.three(S.VAULT).matches((S.VAULT, S.VAULT, S.CASH)))

    def test_any_three(self) -> None:
        pattern = Pattern.any_three(S.RARE, S.EPIC)
        self.assertTrue(pattern.matches((S.RARE, S.EPIC, S.RARE)))
        self.assertFalse(pattern.matches((S.RARE, S.EPIC, S.CASH)))

    def test_at_least(self) -> None:
        pattern = Pattern.at_least(S.CASH, 2)
        self.assertTrue(pattern.matches((S.CASH, S.SKULL, S.CASH)))
        self.assertTrue(pattern.matches((S.CASH, S.CASH, S.CASH)))
        self.assertFalse(pattern.matches((S.CASH, S.SKULL, S.SKULL)))

    def test_describe(self) -> None:
        self.assertEqual(Pattern.three(S.VAULT).describe(), "3x VAULT")
        self.assertEqual(Pattern.any_three(S.EPIC, S.RARE).describe(), "any 3 of RARE/EPIC")


class EvaluateTests(unittest.TestCase):
    def test_first_matching_row_wins(self) -> None:
        big = Prize("big", payout=10)
        small = Prize("small", payout=1)
        machine = machine_with(((Pattern.three(S.CASH), big), (Pattern.at_least(S.CASH, 1), small)))
        self.assertIs(machine.evaluate((S.CASH, S.CASH, S.CASH)), big)
        self.assertIs(machine.evaluate((S.CASH, S.SKULL, S.SKULL)), small)
        self.assertIsNone(machine.evaluate((S.SKULL, S.SKULL, S.SKULL)))

    def test_non_paying_row_blocks_later_rows(self) -> None:
        blocker = Prize("house wins")
        machine = machine_with(
            ((Pattern.three(S.SKULL), blocker), (Pattern.at_least(S.SKULL, 2), Prize("pair", payout=1))),
        )
        self.assertIs(machine.evaluate((S.SKULL, S.SKULL, S.SKULL)), blocker)
        self.assertFalse(blocker.pays_anything)

    def test_rejects_wrong_line_length(self) -> None:
        with self.assertRaises(ValueError):
            MACHINES["cash"].evaluate((S.CASH, S.CASH))

    def test_mixed_loot_pays_lowest_rarity(self) -> None:
        for machine in MACHINES.values():
            with self.subTest(machine=machine.key):
                self.assertEqual(machine.evaluate((S.EPIC, S.LEGENDARY, S.EPIC)).loot[0][0], Tier.EPIC)
                self.assertEqual(machine.evaluate((S.RARE, S.LEGENDARY, S.EPIC)).loot[0][0], Tier.RARE)
                self.assertEqual(machine.evaluate((S.LEGENDARY,) * 3).loot[0][0], Tier.LEGENDARY)

    def test_jackpot_is_three_vaults(self) -> None:
        for machine in MACHINES.values():
            with self.subTest(machine=machine.key):
                prize = machine.evaluate((S.VAULT, S.VAULT, S.VAULT))
                self.assertTrue(prize.jackpot)
                jackpots = [prize for _, prize in machine.paytable if prize.jackpot]
                self.assertEqual(len(jackpots), 1)


class ScalePrizeTests(unittest.TestCase):
    def test_losing_spin_pays_nothing(self) -> None:
        self.assertEqual(scale_prize(None, 100, 1), (0, 0, ()))
        self.assertEqual(scale_prize(Prize("nothing"), 100, 1), (0, 0, ()))

    def test_scales_with_stake_and_bet(self) -> None:
        prize = Prize("x", payout=2.5, eridium=3, loot=((Tier.RARE, 1),))
        self.assertEqual(scale_prize(prize, 1000, 5), (2500, 15, ((Tier.RARE, 5),)))

    def test_rounds_half_up(self) -> None:
        self.assertEqual(scale_prize(Prize("x", payout=1.5), 3, 1)[0], 5)

    def test_caps_items(self) -> None:
        prize = Prize("x", loot=((Tier.LEGENDARY, 3), (Tier.EPIC, 3)))
        _, _, loot = scale_prize(prize, 1, 10)
        self.assertEqual(sum(count for _, count in loot), MAX_ITEMS_PER_SPIN)
        self.assertEqual(loot[0], (Tier.LEGENDARY, 20))


class SpinTests(unittest.TestCase):
    def test_result_matches_paytable(self) -> None:
        rng = random.Random(42)
        for machine in MACHINES.values():
            for _ in range(2000):
                result = spin(machine, rng, stake=100, bet=2)
                prize = machine.evaluate(result.line)
                self.assertIs(result.prize, prize)
                self.assertEqual((result.payout, result.eridium, result.loot), scale_prize(prize, 100, 2))
                self.assertEqual(result.won, prize is not None and prize.pays_anything)

    def test_seeded_spins_are_deterministic(self) -> None:
        machine = MACHINES["cash"]
        rng_a, rng_b = random.Random(7), random.Random(7)
        first = [spin(machine, rng_a, stake=10).line for _ in range(50)]
        second = [spin(machine, rng_b, stake=10).line for _ in range(50)]
        self.assertEqual(first, second)

    def test_rejects_bad_bets(self) -> None:
        with self.assertRaises(ValueError):
            spin(MACHINES["cash"], random.Random(), stake=10, bet=0)
        with self.assertRaises(ValueError):
            spin(MACHINES["cash"], random.Random(), stake=-1)


class OddsTests(unittest.TestCase):
    def test_rows_are_a_partition(self) -> None:
        for machine in MACHINES.values():
            reel_probs = [reel.probabilities() for reel in machine.reels]
            no_match = sum(
                reel_probs[0][a] * reel_probs[1][b] * reel_probs[2][c]
                for a, b, c in itertools.product(*reel_probs)
                if machine.evaluate((a, b, c)) is None
            )
            odds = exact_odds(machine)
            self.assertAlmostEqual(sum(p for *_, p in odds.rows) + no_match, 1.0)

    def test_matches_simulation(self) -> None:
        machine = MACHINES["cash"]
        odds = exact_odds(machine)
        rng = random.Random(99)
        pulls = 60_000
        results = [spin(machine, rng, stake=1_000) for _ in range(pulls)]
        hit_rate = sum(r.won for r in results) / pulls
        rtp = sum(r.payout for r in results) / (pulls * 1_000)
        self.assertAlmostEqual(hit_rate, odds.hit_rate, delta=0.01)
        self.assertAlmostEqual(rtp, odds.return_to_player, delta=0.06)

    def test_house_keeps_an_edge(self) -> None:
        for machine in MACHINES.values():
            for name, luck in LUCK_PRESETS.items():
                with self.subTest(machine=machine.key, luck=name):
                    odds = exact_odds(machine.with_luck(luck))
                    self.assertLess(odds.return_to_player, 1.0)
                    self.assertGreater(odds.hit_rate, 0.25)

    def test_more_luck_means_more_legendaries(self) -> None:
        for machine in MACHINES.values():
            rates = []
            for luck in sorted(LUCK_PRESETS.values()):
                items = dict(exact_odds(machine.with_luck(luck)).items_per_spin)
                rates.append(items[Tier.LEGENDARY])
            self.assertEqual(rates, sorted(rates))


class DataTests(unittest.TestCase):
    def test_every_symbol_is_drawable(self) -> None:
        for symbol in Symbol:
            self.assertIn(symbol, SYMBOL_LABELS)
            self.assertIn(symbol, SYMBOL_COLORS)
            self.assertEqual(len(SYMBOL_COLORS[symbol]), 4)

    def test_paytable_symbols_are_on_the_reels(self) -> None:
        for machine in MACHINES.values():
            on_reels = set().union(*(reel.symbols for reel in machine.reels))
            for pattern, _ in machine.paytable:
                self.assertLessEqual(pattern.symbols, on_reels)

    def test_format_amount(self) -> None:
        self.assertEqual(format_amount(Currency.CASH, 1234567), "$1,234,567")
        self.assertEqual(format_amount(Currency.CASH, -50), "-$50")
        self.assertEqual(format_amount(Currency.ERIDIUM, 1500), "1,500 eridium")


if __name__ == "__main__":
    unittest.main()
