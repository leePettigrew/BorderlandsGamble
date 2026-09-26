"""
The co-op protocol: clients ask the host to pull the lever, and the host answers.

Messages ride on network calls every Unreal player controller already has (see `coop.py`), so
they're short plain strings: `BLGMB|<protocol>|<type>|<fields...>`.

Results only carry the line of symbols. Both sides share the same paytables, so the client works
out the prize itself. That's also why a pull carries the mod version, and why the host refuses
clients running a different version.
"""

from __future__ import annotations

from dataclasses import dataclass

from .machines import BET_MULTIPLIERS, MACHINES
from .slots import Line, SpinResult, Symbol, scale_prize

MOD_VERSION = "0.3.0"

PREFIX = "BLGMB"
PROTOCOL = 1
SEPARATOR = "|"
# Unreal limits some string RPCs to 128 characters, so leave plenty of headroom
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


class ProtocolError(ValueError):
    """A message had our prefix, but couldn't be understood."""


# Client -> host


@dataclass(frozen=True)
class Pull:
    request_id: int
    machine_key: str
    bet: int
    mod_version: str = MOD_VERSION


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


Message = Pull | Settle | Ping | Result | Error | Pong


def is_ours(text: str) -> bool:
    return text.startswith(PREFIX + SEPARATOR)


def _clean(text: str) -> str:
    return " ".join(text.replace(SEPARATOR, "/").split())


def encode(message: Message) -> str:
    """Converts a message to the string sent over the wire."""
    match message:
        case Pull():
            fields = ["pull", message.request_id, message.machine_key, message.bet, message.mod_version]
        case Settle():
            fields = ["settle", message.request_id]
        case Ping():
            fields = ["ping", message.nonce]
        case Result():
            line = "".join(SYMBOL_CODES[symbol] for symbol in message.line)
            fields = [
                "result",
                message.request_id,
                message.machine_key,
                line,
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

    text = SEPARATOR.join(str(field) for field in [PREFIX, PROTOCOL, *fields])
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
    expected = {"pull": 4, "settle": 1, "ping": 1, "result": 6, "error": 2, "pong": 2}
    if kind not in expected:
        raise ProtocolError(f"Unknown message type '{kind}'")
    if len(fields) != expected[kind]:
        raise ProtocolError(f"'{kind}' needs {expected[kind]} fields, got {len(fields)}")

    match kind:
        case "pull":
            # Machine and bet are checked by the casino, so that a client on another version gets a
            # proper "versions don't match" reply rather than silence
            return Pull(_int(fields[0]), fields[1], _int(fields[2], minimum=1), fields[3])
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
        case _:
            return Pong(_int(fields[0]), fields[1])


def to_result(request_id: int, spin: SpinResult, charged: int) -> Result:
    return Result(request_id, spin.machine_key, spin.line, spin.bet, spin.stake, charged)


def to_spin(result: Result) -> SpinResult:
    """Rebuilds the full spin from a result, using the (shared) paytables."""
    machine = MACHINES[result.machine_key]
    prize = machine.evaluate(result.line)
    payout, eridium, loot = scale_prize(prize, result.stake, result.bet)
    return SpinResult(
        machine_key=result.machine_key,
        currency=machine.currency,
        line=result.line,
        prize=prize,
        bet=result.bet,
        stake=result.stake,
        payout=payout,
        eridium=eridium,
        loot=loot,
    )
