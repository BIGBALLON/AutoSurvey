#!/usr/bin/env python3
"""
build_dimension_tables.py — Per-section LaTeX comparison tables for surveys.

For each body section in ``outline.json``, generates a wide LaTeX table
comparing the section's ``primary_papers`` along the schema fields that
belong to that dimension's field group (read from
``brief.derived_schema.json``). Per-paper values are looked up from
``cards.jsonl`` (one detail card per paper) and rendered with k/M/B/T
number scaling, LaTeX escaping, and truncation for layout.

Replaces the abstract-derived ``build_comparison_tables.py``.

Usage::

    python3 build_dimension_tables.py \\
        --cards   <run_dir>/3_extract/cards.jsonl \\
        --outline <run_dir>/4_outline/outline.json \\
        --schema  <run_dir>/0_brief/brief.derived_schema.json \\
        --output-dir <run_dir>/5_paper/figures/tables/ \\
        [--max-cols-per-table 8]   \\
        [--use-natbib]              # use \\citet{} (default true)
        [--verbose]

Output: one ``<section_id>_comparison.tex`` per matched section.
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


def load_cards(path: str | Path) -> dict[str, dict]:
    """Read a JSONL file of per-paper detail cards into ``cite_key -> card``."""
    cards: dict[str, dict] = {}
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            ck = obj.get("cite_key")
            if not ck:
                continue
            cards[ck] = obj
    return cards


def load_outline(path: str | Path) -> list[dict]:
    """Return the list of section dicts from ``outline.json``."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    return obj.get("sections", [])


def load_schema(path: str | Path) -> dict:
    """Load the derived-schema dict.

    Canonical shape (per skills/shared-references/claims-contract.md):

        {"_template_used": "...", "groups": {"<group>": {"<field>": "<type>"}}}

    Also accepted (drift-tolerant alias):

        {"fields": [{"name": "...", "type": "...", "group": "..."}, ...]}

    A `fields` list is normalised into the canonical `groups` shape. Fields
    without a `group` are placed in a synthetic group `_all` so the matcher
    has something to bind sections to.
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        obj = json.load(f)

    if "groups" in obj and isinstance(obj["groups"], dict):
        return obj

    if isinstance(obj.get("fields"), list):
        groups: dict[str, dict[str, str]] = {}
        for fld in obj["fields"]:
            name = fld.get("name")
            if not name:
                continue
            ftype = fld.get("type", "str")
            grp = fld.get("group") or "_all"
            groups.setdefault(grp, {})[name] = ftype
        obj = dict(obj)
        obj["groups"] = groups
        return obj

    # Empty / unknown shape — return as-is so downstream gets an empty groups
    return obj


def _section_id(sec: dict) -> str | None:
    """Outline section id — accept canonical `section_id` or alias `id`."""
    return sec.get("section_id") or sec.get("id")


def _section_papers(sec: dict) -> list[str]:
    """Outline section's paper list — primary_papers (canonical) or papers (alias)."""
    if "primary_papers" in sec:
        return list(sec["primary_papers"])
    return list(sec.get("papers", []))


# ---------------------------------------------------------------------------
# Group / column selection
# ---------------------------------------------------------------------------


def match_group_to_section(
    section: dict,
    schema_groups: dict[str, dict],
) -> str | None:
    """Pick the schema-group whose name best matches a section.

    Heuristic, in priority order:
      1. Exact case-insensitive match between group name and section id/title.
      2. Substring containment in either direction.
      3. Token-overlap score (best non-zero wins).
      4. None if no candidate has any signal at all.
    """
    if not schema_groups:
        return None

    sid = (_section_id(section) or "").lower().strip()
    title = (section.get("title") or "").lower().strip()
    candidates = list(schema_groups.keys())

    # 1) exact match on id or title
    for g in candidates:
        gn = g.lower()
        if gn == sid or gn == title:
            return g

    # 2) substring match (group inside section, or section inside group)
    for g in candidates:
        gn = g.lower()
        if sid and (gn in sid or sid in gn):
            return g
        if title and (gn in title or title in gn):
            return g

    # 3) token-overlap fallback
    def tokens(s: str) -> set[str]:
        return {t for t in s.replace("-", "_").split("_") if t}

    sec_tokens = tokens(sid) | tokens(title)
    if not sec_tokens:
        return None

    best_group: str | None = None
    best_score = 0
    for g in candidates:
        # field-name tokens give finer signal than the bare group name
        field_tokens: set[str] = set()
        for fname in schema_groups[g].keys():
            field_tokens |= tokens(fname.lower())
        score = len(sec_tokens & (tokens(g.lower()) | field_tokens))
        if score > best_score:
            best_score = score
            best_group = g

    if best_score == 0:
        return None
    return best_group


def select_columns(
    rows: list[dict],
    schema_group: dict[str, str],
    max_cols: int,
) -> list[str]:
    """Pick at most ``max_cols`` columns from a schema group.

    Drops fields where every row is ``"N/R"`` / missing. Among the survivors
    prefers simpler types (int/float/str) over ``dict``/``list``, then
    prefers fields with shorter average rendered length.
    """
    field_names = list(schema_group.keys())
    if not field_names:
        return []

    type_priority = {"int": 0, "float": 0, "bool": 0, "str": 1, "list": 2, "dict": 3}

    survivors: list[tuple[int, float, int, str]] = []
    for idx, fname in enumerate(field_names):
        present_values = []
        for row in rows:
            v = row.get(fname, "N/R")
            if v == "N/R" or v is None:
                continue
            present_values.append(v)
        if not present_values:
            continue  # all N/R → drop
        thint = (schema_group.get(fname) or "str").lower()
        tprio = type_priority.get(thint.split("[")[0], 2)
        avg_len = sum(len(str(v)) for v in present_values) / len(present_values)
        survivors.append((tprio, avg_len, idx, fname))

    # Stable sort by (type-priority, avg-length, original index).
    survivors.sort(key=lambda t: (t[0], t[1], t[2]))
    return [f for _, _, _, f in survivors[:max_cols]]


# ---------------------------------------------------------------------------
# Cell formatting
# ---------------------------------------------------------------------------


_LATEX_SPECIAL = [
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("$", r"\$"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
    # `<` / `>` render as inverted punctuation in text mode; map to the
    # text-command forms. Listed AFTER `{`/`}` so the braces they introduce
    # are not re-escaped by the brace rules above.
    ("<", r"\textless{}"),
    (">", r"\textgreater{}"),
]


def escape_latex(text: str) -> str:
    """Escape LaTeX special characters in ``text``.

    Backslash is staged via a sentinel placeholder so the
    ``\\textbackslash{}`` expansion is not double-escaped when subsequent
    rules substitute ``{`` → ``\\{`` and ``}`` → ``\\}``. Naive ordered
    replacement produces ``\\textbackslash\\{\\}`` (invalid LaTeX); the
    sentinel keeps the introduced braces opaque until all other
    substitutions have run.

    See :func:`gen_taxonomy_tikz.latex_escape` for the same pattern.
    """
    if text is None:
        return ""
    s = str(text)
    BS_SENTINEL = "\x00BS\x00"          # never occurs in real input
    s = s.replace("\\", BS_SENTINEL)    # stage backslash first
    for old, new in _LATEX_SPECIAL:
        if old == "\\":                  # already staged via sentinel
            continue
        s = s.replace(old, new)
    s = s.replace(BS_SENTINEL, r"\textbackslash{}")
    return s


# Characters that break LaTeX identifier parsing inside \label{} / \ref{}
# keys. Underscore / hyphen / colon / alnum stay; everything below is
# stripped (NOT escaped — backslash-escapes inside identifier context are
# themselves ill-formed).
_IDENT_BAD_CHARS = re.compile(r"[\s{},\\#%$~^&]")


def escape_latex_ident(text: str) -> str:
    """Sanitize a LaTeX identifier (``\\label{}`` / ``\\ref{}`` key).

    Unlike :func:`escape_latex` which is for free-form prose, identifier
    keys are not rendered — they are matched character-for-character by
    the LaTeX cross-reference machinery. Keys may contain alnum,
    underscore, hyphen, colon and period; anything else (especially
    whitespace, braces, backslash, hash, percent, dollar) silently
    breaks the parser without raising a clear error.

    This helper *removes* such characters rather than escaping them
    because there is no valid escape sequence inside an identifier
    context — ``\\_`` for example renders fine in prose but is illegal
    inside ``\\ref{tab:foo\\_bar}``.
    """
    if text is None:
        return ""
    return _IDENT_BAD_CHARS.sub("", str(text))


def format_number(num: int | float) -> str:
    """Format a number with k/M/B/T scaling for ints ≥ 1000.

    - ``670_000_000_000``     → ``"670B"``
    - ``14_800_000_000_000``  → ``"14.8T"``
    - ``1500``                → ``"1.5k"``
    - ``4096``                → ``"4096"`` (small four-digit ints kept as-is)
    - ``0.85``                → ``"0.85"``
    - ``7.3e-6``              → ``"7.3e-06"``
    """
    if isinstance(num, bool):
        return str(num)
    if not isinstance(num, (int, float)):
        return str(num)

    # floats
    if isinstance(num, float):
        if num == 0:
            return "0"
        absv = abs(num)
        if absv < 1e-3 or absv >= 1e7:
            # very small / very large → scientific
            return f"{num:.1e}"
        # mid-range floats: trim trailing zeros
        s = f"{num:.4f}".rstrip("0").rstrip(".")
        return s if s else "0"

    # ints
    n = int(num)
    absn = abs(n)
    if absn < 1000:
        return str(n)
    # In [1000, 10000): use k-scaling only when it's lossless to one decimal
    # (i.e. divisible by 100). 1500 → "1.5k"; 4096 → "4096".
    if absn < 10_000 and (absn % 100 != 0):
        return str(n)
    sign = "-" if n < 0 else ""
    for divisor, suffix in ((1_000_000_000_000, "T"),
                            (1_000_000_000, "B"),
                            (1_000_000, "M"),
                            (1_000, "k")):
        if absn >= divisor:
            scaled = absn / divisor
            if scaled >= 100:
                s = f"{scaled:.0f}"
            elif scaled >= 10:
                s = f"{scaled:.1f}".rstrip("0").rstrip(".")
            else:
                s = f"{scaled:.2f}".rstrip("0").rstrip(".")
            return f"{sign}{s}{suffix}"
    return str(n)


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def format_cell(value: Any, type_hint: str = "str") -> str:
    """Render ``value`` as a LaTeX-safe table cell.

    - ``"N/R"`` / ``None``           → ``"--"``.
    - numbers                        → ``format_number``.
    - dict                           → ``{k:v, k:v}`` compact, ≤40 chars.
    - list / tuple                   → comma-joined, ≤40 chars.
    - str                            → LaTeX-escaped, ≤30 chars + "...".
    """
    if value is None or value == "N/R":
        return "--"

    # bool first because bool is a subclass of int
    if isinstance(value, bool):
        return escape_latex(str(value))

    if isinstance(value, (int, float)):
        return escape_latex(format_number(value))

    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            short_key = str(k).replace("num_", "").replace("number_of_", "")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vs = format_number(v)
            else:
                vs = str(v)
            parts.append(f"{short_key}:{vs}")
        joined = ", ".join(parts)
        # Dict / list cells render compact key-value pairs verbatim (the spec
        # only requires LaTeX-escaping for ``str`` cells; escaping inside dict
        # values would mangle field names like ``top_k`` for matching).
        return _truncate(joined, 40)

    if isinstance(value, (list, tuple)):
        rendered = []
        for v in value:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                rendered.append(format_number(v))
            elif isinstance(v, dict):
                rendered.append("{...}")
            else:
                rendered.append(str(v))
        joined = ", ".join(rendered)
        return _truncate(joined, 40)

    # strings (and anything else)
    s = str(value)
    return escape_latex(_truncate(s, 30))


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def _column_alignment(columns: list[str], schema_group: dict[str, str]) -> str:
    """Return the LaTeX column-spec for the data columns (after Reference).

    Right-align numeric columns, left-align everything else.
    """
    spec_chars = []
    for c in columns:
        thint = (schema_group.get(c) or "str").lower()
        if thint.startswith(("int", "float")):
            spec_chars.append("r")
        else:
            spec_chars.append("l")
    return "".join(spec_chars)


def _estimate_width(rows: list[list[str]]) -> int:
    """Rough estimate of total rendered width in chars."""
    if not rows:
        return 0
    widths = [0] * len(rows[0])
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
    # add ~3 chars per column for separator overhead
    return sum(widths) + 3 * len(widths)


def generate_table(
    section: dict,
    group_name: str,
    cards: dict[str, dict],
    columns: list[str],
    schema_group: dict[str, str] | None = None,
    use_natbib: bool = True,
) -> str:
    """Render one section's comparison table as a LaTeX string."""
    schema_group = schema_group or {c: "str" for c in columns}
    sid = _section_id(section) or "section"
    # Sections may carry either `title` (canonical) or `name` (outline-sketch
    # shape); fall back to the id only as a last resort so captions read as
    # prose ("...Positional-Encoding Interpolation...") not "03\_positional".
    title = section.get("title") or section.get("name") or sid
    cite_macro = "citet" if use_natbib else "cite"

    # Build header
    header_cells = ["\\textbf{Reference}"] + [
        f"\\textbf{{{escape_latex(c.replace('_', ' '))}}}" for c in columns
    ]

    # Build body rows
    body_rows: list[list[str]] = [header_cells]
    for ck in _section_papers(section):
        card = cards.get(ck)
        if card is None:
            continue
        extraction = card.get("extraction")
        if isinstance(extraction, dict):
            group_data = extraction.get(group_name, {}) or {}
        else:
            group_data = {
                k: v for k, v in card.items()
                if k not in {"cite_key", "paper_id", "title", "extraction", "_template_used"}
            }
        ref_cell = f"\\{cite_macro}{{{ck}}}"
        cells = [ref_cell]
        for col in columns:
            v = group_data.get(col, "N/R")
            cells.append(format_cell(v, schema_group.get(col, "str")))
        body_rows.append(cells)

    if len(body_rows) == 1:
        # Header only — no rows to compare. Still emit a stub so the caller
        # gets a deterministic file; downstream pipeline can decide to skip.
        body_rows.append(["--"] + ["--"] * len(columns))

    col_align = _column_alignment(columns, schema_group)
    tabular_spec = f"@{{}}l{col_align}@{{}}"

    header_line = " & ".join(body_rows[0]) + r" \\"
    data_lines = "\n".join(" & ".join(r) + r" \\" for r in body_rows[1:])

    caption = (
        f"Comparison of {escape_latex(title)} across surveyed works."
    )
    label = f"tab:{sid}_comparison"

    inner = (
        f"\\begin{{tabular}}{{{tabular_spec}}}\n"
        f"\\toprule\n"
        f"{header_line}\n"
        f"\\midrule\n"
        f"{data_lines}\n"
        f"\\bottomrule\n"
        f"\\end{{tabular}}"
    )

    # Width handling: wrap in adjustbox if too wide for a single column.
    est_width = _estimate_width(body_rows)
    if est_width > 90 or len(columns) >= 6:
        inner = (
            "\\begin{adjustbox}{max width=\\textwidth}\n"
            f"{inner}\n"
            "\\end{adjustbox}"
        )

    table = (
        "\\begin{table}[htbp]\n"
        "\\centering\n"
        "\\small\n"
        f"\\caption{{{caption}}}\n"
        f"\\label{{{label}}}\n"
        f"{inner}\n"
        "\\end{table}\n"
    )
    return table


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _build_rows_for_group(
    section: dict,
    group_name: str,
    cards: dict[str, dict],
) -> list[dict]:
    """Collect raw per-paper field dicts for the section's primary papers.

    Card shapes accepted:
      canonical (per claims-contract.md):  card["extraction"][group_name][field] = value
      flat (drift-tolerant):            card[field] = value     (group_name implicit)

    If the canonical nested form is present we read from it; otherwise we
    treat the card itself as the row dict.
    """
    rows = []
    for ck in _section_papers(section):
        card = cards.get(ck)
        if card is None:
            continue
        extraction = card.get("extraction")
        if isinstance(extraction, dict):
            group_data = extraction.get(group_name) or {}
        else:
            # Flat card — drop bookkeeping keys, treat the rest as field values.
            group_data = {
                k: v for k, v in card.items()
                if k not in {"cite_key", "paper_id", "title", "extraction", "_template_used"}
            }
        rows.append(group_data)
    return rows


def _truncate_words(text: str, max_words: int = 4) -> str:
    """Decision-mode cell width: ≤ N whitespace tokens; otherwise add ellipsis."""
    if not isinstance(text, str):
        text = str(text)
    parts = text.split()
    if len(parts) <= max_words:
        return text
    return " ".join(parts[:max_words]) + "…"


def _availability_glyph(value: str) -> str:
    """Map _decision_summary.availability to a ✓/×/partial glyph + LaTeX-safe form."""
    if not value:
        return "\\textemdash"
    v = str(value).strip().lower()
    if v == "open":
        return "$\\checkmark$"
    if v == "closed":
        return "$\\times$"
    if v in {"weights-only", "weights only", "partial"}:
        return "partial"
    return escape_latex(v)


def generate_decision_table(
    section: dict,
    cards: dict[str, dict],
    *,
    use_natbib: bool = True,
    cell_max_words: int = 4,
    max_rows: int = 12,
) -> str | None:
    """Render the decision-summary table for a section.

    Columns (5–6): System / Tier / Approach / Capability / Limitation / Open?
    Cells are clamped to cell_max_words tokens. Returns LaTeX or None when
    the section has no _decision_summary records to render.
    """
    sid = _section_id(section) or ""
    paper_keys = _section_papers(section)
    rows: list[dict] = []
    for key in paper_keys:
        card = cards.get(key)
        if not card:
            continue
        ds = card.get("_decision_summary") or card.get("decision_summary")
        if not isinstance(ds, dict):
            continue
        rows.append({
            "cite_key":            key,
            "one_line_role":       ds.get("one_line_role") or "",
            "key_capability":      ds.get("key_capability") or "",
            "primary_limitation":  ds.get("primary_limitation") or "",
            "availability":        ds.get("availability") or "",
            "tier":                ds.get("tier") or "",
        })
    if not rows:
        return None

    rows = rows[:max_rows]
    cite = "\\citet" if use_natbib else "\\cite"

    body_lines: list[str] = []
    body_lines.append(r"\begin{table*}[tb]")
    body_lines.append(r"  \centering")
    body_lines.append(r"  \footnotesize")
    body_lines.append(r"  \begin{tabular}{@{}lllllc@{}}")
    body_lines.append(r"  \toprule")
    body_lines.append(
        r"  \textbf{System} & \textbf{Tier} & \textbf{Approach} "
        r"& \textbf{Capability} & \textbf{Limitation} & \textbf{Open?} \\"
    )
    body_lines.append(r"  \midrule")
    for r in rows:
        cells = [
            f"{cite}{{{r['cite_key']}}}",
            _truncate_words(escape_latex(r["tier"]),                cell_max_words),
            _truncate_words(escape_latex(r["one_line_role"]),       cell_max_words),
            _truncate_words(escape_latex(r["key_capability"]),      cell_max_words),
            _truncate_words(escape_latex(r["primary_limitation"]),  cell_max_words),
            _availability_glyph(r["availability"]),
        ]
        body_lines.append("  " + " & ".join(cells) + r" \\")
    body_lines.append(r"  \bottomrule")
    body_lines.append(r"  \end{tabular}")
    body_lines.append(
        r"  \caption{Decision summary for the systems compared in \S\ref{sec:" +
        escape_latex_ident(sid) + r"}. Cells are limited to " +
        f"{cell_max_words}" + r" words; full descriptions live in the per-paper "
        r"cards. \emph{Open?} column maps the paper's release status: "
        r"$\checkmark$=open weights, $\times$=closed, \emph{partial}=weights-only.}"
    )
    body_lines.append(r"  \label{tab:" + escape_latex_ident(sid) + r"_decision}")
    body_lines.append(r"\end{table*}")
    return "\n".join(body_lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate per-section LaTeX comparison tables from cards.jsonl"
    )
    parser.add_argument("--cards", required=True, type=Path,
                        help="Path to cards.jsonl (per-paper detail cards).")
    parser.add_argument("--outline", required=True, type=Path,
                        help="Path to outline.json.")
    parser.add_argument("--schema", required=False, type=Path,
                        help="Path to brief.derived_schema.json (required for --mode fields).")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="Directory where <section_id>_comparison.tex files are written.")
    parser.add_argument("--mode", choices=["fields", "decision"], default="fields",
                        help="fields (wide booktabs from schema groups) | "
                             "decision (5-6-col Decision Summary; ≤4 words/cell + ✓/× column). "
                             "decision mode reads cards[i]._decision_summary and ignores --schema. "
                             "If _decision_summary is missing on the cards, decision mode "
                             "warns and falls back to fields.")
    parser.add_argument("--section", type=str, default=None,
                        help="If set, generate only the table for this section_id (used by "
                             "/survey-write's per-section invocation). Default: all sections.")
    parser.add_argument("--max-tables", type=int, default=99,
                        help="Maximum number of tables to emit across the whole paper. "
                             "Narrative discipline recommends ≤3 (see "
                             "shared-references/narrative-scaffolding.md).")
    parser.add_argument("--max-cols-per-table", type=int, default=8)
    parser.add_argument("--use-natbib", dest="use_natbib", action="store_true",
                        default=True, help="Use \\citet{} (default).")
    parser.add_argument("--no-natbib", dest="use_natbib", action="store_false",
                        help="Use plain \\cite{} instead of \\citet{}.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    cards_path: Path = args.cards.expanduser().resolve()
    outline_path: Path = args.outline.expanduser().resolve()
    out_dir: Path = args.output_dir.expanduser().resolve()

    for required in (cards_path, outline_path):
        if not required.exists():
            print(f"ERROR: missing input file: {required}", file=sys.stderr)
            return 2

    out_dir.mkdir(parents=True, exist_ok=True)

    cards = load_cards(cards_path)
    sections = load_outline(outline_path)
    if args.section:
        sections = [s for s in sections if _section_id(s) == args.section]
        if not sections:
            print(f"ERROR: --section {args.section!r} not found in outline.", file=sys.stderr)
            return 2

    # decision mode: cards-only, no schema needed; auto-detect _decision_summary
    if args.mode == "decision":
        any_decision = any(
            isinstance(c.get("_decision_summary") or c.get("decision_summary"), dict)
            for c in cards.values()
        )
        if not any_decision:
            # Falling back to --mode fields silently used to dead-end with
            # 'ERROR: --schema is required' — confusing because
            # the user explicitly asked for decision mode and never asked
            # for fields mode. If --schema isn't available we now try to
            # auto-locate it next to the outline (the standard layout
            # is <run>/0_brief/brief.derived_schema.json), and otherwise
            # fail loudly with a precise diagnosis.
            if args.schema is None:
                run_root = outline_path.parent.parent
                # Two layouts in the wild:
                #   * spec layout:    <run>/0_brief/brief.derived_schema.json
                #   * flat layout:    <run>/brief.derived_schema.json
                # Real production runs use the flat layout.
                candidates = [
                    run_root / "0_brief" / "brief.derived_schema.json",
                    run_root / "brief.derived_schema.json",
                ]
                located = next((c for c in candidates if c.exists()), None)
                if located is not None:
                    args.schema = located
                    print(
                        f"WARN: --mode decision requested but no card has "
                        f"_decision_summary; falling back to --mode fields "
                        f"using auto-located schema at {located}",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "ERROR: --mode decision requested but no card has "
                        "_decision_summary, and --mode fields fallback is "
                        "not viable because --schema was not passed and "
                        "the default locations\n"
                        + "".join(f"  {c}\n" for c in candidates) +
                        "do not exist. Either re-run /survey-write to "
                        "produce decision summaries via lazy claim "
                        "mining, or pass --schema "
                        "brief.derived_schema.json explicitly.",
                        file=sys.stderr,
                    )
                    return 2
            else:
                print(
                    "WARN: --mode decision requested but no card has "
                    "_decision_summary; falling back to --mode fields.",
                    file=sys.stderr,
                )
            args.mode = "fields"

    if args.mode == "decision":
        generated: list[str] = []
        for section in sections:
            if len(generated) >= args.max_tables:
                if args.verbose:
                    print(f"  hit --max-tables={args.max_tables}; stopping")
                break
            sid = _section_id(section) or ""
            tex = generate_decision_table(
                section, cards,
                use_natbib=args.use_natbib,
            )
            if tex is None:
                if args.verbose:
                    print(f"  skip {sid!r}: no _decision_summary records "
                          f"for primary papers")
                continue
            out_path = out_dir / f"{sid}_decision.tex"
            out_path.write_text(tex, encoding="utf-8")
            generated.append(sid)
            if args.verbose:
                print(f"  wrote {out_path}")
        print(f"Generated {len(generated)} decision-mode tables: {generated}")
        return 0

    # ===== fields mode (alternative output) =====
    if args.schema is None:
        print(
            "ERROR: --schema is required for --mode fields. "
            "Pass --schema brief.derived_schema.json or use --mode decision.",
            file=sys.stderr,
        )
        return 2
    schema_path: Path = args.schema.expanduser().resolve()
    if not schema_path.exists():
        print(f"ERROR: missing input file: {schema_path}", file=sys.stderr)
        return 2

    schema_doc = load_schema(schema_path)
    schema_groups: dict[str, dict] = schema_doc.get("groups", {}) or {}

    if args.verbose:
        print(f"Loaded {len(cards)} cards, {len(sections)} sections, "
              f"{len(schema_groups)} schema groups.")

    generated: list[str] = []
    for section in sections:
        if len(generated) >= args.max_tables:
            if args.verbose:
                print(f"  hit --max-tables={args.max_tables}; stopping")
            break
        sid = _section_id(section) or ""
        group_name = match_group_to_section(section, schema_groups)
        if group_name is None:
            print(f"WARNING: no matching schema group for section "
                  f"id={sid!r}; skipping.", file=sys.stderr)
            continue

        schema_group = schema_groups[group_name]
        rows = _build_rows_for_group(section, group_name, cards)
        if not rows:
            print(f"WARNING: section {sid!r} has no resolvable primary "
                  f"papers in cards.jsonl; skipping.", file=sys.stderr)
            continue

        columns = select_columns(rows, schema_group, args.max_cols_per_table)
        if not columns:
            print(f"WARNING: section {sid!r} has no usable columns "
                  f"(all N/R); skipping.", file=sys.stderr)
            continue

        tex = generate_table(
            section=section,
            group_name=group_name,
            cards=cards,
            columns=columns,
            schema_group=schema_group,
            use_natbib=args.use_natbib,
        )
        out_path = out_dir / f"{sid}_comparison.tex"
        out_path.write_text(tex, encoding="utf-8")
        generated.append(sid)
        if args.verbose:
            print(f"  wrote {out_path}")

    print(f"Generated {len(generated)} comparison tables: {generated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
