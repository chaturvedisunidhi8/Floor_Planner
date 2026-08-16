"""Measurable architectural features of a solved plan.

The solver proves *geometry* - rooms packed with no overlap, every access edge
walkable. This module measures *architecture*: whether the packing reads as a
house an architect would sign, on the axes the milestone cares about.

* corridor  - the passage/foyer band: how many fragments, how wide, how
  regular, whether it forms one spine instead of leftover strips;
* doors     - corner clearance, spacing on a wall, and doors that face each
  other across a narrow corridor;
* wet zone  - bathroom shape, bathrooms kept off the social rooms, bathrooms
  pulled towards the bedrooms;
* zones     - the bedrooms clustered into a private zone entered from
  circulation, not off the living room;
* daylight  - habitable rooms with two external walls, windows clear of
  corners and of doors;
* space     - the plot area no room claims (``uncovered_fraction``) and
  balconies that actually serve a habitable room.

Everything here is a pure, deterministic function of ``plan``. It never
mutates the plan and never invents constraints - the values only feed the
scorer and the adaptive search, so nothing a measurement says can make a brief
infeasible.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from statistics import pstdev

from app.geometry.connectivity import CIRCULATION_TYPES, door_rooms, walkable_graph
from app.geometry.models import Door, Plan, Room, Window
from app.geometry.walls import WALLS
from app.schemas.enums import RoomType

#: Rooms that want daylight and therefore an external wall.
HABITABLE: frozenset[RoomType] = frozenset(
    {
        RoomType.LIVING_ROOM,
        RoomType.DINING_ROOM,
        RoomType.KITCHEN,
        RoomType.MASTER_BEDROOM,
        RoomType.GUEST_BEDROOM,
        RoomType.CHILDREN_BEDROOM,
        RoomType.BEDROOM,
        RoomType.STUDY_ROOM,
    }
)

#: The social / shared zone.
SOCIAL: frozenset[RoomType] = frozenset(
    {RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN}
)

#: Corridor rooms (the passage/foyer band). Mirrors the scorer's circulation set.
_CORRIDOR_TYPES: frozenset[RoomType] = frozenset({RoomType.PASSAGE, RoomType.FOYER})

#: Rooms a balcony must serve to be useful rather than a leftover ribbon.
_BALCONY_SERVED: frozenset[RoomType] = frozenset(
    {RoomType.LIVING_ROOM, RoomType.MASTER_BEDROOM}
)

# --- design rules, in feet -------------------------------------------------

#: Feet a door must stay away from the end of its wall run.
DOOR_CORNER_CLEARANCE = WALLS.door_corner_clearance
#: Minimum gap between two doors on the same wall.
DOOR_SPACING = WALLS.door_spacing
#: Corridors narrower than this are not usable.
CORRIDOR_MIN_WIDTH = 4.5
#: Doors on two parallel walls closer than this, facing each other, conflict.
OPPOSING_CORRIDOR_WIDTH = 5.0
#: Bathrooms with a short side below this read as slots, not wet rooms.
BATHROOM_MIN_SIDE = 4.5
#: Shared-wall run that counts as "the two rooms actually connect".
CONNECT_RUN = WALLS.min_opening
#: Shared-wall run that counts as a room "serving" another.
SERVE_RUN = 1.0
#: Window must stay this far from a corner jamb.
WINDOW_CORNER_CLEARANCE = WALLS.window_corner_clearance
#: Window and door on the same wall must stay this far apart.
WINDOW_DOOR_SPACING = WALLS.window_door_spacing

#: Wall line tolerance for "these openings are on the same wall", in feet.
_LINE_TOL = WALLS.wall_tolerance


@dataclass(frozen=True)
class QualityMetrics:
    """The measured features of one plan, as plain, explainable numbers.

    Every field is either a count, a fraction in ``[0, 1]`` or ``None`` when
    the plan has no room of the relevant kind (so the scorer can drop that
    axis and renormalise, exactly as the existing component scorer does).
    """

    # --- corridor ----------------------------------------------------------
    corridor_rooms: int
    corridor_band_count: int
    corridor_fragmentation: float | None
    corridor_min_width: float | None
    corridor_width_std: float | None
    corridor_spine_ratio: float | None
    # --- doors ------------------------------------------------------------
    door_count: int
    door_corner_violations: int
    door_spacing_violations: int
    opposing_door_pairs: int
    # --- wet zone ---------------------------------------------------------
    bathroom_count: int
    slender_bathrooms: int
    bathroom_social_walls: int
    attached_bath_bedroom_share: float | None
    common_bath_bedroom_share: float | None
    # --- zones ------------------------------------------------------------
    bedroom_social_walls: int
    bedroom_bedroom_walls: int
    private_zone_share: float | None
    bedroom_from_circulation: float | None
    # --- daylight ---------------------------------------------------------
    cross_ventilated: int
    window_corner_violations: int
    window_door_violations: int
    # --- space ------------------------------------------------------------
    uncovered_fraction: float
    balcony_without_habitable: int


def quality_metrics(plan: Plan) -> QualityMetrics:
    """Measure ``plan`` on every architectural axis, deterministically.

    Accepts the engine's internal :class:`~app.geometry.models.Plan` (rooms are
    :class:`~app.geometry.models.Room`) or a public layout whose rooms are
    ``Rect`` - the two differ only in the shared-wall helpers this module needs,
    so ``Rect`` rooms are lifted to ``Room`` before measuring.
    """
    plan = _normalize(plan)
    rooms = plan.rooms
    corridor = [r for r in rooms if r.type in _CORRIDOR_TYPES]
    bathrooms = [r for r in rooms if r.type.is_bathroom]
    bedrooms = [r for r in rooms if r.type.is_bedroom]
    habitable = [r for r in rooms if r.type in HABITABLE]
    balconies = [r for r in rooms if r.type is RoomType.BALCONY]
    social = [r for r in rooms if r.type in SOCIAL]

    return QualityMetrics(
        corridor_rooms=len(corridor),
        corridor_band_count=_connected_bands(corridor),
        corridor_fragmentation=_fragmentation(corridor),
        corridor_min_width=min((r.short_side for r in corridor), default=None),
        corridor_width_std=_width_std(corridor),
        corridor_spine_ratio=_spine_ratio(corridor),
        door_count=len(plan.doors),
        door_corner_violations=_door_corner_violations(plan),
        door_spacing_violations=_door_spacing_violations(plan),
        opposing_door_pairs=_opposing_door_pairs(plan),
        bathroom_count=len(bathrooms),
        slender_bathrooms=sum(r.short_side < BATHROOM_MIN_SIDE for r in bathrooms),
        bathroom_social_walls=sum(
            _runs(a, b) for a in bathrooms for b in social
        ),
        attached_bath_bedroom_share=_bedroom_share(
            [r for r in bathrooms if r.type is RoomType.ATTACHED_BATHROOM], bedrooms
        ),
        common_bath_bedroom_share=_bedroom_share(
            [r for r in bathrooms if r.type is RoomType.COMMON_BATHROOM], bedrooms
        ),
        bedroom_social_walls=sum(_runs(a, b) for a in bedrooms for b in social),
        bedroom_bedroom_walls=sum(
            _runs(a, b)
            for i, a in enumerate(bedrooms)
            for b in bedrooms[i + 1 :]
        ),
        private_zone_share=_largest_cluster_share(bedrooms),
        bedroom_from_circulation=_bedroom_from_circulation(plan, bedrooms),
        cross_ventilated=sum(external_edges(r, plan) >= 2 for r in habitable),
        window_corner_violations=_window_corner_violations(plan),
        window_door_violations=_window_door_violations(plan),
        uncovered_fraction=_uncovered_fraction(plan),
        balcony_without_habitable=sum(
            not any(_runs(b, h, SERVE_RUN) for h in rooms if h.type in _BALCONY_SERVED)
            for b in balconies
        ),
    )


# --- corridor ----------------------------------------------------------------

def _as_room(rect) -> Room:
    """Lift a ``Rect`` to a :class:`Room` (no-op when it already is one)."""
    if hasattr(rect, "shared_wall"):
        return rect
    return Room(rect.type, rect.name, rect.x, rect.y, rect.width, rect.height)


def _normalize(plan: Plan) -> Plan:
    """A plan whose rooms expose the shared-wall helpers this module needs."""
    rooms = [_as_room(r) for r in plan.rooms]
    if all(r is p for r, p in zip(rooms, plan.rooms, strict=True)):
        return plan
    normalized = Plan(rooms=rooms, plot_width=plan.plot_width, plot_length=plan.plot_length)
    normalized.doors = plan.doors
    normalized.windows = plan.windows
    normalized.walls = plan.walls
    normalized.status = plan.status
    normalized.quality_score = plan.quality_score
    return normalized


def _connected_bands(rooms: list[Room]) -> int:
    """Number of connected corridor components (a fragmented band is several)."""
    n = len(rooms)
    seen: list[bool] = [False] * n
    bands = 0
    for start in range(n):
        if seen[start]:
            continue
        bands += 1
        stack = [start]
        seen[start] = True
        while stack:
            i = stack.pop()
            for j in range(n):
                if not seen[j] and rooms[i].shared_wall_length(rooms[j]) >= CONNECT_RUN:
                    seen[j] = True
                    stack.append(j)
    return bands


def _fragmentation(rooms: list[Room]) -> float | None:
    """0 = one connected corridor band, 1 = every corridor room isolated."""
    if len(rooms) < 2:
        return None
    return (_connected_bands(rooms) - 1) / (len(rooms) - 1)


def _width_std(rooms: list[Room]) -> float | None:
    """Population std-dev of the corridor widths; ``None`` with < 2 rooms."""
    if len(rooms) < 2:
        return None
    return round(pstdev(r.short_side for r in rooms), 2)


def _same_band(a: Room, b: Room, tolerance: float = _LINE_TOL) -> bool:
    """Two corridor rooms sharing one x band or one y band sit on one spine."""
    return (
        abs(a.y - b.y) <= tolerance
        and abs(a.y2 - b.y2) <= tolerance
    ) or (
        abs(a.x - b.x) <= tolerance
        and abs(a.x2 - b.x2) <= tolerance
    )


def _spine_ratio(rooms: list[Room]) -> float | None:
    """Fraction of corridor pairs on one shared band; 1.0 = a clean spine."""
    n = len(rooms)
    if n < 2:
        return None
    pairs = sum(1 for i in range(n) for j in range(i + 1, n))
    aligned = sum(1 for i in range(n) for j in range(i + 1, n) if _same_band(rooms[i], rooms[j]))
    return round(aligned / pairs, 2)


# --- doors -------------------------------------------------------------------

def _door_wall(plan: Plan, door: Door) -> tuple[str, float, float, float] | None:
    """``(orientation, lo, hi, line)`` of the wall the door sits on."""
    pair = door_rooms(door, plan.rooms)
    if pair is None:
        return None
    return plan.rooms[pair[0]].shared_wall(plan.rooms[pair[1]])


def _door_interval(door: Door, wall: tuple[str, float, float, float]) -> tuple[float, float]:
    """The door's interval along its wall: ``(start, end)`` in feet."""
    lo, hi = wall[1], wall[2]
    if wall[0] == "vertical":
        return max(lo, door.y), min(hi, door.y + door.width)
    return max(lo, door.x), min(hi, door.x + door.width)


def _door_corner_violations(plan: Plan) -> int:
    """Doors closer than ``DOOR_CORNER_CLEARANCE`` to a wall corner."""
    violations = 0
    for door in plan.doors:
        wall = _door_wall(plan, door)
        if wall is None:
            continue
        _, lo, hi, _ = wall
        start, end = _door_interval(door, wall)
        if start - lo < DOOR_CORNER_CLEARANCE or hi - end < DOOR_CORNER_CLEARANCE:
            violations += 1
    return violations


def _door_spacing_violations(plan: Plan) -> int:
    """Pairs of doors on the same wall closer than ``DOOR_SPACING`` apart."""
    by_wall: dict[tuple, list[tuple[float, float]]] = {}
    for door in plan.doors:
        wall = _door_wall(plan, door)
        if wall is None:
            continue
        by_wall.setdefault(wall, []).append(_door_interval(door, wall))
    violations = 0
    for intervals in by_wall.values():
        intervals.sort()
        for prev, current in itertools.pairwise(intervals):
            if current[0] - prev[1] < DOOR_SPACING:
                violations += 1
    return violations


def _opposing_door_pairs(plan: Plan) -> int:
    """Doors facing each other across a corridor narrower than 5 ft."""
    placed: list[tuple[Door, tuple[str, float, float, float], tuple[float, float]]] = []
    for door in plan.doors:
        wall = _door_wall(plan, door)
        if wall is None:
            continue
        placed.append((door, wall, _door_interval(door, wall)))
    pairs = 0
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            door_a, wall_a, (a_lo, a_hi) = placed[i]
            door_b, wall_b, (b_lo, b_hi) = placed[j]
            if wall_a[0] != wall_b[0]:
                continue
            line_a, line_b = wall_a[3], wall_b[3]
            gap = abs(line_a - line_b)
            if gap <= _LINE_TOL or gap > OPPOSING_CORRIDOR_WIDTH:
                continue
            if max(a_lo, b_lo) < min(a_hi, b_hi):
                pairs += 1
    return pairs


# --- wet zone and zones ------------------------------------------------------

def _runs(a: Room, b: Room, minimum: float = CONNECT_RUN) -> bool:
    """True when the two rooms share a wall run of at least ``minimum`` feet."""
    return a.shared_wall_length(b) >= minimum


def _bedroom_share(bathrooms: list[Room], bedrooms: list[Room]) -> float | None:
    """Fraction of these bathrooms sharing a wall with some bedroom."""
    if not bathrooms:
        return None
    near = sum(any(_runs(b, bed) for bed in bedrooms) for b in bathrooms)
    return round(near / len(bathrooms), 2)


def _largest_cluster_share(bedrooms: list[Room]) -> float | None:
    """Fraction of bedrooms in the largest wall-connected bedroom cluster."""
    n = len(bedrooms)
    if n == 0:
        return None
    seen: list[bool] = [False] * n
    largest = 0
    for start in range(n):
        if seen[start]:
            continue
        size = 0
        stack = [start]
        seen[start] = True
        while stack:
            i = stack.pop()
            size += 1
            for j in range(n):
                if not seen[j] and _runs(bedrooms[i], bedrooms[j]):
                    seen[j] = True
                    stack.append(j)
        largest = max(largest, size)
    return round(largest / n, 2)


def _bedroom_from_circulation(plan: Plan, bedrooms: list[Room]) -> float | None:
    """Fraction of bedrooms whose modeled door opens onto circulation.

    A bedroom entered through another bedroom (its door graph neighbour is not
    circulation) reads as a private zone failure; the en-suite exception is
    handled by the walkability rule, so here we only care about the bedroom's
    own neighbours in the door graph.
    """
    if not bedrooms:
        return None
    graph = walkable_graph(plan)
    count = 0
    for index, room in enumerate(plan.rooms):
        if room not in bedrooms:
            continue
        neighbours = [plan.rooms[n] for n in graph.neighbors(index)]
        if any(n.type in CIRCULATION_TYPES for n in neighbours):
            count += 1
    return round(count / len(bedrooms), 2)


# --- daylight ----------------------------------------------------------------

def external_edges(room: Room, plan: Plan, tolerance: float = 0.1) -> int:
    """Number of distinct plot edges the room touches (0..2).

    A public helper the adaptive search uses to see how much daylight a room
    has: one external wall for a window, two (a corner room) for
    cross-ventilation.
    """
    edges = 0
    if room.x <= tolerance:
        edges += 1
    if room.y <= tolerance:
        edges += 1
    if room.x2 >= plan.plot_width - tolerance:
        edges += 1
    if room.y2 >= plan.plot_length - tolerance:
        edges += 1
    return edges


def _window_room(plan: Plan, window: Window) -> Room | None:
    """The room whose external wall run contains ``window``."""
    tolerance = WALLS.edge_tolerance
    for room in plan.rooms:
        if room.type is not window.room:
            continue
        if window.orientation == "vertical":
            on_line = (
                abs(window.x - room.x) <= tolerance or abs(window.x - room.x2) <= tolerance
            )
            within = (
                window.y >= room.y - tolerance
                and window.y + window.width <= room.y2 + tolerance
            )
        else:
            on_line = (
                abs(window.y - room.y) <= tolerance or abs(window.y - room.y2) <= tolerance
            )
            within = (
                window.x >= room.x - tolerance
                and window.x + window.width <= room.x2 + tolerance
            )
        if on_line and within:
            return room
    return None


def _window_corner_violations(plan: Plan) -> int:
    """Windows closer than ``WINDOW_CORNER_CLEARANCE`` to a wall corner."""
    violations = 0
    for window in plan.windows:
        room = _window_room(plan, window)
        if room is None:
            continue
        if window.orientation == "vertical":
            clear = min(window.y - room.y, room.y2 - (window.y + window.width))
        else:
            clear = min(window.x - room.x, room.x2 - (window.x + window.width))
        if clear < WINDOW_CORNER_CLEARANCE:
            violations += 1
    return violations


def _window_door_violations(plan: Plan) -> int:
    """Window/door pairs on the same wall line within ``WINDOW_DOOR_SPACING``."""
    violations = 0
    for window in plan.windows:
        w_start, w_end = (
            (window.y, window.y + window.width)
            if window.orientation == "vertical"
            else (window.x, window.x + window.width)
        )
        for door in plan.doors:
            wall = _door_wall(plan, door)
            if wall is None or wall[0] != window.orientation:
                continue
            line = window.x if window.orientation == "vertical" else window.y
            if abs(wall[3] - line) > _LINE_TOL:
                continue
            d_start, d_end = _door_interval(door, wall)
            gap = max(0.0, max(w_start, d_start) - min(w_end, d_end))
            if gap < WINDOW_DOOR_SPACING:
                violations += 1
    return violations


# --- space -------------------------------------------------------------------

def _uncovered_fraction(plan: Plan) -> float:
    """Fraction of the buildable plot no room claims (gross)."""
    if plan.walls is not None:
        return plan.walls.uncovered_fraction
    plot_area = plan.plot_width * plan.plot_length
    if plot_area <= 0:
        return 0.0
    covered = sum(r.area for r in plan.rooms)
    return round(max(0.0, 1.0 - covered / plot_area), 3)


__all__ = [
    "BATHROOM_MIN_SIDE",
    "CORRIDOR_MIN_WIDTH",
    "DOOR_CORNER_CLEARANCE",
    "DOOR_SPACING",
    "HABITABLE",
    "OPPOSING_CORRIDOR_WIDTH",
    "SOCIAL",
    "WINDOW_CORNER_CLEARANCE",
    "WINDOW_DOOR_SPACING",
    "QualityMetrics",
    "quality_metrics",
]
