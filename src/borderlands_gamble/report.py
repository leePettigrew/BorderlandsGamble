"""Human-readable odds tables, shared by the in-game `gamble_odds` command and `tools/odds.py`."""

from __future__ import annotations

from .machines import LUCK_PRESETS, base_cost
from .slots import Currency, Machine, describe_loot, exact_odds, format_amount


def _one_in(probability: float) -> str:
    if probability <= 0:
        return "never"
    return f"1 in {1 / probability:,.0f}" if probability < 0.5 else f"{probability:.0%}"


def odds_report(machine: Machine, luck_name: str, *, level: int = 50) -> list[str]:
    """
    Describes a machine's paytable and exact odds.

    Args:
        machine: The (base) machine to describe.
        luck_name: Which luck preset to apply.
        level: The player level to quote prices at.
    Returns:
        The report, as a list of lines.
    """
    lucky = machine.with_luck(LUCK_PRESETS[luck_name])
    odds = exact_odds(lucky)
    cost = base_cost(machine, level)

    lines = [
        f"{machine.name} ({luck_name} luck) - {format_amount(machine.currency, cost)} a pull"
        + (f" at level {level}" if machine.currency is Currency.CASH else ""),
    ]
    for pattern, prize, probability in odds.rows:
        pays: list[str] = []
        if prize.payout:
            pays.append(f"{prize.payout:g}x stake")
        if prize.eridium:
            pays.append(f"{prize.eridium} eridium")
        if prize.loot:
            pays.append(describe_loot(prize.loot))
        lines.append(
            f"  {pattern.describe():<32} {_one_in(probability):>14}   "
            f"{', '.join(pays) if pays else 'nothing'}",
        )
    items = ", ".join(f"{tier.value} {_one_in(rate)} pulls" for tier, rate in odds.items_per_spin)
    lines += [
        f"  Wins {odds.hit_rate:.1%} of pulls, returns {odds.return_to_player:.0%} of"
        f" {machine.currency.name.lower()} staked"
        + (f" plus {odds.eridium_per_spin:.2f} eridium a pull" if odds.eridium_per_spin else ""),
        f"  Items: {items}",
    ]
    return lines
