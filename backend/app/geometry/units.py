"""Central constants and coordinate conversions for the geometry engine.

Human-facing units are feet; the CP-SAT solver works in integer *cells* of
:data:`UNIT` feet so its interval variables stay exact. Every conversion
between the two happens here and only here.

The sizing tables that used to live in :mod:`app.geometry.primitives` moved
here so the solver, the legacy pipeline and the validator all read the same
numbers. ``primitives`` re-exports them, so existing imports keep working.
"""

from __future__ import annotations

from app.schemas.enums import RoomType

#: One grid cell, in feet. Everything snaps to this half-foot.
UNIT = 0.5

#: Feet per cell - readable name for the same number as :data:`UNIT`.
FEET_PER_CELL = UNIT
CELLS_PER_FOOT = 1.0 / UNIT

#: Absolute upper bound on one axis, in cells. 100 ft of plot at half a foot
#: per cell; anything larger is outside the brief's own limits already.
MAX_EXTENT_CELLS = 200

#: Two edges within this distance are treated as the same wall line.
WALL_TOLERANCE = 1.25

#: A room at or above this length-to-short-side ratio reads as a corridor.
MAX_ASPECT_RATIO = 3.6

#: Everything snaps to this. Half a foot keeps walls visually aligned without
#: quantising small service rooms out of existence.
GRID = UNIT

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
#: times the size of the living room.
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

#: Floor area below which a room stops being usable for its purpose.
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


# --- Conversions -----------------------------------------------------------

def to_cells(feet: float) -> int:
    """Nearest whole number of cells for a length in feet."""
    return round(feet / UNIT)


def to_ft(cells: int) -> float:
    """Feet for a whole number of cells - exact on the grid."""
    return round(cells * UNIT, 2)


def area_to_cells(area_ft: float) -> int:
    """Cells^2 for an area in square feet."""
    return round(area_ft / (UNIT * UNIT))


def cells_area_to_ft(area_cells: int) -> float:
    """Square feet for a whole number of cells^2 - exact on the grid."""
    return round(area_cells * UNIT * UNIT, 2)


# --- Sizing helpers ----------------------------------------------------------

def snap(value: float, grid: float = GRID) -> float:
    return round(round(value / grid) * grid, 2)


def min_side(room_type: RoomType) -> float:
    return MIN_SIDE.get(room_type, DEFAULT_MIN_SIDE)


def max_area(room_type: RoomType) -> float:
    return MAX_AREA.get(room_type, float("inf"))


def min_area(room_type: RoomType) -> float:
    """Smallest sensible area, falling back to a square of the minimum side."""
    return MIN_AREA.get(room_type, min_side(room_type) ** 2)


def natural_area(room_type: RoomType) -> float:
    """A room's unremarkable size - what to build when nobody said otherwise."""
    return (min_area(room_type) * max_area(room_type)) ** 0.5
