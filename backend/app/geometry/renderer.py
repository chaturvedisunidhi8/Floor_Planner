"""Draws a :class:`LayoutPlan` as a professional architectural floor plan.

Conventions borrowed from real drawings, because they are what make a plan read
as architectural rather than as coloured boxes:

* heavy poche for external walls, lighter for internal partitions
* door swings drawn as quarter arcs on every opening
* window ticks broken into the external wall run
* per-room labels with the dimension line underneath, in feet and inches
* hatched parking, stippled garden, tread lines on the staircase
* a title block with the plot size, configuration and overall dimension lines
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.core.logging import get_logger
from app.geometry.labels import feet_inches as _feet_inches
from app.geometry.layout_engine import LayoutPlan
from app.geometry.primitives import Rect
from app.schemas.enums import InteriorStyle, RoomType

logger = get_logger(__name__)


@dataclass(frozen=True)
class Palette:
    background: str
    paper: str
    wall: str
    partition: str
    text: str
    muted: str
    dimension: str
    room_fills: dict[str, str]
    accent: str


def _fills(**overrides: str) -> dict[str, str]:
    base = {
        "living": "#F3EFE7",
        "sleeping": "#EFE9DF",
        "wet": "#E3EAEC",
        "service": "#ECE7DD",
        "outdoor": "#E6EDE3",
        "circulation": "#F7F4EE",
    }
    base.update(overrides)
    return base


PALETTES: dict[InteriorStyle, Palette] = {
    InteriorStyle.MODERN: Palette(
        background="#FFFFFF",
        paper="#FCFBF8",
        wall="#1B1B1B",
        partition="#4A4A4A",
        text="#1B1B1B",
        muted="#6E6E6E",
        dimension="#8A8A8A",
        room_fills=_fills(),
        accent="#2F6F6B",
    ),
    InteriorStyle.MINIMAL: Palette(
        background="#FFFFFF",
        paper="#FFFFFF",
        wall="#222222",
        partition="#5C5C5C",
        text="#222222",
        muted="#7A7A7A",
        dimension="#9A9A9A",
        room_fills=_fills(
            living="#F7F7F5",
            sleeping="#F2F2F0",
            wet="#EAEEEF",
            service="#F0F0EE",
            outdoor="#EEF1EC",
            circulation="#FAFAF9",
        ),
        accent="#4A4A4A",
    ),
    InteriorStyle.LUXURY: Palette(
        background="#FFFFFF",
        paper="#FBF7F0",
        wall="#161311",
        partition="#4B4239",
        text="#1E1913",
        muted="#7A6C5B",
        dimension="#9C8C77",
        room_fills=_fills(
            living="#F1E7D6",
            sleeping="#EDE2D0",
            wet="#DFE7E8",
            service="#E9E0D0",
            outdoor="#E2EADC",
            circulation="#F6EFE3",
        ),
        accent="#A67C3D",
    ),
    InteriorStyle.TRADITIONAL: Palette(
        background="#FFFFFF",
        paper="#FDF8EE",
        wall="#20180F",
        partition="#544433",
        text="#20180F",
        muted="#7C6A54",
        dimension="#9A8B6E",
        room_fills=_fills(
            living="#F5E9D4",
            sleeping="#F1E3CC",
            wet="#E1E9E9",
            service="#EDE2CC",
            outdoor="#E3EBD9",
            circulation="#F8F1E2",
        ),
        accent="#9C5A2D",
    ),
}


ROOM_GROUP: dict[RoomType, str] = {
    RoomType.LIVING_ROOM: "living",
    RoomType.DINING_ROOM: "living",
    RoomType.STUDY_ROOM: "living",
    RoomType.MASTER_BEDROOM: "sleeping",
    RoomType.GUEST_BEDROOM: "sleeping",
    RoomType.CHILDREN_BEDROOM: "sleeping",
    RoomType.BEDROOM: "sleeping",
    RoomType.ATTACHED_BATHROOM: "wet",
    RoomType.COMMON_BATHROOM: "wet",
    RoomType.KITCHEN: "service",
    RoomType.UTILITY_ROOM: "service",
    RoomType.STORE_ROOM: "service",
    RoomType.WASH_AREA: "service",
    RoomType.POOJA_ROOM: "service",
    RoomType.BALCONY: "outdoor",
    RoomType.PARKING: "outdoor",
    RoomType.GARDEN: "outdoor",
    RoomType.STAIRCASE: "circulation",
    RoomType.PASSAGE: "circulation",
    RoomType.FOYER: "circulation",
}


def _dimension_metrics(ppf: float) -> tuple[float, float, int]:
    """``(offset, tick, font_size)`` for the overall dimension lines."""
    return max(18.0, ppf * 1.8), max(5.0, ppf * 0.5), int(max(13, ppf * 1.35))


def _dimension_headroom(ppf: float) -> float:
    """Space the width dimension line and its label occupy above the plan."""
    offset, tick, font_size = _dimension_metrics(ppf)
    return offset + tick + 4 + font_size * 1.15


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "seguisb.ttf"]
        if bold
        else ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


class FloorPlanRenderer:
    """Renders a layout to PNG at a fixed canvas size."""

    def __init__(self, width: int = 1024, height: int = 1024) -> None:
        self._canvas = (width, height)

    def render(
        self,
        plan: LayoutPlan,
        *,
        style: InteriorStyle,
        title: str,
        subtitle: str = "",
    ) -> Image.Image:
        """The finished plan: fills, linework, labels and title block."""
        return self._render(plan, style=style, title=title, subtitle=subtitle, layers="all")

    def render_layers(
        self,
        plan: LayoutPlan,
        *,
        style: InteriorStyle,
        title: str,
        subtitle: str = "",
    ) -> tuple[Image.Image, Image.Image]:
        """Split the drawing into ``(fills, linework)``.

        The hybrid image pipeline stylises the flat fills with FLUX and then
        pastes the linework back on top at full opacity, so walls, door swings,
        dimensions and room labels stay exactly as the geometry engine computed
        them - a diffusion model cannot be trusted with any of those.
        """
        fills = self._render(plan, style=style, title=title, subtitle=subtitle, layers="fills")
        linework = self._render(
            plan, style=style, title=title, subtitle=subtitle, layers="linework"
        )
        return fills, linework

    def _render(
        self,
        plan: LayoutPlan,
        *,
        style: InteriorStyle,
        title: str,
        subtitle: str,
        layers: str,
    ) -> Image.Image:
        palette = PALETTES.get(style, PALETTES[InteriorStyle.MODERN])
        paint_fills = layers in ("all", "fills")
        paint_lines = layers in ("all", "linework")
        transparent = layers == "linework"

        # Supersample, then downscale - gives clean anti-aliased walls without
        # depending on any drawing library beyond Pillow.
        scale = 2
        w, h = self._canvas[0] * scale, self._canvas[1] * scale

        image = Image.new(
            "RGBA" if transparent else "RGB",
            (w, h),
            (0, 0, 0, 0) if transparent else palette.background,
        )
        draw = ImageDraw.Draw(image)

        margin_x = int(w * 0.085)
        margin_bottom = int(h * 0.105)
        avail_w = w - 2 * margin_x

        # Bottom of the title/subtitle stack that _draw_title_block paints.
        header_bottom = h * 0.036 + h * 0.032 * 1.25
        if subtitle:
            header_bottom = h * 0.036 + h * 0.040 + h * 0.019 * 1.25

        # The width dimension line and its label sit above the drawing, and both
        # scale with ppf - which in turn depends on the top margin. Iterating
        # converges quickly: a taller margin means a smaller scale, which needs
        # less headroom, so the label can never grow back into the subtitle. A
        # plan that is already centred low enough keeps the base margin.
        margin_top = int(h * 0.165)
        for _ in range(4):
            avail_h = h - margin_top - margin_bottom
            ppf = min(avail_w / plan.plot_width, avail_h / plan.plot_length)
            plan_h = plan.plot_length * ppf
            origin_y = margin_top + (avail_h - plan_h) / 2
            required = header_bottom + h * 0.012 + _dimension_headroom(ppf)
            if origin_y >= required:
                break
            margin_top = int(required)

        plan_w = plan.plot_width * ppf
        origin_x = (w - plan_w) / 2

        def px(fx: float, fy: float) -> tuple[float, float]:
            """Plot feet -> canvas pixels (y flips: +y is up on the plan)."""
            return (origin_x + fx * ppf, origin_y + (plan.plot_length - fy) * ppf)

        wall = max(3, int(round(ppf * 0.62)))
        partition = max(2, int(round(ppf * 0.34)))

        if paint_fills:
            draw.rectangle(
                [origin_x, origin_y, origin_x + plan_w, origin_y + plan_h],
                fill=palette.paper,
            )
            for room in plan.rooms:
                self._draw_room_fill(draw, room, px, palette)

        if paint_lines:
            for room in plan.rooms:
                self._draw_room_features(draw, room, px, ppf, palette)

            if plan.walls is not None:
                self._draw_wall_model(draw, plan.walls, px, palette)
            else:
                for room in plan.rooms:
                    self._draw_walls(draw, room, px, partition, palette)

            self._draw_openings(draw, plan, px, ppf, palette, wall)

            draw.rectangle(
                [origin_x, origin_y, origin_x + plan_w, origin_y + plan_h],
                outline=palette.wall,
                width=wall,
            )

            for room in plan.rooms:
                self._draw_label(draw, room, px, ppf, palette)

            self._draw_dimension_lines(draw, plan, origin_x, origin_y, plan_w, plan_h, ppf, palette)
            self._draw_title_block(draw, plan, w, h, margin_x, title, subtitle, palette)

        return image.resize(self._canvas, Image.LANCZOS)

    # --- Room graphics ----------------------------------------------------
    @staticmethod
    def _draw_room_fill(draw: ImageDraw.ImageDraw, room: Rect, px, palette: Palette) -> None:
        x1, y1 = px(room.x, room.y2)
        x2, y2 = px(room.x2, room.y)
        group = ROOM_GROUP.get(room.type, "service")
        draw.rectangle([x1, y1, x2, y2], fill=palette.room_fills[group])

    @staticmethod
    def _draw_wall_model(draw: ImageDraw.ImageDraw, model, px, palette: Palette) -> None:
        """Wall poche straight from the modeled wall geometry.

        External walls are drawn heavy in the wall colour, internal partitions
        lighter; the polygons are already final (bands unioned and clipped), so
        this is exact rather than inferred from room adjacency.
        """
        for segment in model.segments:
            ring = segment.polygon.exterior
            coords = [(px(v[0], v[1])) for v in ring.coords]
            fill = palette.wall if segment.kind == "external" else palette.partition
            draw.polygon(coords, fill=fill, outline=fill)

    @staticmethod
    def _draw_walls(
        draw: ImageDraw.ImageDraw, room: Rect, px, partition: int, palette: Palette
    ) -> None:
        x1, y1 = px(room.x, room.y2)
        x2, y2 = px(room.x2, room.y)
        draw.rectangle([x1, y1, x2, y2], outline=palette.partition, width=partition)

    def _draw_room_features(
        self, draw: ImageDraw.ImageDraw, room: Rect, px, ppf: float, palette: Palette
    ) -> None:
        """Per-room symbols: treads, hatching, fixtures."""
        x1, y1 = px(room.x, room.y2)
        x2, y2 = px(room.x2, room.y)

        if room.type is RoomType.STAIRCASE:
            self._draw_treads(draw, x1, y1, x2, y2, palette)
        elif room.type is RoomType.PARKING:
            self._draw_hatch(draw, x1, y1, x2, y2, palette.muted, spacing=ppf * 1.4)
        elif room.type is RoomType.GARDEN:
            self._draw_stipple(draw, x1, y1, x2, y2, palette.accent, spacing=ppf * 1.6)
        elif room.type is RoomType.BALCONY:
            self._draw_hatch(draw, x1, y1, x2, y2, palette.muted, spacing=ppf * 2.2)
        elif room.type.is_bathroom:
            self._draw_sanitary(draw, x1, y1, x2, y2, ppf, palette)
        elif room.type is RoomType.KITCHEN:
            self._draw_counter(draw, x1, y1, x2, y2, ppf, palette)
        elif room.type.is_bedroom:
            self._draw_bed(draw, x1, y1, x2, y2, ppf, palette)

    @staticmethod
    def _draw_treads(draw: ImageDraw.ImageDraw, x1, y1, x2, y2, palette: Palette) -> None:
        steps = 9
        horizontal = (x2 - x1) > (y2 - y1)
        for i in range(1, steps):
            t = i / steps
            if horizontal:
                x = x1 + (x2 - x1) * t
                draw.line([x, y1, x, y2], fill=palette.muted, width=2)
            else:
                y = y1 + (y2 - y1) * t
                draw.line([x1, y, x2, y], fill=palette.muted, width=2)
        # Direction arrow along the flight.
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if horizontal:
            draw.line([x1 + 6, cy, x2 - 6, cy], fill=palette.muted, width=2)
        else:
            draw.line([cx, y1 + 6, cx, y2 - 6], fill=palette.muted, width=2)

    @staticmethod
    def _draw_hatch(draw: ImageDraw.ImageDraw, x1, y1, x2, y2, colour: str, spacing: float) -> None:
        spacing = max(8.0, spacing)
        span = int((x2 - x1) + (y2 - y1))
        offset = 0
        while offset < span:
            draw.line(
                [
                    max(x1, x1 + offset - (y2 - y1)),
                    min(y2, y1 + offset),
                    min(x2, x1 + offset),
                    max(y1, y1 + offset - (x2 - x1)),
                ],
                fill=colour,
                width=1,
            )
            offset += int(spacing)

    @staticmethod
    def _draw_stipple(
        draw: ImageDraw.ImageDraw, x1, y1, x2, y2, colour: str, spacing: float
    ) -> None:
        spacing = max(10.0, spacing)
        y = y1 + spacing / 2
        row = 0
        while y < y2 - 2:
            x = x1 + spacing / 2 + (spacing / 2 if row % 2 else 0)
            while x < x2 - 2:
                r = max(1.5, spacing * 0.09)
                draw.ellipse([x - r, y - r, x + r, y + r], outline=colour, width=1)
                x += spacing
            y += spacing
            row += 1

    @staticmethod
    def _draw_sanitary(
        draw: ImageDraw.ImageDraw, x1, y1, x2, y2, ppf: float, palette: Palette
    ) -> None:
        """WC pan and a wash basin against the longer wall."""
        w, h = x2 - x1, y2 - y1
        if w < 12 or h < 12:
            return
        pad = max(3.0, ppf * 0.4)
        unit = min(w, h) * 0.34
        # WC
        draw.rounded_rectangle(
            [x1 + pad, y1 + pad, x1 + pad + unit * 0.8, y1 + pad + unit * 1.15],
            radius=max(2, unit * 0.3),
            outline=palette.muted,
            width=2,
        )
        # Basin
        draw.ellipse(
            [x2 - pad - unit, y2 - pad - unit * 0.75, x2 - pad, y2 - pad],
            outline=palette.muted,
            width=2,
        )

    @staticmethod
    def _draw_counter(
        draw: ImageDraw.ImageDraw, x1, y1, x2, y2, ppf: float, palette: Palette
    ) -> None:
        """L-shaped platform with a sink and a hob."""
        w, h = x2 - x1, y2 - y1
        if w < 16 or h < 16:
            return
        depth = max(5.0, min(w, h) * 0.18)
        draw.rectangle([x1 + 2, y1 + 2, x2 - 2, y1 + 2 + depth], outline=palette.muted, width=2)
        draw.rectangle([x1 + 2, y1 + 2, x1 + 2 + depth, y2 - 2], outline=palette.muted, width=2)
        # Sink
        sx = x1 + depth + (w - depth) * 0.25
        draw.rectangle(
            [sx, y1 + 5, sx + depth * 0.9, y1 + depth - 1], outline=palette.muted, width=1
        )
        # Hob
        hx = x1 + depth + (w - depth) * 0.62
        for i in range(2):
            for j in range(2):
                r = depth * 0.13
                cx, cy = hx + i * depth * 0.42, y1 + depth * 0.35 + j * depth * 0.42
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=palette.muted, width=1)

    @staticmethod
    def _draw_bed(
        draw: ImageDraw.ImageDraw, x1, y1, x2, y2, ppf: float, palette: Palette
    ) -> None:
        """Bed with pillows and a wardrobe strip, oriented along the short wall."""
        w, h = x2 - x1, y2 - y1
        if w < 30 or h < 30:
            return
        pad = max(4.0, ppf * 0.5)
        if w >= h:
            bw, bh = min(w * 0.5, ppf * 6.5), min(h * 0.72, ppf * 6.0)
            bx, by = x1 + pad, y1 + (h - bh) / 2
            draw.rectangle([bx, by, bx + bw, by + bh], outline=palette.muted, width=2)
            draw.line([bx + bw * 0.22, by, bx + bw * 0.22, by + bh], fill=palette.muted, width=1)
            draw.rectangle(
                [x2 - pad - ppf * 1.9, y1 + pad, x2 - pad, y2 - pad],
                outline=palette.muted,
                width=1,
            )
        else:
            bw, bh = min(w * 0.72, ppf * 6.0), min(h * 0.5, ppf * 6.5)
            bx, by = x1 + (w - bw) / 2, y1 + pad
            draw.rectangle([bx, by, bx + bw, by + bh], outline=palette.muted, width=2)
            draw.line([bx, by + bh * 0.22, bx + bw, by + bh * 0.22], fill=palette.muted, width=1)
            draw.rectangle(
                [x1 + pad, y2 - pad - ppf * 1.9, x2 - pad, y2 - pad],
                outline=palette.muted,
                width=1,
            )

    # --- Openings ---------------------------------------------------------
    def _draw_openings(
        self,
        draw: ImageDraw.ImageDraw,
        plan: LayoutPlan,
        px,
        ppf: float,
        palette: Palette,
        wall: int,
    ) -> None:
        """Doors and windows from the model when present, else inferred.

        Solver plans carry modeled ``plan.doors`` / ``plan.windows`` (validated
        geometry), so those are drawn exactly. Legacy plans have none, so their
        openings are re-derived from room adjacency and external walls as
        before.
        """
        if plan.doors or plan.windows:
            for door in plan.doors:
                pair = self._door_room_pair(door, plan.rooms)
                if pair is not None:
                    self._draw_door(draw, pair[0], pair[1], px, ppf, palette)
            for window in plan.windows:
                self._draw_modeled_window(draw, window, px, ppf, palette, wall)
            return

        drawn: set[tuple[int, int]] = set()
        for i, a in enumerate(plan.rooms):
            for j in range(i + 1, len(plan.rooms)):
                b = plan.rooms[j]
                if not a.is_adjacent(b) or (i, j) in drawn:
                    continue
                drawn.add((i, j))
                self._draw_door(draw, a, b, px, ppf, palette)

        for room in plan.rooms:
            self._draw_windows(draw, room, plan, px, ppf, palette, wall)

    @staticmethod
    def _door_room_pair(door, rooms: list[Rect]) -> tuple[Rect, Rect] | None:
        """The two rooms whose shared wall ``door`` sits on, or ``None``."""
        tolerance = 1.25
        for i, a in enumerate(rooms):
            for b in rooms[i + 1 :]:
                run = a.shared_wall_length(b)
                if run <= 0:
                    continue
                if door.orientation == "vertical":
                    if not (abs(a.x2 - b.x) <= tolerance or abs(b.x2 - a.x) <= tolerance):
                        continue
                    line = (a.x2 + b.x) / 2 if abs(a.x2 - b.x) <= tolerance else (b.x2 + a.x) / 2
                    if abs(door.x - line) > tolerance:
                        continue
                    lo, hi = max(a.y, b.y), min(a.y2, b.y2)
                    inside = door.y >= lo - tolerance and door.y + door.width <= hi + tolerance
                else:
                    if not (abs(a.y2 - b.y) <= tolerance or abs(b.y2 - a.y) <= tolerance):
                        continue
                    line = (a.y2 + b.y) / 2 if abs(a.y2 - b.y) <= tolerance else (b.y2 + a.y) / 2
                    if abs(door.y - line) > tolerance:
                        continue
                    lo, hi = max(a.x, b.x), min(a.x2, b.x2)
                    inside = door.x >= lo - tolerance and door.x + door.width <= hi + tolerance
                if inside:
                    return a, b
        return None

    @staticmethod
    def _draw_modeled_window(
        draw: ImageDraw.ImageDraw,
        window,
        px,
        ppf: float,
        palette: Palette,
        wall: int,
    ) -> None:
        """A window from the model, at its exact (x, y, width, orientation)."""
        thickness = max(3, int(wall * 0.75))
        if window.orientation == "vertical":
            x1, y1 = px(window.x, window.y + window.width)
            x2, y2 = px(window.x, window.y)
        else:
            x1, y1 = px(window.x, window.y)
            x2, y2 = px(window.x + window.width, window.y)
        draw.line([x1, y1, x2, y2], fill=palette.paper, width=thickness)
        draw.line([x1, y1, x2, y2], fill=palette.muted, width=max(1, thickness // 3))

    @staticmethod
    def _draw_door(
        draw: ImageDraw.ImageDraw, a: Rect, b: Rect, px, ppf: float, palette: Palette
    ) -> None:
        leaf = min(3.0, a.shared_wall_length(b) * 0.6)
        if leaf < 2.0:
            return
        radius = leaf * ppf

        vertical = abs(a.x2 - b.x) < 1.25 or abs(b.x2 - a.x) < 1.25
        if vertical:
            wall_x = a.x2 if abs(a.x2 - b.x) < 1.25 else b.x2
            lo = max(a.y, b.y)
            hi = min(a.y2, b.y2)
            hinge_y = lo + (hi - lo - leaf) / 2 + leaf
            hx, hy = px(wall_x, hinge_y)
            opens_right = b.center[0] > a.center[0]
            # Clear the wall poche, then sweep the leaf.
            draw.line([hx, hy, hx, hy + radius], fill=palette.paper, width=max(3, int(ppf * 0.4)))
            box = [hx - radius, hy - radius, hx + radius, hy + radius]
            start, end = (270, 360) if opens_right else (180, 270)
            draw.arc(box, start=start, end=end, fill=palette.muted, width=2)
            end_x = hx + (radius if opens_right else -radius)
            draw.line([hx, hy, end_x, hy], fill=palette.muted, width=2)
        else:
            wall_y = a.y2 if abs(a.y2 - b.y) < 1.25 else b.y2
            lo = max(a.x, b.x)
            hi = min(a.x2, b.x2)
            hinge_x = lo + (hi - lo - leaf) / 2
            hx, hy = px(hinge_x, wall_y)
            opens_up = b.center[1] > a.center[1]
            draw.line([hx, hy, hx + radius, hy], fill=palette.paper, width=max(3, int(ppf * 0.4)))
            box = [hx - radius, hy - radius, hx + radius, hy + radius]
            start, end = (270, 360) if opens_up else (0, 90)
            draw.arc(box, start=start, end=end, fill=palette.muted, width=2)
            end_y = hy - radius if opens_up else hy + radius
            draw.line([hx, hy, hx, end_y], fill=palette.muted, width=2)

    @staticmethod
    def _draw_windows(
        draw: ImageDraw.ImageDraw,
        room: Rect,
        plan: LayoutPlan,
        px,
        ppf: float,
        palette: Palette,
        wall: int,
    ) -> None:
        """A window centred on each external wall long enough to take one."""
        if room.type in {RoomType.PARKING, RoomType.GARDEN}:
            return

        edges = (
            (abs(room.x) < 0.6, "left", room.height),
            (abs(room.x2 - plan.plot_width) < 0.6, "right", room.height),
            (abs(room.y) < 0.6, "bottom", room.width),
            (abs(room.y2 - plan.plot_length) < 0.6, "top", room.width),
        )
        thickness = max(3, int(wall * 0.75))
        for is_external, side, run in edges:
            if not is_external or run < 6.0:
                continue
            span = min(run * 0.45, 5.0)
            if side in ("left", "right"):
                x = room.x if side == "left" else room.x2
                mid = room.y + room.height / 2
                x1, y1 = px(x, mid + span / 2)
                x2, y2 = px(x, mid - span / 2)
            else:
                y = room.y if side == "bottom" else room.y2
                mid = room.x + room.width / 2
                x1, y1 = px(mid - span / 2, y)
                x2, y2 = px(mid + span / 2, y)
            draw.line([x1, y1, x2, y2], fill=palette.paper, width=thickness)
            draw.line([x1, y1, x2, y2], fill=palette.muted, width=max(1, thickness // 3))

    # --- Annotation -------------------------------------------------------
    @staticmethod
    def _draw_label(
        draw: ImageDraw.ImageDraw, room: Rect, px, ppf: float, palette: Palette
    ) -> None:
        x1, y1 = px(room.x, room.y2)
        x2, y2 = px(room.x2, room.y)
        box_w, box_h = x2 - x1, y2 - y1
        if box_w < 26 or box_h < 20:
            return

        name_size = int(max(11, min(box_w * 0.115, box_h * 0.16, ppf * 1.55)))
        dim_size = int(name_size * 0.82)
        name_font = _load_font(name_size, bold=True)
        dim_font = _load_font(dim_size)

        name = room.name.upper()
        dims = f"{_feet_inches(room.width)} x {_feet_inches(room.height)}"

        # Shrink until the longer of the two lines fits inside the room.
        while name_size > 8:
            name_w = draw.textlength(name, font=name_font)
            dim_w = draw.textlength(dims, font=dim_font)
            if max(name_w, dim_w) <= box_w * 0.9:
                break
            name_size -= 1
            dim_size = int(name_size * 0.82)
            name_font = _load_font(name_size, bold=True)
            dim_font = _load_font(dim_size)

        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        show_dims = box_h > name_size * 2.6
        top = cy - (name_size * 1.15 if show_dims else name_size * 0.55)

        draw.text((cx, top), name, font=name_font, fill=palette.text, anchor="ma")
        if show_dims:
            draw.text(
                (cx, top + name_size * 1.25), dims, font=dim_font, fill=palette.muted, anchor="ma"
            )

    @staticmethod
    def _draw_dimension_lines(
        draw: ImageDraw.ImageDraw,
        plan: LayoutPlan,
        ox: float,
        oy: float,
        pw: float,
        ph: float,
        ppf: float,
        palette: Palette,
    ) -> None:
        offset, tick, font_size = _dimension_metrics(ppf)
        font = _load_font(font_size, bold=True)

        # Width, above the plan.
        y = oy - offset
        draw.line([ox, y, ox + pw, y], fill=palette.dimension, width=2)
        for x in (ox, ox + pw):
            draw.line([x, y - tick, x, y + tick], fill=palette.dimension, width=2)
        draw.text(
            (ox + pw / 2, y - tick - 4),
            _feet_inches(plan.plot_width),
            font=font,
            fill=palette.dimension,
            anchor="mb",
        )

        # Length, to the right of the plan.
        x = ox + pw + offset
        draw.line([x, oy, x, oy + ph], fill=palette.dimension, width=2)
        for yy in (oy, oy + ph):
            draw.line([x - tick, yy, x + tick, yy], fill=palette.dimension, width=2)
        draw.text(
            (x + tick + 4, oy + ph / 2),
            _feet_inches(plan.plot_length),
            font=font,
            fill=palette.dimension,
            anchor="lm",
        )

    @staticmethod
    def _draw_title_block(
        draw: ImageDraw.ImageDraw,
        plan: LayoutPlan,
        w: int,
        h: int,
        margin: int,
        title: str,
        subtitle: str,
        palette: Palette,
    ) -> None:
        title_font = _load_font(int(h * 0.032), bold=True)
        sub_font = _load_font(int(h * 0.019))
        foot_font = _load_font(int(h * 0.017))

        draw.text(
            (w / 2, h * 0.036), title.upper(), font=title_font, fill=palette.text, anchor="ma"
        )
        if subtitle:
            draw.text(
                (w / 2, h * 0.036 + h * 0.040),
                subtitle,
                font=sub_font,
                fill=palette.muted,
                anchor="ma",
            )

        baseline = h - int(h * 0.052)
        draw.line(
            [margin, baseline - int(h * 0.018), w - margin, baseline - int(h * 0.018)],
            fill=palette.dimension,
            width=2,
        )
        left = (
            f"PLOT {_feet_inches(plan.plot_width)} x {_feet_inches(plan.plot_length)}   |   "
            f"BUILT-UP {plan.built_up_sqft:,.0f} SQ FT   |   {plan.room_count} SPACES"
        )
        draw.text((margin, baseline), left, font=foot_font, fill=palette.muted, anchor="la")
        draw.text(
            (w - margin, baseline),
            "GROUND FLOOR PLAN",
            font=foot_font,
            fill=palette.muted,
            anchor="ra",
        )

        # North arrow, bottom-right above the title block.
        cx, cy = w - margin - int(h * 0.028), baseline - int(h * 0.062)
        r = h * 0.022
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=palette.dimension, width=2)
        draw.polygon(
            [
                (cx, cy - r * 0.78),
                (cx - r * 0.32, cy + r * 0.5),
                (cx, cy + r * 0.22),
                (cx + r * 0.32, cy + r * 0.5),
            ],
            fill=palette.dimension,
        )
        draw.text(
            (cx, cy - r - 3), "N", font=_load_font(int(h * 0.017), bold=True),
            fill=palette.dimension, anchor="mb",
        )


def render_to_file(
    plan: LayoutPlan,
    destination: Path,
    *,
    style: InteriorStyle,
    title: str,
    subtitle: str = "",
    size: tuple[int, int] = (1024, 1024),
) -> Path:
    renderer = FloorPlanRenderer(*size)
    image = renderer.render(plan, style=style, title=title, subtitle=subtitle)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=True)
    return destination


__all__ = ["FloorPlanRenderer", "Palette", "render_to_file"]
