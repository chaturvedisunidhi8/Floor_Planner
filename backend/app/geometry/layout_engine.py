"""Template-guided layout generation.

Step 4 of the specification: produce unique layouts that follow the user's
requirements, are inspired by the matched templates, and are *not* copies.

The pipeline for one variation:

1. **Orient**  - rotate the template when the user's plot is the other way up.
2. **Scale**   - map the template's rectangles onto the user's plot.
3. **Reconcile** - drop rooms the client did not ask for, insert the ones they
   did by splitting a suitable neighbour, retype bedrooms to match the BHK.
4. **Vary**    - apply seeded operators (mirror, swap same-zone pairs, shift
   shared wall lines, resize the social core) so each variation is genuinely
   different rather than a recolour of the same plan.
5. **Align & repair** - snap every edge to shared wall lines, resolve any
   overlap the variation introduced, and grow the envelope to the plot edges.

Everything is driven by a seeded ``random.Random``, so a given
(template, seed) pair always yields the same plan - essential for
reproducible tests and for regenerating an image the user has already seen.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, replace

from app.core.logging import get_logger
from app.geometry.primitives import (
    GRID,
    Rect,
    align_walls,
    min_area,
    min_side,
    natural_area,
    snap,
)
from app.geometry.slicing import add_circulation, assign_demands, build_tree, instantiate
from app.geometry.validator import (
    LayoutRepairer,
    LayoutValidator,
    absorb_vacated,
    cap_room_sizes,
    ensure_circulation,
    fill_gaps,
    fit_to_plot,
    merge_adjacent_passages,
    rebalance_room_sizes,
    resize_to_targets,
)
from app.schemas.enums import RoomType
from app.schemas.requirements import FloorPlanRequirements
from app.schemas.template import FloorPlanTemplate

logger = get_logger(__name__)

#: Past this proportion a carved slice reads as a corridor rather than a room,
#: and :meth:`LayoutEngine._enforce_minimum_sizes` would throw it away.
RIBBON_ASPECT = 2.8

#: Bedrooms in descending order of desirability - the largest template bedroom
#: becomes the master, the next the children's room, and so on.
BEDROOM_PRIORITY: tuple[RoomType, ...] = (
    RoomType.MASTER_BEDROOM,
    RoomType.CHILDREN_BEDROOM,
    RoomType.GUEST_BEDROOM,
    RoomType.BEDROOM,
)

#: Which rooms a newly requested room may be carved out of, best host first.
CARVE_HOSTS: dict[RoomType, tuple[RoomType, ...]] = {
    RoomType.POOJA_ROOM: (RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.PASSAGE),
    RoomType.STUDY_ROOM: (RoomType.LIVING_ROOM, RoomType.BEDROOM, RoomType.GUEST_BEDROOM),
    RoomType.STORE_ROOM: (RoomType.KITCHEN, RoomType.PASSAGE, RoomType.LIVING_ROOM),
    RoomType.UTILITY_ROOM: (RoomType.KITCHEN, RoomType.WASH_AREA, RoomType.STORE_ROOM),
    RoomType.WASH_AREA: (RoomType.KITCHEN, RoomType.UTILITY_ROOM, RoomType.BALCONY),
    RoomType.DINING_ROOM: (RoomType.LIVING_ROOM, RoomType.KITCHEN),
    RoomType.BALCONY: (RoomType.LIVING_ROOM, RoomType.MASTER_BEDROOM),
    RoomType.STAIRCASE: (RoomType.LIVING_ROOM, RoomType.PASSAGE, RoomType.FOYER),
    RoomType.PARKING: (RoomType.GARDEN, RoomType.LIVING_ROOM),
    RoomType.GARDEN: (RoomType.PARKING, RoomType.BALCONY),
    RoomType.COMMON_BATHROOM: (RoomType.PASSAGE, RoomType.STORE_ROOM, RoomType.LIVING_ROOM),
    RoomType.ATTACHED_BATHROOM: (RoomType.MASTER_BEDROOM, RoomType.BEDROOM, RoomType.PASSAGE),
}

#: Rooms that may be removed when the client did not request them.
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


@dataclass
class LayoutPlan:
    """A finished, validated layout ready to be rendered."""

    rooms: list[Rect]
    plot_width: float
    plot_length: float
    template_id: str
    template_name: str
    variation: str
    seed: int
    warnings: list[str]

    @property
    def built_up_sqft(self) -> float:
        return round(sum(r.area for r in self.rooms if not r.type.is_outdoor), 1)

    @property
    def room_count(self) -> int:
        return len(self.rooms)


class LayoutEngine:
    """Turns (template, requirements, seed) into a distinct, valid layout."""

    def __init__(self, requirements: FloorPlanRequirements) -> None:
        self._req = requirements
        self._w = requirements.plot.width_ft
        self._l = requirements.plot.length_ft
        # Empty unless the client sized the rooms itself, in which case every
        # sizing decision below aims at these instead of the generic bands -
        # shape as well as area, so a 168 sq ft request cannot be met with a
        # 10.5 x 25 ft room.
        self._targets = requirements.room_targets
        self._validator = LayoutValidator(self._w, self._l)
        self._repairer = LayoutRepairer(self._w, self._l)

    def generate(self, template: FloorPlanTemplate, seed: int, variation_index: int) -> LayoutPlan:
        rng = random.Random(seed)

        rooms = self._orient_and_scale(template)
        rooms = self._reconcile_programme(rooms, rng)
        # Circulation before variation: the operators below move rooms about,
        # and it is far easier to mirror a plan that already has a corridor
        # than to thread one through a plan that has been shuffled.
        rooms = ensure_circulation(rooms, self._w, self._l)
        rooms, variation = self._apply_variation(rooms, rng, variation_index)

        targets = self._targets
        rooms = align_walls(rooms)
        rooms = self._repairer.repair(rooms)
        # Reach the plot edges first, then claw back the service rooms that
        # scaling inflated - capping earlier would just be undone by the fit.
        rooms = fit_to_plot(rooms, self._w, self._l, targets=targets)
        rooms = self._repairer.repair(rooms)
        # Settle the proportions before anything caps them. Sliding a wall
        # costs no floor area; trimming a badly shaped room throws away the
        # very area it was given, so it must have nothing left to trim.
        rooms = resize_to_targets(rooms, targets, plot_width=self._w, plot_length=self._l)
        rooms = cap_room_sizes(rooms, targets=targets)
        rooms = fill_gaps(rooms, self._w, self._l, targets=targets)
        rooms = self._repairer.repair(rooms)
        rooms = self._enforce_minimum_sizes(rooms)
        rooms = fill_gaps(rooms, self._w, self._l, targets=targets)
        rooms = self._repairer.repair(rooms)
        # Strict pass last: repair can reopen a pocket by trimming an overlap,
        # and only overlap-safe merges may run once nothing will repair after.
        rooms = fill_gaps(rooms, self._w, self._l, strict=True, targets=targets)
        rooms = merge_adjacent_passages(rooms)
        # Even out the plan last. Wall shifts are geometry-neutral, but the
        # trim-and-reabsorb fallback is not, so tidy up behind it.
        rooms = rebalance_room_sizes(rooms, targets=targets)
        rooms = self._repairer.repair(rooms)
        rooms = fill_gaps(rooms, self._w, self._l, strict=True, targets=targets)
        # Repair and gap filling can both sever a doorway; re-cut whatever the
        # tidying broke before the sizes are settled around it.
        rooms = ensure_circulation(rooms, self._w, self._l)
        # The client's own sizes have the final word: this runs after the
        # generic bands have had their say so nothing can cap a room back down
        # below what was asked for. Sliding whole wall lines conserves the
        # footprint exactly, so it cannot reopen a gap behind itself.
        rooms = resize_to_targets(
            rooms, targets, plot_width=self._w, plot_length=self._l
        )

        report = self._validator.validate(rooms)
        if not report.ok:
            logger.info(
                "Layout from %s (seed %d) needed a second repair pass: %s",
                template.id,
                seed,
                "; ".join(report.errors[:3]),
            )
            rooms = self._repairer.repair(rooms)
            rooms = fill_gaps(rooms, self._w, self._l, strict=True)
            report = self._validator.validate(rooms)

        return LayoutPlan(
            rooms=rooms,
            plot_width=self._w,
            plot_length=self._l,
            template_id=template.id,
            template_name=template.name,
            variation=variation,
            seed=seed,
            warnings=report.all_messages(),
        )

    # --- 1 & 2: orientation and instantiation -------------------------------
    def _orient_and_scale(self, template: FloorPlanTemplate) -> list[Rect]:
        """Rebuild the template's arrangement at the client's sizes.

        The template supplies the topology - which room sits beside which, and
        on which side. Every dimension comes from the brief. See
        :mod:`app.geometry.slicing` for why this is not simply a scale.
        """
        rooms = [Rect(r.type, r.name, r.x, r.y, r.width, r.height) for r in template.rooms]
        tw, tl = template.plot_width_ft, template.plot_length_ft

        # A portrait template on a landscape plot (or vice versa) reads better
        # turned a quarter first: the arrangement keeps its original grain.
        direct = abs((tw / tl) - (self._w / self._l))
        rotated = abs((tl / tw) - (self._w / self._l))
        if rotated < direct - 0.05:
            rooms = [r.rotated() for r in rooms]
            tw, tl = tl, tw

        rooms = self._select_programme(rooms)
        if not rooms:
            return []

        tree = build_tree(rooms, lambda room: self._wanted_area(room.type))
        spare = assign_demands(tree, self._targets, self._w * self._l, tw * tl)
        # Slack goes to circulation rather than being shared out among the
        # rooms, which is what inflated them past the brief in the first place.
        # This matters even with no sizes given: the tree divides the plot in
        # proportion to demand, so area nobody demands would otherwise be
        # handed out anyway, straight past the generic ceilings.
        tree = add_circulation(tree, spare)

        placed = instantiate(tree, 0.0, 0.0, self._w, self._l, self._targets)
        return [r.snapped() for r in placed]

    def _missing_types(self, rooms: list[Rect]) -> list[RoomType]:
        """Rooms the brief asks for that this template does not have.

        Bathrooms are counted rather than merely checked for, since the brief
        gives a number of each and a template rarely happens to match it.
        """
        have = Counter(room.type for room in rooms)
        missing: list[RoomType] = []
        for room_type, wanted in Counter(self._req.all_room_types).items():
            shortfall = wanted - have.get(room_type, 0)
            missing.extend([room_type] * max(0, shortfall))
        return missing

    def _select_programme(self, rooms: list[Rect]) -> list[Rect]:
        """Decide which of the template's rooms the brief actually wants.

        Done before the tree is built rather than after it is laid out: a room
        dropped here simply is not in the arrangement, and its floor area is
        redistributed by the proportional split instead of having to be
        absorbed by a neighbour afterwards.
        """
        rooms = self._retype_bedrooms(rooms)
        requested = set(self._req.all_room_types)
        kept = [
            room
            for room in rooms
            if room.type not in OPTIONAL_ROOMS or room.type in requested
        ]
        kept = kept or rooms
        return kept + self._stand_ins(kept)

    def _stand_ins(self, rooms: list[Rect]) -> list[Rect]:
        """Placeholders for rooms the brief wants and the template has not got.

        They join the arrangement as ordinary rooms, sitting where the room
        they belong next to sits, so the cuts are chosen knowing they are
        there. Adding them to the finished tree instead would nest every one of
        them against the same neighbour, and a chain like that puts each room
        in a thinner box than the last.
        """
        missing = self._missing_types(rooms)
        if not missing:
            return []

        placed: list[Rect] = []
        for room_type in missing:
            wanted = self._wanted_area(room_type)
            side = max(min_side(room_type), wanted**0.5)
            anchor = self._anchor(rooms + placed, room_type)
            placed.append(
                Rect(
                    room_type,
                    room_type.label,
                    anchor[0] - side / 2,
                    anchor[1] - wanted / side / 2,
                    side,
                    wanted / side,
                )
            )
        return placed

    @staticmethod
    def _anchor(rooms: list[Rect], room_type: RoomType) -> tuple[float, float]:
        """Where in the template's arrangement this room belongs."""
        for host_type in CARVE_HOSTS.get(room_type, (RoomType.LIVING_ROOM,)):
            host = next((r for r in rooms if r.type is host_type), None)
            if host is not None:
                return host.center

        widest = max(rooms, key=lambda r: r.area)
        return widest.center

    # --- 3: programme reconciliation --------------------------------------
    def _reconcile_programme(self, rooms: list[Rect], rng: random.Random) -> list[Rect]:
        """Bring the placed rooms in line with the brief.

        Retyping and dropping already happened in :meth:`_select_programme`,
        before the arrangement was laid out. What is left needs geometry: rooms
        the template did not have must be cut out of the ones it did.
        """
        rooms = self._adjust_bathrooms(rooms, rng)
        rooms = self._insert_missing(rooms, rng)
        return rooms

    def _retype_bedrooms(self, rooms: list[Rect]) -> list[Rect]:
        """Match the template's bedrooms to the requested BHK, largest first."""
        bedrooms = sorted(
            [r for r in rooms if r.type.is_bedroom], key=lambda r: r.area, reverse=True
        )
        wanted = self._req.bedroom_rooms or list(BEDROOM_PRIORITY[: self._req.bhk.bedroom_count])
        target = self._req.bhk.bedroom_count

        # Too many bedrooms: the smallest become study or store space.
        surplus = bedrooms[target:]
        keep = bedrooms[:target]

        renamed: dict[int, Rect] = {}
        for index, room in enumerate(keep):
            room_type = wanted[index] if index < len(wanted) else RoomType.BEDROOM
            renamed[id(room)] = replace(room, type=room_type, name=self._label(room_type, index))

        for room in surplus:
            fallback = (
                RoomType.STUDY_ROOM
                if RoomType.STUDY_ROOM in self._req.rooms
                else RoomType.STORE_ROOM
            )
            renamed[id(room)] = replace(room, type=fallback, name=fallback.label)

        # Too few bedrooms: promote the largest non-essential room.
        result = [renamed.get(id(r), r) for r in rooms]
        deficit = target - len(keep)
        if deficit > 0:
            result = self._promote_to_bedrooms(result, deficit, len(keep))
        return result

    def _promote_to_bedrooms(
        self, rooms: list[Rect], deficit: int, existing: int
    ) -> list[Rect]:
        candidates = sorted(
            (
                r
                for r in rooms
                if r.type
                in {
                    RoomType.STUDY_ROOM,
                    RoomType.STORE_ROOM,
                    RoomType.DINING_ROOM,
                    RoomType.PASSAGE,
                    RoomType.FOYER,
                }
                and r.area >= 90
            ),
            key=lambda r: r.area,
            reverse=True,
        )
        promoted = {id(r) for r in candidates[:deficit]}
        index = existing
        result: list[Rect] = []
        for room in rooms:
            if id(room) in promoted:
                room_type = BEDROOM_PRIORITY[min(index, len(BEDROOM_PRIORITY) - 1)]
                result.append(replace(room, type=room_type, name=self._label(room_type, index)))
                index += 1
            else:
                result.append(room)
        return result

    def _adjust_bathrooms(self, rooms: list[Rect], rng: random.Random) -> list[Rect]:
        """Bring the bathroom counts in line with what was requested."""
        for room_type, wanted in (
            (RoomType.ATTACHED_BATHROOM, self._req.bathrooms.attached_count),
            (RoomType.COMMON_BATHROOM, self._req.bathrooms.common_count),
        ):
            present = [r for r in rooms if r.type is room_type]
            if len(present) > wanted:
                for extra in sorted(present, key=lambda r: r.area)[: len(present) - wanted]:
                    rooms = self._absorb([r for r in rooms if r is not extra], extra)
            elif len(present) < wanted:
                for _ in range(wanted - len(present)):
                    rooms = self._carve(rooms, room_type, rng)
        return rooms

    def _insert_missing(self, rooms: list[Rect], rng: random.Random) -> list[Rect]:
        present = {r.type for r in rooms}
        for room_type in self._req.all_room_types:
            if room_type.is_bathroom or room_type in present:
                continue
            rooms = self._carve(rooms, room_type, rng)
            present.add(room_type)
        return rooms

    def _carve(self, rooms: list[Rect], room_type: RoomType, rng: random.Random) -> list[Rect]:
        """Split a host room to make space for ``room_type``."""
        hosts = CARVE_HOSTS.get(room_type, (RoomType.LIVING_ROOM, RoomType.PASSAGE))
        if self._targets:
            # The corridor is holding exactly the floor area the brief did not
            # spend. Take a room the template lacked out of that before taking
            # it out of a room the client sized themselves.
            hosts = (RoomType.PASSAGE, *hosts)
        needed = min_side(room_type)
        # The host must survive giving up the room's *minimum*; the slice itself
        # then aims for the requested size, so a room the client sized generously
        # does not get carved out at the bare minimum and grown back later.
        wanted_area = min_area(room_type)

        for host_type in (*hosts, None):
            candidates = [
                r
                for r in rooms
                if (host_type is None or r.type is host_type)
                # The host must survive the split with a usable room left over.
                and r.area >= wanted_area + min_area(r.type)
                and min(r.width, r.height) >= needed + GRID
                and self._is_valid_host(r, room_type)
            ]
            if not candidates:
                continue

            host = max(candidates, key=lambda r: r.area)
            new_room, pieces = self._split(host, room_type, needed, rng)
            if new_room is None:
                continue
            return [r for r in rooms if r is not host] + pieces + [new_room]

        logger.debug("Could not carve space for %s", room_type.value)
        return rooms

    def _is_valid_host(self, host: Rect, room_type: RoomType) -> bool:
        """Outdoor space only makes sense against an external wall.

        Without this a car park can end up landlocked in the middle of the
        plan, which is the single most obvious way a generated layout betrays
        itself as machine-made.
        """
        if not room_type.is_outdoor:
            return True
        return (
            host.x <= 0.6
            or host.y <= 0.6
            or host.x2 >= self._w - 0.6
            or host.y2 >= self._l - 0.6
        )

    def _split(
        self, host: Rect, room_type: RoomType, needed: float, rng: random.Random
    ) -> tuple[Rect | None, list[Rect]]:
        """Take a slice off the host, keeping every piece usable.

        Returns the new room and the rectangles that replace the host. A small
        room taken as a full-width band off a large host comes out a ribbon -
        a 16 x 3.5 ft prayer room - so when that would happen the band is
        divided again and the offcut becomes circulation.
        """
        host_floor = min_area(host.type)
        target_area = max(self._wanted_area(room_type), needed * needed)
        target_area = min(target_area, host.area - host_floor)
        if target_area < needed * needed:
            return None, []

        horizontal = host.width >= host.height
        # An outdoor slice must land on the external edge, not the inner one.
        edge = self._external_edge(host) if room_type.is_outdoor else None
        if edge in ("left", "right"):
            horizontal = True
        elif edge in ("bottom", "top"):
            horizontal = False

        if horizontal:
            slice_w = snap(min(max(needed, target_area / host.height), host.width - needed))
            if slice_w < needed or (host.width - slice_w) * host.height < host_floor:
                return None, []
            from_left = edge == "left" if edge in ("left", "right") else rng.random() < 0.5
            x = host.x if from_left else host.x2 - slice_w
            new = Rect(room_type, room_type.label, x, host.y, slice_w, host.height)
            rest = (
                replace(host, x=host.x + slice_w, width=host.width - slice_w)
                if from_left
                else replace(host, width=host.width - slice_w)
            )
        else:
            slice_h = snap(min(max(needed, target_area / host.width), host.height - needed))
            if slice_h < needed or host.width * (host.height - slice_h) < host_floor:
                return None, []
            from_bottom = edge == "bottom" if edge in ("bottom", "top") else rng.random() < 0.5
            y = host.y if from_bottom else host.y2 - slice_h
            new = Rect(room_type, room_type.label, host.x, y, host.width, slice_h)
            rest = (
                replace(host, y=host.y + slice_h, height=host.height - slice_h)
                if from_bottom
                else replace(host, height=host.height - slice_h)
            )

        new, offcut = self._trim_ribbon(new, room_type, needed)
        return new, [rest, *offcut]

    @staticmethod
    def _trim_ribbon(new: Rect, room_type: RoomType, needed: float) -> tuple[Rect, list[Rect]]:
        """Shorten an over-long slice, handing the offcut to circulation."""
        if new.aspect <= RIBBON_ASPECT or room_type.is_outdoor:
            return new, []

        if new.width >= new.height:
            width = snap(max(needed, new.height * RIBBON_ASPECT))
            if width >= new.width - GRID:
                return new, []
            offcut = replace(
                new,
                type=RoomType.PASSAGE,
                name=RoomType.PASSAGE.label,
                x=new.x + width,
                width=new.width - width,
            )
            return replace(new, width=width), [offcut]

        height = snap(max(needed, new.width * RIBBON_ASPECT))
        if height >= new.height - GRID:
            return new, []
        offcut = replace(
            new,
            type=RoomType.PASSAGE,
            name=RoomType.PASSAGE.label,
            y=new.y + height,
            height=new.height - height,
        )
        return replace(new, height=height), [offcut]

    def _wanted_area(self, room_type: RoomType) -> float:
        """The size to aim for: what the client asked for, or a believable default."""
        target = self._targets.get(room_type)
        return target.area if target else natural_area(room_type)

    def _external_edge(self, host: Rect) -> str | None:
        """Which plot boundary this room sits against, if any."""
        for edge, distance in (
            ("bottom", host.y),
            ("left", host.x),
            ("right", self._w - host.x2),
            ("top", self._l - host.y2),
        ):
            if distance <= 0.6:
                return edge
        return None

    def _absorb(self, rooms: list[Rect], vacated: Rect) -> list[Rect]:
        """Extend the best-placed neighbour over a removed room's footprint.

        ``vacated`` must already be excluded from ``rooms``, which makes its
        footprint an uncovered pocket - exactly what the gap filler handles,
        including its guards against handing a bathroom enough floor to turn
        it into a corridor. Sharing that logic keeps one set of rules for
        "who gets this space" instead of two that drift apart.
        """
        return absorb_vacated(rooms, vacated, targets=self._targets)

    # --- 4: variation operators -------------------------------------------
    def _apply_variation(
        self, rooms: list[Rect], rng: random.Random, index: int
    ) -> tuple[list[Rect], str]:
        """Make this variation visibly distinct from its siblings.

        ``index`` picks the primary operator so the four layouts in a response
        never collapse onto the same transform, while ``rng`` decides the
        details.
        """
        operators = [
            ("Mirrored", self._mirror_x),
            ("Rotated Entry", self._mirror_y),
            ("Rebalanced Core", self._rebalance_core),
            ("Reordered Wing", self._swap_same_zone),
        ]
        label, primary = operators[index % len(operators)]
        rooms = primary(rooms, rng)

        # A light secondary nudge keeps repeat runs from looking mechanical.
        if rng.random() < 0.6:
            rooms = self._shift_wall(rooms, rng)

        return rooms, label

    def _mirror_x(self, rooms: list[Rect], rng: random.Random) -> list[Rect]:
        return [r.mirrored_x(self._w) for r in rooms]

    def _mirror_y(self, rooms: list[Rect], rng: random.Random) -> list[Rect]:
        return [r.mirrored_y(self._l) for r in rooms]

    def _rebalance_core(self, rooms: list[Rect], rng: random.Random) -> list[Rect]:
        """Grow the living room into a neighbour, shrinking that neighbour."""
        living = next((r for r in rooms if r.type is RoomType.LIVING_ROOM), None)
        if living is None:
            return rooms

        neighbours = [
            r
            for r in rooms
            if r is not living
            and r.shared_wall_length(living) > 3.0
            and r.type not in {RoomType.PARKING, RoomType.GARDEN}
            and min(r.width, r.height) > min_side(r.type) + 2.0
        ]
        if not neighbours:
            return rooms

        donor = rng.choice(neighbours)
        shift = snap(min(2.5, min(donor.width, donor.height) - min_side(donor.type)))
        if shift < GRID:
            return rooms

        result: list[Rect] = []
        for room in rooms:
            if room is donor:
                if abs(donor.x2 - living.x) < 1.0:
                    result.append(replace(donor, width=donor.width - shift))
                elif abs(living.x2 - donor.x) < 1.0:
                    result.append(replace(donor, x=donor.x + shift, width=donor.width - shift))
                elif abs(donor.y2 - living.y) < 1.0:
                    result.append(replace(donor, height=donor.height - shift))
                else:
                    result.append(replace(donor, y=donor.y + shift, height=donor.height - shift))
            elif room is living:
                if abs(donor.x2 - living.x) < 1.0:
                    result.append(replace(living, x=living.x - shift, width=living.width + shift))
                elif abs(living.x2 - donor.x) < 1.0:
                    result.append(replace(living, width=living.width + shift))
                elif abs(donor.y2 - living.y) < 1.0:
                    result.append(replace(living, y=living.y - shift, height=living.height + shift))
                else:
                    result.append(replace(living, height=living.height + shift))
            else:
                result.append(room)
        return result

    @staticmethod
    def _swap_same_zone(rooms: list[Rect], rng: random.Random) -> list[Rect]:
        """Exchange the positions of two similarly sized rooms of the same kind."""
        groups: dict[str, list[Rect]] = {}
        for room in rooms:
            if room.type.is_bedroom:
                groups.setdefault("bedroom", []).append(room)
            elif room.type.is_bathroom:
                groups.setdefault("bathroom", []).append(room)

        candidates = [g for g in groups.values() if len(g) >= 2]
        if not candidates:
            return rooms

        group = rng.choice(candidates)
        a, b = rng.sample(group, 2)
        swapped = {
            id(a): replace(a, type=b.type, name=b.name),
            id(b): replace(b, type=a.type, name=a.name),
        }
        return [swapped.get(id(r), r) for r in rooms]

    @staticmethod
    def _shift_wall(rooms: list[Rect], rng: random.Random) -> list[Rect]:
        """Move one shared wall line, resizing both rooms that meet on it."""
        pairs = [
            (a, b)
            for i, a in enumerate(rooms)
            for b in rooms[i + 1 :]
            if abs(a.x2 - b.x) < 0.6 and min(a.y2, b.y2) - max(a.y, b.y) > 4.0
        ]
        if not pairs:
            return rooms

        a, b = rng.choice(pairs)
        headroom = min(a.width - min_side(a.type), b.width - min_side(b.type))
        if headroom < 1.0:
            return rooms

        shift = snap(rng.uniform(-1.0, 1.0) * min(2.0, headroom))
        if abs(shift) < GRID:
            return rooms

        updated = {
            id(a): replace(a, width=a.width + shift),
            id(b): replace(b, x=b.x + shift, width=b.width - shift),
        }
        return [updated.get(id(r), r) for r in rooms]

    # --- 5: finishing ------------------------------------------------------
    def _enforce_minimum_sizes(self, rooms: list[Rect]) -> list[Rect]:
        """Drop optional rooms that repair squeezed below a usable size.

        Both tests matter: a room can hold enough square feet and still be a
        3 ft ribbon that no one could stand in, and a ribbon labelled "Balcony"
        is exactly the kind of artefact that gives a generated plan away.
        """
        # A room the client ticked is not optional, whatever shape repair left
        # it in. Dropping it silently is how a brief that asked for a dining
        # room comes back without one; a badly proportioned dining room at
        # least tells them something they can act on.
        droppable = OPTIONAL_ROOMS - set(self._req.all_room_types)

        kept: list[Rect] = []
        for room in rooms:
            if room.type not in droppable:
                kept.append(room)
                continue

            too_small = room.area < min_side(room.type) ** 2 * 0.7
            too_thin = min(room.width, room.height) < min_side(room.type) * 0.8
            too_long = room.aspect > 4.2
            if too_small or too_thin or too_long:
                logger.debug(
                    "Dropping %s (%.1f x %.1f ft) - below usable proportions",
                    room.name,
                    room.width,
                    room.height,
                )
                kept = self._absorb(kept, room) if kept else kept
                continue
            kept.append(room)
        return kept

    @staticmethod
    def _label(room_type: RoomType, index: int) -> str:
        if room_type is RoomType.BEDROOM:
            return f"Bedroom {index + 1}"
        return room_type.label
