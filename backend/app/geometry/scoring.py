"""Weighted quality score for a solved plan, 0..100.

The weights are the ones agreed in the re-engineering plan, rebalanced for the
architectural-quality milestone. The axes that used to soak up weight without
discriminating - ``connectivity`` scores 100 on every solver plan, ``area``
bought little - give way to *measured* architecture: corridor quality, door
placement, the wet zone, the private zone and wasted space. Only the components
that can actually be measured count: if a component has no data it is dropped
and the remaining weights are renormalised, so an early plan is never punished
for a metric that belongs to a later milestone.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.geometry.connectivity import stranded_indices
from app.geometry.models import Plan
from app.geometry.quality import (
    CORRIDOR_MIN_WIDTH,
    HABITABLE,
    quality_metrics,
)
from app.geometry.units import MAX_ASPECT_RATIO
from app.schemas.requirements import FloorPlanRequirements

#: Per-component weights, summing to 100.
#: ``connectivity`` carries a small weight now: the strict gate makes every
#: accepted solver plan 100% walkable, so the axis no longer discriminates.
#: The points it used to hold moved to measured architectural features.
WEIGHTS: dict[str, int] = {
    "area": 20,
    "connectivity": 10,
    "utilization": 8,
    "circulation": 8,
    "aspect": 8,
    "lighting": 6,
    "doors_windows": 5,
    "wet_zone": 8,
    "zone_privacy": 6,
    "corridor_quality": 10,
    "door_quality": 6,
    "space_use": 5,
}

#: Rooms that want daylight and therefore an external wall. Owned by
#: :mod:`app.geometry.quality`.
_HABITABLE = HABITABLE

#: Circulation rooms, as a fraction of built-up area.
_CIRCULATION = {"passage", "foyer"}


@dataclass(frozen=True)
class PlanScore:
    """Component breakdown plus the weighted total."""

    components: dict[str, float]
    total: float

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 1) for k, v in self.components.items()}


def _aspect_score(mean_aspect: float) -> float:
    """Piecewise: <=2 full marks, 2.5 -> 60, 3.0 -> 20, >=3.6 -> 0."""
    if mean_aspect <= 2.0:
        return 100.0
    if mean_aspect <= 2.5:
        return 100.0 - (mean_aspect - 2.0) * 80.0
    if mean_aspect <= 3.0:
        return 60.0 - (mean_aspect - 2.5) * 80.0
    if mean_aspect <= MAX_ASPECT_RATIO:
        return 20.0 * (MAX_ASPECT_RATIO - mean_aspect) / (MAX_ASPECT_RATIO - 3.0)
    return 0.0


def _uncovered_score(fraction: float) -> float:
    """Space use: <=10% leftover is fine, 25% -> 40, >=40% -> 0."""
    if fraction <= 0.10:
        return 100.0
    if fraction <= 0.25:
        return 100.0 - (fraction - 0.10) * 400.0
    if fraction <= 0.40:
        return 40.0 * (0.40 - fraction) / 0.15
    return 0.0


def score_plan(plan: Plan, requirements: FloorPlanRequirements) -> PlanScore:
    """Score a feasible plan against the brief."""
    rects = plan.to_rects()
    components: dict[str, float] = {}
    metrics = quality_metrics(plan)

    # --- area: how close the sized rooms land on their requested size ---------
    targets = requirements.room_targets
    sized = [r for r in rects if r.type in targets]
    if sized:
        relative = [
            abs(r.area - targets[r.type].area) / targets[r.type].area for r in sized
        ]
        components["area"] = max(0.0, 100.0 * (1.0 - sum(relative) / len(relative)))

    # --- connectivity: fraction of indoor rooms reachable from circulation ----
    indoor = [r for r in rects if not r.type.is_outdoor]
    if indoor:
        stranded = stranded_indices(plan)
        components["connectivity"] = 100.0 * (1.0 - len(stranded) / len(indoor))

    # --- utilization: how much of the plot the rooms cover --------------------
    plot_area = plan.plot_width * plan.plot_length
    if plot_area > 0:
        components["utilization"] = min(
            100.0, 100.0 * sum(r.area for r in rects) / plot_area
        )

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

    # --- aspect: how corridor-like the habitable rooms have become ------------
    habitable = [r for r in rects if r.type in _HABITABLE]
    if habitable:
        mean_aspect = sum(min(r.aspect, 6.0) for r in habitable) / len(habitable)
        components["aspect"] = _aspect_score(mean_aspect)

    # --- lighting: external wall + cross-ventilation (two external walls) -----
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

    # --- doors & windows: walkability plus window coverage and placement ------
    walkable = 1.0 - len(stranded_indices(plan)) / len(indoor) if indoor else 1.0
    # Only habitable rooms that actually reach an external wall can take a
    # window, so an interior kitchen is not punished for having none.
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
        base = 100.0 * (0.5 * walkable + 0.5 * glazed / len(glazable))
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

    # --- space_use: leftover plot and balconies that serve no habitable room --
    penalty = 20.0 if metrics.balcony_without_habitable > 0 else 0.0
    components["space_use"] = max(
        0.0, _uncovered_score(metrics.uncovered_fraction) - penalty
    )

    available_weight = sum(WEIGHTS[c] for c in components)
    if available_weight == 0:
        return PlanScore(components=components, total=0.0)

    total = (
        sum(components[c] * WEIGHTS[c] for c in components) / available_weight
    )
    return PlanScore(components=components, total=round(total, 1))
