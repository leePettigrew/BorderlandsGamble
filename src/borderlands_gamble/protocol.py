"""
The co-op protocol: clients ask the host to pull the lever, and the host answers.

Messages ride on network calls every Unreal player controller already has (see `coop.py`), so
they're short plain strings: `BLGMB|<protocol>|<type>|<fields...>`.

Results only carry the line of symbols. Both sides share the same paytables, so the client works
out the prize itself. That's also why a pull carries the mod version, and why the host refuses
clients running a different version.

A machine and the loot type the player picked travel together as `<machine>` or
`<machine>.<loot type>`. Keeping the pull's shape the same as older versions means an older host
still answers "versions don't match", rather than not understanding the pull at all.

Once a pull has paid out, the host sends everyone a `leaderboard.SpinRecord` of it, so that every
player's leaderboard has everyone's pulls, and what they really paid and dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .leaderboard import Drop, SpinRecord, clean_name
from .loot import DEFAULT_LOOT_TYPE, ITEM_FAMILIES
from .machines import BET_MULTIPLIERS, MACHINES, replay
from .slots import MAX_ITEMS_PER_SPIN, Line, SpinResult, Symbol, Tier

if TYPE_CHECKING:
    from collections.abc import Iterable

MOD_VERSION = "0.5.0"

PREFIX = "BLGMB"
PROTOCOL = 1
SEPARATOR = "|"
# The host kicks a client whose ServerExecRPC message is over 128 characters, so leave some headroom
MAX_LENGTH = 120

SYMBOL_CODES: dict[Symbol, str] = {
    Symbol.SKULL: "S",
    Symbol.CASH: "C",
    Symbol.ERIDIUM: "E",
    Symbol.RARE: "R",
    Symbol.EPIC: "P",
    Symbol.LEGENDARY: "L",
    Symbol.VAULT: "V",
}
_CODE_SYMBOLS = {code: symbol for symbol, code in SYMBOL_CODES.items()}

TIER_CODES: dict[Tier, str] = {Tier.RARE: "R", Tier.EPIC: "E", Tier.LEGENDARY: "L"}
_CODE_TIERS = {code: tier for tier, code in TIER_CODES.items()}
_FAMILY_CODES = {family: code for family, (_, code) in ITEM_FAMILIES.items()}
_CODE_FAMILIES = {code: family for family, code in _FAMILY_CODES.items()}
# A group of dropped items: how many (if more than one), their rarity, then what they are if known
_DROP_GROUP = re.compile(r"(\d*)([A-Z])([a-z]{2})?")


class ProtocolError(ValueError):
    """A message had our prefix, but couldn't be understood."""


# Client -> host


@dataclass(frozen=True)
class Pull:
    request_id: int
    machine_key: str
    bet: int
    mod_version: str = MOD_VERSION
    loot_type: str = DEFAULT_LOOT_TYPE


@dataclass(frozen=True)
class Settle:
    """The client's reels stopped, so the host should pay out now."""

    request_id: int


@dataclass(frozen=True)
class Ping:
    nonce: int


# Host -> client


@dataclass(frozen=True)
class Result:
    request_id: int
    machine_key: str
    line: Line
    bet: int
    stake: int
    charged: int


@dataclass(frozen=True)
class Error:
    request_id: int
    text: str


@dataclass(frozen=True)
class Pong:
    nonce: int
    mod_version: str = MOD_VERSION


@dataclass(frozen=True)
class Show:
    """Another player pulled the lever. Sent to everyone else, so they can watch the reels."""

    player_id: int
    machine_key: str
    line: Line
    bet: int
    stake: int
    loot_type: str = DEFAULT_LOOT_TYPE


Message = Pull | Settle | Ping | Result | Error | Pong | Show | SpinRecord


def is_ours(text: str) -> bool:
    return text.startswith(PREFIX + SEPARATOR)


def _clean(text: str) -> str:
    return " ".join(text.replace(SEPARATOR, "/").split())


def _game(machine_key: str, loot_type: str) -> str:
    return machine_key if loot_type == DEFAULT_LOOT_TYPE else f"{machine_key}.{loot_type}"


def _split_game(value: str) -> tuple[str, str]:
    machine_key, _, loot_type = value.partition(".")
    if not machine_key or (loot_type and not loot_type.replace("_", "").isalnum()):
        raise ProtocolError(f"Bad machine '{value}'")
    return machine_key, loot_type or DEFAULT_LOOT_TYPE


def _line_codes(line: Line) -> str:
    return "".join(SYMBOL_CODES[symbol] for symbol in line)


def _drop_codes(drops: Iterable[Drop], *, kinds: bool) -> str:
    """Lists dropped items, e.g. "2Lsg,Esh" for two legendary shotguns and an epic shield."""
    groups: dict[tuple[Tier, str | None], int] = {}
    for drop in drops:
        key = (drop.tier, drop.family if kinds else None)
        groups[key] = groups.get(key, 0) + 1
    return ",".join(
        f"{count if count > 1 else ''}{TIER_CODES[tier]}{_FAMILY_CODES.get(family or '', '')}"
        for (tier, family), count in groups.items()
    )


def _join(fields: list[object]) -> str:
    return SEPARATOR.join(str(field) for field in [PREFIX, PROTOCOL, *fields])


def _encode_record(record: SpinRecord) -> str:
    # Say what each item is if it fits, then fall back to just how many of each rarity, then to a
    # shorter name. The biggest numbers still fit with an 8 character name.
    text = ""
    for name, kinds in ((record.player, True), (record.player, False), (record.player[:8], False)):
        text = _join(
            [
                "record",
                _clean(name),
                record.machine_key,
                _line_codes(record.line),
                record.bet,
                record.stake,
                record.charged,
                record.cash,
                record.eridium,
                _drop_codes(record.drops, kinds=kinds),
            ],
        )
        if len(text) <= MAX_LENGTH:
            return text
    raise ProtocolError(f"Message too long ({len(text)} characters): {text}")


def encode(message: Message) -> str:
    """Converts a message to the string sent over the wire."""
    match message:
        case SpinRecord():
            return _encode_record(message)
        case Pull():
            game = _game(message.machine_key, message.loot_type)
            fields = ["pull", message.request_id, game, message.bet, message.mod_version]
        case Settle():
            fields = ["settle", message.request_id]
        case Ping():
            fields = ["ping", message.nonce]
        case Result():
            fields = [
                "result",
                message.request_id,
                message.machine_key,
                _line_codes(message.line),
                message.bet,
                message.stake,
                message.charged,
            ]
        case Error():
            fields = ["error", message.request_id, ""]
            head = SEPARATOR.join(str(f) for f in [PREFIX, PROTOCOL, *fields])
            fields[-1] = _clean(message.text)[: MAX_LENGTH - len(head)]
        case Pong():
            fields = ["pong", message.nonce, message.mod_version]
        case Show():
            fields = [
                "show",
                message.player_id,
                _game(message.machine_key, message.loot_type),
                _line_codes(message.line),
                message.bet,
                message.stake,
            ]

    text = _join(fields)
    if len(text) > MAX_LENGTH:
        raise ProtocolError(f"Message too long ({len(text)} characters): {text}")
    return text


def _int(value: str, *, minimum: int = 0) -> int:
    if not value.isdigit():
        raise ProtocolError(f"Expected a number, got '{value}'")
    number = int(value)
    if number < minimum:
        raise ProtocolError(f"Expected at least {minimum}, got {number}")
    return number


def _machine(value: str) -> str:
    if value not in MACHINES:
        raise ProtocolError(f"Unknown machine '{value}'")
    return value


def _bet(value: str) -> int:
    bet = _int(value, minimum=1)
    if bet not in BET_MULTIPLIERS:
        raise ProtocolError(f"Unsupported bet {bet}x")
    return bet


def _line(value: str) -> Line:
    if len(value) != 3 or any(code not in _CODE_SYMBOLS for code in value):
        raise ProtocolError(f"Bad line '{value}'")
    return (_CODE_SYMBOLS[value[0]], _CODE_SYMBOLS[value[1]], _CODE_SYMBOLS[value[2]])


def _drops(value: str) -> tuple[Drop, ...]:
    drops: list[Drop] = []
    for group in value.split(",") if value else ():
        match = _DROP_GROUP.fullmatch(group)
        if match is None or match[2] not in _CODE_TIERS or (match[3] and match[3] not in _CODE_FAMILIES):
            raise ProtocolError(f"Bad drops '{value}'")
        count = int(match[1]) if match[1] else 1
        drops += [Drop(_CODE_TIERS[match[2]], _CODE_FAMILIES.get(match[3] or ""))] * count
    if len(drops) > MAX_ITEMS_PER_SPIN:
        raise ProtocolError(f"Too many drops in '{value}'")
    return tuple(drops)


def decode(text: str) -> Message | None:
    """
    Parses a message.

    Args:
        text: The raw string that came over the wire.
    Returns:
        The message, or None if the string isn't one of ours.
    """
    if not is_ours(text):
        return None
    parts = text.split(SEPARATOR)
    if len(parts) < 3 or parts[1] != str(PROTOCOL):
        raise ProtocolError(f"Unsupported protocol in '{text}'")

    kind, fields = parts[2], parts[3:]
    expected = {
        "pull": 4,
        "settle": 1,
        "ping": 1,
        "result": 6,
        "error": 2,
        "pong": 2,
        "show": 5,
        "record": 9,
    }
    if kind not in expected:
        raise ProtocolError(f"Unknown message type '{kind}'")
    if len(fields) != expected[kind]:
        raise ProtocolError(f"'{kind}' needs {expected[kind]} fields, got {len(fields)}")

    match kind:
        case "pull":
            # Machine and bet are checked by the casino, so that a client on another version gets a
            # proper "versions don't match" reply rather than silence
            machine_key, loot_type = _split_game(fields[1])
            return Pull(_int(fields[0]), machine_key, _int(fields[2], minimum=1), fields[3], loot_type)
        case "settle":
            return Settle(_int(fields[0]))
        case "ping":
            return Ping(_int(fields[0]))
        case "result":
            return Result(
                request_id=_int(fields[0]),
                machine_key=_machine(fields[1]),
                line=_line(fields[2]),
                bet=_bet(fields[3]),
                stake=_int(fields[4]),
                charged=_int(fields[5]),
            )
        case "error":
            return Error(_int(fields[0]), fields[1])
        case "show":
            machine_key, loot_type = _split_game(fields[1])
            return Show(
                player_id=_int(fields[0]),
                machine_key=_machine(machine_key),
                line=_line(fields[2]),
                bet=_bet(fields[3]),
                stake=_int(fields[4]),
                loot_type=loot_type,
            )
        case "record":
            try:
                return SpinRecord(
                    player=clean_name(fields[0]),
                    machine_key=_machine(fields[1]),
                    line=_line(fields[2]),
                    bet=_bet(fields[3]),
                    stake=_int(fields[4]),
                    charged=_int(fields[5]),
                    cash=_int(fields[6]),
                    eridium=_int(fields[7]),
                    drops=_drops(fields[8]),
                )
            except ValueError as ex:
                raise ProtocolError(f"Bad record '{text}': {ex}") from ex
        case _:
            return Pong(_int(fields[0]), fields[1])


def to_result(request_id: int, spin: SpinResult, charged: int) -> Result:
    return Result(request_id, spin.machine_key, spin.line, spin.bet, spin.stake, charged)


def to_show(player_id: int, spin: SpinResult, loot_type: str) -> Show:
    return Show(player_id, spin.machine_key, spin.line, spin.bet, spin.stake, loot_type)


def to_spin(result: Result | Show) -> SpinResult:
    """Rebuilds the full spin from a result, using the (shared) paytables."""
    return replay(result.machine_key, result.line, result.bet, result.stake)
