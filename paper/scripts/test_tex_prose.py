"""Tests for paper/scripts/tex_prose.py, the LaTeX-to-prose extraction (PRD 22.6, D36).

lint_prose.py and overlap_check.py both read the manuscript through this module. Test inputs are
strings or files in tmp_path; no fixture .tex file is committed under paper/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.tex_prose import TexSyntaxError, checked_files, extract, fold_latex, line_at, prose


def words(text: str, *, keep_tikz: bool = False) -> list[str]:
    return prose(text, keep_tikz=keep_tikz).split()


# --- Comments -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a % b c\nd", ["a", "d"]),
        ("% whole line\nd", ["d"]),
        ("50\\% of", ["50%", "of"]),
        ("a\\\\% b\nc", ["a", "c"]),
        ("a %% double\nb", ["a", "b"]),
        ("a\\%b % c", ["a%b"]),
    ],
)
def test_unescaped_percent_starts_a_comment(text: str, expected: list[str]) -> None:
    assert words(text) == expected


def test_comments_are_recorded_by_line_without_the_percent() -> None:
    source = extract("a\n% one\nb % two\n50\\% three\n")
    assert source.comments == {2: " one", 3: " two"}


def test_comment_inside_math_is_a_comment() -> None:
    source = extract("$a % note\n b$ c")
    assert source.prose.split() == ["c"]
    assert source.comments == {1: " note"}


# --- Math ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "math",
    [
        "$a-b$",
        "$$a -- b$$",
        "\\(a - b\\)",
        "\\[a --- b\\]",
        "\\begin{equation}a-b\\end{equation}",
        "\\begin{equation*}a-b\\end{equation*}",
        "\\begin{align}a &= b\\\\ c &= d\\end{align}",
        "\\begin{align*}a &= b\\end{align*}",
        "\\begin{gather}a\\end{gather}",
        "\\begin{gather*}a\\end{gather*}",
        "\\begin{multline}a\\end{multline}",
        "\\begin{multline*}a\\end{multline*}",
        "\\begin {equation}a\\end {equation}",
    ],
)
def test_math_is_removed(math: str) -> None:
    assert words(f"before {math} after") == ["before", "after"]


def test_escaped_dollar_is_text() -> None:
    assert words("costs \\$5 and $x$ more") == ["costs", "$5", "and", "more"]


def test_double_backslash_with_spacing_is_not_display_math() -> None:
    assert words("a\\\\[2pt] b") == ["a", "b"]


def test_math_environment_end_inside_a_comment_does_not_close_it() -> None:
    text = "\\begin{equation}\na % \\end{equation}\n\\end{equation}\nafter"
    assert words(text) == ["after"]


# --- Verbatim -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verbatim",
    [
        "\\verb|a -- b|",
        "\\verb+a?+",
        "\\verb*|a b|",
        "\\verb|%|",
        "\\begin{verbatim}\n% x -- y?\n\\end{verbatim}",
        "\\begin{verbatim*}\nx\n\\end{verbatim*}",
    ],
)
def test_verbatim_is_removed(verbatim: str) -> None:
    assert words(f"before {verbatim} after") == ["before", "after"]


def test_percent_inside_verb_is_not_a_comment() -> None:
    assert extract("\\verb|%| after").comments == {}


# --- Commands whose arguments are not prose ----------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "\\cite{shepard2001}",
        "\\citet{shepard2001}",
        "\\citep[see][p.~5]{shepard2001,rosenburg2011}",
        "\\citep*{shepard2001}",
        "\\Citet{shepard2001}",
        "\\citeauthor{shepard2001}",
        "\\citeyearpar{shepard2001}",
        "\\citealp{shepard2001}",
        "\\nocite{shepard2001}",
        "\\ref{fig:a--b}",
        "\\eqref{eq:x}",
        "\\label{sec:a-b}",
        "\\input{generated/numbers.tex}",
        "\\include{appendix}",
        "\\url{https://x.org/?q=1}",
        "\\url{https://x.org/a%20b~c#d}",
        "\\cite{a,\n b}",
    ],
)
def test_key_command_arguments_are_removed(command: str) -> None:
    assert words(f"before {command} after") == ["before", "after"]


def test_href_keeps_its_text_and_drops_its_url() -> None:
    assert words("see \\href{https://x.org/?a=b%20c}{the archive} now") == [
        "see",
        "the",
        "archive",
        "now",
    ]


def test_percent_inside_url_is_not_a_comment() -> None:
    source = extract("\\url{https://x.org/a%20b} after")
    assert source.comments == {}
    assert source.prose.split() == ["after"]


# --- TikZ ---------------------------------------------------------------------------------------

TIKZ = "\\begin{tikzpicture}\n\\draw (a) -- (b) node {seamless};\n\\end{tikzpicture}"


def test_tikz_code_is_removed() -> None:
    assert words(f"before\n{TIKZ}\nafter") == ["before", "after"]


def test_tikz_code_is_kept_as_text_on_request() -> None:
    kept = words(f"before\n{TIKZ}\nafter", keep_tikz=True)
    assert "seamless" in kept
    assert kept[0] == "before"
    assert kept[-1] == "after"


def test_nested_tikzpicture_is_removed_to_the_outer_end() -> None:
    text = "\\begin{tikzpicture}\\begin{tikzpicture}a\\end{tikzpicture} b\\end{tikzpicture} c"
    assert words(text) == ["c"]


# --- Formatting and markup ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\\emph{seamless} text", ["seamless", "text"]),
        ("\\textit{a} \\textbf{b} \\textsc{c}", ["a", "b", "c"]),
        ("{\\bf a} b", ["a", "b"]),
        ("\\emph{sea}mless", ["seamless"]),
        ("a~b", ["a", "b"]),
        ("\\section{Background}", ["Background"]),
        ("\\section*{Acknowledgements}", ["Acknowledgements"]),
        ("Recall is \\MoonRecall{} here", ["Recall", "is", "here"]),
        ("self\\-supervised", ["selfsupervised"]),
        ("a\\,b", ["a", "b"]),
        ("\\begin{itemize}\\item one\\end{itemize}", ["one"]),
        ("\\begin{tabular}{lcr}x & y\\end{tabular}", ["x", "&", "y"]),
        ("\\begin{figure}[t]\\caption{Map}\\end{figure}", ["Map"]),
        ("\\textemdash{} a", ["a"]),
    ],
)
def test_markup_is_removed_and_text_kept(text: str, expected: list[str]) -> None:
    assert words(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("caf\\'e", "cafe"),
        ("caf\\'{e}", "cafe"),
        ("caf{\\'e}", "cafe"),
        ("Ko\\v{c}i", "Koci"),
        ("Ko\\v ci", "Koci"),
        ("na\\\"ive", "naive"),
        ("Mart\\'{\\i}nez", "Martinez"),
        ("Gro\\ss e", "Grosse"),
        ("\\AA ngstr\\\"om", "Angstrom"),
    ],
)
def test_accent_commands_give_base_letters(text: str, expected: str) -> None:
    assert prose(text).strip() == expected


def test_fold_latex_folds_accents_and_drops_braces() -> None:
    assert fold_latex("caf{\\'e} na\\\"ive Ko\\v{c}i {\\o}l") == "cafe naive Koci ol"


# --- Line numbers -------------------------------------------------------------------------------

MIXED = (
    "Line one % comment\n"
    "$a\n= b$ and \\[\nx\n\\]\n"
    "\\begin{equation}\ny\n\\end{equation}\n"
    "\\cite{a,\nb} \\href{https://x.org/\n}{text\nmore}\n"
    "\\begin{verbatim}\nv\n\\end{verbatim}\n"
    f"{TIKZ}\n"
    "\\emph{a\nb} \\'{\ne} \\\\[\n2pt] \\begin{tabular}{l\nr}\\end{tabular}\n"
    "marker\n"
)


@pytest.mark.parametrize("keep_tikz", [False, True])
def test_code_and_prose_keep_every_line_break(keep_tikz: bool) -> None:
    source = extract(MIXED, keep_tikz=keep_tikz)
    assert source.code.count("\n") == MIXED.count("\n")
    assert source.prose.count("\n") == MIXED.count("\n")
    lines = source.prose.split("\n")
    assert lines.index("marker") == MIXED.split("\n").index("marker")


def test_code_keeps_source_offsets() -> None:
    source = extract(MIXED)
    assert len(source.code) == len(MIXED)
    assert source.code.index("marker") == MIXED.index("marker")


def test_line_at_counts_from_one() -> None:
    assert [line_at("a\nb\nc", offset) for offset in (0, 1, 2, 4)] == [1, 1, 2, 3]


# --- Syntax errors ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("ok\n$a", 2),
        ("ok\n\n$a\n\nb$", 3),
        ("\\(a", 1),
        ("a\n\\[b", 2),
        ("\\begin{equation}\na", 1),
        ("\\begin{verbatim}\na", 1),
        ("x \\verb|a", 1),
        ("\\begin{tikzpicture}\na", 1),
        ("\\cite{a", 1),
        ("\\url{a", 1),
    ],
)
def test_unclosed_constructs_raise_with_their_line(text: str, line: int) -> None:
    with pytest.raises(TexSyntaxError) as raised:
        extract(text)
    assert raised.value.line == line


# --- Checked files ------------------------------------------------------------------------------


def test_checked_files_are_every_tex_file_except_the_figure_styles(tmp_path: Path) -> None:
    for name in [
        "main.tex",
        "background.tex",
        "figures/styles.tex",
        "figures/system-overview.tex",
        "generated/numbers.tex",
        "notes.txt",
        "arxiv.sty",
    ]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("", encoding="utf-8")
    assert [path.relative_to(tmp_path).as_posix() for path in checked_files(tmp_path)] == [
        "background.tex",
        "figures/system-overview.tex",
        "generated/numbers.tex",
        "main.tex",
    ]
