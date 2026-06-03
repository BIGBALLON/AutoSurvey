#!/usr/bin/env python3
"""validate_artifacts.py — schema-class audit.

Unified validator that walks the artifacts produced by /survey-thesis,
/survey-write lazy claim mining, and /survey-write per-section cards.
Specifically:

  1. thesis_schema       — 2_thesis/thesis.json validity per
                           shared-references/thesis-contract.md
  2. claims_schema       — 1_search/claims_cache.jsonl validity per
                           shared-references/claims-contract.md
  3. cite_key_closed_set — every claim's cite_key ∈ filtered.jsonl,
                           and every \\cite{key} in sections .tex too
  4. decision_summary    — every card's _decision_summary present and
                           well-formed (≤4 words/cell, valid availability
                           enum, tier matches outline.tier_axis if present)

CLI:
    validate_artifacts.py <run_dir> [--strict] [--report PATH]

Exit codes:
    0  — all checks pass (or warnings only)
    1  — at least one check FAILed (--strict elevates warnings too)
    2  — input error (missing required file)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_jsonl(
    path: Path, *, errors: list[tuple[str, str]] | None = None
) -> list[dict[str, Any]]:
    """Load a .jsonl file. Missing file is treated as empty (caller's
    responsibility to decide whether that's OK). Lines that fail to
    parse as JSON are dropped, but the failure is recorded in
    ``errors`` if the caller passed a list — silently swallowing
    decode failures used to hide corruption from the audit."""
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            if errors is not None:
                errors.append((
                    "ERROR",
                    f"{path.name}:{lineno} JSON decode failed: {exc.msg}",
                ))
    return out


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# 1. thesis_schema
# ---------------------------------------------------------------------------

# Heuristic markers that suggest the thesis is contestable
_CONTESTABLE_MARKERS = re.compile(
    r"\b(more\s+\w+\s+than|outperforms?|consolidat\w+|"
    r"not\s+\w+\s+but|fails?\s+to|remains?|aspirational|"
    r"premature|settled|the\s+dominant|established|"
    r"must|cannot|will\s+not|should|ought\s+to)\b",
    re.IGNORECASE,
)


def check_thesis(thesis_doc: dict[str, Any]) -> list[tuple[str, str]]:
    """Returns list of (severity, message) tuples. severity in {ERROR, WARN}."""
    issues: list[tuple[str, str]] = []
    if not isinstance(thesis_doc, dict):
        return [("ERROR", "thesis.json is not a JSON object")]

    text = thesis_doc.get("thesis")
    if not isinstance(text, str) or not text.strip():
        issues.append(("ERROR", "thesis.thesis is missing or empty"))
    else:
        if not _CONTESTABLE_MARKERS.search(text):
            issues.append((
                "WARN",
                f"thesis text may not be contestable (no comparative / negation / "
                f"judgment marker matched): {text[:120]!r}"
            ))

    chosen = thesis_doc.get("thesis_id_chosen")
    candidates = thesis_doc.get("candidates") or []
    candidate_ids = {c.get("id") for c in candidates if isinstance(c, dict)}
    if chosen is None:
        issues.append(("ERROR", "thesis.thesis_id_chosen is missing"))
    elif candidate_ids and chosen not in candidate_ids:
        issues.append((
            "ERROR",
            f"thesis_id_chosen={chosen!r} not in candidates {sorted(candidate_ids)}"
        ))

    steps = thesis_doc.get("argument_steps") or []
    if not (3 <= len(steps) <= 6):
        issues.append((
            "ERROR",
            f"argument_steps length is {len(steps)}; spec requires 3-6"
        ))
    step_ids = [s.get("step_id") for s in steps if isinstance(s, dict)]
    if len(step_ids) != len(set(step_ids)):
        issues.append(("ERROR", "argument_steps contain duplicate step_id values"))
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            issues.append(("ERROR", f"argument_steps[{i}] is not an object"))
            continue
        if not (s.get("step_id") and s.get("claim")):
            issues.append((
                "ERROR",
                f"argument_steps[{i}] missing step_id or claim"
            ))

    objs = thesis_doc.get("anticipated_objections") or []
    if len(objs) < 2:
        issues.append((
            "ERROR",
            f"anticipated_objections count is {len(objs)}; spec requires ≥2"
        ))
    for i, o in enumerate(objs):
        if not isinstance(o, dict):
            issues.append(("ERROR", f"anticipated_objections[{i}] is not an object"))
            continue
        if not (isinstance(o.get("rebuttal"), str) and o["rebuttal"].strip()):
            issues.append((
                "ERROR",
                f"anticipated_objections[{i}].rebuttal missing/empty"
            ))

    # Optional 'counter-intuitive insight' axis. Field is opt-in; if
    # present, each entry must carry both `finding` (the contrarian
    # claim, ≥ 1 sentence) and `section_id` (where the % [INSIGHT]
    # anchor will live). audit_writing.py then enforces the anchor.
    findings = thesis_doc.get("non_obvious_findings")
    if findings is not None:
        if not isinstance(findings, list):
            issues.append((
                "ERROR",
                "non_obvious_findings must be a list of "
                "{finding, section_id} objects"
            ))
        else:
            for i, f in enumerate(findings):
                if not isinstance(f, dict):
                    issues.append((
                        "ERROR",
                        f"non_obvious_findings[{i}] is not an object"
                    ))
                    continue
                if not (isinstance(f.get("finding"), str)
                        and f["finding"].strip()):
                    issues.append((
                        "ERROR",
                        f"non_obvious_findings[{i}].finding missing/empty"
                    ))
                if not (isinstance(f.get("section_id"), str)
                        and f["section_id"].strip()):
                    issues.append((
                        "ERROR",
                        f"non_obvious_findings[{i}].section_id missing/empty"
                    ))
    return issues


# ---------------------------------------------------------------------------
# 2. claims_schema + 3. cite_key closed-set
# ---------------------------------------------------------------------------

_VALID_CLAIM_TYPES = {"empirical", "theoretical", "methodological", "critique"}


def _normalise_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def check_claims(
    claims: list[dict[str, Any]],
    paper_texts: dict[str, str],
    closed_set: set[str],
) -> list[tuple[str, str]]:
    """Validates every record in claims_cache.jsonl."""
    issues: list[tuple[str, str]] = []
    seen_claim_ids: set[str] = set()

    for ci, rec in enumerate(claims):
        cite_key = rec.get("cite_key")
        if not cite_key:
            issues.append(("ERROR", f"claims[{ci}] has no cite_key"))
            continue
        if cite_key not in closed_set:
            issues.append((
                "ERROR",
                f"claims[{ci}].cite_key={cite_key!r} not in filtered.jsonl (closed-set)"
            ))

        if not (isinstance(rec.get("what_paper_argues"), str) and
                rec["what_paper_argues"].strip()):
            issues.append((
                "WARN",
                f"claims[{cite_key}].what_paper_argues missing or empty"
            ))

        atomic = rec.get("atomic_claims") or []
        if not (2 <= len(atomic) <= 5):
            issues.append((
                "WARN",
                f"claims[{cite_key}].atomic_claims count is {len(atomic)}; "
                f"recommended 2-5"
            ))

        # Verbatim-quote check
        paper_text_norm = _normalise_ws(paper_texts.get(cite_key, ""))

        for ai, ac in enumerate(atomic):
            if not isinstance(ac, dict):
                issues.append((
                    "ERROR",
                    f"claims[{cite_key}].atomic_claims[{ai}] is not an object"
                ))
                continue
            cid = ac.get("claim_id")
            if not cid:
                issues.append((
                    "ERROR",
                    f"claims[{cite_key}].atomic_claims[{ai}] has no claim_id"
                ))
            elif cid in seen_claim_ids:
                issues.append((
                    "ERROR",
                    f"duplicate claim_id={cid!r}"
                ))
            else:
                seen_claim_ids.add(cid)

            ctype = ac.get("claim_type")
            if ctype not in _VALID_CLAIM_TYPES:
                issues.append((
                    "ERROR",
                    f"claims[{cite_key}].atomic_claims[{ai}].claim_type={ctype!r} "
                    f"not in {sorted(_VALID_CLAIM_TYPES)}"
                ))

            if not ac.get("anchor"):
                issues.append((
                    "WARN",
                    f"claims[{cite_key}].atomic_claims[{ai}] has no anchor"
                ))

            quote = ac.get("quote") or ""
            if len(quote.split()) < 6:
                issues.append((
                    "WARN",
                    f"claims[{cite_key}].atomic_claims[{ai}].quote shorter than "
                    f"6 tokens (likely paraphrase, not quotation)"
                ))
            elif paper_text_norm and _normalise_ws(quote) not in paper_text_norm:
                issues.append((
                    "WARN",
                    f"claims[{cite_key}].atomic_claims[{ai}].quote not found in "
                    f"paper text (verbatim mismatch); marked unverified"
                ))

    return issues


# ---------------------------------------------------------------------------
# 4. decision_summary
# ---------------------------------------------------------------------------

_VALID_AVAILABILITY = {"open", "weights-only", "weights only", "closed", "partial"}


def check_decision_summaries(
    cards: list[dict[str, Any]],
    tier_ids: set[str] | None,
) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    for ci, card in enumerate(cards):
        cite_key = card.get("cite_key", f"<card_{ci}>")
        ds = card.get("_decision_summary") or card.get("decision_summary")
        if not isinstance(ds, dict):
            issues.append((
                "WARN",
                f"card {cite_key!r}: no _decision_summary (decision-mode tables won't include it)"
            ))
            continue
        for field in ("one_line_role", "key_capability", "primary_limitation",
                      "availability"):
            v = ds.get(field)
            if v and isinstance(v, str) and len(v.split()) > 4 and field != "availability":
                issues.append((
                    "WARN",
                    f"card {cite_key!r}._decision_summary.{field}={v!r} "
                    f"exceeds 4 words; will be truncated in the table"
                ))
        if ds.get("availability") and str(ds["availability"]).lower() not in _VALID_AVAILABILITY:
            issues.append((
                "ERROR",
                f"card {cite_key!r}._decision_summary.availability={ds['availability']!r} "
                f"not in {sorted(_VALID_AVAILABILITY)}"
            ))
        if tier_ids and ds.get("tier") and ds["tier"] not in tier_ids:
            issues.append((
                "WARN",
                f"card {cite_key!r}._decision_summary.tier={ds['tier']!r} "
                f"not in outline.tier_axis.tiers ids {sorted(tier_ids)}"
            ))
    return issues


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat WARN findings as failures too (exit 1 on any WARN)",
    )
    parser.add_argument("--report", type=Path,
                        help="Write JSON report to this path")
    args = parser.parse_args(argv)

    run_dir: Path = args.run_dir.expanduser().resolve()

    # Fail fast on a non-existent run_dir. Without this gate, every
    # artifact loader returns an empty list / None and the audit
    # cheerfully reports "all checks pass" against a directory that
    # doesn't exist (exit 2 = input error, distinct from validation
    # failure exit 1).
    if not run_dir.is_dir():
        print(f"ERROR: run_dir not found or not a directory: {run_dir}",
              file=sys.stderr)
        return 2

    # Load all artifacts (some are optional in early-pipeline runs).
    # JSONL decode failures are surfaced as ERROR findings under the
    # respective area instead of silently dropped.
    claims_decode_errors: list[tuple[str, str]] = []
    filtered_decode_errors: list[tuple[str, str]] = []
    cards_decode_errors: list[tuple[str, str]] = []

    thesis_doc = _load_json(run_dir / "2_thesis" / "thesis.json")
    claims = _load_jsonl(run_dir / "1_search" / "claims_cache.jsonl",
                         errors=claims_decode_errors)
    filtered = _load_jsonl(run_dir / "1_search" / "filtered.jsonl",
                           errors=filtered_decode_errors)
    cards = _load_jsonl(run_dir / "1_search" / "cards.jsonl",
                        errors=cards_decode_errors)
    outline_doc = _load_json(run_dir / "4_outline" / "outline.json") or {}

    closed_set = {p.get("cite_key") or p.get("paper_id")
                  for p in filtered if (p.get("cite_key") or p.get("paper_id"))}
    tier_axis = outline_doc.get("tier_axis") or {}
    tier_ids = {t.get("id") for t in (tier_axis.get("tiers") or [])
                if t.get("id")}
    if not tier_ids:
        tier_ids = None  # signal "no outline tier_axis to check against"

    # Paper text cache (for verbatim quote check) — agent-managed cache
    # populated by tools/extract_paper_card.py --fetch-all. Paths used
    # historically:
    text_cache_dir = run_dir / ".cache" / "paper_texts"
    paper_texts: dict[str, str] = {}
    if text_cache_dir.exists():
        for f in text_cache_dir.glob("*.txt"):
            paper_texts[f.stem] = f.read_text(encoding="utf-8", errors="replace")

    findings: dict[str, list[tuple[str, str]]] = {}

    # 1. thesis schema
    if thesis_doc is None:
        findings["thesis_schema"] = [("WARN",
            "2_thesis/thesis.json missing — pipeline may still be in Phase 1")]
    else:
        findings["thesis_schema"] = check_thesis(thesis_doc)

    # 2 + 3. claims (prepend any decode-failure errors so corrupted
    # JSONL files surface as findings rather than silent drops).
    findings["claims_schema"] = (
        claims_decode_errors
        + filtered_decode_errors
        + check_claims(claims, paper_texts, closed_set)
    )

    # 4. decision_summary
    # When a run has no thesis.json, decision-mode is not available and
    # checking _decision_summary on every card would produce hundreds of
    # low-signal warnings. Collapse those into a single informational
    # note in that regime.
    if thesis_doc is None and cards and not any(
        (c.get("_decision_summary") or c.get("decision_summary")) for c in cards
    ):
        findings["decision_summary"] = cards_decode_errors + [(
            "WARN",
            f"all {len(cards)} cards lack _decision_summary "
            "(no thesis.json; decision-mode tables unavailable; not re-checked per card)"
        )]
    else:
        findings["decision_summary"] = (
            cards_decode_errors
            + check_decision_summaries(cards, tier_ids)
        )

    # Summary print
    print("=" * 60)
    print("validate_artifacts — schema audit")
    print("=" * 60)
    n_errors = 0
    n_warns = 0
    for area, issues in findings.items():
        if not issues:
            print(f"  {area:<20s} ✓ pass")
            continue
        errs = [m for sev, m in issues if sev == "ERROR"]
        warns = [m for sev, m in issues if sev == "WARN"]
        n_errors += len(errs)
        n_warns += len(warns)
        flag = "✗" if errs else "⚠"
        print(f"  {area:<20s} {flag} {len(errs)} errors, {len(warns)} warns")
        for sev, m in issues[:8]:
            print(f"    [{sev}] {m}")
        if len(issues) > 8:
            print(f"    ... ({len(issues) - 8} more)")
    print()
    print(f"Total: {n_errors} errors, {n_warns} warnings")

    if args.report:
        args.report.write_text(json.dumps(findings, indent=2))
        print(f"Report → {args.report}")

    if n_errors > 0 or (args.strict and n_warns > 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
