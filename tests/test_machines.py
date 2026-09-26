import math
import random
import unittest
from collections import Counter

from borderlands_gamble import loot
from borderlands_gamble.machines import (
    BET_MULTIPLIERS,
    DEFAULT_LUCK,
    ERIDIUM_SLOTS,
    LOOT_SLOTS,
    LUCK_PRESETS,
    base_cost,
    nice_round,
    spin_cost,
)
from borderlands_gamble.report import odds_report
from borderlands_gamble.slots import Tier


class CostTests(unittest.TestCase):
    def test_nice_round(self) -> None:
        self.assertEqual(nice_round(0.2), 1)
        self.assertEqual(nice_round(27.7), 28)
        self.assertEqual(nice_round(2578), 2600)
        self.assertEqual(nice_round(24866), 25000)
        self.assertEqual(nice_round(150), 150)

    def test_cash_cost_grows_with_level(self) -> None:
        costs = [base_cost(LOOT_SLOTS, level) for level in range(1, 71)]
        self.assertEqual(costs, sorted(costs))
        self.assertEqual(costs[0], 10)
        self.assertEqual(base_cost(LOOT_SLOTS, 0), 10)

    def test_eridium_cost_is_flat(self) -> None:
        self.assertEqual(base_cost(ERIDIUM_SLOTS, 1), base_cost(ERIDIUM_SLOTS, 70))

    def test_spin_cost_applies_bet_and_multiplier(self) -> None:
        self.assertEqual(spin_cost(LOOT_SLOTS, 50, bet=5), 5 * 2600)
        self.assertEqual(spin_cost(LOOT_SLOTS, 50, cost_multiplier=0.5), 1300)
        self.assertEqual(spin_cost(ERIDIUM_SLOTS, 50, cost_multiplier=0.01), 1)
        with self.assertRaises(ValueError):
            spin_cost(LOOT_SLOTS, 50, bet=0)
        with self.assertRaises(ValueError):
            spin_cost(LOOT_SLOTS, 50, cost_multiplier=0)

    def test_presets(self) -> None:
        self.assertIn(DEFAULT_LUCK, LUCK_PRESETS)
        self.assertEqual(LUCK_PRESETS[DEFAULT_LUCK], 1.0)
        self.assertEqual(BET_MULTIPLIERS[0], 1)


class LootTests(unittest.TestCase):
    def test_every_tier_has_pools(self) -> None:
        for tier in Tier:
            self.assertTrue(loot.ITEM_POOLS[tier])
            for pool, weight in loot.ITEM_POOLS[tier]:
                self.assertTrue(pool.lower().startswith("itempool_"))
                self.assertGreater(weight, 0)

    def test_pools_match_their_tier(self) -> None:
        suffixes = {Tier.RARE: "_03_rare", Tier.EPIC: "_04_epic", Tier.LEGENDARY: "_05_legendary"}
        for tier, pools in loot.ITEM_POOLS.items():
            for pool, _ in pools:
                self.assertTrue(pool.endswith(suffixes[tier]), pool)

    def test_choose_pool_prefers_guns(self) -> None:
        rng = random.Random(3)
        counts = Counter(loot.choose_pool(Tier.RARE, rng) for _ in range(5000))
        self.assertGreater(counts["itempool_guns_03_rare"], 2000)

    def test_roll_drops_expands_counts(self) -> None:
        drops = loot.roll_drops(((Tier.LEGENDARY, 2), (Tier.RARE, 1)), random.Random(0))
        self.assertEqual([tier for tier, _ in drops], [Tier.LEGENDARY, Tier.LEGENDARY, Tier.RARE])
        pools = {pool for pool, _ in loot.ITEM_POOLS[Tier.LEGENDARY]}
        self.assertIn(drops[0][1], pools)


class DropPositionTests(unittest.TestCase):
    def test_single_drop_is_straight_ahead(self) -> None:
        self.assertEqual(loot.drop_offset(0, 1), (loot.DROP_DISTANCE, 0.0))

    def test_drops_fan_out_symmetrically(self) -> None:
        offsets = [loot.drop_offset(i, 5) for i in range(5)]
        rights = [right for _, right in offsets]
        self.assertEqual(rights, sorted(rights))
        self.assertAlmostEqual(rights[0], -rights[-1])
        self.assertAlmostEqual(rights[2], 0.0)
        for forward, right in offsets:
            self.assertAlmostEqual(math.hypot(forward, right), loot.DROP_DISTANCE)
            self.assertGreater(forward, 0)

    def test_big_batches_stay_in_front(self) -> None:
        for i in range(20):
            forward, _ = loot.drop_offset(i, 20)
            self.assertGreater(forward, 0)

    def test_world_position_follows_facing(self) -> None:
        x, y, z = loot.drop_position((100.0, 200.0, 300.0), 90.0, 0, 1)
        self.assertAlmostEqual(x, 100.0)
        self.assertAlmostEqual(y, 200.0 + loot.DROP_DISTANCE)
        self.assertAlmostEqual(z, 300.0 + loot.DROP_HEIGHT)


class ReportTests(unittest.TestCase):
    def test_report_lists_every_row(self) -> None:
        for machine in (LOOT_SLOTS, ERIDIUM_SLOTS):
            for luck in LUCK_PRESETS:
                lines = odds_report(machine, luck)
                self.assertIn(machine.name, lines[0])
                self.assertEqual(len(lines), 1 + len(machine.paytable) + 2)


if __name__ == "__main__":
    unittest.main()
