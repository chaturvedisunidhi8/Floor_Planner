"""Modeled plan output - the solver's currency, distinct from drawing `Rect`s.

The legacy pipeline thinks in ``Rect`` objects glued to the plot. The solver
produces a :class:`Plan` - rooms plus the doors, windows and wall segments
that *connect* them - which is what Milestone B renders from. ``Rect`` stays
the interchange format for the rest of the app: :meth:`Room.to_rect` converts
a solver room back into something the renderer and the old validator know.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.geometry.primitives import WALL_TOLERANCE, Rect
from app.geometry.walls import WallModel
from app.schemas.enums import RoomType


@dataclass(frozen=True)
class Room:
    """A placed room in solver output, in feet, on the grid."""

    type: RoomType
    name: str
    x: float
    y: float
    width: float
    height: float

    @property
    def x2(self) -> float:
        return self.x + self.width

    @property
    def y2(self) -> float:
        return self.y + self.height

    @property
    def area(self) -> float:
        return round(self.width * self.height, 2)

    @property
    def short_side(self) -> float:
        return min(self.width, self.height)

    @property
    def long_side(self) -> float:
        return max(self.width, self.height)

    @property
    def aspect(self) -> float:
        return self.long_side / self.short_side if self.short_side > 0 else float("inf")

    def to_rect(self) -> Rect:
        return Rect(self.type, self.name, self.x, self.y, self.width, self.height)

    def shared_wall(
        self, other: Room, tolerance: float = WALL_TOLERANCE
    ) -> tuple[str, float, float, float] | None:
        """The wall the two rooms share.

        Returns ``(orientation, lo, hi, line)`` where ``orientation`` is
        ``"vertical"`` (the wall runs north-south) or ``"horizontal"``, ``lo``/
        ``hi`` bound the shared run along the wall, and ``line`` is the wall's
        coordinate (x for a vertical wall, y for a horizontal one) placed on
        the wall's centreline. Returns ``None`` when the rooms do not meet.

        The tolerance matches the legacy engine's ``WALL_TOLERANCE``: the solver
        leaves a gap between rooms where the wall itself sits, so two rooms
        separated by up to a wall's thickness still share a wall.

        The centreline is independent of argument order: the shared boundary of
        two rooms is one of the four edges, so ``line`` is whichever edge the
        two rooms actually meet on, whether ``self`` is the left, right, bottom
        or top neighbour.
        """
        if abs(self.x2 - other.x) <= tolerance or abs(other.x2 - self.x) <= tolerance:
            line = self.x2 if abs(self.x2 - other.x) <= tolerance else other.x2
            return ("vertical", max(self.y, other.y), min(self.y2, other.y2), line)
        if abs(self.y2 - other.y) <= tolerance or abs(other.y2 - self.y) <= tolerance:
            line = self.y2 if abs(self.y2 - other.y) <= tolerance else other.y2
            return ("horizontal", max(self.x, other.x), min(self.x2, other.x2), line)
        return None

    def shared_wall_length(self, other: Room, tolerance: float = WALL_TOLERANCE) -> float:
        """Length of the wall the two rooms share, 0 when they do not touch."""
        wall = self.shared_wall(other, tolerance)
        return max(0.0, wall[2] - wall[1]) if wall is not None else 0.0

    def is_adjacent(self, other: Room, min_opening: float = 2.5) -> bool:
        """True when a doorway could realistically be cut between the two."""
        return self.shared_wall_length(other) >= min_opening

    def touches_edge(self, plot_width: float, plot_length: float, tolerance: float = 0.1) -> bool:
        return (
            self.x <= tolerance
            or self.y <= tolerance
            or self.x2 >= plot_width - tolerance
            or self.y2 >= plot_length - tolerance
        )


@dataclass(frozen=True)
class Door:
    """A doorway cut between two rooms, sitting on their shared wall.

    ``orientation`` is ``"vertical"`` for a door on a vertical wall (the
    wall runs north-south) or ``"horizontal"`` for one on a horizontal wall.
    """

    room_from: RoomType
    room_to: RoomType
    x: float
    y: float
    width: float = 3.0
    orientation: str = "vertical"
    swing: str = "in"

    @property
    def is_external(self) -> bool:
        return self.room_from is None or self.room_to is None


@dataclass(frozen=True)
class Window:
    """A window in an external wall."""

    room: RoomType
    x: float
    y: float
    width: float = 4.0
    orientation: str = "horizontal"

    @property
    def is_external(self) -> bool:
        return True


@dataclass
class Plan:
    """Everything the solver knows about one arrangement."""

    rooms: list[Room]
    plot_width: float
    plot_length: float
    #: Populated by Milestone B's connectivity/doors/windows passes.
    doors: list[Door] = field(default_factory=list)
    windows: list[Window] = field(default_factory=list)
    #: Populated by Milestone E/F's wall pass, when it has run.
    walls: WallModel | None = None
    #: ``"feasible"`` / ``"infeasible"`` / ``"timeout"``.
    status: str = "feasible"
    #: 0..100 when feasible; ``None`` otherwise.
    quality_score: float | None = None
    #: 0..100 geometry accuracy (see :mod:`app.geometry.accuracy`); ``None``
    #: when the plan was not scored or is not feasible.
    geometry_score: float | None = None
    #: Diagnostics dict; populated only when ``status != "feasible"``.
    infeasibility: dict | None = None

    @property
    def built_up_sqft(self) -> float:
        return round(sum(r.area for r in self.rooms if not r.type.is_outdoor), 1)

    @property
    def room_count(self) -> int:
        return len(self.rooms)

    # --- Milestone E/F area ledger -----------------------------------------
    # ``Rect.area`` (the gross rectangle) is untouched; these live alongside
    # it so callers can choose gross or clear semantics explicitly.
    @property
    def gross_area(self) -> float:
        """Total room gross area - the sum of the solved rectangles."""
        if self.walls is not None:
            return self.walls.gross_area
        return sum(r.area for r in self.rooms)

    @property
    def clear_area(self) -> float:
        """Usable interior after the walls are carved out."""
        if self.walls is not None:
            return self.walls.clear_area
        return sum(r.area for r in self.rooms)

    @property
    def wall_area(self) -> float:
        """Floor area actually occupied by walls."""
        if self.walls is not None:
            return self.walls.wall_area
        return 0.0

    def to_rects(self) -> list[Rect]:
        return [room.to_rect() for room in self.rooms]
