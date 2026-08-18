"""Weighted quality score for a solved plan, 0..100.

The score is the blend agreed for the accuracy-and-precision milestone:

    total = 0.7 x architecture + 0.3 x geometry

The architectural half measures *designed* qualities - the corridor as a
spine, doors clear of corners, the wet zone pulled to the bedrooms, the
private zone off circulation. The geometry half (in
:mod:`app.geometry.accuracy`) measures *exactness* - sized rooms hitting
their areas and dimensions, edges on the grid and aligned, the area ledger
reconciling, rendered labels round-tripping. Blending keeps a
dimensionally sloppy plan from passing for beautiful while still letting a
geometrically perfect but uninhabitable plan score badly.

Only the components that can actually be measured count: if a component has
no data it is dropped and the remaining weights are renormalised, so a plan
is never punished for a metric that does not apply. Nothing in this module
changes the search - the blend is applied to the same solved plans the
solver already produces, so it can never make a brief infeasible.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.geometry.accuracy import GEOMETRY_WEIGHTS, geometry_score
from app.geometry.models import Plan
from app.geometry.quality import (
    CORRIDOR_MIN_WIDTH,
    HABITABLE,
    quality_metrics,
)
from app.schemas.requirements import FloorPlanRequirements

#: Per-component weights of the architectural half, summing to 100.
#: The axes that used to soak up weight without discriminating - pure
#: ``area``, ``connectivity``, ``utilization`` and ``aspect`` - moved to the
#: geometry half, where they belong; their points went to measured
#: architecture. ``connectivity`` is the geometry's job now: the strict gate
#: makes every accepted solver plan 100% walkable, so it no longer
#: discriminates architecturally.
ARCHITECTURE_WEIGHTS: dict[str, int] = {
    "circulation": 10,
    "lighting": 10,
    "doors_windows": 10,
    "wet_zone": 15,
    "zone_privacy": 12,
    "corridor_quality": 18,
    "door_quality": 15,
    "space_use": 5,
    "furniture": 5,
}

#: Backwards-compatible name for the architectural weights.
WEIGHTS: dict[str, int] = ARCHITECTURE_WEIGHTS

#: Rooms that want daylight and therefore an external wall. Owned by
#: :mod:`app.geometry.quality`.
_HABITABLE = HABITABLE

#: Circulation rooms, as a fraction of built-up area.
_CIRCULATION = {"passage", "foyer"}

#: Weight of the geometry half in the blended total.
_GEOMETRY_WEIGHT = 0.3


@dataclass(frozen=True)
class PlanScore:
    """Component breakdown plus the blended total.

    ``components`` and ``architecture`` describe the architectural half;
    ``geometry`` is the geometry-accuracy half from
    :mod:`app.geometry.accuracy`. ``total`` is
    ``round(0.7 * architecture + 0.3 * geometry, 1)``.
    """

    components: dict[str, float]
    architecture: float
    geometry: float
    total: float

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 1) for k, v in self.components.items()}


def _weighted(components: dict[str, float], weights: dict[str, int]) -> float:
    """Weighted mean of the present components, renormalised over their weights."""
    available = sum(weights[c] for c in components)
    if available == 0:
        return 0.0
    return sum(components[c] * weights[c] for c in components) / available


def _architecture_components(
    plan: Plan, requirements: FloorPlanRequirements
) -> dict[str, float]:
    """Component scores (0..100) for the architectural axes with enough data."""
    rects = plan.to_rects()
    components: dict[str, float] = {}
    metrics = quality_metrics(plan)

    # --- circulation: the corridor band [5%, 20%] of built-up area ------------
    built_up = plan.built_up_sqft
    circulation_area = sum(r.area for r in rects if r.type.value in _CIRCULATION)
    if built_up > 0:
        share = circulation_area / built_up
        if 0.05 <= share <= 0.20:
            components["circulation"] = 100.0
        else:
            components["circulation"] = max(
                0.0, 100.0 * (1.0 - abs(share - 0.125) / 0.30)
            )

    # --- lighting: external wall + cross-ventilation (two external walls) -----
    habitable = [r for r in rects if r.type in _HABITABLE]
    if habitable:
        lit = sum(
            r.x <= 0.1
            or r.y <= 0.1
            or r.x2 >= plan.plot_width - 0.1
            or r.y2 >= plan.plot_length - 0.1
            for r in habitable
        )
        cross = metrics.cross_ventilated
        components["lighting"] = 100.0 * (
            0.7 * lit / len(habitable) + 0.3 * cross / len(habitable)
        )

    # --- doors & windows: window coverage and window placement ---------------
    # Walkability is guaranteed by the strict gate, so this axis concentrates
    # on the windows: every habitable room that reaches an external wall
    # should get one, and no window should crowd a corner or a door.
    glazable = [
        r
        for r in plan.rooms
        if r.type in _HABITABLE
        and (
            r.x <= 0.1
            or r.y <= 0.1
            or r.x2 >= plan.plot_width - 0.1
            or r.y2 >= plan.plot_length - 0.1
        )
    ]
    if glazable:
        glazed = sum(any(w.room == r.type for w in plan.windows) for r in glazable)
        base = 100.0 * glazed / len(glazable)
        window_violations = metrics.window_corner_violations + metrics.window_door_violations
        components["doors_windows"] = max(
            0.0, base - 20.0 * window_violations / max(1, len(plan.windows))
        )

    # --- wet_zone: bathroom shape, placement near bedrooms, off social --------
    if metrics.bathroom_count > 0:
        penalty = 25.0 * metrics.slender_bathrooms / metrics.bathroom_count
        if metrics.attached_bath_bedroom_share is not None:
            penalty += 30.0 * (1.0 - metrics.attached_bath_bedroom_share)
        if metrics.common_bath_bedroom_share is not None:
            penalty += 20.0 * (1.0 - metrics.common_bath_bedroom_share)
        penalty += 25.0 * min(1.0, metrics.bathroom_social_walls / metrics.bathroom_count)
        components["wet_zone"] = max(0.0, 100.0 - penalty)

    # --- zone_privacy: bedrooms kept as a private zone off circulation --------
    bedrooms = [r for r in rects if r.type.is_bedroom]
    if bedrooms:
        penalty = 40.0 * metrics.bedroom_social_walls / len(bedrooms)
        if metrics.bedroom_from_circulation is not None:
            penalty += 40.0 * (1.0 - metrics.bedroom_from_circulation)
        if metrics.private_zone_share is not None:
            penalty += 20.0 * (1.0 - metrics.private_zone_share)
        components["zone_privacy"] = max(0.0, 100.0 - penalty)

    # --- corridor_quality: the passage/foyer band as a designed spine ---------
    if metrics.corridor_rooms > 0:
        penalty = 0.0
        if metrics.corridor_fragmentation is not None:
            penalty += 25.0 * metrics.corridor_fragmentation
        if (
            metrics.corridor_min_width is not None
            and metrics.corridor_min_width < CORRIDOR_MIN_WIDTH
        ):
            penalty += (
                35.0
                * (CORRIDOR_MIN_WIDTH - metrics.corridor_min_width)
                / CORRIDOR_MIN_WIDTH
            )
        if metrics.corridor_width_std is not None and metrics.corridor_width_std > 1.0:
            penalty += 15.0 * min(1.0, (metrics.corridor_width_std - 1.0) / 1.5)
        if metrics.corridor_spine_ratio is not None:
            penalty += 25.0 * (1.0 - metrics.corridor_spine_ratio)
        components["corridor_quality"] = max(0.0, 100.0 - penalty)

    # --- door_quality: corner clearance, wall spacing, no opposing doors ------
    if metrics.door_count > 0:
        violations = (
            metrics.door_corner_violations
            + metrics.door_spacing_violations
            + metrics.opposing_door_pairs
        )
        components["door_quality"] = max(
            0.0, 100.0 - 50.0 * violations / metrics.door_count
        )

    # --- space_use: balconies that serve no habitable room --------------------
    # The leftover-plot half of the old axis now lives in the geometry score's
    # ``unused`` component, so this is purely the balcony penalty.
    penalty = 20.0 if metrics.balcony_without_habitable > 0 else 0.0
    components["space_use"] = max(0.0, 100.0 - penalty)

    # --- furniture: rooms whose clear space cannot host their furniture --------
    if metrics.furniture_rooms > 0:
        components["furniture"] = 100.0 * (
            1.0 - metrics.furniture_shortfalls / metrics.furniture_rooms
        )

    return components


def score_plan(plan: Plan, requirements: FloorPlanRequirements) -> PlanScore:
    """Score a feasible plan against the brief.

    Returns the architectural components, the two half-scores and the blended
    ``total = round(0.7 * architecture + 0.3 * geometry, 1)``.
    """
    architecture = _architecture_components(plan, requirements)
    arch_total = _weighted(architecture, ARCHITECTURE_WEIGHTS)
    geometry = geometry_score(plan, requirements)
    geo_total = _weighted(geometry, GEOMETRY_WEIGHTS)
    return PlanScore(
        components=architecture,
        architecture=round(arch_total, 1),
        geometry=round(geo_total, 1),
        total=round((1.0 - _GEOMETRY_WEIGHT) * arch_total + _GEOMETRY_WEIGHT * geo_total, 1),
    )
