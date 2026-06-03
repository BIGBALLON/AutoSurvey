"""Tests for tools/reverse_outline.py — survey-write Step 7 weak-topic
sentence detector + reverse-outline reporter.

Two layers:
  - Pure-function unit tests (`is_weak`, `extract_section_metadata`,
    `extract_first_sentence`)
  - CLI integration tests via subprocess — happy / strict-fail / missing-dir
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import reverse_outline as ro  # noqa: E402


# ---------------------------------------------------------------------------
# is_weak — generic openers + word count
# ---------------------------------------------------------------------------


def test_is_weak_strong_sentence_passes():
    weak, reasons = ro.is_weak(
        "The compute-optimal recipe consolidated pretraining practice across the field in 2022 firmly."
    )
    assert weak is False
    assert reasons == []


def test_is_weak_too_short_flags_word_count():
    """< 8 words → flagged as too-short."""
    weak, reasons = ro.is_weak("Scaling laws are universal.")   # 4 words
    assert weak is True
    assert any("too short" in r for r in reasons), reasons


def test_is_weak_generic_opener_flags():
    """'Recent advances...' is one of GENERIC_OPENERS."""
    weak, reasons = ro.is_weak(
        "Recent advances in language models have produced impressive scaling laws."
    )
    assert weak is True
    assert any("generic opener" in r for r in reasons), reasons


def test_is_weak_can_combine_two_reasons():
    """Both too-short AND generic-opener can fire on the same sentence."""
    weak, reasons = ro.is_weak("In this section we begin.")    # 5 words + opener
    assert weak is True
    assert len(reasons) == 2
    assert any("too short" in r for r in reasons)
    assert any("generic opener" in r for r in reasons)


def test_is_weak_strips_latex_for_word_count():
    """\\cite{} commands must NOT count toward the word budget."""
    # Below has 5 plain words + 1 cite — should fall under the 8-word floor
    sentence = r"Models scale predictably with parameter count \cite{kaplan2020}."
    weak, reasons = ro.is_weak(sentence)
    # 5 words after stripping cite — flagged as too short
    assert weak is True
    assert any("too short" in r for r in reasons)


# ---------------------------------------------------------------------------
# extract_section_metadata
# ---------------------------------------------------------------------------


def test_extract_section_metadata_returns_full_shape():
    text = (
        r"\section{Scaling Laws}" "\n"
        r"\subsection{Power Laws}" "\n"
        r"\subsection{Compute Frontier}" "\n\n"
        "Compute-optimal training shifted the field in 2022.\n\n"
        "Token budgets matter more than parameter count past 1e23 FLOPs.\n"
    )
    meta = ro.extract_section_metadata(text)
    assert meta["section_title"] == "Scaling Laws"
    assert meta["subsection_titles"] == ["Power Laws", "Compute Frontier"]
    assert meta["n_paragraphs"] == 2
    assert len(meta["topic_sentences"]) == 2
    assert meta["topic_sentences"][0].startswith("Compute-optimal")


def test_extract_section_metadata_handles_no_section():
    """Body-only text with no \\section{} → section_title is None."""
    text = "Just a paragraph.\n\nAnother paragraph.\n"
    meta = ro.extract_section_metadata(text)
    assert meta["section_title"] is None
    assert meta["n_paragraphs"] == 2


def test_extract_section_metadata_strips_comments():
    """`%`-prefixed LaTeX comments must not become paragraphs."""
    text = (
        r"\section{X}" "\n\n"
        "% This is a comment, ignore me\n\n"
        "Real first paragraph here, with at least eight words present.\n"
    )
    meta = ro.extract_section_metadata(text)
    assert meta["n_paragraphs"] == 1


# ---------------------------------------------------------------------------
# _has_prose + pure-LaTeX paragraph filtering
#
# Bug fixed: \input{figures/tables/...} and similar pure-LaTeX paragraphs
# were treated as if they had a topic sentence, then flagged as 'weak topic
# sentence (0 words)'. They yield no narrative spine — they should not even
# enter the topic-sentence list.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    (r"\input{figures/tables/02_arch_compare.tex}", False),
    (r"\includegraphics[width=0.9\linewidth]{fig.pdf}", False),
    (r"\begin{figure}\centering\end{figure}", False),
    ("Real prose with at least three real words.", True),
    (r"As shown in Figure~\ref{fig:foo}, the trend is clear and rises.", True),
    # 2-word fragment doesn't qualify
    ("Short bit.", False),
])
def test_has_prose_distinguishes_latex_only_from_real_text(text, expected):
    assert ro._has_prose(text) is expected


def test_strip_latex_keeps_text_inside_textbf_emph():
    """Bug fixed in previous regex deleted the brace contents
    of \\textbf{...}/\\emph{...}, so '\\textbf{US labs} dominate the
    dense-scaling-laws literature' counted as 6 words and was flagged
    weak — even though a reader sees 8 words."""
    s = r"\textbf{US labs} dominate the dense-scaling-laws literature"
    cleaned = ro._strip_latex_for_word_count(s)
    # 'US' and 'labs' must survive
    assert "US" in cleaned and "labs" in cleaned
    # Word count restored — pre-fix this dropped 'US labs' entirely so the
    # count went from 6 to 4. Post-fix it stays at 6.
    assert len(cleaned.split()) == 6


def test_strip_latex_drops_citep_and_ref_entirely():
    """Reference-style commands carry NO visible text — strip them whole.
    Otherwise the citation key becomes a fake word."""
    s = r"DeepSeek-V2 \citep{deepseekai2024deepseek-v2} pushed further \ref{fig:1}"
    cleaned = ro._strip_latex_for_word_count(s)
    # The citation key must NOT survive
    assert "deepseekai2024deepseek" not in cleaned
    assert "fig:1" not in cleaned
    # The hyphenated model name and the verb must survive
    assert "DeepSeek-V2" in cleaned and "pushed" in cleaned


def test_is_weak_textbf_does_not_lose_words():
    """Real-run regression: '\\textbf{US labs} dominate the dense-scaling-
    laws and data-pipeline literature' is 8 visible words — must NOT be
    flagged as too-short."""
    is_w, reasons = ro.is_weak(
        r"\textbf{US labs} dominate the dense-scaling-laws "
        r"and data-pipeline literature."
    )
    assert is_w is False, reasons


def test_is_weak_handles_nested_textbf_emph():
    """Nested typographic commands: \\textbf{\\emph{Foo bar}} → 'Foo bar'."""
    cleaned = ro._strip_latex_for_word_count(r"\textbf{\emph{Foo bar}} baz qux")
    assert "Foo" in cleaned and "bar" in cleaned and "baz" in cleaned


def test_extract_section_metadata_skips_pure_latex_paragraphs():
    """Mixed body — \\input + \\caption + real prose. Only the real prose
    paragraph should be reported as a topic sentence."""
    text = (
        r"\section{Body}"  "\n\n"
        r"\input{figures/tables/02_arch_compare.tex}"  "\n\n"
        r"\begin{figure}"  "\n"
        r"\includegraphics[width=0.9\linewidth]{fig.pdf}"  "\n"
        r"\caption{A figure caption.}"  "\n"
        r"\end{figure}"  "\n\n"
        "Real first paragraph here, with at least eight words present.\n"
    )
    meta = ro.extract_section_metadata(text)
    # 3 chunks split on blank lines, but only the prose one survives:
    assert len(meta["topic_sentences"]) == 1
    assert meta["topic_sentences"][0].startswith("Real first paragraph")


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def _make_run_dir(tmp_path: Path, sections: dict[str, str]) -> Path:
    rd = tmp_path / "run"
    sec = rd / "5_paper" / "sections"
    sec.mkdir(parents=True)
    for name, content in sections.items():
        (sec / name).write_text(content)
    return rd


def _run_cli(run_dir: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "reverse_outline.py"),
         str(run_dir), *flags],
        capture_output=True, text=True,
    )


def test_cli_clean_paper_exits_zero(tmp_path):
    """All paragraphs strong → exit 0, default report written."""
    rd = _make_run_dir(tmp_path, {
        "01_intro.tex": (
            r"\section{Introduction}" "\n\n"
            "Compute-optimal pretraining recipes consolidated the field in 2022.\n\n"
            "Token budgets matter more than parameter count past the 1e23 threshold.\n"
        )
    })
    res = _run_cli(rd)
    assert res.returncode == 0, f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    # Default report path is written
    report = rd / "4_outline" / "reverse_outline.md"
    assert report.exists()
    assert "# Reverse Outline Report" in report.read_text()
    assert "Survey-Wide Narrative Skeleton" in report.read_text()


def test_cli_strict_with_weak_topic_exits_one(tmp_path):
    """Generic opener + --strict → exit 1."""
    rd = _make_run_dir(tmp_path, {
        "01_intro.tex": (
            r"\section{Introduction}" "\n\n"
            "Recent advances in language models have produced strong results.\n\n"
            "Compute scaling continues without obvious diminishing returns at 1e25 FLOPs.\n"
        )
    })
    res = _run_cli(rd, "--strict")
    assert res.returncode == 1
    assert ("Strict mode" in res.stdout or "weak" in res.stdout.lower())


def test_cli_strict_clean_paper_exits_zero(tmp_path):
    """--strict on a clean paper still exits 0."""
    rd = _make_run_dir(tmp_path, {
        "01_intro.tex": (
            r"\section{Introduction}" "\n\n"
            "Compute-optimal pretraining recipes consolidated the field in 2022.\n\n"
            "Token budgets matter more than parameter count past the 1e23 threshold.\n"
        )
    })
    res = _run_cli(rd, "--strict")
    assert res.returncode == 0


def test_cli_missing_sections_dir_returns_two(tmp_path):
    """No 5_paper/sections/ at all → exit 2."""
    rd = tmp_path / "empty"
    rd.mkdir()
    res = _run_cli(rd)
    assert res.returncode == 2
    assert "not found" in res.stderr


def test_cli_json_writes_structured_findings(tmp_path):
    """--json PATH writes a structured findings file with the documented schema."""
    rd = _make_run_dir(tmp_path, {
        "01_intro.tex": (
            r"\section{Introduction}" "\n\n"
            "Recent advances in language models keep producing impressive results.\n"
        )
    })
    json_path = tmp_path / "findings.json"
    res = _run_cli(rd, "--json", str(json_path))
    assert res.returncode == 0   # without --strict, weak topics don't fail
    assert json_path.exists()
    findings = json.loads(json_path.read_text())
    # Documented schema (verbatim keys)
    for k in ("total_sections", "total_paragraphs", "total_weak",
              "weak_sentences", "repeated_openers"):
        assert k in findings, f"missing key {k!r} in findings JSON"
    assert findings["total_sections"] == 1
    assert findings["total_weak"] >= 1
    # Each weak entry has the expected sub-keys
    for w in findings["weak_sentences"]:
        assert "section" in w and "sentence" in w and "reasons" in w


def test_cli_custom_report_path(tmp_path):
    """--report PATH overrides the default output location."""
    rd = _make_run_dir(tmp_path, {
        "01_intro.tex": (
            r"\section{Introduction}" "\n\n"
            "Compute-optimal pretraining recipes consolidated the field in 2022.\n"
        )
    })
    custom = tmp_path / "custom_report.md"
    res = _run_cli(rd, "--report", str(custom))
    assert res.returncode == 0
    assert custom.exists()
    # Default path was NOT used
    assert not (rd / "4_outline" / "reverse_outline.md").exists()
