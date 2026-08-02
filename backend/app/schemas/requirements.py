"""The structured requirement object produced by the guided UI.

Step 1 of the specification: every widget in the wizard maps onto exactly one
field here, and the whole thing is posted as a single JSON document.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.enums import (
    SELECTABLE_FEATURES,
    SELECTABLE_ROOMS,
    BHKType,
    Facing,
    InteriorStyle,
    PlotShape,
    RoomType,
)

MIN_PLOT_FT = 15
MAX_PLOT_FT = 100


class PlotDetails(BaseModel):
    """Plot geometry in feet - the hard constraint every layout must respect."""

    model_config = ConfigDict(extra="forbid")

    width_ft: float = Field(..., ge=MIN_PLOT_FT, le=MAX_PLOT_FT, description="Plot width in feet")
    length_ft: float = Field(..., ge=MIN_PLOT_FT, le=MAX_PLOT_FT, description="Plot length in feet")
    shape: PlotShape = PlotShape.RECTANGLE
    facing: Facing = Facing.ANY

    @model_validator(mode="after")
    def _square_must_be_square(self) -> PlotDetails:
        if self.shape is PlotShape.SQUARE and abs(self.width_ft - self.length_ft) > 0.01:
            # Treat width as authoritative rather than rejecting the request:
            # the slider pair in the UI is free-form and this is a benign fix-up.
            object.__setattr__(self, "length_ft", self.width_ft)
        return self

    @property
    def area_sqft(self) -> float:
        return round(self.width_ft * self.length_ft, 2)

    @property
    def aspect_ratio(self) -> float:
        return round(max(self.width_ft, self.length_ft) / min(self.width_ft, self.length_ft), 3)

    def describe(self) -> str:
        return (
            f"{self.width_ft:g}ft x {self.length_ft:g}ft {self.shape.value} plot "
            f"({self.area_sqft:g} sq ft)"
        )


class BathroomRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attached_count: int = Field(default=1, ge=0, le=4, description="Bathrooms inside bedrooms")
    common_count: int = Field(default=1, ge=0, le=3, description="Shared bathrooms")

    @property
    def total(self) -> int:
        return self.attached_count + self.common_count

    def describe(self) -> str:
        parts = []
        if self.attached_count:
            parts.append(f"{self.attached_count} attached bathroom(s)")
        if self.common_count:
            parts.append(f"{self.common_count} common bathroom(s)")
        return ", ".join(parts) or "no dedicated bathrooms"


class FloorPlanRequirements(BaseModel):
    """Complete, validated user intent. This is what the AI agent reasons over."""

    model_config = ConfigDict(extra="forbid")

    plot: PlotDetails
    bhk: BHKType
    rooms: list[RoomType] = Field(default_factory=list, description="Rooms explicitly requested")
    bathrooms: BathroomRequirements = Field(default_factory=BathroomRequirements)
    features: list[RoomType] = Field(default_factory=list, description="Balcony/parking/garden/...")
    style: InteriorStyle = InteriorStyle.MODERN
    notes: str = Field(default="", max_length=500, description="Optional free-text nuance")

    @model_validator(mode="after")
    def _normalise(self) -> FloorPlanRequirements:
        allowed_rooms = set(SELECTABLE_ROOMS)
        allowed_features = set(SELECTABLE_FEATURES)

        rooms = [r for r in dict.fromkeys(self.rooms) if r in allowed_rooms]
        features = [f for f in dict.fromkeys(self.features) if f in allowed_features]

        # A dwelling without these is not a dwelling; the wizard pre-checks them
        # but a direct API caller might not.
        for essential in (RoomType.LIVING_ROOM, RoomType.KITCHEN):
            if essential not in rooms:
                rooms.append(essential)

        # The BHK count is authoritative over individual bedroom ticks.
        rooms = self._reconcile_bedrooms(rooms)

        object.__setattr__(self, "rooms", rooms)
        object.__setattr__(self, "features", features)
        return self

    def _reconcile_bedrooms(self, rooms: list[RoomType]) -> list[RoomType]:
        """Make the bedroom selection add up to ``bhk.bedroom_count``."""
        preference = (
            RoomType.MASTER_BEDROOM,
            RoomType.CHILDREN_BEDROOM,
            RoomType.GUEST_BEDROOM,
        )
        selected = [r for r in rooms if r.is_bedroom]
        others = [r for r in rooms if not r.is_bedroom]
        target = self.bhk.bedroom_count

        if len(selected) > target:
            selected = selected[:target]
        else:
            for candidate in preference:
                if len(selected) >= target:
                    break
                if candidate not in selected:
                    selected.append(candidate)
            # 4BHK needs one more than the three named types provide.
            while len(selected) < target:
                selected.append(RoomType.BEDROOM)

        ordered = [r for r in rooms if r in selected]
        ordered += [r for r in selected if r not in ordered]
        return ordered + [r for r in others if r not in ordered]

    # --- Derived views used by retrieval, prompting and geometry ----------
    @property
    def bedroom_rooms(self) -> list[RoomType]:
        return [r for r in self.rooms if r.is_bedroom]

    @property
    def all_room_types(self) -> list[RoomType]:
        """Requested rooms + bathrooms + features, in placement order."""
        result = list(self.rooms)
        result += [RoomType.ATTACHED_BATHROOM] * self.bathrooms.attached_count
        result += [RoomType.COMMON_BATHROOM] * self.bathrooms.common_count
        result += list(self.features)
        return result

    @property
    def has_parking(self) -> bool:
        return RoomType.PARKING in self.features

    @property
    def has_balcony(self) -> bool:
        return RoomType.BALCONY in self.features

    def to_search_text(self) -> str:
        """Natural-language projection used to embed the request for FAISS."""
        rooms = ", ".join(r.label for r in self.rooms)
        features = ", ".join(f.label for f in self.features) or "none"
        return (
            f"{self.bhk.value} residential floor plan on a {self.plot.describe()}. "
            f"Rooms: {rooms}. Bathrooms: {self.bathrooms.describe()}. "
            f"Additional features: {features}. Interior style: {self.style.label}. "
            f"Facing: {self.plot.facing.value}. {self.notes}".strip()
        )


class GenerationRequest(BaseModel):
    """Wire format for ``POST /generate``."""

    model_config = ConfigDict(extra="forbid")

    requirements: FloorPlanRequirements
    variants: int | None = Field(default=None, ge=1, le=8)
    seed: int | None = Field(default=None, description="Set for reproducible layouts")
