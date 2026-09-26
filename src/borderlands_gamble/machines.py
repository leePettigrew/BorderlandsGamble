"""The machines themselves: reel weights, paytables, and what a pull costs."""

from __future__ import annotations

import math

from .slots import Currency, Machine, Pattern, Prize, Reel, Symbol, Tier

S = Symbol

# Both machines share the same symbols and layout, but weight them differently. Weights are out of
# 100 per reel; run `tools/odds.py` to see the odds they produce.
_LOOT_SLOTS_REEL = Reel.of(
    {
        S.SKULL: 20,
        S.CASH: 22,
        S.ERIDIUM: 12,
        S.RARE: 18,
        S.EPIC: 12,
        S.LEGENDARY: 9,
        S.VAULT: 7,
    },
)

_ERIDIUM_SLOTS_REEL = Reel.of(
    {
        S.SKULL: 20,
        S.CASH: 10,
        S.ERIDIUM: 22,
        S.RARE: 17,
        S.EPIC: 14,
        S.LEGENDARY: 10,
        S.VAULT: 7,
    },
)

_LOOT = (S.RARE, S.EPIC, S.LEGENDARY)

LOOT_SLOTS = Machine(
    key="cash",
    name="Loot Slots",
    currency=Currency.CASH,
    reels=(_LOOT_SLOTS_REEL, _LOOT_SLOTS_REEL, _LOOT_SLOTS_REEL),
    paytable=(
        (Pattern.three(S.VAULT), Prize("JACKPOT!", payout=50, loot=((Tier.LEGENDARY, 2),), jackpot=True)),
        (Pattern.three(S.LEGENDARY), Prize("LEGENDARY!", payout=5, loot=((Tier.LEGENDARY, 1),))),
        (Pattern.three(S.EPIC), Prize("Epic loot!", payout=2, loot=((Tier.EPIC, 1),))),
        (Pattern.three(S.RARE), Prize("Rare loot!", loot=((Tier.RARE, 1),))),
        (Pattern.three(S.CASH), Prize("Cash out!", payout=20)),
        (Pattern.three(S.ERIDIUM), Prize("Eridium!", eridium=40)),
        (Pattern.three(S.SKULL), Prize("Three skulls. The house wins.")),
        # Mixed loot pays out the lowest rarity on the line - so check the rarer mix first.
        (Pattern.any_three(S.EPIC, S.LEGENDARY), Prize("Mixed loot!", loot=((Tier.EPIC, 1),))),
        (Pattern.any_three(*_LOOT), Prize("Mixed loot!", loot=((Tier.RARE, 1),))),
        (Pattern.at_least(S.VAULT, 2), Prize("Two vaults!", payout=8)),
        (Pattern.at_least(S.CASH, 2), Prize("Pair of cash!", payout=2)),
        (Pattern.at_least(S.ERIDIUM, 2), Prize("Eridium pair!", eridium=6)),
        (Pattern.at_least(S.VAULT, 1), Prize("Vault symbol - money back", payout=1)),
    ),
)

ERIDIUM_SLOTS = Machine(
    key="eridium",
    name="Eridium Slots",
    currency=Currency.ERIDIUM,
    reels=(_ERIDIUM_SLOTS_REEL, _ERIDIUM_SLOTS_REEL, _ERIDIUM_SLOTS_REEL),
    paytable=(
        (Pattern.three(S.VAULT), Prize("JACKPOT!", payout=40, loot=((Tier.LEGENDARY, 3),), jackpot=True)),
        (Pattern.three(S.LEGENDARY), Prize("LEGENDARY!", payout=5, loot=((Tier.LEGENDARY, 1),))),
        (Pattern.three(S.EPIC), Prize("Epic loot!", payout=2, loot=((Tier.EPIC, 2),))),
        (Pattern.three(S.RARE), Prize("Rare loot!", loot=((Tier.RARE, 2),))),
        (Pattern.three(S.ERIDIUM), Prize("Eridium haul!", payout=15)),
        (Pattern.three(S.CASH), Prize("Wrong machine, right result", payout=4)),
        (Pattern.three(S.SKULL), Prize("Three skulls. The house wins.")),
        (Pattern.any_three(S.EPIC, S.LEGENDARY), Prize("Mixed loot!", loot=((Tier.EPIC, 1),))),
        (Pattern.any_three(*_LOOT), Prize("Mixed loot!", loot=((Tier.RARE, 1),))),
        (Pattern.at_least(S.VAULT, 2), Prize("Two vaults!", payout=8)),
        (Pattern.at_least(S.ERIDIUM, 2), Prize("Eridium pair!", payout=2)),
        (Pattern.at_least(S.VAULT, 1), Prize("Vault symbol - money back", payout=1)),
    ),
)

MACHINES: dict[str, Machine] = {machine.key: machine for machine in (LOOT_SLOTS, ERIDIUM_SLOTS)}

# Multiplies the weights of the loot and vault symbols.
LUCK_PRESETS: dict[str, float] = {
    "Stingy": 0.75,
    "Fair": 1.0,
    "Generous": 1.5,
    "Moxxi Likes You": 2.5,
}
DEFAULT_LUCK = "Fair"

BET_MULTIPLIERS: tuple[int, ...] = (1, 2, 5, 10)

# Cash cost of a 1x pull at level 1, and how much it grows per level. Borderlands money scales
# exponentially with level; this lands around $2.6k at level 50 and $25k at level 70.
CASH_BASE_COST = 10.0
CASH_COST_GROWTH = 1.12
ERIDIUM_BASE_COST = 10.0


def nice_round(value: float) -> int:
    """Rounds to two significant figures (at least 1), so prices look like prices."""
    if value < 1:
        return 1
    if value < 100:
        return math.floor(value + 0.5)
    magnitude = 10 ** (math.floor(math.log10(value)) - 1)
    return math.floor(value / magnitude + 0.5) * magnitude


def base_cost(machine: Machine, level: int) -> int:
    """
    Gets the cost of a 1x pull, before any cost multiplier.

    Args:
        machine: The machine being played.
        level: The player's level. Only cash machines scale with it.
    Returns:
        The cost, in the machine's currency.
    """
    if machine.currency is Currency.CASH:
        return nice_round(CASH_BASE_COST * CASH_COST_GROWTH ** (max(1, level) - 1))
    return nice_round(ERIDIUM_BASE_COST)


def spin_cost(machine: Machine, level: int, *, bet: int = 1, cost_multiplier: float = 1.0) -> int:
    """
    Gets the total cost of a pull.

    Args:
        machine: The machine being played.
        level: The player's level.
        bet: The bet multiplier.
        cost_multiplier: A user-configurable multiplier on the base price.
    Returns:
        The total stake, in the machine's currency.
    """
    if bet < 1:
        raise ValueError("Bet multiplier must be at least 1")
    if cost_multiplier <= 0:
        raise ValueError("Cost multiplier must be positive")
    return max(1, nice_round(base_cost(machine, level) * cost_multiplier)) * bet
