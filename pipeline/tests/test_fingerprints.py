"""Tests for the terrain fingerprint wrapper, window assembly and input hash (T4L-01).

Every input here is synthetic and the test names say so: analytic surfaces (a plane plus
sinusoids) measured by the merged T3 ``terrain_fingerprint``, a square ring and a temporary
config file. The wrapper must copy T3's nu, lags, reasons and H unchanged (CONTRACTS a.4,
review-science #6); these tests compare the record against the T3 object field by field.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from terraluna_pipeline import fingerprints, provenance
from terraluna_pipeline.config import DetrendPolicy, load_terrain_config
from terraluna_pipeline.fingerprints import (
    COPERNICUS_SURFACE_MODEL_NOTE,
    build_window_record,
    input_hash,
    lag_key,
    terrain_record,
)
from terraluna_pipeline.geometry import window_grid
from terraluna_pipeline.schema import (
    FingerprintRecord,
    ObservedInterpolatedQuality,
    WindowEntry,
    WindowPolygon,
)
from terraluna_pipeline.terrain import DIRECTIONS, TerrainFingerprint, terrain_fingerprint

pytestmark = pytest.mark.filterwarnings("error")

CONFIG = load_terrain_config()
REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_YAML = REPO_ROOT / "config" / "run.yaml"
PRODUCTS: list[tuple[str, str | None]] = [("synthetic-dem-a", "a" * 64)]

Array = npt.NDArray[np.float64]


def synthetic_surface(size: int, *, tilt: bool = True) -> Array:
    """A synthetic surface: a plane plus two sinusoids, metres, no random source."""
    rows, cols = np.mgrid[0:size, 0:size].astype(np.float64)
    field = (
        40.0 * np.sin(cols / 3.1) + 25.0 * np.cos(rows / 4.7) + 9.0 * np.sin((rows + cols) / 2.3)
    )
    if tilt:
        field = field + 1500.0 + 0.8 * cols - 0.3 * rows
    return np.asarray(field, dtype=np.float64)


def synthetic_fingerprint(
    values: Array | None = None, valid: npt.NDArray[np.bool_] | None = None, **changes: Any
) -> TerrainFingerprint:
    config = dataclasses.replace(CONFIG, **changes) if changes else CONFIG
    grid = window_grid(config.window_m, config.analysis_grid_m)
    z = synthetic_surface(grid.size) if values is None else values
    keep = np.ones(z.shape, dtype=np.bool_) if valid is None else valid
    return terrain_fingerprint(
        z,
        valid=keep,
        grid=grid,
        config=config,
        isotropic_rule="direction_mean",
        coarsening_factor=1.0,
    )


SQUARE_M = [[-2500.0, 2500.0], [2500.0, 2500.0], [2500.0, -2500.0], [-2500.0, -2500.0]]
SQUARE_M.append(SQUARE_M[0])
SQUARE_LONLAT = [[-16.8, 64.98], [-16.7, 64.98], [-16.7, 64.94], [-16.8, 64.94], [-16.8, 64.98]]


def synthetic_polygon(body: str = "earth") -> WindowPolygon:
    if body == "earth":
        return WindowPolygon(
            frame="utm",
            crs="EPSG:32627",
            epsg=32627,
            utm_zone="27N",
            ring_m=SQUARE_M,
            ring_lonlat=SQUARE_LONLAT,
        )
    return WindowPolygon(
        frame="aeqd_sphere",
        crs="+proj=aeqd +lat_0=-89.45 +lon_0=-137.31 +R=1737400 +units=m",
        epsg=None,
        utm_zone=None,
        ring_m=SQUARE_M,
        ring_lonlat=SQUARE_LONLAT,
    )


def synthetic_hash(**settings: object) -> str:
    size = window_grid(CONFIG.window_m, CONFIG.analysis_grid_m).size
    return input_hash(
        arrays={"elevation_m": synthetic_surface(size)},
        rings={"ring_m": SQUARE_M},
        settings={"isotropic_rule": "direction_mean", **settings},
    )


def synthetic_terrain_record(
    fp: TerrainFingerprint | None = None,
    window_id: str = "earth-synthetic-w000",
    body: str = "earth",
) -> FingerprintRecord:
    return terrain_record(
        synthetic_fingerprint() if fp is None else fp,
        window_id=window_id,
        body=body,  # type: ignore[arg-type]
        products=PRODUCTS,
        config_path=RUN_YAML,
        input_hash=synthetic_hash(),
    )


NO_PGDA = ObservedInterpolatedQuality(
    observed_fraction=None, interpolated_fraction=None, source=None, reason="not_applicable_body"
)


# --- Lag keys --------------------------------------------------------------------------------


def test_lag_key_writes_whole_metres_without_a_fraction_like_json_numbers_in_typescript() -> None:
    assert lag_key(270.0) == "270"
    assert lag_key(1080.0) == "1080"
    assert lag_key(270.5) == "270.5"
    for bad in (math.nan, math.inf, 0.0, -270.0, 1e-7, 1e22):
        with pytest.raises(ValueError, match="lag"):
            lag_key(bad)


# --- terrain_record ----------------------------------------------------------------------------


def test_synthetic_terrain_record_copies_t3_values_unchanged() -> None:
    fp = synthetic_fingerprint()
    record = synthetic_terrain_record(fp)
    assert record.kind == "terrain"
    assert record.radar is None
    assert record.evidence_label == "Derived"
    assert record.product_ids == ["synthetic-dem-a"]
    assert record.run_id == provenance.run_id(RUN_YAML, PRODUCTS)
    terrain = record.terrain
    assert terrain is not None
    assert terrain.resampler == "aeqd_sphere"
    assert terrain.analysis_grid_m == fp.analysis_grid_m
    assert terrain.coarsening_factor == fp.coarsening_factor
    assert terrain.valid_fraction == fp.valid_fraction
    pairs: tuple[tuple[str, DetrendPolicy], ...] = (("relief", "none"), ("texture", "plane"))
    for name, policy in pairs:
        variant = getattr(terrain, name)
        source = fp.variants[policy]
        assert variant.detrend == policy
        assert variant.supported_lags_m == list(fp.supported_lags_m)
        assert variant.nu_m == {
            lag_key(lag): nu for lag, nu in zip(fp.lags_m, source.nu_m, strict=True)
        }
        assert list(variant.nu_by_direction_m) == list(DIRECTIONS)
        for direction in DIRECTIONS:
            expected = dict(
                zip(map(lag_key, fp.lags_m), source.directional_nu_m[direction], strict=True)
            )
            assert variant.nu_by_direction_m[direction] == expected
        assert variant.hurst == source.hurst.H
        assert variant.hurst_reason == source.hurst.reason
    assert terrain.relief.tilt is None
    plane = fp.variants["plane"].plane
    assert plane is not None
    assert terrain.texture.tilt == [plane.slope_east, plane.slope_north, plane.intercept_m]
    # Relief and texture stay separate: the synthetic tilt changes relief nu, not texture nu.
    assert terrain.relief.nu_m != terrain.texture.nu_m


def test_synthetic_unsupported_h_stays_null_with_its_reason() -> None:
    # Three lags cannot give the four that min_lags_for_H asks for.
    fp = synthetic_fingerprint(lags_m=(270.0, 360.0, 1080.0))
    record = synthetic_terrain_record(fp)
    assert record.terrain is not None
    for variant in (record.terrain.relief, record.terrain.texture):
        assert variant.hurst is None
        assert variant.hurst_reason == "insufficient_lags"
    assert record.evidence_label == "Derived"


def test_synthetic_unsupported_lag_keeps_its_t3_reason_and_a_null_nu() -> None:
    fp = synthetic_fingerprint(lags_m=(180.0, 270.0, 360.0, 540.0, 720.0, 1080.0, 1440.0))
    record = synthetic_terrain_record(fp)
    assert record.terrain is not None
    relief = record.terrain.relief
    reasons = {item.lag_m: item.reason for item in relief.unsupported_lags}
    assert reasons == {180.0: "below_min_lag_cells", 1440.0: "exceeds_quarter_window"}
    assert relief.nu_m["180"] is None
    assert relief.nu_m["1440"] is None
    assert 180.0 not in relief.supported_lags_m


def test_synthetic_excluded_window_is_unavailable_with_null_values() -> None:
    size = window_grid(CONFIG.window_m, CONFIG.analysis_grid_m).size
    valid = np.zeros((size, size), dtype=np.bool_)
    valid[: size // 2] = True
    fp = synthetic_fingerprint(valid=valid)
    assert fp.excluded
    record = synthetic_terrain_record(fp)
    assert record.evidence_label == "Unavailable"
    assert record.terrain is not None
    for variant in (record.terrain.relief, record.terrain.texture):
        assert variant.supported_lags_m == []
        assert all(nu is None for nu in variant.nu_m.values())
        assert {item.reason for item in variant.unsupported_lags} == {"window_excluded"}
        assert variant.hurst is None
        assert variant.hurst_reason == "window_excluded"
    assert record.terrain.texture.tilt is None


def test_synthetic_degenerate_plane_makes_only_texture_unavailable() -> None:
    size = window_grid(CONFIG.window_m, CONFIG.analysis_grid_m).size
    valid = np.zeros((size, size), dtype=np.bool_)
    valid[size // 2] = True  # one row of valid cells: no plane is determined
    fp = synthetic_fingerprint(valid=valid, min_valid_fraction=1.0 / (size * size))
    assert fp.variants["plane"].reason == "plane_fit_degenerate"
    record = synthetic_terrain_record(fp)
    assert record.terrain is not None
    texture = record.terrain.texture
    assert texture.supported_lags_m == []
    assert texture.tilt is None
    assert texture.hurst_reason == "plane_fit_degenerate"
    assert {item.reason for item in texture.unsupported_lags} >= {"plane_fit_degenerate"}
    assert all(nu is None for nu in texture.nu_m.values())


def test_synthetic_terrain_record_needs_both_variants() -> None:
    fp = synthetic_fingerprint(detrend=("none",))
    with pytest.raises(ValueError, match="plane"):
        synthetic_terrain_record(fp)


# --- input_hash -----------------------------------------------------------------------------


def test_synthetic_input_hash_changes_with_arrays_rings_and_settings() -> None:
    base = synthetic_hash()
    assert base == synthetic_hash()
    assert len(base) == 64
    assert synthetic_hash(isotropic_rule="pooled_pairs") != base
    moved = [[x + 1.0, y] for x, y in SQUARE_M]
    assert (
        input_hash(
            arrays={"elevation_m": synthetic_surface(55)},
            rings={"ring_m": moved},
            settings={"isotropic_rule": "direction_mean"},
        )
        != base
    )
    changed = synthetic_surface(55)
    changed[3, 4] += 0.5
    assert (
        input_hash(
            arrays={"elevation_m": changed},
            rings={"ring_m": SQUARE_M},
            settings={"isotropic_rule": "direction_mean"},
        )
        != base
    )


def test_synthetic_input_hash_distinguishes_dtype_shape_and_mask() -> None:
    a = np.arange(6, dtype=np.float64)
    h = input_hash(arrays={"a": a}, rings={}, settings={})
    assert input_hash(arrays={"a": a.reshape(2, 3)}, rings={}, settings={}) != h
    assert input_hash(arrays={"a": a.astype(np.float32)}, rings={}, settings={}) != h
    assert input_hash(arrays={"a": a.astype(">f8")}, rings={}, settings={}) == h
    masked = np.ma.masked_array(a, mask=[False, True, False, False, False, False])
    assert input_hash(arrays={"a": masked}, rings={}, settings={}) != h
    with pytest.raises(ValueError, match="NaN"):
        input_hash(arrays={}, rings={}, settings={"x": math.nan})


def test_changed_config_changes_the_run_id(tmp_path: Path) -> None:
    changed = tmp_path / "run.yaml"
    changed.write_bytes(RUN_YAML.read_bytes() + b"\n# synthetic edit\n")
    fp = synthetic_fingerprint()
    a = synthetic_terrain_record(fp)
    b = terrain_record(
        fp,
        window_id="earth-synthetic-w000",
        body="earth",
        products=PRODUCTS,
        config_path=changed,
        input_hash=synthetic_hash(),
    )
    assert a.run_id != b.run_id


# --- build_window_record ----------------------------------------------------------------------


def synthetic_entry(body: str = "earth", **overrides: object) -> WindowEntry:
    window_id = f"{body}-synthetic-w000"
    terrain = synthetic_terrain_record(window_id=window_id, body=body)
    kwargs: dict[str, object] = {
        "window_id": window_id,
        "body": body,
        "polygon": synthetic_polygon(body),
        "area_m2": 25_000_000.0,
        "analysis_grid_m": 90.0,
        "product_ids": ["synthetic-dem-a"],
        "terrain": terrain,
        "radar": [],
        "status": "eligible",
        "reasons": [],
        "search_region_id": "holuhraun" if body == "earth" else None,
        "scene_ids": [],
        "acquisition_dates": [],
        "permanent_shadow_fraction": None,
        "observed_interpolated_quality": NO_PGDA,
        "grid_index": None,
        "card_id": None,
    }
    kwargs.update(overrides)
    return build_window_record(**kwargs)  # type: ignore[arg-type]


def test_synthetic_earth_window_flags_and_note() -> None:
    entry = synthetic_entry("earth")
    flags = entry.window.environment_flags
    assert flags.snow_or_freeze == "unknown"
    assert flags.moisture_state == "unknown"
    assert flags.surface_change_since_acquisition == "unknown"
    assert flags.surface_model_note == COPERNICUS_SURFACE_MODEL_NOTE
    assert "EGM2008" in COPERNICUS_SURFACE_MODEL_NOTE
    assert flags.search_region_id == "holuhraun"
    assert flags.permanent_shadow_fraction is None
    status = entry.window.evidence_status_by_field
    assert status["permanent_shadow_fraction"] == "Unavailable"
    assert status["terrain_relief"] == "Derived"
    assert status["terrain_texture"] == "Derived"
    assert entry.window.polygon.utm_zone == "27N"
    assert entry.status == "eligible"
    assert entry.reasons == []


@pytest.mark.parametrize("body", ["moon", "mars"])
def test_synthetic_planetary_window_has_null_earth_flags(body: str) -> None:
    entry = synthetic_entry(body)
    flags = entry.window.environment_flags
    assert flags.snow_or_freeze is None
    assert flags.moisture_state is None
    assert flags.surface_change_since_acquisition is None
    assert flags.surface_model_note is None


def test_synthetic_window_without_radar_marks_radar_unavailable_never_zero() -> None:
    entry = synthetic_entry("earth")
    assert entry.radar == []
    assert entry.window.evidence_status_by_field["radar_cpr"] == "Unavailable"
    assert entry.window.valid_fraction_by_observable["radar_cpr"] is None
    assert entry.terrain is not None
    assert entry.terrain.terrain is not None
    assert entry.window.valid_fraction_by_observable["terrain"] == (
        entry.terrain.terrain.valid_fraction
    )


def test_synthetic_mars_window_has_radar_unavailable_and_refuses_radar_entries() -> None:
    entry = synthetic_entry("mars")
    assert entry.window.evidence_status_by_field["radar_cpr"] == "Unavailable"
    radar_like = synthetic_terrain_record(window_id="mars-synthetic-w000", body="mars")
    with pytest.raises(ValueError, match="Mars"):
        synthetic_entry("mars", radar=[radar_like])


def test_synthetic_permanent_shadow_fraction_is_derived_when_supplied_on_the_moon() -> None:
    entry = synthetic_entry("moon", permanent_shadow_fraction=0.25)
    assert entry.window.environment_flags.permanent_shadow_fraction == 0.25
    assert entry.window.evidence_status_by_field["permanent_shadow_fraction"] == "Derived"
    with pytest.raises(ValueError, match="Moon"):
        synthetic_entry("earth", permanent_shadow_fraction=0.25)


def test_synthetic_status_and_reasons_come_from_the_caller() -> None:
    entry = synthetic_entry(status="rejected", reasons=["land_cover_below_min_fraction"])
    assert entry.status == "rejected"
    assert entry.reasons == ["land_cover_below_min_fraction"]
    with pytest.raises(ValueError):
        synthetic_entry(status="rejected", reasons=[])


def test_synthetic_window_refuses_mismatched_fingerprints() -> None:
    with pytest.raises(ValueError, match="window_id"):
        synthetic_entry(window_id="earth-synthetic-w001")
    with pytest.raises(ValueError, match="product"):
        synthetic_entry(product_ids=["synthetic-other"])
    with pytest.raises(ValueError, match="analysis_grid_m"):
        synthetic_entry(analysis_grid_m=180.0)


def test_synthetic_window_without_terrain_marks_terrain_unavailable() -> None:
    entry = synthetic_entry(terrain=None, status="unknown", reasons=["cache_missing"])
    status = entry.window.evidence_status_by_field
    assert status["terrain_relief"] == "Unavailable"
    assert status["terrain_texture"] == "Unavailable"
    assert entry.window.valid_fraction_by_observable["terrain"] is None


def test_module_exports_its_public_names() -> None:
    for name in ("terrain_record", "build_window_record", "input_hash", "lag_key"):
        assert name in fingerprints.__all__
