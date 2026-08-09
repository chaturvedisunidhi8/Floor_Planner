"""Vastu Shastra compliance: scoring a layout, and orienting it to comply.

Every plan this project draws is north-up - the renderer's compass points along
+y - so the plot's compass zones are fixed:

    +y is north, +x is east, and the origin corner is the south-west.

That makes each principle a statement about where a room's centre should sit
inside the unit square, which is all :func:`score` needs. Compliance is a
gradient rather than a yes/no: a kitchen two feet off the south-east corner has
not failed, and telling the client it has would be worse than useless.

:func:`orient` is how the preference reaches the drawing. It reflects the
finished plan about its own axes and keeps whichever of the four results scores
best. Reflection is used deliberately in preference to moving rooms: it is
exact, so a layout that the engine spent five repair passes making valid cannot
be made invalid again by asking for Vastu.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.geometry.primitives import Rect, min_side
from app.schemas.enums import RoomType, VastuPrinciple

#: Which end of each axis a zone pulls towards, as (east, north). 1.0 means the
#: high side of the axis - the east edge, the north edge - and 0.0 the low one.
ZONE_TARGETS: dict[str, tuple[float, float]] = {
    "north_east": (1.0, 1.0),
    "south_east": (1.0, 0.0),
    "south_west": (0.0, 0.0),
    "north_west": (0.0, 1.0),
}


@dataclass(frozen=True)
class _Rule:
    """One principle: the rooms it governs and the zone it wants them in."""

    zone: str
    rooms: frozenset[RoomType]


RULES: dict[VastuPrinciple, _Rule] = {
    VastuPrinciple.POOJA_NORTHEAST: _Rule("north_east", frozenset({RoomType.POOJA_ROOM})),
    VastuPrinciple.KITCHEN_SOUTHEAST: _Rule("south_east", frozenset({RoomType.KITCHEN})),
    VastuPrinciple.MASTER_BEDROOM_SOUTHWEST: _Rule(
        "south_west", frozenset({RoomType.MASTER_BEDROOM})
    ),
    VastuPrinciple.LIVING_NORTHEAST: _Rule("north_east", frozenset({RoomType.LIVING_ROOM})),
    VastuPrinciple.BATHROOMS_NORTHWEST: _Rule(
        "north_west", frozenset({RoomType.ATTACHED_BATHROOM, RoomType.COMMON_BATHROOM})
    ),
    VastuPrinciple.STORAGE_SOUTHWEST: _Rule(
        "south_west", frozenset({RoomType.STORE_ROOM, RoomType.UTILITY_ROOM})
    ),
}

#: Below this a room is not in its zone in any meaningful sense, and saying it
#: complies would be flattery rather than information.
SATISFIED_AT = 0.62

#: Service rooms that no adjacency pins down. Two of these can trade places
#: without changing how the home works - a store room does not care what it
#: sits next to, where a kitchen or an en-suite very much does.
SWAPPABLE: frozenset[RoomType] = frozenset(
    {
        RoomType.POOJA_ROOM,
        RoomType.STORE_ROOM,
        RoomType.UTILITY_ROOM,
        RoomType.WASH_AREA,
        RoomType.COMMON_BATHROOM,
    }
)

#: How different two rooms' footprints may be and still be swapped. Wider than
#: this and the plan gains a prayer room the size of a bedroom.
_SWAP_AREA_TOLERANCE = 0.45


@dataclass(frozen=True)
class VastuReport:
    """How well one layout follows the principles that were asked for."""

    score: float
    satisfied: list[VastuPrinciple]
    unmet: list[VastuPrinciple]

    @property
    def notes(self) -> list[str]:
        """Human-readable lines for the gallery card."""
        lines = [f"{p.label} ✓" for p in self.satisfied]
        lines += [f"{p.label} - not achieved in this layout" for p in self.unmet]
        return lines


def score(
    rooms: list[Rect],
    plot_width: float,
    plot_length: float,
    principles: list[VastuPrinciple] | tuple[VastuPrinciple, ...],
) -> VastuReport:
    """Grade a layout against the requested principles.

    Principles whose rooms are not in the plan are skipped rather than failed:
    a brief with no pooja room cannot break the rule about where one goes.
    """
    scores: list[float] = []
    satisfied: list[VastuPrinciple] = []
    unmet: list[VastuPrinciple] = []

    for principle in principles:
        rule = RULES.get(principle)
        if rule is None:
            continue
        governed = [room for room in rooms if room.type in rule.rooms]
        if not governed:
            continue

        # Worst room decides: two bathrooms only follow the rule if both do.
        value = min(zone_score(room, plot_width, plot_length, rule.zone) for room in governed)
        scores.append(value)
        (satisfied if value >= SATISFIED_AT else unmet).append(principle)

    overall = round(sum(scores) / len(scores), 4) if scores else 0.0
    return VastuReport(score=overall, satisfied=satisfied, unmet=unmet)


def orient(
    rooms: list[Rect],
    plot_width: float,
    plot_length: float,
    principles: list[VastuPrinciple] | tuple[VastuPrinciple, ...],
) -> tuple[list[Rect], VastuReport]:
    """Return the reflection of ``rooms`` that best follows the principles.

    The four candidates are the plan as drawn, mirrored east-west, mirrored
    north-south, and both. Ties keep the earlier candidate, so a plan that
    already complies is never flipped for nothing.
    """
    best = rooms
    best_report = score(rooms, plot_width, plot_length, principles)

    for flip_x, flip_y in ((True, False), (False, True), (True, True)):
        candidate = list(rooms)
        if flip_x:
            candidate = [r.mirrored_x(plot_width) for r in candidate]
        if flip_y:
            candidate = [r.mirrored_y(plot_length) for r in candidate]

        report = score(candidate, plot_width, plot_length, principles)
        if report.score > best_report.score:
            best, best_report = candidate, report

    return best, best_report


def comply(
    rooms: list[Rect],
    plot_width: float,
    plot_length: float,
    principles: list[VastuPrinciple] | tuple[VastuPrinciple, ...],
) -> tuple[list[Rect], VastuReport]:
    """Do what can be done for compliance without redrawing the plan.

    Reflect the plan into its best orientation, trade any service rooms that
    would each be better off in the other's place, then reflect again now that
    the programme has moved. Both operations leave the geometry itself
    untouched, so the result is exactly as valid as the plan that came in.
    """
    rooms, report = orient(rooms, plot_width, plot_length, principles)
    swapped = _swap_service_rooms(rooms, plot_width, plot_length, principles)
    if swapped is rooms:
        return rooms, report
    return orient(swapped, plot_width, plot_length, principles)


def _swap_service_rooms(
    rooms: list[Rect],
    plot_width: float,
    plot_length: float,
    principles: list[VastuPrinciple] | tuple[VastuPrinciple, ...],
) -> list[Rect]:
    """Exchange pairs of loose service rooms while that improves compliance.

    Greedy and deterministic: the best single swap is taken, then the search
    repeats on the result. Only labels move - the rectangles stay exactly where
    the engine put them - so a swap can neither overlap two rooms nor open a
    gap.
    """
    current = rooms
    best_score = score(current, plot_width, plot_length, principles).score

    # One pass per swappable room is more than enough to settle; the bound is
    # here so a scoring quirk can never turn this into an endless trade.
    for _ in range(len(SWAPPABLE)):
        best_pair: tuple[list[Rect], float] | None = None
        for i, a in enumerate(current):
            if a.type not in SWAPPABLE:
                continue
            for b in current[i + 1 :]:
                if b.type not in SWAPPABLE or b.type is a.type or not _swappable(a, b):
                    continue
                candidate = _with_types_swapped(current, a, b)
                value = score(candidate, plot_width, plot_length, principles).score
                if value > best_score + 1e-6 and (
                    best_pair is None or value > best_pair[1]
                ):
                    best_pair = (candidate, value)

        if best_pair is None:
            return current
        current, best_score = best_pair

    return current


def _swappable(a: Rect, b: Rect) -> bool:
    """Can these two rooms trade labels and both still be usable?"""
    larger = max(a.area, b.area)
    if larger <= 0 or abs(a.area - b.area) / larger > _SWAP_AREA_TOLERANCE:
        return False
    return min(a.width, a.height) >= min_side(b.type) and min(b.width, b.height) >= min_side(a.type)


def _with_types_swapped(rooms: list[Rect], a: Rect, b: Rect) -> list[Rect]:
    exchanged = {
        id(a): replace(a, type=b.type, name=b.name),
        id(b): replace(b, type=a.type, name=a.name),
    }
    return [exchanged.get(id(room), room) for room in rooms]


def describe_zone(room: Rect, plot_width: float, plot_length: float) -> str:
    """Which compass zone a room actually landed in, e.g. ``north-east``."""
    east, north = _normalised_centre(room, plot_width, plot_length)
    vertical = "north" if north >= 0.5 else "south"
    horizontal = "east" if east >= 0.5 else "west"
    return f"{vertical}-{horizontal}"


def zone_score(room: Rect, plot_width: float, plot_length: float, zone: str) -> float:
    """How far into its zone the room sits, 1.0 being as far as it can go.

    Measured against the positions a room *of this size* could occupy rather
    than against the bare corner, because a 400 sq ft living room can never
    have its centre where a 40 sq ft prayer room can. Scoring against the
    corner itself would mark every large room non-compliant however far into
    the north-east it was pushed.
    """
    east, north = ZONE_TARGETS[zone]
    ideal_x, worst_x = _extremes(room.width, plot_width, east)
    ideal_y, worst_y = _extremes(room.height, plot_length, north)

    span = ((ideal_x - worst_x) ** 2 + (ideal_y - worst_y) ** 2) ** 0.5
    if span <= 0:
        # A room spanning the whole plot is in every zone at once.
        return 1.0

    cx, cy = room.center
    offset = ((cx - ideal_x) ** 2 + (cy - ideal_y) ** 2) ** 0.5
    return round(max(0.0, 1.0 - offset / span), 4)


def _extremes(side: float, plot_side: float, towards_high: float) -> tuple[float, float]:
    """The best and worst centre coordinates for a room of this size on one axis."""
    low = side / 2
    high = max(low, plot_side - side / 2)
    return (high, low) if towards_high >= 0.5 else (low, high)


def _normalised_centre(room: Rect, plot_width: float, plot_length: float) -> tuple[float, float]:
    """The room's centre as (east, north) fractions of the plot."""
    cx, cy = room.center
    east = cx / plot_width if plot_width > 0 else 0.5
    north = cy / plot_length if plot_length > 0 else 0.5
    return (min(max(east, 0.0), 1.0), min(max(north, 0.0), 1.0))


__all__ = [
    "RULES",
    "SATISFIED_AT",
    "SWAPPABLE",
    "ZONE_TARGETS",
    "VastuReport",
    "comply",
    "describe_zone",
    "orient",
    "score",
    "zone_score",
]
