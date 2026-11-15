"""T6-01: PGDA78 clone ensemble. Synthetic base DEM and synthetic correlated clones only.

The generator below lives in this test file only (no clone is ever synthesized for a real run).
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from terraluna_pipeline import uncertainty
from terraluna_pipeline.config import TerrainConfig
from terraluna_pipeline.geometry import window_grid
from terraluna_pipeline.terrain import TerrainFingerprint, terrain_fingerprint

RTOL = 1e-10
ATOL = 1e-12

CONFIG = TerrainConfig(
    analysis_grid_m=90.0,
    lags_m=(270.0, 360.0, 540.0, 720.0, 1080.0),
    min_lags_for_H=4,
    min_lag_span_factor=4.0,
    detrend=("none", "plane"),
    min_valid_fraction=0.8,
    resampler="aeqd_sphere",
    window_m=5000.0,
    stride_m=2500.0,
)
GRID = window_grid(5000.0, 90.0)


def _lowpass(rng: np.random.Generator, n: int, scale: float) -> npt.NDArray[np.float64]:
    white = rng.standard_normal((n, n))
    k = np.fft.fftfreq(n)
    kk = np.hypot(*np.meshgrid(k, k))
    spectrum = np.fft.fft2(white) * np.exp(-((kk / 0.05) ** 2))
    field = np.real(np.fft.ifft2(spectrum))
    return np.asarray(scale * field / field.std(), dtype=np.float64)


def _base(n: int = GRID.size) -> npt.NDArray[np.float64]:
    rng = np.random.default_rng(7)
    y, x = np.mgrid[0:n, 0:n].astype(np.float64)
    return np.asarray(0.8 * x * 90.0 / 50.0 + _lowpass(rng, n, 120.0) + 0.3 * y, dtype=np.float64)


def _fingerprint(z: npt.NDArray[np.float64]) -> TerrainFingerprint:
    return terrain_fingerprint(
        z,
        valid=np.ones(z.shape, dtype=bool),
        grid=GRID,
        config=CONFIG,
        isotropic_rule="direction_mean",
        coarsening_factor=1.0,
    )


def _clones(count: int, seed: int = 11) -> list[tuple[str, TerrainFingerprint]]:
    rng = np.random.default_rng(seed)
    base = _base()
    return [
        (f"{i + 1:04d}", _fingerprint(base + _lowpass(rng, GRID.size, 2.0))) for i in range(count)
    ]


def test_identical_clones_give_zero_spread_and_base_median() -> None:
    base = _fingerprint(_base())
    clones = [(f"{i:04d}", _fingerprint(_base())) for i in range(1, 6)]
    result = uncertainty.clone_spread(base, clones, body="moon", n_requested=5)
    assert result["status"] == "available"
    for name, entry in result["descriptors"].items():
        assert entry["status"] == "available", name
        assert entry["max"] - entry["min"] == pytest.approx(0.0, rel=RTOL, abs=ATOL)
        assert entry["p97_5"] - entry["p2_5"] == pytest.approx(0.0, rel=RTOL, abs=ATOL)
        assert entry["median"] == pytest.approx(entry["base"], rel=RTOL, abs=ATOL)


def test_constant_offset_leaves_nu_unchanged() -> None:
    # Policy relied on: nu(L) is the RMS of elevation differences at lag L (PRD 6.3), so both the
    # relief ("none") and the texture ("plane") detrend policies cancel a constant offset.
    z = _base()
    base = _fingerprint(z)
    clones = [(f"{i:04d}", _fingerprint(z + offset)) for i, offset in enumerate((-5.0, 3.0, 40.0))]
    result = uncertainty.clone_spread(base, clones, body="moon", n_requested=3)
    for name, entry in result["descriptors"].items():
        if ".nu_m@" in name:
            assert entry["min"] == pytest.approx(entry["base"], rel=1e-9)
            assert entry["max"] == pytest.approx(entry["base"], rel=1e-9)


def test_counts_subset_and_clone_ids() -> None:
    base = _fingerprint(_base())
    clones = _clones(37)
    result = uncertainty.clone_spread(base, clones, body="moon", n_requested=100)
    assert result["n_requested"] == 100
    assert result["n_used"] == 37
    assert result["clone_ids"] == [cid for cid, _ in clones]
    assert "37 of 100" in result["count_statement"]


def test_zero_clones_is_unavailable_not_zero_spread() -> None:
    base = _fingerprint(_base())
    result = uncertainty.clone_spread(base, [], body="moon", n_requested=100)
    assert result["status"] == "unavailable"
    assert result["reason"] == "no_usable_clones"
    assert result["descriptors"] == {}


def test_h_unavailable_clone_is_counted_and_excluded() -> None:
    base = _fingerprint(_base())
    good = _clones(4)
    # A clone that is mostly invalid is excluded by T3, so H is unavailable for it.
    z = _base()
    valid = np.zeros(z.shape, dtype=bool)
    valid[:10, :10] = True
    bad = terrain_fingerprint(
        z,
        valid=valid,
        grid=GRID,
        config=CONFIG,
        isotropic_rule="direction_mean",
        coarsening_factor=1.0,
    )
    assert bad.variants["none"].hurst.H is None
    result = uncertainty.clone_spread(base, [*good, ("0099", bad)], body="moon", n_requested=5)
    h = result["descriptors"]["relief.H"]
    assert h["n"] == 4
    assert h["n_unavailable"] == 1
    assert sum(h["unavailable_reasons"].values()) == 1
    nu = result["descriptors"]["relief.nu_m@270"]
    assert nu["n"] == 4 and nu["n_unavailable"] == 1


def test_all_clones_h_unavailable_gives_unavailable_with_t3_reason() -> None:
    z = _base()
    valid = np.zeros(z.shape, dtype=bool)
    valid[:10, :10] = True
    bad = terrain_fingerprint(
        z,
        valid=valid,
        grid=GRID,
        config=CONFIG,
        isotropic_rule="direction_mean",
        coarsening_factor=1.0,
    )
    base = _fingerprint(z)
    result = uncertainty.clone_spread(
        base, [("0001", bad), ("0002", bad)], body="moon", n_requested=2
    )
    h = result["descriptors"]["relief.H"]
    assert h["status"] == "unavailable"
    assert h["median"] is None and h["min"] is None
    assert h["reason"]


def test_no_noise_or_seed_parameter_in_public_api() -> None:
    for name, member in inspect.getmembers(uncertainty, inspect.isfunction):
        if name.startswith("_") or member.__module__ != uncertainty.__name__:
            continue
        params = inspect.signature(member).parameters
        for param in params:
            lowered = param.lower()
            assert "noise" not in lowered and "seed" not in lowered and "rng" not in lowered, (
                name,
                param,
            )


def test_runs_are_bitwise_reproducible() -> None:
    base = _fingerprint(_base())
    clones = _clones(6)
    first = uncertainty.clone_spread(base, clones, body="moon", n_requested=6)
    second = uncertainty.clone_spread(base, clones, body="moon", n_requested=6)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_spread_fields_labels_and_limits_note() -> None:
    base = _fingerprint(_base())
    result = uncertainty.clone_spread(base, _clones(8), body="moon", n_requested=8)
    entry = result["descriptors"]["texture.nu_m@540"]
    assert set(entry) >= {"base", "median", "p2_5", "p97_5", "min", "max", "n"}
    assert entry["min"] <= entry["p2_5"] <= entry["median"] <= entry["p97_5"] <= entry["max"]
    assert result["percentile_estimator"] == uncertainty.PERCENTILE_ESTIMATOR
    assert result["labels"]["spread"] == "Derived"
    assert "topographic error model only" in result["note"]
    assert "radar calibration" in result["note"]


@pytest.mark.parametrize("body", ["earth", "mars"])
def test_earth_and_mars_have_no_clone_set(body: str) -> None:
    base = _fingerprint(_base())
    result = uncertainty.clone_spread(base, _clones(2), body=body, n_requested=2)
    assert result["status"] == "unavailable"
    assert result["reason"] == "no_published_clone_set"


# --- fetch-clones and run-clones wiring ------------------------------------------------------

PAGE = """<html><body>
<a href="https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site01/Site01_final_adj_5mpp_surf.tif">surf</a>
<a href="https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site01/Clones/Site01_final_adj_5mpp_0002_err.tif">2</a>
<a href="https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site01/Clones/Site01_final_adj_5mpp_0001_err.tif">1</a>
<a href="https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site04/Clones/Site04_final_adj_5mpp_0001_err.tif">x</a>
</body></html>"""


def test_parse_clone_links_site01_only_sorted() -> None:
    links = uncertainty.parse_clone_links(PAGE, site="Site01")
    assert [link.rsplit("/", 1)[1] for link in links] == [
        "Site01_final_adj_5mpp_0001_err.tif",
        "Site01_final_adj_5mpp_0002_err.tif",
    ]


def test_fetch_clones_downloads_with_sha256(tmp_path: Path) -> None:
    root = "https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/Site01/Clones/Site01_final_adj_5mpp_"
    blobs = {
        "https://pgda.example/products/78": PAGE.encode(),
        root + "0001_err.tif": b"one",
        root + "0002_err.tif": b"two",
    }
    records = uncertainty.fetch_clones(
        page_url="https://pgda.example/products/78",
        cache_dir=tmp_path / "clones",
        opener=blobs.__getitem__,
    )
    assert [r["file"] for r in records] == [
        "Site01_final_adj_5mpp_0001_err.tif",
        "Site01_final_adj_5mpp_0002_err.tif",
    ]
    assert (tmp_path / "clones" / "Site01_final_adj_5mpp_0001_err.tif").read_bytes() == b"one"
    assert (
        records[0]["sha256"] == "7692c3ad3540bb803c020b3aee66cd8887123234ea0c6e7143c0add73ff431ed"
    )


def test_run_clones_exits_3_when_cache_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    code = uncertainty.main(
        [
            "run-clones",
            "--pgda-dir",
            str(tmp_path / "nope"),
            "--windows",
            str(tmp_path / "windows.json"),
            "--out-dir",
            str(out),
        ]
    )
    assert code == 3
    assert "blocked" in capsys.readouterr().out
    assert not out.exists()


def test_clone_id_from_filename() -> None:
    assert uncertainty.clone_id("Site01_final_adj_5mpp_0042_err.tif") == "0042"
    with pytest.raises(ValueError):
        uncertainty.clone_id("Site01_final_adj_5mpp_surf.tif")
