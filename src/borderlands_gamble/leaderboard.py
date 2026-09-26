"""
The leaderboard: every pull you've seen paid out, yours and your co-op partners', with what dropped.

The host pays out every pull in co-op, so it's the one that knows exactly what each paid and
dropped. It records each one, and sends the record to everyone else (see `protocol`), so every
player keeps the same leaderboard of the pulls they were there for. Each player's game saves its own
copy with the mod's settings.

Nothing here touches the game.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .loot import ITEM_FAMILIES, item_name, pool_family
from .machines import MACHINES, replay
from .slots import Currency, Symbol, Tier, format_amount
from .stats import Stats

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .casino import Payout
    from .slots import Line, SpinResult

# How many recent pulls the leaderboard keeps, and how many recent drops per player
HISTORY_SIZE = 100
DROPS_PER_PLAYER = 20
# Longer names get cut short, to fit on screen and in co-op messages
NAME_LENGTH = 20


def clean_name(name: str) -> str:
    """Tidies up a player's name for the leaderboard."""
    return " ".join(name.replace("|", "/").split())[:NAME_LENGTH].strip() or "Player"


@dataclass(frozen=True)
class Drop:
    """One item a pull dropped."""

    tier: Tier
    # The item pool family it came from (see `loot.ITEM_FAMILIES`), or None if that isn't known
    family: str | None = None

    @property
    def name(self) -> str:
        """E.g. "legendary shotgun"."""
        return item_name(self.tier, self.family)

    def to_json(self) -> str:
        return self.tier.value if self.family is None else f"{self.tier.value}:{self.family}"

    @classmethod
    def from_json(cls, data: Any) -> Drop | None:
        if not isinstance(data, str):
            return None
        tier_name, _, family = data.partition(":")
        try:
            tier = Tier(tier_name)
        except ValueError:
            return None
        return cls(tier, family if family in ITEM_FAMILIES else None)


def describe_drops(drops: Iterable[Drop]) -> str:
    """Lists what dropped, e.g. "legendary shotgun, 2 epic shields"."""
    counts: dict[str, int] = {}
    for drop in drops:
        counts[drop.name] = counts.get(drop.name, 0) + 1
    return ", ".join(name if count == 1 else f"{count} {name}s" for name, count in counts.items())


@dataclass(frozen=True)
class SpinRecord:
    """A pull that's been paid out: who pulled, what it cost, and what it actually paid."""

    player: str
    machine_key: str
    line: Line
    bet: int
    stake: int
    # What was actually taken, in the machine's currency. 0 on free play
    charged: int
    # What was actually paid out
    cash: int = 0
    eridium: int = 0
    drops: tuple[Drop, ...] = ()

    def __post_init__(self) -> None:
        if not self.player:
            raise ValueError("A record needs a player")
        if self.machine_key not in MACHINES:
            raise ValueError(f"Unknown machine '{self.machine_key}'")
        if len(self.line) != 3 or not all(isinstance(symbol, Symbol) for symbol in self.line):
            raise ValueError(f"Bad line {self.line!r}")
        if self.bet < 1 or min(self.stake, self.charged, self.cash, self.eridium) < 0:
            raise ValueError("Amounts can't be negative")

    @classmethod
    def of_payout(cls, player: str, spin: SpinResult, charged: int, payout: Payout) -> SpinRecord:
        """Makes the record of a pull the casino has just paid out."""
        families = [pool_family(pool) for pool in payout.pools]
        families += [None] * (len(payout.items) - len(families))
        drops = tuple(Drop(tier, family) for tier, family in zip(payout.items, families, strict=False))
        return cls(
            clean_name(player),
            spin.machine_key,
            spin.line,
            spin.bet,
            spin.stake,
            charged,
            payout.cash,
            payout.eridium,
            drops,
        )

    @property
    def currency(self) -> Currency:
        return MACHINES[self.machine_key].currency

    def spin(self) -> SpinResult:
        """The spin itself, rebuilt from its line."""
        return replay(self.machine_key, self.line, self.bet, self.stake)

    @property
    def net(self) -> int:
        """What the player came out with, in the machine's currency."""
        paid = self.cash if self.currency is Currency.CASH else self.eridium
        return paid - self.charged

    def outcome(self) -> str:
        """Says briefly what happened, e.g. "legendary shotgun", "Cash out!", or "no luck"."""
        if self.drops:
            return describe_drops(self.drops)
        spin = self.spin()
        if spin.prize is None or not spin.won:
            return "no luck"
        if self.currency is Currency.CASH and self.eridium and not self.cash:
            return f"{self.eridium:,} eridium"
        return spin.prize.title

    def to_json(self) -> dict[str, Any]:
        return {
            "player": self.player,
            "machine": self.machine_key,
            "line": [symbol.value for symbol in self.line],
            "bet": self.bet,
            "stake": self.stake,
            "charged": self.charged,
            "cash": self.cash,
            "eridium": self.eridium,
            "drops": [drop.to_json() for drop in self.drops],
        }

    @classmethod
    def from_json(cls, data: Any) -> SpinRecord | None:
        """Loads a record, or returns None if it's malformed."""
        if not isinstance(data, dict):
            return None
        try:
            line = data["line"]
            drops = [Drop.from_json(drop) for drop in data.get("drops", [])]
            numbers = [data[key] for key in ("bet", "stake", "charged", "cash", "eridium")]
            if not all(isinstance(n, int) and not isinstance(n, bool) for n in numbers):
                return None
            bet, stake, charged, cash, eridium = numbers
            return cls(
                player=clean_name(str(data["player"])),
                machine_key=str(data["machine"]),
                line=(Symbol(line[0]), Symbol(line[1]), Symbol(line[2])),
                bet=bet,
                stake=stake,
                charged=charged,
                cash=cash,
                eridium=eridium,
                drops=tuple(drop for drop in drops if drop is not None),
            )
        except (KeyError, IndexError, TypeError, ValueError):
            return None


@dataclass
class Standing:
    """One player's totals."""

    player: str
    stats: Stats = field(default_factory=Stats)
    # The most recent first
    drops: list[Drop] = field(default_factory=list)

    @property
    def losses(self) -> int:
        return self.stats.spins - self.stats.wins


class Leaderboard:
    """Everyone's totals, and the most recent pulls."""

    def __init__(self) -> None:
        self.standings: dict[str, Standing] = {}
        # Oldest first
        self.history: list[SpinRecord] = []

    @property
    def pulls(self) -> int:
        return sum(standing.stats.spins for standing in self.standings.values())

    def record(self, spin: SpinRecord) -> None:
        """Adds a pull that's been paid out."""
        standing = self.standings.setdefault(spin.player, Standing(spin.player))
        standing.stats.record(
            spin.spin(),
            charged=spin.charged,
            cash_paid=spin.cash,
            eridium_paid=spin.eridium,
            items=[drop.tier for drop in spin.drops],
        )
        standing.drops[:0] = reversed(spin.drops)
        del standing.drops[DROPS_PER_PLAYER:]
        self.history.append(spin)
        del self.history[:-HISTORY_SIZE]

    def ranked(self) -> list[Standing]:
        """Everyone, the biggest winner first: by cash won or lost, then eridium, then pulls."""
        return sorted(
            self.standings.values(),
            key=lambda standing: (standing.stats.cash_net, standing.stats.eridium_net, standing.stats.spins),
            reverse=True,
        )

    def recent(self) -> list[SpinRecord]:
        """The pulls kept, newest first."""
        return self.history[::-1]

    def clear(self) -> None:
        self.standings.clear()
        self.history.clear()

    def to_json(self) -> dict[str, Any]:
        players = {
            name: {"stats": standing.stats.to_json(), "drops": [drop.to_json() for drop in standing.drops]}
            for name, standing in self.standings.items()
        }
        return {"players": players, "history": [spin.to_json() for spin in self.history]}

    @classmethod
    def from_json(cls, data: Any) -> Leaderboard:
        """Loads a leaderboard, skipping anything malformed rather than failing."""
        board = cls()
        if not isinstance(data, dict):
            return board
        players = data.get("players")
        if isinstance(players, dict):
            for name, entry in players.items():
                if not isinstance(entry, dict):
                    continue
                player = clean_name(str(name))
                drops = [Drop.from_json(drop) for drop in entry.get("drops", []) or []]
                board.standings[player] = Standing(
                    player,
                    Stats.from_json(entry.get("stats")),
                    [drop for drop in drops if drop is not None][:DROPS_PER_PLAYER],
                )
        history = data.get("history")
        if isinstance(history, list):
            records = (SpinRecord.from_json(spin) for spin in history[-HISTORY_SIZE:])
            board.history = [spin for spin in records if spin is not None]
        return board

    def summary_lines(self) -> list[str]:
        """Everything on the leaderboard, as lines for the console."""
        if not self.standings:
            return ["No pulls on the leaderboard yet."]
        players = len(self.standings)
        lines = [f"Leaderboard: {self.pulls:,} pulls by {players} player{'s' if players != 1 else ''}"]
        for rank, standing in enumerate(self.ranked(), 1):
            stats = standing.stats
            parts = [
                f"net {format_amount(Currency.CASH, stats.cash_net)}",
                f"{stats.wins:,} won, {standing.losses:,} lost",
            ]
            if stats.eridium_spent or stats.eridium_won:
                parts.append(f"{stats.eridium_net:+,} eridium")
            items = items_summary(stats)
            if items:
                parts.append(items)
            lines.append(f"  {rank}. {standing.player}: {', '.join(parts)}")
            if standing.drops:
                lines.append(f"     Recent drops: {describe_drops(standing.drops)}")
        lines.append("Recent pulls, newest first:")
        for spin in self.recent():
            machine = MACHINES[spin.machine_key].name
            net = compact_amount(spin.currency, spin.net, signed=True)
            lines.append(f"  {spin.player}: {machine} x{spin.bet}, {net}, {spin.outcome()}")
        return lines


def items_summary(stats: Stats) -> str:
    """Counts the items someone's won, best first, e.g. "2 legendary, 5 epic, 9 rare"."""
    counts = [(tier, stats.items.get(tier.value, 0)) for tier in reversed(Tier)]
    return ", ".join(f"{count:,} {tier.value}" for tier, count in counts if count)


def _short_number(value: int) -> str:
    """E.g. 9,500, 52.5k, 250k, 1.25M."""
    if value < 10_000:
        return f"{value:,}"
    for divisor, suffix in ((1_000, "k"), (1_000_000, "M"), (1_000_000_000, "B")):
        scaled = value / divisor
        text = f"{scaled:.2f}" if scaled < 10 else f"{scaled:.1f}" if scaled < 100 else f"{scaled:.0f}"
        if float(text) < 1000 or suffix == "B":
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return text + suffix
    raise AssertionError("unreachable")


def compact_amount(currency: Currency, amount: int, *, signed: bool = False) -> str:
    """A short amount for tight spaces, e.g. "$1.25M", "-$52.5k", "+40 eridium"."""
    sign = "-" if amount < 0 else "+" if signed and amount > 0 else ""
    number = _short_number(abs(amount))
    return f"{sign}${number}" if currency is Currency.CASH else f"{sign}{number} eridium"
