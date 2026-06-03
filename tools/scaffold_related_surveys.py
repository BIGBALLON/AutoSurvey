#!/usr/bin/env python3
"""scaffold_related_surveys.py — emit the 'Relationship to existing surveys'
subsection stub.

This is the deterministic half of structural-template invariant 5: locate
adjacent-survey candidates inside `1_search/filtered.jsonl` and write a
LaTeX scaffold the LLM writer fills in with the per-survey delta sentence.

Behaviour
---------
1. Scan filtered.jsonl + tech_reports.jsonl for entries that *look* like
   surveys: `type in {survey, review, book}`, OR title contains
   "survey"/"review"/"overview" (case-insensitive). Author-listed
   monographs (>40 author count) are excluded — they are usually venue
   anthologies, not the kind of focused adjacent survey we want named.
2. Rank candidates by `cited_by_count` desc, year desc, breaking ties on
   cite_key.
3. Emit `5_paper/sections/02_background.related_surveys.tex` with the
   top-N (default 5) candidates, each as a `\\citet{}` line plus a
   `% TODO: 1–2-sentence delta` comment.
4. If `--inject` is passed and `02_background.tex` exists, splice the
   scaffold into the file (idempotent: a previous scaffold is replaced
   in-place via the `% RELATED_SURVEYS_BEGIN/END` markers).

The scaffold satisfies audit_writing's invariant 5 detection (regex
matches on the subsection title and counts ≥ 3 named adjacent surveys).
The actual delta sentences are still authored by the LLM writer or by
hand — we are not pretending to summarise other people's surveys.

CLI
---
    scaffold_related_surveys.py <run_dir> [--top N] [--inject]
                                          [--out PATH]

Exit codes
----------
    0 — scaffold generated (or injected, with --inject)
    1 — fewer than 3 candidates found (invariant 5 needs ≥ 3 named
        adjacent surveys; with too few candidates the scaffold cannot
        satisfy the invariant on its own)
    2 — input error (missing required file)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SURVEY_TITLE_PATTERN = re.compile(
    r"\b(?:survey|review|overview|tutorial|primer)\b",
    re.IGNORECASE,
)
SURVEY_TYPES = {"survey", "review", "book", "book-chapter"}


def looks_like_survey(rec: dict[str, Any]) -> bool:
    """True if the record looks like an adjacent survey we'd want to cite.

    Heuristic; the writer (LLM) makes the final call on whether to include
    the candidate.
    """
    title = (rec.get("title") or "").strip()
    if not title:
        return False
    rtype = (rec.get("type") or "").strip().lower()
    if rtype in SURVEY_TYPES:
        return True
    if SURVEY_TITLE_PATTERN.search(title):
        # Filter out obvious noise: PRISMA-style methodology checklists are
        # surveys *of* the survey-search procedure, not adjacent literature
        # surveys.
        if "prisma" in title.lower():
            return False
        # Exclude very-many-author anthologies (likely conference proceedings).
        if (rec.get("author_count") or 0) > 40:
            return False
        return True
    return False


def load_candidates(run_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fname in ("filtered.jsonl", "tech_reports.jsonl"):
        path = run_dir / "1_search" / fname
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[WARN] {fname}:{lineno} JSON decode failed: {exc.msg}",
                      file=sys.stderr)
                continue
            if looks_like_survey(rec):
                out.append(rec)
    return out


def rank_candidates(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        cands,
        key=lambda r: (
            -int(r.get("cited_by_count") or 0),
            -int(r.get("year") or r.get("publication_year") or 0),
            r.get("cite_key") or "",
        ),
    )


SCAFFOLD_BEGIN = "% RELATED_SURVEYS_BEGIN — scaffold_related_surveys.py"
SCAFFOLD_END = "% RELATED_SURVEYS_END"


def render_scaffold(top: list[dict[str, Any]]) -> str:
    """Render the LaTeX subsection between `BEGIN`/`END` markers.

    The subsection title is fixed (`Relationship to existing surveys`)
    so the audit's title regex matches.
    """
    lines = [
        SCAFFOLD_BEGIN,
        r"\subsection{Relationship to existing surveys}",
        "",
        ("% Each adjacent survey gets one sentence stating the delta — what "
         "*this* survey covers that the cited survey does not, and "
         "vice-versa. Do not paraphrase the cited surveys; state the "
         "comparative scope only."),
        "",
    ]
    for rec in top:
        cite_key = rec.get("cite_key") or rec.get("paper_id") or "UNKNOWN"
        title = (rec.get("title") or "").strip()
        # First-author surname for the \citet rendering reference.
        authors = rec.get("authors")
        first = ""
        if isinstance(authors, list) and authors:
            a0 = authors[0]
            first = (a0.get("name") if isinstance(a0, dict) else str(a0)) or ""
        elif isinstance(authors, str):
            first = authors.split(",")[0].strip()
        first_surname = first.split()[-1] if first else ""
        year = rec.get("year") or rec.get("publication_year") or ""
        author_year_hint = (f"{first_surname} ({year})"
                            if first_surname and year else cite_key)
        lines += [
            f"\\citet{{{cite_key}}}",
            f"% candidate: {author_year_hint} — {title}",
            ("% TODO: state the delta in 1–2 sentences (what this survey "
             "covers that the cited one does not, and vice-versa)."),
            "",
        ]
    lines.append(SCAFFOLD_END)
    return "\n".join(lines) + "\n"


def inject(scaffold: str, target_tex: Path) -> bool:
    """Splice scaffold into `target_tex`. Idempotent: a previous scaffold
    is replaced by re-anchoring on the BEGIN/END markers.

    Returns True if the file was modified.
    """
    if not target_tex.exists():
        target_tex.parent.mkdir(parents=True, exist_ok=True)
        target_tex.write_text(scaffold)
        return True
    content = target_tex.read_text()
    pattern = re.compile(
        re.escape(SCAFFOLD_BEGIN) + r".*?" + re.escape(SCAFFOLD_END) + r"\n?",
        re.DOTALL,
    )
    if pattern.search(content):
        # `scaffold` contains LaTeX backslashes (\subsection, \citet, …);
        # passing it as the replacement string makes re.sub interpret
        # `\s` etc. as group back-references. Use a callable replacement
        # to short-circuit that interpretation.
        new = pattern.sub(lambda _m: scaffold, content, count=1)
    else:
        # Append to the end of the existing background section.
        new = content.rstrip() + "\n\n" + scaffold
    if new == content:
        return False
    target_tex.write_text(new)
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run_dir", type=Path)
    p.add_argument(
        "--top", type=int, default=5,
        help="Maximum number of adjacent-survey candidates to include "
             "in the scaffold (default 5; need ≥ 3 for invariant 5).",
    )
    p.add_argument(
        "--inject", action="store_true",
        help="Splice the scaffold into 5_paper/sections/02_background.tex "
             "between RELATED_SURVEYS_BEGIN/END markers (idempotent). If "
             "the file is missing, write the scaffold there.",
    )
    p.add_argument(
        "--out", type=Path, default=None,
        help="Override the scaffold output path (default: "
             "5_paper/sections/02_background.related_surveys.tex).",
    )
    args = p.parse_args(argv)

    run_dir: Path = args.run_dir.expanduser().resolve()
    if not run_dir.exists():
        print(f"ERROR: run dir not found: {run_dir}", file=sys.stderr)
        return 2
    filtered = run_dir / "1_search" / "filtered.jsonl"
    if not filtered.exists():
        print(f"ERROR: required file missing: {filtered}", file=sys.stderr)
        return 2

    cands = load_candidates(run_dir)
    ranked = rank_candidates(cands)
    top = ranked[: args.top]

    print(f"scaffold_related_surveys: {len(cands)} candidate(s) found, "
          f"using top {len(top)}.")
    for i, rec in enumerate(top, 1):
        print(f"  {i}. {rec.get('cite_key', '?'):<35s} "
              f"({rec.get('year') or rec.get('publication_year') or '????'}) "
              f"— {(rec.get('title') or '')[:80]}")

    scaffold = render_scaffold(top)

    out_path = args.out or (
        run_dir / "5_paper" / "sections" / "02_background.related_surveys.tex"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(scaffold)
    print(f"Wrote scaffold → {out_path}")

    if args.inject:
        target = run_dir / "5_paper" / "sections" / "02_background.tex"
        modified = inject(scaffold, target)
        if modified:
            print(f"Injected scaffold into {target}")
        else:
            print(f"Scaffold already up to date in {target}")

    if len(top) < 3:
        print(
            "\n⚠ fewer than 3 candidates: invariant 5 requires "
            "≥ 3 named adjacent surveys. Either broaden the "
            "search query (survey-search Step 1) or hand-add adjacent "
            "surveys to the bibliography before re-running.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
