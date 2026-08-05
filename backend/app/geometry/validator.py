"""Architectural sanity checks and automatic repair.

Step 4 of the specification demands proper room arrangement, logical
connectivity, consistent wall alignment and balanced proportions. This module
is where those become testable assertions rather than aspirations.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import pairwise
from math import log

from app.core.logging import get_logger
from app.geometry.primitives import (
    GRID,
    Rect,
    align_walls,
    max_area,
    min_area,
    min_side,
    snap,
)
from app.schemas.enums import RoomType
from app.schemas.requirements import RoomTarget

logger = get_logger(__name__)

MAX_ASPECT_RATIO = 3.6
MIN_ROOM_AREA = 14.0

#: A room the client sized itself is held to its own request rather than to the
#: generic bands: these are the fraction of the requested area it may sit below
#: and above before every sizing pass treats it as out of tolerance.
TARGET_FLOOR = 0.92
TARGET_CEILING = 1.08

#: Rooms you may legitimately walk *through* to reach somewhere else. A plan
#: that only connects because you can cross a bedroom is not a plan.
CIRCULATION_TYPES: frozenset[RoomType] = frozenset(
    {
        RoomType.LIVING_ROOM,
        RoomType.DINING_ROOM,
        RoomType.FOYER,
        RoomType.PASSAGE,
    }
)

#: Targets keyed by room type. Values may be a bare area for older callers.
Targets = dict[RoomType, RoomTarget | float]


def normalise_targets(targets: Targets | None) -> dict[RoomType, RoomTarget]:
    """Accept ``{type: area}`` as well as ``{type: RoomTarget}``."""
    if not targets:
        return {}
    return {room: RoomTarget.coerce(value) for room, value in targets.items()}


def target_floor(room: Rect, targets: dict[RoomType, RoomTarget] | None) -> float:
    """Smallest area this room may have - the client's request wins if there is one."""
    target = targets.get(room.type) if targets else None
    if target is None:
        return min_area(room.type)
    return target.area * TARGET_FLOOR


def target_ceiling(room: Rect, targets: dict[RoomType, RoomTarget] | None) -> float:
    """Largest area this room may have - the client's request wins if there is one."""
    target = targets.get(room.type) if targets else None
    if target is None:
        return max_area(room.type)
    return target.area * TARGET_CEILING


def unreachable_indices(indoor: list[Rect], *, through_any_room: bool = False) -> list[int]:
    """Indices of rooms you cannot walk to from the living room.

    The search starts at the living room (or the first circulation space, or
    room 0 as a last resort) and by default only *continues* through
    circulation - plus the one step from a bedroom into its own en-suite - so a
    kitchen reached by crossing a bedroom does not count as connected.

    ``through_any_room`` relaxes that to bare adjacency. A room unreachable
    even then touches nothing that leads anywhere, which is a hard fault in the
    plan rather than a question of how you get there.
    """
    if not indoor:
        return []

    start = next(
        (i for i, r in enumerate(indoor) if r.type is RoomType.LIVING_ROOM),
        next((i for i, r in enumerate(indoor) if r.type in CIRCULATION_TYPES), 0),
    )

    adjacency: dict[int, set[int]] = {i: set() for i in range(len(indoor))}
    for i, a in enumerate(indoor):
        for j in range(i + 1, len(indoor)):
            if a.is_adjacent(indoor[j]):
                adjacency[i].add(j)
                adjacency[j].add(i)

    seen = {start}
    queue = [start]
    while queue:
        current = queue.pop()
        for neighbour in adjacency[current] - seen:
            room = indoor[neighbour]
            # An en-suite is entered from its own bedroom and nowhere else, so
            # that one step through a bedroom is the whole point rather than a
            # fault. Every other room has to be reached from circulation.
            if (
                not through_any_room
                and indoor[current].type.is_bedroom
                and room.type is not RoomType.ATTACHED_BATHROOM
            ):
                continue
            seen.add(neighbour)
            if (
                through_any_room
                or room.type in CIRCULATION_TYPES
                or room.type.is_bedroom
            ):
                queue.append(neighbour)

    return [i for i in range(len(indoor)) if i not in seen]


@dataclass
class ValidationReport:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors

    def all_messages(self) -> list[str]:
        return [*self.errors, *self.warnings]


class LayoutValidator:
    def __init__(self, plot_width: float, plot_length: float) -> None:
        self._w = plot_width
        self._l = plot_length

    def validate(self, rooms: list[Rect]) -> ValidationReport:
        errors: list[str] = []
        warnings: list[str] = []

        if not rooms:
            return ValidationReport(["Layout contains no rooms"], [])

        self._check_bounds(rooms, errors)
        self._check_overlaps(rooms, errors)
        self._check_dimensions(rooms, warnings)
        self._check_alignment(rooms, warnings)
        self._check_connectivity(rooms, errors, warnings)
        self._check_coverage(rooms, warnings)

        return ValidationReport(errors, warnings)

    # --- Individual checks ------------------------------------------------
    def _check_bounds(self, rooms: list[Rect], errors: list[str]) -> None:
        for room in rooms:
            outside = (
                room.x < -0.05
                or room.y < -0.05
                or room.x2 > self._w + 0.05
                or room.y2 > self._l + 0.05
            )
            if outside:
                errors.append(f"'{room.name}' falls outside the plot boundary")

    @staticmethod
    def _check_overlaps(rooms: list[Rect], errors: list[str]) -> None:
        for i, a in enumerate(rooms):
            for b in rooms[i + 1 :]:
                if a.overlaps(b, tolerance=0.25):
                    errors.append(f"'{a.name}' overlaps '{b.name}'")

    @staticmethod
    def _check_dimensions(rooms: list[Rect], warnings: list[str]) -> None:
        for room in rooms:
            if room.area < MIN_ROOM_AREA:
                warnings.append(f"'{room.name}' is only {room.area:.0f} sq ft")
            elif min(room.width, room.height) < min_side(room.type) - 0.6:
                warnings.append(
                    f"'{room.name}' is narrower than the {min_side(room.type):g} ft minimum"
                )
            if room.aspect > MAX_ASPECT_RATIO:
                warnings.append(f"'{room.name}' is disproportionately long ({room.aspect:.1f}:1)")

    @staticmethod
    def _check_alignment(rooms: list[Rect], warnings: list[str]) -> None:
        """Edges should sit on shared wall lines, not a few inches off them."""
        strays = 0
        for edges in (
            sorted(v for r in rooms for v in (r.x, r.x2)),
            sorted(v for r in rooms for v in (r.y, r.y2)),
        ):
            for a, b in pairwise(edges):
                if 0 < b - a < GRID:
                    strays += 1
        if strays:
            warnings.append(f"{strays} wall edge(s) are marginally out of alignment")

    @staticmethod
    def _check_connectivity(rooms: list[Rect], errors: list[str], warnings: list[str]) -> None:
        """Every room must be reachable from the living room *through circulation*.

        Touching another room is not the same as being reachable: a bedroom
        that can only be entered by crossing a second bedroom, a bathroom or
        the kitchen is not connected in any sense an occupant would recognise.
        The walk therefore only continues through the spaces in
        :data:`CIRCULATION_TYPES`; everything else is a destination.
        """
        indoor = [r for r in rooms if not r.type.is_outdoor]
        if not indoor:
            return

        stranded = [
            indoor[i].name for i in unreachable_indices(indoor, through_any_room=True)
        ]
        if len(stranded) > 2:
            # Touching nothing that leads anywhere: the plan does not hold
            # together, whatever route you are willing to take.
            errors.append(f"Cut off from the rest of the plan: {', '.join(stranded)}")

        awkward = [
            indoor[i].name
            for i in unreachable_indices(indoor)
            if indoor[i].name not in stranded
        ]
        if awkward:
            warnings.append(
                "Reachable only by crossing another room: " + ", ".join(awkward)
            )

    def _check_coverage(self, rooms: list[Rect], warnings: list[str]) -> None:
        plot_area = self._w * self._l
        if plot_area <= 0:
            return

        coverage = sum(r.area for r in rooms) / plot_area
        if coverage < 0.55:
            warnings.append(f"Only {coverage * 100:.0f}% of the plot is used")

        # Unassigned pockets show up on the drawing as unexplained white holes.
        stray = sum(g.area for g in _find_gaps(rooms, self._w, self._l) if g.area >= 6.0)
        if stray / plot_area > 0.02:
            warnings.append(f"{stray:.0f} sq ft of floor area is unassigned")


class LayoutRepairer:
    """Best-effort fixes for the problems the validator finds.

    Runs before validation reports anything to the user, so most layouts never
    surface a warning at all.
    """

    def __init__(self, plot_width: float, plot_length: float) -> None:
        self._w = plot_width
        self._l = plot_length

    def repair(self, rooms: list[Rect]) -> list[Rect]:
        result = [r.copy() for r in rooms]
        result = self._clamp(result)
        result = self._resolve_overlaps(result)
        result = align_walls(result)
        result = self._clamp(result)
        result = self._drop_degenerate(result)
        return [r.snapped() for r in result]

    def _clamp(self, rooms: list[Rect]) -> list[Rect]:
        return [r.clamped(self._w, self._l) for r in rooms]

    @staticmethod
    def _resolve_overlaps(rooms: list[Rect]) -> list[Rect]:
        """Trim the smaller room along its shallower axis until the overlap clears.

        Trimming (rather than shifting) keeps every other wall where it is, so
        one fix cannot cascade into a chain of new misalignments.
        """
        ordered = sorted(range(len(rooms)), key=lambda i: rooms[i].area, reverse=True)
        for pos, i in enumerate(ordered):
            for j in ordered[pos + 1 :]:
                a, b = rooms[i], rooms[j]
                if not a.overlaps(b, tolerance=0.25):
                    continue

                dx = min(a.x2, b.x2) - max(a.x, b.x)
                dy = min(a.y2, b.y2) - max(a.y, b.y)

                if dx <= dy:
                    # Trim b horizontally, away from a's centre.
                    if b.center[0] >= a.center[0]:
                        rooms[j] = replace(b, x=b.x + dx, width=max(GRID, b.width - dx))
                    else:
                        rooms[j] = replace(b, width=max(GRID, b.width - dx))
                else:
                    if b.center[1] >= a.center[1]:
                        rooms[j] = replace(b, y=b.y + dy, height=max(GRID, b.height - dy))
                    else:
                        rooms[j] = replace(b, height=max(GRID, b.height - dy))
        return rooms

    @staticmethod
    def _drop_degenerate(rooms: list[Rect]) -> list[Rect]:
        """Discard rooms that repair squeezed below a usable size."""
        kept: list[Rect] = []
        for room in rooms:
            if room.width < 2.0 or room.height < 2.0 or room.area < 6.0:
                logger.debug("Dropping degenerate room '%s' (%.1f sq ft)", room.name, room.area)
                continue
            kept.append(room)
        return kept


def fill_gaps(
    rooms: list[Rect],
    plot_width: float,
    plot_length: float,
    *,
    strict: bool = False,
    targets: Targets | None = None,
) -> list[Rect]:
    """Absorb uncovered pockets into their neighbours.

    Trimming and shrinking leave slivers of unassigned floor. On a drawing
    those read as unexplained white holes, so every pocket is either merged
    into the adjacent room that shares its full edge, or - when it is big
    enough to stand alone and nothing can absorb it cleanly - promoted to a
    passage.

    In ``strict`` mode only merges that provably cannot introduce an overlap
    are applied (the host is flush with the gap, so the union is still a
    rectangle). That makes the pass safe to run last, with no repair after it
    to undo the work and reopen the hole.
    """
    if not rooms:
        return rooms

    book = normalise_targets(targets)
    for _ in range(4):
        gaps = _find_gaps(rooms, plot_width, plot_length)
        if not gaps:
            break
        for gap in gaps:
            rooms = _absorb_gap(rooms, gap, strict=strict, targets=book)
    return rooms


def rebalance_room_sizes(
    rooms: list[Rect], passes: int = 3, *, targets: Targets | None = None
) -> list[Rect]:
    """Move floor area from oversized rooms to undersized neighbours.

    Capping and gap filling both work one room at a time, so a plan can end up
    technically valid but lopsided - an 80 sq ft living room next to a 290 sq ft
    bedroom. This slides the wall between such a pair, which keeps both
    rectangles intact and cannot create an overlap or a gap because the two
    rooms are flush and the shared edge simply moves.
    """
    result = list(rooms)
    book = normalise_targets(targets)

    # Phase 1 - pull: rooms below their minimum take area from a fat neighbour.
    for _ in range(passes):
        needy = sorted(
            (r for r in result if r.area < target_floor(r, book) * 0.95),
            key=lambda r: target_floor(r, book) - r.area,
            reverse=True,
        )
        if not needy:
            break

        moved = False
        for short_room in needy:
            donor = _pick_donor(result, short_room, book)
            if donor is None:
                continue
            updated = _shift_shared_wall(result, donor, short_room, targets=book)
            if updated is not None:
                result = updated
                moved = True
                break
        if not moved:
            break

    # Phase 2 - push: rooms above their ceiling hand the excess to a neighbour
    # that still has headroom, even though that neighbour was not short of
    # space. Without this a bedroom left over its cap by gap filling stays
    # over it, because phase 1 only ever runs on behalf of a needy room.
    for _ in range(passes):
        bloated = sorted(
            (r for r in result if r.area > target_ceiling(r, book)),
            key=lambda r: r.area - target_ceiling(r, book),
            reverse=True,
        )
        if not bloated:
            break

        moved = False
        for fat_room in bloated:
            receiver = _pick_receiver(result, fat_room, book)
            if receiver is not None:
                updated = _shift_shared_wall(
                    result, fat_room, receiver, cap_donor=True, targets=book
                )
                if updated is not None:
                    result = updated
                    moved = True
                    break

            # No neighbour lines up with it. Cut the excess off as a strip and
            # let the ordinary gap logic find a home for it, which copes with
            # partial adjacency where a straight wall shift cannot.
            trimmed, freed = _trim_to_cap(fat_room, book)
            if trimmed is None or freed is None:
                continue
            result = [trimmed if r is fat_room else r for r in result]
            result = _absorb_gap(result, freed, targets=book)
            moved = True
            break

        if not moved:
            break

    return result


#: Below this many square feet a room is close enough to what was asked for.
TARGET_TOLERANCE = 4.0

#: How far one wall line may travel in a single solver move.
MAX_LINE_SHIFT = 8.0

#: Relative weights in the solver's objective. Area dominates, shape matters
#: nearly as much (it is what turns a 168 sq ft request into 10.5 x 25 ft), and
#: rooms the client did not size are held to their generic band more loosely.
AREA_WEIGHT = 1.0
ASPECT_WEIGHT = 0.15
UNTARGETED_WEIGHT = 0.35

#: The shape term is measured on a log ratio and capped, so that however badly
#: proportioned a room is it can never be worth trading away the floor area it
#: was asked for. Shape is a tie-breaker between plans of the right size.
MAX_ASPECT_PENALTY = 1.0


def resize_to_targets(
    rooms: list[Rect],
    targets: Targets,
    passes: int = 12,
    *,
    plot_width: float | None = None,
    plot_length: float | None = None,
) -> list[Rect]:
    """Drive rooms toward the size *and shape* the client asked for.

    Works by sliding whole wall lines rather than by trading area between one
    pair of rooms at a time. Every room whose edge lies on the line moves with
    it, so each step keeps all rooms rectangular and the footprint exactly
    conserved - no gap and no overlap can appear, which is what makes this safe
    to run as the last word on sizing.

    The move is bidirectional: a line is shifted whichever way lowers the total
    deviation from the brief, so a room *above* its requested size is trimmed
    just as readily as one below it is grown.

    Best effort by design: on a plot too small for everything requested, rooms
    end up proportionally short rather than the layout failing.
    """
    book = normalise_targets(targets)
    if not book or len(rooms) < 2:
        return rooms

    width = plot_width if plot_width is not None else max(r.x2 for r in rooms)
    length = plot_length if plot_length is not None else max(r.y2 for r in rooms)

    result = list(rooms)
    for _ in range(passes):
        improved = False
        for axis, extent in (("x", width), ("y", length)):
            groups = [
                group
                for line in _wall_lines(result, axis, extent)
                for group in _line_groups(result, axis, line)
            ]
            groups += _room_edge_groups(result, axis)

            # Every improving move is taken, not just the first: a plan has far
            # more walls than passes, and one move per pass converges nowhere
            # near the brief. Groups that share a room with a move already made
            # are left for the next pass, when they can be rebuilt against the
            # rooms as they now are.
            touched: set[int] = set()
            for group in groups:
                members = [id(r) for r in (*group[0], *group[1])]
                if touched.intersection(members):
                    continue
                moved = _best_line_shift(result, group, axis, book)
                if moved is not None:
                    result = moved
                    touched.update(members)
                    improved = True
        if not improved:
            break

    return result


def _room_cost(room: Rect, targets: dict[RoomType, RoomTarget]) -> float:
    """How badly this room misses what was asked of it."""
    target = targets.get(room.type)
    if target is not None:
        cost = AREA_WEIGHT * ((room.area - target.area) / target.area) ** 2
        aspect = target.aspect
        if aspect:
            drift = log(room.aspect / aspect) ** 2
            cost += ASPECT_WEIGHT * min(MAX_ASPECT_PENALTY, drift)
        return cost

    # Circulation is the designated home for whatever the sized rooms do not
    # want, so a long corridor is not a fault the solver should design away.
    if room.type is RoomType.PASSAGE:
        return 0.0

    cost = 0.0
    floor, ceiling = min_area(room.type), max_area(room.type)
    if room.area < floor:
        cost += ((floor - room.area) / floor) ** 2
    elif room.area > ceiling:
        cost += ((room.area - ceiling) / ceiling) ** 2
    if room.aspect > MAX_ASPECT_RATIO and not _tolerates_length(room.type):
        cost += ((room.aspect - MAX_ASPECT_RATIO) / MAX_ASPECT_RATIO) ** 2
    return cost * UNTARGETED_WEIGHT


def _plan_cost(rooms: list[Rect], targets: dict[RoomType, RoomTarget]) -> float:
    return sum(_room_cost(r, targets) for r in rooms)


def _low(room: Rect, axis: str) -> float:
    return room.x if axis == "x" else room.y


def _high(room: Rect, axis: str) -> float:
    return room.x2 if axis == "x" else room.y2


def _perp(room: Rect, axis: str) -> tuple[float, float]:
    """The room's extent along the axis the line does *not* run on."""
    return (room.y, room.y2) if axis == "x" else (room.x, room.x2)


def _wall_lines(rooms: list[Rect], axis: str, extent: float) -> list[float]:
    """Internal wall lines, in a stable order. The plot boundary never moves."""
    values = {snap(v) for r in rooms for v in (_low(r, axis), _high(r, axis))}
    return sorted(v for v in values if 0.3 < v < extent - 0.3)


def _merge_intervals(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 0.05:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _same_span(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    return len(a) == len(b) and all(
        abs(p[0] - q[0]) < 0.3 and abs(p[1] - q[1]) < 0.3 for p, q in zip(a, b, strict=True)
    )


def _line_groups(
    rooms: list[Rect], axis: str, line: float
) -> list[tuple[list[Rect], list[Rect]]]:
    """Sets of rooms that can move together when this wall line slides.

    A group is movable only when the rooms ending at the line cover exactly the
    same run as the rooms starting at it. Otherwise sliding would either open a
    hole where only one side retreats or drive two rooms into each other.
    """
    if any(_low(r, axis) < line - 0.3 and _high(r, axis) > line + 0.3 for r in rooms):
        return []  # a room straddles the line; moving it would cut that room in two

    behind = [r for r in rooms if abs(_high(r, axis) - line) < 0.3]
    ahead = [r for r in rooms if abs(_low(r, axis) - line) < 0.3]
    if not behind or not ahead:
        return []

    # Rooms on opposite sides whose runs overlap must move as one unit.
    parent = list(range(len(behind) + len(ahead)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    for i, a in enumerate(behind):
        a_lo, a_hi = _perp(a, axis)
        for j, b in enumerate(ahead):
            b_lo, b_hi = _perp(b, axis)
            if min(a_hi, b_hi) - max(a_lo, b_lo) > 0.05:
                union(i, len(behind) + j)

    components: dict[int, tuple[list[Rect], list[Rect]]] = {}
    for i, room in enumerate(behind):
        components.setdefault(find(i), ([], []))[0].append(room)
    for j, room in enumerate(ahead):
        components.setdefault(find(len(behind) + j), ([], []))[1].append(room)

    groups: list[tuple[list[Rect], list[Rect]]] = []
    for key in sorted(components):
        back, front = components[key]
        if not back or not front:
            continue
        if _same_span(
            _merge_intervals([_perp(r, axis) for r in back]),
            _merge_intervals([_perp(r, axis) for r in front]),
        ):
            groups.append((back, front))
    return groups


def _room_edge_groups(
    rooms: list[Rect], axis: str
) -> list[tuple[list[Rect], list[Rect]]]:
    """Movable units built around a single room's wall.

    A whole wall line rarely balances in a plan grown from a traced template -
    the rooms either side of it start and stop in different places. One room's
    wall very often does: the neighbours backing onto it sit inside its run and
    between them cover the whole of it. Sliding that wall keeps every rectangle
    rectangular and touches nothing outside the room's own span, so it is just
    as safe as moving the entire line and reaches far more of the plan.
    """
    groups: list[tuple[list[Rect], list[Rect]]] = []
    seen: set[tuple[str, float, tuple[int, ...]]] = set()

    for room in rooms:
        span_lo, span_hi = _perp(room, axis)
        for side in ("high", "low"):
            line = _high(room, axis) if side == "high" else _low(room, axis)
            facing = "low" if side == "high" else "high"

            neighbours = [
                other
                for other in rooms
                if other is not room
                and abs(
                    (_low(other, axis) if facing == "low" else _high(other, axis)) - line
                )
                < 0.3
                and min(_perp(other, axis)[1], span_hi) - max(_perp(other, axis)[0], span_lo)
                > 0.05
            ]
            if not neighbours:
                continue

            spans = [_perp(o, axis) for o in neighbours]
            # They must sit inside this room's run and together fill it, or the
            # wall cannot move without opening a hole at one end.
            if any(lo < span_lo - 0.05 or hi > span_hi + 0.05 for lo, hi in spans):
                continue
            if not _same_span(_merge_intervals(spans), [(span_lo, span_hi)]):
                continue

            key = (
                axis,
                snap(line),
                tuple(sorted(id(r) for r in (room, *neighbours))),
            )
            if key in seen:
                continue
            seen.add(key)
            groups.append(([room], neighbours) if side == "high" else (neighbours, [room]))

    return groups


def _shift_group(
    group: tuple[list[Rect], list[Rect]], axis: str, delta: float
) -> dict[int, Rect] | None:
    """Slide one wall by ``delta``, growing the rooms behind it.

    Returns the replacement for each room that moves, keyed by the identity of
    the room it replaces, or ``None`` when the shift would squeeze one of them
    below a usable width.
    """
    behind, ahead = group
    moved: dict[int, Rect] = {}

    for room in behind:
        size = (room.width if axis == "x" else room.height) + delta
        if size < min_side(room.type) or size < GRID:
            return None
        moved[id(room)] = (
            replace(room, width=size) if axis == "x" else replace(room, height=size)
        )
    for room in ahead:
        size = (room.width if axis == "x" else room.height) - delta
        if size < min_side(room.type) or size < GRID:
            return None
        moved[id(room)] = (
            replace(room, x=room.x + delta, width=size)
            if axis == "x"
            else replace(room, y=room.y + delta, height=size)
        )

    return moved


def _best_line_shift(
    rooms: list[Rect],
    group: tuple[list[Rect], list[Rect]],
    axis: str,
    targets: dict[RoomType, RoomTarget],
) -> list[Rect] | None:
    """The shift of this wall that most improves the fit, if any does.

    Only the rooms meeting on the wall change, so the whole plan's cost need
    never be recomputed - the difference across those few rooms *is* the
    difference across the plan.
    """
    members = [*group[0], *group[1]]
    best: dict[int, Rect] | None = None
    best_cost = sum(_room_cost(r, targets) for r in members) - 1e-9

    steps = int(MAX_LINE_SHIFT / GRID)
    for step in range(1, steps + 1):
        for delta in (step * GRID, -step * GRID):
            moved = _shift_group(group, axis, delta)
            if moved is None:
                continue
            cost = sum(_room_cost(moved[id(r)], targets) for r in members)
            if cost < best_cost:
                best, best_cost = moved, cost

    if best is None:
        return None
    return [best.get(id(r), r) for r in rooms]


def _trim_to_cap(
    room: Rect, targets: dict[RoomType, RoomTarget] | None = None
) -> tuple[Rect | None, Rect | None]:
    """Split a room into (room at its ceiling, the leftover strip)."""
    ceiling = target_ceiling(room, targets)
    if room.area <= ceiling:
        return None, None

    if room.width >= room.height:
        width = snap(max(min_side(room.type), ceiling / room.height))
        strip = room.width - width
        if strip < GRID:
            return None, None
        return (
            replace(room, width=width),
            replace(room, x=room.x + width, width=strip),
        )

    height = snap(max(min_side(room.type), ceiling / room.width))
    strip = room.height - height
    if strip < GRID:
        return None, None
    return (
        replace(room, height=height),
        replace(room, y=room.y + height, height=strip),
    )


def _pick_donor(
    rooms: list[Rect], needy: Rect, targets: dict[RoomType, RoomTarget] | None = None
) -> Rect | None:
    """The flush neighbour with the most spare area to give."""
    candidates: list[tuple[Rect, float]] = []
    for room in rooms:
        if room is needy or room.shared_wall_length(needy, tolerance=0.3) <= 0:
            continue
        if not _flush(room, needy):
            continue
        spare = room.area - target_floor(room, targets)
        if spare > 12.0:
            candidates.append((room, spare))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1])[0]


def _pick_receiver(
    rooms: list[Rect], bloated: Rect, targets: dict[RoomType, RoomTarget] | None = None
) -> Rect | None:
    """The flush neighbour with the most room left under its own ceiling."""
    candidates: list[tuple[Rect, float]] = []
    for room in rooms:
        if room is bloated or room.shared_wall_length(bloated, tolerance=0.3) <= 0:
            continue
        if not _flush(room, bloated):
            continue
        headroom = target_ceiling(room, targets) - room.area
        if headroom > 12.0:
            candidates.append((room, headroom))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1])[0]


def _flush(a: Rect, b: Rect) -> bool:
    """True when the two rooms line up exactly on the axis they do not share."""
    return (abs(a.x - b.x) < 0.3 and abs(a.x2 - b.x2) < 0.3) or (
        abs(a.y - b.y) < 0.3 and abs(a.y2 - b.y2) < 0.3
    )


def _shift_shared_wall(
    rooms: list[Rect],
    donor: Rect,
    needy: Rect,
    *,
    cap_donor: bool = False,
    targets: dict[RoomType, RoomTarget] | None = None,
) -> list[Rect] | None:
    """Slide the wall between ``donor`` and ``needy`` toward the donor.

    ``cap_donor`` switches the goal from "bring the receiver up to its minimum"
    to "bring the donor down to its ceiling", which is what the push phase
    needs.
    """
    if cap_donor:
        wanted = donor.area - target_ceiling(donor, targets)
        spare = target_ceiling(needy, targets) - needy.area
    else:
        wanted = target_floor(needy, targets) - needy.area
        spare = donor.area - target_floor(donor, targets)

    return _move_area(rooms, donor, needy, wanted_area=wanted, spare_area=spare)


def _move_area(
    rooms: list[Rect], donor: Rect, needy: Rect, *, wanted_area: float, spare_area: float
) -> list[Rect] | None:
    """Hand ``wanted_area`` sq ft from ``donor`` to ``needy`` across their shared wall.

    Both rooms stay rectangular and the pair still covers exactly the same
    footprint, so no gap or overlap can appear - which is what makes this safe
    to run after the last repair pass.
    """
    vertical = abs(donor.x - needy.x) < 0.3 and abs(donor.x2 - needy.x2) < 0.3
    span = donor.width if vertical else donor.height
    if span <= 0:
        return None

    wanted = wanted_area / span
    spare = spare_area / span

    shift = snap(min(wanted, spare, 6.0))
    if shift < GRID:
        return None

    if vertical:
        # Stacked: the wall is horizontal, so heights change.
        if needy.y > donor.y:
            new_donor = replace(donor, height=donor.height - shift)
            new_needy = replace(needy, y=needy.y - shift, height=needy.height + shift)
        else:
            new_donor = replace(donor, y=donor.y + shift, height=donor.height - shift)
            new_needy = replace(needy, height=needy.height + shift)
    else:
        # Side by side: the wall is vertical, so widths change.
        if needy.x > donor.x:
            new_donor = replace(donor, width=donor.width - shift)
            new_needy = replace(needy, x=needy.x - shift, width=needy.width + shift)
        else:
            new_donor = replace(donor, x=donor.x + shift, width=donor.width - shift)
            new_needy = replace(needy, width=needy.width + shift)

    if min(new_donor.width, new_donor.height) < min_side(new_donor.type):
        return None

    return [
        new_donor if r is donor else new_needy if r is needy else r for r in rooms
    ]


#: A corridor narrower than this is not a corridor, it is a gap between walls.
CORRIDOR_WIDTH = 3.5


def _with_axis_range(room: Rect, axis: str, lo: float, hi: float) -> Rect:
    """Replace the room's extent along ``axis``."""
    if axis == "x":
        return replace(room, x=lo, width=hi - lo)
    return replace(room, y=lo, height=hi - lo)


def _with_perp_range(room: Rect, axis: str, lo: float, hi: float) -> Rect:
    """Replace the room's extent across ``axis``."""
    return _with_axis_range(room, "y" if axis == "x" else "x", lo, hi)


def ensure_circulation(
    rooms: list[Rect], plot_width: float, plot_length: float, *, max_corridors: int = 3
) -> list[Rect]:
    """Cut corridors until every room can be entered without crossing another.

    Gap filling produces passages by accident, wherever trimming happened to
    leave a pocket; that is why they turn up as floating blobs in the middle of
    a plan. This does the opposite - it finds the rooms that are stranded under
    :func:`unreachable_indices` and routes a corridor to each one on purpose,
    shaving it off the rooms in the way.

    Strictly best effort: a candidate corridor is only committed once it has
    been checked for overlaps and for actually having connected the room, so a
    plan that cannot be routed is left exactly as it was.
    """
    result = list(rooms)
    for _ in range(max_corridors):
        indoor = [r for r in result if not r.type.is_outdoor]
        stranded = unreachable_indices(indoor)
        if not stranded:
            break

        # Largest first: the biggest stranded room is the most obvious fault.
        orphan = max((indoor[i] for i in stranded), key=lambda r: r.area)
        routed = _route_corridor(result, orphan, plot_width, plot_length)
        if routed is None:
            logger.debug("No corridor could be routed to '%s'", orphan.name)
            break
        result = routed

    return merge_adjacent_passages(result)


def _route_corridor(
    rooms: list[Rect], orphan: Rect, plot_width: float, plot_length: float
) -> list[Rect] | None:
    """Try to connect ``orphan`` to the nearest circulation space."""
    hubs = [r for r in rooms if r.type in CIRCULATION_TYPES and r is not orphan]
    hubs.sort(
        key=lambda h: abs(h.center[0] - orphan.center[0]) + abs(h.center[1] - orphan.center[1])
    )

    for hub in hubs:
        for axis in ("x", "y"):
            for corridor in _corridor_candidates(orphan, hub, axis):
                carved = _carve_corridor(rooms, corridor, axis, orphan, hub)
                if carved is not None:
                    return carved
    return None


def _corridor_candidates(orphan: Rect, hub: Rect, axis: str) -> list[Rect]:
    """Straight runs of corridor between two rooms, flush with one wall or the other."""
    o_lo, o_hi = _perp(orphan, axis)
    h_lo, h_hi = _perp(hub, axis)
    band_lo, band_hi = max(o_lo, h_lo), min(o_hi, h_hi)
    if band_hi - band_lo < CORRIDOR_WIDTH:
        return []  # no straight run lines up between them

    if _low(hub, axis) >= _high(orphan, axis) - 0.05:
        start, end = _high(orphan, axis), _low(hub, axis)
    elif _low(orphan, axis) >= _high(hub, axis) - 0.05:
        start, end = _high(hub, axis), _low(orphan, axis)
    else:
        return []  # they overlap on this axis; a straight corridor makes no sense

    if end - start < GRID:
        return []  # already touching

    template = Rect(RoomType.PASSAGE, "Passage", 0.0, 0.0, 1.0, 1.0)
    spine = _with_axis_range(template, axis, start, end)
    return [
        _with_perp_range(spine, axis, band_lo, band_lo + CORRIDOR_WIDTH),
        _with_perp_range(spine, axis, band_hi - CORRIDOR_WIDTH, band_hi),
    ]


def _carve_corridor(
    rooms: list[Rect], corridor: Rect, axis: str, orphan: Rect, hub: Rect
) -> list[Rect] | None:
    """Shave the corridor out of the rooms it crosses, or give up cleanly.

    A room may only be shaved when the corridor runs the room's full length and
    lies flush against one of its sides - anything else would cut the room into
    two pieces, and a room is a rectangle.
    """
    run_lo, run_hi = _low(corridor, axis), _high(corridor, axis)
    blockers = [r for r in rooms if r.overlap_area(corridor) > 0.05]
    if not blockers or orphan in blockers or hub in blockers:
        return None

    # The run has to cross each blocker end to end. A room sticking out past it
    # would be shaved along its whole length while the corridor only covers
    # part of that, leaving a sliver of unassigned floor behind.
    if any(
        _low(b, axis) < run_lo - 0.05 or _high(b, axis) > run_hi + 0.05 for b in blockers
    ):
        return None

    c_lo, c_hi = _perp(corridor, axis)
    shaved: dict[int, Rect | None] = {}
    for blocker in blockers:
        b_lo, b_hi = _perp(blocker, axis)
        if c_lo <= b_lo + 0.05 and c_hi >= b_hi - 0.05:
            # The corridor swallows it whole; only acceptable for circulation.
            if blocker.type is not RoomType.PASSAGE:
                return None
            shaved[id(blocker)] = None
            continue

        if abs(b_lo - c_lo) < 0.35:
            trimmed = _with_perp_range(blocker, axis, c_hi, b_hi)
        elif abs(b_hi - c_hi) < 0.35:
            trimmed = _with_perp_range(blocker, axis, b_lo, c_lo)
        else:
            return None  # the corridor would run down the middle of this room

        if min(trimmed.width, trimmed.height) < min_side(trimmed.type):
            return None
        if trimmed.area < min_area(trimmed.type) * 0.7:
            return None
        shaved[id(blocker)] = trimmed

    result = [shaved.get(id(r), r) for r in rooms]
    result = [r for r in result if r is not None]
    result.append(corridor.snapped())

    for i, a in enumerate(result):
        for b in result[i + 1 :]:
            if a.overlaps(b, tolerance=0.25):
                return None

    indoor = [r for r in result if not r.type.is_outdoor]
    if len(unreachable_indices(indoor)) >= len(
        unreachable_indices([r for r in rooms if not r.type.is_outdoor])
    ):
        return None  # carving it did not actually connect anything

    return result


def merge_adjacent_passages(rooms: list[Rect]) -> list[Rect]:
    """Join circulation spaces that touch into one, where the union is a box.

    Gap filling can leave several small pockets around the core, each labelled
    separately. A real plan draws that as one corridor.
    """
    result = list(rooms)
    merged_any = True
    while merged_any:
        merged_any = False
        passages = [r for r in result if r.type is RoomType.PASSAGE]
        for i, a in enumerate(passages):
            for b in passages[i + 1 :]:
                if a.shared_wall_length(b, tolerance=0.3) <= 0:
                    continue
                flush = (abs(a.x - b.x) < 0.3 and abs(a.x2 - b.x2) < 0.3) or (
                    abs(a.y - b.y) < 0.3 and abs(a.y2 - b.y2) < 0.3
                )
                if not flush:
                    continue
                x1, y1 = min(a.x, b.x), min(a.y, b.y)
                combined = replace(
                    a, x=x1, y=y1, width=max(a.x2, b.x2) - x1, height=max(a.y2, b.y2) - y1
                )
                result = [combined if r is a else r for r in result if r is not b]
                merged_any = True
                break
            if merged_any:
                break
    return result


def absorb_vacated(
    rooms: list[Rect], vacated: Rect, *, targets: Targets | None = None
) -> list[Rect]:
    """Hand a removed room's footprint to a neighbour.

    ``vacated`` must not be in ``rooms``. Uses the same tiered rules as gap
    filling, so removing a room can never distort its neighbour into a shape
    the validator would reject.
    """
    return _absorb_gap(rooms, vacated, targets=normalise_targets(targets))


def _find_gaps(rooms: list[Rect], plot_width: float, plot_length: float) -> list[Rect]:
    """Maximal uncovered rectangles, largest first.

    Room edges induce a non-uniform grid over the plot; a cell of that grid is
    either wholly inside a room or wholly outside every room, so testing cell
    centres is exact rather than approximate.
    """
    xs = sorted({0.0, plot_width, *(v for r in rooms for v in (r.x, r.x2))})
    ys = sorted({0.0, plot_length, *(v for r in rooms for v in (r.y, r.y2))})
    xs = [v for v in xs if -0.01 <= v <= plot_width + 0.01]
    ys = [v for v in ys if -0.01 <= v <= plot_length + 0.01]

    columns, rows = len(xs) - 1, len(ys) - 1
    if columns <= 0 or rows <= 0:
        return []

    covered = [[False] * columns for _ in range(rows)]
    for j in range(rows):
        cy = (ys[j] + ys[j + 1]) / 2
        for i in range(columns):
            cx = (xs[i] + xs[i + 1]) / 2
            covered[j][i] = any(
                r.x - 0.01 <= cx <= r.x2 + 0.01 and r.y - 0.01 <= cy <= r.y2 + 0.01 for r in rooms
            )

    gaps: list[Rect] = []
    used = [[False] * columns for _ in range(rows)]
    for j in range(rows):
        for i in range(columns):
            if covered[j][i] or used[j][i]:
                continue
            # Grow right, then down, keeping the block rectangular.
            i2 = i
            while i2 + 1 < columns and not covered[j][i2 + 1] and not used[j][i2 + 1]:
                i2 += 1
            j2 = j
            while j2 + 1 < rows and all(
                not covered[j2 + 1][k] and not used[j2 + 1][k] for k in range(i, i2 + 1)
            ):
                j2 += 1

            for jj in range(j, j2 + 1):
                for ii in range(i, i2 + 1):
                    used[jj][ii] = True

            width, height = xs[i2 + 1] - xs[i], ys[j2 + 1] - ys[j]
            if width * height >= 1.0:
                gaps.append(Rect(RoomType.PASSAGE, "Passage", xs[i], ys[j], width, height))

    gaps.sort(key=lambda g: g.area, reverse=True)
    return gaps


def _absorb_gap(
    rooms: list[Rect],
    gap: Rect,
    *,
    strict: bool = False,
    targets: dict[RoomType, RoomTarget] | None = None,
) -> list[Rect]:
    """Extend the neighbour that can swallow the gap and stay rectangular.

    Candidates are tried in tiers, best first. Merging is only *safe* when the
    host is flush with the gap along one axis, because then their union is
    still a rectangle; those are the "flush" tiers. The tiers below that trade
    a little quality for closing the hole, because an unassigned pocket on the
    drawing is worse than one slightly outsized room.

    A room the client sized is never grown past its request just to close a
    hole: it falls to the overflow tier, and the pocket goes to circulation
    instead. Closing a gap is not a reason to hand someone a bedroom half again
    as big as the one they asked for.
    """
    corridor: list[tuple[Rect, float]] = []  # flush passage - the natural sink
    ideal: list[tuple[Rect, float]] = []  # flush, under area cap, keeps its shape
    stretched: list[tuple[Rect, float]] = []  # flush, under area cap, gets long
    overflow: list[tuple[Rect, float]] = []  # flush, over area cap
    partial: list[tuple[Rect, float]] = []  # not flush - may overlap, needs repair

    for room in rooms:
        wall = room.shared_wall_length(gap, tolerance=0.3)
        if wall <= 0:
            continue

        flush = (abs(room.x - gap.x) < 0.3 and abs(room.x2 - gap.x2) < 0.3) or (
            abs(room.y - gap.y) < 0.3 and abs(room.y2 - gap.y2) < 0.3
        )
        if not flush:
            partial.append((room, wall))
            continue

        if room.type is RoomType.PASSAGE:
            # A corridor may be any length; extending one is always preferable
            # to inflating a habitable room.
            corridor.append((room, wall))
        elif room.area + gap.area > target_ceiling(room, targets):
            overflow.append((room, wall))
        elif _merged_aspect(room, gap) > MAX_ASPECT_RATIO and not _tolerates_length(room.type):
            # Absorbing this strip would turn a bathroom into a corridor.
            stretched.append((room, wall))
        else:
            ideal.append((room, wall))

    if ideal:
        # Prefer habitable rooms over outdoor ones, then the longest shared
        # wall, then whichever has the most headroom under its ceiling.
        host, _ = max(
            ideal,
            key=lambda item: (
                0 if item[0].type in {RoomType.PARKING, RoomType.GARDEN} else 1,
                item[1],
                target_ceiling(item[0], targets) - item[0].area,
            ),
        )
        return _merge(rooms, host, gap)

    # Nothing that wants the space can take it without being pushed past what
    # it was asked to be. Circulation is the designated home for that slack:
    # extend a corridor that already touches the pocket before considering
    # anything more drastic.
    if corridor:
        host, _ = max(corridor, key=lambda item: item[1])
        return _merge(rooms, host, gap)

    # A leftover pocket becomes circulation of its own when it is big enough
    # and squarish enough to read as one. Where the client sized the rooms the
    # bar is lower: the slack between their brief and the plot is *meant* to
    # end up as corridor, and merging adjacent passages tidies the result.
    short, long_ = min(gap.width, gap.height), max(gap.width, gap.height)
    passages = sum(1 for r in rooms if r.type is RoomType.PASSAGE)
    if targets and gap.area >= 20.0 and short >= 3.0 and long_ / short <= 6.0:
        return [*rooms, replace(gap, type=RoomType.PASSAGE, name="Passage")]
    if gap.area >= 45.0 and short >= 5.0 and long_ / short <= 2.6 and passages < 2:
        # Retype as well as rename: absorb_vacated passes a real room here, and
        # keeping its old type would resurrect the very room that was removed.
        return [*rooms, replace(gap, type=RoomType.PASSAGE, name="Passage")]

    if stretched:
        # Give it to whichever room ends up least distorted.
        host, _ = min(stretched, key=lambda item: _merged_aspect(item[0], gap))
        return _merge(rooms, host, gap)

    if overflow:
        # Everything adjoining is already at capacity: give the sliver to the
        # room that is least over its ceiling in relative terms.
        host, _ = min(
            overflow, key=lambda item: item[0].area / target_ceiling(item[0], targets)
        )
        return _merge(rooms, host, gap)

    if partial and not strict:
        # Nothing lines up: stretch the largest neighbour along the touching
        # axis only, accepting that it will be trimmed back if it collides.
        host, _ = max(partial, key=lambda item: item[1])
        if abs(host.x2 - gap.x) < 0.3 or abs(gap.x2 - host.x) < 0.3:
            x1 = min(host.x, gap.x)
            return [
                replace(host, x=x1, width=max(host.x2, gap.x2) - x1) if r is host else r
                for r in rooms
            ]
        y1 = min(host.y, gap.y)
        return [
            replace(host, y=y1, height=max(host.y2, gap.y2) - y1) if r is host else r
            for r in rooms
        ]

    return rooms


def _tolerates_length(room_type: RoomType) -> bool:
    """Rooms where a long, narrow footprint is a legitimate design, not a fault."""
    return room_type in {
        RoomType.LIVING_ROOM,
        RoomType.DINING_ROOM,
        RoomType.PASSAGE,
        RoomType.BALCONY,
        RoomType.GARDEN,
    }


def _merged_aspect(host: Rect, gap: Rect) -> float:
    """Aspect ratio the host would have after swallowing the gap."""
    width = max(host.x2, gap.x2) - min(host.x, gap.x)
    height = max(host.y2, gap.y2) - min(host.y, gap.y)
    short = min(width, height)
    return (max(width, height) / short) if short > 0 else float("inf")


def _merge(rooms: list[Rect], host: Rect, gap: Rect) -> list[Rect]:
    """Grow ``host`` to the bounding box of itself and ``gap``."""
    x1, y1 = min(host.x, gap.x), min(host.y, gap.y)
    merged = replace(
        host,
        x=x1,
        y=y1,
        width=max(host.x2, gap.x2) - x1,
        height=max(host.y2, gap.y2) - y1,
    )
    return [merged if r is host else r for r in rooms]


def cap_room_sizes(rooms: list[Rect], *, targets: Targets | None = None) -> list[Rect]:
    """Shrink rooms that scaling blew out of proportion, in area and in shape.

    The freed strip becomes a gap, which :func:`fill_gaps` then hands to a
    neighbour that is still under its own ceiling - so area moves from a
    150 sq ft toilet to the living room instead of simply vanishing. This is
    what keeps the room proportions balanced across the whole plan.
    """
    book = normalise_targets(targets)
    result: list[Rect] = []
    for room in rooms:
        room = _cap_area(room, book)
        room = _cap_aspect(room, book)
        result.append(room)
    return result


def _cap_area(room: Rect, targets: dict[RoomType, RoomTarget] | None = None) -> Rect:
    ceiling = target_ceiling(room, targets)
    if room.area <= ceiling:
        return room

    floor = min_side(room.type)
    if room.width >= room.height:
        width = max(floor, snap(ceiling / room.height))
        if width < room.width:
            room = replace(room, width=width)
    else:
        height = max(floor, snap(ceiling / room.width))
        if height < room.height:
            room = replace(room, height=height)

    # Still over budget because the short side is already at its minimum:
    # trim that too rather than leave the room oversized.
    if room.area > ceiling * 1.05:
        if room.width >= room.height:
            room = replace(room, height=max(floor, snap(ceiling / room.width)))
        else:
            room = replace(room, width=max(floor, snap(ceiling / room.height)))
    return room


def _cap_aspect(room: Rect, targets: dict[RoomType, RoomTarget] | None = None) -> Rect:
    """Pull ribbon-shaped rooms back toward a usable proportion.

    A 4 ft x 30 ft bathroom has a perfectly reasonable area and is still
    nonsense. Habitable rooms are normally left alone - a long living-dining
    run is a legitimate architectural move - but not once the client has said
    what shape they want: someone who asked for a 14 x 12 bedroom did not ask
    for a 10.5 x 25 one of the same area.
    """
    limit, target = MAX_ASPECT_RATIO, 2.6
    request = (targets or {}).get(room.type)

    if request is not None and request.aspect:
        # Trimming a room to fix its shape throws away the very floor area it
        # was given, so this only steps in for genuine nonsense; the solver
        # corrects ordinary drift by moving a wall, which costs nothing.
        limit = max(2.2, request.aspect * 1.5)
        target = max(1.4, request.aspect)
    elif room.type.is_bedroom or room.type in {RoomType.LIVING_ROOM, RoomType.DINING_ROOM}:
        return room

    if room.aspect <= limit:
        return room

    if room.width >= room.height:
        width = max(min_side(room.type), snap(room.height * target))
        return replace(room, width=min(room.width, width))

    height = max(min_side(room.type), snap(room.width * target))
    return replace(room, height=min(room.height, height))


def fit_to_plot(
    rooms: list[Rect],
    plot_width: float,
    plot_length: float,
    *,
    targets: Targets | None = None,
) -> list[Rect]:
    """Expand edge rooms outward so the built envelope reaches the plot edges."""
    if not rooms:
        return rooms

    book = normalise_targets(targets)
    max_x = max(r.x2 for r in rooms)
    max_y = max(r.y2 for r in rooms)
    result: list[Rect] = []
    for room in rooms:
        updated = room
        # Expand only as far as the room's own size ceiling allows, so closing
        # the envelope never re-inflates a room that cap_room_sizes just trimmed
        # - and never past what the client actually asked that room to be.
        ceiling = target_ceiling(room, book)

        if abs(room.x2 - max_x) < GRID and max_x < plot_width:
            width = snap(min(plot_width - updated.x, ceiling / updated.height))
            if width > updated.width:
                updated = replace(updated, width=width)
        if abs(room.y2 - max_y) < GRID and max_y < plot_length:
            height = snap(min(plot_length - updated.y, ceiling / updated.width))
            if height > updated.height:
                updated = replace(updated, height=height)
        result.append(updated)
    return result
