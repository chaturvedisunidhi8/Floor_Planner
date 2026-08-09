"""Schemas for retrieval results and generated layouts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import BHKType, InteriorStyle
from app.schemas.template import RoomPlacement, TemplateSummary


class ScoreBreakdown(BaseModel):
    """Per-criterion similarity, so the UI can explain *why* a template matched."""

    model_config = ConfigDict(extra="forbid")

    semantic: float = 0.0
    plot_dimensions: float = 0.0
    bedrooms: float = 0.0
    required_rooms: float = 0.0
    bathrooms: float = 0.0
    parking: float = 0.0
    balcony: float = 0.0
    style: float = 0.0
    layout_compatibility: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 4) for k, v in self.model_dump().items()}


class TemplateMatch(BaseModel):
    """One scored knowledge-base hit."""

    model_config = ConfigDict(extra="forbid")

    template: TemplateSummary
    score: float = Field(..., ge=0.0, le=1.0)
    breakdown: ScoreBreakdown
    rank: int = Field(..., ge=1)
    rationale: str = ""


class LayoutRoom(RoomPlacement):
    """A placed room in a generated layout (same geometry contract as templates)."""


class GeneratedLayout(BaseModel):
    """One of the 3-4 variations returned to the gallery."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    bhk: BHKType
    style: InteriorStyle
    plot_width_ft: float
    plot_length_ft: float
    plot_size_label: str
    built_up_sqft: float
    description: str
    rooms: list[LayoutRoom]
    image_url: str
    thumbnail_url: str | None = None
    source_template_id: str
    source_template_name: str
    match_score: float
    render_mode: str = Field(description="vector | flux | hybrid - how the image was produced")
    validation_warnings: list[str] = Field(default_factory=list)
    seed: int
    #: ``None`` unless the brief asked for Vastu compliance.
    vastu_score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="How far this plan follows the chosen principles"
    )
    vastu_notes: list[str] = Field(
        default_factory=list, description="Per-principle outcome, one line each"
    )


class GenerationResponse(BaseModel):
    """Payload behind the results gallery."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    created_at: datetime
    requirement_summary: str
    analysis: dict = Field(
        default_factory=dict, description="Structured requirement analysis from the LLM agent"
    )
    matches: list[TemplateMatch]
    layouts: list[GeneratedLayout]
    warnings: list[str] = Field(default_factory=list)


class LayoutSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layout_id: str


class OptionItem(BaseModel):
    value: str
    label: str
    description: str = ""


class OptionsResponse(BaseModel):
    """Drives every control in the requirement wizard."""

    plot_shapes: list[OptionItem]
    bhk_types: list[OptionItem]
    rooms: list[OptionItem]
    features: list[OptionItem]
    styles: list[OptionItem]
    facings: list[OptionItem]
    #: Directional rules offered by the optional Vastu step.
    vastu_principles: list[OptionItem]
    plot_width_range: dict[str, float]
    plot_length_range: dict[str, float]
    #: Default length/width per room key - the wizard seeds its steppers here.
    room_defaults: dict[str, dict[str, float]]
    #: Bounds and step for a single room edge.
    room_dimension_range: dict[str, float]
    max_attached_bathrooms: int
    max_common_bathrooms: int
