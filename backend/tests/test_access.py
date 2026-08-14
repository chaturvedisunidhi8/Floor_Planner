"""Tests for Milestone C/D: the access model, connectivity and doors.

Milestone C makes the intended access graph a hard part of candidate
generation: every access requirement (kitchen to dining, a bedroom off the
spine, an attached bath off its own bedroom) becomes a CP-SAT constraint that
demands a shared wall long enough for a real door. Connectivity is therefore a
property of the solution, not a repair pass, and the strict validation gate
refuses any plan that is not walkable from the entrance through the modeled
doors.

Milestone D models doors only on those intended access edges - never on a wall
two rooms merely happen to share - so the modeled door graph equals the access
graph exactly.
"""

from __future__ import annotations

from app.geometry.connectivity import (
    adjacency_graph,
    door_rooms,
    stranded_indices,
    walkable_graph,
)
from app.geometry.doors import model_doors
from app.geometry.envelope import Envelope
from app.geometry.layout_engine import LayoutEngine
from app.geometry.models import Plan, Room
from app.geometry.solver import cp_sat
from app.geometry.solver.topology import programme_from_brief
from app.geometry.validation import validate_plan
from app.schemas.enums import BHKType, Facing, InteriorStyle, RoomType
from app.schemas.requirements import (
    BathroomRequirements,
    FloorPlanRequirements,
    PlotDetails,
    RoomDimensions,
)

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


# --- the access model -------------------------------------------------------

def test_access_model_kitchen_connects_to_dining_or_spine(repository) -> None:
    programme = programme_from_brief(_req(), repository.get("TPL-001"))
    kitchen = next(i for i, s in enumerate(programme.specs) if s.type is RoomType.KITCHEN)
    dining = next(i for i, s in enumerate(programme.specs) if s.type is RoomType.DINING_ROOM)
    requirement = next(r for r in programme.access_requirements if r.room == kitchen)
    assert dining in requirement.candidates


def test_access_model_bedrooms_connect_to_circulation_not_each_other(repository) -> None:
    programme = programme_from_brief(_req(), repository.get("TPL-001"))
    circulation = {i for i, s in enumerate(programme.specs) if s.type in (
        RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.FOYER, RoomType.PASSAGE)}
    for i, spec in enumerate(programme.specs):
        if not spec.type.is_bedroom:
            continue
        requirement = next(r for r in programme.access_requirements if r.room == i)
        # Every candidate is circulation - never another bedroom (a bedroom is
        # not a corridor, even with a door between two of them).
        assert set(requirement.candidates) <= circulation


def test_access_model_attached_bathroom_opens_off_own_bedroom_or_spine(repository) -> None:
    programme = programme_from_brief(_req(), repository.get("TPL-001"))
    for i, spec in enumerate(programme.specs):
        if spec.type is not RoomType.ATTACHED_BATHROOM:
            continue
        requirement = next(r for r in programme.access_requirements if r.room == i)
        candidates = {programme.specs[c].type for c in requirement.candidates}
        # The en-suite rule: an attached bath opens off a bedroom (its own, by
        # rank) or the spine - never off the dining room as a first choice.
        assert candidates & {
            RoomType.MASTER_BEDROOM,
            RoomType.CHILDREN_BEDROOM,
            RoomType.GUEST_BEDROOM,
            RoomType.BEDROOM,
            RoomType.LIVING_ROOM,
        }, (spec.name, candidates)
        assert RoomType.ATTACHED_BATHROOM not in candidates


def test_access_model_outdoor_rooms_open_off_the_spine(repository) -> None:
    programme = programme_from_brief(
        _req(features=[RoomType.BALCONY, RoomType.PARKING]), repository.get("TPL-001")
    )
    balcony = next(i for i, s in enumerate(programme.specs) if s.type is RoomType.BALCONY)
    requirement = next(r for r in programme.access_requirements if r.room == balcony)
    spine = {programme.specs[c].type for c in requirement.candidates}
    assert spine & {RoomType.LIVING_ROOM, RoomType.DINING_ROOM}


def test_access_model_bathroom_never_adjacent_to_social_core(repository) -> None:
    programme = programme_from_brief(_req(), repository.get("TPL-001"))
    for a, b in programme.forbidden_pairs:
        assert programme.specs[a].type.is_bathroom
        assert programme.specs[b].type in (RoomType.LIVING_ROOM, RoomType.DINING_ROOM)


def test_access_model_entrance_is_the_living_room(repository) -> None:
    programme = programme_from_brief(_req(), repository.get("TPL-001"))
    assert programme.specs[programme.entrance_index].type is RoomType.LIVING_ROOM


# --- the solver enforces the access graph -----------------------------------

FEASIBLE_CORPUS = [
    (20, 30, BHKType.BHK1, [RoomType.LIVING_ROOM, RoomType.KITCHEN], (1, 1)),
    (25, 50, BHKType.BHK2, [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN], (2, 1)),
    (30, 45, BHKType.BHK3, [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN], (2, 1)),
    (40, 55, BHKType.BHK4, [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN], (2, 1)),
]


def test_every_feasible_solution_delivers_the_access_graph(repository) -> None:
    """Regression for the pre-C solver: 57% of plans stranded rooms. Now every
    access requirement must be a real shared wall in the solved geometry, so
    connectivity is a property of the solution."""
    failures: list[str] = []
    for width, length, bhk, rooms, baths in FEASIBLE_CORPUS:
        requirements = _req(width=width, length=length, bhk=bhk, rooms=rooms, baths=baths)
        envelope = Envelope(width, length)
        programme = programme_from_brief(requirements, repository.get("TPL-001"))
        for seed in (1, 7):
            outcome = cp_sat.solve(
                programme.specs, envelope, seed=seed, time_limit=SOLVER_BUDGET,
                access_requirements=programme.access_requirements,
            )
            if outcome.status != cp_sat.FEASIBLE:
                continue
            plan = Plan(rooms=outcome.rooms, plot_width=width, plot_length=length)
            graph = adjacency_graph(plan)
            for requirement in programme.access_requirements:
                if not any(
                    graph.has_edge(requirement.room, candidate)
                    for candidate in requirement.candidates
                ):
                    failures.append(
                        f"{bhk.value}/{seed}: room {requirement.room} not adjacent "
                        f"to any of {requirement.candidates}"
                    )
            if stranded_indices(plan):
                failures.append(f"{bhk.value}/{seed}: stranded rooms")
    assert not failures, "\n".join(failures)


def test_solver_connectivity_from_entrance_across_seeds(repository) -> None:
    """Every feasible solver plan is walkable from the entrance. At Milestone A
    this corpus left up to 43% of rooms stranded; the access constraints close
    that gap in candidate generation, not in a repair pass."""
    import networkx as nx

    for width, length, bhk, rooms, baths in FEASIBLE_CORPUS:
        requirements = _req(width=width, length=length, bhk=bhk, rooms=rooms, baths=baths)
        envelope = Envelope(width, length)
        programme = programme_from_brief(requirements, repository.get("TPL-001"))
        for seed in (1, 7):
            outcome = cp_sat.solve(
                programme.specs, envelope, seed=seed, time_limit=SOLVER_BUDGET,
                access_requirements=programme.access_requirements,
            )
            if outcome.status != cp_sat.FEASIBLE:
                continue
            plan = Plan(rooms=outcome.rooms, plot_width=width, plot_length=length)
            graph = walkable_graph(plan)
            entrance = programme.entrance_index
            reachable = nx.descendants(graph, entrance) | {entrance}
            indoor = [i for i, r in enumerate(plan.rooms) if not r.type.is_outdoor]
            missing = [i for i in indoor if i not in reachable]
            assert not missing, (bhk.value, seed, missing)


# --- doors live on the access edges -----------------------------------------

def test_model_doors_cuts_one_door_per_access_requirement(repository) -> None:
    requirements = _req()
    envelope = Envelope(30, 45)
    programme = programme_from_brief(requirements, repository.get("TPL-001"))
    outcome = cp_sat.solve(
        programme.specs, envelope, seed=1, time_limit=SOLVER_BUDGET,
        access_requirements=programme.access_requirements,
    )
    assert outcome.status == cp_sat.FEASIBLE
    plan = Plan(rooms=outcome.rooms, plot_width=30, plot_length=45)
    doors = model_doors(plan, programme.access_requirements)

    assert len(doors) == len(programme.access_requirements)
    # Every modeled door sits on a real shared wall.
    assert all(door_rooms(door, plan.rooms) is not None for door in doors)


def test_door_graph_equals_access_graph(repository) -> None:
    requirements = _req()
    envelope = Envelope(30, 45)
    programme = programme_from_brief(requirements, repository.get("TPL-001"))
    outcome = cp_sat.solve(
        programme.specs, envelope, seed=1, time_limit=SOLVER_BUDGET,
        access_requirements=programme.access_requirements,
    )
    assert outcome.status == cp_sat.FEASIBLE
    plan = Plan(rooms=outcome.rooms, plot_width=30, plot_length=45)
    plan.doors = model_doors(plan, programme.access_requirements)

    door_graph = walkable_graph(plan)
    # One door per requirement, on the shared wall of the chosen candidate:
    # the door graph is a spanning tree on the intended access edges.
    assert len(door_graph.edges()) == len(programme.access_requirements)
    for requirement in programme.access_requirements:
        assert any(
            door_graph.has_edge(requirement.room, candidate)
            for candidate in requirement.candidates
        )


def test_model_doors_access_mode_skips_non_access_shared_walls() -> None:
    """Two bedrooms and a kitchen side by side: with an access model the kitchen
    gets its door and the bedroom pair gets none, even though they all share
    walls long enough for one."""
    plan = Plan(
        rooms=[
            Room(RoomType.LIVING_ROOM, "Living", 0, 0, 10, 10),
            Room(RoomType.KITCHEN, "Kitchen", 10, 0, 10, 10),
            Room(RoomType.MASTER_BEDROOM, "Master", 20, 0, 10, 10),
        ],
        plot_width=30,
        plot_length=10,
    )
    from app.geometry.solver.topology import AccessRequirement

    access = (AccessRequirement(1, (0,)),)  # kitchen -> living
    doors = model_doors(plan, access)
    assert len(doors) == 1
    assert (doors[0].room_from, doors[0].room_to) == (RoomType.KITCHEN, RoomType.LIVING_ROOM)


# --- the validation gate is hard on connectivity -----------------------------

def test_validate_plan_rejects_disconnected_geometry() -> None:
    """A layout where the kitchen is reached only through a bedroom is refused,
    not repaired - the connectivity gate is a hard fault (Milestone C)."""
    plan = Plan(
        rooms=[
            Room(RoomType.LIVING_ROOM, "Living", 0, 0, 10, 10),
            Room(RoomType.MASTER_BEDROOM, "Master", 10, 0, 10, 10),
            Room(RoomType.KITCHEN, "Kitchen", 10, 10, 10, 5),
        ],
        plot_width=20,
        plot_length=15,
    )
    plan.doors = model_doors(plan)  # doors on every shared wall
    from app.geometry.solver.topology import RoomSpec
    from app.geometry.units import max_area, min_area, min_side

    specs = [
        RoomSpec(type=r.type, name=r.name, target_area=min_area(r.type),
                 min_side=min_side(r.type), min_area=min_area(r.type),
                 max_area=max_area(r.type), outdoor=r.type.is_outdoor)
        for r in plan.rooms
    ]
    report = validate_plan(plan, Envelope(20, 15), specs)
    assert not report.ok
    assert any("unreachable" in error for error in report.errors)


# --- shared_wall symmetry (Milestone D regression) ---------------------------

def test_shared_wall_line_is_order_independent() -> None:
    above = Room(RoomType.DINING_ROOM, "Dining", 4.0, 8.5, 16.5, 8.5)
    below = Room(RoomType.LIVING_ROOM, "Living", 4.0, 0.0, 8.0, 8.5)
    wall_a = above.shared_wall(below)
    wall_b = below.shared_wall(above)
    assert wall_a is not None and wall_b is not None
    assert wall_a == wall_b
    assert wall_a[0] == "horizontal"
    assert abs(wall_a[3] - 8.5) < 1e-9  # the actual shared edge, not the span mid


# --- end-to-end: the engine's final plan is walkable through its doors --------

def _as_plan(layout_plan) -> Plan:
    return Plan(
        rooms=[Room(r.type, r.name, r.x, r.y, r.width, r.height) for r in layout_plan.rooms],
        plot_width=layout_plan.plot_width,
        plot_length=layout_plan.plot_length,
        doors=layout_plan.doors,
    )


def _assert_walkable_from_entrance(layout_plan, requirements, template, template_id) -> None:
    """The engine's finished plan: one modeled door per access edge, every
    indoor room reachable from the entrance through those doors."""
    programme = programme_from_brief(requirements, template)
    plan = _as_plan(layout_plan)
    assert len(plan.doors) == len(programme.access_requirements), (template_id, plan.doors)
    assert stranded_indices(plan) == [], (template_id, stranded_indices(plan))

    import networkx as nx

    graph = walkable_graph(plan)
    entrance = programme.entrance_index
    reachable = nx.descendants(graph, entrance) | {entrance}
    indoor = [i for i, r in enumerate(plan.rooms) if not r.type.is_outdoor]
    assert not [i for i in indoor if i not in reachable], (template_id, indoor, reachable)


def test_engine_narrow_and_deep_briefs_stay_walkable(repository) -> None:
    """The benchmark's '1BHK narrow 20x30' and '2BHK deep 25x50' briefs are the
    hardest shapes for connectivity. End to end (solve + doors + gate) every
    feasible plan must be fully walkable from the entrance through its modeled
    doors, with exactly one door per access edge."""
    briefs = [
        ("1BHK narrow 20x30",
         _req(width=20, length=30, bhk=BHKType.BHK1,
              rooms=[RoomType.LIVING_ROOM, RoomType.KITCHEN],
              baths=(1, 1),
              dims={RoomType.LIVING_ROOM: RoomDimensions(length_ft=16, width_ft=12),
                    RoomType.KITCHEN: RoomDimensions(length_ft=10, width_ft=8)})),
        ("2BHK deep 25x50",
         _req(width=25, length=50, bhk=BHKType.BHK2,
              rooms=[RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN],
              baths=(2, 1),
              features=[RoomType.BALCONY],
              dims={RoomType.LIVING_ROOM: RoomDimensions(length_ft=18, width_ft=14),
                    RoomType.DINING_ROOM: RoomDimensions(length_ft=12, width_ft=10),
                    RoomType.KITCHEN: RoomDimensions(length_ft=10, width_ft=9)})),
    ]
    for name, requirements in briefs:
        engine = LayoutEngine(requirements)
        for template_id in ("TPL-001", "TPL-007", "TPL-013"):
            for seed in (5, 17):
                plan = engine.generate_solver(
                    repository.get(template_id), seed=seed, variation_index=0
                )
                assert plan.status == "feasible", (name, template_id, seed, plan.infeasibility)
                _assert_walkable_from_entrance(
                    plan, requirements, repository.get(template_id), template_id
                )


def test_engine_door_graph_walkable_across_bhk_corpus(repository) -> None:
    """Every feasible plan across 1BHK-4BHK is fully walkable from the entrance
    through the modeled doors - the access model is a hard solver constraint,
    so connectivity is a property of the solution, not of a repair pass."""
    corpus = [
        (20, 30, BHKType.BHK1, [RoomType.LIVING_ROOM, RoomType.KITCHEN], (1, 1)),
        (
            25, 50, BHKType.BHK2,
            [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN],
            (2, 1),
        ),
        (
            30, 45, BHKType.BHK3,
            [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN],
            (2, 1),
        ),
        (
            40, 55, BHKType.BHK4,
            [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN],
            (2, 1),
        ),
    ]
    for width, length, bhk, rooms, baths in corpus:
        requirements = _req(width=width, length=length, bhk=bhk, rooms=rooms, baths=baths)
        engine = LayoutEngine(requirements)
        for template_id in ("TPL-001", "TPL-012"):
            template = repository.get(template_id)
            for seed in (3, 11):
                plan = engine.generate_solver(template, seed=seed, variation_index=0)
                assert plan.status == "feasible", (bhk.value, template_id, seed, plan.infeasibility)
                _assert_walkable_from_entrance(plan, requirements, template, template_id)
