r"""Lint the prose of the Terraluna paper (PRD 22.6 check 2, D36).

Checks every .tex file under paper/ except figures/styles.tex, which is style code. It prints
'file:line: rule: text' for each finding and exits 1 if there is any. No option disables a rule.

Rules, read from the text that tex_prose.py extracts:

  dash               Unicode dash punctuation (general category Pd other than the ASCII hyphen,
                     plus the minus sign U+2212 and the swung dash U+2053), \textemdash,
                     \textendash and --- anywhere; -- or an en dash except directly between two
                     digits (9--11); a hyphen with whitespace on both sides. Hyphens inside words
                     and anything in math pass.
  banned-word        delve, seamless, robust, leverage, cutting-edge, comprehensive, crucial,
                     pivotal, showcase, innovative, harness, groundbreaking, game-changing,
                     tapestry, testament, underscore, unlock and empower, as whole words in any
                     case with their inflections. Only robust can be allowed, on a line that
                     carries the comment '% lint-allow robust: <reason>' with a non-empty reason.
  important-to-note  "it is important to note", in any case, also across lines and markup.
  question-mark      any question mark in prose, which catches rhetorical questions.
  bullet-list        itemize, enumerate or description environments (and the paralist forms)
                     anywhere but the appendix files appendix-*.tex.
  bold               \textbf, \bfseries or {\bf ...} outside tabular environments.

banned-word and important-to-note also read the raw text of tikzpicture environments: node labels,
options, style names, math and every command argument, such as those of \tikzset. Only comments,
command names and braces are removed there. The other rules skip TikZ code, where -- and -> are
path syntax.

Run it from paper/ as python -P -m scripts.lint_prose with PYTHONPATH=., as the Makefile does.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.tex_prose import (
    Source,
    TexSyntaxError,
    checked_files,
    environment_boundary,
    extract,
    line_at,
)

PAPER = Path(__file__).resolve().parents[1]

# Each banned word with the forms that count as it: inflections, and the adverb or noun that a
# writer would reach for instead.
BANNED_WORDS: dict[str, str] = {
    "delve": r"delv(?:e|es|ed|ing)",
    "seamless": r"seamless(?:ly|ness)?",
    "robust": r"robust(?:ly|ness|er|est)?",
    "leverage": r"leverag(?:e|es|ed|ing)",
    "cutting-edge": r"cutting(?:-|\s+)edge",
    "comprehensive": r"comprehensive(?:ly|ness)?",
    "crucial": r"crucial(?:ly)?",
    "pivotal": r"pivotal(?:ly)?",
    "showcase": r"showcas(?:e|es|ed|ing)",
    "innovative": r"innovative(?:ly|ness)?",
    "harness": r"harness(?:es|ed|ing)?",
    "groundbreaking": r"ground(?:-|\s*)breaking",
    "game-changing": r"game(?:-|\s*)chang(?:ing|er|ers)",
    "tapestry": r"tapestr(?:y|ies)",
    "testament": r"testaments?",
    "underscore": r"underscor(?:e|es|ed|ing)",
    "unlock": r"unlock(?:s|ed|ing)?",
    "empower": r"empower(?:s|ed|ing|ment|ments)?",
}
ALLOWABLE_WORD = "robust"
# Not preceded or followed by a letter or digit; an underscore or a hyphen is a boundary.
WORD_START = r"(?<![^\W_])"
WORD_END = r"(?![^\W_])"
BANNED_PATTERNS = {
    word: re.compile(WORD_START + f"(?:{forms})" + WORD_END, re.IGNORECASE)
    for word, forms in BANNED_WORDS.items()
}
ALLOW_COMMENT = re.compile(r"[%\s]*lint-allow robust:[ \t]*\S")

IMPORTANT_TO_NOTE = re.compile(
    WORD_START + r"it\s+is\s+important\s+to\s+note" + WORD_END, re.IGNORECASE
)

HYPHEN_RUN = re.compile(r"-{2,}")
LONE_HYPHEN = re.compile(r"(?<!\S)-(?!\S)")
DASH_COMMAND = re.compile(r"\\text(?:em|en)dash(?![A-Za-z])")
EN_DASH = "\u2013"
EXTRA_DASHES = frozenset({"\u2212", "\u2053"})
DIGITS = frozenset("0123456789")

QUESTION_MARKS = frozenset("?\u00bf\u203d\u2047\u2048\u2049\ufe16\ufe56\uff1f")

LIST_ENVIRONMENT = environment_boundary(
    r"(?:itemize|enumerate|description|(?:compact|aspara|inpara)(?:item|enum|desc))\*?"
)
LISTS_ALLOWED = "appendix-*.tex"

BOLD = re.compile(r"\\(?:textbf|bfseries|bf)(?![A-Za-z])")
TABULAR = environment_boundary(r"tabular\*?|tabularx|tabulary|longtable\*?")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.text}"


@dataclass(frozen=True)
class _Hit:
    """One rule match: its source line and the text that matched."""

    line: int
    rule: str
    match: str


def code_hit(code: str, offset: int, rule: str, match: str) -> _Hit:
    """A match at code[offset]; the code keeps every source offset."""
    return _Hit(line_at(code, offset), rule, match)


def prose_hit(source: Source, offset: int, rule: str, match: str) -> _Hit:
    """A match at source.prose[offset], on the source line where its text starts."""
    return _Hit(source.prose_line(offset), rule, match)


def is_dash(char: str) -> bool:
    return (unicodedata.category(char) == "Pd" and char != "-") or char in EXTRA_DASHES


def between_digits(text: str, start: int, end: int) -> bool:
    return start > 0 and end < len(text) and text[start - 1] in DIGITS and text[end] in DIGITS


def dash_hits(plain: Source) -> Iterator[_Hit]:
    prose = plain.prose
    for offset, char in enumerate(prose):
        if is_dash(char) and not (char == EN_DASH and between_digits(prose, offset, offset + 1)):
            yield prose_hit(plain, offset, "dash", f"U+{ord(char):04X}")
    for run in HYPHEN_RUN.finditer(prose):
        if len(run[0]) > 2 or not between_digits(prose, run.start(), run.end()):
            yield prose_hit(plain, run.start(), "dash", run[0])
    for hyphen in LONE_HYPHEN.finditer(prose):
        yield prose_hit(plain, hyphen.start(), "dash", "spaced hyphen")
    for command in DASH_COMMAND.finditer(plain.code):
        yield code_hit(plain.code, command.start(), "dash", command[0])


def banned_word_hits(full: Source) -> Iterator[_Hit]:
    for word, pattern in BANNED_PATTERNS.items():
        for match in pattern.finditer(full.prose):
            found = prose_hit(full, match.start(), "banned-word", match[0])
            if word != ALLOWABLE_WORD:
                yield found
            elif not ALLOW_COMMENT.match(full.comments.get(found.line, "")):
                yield _Hit(found.line, found.rule, f"{match[0]} (needs % lint-allow robust)")


def phrase_hits(full: Source) -> Iterator[_Hit]:
    for match in IMPORTANT_TO_NOTE.finditer(full.prose):
        yield prose_hit(full, match.start(), "important-to-note", " ".join(match[0].split()))


def question_hits(plain: Source) -> Iterator[_Hit]:
    for offset, char in enumerate(plain.prose):
        if char in QUESTION_MARKS:
            yield prose_hit(plain, offset, "question-mark", char)


def lists_allowed(name: str) -> bool:
    """Bullet lists are allowed only in the appendix files at the top of paper/."""
    return "/" not in name and fnmatch.fnmatchcase(name, LISTS_ALLOWED)


def list_hits(plain: Source) -> Iterator[_Hit]:
    for match in LIST_ENVIRONMENT.finditer(plain.code):
        if match[1] == "begin":
            yield code_hit(plain.code, match.start(), "bullet-list", f"\\begin{{{match[2]}}}")


def tabular_spans(code: str) -> list[tuple[int, int]]:
    spans = []
    depth = 0
    start = 0
    for match in TABULAR.finditer(code):
        if match[1] == "begin":
            if depth == 0:
                start = match.end()
            depth += 1
        elif depth > 0:
            depth -= 1
            if depth == 0:
                spans.append((start, match.start()))
    if depth > 0:
        raise TexSyntaxError(line_at(code, start), "unclosed tabular environment")
    return spans


def bold_hits(plain: Source) -> Iterator[_Hit]:
    spans = tabular_spans(plain.code)
    for match in BOLD.finditer(plain.code):
        if not any(start <= match.start() < end for start, end in spans):
            yield code_hit(plain.code, match.start(), "bold", match[0])


def lint_text(name: str, text: str) -> list[Finding]:
    """The findings for one file, ordered by line; name is its POSIX path relative to paper/."""
    try:
        plain = extract(text)
        full = extract(text, keep_tikz=True)
        hits = [
            *dash_hits(plain),
            *banned_word_hits(full),
            *phrase_hits(full),
            *question_hits(plain),
            *(() if lists_allowed(name) else list_hits(plain)),
            *bold_hits(plain),
        ]
    except TexSyntaxError as error:
        return [Finding(name, error.line, "syntax", error.message)]
    lines = text.split("\n")
    findings = [
        Finding(
            name, found.line, found.rule, f'"{found.match}" in: {lines[found.line - 1].strip()}'
        )
        for found in hits
    ]
    return sorted(findings, key=lambda finding: finding.line)


def lint_file(root: Path, path: Path) -> list[Finding]:
    name = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        return [Finding(name, 1, "encoding", f"not UTF-8: {error}")]
    return lint_text(name, text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lint the prose of the paper's .tex files.")
    parser.add_argument(
        "--root", type=Path, default=PAPER, help="paper directory to check (default: paper/)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.root
    files = checked_files(root)
    if not files:
        print(f"lint_prose.py: no .tex files under {root}", file=sys.stderr)
        return 1
    findings = [finding for path in files for finding in lint_file(root, path)]
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print(
            f"lint_prose.py: {len(findings)} findings in {len(files)} .tex files", file=sys.stderr
        )
        return 1
    print(f"lint_prose.py: no findings in {len(files)} .tex files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
