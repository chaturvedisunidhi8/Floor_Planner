"""Vastu compliance: the scoring, the orientation pass and the brief it comes from.

North is up and east is to the right on every plan, so the zone a room sits in
is decided entirely by where its rectangle lands on the drawing.
"""

from __future__ import annotations

import pytest

from app.geometry import vastu
from app.geometry.layout_engine import LayoutEngine
from app.geometry.primitives import Rect
from app.geometry.validator import LayoutValidator
from app.schemas.enums import BHKType, RoomType, VastuPrinciple
from app.schemas.requirements import (
    DEFAULT_VASTU_PRINCIPLES,
    FloorPlanRequirements,
    PlotDetails,
    VastuPreferences,
)
from tests.conftest import make_requirements

PLOT_W, PLOT_L = 30.0, 45.0

THREE_RULES = [
    VastuPrinciple.POOJA_NORTHEAST,
    VastuPrinciple.KITCHEN_SOUTHEAST,
    VastuPrinciple.MASTER_BEDROOM_SOUTHWEST,
]


def _room(room_type: RoomType, x: float, y: float, w: float = 8.0, h: float = 8.0) -> Rect:
    return Rect(room_type, room_type.label, x, y, w, h)


def _north_east(room_type: RoomType) -> Rect:
    return _room(room_type, PLOT_W - 8, PLOT_L - 8)


def _south_west(room_type: RoomType) -> Rect:
    return _room(room_type, 0, 0)


# --- The vocabulary --------------------------------------------------------
def test_every_principle_has_a_rule_and_a_label() -> None:
    for principle in VastuPrinciple:
        assert principle in vastu.RULES
        assert vastu.RULES[principle].zone in vastu.ZONE_TARGETS
        assert principle.label


# --- Scoring ---------------------------------------------------------------
def test_a_room_in_its_corner_scores_far_higher_than_one_opposite() -> None:
    good = vastu.score([_north_east(RoomType.POOJA_ROOM)], PLOT_W, PLOT_L, [THREE_RULES[0]])
    bad = vastu.score([_south_west(RoomType.POOJA_ROOM)], PLOT_W, PLOT_L, [THREE_RULES[0]])

    assert good.score == 1.0
    assert bad.score == 0.0
    assert good.satisfied == [VastuPrinciple.POOJA_NORTHEAST]
    assert bad.unmet == [VastuPrinciple.POOJA_NORTHEAST]


def test_a_large_room_is_judged_against_where_a_room_its_size_could_go() -> None:
    """A room filling half the plot cannot reach the corner, and is not asked to."""
    big = Rect(RoomType.LIVING_ROOM, "Living", 0, PLOT_L / 2, PLOT_W, PLOT_L / 2)
    report = vastu.score([big], PLOT_W, PLOT_L, [VastuPrinciple.LIVING_NORTHEAST])

    # As far north as its footprint allows, and spanning east to west.
    assert report.score == 1.0


def test_a_principle_with_no_room_to_govern_is_skipped_not_failed() -> None:
    report = vastu.score([_south_west(RoomType.KITCHEN)], PLOT_W, PLOT_L, THREE_RULES)

    assert VastuPrinciple.POOJA_NORTHEAST not in report.satisfied
    assert VastuPrinciple.POOJA_NORTHEAST not in report.unmet
    assert report.unmet == [VastuPrinciple.KITCHEN_SOUTHEAST]


def test_the_worst_placed_room_decides_a_shared_principle() -> None:
    rooms = [
        _room(RoomType.COMMON_BATHROOM, 0, PLOT_L - 8),  # north-west, as asked
        _room(RoomType.ATTACHED_BATHROOM, PLOT_W - 8, 0),  # south-east, not
    ]
    report = vastu.score(rooms, PLOT_W, PLOT_L, [VastuPrinciple.BATHROOMS_NORTHWEST])

    assert report.score == 0.0
    assert report.unmet == [VastuPrinciple.BATHROOMS_NORTHWEST]


def test_no_principles_scores_zero_without_claiming_anything() -> None:
    report = vastu.score([_north_east(RoomType.POOJA_ROOM)], PLOT_W, PLOT_L, [])

    assert report.score == 0.0
    assert report.satisfied == []
    assert report.unmet == []


# --- Orientation -----------------------------------------------------------
def test_orient_reflects_the_plan_into_its_best_corner() -> None:
    rooms = [_south_west(RoomType.POOJA_ROOM), _north_east(RoomType.MASTER_BEDROOM)]
    oriented, report = vastu.orient(rooms, PLOT_W, PLOT_L, THREE_RULES)

    assert report.score == 1.0
    placed = {room.type: vastu.describe_zone(room, PLOT_W, PLOT_L) for room in oriented}
    assert placed[RoomType.POOJA_ROOM] == "north-east"
    assert placed[RoomType.MASTER_BEDROOM] == "south-west"


def test_orient_leaves_a_compliant_plan_exactly_where_it_is() -> None:
    rooms = [_north_east(RoomType.POOJA_ROOM), _south_west(RoomType.MASTER_BEDROOM)]
    oriented, _ = vastu.orient(rooms, PLOT_W, PLOT_L, THREE_RULES)

    assert oriented == rooms


def test_orient_preserves_every_room_size() -> None:
    rooms = [
        _room(RoomType.KITCHEN, 2, 3, 10, 12),
        _room(RoomType.POOJA_ROOM, 4, 30, 6, 7),
        _room(RoomType.MASTER_BEDROOM, 14, 20, 13, 11),
    ]
    oriented, _ = vastu.orient(rooms, PLOT_W, PLOT_L, THREE_RULES)

    assert sorted((r.type, r.width, r.height) for r in oriented) == sorted(
        (r.type, r.width, r.height) for r in rooms
    )
    for room in oriented:
        assert room.x >= 0 and room.x2 <= PLOT_W
        assert room.y >= 0 and room.y2 <= PLOT_L


def test_comply_trades_two_service_rooms_that_are_each_in_the_others_place() -> None:
    """Reflection cannot separate them; swapping the two labels can."""
    rooms = [
        _room(RoomType.POOJA_ROOM, 0, 0, 7, 7),  # south-west, wants north-east
        _room(RoomType.STORE_ROOM, PLOT_W - 7, PLOT_L - 7, 7, 7),  # the other way about
        # Pins the orientation, so a reflection cannot be the answer.
        _room(RoomType.MASTER_BEDROOM, 8, 0, 12, 12),
    ]
    complied, report = vastu.comply(
        rooms,
        PLOT_W,
        PLOT_L,
        [VastuPrinciple.POOJA_NORTHEAST, VastuPrinciple.STORAGE_SOUTHWEST],
    )

    placed = {room.type: vastu.describe_zone(room, PLOT_W, PLOT_L) for room in complied}
    assert placed[RoomType.POOJA_ROOM] == "north-east"
    assert placed[RoomType.STORE_ROOM] == "south-west"
    assert report.score > 0.9


def test_rooms_of_very_different_sizes_are_never_traded() -> None:
    rooms = [
        _room(RoomType.POOJA_ROOM, 0, 0, 6, 6),
        _room(RoomType.STORE_ROOM, PLOT_W - 20, PLOT_L - 20, 20, 20),
    ]
    complied, _ = vastu.comply(rooms, PLOT_W, PLOT_L, [VastuPrinciple.POOJA_NORTHEAST])

    assert {r.type for r in complied if r.area < 50} == {RoomType.POOJA_ROOM}


# --- The brief -------------------------------------------------------------
def test_vastu_is_off_by_default() -> None:
    requirements = make_requirements()
    assert not requirements.vastu.enabled
    assert not requirements.vastu.is_active


def test_disabling_vastu_clears_the_principles() -> None:
    preferences = VastuPreferences(enabled=False, principles=THREE_RULES)
    assert preferences.principles == []
    assert not preferences.is_active


def test_enabling_vastu_without_a_choice_falls_back_to_the_common_rules() -> None:
    preferences = VastuPreferences(enabled=True)
    assert preferences.principles == list(DEFAULT_VASTU_PRINCIPLES)
    assert preferences.is_active


def test_duplicate_principles_are_collapsed() -> None:
    preferences = VastuPreferences(
        enabled=True,
        principles=[VastuPrinciple.KITCHEN_SOUTHEAST, VastuPrinciple.KITCHEN_SOUTHEAST],
    )
    assert preferences.principles == [VastuPrinciple.KITCHEN_SOUTHEAST]


def test_the_search_text_mentions_vastu_only_when_it_is_wanted() -> None:
    without = make_requirements().to_search_text()
    with_vastu = make_requirements(
        vastu=VastuPreferences(enabled=True, principles=THREE_RULES)
    ).to_search_text()

    assert "vastu" not in without.lower()
    assert "vastu" in with_vastu.lower()
    assert "north-east" in with_vastu


# --- End to end through the engine ----------------------------------------
def _brief(vastu_preferences: VastuPreferences) -> FloorPlanRequirements:
    return make_requirements(
        plot=PlotDetails(width_ft=PLOT_W, length_ft=PLOT_L),
        bhk=BHKType.BHK3,
        rooms=[
            RoomType.LIVING_ROOM,
            RoomType.KITCHEN,
            RoomType.POOJA_ROOM,
            RoomType.MASTER_BEDROOM,
            RoomType.CHILDREN_BEDROOM,
            RoomType.GUEST_BEDROOM,
        ],
        vastu=vastu_preferences,
    )


def test_a_plan_carries_no_vastu_report_when_none_was_asked_for(repository) -> None:
    plan = LayoutEngine(_brief(VastuPreferences())).generate(
        repository.get("TPL-019"), seed=7, variation_index=0
    )
    assert plan.vastu is None


def test_asking_for_vastu_improves_the_placement_of_the_same_plan(repository) -> None:
    template = repository.get("TPL-019")
    principles = THREE_RULES

    off = LayoutEngine(_brief(VastuPreferences())).generate(template, seed=7, variation_index=0)
    on = LayoutEngine(_brief(VastuPreferences(enabled=True, principles=principles))).generate(
        template, seed=7, variation_index=0
    )

    baseline = vastu.score(off.rooms, off.plot_width, off.plot_length, principles)
    assert on.vastu is not None
    assert on.vastu.score >= baseline.score


def test_the_vastu_pass_never_invalidates_the_geometry(repository) -> None:
    """The whole point of reflecting rather than redrawing."""
    requirements = _brief(VastuPreferences(enabled=True, principles=list(VastuPrinciple)))
    engine = LayoutEngine(requirements)

    for index, template_id in enumerate(("TPL-001", "TPL-008", "TPL-010", "TPL-019")):
        plan = engine.generate(repository.get(template_id), seed=31 + index, variation_index=index)
        report = LayoutValidator(plan.plot_width, plan.plot_length).validate(plan.rooms)
        assert report.ok, f"{template_id}: {report.errors}"
        assert plan.vastu is not None
        assert 0.0 <= plan.vastu.score <= 1.0


def test_the_vastu_pass_keeps_the_built_up_area(repository) -> None:
    template = repository.get("TPL-010")
    off = LayoutEngine(_brief(VastuPreferences())).generate(template, seed=5, variation_index=1)
    on = LayoutEngine(
        _brief(VastuPreferences(enabled=True, principles=THREE_RULES))
    ).generate(template, seed=5, variation_index=1)

    assert on.built_up_sqft == pytest.approx(off.built_up_sqft, rel=0.02)
    assert on.room_count == off.room_count
