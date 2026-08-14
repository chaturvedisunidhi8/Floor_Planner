"""Turn a brief plus a matched template into solver input.

A :class:`Programme` is the list of rooms the solver must place (with every
hard floor and the objective target) *and* the access model that describes how
people move between them (Milestone C):

* :attr:`Programme.access_requirements` - pairs/any-of that MUST share a wall
  long enough to take a door. These are the edges of the intended access graph
  and become hard CP-SAT constraints, so every accepted plan is walkable from
  the entrance by construction.
* :attr:`Programme.preferred_pairs` - pairs that read best as neighbours. Only
  a scoring hook, never a rejection.
* :attr:`Programme.forbidden_pairs` - pairs that should not share a wall
  (bathroom against the social core). A scoring hook and a benchmark metric.

The template supplies the room *set* (which rooms belong together); the brief
supplies the *numbers* (sizes, bathroom counts, features). Bedrooms are
retyped to match the BHK the same way the legacy engine does.

Milestone C deliberately does **not** expand the topology search: the 20
templates stay the only candidate arrangements. The access model is what turns
each one from a rectangle packing into an actually accessible plan.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.geometry.connectivity import CIRCULATION_TYPES
from app.geometry.primitives import Rect
from app.geometry.units import max_area, min_area, min_side, natural_area
from app.schemas.enums import RoomType
from app.schemas.requirements import FloorPlanRequirements
from app.schemas.template import FloorPlanTemplate

#: Bedrooms in descending order of desirability - the largest template bedroom
#: becomes the master, the next the children's room, and so on.
BEDROOM_PRIORITY: tuple[RoomType, ...] = (
    RoomType.MASTER_BEDROOM,
    RoomType.CHILDREN_BEDROOM,
    RoomType.GUEST_BEDROOM,
    RoomType.BEDROOM,
)

#: Rooms that may be dropped when the client did not request them.
OPTIONAL_ROOMS: frozenset[RoomType] = frozenset(
    {
        RoomType.POOJA_ROOM,
        RoomType.STUDY_ROOM,
        RoomType.STORE_ROOM,
        RoomType.UTILITY_ROOM,
        RoomType.WASH_AREA,
        RoomType.BALCONY,
        RoomType.PARKING,
        RoomType.GARDEN,
        RoomType.STAIRCASE,
        RoomType.DINING_ROOM,
    }
)

#: Room pairs that read best as neighbours. Only a scoring hook in Milestone A;
#: Milestone B turns these into hard connectivity constraints.
ADJACENCY_RULES: tuple[tuple[RoomType, RoomType], ...] = (
    (RoomType.ATTACHED_BATHROOM, RoomType.MASTER_BEDROOM),
    (RoomType.ATTACHED_BATHROOM, RoomType.GUEST_BEDROOM),
    (RoomType.ATTACHED_BATHROOM, RoomType.CHILDREN_BEDROOM),
    (RoomType.ATTACHED_BATHROOM, RoomType.BEDROOM),
    (RoomType.KITCHEN, RoomType.DINING_ROOM),
    (RoomType.DINING_ROOM, RoomType.LIVING_ROOM),
    (RoomType.KITCHEN, RoomType.LIVING_ROOM),
    (RoomType.PASSAGE, RoomType.LIVING_ROOM),
)

#: Rooms a bathroom should never share a wall with, for the privacy score.
PRIVACY_AWAY_FROM: frozenset[RoomType] = frozenset(
    {RoomType.LIVING_ROOM, RoomType.DINING_ROOM}
)

#: Feet of shared wall below which a doorway is not worth cutting. Every access
#: requirement enforces this overlap in the solver, so a valid plan always has
#: a real place to cut the door that connects the two rooms.
MIN_OPENING = 2.5


@dataclass(frozen=True)
class RoomSpec:
    """Everything the solver knows about one room in the brief.

    Hard constraints are ``min_side``, ``min_area`` and (when the brief sized
    the room) ``target_long``/``target_short``. ``target_area`` is only the
    objective's aim - the solver may land anywhere at or above the floor.
    """

    type: RoomType
    name: str
    target_area: float
    min_side: float
    min_area: float
    max_area: float = float("inf")
    outdoor: bool = False
    sized: bool = False
    target_long: float | None = None
    target_short: float | None = None


@dataclass(frozen=True)
class AccessRequirement:
    """One rule the access graph must satisfy.

    ``room`` must share a wall long enough to take a door with **at least one**
    of ``candidates``. A single candidate makes it a required adjacency; several
    make it an "attach to whichever circulation room fits" rule, which is what
    lets a bedroom open off a corridor without dictating which one.

    Indices are into :attr:`Programme.specs`.
    """

    room: int
    candidates: tuple[int, ...] = ()


@dataclass(frozen=True)
class Programme:
    """One candidate topology to solve."""

    specs: list[RoomSpec] = field(default_factory=list)
    #: The intended access graph, as hard adjacency rules for the solver.
    access_requirements: tuple[AccessRequirement, ...] = ()
    #: Indices into ``specs`` that ideally sit next to each other.
    adjacency_pairs: tuple[tuple[int, int], ...] = ()
    #: Indices into ``specs`` that should not share a wall.
    forbidden_pairs: tuple[tuple[int, int], ...] = ()
    #: Index of the room the front door leads into - the root of the access
    #: graph and where walkability is measured from.
    entrance_index: int = 0


def _spec_for(room_type: RoomType, targets: dict[RoomType, object]) -> RoomSpec:
    target = targets.get(room_type)
    sized = target is not None
    return RoomSpec(
        type=room_type,
        name=room_type.label,
        target_area=target.area if sized else natural_area(room_type),
        min_side=min_side(room_type),
        min_area=min_area(room_type),
        max_area=max_area(room_type),
        outdoor=room_type.is_outdoor,
        sized=sized,
        target_long=target.long_side if sized else None,
        target_short=target.short_side if sized else None,
    )


def _retype_bedrooms(
    rects: list[Rect], requirements: FloorPlanRequirements
) -> list[RoomType]:
    """Match the template's bedrooms to the requested BHK, largest first."""
    wanted = requirements.bedroom_rooms or list(
        BEDROOM_PRIORITY[: requirements.bhk.bedroom_count]
    )
    target = requirements.bhk.bedroom_count

    beds = sorted(
        (r for r in rects if r.type.is_bedroom), key=lambda r: r.area, reverse=True
    )
    retyped: dict[int, RoomType] = {}
    for index, room in enumerate(beds[:target]):
        retyped[id(room)] = wanted[index] if index < len(wanted) else RoomType.BEDROOM
    for room in beds[target:]:
        retyped[id(room)] = (
            RoomType.STUDY_ROOM
            if RoomType.STUDY_ROOM in requirements.rooms
            else RoomType.STORE_ROOM
        )

    types = [retyped.get(id(r), r.type) for r in rects]

    deficit = target - len(beds)
    if deficit > 0:
        candidates = [
            r
            for r in rects
            if r.type
            in {
                RoomType.STUDY_ROOM,
                RoomType.STORE_ROOM,
                RoomType.DINING_ROOM,
                RoomType.PASSAGE,
                RoomType.FOYER,
            }
            and r.area >= 90
        ]
        next_wanted = len(beds)
        for room in sorted(candidates, key=lambda r: r.area, reverse=True)[:deficit]:
            index = rects.index(room)
            types[index] = wanted[next_wanted] if next_wanted < len(wanted) else RoomType.BEDROOM
            next_wanted += 1

    return types


def _build_access_model(
    specs: list[RoomSpec],
) -> tuple[
    tuple[AccessRequirement, ...],
    tuple[tuple[int, int], ...],
    tuple[tuple[int, int], ...],
    int,
]:
    """The intended access graph for a room set.

    The rules encode how people actually move through a house:

    * circulation rooms (living, dining, foyer, passage) form a connected
      spine rooted at the entrance;
    * the kitchen hangs off the dining room when there is one, else the spine;
    * every bedroom, bathroom and service room opens off the spine - never off
      another private room;
    * an attached bathroom opens off *its own* bedroom (the en-suite rule) or
      the spine.

    Because each requirement only asks for a shared wall at least
    :data:`MIN_OPENING` long, the door pass can always cut a real door on it,
    so the modeled door graph is exactly the access graph and every accepted
    plan is walkable from the entrance. A plan that cannot satisfy the access
    model is infeasible and is refused, never repaired.
    """
    indoor = [i for i, spec in enumerate(specs) if not spec.outdoor]
    circulation = [i for i in indoor if specs[i].type in CIRCULATION_TYPES]
    if not circulation:
        circulation = indoor[:1] if indoor else []

    entrance = next(
        (i for i, spec in enumerate(specs) if spec.type is RoomType.LIVING_ROOM),
        next(iter(circulation), next(iter(indoor), 0)),
    )
    circ_set = frozenset(circulation)

    requirements: list[AccessRequirement] = []

    # 1. The circulation spine: every circulation room connects to the entrance
    #    (a star is the minimal spanning tree that keeps them all reachable).
    for index in circulation:
        if index != entrance:
            requirements.append(AccessRequirement(index, (entrance,)))

    def attach_to_any_circulation(index: int) -> None:
        if circ_set:
            requirements.append(AccessRequirement(index, tuple(sorted(circ_set))))

    bedrooms = [i for i in indoor if specs[i].type.is_bedroom]

    # 2. The kitchen hangs off the dining room when there is one, else the spine.
    dining = [i for i, spec in enumerate(specs) if spec.type is RoomType.DINING_ROOM]
    for index, spec in enumerate(specs):
        if spec.type is RoomType.KITCHEN:
            candidates = tuple(dining) if dining else tuple(sorted(circ_set))
            if candidates:
                requirements.append(AccessRequirement(index, candidates))

    # 3. Every bedroom opens off the spine - never off another private room.
    for index in bedrooms:
        attach_to_any_circulation(index)

    # 4. An attached bathroom opens off its own bedroom (the en-suite rule) or
    #    the spine. Assigning by rank keeps the mapping deterministic, so the
    #    first attached bathroom belongs to the principal bedroom.
    attached = [i for i in indoor if specs[i].type is RoomType.ATTACHED_BATHROOM]
    for rank, index in enumerate(attached):
        owner = bedrooms[rank % len(bedrooms)] if bedrooms else None
        candidates = (owner, *sorted(circ_set)) if owner is not None else tuple(sorted(circ_set))
        if candidates:
            requirements.append(AccessRequirement(index, candidates))

    # 5. Everything else (common bathrooms, staircase, study, store, ...)
    #    opens off the spine.
    for index in indoor:
        spec = specs[index]
        if index in circulation or index in bedrooms or spec.type in {
            RoomType.KITCHEN,
            RoomType.ATTACHED_BATHROOM,
        }:
            continue
        attach_to_any_circulation(index)

    # 6. Outdoor rooms (balcony, parking, garden) open off the spine too, so
    #    the door pass models a real door onto them rather than leaving a
    #    space you can see but never walk to.
    for index, spec in enumerate(specs):
        if spec.outdoor:
            attach_to_any_circulation(index)

    # Preferred pairs (the old scoring hooks) and forbidden pairs (a bathroom
    # against the social core reads as a privacy failure, not a hard fault).
    by_type: dict[RoomType, int] = {}
    for index, spec in enumerate(specs):
        by_type.setdefault(spec.type, index)

    preferred: list[tuple[int, int]] = []
    for first, second in ADJACENCY_RULES:
        if first in by_type and second in by_type:
            preferred.append((by_type[first], by_type[second]))

    bathrooms = [i for i in indoor if specs[i].type.is_bathroom]
    social = [i for i in indoor if specs[i].type in PRIVACY_AWAY_FROM]
    forbidden = sorted((a, b) for a in bathrooms for b in social)

    return tuple(requirements), tuple(preferred), tuple(forbidden), entrance


def programme_from_brief(
    requirements: FloorPlanRequirements, template: FloorPlanTemplate
) -> Programme:
    """The room set for this (brief, template) pair, ready to solve.

    Returns the full brief's room set plus the access model that describes how
    they are meant to connect. Milestone C's access requirements are the hard
    part of the programme: the solver enforces them, so the candidate is
    walkable from the entrance by construction or it is rejected.
    """
    rects = [Rect(r.type, r.name, r.x, r.y, r.width, r.height) for r in template.rooms]
    types = _retype_bedrooms(rects, requirements)

    required = Counter(requirements.all_room_types)
    chosen = [t for t in types if t in required or t not in OPTIONAL_ROOMS]
    if not chosen:
        chosen = list(required.elements())

    for room_type, count in required.items():
        deficit = count - chosen.count(room_type)
        if deficit > 0:
            chosen.extend([room_type] * deficit)

    targets = requirements.room_targets
    specs = [_spec_for(room_type, targets) for room_type in chosen]

    access, preferred, forbidden, entrance = _build_access_model(specs)
    return Programme(
        specs=specs,
        access_requirements=access,
        adjacency_pairs=preferred,
        forbidden_pairs=forbidden,
        entrance_index=entrance,
    )


def candidate_programmes(
    requirements: FloorPlanRequirements,
    template: FloorPlanTemplate,
    *,
    count: int = 1,
) -> list[Programme]:
    """The candidate topologies to solve.

    Milestone A returns the single programme derived from the brief; Milestone C
    does the same but enriches it with the access model. Topology search beyond
    the existing 20 templates is deliberately deferred - first prove that the
    current templates produce geometrically valid *and* accessible plans.
    """
    return [programme_from_brief(requirements, template)]
