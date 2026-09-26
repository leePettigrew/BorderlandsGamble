# Game APIs the mod relies on

Everything game specific lives in [`bl4.py`](../src/borderlands_gamble/bl4.py) (game state) and
[`overlay.py`](../src/borderlands_gamble/overlay.py) (drawing). Each API below comes from a
published BL4 SDK mod that already uses it. `gamble_diag` checks all of them. When a check fails
after a game patch, paste the matching snippet into the console (open it with `~`) to dig in.

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

```
py print([(m.Name, m.bHidden) for m in unrealsdk.find_all("OakVendingMachine", False)])
```

## Drawing and animating

| What | API | Proven by |
|---|---|---|
| Overlay | `construct_object("/Script/UMG.UserWidget", pc)`, then a `WidgetTree` with a `CanvasPanel` root holding `Border`/`TextBlock` children. `AddToViewport`, `SetVisibility(3)` (hit-test invisible, so no input is stolen) | MSBT `quick_menu.py` toast overlay |
| Screen size | `WidgetLayoutLibrary.GetViewportSize(pc)` / `GetViewportScale(pc)` | MSBT `quick_menu.py` |
| Per-frame tick | Post-hook on `/Script/Engine.CameraModifier:BlueprintModifyCamera`. It fires several times a frame, so the mod throttles to 60 Hz and only hooks it while the overlay is up | MSBT `camera_tick.py` |

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

The lever defaults to **F8**, which the other mods we checked don't use (MSBT uses F7, F10, and F11).
Rebind it in the mods menu if it clashes with anything. If keybinds ever stop working after a patch,
the `gamble_spin` console command does the same thing.
