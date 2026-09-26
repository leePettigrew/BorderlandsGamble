# Borderlands Gamble

Slot machines for Borderlands 4, as a [PythonSDK](https://github.com/bl-sdk/oak2-mod-manager) mod.

Borderlands 4 shipped without the slot machines from earlier games. This mod adds them back. Walk up
to any vending machine, pull the lever, and three reels spin across the top of your screen. Wins pay
out in cash or eridium, or in real loot that the machine drops at your feet, straight from the game's
own item pools.

> **Status: prototype, not yet run in the real game.** The slot machine engine is fully unit tested.
> The game side is built only from APIs that other published BL4 SDK mods already use, or that every
> Unreal game has. Full solo and co-op sessions have been run against a simulated game. It still
> needs a first in-game test. If anything is off, run `gamble_diag` (see
> [Troubleshooting](#troubleshooting)).

Wondering how much of this is possible, and what's hard? See [docs/feasibility.md](docs/feasibility.md).

## Features

- **Two machines.** *Loot Slots* cost cash and get pricier as you level ($10 at level 1, $2.6k at 50,
  $25k at 70). *Eridium Slots* cost 10 eridium and roll rarer loot more often.
- **Real loot.** Loot prizes drop at your level from the game's rarity pools: rare, epic, and
  legendary guns, shields, grenades, repkits, class mods, and enhancements.
- **Animated reels.** The reels spin and stop left to right, drawn with the game's own UI system, so
  no custom assets are needed. Press the key again to skip the spin.
- **Bets and luck.** Choose a 1x/2x/5x/10x bet and a luck preset from *Stingy* to *Moxxi Likes You*.
- **Transparent odds.** Exact odds, not simulations: `gamble_odds` prints the full paytable.
- **Lifetime stats.** Tracks spins, net winnings, jackpots, and items won.
- **Co-op.** When both players have the mod, the host's game acts as the bank for everyone, and each
  player gets their own machine. See [docs/coop.md](docs/coop.md).

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

| Input | Does |
|---|---|
| **F8** (rebindable) | Pull the lever. Press again mid-spin to skip to the result. |
| *Switch Machine* (unbound) | Swap between Loot Slots and Eridium Slots. |
| *Change Bet* (unbound) | Cycle 1x/2x/5x/10x. |

Console commands:

| Command | Does |
|---|---|
| `gamble_spin` | Pull the lever. Works even if keybinds don't. |
| `gamble_odds [--machine cash\|eridium] [--luck NAME] [--level N]` | Print the paytable and exact odds. |
| `gamble_stats [--reset]` | Print or reset your lifetime stats. |
| `gamble_diag [--wallet]` | Check every game API the mod uses. `--wallet` also test-charges $1 and refunds it. |
| `gamble_coop_test` | As a co-op client, check the host's mod can hear you. |

Options (in the mods menu):
- **Your Machine:** machine, bet, spin time, result time, overlay scale and position.
- **House Rules:** luck, price multiplier, vending machine requirement, free play, and loot level. In
  co-op, the host's house rules apply to everyone.

## Troubleshooting

Game patches sometimes move things the mod relies on. Run `gamble_diag --wallet` in the console. Every
line should say `[OK]`. [docs/game-api.md](docs/game-api.md) lists what each check covers and gives
console snippets for investigating a failure. If charging breaks but everything else works, turn on
**Free Play** to keep playing.

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
  bl4.py        the game side: wallets, levels, loot drops, vending machines, co-op transport
  overlay.py    the on-screen machine, built from UMG widgets at runtime
  sdk_mod.py    options, keybinds, console commands, frame tick
tests/          unit tests, plus a fake game for full solo and co-op sessions
tools/          build_sdkmod.py, odds.py
docs/           feasibility study, co-op design, game API notes
```

Everything except `bl4.py`, `overlay.py` and `sdk_mod.py` is plain Python with no game dependency.
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
Boosting Tools, RedxYeti, FreepDryer, and Squ1ggs. Borderlands is a trademark of Gearbox Software; this
is an unofficial fan mod.
