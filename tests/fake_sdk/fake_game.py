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

    def __init__(self) -> None:
        self.GameState: GameState | None = None


class GameState(FakeObject):
    def __init__(self, players: list[PlayerState]) -> None:
        self.PlayerArray = players


def rotator(pitch: float, yaw: float, roll: float = 0.0) -> WrappedStruct:
    return WrappedStruct("Rotator", Pitch=pitch, Yaw=yaw, Roll=roll)


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
    def __init__(self, name: str, level: int, player_id: int, controller: PlayerController) -> None:
        self.name = name
        self.level = level
        self.player_id = player_id
        self.controller = controller

    def GetPlayerName(self) -> str:
        return self.name

    def GetPlayerId(self) -> int:
        return self.player_id

    @property
    def PawnPrivate(self) -> Pawn | None:
        return self.controller.Pawn

    def GetPlayerController(self) -> PlayerController:
        return self.controller

    def BP_GetExperienceLevel(self, ptr: FGbxDefPtr) -> int:
        if ptr._name != "Character":
            raise ValueError(f"Unknown experience type {ptr._name}")
        return self.level


class Capsule(FakeObject):
    def GetScaledCapsuleHalfHeight(self) -> float:
        return 90.0


class Pawn(FakeObject):
    def __init__(self, location: tuple[float, float, float]) -> None:
        self.location = location
        self.yaw = 90.0
        self.CapsuleComponent = Capsule()

    def K2_GetActorLocation(self) -> WrappedStruct:
        return vector(*self.location)

    def K2_GetActorRotation(self) -> WrappedStruct:
        return WrappedStruct("Rotator", Pitch=0.0, Yaw=self.yaw, Roll=0.0)


class CameraManager(FakeObject):
    """The camera sits at the pawn's eyes, looking wherever `view` says (pitch, yaw)."""

    EYE_HEIGHT = 70.0

    def __init__(self, pc: PlayerController) -> None:
        self.pc = pc
        self.view = (0.0, 90.0)

    def GetCameraLocation(self) -> WrappedStruct:
        x, y, z = self.pc.Pawn.location
        return vector(x, y, z + self.EYE_HEIGHT)

    def GetCameraRotation(self) -> WrappedStruct:
        return rotator(*self.view)

    def look_at(self, x: float, y: float, z: float) -> None:
        import math

        eye = self.GetCameraLocation()
        dx, dy, dz = x - eye.X, y - eye.Y, z - eye.Z
        self.view = (math.degrees(math.atan2(dz, math.hypot(dx, dy))), math.degrees(math.atan2(dy, dx)))


class PlayerController(FakeObject):
    def __init__(
        self,
        name: str,
        level: int,
        balances: dict[str, int],
        location: tuple[float, float, float],
        *,
        local: bool,
        player_id: int,
    ) -> None:
        self.Name = f"OakPlayerController_{name}"
        self.Pawn: Pawn | None = Pawn(location)
        self.PlayerState = PlayerState(name, level, player_id, self)
        self.CurrencyManager = CurrencyManager(balances)
        self.local = local
        self.authority = True
        # Network calls made on this controller, as (function, text)
        self.sent: list[tuple[str, str]] = []
        self.PlayerCameraManager = CameraManager(self)
        self.bShowMouseCursor = False
        self.bEnableMouseOverEvents = False
        self.bBlockInput = False
        self.ignore_look = 0
        self.ignore_move = 0
        # Input state, set by the test
        self.keys_down: set[str] = set()
        self.mouse: tuple[float, float] | None = None

    def ProjectWorldLocationToScreen(self, location: Any, screen: Any, relative: bool) -> tuple[bool, Any]:
        """A simple pinhole camera: 1 pixel per unit sideways at 1 m away, centred on a 2560x1440 screen."""
        import math

        eye = self.PlayerCameraManager.GetCameraLocation()
        pitch, yaw = (math.radians(a) for a in self.PlayerCameraManager.view)
        fx, fy, fz = math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch)
        dx, dy, dz = location.X - eye.X, location.Y - eye.Y, location.Z - eye.Z
        ahead = dx * fx + dy * fy + dz * fz
        if ahead <= 1.0:
            return False, WrappedStruct("Vector2D", X=0.0, Y=0.0)
        right = -dx * math.sin(yaw) + dy * math.cos(yaw)
        up = dz - ahead * fz
        return True, WrappedStruct("Vector2D", X=1280.0 + right * 100 / ahead, Y=720.0 - up * 100 / ahead)

    def IsInputKeyDown(self, key: WrappedStruct) -> bool:
        if key._struct_name != "Key":
            raise TypeError("IsInputKeyDown takes an FKey")
        return key.KeyName in self.keys_down

    def GetMousePosition(self, x: float, y: float) -> tuple[bool, float, float]:
        if self.mouse is None:
            return False, 0.0, 0.0
        return True, *self.mouse

    def SetIgnoreLookInput(self, value: bool) -> None:
        self.ignore_look = max(0, self.ignore_look + (1 if value else -1))

    def SetIgnoreMoveInput(self, value: bool) -> None:
        self.ignore_move = max(0, self.ignore_move + (1 if value else -1))

    def ResetIgnoreLookInput(self) -> None:
        self.ignore_look = 0

    def ResetIgnoreMoveInput(self) -> None:
        self.ignore_move = 0

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
        if amount < 0 and not self.game.negative_gives:
            return
        row = context.CurrencyManager.row(ptr._name)
        row.Amount = max(0, min(row.Amount + amount, 2_147_483_647))


class ItemPoolStore(FakeObject):
    def __init__(self, game: Game, name: str) -> None:
        self.game = game
        self.Name = name

    def SpawnInventoryFromItemPool(self, world: World, transform: Any, level: int, pool: str) -> None:
        if not isinstance(world, World):
            raise TypeError("Expected a world")
        q = transform.Rotation
        if transform.Scale3D.Z != 1.0 or abs(q.X**2 + q.Y**2 + q.Z**2 + q.W**2 - 1.0) > 1e-6:
            raise ValueError("Expected a transform with a proper rotation and scale")
        t = transform.Translation
        self.game.spawned.append((pool, level, (t.X, t.Y, t.Z)))


class MeshAsset(FakeObject):
    def __init__(self, name: str) -> None:
        self.Name = name


class MeshComponent(FakeObject):
    """A static or skeletal mesh component, either on a vending machine or on one of our copies."""

    def __init__(
        self,
        kind: str,
        mesh: MeshAsset | None = None,
        location: tuple[float, float, float] = (0.0, 0.0, 0.0),
        yaw: float = 0.0,
        materials: int = 2,
    ) -> None:
        self.kind = kind
        self.mesh = mesh
        self.location = location
        self.yaw = yaw
        self.bHiddenInGame = False
        self.visible = True
        # Static meshes start out static, like on a StaticMeshActor
        self.mobility = 0
        self.materials: dict[int, Any] = {i: FakeObject() for i in range(materials)} if mesh else {}

    @property
    def StaticMesh(self) -> MeshAsset | None:
        if self.kind != "static":
            raise AttributeError("StaticMesh")
        return self.mesh

    @property
    def SkeletalMesh(self) -> MeshAsset | None:
        if self.kind != "skeletal":
            raise AttributeError("SkeletalMesh")
        return self.mesh

    def GetStaticMesh(self) -> MeshAsset | None:
        return self.StaticMesh

    def SetMobility(self, value: int) -> None:
        self.mobility = value

    def SetStaticMesh(self, mesh: MeshAsset) -> bool:
        if self.kind != "static":
            raise AttributeError("SetStaticMesh")
        if self.mesh is not None and self.mobility != 2:
            # Like Unreal: a static component's mesh can't change once set
            return False
        self.mesh = mesh
        return True

    def SetSkeletalMeshAsset(self, mesh: MeshAsset) -> None:
        if self.kind != "skeletal":
            raise AttributeError("SetSkeletalMeshAsset")
        self.mesh = mesh

    def GetNumMaterials(self) -> int:
        return len(self.materials)

    def GetMaterial(self, index: int) -> Any:
        return self.materials.get(index)

    def SetMaterial(self, index: int, material: Any) -> None:
        self.materials[index] = material

    def SetHiddenInGame(self, hidden: bool, propagate: bool) -> None:
        self.bHiddenInGame = hidden

    def SetVisibility(self, visible: bool, propagate: bool) -> None:
        self.visible = visible

    def IsVisible(self) -> bool:
        return self.visible

    def K2_GetComponentLocation(self) -> WrappedStruct:
        return vector(*self.location)

    def K2_GetComponentRotation(self) -> WrappedStruct:
        return rotator(0.0, self.yaw)

    def K2_GetComponentScale(self) -> WrappedStruct:
        return vector(1.0, 1.0, 1.0)


class VendingMachine(FakeObject):
    """A row machine: 120 wide, 80 deep and 230 tall, facing `yaw`, with its origin at its base."""

    def __init__(
        self,
        name: str,
        location: tuple[float, float, float],
        hidden: bool = False,
        yaw: float = 180.0,
    ) -> None:
        self.Name = name
        self.location = location
        self.bHidden = hidden
        self.yaw = yaw
        x, y, z = location
        self.components = [
            MeshComponent("static", MeshAsset(f"SM_{name}_Body"), location, yaw, materials=3),
            MeshComponent("static", MeshAsset(f"SM_{name}_Screen"), (x, y, z + 150.0), yaw),
            MeshComponent("skeletal", MeshAsset(f"SK_{name}_Door"), (x - 30.0, y, z + 60.0), yaw),
            # Hidden and empty components aren't copied
            MeshComponent("static", MeshAsset("SM_LOD_Proxy"), location, yaw),
            MeshComponent("static", None, location, yaw),
        ]
        self.components[3].bHiddenInGame = True

    def _path_name(self) -> str:
        return f"/Game/Maps/Kairos/Kairos_P.Kairos_P:PersistentLevel.{self.Name}"

    def K2_GetActorLocation(self) -> WrappedStruct:
        return vector(*self.location)

    def K2_GetActorRotation(self) -> WrappedStruct:
        return rotator(0.0, self.yaw)

    def GetActorBounds(
        self, only_colliding: bool, origin: Any, extent: Any, children: bool
    ) -> tuple[Any, ...]:
        x, y, z = self.location
        # 40 deep along where it faces, 60 wide across it
        depth_x = self.yaw % 180 == 0
        return (
            ...,
            vector(x, y, z + 115.0),
            vector(40.0 if depth_x else 60.0, 60.0 if depth_x else 40.0, 115.0),
        )

    def K2_GetComponentsByClass(self, cls: Any) -> list[MeshComponent]:
        kind = {
            "/Script/Engine.StaticMeshComponent": "static",
            "/Script/Engine.SkeletalMeshComponent": "skeletal",
        }
        return [c for c in self.components if c.kind == kind[cls.Name]]


class TextRender(FakeObject):
    def __init__(self) -> None:
        self.text = ""
        self.calls: list[tuple[str, Any]] = []

    def K2_SetText(self, value: str) -> None:
        self.text = value

    def SetTextRenderColor(self, value: Any) -> None:
        self.calls.append(("SetTextRenderColor", value))

    def SetWorldSize(self, value: float) -> None:
        self.calls.append(("SetWorldSize", value))

    def SetHorizontalAlignment(self, value: int) -> None:
        self.calls.append(("SetHorizontalAlignment", value))

    def SetVerticalAlignment(self, value: int) -> None:
        self.calls.append(("SetVerticalAlignment", value))


class SpawnedActor(FakeObject):
    """An actor the mod spawned: a StaticMeshActor, SkeletalMeshActor or TextRenderActor."""

    def __init__(self, game: Game, class_name: str, transform: Any) -> None:
        self.game = game
        self.Name = f"{class_name}_{len(game.actors)}"
        self.class_name = class_name
        self.transform = transform
        self.replicates = class_name == "SkeletalMeshActor"
        self.replicates_at_finish: bool | None = None
        self.collision = True
        self.finished = False
        self.destroyed = False
        match class_name:
            case "StaticMeshActor":
                self.StaticMeshComponent = MeshComponent("static")
            case "SkeletalMeshActor":
                self.SkeletalMeshComponent = MeshComponent("skeletal")
            case "TextRenderActor":
                self.TextRender = TextRender()

    @property
    def location(self) -> tuple[float, float, float]:
        t = self.transform.Translation
        return t.X, t.Y, t.Z

    def SetReplicates(self, value: bool) -> None:
        self.replicates = value

    def SetActorEnableCollision(self, value: bool) -> None:
        self.collision = value

    def K2_DestroyActor(self) -> None:
        self.destroyed = True
        self._collected = True


class GameplayStatics(FakeObject):
    def __init__(self, game: Game) -> None:
        self.game = game

    def GetCurrentLevelName(self, context: Any, remove_prefix: bool) -> str:
        if not isinstance(context, World):
            raise TypeError("Expected a world")
        return self.game.map_name

    def BeginDeferredActorSpawnFromClass(
        self,
        context: Any,
        cls: Any,
        transform: Any,
        collision: int,
        owner: Any,
        scale_method: int,
    ) -> SpawnedActor:
        if not isinstance(context, World) or transform._struct_name != "Transform":
            raise TypeError("Bad spawn arguments")
        rotation = transform.Rotation
        if abs(rotation.X**2 + rotation.Y**2 + rotation.Z**2 + rotation.W**2 - 1) > 1e-6:
            raise ValueError("Spawn rotation isn't a unit quaternion")
        return SpawnedActor(self.game, cls.Name.rsplit(".", 1)[-1], transform)

    def FinishSpawningActor(self, actor: SpawnedActor, transform: Any, scale_method: int) -> SpawnedActor:
        actor.finished = True
        actor.replicates_at_finish = actor.replicates
        self.game.actors.append(actor)
        return actor


class KismetSystemLibrary(FakeObject):
    def __init__(self, game: Game) -> None:
        self.game = game

    def ExecuteConsoleCommand(self, context: Any, command: str, player: Any) -> None:
        if not isinstance(command, str):
            raise TypeError("Expected a command")
        self.game.console_commands.append(command)

    def LineTraceSingle(self, *args: Any) -> tuple[bool, WrappedStruct]:
        """Checks the line against the game's walls: (x min, x max, y min, y max) boxes."""
        if len(args) != 12:
            raise TypeError(f"LineTraceSingle takes 12 arguments, got {len(args)}")
        _, start, end, channel, _, ignore, *_ = args
        if not isinstance(ignore, list) or channel != 0:
            raise TypeError("Bad trace arguments")
        self.game.traces += 1
        for step in range(101):
            t = step / 100
            x, y = start.X + (end.X - start.X) * t, start.Y + (end.Y - start.Y) * t
            if any(x0 <= x <= x1 and y0 <= y <= y1 for x0, x1, y0, y1 in self.game.walls):
                return True, WrappedStruct("HitResult", Distance=t)
        return False, WrappedStruct("HitResult", Distance=0.0)


class WidgetBlueprintLibrary(FakeObject):
    def __init__(self, game: Game) -> None:
        self.game = game

    def SetInputMode_GameAndUIEx(
        self, pc: Any, focus: Any, lock: int, hide_cursor: bool, flush: bool
    ) -> None:
        self.game.input_mode = ("GameAndUI", focus)

    def SetInputMode_GameOnly(self, pc: Any, flush: bool) -> None:
        self.game.input_mode = ("GameOnly", None)

    def SetFocusToGameViewport(self) -> None:
        pass


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

    def SetRenderOpacity(self, value: float) -> None:
        self.calls.append(("SetRenderOpacity", value))

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


class Button(Widget):
    def __init__(self, cls_name: str, outer: Any) -> None:
        super().__init__(cls_name, outer)
        # Set by the test, like the player holding the mouse down on it
        self.pressed = False
        self.hovered = False
        self.focused_by: Any = None

    def IsPressed(self) -> bool:
        return self.pressed

    def IsHovered(self) -> bool:
        return self.hovered

    def SetUserFocus(self, pc: Any) -> None:
        self.focused_by = pc

    def SetKeyboardFocus(self) -> None:
        pass


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
    "/Script/UMG.Button": Button,
}

ENGINE_CLASSES = (
    "/Script/Engine.StaticMeshComponent",
    "/Script/Engine.SkeletalMeshComponent",
    "/Script/Engine.StaticMeshActor",
    "/Script/Engine.SkeletalMeshActor",
    "/Script/Engine.TextRenderActor",
)


# ==================================================================================================


class Game:
    def __init__(self) -> None:
        self.engine = Engine(self)
        balances = {"Cash": 1_000_000, "eridium": 500}
        # The local player, and a co-op partner who exists on the host as a remote controller
        self.pc = PlayerController(
            "Moze", 50, dict(balances), (1000.0, 2000.0, 300.0), local=True, player_id=256
        )
        self.friend = PlayerController(
            "Zane", 20, dict(balances), (1300.0, 2000.0, 300.0), local=False, player_id=257
        )
        self.engine.GameViewport.World.GameState = GameState([self.pc.PlayerState, self.friend.PlayerState])
        # Solid walls, for line traces: (x min, x max, y min, y max)
        self.walls: list[tuple[float, float, float, float]] = []
        self.traces = 0
        self.give_calls: list[tuple[str, str, int]] = []
        # Whether GiveCurrency takes currency away when given a negative amount. Unknown in the real
        # game, so the session tries both
        self.negative_gives = False
        self.spawned: list[tuple[str, int, tuple[float, float, float]]] = []
        self.map_name = "Kairos_P"
        # A safehouse row of two machines along +Y, facing the player, and a lone one far away
        self.machines = [
            VendingMachine("Default__OakVendingMachine", (1000.0, 2000.0, 300.0)),
            VendingMachine("VendingMachine_Guns_1", (1200.0, 2100.0, 300.0)),
            VendingMachine("VendingMachine_Ammo_2", (1200.0, 2230.0, 300.0)),
            VendingMachine("HiddenHelperMachine", (1000.0, 2000.0, 300.0), hidden=True),
            VendingMachine("VendingMachine_Health_3", (30_000.0, 0.0, 0.0), yaw=90.0),
        ]
        self.actors: list[SpawnedActor] = []
        self.console_commands: list[str] = []
        self.input_mode: tuple[str, Any] | None = None
        self.item_stores = [
            ItemPoolStore(self, "Default__NexusConfigStoreItemPool"),
            ItemPoolStore(self, "NexusConfigStoreItemPool_0"),
        ]
        self.widgets: list[Widget | WidgetTree] = []
        self.classes = {
            "GbxCurrencyFunctionLibrary": ClassDefault("GbxCurrencyFunctionLibrary", CurrencyLibrary(self)),
            "GameplayStatics": ClassDefault("GameplayStatics", GameplayStatics(self)),
            "KismetSystemLibrary": ClassDefault("KismetSystemLibrary", KismetSystemLibrary(self)),
            "WidgetBlueprintLibrary": ClassDefault("WidgetBlueprintLibrary", WidgetBlueprintLibrary(self)),
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
        if cls == "Class" and (name in WIDGET_CLASSES or name in ENGINE_CLASSES):
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

    def live_actors(self, class_name: str | None = None) -> list[SpawnedActor]:
        return [
            a
            for a in self.actors
            if not a.destroyed
            and not getattr(a, "_collected", False)
            and (class_name is None or a.class_name == class_name)
        ]

    def collect_actors(self) -> None:
        """Simulates a map change taking every spawned actor with it."""
        for actor in self.actors:
            actor._collected = True

    def cash(self) -> int:
        return self.pc.cash()

    def collect_widgets(self) -> None:
        """Simulates a map change garbage collecting every widget."""
        for widget in self.widgets:
            widget._collected = True  # type: ignore[attr-defined]


GAME = Game()
