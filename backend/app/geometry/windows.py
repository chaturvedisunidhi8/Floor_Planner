"""Model the windows: one on each external wall long enough to take one.

Windows sit on the plot boundary - the one wall a room shares with the outside.
Parking and garden get none (their doors and openings are shown instead). Each
window is centred on the room's wall run and kept well short of the corners so
the drawing reads as a real opening rather than a break at the jamb.
"""

from __future__ import annotations

from app.geometry.models import Plan, Window
from app.schemas.enums import RoomType

#: Feet of external wall below which a window is not worth cutting.
MIN_RUN = 6.0

#: Widest window, in feet, whatever the run.
MAX_WIDTH = 5.0

#: Rooms whose openings are drawn as hatches/swings rather than windows.
_NO_WINDOWS: frozenset[RoomType] = frozenset({RoomType.PARKING, RoomType.GARDEN})

#: Tolerance for "this wall is the plot boundary", in feet.
EDGE_TOL = 0.1


def model_windows(plan: Plan, *, min_run: float = MIN_RUN) -> list[Window]:
    """A window centred on each external wall long enough to take one."""
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
            mid = run_start + run / 2
            if orientation == "vertical":
                windows.append(
                    Window(room.type, wall_at, mid - width / 2, width, orientation)
                )
            else:
                windows.append(
                    Window(room.type, mid - width / 2, wall_at, width, orientation)
                )
    return windows
