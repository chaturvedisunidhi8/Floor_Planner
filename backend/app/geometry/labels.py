"""The feet-and-inches label format the renderer and the geometry audit share.

The renderer draws room labels and overall dimension lines with
:func:`feet_inches`; the geometry-accuracy scorer needs to prove those labels
round-trip back to the authoritative dimensions, so the format lives here
instead of being duplicated in both modules and silently drifting.
"""

from __future__ import annotations


def feet_inches(value: float) -> str:
    """A dimension in feet to the classic ``12'6"`` label, to the nearest inch."""
    feet = int(value)
    inches = int(round((value - feet) * 12))
    if inches == 12:
        feet, inches = feet + 1, 0
    return f"{feet}'{inches}\"" if inches else f"{feet}'"


def decode_feet_inches(text: str) -> float:
    """Parse the :func:`feet_inches` output back into feet (``12'6"`` -> 12.5)."""
    feet, _, rest = text.partition("'")
    inches = 0
    if rest:
        inches = int(rest.rstrip('"') or 0)
    return float(feet) + inches / 12.0


__all__ = ["decode_feet_inches", "feet_inches"]
