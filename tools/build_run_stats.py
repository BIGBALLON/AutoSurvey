#!/usr/bin/env python3
"""build_run_stats.py — quantitative meta-narrative for an AutoSurvey run.

Aggregates run-level numbers that are otherwise scattered across
``filtered.jsonl`` / ``thesis.json`` / ``outline.json`` /
``claims_cache.jsonl`` / ``5_paper/sections/*.tex`` into a single
``5_paper/stats.json`` artefact, plus a one-paragraph human-readable
preview the agent can drop verbatim into the abstract or a "How this
survey was produced" appendix.

The stats answer questions every reader asks unconsciously:

  * how many papers did the survey actually cover?
  * how dense are the citations?
  * how many argument steps + anticipated objections back the thesis?
  * how many distinct systems/comparisons does the survey contain?
  * roughly how long is the document?

These numbers are the "trust scaffolding" that turns a survey from
"long blog post" into "I should read this".

CLI::

    python3 build_run_stats.py <run_dir>
    python3 build_run_stats.py <run_dir> --output 5_paper/stats.json
    python3 build_run_stats.py <run_dir> --print-paragraph

Exit codes:
    0  — stats written
    2  — required artefact missing (filtered.jsonl / outline.json)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _load_json(p: Path) -> dict | None:
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    out: list[dict] = []
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"[WARN] {p.name}:{lineno} JSON decode failed: {exc.msg}",
                  file=sys.stderr)
            continue
    return out


# ---------------------------------------------------------------------------
# Counters (each is small + pure for testability)
# ---------------------------------------------------------------------------


_CITE_RE = re.compile(r"\\cite[tp]?\*?\{([^}]+)\}")
_CHARS_PER_PAGE = 3000   # neurips_2024 single-column rough estimate


def count_papers(filtered: list[dict]) -> int:
    return len(filtered)


def count_thesis_pieces(thesis: dict | None) -> dict[str, int]:
    if not thesis:
        return {"argument_steps": 0, "anticipated_objections": 0}
    return {
        "argument_steps": len(thesis.get("argument_steps") or []),
        "anticipated_objections": len(thesis.get("anticipated_objections") or []),
    }


def count_outline_pieces(outline: dict | None) -> dict[str, int]:
    if not outline:
        return {"sections": 0, "body_sections": 0, "tier_axis_tiers": 0}
    sections = outline.get("sections") or []
    body = [s for s in sections
            if isinstance(s.get("section_id") or s.get("id"), str)
            and (s.get("section_id") or s.get("id"))[:2].isdigit()
            and (s.get("section_id") or s.get("id"))[:2] not in {"00", "01"}]
    tiers = (outline.get("tier_axis") or {}).get("tiers") or []
    return {"sections": len(sections),
            "body_sections": len(body),
            "tier_axis_tiers": len(tiers)}


def count_claims(claims_cache: list[dict]) -> dict[str, int]:
    n_papers = 0
    n_atomic = 0
    for rec in claims_cache:
        if not rec.get("cite_key"):
            continue
        n_papers += 1
        n_atomic += len(rec.get("atomic_claims") or [])
    return {"papers_mined": n_papers, "atomic_claims": n_atomic}


_WORD_RE = re.compile(r"\b\w+\b")
# LaTeX commands stripped before word counting so '\citep{kaplan2020}' does
# not contribute 'citep', 'kaplan2020' as two phantom 'words'. We replace
# the command + its braced argument with a single space.
_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?(?:\{[^}]*\})?")


def scan_sections(sections_dir: Path) -> dict[str, Any]:
    """Walk 5_paper/sections/*.tex and gather citation + length stats.

    ``body_words`` is the count of word-like tokens after stripping
    LaTeX comments and commands — close enough to the benchmark's
    ``pdftotext | wc -w`` figure (17,457) to make a meaningful diff.
    """
    if not sections_dir.is_dir():
        return {"section_files": 0, "total_citations": 0,
                "unique_cite_keys": 0, "total_chars": 0,
                "body_words": 0,
                "estimated_pages": 0}
    cite_keys: set[str] = set()
    total_citations = 0
    total_chars = 0
    body_words = 0
    n_files = 0
    for tex in sorted(sections_dir.glob("*.tex")):
        n_files += 1
        text = tex.read_text(encoding="utf-8", errors="replace")
        # Strip LaTeX comments for char count fairness.
        stripped = "\n".join(line.split("%", 1)[0] for line in text.splitlines())
        total_chars += len(stripped)
        # Word count: also strip LaTeX commands so cite-keys don't pollute
        # the body-word figure.
        prose = _LATEX_CMD_RE.sub(" ", stripped)
        body_words += len(_WORD_RE.findall(prose))
        for m in _CITE_RE.finditer(text):
            for k in m.group(1).split(","):
                k = k.strip()
                if k:
                    cite_keys.add(k)
                    total_citations += 1
    return {
        "section_files": n_files,
        "total_citations": total_citations,
        "unique_cite_keys": len(cite_keys),
        "total_chars": total_chars,
        "body_words": body_words,
        "estimated_pages": max(1, round(total_chars / _CHARS_PER_PAGE)),
    }


def count_systems_compared(outline: dict | None,
                            cards: list[dict] | None = None) -> int:
    """Best estimate of 'systems compared' across the survey.

    Resolution order (preferred → fallback):

      1. ``outline.tier_axis.cells`` token set — the preferred path:
         only systems explicitly placed into the comparison matrix count.
      2. Cards with a ``_decision_summary`` field — the marker for
         'this paper participates in dimension tables'.
      3. Plain card count — older surveys with no decision_summary
         annotations; cards.jsonl is still a closer approximation
         than 0.

    Returns 0 only if both outline AND cards are absent.
    """
    # Tier-axis cells (preferred)
    if outline:
        cells = (outline.get("tier_axis") or {}).get("cells") or {}
        items: set[str] = set()
        for _tier_id, row in cells.items():
            if not isinstance(row, dict):
                continue
            for _col, vals in row.items():
                if isinstance(vals, list):
                    for v in vals:
                        if isinstance(v, str) and v.strip():
                            items.add(v.strip())
                elif isinstance(vals, str) and vals.strip():
                    items.add(vals.strip())
        if items:
            return len(items)

    # Decision-summary cards (fallback)
    if cards:
        with_ds = [c for c in cards
                   if isinstance(c, dict)
                   and (c.get("_decision_summary") or c.get("decision_summary"))]
        if with_ds:
            return len(with_ds)
        # Plain card count (earlier fallback)
        return len(cards)

    return 0


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def build_stats(run_dir: Path) -> dict[str, Any]:
    filtered = _load_jsonl(run_dir / "1_search" / "filtered.jsonl")
    thesis = _load_json(run_dir / "2_thesis" / "thesis.json")
    outline = _load_json(run_dir / "4_outline" / "outline.json")
    claims = _load_jsonl(run_dir / "1_search" / "claims_cache.jsonl")
    cards = _load_jsonl(run_dir / "1_search" / "cards.jsonl")
    sections_dir = run_dir / "5_paper" / "sections"

    sec = scan_sections(sections_dir)
    thesis_pieces = count_thesis_pieces(thesis)
    outline_pieces = count_outline_pieces(outline)
    claim_pieces = count_claims(claims)

    n_papers = count_papers(filtered)
    citations_per_paper = (
        round(sec["total_citations"] / n_papers, 2) if n_papers else 0.0
    )

    return {
        "schema_version": 1,
        "papers": {
            "in_corpus": n_papers,
            "cited": sec["unique_cite_keys"],
            "coverage": (
                round(sec["unique_cite_keys"] / n_papers, 3) if n_papers else 0.0
            ),
        },
        "citations": {
            "total": sec["total_citations"],
            "unique": sec["unique_cite_keys"],
            "per_paper_avg": citations_per_paper,
            # Avg inline citations per *cited* paper (reuse rate). This is
            # what the human-readable paragraph reports as "per cited paper";
            # dividing by the whole corpus (per_paper_avg) understates it and
            # mislabels coverage as reuse.
            "per_cited_avg": (
                round(sec["total_citations"] / sec["unique_cite_keys"], 2)
                if sec["unique_cite_keys"] else 0.0
            ),
        },
        "thesis": thesis_pieces,
        "outline": outline_pieces,
        "claims_cache": claim_pieces,
        "systems_compared": count_systems_compared(outline, cards),
        "document": {
            "section_files": sec["section_files"],
            "body_sections": outline_pieces["body_sections"],
            "estimated_pages": sec["estimated_pages"],
            "total_chars": sec["total_chars"],
            "body_words": sec["body_words"],
        },
    }


def _a_an(n: int) -> str:
    """Pick "a" or "an" for an integer based on its spoken English form.

    The phonetic rule is: "an" before a vowel sound. The English
    pronunciation of an integer begins with a vowel sound iff the
    leading digit-block triggers it:

      * "8" → "eight" (vowel sound)        — 8, 80–89, 800–899, 8000–8999, ...
      * "11" → "eleven" (vowel sound)      — 11, 11000–11999, 11M, ...
      * "18" → "eighteen" (vowel sound)    — 18, 18000–18999, ...

    All other leading patterns ("one", "two", "three", "four", "five",
    "six", "seven", "nine", "ten", "twelve"…"seventeen", "twenty"+)
    begin with a consonant sound and take "a".

    Worked examples (used in survey paragraphs):
       _a_an(86) == "an"   ("eighty-six")
       _a_an(11) == "an"
       _a_an(15) == "a"    ("fifteen")
       _a_an(100) == "a"   ("one hundred")
    """
    s = str(n)
    if s.startswith("8"):
        return "an"
    if s.startswith("11") and (len(s) <= 2 or len(s) >= 5):
        # 11, 11_000+ → "eleven" / "eleven thousand"; 110, 1_100, 11_000
        # are read "one hundred ten" / "one thousand one hundred" / "eleven
        # thousand". Treat 110-999 as a-words; 11_000+ as an-words.
        return "an"
    if s.startswith("18") and (len(s) <= 2 or len(s) >= 5):
        return "an"
    return "a"


def render_paragraph(stats: dict[str, Any]) -> str:
    """One-paragraph human preview; safe for direct paste into a draft."""
    p = stats["papers"]
    c = stats["citations"]
    t = stats["thesis"]
    d = stats["document"]
    sys_n = stats["systems_compared"]
    # Build the paragraph as a sequence of clauses joined by a single
    # ", and " so we don't end up with "..., and ..., yielding ..." —
    # which mis-binds "yielding" to the and-list.
    lead = (
        f"This survey covers {p['in_corpus']} papers "
        f"({p['cited']} cited, {int(p['coverage'] * 100)}% coverage) "
        f"across {d['body_sections']} body sections "
        f"(~{d['estimated_pages']} pages)"
    )
    middle: list[str] = []
    if t["argument_steps"] or t["anticipated_objections"]:
        middle.append(
            f"is organised around {t['argument_steps']} argument steps "
            f"with {t['anticipated_objections']} anticipated objections"
        )
    if sys_n:
        middle.append(
            f"pivots on {_a_an(sys_n)} {sys_n}-item comparison matrix"
        )
    tail = (
        f"yields {c['total']} citations "
        f"(avg {c.get('per_cited_avg', c.get('per_paper_avg', 0.0)):.1f} "
        f"per cited paper)"
    )
    # Compose: <lead>; <m1>; <m2>; and <tail>. Use semicolons so each
    # clause stays self-contained and "and" never adjoins a participle.
    clauses = [lead] + middle + [tail]
    if len(clauses) == 1:
        return clauses[0] + "."
    if len(clauses) == 2:
        return f"{clauses[0]}, and {clauses[1]}."
    return "; ".join(clauses[:-1]) + f"; and {clauses[-1]}."


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path,
                        help="Write stats.json here (default: <run>/5_paper/stats.json)")
    parser.add_argument("--print-paragraph", action="store_true",
                        help="Also print the human-readable paragraph to stdout")
    args = parser.parse_args(argv)

    run_dir: Path = args.run_dir.expanduser().resolve()
    if not run_dir.is_dir():
        print(f"ERROR: run_dir not found: {run_dir}", file=sys.stderr)
        return 2
    if not (run_dir / "1_search" / "filtered.jsonl").is_file():
        print(f"ERROR: 1_search/filtered.jsonl missing under {run_dir} — "
              "run /survey-search first", file=sys.stderr)
        return 2

    stats = build_stats(run_dir)
    out = args.output or (run_dir / "5_paper" / "stats.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"✅ stats → {out}")
    print(f"   papers:    {stats['papers']['in_corpus']} in corpus, "
          f"{stats['papers']['cited']} cited "
          f"({int(stats['papers']['coverage'] * 100)}% coverage)")
    print(f"   citations: {stats['citations']['total']} total, "
          f"{stats['citations']['unique']} unique")
    print(f"   thesis:    {stats['thesis']['argument_steps']} steps, "
          f"{stats['thesis']['anticipated_objections']} objections")
    print(f"   document:  {stats['document']['body_sections']} body sections, "
          f"~{stats['document']['estimated_pages']} pages")
    if args.print_paragraph:
        print()
        print(render_paragraph(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
