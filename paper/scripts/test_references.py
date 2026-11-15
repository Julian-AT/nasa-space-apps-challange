"""Tests for paper/scripts/references.py, the reference record and refs.bib (PRD 22.6 check 3)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.references import BIB_HEADER, Reference, bib_text, citations, load, main

VALID: dict[str, Any] = {
    "status": "verified",
    "unresolvedReason": None,
    "entryType": "article",
    "fields": {
        "author": "Doe, Jane",
        "title": "A {LOLA} study",
        "journal": "Icarus",
        "year": "2020",
        "doi": "10.1016/j.icarus.2020.000001",
    },
    "primaryUrl": "https://doi.org/10.1016/j.icarus.2020.000001",
    "sourceKind": "publisher",
    "metadataUrl": "https://api.crossref.org/works/10.1016/j.icarus.2020.000001",
    "searchQuery": "doe lola",
    "checked": "2026-11-15",
    "verifiedFields": ["author", "title", "journal", "year", "doi"],
    "evidence": "doi.org landing page; compared author, title, journal, year",
    "abstract": "We study the Moon.",
    "abstractSource": "https://api.crossref.org/works/10.1016/j.icarus.2020.000001",
    "abstractUnavailableReason": None,
    "supports": [{"claim": "The Moon is studied.", "location": "abstract"}],
}

ARXIV: dict[str, Any] = {
    **VALID,
    "entryType": "misc",
    "fields": {
        "author": "Doe, Jane",
        "title": "Embeddings",
        "year": "2025",
        "eprint": "2501.08584",
        "archiveprefix": "arXiv",
    },
    "primaryUrl": "https://arxiv.org/abs/2501.08584",
    "sourceKind": "arxiv",
    "metadataUrl": "https://arxiv.org/abs/2501.08584",
    "verifiedFields": ["author", "title", "year", "eprint", "archiveprefix"],
    "abstractSource": "https://arxiv.org/abs/2501.08584",
}


def write(tmp_path: Path, refs: dict[str, Any]) -> Path:
    path = tmp_path / "record.json"
    path.write_text(json.dumps({"method": "read at the source", "references": refs}))
    return path


def errors_of(tmp_path: Path, key: str, ref: dict[str, Any]) -> list[str]:
    return load(write(tmp_path, {key: ref}))[1]


def test_valid_records_pass(tmp_path: Path) -> None:
    assert errors_of(tmp_path, "doe2020", VALID) == []
    assert errors_of(tmp_path, "doe2025qaidam", ARXIV) == []


def mutated(base: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    ref = copy.deepcopy(base)
    target = ref
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    if value is KeyError:
        del target[parts[-1]]
    else:
        target[parts[-1]] = value
    return ref


@pytest.mark.parametrize(
    ("key", "base", "path", "value"),
    [
        ("Doe2020", VALID, "status", "verified"),
        ("doe2025", ARXIV, "abstract", None),
        ("doe2020", VALID, "verifiedFields", ["author", "title", "journal", "year"]),
        ("doe2020", VALID, "primaryUrl", "https://arxiv.org/abs/2501.08584"),
        ("doe2025", ARXIV, "fields.eprint", "2501.08584v2"),
        ("doe2020", VALID, "primaryUrl", "https://doi.org/10.1016/j.icarus.2020.000002"),
        ("doe2020", VALID, "metadataUrl", "https://typeset.io/papers/x"),
        ("doe2020", VALID, "fields.shorttitle", "x"),
        ("doe2020", VALID, "fields.title", "A {LOLA study"),
        ("doe2020", VALID, "supports", []),
        ("doe2020", VALID, "extra", "x"),
        ("doe2020", VALID, "abstractSource", KeyError),
    ],
)
def test_rule_violations_fail(
    tmp_path: Path, key: str, base: dict[str, Any], path: str, value: Any
) -> None:
    ref = mutated(base, path, value)
    assert errors_of(tmp_path, key, ref) != []


def test_unresolved_needs_a_reason(tmp_path: Path) -> None:
    ref = {**VALID, "status": "unresolved", "unresolvedReason": None}
    assert errors_of(tmp_path, "doe2020", ref) != []
    ref["unresolvedReason"] = "no allowed source shows the abstract"
    assert errors_of(tmp_path, "doe2020", ref) == []


def test_missing_record_file_fails(tmp_path: Path) -> None:
    assert load(tmp_path / "absent.json")[0] is None


def tex(tmp_path: Path, text: str) -> list[Path]:
    path = tmp_path / "section.tex"
    path.write_text(text)
    return [path]


def test_citations_read_every_form_and_skip_comments(tmp_path: Path) -> None:
    files = tex(tmp_path, "\\citet{a2020} and \\citep[see][p.~3]{b2021, c2022}\n% \\cite{d2023}\n")
    assert [key for key, _, _ in citations(files)] == ["a2020", "b2021", "c2022"]


def record_of(refs: dict[str, Any]) -> Any:
    from scripts.references import Record

    return Record.model_validate({"method": "m", "references": refs})


def test_bib_has_cited_keys_only_and_is_deterministic(tmp_path: Path) -> None:
    record = record_of({"doe2020": VALID, "doe2025": ARXIV})
    cited = citations(tex(tmp_path, "\\citep{doe2020}\n"))
    first, errors = bib_text(record, cited)
    second, _ = bib_text(record, cited)
    assert errors == []
    assert first == second
    assert first.startswith(BIB_HEADER + "\n\n@article{doe2020,\n  author = {Doe, Jane},\n")
    assert "doe2025" not in first
    assert first.endswith("}\n")


def test_citing_an_unresolved_or_missing_key_fails(tmp_path: Path) -> None:
    unresolved = {**VALID, "status": "unresolved", "unresolvedReason": "paywalled abstract"}
    record = record_of({"doe2020": unresolved})
    _, errors = bib_text(record, citations(tex(tmp_path, "\\cite{doe2020,roe2021}\n")))
    assert len(errors) == 2


def test_main_check_on_committed_record() -> None:
    assert main(["check"]) == 0


def test_reference_model_forbids_extra_fields() -> None:
    with pytest.raises(ValueError):
        Reference.model_validate({**VALID, "x": 1})
