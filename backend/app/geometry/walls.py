"""Explicit wall model for the solved geometry, built on Shapely.

The CP-SAT solver packs room *gross* rectangles - no wall has a thickness yet.
This module (Milestone E/F) turns those rectangles into a physically
meaningful floor plan:

* every wall is a real polygon with a documented thickness,
* every room gets a *clear* interior boundary (its gross minus the walls),
* the area ledger separates gross area, clear area and wall area.

Concepts
--------
* :class:`PlotBoundary`       - the plot outline ``(0, 0, width, length)``.
* :class:`BuildableBoundary`  - the plot minus setbacks (the solver's envelope).
* room gross                  - the rectangle the solver placed.
* room clear                  - the room's gross polygon minus the walls that
  bound it - the usable floor a furniture layout would actually see.
* :class:`WallSegment`        - one wall band: polygon, kind, the rooms it
  bounds, its thickness and its centreline.
* external wall               - full ``external_wall_thickness`` deep, sitting
  *inside* the plot boundary (the boundary is the wall's outer face).
* internal wall               - ``internal_wall_thickness`` (or the thinner
  ``partition_thickness`` around wet/service rooms) wide, centred on the
  shared room boundary, half of it in each room.

All thicknesses and tolerances live in one place, :class:`WallConfig`. No
other module re-derives a wall pad or tolerance; doors, windows, connectivity
and the shared-wall matching all read from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from shapely import (
    Geometry,
    LineString,
    Polygon,
    box,
    difference,
    intersection,
    make_valid,
    unary_union,
)

from app.schemas.enums import RoomType


@dataclass(frozen=True)
class WallConfig:
    """The single source of truth for every wall dimension in the engine.

    Feet everywhere. The first three are the wall thicknesses; the rest are
    the opening sizes and the tolerances the rest of the engine already used
    as private constants, now centralised so no module can drift.
    """

    #: Structural wall on the plot boundary. The boundary is its outer face.
    external_wall_thickness: float = 0.75
    #: Wall between two habitable rooms, centred on their shared boundary.
    internal_wall_thickness: float = 0.5
    #: Thinner wall around wet/service rooms (bathrooms, stores, utilities).
    partition_thickness: float = 0.4
    #: Standard door leaf, in feet.
    door_width: float = 3.0
    #: Widest window, in feet, whatever the external run.
    window_width: float = 5.0
    #: Numeric tolerance for Shapely geometry comparisons, in feet.
    geometry_tolerance: float = 1e-6
    #: Two edges within this distance are treated as the same wall line.
    wall_tolerance: float = 1.25
    #: A room edge within this of the buildable boundary is "on the boundary".
    edge_tolerance: float = 0.1
    #: Shared wall run below which a doorway is not worth cutting.
    min_opening: float = 2.5
    #: External wall run below which a window is not worth cutting.
    window_min_run: float = 6.0
    #: Feet a door must stay away from the end of its wall run.
    door_corner_clearance: float = 1.5
    #: Minimum gap between two doors on the same wall.
    door_spacing: float = 3.0
    #: Feet a window must stay away from the end of its wall run.
    window_corner_clearance: float = 1.0
    #: Minimum gap between a window and a door on the same wall.
    window_door_spacing: float = 3.0


#: The engine-wide wall configuration. Modules import this instead of defining
#: their own pad/tolerance constants.
WALLS = WallConfig()

#: Rooms bounded by thin partitions rather than full interior walls.
_PARTITION_TYPES: frozenset[RoomType] = frozenset(
    {
        RoomType.ATTACHED_BATHROOM,
        RoomType.COMMON_BATHROOM,
        RoomType.STORE_ROOM,
        RoomType.UTILITY_ROOM,
        RoomType.WASH_AREA,
    }
)


@dataclass
class PlotBoundary:
    """The plot outline - the outer face of the external walls."""

    width: float
    length: float
    polygon: Polygon = field(init=False)

    def __post_init__(self) -> None:
        self.polygon = box(0.0, 0.0, self.width, self.length)

    @property
    def area(self) -> float:
        return self.width * self.length


@dataclass
class BuildableBoundary:
    """The plot minus setbacks - the solver's envelope, in feet."""

    width: float
    length: float
    polygon: Polygon = field(init=False)

    def __post_init__(self) -> None:
        self.polygon = box(0.0, 0.0, self.width, self.length)

    @property
    def area(self) -> float:
        return self.width * self.length


@dataclass(frozen=True)
class WallSegment:
    """One wall band as a real polygon.

    ``kind`` is ``"external"`` (on the plot boundary) or ``"internal"`` (on a
    shared room boundary). ``rooms``/``room_indices`` say which rooms the wall
    bounds - one room for an external wall, two for an internal one.
    """

    kind: Literal["external", "internal"]
    polygon: Polygon
    line: LineString
    thickness: float
    rooms: tuple[str, ...] = ()
    room_indices: tuple[int, ...] = ()


@dataclass
class WallModel:
    """Everything the wall pass knows about one plan.

    The area ledger is the point of Milestone E/F: ``gross_area`` is the sum of
    the solved rectangles, ``wall_area`` the area actually occupied by walls,
    and ``clear_area`` the usable interior left over. They reconcile exactly
    (within ``config.geometry_tolerance``)::

        gross_area == clear_area + wall_area
    """

    config: WallConfig
    plot_boundary: PlotBoundary
    buildable_boundary: BuildableBoundary
    external_walls: Geometry | None
    internal_walls: Geometry | None
    segments: list[WallSegment]
    room_gross: list[Polygon]
    #: Room index -> clear interior polygon.
    clear_polygons: dict[int, Polygon]
    gross_area: float
    clear_area: float
    wall_area: float
    external_wall_area: float
    internal_wall_area: float
    #: Plot area the rooms do not occupy at all (gaps / future circulation).
    uncovered_area: float
    uncovered_fraction: float


def _room_name(room, index: int) -> str:
    return getattr(room, "name", None) or f"Room {index + 1}"


def _room_polygon(room) -> Polygon:
    return box(room.x, room.y, room.x + room.width, room.y + room.height)


def _uses_partition(room) -> bool:
    return getattr(room, "type", None) in _PARTITION_TYPES


def _shared_wall_segments(rooms, config: WallConfig) -> list[WallSegment]:
    """The internal walls: one band per shared room boundary.

    A band is centred on the shared line - ``half = thickness / 2`` into each
    room - and spans the run the two rooms actually share. Rooms placed within
    ``wall_tolerance`` of each other count as sharing a wall, but the band is
    clipped to the two rooms' union so a near-miss gap never leaves a wall
    floating in open space. T-junctions and corners are handled by the caller's
    union; each pair contributes exactly one band.
    """
    segments: list[WallSegment] = []
    tol = config.wall_tolerance
    geo = config.geometry_tolerance
    polygons = [_room_polygon(room) for room in rooms]

    def clip_band(band: Geometry, i: int, j: int) -> Polygon | None:
        clipped = make_valid(intersection(band, unary_union([polygons[i], polygons[j]])))
        if clipped.is_empty:
            return None
        if clipped.geom_type == "MultiPolygon":
            clipped = max(clipped.geoms, key=lambda g: g.area)
        return Polygon(clipped.exterior)

    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            a, b = rooms[i], rooms[j]
            a_x2, a_y2 = a.x + a.width, a.y + a.height
            b_x2, b_y2 = b.x + b.width, b.y + b.height
            thickness = (
                config.partition_thickness
                if _uses_partition(a) or _uses_partition(b)
                else config.internal_wall_thickness
            )
            half = thickness / 2

            if abs(a_x2 - b.x) <= tol or abs(b_x2 - a.x) <= tol:
                line_x = a_x2 if abs(a_x2 - b.x) <= tol else b_x2
                lo, hi = max(a.y, b.y), min(a_y2, b_y2)
                if hi - lo > geo:
                    polygon = clip_band(box(line_x - half, lo, line_x + half, hi), i, j)
                    if polygon is not None:
                        segments.append(
                            WallSegment(
                                "internal",
                                polygon,
                                LineString([(line_x, lo), (line_x, hi)]),
                                thickness,
                                (_room_name(a, i), _room_name(b, j)),
                                (i, j),
                            )
                        )
            elif abs(a_y2 - b.y) <= tol or abs(b_y2 - a.y) <= tol:
                line_y = a_y2 if abs(a_y2 - b.y) <= tol else b_y2
                lo, hi = max(a.x, b.x), min(a_x2, b_x2)
                if hi - lo > geo:
                    polygon = clip_band(box(lo, line_y - half, hi, line_y + half), i, j)
                    if polygon is not None:
                        segments.append(
                            WallSegment(
                                "internal",
                                polygon,
                                LineString([(lo, line_y), (hi, line_y)]),
                                thickness,
                                (_room_name(a, i), _room_name(b, j)),
                                (i, j),
                            )
                        )
    return segments


def _external_wall_segments(
    rooms, build_w: float, build_l: float, config: WallConfig
) -> list[WallSegment]:
    """The external walls: a full-thickness band on the buildable boundary.

    The boundary is the wall's outer face, so the band runs from the boundary
    *inward* by ``external_wall_thickness``. Only room edges that actually lie
    on the buildable boundary produce one - an interior room has none.
    """
    segments: list[WallSegment] = []
    tol = config.edge_tolerance
    geo = config.geometry_tolerance
    thickness = config.external_wall_thickness

    for i, room in enumerate(rooms):
        x, y = room.x, room.y
        w, h = room.width, room.height
        sides = (
            (
                y <= tol,
                "bottom",
                LineString([(x, 0.0), (x + w, 0.0)]),
                box(x, 0.0, x + w, thickness),
                w,
            ),
            (
                x <= tol,
                "left",
                LineString([(0.0, y), (0.0, y + h)]),
                box(0.0, y, thickness, y + h),
                h,
            ),
            (
                x + w >= build_w - tol,
                "right",
                LineString([(build_w, y), (build_w, y + h)]),
                box(build_w - thickness, y, build_w, y + h),
                h,
            ),
            (
                y + h >= build_l - tol,
                "top",
                LineString([(x, build_l), (x + w, build_l)]),
                box(x, build_l - thickness, x + w, build_l),
                w,
            ),
        )
        for on_edge, _side, line, band, run in sides:
            if not on_edge or run <= geo:
                continue
            segments.append(
                WallSegment(
                    "external",
                    make_valid(band),
                    line,
                    thickness,
                    (_room_name(room, i),),
                    (i,),
                )
            )
    return segments


def build_wall_model(
    rooms,
    *,
    plot_width: float,
    plot_length: float,
    envelope=None,
    config: WallConfig | None = None,
) -> WallModel:
    """Turn solved room rectangles into a full wall model.

    ``rooms`` is any sequence of rectangles exposing ``x``, ``y``, ``width``,
    ``height`` and (optionally) ``name``/``type`` - both ``Room`` and ``Rect``
    satisfy it, so the solver plan and the legacy plan share one wall pass.
    ``envelope`` (the buildable box) is optional; without it the plot is the
    buildable area.
    """
    config = config or WALLS
    build_w = envelope.buildable_width if envelope is not None else plot_width
    build_l = envelope.buildable_length if envelope is not None else plot_length

    plot = PlotBoundary(plot_width, plot_length)
    buildable = BuildableBoundary(build_w, build_l)

    room_gross = [make_valid(_room_polygon(room)) for room in rooms]

    segments = [
        *_shared_wall_segments(rooms, config),
        *_external_wall_segments(rooms, build_w, build_l, config),
    ]

    internal_union = unary_union([s.polygon for s in segments if s.kind == "internal"]) or None
    external_union = unary_union([s.polygon for s in segments if s.kind == "external"]) or None
    walls_union = unary_union([s.polygon for s in segments]) or None

    clear_polygons: dict[int, Polygon] = {}
    total_clear = 0.0
    for index, gross in enumerate(room_gross):
        clear = gross if walls_union is None else make_valid(difference(gross, walls_union))
        clear_polygons[index] = clear
        total_clear += clear.area

    gross_area = sum(g.area for g in room_gross)
    external_wall_area = external_union.area if external_union is not None else 0.0
    wall_area = walls_union.area if walls_union is not None else 0.0
    # The split reconciles exactly with ``wall_area``: where an external and an
    # internal wall meet at a corner the overlap is counted only once.
    internal_wall_area = max(0.0, wall_area - external_wall_area)
    plot_area = build_w * build_l
    uncovered_area = max(0.0, plot_area - gross_area)

    return WallModel(
        config=config,
        plot_boundary=plot,
        buildable_boundary=buildable,
        external_walls=external_union,
        internal_walls=internal_union,
        segments=segments,
        room_gross=room_gross,
        clear_polygons=clear_polygons,
        gross_area=gross_area,
        clear_area=total_clear,
        wall_area=wall_area,
        external_wall_area=external_wall_area,
        internal_wall_area=internal_wall_area,
        uncovered_area=uncovered_area,
        uncovered_fraction=uncovered_area / plot_area if plot_area > 0 else 0.0,
    )


def validate_walls(model: WallModel) -> list[str]:
    """Strict checks on the generated wall polygons.

    Returns a list of problems, empty when the walls are sound. This is a
    modelling gate on the *new* wall geometry: the rooms themselves are already
    validated elsewhere. A wall must be a valid, non-empty polygon inside the
    plot, and the area ledger must reconcile.
    """
    errors: list[str] = []
    config = model.config
    tol = config.geometry_tolerance
    plot = model.plot_boundary.polygon

    for index, segment in enumerate(model.segments):
        poly = segment.polygon
        if poly.is_empty:
            errors.append(f"wall segment {index + 1} is empty")
        elif not poly.is_valid:
            errors.append(f"wall segment {index + 1} is geometrically invalid")
        elif poly.area <= tol:
            errors.append(f"wall segment {index + 1} has no measurable area")
        if not plot.covers(poly):
            errors.append(f"wall segment {index + 1} extends outside the plot")

    if model.gross_area <= 0:
        errors.append("no room gross area to build walls on")
        return errors

    if model.clear_area <= tol:
        errors.append("no clear interior remains after the walls")

    ledger = abs((model.clear_area + model.wall_area) - model.gross_area)
    tolerance = max(1.0, model.gross_area) * 1e-3
    if ledger > tolerance:
        errors.append(
            f"wall/clear/gross areas do not reconcile (off by {ledger:.4f} sq ft)"
        )

    for index, clear in model.clear_polygons.items():
        if clear.is_empty or clear.area <= tol:
            errors.append(f"room {index + 1} has no clear interior after its walls")
            continue
        gross = model.room_gross[index]
        if clear.area > gross.area + max(1.0, gross.area) * 1e-3:
            errors.append(f"room {index + 1} clear area exceeds its gross area")

    return errors


__all__ = [
    "WALLS",
    "BuildableBoundary",
    "PlotBoundary",
    "WallConfig",
    "WallModel",
    "WallSegment",
    "build_wall_model",
    "validate_walls",
]
