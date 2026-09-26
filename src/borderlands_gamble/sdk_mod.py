"""Wires the slot machine up to the SDK: options, keybinds, console commands, and the frame tick."""

from __future__ import annotations

import argparse
import time
from typing import TYPE_CHECKING, Any

from mods_base import (
    SETTINGS_DIR,
    BoolOption,
    ButtonOption,
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

from .bl4 import BL4Backend
from .casino import PlaySettings, SlotController
from .machines import BET_MULTIPLIERS, DEFAULT_LUCK, LUCK_PRESETS, MACHINES
from .overlay import POSITIONS, UmgOverlay
from .report import odds_report
from .stats import Stats

if TYPE_CHECKING:
    from mods_base import Mod

PREFIX = "[Borderlands Gamble]"

# How close you need to be to a vending machine to gamble at it, in Unreal units (cm).
MACHINE_RADIUS = 600.0

# The camera tick fires several times a frame, so cap how often we actually do work.
TICK_INTERVAL = 1 / 60

MACHINE_CHOICES = {
    "Loot Slots (cash)": "cash",
    "Eridium Slots": "eridium",
}


def log(message: str) -> None:
    logging.info(f"{PREFIX} {message}")


# ==================================================================================================
# Options

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
    description=(
        "Pulls don't cost anything. For testing, or if a game update breaks charging - see 'gamble_diag'."
    ),
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
loot_level_option = SliderOption(
    "loot_level",
    0,
    0,
    70,
    1,
    display_name="Loot Level",
    description="The level of any loot you win. 0 uses your own level.",
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
stats_option: HiddenOption[Any] = HiddenOption("stats", {})


def _print_odds(_: ButtonOption) -> None:
    run_odds_command(argparse.Namespace(machine=None, luck=None, level=None))


def _reset_stats(_: ButtonOption) -> None:
    run_stats_command(argparse.Namespace(reset=True))


odds_button = ButtonOption(
    "Print Odds To Console",
    on_press=_print_odds,
    description="Prints the exact odds for your current machine and luck to the console.",
)
reset_stats_button = ButtonOption(
    "Reset Stats",
    on_press=_reset_stats,
    description="Forgets your lifetime winnings and losses.",
)


def current_settings() -> PlaySettings:
    try:
        bet = int(bet_option.value.rstrip("x"))
    except ValueError:
        bet = BET_MULTIPLIERS[0]
    return PlaySettings(
        machine_key=MACHINE_CHOICES.get(machine_option.value, "cash"),
        bet=bet,
        cost_multiplier=cost_option.value,
        luck=LUCK_PRESETS.get(luck_option.value, 1.0),
        require_machine=require_machine_option.value,
        machine_radius=MACHINE_RADIUS,
        free_play=free_play_option.value,
        spin_seconds=spin_time_option.value,
        result_seconds=result_time_option.value,
        loot_level=int(loot_level_option.value),
    )


# ==================================================================================================
# Runtime state - created on enable

backend: BL4Backend | None = None
overlay: UmgOverlay | None = None
controller: SlotController | None = None
stats: Stats = Stats()
_last_tick = 0.0


def save_stats(new_stats: Stats) -> None:
    stats_option.value = new_stats.to_json()
    stats_option.save()


def _ensure_ticking() -> None:
    if controller is not None and controller.needs_tick:
        frame_tick.enable()


@hook("/Script/Engine.CameraModifier:BlueprintModifyCamera", Type.POST)
def frame_tick(*_: Any) -> None:
    """Drives the reel animation. Only enabled while something is on screen."""
    global _last_tick
    now = time.monotonic()
    if now - _last_tick < TICK_INTERVAL:
        return
    _last_tick = now

    if controller is None:
        frame_tick.disable()
        return
    try:
        still_needed = controller.tick()
    except Exception as ex:  # noqa: BLE001 - stop ticking rather than erroring every frame
        logging.error(f"{PREFIX} Animation tick failed: {ex!r}")
        still_needed = False
        try:
            controller.settle_now()
        except Exception as settle_ex:  # noqa: BLE001
            logging.error(f"{PREFIX} Couldn't pay out the spin: {settle_ex!r}")
    if not still_needed:
        frame_tick.disable()


def pull_lever() -> None:
    if controller is None:
        logging.warning(f"{PREFIX} Enable the mod first.")
        return
    controller.pull()
    _ensure_ticking()


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
    settings = current_settings()
    machine = MACHINES[args.machine or settings.machine_key]
    luck = args.luck or next(
        (name for name, value in LUCK_PRESETS.items() if value == settings.luck),
        DEFAULT_LUCK,
    )
    level = args.level
    if level is None and backend is not None:
        level = backend.player_level()
    for line in odds_report(machine, luck, level=level or 50):
        log(line)


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
    diag_backend = backend or BL4Backend()
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


# ==================================================================================================
# Lifecycle


def on_enable() -> None:
    global backend, overlay, controller, stats
    # Settings, including saved stats, are always loaded before the mod first gets enabled
    stats = Stats.from_json(stats_option.value)
    backend = BL4Backend()
    overlay = UmgOverlay(lambda: (float(ui_scale_option.value), str(ui_position_option.value)))
    controller = SlotController(
        backend,
        overlay,
        settings=current_settings,
        stats=stats,
        on_stats_changed=save_stats,
        log=log,
    )


def on_disable() -> None:
    global backend, overlay, controller
    frame_tick.disable()
    if controller is not None:
        controller.shutdown()
    if overlay is not None:
        overlay.destroy()
    backend = overlay = controller = None


mod: Mod = build_mod(
    options=[
        machine_option,
        bet_option,
        luck_option,
        cost_option,
        require_machine_option,
        free_play_option,
        spin_time_option,
        result_time_option,
        loot_level_option,
        ui_scale_option,
        ui_position_option,
        odds_button,
        reset_stats_button,
        stats_option,
    ],
    keybinds=[pull_lever_keybind, switch_machine_keybind, change_bet_keybind],
    # The frame tick is enabled on demand, not whenever the mod is
    hooks=[],
    commands=[spin_command, run_odds_command, run_stats_command, diag_command],
    settings_file=SETTINGS_DIR / "borderlands_gamble.json",
    on_enable=on_enable,
    on_disable=on_disable,
)
