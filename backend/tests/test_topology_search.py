"""Tests for the topology-search milestone.

The search must widen the space of arrangements the engine actually tries
without touching the hard guarantees A-F proved: every accepted plan is still
validated, connected, non-overlapping, dimensionally correct and in the base
programme's room order. The invariants below pin down:

* backward compatibility of :func:`candidate_programmes` - no config still
  yields exactly the single pre-search programme;
* the search actually surfaces distinct candidate topologies, in deterministic
  order, and every variant keeps the base access model (same edge set);
* a search result is never *worse* than the base programme's own plan: base is
  candidate 0, so the best score can only improve;
* winning rooms always come back in the base order, so door modeling, the
  strict gate and response building keep their spec->room mapping;
* single-candidate runs reproduce the pre-search behaviour exactly;
* the per-candidate audit trail (``plan.topology_search``) is present and well
  shaped, so the milestone report can be read off every plan.
"""

from __future__ import annotations

from collections import Counter

from app.geometry.layout_engine import LayoutEngine
from app.geometry.solver.topology import (
    TopologySearchConfig,
    candidate_programmes,
    programme_from_brief,
)
from app.schemas.enums import BHKType, Facing, InteriorStyle, RoomType
from app.schemas.requirements import (
    BathroomRequirements,
    FloorPlanRequirements,
    PlotDetails,
    RoomDimensions,
)

#: A small brief that solves to completion well inside the budget, so the
#: determinism assertions below are exact. Time-limited CP-SAT can return
#: different incumbents when it is cut off mid-search, so determinism is only
#: asserted where the solve finishes (mirroring ``test_solver.py``).
SOLVER_BUDGET = 1.0


def _req(
    width: float = 30,
    length: float = 45,
    bhk: BHKType = BHKType.BHK3,
    rooms: list[RoomType] | None = None,
    baths: tuple[int, int] = (2, 1),
    dims: dict[RoomType, RoomDimensions] | None = None,
) -> FloorPlanRequirements:
    return FloorPlanRequirements(
        plot=PlotDetails(width_ft=width, length_ft=length, facing=Facing.EAST),
        bhk=bhk,
        rooms=rooms
        or [
            RoomType.LIVING_ROOM,
            RoomType.DINING_ROOM,
            RoomType.KITCHEN,
            RoomType.MASTER_BEDROOM,
            RoomType.CHILDREN_BEDROOM,
            RoomType.GUEST_BEDROOM,
        ],
        bathrooms=BathroomRequirements(attached_count=baths[0], common_count=baths[1]),
        features=[],
        room_dimensions=dims or {},
        style=InteriorStyle.MODERN,
    )


def _small_req() -> FloorPlanRequirements:
    """A 5-spec brief the solver finishes quickly - determinism tests only."""
    return FloorPlanRequirements(
        plot=PlotDetails(width_ft=20, length_ft=30, facing=Facing.EAST),
        bhk=BHKType.BHK1,
        rooms=[RoomType.LIVING_ROOM, RoomType.KITCHEN, RoomType.MASTER_BEDROOM],
        bathrooms=BathroomRequirements(attached_count=1, common_count=0),
        features=[],
        style=InteriorStyle.MODERN,
    )


def _room_signature(plan) -> tuple:
    return tuple((r.type, r.x, r.y, r.width, r.height) for r in plan.rooms)


def _base_programme(requirements, template):
    return candidate_programmes(requirements, template)[0]


def _edge_set(programme) -> frozenset:
    """The access graph as room-type pairs, so order permutations don't matter."""
    types = [spec.type for spec in programme.specs]
    return frozenset(
        (types[requirement.room], types[candidate])
        for requirement in programme.access_requirements
        for candidate in requirement.candidates
    )


# --- candidate generation -------------------------------------------------


def test_candidate_programmes_backward_compatible(requirements, repository):
    """No config returns exactly one programme - the pre-search behaviour."""
    programme = candidate_programmes(requirements, repository.get("TPL-001"))
    assert len(programme) == 1
    assert programme[0] == programme_from_brief(requirements, repository.get("TPL-001"))


def test_candidates_keep_the_base_room_set_and_access_edges(requirements, repository):
    """Every variant carries the same room set and the same access edge set."""
    base = _base_programme(requirements, repository.get("TPL-001"))
    candidates = candidate_programmes(
        requirements,
        repository.get("TPL-001"),
        config=TopologySearchConfig(max_candidates=8),
    )
    assert len(candidates) > 1
    base_types = Counter(spec.type for spec in base.specs)
    base_edges = _edge_set(base)
    for candidate in candidates:
        assert Counter(spec.type for spec in candidate.specs) == base_types
        assert _edge_set(candidate) == base_edges


def test_zoning_and_permutation_variants(requirements, repository):
    """Zoning variants carry a spatial bias; permutations carry an order."""
    candidates = candidate_programmes(
        requirements,
        repository.get("TPL-001"),
        config=TopologySearchConfig(max_candidates=8),
    )
    zoning = [c for c in candidates if c.spatial_bias is not None]
    permuted = [c for c in candidates if c.order is not None]
    assert zoning, "a 3BHK brief must produce zoning variants"
    assert permuted, "a 3BHK brief must produce permutation variants"
    assert candidates[0].label == "Base"
    assert candidates[0].spatial_bias is None
    assert candidates[0].order is None


def test_max_candidates_1_disables_the_search(requirements, repository):
    """A config capped at one reproduces the single-programme engine."""
    candidates = candidate_programmes(
        requirements,
        repository.get("TPL-001"),
        config=TopologySearchConfig(max_candidates=1),
    )
    assert [c.label for c in candidates] == ["Base"]


# --- engine-level search --------------------------------------------------


def test_single_candidate_reproduces_pre_search_behaviour(repository):
    """candidates=1 is the old path: one candidate, deterministic, in base order."""
    engine = LayoutEngine(_small_req())
    template = repository.get("TPL-001")
    first = engine.generate_solver(
        template, seed=100, variation_index=0, time_limit=SOLVER_BUDGET, topology_candidates=1
    )
    second = engine.generate_solver(
        template, seed=100, variation_index=0, time_limit=SOLVER_BUDGET, topology_candidates=1
    )
    assert [entry["label"] for entry in first.topology_search] == ["Base"]
    assert _room_signature(first) == _room_signature(second)
    assert first.quality_score == second.quality_score


def test_search_is_deterministic(repository):
    """Same (brief, template, seed, candidates) reproduces the same search."""
    engine = LayoutEngine(_small_req())
    template = repository.get("TPL-001")
    first = engine.generate_solver(
        template, seed=100, variation_index=0, time_limit=SOLVER_BUDGET, topology_candidates=3
    )
    second = engine.generate_solver(
        template, seed=100, variation_index=0, time_limit=SOLVER_BUDGET, topology_candidates=3
    )
    assert _room_signature(first) == _room_signature(second)
    assert first.quality_score == second.quality_score
    assert [(e["label"], e["status"]) for e in first.topology_search] == [
        (e["label"], e["status"]) for e in second.topology_search
    ]


def test_search_keeps_the_base_room_order(requirements, repository):
    """Rooms always come back in the base programme's order, even when a
    permutation or zoning candidate wins."""
    for template_id in ("TPL-001", "TPL-005", "TPL-012"):
        engine = LayoutEngine(requirements)
        template = repository.get(template_id)
        base_types = [spec.type for spec in _base_programme(requirements, template).specs]
        plan = engine.generate_solver(
            template, seed=100, variation_index=0, time_limit=SOLVER_BUDGET, topology_candidates=3
        )
        assert plan.status == "feasible"
        assert [room.type for room in plan.rooms] == base_types


def test_search_result_is_never_worse_than_the_base(requirements, repository):
    """Candidate 0 is the base programme, so the best score >= the base score."""
    engine = LayoutEngine(requirements)
    for template_id in ("TPL-001", "TPL-005", "TPL-012"):
        template = repository.get(template_id)
        plan = engine.generate_solver(
            template, seed=100, variation_index=0, time_limit=SOLVER_BUDGET, topology_candidates=3
        )
        assert plan.status == "feasible"
        base_score = next(
            (e["score"] for e in plan.topology_search if e["label"] == "Base"),
            None,
        )
        assert base_score is not None
        assert plan.quality_score >= base_score
        winner = max(
            (e for e in plan.topology_search if e["status"] == "feasible"),
            key=lambda e: e["score"],
        )
        assert winner["label"] != "Base" or plan.quality_score == base_score


def test_search_log_shape(requirements, repository):
    """Every plan carries a well-shaped audit trail the report can read."""
    engine = LayoutEngine(requirements)
    template = repository.get("TPL-001")
    plan = engine.generate_solver(
        template, seed=100, variation_index=0, time_limit=SOLVER_BUDGET, topology_candidates=3
    )
    assert plan.status == "feasible"
    assert plan.topology_search
    for entry in plan.topology_search:
        assert entry["label"] in {"Base", "Bedrooms left / social right",
                                  "Bedrooms right / social left",
                                  "Bedrooms back / social front",
                                  "Bedrooms front / social back",
                                  "Largest rooms first",
                                  "Smallest rooms first",
                                  "Mirrored room order"}
        assert entry["status"] in {"feasible", "pruned", "infeasible", "timeout"}
        if entry["status"] == "feasible":
            assert entry["score"] is not None
        if entry["status"] == "pruned":
            assert entry["validation_errors"]


def test_pruned_candidates_do_not_sink_the_search(requirements, repository):
    """A candidate the strict gate rejects is dropped; survivors still win."""
    engine = LayoutEngine(requirements)
    plan = engine.generate_solver(
        repository.get("TPL-001"), seed=100, variation_index=0,
        time_limit=SOLVER_BUDGET, topology_candidates=3,
    )
    # The search log always contains the winner; pruning is optional but must
    # never flip a feasible brief to infeasible when a survivor exists.
    assert plan.status == "feasible"
    survivors = [e for e in plan.topology_search if e["status"] == "feasible"]
    assert survivors
