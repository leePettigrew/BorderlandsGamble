"""Wires the slot machine up to the SDK: options, keybinds, console commands, co-op, and the frame tick."""

from __future__ import annotations

import argparse
import random
import time
from typing import TYPE_CHECKING, Any

from mods_base import (
    SETTINGS_DIR,
    BoolOption,
    ButtonOption,
    GroupedOption,
    HiddenOption,
    SliderOption,
    SpinnerOption,
    build_mod,
    command,
    hook,
    keybind,
)
from unrealsdk import logging
from unrealsdk.hooks import Type

from . import bl4, coop, protocol
from .casino import Casino, HouseRules, LocalLink, PlayerSettings, RemoteLink, SlotController
from .machines import BET_MULTIPLIERS, DEFAULT_LUCK, LUCK_PRESETS, MACHINES
from .overlay import POSITIONS, UmgOverlay
from .report import odds_report
from .stats import Stats

if TYPE_CHECKING:
    from mods_base import Mod
    from unrealsdk.unreal import UObject

    from .casino import Payout, Reply

PREFIX = "[Borderlands Gamble]"

# How close you need to be to a vending machine to gamble at it, in Unreal units (cm).
MACHINE_RADIUS = 600.0

# The camera tick fires several times a frame, so cap how often we actually do work.
TICK_INTERVAL = 1 / 60

# How long `gamble_coop_test` waits for the host to answer.
PING_TIMEOUT = 5.0

MACHINE_CHOICES = {
    "Loot Slots (cash)": "cash",
    "Eridium Slots": "eridium",
}


def log(message: str) -> None:
    logging.info(f"{PREFIX} {message}")


# ==================================================================================================
# Options - your own machine

machine_option = SpinnerOption(
    "machine",
    next(iter(MACHINE_CHOICES)),
    list(MACHINE_CHOICES),
    wrap_enabled=True,
    display_name="Machine",
    description=(
        "Loot Slots cost cash (scaling with your level). Eridium Slots cost eridium, and pay out"
        " rarer loot more often."
    ),
)
bet_option = SpinnerOption(
    "bet",
    f"{BET_MULTIPLIERS[0]}x",
    [f"{bet}x" for bet in BET_MULTIPLIERS],
    wrap_enabled=True,
    display_name="Bet",
    description="Multiplies both the cost of a pull and everything it pays out, loot included.",
)
spin_time_option = SliderOption(
    "spin_seconds",
    1.1,
    0.0,
    3.0,
    0.1,
    is_integer=False,
    display_name="Spin Time",
    description="How long, in seconds, the first reel spins for. 0 skips the animation.",
)
result_time_option = SliderOption(
    "result_seconds",
    4.0,
    1.0,
    10.0,
    0.5,
    is_integer=False,
    display_name="Result Time",
    description="How long, in seconds, the result stays on screen.",
)
ui_scale_option = SliderOption(
    "ui_scale",
    1.0,
    0.5,
    2.0,
    0.05,
    is_integer=False,
    display_name="Overlay Scale",
    description="How big to draw the slot machine.",
)
ui_position_option = SpinnerOption(
    "ui_position",
    POSITIONS[0],
    list(POSITIONS),
    display_name="Overlay Position",
    description="Where on screen to draw the slot machine.",
)

# ==================================================================================================
# Options - house rules, which the host decides for everyone in co-op

luck_option = SpinnerOption(
    "luck",
    DEFAULT_LUCK,
    list(LUCK_PRESETS),
    display_name="Luck",
    description=(
        "How often loot and vault symbols land. Luckier machines pay out more loot, but less cash."
        " Run 'gamble_odds' in console to see the exact odds."
    ),
)
cost_option = SliderOption(
    "cost_multiplier",
    1.0,
    0.1,
    5.0,
    0.1,
    is_integer=False,
    display_name="Price Multiplier",
    description="Scales the price of every pull.",
)
require_machine_option = BoolOption(
    "require_machine",
    True,
    display_name="Only At Vending Machines",
    description="Only let the lever be pulled while standing next to a vending machine.",
)
free_play_option = BoolOption(
    "free_play",
    False,
    display_name="Free Play",
    description="Pulls don't cost anything. For testing, or if a game update breaks charging.",
)
loot_level_option = SliderOption(
    "loot_level",
    0,
    0,
    70,
    1,
    display_name="Loot Level",
    description="The level of any loot won. 0 uses the winner's own level.",
)

stats_option: HiddenOption[Any] = HiddenOption("stats", {})


def _print_odds(_: ButtonOption) -> None:
    run_odds_command(argparse.Namespace(machine=None, luck=None, level=None))


def _reset_stats(_: ButtonOption) -> None:
    run_stats_command(argparse.Namespace(reset=True))


def player_settings() -> PlayerSettings:
    try:
        bet = int(bet_option.value.rstrip("x"))
    except ValueError:
        bet = BET_MULTIPLIERS[0]
    return PlayerSettings(
        machine_key=MACHINE_CHOICES.get(machine_option.value, "cash"),
        bet=bet,
        spin_seconds=spin_time_option.value,
        result_seconds=result_time_option.value,
    )


def house_rules() -> HouseRules:
    return HouseRules(
        luck=LUCK_PRESETS.get(luck_option.value, 1.0),
        cost_multiplier=cost_option.value,
        require_machine=require_machine_option.value,
        machine_radius=MACHINE_RADIUS,
        free_play=free_play_option.value,
        loot_level=int(loot_level_option.value),
    )


# ==================================================================================================
# Runtime state - created on enable

backend: bl4.BL4Backend | None = None
overlay: UmgOverlay | None = None
casino: Casino | None = None
channel: bl4.CoopChannel | None = None
controller: SlotController | None = None
stats: Stats = Stats()
_last_tick = 0.0
# (nonce, when it was sent) of an outstanding `gamble_coop_test` ping
_ping: tuple[int, float] | None = None


class SmartLink:
    """Plays at our own casino when we're the host, or at the host's when we joined their game."""

    def __init__(self, local: LocalLink, remote: RemoteLink) -> None:
        self.local = local
        self.remote = remote
        self._active: LocalLink | RemoteLink = local

    def request(self, request_id: int, machine_key: str, bet: int) -> Reply | None:
        # Remember where each pull went, so it gets settled in the same place
        self._active = self.local if bl4.is_host() else self.remote
        return self._active.request(request_id, machine_key, bet)

    def settle(self, request_id: int) -> Payout | None:
        return self._active.settle(request_id)


def save_stats(new_stats: Stats) -> None:
    stats_option.value = new_stats.to_json()
    stats_option.save()


def _needs_tick() -> bool:
    return (
        (controller is not None and controller.needs_tick)
        or (casino is not None and casino.has_pending)
        or _ping is not None
    )


def _ensure_ticking() -> None:
    if _needs_tick():
        frame_tick.enable()


def _tick_ping(now: float) -> None:
    global _ping
    if _ping is not None and now - _ping[1] >= PING_TIMEOUT:
        _ping = None
        log("Co-op test: no answer from the host. Do they have Borderlands Gamble enabled?")
        log(f"Co-op test: both players need v{protocol.MOD_VERSION}, and the host needs to run it too.")


@hook("/Script/Engine.CameraModifier:BlueprintModifyCamera", Type.POST)
def frame_tick(*_: Any) -> None:
    """Drives animations and co-op timeouts. Only enabled while something needs it."""
    global _last_tick
    now = time.monotonic()
    if now - _last_tick < TICK_INTERVAL:
        return
    _last_tick = now

    try:
        if controller is not None:
            controller.tick()
        if casino is not None:
            casino.tick()
        _tick_ping(now)
    except Exception as ex:  # noqa: BLE001 - stop ticking rather than erroring every frame
        logging.error(f"{PREFIX} Tick failed: {ex!r}")
        _shutdown_runtime()
    if not _needs_tick():
        frame_tick.disable()


def pull_lever() -> None:
    if controller is None:
        logging.warning(f"{PREFIX} Enable the mod first.")
        return
    controller.pull()
    _ensure_ticking()


# ==================================================================================================
# Co-op


def on_host_message(sender: UObject, text: str) -> None:
    """A client sent us (the host) a message."""
    if casino is None or channel is None:
        return
    coop.handle_host_message(casino, sender, text, lambda reply: channel.send_to_client(sender, reply), log)
    _ensure_ticking()


def on_client_message(text: str) -> None:
    """The host sent us (a client) a message."""
    if controller is None:
        return
    coop.handle_client_message(controller, text, _on_pong, log)
    _ensure_ticking()


def _on_pong(pong: protocol.Pong) -> None:
    global _ping
    if _ping is None or _ping[0] != pong.nonce:
        return
    elapsed = time.monotonic() - _ping[1]
    _ping = None
    log(f"Co-op test: the host answered in {elapsed * 1000:.0f} ms, running v{pong.mod_version}.")
    if pong.mod_version != protocol.MOD_VERSION:
        log(f"Co-op test: you're on v{protocol.MOD_VERSION} - update so you both match.")
    else:
        log("Co-op test: all good, pull away!")


# ==================================================================================================
# Keybinds


@keybind("Pull The Lever", "F8", description="Spins the slot machine. Press again to skip the spin.")
def pull_lever_keybind() -> None:
    pull_lever()


@keybind("Switch Machine", description="Swaps between Loot Slots and Eridium Slots.")
def switch_machine_keybind() -> None:
    choices = list(MACHINE_CHOICES)
    machine_option.value = choices[(choices.index(machine_option.value) + 1) % len(choices)]
    mod.save_settings()
    log(f"Switched to {machine_option.value}.")


@keybind("Change Bet", description="Cycles through the bet multipliers.")
def change_bet_keybind() -> None:
    choices = bet_option.choices
    bet_option.value = choices[(choices.index(bet_option.value) + 1) % len(choices)]
    mod.save_settings()
    log(f"Bet set to {bet_option.value}.")


# ==================================================================================================
# Console commands


@command("gamble_spin", description="Pulls the lever once, as if you pressed the keybind.")
def spin_command(_: argparse.Namespace) -> None:
    pull_lever()


@command("gamble_odds", description="Prints a machine's paytable and exact odds.")
def run_odds_command(args: argparse.Namespace) -> None:
    rules = house_rules()
    machine = MACHINES[args.machine or player_settings().machine_key]
    luck = args.luck or next(
        (name for name, value in LUCK_PRESETS.items() if value == rules.luck),
        DEFAULT_LUCK,
    )
    level = args.level
    pc = bl4.local_player()
    if level is None and backend is not None and pc is not None:
        level = backend.player_level(pc)
    for line in odds_report(machine, luck, level=level or 50):
        log(line)
    if not bl4.is_host():
        log("You joined a co-op game, so the host's luck and prices apply.")


run_odds_command.add_argument("--machine", choices=sorted(MACHINES), help="Which machine to show.")
run_odds_command.add_argument("--luck", choices=list(LUCK_PRESETS), help="Which luck preset to use.")
run_odds_command.add_argument("--level", type=int, help="Which level to quote prices at.")


@command("gamble_stats", description="Prints your lifetime gambling stats.")
def run_stats_command(args: argparse.Namespace) -> None:
    global stats
    if args.reset:
        stats = Stats()
        if controller is not None:
            controller.stats = stats
        save_stats(stats)
        log("Stats reset.")
        return
    for line in stats.summary_lines():
        log(line)


run_stats_command.add_argument("--reset", action="store_true", help="Forget all stats.")


@command("gamble_diag", description="Checks every game API the mod relies on.")
def diag_command(args: argparse.Namespace) -> None:
    diag_backend = backend or bl4.BL4Backend()
    checks = diag_backend.diagnose()
    if args.wallet:
        checks += diag_backend.wallet_test()
    for check in checks:
        log(str(check))
    failed = sum(1 for check in checks if not check.ok)
    log("All good!" if not failed else f"{failed} check(s) failed - see docs/game-api.md.")


diag_command.add_argument(
    "--wallet",
    action="store_true",
    help="Also test charging, by taking $1 and giving it back.",
)


@command("gamble_coop_test", description="Checks that the co-op host can hear you.")
def coop_test_command(_: argparse.Namespace) -> None:
    global _ping
    if channel is None:
        log("Enable the mod first.")
        return
    if bl4.is_host():
        log("You're the host (or playing solo). Ask your co-op partner to run gamble_coop_test.")
        return
    nonce = random.randrange(1, 1_000_000)
    try:
        channel.send_to_host(protocol.encode(protocol.Ping(nonce)))
    except Exception as ex:  # noqa: BLE001
        log(f"Co-op test: couldn't send to the host: {ex!r}")
        return
    _ping = (nonce, time.monotonic())
    log("Co-op test: asked the host, waiting for an answer...")
    _ensure_ticking()


# ==================================================================================================
# Lifecycle


def on_enable() -> None:
    global backend, overlay, casino, channel, controller, stats
    # Settings, including saved stats, are always loaded before the mod first gets enabled
    stats = Stats.from_json(stats_option.value)
    backend = bl4.BL4Backend()
    overlay = UmgOverlay(lambda: (float(ui_scale_option.value), str(ui_position_option.value)))
    casino = Casino(backend, house_rules, log=log)
    channel = bl4.CoopChannel(on_host_message, on_client_message)
    channel.enable()
    controller = SlotController(
        SmartLink(LocalLink(casino, bl4.local_player), RemoteLink(channel.send_to_host)),
        overlay,
        settings=player_settings,
        stats=stats,
        on_stats_changed=save_stats,
        log=log,
    )


def _shutdown_runtime() -> None:
    global _ping
    _ping = None
    frame_tick.disable()
    if controller is not None:
        controller.shutdown()
    if casino is not None:
        casino.settle_all()


def on_disable() -> None:
    global backend, overlay, casino, channel, controller
    _shutdown_runtime()
    if channel is not None:
        channel.disable()
    if overlay is not None:
        overlay.destroy()
    backend = overlay = casino = channel = controller = None


mod: Mod = build_mod(
    options=[
        GroupedOption(
            "Your Machine",
            [
                machine_option,
                bet_option,
                spin_time_option,
                result_time_option,
                ui_scale_option,
                ui_position_option,
            ],
        ),
        GroupedOption(
            "House Rules",
            [luck_option, cost_option, require_machine_option, free_play_option, loot_level_option],
            description="In co-op, the host's house rules apply to everyone.",
        ),
        ButtonOption(
            "Print Odds To Console",
            on_press=_print_odds,
            description="Prints the exact odds for your current machine and luck to the console.",
        ),
        ButtonOption(
            "Reset Stats",
            on_press=_reset_stats,
            description="Forgets your lifetime winnings and losses.",
        ),
        stats_option,
    ],
    keybinds=[pull_lever_keybind, switch_machine_keybind, change_bet_keybind],
    # The frame tick is enabled on demand, not whenever the mod is
    hooks=[],
    commands=[spin_command, run_odds_command, run_stats_command, diag_command, coop_test_command],
    settings_file=SETTINGS_DIR / "borderlands_gamble.json",
    on_enable=on_enable,
    on_disable=on_disable,
)
