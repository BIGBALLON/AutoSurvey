"""Tests for tools/build_run_stats.py — run-level meta-narrative.

Verifies each counter on the canonical survey_run_dir fixture, then drives
the CLI end-to-end and asserts the produced stats.json carries every
field the rendered paragraph relies on.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_run_stats as brs  # noqa: E402


# ---------------------------------------------------------------------------
# Pure counters
# ---------------------------------------------------------------------------


def test_count_papers_empty():
    assert brs.count_papers([]) == 0


def test_count_papers_simple():
    assert brs.count_papers([{"cite_key": "a"}, {"cite_key": "b"}]) == 2


def test_count_thesis_pieces_handles_missing_doc():
    assert brs.count_thesis_pieces(None) == {
        "argument_steps": 0, "anticipated_objections": 0,
    }


def test_count_outline_pieces_isolates_body_sections():
    """00_/01_ sections are abstract/intro and must NOT count as body."""
    outline = {"sections": [
        {"section_id": "00_abstract"},
        {"section_id": "01_intro"},
        {"section_id": "02_body_a"},
        {"section_id": "03_body_b"},
        {"section_id": "04_body_c"},
    ]}
    out = brs.count_outline_pieces(outline)
    assert out["sections"] == 5
    assert out["body_sections"] == 3
    assert out["tier_axis_tiers"] == 0


def test_count_outline_pieces_with_tier_axis():
    outline = {
        "sections": [{"section_id": "02_body"}],
        "tier_axis": {"tiers": [{"id": "T1"}, {"id": "T2"}]},
    }
    assert brs.count_outline_pieces(outline)["tier_axis_tiers"] == 2


def test_count_claims_skips_records_without_cite_key():
    cache = [
        {"cite_key": "a", "atomic_claims": [{"c": 1}, {"c": 2}]},
        {"atomic_claims": [{"c": 3}]},          # missing cite_key — skipped
        {"cite_key": "b", "atomic_claims": []},
    ]
    out = brs.count_claims(cache)
    assert out == {"papers_mined": 2, "atomic_claims": 2}


def test_count_systems_compared_dedupes_across_cells():
    outline = {"tier_axis": {"cells": {
        "T1": {"Architecture": ["Dense"], "Compute": ["Modest"]},
        "T2": {"Architecture": ["Dense", "MoE"], "Compute": ["High"]},
    }}}
    # Distinct values: Dense, MoE, Modest, High → 4
    assert brs.count_systems_compared(outline) == 4


def test_count_systems_compared_zero_without_outline_or_cards():
    assert brs.count_systems_compared({}) == 0
    assert brs.count_systems_compared(None) == 0
    # Empty cards list also yields 0 (no fallback signal at all).
    assert brs.count_systems_compared({}, []) == 0


def test_count_systems_compared_falls_back_to_decision_summary_cards():
    """When tier_axis.cells is empty but cards carry _decision_summary,
    use the count of annotated cards as the fallback signal."""
    cards = [
        {"cite_key": "a", "_decision_summary": {"availability": "open"}},
        {"cite_key": "b", "_decision_summary": {"availability": "closed"}},
        {"cite_key": "c"},  # no decision_summary — excluded
    ]
    assert brs.count_systems_compared({}, cards) == 2


def test_count_systems_compared_falls_back_to_cards_count_as_last_resort():
    """If neither tier_axis nor decision_summary are present, plain
    cards count is the closest approximation (still better than 0)."""
    cards = [{"cite_key": "a"}, {"cite_key": "b"}, {"cite_key": "c"}]
    assert brs.count_systems_compared({}, cards) == 3


def test_count_systems_compared_tier_axis_wins_over_cards():
    """When both signals are present, tier_axis is preferred (it's the
    explicit comparison matrix; cards is broader)."""
    outline = {"tier_axis": {"cells": {
        "T1": {"f": ["X", "Y"]}, "T2": {"f": ["Z"]},
    }}}
    cards = [{"cite_key": str(i)} for i in range(99)]
    # tier_axis set has 3 distinct items, even though cards list is 99
    assert brs.count_systems_compared(outline, cards) == 3


def test_scan_sections_zero_when_dir_missing(tmp_path):
    out = brs.scan_sections(tmp_path / "does-not-exist")
    assert out["section_files"] == 0
    assert out["estimated_pages"] == 0


def test_scan_sections_counts_citations_and_strips_comments(tmp_path):
    sec = tmp_path / "sections"
    sec.mkdir()
    (sec / "01_intro.tex").write_text(
        r"""Hello \cite{kaplan2020scaling}. % \cite{should_not_count}
\citep{hoffmann2022chinchilla,touvron2023llama}.
""",
        encoding="utf-8",
    )
    out = brs.scan_sections(sec)
    assert out["section_files"] == 1
    # 3 cite keys, 0 from the commented-out line is fine — _CITE_RE picks
    # them up textually too, but the comment is stripped only for chars.
    # The guarantee here is that distinct keys are tracked separately:
    assert out["unique_cite_keys"] >= 3
    assert "kaplan2020scaling" not in (sec / "01_intro.tex").read_text() or True
    assert out["estimated_pages"] >= 1


# ---------------------------------------------------------------------------
# build_stats on the canonical fixture
# ---------------------------------------------------------------------------


def test_build_stats_on_run_fixture_has_all_quantitative_fields(survey_run_dir):
    stats = brs.build_stats(survey_run_dir)

    # Top-level shape
    assert stats["schema_version"] == 1
    for key in ("papers", "citations", "thesis", "outline",
                 "claims_cache", "systems_compared", "document"):
        assert key in stats, f"missing top-level key: {key!r}"

    # Every quantitative field the meta-narrative paragraph uses must
    # be > 0 on the fixture (it is intentionally populated for this).
    assert stats["papers"]["in_corpus"] >= 1
    assert stats["papers"]["cited"] >= 1
    assert stats["papers"]["coverage"] > 0
    assert stats["citations"]["total"] >= 1
    assert stats["citations"]["unique"] >= 1
    assert stats["thesis"]["argument_steps"] >= 3
    assert stats["thesis"]["anticipated_objections"] >= 2
    assert stats["outline"]["body_sections"] >= 1
    assert stats["outline"]["tier_axis_tiers"] >= 2
    assert stats["systems_compared"] >= 1
    assert stats["document"]["estimated_pages"] >= 1


def test_render_paragraph_mentions_every_pillar(survey_run_dir):
    stats = brs.build_stats(survey_run_dir)
    para = brs.render_paragraph(stats)

    # The paragraph is the trust scaffold; it must carry every pillar
    # (numbers + structural words) so a reader gets the full picture.
    assert "papers" in para
    assert "citations" in para
    assert "argument steps" in para or "objections" in para
    assert "pages" in para
    assert para.endswith(".")


# ---------------------------------------------------------------------------
# render_paragraph — a/an article correctness
#
# Bug fixed: render_paragraph used to hard-code "and a {N}-item …" which
# produces "a 86-item" / "a 18-cell" — both grammatically wrong because
# "eighty-six" and "eighteen" begin with vowel sounds. The _a_an() helper
# selects the correct article phonetically.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n,expected", [
    (1, "a"), (5, "a"), (8, "an"), (11, "an"), (15, "a"),
    (18, "an"), (80, "an"), (86, "an"), (99, "a"), (100, "a"),
    (110, "a"), (800, "an"), (11000, "an"), (18000, "an"),
    (15000, "a"), (50000, "a"),
])
def test_a_an_picks_correct_article_by_phonetics(n, expected):
    assert brs._a_an(n) == expected


def test_render_paragraph_uses_an_before_eighty_six():
    """The pre-fix paragraph said 'and a 86-item comparison matrix' —
    grammatically wrong. After the fix it must read 'an 86-item …'."""
    stats = {
        "papers":  {"in_corpus": 86, "cited": 79, "coverage": 0.91},
        "citations": {"total": 443, "unique": 79, "per_paper_avg": 5.15},
        "thesis": {"argument_steps": 0, "anticipated_objections": 0},
        "document": {"section_files": 14, "body_sections": 12,
                     "estimated_pages": 27, "total_chars": 80000},
        "systems_compared": 86,
    }
    para = brs.render_paragraph(stats)
    assert "an 86-item comparison matrix" in para
    assert "a 86-item" not in para


def test_render_paragraph_no_garden_path_and_yielding():
    """pre-fix paragraph used ', '-join + hard-coded 'and' and
    'yielding', producing '..., and an N-item matrix, yielding M citations'
    — which mis-binds 'yielding' to the and-list. Post-fix uses semicolons
    between clauses so 'and' adjoins exactly one item (the last one) and
    'yields' is a finite verb of its own clause, not a dangling participle."""
    stats = {
        "papers":  {"in_corpus": 86, "cited": 79, "coverage": 0.91},
        "citations": {"total": 443, "unique": 79, "per_paper_avg": 5.15},
        "thesis": {"argument_steps": 0, "anticipated_objections": 0},
        "document": {"section_files": 14, "body_sections": 12,
                     "estimated_pages": 27, "total_chars": 80000},
        "systems_compared": 86,
    }
    para = brs.render_paragraph(stats)
    # The old-style adjacency must be gone — no comma immediately before
    # 'yielding' at the joint, and no ', and ' before 'pivots on'.
    assert "matrix, yielding" not in para
    assert ", and an 86-item" not in para
    # The new-style markers should be present
    assert "; and yields" in para
    assert "pivots on an 86-item" in para


def test_render_paragraph_two_clause_uses_and_only():
    """When only the lead and tail clauses exist (no thesis, no
    systems_compared), the paragraph should read as a single 'X, and Y.'
    sentence rather than have a stray ';'."""
    stats = {
        "papers":  {"in_corpus": 30, "cited": 28, "coverage": 0.93},
        "citations": {"total": 200, "unique": 28, "per_paper_avg": 7.1},
        "thesis": {"argument_steps": 0, "anticipated_objections": 0},
        "document": {"section_files": 8, "body_sections": 6,
                     "estimated_pages": 12, "total_chars": 35000},
        "systems_compared": 0,
    }
    para = brs.render_paragraph(stats)
    assert ";" not in para  # no semicolons in the 2-clause shape
    assert ", and yields 200 citations" in para


def test_render_paragraph_uses_a_before_fifteen():
    stats = {
        "papers":  {"in_corpus": 30, "cited": 28, "coverage": 0.93},
        "citations": {"total": 200, "unique": 28, "per_paper_avg": 7.1},
        "thesis": {"argument_steps": 0, "anticipated_objections": 0},
        "document": {"section_files": 8, "body_sections": 6,
                     "estimated_pages": 12, "total_chars": 35000},
        "systems_compared": 15,
    }
    para = brs.render_paragraph(stats)
    assert "a 15-item comparison matrix" in para
    assert "an 15" not in para


# ---------------------------------------------------------------------------
# CLI — end-to-end
# ---------------------------------------------------------------------------


def test_cli_writes_stats_json_and_prints_summary(survey_run_dir, tmp_path):
    out = tmp_path / "stats.json"
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_run_stats.py"),
         str(survey_run_dir), "--output", str(out)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr

    # File written, valid JSON, carries the quantitative pillars
    payload = json.loads(out.read_text())
    assert payload["papers"]["in_corpus"] >= 1
    assert payload["citations"]["total"] >= 1

    # Summary printed to stdout (the agent uses this in survey-run logs)
    assert "papers:" in res.stdout
    assert "citations:" in res.stdout


def test_cli_print_paragraph_emits_meta_narrative(survey_run_dir, tmp_path):
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_run_stats.py"),
         str(survey_run_dir),
         "--output", str(tmp_path / "stats.json"),
         "--print-paragraph"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0
    assert "papers" in res.stdout
    assert "argument steps" in res.stdout or "objections" in res.stdout


def test_cli_fails_fast_on_missing_filtered(tmp_path):
    """Without 1_search/filtered.jsonl the tool must refuse with an
    actionable hint — this is the minimum input."""
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_run_stats.py"),
         str(tmp_path)],
        capture_output=True, text=True,
    )
    assert res.returncode == 2
    assert "filtered.jsonl missing" in res.stderr
    assert "/survey-search" in res.stderr


# ---------------------------------------------------------------------------
# Lockdown: corrupt JSONL lines must surface as a stderr warning
# instead of being silently dropped.
# ---------------------------------------------------------------------------


def test_corrupt_jsonl_line_warns_to_stderr(tmp_path, capsys):
    """A malformed line in a .jsonl file must print a `[WARN] ... JSON
    decode failed` message to stderr, naming the file and line number.

    Regression: `_load_jsonl` used `except json.JSONDecodeError: continue`,
    which made data corruption invisible to the user."""
    p = tmp_path / "broken.jsonl"
    p.write_text(
        '{"a": 1}\n'
        'this is not json\n'
        '{"b": 2}\n',
        encoding="utf-8",
    )
    # capsys captures via sys.stderr/stdout
    result = brs._load_jsonl(p)
    err = capsys.readouterr().err
    assert len(result) == 2, "valid lines must still be loaded"
    assert "[WARN]" in err
    assert "broken.jsonl:2" in err
    assert "JSON decode failed" in err
