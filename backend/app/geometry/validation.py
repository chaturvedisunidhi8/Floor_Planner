"""Strict post-solve gate: prove a solved plan actually meets the brief.

The solver's hard constraints are enforced by the model itself; this pass
re-checks them on the *extracted* geometry in case of a solver/extraction bug,
and guards the solver output the way the legacy validator guards the legacy
pipeline. A plan that fails this gate is treated as infeasible, not repaired.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.geometry.connectivity import door_rooms, stranded_indices
from app.geometry.envelope import Envelope
from app.geometry.models import Plan, Room, Window
from app.geometry.solver.topology import RoomSpec
from app.geometry.units import to_cells
from app.geometry.walls import validate_walls
from app.schemas.enums import RoomType

#: Tolerances (feet) - the solver output is grid-exact, so these only absorb
#: floating point round-trips.
_BOUND_TOL = 0.1
_OVERLAP_TOL = 0.25
_SIZE_TOL = 0.1
_OPENING_TOL = 0.1

#: Rooms whose openings are drawn as hatches/swings rather than windows.
_NO_WINDOW_TYPES: frozenset[RoomType] = frozenset({RoomType.PARKING, RoomType.GARDEN})


@dataclass
class ValidationReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def messages(self) -> list[str]:
        return [*self.errors, *self.warnings]


def validate_plan(plan: Plan, envelope: Envelope, specs: list[RoomSpec]) -> ValidationReport:
    report = ValidationReport()

    # The buildable box, not the raw plot, is the authority.
    box_w = envelope.buildable_width
    box_h = envelope.buildable_length

    if len(plan.rooms) != len(specs):
        report.ok = False
        report.errors.append(
            f"room count mismatch: solver returned {len(plan.rooms)}, brief wants {len(specs)}"
        )
        return report

    # Per-spec lookup by room type (multiple same-type rooms share the floors).
    floors = {spec.type: spec for spec in specs}

    for room in plan.rooms:
        if room.x < -_BOUND_TOL or room.y < -_BOUND_TOL:
            report.ok = False
            report.errors.append(f"{room.name} starts outside the envelope")
        if room.x2 > box_w + _BOUND_TOL or room.y2 > box_h + _BOUND_TOL:
            report.ok = False
            report.errors.append(f"{room.name} overflows the envelope")

        spec = floors.get(room.type)
        if spec is None:
            report.ok = False
            report.errors.append(f"no spec for {room.name}")
            continue

        if room.short_side < spec.min_side - _SIZE_TOL:
            report.ok = False
            report.errors.append(
                f"{room.name} short side {room.short_side:g} ft is below "
                f"the {spec.min_side:g} ft minimum"
            )
        if room.area < spec.min_area - _SIZE_TOL:
            report.ok = False
            report.errors.append(
                f"{room.name} area {room.area:g} sq ft is below the "
                f"{spec.min_area:g} sq ft minimum"
            )

        if spec.outdoor and not room.touches_edge(box_w, box_h):
            report.ok = False
            report.errors.append(f"{room.name} does not touch an external wall")

        if spec.sized and spec.target_long and spec.target_short:
            if room.long_side < spec.target_long - 0.5:
                report.ok = False
                report.errors.append(
                    f"{room.name} long side {room.long_side:g} ft is below the "
                    f"requested {spec.target_long:g} ft"
                )
            if room.short_side < spec.target_short - 0.5:
                report.ok = False
                report.errors.append(
                    f"{room.name} short side {room.short_side:g} ft is below the "
                    f"requested {spec.target_short:g} ft"
                )

    # Overlaps.
    for i in range(len(plan.rooms)):
        for j in range(i + 1, len(plan.rooms)):
            a, b = plan.rooms[i], plan.rooms[j]
            dx = min(a.x2, b.x2) - max(a.x, b.x)
            dy = min(a.y2, b.y2) - max(a.y, b.y)
            if dx > _OVERLAP_TOL and dy > _OVERLAP_TOL:
                report.ok = False
                report.errors.append(f"{a.name} overlaps {b.name}")

    # Grid alignment (the solver is exact; a drift here is a unit bug).
    for room in plan.rooms:
        for value in (room.x, room.y, room.width, room.height):
            if abs(value - to_cells(value) * 0.5) > 1e-6:
                report.warnings.append(f"{room.name} is not grid-aligned")
                break

    # Milestone B: the modeled doors and windows, when the plan has them.
    _validate_openings(plan, report)

    # Milestone E/F: the modeled walls, when the plan has them.
    if plan.walls is not None:
        for problem in validate_walls(plan.walls):
            report.ok = False
            report.errors.append(problem)

    return report


def _validate_openings(plan: Plan, report: ValidationReport) -> None:
    """Strict gate for the modeled doors and windows.

    A door must sit on the shared wall of two rooms and stay inside its run; a
    window must sit on the room's external wall; nothing may overlap another
    opening. These are modelling bugs when they fire - the geometry itself is
    already validated. Since Milestone C the solver enforces the access graph,
    so any room left unreachable through the modeled doors is a hard fault too
    - the plan is refused, never repaired.
    """
    if not plan.doors and not plan.windows:
        return

    for index, door in enumerate(plan.doors):
        if door_rooms(door, plan.rooms) is None:
            report.ok = False
            report.errors.append(
                f"door {index + 1} ({door.room_from.value} -> {door.room_to.value}) "
                "does not sit on a shared wall"
            )

    for index, window in enumerate(plan.windows):
        if not _on_external_wall(window, plan.rooms, plan.plot_width, plan.plot_length):
            report.ok = False
            report.errors.append(
                f"window {index + 1} ({window.room.value}) does not sit on an external wall"
            )

    # Nothing may overlap another opening on the same wall.
    for i, first in enumerate(plan.doors):
        for second in plan.doors[i + 1 :]:
            if _openings_overlap(first.x, first.y, first.width, first.orientation,
                                 second.x, second.y, second.width, second.orientation):
                report.ok = False
                report.errors.append(
                    f"door {i + 1} overlaps door {plan.doors.index(second) + 1}"
                )
    for window in plan.windows:
        for index, door in enumerate(plan.doors):
            if _openings_overlap(window.x, window.y, window.width, window.orientation,
                                 door.x, door.y, door.width, door.orientation):
                report.ok = False
                report.errors.append(
                    f"window ({window.room.value}) overlaps door {index + 1}"
                )

    # A habitable room on the outside that is long enough for a window should
    # have one modeled. Missing coverage is a quality nit rather than a hard
    # fault, so it stays a warning.
    external_habitable = [
        room
        for room in plan.rooms
        if room.type not in _NO_WINDOW_TYPES
        and room.touches_edge(plan.plot_width, plan.plot_length)
    ]
    for room in external_habitable:
        has_window = any(
            w.room == room.type and _window_on_room(w, room) for w in plan.windows
        )
        if not has_window:
            report.warnings.append(f"{room.name} has no window on its external wall")

    stranded = stranded_indices(plan)
    if stranded:
        names = ", ".join(plan.rooms[i].name for i in stranded[:3])
        report.ok = False
        report.errors.append(
            f"{len(stranded)} room(s) unreachable through the modeled doors: {names}"
        )


def _on_external_wall(
    window: Window, rooms: list[Room], plot_width: float, plot_length: float
) -> bool:
    """True when ``window`` sits on its room's external wall, inside its run."""
    for room in rooms:
        if room.type is not window.room:
            continue
        if not _window_on_room(window, room):
            continue
        if abs(window.x - room.x) <= _OPENING_TOL and window.orientation == "vertical":
            return room.x <= _OPENING_TOL
        if abs(window.x - room.x2) <= _OPENING_TOL and window.orientation == "vertical":
            return plot_width - room.x2 <= _OPENING_TOL
        if abs(window.y - room.y) <= _OPENING_TOL and window.orientation == "horizontal":
            return room.y <= _OPENING_TOL
        if abs(window.y - room.y2) <= _OPENING_TOL and window.orientation == "horizontal":
            return plot_length - room.y2 <= _OPENING_TOL
    return False


def _window_on_room(window: Window, room: Room) -> bool:
    """True when the window's segment stays within the room's wall run."""
    if window.orientation == "vertical":
        lo, hi = room.y, room.y2
        return window.y >= lo - _OPENING_TOL and window.y + window.width <= hi + _OPENING_TOL
    lo, hi = room.x, room.x2
    return window.x >= lo - _OPENING_TOL and window.x + window.width <= hi + _OPENING_TOL


def _openings_overlap(
    x1: float, y1: float, w1: float, o1: str, x2: float, y2: float, w2: float, o2: str
) -> bool:
    """True when two collinear openings on the same wall intersect."""
    if o1 != o2:
        return False
    if o1 == "vertical":
        if abs(x1 - x2) > _OPENING_TOL:
            return False
        return y1 < y2 + w2 - _OPENING_TOL and y2 < y1 + w1 - _OPENING_TOL
    if abs(y1 - y2) > _OPENING_TOL:
        return False
    return x1 < x2 + w2 - _OPENING_TOL and x2 < x1 + w1 - _OPENING_TOL
