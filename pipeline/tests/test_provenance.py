"""Tests for provenance.py: the one run_id implementation (CONTRACTS c.4, PRD 8.13).

``run_id`` is ``"sha256:" + sha256(canonical_json({"config_sha256": ..., "products": ...}))``.
Any edit to config/run.yaml or to a listed product's checksum changes it, and nothing else does:
no wall-clock time and no random value enter it, so re-running a step gives the same id.
"""

from __future__ import annotations

import ast
import hashlib
import json
import random
import re
import shlex
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter

from terraluna_pipeline import export, provenance
from terraluna_pipeline.schema import ProductRecord, RunId, Sha256

PROVENANCE_PY = Path(provenance.__file__)
HEX_A = "a" * 64
HEX_B = "b" * 64
CONFIG_TEXT = b"run:\n  shipping_mode: null\nterrain:\n  analysis_grid_m: 90\n"


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "run.yaml"
    path.write_bytes(CONFIG_TEXT)
    return path


def expected_run_id(config_bytes: bytes, products: list[tuple[str, str | None]]) -> str:
    """The CONTRACTS c.4 definition, written out independently of provenance.py."""
    document = {
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "products": [[pid, checksum] for pid, checksum in sorted(products, key=lambda p: p[0])],
    }
    text = json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    return "sha256:" + hashlib.sha256((text + "\n").encode("utf-8")).hexdigest()


def product_record(product_id: str, checksum: str | None) -> ProductRecord:
    return ProductRecord.model_validate(
        {
            "product_id": product_id,
            "provider": "Example provider",
            "version": "v1",
            "source_url": None,
            "local_path": None if checksum is None else "data/raw/example/dem.tif",
            "checksum": checksum,
            "body": "moon",
            "acquisition_time": "unknown",
            "frequency_or_wavelength": None,
            "transmit_receive_convention": None,
            "units": "m",
            "calibration_type": None,
            "native_grid": None,
            "effective_resolution": "unknown",
            "crs_and_frame": "unknown",
            "incidence_source": None,
            "qa_definition": "unknown",
            "license_or_attribution": "unknown",
            "verification_status": "PARTIAL",
        }
    )


# --- run_id -------------------------------------------------------------------------------------


def test_run_id_is_sha256_prefix_and_64_lowercase_hex(config: Path) -> None:
    value = provenance.run_id(config, [("pgda78-site01-dem", HEX_A)])
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", value)
    assert TypeAdapter(RunId).validate_python(value) == value


def test_run_id_is_the_contract_definition(config: Path) -> None:
    products: list[tuple[str, str | None]] = [("b-product", HEX_B), ("a-product", None)]
    assert provenance.run_id(config, products) == expected_run_id(CONFIG_TEXT, products)


def test_run_id_of_no_products_is_defined(config: Path) -> None:
    assert provenance.run_id(config, []) == expected_run_id(CONFIG_TEXT, [])


def test_run_id_changes_when_one_config_byte_changes(tmp_path: Path, config: Path) -> None:
    other = tmp_path / "other.yaml"
    other.write_bytes(CONFIG_TEXT.replace(b"90", b"91"))
    products = [("dem", HEX_A)]
    assert provenance.run_id(config, products) != provenance.run_id(other, products)


@pytest.mark.parametrize("index", [0, 17, len(CONFIG_TEXT) - 1])
def test_run_id_changes_when_any_single_config_byte_changes(
    tmp_path: Path, config: Path, index: int
) -> None:
    changed = bytearray(CONFIG_TEXT)
    changed[index] ^= 0x01
    other = tmp_path / "changed.yaml"
    other.write_bytes(bytes(changed))
    assert provenance.run_id(config, []) != provenance.run_id(other, [])


def test_run_id_changes_when_one_product_checksum_changes(config: Path) -> None:
    before = provenance.run_id(config, [("dem", HEX_A), ("mask", HEX_B)])
    after = provenance.run_id(config, [("dem", HEX_A), ("mask", "c" * 64)])
    assert before != after


def test_a_null_checksum_differs_from_a_checksum(config: Path) -> None:
    assert provenance.run_id(config, [("dem", None)]) != provenance.run_id(config, [("dem", HEX_A)])


def test_run_id_changes_when_a_product_is_added(config: Path) -> None:
    assert provenance.run_id(config, [("dem", HEX_A)]) != provenance.run_id(
        config, [("dem", HEX_A), ("mask", None)]
    )


def test_run_id_does_not_change_when_products_are_reordered(config: Path) -> None:
    products: list[tuple[str, str | None]] = [("c", None), ("a", HEX_A), ("b", HEX_B)]
    assert provenance.run_id(config, products) == provenance.run_id(config, products[::-1])


@given(
    st.lists(
        st.tuples(st.text(min_size=1, max_size=6), st.sampled_from([None, HEX_A, HEX_B])),
        max_size=6,
        unique_by=lambda pair: pair[0],
    ),
    st.randoms(use_true_random=False),
)
def test_run_id_is_independent_of_product_order(
    products: list[tuple[str, str | None]], rng: random.Random
) -> None:
    shuffled = list(products)
    rng.shuffle(shuffled)
    path = Path(provenance.__file__).parents[3] / "config" / "run.yaml"
    assert provenance.run_id(path, products) == provenance.run_id(path, shuffled)


def test_two_calls_give_identical_strings(config: Path) -> None:
    products = [("dem", HEX_A), ("mask", None)]
    first = provenance.run_id(config, products)
    second = provenance.run_id(config, products)
    assert first == second
    assert type(first) is str


def test_run_id_accepts_product_records(config: Path) -> None:
    records = [product_record("dem", HEX_A), product_record("mask", None)]
    assert provenance.run_id(config, records) == provenance.run_id(
        config, [("dem", HEX_A), ("mask", None)]
    )


def test_run_id_refuses_a_duplicated_product_id(config: Path) -> None:
    with pytest.raises(ValueError, match="dem"):
        provenance.run_id(config, [("dem", HEX_A), ("dem", HEX_A)])
    with pytest.raises(ValueError, match="dem"):
        provenance.run_id(config, [("dem", HEX_A), ("dem", HEX_B)])


@pytest.mark.parametrize(
    "product",
    [("", HEX_A), ("dem", HEX_A.upper()), ("dem", HEX_A[:63]), ("dem", HEX_A + "\n"), ("dem", "")],
)
def test_run_id_refuses_an_empty_id_or_a_malformed_checksum(
    config: Path, product: tuple[str, str | None]
) -> None:
    with pytest.raises(ValueError):
        provenance.run_id(config, [product])


def test_run_id_reads_the_config_file_and_fails_when_it_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        provenance.run_id(tmp_path / "missing.yaml", [])


# --- config_version and file_hashes -------------------------------------------------------------


def test_config_version_is_the_sha256_of_the_bytes_as_read(config: Path) -> None:
    value = provenance.config_version(config)
    assert value == hashlib.sha256(CONFIG_TEXT).hexdigest()
    assert TypeAdapter(Sha256).validate_python(value) == value


def test_config_version_does_not_normalise_line_endings(tmp_path: Path, config: Path) -> None:
    crlf = tmp_path / "crlf.yaml"
    crlf.write_bytes(CONFIG_TEXT.replace(b"\n", b"\r\n"))
    assert provenance.config_version(crlf) == hashlib.sha256(crlf.read_bytes()).hexdigest()
    assert provenance.config_version(crlf) != provenance.config_version(config)


def test_file_hashes_equal_hashlib_values_in_path_order(tmp_path: Path) -> None:
    contents = {"b.bin": b"\x00\x01", "a/z.txt": b"z\n", "a/b.txt": b"", "c.json": b"{}\n"}
    paths = []
    for name, data in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        paths.append(path)
    hashes = provenance.file_hashes(paths)
    expected = sorted(
        (
            {"path": (tmp_path / name).as_posix(), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in contents.items()
        ),
        key=lambda entry: entry["path"],
    )
    assert hashes == expected
    assert [list(entry) for entry in hashes] == [["path", "sha256"]] * len(contents)


def test_file_hashes_keep_relative_paths_as_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "g0.json").write_bytes(b"[]\n")
    assert provenance.file_hashes(["outputs/g0.json"]) == [
        {"path": "outputs/g0.json", "sha256": hashlib.sha256(b"[]\n").hexdigest()}
    ]


def test_file_hashes_of_nothing_is_empty() -> None:
    assert provenance.file_hashes([]) == []


def test_file_hashes_refuse_a_path_given_twice(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(b"a")
    with pytest.raises(ValueError, match=r"a\.txt"):
        provenance.file_hashes([path, path])


def test_file_hashes_fail_on_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        provenance.file_hashes([tmp_path / "missing.bin"])


# --- command_line -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["merge"],
        ["attest-g0", "--mode", "B", "--operator", "Ada Lovelace"],
        ["--note", 'it\'s "quoted" $HOME `cmd` ; rm'],
        ["", "a\nb", "tab\there", "ünïcode"],
    ],
)
def test_command_line_round_trips_through_shlex_split(argv: list[str]) -> None:
    line = provenance.command_line("terraluna_pipeline.gates", argv)
    assert shlex.split(line) == ["python", "-m", "terraluna_pipeline.gates", *argv]
    assert line == shlex.join(["python", "-m", "terraluna_pipeline.gates", *argv])


@given(st.lists(st.text(alphabet=st.characters(exclude_characters="\x00")), max_size=5))
def test_command_line_round_trips_any_argv(argv: list[str]) -> None:
    line = provenance.command_line("terraluna_pipeline.export", argv)
    assert shlex.split(line) == ["python", "-m", "terraluna_pipeline.export", *argv]


# --- canonical JSON and determinism -------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        {"b": 1, "a": [None, True, 1.5, "é"]},
        [],
        {"config_sha256": HEX_A, "products": [["a", None], ["b", HEX_B]]},
        {"nested": {"z": {}, "a": []}},
    ],
)
def test_canonical_json_is_the_export_canonical_json(value: object) -> None:
    assert provenance.canonical_json(value) == export.canonical_json(value)


def test_canonical_json_refuses_nan() -> None:
    with pytest.raises(ValueError):
        provenance.canonical_json({"value": float("nan")})


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def test_provenance_uses_no_clock_and_no_randomness() -> None:
    imported = imported_modules(PROVENANCE_PY)
    forbidden = {"time", "datetime", "random", "secrets", "uuid"}
    assert not {name.split(".")[0] for name in imported} & forbidden, imported


def test_provenance_does_not_import_export_matching_or_search() -> None:
    imported = imported_modules(PROVENANCE_PY)
    for module in ("export", "matching", "search"):
        assert not any(name.split(".")[-1] == module for name in imported), imported
        assert f"terraluna_pipeline.{module}" not in imported, imported
