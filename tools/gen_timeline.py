#!/usr/bin/env python3
"""gen_timeline.py — Per-work timeline figure for a survey.

Default mode is a **lane plot**: x-axis = publication time, y-axis = one
lane per taxonomy node, each surveyed work plotted as a labeled point on
its lane. This communicates *what came when, in which thread* — much more
informative than the year-aggregate bar chart this tool used to emit.

Two invocation styles are supported:

    # New style — operates on a run directory; auto-detects clusters / cards
    python3 gen_timeline.py <run_dir> [--output figures/01_timeline.pdf]

    # Bare-filtered-jsonl style — produces the year-bar chart
    python3 gen_timeline.py <filtered.jsonl> --output figures/01_timeline.pdf

When invoked on a run directory but the run has no clusters.json (or it is
empty), gen_timeline falls back to the year-bar chart automatically.

Requires matplotlib (pip install matplotlib).
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Loaders — accept canonical + drift-tolerant schemas
# ---------------------------------------------------------------------------


def _paper_id(p: dict[str, Any]) -> str | None:
    return p.get("paper_id") or p.get("cite_key")


def load_papers(filtered_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for lineno, line in enumerate(filtered_path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"[WARN] {filtered_path.name}:{lineno} JSON decode failed: {exc.msg}",
                  file=sys.stderr)
    return out


def load_clusters(clusters_path: Path) -> dict[str, str]:
    """Return canonical {paper_id: node_id} flat dict regardless of input shape.

    Accepts:
      canonical:      {"assignments": {pid: node, ...}}
      inverted:       {node: [pid, ...]}                       (list-valued)
      flat:           {pid: node, ...}                          (string-valued)
    """
    if not clusters_path.exists():
        return {}
    cl = json.loads(clusters_path.read_text())
    if isinstance(cl.get("assignments"), dict):
        return dict(cl["assignments"])
    flat: dict[str, str] = {}
    for k, v in cl.items():
        if isinstance(v, list):
            for pid in v:
                flat[pid] = k
        elif isinstance(v, str):
            flat[k] = v
    return flat


def load_taxonomy(taxonomy_path: Path) -> list[dict[str, Any]]:
    if not taxonomy_path.exists():
        return []
    tx = json.loads(taxonomy_path.read_text())
    return tx.get("nodes", []) if isinstance(tx, dict) else []


def load_cards(cards_path: Path) -> dict[str, dict[str, Any]]:
    """cards.jsonl → {cite_key: card_dict}. Returns empty dict if file absent."""
    cards: dict[str, dict[str, Any]] = {}
    if not cards_path.exists():
        return cards
    for lineno, line in enumerate(cards_path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[WARN] {cards_path.name}:{lineno} JSON decode failed: {exc.msg}",
                  file=sys.stderr)
            continue
        ck = _paper_id(obj)
        if ck:
            cards[ck] = obj
    return cards


# ---------------------------------------------------------------------------
# Date + label helpers
# ---------------------------------------------------------------------------


_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?")


def paper_date(p: dict[str, Any]) -> date | None:
    """Extract a publication date from a filtered.jsonl record.

    Priority:
      1. `published` / `date` ISO-prefix string (YYYY[-MM[-DD]])
      2. `year` integer (placed at month=06, day=15 — mid-year midpoint)
    """
    for k in ("published", "date", "issued"):
        s = p.get(k)
        if isinstance(s, str):
            m = _DATE_RE.match(s.strip())
            if m:
                y = int(m.group(1))
                mo = int(m.group(2) or 6)
                d = int(m.group(3) or 15)
                try:
                    return date(y, max(1, min(12, mo)), max(1, min(28, d)))
                except ValueError:
                    pass
    y = p.get("year")
    if isinstance(y, (int, float)) and 1990 <= int(y) <= 2030:
        return date(int(y), 6, 15)
    return None


def paper_label(p: dict[str, Any], cards: dict[str, dict]) -> str:
    """Pick a short, recognisable label for a paper."""
    pid = _paper_id(p)
    if pid and pid in cards:
        name = cards[pid].get("model_name")
        if name:
            return str(name)
    # Fall back to first-author + 2-digit year
    first = (p.get("authors") or ["?"])[0]
    last = first.split()[-1] if first else "?"
    yr = p.get("year")
    if isinstance(yr, (int, float)):
        yr_s = f"'{int(yr) % 100:02d}"
    else:
        yr_s = ""
    return f"{last}{yr_s}"


def _node_label(node: dict[str, Any]) -> str:
    return node.get("name") or node.get("title") or node.get("id", "")


def _year_tick_intervals(span_years: int) -> tuple[int, int | None]:
    """Pick (major, minor) year-tick spacings for a time span.

    A label-every-year axis turns into an unreadable smear once a survey
    spans more than ~a decade (e.g. a 1950-2026 history). Thin the labelled
    ticks as the span grows and keep the in-between years as short, unlabelled
    minor ticks so the axis still reads at a glance.
    """
    if span_years <= 12:
        return 1, None
    if span_years <= 25:
        return 2, 1
    if span_years <= 60:
        return 5, 1
    return 10, 5


# ---------------------------------------------------------------------------
# Plotting — lane plot (preferred) and year-bar chart (fallback)
# ---------------------------------------------------------------------------


def _import_mpl():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except ImportError:
        print("ERROR: matplotlib not installed. pip install matplotlib", file=sys.stderr)
        sys.exit(1)


# Distinct, print-safe colours (Tableau-10 subset, in stable order)
_LANE_COLOURS = [
    "#4C72B0", "#DD8452", "#55A467", "#C44E52", "#8172B3",
    "#937860", "#DA8BC3", "#8C8C8C", "#CCB974", "#64B5CD",
]


def plot_lanes(
    papers: list[dict[str, Any]],
    assignments: dict[str, str],
    nodes: list[dict[str, Any]],
    cards: dict[str, dict[str, Any]],
    output: Path,
    *,
    title: str = "Surveyed works on the timeline",
) -> int:
    """Draw the per-paper lane plot. Returns number of plotted papers."""
    plt = _import_mpl()
    import matplotlib.dates as mdates

    # Order lanes following the taxonomy node order; only keep nodes that
    # have at least one assigned paper.
    counts: dict[str, int] = collections.Counter(assignments.values())
    lane_nodes = [n for n in nodes if counts.get(n.get("id"), 0) > 0]
    if not lane_nodes:
        return 0
    lane_index = {n["id"]: i for i, n in enumerate(lane_nodes)}

    # Layout sizing: width scales with the time range, height with the lane count.
    fig_h = max(3.0, 1.0 + 0.85 * len(lane_nodes))

    points: list[tuple[date, str, str, str]] = []  # (date, node_id, label, paper_id)
    for p in papers:
        pid = _paper_id(p)
        node = assignments.get(pid)
        d = paper_date(p)
        if not (pid and node and d and node in lane_index):
            continue
        points.append((d, node, paper_label(p, cards), pid))
    if not points:
        return 0

    points.sort(key=lambda t: t[0])
    x_min = min(t[0] for t in points)
    x_max = max(t[0] for t in points)
    span_days = max(60, (x_max - x_min).days)
    fig_w = min(14.0, max(7.0, 0.06 * span_days))

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    # Lane backgrounds — alternating subtle stripes
    for i, _ in enumerate(lane_nodes):
        if i % 2 == 0:
            ax.axhspan(i - 0.45, i + 0.45, color="#f3f3f5", zorder=0)

    # Plot points + labels per lane
    for d, node, label, pid in points:
        i = lane_index[node]
        c = _LANE_COLOURS[i % len(_LANE_COLOURS)]
        ax.plot([d], [i], marker="o", markersize=7, color=c,
                markeredgecolor="white", markeredgewidth=0.8, zorder=3)

    # Stagger labels above/below the marker to reduce collisions inside a lane.
    by_lane: dict[int, list[tuple[date, str]]] = collections.defaultdict(list)
    for d, node, label, _pid in points:
        by_lane[lane_index[node]].append((d, label))
    for i, items in by_lane.items():
        items.sort(key=lambda t: t[0])
        for k, (d, label) in enumerate(items):
            dy = 0.20 if k % 2 == 0 else -0.28
            va = "bottom" if dy > 0 else "top"
            ax.annotate(
                label,
                xy=(d, i),
                xytext=(0, 9 if dy > 0 else -9),
                textcoords="offset points",
                ha="center",
                va=va,
                fontsize=7.5,
                color="#222",
                zorder=4,
            )

    # Lane labels on the y-axis
    ax.set_yticks(range(len(lane_nodes)))
    ax.set_yticklabels([_node_label(n) for n in lane_nodes], fontsize=9)
    ax.invert_yaxis()  # first lane at the top reads better
    ax.set_ylim(len(lane_nodes) - 0.5, -0.5)

    # X-axis: tick density scales with the span. Short spans keep the
    # half-year "%b %Y" ticks; multi-year/decade spans switch to thinned
    # year-only ticks so labels never pile up.
    span_years = x_max.year - x_min.year
    if span_years <= 3:
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    else:
        major_step, minor_step = _year_tick_intervals(span_years)
        ax.xaxis.set_major_locator(mdates.YearLocator(major_step))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        if minor_step:
            ax.xaxis.set_minor_locator(mdates.YearLocator(minor_step))
            ax.tick_params(axis="x", which="minor", length=2, color="#c4c4c8")
    fig.autofmt_xdate(rotation=0, ha="center")

    ax.set_xlim(
        date(x_min.year, x_min.month, 1),
        date(x_max.year + (1 if x_max.month >= 11 else 0),
             ((x_max.month % 12) + 1) if x_max.month < 11 else 1, 1),
    )

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="x", which="both", length=3)
    ax.tick_params(axis="y", which="both", length=0)
    ax.grid(axis="x", color="#e0e0e3", linewidth=0.6, zorder=1)

    ax.set_title(title, fontsize=12, fontweight="bold", loc="left", pad=8)

    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return len(points)


def _parse_milestone_date(s: Any) -> date | None:
    """Parse 'YYYY' or 'YYYY-MM[-DD]' into a date (mid-month default)."""
    if isinstance(s, (int, float)):
        return date(int(s), 6, 15)
    if isinstance(s, str):
        m = _DATE_RE.match(s.strip())
        if m:
            y = int(m.group(1)); mo = int(m.group(2) or 6); d = int(m.group(3) or 15)
            try:
                return date(y, max(1, min(12, mo)), max(1, min(28, d)))
            except ValueError:
                return None
    return None


# Category palette for the milestone timeline — distinct, print-safe.
_MILESTONE_PALETTE = [
    "#2563eb", "#7c3aed", "#dc2626", "#16a34a",
    "#0891b2", "#d97706", "#db2777", "#475569",
]


def plot_milestones(
    milestones: list[dict[str, Any]],
    output: Path,
    *,
    title: str = "Milestones on the timeline",
    category_colors: dict[str, str] | None = None,
) -> int:
    """Reference-style single-axis milestone timeline.

    Each milestone is a coloured dot ON a central time axis with a dotted
    leader to a category-coloured label; labels alternate above/below and
    stack into levels to avoid collisions. A category legend sits at the
    bottom. ``milestones`` items: ``{label, date, category}``.
    """
    plt = _import_mpl()
    import matplotlib.dates as mdates
    from matplotlib.lines import Line2D

    items: list[tuple[date, str, str]] = []
    for m in milestones:
        d = _parse_milestone_date(m.get("date"))
        if d and m.get("label"):
            items.append((d, str(m["label"]), str(m.get("category", "Other"))))
    if not items:
        return 0
    items.sort(key=lambda t: t[0])

    cats = list(dict.fromkeys(c for _, _, c in items))
    if category_colors is None:
        category_colors = {c: _MILESTONE_PALETTE[i % len(_MILESTONE_PALETTE)]
                           for i, c in enumerate(cats)}

    x_min = min(t[0] for t in items); x_max = max(t[0] for t in items)
    span_days = max(120, (x_max - x_min).days)
    fig_w = min(15.0, max(9.0, 0.022 * span_days))

    # Assign each milestone a side (+1 above / -1 below) and a stacking level.
    # Alternate sides in date order; within a side, bump the level when the
    # previous same-side label is closer than a min separation in days.
    # A label needs horizontal room proportional to its text width; wide
    # labels close in time must stack onto separate levels. Scale the
    # minimum separation by the label length so "Position Interpolation"
    # (wide) reserves more room than "YaRN" (narrow).
    base_sep = span_days * 0.060
    last_on_side: dict[int, tuple[date, str] | None] = {1: None, -1: None}
    level_on_side = {1: 0, -1: 0}
    placed: list[tuple[date, str, str, int, int]] = []
    for k, (d, label, cat) in enumerate(items):
        side = 1 if k % 2 == 0 else -1
        prev = last_on_side[side]
        if prev is not None:
            prev_d, prev_label = prev
            need = base_sep * (1.0 + 0.045 * max(len(label), len(prev_label)))
            if (d - prev_d).days <= need:
                level_on_side[side] += 1
            else:
                level_on_side[side] = 0
        placed.append((d, label, cat, side, level_on_side[side]))
        last_on_side[side] = (d, label)

    max_level = max((lv for *_, lv in placed), default=0)
    fig_h = 2.4 + 0.62 * (max_level + 1)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    # Central timeline
    ax.axhline(0, color="#222", linewidth=2.2, zorder=2)

    step = 0.82  # vertical spacing per level
    for d, label, cat, side, lv in placed:
        c = category_colors.get(cat, "#475569")
        y_label = side * (0.95 + step * lv)
        ax.plot([d], [0], marker="o", markersize=8, color=c,
                markeredgecolor="white", markeredgewidth=1.0, zorder=4)
        ax.plot([d, d], [0, y_label - side * 0.18], linestyle=":",
                color=c, linewidth=1.0, zorder=1)
        ax.annotate(
            label, xy=(d, y_label), ha="center",
            va="bottom" if side > 0 else "top",
            fontsize=8.2, fontweight="bold", color=c, zorder=5,
        )

    ax.set_ylim(-(1.1 + step * (max_level + 1)), (1.1 + step * (max_level + 1)))
    ax.set_yticks([])
    for sp in ("top", "left", "right"):
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_visible(False)

    # Year ticks just under the axis — thinned for long spans so a
    # multi-decade history doesn't collapse into a wall of labels.
    major_step, minor_step = _year_tick_intervals(x_max.year - x_min.year)
    ax.xaxis.set_major_locator(mdates.YearLocator(major_step))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.tick_params(axis="x", which="major", length=0, labelsize=10, pad=6)
    if minor_step:
        ax.xaxis.set_minor_locator(mdates.YearLocator(minor_step))
        ax.tick_params(axis="x", which="minor", length=4, color="#bbb")
    ax.set_xlim(date(x_min.year, 1, 1),
                date(x_max.year + 1, 1, 1))

    handles = [Line2D([0], [0], marker="o", linestyle="", markersize=8,
                      markeredgecolor="white", color=category_colors.get(c, "#475569"),
                      label=c) for c in cats]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.02),
              ncol=min(len(cats), 6), frameon=False, fontsize=8.5,
              handletextpad=0.3, columnspacing=1.2)

    ax.set_title(title, fontsize=12.5, fontweight="bold", pad=10)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return len(placed)


def plot_year_bars(years: list[int], output: Path, *, title: str) -> int:
    plt = _import_mpl()
    if not years:
        return 0
    counts = collections.Counter(years)
    y_min, y_max = min(counts), max(counts)
    all_years = list(range(y_min, y_max + 1))
    values = [counts.get(y, 0) for y in all_years]

    fig, ax = plt.subplots(figsize=(min(16.0, max(7, len(all_years) * 0.5)), 3.4))
    bars = ax.bar(all_years, values, color="#4C72B0", edgecolor="white", linewidth=0.5)
    for bar, val in zip(bars, values):
        if val > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                    str(val), ha="center", va="bottom", fontsize=8)
    ax.set_xlabel("Year")
    ax.set_ylabel("Papers")
    ax.set_title(title, fontsize=12, fontweight="bold", loc="left", pad=8)
    # Label every Nth year so a long span doesn't overprint its axis.
    major_step, _ = _year_tick_intervals(y_max - y_min)
    ax.set_xticks([y for y in all_years if y % major_step == 0] or all_years)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return sum(values)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _resolve_run_dir(arg: Path) -> Path | None:
    """Return run_dir if `arg` is a survey-run directory, else None."""
    if arg.is_dir() and (arg / "1_search/filtered.jsonl").exists():
        return arg
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("input", type=Path,
                   help="A run directory (preferred) or a bare filtered.jsonl path")
    p.add_argument("--output", "-o", type=Path, default=None,
                   help="Output PDF/PNG path. Defaults to <run_dir>/5_paper/figures/01_timeline.pdf "
                        "or alongside the input jsonl.")
    p.add_argument("--title", default="Surveyed works on the timeline",
                   help="Figure title.")
    p.add_argument("--force-bars", action="store_true",
                   help="Force the year-bar chart even when a run dir has clusters.")
    p.add_argument("--milestones", type=Path, default=None,
                   help="JSON file of curated milestones for the reference-style "
                        "single-axis timeline. Either a list of "
                        "{label,date,category} or {milestones:[...],title:...,"
                        "category_colors:{...}}. Takes precedence over lane/bar modes.")
    args = p.parse_args()

    run_dir = _resolve_run_dir(args.input)

    # Resolve output path
    if args.output is None:
        if run_dir:
            args.output = run_dir / "5_paper/figures/01_timeline.pdf"
        else:
            args.output = args.input.with_name("01_timeline.pdf")

    # Milestone mode — preferred when a curated list is provided (or present
    # at the conventional run-dir path).
    ms_path = args.milestones
    if ms_path is None and run_dir:
        cand = run_dir / "1_search/timeline_milestones.json"
        ms_path = cand if cand.exists() else None
    if ms_path and ms_path.exists():
        doc = json.loads(ms_path.read_text())
        if isinstance(doc, list):
            milestones, cat_colors, ms_title = doc, None, args.title
        else:
            milestones = doc.get("milestones", [])
            cat_colors = doc.get("category_colors")
            ms_title = doc.get("title", args.title)
        n = plot_milestones(milestones, args.output, title=ms_title,
                            category_colors=cat_colors)
        if n > 0:
            print(f"Timeline (milestones) saved → {args.output}  ({n} works)",
                  file=sys.stderr)
            return 0
        print("No plottable milestones; falling back.", file=sys.stderr)

    if run_dir and not args.force_bars:
        papers = load_papers(run_dir / "1_search/filtered.jsonl")
        assignments = load_clusters(run_dir / "2_cluster/clusters.json")
        nodes = load_taxonomy(run_dir / "3_taxonomy.json")
        cards = load_cards(run_dir / "1_search/cards.jsonl")

        if assignments and nodes:
            n = plot_lanes(papers, assignments, nodes, cards, args.output, title=args.title)
            if n > 0:
                print(f"Timeline (lane plot) saved → {args.output}  "
                      f"({n} papers across {len({assignments[p['paper_id'] if 'paper_id' in p else p.get('cite_key')] for p in papers if assignments.get(_paper_id(p))})} lanes)",
                      file=sys.stderr)
                return 0
            print("No plottable papers (missing dates or cluster assignments); "
                  "falling back to year-bar chart.", file=sys.stderr)

    # Fallback: aggregated year-bar chart
    jsonl_path = (run_dir / "1_search/filtered.jsonl") if run_dir else args.input
    years = [int(p["year"]) for p in load_papers(jsonl_path)
             if isinstance(p.get("year"), (int, float))
             and 1990 <= int(p["year"]) <= 2030]
    if len(years) < 3:
        print(f"WARN: only {len(years)} year values found — skipping timeline",
              file=sys.stderr)
        return 0
    n = plot_year_bars(years, args.output, title=args.title)
    print(f"Timeline (year bars) saved → {args.output}  ({n} papers)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
