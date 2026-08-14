"""The CP-SAT packing core.

Hard constraints (a solution violates none of these, or it does not exist):

* every room is entirely inside the buildable envelope;
* no two rooms overlap;
* every room's short side and floor area meet its minimum;
* a brief-sized room keeps its requested long/short sides;
* outdoor rooms (balcony, parking, garden) touch an external wall;
* the access graph (Milestone C) - every ``access_requirement`` connects its
  room to at least one candidate through a shared wall long enough to take a
  door. Connectivity is therefore a *solver constraint*, not a repair pass:
  a candidate that cannot be walked from the entrance is infeasible, not fixed.

The objective only aims the rooms at their target areas - missing a target
costs score but is never a rejection. A ``FEASIBLE`` model verdict is not
proof of a valid plan: the extracted geometry is re-checked against the strict
gate (:mod:`app.geometry.validation`) before a feasible outcome is returned.
Infeasibility of the model - or a gate rejection - is the only way a brief is
refused, and it is reported as ``status="infeasible"`` rather than shrinking
everyone proportionally like the legacy engine did.

Run with one worker and a fixed random seed so a given (brief, seed) always
yields the same plan - the same reproducibility contract as the legacy engine.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass

from ortools.sat.python import cp_model

from app.geometry.envelope import Envelope
from app.geometry.models import Plan, Room
from app.geometry.solver.topology import MIN_OPENING, AccessRequirement, RoomSpec
from app.geometry.units import area_to_cells, to_cells, to_ft
from app.geometry.validation import validate_plan

#: Default wall-clock budget for one solve, in seconds.
DEFAULT_TIME_LIMIT = 3.0

#: Statuses the model can end in.
FEASIBLE = "feasible"
INFEASIBLE = "infeasible"
TIMEOUT = "timeout"


@dataclass
class SolverOutcome:
    """Result of one CP-SAT solve."""

    status: str
    rooms: list[Room]
    elapsed: float
    time_limit: float
    objective: float | None = None
    reason: str | None = None
    #: True when the model found a layout but the strict geometry gate rejected
    #: it. A solver/extraction bug, never repaired - the outcome comes back
    #: ``infeasible`` so no caller can return the invalid geometry.
    validation_failed: bool = False


def _cell_bounds(spec: RoomSpec, extent: int, *, shape: bool) -> tuple[int, int]:
    """(min, max) cells for a room's width or height axis."""
    low = to_cells(spec.min_side)
    if shape and spec.sized and spec.target_short:
        low = max(low, to_cells(spec.target_short))
    low = min(low, extent)
    high = extent
    if math.isfinite(spec.max_area):
        high = min(extent, area_to_cells(spec.max_area))
    return low, max(low, high)


def _build_model(
    model: cp_model.CpModel,
    specs: list[RoomSpec],
    W: int,
    H: int,
    *,
    shape: bool,
    edge: bool,
    strict_area: bool,
) -> tuple[list[object], list[object], list[object], list[object], list[object]]:
    """Add variables and constraints; return (x,y,w,h,area) per room."""
    starts_x: list[cp_model.IntVar] = []
    starts_y: list[cp_model.IntVar] = []
    widths: list[cp_model.IntVar] = []
    heights: list[cp_model.IntVar] = []
    areas: list[cp_model.IntVar] = []
    intervals_x: list[cp_model.IntervalVar] = []
    intervals_y: list[cp_model.IntervalVar] = []

    for index, spec in enumerate(specs):
        min_w, max_w = _cell_bounds(spec, W, shape=shape)
        min_h, max_h = _cell_bounds(spec, H, shape=shape)

        w = model.NewIntVar(min_w, max_w, f"w_{index}")
        h = model.NewIntVar(min_h, max_h, f"h_{index}")
        x = model.NewIntVar(0, max(0, W - min_w), f"x_{index}")
        y = model.NewIntVar(0, max(0, H - min_h), f"y_{index}")
        starts_x.append(x)
        starts_y.append(y)
        widths.append(w)
        heights.append(h)

        floor_cells = area_to_cells(spec.min_area if strict_area else spec.min_side**2)
        cap_cells = (
            min(area_to_cells(spec.max_area), W * H)
            if math.isfinite(spec.max_area)
            else W * H
        )
        area = model.NewIntVar(min(floor_cells, cap_cells), max(1, cap_cells), f"area_{index}")
        model.AddMultiplicationEquality(area, w, h)
        areas.append(area)

        # Interval vars need affine end expressions; x + w is not affine, so
        # bind the sum to a dedicated end variable first.
        x_end = model.NewIntVar(0, W, f"xe_{index}")
        y_end = model.NewIntVar(0, H, f"ye_{index}")
        model.Add(x + w == x_end)
        model.Add(y + h == y_end)
        intervals_x.append(model.NewIntervalVar(x, w, x_end, f"ix_{index}"))
        intervals_y.append(model.NewIntervalVar(y, h, y_end, f"iy_{index}"))

        model.Add(x + w <= W)
        model.Add(y + h <= H)

        if shape and spec.sized and spec.target_long and spec.target_short:
            long_cells = to_cells(spec.target_long)
            short_cells = to_cells(spec.target_short)
            horizontal = model.NewBoolVar(f"orient_{index}")
            model.Add(w >= h).OnlyEnforceIf(horizontal)
            model.Add(h >= w).OnlyEnforceIf(horizontal.Not())
            model.Add(w >= long_cells).OnlyEnforceIf(horizontal)
            model.Add(h >= long_cells).OnlyEnforceIf(horizontal.Not())
            model.Add(h >= short_cells).OnlyEnforceIf(horizontal)
            model.Add(w >= short_cells).OnlyEnforceIf(horizontal.Not())

        if edge and spec.outdoor:
            at_left = model.NewBoolVar(f"e_l_{index}")
            at_right = model.NewBoolVar(f"e_r_{index}")
            at_bottom = model.NewBoolVar(f"e_b_{index}")
            at_top = model.NewBoolVar(f"e_t_{index}")
            model.Add(x == 0).OnlyEnforceIf(at_left)
            model.Add(x + w == W).OnlyEnforceIf(at_right)
            model.Add(y == 0).OnlyEnforceIf(at_bottom)
            model.Add(y + h == H).OnlyEnforceIf(at_top)
            model.AddBoolOr([at_left, at_right, at_bottom, at_top])

    model.AddNoOverlap2D(intervals_x, intervals_y)
    return starts_x, starts_y, widths, heights, areas


def _end_vars(
    model: cp_model.CpModel,
    starts: list[cp_model.IntVar],
    lengths: list[cp_model.IntVar],
    extent: int,
    prefix: str,
) -> list[cp_model.IntVar]:
    """Affine end variables ``starts[i] + lengths[i]``, for each room."""
    ends: list[cp_model.IntVar] = []
    for i, (start, length) in enumerate(zip(starts, lengths, strict=True)):
        end = model.NewIntVar(0, extent, f"{prefix}_{i}")
        model.Add(start + length == end)
        ends.append(end)
    return ends


def _add_access_graph(
    model: cp_model.CpModel,
    specs: list[RoomSpec],
    starts_x: list[cp_model.IntVar],
    starts_y: list[cp_model.IntVar],
    ends_x: list[cp_model.IntVar],
    ends_y: list[cp_model.IntVar],
    access_requirements: Sequence[AccessRequirement],
    min_opening: float,
) -> None:
    """Turn the intended access graph into hard geometry constraints.

    For every unordered pair that appears in the access graph this adds the
    four separation booleans (i left of j / j left of i / i below j / j below
    i). ``AddNoOverlap2D`` already prevents overlap, so the booleans only pick
    which separation the solver chose - they are consistent with it, never a
    second authority.

    Each requirement then forces its room to be adjacent (shared run at least
    ``min_opening`` long on the perpendicular axis) to at least one candidate.
    That is exactly the condition for a real door to be cut between them, so a
    feasible model is one where every access edge has a place for its door.
    """
    pairs: set[tuple[int, int]] = set()
    for req in access_requirements:
        for candidate in req.candidates:
            if candidate != req.room:
                pairs.add((min(req.room, candidate), max(req.room, candidate)))

    directions: dict[tuple[int, int], tuple[object, object, object, object]] = {}
    for i, j in sorted(pairs):
        left = model.NewBoolVar(f"adj_l_{i}_{j}")  # i is left of j
        right = model.NewBoolVar(f"adj_r_{i}_{j}")  # j is left of i
        below = model.NewBoolVar(f"adj_b_{i}_{j}")  # i is below j
        above = model.NewBoolVar(f"adj_a_{i}_{j}")  # j is below i
        model.Add(left + right + below + above >= 1)
        model.Add(ends_x[i] <= starts_x[j]).OnlyEnforceIf(left)
        model.Add(ends_x[j] <= starts_x[i]).OnlyEnforceIf(right)
        model.Add(ends_y[i] <= starts_y[j]).OnlyEnforceIf(below)
        model.Add(ends_y[j] <= starts_y[i]).OnlyEnforceIf(above)
        directions[(i, j)] = (left, right, below, above)

    overlap = to_cells(min_opening)

    def require_adjacent(u: int, v: int, gate: object | None) -> None:
        """``u`` and ``v`` share a wall whose run is at least ``overlap``.

        ``gate`` (an any-of adjacency boolean) further conditions the rule, so
        several candidates can be offered and the solver picks which one to
        connect to.
        """
        a, b = (u, v) if u < v else (v, u)
        u_left, v_left, u_below, v_below = _direction_vars(directions, a, b, u)
        guard = [gate] if gate is not None else []
        # Touching (gap exactly zero) in the chosen direction, plus a run of at
        # least ``overlap`` on the perpendicular axis: the two rooms really
        # share a wall. ``u_left`` (u to the left of v) and ``v_left`` (v to
        # the left of u) each own *their own* touch equality - enforcing both
        # per boolean would demand two opposite arrangements at once.
        model.Add(ends_x[u] == starts_x[v]).OnlyEnforceIf([*guard, u_left])
        model.Add(ends_x[v] == starts_x[u]).OnlyEnforceIf([*guard, v_left])
        for var in (u_left, v_left):
            model.Add(starts_y[u] <= ends_y[v] - overlap).OnlyEnforceIf([*guard, var])
            model.Add(starts_y[v] <= ends_y[u] - overlap).OnlyEnforceIf([*guard, var])
        model.Add(ends_y[u] == starts_y[v]).OnlyEnforceIf([*guard, u_below])
        model.Add(ends_y[v] == starts_y[u]).OnlyEnforceIf([*guard, v_below])
        for var in (u_below, v_below):
            model.Add(starts_x[u] <= ends_x[v] - overlap).OnlyEnforceIf([*guard, var])
            model.Add(starts_x[v] <= ends_x[u] - overlap).OnlyEnforceIf([*guard, var])

    for req in access_requirements:
        if not req.candidates:
            continue
        if len(req.candidates) == 1:
            require_adjacent(req.room, req.candidates[0], gate=None)
            continue
        gates: list[cp_model.IntVar] = []
        for candidate in req.candidates:
            if candidate == req.room:
                continue
            gate = model.NewBoolVar(f"acc_{req.room}_{candidate}")
            gates.append(gate)
            require_adjacent(req.room, candidate, gate=gate)
        if gates:
            model.Add(sum(gates) >= 1)


def _direction_vars(
    directions: dict[tuple[int, int], tuple[object, object, object, object]],
    a: int,
    b: int,
    u: int,
) -> tuple[object, object, object, object]:
    """The four separation booleans, in the coordinate frame of ``u`` vs ``v``.

    ``directions`` stores (i, j) with i < j and booleans meaning "i left of j",
    "j left of i", "i below j", "j below i". The caller passes ``u`` (which may
    be the larger index) and ``v`` (the other) so the frame is (u, v).
    """
    left, right, below, above = directions[(a, b)]
    if u == a:
        return left, right, below, above
    return right, left, above, below


def _add_objective(
    model: cp_model.CpModel,
    specs: list[RoomSpec],
    areas: list[cp_model.IntVar],
    W: int,
    H: int,
) -> None:
    terms: list[object] = []
    for index, spec in enumerate(specs):
        target = area_to_cells(spec.target_area)
        deviation = model.NewIntVar(0, W * H, f"dev_{index}")
        model.AddAbsEquality(deviation, areas[index] - target)
        # Rooms the brief sized weigh double: hitting their number matters more
        # than a generic room landing exactly on its default.
        terms.append((2 if spec.sized else 1) * deviation)
    model.Minimize(sum(terms))


def _preflight(specs: list[RoomSpec], envelope: Envelope) -> str | None:
    """Static checks the model could only express as trivial infeasibility."""
    W, H = envelope.cells
    if not envelope.within_max_extent:
        return f"the buildable area {envelope.describe()} exceeds the solver's 100 ft grid"
    if W <= 0 or H <= 0:
        return f"the buildable area {envelope.describe()} has no interior to pack"
    for spec in specs:
        side_cells = to_cells(spec.min_side)
        if side_cells > W or side_cells > H:
            return (
                f"{spec.name} needs {spec.min_side:g} ft on its short side, "
                f"more than the buildable width ({envelope.buildable_width:g} ft) "
                f"or length ({envelope.buildable_length:g} ft)"
            )
        if area_to_cells(spec.min_area) > envelope.area_cells:
            return f"{spec.name}'s minimum area ({spec.min_area:g} sq ft) exceeds the envelope"
    return None


def solve(
    specs: list[RoomSpec],
    envelope: Envelope,
    *,
    seed: int = 1,
    time_limit: float | None = None,
    shape: bool = True,
    edge: bool = True,
    strict_area: bool = True,
    validate: bool = True,
    access_requirements: Sequence[AccessRequirement] = (),
) -> SolverOutcome:
    """Pack ``specs`` into ``envelope``, returning a :class:`SolverOutcome`.

    ``shape``/``edge``/``strict_area`` relax the hard constraints and are only
    used by the infeasibility ladder in :mod:`app.geometry.solver.infeasibility`.

    ``access_requirements`` are the intended access graph (Milestone C): each
    requirement forces a shared wall long enough for a door, so a feasible
    outcome is walkable from the entrance. The infeasibility ladder passes none
    - its probes isolate geometric feasibility from connectivity.

    ``validate`` runs the strict geometry gate on the extracted layout before a
    ``feasible`` outcome is returned. A CP-SAT ``FEASIBLE`` verdict alone is
    never proof of a valid plan - the gate is the authority. It stays on for
    production solves and off for the ladder's relaxed probes, whose whole
    point is geometry that cannot meet the brief.
    """
    budget = time_limit or DEFAULT_TIME_LIMIT
    started = time.perf_counter()

    reason = _preflight(specs, envelope)
    if reason is not None:
        return SolverOutcome(INFEASIBLE, [], time.perf_counter() - started, budget, reason=reason)

    W, H = envelope.cells
    model = cp_model.CpModel()
    starts_x, starts_y, widths, heights, areas = _build_model(
        model, specs, W, H, shape=shape, edge=edge, strict_area=strict_area
    )
    if access_requirements:
        # Bind the sums to affine end vars the way _build_model does, so the
        # OnlyEnforceIf adjacency constraints operate on model variables.
        ends_x = _end_vars(model, starts_x, widths, W, "xe")
        ends_y = _end_vars(model, starts_y, heights, H, "ye")
        _add_access_graph(
            model,
            specs,
            starts_x,
            starts_y,
            ends_x,
            ends_y,
            access_requirements,
            MIN_OPENING,
        )
    _add_objective(model, specs, areas, W, H)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = budget
    solver.parameters.random_seed = seed
    solver.parameters.num_search_workers = 1
    solver.parameters.log_search_progress = False

    result = solver.Solve(model)
    elapsed = time.perf_counter() - started

    if result == cp_model.INFEASIBLE:
        return SolverOutcome(INFEASIBLE, [], elapsed, budget)
    if result not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        return SolverOutcome(TIMEOUT, [], elapsed, budget, reason="no solution found in time")

    rooms = [
        Room(
            spec.type,
            spec.name,
            to_ft(int(solver.Value(starts_x[i]))),
            to_ft(int(solver.Value(starts_y[i]))),
            to_ft(int(solver.Value(widths[i]))),
            to_ft(int(solver.Value(heights[i]))),
        )
        for i, spec in enumerate(specs)
    ]

    if validate:
        plan = Plan(
            rooms=rooms,
            plot_width=envelope.buildable_width,
            plot_length=envelope.buildable_length,
        )
        report = validate_plan(plan, envelope, specs)
        if not report.ok:
            return SolverOutcome(
                INFEASIBLE,
                [],
                elapsed,
                budget,
                reason="; ".join(report.errors[:4]),
                validation_failed=True,
            )

    return SolverOutcome(
        FEASIBLE,
        rooms,
        elapsed,
        budget,
        objective=float(solver.ObjectiveValue()),
    )
