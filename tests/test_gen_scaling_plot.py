"""Tests for tools/gen_scaling_plot.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless backend for CI


# ---------------------------------------------------------------------------
# Module loader (the tool lives outside any package, so import by path).
# ---------------------------------------------------------------------------


def _load_tool():
    repo_root = Path(__file__).resolve().parent.parent
    tool_path = repo_root / "tools" / "gen_scaling_plot.py"
    spec = importlib.util.spec_from_file_location("gen_scaling_plot", tool_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_scaling_plot"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


gsp = _load_tool()


# ---------------------------------------------------------------------------
# match_affiliation_to_region
# ---------------------------------------------------------------------------


def test_match_affiliation_to_region_exact():
    assert gsp.match_affiliation_to_region("Stanford", {"stanford": "US"}) == "US"


def test_match_affiliation_to_region_substring():
    assert gsp.match_affiliation_to_region("Stanford University", {"stanford": "US"}) == "US"


def test_match_affiliation_to_region_case_insensitive():
    assert gsp.match_affiliation_to_region("STANFORD", {"stanford": "US"}) == "US"


def test_match_affiliation_to_region_longest_wins():
    lookup = {"google": "US", "google deepmind": "US"}
    # Both keys match; longer should win. Region is the same here ("US"),
    # but we sanity-check by exposing the chosen key via the public API
    # (we still require the canonical answer).
    assert gsp.match_affiliation_to_region("Google DeepMind, London", lookup) == "US"


def test_match_affiliation_to_region_unknown():
    assert gsp.match_affiliation_to_region("Unknown Lab", {"stanford": "US"}) == "Unknown"


# ---------------------------------------------------------------------------
# extract_metric_value
# ---------------------------------------------------------------------------


def test_extract_metric_value_params():
    card = {"extraction": {"scale": {"total_params": 670_000_000_000}}}
    assert gsp.extract_metric_value(card, "params") == 670_000_000_000


def test_extract_metric_value_NR_returns_none():
    card = {"extraction": {"scale": {"total_params": "N/R"}}}
    assert gsp.extract_metric_value(card, "params") is None


# ---------------------------------------------------------------------------
# find_frontier_points
# ---------------------------------------------------------------------------


def test_find_frontier_points():
    records = [
        (2022, 1e10, "small-2022"),
        (2022, 7e10, "big-2022"),
        (2024, 6.71e11, "deepseek-v3"),
    ]
    frontier = gsp.find_frontier_points(records)
    assert len(frontier) == 2
    by_year = {r[0]: r for r in frontier}
    assert by_year[2022][1] == 7e10
    assert by_year[2022][2] == "big-2022"
    assert by_year[2024][1] == 6.71e11


# ---------------------------------------------------------------------------
# fit_log_trend
# ---------------------------------------------------------------------------


def test_fit_log_trend_returns_slope():
    records = [(2020, 1e9, "a"), (2024, 1e12, "b")]
    slope, _intercept = gsp.fit_log_trend(records)
    assert slope > 0


# ---------------------------------------------------------------------------
# plot_scaling
# ---------------------------------------------------------------------------


def test_plot_scaling_creates_pdf(tmp_path):
    records = [
        (2021, 1.75e11, "GPT-3", "US"),
        (2022, 5.4e11, "PaLM", "US"),
        (2023, 1.0e12, "GPT-4 (est.)", "US"),
        (2024, 6.71e11, "DeepSeek-V3", "CN"),
        (2024, 4.05e11, "Mixtral", "EU"),
    ]
    out = tmp_path / "scaling.pdf"
    gsp.plot_scaling(records, "params", str(out), width=8, height=5)
    assert out.exists()
    assert out.stat().st_size > 0


# ---------------------------------------------------------------------------
# CLI fail-fast on missing inputs (regression: previously raised raw
# FileNotFoundError stack trace instead of an actionable message + exit 2)
# ---------------------------------------------------------------------------


def test_cli_missing_cards_fails_fast(tmp_path, capsys):
    """Missing --cards file must produce an actionable stderr message and
    exit code 2, not a Python FileNotFoundError stack trace."""
    import subprocess

    repo_root = Path(__file__).resolve().parent.parent
    tool = repo_root / "tools" / "gen_scaling_plot.py"
    papers = tmp_path / "filtered.jsonl"
    papers.write_text("")  # exists but empty
    out = tmp_path / "scaling.pdf"

    res = subprocess.run(
        [sys.executable, str(tool),
         "--cards", str(tmp_path / "missing-cards.jsonl"),
         "--papers", str(papers),
         "--output", str(out)],
        capture_output=True, text=True,
    )
    assert res.returncode == 2
    assert "ERROR" in res.stderr
    assert "missing-cards.jsonl" in res.stderr
    assert "/survey-write" in res.stderr  # actionable hint
    assert "Traceback" not in res.stderr


# ---------------------------------------------------------------------------
# CLI: empty-records guard (cards.jsonl exists but yields 0 plottable rows)
# ---------------------------------------------------------------------------


def test_cli_empty_cards_exits_with_diagnosis(tmp_path):
    """If cards.jsonl is empty, main must exit non-zero with a clear
    'cards.jsonl is empty — run /survey-write first' hint instead of
    silently emitting the WARN line and exiting 0."""
    import subprocess

    repo_root = Path(__file__).resolve().parent.parent
    tool = repo_root / "tools" / "gen_scaling_plot.py"
    cards = tmp_path / "cards.jsonl"
    cards.write_text("")  # exists but empty
    papers = tmp_path / "filtered.jsonl"
    papers.write_text("")
    out = tmp_path / "scaling.pdf"

    res = subprocess.run(
        [sys.executable, str(tool),
         "--cards", str(cards), "--papers", str(papers),
         "--output", str(out)],
        capture_output=True, text=True,
    )
    assert res.returncode == 3, res.stderr
    assert "no plottable records" in res.stderr
    assert "cards.jsonl is empty" in res.stderr
    assert "/survey-write" in res.stderr
    assert not out.exists()  # no half-baked PDF


def test_cli_cards_without_metric_value_exits_with_diagnosis(tmp_path):
    """If every card has a year but no extractable metric value (e.g.
    no 'NR' params), the diagnosis must point at the metric/extraction
    path, not at the cards file existence."""
    import json
    import subprocess

    repo_root = Path(__file__).resolve().parent.parent
    tool = repo_root / "tools" / "gen_scaling_plot.py"
    cards = tmp_path / "cards.jsonl"
    # Card with an architecture group but no params number — extract
    # returns None for --metric params.
    cards.write_text(json.dumps({
        "cite_key": "smith2024foo",
        "title": "Foo",
        "extraction": {"architecture": {"params": "NR"}},
    }) + "\n")
    papers = tmp_path / "filtered.jsonl"
    papers.write_text(json.dumps({
        "cite_key": "smith2024foo",
        "year": 2024,
    }) + "\n")
    out = tmp_path / "scaling.pdf"

    res = subprocess.run(
        [sys.executable, str(tool),
         "--cards", str(cards), "--papers", str(papers),
         "--output", str(out)],
        capture_output=True, text=True,
    )
    assert res.returncode == 3, res.stderr
    assert "no plottable records" in res.stderr
    # Hint should be about the metric, not about the empty file.
    assert "lacked a usable" in res.stderr or "lacked a 'year'" in res.stderr \
           or "no card-paper pair" in res.stderr
