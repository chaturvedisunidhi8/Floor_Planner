"""Geometric primitives for floor plan layout.

All units are feet. Origin is the plot's bottom-left corner, +x right, +y up.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.schemas.enums import RoomType

#: Everything snaps to this grid. Half a foot keeps walls visually aligned
#: without quantising small service rooms out of existence.
GRID = 0.5

#: Two edges within this distance are treated as the same wall line.
WALL_TOLERANCE = 1.25

#: Minimum sensible short side, by room family.
MIN_SIDE: dict[RoomType, float] = {
    RoomType.ATTACHED_BATHROOM: 4.0,
    RoomType.COMMON_BATHROOM: 4.0,
    RoomType.POOJA_ROOM: 3.5,
    RoomType.STORE_ROOM: 3.5,
    RoomType.UTILITY_ROOM: 3.5,
    RoomType.WASH_AREA: 3.5,
    RoomType.BALCONY: 3.5,
    RoomType.PASSAGE: 3.0,
    RoomType.FOYER: 4.0,
    RoomType.STAIRCASE: 4.0,
    RoomType.KITCHEN: 6.5,
    RoomType.DINING_ROOM: 7.0,
    RoomType.STUDY_ROOM: 7.0,
    RoomType.GARDEN: 4.0,
    RoomType.PARKING: 8.0,
}
DEFAULT_MIN_SIDE = 8.0  # bedrooms and living rooms

#: Upper bound on floor area, by room type. Scaling a template up to a larger
#: plot otherwise produces absurdities - a 150 sq ft toilet, or a bedroom four
#: times the size of the living room. Surplus area is handed back to whichever
#: neighbour is still under its own ceiling, which is what keeps the room
#: proportions balanced across the plan.
MAX_AREA: dict[RoomType, float] = {
    # Wet and service areas
    RoomType.ATTACHED_BATHROOM: 72.0,
    RoomType.COMMON_BATHROOM: 66.0,
    RoomType.POOJA_ROOM: 64.0,
    RoomType.STORE_ROOM: 80.0,
    RoomType.UTILITY_ROOM: 80.0,
    RoomType.WASH_AREA: 80.0,
    RoomType.PASSAGE: 130.0,
    RoomType.FOYER: 100.0,
    RoomType.STAIRCASE: 145.0,
    RoomType.KITCHEN: 180.0,
    # Habitable rooms
    RoomType.MASTER_BEDROOM: 260.0,
    RoomType.GUEST_BEDROOM: 210.0,
    RoomType.CHILDREN_BEDROOM: 210.0,
    RoomType.BEDROOM: 210.0,
    RoomType.STUDY_ROOM: 180.0,
    RoomType.LIVING_ROOM: 400.0,
    RoomType.DINING_ROOM: 220.0,
    # Outdoor
    RoomType.BALCONY: 120.0,
    RoomType.PARKING: 260.0,
    RoomType.GARDEN: 320.0,
}


def max_area(room_type: RoomType) -> float:
    return MAX_AREA.get(room_type, float("inf"))


#: Floor area below which a room stops being usable for its purpose. Guards the
#: habitable rooms against being carved down to nothing when a late requirement
#: has to be squeezed in.
MIN_AREA: dict[RoomType, float] = {
    RoomType.LIVING_ROOM: 130.0,
    RoomType.DINING_ROOM: 90.0,
    RoomType.KITCHEN: 70.0,
    RoomType.MASTER_BEDROOM: 120.0,
    RoomType.GUEST_BEDROOM: 100.0,
    RoomType.CHILDREN_BEDROOM: 100.0,
    RoomType.BEDROOM: 100.0,
    RoomType.STUDY_ROOM: 70.0,
    RoomType.PARKING: 100.0,
}


def min_area(room_type: RoomType) -> float:
    """Smallest sensible area, falling back to a square of the minimum side."""
    return MIN_AREA.get(room_type, min_side(room_type) ** 2)


def snap(value: float, grid: float = GRID) -> float:
    return round(round(value / grid) * grid, 2)


def min_side(room_type: RoomType) -> float:
    return MIN_SIDE.get(room_type, DEFAULT_MIN_SIDE)


@dataclass
class Rect:
    """A placed room."""

    type: RoomType
    name: str
    x: float
    y: float
    width: float
    height: float

    # --- Edges ------------------------------------------------------------
    @property
    def x2(self) -> float:
        return self.x + self.width

    @property
    def y2(self) -> float:
        return self.y + self.height

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    @property
    def aspect(self) -> float:
        short = min(self.width, self.height)
        return (max(self.width, self.height) / short) if short > 0 else float("inf")

    # --- Relations --------------------------------------------------------
    def overlap_area(self, other: Rect) -> float:
        dx = min(self.x2, other.x2) - max(self.x, other.x)
        dy = min(self.y2, other.y2) - max(self.y, other.y)
        return dx * dy if dx > 0 and dy > 0 else 0.0

    def overlaps(self, other: Rect, tolerance: float = 0.05) -> bool:
        return self.overlap_area(other) > tolerance

    def shared_wall_length(self, other: Rect, tolerance: float = WALL_TOLERANCE) -> float:
        """Length of the wall the two rooms share, 0 when they do not touch."""
        if abs(self.x2 - other.x) <= tolerance or abs(other.x2 - self.x) <= tolerance:
            return max(0.0, min(self.y2, other.y2) - max(self.y, other.y))
        if abs(self.y2 - other.y) <= tolerance or abs(other.y2 - self.y) <= tolerance:
            return max(0.0, min(self.x2, other.x2) - max(self.x, other.x))
        return 0.0

    def is_adjacent(self, other: Rect, min_opening: float = 2.5) -> bool:
        """True when a doorway could realistically be cut between the two."""
        return self.shared_wall_length(other) >= min_opening

    # --- Transforms -------------------------------------------------------
    def scaled(self, sx: float, sy: float) -> Rect:
        return replace(
            self,
            x=self.x * sx,
            y=self.y * sy,
            width=self.width * sx,
            height=self.height * sy,
        )

    def mirrored_x(self, plot_width: float) -> Rect:
        return replace(self, x=plot_width - self.x2)

    def mirrored_y(self, plot_length: float) -> Rect:
        return replace(self, y=plot_length - self.y2)

    def rotated(self) -> Rect:
        """Quarter turn about the origin, swapping the axes."""
        return replace(self, x=self.y, y=self.x, width=self.height, height=self.width)

    def snapped(self, grid: float = GRID) -> Rect:
        x, y = snap(self.x, grid), snap(self.y, grid)
        return replace(
            self,
            x=x,
            y=y,
            width=max(grid, snap(self.x2, grid) - x),
            height=max(grid, snap(self.y2, grid) - y),
        )

    def clamped(self, plot_width: float, plot_length: float) -> Rect:
        x = min(max(0.0, self.x), max(0.0, plot_width - self.width))
        y = min(max(0.0, self.y), max(0.0, plot_length - self.height))
        return replace(
            self,
            x=x,
            y=y,
            width=min(self.width, plot_width - x),
            height=min(self.height, plot_length - y),
        )

    def copy(self) -> Rect:
        return replace(self)


def align_walls(rects: list[Rect], tolerance: float = WALL_TOLERANCE) -> list[Rect]:
    """Collapse near-coincident edges onto a single wall line.

    Scaling a template to a different plot leaves edges a few inches apart that
    were flush in the original. Without this pass the render shows hairline
    steps in what should be one straight wall - the single most obvious tell
    that a plan was machine-generated.
    """
    x_lines = _cluster([v for r in rects for v in (r.x, r.x2)], tolerance)
    y_lines = _cluster([v for r in rects for v in (r.y, r.y2)], tolerance)

    aligned: list[Rect] = []
    for rect in rects:
        x1 = _nearest(x_lines, rect.x, tolerance)
        x2 = _nearest(x_lines, rect.x2, tolerance)
        y1 = _nearest(y_lines, rect.y, tolerance)
        y2 = _nearest(y_lines, rect.y2, tolerance)

        # Never let alignment collapse a room to nothing.
        if x2 - x1 < GRID:
            x1, x2 = rect.x, rect.x2
        if y2 - y1 < GRID:
            y1, y2 = rect.y, rect.y2

        aligned.append(replace(rect, x=x1, y=y1, width=x2 - x1, height=y2 - y1))
    return aligned


def _cluster(values: list[float], tolerance: float) -> list[float]:
    """Group nearby coordinates and return one representative per group."""
    if not values:
        return []
    ordered = sorted(values)
    lines: list[float] = []
    group = [ordered[0]]
    for value in ordered[1:]:
        if value - group[0] <= tolerance:
            group.append(value)
        else:
            lines.append(sum(group) / len(group))
            group = [value]
    lines.append(sum(group) / len(group))
    return [snap(line) for line in lines]


def _nearest(lines: list[float], value: float, tolerance: float) -> float:
    if not lines:
        return snap(value)
    best = min(lines, key=lambda line: abs(line - value))
    return best if abs(best - value) <= tolerance else snap(value)


def bounding_coverage(rects: list[Rect], plot_width: float, plot_length: float) -> float:
    """Fraction of the plot covered by rooms (overlaps counted once)."""
    plot_area = plot_width * plot_length
    if plot_area <= 0:
        return 0.0
    total = sum(r.area for r in rects)
    doubled = sum(
        rects[i].overlap_area(rects[j]) for i in range(len(rects)) for j in range(i + 1, len(rects))
    )
    return max(0.0, (total - doubled) / plot_area)
