"""Which of the game's item pools each prize tier draws from, and where the drops land."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .slots import Tier

if TYPE_CHECKING:
    import random

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


def choose_pool(tier: Tier, rng: random.Random) -> str:
    """
    Picks which item pool a single item of the given tier drops from.

    Args:
        tier: The prize tier.
        rng: The random number generator to use.
    Returns:
        The item pool's name.
    """
    pools, weights = zip(*ITEM_POOLS[tier], strict=True)
    return rng.choices(pools, weights=weights)[0]


def roll_drops(loot: tuple[tuple[Tier, int], ...], rng: random.Random) -> list[tuple[Tier, str]]:
    """
    Expands a prize's loot into one item pool per item.

    Args:
        loot: The (tier, count) pairs to drop.
        rng: The random number generator to use.
    Returns:
        A list of (tier, item pool) pairs, one per item.
    """
    return [(tier, choose_pool(tier, rng)) for tier, count in loot for _ in range(count)]


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
