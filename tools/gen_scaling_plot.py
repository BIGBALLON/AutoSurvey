#!/usr/bin/env python3
"""gen_scaling_plot.py — Generate a scaling-trend scatter from cards.jsonl + papers.jsonl.

Produces a matplotlib scatter (year × value, log-y) coloured by region, with
frontier-point annotations and an overlaid log-linear trend line.

Usage:
    python3 gen_scaling_plot.py --cards cards.jsonl --papers filtered.jsonl \
                                --output figures/02_scaling_trend.pdf \
                                [--metric params|tokens|flops] \
                                [--affiliation-map tools/affiliation_to_region.json] \
                                [--width 10] [--height 6] [--verbose]

Requires matplotlib + numpy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def load_cards(path: str) -> Dict[str, dict]:
    """Load cards.jsonl into a dict keyed by cite_key."""
    out: Dict[str, dict] = {}
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[WARN] {path}:{lineno} JSON decode failed: {exc.msg}",
                      file=sys.stderr)
                continue
            key = rec.get("cite_key")
            if key:
                out[key] = rec
    return out


def load_papers(path: str) -> Dict[str, dict]:
    """Load papers.jsonl / filtered.jsonl into a dict keyed by cite_key."""
    out: Dict[str, dict] = {}
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[WARN] {path}:{lineno} JSON decode failed: {exc.msg}",
                      file=sys.stderr)
                continue
            key = rec.get("cite_key")
            if key:
                out[key] = rec
    return out


# ---------------------------------------------------------------------------
# Affiliation → region
# ---------------------------------------------------------------------------


def match_affiliation_to_region(affiliation: str, lookup: Dict[str, str]) -> str:
    """Case-insensitive substring match; longest matching key wins.

    Returns "Unknown" if no key in ``lookup`` is a substring of ``affiliation``.
    """
    if not affiliation:
        return "Unknown"
    aff_lc = affiliation.lower()
    best_key = ""
    best_region = "Unknown"
    for key, region in lookup.items():
        key_lc = key.lower()
        if key_lc and key_lc in aff_lc and len(key_lc) > len(best_key):
            best_key = key_lc
            best_region = region
    return best_region


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------


_METRIC_FIELDS = {
    "params": "total_params",
    "tokens": "training_tokens",
    "flops": "total_compute_flops",
}

_METRIC_LABELS = {
    "params": "Total parameters",
    "tokens": "Training tokens",
    "flops": "Compute (FLOPs)",
}


def extract_metric_value(card: dict, metric: str) -> Optional[float]:
    """Pull the requested metric from a card; return None if missing/unreported."""
    field = _METRIC_FIELDS.get(metric)
    if not field:
        return None
    scale = (card or {}).get("extraction", {}).get("scale", {}) or {}
    val = scale.get(field)
    if val is None:
        return None
    if isinstance(val, str):
        if val.strip().upper() in {"N/R", "N/A", ""}:
            return None
        try:
            val = float(val)
        except ValueError:
            return None
    if not isinstance(val, (int, float)):
        return None
    if val <= 0:
        return None
    return float(val)


# ---------------------------------------------------------------------------
# Frontier + trend fit
# ---------------------------------------------------------------------------


def find_frontier_points(records: List[Tuple[int, float, str]]) -> List[Tuple[int, float, str]]:
    """Return one record per year with the maximum value (the frontier).

    Each record is ``(year, value, name)``. Output is sorted by year ascending.
    """
    best_per_year: Dict[int, Tuple[int, float, str]] = {}
    for rec in records:
        year, value, name = rec
        cur = best_per_year.get(year)
        if cur is None or value > cur[1]:
            best_per_year[year] = (year, value, name)
    return sorted(best_per_year.values(), key=lambda r: r[0])


def fit_log_trend(records: List[Tuple[int, float, str]]) -> Tuple[float, float]:
    """Linear fit on (year, log10(value)). Returns (slope, intercept).

    Raises ValueError when fewer than two valid points are supplied.
    """
    import numpy as np

    pairs = [(y, v) for (y, v, _n) in records if v and v > 0]
    if len(pairs) < 2:
        raise ValueError("need at least 2 points for a log-linear fit")
    years = np.array([p[0] for p in pairs], dtype=float)
    logs = np.log10(np.array([p[1] for p in pairs], dtype=float))
    slope, intercept = np.polyfit(years, logs, 1)
    return float(slope), float(intercept)


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------


_REGION_COLORS = {
    "US": "#2E5EAA",
    "CN": "#C8412B",
    "EU": "#2E8B57",
    "UK": "#7E4FBF",
    "Other": "#7F7F7F",
    "Unknown": "#BDBDBD",
}

_REGION_ORDER = ["US", "CN", "EU", "UK", "Other", "Unknown"]


def _color_for_region(region: str) -> str:
    return _REGION_COLORS.get(region, _REGION_COLORS["Other"])


def plot_scaling(
    records: List[Tuple[int, float, str, str]],
    metric: str,
    output_path: str,
    width: float = 10.0,
    height: float = 6.0,
) -> None:
    """Render a scaling-trend scatter to ``output_path``.

    ``records`` is a list of ``(year, value, name, region)``.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("ERROR: matplotlib not installed. pip install matplotlib", file=sys.stderr)
        sys.exit(1)

    if not records:
        print("WARN: no scaling records — plot not generated", file=sys.stderr)
        return

    fig, ax = plt.subplots(figsize=(width, height))

    # Scatter, grouped by region so the legend stays clean.
    regions_seen = set()
    by_region: Dict[str, List[Tuple[int, float, str]]] = {}
    for year, value, name, region in records:
        by_region.setdefault(region, []).append((year, value, name))
        regions_seen.add(region)

    for region in _REGION_ORDER:
        if region not in by_region:
            continue
        pts = by_region[region]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.scatter(
            xs,
            ys,
            s=42,
            c=_color_for_region(region),
            edgecolors="white",
            linewidths=0.5,
            alpha=0.85,
            label=region,
        )

    # Any region not in our predefined order (defensive).
    for region in sorted(regions_seen - set(_REGION_ORDER)):
        pts = by_region[region]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.scatter(xs, ys, s=42, c=_color_for_region(region), alpha=0.85, label=region)

    # Frontier annotations.
    frontier = find_frontier_points([(y, v, n) for (y, v, n, _r) in records])
    for year, value, name in frontier:
        ax.annotate(
            name,
            xy=(year, value),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=7,
            color="#222222",
        )

    # Log-linear trend overlay.
    plain = [(y, v, n) for (y, v, n, _r) in records]
    try:
        slope, intercept = fit_log_trend(plain)
        import numpy as np

        years_sorted = sorted({y for (y, _v, _n, _r) in records})
        xs = np.array([years_sorted[0], years_sorted[-1]], dtype=float)
        ys = 10 ** (slope * xs + intercept)
        ax.plot(xs, ys, color="#444444", linestyle="--", linewidth=1.2,
                label=f"log-linear trend (slope={slope:.2f}/yr)")
    except ValueError:
        pass

    ax.set_yscale("log")
    ax.set_xlabel("Year", fontsize=11)
    ax.set_ylabel(_METRIC_LABELS.get(metric, metric), fontsize=11)
    ax.set_title(f"Scaling Trend: {metric} over time, by region", fontsize=13, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, which="both", linestyle=":", linewidth=0.4, alpha=0.5)
    ax.legend(loc="best", fontsize=8, frameon=False)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Scaling plot saved → {output_path}  ({len(records)} points)", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_affiliation_map() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "affiliation_to_region.json")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a scaling-trend scatter (params/tokens/flops × time × region)."
    )
    parser.add_argument("--cards", required=True, help="Path to cards.jsonl")
    parser.add_argument("--papers", required=True, help="Path to papers/filtered.jsonl")
    parser.add_argument("--output", required=True, help="Output PDF path")
    parser.add_argument("--metric", default="params", choices=["params", "tokens", "flops"])
    parser.add_argument("--affiliation-map", default=_default_affiliation_map(),
                        help="Path to affiliation→region JSON (default: tools/affiliation_to_region.json)")
    parser.add_argument("--width", type=float, default=10.0)
    parser.add_argument("--height", type=float, default=6.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # Fail-fast with actionable messages on missing inputs (callers usually
    # forget to run /survey-write to populate cards.jsonl on early runs).
    for label, p in (("--cards", args.cards), ("--papers", args.papers),
                     ("--affiliation-map", args.affiliation_map)):
        if not Path(p).exists():
            hint = ""
            if label == "--cards":
                hint = " (run /survey-write first to populate 1_search/cards.jsonl)"
            print(f"ERROR: {label} not found: {p}{hint}", file=sys.stderr)
            return 2

    cards = load_cards(args.cards)
    papers = load_papers(args.papers)
    with open(args.affiliation_map) as f:
        lookup = json.load(f)

    records: List[Tuple[int, float, str, str]] = []
    skipped_no_year = skipped_no_value = 0
    for cite_key, card in cards.items():
        paper = papers.get(cite_key, {})
        year = paper.get("year")
        try:
            year = int(year) if year is not None else None
        except (TypeError, ValueError):
            year = None
        if year is None:
            skipped_no_year += 1
            continue
        value = extract_metric_value(card, args.metric)
        if value is None:
            skipped_no_value += 1
            continue
        affiliation = paper.get("affiliation") or ""
        region = match_affiliation_to_region(affiliation, lookup)
        name = card.get("title") or paper.get("title") or cite_key
        # Truncate long titles for annotation readability.
        if len(name) > 32:
            name = name[:30] + "…"
        records.append((year, value, name, region))

    if args.verbose:
        print(
            f"[gen_scaling_plot] cards={len(cards)} papers={len(papers)} "
            f"records={len(records)} skipped_no_year={skipped_no_year} "
            f"skipped_no_value={skipped_no_value}",
            file=sys.stderr,
        )

    # If we filtered everything out, fail loudly with a diagnosis. A
    # silent return here previously produced the confusing "WARN: no
    # scaling records" line followed by exit 0, leaving the caller
    # with no .pdf and no actionable signal.
    if not records:
        diagnosis = (
            f"  cards={len(cards)} papers={len(papers)} records=0 "
            f"(skipped_no_year={skipped_no_year}, "
            f"skipped_no_value={skipped_no_value})"
        )
        if not cards:
            hint = "cards.jsonl is empty — run /survey-write first"
        elif skipped_no_value == len(cards):
            hint = (
                f"every card lacked a usable {args.metric!r} value; "
                "check cards[].extraction.architecture / .pretraining_recipe "
                "or pick a different --metric"
            )
        elif skipped_no_year == len(cards):
            hint = (
                "every paper lacked a 'year' field in filtered.jsonl; "
                "/survey-search should populate this"
            )
        else:
            hint = (
                "no card-paper pair had BOTH a year AND a usable value; "
                "verify cards.jsonl cite_keys match filtered.jsonl"
            )
        print(f"ERROR: no plottable records\n{diagnosis}\n  hint: {hint}",
              file=sys.stderr)
        return 3

    plot_scaling(records, args.metric, args.output, width=args.width, height=args.height)
    return 0


if __name__ == "__main__":
    sys.exit(main())
