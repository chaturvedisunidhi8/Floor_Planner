"""Instantiating a template's arrangement at the sizes the client asked for.

Scaling a traced template's rectangles onto a different plot stretches every
room by the same two factors, which is how a 14 x 12 ft bedroom arrives as
10.5 x 25 ft before a single repair pass has run. Worse, the result is a ragged
tiling: the rooms either side of a wall start and stop in different places, so
there is almost no wall left that can be moved without tearing a hole in the
plan. Sizes can be nudged, never fixed.

This module takes the other route. It reads the template as a *slicing tree* -
the recursive sequence of cuts that produces its arrangement - and then rebuilds
that arrangement on the client's plot from their room sizes. What carries over
from the template is the topology: which rooms sit beside which, in what order,
on which side. What comes from the brief is every dimension.

Because each cut divides a box in proportion to the floor area demanded beneath
it, a leaf ends up with exactly ``plot_area * demand / total_demand`` square
feet. Ask for a 168 sq ft master bedroom on a plot that can carry the whole
brief and that is what you get, not 262. The result is also a true slicing
layout, so every internal wall is movable and the passes that follow can adjust
proportions without any of them opening a gap.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from app.core.logging import get_logger
from app.geometry.primitives import GRID, Rect, max_area, min_area, min_side, snap
from app.schemas.enums import RoomType
from app.schemas.requirements import RoomTarget

logger = get_logger(__name__)

#: Two edges this close in the template are the same cut line.
CUT_TOLERANCE = 0.75


@dataclass(eq=False)
class Slice:
    """A node of the slicing tree: either one room, or two boxes side by side."""

    room: Rect | None = None
    #: ``"v"`` puts the children left to right, ``"h"`` bottom to top.
    orientation: str | None = None
    children: list[Slice] = field(default_factory=list)
    #: Floor area wanted below this node, in square feet.
    demand: float = 0.0

    @property
    def is_leaf(self) -> bool:
        return self.room is not None

    def leaves(self) -> list[Slice]:
        if self.is_leaf:
            return [self]
        return [leaf for child in self.children for leaf in child.leaves()]


Demand = Callable[[Rect], float]


def build_tree(rooms: list[Rect], demand: Demand) -> Slice:
    """Decompose the arrangement, keeping the outdoor space on the outside.

    Parking and garden are pushed out to one edge as a single block first,
    because nothing else prevents the cuts from laying them in a band across
    the middle of the plot. A band like that is not merely odd to look at: the
    house either side of it is no longer connected, since you cannot reach the
    bedrooms by walking across the car park.
    """
    outdoor = [room for room in rooms if room.type.is_outdoor]
    indoor = [room for room in rooms if not room.type.is_outdoor]
    if not outdoor or not indoor:
        return decompose(rooms, demand)

    inside = decompose(indoor, demand)
    outside = decompose(outdoor, demand)

    # Put them back on the side of the plan the template had them on.
    spread_x = _spread(outdoor, indoor, axis="x")
    spread_y = _spread(outdoor, indoor, axis="y")
    if abs(spread_x) >= abs(spread_y):
        order = [outside, inside] if spread_x < 0 else [inside, outside]
        return Slice(orientation="v", children=order)
    order = [outside, inside] if spread_y < 0 else [inside, outside]
    return Slice(orientation="h", children=order)


def _spread(outdoor: list[Rect], indoor: list[Rect], axis: str) -> float:
    """How far the outdoor rooms sit from the indoor ones along one axis."""
    index = 0 if axis == "x" else 1
    out = sum(r.center[index] for r in outdoor) / len(outdoor)
    inside = sum(r.center[index] for r in indoor) / len(indoor)
    return out - inside


def decompose(rooms: list[Rect], demand: Demand) -> Slice:
    """Read the arrangement of ``rooms`` as a tree of guillotine cuts.

    Cuts are chosen to put roughly half the demanded floor area on each side.
    That is what keeps the rooms squarish: a box divided near the middle, cut
    the other way next time, halves in each direction alternately and never
    drifts far from square. A cut that peels off one room at a time builds a
    tree as deep as the plan is wide, and each box below it is thinner than the
    last - which is how a bedroom comes out 22 x 5 ft with exactly the area
    that was asked for.

    Traced plans are very nearly guillotine-cuttable, but not always, and the
    straight cuts on offer are not always balanced ones. Where neither holds,
    the split falls back to the median along the wider axis, which keeps the
    left/right (or lower/upper) reading of the arrangement even though it is
    not a true cut.
    """
    if len(rooms) == 1:
        return Slice(room=rooms[0])

    split = _find_cut(rooms, demand)
    if split is None or _lopsided(split, rooms, demand):
        split = _median_split(rooms, demand)

    first, second, orientation = split
    return Slice(
        orientation=orientation,
        children=[decompose(first, demand), decompose(second, demand)],
    )


def _imbalance(before: list[Rect], after: list[Rect], demand: Demand) -> float:
    """How unevenly a split shares out the floor area that has to be fitted."""
    first, second = sum(map(demand, before)), sum(map(demand, after))
    total = first + second
    return abs(first - second) / total if total > 0 else 1.0


def _find_cut(
    rooms: list[Rect], demand: Demand
) -> tuple[list[Rect], list[Rect], str] | None:
    """The straight line that shares the demanded area out most evenly."""
    best: tuple[float, list[Rect], list[Rect], str] | None = None

    for orientation, low, high in (
        ("v", lambda r: r.x, lambda r: r.x2),
        ("h", lambda r: r.y, lambda r: r.y2),
    ):
        for cut in sorted({low(r) for r in rooms} | {high(r) for r in rooms}):
            before = [r for r in rooms if high(r) <= cut + CUT_TOLERANCE]
            after = [r for r in rooms if low(r) >= cut - CUT_TOLERANCE]
            if not before or not after or len(before) + len(after) != len(rooms):
                continue

            imbalance = _imbalance(before, after, demand)
            if best is None or imbalance < best[0]:
                best = (imbalance, before, after, orientation)

    if best is None:
        return None
    return best[1], best[2], best[3]


def _lopsided(
    split: tuple[list[Rect], list[Rect], str], rooms: list[Rect], demand: Demand
) -> bool:
    """True when a cut shares the area out too unevenly to be worth taking."""
    before, after, _ = split
    return len(rooms) >= 4 and _imbalance(before, after, demand) > 0.34


def _median_split(
    rooms: list[Rect], demand: Demand
) -> tuple[list[Rect], list[Rect], str]:
    """Split down the middle of the arrangement, by area rather than by count."""
    spread_x = max(r.center[0] for r in rooms) - min(r.center[0] for r in rooms)
    spread_y = max(r.center[1] for r in rooms) - min(r.center[1] for r in rooms)
    orientation = "v" if spread_x >= spread_y else "h"

    key = (lambda r: r.center[0]) if orientation == "v" else (lambda r: r.center[1])
    ordered = sorted(rooms, key=key)

    # Keep the spatial order, but cut it where the two halves weigh the same.
    at = min(
        range(1, len(ordered)),
        key=lambda i: _imbalance(ordered[:i], ordered[i:], demand),
    )
    logger.debug("No balanced cut across %d rooms; splitting at the median", len(rooms))
    return ordered[:at], ordered[at:], orientation


def assign_demands(
    tree: Slice,
    targets: dict[RoomType, RoomTarget],
    plot_area: float,
    template_area: float,
) -> float:
    """Decide how much floor area every room should get, and total it up.

    Rooms the client sized are given exactly what they asked for. Everything
    else shares what is left over, in the proportions the template used, within
    the bounds that keep a toilet a toilet. Whatever remains after that is
    returned so the caller can turn it into circulation instead of quietly
    inflating the rooms with it.
    """
    leaves = tree.leaves()
    scale = plot_area / template_area if template_area > 0 else 1.0

    sized = [leaf for leaf in leaves if leaf.room and leaf.room.type in targets]
    claimed = {id(leaf) for leaf in sized}
    unsized = [leaf for leaf in leaves if id(leaf) not in claimed]

    requested = 0.0
    for leaf in sized:
        assert leaf.room is not None
        leaf.demand = targets[leaf.room.type].area
        requested += leaf.demand

    if requested > plot_area and requested > 0:
        # The brief does not fit the plot, so everyone gives up the same
        # proportion and the plan stays balanced even though nobody gets what
        # they asked for. The *strict* proportional shortfall is reported by
        # the solver engine, which refuses the brief outright instead; the
        # legacy engine keeps proportional scaling here but without the old
        # extra 15% haircut on top.
        squeeze = plot_area / requested
        for leaf in sized:
            leaf.demand *= squeeze
        requested *= squeeze

    remaining = max(0.0, plot_area - requested)
    _share_remainder(unsized, remaining, scale)

    total = sum(leaf.demand for leaf in leaves)
    _roll_up(tree)
    return max(0.0, plot_area - total)


def _share_remainder(leaves: list[Slice], remaining: float, scale: float) -> None:
    """Split what the sized rooms did not claim between the rest."""
    if not leaves:
        return

    # Start from the template's own proportions, then hold each room inside the
    # band that keeps it usable and stops it swelling into the spare space.
    natural = []
    for leaf in leaves:
        assert leaf.room is not None
        natural.append(leaf.room.area * scale)

    total_natural = sum(natural) or 1.0
    for leaf, want in zip(leaves, natural, strict=True):
        assert leaf.room is not None
        share = remaining * want / total_natural
        leaf.demand = min(
            max(share, min_area(leaf.room.type)), max_area(leaf.room.type)
        )

    # Holding rooms to their bands will have over- or under-spent the remainder;
    # settle the difference among those with room left to move.
    for _ in range(4):
        spent = sum(leaf.demand for leaf in leaves)
        drift = remaining - spent
        if abs(drift) < 1.0:
            break

        movable = [
            leaf
            for leaf in leaves
            if leaf.room
            and (
                leaf.demand < max_area(leaf.room.type) - 0.5
                if drift > 0
                else leaf.demand > min_area(leaf.room.type) + 0.5
            )
        ]
        if not movable:
            break
        for leaf in movable:
            assert leaf.room is not None
            leaf.demand = min(
                max(leaf.demand + drift / len(movable), min_area(leaf.room.type)),
                max_area(leaf.room.type),
            )


def _roll_up(node: Slice) -> float:
    """Total each node's demand from its children."""
    if node.is_leaf:
        return node.demand
    node.demand = sum(_roll_up(child) for child in node.children)
    return node.demand


def add_circulation(tree: Slice, area: float) -> Slice:
    """Put the floor area nobody claimed beside the living room, as corridor.

    Without this the leftovers are shared out among the rooms, which is exactly
    the inflation the client sees when a 110 sq ft bedroom arrives at 192. A
    corridor is what a plan is supposed to do with its spare floor.
    """
    if area < min_area(RoomType.PASSAGE):
        return tree

    hub = next(
        (leaf for leaf in tree.leaves() if leaf.room and leaf.room.type is RoomType.LIVING_ROOM),
        None,
    )
    if hub is None:
        return tree

    corridor = Slice(
        room=Rect(RoomType.PASSAGE, "Passage", 0.0, 0.0, 1.0, 1.0), demand=area
    )
    # Rebuild the hub in place as "living room plus corridor beside it".
    beside = Slice(room=hub.room, demand=hub.demand)
    hub.room = None
    hub.orientation = "h"
    hub.children = [beside, corridor]
    _roll_up(tree)
    return tree


def instantiate(
    tree: Slice,
    x: float,
    y: float,
    width: float,
    height: float,
    targets: dict[RoomType, RoomTarget] | None = None,
) -> list[Rect]:
    """Lay the tree out on a box, dividing it in proportion to demand.

    Area alone would be satisfied by any cut; the room the client pictured
    would not. So each cut is also taken in the direction that leaves the rooms
    beneath it closest to the proportions asked for - a 168 sq ft master
    bedroom comes out near 14 x 12, not 6.5 x 26.
    """
    if tree.is_leaf:
        assert tree.room is not None
        return [replace(tree.room, x=x, y=y, width=width, height=height)]

    memo: dict[tuple[int, float, float], tuple[float, str]] = {}
    return _place(tree, x, y, width, height, targets, memo)


def _place(
    node: Slice,
    x: float,
    y: float,
    width: float,
    height: float,
    targets: dict[RoomType, RoomTarget] | None,
    memo: dict[tuple[int, float, float], tuple[float, str]],
) -> list[Rect]:
    if node.is_leaf:
        assert node.room is not None
        return [replace(node.room, x=x, y=y, width=width, height=height)]

    first, second = node.children
    ratio = _ratio(node)
    _, orientation = _best_cut(node, width, height, targets, memo)

    if orientation == "v":
        cut = _cut_at(x, width, ratio)
        return _place(first, x, y, cut - x, height, targets, memo) + _place(
            second, cut, y, x + width - cut, height, targets, memo
        )

    cut = _cut_at(y, height, ratio)
    return _place(first, x, y, width, cut - y, targets, memo) + _place(
        second, x, cut, width, y + height - cut, targets, memo
    )


def _ratio(node: Slice) -> float:
    total = node.children[0].demand + node.children[1].demand
    return (node.children[0].demand / total) if total > 0 else 0.5


def _best_cut(
    node: Slice,
    width: float,
    height: float,
    targets: dict[RoomType, RoomTarget] | None,
    memo: dict[tuple[int, float, float], tuple[float, str]],
) -> tuple[float, str]:
    """The best direction to cut this box, and what it costs to do so.

    Judging a cut by the two boxes it makes is not enough - a box that looks
    reasonable can still leave a room four cuts further down at 30 x 3.5 ft. So
    each direction is scored by laying the whole subtree out beneath it and
    adding up how far its rooms land from the shapes they were asked to be.
    Results are memoised because the same subtree is asked about the same box
    repeatedly as the search backs up.
    """
    key = (id(node), round(width, 1), round(height, 1))
    cached = memo.get(key)
    if cached is not None:
        return cached

    first, second = node.children
    ratio = _ratio(node)
    scores = {
        "v": _penalty(first, width * ratio, height, targets, memo)
        + _penalty(second, width * (1 - ratio), height, targets, memo),
        "h": _penalty(first, width, height * ratio, targets, memo)
        + _penalty(second, width, height * (1 - ratio), targets, memo),
    }

    # The template's own direction stands unless turning the cut is a clear
    # improvement, so the arrangement still reads like the plan it came from.
    # A node the template never had - one holding a room the brief added - has
    # no direction to defend, so it simply takes the better of the two.
    if node.orientation in scores:
        inherited = node.orientation
        other = "h" if inherited == "v" else "v"
        chosen = inherited if scores[inherited] <= scores[other] + 0.12 else other
    else:
        chosen = min(scores, key=lambda key: scores[key])

    result = (scores[chosen], chosen)
    memo[key] = result
    return result


def _penalty(
    node: Slice,
    width: float,
    height: float,
    targets: dict[RoomType, RoomTarget] | None,
    memo: dict[tuple[int, float, float], tuple[float, str]],
) -> float:
    """How badly a box of this shape suits everything that has to go inside it."""
    if width <= 0 or height <= 0:
        return 100.0
    if node.is_leaf:
        return _leaf_penalty(node, width, height, targets)
    return _best_cut(node, width, height, targets, memo)[0]


def _leaf_penalty(
    node: Slice, width: float, height: float, targets: dict[RoomType, RoomTarget] | None
) -> float:
    """How far one room's box is from the shape it was asked to be."""
    assert node.room is not None
    short, aspect = min(width, height), max(width, height) / min(width, height)

    room_type = node.room.type
    penalty = 0.0
    if short < min_side(room_type):
        # A room too narrow to stand in is a much worse fault than one that is
        # merely the wrong shape, and it is what a bad cut produces first.
        penalty += 6.0 * (min_side(room_type) - short) ** 2

    target = (targets or {}).get(room_type)
    if target is None or not target.aspect:
        # Nobody said what shape this room should be, so any workable one will
        # do. Scoring it against a guess would only spend the search's freedom
        # on rooms that do not care, at the expense of the ones that do.
        return penalty + max(0.0, aspect - 2.5) ** 2

    # Squared, so the search will not accept one 4:1 room in exchange for
    # nudging several others a little closer to square. It is the outlier that
    # makes a plan look machine-made.
    return penalty + (aspect - target.aspect) ** 2


def _cut_at(start: float, extent: float, ratio: float) -> float:
    """Where to put the wall, on the grid and never flush against either side."""
    cut = snap(start + extent * ratio)
    return min(max(cut, start + GRID), start + extent - GRID)
