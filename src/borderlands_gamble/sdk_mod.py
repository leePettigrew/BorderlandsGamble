"""Wires the slot machines up to the SDK: options, keybinds, console commands, co-op, and the frame tick."""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
from pathlib import Path
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
from unrealsdk.hooks import Block, Type, log_all_calls

from . import bl4, coop, protocol, world
from .cabinets import MachineOverrides
from .casino import Casino, DisplaySwitch, HouseRules, LocalLink, PlayerSettings, RemoteLink, SlotController
from .loot import DEFAULT_LOOT_TYPE, LOOT_TYPES
from .machines import BET_MULTIPLIERS, DEFAULT_LUCK, LUCK_PRESETS, MACHINES, nice_round, spin_cost
from .menu import IDLE_STATUS, SlotMenu
from .menu_model import (
    ActionGate,
    MenuAction,
    MenuInfo,
    build_menu_info,
    next_bet,
    next_loot_type,
    next_machine,
)
from .overlay import POSITIONS, UmgOverlay, UmgPrompt, UmgSpectators
from .report import odds_report
from .slots import Currency
from .spectate import Spectator
from .stats import Stats

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from mods_base import Mod
    from unrealsdk.unreal import UObject

    from .casino import Payout, Reply
    from .slots import SpinResult

PREFIX = "[Borderlands Gamble]"

# How close you need to be to a machine to gamble at it, in Unreal units (cm).
MACHINE_RADIUS = 600.0
# How close you need to be to one of the mod's slot machines to remove it by command.
REMOVE_RADIUS = 500.0

# The camera tick fires several times a frame, so cap how often we actually do work.
TICK_INTERVAL = 1 / 60
# How often to consider looking for vending machines that streamed in, to put up slot machines by
# them. Looking means going through every loaded object, so it only happens once the player has moved
# a fair way, the map changed, or there's more to put up - or every so often anyway.
WORLD_INTERVAL = 5.0
WORLD_MOVE_DISTANCE = 2000.0
WORLD_MAX_INTERVAL = 30.0
WORLD_BUSY_INTERVAL = 1.0
# How often to check what the player is aiming at, for the "press E" prompt.
AIM_INTERVAL = 0.1
# How often to refresh the menu's wallet, prices and stats.
MENU_INFO_INTERVAL = 0.5
# How long to reuse a read of the player's level for.
LEVEL_CACHE_SECONDS = 5.0
# How often the same background error gets logged.
ERROR_LOG_INTERVAL = 30.0

# How long `gamble_coop_test` waits for the host to answer.
PING_TIMEOUT = 5.0

# Other players' reels float this far above their location, i.e. just over their head.
HEAD_HEIGHT = 115.0
# Further away than this, they show in the corner of the screen instead.
WATCH_DISTANCE = 4000.0

# What `gamble_trace` looks for in the game's function calls.
TRACE_WORDS = ("usable", "interact", "vending", "vendor", "shop", "onused", "_use", "use_")
TRACE_FILE = "unrealsdk.calls.tsv"
# How many lines of a trace to read per frame, so reading it doesn't freeze the game.
TRACE_LINES_PER_FRAME = 20_000

MACHINE_CHOICES = {
    "Loot Slots (cash)": "cash",
    "Eridium Slots": "eridium",
}
MACHINE_LABELS = {key: label for label, key in MACHINE_CHOICES.items()}
LOOT_CHOICES = {loot_type.name: key for key, loot_type in LOOT_TYPES.items()}
LOOT_LABELS = {key: name for name, key in LOOT_CHOICES.items()}


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
        "Loot Slots cost $1,000 per level (at a 1x bet). Eridium Slots cost eridium, and pay out"
        " rarer loot more often. You can also switch in the slot machine menu."
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
loot_option = SpinnerOption(
    "loot_type",
    LOOT_LABELS[DEFAULT_LOOT_TYPE],
    list(LOOT_CHOICES),
    wrap_enabled=True,
    display_name="Drops",
    description=(
        "What loot wins drop. Anything costs the normal price, and picking a kind costs more: guns,"
        " shields, grenades and repkits +25%, one weapon type or enhancements +50%, class mods +100%."
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
menu_scale_option = SliderOption(
    "menu_scale",
    1.0,
    0.5,
    1.5,
    0.05,
    is_integer=False,
    display_name="Menu Scale",
    description="How big to draw the slot machine menu.",
)
ui_scale_option = SliderOption(
    "ui_scale",
    1.0,
    0.5,
    2.0,
    0.05,
    is_integer=False,
    display_name="HUD Reels Scale",
    description="How big to draw the reels on your HUD, e.g. after a Quick Pull.",
)
ui_position_option = SpinnerOption(
    "ui_position",
    POSITIONS[0],
    list(POSITIONS),
    display_name="HUD Reels Position",
    description="Where on screen to draw the reels on your HUD.",
)

# ==================================================================================================
# Options - slot machines in the world


def _on_show_machines(_: BoolOption, value: bool) -> None:
    global _next_world_update
    global _last_scan_position
    if value:
        # Put them up straight away. The option only takes its new value after this returns
        _next_world_update = 0.0
        _last_scan_position = None
        frame_tick.enable()
    else:
        if slot_machines is not None:
            slot_machines.clear()
        if prompt is not None:
            prompt.hide()


def _on_watch_others(_: BoolOption, value: bool) -> None:
    if not value:
        if spectator is not None:
            spectator.clear()
        if spectators is not None:
            spectators.hide()


def _on_signs(_: BoolOption, value: bool) -> None:
    # Put the machines back up with (or without) their signs
    if slot_machines is not None:
        slot_machines.clear()


show_machines_option = BoolOption(
    "show_machines",
    True,
    display_name="Slot Machines In Safehouses",
    description=(
        "Puts a slot machine at the end of each row of vending machines. Aim at one and press the"
        " Use Slot Machine key (E) to play. In co-op, each player's game puts up its own."
    ),
    on_change_while_enabled=_on_show_machines,
)
watch_others_option = BoolOption(
    "watch_others",
    True,
    display_name="Show Others' Spins",
    description=(
        "In co-op, shows your partners' reels spinning above their heads, then what they won. If they're"
        " off screen or far away, it shows in the corner instead."
    ),
    on_change_while_enabled=_on_watch_others,
)
signs_option = BoolOption(
    "machine_signs",
    True,
    display_name="Slot Machine Signs",
    description="Floats a SLOTS sign above each slot machine.",
    on_change_while_enabled=_on_signs,
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
    display_name="Only At Machines",
    description="Only let the lever be pulled next to a slot machine or vending machine.",
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
placed_machines_option: HiddenOption[Any] = HiddenOption("placed_machines", {})


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
        loot_type=LOOT_CHOICES.get(loot_option.value, DEFAULT_LOOT_TYPE),
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
prompt: UmgPrompt | None = None
menu: SlotMenu | None = None
display: DisplaySwitch | None = None
casino: Casino | None = None
channel: bl4.CoopChannel | None = None
controller: SlotController | None = None
slot_machines: world.SlotMachines | None = None
spectator: Spectator | None = None
spectators: UmgSpectators | None = None
overrides: MachineOverrides = MachineOverrides()
stats: Stats = Stats()
gate: ActionGate | None = None

_last_tick = 0.0
_next_world_update = 0.0
_last_scan_at = 0.0
_last_scan_position: tuple[float, float, float] | None = None
# If the last look around stopped with more slot machines left to put up
_world_busy = False
_next_aim_check = 0.0
_next_menu_info = 0.0
# (level, when it was read)
_level_cache: tuple[int | None, float] | None = None
# When the last error was logged, per background job
_last_errors: dict[str, float] = {}
# (nonce, when it was sent) of an outstanding `gamble_coop_test` ping
_ping: tuple[int, float] | None = None
# When a running `gamble_trace` should stop, and reading its file once it has
_trace_until: float | None = None
_trace_steps: Iterator[None] | None = None
# A keybind asked for the menu, and if it was the use key. It opens on the next frame, rather than
# while the game is still in the middle of handling the key press.
_open_requested = False
_opened_by_use = False


class SmartLink:
    """Plays at our own casino when we're the host, or at the host's when we joined their game."""

    def __init__(self, local: LocalLink, remote: RemoteLink) -> None:
        self.local = local
        self.remote = remote
        self._active: LocalLink | RemoteLink = local

    def request(self, request_id: int, machine_key: str, bet: int, loot_type: str) -> Reply | None:
        # Remember where each pull went, so it gets settled in the same place
        self._active = self.local if bl4.is_host() else self.remote
        return self._active.request(request_id, machine_key, bet, loot_type)

    def settle(self, request_id: int) -> Payout | None:
        return self._active.settle(request_id)


def save_stats(new_stats: Stats) -> None:
    stats_option.value = new_stats.to_json()
    stats_option.save()


def save_overrides() -> None:
    placed_machines_option.value = overrides.to_json()
    placed_machines_option.save()


def _world_active() -> bool:
    return slot_machines is not None and show_machines_option.value


def _needs_tick() -> bool:
    return (
        (controller is not None and controller.needs_tick)
        or (casino is not None and casino.has_pending)
        or (menu is not None and menu.is_open)
        or _open_requested
        or (spectator is not None and spectator.active)
        or _world_active()
        or _ping is not None
        or _trace_until is not None
        or _trace_steps is not None
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


def _guarded(name: str, job: Callable[[float], None], now: float) -> None:
    """Runs a background job, logging (now and then) rather than raising if it fails."""
    try:
        job(now)
    except Exception as ex:  # noqa: BLE001 - one broken job mustn't stop the others
        if now - _last_errors.get(name, -ERROR_LOG_INTERVAL) >= ERROR_LOG_INTERVAL:
            _last_errors[name] = now
            logging.error(f"{PREFIX} Updating the {name} failed: {ex!r}")
        if name == "menu":
            close_menu()


@hook("/Script/Engine.CameraModifier:BlueprintModifyCamera", Type.POST)
def frame_tick(*_: Any) -> None:
    """Drives animations, the menu, slot machines in the world, and co-op timeouts."""
    global _last_tick
    now = time.monotonic()
    if now - _last_tick < TICK_INTERVAL:
        return
    _last_tick = now

    # Before anything draws on it: the game may have taken the menu away, e.g. on a map change
    _guarded("menu", _check_menu, now)
    try:
        if controller is not None:
            controller.tick()
        if casino is not None:
            casino.tick()
        _tick_ping(now)
    except Exception as ex:  # noqa: BLE001 - stop ticking rather than erroring every frame
        logging.error(f"{PREFIX} Tick failed: {ex!r}")
        _shutdown_runtime()
        return

    _guarded("menu", _tick_menu, now)
    _guarded("slot machines", _tick_world, now)
    _guarded("other players' spins", _tick_spectators, now)
    _guarded("trace", _tick_trace, now)
    if not _needs_tick():
        frame_tick.disable()


def pull_lever() -> None:
    if controller is None:
        logging.warning(f"{PREFIX} Enable the mod first.")
        return
    controller.pull()
    _refresh_menu_info()
    _ensure_ticking()


# ==================================================================================================
# The menu


def _player_level(pc: UObject) -> int | None:
    global _level_cache
    now = time.monotonic()
    if _level_cache is None or now - _level_cache[1] >= LEVEL_CACHE_SECONDS:
        level = backend.player_level(pc) if backend is not None else None
        _level_cache = (level, now)
    return _level_cache[0]


def _wallet(pc: UObject) -> dict[Currency, int | None]:
    try:
        rows = {name.lower(): amount for name, amount in backend.currency_rows(pc).items()} if backend else {}
    except Exception:  # noqa: BLE001 - shown as unknown
        rows = {}
    return {currency: rows.get(currency.value.lower()) for currency in Currency}


def _menu_info() -> MenuInfo:
    assert controller is not None
    settings = player_settings()
    machine = MACHINES[settings.machine_key]
    rules = house_rules()
    host = bl4.is_host()
    pc = bl4.local_player()

    loot_price = LOOT_TYPES[settings.loot_type].price
    price: int | None = None
    if host and pc is not None:
        level = _player_level(pc)
        if level is not None:
            cost = rules.cost_multiplier * loot_price
            price = spin_cost(machine, level, bet=settings.bet, cost_multiplier=cost)
    elif (
        (last := controller.last_result) is not None
        and (last_settings := controller.last_settings) is not None
        and last.machine_key == settings.machine_key
    ):
        # A client only learns the host's prices from what it's been charged, so scale that
        unit = last.stake / last.bet / LOOT_TYPES[last_settings.loot_type].price
        price = nice_round(unit * loot_price) * settings.bet

    luck = luck_option.value if luck_option.value in LUCK_PRESETS else DEFAULT_LUCK
    return build_menu_info(
        settings.machine_key,
        bet=settings.bet,
        loot_type_key=settings.loot_type,
        price=price,
        free_play=host and rules.free_play,
        luck_name=luck,
        is_host=host,
        wallet=_wallet(pc) if pc is not None else {},
        stats=stats,
        busy=controller.is_waiting or controller.is_spinning,
        spinning=controller.is_spinning,
        pull_key=open_menu_keybind.key,
    )


def _refresh_menu_info() -> None:
    global _next_menu_info
    if menu is not None and menu.is_open and controller is not None:
        menu.set_info(_menu_info())
        _next_menu_info = time.monotonic() + MENU_INFO_INTERVAL


def request_menu(*, by_use: bool = False) -> None:
    """Opens the menu on the next frame."""
    global _open_requested, _opened_by_use
    _open_requested = True
    _opened_by_use = by_use
    frame_tick.enable()


def _check_menu(_now: float) -> None:
    if menu is not None and menu.is_open and not menu.check() and display is not None:
        display.switch("hud")


def open_menu(*, by_use: bool = False) -> None:
    if menu is None or controller is None or display is None or backend is None:
        logging.warning(f"{PREFIX} Enable the mod first.")
        return
    if menu.is_open:
        return
    pc = bl4.local_player()
    if pc is None or pc.Pawn is None:
        log("Load into the game first.")
        return
    settings = player_settings()
    if require_machine_option.value and not backend.is_near_machine(pc, MACHINE_RADIUS):
        # Pulls would be refused anyway, and a menu that stops you moving is no fun mid fight
        controller.notify("Find a slot machine or vending machine to gamble at.", settings.machine_key)
        _ensure_ticking()
        return
    # The SDK doesn't run keybinds while the cursor is showing, so the menu watches their keys itself.
    # The use key only leaves if it's what opened the menu: aiming at the slot machine, the game has
    # nothing to use. Opened some other way, it might be looking at a real vending machine
    extra_keys = {}
    if open_menu_keybind.key is not None:
        extra_keys[open_menu_keybind.key] = MenuAction.PULL
    if by_use and use_machine_keybind.key is not None:
        extra_keys[use_machine_keybind.key] = MenuAction.LEAVE
    if not menu.open(_menu_info(), controller.resting_view(settings.machine_key, IDLE_STATUS), extra_keys):
        log("Couldn't open the slot machine menu - see the errors above.")
        return
    # Carry over a spin that's still going on the HUD
    display.switch("menu")
    if prompt is not None:
        prompt.hide()
    _ensure_ticking()


def close_menu() -> None:
    if menu is None or not menu.is_open:
        return
    menu.close()
    if display is not None:
        display.switch("hud")


def on_menu_action(action: MenuAction) -> None:
    """Handles a button clicked or key pressed in the menu."""
    if controller is None or menu is None or gate is None or not gate.allow(action):
        return
    busy = controller.is_waiting or controller.is_spinning
    settings = player_settings()
    match action:
        case MenuAction.PULL:
            pull_lever()
        case MenuAction.LEAVE:
            close_menu()
        case MenuAction.MACHINE if not busy:
            new_machine = next_machine(settings.machine_key)
            machine_option.value = MACHINE_LABELS[new_machine]
            mod.save_settings()
            menu.render(controller.resting_view(new_machine, IDLE_STATUS))
        case MenuAction.LOOT_NEXT | MenuAction.LOOT_PREV if not busy:
            loot = next_loot_type(settings.loot_type, 1 if action is MenuAction.LOOT_NEXT else -1)
            loot_option.value = LOOT_LABELS[loot]
            mod.save_settings()
        case MenuAction.BET_UP | MenuAction.BET_DOWN if not busy:
            bet = next_bet(settings.bet, 1 if action is MenuAction.BET_UP else -1)
            bet_option.value = f"{bet}x"
            mod.save_settings()
        case _:
            pass
    _refresh_menu_info()


def _tick_menu(now: float) -> None:
    global _open_requested
    if _open_requested:
        _open_requested = False
        open_menu(by_use=_opened_by_use)

    if menu is None or not menu.is_open:
        return
    menu.tick()
    if menu.is_open and now >= _next_menu_info:
        _refresh_menu_info()


# ==================================================================================================
# Slot machines in the world


def _player_position() -> tuple[float, float, float] | None:
    pc = bl4.local_player()
    if pc is None or pc.Pawn is None:
        return None
    loc = pc.Pawn.K2_GetActorLocation()
    return loc.X, loc.Y, loc.Z


def update_world() -> None:
    """Looks around for vending machines now, and puts up or takes down slot machines to match."""
    global _world_busy, _last_scan_at, _last_scan_position
    if slot_machines is None:
        return
    _world_busy = slot_machines.update()
    _last_scan_at = time.monotonic()
    _last_scan_position = _player_position()


def _tick_world(now: float) -> None:
    global _next_world_update, _next_aim_check
    if slot_machines is None or not show_machines_option.value:
        return
    if now >= _next_world_update:
        _next_world_update = now + WORLD_INTERVAL
        position = _player_position()
        moved = (
            position is None
            or _last_scan_position is None
            or math.dist(position, _last_scan_position) >= WORLD_MOVE_DISTANCE
        )
        new_map = world.current_map() != slot_machines.keeper.map_name
        if _world_busy or moved or new_map or now - _last_scan_at >= WORLD_MAX_INTERVAL:
            update_world()
            if _world_busy:
                _next_world_update = now + WORLD_BUSY_INTERVAL
    if now >= _next_aim_check:
        _next_aim_check = now + AIM_INTERVAL
        _update_prompt()


def _update_prompt() -> None:
    if prompt is None or slot_machines is None:
        return
    key = use_machine_keybind.key or open_menu_keybind.key
    if key is None or (menu is not None and menu.is_open) or slot_machines.aimed_at() is None:
        prompt.hide()
        return
    machine = MACHINES[player_settings().machine_key]
    prompt.show(f"[{key}]  PLAY {machine.name.upper()}")


# ==================================================================================================
# Watching other players spin


def _on_spin(player: UObject, result: SpinResult, loot_type_key: str) -> None:
    """The host's casino took a pull: let everyone else watch it."""
    spinner = bl4.player_id(player)
    if spinner is None:
        return
    if channel is not None:
        message = protocol.encode(protocol.to_show(spinner, result, loot_type_key))
        for pc in bl4.other_controllers(exclude=player):
            channel.send_to_client(pc, message)
    if not bl4.same_object(player, bl4.local_player()):
        _watch(spinner, result, loot_type_key)


def _on_show(show: protocol.Show) -> None:
    """The host says another player pulled."""
    local = bl4.local_player()
    if local is not None and bl4.player_id(local) == show.player_id:
        return
    _watch(show.player_id, protocol.to_spin(show), show.loot_type)


def _watch(player_id: int, result: SpinResult, loot_type_key: str) -> None:
    if spectator is None or not watch_others_option.value:
        return
    name = next((name for pid, name, _, _ in bl4.players() if pid == player_id), "Your partner")
    spectator.watch(player_id, name, result, loot_type_key)
    _ensure_ticking()


def _tick_spectators(_now: float) -> None:
    if spectator is None or spectators is None:
        return
    views = spectator.views()
    if not views:
        spectators.hide()
        return

    pc = bl4.local_player()
    pawns = {pid: pawn for pid, _, pawn, _ in bl4.players()} if pc is not None else {}
    ray = world.view_ray(pc) if pc is not None else None
    placed: list[tuple[Any, tuple[float, float] | None]] = []
    for view in views:
        point = None
        pawn = pawns.get(view.player_id)
        if pc is not None and pawn is not None:
            try:
                loc = pawn.K2_GetActorLocation()
                if ray is None or math.dist(ray[0], (loc.X, loc.Y, loc.Z)) <= WATCH_DISTANCE:
                    point = bl4.screen_point(pc, loc.X, loc.Y, loc.Z + HEAD_HEIGHT)
            except Exception:  # noqa: BLE001 - show it in the corner instead
                point = None
        placed.append((view, point))
    spectators.draw(placed)


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
    coop.handle_client_message(controller, text, _on_pong, log, _on_show)
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


@keybind(
    "Open Slot Machine",
    "F8",
    description="Opens the slot machine menu next to any slot or vending machine. In the menu, it pulls.",
)
def open_menu_keybind() -> None:
    # Only fires with no cursor showing, i.e. never while the menu is open - it watches F8 itself
    request_menu()


@keybind(
    "Use Slot Machine",
    "E",
    description=(
        "Plays the slot machine you're looking at. Only takes the key over while you aim at one of"
        " the mod's slot machines, so it keeps working as normal everywhere else."
    ),
)
def use_machine_keybind() -> type[Block] | None:
    if (menu is not None and menu.is_open) or slot_machines is None or not show_machines_option.value:
        return None
    try:
        aimed = slot_machines.aimed_at()
    except Exception as ex:  # noqa: BLE001 - never break the game's own use key
        logging.error(f"{PREFIX} Couldn't check what you're aiming at: {ex!r}")
        return None
    if aimed is None:
        return None
    request_menu(by_use=True)
    return Block


@keybind(
    "Quick Pull",
    description="Pulls the lever without opening the menu, with the reels on your HUD. Press again to skip.",
)
def quick_pull_keybind() -> None:
    pull_lever()


# ==================================================================================================
# Console commands


@command("gamble_spin", description="Pulls the lever once, showing the reels on your HUD.")
def spin_command(_: argparse.Namespace) -> None:
    pull_lever()


@command("gamble_menu", description="Opens the slot machine menu, or closes it if it's open.")
def menu_command(_: argparse.Namespace) -> None:
    if menu is not None and menu.is_open:
        close_menu()
    else:
        open_menu()


@command("gamble_machine", description="Lists, adds, or removes the slot machines on this map.")
def machine_command(args: argparse.Namespace) -> None:
    if slot_machines is None:
        log("Enable the mod first.")
        return
    pc = bl4.local_player()
    map_name = world.current_map()
    if pc is None or pc.Pawn is None or map_name is None:
        log("Load into the game first.")
        return
    loc = pc.Pawn.K2_GetActorLocation()

    match args.action:
        case "add":
            spot = world.player_spot(pc)
            if spot is None:
                log("Load into the game first.")
                return
            overrides.add(map_name, spot)
            save_overrides()
            update_world()
            log("Added a slot machine in front of you. 'gamble_machine remove' takes it away again.")
        case "remove":
            nearest = slot_machines.keeper.nearest(loc.X, loc.Y, loc.Z)
            if nearest is None or nearest[1] > REMOVE_RADIUS:
                log(f"There's no slot machine within {REMOVE_RADIUS / 100:.0f} m of you.")
                return
            cabinet = nearest[0]
            overrides.remove(map_name, cabinet.spot, automatic=cabinet.automatic)
            save_overrides()
            update_world()
            if cabinet.automatic:
                log("Removed. If this group of vending machines has another free spot, it moves there.")
            else:
                log("Removed.")
        case "reset":
            overrides.reset(map_name)
            save_overrides()
            slot_machines.clear()
            update_world()
            log(f"Put the slot machines on {map_name} back to normal.")
        case "refresh":
            slot_machines.clear()
            update_world()
            log("Rebuilt the slot machines nearby.")
        case _:
            cabinets = sorted(
                slot_machines.cabinets,
                key=lambda c: c.spot.distance_to(loc.X, loc.Y, loc.Z),
            )
            log(f"{len(cabinets)} slot machine(s) up on {map_name}:")
            for cabinet in cabinets:
                spot = cabinet.spot
                log(
                    f"  {'automatic' if cabinet.automatic else 'placed by you'},"
                    f" {spot.distance_to(loc.X, loc.Y, loc.Z) / 100:.0f} m away"
                    f" at ({spot.x:.0f}, {spot.y:.0f}, {spot.z:.0f})",
                )
            log(
                f"Your changes on this map: {len(overrides.added.get(map_name, []))} added,"
                f" {len(overrides.removed.get(map_name, []))} removed.",
            )


machine_command.add_argument(
    "action",
    nargs="?",
    default="list",
    choices=("list", "add", "remove", "reset", "refresh"),
    help=(
        "list: show them. add: put one in front of you. remove: take away the nearest one (an"
        " automatic one moves to its next spot). reset: undo your changes on this map. refresh:"
        " rebuild them."
    ),
)


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
    if slot_machines is not None:
        log(f"Slot machines up on this map: {len(slot_machines.cabinets)}.")


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


@command(
    "gamble_trace",
    description="For research: records every game function call for a few seconds.",
)
def trace_command(args: argparse.Namespace) -> None:
    global _trace_until
    if _trace_until is not None:
        log("A trace is already running.")
        return
    seconds = max(1.0, min(float(args.seconds), 30.0))
    log_all_calls(True)
    _trace_until = time.monotonic() + seconds
    log(f"Recording for {seconds:g} seconds - use a vending machine now! The game may stutter a little.")
    _ensure_ticking()


trace_command.add_argument("--seconds", type=float, default=6.0, help="How long to record for.")


def _tick_trace(now: float) -> None:
    global _trace_until, _trace_steps
    if _trace_steps is not None:
        try:
            next(_trace_steps)
        except StopIteration:
            _trace_steps = None
        return
    if _trace_until is None or now < _trace_until:
        return
    _trace_until = None
    log_all_calls(False)
    _trace_steps = _summarize_trace()


def _summarize_trace() -> Iterator[None]:
    """Reads the trace a chunk per frame, then prints what looks use-related."""
    plugins = Path(sys.executable).parent / "Plugins"
    path = next((p for p in (plugins / TRACE_FILE, plugins.parent / TRACE_FILE) if p.exists()), None)
    if path is None:
        log(f"Recording done. It's saved as {TRACE_FILE}, in OakGame/Binaries/Win64/Plugins.")
        return

    counts: dict[str, int] = {}
    with path.open(encoding="utf-8", errors="replace") as file:
        for number, line in enumerate(file, 1):
            fields = line.rstrip("\n").split("\t")
            if len(fields) >= 2 and any(word in line.lower() for word in TRACE_WORDS):
                counts[fields[1]] = counts.get(fields[1], 0) + 1
            if number % TRACE_LINES_PER_FRAME == 0:
                yield
    log(f"Recording done, saved to {path}. Calls that look use-related:")
    for func, count in sorted(counts.items(), key=lambda item: -item[1])[:40]:
        log(f"  {count:>7,}  {func}")
    if not counts:
        log("  none found")


# ==================================================================================================
# Lifecycle


def on_enable() -> None:
    global backend, overlay, prompt, menu, display, casino, channel, controller, slot_machines
    global spectator, spectators, overrides, stats, gate
    # Settings, including saved stats, are always loaded before the mod first gets enabled
    stats = Stats.from_json(stats_option.value)
    overrides = MachineOverrides.from_json(placed_machines_option.value)
    slot_machines = world.SlotMachines(lambda: overrides, lambda: bool(signs_option.value))
    backend = bl4.BL4Backend(slot_machines.bodies)
    overlay = UmgOverlay(lambda: (float(ui_scale_option.value), str(ui_position_option.value)))
    prompt = UmgPrompt()
    menu = SlotMenu(on_menu_action, lambda: float(menu_scale_option.value))
    display = DisplaySwitch({"hud": overlay, "menu": menu}, "hud")
    casino = Casino(backend, house_rules, log=log, on_spin=_on_spin)
    spectator = Spectator(clock=lambda: time.monotonic())
    spectators = UmgSpectators()
    channel = bl4.CoopChannel(on_host_message, on_client_message)
    channel.enable()
    controller = SlotController(
        SmartLink(LocalLink(casino, bl4.local_player), RemoteLink(channel.send_to_host)),
        display,
        settings=player_settings,
        stats=stats,
        on_stats_changed=save_stats,
        log=log,
    )
    gate = ActionGate(lambda: time.monotonic())
    _ensure_ticking()


def _shutdown_runtime() -> None:
    global _ping, _trace_until, _trace_steps, _open_requested
    _ping = None
    _open_requested = False
    _trace_steps = None
    if _trace_until is not None:
        _trace_until = None
        log_all_calls(False)
    if spectator is not None:
        spectator.clear()
    if spectators is not None:
        spectators.hide()
    frame_tick.disable()
    close_menu()
    if prompt is not None:
        prompt.hide()
    if controller is not None:
        controller.shutdown()
    if casino is not None:
        casino.settle_all()


def on_disable() -> None:
    global backend, overlay, prompt, menu, display, casino, channel, controller, slot_machines, gate
    global spectator, spectators
    _shutdown_runtime()
    if channel is not None:
        channel.disable()
    if slot_machines is not None:
        slot_machines.clear()
    if prompt is not None:
        prompt.destroy()
    if overlay is not None:
        overlay.destroy()
    if spectators is not None:
        spectators.destroy()
    backend = overlay = prompt = menu = display = casino = channel = controller = slot_machines = gate = None
    spectator = spectators = None


mod: Mod = build_mod(
    options=[
        GroupedOption(
            "Your Machine",
            [
                machine_option,
                loot_option,
                bet_option,
                spin_time_option,
                result_time_option,
                menu_scale_option,
                ui_scale_option,
                ui_position_option,
            ],
        ),
        GroupedOption("Slot Machines", [show_machines_option, signs_option, watch_others_option]),
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
        placed_machines_option,
    ],
    keybinds=[open_menu_keybind, use_machine_keybind, quick_pull_keybind],
    # The frame tick is enabled on demand, not whenever the mod is
    hooks=[],
    commands=[
        spin_command,
        menu_command,
        machine_command,
        run_odds_command,
        run_stats_command,
        diag_command,
        coop_test_command,
        trace_command,
    ],
    settings_file=SETTINGS_DIR / "borderlands_gamble.json",
    on_enable=on_enable,
    on_disable=on_disable,
)
