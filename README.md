# Borderlands Gamble

Slot machines for Borderlands 4, as a [PythonSDK](https://github.com/bl-sdk/oak2-mod-manager) mod.

Borderlands 4 shipped without the slot machines from earlier games. This mod adds them back. Walk into
a safehouse and a slot machine stands at the end of the row of vending machines, with a **SLOTS** sign
above it. Aim at it and press **E**, and the slot machine menu opens: pick a machine and a bet, check
the paytable, and pull the lever. Wins pay out in cash or eridium, or in real loot that drops at your
feet, straight from the game's own item pools.

> **Status: prototype, not yet run in the real game.** The slot machine engine is fully unit tested.
> The game side is built only from APIs that other published BL4 SDK mods already use, or that every
> Unreal game has. Full solo and co-op sessions, including walking up to a slot machine and playing
> through the menu, have been run against a simulated game. It still needs a first in-game test. If
> anything is off, run `gamble_diag` (see [Troubleshooting](#troubleshooting)).

Wondering how much of this is possible, and what's hard? See [docs/feasibility.md](docs/feasibility.md).

## Features

- **Slot machines in safehouses.** Every row of vending machines (safehouses, settlements, the hub)
  gets a slot machine at the end. BL4 has no slot machine model, so it's a look-alike of the vending
  machine next to it, with a sign. Put more wherever you like, or move one that landed somewhere
  awkward, with `gamble_machine`.
- **A proper menu.** Reels, the paytable with prices at your bet, your wallet, and your lifetime
  stats on one screen. Click the buttons, or use the keyboard or a controller.
- **Two machines.** *Loot Slots* cost cash and get pricier as you level ($10 at level 1, $2.6k at 50,
  $25k at 70). *Eridium Slots* cost 10 eridium and roll rarer loot more often.
- **Real loot.** Loot prizes drop at your level from the game's rarity pools: rare, epic, and
  legendary guns, shields, grenades, repkits, class mods, and enhancements.
- **Animated reels.** The reels spin and stop left to right, drawn with the game's own UI system, so
  no custom assets are needed. Pull again to skip the spin.
- **Bets and luck.** Choose a 1x/2x/5x/10x bet and a luck preset from *Stingy* to *Moxxi Likes You*.
- **Transparent odds.** Exact odds, not simulations: `gamble_odds` prints the full paytable.
- **Lifetime stats.** Tracks spins, net winnings, jackpots, and items won.
- **Co-op.** When both players have the mod, the host's game acts as the bank for everyone, and each
  player gets their own machine and menu. See [docs/coop.md](docs/coop.md).

Why a look-alike rather than a real slot machine model, and why the mod shows its own "[E] Play"
prompt instead of the game's? Both come down to what the game ships with. See
[docs/feasibility.md](docs/feasibility.md#whats-hard-and-why).

### Odds (Fair luck, 1x bet)

| | Loot Slots | Eridium Slots |
|---|---|---|
| Any win | 41% of pulls | 38% of pulls |
| Currency returned | 74% of what you put in | 70% of what you put in |
| Rare item | 1 in 20 | 1 in 17 |
| Epic item | 1 in 117 | 1 in 64 |
| Legendary item | 1 in 707 | 1 in 493 |
| Jackpot (3x VAULT) | 1 in 2,915: 50x stake + 2 legendaries | 1 in 2,915: 40x stake + 3 legendaries |

The house always keeps an edge on currency; you gamble for the loot. All of this is tunable in
[`machines.py`](src/borderlands_gamble/machines.py). Run `python tools/odds.py` to see what a change does.

## Installing

1. Install the BL4 PythonSDK by following the
   [official instructions](https://bl-sdk.github.io/oak2-mod-db/). You should be able to open the
   console with `~` and type `mods`.
2. Build the mod with `python tools/build_sdkmod.py`, then drop `dist/borderlands_gamble.sdkmod`
   into your game's `sdk_mods` folder.
3. Restart the game, open `mods` in the console, and enable **Borderlands Gamble**.

Works solo, and in co-op when **everyone installs the same version** (see
[docs/coop.md](docs/coop.md)). Keep modded play out of matchmaking.

## Playing

Walk up to a slot machine, aim at it, and press **E**. The keys are rebindable in the mods menu.

| Input | Does |
|---|---|
| **E**, aiming at a slot machine | Open the slot machine menu. Anywhere else, E does its normal thing. |
| **F8** | Open the menu next to any slot machine or vending machine. In the menu, pull the lever. |
| *Quick Pull* (unbound) | Pull without the menu, with the reels on your HUD instead. |

In the menu:

| Mouse | Keyboard | Controller | Does |
|---|---|---|---|
| **PULL THE LEVER** | Space, Enter, F8 | A | Pull. Pull again mid-spin to skip to the result. |
| **BET** | Up / Down | Y, D-pad | Change the bet. |
| **PLAY ... SLOTS** | Left / Right | X, D-pad | Switch machine. |
| **LEAVE** | Esc, E | B | Close the menu. A spin still going carries on on your HUD. |

Console commands:

| Command | Does |
|---|---|
| `gamble_menu` | Open or close the menu. Works even if keybinds don't. |
| `gamble_spin` | Pull the lever, with the reels on your HUD. |
| `gamble_machine [list\|add\|remove\|reset\|refresh]` | Manage the slot machines on this map: `add` puts one in front of you, `remove` takes away the nearest (an automatic one moves to its next spot), `reset` undoes your changes, `refresh` rebuilds them. |
| `gamble_odds [--machine cash\|eridium] [--luck NAME] [--level N]` | Print the paytable and exact odds. |
| `gamble_stats [--reset]` | Print or reset your lifetime stats. |
| `gamble_diag [--wallet]` | Check every game API the mod uses. `--wallet` also test-charges $1 and refunds it. |
| `gamble_coop_test` | As a co-op client, check the host's mod can hear you. |
| `gamble_trace [--seconds N]` | Research: records every game function call for a few seconds (see [docs/game-api.md](docs/game-api.md#research-the-games-own-use-prompt)). |

Options (in the mods menu):
- **Your Machine:** machine, bet, spin time, result time, menu scale, and the HUD reels' scale and
  position.
- **Slot Machines:** whether to put slot machines in safehouses, and whether they get a sign.
- **House Rules:** luck, price multiplier, whether you have to be at a machine, free play, and loot
  level. In co-op, the host's house rules apply to everyone.

## Troubleshooting

Game patches sometimes move things the mod relies on. Run `gamble_diag --wallet` in the console. Every
line should say `[OK]`. [docs/game-api.md](docs/game-api.md) lists what each check covers and gives
console snippets for investigating a failure. If charging breaks but everything else works, turn on
**Free Play** to keep playing.

- **A slot machine is stuck in a wall**, or blocks a door: stand next to it and run
  `gamble_machine remove`. It moves to the other end of the row.
- **No slot machines appear:** `gamble_diag` reports how many are up. `gamble_machine refresh`
  rebuilds them, and prints why if one can't be built. F8 next to a vending machine still works.
- **The menu won't close:** press Esc or E, click LEAVE, or run `gamble_menu` in the console.

## Development

```
src/borderlands_gamble/   the mod (this folder is what goes in the .sdkmod)
  slots.py      pure engine: symbols, reels, paytables, spins, exact odds
  machines.py   the two machines' reels, paytables and prices
  loot.py       which item pools each prize tier drops from, and where drops land
  animation.py  reel animation timeline
  casino.py     Casino (the bank) and SlotController (a player's machine)
  protocol.py   co-op messages
  coop.py       how host and client handle co-op messages
  stats.py      lifetime stats
  cabinets.py   where slot machines stand, and which one you're aiming at
  menu_model.py what the menu shows, and what its buttons and keys do
  bl4.py        the game side: wallets, levels, loot drops, vending machines, co-op transport
  world.py      the game side of slot machines in the world: copying vending machines
  overlay.py    the HUD reels and the "[E] Play" prompt, built from UMG widgets at runtime
  menu.py       the slot machine menu, built from UMG widgets at runtime
  sdk_mod.py    options, keybinds, console commands, frame tick
tests/          unit tests, plus a fake game for full solo and co-op sessions
tools/          build_sdkmod.py, odds.py
docs/           feasibility study, co-op design, game API notes
```

Everything except `bl4.py`, `world.py`, `overlay.py`, `menu.py` and `sdk_mod.py` is plain Python
with no game dependency.
Working on it with someone? [CONTRIBUTING.md](CONTRIBUTING.md) covers setup, the dev loop, and the
rules that keep co-op working.

- **Tests:** `python -m unittest` from the repo root (Python 3.11+). To also run a full session
  through the real [mods_base](https://github.com/bl-sdk/mods_base) inside a simulated game, clone it
  and use Python 3.14 (the SDK's version):
  `MODS_BASE_DIR=/path/to/mods_base python3.14 -m unittest`.
- **Lint:** `ruff check . && ruff format --check .`
- **Build:** `python tools/build_sdkmod.py` writes `dist/borderlands_gamble.sdkmod`.
- **Live development:** point the SDK at this repo instead of building. In
  `<game>/OakGame/Binaries/Win64/Plugins/unrealsdk.user.toml`, add:
  ```toml
  [mod_manager]
  extra_folders = ["C:\\path\\to\\BorderlandsGamble\\src"]
  ```

## Credits

This mod stands on the BL4 modding community's work. See [docs/game-api.md](docs/game-api.md) for which
published mods proved out which game APIs: the [PythonSDK](https://github.com/bl-sdk) team, Matt's SDK
Boosting Tools, ActorScriptDeployer and BL4 Mods Menu, RedxYeti, FreepDryer, and Squ1ggs. Borderlands
is a trademark of Gearbox Software; this is an unofficial fan mod.
