"""Tests for tools/_latex_text.py — shared LaTeX-text helpers.

These tests mirror what each caller (audit_writing, prose_polish,
reverse_outline) used to test privately; together they constitute the
contract of the helper module.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import _latex_text as lt  # noqa: E402


# ---------------------------------------------------------------------------
# strip_leading_latex_commands
# ---------------------------------------------------------------------------


def test_strip_leading_label_only_chunk_becomes_empty():
    """A chunk that is just \\label{...} carries no prose at its head."""
    assert lt.strip_leading_latex_commands(r"\label{sec:intro}") == ""


def test_strip_leading_subsection_only_chunk_becomes_empty():
    assert lt.strip_leading_latex_commands(
        r"\subsection{What pretraining covers}"
    ) == ""


def test_strip_leading_recurses_through_label_and_subsection():
    """Realistic Intro shape: \\label, then \\subsection, then prose."""
    s = (
        r"\label{sec:intro}"
        "\n"
        r"\subsection{Title}"
        "\n"
        r"\textbf{(1) Hook.} Body of paragraph here."
    )
    body = lt.strip_leading_latex_commands(s)
    # \textbf{(1) Hook.} is also stripped (it's typographic and leading)
    assert body.startswith("Body of paragraph") or body.startswith("(1) Hook.")
    # Either way, no \label / \subsection residue remains at the head
    assert not body.lstrip().startswith(r"\label")
    assert not body.lstrip().startswith(r"\subsection")


def test_strip_leading_does_not_touch_mid_paragraph_commands():
    """Commands not at the *head* must survive — we only peel the leader."""
    s = r"Real prose here with \emph{emphasis} mid-sentence."
    out = lt.strip_leading_latex_commands(s)
    assert out == s


def test_strip_leading_passthrough_when_already_prose():
    s = "Plain prose paragraph."
    assert lt.strip_leading_latex_commands(s) == s


# ---------------------------------------------------------------------------
# has_prose
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    (r"\input{figures/tables/02_arch_compare.tex}", False),
    (r"\includegraphics[width=0.9\linewidth]{fig.pdf}", False),
    (r"\begin{figure}\centering\end{figure}", False),
    ("Real prose with at least three real words.", True),
    (r"As shown in Figure~\ref{fig:foo}, the trend is clear and rises.", True),
    ("Short bit.", False),  # 2-word fragment — too short
    ("", False),
])
def test_has_prose_distinguishes_latex_only_from_real_text(text, expected):
    assert lt.has_prose(text) is expected


def test_has_prose_respects_min_words_override():
    """Tools that want a stricter / looser bar can pass min_words."""
    assert lt.has_prose("Two words.", min_words=2) is True
    assert lt.has_prose("Two words.", min_words=3) is False


def test_has_prose_strips_inline_math():
    """$\\sigma$ alone is not prose; surrounding words are."""
    assert lt.has_prose(r"$\sigma$") is False
    assert lt.has_prose(r"The variance $\sigma^2$ scales like the inverse.") is True


# ---------------------------------------------------------------------------
# strip_latex_for_word_count
# ---------------------------------------------------------------------------


def test_strip_word_count_keeps_text_inside_textbf_and_emph():
    """\\textbf{US labs} dominate ... — 'US labs' must survive."""
    s = r"\textbf{US labs} dominate the dense-scaling-laws literature"
    cleaned = lt.strip_latex_for_word_count(s)
    assert "US" in cleaned and "labs" in cleaned
    # 'dominate' too
    assert "dominate" in cleaned


def test_strip_word_count_drops_citep_and_ref_entirely():
    s = r"DeepSeek-V2 \citep{deepseekai2024deepseek-v2} pushed further \ref{fig:1}"
    cleaned = lt.strip_latex_for_word_count(s)
    assert "deepseekai2024deepseek" not in cleaned
    assert "fig:1" not in cleaned
    assert "DeepSeek-V2" in cleaned and "pushed" in cleaned


def test_strip_word_count_handles_nested_textbf_emph():
    cleaned = lt.strip_latex_for_word_count(
        r"\textbf{\emph{Foo bar}} baz qux"
    )
    assert "Foo" in cleaned and "bar" in cleaned and "baz" in cleaned


def test_strip_word_count_drops_remaining_unknown_commands():
    """Unknown command is not text-bearing, not in reference list — must drop."""
    s = r"prefix \unknowncommand{secret} suffix"
    cleaned = lt.strip_latex_for_word_count(s)
    assert "secret" not in cleaned
    assert "prefix" in cleaned and "suffix" in cleaned


# ---------------------------------------------------------------------------
# Re-exports: confirm the three call sites can still import private aliases
# (regression guard against future cleanup that might over-prune)
# ---------------------------------------------------------------------------


def test_audit_writing_alias_still_callable():
    import audit_writing as aw
    assert aw._strip_leading_latex_commands(r"\label{x}") == ""


def test_prose_polish_alias_still_callable():
    import prose_polish as pp
    assert pp._strip_leading_latex_commands(r"\label{x}") == ""


def test_reverse_outline_aliases_still_callable():
    import reverse_outline as ro
    assert ro._has_prose(r"\input{x.tex}") is False
    assert ro._strip_latex_for_word_count(r"\textbf{a} b c d e") == "a b c d e"
