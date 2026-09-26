# Co-op

Both players install the same version of the mod, and **gamble separately**: each has their own
menu, machine, bet and drops choice, pays from their own wallet, and wins for themselves. You can
both play the same slot machine at once.

Behind the scenes, the **host is the bank**. BL4 only lets the host's game change anyone's money or
create items, so every pull, whoever makes it, is checked, charged, and rolled by the host's game.
It also *pays out*: when the reels stop, it adds the winnings to the winner's wallet and drops any
loot on the floor in front of their slot machine.

## How a pull works

```
 Client (your friend)                                     Host (you)
 ─────────────────────                                    ─────────────────────────────
 pull the lever
   └─ "pull #1, cash, shotguns, 1x" ─ ServerExecRPC ─▶    check they're near a machine
                                                          charge THEIR wallet, roll the reels
                                                          show everyone else the spin
      reels spin with the host's result ◀── ClientMessage ── "result #1, VAULT VAULT VAULT"
   reels stop
   └─ "settle #1" ──────────────────── ServerExecRPC ─▶   pay THEIR wallet, drop loot
                                                          in front of their machine
```

- **Messages** are short strings like `BLGMB|1|pull|1|cash.shotguns|1|0.4.3`
  ([`protocol.py`](../src/borderlands_gamble/protocol.py)). They ride on two network calls that
  every Unreal Engine player controller has: `ServerExecRPC` (client → host; its normal job is
  dev-build console commands) and `ClientMessage` (host → client). The mod catches its own messages
  on arrival and blocks them, so the game never acts on them
  ([`bl4.CoopChannel`](../src/borderlands_gamble/bl4.py)).
- **The payout waits for the client's reels**, so loot doesn't pop out before the jackpot shows. If
  the client never confirms (crash, disconnect), the host pays out anyway after 15 seconds.
- **Everyone watches everyone.** Whenever anyone pulls, the host sends everyone else a `show` message
  with the line of symbols. Their games spin a small copy of the reels above that player's head, then
  show what they won for a few seconds. If that player is off screen or far away, it shows in the top
  left corner instead. Turn it off with **Show Others' Spins**.
- **The host's house rules apply to everyone**: luck, price multiplier, "only at machines", free
  play, and loot level. Each player picks their own machine, bet, animation speed, and menu size. A
  client's menu learns the host's price from its first pull.
- **Each player's game puts up its own slot machines** in the world. They aren't networked, but
  every game places them the same way, so you both see one at the same end of the same row. They have
  no collision, so machines only one of you has can't turn into invisible walls.
- **Changes made with `gamble_machine` are just for you.** Automatic machines stand next to vending
  machines, so the host's "only at machines" check passes for either of you there. A machine a client
  adds somewhere else only exists in the client's game, so the host refuses pulls there, unless the
  client is also near a vending machine or one of the host's machines, or the host turns off **Only
  At Machines**.
- **Loot drops for both of you to see, we think.** BL4's own loot is per player: normally each of
  you gets your own drops, and can't see the other's. The mod's drops are made a different way: the
  host's game spawns them straight from the item pools, the way other mods spawn items, with no
  player attached. We expect both of you to see them, but it's the first thing to check together.
- **Versions must match.** Results only carry the symbols, and each game works out the prize from its
  own paytable, so the host refuses clients on a different version (with a message saying so).
- **Stats are per player**, kept in each player's own settings file.

## Testing it together

1. Both install the same build. The version shows next to the mod in the `mods` menu (**0.4.3**).
2. Host starts the game; friend joins.
3. **Friend** runs `gamble_coop_test` in the console. Expected:
   `Co-op test: the host answered in 45 ms, running v0.4.3` then `all good, pull away!`
4. Host walks up to the slot machine in a safehouse, presses E, and pulls. This checks the host
   path. The friend should see the host's reels spinning above the host's head.
5. Friend does the same, at the same slot machine. Check, in order:
   - Friend sees "Pulling the lever...", then the reels spin.
   - Friend's cash drops by the pull price right away.
   - When the reels stop, the friend's winnings arrive, and any loot drops in front of the machine.
   - Host's wallet doesn't change.
   - Host sees the friend's reels above the friend's head, then what they won.
   - **Both** of you can see (and either can pick up) the loot. If only the winner can, tell us.
6. Friend walks away from the machines and pulls: they should get "Find a slot machine or vending
   machine to gamble at."

If step 3 says **no answer from the host**:
- Check the host has the mod enabled (`mods` in console) and that versions match.
- Run `gamble_diag` on both sides. The `Function /Script/Engine.PlayerController:ServerExecRPC`
  and `...:ClientMessage` lines must be `[OK]`.
- Did the test reach the host? The host's console says `<name> ran the co-op test. Answering.` If
  it does, the answer is what's lost on the way back. If it doesn't, the question never arrived.
- Versions up to 0.4.2 sent with `ServerExec`, which never leaves the client in BL4 (see
  [game-api.md](game-api.md#co-op)), so they always get no answer.

## What's verified and what isn't

The co-op logic runs in the automated tests: a simulated host and client pass real protocol messages
back and forth, and the full mod plays co-op sessions in a fake game. Nobody has run it in two real
copies of BL4 with this version yet. The first real co-op test, on 0.4.0, found pulls never reached
the host, because they went through `ServerExec`. 0.4.3 uses `ServerExecRPC`, the actual network
call, but whether it and `ClientMessage` get through in BL4's networking still needs checking. That's
exactly what `gamble_coop_test` does.
