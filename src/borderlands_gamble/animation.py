"""Pure timeline for the spinning-reel animation.

The outcome is decided the moment the lever is pulled; this only works out which symbols to show
while the reels spin down onto it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import random
    from collections.abc import Mapping, Sequence

    from .slots import Reel, Symbol

# How many symbols each reel scrolls past, at minimum, before stopping.
MIN_SCROLL_STEPS = 18


def display_strip(reel: Reel, length: int = 24) -> tuple[Symbol, ...]:
    """
    Builds the cosmetic strip of symbols a reel scrolls through.

    Every symbol appears at least once, roughly in proportion to its weight, and spread out as
    evenly as possible (using smooth weighted round-robin). The strip doesn't influence the odds.

    Args:
        reel: The reel to build a strip for.
        length: The minimum strip length.
    Returns:
        The strip, as a cyclic sequence of symbols.
    """
    weights: Mapping[Symbol, float] = reel.probabilities()
    length = max(length, len(weights))

    # Share out the slots by largest remainder, making sure every symbol gets at least one
    exact = {symbol: prob * length for symbol, prob in weights.items()}
    counts = {symbol: max(1, math.floor(value)) for symbol, value in exact.items()}
    shortfall = length - sum(counts.values())
    if shortfall > 0:
        by_remainder = sorted(exact, key=lambda s: exact[s] - math.floor(exact[s]), reverse=True)
        for symbol in by_remainder[:shortfall]:
            counts[symbol] += 1

    total = sum(counts.values())
    current = dict.fromkeys(counts, 0)
    strip: list[Symbol] = []
    for _ in range(total):
        for symbol, count in counts.items():
            current[symbol] += count
        best = max(current, key=lambda s: current[s])
        current[best] -= total
        strip.append(best)
    return tuple(strip)


def _ease_out_cubic(x: float) -> float:
    return 1 - (1 - x) ** 3


@dataclass(frozen=True)
class ReelMotion:
    strip: tuple[Symbol, ...]
    start_index: int
    steps: int
    start_time: float
    duration: float

    @property
    def stop_time(self) -> float:
        return self.start_time + self.duration

    @property
    def final_index(self) -> int:
        return (self.start_index + self.steps) % len(self.strip)

    def position(self, now: float) -> int:
        """Gets how many whole symbols this reel has scrolled at the given time."""
        if self.duration <= 0 or now >= self.stop_time:
            return self.steps
        progress = max(0.0, (now - self.start_time) / self.duration)
        return min(self.steps, int(self.steps * _ease_out_cubic(progress)))

    def window(self, now: float) -> tuple[Symbol, Symbol, Symbol]:
        """Gets the (above, payline, below) symbols showing at the given time."""
        idx = self.start_index + self.position(now)
        n = len(self.strip)
        return self.strip[(idx - 1) % n], self.strip[idx % n], self.strip[(idx + 1) % n]

    def stopped(self, now: float) -> bool:
        return now >= self.stop_time


@dataclass(frozen=True)
class SpinAnimation:
    reels: tuple[ReelMotion, ...]

    @property
    def end_time(self) -> float:
        return max(reel.stop_time for reel in self.reels)

    @property
    def final_indices(self) -> tuple[int, ...]:
        return tuple(reel.final_index for reel in self.reels)

    def windows(self, now: float) -> tuple[tuple[Symbol, Symbol, Symbol], ...]:
        return tuple(reel.window(now) for reel in self.reels)

    def stopped(self, now: float) -> tuple[bool, ...]:
        return tuple(reel.stopped(now) for reel in self.reels)

    def done(self, now: float) -> bool:
        return now >= self.end_time


def plan_spin(
    strips: Sequence[tuple[Symbol, ...]],
    line: Sequence[Symbol],
    rng: random.Random,
    *,
    start_time: float,
    start_indices: Sequence[int] | None = None,
    first_stop: float = 1.0,
    stagger: float = 0.45,
) -> SpinAnimation:
    """
    Plans an animation which lands each reel on its symbol from the given line.

    Args:
        strips: The display strip for each reel.
        line: The symbols each reel must stop on.
        rng: Used to pick which copy of a symbol on the strip to stop on.
        start_time: When the animation starts.
        start_indices: Where each reel starts on its strip, e.g. where it stopped last time.
        first_stop: How long the first reel spins for. Zero or less skips the animation.
        stagger: How much longer each subsequent reel spins for.
    Returns:
        The planned animation.
    """
    if len(strips) != len(line):
        raise ValueError("Need exactly one strip per symbol on the line")
    if start_indices is None:
        start_indices = [0] * len(strips)

    reels: list[ReelMotion] = []
    for idx, (strip, symbol) in enumerate(zip(strips, line, strict=True)):
        stops = [i for i, s in enumerate(strip) if s == symbol]
        if not stops:
            raise ValueError(f"Symbol {symbol} isn't on reel {idx}'s strip")
        target = rng.choice(stops)
        start = start_indices[idx] % len(strip)

        # Spin at least a few full-ish rotations, more for later reels, then line up the target.
        min_steps = MIN_SCROLL_STEPS + idx * 6
        steps = min_steps + (target - start - min_steps) % len(strip)

        duration = 0.0 if first_stop <= 0 else first_stop + idx * max(0.0, stagger)
        reels.append(ReelMotion(strip, start, steps, start_time, duration))
    return SpinAnimation(tuple(reels))
