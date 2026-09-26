"""
Slot machines standing in the world, built from copies of the game's own vending machines.

The game has no slot machine models, so each one is a look-alike of a real vending machine: plain
new actors given the same meshes and materials, standing at the end of the row. Being plain actors,
the game never offers to "use" them. Instead, the mod works out when the player is aiming at one,
and handles the use key itself (see `sdk_mod.py`).

Each player's game puts up its own copies, and they're never replicated, so co-op partners don't see
double. They have no collision, like ActorScriptDeployer's visual copies: the host's game decides
where everyone can walk, and a partner may not have the same machines (e.g. ones placed by hand), so
solid copies could turn into invisible walls. The APIs used are the same ones Matt's
ActorScriptDeployer uses to spawn visual copies of game actors - see `docs/game-api.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import unrealsdk
from mods_base import ENGINE
from unrealsdk import logging
from unrealsdk.unreal import IGNORE_STRUCT, UObject, WeakPointer

from . import bl4, cabinets
from .cabinets import CabinetKeeper, MachineOverrides, Spot, VendingMachine

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from .cabinets import Cabinet, Vector

STATIC_MESH_COMPONENT = "/Script/Engine.StaticMeshComponent"
SKELETAL_MESH_COMPONENT = "/Script/Engine.SkeletalMeshComponent"
STATIC_MESH_ACTOR = "/Script/Engine.StaticMeshActor"
SKELETAL_MESH_ACTOR = "/Script/Engine.SkeletalMeshActor"
TEXT_RENDER_ACTOR = "/Script/Engine.TextRenderActor"

# ESpawnActorCollisionHandlingMethod::AlwaysSpawn, so no part gets nudged out of place
ALWAYS_SPAWN = 1
# ESpawnActorScaleMethod::MultiplyWithRoot
MULTIPLY_WITH_ROOT = 1
# EComponentMobility::Movable, so a mesh can be set after spawning
MOVABLE = 2
# EHorizTextAligment::EHTA_Center, EVerticalTextAligment::EVRTA_TextCenter
TEXT_CENTER = 1

# The most mesh parts copied from one vending machine.
MAX_PARTS = 16
SIGN_TEXT = "SLOTS"
SIGN_COLOR = (255, 190, 40)
SIGN_SIZE = 42.0
SIGN_GAP = 30.0

# ETraceTypeQuery::TraceTypeQuery1, the visibility channel
VISIBILITY_TRACE = 0
# Hits closer than this to the camera are the player's own arms or gun, not a wall.
TRACE_SKIP_NEAR = 50.0
# Anything hit this far in front of a slot machine's surface is between it and the player.
TRACE_MARGIN = 40.0

# Sanity limits on a vending machine's bounding box (half sizes), in case it includes something
# large like a trigger volume.
HORIZONTAL_EXTENT = (20.0, 150.0)
VERTICAL_EXTENT = (50.0, 200.0)


def _first(value: Any) -> Any:
    """Unwraps a function's return value, if the SDK handed back (return value, *out params)."""
    return value[0] if isinstance(value, tuple) else value


def _clamp(value: float, limits: tuple[float, float]) -> float:
    return min(max(value, limits[0]), limits[1])


def current_map() -> str | None:
    """Gets the name of the loaded map, e.g. to store hand placed machines against."""
    world = ENGINE.GameViewport.World
    if world is None:
        return None
    try:
        statics = unrealsdk.find_class("GameplayStatics").ClassDefaultObject
        name = str(statics.GetCurrentLevelName(world, True))
        if name:
            return name
    except Exception as ex:  # noqa: BLE001 - fall back to the world's own name
        logging.dev_warning(f"[Borderlands Gamble] GetCurrentLevelName failed: {ex!r}")
    return str(world.Name)


def view_ray(pc: UObject) -> tuple[Vector, Vector] | None:
    """Gets where the player's camera is, and the direction it's looking."""
    try:
        camera = pc.PlayerCameraManager
        loc = camera.GetCameraLocation()
        rot = camera.GetCameraRotation()
    except Exception:  # noqa: BLE001 - e.g. mid load
        return None
    return (loc.X, loc.Y, loc.Z), cabinets.forward(rot.Pitch, rot.Yaw)


_reported_trace_failure = False


def view_blocked(pc: UObject, origin: Vector, direction: Vector, distance: float) -> bool:
    """
    Checks for a wall, or anything else solid, between the camera and a point along its view.

    Args:
        pc: The player looking.
        origin: The camera's location.
        direction: The unit vector it looks along.
        distance: How far along the view the point is.
    Returns:
        True if something's in the way. False if the view is clear, or it couldn't be checked.
    """
    global _reported_trace_failure
    end = distance - TRACE_MARGIN
    if end <= TRACE_SKIP_NEAR:
        return False

    def point(along: float) -> Any:
        return unrealsdk.make_struct(
            "Vector",
            X=origin[0] + direction[0] * along,
            Y=origin[1] + direction[1] * along,
            Z=origin[2] + direction[2] * along,
        )

    try:
        library = unrealsdk.find_class("KismetSystemLibrary").ClassDefaultObject
        ignore = [pc.Pawn] if pc.Pawn is not None else []
        hit, _ = library.LineTraceSingle(
            ENGINE.GameViewport.World,
            point(TRACE_SKIP_NEAR),
            point(end),
            VISIBILITY_TRACE,
            False,
            ignore,
            0,
            IGNORE_STRUCT,
            True,
            IGNORE_STRUCT,
            IGNORE_STRUCT,
            0.0,
        )
    except Exception as ex:  # noqa: BLE001 - better a prompt through a wall than no prompt at all
        if not _reported_trace_failure:
            _reported_trace_failure = True
            logging.dev_warning(f"[Borderlands Gamble] Couldn't check for walls: {ex!r}")
        return False
    return bool(hit)


def _bounds(actor: UObject) -> tuple[Vector | None, Vector]:
    try:
        _, origin, extent = actor.GetActorBounds(False, IGNORE_STRUCT, IGNORE_STRUCT, False)
    except Exception:  # noqa: BLE001 - use the default size
        return None, cabinets.DEFAULT_EXTENT
    return (
        (origin.X, origin.Y, origin.Z),
        (
            _clamp(extent.X, HORIZONTAL_EXTENT),
            _clamp(extent.Y, HORIZONTAL_EXTENT),
            _clamp(extent.Z, VERTICAL_EXTENT),
        ),
    )


def scan_vending_machines() -> list[tuple[UObject, VendingMachine]]:
    """Finds every vending machine currently loaded."""
    found: list[tuple[UObject, VendingMachine]] = []
    for actor in unrealsdk.find_all(bl4.VENDING_MACHINE_CLASS, exact=False):
        if bl4.is_template(actor):
            continue
        try:
            # Other mods sometimes spawn hidden machines as helpers - ignore those
            if actor.bHidden:
                continue
            loc = actor.K2_GetActorLocation()
            yaw = float(actor.K2_GetActorRotation().Yaw)
            center, extent = _bounds(actor)
            record = VendingMachine(actor._path_name(), loc.X, loc.Y, loc.Z, yaw, center, extent)
        except Exception:  # noqa: BLE001 - half loaded actor, skip it
            continue
        found.append((actor, record))
    return found


# ==================================================================================================
# Building copies


@dataclass
class CabinetActors:
    """The actors that make up one slot machine."""

    parts: list[WeakPointer] = field(default_factory=list)
    sign: WeakPointer | None = None

    def actors(self) -> Iterator[UObject]:
        for ptr in (*self.parts, *([self.sign] if self.sign is not None else [])):
            actor = ptr()
            if actor is not None:
                yield actor


@dataclass(frozen=True)
class _Part:
    """One mesh on a vending machine, and where it sits relative to the machine."""

    kind: str
    component: UObject
    mesh: UObject
    location: Vector
    rotation: Vector
    scale: Vector


def _components_of(actor: UObject, class_path: str) -> list[UObject]:
    cls = unrealsdk.find_object("Class", class_path)
    for name in ("K2_GetComponentsByClass", "GetComponentsByClass"):
        try:
            return [component for component in getattr(actor, name)(cls) if component is not None]
        except AttributeError:
            continue
    return []


def _mesh_asset(kind: str, component: UObject) -> UObject | None:
    names = (
        ("GetStaticMesh", "StaticMesh")
        if kind == "static"
        else ("GetSkeletalMeshAsset", "SkeletalMesh", "SkinnedAsset")
    )
    for name in names:
        try:
            value = getattr(component, name)
            mesh = value() if callable(value) else value
        except Exception:  # noqa: BLE001 - try the next spelling
            continue
        if mesh is not None:
            return mesh
    return None


def _parts_of(actor: UObject) -> list[_Part]:
    parts: list[_Part] = []
    for kind, path in (("static", STATIC_MESH_COMPONENT), ("skeletal", SKELETAL_MESH_COMPONENT)):
        for component in _components_of(actor, path):
            try:
                if component.bHiddenInGame or not component.IsVisible():
                    continue
                mesh = _mesh_asset(kind, component)
                if mesh is None:
                    continue
                loc = component.K2_GetComponentLocation()
                rot = component.K2_GetComponentRotation()
                scale = component.K2_GetComponentScale()
            except Exception:  # noqa: BLE001 - skip anything we can't read
                continue
            parts.append(
                _Part(
                    kind,
                    component,
                    mesh,
                    (loc.X, loc.Y, loc.Z),
                    (rot.Pitch, rot.Yaw, rot.Roll),
                    (scale.X, scale.Y, scale.Z),
                ),
            )
    return parts[:MAX_PARTS]


def _transform(location: Vector, rotation: Vector, scale: Vector) -> Any:
    x, y, z, w = cabinets.rotator_to_quat(*rotation)
    return unrealsdk.make_struct(
        "Transform",
        Rotation=unrealsdk.make_struct("Quat", X=x, Y=y, Z=z, W=w),
        Translation=unrealsdk.make_struct("Vector", X=location[0], Y=location[1], Z=location[2]),
        Scale3D=unrealsdk.make_struct("Vector", X=scale[0], Y=scale[1], Z=scale[2]),
    )


def _spawn_actor(class_path: str, transform: Any) -> UObject | None:
    world = ENGINE.GameViewport.World
    statics = unrealsdk.find_class("GameplayStatics").ClassDefaultObject
    cls = unrealsdk.find_object("Class", class_path)
    actor = _first(
        statics.BeginDeferredActorSpawnFromClass(
            world, cls, transform, ALWAYS_SPAWN, None, MULTIPLY_WITH_ROOT
        ),
    )
    if actor is None:
        return None
    try:
        # Every player puts up their own copy, so the host's mustn't replicate to clients
        actor.SetReplicates(False)
    except Exception as ex:  # noqa: BLE001
        logging.dev_warning(f"[Borderlands Gamble] SetReplicates failed: {ex!r}")
    return _first(statics.FinishSpawningActor(actor, transform, MULTIPLY_WITH_ROOT))


def _copy_materials(source: UObject, target: UObject) -> None:
    try:
        count = int(source.GetNumMaterials())
    except Exception:  # noqa: BLE001
        return
    for index in range(min(count, 64)):
        try:
            material = source.GetMaterial(index)
            if material is not None:
                target.SetMaterial(index, material)
        except Exception:  # noqa: BLE001 - keep whatever the slot has
            continue


def _set_mesh(kind: str, component: UObject, mesh: UObject) -> None:
    if kind == "static":
        component.SetMobility(MOVABLE)
        component.SetStaticMesh(mesh)
        return
    try:
        component.SetSkeletalMeshAsset(mesh)
    except AttributeError:
        component.SetSkeletalMesh(mesh, True)


def _spawn_part(part: _Part, location: Vector, rotation: Vector) -> UObject | None:
    class_path, component_name = (
        (STATIC_MESH_ACTOR, "StaticMeshComponent")
        if part.kind == "static"
        else (SKELETAL_MESH_ACTOR, "SkeletalMeshComponent")
    )
    actor = _spawn_actor(class_path, _transform(location, rotation, part.scale))
    if actor is None:
        return None
    try:
        actor.SetActorEnableCollision(False)
        component = getattr(actor, component_name)
        _set_mesh(part.kind, component, part.mesh)
        _copy_materials(part.component, component)
        component.SetHiddenInGame(False, True)
        component.SetVisibility(True, True)
    except Exception:
        actor.K2_DestroyActor()
        raise
    return actor


def _spawn_sign(body: cabinets.Body, yaw: float) -> UObject | None:
    location = (body.x, body.y, body.top + SIGN_GAP)
    actor = _spawn_actor(TEXT_RENDER_ACTOR, _transform(location, (0.0, yaw, 0.0), (1.0, 1.0, 1.0)))
    if actor is None:
        return None
    text = actor.TextRender
    text.K2_SetText(SIGN_TEXT)
    r, g, b = SIGN_COLOR
    text.SetTextRenderColor(unrealsdk.make_struct("Color", R=r, G=g, B=b, A=255))
    text.SetWorldSize(SIGN_SIZE)
    text.SetHorizontalAlignment(TEXT_CENTER)
    text.SetVerticalAlignment(TEXT_CENTER)
    return actor


def spawn_copy(
    source_actor: UObject, source: VendingMachine, spot: Spot, *, sign: bool
) -> CabinetActors | None:
    """
    Puts up a look-alike of a vending machine.

    Args:
        source_actor: The vending machine to copy.
        source: Where that vending machine is, and how big.
        spot: Where the copy should stand.
        sign: If to float a sign above it.
    Returns:
        The copy's actors, or None if nothing could be copied.
    """
    parts = _parts_of(source_actor)
    if not parts:
        logging.warning(f"[Borderlands Gamble] Found no meshes to copy on {source.key}.")
        return None

    turn = spot.yaw - source.yaw
    # Stand the copy on the spot's floor, however the original's origin sits relative to its base
    lift = source.z - source.bottom
    built = CabinetActors()
    try:
        for part in parts:
            dx, dy = cabinets.rotate(part.location[0] - source.x, part.location[1] - source.y, turn)
            location = (spot.x + dx, spot.y + dy, spot.z + lift + (part.location[2] - source.z))
            pitch, yaw, roll = part.rotation
            actor = _spawn_part(part, location, (pitch, yaw + turn, roll))
            if actor is not None:
                built.parts.append(WeakPointer(actor))
    except Exception:
        destroy_copy(built)
        raise
    if not built.parts:
        return None

    if sign:
        try:
            sign_actor = _spawn_sign(source.body_at(spot), spot.yaw)
            if sign_actor is not None:
                built.sign = WeakPointer(sign_actor)
        except Exception as ex:  # noqa: BLE001 - the machine works fine without one
            logging.dev_warning(f"[Borderlands Gamble] Couldn't put up a sign: {ex!r}")
    return built


def destroy_copy(handle: CabinetActors) -> None:
    for actor in list(handle.actors()):
        try:
            actor.K2_DestroyActor()
        except Exception as ex:  # noqa: BLE001
            logging.dev_warning(f"[Borderlands Gamble] Couldn't remove a slot machine part: {ex!r}")
    handle.parts.clear()
    handle.sign = None


def copy_is_alive(handle: CabinetActors) -> bool:
    return bool(handle.parts) and all(ptr() is not None for ptr in handle.parts)


# ==================================================================================================


class SlotMachines:
    """Keeps slot machines standing near vending machines, and works out which one you're aiming at."""

    def __init__(self, overrides: Callable[[], MachineOverrides], signs: Callable[[], bool]) -> None:
        """
        Args:
            overrides: Gets the players' hand placed and removed machines.
            signs: Gets if machines should have a sign floating above them.
        """
        self.overrides = overrides
        self.signs = signs
        self.keeper: CabinetKeeper[CabinetActors] = CabinetKeeper(
            self._spawn,
            destroy_copy,
            copy_is_alive,
            log=lambda msg: logging.warning(f"[Borderlands Gamble] {msg}"),
        )
        self.machines: list[VendingMachine] = []
        self._sources: dict[str, WeakPointer] = {}

    @property
    def cabinets(self) -> list[Cabinet[CabinetActors]]:
        return self.keeper.cabinets

    def update(self) -> bool:
        """
        Rescans the vending machines, and puts up or takes down slot machines to match.

        Returns:
            True if there may be more to put up, so it's worth updating again soon.
        """
        pc = bl4.local_player()
        if pc is None or pc.Pawn is None:
            return False
        map_name = current_map()
        if map_name is None:
            return False
        scanned = scan_vending_machines()
        self._sources = {record.key: WeakPointer(actor) for actor, record in scanned}
        self.machines = [record for _, record in scanned]
        return self.keeper.update(map_name, self.machines, self.overrides())

    def clear(self) -> None:
        """Takes down every slot machine."""
        self.keeper.clear()

    def aimed_at(self) -> Cabinet[CabinetActors] | None:
        """Gets the slot machine the player is aiming at, if any, and not through a wall."""
        pc = bl4.local_player()
        if pc is None or not self.keeper.cabinets:
            return None
        ray = view_ray(pc)
        if ray is None:
            return None
        found = cabinets.aim(
            *ray,
            ((cabinet, cabinet.body) for cabinet in self.keeper.cabinets),
            (machine.body for machine in self.machines),
        )
        if found is None or view_blocked(pc, *ray, found[1]):
            return None
        return found[0]

    def bodies(self) -> list[cabinets.Body]:
        return [cabinet.body for cabinet in self.keeper.cabinets]

    def _spawn(self, source: VendingMachine, spot: Spot) -> CabinetActors | None:
        ptr = self._sources.get(source.key)
        actor = ptr() if ptr is not None else None
        if actor is None:
            return None
        return spawn_copy(actor, source, spot, sign=self.signs())


def player_spot(pc: UObject) -> Spot | None:
    """Gets a spot just in front of the player, facing them, for placing a machine by hand."""
    pawn = pc.Pawn
    if pawn is None:
        return None
    loc = pawn.K2_GetActorLocation()
    yaw = float(pawn.K2_GetActorRotation().Yaw)
    try:
        half_height = float(pawn.CapsuleComponent.GetScaledCapsuleHalfHeight())
    except Exception:  # noqa: BLE001 - a typical capsule
        half_height = 90.0
    return cabinets.spot_in_front(loc.X, loc.Y, loc.Z - half_height, yaw)
