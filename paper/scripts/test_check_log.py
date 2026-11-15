"""Tests for paper/scripts/check_log.py, the post-Tectonic log gate (PRD 22.6).

Tectonic 0.17.0 exits 0 on undefined citations and on BibTeX errors, so check_log.py is what
makes them block the build. The excerpts below follow lines that Tectonic 0.17.0 wrote to
main.log and main.blg on a scratch document; long lines wrap at 79 characters as in a real log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_log import Finding, check_blg, check_log, main

CLEAN_LOG = """\
This is XeTeX, Version 3.141592653-2.6-0.999996 (TeX Live 2024/dev) (preloaded format=latex)
LaTeX Font Info:    External font `lmex10' loaded for size
Package natbib Warning: Empty `thebibliography' environment on input line 8.
Overfull \\hbox (0.99pt too wide) in paragraph at lines 12--13
Overfull \\hbox (1.0pt too wide) in paragraph at lines 14--15
Package rerunfilecheck Info: File `main.out' has not changed.
(rerunfilecheck)             Checksum: 3B1D0E2A1F1B1C2D3E4F5A6B7C8D9E0F;0.
 )
Output written on main.xdv (11 pages, 123456 bytes).
"""

CLEAN_BLG = """\
This is BibTeX, Version 0.99d
Capacity: max_strings=35307, hash_size=35307, hash_prime=30011
The top-level auxiliary file: main.aux
The style file: plainnat.bst
Database file #1: refs.bib
"""


def rules(findings: list[Finding]) -> list[tuple[int, str]]:
    return [(finding.line, finding.rule) for finding in findings]


# --- main.log -----------------------------------------------------------------------------------


def test_clean_log_passes() -> None:
    assert check_log(CLEAN_LOG) == []


@pytest.mark.parametrize(
    ("excerpt", "rule"),
    [
        (
            "Package natbib Warning: Citation `shepard2001' on page 1 undefined on input line 5.",
            "undefined-citation",
        ),
        (
            "LaTeX Warning: Citation `shepard2001' on page 1 undefined on input line 5.",
            "undefined-citation",
        ),
        ("Package natbib Warning: There were undefined citations.", "undefined-citation"),
        (
            "LaTeX Warning: Reference `sec:data' on page 2 undefined on input line 9.",
            "undefined-reference",
        ),
        ("LaTeX Warning: There were undefined references.", "undefined-reference"),
        ("LaTeX Warning: Label `sec:data' multiply defined.", "multiply-defined-label"),
        ("LaTeX Warning: There were multiply-defined labels.", "multiply-defined-label"),
        (
            "LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right.",
            "labels-changed",
        ),
        (
            "Package natbib Warning: Citation(s) may have changed.",
            "labels-changed",
        ),
        (
            "Missing character: There is no ^^A (U+0001) in font [lmroman10-regular]:mapping=",
            "missing-character",
        ),
        ("Overfull \\hbox (72.26999pt too wide) detected at line 6", "overfull-hbox"),
        ("Overfull \\hbox (1.00001pt too wide) in paragraph at lines 3--4", "overfull-hbox"),
        ("! Undefined control sequence.", "tex-error"),
    ],
)
def test_each_log_pattern_fails(excerpt: str, rule: str) -> None:
    text = CLEAN_LOG + excerpt + "\n"
    line = len(CLEAN_LOG.splitlines()) + 1
    assert rules(check_log(text)) == [(line, rule)]


def test_citation_key_wrapped_at_79_characters_is_still_caught() -> None:
    text = (
        "Package natbib Warning: Citation `averyveryveryveryveryverylongcitationkeythatw\n"
        "rapsthelogline2026' on page 1 undefined on input line 5.\n"
    )
    assert rules(check_log(text)) == [(1, "undefined-citation")]


@pytest.mark.parametrize(
    ("width", "fails"), [("0.5", False), ("1", False), ("1.0", False), ("1.01", True), ("12", True)]
)
def test_overfull_hbox_fails_only_when_wider_than_1pt(width: str, fails: bool) -> None:
    text = f"Overfull \\hbox ({width}pt too wide) in paragraph at lines 3--4\n"
    assert rules(check_log(text)) == ([(1, "overfull-hbox")] if fails else [])


def test_every_finding_is_reported_with_its_line() -> None:
    text = (
        "LaTeX Warning: Reference `sec:data' on page 2 undefined on input line 9.\n"
        "Text.\n"
        "Overfull \\hbox (3.5pt too wide) in paragraph at lines 3--4\n"
    )
    findings = check_log(text, "main.log")
    assert rules(findings) == [(1, "undefined-reference"), (3, "overfull-hbox")]
    assert str(findings[1]) == (
        "main.log:3: overfull-hbox: Overfull \\hbox (3.5pt too wide) in paragraph at lines 3--4"
    )


# --- main.blg -----------------------------------------------------------------------------------


def test_clean_blg_passes() -> None:
    assert check_blg(CLEAN_BLG) == []


@pytest.mark.parametrize(
    ("excerpt", "rule"),
    [
        ('Warning--I didn\'t find a database entry for "shepard2001"', "bibtex-warning"),
        ("Warning--empty journal in shepard2001", "bibtex-warning"),
        ("(There was 1 warning)", "bibtex-warning"),
        ("(There were 3 warnings)", "bibtex-warning"),
        ("I found no \\citation commands---while reading file main.aux", "bibtex-error"),
        ("I couldn't open database file refs.bib", "bibtex-error"),
        ("---line 3 of file main.aux", "bibtex-error"),
        ("Repeated entry---line 7 of file refs.bib", "bibtex-error"),
        ("(There was 1 error message)", "bibtex-error"),
        ("(There were 2 error messages)", "bibtex-error"),
    ],
)
def test_each_blg_pattern_fails(excerpt: str, rule: str) -> None:
    text = CLEAN_BLG + excerpt + "\n"
    line = len(CLEAN_BLG.splitlines()) + 1
    assert rules(check_blg(text)) == [(line, rule)]


def test_uncited_bibliography_error_as_written_by_tectonic_fails() -> None:
    text = (
        "This is BibTeX, Version 0.99d\n"
        "The top-level auxiliary file: main.aux\n"
        "The style file: plainnat.bst\n"
        "I found no \\citation commands---while reading file main.aux\n"
        "Database file #1: refs.bib\n"
        "(There was 1 error message)\n"
    )
    assert rules(check_blg(text)) == [(4, "bibtex-error"), (6, "bibtex-error")]


# --- Command line -------------------------------------------------------------------------------


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_cli_passes_clean_logs(tmp_path: Path) -> None:
    log = write(tmp_path / "main.log", CLEAN_LOG)
    blg = write(tmp_path / "main.blg", CLEAN_BLG)
    assert main([str(log), "--blg", str(blg)]) == 0


def test_cli_skips_a_blg_that_does_not_exist(tmp_path: Path) -> None:
    log = write(tmp_path / "main.log", CLEAN_LOG)
    assert main([str(log), "--blg", str(tmp_path / "main.blg")]) == 0


def test_cli_fails_and_prints_every_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = write(tmp_path / "main.log", "LaTeX Warning: There were undefined references.\n")
    blg = write(tmp_path / "main.blg", "(There was 1 warning)\n")
    assert main([str(log), "--blg", str(blg)]) == 1
    err = capsys.readouterr().err
    assert f"{log}:1: undefined-reference: LaTeX Warning: There were undefined references." in err
    assert f"{blg}:1: bibtex-warning: (There was 1 warning)" in err


def test_cli_fails_when_the_log_is_missing(tmp_path: Path) -> None:
    assert main([str(tmp_path / "main.log")]) == 1
