"""Tests for the card figures A to C (PRD 10, 8.4; story T4R-02).

Every card, comparison, window entry and DEM array here is SYNTHETIC test input built in this
file under pytest's tmp_path. None of it is an observation or a result, and none of it leaves
the test.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from terraluna_pipeline.figures import (
    COMMAND,
    hillshade,
    main,
    nice_scale_bar_m,
    png_bytes,
    render_card,
)
from terraluna_pipeline.schema import CardRecord, ComparisonRecord, WindowEntry
from terraluna_pipeline.svg import svg_allowlist_errors

HEX64 = "0123456789abcdef" * 4
RUN_ID = "sha256:" + HEX64
TARGET_ID = "moon-synthetic-target"
TARGET_WINDOW = "moon-synthetic-0001"
EARTH_WINDOW = "earth-synthetic-0002"
CARD_ID = f"{TARGET_WINDOW}__{EARTH_WINDOW}"

# Synthetic distance values chosen so they cannot coincide with an axis tick label.
RELIEF_DISTANCE = 0.371937
TEXTURE_DISTANCE = 0.289113
RADAR_DISTANCE = 0.0461729


# --- synthetic records ---------------------------------------------------------------------------


def closed(ring: list[list[float]]) -> list[list[float]]:
    return [*ring, ring[0]]


def polygon(body: str) -> dict[str, Any]:
    if body == "earth":
        return {
            "frame": "utm",
            "crs": "EPSG:32627",
            "epsg": 32627,
            "utm_zone": "27N",
            "ring_m": closed([[0.0, 0.0], [5000.0, 0.0], [5000.0, 5000.0], [0.0, 5000.0]]),
            "ring_lonlat": closed([[-16.75, 64.875], [-16.625, 64.875], [-16.625, 64.9]]),
        }
    return {
        "frame": "aeqd_sphere",
        "crs": "+proj=aeqd +lat_0=-89.375 +lon_0=11.375 +R=1737400 +units=m",
        "epsg": None,
        "utm_zone": None,
        "ring_m": closed([[-2500.0, -2500.0], [2500.0, -2500.0], [2500.0, 2500.0]]),
        "ring_lonlat": closed([[10.25, -89.5], [12.5, -89.5], [12.5, -89.25]]),
    }


def distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "values": ordered,
        "min": ordered[0],
        "median": ordered[len(ordered) // 2],
        "p05": ordered[0],
        "p95": ordered[-1],
        "max": ordered[-1],
    }


def available(value: float, *, spread: bool = True) -> dict[str, Any]:
    return {
        "value": value,
        "unit": "RMS of log nu",
        "evidence_label": "Derived",
        "status": "available",
        "reason": None,
        "reference_spread": {"min": 0.11, "median": 0.23, "max": 0.47, "n": 12} if spread else None,
        "reference_spread_reason": None if spread else "spread_unavailable",
        "background_percentile": None,
        "background_percentile_reason": "background_sample_size_unset",
        "tied": False if spread else None,
        "tie_group": None,
        "tie_reason": None,
    }


def unavailable(reason: str, unit: str = "CPR Wasserstein-1") -> dict[str, Any]:
    return {
        "value": None,
        "unit": unit,
        "evidence_label": "Unavailable",
        "status": "unavailable",
        "reason": reason,
        "reference_spread": None,
        "reference_spread_reason": "spread_unavailable",
        "background_percentile": None,
        "background_percentile_reason": "background_sample_size_unset",
        "tied": None,
        "tie_group": None,
        "tie_reason": "spread_unavailable",
    }


def distances(mode: str, *, spread: bool = True) -> dict[str, Any]:
    radar = (
        {**available(RADAR_DISTANCE, spread=False), "unit": "CPR Wasserstein-1"}
        if mode == "A"
        else unavailable("radar_comparison_not_completed")
    )
    return {
        "terrain_relief": available(RELIEF_DISTANCE, spread=spread),
        "terrain_texture": available(TEXTURE_DISTANCE, spread=spread),
        "radar_cpr": radar,
        "model_embedding": unavailable("g5_not_pass", unit="cosine distance"),
    }


def component(*, envelope: bool) -> dict[str, Any]:
    if not envelope:
        return {
            "status": "unavailable",
            "evidence_label": "Unavailable",
            "reason": "clones_not_run",
            "note": None,
            "by_family": None,
            "n_requested": None,
            "n_used": None,
            "scenarios": None,
            "rank_frequency": None,
        }
    spread = {"spread_min": 0.0611, "spread_median": 0.0733, "spread_max": 0.0912, "n": 100}
    return {
        "status": "available",
        "evidence_label": "Derived",
        "reason": None,
        "note": "Synthetic clone spread",
        "by_family": {"terrain_relief": spread, "terrain_texture": spread},
        "n_requested": 100,
        "n_used": 100,
        "scenarios": None,
        "rank_frequency": None,
    }


def uncertainty(*, envelope: bool) -> dict[str, Any]:
    off = component(envelope=False)
    return {
        "heterogeneity": off,
        "measurement_error": component(envelope=envelope),
        "statistical_prediction_error": off,
        "scenario_sensitivity": off,
    }


def comparison(mode: str, *, envelope: bool = True, spread: bool = True) -> dict[str, Any]:
    return {
        "target_window_id": TARGET_WINDOW,
        "earth_window_id": EARTH_WINDOW,
        "shipping_mode": mode,
        "comparable_observables": ["terrain_relief", "terrain_texture"],
        "excluded_observables_and_reasons": [],
        "distances_by_family": distances(mode, spread=spread),
        "uncertainty_components": uncertainty(envelope=envelope),
        "sort_rule": "synthetic",
        "claim_limits": ["Synthetic test comparison"],
        "source_ids": ["synthetic-dem"],
        "run_id": RUN_ID,
    }


def card(mode: str, body: str = "moon", **comparison_options: bool) -> dict[str, Any]:
    by_family = distances(mode, spread=comparison_options.get("spread", True))
    stability = {
        "uncertainty_components": uncertainty(envelope=comparison_options.get("envelope", True)),
        "tied_by_family": {family: entry["tied"] for family, entry in by_family.items()},
        "tie_group_by_family": {family: entry["tie_group"] for family, entry in by_family.items()},
    }
    unknown = {"value": "unknown", "source": None}
    target_window = TARGET_WINDOW.replace("moon", body)
    target_id = TARGET_ID.replace("moon", body)
    return {
        "card_id": f"{target_window}__{EARTH_WINDOW}",
        "mock": False,
        "run_id": RUN_ID,
        "shipping_mode": mode,
        "title": "Synthetic test card",
        "wording": "candidate window identified by this search",
        "target": {
            "target_id": target_id,
            "target_window_id": target_window,
            "body": body,
            "polygon": polygon(body),
            "product_ids": ["synthetic-dem"],
        },
        "earth": {
            "earth_window_id": EARTH_WINDOW,
            "polygon": polygon("earth"),
            "product_ids": ["synthetic-glo30"],
            "search_region_id": None,
        },
        "comparison_ref": {
            "path": f"comparisons/{target_id}.json",
            "target_window_id": target_window,
            "earth_window_id": EARTH_WINDOW,
        },
        "q1_compared": "synthetic",
        "q2_interpretation": {"text": "synthetic", "evidence_label": "Assumed"},
        "q3_measurements": [],
        "q4_differences": {"limits": [], "differing": [], "ambiguous": [], "not_observed": []},
        "q5_stability": stability,
        "q6_campaign": dict.fromkeys(
            ("access", "permits", "season", "land_status", "logistics"), unknown
        ),
        "q7_field_experiment": {
            "text": "synthetic",
            "evidence_label": "Assumed",
            "status": "proposed",
        },
        "distances_by_family": by_family,
        "radar_context": [],
        "figures": [],
        "g5_status": "NOT_RUN",
        "claim_limits": ["Synthetic test card"],
    }


def variant(detrend: str, scale: float) -> dict[str, Any]:
    nu = {"90": 1.0 * scale, "180": 1.6 * scale, "360": 2.5 * scale, "720": None}
    return {
        "detrend": detrend,
        "supported_lags_m": [90.0, 180.0, 360.0],
        "unsupported_lags": [{"lag_m": 720.0, "reason": "below_min_valid_fraction"}],
        "nu_m": nu,
        "nu_by_direction_m": {},
        "hurst": 0.6,
        "hurst_reason": None,
        "tilt": [0.0, 0.0, 0.0] if detrend == "plane" else None,
    }


def radar(window_id: str, body: str, band: str, values: list[float]) -> dict[str, Any]:
    return {
        "window_id": window_id,
        "body": body,
        "kind": "radar",
        "product_ids": [f"synthetic-{band.lower()}-radar"],
        "evidence_label": "Observed",
        "input_hash": HEX64,
        "run_id": RUN_ID,
        "terrain": None,
        "radar": {
            "product_id": f"synthetic-{band.lower()}-radar",
            "band": band,
            "frequency_or_wavelength": "synthetic",
            "convention": None,
            "support_m": 250.0,
            "aggregation": "ratio_of_mean_powers",
            "blocks_independent": False,
            "n_blocks": len(values),
            "n_valid": len(values),
            "valid_fraction": 1.0,
            "cpr_ratio_of_mean_powers": distribution(values),
            "cpr_mean_of_pixelwise_ratios": None,
            "sc": None,
            "oc": None,
            "total_power": None,
            "incidence_deg": None,
            "handedness_ratio": None,
            "reason": None,
        },
    }


def window_entry(window_id: str, body: str, scale: float, band: str) -> dict[str, Any]:
    values = [0.2 * scale + 0.05 * i for i in range(8)]
    return {
        "window": {
            "window_id": window_id,
            "body": body,
            "polygon": polygon(body),
            "area_m2": 25000000.0,
            "analysis_grid_m": 90.0,
            "product_ids": ["synthetic-dem"],
            "valid_fraction_by_observable": {"terrain": 1.0},
            "observed_interpolated_quality": {
                "observed_fraction": None,
                "interpolated_fraction": None,
                "source": None,
                "reason": "not_present",
            },
            "environment_flags": {
                "search_region_id": None,
                "scene_ids": [],
                "acquisition_dates": [],
                "snow_or_freeze": None,
                "moisture_state": None,
                "surface_change_since_acquisition": None,
                "permanent_shadow_fraction": None,
                "surface_model_note": None,
            },
            "evidence_status_by_field": {},
        },
        "terrain": {
            "window_id": window_id,
            "body": body,
            "kind": "terrain",
            "product_ids": ["synthetic-dem"],
            "evidence_label": "Derived",
            "input_hash": HEX64,
            "run_id": RUN_ID,
            "terrain": {
                "analysis_grid_m": 90.0,
                "coarsening_factor": None,
                "resampler": "aeqd_sphere",
                "valid_fraction": 1.0,
                "relief": variant("none", scale),
                "texture": variant("plane", scale * 0.5),
            },
            "radar": None,
        },
        "radar": [radar(window_id, body, band, values)],
        "status": "eligible",
        "reasons": [],
        "grid_index": [1, 2],
        "card_id": None,
    }


def synthetic_dem(seed: int, shape: tuple[int, int] = (40, 50)) -> NDArray[np.float64]:
    rng = np.random.default_rng(seed)
    rows, cols = np.mgrid[0 : shape[0], 0 : shape[1]]
    surface: NDArray[np.float64] = (
        30.0 * np.sin(rows / 6.0) + 20.0 * np.cos(cols / 9.0) + rng.normal(0, 1, shape)
    )
    surface[0:3, 0:4] = np.nan
    return surface


def write_inputs(
    root: Path, mode: str, body: str = "moon", earth_band: str = "S", **options: bool
) -> CardRecord:
    """Write one synthetic card, its comparison, both windows files and both DEM caches."""
    record = card(mode, body, **options)
    target_window = record["target"]["target_window_id"]
    target_id = record["target"]["target_id"]
    record_cmp = comparison(mode, **options)
    record_cmp["target_window_id"] = target_window
    for sub, name, data in (
        ("outputs/cards", f"{record['card_id']}.json", record),
        ("outputs/comparisons", f"{target_id}.json", [record_cmp]),
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
        (root / sub / name).write_text(json.dumps(data))
    windows = root / "data/processed/windows"
    windows.mkdir(parents=True, exist_ok=True)
    target_entries = [window_entry(target_window, body, 1.0, "S")]
    earth_entries = [window_entry(EARTH_WINDOW, "earth", 1.4, earth_band)]
    if body == "earth":
        target_entries = target_entries + earth_entries
    (windows / f"{body}.json").write_text(json.dumps(target_entries))
    if body != "earth":
        (windows / "earth.json").write_text(json.dumps(earth_entries))
    dems = root / "data/processed/dem_windows"
    dems.mkdir(parents=True, exist_ok=True)
    products = {"moon": "synthetic-pgda78", "mars": "synthetic-ctx", "earth": "synthetic-glo30"}
    for window_id, seed, spacing, product in (
        (target_window, 1, 100.0, products[body]),
        (EARTH_WINDOW, 2, 100.0, products["earth"]),
    ):
        np.savez(
            dems / f"{window_id}.npz",
            elevation_m=synthetic_dem(seed),
            spacing_m=np.array(spacing),
            product_id=np.array(product),
        )
    return CardRecord.model_validate_json(json.dumps(record))


def figures_dir(root: Path) -> Path:
    return root / "data/processed/figures"


def render(root: Path, letter: str) -> str:
    assert main(["cards", "--root", str(root)]) == 0
    return (figures_dir(root) / f"{CARD_ID}_{letter}.svg").read_text()


# --- the builders are valid records --------------------------------------------------------------


def test_synthetic_builders_are_valid_records() -> None:
    CardRecord.model_validate_json(json.dumps(card("C")))
    ComparisonRecord.model_validate_json(json.dumps(comparison("A")))
    WindowEntry.model_validate_json(json.dumps(window_entry(TARGET_WINDOW, "moon", 1.0, "S")))


# --- hillshade and PNG ---------------------------------------------------------------------------


def test_hillshade_of_flat_ground_is_sin_altitude() -> None:
    shade = hillshade(np.zeros((5, 6)), 30.0, azimuth_deg=315.0, altitude_deg=45.0)
    np.testing.assert_allclose(shade, math.sin(math.radians(45.0)), atol=1e-12)


def test_hillshade_lights_the_slope_facing_the_sun() -> None:
    cols = np.tile(np.arange(10.0), (10, 1)) * 10.0
    rising_east = cols  # faces west, toward a 270 deg sun
    west = hillshade(rising_east, 10.0, azimuth_deg=270.0, altitude_deg=30.0)
    east = hillshade(-rising_east, 10.0, azimuth_deg=270.0, altitude_deg=30.0)
    assert float(np.mean(west)) > float(np.mean(east))
    rows = np.tile(np.arange(10.0)[:, None], (1, 10)) * 10.0  # row 0 north: rises southward
    north_sun = hillshade(rows, 10.0, azimuth_deg=0.0, altitude_deg=30.0)
    south_sun = hillshade(rows, 10.0, azimuth_deg=180.0, altitude_deg=30.0)
    assert float(np.mean(north_sun)) > float(np.mean(south_sun))


def test_hillshade_keeps_missing_cells_missing() -> None:
    elevation = np.zeros((6, 6))
    elevation[2, 2] = np.nan
    shade = hillshade(elevation, 30.0)
    assert np.isnan(shade[2, 2])
    assert np.isfinite(shade[5, 5])


def test_png_is_deterministic_and_has_no_metadata_chunks() -> None:
    shade = hillshade(synthetic_dem(3), 30.0)
    data = png_bytes(shade)
    assert data == png_bytes(shade)
    assert data.startswith(b"\x89PNG")
    for chunk in (b"tIME", b"tEXt", b"iTXt", b"zTXt"):
        assert chunk not in data


@pytest.mark.parametrize(
    ("width_m", "expected"),
    [(5000.0, 1000.0), (4000.0, 1000.0), (1500.0, 200.0), (25000.0, 5000.0), (90.0, 20.0)],
)
def test_scale_bar_is_a_nice_length_from_the_window_metres(width_m: float, expected: float) -> None:
    assert nice_scale_bar_m(width_m) == expected


# --- figures -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "label"), [("moon", "Lunar"), ("mars", "Mars"), ("earth", "Earth")]
)
def test_figure_a_for_each_body(tmp_path: Path, body: str, label: str) -> None:
    record = write_inputs(tmp_path, "C", body)
    assert main(["cards", "--root", str(tmp_path)]) == 0
    svg = (figures_dir(tmp_path) / f"{record.card_id}_A.svg").read_text()
    assert f"{label} target window" in svg
    assert "Earth candidate window" in svg
    assert "data:image/png;base64," in svg
    assert "azimuth 315" in svg and "altitude 45" in svg
    # 50 cells of 100 m make a 5 km window, so the scale bar is 1 km.
    assert svg.count(">1 km<") == 2
    assert "synthetic-glo30" in svg


def test_figure_a_prints_non_default_hillshade_arguments(tmp_path: Path) -> None:
    write_inputs(tmp_path, "C")
    assert main(["cards", "--root", str(tmp_path), "--azimuth", "300", "--altitude", "30"]) == 0
    svg = (figures_dir(tmp_path) / f"{CARD_ID}_A.svg").read_text()
    assert "azimuth 300" in svg and "altitude 30" in svg
    index = json.loads((tmp_path / "outputs/figures/index.json").read_text())
    assert index[0]["command"] == f"{COMMAND} --azimuth 300 --altitude 30"


def test_figure_b_with_envelope_and_reference_spread(tmp_path: Path) -> None:
    write_inputs(tmp_path, "C")
    svg = render(tmp_path, "B")
    assert "nu(L)" in svg and "Lag L (m)" in svg
    assert format(RELIEF_DISTANCE, ".6g") in svg
    assert format(TEXTURE_DISTANCE, ".6g") in svg
    assert "Reference spread" in svg and "Clone envelope" in svg
    assert "0.47" in svg and "0.0912" in svg
    assert "Unavailable" not in svg


def test_figure_b_without_envelope_or_spread_shows_reasons(tmp_path: Path) -> None:
    write_inputs(tmp_path, "C", envelope=False, spread=False)
    svg = render(tmp_path, "B")
    assert "Unavailable: spread_unavailable" in svg
    assert "Unavailable: clones_not_run" in svg
    assert format(RELIEF_DISTANCE, ".6g") in svg


def test_figure_b_shows_the_reason_for_an_unavailable_terrain_distance(tmp_path: Path) -> None:
    write_inputs(tmp_path, "C")
    path = tmp_path / "outputs/comparisons" / f"{TARGET_ID}.json"
    records = json.loads(path.read_text())
    records[0]["distances_by_family"]["terrain_texture"] = unavailable(
        "texture_not_supported", unit="RMS of log nu"
    )
    path.write_text(json.dumps(records))
    svg = render(tmp_path, "B")
    assert "Unavailable: texture_not_supported" in svg
    assert format(TEXTURE_DISTANCE, ".6g") not in svg


def test_figure_c_mode_a_overlays_cdfs_with_the_wasserstein_value(tmp_path: Path) -> None:
    write_inputs(tmp_path, "A")
    svg = render(tmp_path, "C")
    assert "Wasserstein" in svg
    assert format(RADAR_DISTANCE, ".6g") in svg
    assert svg.count('class="cdf"') == 2


def test_figure_c_mode_b_has_band_labels_and_no_distance(tmp_path: Path) -> None:
    write_inputs(tmp_path, "B", earth_band="L")
    svg = render(tmp_path, "C")
    assert "S-band" in svg and "L-band" in svg
    lowered = svg.lower()
    assert "distance" not in lowered
    assert "wasserstein" not in lowered
    # Every value printed on the figure, whatever its element; coordinates are not values.
    printed = " ".join(ET.fromstring(svg.encode()).itertext())
    assert "S-band" in printed
    for value in (RELIEF_DISTANCE, TEXTURE_DISTANCE, RADAR_DISTANCE, 0.11, 0.23, 0.47, 0.0912):
        for text in (format(value, ".6g"), repr(value), f"{value:.2f}"):
            assert text not in printed, text


def test_figure_c_mode_c_is_an_unavailable_panel_with_the_reason(tmp_path: Path) -> None:
    write_inputs(tmp_path, "C")
    svg = render(tmp_path, "C")
    assert "Unavailable: radar_comparison_not_completed" in svg
    assert 'class="cdf"' not in svg


@pytest.mark.parametrize("mode", ["A", "B", "C"])
def test_every_figure_passes_the_allowlist_and_is_accessible(tmp_path: Path, mode: str) -> None:
    write_inputs(tmp_path, mode)
    assert main(["cards", "--root", str(tmp_path)]) == 0
    for letter in "ABC":
        data = (figures_dir(tmp_path) / f"{CARD_ID}_{letter}.svg").read_bytes()
        assert svg_allowlist_errors(data) == []
        assert b"<title" in data and b"<desc" in data
        assert b"http://" not in data.replace(b"http://www.w3.org/2000/svg", b"")
        assert b"https://" not in data


# --- determinism ---------------------------------------------------------------------------------


def test_rendering_twice_gives_identical_bytes(tmp_path: Path) -> None:
    record = write_inputs(tmp_path, "A")
    first = render_card(tmp_path, record)
    second = render_card(tmp_path, record)
    assert first == second
    assert set(first) == {"A", "B", "C"}


def test_a_fresh_process_renders_the_same_bytes(tmp_path: Path) -> None:
    record = write_inputs(tmp_path, "A")
    in_process = render_card(tmp_path, record)
    subprocess.run(
        [sys.executable, "-m", "terraluna_pipeline.figures", "cards", "--root", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    for letter, data in in_process.items():
        assert (figures_dir(tmp_path) / f"{CARD_ID}_{letter}.svg").read_bytes() == data


def test_index_lists_each_figure_with_hash_bytes_and_command(tmp_path: Path) -> None:
    write_inputs(tmp_path, "C")
    assert main(["cards", "--root", str(tmp_path)]) == 0
    raw = (tmp_path / "outputs/figures/index.json").read_bytes()
    index = json.loads(raw)
    assert [entry["figure"] for entry in index] == ["A", "B", "C"]
    for entry in index:
        assert set(entry) == {"card_id", "figure", "path", "sha256", "bytes", "command"}
        assert entry["card_id"] == CARD_ID
        assert entry["path"] == f"figures/{CARD_ID}_{entry['figure']}.svg"
        data = (figures_dir(tmp_path) / f"{CARD_ID}_{entry['figure']}.svg").read_bytes()
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert entry["bytes"] == len(data)
        assert entry["command"] == COMMAND
    assert raw.endswith(b"\n") and b"utc" not in raw.lower()


# --- --check -------------------------------------------------------------------------------------


def test_check_passes_after_cards_and_writes_nothing(tmp_path: Path) -> None:
    write_inputs(tmp_path, "C")
    assert main(["cards", "--root", str(tmp_path)]) == 0
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert main(["cards", "--check", "--root", str(tmp_path)]) == 0
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after


def test_check_detects_an_edited_figure_and_an_edited_index_hash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_inputs(tmp_path, "C")
    assert main(["cards", "--root", str(tmp_path)]) == 0
    figure = figures_dir(tmp_path) / f"{CARD_ID}_A.svg"
    data = bytearray(figure.read_bytes())
    data[-2] = ord("X")
    figure.write_bytes(bytes(data))
    index_path = tmp_path / "outputs/figures/index.json"
    index = json.loads(index_path.read_text())
    index[1]["sha256"] = "0" * 64
    index_path.write_text(json.dumps(index))
    capsys.readouterr()
    assert main(["cards", "--check", "--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert f"figures/{CARD_ID}_A.svg" in out
    assert f"figures/{CARD_ID}_B.svg" in out
    assert f"figures/{CARD_ID}_C.svg" not in out


def test_check_detects_a_changed_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_inputs(tmp_path, "C")
    assert main(["cards", "--root", str(tmp_path)]) == 0
    path = tmp_path / "data/processed/windows/earth.json"
    entries = json.loads(path.read_text())
    entries[0]["terrain"]["terrain"]["relief"]["nu_m"]["90"] = 9.0
    path.write_text(json.dumps(entries))
    capsys.readouterr()
    assert main(["cards", "--check", "--root", str(tmp_path)]) == 1
    assert f"figures/{CARD_ID}_B.svg" in capsys.readouterr().out


@pytest.mark.parametrize("check", [False, True])
def test_a_missing_dem_cache_blocks_with_its_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], check: bool
) -> None:
    write_inputs(tmp_path, "C")
    if check:
        assert main(["cards", "--root", str(tmp_path)]) == 0
    missing = tmp_path / "data/processed/dem_windows" / f"{EARTH_WINDOW}.npz"
    missing.unlink()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    capsys.readouterr()
    argv = ["cards", "--root", str(tmp_path)] + (["--check"] if check else [])
    assert main(argv) == 3
    out = capsys.readouterr().out
    assert "blocked:" in out and f"dem_windows/{EARTH_WINDOW}.npz" in out
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_no_cards_blocks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["cards", "--root", str(tmp_path)]) == 3
    assert "blocked:" in capsys.readouterr().out


def test_a_mock_card_is_labelled_mock(tmp_path: Path) -> None:
    write_inputs(tmp_path, "C")
    path = tmp_path / "outputs/cards" / f"{CARD_ID}.json"
    record = copy.deepcopy(json.loads(path.read_text()))
    record["mock"] = True
    path.write_text(json.dumps(record))
    for letter in "ABC":
        assert "MOCK" in render(tmp_path, letter)
