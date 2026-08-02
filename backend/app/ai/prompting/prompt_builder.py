"""Custom Prompt Builder module.

Turns a validated layout plus the client's requirements into the prompt pair
that FLUX.1-dev receives. The prompt describes the *actual* geometry the
engine produced - room list, sizes and adjacencies - rather than paraphrasing
the user's form, so the stylised output stays faithful to the drawing beneath
it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.geometry.layout_engine import LayoutPlan
from app.geometry.primitives import Rect
from app.schemas.enums import InteriorStyle, RoomType
from app.schemas.requirements import FloorPlanRequirements

STYLE_LANGUAGE: dict[InteriorStyle, str] = {
    InteriorStyle.MODERN: (
        "clean contemporary architectural presentation, crisp neutral greys and warm off-whites, "
        "subtle drop shadows, matte finish"
    ),
    InteriorStyle.MINIMAL: (
        "restrained monochrome architectural line drawing, generous white space, hairline walls, "
        "no decorative flourish"
    ),
    InteriorStyle.LUXURY: (
        "premium marketing brochure rendering, warm champagne and walnut tones, soft ambient "
        "shadows, polished stone and timber floor textures"
    ),
    InteriorStyle.TRADITIONAL: (
        "classic Indian residential blueprint presentation, warm cream paper, terracotta and "
        "sandstone accents, hand-drafted character"
    ),
}

#: FLUX renders text as gibberish and invents rooms if not constrained. The
#: negative prompt suppresses the failure modes; the linework overlay is what
#: actually guarantees correct labels.
NEGATIVE_PROMPT = (
    "3d render, perspective view, isometric, elevation, photograph of a building, furniture "
    "catalogue, people, cars in the foreground, watermark, logo, signature, blurry, distorted "
    "geometry, warped walls, curved walls, overlapping rooms, unreadable text, gibberish text, "
    "duplicated labels, cluttered annotations, low resolution, jpeg artifacts, sketchy hand "
    "drawing, colour bleeding outside walls"
)


@dataclass(frozen=True)
class ImagePrompt:
    positive: str
    negative: str
    seed: int

    def as_dict(self) -> dict:
        return {"prompt": self.positive, "negative_prompt": self.negative, "seed": self.seed}


class PromptBuilder:
    """Builds the FLUX prompt from a concrete layout."""

    MAX_ROOMS_IN_PROMPT = 12

    def build(
        self,
        plan: LayoutPlan,
        requirements: FloorPlanRequirements,
        *,
        variation_label: str = "",
    ) -> ImagePrompt:
        return ImagePrompt(
            positive=self._positive(plan, requirements, variation_label),
            negative=NEGATIVE_PROMPT,
            seed=plan.seed,
        )

    # --- Composition ------------------------------------------------------
    def _positive(
        self, plan: LayoutPlan, req: FloorPlanRequirements, variation_label: str
    ) -> str:
        style_text = STYLE_LANGUAGE.get(req.style, STYLE_LANGUAGE[InteriorStyle.MODERN])

        segments = [
            "Top-down orthographic architectural floor plan drawing of a single-storey "
            f"{req.bhk.value} residence, drawn to scale on a "
            f"{plan.plot_width:g} by {plan.plot_length:g} foot plot "
            f"({plan.plot_width * plan.plot_length:,.0f} square feet)",
            f"Total built-up area {plan.built_up_sqft:,.0f} square feet across "
            f"{plan.room_count} spaces",
            self._room_sentence(plan),
            self._adjacency_sentence(plan),
            self._feature_sentence(req),
            style_text,
            "thick solid black external walls, thinner internal partitions, quarter-circle door "
            "swing arcs, window breaks in the external walls, flat two-dimensional plan view "
            "seen from directly above, professional CAD presentation quality, sharp vector "
            "edges, evenly lit, white background border",
        ]
        if variation_label:
            segments.insert(2, f"Layout variant: {variation_label.lower()} arrangement")

        return ". ".join(s for s in segments if s) + "."

    def _room_sentence(self, plan: LayoutPlan) -> str:
        ordered = sorted(plan.rooms, key=lambda r: r.area, reverse=True)
        described = [self._describe(r) for r in ordered[: self.MAX_ROOMS_IN_PROMPT]]
        remainder = len(ordered) - len(described)
        text = "Rooms: " + ", ".join(described)
        if remainder > 0:
            text += f", and {remainder} further service spaces"
        return text

    @staticmethod
    def _describe(room: Rect) -> str:
        return f"{room.name.lower()} {room.width:g}x{room.height:g} ft"

    @staticmethod
    def _adjacency_sentence(plan: LayoutPlan) -> str:
        """Name a few real adjacencies so the model reinforces the zoning."""
        interesting = {
            (RoomType.KITCHEN, RoomType.DINING_ROOM),
            (RoomType.DINING_ROOM, RoomType.LIVING_ROOM),
            (RoomType.LIVING_ROOM, RoomType.BALCONY),
            (RoomType.MASTER_BEDROOM, RoomType.ATTACHED_BATHROOM),
        }
        phrases: list[str] = []
        for i, a in enumerate(plan.rooms):
            for b in plan.rooms[i + 1 :]:
                pair = (a.type, b.type)
                if (pair in interesting or pair[::-1] in interesting) and a.is_adjacent(b):
                    phrases.append(f"the {a.name.lower()} opens onto the {b.name.lower()}")
                if len(phrases) >= 3:
                    break
            if len(phrases) >= 3:
                break
        return "; ".join(phrases).capitalize() if phrases else ""

    @staticmethod
    def _feature_sentence(req: FloorPlanRequirements) -> str:
        present = set(req.features)
        notes = []
        if RoomType.PARKING in present:
            notes.append("hatched car parking bay at the entrance")
        if RoomType.GARDEN in present:
            notes.append("stippled garden area")
        if RoomType.BALCONY in present:
            notes.append("open balcony with a railing line")
        if RoomType.STAIRCASE in present:
            notes.append("staircase drawn with individual treads and an up arrow")
        return ", ".join(notes)


_builder: PromptBuilder | None = None


def get_prompt_builder() -> PromptBuilder:
    global _builder
    if _builder is None:
        _builder = PromptBuilder()
    return _builder
