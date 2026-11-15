"""T2-07: oracle check of our polarimetry against polsartools on one real UAVSAR scene.

PRD 12 ("Added to T1 and T2"), PRD 6.2 (Earth side), D19, PRD 9 (G1) and PRD 5 (UAVSAR
products). The real-scene check, :func:`test_real_scene_matches_polsartools`, reads the scene
named by ``TERRALUNA_UAVSAR_SCENE``, compares our SC, OC, base_SC, hybrid-pol Stokes
parameters (both ellipticities) and m with polsartools' outputs on the declared window, and
writes ``fixtures/polarimetry/oracle_run.json`` from the run. Without a scene it fails with
``data not present``; without a decided tolerance it fails with ``tolerance not declared``.

The other tests check the conditions around it: polsartools stays out of ``pipeline/src``
(D19), the declarations load and cover every compared field, and the refusals and reports
behave as stated. Their small annotation and GRD files are synthetic, written to ``tmp_path``
only to reach a refusal, and never compared with anything.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pytest

from terraluna_pipeline.polarimetry import InvalidReason, ObsState, StatedArray

if TYPE_CHECKING:
    from pipeline.tests.polarimetry import oracle
else:
    from polarimetry import oracle

REPO_ROOT = oracle.REPO_ROOT

DECLARED_WINDOW = oracle.Window(row0=14981, col0=2469, rows=512, cols=512)
"""The compared window, zero-based GRD rows 14981-15492 and columns 2469-2980.

It was chosen from the data footprint only, before any comparison: the 512 x 512 window centred
on the scene's middle row band (row 30474 // 2 - 256) and on the middle of that band's columns
where all six products are finite and the three powers positive. It lies inside the swath of
Icelnd_00208_15083_004_150530_L090_CX_01."""


# --- D19: polsartools is a development-only oracle -------------------------------------------


def test_no_pipeline_src_module_imports_polsartools() -> None:
    sources = sorted((REPO_ROOT / "pipeline" / "src").rglob("*.py"))
    assert sources, "no pipeline source found"
    found = [
        place
        for path in sources
        for place in oracle.polsartools_imports(
            path.read_text(encoding="utf-8"), path.relative_to(REPO_ROOT).as_posix()
        )
    ]
    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        "import polsartools",
        "import numpy, polsartools.utils as u",
        "from polsartools import simulate_CP",
        "from polsartools.polsar.cp import m_chi",
        "def f():\n    import polsartools",
        "import importlib\nimportlib.import_module('polsartools')",
        "from importlib import import_module\nimport_module('polsartools.sensors')",
        "__import__('polsartools')",
    ],
)
def test_import_scan_finds_each_form_of_import(source: str) -> None:
    assert len(oracle.polsartools_imports(source)) == 1


@pytest.mark.parametrize(
    "source",
    ["import polsartoolsx", "from .polsartools import x", "x = 'polsartools'", "import numpy"],
)
def test_import_scan_ignores_other_modules(source: str) -> None:
    assert oracle.polsartools_imports(source) == []


def test_polsartools_is_only_a_development_dependency() -> None:
    pipeline = tomllib.loads((REPO_ROOT / "pipeline" / "pyproject.toml").read_text("utf-8"))
    project = pipeline["project"]
    runtime = list(project.get("dependencies", []))
    runtime += [d for deps in project.get("optional-dependencies", {}).values() for d in deps]
    assert not any(d.lower().startswith("polsartools") for d in runtime)
    groups = pipeline["dependency-groups"]
    holding = [name for name, deps in groups.items() if "polsartools==0.13" in deps]
    assert holding == ["oracle"]
    root = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    root_deps = [d for deps in root.get("dependency-groups", {}).values() for d in deps]
    assert not any(str(d).lower().startswith("polsartools") for d in root_deps)
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text("utf-8"))
    locked = [p for p in lock["package"] if p["name"] == "polsartools"]
    assert [p["version"] for p in locked] == ["0.13"]
    member = next(p for p in lock["package"] if p["name"] == "terraluna-pipeline")
    assert "polsartools" not in [d["name"] for d in member.get("dependencies", [])]
    assert [d["name"] for d in member["dev-dependencies"]["oracle"]] == ["polsartools"]


# --- Declarations ----------------------------------------------------------------------------


def test_committed_tolerance_is_decided_and_covers_every_compared_field() -> None:
    tolerance = oracle.load_tolerance(oracle.TOLERANCE_PATH, compared_fields=oracle.COMPARED_FIELDS)
    assert tolerance.decided_by and tolerance.decided_utc
    assert {name for g in tolerance.groups for name in g.fields} == set(oracle.COMPARED_FIELDS)
    assert tolerance.conversion_tolerance.rtol > 0.0


def test_committed_conventions_map_both_ellipticities_once() -> None:
    conventions = oracle.load_conventions(oracle.CONVENTIONS_PATH)
    assert sorted(conventions.polsartools_chi(chi) for chi in oracle.TERRALUNA_CHI_TX) == [
        -45.0,
        45.0,
    ]
    for chi in oracle.TERRALUNA_CHI_TX:
        assert conventions.terraluna_chi(conventions.polsartools_chi(chi)) == chi
    assert sorted(conventions.stokes_map()) == ["s1", "s2", "s3", "s4"]
    assert conventions.processing.win == 1


def _tolerance_json(**changes: object) -> dict[str, object]:
    data: dict[str, object] = json.loads(oracle.TOLERANCE_PATH.read_text("utf-8"))
    data.update(changes)
    return data


def test_missing_tolerance_is_not_declared(tmp_path: Path) -> None:
    with pytest.raises(oracle.ToleranceNotDeclared, match=r"^tolerance not declared"):
        oracle.load_tolerance(tmp_path / "absent.json", compared_fields=oracle.COMPARED_FIELDS)


@pytest.mark.parametrize(
    "changes",
    [
        {"decided_by": None},
        {"decided_by": "  "},
        {"decided_utc": None},
        {"declared_before_results": False},
        {"groups": []},
    ],
    ids=["no-person", "blank-person", "no-date", "after-results", "no-groups"],
)
def test_undecided_tolerance_is_not_declared(tmp_path: Path, changes: dict[str, object]) -> None:
    path = tmp_path / "oracle_tolerance.json"
    path.write_text(json.dumps(_tolerance_json(**changes)), "utf-8")
    with pytest.raises(oracle.ToleranceNotDeclared, match=r"^tolerance not declared"):
        oracle.load_tolerance(path, compared_fields=oracle.COMPARED_FIELDS)


def test_tolerance_missing_a_compared_field_is_not_declared(tmp_path: Path) -> None:
    data = _tolerance_json()
    groups = data["groups"]
    assert isinstance(groups, list)
    groups[1]["fields"] = ["m@chi_tx=-45"]
    path = tmp_path / "oracle_tolerance.json"
    path.write_text(json.dumps(data), "utf-8")
    with pytest.raises(oracle.ToleranceNotDeclared, match=r"not covered: m@chi_tx=\+45"):
        oracle.load_tolerance(path, compared_fields=oracle.COMPARED_FIELDS)


# --- The scene and its annotation ------------------------------------------------------------

_SYNTHETIC_ROWS, _SYNTHETIC_COLS = 3, 4


def _synthetic_annotation() -> str:
    """A minimal annotation with the GRD layout keys (synthetic, for refusals only)."""
    lines = [
        "; synthetic annotation for a refusal test; not a UAVSAR product",
        f"grd_pwr.set_rows (pixels) = {_SYNTHETIC_ROWS} ; GRD Lines",
        f"grd_pwr.set_cols (pixels) = {_SYNTHETIC_COLS} ; GRD Samples",
        f"grd_mag.set_rows (pixels) = {_SYNTHETIC_ROWS} ; GRD Lines",
        f"grd_mag.set_cols (pixels) = {_SYNTHETIC_COLS} ; GRD Samples",
        "set_hddr (bytes) = 0 ; Header Bytes in Raw Data",
        "set_tail (bytes) = 0 ; Tail Bytes in Raw Data",
        "val_endi (&) = LITTLE ENDIAN ; Byte Order",
        "grd_pwr.val_frmt (&) = REAL*4 ; Format",
        "grd_pwr.val_size (bytes) = 4 ; Bytes per value",
        "grd_mag.val_size (bytes) = 8 ; Bytes per value",
        "grd_pwr.row_addr (deg) = 66.0 ; Center Latitude of Upper Left Pixel",
        "grd_pwr.col_addr (deg) = -16.5 ; Center Longitude of Upper Left Pixel",
        "grd_pwr.row_mult (deg/pixel) = -0.0001 ; Latitude Pixel Spacing",
        "grd_pwr.col_mult (deg/pixel) = 0.0002 ; Longitude Pixel Spacing",
        "Date of Acquisition (&) = 30-May-2015 12:00:00 UTC",
        "Look Direction (&) = Left",
        "Steering Angle (90 is Boresite) (deg) = 90.0",
    ]
    lines += [f"grd{p} = synthetic{p}.grd ; File Size bytes" for p in oracle.PRODUCTS]
    return "\n".join(lines) + "\n"


def _write_synthetic_scene(directory: Path, *, omit: str | None = None) -> None:
    directory.mkdir()
    (directory / "synthetic.ann").write_text(_synthetic_annotation(), "latin-1")
    for product in oracle.PRODUCTS:
        if product == omit:
            continue
        dtype = "<f4" if product in oracle.REAL_PRODUCTS else "<c8"
        np.ones((_SYNTHETIC_ROWS, _SYNTHETIC_COLS), dtype=dtype).tofile(
            directory / f"synthetic{product}.grd"
        )


def test_scene_location_unset_is_data_not_present() -> None:
    with pytest.raises(oracle.DataNotPresent, match=rf"^data not present: {oracle.SCENE_ENV}"):
        oracle.locate_scene({})


def test_scene_directory_absent_is_data_not_present(tmp_path: Path) -> None:
    where = tmp_path / "no-such-scene"
    with pytest.raises(oracle.DataNotPresent, match=r"^data not present") as error:
        oracle.locate_scene({oracle.SCENE_ENV: str(where)})
    assert str(where) in str(error.value)


def test_scene_without_annotation_is_data_not_present(tmp_path: Path) -> None:
    with pytest.raises(oracle.DataNotPresent, match=r"found no \.ann file") as error:
        oracle.locate_scene({oracle.SCENE_ENV: str(tmp_path)})
    assert str(tmp_path) in str(error.value)


def test_scene_missing_a_grd_product_is_data_not_present(tmp_path: Path) -> None:
    scene = tmp_path / "scene"
    _write_synthetic_scene(scene, omit="HVVV")
    with pytest.raises(oracle.DataNotPresent, match=r"^data not present") as error:
        oracle.locate_scene({oracle.SCENE_ENV: str(scene)})
    assert str(scene / "syntheticHVVV.grd") in str(error.value)


def test_scene_whose_files_disagree_with_the_annotation_is_refused(tmp_path: Path) -> None:
    scene = tmp_path / "scene"
    _write_synthetic_scene(scene)
    np.ones(5, dtype="<f4").tofile(scene / "syntheticHHHH.grd")
    with pytest.raises(oracle.SceneLayoutError, match=r"syntheticHHHH\.grd has 20 bytes"):
        oracle.locate_scene({oracle.SCENE_ENV: str(scene)})


def test_annotation_layout_and_angles_come_from_the_annotation(tmp_path: Path) -> None:
    scene_dir = tmp_path / "scene"
    _write_synthetic_scene(scene_dir)
    scene = oracle.locate_scene({oracle.SCENE_ENV: str(scene_dir)})
    layout = scene.layout
    assert (layout.rows, layout.cols) == (_SYNTHETIC_ROWS, _SYNTHETIC_COLS)
    assert (layout.real_dtype, layout.complex_dtype) == ("<f4", "<c8")
    assert layout.upper_left_lat_deg == 66.0
    assert layout.lon_step_deg == 0.0002
    entries = oracle.parse_annotation(_synthetic_annotation())
    steering = entries["Steering Angle (90 is Boresite)"][0]
    assert (steering.unit, steering.value) == ("deg", "90.0")


def test_angles_in_radians_are_converted_with_story_1() -> None:
    annotation = oracle.Annotation(oracle.parse_annotation("look (rad) = 0.5 ; an angle\n"))
    assert annotation.angle_degrees("look") == pytest.approx(np.degrees(0.5), rel=1e-15)
    with pytest.raises(oracle.SceneLayoutError, match=r"not an angle"):
        oracle.Annotation(oracle.parse_annotation("look (m) = 0.5\n")).angle_degrees("look")


def test_annotation_unit_mismatch_is_refused() -> None:
    annotation = oracle.Annotation(oracle.parse_annotation("set_hddr (pixels) = 0\n"))
    with pytest.raises(oracle.SceneLayoutError, match=r"expected 'bytes'"):
        annotation.integer("set_hddr", unit="bytes")


# --- The extent polsartools processes ---------------------------------------------------------


def test_polsartools_extent_surrounds_the_window_with_a_margin() -> None:
    window = oracle.Window(row0=10, col0=20, rows=4, cols=5)
    extent, rows, cols = oracle.polsartools_extent(window, margin=1, grid_rows=100, grid_cols=100)
    assert extent == oracle.Window(row0=9, col0=19, rows=6, cols=7)
    grid = np.arange(100 * 100).reshape(100, 100)
    clipped = grid[9:15, 19:26]
    assert np.array_equal(clipped[rows, cols], grid[10:14, 20:25])


def test_polsartools_extent_stops_at_the_grid_edge() -> None:
    window = oracle.Window(row0=0, col0=96, rows=4, cols=4)
    extent, rows, cols = oracle.polsartools_extent(window, margin=2, grid_rows=50, grid_cols=100)
    assert extent == oracle.Window(row0=0, col0=94, rows=6, cols=6)
    assert (rows, cols) == (slice(0, 4), slice(2, 6))


def test_polsartools_extent_without_margin_is_the_window() -> None:
    window = oracle.Window(row0=3, col0=4, rows=2, cols=2)
    extent, rows, cols = oracle.polsartools_extent(window, margin=0, grid_rows=10, grid_cols=10)
    assert extent == window
    assert (rows, cols) == (slice(0, 2), slice(0, 2))


# --- The comparison report -------------------------------------------------------------------


def _stated(values: list[float], invalid: tuple[int, ...] = ()) -> StatedArray:
    value = np.array(values, dtype=np.float64)
    state = np.full(value.shape, ObsState.VALID, dtype=np.uint8)
    reason = np.full(value.shape, InvalidReason.NONE.value, dtype="<U32")
    for index in invalid:
        value[index] = np.nan
        state[index] = ObsState.INVALID
        reason[index] = InvalidReason.COV_NOT_PSD.value
    return StatedArray(value=value, state=state, reason=reason)


def test_comparison_reports_field_pixel_count_and_max_difference() -> None:
    ours = _stated([1.0, 2.0, 3.0, 4.0], invalid=(3,))
    theirs = np.array([1.0, 2.5, 3.0, 9.0])
    result = oracle.compare_field("sc_ll", ours, theirs, np.full(4, 0.1))
    assert (result.n_pixels, result.n_compared, result.n_mismatch) == (4, 3, 1)
    assert result.not_compared == {"COV_NOT_PSD": 1}
    assert result.max_abs_diff == 0.5
    assert result.max_diff_over_allowance == pytest.approx(5.0)
    failure = result.failure()
    assert failure is not None
    assert failure.startswith("sc_ll: 1 of 3 compared pixels differ")
    assert "max |difference| 0.5" in failure


def test_agreement_under_another_convention_is_reported_and_still_fails() -> None:
    ours = _stated([0.25, -0.5])
    declared = np.array([-0.25, 0.5])
    result = oracle.compare_field(
        "s4@chi_tx=-45",
        ours,
        declared,
        np.full(2, 1e-9),
        {"the opposite S4 sign": -declared},
    )
    assert not result.matches
    failure = result.failure()
    assert failure is not None
    assert "Convention difference" in failure
    assert "the opposite S4 sign" in failure


def test_nonfinite_oracle_value_is_a_mismatch() -> None:
    result = oracle.compare_field("m@chi_tx=+45", _stated([0.5]), np.array([np.nan]), np.ones(1))
    assert (result.n_mismatch, result.n_oracle_nonfinite) == (1, 1)
    assert result.max_abs_diff == float("inf")


def test_no_compared_pixel_fails() -> None:
    result = oracle.compare_field("oc_ll", _stated([1.0], invalid=(0,)), np.ones(1), np.ones(1))
    assert not result.matches
    assert result.failure() == "oc_ll: no pixel was compared (ours: {'COV_NOT_PSD': 1})"


def test_run_record_is_written_as_sorted_json(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    oracle.write_run_record({"b": 1, "a": [0.5]}, path)
    first = path.read_bytes()
    oracle.write_run_record({"a": [0.5], "b": 1}, path)
    assert path.read_bytes() == first == b'{\n  "a": [\n    0.5\n  ],\n  "b": 1\n}\n'


# --- The real-scene check (G1 lists this test) -----------------------------------------------


def test_real_scene_matches_polsartools(tmp_path: Path) -> None:
    """Our polarimetry against polsartools on the declared window of the real scene."""
    import os

    try:
        scene = oracle.locate_scene(os.environ)
        tolerance = oracle.load_tolerance(
            oracle.TOLERANCE_PATH, compared_fields=oracle.COMPARED_FIELDS
        )
        conventions = oracle.load_conventions(oracle.CONVENTIONS_PATH)
        polsartools, gdal = oracle.import_polsartools()
    except oracle.OracleNotReady as error:
        pytest.fail(str(error), pytrace=False)

    run = oracle.run_oracle_check(
        scene, DECLARED_WINDOW, tolerance, conventions, polsartools, gdal, tmp_path
    )
    oracle.write_run_record(run.record, oracle.RUN_RECORD_PATH)
    assert run.failures == [], "\n".join(run.failures)


def test_real_scene_check_without_a_scene_fails_with_data_not_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    where = tmp_path / "absent"
    monkeypatch.setenv(oracle.SCENE_ENV, str(where))
    with pytest.raises(pytest.fail.Exception, match=r"^data not present") as error:
        test_real_scene_matches_polsartools(tmp_path)
    assert str(where) in str(error.value)
    monkeypatch.delenv(oracle.SCENE_ENV)
    with pytest.raises(pytest.fail.Exception, match=r"^data not present"):
        test_real_scene_matches_polsartools(tmp_path)


def test_real_scene_check_without_a_tolerance_fails_with_tolerance_not_declared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scene = tmp_path / "scene"
    _write_synthetic_scene(scene)
    monkeypatch.setenv(oracle.SCENE_ENV, str(scene))
    monkeypatch.setattr(oracle, "TOLERANCE_PATH", tmp_path / "oracle_tolerance.json")
    with pytest.raises(pytest.fail.Exception, match=r"^tolerance not declared"):
        test_real_scene_matches_polsartools(tmp_path)
