# Can we add gambling to Borderlands 4?

*Researched September 2026, against BL4 v1.10 (the September 10, 2026 update).*

**Short answer:** yes. Slot machines that take your cash or eridium and pay out currency and real loot
are very doable as a **PythonSDK mod**. Other published BL4 mods already use every piece of game
access this needs. What's *hard* is a brand new physical slot machine cabinet with its own 3D model,
sounds, and lever animation, because the game has no such assets and making them means a full Unreal
Engine asset pipeline.

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

That covers everything a slot machine needs. [game-api.md](game-api.md) has the details.

## What's hard, and why

- **A custom cabinet.** A real slot machine model, lever, spinning reel meshes, and jingles all need new
  assets. That means authoring them in a version-matched Unreal Editor, cooking them, and packaging
  them as a pak mod. No Python can do that. *Workaround:* reuse existing machines (every vending
  machine becomes a slot machine) and draw the reels as a UI overlay. That's what this mod does.
- **Taking over a machine's "use" prompt.** It's possible in principle, since EncoreTweaks hooks the
  Big Encore Machine. But hijacking vending machines without breaking normal shopping needs in-game
  research into their script events. For now, a keybind works anywhere near a vending machine.
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

1. **MVP (this repo).** Lever keybind at any vending machine, animated overlay reels, cash and eridium
   machines, loot payouts from the game's pools, bets, luck presets, lifetime stats, exact odds,
   diagnostics, and co-op with the host as the bank. The first in-game test is next.
2. **Diegetic machines.** Hook vending-machine interaction (e.g. hold-to-gamble), or spawn dedicated
   "slot machine" actors in hub areas using the proven Spawner approach, and reuse existing game
   sounds.
3. **More games.** Double-or-nothing on items, roulette, blackjack against Moxxi, and daily jackpot
   events.
4. **Custom assets.** A pak mod with a real cabinet model. This is a separate, much larger project.
