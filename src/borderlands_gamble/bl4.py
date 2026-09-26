"""
The Borderlands 4 side of the mod: everything that talks to the game through unrealsdk.

Every game API used here is one that other published BL4 SDK mods already rely on. See
`docs/game-api.md` for where each one comes from, and how to poke at it from the console when a
game patch breaks something.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import unrealsdk
from mods_base import ENGINE, get_pc
from unrealsdk import logging
from unrealsdk.unreal import FGbxDefPtr, UObject, WeakPointer

from . import loot
from .slots import Currency

CURRENCY_DEF_PATHS = ("/Script/GbxGame.GbxCurrencyDef", "/Script/OakGame.GbxCurrencyDef")
EXPERIENCE_DEF_PATHS = ("/Script/GbxGame.GbxExperienceDef", "/Script/OakGame.GbxExperienceDef")
CHARACTER_EXPERIENCE = "Character"

CURRENCY_LIBRARY_CLASS = "GbxCurrencyFunctionLibrary"
ITEM_POOL_STORE_CLASS = "NexusConfigStoreItemPool"
VENDING_MACHINE_CLASS = "OakVendingMachine"


def _is_template(obj: UObject) -> bool:
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


@dataclass
class Check:
    ok: bool
    label: str
    detail: str = ""

    def __str__(self) -> str:
        return f"[{'OK' if self.ok else '!!'}] {self.label}" + (f": {self.detail}" if self.detail else "")


class BL4Backend:
    """Implements `casino.Backend` against the real game."""

    def __init__(self) -> None:
        self._currency_struct: UObject | None = None
        self._experience_struct: UObject | None = None
        self._item_pool_store_ptr: WeakPointer = WeakPointer()

    # ==============================================================================================
    # Lookups

    @staticmethod
    def _pc() -> UObject | None:
        try:
            return get_pc(possibly_loading=True)
        except (IndexError, AttributeError):
            # No local player yet, e.g. mid load
            return None

    def _pawn(self) -> UObject | None:
        pc = self._pc()
        return None if pc is None else pc.Pawn

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
            live = [store for store in stores if not _is_template(store)]
            store = (live or stores)[-1]
            self._item_pool_store_ptr = WeakPointer(store)
        return store

    def currency_rows(self) -> dict[str, int]:
        """Reads every currency the player holds, keyed by the game's own token names."""
        pc = self._pc()
        if pc is None:
            return {}
        rows: dict[str, int] = {}
        for row in pc.CurrencyManager.currencies:
            name = def_name(row.type)
            if name is not None:
                rows[name] = int(row.Amount)
        return rows

    # ==============================================================================================
    # casino.Backend

    def check_can_play(self) -> str | None:
        pc = self._pc()
        if pc is None or pc.Pawn is None:
            return "Load into the game first."
        try:
            if not pc.HasAuthority():
                return "Slots are host-only in co-op."
        except Exception as ex:  # noqa: BLE001 - if we can't tell, let them try
            logging.dev_warning(f"[Borderlands Gamble] HasAuthority failed: {ex!r}")
        return None

    def is_near_machine(self, radius: float) -> bool:
        pawn = self._pawn()
        if pawn is None:
            return False
        here = pawn.K2_GetActorLocation()
        max_dist_sq = radius * radius

        for machine in unrealsdk.find_all(VENDING_MACHINE_CLASS, exact=False):
            if _is_template(machine):
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

    def player_level(self) -> int | None:
        pc = self._pc()
        if pc is None:
            return None
        try:
            return int(pc.PlayerState.BP_GetExperienceLevel(self._experience_def(CHARACTER_EXPERIENCE)))
        except Exception as ex:  # noqa: BLE001
            logging.warning(f"[Borderlands Gamble] Couldn't read player level: {ex!r}")
            return None

    def get_balance(self, currency: Currency) -> int | None:
        try:
            rows = self.currency_rows()
        except Exception as ex:  # noqa: BLE001
            logging.warning(f"[Borderlands Gamble] Couldn't read currencies: {ex!r}")
            return None
        wanted = currency.value.lower()
        for name, amount in rows.items():
            if name.lower() == wanted:
                return amount
        return None

    def add_currency(self, currency: Currency, amount: int) -> None:
        pc = self._pc()
        if pc is None:
            raise RuntimeError("No player controller")
        give = self._currency_library().GiveCurrency
        ptr = self._currency_def(currency)
        try:
            give(pc, ptr, amount)
        except TypeError:
            # Some builds want the currency manager as the context object instead
            give(pc.CurrencyManager, ptr, amount)

    def spawn_item(self, pool: str, level: int, index: int, count: int) -> None:
        pawn = self._pawn()
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

        def attempt(label: str, func: Any) -> Any:
            try:
                value = func()
            except Exception as ex:  # noqa: BLE001
                checks.append(Check(False, label, repr(ex)))
                return None
            checks.append(Check(value is not None and value is not False, label, _short(value)))
            return value

        pc = attempt("Player controller", self._pc)
        if pc is None:
            return checks
        attempt("Player pawn", lambda: pc.Pawn)
        attempt("Host / single player (HasAuthority)", pc.HasAuthority)
        attempt("GbxCurrencyDef struct", lambda: self._currency_def(Currency.CASH)._type)
        attempt("GbxCurrencyFunctionLibrary.GiveCurrency", lambda: self._currency_library().GiveCurrency)
        rows = attempt("Currency rows", self.currency_rows)
        for currency in Currency:
            attempt(
                f"{currency.name.title()} balance",
                lambda currency=currency: self.get_balance(currency),
            )
        if rows:
            checks.append(Check(True, "Currency tokens", ", ".join(sorted(rows))))
        attempt("Player level", self.player_level)
        attempt("Item pool store", self._item_pool_store)
        attempt("World", lambda: ENGINE.GameViewport.World)
        attempt(
            "Vending machines loaded",
            lambda: sum(
                1 for m in unrealsdk.find_all(VENDING_MACHINE_CLASS, exact=False) if not _is_template(m)
            ),
        )
        for path in ("/Script/UMG.UserWidget", "/Script/UMG.TextBlock", "/Script/UMG.CanvasPanel"):
            attempt(f"Class {path}", lambda path=path: unrealsdk.find_object("Class", path))
        attempt(
            "Camera tick function",
            lambda: unrealsdk.find_object("Function", "/Script/Engine.CameraModifier:BlueprintModifyCamera"),
        )
        return checks

    def wallet_test(self) -> list[Check]:
        """Takes $1 and gives it back, checking the balance moves both times."""
        before = self.get_balance(Currency.CASH)
        if before is None or before < 1:
            return [Check(False, "Wallet test", "need a readable balance of at least $1")]

        self.add_currency(Currency.CASH, -1)
        taken = self.get_balance(Currency.CASH)
        checks = [Check(taken == before - 1, "Charge $1", f"{before} -> {taken}")]
        if taken is None or taken >= before:
            checks.append(Check(False, "Refund $1", "skipped, since the charge didn't apply"))
            return checks

        self.add_currency(Currency.CASH, before - taken)
        after = self.get_balance(Currency.CASH)
        checks.append(Check(after == before, "Refund $1", f"{taken} -> {after}"))
        return checks


def _short(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "..."
