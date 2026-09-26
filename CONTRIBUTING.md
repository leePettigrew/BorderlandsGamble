# Working on Borderlands Gamble

## One-time setup

1. Install the [BL4 PythonSDK](https://bl-sdk.github.io/oak2-mod-db/) in your game.
2. Clone this repo anywhere.
3. Point the SDK at your clone instead of a built `.sdkmod`. Create
   `<game>/OakGame/Binaries/Win64/Plugins/unrealsdk.user.toml`:
   ```toml
   [unrealsdk]
   console_log_level = "DWRN"

   [mod_manager]
   extra_folders = ["C:\\path\\to\\BorderlandsGamble\\src"]
   ```
   Remove any `borderlands_gamble.sdkmod` from `sdk_mods`, so the game loads your clone.
4. Optional: install Python 3.14 (the SDK's version) and [ruff](https://docs.astral.sh/ruff/) for the
   checks below.

## Day to day

- **Edit, then reload in game:** `rlm borderlands_gamble*` in the console reloads the mod without
  restarting. If anything behaves oddly afterwards, restart the game.
- **Try things live:** `py <python>` runs one line inside the game, e.g.
  `py from mods_base import get_pc; print(get_pc().Pawn)`. [docs/game-api.md](docs/game-api.md) has
  snippets for every game API the mod uses.
- **Logs:** everything the mod prints starts with `[Borderlands Gamble]`. It shows in the console
  (`~`) and in `OakGame/Binaries/Win64/Plugins/unrealsdk.log`.

## Before pushing

```
python -m unittest                  # 160+ tests, a couple of seconds
ruff check . && ruff format --check .
python tools/build_sdkmod.py        # builds dist/borderlands_gamble.sdkmod
```

To also run a full session of the mod in a simulated game through the real mods_base (Python 3.14):

```
git clone https://github.com/bl-sdk/mods_base ../mods_base
MODS_BASE_DIR=../mods_base python3.14 -m unittest
```

## Where things live

| File | What | Needs the game? |
|---|---|---|
| `slots.py`, `machines.py`, `loot.py` | Symbols, reels, paytables, prices, prize item pools | No |
| `animation.py`, `stats.py`, `report.py` | Reel animation, lifetime stats, odds tables | No |
| `casino.py` | `Casino` (the bank) and `SlotController` (a player's machine) | No |
| `protocol.py`, `coop.py` | Co-op messages and how each side handles them | No |
| `cabinets.py` | Where slot machines stand in the world, and which one you're aiming at | No |
| `menu_model.py` | What the menu shows, and what its buttons and keys do | No |
| `bl4.py` | Everything that touches BL4: wallets, levels, loot drops, vending machines, co-op transport | Yes |
| `world.py` | Building slot machines as copies of vending machines | Yes |
| `overlay.py`, `menu.py` | The HUD reels, the "[E] Play" prompt, and the menu (UMG widgets) | Yes |
| `sdk_mod.py` | Options, keybinds, console commands, frame tick | Yes |

The "No" files have no game dependency and are covered by unit tests. Most gameplay changes (odds,
prizes, new machines) only touch those.

## Rules of thumb

- **Co-op needs matching versions.** Any change to paytables, reels, or `protocol.py` must bump
  `MOD_VERSION` in `protocol.py` *and* both versions in `src/borderlands_gamble/pyproject.toml` (a
  test checks they agree). Both players then update.
- **Only the host changes wallets or spawns loot.** Anything new that pays out goes through `Casino`.
- **Keep game calls in `bl4.py`, `world.py`, `overlay.py` and `menu.py`**, so the rest stays
  testable without the game. The fake game in `tests/fake_sdk` needs to learn any new game function
  the mod calls, so the full session keeps covering it.
- Work on a branch and open a pull request. It's easier to review, and to test together before
  merging.
