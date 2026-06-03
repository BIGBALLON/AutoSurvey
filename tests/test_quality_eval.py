"""Unit tests for the LLM-judge quality evaluation (tools/quality_eval.py).

The judging is the agent's job; these pin the deterministic scaffold: the
shipped rubric is well-formed, verdict validation catches bad input, the
weighted aggregate + 0-100 scaling are correct, the regression bar behaves,
and the prepare/score CLI round-trips.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import quality_eval as qe  # noqa: E402


def _rubric():
    return qe.load_rubric()


# ── shipped rubric is valid ─────────────────────────────────────────────────

def test_shipped_rubric_is_wellformed():
    r = _rubric()
    assert qe.validate_rubric(r) == []
    # the four edges carry the documented 70% of weight
    w = {d["id"]: d["weight"] for d in r["dimensions"]}
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert round(w["thesis"] + w["synthesis"] + w["insight"] + w["evidence"], 3) == 0.70


def test_validate_rubric_flags_bad_weights():
    bad = {"scale": {"min": 1, "max": 5},
           "dimensions": [{"id": "a", "weight": 0.4}, {"id": "b", "weight": 0.4}]}
    errs = qe.validate_rubric(bad)
    assert any("weights sum" in e for e in errs)


# ── verdict validation ──────────────────────────────────────────────────────

def test_validate_verdict_catches_missing_and_out_of_range():
    r = _rubric()
    ids = [d["id"] for d in r["dimensions"]]
    scores = {i: 4 for i in ids}
    scores[ids[0]] = 9          # out of range
    del scores[ids[1]]          # missing
    scores["bogus"] = 3         # unknown
    errs = qe.validate_verdict({"scores": scores}, r)
    assert any("out of range" in e for e in errs)
    assert any("missing score" in e for e in errs)
    assert any("unknown dimension" in e for e in errs)


def test_validate_verdict_accepts_complete():
    r = _rubric()
    scores = {d["id"]: 4 for d in r["dimensions"]}
    assert qe.validate_verdict({"scores": scores}, r) == []


# ── aggregation + bar ───────────────────────────────────────────────────────

def test_aggregate_all_fives_is_100_all_ones_is_0():
    r = _rubric()
    hi = {"scores": {d["id"]: 5 for d in r["dimensions"]}}
    lo = {"scores": {d["id"]: 1 for d in r["dimensions"]}}
    assert qe.aggregate(hi, r)["overall_100"] == 100.0
    assert qe.aggregate(lo, r)["overall_100"] == 0.0


def test_aggregate_is_weighted_not_plain_mean():
    r = _rubric()
    # 5 on the four heavy edges (0.70), 1 on the rest (0.30):
    heavy = {"thesis", "synthesis", "insight", "evidence"}
    scores = {d["id"]: (5 if d["id"] in heavy else 1) for d in r["dimensions"]}
    agg = qe.aggregate({"scores": scores}, r)
    # weighted mean = 0.70*5 + 0.30*1 = 3.8 -> (3.8-1)/4*100 = 70.0
    assert agg["weighted_mean"] == 3.8
    assert agg["overall_100"] == 70.0


def test_compare_bar_blocks_on_low_dimension_even_if_overall_ok():
    r = _rubric()
    scores = {d["id"]: 5 for d in r["dimensions"]}
    scores["evidence"] = 2                      # below per_dimension_min=3
    agg = qe.aggregate({"scores": scores}, r)
    bar = qe.compare_bar(agg, r)
    assert agg["overall_100"] >= bar["overall_min"]
    assert bar["overall_ok"] is True
    assert "evidence" in bar["dimensions_below_min"]
    assert bar["passed"] is False


# ── CLI round-trip ──────────────────────────────────────────────────────────

def test_cli_prepare_then_score_roundtrip(tmp_path: Path):
    run = tmp_path
    (run / "5_paper" / "sections").mkdir(parents=True)
    (run / "5_paper" / "sections" / "03_body.tex").write_text("Some prose.\n")
    (run / "2_thesis").mkdir(parents=True)
    (run / "2_thesis" / "thesis.json").write_text('{"thesis":"X beats Y."}')

    assert qe.main(["prepare", str(run)]) == 0
    packet = run / "6_verify" / "quality_eval_packet.md"
    template = run / "6_verify" / "quality_verdict.template.json"
    assert packet.exists() and template.exists()
    assert "Survey prose" in packet.read_text()

    r = _rubric()
    verdict = {"scores": {d["id"]: 4 for d in r["dimensions"]}}
    (run / "6_verify" / "quality_verdict.json").write_text(json.dumps(verdict))
    rc = qe.main(["score", str(run), "--strict"])
    assert rc == 0                       # all-4s clears the 70 bar
    out = json.loads((run / "6_verify" / "quality_eval.json").read_text())
    assert out["overall_100"] == 75.0    # (4-1)/4*100
    assert out["bar"]["passed"] is True


def test_cli_score_missing_verdict_returns_2(tmp_path: Path):
    (tmp_path / "5_paper" / "sections").mkdir(parents=True)
    assert qe.main(["score", str(tmp_path)]) == 2
