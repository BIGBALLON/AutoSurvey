#!/usr/bin/env python3
"""audit_writing.py — writing-quality audit on the assembled .tex pass.

The Phase-3 / submission-gate complement to validate_artifacts.py
(which audits the SCHEMA layer). audit_writing.py audits the writing
itself: the 5-anchor argument skeleton, 4 narrative pillars,
thesis-coherence back-references, claim-grounding for numerical
citations, and Open-Problems 4-bucket structure.

Spec: shared-references/argument-skeleton.md, narrative-scaffolding.md,
      thesis-contract.md, claims-contract.md.

CLI:
    audit_writing.py <run_dir> [--assurance draft|polished|submission]
                               [--report PATH]

Exit codes:
    0  — all checks pass at this assurance level
    1  — critical fail (submission gate — narrative or argument < 0.9
                        OR thesis-coherence FAIL OR claim-grounding FAIL)
    2  — input error (missing required file)
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
)


# ---------------------------------------------------------------------------
# Helpers shared with prose_polish.py (kept stand-alone for tool
# independence; see prose_polish.py for the originals).
# ---------------------------------------------------------------------------

ANCHOR_ORDER = ["CLAIM", "STEELMAN", "EVIDENCE", "CONCESSION", "SO-WHAT"]
_ANCHOR_RE = re.compile(r"^\s*%\s*\[([A-Z\-]+)\]", re.MULTILINE)

OP_ANCHORS = ["PROBLEM-STATEMENT", "EXISTING-APPROACHES", "LIMITATIONS",
              "RESEARCH-DIRECTIONS"]

# Hook detection — see tools/prose_polish.py for the rationale.
# Any 2 of {year, number, metaphor} in the Intro's first paragraph.
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

_BODY_SKIP_RE = re.compile(
    r"(?:^00|^01"
    r"|abstract|introduction"
    r"|open[\s_-]*problem"
    r"|future(?:[\s_-]*direction|[\s_-]*work)?"
    r"|conclusion|trends?)",
    re.IGNORECASE,
)


def _normalise_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _read_section_files(sections_dir: Path) -> dict[str, str]:
    return {f.stem: f.read_text(encoding="utf-8")
            for f in sorted(sections_dir.glob("*.tex"))}


def _find_intro_text(sections: dict[str, str]) -> str:
    for sid, text in sections.items():
        if "intro" in sid.lower() or sid.startswith("01"):
            return text
    return ""


def _find_section_text(sections: dict[str, str], pattern: str) -> str:
    rx = re.compile(pattern, re.IGNORECASE)
    for sid, text in sections.items():
        if rx.search(sid) or rx.search(text[:200]):
            return text
    return ""


# ---------------------------------------------------------------------------
# 1. Argument-skeleton anchor scan (per body section)
# ---------------------------------------------------------------------------

def audit_argument_anchors(sections: dict[str, str]) -> dict[str, Any]:
    # A "body section" is one whose .tex file (a) is not on the skip list
    # (abstract / intro / open-problems / future / conclusion) AND (b)
    # contains an explicit `\section{...}` command. Auxiliary .tex
    # fragments that emit only \subsection (e.g. an evaluation-methodology
    # block \input by another section) are not standalone chapters and
    # should not be argument-anchor-checked in isolation; their anchors
    # belong to whichever \section file \input{}s them.
    section_cmd_re = re.compile(r"\\section\b")
    per_section: dict[str, dict] = {}
    body = [(sid, txt) for sid, txt in sections.items()
            if not _BODY_SKIP_RE.search(sid)
            and section_cmd_re.search(txt) is not None]
    for sid, text in body:
        anchors = [m.group(1) for m in _ANCHOR_RE.finditer(text)]
        canonical_seen = [a for a in anchors if a in ANCHOR_ORDER]
        if canonical_seen == ANCHOR_ORDER:
            ok, issue = True, ""
        elif set(canonical_seen) == set(ANCHOR_ORDER):
            ok, issue = False, f"anchors out of order: {canonical_seen}"
        else:
            missing = [a for a in ANCHOR_ORDER if a not in canonical_seen]
            ok, issue = False, f"missing anchors: {missing}"
        per_section[sid] = {"ok": ok, "issue": issue,
                            "anchors_found": anchors}

    passing = sum(1 for v in per_section.values() if v["ok"])
    total = len(per_section)
    score = round(passing / total, 2) if total else 1.0
    return {"per_section": per_section, "passing": passing,
            "total": total, "score": score}


# ---------------------------------------------------------------------------
# 1.5. Structural-template invariants
#      (rationale: shared-references/structural-template.md;
#       thresholds:  shared-references/benchmark-targets.json).
#
# Each invariant returns a dict {"ok": bool, "value": <observed>, "issue": str}
# so we can both gate on overall pass and surface a single human-readable line
# per invariant.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Threshold defaults.
#
# These constants are the *fallback* values audit_writing uses if
# benchmark-targets.json cannot be loaded (e.g. running the script
# from a checkout that does not include shared-references/, or in a
# unit test that imports audit_writing directly). The CLI driver calls
# `_load_audit_thresholds_from_json()` at startup and overwrites these
# module-level constants in place, so the diff panel in the dashboard
# and the audit gate read the same numbers from the same single source
# of truth.
#
# Each value's rationale lives next to it in benchmark-targets.json
# (the `_<key>_why` keys); the single-line comments below are kept to
# preserve grep-ability of the constant name → meaning mapping.
# ---------------------------------------------------------------------------

# Inline-citation density cap (per 1 K body words).
CITATION_DENSITY_CAP = 12.0

# Per-sentence cap on `\cite*{}` calls.
SENTENCE_CITATION_CAP = 3

# Annotated-bibliography ratio.
ANNOTATED_BIB_MIN_RATIO = 0.80

# Conclusion length window (re-frame, not summary).
CONCLUSION_MIN_WORDS = 400
CONCLUSION_MAX_WORDS = 700

# Top-level section count window. The benchmark sits at 8 with deep nesting.
TOP_SECTIONS_MIN = 6
TOP_SECTIONS_MAX = 12
SUBSECTIONS_MIN_PER_NESTED = 3
NESTED_SECTIONS_MIN = 4

# Auxiliary tables (every \begin{table} other than the cross-cutting
# matrix) must be load-bearing — i.e. referenced from the prose via
# \ref{...} or \autoref{...}. We do not cap their count: a survey may
# legitimately ship many tables as long as each one earns its place
# by being cited from the section text. An aux table that no sentence
# references is decoration, not load-bearing, and trips the invariant.


# Mapping from JSON key → module-level constant name. Driven both by
# _load_audit_thresholds_from_json (sets the constants) and by tests
# (verifying SSOT consistency). When you add a new threshold, add an
# entry here AND a JSON key in benchmark-targets.json.
_THRESHOLD_KEYS: tuple[tuple[str, str], ...] = (
    ("citation_density_cap",      "CITATION_DENSITY_CAP"),
    ("sentence_citation_cap",     "SENTENCE_CITATION_CAP"),
    ("annotated_bib_min_ratio",   "ANNOTATED_BIB_MIN_RATIO"),
    ("conclusion_min_words",      "CONCLUSION_MIN_WORDS"),
    ("conclusion_max_words",      "CONCLUSION_MAX_WORDS"),
    ("top_sections_min",          "TOP_SECTIONS_MIN"),
    ("top_sections_max",          "TOP_SECTIONS_MAX"),
    ("subsections_min_per_nested", "SUBSECTIONS_MIN_PER_NESTED"),
    ("nested_sections_min",       "NESTED_SECTIONS_MIN"),
)


def _benchmark_targets_path() -> Path:
    """Resolve shared-references/benchmark-targets.json next to this
    file. Lives here (not as a module-level constant) so that tests
    monkey-patching `__file__` paths still work."""
    return (Path(__file__).resolve().parent.parent
            / "skills" / "shared-references" / "benchmark-targets.json")


def _load_audit_thresholds_from_json(
    path: Path | None = None,
) -> dict[str, float | int] | None:
    """Read ``audit_thresholds`` from benchmark-targets.json and overwrite
    the matching module-level constants in place.

    Returns the parsed thresholds dict on success, ``None`` if the file
    is absent / malformed / lacks the section. Failures are silent —
    audit must still work in stripped-down installations that ship
    only the tools/ directory.
    """
    p = path or _benchmark_targets_path()
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    thresholds = doc.get("audit_thresholds")
    if not isinstance(thresholds, dict):
        return None

    mod = sys.modules[__name__]
    applied: dict[str, float | int] = {}
    for json_key, const_name in _THRESHOLD_KEYS:
        if json_key in thresholds:
            value = thresholds[json_key]
            if isinstance(value, (int, float)):
                setattr(mod, const_name, value)
                applied[json_key] = value
    return applied or None


def _strip_latex_comments(tex: str) -> str:
    """Drop trailing %-comments without eating LaTeX-escaped \\%."""
    return re.sub(r"(?<!\\)%[^\n]*", "", tex)


def _strip_float_environments(tex: str) -> str:
    """Remove table / figure float environments before the sentence-level
    citation scan.

    A cross-cutting comparison matrix (structural-template invariant 4)
    legitimately carries one \\citep{} per row and has no sentence
    punctuation, so a naïve sentence splitter would count the whole table
    body as a single sentence with many citations and falsely trip the
    per-sentence citation cap. Table/figure citations are structural, not
    prose, so they are excluded from the prose citation-density scan.
    """
    return re.sub(
        r"\\begin\{(table\*?|figure\*?|tabular)\}.*?\\end\{\1\}",
        " ",
        tex,
        flags=re.DOTALL,
    )


def _split_sentences_for_citation_audit(tex: str) -> list[str]:
    """Naïve sentence split. Good enough for citation-density auditing —
    we only need to count `\\cite*{}` per sentence, not parse semantics.
    Float environments (tables/figures) are stripped first so a citation-
    per-row comparison matrix is not mistaken for one over-cited sentence."""
    body = _strip_float_environments(_strip_latex_comments(tex))
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]


def audit_structural_template(
    sections: dict[str, str],
    bib_path: Path | None,
    outline_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    invariants: dict[str, dict[str, Any]] = {}

    # ---- Inv 2: inline-citation density ≤ CITATION_DENSITY_CAP / 1K words ----
    body_words = 0
    cite_calls = 0
    over_cap_sentences: list[str] = []
    for sec_id, tex in sections.items():
        body = _strip_latex_comments(tex)
        body_words += len(re.findall(r"\b\w+\b", body))
        for sentence in _split_sentences_for_citation_audit(tex):
            n_cites = len(re.findall(r"\\cite[a-z]*\b", sentence))
            cite_calls += n_cites
            if n_cites > SENTENCE_CITATION_CAP:
                over_cap_sentences.append(
                    f"{sec_id}: {n_cites} cites in one sentence — {sentence[:120]}"
                )
    density = (cite_calls / body_words * 1000) if body_words else 0.0
    invariants["citation_density"] = {
        "ok": density <= CITATION_DENSITY_CAP and not over_cap_sentences,
        "value": round(density, 2),
        "issue": (
            f"density {density:.1f}/1Kw exceeds cap {CITATION_DENSITY_CAP}"
            if density > CITATION_DENSITY_CAP
            else (f"{len(over_cap_sentences)} sentence(s) > "
                  f"{SENTENCE_CITATION_CAP} cites" if over_cap_sentences else "")
        ),
        "examples": over_cap_sentences[:3],
    }

    # ---- Inv 3: annotated bibliography (≥ 80%) ----
    if bib_path and bib_path.exists():
        bib_text = bib_path.read_text()
        n_entries = len(re.findall(r"^\s*@\w+\s*\{", bib_text, re.MULTILINE))
        n_annotated = len(re.findall(r"^\s*annote\s*=", bib_text, re.MULTILINE))
        ratio = (n_annotated / n_entries) if n_entries else 0.0
        invariants["annotated_bibliography"] = {
            "ok": ratio >= ANNOTATED_BIB_MIN_RATIO,
            "value": f"{n_annotated}/{n_entries} ({ratio:.0%})",
            "issue": (
                f"only {ratio:.0%} of entries annotated; need "
                f"≥ {ANNOTATED_BIB_MIN_RATIO:.0%}"
                if ratio < ANNOTATED_BIB_MIN_RATIO else ""
            ),
        }
    else:
        invariants["annotated_bibliography"] = {
            "ok": False, "value": "n/a", "issue": "references.bib missing",
        }

    # ---- Inv 4: cross-cutting matrix declared in outline; every aux
    # ----        table is load-bearing (referenced via \ref{}/\autoref{}).
    # The cross-cutting matrix is the survey-genre's load-bearing artefact
    # and must be declared by the outline. Beyond it, the project does not
    # cap how many auxiliary tables a run may ship — but each aux table
    # must earn its place by being cited from the prose. An aux table
    # with a \label{} that no sentence \ref{}'s is decoration, not
    # load-bearing, and trips this invariant.
    matrix_in_outline = False
    if outline_doc:
        # Outline-sketch should declare a `cross_cutting_matrix` slot — by
        # explicit field, OR by a section/subsection of section_type ==
        # "cross_cutting_matrix".
        if outline_doc.get("cross_cutting_matrix"):
            matrix_in_outline = True
        else:
            for sec in outline_doc.get("sections", []):
                if sec.get("section_type") == "cross_cutting_matrix":
                    matrix_in_outline = True
                    break
                for sub in sec.get("subsections", []) or []:
                    if sub.get("section_type") == "cross_cutting_matrix":
                        matrix_in_outline = True
                        break

    # Find every table label in the assembled tex, then check whether the
    # prose references it. Tables without a label are excluded from the
    # check (they cannot be referenced by definition).
    #
    # Aux tables are commonly built as a fragment .tex file that the
    # section \input{}s. We resolve one level of \input{} so labels
    # defined in the fragment are visible to the audit. The search
    # root is the paper directory (parent of references.bib).
    paper_root = bib_path.parent if bib_path else None

    def _resolve_inputs(tex: str) -> str:
        """Inline one level of \\input{...} relative to paper_root.
        Best-effort: missing files are skipped silently. Recursion is
        intentionally bounded at one level — that is enough for the
        common pattern (section \\input{}s a figure/table fragment),
        and it bounds the cost on adversarial nesting."""
        if not paper_root:
            return tex
        def _sub(m: re.Match) -> str:
            rel = m.group(1).strip()
            for candidate in (paper_root / rel, paper_root / f"{rel}.tex"):
                if candidate.is_file():
                    try:
                        return candidate.read_text(encoding="utf-8",
                                                    errors="replace")
                    except OSError:
                        return ""
            return ""
        return re.sub(r"\\input\{([^}]+)\}", _sub, tex)

    full_tex = "\n".join(_resolve_inputs(t) for t in sections.values())

    table_label_re = re.compile(
        r"\\begin\{table\*?\}.*?\\label\{(tab:[^}]+)\}.*?\\end\{table\*?\}",
        re.DOTALL,
    )
    table_labels = table_label_re.findall(full_tex)

    # The cross-cutting matrix's label conventionally starts with
    # "tab:cross-cutting" or "tab:cross_cutting" or "tab:matrix"; we treat
    # any label that contains the substring "cross" as the primary matrix
    # and exclude it from the aux-table check (it is the load-bearing
    # artefact whose presence is itself the invariant).
    aux_labels = [lbl for lbl in table_labels if "cross" not in lbl.lower()]
    unreferenced_aux: list[str] = []
    for lbl in aux_labels:
        ref_pattern = re.compile(
            r"\\(?:ref|autoref|cref|Cref)\{" + re.escape(lbl) + r"\}"
        )
        if not ref_pattern.search(full_tex):
            unreferenced_aux.append(lbl)

    invariants["cross_cutting_matrix"] = {
        "ok": matrix_in_outline and not unreferenced_aux,
        "value": (f"matrix={'yes' if matrix_in_outline else 'no'}, "
                  f"aux_tables={len(aux_labels)} "
                  f"({len(unreferenced_aux)} unreferenced)"),
        "issue": (
            "outline.json declares no cross_cutting_matrix slot"
            if not matrix_in_outline
            else (f"{len(unreferenced_aux)} aux table(s) not referenced from "
                  f"prose: {', '.join(unreferenced_aux)}"
                  if unreferenced_aux else "")
        ),
    }

    # ---- Inv 1: 6–12 top-level sections, ≥ 4 nested with ≥ 3 subsections ----
    # We count `\section{...}` commands across the assembled tex (preferred,
    # robust to multiple section-files contributing to the same logical
    # chapter through \input or to "appendix" .tex fragments that emit only
    # \subsection). If no section-file contains an explicit \section
    # command (very early drafts), fall back to one-section-per-file
    # excluding the abstract.
    section_cmd_re = re.compile(r"\\section\b")
    explicit_section_count = sum(
        len(section_cmd_re.findall(tex)) for tex in sections.values()
    )
    if explicit_section_count > 0:
        top_count = explicit_section_count
    else:
        top_count = sum(1 for sid in sections
                        if not re.search(r"abstract", sid, re.IGNORECASE))
    nested_enough = 0
    for tex in sections.values():
        if len(re.findall(r"\\subsection\b", tex)) >= SUBSECTIONS_MIN_PER_NESTED:
            nested_enough += 1
    sec_ok = (TOP_SECTIONS_MIN <= top_count <= TOP_SECTIONS_MAX
              and nested_enough >= NESTED_SECTIONS_MIN)
    invariants["section_nesting"] = {
        "ok": sec_ok,
        "value": (f"top={top_count}, "
                  f"nested(≥{SUBSECTIONS_MIN_PER_NESTED} subs)={nested_enough}"),
        "issue": (
            f"top-level count {top_count} outside "
            f"[{TOP_SECTIONS_MIN}..{TOP_SECTIONS_MAX}]"
            if not (TOP_SECTIONS_MIN <= top_count <= TOP_SECTIONS_MAX)
            else (f"only {nested_enough}/{NESTED_SECTIONS_MIN} sections "
                  f"have ≥ {SUBSECTIONS_MIN_PER_NESTED} subsections"
                  if nested_enough < NESTED_SECTIONS_MIN else "")
        ),
    }

    # ---- Inv 5: relationship-to-existing-surveys subsection ----
    # Acceptable forms (all attested in well-formed surveys):
    #   \subsection{Relationship to existing surveys}
    #   \subsection*{Relationship to Prior Surveys}
    #   \paragraph{Relationship to Existing Surveys.}
    # The benchmark survey uses the \paragraph form at the end of §1
    # (an inline block, not a separate subsection); we accept either.
    rel_ok = False
    rel_named = 0
    title_re = re.compile(
        r"\\(?:subsection\*?|paragraph)\s*\{([^}]*)\}",
    )
    next_block_re = re.compile(
        r"\\(?:subsection|paragraph|section)\b",
    )
    for tex in sections.values():
        for m in title_re.finditer(tex):
            title = m.group(1).lower()
            if (
                "relationship" in title
                and ("survey" in title or "review" in title)
            ):
                # Extract surrounding paragraph(s) — up to the next
                # \subsection / \paragraph / \section.
                start = m.end()
                tail_match = next_block_re.search(tex, start)
                end = tail_match.start() if tail_match else len(tex)
                body = tex[start:end]
                # Author-year mentions accepted in both plain-text and
                # LaTeX-typeset forms:
                #   "Smith et al., 2024"     "Smith et al. (2024)"
                #   "Smith et~al. (2024)"    "Smith and Jones (2024)"
                # The `~` is LaTeX's non-breaking space; `\s*~?\s*` lets
                # the regex see it as part of "et al" without forcing
                # writers to use plain ASCII spacing in source.
                literal_named = len(re.findall(
                    r"[A-Z][A-Za-z]+\s+et\s*~?\s*al\.?,? ?\(?[0-9]{4}\)?"
                    r"|[A-Z][A-Za-z]+\s+and\s+[A-Z][A-Za-z]+,? ?\(?[0-9]{4}\)?",
                    body,
                ))
                # A natbib \citet/\citealt renders as "Author (year)", so a
                # \citet to an adjacent survey IS a named reference. Count the
                # distinct cite keys in the block; prefer them when present so
                # writers can use clean \citet{key} instead of duplicating the
                # author-year in prose ("Zhao et al. (2024) \citep{...}").
                cite_keys: set[str] = set()
                for cm in re.finditer(r"\\cite[a-z]*\*?\{([^}]+)\}", body):
                    for k in cm.group(1).split(","):
                        if k.strip():
                            cite_keys.add(k.strip())
                rel_named = len(cite_keys) if cite_keys else literal_named
                rel_ok = rel_named >= 3
                break
        if rel_ok:
            break
    invariants["related_surveys_subsection"] = {
        "ok": rel_ok,
        "value": f"named_surveys={rel_named}" if rel_named else "missing",
        "issue": (
            "no 'Relationship to existing surveys' subsection found"
            if rel_named == 0
            else (f"only {rel_named} adjacent surveys named, need ≥ 3"
                  if rel_named < 3 else "")
        ),
    }

    # ---- Inv 6: open-problems × future-directions parallel pairing ----
    pairing_ok = False
    pairing_value = "outline missing"
    pairing_issue = "outline.json not loaded"
    if outline_doc:
        op_items: list[Any] = []
        fd_items: list[Any] = []
        for sec in outline_doc.get("sections", []):
            stype = sec.get("section_type", "")
            if stype == "open_problems":
                op_items = sec.get("items") or sec.get("subsections") or []
            elif stype in ("future_directions", "trends"):
                fd_items = sec.get("items") or sec.get("subsections") or []
        n_op, n_fd = len(op_items), len(fd_items)
        in_window = lambda n: 5 <= n <= 8  # noqa: E731
        # The benchmark survey's open-problems / future-directions lists
        # have unequal item counts (6 vs 5) — strict equality is too
        # rigid. Allow |Δ| ≤ 1 so a single dropped or merged direction
        # does not flip the invariant.
        same = abs(n_op - n_fd) <= 1
        n_paired = sum(
            1 for it in op_items
            if isinstance(it, dict) and it.get("paired_direction_id")
        )
        # Require ≥ 80% pairing rate rather than 100%. Gives the writer
        # room to leave one OP item unpaired (e.g. an 'orthogonal'
        # problem that explicitly lacks a matching direction) without
        # tripping the audit.
        pair_ratio = (n_paired / n_op) if n_op else 0.0
        paired = pair_ratio >= 0.80
        pairing_ok = in_window(n_op) and same and paired
        pairing_value = (f"open={n_op}, future={n_fd}, "
                         f"paired={n_paired}/{n_op} ({pair_ratio:.0%})")
        if not in_window(n_op):
            pairing_issue = f"open-problems count {n_op} outside [5..8]"
        elif not same:
            pairing_issue = (
                f"open ({n_op}) vs future ({n_fd}) counts differ "
                f"by more than 1"
            )
        elif not paired:
            pairing_issue = (
                f"only {n_paired}/{n_op} ({pair_ratio:.0%}) "
                f"open-problem items carry paired_direction_id; "
                f"need ≥ 80%"
            )
        else:
            pairing_issue = ""
    invariants["open_problems_pairing"] = {
        "ok": pairing_ok, "value": pairing_value, "issue": pairing_issue,
    }

    # ---- Inv 7: conclusion is a re-frame (400–700 words, not bullets) ----
    concl_text = _find_section_text(
        sections, r"conclusion|conclud[a-z]*remarks?",
    )
    if concl_text:
        body = _strip_latex_comments(concl_text)
        concl_words = len(re.findall(r"\b\w+\b", body))
        bullet_ratio = (
            len(re.findall(r"^\s*\\item\b|^\s*-\s+|^\s*\*\s+", body, re.MULTILINE))
            / max(1, concl_words / 50)  # bullets per 50 words
        )
        in_range = CONCLUSION_MIN_WORDS <= concl_words <= CONCLUSION_MAX_WORDS
        not_bulleted = bullet_ratio < 0.5
        invariants["conclusion_reframe"] = {
            "ok": in_range and not_bulleted,
            "value": f"{concl_words} words, {bullet_ratio:.2f} bullets/50w",
            "issue": (
                f"length {concl_words} outside "
                f"[{CONCLUSION_MIN_WORDS}..{CONCLUSION_MAX_WORDS}]"
                if not in_range
                else ("conclusion looks bulleted; should be a re-frame"
                      if not not_bulleted else "")
            ),
        }
    else:
        invariants["conclusion_reframe"] = {
            "ok": False, "value": "missing", "issue": "no conclusion section found",
        }

    # ---- Inv 8: each numbered contribution carries a (§N) cross-ref ----
    # The benchmark survey's contributions list reads:
    #   1. <Bold lead.> <description> (§2).
    #   2. <Bold lead.> <description> (§3).
    #   ...
    # Every item ends with `(§N)` or `(\S\,N)` or `(Section N)`,
    # making it possible for a skim-reader to jump directly from
    # contribution to the supporting section. Without this, the
    # contributions list reads as marketing rather than as a map.
    intro = ""
    for sid, tex in sections.items():
        if "intro" in sid.lower() or sid.startswith("01"):
            intro = tex
            break
    contrib_items, n_with_ref = _detect_contribution_items_with_refs(intro)
    if not contrib_items:
        invariants["contributions_section_refs"] = {
            "ok": False, "value": "no contributions list found",
            "issue": "intro has no \\begin{enumerate} or numbered "
                     "\\textbf{(N)} contributions block",
        }
    else:
        ratio = n_with_ref / len(contrib_items)
        # Need ≥ 75% of items to carry a section reference. The benchmark
        # is 4/4 = 100%; 75% leaves room for a single 'paper-organisation'
        # contribution (e.g. 'we organise the survey as follows') without
        # tripping the audit.
        invariants["contributions_section_refs"] = {
            "ok": ratio >= 0.75,
            "value": f"{n_with_ref}/{len(contrib_items)} items have (§N) "
                     f"cross-ref ({ratio:.0%})",
            "issue": (
                f"only {n_with_ref}/{len(contrib_items)} contributions "
                f"carry a (§N) cross-ref; need ≥ 75%"
                if ratio < 0.75 else ""
            ),
        }

    n_ok = sum(1 for inv in invariants.values() if inv["ok"])
    score = round(n_ok / len(invariants), 2) if invariants else 1.0
    return {"invariants": invariants, "passing": n_ok,
            "total": len(invariants), "score": score}


# Section-cross-ref patterns accepted inside contributions. Forms seen
# in the wild:
#   (§2)            — natural Unicode (most common in benchmark)
#   (\S\,2)         — LaTeX section sign + thin-space
#   (Section 2)     — spelled-out form
#   (Sec.\ 6)       — abbreviated + LaTeX inter-word space
#   (sec. 6)        — lowercased abbreviated
# The `[\s\\,]*` after the marker swallows any combination of
# whitespace, backslashes (for \\,, \\ , \\:), and commas.
_SECTION_REF_RE = re.compile(
    r"\("
    r"(?:§|\\S|Section|Sec\.?|sec\.?)"
    r"[\s\\,]*"
    r"\d+(?:\.\d+)?"
    r"\)",
    re.IGNORECASE,
)


def _detect_contribution_items_with_refs(intro: str) -> tuple[list[str], int]:
    """Return ``(items, n_with_section_ref)``.

    Splits the contributions enumeration (either \\enumerate or inline
    \\textbf{(N)}…) into per-item text, then counts how many carry a
    (§N) / (Section N) / (\\S\\,N) cross-reference.
    """
    items: list[str] = []

    # Style 1 — \begin{enumerate} … \end{enumerate}
    m = re.search(
        r"\\begin\{enumerate\}(.*?)\\end\{enumerate\}",
        intro, re.DOTALL,
    )
    if m:
        body = m.group(1)
        # Split on \item, drop the empty leading chunk.
        chunks = re.split(r"\\item\b", body)
        items = [c.strip() for c in chunks[1:] if c.strip()]
    else:
        # Style 2 — inline \textbf{(N)} markers
        markers = list(_INLINE_CONTRIB_MARKER_RE.finditer(intro))
        if len(markers) >= 4:
            for i, mk in enumerate(markers):
                start = mk.end()
                end = (markers[i + 1].start()
                       if i + 1 < len(markers) else len(intro))
                items.append(intro[start:end].strip())

    n_with_ref = sum(1 for it in items if _SECTION_REF_RE.search(it))
    return items, n_with_ref


# ---------------------------------------------------------------------------
# 2. Open-Problems 4-bucket scan
# ---------------------------------------------------------------------------

def audit_open_problems(sections: dict[str, str]) -> dict[str, Any]:
    op_text = _find_section_text(
        sections, r"open[\s_-]*problem|challenges?")
    if not op_text:
        return {"present": False, "subsections_total": 0, "subsections_passing": 0,
                "score": 1.0, "issues": ["no Open Problems section detected"]}

    # Split into \subsection chunks
    chunks = re.split(r"(?=\\subsection\b)", op_text)[1:]   # drop preamble
    if not chunks:
        return {"present": True, "subsections_total": 0, "subsections_passing": 0,
                "score": 0.0,
                "issues": ["Open Problems section has no \\subsection blocks; "
                           "narrative discipline requires per-subsection "
                           "4-bucket structure"]}

    passing = 0
    issues: list[str] = []
    for i, chunk in enumerate(chunks):
        anchors = [m.group(1) for m in _ANCHOR_RE.finditer(chunk)]
        present = set(anchors)
        missing = [a for a in OP_ANCHORS if a not in present]
        if not missing:
            passing += 1
        else:
            sub_title_match = re.match(r"\\subsection\s*\{([^}]+)\}", chunk)
            sub_title = sub_title_match.group(1) if sub_title_match else f"<sub_{i}>"
            issues.append(f"Open Problems subsection {sub_title!r}: "
                          f"missing {missing}")
    total = len(chunks)
    score = round(passing / total, 2) if total else 1.0
    return {"present": True, "subsections_total": total,
            "subsections_passing": passing, "score": score,
            "issues": issues}


# ---------------------------------------------------------------------------
# 3. Narrative pillars (Hook + Why-Now + Relationship + Contributions)
# ---------------------------------------------------------------------------


def audit_narrative_pillars(sections: dict[str, str]) -> dict[str, Any]:
    intro = _find_intro_text(sections)
    intro_first = ""
    # Match the full \section{Title} so the rest-of-text doesn't carry the
    # closing 'Title}' as the (false) first paragraph; and accept any chunk
    # that contains real prose after stripping leading structural commands
    # (\label, \subsection, \textbf, \emph) — pre-fix, virtually every
    # well-typeset Intro paragraph was rejected by the naive '\\\\'-leading test.
    m = re.search(r"\\section\*?\{[^}]*\}", intro)
    if m:
        rest = intro[m.end():]
        for chunk in re.split(r"\n\s*\n", rest):
            prose = _strip_latex_comments(chunk).strip()
            if not prose:
                continue
            body = _strip_leading_latex_commands(prose)
            if body:
                intro_first = prose
                break

    # Any 2 of 3 hook signals in the first Intro paragraph.
    hook_signals = sum([
        bool(_HOOK_YEAR_RE.search(intro_first)),
        bool(_HOOK_NUMBER_RE.search(intro_first)),
        bool(_HOOK_METAPHOR_RE.search(intro_first)),
    ])
    hook = hook_signals >= 2
    why_now = bool(re.search(
        r"\\subsection\*?\s*\{\s*(Why Now\??|The Inflection Point|Why this survey now)\s*\}",
        intro, re.IGNORECASE))
    relationship = bool(re.search(
        r"\\subsection\*?\s*\{\s*(Relationship to (Existing|Prior) Surveys|Differences from Existing Surveys)\s*\}",
        intro, re.IGNORECASE))
    contributions, contrib_count = _detect_numbered_contributions(intro)

    pillars = {"hook": hook, "why_now": why_now,
               "relationship": relationship, "contributions": contributions}
    score = round(sum(pillars.values()) / 4, 2)
    return {"pillars": pillars, "contrib_count": contrib_count, "score": score}


# In-prose numbered contributions: many real papers number their
# contributions inline using bold (1)/(2)/(3) markers instead of a
# \begin{enumerate} block. spec didn't say which style to use, only
# that there must be ≥4 numbered items. The detector should accept both.
_INLINE_CONTRIB_MARKER_RE = re.compile(
    r"\\textbf\s*\{\s*\(\s*(\d{1,2})\s*\)"
)


def _detect_numbered_contributions(intro_text: str) -> tuple[bool, int]:
    """Return (passes, count) for the 'numbered contributions ≥4' pillar.

    Two acceptable styles:
      1. \\begin{enumerate} ... ≥4 \\item ... \\end{enumerate}
      2. ≥4 inline \\textbf{(1)} … \\textbf{(2)} … markers, with the
         numeric labels strictly increasing from 1 with no gaps.
    The second pattern is what the llm-pretraining run uses — and what
    many ICLR/NeurIPS-style intros use to avoid the visual heaviness of
    an enumerate block.
    """
    # Style 1 — \begin{enumerate} block
    for em in re.finditer(r"\\begin\{enumerate\}(.*?)\\end\{enumerate\}",
                          intro_text, re.DOTALL):
        items = re.findall(r"\\item\b", em.group(1))
        if len(items) >= 4:
            return True, len(items)

    # Style 2 — inline \textbf{(N)} markers in strict 1, 2, 3, … sequence
    nums = [int(m.group(1))
            for m in _INLINE_CONTRIB_MARKER_RE.finditer(intro_text)]
    if len(nums) >= 4:
        # Find longest prefix that matches 1, 2, 3, ...
        run = 0
        for n in nums:
            if n == run + 1:
                run += 1
            else:
                break
        if run >= 4:
            return True, run

    return False, 0


# ---------------------------------------------------------------------------
# 4. Thesis coherence
# ---------------------------------------------------------------------------

def audit_thesis_coherence(
    sections: dict[str, str],
    thesis_doc: dict[str, Any] | None,
    outline_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    if thesis_doc is None or outline_doc is None:
        return {"applicable": False, "score": 1.0, "issues": []}

    issues: list[str] = []
    thesis_text = thesis_doc.get("thesis", "")
    if not thesis_text:
        return {"applicable": False, "score": 0.0,
                "issues": ["thesis.json has no thesis text"]}

    # (a) abstract + conclusion mention thesis
    abstract = sections.get("00_abstract") or _find_section_text(
        sections, r"abstract")
    conclusion = _find_section_text(
        sections, r"conclusion")
    thesis_words = set(_normalise_ws(thesis_text).split())
    significant = {w for w in thesis_words
                   if len(w) >= 5 and w not in {"which", "their", "these"}}
    abstract_overlap = len(significant & set(_normalise_ws(abstract).split()))
    conclusion_overlap = len(significant & set(_normalise_ws(conclusion).split()))
    if abstract_overlap < 3:
        issues.append("abstract does not appear to restate the thesis "
                      f"(only {abstract_overlap} significant overlap words)")
    if conclusion_overlap < 3:
        issues.append("conclusion does not appear to restate the thesis "
                      f"(only {conclusion_overlap} significant overlap words)")

    # (b) every argument_step is referenced by ≥1 outline section
    step_ids = {s.get("step_id")
                for s in (thesis_doc.get("argument_steps") or [])
                if s.get("step_id")}
    referenced: set[str] = set()
    for sec in (outline_doc.get("sections") or []):
        ref = sec.get("argues_for_thesis_step")
        if ref:
            referenced.add(ref)
    uncovered = step_ids - referenced
    if uncovered:
        issues.append(f"argument_step coverage gap: steps {sorted(uncovered)} "
                      f"have no section binding")

    score = 1.0 if not issues else round(max(0.0, 1.0 - 0.3 * len(issues)), 2)
    return {"applicable": True, "score": score, "issues": issues,
            "abstract_overlap_words": abstract_overlap,
            "conclusion_overlap_words": conclusion_overlap,
            "argument_step_coverage": (
                f"{len(referenced)}/{len(step_ids)}" if step_ids else "n/a")}


# ---------------------------------------------------------------------------
# 5. Claim-grounding (abstract ∪ atomic_claims.quote)
# ---------------------------------------------------------------------------

# Numeric tokens in cite-bearing sentences
#
# Quantitative-claim shapes we want to match:
#   * bare numbers — "70", "1.4", "0.31"
#   * percentages — "65%"
#   * multiplicative — "90×", "10x"
#   * scale suffixes — "15B", "175M", "1.5K", "15T" (trillion), "100GB", "2TB"
#   * FLOP units — "100FLOPs", "6FLOP"
#
# We keep the suffix list closed (no scientific-notation "6e23" yet, no
# "billion"/"trillion" English words) — false-negatives are safer than
# false-positives for an audit that gates submissions.
_NUMERIC_TOKEN_RE = re.compile(
    r"\b\d+(?:\.\d+)?(?:%|×|x|B|M|K|T|GB|TB|FLOPs?)?\b"
)
_CITE_RE = re.compile(r"\\cite[tp]?\*?\{([^}]+)\}")
_INSIGHT_RE = re.compile(r"^\s*%\s*\[INSIGHT\]", re.MULTILINE)

# Patterns that look numeric but are NOT quantitative claims:
#   * enumerator markers   — "(1)", "(2.5)", "(iv)"
#   * model-name fragments — "Llama-3", "GPT-4-8B", "Qwen2.5"
#   * version codes        — "V2", "V3", standalone "B100" etc.
#   * 4-digit years        — "2024", "by 2025", "between 2020 and 2025"
#   * name-then-version    — "OLMo 2", "GPT 4", "Llama 3" (capital-led
#                            word followed by a small integer used as a
#                            release ordinal, NOT a quantitative claim)
# We strip these BEFORE checking _NUMERIC_TOKEN_RE so that headings like
# "(1) Tokens-per-parameter has roughly tripled" or release lists like
# "Qwen3, DeepSeek-V3, Skywork-MoE" don't trigger spurious "ungrounded
# numeric claim" warnings. Real quantitative claims ("15T tokens",
# "65% accuracy", "90× Chinchilla-optimal") are unaffected.
_ENUM_MARKER_RE = re.compile(r"\([0-9ivxIVX]+\)\s*")
_MODEL_FRAGMENT_RE = re.compile(r"-[A-Za-z]*\d+(?:\.\d+)?[A-Za-z]*")
_VERSION_CODE_RE = re.compile(r"\b[A-Z]\d+(?:\.\d+)?\b")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_NAME_VERSION_RE = re.compile(r"\b[A-Z][A-Za-z]{1,}\s+\d{1,2}(?!\d)")


def _has_quantitative_numeric(sentence: str) -> bool:
    """True iff the sentence contains a numeric token that survives
    after stripping non-quantitative artefacts (enumerators, model
    name fragments, version codes, years, name-then-ordinal pairs).
    Used to decide whether a cite-bearing sentence makes a claim that
    *needs* grounding."""
    cleaned = _ENUM_MARKER_RE.sub("", sentence)
    cleaned = _MODEL_FRAGMENT_RE.sub("", cleaned)
    cleaned = _VERSION_CODE_RE.sub("", cleaned)
    cleaned = _YEAR_RE.sub("", cleaned)
    cleaned = _NAME_VERSION_RE.sub("", cleaned)
    return bool(_NUMERIC_TOKEN_RE.search(cleaned))


def audit_insight_anchors(
    sections: dict[str, str],
    thesis_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    """Verify that every thesis.non_obvious_findings entry is backed by
    a ``% [INSIGHT]`` LaTeX comment in the named section.

    A 'non-obvious finding' is the survey's contrarian-but-defensible
    insight (the equivalent of 'the bottleneck is knowledge accrual,
    not model capability' in the L1-L5 benchmark). The thesis declares
    them, the writing must mark them. This audit closes the loop.

    The check is OPT-IN: if thesis has no non_obvious_findings field
    (or it is empty), this audit returns score=1.0 and is silently
    not_applicable.
    """
    if not thesis_doc:
        return {"applicable": False, "score": 1.0,
                "findings_total": 0, "findings_anchored": 0, "issues": []}
    findings = thesis_doc.get("non_obvious_findings") or []
    if not findings:
        return {"applicable": False, "score": 1.0,
                "findings_total": 0, "findings_anchored": 0, "issues": []}

    issues: list[str] = []
    anchored = 0
    for i, entry in enumerate(findings):
        if not isinstance(entry, dict):
            issues.append(f"non_obvious_findings[{i}] is not an object")
            continue
        finding = entry.get("finding")
        sid = entry.get("section_id")
        if not (isinstance(finding, str) and finding.strip()):
            issues.append(f"non_obvious_findings[{i}].finding missing or empty")
            continue
        if not (isinstance(sid, str) and sid.strip()):
            issues.append(
                f"non_obvious_findings[{i}].section_id missing — "
                "cannot verify anchor placement"
            )
            continue

        # Match section_id loosely: 'fname.tex stem starts with section_id'.
        # E.g. section_id='02_scaling' matches '02_scaling.tex'.
        candidates = [text for fname, text in sections.items()
                      if fname == sid or fname.startswith(f"{sid}_")
                      or fname.startswith(f"{sid}.")]
        if not candidates:
            issues.append(
                f"non_obvious_findings[{i}]: section_id={sid!r} not found "
                f"among {sorted(sections.keys())}"
            )
            continue
        if any(_INSIGHT_RE.search(t) for t in candidates):
            anchored += 1
        else:
            issues.append(
                f"non_obvious_findings[{i}]: section {sid!r} has no "
                f"% [INSIGHT] anchor for finding {finding[:60]!r}"
            )

    score = round(anchored / len(findings), 2) if findings else 1.0
    return {"applicable": True, "score": score,
            "findings_total": len(findings), "findings_anchored": anchored,
            "issues": issues}


# Sectioning / structural commands that should NEVER be glued onto the
# beginning of a prose sentence. Previously, '\section{Foo}\n\label{...}\n\n
# We close with four open problems...' was returned by _extract_sentences as
# ONE sentence — because the regex split only on '.!?' and these commands
# carry no such terminator. The downstream grounding audit then saw
# '\section{Open Problems}\n\label{sec:open}\n\nWe close with four ...' as
# a single 'sentence containing \citep' (it carried the inner real one).
_STRUCTURAL_CMD_RE = re.compile(
    r"\\(?:section|subsection|subsubsection|paragraph|subparagraph|"
    r"chapter|part|label|input|include|includegraphics)\*?"
    r"(?:\[[^\]]*\])?(?:\{[^}]*\})?\s*"
)


def _extract_sentences(text: str) -> list[str]:
    text = _strip_latex_comments(text)
    # Replace structural commands with a sentence-terminator so the regex
    # split can treat them as paragraph boundaries. Otherwise a section
    # header that lacks a final '.' silently merges into the next sentence.
    text = _STRUCTURAL_CMD_RE.sub(". ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


# ---------------------------------------------------------------------------
# Grounding-overlap helpers
#
# A naive `set(sentence.split()) & set(abstract.split())` with a ≥6 token
# threshold scores far too low on real surveys. Empirically (e.g. the
# llm-pretraining run we use as our regression fixture) it bottoms out
# around 0.37, because:
#
#   * \citep{...}, \textbf{...}, $\sim$, \\ etc. were left in the token set —
#     so '\\citep{team2024llama}' became one indivisible token that never
#     matched the plain-text abstract.
#   * stop-words inflated the threshold pressure ('the', 'and', 'is' counted
#     toward 6).
#   * scale-suffix numbers ('15T tokens' in the body, '15.6t tokens' in the
#     abstract) compared as strings and never matched even though they refer
#     to the same quantity.
#
# The new algorithm: tokenise after stripping LaTeX commands, drop stop-words
# and short tokens, AND additionally count fuzzy numeric matches as a
# strong-signal anchor (15T ≈ 15.6t within 30% AND same unit suffix). A
# sentence counts as grounded if any cited paper supports any of:
#
#   * ≥4 shared content tokens, OR
#   * ≥2 fuzzy numeric matches, OR
#   * ≥1 fuzzy numeric match AND ≥2 shared content tokens.
#
# Verified against llm-pretraining-20260529-113000 — grounding 0.37 → 0.57
# with no false positives flagged in spot checks.
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset((
    "a an the and or but if then in on at to for of with by from is are "
    "was were be been being have has had do does did this that these "
    "those it its as we our which who whom what whether than into onto "
    "over under between among per via vs versus also however therefore "
    "thus moreover while although when not no any all most some many "
    "much such other another both either neither each every"
).split())

_GROUND_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?(?:\{[^}]*\})?")
_GROUND_NUM_RE = re.compile(r"\d+(?:\.\d+)?[a-zA-Z]*")


def _ground_content_tokens(text: str) -> set[str]:
    """Tokenise to content words: strip LaTeX commands first, lower-case,
    keep word characters + '%' and '×', drop stop-words and tokens shorter
    than 3 characters."""
    cleaned = _GROUND_LATEX_CMD_RE.sub(" ", text)
    cleaned = re.sub(r"[^\w%×]+", " ", cleaned.lower())
    return {t for t in cleaned.split()
            if t and t not in _STOP_WORDS and len(t) > 2}


def _ground_numeric_tokens(text: str) -> set[str]:
    """Numeric-with-optional-unit tokens (lower-cased)."""
    cleaned = _GROUND_LATEX_CMD_RE.sub(" ", text)
    return {m.group(0).lower() for m in _GROUND_NUM_RE.finditer(cleaned)}


def _ground_numeric_fuzzy_overlap(s_nums: set[str], a_nums: set[str]) -> int:
    """Count pairs where a sentence-side number is 'close enough' to an
    abstract-side number to be the same physical quantity:
      * same unit suffix (e.g. both 't', both 'b', both '%'), AND
      * within 30% of each other relative to the larger value, OR
      * unit-less and exactly equal AND ≥5 (avoids '1', '2', '3' noise).
    """
    matches = 0
    for sn in s_nums:
        ms = re.match(r"(\d+(?:\.\d+)?)([a-z]*)", sn)
        if not ms:
            continue
        v1, u1 = float(ms.group(1)), ms.group(2)
        for an in a_nums:
            ma = re.match(r"(\d+(?:\.\d+)?)([a-z]*)", an)
            if not ma:
                continue
            v2, u2 = float(ma.group(1)), ma.group(2)
            if u1 and u1 == u2 and v1 > 0 and abs(v1 - v2) / max(v1, v2) <= 0.3:
                matches += 1
                break
            if not u1 and not u2 and v1 == v2 and v1 >= 5:
                matches += 1
                break
    return matches


def _is_grounded(
    sentence: str,
    cite_keys: list[str],
    abstracts: dict[str, str],
    quote_pool_by_key: dict[str, str],
) -> bool:
    """Apply the new grounding rule: any cited paper whose
    abstract+claim-quotes pool satisfies ≥4 word overlap, ≥2 numeric
    fuzzy matches, or 1 numeric + 2 word overlap counts as grounding."""
    s_words = _ground_content_tokens(sentence)
    s_nums = _ground_numeric_tokens(sentence)
    for k in cite_keys:
        pool_text = (abstracts.get(k, "") + " " + quote_pool_by_key.get(k, "")).strip()
        if not pool_text:
            continue
        a_words = _ground_content_tokens(pool_text)
        a_nums = _ground_numeric_tokens(pool_text)
        word_overlap = len(s_words & a_words)
        num_match = _ground_numeric_fuzzy_overlap(s_nums, a_nums)
        if word_overlap >= 4 or num_match >= 2 or (
                word_overlap >= 2 and num_match >= 1):
            return True
    return False


def _load_card_text(cards_dir: Path) -> dict[str, str]:
    """Read every cite_key.md file in a 1_search/cards/ directory and return
    a flat 'card body' string per cite_key. Used as a fallback grounding
    source when filtered.jsonl has empty abstracts.

    The card markdown carries the paper's key insights — design rationale,
    SOTA claims, lessons learnt — and is exactly what a 'Quote' field
    distils. Pre-fix, audit_claim_grounding ignored these files entirely.
    """
    if not cards_dir.exists():
        return {}
    out: dict[str, str] = {}
    for p in cards_dir.glob("*.md"):
        cite_key = p.stem
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Strip Markdown bullets / formatting so the token-set comparator
        # in _is_grounded sees plain prose.
        text = re.sub(r"`([^`]+)`", r"\1", text)         # `code` → code
        text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.M)  # bullets
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)    # **bold**
        out[cite_key] = _normalise_ws(text)
    return out


def audit_claim_grounding(
    sections: dict[str, str],
    filtered: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    cards: dict[str, str] | None = None,
) -> dict[str, Any]:
    abstracts: dict[str, str] = {}
    for p in filtered:
        key = p.get("cite_key") or p.get("paper_id")
        if key:
            abstracts[key] = _normalise_ws(p.get("abstract") or "")

    # Cards (1_search/cards/*.md) are an *additional* grounding pool,
    # appended onto whatever the filtered.jsonl abstract carries (which
    # may be empty for many cite_keys in real runs).
    if cards:
        for key, body in cards.items():
            if not body:
                continue
            existing = abstracts.get(key, "")
            abstracts[key] = (existing + " " + body).strip() if existing else body

    quote_pool_by_key: dict[str, str] = {}
    for rec in claims:
        key = rec.get("cite_key")
        if not key:
            continue
        chunks: list[str] = []
        for ac in (rec.get("atomic_claims") or []):
            q = (ac or {}).get("quote") or ""
            if q:
                chunks.append(_normalise_ws(q))
        if chunks:
            quote_pool_by_key[key] = " ".join(chunks)

    n_total = 0
    n_grounded = 0
    ungrounded_examples: list[str] = []

    for sid, text in sections.items():
        if "abstract" in sid.lower() or sid.startswith("00"):
            continue
        for sentence in _extract_sentences(text):
            cite_keys: list[str] = []
            for cm in _CITE_RE.finditer(sentence):
                for k in cm.group(1).split(","):
                    cite_keys.append(k.strip())
            if not cite_keys:
                continue
            if not _has_quantitative_numeric(sentence):
                continue
            n_total += 1

            if _is_grounded(sentence, cite_keys, abstracts, quote_pool_by_key):
                n_grounded += 1
            elif len(ungrounded_examples) < 5:
                ungrounded_examples.append(
                    f"[{sid}] {sentence[:180]}{'...' if len(sentence) > 180 else ''}"
                )

    score = round(n_grounded / n_total, 2) if n_total else 1.0
    return {"numeric_cited_sentences": n_total,
            "grounded": n_grounded,
            "ungrounded_examples": ungrounded_examples,
            "score": score}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--assurance",
                   choices=["draft", "polished", "submission"],
                   default="submission",
                   help="Default is the strictest level (`submission`): low "
                        "scores fail with exit 1. `polished` and `draft` are "
                        "kept for tooling that needs to inspect borderline "
                        "drafts without failing them.")
    p.add_argument("--no-strict-template", action="store_true",
                   help="Skip the structural_template gate at submission "
                        "level. Used by the in-tree fixture tests where the "
                        "minimal 4-section paper cannot satisfy the 8 "
                        "structural invariants by construction. Real "
                        "/survey-run invocations MUST NOT pass this flag.")
    p.add_argument("--report", type=Path)
    p.add_argument("--benchmark-targets", type=Path, default=None,
                   help="Path to benchmark-targets.json. If omitted, uses "
                        "shared-references/benchmark-targets.json from this "
                        "repo. Use to override thresholds for a specific "
                        "venue (e.g. a 25-page workshop paper that should "
                        "permit a tighter conclusion window).")
    args = p.parse_args(argv)

    # Calibrate thresholds from the single source of truth.
    _load_audit_thresholds_from_json(args.benchmark_targets)

    run_dir: Path = args.run_dir.expanduser().resolve()
    sections_dir = run_dir / "5_paper" / "sections"
    if not sections_dir.exists():
        print(f"ERROR: sections dir not found: {sections_dir}", file=sys.stderr)
        return 2

    sections = _read_section_files(sections_dir)
    if not sections:
        print(f"ERROR: no .tex sections in {sections_dir}", file=sys.stderr)
        return 2

    thesis_doc = (json.loads((run_dir / "2_thesis" / "thesis.json").read_text())
                  if (run_dir / "2_thesis" / "thesis.json").exists() else None)
    outline_doc = (json.loads((run_dir / "4_outline" / "outline.json").read_text())
                   if (run_dir / "4_outline" / "outline.json").exists() else None)
    filtered = []
    f_path = run_dir / "1_search" / "filtered.jsonl"
    if f_path.exists():
        filtered = [json.loads(l) for l in f_path.read_text().splitlines() if l.strip()]
    claims = []
    c_path = run_dir / "1_search" / "claims_cache.jsonl"
    if c_path.exists():
        claims = [json.loads(l) for l in c_path.read_text().splitlines() if l.strip()]

    # Per-paper card markdown carries design-rationale / SOTA / lessons
    # that the filtered.jsonl abstract often lacks. Folded into the
    # grounding pool.
    cards = _load_card_text(run_dir / "1_search" / "cards")

    findings: dict[str, Any] = {
        "argument_anchors":   audit_argument_anchors(sections),
        "structural_template": audit_structural_template(
            sections,
            run_dir / "5_paper" / "references.bib",
            outline_doc,
        ),
        "open_problems":      audit_open_problems(sections),
        "narrative_pillars":  audit_narrative_pillars(sections),
        "thesis_coherence":   audit_thesis_coherence(sections, thesis_doc, outline_doc),
        "insight_anchors":    audit_insight_anchors(sections, thesis_doc),
        "claim_grounding":    audit_claim_grounding(sections, filtered, claims, cards),
    }

    print("=" * 60)
    print(f"audit_writing — assurance: {args.assurance}")
    print("=" * 60)
    for area, info in findings.items():
        score = info.get("score", "n/a")
        print(f"  {area:<22s} score = {score}")
        # show a short summary
        if area == "argument_anchors":
            print(f"    body sections passing all 5 anchors: "
                  f"{info['passing']}/{info['total']}")
        elif area == "structural_template":
            print(f"    invariants passing: "
                  f"{info['passing']}/{info['total']}")
            for inv_name, inv in info["invariants"].items():
                mark = "✓" if inv["ok"] else "✗"
                line = f"    {mark} {inv_name:<28s} {inv['value']}"
                if inv["issue"]:
                    line += f" — {inv['issue']}"
                print(line)
        elif area == "open_problems":
            if not info.get("present"):
                print("    no Open Problems section detected (skipping check)")
            else:
                print(f"    subsections passing 4-bucket: "
                      f"{info['subsections_passing']}/{info['subsections_total']}")
        elif area == "narrative_pillars":
            for pn, ok in info["pillars"].items():
                print(f"    {pn:<14s} {'✓' if ok else '✗'}")
        elif area == "thesis_coherence":
            if not info["applicable"]:
                print("    not applicable (thesis or outline missing)")
            else:
                for issue in info["issues"][:5]:
                    print(f"    - {issue}")
        elif area == "insight_anchors":
            if not info["applicable"]:
                print("    not applicable (thesis has no non_obvious_findings)")
            else:
                print(f"    findings anchored: "
                      f"{info['findings_anchored']}/{info['findings_total']}")
                for issue in info["issues"][:3]:
                    print(f"    - {issue}")
        elif area == "claim_grounding":
            print(f"    numeric+cite sentences: {info['numeric_cited_sentences']}; "
                  f"grounded: {info['grounded']}")
            for ex in info["ungrounded_examples"][:3]:
                print(f"    - ungrounded: {ex}")

    if args.report:
        args.report.write_text(json.dumps(findings, indent=2))
        print(f"\nReport → {args.report}")

    # Submission gate (per shared-references/argument-skeleton.md,
    # narrative-scaffolding.md, structural-template.md).
    #
    # The structural_template gate codifies the benchmark-derived
    # 8 invariants. Threshold 0.85 ≡ "≥ 7 of 8 invariants must hold";
    # one slot of slack so a single missed invariant (e.g. only 4 of
    # 5 contributions cross-ref Section N) does not block submission
    # outright while still surfacing in the audit log.
    if args.assurance == "submission":
        gates = {
            "argument_anchors":  findings["argument_anchors"]["score"] >= 0.9,
            "narrative_pillars": findings["narrative_pillars"]["score"] >= 0.9,
            "thesis_coherence":  not findings["thesis_coherence"]["applicable"]
                                  or findings["thesis_coherence"]["score"] >= 0.9,
            "open_problems":     (not findings["open_problems"]["present"]
                                  or findings["open_problems"]["score"] >= 0.9),
            "insight_anchors":   not findings["insight_anchors"]["applicable"]
                                  or findings["insight_anchors"]["score"] >= 1.0,
            "claim_grounding":   findings["claim_grounding"]["score"] >= 0.5,
        }
        if not args.no_strict_template:
            gates["structural_template"] = (
                findings["structural_template"]["score"] >= 0.85
            )
        failing = [k for k, ok in gates.items() if not ok]
        if failing:
            print(f"\n❌ Submission gate FAIL: {failing}")
            return 1
        print("\n✅ Submission gate PASS")
    else:
        print(f"\n(assurance={args.assurance}: gates not enforced)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
