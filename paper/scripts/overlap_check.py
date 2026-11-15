r"""Fail on prose copied from a cited abstract (PRD 22.4, 22.6 check 4, D36).

The paper builds on prior work by citation, never by copied text. This script tokenises the prose
of every file that lint_prose.py checks and every non-null abstract in reference-verification.json,
read from references.<key>.abstract. Any run of MIN_RUN or more consecutive tokens shared with one
abstract is a finding. Each maximal run is reported once, as 'file:line: overlap: text', with the
line of its first word, the reference key and the shared words; it exits 1 if there is any.

A token is a run of [a-z0-9] after LaTeX accents are folded, Unicode NFKC is applied, the text is
case-folded and diacritics are removed, so case, hyphens, punctuation, ties, markup, comments and
line breaks cannot make the two sides differ. The limit is a constant; no option changes it. A
missing or unparsable record file fails. Entries whose abstract is null are skipped and counted in
the summary line.

Run it from paper/ as python -P -m scripts.overlap_check with PYTHONPATH=., as the Makefile does.
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.tex_prose import Source, TexSyntaxError, checked_files, extract, fold_latex

PAPER = Path(__file__).resolve().parents[1]
RECORD_FILE = "reference-verification.json"

# The shortest shared run of words that fails (PRD 22.6 check 4).
MIN_RUN = 12

TOKEN = re.compile(r"[a-z0-9]+")
# Letters that Unicode does not decompose into a base letter and a combining mark.
BASE_LETTERS = str.maketrans(
    {
        "\u00f8": "o",
        "\u0142": "l",
        "\u0111": "d",
        "\u0127": "h",
        "\u0131": "i",
        "\u0167": "t",
        "\u00e6": "ae",
        "\u0153": "oe",
    }
)


class RecordError(Exception):
    """The reference record is missing, unparsable or malformed."""


@dataclass(frozen=True)
class Overlap:
    path: str
    line: int
    key: str
    words: tuple[str, ...]

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.line}: overlap: {len(self.words)} words shared with the abstract "
            f"of {self.key}: {' '.join(self.words)}"
        )


def normalise(text: str) -> str:
    """Text with LaTeX accents folded, NFKC applied, case folded and diacritics removed."""
    text = unicodedata.normalize("NFKC", fold_latex(text)).casefold()
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )
    return text.translate(BASE_LETTERS)


def tokens(text: str) -> list[str]:
    return TOKEN.findall(normalise(text))


_normalised_character = functools.cache(normalise)


def located_tokens(source: Source) -> tuple[list[str], list[int]]:
    """The tokens of the prose of source, with the source line where each one starts.

    The prose holds no backslash, so it normalises one character at a time to the same text as
    a whole: a combining mark that NFKC would compose with its letter is removed either way.
    """
    folded: list[str] = []
    lines: list[int] = []
    for offset, char in enumerate(source.prose):
        normal = _normalised_character(char)
        folded.append(normal)
        lines += [source.prose_line(offset)] * len(normal)
    text = "".join(folded)
    words = []
    starts = []
    for match in TOKEN.finditer(text):
        words.append(match[0])
        starts.append(lines[match.start()])
    return words, starts


def shared_runs(paper: Sequence[str], abstract: Sequence[str]) -> list[tuple[int, int]]:
    """(start, length) in paper of each maximal run of MIN_RUN or more tokens found in abstract.

    A run inside a longer reported run, which a repeated phrase in the abstract can produce, is
    not reported again.
    """
    starts: dict[tuple[str, ...], list[int]] = {}
    for j in range(len(abstract) - MIN_RUN + 1):
        starts.setdefault(tuple(abstract[j : j + MIN_RUN]), []).append(j)
    runs = set()
    for i in range(len(paper) - MIN_RUN + 1):
        for j in starts.get(tuple(paper[i : i + MIN_RUN]), []):
            if i > 0 and j > 0 and paper[i - 1] == abstract[j - 1]:
                continue
            length = MIN_RUN
            while (
                i + length < len(paper)
                and j + length < len(abstract)
                and paper[i + length] == abstract[j + length]
            ):
                length += 1
            runs.add((i, length))
    return sorted(
        (start, length)
        for start, length in runs
        if not any(
            (other, other_length) != (start, length)
            and other <= start
            and start + length <= other + other_length
            for other, other_length in runs
        )
    )


def overlaps(path: str, text: str, abstracts: Mapping[str, str]) -> list[Overlap]:
    """Shared runs between the prose of one .tex source and each abstract, by line and key."""
    words, lines = located_tokens(extract(text))
    found = [
        Overlap(path, lines[start], key, tuple(words[start : start + length]))
        for key, abstract in abstracts.items()
        for start, length in shared_runs(words, tokens(abstract))
    ]
    return sorted(found, key=lambda overlap: (overlap.line, overlap.key))


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise RecordError(f"duplicate keys: {', '.join(duplicates)}")
    return dict(pairs)


def load_abstracts(path: Path) -> tuple[dict[str, str], int]:
    """The non-null abstracts by reference key, and the number of null ones."""
    if not path.is_file():
        raise RecordError(f"{path} does not exist")
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_object_without_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RecordError(f"{path} is not valid JSON: {error}") from error
    except RecordError as error:
        raise RecordError(f"{path}: {error}") from error
    references = data.get("references") if isinstance(data, dict) else None
    if not isinstance(references, dict):
        raise RecordError(f"{path} must be an object with a references object")
    abstracts: dict[str, str] = {}
    skipped = 0
    problems = []
    for key, entry in references.items():
        if not isinstance(entry, dict) or "abstract" not in entry:
            problems.append(f"references.{key} has no abstract field")
            continue
        abstract = entry["abstract"]
        if abstract is None:
            skipped += 1
        elif isinstance(abstract, str) and abstract.strip():
            abstracts[key] = abstract
        else:
            problems.append(f"references.{key}.abstract is not a non-empty string or null")
    if problems:
        raise RecordError(f"{path}: {'; '.join(problems)}")
    return abstracts, skipped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"Fail on {MIN_RUN} or more consecutive words shared with a cited abstract."
    )
    parser.add_argument(
        "--root", type=Path, default=PAPER, help="paper directory to check (default: paper/)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.root
    try:
        abstracts, skipped = load_abstracts(root / RECORD_FILE)
    except RecordError as error:
        print(f"overlap_check.py: {error}", file=sys.stderr)
        return 1
    files = checked_files(root)
    if not files:
        print(f"overlap_check.py: no .tex files under {root}", file=sys.stderr)
        return 1
    problems: list[str] = []
    found: list[Overlap] = []
    for path in files:
        name = path.relative_to(root).as_posix()
        try:
            found += overlaps(name, path.read_text(encoding="utf-8"), abstracts)
        except UnicodeDecodeError as error:
            problems.append(f"{name}:1: encoding: not UTF-8: {error}")
        except TexSyntaxError as error:
            problems.append(f"{name}:{error.line}: syntax: {error.message}")
    summary = (
        f"overlap_check.py: {len(files)} .tex files, {len(abstracts)} abstracts, "
        f"{skipped} references without an abstract skipped"
    )
    for line in [*problems, *map(str, found)]:
        print(line, file=sys.stderr)
    if problems or found:
        print(
            f"{summary}; {len(found)} runs of {MIN_RUN} or more shared words, "
            f"{len(problems)} unreadable files",
            file=sys.stderr,
        )
        return 1
    print(f"{summary}; no run of {MIN_RUN} or more shared words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
