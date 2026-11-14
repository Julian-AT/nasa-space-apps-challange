"""Body spheres for the D29 window resampler (PRD 7, "Window resampler (D29)").

Terrain windows are resampled on a sphere per body, in float64. The radii below are the only
radii the resampler uses; ``@terraluna/core`` mirrors them for the browser probe (PRD 8.8).
Terrain distances derived from them are metres on the body sphere and are kept apart from radar
and model distances (PRD 6.5, 8.11).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

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
