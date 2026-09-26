"""
What the slot machine menu shows, and how its clicks and key presses turn into actions.

Nothing here touches the game. `menu.py` draws it with UMG widgets, and feeds it input.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .machines import BET_MULTIPLIERS, LUCK_PRESETS, MACHINES
from .slots import SYMBOL_COLORS, Currency, describe_loot, exact_odds, format_amount, scale_prize

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from .slots import Machine, Odds, Prize
    from .stats import Stats

RGBA = tuple[float, float, float, float]

WHITE: RGBA = (0.92, 0.92, 0.92, 1.0)


class MenuAction(Enum):
    PULL = "pull"
    MACHINE = "machine"
    BET_UP = "bet_up"
    BET_DOWN = "bet_down"
    LEAVE = "leave"


# Keys the menu watches while it's open, by their Unreal names. The mod's keybinds can't be relied on
# while a menu has focus, so these get polled instead.
MENU_KEYS: dict[str, MenuAction] = {
    "SpaceBar": MenuAction.PULL,
    "Enter": MenuAction.PULL,
    "Gamepad_FaceButton_Bottom": MenuAction.PULL,
    "Left": MenuAction.MACHINE,
    "Right": MenuAction.MACHINE,
    "Gamepad_DPad_Left": MenuAction.MACHINE,
    "Gamepad_DPad_Right": MenuAction.MACHINE,
    "Gamepad_FaceButton_Left": MenuAction.MACHINE,
    "Up": MenuAction.BET_UP,
    "Down": MenuAction.BET_DOWN,
    "Gamepad_DPad_Up": MenuAction.BET_UP,
    "Gamepad_DPad_Down": MenuAction.BET_DOWN,
    "Gamepad_FaceButton_Top": MenuAction.BET_UP,
    "Escape": MenuAction.LEAVE,
    "Gamepad_FaceButton_Right": MenuAction.LEAVE,
}

# Keys that act when they're let go rather than pressed. Leaving when Escape comes back up means the
# game can't also catch the press, and open its pause menu the moment ours closes.
ACT_ON_RELEASE: frozenset[str] = frozenset({"Escape"})

# The same action arriving again sooner than this is the same press, e.g. seen both as a key and as
# a click on the focused button.
REPEAT_GUARD = 0.25

MACHINE_TAGLINES: dict[str, str] = {
    "cash": "Costs cash, priced for your level. Pays cash, eridium, and loot.",
    "eridium": "Costs eridium. Pays out rarer loot, more often.",
}


class KeyWatcher:
    """Turns key states, polled every frame, into key presses."""

    def __init__(self, keys: Iterable[str], act_on_release: Iterable[str] = ()) -> None:
        self.keys = tuple(dict.fromkeys(keys))
        self.act_on_release = frozenset(act_on_release)
        self._down: set[str] = set()
        self._armed: set[str] = set()

    def prime(self, down: Iterable[str]) -> None:
        """Starts watching. Keys already held (e.g. the one that opened the menu) don't count."""
        self._down = set(down) & set(self.keys)
        self._armed.clear()

    def update(self, down: Iterable[str]) -> list[str]:
        """
        Takes the keys held this frame.

        Returns:
            The keys that just got pressed, or for release keys, let go.
        """
        now_down = set(down) & set(self.keys)
        pressed: list[str] = []
        for key in self.keys:
            was_down = key in self._down
            is_down = key in now_down
            if key in self.act_on_release:
                if is_down and not was_down:
                    self._armed.add(key)
                elif was_down and not is_down and key in self._armed:
                    self._armed.discard(key)
                    pressed.append(key)
            elif is_down and not was_down:
                pressed.append(key)
        self._down = now_down
        return pressed


class ActionGate:
    """Lets each action through at most once per `REPEAT_GUARD` seconds."""

    def __init__(self, clock: Callable[[], float], guard: float = REPEAT_GUARD) -> None:
        self.clock = clock
        self.guard = guard
        self._last: dict[MenuAction, float] = {}

    def allow(self, action: MenuAction) -> bool:
        now = self.clock()
        last = self._last.get(action)
        if last is not None and now - last < self.guard:
            return False
        self._last[action] = now
        return True


def next_bet(bet: int, step: int) -> int:
    """Steps through the bet multipliers, wrapping around."""
    if bet not in BET_MULTIPLIERS:
        return BET_MULTIPLIERS[0]
    return BET_MULTIPLIERS[(BET_MULTIPLIERS.index(bet) + step) % len(BET_MULTIPLIERS)]


def next_machine(machine_key: str) -> str:
    """Steps to the next machine, wrapping around."""
    keys = list(MACHINES)
    if machine_key not in keys:
        return keys[0]
    return keys[(keys.index(machine_key) + 1) % len(keys)]


# ==================================================================================================
# What the menu shows


@dataclass(frozen=True)
class PaytableRow:
    pattern: str
    color: RGBA
    odds: str
    pays: str


@dataclass(frozen=True)
class MenuInfo:
    tagline: str
    pull_label: str
    bet_label: str
    machine_label: str
    wallet: str
    paytable_title: str
    paytable: tuple[PaytableRow, ...]
    summary: str
    note: str
    lifetime: str
    hints: str
    # True while a pull is in flight, when switching machine or bet isn't allowed
    busy: bool


@functools.cache
def _odds(machine_key: str, luck_name: str) -> Odds:
    return exact_odds(MACHINES[machine_key].with_luck(LUCK_PRESETS[luck_name]))


def _one_in(probability: float) -> str:
    if probability <= 0:
        return "never"
    return f"1 in {1 / probability:,.0f}" if probability < 0.5 else f"{probability:.0%}"


def _currency_name(currency: Currency) -> str:
    return "cash" if currency is Currency.CASH else "eridium"


def _pays(machine: Machine, prize: Prize, *, bet: int, price: int | None) -> str:
    """Describes what a paytable row pays at the given bet, in money if the price is known."""
    payout, eridium, loot = scale_prize(prize, price if price is not None else 0, bet)
    parts: list[str] = []
    if prize.payout:
        if price is None:
            parts.append(f"{prize.payout:g}x stake")
        else:
            parts.append(format_amount(machine.currency, payout))
    if eridium:
        parts.append(f"{eridium:,} eridium")
    if loot:
        parts.append(describe_loot(loot))
    return ", ".join(parts) if parts else "nothing"


def _wallet(wallet: Mapping[Currency, int | None]) -> str:
    def show(currency: Currency) -> str:
        amount = wallet.get(currency)
        if amount is None:
            return "?"
        return format_amount(Currency.CASH, amount) if currency is Currency.CASH else f"{amount:,}"

    return f"Cash {show(Currency.CASH)}     Eridium {show(Currency.ERIDIUM)}"


def _lifetime(stats: Stats) -> str:
    if stats.spins == 0:
        return "No pulls yet. Good luck!"
    items = sum(stats.items.values())
    parts = [
        f"Lifetime: {stats.spins:,} pulls",
        f"net {format_amount(Currency.CASH, stats.cash_net)}",
        f"{stats.eridium_net:+,} eridium",
        f"{items:,} item{'s' if items != 1 else ''}",
    ]
    return "   |   ".join(parts)


def build_menu_info(
    machine_key: str,
    *,
    bet: int,
    price: int | None,
    free_play: bool,
    luck_name: str,
    is_host: bool,
    wallet: Mapping[Currency, int | None],
    stats: Stats,
    busy: bool,
    spinning: bool,
    pull_key: str | None,
) -> MenuInfo:
    """
    Works out what the menu shows.

    Args:
        machine_key: The machine the player has picked.
        bet: The player's bet multiplier.
        price: What a pull costs at that bet, or None if it isn't known yet (a co-op client only
               learns the host's prices from its first pull).
        free_play: True if pulls cost nothing.
        luck_name: The luck preset to show odds for.
        is_host: False when playing in someone else's co-op game, whose house rules apply.
        wallet: The player's balances, or None where they couldn't be read.
        stats: The player's lifetime stats.
        busy: True while a pull is in flight.
        spinning: True while the reels are spinning, when pulling again skips to the result.
        pull_key: The name of a key that pulls the lever, for the hints.
    Returns:
        The menu's contents.
    """
    machine = MACHINES[machine_key]
    other = MACHINES[next_machine(machine_key)]

    if spinning:
        pull_label = "SKIP"
    elif free_play:
        pull_label = "PULL THE LEVER  (FREE)"
    elif price is not None:
        pull_label = f"PULL THE LEVER  ({format_amount(machine.currency, price)})"
    else:
        pull_label = "PULL THE LEVER"

    odds = _odds(machine_key, luck_name)
    rows = []
    for pattern, prize, probability in odds.rows:
        color = SYMBOL_COLORS[next(iter(pattern.symbols))] if len(pattern.symbols) == 1 else WHITE
        rows.append(
            PaytableRow(
                pattern.describe(),
                color,
                _one_in(probability),
                _pays(machine, prize, bet=bet, price=None if free_play else price),
            ),
        )

    currency = _currency_name(machine.currency)
    summary = f"Wins {odds.hit_rate:.0%} of pulls. Pays back {odds.return_to_player:.0%} of {currency} staked"
    summary += ", plus loot." if odds.items_per_spin else "."
    note = (
        f"Odds at {luck_name} luck."
        if is_host
        else f"In co-op the host's house rules apply. Odds shown at your {luck_name} luck."
    )

    hints = ["Space: pull" if pull_key is None else f"Space or {pull_key}: pull"]
    hints += ["Left/Right: machine", "Up/Down: bet", "Esc: leave"]

    return MenuInfo(
        tagline=MACHINE_TAGLINES.get(machine_key, ""),
        pull_label=pull_label,
        bet_label=f"BET  x{bet}",
        machine_label=f"PLAY {other.name.upper()}",
        wallet=_wallet(wallet),
        paytable_title=f"PAYTABLE  (x{bet} bet)",
        paytable=tuple(rows),
        summary=summary,
        note=note,
        lifetime=_lifetime(stats),
        hints="    ".join(hints),
        busy=busy,
    )
