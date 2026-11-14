r"""Turn a LaTeX source file into prose that keeps source line numbers (PRD 22.6, D36).

lint_prose.py and overlap_check.py read the manuscript through this module, so both see the same
text. extract() works in two passes.

1. Code. Comments (an unescaped % to the end of the line), \verb and verbatim environments, math
   ($...$, $$...$$, \(...\), \[...\] and the equation, align, gather and multline environments,
   starred or not), the arguments of key commands (the \cite family, \ref, \eqref, \label, \input,
   \include, \url, the URL of \href and a few more in KEY_COMMANDS), the layout arguments of
   environments such as tabular and figure, the length of a \\ line break and the code of
   tikzpicture environments are replaced by spaces; with keep_tikz only the comments of a
   tikzpicture are. Every other character stays where it was, so an offset in the code is an
   offset in the source. Between a command and its arguments the scanner skips what TeX skips:
   spaces, comments and single line breaks. A blank line, which TeX reads as \par, ends the
   search.
2. Prose. Commands, environment names and braces are removed from the code. Formatting commands
   such as \emph, \textit and \textbf and declarations such as \bf keep their text, a tie ~
   reads as a space, and accent commands give their base letter. Adjacent groups join as TeX
   prints them, so {seam}{less} reads seamless; only two arguments of one command, as in
   \textcolor{red}{x} in kept TikZ text, are separate words (argument_breaks). What TeX skips
   without printing a space is dropped (unprinted): the spaces, comments and single line break
   after a control word or before the letter of an accent, a comment with its line break and
   the indent of the next line, key commands that print nothing, such as \label, and the gap
   before the text argument that follows keys, as in \href{url} {text}. So seam\emph {less},
   seam{\bf less}, seam\label{x}less, seam\href{url} {less} and sea% with mless on the next line
   all read seamless.

The code keeps every character offset and the prose every line break of the source. A word that
TeX joins across a line break it skips is written whole on the line where the word starts, and
the line break follows the word, so line n of the prose holds the words that start on line n of
the source. An unclosed construct raises TexSyntaxError with the line where it opens, so a check
fails instead of reading the rest of the file wrongly.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from pathlib import Path

# figures/styles.tex holds the shared TikZ styles (PRD 22.5); it is style code, not prose.
STYLE_FILE = "figures/styles.tex"

MATH_ENVIRONMENTS = frozenset(
    name + star
    for name in ("equation", "align", "alignat", "flalign", "gather", "multline", "eqnarray")
    for star in ("", "*")
) | {"displaymath", "math"}
VERBATIM_ENVIRONMENTS = frozenset(
    {"verbatim", "verbatim*", "Verbatim", "Verbatim*", "lstlisting", "minted"}
)
TIKZ_ENVIRONMENT = "tikzpicture"

# Commands whose leading mandatory arguments are keys, labels, paths, URLs or settings rather than
# prose, with the number of such arguments. Optional [..] arguments before each are removed too.
# A \newcommand keeps its body, which is text wherever the macro is used.
KEY_COMMANDS: dict[str, int] = {
    "ref": 1,
    "eqref": 1,
    "pageref": 1,
    "autoref": 1,
    "cref": 1,
    "Cref": 1,
    "nameref": 1,
    "label": 1,
    "input": 1,
    "include": 1,
    "includeonly": 1,
    "includegraphics": 1,
    "documentclass": 1,
    "usepackage": 1,
    "RequirePackage": 1,
    "bibliography": 1,
    "bibliographystyle": 1,
    "bibitem": 1,
    "nocite": 1,
    "url": 1,
    "href": 1,
    "newcommand": 1,
    "renewcommand": 1,
    "providecommand": 1,
    "hypersetup": 1,
    "usetikzlibrary": 1,
    "tikzset": 1,
    "pgfplotsset": 1,
    "definecolor": 3,
    "color": 1,
    "textcolor": 1,
    "setlength": 2,
    "addtolength": 2,
    "vspace": 1,
    "hspace": 1,
    "graphicspath": 1,
    "multicolumn": 2,
    "multirow": 2,
}
# The \cite family: \cite, \citet, \citep, \citealp, \Citet, \citeauthor, \citeyearpar and so on.
CITE_COMMAND = re.compile(r"[Cc]ite[A-Za-z]*")
# Key commands that print nothing where they stand, with their keys: the text on either side prints
# as one word, so seam\label{x}less and seam\textcolor{red}{less} read seamless. The others print
# a citation, a number, a URL, a file, a figure or a space, which breaks a word.
UNPRINTED_KEY_COMMANDS = frozenset(
    {
        "label",
        "nocite",
        "href",
        "color",
        "textcolor",
        "vspace",
        "hypersetup",
        "newcommand",
        "renewcommand",
        "providecommand",
        "definecolor",
        "setlength",
        "addtolength",
        "tikzset",
        "pgfplotsset",
        "usetikzlibrary",
        "graphicspath",
        "multicolumn",
        "multirow",
        "documentclass",
        "usepackage",
        "RequirePackage",
        "includeonly",
        "bibliographystyle",
    }
)
# Commands whose first argument is read verbatim, as the url package does, so % and ~ are text.
VERBATIM_ARGUMENT = frozenset({"url", "href"})
# hyperref's \url makes % and the end of a line active before it reads its argument
# (\hyper@normalise), so either one there would become the URL. \href reads its { first.
SAME_LINE_ARGUMENT = frozenset({"url"})
# What ends a file name after \input when no brace follows it, outside double quotes. TeX ends it
# at a space or a line break; a comment, a brace or a control sequence ends it here too.
FILE_NAME_END = frozenset(" \t\n%{}\\")

# Environments whose own arguments, after \begin{name}, are layout rather than prose.
ENVIRONMENT_ARGUMENTS: dict[str, int] = {
    "tabular": 1,
    "tabular*": 2,
    "tabularx": 2,
    "tabulary": 2,
    "array": 1,
    "longtable": 1,
    "minipage": 1,
    "multicols": 1,
    "wrapfigure": 2,
    "thebibliography": 1,
}
FLOAT_ENVIRONMENTS = frozenset({"figure", "figure*", "table", "table*"})

# Formatting commands that print their argument as it is.
TEXT_COMMANDS = frozenset(
    {
        "emph",
        "textit",
        "textbf",
        "textsc",
        "textrm",
        "textsf",
        "texttt",
        "textsl",
        "textup",
        "textmd",
        "textnormal",
        "underline",
        "mbox",
        "text",
    }
)

# Font and size declarations and other commands that print nothing. A word they stand in prints
# as one word, so seam{\bf less} reads seamless.
DECLARATIONS = frozenset(
    {
        "bf",
        "it",
        "em",
        "sc",
        "rm",
        "sf",
        "tt",
        "sl",
        "bfseries",
        "mdseries",
        "itshape",
        "slshape",
        "scshape",
        "upshape",
        "normalfont",
        "rmfamily",
        "sffamily",
        "ttfamily",
        "tiny",
        "scriptsize",
        "footnotesize",
        "small",
        "normalsize",
        "large",
        "Large",
        "LARGE",
        "huge",
        "Huge",
        "noindent",
        "centering",
        "raggedright",
        "raggedleft",
        "relax",
    }
)
# Commands that take no argument. A group after one is ordinary text, so \bf{seam}{less} prints
# seamless, as {seam}{less} does.
NO_ARGUMENT_COMMANDS = DECLARATIONS | frozenset(
    {
        "item",
        "indent",
        "par",
        "newline",
        "quad",
        "qquad",
        "maketitle",
        "appendix",
        "clearpage",
        "newpage",
    }
)
# Key commands whose key arguments are followed by text arguments, with the number of those.
TEXT_ARGUMENTS_AFTER_KEYS: dict[str, int] = {
    "textcolor": 1,
    "href": 1,
    "newcommand": 1,
    "renewcommand": 1,
    "providecommand": 1,
    "multicolumn": 1,
    "multirow": 1,
}
# Of those, the commands that take optional settings before a text argument: the argument count
# and default of \newcommand{\x}[1][a]{...} and the [vmove] of \multirow. The others read a [ there
# as their text, as \href and \textcolor do.
OPTIONS_BEFORE_TEXT = frozenset({"newcommand", "renewcommand", "providecommand", "multirow"})

# The one-letter accent commands, \v{c} and the like, and the accent control symbols, \'e and the
# like.
ACCENT_LETTERS = "vuHckrdbtG"
ACCENT_COMMANDS = frozenset(ACCENT_LETTERS)
ACCENT_SYMBOLS = frozenset("'`^\"~=.")
# Commands that print no text of their own, so no word break: formatting commands print their
# argument, declarations nothing and accents their letter.
PRINTS_NO_TEXT = TEXT_COMMANDS | DECLARATIONS | ACCENT_COMMANDS
# A LaTeX accent command and the letter it accents: \'e, \'{e}, \"{\i}, \v{c} or \v c.
ACCENT = re.compile(
    r"\\(?:['`^\"~=.]|[" + ACCENT_LETTERS + r"](?![A-Za-z]))[ \t]*"
    r"(?:\{[ \t]*(?:\\([ij])(?![A-Za-z])[ \t]*|([A-Za-z]))[ \t]*\}|\\([ij])(?![A-Za-z])|([A-Za-z]))"
)
# Special letters. TeX drops the spaces after a control word, so \ss e prints as one word.
SPECIAL_LETTER = re.compile(r"\\(ae|AE|oe|OE|aa|AA|ss|SS|i|j|o|O|l|L)(?![A-Za-z])[ \t]*")
SPECIAL_LETTERS = {
    "ae": "ae",
    "AE": "AE",
    "oe": "oe",
    "OE": "OE",
    "aa": "a",
    "AA": "A",
    "ss": "ss",
    "SS": "SS",
    "i": "i",
    "j": "j",
    "o": "o",
    "O": "O",
    "l": "l",
    "L": "L",
}

LETTERS = re.compile(r"[A-Za-z]+")
# A control sequence; group 1 is the name of a control word. An escape pair such as \\ is read
# whole, so the letters after it are text.
CONTROL_SEQUENCE = re.compile(r"\\(?:([A-Za-z]+)|.)", re.DOTALL)
ENVIRONMENT_PATTERN = r"[A-Za-z@]+\*?"
ENVIRONMENT_BRACE = re.compile(r"\{[ \t]*(" + ENVIRONMENT_PATTERN + r")[ \t]*\}")
BEGIN_OR_END = re.compile(r"\\(begin|end)(?![A-Za-z])")
BLANK_LINE = re.compile(r"\n[ \t]*\n")


class TexSyntaxError(ValueError):
    """An unclosed or malformed construct, with the line where it opens."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.message = message


@dataclass(frozen=True)
class Source:
    """The two passes over one file, and its comments by line."""

    code: str
    prose: str
    comments: dict[int, str]


def line_at(text: str, offset: int) -> int:
    """The 1-based line of text[offset]."""
    return text.count("\n", 0, offset) + 1


def extract(text: str, *, keep_tikz: bool = False) -> Source:
    r"""The code and prose of a LaTeX source.

    With keep_tikz the raw text of tikzpicture environments stays in the code, all but its
    comments: node labels, options, style names, math and the arguments of commands such as
    \tikzset reach the prose, and pass 2 removes only command names and braces there.
    lint_prose.py runs its word rules on that version.
    """
    scanner = _CodeScanner(text, keep_tikz)
    code = scanner.run()
    breaks = scanner.argument_breaks(code)
    return Source(code, _prose(code, breaks, scanner.unprinted(code)), scanner.comments)


def prose(text: str, *, keep_tikz: bool = False) -> str:
    return extract(text, keep_tikz=keep_tikz).prose


def fold_latex(text: str) -> str:
    r"""Fold LaTeX accent commands and special letters to base letters and drop braces.

    TeX prints no braces, so caf{\'e} becomes cafe. overlap_check.py applies this to abstracts,
    which may carry LaTeX accents; the prose of a .tex file is already folded.
    """
    text = ACCENT.sub(lambda match: next(group for group in match.groups() if group), text)
    text = SPECIAL_LETTER.sub(lambda match: SPECIAL_LETTERS[match[1]], text)
    return text.replace("{", "").replace("}", "")


def argument_count(name: str) -> int | None:
    """The number of mandatory arguments of a command, or None if its definition is not known."""
    if name in TEXT_COMMANDS or name in ("begin", "end") or name in ACCENT_COMMANDS:
        return 1
    if name in NO_ARGUMENT_COMMANDS or name in SPECIAL_LETTERS:
        return 0
    if CITE_COMMAND.fullmatch(name):
        return 1
    if name in KEY_COMMANDS:
        return KEY_COMMANDS[name] + TEXT_ARGUMENTS_AFTER_KEYS.get(name, 0)
    return None


def environment_boundary(names: str) -> re.Pattern[str]:
    r"""\begin{name} or \end{name} in the code that extract() returns, for a regex of names.

    Group 1 is begin or end and group 2 the name. Pass 1 has checked that only spaces, comments
    and single line breaks come between the command and its brace (kept TikZ text it leaves as it
    is), and has blanked the comments, so any white space may stand there in the code.
    """
    return re.compile(r"\\(begin|end)\s*\{[ \t]*(" + names + r")[ \t]*\}")


CODE_ENVIRONMENT = environment_boundary(ENVIRONMENT_PATTERN)


def checked_files(root: Path) -> list[Path]:
    """Every .tex file under root, sorted by path, except the TikZ style file."""
    files = (path for path in root.rglob("*.tex") if path.is_file())
    return sorted(
        (path for path in files if path.relative_to(root).as_posix() != STYLE_FILE),
        key=lambda path: path.relative_to(root).as_posix(),
    )


# --- Pass 1: code -------------------------------------------------------------------------------


class _CodeScanner:
    """Blank the non-prose parts of a source, keeping its length and line breaks."""

    def __init__(self, text: str, keep_tikz: bool) -> None:
        self.text = text
        self.keep_tikz = keep_tikz
        self.out = list(text)
        self.comments: dict[int, str] = {}
        # What TeX drops without printing a space: each comment with its line break and the indent
        # of the next line, and each key command in UNPRINTED_KEY_COMMANDS with its keys.
        self.unprinted_offsets: set[int] = set()
        self.line_starts: list[int] | None = None

    def line(self, offset: int) -> int:
        if self.line_starts is None:
            self.line_starts = [0] + [match.end() for match in re.finditer("\n", self.text)]
        return bisect.bisect_right(self.line_starts, offset)

    def error(self, offset: int, message: str) -> TexSyntaxError:
        return TexSyntaxError(self.line(offset), message)

    def blank(self, start: int, end: int) -> None:
        for index in range(start, end):
            if self.out[index] != "\n":
                self.out[index] = " "

    def run(self) -> str:
        text = self.text
        i = 0
        while i < len(text):
            char = text[i]
            if char == "%":
                end = self.comment(i)
                self.blank(i, end)
                i = end
            elif char == "$":
                end = self.dollar_math_end(i)
                self.blank(i, end)
                i = end
            elif char == "\\":
                i = self.command(i)
            else:
                i += 1
        return "".join(self.out)

    def comment(self, start: int) -> int:
        """Record the comment that starts at text[start] == '%' and return the end of its line."""
        text = self.text
        end = text.find("\n", start)
        end = len(text) if end == -1 else end
        self.comments.setdefault(self.line(start), text[start + 1 : end])
        dropped_end = end if end == len(text) else _blanks_end(text, end + 1)
        self.unprinted_offsets.update(range(start, dropped_end))
        return end

    def skip(self, i: int) -> int:
        """Index past the token at text[i] inside a code region: an escape pair or a comment."""
        if self.text[i] == "\\":
            return i + 2
        if self.text[i] == "%":
            return self.comment(i)
        return i + 1

    def dollar_math_end(self, start: int) -> int:
        text = self.text
        display = text.startswith("$$", start)
        delimiter = "$$" if display else "$"
        i = start + len(delimiter)
        while i < len(text):
            if text.startswith(delimiter, i):
                end = i + len(delimiter)
                if not display and BLANK_LINE.search(text, start, end):
                    break
                return end
            i = self.skip(i)
        raise self.error(start, f"unclosed {delimiter} math")

    def bracket_math_end(self, start: int, closing: str) -> int:
        """End of \\( ... \\) or \\[ ... \\] that opens at text[start]."""
        text = self.text
        i = start + 2
        while i < len(text):
            if text.startswith(closing, i):
                end = i + 2
                if closing == "\\)" and BLANK_LINE.search(text, start, end):
                    break
                return end
            i = self.skip(i)
        raise self.error(start, f"unclosed {text[start : start + 2]} math")

    def environment_end(self, start: int, name: str, *, verbatim: bool, nested: bool) -> int:
        """Index past the \\end{name} that closes the environment opened at text[start]."""
        text = self.text
        if verbatim:
            match = re.compile(r"\\end[ \t]*\{" + re.escape(name) + r"\}").search(text, start + 1)
            if match is None:
                raise self.error(start, f"unclosed {{{name}}} environment")
            return match.end()
        brace = re.compile(r"\{[ \t]*" + re.escape(name) + r"[ \t]*\}")
        depth = 0
        i = start
        while i < len(text):
            boundary = BEGIN_OR_END.match(text, i)
            if boundary is None:
                i = self.skip(i)
                continue
            found = brace.match(text, self.space_end(boundary.end()))
            if found is None:
                i = boundary.end()
                continue
            if boundary[1] == "begin":
                if nested or depth == 0:
                    depth += 1
            else:
                depth -= 1
                if depth == 0:
                    return found.end()
            i = found.end()
        raise self.error(start, f"unclosed {{{name}}} environment")

    def command(self, start: int) -> int:
        """Handle the control sequence at text[start] == '\\' and return where scanning goes on."""
        text = self.text
        if start + 1 >= len(text):
            return len(text)
        following = text[start + 1]
        if following in "([":
            end = self.bracket_math_end(start, "\\)" if following == "(" else "\\]")
            self.blank(start, end)
            return end
        if following == "\\":
            end = self.line_break_end(start)
            self.blank(start + 2, end)
            return end
        name_match = LETTERS.match(text, start + 1)
        if name_match is None:
            return start + 2
        name = name_match[0]
        i = name_match.end()
        if name in ("begin", "end"):
            return self.environment(start, i, name)
        if name == "verb":
            end = self.verb_end(start, i)
        elif name in KEY_COMMANDS or CITE_COMMAND.fullmatch(name):
            end = self.key_arguments_end(start, i, name)
            if name in UNPRINTED_KEY_COMMANDS:
                self.unprinted_offsets.update(range(start, end))
            self.drop_text_argument_gaps(start, end, name)
        else:
            return i
        self.blank(start, end)
        return end

    def line_break_end(self, start: int) -> int:
        """Index past the \\\\ at text[start] with its optional * and [length]."""
        i = start + 2
        if self.text.startswith("*", i):
            i += 1
        j = self.space_end(i)
        return self.bracket_end(j) if self.text.startswith("[", j) else i

    def environment(self, start: int, i: int, command: str) -> int:
        """Handle the \\begin or \\end at text[start], whose command name ends at text[i].

        The environment name stays in the code, where lint_prose.py looks for lists and tables.
        """
        brace = ENVIRONMENT_BRACE.match(self.text, self.space_end(i))
        if brace is None:
            raise self.error(start, f"\\{command} without an environment name")
        self.blank(i, brace.start())
        if command == "end":
            return brace.end()
        name = brace[1]
        if name == TIKZ_ENVIRONMENT and self.keep_tikz:
            kept_end = self.environment_end(start, name, verbatim=False, nested=True)
            self.blank_comments(brace.end(), kept_end)
            return kept_end
        removed_end = self.begin_end(start, name)
        if removed_end is not None:
            self.blank(start, removed_end)
            return removed_end
        arguments_end = self.environment_arguments_end(start, brace.end(), name)
        self.blank(brace.end(), arguments_end)
        return arguments_end

    def environment_arguments_end(self, start: int, i: int, name: str) -> int:
        """Index past the layout arguments after \\begin{name}, such as table columns."""
        if name in FLOAT_ENVIRONMENTS:
            return self.optional_arguments_end(i)
        for _ in range(ENVIRONMENT_ARGUMENTS.get(name, 0)):
            i = self.optional_arguments_end(i)
            i = self.mandatory_argument_end(start, i, f"begin{{{name}}}", verbatim=False)
        return i

    def verb_end(self, start: int, i: int) -> int:
        text = self.text
        if text.startswith("*", i):
            i += 1
        if i >= len(text) or text[i] == "\n":
            raise self.error(start, "unclosed \\verb")
        end = text.find(text[i], i + 1)
        if end == -1 or "\n" in text[i:end]:
            raise self.error(start, "unclosed \\verb")
        return end + 1

    def begin_end(self, start: int, name: str) -> int | None:
        """End of a math, verbatim or TikZ environment; None for any other one."""
        if name in VERBATIM_ENVIRONMENTS:
            return self.environment_end(start, name, verbatim=True, nested=False)
        if name in MATH_ENVIRONMENTS:
            return self.environment_end(start, name, verbatim=False, nested=False)
        if name == TIKZ_ENVIRONMENT:
            return self.environment_end(start, name, verbatim=False, nested=True)
        return None

    def blank_comments(self, start: int, end: int) -> None:
        """Blank and record the comments in text[start:end] and keep everything else."""
        i = start
        while i < end:
            if self.text[i] == "%":
                comment_end = self.comment(i)
                self.blank(i, comment_end)
                i = comment_end
            else:
                i = self.skip(i)

    def key_arguments_end(self, start: int, i: int, name: str) -> int:
        if name == "input":
            return self.input_argument_end(start, i)
        count = 1 if CITE_COMMAND.fullmatch(name) else KEY_COMMANDS[name]
        if self.text.startswith("*", i):
            i += 1
        for index in range(count):
            if index == 0 and name in SAME_LINE_ARGUMENT:
                i = self.same_line_argument_start(start, i, name)
            else:
                i = self.optional_arguments_end(i)
            i = self.mandatory_argument_end(
                start, i, name, verbatim=index == 0 and name in VERBATIM_ARGUMENT
            )
        return i

    def drop_text_argument_gaps(self, start: int, i: int, name: str) -> None:
        r"""Blank and drop the gap before each text argument after the keys, which end at text[i].

        TeX skips spaces, comments and one line break before an undelimited argument, and the
        options of OPTIONS_BEFORE_TEXT are settings, so seam\href{url} {less} and
        seam\textcolor{red}, a line break and {less} print seamless. A blank line, which TeX reads
        as \par, or a } keeps the gap. The text arguments themselves stay for the main scan.
        """
        text = self.text
        for _ in range(TEXT_ARGUMENTS_AFTER_KEYS.get(name, 0)):
            j = self.optional_arguments_end(i) if name in OPTIONS_BEFORE_TEXT else i
            j = self.space_end(j)
            if j >= len(text) or text[j] in "\n}":
                return
            self.blank(i, j)
            self.unprinted_offsets.update(range(i, j))
            i = self.mandatory_argument_end(start, j, name, verbatim=False)

    def input_argument_end(self, start: int, i: int) -> int:
        """Index past the file name after the \\input at text[start], whose name ends at text[i].

        LaTeX's \\input looks past spaces for a brace (\\@ifnextchar) and reads a {group} as the
        name. Without one, TeX's \\input primitive reads the name up to the first space or line
        break; XeTeX lets double quotes hold spaces in it, as in \\input "a b.tex".
        """
        text = self.text
        i = self.space_end(i)
        if text.startswith("{", i):
            return self.group_end(i, verbatim=False)
        end = i
        quoted = False
        while end < len(text) and (quoted or text[end] not in FILE_NAME_END):
            if text[end] == "\n":
                raise self.error(start, 'unclosed " in the file name of \\input')
            if text[end] == '"':
                quoted = not quoted
            end += 1
        if quoted:
            raise self.error(start, 'unclosed " in the file name of \\input')
        if end == i:
            raise self.error(start, "missing argument of \\input")
        return end

    def same_line_argument_start(self, start: int, i: int, name: str) -> int:
        i = _blanks_end(self.text, i)
        if self.text.startswith(("%", "\n"), i):
            raise self.error(
                start, f"the argument of \\{name} must start on its line, with no comment before it"
            )
        return i

    def space_end(self, i: int) -> int:
        """Index past what TeX skips before an argument: spaces, comments and single line breaks.

        The comments are recorded. A blank line, which TeX reads as \\par, ends the space.
        """
        text = self.text
        i = _blanks_end(text, i)
        at_line_start = False
        while i < len(text):
            if text[i] == "%":
                i = self.comment(i)
            elif text[i] != "\n" or at_line_start:
                return i
            if i < len(text):
                i = _blanks_end(text, i + 1)
            at_line_start = True
        return i

    def argument_breaks(self, code: str) -> frozenset[int]:
        r"""Offsets in code of each } that ends an argument of a command whose next argument
        follows it directly. Pass 2 reads each one as a word break.

        TeX prints adjacent groups as adjacent text, so {seam}{less} and \emph{sea}{mless} print
        seamless. Two arguments of one command are separate text, as in \textcolor{red}{x} in
        kept TikZ text or \newterm{a}{b}. A command takes as many arguments as argument_count()
        gives; one whose definition is not known here takes every group that follows it. Before
        each argument the scan skips what TeX skips there (space_end, read in the source, where
        a comment line is not a blank line) and optional [..] arguments.
        """
        breaks: set[int] = set()
        for match in CONTROL_SEQUENCE.finditer(code):
            if match[1] is None:
                continue
            limit = argument_count(match[1])
            i = match.end()
            if code.startswith("*", i):
                i += 1
            count = 0
            while limit is None or count < limit:
                i = self.space_end(i)
                while code.startswith("[", i):
                    i = self.space_end(_code_bracket_end(code, i))
                if not code.startswith("{", i):
                    break
                i = _code_group_end(code, i)
                count += 1
                if code.startswith("{", i) and (limit is None or count < limit):
                    breaks.add(i - 1)
        return frozenset(breaks)

    def unprinted(self, code: str) -> frozenset[int]:
        r"""Offsets in code of what TeX skips without printing a space. Pass 2 drops them.

        After a control word, and before the letter of an accent such as \', TeX skips spaces,
        comments and one line break (space_end, read in the source, where a comment line is not
        a blank line). A comment goes with its line break and the indent of the next line, and a
        key command in UNPRINTED_KEY_COMMANDS prints nothing. So seam\emph {less}, seam{\bf less},
        seam\label{x}less and caf\' with e on the next line print one word. A blank line is \par
        and stays.
        """
        offsets: set[int] = set()
        for match in CONTROL_SEQUENCE.finditer(code):
            if match[1] is not None or match[0][1] in ACCENT_SYMBOLS:
                offsets.update(range(match.end(), self.space_end(match.end())))
        return frozenset(offsets | self.unprinted_offsets)

    def optional_arguments_end(self, i: int) -> int:
        text = self.text
        while True:
            j = self.space_end(i)
            if not text.startswith("[", j):
                return i
            i = self.bracket_end(j)

    def bracket_end(self, start: int) -> int:
        text = self.text
        i = start + 1
        while i < len(text):
            if text[i] == "]":
                return i + 1
            i = self.group_end(i, verbatim=False) if text[i] == "{" else self.skip(i)
        raise self.error(start, "unclosed [ argument")

    def group_end(self, start: int, *, verbatim: bool) -> int:
        """Index past the brace group that opens at text[start] == '{'."""
        text = self.text
        depth = 0
        i = start
        while i < len(text):
            char = text[i]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
            elif not verbatim and char in "\\%":
                i = self.skip(i)
                continue
            i += 1
        raise self.error(start, "unclosed { group")

    def mandatory_argument_end(self, start: int, i: int, name: str, *, verbatim: bool) -> int:
        text = self.text
        i = self.space_end(i)
        if i >= len(text) or text[i] in "\n}":
            raise self.error(start, f"missing argument of \\{name}")
        if text[i] == "{":
            return self.group_end(i, verbatim=verbatim)
        if text[i] == "\\":
            word = LETTERS.match(text, i + 1)
            return i + 2 if word is None else word.end()
        return i + 1


# --- Pass 2: prose ------------------------------------------------------------------------------


def _blanks_end(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t":
        i += 1
    return i


def _code_group_end(code: str, start: int) -> int:
    """Index past the group that opens at code[start] == '{', or the end of an unclosed one."""
    depth = 0
    i = start
    while i < len(code):
        char = code[i]
        if char == "\\":
            i += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(code)


def _code_bracket_end(code: str, start: int) -> int:
    """Index past the [..] argument that opens at code[start], or the end of an unclosed one."""
    i = start + 1
    while i < len(code):
        char = code[i]
        if char == "]":
            return i + 1
        if char == "{":
            i = _code_group_end(code, i)
        else:
            i += 2 if char == "\\" else 1
    return len(code)


class _ProseWriter:
    r"""The prose of pass 2.

    A line break that TeX skips inside a word, as in seam\emph, a line break and {less}, is held
    back until the word ends, so the word is written whole and the prose keeps every line break.
    """

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.held = 0

    def write(self, text: str) -> None:
        if self.held and text[0].isspace():
            self.parts.append("\n" * self.held)
            self.held = 0
        self.parts.append(text)

    def unprinted_line_break(self) -> None:
        if self.parts and not self.parts[-1][-1].isspace():
            self.held += 1
        else:
            self.parts.append("\n")

    def text(self) -> str:
        return "".join(self.parts) + "\n" * self.held


def _prose(code: str, breaks: frozenset[int], unprinted: frozenset[int]) -> str:
    out = _ProseWriter()
    i = 0
    while i < len(code):
        char = code[i]
        if i in unprinted:
            # Blanked text that TeX prints nothing for; see _CodeScanner.unprinted.
            if char == "\n":
                out.unprinted_line_break()
            i += 1
        elif char == "\\":
            i = _prose_command(code, i, out)
        elif i in breaks:
            # The end of one argument when the next one follows, as in \textcolor{red}{x}.
            out.write(" ")
            i += 1
        elif char in "{}":
            i += 1
        elif char == "~":
            out.write(" ")
            i += 1
        else:
            out.write(char)
            i += 1
    return out.text()


def _removed(span: str) -> str:
    """What a removed span leaves in the prose: a space, or its line breaks."""
    return "\n" * span.count("\n") or " "


def _prose_command(code: str, start: int, out: _ProseWriter) -> int:
    accent = ACCENT.match(code, start)
    if accent is not None:
        out.write(next(group for group in accent.groups() if group))
        return accent.end()
    letter = SPECIAL_LETTER.match(code, start)
    if letter is not None:
        out.write(SPECIAL_LETTERS[letter[1]])
        return letter.end()
    if start + 1 >= len(code):
        return len(code)
    following = code[start + 1]
    name_match = LETTERS.match(code, start + 1)
    if name_match is not None:
        name = name_match[0]
        end = name_match.end()
        if name in ("begin", "end"):
            # Pass 1 has blanked the arguments after \begin{name}.
            environment = CODE_ENVIRONMENT.match(code, start)
            if environment is not None:
                end = environment.end()
            out.write(_removed(code[start:end]))
            return end
        if code.startswith("*", end):
            end += 1
        if name not in PRINTS_NO_TEXT:
            out.write(" ")
        return end
    if following == "\\":
        # A line break; pass 1 has blanked its * and [length].
        out.write(" ")
        return start + 2
    if following in "-/@" or following in ACCENT_SYMBOLS:
        # A discretionary hyphen, an italic correction, \@, or an accent whose letter is not on
        # its line; the letter follows as text.
        return start + 2
    if following in "%&#$_{}":
        out.write(following)
    elif following == "\n":
        out.write("\n")
    else:
        out.write(" ")
    return start + 2
