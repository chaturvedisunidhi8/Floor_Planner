"""Model the windows: one on each external wall long enough to take one.

Windows sit on the plot boundary - the one wall a room shares with the outside.
Parking and garden get none (their doors and openings are shown instead). Each
window is kept clear of the wall corners and of any door on the same wall so
the drawing reads as a real opening rather than a break at the jamb or a
frame wedged against a door swing.
"""

from __future__ import annotations

from app.geometry.models import Plan, Window
from app.geometry.primitives import clear_of_corners
from app.geometry.walls import WALLS
from app.schemas.enums import RoomType

#: Feet of external wall below which a window is not worth cutting.
#: Owned by :data:`app.geometry.walls.WALLS`.
MIN_RUN = WALLS.window_min_run

#: Widest window, in feet, whatever the run. Owned by :data:`app.geometry.walls.WALLS`.
MAX_WIDTH = WALLS.window_width

#: Rooms whose openings are drawn as hatches/swings rather than windows.
_NO_WINDOWS: frozenset[RoomType] = frozenset({RoomType.PARKING, RoomType.GARDEN})

#: Tolerance for "this wall is the plot boundary", in feet.
#: Owned by :data:`app.geometry.walls.WALLS`.
EDGE_TOL = WALLS.edge_tolerance


def model_windows(plan: Plan, *, min_run: float = MIN_RUN) -> list[Window]:
    """A window on each external wall long enough to take one.

    The window starts clear of both wall corners; when a door sits on the same
    wall line the window is slid the least that keeps ``window_door_spacing``,
    staying inside the wall run. Doors on the same external wall are impossible
    on solver plans (external walls are plot boundary, doors need a neighbour),
    but hand-built plans and corner rooms on neighbouring walls are guarded
    anyway.
    """
    doors = _door_blockers(plan)
    windows: list[Window] = []
    for room in plan.rooms:
        if room.type in _NO_WINDOWS:
            continue
        sides = (
            ("left", "vertical", room.x, room.height, room.y, room.x),
            ("right", "vertical", plan.plot_width - room.x2, room.height, room.y, room.x2),
            ("bottom", "horizontal", room.y, room.width, room.x, room.y),
            ("top", "horizontal", plan.plot_length - room.y2, room.width, room.x, room.y2),
        )
        for _side, orientation, distance, run, run_start, wall_at in sides:
            if distance > EDGE_TOL or run < min_run:
                continue
            width = min(MAX_WIDTH, run * 0.45)
            start = clear_of_corners(
                run_start, run_start + run, width, WALLS.window_corner_clearance
            )
            start = _clear_of_doors(
                start,
                width,
                orientation,
                wall_at,
                run_start,
                run_start + run,
                doors,
            )
            if orientation == "vertical":
                windows.append(
                    Window(room.type, wall_at, start, width, orientation)
                )
            else:
                windows.append(
                    Window(room.type, start, wall_at, width, orientation)
                )
    return windows


def _door_blockers(plan: Plan) -> dict[tuple[str, float], list[tuple[float, float]]]:
    """Door intervals per wall line, for the window-to-door spacing rule."""
    blockers: dict[tuple[str, float], list[tuple[float, float]]] = {}
    for door in plan.doors:
        if door.orientation == "vertical":
            line, start, end = door.x, door.y, door.y + door.width
        else:
            line, start, end = door.y, door.x, door.x + door.width
        blockers.setdefault((door.orientation, line), []).append((start, end))
    return blockers


def _clear_of_doors(
    start: float,
    width: float,
    orientation: str,
    wall_at: float,
    lo: float,
    hi: float,
    doors: dict[tuple[str, float], list[tuple[float, float]]],
) -> float:
    """Shift the window clear of doors on the same wall line.

    Each door is either met with ``window_door_spacing`` to spare or ignored;
    the window moves the least that satisfies the closest blocking door, and
    when neither side fits it stays put (best effort - the scorer still flags
    the conflict rather than hiding it).
    """
    for d_start, d_end in doors.get((orientation, wall_at), ()):
        if start >= d_end + WALLS.window_door_spacing:
            continue
        if start + width <= d_start - WALLS.window_door_spacing:
            continue
        right_start = d_end + WALLS.window_door_spacing
        left_start = d_start - WALLS.window_door_spacing - width
        right_ok = right_start >= lo and right_start + width <= hi
        left_ok = left_start >= lo and left_start + width <= hi
        if right_ok and not left_ok:
            start = right_start
        elif left_ok and not right_ok:
            start = left_start
        elif right_ok:
            start = min(right_start, left_start, key=lambda s: abs(s - start))
    return start
