#!/usr/bin/env python3
"""
gen_taxonomy_tikz.py — Beautiful TikZ taxonomy figures for survey papers.

Produces a self-contained `\\begin{figure}…\\end{figure}` block. Two layouts:

  --layout tree     (default) horizontal organizational chart with L-shaped edges.
                    Cleaner for 5–15 nodes; matches the style of most published
                    survey figures (e.g., LLM survey by Zhao et al., RAG survey).
                    Supports a description line for each node.

  --layout radial   improved hub-and-spoke with drop shadows and curved Bezier
                    edges. Best for 4–8 nodes when you want a "centered" feel.

Both layouts use:
  - Designer color palette (Tableau-10 / Category10 inspired soft pastels)
  - shadows.blur drop shadows for depth
  - sans-serif bold node titles + italic paper count + serif description
  - Acronym-aware title casing (LLM, RAG, MoE, PEFT, …)

Required TikZ libraries (add to main.tex preamble):
  \\usetikzlibrary{shapes,positioning,arrows.meta,shadows.blur,calc,fit}

Output: 5_paper/figures/00_taxonomy.tex

Usage:
  gen_taxonomy_tikz.py <run_dir>                       # default tree layout
  gen_taxonomy_tikz.py <run_dir> --layout radial
  gen_taxonomy_tikz.py <run_dir> --layout tree --no-descriptions
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# 8-color palette — Tableau-10 inspired, soft enough to read black text on, with
# matching darker tint for borders. Each entry: (fill_color, border_color).
PALETTE: list[tuple[str, str]] = [
    ("blue!18",     "blue!55!black"),       # 1: cool blue
    ("orange!22",   "orange!75!black"),     # 2: warm orange
    ("green!20",    "green!55!black"),      # 3: leaf green
    ("yellow!28",   "yellow!75!black"),     # 4: golden
    ("cyan!22",     "cyan!55!black"),       # 5: aqua
    ("violet!18",   "violet!55!black"),     # 6: lavender
    ("red!18",      "red!55!black"),        # 7: rose
    ("teal!22",     "teal!55!black"),       # 8: teal
    ("magenta!18",  "magenta!55!black"),    # 9: pink
    ("brown!18",    "brown!55!black"),      # 10: warm brown
    ("olive!22",    "olive!55!black"),      # 11: olive
    ("pink!28",     "pink!75!black"),       # 12: blush
]

ACRONYMS = {
    "llm", "rag", "moe", "peft", "mlp", "rnn", "lstm", "gru", "cnn",
    "vit", "ssm", "ssms", "lora", "qlora", "rlhf", "dpo", "kv",
    "nlp", "cv", "ai", "ml", "gpu", "cpu", "tpu", "api",
    "llms", "vae", "gan", "gans", "ddpm", "flan", "gpt",
    "bert", "t5", "v3", "v2",
}

# Words that stay lowercase in title case (unless first or last)
LOWERCASE_TITLE_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "if", "in",
    "nor", "of", "on", "or", "so", "the", "to", "up", "yet", "via",
    "vs", "with", "from", "into", "over",
}


def _node_name(node: dict) -> str:
    """Taxonomy node display name — accept canonical `name` or alias `title`."""
    return node.get("name") or node.get("title") or node.get("id", "")


def title_case_smart(text: str) -> str:
    """Title-case with two refinements:
       1. Preserve acronyms (LLM, RAG, PEFT, …)
       2. Keep "small" words lowercase (and, of, the, …) unless first/last
       3. Properly handle hyphenated compounds (Parameter-Efficient, Long-Context)
    """
    words = text.split()
    out: list[str] = []
    last_idx = len(words) - 1
    for i, w in enumerate(words):
        # Strip trailing punctuation for the lookup key
        clean = w.rstrip(".,;:!?").lower()
        if clean in ACRONYMS:
            out.append(w.upper())
            continue
        # Lowercase function-words unless first/last
        if 0 < i < last_idx and clean in LOWERCASE_TITLE_WORDS:
            out.append(clean)
            continue
        # Hyphenated word: capitalize each segment, preserving acronyms inside
        if "-" in w:
            segments = []
            for seg in w.split("-"):
                seg_clean = seg.lower().rstrip(".,;:!?")
                if seg_clean in ACRONYMS:
                    segments.append(seg.upper())
                else:
                    segments.append(seg.capitalize())
            out.append("-".join(segments))
            continue
        out.append(w.capitalize())
    return " ".join(out)


def latex_escape(text: str) -> str:
    """Escape LaTeX special chars + normalise unicode that ptmr8t can't render.

    Covers the same 10 metacharacters as
    ``build_dimension_tables.escape_latex`` so a brief topic / tier label /
    feature column with ``$``, ``{``, ``\\`` etc. cannot break the tikz
    figure compile.

    Backslash is replaced via a sentinel placeholder, then re-emitted at
    the end. Otherwise the ``{}`` introduced by ``\\textbackslash{}``
    would be matched a second time when we substitute ``{`` → ``\\{`` and
    we'd end up with ``\\textbackslash\\{\\}`` — bad LaTeX.
    """
    if text is None:
        return ""
    text = str(text)
    BS_SENTINEL = "\x00BS\x00"  # never appears in real input
    text = text.replace("\\", BS_SENTINEL)
    text = (text
            .replace("{",  r"\{")
            .replace("}",  r"\}")
            .replace("$",  r"\$")
            .replace("#",  r"\#")
            .replace("&",  r"\&")
            .replace("_",  r"\_")
            .replace("%",  r"\%")
            .replace("~",  r"\textasciitilde{}")
            .replace("^",  r"\textasciicircum{}"))
    text = text.replace(BS_SENTINEL, r"\textbackslash{}")
    # Unicode dashes / quotes / ellipsis → LaTeX equivalents (matches prose_polish)
    text = (text
            .replace("—", "---")   # em-dash
            .replace("–", "--")    # en-dash
            .replace("…", r"\dots ")
            .replace("“", "``").replace("”", "''")
            .replace("‘", "`").replace("’", "'"))
    return text


def shorten(text: str, max_len: int) -> str:
    """Truncate to max_len chars at a word boundary, using LaTeX \\dots."""
    text = text.strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    # Use a Unicode ellipsis; latex_escape() maps it to \dots. Emitting raw
    # LaTeX (\,\dots) here would be re-escaped by latex_escape into the
    # literal text "\,\dots" in the figure.
    return cut.rstrip(",.;:") + "…"


# ──────────────────────────────────────────────────────────────────────────────
#  TREE LAYOUT (horizontal, root on left)
# ──────────────────────────────────────────────────────────────────────────────

def render_tree(taxonomy: dict, node_counts: dict[str, int],
                topic: str, total_papers: int,
                show_descriptions: bool = True,
                hide_empty: bool = True) -> str:
    all_nodes = taxonomy["nodes"]
    # Optionally drop empty nodes (cleaner visual; their absence is noted in caption)
    if hide_empty:
        nodes = [n for n in all_nodes if node_counts.get(n["id"], 0) > 0]
        n_hidden = len(all_nodes) - len(nodes)
    else:
        nodes = all_nodes
        n_hidden = 0
    n = len(nodes)
    # Vertical spacing per node (in cm). With descriptions, each box renders
    # title (~0.5cm) + 2 lines of description (~0.7cm) + 0.3cm padding ≈ 1.5cm.
    # Add ~0.55cm gap between boxes so they don't touch.
    spacing = 2.05 if show_descriptions else 0.95
    total_height = (n - 1) * spacing
    # Y coordinates: top to bottom (positive y = up in TikZ)
    ys = [total_height / 2 - i * spacing for i in range(n)]

    root_x = 0.0
    leaf_x = 4.0        # left edge of category boxes

    spoke_lines: list[str] = []
    edge_lines: list[str] = []

    for i, node in enumerate(nodes):
        nid = node["id"]
        name = title_case_smart(latex_escape(_node_name(node)))
        count = node_counts.get(nid, 0)
        fill, border = PALETTE[i % len(PALETTE)]
        y = ys[i]

        # Build node body — title and count on first line; description below.
        # Use a small parenthesized count rather than "N papers" so it never wraps
        count_str = rf"\textcolor{{gray!55!black}}{{({count})}}"
        if show_descriptions and node.get("description"):
            # Cap at ~85 chars so it fits in 2 lines @ 6.6cm wide
            desc = latex_escape(shorten(node["description"], 85))
            body = (
                rf"{{\sffamily\bfseries\small {name}}}~{count_str}"
                rf"\\[3pt]"
                rf"{{\footnotesize\color{{gray!50!black}} {desc}}}"
            )
            text_width = "6.6cm"
            min_height = "1.55cm"
        else:
            body = (
                rf"{{\sffamily\bfseries\small {name}}}~{count_str}"
            )
            text_width = "5.6cm"
            min_height = "0.7cm"

        spoke_lines.append(
            f"\\node[category, fill={fill}, draw={border}, "
            f"text width={text_width}, minimum height={min_height}] "
            f"({nid}) at ({leaf_x},{y:.3f}) {{{body}}};"
        )

        # L-shaped edge: from the root east anchor to the node's west.
        edge_lines.append(
            f"\\draw[branch] (root.east) -- ++(1.2,0) |- ({nid}.west);"
        )

    # Root (center) node
    if n_hidden > 0:
        root_subtitle = (rf"{{\normalfont\itshape\footnotesize "
                         rf"{total_papers}~papers, {n}~nodes shown"
                         rf"\\(+{n_hidden} empty)}}")
    else:
        root_subtitle = (rf"{{\normalfont\itshape\footnotesize "
                         rf"{total_papers}~papers, {n}~nodes}}")
    root_label = (
        rf"\sffamily\bfseries {title_case_smart(latex_escape(topic))}"
        rf"\\[2pt]"
        + root_subtitle
    )

    # Caption notes the hidden empty nodes if any
    caption_extra = (rf" An additional {n_hidden} taxonomy node{'s' if n_hidden != 1 else ''} "
                     rf"with no surveyed papers {'are' if n_hidden != 1 else 'is'} omitted from this view."
                     ) if n_hidden > 0 else ""

    body = "\n".join([
        r"\begin{figure}[!t]",
        r"\centering",
        r"\begin{tikzpicture}[",
        r"  >=Stealth,",
        r"  root/.style={rounded corners=6pt, fill=gray!12, draw=gray!55,",
        r"    line width=0.7pt, drop shadow={opacity=0.35,shadow xshift=1.5pt,",
        r"    shadow yshift=-1.5pt}, align=center, text width=2.6cm,",
        r"    minimum height=1.6cm, inner sep=8pt},",
        r"  category/.style={rounded corners=4pt, draw, line width=0.6pt,",
        r"    drop shadow={opacity=0.25,shadow xshift=1.2pt,shadow yshift=-1.2pt},",
        r"    align=left, inner sep=7pt, anchor=west,",
        r"    font=\small},",
        r"  branch/.style={draw=gray!55, line width=0.7pt, rounded corners=3pt}",
        r"]",
        "% Root node (left)",
        f"\\node[root] (root) at ({root_x},0) {{{root_label}}};",
        f"% {n} category nodes (right column)",
        *spoke_lines,
        "% L-shaped edges from root to each category",
        *edge_lines,
        r"\end{tikzpicture}",
        rf"\caption{{Taxonomy of \textit{{{title_case_smart(latex_escape(topic))}}} "
        rf"surveyed in this work: {total_papers} papers organized into {n} thematic nodes.{caption_extra}}}",
        r"\label{fig:taxonomy}",
        r"\end{figure}",
        "",
    ])
    return body


# ──────────────────────────────────────────────────────────────────────────────
#  RADIAL LAYOUT (improved hub-and-spoke)
# ──────────────────────────────────────────────────────────────────────────────

def render_radial(taxonomy: dict, node_counts: dict[str, int],
                  topic: str, total_papers: int,
                  radius: float = 4.2,
                  show_descriptions: bool = False) -> str:
    nodes = taxonomy["nodes"]
    n = len(nodes)

    spoke_lines: list[str] = []
    edge_lines: list[str] = []

    for i, node in enumerate(nodes):
        # Start at top, go clockwise
        angle = -math.pi / 2 + 2 * math.pi * i / n
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        fill, border = PALETTE[i % len(PALETTE)]
        nid = node["id"]
        name = title_case_smart(latex_escape(_node_name(node)))
        count = node_counts.get(nid, 0)

        # Body content
        if show_descriptions and node.get("description"):
            desc = latex_escape(shorten(node["description"], 50))
            body = (
                rf"\textbf{{\sffamily {name}}}\\[1pt]"
                rf"{{\itshape\scriptsize {count}~paper{'s' if count != 1 else ''}}}\\[1pt]"
                rf"{{\scriptsize\color{{gray!50!black}} {desc}}}"
            )
            text_width = "3.2cm"
            min_height = "1.4cm"
        else:
            body = (
                rf"\textbf{{\sffamily {name}}}\\[1pt]"
                rf"{{\itshape\scriptsize {count}~paper{'s' if count != 1 else ''}}}"
            )
            text_width = "3.0cm"
            min_height = "1.1cm"

        spoke_lines.append(
            f"\\node[spoke, fill={fill}, draw={border}, "
            f"text width={text_width}, minimum height={min_height}] "
            f"({nid}) at ({x:.3f},{y:.3f}) {{{body}}};"
        )
        # Curved edge with subtle bend
        bend = 6 if i % 2 == 0 else -6
        edge_lines.append(
            f"\\draw[edge] (center.north) to[bend left={bend}] ({nid});"
        )

    # Recompute root with cleaner edge anchor
    edge_lines = []
    for i, node in enumerate(nodes):
        nid = node["id"]
        edge_lines.append(f"\\draw[edge] (center) -- ({nid});")

    # Center label
    root_label = (
        rf"\sffamily\bfseries {title_case_smart(latex_escape(topic))}"
        rf"\\[2pt]"
        rf"{{\normalfont\itshape\footnotesize {total_papers}~papers}}"
    )

    body = "\n".join([
        r"\begin{figure}[!t]",
        r"\centering",
        r"\begin{tikzpicture}[",
        r"  center/.style={circle, fill=gray!22, draw=gray!65, line width=0.8pt,",
        r"    drop shadow={opacity=0.4,shadow xshift=1.5pt,shadow yshift=-1.5pt},",
        r"    align=center, minimum size=2.4cm, inner sep=4pt},",
        r"  spoke/.style={rounded corners=4pt, draw, line width=0.6pt,",
        r"    drop shadow={opacity=0.25,shadow xshift=1.2pt,shadow yshift=-1.2pt},",
        r"    align=center, font=\small},",
        r"  edge/.style={draw=gray!55, line width=0.8pt}",
        r"]",
        "% Center node",
        f"\\node[center] (center) at (0,0) {{{root_label}}};",
        f"% {n} spoke nodes at radius={radius}",
        *spoke_lines,
        "% Edges from center to each spoke",
        *edge_lines,
        r"\end{tikzpicture}",
        rf"\caption{{Taxonomy of \textit{{{title_case_smart(latex_escape(topic))}}} "
        rf"surveyed in this work: {total_papers} papers organized into {n} thematic nodes.}}",
        r"\label{fig:taxonomy}",
        r"\end{figure}",
        "",
    ])
    return body


# ──────────────────────────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────────────────────────

def render_matrix(tier_axis: dict, taxonomy: dict | None, topic: str,
                   total_papers: int, *, show_descriptions: bool = True) -> str:
    """Matrix layout: tiers (rows) × feature_columns (cols) × cells.

    `tier_axis` is the outline-sketch artifact:
        {
          "name": "Pretraining Generation",
          "tiers": [{"id": "T1", "label": "...", "description": "..."}, ...],
          "feature_columns": ["Architecture", "Data scale", ...],
          "cells": {"T1": {"Architecture": ["Dense", "MHA"], ...}, ...},
          "key_insight": "<one-sentence takeaway shown at the figure bottom>"
        }
    """
    name = tier_axis.get("name", "Tier")
    tiers = tier_axis.get("tiers") or []
    cols = tier_axis.get("feature_columns") or []
    cells = tier_axis.get("cells") or {}
    key_insight = tier_axis.get("key_insight") or ""

    if not tiers or not cols:
        # Caller should have caught this, but be defensive.
        return ("% gen_taxonomy_tikz --layout matrix: tier_axis missing "
                "tiers or feature_columns; falling back to tree layout.\n")

    n_tiers = len(tiers)
    n_cols = len(cols)
    cell_w = 3.6        # cm
    cell_h = 1.6        # cm
    header_h = 1.0      # cm
    row_label_w = 2.6   # cm

    # Tier-graded fill colors (T1 light → TN dark). The palette is cycled
    # via modulo so it works for any number of tiers.
    fill_for_tier = {}
    for i, tier in enumerate(tiers):
        fill, border = PALETTE[i % len(PALETTE)]
        fill_for_tier[tier["id"]] = (fill, border)

    # Maturity overlay: orthogonal epistemic axis. If any tier
    # carries `maturity` ∈ {mature, frontier, speculative}, render a
    # small badge inside that tier's row label and emit a legend strip
    # below the figure. tier_axis lacking the field renders unchanged.
    _MATURITY_STYLE = {
        "mature":      ("blue!18",   "blue!55!black",   "Mature"),
        "frontier":    ("orange!22", "orange!75!black", "Frontier"),
        "speculative": ("gray!22",   "gray!60!black",   "Speculative"),
    }
    maturity_for_tier = {
        t["id"]: t.get("maturity")
        for t in tiers
        if isinstance(t, dict) and t.get("maturity") in _MATURITY_STYLE
    }
    has_maturity = bool(maturity_for_tier)

    lines: list[str] = []
    lines.append(r"% Auto-generated by gen_taxonomy_tikz.py --layout matrix")
    lines.append(r"% Required preamble: \usetikzlibrary{shapes,positioning,fit,calc,shadows.blur}")
    lines.append(r"\begin{figure*}[tb]")
    lines.append(r"  \centering")
    # The matrix is wider than a single text column; scale it to \textwidth
    # so it never overflows the margin regardless of venue column width.
    lines.append(r"  \resizebox{\textwidth}{!}{%")
    lines.append(r"  \begin{tikzpicture}[every node/.style={font=\footnotesize}]")
    # Title bar — sit clearly ABOVE the header row (headers are centred at
    # n_tiers*cell_h + header_h/2, so place the title a further header_h up)
    # and keep it short; the full topic lives in the figure caption, so a
    # verbose in-figure title only collides with the first column header.
    lines.append(
        r"    \node[anchor=south west, font=\bfseries\small] at (0," +
        f"{n_tiers * cell_h + header_h + 0.45:.2f}) "
        f"{{{latex_escape(name)} \\(\\times\\) feature dimensions}};"
    )
    # Column headers
    for ci, col in enumerate(cols):
        x = row_label_w + ci * cell_w + cell_w / 2
        y = n_tiers * cell_h + header_h / 2
        lines.append(
            f"    \\node[fill=gray!18, draw=gray!55!black, minimum width={cell_w}cm, "
            f"minimum height={header_h}cm, font=\\bfseries\\footnotesize, "
            f"text width={cell_w - 0.3}cm, align=center] "
            f"at ({x:.2f},{y:.2f}) {{{latex_escape(col)}}};"
        )
    # Rows: tier label + cells
    for ti, tier in enumerate(tiers):
        # Visually invert so T1 is on top
        row_idx = n_tiers - 1 - ti
        y_center = row_idx * cell_h + cell_h / 2
        fill, border = fill_for_tier[tier["id"]]
        # Row label (tier id + label)
        label = f"\\textbf{{{latex_escape(tier['id'])}}} \\ {latex_escape(shorten(tier.get('label', ''), 22))}"
        lines.append(
            f"    \\node[fill={fill}, draw={border}, minimum width={row_label_w}cm, "
            f"minimum height={cell_h}cm, font=\\footnotesize, text width={row_label_w - 0.2}cm, "
            f"align=center] at ({row_label_w / 2:.2f},{y_center:.2f}) {{{label}}};"
        )
        # Maturity badge — small chip pinned to the row label's top-right
        # corner. Visible without dominating the tier's primary color.
        m = maturity_for_tier.get(tier["id"])
        if m:
            m_fill, m_border, m_label = _MATURITY_STYLE[m]
            badge_x = row_label_w - 0.45
            badge_y = y_center + cell_h / 2 - 0.25
            lines.append(
                f"    \\node[fill={m_fill}, draw={m_border}, "
                f"rounded corners=2pt, inner sep=1pt, "
                f"font=\\tiny\\bfseries] "
                f"at ({badge_x:.2f},{badge_y:.2f}) {{{m_label}}};"
            )
        # Per-feature cells
        cell_map = cells.get(tier["id"], {})
        for ci, col in enumerate(cols):
            x_center = row_label_w + ci * cell_w + cell_w / 2
            entries = cell_map.get(col, []) or []
            if isinstance(entries, str):
                entries = [entries]
            # Render up to 4 bullets per cell
            visible = entries[:4]
            cell_text = " \\\\ ".join(
                "$\\bullet$ " + latex_escape(shorten(str(e), 22)) for e in visible
            ) or "—"
            # Body cells reuse the row's tint. ``fill`` is already a complete
            # xcolor expression like ``blue!18`` (18% blue into white); xcolor
            # does not allow chaining a second percentage after it
            # (``blue!18!40`` / ``blue!18!60!white`` both fail), so use it as-is.
            lines.append(
                f"    \\node[fill={fill}, draw={border}, minimum width={cell_w}cm, "
                f"minimum height={cell_h}cm, font=\\scriptsize, text width={cell_w - 0.3}cm, "
                f"align=left] at ({x_center:.2f},{y_center:.2f}) {{{cell_text}}};"
            )
    # Maturity legend strip (only when at least one tier is annotated).
    # Sits just below the matrix, above the Key Insight footer if any.
    total_w = row_label_w + n_cols * cell_w
    insight_y_top = -0.1
    if has_maturity:
        legend_y = -0.55
        # one centered node containing all three swatches inline
        chips: list[str] = []
        for key in ("mature", "frontier", "speculative"):
            f, b, lbl = _MATURITY_STYLE[key]
            chips.append(
                f"\\tikz\\node[fill={f},draw={b},rounded corners=2pt,"
                f"inner sep=1pt,font=\\tiny\\bfseries] {{{lbl}}};"
            )
        chips_str = " \\quad ".join(chips)
        lines.append(
            f"    \\node[minimum width={total_w}cm, minimum height=0.7cm, "
            f"font=\\footnotesize, anchor=north, align=center] "
            f"at ({total_w / 2:.2f},{legend_y:.2f}) "
            f"{{Maturity overlay: {chips_str}}};"
        )
        insight_y_top = legend_y - 0.7  # push insight banner further down

    # Key Insight footer
    if key_insight:
        lines.append(
            f"    \\node[fill=yellow!18, draw=yellow!75!black, minimum width={total_w}cm, "
            f"minimum height=0.9cm, font=\\itshape\\footnotesize, text width={total_w - 0.3}cm, "
            f"align=center, anchor=north] "
            f"at ({total_w / 2:.2f},{insight_y_top:.2f}) {{Key Insight: {latex_escape(key_insight)}}};"
        )
    lines.append(r"  \end{tikzpicture}%")
    lines.append(r"  }")
    cap = (
        f"Comprehensive taxonomy of {latex_escape(topic)} across "
        f"{n_cols} feature dimensions and {n_tiers} tiers "
        f"({total_papers} papers surveyed)."
    )
    lines.append(r"  \caption{" + cap + r"}")
    lines.append(r"  \label{fig:taxonomy_matrix}")
    lines.append(r"\end{figure*}")
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--layout", choices=["tree", "radial", "matrix"], default="tree",
                   help="tree (default; horizontal org chart) | radial (hub+spoke) | "
                        "matrix (N×M tier-by-feature with Key Insight footer)")
    p.add_argument("--no-descriptions", action="store_true",
                   help="Don't include node description on a second line")
    p.add_argument("--include-empty", action="store_true",
                   help="Include taxonomy nodes with 0 papers (default: hide them)")
    p.add_argument("--radius", type=float, default=4.2,
                   help="Spoke distance from center (radial only)")
    args = p.parse_args()

    run_dir = args.run_dir.expanduser().resolve()
    taxonomy_path = run_dir / "3_taxonomy.json"
    clusters_path = run_dir / "2_cluster" / "clusters.json"
    outline_path = run_dir / "4_outline" / "outline.json"   # tier_axis source
    state_path = run_dir / "state.json"
    out_path = run_dir / "5_paper" / "figures" / "00_taxonomy.tex"

    # Matrix layout reads tier_axis from outline.json (clustering is folded
    # into outline-sketch). Tree/radial layouts still read 3_taxonomy.json
    # when a run produces one — surveys older than the outline-sketch
    # consolidation kept it as a separate artifact.
    if args.layout == "matrix":
        if not outline_path.exists():
            print(f"WARN: --layout matrix requires {outline_path} (outline-sketch); "
                  f"falling back to tree layout.", file=sys.stderr)
            args.layout = "tree"
        else:
            outline_doc = json.loads(outline_path.read_text())
            tier_axis = outline_doc.get("tier_axis")
            if not tier_axis:
                print("WARN: outline.json has no tier_axis; falling back to tree layout.",
                      file=sys.stderr)
                args.layout = "tree"

    if args.layout != "matrix" and not taxonomy_path.exists():
        print(f"ERROR: {taxonomy_path} not found", file=sys.stderr)
        return 2

    taxonomy = json.loads(taxonomy_path.read_text()) if taxonomy_path.exists() else {"nodes": []}

    # Derive per-node paper counts. Accept four clusters.json shapes, in order:
    #   (a) precomputed:    {"node_counts": {node_id: int, ...}}
    #   (b) canonical:      {"assignments": {paper_id: node_id, ...}}
    #   (c) inverted:       {node_id: [paper_id, ...], ...}
    #   (d) flat:           {paper_id: node_id, ...}
    node_counts: dict[str, int] = {}
    if clusters_path.exists():
        cl = json.loads(clusters_path.read_text())
        if isinstance(cl.get("node_counts"), dict):
            node_counts = dict(cl["node_counts"])
        elif isinstance(cl.get("assignments"), dict):
            for _, node in cl["assignments"].items():
                node_counts[node] = node_counts.get(node, 0) + 1
        else:
            for k, v in cl.items():
                if isinstance(v, list):
                    node_counts[k] = node_counts.get(k, 0) + len(v)
                elif isinstance(v, str):
                    node_counts[v] = node_counts.get(v, 0) + 1

    total_papers = sum(node_counts.values()) if node_counts else 0
    if total_papers == 0:
        # Fallback: count from filtered.jsonl
        filtered = run_dir / "1_search" / "filtered.jsonl"
        if filtered.exists():
            total_papers = sum(1 for line in filtered.read_text().splitlines() if line.strip())

    # Topic resolution: state.json → brief.parsed.json → "Survey"
    topic = "Survey"
    if state_path.exists():
        topic = json.loads(state_path.read_text()).get("topic") or topic
    if topic == "Survey":
        brief = run_dir / "brief.parsed.json"
        if brief.exists():
            topic = json.loads(brief.read_text()).get("topic") or topic

    show_descriptions = not args.no_descriptions

    if args.layout == "tree":
        tex = render_tree(taxonomy, node_counts, topic, total_papers,
                          show_descriptions=show_descriptions,
                          hide_empty=not args.include_empty)
    elif args.layout == "radial":
        tex = render_radial(taxonomy, node_counts, topic, total_papers,
                           radius=args.radius, show_descriptions=show_descriptions)
    else:  # matrix
        tex = render_matrix(tier_axis, taxonomy, topic, total_papers,
                             show_descriptions=show_descriptions)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(tex)

    print(f"✅ Taxonomy {args.layout} TikZ → {out_path.relative_to(run_dir)}")
    print(f"   Topic:        {topic}  ({total_papers} papers)")
    if args.layout == "matrix":
        n_tiers = len(tier_axis.get("tiers", []))
        n_cols  = len(tier_axis.get("feature_columns", []))
        print(f"   Matrix:       {n_tiers} tiers × {n_cols} feature columns")
        if tier_axis.get("key_insight"):
            print(f"   Key Insight:  {tier_axis['key_insight'][:80]}…")
    else:
        print(f"   Nodes:        {len(taxonomy.get('nodes', []))}")
    print(f"   Layout:       {args.layout}")
    print(f"   Descriptions: {'on' if show_descriptions else 'off'}")
    print()
    print("   ⚠ Required main.tex preamble:")
    print(r"      \usepackage{tikz}")
    print(r"      \usetikzlibrary{shapes,positioning,arrows.meta,shadows.blur,calc,fit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
