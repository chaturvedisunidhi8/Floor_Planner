"""Knowledge-base template schema.

A template is a *digitised* real floor plan: room rectangles in feet on a
plot-local coordinate grid (origin = bottom-left, +x = right, +y = up), plus the
metadata the retrieval and geometry layers need.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.enums import BHKType, Facing, InteriorStyle, PlotShape, RoomType


class RoomPlacement(BaseModel):
    """One room rectangle, in feet, relative to the plot's bottom-left corner."""

    model_config = ConfigDict(extra="forbid")

    type: RoomType
    name: str = Field(..., max_length=40, description="Label drawn on the plan")
    x: float = Field(..., ge=0)
    y: float = Field(..., ge=0)
    width: float = Field(..., gt=0)
    height: float = Field(..., gt=0)

    @property
    def area(self) -> float:
        """Derived, deliberately *not* serialised.

        A computed field would appear in ``model_dump()`` and then be rejected
        by ``extra="forbid"`` when the same document is validated back in,
        which breaks reloading a stored generation session.
        """
        return round(self.width * self.height, 2)

    @property
    def x2(self) -> float:
        return self.x + self.width

    @property
    def y2(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    def overlaps(self, other: RoomPlacement, tolerance: float = 0.01) -> bool:
        return (
            self.x < other.x2 - tolerance
            and other.x < self.x2 - tolerance
            and self.y < other.y2 - tolerance
            and other.y < self.y2 - tolerance
        )

    def shares_wall_with(self, other: RoomPlacement, tolerance: float = 0.35) -> bool:
        """True when the two rectangles touch along a segment of non-zero length."""
        vertical_touch = (
            abs(self.x2 - other.x) <= tolerance or abs(other.x2 - self.x) <= tolerance
        ) and min(self.y2, other.y2) - max(self.y, other.y) > tolerance
        horizontal_touch = (
            abs(self.y2 - other.y) <= tolerance or abs(other.y2 - self.y) <= tolerance
        ) and min(self.x2, other.x2) - max(self.x, other.x) > tolerance
        return vertical_touch or horizontal_touch


class RoomRelationship(BaseModel):
    """A recorded adjacency, e.g. kitchen -> dining ``adjacent``."""

    model_config = ConfigDict(extra="forbid")

    source: RoomType
    target: RoomType
    relation: str = Field(default="adjacent", pattern="^(adjacent|connected|attached|opposite)$")


class TemplateMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(default="reference-library", description="Where the plan came from")
    reference_image: str | None = Field(default=None, description="File in Templates/")
    facing: Facing = Facing.ANY
    floors: int = Field(default=1, ge=1, le=3)
    built_up_sqft: float | None = None
    tags: list[str] = Field(default_factory=list)


class FloorPlanTemplate(BaseModel):
    """A single entry in the Template Knowledge Base."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^TPL-\d{3}$")
    name: str
    bhk: BHKType
    plot_width_ft: float = Field(..., gt=0)
    plot_length_ft: float = Field(..., gt=0)
    plot_shape: PlotShape = PlotShape.RECTANGLE
    style: InteriorStyle = InteriorStyle.MODERN
    rooms: list[RoomPlacement] = Field(..., min_length=3)
    relationships: list[RoomRelationship] = Field(default_factory=list)
    description: str = Field(..., min_length=20)
    metadata: TemplateMetadata = Field(default_factory=TemplateMetadata)

    @model_validator(mode="after")
    def _rooms_fit_inside_plot(self) -> FloorPlanTemplate:
        margin = 0.75  # digitising tolerance on hand-measured plans
        for room in self.rooms:
            if room.x2 > self.plot_width_ft + margin or room.y2 > self.plot_length_ft + margin:
                raise ValueError(
                    f"{self.id}: room '{room.name}' extends beyond the plot "
                    f"({room.x2:g}x{room.y2:g} vs {self.plot_width_ft:g}x{self.plot_length_ft:g})"
                )
        return self

    # --- Derived features used for scoring --------------------------------
    @property
    def plot_area(self) -> float:
        return round(self.plot_width_ft * self.plot_length_ft, 2)

    @property
    def aspect_ratio(self) -> float:
        longest, shortest = (
            max(self.plot_width_ft, self.plot_length_ft),
            min(self.plot_width_ft, self.plot_length_ft),
        )
        return round(longest / shortest, 3)

    @property
    def room_types(self) -> set[RoomType]:
        return {r.type for r in self.rooms}

    @property
    def bedroom_count(self) -> int:
        return sum(1 for r in self.rooms if r.type.is_bedroom)

    @property
    def attached_bathroom_count(self) -> int:
        return sum(1 for r in self.rooms if r.type is RoomType.ATTACHED_BATHROOM)

    @property
    def common_bathroom_count(self) -> int:
        return sum(1 for r in self.rooms if r.type is RoomType.COMMON_BATHROOM)

    @property
    def has_parking(self) -> bool:
        return RoomType.PARKING in self.room_types

    @property
    def has_balcony(self) -> bool:
        return RoomType.BALCONY in self.room_types

    @property
    def built_up_area(self) -> float:
        return round(sum(r.area for r in self.rooms if not r.type.is_outdoor), 2)

    def to_embedding_text(self) -> str:
        """The document that gets encoded by bge-small-en-v1.5."""
        rooms = ", ".join(f"{r.name} {r.width:g}x{r.height:g}ft" for r in self.rooms)
        relations = ", ".join(
            f"{rel.source.label} {rel.relation} {rel.target.label}" for rel in self.relationships
        )
        return (
            f"{self.bhk.value} residential floor plan on a {self.plot_width_ft:g}ft x "
            f"{self.plot_length_ft:g}ft {self.plot_shape.value} plot "
            f"({self.plot_area:g} sq ft), {self.style.label} style, "
            f"{self.metadata.facing.value} facing. "
            f"Rooms: {rooms}. Adjacencies: {relations}. {self.description}"
        )


class TemplateSummary(BaseModel):
    """Lightweight projection returned by list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    bhk: BHKType
    plot_width_ft: float
    plot_length_ft: float
    style: InteriorStyle
    room_count: int
    description: str

    @classmethod
    def from_template(cls, template: FloorPlanTemplate) -> TemplateSummary:
        return cls(
            id=template.id,
            name=template.name,
            bhk=template.bhk,
            plot_width_ft=template.plot_width_ft,
            plot_length_ft=template.plot_length_ft,
            style=template.style,
            room_count=len(template.rooms),
            description=template.description,
        )
