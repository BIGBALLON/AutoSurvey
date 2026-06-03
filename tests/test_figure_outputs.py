"""Regression tests for figure generators that were silently emitting empty
output when fed canonical-shaped clusters / outline / taxonomy data:

  - tools/gen_taxonomy_tikz.py  used to read `clusters.node_counts` only;
    now must derive counts from `clusters.assignments` (canonical) or the
    inverted `{node:[pids]}` shape.

  - tools/gen_timeline.py  used to emit only a year-aggregate bar chart; the
    new lane-plot mode must produce one lane per taxonomy node when a run
    directory carries clusters.json. The bar-chart fallback must still work
    when clusters are absent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
GEN_TAXONOMY = TOOLS / "gen_taxonomy_tikz.py"
GEN_TIMELINE = TOOLS / "gen_timeline.py"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _write_taxonomy(run_dir: Path) -> None:
    (run_dir / "3_taxonomy.json").write_text(json.dumps({
        "name": "test",
        "nodes": [
            {"id": "arch",    "title": "Architectures",
             "description": "Dense vs MoE designs."},
            {"id": "objective", "title": "Objectives",
             "description": "Multi-token prediction and friends."},
            {"id": "scaling", "title": "Scaling",
             "description": "Empirical and theoretical scaling laws."},
        ],
    }))


def _write_brief_topic(run_dir: Path, topic: str = "Test Survey") -> None:
    (run_dir / "brief.parsed.json").write_text(json.dumps({"topic": topic}))


def _write_filtered(run_dir: Path) -> None:
    search = run_dir / "1_search"
    search.mkdir(parents=True, exist_ok=True)
    papers = [
        {"paper_id": "p1", "title": "Mixtral",     "year": 2024, "published": "2024-01-08"},
        {"paper_id": "p2", "title": "DeepSeek-V3", "year": 2024, "published": "2024-12-26"},
        {"paper_id": "p3", "title": "OLMoE",       "year": 2024, "published": "2024-09-03"},
        {"paper_id": "p4", "title": "Meta MTP",    "year": 2024, "published": "2024-04-30"},
        {"paper_id": "p5", "title": "Leap MTP",    "year": 2025, "published": "2025-05-26"},
        {"paper_id": "p6", "title": "LR-anneal SL","year": 2024, "published": "2024-08-21"},
    ]
    (search / "filtered.jsonl").write_text(
        "\n".join(json.dumps(p) for p in papers) + "\n"
    )


def _write_clusters_canonical(run_dir: Path) -> None:
    cluster_dir = run_dir / "2_cluster"
    cluster_dir.mkdir(parents=True, exist_ok=True)
    (cluster_dir / "clusters.json").write_text(json.dumps({
        "assignments": {
            "p1": "arch", "p2": "arch", "p3": "arch",
            "p4": "objective", "p5": "objective",
            "p6": "scaling",
        },
    }))


def _write_clusters_inverted(run_dir: Path) -> None:
    cluster_dir = run_dir / "2_cluster"
    cluster_dir.mkdir(parents=True, exist_ok=True)
    (cluster_dir / "clusters.json").write_text(json.dumps({
        "arch":      ["p1", "p2", "p3"],
        "objective": ["p4", "p5"],
        "scaling":   ["p6"],
    }))


def _write_clusters_flat(run_dir: Path) -> None:
    """Alternative cluster shape: bare {paper_id: node_id} dict."""
    cluster_dir = run_dir / "2_cluster"
    cluster_dir.mkdir(parents=True, exist_ok=True)
    (cluster_dir / "clusters.json").write_text(json.dumps({
        "p1": "arch", "p2": "arch", "p3": "arch",
        "p4": "objective", "p5": "objective",
        "p6": "scaling",
    }))


# ---------------------------------------------------------------------------
# gen_taxonomy_tikz.py
# ---------------------------------------------------------------------------


def _run_taxonomy(run_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GEN_TAXONOMY), str(run_dir), "--layout", "tree"],
        capture_output=True, text=True,
    )


def test_taxonomy_canonical_assignments(tmp_path: Path) -> None:
    """clusters.assignments → all 3 nodes must render with their paper counts."""
    _write_taxonomy(tmp_path)
    _write_brief_topic(tmp_path, topic="LLM Pretraining")
    _write_filtered(tmp_path)
    _write_clusters_canonical(tmp_path)

    res = _run_taxonomy(tmp_path)
    assert res.returncode == 0, f"stderr:\n{res.stderr}"

    tex = (tmp_path / "5_paper/figures/00_taxonomy.tex").read_text()
    # All 3 nodes must appear; each must show its paper count.
    assert "Architectures" in tex
    assert "Objectives" in tex
    assert "Scaling" in tex
    assert "(3)" in tex   # arch has 3
    assert "(2)" in tex   # objective has 2
    assert "(1)" in tex   # scaling has 1
    # No "0 nodes shown" — that's the bug regression we're guarding against.
    assert "0 nodes shown" not in tex
    assert "0~nodes shown" not in tex
    # Topic should pick up from brief.parsed.json (not the literal "Survey")
    assert "LLM Pretraining" in tex or "Pretraining" in tex


def test_taxonomy_inverted_clusters(tmp_path: Path) -> None:
    """{node:[pids]} alias → identical effective output."""
    _write_taxonomy(tmp_path)
    _write_brief_topic(tmp_path)
    _write_filtered(tmp_path)
    _write_clusters_inverted(tmp_path)

    res = _run_taxonomy(tmp_path)
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    tex = (tmp_path / "5_paper/figures/00_taxonomy.tex").read_text()
    assert "(3)" in tex and "(2)" in tex and "(1)" in tex
    assert "0 nodes shown" not in tex


def test_taxonomy_flat_clusters(tmp_path: Path) -> None:
    """The flat {paper_id: node_id} dict is the third tolerated cluster shape."""
    _write_taxonomy(tmp_path)
    _write_brief_topic(tmp_path)
    _write_filtered(tmp_path)
    _write_clusters_flat(tmp_path)

    res = _run_taxonomy(tmp_path)
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    tex = (tmp_path / "5_paper/figures/00_taxonomy.tex").read_text()
    assert "(3)" in tex and "(2)" in tex and "(1)" in tex
    assert "0 nodes shown" not in tex


# ---------------------------------------------------------------------------
# gen_timeline.py
# ---------------------------------------------------------------------------


def _run_timeline(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GEN_TIMELINE), *args],
        capture_output=True, text=True,
    )


def _has_pdf(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 1024 and path.read_bytes()[:4] == b"%PDF"


def test_timeline_lane_plot_with_clusters(tmp_path: Path) -> None:
    """Run-dir invocation with clusters.json must emit the lane-plot mode."""
    _write_taxonomy(tmp_path)
    _write_filtered(tmp_path)
    _write_clusters_canonical(tmp_path)

    out = tmp_path / "5_paper/figures/01_timeline.pdf"
    res = _run_timeline([str(tmp_path), "--output", str(out)])
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    assert _has_pdf(out), "lane-plot PDF not produced"
    assert "lane plot" in res.stderr.lower(), \
        f"expected lane-plot mode, got: {res.stderr!r}"


def test_timeline_lane_plot_with_flat_clusters(tmp_path: Path) -> None:
    """The flat {paper_id: node_id} clusters.json shape must still produce a lane plot."""
    _write_taxonomy(tmp_path)
    _write_filtered(tmp_path)
    _write_clusters_flat(tmp_path)

    out = tmp_path / "5_paper/figures/01_timeline.pdf"
    res = _run_timeline([str(tmp_path), "--output", str(out)])
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    assert _has_pdf(out), "lane-plot PDF not produced from flat clusters"
    assert "lane plot" in res.stderr.lower()


def test_timeline_falls_back_to_year_bars(tmp_path: Path) -> None:
    """Run-dir without clusters.json → fall back to year-bar chart."""
    _write_filtered(tmp_path)   # NOTE: no clusters.json

    out = tmp_path / "01_timeline.pdf"
    res = _run_timeline([str(tmp_path), "--output", str(out)])
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    assert _has_pdf(out), "fallback bar PDF not produced"
    assert "year bar" in res.stderr.lower() or "bars" in res.stderr.lower(), \
        f"expected fallback mode, got: {res.stderr!r}"


def test_timeline_bare_jsonl_invocation(tmp_path: Path) -> None:
    """Bare filtered.jsonl path keeps working (no run-dir context)."""
    _write_filtered(tmp_path)
    out = tmp_path / "01_timeline.pdf"
    res = _run_timeline([
        str(tmp_path / "1_search/filtered.jsonl"),
        "--output", str(out),
    ])
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    assert _has_pdf(out), "year-bar PDF not produced"


# ---------------------------------------------------------------------------
# render_matrix (layout=matrix)
# ---------------------------------------------------------------------------


def test_taxonomy_matrix_layout_renders_tier_axis(survey_run_dir):
    """`gen_taxonomy_tikz.py --layout matrix` must produce a tikzpicture with
    one row per tier_axis.tier and one column per feature_columns + Key Insight.
    """
    res = subprocess.run(
        [sys.executable, str(GEN_TAXONOMY), str(survey_run_dir),
         "--layout", "matrix"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, f"stderr:\n{res.stderr}"

    tex = (survey_run_dir / "5_paper" / "figures" / "00_taxonomy.tex").read_text()
    # Header note shows the matrix mode was actually used (not silent fallback)
    assert "matrix" in tex.lower()
    assert r"\begin{figure*}" in tex   # matrix uses figure* (full width)
    # Each tier id appears as a row label
    for tier_id in ("T1", "T2", "T3"):
        assert tier_id in tex, f"missing tier {tier_id}"
    # Each feature column is a header
    assert "Architecture" in tex
    assert "Data Scale" in tex
    assert "Compute" in tex
    # Some cells render
    assert "Dense" in tex
    assert "1.4T tok" in tex
    # Key Insight appears in a styled footer
    assert "Key Insight" in tex
    assert "token-budget axis matters" in tex


def test_taxonomy_matrix_falls_back_when_tier_axis_missing(tmp_path):
    """Without outline.json carrying tier_axis, --layout matrix prints a
    WARN and falls back to the tree layout (does not crash)."""
    # Use the basic fixtures (taxonomy + clusters but no outline.tier_axis)
    _write_taxonomy(tmp_path)
    _write_filtered(tmp_path)
    _write_clusters_canonical(tmp_path)

    res = subprocess.run(
        [sys.executable, str(GEN_TAXONOMY), str(tmp_path), "--layout", "matrix"],
        capture_output=True, text=True,
    )
    # Either fallback (rc=0 + warn) OR matrix-rendered with empty cells
    # Spec: "WARN: --layout matrix requires .../outline.json" → falls back to tree
    assert res.returncode == 0
    assert "WARN" in res.stderr or "tree" in res.stdout.lower()


def test_taxonomy_matrix_renders_partial_cells(survey_run_dir):
    """A tier with `[ ]` for a feature must render an em-dash, not crash."""
    # Wipe one cell in the fixture to force the dash branch
    outline_path = survey_run_dir / "4_outline" / "outline.json"
    doc = json.loads(outline_path.read_text())
    doc["tier_axis"]["cells"]["T2"]["Compute"] = []
    outline_path.write_text(json.dumps(doc, indent=2))

    res = subprocess.run(
        [sys.executable, str(GEN_TAXONOMY), str(survey_run_dir),
         "--layout", "matrix"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    tex = (survey_run_dir / "5_paper" / "figures" / "00_taxonomy.tex").read_text()
    # Em-dash is the empty-cell glyph
    assert "—" in tex


# ---------------------------------------------------------------------------
# latex_escape coverage hardening
# ---------------------------------------------------------------------------


def _latex_escape():
    sys.path.insert(0, str(REPO / "tools"))
    import gen_taxonomy_tikz
    return gen_taxonomy_tikz.latex_escape


def test_latex_escape_covers_all_10_metachars():
    """All 10 LaTeX metacharacters must be rendered as a safe form so a
    topic/tier label/feature column with these chars cannot break tikz."""
    le = _latex_escape()
    assert le("a$b{c}") == r"a\$b\{c\}"
    assert le("Foo & Bar") == r"Foo \& Bar"
    assert le(r"a\b") == r"a\textbackslash{}b"
    assert le("a^b~c") == r"a\textasciicircum{}b\textasciitilde{}c"
    assert le("a_b%c#d") == r"a\_b\%c\#d"


def test_latex_escape_handles_none_and_empty():
    le = _latex_escape()
    assert le(None) == ""
    assert le("") == ""


def test_latex_escape_does_not_double_escape_backslash():
    """The naive (\\ -> \\textbackslash{}) replacement BEFORE ({ -> \\{) and
    (} -> \\}) double-escapes the introduced braces. Sentinel-based impl
    must not regress."""
    le = _latex_escape()
    out = le(r"a\b")
    assert out == r"a\textbackslash{}b"
    assert r"\textbackslash\{\}" not in out


def test_latex_escape_preserves_unicode_normalisations():
    """Em/en-dashes, ellipsis, smart quotes still convert."""
    le = _latex_escape()
    assert "---" in le("a—b")
    assert "--" in le("a–b")
    assert r"\dots" in le("a…b")
    out = le("“hi”")
    assert "``" in out and "''" in out


def test_taxonomy_matrix_sanitizes_special_chars(survey_run_dir):
    """End-to-end: a brief topic with $ & {} must not break tikz rendering."""
    # Pollute the topic in BOTH state.json and brief.parsed.json — gen_taxonomy
    # reads state.json first then falls back to brief.parsed.json.
    state_p = survey_run_dir / "state.json"
    state = json.loads(state_p.read_text())
    state["topic"] = "Foo $X^2$ & {Bar} #1"
    state_p.write_text(json.dumps(state, indent=2))

    brief_p = survey_run_dir / "brief.parsed.json"
    brief = json.loads(brief_p.read_text())
    brief["topic"] = "Foo $X^2$ & {Bar} #1"
    brief_p.write_text(json.dumps(brief, indent=2))

    # Also break a feature column for thorough coverage
    outline_p = survey_run_dir / "4_outline" / "outline.json"
    outline = json.loads(outline_p.read_text())
    outline["tier_axis"]["feature_columns"][0] = "A&B%C"   # was "Architecture"
    outline_p.write_text(json.dumps(outline, indent=2))

    res = subprocess.run(
        [sys.executable, str(GEN_TAXONOMY), str(survey_run_dir),
         "--layout", "matrix"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    tex = (survey_run_dir / "5_paper" / "figures" / "00_taxonomy.tex").read_text()
    # Each metachar from the polluted topic / column must be properly escaped
    assert r"\$" in tex
    assert r"\&" in tex
    assert r"\{" in tex and r"\}" in tex
    assert r"\#" in tex
    assert r"\%" in tex
    # Critical: the raw user string must not appear unescaped
    assert "Foo $X" not in tex
    assert "& {Bar}" not in tex
    # Polluted feature column must render escaped
    assert r"A\&B\%C" in tex


# ---------------------------------------------------------------------------
# Maturity overlay (tier_axis.tiers[].maturity → badge + legend)
# ---------------------------------------------------------------------------


def test_matrix_no_maturity_field_renders_clean(survey_run_dir):
    """Default fixture has no maturity field → no legend, no badge nodes,
    figure renders unchanged."""
    res = subprocess.run(
        [sys.executable, str(GEN_TAXONOMY), str(survey_run_dir),
         "--layout", "matrix"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    tex = (survey_run_dir / "5_paper" / "figures" / "00_taxonomy.tex").read_text()
    # Legend strip and badges must NOT be emitted
    assert "Maturity overlay" not in tex
    assert "Mature" not in tex
    assert "Frontier" not in tex
    assert "Speculative" not in tex


def test_matrix_with_maturity_emits_badges_and_legend(survey_run_dir):
    """Annotate each tier with a maturity value → tikz must contain a
    legend strip AND one badge per annotated tier."""
    outline_p = survey_run_dir / "4_outline" / "outline.json"
    outline = json.loads(outline_p.read_text())
    # T1=mature, T2=frontier, T3=speculative — full spectrum
    tier_specs = {"T1": "mature", "T2": "frontier", "T3": "speculative"}
    for t in outline["tier_axis"]["tiers"]:
        t["maturity"] = tier_specs[t["id"]]
    outline_p.write_text(json.dumps(outline, indent=2))

    res = subprocess.run(
        [sys.executable, str(GEN_TAXONOMY), str(survey_run_dir),
         "--layout", "matrix"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    tex = (survey_run_dir / "5_paper" / "figures" / "00_taxonomy.tex").read_text()

    # Legend strip
    assert "Maturity overlay" in tex
    # Each label appears at least twice: once as badge on its tier row,
    # once in the legend strip.
    assert tex.count("Mature") >= 2
    assert tex.count("Frontier") >= 2
    assert tex.count("Speculative") >= 2

    # Color hints — verify each maturity got its assigned palette
    assert "blue!18"   in tex     # mature
    assert "orange!22" in tex     # frontier
    assert "gray!22"   in tex     # speculative


def test_matrix_partial_maturity_only_marks_annotated_tiers(survey_run_dir):
    """If only one tier carries maturity, badge appears once, legend still
    rendered (so reader knows what the chip means)."""
    outline_p = survey_run_dir / "4_outline" / "outline.json"
    outline = json.loads(outline_p.read_text())
    outline["tier_axis"]["tiers"][1]["maturity"] = "frontier"  # T2 only
    outline_p.write_text(json.dumps(outline, indent=2))

    res = subprocess.run(
        [sys.executable, str(GEN_TAXONOMY), str(survey_run_dir),
         "--layout", "matrix"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    tex = (survey_run_dir / "5_paper" / "figures" / "00_taxonomy.tex").read_text()

    assert "Maturity overlay" in tex
    # Frontier appears at least twice (badge + legend); other labels still
    # in legend (we render the full strip so the chip semantics is obvious).
    assert tex.count("Frontier") >= 2


def test_matrix_unknown_maturity_value_silently_ignored(survey_run_dir):
    """If a tier carries an unknown maturity value, that tier renders
    no badge but other annotated tiers still do (graceful degrade).
    validate_outline.py emits the schema warning separately."""
    outline_p = survey_run_dir / "4_outline" / "outline.json"
    outline = json.loads(outline_p.read_text())
    outline["tier_axis"]["tiers"][0]["maturity"] = "tomorrow"  # invalid
    outline["tier_axis"]["tiers"][1]["maturity"] = "frontier"  # valid
    outline_p.write_text(json.dumps(outline, indent=2))

    res = subprocess.run(
        [sys.executable, str(GEN_TAXONOMY), str(survey_run_dir),
         "--layout", "matrix"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    tex = (survey_run_dir / "5_paper" / "figures" / "00_taxonomy.tex").read_text()
    # 'tomorrow' must not appear as a chip
    assert "tomorrow" not in tex
    # Valid tier still renders its badge + legend
    assert "Maturity overlay" in tex
    assert tex.count("Frontier") >= 2
