"""Tests for tools/scaffold_related_surveys.py — emit the
'Relationship to existing surveys' subsection stub.

Cover both the candidate-detection heuristic and the LaTeX scaffold +
inject behaviour.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "scaffold_related_surveys.py"

sys.path.insert(0, str(REPO / "tools"))
import scaffold_related_surveys as srs  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_run(tmp_path: Path, records: list[dict]) -> Path:
    rd = tmp_path / "run"
    (rd / "1_search").mkdir(parents=True)
    (rd / "1_search" / "filtered.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n"
    )
    return rd


def _record(cite_key: str, title: str, *, type_: str = "", year: int = 2024,
            cited_by_count: int = 0, authors: list | None = None,
            author_count: int = 1) -> dict:
    return {
        "cite_key": cite_key, "paper_id": cite_key,
        "title": title, "type": type_, "year": year,
        "cited_by_count": cited_by_count,
        "authors": authors or [{"name": "First Author"}],
        "author_count": author_count,
    }


# ---------------------------------------------------------------------------
# looks_like_survey heuristic
# ---------------------------------------------------------------------------


def test_looks_like_survey_matches_title_keywords():
    assert srs.looks_like_survey(_record("a", "A Survey of LLM Agents"))
    assert srs.looks_like_survey(_record("b", "A Review of Pretraining"))
    assert srs.looks_like_survey(_record("c", "An Overview of MoE Models"))
    assert srs.looks_like_survey(_record("d", "A Tutorial on Diffusion Models"))


def test_looks_like_survey_matches_type_field():
    """An OpenAlex `type=review` record matches even without 'survey' in title."""
    assert srs.looks_like_survey(
        _record("a", "Foundations of Cognition", type_="review")
    )


def test_looks_like_survey_rejects_non_surveys():
    assert not srs.looks_like_survey(_record("a", "Attention Is All You Need"))
    assert not srs.looks_like_survey(_record("b", "Llama 2"))
    assert not srs.looks_like_survey(_record("c", "Mixture of Experts at Scale"))


def test_looks_like_survey_filters_prisma_methodology():
    """PRISMA-S is methodology, not an adjacent literature survey."""
    assert not srs.looks_like_survey(_record(
        "rethlefsen2021prismas",
        "PRISMA-S: an extension to the PRISMA Statement for "
        "Reporting Literature Searches",
    ))


def test_looks_like_survey_filters_huge_anthologies():
    """Conference proceedings dressed as 'A Survey of ...' usually have
    50+ authors. Skip them — the scaffold wants focused adjacent surveys."""
    rec = _record("a", "A Survey of Recent NLP Work", author_count=80)
    assert not srs.looks_like_survey(rec)


# ---------------------------------------------------------------------------
# rank_candidates
# ---------------------------------------------------------------------------


def test_rank_candidates_orders_by_citation_then_year():
    cands = [
        _record("a", "Survey A", cited_by_count=10, year=2023),
        _record("b", "Survey B", cited_by_count=100, year=2022),
        _record("c", "Survey C", cited_by_count=10, year=2024),
    ]
    ordered = [r["cite_key"] for r in srs.rank_candidates(cands)]
    assert ordered == ["b", "c", "a"]  # b first (100 cites);
    # then c over a because 2024 > 2023 at tied citation_count


# ---------------------------------------------------------------------------
# render_scaffold
# ---------------------------------------------------------------------------


def test_render_scaffold_includes_subsection_title_and_citet():
    top = [
        _record("smith2024", "Survey of LLM Agents", cited_by_count=100,
                authors=[{"name": "Alice Smith"}]),
        _record("doe2023", "A Review of Code Agents", cited_by_count=50,
                authors=[{"name": "Bob Doe"}]),
    ]
    rendered = srs.render_scaffold(top)
    assert "\\subsection{Relationship to existing surveys}" in rendered
    assert "\\citet{smith2024}" in rendered
    assert "\\citet{doe2023}" in rendered
    assert srs.SCAFFOLD_BEGIN in rendered
    assert srs.SCAFFOLD_END in rendered
    assert "Smith (2024)" in rendered  # author/year hint comment


def test_render_scaffold_handles_missing_author():
    top = [_record("x", "A Survey", authors=[])]
    rendered = srs.render_scaffold(top)
    assert "\\citet{x}" in rendered  # cite_key fallback


# ---------------------------------------------------------------------------
# inject — idempotent splice into 02_background.tex
# ---------------------------------------------------------------------------


def test_inject_creates_target_when_missing(tmp_path):
    target = tmp_path / "02_background.tex"
    scaffold = srs.render_scaffold([_record("a", "Survey A")])
    modified = srs.inject(scaffold, target)
    assert modified
    assert target.exists()
    assert "\\subsection{Relationship to existing surveys}" in target.read_text()


def test_inject_appends_when_no_existing_marker(tmp_path):
    target = tmp_path / "02_background.tex"
    target.write_text("\\section{Background}\nSome existing prose.\n")
    scaffold = srs.render_scaffold([_record("a", "Survey A")])
    modified = srs.inject(scaffold, target)
    assert modified
    text = target.read_text()
    assert "Some existing prose." in text  # original preserved
    assert srs.SCAFFOLD_BEGIN in text


def test_inject_replaces_existing_scaffold(tmp_path):
    target = tmp_path / "02_background.tex"
    old_scaffold = srs.render_scaffold([_record("old", "Old Survey")])
    target.write_text(
        "\\section{Background}\nPreamble.\n\n" + old_scaffold
        + "\nTrailing prose.\n"
    )
    new_scaffold = srs.render_scaffold([_record("new", "New Survey")])
    modified = srs.inject(new_scaffold, target)
    assert modified
    text = target.read_text()
    assert "\\citet{new}" in text
    assert "\\citet{old}" not in text
    assert "Preamble." in text
    assert "Trailing prose." in text  # the framing prose is untouched


def test_inject_idempotent_when_scaffold_unchanged(tmp_path):
    target = tmp_path / "02_background.tex"
    scaffold = srs.render_scaffold([_record("a", "Survey A")])
    target.write_text("\\section{Background}\n\n" + scaffold)
    modified = srs.inject(scaffold, target)
    assert not modified


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_cli_emits_warning_when_too_few_candidates(tmp_path):
    rd = _make_run(tmp_path, [
        _record("paper1", "Attention Is All You Need"),
        _record("paper2", "Llama 2: Open Foundation Models"),
    ])
    res = subprocess.run(
        [sys.executable, str(TOOL), str(rd)],
        capture_output=True, text=True,
    )
    assert res.returncode == 1
    assert "fewer than 3 candidates" in res.stderr


def test_cli_succeeds_with_three_candidates(tmp_path):
    rd = _make_run(tmp_path, [
        _record("survey1", "A Survey of LLM Agents", cited_by_count=200),
        _record("survey2", "A Review of Code Agents", cited_by_count=100),
        _record("survey3", "An Overview of Scientific Discovery Agents",
                cited_by_count=50),
        _record("paper1", "Attention Is All You Need"),
    ])
    res = subprocess.run(
        [sys.executable, str(TOOL), str(rd)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    out = (rd / "5_paper" / "sections"
           / "02_background.related_surveys.tex").read_text()
    assert "\\citet{survey1}" in out
    assert "\\citet{survey2}" in out
    assert "\\citet{survey3}" in out


def test_cli_inject_splices_into_background_tex(tmp_path):
    rd = _make_run(tmp_path, [
        _record(f"survey{i}", f"A Survey of Topic {i}",
                cited_by_count=200 - i)
        for i in range(1, 5)
    ])
    bg = rd / "5_paper" / "sections" / "02_background.tex"
    bg.parent.mkdir(parents=True)
    bg.write_text("\\section{Background}\nPreamble paragraph.\n")

    res = subprocess.run(
        [sys.executable, str(TOOL), str(rd), "--inject"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    text = bg.read_text()
    assert "Preamble paragraph." in text
    assert "\\subsection{Relationship to existing surveys}" in text
    assert "\\citet{survey1}" in text
