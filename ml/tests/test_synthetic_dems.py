"""Synthetic DEM sources for the encoder dataset (T11-01, PRD 11.2, 12 T11).

Every synthetic DEM is a labelled mock: it carries its body, its nodata value and ``mock=True``,
and is built deterministically from an explicit integer seed. A DEM only supplies a float64
source raster, its nodata value and a T3 lookup (``terraluna_pipeline.geometry``); windows are
cut from it only by the D29 resampler (tests in test_dataset_window.py).
"""

from __future__ import annotations

import dataclasses
import math
import typing

import numpy as np
import pytest

from terraluna_ml import synthetic
from terraluna_ml.synthetic import (
    MOCK_LABEL,
    NODATA_SENTINEL,
    DemSource,
    SyntheticDem,
    SyntheticDemError,
    fbm_dem,
    hole_dem,
    plane_dem,
    polar_fourfold_dem,
)
from terraluna_pipeline.geometry import (
    BODY_RADII_M,
    EquirectangularLookup,
    PolarStereographicLookup,
    aeqd_forward,
)

BODIES = ("moon", "mars", "earth")


def small_dems(seed: int) -> list[SyntheticDem]:
    """One DEM of each kind, small enough for quick tests."""
    common = {"half_width_m": 1000.0, "pixel_m": 20.0}
    return [
        plane_dem(seed=seed, body="mars", center_lat_deg=12.0, center_lon_deg=-40.0, **common),
        fbm_dem(
            seed=seed,
            body="earth",
            hurst=0.7,
            rms_m=15.0,
            center_lat_deg=45.0,
            center_lon_deg=10.0,
            **common,
        ),
        hole_dem(
            seed=seed,
            body="moon",
            hurst=0.6,
            rms_m=10.0,
            hole_radius_m=150.0,
            hole_offset_m=(100.0, -80.0),
            center_lat_deg=-30.0,
            center_lon_deg=100.0,
            **common,
        ),
        polar_fourfold_dem(
            seed=seed,
            body="moon",
            pole="south",
            hurst=0.8,
            rms_m=20.0,
            half_size_px=40,
            pixel_m=20.0,
        ),
    ]


@pytest.mark.parametrize("index", range(4))
def test_every_synthetic_dem_is_a_labelled_mock(index: int) -> None:
    dem = small_dems(7)[index]
    assert dem.mock is True
    assert dem.label == MOCK_LABEL == "mock"
    assert dem.body in BODIES
    assert dem.seed == 7
    assert dem.name in ("plane", "fbm", "hole", "polar_fourfold")
    assert dem.raster.dtype == np.float64
    assert dem.raster.ndim == 2
    assert not dem.raster.flags.writeable
    assert dem.nodata == NODATA_SENTINEL
    assert isinstance(dem.lookup, EquirectangularLookup | PolarStereographicLookup)
    # The lookup's sphere is the body's D29 sphere.
    assert dem.lookup.radius_m == BODY_RADII_M[dem.body]
    with pytest.raises(dataclasses.FrozenInstanceError):
        dem.mock = False  # type: ignore[misc]


@pytest.mark.parametrize("index", range(4))
def test_the_same_seed_gives_byte_identical_dems(index: int) -> None:
    first, second = small_dems(11)[index], small_dems(11)[index]
    assert first.raster.tobytes() == second.raster.tobytes()
    assert first.lookup == second.lookup
    assert dict(first.parameters) == dict(second.parameters)
    other = small_dems(12)[index]
    assert first.raster.tobytes() != other.raster.tobytes()


def test_the_dem_source_protocol_supplies_only_a_raster_and_its_metadata() -> None:
    # A real reader (T11-07) implements exactly these members; none of them evaluates
    # elevation at a latitude and longitude, so every window goes through the resampler.
    members = {name for name in vars(DemSource) if not name.startswith("_")}
    assert members == {"body", "raster", "nodata", "lookup", "mock"}
    assert all(isinstance(vars(DemSource)[name], property) for name in members)
    assert typing.get_type_hints(DemSource) == {}
    source: DemSource = small_dems(0)[1]
    assert (source.body, source.nodata, source.mock) == ("earth", NODATA_SENTINEL, True)


def test_a_plane_dem_is_a_plane_in_the_window_frame_about_its_centre() -> None:
    dem = plane_dem(
        seed=3,
        body="mars",
        center_lat_deg=12.0,
        center_lon_deg=-40.0,
        half_width_m=1000.0,
        pixel_m=20.0,
    )
    a, b, c = (dem.parameters[key] for key in ("a_m", "b", "c"))
    assert dem.raster.shape == (100, 100)
    columns, rows = np.meshgrid(np.arange(100.0), np.arange(100.0), indexing="xy")
    lat, lon = dem.lookup.from_pixel(columns, rows)
    x, y = aeqd_forward(
        lat,
        lon,
        lat0_rad=math.radians(12.0),
        lon0_rad=math.radians(-40.0),
        radius_m=BODY_RADII_M["mars"],
    )
    np.testing.assert_allclose(dem.raster, a + b * x + c * y, rtol=1e-10, atol=1e-12)
    assert b != 0.0 and c != 0.0


def test_an_fbm_dem_has_the_requested_rms_and_zero_mean() -> None:
    dem = small_dems(5)[1]
    assert dem.name == "fbm"
    assert dem.parameters["hurst"] == 0.7
    assert math.isclose(float(np.std(dem.raster)), 15.0, rel_tol=1e-12)
    assert abs(float(np.mean(dem.raster))) < 1e-9 * 15.0
    assert np.all(dem.raster != NODATA_SENTINEL)


def test_a_hole_dem_holds_the_nodata_sentinel_exactly_inside_its_disc() -> None:
    dem = small_dems(5)[2]
    assert dem.name == "hole"
    columns, rows = np.meshgrid(np.arange(100.0), np.arange(100.0), indexing="xy")
    east, north = dem.lookup.projected_from_pixel(columns, rows)
    inside = np.hypot(east - 100.0, north + 80.0) <= 150.0
    assert np.count_nonzero(inside) > 100
    assert np.all(dem.raster[inside] == NODATA_SENTINEL)
    assert np.all(dem.raster[~inside] != NODATA_SENTINEL)
    assert np.all(np.isfinite(dem.raster))


def test_a_polar_dem_is_centred_on_the_pole_with_the_pole_on_a_lattice_node() -> None:
    dem = small_dems(9)[3]
    assert dem.name == "polar_fourfold"
    assert isinstance(dem.lookup, PolarStereographicLookup)
    assert dem.lookup.pole == "south"
    assert dem.raster.shape == (81, 81)
    column, row = dem.lookup.to_pixel(-math.pi / 2, 0.0)
    assert (float(column), float(row)) == (40.0, 40.0)
    lat, _ = dem.lookup.from_pixel(40.0, 40.0)
    assert float(lat) == -math.pi / 2


def test_a_polar_dem_is_exactly_four_fold_symmetric_and_not_constant() -> None:
    raster = small_dems(9)[3].raster
    for k in (1, 2, 3):
        assert np.rot90(raster, k).tobytes() == raster.tobytes()
    assert float(np.std(raster)) > 1.0
    # Not mirror symmetric: the rotation test cannot pass with a reflected window.
    assert raster.tobytes() != np.fliplr(raster).tobytes()
    assert raster.tobytes() != raster.T.tobytes()


@pytest.mark.parametrize("seed", [-1, 1.5, True, "1"])
def test_a_seed_must_be_an_explicit_non_negative_integer(seed: object) -> None:
    with pytest.raises(SyntheticDemError):
        fbm_dem(
            seed=seed,  # type: ignore[arg-type]
            body="earth",
            hurst=0.7,
            rms_m=1.0,
            center_lat_deg=0.0,
            center_lon_deg=0.0,
            half_width_m=100.0,
            pixel_m=10.0,
        )


def test_the_raster_width_must_be_a_whole_number_of_pixels() -> None:
    with pytest.raises(SyntheticDemError):
        plane_dem(
            seed=0,
            body="moon",
            center_lat_deg=0.0,
            center_lon_deg=0.0,
            half_width_m=105.0,
            pixel_m=10.0,
        )


def test_an_unknown_body_is_rejected() -> None:
    with pytest.raises(ValueError, match="body"):
        plane_dem(
            seed=0,
            body="venus",
            center_lat_deg=0.0,
            center_lon_deg=0.0,  # type: ignore[arg-type]
            half_width_m=100.0,
            pixel_m=10.0,
        )


def test_the_module_states_that_it_is_mock_only() -> None:
    assert synthetic.__doc__ is not None
    assert "mock" in synthetic.__doc__.lower()
