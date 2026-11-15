"""Tests for export stage, bundle-version, assemble and the per-path bundle checks (T4L-08).

Every tree and bundle here is synthetic and built under pytest's tmp_path; none of its values is
an observation or a result, and nothing is written outside tmp_path.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from terraluna_pipeline import provenance
from terraluna_pipeline.export import (
    STAGING_MANIFEST,
    AssembleError,
    CardExportError,
    bundle_version,
    canonical_json,
    compose_background,
    main,
    validate_bundle,
    write_bundle,
)

# The synthetic card inputs of test_cards.py (loaded by name: pytest puts pipeline/tests on
# sys.path, and mypy does not resolve one test module from another).
cards: Any = importlib.import_module("test_cards")

ROOT = Path(__file__).resolve().parents[2]
HEX = "0123456789abcdef" * 4
CARD_ID = f"{cards.MOON_WINDOW}__{cards.EARTH_WINDOW}"
VERSION_UTC = "2026-11-15T0900Z"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect/></svg>\n'


# --- A synthetic real-shaped tree ---------------------------------------------------------------


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def by_id(root: Path) -> dict[str, Any]:
    from terraluna_pipeline.schema import ProductRecord

    records = [ProductRecord.model_validate(r) for r in read(root / "outputs" / "sources.json")]
    return {record.product_id: record for record in records}


def refresh_run_ids(root: Path) -> None:
    """Give the comparisons and fingerprints the run_ids of the current sources and config."""
    config = root / "config" / "run.yaml"
    sources = by_id(root)
    path = root / "outputs" / "comparisons" / f"{cards.TARGET_ID}.json"
    records = read(path)
    for record in records:
        record["run_id"] = provenance.run_id(config, [sources[p] for p in record["source_ids"]])
    write(path, records)
    for body in ("moon", "earth"):
        path = root / "data" / "processed" / "windows" / f"{body}.json"
        entries = read(path)
        for entry in entries:
            for print_ in entry["radar"]:
                products = [sources[p] for p in print_["product_ids"]]
                print_["run_id"] = provenance.run_id(config, products)
        write(path, entries)


def real_tree(tmp_path: Path) -> Path:
    root: Path = cards.card_tree(tmp_path)
    write(root / "outputs" / "validation.json", [cards.gate(f"G{n}") for n in range(7)])
    refresh_run_ids(root)
    assert main(["cards", "--root", str(root)]) == 0
    return root


def stage(root: Path, out: str = "data/processed/bundle-staging") -> int:
    return main(["stage", "--root", str(root), "--out", out])


def staging(root: Path) -> Path:
    return root / "data" / "processed" / "bundle-staging"


def add_figure(root: Path, data: bytes = SVG, source: str = "data/processed/figures") -> None:
    path = root / source / f"{CARD_ID}_A.svg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    entry = {
        "card_id": CARD_ID,
        "figure": "A",
        "path": f"{source}/{CARD_ID}_A.svg",
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "command": "python -m terraluna_pipeline.figures cards",
    }
    write(root / "outputs" / "figures" / "index.json", [entry])


# --- stage --------------------------------------------------------------------------------------


def test_stage_copies_every_present_path_and_lists_it(tmp_path: Path) -> None:
    root = real_tree(tmp_path)
    add_figure(root)
    assert main(["cards", "--root", str(root)]) == 0
    assert stage(root) == 0
    listing = read(staging(root) / STAGING_MANIFEST)
    paths = {entry["bundle_path"]: entry for entry in listing["files"]}
    assert set(paths) == {
        "validation.json",
        "sources.json",
        "targets.json",
        f"comparisons/{cards.TARGET_ID}.json",
        f"cards/{CARD_ID}.json",
        "windows/moon.json",
        "windows/earth.json",
        f"figures/{CARD_ID}_A.svg",
    }
    for path, entry in paths.items():
        data = (staging(root) / path).read_bytes()
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert entry["bytes"] == len(data)
        assert entry["producer"] and entry["source_path"]
    absent = {entry["bundle_path"] for entry in listing["not_present"]}
    assert "tiles/{layer}/{z}/{x}/{y}.webp" in absent
    assert "model/model_card.json" in absent
    assert all(entry["reason"] == "not_present" for entry in listing["not_present"])
    assert not (staging(root) / "model").exists()
    assert stage(root) == 0


@pytest.mark.parametrize(
    "missing",
    [
        "outputs/validation.json",
        "outputs/sources.json",
        "outputs/search/targets.json",
        f"outputs/comparisons/{cards.TARGET_ID}.json",
        f"outputs/cards/{CARD_ID}.json",
    ],
)
def test_stage_exits_3_naming_a_missing_required_path(
    tmp_path: Path, missing: str, capsys: pytest.CaptureFixture[str]
) -> None:
    root = real_tree(tmp_path)
    (root / missing).unlink()
    assert stage(root) == 3
    assert "blocked:" in capsys.readouterr().err
    assert not staging(root).exists()


def refused(root: Path, capsys: pytest.CaptureFixture[str], text: str) -> None:
    assert stage(root) == 1
    assert text in capsys.readouterr().out
    assert not staging(root).exists()


def test_stage_refuses_a_mock_record(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = real_tree(tmp_path)
    targets = read(root / "outputs" / "search" / "targets.json")
    targets[0]["mock"] = True
    write(root / "outputs" / "search" / "targets.json", targets)
    refused(root, capsys, "mock_record")


def test_stage_reads_index_bundle_paths_from_data_processed_figures(tmp_path: Path) -> None:
    root = real_tree(tmp_path)
    add_figure(root)
    index = root / "outputs" / "figures" / "index.json"
    entries = read(index)
    entries[0]["path"] = f"figures/{CARD_ID}_A.svg"
    write(index, entries)
    assert stage(root) == 0
    assert (staging(root) / "figures" / f"{CARD_ID}_A.svg").read_bytes() == SVG


def test_stage_refuses_a_file_under_fixtures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = real_tree(tmp_path)
    add_figure(root, source="fixtures/figures")
    refused(root, capsys, "fixture_file")


def test_stage_refuses_an_svg_outside_the_allowlist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = real_tree(tmp_path)
    add_figure(root, b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>\n')
    refused(root, capsys, f"figures/{CARD_ID}_A.svg")


def test_stage_refuses_a_figure_whose_sha256_differs_from_the_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = real_tree(tmp_path)
    add_figure(root)
    (root / "data" / "processed" / "figures" / f"{CARD_ID}_A.svg").write_bytes(SVG + b"\n")
    refused(root, capsys, "sha256 differs")


def add_model(root: Path, onnx: bytes, record: dict[str, Any], onnx_sha: str) -> None:
    model = root / "data" / "processed" / "model"
    model.mkdir(parents=True, exist_ok=True)
    (model / "encoder.fp16.onnx").write_bytes(onnx)
    write(model / "export_record.json", record)
    write(root / "data" / "processed" / "report" / "model" / "model_card.json", {"x": onnx_sha})


def test_stage_refuses_a_random_init_model_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = real_tree(tmp_path)
    onnx = b"synthetic onnx bytes"
    add_model(root, onnx, {"checkpoint": "random-init"}, hashlib.sha256(onnx).hexdigest())
    refused(root, capsys, "random-init")


def test_stage_refuses_a_mock_model_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = real_tree(tmp_path)
    onnx = b"synthetic onnx bytes"
    add_model(root, onnx, {"mock": True}, hashlib.sha256(onnx).hexdigest())
    refused(root, capsys, "says mock")


def test_stage_refuses_an_onnx_whose_sha256_differs_from_the_model_card(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = real_tree(tmp_path)
    add_model(root, b"synthetic onnx bytes", {"checkpoint": "frozen"}, HEX)
    refused(root, capsys, "differs from model_card.onnx_sha256")


def test_stage_refuses_a_blown_budget(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = real_tree(tmp_path)
    extra = [cards.product(f"SYNTH/padding-{n:04d}", "earth") for n in range(400)]
    write(root / "outputs" / "padding" / "sources.json", extra)
    refused(root, capsys, "budget_exceeded")


def test_stage_refuses_an_invalid_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = real_tree(tmp_path)
    path = root / "outputs" / "comparisons" / f"{cards.TARGET_ID}.json"
    records = read(path)
    records[0]["distances_by_family"]["terrain_relief"]["value"] = None
    write(path, records)
    refused(root, capsys, f"comparisons/{cards.TARGET_ID}.json")


# --- stale run_id -------------------------------------------------------------------------------


def test_a_changed_source_checksum_makes_records_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = real_tree(tmp_path)
    sources = read(root / "outputs" / "sources.json")
    sources[0]["checksum"] = "f" * 64
    write(root / "outputs" / "sources.json", sources)
    refused(root, capsys, "stale_run_id")


def test_a_changed_polygon_makes_the_card_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = real_tree(tmp_path)
    path = root / "outputs" / "cards" / f"{CARD_ID}.json"
    card = read(path)
    card["target"]["polygon"]["ring_lonlat"][1] = [12.625, -89.5]
    write(path, card)
    refused(root, capsys, "stale_run_id")


def test_a_changed_config_byte_makes_records_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = real_tree(tmp_path)
    config = root / "config" / "run.yaml"
    config.write_bytes(config.read_bytes() + b"# edited\n")
    refused(root, capsys, "stale_run_id")


# --- sources merge ------------------------------------------------------------------------------


def test_identical_duplicate_sources_are_kept_once(tmp_path: Path) -> None:
    root = real_tree(tmp_path)
    shutil.copy(root / "outputs" / "sources.json", root / "outputs" / "copy-sources.json")
    (root / "outputs" / "dup").mkdir()
    shutil.copy(root / "outputs" / "sources.json", root / "outputs" / "dup" / "sources.json")
    assert stage(root) == 0
    merged = read(staging(root) / "sources.json")
    assert len(merged) == len({record["product_id"] for record in merged})


def test_a_verified_record_supersedes_a_partial_one(tmp_path: Path) -> None:
    root = real_tree(tmp_path)
    partial = read(root / "outputs" / "sources.json")
    verified = [
        dict(record, verification_status="VERIFIED", local_path="data/raw/x.tif")
        for record in partial
        if record["product_id"] == cards.MOON_DEM
    ]
    write(root / "outputs" / "realcard" / "sources.json", verified)
    assert stage(root) == 0
    merged = {r["product_id"]: r for r in read(staging(root) / "sources.json")}
    assert merged[cards.MOON_DEM]["verification_status"] == "VERIFIED"
    superseded = read(staging(root) / STAGING_MANIFEST)["superseded_sources"]
    assert [item["product_id"] for item in superseded] == [cards.MOON_DEM]
    assert superseded[0]["superseded"]["verification_status"] == "PARTIAL"


def test_differing_duplicate_sources_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = real_tree(tmp_path)
    other = [
        dict(record, provider="another provider")
        for record in read(root / "outputs" / "sources.json")
    ]
    write(root / "outputs" / "other" / "sources.json", other)
    refused(root, capsys, "differs between")


# --- background composition ---------------------------------------------------------------------


def test_compose_background_refuses_a_model_part_of_another_model() -> None:
    terrain = {"families": {"model_embedding": None}, "family_reasons": {"model_embedding": "x"}}
    card = {"model_id": "terraluna-enc-v1", "onnx_sha256": HEX}
    good = {"model_id": "terraluna-enc-v1", "onnx_sha256": HEX, "n": 1}
    composed = compose_background(terrain, good, card)
    assert composed["families"]["model_embedding"] == good
    assert composed["family_reasons"]["model_embedding"] is None
    assert compose_background(terrain, None, None) == terrain
    with pytest.raises(CardExportError):
        compose_background(terrain, dict(good, onnx_sha256="f" * 64), card)
    with pytest.raises(CardExportError):
        compose_background(terrain, good, None)


# --- bundle-version -----------------------------------------------------------------------------


def test_bundle_version_is_the_prd_8_4_form() -> None:
    assert bundle_version(VERSION_UTC, "3f9c2a1", "3f9c2a1" + "0" * 33) == (
        "2026-11-15T0900Z-3f9c2a1"
    )


def test_bundle_version_needs_a_prefix_of_head() -> None:
    with pytest.raises(ValueError, match=r"3f9c2a1.*abcdef0"):
        bundle_version(VERSION_UTC, "3f9c2a1", "abcdef0" + "0" * 33)
    with pytest.raises(ValueError):
        bundle_version("2026-11-15T09:00Z", "3f9c2a1", "3f9c2a1")


# --- assemble -----------------------------------------------------------------------------------


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_tree(tmp_path: Path) -> tuple[Path, str]:
    root = real_tree(tmp_path)
    (root / ".gitignore").write_text("data/\nrelease/\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    git(root, "config", "core.hooksPath", "/dev/null")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "synthetic test tree")
    head = git(root, "rev-parse", "HEAD")
    assert stage(root) == 0
    return root, f"{VERSION_UTC}-{head[:7]}"


def assemble(root: Path, version: str, release: str = "release") -> int:
    return main(
        [
            "assemble",
            "--root",
            str(root),
            "--staging",
            "data/processed/bundle-staging",
            "--bundle-version",
            version,
            "--release-dir",
            release,
        ]
    )


def test_assemble_writes_a_valid_production_bundle(tmp_path: Path) -> None:
    root, version = git_tree(tmp_path)
    assert (
        main(["bundle-version", "--root", str(root), "--utc", VERSION_UTC, "--sha", version[-7:]])
        == 0
    )
    assert assemble(root, version) == 0
    bundle = root / "release" / version
    assert validate_bundle(bundle, production=True, contracts=True) == []
    manifest = read(bundle / "manifest.json")
    head = git(root, "rev-parse", "HEAD")
    sources = by_id(root)
    config = root / "config" / "run.yaml"
    assert manifest["bundle_version"] == version
    assert manifest["git_sha"] == head
    assert manifest["created_utc"] == "2026-11-15T09:00:00Z"
    assert manifest["shipping_mode"] == "C"
    assert manifest["mock"] is False
    assert manifest["targets"] == [cards.TARGET_ID]
    assert manifest["model"] is None
    staged = [ProductRecord for ProductRecord in sources.values()]
    assert manifest["run_id"] == provenance.run_id(config, staged)
    validation = read(bundle / "validation.json")
    assert [entry["gate_id"] for entry in validation] == [f"G{n}" for n in range(7)]
    assert validation[:6] == read(root / "outputs" / "validation.json")[:6]
    assert validation[6]["status"] == "NOT_RUN"
    assert validation[6]["scope"].startswith(f"G6 for bundle {version} is evaluated after")
    assert not list((root / "release").glob(".*"))


def test_assembling_twice_gives_byte_identical_files(tmp_path: Path) -> None:
    root, version = git_tree(tmp_path)
    assert assemble(root, version, "release") == 0
    assert assemble(root, version, "release-2") == 0
    first, second = root / "release" / version, root / "release-2" / version
    names = sorted(p.relative_to(first).as_posix() for p in first.rglob("*") if p.is_file())
    assert names == sorted(
        p.relative_to(second).as_posix() for p in second.rglob("*") if p.is_file()
    )
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_assemble_refuses_an_existing_version(tmp_path: Path) -> None:
    root, version = git_tree(tmp_path)
    assert assemble(root, version) == 0
    assert assemble(root, version) == 1


def test_assemble_refuses_a_dirty_tree(tmp_path: Path) -> None:
    root, version = git_tree(tmp_path)
    (root / "untracked.txt").write_text("x", encoding="utf-8")
    assert assemble(root, version) == 1
    assert not (root / "release" / version).exists()


def test_assemble_refuses_a_sha_that_is_not_head(tmp_path: Path) -> None:
    root, _ = git_tree(tmp_path)
    assert assemble(root, f"{VERSION_UTC}-abcdef0") == 1


def test_assemble_refuses_a_null_shipping_mode(tmp_path: Path) -> None:
    root, version = git_tree(tmp_path)
    (root / "config" / "run.yaml").write_text("run:\n  shipping_mode: null\n", encoding="utf-8")
    git(root, "commit", "-q", "-am", "unset mode")
    version = f"{VERSION_UTC}-{git(root, 'rev-parse', 'HEAD')[:7]}"
    assert assemble(root, version) == 1


def test_assemble_injectable_git_runner_refuses_dirty(tmp_path: Path) -> None:
    from terraluna_pipeline.export import assemble as assemble_bundle

    root, version = git_tree(tmp_path)

    def dirty(args: Any) -> str:
        return " M file\n" if args[0] == "status" else version[-7:] + "0" * 33

    with pytest.raises(AssembleError, match="not clean"):
        assemble_bundle(
            staging(root),
            version,
            root / "release",
            root=root,
            config=root / "config" / "run.yaml",
            git=dirty,
        )


# --- validate_bundle with contracts -------------------------------------------------------------


def contract_bundle(tmp_path: Path, files: Mapping[str, object]) -> Path:
    fields = {
        "bundle_version": "test-0",
        "schema_version": "1.0.0",
        "mock": False,
        "run_id": None,
        "git_sha": None,
        "created_utc": "2026-11-14T00:00:00Z",
        "shipping_mode": "C",
        "targets": [],
        "model": None,
    }
    out = tmp_path / "bundle"
    write_bundle(out, files, fields)
    return out


def gates_json(count: int = 7) -> list[dict[str, Any]]:
    return [cards.gate(f"G{n}") for n in range(count)]


def error_codes(bundle: Path, production: bool = True) -> set[str]:
    return {e.code for e in validate_bundle(bundle, production=production, contracts=True)}


def test_contracts_accept_valid_records(tmp_path: Path) -> None:
    bundle = contract_bundle(
        tmp_path, {"validation.json": gates_json(), "targets.json": [cards.target("C")]}
    )
    assert validate_bundle(bundle, production=True, contracts=True) == []


def test_contracts_refuse_a_schema_error(tmp_path: Path) -> None:
    bundle = contract_bundle(tmp_path, {"validation.json": gates_json(), "targets.json": ["x"]})
    assert "contract_invalid" in error_codes(bundle)


def test_contracts_refuse_a_missing_gate(tmp_path: Path) -> None:
    bundle = contract_bundle(tmp_path, {"validation.json": gates_json(6)})
    assert "gate_missing" in error_codes(bundle)


def test_contracts_refuse_an_unknown_path(tmp_path: Path) -> None:
    bundle = contract_bundle(tmp_path, {"validation.json": gates_json(), "extra/x.json": {}})
    assert "unknown_bundle_path" in error_codes(bundle)


def test_contracts_refuse_a_mock_record_on_production_only(tmp_path: Path) -> None:
    files = {"validation.json": gates_json(), "targets.json": [cards.target("C", mock=True)]}
    bundle = contract_bundle(tmp_path, files)
    assert "mock_on_production" in error_codes(bundle)
    assert "mock_on_production" not in error_codes(bundle, production=False)


def test_contracts_refuse_an_svg_outside_the_allowlist(tmp_path: Path) -> None:
    bad = b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"/>\n'
    bundle = contract_bundle(tmp_path, {"validation.json": gates_json(), "figures/a.svg": bad})
    assert "contract_invalid" in error_codes(bundle)


def test_mock_bundle_still_passes_non_production_with_contracts() -> None:
    assert (
        validate_bundle(ROOT / "fixtures" / "mock-bundle", production=False, contracts=True) == []
    )


# --- Makefile -----------------------------------------------------------------------------------


def test_make_n_bundle_prints_the_three_commands() -> None:
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    result = subprocess.run(
        ["make", "-n", "bundle", "BUNDLE_UTC=2026-11-15T0900Z", "BUNDLE_SHA=3f9c2a1"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    text = result.stdout
    stage_at = text.index("terraluna_pipeline.export stage --out data/processed/bundle-staging")
    version_at = text.index("terraluna_pipeline.export bundle-version --utc 2026-11-15T0900Z")
    assemble_at = text.index("terraluna_pipeline.export assemble --staging")
    assert stage_at < version_at < assemble_at
