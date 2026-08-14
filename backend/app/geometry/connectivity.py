"""Adjacency and walkability graphs over a solved plan (networkx).

Milestone B's first pass: turn the rooms' shared-wall geometry into a graph the
doors pass can walk. Nodes are indices into ``plan.rooms``; an edge means the
two rooms share a wall long enough to cut a doorway through. ``walkable_graph``
then models how people actually move - the door graph when doors have been
modeled, else bare adjacency - and :func:`stranded_indices` reports the rooms
you cannot reach from the living room, which is what the connectivity score and
the validation gate consume.
"""

from __future__ import annotations

from collections.abc import Sequence

import networkx as nx

from app.geometry.models import Door, Plan, Room
from app.geometry.primitives import WALL_TOLERANCE
from app.schemas.enums import RoomType

#: Rooms you may walk through to reach the rest of the house.
CIRCULATION_TYPES: frozenset[RoomType] = frozenset(
    {RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.FOYER, RoomType.PASSAGE}
)

#: Feet of shared wall below which a doorway is not worth cutting.
MIN_OPENING = 2.5

#: Tolerance for "this opening sits on that wall" checks, in feet.
WALL_TOL = WALL_TOLERANCE


def adjacency_graph(plan: Plan, *, min_opening: float = MIN_OPENING) -> nx.Graph:
    """Rooms as nodes, one edge per shared wall long enough to take a door.

    Edge attribute ``run`` is the shared wall length in feet. Outdoor-outdoor
    pairs are left out - a balcony does not give access to the parking.
    """
    graph = nx.Graph()
    graph.add_nodes_from(range(len(plan.rooms)))
    for i in range(len(plan.rooms)):
        for j in range(i + 1, len(plan.rooms)):
            if plan.rooms[i].type.is_outdoor and plan.rooms[j].type.is_outdoor:
                continue
            run = plan.rooms[i].shared_wall_length(plan.rooms[j])
            if run >= min_opening:
                graph.add_edge(i, j, run=run)
    return graph


def door_rooms(door: Door, rooms: Sequence[Room]) -> tuple[int, int] | None:
    """Indices of the two rooms whose shared wall ``door`` sits on.

    Doors are identified by geometry rather than by the ``room_from``/``room_to``
    types they carry (two rooms of the same type make the types ambiguous); a
    door that sits on no room's shared wall returns ``None``.
    """
    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            wall = rooms[i].shared_wall(rooms[j])
            if wall is None:
                continue
            orientation, lo, hi, line = wall
            if orientation != door.orientation:
                continue
            if orientation == "vertical":
                if abs(door.x - line) > WALL_TOL:
                    continue
                d_lo, d_hi = door.y, door.y + door.width
            else:
                if abs(door.y - line) > WALL_TOL:
                    continue
                d_lo, d_hi = door.x, door.x + door.width
            if d_lo >= lo - WALL_TOL and d_hi <= hi + WALL_TOL:
                return i, j
    return None


def walkable_graph(plan: Plan, *, min_opening: float = MIN_OPENING) -> nx.Graph:
    """How people move: the door graph when doors are modeled, else adjacency.

    The solver only enforces shared-wall adjacency, not that a door exists on
    every shared wall, so before Milestone B models doors the graph falls back
    to the full adjacency graph (the "every wall is open" assumption the scoring
    already made).
    """
    if not plan.doors:
        return adjacency_graph(plan, min_opening=min_opening)

    graph = nx.Graph()
    graph.add_nodes_from(range(len(plan.rooms)))
    for door in plan.doors:
        pair = door_rooms(door, plan.rooms)
        if pair is not None:
            graph.add_edge(*pair)
    return graph


def stranded_indices(plan: Plan) -> list[int]:
    """Indices of indoor rooms you cannot walk to from the living room.

    The walk starts at the living room (or the first circulation space, or room
    0) and only *continues* through circulation and bedrooms - plus the one step
    from a bedroom into its own en-suite - so a kitchen reached by crossing a
    bedroom does not count as connected. Mirrors the legacy validator's rule on
    top of the modeled door graph.
    """
    indoor = [i for i, room in enumerate(plan.rooms) if not room.type.is_outdoor]
    if not indoor:
        return []

    start = next(
        (i for i in indoor if plan.rooms[i].type is RoomType.LIVING_ROOM),
        next((i for i in indoor if plan.rooms[i].type in CIRCULATION_TYPES), indoor[0]),
    )

    graph = walkable_graph(plan).subgraph(indoor)
    reachable = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for neighbour in graph.neighbors(current):
            if neighbour in reachable:
                continue
            # An en-suite is entered from its own bedroom and nowhere else, so
            # that one step through a bedroom is the whole point rather than a
            # fault. Every other room has to be reached from circulation.
            current_room = plan.rooms[current]
            next_room = plan.rooms[neighbour]
            if current_room.type.is_bedroom and next_room.type is not RoomType.ATTACHED_BATHROOM:
                continue
            reachable.add(neighbour)
            if next_room.type in CIRCULATION_TYPES or next_room.type.is_bedroom:
                frontier.append(neighbour)

    return [i for i in indoor if i not in reachable]


def reachable_fraction(plan: Plan) -> float:
    """Fraction of indoor rooms reachable from the living room, 1.0 when none."""
    indoor = [i for i, room in enumerate(plan.rooms) if not room.type.is_outdoor]
    if not indoor:
        return 1.0
    return 1.0 - len(stranded_indices(plan)) / len(indoor)
