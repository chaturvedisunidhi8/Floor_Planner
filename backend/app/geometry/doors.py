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

from app.geometry.models import Door, Plan
from app.geometry.solver.topology import AccessRequirement
from app.geometry.walls import WALLS

#: Feet of shared wall below which a doorway is not worth cutting.
#: Owned by :data:`app.geometry.walls.WALLS`.
MIN_OPENING = WALLS.min_opening

#: Standard door leaf, in feet. Clamped down when the shared run is shorter.
#: Owned by :data:`app.geometry.walls.WALLS`.
DOOR_WIDTH = WALLS.door_width


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
    doors: list[Door] = []
    for requirement in access_requirements:
        for candidate in requirement.candidates:
            if candidate == requirement.room or not (0 <= candidate < len(plan.rooms)):
                continue
            door = _door_between(plan, requirement.room, candidate, min_opening)
            if door is not None:
                doors.append(door)
                break
    return doors


def _doors_on_every_shared_wall(plan: Plan, *, min_opening: float) -> list[Door]:
    """One door centred on every shared wall long enough to take one."""
    doors: list[Door] = []
    for i in range(len(plan.rooms)):
        for j in range(i + 1, len(plan.rooms)):
            if plan.rooms[i].type.is_outdoor and plan.rooms[j].type.is_outdoor:
                continue
            door = _door_between(plan, i, j, min_opening)
            if door is not None:
                doors.append(door)
    return doors


def _door_between(plan: Plan, i: int, j: int, min_opening: float) -> Door | None:
    """A centred door on the shared wall of rooms ``i`` and ``j``, or ``None``.

    Only rooms that actually share a wall with a run of at least
    ``min_opening`` get a door; the width clamps down to the run when the run is
    shorter than a standard leaf.
    """
    wall = plan.rooms[i].shared_wall(plan.rooms[j])
    if wall is None:
        return None
    orientation, lo, hi, line = wall
    run = hi - lo
    if run < min_opening:
        return None

    width = min(DOOR_WIDTH, run)
    mid = (lo + hi) / 2
    if orientation == "vertical":
        return Door(
            room_from=plan.rooms[i].type,
            room_to=plan.rooms[j].type,
            x=line,
            y=mid - width / 2,
            width=width,
            orientation="vertical",
        )
    return Door(
        room_from=plan.rooms[i].type,
        room_to=plan.rooms[j].type,
        x=mid - width / 2,
        y=line,
        width=width,
        orientation="horizontal",
    )
