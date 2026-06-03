#!/usr/bin/env python3
"""
validate_outline.py — closed-set enforcement for survey outline paper IDs.

Strips hallucinated paper_ids from outline.json and back-fills from clusters.json.
Used as Step 3.5 in /survey-outline pipeline.

Exit codes:
  0 — success (with or without repairs)
  1 — repair failed (e.g. missing input files)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Schema aliasing
#
# Canonical artifact field names (per skills/survey-{search,cluster,outline}/SKILL.md):
#   filtered.jsonl[].paper_id           outline.sections[].section_id
#   clusters.assignments: pid → node    outline.sections[].primary_papers
#                                        outline.sections[].secondary_papers
#                                        outline.sections[].taxonomy_nodes
#
# Drift-tolerant aliases this module also accepts:
#   filtered.jsonl[].cite_key           outline.sections[].id
#                                        outline.sections[].papers (treated as primary)
#   clusters[node_id]: [pids]           taxonomy_nodes missing → fall back to [section_id]
# ---------------------------------------------------------------------------


def _paper_id(p: dict[str, Any]) -> str | None:
    """Return paper identifier — canonical `paper_id` or alias `cite_key`."""
    return p.get("paper_id") or p.get("cite_key")


def _section_id(s: dict[str, Any]) -> str | None:
    return s.get("section_id") or s.get("id")


def _section_primary(s: dict[str, Any]) -> list[str]:
    if "primary_papers" in s:
        return list(s["primary_papers"])
    return list(s.get("papers", []))


def _section_secondary(s: dict[str, Any]) -> list[str]:
    return list(s.get("secondary_papers", []))


def _section_nodes(s: dict[str, Any]) -> list[str]:
    """Resolve a section's taxonomy nodes.

    Priority: explicit `taxonomy_nodes` → infer from `section_id` (each
    section is its own taxonomy node, the common 1:1 pattern produced
    when outline-sketch seeds taxonomy directly from brief.dimensions).
    """
    nodes = s.get("taxonomy_nodes")
    if nodes is not None:
        return list(nodes)
    sid = _section_id(s)
    return [sid] if sid else []


def _normalize_clusters(c: dict[str, Any]) -> dict[str, str]:
    """Return canonical {pid: node_id} flat dict regardless of input shape.

    Accepts:
      - canonical:      {"assignments": {pid: node, ...}}
      - inverted:       {node_id: [pid, ...]}                  (list-valued)
      - flat:           {pid: node_id, ...}                    (string-valued)
    """
    if isinstance(c.get("assignments"), dict):
        return dict(c["assignments"])
    flat: dict[str, str] = {}
    for k, v in c.items():
        if isinstance(v, list):
            for pid in v:
                flat[pid] = k
        elif isinstance(v, str):
            # Flat-shape cluster output: dict IS the {pid: node} map.
            flat[k] = v
    return flat


def validate_outline(
    outline: dict[str, Any],
    papers: list[dict[str, Any]],
    clusters: dict[str, Any],
    *,
    min_primary: int = 3,
    max_primary: int = 15,
    max_secondary: int = 5,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Strip hallucinated paper_ids and back-fill from cluster assignments.

    Returns (repaired_outline, repairs_log).
    """
    # Accept EITHER identifier in the closed set. Search writes source ids
    # (arXiv / OpenAlex) into ``paper_id`` while the outline and every
    # ``\cite{}`` use the ``cite_key``; keying only on ``paper_id`` made a
    # cite-key-referencing outline look 100% hallucinated and stripped every
    # paper. Indexing both ids keeps the closed-set check honest without
    # forcing the upstream stages to pre-align the two fields.
    valid_ids: set[str] = set()
    paper_meta: dict[str, dict[str, Any]] = {}
    for p in papers:
        for ident in (_paper_id(p), p.get("cite_key")):
            if ident:
                valid_ids.add(ident)
                paper_meta.setdefault(ident, p)

    assignments = _normalize_clusters(clusters)
    by_node: dict[str, list[str]] = {}
    for pid, node in assignments.items():
        by_node.setdefault(node, []).append(pid)

    # Sort each node's paper list by citation count (desc) for deterministic back-fill.
    for node in by_node:
        by_node[node].sort(
            key=lambda pid: -int(paper_meta.get(pid, {}).get("citation_count", 0) or 0)
        )

    # Identify canonical papers (top 10% by citation count, min 5).
    # `citation_count` is optional; missing → 0, ties broken by paper_id order.
    n_canonical = max(5, len(papers) // 10)
    canonical_ids = {
        _paper_id(p)
        for p in sorted(papers, key=lambda x: -int(x.get("citation_count", 0) or 0))[
            :n_canonical
        ]
    } - {None}

    repairs: dict[str, Any] = {
        "removed_total": 0,
        "added_total": 0,
        "sections": {},
    }

    for sec in outline["sections"]:
        sid = _section_id(sec)
        node_ids = _section_nodes(sec)

        before_primary = _section_primary(sec)
        before_secondary = _section_secondary(sec)

        # Strip hallucinated IDs
        kept_primary = [pid for pid in before_primary if pid in valid_ids]
        kept_secondary = [pid for pid in before_secondary if pid in valid_ids]

        removed = sorted(
            (set(before_primary) - set(kept_primary))
            | (set(before_secondary) - set(kept_secondary))
        )

        # Back-fill primary from cluster assignments matching the section's nodes.
        # Skip Intro/Conclusion/Open Problems (no taxonomy_nodes assigned).
        added: list[str] = []
        if node_ids:
            seen = set(kept_primary) | set(kept_secondary)
            candidate_pool: list[str] = []
            for node in node_ids:
                for pid in by_node.get(node, []):
                    if pid not in seen and pid not in candidate_pool:
                        candidate_pool.append(pid)

            # Add until we hit min_primary, then continue if there's room (cap at max_primary)
            target_count = max(min_primary, len(kept_primary))
            for pid in candidate_pool:
                if len(kept_primary) >= max_primary:
                    break
                if len(kept_primary) >= target_count and len(kept_primary) >= min_primary:
                    # We've covered the minimum; only add canonical papers from these nodes
                    if pid not in canonical_ids:
                        continue
                kept_primary.append(pid)
                added.append(pid)
                seen.add(pid)

            # Back-fill secondary with canonical papers from the section's nodes
            # (if not already in primary)
            sec_canonicals = [
                pid
                for pid in candidate_pool
                if pid in canonical_ids and pid not in seen
            ]
            for pid in sec_canonicals[: max_secondary - len(kept_secondary)]:
                kept_secondary.append(pid)
                added.append(pid)
                seen.add(pid)

        sec["primary_papers"] = kept_primary
        sec["secondary_papers"] = kept_secondary

        # Update thin flag
        if node_ids:
            sec["thin"] = len(kept_primary) < min_primary

        repairs["sections"][sid] = {
            "removed": removed,
            "added": added,
            "final_primary_count": len(kept_primary),
            "final_secondary_count": len(kept_secondary),
            "thin": sec.get("thin", False),
        }
        repairs["removed_total"] += len(removed)
        repairs["added_total"] += len(added)

    return outline, repairs


def validate_thesis_schema(
    outline: dict[str, Any],
    thesis: dict[str, Any] | None,
) -> list[str]:
    """Thesis-driven schema checks: argues_for_thesis_step coverage,
    argument_skeleton, and tier_axis.

    Returns a list of human-readable violation messages. Empty list = pass.
    Returns [] silently when no body section carries `argues_for_thesis_step`
    — that signals an outline that was authored without thesis binding (e.g.
    the agent decided the run does not need it) and the thesis-coherence
    rules don't apply.
    """
    violations: list[str] = []
    sections = outline.get("sections", []) or []

    thesis_bound = any("argues_for_thesis_step" in s for s in sections)
    if not thesis_bound:
        return []

    if thesis is None:
        violations.append(
            "outline references argues_for_thesis_step but 2_thesis/thesis.json is missing"
        )
        return violations

    step_ids = {s.get("step_id") for s in (thesis.get("argument_steps") or [])}
    referenced: set[str] = set()
    for sec in sections:
        sid = _section_id(sec) or "<unnamed>"
        ref = sec.get("argues_for_thesis_step")
        # Non-body sections (intro/background/open-problems/future/conclusion/
        # trends/abstract) legally carry no thesis binding. Exempt them by
        # their declared section_type rather than by an id-prefix heuristic,
        # which mis-fires on ids like "07_future" / "09_conclusion".
        sec_type = sec.get("section_type")
        non_body_types = {
            "intro", "background", "open_problems", "future_directions",
            "conclusion", "trends", "abstract",
        }
        if ref is None:
            is_body = sec_type == "body" or (
                sec_type is None
                and isinstance(sid, str)
                and sid[:2].isdigit()
                and sid[:2] not in {"01", "00"}
            )
            if is_body and sec_type not in non_body_types:
                # only warn if the thesis has steps to bind to
                if step_ids and len(sections) > 4:
                    violations.append(
                        f"section {sid!r}: missing argues_for_thesis_step "
                        f"(body sections must bind to one of {sorted(step_ids)})"
                    )
        elif ref not in step_ids:
            violations.append(
                f"section {sid!r}: argues_for_thesis_step={ref!r} "
                f"is not a valid thesis.argument_steps[].step_id "
                f"(valid: {sorted(step_ids)})"
            )
        else:
            referenced.add(ref)

        # argument_skeleton schema check (only on sections that bind to a step)
        if ref:
            skel = sec.get("argument_skeleton") or {}
            for required in ("claim", "steelman", "concession", "so_what"):
                if not (isinstance(skel.get(required), str) and skel[required].strip()):
                    violations.append(
                        f"section {sid!r}: argument_skeleton.{required} "
                        f"missing or empty (required for body sections)"
                    )
            if "evidence_claim_keys" not in skel:
                violations.append(
                    f"section {sid!r}: argument_skeleton.evidence_claim_keys "
                    f"missing (use [] at outline time; populated during write)"
                )

    # Coverage: every step_id should be referenced by ≥ 1 section
    uncovered = step_ids - referenced
    if uncovered:
        violations.append(
            f"argument_step coverage gap: steps {sorted(uncovered)} "
            f"are not referenced by any section's argues_for_thesis_step"
        )

    # Optional: maturity_tier — 'state-of-art / frontier / speculative'
    # axis (orthogonal to tier_axis). Lets the survey carve out a clear
    # 'what is settled vs. what is contested vs. what is open' frame, the
    # way the L1-L5 benchmark surveys do.
    valid_maturity = {"mature", "frontier", "speculative"}
    seen_maturity: set[str] = set()
    for sec in sections:
        if "maturity_tier" not in sec:
            continue
        mt = sec.get("maturity_tier")
        sid = _section_id(sec) or "<unnamed>"
        if mt not in valid_maturity:
            violations.append(
                f"section {sid!r}: maturity_tier={mt!r} not in "
                f"{sorted(valid_maturity)}"
            )
        else:
            seen_maturity.add(mt)
    # If the field is used at all, require ≥ 2 distinct tiers — a single
    # tier across the whole survey defeats the point of the axis.
    if seen_maturity and len(seen_maturity) < 2:
        violations.append(
            f"maturity_tier coverage thin: only {sorted(seen_maturity)} "
            f"present; cover ≥ 2 of {sorted(valid_maturity)} so the "
            f"survey forms a clear 'mature → frontier → speculative' "
            f"spectrum"
        )

    # tier_axis schema (optional)
    tier_axis = outline.get("tier_axis")
    if tier_axis is not None:
        tiers = tier_axis.get("tiers") or []
        tier_ids = [t.get("id") for t in tiers if t.get("id")]
        if len(tier_ids) != len(set(tier_ids)):
            violations.append("tier_axis.tiers contain duplicate ids")

        # Optional per-tier maturity overlay: orthogonal to the
        # technical tier ordering. Same closed set as sections[].maturity_tier.
        for ti, t in enumerate(tiers):
            if not isinstance(t, dict):
                continue
            if "maturity" not in t:
                continue
            mt = t.get("maturity")
            if mt not in valid_maturity:
                violations.append(
                    f"tier_axis.tiers[{ti}].maturity={mt!r} not in "
                    f"{sorted(valid_maturity)}"
                )
        if not (5 <= len(tier_ids) <= 7):
            violations.append(
                f"tier_axis.tiers count is {len(tier_ids)}; recommended 5–7"
            )
        feature_cols = tier_axis.get("feature_columns") or []
        cells = tier_axis.get("cells") or {}
        if set(cells.keys()) - set(tier_ids):
            violations.append(
                f"tier_axis.cells references unknown tier ids: "
                f"{sorted(set(cells.keys()) - set(tier_ids))}"
            )
        for tid, col_map in cells.items():
            if not isinstance(col_map, dict):
                violations.append(
                    f"tier_axis.cells[{tid!r}] must be a dict of "
                    f"feature_column → list[str]"
                )
                continue
            unknown_cols = set(col_map.keys()) - set(feature_cols)
            if unknown_cols:
                violations.append(
                    f"tier_axis.cells[{tid!r}] references unknown "
                    f"feature_columns: {sorted(unknown_cols)}"
                )

    return violations


# ---------------------------------------------------------------------------
# Structural-template validation (outline-side complement to
# audit_writing.audit_structural_template, which audits the assembled .tex).
#
# These checks fire at outline-validation time so the outline itself can be
# rejected before the agent burns tokens writing sections that are
# structurally guaranteed to fail invariants 1, 4, 6.
# ---------------------------------------------------------------------------


def validate_structural_template(outline: dict[str, Any]) -> list[str]:
    """Return a list of human-readable violations of the structural template.

    Empty list = pass. Implements the outline-side enforcement of
    `shared-references/structural-template.md` invariants 1, 4, 6.
    Invariants 2, 3, 7 are tex-content-side and live in audit_writing;
    invariant 5 is a content check that needs the assembled .tex.
    """
    violations: list[str] = []
    sections = outline.get("sections", []) or []

    # ---- Inv 1: 6–12 top-level body sections, ≥ 4 with ≥ 3 subsections ----
    # Window mirrors benchmark-targets.json top_sections_{min,max}; the upper
    # bound is 12 so broad / historical briefs can give each dimension or era
    # its own section instead of merging back into one templated skeleton.
    body_like = [s for s in sections
                 if s.get("section_type") not in {"abstract"}]
    n_top = len(body_like)
    if not (6 <= n_top <= 12):
        violations.append(
            f"section_nesting: {n_top} top-level sections "
            f"(must be 6–12); add or merge sections to land in window"
        )
    nested_enough = sum(
        1 for s in body_like
        if len(s.get("subsections") or []) >= 3
    )
    if nested_enough < 4:
        violations.append(
            f"section_nesting: only {nested_enough} sections carry ≥ 3 "
            f"subsections (need ≥ 4); flat outlines lose the "
            f"'taxonomy → patterns → systems → problems' rhythm"
        )

    # ---- Inv 4: exactly one cross_cutting_matrix slot ----
    n_matrix = 0
    if outline.get("cross_cutting_matrix"):
        n_matrix += 1
    for sec in sections:
        if sec.get("section_type") == "cross_cutting_matrix":
            n_matrix += 1
        for sub in sec.get("subsections") or []:
            if sub.get("section_type") == "cross_cutting_matrix":
                n_matrix += 1
    if n_matrix == 0:
        violations.append(
            "cross_cutting_matrix: outline declares no cross_cutting_matrix "
            "slot; add one as a subsection of the body section that "
            "introduces the surveyed systems "
            "(see shared-references/structural-template.md inv 4)"
        )
    elif n_matrix > 1:
        violations.append(
            f"cross_cutting_matrix: outline declares {n_matrix} slots; "
            f"there must be exactly 1 (the matrix is the table the "
            f"whole paper points back to — duplicates dilute it)"
        )

    # ---- Inv 6: paired open_problems / future_directions ----
    op_secs = [s for s in sections if s.get("section_type") == "open_problems"]
    fd_secs = [s for s in sections
               if s.get("section_type") in ("future_directions", "trends")]
    if op_secs and fd_secs:
        op_items = op_secs[0].get("items") or op_secs[0].get("subsections") or []
        fd_items = fd_secs[0].get("items") or fd_secs[0].get("subsections") or []
        n_op, n_fd = len(op_items), len(fd_items)
        if not (5 <= n_op <= 8):
            violations.append(
                f"open_problems_pairing: {n_op} open-problem items "
                f"(must be 5–8)"
            )
        # The benchmark survey runs 6 OP × 5 FD; allow |Δ| ≤ 1 here too.
        if abs(n_op - n_fd) > 1:
            violations.append(
                f"open_problems_pairing: open-problems ({n_op}) and "
                f"future-directions ({n_fd}) item counts differ by more "
                f"than 1; they must be roughly parallel lists"
            )
        fd_ids = {it.get("id") for it in fd_items if isinstance(it, dict)}
        unpaired = [it for it in op_items
                    if isinstance(it, dict)
                    and not it.get("paired_direction_id")]
        # Allow up to 20% of OP items to lack a paired_direction_id —
        # an 'orthogonal' open problem that legitimately has no matching
        # future direction is allowed.
        if n_op and len(unpaired) > 0.20 * n_op:
            violations.append(
                f"open_problems_pairing: {len(unpaired)} of {n_op} "
                f"open-problem items lack paired_direction_id "
                f"(need ≥ 80% paired)"
            )
        bad_refs = [it for it in op_items
                    if isinstance(it, dict)
                    and it.get("paired_direction_id")
                    and it.get("paired_direction_id") not in fd_ids]
        if bad_refs:
            violations.append(
                f"open_problems_pairing: {len(bad_refs)} open-problem "
                f"items reference unknown future_directions ids "
                f"(known: {sorted(fd_ids)})"
            )
    elif op_secs and not fd_secs:
        violations.append(
            "open_problems_pairing: outline has an open_problems section "
            "but no future_directions section to pair it against"
        )
    elif fd_secs and not op_secs:
        violations.append(
            "open_problems_pairing: outline has a future_directions section "
            "but no open_problems section to pair it against"
        )
    else:
        violations.append(
            "open_problems_pairing: outline declares neither open_problems "
            "nor future_directions sections; both are required"
        )

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="Survey run directory")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print repairs but do not write outline.json",
    )
    parser.add_argument(
        "--min-primary", type=int, default=3, help="Minimum primary papers per section"
    )
    parser.add_argument(
        "--max-primary", type=int, default=15, help="Maximum primary papers per section"
    )
    parser.add_argument(
        "--strict-thesis",
        action="store_true",
        help="Treat thesis-driven schema violations (argument_skeleton / "
        "argues_for_thesis_step / tier_axis) as errors. Default is to print "
        "them as warnings; absence of these fields is tolerated for older "
        "outlines.",
    )
    parser.add_argument(
        "--strict-template",
        action="store_true",
        help="Treat structural-template violations (section nesting, "
        "cross_cutting_matrix slot, open/future pairing) as errors. "
        "Default is to print them as warnings — see "
        "shared-references/structural-template.md for the contract. "
        "/survey-outline always passes --strict-template.",
    )
    args = parser.parse_args()

    run_dir: Path = args.run_dir.expanduser().resolve()
    outline_path = run_dir / "4_outline" / "outline.json"
    filtered_path = run_dir / "1_search" / "filtered.jsonl"
    clusters_path = run_dir / "2_cluster" / "clusters.json"   # optional
    thesis_path = run_dir / "2_thesis" / "thesis.json"
    repairs_path = run_dir / "4_outline" / "outline_repairs.json"

    # outline + filtered are required in all modes
    for p in (outline_path, filtered_path):
        if not p.exists():
            print(f"ERROR: missing required file: {p}", file=sys.stderr)
            return 1

    outline = json.loads(outline_path.read_text())
    papers = load_jsonl(filtered_path)

    # clusters.json may be absent (cluster is merged into outline-sketch).
    # Fall back to a synthetic clusters dict that puts every paper in a single
    # bucket so the closed-set repair logic still works.
    if clusters_path.exists():
        clusters = json.loads(clusters_path.read_text())
    else:
        clusters = {"_synthetic_single_bucket": [_paper_id(p) for p in papers if _paper_id(p)]}

    thesis = None
    if thesis_path.exists():
        thesis = json.loads(thesis_path.read_text())

    repaired, repairs = validate_outline(
        outline,
        papers,
        clusters,
        min_primary=args.min_primary,
        max_primary=args.max_primary,
    )

    # Thesis-driven schema validation (in addition to closed-set repair)
    thesis_violations = validate_thesis_schema(repaired, thesis)

    # Structural-template invariants (1, 4, 6 — the outline-side ones)
    structural_violations = validate_structural_template(repaired)

    print("Outline validation results:")
    print(f"  Removed (hallucinated) paper_ids: {repairs['removed_total']}")
    print(f"  Added (back-filled) paper_ids:    {repairs['added_total']}")
    if thesis_violations:
        print(f"  thesis schema violations:         {len(thesis_violations)}")
        for v in thesis_violations:
            print(f"    - {v}")
    else:
        print("  thesis schema:                    OK")
    if structural_violations:
        print(f"  structural-template violations:   {len(structural_violations)}")
        for v in structural_violations:
            print(f"    - {v}")
    else:
        print("  structural-template:              OK")
    print()
    for sid, info in repairs["sections"].items():
        if info["removed"] or info["added"]:
            print(
                f"  [{sid}] -{len(info['removed'])} removed, +{len(info['added'])} added"
                f"  → {info['final_primary_count']} primary, "
                f"{info['final_secondary_count']} secondary"
                f"{'  ⚠ THIN' if info['thin'] else ''}"
            )

    strict_failure = (
        (args.strict_thesis and thesis_violations)
        or (args.strict_template and structural_violations)
    )

    if args.dry_run:
        print("\n(dry-run: no files written)")
        return 1 if strict_failure else 0

    if repairs["removed_total"] > 0 or repairs["added_total"] > 0:
        outline_path.write_text(json.dumps(repaired, indent=2) + "\n")
        repairs_path.write_text(json.dumps(repairs, indent=2) + "\n")
        print(f"\nWrote repaired outline → {outline_path}")
        print(f"Wrote repairs log     → {repairs_path}")
    else:
        print("\nNo repairs needed; outline is already closed-set-valid.")

    if strict_failure:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
