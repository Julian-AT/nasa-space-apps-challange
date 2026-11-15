"""Tests for paper/scripts/tex_prose.py, the LaTeX-to-prose extraction (PRD 22.6, D36).

lint_prose.py and overlap_check.py both read the manuscript through this module. Test inputs are
strings or files in tmp_path; no fixture .tex file is committed under paper/.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.tex_prose import (
    CITE_COMMAND,
    KEY_COMMANDS,
    KEY_OPTIONS,
    STARRED_KEY_COMMANDS,
    TexSyntaxError,
    checked_files,
    extract,
    fold_latex,
    line_at,
    prose,
)


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


# LaTeX's \input looks past spaces for a brace. Without one, TeX's \input primitive reads the file
# name up to the first space or line break, and XeTeX lets double quotes hold spaces in it.
@pytest.mark.parametrize(
    "command",
    [
        "\\input generated/numbers.tex",
        "\\input generated/numbers",
        "\\input  generated/numbers.tex\n",
        "\\input\n  generated/numbers.tex",
        "\\input % the generated numbers\n generated/numbers.tex",
        '\\input "generated/number file.tex"',
        '\\input generated/"number file".tex',
    ],
)
def test_unbraced_input_file_name_is_removed(command: str) -> None:
    assert words(f"before {command} after") == ["before", "after"]


# A comment inside the name goes with its line break, and TeX reads on from the next line past its
# indent; a blank line there is \par, which ends the name.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a \\input generated/% the numbers\nnumbers.tex b", ["a", "b"]),
        ("a \\input generated/%\n   numbers.tex b", ["a", "b"]),
        ("a \\input generated/%\n%\nnumbers.tex b", ["a", "b"]),
        ('a \\input "generated/%\nnumber file.tex" b', ["a", "b"]),
        ("a \\input numbers.tex%\nb c", ["a", "c"]),
        ("a \\input numbers.tex%\n\nb", ["a", "b"]),
        ("a \\input numbers.tex%\n  \nb", ["a", "b"]),
        ("a \\input numbers.tex%", ["a"]),
    ],
)
def test_comment_inside_an_unbraced_input_file_name_continues_it(
    text: str, expected: list[str]
) -> None:
    source = extract(text)
    assert source.prose.split() == expected
    assert source.prose.count("\n") == text.count("\n")


def test_comment_inside_an_unbraced_input_file_name_is_recorded() -> None:
    assert extract("a \\input generated/% the numbers\nnumbers.tex b").comments == {
        1: " the numbers"
    }


@pytest.mark.parametrize("text", ['\\input "generated/numbers.tex', '\\input "a\nb"', "\\input}"])
def test_malformed_input_file_name_raises(text: str) -> None:
    with pytest.raises(TexSyntaxError) as raised:
        extract(f"ok\n{text}")
    assert raised.value.line == 2


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


# TeX skips the spaces after a control word, and LaTeX's \@ifstar looks for its * with
# \@ifnextchar, which skips space tokens. So a space, a comment or one line break may stand
# before the * of a starred command, and the keys after it are still not prose.
@pytest.mark.parametrize(
    "command",
    [
        "\\citep *{other}",
        "\\citet  *[see][p.~5]{other}",
        "\\citep% note\n  *{other}",
        "\\citeauthor\n*{other}",
        "\\ref *{fig:other}",
        "\\autoref %\n%\n *{fig:other}",
        "\\\\ *[2pt]",
        "\\\\\n*\n[2pt]",
        "\\\\ % note\n *[2pt]",
    ],
)
def test_a_star_after_a_gap_belongs_to_its_command(command: str) -> None:
    assert words(f"before {command} after") == ["before", "after"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("seam\\vspace *{2pt}less", ["seamless"]),
        ("seam\\vspace\n*{2pt}less", ["seamless"]),
        ("seam\\vspace % note\n  *{2pt}less", ["seamless"]),
    ],
)
def test_a_starred_unprinted_command_after_a_gap_joins_its_word(
    text: str, expected: list[str]
) -> None:
    assert words(text) == expected


def test_a_blank_line_before_a_star_is_a_paragraph() -> None:
    with pytest.raises(TexSyntaxError):
        extract("\\citep\n\n*{other}")
    assert words("a\\\\\n\n*[2pt] b") == ["a", "*[2pt]", "b"]


# A command without a starred form reads a * after it as its argument, as TeX reads any one
# token: seam\label *less sets the label * and prints seamless, and \eqref*{eq} prints a
# reference to the key * and then eq. natbib's \citeyear reads the * as its key (natbib.sty in
# bundle v33).
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("seam\\label *less", ["seamless"]),
        ("seam\\label*less", ["seamless"]),
        ("seam\\label\n*less", ["seamless"]),
        ("seam\\label % note\n  *less", ["seamless"]),
        ("lu\\nocite *nar", ["lunar"]),
        ("see \\label*{x} now", ["see", "x", "now"]),
        ("see \\eqref*{eq} now", ["see", "eq", "now"]),
        ("a \\citeyear *{b} c", ["a", "b", "c"]),
        ("a \\citenum*{b} c", ["a", "b", "c"]),
    ],
)
def test_a_star_after_a_command_without_a_starred_form_is_its_argument(
    text: str, expected: list[str]
) -> None:
    assert words(text) == expected


# An unbraced argument may be a control word, and TeX skips the spaces, comment and one line
# break after a control word wherever it stands: seam\label\empty less sets the label \empty and
# prints seamless. A control symbol such as \% skips nothing, and a blank line is \par.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("seam\\label\\empty less", ["seamless"]),
        ("seam\\label\\empty\nless", ["seamless"]),
        ("seam\\label\\empty % note\n  less", ["seamless"]),
        ("lu\\nocite\\empty nar", ["lunar"]),
        ("lu\\ref\\empty nar", ["lu", "nar"]),
        ("seam\\label\\% less", ["seam", "less"]),
        ("seam\\label\\empty\n\nless", ["seam", "less"]),
    ],
)
def test_the_gap_after_a_control_word_argument_is_not_printed(
    text: str, expected: list[str]
) -> None:
    assert words(text) == expected


def test_a_line_break_after_a_control_word_argument_keeps_the_line_count() -> None:
    source = extract("seam\\label\\empty\nless\nnext")
    assert source.prose.count("\n") == 2
    assert source.prose.split("\n")[0].strip() == "seamless"


def test_starred_key_commands_are_key_commands() -> None:
    assert all(
        name in KEY_COMMANDS or CITE_COMMAND.fullmatch(name) for name in STARRED_KEY_COMMANDS
    )
    assert {"label", "eqref", "url", "href", "nocite", "citeyear"}.isdisjoint(STARRED_KEY_COMMANDS)


# A command reads only the optional [..] arguments it defines, where it defines them, as the
# pinned bundle (v33) defines them. \label, \ref, \eqref, \nocite and natbib's \citenum read none,
# so a [ after one is its argument, as TeX reads any one token: \label[seamless]{} sets the label
# [ and prints seamless]. natbib's citations read at most two before the key, and a float reads
# one placement.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a \\label[seamless]{} b", ["a", "seamless]", "b"]),
        ("seam\\label[less]{} now", ["seamless]", "now"]),
        ("seam\\label [less]{} now", ["seamless]", "now"]),
        ("seam\\label\n[less]{} now", ["seamless]", "now"]),
        ("lu\\nocite[nar]{} now", ["lunar]", "now"]),
        ("see \\ref[x]{} now", ["see", "x]", "now"]),
        ("see \\eqref [x]{} now", ["see", "x]", "now"]),
        ("a \\citenum[x]{} b", ["a", "x]", "b"]),
        ("a \\citep[see][p.~3][x]{} b", ["a", "x]", "b"]),
        ("\\begin{figure}[t][x] y\\end{figure}", ["[x]", "y"]),
    ],
)
def test_a_bracket_where_a_command_reads_no_option_is_its_argument(
    text: str, expected: list[str]
) -> None:
    assert words(text) == expected


# The optional arguments that commands do read stay out of the prose (bundle v33): natbib's
# \NAT@citetp, latex.ltx's \@lbibitem, \@float and \minipage, graphics.sty's \Gin@ii, xcolor.sty's
# \definecolor, \color and \textcolor, hyperref's \href[options], multirow.sty's [vpos],
# [bigstruts] and [vmove], \newcommand's argument count and default, and wrapfig.sty's [lines]
# and [overhang].
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a \\citep[see][p.~3]{k} b", ["a", "b"]),
        ("a \\citet [see] [p.~3] {k} b", ["a", "b"]),
        ("a \\citeyear[x]{k} b", ["a", "b"]),
        ("\\bibitem[Shepard(2001)]{shepard2001} text", ["text"]),
        ("\\includegraphics[0,0][1,1]{f} text", ["text"]),
        ("\\definecolor[named]{c}{rgb}{1,0,0} text", ["text"]),
        ("seam\\color[rgb]{1,0,0}less", ["seamless"]),
        ("seam\\textcolor[rgb]{1,0,0}{less}", ["seamless"]),
        ("seam\\href[pdfnewwindow]{https://x.org/}{less}", ["seamless"]),
        ("\\multirow[t]{2}[1]{*}[2pt]{text}", ["text"]),
        ("\\newcommand{\\x}[1][a]{body}", ["body"]),
        ("\\begin{minipage}[t][2cm][b]{0.5\\linewidth}x\\end{minipage}", ["x"]),
        ("\\begin{wrapfigure}[10]{r}[0pt]{5cm}x\\end{wrapfigure}", ["x"]),
        ("\\begin{longtable}[c]{ll}x\\end{longtable}", ["x"]),
        ("\\begin{tabularx}{\\linewidth}[t]{lX}x\\end{tabularx}", ["x"]),
    ],
)
def test_the_options_a_command_reads_are_removed(text: str, expected: list[str]) -> None:
    assert words(text) == expected


def test_key_options_name_key_commands_with_one_count_per_key() -> None:
    assert all(len(KEY_OPTIONS[name]) == KEY_COMMANDS[name] for name in KEY_OPTIONS)
    assert {"label", "ref", "eqref", "nocite", "url", "input"}.isdisjoint(KEY_OPTIONS)


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
        "\\input\n\ngenerated/numbers.tex",
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
        ("{seam}{less}", ["seamless"]),
        ("\\emph{{seam}{less}}", ["seamless"]),
        ("\\emph{sea}{mless}", ["seamless"]),
        ("\\textbf{seam}{less} text", ["seamless", "text"]),
        ("a {b}{c}{d} e", ["a", "bcd", "e"]),
        ("{\\bf seam}{less}", ["seamless"]),
        ("\\bf{seam}{less}", ["seamless"]),
        ("\\small{seam}{less}", ["seamless"]),
        ("\\item{seam}{less}", ["seamless"]),
        ("\\textcolor{red}{seam}{less}", ["seamless"]),
        ("\\newterm{a}{b}", ["a", "b"]),
        ("\\newterm{a} {b}{c}", ["a", "b", "c"]),
        ("\\newterm{\\emph{a}{b}}{c}", ["ab", "c"]),
    ],
)
def test_adjacent_groups_join_unless_they_are_arguments_of_one_command(
    text: str, expected: list[str]
) -> None:
    assert words(text) == expected


@pytest.mark.parametrize(
    ("tikz", "expected"),
    [
        ("\\node {\\textcolor{red}{seam}{less}};", ["red", "seamless;"]),
        ("\\node {\\textcolor[rgb]{1,0,0}{seamless}};", ["[rgb]1,0,0", "seamless;"]),
        ("\\node {\\emph{{seam}{less}}};", ["seamless;"]),
        ("\\node {{seam}{less}};", ["seamless;"]),
    ],
)
def test_kept_tikz_groups_join_unless_they_are_arguments_of_one_command(
    tikz: str, expected: list[str]
) -> None:
    text = f"\\begin{{tikzpicture}}\n{tikz}\n\\end{{tikzpicture}}"
    assert words(text, keep_tikz=True) == expected


# --- What TeX skips inside a word --------------------------------------------------------------
# After a control word TeX skips spaces, comments and one line break; an accent skips them before
# its letter. A comment takes its line break and the indent of the next line with it. A blank line
# is \par and still breaks a word.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("seam\\emph {less}", ["seamless"]),
        ("seam\\emph\t {less} text", ["seamless", "text"]),
        ("seam\\emph\n{less}", ["seamless"]),
        ("seam\\emph\n   {less}", ["seamless"]),
        ("seam\\emph % note\n{less}", ["seamless"]),
        ("seam\\emph\n% note\n  {less}", ["seamless"]),
        ("lu\\textit {nar} and", ["lunar", "and"]),
        ("seam{\\bf less}", ["seamless"]),
        ("seam{\\itshape\nless}", ["seamless"]),
        ("seam{\\small less}", ["seamless"]),
        ("Ko\\v\nci", ["Koci"]),
        ("caf\\'\ne", ["cafe"]),
        ("caf\\' % note\n e", ["cafe"]),
        ("Gro\\ss\ne", ["Grosse"]),
    ],
)
def test_space_after_a_control_word_prints_nothing(text: str, expected: list[str]) -> None:
    assert words(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("seam{\\bf\n\nless}", ["seam", "less"]),
        ("seam\\emph%\n\n{less}", ["seam", "less"]),
        ("seam\\quad less", ["seam", "less"]),
        ("seam\\newline less", ["seam", "less"]),
        ("seam\\MoonRecall less", ["seam", "less"]),
        ("seam \\emph{less}", ["seam", "less"]),
    ],
)
def test_a_blank_line_and_commands_that_print_space_or_unknown_text_break_words(
    text: str, expected: list[str]
) -> None:
    assert words(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sea%\nmless", ["seamless"]),
        ("sea% note\n   mless", ["seamless"]),
        ("sea%\n%\nmless", ["seamless"]),
        ("sea %\nmless", ["sea", "mless"]),
        ("sea%\n\nmless", ["sea", "mless"]),
    ],
)
def test_a_comment_takes_its_line_break_with_it(text: str, expected: list[str]) -> None:
    assert words(text) == expected


# A key command that prints nothing, such as \label or the colour of \textcolor, joins the text on
# either side. One that prints a citation, a number, a URL, a file or a space breaks it.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("seam\\label{x}less", ["seamless"]),
        ("seam\\label % note\n{x}less", ["seamless"]),
        ("seam\\textcolor{red}{less}", ["seamless"]),
        ("seam\\href{https://x.org/}{less}", ["seamless"]),
        ("seam{\\color{red}less}", ["seamless"]),
        ("seam\\vspace*{2pt}less", ["seamless"]),
        ("seam\\nocite{x}less", ["seamless"]),
        ("seam\\label{x} less", ["seam", "less"]),
        ("seam\\cite{x}less", ["seam", "less"]),
        ("seam\\citep[p.~5]{x}less", ["seam", "less"]),
        ("seam\\ref{x}less", ["seam", "less"]),
        ("seam\\url{https://x.org/}less", ["seam", "less"]),
        ("seam\\input{x}less", ["seam", "less"]),
        ("seam\\includegraphics{a.pdf}less", ["seam", "less"]),
        ("seam\\hspace{1em}less", ["seam", "less"]),
    ],
)
def test_key_commands_that_print_nothing_join_words(text: str, expected: list[str]) -> None:
    assert words(text) == expected


# TeX skips spaces, comments and one line break before each undelimited argument, and the optional
# arguments before a text argument are settings. So the gap between the keys of \href or
# \textcolor and their text prints nothing, and seam\href{...} {less} reads seamless.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("seam\\href{https://x.org/} {less}", ["seamless"]),
        ("seam\\href{https://x.org/}\n  {less}", ["seamless"]),
        ("seam\\href{https://x.org/}% note\n{less}", ["seamless"]),
        ("seam\\href{https://x.org/}%\n%\n {less}", ["seamless"]),
        ("seam\\textcolor{red} {less}", ["seamless"]),
        ("seam\\textcolor{red}\n{less}", ["seamless"]),
        ("seam\\textcolor[rgb]{1,0,0} {less}", ["seamless"]),
        ("seam\\multicolumn{1}{c} {less}", ["seamless"]),
        ("seam\\multirow{2}{*}[1ex] {less}", ["seamless"]),
        ("\\newcommand{\\x}[1]{body}", ["body"]),
        ("\\newcommand{\\x}[1][a] {body}", ["body"]),
        ("seam\\href{https://x.org/}\n\n{less}", ["seam", "less"]),
        ("seam\\textcolor{red}less", ["seamless"]),
    ],
)
def test_space_before_a_text_argument_after_keys_prints_nothing(
    text: str, expected: list[str]
) -> None:
    source = extract(text)
    assert source.prose.split() == expected
    assert source.prose.count("\n") == text.count("\n")


def test_kept_tikz_comment_takes_its_line_break_with_it() -> None:
    text = "\\begin{tikzpicture}\n\\node {sea%\nmless};\n\\end{tikzpicture}"
    assert words(text, keep_tikz=True) == ["seamless;"]


def test_a_word_joined_across_a_line_break_is_written_on_its_first_line() -> None:
    text = "seam\\emph\n{less} more\nnext %\nlast sea%\nmless.\nend"
    source = extract(text)
    assert source.prose.count("\n") == text.count("\n")
    assert [line.split() for line in source.prose.split("\n")] == [
        ["seamless"],
        ["more"],
        ["next"],
        ["last", "seamless."],
        [],
        ["end"],
    ]


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


# An accent reads its letter as TeX reads an argument, and latex.ltx's \@text@composite takes the
# first token of the group and drops the rest. So spaces and one line break, with the indent of
# the next line, may stand before the letter or its brace, inside the braces and after \i or \ss.
# A blank line is \par and ends the accent.
SPLIT_ACCENTS = [
    ("caf\\'\ne", "cafe"),
    ("caf\\'\n   e", "cafe"),
    ("caf\\' \n {e}", "cafe"),
    ("caf\\'{\ne}", "cafe"),
    ("caf\\'\n{ e }", "cafe"),
    ("caf\\'{ e\n}s", "cafes"),
    ("Ko\\v\nci", "Koci"),
    ("Ko\\v{\nc}i", "Koci"),
    ('na\\"\\i ve', "naive"),
    ('na\\"\\i\nve', "naive"),
    ('na\\"\n\\i ve', "naive"),
    ('na\\"{\\i\n}ve', "naive"),
    ("Gro\\ss\ne", "Grosse"),
    ("Gro\\ss\n  e", "Grosse"),
    ("\\o\nl", "ol"),
]


@pytest.mark.parametrize(("text", "expected"), SPLIT_ACCENTS)
def test_fold_latex_reads_an_accent_across_one_line_break(text: str, expected: str) -> None:
    assert fold_latex(text) == expected


@pytest.mark.parametrize(("text", "expected"), SPLIT_ACCENTS)
def test_an_accent_across_one_line_break_keeps_the_word_and_the_lines(
    text: str, expected: str
) -> None:
    source = extract(text)
    assert source.prose.split() == [expected]
    assert source.prose.count("\n") == text.count("\n")
    assert source.prose_lines[source.prose.index(expected[0])] == 1


@pytest.mark.parametrize("text", ["caf\\'\n\ne", "caf\\'{\n\ne}", "Gro\\ss\n\ne"])
def test_a_blank_line_ends_an_accent(text: str) -> None:
    assert len(re.findall("[A-Za-z]+", fold_latex(text))) == 2
    assert len(prose(text).split()) == 2


def test_fold_latex_reads_a_carriage_return_as_a_line_break() -> None:
    assert fold_latex("caf\\'\r\ne na\\\"\r\\i ve") == "cafe naive"


# fold_latex reads an abstract from left to right, as pass 2 reads a .tex file, so a letter that
# one accent prints cannot hide the end of the control word before it.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bia\\l{}o\\l\\k{e}ka", "Bialoleka"),
        ("\\L\\'od\\'z", "Lodz"),
        ("lu\\L\\`a", "luLa"),
        ("Gro\\ss\\'e", "Grosse"),
    ],
)
def test_fold_latex_reads_from_left_to_right(text: str, expected: str) -> None:
    assert fold_latex(text) == expected
    assert prose(text).strip() == expected


# An accent with no letter, as in \'{}, prints nothing that ends a word, in either reading.
@pytest.mark.parametrize("text", ["caf\\'{}e", "caf\\' {}e", "Ko\\v{}ci"])
def test_an_accent_without_a_letter_prints_nothing(text: str) -> None:
    expected = re.sub("[^A-Za-z]", "", text.replace("\\v", ""))
    assert fold_latex(text) == expected
    assert prose(text).split() == [expected]


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


def test_prose_line_of_mixed_source() -> None:
    source = extract(MIXED)
    assert source.prose_line(source.prose.index("marker")) == MIXED.split("\n").index("marker") + 1


# A word that TeX joins across a skipped line break is written whole on its first line, so text
# after the break can sit on the line before in the prose. prose_line() gives the source line of
# each prose character, where the text starts.
@pytest.mark.parametrize(
    ("text", "word", "line"),
    [
        ("Text.%\nrobust", "robust", 2),
        ("Text-% note\nrobust", "robust", 2),
        ("Text.\\emph\n{robust} x", "robust", 2),
        ("Text.\\emph% note\n{robust} x", "robust", 2),
        ("(%\nrobust)", "robust", 2),
        ("Text.%\nrobust", "Text", 1),
        ("sea%\nmless", "seamless", 1),
        ("a\nb sea%\nmless.", "seamless", 2),
        ("a\nb sea%\nmless. c", "c", 3),
        ("seam\\emph\n{less} more", "more", 2),
    ],
)
def test_prose_line_is_the_source_line_where_the_text_starts(
    text: str, word: str, line: int
) -> None:
    source = extract(text)
    assert source.prose_line(source.prose.index(word)) == line


def test_prose_line_covers_every_prose_character() -> None:
    source = extract(MIXED, keep_tikz=True)
    lines = [source.prose_line(offset) for offset in range(len(source.prose))]
    assert lines == sorted(lines)
    assert lines[0] == 1
    assert lines[-1] == MIXED.count("\n")


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
