"""Controlled vocabularies shared by the UI, the knowledge base and the AI layer.

The frontend renders its dropdowns/checkboxes straight from these values via
``GET /api/v1/options``, so adding an option here is enough to surface it in the
wizard - no duplicated lists on the client.
"""

from __future__ import annotations

from enum import Enum


class PlotShape(str, Enum):
    RECTANGLE = "rectangle"
    SQUARE = "square"


class BHKType(str, Enum):
    BHK1 = "1BHK"
    BHK2 = "2BHK"
    BHK3 = "3BHK"
    BHK4 = "4BHK"

    @property
    def bedroom_count(self) -> int:
        return int(self.value[0])


class RoomType(str, Enum):
    """Every room the planner knows how to place, label and score."""

    LIVING_ROOM = "living_room"
    DINING_ROOM = "dining_room"
    KITCHEN = "kitchen"
    MASTER_BEDROOM = "master_bedroom"
    GUEST_BEDROOM = "guest_bedroom"
    CHILDREN_BEDROOM = "children_bedroom"
    BEDROOM = "bedroom"
    STUDY_ROOM = "study_room"
    POOJA_ROOM = "pooja_room"
    UTILITY_ROOM = "utility_room"
    STORE_ROOM = "store_room"
    ATTACHED_BATHROOM = "attached_bathroom"
    COMMON_BATHROOM = "common_bathroom"
    BALCONY = "balcony"
    PARKING = "parking"
    GARDEN = "garden"
    STAIRCASE = "staircase"
    WASH_AREA = "wash_area"
    PASSAGE = "passage"
    FOYER = "foyer"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()

    @property
    def is_bedroom(self) -> bool:
        return self in _BEDROOM_TYPES

    @property
    def is_bathroom(self) -> bool:
        return self in {RoomType.ATTACHED_BATHROOM, RoomType.COMMON_BATHROOM}

    @property
    def is_outdoor(self) -> bool:
        return self in {RoomType.BALCONY, RoomType.PARKING, RoomType.GARDEN}


_BEDROOM_TYPES = {
    RoomType.MASTER_BEDROOM,
    RoomType.GUEST_BEDROOM,
    RoomType.CHILDREN_BEDROOM,
    RoomType.BEDROOM,
}

#: Rooms the user can explicitly tick in the "Required Rooms" step.
SELECTABLE_ROOMS: tuple[RoomType, ...] = (
    RoomType.LIVING_ROOM,
    RoomType.DINING_ROOM,
    RoomType.KITCHEN,
    RoomType.MASTER_BEDROOM,
    RoomType.GUEST_BEDROOM,
    RoomType.CHILDREN_BEDROOM,
    RoomType.STUDY_ROOM,
    RoomType.POOJA_ROOM,
    RoomType.UTILITY_ROOM,
    RoomType.STORE_ROOM,
)

#: Rooms exposed in the "Additional Features" step.
SELECTABLE_FEATURES: tuple[RoomType, ...] = (
    RoomType.BALCONY,
    RoomType.PARKING,
    RoomType.GARDEN,
    RoomType.STAIRCASE,
    RoomType.WASH_AREA,
)


class InteriorStyle(str, Enum):
    MODERN = "modern"
    MINIMAL = "minimal"
    LUXURY = "luxury"
    TRADITIONAL = "traditional"

    @property
    def label(self) -> str:
        return self.value.title()


class Facing(str, Enum):
    """Optional plot orientation - drives entry placement in the geometry engine."""

    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"
    ANY = "any"


class VastuPrinciple(str, Enum):
    """The directional rules the planner knows how to honour.

    Each one names a room family and the compass zone Vastu Shastra places it
    in. Plans are drawn north-up (see :mod:`app.geometry.vastu`), so every rule
    here is a statement about where a room should sit on the drawing.
    """

    POOJA_NORTHEAST = "pooja_northeast"
    KITCHEN_SOUTHEAST = "kitchen_southeast"
    MASTER_BEDROOM_SOUTHWEST = "master_bedroom_southwest"
    LIVING_NORTHEAST = "living_northeast"
    BATHROOMS_NORTHWEST = "bathrooms_northwest"
    STORAGE_SOUTHWEST = "storage_southwest"

    @property
    def label(self) -> str:
        return _VASTU_LABELS[self]


_VASTU_LABELS: dict[VastuPrinciple, str] = {
    VastuPrinciple.POOJA_NORTHEAST: "Pooja room in the north-east",
    VastuPrinciple.KITCHEN_SOUTHEAST: "Kitchen in the south-east",
    VastuPrinciple.MASTER_BEDROOM_SOUTHWEST: "Master bedroom in the south-west",
    VastuPrinciple.LIVING_NORTHEAST: "Living room towards the north-east",
    VastuPrinciple.BATHROOMS_NORTHWEST: "Bathrooms towards the north-west",
    VastuPrinciple.STORAGE_SOUTHWEST: "Heavy storage in the south-west",
}

#: The principles offered in the wizard, in the order they are shown.
SELECTABLE_VASTU_PRINCIPLES: tuple[VastuPrinciple, ...] = tuple(VastuPrinciple)
