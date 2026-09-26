"""Lifetime gambling stats, kept as plain JSON-friendly data so they can live in the mod settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import TYPE_CHECKING, Any

from .slots import Currency, Tier, format_amount

if TYPE_CHECKING:
    from .slots import SpinResult


@dataclass
class Stats:
    spins: int = 0
    wins: int = 0
    jackpots: int = 0
    cash_spent: int = 0
    cash_won: int = 0
    eridium_spent: int = 0
    eridium_won: int = 0
    biggest_cash_win: int = 0
    items: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: Any) -> Stats:
        """Loads stats, ignoring anything missing or malformed rather than failing."""
        stats = cls()
        if not isinstance(data, dict):
            return stats
        for f in fields(cls):
            value = data.get(f.name)
            if f.name == "items":
                if isinstance(value, dict):
                    stats.items = {
                        str(k): int(v)
                        for k, v in value.items()
                        if isinstance(v, int) and not isinstance(v, bool)
                    }
            elif isinstance(value, int) and not isinstance(value, bool):
                setattr(stats, f.name, value)
        return stats

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def record(
        self,
        result: SpinResult,
        *,
        charged: int,
        cash_paid: int,
        eridium_paid: int,
        items: list[Tier],
    ) -> None:
        """
        Records a settled spin, using what was actually charged and paid out.

        Args:
            result: The spin's result.
            charged: How much was actually charged, in the machine's currency.
            cash_paid: How much cash was actually paid out.
            eridium_paid: How much eridium was actually paid out.
            items: The tier of each item actually dropped.
        """
        self.spins += 1
        if result.won:
            self.wins += 1
        if result.jackpot:
            self.jackpots += 1

        if result.currency is Currency.CASH:
            self.cash_spent += charged
        else:
            self.eridium_spent += charged

        self.cash_won += cash_paid
        self.eridium_won += eridium_paid
        self.biggest_cash_win = max(self.biggest_cash_win, cash_paid)
        for tier in items:
            self.items[tier.value] = self.items.get(tier.value, 0) + 1

    @property
    def cash_net(self) -> int:
        return self.cash_won - self.cash_spent

    @property
    def eridium_net(self) -> int:
        return self.eridium_won - self.eridium_spent

    def summary_lines(self) -> list[str]:
        win_rate = f"{self.wins / self.spins:.0%}" if self.spins else "n/a"
        items = ", ".join(f"{self.items.get(tier.value, 0)} {tier.value}" for tier in Tier)
        return [
            f"Spins: {self.spins:,} (won {self.wins:,}, {win_rate}), jackpots: {self.jackpots:,}",
            f"Cash: spent {format_amount(Currency.CASH, self.cash_spent)},"
            f" won {format_amount(Currency.CASH, self.cash_won)},"
            f" net {format_amount(Currency.CASH, self.cash_net)}",
            f"Eridium: spent {self.eridium_spent:,}, won {self.eridium_won:,}, net {self.eridium_net:,}",
            f"Biggest cash win: {format_amount(Currency.CASH, self.biggest_cash_win)}",
            f"Items won: {items}",
        ]
