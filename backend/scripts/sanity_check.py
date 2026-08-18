"""Smoke-test the adaptive solver path on one real brief and verify invariants.

    python scripts/sanity_check.py

Runs the CP-SAT engine with the topology search and the adaptive
re-prioritisation enabled (``topology_candidates > 1``) on a standard 3BHK
brief, then asserts the invariants the whole milestone hangs on:

* the plan is reported feasible, not refused;
* the search log has the documented shape (a ``label`` and a ``status`` per
  candidate, at least one survivor, never more than the candidate budget);
* the rooms overlap nowhere;
* every indoor room is reachable from circulation *through the modeled doors*;
* the strict wall/door/window gate accepts the finished plan.

On top of the hard invariants it prints the architectural quality measurement
of the winning plan, so a regression in the axes the adaptive search optimises
(corridor, wet zone, daylight, door/window placement) shows up in CI logs even
when nothing throws.

Exit status is 0 when every invariant holds, 1 otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.geometry.connectivity import stranded_indices
from app.geometry.envelope import Envelope
from app.geometry.layout_engine import LayoutEngine
from app.geometry.models import Plan, Room
from app.geometry.quality import quality_metrics
from app.geometry.solver.topology import candidate_programmes
from app.geometry.validation import validate_plan
from app.repositories.template_repository import JsonTemplateRepository
from app.schemas.enums import BHKType, Facing, InteriorStyle, RoomType
from app.schemas.requirements import (
    BathroomRequirements,
    FloorPlanRequirements,
    PlotDetails,
    RoomDimensions,
)

logger = get_logger("sanity")

ROOMS_3BHK = [
    RoomType.LIVING_ROOM,
    RoomType.DINING_ROOM,
    RoomType.KITCHEN,
    RoomType.MASTER_BEDROOM,
    RoomType.GUEST_BEDROOM,
    RoomType.CHILDREN_BEDROOM,
]


def _brief() -> FloorPlanRequirements:
    return FloorPlanRequirements(
        plot=PlotDetails(width_ft=30, length_ft=45, facing=Facing.EAST),
        bhk=BHKType.BHK3,
        rooms=ROOMS_3BHK,
        bathrooms=BathroomRequirements(attached_count=2, common_count=1),
        features=[RoomType.BALCONY, RoomType.PARKING],
        room_dimensions={
            RoomType.LIVING_ROOM: RoomDimensions(length_ft=16, width_ft=14),
            RoomType.DINING_ROOM: RoomDimensions(length_ft=12, width_ft=10),
            RoomType.KITCHEN: RoomDimensions(length_ft=10, width_ft=10),
            RoomType.MASTER_BEDROOM: RoomDimensions(length_ft=14, width_ft=12),
            RoomType.GUEST_BEDROOM: RoomDimensions(length_ft=12, width_ft=11),
            RoomType.CHILDREN_BEDROOM: RoomDimensions(length_ft=11, width_ft=10),
        },
        style=InteriorStyle.MODERN,
    )


def _as_solver_plan(plan) -> Plan:
    """The engine's ``LayoutPlan`` as the geometry :class:`Plan`."""
    return Plan(
        rooms=[Room(r.type, r.name, r.x, r.y, r.width, r.height) for r in plan.rooms],
        plot_width=plan.plot_width,
        plot_length=plan.plot_length,
        doors=plan.doors,
        windows=plan.windows,
    )


def _has_overlap(rooms: list) -> bool:
    return any(
        rooms[i].overlaps(rooms[j], tolerance=0.25)
        for i in range(len(rooms))
        for j in range(i + 1, len(rooms))
    )


def main() -> int:
    parser = __import__("argparse").ArgumentParser(description=__doc__)
    parser.add_argument("--template", default="TPL-001")
    parser.add_argument("--seed", type=int, default=100)
    parser.add_argument("--time-limit", type=float, default=1.5)
    parser.add_argument("--candidates", type=int, default=3)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)

    requirements = _brief()
    template = JsonTemplateRepository(settings.templates_path).get(args.template)
    engine = LayoutEngine(requirements)

    plan = engine.generate_solver(
        template,
        seed=args.seed,
        variation_index=0,
        time_limit=args.time_limit,
        topology_candidates=args.candidates,
    )

    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        print(f"  [{'ok' if condition else 'FAIL'}] {message}")
        if not condition:
            failures.append(message)

    print(f"brief         : 3BHK standard 30x45 x template {template.id}")
    print(f"status        : {plan.status}")
    print(f"seed          : {args.seed}   candidate budget: {args.candidates}")
    print()

    check(plan.status == "feasible", "engine reports the brief feasible")
    if plan.status != "feasible":
        print(f"  reason: {plan.infeasibility}")
        print("\nsanity check FAILED")
        return 1

    search = plan.topology_search
    check(search is not None, "topology search log is present")
    search = search or []
    check(
        1 <= len(search) <= args.candidates,
        f"search solved 1..{args.candidates} candidates (saw {len(search)})",
    )
    check(
        all("label" in entry and "status" in entry for entry in search),
        "every entry has a label and a status",
    )
    check(
        any(entry["status"] == "feasible" for entry in search),
        "at least one candidate survived the gate",
    )

    solver_plan = _as_solver_plan(plan)
    check(not _has_overlap(plan.rooms), "no two rooms overlap")
    check(stranded_indices(solver_plan) == [], "every indoor room is reachable through the doors")

    base = candidate_programmes(requirements, template)[0]
    report = validate_plan(solver_plan, Envelope(plan.plot_width, plan.plot_length), base.specs)
    check(report.ok, "strict wall/door/window gate accepts the plan")
    for warning in report.warnings:
        print(f"  warning       : {warning}")
    check(plan.quality_score is not None, "plan was scored")

    print("\nsearch log")
    for entry in search:
        status = entry["status"]
        score = entry.get("score")
        optimal = "optimal" if entry.get("is_optimal") else ""
        print(
            f"  {entry['label']:<40s} {status:>10s}"
            f"{('  score ' + f'{score:.2f}' if score is not None else ''):>14s} {optimal}"
        )

    metrics = quality_metrics(solver_plan)
    print("\narchitectural quality of the winning plan")
    print(
        "  corridor rooms      : "
        f"{metrics.corridor_rooms} band(s) x min-width {metrics.corridor_min_width}"
    )
    print(
        "  door corner/spacing : "
        f"{metrics.door_corner_violations} / {metrics.door_spacing_violations} violations"
    )
    print(f"  opposing doors      : {metrics.opposing_door_pairs} facing pairs")
    print(
        "  wet zone            : "
        f"{metrics.slender_bathrooms} slender, {metrics.bathroom_social_walls} off social, "
        f"attached near bedroom {metrics.attached_bath_bedroom_share}, "
        f"common near bedroom {metrics.common_bath_bedroom_share}"
    )
    print(
        "  daylight            : window corner/door "
        f"{metrics.window_corner_violations} / {metrics.window_door_violations} violations"
    )
    print(f"  score               : {plan.quality_score:.2f}")

    if failures:
        print("\nsanity check FAILED:")
        for message in failures:
            print(f"  - {message}")
        return 1
    print("\nsanity check PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
