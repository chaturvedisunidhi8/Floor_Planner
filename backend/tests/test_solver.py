"""Tests for the CP-SAT geometry solver (Milestone A).

These are the invariants the re-engineering hinges on: the solver packs rooms
with no overlap and every hard floor met, it refuses infeasible briefs with a
`status: "infeasible"` card and diagnostics instead of silently shrinking them,
and it is deterministic for a fixed (brief, seed).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import GeometryEngine, get_settings
from app.geometry.envelope import Envelope
from app.geometry.layout_engine import LayoutEngine
from app.geometry.models import Plan, Room
from app.geometry.solver.cp_sat import solve
from app.geometry.solver.infeasibility import diagnose_brief, diagnose_solver
from app.geometry.solver.topology import RoomSpec, programme_from_brief
from app.geometry.units import area_to_cells, to_cells, to_ft
from app.geometry.validation import validate_plan
from app.main import create_app
from app.schemas.enums import BHKType, Facing, InteriorStyle, RoomType
from app.schemas.requirements import (
    BathroomRequirements,
    FloorPlanRequirements,
    PlotDetails,
    RoomDimensions,
)

PREFIX = "/api/v1"
SOLVER_BUDGET = 2.0


def _req(
    width: float = 30,
    length: float = 45,
    bhk: BHKType = BHKType.BHK3,
    rooms: list[RoomType] | None = None,
    baths: tuple[int, int] = (2, 1),
    features: list[RoomType] | None = None,
    dims: dict[RoomType, RoomDimensions] | None = None,
) -> FloorPlanRequirements:
    return FloorPlanRequirements(
        plot=PlotDetails(width_ft=width, length_ft=length, facing=Facing.EAST),
        bhk=bhk,
        rooms=rooms or [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN],
        bathrooms=BathroomRequirements(attached_count=baths[0], common_count=baths[1]),
        features=features or [],
        room_dimensions=dims or {},
        style=InteriorStyle.MODERN,
    )


def _spec(room_type: RoomType, **overrides) -> RoomSpec:
    """A RoomSpec with sensible floors for the type, overridable."""
    from app.geometry.units import max_area, min_area, min_side, natural_area

    base = {
        "type": room_type,
        "name": room_type.label,
        "target_area": natural_area(room_type),
        "min_side": min_side(room_type),
        "min_area": min_area(room_type),
        "max_area": max_area(room_type),
        "outdoor": room_type.is_outdoor,
    }
    base.update(overrides)
    return RoomSpec(**base)


def _sized(room_type: RoomType, long_: float, short_: float) -> RoomSpec:
    """A brief-sized room: exact long/short sides become hard constraints."""
    return _spec(
        room_type,
        sized=True,
        target_area=long_ * short_,
        target_long=long_,
        target_short=short_,
    )


def _rooms_within(plan: Plan, envelope: Envelope) -> bool:
    return all(
        r.x >= 0.0
        and r.y >= 0.0
        and r.x2 <= envelope.buildable_width + 1e-6
        and r.y2 <= envelope.buildable_length + 1e-6
        for r in plan.rooms
    )


def _no_overlaps(plan: Plan) -> bool:
    for i in range(len(plan.rooms)):
        for j in range(i + 1, len(plan.rooms)):
            a, b = plan.rooms[i], plan.rooms[j]
            dx = min(a.x2, b.x2) - max(a.x, b.x)
            dy = min(a.y2, b.y2) - max(a.y, b.y)
            if dx > 0.25 and dy > 0.25:
                return False
    return True


# --- units and envelope -----------------------------------------------------

def test_units_round_trip() -> None:
    assert to_ft(to_cells(30.0)) == 30.0
    assert to_cells(30.0) == 60
    assert to_cells(12.2) == 24  # nearest whole cell
    assert to_cells(12.3) == 25
    assert to_ft(25) == 12.5
    assert area_to_cells(450.0) == 1800  # 450 / 0.25
    assert to_cells(1.0) == 2


def test_envelope_buildable_area_and_cells() -> None:
    envelope = Envelope(30, 45)
    assert envelope.buildable_width == 30
    assert envelope.buildable_length == 45
    assert envelope.cells == (60, 90)
    assert envelope.area_sqft == 1350
    assert envelope.fits_area(1000)

    setback = Envelope(30, 45, setbacks={"left": 2, "right": 2, "bottom": 3, "top": 3})
    assert setback.buildable_width == 26
    assert setback.buildable_length == 39
    assert setback.cells == (52, 78)


# --- core solver ------------------------------------------------------------

def test_solve_packs_rooms_within_envelope() -> None:
    envelope = Envelope(30, 45)
    specs = [
        _spec(RoomType.LIVING_ROOM),
        _spec(RoomType.KITCHEN),
        _spec(RoomType.MASTER_BEDROOM),
        _spec(RoomType.GUEST_BEDROOM),
        _spec(RoomType.ATTACHED_BATHROOM),
        _spec(RoomType.BALCONY),
    ]
    outcome = solve(specs, envelope, seed=1, time_limit=SOLVER_BUDGET)
    assert outcome.status == "feasible", outcome.reason
    assert len(outcome.rooms) == len(specs)
    plan = Plan(rooms=outcome.rooms, plot_width=30, plot_length=45)
    assert _rooms_within(plan, envelope)
    assert _no_overlaps(plan)
    # Every room meets its hard floor.
    for spec, room in zip(specs, plan.rooms, strict=True):
        assert room.short_side >= spec.min_side - 0.1
        assert room.area >= spec.min_area - 0.1
        assert abs(room.x - round(room.x * 2) / 2) < 1e-9  # grid-aligned


def test_solve_is_deterministic_for_a_seed() -> None:
    envelope = Envelope(30, 45)
    specs = [_spec(RoomType.LIVING_ROOM), _spec(RoomType.KITCHEN), _spec(RoomType.MASTER_BEDROOM)]
    first = solve(specs, envelope, seed=7, time_limit=SOLVER_BUDGET)
    second = solve(specs, envelope, seed=7, time_limit=SOLVER_BUDGET)
    signature = lambda rooms: [(r.x, r.y, r.width, r.height) for r in rooms]  # noqa: E731
    assert first.status == second.status == "feasible"
    assert signature(first.rooms) == signature(second.rooms)


def test_solve_runs_the_strict_gate_before_returning_feasible(monkeypatch) -> None:
    """A CP-SAT FEASIBLE verdict is not a valid plan: the extracted geometry
    must survive the strict validator or the solver refuses to return it."""
    from app.geometry import solver as solver_pkg
    from app.geometry.validation import ValidationReport

    calls: list[bool] = []

    def _stub_gate(plan, envelope, specs):
        calls.append(True)
        report = ValidationReport()
        report.ok = False
        report.errors.append("injected geometry failure")
        return report

    monkeypatch.setattr(solver_pkg.cp_sat, "validate_plan", _stub_gate)
    envelope = Envelope(30, 45)
    specs = [_spec(RoomType.LIVING_ROOM), _spec(RoomType.KITCHEN)]
    outcome = solve(specs, envelope, seed=1, time_limit=SOLVER_BUDGET)
    assert calls, "the strict gate must run on a feasible solve"
    assert outcome.status == "infeasible"
    assert outcome.validation_failed is True
    assert outcome.rooms == []


def test_solve_relaxed_probes_skip_the_gate(monkeypatch) -> None:
    """The infeasibility ladder's relaxed solves are diagnostics, not plans -
    they must not be filtered by the strict gate, which is what would make
    every relaxed probe report a validation failure."""
    from app.geometry import solver as solver_pkg

    calls: list[bool] = []
    original = solver_pkg.cp_sat.validate_plan

    def _counting_gate(plan, envelope, specs):
        calls.append(True)
        return original(plan, envelope, specs)

    monkeypatch.setattr(solver_pkg.cp_sat, "validate_plan", _counting_gate)
    envelope = Envelope(30, 45)
    specs = [_spec(RoomType.LIVING_ROOM), _spec(RoomType.KITCHEN)]
    outcome = solve(specs, envelope, seed=1, time_limit=SOLVER_BUDGET, validate=False)
    assert outcome.status == "feasible"
    assert not calls, "relaxed probes must not run the strict gate"


def test_solve_reports_infeasible_when_min_area_overflows() -> None:
    # 15x15 = 225 sq ft cannot hold a living room (min 130) + kitchen (70) +
    # master (120) + 3 bathrooms + ... no matter how clever the packing.
    envelope = Envelope(15, 15)
    specs = [
        _spec(RoomType.LIVING_ROOM),
        _spec(RoomType.KITCHEN),
        _spec(RoomType.MASTER_BEDROOM),
        _spec(RoomType.ATTACHED_BATHROOM),
        _spec(RoomType.ATTACHED_BATHROOM),
        _spec(RoomType.COMMON_BATHROOM),
    ]
    outcome = solve(specs, envelope, seed=1, time_limit=SOLVER_BUDGET)
    assert outcome.status == "infeasible"
    assert outcome.rooms == []


def test_solve_enforces_sized_room_shape() -> None:
    envelope = Envelope(30, 30)
    specs = [
        _sized(RoomType.LIVING_ROOM, 20, 16),
        _sized(RoomType.MASTER_BEDROOM, 12, 11),
    ]
    outcome = solve(specs, envelope, seed=2, time_limit=SOLVER_BUDGET)
    assert outcome.status == "feasible", outcome.reason
    living = next(r for r in outcome.rooms if r.type is RoomType.LIVING_ROOM)
    assert living.long_side >= 20 - 0.5
    assert living.short_side >= 16 - 0.5


def test_outdoor_rooms_touch_an_external_wall() -> None:
    envelope = Envelope(30, 45)
    specs = [_spec(RoomType.LIVING_ROOM), _spec(RoomType.BALCONY), _spec(RoomType.PARKING)]
    outcome = solve(specs, envelope, seed=3, time_limit=SOLVER_BUDGET)
    assert outcome.status == "feasible", outcome.reason
    for room in outcome.rooms:
        if room.type.is_outdoor:
            assert room.touches_edge(30, 45)


# --- infeasibility diagnostics ----------------------------------------------

def test_diagnose_brief_area_stage() -> None:
    envelope = Envelope(15, 15)
    specs = [
        _spec(RoomType.LIVING_ROOM),
        _spec(RoomType.KITCHEN),
        _spec(RoomType.MASTER_BEDROOM),
    ]
    diag = diagnose_brief(specs, envelope)
    assert diag is not None
    assert diag.stage == "area"
    assert diag.suggestions


def test_diagnose_brief_returns_none_for_feasible_brief() -> None:
    envelope = Envelope(30, 45)
    specs = [_spec(RoomType.LIVING_ROOM), _spec(RoomType.KITCHEN)]
    assert diagnose_brief(specs, envelope) is None


def test_diagnose_solver_shape_stage_when_shapes_will_not_pack() -> None:
    # Sized rooms sum to nearly the whole plot: at their exact shapes they do
    # not tile, but relaxing the shapes fits comfortably -> "shape" verdict.
    envelope = Envelope(18, 25)
    specs = [
        _sized(RoomType.LIVING_ROOM, 16, 14),
        _sized(RoomType.KITCHEN, 10, 9),
        _sized(RoomType.MASTER_BEDROOM, 12, 11),
        _spec(RoomType.ATTACHED_BATHROOM),
        _spec(RoomType.COMMON_BATHROOM),
        _spec(RoomType.BALCONY),
    ]
    diag = diagnose_solver(specs, envelope, seed=5, time_limit=1.0)
    assert diag.stage in ("shape", "area")
    assert diag.reason and diag.suggestions


# --- scoring and validation -------------------------------------------------

def test_regression_benchmark_1bhk_18x25_infeasible(repository) -> None:
    """The benchmark's '1BHK 18x25 infeasible' brief must be refused.

    The legacy engine squeezed this brief and returned it with ~0.1-0.18 area
    error; the solver engine must answer ``status="infeasible"`` with rooms=[]
    and usable diagnostics instead of silently shrinking the requested sizes.
    """
    engine = LayoutEngine(
        _req(
            width=18,
            length=25,
            bhk=BHKType.BHK1,
            rooms=[RoomType.LIVING_ROOM, RoomType.KITCHEN],
            baths=(1, 1),
            features=[RoomType.BALCONY],
            dims={
                RoomType.LIVING_ROOM: RoomDimensions(length_ft=16, width_ft=14),
                RoomType.KITCHEN: RoomDimensions(length_ft=10, width_ft=9),
                RoomType.MASTER_BEDROOM: RoomDimensions(length_ft=12, width_ft=11),
            },
        )
    )
    plan = engine.generate_solver(repository.get("TPL-001"), seed=42, variation_index=0)
    assert plan.status == "infeasible"
    assert plan.rooms == []
    assert plan.built_up_sqft == 0
    assert plan.infeasibility is not None
    assert plan.infeasibility["stage"] in ("area", "shape", "packing", "envelope", "timeout")
    assert plan.infeasibility["reason"]
    assert isinstance(plan.infeasibility["suggestions"], list)


def test_regression_benchmark_3bhk_20x40_infeasible(repository) -> None:
    """The benchmark's '3BHK 20x40 infeasible' brief must be refused."""
    engine = LayoutEngine(
        _req(
            width=20,
            length=40,
            bhk=BHKType.BHK3,
            rooms=[
                RoomType.LIVING_ROOM,
                RoomType.DINING_ROOM,
                RoomType.KITCHEN,
                RoomType.MASTER_BEDROOM,
                RoomType.GUEST_BEDROOM,
                RoomType.CHILDREN_BEDROOM,
            ],
            baths=(2, 1),
            features=[RoomType.BALCONY, RoomType.PARKING],
            dims={
                RoomType.LIVING_ROOM: RoomDimensions(length_ft=20, width_ft=16),
                RoomType.KITCHEN: RoomDimensions(length_ft=12, width_ft=10),
                RoomType.MASTER_BEDROOM: RoomDimensions(length_ft=16, width_ft=14),
                RoomType.GUEST_BEDROOM: RoomDimensions(length_ft=14, width_ft=12),
                RoomType.CHILDREN_BEDROOM: RoomDimensions(length_ft=13, width_ft=11),
            },
        )
    )
    for template_id in ("TPL-001", "TPL-005", "TPL-012"):
        plan = engine.generate_solver(repository.get(template_id), seed=42, variation_index=0)
        assert plan.status == "infeasible"
        assert plan.rooms == []
        assert plan.infeasibility is not None
        assert plan.infeasibility["reason"]


# --- hard constraints are never violated -----------------------------------

FEASIBLE_CORPUS = [
    (20, 30, BHKType.BHK1, [RoomType.LIVING_ROOM, RoomType.KITCHEN]),
    (25, 50, BHKType.BHK2, [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN]),
    (30, 45, BHKType.BHK3, [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN]),
    (40, 55, BHKType.BHK4, [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN]),
]


def test_solver_never_violates_hard_constraints(repository) -> None:
    """Across the feasible corpus and several seeds, every solved room keeps
    its hard floor: minimum short side, minimum area, no overlap with any
    other room, and full containment in the buildable envelope."""
    failures: list[str] = []
    for width, length, bhk, rooms in FEASIBLE_CORPUS:
        requirements = _req(width=width, length=length, bhk=bhk, rooms=rooms)
        envelope = Envelope(width, length)
        programme = programme_from_brief(requirements, repository.get("TPL-001"))
        for seed in range(1, 6):
            outcome = solve(programme.specs, envelope, seed=seed, time_limit=SOLVER_BUDGET)
            assert outcome.status == "feasible", (width, length, seed, outcome.reason)
            for spec, room in zip(programme.specs, outcome.rooms, strict=True):
                if room.short_side < spec.min_side - 0.1:
                    failures.append(f"{spec.name}: short {room.short_side} < {spec.min_side}")
                if room.area < spec.min_area - 0.1:
                    failures.append(f"{spec.name}: area {room.area} < {spec.min_area}")
            plan = Plan(rooms=outcome.rooms, plot_width=width, plot_length=length)
            assert _rooms_within(plan, envelope), (width, length, seed)
            assert _no_overlaps(plan), (width, length, seed)
    assert not failures, failures


def test_solver_never_shrinks_sized_room_sides(repository) -> None:
    """A brief that sizes rooms keeps their long/short sides as hard floors."""
    requirements = _req(
        width=40,
        length=55,
        bhk=BHKType.BHK3,
        dims={
            RoomType.LIVING_ROOM: RoomDimensions(length_ft=20, width_ft=16),
            RoomType.MASTER_BEDROOM: RoomDimensions(length_ft=16, width_ft=13),
            RoomType.KITCHEN: RoomDimensions(length_ft=12, width_ft=10),
        },
    )
    envelope = Envelope(40, 55)
    programme = programme_from_brief(requirements, repository.get("TPL-001"))
    for seed in range(1, 4):
        outcome = solve(programme.specs, envelope, seed=seed, time_limit=SOLVER_BUDGET)
        assert outcome.status == "feasible", (seed, outcome.reason)
        for spec, room in zip(programme.specs, outcome.rooms, strict=True):
            if not spec.sized:
                continue
            assert room.short_side >= spec.target_short - 0.5, (seed, spec.name)
            assert room.long_side >= spec.target_long - 0.5, (seed, spec.name)


def test_engine_solver_output_meets_hard_constraints(repository) -> None:
    """The engine path (solve + doors + windows + gate) returns geometry that
    still satisfies the hard constraints once it is converted back to Rects."""
    requirements = _req(
        width=30,
        length=45,
        bhk=BHKType.BHK3,
        rooms=[RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN],
        baths=(2, 1),
    )
    envelope = Envelope(30, 45)
    engine = LayoutEngine(requirements)
    for seed in (7, 19, 42):
        plan = engine.generate_solver(repository.get("TPL-001"), seed=seed, variation_index=0)
        assert plan.status == "feasible", (seed, plan.infeasibility)
        assert plan.rooms
        programme = programme_from_brief(requirements, repository.get("TPL-001"))
        for spec, room in zip(programme.specs, plan.rooms, strict=True):
            assert min(room.width, room.height) >= spec.min_side - 0.1, (seed, room.name)
            assert room.width * room.height >= spec.min_area - 0.1, (seed, room.name)
        assert _rooms_within(plan, envelope), seed
        assert _no_overlaps(plan), seed


# --- API contract: infeasible brief returns 201 + status --------------------

def test_validate_plan_flags_an_overlap() -> None:
    envelope = Envelope(30, 30)
    specs = [_spec(RoomType.LIVING_ROOM), _spec(RoomType.KITCHEN)]
    plan = Plan(
        rooms=[
            Room(RoomType.LIVING_ROOM, "Living", 0.0, 0.0, 20.0, 20.0),
            Room(RoomType.KITCHEN, "Kitchen", 10.0, 10.0, 15.0, 15.0),
        ],
        plot_width=30,
        plot_length=30,
    )
    report = validate_plan(plan, envelope, specs)
    assert not report.ok
    assert any("overlap" in error for error in report.errors)


# --- engine integration -----------------------------------------------------

def test_layout_engine_solver_feasible_and_scored(repository) -> None:
    engine = LayoutEngine(_req())
    plan = engine.generate_solver(repository.get("TPL-001"), seed=42, variation_index=0)
    assert plan.status == "feasible"
    assert plan.rooms
    assert plan.quality_score is not None and 0 <= plan.quality_score <= 100
    plan2 = engine.generate_solver(repository.get("TPL-001"), seed=42, variation_index=0)
    assert [(r.x, r.y, r.width, r.height) for r in plan.rooms] == [
        (r.x, r.y, r.width, r.height) for r in plan2.rooms
    ]


def test_layout_engine_solver_infeasible_carries_diagnostics(repository) -> None:
    engine = LayoutEngine(
        _req(
            width=20,
            length=40,
            dims={
                RoomType.LIVING_ROOM: RoomDimensions(length_ft=20, width_ft=16),
                RoomType.KITCHEN: RoomDimensions(length_ft=12, width_ft=10),
                RoomType.MASTER_BEDROOM: RoomDimensions(length_ft=16, width_ft=14),
                RoomType.GUEST_BEDROOM: RoomDimensions(length_ft=14, width_ft=12),
                RoomType.CHILDREN_BEDROOM: RoomDimensions(length_ft=13, width_ft=11),
            },
        )
    )
    plan = engine.generate_solver(repository.get("TPL-001"), seed=42, variation_index=0)
    assert plan.status == "infeasible"
    assert plan.rooms == []
    assert plan.built_up_sqft == 0
    assert plan.infeasibility is not None
    assert plan.infeasibility["stage"] in ("area", "shape", "packing", "timeout")
    assert plan.infeasibility["reason"]
    assert isinstance(plan.infeasibility["suggestions"], list)


def test_solver_infeasible_never_runs_repair_logic(repository) -> None:
    """Req 9: a brief CP-SAT proves infeasible is returned as-is - the legacy
    repair pipeline must never be invoked to force a layout out of it."""

    class _BoomRepairer:
        def __getattr__(self, name):
            raise AssertionError(f"repair logic ({name}) ran after infeasibility")

    engine = LayoutEngine(
        _req(
            width=18,
            length=25,
            bhk=BHKType.BHK1,
            rooms=[RoomType.LIVING_ROOM, RoomType.KITCHEN],
            baths=(1, 1),
            features=[RoomType.BALCONY],
            dims={
                RoomType.LIVING_ROOM: RoomDimensions(length_ft=16, width_ft=14),
                RoomType.KITCHEN: RoomDimensions(length_ft=10, width_ft=9),
                RoomType.MASTER_BEDROOM: RoomDimensions(length_ft=12, width_ft=11),
            },
        )
    )
    engine._repairer = _BoomRepairer()
    engine._validator = _BoomRepairer()
    plan = engine.generate_solver(repository.get("TPL-001"), seed=42, variation_index=0)
    assert plan.status == "infeasible"
    assert plan.rooms == []
    assert plan.infeasibility is not None


def test_programme_includes_the_full_brief_room_set(repository) -> None:
    requirements = _req(
        rooms=[
            RoomType.LIVING_ROOM,
            RoomType.DINING_ROOM,
            RoomType.KITCHEN,
            RoomType.MASTER_BEDROOM,
            RoomType.CHILDREN_BEDROOM,
            RoomType.GUEST_BEDROOM,
        ],
        features=[RoomType.BALCONY, RoomType.PARKING],
    )
    programme = programme_from_brief(requirements, repository.get("TPL-001"))
    types = [spec.type for spec in programme.specs]
    for wanted in (
        RoomType.LIVING_ROOM,
        RoomType.KITCHEN,
        RoomType.MASTER_BEDROOM,
        RoomType.CHILDREN_BEDROOM,
        RoomType.GUEST_BEDROOM,
        RoomType.BALCONY,
        RoomType.PARKING,
    ):
        assert wanted in types
    assert types.count(RoomType.ATTACHED_BATHROOM) == 2
    assert types.count(RoomType.COMMON_BATHROOM) == 1


# --- API contract: infeasible brief returns 201 + status --------------------

@pytest.fixture()
def solver_engine_enabled():
    settings = get_settings()
    previous = settings.geometry_engine
    settings.geometry_engine = GeometryEngine.SOLVER
    settings.solver_time_limit_seconds = 1.0
    try:
        yield
    finally:
        settings.geometry_engine = previous


def test_infeasible_brief_returns_201_with_status_infeasible(solver_engine_enabled) -> None:
    brief = {
        "requirements": {
            "plot": {"width_ft": 18, "length_ft": 25, "shape": "rectangle", "facing": "east"},
            "bhk": "1BHK",
            "rooms": ["living_room", "kitchen"],
            "bathrooms": {"attached_count": 1, "common_count": 1},
            "features": ["balcony"],
            "room_dimensions": {
                "living_room": {"length_ft": 16, "width_ft": 14},
                "kitchen": {"length_ft": 10, "width_ft": 9},
                "master_bedroom": {"length_ft": 12, "width_ft": 11},
            },
            "style": "modern",
        },
        "variants": 2,
        "seed": 42,
    }
    with TestClient(create_app()) as client:
        response = client.post(f"{PREFIX}/generate", json=brief)
    assert response.status_code == 201
    body = response.json()
    assert body["layouts"]
    for layout in body["layouts"]:
        assert layout["status"] == "infeasible"
        assert layout["rooms"] == []
        assert layout["image_url"] == ""
        assert layout["infeasibility"]["reason"]
        assert layout["infeasibility"]["suggestions"]
