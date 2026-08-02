"""Architectural sanity checks and automatic repair.

Step 4 of the specification demands proper room arrangement, logical
connectivity, consistent wall alignment and balanced proportions. This module
is where those become testable assertions rather than aspirations.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import pairwise

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

logger = get_logger(__name__)

MAX_ASPECT_RATIO = 3.6
MIN_ROOM_AREA = 14.0


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
        """Every room must be reachable from the living room through doorways."""
        indoor = [r for r in rooms if not r.type.is_outdoor]
        if not indoor:
            return

        start = next(
            (i for i, r in enumerate(indoor) if r.type is RoomType.LIVING_ROOM),
            0,
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
                seen.add(neighbour)
                queue.append(neighbour)

        isolated = [indoor[i].name for i in range(len(indoor)) if i not in seen]
        if isolated:
            message = f"Unreachable from the living room: {', '.join(isolated)}"
            # One stranded service room is a blemish; several means the plan
            # does not hold together.
            (errors if len(isolated) > 2 else warnings).append(message)

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
    rooms: list[Rect], plot_width: float, plot_length: float, *, strict: bool = False
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

    for _ in range(4):
        gaps = _find_gaps(rooms, plot_width, plot_length)
        if not gaps:
            break
        for gap in gaps:
            rooms = _absorb_gap(rooms, gap, strict=strict)
    return rooms


def rebalance_room_sizes(rooms: list[Rect], passes: int = 3) -> list[Rect]:
    """Move floor area from oversized rooms to undersized neighbours.

    Capping and gap filling both work one room at a time, so a plan can end up
    technically valid but lopsided - an 80 sq ft living room next to a 290 sq ft
    bedroom. This slides the wall between such a pair, which keeps both
    rectangles intact and cannot create an overlap or a gap because the two
    rooms are flush and the shared edge simply moves.
    """
    result = list(rooms)

    # Phase 1 - pull: rooms below their minimum take area from a fat neighbour.
    for _ in range(passes):
        needy = sorted(
            (r for r in result if r.area < min_area(r.type) * 0.95),
            key=lambda r: min_area(r.type) - r.area,
            reverse=True,
        )
        if not needy:
            break

        moved = False
        for short_room in needy:
            donor = _pick_donor(result, short_room)
            if donor is None:
                continue
            updated = _shift_shared_wall(result, donor, short_room)
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
            (r for r in result if r.area > max_area(r.type)),
            key=lambda r: r.area - max_area(r.type),
            reverse=True,
        )
        if not bloated:
            break

        moved = False
        for fat_room in bloated:
            receiver = _pick_receiver(result, fat_room)
            if receiver is not None:
                updated = _shift_shared_wall(result, fat_room, receiver, cap_donor=True)
                if updated is not None:
                    result = updated
                    moved = True
                    break

            # No neighbour lines up with it. Cut the excess off as a strip and
            # let the ordinary gap logic find a home for it, which copes with
            # partial adjacency where a straight wall shift cannot.
            trimmed, freed = _trim_to_cap(fat_room)
            if trimmed is None or freed is None:
                continue
            result = [trimmed if r is fat_room else r for r in result]
            result = _absorb_gap(result, freed)
            moved = True
            break

        if not moved:
            break

    return result


def _trim_to_cap(room: Rect) -> tuple[Rect | None, Rect | None]:
    """Split a room into (room at its ceiling, the leftover strip)."""
    ceiling = max_area(room.type)
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


def _pick_donor(rooms: list[Rect], needy: Rect) -> Rect | None:
    """The flush neighbour with the most spare area to give."""
    candidates: list[tuple[Rect, float]] = []
    for room in rooms:
        if room is needy or room.shared_wall_length(needy, tolerance=0.3) <= 0:
            continue
        if not _flush(room, needy):
            continue
        spare = room.area - min_area(room.type)
        if spare > 12.0:
            candidates.append((room, spare))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1])[0]


def _pick_receiver(rooms: list[Rect], bloated: Rect) -> Rect | None:
    """The flush neighbour with the most room left under its own ceiling."""
    candidates: list[tuple[Rect, float]] = []
    for room in rooms:
        if room is bloated or room.shared_wall_length(bloated, tolerance=0.3) <= 0:
            continue
        if not _flush(room, bloated):
            continue
        headroom = max_area(room.type) - room.area
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
    rooms: list[Rect], donor: Rect, needy: Rect, *, cap_donor: bool = False
) -> list[Rect] | None:
    """Slide the wall between ``donor`` and ``needy`` toward the donor.

    ``cap_donor`` switches the goal from "bring the receiver up to its minimum"
    to "bring the donor down to its ceiling", which is what the push phase
    needs. Either way both rooms stay rectangular and the pair still covers
    exactly the same footprint, so no gap or overlap can appear.
    """
    vertical = abs(donor.x - needy.x) < 0.3 and abs(donor.x2 - needy.x2) < 0.3
    span = donor.width if vertical else donor.height
    if span <= 0:
        return None

    if cap_donor:
        wanted = (donor.area - max_area(donor.type)) / span
        spare = (max_area(needy.type) - needy.area) / span
    else:
        wanted = (min_area(needy.type) - needy.area) / span
        spare = (donor.area - min_area(donor.type)) / span

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


def absorb_vacated(rooms: list[Rect], vacated: Rect) -> list[Rect]:
    """Hand a removed room's footprint to a neighbour.

    ``vacated`` must not be in ``rooms``. Uses the same tiered rules as gap
    filling, so removing a room can never distort its neighbour into a shape
    the validator would reject.
    """
    return _absorb_gap(rooms, vacated)


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


def _absorb_gap(rooms: list[Rect], gap: Rect, *, strict: bool = False) -> list[Rect]:
    """Extend the neighbour that can swallow the gap and stay rectangular.

    Candidates are tried in tiers, best first. Merging is only *safe* when the
    host is flush with the gap along one axis, because then their union is
    still a rectangle; those are the "flush" tiers. The tiers below that trade
    a little quality for closing the hole, because an unassigned pocket on the
    drawing is worse than one slightly outsized room.
    """
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

        if room.area + gap.area > max_area(room.type):
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
                max_area(item[0].type) - item[0].area,
            ),
        )
        return _merge(rooms, host, gap)

    # A leftover pocket only becomes a room of its own when it is big enough
    # and squarish enough to read as one, and only if the plan is not already
    # peppered with them - a drawing with five rooms labelled "Passage" looks
    # like the generator gave up, so past that point they are merged instead.
    short, long_ = min(gap.width, gap.height), max(gap.width, gap.height)
    passages = sum(1 for r in rooms if r.type is RoomType.PASSAGE)
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
        host, _ = min(overflow, key=lambda item: item[0].area / max_area(item[0].type))
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


def cap_room_sizes(rooms: list[Rect]) -> list[Rect]:
    """Shrink rooms that scaling blew out of proportion, in area and in shape.

    The freed strip becomes a gap, which :func:`fill_gaps` then hands to a
    neighbour that is still under its own ceiling - so area moves from a
    150 sq ft toilet to the living room instead of simply vanishing. This is
    what keeps the room proportions balanced across the whole plan.
    """
    result: list[Rect] = []
    for room in rooms:
        room = _cap_area(room)
        room = _cap_aspect(room)
        result.append(room)
    return result


def _cap_area(room: Rect) -> Rect:
    ceiling = max_area(room.type)
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


def _cap_aspect(room: Rect) -> Rect:
    """Pull ribbon-shaped service rooms back toward a usable proportion.

    A 4 ft x 30 ft bathroom has a perfectly reasonable area and is still
    nonsense. Habitable rooms are left alone - long living-dining runs are a
    legitimate architectural move - but wet and service rooms are not.
    """
    if room.type.is_bedroom or room.type in {RoomType.LIVING_ROOM, RoomType.DINING_ROOM}:
        return room
    if room.aspect <= MAX_ASPECT_RATIO:
        return room

    target = 2.6
    if room.width >= room.height:
        width = max(min_side(room.type), snap(room.height * target))
        return replace(room, width=min(room.width, width))

    height = max(min_side(room.type), snap(room.width * target))
    return replace(room, height=min(room.height, height))


def fit_to_plot(rooms: list[Rect], plot_width: float, plot_length: float) -> list[Rect]:
    """Expand edge rooms outward so the built envelope reaches the plot edges."""
    if not rooms:
        return rooms

    max_x = max(r.x2 for r in rooms)
    max_y = max(r.y2 for r in rooms)
    result: list[Rect] = []
    for room in rooms:
        updated = room
        # Expand only as far as the room's own size ceiling allows, so closing
        # the envelope never re-inflates a room that cap_room_sizes just trimmed.
        ceiling = max_area(room.type)

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
