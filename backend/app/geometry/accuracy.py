"""Geometry accuracy: dimensional and spatial correctness of a solved plan.

The architectural scorer answers "does this read as a house an architect would
sign?"; this module answers the other half of the milestone - "is the plan
*exact*?". Every component is a pure function of the solved geometry:

* ``area``         - sized rooms land on their requested floor area,
* ``dimensions``   - sized rooms land on their requested long/short sides,
* ``aspect``       - habitable rooms stay away from ribbon proportions,
* ``connectivity`` - every indoor room is reachable through the door graph,
* ``alignment``    - no wall edge sits a few inches off its neighbours,
* ``grid``         - every room edge sits on the half-foot grid,
* ``ledger``       - the wall/clear/gross area ledger reconciles exactly,
* ``rendered``     - the labels the renderer draws round-trip to the geometry,
* ``unused``       - the plot area no room claims stays small.

``geometry_score`` returns the weighted blend of the components that have data
(missing ones are dropped and the weights renormalised, exactly like the
architectural scorer), so a plan is never punished for a metric that does not
apply. The overall score in :mod:`app.geometry.scoring` is
``0.7 x architectural + 0.3 x geometry``, which stops a dimensionally sloppy
plan from passing for beautiful and stops a geometrically perfect but
uninhabitable plan from being considered good.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from statistics import mean

from app.geometry.connectivity import CIRCULATION_TYPES, stranded_indices
from app.geometry.labels import decode_feet_inches, feet_inches
from app.geometry.models import Plan, Room
from app.geometry.units import GRID
from app.schemas.requirements import FloorPlanRequirements


@dataclass(frozen=True)
class AccuracyMetrics:
    """Raw, unweighted accuracy numbers. ``None`` means "no data to compute"."""

    area_err: float | None
    dim_err: float | None
    mean_aspect: float | None
    stranded_rooms: int
    stray_edges: int
    off_grid_edges: int
    ledger_residual: float | None
    ledger_ok: bool | None
    label_mismatches: int
    unused_fraction: float


#: Geometry axes, summing to 100. Blended separately from the architectural
#: axes in :mod:`app.geometry.scoring`.
GEOMETRY_WEIGHTS: dict[str, int] = {
    "area": 20,
    "dimensions": 20,
    "aspect": 10,
    "connectivity": 5,
    "alignment": 10,
    "grid": 5,
    "ledger": 10,
    "rendered": 10,
    "unused": 10,
}


def _area_err(plan: Plan, requirements: FloorPlanRequirements) -> float | None:
    """Mean relative error of every sized room's floor area vs its target."""
    targets = requirements.room_targets
    errors = [
        abs(r.area - targets[r.type].area) / targets[r.type].area
        for r in plan.rooms
        if r.type in targets
    ]
    return mean(errors) if errors else None


def _dim_err(plan: Plan, requirements: FloorPlanRequirements) -> float | None:
    """Mean relative error of long/short sides where the brief pins them."""
    targets = requirements.room_targets
    errors: list[float] = []
    for r in plan.rooms:
        target = targets.get(r.type)
        if target is None or not target.long_side or not target.short_side:
            continue
        built_long, built_short = max(r.width, r.height), min(r.width, r.height)
        errors.append(abs(built_long - target.long_side) / target.long_side)
        errors.append(abs(built_short - target.short_side) / target.short_side)
    return mean(errors) if errors else None


def _stranded_rooms(plan: Plan) -> int:
    """Indoor rooms you cannot walk to from the living room through the doors.

    Delegates to the door-graph walker in :mod:`app.geometry.connectivity`, so
    this measures the *modeled* movement network rather than bare adjacency.
    """
    return len(stranded_indices(plan))


def _stray_edges(plan: Plan) -> int:
    """Wall edges that stop inches away from a neighbour instead of aligning.

    Replicates the validator's alignment check on the raw solved coordinates:
    when a room edge and a neighbour's edge are within a sub-foot gap they
    should have been one wall line, so each near-miss costs the alignment axis.
    """
    strays = 0
    for edges in (
        sorted(value for room in plan.rooms for value in (room.x, room.x2)),
        sorted(value for room in plan.rooms for value in (room.y, room.y2)),
    ):
        for left, right in pairwise(edges):
            if 0 < right - left < GRID:
                strays += 1
    return strays


def _off_grid_edges(plan: Plan) -> int:
    """Room edges that fall off the half-foot grid, counting each stray edge."""
    off = 0
    for room in plan.rooms:
        for value in (room.x, room.x2, room.y, room.y2):
            rem = abs(value % GRID)
            if min(rem, GRID - rem) > 1e-3:
                off += 1
    return off


def _label_mismatches(plan: Plan) -> int:
    """Labels the renderer draws that do not round-trip to the geometry.

    Each mismatch is one rendered number (width, height, plot dimension) that
    decodes to a different length than the authoritative value. A perfect plan
    has zero; the tally feeds the ``rendered`` axis.
    """
    mismatches = 0
    for room in plan.rooms:
        for value in (room.width, room.height):
            if abs(decode_feet_inches(feet_inches(value)) - value) > 0.25:
                mismatches += 1
    for value in (plan.plot_width, plan.plot_length):
        if abs(decode_feet_inches(feet_inches(value)) - value) > 0.25:
            mismatches += 1
    return mismatches


def _aspect_ratio(plan: Plan) -> float | None:
    """Mean aspect (long/short) of the habitable rooms only."""
    habitable = [
        r for r in plan.rooms
        if not r.type.is_outdoor and r.type not in CIRCULATION_TYPES
    ]
    if not habitable:
        return None
    return mean(
        max(r.width, r.height) / min(r.width, r.height) for r in habitable
    )


def _aspect_score(aspect: float) -> float:
    """Score an aspect ratio: 2.0 is neutral, 1.0 square is perfect, 4.0 floor."""
    if aspect <= 1.0:
        return 100.0
    if aspect >= 4.0:
        return 0.0
    return max(0.0, 100.0 * (1.0 - (aspect - 1.0) / 3.0))


def _uncovered_fraction(plan: Plan) -> float:
    """Fraction of the plot footprint no room claims (overlaps ignored)."""
    plot_area = plan.plot_width * plan.plot_length
    if plot_area <= 0:
        return 1.0
    covered = sum(room.area for room in plan.rooms)
    return max(0.0, 1.0 - covered / plot_area)


def _unused_fraction(plan: Plan) -> float:
    """Fraction of the plot that stays open (uncovered), capped at 1."""
    return min(1.0, _uncovered_fraction(plan))


def _uncovered_score(uncovered: float) -> float:
    """Score for unused land: 15% of the plot is fine, more is wasted."""
    if uncovered <= 0.15:
        return 100.0
    if uncovered >= 0.75:
        return 0.0
    return max(0.0, 100.0 * (1.0 - (uncovered - 0.15) / 0.6))


def _ledger_residual(plan: Plan) -> float | None:
    """How far the wall model's gross == clear + wall ledger is off, in sqft."""
    if plan.walls is None:
        return None
    return abs((plan.clear_area + plan.wall_area) - plan.gross_area)


def _ledger_ok(plan: Plan) -> bool | None:
    """Whether the area ledger reconciles within a 0.1% tolerance."""
    if plan.walls is None:
        return None
    residual = _ledger_residual(plan)
    assert residual is not None
    tolerance = max(1.0, plan.gross_area) * 1e-3
    return residual <= tolerance


def _normalize(plan: Plan) -> Plan:
    """A plan whose rooms expose the shared-wall helpers the metrics need."""
    rooms = [
        r if hasattr(r, "shared_wall") else Room(r.type, r.name, r.x, r.y, r.width, r.height)
        for r in plan.rooms
    ]
    if all(r is p for r, p in zip(rooms, plan.rooms, strict=True)):
        return plan
    normalized = Plan(
        rooms=rooms, plot_width=plan.plot_width, plot_length=plan.plot_length
    )
    normalized.doors = plan.doors
    normalized.windows = plan.windows
    normalized.walls = plan.walls
    normalized.status = plan.status
    normalized.quality_score = plan.quality_score
    normalized.geometry_score = plan.geometry_score
    return normalized


def accuracy_metrics(
    plan: Plan, requirements: FloorPlanRequirements
) -> AccuracyMetrics:
    """Raw accuracy numbers for a solved plan.

    Accepts the engine's internal :class:`~app.geometry.models.Plan` or a
    public layout whose rooms are ``Rect``; the two differ only in the
    shared-wall helpers the reach check needs, so ``Rect`` rooms are lifted to
    ``Room`` first.
    """
    plan = _normalize(plan)
    return AccuracyMetrics(
        area_err=_area_err(plan, requirements),
        dim_err=_dim_err(plan, requirements),
        mean_aspect=_aspect_ratio(plan),
        stranded_rooms=_stranded_rooms(plan),
        stray_edges=_stray_edges(plan),
        off_grid_edges=_off_grid_edges(plan),
        ledger_residual=_ledger_residual(plan),
        ledger_ok=_ledger_ok(plan),
        label_mismatches=_label_mismatches(plan),
        unused_fraction=_unused_fraction(plan),
    )


def geometry_score(
    plan: Plan, requirements: FloorPlanRequirements
) -> dict[str, float]:
    """Component scores (0..100) for the geometry axes with enough data."""
    metrics = accuracy_metrics(plan, requirements)
    components: dict[str, float] = {}
    if metrics.area_err is not None:
        components["area"] = max(0.0, 100.0 * (1.0 - metrics.area_err))
    if metrics.dim_err is not None:
        components["dimensions"] = max(0.0, 100.0 * (1.0 - metrics.dim_err))
    if metrics.mean_aspect is not None:
        components["aspect"] = _aspect_score(metrics.mean_aspect)
    indoor = [r for r in plan.rooms if not r.type.is_outdoor]
    if indoor:
        components["connectivity"] = 100.0 * (
            1.0 - metrics.stranded_rooms / len(indoor)
        )
    components["alignment"] = max(0.0, 100.0 - 5.0 * metrics.stray_edges)
    total_edges = len(plan.rooms) * 4
    components["grid"] = (
        100.0 * (1.0 - metrics.off_grid_edges / total_edges)
        if total_edges
        else 100.0
    )
    if metrics.ledger_ok is not None:
        if metrics.ledger_ok:
            components["ledger"] = 100.0
        else:
            residual = metrics.ledger_residual or 0.0
            components["ledger"] = max(0.0, 100.0 - 10.0 * residual)
    components["rendered"] = max(0.0, 100.0 - 10.0 * metrics.label_mismatches)
    components["unused"] = _uncovered_score(metrics.unused_fraction)
    return components


__all__ = [
    "GEOMETRY_WEIGHTS",
    "AccuracyMetrics",
    "accuracy_metrics",
    "geometry_score",
]
