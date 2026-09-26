# Game APIs the mod relies on

Everything game specific lives in [`bl4.py`](../src/borderlands_gamble/bl4.py) (game state),
[`world.py`](../src/borderlands_gamble/world.py) (slot machines in the world),
[`overlay.py`](../src/borderlands_gamble/overlay.py) (HUD) and
[`menu.py`](../src/borderlands_gamble/menu.py) (the menu). Each API below comes from a published BL4
SDK mod that already uses it, or is a standard Unreal Engine function. `gamble_diag` checks the core
ones. When a check fails after a game patch, paste the matching snippet into the console (open it
with `~`) to dig in.

The snippets assume you've run this first:

```
py import unrealsdk
py from mods_base import get_pc, ENGINE
py pc = get_pc()
```

## Wallet

| What | API | Proven by |
|---|---|---|
| Balances | `pc.CurrencyManager.currencies`: rows with `.type` (an `FGbxDefPtr`) and `.Amount` | [MSBT](https://github.com/funkyoushift/MattsSDKBoostingTools) `player_readback.py`, [TrashSeller](https://github.com/FreepDryer/freepdryer-bl4-sdk-mods) |
| Currency tokens | `Cash`, `eridium`, `VaultCard01_Tokens` … `VaultCard05_Tokens` | MSBT `player_economy.py` |
| Grant / charge | `GbxCurrencyFunctionLibrary` CDO `.GiveCurrency(pc, FGbxDefPtr(token, <GbxCurrencyDef struct>), amount)` | MSBT `player_economy.py` |

```
py print([(r.type._name, r.Amount) for r in pc.CurrencyManager.currencies])
py lib = unrealsdk.find_class("GbxCurrencyFunctionLibrary").ClassDefaultObject
py from unrealsdk.unreal import FGbxDefPtr
py cash = FGbxDefPtr("Cash", unrealsdk.find_object("ScriptStruct", "/Script/GbxGame.GbxCurrencyDef"))
py lib.GiveCurrency(pc, cash, 100)
```

**Assumption to verify:** charging a pull passes a *negative* amount to `GiveCurrency`. MSBT's
`givecurrency` command accepts negative amounts, but no mod we know of relies on them. The mod checks
the balance after every charge, and voids the spin if the money didn't actually leave the wallet. So
the worst case is "can't charge", never a free win or a lost stake. `gamble_diag --wallet` tests it
directly by taking $1 and giving it back. If it fails, **Free Play** still works.

## Player

| What | API | Proven by |
|---|---|---|
| Player controller | `mods_base.get_pc()` | mods_base |
| Host check | `pc.HasAuthority()` | MSBT `party_helpers.py` |
| Level | `pc.PlayerState.BP_GetExperienceLevel(FGbxDefPtr("Character", <GbxExperienceDef struct>))` | MSBT `player_readback.py` |
| Position / facing | `pc.Pawn.K2_GetActorLocation()`, `.K2_GetActorRotation()`, `.K2_GetActorTransform()` | Unreal built-ins, used throughout MSBT |

```
py from unrealsdk.unreal import FGbxDefPtr
py xp = FGbxDefPtr("Character", unrealsdk.find_object("ScriptStruct", "/Script/GbxGame.GbxExperienceDef"))
py print(pc.PlayerState.BP_GetExperienceLevel(xp), pc.HasAuthority())
```

## Loot

| What | API | Proven by |
|---|---|---|
| Spawn an item | `NexusConfigStoreItemPool.SpawnInventoryFromItemPool(world, transform, level, pool_name)` | MSBT `shinies.py`, `item_pool_spawning.py` |
| World | `ENGINE.GameViewport.World` | MSBT, [EncoreTweaks](https://github.com/RedxYeti/yeti-bl4-sdk) |
| Pool names | e.g. `itempool_guns_03_rare`, `itempool_sr_05_legendary`, from the game's own data (v1.10 / build 25234898) | MSBT's `item_pools.json` catalog |

The prize pools per tier are in [`loot.py`](../src/borderlands_gamble/loot.py). There's no combined
legendary gun pool, so legendaries roll a weapon type first.

```
py store = list(unrealsdk.find_all("NexusConfigStoreItemPool", False))[-1]
py t = pc.Pawn.K2_GetActorTransform()
py store.SpawnInventoryFromItemPool(ENGINE.GameViewport.World, t, 50, "itempool_guns_03_rare")
```

## Vending machines

| What | API | Proven by |
|---|---|---|
| Find machines | `unrealsdk.find_all("OakVendingMachine", False)`, skipping `Default__` templates and hidden (`bHidden`) helper machines | [GroundLootHelpers](https://github.com/RedxYeti/yeti-bl4-sdk), TrashSeller |
| Where, and how big | `K2_GetActorLocation()`, `K2_GetActorRotation()`, and `GetActorBounds(False, IGNORE_STRUCT, IGNORE_STRUCT, False)`, which returns `(…, origin, extent)` | Unreal built-ins |

```
py print([(m.Name, m.bHidden) for m in unrealsdk.find_all("OakVendingMachine", False)])
```

"Only at machines" counts both vending machines and the mod's own slot machines.

## Slot machines in the world

Each slot machine is a look-alike of the vending machine at the end of a row: new, plain actors given
the same meshes and materials. [`cabinets.py`](../src/borderlands_gamble/cabinets.py) works out where
they go, and `world.py` builds them. The spawning pattern is the one Matt's ActorScriptDeployer (ASD,
MIT, in [MSBT](https://github.com/funkyoushift/MattsSDKBoostingTools)'s `tools/third_party`) uses
for its visual copies of game actors.

| What | API | Proven by |
|---|---|---|
| A machine's parts | `K2_GetComponentsByClass(<class>)` for `/Script/Engine.StaticMeshComponent` and `/Script/Engine.SkeletalMeshComponent`. For each: `GetStaticMesh()` or `GetSkeletalMeshAsset()`, `K2_GetComponentLocation/Rotation/Scale()`, `GetNumMaterials()`, `GetMaterial(i)` | ASD `_actor_mesh`, Unreal built-ins |
| Spawn a plain actor | `GameplayStatics.BeginDeferredActorSpawnFromClass(world, cls, transform, 1, None, 1)`, then `FinishSpawningActor(actor, transform, 1)`. `SetReplicates(False)` in between, since every player's game builds its own copy | ASD `_spawn_actor_deferred` |
| Give it a mesh | `StaticMeshActor`: `StaticMeshComponent.SetMobility(2)` (movable), then `SetStaticMesh(mesh)`. `SkeletalMeshActor`: `SkeletalMeshComponent.SetSkeletalMeshAsset(mesh)`. Then `SetMaterial(i, material)` for each slot | ASD `_spawn_generic_skeletal_duplicate` |
| The SLOTS sign | `/Script/Engine.TextRenderActor`, its `TextRender` component's `K2_SetText`, `SetTextRenderColor`, `SetWorldSize` | Unreal built-in |
| Take one down | `K2_DestroyActor()` | ASD |
| Which map | `GameplayStatics.GetCurrentLevelName(world, True)`, to store hand placed machines against | Unreal built-in |
| What you're aiming at | `pc.PlayerCameraManager.GetCameraLocation()` and `GetCameraRotation()`, tested against each machine's rough shape (an upright cylinder) | Unreal built-ins |

```
py m = [m for m in unrealsdk.find_all("OakVendingMachine", False) if not str(m.Name).startswith("Default__")][0]
py from unrealsdk.unreal import IGNORE_STRUCT
py print(m.GetActorBounds(False, IGNORE_STRUCT, IGNORE_STRUCT, False))
py print(list(m.K2_GetComponentsByClass(unrealsdk.find_object("Class", "/Script/Engine.StaticMeshComponent"))))
```

**To check in game:**
- That vending machines are drawn with ordinary static or skeletal mesh components. If not, the copy
  comes out empty, and the log says `Found no meshes to copy`.
- That the engine's default 3D text material is in the game's files. If not, the sign just doesn't
  show. Turn off **Slot Machine Signs** if it looks wrong.
- Which way the copies face. They copy each part's own rotation, so they should match the vending
  machine next to them.

## Drawing and animating

| What | API | Proven by |
|---|---|---|
| Overlay | `construct_object("/Script/UMG.UserWidget", pc)`, then a `WidgetTree` with a `CanvasPanel` root holding `Border`/`TextBlock` children. `AddToViewport`, `SetVisibility(3)` (hit-test invisible, so no input is stolen) | MSBT `quick_menu.py` toast overlay |
| Screen size | `WidgetLayoutLibrary.GetViewportSize(pc)` / `GetViewportScale(pc)` | MSBT `quick_menu.py` |
| Per-frame tick | Post-hook on `/Script/Engine.CameraModifier:BlueprintModifyCamera`. It fires several times a frame, so the mod throttles to 60 Hz. It runs while slot machines are shown in the world (they're checked every 5 s, and what you aim at 10 times a second), and otherwise only while something is on screen | MSBT `camera_tick.py` |

## The menu

The menu is built like the overlay, then made clickable the way
[Matt's BL4 Mods Menu](https://github.com/mattmab/MattsBL4ModsMenu) does it.

| What | API | Proven by |
|---|---|---|
| Buttons | `/Script/UMG.Button` at 3% opacity over our own `Border`, with the label on top. Clicks are read by polling `IsPressed()`: pressed, then released, is a click. `IsHovered()` for highlighting | Matt's BL4 Mods Menu |
| Fallback clicks | `pc.GetMousePosition(0.0, 0.0)` and `pc.IsInputKeyDown(<LeftMouseButton>)`, against each button's rectangle in screen pixels | Matt's BL4 Mods Menu |
| Keys | `pc.IsInputKeyDown(unrealsdk.make_struct("Key", KeyName="SpaceBar"))`, polled every frame, since keybinds can't be relied on while a menu has focus. Esc acts when released, so the pause menu doesn't also catch it | Matt's BL4 Mods Menu |
| Cursor and focus | `pc.bShowMouseCursor = True`, `WidgetBlueprintLibrary.SetInputMode_GameAndUIEx(pc, button, 0, False, False)`. `SetInputMode_GameOnly(pc, True)` undoes it | Matt's BL4 Mods Menu |
| Stop the player moving | `pc.SetIgnoreMoveInput(True)`, `SetIgnoreLookInput(True)`, `pc.bBlockInput = True`. `ResetIgnoreMoveInput()` and `ResetIgnoreLookInput()` undo it | Matt's BL4 Mods Menu |
| Hide the HUD | The console command `gbx.ui.view.stateadd CINEMATIC` (`stateremove` to undo), run with `KismetSystemLibrary.ExecuteConsoleCommand(pc, command, pc)` | Matt's BL4 Mods Menu |

If the menu ever gets stuck open, `gamble_menu` in the console closes it and gives back control.

## Co-op

| What | API | Proven by |
|---|---|---|
| Client → host message | `pc.ServerExec(text)`: a reliable server RPC on every Unreal `PlayerController`. Its normal job is a dev console, which shipping builds skip | Part of Unreal Engine. BL4 mods call it too (world travel, MSBT `travel.py`) |
| Host → client message | `pc.ClientMessage(text, "None", 0.0)`: a reliable client RPC on every `PlayerController` | Part of Unreal Engine |
| Receiving | Pre-hooks on `/Script/Engine.PlayerController:ServerExec` and `:ClientMessage`. unrealsdk hooks `ProcessEvent`, which is how incoming RPCs get dispatched. Our messages start with `BLGMB\|` and are blocked after handling; anything else passes through untouched | unrealsdk's BL4 `ProcessEvent` hook |
| Acting for a partner | On the host, the hook's `obj` is the sending player's controller, and wallet, level, and loot calls take it directly | MSBT's host-side `givecurrency` for other players |

Outgoing calls are wrapped in `unrealsdk.hooks.prevent_hooking_direct_calls()`, so our own hooks
don't see them. `gamble_coop_test` checks the whole round trip in a real session. See
[coop.md](coop.md).

## Keys

The menu opens with **F8** next to any machine (MSBT uses F7, F10, and F11, and no other mod we
checked uses F8), and with **E** while you aim at one of the mod's slot machines. The SDK's keybinds
hook the game's own input handling (`UGbxEnhancedPlayerInput::InputKey`), and a keybind can return
`Block` to keep a key press from the game. The mod only does that for E while you're aiming at a
slot machine (which the game has nothing to use on anyway) or have its menu open. Anywhere else, E
goes straight to the game. In the menu, both keys are also polled directly, like the menu's other
keys, in case keybinds don't fire while it has focus. Rebind either key in the mods menu if it clashes with anything. If keybinds ever stop working
after a patch, the `gamble_menu` and `gamble_spin` console commands do the same things.

## Research: the game's own use prompt

The mod shows its own "[E] Play" prompt because its slot machines aren't the game's interactive
objects. To use the game's prompt instead, the next step is spawning a real interactive object and
turning its "used" event into opening the menu. [EncoreTweaks](https://github.com/RedxYeti/yeti-bl4-sdk)
does this for the Big Encore Machine by hooking
`/Game/InteractiveObjects/GameSystemMachines/BossReplay/Script_BossReplay.Script_BossReplay_C:GbxActorScriptEvt__UsableActorState_K2_OnUsed`.
The equivalent names for vending machines only show up in game:

1. Stand next to a vending machine and run `gamble_trace`.
2. Within 6 seconds, use the vending machine (open it, then close it).
3. The mod turns on the SDK's call logger (`unrealsdk.hooks.log_all_calls`) for those seconds. It
   writes every function call to `OakGame/Binaries/Win64/Plugins/unrealsdk.calls.tsv`, then prints
   the ones that look use-related (names containing Usable, Interact, Vending, Shop, and so on).
4. Paste what it prints into an issue.

The game may stutter briefly while it records.
