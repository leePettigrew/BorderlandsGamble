"""
Game-agnostic gambling logic, split the way co-op needs it.

- `Casino` is the bank. It runs wherever the game has authority (single player, or the co-op host),
  and checks, charges, rolls, and pays out pulls for any player.
- `SlotController` is one player's machine. It asks a casino for a pull, animates the reels, then
  shows the result. It reaches the casino through a `CasinoLink`: directly when it's the host, or
  over the network (see `coop.py`) when it's a co-op client.

Everything game specific goes through the `Backend` and `Display` protocols, so this can be driven
by fakes in tests and by the real game in `bl4.py` / `overlay.py`.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from . import loot, protocol
from .animation import SpinAnimation, display_strip, plan_spin
from .machines import BET_MULTIPLIERS, MACHINES, spin_cost
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
    from collections.abc import Callable, Mapping

    from .stats import Stats

INT32_MAX = 2_147_483_647

# How long plain messages (e.g. "not enough cash") stay on screen.
MESSAGE_SECONDS = 2.5
# How long a client waits for the host to answer a pull.
REPLY_TIMEOUT = 5.0
# How long the host waits for a client's reels to stop before paying out anyway.
SETTLE_TIMEOUT = 15.0


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
    """
    Everything the casino needs from the game.

    `player` is whatever the game uses to identify a player - in BL4, their player controller.
    """

    def player_key(self, player: Any) -> str:
        """Gets a stable identifier for a player."""
        ...

    def player_name(self, player: Any) -> str:
        """Gets a player's display name, for logging."""
        ...

    def check_can_play(self, player: Any) -> str | None:
        """Returns None if the player can gamble right now, or a user-facing reason why not."""
        ...

    def is_near_machine(self, player: Any, radius: float) -> bool:
        """Checks if the player is within `radius` units of a machine they can gamble at."""
        ...

    def player_level(self, player: Any) -> int | None:
        """Gets the player's level, or None if it can't be read."""
        ...

    def get_balance(self, player: Any, currency: Currency) -> int | None:
        """Gets the player's balance of a currency, or None if it can't be read."""
        ...

    def add_currency(self, player: Any, currency: Currency, amount: int) -> None:
        """Adds (or with a negative amount, removes) currency. Raises on failure."""
        ...

    def spawn_item(self, player: Any, pool: str, level: int, index: int, count: int) -> None:
        """Drops one item in front of the player, as item `index` of `count`. Raises on failure."""
        ...


class Display(Protocol):
    def render(self, view: OverlayView) -> None:
        """Shows the overlay, updated to the given view."""
        ...

    def hide(self) -> None:
        """Hides the overlay."""
        ...


class DisplaySwitch:
    """
    A display that shows on whichever of several displays is active.

    E.g. the menu while it's open, and the HUD overlay otherwise. Switching moves whatever is on
    screen, so closing the menu mid spin finishes the spin on the HUD.
    """

    def __init__(self, displays: Mapping[str, Display], active: str) -> None:
        self.displays = dict(displays)
        self.active = active
        self._view: OverlayView | None = None

    def render(self, view: OverlayView) -> None:
        self._view = view
        self.displays[self.active].render(view)

    def hide(self) -> None:
        self._view = None
        self.displays[self.active].hide()

    def switch(self, name: str) -> None:
        if name == self.active:
            return
        previous = self.displays[self.active]
        self.active = name
        if self._view is not None:
            previous.hide()
            self.displays[name].render(self._view)


# ==================================================================================================
# The bank


@dataclass(frozen=True)
class HouseRules:
    """Settings that belong to whoever runs the casino - in co-op, the host's."""

    luck: float = 1.0
    cost_multiplier: float = 1.0
    require_machine: bool = True
    machine_radius: float = 600.0
    free_play: bool = False
    loot_level: int = 0


@dataclass(frozen=True)
class Payout:
    """What a settled spin actually paid."""

    cash: int = 0
    eridium: int = 0
    items: tuple[Tier, ...] = ()
    errors: tuple[str, ...] = ()


# Either (the spin, how much was charged for it), or why the pull was refused
Reply = tuple[SpinResult, int] | str


@dataclass(frozen=True)
class _PendingPayout:
    player: Any
    request_id: int
    result: SpinResult
    loot_level: int
    deadline: float


class Casino:
    def __init__(
        self,
        backend: Backend,
        rules: Callable[[], HouseRules],
        *,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = print,
    ) -> None:
        self.backend = backend
        self.rules = rules
        self.rng = rng if rng is not None else random.Random()
        self.clock = clock
        self.log = log
        self._pending: dict[str, _PendingPayout] = {}

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def pull(self, player: Any, request_id: int, machine_key: str, bet: int) -> Reply:
        """
        Handles a player pulling the lever: checks, charges, and rolls the spin.

        The payout is held until `settle` is called for it, so it lands when the player's reels stop.

        Args:
            player: The player pulling the lever.
            request_id: The player's id for this pull, echoed back by `settle`.
            machine_key: Which machine they're playing.
            bet: Their bet multiplier.
        Returns:
            The spin and how much was charged, or why the pull was refused.
        """
        key = self.backend.player_key(player)
        if key in self._pending:
            # A new pull always pays out the previous one first
            self.settle(key)

        base_machine = MACHINES.get(machine_key)
        if base_machine is None:
            return f"Unknown machine '{machine_key}'."
        if bet not in BET_MULTIPLIERS:
            return f"Unsupported bet {bet}x."
        rules = self.rules()
        machine = base_machine.with_luck(rules.luck)

        if (reason := self.backend.check_can_play(player)) is not None:
            return reason
        if rules.require_machine and not self.backend.is_near_machine(player, rules.machine_radius):
            return "Find a slot machine or vending machine to gamble at."

        level = self.backend.player_level(player)
        if level is None:
            self.log("Couldn't read the player's level, pricing as level 1.")
            level = 1
        stake = spin_cost(machine, level, bet=bet, cost_multiplier=rules.cost_multiplier)

        charged = 0
        if not rules.free_play:
            charged_or_error = self._charge(player, machine.currency, stake)
            if isinstance(charged_or_error, str):
                return charged_or_error
            charged = charged_or_error

        result = spin(machine, self.rng, stake=stake, bet=bet)
        self._pending[key] = _PendingPayout(
            player=player,
            request_id=request_id,
            result=result,
            loot_level=rules.loot_level if rules.loot_level > 0 else level,
            deadline=self.clock() + SETTLE_TIMEOUT,
        )
        # Don't log the line yet, the console shouldn't spoil the spin
        self.log(
            f"{self.backend.player_name(player)} pulled {machine.name}"
            f" for {format_amount(machine.currency, stake)}",
        )
        return result, charged

    def settle(self, player_key: str, request_id: int | None = None) -> Payout | None:
        """
        Pays out a player's pending spin.

        Args:
            player_key: The player's key, from `Backend.player_key`.
            request_id: If given, only settle if the pending spin has this id.
        Returns:
            What was paid, or None if there was nothing to settle.
        """
        pending = self._pending.get(player_key)
        if pending is None or (request_id is not None and pending.request_id != request_id):
            return None
        del self._pending[player_key]
        return self._pay_out(pending)

    def tick(self) -> bool:
        """
        Pays out spins whose players never confirmed their reels stopped.

        Returns:
            True if any payouts are still pending.
        """
        now = self.clock()
        for key, pending in list(self._pending.items()):
            if now >= pending.deadline:
                self.log(f"{self.backend.player_name(pending.player)}'s reels never stopped, paying out.")
                self.settle(key)
        return self.has_pending

    def settle_all(self) -> None:
        for key in list(self._pending):
            self.settle(key)

    def _charge(self, player: Any, currency: Currency, stake: int) -> int | str:
        """Charges the stake, verifying it actually left the wallet. Returns an error on failure."""
        balance = self.backend.get_balance(player, currency)
        if balance is None:
            return "Couldn't read your wallet - try 'gamble_diag'."
        if balance < stake:
            name = "cash" if currency is Currency.CASH else "eridium"
            return f"Not enough {name}: a pull costs {format_amount(currency, stake)}."

        try:
            self.backend.add_currency(player, currency, -stake)
        except Exception as ex:  # noqa: BLE001 - anything from the game is reported the same way
            self.log(f"Charging {stake} {currency.value} failed: {ex!r}")
            return "Couldn't charge your wallet - try 'gamble_diag'."

        after = self.backend.get_balance(player, currency)
        if after is None or after > balance - stake:
            self.log(f"Charge of {stake} {currency.value} didn't apply ({balance} -> {after}).")
            return "Charge didn't go through, so no spin - try 'gamble_diag'."
        return balance - after

    def _pay(self, player: Any, currency: Currency, amount: int) -> tuple[int, str | None]:
        """Pays out currency, clamped to what the wallet can hold. Returns (paid, error)."""
        if amount <= 0:
            return 0, None
        balance = self.backend.get_balance(player, currency)
        if balance is not None:
            amount = max(0, min(amount, INT32_MAX - balance))
        if amount <= 0:
            return 0, None
        try:
            self.backend.add_currency(player, currency, amount)
        except Exception as ex:  # noqa: BLE001
            self.log(f"Paying {amount} {currency.value} failed: {ex!r}")
            return 0, f"couldn't pay {format_amount(currency, amount)}"
        return amount, None

    def _pay_out(self, pending: _PendingPayout) -> Payout:
        result = pending.result
        errors: list[str] = []
        paid = {Currency.CASH: 0, Currency.ERIDIUM: 0}

        for currency, amount in ((result.currency, result.payout), (Currency.ERIDIUM, result.eridium)):
            amount_paid, error = self._pay(pending.player, currency, amount)
            paid[currency] += amount_paid
            if error:
                errors.append(error)

        dropped: list[Tier] = []
        drops = loot.roll_drops(result.loot, self.rng)
        for idx, (tier, pool) in enumerate(drops):
            try:
                self.backend.spawn_item(pending.player, pool, pending.loot_level, idx, len(drops))
                dropped.append(tier)
            except Exception as ex:  # noqa: BLE001
                self.log(f"Dropping {pool} failed: {ex!r}")
                errors.append(f"couldn't drop a {tier.value} item")

        if errors:
            self.log(f"Payout problems for {self.backend.player_name(pending.player)}: {', '.join(errors)}")
        return Payout(paid[Currency.CASH], paid[Currency.ERIDIUM], tuple(dropped), tuple(errors))


# ==================================================================================================
# Links from a player's machine to the bank


class CasinoLink(Protocol):
    def request(self, request_id: int, machine_key: str, bet: int) -> Reply | None:
        """Asks for a pull. Returns the reply straight away, or None if it'll arrive later."""
        ...

    def settle(self, request_id: int) -> Payout | None:
        """Tells the casino the reels stopped. Returns what was paid, if known."""
        ...


class LocalLink:
    """Plays at a casino running in this same game, i.e. when we're the host."""

    def __init__(self, casino: Casino, player: Callable[[], Any]) -> None:
        self.casino = casino
        self.player = player

    def request(self, request_id: int, machine_key: str, bet: int) -> Reply | None:
        return self.casino.pull(self.player(), request_id, machine_key, bet)

    def settle(self, request_id: int) -> Payout | None:
        return self.casino.settle(self.casino.backend.player_key(self.player()), request_id)


class RemoteLink:
    """Plays at the host's casino over the network. Replies come back via the controller."""

    def __init__(self, send: Callable[[str], None]) -> None:
        self.send = send

    def request(self, request_id: int, machine_key: str, bet: int) -> Reply | None:
        self.send(protocol.encode(protocol.Pull(request_id, machine_key, bet)))
        return None

    def settle(self, request_id: int) -> Payout | None:
        self.send(protocol.encode(protocol.Settle(request_id)))
        return None


# ==================================================================================================
# A player's machine


@dataclass(frozen=True)
class PlayerSettings:
    """Settings each player picks for themselves, even in co-op."""

    machine_key: str = "cash"
    bet: int = 1
    spin_seconds: float = 1.1
    stagger_seconds: float = 0.45
    result_seconds: float = 4.0


@dataclass(frozen=True)
class _Waiting:
    request_id: int
    since: float
    settings: PlayerSettings


@dataclass(frozen=True)
class _Spinning:
    request_id: int
    result: SpinResult
    charged: int
    animation: SpinAnimation
    settings: PlayerSettings


def expected_payout(result: SpinResult) -> Payout:
    """What a spin should pay, for when the casino is remote and can't say what it actually paid."""
    paid = {Currency.CASH: 0, Currency.ERIDIUM: result.eridium}
    paid[result.currency] += result.payout
    items = tuple(tier for tier, count in result.loot for _ in range(count))
    return Payout(paid[Currency.CASH], paid[Currency.ERIDIUM], items)


class SlotController:
    def __init__(
        self,
        link: CasinoLink,
        display: Display,
        *,
        settings: Callable[[], PlayerSettings],
        stats: Stats,
        on_stats_changed: Callable[[Stats], None] | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = print,
    ) -> None:
        self.link = link
        self.display = display
        self.settings = settings
        self.stats = stats
        self.on_stats_changed = on_stats_changed
        self.rng = rng if rng is not None else random.Random()
        self.clock = clock
        self.log = log

        self._strips: dict[str, tuple[tuple[Symbol, ...], ...]] = {}
        self._reel_positions: tuple[int, ...] = (0, 0, 0)
        self._next_request_id = 1
        self._waiting: _Waiting | None = None
        self._spinning: _Spinning | None = None
        self._visible_until: float | None = None
        self._last_view: OverlayView | None = None
        self.last_result: SpinResult | None = None

    @property
    def is_spinning(self) -> bool:
        return self._spinning is not None

    @property
    def is_waiting(self) -> bool:
        return self._waiting is not None

    @property
    def needs_tick(self) -> bool:
        """True while something on screen still needs animating, or a reply is outstanding."""
        return self._spinning is not None or self._waiting is not None or self._visible_until is not None

    def pull(self) -> None:
        """Pulls the lever. Pulling again while the reels spin skips straight to the result."""
        now = self.clock()
        if self._spinning is not None:
            self._finish(now)
            return
        if self._waiting is not None:
            return

        settings = self.settings()
        if settings.machine_key not in MACHINES:
            self._message(f"Unknown machine '{settings.machine_key}'.", Tone.ERROR, now)
            return

        request_id = self._next_request_id
        self._next_request_id += 1
        self._waiting = _Waiting(request_id, now, settings)
        try:
            reply = self.link.request(request_id, settings.machine_key, settings.bet)
        except Exception as ex:  # noqa: BLE001 - e.g. the co-op channel isn't available
            self.log(f"Couldn't reach the casino: {ex!r}")
            reply = "Couldn't reach the casino - try 'gamble_diag'."

        if reply is not None:
            self._on_reply(request_id, reply, now)
        else:
            self._render_waiting(settings)

    def receive_result(self, request_id: int, result: SpinResult, charged: int) -> None:
        """Handles the host's answer to a pull we sent over the network."""
        self._on_reply(request_id, (result, charged), self.clock())

    def receive_error(self, request_id: int, text: str) -> None:
        """Handles the host refusing a pull we sent over the network."""
        self._on_reply(request_id, text, self.clock())

    def tick(self) -> bool:
        """
        Advances the animation. Should be called every frame while `needs_tick` is set.

        Returns:
            True if this still needs to be ticked.
        """
        now = self.clock()
        if self._waiting is not None and now - self._waiting.since >= REPLY_TIMEOUT:
            machine_key = self._waiting.settings.machine_key
            self._waiting = None
            self._message(
                "No answer from the host - do they have Borderlands Gamble enabled?",
                Tone.ERROR,
                now,
                machine_key,
            )
        elif self._spinning is not None:
            if self._spinning.animation.done(now):
                self._finish(now)
            else:
                self._render_spinning(now)
        elif self._waiting is None and self._visible_until is not None and now >= self._visible_until:
            self._visible_until = None
            self._last_view = None
            self.display.hide()
        return self.needs_tick

    def notify(self, text: str, machine_key: str = "cash") -> None:
        """Shows a message for a few seconds, e.g. why the menu can't open. Ignored mid pull."""
        if self._waiting is None and self._spinning is None:
            self._message(text, Tone.ERROR, self.clock(), machine_key)

    def resting_view(self, machine_key: str, status: str = "") -> OverlayView:
        """Gets how a machine looks between pulls: its reels where they last stopped."""
        machine_key = machine_key if machine_key in MACHINES else "cash"
        return OverlayView(
            title=_title(machine_key),
            reels=self._reel_views(machine_key, None, self.clock()),
            status=status,
            tone=Tone.INFO,
            footer="",
        )

    def shutdown(self) -> None:
        """Settles anything in progress, then hides the overlay."""
        if self._spinning is not None:
            self._finish(self.clock())
        self._waiting = None
        self._visible_until = None
        self._last_view = None
        self.display.hide()

    # ==============================================================================================

    def _on_reply(self, request_id: int, reply: Reply, now: float) -> None:
        waiting = self._waiting
        if waiting is None or waiting.request_id != request_id:
            self.log(f"Ignoring a stale reply to pull #{request_id}.")
            return
        self._waiting = None

        if isinstance(reply, str):
            self._message(reply, Tone.ERROR, now, waiting.settings.machine_key)
            return

        result, charged = reply
        animation = plan_spin(
            self._strips_for(result.machine_key),
            result.line,
            self.rng,
            start_time=now,
            start_indices=self._reel_positions,
            first_stop=waiting.settings.spin_seconds,
            stagger=waiting.settings.stagger_seconds,
        )
        self._reel_positions = animation.final_indices
        self._spinning = _Spinning(request_id, result, charged, animation, waiting.settings)

        if animation.done(now):
            self._finish(now)
        else:
            self._render_spinning(now)

    def _finish(self, now: float) -> None:
        spinning = self._spinning
        if spinning is None:
            return
        self._spinning = None
        result = spinning.result
        self.last_result = result

        try:
            payout = self.link.settle(spinning.request_id)
        except Exception as ex:  # noqa: BLE001
            self.log(f"Couldn't settle pull #{spinning.request_id}: {ex!r}")
            payout = None
        if payout is None:
            payout = expected_payout(result)

        self.stats.record(
            result,
            charged=spinning.charged,
            cash_paid=payout.cash,
            eridium_paid=payout.eridium,
            items=list(payout.items),
        )
        if self.on_stats_changed is not None:
            try:
                self.on_stats_changed(self.stats)
            except Exception as ex:  # noqa: BLE001 - never let saving stats break a spin
                self.log(f"Saving stats failed: {ex!r}")

        status, tone = _describe_result(result, payout)
        if payout.errors:
            # The details are in the log, keep the on screen text short
            status = f"{result.prize.title if result.prize else 'Spin'} - payout failed, see console"
            tone = Tone.ERROR
        self.log(f"{' | '.join(SYMBOL_LABELS[s] for s in result.line)} -> {status}")

        self._render(
            OverlayView(
                title=_title(result.machine_key),
                reels=self._reel_views(result.machine_key, spinning.animation, now, force_stopped=True),
                status=status,
                tone=tone,
                footer=self._footer(spinning),
            ),
        )
        self._visible_until = now + max(0.0, spinning.settings.result_seconds)

    def _strips_for(self, machine_key: str) -> tuple[tuple[Symbol, ...], ...]:
        # Always build strips from the base machine, so luck doesn't change how the reels look
        if machine_key not in self._strips:
            machine = MACHINES.get(machine_key, MACHINES["cash"])
            self._strips[machine_key] = tuple(display_strip(reel) for reel in machine.reels)
        return self._strips[machine_key]

    def _footer(self, spinning: _Spinning) -> str:
        machine = MACHINES[spinning.result.machine_key]
        parts = [f"Pull: {format_amount(machine.currency, spinning.result.stake)}"]
        if spinning.result.bet > 1:
            parts[0] += f" (x{spinning.result.bet} bet)"
        if spinning.charged == 0:
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

    def _render_waiting(self, settings: PlayerSettings) -> None:
        self._render(
            OverlayView(
                title=_title(settings.machine_key),
                reels=self._reel_views(settings.machine_key, None, 0.0),
                status="Pulling the lever...",
                tone=Tone.INFO,
                footer="",
            ),
        )

    def _render_spinning(self, now: float) -> None:
        spinning = self._spinning
        if spinning is None:
            return
        self._render(
            OverlayView(
                title=_title(spinning.result.machine_key),
                reels=self._reel_views(spinning.result.machine_key, spinning.animation, now),
                status="Spinning...",
                tone=Tone.INFO,
                footer=self._footer(spinning),
            ),
        )

    def _message(self, text: str, tone: Tone, now: float, machine_key: str = "cash") -> None:
        self.log(text)
        machine_key = machine_key if machine_key in MACHINES else "cash"
        self._render(
            OverlayView(
                title=_title(machine_key),
                reels=self._reel_views(machine_key, None, now),
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


def _describe_result(result: SpinResult, payout: Payout) -> tuple[str, Tone]:
    if result.prize is None:
        return "No luck this time.", Tone.LOSE

    winnings: list[str] = []
    if payout.cash:
        winnings.append("+" + format_amount(Currency.CASH, payout.cash))
    if payout.eridium:
        winnings.append("+" + format_amount(Currency.ERIDIUM, payout.eridium))
    if payout.items:
        winnings.append(describe_loot((tier, payout.items.count(tier)) for tier in Tier))

    status = result.prize.title
    if winnings:
        status += "  " + ", ".join(winnings)

    if not result.won:
        return status, Tone.LOSE
    if result.jackpot:
        return status, Tone.JACKPOT
    if payout.items or result.payout >= 5 * result.stake:
        return status, Tone.BIG_WIN
    return status, Tone.WIN


def _title(machine_key: str) -> str:
    machine: Machine = MACHINES.get(machine_key, MACHINES["cash"])
    return machine.name.upper()
