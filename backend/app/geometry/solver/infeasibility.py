"""Why a brief is infeasible, and what would make it feasible.

Two tiers of diagnosis:

* :func:`diagnose_brief` - static arithmetic run *before* the solver. Catches
  the cases that are true by inspection: the buildable area is smaller than
  the minimum the rooms need, or a room is wider than the plot on its short
  side.
* :func:`diagnose_solver` - a relaxation ladder run *after* the strict model
  reports infeasible. It re-solves with constraints peeled back one at a time
  to find which constraint is the one that cannot be met:
  shape/edge constraints first, then the area floor.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.geometry.envelope import Envelope
from app.geometry.solver import cp_sat
from app.geometry.solver.cp_sat import SolverOutcome
from app.geometry.solver.topology import RoomSpec
from app.geometry.units import area_to_cells, cells_area_to_ft, to_cells


@dataclass(frozen=True)
class InfeasibilityDiagnostics:
    """A human- and machine-readable account of why no layout exists."""

    #: ``"area"`` / ``"shape"`` / ``"packing"`` / ``"envelope"``
    stage: str
    reason: str
    detail: str
    suggestions: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "stage": self.stage,
            "reason": self.reason,
            "detail": self.detail,
            "suggestions": list(self.suggestions),
        }


def diagnose_brief(
    specs: list[RoomSpec], envelope: Envelope
) -> InfeasibilityDiagnostics | None:
    """Static pre-flight checks. ``None`` means the brief deserves a solve."""
    W, H = envelope.cells
    if not envelope.within_max_extent:
        return InfeasibilityDiagnostics(
            "envelope",
            "the plot is larger than the solver's grid supports",
            f"the buildable area is {envelope.width:g}x{envelope.length:g} ft; "
            "the solver grid caps at 100 ft per side",
            ("Use a plot of at most 100 ft on each side",),
        )
    if W <= 0 or H <= 0:
        return InfeasibilityDiagnostics(
            "envelope",
            "the buildable area has no interior",
            f"after setbacks the buildable area is {envelope.buildable_width:g}x"
            f"{envelope.buildable_length:g} ft",
            ("Enlarge the plot or reduce the setbacks",),
        )

    for spec in specs:
        if to_cells(spec.min_side) > W or to_cells(spec.min_side) > H:
            return InfeasibilityDiagnostics(
                "shape",
                f"{spec.name} cannot fit the plot",
                f"{spec.name} needs {spec.min_side:g} ft on its short side but the "
                f"buildable area is {envelope.buildable_width:g}x"
                f"{envelope.buildable_length:g} ft",
                (
                    f"Shrink {spec.name.lower()} below {spec.min_side:g} ft",
                    "Choose a larger plot",
                ),
            )

    minimum_total = sum(area_to_cells(spec.min_area) for spec in specs)
    if minimum_total > envelope.area_cells:
        return InfeasibilityDiagnostics(
            "area",
            "the rooms need more floor area than the plot provides",
            f"the rooms need at least {cells_area_to_ft(minimum_total):.0f} sq ft "
            f"but the buildable area is {envelope.area_sqft:.0f} sq ft",
            (
                "Reduce the number of rooms",
                "Remove a feature such as parking or a garden",
                "Choose a larger plot",
            ),
        )

    return None


def diagnose_solver(
    specs: list[RoomSpec],
    envelope: Envelope,
    *,
    seed: int = 1,
    time_limit: float = 3.0,
) -> InfeasibilityDiagnostics:
    """Relaxation ladder: find which hard constraint cannot be met.

    Each stage re-solves with constraints peeled back. A timeout is *not*
    evidence of infeasibility - only a proven ``INFEASIBLE`` final stage gets
    the "packing" verdict.
    """
    ladder_budget = max(1.5, time_limit * 0.5)

    # 1. Keep the area floors, drop the sized-room shape and outdoor edge rules.
    without_shape = cp_sat.solve(
        specs,
        envelope,
        seed=seed,
        time_limit=ladder_budget,
        shape=False,
        edge=False,
        strict_area=True,
        validate=False,
    )
    if without_shape.status == cp_sat.FEASIBLE:
        return InfeasibilityDiagnostics(
            "shape",
            "the requested room shapes cannot be packed into the plot",
            "Relaxing the exact long/short sides and the outdoor edge placement "
            "makes this brief feasible, so the sized rooms' proportions are what "
            "will not fit",
            (
                "Adjust the sized rooms' length/width ratios",
                "Move balconies or parking to a plot edge",
                "Choose a larger or differently shaped plot",
            ),
        )

    # 2. Also drop the area floors down to the absolute usable minimum. This is
    #    the decisive solve, so it gets a longer budget.
    at_minimum = cp_sat.solve(
        specs,
        envelope,
        seed=seed,
        time_limit=max(time_limit, ladder_budget),
        shape=False,
        edge=False,
        strict_area=False,
        validate=False,
    )
    if at_minimum.status == cp_sat.FEASIBLE:
        return InfeasibilityDiagnostics(
            "area",
            "the brief demands more floor area than the plot can hold",
            f"Even at absolute minimum room sizes, the full room set cannot be "
            f"tiled into {envelope.area_sqft:.0f} sq ft of buildable area",
            (
                "Reduce the number of rooms",
                "Shrink the sized rooms' requested areas",
                "Choose a larger plot",
            ),
        )
    if at_minimum.status == cp_sat.INFEASIBLE:
        return InfeasibilityDiagnostics(
            "packing",
            "the room set cannot be tiled into the plot at all",
            f"Even with every constraint relaxed to its absolute minimum, {len(specs)} "
            f"rooms will not fit the {envelope.area_sqft:.0f} sq ft buildable area",
            (
                "Remove a room or a feature",
                "Choose a larger plot",
            ),
        )

    return InfeasibilityDiagnostics(
        "timeout",
        "the solver could not decide whether this brief is feasible",
        f"Neither the strict model nor its relaxed variants were solved to "
        f"completion within the {time_limit:g}s budget",
        (
            "Increase the solver time budget",
            "Simplify the brief (fewer sized rooms or features)",
        ),
    )


__all__ = ["InfeasibilityDiagnostics", "SolverOutcome", "diagnose_brief", "diagnose_solver"]
