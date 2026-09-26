import random
import tomllib
import unittest
from pathlib import Path

from borderlands_gamble import protocol
from borderlands_gamble.leaderboard import NAME_LENGTH, Drop, SpinRecord
from borderlands_gamble.loot import ITEM_FAMILIES
from borderlands_gamble.machines import MACHINES
from borderlands_gamble.protocol import (
    MAX_LENGTH,
    MOD_VERSION,
    Error,
    Ping,
    Pong,
    ProtocolError,
    Pull,
    Result,
    Settle,
    Show,
    decode,
    encode,
)
from borderlands_gamble.slots import Symbol, Tier, spin

S = Symbol


class RoundTripTests(unittest.TestCase):
    def test_every_message_round_trips(self) -> None:
        messages = [
            Pull(1, "cash", 10),
            Pull(987654, "eridium", 1, "9.9.9"),
            Pull(2, "cash", 5, MOD_VERSION, "class_mods"),
            Settle(42),
            Ping(123456),
            Result(3, "cash", (S.VAULT, S.SKULL, S.EPIC), 5, 13000, 13000),
            Result(4, "eridium", (S.LEGENDARY,) * 3, 1, 10, 0),
            Error(5, "Not enough cash: a pull costs $2,600."),
            Pong(99),
            Show(256, "cash", (S.VAULT,) * 3, 10, 26000),
            Show(2_147_483_647, "eridium", (S.SKULL, S.RARE, S.EPIC), 1, 15, "assault_rifles"),
            SpinRecord("Zane", "cash", (S.SKULL,) * 3, 1, 50_000, 50_000),
            SpinRecord(
                "ZoVxBeeF",
                "cash",
                (S.VAULT,) * 3,
                2,
                100_000,
                0,
                5_000_000,
                0,
                (Drop(Tier.LEGENDARY, "sg"),) * 2 + (Drop(Tier.LEGENDARY, "class_mods"), Drop(Tier.RARE)),
            ),
            SpinRecord("Amara", "eridium", (S.ERIDIUM,) * 3, 10, 100, 100, 0, 1500),
        ]
        for message in messages:
            with self.subTest(message=message):
                text = encode(message)
                self.assertTrue(protocol.is_ours(text))
                self.assertLessEqual(len(text), MAX_LENGTH)
                self.assertEqual(decode(text), message)

    def test_records_fit_at_their_biggest(self) -> None:
        # A long name, the biggest amounts, and as many different items as a pull can drop
        families = list(ITEM_FAMILIES)
        drops = tuple(Drop(tier, families[idx % len(families)]) for idx, tier in enumerate([Tier.EPIC] * 20))
        big = 2_147_483_647
        biggest = SpinRecord("x" * NAME_LENGTH, "eridium", (S.VAULT,) * 3, 10, big, big, big, big, drops)
        text = encode(biggest)
        self.assertLessEqual(len(text), MAX_LENGTH)
        decoded = decode(text)
        assert isinstance(decoded, SpinRecord)
        # Too long to say what every item is, so it just counts them
        self.assertEqual(decoded.drops, (Drop(Tier.EPIC),) * 20)
        self.assertEqual((decoded.stake, decoded.cash, decoded.eridium), (big, big, big))

    def test_records_say_what_dropped_when_they_can(self) -> None:
        drops = (Drop(Tier.LEGENDARY, "sg"),) * 2 + (Drop(Tier.EPIC, "shields"), Drop(Tier.RARE))
        text = encode(SpinRecord("Zane", "cash", (S.VAULT,) * 3, 1, 50_000, 50_000, 2_500_000, 0, drops))
        self.assertTrue(text.endswith("|2Lsg,Esh,R"), text)

    def test_bad_records_are_refused(self) -> None:
        head = "BLGMB|1|record|Zane|cash|VVV|1|10|10|0|0|"
        for drops in ("2Xsg", "Lzz", "21L", "L,,E", "lsg", "-1L"):
            with self.subTest(drops=drops), self.assertRaises(ProtocolError):
                decode(head + drops)
        with self.assertRaises(ProtocolError):
            decode("BLGMB|1|record|Zane|cash|VVV|0|10|10|0|0|")
        self.assertEqual(decode("BLGMB|1|record| |cash|VVV|1|10|10|0|0|").player, "Player")

    def test_every_symbol_has_a_unique_code(self) -> None:
        self.assertEqual(set(protocol.SYMBOL_CODES), set(Symbol))
        self.assertEqual(len(set(protocol.SYMBOL_CODES.values())), len(Symbol))

    def test_error_text_is_cleaned_and_truncated(self) -> None:
        text = encode(Error(1, "bad | news\nhere " + "x" * 500))
        self.assertLessEqual(len(text), MAX_LENGTH)
        decoded = decode(text)
        self.assertIsInstance(decoded, Error)
        self.assertTrue(decoded.text.startswith("bad / news here"))

    def test_loot_type_rides_with_the_machine(self) -> None:
        # So a pull keeps the same number of fields as older versions sent
        self.assertEqual(
            encode(Pull(1, "cash", 2, "0.4.0", "shotguns")), "BLGMB|1|pull|1|cash.shotguns|2|0.4.0"
        )
        self.assertEqual(encode(Pull(1, "cash", 2, "0.4.0")), "BLGMB|1|pull|1|cash|2|0.4.0")
        # Older clients' pulls still decode, as "anything"
        self.assertEqual(decode("BLGMB|1|pull|1|cash|2|0.3.0"), Pull(1, "cash", 2, "0.3.0", "any"))

    def test_shows_rebuild_the_spin(self) -> None:
        rng = random.Random(9)
        for machine in MACHINES.values():
            original = spin(machine, rng, stake=39, bet=2)
            message = decode(encode(protocol.to_show(300, original, "shields")))
            assert isinstance(message, Show)
            self.assertEqual((message.player_id, message.loot_type), (300, "shields"))
            self.assertEqual(protocol.to_spin(message), original)

    def test_results_rebuild_the_spin(self) -> None:
        rng = random.Random(5)
        for machine in MACHINES.values():
            for _ in range(300):
                original = spin(machine, rng, stake=2600, bet=2)
                message = decode(encode(protocol.to_result(8, original, 2600)))
                self.assertEqual(protocol.to_spin(message), original)


class DecodeTests(unittest.TestCase):
    def test_ignores_other_strings(self) -> None:
        for text in ("", "hello", "BLGMB", "BLGMBX|1|ping|1", "say BLGMB|1|ping|1"):
            self.assertIsNone(decode(text))

    def test_rejects_malformed_messages(self) -> None:
        for text in (
            "BLGMB|2|ping|1",
            "BLGMB|1|dance|1",
            "BLGMB|1|ping",
            "BLGMB|1|ping|1|2",
            "BLGMB|1|ping|-1",
            "BLGMB|1|settle|abc",
            "BLGMB|1|pull|1|cash|0|0.2.0",
            "BLGMB|1|pull|1|.shotguns|1|0.4.0",
            "BLGMB|1|pull|1|cash.sho tguns|1|0.4.0",
            "BLGMB|1|show|1|roulette|VVV|1|10",
            "BLGMB|1|show|x|cash|VVV|1|10",
            "BLGMB|1|show|1|cash|VVV|1",
            "BLGMB|1|result|1|roulette|VVV|1|10|10",
            "BLGMB|1|result|1|cash|VVX|1|10|10",
            "BLGMB|1|result|1|cash|VV|1|10|10",
            "BLGMB|1|result|1|cash|VVV|3|10|10",
        ):
            with self.subTest(text=text), self.assertRaises(ProtocolError):
                decode(text)

    def test_pull_leaves_machine_checks_to_the_casino(self) -> None:
        # So a client on another version gets a proper "versions don't match" answer
        self.assertEqual(decode("BLGMB|1|pull|1|roulette|3|9.0.0"), Pull(1, "roulette", 3, "9.0.0"))


class VersionTests(unittest.TestCase):
    def test_matches_pyproject(self) -> None:
        pyproject = Path(__file__).resolve().parent.parent / "src" / "borderlands_gamble" / "pyproject.toml"
        with pyproject.open("rb") as file:
            data = tomllib.load(file)
        self.assertEqual(MOD_VERSION, data["project"]["version"])
        self.assertEqual(MOD_VERSION, data["tool"]["sdkmod"]["version"])


if __name__ == "__main__":
    unittest.main()
