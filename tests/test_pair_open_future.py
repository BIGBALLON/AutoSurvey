"""Tests for tools/pair_open_future.py — auto-pair open-problem and
future-direction items so structural-template invariant 6 passes
deterministically.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "pair_open_future.py"

sys.path.insert(0, str(REPO / "tools"))
import pair_open_future as pof  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _outline(op_items: list[dict] | None,
             fd_items: list[dict] | None) -> dict:
    sections: list[dict] = []
    if op_items is not None:
        sections.append({"id": "06_problems", "section_type": "open_problems",
                         "items": op_items})
    if fd_items is not None:
        sections.append({"id": "07_future", "section_type": "future_directions",
                         "items": fd_items})
    return {"sections": sections}


def _make_run(tmp_path: Path, outline: dict) -> Path:
    rd = tmp_path / "run"
    (rd / "4_outline").mkdir(parents=True)
    (rd / "4_outline" / "outline.json").write_text(
        json.dumps(outline, indent=2)
    )
    return rd


# ---------------------------------------------------------------------------
# tokens / jaccard primitives
# ---------------------------------------------------------------------------


def test_tokens_strips_stopwords_and_punctuation():
    toks = pof.tokens("The cognitive loop trap and its mitigation")
    assert "cognitive" in toks
    assert "loop" in toks
    assert "trap" in toks
    assert "the" not in toks
    assert "and" not in toks


def test_jaccard_zero_for_disjoint_sets():
    assert pof.jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_one_for_identical_sets():
    assert pof.jaccard({"a", "b", "c"}, {"a", "b", "c"}) == 1.0


def test_jaccard_intermediate_for_partial_overlap():
    s = pof.jaccard({"a", "b", "c"}, {"b", "c", "d"})
    assert 0.4 < s < 0.6  # 2/4 = 0.5


# ---------------------------------------------------------------------------
# pair() — greedy assignment with Jaccard threshold
# ---------------------------------------------------------------------------


def test_pair_matches_obvious_pairs_by_title_overlap():
    op = [
        {"id": "OP1", "title": "Cognitive loop trap"},
        {"id": "OP2", "title": "Context window limitations"},
    ]
    fd = [
        {"id": "FD1", "title": "Self-improving agents and context scaling"},
        {"id": "FD2", "title": "Termination heuristics for cognitive loops"},
    ]
    n_paired, _ = pof.pair(op, fd, min_jaccard=0.05)
    assert n_paired == 2
    assert op[0]["paired_direction_id"] == "FD2"  # 'cognitive loops'
    assert op[1]["paired_direction_id"] == "FD1"  # 'context'


def test_pair_skips_already_paired_items():
    op = [
        {"id": "OP1", "title": "Cognitive loop trap",
         "paired_direction_id": "FD_PRESET"},
        {"id": "OP2", "title": "Context window"},
    ]
    fd = [{"id": "FD_PRESET", "title": "Whatever"},
          {"id": "FD2", "title": "Long context windows"}]
    n_paired, _ = pof.pair(op, fd, min_jaccard=0.05)
    assert op[0]["paired_direction_id"] == "FD_PRESET"
    assert op[1]["paired_direction_id"] == "FD2"
    assert n_paired == 2


def test_pair_leaves_unpaired_below_threshold():
    op = [
        {"id": "OP1", "title": "Reproducibility under closed releases"},
        {"id": "OP2", "title": "Cost and accessibility"},
    ]
    fd = [
        {"id": "FD1", "title": "Quantum cryptography for blockchain"},
    ]
    _, diagnostics = pof.pair(op, fd, min_jaccard=0.30)
    # FD1 has zero overlap with either OP — both stay unpaired
    assert "paired_direction_id" not in op[0]
    assert "paired_direction_id" not in op[1]
    assert any("no FD match" in d for d in diagnostics)


def test_pair_one_to_one_no_double_assignment():
    """Even when one FD title scores highest for two OPs, only the top-scoring
    OP gets it; the other OP must use a different (or no) match."""
    op = [
        {"id": "OP1", "title": "Cognitive loops in agents"},
        {"id": "OP2", "title": "Cognitive loops in agents"},  # identical to OP1
    ]
    fd = [
        {"id": "FD1", "title": "Cognitive loops"},
        {"id": "FD2", "title": "Something else entirely"},
    ]
    pof.pair(op, fd, min_jaccard=0.05)
    paired = {op[0].get("paired_direction_id"), op[1].get("paired_direction_id")}
    # Both OPs must point to *different* FDs (one to FD1, the other unpaired
    # because the second-best score is below threshold)
    assert "FD1" in paired
    # FD1 is used at most once
    fd1_uses = sum(1 for o in op if o.get("paired_direction_id") == "FD1")
    assert fd1_uses == 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_writes_outline_back(tmp_path):
    rd = _make_run(tmp_path, _outline(
        op_items=[
            {"id": "OP1", "title": "Cognitive loop trap"},
            {"id": "OP2", "title": "Context window limitations"},
            {"id": "OP3", "title": "Reproducibility under closed releases"},
            {"id": "OP4", "title": "Novelty evaluation"},
            {"id": "OP5", "title": "Safety and ethics"},
        ],
        fd_items=[
            {"id": "FD1", "title": "Termination of cognitive loops"},
            {"id": "FD2", "title": "Long context windows"},
            {"id": "FD3", "title": "Reproducibility frameworks"},
            {"id": "FD4", "title": "Automated novelty assessment"},
            {"id": "FD5", "title": "Ethical safety guardrails"},
        ],
    ))
    res = subprocess.run(
        [sys.executable, str(TOOL), str(rd)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    out = json.loads((rd / "4_outline" / "outline.json").read_text())
    op_sec = next(s for s in out["sections"]
                  if s.get("section_type") == "open_problems")
    paired = [it.get("paired_direction_id") for it in op_sec["items"]]
    assert all(p for p in paired), f"all OP items should pair, got {paired}"


def test_cli_dry_run_does_not_write(tmp_path):
    rd = _make_run(tmp_path, _outline(
        op_items=[{"id": "OP1", "title": "Cognitive loops"}],
        fd_items=[{"id": "FD1", "title": "Cognitive loops"}],
    ))
    before = (rd / "4_outline" / "outline.json").read_text()
    res = subprocess.run(
        [sys.executable, str(TOOL), str(rd), "--dry-run"],
        capture_output=True, text=True,
    )
    # Identical titles ⇒ jaccard=1.0 ≥ 0.10 ⇒ 1/1 paired ⇒ exit 0
    assert res.returncode == 0, res.stdout + res.stderr
    after = (rd / "4_outline" / "outline.json").read_text()
    assert before == after  # dry-run preserves the file on disk


def test_cli_fails_when_pairing_below_80_percent(tmp_path):
    rd = _make_run(tmp_path, _outline(
        op_items=[
            {"id": "OP1", "title": "Cognitive loop trap"},
            {"id": "OP2", "title": "Context windows"},
            {"id": "OP3", "title": "Disjoint topic alpha"},
            {"id": "OP4", "title": "Disjoint topic beta"},
            {"id": "OP5", "title": "Disjoint topic gamma"},
        ],
        fd_items=[
            {"id": "FD1", "title": "Quantum cryptography blockchain"},
            {"id": "FD2", "title": "Genomic CRISPR techniques"},
            {"id": "FD3", "title": "Robotics policy gradients"},
            {"id": "FD4", "title": "Optical neural networks"},
            {"id": "FD5", "title": "Ferroelectric memristors"},
        ],
    ))
    res = subprocess.run(
        [sys.executable, str(TOOL), str(rd), "--min-jaccard", "0.30"],
        capture_output=True, text=True,
    )
    # No OP↔FD pair clears the 0.30 jaccard threshold ⇒ 0/5 paired ⇒ exit 1
    assert res.returncode == 1
    assert "below 80%" in res.stderr


def test_cli_errors_if_section_missing(tmp_path):
    rd = _make_run(tmp_path, _outline(
        op_items=[{"id": "OP1", "title": "X"}],
        fd_items=None,
    ))
    res = subprocess.run(
        [sys.executable, str(TOOL), str(rd)],
        capture_output=True, text=True,
    )
    assert res.returncode == 1
    assert "must declare both" in res.stderr
