"""Digitise the reference floor plans into the Template Knowledge Base.

Run once (or after editing the tables below) to regenerate
``backend/data/templates/TPL-XXX.json``::

    python scripts/author_templates.py

Templates 001-016 are traced from the images in ``Templates/``; 017-020 are
architect-standard variants added to round the library out to 20 and to cover
plot sizes the traced set leaves thin (compact 1BHK, minimal 2BHK, traditional
3BHK with pooja, luxury 4BHK villa).

Coordinate system: feet, origin at the plot's bottom-left corner, +x right,
+y up (i.e. "up" on the drawing is the rear of the plot).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.enums import BHKType, Facing, InteriorStyle, PlotShape, RoomType
from app.schemas.template import (
    FloorPlanTemplate,
    RoomPlacement,
    RoomRelationship,
    TemplateMetadata,
)

R = RoomType

# Room tables: (RoomType, label, x, y, width, height) in feet.
Row = tuple[RoomType, str, float, float, float, float]


def _rooms(rows: list[Row]) -> list[RoomPlacement]:
    return [
        RoomPlacement(type=t, name=name, x=x, y=y, width=w, height=h)
        for t, name, x, y, w, h in rows
    ]


# Adjacency pairs worth recording explicitly: everything else is derived from
# shared walls. These express *intent* ("kitchen serves the dining room")
# rather than mere geometry, and they are what the LLM reasons over.
SEMANTIC_PAIRS: tuple[tuple[RoomType, RoomType, str], ...] = (
    (R.KITCHEN, R.DINING_ROOM, "connected"),
    (R.DINING_ROOM, R.LIVING_ROOM, "connected"),
    (R.KITCHEN, R.UTILITY_ROOM, "connected"),
    (R.KITCHEN, R.WASH_AREA, "connected"),
    (R.KITCHEN, R.STORE_ROOM, "adjacent"),
    (R.MASTER_BEDROOM, R.ATTACHED_BATHROOM, "attached"),
    (R.GUEST_BEDROOM, R.ATTACHED_BATHROOM, "attached"),
    (R.CHILDREN_BEDROOM, R.ATTACHED_BATHROOM, "attached"),
    (R.BEDROOM, R.ATTACHED_BATHROOM, "attached"),
    (R.LIVING_ROOM, R.BALCONY, "connected"),
    (R.MASTER_BEDROOM, R.BALCONY, "connected"),
    (R.LIVING_ROOM, R.STAIRCASE, "adjacent"),
    (R.LIVING_ROOM, R.FOYER, "connected"),
    (R.LIVING_ROOM, R.PARKING, "adjacent"),
    (R.PASSAGE, R.LIVING_ROOM, "connected"),
)


def derive_relationships(rooms: list[RoomPlacement]) -> list[RoomRelationship]:
    """Record the meaningful adjacencies that actually exist in the geometry."""
    seen: set[tuple[RoomType, RoomType, str]] = set()
    out: list[RoomRelationship] = []
    for source, target, relation in SEMANTIC_PAIRS:
        for a in rooms:
            if a.type is not source:
                continue
            for b in rooms:
                if b.type is not target or a is b:
                    continue
                if not a.shares_wall_with(b):
                    continue
                key = (source, target, relation)
                if key in seen:
                    continue
                seen.add(key)
                out.append(RoomRelationship(source=source, target=target, relation=relation))
    return out


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------
LIBRARY: list[dict] = [
    {
        "id": "TPL-001",
        "name": "Compact Single Bedroom Cottage",
        "bhk": BHKType.BHK1,
        "plot": (26, 29),
        "shape": PlotShape.SQUARE,
        "style": InteriorStyle.TRADITIONAL,
        "facing": Facing.SOUTH,
        "image": "1BHK(1).jpeg",
        "description": (
            "Single bedroom cottage with a double-height living hall running the full depth of "
            "the right bay, an internal staircase to the first floor, and a service spine of "
            "kitchen, bath and WC along the left wall. A shallow verandah screens the entrance."
        ),
        "rows": [
            (R.MASTER_BEDROOM, "Bed Room", 0, 19, 13, 10),
            (R.LIVING_ROOM, "Living Hall", 13, 7, 13, 14),
            (R.STAIRCASE, "Staircase", 13, 21, 13, 8),
            (R.COMMON_BATHROOM, "W.C.", 0, 15.5, 6, 3.5),
            (R.ATTACHED_BATHROOM, "Bath", 6, 15.5, 7, 3.5),
            (R.KITCHEN, "Kitchen", 0, 3, 13, 12.5),
            (R.BALCONY, "Verandah", 13, 0, 13, 7),
            (R.UTILITY_ROOM, "Kitchen Otta", 0, 0, 13, 3),
        ],
    },
    {
        "id": "TPL-002",
        "name": "Narrow Plot Two Bedroom with Covered Parking",
        "bhk": BHKType.BHK2,
        "plot": (25, 45),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.MODERN,
        "facing": Facing.EAST,
        "image": "2BHK(1).jpeg",
        "description": (
            "Classic 25x45 narrow-plot layout. Covered parking occupies the street edge, the "
            "living room opens off a side foyer, and both bedrooms stack along the right bay "
            "with the master taking the quieter middle position. Open kitchen adjoins dining."
        ),
        "rows": [
            (R.DINING_ROOM, "Dining", 0, 34, 11, 11),
            (R.KITCHEN, "Open Kitchen", 11, 37, 8, 8),
            (R.COMMON_BATHROOM, "Toilet", 19, 37, 6, 8),
            (R.CHILDREN_BEDROOM, "C. Bed Room", 11, 26, 14, 11),
            (R.ATTACHED_BATHROOM, "Toilet", 18, 21, 7, 5),
            (R.STAIRCASE, "Staircase", 11, 21, 7, 5),
            (R.MASTER_BEDROOM, "M. Bed Room", 11, 10, 14, 11),
            (R.LIVING_ROOM, "Living", 0, 19, 11, 15),
            (R.FOYER, "Foyer", 0, 10, 11, 9),
            (R.PARKING, "Parking", 0, 0, 14, 10),
            (R.GARDEN, "Garden", 14, 0, 11, 10),
        ],
    },
    {
        "id": "TPL-003",
        "name": "West Facing 832 sq ft Two Bedroom",
        "bhk": BHKType.BHK2,
        "plot": (26, 32),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.MINIMAL,
        "facing": Facing.WEST,
        "image": "2BHK(2).jpeg",
        "description": (
            "Efficient 832 sq ft west-facing plan. Two equal bedrooms share the left bay, the "
            "hall sits at the entrance corner with the kitchen behind it, and a dog-leg "
            "staircase is tucked under the rear-left corner to keep circulation off the hall."
        ),
        "rows": [
            (R.MASTER_BEDROOM, "Bedroom 1", 0, 21, 13, 11),
            (R.ATTACHED_BATHROOM, "Toilet", 13, 21, 6, 11),
            (R.COMMON_BATHROOM, "Toilet", 19, 26, 7, 6),
            (R.STORE_ROOM, "Store", 19, 21, 7, 5),
            (R.CHILDREN_BEDROOM, "Bedroom 2", 0, 11, 13, 10),
            (R.KITCHEN, "Kitchen", 13, 13, 13, 8),
            (R.LIVING_ROOM, "Hall", 13, 0, 13, 13),
            (R.STAIRCASE, "Staircase", 0, 0, 13, 11),
        ],
    },
    {
        "id": "TPL-004",
        "name": "20x40 Two Bedroom with Scooter Parking",
        "bhk": BHKType.BHK2,
        "plot": (20, 40),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.MODERN,
        "facing": Facing.EAST,
        "image": "2BHK(4).jpeg",
        "description": (
            "Tight 20 ft frontage handled with a single-loaded corridor: bedrooms sit front and "
            "rear, the drawing-cum-living room occupies the middle third, and the kitchen takes "
            "the rear-right corner for cross ventilation. Paved parking bay at the entrance."
        ),
        "rows": [
            (R.MASTER_BEDROOM, "Bedroom 1", 0, 29, 10, 11),
            (R.KITCHEN, "Kitchen", 10, 29, 10, 11),
            (R.ATTACHED_BATHROOM, "W.C / Bath", 0, 22, 5, 7),
            (R.COMMON_BATHROOM, "W.C", 0, 18, 5, 4),
            (R.LIVING_ROOM, "Drawing / Living", 5, 18, 15, 11),
            (R.CHILDREN_BEDROOM, "Bedroom 2", 0, 7, 11, 11),
            (R.WASH_AREA, "Wash Area", 11, 14, 9, 4),
            (R.PARKING, "Parking", 11, 0, 9, 14),
            (R.GARDEN, "Garden", 0, 0, 11, 7),
        ],
    },
    {
        "id": "TPL-005",
        "name": "900 sq ft Two Bedroom Comfort Apartment",
        "bhk": BHKType.BHK2,
        "plot": (30, 30),
        "shape": PlotShape.SQUARE,
        "style": InteriorStyle.MODERN,
        "facing": Facing.NORTH,
        "image": "2BHK(5).jpeg",
        "description": (
            "Square apartment footprint organised in three bays. Entry foyer and store screen "
            "the living room from the door, the living-dining runs through the middle bay to a "
            "balcony, and the master bedroom claims the far corner beside the kitchen bay."
        ),
        "rows": [
            (R.CHILDREN_BEDROOM, "Bedroom 2", 0, 17, 11, 13),
            (R.COMMON_BATHROOM, "Common Toilet", 0, 11, 11, 6),
            (R.STORE_ROOM, "Store", 0, 6, 11, 5),
            (R.FOYER, "Foyer", 0, 0, 11, 6),
            (R.ATTACHED_BATHROOM, "Toilet", 11, 24, 9, 6),
            (R.LIVING_ROOM, "Living", 11, 11, 9, 13),
            (R.BALCONY, "Balcony", 11, 0, 9, 11),
            (R.MASTER_BEDROOM, "Master Bedroom", 20, 18, 10, 12),
            (R.DINING_ROOM, "Dining", 20, 9, 10, 9),
            (R.KITCHEN, "Kitchen", 20, 0, 10, 9),
        ],
    },
    {
        "id": "TPL-006",
        "name": "Two Bedroom Apartment Unit with Twin Balconies",
        "bhk": BHKType.BHK2,
        "plot": (30, 40),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.MODERN,
        "facing": Facing.WEST,
        "image": "2BHK(6).jpeg",
        "description": (
            "Apartment unit lifted from a twin-unit floor plate. A central passage separates the "
            "sleeping wing from the living wing, the hall opens onto a rear balcony, and the "
            "kitchen sits beside the store with the wash area running the front edge."
        ),
        "rows": [
            (R.MASTER_BEDROOM, "Bedroom 1", 0, 28, 13, 12),
            (R.ATTACHED_BATHROOM, "L/B", 13, 34, 6, 6),
            (R.BALCONY, "Balcony", 19, 34, 11, 6),
            (R.LIVING_ROOM, "Hall", 13, 22, 17, 12),
            (R.DINING_ROOM, "Dining", 0, 18, 13, 10),
            (R.COMMON_BATHROOM, "Toilet", 13, 16, 6, 6),
            (R.PASSAGE, "Passage", 19, 16, 5, 6),
            (R.STAIRCASE, "Staircase", 24, 16, 6, 6),
            (R.CHILDREN_BEDROOM, "Bedroom 2", 0, 4, 13, 14),
            (R.KITCHEN, "Kitchen", 13, 4, 11, 12),
            (R.STORE_ROOM, "Store", 24, 4, 6, 12),
            (R.WASH_AREA, "Wash Area", 0, 0, 30, 4),
        ],
    },
    {
        "id": "TPL-007",
        "name": "50x60 Three Bedroom Bungalow",
        "bhk": BHKType.BHK3,
        "plot": (50, 60),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.LUXURY,
        "facing": Facing.SOUTH,
        "image": "3BHK(1).jpeg",
        "description": (
            "Generous bungalow with a formal drawing/office off the entrance, a double-volume "
            "dining core lit by the rear backyard, and three bedrooms placed on three different "
            "corners for privacy. Two-car parking and a front sitout complete the street face."
        ),
        "rows": [
            (R.MASTER_BEDROOM, "Bedroom", 0, 45, 14, 15),
            (R.GARDEN, "Backyard", 14, 50, 18, 10),
            (R.CHILDREN_BEDROOM, "Bedroom", 32, 45, 18, 15),
            (R.KITCHEN, "Kitchen", 0, 32, 14, 13),
            (R.DINING_ROOM, "Dining", 14, 30, 18, 20),
            (R.STUDY_ROOM, "Drawing / Office", 32, 28, 18, 17),
            (R.LIVING_ROOM, "Living", 14, 10, 18, 20),
            (R.GUEST_BEDROOM, "Bedroom", 0, 14, 14, 18),
            (R.ATTACHED_BATHROOM, "Bath", 0, 8, 7, 6),
            (R.COMMON_BATHROOM, "Bath", 7, 8, 7, 6),
            (R.BALCONY, "Sitout", 14, 0, 10, 10),
            (R.STAIRCASE, "Staircase", 24, 0, 8, 10),
            (R.PARKING, "Parking", 32, 0, 18, 28),
            (R.GARDEN, "Garden", 0, 0, 14, 8),
        ],
    },
    {
        "id": "TPL-008",
        "name": "1600 sq ft North Facing Three Bedroom",
        "bhk": BHKType.BHK3,
        "plot": (34, 47),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.MODERN,
        "facing": Facing.NORTH,
        "image": "3BHK(3).jpeg",
        "description": (
            "North-facing 1600 sq ft plan with a formal drawing room at the rear, living and "
            "dining stacked along the spine, and the master suite kept on the quiet left flank. "
            "Kitchen backs onto a dedicated utility with a balcony overlooking the front garden."
        ),
        "rows": [
            (R.CHILDREN_BEDROOM, "Bedroom", 0, 35, 12, 12),
            (R.STUDY_ROOM, "Drawing", 12, 35, 10, 12),
            (R.GUEST_BEDROOM, "Bedroom", 22, 35, 12, 12),
            (R.COMMON_BATHROOM, "Toilet", 0, 29, 8, 6),
            (R.ATTACHED_BATHROOM, "Toilet", 22, 29, 6, 6),
            (R.ATTACHED_BATHROOM, "Toilet", 28, 29, 6, 6),
            (R.MASTER_BEDROOM, "M. Bedroom", 0, 14, 12, 15),
            (R.DINING_ROOM, "Dining", 12, 25, 10, 10),
            (R.LIVING_ROOM, "Living", 12, 15, 10, 10),
            (R.KITCHEN, "Kitchen", 22, 17, 8, 12),
            (R.UTILITY_ROOM, "Utility", 30, 17, 4, 12),
            (R.BALCONY, "Balcony", 12, 8, 12, 7),
            (R.POOJA_ROOM, "Pooja", 0, 8, 12, 6),
            (R.PARKING, "Parking", 24, 0, 10, 17),
            (R.GARDEN, "Garden", 0, 0, 24, 8),
        ],
    },
    {
        "id": "TPL-009",
        "name": "Three Bedroom Deck Apartment",
        "bhk": BHKType.BHK3,
        "plot": (40, 30),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.LUXURY,
        "facing": Facing.EAST,
        "image": "3BHK(5).jpeg",
        "description": (
            "Wide-frontage apartment with a through living-dining flanked by decks on both "
            "faces. A service bay carries kitchen, utility and the common toilet, while the "
            "master and second bedroom share the far column with a toilet between them."
        ),
        "rows": [
            (R.CHILDREN_BEDROOM, "Bedroom 3", 0, 16, 11, 14),
            (R.ATTACHED_BATHROOM, "Toilet 1", 0, 8, 6, 8),
            (R.STORE_ROOM, "Store", 6, 8, 5, 8),
            (R.FOYER, "Foyer", 0, 0, 11, 8),
            (R.BALCONY, "Wide Deck", 11, 24, 13, 6),
            (R.LIVING_ROOM, "Living / Dining", 11, 6, 13, 18),
            (R.BALCONY, "Wide Deck", 11, 0, 13, 6),
            (R.KITCHEN, "Kitchen", 24, 20, 8, 10),
            (R.UTILITY_ROOM, "Utility", 24, 14, 8, 6),
            (R.COMMON_BATHROOM, "Toilet", 24, 7, 8, 7),
            (R.PASSAGE, "Wide Passage", 24, 0, 8, 7),
            (R.MASTER_BEDROOM, "Bedroom 1", 32, 18, 8, 12),
            (R.ATTACHED_BATHROOM, "Toilet 2", 32, 12, 8, 6),
            (R.GUEST_BEDROOM, "Bedroom 2", 32, 0, 8, 12),
        ],
    },
    {
        "id": "TPL-010",
        "name": "1800 sq ft East Facing Three Bedroom",
        "bhk": BHKType.BHK3,
        "plot": (36, 50),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.TRADITIONAL,
        "facing": Facing.EAST,
        "image": "3BHK(6).jpeg",
        "description": (
            "East-facing 1800 sq ft house arranged around a large living hall at the entrance. "
            "Three bedrooms occupy the rear half with two toilets between them, the dining hall "
            "sits centrally, and a pooja room is set beside the kitchen as tradition dictates."
        ),
        "rows": [
            (R.MASTER_BEDROOM, "Bedroom 1", 0, 36, 13, 14),
            (R.ATTACHED_BATHROOM, "Toilet", 13, 41, 5, 9),
            (R.ATTACHED_BATHROOM, "Toilet", 18, 41, 5, 9),
            (R.CHILDREN_BEDROOM, "Bedroom 2", 23, 36, 13, 14),
            (R.UTILITY_ROOM, "Utility Space", 13, 36, 10, 5),
            (R.COMMON_BATHROOM, "C Toilet", 0, 30, 11, 6),
            (R.STUDY_ROOM, "Verandah Study", 0, 18, 11, 12),
            (R.DINING_ROOM, "Dining Hall", 11, 24, 12, 12),
            (R.GUEST_BEDROOM, "Bedroom 3", 23, 24, 13, 12),
            (R.POOJA_ROOM, "Puja", 11, 18, 6, 6),
            (R.LIVING_ROOM, "Living Hall", 17, 10, 19, 14),
            (R.KITCHEN, "Kitchen", 0, 10, 17, 8),
            (R.STAIRCASE, "Stairs", 0, 0, 17, 10),
            (R.BALCONY, "Open Space", 17, 0, 19, 10),
        ],
    },
    {
        "id": "TPL-011",
        "name": "40x60 Modern Three Bedroom Villa",
        "bhk": BHKType.BHK3,
        "plot": (40, 60),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.MODERN,
        "facing": Facing.EAST,
        "image": "3BHK(7).jpeg",
        "description": (
            "2400 sq ft single-storey villa. Every bedroom gets its own dress area and toilet, "
            "the living hall sits at the crossing of both circulation axes, and the service zone "
            "of kitchen, wash and utility is pushed to the rear away from the parking court."
        ),
        "rows": [
            (R.STORE_ROOM, "Dress", 0, 44, 7, 8),
            (R.ATTACHED_BATHROOM, "Toilet", 0, 52, 7, 8),
            (R.MASTER_BEDROOM, "Bed Room 1", 7, 44, 15, 16),
            (R.CHILDREN_BEDROOM, "Bed Room 2", 22, 44, 12, 16),
            (R.ATTACHED_BATHROOM, "Toilet", 34, 44, 6, 8),
            (R.BALCONY, "Balcony", 34, 52, 6, 8),
            (R.ATTACHED_BATHROOM, "Toilet", 0, 36, 7, 8),
            (R.STORE_ROOM, "Dress", 7, 36, 7, 8),
            (R.LIVING_ROOM, "Living Hall", 14, 28, 20, 16),
            (R.STUDY_ROOM, "Study", 34, 36, 6, 8),
            (R.COMMON_BATHROOM, "Toilet", 34, 28, 6, 8),
            (R.GUEST_BEDROOM, "Bed Room 3", 0, 20, 14, 16),
            (R.PASSAGE, "Passage", 0, 14, 14, 6),
            (R.KITCHEN, "Kitchen", 14, 14, 12, 14),
            (R.UTILITY_ROOM, "Store", 26, 20, 8, 8),
            (R.WASH_AREA, "Wash", 26, 14, 8, 6),
            (R.STAIRCASE, "Stairs", 34, 14, 6, 14),
            (R.PARKING, "Parking", 0, 0, 26, 14),
            (R.GARDEN, "Garden", 26, 0, 14, 14),
        ],
    },
    {
        "id": "TPL-012",
        "name": "48x52 Four Bedroom Family House",
        "bhk": BHKType.BHK4,
        "plot": (48, 52),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.TRADITIONAL,
        "facing": Facing.SOUTH,
        "image": "4BHK(2).jpeg",
        "description": (
            "Four bedroom house built around a tall central hall. Two master suites take the "
            "rear corners, a third bedroom and the guest room sit on the left flank, and the "
            "drawing room and kitchen line the front verandah that runs the full width."
        ),
        "rows": [
            (R.ATTACHED_BATHROOM, "Bath / WC", 0, 44, 8, 8),
            (R.ATTACHED_BATHROOM, "Bath / WC", 0, 36, 8, 8),
            (R.PASSAGE, "Passage", 8, 36, 6, 16),
            (R.MASTER_BEDROOM, "Bedroom 1", 14, 36, 17, 16),
            (R.CHILDREN_BEDROOM, "Bedroom 2", 31, 36, 17, 16),
            (R.STORE_ROOM, "Store", 0, 22, 8, 7),
            (R.ATTACHED_BATHROOM, "Bath / WC", 0, 29, 8, 7),
            (R.BEDROOM, "Bedroom 3", 8, 22, 14, 14),
            (R.LIVING_ROOM, "Hall", 22, 20, 16, 16),
            (R.ATTACHED_BATHROOM, "Bath / WC", 38, 28, 10, 8),
            (R.COMMON_BATHROOM, "Bath / WC", 38, 20, 10, 8),
            (R.GUEST_BEDROOM, "Guestroom", 0, 8, 16, 14),
            (R.STUDY_ROOM, "Drawing", 16, 8, 16, 12),
            (R.KITCHEN, "Kitchen", 32, 8, 16, 12),
            (R.BALCONY, "Varanda", 0, 0, 48, 8),
        ],
    },
    {
        "id": "TPL-013",
        "name": "Four Bedroom Luxury Apartment",
        "bhk": BHKType.BHK4,
        "plot": (42, 32),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.LUXURY,
        "facing": Facing.NORTH,
        "image": "4BHK(3).jpeg",
        "description": (
            "Wide luxury apartment with a 16x18 ft living-dining at its heart. Three bedrooms "
            "ring the living space with attached toilets, the fourth sits beside the entrance, "
            "and the kitchen block carries its own utility along the outside wall."
        ),
        "rows": [
            (R.MASTER_BEDROOM, "M. Bedroom", 0, 0, 14, 12),
            (R.ATTACHED_BATHROOM, "Toilet", 14, 0, 6, 6),
            (R.BALCONY, "Balcony", 20, 0, 10, 6),
            (R.FOYER, "Foyer", 30, 0, 12, 6),
            (R.BEDROOM, "Bedroom 4", 0, 12, 14, 8),
            (R.COMMON_BATHROOM, "W.C", 0, 20, 6, 6),
            (R.UTILITY_ROOM, "Utility", 0, 26, 6, 6),
            (R.KITCHEN, "Kitchen", 6, 20, 8, 12),
            (R.LIVING_ROOM, "Living / Dining", 14, 6, 16, 18),
            (R.PASSAGE, "Passage", 30, 6, 12, 8),
            (R.ATTACHED_BATHROOM, "Toilet", 30, 14, 6, 8),
            (R.BALCONY, "Balcony", 36, 14, 6, 8),
            (R.CHILDREN_BEDROOM, "Bedroom 3", 30, 22, 12, 10),
            (R.ATTACHED_BATHROOM, "Toilet", 24, 24, 6, 8),
            (R.GUEST_BEDROOM, "Bedroom 2", 14, 24, 10, 8),
        ],
    },
    {
        "id": "TPL-014",
        "name": "2122 sq ft Four Bedroom Penthouse",
        "bhk": BHKType.BHK4,
        "plot": (45, 50),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.LUXURY,
        "facing": Facing.WEST,
        "image": "4BHK(4).jpeg",
        "description": (
            "Penthouse-style floor with a 26x24 ft living room opening onto two balconies. The "
            "bedroom wing runs the full right flank so every room gets an attached toilet on the "
            "outside wall, and the kitchen, utility and servant room form a self-contained bay."
        ),
        "rows": [
            (R.BALCONY, "Balcony", 0, 44, 14, 6),
            (R.BALCONY, "Balcony", 14, 44, 12, 6),
            (R.LIVING_ROOM, "Living", 0, 20, 26, 24),
            (R.MASTER_BEDROOM, "M. Bed Rm.", 26, 36, 14, 14),
            (R.ATTACHED_BATHROOM, "Toilet", 40, 36, 5, 6),
            (R.STORE_ROOM, "Dress", 40, 42, 5, 8),
            (R.DINING_ROOM, "Dining", 26, 24, 14, 12),
            (R.ATTACHED_BATHROOM, "Toilet", 40, 26, 5, 10),
            (R.CHILDREN_BEDROOM, "Bed Room 2", 26, 12, 14, 12),
            (R.ATTACHED_BATHROOM, "Toilet", 40, 16, 5, 10),
            (R.GUEST_BEDROOM, "Bed Room 3", 26, 0, 14, 12),
            (R.ATTACHED_BATHROOM, "Toilet", 40, 6, 5, 10),
            (R.COMMON_BATHROOM, "Toilet", 40, 0, 5, 6),
            (R.BEDROOM, "Bed Room 4", 12, 6, 14, 14),
            (R.KITCHEN, "Kitchen", 0, 6, 12, 14),
            (R.UTILITY_ROOM, "Utility", 0, 0, 8, 6),
            (R.STORE_ROOM, "Servant", 8, 0, 8, 6),
            (R.FOYER, "Foyer", 16, 0, 10, 6),
        ],
    },
    {
        "id": "TPL-015",
        "name": "40x45 Four Bedroom with Three Balconies",
        "bhk": BHKType.BHK4,
        "plot": (40, 45),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.MODERN,
        "facing": Facing.NORTH,
        "image": "4BHK(5).jpeg",
        "description": (
            "Four bedroom floor organised as three parallel bays. The middle bay carries the "
            "circulation and dining, the left bay holds the master suite with its dressing area, "
            "and the right bay stacks the second bedroom over the kitchen and service block."
        ),
        "rows": [
            (R.ATTACHED_BATHROOM, "Att. Toilet", 0, 36, 8, 9),
            (R.BALCONY, "Balcony", 8, 36, 6, 9),
            (R.MASTER_BEDROOM, "M. Bed Room 4", 0, 20, 14, 16),
            (R.STORE_ROOM, "Dressing Area", 0, 14, 14, 6),
            (R.BEDROOM, "Bed Room 1", 0, 0, 14, 14),
            (R.CHILDREN_BEDROOM, "M. Bed Room 3", 14, 32, 12, 13),
            (R.PASSAGE, "Passage", 14, 26, 12, 6),
            (R.DINING_ROOM, "Dining Area", 14, 14, 12, 12),
            (R.LIVING_ROOM, "Drawing Room", 14, 0, 12, 14),
            (R.GUEST_BEDROOM, "M. Bed Room 2", 26, 33, 14, 12),
            (R.ATTACHED_BATHROOM, "Att. Toilet", 26, 26, 7, 7),
            (R.WASH_AREA, "Wash Area", 33, 26, 7, 7),
            (R.KITCHEN, "Kitchen", 26, 14, 10, 12),
            (R.COMMON_BATHROOM, "G. Toilet", 36, 20, 4, 6),
            (R.STORE_ROOM, "Store Room", 36, 14, 4, 6),
            (R.BALCONY, "Balcony", 26, 8, 14, 6),
            (R.FOYER, "Foyer", 26, 0, 14, 8),
        ],
    },
    {
        "id": "TPL-016",
        "name": "42x50 Two Bedroom with Lobby and Porch",
        "bhk": BHKType.BHK2,
        "plot": (42, 50),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.TRADITIONAL,
        "facing": Facing.SOUTH,
        "image": "4BHK(6).jpeg",
        "description": (
            "Spacious two bedroom ground floor. A 26 ft lobby doubles as the dining space and "
            "links the drawing room to both bedrooms, a pooja niche faces east, and a deep car "
            "porch runs beside the lawn along the entire street elevation."
        ),
        "rows": [
            (R.MASTER_BEDROOM, "Bedroom", 0, 36, 16, 14),
            (R.ATTACHED_BATHROOM, "Toilet", 16, 42, 8, 8),
            (R.STAIRCASE, "Staircase", 16, 36, 8, 6),
            (R.CHILDREN_BEDROOM, "Bedroom", 24, 36, 18, 14),
            (R.COMMON_BATHROOM, "Bath Room", 0, 30, 8, 6),
            (R.ATTACHED_BATHROOM, "Toilet", 0, 22, 8, 8),
            (R.DINING_ROOM, "Lobby / Dining", 8, 22, 26, 14),
            (R.POOJA_ROOM, "Pooja", 34, 22, 8, 14),
            (R.KITCHEN, "Kitchen", 0, 6, 10, 16),
            (R.LIVING_ROOM, "Drawing Room", 10, 6, 18, 16),
            (R.PARKING, "Porch", 28, 0, 14, 22),
            (R.GARDEN, "Lawn", 0, 0, 28, 6),
        ],
    },
    # --- Architect-standard additions -------------------------------------
    {
        "id": "TPL-017",
        "name": "22x35 Compact Studio Bedroom Home",
        "bhk": BHKType.BHK1,
        "plot": (22, 35),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.MINIMAL,
        "facing": Facing.EAST,
        "image": None,
        "description": (
            "Minimal single-bedroom home for a narrow urban plot. The living and dining share an "
            "open L, the kitchen backs onto the bedroom's service wall to shorten the plumbing "
            "run, and a slim balcony off the bedroom brings light into the rear half."
        ),
        "rows": [
            (R.MASTER_BEDROOM, "Master Bedroom", 0, 23, 12, 12),
            (R.ATTACHED_BATHROOM, "Attached Bath", 12, 28, 6, 7),
            (R.BALCONY, "Balcony", 18, 28, 4, 7),
            (R.KITCHEN, "Kitchen", 12, 19, 10, 9),
            (R.LIVING_ROOM, "Living Room", 0, 8, 12, 15),
            (R.DINING_ROOM, "Dining", 12, 8, 10, 11),
            (R.COMMON_BATHROOM, "Common Bath", 0, 0, 6, 8),
            (R.STAIRCASE, "Staircase", 6, 0, 8, 8),
            (R.UTILITY_ROOM, "Utility", 14, 0, 8, 8),
        ],
    },
    {
        "id": "TPL-018",
        "name": "24x40 Minimal Two Bedroom",
        "bhk": BHKType.BHK2,
        "plot": (24, 40),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.MINIMAL,
        "facing": Facing.NORTH,
        "image": None,
        "description": (
            "Pared-back two bedroom plan with a single straight corridor. Bedrooms occupy the "
            "quiet rear, the living room sits centrally between the dining and the balcony, and "
            "all wet areas are stacked in one vertical band for an economical service core."
        ),
        "rows": [
            (R.MASTER_BEDROOM, "Master Bedroom", 0, 28, 12, 12),
            (R.ATTACHED_BATHROOM, "Attached Bath", 12, 34, 6, 6),
            (R.UTILITY_ROOM, "Utility", 18, 34, 6, 6),
            (R.KITCHEN, "Kitchen", 12, 24, 12, 10),
            (R.CHILDREN_BEDROOM, "Children Bedroom", 0, 16, 12, 12),
            (R.LIVING_ROOM, "Living Room", 12, 10, 12, 14),
            (R.DINING_ROOM, "Dining", 0, 6, 12, 10),
            (R.COMMON_BATHROOM, "Common Bath", 12, 4, 6, 6),
            (R.STAIRCASE, "Staircase", 18, 4, 6, 6),
            (R.BALCONY, "Balcony", 0, 0, 12, 6),
            (R.WASH_AREA, "Wash Area", 12, 0, 12, 4),
        ],
    },
    {
        "id": "TPL-019",
        "name": "30x45 Traditional Three Bedroom with Pooja",
        "bhk": BHKType.BHK3,
        "plot": (30, 45),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.TRADITIONAL,
        "facing": Facing.EAST,
        "image": None,
        "description": (
            "Vastu-minded three bedroom home. The pooja room occupies the north-east corner, the "
            "kitchen the south-east, and the master bedroom the south-west. A square living room "
            "anchors the middle with the dining room opening directly off the kitchen."
        ),
        "rows": [
            (R.MASTER_BEDROOM, "Master Bedroom", 0, 32, 14, 13),
            (R.ATTACHED_BATHROOM, "Attached Bath", 14, 38, 8, 7),
            (R.POOJA_ROOM, "Pooja Room", 22, 38, 8, 7),
            (R.GUEST_BEDROOM, "Guest Bedroom", 14, 26, 16, 12),
            (R.ATTACHED_BATHROOM, "Attached Bath", 0, 24, 7, 8),
            (R.STAIRCASE, "Staircase", 7, 24, 7, 8),
            (R.CHILDREN_BEDROOM, "Children Bedroom", 0, 10, 14, 14),
            (R.LIVING_ROOM, "Living Room", 14, 12, 16, 14),
            (R.DINING_ROOM, "Dining Room", 0, 0, 14, 10),
            (R.KITCHEN, "Kitchen", 14, 0, 10, 12),
            (R.COMMON_BATHROOM, "Common Bath", 24, 6, 6, 6),
            (R.WASH_AREA, "Wash Area", 24, 0, 6, 6),
        ],
    },
    {
        "id": "TPL-020",
        "name": "50x60 Four Bedroom Luxury Villa",
        "bhk": BHKType.BHK4,
        "plot": (50, 60),
        "shape": PlotShape.RECTANGLE,
        "style": InteriorStyle.LUXURY,
        "facing": Facing.NORTH,
        "image": None,
        "description": (
            "Large villa floor with a 22x20 ft living room at the centre of a cross plan. All "
            "four bedrooms have attached bathrooms on external walls, a formal dining sits "
            "beside the kitchen and utility bay, and covered parking flanks the front lawn."
        ),
        "rows": [
            (R.MASTER_BEDROOM, "Master Bedroom", 0, 44, 18, 16),
            (R.PASSAGE, "Passage", 18, 44, 8, 8),
            (R.ATTACHED_BATHROOM, "Attached Bath", 18, 52, 8, 8),
            (R.BALCONY, "Balcony", 26, 52, 10, 8),
            (R.ATTACHED_BATHROOM, "Attached Bath", 26, 44, 10, 8),
            (R.CHILDREN_BEDROOM, "Children Bedroom", 36, 44, 14, 16),
            (R.LIVING_ROOM, "Living Room", 14, 24, 22, 20),
            (R.DINING_ROOM, "Dining Room", 0, 26, 14, 18),
            (R.GUEST_BEDROOM, "Guest Bedroom", 36, 28, 14, 16),
            (R.ATTACHED_BATHROOM, "Attached Bath", 36, 20, 8, 8),
            (R.POOJA_ROOM, "Pooja Room", 44, 20, 6, 8),
            (R.KITCHEN, "Kitchen", 0, 16, 14, 10),
            (R.UTILITY_ROOM, "Utility", 0, 10, 8, 6),
            (R.STORE_ROOM, "Store", 8, 10, 6, 6),
            (R.BEDROOM, "Bedroom 4", 14, 10, 14, 14),
            (R.STAIRCASE, "Staircase", 28, 10, 8, 6),
            (R.ATTACHED_BATHROOM, "Attached Bath", 28, 16, 8, 8),
            (R.COMMON_BATHROOM, "Common Bath", 36, 12, 8, 8),
            (R.WASH_AREA, "Wash Area", 44, 12, 6, 8),
            (R.PARKING, "Parking", 36, 0, 14, 12),
            (R.GARDEN, "Lawn", 0, 0, 36, 10),
        ],
    },
]


def build(entry: dict) -> FloorPlanTemplate:
    rooms = _rooms(entry["rows"])
    width, length = entry["plot"]
    template = FloorPlanTemplate(
        id=entry["id"],
        name=entry["name"],
        bhk=entry["bhk"],
        plot_width_ft=width,
        plot_length_ft=length,
        plot_shape=entry["shape"],
        style=entry["style"],
        rooms=rooms,
        relationships=derive_relationships(rooms),
        description=entry["description"],
        metadata=TemplateMetadata(
            source="traced-reference" if entry["image"] else "architect-standard",
            reference_image=entry["image"],
            facing=entry["facing"],
            floors=1,
            built_up_sqft=round(
                sum(r.area for r in rooms if not r.type.is_outdoor),
                2,
            ),
            tags=[entry["bhk"].value, entry["style"].value, entry["facing"].value],
        ),
    )
    return template


def assert_no_overlaps(template: FloorPlanTemplate) -> None:
    for i, a in enumerate(template.rooms):
        for b in template.rooms[i + 1 :]:
            if a.overlaps(b):
                raise SystemExit(
                    f"{template.id}: '{a.name}' overlaps '{b.name}' "
                    f"({a.x},{a.y},{a.width},{a.height}) vs ({b.x},{b.y},{b.width},{b.height})"
                )


def main() -> None:
    out_dir = Path(__file__).resolve().parents[1] / "data" / "templates"
    out_dir.mkdir(parents=True, exist_ok=True)

    for stale in out_dir.glob("TPL-*.json"):
        stale.unlink()

    for entry in LIBRARY:
        template = build(entry)
        assert_no_overlaps(template)
        payload = template.model_dump(mode="json")
        (out_dir / f"{template.id}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        coverage = template.built_up_area / template.plot_area * 100
        print(
            f"{template.id}  {template.bhk.value:5s} "
            f"{template.plot_width_ft:g}x{template.plot_length_ft:g}  "
            f"{len(template.rooms):2d} rooms  {coverage:5.1f}% built-up  {template.name}"
        )

    print(f"\nWrote {len(LIBRARY)} templates to {out_dir}")


if __name__ == "__main__":
    main()
