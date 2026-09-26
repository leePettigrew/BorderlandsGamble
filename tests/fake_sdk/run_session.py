"""
Plays a session of the mod inside the fake game, using the real mods_base.

Builds the `.sdkmod`, drops it into a scratch `sdk_mods` folder next to a copy of mods_base, and
imports it the same way the mod manager does. Then enables the mod, pulls the lever, drives the
frame tick, and checks what happened to the (fake) wallet, loot, overlay and settings.

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
    assert mod.version == "0.1.0", mod.version
    assert not mod.enabling_locked, "mod should be allowed to enable in BL4"
    assert ".sdkmod" in str(sdk_mod.__file__), f"should import from the .sdkmod, not {sdk_mod.__file__}"

    def tick_until_idle(limit: float = 30.0) -> int:
        frames = 0
        end = clock.now + limit
        while hooks.has_hook(TICK_FUNC, hooks.Type.POST, sdk_mod.frame_tick.hook_identifier):
            clock.now += 1 / 30
            hooks.fire(TICK_FUNC)
            frames += 1
            assert clock.now < end, "frame tick never switched itself off"
        return frames

    def statuses() -> list[str]:
        return [text for block in game.text_blocks() for text in block.texts]

    def logged(level: str | None = None) -> list[str]:
        return [line for lvl, line in logging.LINES if level is None or lvl == level]

    mod.enable()
    assert mod.is_enabled
    for cmd in ("gamble_spin", "gamble_odds", "gamble_stats", "gamble_diag"):
        assert commands.has_command(cmd), cmd

    # Diagnostics should find everything in the fake game
    commands.run("gamble_diag --wallet")
    assert any("All good!" in line for line in logged("info")), logged()
    assert game.cash() == 1_000_000, "wallet test should give the dollar back"

    commands.run("gamble_odds --machine eridium --luck Generous")
    assert any("Eridium Slots (Generous luck)" in line for line in logged("info"))

    # A rigged jackpot on Loot Slots
    rng = RiggedRandom()
    sdk_mod.controller.rng = rng
    from borderlands_gamble.slots import Symbol

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

    # Eridium Slots via the keybind, with a 2x bet, pulled twice to skip the animation
    sdk_mod.switch_machine_keybind.callback()
    sdk_mod.change_bet_keybind.callback()
    assert sdk_mod.current_settings().machine_key == "eridium"
    assert sdk_mod.current_settings().bet == 2
    rng.queue = [Symbol.ERIDIUM] * 3
    eridium_before = game.pc.CurrencyManager.row("eridium").Amount
    sdk_mod.pull_lever_keybind.callback()
    sdk_mod.pull_lever_keybind.callback()
    assert not sdk_mod.controller.is_spinning
    assert game.pc.CurrencyManager.row("eridium").Amount == eridium_before - 20 + 15 * 20
    tick_until_idle()

    # Too far from a machine
    game.pc.Pawn.location = (50_000.0, 0.0, 0.0)
    calls_before = len(game.give_calls)
    commands.run("gamble_spin")
    assert len(game.give_calls) == calls_before
    assert any("Find a vending machine" in text for text in statuses())
    tick_until_idle()
    game.pc.Pawn.location = (1000.0, 2000.0, 300.0)

    # Co-op clients can't play
    game.pc.authority = False
    commands.run("gamble_spin")
    assert len(game.give_calls) == calls_before
    assert any("host-only" in text for text in statuses())
    tick_until_idle()
    game.pc.authority = True

    # A map change garbage collects the overlay - the next pull should rebuild it
    game.collect_widgets()
    rng.queue = [Symbol.SKULL, Symbol.CASH, Symbol.EPIC]
    commands.run("gamble_spin")
    tick_until_idle()
    user_widgets = [w for w in game.widgets if isinstance(w, fake_game.UserWidget)]
    assert len(user_widgets) == 2, len(user_widgets)

    # Disabling mid-spin still pays out, then tears everything down
    rng.queue = [Symbol.RARE] * 3
    spawned_before = len(game.spawned)
    commands.run("gamble_spin")
    assert sdk_mod.controller.is_spinning
    mod.disable()
    # Eridium Slots pay 2 rares for three RARE symbols, doubled by the 2x bet
    assert len(game.spawned) == spawned_before + 4, game.spawned[spawned_before:]
    assert not hooks.has_hook(TICK_FUNC, hooks.Type.POST, sdk_mod.frame_tick.hook_identifier)
    assert not user_widgets[-1].in_viewport
    assert not commands.has_command("gamble_spin")

    errors = [line for line in logged("error") if "keybind" not in line.lower()]
    assert not errors, errors

    print(f"Session OK: {len(game.give_calls)} currency changes, {len(game.spawned)} items dropped")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
