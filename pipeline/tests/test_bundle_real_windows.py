"""realcard.py helpers that need no raw data: ids, tile names, regions, blocking (T4R-04)."""

from __future__ import annotations

from pathlib import Path

import pytest

from terraluna_pipeline import realcard
from terraluna_pipeline.config import load_run_config

REPO = Path(__file__).resolve().parents[2]


def test_cop30_tile_names() -> None:
    assert realcard.cop30_tile_name(64.885, -16.72) == "Copernicus_DSM_COG_10_N64_00_W017_00_DEM"
    assert realcard.cop30_tile_name(34.9, -118.1) == "Copernicus_DSM_COG_10_N34_00_W119_00_DEM"
    assert realcard.cop30_tile_name(-77.5, 160.5) == "Copernicus_DSM_COG_10_S78_00_E160_00_DEM"


def test_worldcover_tile_names() -> None:
    assert realcard.worldcover_tile_name(64.885, -16.72) == (
        "ESA_WorldCover_10m_2021_v200_N63W018_Map"
    )
    assert realcard.worldcover_tile_name(34.9, -118.1) == (
        "ESA_WorldCover_10m_2021_v200_N33W120_Map"
    )


def test_point_in_polygon_synthetic_square() -> None:
    square = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
    assert realcard._inside(square, 0.5, 0.5)
    assert not realcard._inside(square, 1.5, 0.5)


def test_region_windows_lie_inside_and_include_the_g0_05_window() -> None:
    run = load_run_config(REPO / "config" / "run.yaml")
    region = realcard.earth_region(run, "27n")
    ids = realcard.region_window_ids(run, region)
    assert "earth-utm27n-e280-n2880" in ids
    assert ids == sorted(set(ids), key=ids.index)
    assert all(realcard.EARTH_ID.fullmatch(i) for i in ids)


def test_window_ids_are_parsed_strictly() -> None:
    assert realcard.MOON_ID.fullmatch("moon-sps-x2-y2")
    assert realcard.MOON_ID.fullmatch("moon-sps-x2") is None
    match = realcard.EARTH_ID.fullmatch("earth-utm27n-e280-n2880")
    assert match is not None and match["zone"] == "27n"


def test_missing_raw_dir_is_cache_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(realcard.RAW_DIR_ENV, raising=False)
    with pytest.raises(realcard.CacheMissingError, match="cache_missing"):
        realcard.raw_dir()


def test_build_exits_3_without_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(realcard.RAW_DIR_ENV, raising=False)
    argv = ["build", "--target", "moon-sps-x2-y2", "--earth", "earth-utm27n-e280-n2880"]
    assert realcard.main(["--root", str(REPO), *argv]) == 3


def test_build_blocks_while_shipping_mode_is_unset(tmp_path: Path) -> None:
    text = (REPO / "config" / "run.yaml").read_text(encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "run.yaml").write_text(
        text.replace("shipping_mode: C", "shipping_mode: null"), encoding="utf-8"
    )
    argv = ["build", "--target", "moon-sps-x2-y2", "--earth", "earth-utm27n-e280-n2880"]
    assert realcard.main(["--root", str(tmp_path), *argv]) == 3
    assert not (tmp_path / "outputs").exists()
