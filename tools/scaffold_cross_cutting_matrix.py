#!/usr/bin/env python3
"""scaffold_cross_cutting_matrix.py — emit the survey's single cross-cutting
matrix as a fillable LaTeX skeleton.

Closes the loop on structural-template invariant 4: outline.json declares
*one* ``cross_cutting_matrix`` slot (with ``col_labels`` and an optional
``row_label``); this tool reads that slot together with
``1_search/cards.jsonl`` (or, as fallback, ``filtered.jsonl``) and emits a
``5_paper/sections/<slot_id>.tex`` containing a populated
``\\begin{table*} … \\end{table*}`` block.

Cells are filled deterministically when the card's structured fields cover
the column; unknown cells are written as ``\\textit{?}`` so the writer can
see at a glance which (system, dimension) pairs still need editorial
attention. Every row carries a ``\\citep{<cite_key>}`` so the closed-set
verification step (verify_papers / phantom-key audit) can validate the
matrix in one pass.

Spec: shared-references/structural-template.md (invariant 4),
      shared-references/reference-assets/cross_cutting_matrix.example.tex.

CLI:
    scaffold_cross_cutting_matrix.py <run_dir>
        [--output PATH]            # defaults to 5_paper/sections/<slot_id>.tex
        [--max-rows N]             # cap row count (defaults to expected_rows)
        [--dry-run]                # print to stdout instead of writing

Exit codes:
    0  — matrix tex emitted (or printed under --dry-run)
    1  — outline.json declares no cross_cutting_matrix slot
    2  — required input file missing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# Heuristic field map from card-side dotted paths to the column labels we
# expect to see in outline.cross_cutting_matrix.col_labels. Keys are the
# *normalised* column label (lower-cased, non-alnum stripped); values are
# a list of dotted card paths to try in order — first non-empty wins.
#
# This is intentionally heuristic-only: it is correct for the field names
# AutoSurvey's extract_paper_card.py emits today, and degrades to "?" for
# columns it doesn't know how to fill. The writer agent is expected to
# tighten any "?" cells before the survey ships; the scaffolder just
# guarantees the *layout* is benchmark-shaped from the start.
_COLUMN_FIELD_MAP: dict[str, list[str]] = {
    "architecture":      ["architecture.attention_type",
                           "architecture.moe_config.num_experts",
                           "kind"],
    "attention":         ["architecture.attention_type"],
    "moe":               ["architecture.moe_config.num_experts"],
    "totalparams":       ["scale.total_params", "total_params"],
    "params":            ["scale.total_params", "total_params"],
    "activeparams":      ["scale.active_params"],
    "trainingtokens":    ["scale.training_tokens",
                           "scale.training_tokens_per_stage",
                           "training_tokens"],
    "tokens":            ["scale.training_tokens",
                           "scale.training_tokens_per_stage",
                           "training_tokens"],
    "compute":           ["scale.total_compute_flops", "total_flops"],
    "flops":             ["scale.total_compute_flops", "total_flops"],
    "data":              ["data.curation", "data.sources"],
    "objective":         ["recipe.objective", "recipe.pretraining_objective"],
    "objectives":        ["recipe.objective", "recipe.pretraining_objective"],
    "year":              ["year"],
    "opensource":        ["recipe.open_source", "open_source"],
    "os":                ["recipe.open_source", "open_source"],
    "domain":            ["kind"],
    # Generic comparison columns that surveys reuse across topics. These read
    # the flat, free-text dimension fields the writer puts on enriched cards
    # (mechanism / routing / balancing / failure mode …), so the matrix
    # auto-fills for any topic that names its columns conventionally rather
    # than only for the rich extract_paper_card.py schema above.
    "mechanism":         ["mechanism", "routing", "method"],
    "routing":           ["routing", "mechanism"],
    "routingstrategy":   ["routing", "mechanism"],
    "balancing":         ["balancing", "load_balancing", "loadbalancing"],
    "loadbalancing":     ["balancing", "load_balancing"],
    "granularity":       ["granularity", "experts_total_active"],
    "experts":           ["experts_total_active", "granularity",
                          "architecture.moe_config.num_experts"],
    "expertstotalactive":["experts_total_active", "granularity"],
    "precision":         ["precision", "dtype"],
    "topk":              ["top_k", "topk"],
    "qualitysignal":     ["quality_signal", "quality"],
    "quality":           ["quality_signal", "quality"],
    "failuremode":       ["failure_mode", "key_limitation", "failure_modes"],
    "primaryfailuremode":["failure_mode", "key_limitation", "failure_modes"],
    "failuremodes":      ["failure_mode", "key_limitation", "failure_modes"],
    "limitation":        ["key_limitation", "failure_mode"],
    "keyidea":           ["key_idea", "key_insight"],
    "reach":             ["reach", "max_context", "claimed_window"],
    "adaptation":        ["adaptation", "adaptation_cost", "training_cost"],
}


def _normalise_col(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", label.lower())


def _walk(obj: Any, dotted: str) -> Any:
    """Walk ``obj`` along a dotted path. Returns the leaf or None."""
    cur: Any = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _format_cell(value: Any) -> str:
    """Render a card value as a single-line LaTeX cell. Unknown → ``\\textit{?}``."""
    if value is None or value == "" or value == [] or value == {}:
        return r"\textit{?}"
    if isinstance(value, bool):
        return r"\cmark" if value else r"\xmark"
    if isinstance(value, (int, float)):
        return _scale_number(value)
    if isinstance(value, list):
        # Take the first non-null element; fall through to its renderer.
        for v in value:
            if v is not None and v != "":
                return _format_cell(v)
        return r"\textit{?}"
    text = str(value).strip()
    if not text:
        return r"\textit{?}"
    # Truncate long descriptions so the table stays single-line.
    if len(text) > 28:
        text = text[:25] + "…"
    return _latex_escape(text)


def _scale_number(n: float) -> str:
    """Format an integer-ish numeric value with k/M/B/T suffixes.

    4-digit integers in the 1900-2100 range are treated as years and
    rendered verbatim — applying k-scaling to ``2024`` gives the absurd
    ``2.0K``, which was the dominant complaint when the scaffolder
    first ran on real cards.
    """
    if n is None:
        return r"\textit{?}"
    n = float(n)
    if n.is_integer() and 1900 <= n <= 2100:
        return str(int(n))
    if abs(n) >= 1e12:
        return f"{n / 1e12:.1f}T"
    if abs(n) >= 1e9:
        return f"{n / 1e9:.1f}B"
    if abs(n) >= 1e6:
        return f"{n / 1e6:.1f}M"
    if abs(n) >= 1e3:
        return f"{n / 1e3:.1f}K"
    if n.is_integer():
        return str(int(n))
    return f"{n:.2f}"


def _latex_escape(s: str) -> str:
    # Minimal LaTeX-escape for table cells.
    repl = {
        "&": r"\&", "%": r"\%", "_": r"\_", "#": r"\#",
        "$": r"\$", "{": r"\{", "}": r"\}",
    }
    out: list[str] = []
    for ch in s:
        out.append(repl.get(ch, ch))
    return "".join(out)


def _find_matrix_slot(outline: dict[str, Any]) -> dict[str, Any] | None:
    """Locate the single cross_cutting_matrix slot in outline.json.

    Accepts either a top-level ``cross_cutting_matrix`` field OR a
    section/subsection with ``section_type == 'cross_cutting_matrix'``.
    """
    if isinstance(outline.get("cross_cutting_matrix"), dict):
        return outline["cross_cutting_matrix"]
    for sec in outline.get("sections", []) or []:
        if sec.get("section_type") == "cross_cutting_matrix":
            return sec
        for sub in sec.get("subsections", []) or []:
            if isinstance(sub, dict) and sub.get("section_type") == "cross_cutting_matrix":
                return sub
    return None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"[WARN] {path.name}:{lineno} JSON decode failed: {exc.msg}",
                  file=sys.stderr)
    return out


_NAME_LEADING_STOPWORDS = {"a", "an", "the", "towards", "toward", "on", "of", "for"}


def _short_system_name(card: dict[str, Any]) -> str:
    """Pick a short display name for the matrix row.

    Order of preference: an explicit name field on the card
    (``short_name`` / ``nickname`` / ``method`` / ``method_name``), then the
    title's leading "Name" before the first colon, then the cite_key.

    The title is split on the colon ONLY. Hyphens are part of method names
    (``DeepSeek-MoE``, ``ST-MoE``, ``Auxiliary-Loss-Free``) — splitting on
    them shreds the name into nonsense ("ST", "Auxiliary"), which is exactly
    what happened the first time the scaffolder met titles whose method name
    is hyphenated rather than colon-delimited.
    """
    for field in ("short_name", "nickname", "method", "method_name"):
        v = card.get(field)
        if isinstance(v, str) and v.strip():
            return _latex_escape(v.strip())
    title = (card.get("title") or "").strip()
    if title:
        head = title.split(":", 1)[0].strip()
        words = head.split()
        # Drop a leading article/preposition so descriptive titles like
        # "A Theoretical Framework for …" don't surface as "A Theoretical …".
        while len(words) > 1 and words[0].lower() in _NAME_LEADING_STOPWORDS:
            words = words[1:]
        if words:
            return _latex_escape(" ".join(words[:4]))
    return _latex_escape(card.get("cite_key", "?"))


def render_matrix_tex(
    slot: dict[str, Any],
    cards: list[dict[str, Any]],
    max_rows: int | None = None,
    preferred_keys: list[str] | None = None,
) -> str:
    """Render the LaTeX ``table*`` block for the cross-cutting matrix.

    ``slot`` must carry ``col_labels`` (≥ 1 string). ``cards`` is the
    closed paper pool — each card with a ``cite_key`` becomes one row.

    ``preferred_keys`` is the ordered list of cite_keys the *survey itself*
    treats as the methods being compared (the union of the body sections'
    ``primary_papers``). When provided, rows are drawn from it in order:
    the comparison matrix must show the surveyed *methods*, not whichever
    corpus entries happen to have the highest citation count (which is how
    foundation models and off-topic papers leaked into early scaffolds).
    """
    col_labels: list[str] = slot.get("col_labels") or []
    if not col_labels:
        raise ValueError("cross_cutting_matrix slot has no col_labels")

    row_label = slot.get("row_label") or "System"
    expected_rows = slot.get("expected_rows") or len(cards)
    cap = max_rows if max_rows is not None else expected_rows

    # Filter to cards with cite_keys — we need a citation for every row.
    by_key = {c["cite_key"]: c for c in cards if c.get("cite_key")}
    if preferred_keys:
        # Rows = the surveyed methods, in outline order; ignore the rest.
        seen: set[str] = set()
        eligible = []
        for k in preferred_keys:
            if k in by_key and k not in seen:
                eligible.append(by_key[k])
                seen.add(k)
    else:
        eligible = list(by_key.values())
    if cap and len(eligible) > cap:
        if preferred_keys:
            # Preserve the survey's own ordering when capping.
            eligible = eligible[:cap]
        else:
            # No guidance: prefer richer cards, then insertion order.
            eligible.sort(key=lambda c: c.get("_completeness", 0.0), reverse=True)
            eligible = eligible[:cap]

    # Header — row-label column ("l") plus one centred column per dimension.
    col_spec = "l " + " ".join(["c"] * len(col_labels))

    out: list[str] = []
    out.append("% =====================================================================")
    out.append("% Cross-cutting matrix — invariant 4 of structural-template.md.")
    out.append("% This file was scaffolded by tools/scaffold_cross_cutting_matrix.py.")
    out.append("% Cells marked \\textit{?} need editorial attention before submission;")
    out.append("% rerunning the scaffolder OVERWRITES this file (any \\textit{?} you")
    out.append("% have already filled in will be lost). Edit by hand once the")
    out.append("% scaffolder has been run for the last time, OR move the file out of")
    out.append("% sections/ and \\input it from main.tex by a different name.")
    out.append("% =====================================================================")
    out.append("\\begin{table*}[t]")
    out.append("\\centering")
    out.append("\\small")
    out.append("\\caption{Cross-cutting comparison of "
               f"{len(eligible)} systems across {len(col_labels)} dimensions. "
               r"Cells marked \textit{?} are unfilled at scaffolding time.}")
    out.append("\\label{tab:cross-cutting-matrix}")
    # Width-fit the tabular: a 1-label + N-dimension matrix routinely exceeds
    # \textwidth once cells carry real content (it did the first time the
    # scaffolder met a topic with long cell text). \resizebox guarantees the
    # matrix never produces an Overfull \hbox regardless of column count.
    out.append("\\resizebox{\\textwidth}{!}{%")
    out.append(f"\\begin{{tabular}}{{{col_spec}}}")
    out.append("\\toprule")
    header_cells = [f"\\textbf{{{_latex_escape(row_label)}}}"]
    for c in col_labels:
        header_cells.append(f"\\textbf{{{_latex_escape(c)}}}")
    out.append(" & ".join(header_cells) + r" \\")
    out.append("\\midrule")

    for card in eligible:
        cells: list[str] = []
        sys_name = _short_system_name(card)
        ck = card.get("cite_key", "?")
        cells.append(f"{sys_name}~\\citep{{{ck}}}")
        for col in col_labels:
            key = _normalise_col(col)
            paths = _COLUMN_FIELD_MAP.get(key, [])
            value: Any = None
            for p in paths:
                value = _walk(card, p)
                if value not in (None, "", [], {}):
                    break
            cells.append(_format_cell(value))
        out.append(" & ".join(cells) + r" \\")

    out.append("\\bottomrule")
    out.append("\\end{tabular}")
    out.append("}")  # close \resizebox
    out.append("\\end{table*}")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("run_dir", type=Path)
    p.add_argument("--output", type=Path, default=None,
                   help="Where to write the matrix .tex. Defaults to "
                        "<run_dir>/5_paper/sections/<slot_id>.tex.")
    p.add_argument("--max-rows", type=int, default=None,
                   help="Cap the number of rows. Defaults to slot's "
                        "expected_rows, or all cards if unset.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print to stdout instead of writing.")
    args = p.parse_args(argv)

    run_dir: Path = args.run_dir.expanduser().resolve()
    outline_path = run_dir / "4_outline" / "outline.json"
    if not outline_path.exists():
        print(f"ERROR: outline.json not found at {outline_path}", file=sys.stderr)
        return 2
    try:
        outline = json.loads(outline_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: outline.json is not valid JSON: {e}", file=sys.stderr)
        return 2

    slot = _find_matrix_slot(outline)
    if not slot:
        print(
            "ERROR: outline.json declares no cross_cutting_matrix slot. "
            "Add a section/subsection with section_type='cross_cutting_matrix' "
            "(see shared-references/structural-template.md invariant 4).",
            file=sys.stderr,
        )
        return 1

    # Cards source: prefer 1_search/cards.jsonl (rich structured fields),
    # fall back to 1_search/filtered.jsonl (just title + cite_key).
    cards = _load_jsonl(run_dir / "1_search" / "cards.jsonl")
    if not cards:
        cards = _load_jsonl(run_dir / "1_search" / "filtered.jsonl")
    if not cards:
        print(
            "ERROR: no cards.jsonl or filtered.jsonl found under 1_search/. "
            "Run /survey-search before scaffolding the matrix.",
            file=sys.stderr,
        )
        return 2

    # Rows should be the surveyed methods, not the highest-cited corpus
    # entries. Gather the ordered union of body sections' primary_papers as
    # the preferred row set (matrix-slot host section first, if it carries
    # any), and let render_matrix_tex restrict rows to it.
    preferred_keys: list[str] = []
    slot_id = slot.get("id") or slot.get("section_id")
    sections = outline.get("sections", []) or []

    def _collect(sec: dict[str, Any]) -> None:
        for k in (sec.get("primary_papers") or []):
            if k not in preferred_keys:
                preferred_keys.append(k)

    # Host section (the one containing the matrix slot) first.
    for sec in sections:
        subs = sec.get("subsections") or []
        if sec.get("id") == slot_id or any(
            isinstance(s, dict) and s.get("id") == slot_id for s in subs
        ):
            _collect(sec)
    # Then every other body section, in document order.
    for sec in sections:
        if sec.get("section_type") == "body":
            _collect(sec)

    try:
        tex = render_matrix_tex(
            slot, cards, max_rows=args.max_rows,
            preferred_keys=preferred_keys or None,
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        sys.stdout.write(tex)
        return 0

    output = args.output
    if output is None:
        slot_id = slot.get("id") or slot.get("section_id") or "cross_cutting_matrix"
        output = run_dir / "5_paper" / "sections" / f"{slot_id}.tex"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(tex, encoding="utf-8")

    n_rows = tex.count(r"\\") - 2  # toprule + midrule are not data rows
    print(f"✅ Cross-cutting matrix → {output}")
    print(f"   {n_rows} data rows × {len(slot.get('col_labels') or [])} cols")
    return 0


if __name__ == "__main__":
    sys.exit(main())
