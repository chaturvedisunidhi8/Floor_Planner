"""Tests for Milestone B: doors, windows and walkability over a solved plan.

The solver packs rooms with no idea of circulation; these passes read the
finished geometry and model the openings that connect it. The invariants here
are the ones the scoring and the validation gate depend on: a door sits on the
centreline of a shared wall, a window sits on a room's external wall, the door
graph over the modeled doors equals shared-wall adjacency, and an en-suite is
the one thing you may walk through a bedroom to reach.
"""

from __future__ import annotations

import networkx as nx

from app.geometry.connectivity import adjacency_graph, door_rooms, stranded_indices
from app.geometry.doors import DOOR_WIDTH, model_doors
from app.geometry.envelope import Envelope
from app.geometry.models import Door, Plan, Room, Window
from app.geometry.validation import validate_plan
from app.geometry.windows import MAX_WIDTH, model_windows
from app.schemas.enums import RoomType


def _plan(
    rooms: list[tuple[RoomType, str, float, float, float, float]],
    width: float = 40,
    length: float = 40,
) -> Plan:
    return Plan(
        rooms=[Room(*args) for args in rooms],
        plot_width=width,
        plot_length=length,
    )


# --- model_doors ------------------------------------------------------------

def test_model_doors_one_per_shared_wall_centered() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.KITCHEN, "Kitchen", 0, 20, 20, 10),
        ]
    )
    doors = model_doors(plan)
    assert len(doors) == 1
    door = doors[0]
    # Shared wall y=20, run x in [0,20]: centred, full width, on the line.
    assert door.orientation == "horizontal"
    assert door.y == 20.0
    assert door.width == DOOR_WIDTH
    assert abs(door.x + door.width / 2 - 10.0) < 1e-9  # centred on the run


def test_model_doors_clamps_width_to_short_run() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.KITCHEN, "Kitchen", 20, 0, 20, 2.5),
        ]
    )
    doors = model_doors(plan)
    assert len(doors) == 1
    assert doors[0].width == 2.5  # the whole run


def test_model_doors_skips_walls_too_short() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.KITCHEN, "Kitchen", 20, 0, 20, 2.0),
        ]
    )
    assert model_doors(plan) == []


def test_model_doors_skips_outdoor_outdoor() -> None:
    # Balcony and parking share a wall long enough for a door, but the pair is
    # skipped (a balcony does not give access to the parking) so only the
    # living-balcony door survives.
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.BALCONY, "Balcony", 20, 0, 10, 20),
            (RoomType.PARKING, "Parking", 20, 20, 10, 10),
        ]
    )
    doors = model_doors(plan)
    types = {(d.room_from, d.room_to) for d in doors}
    assert (RoomType.BALCONY, RoomType.PARKING) not in types
    assert len(doors) == 1


def test_model_doors_vertical_wall_coordinates() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.MASTER_BEDROOM, "Master", 20, 0, 20, 20),
        ]
    )
    door = model_doors(plan)[0]
    assert door.orientation == "vertical"
    assert door.x == 20.0  # on the centreline of the shared x=20 wall
    assert abs(door.y + door.width / 2 - 10.0) < 1e-9


# --- model_windows ----------------------------------------------------------

def test_model_windows_on_external_walls_centered() -> None:
    plan = _plan([(RoomType.LIVING_ROOM, "Living", 0, 0, 10, 10)], width=30, length=30)
    windows = model_windows(plan)
    # A 10x10 room in the corner: left and bottom walls qualify (each run 10).
    assert len(windows) == 2
    by_orient = {w.orientation: w for w in windows}
    left = by_orient["vertical"]
    assert left.x == 0.0
    width = min(MAX_WIDTH, 10 * 0.45)
    assert left.width == width
    assert abs(left.y + width / 2 - 5.0) < 1e-9
    bottom = by_orient["horizontal"]
    assert bottom.y == 0.0
    assert abs(bottom.x + width / 2 - 5.0) < 1e-9


def test_model_windows_none_for_interior_room() -> None:
    plan = _plan([(RoomType.MASTER_BEDROOM, "Master", 10, 10, 10, 10)], width=30, length=30)
    assert model_windows(plan) == []


def test_model_windows_skips_parking_and_garden() -> None:
    plan = _plan(
        [
            (RoomType.PARKING, "Parking", 0, 0, 10, 10),
            (RoomType.GARDEN, "Garden", 10, 0, 10, 10),
        ],
        width=30,
        length=30,
    )
    assert model_windows(plan) == []


def test_model_windows_none_for_short_external_wall() -> None:
    plan = _plan([(RoomType.KITCHEN, "Kitchen", 0, 0, 5, 5)], width=30, length=30)
    assert model_windows(plan) == []


# --- connectivity -----------------------------------------------------------

def test_adjacency_graph_matches_shared_walls() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.BALCONY, "Balcony", 20, 0, 10, 20),
            (RoomType.PARKING, "Parking", 0, 20, 20, 10),
        ]
    )
    graph = adjacency_graph(plan)
    assert set(graph.edges()) == {(0, 1), (0, 2)}  # outdoor-outdoor excluded
    assert graph[0][1]["run"] == 20.0


def test_door_rooms_resolves_a_modeled_door() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.KITCHEN, "Kitchen", 20, 0, 20, 20),
        ]
    )
    door = model_doors(plan)[0]
    assert door_rooms(door, plan.rooms) == (0, 1)
    # A door floating in mid-room resolves to nothing.
    stray = Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, 5.0, 5.0, 3.0, "vertical")
    assert door_rooms(stray, plan.rooms) is None


def test_door_graph_equals_adjacency_when_doors_modeled() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.KITCHEN, "Kitchen", 0, 20, 20, 10),
            (RoomType.MASTER_BEDROOM, "Master", 20, 20, 20, 10),
        ]
    )
    plan.doors = model_doors(plan)
    from app.geometry.connectivity import walkable_graph

    assert nx.is_isomorphic(walkable_graph(plan), adjacency_graph(plan))


def test_stranded_en_suite_is_reachable_through_its_bedroom() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.MASTER_BEDROOM, "Master", 20, 0, 20, 20),
            (RoomType.ATTACHED_BATHROOM, "Attached", 20, 20, 10, 10),
        ]
    )
    plan.doors = model_doors(plan)
    assert stranded_indices(plan) == []


def test_stranded_kitchen_behind_a_bedroom_is_not_connected() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.MASTER_BEDROOM, "Master", 20, 0, 20, 20),
            (RoomType.KITCHEN, "Kitchen", 20, 20, 20, 15),
        ]
    )
    plan.doors = model_doors(plan)
    assert stranded_indices(plan) == [2]


# --- validation gate --------------------------------------------------------

def test_validate_plan_accepts_modeled_openings() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.KITCHEN, "Kitchen", 0, 20, 20, 10),
            (RoomType.BALCONY, "Balcony", 20, 0, 10, 20),
        ],
        width=30,
        length=30,
    )
    plan.doors = model_doors(plan)
    plan.windows = model_windows(plan)
    envelope = Envelope(30, 30)
    from app.geometry.units import max_area, min_area, min_side

    specs = []
    for room in plan.rooms:
        from app.geometry.solver.topology import RoomSpec

        specs.append(
            RoomSpec(
                type=room.type,
                name=room.name,
                target_area=min_area(room.type),
                min_side=min_side(room.type),
                min_area=min_area(room.type),
                max_area=max_area(room.type),
                outdoor=room.type.is_outdoor,
            )
        )
    report = validate_plan(plan, envelope, specs)
    assert report.ok, report.errors


def test_validate_plan_rejects_door_off_a_shared_wall() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.KITCHEN, "Kitchen", 0, 20, 20, 10),
        ]
    )
    plan.doors = [Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, 5.0, 15.0, 3.0, "vertical")]
    envelope = Envelope(30, 30)
    specs = [
        _spec_room(RoomType.LIVING_ROOM, "Living"),
        _spec_room(RoomType.KITCHEN, "Kitchen"),
    ]
    report = validate_plan(plan, envelope, specs)
    assert not report.ok
    assert any("does not sit on a shared wall" in e for e in report.errors)


def test_validate_plan_rejects_window_off_an_external_wall() -> None:
    plan = _plan(
        [
            (RoomType.MASTER_BEDROOM, "Master", 10, 10, 10, 10),
        ],
        width=30,
        length=30,
    )
    plan.windows = [Window(RoomType.MASTER_BEDROOM, 10.0, 12.0, 4.0, "vertical")]
    report = validate_plan(plan, Envelope(30, 30), [_spec_room(RoomType.MASTER_BEDROOM, "Master")])
    assert not report.ok
    assert any("does not sit on an external wall" in e for e in report.errors)


def test_validate_plan_rejects_overlapping_doors() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.KITCHEN, "Kitchen", 0, 20, 20, 10),
        ]
    )
    plan.doors = [
        Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, 2.0, 20.0, 4.0, "horizontal"),
        Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, 5.0, 20.0, 4.0, "horizontal"),
    ]
    report = validate_plan(plan, Envelope(30, 30), [_spec_room(RoomType.LIVING_ROOM, "Living"),
                                                     _spec_room(RoomType.KITCHEN, "Kitchen")])
    assert not report.ok
    assert any("overlaps" in e for e in report.errors)


def test_validate_plan_warns_missing_window_on_exterior_room() -> None:
    plan = _plan(
        [
            (RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
            (RoomType.KITCHEN, "Kitchen", 0, 20, 20, 10),
        ],
        width=30,
        length=30,
    )
    # Only the living room gets a window; the kitchen has none.
    plan.windows = [Window(RoomType.LIVING_ROOM, 0.0, 8.0, 4.0, "vertical")]
    plan.doors = model_doors(plan)
    report = validate_plan(plan, Envelope(30, 30), [_spec_room(RoomType.LIVING_ROOM, "Living"),
                                                     _spec_room(RoomType.KITCHEN, "Kitchen")])
    assert report.ok
    assert any("Kitchen has no window" in w for w in report.warnings)


def _spec_room(room_type: RoomType, name: str):
    from app.geometry.solver.topology import RoomSpec
    from app.geometry.units import max_area, min_area, min_side

    return RoomSpec(
        type=room_type,
        name=name,
        target_area=min_area(room_type),
        min_side=min_side(room_type),
        min_area=min_area(room_type),
        max_area=max_area(room_type),
        outdoor=room_type.is_outdoor,
    )
