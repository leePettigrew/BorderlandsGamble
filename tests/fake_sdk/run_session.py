"""
Plays a session of the mod inside the fake game, using the real mods_base.

Builds the `.sdkmod`, drops it into a scratch `sdk_mods` folder next to a copy of mods_base, and
imports it the same way the mod manager does. Then enables the mod, pulls the lever, drives the
frame tick, and checks what happened to the (fake) wallet, loot, overlay and settings. It also walks
up to a slot machine in a safehouse, presses E, and plays through the menu.

Usage: python tests/fake_sdk/run_session.py <path to a bl-sdk/mods_base checkout>
Needs Python 3.14+, like the SDK itself.
"""

from __future__ import annotations

import importlib
import json
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TICK_FUNC = "/Script/Engine.CameraModifier:BlueprintModifyCamera"
SERVER_RPC = "/Script/Engine.PlayerController:ServerExec"
CLIENT_RPC = "/Script/Engine.PlayerController:ClientMessage"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class RiggedRandom(random.Random):
    """Seeded, except the next reel draws come from a queue."""

    def __init__(self) -> None:
        super().__init__(1234)
        self.queue: list[Any] = []

    def choices(self, population: Any, *args: Any, **kwargs: Any) -> list[Any]:
        from borderlands_gamble.slots import Symbol

        if self.queue and all(isinstance(x, Symbol) for x in population):
            return [self.queue.pop(0)]
        return super().choices(population, *args, **kwargs)


def play_in_the_world(
    game: Any,
    sdk_mod: Any,
    commands: Any,
    hooks: Any,
    tick: Any,
    tick_until_idle: Any,
    statuses: Any,
    logged: Any,
    rng: RiggedRandom,
    tmp: Path,
) -> None:
    """Walks up to a slot machine in a safehouse, and plays it through the menu."""
    import fake_game

    from borderlands_gamble.menu_model import MenuAction
    from borderlands_gamble.slots import Symbol

    pc = game.pc
    pc.authority = True
    machines = sdk_mod.slot_machines
    settings_file = tmp / "sdk_mods" / "settings" / "borderlands_gamble.json"

    def spots() -> list[tuple[float, ...]]:
        return sorted(tuple(round(v, 1) + 0.0 for v in c.spot.to_json()) for c in machines.cabinets)

    # ---- Slot machines go up next to the vending machines, a couple per update ----
    tick(sdk_mod.WORLD_INTERVAL + 0.5)
    assert spots() == [(1200.0, 2390.0, 300.0, 180.0), (29_840.0, 0.0, 0.0, 90.0)], spots()
    for kind, per_machine in (("StaticMeshActor", 2), ("SkeletalMeshActor", 1), ("TextRenderActor", 1)):
        actors = game.live_actors(kind)
        assert len(actors) == 2 * per_machine, (kind, len(actors))
    assert all(a.finished and a.replicates_at_finish is False for a in game.actors), (
        "copies mustn't replicate"
    )
    body = next(a for a in game.live_actors("StaticMeshActor") if a.location == (1200.0, 2390.0, 300.0))
    component = body.StaticMeshComponent
    assert component.mobility == 2 and component.mesh.Name == "SM_VendingMachine_Ammo_2_Body", component.mesh
    source = next(m for m in game.machines if m.Name == "VendingMachine_Ammo_2").components[0]
    assert component.materials == source.materials, "materials should be copied"
    door = next(a for a in game.live_actors("SkeletalMeshActor") if a.location[1] == 2390.0)
    assert door.location == (1170.0, 2390.0, 360.0), door.location
    assert door.SkeletalMeshComponent.mesh.Name == "SK_VendingMachine_Ammo_2_Door"
    assert all(a.TextRender.text == "SLOTS" for a in game.live_actors("TextRenderActor"))
    # No collision, so partners with different machines can't hit invisible walls
    assert not any(a.collision for a in game.live_actors() if a.class_name != "TextRenderActor")
    assert not any(
        a.StaticMeshComponent.mesh.Name == "SM_LOD_Proxy" for a in game.live_actors("StaticMeshActor")
    )

    # ---- Aiming at one shows a prompt ----
    pc.Pawn.location = (1000.0, 2390.0, 300.0)
    pc.PlayerCameraManager.look_at(1200.0, 2390.0, 400.0)
    tick(0.2)
    # Still on Eridium Slots from earlier
    assert "[E]  PLAY ERIDIUM SLOTS" in statuses(), statuses()[-5:]
    prompt_root = sdk_mod.prompt._root()
    assert prompt_root.visibility == 3
    pc.PlayerCameraManager.look_at(1000.0, 3000.0, 400.0)
    tick(0.2)
    assert prompt_root.visibility == 1, "the prompt should go when you look away"
    # E does its normal thing when you're not aiming at a slot machine
    assert sdk_mod.use_machine_keybind.callback() is None
    assert not sdk_mod.menu.is_open
    # Nor when aiming at the real vending machine next to it
    pc.PlayerCameraManager.look_at(1200.0, 2230.0, 400.0)
    tick(0.2)
    assert sdk_mod.use_machine_keybind.callback() is None
    assert prompt_root.visibility == 1

    # A wall between the player and the slot machine: no prompt, and E does its normal thing
    game.walls = [(1100.0, 1110.0, 2300.0, 2500.0)]
    pc.PlayerCameraManager.look_at(1200.0, 2390.0, 400.0)
    tick(0.2)
    assert prompt_root.visibility == 1, "no prompt through walls"
    assert sdk_mod.use_machine_keybind.callback() is None
    assert game.traces > 0
    game.walls = []

    # ---- Pressing E opens the menu ----
    pc.PlayerCameraManager.look_at(1200.0, 2390.0, 400.0)
    assert sdk_mod.use_machine_keybind.callback() is hooks.Block, "E should be kept from the game"
    menu = sdk_mod.menu
    assert not menu.is_open, "the menu opens on the next frame, not mid key press"
    tick(1 / 30)
    assert menu.is_open
    assert pc.bShowMouseCursor is True and pc.ignore_look == 1 and pc.ignore_move == 1
    assert "gbx.ui.view.stateadd CINEMATIC" in game.console_commands
    mode, focus = game.input_mode
    buttons = {button.action: button for button in menu._widgets.buttons}
    assert mode == "GameAndUI" and focus is buttons[MenuAction.PULL].hit, game.input_mode
    tick(0.2)
    assert prompt_root.visibility == 1, "no prompt while the menu is open"
    shown = statuses()
    for text in ("ERIDIUM SLOTS", "PULL THE LEVER  (20 eridium)", "PAYTABLE  (x2 bet)", "3x VAULT", "LEAVE"):
        assert text in shown, text
    assert any(text.startswith("Cash $") for text in shown)

    # Only PULL can take keyboard focus, so Space and Enter always pull
    assert not hasattr(buttons[MenuAction.PULL].hit, "IsFocusable")
    assert all(buttons[a].hit.IsFocusable is False for a in buttons if a is not MenuAction.PULL)

    def click(action: MenuAction) -> None:
        button = buttons[action].hit
        button.pressed = True
        tick(0.1)
        button.pressed = False
        tick(0.3)

    def press(*keys: str) -> None:
        pc.keys_down = set(keys)
        tick(0.1)
        pc.keys_down = set()
        tick(0.3)

    # The player left Eridium Slots at 2x earlier - switch back, and up the bet
    click(MenuAction.MACHINE)
    assert sdk_mod.player_settings().machine_key == "cash"
    assert "LOOT SLOTS" in statuses()
    click(MenuAction.BET_UP)
    assert sdk_mod.player_settings().bet == 5
    assert "PULL THE LEVER  ($13,000)" in statuses()
    saved = json.loads(settings_file.read_text())
    assert saved["options"]["Your Machine"]["bet"] == "5x", saved

    # If clicks never reach the buttons, the raw mouse still works
    x, y, w, h = buttons[MenuAction.BET_UP].rect
    pc.mouse = (x + w / 2, y + h / 2)
    press("LeftMouseButton")
    assert sdk_mod.player_settings().bet == 10
    pc.mouse = None
    press("Down")
    press("Down")
    assert sdk_mod.player_settings().bet == 2

    # Drops: pick what loot wins drop, for a price
    click(MenuAction.LOOT_NEXT)
    assert sdk_mod.player_settings().loot_type == "guns"
    assert "DROPS: GUNS  +25%" in statuses()
    assert "PULL THE LEVER  ($6,600)" in statuses()
    press("Left")
    assert sdk_mod.player_settings().loot_type == "any"
    saved = json.loads(settings_file.read_text())
    assert saved["options"]["Your Machine"]["loot_type"] == "Anything", saved

    # Space pulls. Switching machine or bet mid-spin is ignored
    rng.queue = [Symbol.CASH] * 3
    cash_before = game.cash()
    press("SpaceBar")
    assert sdk_mod.controller.is_spinning
    assert game.cash() == cash_before - 5200
    assert "Spinning..." in statuses()
    assert "SKIP" in statuses()
    click(MenuAction.MACHINE)
    assert sdk_mod.player_settings().machine_key == "cash"

    # Escape leaves, on release. The spin carries on on the HUD
    pc.keys_down = {"Escape"}
    tick(0.1)
    assert menu.is_open, "leaving waits for Escape to come back up"
    pc.keys_down = set()
    tick(0.1)
    assert not menu.is_open
    assert pc.bShowMouseCursor is False and pc.ignore_look == 0 and pc.ignore_move == 0
    assert game.console_commands[-1] == "gbx.ui.view.stateremove CINEMATIC"
    assert game.input_mode == ("GameOnly", None)
    tick_until_idle()
    assert game.cash() == cash_before - 5200 + 20 * 5200, game.cash()
    assert sdk_mod.overlay._root() is not None, "the HUD should have taken over the spin"

    # ---- F8 opens the menu near any machine, and pulls inside it ----
    sdk_mod.open_menu_keybind.callback()
    tick(1 / 30)
    assert menu.is_open
    rng.queue = [Symbol.SKULL, Symbol.CASH, Symbol.EPIC]
    tick(0.3)
    # The SDK doesn't run keybinds while the cursor shows, so the menu watches F8 itself
    press("F8")
    assert sdk_mod.controller.is_spinning
    tick_until_idle()
    assert "Pull the lever!" in statuses()
    # Opened with F8, the player could be looking at a real vending machine, so E isn't taken over
    press("E")
    assert menu.is_open
    press("Escape")
    assert not menu.is_open

    # Opened with E at a slot machine, E leaves again
    assert sdk_mod.use_machine_keybind.callback() is hooks.Block
    tick(1 / 30)
    assert menu.is_open
    press("E")
    assert not menu.is_open

    # A win at a slot machine drops on the floor in front of it, from the chosen kind of loot
    sdk_mod.loot_option.value = "Shotguns"
    rng.queue = [Symbol.RARE] * 3
    spawned_before = len(game.spawned)
    commands.run("gamble_spin")
    tick_until_idle()
    drops = game.spawned[spawned_before:]
    assert [pool for pool, _, _ in drops] == ["itempool_sg_03_rare"] * 2, drops
    landed = sorted((round(x), round(y), round(z)) for _, _, (x, y, z) in drops)
    assert landed == [(1090, 2360, 360), (1090, 2420, 360)], landed
    sdk_mod.loot_option.value = "Anything"
    commands.run("gamble_menu")
    assert menu.is_open
    commands.run("gamble_menu")
    assert not menu.is_open

    # A map change takes the menu's widgets - it closes, and hands control back
    commands.run("gamble_menu")
    game.collect_widgets()
    tick(0.1)
    assert not menu.is_open and pc.bShowMouseCursor is False
    assert game.console_commands[-1] == "gbx.ui.view.stateremove CINEMATIC"

    # As does respawning
    commands.run("gamble_menu")
    old_pawn = pc.Pawn
    pc.Pawn = fake_game.Pawn(old_pawn.location)
    tick(0.1)
    assert not menu.is_open
    pc.Pawn = old_pawn

    # Far from any machine, F8 says where to go instead of opening
    pc.Pawn.location = (9000.0, 9000.0, 300.0)
    sdk_mod.open_menu_keybind.callback()
    tick(1 / 30)
    assert not menu.is_open
    assert "Find a slot machine or vending machine to gamble at." in statuses()
    tick_until_idle()

    # ---- Placing and removing machines by hand ----
    pc.Pawn.location = (5000.0, 2000.0, 300.0)
    pc.Pawn.yaw = 90.0
    commands.run("gamble_machine add")
    assert (5000.0, 2150.0, 210.0, 270.0) in spots(), spots()
    saved = json.loads(settings_file.read_text())
    assert saved["options"]["placed_machines"]["added"] == {"Kairos_P": [[5000.0, 2150.0, 210.0, 270.0]]}, (
        saved
    )

    # The host counts the mod's own slot machines, not just vending machines
    rng.queue = [Symbol.SKULL, Symbol.SKULL, Symbol.CASH]
    cash_before = game.cash()
    commands.run("gamble_spin")
    assert game.cash() == cash_before - 5200, "a slot machine should count as a machine"
    tick_until_idle()

    commands.run("gamble_machine remove")
    assert (5000.0, 2150.0, 210.0, 270.0) not in spots()

    # Removing an automatic one moves it to the other end of the row
    pc.Pawn.location = (1100.0, 2390.0, 300.0)
    commands.run("gamble_machine remove")
    tick(0.1)
    assert (1200.0, 1940.0, 300.0, 180.0) in spots(), spots()
    commands.run("gamble_machine")
    assert any("Your changes on this map: 0 added, 1 removed." in line for line in logged("info"))
    commands.run("gamble_machine reset")
    assert (1200.0, 2390.0, 300.0, 180.0) in spots(), spots()
    before = {id(a) for a in game.live_actors()}
    commands.run("gamble_machine refresh")
    assert not before & {id(a) for a in game.live_actors()}, "refresh should rebuild them"

    # ---- A new map ----
    game.map_name = "Fadefields_P"
    game.collect_actors()
    tick(sdk_mod.WORLD_INTERVAL + 0.5)
    assert machines.keeper.map_name == "Fadefields_P"
    assert len(machines.cabinets) == 2
    game.map_name = "Kairos_P"

    # ---- Turning them off takes them down, and lets the tick rest ----
    sdk_mod.show_machines_option.value = False
    assert not game.live_actors()
    tick(0.1)
    assert not hooks.has_hook(TICK_FUNC, hooks.Type.POST, sdk_mod.frame_tick.hook_identifier)

    # ---- Tracing function calls, to research the native use prompt ----
    plugins = tmp / "Plugins"
    plugins.mkdir(exist_ok=True)
    used = (
        "/Game/InteractiveObjects/Vending/Script_Vending.Script_Vending_C"
        ":GbxActorScriptEvt__UsableActorState_K2_OnUsed"
    )
    (plugins / "unrealsdk.calls.tsv").write_text(
        f"ProcessEvent\t{used}\tScript_Vending_C_0\n" * 3
        + "ProcessEvent\t/Script/Engine.Actor:ReceiveTick\tActor_0\n",
    )
    commands.run("gamble_trace --seconds 1")
    assert hooks.LOG_ALL_CALLS == [True]
    tick_until_idle()
    assert hooks.LOG_ALL_CALLS == [True, False]
    assert any(line.endswith(f"3  {used}") for line in logged("info")), logged("info")[-5:]
    assert not any("ReceiveTick" in line for line in logged("info"))

    pc.Pawn.location = (1000.0, 2000.0, 300.0)


def panel_position(panel: Any) -> tuple[float, float]:
    """Where a spectator panel was last put, in layout units."""
    _, position = [call for call in panel.canvas.Slot.calls if call[0] == "SetPosition"][-1]
    return position.X, position.Y


def fake_game_width(sdk_mod: Any) -> float:
    """How wide a spectator panel is on the fake game's screen, in layout units."""
    from borderlands_gamble.overlay import SPECTATOR_W

    return SPECTATOR_W * sdk_mod.spectators._scale


def main(mods_base_dir: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="bl4_fake_game_"))
    sdk_mods = tmp / "sdk_mods"
    shutil.copytree(
        mods_base_dir, sdk_mods / "mods_base", ignore=shutil.ignore_patterns(".git*", "__pycache__")
    )

    sys.path.insert(0, str(REPO / "tools"))
    import build_sdkmod

    sdkmod = build_sdkmod.build(sdk_mods)
    build_sdkmod.validate(sdkmod)

    # Pretend to be the game: the exe name picks the game, and the clock drives animations
    sys.executable = str(tmp / "Borderlands4.exe")
    clock = FakeClock()
    time.monotonic = clock
    sys.path[:0] = [str(HERE), str(sdk_mods)]
    sys.path.append(str(sdkmod))  # How the mod manager imports .sdkmod files

    import fake_game
    from unrealsdk import commands, hooks, logging

    game = fake_game.GAME
    importlib.import_module("mods_base")
    gamble = importlib.import_module("borderlands_gamble")
    sdk_mod = importlib.import_module("borderlands_gamble.sdk_mod")
    mod = gamble.mod

    assert mod.name == "Borderlands Gamble", mod.name
    assert mod.version == "0.4.0", mod.version
    assert not mod.enabling_locked, "mod should be allowed to enable in BL4"
    assert ".sdkmod" in str(sdk_mod.__file__), f"should import from the .sdkmod, not {sdk_mod.__file__}"

    from unrealsdk.unreal import WrappedStruct

    from borderlands_gamble import protocol
    from borderlands_gamble.menu_model import MenuAction
    from borderlands_gamble.slots import Symbol

    def ticking() -> bool:
        return hooks.has_hook(TICK_FUNC, hooks.Type.POST, sdk_mod.frame_tick.hook_identifier)

    def busy() -> bool:
        return (
            sdk_mod.controller.needs_tick
            or sdk_mod.casino.has_pending
            or sdk_mod._ping is not None
            or sdk_mod._trace_until is not None
            or sdk_mod._trace_steps is not None
        )

    def tick(seconds: float = 1 / 15) -> None:
        """Runs the game for a while, at 30 frames a second."""
        end = clock.now + seconds
        while clock.now < end:
            clock.now += 1 / 30
            hooks.fire(TICK_FUNC)

    def tick_until_idle(limit: float = 30.0) -> int:
        frames = 0
        end = clock.now + limit
        while busy():
            assert ticking(), "the frame tick is off while something still needs it"
            clock.now += 1 / 30
            hooks.fire(TICK_FUNC)
            frames += 1
            assert clock.now < end, "never finished"
        return frames

    def statuses() -> list[str]:
        return [text for block in game.text_blocks() for text in block.texts]

    def logged(level: str | None = None) -> list[str]:
        return [line for lvl, line in logging.LINES if level is None or lvl == level]

    mod.enable()
    assert mod.is_enabled
    for cmd in (
        "gamble_spin",
        "gamble_menu",
        "gamble_machine",
        "gamble_odds",
        "gamble_stats",
        "gamble_diag",
        "gamble_coop_test",
        "gamble_trace",
    ):
        assert commands.has_command(cmd), cmd
    # Slot machines in the world are on by default, which keeps the frame tick running
    assert ticking()

    # Diagnostics should find everything in the fake game
    commands.run("gamble_diag --wallet")
    assert any("All good!" in line for line in logged("info")), logged()
    assert game.cash() == 1_000_000, "wallet test should give the dollar back"

    commands.run("gamble_odds --machine eridium --luck Generous")
    assert any("Eridium Slots (Generous luck)" in line for line in logged("info"))

    # A rigged jackpot on Loot Slots
    rng = RiggedRandom()
    sdk_mod.casino.rng = rng
    rng.queue = [Symbol.VAULT] * 3
    commands.run("gamble_spin")
    assert game.cash() == 1_000_000 - 2600, game.cash()
    assert sdk_mod.controller.is_spinning
    frames = tick_until_idle()
    assert frames > 10, f"expected an animation, got {frames} frames"
    assert game.cash() == 1_000_000 - 2600 + 50 * 2600, game.cash()
    assert [pool.endswith("_05_legendary") for pool, _, _ in game.spawned] == [True, True], game.spawned
    assert all(level == 50 for _, level, _ in game.spawned)
    for _, _, (x, y, z) in game.spawned:
        # Player at (1000, 2000, 300) facing +Y, so loot lands in front of them
        assert y > 2100 and abs(x - 1000) < 200 and z > 300, (x, y, z)
    assert any("JACKPOT!" in text for text in statuses()), statuses()
    assert {"VAULT", "SKULL", "$$$"} <= set(statuses())

    user_widgets = [w for w in game.widgets if isinstance(w, fake_game.UserWidget)]
    assert len(user_widgets) == 1 and user_widgets[0].in_viewport
    assert user_widgets[0].visibility == 1, "overlay should collapse once the result times out"

    settings_file = sdk_mods / "settings" / "borderlands_gamble.json"
    saved = json.loads(settings_file.read_text())
    assert saved["options"]["stats"]["jackpots"] == 1, saved
    assert saved["options"]["stats"]["items"] == {"legendary": 2}, saved
    assert saved["enabled"] is True

    # Eridium Slots with a 2x bet, via the menu's actions, then a quick pull pressed twice to skip
    # the animation
    sdk_mod.on_menu_action(MenuAction.MACHINE)
    sdk_mod.on_menu_action(MenuAction.BET_UP)
    assert sdk_mod.player_settings().machine_key == "eridium"
    assert sdk_mod.player_settings().bet == 2
    saved = json.loads(settings_file.read_text())
    assert saved["options"]["Your Machine"]["machine"] == "Eridium Slots", saved
    rng.queue = [Symbol.ERIDIUM] * 3
    eridium_before = game.pc.CurrencyManager.row("eridium").Amount
    sdk_mod.quick_pull_keybind.callback()
    sdk_mod.quick_pull_keybind.callback()
    assert not sdk_mod.controller.is_spinning
    assert game.pc.CurrencyManager.row("eridium").Amount == eridium_before - 20 + 15 * 20
    tick_until_idle()

    # Too far from a machine
    game.pc.Pawn.location = (50_000.0, 0.0, 0.0)
    calls_before = len(game.give_calls)
    commands.run("gamble_spin")
    assert len(game.give_calls) == calls_before
    assert any("Find a slot machine" in text for text in statuses())
    tick_until_idle()
    game.pc.Pawn.location = (1000.0, 2000.0, 300.0)

    # ---- Co-op, as the host: our partner Zane's pulls arrive as ServerExec calls ----
    def from_friend(message: protocol.Message | str) -> bool:
        text = message if isinstance(message, str) else protocol.encode(message)
        return hooks.fire(SERVER_RPC, hooks.Type.PRE, game.friend, WrappedStruct("ServerExec", Msg=text))

    def replies_to_friend() -> list[protocol.Message | None]:
        replies = [protocol.decode(text) for func, text in game.friend.sent if func == "ClientMessage"]
        game.friend.sent.clear()
        return replies

    # Every pull the host made so far was shown to Zane, so he could watch
    shows = replies_to_friend()
    assert shows and all(isinstance(m, protocol.Show) and m.player_id == 256 for m in shows), shows
    assert shows[0].line == (Symbol.VAULT,) * 3 and shows[0].stake == 2600, shows[0]

    assert not from_friend("stat fps"), "other ServerExec traffic must pass through"
    assert from_friend(protocol.Ping(7))
    assert replies_to_friend() == [protocol.Pong(7)]

    host_cash, friend_cash = game.cash(), game.friend.cash()
    rng.queue = [Symbol.LEGENDARY] * 3
    assert from_friend(protocol.Pull(1, "cash", 1))
    [reply] = replies_to_friend()
    assert isinstance(reply, protocol.Result) and reply.line == (Symbol.LEGENDARY,) * 3, reply
    assert reply.charged == reply.stake == 86, reply  # Zane is level 20
    assert game.friend.cash() == friend_cash - 86
    spawned_before = len(game.spawned)
    assert from_friend(protocol.Settle(1))
    assert game.friend.cash() == friend_cash - 86 + 5 * 86
    assert game.cash() == host_cash, "the host's own wallet is never touched"
    [(pool, level, (x, _, _))] = game.spawned[spawned_before:]
    assert pool.endswith("_05_legendary") and level == 20 and x > 1200, game.spawned[-1]

    # The host watches Zane's spin: reels above his head while Moze looks his way...
    [watched] = sdk_mod.spectator.views()
    assert (watched.player_id, watched.name, watched.machine) == (257, "Zane", "LOOT SLOTS"), watched
    game.pc.PlayerCameraManager.look_at(1300.0, 2000.0, 415.0)
    tick(0.1)
    assert "ZANE  -  LOOT SLOTS" in statuses(), statuses()[-6:]
    panel = sdk_mod.spectators._panels[0]
    assert panel.canvas.visibility == 3
    px, py = panel_position(panel)
    x_center = px + fake_game_width(sdk_mod) / 2
    assert abs(x_center - 960) < 30 and py < 540, (px, py)
    # ...and in the corner when he's behind him
    game.pc.PlayerCameraManager.look_at(1000.0, 3000.0, 370.0)
    tick(0.1)
    assert panel_position(panel) == (24, 140), panel_position(panel)
    tick(3.0)
    assert "LEGENDARY!  +$430, 1 legendary" in statuses(), statuses()[-6:]
    tick(5.0)
    assert not sdk_mod.spectator.active and sdk_mod.spectators._root().visibility == 1
    game.pc.PlayerCameraManager.view = (0.0, 90.0)

    game.friend.Pawn.location = (90_000.0, 0.0, 0.0)
    assert from_friend(protocol.Pull(2, "cash", 1))
    [reply] = replies_to_friend()
    assert isinstance(reply, protocol.Error) and "vending machine" in reply.text, reply
    game.friend.Pawn.location = (1300.0, 2000.0, 300.0)

    assert from_friend("BLGMB|1|pull|3|cash|1|0.0.1")
    [reply] = replies_to_friend()
    assert isinstance(reply, protocol.Error) and "Version mismatch" in reply.text, reply

    # If Zane's game never confirms the reels stopped, the host still pays out
    rng.queue = [Symbol.CASH] * 3
    friend_cash = game.friend.cash()
    assert from_friend(protocol.Pull(4, "cash", 1))
    replies_to_friend()
    tick_until_idle()
    assert game.friend.cash() == friend_cash - 86 + 20 * 86, game.friend.cash()

    # ---- Co-op, as a client: our pulls go to the host over ServerExec ----
    game.pc.authority = False
    game.pc.sent.clear()
    wallet = (game.cash(), game.pc.CurrencyManager.row("eridium").Amount)
    jackpots_before = sdk_mod.stats.jackpots

    def from_host(message: protocol.Message | str) -> bool:
        text = message if isinstance(message, str) else protocol.encode(message)
        args = WrappedStruct("ClientMessage", S=text, Type="None", MsgLifeTime=0.0)
        return hooks.fire(CLIENT_RPC, hooks.Type.PRE, game.pc, args)

    commands.run("gamble_spin")
    [(func, text)] = game.pc.sent
    pull = protocol.decode(text)
    assert func == "ServerExec" and isinstance(pull, protocol.Pull), (func, text)
    assert (pull.machine_key, pull.bet) == ("eridium", 2), pull
    assert sdk_mod.controller.is_waiting
    assert any("Pulling the lever" in text for text in statuses())

    assert from_host(protocol.Result(pull.request_id, "eridium", (Symbol.VAULT,) * 3, 2, 20, 20))
    assert sdk_mod.controller.is_spinning
    tick_until_idle()
    assert game.pc.sent[-1] == ("ServerExec", protocol.encode(protocol.Settle(pull.request_id)))
    assert sdk_mod.stats.jackpots == jackpots_before + 1
    assert any("JACKPOT!" in text and "6 legendary" in text for text in statuses()), statuses()
    assert (game.cash(), game.pc.CurrencyManager.row("eridium").Amount) == wallet, "the host pays, not us"

    assert not from_host("Welcome to Kairos!"), "other ClientMessages must pass through"

    # The host says Zane pulled: we watch him too. We're never shown our own pulls
    assert from_host(protocol.Show(257, "eridium", (Symbol.RARE,) * 3, 1, 15, "shotguns"))
    assert from_host(protocol.Show(256, "cash", (Symbol.SKULL,) * 3, 1, 2600))
    [watched] = sdk_mod.spectator.views()
    assert (watched.player_id, watched.machine) == (257, "ERIDIUM SLOTS"), watched
    tick(3.0)
    assert "Rare loot!  2 rare shotguns" in statuses(), statuses()[-6:]
    tick(5.0)

    commands.run("gamble_coop_test")
    ping = protocol.decode(game.pc.sent[-1][1])
    assert isinstance(ping, protocol.Ping), ping
    assert from_host(protocol.Pong(ping.nonce))
    assert any("the host answered" in line for line in logged("info")), logged("info")[-5:]
    assert any("all good" in line for line in logged("info"))

    commands.run("gamble_spin")
    tick_until_idle()
    assert any("No answer from the host" in text for text in statuses())
    game.pc.authority = True

    # A map change garbage collects the overlay - the next pull should rebuild it
    overlay_root = sdk_mod.overlay._root()
    game.collect_widgets()
    rng.queue = [Symbol.SKULL, Symbol.CASH, Symbol.EPIC]
    commands.run("gamble_spin")
    tick_until_idle()
    assert sdk_mod.overlay._root() not in (None, overlay_root), "the overlay should have been rebuilt"

    play_in_the_world(game, sdk_mod, commands, hooks, tick, tick_until_idle, statuses, logged, rng, tmp)

    # Disabling mid-spin still pays out, then tears everything down
    sdk_mod.show_machines_option.value = True
    tick(0.1)
    assert game.live_actors(), "slot machines should be back up"
    sdk_mod.machine_option.value = "Eridium Slots"
    rng.queue = [Symbol.RARE] * 3
    spawned_before = len(game.spawned)
    commands.run("gamble_menu")
    commands.run("gamble_spin")
    assert sdk_mod.controller.is_spinning
    menu_root = sdk_mod.menu._root()
    assert menu_root.in_viewport
    mod.disable()
    # Eridium Slots pay 2 rares for three RARE symbols, doubled by the 2x bet
    assert len(game.spawned) == spawned_before + 4, game.spawned[spawned_before:]
    assert not ticking()
    assert not hooks.has_hook(SERVER_RPC, hooks.Type.PRE, "borderlands_gamble.coop")
    assert not hooks.has_hook(CLIENT_RPC, hooks.Type.PRE, "borderlands_gamble.coop")
    assert not menu_root.in_viewport
    assert not game.live_actors(), "disabling should take every slot machine down"
    assert game.pc.bShowMouseCursor is False and game.input_mode == ("GameOnly", None)
    assert not commands.has_command("gamble_spin")

    # The fake SDK has no keybind system, which mods_base complains about. Anything else is a bug
    problems = [
        f"{level}: {line}"
        for level, line in logging.LINES
        if level != "info" and "keybind" not in line.lower()
    ]
    assert not problems, problems

    print(f"Session OK: {len(game.give_calls)} currency changes, {len(game.spawned)} items dropped")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
