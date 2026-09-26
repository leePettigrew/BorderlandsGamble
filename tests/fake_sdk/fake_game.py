"""
A pocket-sized simulation of the parts of Borderlands 4 the mod touches.

Objects only expose the fields and functions the real game has (as used by other published BL4 SDK
mods), so calling anything else raises AttributeError - the same as the real SDK would.
"""

from __future__ import annotations

from typing import Any

from unrealsdk.unreal import FGbxDefPtr, UClass, UObject, UScriptStruct, WrappedStruct


def vector(x: float, y: float, z: float) -> WrappedStruct:
    return WrappedStruct("Vector", X=x, Y=y, Z=z)


class Prop:
    def __init__(self, name: str, *children: str) -> None:
        self.name = name
        self.PropertyClass = PropClass(children)
        self.Inner = self


class PropClass:
    def __init__(self, children: tuple[str, ...]) -> None:
        self.children = children

    def _find_prop(self, name: str) -> Prop:
        return Prop(name, "PlayerController")


class FakeObject(UObject):
    def _get_field(self, prop: Prop) -> Any:
        return getattr(self, prop.name)


# ==================================================================================================
# Engine + player


class Engine(FakeObject):
    Name = "OakGameEngine_2147482611"

    def __init__(self, game: Game) -> None:
        self.Class = PropClass(("GameInstance",))
        self.GameInstance = GameInstance(game)
        self.GameViewport = GameViewport()


class GameInstance(FakeObject):
    def __init__(self, game: Game) -> None:
        self.LocalPlayers = [LocalPlayer(game)]


class LocalPlayer(FakeObject):
    def __init__(self, game: Game) -> None:
        self.game = game

    @property
    def PlayerController(self) -> PlayerController:
        return self.game.pc


class GameViewport(FakeObject):
    def __init__(self) -> None:
        self.World = World()


class World(FakeObject):
    Name = "World_Kairos"


class CurrencyRow(FakeObject):
    def __init__(self, token: str, amount: int) -> None:
        self.type = FGbxDefPtr(token, "GbxCurrencyDef")
        self.Amount = amount


class CurrencyManager(FakeObject):
    def __init__(self, balances: dict[str, int]) -> None:
        self.currencies = [CurrencyRow(token, amount) for token, amount in balances.items()]

    def row(self, token: str) -> CurrencyRow:
        return next(row for row in self.currencies if row.type._name == token)


class PlayerState(FakeObject):
    def __init__(self, name: str, level: int) -> None:
        self.name = name
        self.level = level

    def GetPlayerName(self) -> str:
        return self.name

    def BP_GetExperienceLevel(self, ptr: FGbxDefPtr) -> int:
        if ptr._name != "Character":
            raise ValueError(f"Unknown experience type {ptr._name}")
        return self.level


class Pawn(FakeObject):
    def __init__(self, location: tuple[float, float, float]) -> None:
        self.location = location
        self.yaw = 90.0

    def K2_GetActorLocation(self) -> WrappedStruct:
        return vector(*self.location)

    def K2_GetActorRotation(self) -> WrappedStruct:
        return WrappedStruct("Rotator", Pitch=0.0, Yaw=self.yaw, Roll=0.0)

    def K2_GetActorTransform(self) -> WrappedStruct:
        return WrappedStruct(
            "Transform",
            Translation=vector(*self.location),
            Rotation=WrappedStruct("Quat", X=0.0, Y=0.0, Z=0.0, W=1.0),
            Scale3D=vector(1.0, 1.0, 1.0),
        )


class PlayerController(FakeObject):
    def __init__(
        self,
        name: str,
        level: int,
        balances: dict[str, int],
        location: tuple[float, float, float],
        *,
        local: bool,
    ) -> None:
        self.Name = f"OakPlayerController_{name}"
        self.Pawn = Pawn(location)
        self.PlayerState = PlayerState(name, level)
        self.CurrencyManager = CurrencyManager(balances)
        self.local = local
        self.authority = True
        # Network calls made on this controller, as (function, text)
        self.sent: list[tuple[str, str]] = []

    def HasAuthority(self) -> bool:
        return self.authority

    def IsLocalController(self) -> bool:
        return self.local

    def ServerExec(self, msg: str) -> None:
        if not isinstance(msg, str):
            raise TypeError("ServerExec takes a string")
        self.sent.append(("ServerExec", msg))

    def ClientMessage(self, s: str, msg_type: str, lifetime: float) -> None:
        if not isinstance(s, str) or not isinstance(msg_type, str):
            raise TypeError("ClientMessage takes (string, name, float)")
        self.sent.append(("ClientMessage", s))

    def cash(self) -> int:
        return self.CurrencyManager.row("Cash").Amount


# ==================================================================================================
# Game systems


class CurrencyLibrary(FakeObject):
    Name = "Default__GbxCurrencyFunctionLibrary"

    def __init__(self, game: Game) -> None:
        self.game = game

    def GiveCurrency(self, context: Any, ptr: FGbxDefPtr, amount: int) -> None:
        if context not in (self.game.pc, self.game.friend):
            raise TypeError("Expected a player controller as context")
        if not isinstance(ptr._type, UScriptStruct) or ptr._type.Name != "GbxCurrencyDef":
            raise TypeError("Expected a GbxCurrencyDef pointer")
        self.game.give_calls.append((context.PlayerState.name, ptr._name, amount))
        row = context.CurrencyManager.row(ptr._name)
        row.Amount = max(0, min(row.Amount + amount, 2_147_483_647))


class ItemPoolStore(FakeObject):
    def __init__(self, game: Game, name: str) -> None:
        self.game = game
        self.Name = name

    def SpawnInventoryFromItemPool(self, world: World, transform: Any, level: int, pool: str) -> None:
        if not isinstance(world, World):
            raise TypeError("Expected a world")
        t = transform.Translation
        self.game.spawned.append((pool, level, (t.X, t.Y, t.Z)))


class VendingMachine(FakeObject):
    def __init__(self, name: str, location: tuple[float, float, float], hidden: bool = False) -> None:
        self.Name = name
        self.location = location
        self.bHidden = hidden

    def K2_GetActorLocation(self) -> WrappedStruct:
        return vector(*self.location)


class ClassDefault(UClass):
    def __init__(self, name: str, cdo: Any) -> None:
        self.Name = name
        self.ClassDefaultObject = cdo


# ==================================================================================================
# UMG


class Slot(FakeObject):
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def SetPosition(self, value: Any) -> None:
        self.calls.append(("SetPosition", value))

    def SetSize(self, value: Any) -> None:
        self.calls.append(("SetSize", value))

    def SetAutoSize(self, value: bool) -> None:
        self.calls.append(("SetAutoSize", value))

    def SetAlignment(self, value: Any) -> None:
        self.calls.append(("SetAlignment", value))

    def SetZOrder(self, value: int) -> None:
        self.calls.append(("SetZOrder", value))


class Widget(FakeObject):
    def __init__(self, cls_name: str, outer: Any) -> None:
        self.Name = cls_name
        self.outer = outer
        self.Slot: Slot | None = None
        self.visibility: int | None = None
        self.calls: list[tuple[str, Any]] = []

    def SetVisibility(self, value: int) -> None:
        self.visibility = value

    def SetRenderScale(self, value: Any) -> None:
        self.calls.append(("SetRenderScale", value))

    def SetRenderTransformPivot(self, value: Any) -> None:
        self.calls.append(("SetRenderTransformPivot", value))

    def RemoveFromParent(self) -> None:
        self.calls.append(("RemoveFromParent", None))


class UserWidget(Widget):
    def __init__(self, cls_name: str, outer: Any) -> None:
        super().__init__(cls_name, outer)
        self.WidgetTree: WidgetTree | None = None
        self.in_viewport = False

    def AddToViewport(self, z: int) -> None:
        self.in_viewport = True

    def RemoveFromParent(self) -> None:
        self.in_viewport = False

    def SetAlignmentInViewport(self, value: Any) -> None:
        self.calls.append(("SetAlignmentInViewport", value))

    def SetPositionInViewport(self, value: Any, remove_dpi_scale: bool) -> None:
        self.calls.append(("SetPositionInViewport", value))

    def SetDesiredSizeInViewport(self, value: Any) -> None:
        self.calls.append(("SetDesiredSizeInViewport", value))


class WidgetTree(FakeObject):
    def __init__(self, cls_name: str, outer: Any) -> None:
        self.Name = cls_name
        self.RootWidget: Widget | None = None


class CanvasPanel(Widget):
    def __init__(self, cls_name: str, outer: Any) -> None:
        super().__init__(cls_name, outer)
        self.children: list[Widget] = []

    def AddChild(self, child: Widget) -> Slot:
        child.Slot = Slot()
        self.children.append(child)
        return child.Slot


class Border(Widget):
    def SetBrushColor(self, value: Any) -> None:
        self.calls.append(("SetBrushColor", value))


class TextBlock(Widget):
    def __init__(self, cls_name: str, outer: Any) -> None:
        super().__init__(cls_name, outer)
        self.text = ""
        self.texts: list[str] = []

    def SetText(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("SetText takes text")
        self.text = value
        self.texts.append(value)

    def SetColorAndOpacity(self, value: Any) -> None:
        self.calls.append(("SetColorAndOpacity", value))

    def SetJustification(self, value: int) -> None:
        self.calls.append(("SetJustification", value))

    def SetShadowOffset(self, value: Any) -> None:
        self.calls.append(("SetShadowOffset", value))

    def SetShadowColorAndOpacity(self, value: Any) -> None:
        self.calls.append(("SetShadowColorAndOpacity", value))


class WidgetLayoutLibrary(FakeObject):
    def GetViewportSize(self, context: Any) -> WrappedStruct:
        return WrappedStruct("Vector2D", X=2560.0, Y=1440.0)

    def GetViewportScale(self, context: Any) -> float:
        return 1.3333


WIDGET_CLASSES: dict[str, type] = {
    "/Script/UMG.UserWidget": UserWidget,
    "/Script/UMG.WidgetTree": WidgetTree,
    "/Script/UMG.CanvasPanel": CanvasPanel,
    "/Script/UMG.Border": Border,
    "/Script/UMG.TextBlock": TextBlock,
}


# ==================================================================================================


class Game:
    def __init__(self) -> None:
        self.engine = Engine(self)
        balances = {"Cash": 1_000_000, "eridium": 500}
        # The local player, and a co-op partner who exists on the host as a remote controller
        self.pc = PlayerController("Moze", 50, dict(balances), (1000.0, 2000.0, 300.0), local=True)
        self.friend = PlayerController("Zane", 20, dict(balances), (1300.0, 2000.0, 300.0), local=False)
        self.give_calls: list[tuple[str, str, int]] = []
        self.spawned: list[tuple[str, int, tuple[float, float, float]]] = []
        self.machines = [
            VendingMachine("Default__OakVendingMachine", (1000.0, 2000.0, 300.0)),
            VendingMachine("VendingMachine_Guns_1", (1200.0, 2100.0, 300.0)),
            VendingMachine("HiddenHelperMachine", (1000.0, 2000.0, 300.0), hidden=True),
        ]
        self.item_stores = [
            ItemPoolStore(self, "Default__NexusConfigStoreItemPool"),
            ItemPoolStore(self, "NexusConfigStoreItemPool_0"),
        ]
        self.widgets: list[Widget | WidgetTree] = []
        self.classes = {
            "GbxCurrencyFunctionLibrary": ClassDefault("GbxCurrencyFunctionLibrary", CurrencyLibrary(self)),
        }
        self.structs = {
            "/Script/GbxGame.GbxCurrencyDef": UScriptStruct("GbxCurrencyDef"),
            "/Script/GbxGame.GbxExperienceDef": UScriptStruct("GbxExperienceDef"),
        }

    def find_object(self, cls: str, name: str) -> Any:
        if cls == "OakGameEngine" and name == "/Engine/Transient.OakGameEngine_2147482611":
            return self.engine
        if cls == "ScriptStruct" and name in self.structs:
            return self.structs[name]
        if cls == "Class" and name in WIDGET_CLASSES:
            return ClassDefault(name, None)
        if cls == "Class" and name == "/Script/UMG.WidgetLayoutLibrary":
            return ClassDefault(name, WidgetLayoutLibrary())
        if cls == "Function" and name in (
            "/Script/Engine.CameraModifier:BlueprintModifyCamera",
            "/Script/Engine.PlayerController:ServerExec",
            "/Script/Engine.PlayerController:ClientMessage",
        ):
            return FakeObject()
        raise ValueError(f"Couldn't find {cls} '{name}'")

    def find_all(self, cls: str, exact: bool) -> list[Any]:
        match cls:
            case "OakVendingMachine":
                return list(self.machines)
            case "NexusConfigStoreItemPool":
                return list(self.item_stores)
            case "ScriptStruct":
                return list(self.structs.values())
            case _:
                return []

    def find_class(self, name: str) -> Any:
        if name in self.classes:
            return self.classes[name]
        raise ValueError(f"Couldn't find class {name}")

    def construct(self, cls: Any, outer: Any) -> Any:
        path = cls if isinstance(cls, str) else cls.Name
        widget = WIDGET_CLASSES[path](path, outer)
        self.widgets.append(widget)
        return widget

    def text_blocks(self) -> list[TextBlock]:
        return [w for w in self.widgets if isinstance(w, TextBlock)]

    def cash(self) -> int:
        return self.pc.cash()

    def collect_widgets(self) -> None:
        """Simulates a map change garbage collecting every widget."""
        for widget in self.widgets:
            widget._collected = True  # type: ignore[attr-defined]


GAME = Game()
