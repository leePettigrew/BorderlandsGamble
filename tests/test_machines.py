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
from borderlands_gamble.slots import Tier, describe_loot


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


class LootTypeTests(unittest.TestCase):
    def test_every_type_has_every_tier(self) -> None:
        suffixes = {Tier.RARE: "_03_rare", Tier.EPIC: "_04_epic", Tier.LEGENDARY: "_05_legendary"}
        for loot_type in loot.LOOT_TYPES.values():
            self.assertGreaterEqual(loot_type.price, 1.0)
            for tier in Tier:
                self.assertTrue(loot_type.pools[tier], (loot_type.key, tier))
                for pool, weight in loot_type.pools[tier]:
                    self.assertTrue(pool.startswith("itempool_") and pool.endswith(suffixes[tier]), pool)
                    self.assertGreater(weight, 0)
        self.assertEqual(loot.LOOT_TYPES[loot.DEFAULT_LOOT_TYPE].price, 1.0)
        self.assertIsNone(loot.LOOT_TYPES[loot.DEFAULT_LOOT_TYPE].noun)

    def test_narrow_types_only_drop_their_kind(self) -> None:
        rng = random.Random(4)
        for key, family in (
            ("shotguns", "sg"),
            ("class_mods", "class_mods"),
            ("grenades", "grenade_gadgets"),
        ):
            pools = {loot.choose_pool(tier, rng, key) for tier in Tier for _ in range(20)}
            self.assertEqual(
                pools,
                {
                    f"itempool_{family}_03_rare",
                    f"itempool_{family}_04_epic",
                    f"itempool_{family}_05_legendary",
                },
            )

        guns = Counter(loot.choose_pool(Tier.EPIC, rng, "guns").split("_")[1] for _ in range(2000))
        self.assertEqual(set(guns), {"ar", "ps", "sm", "sg", "sr", "hw"})
        self.assertLess(guns["hw"], guns["ar"])

    def test_unknown_types_drop_anything(self) -> None:
        self.assertIs(loot.loot_type("rocket_launchers"), loot.LOOT_TYPES["any"])
        drops = loot.roll_drops(((Tier.RARE, 3),), random.Random(1), "rocket_launchers")
        pools = {pool for pool, _ in loot.ITEM_POOLS[Tier.RARE]}
        self.assertTrue(all(pool in pools for _, pool in drops))

    def test_describe_loot_with_a_noun(self) -> None:
        loot_line = ((Tier.LEGENDARY, 1), (Tier.EPIC, 2), (Tier.RARE, 0))
        self.assertEqual(describe_loot(loot_line), "1 legendary, 2 epic")
        self.assertEqual(describe_loot(loot_line, "shotgun"), "1 legendary shotgun, 2 epic shotguns")


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

    def test_machine_drops_land_between_it_and_the_player(self) -> None:
        # Machine at the origin, player 250 away along +X
        x, y, z = loot.drop_position_near((0.0, 0.0), 50.0, (250.0, 0.0, 100.0), 0, 1)
        self.assertAlmostEqual(x, 50.0 + loot.MACHINE_CLEARANCE)
        self.assertAlmostEqual(y, 0.0)
        self.assertAlmostEqual(z, 100.0 + loot.DROP_HEIGHT)

        spots = [loot.drop_position_near((0.0, 0.0), 50.0, (250.0, 0.0, 100.0), i, 3) for i in range(3)]
        self.assertEqual([round(s[0], 6) for s in spots], [110.0] * 3)
        self.assertAlmostEqual(spots[0][1], -spots[2][1])

        # Big batches stack up in rows towards the player, never behind the machine
        spots = [loot.drop_position_near((0.0, 0.0), 50.0, (0.0, 400.0, 0.0), i, 12) for i in range(12)]
        self.assertEqual(len({round(s[1]) for s in spots}), 3)
        self.assertTrue(all(s[1] > 50.0 for s in spots))

        # Standing right on top of it still works
        self.assertEqual(len(loot.drop_position_near((0.0, 0.0), 50.0, (0.0, 0.0, 0.0), 0, 1)), 3)

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
