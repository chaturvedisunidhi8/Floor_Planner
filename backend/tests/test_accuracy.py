"""Tests for the geometry-accuracy layer and the score split.

The milestone's accuracy bet has two halves. ``accuracy.py`` must *measure*
the solved geometry exactly - sized rooms hitting their areas and sides,
edges on the grid, walls aligned, the area ledger reconciling, labels
round-tripping - and ``scoring.py`` must blend that measurement into the
score as ``0.7 x architecture + 0.3 x geometry`` so a dimensionally sloppy
plan cannot pass for beautiful. These tests pin both halves on hand-built
plans so the numbers are unambiguous.
"""

from __future__ import annotations

from app.geometry.accuracy import accuracy_metrics, geometry_score
from app.geometry.labels import decode_feet_inches, feet_inches
from app.geometry.models import Door, Plan, Room, Window
from app.geometry.quality import FURNITURE, quality_metrics
from app.geometry.scoring import score_plan
from app.schemas.enums import BHKType, Facing, RoomType
from app.schemas.requirements import (
    FloorPlanRequirements,
    PlotDetails,
    RoomDimensions,
)


def _plan(
    rooms: list[Room],
    *,
    doors=None,
    windows=None,
    width: float = 30,
    length: float = 45,
) -> Plan:
    plan = Plan(rooms=rooms, plot_width=width, plot_length=length)
    plan.doors = doors or []
    plan.windows = windows or []
    return plan


def _room(room_type: RoomType, x, y, w, h, name: str | None = None) -> Room:
    return Room(room_type, name or room_type.label, x, y, w, h)


def _requirements(*, master: tuple[float, float]) -> FloorPlanRequirements:
    return FloorPlanRequirements(
        plot=PlotDetails(width_ft=30, length_ft=45, facing=Facing.NORTH),
        bhk=BHKType.BHK3,
        rooms=[
            RoomType.LIVING_ROOM,
            RoomType.DINING_ROOM,
            RoomType.KITCHEN,
            RoomType.MASTER_BEDROOM,
            RoomType.GUEST_BEDROOM,
            RoomType.CHILDREN_BEDROOM,
        ],
        room_dimensions={
            RoomType.MASTER_BEDROOM: RoomDimensions(length_ft=master[0], width_ft=master[1]),
        },
    )


# --- labels round-trip -------------------------------------------------------


def test_feet_inches_round_trips_half_foot_grid() -> None:
    for value in (12.0, 12.5, 9.5, 4.0, 20.0):
        assert decode_feet_inches(feet_inches(value)) == value


def test_feet_inches_nearest_inch_carries_over() -> None:
    assert feet_inches(12.96) == "13'"
    assert feet_inches(12.0) == "12'"
    assert feet_inches(12.5) == "12'6\""
    assert decode_feet_inches("12'6\"") == 12.5


# --- accuracy_metrics --------------------------------------------------------


def test_perfect_plan_metrics_are_clean() -> None:
    rooms = [
        _room(RoomType.LIVING_ROOM, 0, 0, 18, 12),
        _room(RoomType.KITCHEN, 18, 0, 12, 10),
        _room(RoomType.MASTER_BEDROOM, 0, 12, 14, 12),
        _room(RoomType.PASSAGE, 14, 12, 6, 12),
        _room(RoomType.GUEST_BEDROOM, 20, 12, 10, 12),
        _room(RoomType.ATTACHED_BATHROOM, 14, 24, 6, 8),
    ]
    requirements = _requirements(master=(14, 12))
    plan = _plan(rooms)
    plan.doors = [  # living -> kitchen/passage -> bedrooms keeps everyone reachable
        Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, 18, 2, orientation="vertical"),
        Door(RoomType.LIVING_ROOM, RoomType.PASSAGE, 15, 12, orientation="horizontal"),
        Door(RoomType.PASSAGE, RoomType.MASTER_BEDROOM, 14, 14, orientation="vertical"),
        Door(RoomType.PASSAGE, RoomType.GUEST_BEDROOM, 20, 14, orientation="vertical"),
        Door(RoomType.PASSAGE, RoomType.ATTACHED_BATHROOM, 16, 24, orientation="horizontal"),
    ]
    metrics = accuracy_metrics(plan, requirements)
    assert metrics.area_err == 0.0
    assert metrics.dim_err == 0.0
    assert metrics.stranded_rooms == 0
    assert metrics.stray_edges == 0
    assert metrics.off_grid_edges == 0
    assert metrics.ledger_ok is None  # no wall model in a hand-built plan
    assert metrics.label_mismatches == 0


def test_stray_and_off_grid_edges_are_counted() -> None:
    plan = _plan(
        [
            _room(RoomType.LIVING_ROOM, 0, 0, 18, 12),
            _room(RoomType.KITCHEN, 18.4, 0, 12, 10),  # off-grid edge at 18.4
            _room(RoomType.MASTER_BEDROOM, 0, 12, 14.3, 12),  # 14.3 off-grid
        ]
    )
    metrics = accuracy_metrics(plan, _requirements(master=(14, 12)))
    assert metrics.off_grid_edges == 3  # 18.4, 30.4, 14.3
    assert metrics.stray_edges >= 1


def test_geometry_score_flags_a_ribbon_room() -> None:
    """Same area, wrong shape: the dimension axis must catch it."""
    rooms = [
        _room(RoomType.LIVING_ROOM, 0, 0, 18, 12),
        _room(RoomType.KITCHEN, 18, 0, 12, 10),
        _room(RoomType.MASTER_BEDROOM, 0, 12, 6, 28),  # 168 sqft ribbon
        _room(RoomType.PASSAGE, 14, 12, 6, 12),
        _room(RoomType.GUEST_BEDROOM, 20, 12, 10, 12),
        _room(RoomType.ATTACHED_BATHROOM, 14, 24, 6, 8),
    ]
    requirements = _requirements(master=(14, 12))
    plan = _plan(rooms)
    plan.doors = [
        Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, 18, 2, orientation="vertical"),
        Door(RoomType.LIVING_ROOM, RoomType.PASSAGE, 15, 12, orientation="horizontal"),
        Door(RoomType.PASSAGE, RoomType.MASTER_BEDROOM, 14, 14, orientation="vertical"),
        Door(RoomType.PASSAGE, RoomType.GUEST_BEDROOM, 20, 14, orientation="vertical"),
        Door(RoomType.PASSAGE, RoomType.ATTACHED_BATHROOM, 16, 24, orientation="horizontal"),
    ]
    components = geometry_score(plan, requirements)
    assert components["area"] > 90.0  # 168 sqft still on target
    assert components["dimensions"] < 70.0  # but 6x28 is not 14x12
    assert components["dimensions"] < components["area"]


# --- furniture feasibility ---------------------------------------------------


def test_furniture_table_covers_the_standard_rooms() -> None:
    for room_type in (
        RoomType.MASTER_BEDROOM,
        RoomType.GUEST_BEDROOM,
        RoomType.CHILDREN_BEDROOM,
        RoomType.LIVING_ROOM,
        RoomType.DINING_ROOM,
        RoomType.KITCHEN,
        RoomType.ATTACHED_BATHROOM,
        RoomType.COMMON_BATHROOM,
    ):
        assert room_type in FURNITURE


def test_narrow_rooms_are_furniture_shortfalls() -> None:
    plan = _plan(
        [
            _room(RoomType.MASTER_BEDROOM, 0, 0, 8, 9),  # 8 ft too narrow
            _room(RoomType.ATTACHED_BATHROOM, 12, 0, 4, 8),  # 4 ft slot
            _room(RoomType.LIVING_ROOM, 0, 12, 16, 14),
        ]
    )
    metrics = quality_metrics(plan)
    assert metrics.furniture_rooms == 3
    assert metrics.furniture_shortfalls == 2


def test_generous_rooms_have_no_furniture_shortfalls() -> None:
    plan = _plan(
        [
            _room(RoomType.MASTER_BEDROOM, 0, 0, 14, 12),
            _room(RoomType.ATTACHED_BATHROOM, 14, 0, 6, 8),
            _room(RoomType.LIVING_ROOM, 0, 12, 16, 14),
        ]
    )
    metrics = quality_metrics(plan)
    assert metrics.furniture_shortfalls == 0


# --- score split -------------------------------------------------------------


def test_score_plan_blends_architecture_and_geometry() -> None:
    rooms = [
        _room(RoomType.LIVING_ROOM, 0, 0, 18, 12),
        _room(RoomType.KITCHEN, 18, 0, 12, 10),
        _room(RoomType.MASTER_BEDROOM, 0, 12, 14, 12),
        _room(RoomType.PASSAGE, 14, 12, 6, 12),
        _room(RoomType.GUEST_BEDROOM, 20, 12, 10, 12),
        _room(RoomType.ATTACHED_BATHROOM, 14, 24, 6, 8),
    ]
    requirements = _requirements(master=(14, 12))
    plan = _plan(rooms)
    plan.doors = [
        Door(RoomType.LIVING_ROOM, RoomType.PASSAGE, 17, 15),
        Door(RoomType.PASSAGE, RoomType.MASTER_BEDROOM, 16, 12),
        Door(RoomType.PASSAGE, RoomType.GUEST_BEDROOM, 21, 12),
        Door(RoomType.PASSAGE, RoomType.ATTACHED_BATHROOM, 17, 24),
    ]
    plan.windows = [
        Window(RoomType.LIVING_ROOM, 0, 2),
        Window(RoomType.KITCHEN, 30, 4),
        Window(RoomType.MASTER_BEDROOM, 0, 15),
        Window(RoomType.GUEST_BEDROOM, 30, 15),
    ]
    scores = score_plan(plan, requirements)
    assert 0.0 <= scores.architecture <= 100.0
    assert 0.0 <= scores.geometry <= 100.0
    expected = round(0.7 * scores.architecture + 0.3 * scores.geometry, 1)
    assert scores.total == expected
    # A corridor-heavy, ribbony plan should not beat a clean one on geometry.
    assert scores.components["furniture"] > 90.0
