#!/usr/bin/env python3
"""pair_open_future.py — auto-populate paired_direction_id between
open-problem and future-direction items so structural-template invariant 6
passes deterministically.

The pairing is the LLM's responsibility in principle (each open problem
must logically map to one future direction), but agents drift: half the
runs we sampled produced two parallel lists with no explicit linkage.
This tool computes a best-effort title-similarity pairing, writes the
result back into outline.json, and surfaces any leftovers (open problems
with no future-direction match — those need LLM intervention).

Algorithm
---------
1. Load `4_outline/outline.json`.
2. Find the section with `section_type == open_problems` (call it OP)
   and the one with `section_type in {future_directions, trends}` (FD).
3. For every OP item without a `paired_direction_id`, compute the
   token-Jaccard similarity of its title against every FD item's title.
4. Greedy assignment: strongest match first; an FD item can be paired at
   most once.
5. If counts differ, leave the surplus side unpaired and surface a
   diagnostic so the writer (or a human) knows which items still need
   attention.

CLI
---
    pair_open_future.py <run_dir> [--dry-run] [--min-jaccard 0.10]

Exit codes
----------
    0 — outline updated (or already paired)
    1 — could not pair: no OP / FD section, or counts so divergent
        that fewer than 80% of OP items could be paired
    2 — input error (outline.json missing or invalid)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


_TOKEN_RE = re.compile(r"\b[a-z][a-z0-9]+\b")
# Common stopwords + scaffolding words that pollute Jaccard.
_STOP = {
    "the", "and", "for", "of", "to", "in", "on", "with", "from", "by",
    "is", "are", "be", "an", "a", "as", "at", "or", "this", "that",
    "we", "our", "their", "these", "those", "it", "its",
    "research", "directions", "direction", "future", "open", "problem",
    "problems", "challenge", "challenges", "study", "studies",
}


def tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_section(sections: list[dict[str, Any]],
                 types: tuple[str, ...]) -> dict[str, Any] | None:
    for sec in sections:
        if sec.get("section_type") in types:
            return sec
    return None


def items_of(sec: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not sec:
        return []
    raw = sec.get("items") or sec.get("subsections") or []
    # Coerce strings to dicts so callers can assume the dict shape.
    out: list[dict[str, Any]] = []
    for it in raw:
        if isinstance(it, dict):
            out.append(it)
        elif isinstance(it, str):
            out.append({"id": it, "title": it})
    return out


def text_for_match(item: dict[str, Any]) -> str:
    return " ".join(filter(None, [
        item.get("title") or "",
        item.get("name") or "",
        item.get("description") or "",
        item.get("claim") or "",
    ]))


def pair(op_items: list[dict[str, Any]],
         fd_items: list[dict[str, Any]],
         min_jaccard: float) -> tuple[int, list[str]]:
    """Mutate ``op_items`` in place, attaching ``paired_direction_id``
    fields where a confident match exists. Returns
    ``(n_paired, diagnostics)``."""
    op_tokens = [tokens(text_for_match(it)) for it in op_items]
    fd_tokens = [tokens(text_for_match(it)) for it in fd_items]
    fd_ids = [it.get("id") for it in fd_items]

    # Score every (op, fd) pair where op lacks a paired_direction_id.
    candidates: list[tuple[float, int, int]] = []
    for i, op in enumerate(op_items):
        if op.get("paired_direction_id"):
            continue
        for j, fd_id in enumerate(fd_ids):
            if not fd_id:
                continue
            s = jaccard(op_tokens[i], fd_tokens[j])
            if s >= min_jaccard:
                candidates.append((s, i, j))
    candidates.sort(reverse=True)

    paired_ops: set[int] = set()
    paired_fds: set[int] = set()
    n_paired = 0
    for score, i, j in candidates:
        if i in paired_ops or j in paired_fds:
            continue
        op_items[i]["paired_direction_id"] = fd_ids[j]
        op_items[i].setdefault("_pair_jaccard", round(score, 3))
        paired_ops.add(i)
        paired_fds.add(j)
        n_paired += 1

    # Already-paired items count toward the n_paired total.
    n_paired += sum(
        1 for op in op_items if op.get("paired_direction_id")
        and "_pair_jaccard" not in op
    )

    diagnostics: list[str] = []
    leftover_op = [op for op in op_items if not op.get("paired_direction_id")]
    if leftover_op:
        diagnostics.append(
            f"{len(leftover_op)} open-problem item(s) had no FD match above "
            f"jaccard ≥ {min_jaccard}: "
            f"{[op.get('id') for op in leftover_op]}"
        )
    leftover_fd = [
        fd_ids[j] for j in range(len(fd_ids))
        if j not in paired_fds and fd_ids[j]
        and not any(op.get("paired_direction_id") == fd_ids[j]
                    for op in op_items)
    ]
    if leftover_fd:
        diagnostics.append(
            f"{len(leftover_fd)} future-direction item(s) unreferenced: "
            f"{leftover_fd}"
        )
    return n_paired, diagnostics


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--min-jaccard", type=float, default=0.10,
        help="Minimum token-Jaccard similarity to count as a match. "
             "0.10 is permissive — set higher to demand explicit overlap "
             "(e.g. an open problem titled 'cognitive loops' will only "
             "pair with a future direction whose title also names "
             "'cognitive loops' or related terms).",
    )
    args = p.parse_args(argv)

    run_dir = args.run_dir.expanduser().resolve()
    outline_path = run_dir / "4_outline" / "outline.json"
    if not outline_path.exists():
        print(f"ERROR: outline missing: {outline_path}", file=sys.stderr)
        return 2
    try:
        outline = json.loads(outline_path.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: outline.json invalid: {e}", file=sys.stderr)
        return 2

    sections = outline.get("sections") or []
    op_sec = find_section(sections, ("open_problems",))
    fd_sec = find_section(sections, ("future_directions", "trends"))
    if op_sec is None or fd_sec is None:
        print(
            "ERROR: outline.json must declare both an open_problems and "
            "a future_directions (or trends) section before pairing. "
            "See shared-references/structural-template.md inv 6.",
            file=sys.stderr,
        )
        return 1

    op_items = items_of(op_sec)
    fd_items = items_of(fd_sec)

    n_paired, diagnostics = pair(op_items, fd_items, args.min_jaccard)
    n_op = len(op_items)
    pct = (n_paired / n_op) if n_op else 0.0

    print(f"pair_open_future: {n_paired}/{n_op} open-problem items paired "
          f"({pct:.0%})")
    for d in diagnostics:
        print(f"  - {d}")

    # Persist back into the outline (keep the original keys: 'items'
    # if that is what the section used, else 'subsections').
    if not args.dry_run:
        if "items" in op_sec:
            op_sec["items"] = op_items
        else:
            op_sec["subsections"] = op_items
        outline_path.write_text(json.dumps(outline, indent=2) + "\n")
        print(f"Wrote outline → {outline_path}")

    if pct < 0.80:
        print(
            "\n⚠ pairing rate below 80%: invariant 6 still failing. "
            "Either the OP / FD lists are misaligned (different "
            "topical coverage), or the LLM did not name them in "
            "matching language. Re-run /survey-outline Step 2 with the "
            "structural-template requirements section visible to the "
            "writer prompt.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
