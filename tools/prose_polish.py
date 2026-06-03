#!/usr/bin/env python3
"""
prose_polish.py — survey-write quality audit Pass 1+5 (clutter + compile-readiness).

Performs deterministic prose cleanup that should run BEFORE LLM-driven content review:
  1. Strip AI-isms (delve, pivotal, landscape, tapestry, …)
  2. Replace cluttered phrases with concise alternatives
  3. Normalize unicode dashes / quotes / ellipses for LaTeX (em-dash → ---, en-dash → --)
  4. Flag sentences > MAX_SENTENCE_WORDS (default 40) for splitting
  5. Flag paragraphs > MAX_PARAGRAPH_SENTENCES (default 8)
  6. Detect synonym churn for defined terms (Banana Rule)
  7. Compute passive-voice density per section

Modes:
  --check       — read-only; print findings, exit non-zero if MAX_FAIL_THRESHOLD exceeded
  --fix         — apply deterministic substitutions (AI-isms, clutter, dashes); leave
                  long-sentence/paragraph and passive-voice findings as warnings
  --report PATH — write findings as JSON to PATH

Exit codes:
  0  — clean (or --fix succeeded with no critical issues)
  1  — critical issues remain (e.g., AI-isms still present after --fix)
  2  — input error (missing files, etc.)

Usage:
  prose_polish.py <run_dir> --check
  prose_polish.py <run_dir> --fix
  prose_polish.py <run_dir> --fix --report out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# tools/_latex_text — shared LaTeX helpers
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _latex_text import (  # noqa: E402
    strip_leading_latex_commands as _strip_leading_latex_commands,
    strip_latex_for_word_count as _strip_latex_for_word_count,
)

# --- Pass 1: AI-isms (replace or delete entirely) -----------------------------------
# Order matters: longer phrases first to prevent partial matches.
AI_ISMS: list[tuple[str, str]] = [
    # Phrases (case-insensitive matched on word boundaries)
    (r"\bdelve(s|d|ing)?\s+into\b", "examine"),
    (r"\bpivotal\s+role\b", "role"),
    (r"\bin\s+the\s+landscape\s+of\b", "in"),
    (r"\bthe\s+landscape\s+of\b", "the field of"),
    (r"\brich\s+tapestry\s+of\b", "range of"),
    (r"\btapestry\s+of\b", "range of"),
    (r"\bunderscore(s)?\s+the\s+importance\b", r"emphasize\1 the importance"),
    (r"\bit\s+is\s+worth\s+noting\s+that\b", ""),
    (r"\bit\s+is\s+important\s+to\s+note\s+that\b", ""),
    (r"\bnoteworthy(\s+that)?\b", ""),
    (r"\bintriguingly\b", ""),
    (r"\bseamlessly\b", ""),
    (r"\bnavigate\s+the\s+complexities?\s+of\b", "address"),
    # Single tokens (replace with empty + clean punctuation later)
    (r"\bdelve\b", "examine"),
    (r"\bpivotal\b", "key"),
    (r"\btapestry\b", "range"),
]

# --- Pass 1b: Cluttered phrases ------------------------------------------------------
CLUTTER: list[tuple[str, str]] = [
    (r"\bdue\s+to\s+the\s+fact\s+that\b", "because"),
    (r"\bin\s+order\s+to\b", "to"),
    (r"\ba\s+number\s+of\b", "several"),
    (r"\bat\s+the\s+present\s+time\b", "now"),
    (r"\bon\s+the\s+basis\s+of\b", "based on"),
    (r"\bin\s+light\s+of\s+the\s+fact\s+that\b", "because"),
    (r"\bhave\s+an\s+effect\s+on\b", "affect"),
    (r"\bgive(s|n)?\s+rise\s+to\b", r"cause\1"),
    (r"\bcompletely\s+eliminate(s|d)?\b", r"eliminate\1"),
    (r"\bunexpected\s+surprise\b", "surprise"),
    (r"\bfuture\s+plans\b", "plans"),
    (r"\bperform(s|ed|ing)?\s+an?\s+analysis\s+of\b", r"analyze"),
    (r"\bmake(s|made)?\s+use\s+of\b", r"use\1"),
    (r"\bin\s+the\s+event\s+that\b", "if"),
    (r"\bthe\s+majority\s+of\b", "most"),
    (r"\bwith\s+respect\s+to\b", "for"),
    (r"\bin\s+terms\s+of\b", "for"),  # often
]

# --- Pass 5: Compile-readiness substitutions ----------------------------------------
COMPILE_FIXES: list[tuple[str, str]] = [
    # Unicode dashes → LaTeX equivalents
    ("—", "---"),  # em dash
    ("–", "--"),    # en dash
    ("‐", "-"),     # hyphen (Unicode form)
    # Smart quotes → LaTeX equivalents
    ("“", "``"),
    ("”", "''"),
    ("‘", "`"),
    ("’", "'"),
    # Ellipsis
    ("…", "\\ldots{}"),
    # Greek letters that sometimes leak in (only outside math mode — see safe_apply)
    # Note: we DO NOT auto-fix these because they may be intentional in math mode.
]

# --- Passive voice heuristic --------------------------------------------------------
# Common to-be + past participle patterns. Not exhaustive; used for density only.
PASSIVE_PATTERN = re.compile(
    r"\b(?:is|are|was|were|been|being|be)\s+(?:[a-z]+ly\s+)?"
    r"([a-z]+ed|[a-z]+en|shown|given|made|done|seen|known|taken|found|written|drawn|chosen)\b",
    re.IGNORECASE,
)

# --- Sentence splitter (regex-based, conservative) ----------------------------------
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\\])")


def safe_apply(text: str, patterns: list[tuple[str, str]], *, fix: bool) -> tuple[str, list[dict]]:
    """Apply regex substitutions outside of LaTeX math/comment regions.

    Returns (new_text, hits) where hits is a list of {"pattern":..., "matches":[...]}.
    """
    # Split into safe / unsafe regions:
    #   unsafe = $...$, $$...$$, \begin{equation}..\end{equation}, \begin{align}..\end{align},
    #            line comments starting with %, \cite{...}, \ref{...}, \label{...}
    # We tokenize and process only "safe" regions.
    safe_re = re.compile(
        r"(\$\$.*?\$\$|\$[^$]*\$|"
        r"\\begin\{(?:equation|align|equation\*|align\*|gather|gather\*)\}.*?\\end\{(?:equation|align|equation\*|align\*|gather|gather\*)\}|"
        r"%[^\n]*|"
        r"\\(?:cite|ref|label|input|cref|Cref|citep|citet)\{[^}]*\})",
        re.DOTALL,
    )
    hits: list[dict] = []

    def process_safe(s: str) -> str:
        for pat, repl in patterns:
            new_s, n = re.subn(pat, repl, s, flags=re.IGNORECASE)
            if n > 0:
                hits.append({"pattern": pat, "replacement": repl, "count": n})
                if fix:
                    s = new_s
        return s

    out = []
    last = 0
    for m in safe_re.finditer(text):
        # Process safe region before this match
        out.append(process_safe(text[last:m.start()]))
        # Keep unsafe region as-is
        out.append(m.group(0))
        last = m.end()
    # Trailing safe region
    out.append(process_safe(text[last:]))
    return "".join(out), hits


def find_long_sentences(text: str, *, max_words: int = 40) -> list[dict]:
    """Find sentences exceeding word count limit. Strips LaTeX while
    preserving visible text — the shared helper in _latex_text keeps
    the contents of text-bearing commands like \\textbf{...}/\\emph{...}
    so that "\\textbf{Foo}" counts as one word, not zero."""
    # 1. Drop full LaTeX environments — \begin{figure}…\end{figure} etc.
    stripped = re.sub(r"\\begin\{[^}]*\}.*?\\end\{[^}]*\}", " ", text, flags=re.DOTALL)
    # 2. Strip LaTeX comments — but NOT escaped '\%' which is a literal
    # percent sign. (?<!\\)% means '% not preceded by \'. Previously this
    # also ate everything after a '93\%' in body prose, occasionally
    # devouring an entire sentence and silently undercounting long ones.
    stripped = re.sub(r"(?<!\\)%[^\n]*", "", stripped)
    # 3. Three-pass strip that preserves \textbf{...}/\emph{...} contents
    stripped = _strip_latex_for_word_count(stripped)
    # 4. Inline math collapses to a single 'EQ' token (one word)
    stripped = re.sub(r"\$[^$]*\$", " EQ ", stripped)
    sentences = SENTENCE_SPLIT.split(stripped)
    out = []
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        n_words = len(sent.split())
        if n_words > max_words:
            out.append({"word_count": n_words, "preview": sent[:120] + ("..." if len(sent) > 120 else "")})
    return out


def find_long_paragraphs(text: str, *, max_sentences: int = 8) -> list[dict]:
    """Find paragraphs (separated by blank lines) with too many sentences."""
    out = []
    # Strip LaTeX while preserving visible text inside \textbf{...}/\emph{...}
    # so the sentence-count below isn't artificially deflated by losing the
    # text inside those commands. Comments stripped first, but
    # only true '%' line-comments — '\%' is a literal percent sign.
    cleaned = re.sub(r"(?<!\\)%[^\n]*", "", text)
    cleaned = _strip_latex_for_word_count(cleaned)
    cleaned = re.sub(r"\$[^$]*\$", " EQ ", cleaned)
    paragraphs = re.split(r"\n\s*\n", cleaned)
    for i, p in enumerate(paragraphs):
        p = p.strip()
        if not p:
            continue
        sentences = SENTENCE_SPLIT.split(p)
        n_sentences = sum(1 for s in sentences if s.strip())
        if n_sentences > max_sentences:
            out.append({"paragraph_index": i, "sentence_count": n_sentences,
                        "preview": p[:120].replace("\n", " ") + "..."})
    return out


def passive_voice_density(text: str) -> dict:
    """Approximate passive-voice density per 1000 words."""
    cleaned = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})*", " ", text)
    cleaned = re.sub(r"\$[^$]*\$", " ", cleaned)
    cleaned = re.sub(r"%[^\n]*", "", cleaned)
    n_words = len(cleaned.split())
    n_passive = len(PASSIVE_PATTERN.findall(cleaned))
    rate = (n_passive * 1000.0 / n_words) if n_words > 0 else 0.0
    return {"words": n_words, "passive_count": n_passive, "passive_per_1000_words": round(rate, 2)}


def detect_synonym_churn(sections: dict[str, str], glossary: list[tuple[str, list[str]]]) -> list[dict]:
    """Banana rule: if a defined term has known synonyms used elsewhere, flag.
    glossary entries are (canonical_term, [synonym_phrases]).
    """
    out = []
    for canonical, synonyms in glossary:
        canon_count = 0
        synonym_hits: dict[str, dict] = {s: {"count": 0, "sections": []} for s in synonyms}
        for sid, text in sections.items():
            canon_count += len(re.findall(rf"\b{re.escape(canonical)}\b", text, re.IGNORECASE))
            for syn in synonyms:
                hits = len(re.findall(rf"\b{re.escape(syn)}\b", text, re.IGNORECASE))
                if hits > 0:
                    synonym_hits[syn]["count"] += hits
                    synonym_hits[syn]["sections"].append(sid)
        if canon_count > 0 and any(v["count"] > 0 for v in synonym_hits.values()):
            for syn, info in synonym_hits.items():
                if info["count"] > 0:
                    out.append({
                        "canonical": canonical,
                        "synonym": syn,
                        "synonym_count": info["count"],
                        "synonym_sections": info["sections"],
                        "canonical_count": canon_count,
                    })
    return out


# Default glossary for LLM-architecture-style surveys; user can override via JSON file.
DEFAULT_GLOSSARY: list[tuple[str, list[str]]] = [
    ("self-attention", ["attention block", "attention mechanism", "scaled-dot attention"]),
    ("transformer", ["transformer architecture", "transformer-based model"]),
    ("parameter-efficient fine-tuning", ["lightweight fine-tuning", "lightweight adaptation"]),
    ("retrieval-augmented generation", ["retrieval-augmented LM", "retrieval-augmented model"]),
    ("state space model", ["SSM model"]),  # SSM alone is fine
]


# ---------------------------------------------------------------------------
# NARRATIVE_RULES — document-level scaffolding checks (Hook / Why-Now /
# Relationship to Existing Surveys / numbered Contributions).
# Spec: skills/shared-references/narrative-scaffolding.md
# ---------------------------------------------------------------------------

# Heuristic regexes for the Hook detector — Intro first paragraph must contain
# A well-formed hook signals three things in the first paragraph of
# the Introduction:
#   - a concrete year anchor ("In 2022, …" / "By 2025, …")
#   - a hard number (the year itself counts; or a metric like "70%",
#     "$15 per paper", "18 months")
#   - a metaphor or before-vs-after framing ("from typewriters to
#     colleagues", "transition", "paradigm shift")
# `_HOOK_NUMBER_RE` ends with an optional unit so it also matches a
# bare year like "2022" — that is intentional, since a year *is* a
# concrete number.
_HOOK_YEAR_RE = re.compile(r"\b(?:19\d\d|20\d\d)\b")
_HOOK_NUMBER_RE = re.compile(
    r"\$\d|\b\d+(?:\.\d+)?\s*"
    r"(?:%|×|x|B|M|K|months?|years?|weeks?|days?)?\b"
)
_HOOK_METAPHOR_RE = re.compile(
    r"\bfrom\b[^.]*\bto\b"
    r"|---|—"
    r"|\blike\b"
    r"|\bbecame\b|\bbecome\b"
    r"|\btransition\b"
    r"|\bparadigm shift\b",
    re.IGNORECASE,
)


def _strip_latex_comments(text: str) -> str:
    return "\n".join(line.split("%", 1)[0] for line in text.splitlines())


# this helper now lives in tools/_latex_text. Keep a private
# alias so call sites (and tests importing _strip_leading_latex_commands)
# don't need to change.



def _first_paragraph_after(text: str, marker_pattern: str) -> str | None:
    """Return the first non-empty paragraph appearing after a given LaTeX
    section/subsection marker, or None if the marker isn't found.

    A 'paragraph' is the first chunk between blank lines whose content,
    after stripping LaTeX structural commands at the head (\\label{...},
    \\subsection{...}, \\textbf{...}), still contains real prose. This
    is more robust than ``not chunk.startswith('\\\\')`` — virtually
    every well-typeset Intro paragraph begins with a structural command.
    """
    m = re.search(marker_pattern, text)
    if not m:
        return None
    rest = text[m.end():]
    for chunk in re.split(r"\n\s*\n", rest):
        prose = _strip_latex_comments(chunk).strip()
        if not prose:
            continue
        body = _strip_leading_latex_commands(prose)
        if body:
            # Return the *original* prose so downstream regexes (year /
            # number / metaphor) see the same text the reader sees.
            return prose
    return None


def check_narrative_pillars(sections: dict[str, str]) -> dict[str, Any]:
    """Run the 4 document-level pillar checks. Returns:
        {
          "hook":           {"present": bool, "evidence": str},
          "why_now":        {"present": bool, "evidence": str},
          "relationship":   {"present": bool, "evidence": str},
          "contributions":  {"present": bool, "count": int},
          "score":          float (passing / 4),
        }
    Sections argument is {section_id: text} from the per-section pass.
    """
    # The Intro section is conventionally `01_intro` or any *_intro / 01_*.
    intro_text = ""
    for sid, text in sections.items():
        if "intro" in sid.lower() or sid.startswith("01"):
            intro_text = text
            break

    abstract_text = ""
    for sid, text in sections.items():
        if "abstract" in sid.lower() or sid.startswith("00"):
            abstract_text = text
            break

    # --- Pillar 1: Hook (Intro first paragraph) ---
    hook = {"present": False, "evidence": ""}
    # Match the full \section{Title} so the rest-of-text doesn't carry
    # the closing 'Title}' as the (false) first paragraph.
    intro_first = _first_paragraph_after(intro_text, r"\\section\*?\{[^}]*\}")
    if intro_first:
        has_year = bool(_HOOK_YEAR_RE.search(intro_first))
        has_num  = bool(_HOOK_NUMBER_RE.search(intro_first))
        has_meta = bool(_HOOK_METAPHOR_RE.search(intro_first))
        # Require any 2 of {year, number, metaphor}. The benchmark survey
        # opens with year + metaphor and saves the metric for paragraph 2.
        signals = sum([has_year, has_num, has_meta])
        hook["present"] = signals >= 2
        hook["evidence"] = (
            f"year={has_year}, number={has_num}, metaphor={has_meta} "
            f"({signals}/3)"
        )

    # --- Pillar 2: Why Now? subsection ---
    why_now_re = re.compile(
        r"\\subsection\*?\s*\{\s*(Why Now\??|The Inflection Point|Why this survey now)\s*\}",
        re.IGNORECASE,
    )
    why_now = {"present": bool(why_now_re.search(intro_text)), "evidence": ""}
    if why_now["present"]:
        why_now["evidence"] = why_now_re.search(intro_text).group(0)

    # --- Pillar 3: Relationship to Existing Surveys ---
    rel_re = re.compile(
        r"\\subsection\*?\s*\{\s*(Relationship to (Existing|Prior) Surveys|Differences from Existing Surveys)\s*\}",
        re.IGNORECASE,
    )
    relationship = {"present": bool(rel_re.search(intro_text)), "evidence": ""}
    if relationship["present"]:
        relationship["evidence"] = rel_re.search(intro_text).group(0)

    # --- Pillar 4: Numbered Contributions ---
    # Two acceptable styles:
    #   * \begin{enumerate} ... ≥4 \item ... \end{enumerate}
    #   * ≥4 in-prose \textbf{(1)} … \textbf{(2)} … markers in strict
    #     1, 2, 3, … sequence (used by many ICLR/NeurIPS-style intros)
    contrib = {"present": False, "count": 0}
    enum_re = re.compile(
        r"\\begin\{enumerate\}(.*?)\\end\{enumerate\}",
        re.DOTALL,
    )
    for enum_match in enum_re.finditer(intro_text):
        items = re.findall(r"\\item\b", enum_match.group(1))
        if len(items) >= 4:
            contrib["present"] = True
            contrib["count"] = len(items)
            break
    if not contrib["present"]:
        nums = [int(m.group(1)) for m in re.finditer(
            r"\\textbf\s*\{\s*\(\s*(\d{1,2})\s*\)", intro_text)]
        run = 0
        for n in nums:
            if n == run + 1:
                run += 1
            else:
                break
        if run >= 4:
            contrib["present"] = True
            contrib["count"] = run

    score = sum(int(p["present"]) for p in (hook, why_now, relationship, contrib)) / 4
    return {
        "hook": hook,
        "why_now": why_now,
        "relationship": relationship,
        "contributions": contrib,
        "score": round(score, 2),
        "abstract_present": bool(abstract_text),
    }


# ---------------------------------------------------------------------------
# ARGUMENT_RULES — per-section 5-step skeleton anchor checks.
# Spec: skills/shared-references/argument-skeleton.md
# ---------------------------------------------------------------------------

ANCHOR_ORDER = ["CLAIM", "STEELMAN", "EVIDENCE", "CONCESSION", "SO-WHAT"]
_ANCHOR_RE = re.compile(r"^\s*%\s*\[([A-Z\-]+)\]", re.MULTILINE)

# Sections that SHOULDN'T have the 5-anchor skeleton. 00_abstract uses
# 5-sentence form; 01_intro uses 4-pillar narrative; sections containing
# "open" or "problem" use the 4-bucket Open Problems form; sections
# containing "conclusion" wrap the thesis differently.
_SKIP_PATTERNS = re.compile(
    r"(?:^00|^01|abstract|introduction|open[\s_-]*problem"
    r"|future(?:[\s_-]*direction|[\s_-]*work)?|conclusion|trends?"
    r"|feature[\s_-]*matrix|cross[\s_-]*cutting)",
    re.IGNORECASE,
)


def _strip_float_environments(tex: str) -> str:
    """Drop table/figure floats before sentence-level citation scanning so a
    citation-per-row comparison matrix is not mistaken for one over-cited
    sentence (mirrors audit_writing._strip_float_environments)."""
    return re.sub(
        r"\\begin\{(table\*?|figure\*?|tabular)\}.*?\\end\{\1\}",
        " ", tex, flags=re.DOTALL,
    )


def check_conclusion_reframe(sections: dict[str, str]) -> dict[str, Any]:
    """Structural-template invariant 7: conclusion is a re-frame, not a
    summary.

    Returns ``{present, word_count, in_range, has_bullets, ok, evidence}``.
    The audit lives in ``audit_writing.py audit_structural_template``;
    this function is the prose-polish-side preview so the writer sees the
    same signal at every iteration.
    """
    body: str | None = None
    sec_id: str | None = None
    for sid, tex in sections.items():
        if re.search(r"conclusion|conclud[a-z]*remarks?", sid, re.IGNORECASE):
            body = tex
            sec_id = sid
            break
    if body is None:
        return {"present": False, "word_count": 0, "in_range": False,
                "has_bullets": False, "ok": False,
                "evidence": "no conclusion section found"}
    stripped = _strip_latex_comments(body)
    n_words = len(re.findall(r"\b\w+\b", stripped))
    in_range = 400 <= n_words <= 700
    bullet_lines = len(re.findall(
        r"^\s*\\item\b|^\s*-\s+|^\s*\*\s+", stripped, re.MULTILINE,
    ))
    bullet_density = bullet_lines / max(1, n_words / 50)
    has_bullets = bullet_density >= 0.5
    ok = in_range and not has_bullets
    bits = [f"{n_words} words"]
    if not in_range:
        bits.append("outside [400..700]")
    if has_bullets:
        bits.append("looks bulleted")
    return {"present": True, "word_count": n_words, "in_range": in_range,
            "has_bullets": has_bullets, "ok": ok,
            "section": sec_id, "evidence": ", ".join(bits)}


def check_citation_density(sections: dict[str, str]) -> dict[str, Any]:
    """Structural-template invariant 2: ≤ 12 inline cites / 1 K body words.

    Mirrors the more thorough check in audit_writing; we keep a copy here
    so prose_polish flags it during the deterministic pass.
    """
    n_words = 0
    n_cites = 0
    over_cap_sentences: list[dict] = []
    for sid, tex in sections.items():
        body = _strip_latex_comments(tex)
        n_words += len(re.findall(r"\b\w+\b", body))
        # Table/figure citations are structural (one \citep per matrix row),
        # not prose; exclude floats from the per-sentence citation scan.
        prose = _strip_float_environments(body)
        for sentence in re.split(r"(?<=[.!?])\s+", prose):
            sentence = sentence.strip()
            if not sentence:
                continue
            cites_in_sentence = len(re.findall(r"\\cite[a-z]*\b", sentence))
            n_cites += cites_in_sentence
            if cites_in_sentence > 3:
                over_cap_sentences.append({
                    "section": sid,
                    "n_cites": cites_in_sentence,
                    "preview": sentence[:120],
                })
    density = (n_cites / n_words * 1000) if n_words else 0.0
    return {
        "density_per_1k": round(density, 2),
        "ok": density <= 12.0 and not over_cap_sentences,
        "over_cap_sentences": over_cap_sentences[:5],
    }


def check_argument_anchors(sections: dict[str, str]) -> dict[str, Any]:
    """Per-body-section 5-anchor scan.

    Returns:
        {
          "per_section": { sid: {"anchors_found": [...], "ok": bool, "issue": str} },
          "passing": int, "total": int, "score": float
        }
    """
    per_section: dict[str, dict] = {}
    body_sections = [
        (sid, text) for sid, text in sections.items()
        if not _SKIP_PATTERNS.search(sid)
    ]
    for sid, text in body_sections:
        anchors = [m.group(1) for m in _ANCHOR_RE.finditer(text)]
        info = {"anchors_found": anchors, "ok": True, "issue": ""}
        if anchors == ANCHOR_ORDER:
            info["ok"] = True
        elif set(anchors) == set(ANCHOR_ORDER):
            info["ok"] = False
            info["issue"] = f"anchors present but out of order: {anchors}"
        elif set(anchors) >= set(ANCHOR_ORDER):
            # extras are OK if order of the canonical 5 is preserved
            seen = [a for a in anchors if a in ANCHOR_ORDER]
            if seen == ANCHOR_ORDER:
                info["ok"] = True
            else:
                info["ok"] = False
                info["issue"] = f"canonical anchors out of order amid extras: {seen}"
        else:
            missing = [a for a in ANCHOR_ORDER if a not in anchors]
            info["ok"] = False
            info["issue"] = f"missing anchors: {missing}"
        per_section[sid] = info

    passing = sum(1 for v in per_section.values() if v["ok"])
    total = len(per_section)
    score = round(passing / total, 2) if total else 1.0
    return {"per_section": per_section, "passing": passing, "total": total, "score": score}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run_dir", type=Path, help="Survey run directory")
    p.add_argument("--check", action="store_true", help="Read-only check mode")
    p.add_argument("--fix", action="store_true", help="Apply deterministic fixes in-place")
    p.add_argument("--report", type=Path, help="Write findings JSON to this path")
    p.add_argument("--glossary", type=Path, help="JSON file with [[canonical, [synonyms...]], ...]")
    p.add_argument("--max-sentence-words", type=int, default=40)
    p.add_argument("--max-paragraph-sentences", type=int, default=8)
    p.add_argument("--max-passive-rate", type=float, default=40.0,
                   help="Per 1000 words; warn above this rate")
    p.add_argument("--strict-narrative", action="store_true",
                   help="Treat NARRATIVE_RULES (Hook/Why-Now/Relationship/Contributions) and "
                        "ARGUMENT_RULES (5-anchor) violations as critical for --check exit code. "
                        "Default: scan and report but do not fail on these alone "
                        "(audit_writing.py is the submission gate; this flag is for early CI).")
    args = p.parse_args()

    if not args.check and not args.fix:
        args.check = True  # default

    sections_dir = args.run_dir.expanduser().resolve() / "5_paper" / "sections"
    if not sections_dir.exists():
        print(f"ERROR: sections dir not found: {sections_dir}", file=sys.stderr)
        return 2

    glossary: list[tuple[str, list[str]]] = DEFAULT_GLOSSARY
    if args.glossary and args.glossary.exists():
        glossary = [(c, s) for c, s in json.loads(args.glossary.read_text())]

    section_files = sorted(sections_dir.glob("*.tex"))

    findings: dict[str, Any] = {
        "ai_ism_hits": {},
        "clutter_hits": {},
        "compile_fixes_applied": {},
        "long_sentences": {},
        "long_paragraphs": {},
        "passive_density": {},
        "synonym_churn": [],
        "totals": {"ai_isms": 0, "clutter": 0, "compile_fixes": 0,
                   "long_sentences": 0, "long_paragraphs": 0},
    }

    # Per-section pass
    for f in section_files:
        text = f.read_text()
        new_text = text

        new_text, ai_hits = safe_apply(new_text, AI_ISMS, fix=args.fix)
        if ai_hits:
            findings["ai_ism_hits"][f.name] = ai_hits
            findings["totals"]["ai_isms"] += sum(h["count"] for h in ai_hits)

        new_text, clutter_hits = safe_apply(new_text, CLUTTER, fix=args.fix)
        if clutter_hits:
            findings["clutter_hits"][f.name] = clutter_hits
            findings["totals"]["clutter"] += sum(h["count"] for h in clutter_hits)

        # Compile fixes — straightforward string replacement (safe outside math)
        compile_count = 0
        for old, new in COMPILE_FIXES:
            count = new_text.count(old)
            if count > 0:
                if args.fix:
                    new_text = new_text.replace(old, new)
                compile_count += count
        if compile_count > 0:
            findings["compile_fixes_applied"][f.name] = compile_count
            findings["totals"]["compile_fixes"] += compile_count

        # Long sentences / paragraphs (warning only, not auto-fixed)
        long_sents = find_long_sentences(new_text, max_words=args.max_sentence_words)
        if long_sents:
            findings["long_sentences"][f.name] = long_sents
            findings["totals"]["long_sentences"] += len(long_sents)

        long_paras = find_long_paragraphs(new_text, max_sentences=args.max_paragraph_sentences)
        if long_paras:
            findings["long_paragraphs"][f.name] = long_paras
            findings["totals"]["long_paragraphs"] += len(long_paras)

        # Passive density
        passive = passive_voice_density(new_text)
        findings["passive_density"][f.name] = passive

        # Apply file write if --fix
        if args.fix and new_text != text:
            f.write_text(new_text)

    # Banana rule (cross-section)
    findings["synonym_churn"] = detect_synonym_churn(
        {f.stem: f.read_text() for f in section_files},
        glossary,
    )

    # Print summary
    print("=" * 60)
    print(f"prose_polish — {'fix' if args.fix else 'check'} mode")
    print("=" * 60)
    t = findings["totals"]
    print(f"  AI-isms:          {t['ai_isms']:4d}  ({'fixed' if args.fix else 'flagged'})")
    print(f"  Clutter phrases:  {t['clutter']:4d}  ({'fixed' if args.fix else 'flagged'})")
    print(f"  Compile fixes:    {t['compile_fixes']:4d}  ({'fixed' if args.fix else 'flagged'})")
    print(f"  Long sentences:   {t['long_sentences']:4d}  (>{args.max_sentence_words} words)")
    print(f"  Long paragraphs:  {t['long_paragraphs']:4d}  (>{args.max_paragraph_sentences} sentences)")
    print(f"  Synonym churn:    {len(findings['synonym_churn']):4d}  (banana-rule violations)")

    high_passive = [
        sid for sid, info in findings["passive_density"].items()
        if info["passive_per_1000_words"] > args.max_passive_rate
    ]
    if high_passive:
        print(f"  ⚠  Passive density >{args.max_passive_rate}/1000 in: {high_passive}")

    # ----- NARRATIVE_RULES + ARGUMENT_RULES (read-only, structural) -----
    final_section_texts = {f.stem: f.read_text() for f in section_files}
    narrative = check_narrative_pillars(final_section_texts)
    argument  = check_argument_anchors(final_section_texts)
    conclusion = check_conclusion_reframe(final_section_texts)
    cite_density = check_citation_density(final_section_texts)
    findings["narrative_pillars"]  = narrative
    findings["argument_anchors"]   = argument
    findings["conclusion_reframe"] = conclusion
    findings["citation_density"]   = cite_density

    print()
    print("narrative pillars (document-level):")
    print(f"  Hook (year+number+metaphor in Intro 1st para): "
          f"{'✓' if narrative['hook']['present'] else '✗'}  ({narrative['hook']['evidence']})")
    print(f"  Why Now? subsection:                            "
          f"{'✓' if narrative['why_now']['present'] else '✗'}")
    print(f"  Relationship to Existing Surveys:               "
          f"{'✓' if narrative['relationship']['present'] else '✗'}")
    print(f"  Numbered Contributions (≥4):                    "
          f"{'✓' if narrative['contributions']['present'] else '✗'}  "
          f"(found {narrative['contributions']['count']} items)")
    print(f"  Pillar score:                                    {narrative['score']}")

    print()
    print("argument-skeleton anchors (per body section):")
    print(f"  Body sections passing all 5 anchors in order:   "
          f"{argument['passing']}/{argument['total']}  (score {argument['score']})")
    failing = [(sid, v["issue"]) for sid, v in argument["per_section"].items() if not v["ok"]]
    if failing:
        for sid, issue in failing[:10]:
            print(f"    ✗ {sid}: {issue}")
        if len(failing) > 10:
            print(f"    ... ({len(failing) - 10} more)")

    print()
    print("structural-template (preview; full audit in audit_writing.py):")
    print(f"  Citation density (≤12/1Kw):                     "
          f"{'✓' if cite_density['ok'] else '✗'}  "
          f"({cite_density['density_per_1k']}/1Kw"
          f"{', '+str(len(cite_density['over_cap_sentences']))+' sent. >3 cites' if cite_density['over_cap_sentences'] else ''})")
    if conclusion["present"]:
        print(f"  Conclusion is a re-frame (400–700 words):       "
              f"{'✓' if conclusion['ok'] else '✗'}  ({conclusion['evidence']})")
    else:
        print("  Conclusion is a re-frame (400–700 words):       "
              "✗  (no conclusion section found)")

    if args.report:
        args.report.write_text(json.dumps(findings, indent=2))
        print(f"\n  Report → {args.report}")

    # Exit code
    v5_critical = 0
    if args.strict_narrative:
        if narrative["score"] < 1.0:
            v5_critical += 1
        if argument["score"] < 0.9:
            v5_critical += 1

    if args.check:
        # Critical = anything we WOULD fix in --fix mode
        # (+ narrative/argument violations if --strict-narrative is set)
        critical = t["ai_isms"] + t["clutter"] + t["compile_fixes"] + v5_critical
        if critical > 0:
            print(f"\n❌ {critical} critical issues — run with --fix to apply"
                  + (" (narrative/argument violations require manual review)"
                     if v5_critical > 0 else ""))
            return 1
    deterministic_clean = (
        t["ai_isms"] + t["clutter"] + t["compile_fixes"] + v5_critical == 0
    )
    # Detect non-critical narrative/anchor issues (not counted as critical
    # when --strict-narrative is off) so we don't print a misleading
    # "all pass" banner when the body sections are clearly missing
    # argument anchors.
    v5_advisory = (
        narrative["score"] < 1.0 or argument["score"] < 0.9
    ) and not args.strict_narrative
    if deterministic_clean and v5_advisory:
        print("\n✅ Deterministic auto-fixes pass "
              "(narrative/anchor warnings above require manual review)")
    elif deterministic_clean:
        print("\n✅ All deterministic checks pass")
    elif args.fix:
        print("\n✅ Deterministic fixes applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
