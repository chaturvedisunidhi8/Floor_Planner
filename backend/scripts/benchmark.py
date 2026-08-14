"""Measure the layout engine against a fixed corpus of briefs.

Baseline harness for the geometry re-engineering: every metric reported here is
the number the solver engine has to beat. Run with::

    python scripts/benchmark.py                  # legacy engine only
    python scripts/benchmark.py --engine solver  # solver engine only
    python scripts/benchmark.py --engine both    # side-by-side comparison

Metrics per brief (averaged over all templates x variations):

* **room-area error**   mean and p95 of ``|built - requested| / requested``
  over every room the client sized.
* **dimension error**   mean ``|built - requested| / requested`` over the
  requested long/short sides of every sized room.
* **connectivity**      fraction of indoor rooms reachable from circulation
  (1.0 means every layout is fully connected).
* **door-connectivity** fraction of indoor rooms reachable *through the modeled
  doors* (the solver path only; for the legacy engine, which models no doors,
  it falls back to adjacency, so the two are comparable).
* **door-satisfied**    fraction of access requirements that actually received a
  modeled door on the shared wall the solver produced (Milestone C/D).
* **overlap**           fraction of layouts with any overlapping pair.
* **infeasible-detected** fraction of infeasible briefs the engine *reported*
  as such (the legacy engine is 0 by construction - it never says no).
* **unexpected-infeasible** layouts the engine refused for a brief marked
  feasible in the corpus - the cost of making access a hard constraint.
* **feasibility**       fraction of attempts whose verdict matched the corpus
  label: a brief marked infeasible was refused and a brief marked feasible was
  built. This is the single number that says whether the engine *knows* what
  can and cannot be built (1.0 means it never misclassifies).
* **coverage**          mean fraction of the plot the rooms occupy.
* **corridor**          mean fraction of built-up area held by passage/foyer.
* **time**              mean wall-clock seconds per generated layout.
* **score**             mean architectural quality score of feasible solver
  plans (from the scoring module used for best-plan selection).
* **unique layouts**    distinct final geometries per brief/template - the
  diversity payoff of topology search (``--topology-candidates N``).
  ``N=1`` disables the search and reproduces the single-programme engine.

An infeasible brief is one whose demanded minimum floor area already exceeds
the buildable area, so there is no layout that could satisfy it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, quantiles

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.geometry.connectivity import adjacency_graph, reachable_fraction, walkable_graph
from app.geometry.layout_engine import LayoutEngine
from app.geometry.models import Plan, Room
from app.geometry.solver.topology import candidate_programmes
from app.geometry.validator import unreachable_indices
from app.repositories.template_repository import JsonTemplateRepository
from app.schemas.enums import BHKType, Facing, InteriorStyle, RoomType
from app.schemas.requirements import (
    BathroomRequirements,
    FloorPlanRequirements,
    PlotDetails,
    RoomDimensions,
)

logger = get_logger("benchmark")

#: The corpus. Every brief is run against every template at ``variants`` seeds.
#: ``infeasible=True`` marks a brief whose minimum room areas cannot fit the
#: plot - the engine's job is to say so, not to shrink everyone proportionally.
Brief = tuple[str, FloorPlanRequirements, bool]


def _brief(name: str, plot, bhk, rooms, baths, features, dims, infeasible=False) -> Brief:
    requirements = FloorPlanRequirements(
        plot=plot,
        bhk=bhk,
        rooms=rooms,
        bathrooms=baths,
        features=features,
        room_dimensions=dims,
        style=InteriorStyle.MODERN,
    )
    return name, requirements, infeasible


def _dims(*pairs) -> dict[RoomType, RoomDimensions]:
    return {
        room: RoomDimensions(length_ft=long_, width_ft=short)
        for room, (long_, short) in pairs
    }


def _briefs() -> list[Brief]:
    baths = BathroomRequirements(attached_count=2, common_count=1)
    rooms_3bhk = [
        RoomType.LIVING_ROOM,
        RoomType.DINING_ROOM,
        RoomType.KITCHEN,
        RoomType.MASTER_BEDROOM,
        RoomType.GUEST_BEDROOM,
        RoomType.CHILDREN_BEDROOM,
    ]
    return [
        _brief(
            "1BHK narrow 20x30",
            PlotDetails(width_ft=20, length_ft=30, facing=Facing.EAST),
            BHKType.BHK1,
            [RoomType.LIVING_ROOM, RoomType.KITCHEN],
            BathroomRequirements(attached_count=1, common_count=1),
            [],
            _dims((RoomType.LIVING_ROOM, (16, 12)), (RoomType.KITCHEN, (10, 8))),
        ),
        _brief(
            "2BHK deep 25x50",
            PlotDetails(width_ft=25, length_ft=50, facing=Facing.NORTH),
            BHKType.BHK2,
            [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN],
            BathroomRequirements(attached_count=2, common_count=1),
            [RoomType.BALCONY],
            _dims(
                (RoomType.LIVING_ROOM, (18, 14)),
                (RoomType.DINING_ROOM, (12, 10)),
                (RoomType.KITCHEN, (10, 9)),
            ),
        ),
        _brief(
            "2BHK wide 40x20",
            PlotDetails(width_ft=40, length_ft=20, facing=Facing.WEST),
            BHKType.BHK2,
            [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN],
            BathroomRequirements(attached_count=1, common_count=1),
            [],
            _dims((RoomType.LIVING_ROOM, (16, 14)), (RoomType.KITCHEN, (10, 9))),
        ),
        _brief(
            "3BHK standard 30x45",
            PlotDetails(width_ft=30, length_ft=45, facing=Facing.EAST),
            BHKType.BHK3,
            rooms_3bhk,
            baths,
            [RoomType.BALCONY, RoomType.PARKING],
            _dims(
                (RoomType.LIVING_ROOM, (16, 14)),
                (RoomType.DINING_ROOM, (12, 10)),
                (RoomType.KITCHEN, (10, 10)),
                (RoomType.MASTER_BEDROOM, (14, 12)),
                (RoomType.GUEST_BEDROOM, (12, 11)),
                (RoomType.CHILDREN_BEDROOM, (11, 10)),
            ),
        ),
        _brief(
            "3BHK square 35x35",
            PlotDetails(width_ft=35, length_ft=35, shape="square", facing=Facing.SOUTH),
            BHKType.BHK3,
            rooms_3bhk,
            baths,
            [RoomType.POOJA_ROOM, RoomType.STAIRCASE],
            _dims(
                (RoomType.LIVING_ROOM, (16, 14)),
                (RoomType.KITCHEN, (10, 10)),
                (RoomType.MASTER_BEDROOM, (14, 12)),
            ),
        ),
        _brief(
            "4BHK 40x55",
            PlotDetails(width_ft=40, length_ft=55, facing=Facing.EAST),
            BHKType.BHK4,
            [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN],
            BathroomRequirements(attached_count=3, common_count=2),
            [RoomType.BALCONY, RoomType.PARKING, RoomType.GARDEN, RoomType.UTILITY_ROOM],
            _dims((RoomType.LIVING_ROOM, (20, 16)), (RoomType.MASTER_BEDROOM, (16, 13))),
        ),
        _brief(
            "4BHK deep 30x65",
            PlotDetails(width_ft=30, length_ft=65, facing=Facing.NORTH),
            BHKType.BHK4,
            [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN],
            BathroomRequirements(attached_count=3, common_count=2),
            [RoomType.BALCONY, RoomType.PARKING],
            _dims((RoomType.LIVING_ROOM, (18, 14)), (RoomType.KITCHEN, (11, 10))),
        ),
        _brief(
            "2BHK irregular 26x38",
            PlotDetails(width_ft=26, length_ft=38, facing=Facing.ANY),
            BHKType.BHK2,
            [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN],
            BathroomRequirements(attached_count=1, common_count=1),
            [RoomType.BALCONY, RoomType.STUDY_ROOM],
            _dims((RoomType.LIVING_ROOM, (15, 13)), (RoomType.KITCHEN, (9, 9))),
        ),
        _brief(
            "1BHK 18x25 infeasible",
            PlotDetails(width_ft=18, length_ft=25, facing=Facing.EAST),
            BHKType.BHK1,
            [RoomType.LIVING_ROOM, RoomType.KITCHEN],
            BathroomRequirements(attached_count=1, common_count=1),
            [RoomType.BALCONY],
            _dims(
                (RoomType.LIVING_ROOM, (16, 14)),
                (RoomType.KITCHEN, (10, 9)),
                (RoomType.MASTER_BEDROOM, (12, 11)),
            ),
            infeasible=True,
        ),
        _brief(
            "3BHK 20x40 infeasible",
            PlotDetails(width_ft=20, length_ft=40, facing=Facing.EAST),
            BHKType.BHK3,
            rooms_3bhk,
            baths,
            [RoomType.BALCONY, RoomType.PARKING],
            _dims(
                (RoomType.LIVING_ROOM, (20, 16)),
                (RoomType.KITCHEN, (12, 10)),
                (RoomType.MASTER_BEDROOM, (16, 14)),
                (RoomType.GUEST_BEDROOM, (14, 12)),
                (RoomType.CHILDREN_BEDROOM, (13, 11)),
            ),
            infeasible=True,
        ),
    ]


@dataclass
class BriefMetrics:
    """Aggregated numbers for one brief (or the whole corpus)."""

    name: str
    plans: int = 0
    area_errors: list[float] = field(default_factory=list)
    dim_errors: list[float] = field(default_factory=list)
    connected_fraction: list[float] = field(default_factory=list)
    door_connected_fraction: list[float] = field(default_factory=list)
    overlap_count: int = 0
    infeasible_detected: int = 0
    unexpected_infeasible: int = 0
    #: Infeasible briefs the engine wrongly built instead of refusing.
    infeasible_missed: int = 0
    #: Correct verdicts vs total attempts (the ``feasibility`` metric).
    feasibility_correct: int = 0
    feasibility_total: int = 0
    coverage: list[float] = field(default_factory=list)
    corridor_fraction: list[float] = field(default_factory=list)
    times: list[float] = field(default_factory=list)
    #: Milestone C: fraction of access requirements with a real shared wall.
    access_satisfied: list[float] = field(default_factory=list)
    #: Milestone D: fraction of access requirements with a modeled door.
    door_satisfied: list[float] = field(default_factory=list)
    #: Plans where a forbidden pair (e.g. bathroom vs living) shares a wall.
    forbidden_violations: int = 0
    #: Architectural scores of feasible solver plans (quality_score).
    scores: list[float] = field(default_factory=list)
    #: Distinct final geometries, as ``(type, x, y, w, h)`` signatures.
    layout_signatures: set[str] = field(default_factory=set)

    @property
    def overlap_rate(self) -> float:
        return self.overlap_count / self.plans if self.plans else 0.0

    @property
    def feasibility(self) -> float | None:
        """Fraction of attempts whose verdict matched the corpus label."""
        if not self.feasibility_total:
            return None
        return self.feasibility_correct / self.feasibility_total

    def merge(self, other: BriefMetrics) -> None:
        self.plans += other.plans
        self.area_errors.extend(other.area_errors)
        self.dim_errors.extend(other.dim_errors)
        self.connected_fraction.extend(other.connected_fraction)
        self.door_connected_fraction.extend(other.door_connected_fraction)
        self.overlap_count += other.overlap_count
        self.infeasible_detected += other.infeasible_detected
        self.unexpected_infeasible += other.unexpected_infeasible
        self.infeasible_missed += other.infeasible_missed
        self.feasibility_correct += other.feasibility_correct
        self.feasibility_total += other.feasibility_total
        self.coverage.extend(other.coverage)
        self.corridor_fraction.extend(other.corridor_fraction)
        self.times.extend(other.times)
        self.access_satisfied.extend(other.access_satisfied)
        self.door_satisfied.extend(other.door_satisfied)
        self.forbidden_violations += other.forbidden_violations
        self.scores.extend(other.scores)
        self.layout_signatures.update(other.layout_signatures)


def _area_error(plan, requirements) -> float:
    targets = requirements.room_targets
    errors = [
        abs(room.area - targets[room.type].area) / targets[room.type].area
        for room in plan.rooms
        if room.type in targets
    ]
    return mean(errors) if errors else 0.0


def _dim_error(plan, requirements) -> float:
    targets = requirements.room_targets
    errors: list[float] = []
    for room in plan.rooms:
        target = targets.get(room.type)
        if target is None or not target.long_side or not target.short_side:
            continue
        built_long = max(room.width, room.height)
        built_short = min(room.width, room.height)
        errors.append(abs(built_long - target.long_side) / target.long_side)
        errors.append(abs(built_short - target.short_side) / target.short_side)
    return mean(errors) if errors else 0.0


def _connectivity(plan) -> float:
    indoor = [r for r in plan.rooms if not r.type.is_outdoor]
    if not indoor:
        return 1.0
    stranded = unreachable_indices(indoor)
    return 1.0 - len(stranded) / len(indoor)


def _corridor_fraction(plan) -> float:
    built_up = sum(r.area for r in plan.rooms if not r.type.is_outdoor)
    if built_up <= 0:
        return 0.0
    circulation = sum(
        r.area for r in plan.rooms if r.type in (RoomType.PASSAGE, RoomType.FOYER)
    )
    return circulation / built_up


def _has_overlap(plan) -> bool:
    rooms = plan.rooms
    return any(
        rooms[i].overlaps(rooms[j], tolerance=0.25)
        for i in range(len(rooms))
        for j in range(i + 1, len(rooms))
    )


def _access_metrics(plan, programme) -> tuple[float, int]:
    """Milestone C: how much of the intended access graph the geometry delivers.

    Returns ``(satisfied_fraction, forbidden_violations)``. ``satisfied`` counts
    an access requirement as met when its room shares a wall at least
    ``MIN_OPENING`` long with at least one candidate; ``forbidden_violations``
    is the number of plans where a forbidden pair (e.g. a bathroom against the
    living room) actually shares a wall.
    """
    graph = adjacency_graph(plan)
    satisfied = 0
    for requirement in programme.access_requirements:
        if any(graph.has_edge(requirement.room, candidate) for candidate in requirement.candidates):
            satisfied += 1
    total = len(programme.access_requirements)
    violated = any(graph.has_edge(a, b) for a, b in programme.forbidden_pairs)
    return (satisfied / total if total else 1.0), (1 if violated else 0)


def _as_plan(plan) -> Plan:
    """A benchmark LayoutPlan as the solver's :class:`Plan`, for the door graph."""
    return Plan(
        rooms=[
            Room(r.type, r.name, r.x, r.y, r.width, r.height) for r in plan.rooms
        ],
        plot_width=plan.plot_width,
        plot_length=plan.plot_length,
        doors=plan.doors,
    )


def _door_metrics(plan, programme) -> tuple[float, float]:
    """Milestone D: what the modeled door graph actually delivers.

    Returns ``(door_satisfied, door_connectivity)``. ``door_satisfied`` is the
    fraction of access requirements that received a door on the shared wall the
    solver produced; ``door_connectivity`` is the fraction of indoor rooms
    reachable from the entrance through those doors. The solver enforces both
    as hard constraints, so both should be 1.0 on every feasible plan.
    """
    graph = walkable_graph(_as_plan(plan))
    satisfied = sum(
        any(graph.has_edge(requirement.room, candidate) for candidate in requirement.candidates)
        for requirement in programme.access_requirements
    )
    total = len(programme.access_requirements)
    return (satisfied / total if total else 1.0), reachable_fraction(_as_plan(plan))


def run_brief(
    brief: Brief,
    repository,
    engine_name: str,
    *,
    templates: list[str] | None = None,
    variants: int = 2,
    solver_budget: float = 1.5,
    topology_candidates: int = 3,
) -> BriefMetrics:
    """Run one brief through the chosen engine and aggregate the metrics."""
    name, requirements, infeasible = brief
    metrics = BriefMetrics(name=name)
    engine = LayoutEngine(requirements)

    template_ids = templates or [f"TPL-{i:03d}" for i in range(1, 21)]
    for template_id in template_ids:
        template = repository.get(template_id)
        for variation in range(variants):
            started = time.perf_counter()
            try:
                if engine_name == "solver":
                    plan = engine.generate_solver(
                        template,
                        seed=100 + variation,
                        variation_index=variation,
                        time_limit=solver_budget,
                        topology_candidates=topology_candidates,
                    )
                else:
                    plan = engine.generate(
                        template, seed=100 + variation, variation_index=variation
                    )
            except Exception as exc:
                logger.warning("%s / %s v%d raised %s", name, template_id, variation, exc)
                continue
            elapsed = time.perf_counter() - started
            metrics.times.append(elapsed)
            metrics.plans += 1

            if infeasible:
                metrics.feasibility_total += 1
                if plan.status == "infeasible":
                    metrics.infeasible_detected += 1
                    metrics.feasibility_correct += 1
                else:
                    metrics.infeasible_missed += 1
                continue
            metrics.feasibility_total += 1
            if plan.status == "infeasible":
                metrics.unexpected_infeasible += 1
                continue
            metrics.feasibility_correct += 1

            metrics.area_errors.append(_area_error(plan, requirements))
            metrics.dim_errors.append(_dim_error(plan, requirements))
            metrics.connected_fraction.append(_connectivity(plan))
            coverage = sum(r.area for r in plan.rooms) / (plan.plot_width * plan.plot_length)
            metrics.coverage.append(coverage)
            metrics.corridor_fraction.append(_corridor_fraction(plan))
            if _has_overlap(plan):
                metrics.overlap_count += 1
            if engine_name == "solver":
                programme = candidate_programmes(requirements, template)[0]
                satisfied, violated = _access_metrics(plan, programme)
                metrics.access_satisfied.append(satisfied)
                metrics.forbidden_violations += violated
                door_satisfied, door_connected = _door_metrics(plan, programme)
                metrics.door_satisfied.append(door_satisfied)
                metrics.door_connected_fraction.append(door_connected)
                if plan.quality_score is not None:
                    metrics.scores.append(plan.quality_score)
                signature = "|".join(
                    f"{r.type}:{r.x:.1f},{r.y:.1f},{r.width:.1f},{r.height:.1f}"
                    for r in plan.rooms
                )
                metrics.layout_signatures.add(signature)

    return metrics


def _summary(metrics: BriefMetrics) -> dict:
    """The headline numbers for one engine/brief, for the JSON report."""
    return {
        "plans": metrics.plans,
        "area_err_mean": mean(metrics.area_errors) if metrics.area_errors else None,
        "area_err_p95": quantiles(metrics.area_errors, n=20)[18] if metrics.area_errors else None,
        "dim_err": mean(metrics.dim_errors) if metrics.dim_errors else None,
        "connectivity": mean(metrics.connected_fraction) if metrics.connected_fraction else None,
        "door_connectivity": (
            mean(metrics.door_connected_fraction)
            if metrics.door_connected_fraction
            else None
        ),
        "overlap_rate": metrics.overlap_rate,
        "infeasible_detected": metrics.infeasible_detected,
        "infeasible_missed": metrics.infeasible_missed,
        "unexpected_infeasible": metrics.unexpected_infeasible,
        "feasibility": metrics.feasibility,
        "coverage": mean(metrics.coverage) if metrics.coverage else None,
        "corridor": mean(metrics.corridor_fraction) if metrics.corridor_fraction else None,
        "time_ms": mean(metrics.times) * 1000 if metrics.times else None,
        "access_satisfied": (
            mean(metrics.access_satisfied) if metrics.access_satisfied else None
        ),
        "door_satisfied": mean(metrics.door_satisfied) if metrics.door_satisfied else None,
        "forbidden_violations": metrics.forbidden_violations,
        "score_avg": mean(metrics.scores) if metrics.scores else None,
        "unique_layouts": len(metrics.layout_signatures),
    }


def _print_row(label: str, metrics: BriefMetrics, width: int = 82) -> None:
    p95 = quantiles(metrics.area_errors, n=20)[18] if metrics.area_errors else float("nan")
    mean_err = mean(metrics.area_errors) if metrics.area_errors else float("nan")
    dim = mean(metrics.dim_errors) if metrics.dim_errors else float("nan")
    conn = mean(metrics.connected_fraction) if metrics.connected_fraction else float("nan")
    cov = mean(metrics.coverage) if metrics.coverage else float("nan")
    corr = mean(metrics.corridor_fraction) if metrics.corridor_fraction else float("nan")
    t = mean(metrics.times) if metrics.times else float("nan")
    score = mean(metrics.scores) if metrics.scores else float("nan")
    access = mean(metrics.access_satisfied) if metrics.access_satisfied else float("nan")
    feas = metrics.feasibility if metrics.feasibility is not None else float("nan")
    print(f"{label:<{width}.{width}s}")
    print(
        f"    area-err mean {mean_err:6.3f}  p95 {p95:6.3f}   "
        f"dim-err {dim:6.3f}   connectivity {conn:5.2%}   "
        f"feasibility {feas:5.2%}   "
        f"overlap {metrics.overlap_rate:5.1%}   "
        f"coverage {cov:5.1%}   corridor {corr:5.1%}   "
        f"time {t*1000:6.0f} ms   ({metrics.plans} plans)"
    )
    if metrics.scores:
        print(f"    score avg {score:6.1f}   unique layouts {len(metrics.layout_signatures)}")
    if metrics.access_satisfied:
        door_conn = mean(metrics.door_connected_fraction) if metrics.door_connected_fraction else float("nan")  # noqa: E501
        door_ok = mean(metrics.door_satisfied) if metrics.door_satisfied else float("nan")
        print(
            f"    access {access:5.2%} satisfied   "
            f"door-conn {door_conn:5.2%}   door {door_ok:5.2%} satisfied   "
            f"forbidden-wall plans {metrics.forbidden_violations}"
        )
    if metrics.unexpected_infeasible:
        print(f"    unexpected infeasible: {metrics.unexpected_infeasible}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", default="legacy", choices=["legacy", "solver", "both"])
    parser.add_argument("--variants", type=int, default=2)
    parser.add_argument("--solver-budget", type=float, default=1.5, help="seconds per CP-SAT solve")
    parser.add_argument(
        "--topology-candidates",
        type=int,
        default=3,
        help="candidate topologies per brief for the solver engine; 1 disables the search",
    )
    parser.add_argument("--briefs", type=str, default="")
    parser.add_argument(
        "--report",
        type=str,
        default="",
        help="path to write a JSON report for regression comparison",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING")
    repository = JsonTemplateRepository(settings.templates_path)

    engines = ["legacy", "solver"] if args.engine == "both" else [args.engine]
    briefs = _briefs()

    totals = {engine: BriefMetrics(name=f"{engine} (all briefs)") for engine in engines}
    per_brief: dict[str, dict[str, dict]] = {}
    for name, requirements, infeasible in briefs:
        if args.briefs and name not in args.briefs:
            continue
        print(f"\n=== {name}  [{'infeasible' if infeasible else 'feasible'}] ===")
        per_brief[name] = {}
        for engine in engines:
            metrics = run_brief(
                (name, requirements, infeasible),
                repository,
                engine,
                variants=args.variants,
                solver_budget=args.solver_budget,
                topology_candidates=args.topology_candidates,
            )
            _print_row(f"{engine}:", metrics)
            totals[engine].merge(metrics)
            per_brief[name][engine] = _summary(metrics)

    print("\n=== TOTALS ===")
    report: dict[str, dict] = {}
    for engine in engines:
        total = totals[engine]
        _print_row(f"{engine}:", total, width=100)
        print(f"    infeasible detected: {total.infeasible_detected}")
        if total.feasibility_total:
            print(
                f"    feasibility: {total.feasibility:.2%} "
                f"({total.feasibility_correct}/{total.feasibility_total} correct, "
                f"{total.infeasible_missed} infeasible built, "
                f"{total.unexpected_infeasible} feasible refused)"
            )
        if total.unexpected_infeasible:
            print(f"    unexpected infeasible: {total.unexpected_infeasible}")
        report[engine] = {
            **_summary(total),
            "per_brief": {name: by_engine[engine] for name, by_engine in per_brief.items()},
        }

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True))
        print(f"\nReport written to {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
