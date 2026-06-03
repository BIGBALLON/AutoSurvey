#!/usr/bin/env python3
"""snowball_citations.py — citation-graph recall expansion for survey-search.

Query-only search misses canonical work that uses different terminology than
the brief. Snowballing fixes that: starting from the seed corpus (the papers
the initial queries found), it walks the citation graph one hop — each seed's
**references** (outgoing) and **citing papers** (incoming) — and keeps the
candidates that connect to *several* seeds. A paper that many of your seeds
cite, or that cites many of your seeds, is almost certainly on-topic even if
no query phrasing surfaced it. This is bibliographic coupling + co-citation,
the standard high-precision recall lever for literature reviews.

Backend: OpenAlex (the project's primary fetcher; polite-pool, no key needed).
Output: new candidate papers in the *same* shape as ``openalex_fetch search``,
so they flow through the existing dedup / quality / scope / verify / anchor
pipeline exactly like query hits. Candidates already in the seed corpus are
dropped.

CLI:
    snowball_citations.py <run_dir>
        [--seeds-file PATH]          # default <run>/1_search/filtered.jsonl
        [--out PATH]                 # default <run>/1_search/snowball_candidates.jsonl
        [--max-seeds N=20]           # seeds to expand from (highest-cited first)
        [--per-seed-citations N=40]  # incoming citers fetched per seed
        [--min-overlap N=2]          # keep candidates linked to >= N seeds
        [--max-candidates N=60]      # cap emitted candidates
        [--json]                     # also print candidates to stdout
        [--dry-run]                  # plan only: print seed/limit summary, no network

Exit codes: 0 ok (even with 0 candidates) · 2 input error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple


# ── pure, network-free core (unit-tested) ───────────────────────────────────

def short_id(value: Optional[str]) -> Optional[str]:
    """Reduce an OpenAlex URL or id to its short ``W...`` form."""
    if not value:
        return None
    tok = str(value).rstrip("/").split("/")[-1].strip()
    return tok or None


def extract_openalex_id(record: Dict[str, Any]) -> Optional[str]:
    """Best-effort OpenAlex id for a corpus record, across field conventions."""
    for key in ("openalex_id", "openalex_paper_id"):
        v = record.get(key)
        if isinstance(v, str) and v.startswith("W"):
            return v
    for key in ("id", "url"):
        v = record.get(key)
        if isinstance(v, str) and "openalex.org/W" in v:
            return short_id(v)
    return None


def _record_citation_count(record: Dict[str, Any]) -> int:
    for key in ("cited_by_count", "citation_count", "citationCount"):
        v = record.get(key)
        if isinstance(v, (int, float)):
            return int(v)
    return 0


def _norm_title(title: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def seed_identity_set(seeds: Iterable[Dict[str, Any]]) -> Tuple[Set[str], Set[str], Set[str]]:
    """Return (openalex_ids, dois, normalised_titles) already in the corpus."""
    ids: Set[str] = set()
    dois: Set[str] = set()
    titles: Set[str] = set()
    for s in seeds:
        oid = extract_openalex_id(s)
        if oid:
            ids.add(oid)
        doi = s.get("doi")
        if isinstance(doi, str) and doi.strip():
            dois.add(doi.replace("https://doi.org/", "").strip().lower())
        t = _norm_title(s.get("title"))
        if t:
            titles.add(t)
    return ids, dois, titles


def rank_candidates(
    seed_ids: Set[str],
    neighbor_sets: Dict[str, Set[str]],
    meta_citations: Optional[Dict[str, int]] = None,
    min_overlap: int = 2,
    max_candidates: int = 60,
) -> List[Tuple[str, int]]:
    """Rank graph neighbours by how many distinct seeds link to each.

    ``neighbor_sets`` maps seed-id -> set of neighbour ids (its references
    ∪ its citers). A candidate's *overlap* is the number of distinct seeds it
    is connected to — high overlap == strong co-citation/coupling == on-topic.
    Seeds themselves are excluded. Ties broken by global citation count.
    """
    meta_citations = meta_citations or {}
    counts: Dict[str, int] = {}
    for sid, neigh in neighbor_sets.items():
        for n in neigh:
            if not n or n in seed_ids:
                continue
            counts[n] = counts.get(n, 0) + 1
    ranked = [
        (cid, ov) for cid, ov in counts.items() if ov >= min_overlap
    ]
    ranked.sort(key=lambda t: (t[1], meta_citations.get(t[0], 0)), reverse=True)
    return ranked[:max_candidates]


# ── network shell ───────────────────────────────────────────────────────────

def _with_retries(fn: Callable[[], Any], attempts: int = 4, base_sleep: float = 2.0):
    """Run ``fn`` with exponential backoff; return its value or None on failure."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if i == attempts - 1:
                print(f"  [snowball] giving up after {attempts} tries: {exc}",
                      file=sys.stderr)
                return None
            time.sleep(base_sleep * (i + 1))
    return None


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    seeds_file = Path(args.seeds_file) if args.seeds_file else run_dir / "1_search" / "filtered.jsonl"
    out_path = Path(args.out) if args.out else run_dir / "1_search" / "snowball_candidates.jsonl"
    if not seeds_file.exists():
        print(f"ERROR: seeds file not found: {seeds_file}", file=sys.stderr)
        return 2

    seeds = _load_jsonl(seeds_file)
    if not seeds:
        print(f"ERROR: no seed records in {seeds_file}", file=sys.stderr)
        return 2

    # Choose the highest-cited seeds that carry an OpenAlex id (or DOI we can
    # resolve). These anchor the graph walk.
    seeds_sorted = sorted(seeds, key=_record_citation_count, reverse=True)
    chosen: List[Tuple[str, Dict[str, Any]]] = []
    for s in seeds_sorted:
        handle = extract_openalex_id(s) or (
            s.get("doi") and "doi:" + s["doi"].replace("https://doi.org/", "").strip()
        )
        if handle:
            chosen.append((handle, s))
        if len(chosen) >= args.max_seeds:
            break

    seed_ids, seed_dois, seed_titles = seed_identity_set(seeds)

    print(f"[snowball] seeds={len(seeds)} expandable={len(chosen)} "
          f"(max {args.max_seeds}) · per-seed citers={args.per_seed_citations} · "
          f"min-overlap={args.min_overlap} · max-candidates={args.max_candidates}",
          flush=True)
    if not chosen:
        print("[snowball] no seeds carry an OpenAlex id or DOI — nothing to expand. "
              "(Snowball needs OpenAlex-indexed seeds; run OpenAlex search first.)",
              file=sys.stderr)
        out_path.write_text("")
        return 0
    if args.dry_run:
        print("[snowball] --dry-run: would expand the above seeds; no network calls made.")
        return 0

    try:
        import openalex_fetch  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot import openalex_fetch ({exc})", file=sys.stderr)
        return 2
    client = openalex_fetch.OpenAlexClient(email=args.email)

    neighbor_sets: Dict[str, Set[str]] = {}
    meta_cache: Dict[str, Dict[str, Any]] = {}     # candidate id -> parsed work
    meta_citations: Dict[str, int] = {}
    for i, (handle, seed) in enumerate(chosen, 1):
        work = _with_retries(lambda: client.get_work(handle))
        if not work:
            continue
        sid = work.get("openalex_id") or short_id(work.get("id"))
        if not sid:
            continue
        refs: Set[str] = {r for r in (work.get("referenced_works") or []) if r}
        citing = _with_retries(
            lambda: client.fetch_citing(sid, max_results=args.per_seed_citations)
        ) or []
        citing_ids: Set[str] = set()
        for c in citing:
            cid = c.get("openalex_id")
            if not cid:
                continue
            citing_ids.add(cid)
            meta_cache[cid] = c
            meta_citations[cid] = c.get("cited_by_count", 0)
        neighbor_sets[sid] = refs | citing_ids
        print(f"  [{i}/{len(chosen)}] {sid}: +{len(refs)} refs, +{len(citing_ids)} citers",
              flush=True)
        time.sleep(0.4)

    ranked = rank_candidates(
        seed_ids=seed_ids | {s for s in neighbor_sets},
        neighbor_sets=neighbor_sets,
        meta_citations=meta_citations,
        min_overlap=args.min_overlap,
        max_candidates=args.max_candidates,
    )

    # Fetch metadata for ranked candidates we don't already have cached.
    need_meta = [cid for cid, _ in ranked if cid not in meta_cache]
    if need_meta:
        fetched = _with_retries(lambda: client.get_works_by_ids(need_meta)) or []
        for w in fetched:
            if w.get("openalex_id"):
                meta_cache[w["openalex_id"]] = w

    # Emit candidates not already in the seed corpus, in OpenAlex search shape.
    emitted: List[Dict[str, Any]] = []
    for cid, overlap in ranked:
        rec = meta_cache.get(cid)
        if not rec:
            continue
        doi = (rec.get("doi") or "").replace("https://doi.org/", "").strip().lower()
        title = _norm_title(rec.get("title"))
        if cid in seed_ids or (doi and doi in seed_dois) or (title and title in seed_titles):
            continue
        rec = dict(rec)
        rec["_snowball"] = True
        rec["_snowball_overlap"] = overlap
        rec["source"] = "openalex_snowball"
        emitted.append(rec)

    out_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in emitted) + ("\n" if emitted else "")
    )
    print(f"[snowball] emitted {len(emitted)} new candidates → {out_path}")
    if emitted:
        top = ", ".join(f"{r.get('title','?')[:40]}({r['_snowball_overlap']})" for r in emitted[:5])
        print(f"[snowball] top: {top}")
    if args.json:
        print(json.dumps(emitted, ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("run_dir", type=str)
    p.add_argument("--seeds-file", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--max-seeds", type=int, default=20)
    p.add_argument("--per-seed-citations", type=int, default=40)
    p.add_argument("--min-overlap", type=int, default=2)
    p.add_argument("--max-candidates", type=int, default=60)
    p.add_argument("--email", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
