"""Tests for the architectural-quality measurement layer.

The milestone's bet is that the engine can *measure* the difference between a
packed rectangle arrangement and a house an architect would sign: corridor
fragmentation, door placement, wet-zone layout, private-zone clustering,
cross-ventilation and wasted space. These tests pin the measurements on
hand-built plans so the numbers are unambiguous.

Each fixture is a deliberately *bad* or *good* arrangement of the same rooms;
the metrics must separate them, and the values must be exact.
"""

from __future__ import annotations

from app.geometry.models import Door, Plan, Room, Window
from app.geometry.quality import CORRIDOR_MIN_WIDTH, quality_metrics
from app.schemas.enums import RoomType


def _plan(
    rooms: list[Room],
    *,
    doors=None,
    windows=None,
    width: float = 30,
    length: float = 45,
) -> Plan:
    plan = Plan(rooms=rooms, plot_width=width, plot_length=length)
    plan.doors = doors or []
    plan.windows = windows or []
    return plan


def _room(room_type: RoomType, x, y, w, h, name: str | None = None) -> Room:
    return Room(room_type, name or room_type.label, x, y, w, h)


# --- corridor ----------------------------------------------------------------

def test_fragmented_corridor_counts_two_bands() -> None:
    rooms = [
        _room(RoomType.PASSAGE, 0, 0, 6, 5),
        _room(RoomType.PASSAGE, 12, 0, 6, 5),
        _room(RoomType.LIVING_ROOM, 0, 8, 18, 12),
    ]
    metrics = quality_metrics(_plan(rooms))
    assert metrics.corridor_rooms == 2
    assert metrics.corridor_band_count == 2
    assert metrics.corridor_fragmentation == 1.0
    assert metrics.corridor_spine_ratio == 1.0  # same band, just disconnected


def test_connected_spine_is_not_fragmented() -> None:
    rooms = [
        _room(RoomType.PASSAGE, 0, 0, 8, 5),
        _room(RoomType.PASSAGE, 8, 0, 8, 5),
        _room(RoomType.LIVING_ROOM, 0, 8, 16, 12),
    ]
    metrics = quality_metrics(_plan(rooms))
    assert metrics.corridor_band_count == 1
    assert metrics.corridor_fragmentation == 0.0
    assert metrics.corridor_spine_ratio == 1.0
    assert metrics.corridor_min_width == 5.0


def test_stacked_corridor_rooms_are_not_on_one_spine() -> None:
    rooms = [
        _room(RoomType.PASSAGE, 0, 0, 5, 10),   # a vertical column
        _room(RoomType.PASSAGE, 10, 5, 8, 5),   # a perpendicular band
    ]
    metrics = quality_metrics(_plan(rooms))
    assert metrics.corridor_spine_ratio == 0.0


def test_fragments_in_one_column_are_aligned_but_disconnected() -> None:
    rooms = [
        _room(RoomType.PASSAGE, 0, 0, 5, 10),
        _room(RoomType.PASSAGE, 0, 15, 5, 10),
    ]
    metrics = quality_metrics(_plan(rooms))
    assert metrics.corridor_spine_ratio == 1.0  # collinear...
    assert metrics.corridor_fragmentation == 1.0  # ...but not connected


def test_corridor_min_width_flags_a_slot() -> None:
    rooms = [
        _room(RoomType.PASSAGE, 0, 0, 3.5, 10),
    ]
    metrics = quality_metrics(_plan(rooms))
    assert metrics.corridor_min_width < CORRIDOR_MIN_WIDTH


def test_plan_without_corridor_leaves_corridor_axes_unmeasured() -> None:
    metrics = quality_metrics(_plan([_room(RoomType.LIVING_ROOM, 0, 0, 20, 20)]))
    assert metrics.corridor_rooms == 0
    assert metrics.corridor_fragmentation is None
    assert metrics.corridor_spine_ratio is None
    assert metrics.corridor_min_width is None


# --- doors -------------------------------------------------------------------

def _two_rooms_shared_wall() -> tuple[list[Room], float]:
    living = _room(RoomType.LIVING_ROOM, 0, 0, 10, 10)
    kitchen = _room(RoomType.KITCHEN, 10, 0, 10, 10)
    return [living, kitchen], 10.0  # shared wall at x=10, run [0, 10]


def test_door_too_close_to_a_corner_is_counted() -> None:
    rooms, line = _two_rooms_shared_wall()
    door = Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, line, 0.2, 3.0, "vertical")
    metrics = quality_metrics(_plan(rooms, doors=[door]))
    assert metrics.door_corner_violations == 1


def test_centred_door_clears_the_corners() -> None:
    rooms, line = _two_rooms_shared_wall()
    door = Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, line, 3.5, 3.0, "vertical")
    assert quality_metrics(_plan(rooms, doors=[door])).door_corner_violations == 0


def test_doors_close_on_the_same_wall_are_counted() -> None:
    rooms, line = _two_rooms_shared_wall()
    doors = [
        Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, line, 2.0, 3.0, "vertical"),
        Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, line, 5.5, 3.0, "vertical"),
    ]
    metrics = quality_metrics(_plan(rooms, doors=doors))
    assert metrics.door_spacing_violations == 1  # gap 0.5 < 3.0


def test_doors_well_spaced_on_a_wall_pass() -> None:
    rooms, line = _two_rooms_shared_wall()
    doors = [
        Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, line, 0.5, 3.0, "vertical"),
        Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, line, 6.5, 3.0, "vertical"),
    ]
    assert quality_metrics(_plan(rooms, doors=doors)).door_spacing_violations == 0


def test_opposing_doors_across_a_narrow_corridor_are_counted() -> None:
    corridor = _room(RoomType.PASSAGE, 10, 0, 4, 20)
    left = _room(RoomType.BEDROOM, 5, 2, 5, 10)
    right = _room(RoomType.KITCHEN, 14, 4, 6, 10)
    doors = [
        Door(RoomType.BEDROOM, RoomType.PASSAGE, 10.0, 4.0, 3.0, "vertical"),
        Door(RoomType.PASSAGE, RoomType.KITCHEN, 14.0, 5.0, 3.0, "vertical"),
    ]
    metrics = quality_metrics(_plan([left, corridor, right], doors=doors))
    assert metrics.opposing_door_pairs == 1


# --- wet zone ----------------------------------------------------------------

def test_slender_bathrooms_are_counted() -> None:
    rooms = [_room(RoomType.COMMON_BATHROOM, 0, 0, 3.5, 10)]
    assert quality_metrics(_plan(rooms)).slender_bathrooms == 1


def test_usable_bathroom_is_not_slender() -> None:
    rooms = [_room(RoomType.COMMON_BATHROOM, 0, 0, 5, 8)]
    assert quality_metrics(_plan(rooms)).slender_bathrooms == 0


def test_bathroom_against_the_living_room_is_flagged() -> None:
    living = _room(RoomType.LIVING_ROOM, 0, 0, 20, 10)
    bath = _room(RoomType.COMMON_BATHROOM, 20, 0, 6, 8)
    metrics = quality_metrics(_plan([living, bath]))
    assert metrics.bathroom_social_walls == 1


def test_attached_bathroom_by_its_bedroom_is_credited() -> None:
    master = _room(RoomType.MASTER_BEDROOM, 0, 10, 10, 10)
    bath = _room(RoomType.ATTACHED_BATHROOM, 0, 0, 5, 10)
    metrics = quality_metrics(_plan([master, bath]))
    assert metrics.attached_bath_bedroom_share == 1.0
    assert metrics.bathroom_social_walls == 0


def test_common_bathroom_away_from_the_bedrooms_scores_zero() -> None:
    master = _room(RoomType.MASTER_BEDROOM, 0, 20, 10, 10)
    bath = _room(RoomType.COMMON_BATHROOM, 20, 0, 6, 8)
    metrics = quality_metrics(_plan([master, bath]))
    assert metrics.common_bath_bedroom_share == 0.0


# --- zones -------------------------------------------------------------------

def test_bedrooms_clustered_off_circulation_score_high() -> None:
    corridor = _room(RoomType.PASSAGE, 0, 0, 4, 20)
    b1 = _room(RoomType.MASTER_BEDROOM, 4, 0, 10, 10)
    b2 = _room(RoomType.CHILDREN_BEDROOM, 4, 10, 10, 10)
    doors = [
        Door(RoomType.PASSAGE, RoomType.MASTER_BEDROOM, 4.0, 3.5, 3.0, "vertical"),
        Door(RoomType.PASSAGE, RoomType.CHILDREN_BEDROOM, 4.0, 13.5, 3.0, "vertical"),
    ]
    metrics = quality_metrics(_plan([corridor, b1, b2], doors=doors))
    assert metrics.bedroom_bedroom_walls == 1  # they share a wall
    assert metrics.private_zone_share == 1.0
    assert metrics.bedroom_from_circulation == 1.0
    assert metrics.bedroom_social_walls == 0


def test_bedrooms_off_the_living_room_expose_the_private_zone() -> None:
    living = _room(RoomType.LIVING_ROOM, 0, 0, 20, 10)
    b1 = _room(RoomType.MASTER_BEDROOM, 0, 10, 10, 10)
    b2 = _room(RoomType.CHILDREN_BEDROOM, 10, 10, 10, 10)
    metrics = quality_metrics(_plan([living, b1, b2]))
    assert metrics.bedroom_social_walls == 2
    assert metrics.private_zone_share == 1.0  # still wall-connected to each other


def test_bedroom_entered_only_through_another_bedroom_is_flagged() -> None:
    b1 = _room(RoomType.MASTER_BEDROOM, 0, 0, 10, 10)
    b2 = _room(RoomType.CHILDREN_BEDROOM, 10, 0, 10, 10)
    living = _room(RoomType.LIVING_ROOM, 0, 10, 20, 10)
    doors = [
        Door(RoomType.MASTER_BEDROOM, RoomType.CHILDREN_BEDROOM, 10.0, 3.5, 3.0, "vertical"),
        Door(RoomType.LIVING_ROOM, RoomType.MASTER_BEDROOM, 0.0, 10.0, 3.0, "horizontal"),
    ]
    metrics = quality_metrics(_plan([b1, b2, living], doors=doors))
    assert metrics.bedroom_from_circulation == 0.5


# --- daylight ----------------------------------------------------------------

def test_corner_room_counts_as_cross_ventilated() -> None:
    room = _room(RoomType.LIVING_ROOM, 0, 0, 10, 10)
    metrics = quality_metrics(_plan([room], width=30, length=45))
    assert metrics.cross_ventilated == 1  # touches left + bottom


def test_interior_room_is_not_cross_ventilated() -> None:
    room = _room(RoomType.KITCHEN, 10, 10, 10, 10)
    assert quality_metrics(_plan([room], width=30, length=45)).cross_ventilated == 0


def test_window_close_to_a_corner_is_counted() -> None:
    room = _room(RoomType.LIVING_ROOM, 0, 0, 10, 10)
    window = Window(RoomType.LIVING_ROOM, 0, 0.4, 3.0, "vertical")
    metrics = quality_metrics(_plan([room], windows=[window]))
    assert metrics.window_corner_violations == 1


def test_window_and_door_close_on_one_wall_are_counted() -> None:
    left = _room(RoomType.LIVING_ROOM, 0, 0, 10, 10)
    right = _room(RoomType.KITCHEN, 10, 0, 10, 10)
    door = Door(RoomType.LIVING_ROOM, RoomType.KITCHEN, 10.0, 2.0, 3.0, "vertical")
    # A synthetic window placed on the same wall line inside the room's run.
    window = Window(RoomType.LIVING_ROOM, 10.0, 6.0, 3.0, "vertical")
    metrics = quality_metrics(_plan([left, right], doors=[door], windows=[window]))
    assert metrics.window_door_violations == 1


# --- space -------------------------------------------------------------------

def test_uncovered_fraction_measures_leftover_plot() -> None:
    rooms = [_room(RoomType.LIVING_ROOM, 0, 0, 15, 15)]
    metrics = quality_metrics(_plan(rooms, width=30, length=30))
    assert metrics.uncovered_fraction == round(1.0 - 225 / 900, 3)


def test_balcony_that_serves_no_habitable_room_is_flagged() -> None:
    balcony = _room(RoomType.BALCONY, 0, 0, 4, 12)
    kitchen = _room(RoomType.KITCHEN, 10, 10, 8, 8)
    metrics = quality_metrics(_plan([balcony, kitchen]))
    assert metrics.balcony_without_habitable == 1


def test_balcony_serving_the_living_room_passes() -> None:
    balcony = _room(RoomType.BALCONY, 0, 0, 4, 12)
    living = _room(RoomType.LIVING_ROOM, 4, 0, 12, 12)
    metrics = quality_metrics(_plan([balcony, living]))
    assert metrics.balcony_without_habitable == 0


def test_metrics_accept_rect_rooms_via_the_public_plan() -> None:
    from app.geometry.primitives import Rect

    living = Rect(RoomType.LIVING_ROOM, "Living", 0, 0, 15, 15)
    plan = _plan([living], width=30, length=30)
    metrics = quality_metrics(plan)
    assert metrics.uncovered_fraction == round(1.0 - 225 / 900, 3)
