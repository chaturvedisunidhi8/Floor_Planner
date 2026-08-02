"""Drives the requirement wizard.

The frontend renders every control from this response, so the two can never
drift out of sync.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.enums import (
    SELECTABLE_FEATURES,
    SELECTABLE_ROOMS,
    BHKType,
    Facing,
    InteriorStyle,
    PlotShape,
)
from app.schemas.layout import OptionItem, OptionsResponse
from app.schemas.requirements import MAX_PLOT_FT, MIN_PLOT_FT

router = APIRouter(tags=["options"])

ROOM_HINTS = {
    "living_room": "The main social space - always included",
    "dining_room": "Separate dining, usually next to the kitchen",
    "kitchen": "Always included",
    "master_bedroom": "Largest bedroom, usually with an attached bathroom",
    "guest_bedroom": "Spare bedroom for visitors",
    "children_bedroom": "Secondary bedroom",
    "study_room": "Home office or reading room",
    "pooja_room": "Prayer room, traditionally placed to the north-east",
    "utility_room": "Laundry and services, next to the kitchen",
    "store_room": "General storage",
}

FEATURE_HINTS = {
    "balcony": "Open sit-out attached to the living room or master bedroom",
    "parking": "Covered car parking bay",
    "garden": "Landscaped open area",
    "staircase": "Internal stair to an upper floor",
    "wash_area": "Outdoor washing and drying area",
}

STYLE_HINTS = {
    "modern": "Clean lines, open plan, neutral tones",
    "minimal": "Pared back, maximum light, minimum partitions",
    "luxury": "Generous rooms, premium finishes, formal zoning",
    "traditional": "Classic Indian layout with a pooja room and formal spaces",
}


@router.get("/options", response_model=OptionsResponse, summary="Every wizard choice")
def options() -> OptionsResponse:
    return OptionsResponse(
        plot_shapes=[
            OptionItem(value=s.value, label=s.value.title(), description="")
            for s in PlotShape
        ],
        bhk_types=[
            OptionItem(
                value=b.value,
                label=b.value,
                description=f"{b.bedroom_count} bedroom{'s' if b.bedroom_count > 1 else ''}",
            )
            for b in BHKType
        ],
        rooms=[
            OptionItem(value=r.value, label=r.label, description=ROOM_HINTS.get(r.value, ""))
            for r in SELECTABLE_ROOMS
        ],
        features=[
            OptionItem(value=f.value, label=f.label, description=FEATURE_HINTS.get(f.value, ""))
            for f in SELECTABLE_FEATURES
        ],
        styles=[
            OptionItem(value=s.value, label=s.label, description=STYLE_HINTS.get(s.value, ""))
            for s in InteriorStyle
        ],
        facings=[
            OptionItem(value=f.value, label=f.value.title(), description="")
            for f in Facing
        ],
        plot_width_range={"min": MIN_PLOT_FT, "max": MAX_PLOT_FT, "step": 1, "default": 30},
        plot_length_range={"min": MIN_PLOT_FT, "max": MAX_PLOT_FT, "step": 1, "default": 45},
        max_attached_bathrooms=4,
        max_common_bathrooms=3,
    )
