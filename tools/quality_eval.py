#!/usr/bin/env python3
"""quality_eval.py — LLM-as-judge quality evaluation (semantic, rubric-based).

The structural gates (audit_writing) verify a survey has the right *shape* —
5-anchor sections, a matrix, paired open/future, citation density. They cannot
tell whether the survey is actually *good*: a bland thesis, a paper-by-paper
recap with anchor comments, or an insight-free summary all pass structure.

This tool adds the missing semantic standard. Following the repo's pattern
(deterministic tools + the host agent as the LLM), it splits into:

  * ``prepare`` — assemble a self-contained judge packet (rubric + thesis +
    the full survey prose + structural stats) and an empty verdict template.
    The agent reads the packet and scores each rubric dimension 1-5 with a
    one-line rationale, writing ``6_verify/quality_verdict.json``.

  * ``score`` — validate that verdict against the rubric, compute the weighted
    overall (0-100), compare to the regression bar, and write
    ``6_verify/quality_eval.json`` (+ a printed scorecard). ``--strict``
    returns non-zero when below bar, so it can act as a quality gate / bar.

Rubric: skills/shared-references/quality-rubric.json (single source of truth;
weights sum to 1.0, four edges — thesis/synthesis/insight/evidence — carry 70%).

Exit codes: 0 ok · 1 below bar under --strict, or verdict invalid · 2 input error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def default_rubric_path() -> Path:
    return (Path(__file__).resolve().parent.parent
            / "skills" / "shared-references" / "quality-rubric.json")


# ── pure core (unit-tested) ─────────────────────────────────────────────────

def load_rubric(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else default_rubric_path()
    return json.loads(p.read_text(encoding="utf-8"))


def validate_rubric(rubric: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    dims = rubric.get("dimensions") or []
    if not dims:
        errors.append("rubric has no dimensions")
        return errors
    scale = rubric.get("scale") or {}
    if "min" not in scale or "max" not in scale or scale["min"] >= scale["max"]:
        errors.append("rubric.scale must have min < max")
    total_w = 0.0
    seen = set()
    for d in dims:
        if "id" not in d:
            errors.append("a dimension is missing 'id'")
            continue
        if d["id"] in seen:
            errors.append(f"duplicate dimension id: {d['id']}")
        seen.add(d["id"])
        w = d.get("weight")
        if not isinstance(w, (int, float)) or w < 0:
            errors.append(f"dimension {d['id']} has invalid weight")
        else:
            total_w += w
    if abs(total_w - 1.0) > 1e-6:
        errors.append(f"dimension weights sum to {total_w:.3f}, expected 1.0")
    return errors


def validate_verdict(verdict: Dict[str, Any], rubric: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    scores = verdict.get("scores")
    if not isinstance(scores, dict):
        return ["verdict has no 'scores' object"]
    lo, hi = rubric["scale"]["min"], rubric["scale"]["max"]
    rubric_ids = {d["id"] for d in rubric["dimensions"]}
    for did in rubric_ids:
        if did not in scores:
            errors.append(f"missing score for dimension '{did}'")
            continue
        v = scores[did]
        if not isinstance(v, (int, float)):
            errors.append(f"score for '{did}' is not a number")
        elif not (lo <= v <= hi):
            errors.append(f"score for '{did}' = {v} out of range [{lo},{hi}]")
    for did in scores:
        if did not in rubric_ids:
            errors.append(f"unknown dimension in verdict: '{did}'")
    return errors


def aggregate(verdict: Dict[str, Any], rubric: Dict[str, Any]) -> Dict[str, Any]:
    """Weighted 1-5 mean + a 0-100 overall. Assumes a validated verdict."""
    lo, hi = rubric["scale"]["min"], rubric["scale"]["max"]
    scores = verdict["scores"]
    weighted = 0.0
    per_dim: Dict[str, float] = {}
    for d in rubric["dimensions"]:
        s = float(scores[d["id"]])
        per_dim[d["id"]] = s
        weighted += d["weight"] * s
    overall_100 = round((weighted - lo) / (hi - lo) * 100, 1)
    return {
        "weighted_mean": round(weighted, 3),
        "overall_100": overall_100,
        "per_dimension": per_dim,
    }


def compare_bar(agg: Dict[str, Any], rubric: Dict[str, Any],
                overall_min: Optional[float] = None,
                per_dimension_min: Optional[float] = None) -> Dict[str, Any]:
    bar = rubric.get("bar") or {}
    o_min = overall_min if overall_min is not None else bar.get("overall_min", 0)
    d_min = per_dimension_min if per_dimension_min is not None else bar.get("per_dimension_min", 0)
    dims_below = sorted(
        did for did, s in agg["per_dimension"].items() if s < d_min
    )
    overall_ok = agg["overall_100"] >= o_min
    return {
        "overall_min": o_min,
        "per_dimension_min": d_min,
        "overall_ok": overall_ok,
        "dimensions_below_min": dims_below,
        "passed": overall_ok and not dims_below,
    }


# ── IO / shell ──────────────────────────────────────────────────────────────

def _read_sections(run_dir: Path) -> List[Tuple[str, str]]:
    sec_dir = run_dir / "5_paper" / "sections"
    out: List[Tuple[str, str]] = []
    if sec_dir.exists():
        for fp in sorted(sec_dir.glob("*.tex")):
            out.append((fp.stem, fp.read_text(encoding="utf-8", errors="replace")))
    return out


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def _verdict_template(rubric: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "scores": {d["id"]: None for d in rubric["dimensions"]},
        "rationale": {d["id"]: "" for d in rubric["dimensions"]},
        "overall_comment": "",
    }


def cmd_prepare(run_dir: Path, rubric: Dict[str, Any], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    sections = _read_sections(run_dir)
    thesis = _load_json(run_dir / "2_thesis" / "thesis.json") or {}
    stats = _load_json(run_dir / "5_paper" / "stats.json") or {}

    lo, hi = rubric["scale"]["min"], rubric["scale"]["max"]
    lines: List[str] = []
    lines.append("# Survey quality evaluation — judge packet")
    lines.append("")
    lines.append("You are a senior, skeptical reviewer. Score the survey below on each")
    lines.append(f"rubric dimension from {lo} (worst) to {hi} (best) using the anchored")
    lines.append("descriptors. Judge the *content*, not whether structure boxes are ticked.")
    lines.append("For each dimension give the integer score and a one-line rationale citing")
    lines.append("a concrete example (a section, a sentence, a missing comparison). Be")
    lines.append("calibrated and stingy: a 5 must be genuinely excellent.")
    lines.append("")
    lines.append(f"Write your verdict to `{run_dir / '6_verify' / 'quality_verdict.json'}`")
    lines.append("matching the template, then run `quality_eval.py score <run_dir>`.")
    lines.append("")
    lines.append("## Rubric")
    for d in rubric["dimensions"]:
        lines.append(f"\n### {d['id']} — {d['label']}  (weight {d['weight']})")
        lines.append(f"_{d['question']}_")
        for lvl in sorted(d["descriptors"], key=int):
            lines.append(f"- **{lvl}**: {d['descriptors'][lvl]}")
    if thesis.get("thesis"):
        lines.append("\n## Thesis under evaluation")
        lines.append(f"> {thesis['thesis']}")
        for st in thesis.get("argument_steps", []) or []:
            lines.append(f"- {st.get('step_id','')}: {st.get('claim','')}")
    if stats:
        doc = stats.get("document", {})
        pap = stats.get("papers", {})
        lines.append("\n## Structural stats (context only — do not score structure on these alone)")
        lines.append(f"- papers: {pap.get('in_corpus','?')} corpus / {pap.get('cited','?')} cited")
        lines.append(f"- body sections: {doc.get('body_sections','?')}, est. pages: {doc.get('estimated_pages','?')}")
    lines.append("\n## Survey prose")
    if not sections:
        lines.append("_(no sections found under 5_paper/sections — run the write phase first)_")
    for sid, body in sections:
        lines.append(f"\n<!-- ===== {sid} ===== -->")
        lines.append(body.rstrip())

    packet = out_dir / "quality_eval_packet.md"
    packet.write_text("\n".join(lines), encoding="utf-8")
    template = out_dir / "quality_verdict.template.json"
    template.write_text(json.dumps(_verdict_template(rubric), indent=2), encoding="utf-8")
    print(f"[quality_eval] packet  -> {packet}")
    print(f"[quality_eval] template-> {template}")
    print(f"[quality_eval] sections in packet: {len(sections)}")
    print("[quality_eval] next: fill the verdict (copy the template to "
          "quality_verdict.json), then: quality_eval.py score <run_dir>")
    return 0


def cmd_score(run_dir: Path, rubric: Dict[str, Any], verdict_path: Path,
              report_path: Path, overall_min: Optional[float],
              per_dim_min: Optional[float], strict: bool) -> int:
    verdict = _load_json(verdict_path)
    if verdict is None:
        print(f"ERROR: verdict not found or invalid JSON: {verdict_path}", file=sys.stderr)
        return 2
    verr = validate_verdict(verdict, rubric)
    if verr:
        print("ERROR: verdict does not match rubric:", file=sys.stderr)
        for e in verr:
            print(f"  - {e}", file=sys.stderr)
        return 1
    agg = aggregate(verdict, rubric)
    bar = compare_bar(agg, rubric, overall_min, per_dim_min)
    result = {
        "overall_100": agg["overall_100"],
        "weighted_mean": agg["weighted_mean"],
        "per_dimension": agg["per_dimension"],
        "bar": bar,
        "rationale": verdict.get("rationale", {}),
        "overall_comment": verdict.get("overall_comment", ""),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 56)
    print("quality_eval — semantic scorecard")
    print("=" * 56)
    label = {d["id"]: d["label"] for d in rubric["dimensions"]}
    for d in rubric["dimensions"]:
        did = d["id"]
        flag = "  ⚠below-min" if did in bar["dimensions_below_min"] else ""
        print(f"  {agg['per_dimension'][did]:.0f}/5  {label[did]:<22} (w {d['weight']:.2f}){flag}")
    print("-" * 56)
    print(f"  OVERALL: {agg['overall_100']:.1f}/100  (bar {bar['overall_min']})")
    print(f"  report -> {report_path}")
    if bar["passed"]:
        print("  RESULT: PASS")
        return 0
    why = []
    if not bar["overall_ok"]:
        why.append(f"overall {agg['overall_100']:.1f} < {bar['overall_min']}")
    if bar["dimensions_below_min"]:
        why.append("below-min: " + ", ".join(bar["dimensions_below_min"]))
    print("  RESULT: FAIL — " + "; ".join(why))
    return 1 if strict else 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("prepare", help="assemble the judge packet + verdict template")
    pp.add_argument("run_dir", type=str)
    pp.add_argument("--rubric", default=None)
    pp.add_argument("--out-dir", default=None)

    ps = sub.add_parser("score", help="validate verdict + aggregate + compare bar")
    ps.add_argument("run_dir", type=str)
    ps.add_argument("--rubric", default=None)
    ps.add_argument("--verdict", default=None)
    ps.add_argument("--report", default=None)
    ps.add_argument("--bar-overall", type=float, default=None)
    ps.add_argument("--bar-dimension", type=float, default=None)
    ps.add_argument("--strict", action="store_true")

    args = p.parse_args(argv)
    rubric = load_rubric(args.rubric)
    rerr = validate_rubric(rubric)
    if rerr:
        print("ERROR: invalid rubric:", file=sys.stderr)
        for e in rerr:
            print(f"  - {e}", file=sys.stderr)
        return 2

    run_dir = Path(args.run_dir).expanduser().resolve()
    if args.command == "prepare":
        out_dir = Path(args.out_dir) if args.out_dir else run_dir / "6_verify"
        return cmd_prepare(run_dir, rubric, out_dir)
    # score
    verdict_path = Path(args.verdict) if args.verdict else run_dir / "6_verify" / "quality_verdict.json"
    report_path = Path(args.report) if args.report else run_dir / "6_verify" / "quality_eval.json"
    return cmd_score(run_dir, rubric, verdict_path, report_path,
                     args.bar_overall, args.bar_dimension, args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
