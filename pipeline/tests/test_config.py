"""Tests for the run-config loader (PRD 8.13; terrain block for T3, PRD 6.3 and 7).

config/run.yaml is the only source of the terrain settings. The tests read that file, both
through the loader and directly with PyYAML, and check the loader returns exactly what is
there. The committed values are also pinned once, so a change to the file is noticed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from terraluna_pipeline.config import (
    DEFAULT_RUN_CONFIG,
    DETREND_POLICIES,
    RESAMPLERS,
    ConfigError,
    DuplicateConfigKeyError,
    InvalidConfigValueError,
    MissingConfigKeyError,
    MissingRunConfigError,
    NonPositiveValueError,
    RunConfig,
    TerrainConfig,
    UnknownConfigKeyError,
    UnknownDetrendError,
    UnknownResamplerError,
    load_run_config,
    load_terrain_config,
)

REPO = Path(__file__).resolve().parents[2]
RUN_YAML = REPO / "config" / "run.yaml"

# The loaded field, and where it lives in run.yaml.
TERRAIN_KEYS: dict[str, tuple[str, str]] = {
    "analysis_grid_m": ("terrain", "analysis_grid_m"),
    "lags_m": ("terrain", "lags_m"),
    "min_lags_for_H": ("terrain", "min_lags_for_H"),
    "min_lag_span_factor": ("terrain", "min_lag_span_factor"),
    "detrend": ("terrain", "detrend"),
    "min_valid_fraction": ("terrain", "min_valid_fraction"),
    "resampler": ("terrain", "resampler"),
    "window_m": ("targets", "window_m"),
    "stride_m": ("targets", "stride_m"),
}


def raw_run_yaml() -> dict[str, Any]:
    document = yaml.safe_load(RUN_YAML.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def write_config(tmp_path: Path, change: Callable[[dict[str, Any]], None]) -> Path:
    """Copy the committed run.yaml into tmp_path with one change applied."""
    document = raw_run_yaml()
    change(document)
    path = tmp_path / "run.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def set_key(block: str, key: str, value: object) -> Callable[[dict[str, Any]], None]:
    def change(document: dict[str, Any]) -> None:
        document[block][key] = value

    return change


def drop_key(block: str, key: str) -> Callable[[dict[str, Any]], None]:
    def change(document: dict[str, Any]) -> None:
        del document[block][key]

    return change


def null_paths(node: object, prefix: str = "") -> Iterator[str]:
    """Dotted paths of every null leaf; list items are addressed by their index."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from null_paths(value, f"{prefix}{key}.")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from null_paths(value, f"{prefix}{index}.")
    elif node is None:
        yield prefix.removesuffix(".")


# --- The committed file -----------------------------------------------------------------------


def test_default_path_is_the_repository_run_yaml() -> None:
    assert DEFAULT_RUN_CONFIG == RUN_YAML


def test_loader_returns_exactly_what_run_yaml_holds() -> None:
    terrain = load_terrain_config()
    document = raw_run_yaml()
    for field, (block, key) in TERRAIN_KEYS.items():
        loaded = getattr(terrain, field)
        expected = document[block][key]
        if isinstance(expected, list):
            assert list(loaded) == expected, field
        else:
            assert loaded == expected, field


def test_committed_terrain_values_are_pinned() -> None:
    # The values committed in config/run.yaml (PRD 8.13) at the time T3 was written.
    terrain = load_terrain_config(RUN_YAML)
    assert terrain.analysis_grid_m == 90
    assert terrain.lags_m == (270.0, 360.0, 540.0, 720.0, 1080.0)
    assert terrain.min_lags_for_H == 4
    assert terrain.min_lag_span_factor == 4
    assert terrain.detrend == ("none", "plane")
    assert terrain.min_valid_fraction == 0.8
    assert terrain.resampler == "aeqd_sphere"
    assert terrain.window_m == 5000
    assert terrain.stride_m == 2500


def test_loaded_types() -> None:
    terrain = load_terrain_config()
    for field in ("analysis_grid_m", "min_lag_span_factor", "min_valid_fraction"):
        assert type(getattr(terrain, field)) is float, field
    for field in ("window_m", "stride_m"):
        assert type(getattr(terrain, field)) is float, field
    assert all(type(lag) is float for lag in terrain.lags_m)
    assert type(terrain.min_lags_for_H) is int
    assert all(type(policy) is str for policy in terrain.detrend)


def test_detrend_none_is_the_string_none_not_null() -> None:
    # YAML 1.1 has no `none` null literal, so `none` must arrive as a policy name.
    assert raw_run_yaml()["terrain"]["detrend"] == ["none", "plane"]
    assert load_terrain_config().detrend[0] == "none"


def test_known_values_are_closed_sets() -> None:
    assert RESAMPLERS == ("aeqd_sphere",)
    assert DETREND_POLICIES == ("none", "plane")


def test_terrain_config_is_frozen() -> None:
    terrain = load_terrain_config()
    with pytest.raises(AttributeError):
        terrain.analysis_grid_m = 30.0  # type: ignore[misc]


def test_loading_does_not_modify_the_file() -> None:
    before = RUN_YAML.read_bytes()
    load_run_config()
    assert RUN_YAML.read_bytes() == before


def test_loader_reads_the_file_not_a_built_in_copy(tmp_path: Path) -> None:
    path = write_config(tmp_path, set_key("terrain", "lags_m", [300, 600, 1200, 2400]))
    assert load_terrain_config(path).lags_m == (300.0, 600.0, 1200.0, 2400.0)
    path = write_config(tmp_path, set_key("targets", "window_m", 4000))
    assert load_terrain_config(path).window_m == 4000


def test_run_config_carries_the_terrain_block_and_path() -> None:
    run = load_run_config()
    assert isinstance(run, RunConfig)
    assert isinstance(run.terrain, TerrainConfig)
    assert run.terrain == load_terrain_config()
    assert run.path == RUN_YAML


# --- Kickoff entries stay null ----------------------------------------------------------------


def test_document_is_the_parsed_yaml_with_nothing_filled_in() -> None:
    assert load_run_config().document == raw_run_yaml()


@pytest.mark.parametrize(
    "key",
    [
        "radar.snr_rule",
        "radar.incidence_bins_deg",
        "radar.convention.moon",
        "targets.moon.primary_window_id",
        "targets.mars.0.primary_window_id",
        "targets.mars.1.bbox",
        "run.shipping_mode",
        "run.effective_cutoff",
        "web.bundle_version",
        "uncertainty.background_sample_size",
    ],
)
def test_requested_kickoff_entry_stays_none(key: str, tmp_path: Path) -> None:
    # Kickoff entries are filled by people at kickoff and G0 (PRD 13, 23.6), so this checks the
    # loader on a copy of run.yaml where the entry is null, not the live file's current state.
    def null_out(document: dict[str, Any]) -> None:
        *parents, leaf = key.split(".")
        node: Any = document
        for part in parents:
            node = node[int(part)] if isinstance(node, list) else node[part]
        node[int(leaf) if isinstance(node, list) else leaf] = None

    path = write_config(tmp_path, null_out)
    run = load_run_config(path)
    assert key in set(null_paths(run.document))
    assert run.value(key) is None


def test_every_null_entry_stays_none() -> None:
    run = load_run_config()
    paths = list(null_paths(raw_run_yaml()))
    assert paths, "run.yaml has no null entries to check"
    for key in paths:
        assert run.value(key) is None, key


def test_value_returns_set_entries_unchanged() -> None:
    run = load_run_config()
    assert run.value("targets.moon.product") == "PGDA78/Site01"
    assert run.value("targets.mars.0.bbox") == [76.69, 17.41, 78.69, 19.41]
    assert run.value("terrain.lags_m") == [270, 360, 540, 720, 1080]


def test_value_cannot_mutate_the_document() -> None:
    run = load_run_config()
    lags = run.value("terrain.lags_m")
    assert isinstance(lags, list)
    lags.append(9999)
    assert run.value("terrain.lags_m") == [270, 360, 540, 720, 1080]


@pytest.mark.parametrize(
    "key",
    [
        "radar.no_such_key",
        "nope",
        "targets.mars.7.name",
        "targets.mars.name",
        "run.shipping_mode.x",
    ],
)
def test_value_of_a_missing_key_raises(key: str) -> None:
    with pytest.raises(MissingConfigKeyError) as caught:
        load_run_config().value(key)
    assert caught.value.key == key


# --- Rejections -------------------------------------------------------------------------------


@pytest.mark.parametrize("field", list(TERRAIN_KEYS))
def test_missing_key_raises_a_named_error(tmp_path: Path, field: str) -> None:
    block, key = TERRAIN_KEYS[field]
    path = write_config(tmp_path, drop_key(block, key))
    with pytest.raises(MissingConfigKeyError) as caught:
        load_terrain_config(path)
    assert caught.value.key == f"{block}.{key}"
    assert f"{block}.{key}" in str(caught.value)


@pytest.mark.parametrize("block", ["terrain", "targets"])
def test_missing_block_raises_a_named_error(tmp_path: Path, block: str) -> None:
    def change(document: dict[str, Any]) -> None:
        del document[block]

    with pytest.raises(MissingConfigKeyError) as caught:
        load_terrain_config(write_config(tmp_path, change))
    assert caught.value.key == block


def test_null_terrain_value_is_missing_not_defaulted(tmp_path: Path) -> None:
    path = write_config(tmp_path, set_key("terrain", "min_valid_fraction", None))
    with pytest.raises(MissingConfigKeyError) as caught:
        load_terrain_config(path)
    assert caught.value.key == "terrain.min_valid_fraction"


@pytest.mark.parametrize("resampler", ["bilinear", "cubic", "AEQD_SPHERE", "aeqd_ellipsoid", ""])
def test_unknown_resampler_raises_a_named_error(tmp_path: Path, resampler: str) -> None:
    path = write_config(tmp_path, set_key("terrain", "resampler", resampler))
    with pytest.raises(UnknownResamplerError) as caught:
        load_terrain_config(path)
    assert caught.value.key == "terrain.resampler"


@pytest.mark.parametrize("grid", [0, -90, 0.0, -0.5])
def test_non_positive_grid_raises_a_named_error(tmp_path: Path, grid: float) -> None:
    path = write_config(tmp_path, set_key("terrain", "analysis_grid_m", grid))
    with pytest.raises(NonPositiveValueError) as caught:
        load_terrain_config(path)
    assert caught.value.key == "terrain.analysis_grid_m"


@pytest.mark.parametrize(("block", "key"), [("targets", "window_m"), ("targets", "stride_m")])
@pytest.mark.parametrize("value", [0, -2500])
def test_non_positive_window_or_stride_raises(
    tmp_path: Path, block: str, key: str, value: int
) -> None:
    with pytest.raises(NonPositiveValueError) as caught:
        load_terrain_config(write_config(tmp_path, set_key(block, key, value)))
    assert caught.value.key == f"{block}.{key}"


@pytest.mark.parametrize("value", [True, "90", float("nan"), float("inf"), [90]])
def test_grid_that_is_not_a_finite_number_raises(tmp_path: Path, value: object) -> None:
    path = write_config(tmp_path, set_key("terrain", "analysis_grid_m", value))
    with pytest.raises(InvalidConfigValueError) as caught:
        load_terrain_config(path)
    assert caught.value.key == "terrain.analysis_grid_m"


@pytest.mark.parametrize(
    "lags",
    [
        [],
        [270, 270, 540, 720],
        [360, 270, 540, 720],
        [0, 270, 540, 720],
        [-270, 540],
        "270",
        [True],
    ],
)
def test_bad_lags_raise(tmp_path: Path, lags: object) -> None:
    with pytest.raises(InvalidConfigValueError) as caught:
        load_terrain_config(write_config(tmp_path, set_key("terrain", "lags_m", lags)))
    assert caught.value.key == "terrain.lags_m"


@pytest.mark.parametrize("count", [4.0, True, 1, 0, -4])
def test_bad_min_lags_for_h_raises(tmp_path: Path, count: object) -> None:
    path = write_config(tmp_path, set_key("terrain", "min_lags_for_H", count))
    with pytest.raises(InvalidConfigValueError) as caught:
        load_terrain_config(path)
    assert caught.value.key == "terrain.min_lags_for_H"


@pytest.mark.parametrize("factor", [0.5, 0, -4, "4"])
def test_bad_min_lag_span_factor_raises(tmp_path: Path, factor: object) -> None:
    path = write_config(tmp_path, set_key("terrain", "min_lag_span_factor", factor))
    with pytest.raises(InvalidConfigValueError) as caught:
        load_terrain_config(path)
    assert caught.value.key == "terrain.min_lag_span_factor"


@pytest.mark.parametrize("fraction", [0, 0.0, -0.1, 1.0000001, 80])
def test_min_valid_fraction_outside_unit_interval_raises(tmp_path: Path, fraction: float) -> None:
    path = write_config(tmp_path, set_key("terrain", "min_valid_fraction", fraction))
    with pytest.raises(InvalidConfigValueError) as caught:
        load_terrain_config(path)
    assert caught.value.key == "terrain.min_valid_fraction"


def test_min_valid_fraction_of_one_is_accepted(tmp_path: Path) -> None:
    path = write_config(tmp_path, set_key("terrain", "min_valid_fraction", 1))
    assert load_terrain_config(path).min_valid_fraction == 1.0


@pytest.mark.parametrize(
    "detrend", [["quadratic"], ["none", "Plane"], [], ["none", "none"], "none"]
)
def test_bad_detrend_raises(tmp_path: Path, detrend: object) -> None:
    path = write_config(tmp_path, set_key("terrain", "detrend", detrend))
    with pytest.raises(InvalidConfigValueError) as caught:
        load_terrain_config(path)
    assert caught.value.key == "terrain.detrend"


def test_unknown_detrend_policy_has_its_own_error(tmp_path: Path) -> None:
    path = write_config(tmp_path, set_key("terrain", "detrend", ["none", "quadratic"]))
    with pytest.raises(UnknownDetrendError):
        load_terrain_config(path)


def test_unknown_terrain_key_raises(tmp_path: Path) -> None:
    path = write_config(tmp_path, set_key("terrain", "min_valid_fracton", 0.8))
    with pytest.raises(UnknownConfigKeyError) as caught:
        load_terrain_config(path)
    assert caught.value.key == "terrain.min_valid_fracton"


def test_duplicate_key_raises(tmp_path: Path) -> None:
    text = RUN_YAML.read_text(encoding="utf-8").replace(
        "  min_valid_fraction: 0.8\n", "  min_valid_fraction: 0.8\n  min_valid_fraction: 0.1\n"
    )
    assert text.count("min_valid_fraction:") == 2
    path = tmp_path / "run.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DuplicateConfigKeyError) as caught:
        load_run_config(path)
    assert caught.value.key == "min_valid_fraction"


@pytest.mark.parametrize("load", [load_run_config, load_terrain_config])
def test_missing_run_config_file_raises_a_named_error(
    tmp_path: Path, load: Callable[[Path], object]
) -> None:
    # The default path is found from the editable workspace install (PRD 8.3). Anywhere else
    # it points at no file, and the loader must say so with its own error, not a bare OSError.
    missing = tmp_path / "absent" / "run.yaml"
    with pytest.raises(MissingRunConfigError) as caught:
        load(missing)
    assert caught.value.key == "(document)"
    assert str(missing) in str(caught.value)
    assert "pass" in str(caught.value)


@pytest.mark.parametrize("text", ["", "- a\n- b\n", "just text\n"])
def test_document_that_is_not_a_mapping_raises(tmp_path: Path, text: str) -> None:
    path = tmp_path / "run.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(InvalidConfigValueError):
        load_run_config(path)


def test_every_named_error_is_a_config_error() -> None:
    for error in (
        MissingConfigKeyError,
        MissingRunConfigError,
        UnknownConfigKeyError,
        DuplicateConfigKeyError,
        InvalidConfigValueError,
        NonPositiveValueError,
        UnknownResamplerError,
        UnknownDetrendError,
    ):
        assert issubclass(error, ConfigError)
    assert issubclass(ConfigError, ValueError)
