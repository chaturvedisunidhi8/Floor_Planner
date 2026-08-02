"""The geometry engine is where "looks like an architect drew it" is decided."""

from __future__ import annotations

import pytest

from app.geometry.layout_engine import LayoutEngine
from app.geometry.primitives import Rect, align_walls, max_area, min_area, snap
from app.geometry.validator import LayoutValidator, _find_gaps, cap_room_sizes, fill_gaps
from app.schemas.enums import BHKType, InteriorStyle, RoomType
from app.schemas.requirements import BathroomRequirements, FloorPlanRequirements, PlotDetails
from tests.conftest import make_requirements

ALL_TEMPLATE_IDS = [f"TPL-{i:03d}" for i in range(1, 21)]


# --- Primitives -----------------------------------------------------------
def test_snap_quantises_to_the_half_foot_grid() -> None:
    assert snap(3.26) == 3.5
    assert snap(3.24) == 3.0
    assert snap(-0.1) == 0.0


def test_shared_wall_length_measures_the_touching_run() -> None:
    a = Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 10, 10)
    b = Rect(RoomType.KITCHEN, "Kitchen", 10, 2, 8, 6)
    assert a.shared_wall_length(b) == pytest.approx(6.0)
    assert a.is_adjacent(b)


def test_rooms_that_only_touch_at_a_corner_are_not_adjacent() -> None:
    a = Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 10, 10)
    b = Rect(RoomType.KITCHEN, "Kitchen", 10, 10, 8, 6)
    assert not a.is_adjacent(b)


def test_align_walls_collapses_near_coincident_edges() -> None:
    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 10.0, 10),
        Rect(RoomType.KITCHEN, "Kitchen", 10.4, 0, 8, 10),
    ]
    aligned = align_walls(rooms)
    assert aligned[0].x2 == pytest.approx(aligned[1].x, abs=0.01)


def test_align_walls_never_collapses_a_room_to_nothing() -> None:
    rooms = [
        Rect(RoomType.COMMON_BATHROOM, "Bath", 0, 0, 0.6, 6),
        Rect(RoomType.LIVING_ROOM, "Living", 0.6, 0, 10, 6),
    ]
    for room in align_walls(rooms):
        assert room.width > 0
        assert room.height > 0


# --- Size policy ----------------------------------------------------------
def test_cap_room_sizes_trims_an_oversized_bathroom() -> None:
    huge = Rect(RoomType.COMMON_BATHROOM, "Toilet", 0, 0, 12, 14)
    (capped,) = cap_room_sizes([huge])
    assert capped.area <= max_area(RoomType.COMMON_BATHROOM) * 1.06


def test_cap_room_sizes_fixes_a_ribbon_shaped_service_room() -> None:
    ribbon = Rect(RoomType.COMMON_BATHROOM, "Toilet", 0, 0, 4, 30)
    (capped,) = cap_room_sizes([ribbon])
    assert capped.aspect < 4.0


def test_cap_room_sizes_leaves_a_long_living_room_alone() -> None:
    """A long living-dining run is a legitimate move, not a defect."""
    long_living = Rect(RoomType.LIVING_ROOM, "Living / Dining", 0, 0, 10, 38)
    (capped,) = cap_room_sizes([long_living])
    assert capped.aspect == pytest.approx(long_living.aspect)


def test_min_area_is_defined_for_every_room_type() -> None:
    for room_type in RoomType:
        assert min_area(room_type) > 0
        assert min_area(room_type) < max_area(room_type)


# --- Gap filling ----------------------------------------------------------
def test_find_gaps_spots_an_uncovered_pocket() -> None:
    rooms = [Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 10, 20)]
    gaps = _find_gaps(rooms, 20, 20)
    assert len(gaps) == 1
    assert gaps[0].area == pytest.approx(200.0)


def test_fill_gaps_closes_the_pocket() -> None:
    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 10, 20),
        Rect(RoomType.KITCHEN, "Kitchen", 10, 0, 10, 12),
    ]
    filled = fill_gaps(rooms, 20, 20)
    assert not _find_gaps(filled, 20, 20)


def test_fill_gaps_is_a_no_op_on_a_fully_tiled_plot() -> None:
    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 10, 20),
        Rect(RoomType.KITCHEN, "Kitchen", 10, 0, 10, 20),
    ]
    assert len(fill_gaps(rooms, 20, 20)) == 2


# --- End-to-end layout generation -----------------------------------------
@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
def test_every_template_yields_a_valid_layout(template_id, repository, requirements) -> None:
    """The headline guarantee: any template, any brief, a layout that holds up."""
    engine = LayoutEngine(requirements)
    plan = engine.generate(repository.get(template_id), seed=7, variation_index=0)

    report = LayoutValidator(plan.plot_width, plan.plot_length).validate(plan.rooms)
    assert report.ok, f"{template_id}: {report.errors}"


@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
def test_layouts_never_overlap_or_escape_the_plot(template_id, repository, requirements) -> None:
    plan = LayoutEngine(requirements).generate(
        repository.get(template_id), seed=11, variation_index=1
    )
    for i, a in enumerate(plan.rooms):
        assert a.x >= -0.05 and a.y >= -0.05
        assert a.x2 <= plan.plot_width + 0.05
        assert a.y2 <= plan.plot_length + 0.05
        for b in plan.rooms[i + 1 :]:
            assert not a.overlaps(b, tolerance=0.25), f"{a.name} overlaps {b.name}"


@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
def test_layouts_leave_no_unassigned_floor(template_id, repository, requirements) -> None:
    plan = LayoutEngine(requirements).generate(
        repository.get(template_id), seed=5, variation_index=2
    )
    stray = sum(g.area for g in _find_gaps(plan.rooms, plan.plot_width, plan.plot_length))
    assert stray / (plan.plot_width * plan.plot_length) < 0.03


def test_layout_honours_the_requested_bedroom_count(repository) -> None:
    for bhk in BHKType:
        requirements = make_requirements(bhk=bhk)
        plan = LayoutEngine(requirements).generate(
            repository.get("TPL-010"), seed=3, variation_index=0
        )
        bedrooms = [r for r in plan.rooms if r.type.is_bedroom]
        assert len(bedrooms) == bhk.bedroom_count, f"{bhk}: got {len(bedrooms)}"


def test_layout_matches_the_requested_plot_exactly(repository, requirements) -> None:
    plan = LayoutEngine(requirements).generate(
        repository.get("TPL-007"), seed=1, variation_index=0
    )
    assert plan.plot_width == requirements.plot.width_ft
    assert plan.plot_length == requirements.plot.length_ft


def test_generation_is_deterministic_for_a_given_seed(repository, requirements) -> None:
    engine = LayoutEngine(requirements)
    template = repository.get("TPL-008")
    first = engine.generate(template, seed=123, variation_index=0)
    second = engine.generate(template, seed=123, variation_index=0)

    assert [(r.name, r.x, r.y, r.width, r.height) for r in first.rooms] == [
        (r.name, r.x, r.y, r.width, r.height) for r in second.rooms
    ]


def test_variation_operators_produce_different_layouts(repository, requirements) -> None:
    engine = LayoutEngine(requirements)
    template = repository.get("TPL-010")
    signatures = {
        tuple(sorted((r.name, r.x, r.y) for r in engine.generate(template, 50 + i, i).rooms))
        for i in range(4)
    }
    assert len(signatures) >= 3, "variations are too similar to each other"


def test_requested_rooms_appear_in_the_layout(repository) -> None:
    requirements = make_requirements(
        rooms=[
            RoomType.LIVING_ROOM,
            RoomType.KITCHEN,
            RoomType.POOJA_ROOM,
            RoomType.STUDY_ROOM,
        ],
        bhk=BHKType.BHK2,
    )
    plan = LayoutEngine(requirements).generate(
        repository.get("TPL-007"), seed=9, variation_index=0
    )
    placed = {r.type for r in plan.rooms}
    assert RoomType.POOJA_ROOM in placed
    assert RoomType.STUDY_ROOM in placed


def test_unrequested_features_are_dropped(repository) -> None:
    requirements = make_requirements(features=[], bhk=BHKType.BHK3)
    plan = LayoutEngine(requirements).generate(
        repository.get("TPL-011"), seed=4, variation_index=0
    )
    placed = {r.type for r in plan.rooms}
    assert RoomType.PARKING not in placed
    assert RoomType.GARDEN not in placed


def test_outdoor_space_is_placed_against_an_external_wall(repository) -> None:
    """Landlocked parking is the clearest sign of a machine-made plan."""
    requirements = make_requirements(features=[RoomType.PARKING], bhk=BHKType.BHK3)
    for template_id in ("TPL-009", "TPL-005", "TPL-013"):
        plan = LayoutEngine(requirements).generate(
            repository.get(template_id), seed=6, variation_index=0
        )
        for room in plan.rooms:
            if room.type is not RoomType.PARKING:
                continue
            on_edge = (
                room.x <= 1.0
                or room.y <= 1.0
                or room.x2 >= plan.plot_width - 1.0
                or room.y2 >= plan.plot_length - 1.0
            )
            assert on_edge, f"{template_id}: parking is landlocked"


def test_bathroom_counts_follow_the_brief(repository) -> None:
    requirements = make_requirements(
        bathrooms=BathroomRequirements(attached_count=2, common_count=1), bhk=BHKType.BHK3
    )
    plan = LayoutEngine(requirements).generate(
        repository.get("TPL-019"), seed=8, variation_index=0
    )
    attached = sum(1 for r in plan.rooms if r.type is RoomType.ATTACHED_BATHROOM)
    common = sum(1 for r in plan.rooms if r.type is RoomType.COMMON_BATHROOM)
    # Repair may drop one that could not be fitted, but never invent extras.
    assert attached <= 2
    assert common <= 1
    assert attached + common >= 2


@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
def test_habitable_rooms_end_up_reasonably_proportioned(
    template_id, repository, requirements
) -> None:
    """No 80 sq ft living room next to a 290 sq ft bedroom."""
    plan = LayoutEngine(requirements).generate(
        repository.get(template_id), seed=13, variation_index=0
    )
    for room in plan.rooms:
        if room.type is RoomType.LIVING_ROOM or room.type.is_bedroom:
            assert room.area >= min_area(room.type) * 0.75, (
                f"{template_id}: '{room.name}' is only {room.area:.0f} sq ft"
            )
            assert room.area <= max_area(room.type) * 1.05, (
                f"{template_id}: '{room.name}' is {room.area:.0f} sq ft"
            )


def test_rebalance_moves_area_from_a_bloated_neighbour() -> None:
    from app.geometry.validator import rebalance_room_sizes

    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 9, 9),
        Rect(RoomType.MASTER_BEDROOM, "Master", 0, 9, 9, 30),
    ]
    living, master = rebalance_room_sizes(rooms)
    assert living.area > 81
    assert master.area < 270
    # The wall moved; the pair still tiles the same footprint exactly.
    assert living.y2 == pytest.approx(master.y)
    assert master.y2 == pytest.approx(39)


def test_rebalance_pushes_excess_off_an_oversized_room() -> None:
    """A bloated room sheds area even when its neighbour was not short of any."""
    from app.geometry.validator import rebalance_room_sizes

    rooms = [
        Rect(RoomType.MASTER_BEDROOM, "Master", 0, 0, 10.5, 28),
        Rect(RoomType.LIVING_ROOM, "Living", 0, 28, 10.5, 14),
    ]
    master, living = rebalance_room_sizes(rooms)
    assert master.area <= max_area(RoomType.MASTER_BEDROOM) * 1.05
    assert living.area > 147
    assert master.y2 == pytest.approx(living.y)


def test_rebalance_leaves_a_balanced_plan_untouched() -> None:
    from app.geometry.validator import rebalance_room_sizes

    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 15, 15),
        Rect(RoomType.MASTER_BEDROOM, "Master", 0, 15, 15, 14),
    ]
    assert rebalance_room_sizes(rooms) == rooms


def test_tiny_plot_still_produces_something_valid(repository) -> None:
    requirements = FloorPlanRequirements(
        plot=PlotDetails(width_ft=18, length_ft=25),
        bhk=BHKType.BHK1,
        rooms=[RoomType.LIVING_ROOM, RoomType.KITCHEN],
        bathrooms=BathroomRequirements(attached_count=1, common_count=0),
        features=[],
        style=InteriorStyle.MINIMAL,
    )
    plan = LayoutEngine(requirements).generate(
        repository.get("TPL-017"), seed=2, variation_index=0
    )
    assert plan.rooms
    report = LayoutValidator(plan.plot_width, plan.plot_length).validate(plan.rooms)
    assert report.ok, report.errors
