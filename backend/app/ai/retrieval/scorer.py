"""Requirement <-> template similarity scoring.

Step 3 of the specification names the criteria that must be compared:
plot dimensions, bedroom count, required rooms, bathroom configuration,
parking, balcony, interior style and overall layout compatibility. Each gets a
scorer returning [0, 1]; the weighted sum plus the semantic score from FAISS
produces the final ranking.

Keeping this explicit (rather than leaning on the embedding alone) is what
stops the agent recommending a 4BHK villa for a 20x30 plot.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.enums import RoomType
from app.schemas.layout import ScoreBreakdown
from app.schemas.requirements import FloorPlanRequirements
from app.schemas.template import FloorPlanTemplate


@dataclass(frozen=True)
class ScoringWeights:
    semantic: float = 0.18
    plot_dimensions: float = 0.22
    bedrooms: float = 0.18
    required_rooms: float = 0.15
    bathrooms: float = 0.09
    parking: float = 0.05
    balcony: float = 0.04
    style: float = 0.04
    layout_compatibility: float = 0.05

    def total(self) -> float:
        return (
            self.semantic
            + self.plot_dimensions
            + self.bedrooms
            + self.required_rooms
            + self.bathrooms
            + self.parking
            + self.balcony
            + self.style
            + self.layout_compatibility
        )


DEFAULT_WEIGHTS = ScoringWeights()

#: Rooms that can stand in for one another when the template lacks the exact type.
SUBSTITUTES: dict[RoomType, tuple[RoomType, ...]] = {
    RoomType.DINING_ROOM: (RoomType.LIVING_ROOM,),
    RoomType.STUDY_ROOM: (RoomType.GUEST_BEDROOM, RoomType.BEDROOM),
    RoomType.UTILITY_ROOM: (RoomType.WASH_AREA, RoomType.STORE_ROOM),
    RoomType.STORE_ROOM: (RoomType.UTILITY_ROOM,),
    RoomType.WASH_AREA: (RoomType.UTILITY_ROOM,),
    RoomType.GUEST_BEDROOM: (RoomType.BEDROOM, RoomType.CHILDREN_BEDROOM),
    RoomType.CHILDREN_BEDROOM: (RoomType.BEDROOM, RoomType.GUEST_BEDROOM),
    RoomType.MASTER_BEDROOM: (RoomType.BEDROOM,),
}


def _ratio_score(a: float, b: float, tolerance: float = 0.45) -> float:
    """1.0 when equal, decaying to 0 as the relative gap approaches ``tolerance``."""
    if a <= 0 or b <= 0:
        return 0.0
    deviation = abs(a - b) / max(a, b)
    return max(0.0, 1.0 - deviation / tolerance)


class SimilarityScorer:
    def __init__(self, weights: ScoringWeights = DEFAULT_WEIGHTS) -> None:
        self._weights = weights

    # --- Individual criteria ---------------------------------------------
    @staticmethod
    def score_plot(req: FloorPlanRequirements, tpl: FloorPlanTemplate) -> float:
        """Area, aspect ratio and per-edge fit, so a rotated plot still matches."""
        area = _ratio_score(req.plot.area_sqft, tpl.plot_area, tolerance=0.55)
        aspect = _ratio_score(req.plot.aspect_ratio, tpl.aspect_ratio, tolerance=0.6)

        direct = (
            _ratio_score(req.plot.width_ft, tpl.plot_width_ft)
            + _ratio_score(req.plot.length_ft, tpl.plot_length_ft)
        ) / 2
        rotated = (
            _ratio_score(req.plot.width_ft, tpl.plot_length_ft)
            + _ratio_score(req.plot.length_ft, tpl.plot_width_ft)
        ) / 2
        edges = max(direct, rotated)

        return round(0.45 * area + 0.25 * aspect + 0.30 * edges, 4)

    @staticmethod
    def score_bedrooms(req: FloorPlanRequirements, tpl: FloorPlanTemplate) -> float:
        wanted = req.bhk.bedroom_count
        have = tpl.bedroom_count
        if wanted == have:
            return 1.0
        # An extra bedroom is easier to absorb than a missing one.
        gap = have - wanted
        penalty = 0.28 * gap if gap > 0 else 0.45 * abs(gap)
        return round(max(0.0, 1.0 - penalty), 4)

    @staticmethod
    def score_required_rooms(req: FloorPlanRequirements, tpl: FloorPlanTemplate) -> float:
        wanted = [r for r in req.rooms if not r.is_bedroom]
        if not wanted:
            return 1.0

        available = tpl.room_types
        earned = 0.0
        for room in wanted:
            if room in available:
                earned += 1.0
            elif any(sub in available for sub in SUBSTITUTES.get(room, ())):
                earned += 0.6
        return round(earned / len(wanted), 4)

    @staticmethod
    def score_bathrooms(req: FloorPlanRequirements, tpl: FloorPlanTemplate) -> float:
        attached = _count_score(req.bathrooms.attached_count, tpl.attached_bathroom_count)
        common = _count_score(req.bathrooms.common_count, tpl.common_bathroom_count)
        total = _count_score(
            req.bathrooms.total, tpl.attached_bathroom_count + tpl.common_bathroom_count
        )
        return round(0.4 * attached + 0.25 * common + 0.35 * total, 4)

    @staticmethod
    def score_feature(wanted: bool, present: bool) -> float:
        if wanted and present:
            return 1.0
        if not wanted and not present:
            return 1.0
        # Having something the user did not ask for is a mild mismatch;
        # missing something they did ask for is a real one.
        return 0.7 if present else 0.0

    @staticmethod
    def score_style(req: FloorPlanRequirements, tpl: FloorPlanTemplate) -> float:
        if req.style is tpl.style:
            return 1.0
        # Modern and minimal read alike; luxury and traditional both mean "more
        # formal rooms". Cross-family matches score lower but are not excluded.
        families = ({"modern", "minimal"}, {"luxury", "traditional"})
        for family in families:
            if req.style.value in family and tpl.style.value in family:
                return 0.75
        return 0.4

    @staticmethod
    def score_layout_compatibility(req: FloorPlanRequirements, tpl: FloorPlanTemplate) -> float:
        """Can the requested programme physically fit this template's envelope?

        Compares the requested room count against the template's, and checks
        that the plot can carry the built-up area the template implies.
        """
        requested_rooms = len(req.all_room_types)
        template_rooms = len(tpl.rooms)
        density = _ratio_score(float(requested_rooms), float(template_rooms), tolerance=0.7)

        coverage = tpl.built_up_area / tpl.plot_area if tpl.plot_area else 0.0
        implied_built_up = req.plot.area_sqft * coverage
        area_per_room = implied_built_up / max(requested_rooms, 1)
        # ~90 sq ft per programme element is a comfortable residential average.
        headroom = min(1.0, area_per_room / 90.0)

        return round(0.55 * density + 0.45 * headroom, 4)

    # --- Aggregate --------------------------------------------------------
    def breakdown(
        self,
        req: FloorPlanRequirements,
        tpl: FloorPlanTemplate,
        semantic_score: float,
    ) -> ScoreBreakdown:
        return ScoreBreakdown(
            semantic=round(semantic_score, 4),
            plot_dimensions=self.score_plot(req, tpl),
            bedrooms=self.score_bedrooms(req, tpl),
            required_rooms=self.score_required_rooms(req, tpl),
            bathrooms=self.score_bathrooms(req, tpl),
            parking=self.score_feature(req.has_parking, tpl.has_parking),
            balcony=self.score_feature(req.has_balcony, tpl.has_balcony),
            style=self.score_style(req, tpl),
            layout_compatibility=self.score_layout_compatibility(req, tpl),
        )

    def aggregate(self, breakdown: ScoreBreakdown) -> float:
        w = self._weights
        total = (
            w.semantic * breakdown.semantic
            + w.plot_dimensions * breakdown.plot_dimensions
            + w.bedrooms * breakdown.bedrooms
            + w.required_rooms * breakdown.required_rooms
            + w.bathrooms * breakdown.bathrooms
            + w.parking * breakdown.parking
            + w.balcony * breakdown.balcony
            + w.style * breakdown.style
            + w.layout_compatibility * breakdown.layout_compatibility
        )
        return round(min(1.0, max(0.0, total / w.total())), 4)

    def explain(self, breakdown: ScoreBreakdown) -> str:
        """One-line, human-readable justification for the UI."""
        labels = {
            "plot_dimensions": "plot size",
            "bedrooms": "bedroom count",
            "required_rooms": "requested rooms",
            "bathrooms": "bathroom mix",
            "parking": "parking",
            "balcony": "balcony",
            "style": "interior style",
            "layout_compatibility": "layout fit",
            "semantic": "overall description",
        }
        scores = breakdown.as_dict()
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        strong = [labels[k] for k, v in ranked if v >= 0.8][:3]
        weak = [labels[k] for k, v in ranked if v < 0.5][:2]

        parts = []
        if strong:
            parts.append("Strong match on " + ", ".join(strong))
        if weak:
            parts.append("adapted for " + ", ".join(weak))
        return "; ".join(parts) or "Balanced match across all criteria"


def _count_score(wanted: int, have: int) -> float:
    if wanted == have:
        return 1.0
    if wanted == 0:
        return 0.75 if have else 1.0
    return round(max(0.0, 1.0 - abs(wanted - have) / max(wanted, have, 1) * 0.9), 4)
