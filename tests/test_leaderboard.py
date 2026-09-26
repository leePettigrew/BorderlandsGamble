import json
import unittest

from borderlands_gamble.casino import Payout
from borderlands_gamble.leaderboard import (
    DROPS_PER_PLAYER,
    HISTORY_SIZE,
    NAME_LENGTH,
    Drop,
    Leaderboard,
    SpinRecord,
    clean_name,
    compact_amount,
    describe_drops,
    items_summary,
)
from borderlands_gamble.loot import ITEM_FAMILIES, ITEM_POOLS, LOOT_TYPES, item_name, pool_family
from borderlands_gamble.machines import replay
from borderlands_gamble.slots import Currency, Symbol, Tier

S = Symbol
LEGENDARY_SHOTGUN = Drop(Tier.LEGENDARY, "sg")


def record(
    player: str = "Zane",
    line: tuple[Symbol, Symbol, Symbol] = (S.SKULL, S.CASH, S.RARE),
    *,
    machine: str = "cash",
    stake: int = 50_000,
    charged: int | None = None,
    cash: int = 0,
    eridium: int = 0,
    drops: tuple[Drop, ...] = (),
    bet: int = 1,
) -> SpinRecord:
    return SpinRecord(
        player,
        machine,
        line,
        bet,
        stake,
        stake if charged is None else charged,
        cash,
        eridium,
        drops,
    )


class ItemTests(unittest.TestCase):
    def test_every_pool_has_a_known_family(self) -> None:
        tables = (ITEM_POOLS, *(loot.pools for loot in LOOT_TYPES.values()))
        pools = {pool for table in tables for tier_pools in table.values() for pool, _ in tier_pools}
        for pool in pools:
            self.assertIn(pool_family(pool), ITEM_FAMILIES, pool)

    def test_pool_family(self) -> None:
        self.assertEqual(pool_family("itempool_sg_05_legendary"), "sg")
        self.assertEqual(pool_family("itempool_grenade_gadgets_03_rare"), "grenade_gadgets")
        self.assertIsNone(pool_family("itempool_mystery_03_rare"))
        self.assertIsNone(pool_family("something_else"))

    def test_family_codes_are_unique_and_short(self) -> None:
        codes = [code for _, code in ITEM_FAMILIES.values()]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(len(code) == 2 and code.isalpha() and code.islower() for code in codes))

    def test_names(self) -> None:
        self.assertEqual(item_name(Tier.LEGENDARY, "sg"), "legendary shotgun")
        self.assertEqual(item_name(Tier.RARE, None), "rare item")
        self.assertEqual(
            describe_drops([LEGENDARY_SHOTGUN, Drop(Tier.EPIC, "shields"), Drop(Tier.EPIC, "shields")]),
            "legendary shotgun, 2 epic shields",
        )


class RecordTests(unittest.TestCase):
    def test_net_and_outcome(self) -> None:
        self.assertEqual(record().net, -50_000)
        self.assertEqual(record().outcome(), "no luck")

        drops = (LEGENDARY_SHOTGUN, Drop(Tier.LEGENDARY, "ar"))
        jackpot = record(line=(S.VAULT,) * 3, cash=2_500_000, drops=drops)
        self.assertEqual(jackpot.net, 2_450_000)
        self.assertEqual(jackpot.outcome(), "legendary shotgun, legendary assault rifle")

        self.assertEqual(record(line=(S.CASH,) * 3, cash=1_000_000).outcome(), "Cash out!")
        self.assertEqual(record(line=(S.ERIDIUM,) * 3, eridium=40).outcome(), "40 eridium")
        self.assertEqual(record(line=(S.VAULT, S.SKULL, S.CASH), cash=50_000).net, 0)

    def test_eridium_machine_nets_in_eridium(self) -> None:
        spin = record(machine="eridium", line=(S.ERIDIUM,) * 3, stake=10, eridium=150)
        self.assertEqual(spin.currency, Currency.ERIDIUM)
        self.assertEqual(spin.net, 140)

    def test_free_play_costs_nothing(self) -> None:
        self.assertEqual(record(charged=0).net, 0)

    def test_of_payout(self) -> None:
        spin = replay("cash", (S.VAULT,) * 3, 1, 50_000)
        payout = Payout(
            cash=2_500_000,
            items=(Tier.LEGENDARY, Tier.LEGENDARY),
            pools=("itempool_sg_05_legendary", "itempool_mystery_05_legendary"),
        )
        made = SpinRecord.of_payout(" Zane | x ", spin, 50_000, payout)
        self.assertEqual(
            made,
            record(
                "Zane / x", (S.VAULT,) * 3, cash=2_500_000, drops=(LEGENDARY_SHOTGUN, Drop(Tier.LEGENDARY))
            ),
        )

    def test_bad_records_are_refused(self) -> None:
        for bad in (
            {"player": ""},
            {"machine": "roulette"},
            {"bet": 0},
            {"cash": -1},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                args = {"player": "Zane", "machine": "cash", "bet": 1, "cash": 0} | bad
                SpinRecord(args["player"], args["machine"], (S.SKULL,) * 3, args["bet"], 10, 10, args["cash"])

    def test_json_round_trip(self) -> None:
        drops = (LEGENDARY_SHOTGUN, Drop(Tier.RARE))
        spin = record(line=(S.VAULT,) * 3, cash=2_500_000, eridium=3, drops=drops)
        self.assertEqual(SpinRecord.from_json(json.loads(json.dumps(spin.to_json()))), spin)
        self.assertIsNone(SpinRecord.from_json({"player": "Zane"}))
        self.assertIsNone(SpinRecord.from_json(spin.to_json() | {"line": ["vault", "nope", "vault"]}))
        self.assertIsNone(SpinRecord.from_json(spin.to_json() | {"cash": "lots"}))
        self.assertIsNone(SpinRecord.from_json("nope"))


class LeaderboardTests(unittest.TestCase):
    def test_standings_rank_by_net_cash(self) -> None:
        board = Leaderboard()
        board.record(record("Moze"))
        board.record(record("Zane", (S.CASH,) * 3, cash=1_000_000))
        board.record(record("Zane"))
        board.record(record("Amara", (S.LEGENDARY,) * 3, cash=250_000, drops=(LEGENDARY_SHOTGUN,)))

        self.assertEqual([s.player for s in board.ranked()], ["Zane", "Amara", "Moze"])
        zane = board.standings["Zane"]
        self.assertEqual((zane.stats.spins, zane.stats.wins, zane.losses), (2, 1, 1))
        self.assertEqual(zane.stats.cash_net, 900_000)
        self.assertEqual(board.standings["Amara"].drops, [LEGENDARY_SHOTGUN])
        self.assertEqual(board.standings["Amara"].stats.items, {"legendary": 1})
        self.assertEqual(board.pulls, 4)

    def test_recent_is_newest_first_and_capped(self) -> None:
        board = Leaderboard()
        for idx in range(HISTORY_SIZE + 5):
            board.record(record(f"P{idx % 3}", stake=idx + 1))
        self.assertEqual(len(board.history), HISTORY_SIZE)
        self.assertEqual(board.recent()[0].stake, HISTORY_SIZE + 5)
        self.assertEqual(board.pulls, HISTORY_SIZE + 5, "totals keep counting past the history")

    def test_drops_are_newest_first_and_capped(self) -> None:
        board = Leaderboard()
        for _ in range(DROPS_PER_PLAYER):
            board.record(record(line=(S.RARE,) * 3, drops=(Drop(Tier.RARE, "guns"),)))
        board.record(record(line=(S.LEGENDARY,) * 3, drops=(LEGENDARY_SHOTGUN,)))
        drops = board.standings["Zane"].drops
        self.assertEqual(len(drops), DROPS_PER_PLAYER)
        self.assertEqual(drops[0], LEGENDARY_SHOTGUN)

    def test_json_round_trip_skips_junk(self) -> None:
        board = Leaderboard()
        board.record(record("Zane", (S.VAULT,) * 3, cash=2_500_000, drops=(LEGENDARY_SHOTGUN,)))
        board.record(record("Moze"))
        data = json.loads(json.dumps(board.to_json()))
        data["history"].append({"player": "Broken"})
        data["players"]["Junk"] = "not a player"

        loaded = Leaderboard.from_json(data)
        self.assertEqual(loaded.history, board.history)
        self.assertEqual(set(loaded.standings), {"Zane", "Moze"})
        self.assertEqual(loaded.standings["Zane"].stats, board.standings["Zane"].stats)
        self.assertEqual(loaded.standings["Zane"].drops, [LEGENDARY_SHOTGUN])
        self.assertEqual(Leaderboard.from_json(None).standings, {})

    def test_summary_lines(self) -> None:
        self.assertEqual(Leaderboard().summary_lines(), ["No pulls on the leaderboard yet."])
        board = Leaderboard()
        board.record(record("Zane", (S.VAULT,) * 3, cash=2_500_000, drops=(LEGENDARY_SHOTGUN,)))
        board.record(record("Moze"))
        lines = board.summary_lines()
        self.assertEqual(lines[0], "Leaderboard: 2 pulls by 2 players")
        self.assertIn("1. Zane: net $2,450,000, 1 won, 0 lost, 1 legendary", lines[1])
        self.assertIn("Recent drops: legendary shotgun", lines[2])
        self.assertIn("2. Moze: net -$50,000, 0 won, 1 lost", lines[3])
        self.assertEqual(lines[-1], "  Zane: Loot Slots x1, +$2.45M, legendary shotgun")

    def test_clear(self) -> None:
        board = Leaderboard()
        board.record(record())
        board.clear()
        self.assertEqual((board.standings, board.history), ({}, []))


class FormatTests(unittest.TestCase):
    def test_compact_amount(self) -> None:
        cases = {
            0: "$0",
            9_500: "$9,500",
            10_000: "$10k",
            12_345: "$12.3k",
            52_500: "$52.5k",
            250_000: "$250k",
            999_999: "$1M",
            1_250_000: "$1.25M",
            2_500_000_000: "$2.5B",
        }
        for amount, expected in cases.items():
            self.assertEqual(compact_amount(Currency.CASH, amount), expected, amount)
        self.assertEqual(compact_amount(Currency.CASH, -52_500, signed=True), "-$52.5k")
        self.assertEqual(compact_amount(Currency.CASH, 52_500, signed=True), "+$52.5k")
        self.assertEqual(compact_amount(Currency.CASH, 0, signed=True), "$0")
        self.assertEqual(compact_amount(Currency.ERIDIUM, 40, signed=True), "+40 eridium")

    def test_items_summary(self) -> None:
        board = Leaderboard()
        board.record(record(line=(S.RARE,) * 3, drops=(Drop(Tier.RARE, "guns"),)))
        board.record(record(line=(S.LEGENDARY,) * 3, drops=(LEGENDARY_SHOTGUN,)))
        self.assertEqual(items_summary(board.standings["Zane"].stats), "1 legendary, 1 rare")

    def test_clean_name(self) -> None:
        self.assertEqual(clean_name("  Zo  Vx|Bee  "), "Zo Vx/Bee")
        self.assertEqual(clean_name("   "), "Player")
        self.assertEqual(len(clean_name("x" * 50)), NAME_LENGTH)


if __name__ == "__main__":
    unittest.main()
