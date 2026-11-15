"""Fail the paper build on problems that Tectonic reports but does not treat as errors (PRD 22.6).

Tectonic 0.17.0 exits 0 on undefined citations and on BibTeX errors, so this script reads
main.log, and main.blg if it exists, after every build. It prints 'file:line: rule: text' for each
finding and exits 1 if there is any.

Usage: check_log.py main.log [--blg main.blg]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# The widest Overfull \hbox that passes, in points.
OVERFULL_LIMIT_PT = 1.0

# Each pattern matches the start of a log line; TeX wraps log lines at 79 characters, so only the
# opening words of a message are matched.
LOG_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("undefined-citation", re.compile(r"(?:LaTeX|Package natbib) Warning: Citation `")),
    (
        "undefined-citation",
        re.compile(r"(?:LaTeX|Package natbib) Warning: There were undefined citations"),
    ),
    ("undefined-reference", re.compile(r"LaTeX Warning: (?:Hyper r|R)eference `")),
    ("undefined-reference", re.compile(r"LaTeX Warning: There were undefined references")),
    ("multiply-defined-label", re.compile(r"LaTeX Warning: Label `")),
    ("multiply-defined-label", re.compile(r"LaTeX Warning: There were multiply-defined labels")),
    ("labels-changed", re.compile(r"LaTeX Warning: Label\(s\) may have changed")),
    ("labels-changed", re.compile(r"Package natbib Warning: Citation\(s\) may have changed")),
    ("missing-character", re.compile(r"Missing character: ")),
    ("tex-error", re.compile(r"! ")),
)
OVERFULL_HBOX = re.compile(r"Overfull \\hbox \((\d+(?:\.\d+)?)pt too wide\)")

BLG_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bibtex-warning", re.compile(r"Warning--")),
    ("bibtex-warning", re.compile(r"\(There (?:was|were) \d+ warnings?\)")),
    ("bibtex-error", re.compile(r"\(There (?:was|were) \d+ error messages?\)")),
    ("bibtex-error", re.compile(r".*---(?:line \d+ of file|while reading file)")),
    ("bibtex-error", re.compile(r"I couldn't open")),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.text}"


def first_rule(line: str, rules: tuple[tuple[str, re.Pattern[str]], ...]) -> str | None:
    return next((rule for rule, pattern in rules if pattern.match(line)), None)


def check_log(text: str, path: str = "main.log") -> list[Finding]:
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        rule = first_rule(line, LOG_RULES)
        overfull = OVERFULL_HBOX.match(line)
        if rule is None and overfull and float(overfull[1]) > OVERFULL_LIMIT_PT:
            rule = "overfull-hbox"
        if rule is not None:
            findings.append(Finding(path, number, rule, line))
    return findings


def check_blg(text: str, path: str = "main.blg") -> list[Finding]:
    return [
        Finding(path, number, rule, line)
        for number, line in enumerate(text.splitlines(), start=1)
        if (rule := first_rule(line, BLG_RULES)) is not None
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Tectonic's main.log and main.blg.")
    parser.add_argument("log", type=Path, help="TeX log, main.log")
    parser.add_argument("--blg", type=Path, help="BibTeX log, read only if it exists")
    args = parser.parse_args(argv)
    log: Path = args.log
    blg: Path | None = args.blg

    if not log.is_file():
        print(f"check_log.py: {log} does not exist; run Tectonic with --keep-logs", file=sys.stderr)
        return 1
    findings = check_log(log.read_text(encoding="utf-8", errors="replace"), str(log))
    checked = [str(log)]
    if blg is not None and blg.is_file():
        findings += check_blg(blg.read_text(encoding="utf-8", errors="replace"), str(blg))
        checked.append(str(blg))
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print(f"check_log.py: {len(findings)} findings in {', '.join(checked)}", file=sys.stderr)
        return 1
    print(f"check_log.py: {', '.join(checked)} clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
