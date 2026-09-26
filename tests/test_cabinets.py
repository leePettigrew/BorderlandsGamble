import math
import random
import unittest
from unittest import mock

from borderlands_gamble import cabinets
from borderlands_gamble.cabinets import (
    GAP,
    MAX_SPAWNS_PER_UPDATE,
    Body,
    CabinetKeeper,
    MachineOverrides,
    Spot,
    VendingMachine,
    aimed_at,
    candidate_spots,
    forward,
    group_machines,
    plan_placements,
    ray_hit,
    rotate,
    rotator_to_quat,
    spot_in_front,
)

# 120 wide, 80 deep, 230 tall, with the actor's location at its base
EXTENT = (40.0, 60.0, 115.0)


def machine(key: str, x: float, y: float, z: float = 0.0, yaw: float = 0.0) -> VendingMachine:
    """A vending machine facing `yaw`, with its bounding box centred above its location."""
    extent = EXTENT if yaw % 180 == 0 else (EXTENT[1], EXTENT[0], EXTENT[2])
    return VendingMachine(key, x, y, z, yaw, center=(x, y, z + EXTENT[2]), extent=extent)


def row(prefix: str, count: int, *, x: float = 0.0, y: float = 0.0) -> list[VendingMachine]:
    """A row of machines facing +X, side by side along +Y."""
    return [machine(f"{prefix}{i}", x, y + i * 130.0) for i in range(count)]


class GeometryTests(unittest.TestCase):
    def test_forward(self) -> None:
        for (pitch, yaw), expected in (
            ((0, 0), (1, 0, 0)),
            ((0, 90), (0, 1, 0)),
            ((0, 180), (-1, 0, 0)),
            ((90, 0), (0, 0, 1)),
            ((-90, 0), (0, 0, -1)),
        ):
            for got, want in zip(forward(pitch, yaw), expected, strict=True):
                self.assertAlmostEqual(got, want)

    def test_rotate(self) -> None:
        x, y = rotate(1, 0, 90)
        self.assertAlmostEqual(x, 0)
        self.assertAlmostEqual(y, 1)

    def test_rotator_to_quat(self) -> None:
        def turn(q: tuple[float, float, float, float], v: tuple[float, float, float]) -> tuple[float, ...]:
            # v' = q v q*, written out as v + 2w(u x v) + 2u x (u x v)
            qx, qy, qz, qw = q
            ux, uy, uz = v
            cx, cy, cz = qy * uz - qz * uy, qz * ux - qx * uz, qx * uy - qy * ux
            dx, dy, dz = qy * cz - qz * cy, qz * cx - qx * cz, qx * cy - qy * cx
            return ux + 2 * (qw * cx + dx), uy + 2 * (qw * cy + dy), uz + 2 * (qw * cz + dz)

        for pitch, yaw, roll in (
            (0, 0, 0),
            (0, 90, 0),
            (30, 0, 0),
            (-20, 135, 0),
            (45, -60, 30),
            (10, 200, 80),
        ):
            quat = rotator_to_quat(pitch, yaw, roll)
            self.assertAlmostEqual(sum(c * c for c in quat), 1.0)
            # Whatever the roll, the X axis ends up pointing where the rotator faces
            for got, want in zip(turn(quat, (1, 0, 0)), forward(pitch, yaw), strict=True):
                self.assertAlmostEqual(got, want)

        # Positive roll dips the right side (+Y) down, as in Unreal
        for got, want in zip(turn(rotator_to_quat(0, 0, 90), (0, 1, 0)), (0, 0, -1), strict=True):
            self.assertAlmostEqual(got, want)

    def test_spot_in_front_faces_the_player(self) -> None:
        spot = spot_in_front(100, 200, 50, 90)
        self.assertAlmostEqual(spot.x, 100)
        self.assertAlmostEqual(spot.y, 200 + cabinets.PLACE_DISTANCE)
        self.assertEqual((spot.z, spot.yaw), (50, 270))

    def test_machine_size(self) -> None:
        m = machine("a", 0, 0, 100)
        self.assertEqual(m.bottom, 100)
        self.assertAlmostEqual(m.half_width, 60)
        self.assertAlmostEqual(machine("b", 0, 0, yaw=90).half_width, 60)

        # Without bounds, assume the location is the base and use the default size
        unknown = VendingMachine("c", 0, 0, 100, 0)
        self.assertEqual(unknown.bottom, 100)
        self.assertAlmostEqual(unknown.half_width, cabinets.DEFAULT_EXTENT[1])

        # Bounds centred on the location - the game puts this machine's origin in its middle
        middle = VendingMachine("d", 0, 0, 100, 0, center=(0, 0, 100), extent=EXTENT)
        self.assertEqual(middle.bottom, -15)

    def test_body_follows_the_spot(self) -> None:
        # A machine whose bounds sit 10 units in front of its location
        m = VendingMachine("a", 0, 0, 0, 0, center=(10, 0, 115), extent=EXTENT)
        body = m.body_at(Spot(500, 500, 30, 90))
        self.assertAlmostEqual(body.x, 500)
        self.assertAlmostEqual(body.y, 510)
        self.assertEqual((body.bottom, body.top), (30, 260))
        self.assertAlmostEqual(body.radius, 50)
        self.assertEqual(m.body, m.body_at(m.spot))


class SpotJsonTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        spot = Spot(1.26, -2.0, 3.0, 359.94)
        self.assertEqual(Spot.from_json(spot.to_json()), Spot(1.3, -2.0, 3.0, 359.9))

    def test_rejects_garbage(self) -> None:
        for data in (
            None,
            "1,2,3,4",
            [1, 2, 3],
            [1, 2, 3, 4, 5],
            [1, 2, 3, "4"],
            [1, 2, 3, True],
            [1, 2, math.nan, 4],
        ):
            self.assertIsNone(Spot.from_json(data), data)


class PlacementTests(unittest.TestCase):
    def test_groups(self) -> None:
        safehouse = row("safe", 3)
        settlement = row("town", 2, x=10_000)
        # Chains: each is close to the next, though the ends are far apart
        chain = [machine(f"chain{i}", 20_000 + i * 1400.0, 0) for i in range(3)]
        groups = group_machines([*settlement, *chain, *safehouse])
        self.assertEqual([len(g) for g in groups], [3, 2, 3])
        self.assertEqual({m.key for m in groups[0]}, {m.key for m in safehouse})

    def test_groups_dont_depend_on_order(self) -> None:
        machines = [*row("a", 4), *row("b", 3, x=5000), machine("c", 400, 900)]
        expected = group_machines(machines)
        rng = random.Random(1)
        for _ in range(10):
            rng.shuffle(machines)
            self.assertEqual(group_machines(machines), expected)

    def test_single_machine_goes_beside_it(self) -> None:
        spots = [spot for _, spot in candidate_spots([machine("a", 0, 0, 50)])]
        step = 2 * 60 + GAP
        self.assertEqual(spots, [Spot(0, step, 50, 0), Spot(0, -step, 50, 0)])

    def test_row_ends_come_first(self) -> None:
        machines = row("m", 3)
        candidates = candidate_spots(machines)
        spots = [spot for _, spot in candidates]
        step = 2 * 60 + GAP
        self.assertEqual(spots[:2], [Spot(0, 260 + step, 0, 0), Spot(0, -step, 0, 0)])
        # The machine at each end is the one copied
        self.assertEqual([m.key for m, _ in candidates[:2]], ["m2", "m0"])

        # Beside the end machines is along the row, so nothing new would fit
        self.assertEqual(len(spots), 2)
        for spot in spots:
            for m in machines:
                self.assertGreaterEqual(math.dist((spot.x, spot.y), (m.x, m.y)), 2 * 60)

    def test_placement_ignores_actor_names(self) -> None:
        # In co-op each player's game might name the same machines differently
        machines = row("m", 3)
        renamed = [
            VendingMachine(f"z{9 - i}", m.x, m.y, m.z, m.yaw, m.center, m.extent)
            for i, m in enumerate(machines)
        ]
        self.assertEqual(
            [spot for _, spot in candidate_spots(group_machines(machines)[0])],
            [spot for _, spot in candidate_spots(group_machines(renamed)[0])],
        )

    def test_plan(self) -> None:
        safehouse = row("safe", 2)
        town = row("town", 2, x=10_000)
        plans = plan_placements([*safehouse, *town], existing=[], removed=[])
        self.assertEqual(len(plans), 2)
        self.assertEqual(plans[0][0].source.key, "safe1")

        # One already has a slot machine
        served = plan_placements([*safehouse, *town], existing=[Spot(10_000, -400, 0)], removed=[])
        self.assertEqual([p[0].source.key for p in served], ["safe1"])

    def test_removed_spots_fall_back(self) -> None:
        safehouse = row("safe", 2)
        first, second = (p.spot for p in plan_placements(safehouse, [], [])[0])
        plans = plan_placements(safehouse, [], [first])
        self.assertEqual(plans[0][0].spot, second)
        self.assertEqual(plan_placements(safehouse, [], [first, second]), [])


class AimTests(unittest.TestCase):
    BODY = Body(x=200, y=0, bottom=0, top=200, radius=50)

    def test_hits_from_the_front(self) -> None:
        self.assertAlmostEqual(ray_hit((0, 0, 100), (1, 0, 0), self.BODY), 150)
        # Looking down onto the top
        self.assertAlmostEqual(ray_hit((200, 0, 300), (0, 0, -1), self.BODY), 100)

    def test_misses(self) -> None:
        self.assertIsNone(ray_hit((0, 0, 100), (-1, 0, 0), self.BODY))
        self.assertIsNone(ray_hit((0, 0, 100), (0, 1, 0), self.BODY))
        # Over the top, and under the bottom
        self.assertIsNone(ray_hit((0, 0, 250), (1, 0, 0), self.BODY))
        self.assertIsNone(ray_hit((0, 0, -10), (1, 0, 0), self.BODY))
        # Straight up, beside it
        self.assertIsNone(ray_hit((0, 0, 100), (0, 0, 1), self.BODY))

    def test_inside(self) -> None:
        self.assertEqual(ray_hit((200, 0, 100), (1, 0, 0), self.BODY), 0)

    def test_aimed_at(self) -> None:
        near = Body(200, 0, 0, 200, 50)
        far = Body(400, 0, 0, 200, 50)
        view = ((0, 0, 100), (1, 0, 0))
        self.assertEqual(aimed_at(*view, [("far", far), ("near", near)]), "near")
        self.assertEqual(aimed_at(*view, [("far", far)], reach=500), "far")
        self.assertIsNone(aimed_at(*view, [("far", far)], reach=300))
        # A real vending machine in front hides the slot machine, one behind doesn't
        self.assertIsNone(aimed_at(*view, [("far", far)], blockers=[near], reach=500))
        self.assertEqual(aimed_at(*view, [("near", near)], blockers=[far]), "near")
        self.assertIsNone(aimed_at(*view, []))

    def test_aiming_between_machines(self) -> None:
        # A slot machine standing at the end of a row of vending machines
        machines = row("m", 2)
        placement = plan_placements(machines, [], [])[0][0]
        cabinet = placement.source.body_at(placement.spot)
        blockers = [m.body for m in machines]
        eye = (250.0, placement.spot.y, 170.0)

        def looking_at(x: float, y: float, z: float) -> str | None:
            dx, dy, dz = x - eye[0], y - eye[1], z - eye[2]
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            return aimed_at(eye, (dx / length, dy / length, dz / length), [("slots", cabinet)], blockers)

        self.assertEqual(looking_at(cabinet.x, cabinet.y, 100), "slots")
        self.assertIsNone(looking_at(machines[1].x, machines[1].y, 100))


class OverridesTests(unittest.TestCase):
    def test_json(self) -> None:
        overrides = MachineOverrides()
        overrides.add("Map_A", Spot(1, 2, 3, 4))
        overrides.remove("Map_B", Spot(5, 6, 7, 8), automatic=True)
        data = overrides.to_json()
        self.assertEqual(data, {"added": {"Map_A": [[1, 2, 3, 4]]}, "removed": {"Map_B": [[5, 6, 7, 8]]}})
        self.assertEqual(MachineOverrides.from_json(data), overrides)

    def test_json_skips_garbage(self) -> None:
        for data in (None, [], {"added": []}, {"added": {"Map": "nope"}}, {"removed": {"Map": [[1, 2]]}}):
            self.assertEqual(MachineOverrides.from_json(data), MachineOverrides())
        mixed = MachineOverrides.from_json({"added": {"Map": [[1, 2, 3, 4], "x", None]}})
        self.assertEqual(mixed.added, {"Map": [Spot(1, 2, 3, 4)]})

    def test_add_and_remove(self) -> None:
        overrides = MachineOverrides()
        overrides.remove("Map", Spot(0, 0, 0), automatic=True)
        overrides.remove("Map", Spot(1000, 0, 0), automatic=True)

        # Adding a machine near a removed automatic one undoes that removal
        added = overrides.add("Map", Spot(50.04, 0, 0, 0))
        self.assertEqual(added, Spot(50, 0, 0, 0))
        self.assertEqual(overrides.removed["Map"], [Spot(1000, 0, 0)])

        overrides.remove("Map", added, automatic=False)
        self.assertNotIn("Map", overrides.added)

        overrides.reset("Map")
        self.assertEqual(overrides, MachineOverrides())


class FakeWorld:
    """Spawns fake cabinets, and remembers what happened."""

    def __init__(self) -> None:
        self.alive: set[int] = set()
        self.spawned: list[tuple[str, Spot]] = []
        self.destroyed: list[int] = []
        self.fail_at: set[Spot] = set()
        self.logs: list[str] = []
        self._next = 1

    def spawn(self, source: VendingMachine, spot: Spot) -> int | None:
        if spot in self.fail_at:
            return None
        handle = self._next
        self._next += 1
        self.alive.add(handle)
        self.spawned.append((source.key, spot))
        return handle

    def destroy(self, handle: int) -> None:
        self.alive.discard(handle)
        self.destroyed.append(handle)

    def keeper(self) -> CabinetKeeper[int]:
        return CabinetKeeper(self.spawn, self.destroy, lambda h: h in self.alive, log=self.logs.append)


class KeeperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = FakeWorld()
        self.keeper = self.world.keeper()
        self.overrides = MachineOverrides()

    def test_one_per_group(self) -> None:
        machines = [*row("a", 2), *row("b", 2, x=5000), *row("c", 2, x=10_000)]
        self.keeper.update("Map", machines, self.overrides)
        # Spawning is spread out over updates
        self.assertEqual(len(self.keeper.cabinets), MAX_SPAWNS_PER_UPDATE)
        self.keeper.update("Map", machines, self.overrides)
        self.keeper.update("Map", machines, self.overrides)
        self.assertEqual(len(self.keeper.cabinets), 3)
        self.assertEqual(len(self.world.spawned), 3)
        self.assertTrue(all(c.automatic for c in self.keeper.cabinets))

    def test_streaming(self) -> None:
        safehouse = row("a", 2)
        self.keeper.update("Map", safehouse, self.overrides)
        # The safehouse streams out, then back in - the machine stays put
        self.keeper.update("Map", [], self.overrides)
        self.keeper.update("Map", safehouse, self.overrides)
        self.assertEqual(len(self.world.spawned), 1)
        self.assertEqual(len(self.keeper.cabinets), 1)

    def test_replaces_machines_the_game_cleaned_up(self) -> None:
        safehouse = row("a", 2)
        self.keeper.update("Map", safehouse, self.overrides)
        self.world.alive.clear()
        self.keeper.update("Map", safehouse, self.overrides)
        self.assertEqual(len(self.world.spawned), 2)
        self.assertEqual(self.world.spawned[0][1], self.world.spawned[1][1])

    def test_map_change(self) -> None:
        self.keeper.update("Map_A", row("a", 2), self.overrides)
        self.keeper.update("Map_B", row("b", 2, x=3000), self.overrides)
        self.assertEqual(self.keeper.map_name, "Map_B")
        self.assertEqual(len(self.keeper.cabinets), 1)
        self.assertEqual(self.keeper.cabinets[0].source.key, "b1")
        # The old map's actors are already gone, so nothing is destroyed by hand
        self.assertEqual(self.world.destroyed, [])

    def test_remove_moves_it_along(self) -> None:
        safehouse = row("a", 2)
        self.keeper.update("Map", safehouse, self.overrides)
        first = self.keeper.cabinets[0]
        self.overrides.remove("Map", first.spot, automatic=True)
        self.keeper.update("Map", safehouse, self.overrides)
        self.assertEqual(self.world.destroyed, [first.handle])
        self.assertEqual(len(self.keeper.cabinets), 1)
        self.assertNotEqual(self.keeper.cabinets[0].spot, first.spot)

    def test_hand_placed(self) -> None:
        safehouse = row("a", 2)
        spot = self.overrides.add("Map", Spot(3000, 0, 0, 90))
        self.keeper.update("Map", safehouse, self.overrides)
        manual = [c for c in self.keeper.cabinets if not c.automatic]
        self.assertEqual([(c.spot, c.source.key) for c in manual], [(spot, "a0")])

        self.overrides.remove("Map", spot, automatic=False)
        self.keeper.update("Map", safehouse, self.overrides)
        self.assertEqual([c.automatic for c in self.keeper.cabinets], [True])

    def test_hand_placed_waits_for_a_machine_to_copy(self) -> None:
        self.overrides.add("Map", Spot(0, 0, 0))
        self.keeper.update("Map", [], self.overrides)
        self.assertEqual(self.keeper.cabinets, [])
        self.keeper.update("Map", row("a", 1), self.overrides)
        self.assertEqual(len(self.keeper.cabinets), 1)

    def test_hand_placed_serves_its_group(self) -> None:
        safehouse = row("a", 2)
        self.overrides.add("Map", Spot(-300, 0, 0))
        self.keeper.update("Map", safehouse, self.overrides)
        self.assertEqual([c.automatic for c in self.keeper.cabinets], [False])

    def test_failed_spots_fall_back(self) -> None:
        safehouse = row("a", 2)
        first, second = (p.spot for p in plan_placements(safehouse, [], [])[0])
        self.world.fail_at.add(first)
        self.keeper.update("Map", safehouse, self.overrides)
        self.assertEqual(self.keeper.cabinets, [])
        self.keeper.update("Map", safehouse, self.overrides)
        self.assertEqual([c.spot for c in self.keeper.cabinets], [second])

        # Clearing forgets failures
        self.world.fail_at.clear()
        self.keeper.clear()
        self.keeper.update("Map", safehouse, self.overrides)
        self.assertEqual([c.spot for c in self.keeper.cabinets], [first])

    def test_spawn_errors_are_logged(self) -> None:
        def explode(_source: VendingMachine, _spot: Spot) -> int:
            raise RuntimeError("boom")

        keeper = CabinetKeeper(explode, self.world.destroy, lambda _: True, log=self.world.logs.append)
        keeper.update("Map", row("a", 2), self.overrides)
        self.assertEqual(keeper.cabinets, [])
        self.assertIn("boom", self.world.logs[0])

    def test_makes_way_for_late_vending_machines(self) -> None:
        safehouse = row("a", 2)
        self.keeper.update("Map", safehouse, self.overrides)
        first = self.keeper.cabinets[0]
        # The rest of the row loads in, right where the slot machine went
        longer = row("a", 4)
        self.keeper.update("Map", longer, self.overrides)
        self.assertIn(first.handle, self.world.destroyed)
        self.assertEqual(len(self.keeper.cabinets), 1)
        spot = self.keeper.cabinets[0].spot
        for m in longer:
            self.assertGreaterEqual(math.dist((spot.x, spot.y), (m.x, m.y)), 120)

    def test_cap(self) -> None:
        machines = [m for i in range(10) for m in row(f"g{i}", 1, x=i * 5000.0)]
        with mock.patch.object(cabinets, "MAX_CABINETS", 3):
            for _ in range(10):
                self.keeper.update("Map", machines, self.overrides)
        self.assertEqual(len(self.keeper.cabinets), 3)

    def test_clear_and_nearest(self) -> None:
        self.keeper.update("Map", [*row("a", 2), *row("b", 2, x=5000)], self.overrides)
        nearest = self.keeper.nearest(5000, 0, 0)
        assert nearest is not None
        self.assertEqual(nearest[0].source.key[0], "b")
        self.keeper.clear()
        self.assertEqual(self.keeper.cabinets, [])
        self.assertEqual(len(self.world.destroyed), 2)
        self.assertIsNone(self.keeper.nearest(0, 0, 0))


if __name__ == "__main__":
    unittest.main()
