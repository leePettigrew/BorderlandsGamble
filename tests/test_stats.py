import unittest

from borderlands_gamble.slots import Currency, Prize, SpinResult, Symbol, Tier
from borderlands_gamble.stats import Stats

S = Symbol


def result(currency: Currency = Currency.CASH, prize: Prize | None = None, stake: int = 100) -> SpinResult:
    return SpinResult(
        machine_key="cash",
        currency=currency,
        line=(S.CASH, S.CASH, S.CASH),
        prize=prize,
        bet=1,
        stake=stake,
        payout=0,
        eridium=0,
        loot=(),
    )


class StatsTests(unittest.TestCase):
    def test_record_uses_actual_amounts(self) -> None:
        stats = Stats()
        stats.record(
            result(prize=Prize("win", payout=5)),
            charged=100,
            cash_paid=500,
            eridium_paid=3,
            items=[Tier.RARE],
        )
        stats.record(result(), charged=100, cash_paid=0, eridium_paid=0, items=[])
        stats.record(
            result(Currency.ERIDIUM, Prize("jp", payout=1, jackpot=True)),
            charged=10,
            cash_paid=0,
            eridium_paid=10,
            items=[Tier.LEGENDARY, Tier.LEGENDARY],
        )
        self.assertEqual(stats.spins, 3)
        self.assertEqual(stats.wins, 2)
        self.assertEqual(stats.jackpots, 1)
        self.assertEqual((stats.cash_spent, stats.cash_won, stats.cash_net), (200, 500, 300))
        self.assertEqual((stats.eridium_spent, stats.eridium_won, stats.eridium_net), (10, 13, 3))
        self.assertEqual(stats.biggest_cash_win, 500)
        self.assertEqual(stats.items, {"rare": 1, "legendary": 2})

    def test_non_paying_prize_is_not_a_win(self) -> None:
        stats = Stats()
        stats.record(result(prize=Prize("three skulls")), charged=100, cash_paid=0, eridium_paid=0, items=[])
        self.assertEqual(stats.wins, 0)

    def test_json_round_trip(self) -> None:
        stats = Stats(spins=4, wins=1, cash_spent=50, items={"epic": 2})
        self.assertEqual(Stats.from_json(stats.to_json()), stats)

    def test_from_json_ignores_garbage(self) -> None:
        self.assertEqual(Stats.from_json(None), Stats())
        self.assertEqual(Stats.from_json([1, 2]), Stats())
        loaded = Stats.from_json(
            {"spins": "lots", "wins": True, "cash_won": 7, "items": {"rare": "x", "epic": 3}}
        )
        self.assertEqual(loaded, Stats(cash_won=7, items={"epic": 3}))

    def test_summary(self) -> None:
        lines = Stats(spins=10, wins=4, cash_spent=1000, cash_won=250).summary_lines()
        self.assertIn("40%", lines[0])
        self.assertIn("net -$750", lines[1])


if __name__ == "__main__":
    unittest.main()
