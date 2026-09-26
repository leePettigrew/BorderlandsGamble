"""Two players over a simulated network: the host runs the casino, the client plays through it."""

import random
import unittest
from collections.abc import Sequence
from typing import Any

from borderlands_gamble import coop, protocol
from borderlands_gamble.casino import (
    REPLY_TIMEOUT,
    SETTLE_TIMEOUT,
    Casino,
    HouseRules,
    LocalLink,
    PlayerSettings,
    RemoteLink,
    SlotController,
    Tone,
)
from borderlands_gamble.leaderboard import SpinRecord
from borderlands_gamble.machines import LOOT_SLOTS, spin_cost
from borderlands_gamble.slots import Currency, Symbol
from borderlands_gamble.stats import Stats

from .test_casino import FakeBackend, FakeClock, FakeDisplay, FakePlayer, ScriptedRandom

S = Symbol


class Network:
    """Queues messages in both directions until `flush`, like a real (laggy) connection."""

    def __init__(self) -> None:
        self.to_host: list[str] = []
        self.to_client: list[str] = []
        self.connected = True

    def flush(self, host: "Host", client: "Client") -> None:
        while self.to_host or self.to_client:
            for text in self._take(self.to_host):
                host.receive(client.player, text)
            for text in self._take(self.to_client):
                client.receive(text)

    def _take(self, queue: list[str]) -> list[str]:
        messages = list(queue) if self.connected else []
        queue.clear()
        return messages


class Host:
    def __init__(self, network: Network, casino: Casino) -> None:
        self.network = network
        self.casino = casino
        self.logs: list[str] = []

    def receive(self, sender: FakePlayer, text: str) -> None:
        handled = coop.handle_host_message(
            self.casino, sender, text, self.network.to_client.append, self.logs.append
        )
        assert handled


class Client:
    def __init__(self, network: Network, player: FakePlayer, clock: FakeClock) -> None:
        self.player = player
        self.display = FakeDisplay()
        self.stats = Stats()
        self.pongs: list[protocol.Pong] = []
        self.logs: list[str] = []
        self.settings = PlayerSettings()
        self.controller = SlotController(
            RemoteLink(network.to_host.append),
            self.display,
            settings=lambda: self.settings,
            stats=self.stats,
            rng=random.Random(1),
            clock=clock,
            log=self.logs.append,
        )

    def receive(self, text: str) -> None:
        assert coop.handle_client_message(self.controller, text, self.pongs.append, self.logs.append)


class CoopTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.backend = FakeBackend()
        self.network = Network()
        self.host_player = FakePlayer("Host")
        self.friend = FakePlayer("Friend", level=20)
        self.rules = HouseRules()

    def start(self, lines: Sequence[tuple[Symbol, Symbol, Symbol]] = (), **rules: Any) -> None:
        self.rules = HouseRules(**rules)
        self.casino = Casino(
            self.backend,
            lambda: self.rules,
            rng=ScriptedRandom(lines),
            clock=self.clock,
            log=lambda _: None,
        )
        self.host = Host(self.network, self.casino)
        self.client = Client(self.network, self.friend, self.clock)

    def flush(self) -> None:
        self.network.flush(self.host, self.client)

    def play_until_idle(self, limit: float = 30.0) -> None:
        end = self.clock.now + limit
        while True:
            client_busy = self.client.controller.tick()
            self.flush()
            host_busy = self.casino.tick()
            if not client_busy and not host_busy:
                return
            self.clock.now += 0.05
            if self.clock.now > end:
                self.fail("Never went idle")

    def final_view(self) -> Any:
        return [
            v for v in self.client.display.views if v.status not in ("Spinning...", "Pulling the lever...")
        ][-1]


class CoopTests(CoopTestCase):
    def test_client_pull_charges_and_pays_the_client(self) -> None:
        self.start([(S.CASH,) * 3])
        stake = spin_cost(LOOT_SLOTS, 20)

        self.client.controller.pull()
        self.assertTrue(self.client.controller.is_waiting)
        self.assertEqual(self.client.display.views[-1].status, "Pulling the lever...")

        self.flush()
        self.assertTrue(self.client.controller.is_spinning)
        self.assertEqual(self.friend.balances[Currency.CASH], 1_000_000 - stake)
        self.assertEqual(self.host_player.balances[Currency.CASH], 1_000_000, "host's wallet untouched")
        self.assertTrue(self.casino.has_pending, "payout waits for the client's reels")

        self.play_until_idle()
        self.assertEqual(self.friend.balances[Currency.CASH], 1_000_000 + 19 * stake)
        self.assertEqual(self.client.stats.spins, 1)
        self.assertEqual(self.client.stats.cash_won, 20 * stake)
        self.assertIn("Cash out!", self.final_view().status)

    def test_loot_drops_at_the_client(self) -> None:
        self.start([(S.LEGENDARY,) * 3])
        self.client.controller.pull()
        self.play_until_idle()
        self.assertEqual([(name, level) for name, _, level, *_ in self.backend.spawned], [("Friend", 20)])
        self.assertEqual(self.client.stats.items, {"legendary": 1})

    def test_house_rules_come_from_the_host(self) -> None:
        self.start([(S.SKULL, S.CASH, S.EPIC)], cost_multiplier=2.0)
        self.client.controller.pull()
        self.flush()
        self.assertEqual(
            self.friend.balances[Currency.CASH],
            1_000_000 - spin_cost(LOOT_SLOTS, 20, cost_multiplier=2.0),
        )

    def test_host_refusals_reach_the_client(self) -> None:
        self.start()
        self.friend.near_machine = False
        self.client.controller.pull()
        self.flush()
        self.assertFalse(self.client.controller.is_waiting)
        self.assertIn("vending machine", self.client.display.views[-1].status)
        self.assertEqual(self.client.display.views[-1].tone, Tone.ERROR)

    def test_version_mismatch(self) -> None:
        self.start()
        self.host.receive(self.friend, "BLGMB|1|pull|1|cash|1|0.0.1")
        reply = protocol.decode(self.network.to_client.pop())
        self.assertIsInstance(reply, protocol.Error)
        self.assertIn("Version mismatch", reply.text)
        self.assertEqual(self.backend.currency_calls, [])

    def test_silent_host_times_out(self) -> None:
        self.start()
        self.network.connected = False
        self.client.controller.pull()
        self.flush()
        self.clock.now += REPLY_TIMEOUT
        self.client.controller.tick()
        self.assertFalse(self.client.controller.is_waiting)
        self.assertIn("No answer from the host", self.client.display.views[-1].status)

    def test_client_skip_settles_immediately(self) -> None:
        self.start([(S.CASH,) * 3])
        self.client.controller.pull()
        self.flush()
        self.client.controller.pull()
        self.flush()
        self.assertFalse(self.casino.has_pending)
        self.assertEqual(self.client.stats.spins, 1)

    def test_disconnected_client_still_gets_paid(self) -> None:
        self.start([(S.CASH,) * 3])
        self.client.controller.pull()
        self.flush()
        self.network.connected = False
        self.clock.now += SETTLE_TIMEOUT
        self.assertFalse(self.casino.tick())
        self.assertEqual(self.friend.balances[Currency.CASH], 1_000_000 + 19 * spin_cost(LOOT_SLOTS, 20))

    def test_stale_replies_are_ignored(self) -> None:
        self.start([(S.CASH,) * 3])
        self.client.receive(protocol.encode(protocol.Result(99, "cash", (S.VAULT,) * 3, 1, 10, 10)))
        self.assertFalse(self.client.controller.is_spinning)

    def test_ping(self) -> None:
        self.start()
        self.network.to_host.append(protocol.encode(protocol.Ping(1234)))
        self.flush()
        self.assertEqual(self.client.pongs, [protocol.Pong(1234)])

    def test_records_reach_the_client(self) -> None:
        self.start()
        records: list[SpinRecord] = []
        spin = SpinRecord("Host", "cash", (S.CASH,) * 3, 1, 50_000, 50_000, 1_000_000)
        text = protocol.encode(spin)
        self.assertTrue(
            coop.handle_client_message(
                self.client.controller, text, print, self.client.logs.append, on_record=records.append
            )
        )
        self.assertEqual(records, [spin])
        # With nothing listening, they're still ours, just not used
        client = self.client
        self.assertTrue(coop.handle_client_message(client.controller, text, print, client.logs.append))
        self.assertEqual(self.client.logs, [])

    def test_the_host_ignores_records_from_clients(self) -> None:
        self.start()
        cheat = SpinRecord("Friend", "cash", (S.VAULT,) * 3, 10, 1, 0, 9)
        self.host.receive(self.friend, protocol.encode(cheat))
        self.assertIn("Ignoring a host-bound SpinRecord", self.host.logs[-1])

    def test_bad_messages_are_logged_not_raised(self) -> None:
        self.start()
        self.host.receive(self.friend, "BLGMB|1|pull|nope")
        self.client.receive("BLGMB|1|result|garbage")
        self.assertTrue(self.host.logs)
        self.assertTrue(self.client.logs)

    def test_other_strings_are_not_ours(self) -> None:
        self.start()
        self.assertFalse(coop.handle_host_message(self.casino, self.friend, "stat fps", print, print))
        self.assertFalse(coop.handle_client_message(self.client.controller, "Welcome!", print, print))

    def test_host_and_client_play_at_once(self) -> None:
        self.start([(S.CASH,) * 3, (S.ERIDIUM,) * 3])
        host_display = FakeDisplay()
        host_controller = SlotController(
            LocalLink(self.casino, lambda: self.host_player),
            host_display,
            settings=PlayerSettings,
            stats=Stats(),
            rng=random.Random(2),
            clock=self.clock,
            log=lambda _: None,
        )
        host_controller.pull()
        self.client.controller.pull()
        self.flush()
        for _ in range(200):
            host_controller.tick()
            self.client.controller.tick()
            self.flush()
            self.casino.tick()
            self.clock.now += 0.05
        self.assertEqual(self.host_player.balances[Currency.CASH], 1_000_000 + 19 * spin_cost(LOOT_SLOTS, 50))
        self.assertEqual(self.friend.balances[Currency.ERIDIUM], 540)


if __name__ == "__main__":
    unittest.main()
