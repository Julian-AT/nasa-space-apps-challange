"""Body spheres for the D29 window resampler (PRD 7, "Window resampler (D29)").

Terrain windows are resampled on a sphere per body, in float64. The radii below are the only
radii the resampler uses; ``@terraluna/core`` mirrors them for the browser probe (PRD 8.8).
Terrain distances derived from them are metres on the body sphere and are kept apart from radar
and model distances (PRD 6.5, 8.11).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

Body = Literal["moon", "mars", "earth"]
"""The bodies with a resampler sphere. Names are lower case and matched exactly."""

BODY_RADII_M: Final[Mapping[str, float]] = MappingProxyType(
    {
        "moon": 1737400.0,
        "mars": 3396190.0,
        # Mean radius. The sphere changes scale by under 0.5% on a 5 km window (PRD 7).
        "earth": 6371008.8,
    }
)
"""Sphere radius per body in metres, as Python floats (IEEE 754 binary64, numpy float64).

The table is read-only. Use :func:`body_radius_m` to look up a body by name.
"""


class UnknownBodyError(ValueError):
    """Raised when a body has no resampler sphere."""


def body_radius_m(body: str) -> float:
    """Return the D29 sphere radius of ``body`` in metres.

    Only ``moon``, ``mars`` and ``earth`` resolve. Matching is exact: ``"Moon"`` or ``" moon"``
    raise :class:`UnknownBodyError` rather than being normalised.
    """
    try:
        return BODY_RADII_M[body]
    except KeyError:
        known = ", ".join(BODY_RADII_M)
        raise UnknownBodyError(f"no D29 sphere for body {body!r}; known bodies: {known}") from None


class WindowGridError(ValueError):
    """Raised when a window width or pixel spacing cannot define a pixel-centre grid."""


@dataclass(frozen=True, eq=False)
class WindowGrid:
    """Pixel centres of a square analysis window, in metres from the window centre (PRD 7, D29).

    Column ``i`` sits at ``x_i = (i + 0.5) * spacing - width / 2`` (east positive) and row ``j``
    at ``y_j = width / 2 - (j + 0.5) * spacing`` (north positive, row 0 at the top).

    ``size`` is the largest ``n`` with ``n * spacing <= width`` in float64, so every pixel cell
    lies inside the window. The grid is anchored at the top-left corner: when the width is not a
    whole number of cells, a strip ``uncovered_m`` wide is left along the east and south edges.
    At the PRD's 5000 m window and 90 m grid that is 55 cells and a 50 m strip, and the centre
    pixel (27, 27) sits at (-25 m, 25 m).
    """

    width_m: float
    spacing_m: float
    size: int
    x_m: FloatArray
    y_m: FloatArray

    @property
    def uncovered_m(self) -> float:
        """Width of the strip along the east and south edges that no pixel cell covers."""
        return self.width_m - self.size * self.spacing_m

    def mesh_m(self) -> tuple[FloatArray, FloatArray]:
        """Return read-only 2-D ``(x, y)`` arrays indexed ``[row, column]``."""
        x, y = np.meshgrid(self.x_m, self.y_m, indexing="xy")
        return _read_only(x), _read_only(y)


def window_grid(width_m: float, spacing_m: float) -> WindowGrid:
    """Return the D29 pixel-centre grid for a window ``width_m`` wide at ``spacing_m``.

    Raises :class:`WindowGridError` if either length is not finite and positive, or if not even
    one cell fits.
    """
    width = float(width_m)
    spacing = float(spacing_m)
    for name, value in (("width_m", width), ("spacing_m", spacing)):
        if not (math.isfinite(value) and value > 0.0):
            raise WindowGridError(f"{name} must be finite and positive, got {value!r}")
    ratio = width / spacing
    if not math.isfinite(ratio):
        raise WindowGridError(f"width_m / spacing_m is not finite: {width!r} / {spacing!r}")
    size = math.floor(ratio)
    # Settle the count on the stated rule, n * spacing <= width, not on the rounded ratio.
    while size > 0 and size * spacing > width:
        size -= 1
    while (size + 1) * spacing <= width:
        size += 1
    if size < 1:
        raise WindowGridError(f"no {spacing!r} m cell fits in a {width!r} m window")
    centres = (np.arange(size, dtype=np.float64) + 0.5) * spacing
    return WindowGrid(
        width_m=width,
        spacing_m=spacing,
        size=size,
        x_m=_read_only(centres - width / 2),
        y_m=_read_only(width / 2 - centres),
    )


def _read_only(values: FloatArray) -> FloatArray:
    frozen = np.array(values, dtype=np.float64, copy=True)
    frozen.flags.writeable = False
    return frozen
