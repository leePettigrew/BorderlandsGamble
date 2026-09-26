"""
Watching other players spin, in co-op.

Whenever anyone pulls the lever, the host tells everyone else (see `protocol.Show`), and the host
watches its co-op partners' pulls itself. Each game then spins a small copy of the reels above that
player's head, and shows what they won. Everyone shares the same paytables, so all it takes is the
line of symbols.

Nothing here touches the game. The panels are drawn by `overlay.UmgSpectators`.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .animation import SpinAnimation, display_strip, plan_spin
from .casino import ReelView, Tone, describe_result, expected_payout
from .loot import DEFAULT_LOOT_TYPE, loot_type
from .machines import MACHINES

if TYPE_CHECKING:
    from collections.abc import Callable

    from .slots import SpinResult, Symbol

# About how long the spinner's own reels take with the default settings, so everyone sees the result
# at roughly the same time.
SPIN_SECONDS = 1.1
STAGGER_SECONDS = 0.45
# How long the result stays up afterwards.
RESULT_SECONDS = 5.0


@dataclass(frozen=True)
class WatchedSpin:
    """Someone else's spin, as it looks right now."""

    player_id: int
    name: str
    machine: str
    reels: tuple[ReelView, ...]
    status: str
    tone: Tone
    stopped: bool


@dataclass(frozen=True)
class _Watching:
    name: str
    result: SpinResult
    noun: str | None
    animation: SpinAnimation
    hide_at: float


class Spectator:
    """Keeps track of the other players' spins being watched."""

    def __init__(
        self, *, rng: random.Random | None = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.rng = rng if rng is not None else random.Random()
        self.clock = clock
        self._watching: dict[int, _Watching] = {}
        # Where each player's reels last stopped, per machine, so the next spin carries on from there
        self._positions: dict[tuple[int, str], tuple[int, ...]] = {}
        self._strips: dict[str, tuple[tuple[Symbol, ...], ...]] = {}

    @property
    def active(self) -> bool:
        return bool(self._watching)

    def watch(
        self, player_id: int, name: str, result: SpinResult, loot_type_key: str = DEFAULT_LOOT_TYPE
    ) -> None:
        """
        Starts showing another player's spin. A new spin from the same player replaces the last.

        Args:
            player_id: Who pulled, e.g. their player state's id.
            name: Their name, to label the reels with.
            result: What they rolled.
            loot_type_key: What they picked for loot wins to drop.
        """
        now = self.clock()
        strips = self._strips_for(result.machine_key)
        key = (player_id, result.machine_key)
        animation = plan_spin(
            strips,
            result.line,
            self.rng,
            start_time=now,
            start_indices=self._positions.get(key, (0,) * len(strips)),
            first_stop=SPIN_SECONDS,
            stagger=STAGGER_SECONDS,
        )
        self._positions[key] = animation.final_indices
        self._watching[player_id] = _Watching(
            name,
            result,
            loot_type(loot_type_key).noun,
            animation,
            animation.end_time + RESULT_SECONDS,
        )

    def views(self) -> list[WatchedSpin]:
        """Gets every spin being watched, as it looks right now. Finished ones drop off."""
        now = self.clock()
        for player_id in [pid for pid, watching in self._watching.items() if now >= watching.hide_at]:
            del self._watching[player_id]

        views: list[WatchedSpin] = []
        for player_id, watching in self._watching.items():
            animation = watching.animation
            stopped = animation.done(now)
            reels = tuple(
                ReelView(above, payline, below, reel_stopped)
                for (above, payline, below), reel_stopped in zip(
                    animation.windows(now),
                    animation.stopped(now),
                    strict=True,
                )
            )
            if stopped:
                result = watching.result
                status, tone = describe_result(result, expected_payout(result), watching.noun)
            else:
                status, tone = "Spinning...", Tone.INFO
            machine = MACHINES[watching.result.machine_key].name.upper()
            views.append(WatchedSpin(player_id, watching.name, machine, reels, status, tone, stopped))
        return views

    def clear(self) -> None:
        self._watching.clear()

    def _strips_for(self, machine_key: str) -> tuple[tuple[Symbol, ...], ...]:
        if machine_key not in self._strips:
            machine = MACHINES[machine_key]
            self._strips[machine_key] = tuple(display_strip(reel) for reel in machine.reels)
        return self._strips[machine_key]
