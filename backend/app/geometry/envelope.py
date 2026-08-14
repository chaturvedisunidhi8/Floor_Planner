"""The buildable envelope - the plot minus setbacks.

Milestone A defaults setbacks to zero, so the envelope is the whole plot; the
structure exists so the solver never needs to relearn the bounds if setbacks
arrive later.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.geometry.units import MAX_EXTENT_CELLS, area_to_cells, to_cells

#: The four sides a setback can be measured from.
SIDES = ("left", "right", "bottom", "top")


@dataclass(frozen=True)
class Envelope:
    """The rectangle every room must pack inside, in feet."""

    width: float
    length: float
    #: Feet of forbidden margin per side. All zero in Milestone A.
    setbacks: dict[str, float] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "setbacks",
            {side: 0.0 for side in SIDES}
            if self.setbacks is None
            else {side: max(0.0, float(self.setbacks.get(side, 0.0))) for side in SIDES},
        )

    @property
    def buildable_width(self) -> float:
        return max(0.0, self.width - self.setbacks["left"] - self.setbacks["right"])

    @property
    def buildable_length(self) -> float:
        return max(0.0, self.length - self.setbacks["bottom"] - self.setbacks["top"])

    @property
    def area_sqft(self) -> float:
        return round(self.buildable_width * self.buildable_length, 2)

    @property
    def cells(self) -> tuple[int, int]:
        """(width, length) of the buildable area in solver cells."""
        return to_cells(self.buildable_width), to_cells(self.buildable_length)

    @property
    def area_cells(self) -> int:
        w, h = self.cells
        return w * h

    @property
    def within_max_extent(self) -> bool:
        """The solver's integer grid caps an axis at 200 cells (100 ft)."""
        w, h = self.cells
        return w <= MAX_EXTENT_CELLS and h <= MAX_EXTENT_CELLS

    def fits_area(self, demanded_sqft: float) -> bool:
        """True when ``demanded_sqft`` could physically be tiled in here."""
        if not self.within_max_extent:
            return False
        return area_to_cells(demanded_sqft) <= self.area_cells

    def describe(self) -> str:
        base = f"{self.width:g}x{self.length:g} ft"
        if self.setbacks and any(self.setbacks.values()):
            return f"{base} minus setbacks {self.setbacks}"
        return base
