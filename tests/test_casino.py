import random
import unittest
from collections.abc import Sequence
from typing import Any

from borderlands_gamble.casino import INT32_MAX, PlaySettings, SlotController, Tone
from borderlands_gamble.machines import LOOT_SLOTS, spin_cost
from borderlands_gamble.slots import Currency, Symbol, Tier
from borderlands_gamble.stats import Stats

S = Symbol


class ScriptedRandom(random.Random):
    """A seeded RNG, except reel draws come from a script of lines."""

    def __init__(self, lines: Sequence[tuple[Symbol, Symbol, Symbol]] = ()) -> None:
        super().__init__(0)
        self.queue = [symbol for line in lines for symbol in line]

    def choices(self, population: Any, *args: Any, **kwargs: Any) -> list[Any]:
        if self.queue and all(isinstance(x, Symbol) for x in population):
            return [self.queue.pop(0)]
        return super().choices(population, *args, **kwargs)


class FakeBackend:
    def __init__(self) -> None:
        self.cant_play: str | None = None
        self.near_machine = True
        self.level: int | None = 50
        self.balances = {Currency.CASH: 1_000_000, Currency.ERIDIUM: 500}
        self.ignore_charges = False
        self.fail_spawns = False
        self.currency_calls: list[tuple[Currency, int]] = []
        self.spawned: list[tuple[str, int, int, int]] = []

    def check_can_play(self) -> str | None:
        return self.cant_play

    def is_near_machine(self, radius: float) -> bool:
        return self.near_machine

    def player_level(self) -> int | None:
        return self.level

    def get_balance(self, currency: Currency) -> int | None:
        return self.balances.get(currency)

    def add_currency(self, currency: Currency, amount: int) -> None:
        self.currency_calls.append((currency, amount))
        if amount < 0 and self.ignore_charges:
            return
        self.balances[currency] += amount

    def spawn_item(self, pool: str, level: int, index: int, count: int) -> None:
        if self.fail_spawns:
            raise RuntimeError("no item pool store")
        self.spawned.append((pool, level, index, count))


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


class ControllerTestCase(unittest.TestCase):
    def make(self, lines: Sequence[tuple[Symbol, Symbol, Symbol]] = (), **settings: Any) -> SlotController:
        self.backend = FakeBackend()
        self.display = FakeDisplay()
        self.clock = FakeClock()
        self.stats = Stats()
        self.saved: list[Stats] = []
        self.settings = PlaySettings(**settings)
        return SlotController(
            self.backend,
            self.display,
            settings=lambda: self.settings,
            stats=self.stats,
            on_stats_changed=self.saved.append,
            rng=ScriptedRandom(lines),
            clock=self.clock,
            log=lambda _: None,
        )

    def run_until_idle(self, controller: SlotController, step: float = 0.05, limit: float = 30.0) -> None:
        end = self.clock.now + limit
        while controller.tick():
            self.clock.now += step
            if self.clock.now > end:
                self.fail("Controller never went idle")

    @property
    def last_view(self) -> Any:
        return self.display.views[-1]


class RefusalTests(ControllerTestCase):
    def test_cant_play(self) -> None:
        controller = self.make()
        self.backend.cant_play = "Slots are host-only in co-op."
        controller.pull()
        self.assertEqual(self.last_view.status, "Slots are host-only in co-op.")
        self.assertEqual(self.last_view.tone, Tone.ERROR)
        self.assertEqual(self.backend.currency_calls, [])
        self.assertFalse(controller.is_spinning)

    def test_needs_a_machine(self) -> None:
        controller = self.make()
        self.backend.near_machine = False
        controller.pull()
        self.assertIn("vending machine", self.last_view.status)
        self.assertEqual(self.backend.currency_calls, [])

    def test_machine_not_required(self) -> None:
        controller = self.make([(S.SKULL, S.CASH, S.EPIC)], require_machine=False)
        self.backend.near_machine = False
        controller.pull()
        self.assertTrue(controller.is_spinning)

    def test_not_enough_money(self) -> None:
        controller = self.make()
        self.backend.balances[Currency.CASH] = 5
        controller.pull()
        self.assertIn("Not enough cash", self.last_view.status)
        self.assertEqual(self.backend.currency_calls, [])

    def test_charge_that_does_not_apply_voids_the_spin(self) -> None:
        controller = self.make([(S.VAULT,) * 3])
        self.backend.ignore_charges = True
        controller.pull()
        self.assertFalse(controller.is_spinning)
        self.assertIn("didn't go through", self.last_view.status)
        self.assertEqual(self.stats.spins, 0)

    def test_unknown_machine(self) -> None:
        controller = self.make(machine_key="roulette")
        controller.pull()
        self.assertEqual(self.last_view.tone, Tone.ERROR)

    def test_messages_hide_themselves(self) -> None:
        controller = self.make()
        self.backend.near_machine = False
        controller.pull()
        self.run_until_idle(controller)
        self.assertEqual(self.display.hidden, 1)


class SpinTests(ControllerTestCase):
    def test_losing_spin(self) -> None:
        controller = self.make([(S.SKULL, S.CASH, S.EPIC)])
        stake = spin_cost(LOOT_SLOTS, 50)
        controller.pull()
        self.assertEqual(self.backend.currency_calls, [(Currency.CASH, -stake)])
        self.assertTrue(controller.is_spinning)
        self.assertEqual(self.last_view.status, "Spinning...")

        self.run_until_idle(controller)
        self.assertEqual(self.backend.balances[Currency.CASH], 1_000_000 - stake)
        self.assertEqual(self.stats.spins, 1)
        self.assertEqual(self.stats.cash_spent, stake)
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(self.display.hidden, 1)

        final = [view for view in self.display.views if view.status != "Spinning..."][-1]
        self.assertEqual(final.tone, Tone.LOSE)
        self.assertEqual(tuple(reel.payline for reel in final.reels), (S.SKULL, S.CASH, S.EPIC))
        self.assertTrue(all(reel.stopped for reel in final.reels))

    def test_reels_animate_before_stopping(self) -> None:
        controller = self.make([(S.VAULT, S.VAULT, S.SKULL)])
        controller.pull()
        self.run_until_idle(controller)
        paylines = {tuple(reel.payline for reel in view.reels) for view in self.display.views}
        self.assertGreater(len(paylines), 5)

    def test_jackpot(self) -> None:
        controller = self.make([(S.VAULT,) * 3], bet=2)
        controller.pull()
        self.run_until_idle(controller)

        stake = 2 * 2600
        self.assertEqual(self.backend.currency_calls, [(Currency.CASH, -stake), (Currency.CASH, 50 * stake)])
        self.assertEqual(len(self.backend.spawned), 4)  # 2 legendaries, doubled by the bet
        self.assertTrue(all(pool.endswith("_05_legendary") for pool, *_ in self.backend.spawned))
        self.assertEqual([(i, n) for _, _, i, n in self.backend.spawned], [(0, 4), (1, 4), (2, 4), (3, 4)])
        self.assertEqual(self.stats.jackpots, 1)
        self.assertEqual(self.stats.items, {"legendary": 4})

        final = [view for view in self.display.views if view.status != "Spinning..."][-1]
        self.assertEqual(final.tone, Tone.JACKPOT)
        self.assertIn("JACKPOT!", final.status)
        self.assertIn("4 legendary", final.status)

    def test_loot_uses_player_level_or_override(self) -> None:
        controller = self.make([(S.RARE,) * 3])
        controller.pull()
        self.run_until_idle(controller)
        self.assertEqual(self.backend.spawned[0][1], 50)

        controller = self.make([(S.RARE,) * 3], loot_level=12)
        controller.pull()
        self.run_until_idle(controller)
        self.assertEqual(self.backend.spawned[0][1], 12)

    def test_eridium_machine_pays_eridium(self) -> None:
        controller = self.make([(S.ERIDIUM,) * 3], machine_key="eridium")
        controller.pull()
        self.run_until_idle(controller)
        self.assertEqual(self.backend.currency_calls, [(Currency.ERIDIUM, -10), (Currency.ERIDIUM, 150)])
        self.assertEqual(self.stats.eridium_spent, 10)
        self.assertEqual(self.stats.eridium_won, 150)

    def test_cash_machine_eridium_bonus(self) -> None:
        controller = self.make([(S.ERIDIUM,) * 3])
        controller.pull()
        self.run_until_idle(controller)
        self.assertEqual(self.backend.currency_calls[-1], (Currency.ERIDIUM, 40))

    def test_second_pull_skips_the_animation(self) -> None:
        controller = self.make([(S.CASH,) * 3, (S.SKULL,) * 3])
        controller.pull()
        self.assertTrue(controller.is_spinning)
        controller.pull()
        self.assertFalse(controller.is_spinning)
        self.assertEqual(self.stats.spins, 1)
        self.assertEqual(self.backend.currency_calls[-1], (Currency.CASH, 20 * 2600))

        controller.pull()
        self.assertTrue(controller.is_spinning)

    def test_free_play_does_not_charge(self) -> None:
        controller = self.make([(S.CASH,) * 3], free_play=True)
        controller.pull()
        self.run_until_idle(controller)
        self.assertEqual(self.backend.currency_calls, [(Currency.CASH, 20 * 2600)])
        self.assertEqual(self.stats.cash_spent, 0)
        self.assertIn("FREE PLAY", self.last_view.footer)

    def test_payout_is_clamped_to_wallet_limit(self) -> None:
        controller = self.make([(S.CASH,) * 3])
        self.backend.balances[Currency.CASH] = INT32_MAX - 10
        controller.pull()
        self.run_until_idle(controller)
        self.assertEqual(self.backend.balances[Currency.CASH], INT32_MAX)
        self.assertEqual(self.stats.cash_won, 2600 + 10)

    def test_failed_drops_are_reported(self) -> None:
        controller = self.make([(S.LEGENDARY,) * 3])
        self.backend.fail_spawns = True
        controller.pull()
        self.run_until_idle(controller)
        final = [view for view in self.display.views if view.status != "Spinning..."][-1]
        self.assertEqual(final.tone, Tone.ERROR)
        self.assertIn("payout failed", final.status)
        self.assertEqual(self.stats.items, {})
        self.assertEqual(self.stats.cash_won, 5 * 2600)

    def test_unreadable_level_prices_as_level_one(self) -> None:
        controller = self.make([(S.SKULL, S.CASH, S.EPIC)])
        self.backend.level = None
        controller.pull()
        self.assertEqual(self.backend.currency_calls, [(Currency.CASH, -10)])

    def test_shutdown_pays_out_and_hides(self) -> None:
        controller = self.make([(S.CASH,) * 3])
        controller.pull()
        controller.shutdown()
        self.assertFalse(controller.needs_tick)
        self.assertEqual(self.stats.spins, 1)
        self.assertEqual(self.backend.currency_calls[-1], (Currency.CASH, 20 * 2600))
        self.assertEqual(self.display.hidden, 1)

    def test_stats_saving_errors_do_not_lose_payouts(self) -> None:
        controller = self.make([(S.CASH,) * 3])

        def explode(_: Stats) -> None:
            raise OSError("disk full")

        controller.on_stats_changed = explode
        controller.pull()
        self.run_until_idle(controller)
        self.assertEqual(self.backend.currency_calls[-1], (Currency.CASH, 20 * 2600))

    def test_instant_spins(self) -> None:
        controller = self.make([(S.RARE,) * 3], spin_seconds=0)
        controller.pull()
        self.assertFalse(controller.is_spinning)
        self.assertEqual(len(self.backend.spawned), 1)
        self.assertEqual(self.backend.spawned[0][0].split("_")[-1], Tier.RARE.value)


if __name__ == "__main__":
    unittest.main()
