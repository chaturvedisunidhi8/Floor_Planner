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

The topology-search milestone adds a *search* over the arrangement space on top
of that foundation: :func:`candidate_programmes` now returns several candidate
programmes per (brief, template) pair - the original single programme plus
room-order permutations and soft spatial-zoning variants. Every candidate keeps
the exact same room set and the same access model, so the door graph - and with
it the connectivity guarantee - is identical across the search; only the packing
the solver is asked to find changes. The engine solves each candidate, scores
the survivors with the architectural scorer and returns the best, so "more
plans" never trades away the hard guarantees A-F proved.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.geometry.connectivity import CIRCULATION_TYPES
from app.geometry.models import Plan
from app.geometry.primitives import Rect
from app.geometry.quality import HABITABLE, external_edges, quality_metrics
from app.geometry.units import max_area, min_area, min_side, natural_area
from app.geometry.walls import WALLS
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
#: Owned by :data:`app.geometry.walls.WALLS`.
MIN_OPENING = WALLS.min_opening

#: Rooms that read as a corridor run once aligned on one band.
_CORRIDOR_RUN_TYPES: frozenset[RoomType] = frozenset(
    {RoomType.PASSAGE, RoomType.FOYER}
)

#: Stable labels for the adaptive candidates, so reports and tests can name
#: them without string-matching the solver's search log.
SPINE_LABEL = "Spine corridor"
WET_CLUSTER_LABEL = "Wet rooms near bedrooms"
DAYLIGHT_LABEL = "Daylight on the envelope"


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
class SpatialBias:
    """Soft, objective-only pressure on where rooms sit.

    Each tuple names indices (into :attr:`Programme.specs`) the solver is
    *rewarded* for placing with their centre in the named half of the plot. The
    reward is a negative objective term, so a biased candidate prefers that
    arrangement but is never required to take it: it can still place a bedroom
    anywhere, which is exactly why a spatial bias cannot make a feasible brief
    infeasible. Its job is to steer the search away from the single packing the
    base candidate would find, so the engine actually sees more of the feasible
    space instead of several near-copies of one plan.
    """

    #: Indices pushed to the left / right / bottom / top half of the plot.
    left: tuple[int, ...] = ()
    right: tuple[int, ...] = ()
    bottom: tuple[int, ...] = ()
    top: tuple[int, ...] = ()
    #: Indices rewarded for sharing one vertical band - their x-intervals all
    #: overlap - so they stack into a column. This is how an adaptive candidate
    #: asks the solver for a continuous north-south corridor run.
    align_x: tuple[int, ...] = ()
    #: Indices rewarded for sharing one horizontal band - their y-intervals all
    #: overlap - so they sit in a row. This asks for an east-west corridor run.
    align_y: tuple[int, ...] = ()
    #: Indices rewarded for touching any plot boundary, which is what gives a
    #: room an external wall (daylight) and a corner room two (cross-ventilation).
    touch_edge: tuple[int, ...] = ()
    #: Objective credit per biased room. The CP-SAT objective is integer, so
    #: this must be a whole number; ``20`` is small next to the area-deviation
    #: terms but large enough to steer a typical 1-4 BHK pack.
    weight: int = 20


@dataclass(frozen=True)
class TopologySearchConfig:
    """How many candidate topologies to generate, and what kinds.

    ``max_candidates`` caps the total returned by :func:`candidate_programmes`
    (the base programme is always candidate 0). ``enable_zoning`` turns on the
    soft spatial-bias variants - the main diversity driver - and
    ``enable_permutations`` the room-order variants. ``bias_weight`` feeds every
    :class:`SpatialBias` generated. Setting ``max_candidates=1`` reproduces the
    pre-search behaviour exactly.
    """

    max_candidates: int = 5
    enable_zoning: bool = True
    enable_permutations: bool = True
    bias_weight: int = 20


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
    #: Soft spatial bias (topology search). ``None`` for the base programme and
    #: the permutation variants, which rely on order alone for diversity.
    spatial_bias: SpatialBias | None = None
    #: Human-readable name of this candidate, for logs and reports.
    label: str = "Base"
    #: ``order[k]`` = index into the base programme's ``specs`` of the spec at
    #: position ``k`` in this candidate's ``specs``. ``None`` when the candidate
    #: shares the base order, so the engine leaves its rooms untouched.
    order: tuple[int, ...] | None = None


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

    # Bedrooms ranked by desirability, not spec position, so the en-suite
    # assignment below is invariant to the room order a permutation candidate
    # solves in - the principal bedroom always owns the first attached bathroom,
    # whichever order the specs happen to come in.
    def _bedroom_rank(spec) -> int:
        try:
            return BEDROOM_PRIORITY.index(spec.type)
        except ValueError:
            return len(BEDROOM_PRIORITY)

    bedrooms = sorted(
        (i for i in indoor if specs[i].type.is_bedroom), key=lambda i: _bedroom_rank(specs[i])
    )

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
    #    the spine. Assigning by rank keeps the mapping deterministic and
    #    order-invariant, so the first attached bathroom belongs to the
    #    principal bedroom whatever the spec order.
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


def _zoning_variants(
    base: Programme, weight: int
) -> list[tuple[SpatialBias, str]]:
    """Bedrooms vs social-core splits - the semantic zones of a home.

    Each variant pushes the bedrooms into one half of the plot and the social
    core (living, dining, kitchen) into the opposite half. Trying all four
    directions gives the search two independent axes of real difference - a
    front/back split and a left/right split - and the two mirrors of each. A
    brief with no bedrooms (or no social core) has nothing to zone, so it
    yields no variants and the permutations carry the diversity instead.
    """
    bedrooms = tuple(i for i, spec in enumerate(base.specs) if spec.type.is_bedroom)
    social = tuple(
        i
        for i, spec in enumerate(base.specs)
        if spec.type in {RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.KITCHEN}
    )
    if not bedrooms or not social:
        return []

    return [
        (SpatialBias(left=bedrooms, right=social, weight=weight), "Bedrooms left / social right"),
        (SpatialBias(right=bedrooms, left=social, weight=weight), "Bedrooms right / social left"),
        (SpatialBias(bottom=bedrooms, top=social, weight=weight), "Bedrooms back / social front"),
        (SpatialBias(top=bedrooms, bottom=social, weight=weight), "Bedrooms front / social back"),
    ]


def _permutation_orders(specs: list[RoomSpec]) -> list[tuple[tuple[int, ...], str]]:
    """Deterministic room orders that re-shuffle the packing without changing it.

    The CP-SAT optimum is order-independent, but a time-limited single-worker
    solve finds whichever good packing its search happens on first, and the
    order variables are created in dictates which region that is. Area-sorted
    orders steer the search towards growing the big rooms first; the reversed
    order reads as a mirror of the base packing.
    """
    if len(specs) < 3:
        return []
    by_area = sorted(
        range(len(specs)), key=lambda i: specs[i].target_area, reverse=True
    )
    return [
        (tuple(by_area), "Largest rooms first"),
        (tuple(reversed(by_area)), "Smallest rooms first"),
        (tuple(reversed(range(len(specs)))), "Mirrored room order"),
    ]


def _permuted_programme(base: Programme, order: tuple[int, ...], label: str) -> Programme:
    """A candidate that shares the base room set but solves them in another order.

    The access model is rebuilt over the permuted specs - the *edge set* is the
    same one the base programme carries (same rooms, same attachment rules),
    only its indices follow the new order - so the door graph the solver is
    forced to satisfy is exactly the base's, reindexed.
    """
    permuted = [base.specs[index] for index in order]
    access, preferred, forbidden, entrance = _build_access_model(permuted)
    return Programme(
        specs=permuted,
        access_requirements=access,
        adjacency_pairs=preferred,
        forbidden_pairs=forbidden,
        entrance_index=entrance,
        label=label,
        order=order,
    )


def _bedrooms_half(plan: Plan, bedrooms: tuple[int, ...]) -> str:
    """The half of the plot holding the most bedrooms, for wet clustering.

    ``left``/``right`` split on x, ``bottom``/``top`` on y. When both axes are
    tied the tie-breaks are deterministic (x axis, then the left/bottom side) so
    the adaptive candidate list is stable for a given base plan.
    """
    n = len(bedrooms)
    left = sum(
        1
        for i in bedrooms
        if plan.rooms[i].x + plan.rooms[i].width / 2 <= plan.plot_width / 2
    )
    bottom = sum(
        1
        for i in bedrooms
        if plan.rooms[i].y + plan.rooms[i].height / 2 <= plan.plot_length / 2
    )
    right, top = n - left, n - bottom
    if max(left, right) >= max(bottom, top):
        return "left" if left >= right else "right"
    return "bottom" if bottom >= top else "top"


def adaptive_programmes(
    base: Programme, plan: Plan, *, weight: int = 20
) -> list[Programme]:
    """Targeted candidates for the base plan's measured weaknesses.

    The standard search diversifies *blindly* - zoning halves, room orders.
    This function reads the base plan's :class:`QualityMetrics` and returns at
    most two candidates whose soft spatial bias directly attacks the worst
    measured axes, so the engine spends its fixed candidate budget where it is
    known to help instead of on near-copies of a packing. Each candidate keeps
    the base room set, the base access model and ``order=None``, so the
    connectivity guarantee is identical and rooms still come back in the base
    programme's order.

    ``plan.rooms`` must be in the base programme's spec order (which the engine
    guarantees with ``_remap_to_base_order``), because spec index ``i`` is read
    against ``plan.rooms[i]``.
    """
    metrics = quality_metrics(plan)
    candidates: list[Programme] = []

    def make(label: str, bias: SpatialBias) -> Programme:
        return Programme(
            specs=base.specs,
            access_requirements=base.access_requirements,
            adjacency_pairs=base.adjacency_pairs,
            forbidden_pairs=base.forbidden_pairs,
            entrance_index=base.entrance_index,
            spatial_bias=bias,
            label=label,
        )

    # 1. Fragmented corridor strips -> one continuous spine. Fragmentation is
    #    only measurable when the brief actually carries corridor rooms (the
    #    template's passage/foyer run); with fewer than two there is nothing to
    #    align. A deep plot wants a north-south run (share one x band), a wide
    #    plot an east-west run (share one y band).
    corridor = tuple(
        i for i, spec in enumerate(base.specs) if spec.type in _CORRIDOR_RUN_TYPES
    )
    if (
        len(corridor) >= 2
        and metrics.corridor_fragmentation is not None
        and metrics.corridor_fragmentation > 0.0
    ):
        if plan.plot_length >= plan.plot_width:
            bias = SpatialBias(align_x=corridor, weight=weight)
        else:
            bias = SpatialBias(align_y=corridor, weight=weight)
        candidates.append(make(SPINE_LABEL, bias))

    # 2. Wet rooms not pulled towards the bedrooms: slender bathrooms, a
    #    bathroom against the social core, or bathrooms sharing no wall with
    #    any bedroom. The bias pushes every bathroom into the half the bedrooms
    #    already dominate, which is the en-suite rule generalised to the whole
    #    wet zone.
    bathrooms = tuple(
        i for i, spec in enumerate(base.specs) if spec.type.is_bathroom
    )
    bedrooms = tuple(
        i for i, spec in enumerate(base.specs) if spec.type.is_bedroom
    )
    wet_weak = (
        metrics.slender_bathrooms > 0
        or metrics.bathroom_social_walls > 0
        or (
            metrics.common_bath_bedroom_share is not None
            and metrics.common_bath_bedroom_share < 0.5
        )
        or (
            metrics.attached_bath_bedroom_share is not None
            and metrics.attached_bath_bedroom_share < 0.5
        )
    )
    if bathrooms and bedrooms and wet_weak:
        half = _bedrooms_half(plan, bedrooms)
        if half == "left":
            bias = SpatialBias(left=bathrooms, weight=weight)
        elif half == "right":
            bias = SpatialBias(right=bathrooms, weight=weight)
        elif half == "bottom":
            bias = SpatialBias(bottom=bathrooms, weight=weight)
        else:
            bias = SpatialBias(top=bathrooms, weight=weight)
        candidates.append(make(WET_CLUSTER_LABEL, bias))

    # 3. Habitable rooms starved of daylight: fewer than seven in ten on an
    #    external wall, or fewer than four in ten cross-ventilated. The bias
    #    rewards them touching any plot boundary - a window for one edge, a
    #    corner room for two (cross-ventilation).
    habitable = tuple(
        i for i, spec in enumerate(base.specs) if spec.type in HABITABLE
    )
    if habitable:
        lit = sum(1 for i in habitable if external_edges(plan.rooms[i], plan) >= 1)
        if lit / len(habitable) < 0.7 or metrics.cross_ventilated / len(habitable) < 0.4:
            candidates.append(
                make(DAYLIGHT_LABEL, SpatialBias(touch_edge=habitable, weight=weight))
            )

    return candidates[:2]


def candidate_programmes(
    requirements: FloorPlanRequirements,
    template: FloorPlanTemplate,
    *,
    config: TopologySearchConfig | None = None,
    count: int | None = None,
) -> list[Programme]:
    """The candidate topologies to solve, in order of preference.

    Candidate 0 is always the single programme the engine produced before the
    topology-search milestone - a caller that only wants that behaviour reads
    ``[0]`` and gets it unchanged. With a :class:`TopologySearchConfig` (or a
    ``count`` above 1) the rest of the list holds variants over the *same room
    set*: soft spatial-zoning variants that steer the solver into genuinely
    different arrangements, and room-order permutations that shuffle the
    packing it finds. Every variant keeps the base access model, so the
    connectivity guarantee is identical across the whole search - only the
    packing asked for changes.
    """
    base = programme_from_brief(requirements, template)
    if config is None and count is None:
        return [base]

    limit = count if count is not None else config.max_candidates if config else 1
    limit = max(1, limit)
    if limit == 1:
        return [base]

    if config is None:
        config = TopologySearchConfig()
    variants: list[Programme] = []
    if config.enable_zoning:
        for bias, label in _zoning_variants(base, config.bias_weight):
            variants.append(
                Programme(
                    specs=base.specs,
                    access_requirements=base.access_requirements,
                    adjacency_pairs=base.adjacency_pairs,
                    forbidden_pairs=base.forbidden_pairs,
                    entrance_index=base.entrance_index,
                    spatial_bias=bias,
                    label=label,
                )
            )
    if config.enable_permutations:
        for order, label in _permutation_orders(base.specs):
            variants.append(_permuted_programme(base, order, label))

    return [base, *variants[: limit - 1]]
