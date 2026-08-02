"""Requirement normalisation, prompt building and the image pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.ai.imaging.base import ImageBackend
from app.ai.imaging.pipeline import ImagePipeline
from app.ai.llm.requirement_analyzer import RequirementAnalyzer
from app.ai.prompting.prompt_builder import PromptBuilder
from app.core.config import ImageProvider, ImageStrategy, get_settings
from app.core.exceptions import ImageProviderError
from app.geometry.layout_engine import LayoutEngine
from app.schemas.enums import BHKType, InteriorStyle, RoomType
from app.schemas.requirements import BathroomRequirements, FloorPlanRequirements, PlotDetails
from tests.conftest import make_requirements


# --- Requirement normalisation --------------------------------------------
def test_essential_rooms_are_added_when_omitted() -> None:
    requirements = make_requirements(rooms=[RoomType.STUDY_ROOM])
    assert RoomType.LIVING_ROOM in requirements.rooms
    assert RoomType.KITCHEN in requirements.rooms


def test_bedrooms_are_reconciled_up_to_the_bhk() -> None:
    requirements = make_requirements(bhk=BHKType.BHK4, rooms=[RoomType.LIVING_ROOM])
    assert len(requirements.bedroom_rooms) == 4


def test_bedrooms_are_reconciled_down_to_the_bhk() -> None:
    requirements = make_requirements(
        bhk=BHKType.BHK1,
        rooms=[
            RoomType.MASTER_BEDROOM,
            RoomType.GUEST_BEDROOM,
            RoomType.CHILDREN_BEDROOM,
        ],
    )
    assert len(requirements.bedroom_rooms) == 1


def test_square_plot_squares_itself_up() -> None:
    requirements = FloorPlanRequirements(
        plot=PlotDetails(width_ft=30, length_ft=45, shape="square"),
        bhk=BHKType.BHK2,
        rooms=[RoomType.LIVING_ROOM, RoomType.KITCHEN],
    )
    assert requirements.plot.width_ft == requirements.plot.length_ft == 30


def test_duplicate_selections_are_collapsed() -> None:
    requirements = make_requirements(
        rooms=[RoomType.LIVING_ROOM, RoomType.LIVING_ROOM, RoomType.KITCHEN],
        features=[RoomType.BALCONY, RoomType.BALCONY],
    )
    assert requirements.rooms.count(RoomType.LIVING_ROOM) == 1
    assert requirements.features.count(RoomType.BALCONY) == 1


def test_search_text_mentions_the_essentials() -> None:
    text = make_requirements(bhk=BHKType.BHK3).to_search_text()
    assert "3BHK" in text
    assert "sq ft" in text


def test_all_room_types_expands_bathroom_counts() -> None:
    requirements = make_requirements(
        bathrooms=BathroomRequirements(attached_count=2, common_count=1)
    )
    types = requirements.all_room_types
    assert types.count(RoomType.ATTACHED_BATHROOM) == 2
    assert types.count(RoomType.COMMON_BATHROOM) == 1


# --- Requirement analysis (no LLM key configured) --------------------------
def test_analyzer_falls_back_to_rules_without_a_key(requirements) -> None:
    analysis = RequirementAnalyzer().analyze(requirements)
    assert analysis.source == "rule-based"
    assert analysis.summary
    assert analysis.search_query
    assert analysis.zoning["private"]
    assert analysis.adjacency_preferences


def test_analyzer_zones_every_requested_room(requirements) -> None:
    zoning = RequirementAnalyzer().analyze(requirements).zoning
    zoned = {room for rooms in zoning.values() for room in rooms}
    assert RoomType.LIVING_ROOM in zoned
    assert RoomType.MASTER_BEDROOM in zoned
    assert RoomType.KITCHEN in zoned


# --- Prompt builder --------------------------------------------------------
@pytest.fixture
def plan(repository, requirements):
    return LayoutEngine(requirements).generate(
        repository.get("TPL-010"), seed=17, variation_index=0
    )


def test_prompt_describes_the_actual_geometry(plan, requirements) -> None:
    prompt = PromptBuilder().build(plan, requirements, variation_label=plan.variation)
    assert "floor plan" in prompt.positive.lower()
    assert requirements.bhk.value in prompt.positive
    assert f"{plan.plot_width:g}" in prompt.positive
    assert prompt.seed == plan.seed


def test_prompt_lists_rooms_that_are_really_placed(plan, requirements) -> None:
    prompt = PromptBuilder().build(plan, requirements)
    biggest = max(plan.rooms, key=lambda r: r.area)
    assert biggest.name.lower() in prompt.positive.lower()


def test_negative_prompt_suppresses_the_known_failure_modes(plan, requirements) -> None:
    negative = PromptBuilder().build(plan, requirements).negative
    for unwanted in ("3d render", "gibberish text", "warped walls"):
        assert unwanted in negative


def test_style_changes_the_prompt(plan) -> None:
    builder = PromptBuilder()
    luxury = builder.build(plan, make_requirements(style=InteriorStyle.LUXURY)).positive
    minimal = builder.build(plan, make_requirements(style=InteriorStyle.MINIMAL)).positive
    assert luxury != minimal


# --- Image pipeline --------------------------------------------------------
class _StubBackend(ImageBackend):
    name = "stub"

    def __init__(self, settings, *, fail: bool = False, img2img: bool = False) -> None:
        super().__init__(settings)
        self._fail = fail
        self._img2img = img2img
        self.calls = 0

    @property
    def available(self) -> bool:
        return True

    @property
    def supports_image_to_image(self) -> bool:
        return self._img2img

    def text_to_image(self, prompt, negative_prompt, *, width, height, seed):
        self.calls += 1
        if self._fail:
            raise ImageProviderError("stub backend is down")
        return Image.new("RGB", (width, height), (200, 190, 175))

    def image_to_image(self, prompt, negative_prompt, init_image, *, strength, seed):
        self.calls += 1
        if self._fail:
            raise ImageProviderError("stub backend is down")
        return Image.new("RGB", init_image.size, (180, 175, 165))


def _settings(strategy: ImageStrategy):
    settings = get_settings().model_copy()
    settings.image_strategy = strategy
    settings.image_provider = ImageProvider.NONE
    settings.image_width = settings.image_height = 512
    return settings


def test_vector_strategy_needs_no_backend(plan, requirements, tmp_path: Path) -> None:
    settings = _settings(ImageStrategy.VECTOR)
    pipeline = ImagePipeline(settings, backend=_StubBackend(settings))
    result = pipeline.generate(
        plan, requirements, destination=tmp_path / "v.png", title="T", subtitle="S"
    )
    assert result.mode == "vector"
    assert result.path.exists()
    assert Image.open(result.path).size == (512, 512)


def test_hybrid_uses_text_to_image_when_that_is_all_there_is(
    plan, requirements, tmp_path: Path
) -> None:
    settings = _settings(ImageStrategy.HYBRID)
    backend = _StubBackend(settings)
    result = ImagePipeline(settings, backend=backend).generate(
        plan, requirements, destination=tmp_path / "h.png", title="T", subtitle="S"
    )
    assert result.mode == "hybrid"
    assert backend.calls == 1


def test_hybrid_prefers_image_to_image_when_supported(
    plan, requirements, tmp_path: Path
) -> None:
    settings = _settings(ImageStrategy.HYBRID)
    backend = _StubBackend(settings, img2img=True)
    result = ImagePipeline(settings, backend=backend).generate(
        plan, requirements, destination=tmp_path / "h2.png", title="T", subtitle="S"
    )
    assert result.mode == "hybrid"
    assert backend.calls == 1


def test_provider_failure_falls_back_to_the_vector_render(
    plan, requirements, tmp_path: Path
) -> None:
    """A dead image host must never cost the user their results."""
    settings = _settings(ImageStrategy.HYBRID)
    result = ImagePipeline(settings, backend=_StubBackend(settings, fail=True)).generate(
        plan, requirements, destination=tmp_path / "f.png", title="T", subtitle="S"
    )
    assert result.mode == "vector"
    assert result.warning is not None
    assert result.path.exists()


def test_renderer_layers_are_separable(plan, requirements) -> None:
    """The linework layer must be transparent so FLUX can show through it."""
    from app.geometry.renderer import FloorPlanRenderer

    fills, linework = FloorPlanRenderer(400, 400).render_layers(
        plan, style=requirements.style, title="T", subtitle="S"
    )
    assert fills.size == linework.size == (400, 400)
    assert linework.mode == "RGBA"
    assert linework.getextrema()[3][0] == 0  # some fully transparent pixels
