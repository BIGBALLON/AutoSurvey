"""Tests for tools/validate_artifacts.py — schema-class audit.

Covers four check groups against the canonical fixture (positive path)
and pollutes the fixture in well-defined ways to exercise each error
branch (negative path):

  1. thesis_schema       — contestable text, candidates, argument_steps,
                           anticipated_objections
  2. claims_schema       — atomic_claims schema, claim_type enum, quote
                           length, duplicate claim_id
  3. cite_key_closed_set — cite_key in claims_cache must exist in
                           filtered.jsonl
  4. decision_summary    — availability enum, ≤4-word cells warning,
                           tier in outline.tier_axis
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _write_jsonl(p: Path, records: list[dict]) -> None:
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _run_cli(run_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "validate_artifacts.py"),
         str(run_dir), *extra],
        capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# Positive path: clean fixture should validate green
# ---------------------------------------------------------------------------


def test_clean_fixture_passes(survey_run_dir):
    """The survey_run_dir fixture must validate without ERRORs."""
    res = _run_cli(survey_run_dir)
    assert res.returncode == 0, (
        f"clean fixture must pass, got rc={res.returncode}\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )


# ---------------------------------------------------------------------------
# 1. thesis_schema
# ---------------------------------------------------------------------------


def test_thesis_argument_steps_too_few(survey_run_dir):
    """argument_steps < 3 → ERROR per thesis-contract."""
    p = survey_run_dir / "2_thesis" / "thesis.json"
    doc = json.loads(p.read_text())
    doc["argument_steps"] = doc["argument_steps"][:2]   # 2 only
    p.write_text(json.dumps(doc, indent=2))

    res = _run_cli(survey_run_dir)
    assert res.returncode == 1
    assert "argument_steps" in res.stdout


def test_thesis_anticipated_objections_too_few(survey_run_dir):
    """anticipated_objections < 2 → ERROR."""
    p = survey_run_dir / "2_thesis" / "thesis.json"
    doc = json.loads(p.read_text())
    doc["anticipated_objections"] = doc["anticipated_objections"][:1]
    p.write_text(json.dumps(doc, indent=2))

    res = _run_cli(survey_run_dir)
    assert res.returncode == 1
    assert "anticipated_objections" in res.stdout


def test_thesis_chosen_id_unknown(survey_run_dir):
    """thesis_id_chosen not in candidates → ERROR."""
    p = survey_run_dir / "2_thesis" / "thesis.json"
    doc = json.loads(p.read_text())
    doc["thesis_id_chosen"] = "Z"
    p.write_text(json.dumps(doc, indent=2))

    res = _run_cli(survey_run_dir)
    assert res.returncode == 1
    assert "thesis_id_chosen" in res.stdout


def test_thesis_uncontestable_text_warns_only(survey_run_dir):
    """A bland thesis (no comparative/judgment markers) raises WARN, not ERROR.
    --strict elevates the WARN to a failure."""
    p = survey_run_dir / "2_thesis" / "thesis.json"
    doc = json.loads(p.read_text())
    # Strip every contestable marker
    doc["thesis"] = "Pretraining is a topic with many interesting angles."
    p.write_text(json.dumps(doc, indent=2))

    res = _run_cli(survey_run_dir)
    assert res.returncode == 0   # warns only at default strictness
    assert "WARN" in res.stdout or "may not be contestable" in res.stdout

    res_strict = _run_cli(survey_run_dir, "--strict")
    assert res_strict.returncode == 1


# ---------------------------------------------------------------------------
# 2. claims_schema
# ---------------------------------------------------------------------------


def test_claim_type_enum_violation(survey_run_dir):
    """claim_type outside the closed set {empirical/theoretical/methodological/critique}
    → ERROR."""
    p = survey_run_dir / "1_search" / "claims_cache.jsonl"
    records = _read_jsonl(p)
    records[0]["atomic_claims"][0]["claim_type"] = "vibes"
    _write_jsonl(p, records)

    res = _run_cli(survey_run_dir)
    assert res.returncode == 1
    assert "claim_type" in res.stdout


def test_duplicate_claim_id_is_error(survey_run_dir):
    """Two atomic claims sharing claim_id → ERROR."""
    p = survey_run_dir / "1_search" / "claims_cache.jsonl"
    records = _read_jsonl(p)
    records[1]["atomic_claims"][0]["claim_id"] = records[0]["atomic_claims"][0]["claim_id"]
    _write_jsonl(p, records)

    res = _run_cli(survey_run_dir)
    assert res.returncode == 1
    assert "duplicate claim_id" in res.stdout


# ---------------------------------------------------------------------------
# 3. cite_key closed-set
# ---------------------------------------------------------------------------


def test_cite_key_not_in_filtered_is_error(survey_run_dir):
    """A claim record with cite_key absent from filtered.jsonl → ERROR."""
    p = survey_run_dir / "1_search" / "claims_cache.jsonl"
    records = _read_jsonl(p)
    records.append({
        "cite_key": "ghost2099phantom",
        "what_paper_argues": "phantom paper",
        "atomic_claims": [
            {"claim_id":   "ghost-1",
             "claim_type": "empirical",
             "anchor":     "x",
             "quote":      "this paper does not exist in filtered.jsonl"},
            {"claim_id":   "ghost-2",
             "claim_type": "empirical",
             "anchor":     "x",
             "quote":      "ditto, the second atomic claim is a phantom too"},
        ],
    })
    _write_jsonl(p, records)

    res = _run_cli(survey_run_dir)
    assert res.returncode == 1
    assert "ghost2099phantom" in res.stdout
    assert "closed-set" in res.stdout or "filtered.jsonl" in res.stdout


# ---------------------------------------------------------------------------
# 4. decision_summary
# ---------------------------------------------------------------------------


def test_availability_enum_violation(survey_run_dir):
    """_decision_summary.availability outside the closed set → ERROR."""
    p = survey_run_dir / "1_search" / "cards.jsonl"
    records = _read_jsonl(p)
    records[0]["_decision_summary"]["availability"] = "maybe-open"
    _write_jsonl(p, records)

    res = _run_cli(survey_run_dir)
    assert res.returncode == 1
    assert "availability" in res.stdout


def test_decision_summary_long_cell_warns(survey_run_dir):
    """A > 4-word value in a non-availability field is WARN, not ERROR."""
    p = survey_run_dir / "1_search" / "cards.jsonl"
    records = _read_jsonl(p)
    records[0]["_decision_summary"]["one_line_role"] = (
        "Extremely thorough power-law fitting across many regimes"  # 7 words
    )
    _write_jsonl(p, records)

    res = _run_cli(survey_run_dir)
    assert res.returncode == 0       # WARN only at default
    assert "exceeds 4 words" in res.stdout

    res_strict = _run_cli(survey_run_dir, "--strict")
    assert res_strict.returncode == 1


def test_decision_summary_tier_unknown_warns(survey_run_dir):
    """A tier id not in outline.tier_axis.tiers → WARN."""
    p = survey_run_dir / "1_search" / "cards.jsonl"
    records = _read_jsonl(p)
    records[0]["_decision_summary"]["tier"] = "T99"
    _write_jsonl(p, records)

    res = _run_cli(survey_run_dir)
    assert res.returncode == 0       # WARN only
    assert "T99" in res.stdout
    assert "tier" in res.stdout


# ---------------------------------------------------------------------------
# 5. decision_summary noise collapse for runs without thesis.json
# ---------------------------------------------------------------------------


def test_run_without_thesis_collapses_decision_summary_noise(survey_run_dir):
    """When thesis.json is missing AND no card has _decision_summary,
    the per-card "missing _decision_summary" warnings must be collapsed
    into a single summary WARN to avoid hundreds of low-signal lines."""
    # Drop thesis.json -> decision-mode unavailable
    (survey_run_dir / "2_thesis" / "thesis.json").unlink()

    # Strip _decision_summary from every card
    p = survey_run_dir / "1_search" / "cards.jsonl"
    records = _read_jsonl(p)
    for r in records:
        r.pop("_decision_summary", None)
        r.pop("decision_summary", None)
    _write_jsonl(p, records)

    res = _run_cli(survey_run_dir)
    assert res.returncode == 0  # WARN only

    # Exactly one collapsed line, not N per card
    out = res.stdout
    per_card = out.count("no _decision_summary")
    assert per_card == 0, (
        f"per-card warnings should be collapsed; got {per_card} lines"
    )
    assert "all 3 cards lack _decision_summary" in out
    assert "no thesis.json" in out


def test_thesis_run_keeps_per_card_decision_summary_warnings(survey_run_dir):
    """In a thesis-driven run (thesis.json present), per-card decision_summary
    checks must still fire normally — the noise collapse is only for runs
    without thesis.json."""
    p = survey_run_dir / "1_search" / "cards.jsonl"
    records = _read_jsonl(p)
    records[0].pop("_decision_summary", None)  # remove from one card only
    _write_jsonl(p, records)

    res = _run_cli(survey_run_dir)
    assert res.returncode == 0
    # One per-card WARN should still appear (not the collapsed form)
    assert "no _decision_summary" in res.stdout
    assert "no thesis.json" not in res.stdout


# ---------------------------------------------------------------------------
# 6. non_obvious_findings — optional thesis schema field
# ---------------------------------------------------------------------------


def test_thesis_no_non_obvious_findings_is_silent(survey_run_dir):
    """Thesis without the optional field must validate clean (back-compat)."""
    res = _run_cli(survey_run_dir)
    assert res.returncode == 0
    assert "non_obvious_findings" not in res.stdout


def test_thesis_non_obvious_findings_well_formed_passes(survey_run_dir):
    p = survey_run_dir / "2_thesis" / "thesis.json"
    doc = json.loads(p.read_text())
    doc["non_obvious_findings"] = [
        {"finding": "Token budget dominates parameter count above 1e23 FLOPs.",
         "section_id": "02_body"},
    ]
    p.write_text(json.dumps(doc, indent=2))

    res = _run_cli(survey_run_dir)
    assert res.returncode == 0
    assert "non_obvious_findings" not in res.stdout  # no ERROR


def test_thesis_non_obvious_findings_missing_finding_text_is_error(survey_run_dir):
    p = survey_run_dir / "2_thesis" / "thesis.json"
    doc = json.loads(p.read_text())
    doc["non_obvious_findings"] = [{"section_id": "02_body"}]  # missing finding
    p.write_text(json.dumps(doc, indent=2))

    res = _run_cli(survey_run_dir)
    assert res.returncode == 1
    assert "non_obvious_findings[0].finding" in res.stdout


def test_thesis_non_obvious_findings_wrong_type_is_error(survey_run_dir):
    """Field present but not a list → ERROR."""
    p = survey_run_dir / "2_thesis" / "thesis.json"
    doc = json.loads(p.read_text())
    doc["non_obvious_findings"] = "not a list"
    p.write_text(json.dumps(doc, indent=2))

    res = _run_cli(survey_run_dir)
    assert res.returncode == 1
    assert "non_obvious_findings must be a list" in res.stdout


# ---------------------------------------------------------------------------
# Edge-case lockdown: non-existent run_dir + corrupted JSONL
# ---------------------------------------------------------------------------


def test_nonexistent_run_dir_exits_2(tmp_path):
    """A run_dir that doesn't exist must exit 2 (input error), not 0.

    Regression: silently exiting 0 made it possible for shell pipelines
    to mistake 'no run dir' for 'audit passed'."""
    missing = tmp_path / "no-such-run"
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "validate_artifacts.py"),
         str(missing)],
        capture_output=True, text=True,
    )
    assert res.returncode == 2, (res.returncode, res.stderr)
    assert "not found" in res.stderr


def test_corrupted_claims_jsonl_surfaces_as_error(tmp_path):
    """A claims_cache.jsonl with a malformed line must be reported as
    an ERROR finding, not silently dropped.

    Regression: `except json.JSONDecodeError: pass` used to swallow
    decode failures so corruption could pass schema audit."""
    run_dir = tmp_path / "broken_run"
    (run_dir / "1_search").mkdir(parents=True)
    (run_dir / "1_search" / "claims_cache.jsonl").write_text(
        '{"cite_key": "a2024", "atomic_claims": []}\n'
        'this line is not json\n'
        '{"cite_key": "b2024", "atomic_claims": []}\n',
        encoding="utf-8",
    )
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "validate_artifacts.py"),
         str(run_dir)],
        capture_output=True, text=True,
    )
    assert res.returncode == 1, (res.returncode, res.stderr)
    assert "JSON decode failed" in res.stdout
    assert "claims_cache.jsonl:2" in res.stdout
