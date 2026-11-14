"""Tests for paper/scripts/overlap_check.py, the abstract-overlap check (PRD 22.4, 22.6 check 4).

The abstracts below are written for these tests; they quote no real paper. Test inputs are
strings or files in tmp_path; no fixture .tex file is committed under paper/.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.overlap_check import (
    MIN_RUN,
    Overlap,
    RecordError,
    build_parser,
    load_abstracts,
    main,
    overlaps,
    tokens,
)

ABSTRACT = (
    "We measure the roughness of lunar and martian terrain at baselines from metres to "
    "kilometres with laser altimetry and stereo models, and we compare the slopes."
)
WORDS = ABSTRACT.replace(",", "").replace(".", "").split()
OTHER = (
    "Radar echoes from icy moons carry a circular polarisation ratio that grows with "
    "wavelength scale roughness near the surface."
)
OTHER_WORDS = OTHER.replace(".", "").split()


def found(text: str, abstracts: dict[str, str] | None = None) -> list[Overlap]:
    return overlaps("background.tex", text, abstracts or {"shepard2001": ABSTRACT})


def run_lengths(text: str) -> list[int]:
    return [len(overlap.words) for overlap in found(text)]


def test_minimum_run_is_twelve() -> None:
    assert MIN_RUN == 12


def test_test_abstract_has_more_than_twenty_words() -> None:
    assert len(WORDS) > 20


# --- Tokenising ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Self-supervised, 9--11 km.", ["self", "supervised", "9", "11", "km"]),
        ("ROUGHNESS Roughness roughness", ["roughness"] * 3),
        ("caf\u00e9 cafe\u0301 caf\\'e caf{\\'e}", ["cafe"] * 4),
        ("\ufb01eld", ["field"]),
        ("Stra\u00dfe", ["strasse"]),
        ("\u00d8resund \u0141\u00f3d\u017a", ["oresund", "lodz"]),
        ("a~b\nc", ["a", "b", "c"]),
        ("x\u00b2", ["x2"]),
    ],
)
def test_tokens(text: str, expected: list[str]) -> None:
    assert tokens(text) == expected


# --- Runs ---------------------------------------------------------------------------------------


def test_twelve_shared_words_fail_and_eleven_pass() -> None:
    assert run_lengths("Intro. " + " ".join(WORDS[:MIN_RUN]) + " end.") == [MIN_RUN]
    assert run_lengths("Intro. " + " ".join(WORDS[: MIN_RUN - 1]) + " end.") == []


def test_eleven_words_twice_in_a_row_with_a_break_pass() -> None:
    eleven = " ".join(WORDS[: MIN_RUN - 1])
    assert run_lengths(f"{eleven} however {eleven}") == []


def test_a_long_run_is_reported_once_with_its_full_length() -> None:
    assert run_lengths("Our text: " + " ".join(WORDS) + " Then more.") == [len(WORDS)]


def test_run_reports_file_line_key_and_words() -> None:
    text = "First line.\nSecond line, " + " ".join(WORDS[:MIN_RUN]) + "\n"
    [overlap] = found(text)
    assert (overlap.path, overlap.line, overlap.key) == ("background.tex", 2, "shepard2001")
    assert overlap.words == tuple(word.lower() for word in WORDS[:MIN_RUN])
    assert str(overlap).startswith("background.tex:2: overlap: ")
    assert "shepard2001" in str(overlap)
    assert " ".join(overlap.words) in str(overlap)


def test_runs_with_two_abstracts_are_reported_per_reference() -> None:
    twelve = " ".join(WORDS[:MIN_RUN])
    keys = [overlap.key for overlap in found(twelve, {"a": ABSTRACT, "b": ABSTRACT})]
    assert keys == ["a", "b"]


def twelve_with(change: str) -> str:
    """The first twelve abstract words, changed in one way that must not hide the run."""
    w = WORDS[:MIN_RUN]
    if change == "case":
        return " ".join(word.upper() for word in w)
    if change == "line break":
        return " ".join(w[:5]) + "\n" + " ".join(w[5:])
    if change == "emph":
        return " ".join(w[:3]) + " \\emph{" + " ".join(w[3:6]) + "} " + " ".join(w[6:])
    if change == "tie":
        return "~".join(w)
    if change == "comment line":
        return " ".join(w[:6]) + "\n% an inserted comment line\n" + " ".join(w[6:])
    if change == "trailing comment":
        return " ".join(w[:6]) + " % a note\n" + " ".join(w[6:])
    if change == "markup":
        return " ".join(w[:4]) + " {\\bf " + w[4] + "} \\textit{" + " ".join(w[5:]) + "}"
    if change == "comment before a citation":
        return " ".join(w[:6]) + " \\citep% see the note\n{a} " + " ".join(w[6:])
    if change == "comment lines before a citation":
        return " ".join(w[:6]) + " \\cite %\n% more\n {a,\n b} " + " ".join(w[6:])
    if change == "comment before a reference":
        return " ".join(w[:6]) + " (\\ref%\n{fig:map}) " + " ".join(w[6:])
    if change == "grouped word":
        return " ".join(w[:5]) + " \\emph{{" + w[5][:2] + "}{" + w[5][2:] + "}} " + " ".join(w[6:])
    if change == "adjacent groups":
        return " ".join(w[:5]) + " {" + w[5][:2] + "}{" + w[5][2:] + "} " + " ".join(w[6:])
    raise AssertionError(change)


@pytest.mark.parametrize(
    "change",
    [
        "case",
        "line break",
        "emph",
        "tie",
        "comment line",
        "trailing comment",
        "markup",
        "comment before a citation",
        "comment lines before a citation",
        "comment before a reference",
        "grouped word",
        "adjacent groups",
    ],
)
def test_changes_do_not_hide_a_run(change: str) -> None:
    assert run_lengths(twelve_with(change)) == [MIN_RUN]


def test_hyphenated_word_matches_two_words() -> None:
    abstract = "We train a self supervised encoder on windows of terrain from three bodies today."
    text = "We train a self-supervised encoder on windows of terrain from three bodies."
    assert len(found(text, {"key": abstract})) == 1


def test_latex_accent_matches_an_accented_letter() -> None:
    abstract = "The caf\u00e9 study maps roughness of lunar terrain at metre baselines for rovers."
    text = "The caf\\'e study maps roughness of lunar terrain at metre baselines for rovers."
    assert len(found(text, {"key": abstract})) == 1
    text = "The caf\\'{e} study maps roughness of lunar terrain at metre baselines for rovers."
    assert len(found(text, {"key": abstract})) == 1


def test_six_words_from_each_of_two_abstracts_pass() -> None:
    text = " ".join(WORDS[:6] + OTHER_WORDS[:6])
    assert found(text, {"a": ABSTRACT, "b": OTHER}) == []


def test_math_citations_and_tikz_are_not_prose() -> None:
    w = WORDS[:MIN_RUN]
    text = " ".join(w[:6]) + " $x$ \\citep{a} " + " ".join(w[6:])
    assert run_lengths(text) == [MIN_RUN]
    tikz = "\\begin{tikzpicture}\\node {" + " ".join(w) + "};\\end{tikzpicture}"
    assert run_lengths(tikz) == []


# --- Reference record ---------------------------------------------------------------------------


def write_record(root: Path, references: object) -> Path:
    path = root / "reference-verification.json"
    path.write_text(json.dumps({"references": references}), encoding="utf-8")
    return path


def test_null_abstracts_are_skipped_and_counted(tmp_path: Path) -> None:
    path = write_record(
        tmp_path,
        {"a": {"abstract": ABSTRACT}, "b": {"abstract": None}, "c": {"abstract": None}},
    )
    assert load_abstracts(path) == ({"a": ABSTRACT}, 2)


def test_empty_record_has_no_abstracts(tmp_path: Path) -> None:
    assert load_abstracts(write_record(tmp_path, {})) == ({}, 0)


def test_missing_record_file_fails(tmp_path: Path) -> None:
    with pytest.raises(RecordError):
        load_abstracts(tmp_path / "reference-verification.json")


@pytest.mark.parametrize(
    "content",
    [
        "",
        "{",
        "[]",
        '{"refs": {}}',
        '{"references": []}',
        '{"references": {"a": "text"}}',
        '{"references": {"a": {}}}',
        '{"references": {"a": {"abstract": 3}}}',
        '{"references": {"a": {"abstract": ""}}}',
        '{"references": {"a": {"abstract": "  "}}}',
        '{"references": {"a": {"abstract": null}, "a": {"abstract": null}}}',
    ],
)
def test_unparsable_or_malformed_record_fails(tmp_path: Path, content: str) -> None:
    path = tmp_path / "reference-verification.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RecordError):
        load_abstracts(path)


# --- Command line -------------------------------------------------------------------------------


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_main_fails_on_a_shared_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_record(tmp_path, {"shepard2001": {"abstract": ABSTRACT}, "b": {"abstract": None}})
    write(tmp_path, "main.tex", "Clean.\n")
    write(tmp_path, "background.tex", "Text.\n" + " ".join(WORDS[:MIN_RUN]) + "\n")
    assert main(["--root", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "background.tex:2: overlap: " in err
    assert "1 references without an abstract skipped" in err


def test_main_passes_without_a_shared_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_record(tmp_path, {"shepard2001": {"abstract": ABSTRACT}, "b": {"abstract": None}})
    write(tmp_path, "main.tex", " ".join(WORDS[: MIN_RUN - 1]) + "\n")
    write(tmp_path, "figures/styles.tex", " ".join(WORDS) + "\n")
    assert main(["--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 .tex files" in out
    assert "1 abstracts" in out
    assert "1 references without an abstract skipped" in out


def test_main_fails_without_the_record_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path, "main.tex", "Clean.\n")
    assert main(["--root", str(tmp_path)]) == 1
    assert "reference-verification.json" in capsys.readouterr().err


def test_main_fails_on_an_unparsable_record_file(tmp_path: Path) -> None:
    write(tmp_path, "main.tex", "Clean.\n")
    write(tmp_path, "reference-verification.json", "{not json")
    assert main(["--root", str(tmp_path)]) == 1


def test_main_fails_without_tex_files(tmp_path: Path) -> None:
    write_record(tmp_path, {})
    assert main(["--root", str(tmp_path)]) == 1


def test_main_fails_on_a_tex_syntax_error(tmp_path: Path) -> None:
    write_record(tmp_path, {})
    write(tmp_path, "main.tex", "Unclosed $math.\n")
    assert main(["--root", str(tmp_path)]) == 1


def test_no_option_changes_the_limit() -> None:
    options = {option for action in build_parser()._actions for option in action.option_strings}
    assert options == {"-h", "--help", "--root"}


def test_record_file_is_an_object_with_references() -> None:
    record = Path(__file__).resolve().parents[1] / "reference-verification.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert isinstance(data.get("references"), dict)


def test_manuscript_passes_the_overlap_check(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0, capsys.readouterr().err
