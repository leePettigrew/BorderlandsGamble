import unittest
from typing import Any

from borderlands_gamble.leaderboard import Drop, Leaderboard, SpinRecord
from borderlands_gamble.machines import BET_MULTIPLIERS
from borderlands_gamble.menu_model import (
    ACT_ON_RELEASE,
    GOLD,
    GREY,
    MENU_KEYS,
    PANEL_ROWS,
    TIER_COLORS,
    WHITE,
    ActionGate,
    KeyWatcher,
    MenuAction,
    build_menu_info,
    drops_label,
    next_bet,
    next_loot_type,
    next_machine,
)
from borderlands_gamble.menu_model import (
    leaderboard_rows as board_rows,
)
from borderlands_gamble.slots import SYMBOL_COLORS, Currency, Symbol, Tier
from borderlands_gamble.stats import Stats

S = Symbol


class KeyWatcherTests(unittest.TestCase):
    def test_presses(self) -> None:
        keys = KeyWatcher(["SpaceBar", "Up"])
        keys.prime([])
        self.assertEqual(keys.update(["SpaceBar"]), ["SpaceBar"])
        # Held down isn't another press
        self.assertEqual(keys.update(["SpaceBar"]), [])
        self.assertEqual(keys.update(["SpaceBar", "Up", "F12"]), ["Up"])
        self.assertEqual(keys.update([]), [])
        self.assertEqual(keys.update(["SpaceBar"]), ["SpaceBar"])

    def test_keys_held_when_opened_dont_count(self) -> None:
        keys = KeyWatcher(["SpaceBar"])
        keys.prime(["SpaceBar"])
        self.assertEqual(keys.update(["SpaceBar"]), [])
        self.assertEqual(keys.update([]), [])
        self.assertEqual(keys.update(["SpaceBar"]), ["SpaceBar"])

    def test_release_keys(self) -> None:
        keys = KeyWatcher(["Escape"], act_on_release=["Escape"])
        keys.prime(["Escape"])
        # Still held from before the menu opened, so letting go does nothing
        self.assertEqual(keys.update([]), [])
        self.assertEqual(keys.update(["Escape"]), [])
        self.assertEqual(keys.update(["Escape"]), [])
        self.assertEqual(keys.update([]), ["Escape"])
        self.assertEqual(keys.update([]), [])

    def test_menu_keys(self) -> None:
        self.assertEqual(MENU_KEYS["Escape"], MenuAction.LEAVE)
        self.assertEqual(MENU_KEYS["SpaceBar"], MenuAction.PULL)
        # Keys the game would act on in a harmful way are never used
        for key in (
            "Enter",
            "Gamepad_FaceButton_Left",
            "Gamepad_LeftShoulder",
            "Gamepad_RightShoulder",
            "E",
            "F",
        ):
            self.assertNotIn(key, MENU_KEYS)
        self.assertTrue(ACT_ON_RELEASE.issubset(MENU_KEYS))
        self.assertEqual(set(MENU_KEYS.values()), set(MenuAction))


class ActionGateTests(unittest.TestCase):
    def test_repeats_are_dropped(self) -> None:
        now = [10.0]
        gate = ActionGate(lambda: now[0], guard=0.25)
        self.assertTrue(gate.allow(MenuAction.PULL))
        self.assertFalse(gate.allow(MenuAction.PULL))
        self.assertTrue(gate.allow(MenuAction.BET_UP))
        now[0] += 0.3
        self.assertTrue(gate.allow(MenuAction.PULL))


class ChoiceTests(unittest.TestCase):
    def test_next_bet(self) -> None:
        self.assertEqual(next_bet(BET_MULTIPLIERS[0], 1), BET_MULTIPLIERS[1])
        self.assertEqual(next_bet(BET_MULTIPLIERS[-1], 1), BET_MULTIPLIERS[0])
        self.assertEqual(next_bet(BET_MULTIPLIERS[0], -1), BET_MULTIPLIERS[-1])
        self.assertEqual(next_bet(3, 1), BET_MULTIPLIERS[0])

    def test_next_loot_type(self) -> None:
        self.assertEqual(next_loot_type("any", 1), "guns")
        self.assertEqual(next_loot_type("any", -1), "enhancements")
        self.assertEqual(next_loot_type("enhancements", 1), "any")
        self.assertEqual(next_loot_type("nope", 1), "any")

    def test_drops_label(self) -> None:
        self.assertEqual(drops_label("any"), "ANYTHING")
        self.assertEqual(drops_label("shotguns"), "SHOTGUNS  +50%")
        self.assertEqual(drops_label("class_mods"), "CLASS MODS  +100%")

    def test_next_machine(self) -> None:
        self.assertEqual(next_machine("cash"), "eridium")
        self.assertEqual(next_machine("eridium"), "cash")
        self.assertEqual(next_machine("nope"), "cash")


class MenuInfoTests(unittest.TestCase):
    def info(self, **kwargs: Any) -> Any:
        args: dict[str, Any] = {
            "machine_key": "cash",
            "bet": 1,
            "price": 2600,
            "free_play": False,
            "luck_name": "Fair",
            "is_host": True,
            "wallet": {Currency.CASH: 1_234_567, Currency.ERIDIUM: 230},
            "stats": Stats(),
            "busy": False,
            "spinning": False,
            "pull_key": "F8",
        }
        args.update(kwargs)
        return build_menu_info(**args)

    def test_basics(self) -> None:
        info = self.info()
        self.assertEqual(info.pull_label, "PULL THE LEVER  ($2,600)")
        self.assertEqual(info.bet_label, "BET  x1")
        self.assertEqual(info.machine_label, "PLAY ERIDIUM SLOTS")
        self.assertEqual(info.drops_label, "DROPS: ANYTHING")
        self.assertEqual(info.wallet, "Cash $1,234,567     Eridium 230")
        self.assertEqual(info.lifetime, "No pulls yet. Good luck!")
        self.assertIn("F8", info.hints)
        self.assertIn("Odds at Fair luck", info.note)
        self.assertFalse(info.busy)

    def test_paytable(self) -> None:
        info = self.info(bet=2, price=5200)
        jackpot = info.paytable[0]
        self.assertEqual(jackpot.pattern, "3x VAULT")
        self.assertEqual(jackpot.pays, "$260,000, 4 legendary")
        self.assertEqual(jackpot.color, SYMBOL_COLORS[Symbol.VAULT])
        mixed = next(row for row in info.paytable if row.pattern.startswith("any"))
        self.assertEqual(mixed.color, WHITE)
        skulls = next(row for row in info.paytable if row.pattern == "3x SKULL")
        self.assertEqual(skulls.pays, "nothing")
        self.assertEqual(info.paytable_title, "PAYTABLE  (x2 bet)")
        self.assertTrue(all(row.odds.startswith("1 in") or row.odds.endswith("%") for row in info.paytable))

    def test_loot_type_names_the_loot(self) -> None:
        info = self.info(loot_type_key="snipers", price=3900)
        self.assertEqual(info.paytable[0].pays, "$195,000, 2 legendary sniper rifles")
        self.assertEqual(info.drops_label, "DROPS: SNIPER RIFLES  +50%")

    def test_unknown_price(self) -> None:
        info = self.info(price=None, is_host=False)
        self.assertEqual(info.pull_label, "PULL THE LEVER")
        self.assertEqual(info.paytable[0].pays, "50x stake, 2 legendary")
        self.assertIn("host", info.note)

    def test_free_play_and_spinning(self) -> None:
        self.assertEqual(self.info(free_play=True).pull_label, "PULL THE LEVER  (FREE)")
        self.assertEqual(self.info(spinning=True, busy=True).pull_label, "SKIP")
        self.assertTrue(self.info(busy=True).busy)

    def test_eridium_machine(self) -> None:
        info = self.info(machine_key="eridium", price=10)
        self.assertIn("eridium", info.tagline.lower())
        self.assertEqual(info.pull_label, "PULL THE LEVER  (10 eridium)")
        self.assertEqual(info.machine_label, "PLAY LOOT SLOTS")
        self.assertIn("eridium staked", info.summary)

    def test_unreadable_wallet_and_stats(self) -> None:
        stats = Stats(spins=3, cash_spent=100, cash_won=40, eridium_won=5, items={"rare": 1})
        info = self.info(wallet={Currency.CASH: None}, stats=stats, pull_key=None)
        self.assertEqual(info.wallet, "Cash ?     Eridium ?")
        self.assertEqual(info.lifetime, "Lifetime: 3 pulls   |   net -$60   |   +5 eridium   |   1 item")
        self.assertNotIn("F8", info.hints)


if __name__ == "__main__":
    unittest.main()


class LeaderboardPageTests(unittest.TestCase):
    def info(self, board: Leaderboard | None, show: bool) -> Any:
        return build_menu_info(
            "cash",
            bet=1,
            price=50_000,
            free_play=False,
            luck_name="Fair",
            is_host=True,
            wallet={},
            stats=Stats(),
            busy=False,
            spinning=False,
            pull_key=None,
            leaderboard=board,
            show_leaderboard=show,
        )

    def test_button_switches_pages(self) -> None:
        board = Leaderboard()
        self.assertEqual(MENU_KEYS["Gamepad_LeftThumbstick"], MenuAction.BOARD)
        paytable = self.info(board, show=False)
        self.assertEqual(
            (paytable.board_label, paytable.paytable_title), ("LEADERBOARD", "PAYTABLE  (x1 bet)")
        )
        page = self.info(board, show=True)
        self.assertEqual((page.board_label, page.paytable_title), ("PAYTABLE", "LEADERBOARD"))
        self.assertEqual([row.pattern for row in page.paytable], ["No pulls yet. Pull the lever!"])
        # Without a leaderboard there's only the paytable
        self.assertEqual(self.info(None, show=True).paytable_title, "PAYTABLE  (x1 bet)")

    def test_rows(self) -> None:
        board = Leaderboard()
        jackpot = (Drop(Tier.LEGENDARY, "sg"), Drop(Tier.LEGENDARY, "ar"))
        board.record(SpinRecord("Zane", "cash", (S.VAULT,) * 3, 1, 50_000, 50_000, 2_500_000, 0, jackpot))
        board.record(SpinRecord("Moze", "cash", (S.SKULL,) * 3, 1, 50_000, 50_000))
        board.record(SpinRecord("Moze", "eridium", (S.ERIDIUM,) * 3, 1, 10, 10, 0, 150))

        rows = [(row.pattern, row.odds, row.pays) for row in self.info(board, show=True).paytable]
        self.assertEqual(
            rows,
            [
                ("1. Zane", "1 won, 0 lost", "+$2.45M"),
                ("      2 legendary", "", ""),
                ("2. Moze", "1 won, 1 lost", "-$50k"),
                ("      no items yet", "", "+140 eridium"),
                ("", "", ""),
                ("RECENT PULLS", "", ""),
                ("Moze", "Eridium haul!", "+140 eridium"),
                ("Moze", "no luck", "-$50k"),
                ("Zane", "legendary shotgun +1", "+$2.45M"),
            ],
        )
        colors = [row.color for row in self.info(board, show=True).paytable]
        self.assertEqual(colors[0], GOLD)
        self.assertEqual(colors[1], TIER_COLORS[Tier.LEGENDARY])
        self.assertEqual(colors[7], GREY)
        self.assertEqual(colors[8], TIER_COLORS[Tier.LEGENDARY])

    def test_fits_the_panel(self) -> None:
        board = Leaderboard()
        for idx in range(40):
            board.record(SpinRecord(f"Player {idx % 6}", "cash", (S.SKULL,) * 3, 1, 1000 + idx, 1000))
        rows = self.info(board, show=True).paytable
        self.assertEqual(len(rows), PANEL_ROWS)
        self.assertEqual(sum(1 for row in rows if row.pattern.startswith(("1.", "2.", "3.", "4.", "5."))), 4)


class OutcomeTests(unittest.TestCase):
    def test_shortens_to_fit(self) -> None:
        def outcome(*drops: Drop) -> str:
            board = Leaderboard()
            board.record(SpinRecord("Zane", "cash", (S.VAULT,) * 3, 10, 1, 1, 50, 0, drops))
            return board_rows(board)[-1].odds

        sniper = Drop(Tier.LEGENDARY, "sr")
        self.assertEqual(outcome(sniper), "legendary sniper rifle")
        self.assertEqual(len(outcome(Drop(Tier.LEGENDARY, "ar"))), 23)
        self.assertEqual(outcome(Drop(Tier.EPIC, "sg"), Drop(Tier.EPIC, "sg")), "2 epic shotguns")
        grenade = Drop(Tier.RARE, "grenade_gadgets")
        self.assertEqual(outcome(Drop(Tier.RARE, "sg"), grenade), "rare shotgun +1")
        self.assertEqual(outcome(sniper, sniper, Drop(Tier.EPIC, "hw")), "2 legendary, 1 epic")
