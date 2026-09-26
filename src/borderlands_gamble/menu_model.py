"""
What the slot machine menu shows, and how its clicks and key presses turn into actions.

Nothing here touches the game. `menu.py` draws it with UMG widgets, and feeds it input.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .leaderboard import compact_amount, items_summary
from .loot import DEFAULT_LOOT_TYPE, LOOT_TYPES, loot_type
from .machines import BET_MULTIPLIERS, LUCK_PRESETS, MACHINES
from .slots import (
    SYMBOL_COLORS,
    Currency,
    Symbol,
    Tier,
    describe_loot,
    exact_odds,
    format_amount,
    scale_prize,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from .leaderboard import Leaderboard, SpinRecord
    from .slots import Machine, Odds, Prize
    from .stats import Stats

RGBA = tuple[float, float, float, float]

WHITE: RGBA = (0.92, 0.92, 0.92, 1.0)
GREY: RGBA = (0.62, 0.62, 0.62, 1.0)
GOLD: RGBA = SYMBOL_COLORS[Symbol.VAULT]
TIER_COLORS: dict[Tier, RGBA] = {
    Tier.RARE: SYMBOL_COLORS[Symbol.RARE],
    Tier.EPIC: SYMBOL_COLORS[Symbol.EPIC],
    Tier.LEGENDARY: SYMBOL_COLORS[Symbol.LEGENDARY],
}

# How many rows the right hand panel has, for the paytable or the leaderboard
PANEL_ROWS = 14
# Players shown on the menu's leaderboard. Co-op has at most four; the console lists everyone
BOARD_PLAYERS = 4
# Longest description of what a pull won that fits its column, next to an amount like "+140 eridium".
# Every single item's name fits, e.g. "legendary assault rifle"
OUTCOME_LENGTH = 24


class MenuAction(Enum):
    PULL = "pull"
    MACHINE = "machine"
    LOOT_NEXT = "loot_next"
    LOOT_PREV = "loot_prev"
    BET_UP = "bet_up"
    BET_DOWN = "bet_down"
    LEAVE = "leave"
    BOARD = "board"


# Keys the menu watches while it's open, by their Unreal names. The SDK doesn't run keybinds while
# the mouse cursor is showing, so these get polled instead. The game still sees them too, so they're
# picked to do nothing harmful in game: no fire, grenade, action skill, or "use" buttons.
MENU_KEYS: dict[str, MenuAction] = {
    "SpaceBar": MenuAction.PULL,
    "Gamepad_FaceButton_Bottom": MenuAction.PULL,
    "Left": MenuAction.LOOT_PREV,
    "Right": MenuAction.LOOT_NEXT,
    "Gamepad_DPad_Left": MenuAction.LOOT_PREV,
    "Gamepad_DPad_Right": MenuAction.LOOT_NEXT,
    "Up": MenuAction.BET_UP,
    "Down": MenuAction.BET_DOWN,
    "Gamepad_DPad_Up": MenuAction.BET_UP,
    "Gamepad_DPad_Down": MenuAction.BET_DOWN,
    "Gamepad_FaceButton_Top": MenuAction.MACHINE,
    # Sprint, which does nothing while the menu stops you moving
    "Gamepad_LeftThumbstick": MenuAction.BOARD,
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


def next_loot_type(key: str, step: int) -> str:
    """Steps through the loot types, wrapping around."""
    keys = list(LOOT_TYPES)
    if key not in keys:
        return DEFAULT_LOOT_TYPE
    return keys[(keys.index(key) + step) % len(keys)]


def drops_label(key: str) -> str:
    """Names a loot type and what it adds to the price, e.g. "SHOTGUNS +50%"."""
    chosen = loot_type(key)
    extra = round((chosen.price - 1) * 100)
    return chosen.name.upper() + (f"  +{extra}%" if extra else "")


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
    drops_label: str
    # The button that switches the right hand panel between the paytable and the leaderboard
    board_label: str
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


def _pays(machine: Machine, prize: Prize, *, bet: int, price: int | None, noun: str | None) -> str:
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
        parts.append(describe_loot(loot, noun))
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


def _board_color(spin: SpinRecord) -> RGBA:
    if spin.drops:
        return TIER_COLORS[max(spin.drops, key=lambda drop: list(Tier).index(drop.tier)).tier]
    return SYMBOL_COLORS[Symbol.CASH] if spin.net > 0 else GREY


def _shorten(text: str, length: int) -> str:
    return text if len(text) <= length else text[: length - 3].rstrip() + "..."


def _outcome(spin: SpinRecord, length: int) -> str:
    """What a pull won, in at most `length` characters: every item if they fit, else fewer words."""
    text = spin.outcome()
    if len(text) <= length or len(spin.drops) < 2:
        return _shorten(text, length)
    first_and_more = f"{spin.drops[0].name} +{len(spin.drops) - 1}"
    if len(first_and_more) <= length:
        return first_and_more
    tiers = {tier: sum(1 for drop in spin.drops if drop.tier is tier) for tier in reversed(Tier)}
    return _shorten(describe_loot(tiers.items()), length)


def leaderboard_rows(board: Leaderboard, rows: int = PANEL_ROWS) -> tuple[PaytableRow, ...]:
    """
    Lays out the leaderboard in the paytable's rows (name, detail, amount).

    Each player gets a row with their pulls won and lost and their net cash, and one under it with the
    items they've won. The rest of the rows list the most recent pulls: who, what they won, and what
    they came out with.
    """
    if not board.standings:
        return (PaytableRow("No pulls yet. Pull the lever!", GREY, "", ""),)

    lines: list[PaytableRow] = []
    for rank, standing in enumerate(board.ranked()[:BOARD_PLAYERS], 1):
        stats = standing.stats
        record = f"{stats.wins:,} won, {standing.losses:,} lost"
        cash = compact_amount(Currency.CASH, stats.cash_net, signed=True)
        lines.append(PaytableRow(f"{rank}. {standing.player}", GOLD if rank == 1 else WHITE, record, cash))
        best = next((tier for tier in reversed(Tier) if stats.items.get(tier.value)), None)
        eridium = compact_amount(Currency.ERIDIUM, stats.eridium_net, signed=True)
        items = items_summary(stats) or "no items yet"
        color = TIER_COLORS[best] if best else GREY
        lines.append(PaytableRow(f"      {items}", color, "", eridium if stats.eridium_net else ""))

    lines += [PaytableRow("", WHITE, "", ""), PaytableRow("RECENT PULLS", GREY, "", "")]
    for spin in board.recent()[: max(0, rows - len(lines))]:
        outcome = _outcome(spin, OUTCOME_LENGTH)
        net = compact_amount(spin.currency, spin.net, signed=True)
        lines.append(PaytableRow(spin.player, _board_color(spin), outcome, net))
    return tuple(lines[:rows])


def build_menu_info(
    machine_key: str,
    *,
    bet: int,
    loot_type_key: str = DEFAULT_LOOT_TYPE,
    price: int | None,
    free_play: bool,
    luck_name: str,
    is_host: bool,
    wallet: Mapping[Currency, int | None],
    stats: Stats,
    busy: bool,
    spinning: bool,
    pull_key: str | None,
    leaderboard: Leaderboard | None = None,
    show_leaderboard: bool = False,
) -> MenuInfo:
    """
    Works out what the menu shows.

    Args:
        machine_key: The machine the player has picked.
        bet: The player's bet multiplier.
        loot_type_key: What the player picked for loot wins to drop.
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
        leaderboard: Everyone's results, to show instead of the paytable if `show_leaderboard`.
        show_leaderboard: True to show the leaderboard rather than the paytable.
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

    noun = loot_type(loot_type_key).noun
    odds = _odds(machine_key, luck_name)
    rows = []
    for pattern, prize, probability in odds.rows:
        color = SYMBOL_COLORS[next(iter(pattern.symbols))] if len(pattern.symbols) == 1 else WHITE
        rows.append(
            PaytableRow(
                pattern.describe(),
                color,
                _one_in(probability),
                _pays(machine, prize, bet=bet, price=None if free_play else price, noun=noun),
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
    hints += ["Left/Right: drops", "Up/Down: bet", "Esc: leave"]

    title = f"PAYTABLE  (x{bet} bet)"
    if show_leaderboard and leaderboard is not None:
        title = "LEADERBOARD"
        rows = list(leaderboard_rows(leaderboard))
        players = len(leaderboard.standings)
        who = f"{players} player{'s' if players != 1 else ''}"
        summary = f"{leaderboard.pulls:,} pulls by {who}, including co-op partners."
        note = "Kept in your game. 'gamble_leaderboard' in the console lists more."

    return MenuInfo(
        tagline=MACHINE_TAGLINES.get(machine_key, ""),
        pull_label=pull_label,
        bet_label=f"BET  x{bet}",
        machine_label=f"PLAY {other.name.upper()}",
        drops_label=f"DROPS: {drops_label(loot_type_key)}",
        board_label="PAYTABLE" if show_leaderboard and leaderboard is not None else "LEADERBOARD",
        wallet=_wallet(wallet),
        paytable_title=title,
        paytable=tuple(rows),
        summary=summary,
        note=note,
        lifetime=_lifetime(stats),
        hints="    ".join(hints),
        busy=busy,
    )
