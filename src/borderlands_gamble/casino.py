"""
Game-agnostic controller: turns a lever pull into a charge, a spin, an animation, and a payout.

Everything game specific goes through the `Backend` and `Display` protocols, so this can be driven
by fakes in tests and by the real game in `bl4.py` / `overlay.py`.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from . import loot
from .animation import SpinAnimation, display_strip, plan_spin
from .machines import MACHINES, spin_cost
from .slots import (
    SYMBOL_LABELS,
    Currency,
    Machine,
    SpinResult,
    Symbol,
    Tier,
    describe_loot,
    format_amount,
    spin,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .stats import Stats

INT32_MAX = 2_147_483_647

# How long plain messages (e.g. "not enough cash") stay on screen.
MESSAGE_SECONDS = 2.5


class Tone(Enum):
    INFO = "info"
    WIN = "win"
    BIG_WIN = "big_win"
    JACKPOT = "jackpot"
    LOSE = "lose"
    ERROR = "error"


@dataclass(frozen=True)
class ReelView:
    above: Symbol
    payline: Symbol
    below: Symbol
    stopped: bool


@dataclass(frozen=True)
class OverlayView:
    title: str
    reels: tuple[ReelView, ...]
    status: str
    tone: Tone
    footer: str


class Backend(Protocol):
    """Everything the controller needs from the game."""

    def check_can_play(self) -> str | None:
        """Returns None if the player can gamble right now, or a user-facing reason why not."""
        ...

    def is_near_machine(self, radius: float) -> bool:
        """Checks if the player is within `radius` units of a machine they can gamble at."""
        ...

    def player_level(self) -> int | None:
        """Gets the player's level, or None if it can't be read."""
        ...

    def get_balance(self, currency: Currency) -> int | None:
        """Gets the player's balance of a currency, or None if it can't be read."""
        ...

    def add_currency(self, currency: Currency, amount: int) -> None:
        """Adds (or with a negative amount, removes) currency. Raises on failure."""
        ...

    def spawn_item(self, pool: str, level: int, index: int, count: int) -> None:
        """Drops one item from the given pool, as item `index` of `count`. Raises on failure."""
        ...


class Display(Protocol):
    def render(self, view: OverlayView) -> None:
        """Shows the overlay, updated to the given view."""
        ...

    def hide(self) -> None:
        """Hides the overlay."""
        ...


@dataclass(frozen=True)
class PlaySettings:
    machine_key: str = "cash"
    bet: int = 1
    cost_multiplier: float = 1.0
    luck: float = 1.0
    require_machine: bool = True
    machine_radius: float = 600.0
    free_play: bool = False
    spin_seconds: float = 1.1
    stagger_seconds: float = 0.45
    result_seconds: float = 4.0
    loot_level: int = 0


@dataclass(frozen=True)
class _PendingSpin:
    machine: Machine
    settings: PlaySettings
    result: SpinResult
    charged: int
    loot_level: int
    animation: SpinAnimation


class SlotController:
    def __init__(
        self,
        backend: Backend,
        display: Display,
        *,
        settings: Callable[[], PlaySettings],
        stats: Stats,
        on_stats_changed: Callable[[Stats], None] | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = print,
    ) -> None:
        self.backend = backend
        self.display = display
        self.settings = settings
        self.stats = stats
        self.on_stats_changed = on_stats_changed
        self.rng = rng if rng is not None else random.Random()
        self.clock = clock
        self.log = log

        self._strips: dict[str, tuple[tuple[Symbol, ...], ...]] = {}
        self._reel_positions: tuple[int, ...] = (0, 0, 0)
        self._pending: _PendingSpin | None = None
        self._visible_until: float | None = None
        self._last_view: OverlayView | None = None

    @property
    def is_spinning(self) -> bool:
        return self._pending is not None

    @property
    def needs_tick(self) -> bool:
        """True while something on screen still needs animating or hiding."""
        return self._pending is not None or self._visible_until is not None

    def pull(self) -> None:
        """Pulls the lever. Pulling again while the reels spin skips straight to the result."""
        now = self.clock()
        if self._pending is not None:
            self._settle(now)
            return

        settings = self.settings()
        base_machine = MACHINES.get(settings.machine_key)
        if base_machine is None:
            self._message(f"Unknown machine '{settings.machine_key}'.", Tone.ERROR, now)
            return
        machine = base_machine.with_luck(settings.luck)

        if (reason := self.backend.check_can_play()) is not None:
            self._message(reason, Tone.ERROR, now, machine)
            return

        if settings.require_machine and not self.backend.is_near_machine(settings.machine_radius):
            self._message("Find a vending machine to gamble at.", Tone.INFO, now, machine)
            return

        level = self.backend.player_level()
        if level is None:
            self.log("Couldn't read the player's level, pricing as level 1.")
            level = 1
        stake = spin_cost(machine, level, bet=settings.bet, cost_multiplier=settings.cost_multiplier)

        charged = 0
        if not settings.free_play:
            charged_or_error = self._charge(machine.currency, stake)
            if isinstance(charged_or_error, str):
                self._message(charged_or_error, Tone.ERROR, now, machine)
                return
            charged = charged_or_error

        result = spin(machine, self.rng, stake=stake, bet=settings.bet)
        animation = plan_spin(
            self._strips_for(base_machine.key),
            result.line,
            self.rng,
            start_time=now,
            start_indices=self._reel_positions,
            first_stop=settings.spin_seconds,
            stagger=settings.stagger_seconds,
        )
        self._reel_positions = animation.final_indices

        self._pending = _PendingSpin(
            machine=machine,
            settings=settings,
            result=result,
            charged=charged,
            loot_level=settings.loot_level if settings.loot_level > 0 else level,
            animation=animation,
        )
        # Don't log the line yet, the console shouldn't spoil the spin
        self.log(f"Pulled {machine.name} for {format_amount(machine.currency, stake)}")

        if animation.done(now):
            self._settle(now)
        else:
            self._render_spinning(now)

    def tick(self) -> bool:
        """
        Advances the animation. Should be called every frame while `needs_tick` is set.

        Returns:
            True if this still needs to be ticked.
        """
        now = self.clock()
        if self._pending is not None:
            if self._pending.animation.done(now):
                self._settle(now)
            else:
                self._render_spinning(now)
        elif self._visible_until is not None and now >= self._visible_until:
            self._visible_until = None
            self._last_view = None
            self.display.hide()
        return self.needs_tick

    def settle_now(self) -> None:
        """Immediately finishes any spin in progress, paying it out."""
        if self._pending is not None:
            self._settle(self.clock())

    def shutdown(self) -> None:
        """Pays out anything in progress, then hides the overlay."""
        self.settle_now()
        self._visible_until = None
        self._last_view = None
        self.display.hide()

    # ==============================================================================================

    def _strips_for(self, machine_key: str) -> tuple[tuple[Symbol, ...], ...]:
        # Always build strips from the base machine, so luck doesn't change how the reels look
        if machine_key not in self._strips:
            machine = MACHINES.get(machine_key, MACHINES["cash"])
            self._strips[machine_key] = tuple(display_strip(reel) for reel in machine.reels)
        return self._strips[machine_key]

    def _charge(self, currency: Currency, stake: int) -> int | str:
        """Charges the stake, verifying it actually left the wallet. Returns an error on failure."""
        balance = self.backend.get_balance(currency)
        if balance is None:
            return "Couldn't read your wallet - try 'gamble_diag'."
        if balance < stake:
            return f"Not enough {_currency_name(currency)}: a pull costs {format_amount(currency, stake)}."

        try:
            self.backend.add_currency(currency, -stake)
        except Exception as ex:  # noqa: BLE001 - anything from the game is reported the same way
            self.log(f"Charging {stake} {currency.value} failed: {ex!r}")
            return "Couldn't charge your wallet - try 'gamble_diag'."

        after = self.backend.get_balance(currency)
        if after is None or after > balance - stake:
            self.log(f"Charge of {stake} {currency.value} didn't apply ({balance} -> {after}).")
            return "Charge didn't go through, so no spin - try 'gamble_diag'."
        return balance - after

    def _pay(self, currency: Currency, amount: int) -> tuple[int, str | None]:
        """Pays out currency, clamped to what the wallet can hold. Returns (paid, error)."""
        if amount <= 0:
            return 0, None
        balance = self.backend.get_balance(currency)
        if balance is not None:
            amount = max(0, min(amount, INT32_MAX - balance))
        if amount <= 0:
            return 0, None
        try:
            self.backend.add_currency(currency, amount)
        except Exception as ex:  # noqa: BLE001
            self.log(f"Paying {amount} {currency.value} failed: {ex!r}")
            return 0, f"couldn't pay {format_amount(currency, amount)}"
        return amount, None

    def _settle(self, now: float) -> None:
        pending = self._pending
        if pending is None:
            return
        self._pending = None
        result = pending.result

        errors: list[str] = []
        paid = {Currency.CASH: 0, Currency.ERIDIUM: 0}

        amount, error = self._pay(result.currency, result.payout)
        paid[result.currency] += amount
        if error:
            errors.append(error)

        amount, error = self._pay(Currency.ERIDIUM, result.eridium)
        paid[Currency.ERIDIUM] += amount
        if error:
            errors.append(error)

        dropped: list[Tier] = []
        drops = loot.roll_drops(result.loot, self.rng)
        for idx, (tier, pool) in enumerate(drops):
            try:
                self.backend.spawn_item(pool, pending.loot_level, idx, len(drops))
                dropped.append(tier)
            except Exception as ex:  # noqa: BLE001
                self.log(f"Dropping {pool} failed: {ex!r}")
                errors.append(f"couldn't drop a {tier.value} item")

        self.stats.record(
            result,
            charged=pending.charged,
            cash_paid=paid[Currency.CASH],
            eridium_paid=paid[Currency.ERIDIUM],
            items=dropped,
        )
        if self.on_stats_changed is not None:
            try:
                self.on_stats_changed(self.stats)
            except Exception as ex:  # noqa: BLE001 - never let saving stats lose a payout
                self.log(f"Saving stats failed: {ex!r}")

        status, tone = self._describe_result(result, paid, dropped)
        if errors:
            # The details are in the log, keep the on screen text short
            self.log(f"Payout problems: {', '.join(errors)}")
            status = f"{result.prize.title if result.prize else 'Spin'} - payout failed, see console"
            tone = Tone.ERROR
        self.log(f"{' | '.join(SYMBOL_LABELS[s] for s in result.line)} -> {status}")

        self._render(
            OverlayView(
                title=_title(pending.machine),
                reels=self._reel_views(pending.machine.key, pending.animation, now, force_stopped=True),
                status=status,
                tone=tone,
                footer=self._footer(pending.machine, result.stake, pending.settings),
            ),
        )
        self._visible_until = now + max(0.0, pending.settings.result_seconds)

    @staticmethod
    def _describe_result(
        result: SpinResult,
        paid: dict[Currency, int],
        dropped: list[Tier],
    ) -> tuple[str, Tone]:
        if result.prize is None:
            return "No luck this time.", Tone.LOSE

        winnings: list[str] = []
        if paid[Currency.CASH]:
            winnings.append("+" + format_amount(Currency.CASH, paid[Currency.CASH]))
        if paid[Currency.ERIDIUM]:
            winnings.append("+" + format_amount(Currency.ERIDIUM, paid[Currency.ERIDIUM]))
        if dropped:
            winnings.append(describe_loot((tier, dropped.count(tier)) for tier in Tier))

        status = result.prize.title
        if winnings:
            status += "  " + ", ".join(winnings)

        if not result.won:
            return status, Tone.LOSE
        if result.jackpot:
            return status, Tone.JACKPOT
        if dropped or result.payout >= 5 * result.stake:
            return status, Tone.BIG_WIN
        return status, Tone.WIN

    def _footer(self, machine: Machine, stake: int, settings: PlaySettings) -> str:
        parts = [f"Pull: {format_amount(machine.currency, stake)}"]
        if settings.bet > 1:
            parts[0] += f" (x{settings.bet} bet)"
        if settings.free_play:
            parts.append("FREE PLAY")
        net = self.stats.cash_net if machine.currency is Currency.CASH else self.stats.eridium_net
        parts.append(f"Lifetime net: {format_amount(machine.currency, net)}")
        return "   |   ".join(parts)

    def _reel_views(
        self,
        machine_key: str,
        animation: SpinAnimation | None,
        now: float,
        *,
        force_stopped: bool = False,
    ) -> tuple[ReelView, ...]:
        if animation is not None:
            windows = animation.windows(now if not force_stopped else animation.end_time)
            stopped = (True,) * len(windows) if force_stopped else animation.stopped(now)
        else:
            strips = self._strips_for(machine_key)
            windows = tuple(
                (strip[(pos - 1) % len(strip)], strip[pos % len(strip)], strip[(pos + 1) % len(strip)])
                for strip, pos in zip(strips, self._reel_positions, strict=True)
            )
            stopped = (True,) * len(windows)
        return tuple(
            ReelView(above, payline, below, is_stopped)
            for (above, payline, below), is_stopped in zip(windows, stopped, strict=True)
        )

    def _render_spinning(self, now: float) -> None:
        pending = self._pending
        if pending is None:
            return
        self._render(
            OverlayView(
                title=_title(pending.machine),
                reels=self._reel_views(pending.machine.key, pending.animation, now),
                status="Spinning...",
                tone=Tone.INFO,
                footer=self._footer(pending.machine, pending.result.stake, pending.settings),
            ),
        )

    def _message(self, text: str, tone: Tone, now: float, machine: Machine | None = None) -> None:
        self.log(text)
        machine = machine or MACHINES["cash"]
        self._render(
            OverlayView(
                title=_title(machine),
                reels=self._reel_views(machine.key, None, now),
                status=text,
                tone=tone,
                footer="",
            ),
        )
        self._visible_until = now + MESSAGE_SECONDS

    def _render(self, view: OverlayView) -> None:
        if view == self._last_view:
            return
        self._last_view = view
        self.display.render(view)


def _title(machine: Machine) -> str:
    return machine.name.upper()


def _currency_name(currency: Currency) -> str:
    return "cash" if currency is Currency.CASH else "eridium"
