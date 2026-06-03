"""End-to-end dry-run smoke test for the Polishing-phase tool chain.

Threads together every deterministic Python tool that runs after
Phase 2 (Arguing) finishes — i.e. the tool chain invoked by
``/survey-verify`` and ``compile`` substeps in survey-run/SKILL.md:

  1. validate_artifacts    schema audit                 (exit 0)
  2. audit_writing         writing-quality audit        (exit 0 at polished)
  3. gen_taxonomy_tikz     matrix layout from tier_axis (writes 00_taxonomy.tex)
  4. build_dimension_tables --mode decision             (writes *_decision.tex)
  5. build_evidence_dashboard                           (writes survey.evidence.html)
  6. verify_survey_audits.sh                            (compile gate, exit 0)

The fixture provides mock-LLM artifacts (thesis/claims/cards/sections)
in shapes that the real /survey-thesis, /survey-write etc. would emit
in Phase 1+2 — so this test exercises the deterministic glue without
needing a live Claude/Codex agent.

A separate "FAIL path" test pollutes the run and asserts the gate
correctly blocks compile.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


# ---------------------------------------------------------------------------
# Helpers — each step runs the real CLI; tests assert exit codes + outputs
# ---------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path | None = None,
         env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, env=env)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_e2e_polishing_chain_passes(survey_run_dir, tmp_path):
    """Run the full Polishing tool chain on the fixture; everything must
    succeed and produce the expected artifacts."""

    # 1. validate_artifacts
    res1 = _run([sys.executable, str(TOOLS / "validate_artifacts.py"),
                 str(survey_run_dir)])
    assert res1.returncode == 0, (
        f"validate_artifacts FAILED:\n"
        f"stdout:\n{res1.stdout}\nstderr:\n{res1.stderr}"
    )

    # 2. audit_writing  (polished assurance — gate not enforced, exit 0)
    res2 = _run([sys.executable, str(TOOLS / "audit_writing.py"),
                 str(survey_run_dir), "--assurance", "polished"])
    assert res2.returncode == 0, (
        f"audit_writing FAILED:\n"
        f"stdout:\n{res2.stdout}\nstderr:\n{res2.stderr}"
    )

    # 2b. audit_writing at submission level — clean fixture must still pass
    # The minimal 4-section fixture cannot satisfy the 8 structural
    # invariants by construction (no cross-cutting matrix, no
    # annotated bib), so we pass --no-strict-template here. The
    # full-strength gate (with structural_template enforced) is
    # exercised by the bash compile-gate test below using a real
    # /survey-run output.
    res2b = _run([sys.executable, str(TOOLS / "audit_writing.py"),
                  str(survey_run_dir), "--assurance", "submission",
                  "--no-strict-template"])
    assert res2b.returncode == 0, (
        f"audit_writing(submission) FAILED on clean fixture:\n"
        f"stdout:\n{res2b.stdout}"
    )

    # 3. gen_taxonomy_tikz --layout matrix
    res3 = _run([sys.executable, str(TOOLS / "gen_taxonomy_tikz.py"),
                 str(survey_run_dir), "--layout", "matrix"])
    assert res3.returncode == 0, f"gen_taxonomy_tikz FAILED:\n{res3.stderr}"
    matrix_tex = survey_run_dir / "5_paper" / "figures" / "00_taxonomy.tex"
    assert matrix_tex.exists()
    matrix_content = matrix_tex.read_text()
    assert "Key Insight" in matrix_content
    # Every tier should be present
    for tier in ("T1", "T2", "T3"):
        assert tier in matrix_content

    # 4. build_dimension_tables --mode decision
    tables_dir = tmp_path / "tables"
    res4 = _run([sys.executable, str(TOOLS / "build_dimension_tables.py"),
                 "--mode", "decision",
                 "--cards", str(survey_run_dir / "1_search" / "cards.jsonl"),
                 "--outline", str(survey_run_dir / "4_outline" / "outline.json"),
                 "--output-dir", str(tables_dir)])
    assert res4.returncode == 0, f"build_dimension_tables FAILED:\n{res4.stderr}"
    decision_files = sorted(tables_dir.glob("*_decision.tex"))
    assert decision_files, f"no decision tables emitted; stdout: {res4.stdout}"

    # 4b. build_run_stats — quantitative meta-narrative. Must precede the
    # dashboard step so the meta-banner picks up the just-written stats.json.
    res4b = _run([sys.executable, str(TOOLS / "build_run_stats.py"),
                  str(survey_run_dir), "--print-paragraph"])
    assert res4b.returncode == 0, f"build_run_stats FAILED:\n{res4b.stderr}"
    stats_path = survey_run_dir / "5_paper" / "stats.json"
    assert stats_path.exists()
    stats = json.loads(stats_path.read_text())
    assert stats["schema_version"] == 1
    # The fixture has 4 argument_steps and 4 anticipated_objections — the
    # meta-banner should be able to render every pillar tile.
    assert stats["thesis"]["argument_steps"] >= 1
    assert stats["thesis"]["anticipated_objections"] >= 1
    # The CLI paragraph must use the correct article ('an' before
    # vowel-sound numbers like 86).
    assert " a 86-item " not in res4b.stdout, (
        f"render_paragraph used wrong article 'a' before vowel-sound number "
        f"(article-grammar regression):\n{res4b.stdout}"
    )

    # 5. build_evidence_dashboard
    html_path = survey_run_dir / "survey.evidence.html"
    res5 = _run([sys.executable, str(TOOLS / "build_evidence_dashboard.py"),
                 str(survey_run_dir), "--output", str(html_path)])
    assert res5.returncode == 0, f"build_evidence_dashboard FAILED:\n{res5.stderr}"
    assert html_path.exists()
    html = html_path.read_text()
    assert "<!doctype html>" in html
    assert "kaplan2020scaling" in html
    assert "https://arxiv.org/abs/" in html
    # Meta-banner from stats.json is rendered
    assert 'class="meta-banner"' in html
    assert 'class="tile"' in html

    # 6. verify_survey_audits.sh — the compile gate
    gate = TOOLS / "verify_survey_audits.sh"
    res6 = _run(["bash", str(gate), str(survey_run_dir),
                 "--assurance", "polished"])
    assert res6.returncode == 0, (
        f"compile gate FAILED on clean fixture:\n"
        f"stdout:\n{res6.stdout}\nstderr:\n{res6.stderr}"
    )
    # Gate output sanity
    assert "hard_gate = PASS" in res6.stdout
    assert "All checks green" in res6.stdout


# ---------------------------------------------------------------------------
# FAIL path — pollute the fixture, gate must block
# ---------------------------------------------------------------------------


def test_e2e_compile_gate_blocks_when_hard_gate_fails(survey_run_dir):
    """Pollute CITATION_VERIFY.json so hard_gate=FAIL → gate must exit 1."""
    cv = survey_run_dir / "6_verify" / "CITATION_VERIFY.json"
    doc = json.loads(cv.read_text())
    doc["hard_gate"] = "FAIL"
    doc["phantom_keys"] = ["fake_phantom_key_1", "fake_phantom_key_2"]
    cv.write_text(json.dumps(doc, indent=2))

    gate = TOOLS / "verify_survey_audits.sh"
    res = _run(["bash", str(gate), str(survey_run_dir), "--assurance", "polished"])
    assert res.returncode == 1
    assert "hard_gate = FAIL" in res.stdout
    assert "fake_phantom_key" in res.stdout
    assert "COMPILE BLOCKED" in res.stderr


def test_e2e_validate_artifacts_blocks_at_submission_with_thesis_break(survey_run_dir):
    """Break thesis schema (only 2 argument_steps) and confirm validate_artifacts
    reports it; combined with --assurance submission the bash gate elevates to FAIL."""
    p = survey_run_dir / "2_thesis" / "thesis.json"
    doc = json.loads(p.read_text())
    doc["argument_steps"] = doc["argument_steps"][:2]
    p.write_text(json.dumps(doc, indent=2))

    gate = TOOLS / "verify_survey_audits.sh"
    res = _run(["bash", str(gate), str(survey_run_dir), "--assurance", "submission"])
    # The submission level treats validate_artifacts ERRORs as FAIL
    assert res.returncode == 1
    assert "validate_artifacts" in res.stdout


def test_e2e_audit_writing_blocks_submission_when_pillars_broken(survey_run_dir):
    """Strip narrative pillars → audit_writing submission gate FAIL → bash gate
    elevates to overall FAIL at submission level."""
    intro = survey_run_dir / "5_paper" / "sections" / "01_introduction.tex"
    intro.write_text(r"\section{Intro}\nNothing notable here." )

    gate = TOOLS / "verify_survey_audits.sh"
    res = _run(["bash", str(gate), str(survey_run_dir), "--assurance", "submission"])
    assert res.returncode == 1
    assert "audit_writing" in res.stdout
