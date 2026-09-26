"""
Slot machines standing in the world: where they go, and which one the player is looking at.

Every group of vending machines (safehouses, settlements, the hub) gets one slot machine, standing at
the end of the row. Players can also add machines by hand, or remove ones that landed somewhere
silly. Those changes are stored per map.

Nothing here touches the game, so it runs (and is unit tested) outside Borderlands. Distances are in
Unreal units (cm), yaw is in degrees, and Z is up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

T = TypeVar("T")

Vector = tuple[float, float, float]

# Vending machines closer together than this count as one group.
GROUP_RADIUS = 1500.0
# A group with a slot machine this close to any of its vending machines doesn't get another.
SERVED_RADIUS = 1500.0
# Space left between the end of a row and the slot machine.
GAP = 40.0
# Removing an automatic slot machine stops one being placed this close to the same spot again. The
# group falls back to its next candidate spot, so removing one works like "try somewhere else".
SUPPRESS_RADIUS = 150.0
# Candidate spots closer together than this count as the same spot.
DUPLICATE_RADIUS = 100.0
# How closely the line between the two furthest machines has to follow an end machine's sides for
# them to count as a row (as a cosine). Machines facing each other across an aisle aren't a row.
ROW_ALIGNMENT = 0.9
# How far from the camera a slot machine can be used.
REACH = 300.0
# How far in front of the player `spot_in_front` puts a hand placed machine.
PLACE_DISTANCE = 150.0
# The most slot machines kept standing on one map, as a safety net.
MAX_CABINETS = 32
# The most slot machines put up in one update, so a big area loading in doesn't cause a hitch.
MAX_SPAWNS_PER_UPDATE = 2
# A vending machine's size, for when the game can't tell us: its bounding box's half size.
DEFAULT_EXTENT: Vector = (60.0, 60.0, 115.0)
# Machines whose bases are closer in height than this are on the same floor.
FLOOR_TOLERANCE = 150.0


def _right(yaw: float) -> tuple[float, float]:
    # Yaw 0 faces +X and yaw 90 faces +Y, so "right" is 90 degrees further round
    rad = math.radians(yaw)
    return -math.sin(rad), math.cos(rad)


def rotate(x: float, y: float, degrees: float) -> tuple[float, float]:
    """Turns a point on the floor plan around the origin, the same way yaw turns."""
    rad = math.radians(degrees)
    cos, sin = math.cos(rad), math.sin(rad)
    return x * cos - y * sin, x * sin + y * cos


def rotator_to_quat(pitch: float, yaw: float, roll: float) -> tuple[float, float, float, float]:
    """Converts an Unreal rotator (in degrees) to a quaternion (x, y, z, w), like `FRotator::Quaternion`."""
    half = math.pi / 360.0
    sp, cp = math.sin(pitch * half), math.cos(pitch * half)
    sy, cy = math.sin(yaw * half), math.cos(yaw * half)
    sr, cr = math.sin(roll * half), math.cos(roll * half)
    return (
        cr * sp * sy - sr * cp * cy,
        -cr * sp * cy - sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def forward(pitch: float, yaw: float) -> Vector:
    """Gets the unit vector a camera with the given rotation looks along."""
    p, y = math.radians(pitch), math.radians(yaw)
    return math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p)


@dataclass(frozen=True)
class Spot:
    """Where a slot machine stands. `z` is floor level: the bottom of the machine."""

    x: float
    y: float
    z: float
    yaw: float = 0.0

    def distance_to(self, x: float, y: float, z: float) -> float:
        return math.dist((self.x, self.y, self.z), (x, y, z))

    def to_json(self) -> list[float]:
        return [round(self.x, 1), round(self.y, 1), round(self.z, 1), round(self.yaw, 1)]

    @classmethod
    def from_json(cls, data: Any) -> Spot | None:
        if not isinstance(data, list | tuple) or len(data) != 4:
            return None
        if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in data):
            return None
        if not all(math.isfinite(v) for v in data):
            return None
        return cls(float(data[0]), float(data[1]), float(data[2]), float(data[3]))


def spot_in_front(x: float, y: float, floor: float, yaw: float, distance: float = PLACE_DISTANCE) -> Spot:
    """Gets a spot `distance` in front of someone standing at (x, y), facing back towards them."""
    rad = math.radians(yaw)
    return Spot(x + math.cos(rad) * distance, y + math.sin(rad) * distance, floor, (yaw + 180.0) % 360.0)


@dataclass(frozen=True)
class Body:
    """A machine's rough shape, for working out what the player is aiming at: an upright cylinder."""

    x: float
    y: float
    bottom: float
    top: float
    radius: float


@dataclass(frozen=True)
class VendingMachine:
    """
    A vending machine seen in the world.

    `key` identifies it, e.g. its object path. `x`, `y`, `z` is the actor's location, which the game
    may put at the machine's base or its middle. `center` and `extent` are its world space bounding
    box, when the game can tell us.
    """

    key: str
    x: float
    y: float
    z: float
    yaw: float
    center: Vector | None = None
    extent: Vector = DEFAULT_EXTENT

    @property
    def bottom(self) -> float:
        """The height of the machine's base."""
        return self.center[2] - self.extent[2] if self.center is not None else self.z

    @property
    def spot(self) -> Spot:
        return Spot(self.x, self.y, self.bottom, self.yaw)

    @property
    def half_width(self) -> float:
        """Half the machine's width along its row, i.e. its left/right axis."""
        rx, ry = _right(self.yaw)
        return abs(self.extent[0] * rx) + abs(self.extent[1] * ry)

    def body_at(self, spot: Spot) -> Body:
        """Gets the shape a copy of this machine has when it stands at `spot`."""
        cx, cy = (self.center[0], self.center[1]) if self.center is not None else (self.x, self.y)
        dx, dy = rotate(cx - self.x, cy - self.y, spot.yaw - self.yaw)
        return Body(
            spot.x + dx,
            spot.y + dy,
            spot.z,
            spot.z + 2 * self.extent[2],
            (self.extent[0] + self.extent[1]) / 2,
        )

    @property
    def body(self) -> Body:
        return self.body_at(self.spot)


def _near(spots: Iterable[Spot], spot: Spot, radius: float) -> bool:
    return any(other.distance_to(spot.x, spot.y, spot.z) <= radius for other in spots)


def _overlaps(spot: Spot, half_width: float, machine: VendingMachine) -> bool:
    """Checks if a machine `half_width` wide standing at `spot` would overlap a vending machine."""
    return (
        abs(spot.z - machine.bottom) < FLOOR_TOLERANCE
        and math.dist((spot.x, spot.y), (machine.x, machine.y)) < half_width + machine.half_width
    )


def _order(machine: VendingMachine) -> tuple[float, float, float, str]:
    # Sort by position first, so every player in a co-op game places machines the same way, even if
    # their game names the actors differently
    return round(machine.x, -1), round(machine.y, -1), round(machine.z, -1), machine.key


def group_machines(machines: Sequence[VendingMachine]) -> list[list[VendingMachine]]:
    """Splits machines into groups, where each is within `GROUP_RADIUS` of another in its group."""
    groups: list[list[VendingMachine]] = []
    for machine in machines:
        touching = [
            group
            for group in groups
            if any(math.dist((m.x, m.y), (machine.x, machine.y)) <= GROUP_RADIUS for m in group)
        ]
        merged = [machine]
        for group in touching:
            merged.extend(group)
            groups.remove(group)
        groups.append(merged)
    for group in groups:
        group.sort(key=_order)
    groups.sort(key=lambda g: _order(g[0]))
    return groups


def candidate_spots(group: Sequence[VendingMachine]) -> list[tuple[VendingMachine, Spot]]:
    """
    Works out where a group's slot machine could stand, best first.

    First just past either end of the row, then beside each end machine, always facing the same way
    as the machine it stands next to. Spots that would overlap a vending machine are skipped.

    Args:
        group: The group of vending machines.
    Returns:
        A list of (machine to copy, spot) pairs.
    """
    if not group:
        return []

    directions: list[tuple[VendingMachine, float, float]] = []
    if len(group) == 1:
        ends = [group[0]]
    else:
        # The two machines furthest apart mark the ends of the row
        a, b = max(
            ((m1, m2) for i, m1 in enumerate(group) for m2 in group[i + 1 :]),
            key=lambda pair: math.dist((pair[0].x, pair[0].y), (pair[1].x, pair[1].y)),
        )
        length = math.dist((a.x, a.y), (b.x, b.y)) or 1.0
        dx, dy = (b.x - a.x) / length, (b.y - a.y) / length
        for machine, out_x, out_y in ((b, dx, dy), (a, -dx, -dy)):
            # Only a real row if it runs out of this machine's side. Then use the side itself, so
            # the slot machine lines up with the row
            rx, ry = _right(machine.yaw)
            along = out_x * rx + out_y * ry
            if abs(along) >= ROW_ALIGNMENT:
                side = 1.0 if along > 0 else -1.0
                directions.append((machine, rx * side, ry * side))
        ends = [b, a]
    for machine in ends:
        rx, ry = _right(machine.yaw)
        directions += [(machine, rx, ry), (machine, -rx, -ry)]

    spots: list[tuple[VendingMachine, Spot]] = []
    for machine, dx, dy in directions:
        step = 2 * machine.half_width + GAP
        spot = Spot(machine.x + dx * step, machine.y + dy * step, machine.bottom, machine.yaw)
        overlaps = any(_overlaps(spot, machine.half_width, m) for m in group)
        duplicate = _near((other for _, other in spots), spot, DUPLICATE_RADIUS)
        if not overlaps and not duplicate:
            spots.append((machine, spot))
    return spots


@dataclass(frozen=True)
class Placement:
    """A slot machine that should exist: where, and which vending machine to copy the look of."""

    spot: Spot
    source: VendingMachine


def plan_placements(
    machines: Sequence[VendingMachine],
    existing: Iterable[Spot],
    removed: Iterable[Spot],
    occupied: Iterable[Spot] = (),
) -> list[list[Placement]]:
    """
    Plans the automatic slot machines still missing.

    Args:
        machines: The vending machines currently loaded.
        existing: Where automatic slot machines already stand.
        removed: Spots where players removed an automatic slot machine.
        occupied: Spots taken by anything else, e.g. machines placed by hand.
    Returns:
        For each group still missing a slot machine, its candidate placements, best first.
    """
    existing = list(existing)
    removed = list(removed)
    occupied = list(occupied)
    plans: list[list[Placement]] = []
    for group in group_machines(machines):
        if any(spot.distance_to(m.x, m.y, m.bottom) <= SERVED_RADIUS for spot in existing for m in group):
            continue
        candidates = [
            Placement(spot, machine)
            for machine, spot in candidate_spots(group)
            if not _near(removed, spot, SUPPRESS_RADIUS) and not _near(occupied, spot, DUPLICATE_RADIUS)
        ]
        if candidates:
            plans.append(candidates)
    return plans


# ==================================================================================================
# Aiming


def ray_hit(origin: Vector, direction: Vector, body: Body) -> float | None:
    """
    Works out where a ray first hits a body.

    Args:
        origin: Where the ray starts, e.g. the camera.
        direction: The unit vector the ray travels along.
        body: The body to test against.
    Returns:
        How far along the ray the hit is, or None if it misses. 0 if the ray starts inside.
    """
    ox, oy, oz = origin
    dx, dy, dz = direction
    px, py = ox - body.x, oy - body.y

    # Where the ray is within the cylinder's circle, looking from above
    a = dx * dx + dy * dy
    c = px * px + py * py - body.radius * body.radius
    if a < 1e-12:
        if c > 0:
            return None
        enter, leave = -math.inf, math.inf
    else:
        b = 2 * (px * dx + py * dy)
        discriminant = b * b - 4 * a * c
        if discriminant < 0:
            return None
        root = math.sqrt(discriminant)
        enter, leave = (-b - root) / (2 * a), (-b + root) / (2 * a)

    # Where the ray is within the cylinder's height
    if abs(dz) < 1e-12:
        if not body.bottom <= oz <= body.top:
            return None
    else:
        t1, t2 = (body.bottom - oz) / dz, (body.top - oz) / dz
        enter, leave = max(enter, min(t1, t2)), min(leave, max(t1, t2))

    enter = max(enter, 0.0)
    return enter if enter <= leave else None


def aim(
    origin: Vector,
    direction: Vector,
    targets: Iterable[tuple[T, Body]],
    blockers: Iterable[Body] = (),
    reach: float = REACH,
) -> tuple[T, float] | None:
    """
    Works out which target the player is aiming at.

    Args:
        origin: Where the player's view starts, i.e. the camera.
        direction: The unit vector the player is looking along.
        targets: The things that can be aimed at, with their bodies.
        blockers: Other bodies in the way, e.g. real vending machines next to a slot machine.
        reach: The furthest away a target can be.
    Returns:
        The nearest target the view hits within reach, and how far away it is, unless a blocker is
        hit first.
    """
    best: tuple[T, float] | None = None
    for target, body in targets:
        distance = ray_hit(origin, direction, body)
        if distance is not None and distance <= reach and (best is None or distance < best[1]):
            best = (target, distance)
    if best is None:
        return None
    if any((hit := ray_hit(origin, direction, body)) is not None and hit < best[1] for body in blockers):
        return None
    return best


def aimed_at(
    origin: Vector,
    direction: Vector,
    targets: Iterable[tuple[T, Body]],
    blockers: Iterable[Body] = (),
    reach: float = REACH,
) -> T | None:
    """Like `aim`, but only returns the target."""
    found = aim(origin, direction, targets, blockers, reach)
    return None if found is None else found[0]


# ==================================================================================================
# Player changes


@dataclass
class MachineOverrides:
    """Hand placed slot machines, and automatic ones players removed, per map."""

    added: dict[str, list[Spot]] = field(default_factory=dict)
    removed: dict[str, list[Spot]] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: Any) -> MachineOverrides:
        overrides = cls()
        if not isinstance(data, dict):
            return overrides
        for name, target in (("added", overrides.added), ("removed", overrides.removed)):
            maps = data.get(name)
            if not isinstance(maps, dict):
                continue
            for map_name, spots in maps.items():
                if isinstance(spots, list):
                    parsed = [spot for raw in spots if (spot := Spot.from_json(raw)) is not None]
                    if parsed:
                        target[str(map_name)] = parsed
        return overrides

    def to_json(self) -> dict[str, Any]:
        return {
            "added": {m: [s.to_json() for s in spots] for m, spots in self.added.items() if spots},
            "removed": {m: [s.to_json() for s in spots] for m, spots in self.removed.items() if spots},
        }

    def add(self, map_name: str, spot: Spot) -> Spot:
        """Adds a hand placed machine. Returns the spot as it will be saved."""
        spot = Spot.from_json(spot.to_json()) or spot
        self.added.setdefault(map_name, []).append(spot)
        return spot

    def remove(self, map_name: str, spot: Spot, *, automatic: bool) -> None:
        """Removes a hand placed machine, or stops an automatic one coming back."""
        if automatic:
            self.removed.setdefault(map_name, []).append(Spot.from_json(spot.to_json()) or spot)
        else:
            self._set(self.added, map_name, [a for a in self.added.get(map_name, []) if a != spot])

    @staticmethod
    def _set(maps: dict[str, list[Spot]], map_name: str, spots: list[Spot]) -> None:
        # Drop empty lists, so overrides that cancel out compare equal to none at all
        if spots:
            maps[map_name] = spots
        else:
            maps.pop(map_name, None)

    def reset(self, map_name: str) -> None:
        """Forgets every change made on a map."""
        self.added.pop(map_name, None)
        self.removed.pop(map_name, None)


# ==================================================================================================
# Keeping machines standing


@dataclass
class Cabinet(Generic[T]):
    """A slot machine standing in the world, and the game's handle on the actors that make it up."""

    spot: Spot
    source: VendingMachine
    automatic: bool
    handle: T

    @property
    def body(self) -> Body:
        return self.source.body_at(self.spot)


class CabinetKeeper(Generic[T]):
    """
    Keeps the right slot machines standing as the player moves around and areas stream in and out.

    The game supplies callbacks that actually put machines up and take them down.
    """

    def __init__(
        self,
        spawn: Callable[[VendingMachine, Spot], T | None],
        destroy: Callable[[T], None],
        is_alive: Callable[[T], bool],
        *,
        log: Callable[[str], None] = print,
    ) -> None:
        """
        Args:
            spawn: Puts up a copy of a vending machine at a spot. Returns a handle, or None if it
                   couldn't.
            destroy: Takes down a machine, given its handle.
            is_alive: Checks if a machine's actors still exist, e.g. after a map change.
            log: Where to report problems.
        """
        self.spawn = spawn
        self.destroy = destroy
        self.is_alive = is_alive
        self.log = log
        self.map_name: str | None = None
        self.cabinets: list[Cabinet[T]] = []
        self._failed: list[Spot] = []

    def update(self, map_name: str, machines: Sequence[VendingMachine], overrides: MachineOverrides) -> bool:
        """
        Puts up missing machines and takes down unwanted ones.

        Args:
            map_name: The current map. Changing it forgets every machine from the last one.
            machines: The vending machines currently loaded.
            overrides: The players' changes, for every map.
        Returns:
            True if there may be more to put up, e.g. it stopped to spread the work out.
        """
        if map_name != self.map_name:
            # The old map's actors went with it
            self.cabinets.clear()
            self._failed.clear()
            self.map_name = map_name
        for cabinet in [c for c in self.cabinets if not self.is_alive(c.handle)]:
            # Something took some of it away. Clear up whatever's left before putting it back
            self._take_down(cabinet)

        added = overrides.added.get(map_name, [])
        removed = overrides.removed.get(map_name, [])
        for cabinet in list(self.cabinets):
            spot = cabinet.spot
            if cabinet.automatic:
                # Also make way if a vending machine loaded in where we guessed the row ended, or a
                # player put one there by hand
                unwanted = (
                    _near(removed, spot, SUPPRESS_RADIUS)
                    or _near(added, spot, DUPLICATE_RADIUS)
                    or any(_overlaps(spot, cabinet.source.half_width, m) for m in machines)
                )
            else:
                unwanted = spot not in added
            if unwanted:
                self._take_down(cabinet)

        budget = MAX_SPAWNS_PER_UPDATE
        for spot in added:
            if len(self.cabinets) >= MAX_CABINETS:
                return False
            if budget <= 0:
                return True
            if spot in self._failed or any(c.spot == spot and not c.automatic for c in self.cabinets):
                continue
            source = min(machines, key=lambda m: math.dist((m.x, m.y), (spot.x, spot.y)), default=None)
            if source is None:
                # Wait for a vending machine to load in, to copy the look of
                break
            budget -= 1
            self._put_up(source, spot, automatic=False)

        # Hand placed machines are extras: they don't stand in for a row's own slot machine
        automatic = [c.spot for c in self.cabinets if c.automatic]
        occupied = [c.spot for c in self.cabinets if not c.automatic]
        for candidates in plan_placements(machines, automatic, removed, occupied):
            if len(self.cabinets) >= MAX_CABINETS:
                return False
            if budget <= 0:
                return True
            placement = next((p for p in candidates if p.spot not in self._failed), None)
            if placement is None:
                continue
            budget -= 1
            self._put_up(placement.source, placement.spot, automatic=True)
        return False

    def clear(self) -> None:
        """Takes down every machine, and forgets any spots that failed."""
        for cabinet in list(self.cabinets):
            self._take_down(cabinet)
        self._failed.clear()

    def nearest(self, x: float, y: float, z: float) -> tuple[Cabinet[T], float] | None:
        best: tuple[Cabinet[T], float] | None = None
        for cabinet in self.cabinets:
            distance = cabinet.spot.distance_to(x, y, z)
            if best is None or distance < best[1]:
                best = (cabinet, distance)
        return best

    def _put_up(self, source: VendingMachine, spot: Spot, *, automatic: bool) -> None:
        try:
            handle = self.spawn(source, spot)
        except Exception as ex:  # noqa: BLE001 - one bad spot mustn't stop the rest
            self.log(f"Couldn't put up a slot machine: {ex!r}")
            handle = None
        if handle is None:
            self._failed.append(spot)
            return
        self.cabinets.append(Cabinet(spot, source, automatic, handle))

    def _take_down(self, cabinet: Cabinet[T]) -> None:
        self.cabinets.remove(cabinet)
        try:
            self.destroy(cabinet.handle)
        except Exception as ex:  # noqa: BLE001
            self.log(f"Couldn't take down a slot machine: {ex!r}")
