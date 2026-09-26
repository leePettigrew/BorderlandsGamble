"""
Co-op message handling, independent of how the messages travel (see `bl4.CoopChannel`).

The host is the bank: it runs the only `Casino`, and every player's pulls are charged, rolled, and
paid out there. A client's own `SlotController` sends its pulls to the host, animates the result the
host sends back, then tells the host when its reels stop so the payout lands on time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from .casino import Casino, SlotController


def handle_host_message(
    casino: Casino,
    player: Any,
    text: str,
    reply: Callable[[str], None],
    log: Callable[[str], None],
) -> bool:
    """
    Handles a message a client sent to the host.

    Args:
        casino: The host's casino.
        player: The client who sent the message.
        text: The raw message.
        reply: Sends a message back to that client.
        log: Where to log problems.
    Returns:
        True if the message was one of ours (whether or not it could be handled).
    """
    try:
        message = protocol.decode(text)
    except protocol.ProtocolError as ex:
        log(f"Ignoring a bad message from {casino.backend.player_name(player)}: {ex}")
        return True

    match message:
        case None:
            return False
        case protocol.Pull() if message.mod_version != protocol.MOD_VERSION:
            reply(
                protocol.encode(
                    protocol.Error(
                        message.request_id,
                        f"Version mismatch: host has v{protocol.MOD_VERSION},"
                        f" you have v{message.mod_version}.",
                    ),
                ),
            )
        case protocol.Pull():
            outcome = casino.pull(player, message.request_id, message.machine_key, message.bet)
            if isinstance(outcome, str):
                reply(protocol.encode(protocol.Error(message.request_id, outcome)))
            else:
                result, charged = outcome
                reply(protocol.encode(protocol.to_result(message.request_id, result, charged)))
        case protocol.Settle():
            casino.settle(casino.backend.player_key(player), message.request_id)
        case protocol.Ping():
            reply(protocol.encode(protocol.Pong(message.nonce)))
        case _:
            log(f"Ignoring a host-bound {type(message).__name__} from {casino.backend.player_name(player)}")
    return True


def handle_client_message(
    controller: SlotController,
    text: str,
    on_pong: Callable[[protocol.Pong], None],
    log: Callable[[str], None],
) -> bool:
    """
    Handles a message the host sent to this client.

    Args:
        controller: This player's slot machine.
        text: The raw message.
        on_pong: Called with the host's reply to a ping.
        log: Where to log problems.
    Returns:
        True if the message was one of ours (whether or not it could be handled).
    """
    try:
        message = protocol.decode(text)
    except protocol.ProtocolError as ex:
        log(f"Ignoring a bad message from the host: {ex}")
        return True

    match message:
        case None:
            return False
        case protocol.Result():
            controller.receive_result(message.request_id, protocol.to_spin(message), message.charged)
        case protocol.Error():
            controller.receive_error(message.request_id, message.text)
        case protocol.Pong():
            on_pong(message)
        case _:
            log(f"Ignoring a client-bound {type(message).__name__} from the host")
    return True
