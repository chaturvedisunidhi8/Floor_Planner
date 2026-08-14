"""Milestone E/F: the wall model is the physical geometry milestone.

The solved plan is a set of gross rectangles; ``build_wall_model`` turns those
into real walls - centered internal bands on shared boundaries, full-thickness
external bands on the buildable boundary - and the area ledger is the contract:

    gross_area == clear_area + wall_area

exactly, not approximately.
"""

from __future__ import annotations

import pytest
from shapely import box

from app.geometry.models import Plan
from app.geometry.primitives import Rect
from app.geometry.walls import (
    WALLS,
    BuildableBoundary,
    PlotBoundary,
    WallConfig,
    build_wall_model,
    validate_walls,
)
from app.schemas.enums import BHKType, Facing, InteriorStyle, RoomType
from app.schemas.requirements import RoomDimensions

# --- Configuration ---------------------------------------------------------


def test_wall_config_has_the_milestone_defaults() -> None:
    config = WallConfig()
    assert config.external_wall_thickness == 0.75
    assert config.internal_wall_thickness == 0.5
    assert config.partition_thickness == 0.4
    assert config.door_width == 3.0
    assert config.window_width == 5.0
    assert config.geometry_tolerance == 1e-6


def test_the_module_singleton_is_the_default_config() -> None:
    assert WallConfig() == WALLS
    assert WALLS.internal_wall_thickness == 0.5


def test_boundaries_report_the_right_areas() -> None:
    plot = PlotBoundary(30, 45)
    buildable = BuildableBoundary(28, 43)
    assert plot.area == pytest.approx(1350.0)
    assert buildable.area == pytest.approx(1204.0)
    assert plot.width == 30 and plot.length == 45


# --- Two rooms side by side ------------------------------------------------


def _two_rooms():
    return [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
        Rect(RoomType.KITCHEN, "Kitchen", 20, 0, 20, 20),
    ]


def test_shared_wall_is_centered_on_the_boundary() -> None:
    model = build_wall_model(_two_rooms(), plot_width=40, plot_length=20)
    internal = [s for s in model.segments if s.kind == "internal"]
    assert len(internal) == 1
    wall = internal[0]
    assert wall.thickness == WALLS.internal_wall_thickness
    x_min, y_min, x_max, y_max = wall.polygon.bounds
    assert x_min == pytest.approx(20 - WALLS.internal_wall_thickness / 2)
    assert x_max == pytest.approx(20 + WALLS.internal_wall_thickness / 2)
    assert y_min == pytest.approx(0)
    assert y_max == pytest.approx(20)
    assert wall.rooms == ("Living", "Kitchen")


def test_external_walls_cover_the_plot_perimeter() -> None:
    model = build_wall_model(_two_rooms(), plot_width=40, plot_length=20)
    external = [s for s in model.segments if s.kind == "external"]
    assert len(external) >= 4
    for segment in external:
        assert segment.thickness == WALLS.external_wall_thickness
        assert model.plot_boundary.polygon.covers(segment.polygon)
    # The bands together cover the whole plot boundary ring.
    external_union = model.external_walls
    assert external_union is not None
    assert external_union.covers(model.plot_boundary.polygon.boundary)


def test_external_wall_area_matches_the_perimeter_band() -> None:
    model = build_wall_model(_two_rooms(), plot_width=40, plot_length=20)
    t = WALLS.external_wall_thickness
    # A full-thickness band around the perimeter, corners counted once.
    assert model.external_wall_area == pytest.approx(2 * t * (40 + 20) - 4 * t * t)


def test_partitions_are_thinner_than_internal_walls() -> None:
    rooms = [
        Rect(RoomType.ATTACHED_BATHROOM, "Bath", 0, 0, 8, 8),
        Rect(RoomType.MASTER_BEDROOM, "Master", 8, 0, 14, 14),
    ]
    model = build_wall_model(rooms, plot_width=22, plot_length=14)
    internal = [s for s in model.segments if s.kind == "internal"]
    assert len(internal) == 1
    assert internal[0].thickness == WALLS.partition_thickness


def test_a_wall_never_floats_in_a_gap() -> None:
    """Two rooms 0.5 ft apart count as sharing a wall for connectivity, but the
    wall band must be clipped to the rooms - nothing may hang in open space."""
    rooms = [
        Rect(RoomType.MASTER_BEDROOM, "Master", 0, 10, 10, 10),
        Rect(RoomType.BALCONY, "Balcony", 0, 0, 8, 9.5),
    ]
    model = build_wall_model(rooms, plot_width=10, plot_length=20)
    for segment in model.segments:
        assert model.plot_boundary.polygon.covers(segment.polygon)
        assert segment.polygon.area > 0
    # The 0.5 ft gap between the rooms carries no wall at all.
    gap = box(0, 9.5, 10, 10)
    for segment in model.segments:
        assert not segment.polygon.overlaps(gap)


# --- The area ledger -------------------------------------------------------


def test_ledger_reconciles_exactly() -> None:
    model = build_wall_model(_two_rooms(), plot_width=40, plot_length=20)
    assert model.gross_area == pytest.approx(800.0)
    assert model.clear_area + model.wall_area == pytest.approx(model.gross_area, abs=1e-6)


def test_ledger_reconciles_with_partitions_and_externals() -> None:
    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 20, 20),
        Rect(RoomType.KITCHEN, "Kitchen", 20, 0, 20, 20),
        Rect(RoomType.ATTACHED_BATHROOM, "Bath", 0, 20, 8, 6),
    ]
    model = build_wall_model(rooms, plot_width=40, plot_length=26)
    assert model.clear_area + model.wall_area == pytest.approx(model.gross_area, abs=1e-6)
    assert model.wall_area == pytest.approx(
        model.external_wall_area + model.internal_wall_area, abs=1e-6
    )


def test_clear_polygon_is_the_room_minus_its_walls() -> None:
    model = build_wall_model(_two_rooms(), plot_width=40, plot_length=20)
    living = model.clear_polygons[0]
    assert living.area < 400.0  # the external and internal walls are carved out
    assert living.area == pytest.approx(400.0 - model.wall_area / 2, abs=0.6)
    assert living.geom_type == "Polygon"


def test_uncovered_area_is_what_the_rooms_leave_out() -> None:
    rooms = [Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 10, 20)]
    model = build_wall_model(rooms, plot_width=20, plot_length=20)
    assert model.uncovered_area == pytest.approx(200.0)
    assert model.uncovered_fraction == pytest.approx(0.5)


def test_uncovered_is_zero_when_the_plot_is_tiled() -> None:
    model = build_wall_model(_two_rooms(), plot_width=40, plot_length=20)
    assert model.uncovered_area == pytest.approx(0.0)


# --- Validation ------------------------------------------------------------


def test_validate_walls_passes_a_clean_model() -> None:
    model = build_wall_model(_two_rooms(), plot_width=40, plot_length=20)
    assert validate_walls(model) == []


def test_validate_walls_flags_a_ledger_mismatch() -> None:
    model = build_wall_model(_two_rooms(), plot_width=40, plot_length=20)
    model.clear_area += 10.0
    errors = validate_walls(model)
    assert any("reconcile" in error for error in errors)


def test_validate_walls_flags_walls_outside_the_plot() -> None:
    model = build_wall_model(_two_rooms(), plot_width=40, plot_length=20)
    model.segments[0] = model.segments[0].__class__(
        "external",
        box(39, 0, 41, 20),
        model.segments[0].line,
        model.segments[0].thickness,
    )
    errors = validate_walls(model)
    assert any("outside the plot" in error for error in errors)


# --- Solver integration ----------------------------------------------------


def _solved_plan(repository, template_id: str = "TPL-001", seed: int = 5):
    from app.geometry.envelope import Envelope
    from app.geometry.solver import cp_sat
    from app.geometry.solver.topology import candidate_programmes
    from app.schemas.requirements import (
        BathroomRequirements,
        FloorPlanRequirements,
        PlotDetails,
    )

    requirements = FloorPlanRequirements(
        plot=PlotDetails(width_ft=25, length_ft=50, facing=Facing.EAST),
        bhk=BHKType.BHK2,
        rooms=[RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN],
        bathrooms=BathroomRequirements(attached_count=2, common_count=1),
        features=[RoomType.BALCONY],
        room_dimensions={
            RoomType.LIVING_ROOM: RoomDimensions(length_ft=18, width_ft=14),
            RoomType.DINING_ROOM: RoomDimensions(length_ft=12, width_ft=10),
            RoomType.KITCHEN: RoomDimensions(length_ft=10, width_ft=9),
        },
        style=InteriorStyle.MODERN,
    )
    template = repository.get(template_id)
    programme = candidate_programmes(requirements, template)[0]
    outcome = cp_sat.solve(
        programme.specs,
        Envelope(25, 50),
        seed=seed,
        time_limit=1.5,
        access_requirements=programme.access_requirements,
    )
    return Plan(rooms=outcome.rooms, plot_width=25, plot_length=50)


def test_solved_plan_carries_consistent_walls(repository) -> None:
    plan = _solved_plan(repository)
    model = build_wall_model(plan.rooms, plot_width=25, plot_length=50)
    assert validate_walls(model) == []
    assert model.clear_area + model.wall_area == pytest.approx(model.gross_area, abs=1e-6)
    # Every room kept a usable interior.
    assert all(clear.area > 1.0 for clear in model.clear_polygons.values())


def test_solver_plan_wall_model_reconciles_across_seeds(repository) -> None:
    for seed in range(1, 4):
        plan = _solved_plan(repository, seed=seed)
        model = build_wall_model(plan.rooms, plot_width=25, plot_length=50)
        assert validate_walls(model) == [], f"seed {seed}: {validate_walls(model)}"


def test_plan_area_properties_follow_the_wall_model(repository) -> None:
    plan = _solved_plan(repository)
    model = build_wall_model(plan.rooms, plot_width=25, plot_length=50)
    plan.walls = model
    assert plan.gross_area == pytest.approx(model.gross_area)
    assert plan.clear_area == pytest.approx(model.clear_area)
    assert plan.wall_area == pytest.approx(model.wall_area)
    assert plan.gross_area == pytest.approx(
        sum(room.area for room in plan.rooms), abs=1e-6
    )
