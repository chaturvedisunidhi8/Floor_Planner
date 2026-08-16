"""Model the doorways on the access graph.

Without an access model this pass cuts one door centred on every shared wall
long enough to take one, so walkability over the modeled door graph is
identical to shared-wall adjacency.

With an access model (Milestone D) it cuts a door only on the *intended* edges
- one door per :class:`AccessRequirement`, on the shared wall the solver
actually produced for that requirement's chosen candidate. The modeled door
graph then equals the access graph exactly: no spurious door between two
bedrooms or between a bathroom and the dining room that the packing merely
happened to place side by side, and every edge the access model promised (which
the solver enforces) is really walked through a door.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from app.geometry.models import Door, Plan
from app.geometry.primitives import clear_of_corners
from app.geometry.solver.topology import AccessRequirement
from app.geometry.walls import WALLS

#: Feet of shared wall below which a doorway is not worth cutting.
#: Owned by :data:`app.geometry.walls.WALLS`.
MIN_OPENING = WALLS.min_opening

#: Standard door leaf, in feet. Clamped down when the shared run is shorter.
#: Owned by :data:`app.geometry.walls.WALLS`.
DOOR_WIDTH = WALLS.door_width

#: A door is cut as ``(orientation, lo, hi, line)`` for its wall, so two doors
#: that share a wall can be told apart from doors on parallel neighbours.
Wall = tuple[str, float, float, float]


def model_doors(
    plan: Plan,
    access_requirements: Sequence[AccessRequirement] | None = None,
    *,
    min_opening: float = MIN_OPENING,
) -> list[Door]:
    """Doors for ``plan``.

    ``access_requirements`` is the programme's intended access graph: one door
    is cut per requirement, centred on the wall the room actually shares with
    its first satisfying candidate. ``None`` keeps the legacy behaviour of one
    door on every shared wall (used by the door-unit tests).
    """
    if access_requirements is None:
        return _doors_on_every_shared_wall(plan, min_opening=min_opening)
    return _doors_on_access_edges(plan, access_requirements, min_opening=min_opening)


def _doors_on_access_edges(
    plan: Plan,
    access_requirements: Sequence[AccessRequirement],
    *,
    min_opening: float,
) -> list[Door]:
    """One door per access edge, on the shared wall the solver produced.

    A requirement whose room ends up sharing a wall with several candidates
    still gets exactly one door - the first candidate in its preference order
    with a usable shared run. A requirement with no usable wall has nothing to
    model; the strict gate then rejects the plan, so this never silently drops
    an edge that was promised.
    """
    placed: list[tuple[Door, Wall]] = []
    for requirement in access_requirements:
        for candidate in requirement.candidates:
            if candidate == requirement.room or not (0 <= candidate < len(plan.rooms)):
                continue
            result = _door_between(plan, requirement.room, candidate, min_opening)
            if result is not None:
                placed.append(result)
                break
    return _reposition_doors(placed)


def _doors_on_every_shared_wall(plan: Plan, *, min_opening: float) -> list[Door]:
    """One door on every shared wall long enough to take one."""
    placed: list[tuple[Door, Wall]] = []
    for i in range(len(plan.rooms)):
        for j in range(i + 1, len(plan.rooms)):
            if plan.rooms[i].type.is_outdoor and plan.rooms[j].type.is_outdoor:
                continue
            result = _door_between(plan, i, j, min_opening)
            if result is not None:
                placed.append(result)
    return _reposition_doors(placed)


def _door_between(
    plan: Plan, i: int, j: int, min_opening: float
) -> tuple[Door, Wall] | None:
    """A door on the shared wall of rooms ``i`` and ``j``, or ``None``.

    Only rooms that actually share a wall with a run of at least
    ``min_opening`` get a door; the width clamps down to the run when the run is
    shorter than a standard leaf. The wall is returned alongside the door so the
    spacing pass can group doors that sit on the same run.
    """
    wall = plan.rooms[i].shared_wall(plan.rooms[j])
    if wall is None:
        return None
    orientation, lo, hi, line = wall
    run = hi - lo
    if run < min_opening:
        return None

    width = min(DOOR_WIDTH, run)
    start = clear_of_corners(lo, hi, width, WALLS.door_corner_clearance)
    if orientation == "vertical":
        door = Door(
            room_from=plan.rooms[i].type,
            room_to=plan.rooms[j].type,
            x=line,
            y=start,
            width=width,
            orientation="vertical",
        )
    else:
        door = Door(
            room_from=plan.rooms[i].type,
            room_to=plan.rooms[j].type,
            x=start,
            y=line,
            width=width,
            orientation="horizontal",
        )
    return door, wall


def _reposition_doors(placed: Sequence[tuple[Door, Wall]]) -> list[Door]:
    """Keep ``door_spacing`` between doors on the same wall.

    Doors are grouped by the exact wall run they sit on and re-placed as one
    row, centred on the run: gaps of ``door_spacing`` with corner clearance
    where the wall allows it, plain ``door_spacing`` without clearance if the
    wall is too short, flush against each other if even that is too tight, and
    left exactly where they were when the wall cannot hold them in bounds at
    all. The ladder only improves on the original centred doors - it never
    leaves the plan with a new overlap or a door off its wall.
    """
    doors = [door for door, _wall in placed]
    groups: dict[Wall, list[int]] = {}
    for index, (_door, wall) in enumerate(placed):
        groups.setdefault(wall, []).append(index)

    for wall, indices in groups.items():
        orientation, lo, hi, _line = wall
        if len(indices) < 2:
            continue
        ordered = sorted(indices, key=lambda i: _start(doors[i], orientation))
        widths = [doors[i].width for i in ordered]
        total = sum(widths)
        count = len(ordered)
        for spacing, clearance in (
            (WALLS.door_spacing, WALLS.door_corner_clearance),
            (WALLS.door_spacing, 0.0),
            (0.0, 0.0),
        ):
            lo_start = lo + clearance
            hi_start = hi - clearance - total - spacing * (count - 1)
            if hi_start < lo_start:
                continue
            mid = (lo + hi) / 2
            start = mid - (total + spacing * (count - 1)) / 2
            start = min(max(start, lo_start), hi_start)
            for index, width in zip(ordered, widths, strict=True):
                doors[index] = _set_start(doors[index], start, orientation)
                start += width + spacing
            break
    return doors


def _start(door: Door, orientation: str) -> float:
    """Coordinate along the wall the door runs on."""
    return door.y if orientation == "vertical" else door.x


def _set_start(door: Door, start: float, orientation: str) -> Door:
    """A copy of ``door`` moved to ``start`` along its wall."""
    if orientation == "vertical":
        return replace(door, y=start)
    return replace(door, x=start)
