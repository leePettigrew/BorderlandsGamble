# Co-op

Both players install the same version of the mod. The **host is the bank**: every pull, whoever
makes it, is checked, charged, rolled and paid out by the host's game. That's the only place allowed
to change wallets and spawn loot. Each player still gets their own menu and reels on their own
screen.

## How a pull works

```
 Client (your friend)                          Host (you)
 ─────────────────────                         ─────────────────────────────
 pull the lever
   └─ "pull #1, cash, 1x" ── ServerExec ──▶    check they're near a machine
                                               charge THEIR wallet, roll the reels
      reels spin with the host's result ◀── ClientMessage ── "result #1, VAULT VAULT VAULT"
   reels stop
   └─ "settle #1" ─────────── ServerExec ──▶   pay THEIR wallet, drop loot at THEIR feet
```

- **Messages** are short strings like `BLGMB|1|pull|1|cash|1|0.3.0`
  ([`protocol.py`](../src/borderlands_gamble/protocol.py)). They ride on two network calls that
  every Unreal Engine player controller has: `ServerExec` (client → host; its normal job is a dev
  console, which shipping builds don't use) and `ClientMessage` (host → client). The mod catches
  its own messages on arrival and blocks them, so the game never acts on them
  ([`bl4.CoopChannel`](../src/borderlands_gamble/bl4.py)).
- **The payout waits for the client's reels**, so loot doesn't pop out before the jackpot shows. If
  the client never confirms (crash, disconnect), the host pays out anyway after 15 seconds.
- **The host's house rules apply to everyone**: luck, price multiplier, "only at machines", free
  play, and loot level. Each player picks their own machine, bet, animation speed, and menu size. A
  client's menu learns the host's price from its first pull.
- **Each player's game puts up its own slot machines** in the world. They aren't networked, but
  every game places them the same way, so you both see one at the same end of the same row. Changes
  made with `gamble_machine` are just for you. Since they always stand next to vending machines,
  the host's "only at machines" check passes for either of you.
- **Versions must match.** Results only carry the symbols, and each game works out the prize from its
  own paytable, so the host refuses clients on a different version (with a message saying so).
- **Stats are per player**, kept in each player's own settings file.

## Testing it together

1. Both install the same build. The version shows next to the mod in the `mods` menu (**0.3.0**).
2. Host starts the game; friend joins.
3. **Friend** runs `gamble_coop_test` in the console. Expected:
   `Co-op test: the host answered in 45 ms, running v0.3.0` then `all good, pull away!`
4. Host walks up to the slot machine in a safehouse, presses E, and pulls. This checks the host
   path.
5. Friend does the same, at the same slot machine. Check, in order:
   - Friend sees "Pulling the lever...", then the reels spin.
   - Friend's cash drops by the pull price right away.
   - When the reels stop, the friend's winnings arrive and loot drops in front of the **friend**.
   - Host's wallet doesn't change.
6. Friend walks away from the machines and pulls: they should get "Find a slot machine or vending
   machine to gamble at."

If step 3 says **no answer from the host**:
- Check the host has the mod enabled (`mods` in console) and that versions match.
- Run `gamble_diag` on both sides. The `Function /Script/Engine.PlayerController:ServerExec` and
  `...:ClientMessage` lines must be `[OK]`.
- If both pass but there's still no answer, BL4 is dropping one of those calls. Tell us, and we'll
  move the messages to a different network call. Everything else in co-op stays the same.

## What's verified and what isn't

The co-op logic runs in the automated tests: a simulated host and client pass real protocol messages
back and forth, and the full mod plays co-op sessions in a fake game. Nobody has run it in two real
copies of BL4 yet. The one real unknown is whether `ServerExec`/`ClientMessage` get through in BL4's
networking, which is exactly what `gamble_coop_test` checks.
