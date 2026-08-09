"""The structured requirement object produced by the guided UI.

Step 1 of the specification: every widget in the wizard maps onto exactly one
field here, and the whole thing is posted as a single JSON document.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.enums import (
    SELECTABLE_FEATURES,
    SELECTABLE_ROOMS,
    SELECTABLE_VASTU_PRINCIPLES,
    BHKType,
    Facing,
    InteriorStyle,
    PlotShape,
    RoomType,
    VastuPrinciple,
)

MIN_PLOT_FT = 15
MAX_PLOT_FT = 100

#: Bounds for a single room edge. Wide enough for a walk-in pooja room at the
#: bottom and a double-height living room at the top.
MIN_ROOM_FT = 5
MAX_ROOM_FT = 40


@dataclass(frozen=True)
class RoomTarget:
    """What the client asked one room to be, in the terms the geometry engine aims at.

    Area alone is not enough: a 6 x 28 ft room satisfies a 168 sq ft target and
    is still nothing like the 14 x 12 room that was asked for. Carrying the
    sides as well lets the engine score shape and size together.

    ``long_side``/``short_side`` are optional so an area-only target (from the
    deprecated float API) still works, just without the shape term.
    """

    area: float
    long_side: float | None = None
    short_side: float | None = None

    @property
    def aspect(self) -> float | None:
        """Requested proportion, or ``None`` when only an area was given."""
        if not self.long_side or not self.short_side:
            return None
        return self.long_side / self.short_side

    @classmethod
    def from_dimensions(cls, dims: RoomDimensions) -> RoomTarget:
        return cls(
            area=dims.area_sqft,
            long_side=max(dims.length_ft, dims.width_ft),
            short_side=min(dims.length_ft, dims.width_ft),
        )

    @classmethod
    def coerce(cls, value: RoomTarget | float) -> RoomTarget:
        """Accept a bare area as well as a full target."""
        return value if isinstance(value, cls) else cls(area=float(value))


class RoomDimensions(BaseModel):
    """The size the client asked a room to be, in feet.

    Advisory rather than binding: the geometry engine treats these as targets
    and may adjust them slightly to make the rooms tile the plot.
    """

    model_config = ConfigDict(extra="forbid")

    length_ft: float = Field(..., ge=MIN_ROOM_FT, le=MAX_ROOM_FT)
    width_ft: float = Field(..., ge=MIN_ROOM_FT, le=MAX_ROOM_FT)

    @property
    def area_sqft(self) -> float:
        return round(self.length_ft * self.width_ft, 2)

    def describe(self) -> str:
        return f"{self.length_ft:g}x{self.width_ft:g} ft"


#: Sensible starting sizes for every room the wizard offers. The frontend seeds
#: its steppers from these via ``GET /options``, so the table lives here only.
ROOM_DEFAULT_DIMENSIONS: dict[RoomType, RoomDimensions] = {
    RoomType.LIVING_ROOM: RoomDimensions(length_ft=16, width_ft=14),
    RoomType.DINING_ROOM: RoomDimensions(length_ft=12, width_ft=10),
    RoomType.KITCHEN: RoomDimensions(length_ft=10, width_ft=10),
    RoomType.MASTER_BEDROOM: RoomDimensions(length_ft=14, width_ft=12),
    RoomType.GUEST_BEDROOM: RoomDimensions(length_ft=12, width_ft=11),
    RoomType.CHILDREN_BEDROOM: RoomDimensions(length_ft=11, width_ft=10),
    RoomType.STUDY_ROOM: RoomDimensions(length_ft=10, width_ft=8),
    RoomType.POOJA_ROOM: RoomDimensions(length_ft=8, width_ft=6),
    RoomType.UTILITY_ROOM: RoomDimensions(length_ft=8, width_ft=6),
    RoomType.STORE_ROOM: RoomDimensions(length_ft=7, width_ft=6),
}


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


class VastuPreferences(BaseModel):
    """Whether - and how far - the plan should follow Vastu Shastra.

    Entirely optional: with ``enabled`` off the geometry engine behaves exactly
    as it did before this existed, which is why the toggle rather than the
    principle list is what gates the whole feature.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    principles: list[VastuPrinciple] = Field(
        default_factory=list,
        description="Directional rules to honour. Ignored while `enabled` is false.",
    )

    @model_validator(mode="after")
    def _normalise(self) -> VastuPreferences:
        if not self.enabled:
            object.__setattr__(self, "principles", [])
            return self

        allowed = set(SELECTABLE_VASTU_PRINCIPLES)
        principles = [p for p in dict.fromkeys(self.principles) if p in allowed]
        # Asking for Vastu without saying which rules matter means "the usual
        # ones" rather than "none", which would silently do nothing at all.
        object.__setattr__(self, "principles", principles or list(DEFAULT_VASTU_PRINCIPLES))
        return self

    @property
    def is_active(self) -> bool:
        return self.enabled and bool(self.principles)

    def describe(self) -> str:
        if not self.is_active:
            return "no Vastu constraints"
        return "Vastu: " + ", ".join(p.label.lower() for p in self.principles)


#: Applied when the client enables Vastu without picking any specific rule.
DEFAULT_VASTU_PRINCIPLES: tuple[VastuPrinciple, ...] = (
    VastuPrinciple.POOJA_NORTHEAST,
    VastuPrinciple.KITCHEN_SOUTHEAST,
    VastuPrinciple.MASTER_BEDROOM_SOUTHWEST,
)


class FloorPlanRequirements(BaseModel):
    """Complete, validated user intent. This is what the AI agent reasons over."""

    model_config = ConfigDict(extra="forbid")

    plot: PlotDetails
    bhk: BHKType
    rooms: list[RoomType] = Field(default_factory=list, description="Rooms explicitly requested")
    room_dimensions: dict[RoomType, RoomDimensions] = Field(
        default_factory=dict,
        description="Per-room size targets keyed by room. Omitted rooms are sized by the engine.",
    )
    bathrooms: BathroomRequirements = Field(default_factory=BathroomRequirements)
    features: list[RoomType] = Field(default_factory=list, description="Balcony/parking/garden/...")
    vastu: VastuPreferences = Field(default_factory=VastuPreferences)
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

        # A size for a room nobody asked for would just confuse the engine.
        dimensions = {r: d for r, d in self.room_dimensions.items() if r in rooms}

        object.__setattr__(self, "rooms", rooms)
        object.__setattr__(self, "features", features)
        object.__setattr__(self, "room_dimensions", dimensions)
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
    def room_targets(self) -> dict[RoomType, RoomTarget]:
        """Requested size *and shape* per room - what the geometry engine aims for."""
        return {
            room: RoomTarget.from_dimensions(dims)
            for room, dims in self.room_dimensions.items()
        }

    @property
    def area_targets(self) -> dict[RoomType, float]:
        """Requested floor area per room.

        .. deprecated::
            Area on its own cannot tell a 14 x 12 room from a 6 x 28 one. Use
            :attr:`room_targets`; this remains for callers that only need the
            square footage.
        """
        return {room: dims.area_sqft for room, dims in self.room_dimensions.items()}

    @property
    def requested_area_sqft(self) -> float:
        """Total area the client allocated to rooms, excluding walls and circulation."""
        return round(sum(d.area_sqft for d in self.room_dimensions.values()), 2)

    def describe_dimensions(self) -> str:
        """``living_room 16x14 ft, kitchen 10x10 ft`` - empty when none were sent."""
        return ", ".join(
            f"{room.value} {dims.describe()}" for room, dims in self.room_dimensions.items()
        )

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
        # Spelling the principles out steers retrieval towards the templates
        # whose descriptions already talk about corner placement.
        vastu = f"{self.vastu.describe()}. " if self.vastu.is_active else ""
        return (
            f"{self.bhk.value} residential floor plan on a {self.plot.describe()}. "
            f"Rooms: {rooms}. Bathrooms: {self.bathrooms.describe()}. "
            f"Additional features: {features}. Interior style: {self.style.label}. "
            f"Facing: {self.plot.facing.value}. {vastu}{self.notes}".strip()
        )


class GenerationRequest(BaseModel):
    """Wire format for ``POST /generate``."""

    model_config = ConfigDict(extra="forbid")

    requirements: FloorPlanRequirements
    variants: int | None = Field(default=None, ge=1, le=8)
    seed: int | None = Field(default=None, description="Set for reproducible layouts")
