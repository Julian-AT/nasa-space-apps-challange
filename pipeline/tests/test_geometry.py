"""Tests for the body radii of the D29 window resampler (PRD 7, "Window resampler (D29)")."""

from __future__ import annotations

import re
import typing
from pathlib import Path

import numpy as np
import pytest

from terraluna_pipeline.geometry import BODY_RADII_M, Body, UnknownBodyError, body_radius_m

PRD = Path(__file__).resolve().parents[2] / "PRD.md"


def prd_radii_m() -> dict[str, float]:
    """The radii stated on the PRD 7 "Radii (spheres)" line, in metres."""
    text = PRD.read_text(encoding="utf-8")
    line = next(row for row in text.splitlines() if row.startswith("- **Radii (spheres):**"))
    found = re.findall(r"(Moon|Mars|Earth) ([0-9][0-9,]*(?:\.[0-9]+)?) m", line)
    return {name.lower(): float(value.replace(",", "")) for name, value in found}


def test_radii_are_exactly_the_d29_values() -> None:
    assert dict(BODY_RADII_M) == {"moon": 1737400.0, "mars": 3396190.0, "earth": 6371008.8}


def test_radii_match_the_prd_text() -> None:
    assert prd_radii_m() == {"moon": 1737400.0, "mars": 3396190.0, "earth": 6371008.8}
    assert dict(BODY_RADII_M) == prd_radii_m()


def test_radii_are_float64() -> None:
    for body, radius in BODY_RADII_M.items():
        assert type(radius) is float, body
        assert np.asarray(radius).dtype == np.float64, body
        assert np.float64(radius) == radius, body


def test_body_literal_lists_exactly_the_three_bodies() -> None:
    assert typing.get_args(Body) == ("moon", "mars", "earth")
    assert tuple(BODY_RADII_M) == typing.get_args(Body)


@pytest.mark.parametrize(
    ("body", "radius"), [("moon", 1737400.0), ("mars", 3396190.0), ("earth", 6371008.8)]
)
def test_body_radius_resolves_the_three_bodies(body: str, radius: float) -> None:
    assert body_radius_m(body) == radius
    assert type(body_radius_m(body)) is float


@pytest.mark.parametrize(
    "body",
    ["venus", "sun", "titan", "luna", "Moon", "MARS", " earth", "earth ", "", "moon\n", "ceres"],
)
def test_no_other_body_resolves(body: str) -> None:
    with pytest.raises(UnknownBodyError) as caught:
        body_radius_m(body)
    assert repr(body) in str(caught.value)


def test_unknown_body_error_is_a_value_error() -> None:
    assert issubclass(UnknownBodyError, ValueError)


def test_radii_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        BODY_RADII_M["venus"] = 6051800.0  # type: ignore[index]
    with pytest.raises(TypeError):
        BODY_RADII_M["moon"] = 1.0  # type: ignore[index]
    assert BODY_RADII_M["moon"] == 1737400.0
