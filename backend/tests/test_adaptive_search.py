"""Tests for the adaptive-search milestone.

The adaptive stage re-prioritises the candidate budget from measurement: after
the base programme solves and passes the strict gate, its plan is measured and
the weaknesses buy targeted soft-bias candidates. The invariants pinned here:

* a healthy base plan yields no adaptive candidates - the engine does not burn
  budget fixing what the measurement says is fine;
* each measurable weakness (fragmented corridor, wet zone away from the
  bedrooms, rooms starved of daylight) spawns exactly the candidate that
  attacks it, with the right soft bias (band alignment, bedroom-half push,
  plot-edge touch);
* at most two adaptive candidates come back (the top-2 weaknesses), in a fixed
  deterministic priority order;
* every adaptive candidate keeps the base room set, the base access model and
  ``order=None``, so connectivity and the base room order survive unchanged;
* the engine never solves the base twice and never exceeds the candidate cap.
"""

from __future__ import annotations

from collections import Counter

from app.geometry.layout_engine import LayoutEngine
from app.geometry.models import Plan, Room
from app.geometry.solver.topology import (
    DAYLIGHT_LABEL,
    SPINE_LABEL,
    WET_CLUSTER_LABEL,
    Programme,
    RoomSpec,
    adaptive_programmes,
)
from app.schemas.enums import BHKType, Facing, InteriorStyle, RoomType
from app.schemas.requirements import (
    BathroomRequirements,
    FloorPlanRequirements,
    PlotDetails,
)

SOLVER_BUDGET = 1.0


def _specs(room_types: list[RoomType]) -> list[RoomSpec]:
    return [RoomSpec(t, t.label, 200.0, 5.0, 50.0) for t in room_types]


def _base(room_types: list[RoomType]) -> Programme:
    specs = _specs(room_types)
    return Programme(
        specs=specs,
        access_requirements=(),
        adjacency_pairs=(),
        forbidden_pairs=(),
        entrance_index=0,
        label="Base",
    )


def _plan(
    room_types: list[RoomType],
    positions: list[tuple[float, float, float, float]],
    width: float,
    length: float,
) -> Plan:
    """A hand-built plan whose rooms match ``room_types`` index for index."""
    rooms = [
        Room(t, t.label, x, y, w, h)
        for t, (x, y, w, h) in zip(room_types, positions, strict=True)
    ]
    plan = Plan(rooms=rooms, plot_width=width, plot_length=length)
    plan.doors = []
    plan.windows = []
    return plan


def _req(
    rooms: list[RoomType] | None = None, baths: tuple[int, int] = (2, 1)
) -> FloorPlanRequirements:
    rooms = rooms or [
        RoomType.LIVING_ROOM,
        RoomType.DINING_ROOM,
        RoomType.KITCHEN,
        RoomType.MASTER_BEDROOM,
        RoomType.CHILDREN_BEDROOM,
        RoomType.GUEST_BEDROOM,
    ]
    return FloorPlanRequirements(
        plot=PlotDetails(width_ft=30, length_ft=45, facing=Facing.EAST),
        bhk=BHKType.BHK3,
        rooms=rooms,
        bathrooms=BathroomRequirements(attached_count=baths[0], common_count=baths[1]),
        features=[],
        style=InteriorStyle.MODERN,
    )


# --- unit: adaptive_programmes ---------------------------------------------


def test_healthy_plan_yields_no_adaptive_candidates() -> None:
    """Wet rooms by their bedrooms, daylight on the envelope: nothing to fix."""
    room_types = [
        RoomType.LIVING_ROOM,
        RoomType.KITCHEN,
        RoomType.MASTER_BEDROOM,
        RoomType.CHILDREN_BEDROOM,
        RoomType.ATTACHED_BATHROOM,
        RoomType.COMMON_BATHROOM,
    ]
    # Every habitable room touches two plot edges (cross-ventilated); the wet
    # rooms are well-shaped and share a wall with a bedroom, never with social.
    plan = _plan(
        room_types,
        [
            (0, 0, 20, 20),
            (20, 0, 20, 20),
            (0, 20, 20, 20),
            (20, 20, 20, 20),
            (15, 32, 5, 5),
            (2, 15, 5, 5),
        ],
        40,
        40,
    )
    assert adaptive_programmes(_base(room_types), plan) == []


def test_fragmented_corridor_spawns_a_spine_candidate() -> None:
    """Two disconnected passage strips on a deep plot -> one north-south band."""
    room_types = [
        RoomType.PASSAGE,
        RoomType.PASSAGE,
        RoomType.LIVING_ROOM,
        RoomType.MASTER_BEDROOM,
    ]
    plan = _plan(
        room_types,
        [
            (12, 0, 6, 10),
            (12, 18, 6, 10),
            (0, 30, 30, 15),
            (0, 10, 12, 20),
        ],
        30,
        45,
    )
    candidates = adaptive_programmes(_base(room_types), plan)
    assert [c.label for c in candidates] == [SPINE_LABEL]
    assert candidates[0].spatial_bias.align_x == (0, 1)
    assert candidates[0].spatial_bias.align_y == ()


def test_fragmented_corridor_on_a_wide_plot_aligns_y() -> None:
    """A wide plot wants an east-west corridor run instead."""
    room_types = [
        RoomType.PASSAGE,
        RoomType.PASSAGE,
        RoomType.LIVING_ROOM,
    ]
    plan = _plan(
        room_types,
        [
            (0, 12, 10, 6),
            (18, 12, 10, 6),
            (0, 0, 45, 10),
        ],
        45,
        30,
    )
    candidates = adaptive_programmes(_base(room_types), plan)
    assert [c.label for c in candidates] == [SPINE_LABEL]
    assert candidates[0].spatial_bias.align_y == (0, 1)


def test_connected_corridor_is_not_spine_candidate() -> None:
    """One continuous band is a corridor, not a weakness to fix."""
    room_types = [
        RoomType.PASSAGE,
        RoomType.PASSAGE,
        RoomType.LIVING_ROOM,
        RoomType.MASTER_BEDROOM,
    ]
    plan = _plan(
        room_types,
        [
            (12, 0, 6, 20),
            (12, 20, 6, 20),
            (0, 42, 30, 3),
            (0, 10, 12, 20),
        ],
        30,
        45,
    )
    assert adaptive_programmes(_base(room_types), plan) == []


def test_wet_zone_weakness_pushes_bathrooms_to_the_bedroom_half() -> None:
    """A slender bathroom away from the bedrooms -> bathroom-half push."""
    room_types = [
        RoomType.LIVING_ROOM,
        RoomType.MASTER_BEDROOM,
        RoomType.CHILDREN_BEDROOM,
        RoomType.COMMON_BATHROOM,
    ]
    # Bedrooms fill the right half; the bathroom is a slender slot on the left.
    plan = _plan(
        room_types,
        [
            (0, 0, 20, 40),
            (20, 0, 20, 20),
            (20, 20, 20, 20),
            (2, 2, 3, 5),
        ],
        40,
        40,
    )
    candidates = adaptive_programmes(_base(room_types), plan)
    assert [c.label for c in candidates] == [WET_CLUSTER_LABEL]
    assert candidates[0].spatial_bias.right == (3,)


def test_wet_rooms_with_a_bedroom_wall_are_left_alone() -> None:
    """Wet rooms already pulled towards the bedrooms trigger nothing."""
    room_types = [
        RoomType.LIVING_ROOM,
        RoomType.MASTER_BEDROOM,
        RoomType.COMMON_BATHROOM,
    ]
    plan = _plan(
        room_types,
        [
            (0, 0, 20, 20),
            (0, 20, 20, 20),
            (4, 15, 5, 5),
        ],
        40,
        40,
    )
    # The bathroom shares a wall with the master (y=20 line) and is well-shaped.
    assert adaptive_programmes(_base(room_types), plan) == []


def test_daylight_starved_plan_spawns_a_daylight_candidate() -> None:
    """Habitable rooms with no cross-ventilation -> touch-edge push."""
    room_types = [
        RoomType.LIVING_ROOM,
        RoomType.KITCHEN,
        RoomType.MASTER_BEDROOM,
        RoomType.CHILDREN_BEDROOM,
    ]
    # One single row on the south edge: every room touches exactly the bottom
    # edge (nothing spans a corner), so none is cross-ventilated.
    plan = _plan(
        room_types,
        [
            (2, 0, 12, 20),
            (14, 0, 12, 20),
            (26, 0, 12, 20),
            (38, 0, 12, 20),
        ],
        60,
        30,
    )
    candidates = adaptive_programmes(_base(room_types), plan)
    assert [c.label for c in candidates] == [DAYLIGHT_LABEL]
    assert candidates[0].spatial_bias.touch_edge == (0, 1, 2, 3)


def test_returns_at_most_two_weaknesses_in_priority_order() -> None:
    """All three weaknesses present -> the top two (spine, then wet) survive."""
    room_types = [
        RoomType.LIVING_ROOM,
        RoomType.MASTER_BEDROOM,
        RoomType.CHILDREN_BEDROOM,
        RoomType.COMMON_BATHROOM,
        RoomType.PASSAGE,
        RoomType.PASSAGE,
    ]
    plan = _plan(
        room_types,
        [
            (0, 0, 20, 20),
            (20, 0, 20, 20),
            (20, 20, 20, 20),
            (2, 2, 3, 5),
            (10, 0, 6, 10),
            (10, 18, 6, 10),
        ],
        40,
        40,
    )
    candidates = adaptive_programmes(_base(room_types), plan)
    assert [c.label for c in candidates] == [SPINE_LABEL, WET_CLUSTER_LABEL]


def test_candidates_keep_the_base_room_set_access_and_order() -> None:
    """Every adaptive candidate shares the base room set, access model, None order."""
    room_types = [
        RoomType.LIVING_ROOM,
        RoomType.MASTER_BEDROOM,
        RoomType.CHILDREN_BEDROOM,
        RoomType.COMMON_BATHROOM,
    ]
    plan = _plan(
        room_types,
        [
            (0, 0, 20, 20),
            (20, 0, 20, 20),
            (20, 20, 20, 20),
            (2, 2, 3, 5),
        ],
        40,
        40,
    )
    base = _base(room_types)
    candidates = adaptive_programmes(base, plan)
    assert candidates
    for candidate in candidates:
        assert Counter(s.type for s in candidate.specs) == Counter(
            s.type for s in base.specs
        )
        assert candidate.access_requirements == base.access_requirements
        assert candidate.adjacency_pairs == base.adjacency_pairs
        assert candidate.forbidden_pairs == base.forbidden_pairs
        assert candidate.entrance_index == base.entrance_index
        assert candidate.order is None


def test_single_passage_has_nothing_to_align() -> None:
    """A single passage room cannot form a band, so no spine candidate."""
    room_types = [RoomType.PASSAGE, RoomType.LIVING_ROOM, RoomType.MASTER_BEDROOM]
    plan = _plan(
        room_types,
        [
            (12, 0, 6, 10),
            (0, 20, 30, 10),
            (0, 30, 15, 15),
        ],
        30,
        45,
    )
    assert adaptive_programmes(_base(room_types), plan) == []


# --- engine: the candidate budget ------------------------------------------


def test_adaptive_search_never_exceeds_the_candidate_cap(repository) -> None:
    """Total solves stay within ``topology_candidates`` and base runs once."""
    for cap in (3, 5):
        engine = LayoutEngine(_req())
        plan = engine.generate_solver(
            repository.get("TPL-001"),
            seed=100,
            variation_index=0,
            time_limit=SOLVER_BUDGET,
            topology_candidates=cap,
        )
        assert plan.status == "feasible"
        assert len(plan.topology_search) <= cap
        assert sum(e["label"] == "Base" for e in plan.topology_search) == 1


def test_adaptive_search_is_deterministic(repository) -> None:
    """Same (brief, template, seed, cap) reproduces the same candidate list."""
    engine = LayoutEngine(_req())
    template = repository.get("TPL-001")
    first = engine.generate_solver(
        template, seed=100, variation_index=0, time_limit=SOLVER_BUDGET, topology_candidates=3
    )
    second = engine.generate_solver(
        template, seed=100, variation_index=0, time_limit=SOLVER_BUDGET, topology_candidates=3
    )
    assert [e["label"] for e in first.topology_search] == [
        e["label"] for e in second.topology_search
    ]
