#!/usr/bin/env python3
"""verify_evidence.py — verify the survey's evidence against the *full text*.

The existing claim-grounding gate (audit_writing) checks a numeric+cited
sentence for lexical/fuzzy overlap against the paper's **abstract** plus the
agent's **self-mined quotes**. That has two holes this tool closes, using the
full text the pipeline already fetched into ``1_search/.cache/<cite_key>.txt``
(``extract_paper_card.py --fetch-all``: S2 OA -> arXiv PDF -> HTML -> abstract):

  1. QUOTE VERIFICATION — every ``atomic_claims[].quote`` in claims_cache.jsonl
     must actually appear (verbatim or near-verbatim) in the cited paper's
     source text. The grounding gate trusts these quotes, but nothing checks
     they are real; an unverified quote can launder a hallucinated number.

  2. NUMERIC GROUNDING vs FULL TEXT — every quantitative number in a
     numeric+cited prose sentence should appear in *some* cited paper's full
     text, not merely share words with its abstract. A number found in no
     cited source is flagged as unsourced (likely fabricated or mis-attributed).

When a paper has no cached full text, the tool falls back to its abstract and
records the gap in ``full_text_coverage`` so the operator knows the check ran
at reduced strength rather than silently passing.

CLI:
    verify_evidence.py <run_dir>
        [--cache-dir PATH]            # default <run>/1_search/.cache
        [--claims PATH]               # default <run>/1_search/claims_cache.jsonl
        [--filtered PATH]             # default <run>/1_search/filtered.jsonl
        [--sections-dir PATH]         # default <run>/5_paper/sections
        [--min-quote-verified F=0.80]
        [--min-numeric-grounded F=0.70]
        [--strict]                    # exit 1 when below thresholds
        [--report PATH] [--json]

Exit codes: 0 ok · 1 below threshold under --strict · 2 input error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ── text normalisation ──────────────────────────────────────────────────────

_LATEX_CMD = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?")
_CITE = re.compile(r"\\cite[tp]?\*?\{[^}]*\}")
_WS = re.compile(r"\s+")
_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "with", "by", "at", "as", "that", "this", "from", "it", "its", "be", "we",
    "our", "their", "than", "which", "while", "but", "not", "can", "use", "uses",
}


def strip_latex(s: str) -> str:
    s = _CITE.sub(" ", s)
    s = s.replace(r"\times", "x").replace("$", " ").replace(r"\%", "%")
    s = _LATEX_CMD.sub(" ", s)
    s = s.replace("{", " ").replace("}", " ").replace("~", " ")
    return s


def normalize(s: str) -> str:
    """Lowercase, strip LaTeX, collapse non-alphanumerics to single spaces.

    Periods are kept only between digits (decimals like ``88.5``); sentence
    periods are dropped so ``mmlu.`` and ``mmlu`` match.
    """
    s = strip_latex(s or "").lower()
    s = re.sub(r"[^a-z0-9%.\s-]", " ", s)
    s = re.sub(r"(?<!\d)\.(?!\d)", " ", s)   # drop non-decimal periods
    return _WS.sub(" ", s).strip()


def content_tokens(s: str) -> List[str]:
    toks = normalize(s).split()
    return [t for t in toks if len(t) >= 3 and t not in _STOP]


# ── quote verification ──────────────────────────────────────────────────────

def quote_status(quote: str, source: str, near_recall: float = 0.9) -> str:
    """Return 'verbatim' | 'near' | 'unverified' for a quote vs a source text.

    verbatim  — the normalised quote is a substring of the normalised source.
    near      — >= near_recall of the quote's content tokens appear in source.
    unverified— neither (the quote is likely not from this paper).
    """
    nq = normalize(quote)
    if not nq:
        return "unverified"
    ns = normalize(source)
    if not ns:
        return "unverified"
    if nq in ns:
        return "verbatim"
    q_toks = content_tokens(quote)
    if not q_toks:
        return "unverified"
    src_set = set(ns.split())
    present = sum(1 for t in q_toks if t in src_set)
    return "near" if present / len(q_toks) >= near_recall else "unverified"


# ── numeric extraction + grounding ──────────────────────────────────────────

# A quantitative number: digits with optional decimal, optional thousands
# commas, optional *attached* unit/suffix. We deliberately drop bare 4-digit
# years and small standalone integers (<=12: top-k, counts, enumerators) so
# the check targets benchmark-like figures, not structural numbers. The unit
# must be adjacent (no space) and not run into another word ('128kb' -> no
# unit); the trailing lookahead forbids a letter/digit after the unit.
_NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(%|x|k|m|b|t|gb|tb|bn|bp)?(?![a-z0-9])")


def _canonical_number(num: str, unit: str) -> Optional[Tuple[str, str]]:
    """Return (canonical, bare) or None if the number should be ignored."""
    bare = num.replace(",", "")
    try:
        val = float(bare)
    except ValueError:
        return None
    unit = (unit or "").lower()
    # Drop bare years and small unitless integers (structural, not claims).
    if not unit:
        if val.is_integer() and (1900 <= val <= 2099 or val <= 12):
            return None
    canon = bare + unit
    return canon, bare


def quant_numbers(text: str) -> Set[str]:
    """Set of canonical numeric tokens (e.g. '22.2x', '128k', '88.5', '30%')."""
    out: Set[str] = set()
    norm = normalize(text)
    for m in _NUM.finditer(norm):
        c = _canonical_number(m.group(1), m.group(2))
        if c:
            out.add(c[0])
    return out


def _number_index(text: str) -> Tuple[Set[str], Set[str]]:
    """Return (canonical_set, bare_set) for a source text."""
    canon: Set[str] = set()
    bare: Set[str] = set()
    norm = normalize(text)
    for m in _NUM.finditer(norm):
        c = _canonical_number(m.group(1), m.group(2))
        if c:
            canon.add(c[0])
            bare.add(c[1])
    return canon, bare


def missing_numbers(sentence: str, source_texts: List[str]) -> List[str]:
    """Canonical numbers in ``sentence`` found in NO source text.

    A number is grounded if its canonical form is in any source's canonical
    set, or (when it carries a unit/decimal) its bare value is in any source's
    bare set — tolerating '128k' (prose) vs '128,000' (source)."""
    src_canon: Set[str] = set()
    src_bare: Set[str] = set()
    for s in source_texts:
        c, b = _number_index(s)
        src_canon |= c
        src_bare |= b
    missing: List[str] = []
    norm = normalize(sentence)
    seen: Set[str] = set()
    for m in _NUM.finditer(norm):
        c = _canonical_number(m.group(1), m.group(2))
        if not c:
            continue
        canon, bare = c
        if canon in seen:
            continue
        seen.add(canon)
        has_unit = canon != bare
        grounded = canon in src_canon or (
            (has_unit or "." in bare) and bare in src_bare
        )
        if not grounded:
            missing.append(canon)
    return missing


# ── IO helpers ──────────────────────────────────────────────────────────────

_CITE_KEYS = re.compile(r"\\cite[tp]?\*?\{([^}]+)\}")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def _read_cache_text(cache_dir: Path, key: str) -> Optional[str]:
    fp = cache_dir / f"{key}.txt"
    if not fp.exists():
        return None
    body = fp.read_text(encoding="utf-8", errors="replace")
    if body.startswith("# source: "):
        nl = body.find("\n")
        if nl != -1:
            body = body[nl + 1:]
    return body


def _strip_floats(tex: str) -> str:
    """Remove table/figure environments so matrix cells aren't scanned as prose."""
    return re.sub(r"\\begin\{(table\*?|figure\*?|tabular)\}.*?\\end\{\1\}",
                  " ", tex, flags=re.DOTALL)


def build_source_pool(
    cited_keys: Set[str], cache_dir: Path, abstracts: Dict[str, str]
) -> Tuple[Dict[str, str], int]:
    """Per-key source text: full-text cache if present, else abstract."""
    pool: Dict[str, str] = {}
    with_fulltext = 0
    for k in cited_keys:
        ft = _read_cache_text(cache_dir, k)
        if ft and ft.strip():
            pool[k] = ft
            with_fulltext += 1
        else:
            pool[k] = abstracts.get(k, "")
    return pool, with_fulltext


# ── main analysis ────────────────────────────────────────────────────────────

def analyze(run_dir: Path, cache_dir: Path, claims_path: Path,
            filtered_path: Path, sections_dir: Path) -> Dict[str, Any]:
    filtered = _load_jsonl(filtered_path)
    abstracts = {
        (p.get("cite_key") or p.get("paper_id")): normalize(p.get("abstract") or "")
        for p in filtered if (p.get("cite_key") or p.get("paper_id"))
    }
    claims = _load_jsonl(claims_path)

    # Collect cited keys from prose (float environments stripped).
    section_texts: Dict[str, str] = {}
    cited_keys: Set[str] = set()
    if sections_dir.exists():
        for fp in sorted(sections_dir.glob("*.tex")):
            raw = fp.read_text(encoding="utf-8", errors="replace")
            section_texts[fp.stem] = _strip_floats(raw)
            for m in _CITE_KEYS.finditer(raw):
                cited_keys.update(k.strip() for k in m.group(1).split(","))

    pool_keys = cited_keys | {c.get("cite_key") for c in claims if c.get("cite_key")}
    pool_keys.discard(None)
    source_pool, with_fulltext = build_source_pool(pool_keys, cache_dir, abstracts)

    # 1. Quote verification.
    q_total = q_verbatim = q_near = 0
    q_examples: List[str] = []
    for rec in claims:
        key = rec.get("cite_key")
        src = source_pool.get(key, "")
        for ac in (rec.get("atomic_claims") or []):
            quote = (ac or {}).get("quote") or ""
            if not quote.strip():
                continue
            q_total += 1
            st = quote_status(quote, src)
            if st == "verbatim":
                q_verbatim += 1
            elif st == "near":
                q_near += 1
            elif len(q_examples) < 8:
                q_examples.append(f"[{key}] {quote[:140]}")
    q_verified = q_verbatim + q_near
    q_ratio = round(q_verified / q_total, 3) if q_total else 1.0

    # 2. Numeric grounding vs full text.
    n_total = n_grounded = 0
    n_examples: List[Dict[str, Any]] = []
    for sid, text in section_texts.items():
        if sid.startswith("00") or "abstract" in sid.lower():
            continue
        for sent in _SENT_SPLIT.split(text):
            keys = []
            for m in _CITE_KEYS.finditer(sent):
                keys.extend(k.strip() for k in m.group(1).split(","))
            if not keys:
                continue
            nums = quant_numbers(sent)
            if not nums:
                continue
            n_total += 1
            srcs = [source_pool.get(k, "") for k in keys]
            missing = missing_numbers(sent, srcs)
            if not missing:
                n_grounded += 1
            elif len(n_examples) < 12:
                n_examples.append({
                    "section": sid,
                    "missing": missing,
                    "cited": keys,
                    "sentence": _WS.sub(" ", strip_latex(sent)).strip()[:200],
                })
    n_ratio = round(n_grounded / n_total, 3) if n_total else 1.0

    return {
        "quote_verification": {
            "total": q_total, "verbatim": q_verbatim, "near": q_near,
            "unverified": q_total - q_verified, "verified_ratio": q_ratio,
            "unverified_examples": q_examples,
        },
        "numeric_grounding_fulltext": {
            "numeric_cited_sentences": n_total, "grounded": n_grounded,
            "grounded_ratio": n_ratio, "unsourced_examples": n_examples,
        },
        "full_text_coverage": {
            "cited_keys": len(pool_keys),
            "with_full_text": with_fulltext,
            "ratio": round(with_fulltext / len(pool_keys), 3) if pool_keys else 0.0,
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dir", type=str)
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--claims", default=None)
    p.add_argument("--filtered", default=None)
    p.add_argument("--sections-dir", default=None)
    p.add_argument("--min-quote-verified", type=float, default=0.80)
    p.add_argument("--min-numeric-grounded", type=float, default=0.70)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--report", default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    run_dir = Path(args.run_dir).expanduser().resolve()
    cache_dir = Path(args.cache_dir) if args.cache_dir else run_dir / "1_search" / ".cache"
    claims_path = Path(args.claims) if args.claims else run_dir / "1_search" / "claims_cache.jsonl"
    filtered_path = Path(args.filtered) if args.filtered else run_dir / "1_search" / "filtered.jsonl"
    sections_dir = Path(args.sections_dir) if args.sections_dir else run_dir / "5_paper" / "sections"
    if not sections_dir.exists() and not claims_path.exists():
        print(f"ERROR: neither sections dir nor claims found under {run_dir}", file=sys.stderr)
        return 2

    res = analyze(run_dir, cache_dir, claims_path, filtered_path, sections_dir)
    q = res["quote_verification"]
    n = res["numeric_grounding_fulltext"]
    c = res["full_text_coverage"]

    print("=" * 60)
    print("verify_evidence — evidence checked against full text")
    print("=" * 60)
    print(f"  full-text coverage:   {c['with_full_text']}/{c['cited_keys']} cited keys ({c['ratio']:.0%})")
    if c["ratio"] < 1.0:
        print("    (keys without cached full text fall back to abstract — run "
              "extract_paper_card.py --fetch-all for full strength)")
    print(f"  quote verification:   {q['verbatim']} verbatim + {q['near']} near "
          f"/ {q['total']} = {q['verified_ratio']:.0%} verified "
          f"({q['unverified']} unverified)")
    for ex in q["unverified_examples"][:5]:
        print(f"    ! unverified quote {ex}")
    print(f"  numeric vs full text: {n['grounded']}/{n['numeric_cited_sentences']} "
          f"sentences fully grounded = {n['grounded_ratio']:.0%}")
    for ex in n["unsourced_examples"][:6]:
        print(f"    ! unsourced {ex['missing']} in [{','.join(ex['cited'])}]: {ex['sentence'][:110]}")

    if args.report:
        Path(args.report).write_text(json.dumps(res, indent=2, ensure_ascii=False))
        print(f"  report -> {args.report}")
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))

    failed = []
    if q["total"] and q["verified_ratio"] < args.min_quote_verified:
        failed.append(f"quote_verified {q['verified_ratio']:.0%} < {args.min_quote_verified:.0%}")
    if n["numeric_cited_sentences"] and n["grounded_ratio"] < args.min_numeric_grounded:
        failed.append(f"numeric_grounded {n['grounded_ratio']:.0%} < {args.min_numeric_grounded:.0%}")
    if failed:
        print("  RESULT: FAIL — " + "; ".join(failed))
        return 1 if args.strict else 0
    print("  RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
