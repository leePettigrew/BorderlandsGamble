"""
The Borderlands 4 side of the mod: everything that talks to the game through unrealsdk.

Every game API used here is one that other published BL4 SDK mods already rely on, or is part of
every Unreal Engine game. See `docs/game-api.md` for where each one comes from, and how to poke at
it from the console when a game patch breaks something.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import unrealsdk
from mods_base import ENGINE, get_pc
from unrealsdk import logging
from unrealsdk.hooks import Block, Type, add_hook, prevent_hooking_direct_calls, remove_hook
from unrealsdk.unreal import FGbxDefPtr, UObject, WeakPointer

from . import loot, protocol
from .slots import Currency

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from unrealsdk.unreal import BoundFunction, WrappedStruct

CURRENCY_DEF_PATHS = ("/Script/GbxGame.GbxCurrencyDef", "/Script/OakGame.GbxCurrencyDef")
EXPERIENCE_DEF_PATHS = ("/Script/GbxGame.GbxExperienceDef", "/Script/OakGame.GbxExperienceDef")
CHARACTER_EXPERIENCE = "Character"

CURRENCY_LIBRARY_CLASS = "GbxCurrencyFunctionLibrary"
ITEM_POOL_STORE_CLASS = "NexusConfigStoreItemPool"
VENDING_MACHINE_CLASS = "OakVendingMachine"

# Co-op messages ride on two network calls every Unreal player controller has. ServerExec sends a
# string from a client to the host (its normal job is a dev console, which shipping builds ignore),
# and ClientMessage sends a string from the host to one client.
SERVER_RPC = "/Script/Engine.PlayerController:ServerExec"
CLIENT_RPC = "/Script/Engine.PlayerController:ClientMessage"
COOP_HOOK_ID = "borderlands_gamble.coop"


def is_template(obj: UObject) -> bool:
    return str(obj.Name).startswith("Default__")


def _find_script_struct(paths: tuple[str, ...], name: str) -> UObject | None:
    for path in paths:
        try:
            return unrealsdk.find_object("ScriptStruct", path)
        except ValueError:
            pass
    # Slow fallback, in case a patch moves the struct to another module
    for struct in unrealsdk.find_all("ScriptStruct", exact=False):
        if str(struct.Name) == name:
            return struct
    return None


def def_name(ptr: Any) -> str | None:
    """Gets the name of the def behind an `FGbxDefPtr`, e.g. a currency row's type."""
    for attr in ("_name", "Name", "name"):
        try:
            value = getattr(ptr, attr)
        except Exception:  # noqa: BLE001 - try the next spelling
            continue
        if value:
            return str(value)
    return None


def local_player() -> UObject | None:
    try:
        return get_pc(possibly_loading=True)
    except (IndexError, AttributeError):
        # No local player yet, e.g. mid load
        return None


def is_host() -> bool:
    """True in single player, or when hosting co-op. False when we joined someone else's game."""
    pc = local_player()
    if pc is None:
        return True
    try:
        return bool(pc.HasAuthority())
    except Exception as ex:  # noqa: BLE001 - if we can't tell, act as the host
        logging.dev_warning(f"[Borderlands Gamble] HasAuthority failed: {ex!r}")
        return True


def run_console_command(pc: UObject, text: str) -> bool:
    """Runs a game console command, as if it was typed into the console. Returns if it could."""
    try:
        library = unrealsdk.find_class("KismetSystemLibrary").ClassDefaultObject
        library.ExecuteConsoleCommand(pc, text, pc)
    except Exception as ex:  # noqa: BLE001
        logging.dev_warning(f"[Borderlands Gamble] Couldn't run '{text}': {ex!r}")
        return False
    return True


@dataclass
class Check:
    ok: bool
    label: str
    detail: str = ""

    def __str__(self) -> str:
        return f"[{'OK' if self.ok else '!!'}] {self.label}" + (f": {self.detail}" if self.detail else "")


class BL4Backend:
    """
    Implements `casino.Backend` against the real game.

    Players are identified by their player controller. On the host, every player in the session has
    one, which is what lets the host's casino charge and pay co-op partners.
    """

    def __init__(
        self, slot_machines: Callable[[], Iterable[tuple[float, float, float]]] | None = None
    ) -> None:
        """
        Args:
            slot_machines: Gets where the mod's own slot machines stand, which count as machines too.
        """
        self.slot_machines = slot_machines
        self._currency_struct: UObject | None = None
        self._experience_struct: UObject | None = None
        self._item_pool_store_ptr: WeakPointer = WeakPointer()

    # ==============================================================================================
    # Lookups

    def _currency_def(self, currency: Currency) -> FGbxDefPtr:
        if self._currency_struct is None:
            self._currency_struct = _find_script_struct(CURRENCY_DEF_PATHS, "GbxCurrencyDef")
        if self._currency_struct is None:
            raise RuntimeError("Couldn't find the GbxCurrencyDef struct")
        return FGbxDefPtr(currency.value, self._currency_struct)

    def _experience_def(self, token: str) -> FGbxDefPtr:
        if self._experience_struct is None:
            self._experience_struct = _find_script_struct(EXPERIENCE_DEF_PATHS, "GbxExperienceDef")
        if self._experience_struct is None:
            raise RuntimeError("Couldn't find the GbxExperienceDef struct")
        return FGbxDefPtr(token, self._experience_struct)

    @staticmethod
    def _currency_library() -> UObject:
        return unrealsdk.find_class(CURRENCY_LIBRARY_CLASS).ClassDefaultObject

    def _item_pool_store(self) -> UObject:
        store = self._item_pool_store_ptr()
        if store is None:
            stores = list(unrealsdk.find_all(ITEM_POOL_STORE_CLASS, exact=False))
            if not stores:
                raise RuntimeError(f"Couldn't find a {ITEM_POOL_STORE_CLASS}")
            # Prefer a live instance, but the config store may only exist as its class default
            live = [store for store in stores if not is_template(store)]
            store = (live or stores)[-1]
            self._item_pool_store_ptr = WeakPointer(store)
        return store

    def currency_rows(self, player: UObject) -> dict[str, int]:
        """Reads every currency a player holds, keyed by the game's own token names."""
        rows: dict[str, int] = {}
        for row in player.CurrencyManager.currencies:
            name = def_name(row.type)
            if name is not None:
                rows[name] = int(row.Amount)
        return rows

    # ==============================================================================================
    # casino.Backend

    def player_key(self, player: UObject) -> str:
        return str(player._get_address())

    def player_name(self, player: UObject) -> str:
        try:
            return str(player.PlayerState.GetPlayerName())
        except Exception:  # noqa: BLE001 - only used for logging
            return "A player"

    def check_can_play(self, player: UObject) -> str | None:
        if player is None or player.Pawn is None:
            return "Load into the game first."
        return None

    def is_near_machine(self, player: UObject, radius: float) -> bool:
        pawn = player.Pawn
        if pawn is None:
            return False
        here = pawn.K2_GetActorLocation()
        max_dist_sq = radius * radius

        if self.slot_machines is not None:
            for x, y, z in self.slot_machines():
                if (here.X - x) ** 2 + (here.Y - y) ** 2 + (here.Z - z) ** 2 <= max_dist_sq:
                    return True

        for machine in unrealsdk.find_all(VENDING_MACHINE_CLASS, exact=False):
            if is_template(machine):
                continue
            try:
                # Other mods sometimes spawn hidden machines as helpers - ignore those
                if machine.bHidden:
                    continue
                there = machine.K2_GetActorLocation()
            except Exception:  # noqa: BLE001 - half-loaded actor, skip it
                continue
            dist_sq = (here.X - there.X) ** 2 + (here.Y - there.Y) ** 2 + (here.Z - there.Z) ** 2
            if dist_sq <= max_dist_sq:
                return True
        return False

    def player_level(self, player: UObject) -> int | None:
        try:
            xp = self._experience_def(CHARACTER_EXPERIENCE)
            return int(player.PlayerState.BP_GetExperienceLevel(xp))
        except Exception as ex:  # noqa: BLE001
            logging.warning(f"[Borderlands Gamble] Couldn't read player level: {ex!r}")
            return None

    def get_balance(self, player: UObject, currency: Currency) -> int | None:
        try:
            rows = self.currency_rows(player)
        except Exception as ex:  # noqa: BLE001
            logging.warning(f"[Borderlands Gamble] Couldn't read currencies: {ex!r}")
            return None
        wanted = currency.value.lower()
        for name, amount in rows.items():
            if name.lower() == wanted:
                return amount
        return None

    def add_currency(self, player: UObject, currency: Currency, amount: int) -> None:
        give = self._currency_library().GiveCurrency
        ptr = self._currency_def(currency)
        try:
            give(player, ptr, amount)
        except TypeError:
            # Some builds want the currency manager as the context object instead
            give(player.CurrencyManager, ptr, amount)

    def spawn_item(self, player: UObject, pool: str, level: int, index: int, count: int) -> None:
        pawn = player.Pawn
        world = ENGINE.GameViewport.World
        if pawn is None or world is None:
            raise RuntimeError("Player isn't in the world")

        loc = pawn.K2_GetActorLocation()
        yaw = float(pawn.K2_GetActorRotation().Yaw)
        x, y, z = loot.drop_position((loc.X, loc.Y, loc.Z), yaw, index, count)

        transform = pawn.K2_GetActorTransform()
        transform.Translation = unrealsdk.make_struct("Vector", X=x, Y=y, Z=z)
        self._item_pool_store().SpawnInventoryFromItemPool(world, transform, level, pool)

    # ==============================================================================================
    # Diagnostics

    def diagnose(self) -> list[Check]:
        """Checks every game API the mod relies on, without changing anything."""
        checks: list[Check] = []

        def attempt(label: str, func: Callable[[], Any]) -> Any:
            try:
                value = func()
            except Exception as ex:  # noqa: BLE001
                checks.append(Check(False, label, repr(ex)))
                return None
            checks.append(Check(value is not None and value is not False, label, _short(value)))
            return value

        pc = attempt("Player controller", local_player)
        if pc is None:
            return checks
        attempt("Player pawn", lambda: pc.Pawn)
        checks.append(Check(True, "Role", "host / single player" if is_host() else "co-op client"))
        attempt("GbxCurrencyDef struct", lambda: self._currency_def(Currency.CASH)._type)
        attempt("GbxCurrencyFunctionLibrary.GiveCurrency", lambda: self._currency_library().GiveCurrency)
        rows = attempt("Currency rows", lambda: self.currency_rows(pc))
        for currency in Currency:
            attempt(
                f"{currency.name.title()} balance",
                lambda currency=currency: self.get_balance(pc, currency),
            )
        if rows:
            checks.append(Check(True, "Currency tokens", ", ".join(sorted(rows))))
        attempt("Player level", lambda: self.player_level(pc))
        attempt("Item pool store", self._item_pool_store)
        attempt("World", lambda: ENGINE.GameViewport.World)
        attempt(
            "Vending machines loaded",
            lambda: sum(
                1 for m in unrealsdk.find_all(VENDING_MACHINE_CLASS, exact=False) if not is_template(m)
            ),
        )
        for path in (
            "/Script/UMG.UserWidget",
            "/Script/UMG.TextBlock",
            "/Script/UMG.CanvasPanel",
            "/Script/UMG.Button",
            "/Script/Engine.StaticMeshActor",
            "/Script/Engine.SkeletalMeshActor",
            "/Script/Engine.TextRenderActor",
        ):
            attempt(f"Class {path}", lambda path=path: unrealsdk.find_object("Class", path))
        for name in ("GameplayStatics", "WidgetBlueprintLibrary", "KismetSystemLibrary"):
            attempt(f"Library {name}", lambda name=name: unrealsdk.find_class(name).ClassDefaultObject)
        for path in ("/Script/Engine.CameraModifier:BlueprintModifyCamera", SERVER_RPC, CLIENT_RPC):
            attempt(f"Function {path}", lambda path=path: unrealsdk.find_object("Function", path))
        return checks

    def wallet_test(self) -> list[Check]:
        """Takes $1 and gives it back, checking the balance moves both times."""
        pc = local_player()
        if pc is None:
            return [Check(False, "Wallet test", "load into the game first")]
        if not is_host():
            return [Check(False, "Wallet test", "only the host's wallet can be changed")]
        before = self.get_balance(pc, Currency.CASH)
        if before is None or before < 1:
            return [Check(False, "Wallet test", "need a readable balance of at least $1")]

        self.add_currency(pc, Currency.CASH, -1)
        taken = self.get_balance(pc, Currency.CASH)
        checks = [Check(taken == before - 1, "Charge $1", f"{before} -> {taken}")]
        if taken is None or taken >= before:
            checks.append(Check(False, "Refund $1", "skipped, since the charge didn't apply"))
            return checks

        self.add_currency(pc, Currency.CASH, before - taken)
        after = self.get_balance(pc, Currency.CASH)
        checks.append(Check(after == before, "Refund $1", f"{taken} -> {after}"))
        return checks


# ==================================================================================================
# Co-op transport


def _message_arg(args: WrappedStruct, name: str) -> str:
    """Reads a hooked function's string argument, falling back to the first string argument."""
    try:
        return str(getattr(args, name))
    except AttributeError:
        pass
    for prop in args._type._properties():
        value = args._get_field(prop)
        if isinstance(value, str):
            return value
    return ""


class CoopChannel:
    """
    Sends co-op messages between players, and hands incoming ones to the mod.

    Outgoing calls skip our own hooks. Incoming calls are recognised by our message prefix, handled,
    and blocked, so the game never runs its normal ServerExec/ClientMessage logic on them.
    """

    def __init__(
        self,
        on_host_message: Callable[[UObject, str], None],
        on_client_message: Callable[[str], None],
    ) -> None:
        self.on_host_message = on_host_message
        self.on_client_message = on_client_message

    def enable(self) -> None:
        add_hook(SERVER_RPC, Type.PRE, COOP_HOOK_ID, self._server_hook)
        add_hook(CLIENT_RPC, Type.PRE, COOP_HOOK_ID, self._client_hook)

    def disable(self) -> None:
        remove_hook(SERVER_RPC, Type.PRE, COOP_HOOK_ID)
        remove_hook(CLIENT_RPC, Type.PRE, COOP_HOOK_ID)

    def send_to_host(self, text: str) -> None:
        pc = local_player()
        if pc is None:
            raise RuntimeError("No player controller to send from")
        with prevent_hooking_direct_calls():
            pc.ServerExec(text)

    def send_to_client(self, player: UObject, text: str) -> None:
        with prevent_hooking_direct_calls():
            player.ClientMessage(text, "None", 0.0)

    def _server_hook(
        self,
        obj: UObject,
        args: WrappedStruct,
        _ret: Any,
        _func: BoundFunction,
    ) -> type[Block] | None:
        # Runs on the host, where `obj` is the controller of the player who sent the message
        text = _message_arg(args, "Msg")
        if not protocol.is_ours(text):
            return None
        try:
            self.on_host_message(obj, text)
        except Exception as ex:  # noqa: BLE001 - a bad message must never break the host's game
            logging.error(f"[Borderlands Gamble] Couldn't handle a co-op message: {ex!r}")
        return Block

    def _client_hook(
        self,
        obj: UObject,
        args: WrappedStruct,
        _ret: Any,
        _func: BoundFunction,
    ) -> type[Block] | None:
        text = _message_arg(args, "S")
        if not protocol.is_ours(text):
            return None
        try:
            if not obj.IsLocalController():
                # The host sending a message out, not one arriving for us
                return None
            self.on_client_message(text)
        except Exception as ex:  # noqa: BLE001
            logging.error(f"[Borderlands Gamble] Couldn't handle a co-op message: {ex!r}")
        return Block


def _short(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "..."
