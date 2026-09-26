import random
import unittest
from collections.abc import Sequence
from typing import Any

from borderlands_gamble.casino import (
    INT32_MAX,
    MESSAGE_SECONDS,
    SETTLE_TIMEOUT,
    Casino,
    HouseRules,
    LocalLink,
    Payout,
    PlayerSettings,
    SlotController,
    Tone,
    expected_payout,
)
from borderlands_gamble.machines import LOOT_SLOTS, spin_cost
from borderlands_gamble.slots import Currency, Symbol, Tier
from borderlands_gamble.stats import Stats

S = Symbol
STAKE_50 = spin_cost(LOOT_SLOTS, 50)


class ScriptedRandom(random.Random):
    """A seeded RNG, except reel draws come from a script of lines."""

    def __init__(self, lines: Sequence[tuple[Symbol, Symbol, Symbol]] = ()) -> None:
        super().__init__(0)
        self.queue = [symbol for line in lines for symbol in line]

    def choices(self, population: Any, *args: Any, **kwargs: Any) -> list[Any]:
        if self.queue and all(isinstance(x, Symbol) for x in population):
            return [self.queue.pop(0)]
        return super().choices(population, *args, **kwargs)


class FakePlayer:
    def __init__(self, name: str, level: int | None = 50) -> None:
        self.name = name
        self.level = level
        self.balances = {Currency.CASH: 1_000_000, Currency.ERIDIUM: 500}
        self.near_machine = True
        self.cant_play: str | None = None


class FakeBackend:
    def __init__(self) -> None:
        self.ignore_charges = False
        self.fail_spawns = False
        self.currency_calls: list[tuple[str, Currency, int]] = []
        self.spawned: list[tuple[str, str, int, int, int]] = []

    def player_key(self, player: FakePlayer) -> str:
        return player.name

    def player_name(self, player: FakePlayer) -> str:
        return player.name

    def check_can_play(self, player: FakePlayer) -> str | None:
        return player.cant_play

    def is_near_machine(self, player: FakePlayer, radius: float) -> bool:
        return player.near_machine

    def player_level(self, player: FakePlayer) -> int | None:
        return player.level

    def get_balance(self, player: FakePlayer, currency: Currency) -> int | None:
        return player.balances.get(currency)

    def add_currency(self, player: FakePlayer, currency: Currency, amount: int) -> None:
        self.currency_calls.append((player.name, currency, amount))
        if amount < 0 and self.ignore_charges:
            return
        player.balances[currency] += amount

    def spawn_item(self, player: FakePlayer, pool: str, level: int, index: int, count: int) -> None:
        if self.fail_spawns:
            raise RuntimeError("no item pool store")
        self.spawned.append((player.name, pool, level, index, count))


class FakeDisplay:
    def __init__(self) -> None:
        self.views: list[Any] = []
        self.hidden = 0

    def render(self, view: Any) -> None:
        self.views.append(view)

    def hide(self) -> None:
        self.hidden += 1


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class CasinoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.clock = FakeClock()
        self.rules = HouseRules()
        self.player = FakePlayer("Amara")

    def make_casino(self, lines: Sequence[tuple[Symbol, Symbol, Symbol]] = (), **rules: Any) -> Casino:
        self.rules = HouseRules(**rules)
        return Casino(
            self.backend,
            lambda: self.rules,
            rng=ScriptedRandom(lines),
            clock=self.clock,
            log=lambda _: None,
        )


class CasinoRefusalTests(CasinoTestCase):
    def assert_refused(self, reply: Any, text: str) -> None:
        self.assertIsInstance(reply, str)
        self.assertIn(text, reply)
        self.assertEqual(self.backend.currency_calls, [])

    def test_cant_play(self) -> None:
        casino = self.make_casino()
        self.player.cant_play = "Load into the game first."
        self.assert_refused(casino.pull(self.player, 1, "cash", 1), "Load into the game")

    def test_needs_a_machine(self) -> None:
        casino = self.make_casino()
        self.player.near_machine = False
        self.assert_refused(casino.pull(self.player, 1, "cash", 1), "vending machine")

    def test_machine_not_required(self) -> None:
        casino = self.make_casino([(S.SKULL, S.CASH, S.EPIC)], require_machine=False)
        self.player.near_machine = False
        self.assertNotIsInstance(casino.pull(self.player, 1, "cash", 1), str)

    def test_not_enough_money(self) -> None:
        casino = self.make_casino()
        self.player.balances[Currency.CASH] = 5
        self.assert_refused(casino.pull(self.player, 1, "cash", 1), "Not enough cash")

    def test_charge_that_does_not_apply_voids_the_spin(self) -> None:
        casino = self.make_casino([(S.VAULT,) * 3])
        self.backend.ignore_charges = True
        reply = casino.pull(self.player, 1, "cash", 1)
        self.assertIn("didn't go through", reply)
        self.assertFalse(casino.has_pending)

    def test_bad_requests(self) -> None:
        casino = self.make_casino()
        self.assert_refused(casino.pull(self.player, 1, "roulette", 1), "Unknown machine")
        self.assert_refused(casino.pull(self.player, 1, "cash", 3), "Unsupported bet")


class CasinoPayoutTests(CasinoTestCase):
    def test_charges_now_pays_on_settle(self) -> None:
        casino = self.make_casino([(S.CASH,) * 3])
        result, charged = casino.pull(self.player, 7, "cash", 1)
        self.assertEqual(charged, STAKE_50)
        self.assertEqual(result.line, (S.CASH,) * 3)
        self.assertEqual(self.player.balances[Currency.CASH], 1_000_000 - STAKE_50)
        self.assertTrue(casino.has_pending)

        self.assertIsNone(casino.settle("Amara", request_id=6), "wrong request id shouldn't settle")
        payout = casino.settle("Amara", request_id=7)
        self.assertEqual(payout, Payout(cash=20 * STAKE_50))
        self.assertEqual(self.player.balances[Currency.CASH], 1_000_000 + 19 * STAKE_50)
        self.assertIsNone(casino.settle("Amara"))

    def test_new_pull_pays_out_the_last(self) -> None:
        casino = self.make_casino([(S.CASH,) * 3, (S.SKULL,) * 3])
        casino.pull(self.player, 1, "cash", 1)
        casino.pull(self.player, 2, "cash", 1)
        self.assertEqual(self.backend.currency_calls[1], ("Amara", Currency.CASH, 20 * STAKE_50))

    def test_unconfirmed_spins_pay_out_eventually(self) -> None:
        casino = self.make_casino([(S.RARE,) * 3])
        casino.pull(self.player, 1, "cash", 1)
        self.assertTrue(casino.tick())
        self.clock.now += SETTLE_TIMEOUT
        self.assertFalse(casino.tick())
        self.assertEqual(len(self.backend.spawned), 1)

    def test_players_are_independent(self) -> None:
        casino = self.make_casino([(S.CASH,) * 3, (S.ERIDIUM,) * 3])
        friend = FakePlayer("Rafa", level=10)
        casino.pull(self.player, 1, "cash", 1)
        casino.pull(friend, 1, "cash", 1)
        self.assertEqual(friend.balances[Currency.CASH], 1_000_000 - spin_cost(LOOT_SLOTS, 10))

        self.assertEqual(casino.settle("Rafa", 1), Payout(eridium=40))
        self.assertEqual(friend.balances[Currency.ERIDIUM], 540)
        self.assertEqual(self.player.balances[Currency.ERIDIUM], 500)
        self.assertEqual(casino.settle("Amara", 1), Payout(cash=20 * STAKE_50))

    def test_jackpot_with_bet(self) -> None:
        casino = self.make_casino([(S.VAULT,) * 3])
        casino.pull(self.player, 1, "cash", 2)
        payout = casino.settle("Amara", 1)
        self.assertEqual(payout.cash, 50 * 2 * STAKE_50)
        self.assertEqual(payout.items, (Tier.LEGENDARY,) * 4)
        self.assertEqual([(i, n) for *_, i, n in self.backend.spawned], [(0, 4), (1, 4), (2, 4), (3, 4)])
        self.assertTrue(all(pool.endswith("_05_legendary") for _, pool, *_ in self.backend.spawned))

    def test_loot_level(self) -> None:
        casino = self.make_casino([(S.RARE,) * 3], loot_level=12)
        casino.pull(self.player, 1, "cash", 1)
        casino.settle_all()
        self.assertEqual(self.backend.spawned[0][2], 12)

        casino = self.make_casino([(S.RARE,) * 3])
        casino.pull(self.player, 2, "cash", 1)
        casino.settle_all()
        self.assertEqual(self.backend.spawned[1][2], 50)

    def test_eridium_machine(self) -> None:
        casino = self.make_casino([(S.ERIDIUM,) * 3])
        casino.pull(self.player, 1, "eridium", 1)
        payout = casino.settle("Amara")
        self.assertEqual(payout, Payout(eridium=150))
        self.assertEqual(self.player.balances[Currency.ERIDIUM], 500 - 10 + 150)

    def test_free_play(self) -> None:
        casino = self.make_casino([(S.CASH,) * 3], free_play=True)
        _, charged = casino.pull(self.player, 1, "cash", 1)
        self.assertEqual(charged, 0)
        casino.settle_all()
        self.assertEqual(self.backend.currency_calls, [("Amara", Currency.CASH, 20 * STAKE_50)])

    def test_payout_is_clamped_to_wallet_limit(self) -> None:
        casino = self.make_casino([(S.CASH,) * 3])
        self.player.balances[Currency.CASH] = INT32_MAX - 10
        casino.pull(self.player, 1, "cash", 1)
        payout = casino.settle("Amara")
        self.assertEqual(self.player.balances[Currency.CASH], INT32_MAX)
        self.assertEqual(payout.cash, STAKE_50 + 10)

    def test_failed_drops_are_reported(self) -> None:
        casino = self.make_casino([(S.LEGENDARY,) * 3])
        self.backend.fail_spawns = True
        casino.pull(self.player, 1, "cash", 1)
        payout = casino.settle("Amara")
        self.assertEqual(payout.items, ())
        self.assertEqual(payout.cash, 5 * STAKE_50)
        self.assertTrue(payout.errors)

    def test_unreadable_level_prices_as_level_one(self) -> None:
        casino = self.make_casino([(S.SKULL, S.CASH, S.EPIC)])
        self.player.level = None
        casino.pull(self.player, 1, "cash", 1)
        self.assertEqual(self.backend.currency_calls, [("Amara", Currency.CASH, -10)])


class ControllerTestCase(CasinoTestCase):
    def make(
        self,
        lines: Sequence[tuple[Symbol, Symbol, Symbol]] = (),
        rules: dict[str, Any] | None = None,
        **settings: Any,
    ) -> SlotController:
        self.casino = self.make_casino(lines, **(rules or {}))
        self.display = FakeDisplay()
        self.stats = Stats()
        self.saved: list[Stats] = []
        self.settings = PlayerSettings(**settings)
        return SlotController(
            LocalLink(self.casino, lambda: self.player),
            self.display,
            settings=lambda: self.settings,
            stats=self.stats,
            on_stats_changed=self.saved.append,
            rng=random.Random(0),
            clock=self.clock,
            log=lambda _: None,
        )

    def run_until_idle(self, controller: SlotController, step: float = 0.05, limit: float = 30.0) -> None:
        end = self.clock.now + limit
        while controller.tick():
            self.clock.now += step
            if self.clock.now > end:
                self.fail("Controller never went idle")

    def final_view(self) -> Any:
        return [view for view in self.display.views if view.status != "Spinning..."][-1]


class ControllerTests(ControllerTestCase):
    def test_losing_spin(self) -> None:
        controller = self.make([(S.SKULL, S.CASH, S.EPIC)])
        controller.pull()
        self.assertTrue(controller.is_spinning)
        self.assertEqual(self.display.views[-1].status, "Spinning...")
        self.assertEqual(self.player.balances[Currency.CASH], 1_000_000 - STAKE_50)

        self.run_until_idle(controller)
        self.assertFalse(self.casino.has_pending)
        self.assertEqual(self.stats.spins, 1)
        self.assertEqual(self.stats.cash_spent, STAKE_50)
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(self.display.hidden, 1)
        self.assertEqual(self.final_view().tone, Tone.LOSE)
        self.assertEqual(tuple(r.payline for r in self.final_view().reels), (S.SKULL, S.CASH, S.EPIC))

    def test_payout_lands_when_the_reels_stop(self) -> None:
        controller = self.make([(S.CASH,) * 3])
        controller.pull()
        self.clock.now += 0.5
        controller.tick()
        self.assertEqual(self.player.balances[Currency.CASH], 1_000_000 - STAKE_50, "no spoilers")
        self.run_until_idle(controller)
        self.assertEqual(self.player.balances[Currency.CASH], 1_000_000 + 19 * STAKE_50)

    def test_reels_animate_before_stopping(self) -> None:
        controller = self.make([(S.VAULT, S.VAULT, S.SKULL)])
        controller.pull()
        self.run_until_idle(controller)
        paylines = {tuple(r.payline for r in view.reels) for view in self.display.views}
        self.assertGreater(len(paylines), 5)

    def test_jackpot(self) -> None:
        controller = self.make([(S.VAULT,) * 3], bet=2)
        controller.pull()
        self.run_until_idle(controller)
        self.assertEqual(self.stats.jackpots, 1)
        self.assertEqual(self.stats.items, {"legendary": 4})
        self.assertEqual(self.final_view().tone, Tone.JACKPOT)
        self.assertIn("4 legendary", self.final_view().status)

    def test_second_pull_skips_the_animation(self) -> None:
        controller = self.make([(S.CASH,) * 3, (S.SKULL,) * 3])
        controller.pull()
        controller.pull()
        self.assertFalse(controller.is_spinning)
        self.assertEqual(self.stats.spins, 1)
        self.assertEqual(self.player.balances[Currency.CASH], 1_000_000 + 19 * STAKE_50)
        controller.pull()
        self.assertTrue(controller.is_spinning)

    def test_refusals_are_shown_and_hidden(self) -> None:
        controller = self.make()
        self.player.near_machine = False
        controller.pull()
        self.assertIn("vending machine", self.display.views[-1].status)
        self.assertEqual(self.display.views[-1].tone, Tone.ERROR)
        self.clock.now += MESSAGE_SECONDS
        self.assertFalse(controller.tick())
        self.assertEqual(self.display.hidden, 1)

    def test_free_play_footer(self) -> None:
        controller = self.make([(S.CASH,) * 3], rules={"free_play": True})
        controller.pull()
        self.run_until_idle(controller)
        self.assertIn("FREE PLAY", self.final_view().footer)
        self.assertEqual(self.stats.cash_spent, 0)

    def test_failed_payouts_show_an_error(self) -> None:
        controller = self.make([(S.LEGENDARY,) * 3])
        self.backend.fail_spawns = True
        controller.pull()
        self.run_until_idle(controller)
        self.assertEqual(self.final_view().tone, Tone.ERROR)
        self.assertIn("payout failed", self.final_view().status)
        self.assertEqual(self.stats.items, {})

    def test_shutdown_pays_out_and_hides(self) -> None:
        controller = self.make([(S.CASH,) * 3])
        controller.pull()
        controller.shutdown()
        self.assertFalse(controller.needs_tick)
        self.assertEqual(self.stats.spins, 1)
        self.assertFalse(self.casino.has_pending)
        self.assertEqual(self.display.hidden, 1)

    def test_stats_saving_errors_do_not_break_spins(self) -> None:
        controller = self.make([(S.CASH,) * 3])

        def explode(_: Stats) -> None:
            raise OSError("disk full")

        controller.on_stats_changed = explode
        controller.pull()
        self.run_until_idle(controller)
        self.assertEqual(self.player.balances[Currency.CASH], 1_000_000 + 19 * STAKE_50)

    def test_instant_spins(self) -> None:
        controller = self.make([(S.RARE,) * 3], spin_seconds=0)
        controller.pull()
        self.assertFalse(controller.is_spinning)
        self.assertEqual(len(self.backend.spawned), 1)


class ExpectedPayoutTests(unittest.TestCase):
    def test_matches_the_spin(self) -> None:
        casino = Casino(FakeBackend(), HouseRules, rng=ScriptedRandom([(S.VAULT,) * 3]), log=lambda _: None)
        result, _ = casino.pull(FakePlayer("Vex"), 1, "eridium", 1)
        self.assertEqual(expected_payout(result), Payout(0, 400, (Tier.LEGENDARY,) * 3))


if __name__ == "__main__":
    unittest.main()
