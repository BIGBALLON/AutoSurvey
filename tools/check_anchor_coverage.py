#!/usr/bin/env python3
"""check_anchor_coverage.py — did search actually collect the must-have material?

Search is the load-bearing first stage of AutoSurvey: if the corpus is
missing the canonical methods / models / benchmarks of a topic, every
downstream stage (thesis, outline, writing) is built on sand and the
survey cannot be authoritative. This tool turns "is the corpus good
enough?" into a measurable gate.

Given a list of anchor terms (the works/systems/benchmarks a competent
survey of the topic MUST cover) and the retrieved corpus, it reports
per-anchor hit/miss, an overall coverage ratio, and exits non-zero when
coverage falls below ``--min``. ``/survey-search`` runs it after dedup +
scope-filter so a throttled or mis-queried source is caught before the
pipeline proceeds, rather than discovered in the final PDF.

Anchors are matched case-insensitively against each record's title +
abstract. Short / all-caps anchors (acronyms like ``H2O``, ``MLA``,
``NSA``, ``RULER``) are matched on word boundaries to avoid spurious
substring hits; multi-word anchors are matched as a normalized phrase.

CLI::

    check_anchor_coverage.py --corpus filtered.jsonl [--corpus blogs.jsonl ...]
        --anchors "YaRN,LongRoPE,StreamingLLM,RULER,..."   # or --anchors-file
        [--min 0.7] [--json report.json]

Exit codes:
    0  — coverage >= --min
    1  — coverage < --min (search likely under-collected)
    2  — bad inputs (no corpus / no anchors)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _normalize(text: str) -> str:
    """Lowercase and collapse runs of non-alphanumerics to single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _load_corpus(paths: list[Path]) -> list[str]:
    """Return one normalized 'title + abstract' blob per record."""
    blobs: list[str] = []
    for p in paths:
        if not p.exists():
            print(f"[WARN] corpus file not found: {p}", file=sys.stderr)
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            blobs.append(_normalize(
                f"{rec.get('title', '')} {rec.get('abstract', '')}"))
    return blobs


def _is_acronymish(anchor: str) -> bool:
    """Short or has an upper-case-heavy/alnum-mixed token → match on word
    boundary to avoid substring false positives (e.g. 'NSA' inside
    'transNSAtion')."""
    stripped = anchor.strip()
    if len(stripped.replace(" ", "")) <= 4:
        return True
    # token like H2O / MLA / KV / YaRN (has digits or >=2 caps)
    return bool(re.search(r"\d", stripped)) or sum(c.isupper() for c in stripped) >= 2


def covered(anchor: str, blobs: list[str]) -> bool:
    norm = _normalize(anchor)
    if not norm:
        return False
    if _is_acronymish(anchor):
        pat = re.compile(rf"(?<![a-z0-9]){re.escape(norm)}(?![a-z0-9])")
        return any(pat.search(b) for b in blobs)
    return any(norm in b for b in blobs)


def check(anchors: list[str], corpus_paths: list[Path], min_ratio: float) -> dict:
    blobs = _load_corpus(corpus_paths)
    hits, misses = [], []
    for a in anchors:
        (hits if covered(a, blobs) else misses).append(a)
    ratio = len(hits) / len(anchors) if anchors else 0.0
    return {
        "anchors_total": len(anchors),
        "anchors_hit": len(hits),
        "coverage": round(ratio, 3),
        "min_required": min_ratio,
        "ok": ratio >= min_ratio,
        "hit": sorted(hits),
        "missing": sorted(misses),
        "corpus_records": len(blobs),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", action="append", type=Path, required=True,
                    help="JSONL corpus file (repeatable: papers, blogs, tech reports).")
    ap.add_argument("--anchors", default="",
                    help="Comma-separated anchor terms.")
    ap.add_argument("--anchors-file", type=Path, default=None,
                    help="File with one anchor term per line (merged with --anchors).")
    ap.add_argument("--min", type=float, default=0.7,
                    help="Minimum acceptable coverage ratio (default 0.7).")
    ap.add_argument("--json", type=Path, default=None,
                    help="Write the full report JSON here.")
    args = ap.parse_args(argv)

    anchors: list[str] = [a.strip() for a in args.anchors.split(",") if a.strip()]
    if args.anchors_file and args.anchors_file.exists():
        anchors += [ln.strip() for ln in
                    args.anchors_file.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.startswith("#")]
    # de-dup, preserve order
    seen: set[str] = set()
    anchors = [a for a in anchors if not (a.lower() in seen or seen.add(a.lower()))]

    if not anchors:
        print("ERROR: no anchors provided (use --anchors or --anchors-file).",
              file=sys.stderr)
        return 2

    report = check(anchors, args.corpus, args.min)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    status = "✅" if report["ok"] else "⚠️"
    print(f"{status} Anchor coverage: {report['anchors_hit']}/{report['anchors_total']} "
          f"({report['coverage']:.0%}) of must-have terms found in "
          f"{report['corpus_records']} records  [min {report['min_required']:.0%}]")
    if report["missing"]:
        print("   MISSING: " + ", ".join(report["missing"]))
        print("   → broaden queries / add a source / check for throttling before proceeding.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
