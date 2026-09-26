"""Pure slot-machine engine: symbols, reels, paytables, spins and exact odds.

Nothing in this module touches the game, so it runs (and is unit tested) outside Borderlands.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import random
    from collections.abc import Iterable, Mapping, Sequence

REEL_COUNT = 3

# Cap on items a single spin may drop, so a big bet on a jackpot can't flood the floor with physics
# objects.
MAX_ITEMS_PER_SPIN = 20


class Currency(Enum):
    """In-game currencies, valued by their `GbxCurrencyDef` token names."""

    CASH = "Cash"
    ERIDIUM = "eridium"


class Tier(Enum):
    """Loot rarity tiers the machines can pay out."""

    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"


class Symbol(Enum):
    SKULL = "skull"
    CASH = "cash"
    ERIDIUM = "eridium"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"
    VAULT = "vault"


# What each symbol looks like on a reel. Plain ASCII, since we can't rely on the game's fonts
# having any fancier glyphs.
SYMBOL_LABELS: dict[Symbol, str] = {
    Symbol.SKULL: "SKULL",
    Symbol.CASH: "$$$",
    Symbol.ERIDIUM: "ERIDIUM",
    Symbol.RARE: "RARE",
    Symbol.EPIC: "EPIC",
    Symbol.LEGENDARY: "LEGEND",
    Symbol.VAULT: "VAULT",
}

# RGBA, loosely following the series' rarity colours.
SYMBOL_COLORS: dict[Symbol, tuple[float, float, float, float]] = {
    Symbol.SKULL: (0.72, 0.72, 0.72, 1.0),
    Symbol.CASH: (0.35, 0.95, 0.35, 1.0),
    Symbol.ERIDIUM: (0.90, 0.25, 0.90, 1.0),
    Symbol.RARE: (0.20, 0.55, 1.00, 1.0),
    Symbol.EPIC: (0.62, 0.30, 1.00, 1.0),
    Symbol.LEGENDARY: (1.00, 0.55, 0.05, 1.0),
    Symbol.VAULT: (1.00, 0.85, 0.20, 1.0),
}

# Symbols whose weights the "luck" setting scales.
LUCKY_SYMBOLS: frozenset[Symbol] = frozenset(
    {Symbol.RARE, Symbol.EPIC, Symbol.LEGENDARY, Symbol.VAULT},
)

Line = tuple[Symbol, Symbol, Symbol]


@dataclass(frozen=True)
class Reel:
    """A single reel, as the relative chance of each symbol landing on the payline."""

    weights: tuple[tuple[Symbol, float], ...]

    @classmethod
    def of(cls, weights: Mapping[Symbol, float]) -> Reel:
        return cls(tuple(weights.items()))

    def __post_init__(self) -> None:
        if not self.weights:
            raise ValueError("A reel needs at least one symbol")
        if any(weight <= 0 for _, weight in self.weights):
            raise ValueError("Reel weights must be positive")
        if len({symbol for symbol, _ in self.weights}) != len(self.weights):
            raise ValueError("Reel symbols must be unique")

    @property
    def symbols(self) -> tuple[Symbol, ...]:
        return tuple(symbol for symbol, _ in self.weights)

    def with_luck(self, luck: float) -> Reel:
        """Returns a copy of this reel with the lucky symbols' weights multiplied by `luck`."""
        if luck <= 0:
            raise ValueError("Luck must be positive")
        return Reel(
            tuple(
                (symbol, weight * luck if symbol in LUCKY_SYMBOLS else weight)
                for symbol, weight in self.weights
            ),
        )

    def probabilities(self) -> dict[Symbol, float]:
        total = sum(weight for _, weight in self.weights)
        return {symbol: weight / total for symbol, weight in self.weights}

    def draw(self, rng: random.Random) -> Symbol:
        symbols, weights = zip(*self.weights, strict=True)
        return rng.choices(symbols, weights=weights)[0]


@dataclass(frozen=True)
class Pattern:
    """Matches a line when at least `count` of its symbols are among `symbols`."""

    symbols: frozenset[Symbol]
    count: int = REEL_COUNT

    @classmethod
    def three(cls, symbol: Symbol) -> Pattern:
        return cls(frozenset({symbol}), REEL_COUNT)

    @classmethod
    def any_three(cls, *symbols: Symbol) -> Pattern:
        return cls(frozenset(symbols), REEL_COUNT)

    @classmethod
    def at_least(cls, symbol: Symbol, count: int) -> Pattern:
        return cls(frozenset({symbol}), count)

    def matches(self, line: Sequence[Symbol]) -> bool:
        return sum(1 for symbol in line if symbol in self.symbols) >= self.count

    def describe(self) -> str:
        names = "/".join(SYMBOL_LABELS[s] for s in sorted(self.symbols, key=_symbol_order))
        if len(self.symbols) > 1:
            return f"any {self.count} of {names}"
        return f"{self.count}x {names}"


@dataclass(frozen=True)
class Prize:
    """
    What a winning line pays, per 1x bet.

    `payout` is a multiple of the stake, paid back in the machine's own currency. `eridium` is a
    flat bonus on top of that. `loot` is a list of (tier, count) item drops.
    """

    title: str
    payout: float = 0.0
    eridium: int = 0
    loot: tuple[tuple[Tier, int], ...] = ()
    jackpot: bool = False

    @property
    def pays_anything(self) -> bool:
        return self.payout > 0 or self.eridium > 0 or any(count > 0 for _, count in self.loot)


@dataclass(frozen=True)
class Machine:
    key: str
    name: str
    currency: Currency
    reels: tuple[Reel, Reel, Reel]
    paytable: tuple[tuple[Pattern, Prize], ...]

    def __post_init__(self) -> None:
        if len(self.reels) != REEL_COUNT:
            raise ValueError(f"Machines need exactly {REEL_COUNT} reels")

    def with_luck(self, luck: float) -> Machine:
        return Machine(
            key=self.key,
            name=self.name,
            currency=self.currency,
            reels=(
                self.reels[0].with_luck(luck),
                self.reels[1].with_luck(luck),
                self.reels[2].with_luck(luck),
            ),
            paytable=self.paytable,
        )

    def evaluate(self, line: Sequence[Symbol]) -> Prize | None:
        """
        Finds the first paytable row the line matches.

        Rows may deliberately pay nothing (e.g. three skulls), to stop a line falling through to a
        later, more generous row.

        Args:
            line: The symbols on the payline.
        Returns:
            The matching row's prize, or None if no row matches.
        """
        if len(line) != REEL_COUNT:
            raise ValueError(f"A line has exactly {REEL_COUNT} symbols")
        for pattern, prize in self.paytable:
            if pattern.matches(line):
                return prize
        return None


@dataclass(frozen=True)
class SpinResult:
    machine_key: str
    currency: Currency
    line: Line
    prize: Prize | None
    bet: int
    stake: int
    payout: int
    eridium: int
    loot: tuple[tuple[Tier, int], ...]

    @property
    def won(self) -> bool:
        return self.prize is not None and self.prize.pays_anything

    @property
    def jackpot(self) -> bool:
        return self.won and self.prize is not None and self.prize.jackpot

    @property
    def item_count(self) -> int:
        return sum(count for _, count in self.loot)


def scale_prize(prize: Prize | None, stake: int, bet: int) -> tuple[int, int, tuple[tuple[Tier, int], ...]]:
    """
    Scales a (per 1x bet) prize to an actual stake and bet multiplier.

    Args:
        prize: The prize to scale, or None for a losing spin.
        stake: The total amount charged for the spin.
        bet: The bet multiplier the stake was charged at.
    Returns:
        A tuple of the currency payout, the eridium bonus, and the loot to drop.
    """
    if prize is None or not prize.pays_anything:
        return 0, 0, ()

    payout = math.floor(prize.payout * stake + 0.5)
    eridium = prize.eridium * bet

    loot: list[tuple[Tier, int]] = []
    budget = MAX_ITEMS_PER_SPIN
    for tier, count in prize.loot:
        scaled = min(count * bet, budget)
        if scaled > 0:
            loot.append((tier, scaled))
            budget -= scaled
    return payout, eridium, tuple(loot)


def spin(machine: Machine, rng: random.Random, *, stake: int, bet: int = 1) -> SpinResult:
    """
    Pulls the lever once.

    Args:
        machine: The machine to play. Apply luck beforehand via `Machine.with_luck`.
        rng: The random number generator to draw with.
        stake: The total amount charged for this spin.
        bet: The bet multiplier the stake was charged at.
    Returns:
        The spin's result.
    """
    if bet < 1:
        raise ValueError("Bet multiplier must be at least 1")
    if stake < 0:
        raise ValueError("Stake can't be negative")

    line: Line = (
        machine.reels[0].draw(rng),
        machine.reels[1].draw(rng),
        machine.reels[2].draw(rng),
    )
    prize = machine.evaluate(line)
    payout, eridium, loot = scale_prize(prize, stake, bet)
    return SpinResult(
        machine_key=machine.key,
        currency=machine.currency,
        line=line,
        prize=prize,
        bet=bet,
        stake=stake,
        payout=payout,
        eridium=eridium,
        loot=loot,
    )


@dataclass(frozen=True)
class Odds:
    """Exact odds for a machine, per 1x spin."""

    # Probability of each paytable row paying out, in paytable order.
    rows: tuple[tuple[Pattern, Prize, float], ...]
    # Probability of any prize at all.
    hit_rate: float
    # Expected currency paid back per unit of currency staked.
    return_to_player: float
    # Expected eridium bonus per spin.
    eridium_per_spin: float
    # Expected number of items per spin, by tier.
    items_per_spin: tuple[tuple[Tier, float], ...]

    def one_in(self, probability: float) -> float:
        return math.inf if probability <= 0 else 1 / probability


def exact_odds(machine: Machine) -> Odds:
    """
    Calculates a machine's exact odds by enumerating every possible line.

    Args:
        machine: The machine to analyse. Apply luck beforehand via `Machine.with_luck`.
    Returns:
        The machine's odds.
    """
    reel_probs = [reel.probabilities() for reel in machine.reels]
    row_probs = [0.0] * len(machine.paytable)

    for line in itertools.product(*(probs.keys() for probs in reel_probs)):
        probability = reel_probs[0][line[0]] * reel_probs[1][line[1]] * reel_probs[2][line[2]]
        for idx, (pattern, _) in enumerate(machine.paytable):
            if pattern.matches(line):
                row_probs[idx] += probability
                break

    rows = tuple(
        (pattern, prize, prob) for (pattern, prize), prob in zip(machine.paytable, row_probs, strict=True)
    )
    paying = [(prize, prob) for _, prize, prob in rows if prize.pays_anything]

    items: dict[Tier, float] = {}
    for prize, prob in paying:
        for tier, count in prize.loot:
            items[tier] = items.get(tier, 0.0) + prob * count

    return Odds(
        rows=rows,
        hit_rate=sum(prob for _, prob in paying),
        return_to_player=sum(prize.payout * prob for prize, prob in paying),
        eridium_per_spin=sum(prize.eridium * prob for prize, prob in paying),
        items_per_spin=tuple((tier, items[tier]) for tier in Tier if tier in items),
    )


def format_amount(currency: Currency, amount: int) -> str:
    if currency is Currency.CASH:
        return f"-${-amount:,}" if amount < 0 else f"${amount:,}"
    return f"{amount:,} eridium"


def describe_loot(loot: Iterable[tuple[Tier, int]], noun: str | None = None) -> str:
    """Describes items, e.g. "1 legendary, 2 epic", or with a noun, "1 legendary shotgun"."""
    parts = []
    for tier, count in loot:
        if count <= 0:
            continue
        part = f"{count} {tier.value}"
        if noun is not None:
            part += f" {noun}" + ("" if count == 1 else "s")
        parts.append(part)
    return ", ".join(parts)


def _symbol_order(symbol: Symbol) -> int:
    return list(Symbol).index(symbol)
