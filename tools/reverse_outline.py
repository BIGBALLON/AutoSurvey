#!/usr/bin/env python3
"""
reverse_outline.py — survey-write Step 7 (Reverse Outline Test).

After all sections are drafted, extract the first sentence of every paragraph
and verify the topic-sentence chain forms a coherent narrative.

Specifically, this tool:
  1. Extracts the first sentence of every paragraph in every section.
  2. Per-section: builds a flat "topic sentence chain" report.
  3. Cross-section: builds a survey-wide narrative skeleton.
  4. Flags topic sentences that are likely WEAK:
     - too short (<8 words) — probably a placeholder
     - generic openers ("Recent advances...", "In this section...")
     - starting with "We" / "This" multiple times in a row
     - paragraphs with no topic sentence (start mid-thought)
  5. Optionally writes a `4_outline/reverse_outline.md` report.

Modes:
  --report PATH — write Markdown report to PATH (default: 4_outline/reverse_outline.md)
  --json PATH   — write JSON findings to PATH (optional, for tooling)
  --strict      — exit non-zero if any weak topic sentence found

Usage:
  reverse_outline.py <run_dir>
  reverse_outline.py <run_dir> --strict
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# tools/_latex_text — shared LaTeX helpers
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _latex_text import (  # noqa: E402
    has_prose as _has_prose,
    strip_latex_for_word_count as _strip_latex_for_word_count,
)

# --- Heuristics for weak topic sentences --------------------------------------------

GENERIC_OPENERS = [
    r"^recent advances?\b",
    r"^in this section\b",
    r"^this section (will|presents?|describes?|covers?)\b",
    r"^we (begin|start|present|describe|first|now)\b",
    r"^the field\b",
    r"^there (is|are|exist|exists|has been|have been)\b",
    r"^it (is|has been|can be)\b",
    r"^one (notable|important|key)\b",
    r"^a (number|variety|wide range)\b",
]
GENERIC_RE = [re.compile(p, re.IGNORECASE) for p in GENERIC_OPENERS]

WORDS_PER_SENTENCE_MIN = 8


def extract_section_metadata(text: str) -> dict:
    """Extract section title and first sentence of each non-empty paragraph."""
    # Strip LaTeX comments and \label/\section commands
    cleaned = re.sub(r"%[^\n]*", "", text)
    title_m = re.search(r"\\section\{([^}]+)\}", cleaned)
    subtitle = re.findall(r"\\subsection\{([^}]+)\}", cleaned)
    section_title = title_m.group(1) if title_m else None
    # Remove all \section and \subsection commands AND \label
    body = re.sub(r"\\(section|subsection|label)\{[^}]*\}", "", cleaned)

    # Split into paragraphs (blank-line separated)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]

    topic_sentences = []
    for p in paragraphs:
        # Skip paragraphs that are pure LaTeX (no prose). Common shapes:
        #   \input{figures/tables/...}
        #   \begin{figure} ... \end{figure}
        #   \includegraphics[...]{...}
        # These trigger spurious "weak topic sentence" warnings
        # because they yield 0 prose words after LaTeX stripping.
        if not _has_prose(p):
            continue
        # Strip leading LaTeX commands like \begin{...} or \end{...}
        # Get the first sentence (period-terminated, conservative)
        # Strip inline LaTeX commands for word counting
        first_sent = extract_first_sentence(p)
        if first_sent:
            topic_sentences.append(first_sent)

    return {
        "section_title": section_title,
        "subsection_titles": subtitle,
        "n_paragraphs": len(paragraphs),
        "topic_sentences": topic_sentences,
    }


def extract_first_sentence(paragraph: str) -> str | None:
    """Return the first sentence in a paragraph, with LaTeX commands stripped for the
    purpose of judging strength (but keep the original text for display)."""
    # Strip leading inline LaTeX environments
    p = paragraph.strip()
    # Remove any leading \begin{quote}, \begin{itemize}, etc. but only the begin tag
    p = re.sub(r"^\\begin\{[^}]+\}\s*", "", p)
    # If the paragraph starts with \cite{...} or \textbf{...}, that's still part of
    # the first sentence; we just want to find the first period.
    # Conservative: find first '.' '!' or '?' followed by space + uppercase / EOF
    # Use a simple regex that handles abbreviations rarely.
    m = re.search(r"(.+?[.!?])(\s+[A-Z\\]|\s*$)", p, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: first 200 chars
    return p[:200] if p else None


def is_weak(sentence: str) -> tuple[bool, list[str]]:
    """Return (is_weak, reasons)."""
    reasons = []
    # Strip LaTeX commands for word-count purposes, but keep visible text
    # inside \textbf{...}/\emph{...} etc. so that '\textbf{US
    # labs} dominate the dense-scaling-laws literature' counts as 8 words,
    # not 6.
    cleaned = _strip_latex_for_word_count(sentence)
    cleaned = re.sub(r"\$[^$]*\$", " EQ ", cleaned)
    n_words = len(cleaned.split())
    if n_words < WORDS_PER_SENTENCE_MIN:
        reasons.append(f"too short ({n_words} words)")
    for pat in GENERIC_RE:
        if pat.search(cleaned):
            reasons.append(f"generic opener ({pat.pattern})")
            break
    return (len(reasons) > 0, reasons)


def render_markdown_report(sections: list[dict], findings: dict) -> str:
    out = ["# Reverse Outline Report", ""]
    out.append("_Generated by `tools/reverse_outline.py`_")
    out.append("")
    out.append(f"**Sections analyzed:** {len(sections)}")
    out.append(f"**Total paragraphs:** {sum(s['n_paragraphs'] for s in sections)}")
    out.append(f"**Weak topic sentences flagged:** {findings['total_weak']}")
    out.append("")

    out.append("## Survey-Wide Narrative Skeleton")
    out.append("")
    out.append("_The first sentence of every paragraph, in order. Reading these aloud should reveal the survey's spine; if it doesn't, the prose has incoherence._")
    out.append("")
    for s in sections:
        if not s["section_title"]:
            continue
        out.append(f"### {s['section_title']}")
        out.append("")
        if not s["topic_sentences"]:
            out.append("_(no paragraphs)_")
            out.append("")
            continue
        for i, sent in enumerate(s["topic_sentences"], start=1):
            weak, reasons = is_weak(sent)
            marker = "⚠ " if weak else ""
            note = f"  *(weak: {', '.join(reasons)})*" if weak else ""
            out.append(f"{i}. {marker}{sent}{note}")
        out.append("")

    if findings["weak_sentences"]:
        out.append("## Flagged Weak Topic Sentences")
        out.append("")
        out.append("Each of these is the first sentence of a paragraph and is unlikely to advance the narrative. Consider rewriting.")
        out.append("")
        for entry in findings["weak_sentences"]:
            out.append(f"- **[{entry['section']}]** {entry['sentence']}  *(reasons: {', '.join(entry['reasons'])})*")
        out.append("")

    out.append("## Repeated Openers")
    out.append("")
    if findings["repeated_openers"]:
        out.append("Three or more consecutive paragraphs starting with the same word — usually a sign of unsynthesized prose:")
        out.append("")
        for run in findings["repeated_openers"]:
            out.append(f"- **[{run['section']}]** {run['count']} consecutive paragraphs start with `{run['word']}`")
    else:
        out.append("_None detected._")
    out.append("")

    out.append("## How to Use This Report")
    out.append("")
    out.append("1. Read the **Survey-Wide Narrative Skeleton** aloud. If a sentence")
    out.append("   doesn't follow naturally from the previous one, the paragraph it")
    out.append("   belongs to needs a stronger topic sentence.")
    out.append("2. For each **Flagged Weak Topic Sentence**, rewrite to state the")
    out.append("   paragraph's claim or comparative point upfront.")
    out.append("3. For each **Repeated Opener**, vary the syntactic structure or merge")
    out.append("   adjacent paragraphs that are doing the same job.")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--report", type=Path, default=None,
                   help="Markdown report path (default: 4_outline/reverse_outline.md)")
    p.add_argument("--json", type=Path, default=None,
                   help="Optional JSON findings path")
    p.add_argument("--strict", action="store_true",
                   help="Exit non-zero if any weak topic sentence found")
    args = p.parse_args()

    run_dir = args.run_dir.expanduser().resolve()
    sections_dir = run_dir / "5_paper" / "sections"
    if not sections_dir.exists():
        print(f"ERROR: {sections_dir} not found", file=sys.stderr)
        return 2

    section_files = sorted(sections_dir.glob("*.tex"))
    sections = []
    for f in section_files:
        meta = extract_section_metadata(f.read_text())
        meta["filename"] = f.name
        sections.append(meta)

    # Find weak sentences
    weak_sentences = []
    for s in sections:
        for sent in s["topic_sentences"]:
            is_w, reasons = is_weak(sent)
            if is_w:
                weak_sentences.append({
                    "section": s["section_title"] or s["filename"],
                    "sentence": sent,
                    "reasons": reasons,
                })

    # Find repeated openers (3+ consecutive paragraphs starting with same word)
    repeated_openers = []
    for s in sections:
        if not s["topic_sentences"]:
            continue
        last_word = None
        run_count = 0
        for sent in s["topic_sentences"]:
            # First non-LaTeX word
            cleaned = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})*", " ", sent)
            words = cleaned.strip().split()
            if not words:
                continue
            w = words[0].rstrip(",.").lower()
            if w == last_word:
                run_count += 1
                if run_count >= 3:
                    repeated_openers.append({
                        "section": s["section_title"] or s["filename"],
                        "word": w,
                        "count": run_count,
                    })
            else:
                last_word = w
                run_count = 1

    findings = {
        "total_sections": len(sections),
        "total_paragraphs": sum(s["n_paragraphs"] for s in sections),
        "total_weak": len(weak_sentences),
        "weak_sentences": weak_sentences,
        "repeated_openers": repeated_openers,
    }

    report_path = args.report or (run_dir / "4_outline" / "reverse_outline.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown_report(sections, findings))

    print("=" * 60)
    print("reverse_outline — narrative coherence audit")
    print("=" * 60)
    print(f"  Sections:          {findings['total_sections']}")
    print(f"  Total paragraphs:  {findings['total_paragraphs']}")
    print(f"  Weak topic sentences:  {findings['total_weak']}")
    print(f"  Repeated openers:      {len(findings['repeated_openers'])}")
    print(f"  Report → {report_path}")

    if args.json:
        args.json.write_text(json.dumps(findings, indent=2))
        print(f"  JSON   → {args.json}")

    if args.strict and findings["total_weak"] > 0:
        print("\n❌ Strict mode: weak topic sentences found")
        return 1
    print("\n✅ Reverse outline analysis complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
