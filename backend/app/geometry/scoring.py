"""Weighted quality score for a solved plan, 0..100.

The weights are the ones agreed in the re-engineering plan. Only the
components that can actually be measured count: if a component has no data it
is dropped and the remaining weights are renormalised, so an early plan is
never punished for a metric that belongs to a later milestone. (``doors_windows``
joins the scoring once Milestone B models them.)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.geometry.connectivity import stranded_indices
from app.geometry.models import Plan
from app.geometry.solver.topology import PRIVACY_AWAY_FROM
from app.geometry.units import MAX_ASPECT_RATIO
from app.schemas.requirements import FloorPlanRequirements

#: Per-component weights, summing to 100.
WEIGHTS: dict[str, int] = {
    "area": 25,
    "connectivity": 20,
    "utilization": 15,
    "circulation": 10,
    "aspect": 10,
    "lighting": 10,
    "privacy": 5,
    "doors_windows": 5,
}

#: Rooms that want daylight and therefore an external wall.
_HABITABLE = {
    "living_room",
    "dining_room",
    "kitchen",
    "master_bedroom",
    "guest_bedroom",
    "children_bedroom",
    "bedroom",
    "study_room",
}

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


def score_plan(plan: Plan, requirements: FloorPlanRequirements) -> PlanScore:
    """Score a feasible plan against the brief."""
    rects = plan.to_rects()
    components: dict[str, float] = {}

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
    habitable = [r for r in rects if r.type.value in _HABITABLE]
    if habitable:
        mean_aspect = sum(min(r.aspect, 6.0) for r in habitable) / len(habitable)
        components["aspect"] = _aspect_score(mean_aspect)

    # --- lighting: habitable rooms touching an external wall ------------------
    if habitable:
        lit = sum(
            r.x <= 0.1
            or r.y <= 0.1
            or r.x2 >= plan.plot_width - 0.1
            or r.y2 >= plan.plot_length - 0.1
            for r in habitable
        )
        components["lighting"] = 100.0 * lit / len(habitable)

    # --- privacy: bathrooms kept off the social rooms --------------------------
    bathrooms = [r for r in rects if r.type.is_bathroom]
    if bathrooms:
        social = [r for r in rects if r.type.value in PRIVACY_AWAY_FROM]
        exposed = sum(
            any(b.shared_wall_length(s) >= 2.5 for s in social) for b in bathrooms
        )
        components["privacy"] = 100.0 * (1.0 - exposed / len(bathrooms))

    # --- doors & windows: walkability plus window coverage --------------------
    walkable = 1.0 - len(stranded_indices(plan)) / len(indoor) if indoor else 1.0
    # Only habitable rooms that actually reach an external wall can take a
    # window, so an interior kitchen is not punished for having none.
    glazable = [
        r
        for r in plan.rooms
        if r.type.value in _HABITABLE
        and (
            r.x <= 0.1
            or r.y <= 0.1
            or r.x2 >= plan.plot_width - 0.1
            or r.y2 >= plan.plot_length - 0.1
        )
    ]
    if glazable:
        glazed = sum(any(w.room == r.type for w in plan.windows) for r in glazable)
        components["doors_windows"] = 100.0 * (0.5 * walkable + 0.5 * glazed / len(glazable))

    available_weight = sum(WEIGHTS[c] for c in components)
    if available_weight == 0:
        return PlanScore(components=components, total=0.0)

    total = (
        sum(components[c] * WEIGHTS[c] for c in components) / available_weight
    )
    return PlanScore(components=components, total=round(total, 1))
