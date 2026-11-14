"""Tests for paper/scripts/tex_prose.py, the LaTeX-to-prose extraction (PRD 22.6, D36).

lint_prose.py and overlap_check.py both read the manuscript through this module. Test inputs are
strings or files in tmp_path; no fixture .tex file is committed under paper/.
"""

from __future__ import annotations

import re
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
        "\\url {https://x.org/}",
        "\\cite{a,\n b}",
    ],
)
def test_key_command_arguments_are_removed(command: str) -> None:
    assert words(f"before {command} after") == ["before", "after"]


# --- Comments and line breaks before an argument -----------------------------------------------
# TeX skips spaces, comments and single line breaks between a command and its argument, and stops
# at a blank line, which it reads as \par.


@pytest.mark.parametrize(
    "command",
    [
        "\\cite% note\n{shepard2001}",
        "\\cite %\n  % a second comment line\n {shepard2001}",
        "\\citep%\n[p.~5]%\n{shepard2001}",
        "\\citep[see]%\n%\n[p.~5]\n{shepard2001}",
        "\\ref%\n{fig:a--b}",
        "\\label % note\n{sec:a}",
        "\\href%\n{https://x.org/?a=b%20c}{}",
        "\\begin%\n{equation}a\\end%\n{equation}",
        "\\begin{tikzpicture}\\draw (a) -- (b);\\end % note\n{tikzpicture}",
        "\\\\%\n[2pt]",
        "\\\\*\n[2pt]",
    ],
)
def test_comments_before_an_argument_are_skipped(command: str) -> None:
    assert words(f"before {command} after") == ["before", "after"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\\begin%\n{itemize}\\item one\\end % x\n{itemize}", ["one"]),
        ("\\begin{tabular}%\n%\n{lcr}x & y\\end{tabular}", ["x", "&", "y"]),
        ("\\begin{tabular*}{\\linewidth}%\n%\n[t]{l}x\\end{tabular*}", ["x"]),
        ("\\begin{figure}%\n%\n[t]\\caption{Map}\\end{figure}", ["Map"]),
    ],
)
def test_comments_before_environment_arguments_are_skipped(text: str, expected: list[str]) -> None:
    assert words(text) == expected


def test_comments_before_an_argument_are_recorded() -> None:
    source = extract("\\cite% one\n% two\n{shepard2001} text")
    assert source.comments == {1: " one", 2: " two"}
    assert source.prose.split() == ["text"]


def test_math_environment_end_after_a_comment_closes_it() -> None:
    text = "\\begin{equation}x\\end%\n{equation} prose \\begin{equation}y\\end{equation} after"
    assert words(text) == ["prose", "after"]


@pytest.mark.parametrize(
    "text",
    [
        "\\cite\n\n{key}",
        "\\cite%\n\n{key}",
        "\\cite %\n \n{key}",
        "\\begin\n\n{itemize}",
        "\\end%\n\n{itemize}",
        "\\begin{tabular}%\n\n{l}",
    ],
)
def test_a_blank_line_before_an_argument_raises(text: str) -> None:
    with pytest.raises(TexSyntaxError) as raised:
        extract(f"ok\n{text}")
    assert raised.value.line == 2


@pytest.mark.parametrize("text", ["\\url% note\n{https://x.org/}", "\\url\n{https://x.org/}"])
def test_url_argument_must_start_on_its_line(text: str) -> None:
    # hyperref's \url makes % and the end of a line active before it reads its argument
    # (\hyper@normalise), so either one would become the URL.
    with pytest.raises(TexSyntaxError) as raised:
        extract(f"see\n{text}")
    assert raised.value.line == 2


@pytest.mark.parametrize("text", ["\\begin x", "\\end x", "a \\end", "\\begin{a-b}"])
def test_begin_or_end_without_an_environment_name_raises(text: str) -> None:
    with pytest.raises(TexSyntaxError):
        extract(text)


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
    assert "seamless;" in kept
    assert kept[0] == "before"
    assert kept[-1] == "after"


@pytest.mark.parametrize(
    "tikz",
    [
        "\\tikzset{every node/.style={label={seamless}}}",
        "\\node[label={[above]seamless}] {x};",
        "\\node {\\textcolor{seamless}{x}};",
        "\\node {$\\text{seamless}$};",
        "\\node {\\hspace{1em}\\ref{seamless}};",
        "\\pgfplotsset{title={seamless}}",
    ],
)
def test_kept_tikz_reads_every_argument_as_text(tikz: str) -> None:
    text = f"before\n\\begin{{tikzpicture}}\n{tikz}\n\\end{{tikzpicture}}\nafter"
    assert "seamless" in re.findall(r"[a-z]+", prose(text, keep_tikz=True))
    assert words(text) == ["before", "after"]


def test_kept_tikz_still_drops_and_records_its_comments() -> None:
    text = "\\begin{tikzpicture}% a seamless path\n\\draw (a) -- (b);\n\\end{tikzpicture}"
    source = extract(text, keep_tikz=True)
    assert "seamless" not in source.prose
    assert source.comments == {1: " a seamless path"}


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
        ("\\newterm{a}{b}", ["a", "b"]),
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
        ('na\\"ive', "naive"),
        ("Mart\\'{\\i}nez", "Martinez"),
        ("Gro\\ss e", "Grosse"),
        ('\\AA ngstr\\"om', "Angstrom"),
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
