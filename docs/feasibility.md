# Can we add gambling to Borderlands 4?

*Researched September 2026, against BL4 v1.10 (the September 10, 2026 update).*

**Short answer:** yes. Slot machines that take your cash or eridium and pay out currency and real loot
are very doable as a **PythonSDK mod**. Other published BL4 mods already use every piece of game
access this needs. What's *hard* is a brand new physical slot machine cabinet with its own 3D model,
sounds, and lever animation, because the game has no such assets and making them means a full Unreal
Engine asset pipeline. So the mod's slot machines are look-alikes of the game's vending machines.

## What Borderlands 4 ships with

Earlier games had Moxxi's slot machines. BL4 dropped them, and players noticed
([TheGamer](https://www.thegamer.com/borderlands-bl-borderlands-4-bl4-gambling-slot-machines-removed-player-feedback/),
[Steam discussion](https://steamcommunity.com/app/1285190/discussions/0/673972930560030337/)). The
closest things left are the vending machines, Maurice's Black Market, and Moxxi's Big Encore Machine
(eridium-priced boss rematches).

This means there's **nothing to re-enable**: no slot machine models, animations, or sounds exist in the
game files. Any slot machine we add has to be built from parts the game does have.

## The modding toolbox

| Tool | State in Sept 2026 | Useful for gambling? |
|---|---|---|
| **PythonSDK** ([oak2-mod-manager](https://github.com/bl-sdk/oak2-mod-manager)) | v0.1 March 2026, v0.3 June 2026, 30+ mods in the [mod DB](https://bl-sdk.github.io/oak2-mod-db/) | **Yes.** Game logic, hooks, keybinds, console commands, and runtime UI. This mod is built on it. |
| UE4SS | [Not supported](https://github.com/UE4SS-RE/RE-UE4SS/issues/1022) (open since Sept 2025) | No |
| Pak mods | Work alongside the SDK, per the [SDK FAQ](https://bl-sdk.github.io/oak2-mod-db/faq/) | Only for custom assets, later |
| Save editors | Mature | No. They edit items, not gameplay. |

The SDK runs Python 3.14 inside the game. It can find any object, call any reflected function, read and
write properties, and hook functions. mods_base adds options, keybinds, and console commands on top.

### What other BL4 SDK mods already prove works

| Need | How | Already done by |
|---|---|---|
| Read the wallet | `pc.CurrencyManager.currencies` rows (`type`, `Amount`) | [Matt's SDK Boosting Tools](https://github.com/funkyoushift/MattsSDKBoostingTools) `player_readback.py`, [TrashSeller](https://github.com/FreepDryer/freepdryer-bl4-sdk-mods) |
| Take/give cash and eridium | `GbxCurrencyFunctionLibrary.GiveCurrency(pc, FGbxDefPtr(token, GbxCurrencyDef), amount)` | Matt's `givecurrency` command (which also accepts negative amounts) |
| Know the player's level | `PlayerState.BP_GetExperienceLevel(FGbxDefPtr("Character", GbxExperienceDef))` | Matt's `player_readback.py` |
| Drop real loot | `NexusConfigStoreItemPool.SpawnInventoryFromItemPool(world, transform, level, pool)` | Matt's shiny and item-pool spawners |
| Find vending machines | `find_all("OakVendingMachine")` | [GroundLootHelpers](https://github.com/RedxYeti/yeti-bl4-sdk), TrashSeller |
| Draw a UI | UMG widgets built at runtime (`UserWidget` + `CanvasPanel` + `TextBlock`) | Matt's Quick Menu, [Matt's BL4 Mods Menu](https://github.com/mattmab/MattsBL4ModsMenu) |
| Animate | Hooking `CameraModifier:BlueprintModifyCamera`, which runs every frame | Matt's shared camera tick |
| React to a machine being used | Hooking a machine's `…UsableActorState_K2_OnUsed` script event | [EncoreTweaks](https://github.com/RedxYeti/yeti-bl4-sdk) (the Big Encore Machine) |
| Put a real machine in the world | A fresh `Spawner` given an interactive object def, e.g. `IO_VendingMachine_BlackMarket` | Matt's "Spawn Black Market" |
| Put up a copy of an actor's model | `GameplayStatics.BeginDeferredActorSpawnFromClass` a plain mesh actor, then give it the original's meshes and materials | Matt's [ActorScriptDeployer](https://github.com/funkyoushift/MattsSDKBoostingTools) (MIT) |
| Clickable menus | Real UMG `Button`s, clicks read by polling `IsPressed`, with the mouse cursor on and movement off | [Matt's BL4 Mods Menu](https://github.com/mattmab/MattsBL4ModsMenu) |
| Take over a key | The SDK's keybinds hook the game's own input handling, and can keep a key press from the game | The SDK itself (`UGbxEnhancedPlayerInput::InputKey`) |

That covers everything a slot machine needs. [game-api.md](game-api.md) has the details.

## What's hard, and why

- **A custom cabinet model.** A real slot machine model, lever, spinning reels, and jingles all need
  new assets. Python can only use assets that are already in the game, so new ones mean modelling
  them, importing them into an Unreal Editor matching the exact engine version BL4 was built with,
  cooking them for BL4, and packaging them as a pak mod. Gearbox ships no editor or mod kit for BL4,
  cooked assets that don't match the game's build crash it or fail to load, and every co-op player
  would need the same pak. None of that can be done or tested from Python.
  *What the mod does instead:* at the end of each row of vending machines it puts up a look-alike:
  new, plain actors given the vending machine's own meshes and materials, with a floating **SLOTS**
  sign (the engine's built-in 3D text). The reels, lever, and paytable live in a menu drawn with the
  game's UI system.
- **The game's own "use" prompt (E).** The game only offers "use" on its interactive objects, which
  carry Gearbox's use logic and data. A look-alike is just meshes, so the game ignores it.
  *What the mod does instead:* it handles E itself. When you aim at a slot machine, it shows its own
  "[E] PLAY LOOT SLOTS" prompt, opens the menu on E, and keeps that press from the game. Aim anywhere
  else and E works as normal.
  *Cleaner, later:* spawn a real interactive object (Matt's "Spawn Black Market" shows spawning real
  vending machines works) and turn its "used" event into opening the menu, the way EncoreTweaks hooks
  the Big Encore Machine's `…UsableActorState_K2_OnUsed`. That would give the game's own prompt, with
  controller button icons and the interaction highlight. It needs the exact event names for vending
  machines, which only show up in game: `gamble_trace` records them in one test (see
  [game-api.md](game-api.md#research-the-games-own-use-prompt)).
- **Game patches.** Updates can break the SDK or move game APIs. Users hit SDK breakage after
  mid-September 2026 patches ([#16](https://github.com/bl-sdk/oak2-mod-manager/issues/16),
  [#17](https://github.com/bl-sdk/oak2-mod-manager/issues/17)), though other mods were being
  tested live again by September 23. This mod has a `gamble_diag` command to pinpoint which API broke.
- **Co-op.** Only the host's game can grant currency and spawn loot. So the host's game acts as the
  bank for everyone, and clients send their pulls to it over the network (see [coop.md](coop.md)).
  Every player needs the mod installed, at the same version.
- **Online.** The [SDK FAQ](https://bl-sdk.github.io/oak2-mod-db/faq/) says the SDK itself won't get
  you banned, but 2K can act on griefing. Keep modded play out of matchmaking.
- **Real money.** Out of scope, and it should stay that way. The mod only uses in-game currency, with
  exact odds shown, and there's nothing to buy.

## Roadmap

1. **MVP (done).** Lever keybind at any vending machine, animated reels, cash and eridium machines,
   loot payouts from the game's pools, bets, luck presets, lifetime stats, exact odds, diagnostics,
   and co-op with the host as the bank.
2. **Machines in the world (this version).** Look-alike slot machines at every row of vending
   machines, the mod's own "[E] Play" prompt, a clickable menu, picking what loot drops, and co-op
   partners seeing each other's spins. The first in-game test is next.
3. **The game's own prompt.** With the event names from `gamble_trace`, make the slot machines real
   interactive objects, and reuse existing game sounds.
4. **More games.** Double-or-nothing on items, roulette, blackjack against Moxxi, and daily jackpot
   events.
5. **Custom assets.** A pak mod with a real cabinet model. This is a separate, much larger project.
