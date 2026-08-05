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


# --- Requested room dimensions --------------------------------------------
def test_resize_to_targets_moves_area_toward_the_requested_size() -> None:
    from app.geometry.validator import resize_to_targets

    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 12, 10),
        Rect(RoomType.STORE_ROOM, "Store", 0, 10, 12, 12),
    ]
    living, store = resize_to_targets(rooms, {RoomType.LIVING_ROOM: 200.0})

    assert living.area > 120
    # The pair still tiles exactly the same footprint - no gap, no overlap.
    assert living.y2 == pytest.approx(store.y)
    assert store.y2 == pytest.approx(22)


def test_resize_to_targets_shares_out_a_shortfall_rather_than_starving_one_room() -> None:
    """440 sq ft of brief in a 312 sq ft footprint: both give up a similar share.

    The engine used to hold a donor at its own request come what may, which
    left the room asking for more permanently starved while its neighbour sat
    at 100%. Splitting the shortfall is the better answer, and the one the
    solver's objective produces.
    """
    from app.geometry.validator import resize_to_targets

    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 12, 10),
        Rect(RoomType.MASTER_BEDROOM, "Master", 0, 10, 12, 16),
    ]
    targets = {RoomType.LIVING_ROOM: 260.0, RoomType.MASTER_BEDROOM: 180.0}
    living, master = resize_to_targets(rooms, targets)

    assert living.area > 120, "the starved room must gain something"
    assert master.area >= min_area(RoomType.MASTER_BEDROOM)
    # Neither ends up disproportionately worse off than the other.
    assert abs(living.area / 260.0 - master.area / 180.0) < 0.25
    assert living.y2 == pytest.approx(master.y)


def test_resize_to_targets_leaves_a_donor_alone_when_the_plot_can_hold_both() -> None:
    from app.geometry.validator import resize_to_targets

    # A footprint that holds both requests exactly, badly divided to start with.
    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 14, 10),
        Rect(RoomType.MASTER_BEDROOM, "Master", 0, 10, 14, 16.5),
    ]
    targets = {RoomType.LIVING_ROOM: 200.0, RoomType.MASTER_BEDROOM: 170.0}
    living, master = resize_to_targets(rooms, targets)

    assert living.area == pytest.approx(200.0, abs=15)
    assert master.area == pytest.approx(170.0, abs=15)


def test_resize_to_targets_is_a_no_op_without_targets() -> None:
    from app.geometry.validator import resize_to_targets

    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 12, 10),
        Rect(RoomType.KITCHEN, "Kitchen", 0, 10, 12, 10),
    ]
    assert resize_to_targets(rooms, {}) == rooms


def test_requested_dimensions_pull_the_layout_closer_to_the_brief(repository) -> None:
    """The client's sizes must measurably beat the engine's own defaults."""
    from app.schemas.requirements import RoomDimensions

    wanted = {
        RoomType.LIVING_ROOM: RoomDimensions(length_ft=18, width_ft=14),
        RoomType.MASTER_BEDROOM: RoomDimensions(length_ft=15, width_ft=13),
        RoomType.KITCHEN: RoomDimensions(length_ft=10, width_ft=9),
    }
    rooms = [RoomType.LIVING_ROOM, RoomType.KITCHEN, RoomType.MASTER_BEDROOM]

    def error(room_dimensions) -> float:
        requirements = make_requirements(rooms=rooms, room_dimensions=room_dimensions)
        engine = LayoutEngine(requirements)
        total = 0.0
        for index, template_id in enumerate(ALL_TEMPLATE_IDS):
            plan = engine.generate(repository.get(template_id), seed=7 + index, variation_index=0)
            for room_type, dims in wanted.items():
                built = sum(r.area for r in plan.rooms if r.type is room_type)
                total += abs(built - dims.area_sqft)
        return total

    assert error(wanted) < error({})


# --- The reported failure -------------------------------------------------
#: The brief from the bug report: a 3BHK on 30 x 45 ft, every room sized by the
#: client. 854 sq ft of rooms on a 1,350 sq ft plot.
REPORTED_BRIEF = {
    RoomType.LIVING_ROOM: (16, 14),
    RoomType.DINING_ROOM: (12, 10),
    RoomType.KITCHEN: (10, 10),
    RoomType.MASTER_BEDROOM: (14, 12),
    RoomType.GUEST_BEDROOM: (12, 11),
    RoomType.CHILDREN_BEDROOM: (11, 10),
}


def _reported_requirements():
    from app.schemas.requirements import RoomDimensions

    return make_requirements(
        plot=PlotDetails(width_ft=30, length_ft=45),
        bhk=BHKType.BHK3,
        rooms=list(REPORTED_BRIEF),
        room_dimensions={
            room: RoomDimensions(length_ft=length, width_ft=width)
            for room, (length, width) in REPORTED_BRIEF.items()
        },
        bathrooms=BathroomRequirements(attached_count=2, common_count=1),
        features=[RoomType.BALCONY, RoomType.PARKING],
    )


def _size_errors(plan, requirements) -> dict[RoomType, float]:
    """Signed relative area error per sized room, e.g. ``+0.75`` for 75% too big."""
    targets = requirements.room_targets
    return {
        room.type: (room.area - targets[room.type].area) / targets[room.type].area
        for room in plan.rooms
        if room.type in targets
    }


@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
@pytest.mark.parametrize("variation", range(4))
def test_the_layout_is_built_at_the_sizes_that_were_asked_for(
    template_id, variation, repository
) -> None:
    """The headline regression: the brief used to be ignored outright.

    What the client saw was a 110 sq ft children's bedroom delivered at 192
    (+75%), a 168 sq ft master at 262 (+56%), and a living room 40% short -
    every room resized to fill the plot rather than to match the request.
    """
    requirements = _reported_requirements()
    plan = LayoutEngine(requirements).generate(
        repository.get(template_id), seed=100 + variation, variation_index=variation
    )

    errors = _size_errors(plan, requirements)
    assert set(errors) == set(REPORTED_BRIEF), "a room the client asked for is missing"

    average = sum(abs(e) for e in errors.values()) / len(errors)
    assert average <= 0.15, (
        f"{template_id} v{variation}: rooms average {average:.0%} off the brief - "
        + ", ".join(f"{room.value} {err:+.0%}" for room, err in errors.items())
    )


@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
def test_no_room_is_inflated_far_past_what_was_requested(template_id, repository) -> None:
    """Nothing may balloon to soak up the plot - the original complaint."""
    requirements = _reported_requirements()
    engine = LayoutEngine(requirements)

    for variation in range(4):
        plan = engine.generate(
            repository.get(template_id), seed=100 + variation, variation_index=variation
        )
        for room, error in _size_errors(plan, requirements).items():
            assert error <= 0.35, (
                f"{template_id} v{variation}: {room.value} is {error:+.0%} of the "
                "size it was asked to be"
            )


@pytest.mark.parametrize("template_id", ALL_TEMPLATE_IDS)
def test_sized_rooms_come_out_a_usable_shape(template_id, repository) -> None:
    """A room can hold the right square footage and still be a corridor.

    The reported plans contained a 10.5 x 25 ft master bedroom - the right area
    for a 14 x 12 request, and nothing anyone would live in.
    """
    requirements = _reported_requirements()
    plan = LayoutEngine(requirements).generate(
        repository.get(template_id), seed=100, variation_index=0
    )
    targets = requirements.room_targets

    for room in plan.rooms:
        if room.type not in targets:
            continue
        assert room.aspect <= 4.5, (
            f"{template_id}: {room.name} is {room.width:g} x {room.height:g} ft"
        )
        assert min(room.width, room.height) >= 4.5, (
            f"{template_id}: {room.name} is only {min(room.width, room.height):g} ft across"
        )


def test_the_four_variants_differ_and_all_follow_the_brief(repository) -> None:
    requirements = _reported_requirements()
    engine = LayoutEngine(requirements)
    template = repository.get("TPL-008")

    signatures = set()
    for variation in range(4):
        plan = engine.generate(template, seed=200 + variation, variation_index=variation)
        signatures.add(tuple(sorted((r.name, r.x, r.y) for r in plan.rooms)))

        errors = _size_errors(plan, requirements)
        average = sum(abs(e) for e in errors.values()) / len(errors)
        assert average <= 0.15, f"variant {variation} drifts {average:.0%} from the brief"

    assert len(signatures) >= 3, "the variants are too similar to each other"


def test_slack_between_the_brief_and_the_plot_becomes_circulation(repository) -> None:
    """The 496 sq ft the brief does not spend must not be handed to the rooms."""
    requirements = _reported_requirements()
    plan = LayoutEngine(requirements).generate(
        repository.get("TPL-019"), seed=100, variation_index=0
    )

    targets = requirements.room_targets
    sized = sum(r.area for r in plan.rooms if r.type in targets)
    requested = sum(t.area for t in targets.values())
    assert sized <= requested * 1.15, (
        f"sized rooms total {sized:.0f} sq ft against a {requested:.0f} sq ft brief"
    )


# --- Circulation ----------------------------------------------------------
def test_a_room_reached_only_through_a_bedroom_is_not_connected() -> None:
    from app.geometry.validator import unreachable_indices

    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 12, 10),
        Rect(RoomType.MASTER_BEDROOM, "Master", 0, 10, 12, 12),
        Rect(RoomType.KITCHEN, "Kitchen", 0, 22, 12, 10),
    ]
    # The kitchen only touches the bedroom, so you would have to walk through
    # someone's bedroom to cook.
    assert unreachable_indices(rooms) == [2]
    # It is not cut off from the plan, though - bare adjacency reaches it.
    assert unreachable_indices(rooms, through_any_room=True) == []


def test_an_en_suite_is_reached_through_its_own_bedroom() -> None:
    from app.geometry.validator import unreachable_indices

    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 12, 10),
        Rect(RoomType.MASTER_BEDROOM, "Master", 0, 10, 12, 12),
        Rect(RoomType.ATTACHED_BATHROOM, "En-suite", 0, 22, 12, 6),
    ]
    assert unreachable_indices(rooms) == []


def test_a_corridor_connects_a_room_the_living_space_does_not_touch() -> None:
    from app.geometry.validator import ensure_circulation, unreachable_indices

    rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 20, 12),
        Rect(RoomType.MASTER_BEDROOM, "Master", 0, 12, 20, 12),
        Rect(RoomType.KITCHEN, "Kitchen", 0, 24, 20, 12),
    ]
    assert unreachable_indices(rooms) == [2]

    routed = ensure_circulation(rooms, 20, 36)
    assert unreachable_indices([r for r in routed if not r.type.is_outdoor]) == []
    assert any(r.type is RoomType.PASSAGE for r in routed)


# --- Slicing --------------------------------------------------------------
def test_the_arrangement_is_rebuilt_at_the_demanded_areas() -> None:
    """Each room gets its share of the plot, not its share of the template."""
    from app.geometry.slicing import assign_demands, decompose, instantiate

    template_rooms = [
        Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 10, 10),
        Rect(RoomType.KITCHEN, "Kitchen", 10, 0, 10, 10),
        Rect(RoomType.MASTER_BEDROOM, "Master", 0, 10, 20, 10),
    ]
    demands = {
        RoomType.LIVING_ROOM: 400.0,
        RoomType.KITCHEN: 200.0,
        RoomType.MASTER_BEDROOM: 200.0,
    }
    tree = decompose(template_rooms, lambda r: demands[r.type])
    assign_demands(tree, {}, 800.0, 400.0)
    for leaf in tree.leaves():
        leaf.demand = demands[leaf.room.type]
    from app.geometry.slicing import _roll_up

    _roll_up(tree)

    placed = instantiate(tree, 0, 0, 40, 20)
    by_type = {r.type: r for r in placed}
    assert by_type[RoomType.LIVING_ROOM].area == pytest.approx(400, abs=20)
    assert by_type[RoomType.KITCHEN].area == pytest.approx(200, abs=20)
    # Exactly tiled: no gap and no overlap anywhere.
    assert sum(r.area for r in placed) == pytest.approx(800, abs=1)


def test_instantiating_a_tree_tiles_the_plot_exactly(repository) -> None:
    from app.geometry.validator import _find_gaps

    requirements = _reported_requirements()
    engine = LayoutEngine(requirements)
    for template_id in ALL_TEMPLATE_IDS:
        rooms = engine._orient_and_scale(repository.get(template_id))
        assert not _find_gaps(rooms, 30, 45), f"{template_id} leaves a hole"
        for i, a in enumerate(rooms):
            for b in rooms[i + 1 :]:
                assert not a.overlaps(b, tolerance=0.25), f"{template_id}: {a.name}/{b.name}"


def test_a_generous_request_does_not_break_the_layout(repository) -> None:
    """Sizes the plot cannot honour degrade the fit, never the validity."""
    from app.schemas.requirements import RoomDimensions

    requirements = make_requirements(
        rooms=[RoomType.LIVING_ROOM, RoomType.KITCHEN, RoomType.MASTER_BEDROOM],
        room_dimensions={
            RoomType.LIVING_ROOM: RoomDimensions(length_ft=40, width_ft=30),
            RoomType.MASTER_BEDROOM: RoomDimensions(length_ft=30, width_ft=25),
        },
    )
    plan = LayoutEngine(requirements).generate(
        repository.get("TPL-001"), seed=11, variation_index=0
    )
    report = LayoutValidator(plan.plot_width, plan.plot_length).validate(plan.rooms)
    assert report.ok, report.errors
