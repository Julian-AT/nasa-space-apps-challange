r"""Turn a LaTeX source file into prose that keeps source line numbers (PRD 22.6, D36).

lint_prose.py and overlap_check.py read the manuscript through this module, so both see the same
text. extract() works in two passes.

1. Code. Comments (an unescaped % to the end of the line), \verb and verbatim environments, math
   ($...$, $$...$$, \(...\), \[...\] and the equation, align, gather and multline environments,
   starred or not), the arguments of key commands (the \cite family, \ref, \eqref, \label, \input,
   \include, \url, the URL of \href and a few more in KEY_COMMANDS) and the code of tikzpicture
   environments are replaced by spaces. Every other character stays where it was, so an offset in
   the code is an offset in the source.
2. Prose. Commands, environment names and braces are removed from the code. Formatting commands
   such as \emph, \textit and \textbf keep their text, a tie ~ reads as a space, and accent
   commands give their base letter.

Both passes keep every line break, so line n of the code and of the prose is line n of the
source. An unclosed construct raises TexSyntaxError with the line where it opens, so a check
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
# Commands whose first argument is read verbatim, as the url package does, so % and ~ are text.
VERBATIM_ARGUMENT = frozenset({"url", "href"})

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

# A LaTeX accent command and the letter it accents: \'e, \'{e}, \"{\i}, \v{c} or \v c.
ACCENT = re.compile(
    r"\\(?:['`^\"~=.]|[vuHckrdbtG](?![A-Za-z]))[ \t]*"
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
ENVIRONMENT_NAME = re.compile(r"[ \t]*\{[ \t]*([A-Za-z@]+\*?)[ \t]*\}")
BLANK_LINE = re.compile(r"\n[ \t]*\n")
ARGUMENT_SPACE = re.compile(r"[ \t]*(?:\n[ \t]*)?")
LINE_BREAK_LENGTH = re.compile(r"[ \t]*\[[^\]]*\]")


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

    With keep_tikz the code of tikzpicture environments is read as text, so node labels and
    style names reach the prose; lint_prose.py runs its word rules on that version.
    """
    scanner = _CodeScanner(text, keep_tikz)
    code = scanner.run()
    return Source(code, _prose(code), scanner.comments)


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
        end = self.text.find("\n", start)
        end = len(self.text) if end == -1 else end
        self.comments.setdefault(self.line(start), self.text[start + 1 : end])
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
        boundary = re.compile(r"\\(begin|end)[ \t]*\{[ \t]*" + re.escape(name) + r"[ \t]*\}")
        if verbatim:
            match = re.compile(r"\\end[ \t]*\{" + re.escape(name) + r"\}").search(text, start + 1)
            if match is None:
                raise self.error(start, f"unclosed {{{name}}} environment")
            return match.end()
        depth = 0
        i = start
        while i < len(text):
            match = boundary.match(text, i)
            if match is not None:
                if match[1] == "begin" and (nested or depth == 0):
                    depth += 1
                elif match[1] == "end":
                    depth -= 1
                    if depth == 0:
                        return match.end()
                i = match.end()
            else:
                i = self.skip(i)
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
        name_match = LETTERS.match(text, start + 1)
        if name_match is None:
            return start + 2
        name = name_match[0]
        i = name_match.end()
        if name == "verb":
            end = self.verb_end(start, i)
        elif name == "begin":
            environment = ENVIRONMENT_NAME.match(text, i)
            if environment is None:
                raise self.error(start, "\\begin without an environment name")
            environment_end = self.begin_end(start, environment[1])
            if environment_end is None:
                return environment.end()
            end = environment_end
        elif name in KEY_COMMANDS or CITE_COMMAND.fullmatch(name):
            end = self.key_arguments_end(start, i, name)
        else:
            return i
        self.blank(start, end)
        return end

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
        """End of a math, verbatim or (unless kept) TikZ environment; None for any other one."""
        if name in VERBATIM_ENVIRONMENTS:
            return self.environment_end(start, name, verbatim=True, nested=False)
        if name in MATH_ENVIRONMENTS:
            return self.environment_end(start, name, verbatim=False, nested=False)
        if name == TIKZ_ENVIRONMENT and not self.keep_tikz:
            return self.environment_end(start, name, verbatim=False, nested=True)
        return None

    def key_arguments_end(self, start: int, i: int, name: str) -> int:
        count = 1 if CITE_COMMAND.fullmatch(name) else KEY_COMMANDS[name]
        if self.text.startswith("*", i):
            i += 1
        for index in range(count):
            i = self.optional_arguments_end(i)
            i = self.mandatory_argument_end(
                start, i, name, verbatim=index == 0 and name in VERBATIM_ARGUMENT
            )
        return i

    def space_end(self, i: int) -> int:
        """Index past spaces and tabs with at most one line break, as between TeX arguments."""
        match = ARGUMENT_SPACE.match(self.text, i)
        return i if match is None else match.end()

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


def _prose(code: str) -> str:
    # Reads the arguments of \begin{tabular} and the like; the code has no comments left.
    arguments = _CodeScanner(code, keep_tikz=True)
    out: list[str] = []
    i = 0
    while i < len(code):
        char = code[i]
        if char == "\\":
            i = _prose_command(arguments, i, out)
        elif char in "{}":
            i += 1
        elif char == "~":
            out.append(" ")
            i += 1
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _removed(span: str) -> str:
    """What a removed span leaves in the prose: a space, or its line breaks."""
    return "\n" * span.count("\n") or " "


def _prose_command(arguments: _CodeScanner, start: int, out: list[str]) -> int:
    code = arguments.text
    accent = ACCENT.match(code, start)
    if accent is not None:
        out.append(next(group for group in accent.groups() if group))
        return accent.end()
    letter = SPECIAL_LETTER.match(code, start)
    if letter is not None:
        out.append(SPECIAL_LETTERS[letter[1]])
        return letter.end()
    if start + 1 >= len(code):
        return len(code)
    following = code[start + 1]
    name_match = LETTERS.match(code, start + 1)
    if name_match is not None:
        name = name_match[0]
        end = name_match.end()
        if code.startswith("*", end):
            end += 1
        if name in ("begin", "end"):
            end = _environment_arguments_end(arguments, end, name)
            out.append(_removed(code[start:end]))
        elif name not in TEXT_COMMANDS:
            out.append(" ")
        return end
    if following == "\\":
        # A line break, with an optional * and an optional [length].
        end = start + 2
        if code.startswith("*", end):
            end += 1
        bracket = LINE_BREAK_LENGTH.match(code, end)
        if bracket is not None:
            end = bracket.end()
        out.append(_removed(code[start:end]))
        return end
    if following in "-/@":
        return start + 2
    if following in "%&#$_{}":
        out.append(following)
    elif following == "\n":
        out.append("\n")
    else:
        out.append(" ")
    return start + 2


def _environment_arguments_end(arguments: _CodeScanner, i: int, name: str) -> int:
    """Index past {environment} after \\begin or \\end and, after \\begin, its own arguments."""
    environment = ENVIRONMENT_NAME.match(arguments.text, i)
    if environment is None:
        return i
    i = environment.end()
    if name == "end":
        return i
    if environment[1] in FLOAT_ENVIRONMENTS:
        return arguments.optional_arguments_end(i)
    for _ in range(ENVIRONMENT_ARGUMENTS.get(environment[1], 0)):
        i = arguments.optional_arguments_end(i)
        i = arguments.mandatory_argument_end(i, i, f"begin{{{environment[1]}}}", verbatim=False)
    return i
