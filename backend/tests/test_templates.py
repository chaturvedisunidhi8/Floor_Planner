"""The knowledge base must be geometrically sound - everything builds on it."""

from __future__ import annotations

import pytest

from app.schemas.enums import BHKType, RoomType
from app.schemas.template import FloorPlanTemplate


def test_library_has_twenty_templates(templates: list[FloorPlanTemplate]) -> None:
    assert len(templates) == 20
    assert len({t.id for t in templates}) == 20


def test_every_bhk_type_is_represented(templates: list[FloorPlanTemplate]) -> None:
    covered = {t.bhk for t in templates}
    assert covered == set(BHKType)


@pytest.mark.parametrize("index", range(20))
def test_rooms_never_overlap(index: int, templates: list[FloorPlanTemplate]) -> None:
    template = sorted(templates, key=lambda t: t.id)[index]
    for i, a in enumerate(template.rooms):
        for b in template.rooms[i + 1 :]:
            assert not a.overlaps(b), f"{template.id}: '{a.name}' overlaps '{b.name}'"


def test_rooms_stay_inside_the_plot(templates: list[FloorPlanTemplate]) -> None:
    for template in templates:
        for room in template.rooms:
            assert room.x2 <= template.plot_width_ft + 0.75, f"{template.id}/{room.name}"
            assert room.y2 <= template.plot_length_ft + 0.75, f"{template.id}/{room.name}"


def test_bedroom_count_matches_declared_bhk(templates: list[FloorPlanTemplate]) -> None:
    for template in templates:
        assert template.bedroom_count == template.bhk.bedroom_count, template.id


def test_every_template_has_the_essentials(templates: list[FloorPlanTemplate]) -> None:
    for template in templates:
        assert RoomType.LIVING_ROOM in template.room_types, template.id
        assert RoomType.KITCHEN in template.room_types, template.id


def test_built_up_area_is_plausible(templates: list[FloorPlanTemplate]) -> None:
    """Between half and all of the plot - anything else means a digitising slip."""
    for template in templates:
        coverage = template.built_up_area / template.plot_area
        assert 0.5 <= coverage <= 1.01, f"{template.id} covers {coverage:.0%} of its plot"


def test_embedding_text_mentions_the_key_facts(templates: list[FloorPlanTemplate]) -> None:
    template = templates[0]
    text = template.to_embedding_text()
    assert template.bhk.value in text
    assert template.style.label.lower() in text.lower()
    assert "sq ft" in text


def test_relationships_reference_rooms_that_exist(templates: list[FloorPlanTemplate]) -> None:
    for template in templates:
        present = template.room_types
        for relation in template.relationships:
            assert relation.source in present, f"{template.id}: {relation.source}"
            assert relation.target in present, f"{template.id}: {relation.target}"
