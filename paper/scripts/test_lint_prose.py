"""Tests for paper/scripts/lint_prose.py, the prose linter (PRD 22.6 check 2, D36).

Each rule is tested on short strings that must pass or fail, then the linter runs on the
manuscript under paper/. Test inputs are strings or files in tmp_path; no fixture .tex file is
committed under paper/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.lint_prose import BANNED_WORDS, Finding, build_parser, lint_text, main

SECTION = "background.tex"
APPENDIX = "appendix-data-analysis.tex"


def rules(text: str, name: str = SECTION) -> list[str]:
    return [finding.rule for finding in lint_text(name, text)]


# --- Rule a: dash punctuation -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "a --- b",
        "a---b",
        "a ---- b",
        "a\u2014b",
        "a \u2014 b",
        "a\u2013b",
        "pages 9\u2013b",
        "a \\textemdash{} b",
        "a\\textemdash b",
        "a \\textendash{} b",
        "9\\textendash 11",
        "a -- b",
        "a--b",
        "9 -- 11",
        "9--a",
        "a - b",
        "a\n- b",
        "a -\nb",
        "a\u2012b",
        "a\u2015b",
        "a\u2e3ab",
        "a\u2e3bb",
        "a\ufe58b",
        "a\u2010b",
        "a\u2212b",
        "$x$ - $y$",
        "\\cite{a} -- \\cite{b}",
        "\\emph{a} --- b",
        "a -{}- b",
    ],
)
def test_dash_rule_fails(text: str) -> None:
    assert "dash" in rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "pages 9--11",
        "pages 9\u201311",
        "$a-b$",
        "$a - b$",
        "$a -- b$",
        "self-supervised",
        "pre- and post-processing",
        "a % a dash -- here --- and \u2014 there\nb",
        "\\url{https://x.org/a--b}",
        "\\ref{fig:a--b} and \\label{sec:a---b}",
        "\\verb|a -- b|",
        "\\begin{tikzpicture}\n\\draw (a) -- (b);\n\\draw (b) -> (c);\n\\end{tikzpicture}",
        "$x$-axis",
    ],
)
def test_dash_rule_passes(text: str) -> None:
    assert "dash" not in rules(text)


# --- Rule b: banned words -----------------------------------------------------------------------


def test_banned_words_are_the_list_of_prd_22_6() -> None:
    assert sorted(BANNED_WORDS) == sorted(
        [
            "delve",
            "seamless",
            "robust",
            "leverage",
            "cutting-edge",
            "comprehensive",
            "crucial",
            "pivotal",
            "showcase",
            "innovative",
            "harness",
            "groundbreaking",
            "game-changing",
            "tapestry",
            "testament",
            "underscore",
            "unlock",
            "empower",
        ]
    )


@pytest.mark.parametrize(
    "text",
    [
        *(f"We {word} here." for word in BANNED_WORDS if word != "robust"),
        "Leveraging the data",
        "\\emph{seamless}",
        "\\textit{sea}mless",
        "\\emph{{seam}{less}}",
        "{seam}{less}",
        "\\emph{sea}{mless}",
        "{\\bf pivo}{tal}",
        "It delves into",
        "We leveraged it",
        "showcasing results",
        "This underscores the point",
        "empowering users",
        "a SEAMLESS fit",
        "a seamlessly joined map",
        "cutting edge",
        "cutting~edge",
        "cutting\nedge",
        "a game changing result",
        "ground-breaking",
        "tapestries",
        "harnessed",
        "unlocked",
        "testaments",
        "delved",
        "crucially",
    ],
)
def test_banned_word_rule_fails(text: str) -> None:
    assert "banned-word" in rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "delivery",
        "crucible",
        "pivot",
        "empire",
        "a test",
        "% we leverage this in a comment\nText.",
        "\\cite{robust2020}",
        "\\label{sec:seamless}",
        "$robust$",
    ],
)
def test_banned_word_rule_passes(text: str) -> None:
    assert "banned-word" not in rules(text)


def test_robust_fails_without_an_allow_comment() -> None:
    assert rules("A robust estimator.") == ["banned-word"]
    assert rules("Robustness checks.") == ["banned-word"]


@pytest.mark.parametrize(
    "text",
    [
        "A robust estimator. % lint-allow robust: the median tolerates outliers\n",
        "A robust estimator.%lint-allow robust: Huber's sense\n",
        "Robustly. %% lint-allow robust: the statistical term\n",
    ],
)
def test_robust_passes_with_an_allow_comment_and_a_reason(text: str) -> None:
    assert rules(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "A robust estimator. % lint-allow robust:\n",
        "A robust estimator. % lint-allow robust:   \n",
        "A robust estimator. % lint-allow robust\n",
        "A robust estimator. % the median is robust\n",
        "% lint-allow robust: on the wrong line\nA robust estimator.\n",
        "A robust estimator. \\% lint-allow robust: escaped, so not a comment\n",
    ],
)
def test_robust_fails_with_a_missing_or_empty_allow_comment(text: str) -> None:
    assert "banned-word" in rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "We leverage it. % lint-allow leverage: a reason\n",
        "We leverage it. % lint-allow robust: a reason\n",
    ],
)
def test_only_robust_can_be_allowed(text: str) -> None:
    assert rules(text) == ["banned-word"]


def test_allow_comment_covers_only_robust_on_its_line() -> None:
    text = "A robust and seamless fit. % lint-allow robust: the median\n"
    findings = lint_text(SECTION, text)
    assert [finding.rule for finding in findings] == ["banned-word"]
    assert "seamless" in findings[0].text


# --- Rule c: "it is important to note" ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "it is important to note",
        "It Is Important To Note that",
        "IT IS IMPORTANT TO NOTE",
        "it is\nimportant to note",
        "it is \\emph{important} to note",
        "it is important~to note",
        "it is % a comment\nimportant to note",
        "it\n\nis important to note",
        "it is \\emph{{impor}{tant}} to note",
    ],
)
def test_important_to_note_rule_fails(text: str) -> None:
    assert "important-to-note" in rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "it is important to measure",
        "% it is important to note\nText.",
        "edit is important to note-taking",
    ],
)
def test_important_to_note_rule_passes(text: str) -> None:
    assert "important-to-note" not in rules(text)


# --- Rule d: question marks ---------------------------------------------------------------------


@pytest.mark.parametrize("text", ["Why?", "Does it work ? No.", "Really\uff1f", "\\emph{why?}"])
def test_question_rule_fails(text: str) -> None:
    assert "question-mark" in rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "\\url{https://x.org/?q=1}",
        "\\href{https://x.org/?q=1}{the archive}",
        "% why?\nText.",
        "$P(A?)$",
        "\\verb|?|",
        "\\begin{tikzpicture}\\node {Why?};\\end{tikzpicture}",
    ],
)
def test_question_rule_passes(text: str) -> None:
    assert "question-mark" not in rules(text)


# --- Rule e: bullet lists -----------------------------------------------------------------------

ITEMIZE = "\\begin{itemize}\n\\item One.\n\\end{itemize}\n"


@pytest.mark.parametrize(
    "name",
    [
        "main.tex",
        "abstract.tex",
        "introduction.tex",
        "background.tex",
        "data.tex",
        "fingerprint.tex",
        "architecture.tex",
        "embedding.tex",
        "training.tex",
        "results.tex",
        "limitations.tex",
        "conclusion.tex",
    ],
)
def test_bullet_lists_fail_in_the_main_body(name: str) -> None:
    assert "bullet-list" in rules(ITEMIZE, name)


@pytest.mark.parametrize(
    "text",
    [
        ITEMIZE,
        "\\begin{enumerate}\\item One.\\end{enumerate}",
        "\\begin{description}\\item[A] One.\\end{description}",
        "\\begin {itemize}\\item One.\\end{itemize}",
    ],
)
def test_every_list_environment_fails_in_a_section(text: str) -> None:
    assert "bullet-list" in rules(text)


@pytest.mark.parametrize("name", ["appendix-data-analysis.tex", "appendix-embeddings.tex"])
def test_bullet_lists_pass_in_appendices(name: str) -> None:
    assert "bullet-list" not in rules(ITEMIZE, name)


def test_commented_list_passes() -> None:
    assert "bullet-list" not in rules("% \\begin{itemize}\nText.\n")


# --- Rule f: bold inside sentences --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "A \\textbf{x} word.",
        "A {\\bf x} word.",
        "A {\\bfseries x} word.",
        "\\begin{tabular}{l}a\\end{tabular} \\textbf{x}",
        "\\begin{table}\\caption{\\textbf{x}}\\end{table}",
        "\\textbf{x}",
    ],
)
def test_bold_rule_fails(text: str) -> None:
    assert "bold" in rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "\\begin{tabular}{l}\\textbf{x}\\end{tabular}",
        "\\begin{tabular*}{\\linewidth}{l}{\\bf x}\\end{tabular*}",
        "\\begin{tabular}{l}\\bfseries x\\end{tabular}",
        "% \\textbf{x}\nText.",
        "$\\mathbf{x}$",
        "\\begin{tikzpicture}\\node {\\textbf{x}};\\end{tikzpicture}",
        "\\textbfseries",
    ],
)
def test_bold_rule_passes(text: str) -> None:
    assert "bold" not in rules(text)


# --- TikZ ---------------------------------------------------------------------------------------


def test_tikz_path_syntax_passes() -> None:
    text = "\\begin{tikzpicture}\n\\draw (a) -- (b);\n\\draw[->] (b) -- (c);\n\\end{tikzpicture}\n"
    assert rules(text) == []


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("\\begin{tikzpicture}\\node {leverage};\\end{tikzpicture}", "banned-word"),
        ("\\begin{tikzpicture}\\node {sea\\-mless};\\end{tikzpicture}", "banned-word"),
        (
            "\\begin{tikzpicture}\\node {It is\nimportant to note};\\end{tikzpicture}",
            "important-to-note",
        ),
        (
            "\\begin{tikzpicture}\\tikzset{every node/.style={label={seamless}}}\\end{tikzpicture}",
            "banned-word",
        ),
        (
            "\\begin{tikzpicture}\n\\tikzset{n/.style={label={it is important to note}}}\n"
            "\\end{tikzpicture}",
            "important-to-note",
        ),
        ("\\begin{tikzpicture}\\node {$\\text{leverage}$};\\end{tikzpicture}", "banned-word"),
        (
            "\\begin{tikzpicture}\\node {\\textcolor{pivotal}{x}};\\end{tikzpicture}",
            "banned-word",
        ),
        (
            "\\begin{tikzpicture}\\node {\\textcolor[rgb]{1,0,0}{pivotal}};\\end{tikzpicture}",
            "banned-word",
        ),
        (
            "\\begin{tikzpicture}\\node {\\textcolor{red}{pivo}{tal}};\\end{tikzpicture}",
            "banned-word",
        ),
        ("\\begin{tikzpicture}\\node {\\emph{{seam}{less}}};\\end{tikzpicture}", "banned-word"),
    ],
)
def test_banned_words_and_phrase_also_run_on_tikz(text: str, rule: str) -> None:
    assert rule in rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "\\begin{tikzpicture}% a seamless path\n\\draw (a) -- (b);\n\\end{tikzpicture}",
        "\\begin{tikzpicture}\n\\node {robust}; % lint-allow robust: method name\n"
        "\\end{tikzpicture}",
        "\\begin{tikzpicture}\\tikzset{a/.style={->, dashed}}\\draw[a] (a) -- (b);"
        "\\end{tikzpicture}",
    ],
)
def test_tikz_comments_and_style_code_pass(text: str) -> None:
    assert rules(text) == []


# --- Comments before an argument ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("See \\ref%\n{fig:a--b} here.", []),
        ("As \\cite% why?\n{shepard2001} shows.", []),
        ("As \\citep% we leverage\n[p.~5]{shepard2001} shows.", []),
        ("\\begin%\n{itemize}\\item One.\\end{itemize}", ["bullet-list"]),
        ("\\begin{tabular}%\n%\n{l}\\textbf{x}\\end{tabular}", []),
        ("\\begin{tabular}{l}x\\end%\n{tabular} \\textbf{y}", ["bold"]),
    ],
)
def test_comment_before_an_argument_is_read_as_tex_reads_it(text: str, expected: list[str]) -> None:
    assert rules(text) == expected


# --- Findings -----------------------------------------------------------------------------------


def test_finding_reports_file_line_rule_and_text() -> None:
    findings = lint_text(SECTION, "First.\n% a comment\nThird --- here.\n")
    assert len(findings) == 1
    assert findings[0].line == 3
    assert str(findings[0]).startswith("background.tex:3: dash: ")
    assert "Third --- here." in str(findings[0])


def test_findings_are_ordered_by_line() -> None:
    findings = lint_text(SECTION, "Why?\nA --- b.\nWe leverage.\n")
    assert [(finding.line, finding.rule) for finding in findings] == [
        (1, "question-mark"),
        (2, "dash"),
        (3, "banned-word"),
    ]


def test_syntax_error_is_a_finding() -> None:
    assert lint_text(SECTION, "ok\n$x\n") == [
        Finding(SECTION, 2, "syntax", "unclosed $ math"),
    ]


# --- Command line -------------------------------------------------------------------------------


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_main_exits_1_and_prints_each_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path, "main.tex", "Clean.\n")
    write(tmp_path, "background.tex", "A --- b.\nWhy?\n")
    assert main(["--root", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "background.tex:1: dash: " in err
    assert "background.tex:2: question-mark: " in err


def test_main_exits_0_on_clean_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "main.tex", "Clean.\n")
    write(tmp_path, "figures/styles.tex", "\\tikzset{a/.style={draw}} % --- ?\n---\n")
    assert main(["--root", str(tmp_path)]) == 0
    assert "lint_prose.py: no findings in 1 .tex files" in capsys.readouterr().out


def test_main_checks_nested_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, "main.tex", "Clean.\n")
    write(tmp_path, "figures/system-overview.tex", "\\caption{A seamless view.}\n")
    assert main(["--root", str(tmp_path)]) == 1
    assert "figures/system-overview.tex:1: banned-word: " in capsys.readouterr().err


def test_main_fails_without_tex_files(tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path)]) == 1


def test_main_fails_on_a_file_that_is_not_utf8(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_bytes(b"caf\xe9\n")
    assert main(["--root", str(tmp_path)]) == 1


def test_no_option_disables_a_rule() -> None:
    options = {option for action in build_parser()._actions for option in action.option_strings}
    assert options == {"-h", "--help", "--root"}


def test_manuscript_passes_the_prose_lint(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0, capsys.readouterr().err
