"""Tests for tools/prose_polish.py — NARRATIVE_RULES + ARGUMENT_RULES.

prose_polish.py carries a large rule set. These tests target the two
structural pure functions:

  - check_narrative_pillars(sections)  → 4-pillar score + per-pillar evidence
  - check_argument_anchors(sections)   → per-section 5-anchor score

audit_writing.py (tested separately) is the submission gate; prose_polish
runs --check during writing as an early signal.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import prose_polish as pp  # noqa: E402


def _read_sections(run_dir: Path) -> dict[str, str]:
    return {
        f.stem: f.read_text(encoding="utf-8")
        for f in sorted((run_dir / "5_paper" / "sections").glob("*.tex"))
    }


# ---------------------------------------------------------------------------
# check_narrative_pillars
# ---------------------------------------------------------------------------


def test_pillars_clean_fixture(survey_run_dir):
    sections = _read_sections(survey_run_dir)
    info = pp.check_narrative_pillars(sections)
    assert info["hook"]["present"] is True, info["hook"]
    assert info["why_now"]["present"] is True
    assert info["relationship"]["present"] is True
    assert info["contributions"]["present"] is True
    assert info["contributions"]["count"] >= 4
    assert info["score"] == 1.0
    assert info["abstract_present"] is True


def test_pillars_hook_needs_two_of_three_signals(survey_run_dir):
    """Hook accepts any 2 of {year, number, metaphor}. Drop to a single
    signal (just a number, no year, no metaphor) — Hook must fail.
    The benchmark survey opens with year + metaphor (no metric) and that
    pattern still passes; this test pins the lower bound."""
    intro = survey_run_dir / "5_paper" / "sections" / "01_introduction.tex"
    text = intro.read_text()
    # Bland first paragraph: just a number, no year and no metaphor.
    bland_first = (
        r"\section{Introduction}" "\n"
        "Roughly seventy billion parameters are typical.\n\n"
    )
    rest = text.split("\n\n", 1)[1]
    intro.write_text(bland_first + rest)

    sections = _read_sections(survey_run_dir)
    info = pp.check_narrative_pillars(sections)
    assert info["hook"]["present"] is False, info["hook"]
    # Score drops to 0.75 (3 of 4 pillars still pass)
    assert info["score"] == 0.75


def test_pillars_hook_accepts_year_plus_metaphor_no_metric(survey_run_dir):
    """The benchmark Introduction's first paragraph hits year +
    metaphor and saves the metric for paragraph 2. That pattern is now
    accepted (2 of 3 hook signals)."""
    intro = survey_run_dir / "5_paper" / "sections" / "01_introduction.tex"
    text = intro.read_text()
    benchmark_first = (
        r"\section{Introduction}" "\n"
        "In 2022, AI systems served as sophisticated typewriters: they "
        "predicted the next token. By 2025, they had become colleagues "
        "that independently navigate codebases.\n\n"
    )
    rest = text.split("\n\n", 1)[1]
    intro.write_text(benchmark_first + rest)

    sections = _read_sections(survey_run_dir)
    info = pp.check_narrative_pillars(sections)
    assert info["hook"]["present"] is True, info["hook"]
    # year=True, number=True (the year itself counts), metaphor=True
    assert "year=True" in info["hook"]["evidence"]
    assert "metaphor=True" in info["hook"]["evidence"]


def test_pillars_contributions_needs_at_least_4(survey_run_dir):
    """Truncate the enumerate to 3 items — contributions must fail."""
    intro = survey_run_dir / "5_paper" / "sections" / "01_introduction.tex"
    text = intro.read_text()
    text = text.replace(
        r"""\begin{enumerate}
\item A unified token-budget account across three generations.
\item A decision-summary table comparing recipes.
\item A 5-anchor argument structure for each body section.
\item Identification of long-context as the breaking frontier.
\end{enumerate}""",
        r"""\begin{enumerate}
\item One.
\item Two.
\item Three.
\end{enumerate}"""
    )
    intro.write_text(text)

    sections = _read_sections(survey_run_dir)
    info = pp.check_narrative_pillars(sections)
    assert info["contributions"]["present"] is False
    assert info["contributions"]["count"] == 0


def test_pillars_contributions_accepts_inline_textbf_markers():
    """many real intros number their contributions inline using
    \\textbf{(1)} … \\textbf{(2)} markers instead of an enumerate block.
    The pillar detector must accept this style as well."""
    sections = {"01_intro": (
        r"\section{Introduction}" "\n"
        r"\label{sec:intro}" "\n\n"
        + "\n\n".join(
            rf"\textbf{{({i}) Shift number {i}.}} Body of contribution {i}."
            for i in range(1, 6)
        )
    )}
    info = pp.check_narrative_pillars(sections)
    assert info["contributions"]["present"] is True
    assert info["contributions"]["count"] == 5


def test_pillars_contributions_inline_markers_must_be_in_sequence():
    """Out-of-order or gapped \\textbf{(N)} markers don't count — they
    might be unrelated bold tags scattered through the section."""
    sections = {"01_intro": (
        r"\section{Introduction}" "\n\n"
        r"\textbf{(1) First.} Body."  "\n\n"
        r"\textbf{(7) Skipped.} Body."  "\n\n"
        r"\textbf{(8) Skipped.} Body."  "\n\n"
        r"\textbf{(9) Skipped.} Body."
    )}
    info = pp.check_narrative_pillars(sections)
    assert info["contributions"]["present"] is False


# ---------------------------------------------------------------------------
# _first_paragraph_after — robustness against LaTeX section markers
#
# Bug fixed in real Intro files start with
#   \section{Introduction}\n\label{sec:intro}\n\n
#   \subsection{What pretraining covers ...}\n\n
#   By \emph{pretraining} we mean ...
# Previously the resolver:
#   * matched only `\section\{` (no closing brace), so the first 'paragraph'
#     was 'Introduction}\n\label{sec:intro}' — accidental nonsense.
#   * filtered chunks via `not startswith("\\")`, dropping every paragraph
#     that begins with \subsection / \textbf / \emph — i.e. virtually all
#     real Intro paragraphs.
# Together these made Hook detection effectively dead on the real run.
# ---------------------------------------------------------------------------


def test_first_paragraph_after_handles_full_section_marker(tmp_path):
    text = (
        r"\section{Introduction}"   "\n"
        r"\label{sec:intro}"        "\n\n"
        r"\subsection{What pretraining covers in 2021--2026}" "\n\n"
        r"By \emph{pretraining} we mean the unconditional next-token"
        r" optimisation of a language model. The transition from BF16 to"
        r" FP8 in 2024 marked a regime shift."  "\n\n"
        r"Second body paragraph here." "\n"
    )
    para = pp._first_paragraph_after(text, r"\\section\*?\{[^}]*\}")
    assert para is not None, "first prose paragraph must be found"
    assert "By" in para and "pretraining" in para
    # Must NOT be the closing brace residue
    assert "Introduction}" not in para
    # Must NOT be the bare \subsection header chunk
    assert para.lstrip().startswith("By")


def test_first_paragraph_after_skips_label_only_chunk(tmp_path):
    text = (
        r"\section{Body}"      "\n"
        r"\label{sec:body}"    "\n\n"
        r"Real prose here with content."  "\n"
    )
    para = pp._first_paragraph_after(text, r"\\section\*?\{[^}]*\}")
    assert para == "Real prose here with content."


def test_first_paragraph_after_returns_none_when_marker_absent():
    para = pp._first_paragraph_after("just body text", r"\\section\*?\{[^}]*\}")
    assert para is None


def test_pillars_hook_detects_year_number_metaphor_in_real_first_para():
    """End-to-end: a hook-shaped first paragraph (year + number with unit
    + 'from X to Y' metaphor) must score Hook=True even when sandwiched
    between a section, a label and a subsection header."""
    sections = {"01_intro": (
        r"\section{Introduction}"   "\n"
        r"\label{sec:intro}"        "\n\n"
        r"\subsection{The 2024 inflection}" "\n\n"
        r"In 2024, pretraining moved from BF16 to FP8 across the open"
        r" frontier, with 65% of new releases shipping mixed-precision"
        r" training out of the box."  "\n"
    )}
    info = pp.check_narrative_pillars(sections)
    assert info["hook"]["present"] is True, info["hook"]
    assert "year=True" in info["hook"]["evidence"]
    assert "number=True" in info["hook"]["evidence"]
    assert "metaphor=True" in info["hook"]["evidence"]


# ---------------------------------------------------------------------------
# check_argument_anchors
# ---------------------------------------------------------------------------


def test_anchors_clean_fixture(survey_run_dir):
    sections = _read_sections(survey_run_dir)
    info = pp.check_argument_anchors(sections)
    # Body sections have all 5 anchors in canonical order
    assert info["passing"] == info["total"], info
    assert info["score"] == 1.0
    assert "02_body" in info["per_section"]
    assert info["per_section"]["02_body"]["ok"] is True


def test_anchors_skip_intro_and_abstract(survey_run_dir):
    """01_introduction and 00_abstract must be skipped (different rule sets)."""
    sections = _read_sections(survey_run_dir)
    info = pp.check_argument_anchors(sections)
    assert "00_abstract" not in info["per_section"]
    assert "01_introduction" not in info["per_section"]
    # 03_open_problems is also skipped (uses 4-bucket rule)
    assert "03_open_problems" not in info["per_section"]


def test_anchors_extras_allowed_if_canonical_order_preserved(survey_run_dir):
    """Adding a non-canonical anchor (e.g. NOTE) between the 5 must NOT fail
    as long as the canonical 5 stay in order."""
    body = survey_run_dir / "5_paper" / "sections" / "02_body.tex"
    text = body.read_text()
    text = text.replace("% [SO-WHAT]", "% [NOTE]\n% [SO-WHAT]")
    body.write_text(text)

    sections = _read_sections(survey_run_dir)
    info = pp.check_argument_anchors(sections)
    assert info["per_section"]["02_body"]["ok"] is True


def test_anchors_missing_emits_specific_issue(survey_run_dir):
    body = survey_run_dir / "5_paper" / "sections" / "02_body.tex"
    text = body.read_text().replace("% [STEELMAN]", "% [REMOVED]")
    body.write_text(text)

    sections = _read_sections(survey_run_dir)
    info = pp.check_argument_anchors(sections)
    body_info = info["per_section"]["02_body"]
    assert body_info["ok"] is False
    assert "STEELMAN" in body_info["issue"]


# ---------------------------------------------------------------------------
# CLI banner — must not say "All deterministic checks pass" when
# narrative/anchor warnings are present and not promoted via --strict-narrative
# ---------------------------------------------------------------------------


def test_cli_banner_distinguishes_advisory_from_clean(survey_run_dir):
    """Regression: prose_polish --check on a section file with broken
    anchors but otherwise clean prose must NOT print
    'All deterministic checks pass' (which contradicts the per-section
    ✗ lines printed just above). It should show the
    'manual review' qualifier instead."""
    import subprocess

    # Break anchors on 02_body.tex (drop STEELMAN)
    body = survey_run_dir / "5_paper" / "sections" / "02_body.tex"
    body.write_text(body.read_text().replace("% [STEELMAN]", "% [REMOVED]"))

    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "prose_polish.py"),
         "--check", str(survey_run_dir)],
        capture_output=True, text=True,
    )
    out = res.stdout

    # Per-section ✗ should appear …
    assert "missing anchors" in out
    # … and the banner must reflect that the run is NOT all-clean.
    assert "All deterministic checks pass" not in out
    # The new advisory phrasing should be present:
    assert "manual review" in out


def test_cli_banner_clean_fixture_says_all_pass(survey_run_dir):
    """Mirror test: on the untouched fixture (which carries proper
    anchors and pillars) the banner must say All deterministic checks pass."""
    import subprocess
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "prose_polish.py"),
         "--check", str(survey_run_dir)],
        capture_output=True, text=True,
    )
    assert "All deterministic checks pass" in res.stdout


# ---------------------------------------------------------------------------
# find_long_sentences / find_long_paragraphs — LaTeX handling
#
# Two cooperating bugs were fixed:
#   * '\\%' in body prose (literal percent) used to be eaten as a comment
#     marker, devouring the rest of the sentence and silently undercounting
#     long sentences whenever the paper used percentages.
#   * The naive '\\[a-zA-Z]+...{...}' command-stripper deleted the contents
#     of \\textbf{Foo}/\\emph{Foo}, causing word counts to be artificially
#     low. Both helpers now go through _strip_latex_for_word_count.
# ---------------------------------------------------------------------------


def test_find_long_sentences_does_not_eat_at_escaped_percent():
    """'cache is reduced by approximately 93\\% versus MHA at no
    quality loss. DeepSeek-V3 retained MLA at frontier scale ...' must
    survive comment-stripping; the period after 'no quality loss.' must
    still split off a long second sentence about DeepSeek-V3."""
    text = (
        "The cache is reduced by approximately 93\\% versus MHA at no "
        "quality loss. "
        "DeepSeek-V3 retained MLA at frontier scale of sixty-one layers, "
        "seven thousand one hundred sixty-eight hidden, one hundred "
        "twenty-eight heads, five hundred twelve dim latent, and it "
        "appears that the KV-cache reduction was a precondition for the "
        "particularly aggressive mixture-of-experts configuration that "
        "ended up shipping in production."
    )
    out = pp.find_long_sentences(text, max_words=40)
    # The DeepSeek-V3 sentence (>40 words) must be detected
    assert any("DeepSeek-V3" in o["preview"] and o["word_count"] > 40
               for o in out), out


def test_find_long_sentences_keeps_text_inside_textbf():
    """Pre-fix the textbf body was dropped, so the sentence might fall
    just under the 40-word threshold. Post-fix the visible words count."""
    visible_text = " ".join(["w" + str(i) for i in range(45)])
    sentence = r"\textbf{(1) Big shift.} " + visible_text + "."
    out = pp.find_long_sentences(sentence, max_words=40)
    assert len(out) == 1
    # 'Big shift.' (2 words from textbf body, the period stays inside
    # textbf so it's part of the same sentence) + 45 placeholders
    assert out[0]["word_count"] >= 45


def test_find_long_paragraphs_does_not_eat_at_escaped_percent():
    """Same fix in find_long_paragraphs: a paragraph with '93\\%' in it
    must still be processed sentence-by-sentence, not chopped at the '%'."""
    text = (
        "Foo. Bar. Baz. The cache is reduced by 93\\% on average. "
        "Quux. Quuz. Corge. Grault. Garply. Waldo."
    )
    out = pp.find_long_paragraphs(text, max_sentences=8)
    # 10 sentences in one paragraph (>8) must be flagged
    assert len(out) == 1
    assert out[0]["sentence_count"] >= 9
