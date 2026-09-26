"""Which of the game's item pools each prize tier draws from, and where the drops land."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .slots import Tier

if TYPE_CHECKING:
    import random
    from collections.abc import Mapping

Pools = tuple[tuple[str, float], ...]

# Item pool names come from the game's own data (as of BL4 v1.10 / Steam build 25234898). Guns get
# more weight than gear, so wins mostly feel like the classic "slot machine spits out a gun".
# There's no combined legendary gun pool, so legendaries list each weapon type separately.
ITEM_POOLS: dict[Tier, tuple[tuple[str, float], ...]] = {
    Tier.RARE: (
        ("itempool_guns_03_rare", 6),
        ("itempool_shields_03_rare", 1),
        ("itempool_grenade_gadgets_03_rare", 1),
        ("itempool_repkit_03_rare", 1),
        ("itempool_class_mods_03_rare", 1),
        ("itempool_enhancements_03_rare", 1),
    ),
    Tier.EPIC: (
        ("itempool_guns_04_epic", 6),
        ("itempool_shields_04_epic", 1),
        ("itempool_grenade_gadgets_04_epic", 1),
        ("itempool_repkit_04_epic", 1),
        ("itempool_class_mods_04_epic", 1),
        ("itempool_enhancements_04_epic", 1),
    ),
    Tier.LEGENDARY: (
        ("itempool_ar_05_legendary", 2),
        ("itempool_ps_05_legendary", 2),
        ("itempool_sm_05_legendary", 2),
        ("itempool_sg_05_legendary", 2),
        ("itempool_sr_05_legendary", 2),
        ("itempool_hw_05_legendary", 1),
        ("itempool_shields_05_legendary", 1),
        ("itempool_grenade_gadgets_05_legendary", 1),
        ("itempool_repkit_05_legendary", 1),
        ("itempool_class_mods_05_legendary", 1),
        ("itempool_enhancements_05_legendary", 1),
    ),
}


_TIER_SUFFIXES = {Tier.RARE: "03_rare", Tier.EPIC: "04_epic", Tier.LEGENDARY: "05_legendary"}


def _pools(*families: tuple[str, float]) -> dict[Tier, Pools]:
    """Builds each tier's pools from pool families, e.g. ("sg", 1) is `itempool_sg_03_rare` and so on."""
    return {
        tier: tuple((f"itempool_{family}_{suffix}", weight) for family, weight in families)
        for tier, suffix in _TIER_SUFFIXES.items()
    }


@dataclass(frozen=True)
class LootType:
    """
    What a machine drops when a spin wins loot, picked by the player.

    Narrower choices cost more: `price` multiplies what a pull costs.
    """

    key: str
    name: str
    # What one item is called, e.g. "shotgun". None when it could be anything.
    noun: str | None
    price: float
    pools: Mapping[Tier, Pools]


# Every family below has a rare, epic and legendary pool in the game's data (see `ITEM_POOLS`).
_GUNS = (("ar", 2), ("ps", 2), ("sm", 2), ("sg", 2), ("sr", 2), ("hw", 1))

LOOT_TYPES: dict[str, LootType] = {
    loot_type.key: loot_type
    for loot_type in (
        LootType("any", "Anything", None, 1.0, ITEM_POOLS),
        LootType("guns", "Guns", "gun", 1.25, _pools(*_GUNS)),
        LootType("pistols", "Pistols", "pistol", 1.5, _pools(("ps", 1))),
        LootType("smgs", "SMGs", "SMG", 1.5, _pools(("sm", 1))),
        LootType("assault_rifles", "Assault Rifles", "assault rifle", 1.5, _pools(("ar", 1))),
        LootType("shotguns", "Shotguns", "shotgun", 1.5, _pools(("sg", 1))),
        LootType("snipers", "Sniper Rifles", "sniper rifle", 1.5, _pools(("sr", 1))),
        LootType("heavy", "Heavy Weapons", "heavy weapon", 1.5, _pools(("hw", 1))),
        LootType("shields", "Shields", "shield", 1.25, _pools(("shields", 1))),
        LootType("grenades", "Grenades", "grenade", 1.25, _pools(("grenade_gadgets", 1))),
        LootType("repkits", "Repkits", "repkit", 1.25, _pools(("repkit", 1))),
        LootType("class_mods", "Class Mods", "class mod", 2.0, _pools(("class_mods", 1))),
        LootType("enhancements", "Enhancements", "enhancement", 1.5, _pools(("enhancements", 1))),
    )
}
DEFAULT_LOOT_TYPE = "any"


def loot_type(key: str) -> LootType:
    """Gets a loot type, falling back to "Anything" for keys this version doesn't know."""
    return LOOT_TYPES.get(key, LOOT_TYPES[DEFAULT_LOOT_TYPE])


def choose_pool(tier: Tier, rng: random.Random, loot_type_key: str = DEFAULT_LOOT_TYPE) -> str:
    """
    Picks which item pool a single item of the given tier drops from.

    Args:
        tier: The prize tier.
        rng: The random number generator to use.
        loot_type_key: What the player asked the machine to drop.
    Returns:
        The item pool's name.
    """
    pools, weights = zip(*loot_type(loot_type_key).pools[tier], strict=True)
    return rng.choices(pools, weights=weights)[0]


def roll_drops(
    loot: tuple[tuple[Tier, int], ...],
    rng: random.Random,
    loot_type_key: str = DEFAULT_LOOT_TYPE,
) -> list[tuple[Tier, str]]:
    """
    Expands a prize's loot into one item pool per item.

    Args:
        loot: The (tier, count) pairs to drop.
        rng: The random number generator to use.
        loot_type_key: What the player asked the machine to drop.
    Returns:
        A list of (tier, item pool) pairs, one per item.
    """
    return [(tier, choose_pool(tier, rng, loot_type_key)) for tier, count in loot for _ in range(count)]


# Drops fan out in an arc in front of the player, in Unreal units (cm).
DROP_DISTANCE = 160.0
DROP_SPACING = 60.0
DROP_HEIGHT = 60.0


def drop_offset(index: int, count: int) -> tuple[float, float]:
    """
    Works out where one item of a batch lands, relative to the player.

    Items are spaced evenly along an arc centred straight ahead of the player.

    Args:
        index: Which item this is, from 0.
        count: How many items are being dropped in total.
    Returns:
        The (forward, right) offset from the player.
    """
    count = max(1, count)
    # Spread the arc wider as the batch grows, but never behind the player
    arc = min(math.pi * 0.9, DROP_SPACING * (count - 1) / DROP_DISTANCE)
    angle = 0.0 if count == 1 else -arc / 2 + arc * index / (count - 1)
    return DROP_DISTANCE * math.cos(angle), DROP_DISTANCE * math.sin(angle)


def drop_position(
    location: tuple[float, float, float],
    yaw_degrees: float,
    index: int,
    count: int,
) -> tuple[float, float, float]:
    """
    Converts a drop's offset into a world position.

    Args:
        location: The player's (x, y, z) location.
        yaw_degrees: The direction the player is facing.
        index: Which item this is, from 0.
        count: How many items are being dropped in total.
    Returns:
        The (x, y, z) world position to drop the item at.
    """
    forward, right = drop_offset(index, count)
    yaw = math.radians(yaw_degrees)
    x = location[0] + forward * math.cos(yaw) - right * math.sin(yaw)
    y = location[1] + forward * math.sin(yaw) + right * math.cos(yaw)
    return x, y, location[2] + DROP_HEIGHT


# At a slot machine, drops land on the floor between it and the player, in rows across.
MACHINE_CLEARANCE = 60.0
DROPS_PER_ROW = 5
ROW_SPACING = 50.0


def drop_position_near(
    machine: tuple[float, float],
    machine_radius: float,
    player: tuple[float, float, float],
    index: int,
    count: int,
) -> tuple[float, float, float]:
    """
    Works out where one item of a batch lands when a slot machine pays out.

    Items land just clear of the machine, on the side facing the player, so they're easy to see and
    never inside the machine.

    Args:
        machine: The slot machine's centre, on the floor plan.
        machine_radius: How far the machine reaches out from its centre.
        player: The player's (x, y, z) location.
        index: Which item this is, from 0.
        count: How many items are being dropped in total.
    Returns:
        The (x, y, z) world position to drop the item at.
    """
    dx, dy = player[0] - machine[0], player[1] - machine[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        dx, dy, length = 1.0, 0.0, 1.0
    dx, dy = dx / length, dy / length

    count = max(1, count)
    row, column = divmod(index, DROPS_PER_ROW)
    in_row = min(DROPS_PER_ROW, count - row * DROPS_PER_ROW)
    across = (column - (in_row - 1) / 2) * DROP_SPACING
    out = machine_radius + MACHINE_CLEARANCE + row * ROW_SPACING
    return (
        machine[0] + dx * out - dy * across,
        machine[1] + dy * out + dx * across,
        player[2] + DROP_HEIGHT,
    )
