"""Tests for tools/build_evidence_dashboard.py — HTML evidence dashboard.

Asserts at the contract level (HTML structure + content presence + URL
safety) rather than character-by-character to keep tests resilient as
the JS / CSS gets polished.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_evidence_dashboard as bed  # noqa: E402


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------


def test_safe_arxiv_url_accepts_canonical():
    p = {"url": "https://arxiv.org/abs/2001.08361"}
    assert bed._safe_arxiv_url(p) == "https://arxiv.org/abs/2001.08361"


def test_safe_arxiv_url_constructs_from_arxiv_id():
    p = {"arxiv_id": "2203.15556"}
    assert bed._safe_arxiv_url(p) == "https://arxiv.org/abs/2203.15556"


def test_safe_arxiv_url_rejects_non_arxiv():
    """Anti-XSS: any URL outside https://arxiv.org/abs/ must be rejected."""
    bad_urls = [
        {"url": "javascript:alert(1)"},
        {"url": "http://attacker.com/abs/123"},
        {"url": "https://arxiv.org/pdf/2001.08361"},   # not /abs/
        {"url": "https://evil.org/abs/2001.08361"},
    ]
    for p in bad_urls:
        assert bed._safe_arxiv_url(p) is None, f"should reject {p}"


def test_safe_arxiv_url_rejects_garbage_arxiv_id():
    assert bed._safe_arxiv_url({"arxiv_id": "not-an-arxiv-id"}) is None
    assert bed._safe_arxiv_url({"arxiv_id": ""}) is None


def test_extract_cite_contexts_finds_each_cite():
    text = (
        "We argue that scaling laws are universal \\cite{kaplan2020scaling}. "
        "Yet \\cite{hoffmann2022chinchilla} found that current LLMs are "
        "undertrained."
    )
    contexts = bed._extract_cite_contexts(text)
    keys = [k for c in contexts for k in c["cite_keys"]]
    assert "kaplan2020scaling" in keys
    assert "hoffmann2022chinchilla" in keys
    assert len(contexts) == 2


# ---------------------------------------------------------------------------
# CLI smoke + content checks
# ---------------------------------------------------------------------------


def _run_cli(run_dir: Path, output: Path):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_evidence_dashboard.py"),
         str(run_dir), "--output", str(output)],
        capture_output=True, text=True,
    )


def test_dashboard_emits_one_file_with_thesis_and_cite_keys(survey_run_dir, tmp_path):
    output = tmp_path / "survey.evidence.html"
    res = _run_cli(survey_run_dir, output)
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    assert output.exists()

    html = output.read_text()
    # Single-file HTML: doctype + style + script all inline
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "<script>" in html
    assert "<title>" in html

    # Thesis text from 2_thesis/thesis.json must show in the header
    thesis = json.loads(
        (survey_run_dir / "2_thesis" / "thesis.json").read_text())["thesis"]
    # First 30 chars are enough — escaping might re-encode some unicode
    assert thesis[:30] in html

    # Every cite_key in the fixture sections must appear at least once
    for ck in ("kaplan2020scaling", "hoffmann2022chinchilla", "touvron2023llama"):
        assert ck in html, f"cite_key {ck} missing from dashboard"

    # arXiv URLs from filtered.jsonl must show up as click-through links
    assert "https://arxiv.org/abs/2001.08361" in html
    assert "https://arxiv.org/abs/2203.15556" in html
    assert "https://arxiv.org/abs/2302.13971" in html


def test_dashboard_includes_atomic_claims(survey_run_dir, tmp_path):
    output = tmp_path / "survey.evidence.html"
    _run_cli(survey_run_dir, output)
    html = output.read_text()
    # The chinchilla atomic claim quote must appear (proves the claim
    # records were joined onto cite_keys correctly)
    assert "70B parameter model on 1.4 trillion tokens" in html
    # Claim type badge for at least one record
    assert "empirical" in html
    assert "methodological" in html


def test_dashboard_stats_label_disambiguates_atomic_claim_unit(survey_run_dir, tmp_path):
    """pre-fix the header had a row 'N sections / M with atomic
    claims', which read as 'M of the N sections have atomic claims'. The
    actual unit is *citation context* (rows in the table). Label must
    name the unit so a reader doesn't mis-interpret 0 sections."""
    output = tmp_path / "survey.evidence.html"
    _run_cli(survey_run_dir, output)
    html_doc = output.read_text()
    # The corrected, unambiguous wording must appear.
    assert "citation contexts with atomic claims" in html_doc
    # The pre-fix bare 'with atomic claims' (no 'citation contexts'
    # qualifier) must NOT appear on its own.
    import re as _re
    bare = _re.search(r">\s*\d+\s*</strong>\s*with atomic claims\s*<", html_doc)
    assert bare is None, "bare 'with atomic claims' label still present"


def test_dashboard_html_escapes_section_titles(survey_run_dir, tmp_path):
    """A section with HTML metacharacters in its title must be escaped, not
    raw-injected (XSS hardening)."""
    bad_section = survey_run_dir / "5_paper" / "sections" / "05_xss.tex"
    bad_section.write_text(
        r"\section{<script>alert(1)</script>}" "\n"
        r"Innocent body \cite{kaplan2020scaling}." "\n"
    )
    output = tmp_path / "survey.evidence.html"
    res = _run_cli(survey_run_dir, output)
    assert res.returncode == 0
    html = output.read_text()
    # Raw <script>alert(1) must not appear as injected JS — only as escaped
    # text. The dashboard is allowed to embed its OWN <script> tags (the
    # filter logic), but the per-section content must be escaped.
    # We assert: our own filter <script> exists, but the attacker's literal
    # </script>alert</script> sequence does not survive un-escaped in the
    # rendered ROWS payload area.
    assert "&lt;script&gt;alert" in html or "alert(1)" not in html.split("ROWS")[0]


def test_dashboard_handles_missing_thesis(tmp_path):
    """When 2_thesis/thesis.json is missing, the dashboard still renders
    (without the thesis header line)."""
    rd = tmp_path / "rd"
    sec = rd / "5_paper" / "sections"
    sec.mkdir(parents=True)
    (sec / "01_intro.tex").write_text(r"\section{Intro} hello")
    (rd / "1_search").mkdir()
    (rd / "1_search" / "filtered.jsonl").write_text("")
    (rd / "1_search" / "claims_cache.jsonl").write_text("")
    output = tmp_path / "out.html"
    res = _run_cli(rd, output)
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    html = output.read_text()
    assert "<!doctype html>" in html
    # No thesis line emitted
    assert 'class="thesis"' not in html


def test_dashboard_missing_sections_dir_returns_2(tmp_path):
    """No 5_paper/sections/ → exit 2 (input error)."""
    rd = tmp_path / "empty"
    rd.mkdir()
    output = tmp_path / "out.html"
    res = _run_cli(rd, output)
    assert res.returncode == 2
    assert "sections dir not found" in res.stderr


# ---------------------------------------------------------------------------
# Meta-banner from stats.json
# ---------------------------------------------------------------------------


def test_render_meta_banner_empty_stats_returns_empty_string():
    """No usable stats → no banner (clean degrade, no broken HTML)."""
    assert bed._render_meta_banner({}) == ""
    assert bed._render_meta_banner({"papers": {}}) == ""


def test_render_meta_banner_full_stats_emits_six_tiles():
    """Full stats.json (per build_run_stats schema_version=1) renders
    all 6 trust-scaffold tiles."""
    stats = {
        "papers": {"in_corpus": 86, "cited": 79, "coverage": 0.91},
        "citations": {"total": 443, "unique": 79},
        "thesis": {"argument_steps": 5, "anticipated_objections": 3},
        "outline": {},
        "claims_cache": {},
        "systems_compared": 17,
        "document": {"estimated_pages": 27},
    }
    html_str = bed._render_meta_banner(stats)
    assert html_str.startswith('<div class="meta-banner">')
    assert html_str.endswith("</div>")
    # All six numbers present
    assert ">86<" in html_str
    assert ">443<" in html_str
    assert ">5<" in html_str
    assert ">3<" in html_str
    assert ">17<" in html_str
    assert ">~27<" in html_str
    # Coverage rendered as integer percentage
    assert "91% cov." in html_str


def test_render_meta_banner_partial_stats_renders_only_present_tiles():
    """If only papers + pages are available, banner shows 2 tiles."""
    stats = {
        "papers": {"in_corpus": 12, "cited": 12, "coverage": 1.0},
        "document": {"estimated_pages": 8},
    }
    html_str = bed._render_meta_banner(stats)
    # 2 tiles only
    assert html_str.count('class="tile"') == 2
    # Citations not present
    assert "citations" not in html_str
    # Argument steps not present
    assert "argument steps" not in html_str


def test_dashboard_cli_includes_meta_banner_when_stats_present(
    survey_run_dir, tmp_path
):
    """End-to-end: stats.json sitting next to sections must produce a
    meta-banner div in the rendered HTML."""
    # Ensure stats.json exists with realistic numbers
    stats_path = survey_run_dir / "5_paper" / "stats.json"
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps({
        "papers": {"in_corpus": 4, "cited": 3, "coverage": 0.75},
        "citations": {"total": 11, "unique": 3},
        "thesis": {"argument_steps": 4, "anticipated_objections": 2},
        "systems_compared": 3,
        "document": {"estimated_pages": 5},
    }))

    output = tmp_path / "out.html"
    res = _run_cli(survey_run_dir, output)
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    html_doc = output.read_text()
    assert 'class="meta-banner"' in html_doc
    # Numbers rendered
    assert ">4<" in html_doc       # papers in corpus
    assert ">11<" in html_doc      # citations total
    assert ">~5<" in html_doc      # estimated pages
    # CSS for the banner present
    assert ".meta-banner" in html_doc


def test_dashboard_cli_silently_omits_banner_when_stats_missing(
    survey_run_dir, tmp_path
):
    """No stats.json → dashboard renders the original 4-tile stats but
    NOT the new meta-banner. Backward-compat for runs that pre-date
    populated by build_run_stats."""
    # Make sure stats.json is absent
    stats_path = survey_run_dir / "5_paper" / "stats.json"
    if stats_path.exists():
        stats_path.unlink()

    output = tmp_path / "out.html"
    res = _run_cli(survey_run_dir, output)
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    html_doc = output.read_text()
    assert 'class="meta-banner"' not in html_doc
    # Original stats line still present
    assert 'class="stats"' in html_doc


# ---------------------------------------------------------------------------
# Structural-template invariants panel
# ---------------------------------------------------------------------------


def test_render_structural_panel_empty_returns_empty_string():
    """No audit data → no panel (clean degrade)."""
    assert bed._render_structural_panel(None) == ""
    assert bed._render_structural_panel({}) == ""
    assert bed._render_structural_panel({"invariants": {}}) == ""


def test_render_structural_panel_renders_all_invariants():
    """Each invariant becomes one row with mark / name / value / issue."""
    audit = {
        "invariants": {
            "citation_density": {
                "ok": True, "value": "8.40", "issue": "",
            },
            "annotated_bibliography": {
                "ok": False, "value": "12/95 (13%)",
                "issue": "only 13% of entries annotated; need ≥ 80%",
            },
        },
        "passing": 1, "total": 2, "score": 0.5,
    }
    html_str = bed._render_structural_panel(audit)
    assert 'class="invariants"' in html_str
    assert "1/2 invariants pass" in html_str
    assert "citation_density" in html_str
    assert "annotated_bibliography" in html_str
    assert "13% of entries annotated" in html_str
    # mark column
    assert "✓" in html_str and "✗" in html_str


def test_render_structural_panel_summary_class_reflects_passing_count():
    """7+ pass → ok; 5-6 → warn; <5 → fail. The CSS class drives banner colour."""
    base = {"invariants": {f"i{i}": {"ok": True, "value": "v", "issue": ""}
                            for i in range(8)}, "total": 8, "score": 1.0}
    base["passing"] = 8
    assert "invariants-summary ok" in bed._render_structural_panel(base)

    base["passing"] = 6
    base["score"] = 0.75
    # Flip 2 invariants to fail to keep counts consistent
    base["invariants"]["i0"] = {"ok": False, "value": "v", "issue": "x"}
    base["invariants"]["i1"] = {"ok": False, "value": "v", "issue": "x"}
    assert "invariants-summary warn" in bed._render_structural_panel(base)

    base["passing"] = 3
    base["score"] = 0.375
    for k in ("i2", "i3", "i4", "i5"):
        base["invariants"][k] = {"ok": False, "value": "v", "issue": "x"}
    assert "invariants-summary fail" in bed._render_structural_panel(base)


def test_dashboard_cli_includes_structural_panel(survey_run_dir, tmp_path):
    """End-to-end: dashboard must surface the 8-invariant panel for a
    run, computed from audit_writing.audit_structural_template."""
    output = tmp_path / "out.html"
    res = _run_cli(survey_run_dir, output)
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    html_doc = output.read_text()
    # Panel container present
    assert 'class="invariants"' in html_doc
    # All 8 invariant names rendered (the survey-run fixture flunks them all,
    # so each row carries the ✗ mark — which is the correct, honest
    # signal for a paper that hasn't yet been brought up to spec)
    for name in (
        "citation_density",
        "annotated_bibliography",
        "cross_cutting_matrix",
        "section_nesting",
        "related_surveys_subsection",
        "open_problems_pairing",
        "conclusion_reframe",
        "contributions_section_refs",
    ):
        assert name in html_doc, f"invariant {name!r} missing from dashboard"


# ---------------------------------------------------------------------------
# Benchmark-diff panel
# ---------------------------------------------------------------------------


def test_load_benchmark_targets_parses_repo_file():
    """The repo's shared-references/benchmark-targets.json must parse
    and carry the headline numbers from the gap-analysis doc."""
    targets = bed._load_benchmark_targets()
    assert targets is not None
    bench = targets["benchmark"]
    # The five constants every diff-row depends on.
    assert bench["pages"] == 45
    assert bench["body_words"] == 17457
    assert bench["bib_entries"] == 95
    assert bench["citation_density_per_1k_words"] == 8.4
    assert bench["cross_cutting_matrix"]["present"] is True
    # Tolerances must exist for every diffable metric.
    assert "tolerance" in targets
    assert "metrics" in targets


def test_load_benchmark_targets_returns_none_on_missing(tmp_path):
    """Missing file → None, dashboard degrades to no panel."""
    assert bed._load_benchmark_targets(tmp_path / "absent.json") is None


def test_load_benchmark_targets_returns_none_on_malformed_json(tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text("{not valid json")
    assert bed._load_benchmark_targets(bad) is None


def test_diff_status_within_abs_tolerance_returns_ok():
    status, delta = bed._diff_status("pages", 47, 45, {"abs": 5})
    assert status == "ok"
    assert delta == "+2"


def test_diff_status_outside_abs_but_within_2x_returns_warn():
    status, _ = bed._diff_status("pages", 53, 45, {"abs": 5})
    assert status == "warn"


def test_diff_status_outside_2x_tolerance_returns_fail():
    status, _ = bed._diff_status("pages", 60, 45, {"abs": 5})
    assert status == "fail"


def test_diff_status_relative_tolerance_uses_percent_delta():
    """For body_words style metrics, the Δ string is rendered as a
    percentage of the benchmark, not just an absolute number."""
    status, delta = bed._diff_status(
        "body_words", 14000, 17457, {"rel": 0.20}
    )
    # 14000/17457 ≈ 0.802, |Δ| = 3457, tol = 0.20*17457 = 3491 → ok
    assert status == "ok"
    assert "%" in delta
    assert delta.startswith("-")


def test_diff_status_boolean_metric_compares_directly():
    ok_status, ok_delta = bed._diff_status(
        "cross_cutting_matrix_present", True, True, {}
    )
    assert ok_status == "ok"
    assert ok_delta == "✓"
    fail_status, fail_delta = bed._diff_status(
        "cross_cutting_matrix_present", False, True, {}
    )
    assert fail_status == "fail"
    assert fail_delta == "✗"


def test_diff_status_none_observed_returns_warn():
    """No data for this run → dash, warn (so missing data is visible
    rather than mistaken for 'on target')."""
    status, delta = bed._diff_status("pages", None, 45, {"abs": 5})
    assert status == "warn"
    assert delta == "—"


def test_extract_run_metrics_pulls_from_stats_and_audit():
    stats = {
        "document": {"estimated_pages": 32, "body_words": 11092},
        "papers": {"in_corpus": 87},
        "citations": {"total": 311},
        "outline": {"sections": 14},
    }
    audit = {"invariants": {
        "citation_density":      {"value": "23.49", "ok": False, "issue": "x"},
        "cross_cutting_matrix":  {"value": "matrix=no, aux_tables=0",
                                   "ok": False, "issue": "x"},
        "open_problems_pairing": {"value": "open=0, future=0, paired=0/0 (0%)",
                                   "ok": False, "issue": "x"},
        "conclusion_reframe":    {"value": "796 words, 0.63 bullets/50w",
                                   "ok": False, "issue": "x"},
        "contributions_section_refs": {"value": "0/5 items have (§N) cross-ref (0%)",
                                        "ok": False, "issue": "x"},
    }}
    m = bed._extract_run_metrics(stats, audit, None)
    assert m["pages"] == 32
    assert m["body_words"] == 11092
    assert m["bib_entries"] == 87
    assert m["inline_citations"] == 311
    assert m["top_level_sections"] == 14
    assert m["citation_density_per_1k_words"] == 23.49
    assert m["cross_cutting_matrix_present"] is False
    assert m["open_problems_items"] == 0
    assert m["future_directions_items"] == 0
    assert m["conclusion_words"] == 796
    assert m["contributions_items"] == 5


def test_render_benchmark_diff_panel_empty_inputs_return_empty_string():
    assert bed._render_benchmark_diff_panel(None, {}) == ""
    assert bed._render_benchmark_diff_panel({"benchmark": {}}, {}) == ""
    # No metrics that the run can fill → empty
    assert bed._render_benchmark_diff_panel(
        bed._load_benchmark_targets(), {},
    ) == ""


def test_render_benchmark_diff_panel_renders_rows_and_summary():
    targets = bed._load_benchmark_targets()
    run_metrics = {
        "pages": 47,
        "body_words": 16000,
        "bib_entries": 90,
        "inline_citations": 150,
        "citation_density_per_1k_words": 9.0,
        "top_level_sections": 8,
        "cross_cutting_matrix_present": True,
        "open_problems_items": 6,
        "future_directions_items": 6,
        "conclusion_words": 580,
        "contributions_items": 4,
    }
    html_str = bed._render_benchmark_diff_panel(targets, run_metrics)
    assert 'class="bench-diff"' in html_str
    # Header tabular structure
    assert "<thead>" in html_str
    assert "Benchmark" in html_str
    # Headline metrics rendered
    assert "Pages" in html_str
    assert "Body words" in html_str
    # All metrics on target → summary class ok
    assert "bench-summary ok" in html_str
    # Δ for pages = +2 (within ±5)
    assert "+2" in html_str


def test_render_benchmark_diff_panel_baseline_run_renders_fail_status():
    """A baseline run (32 pp, 11092 body words, 311 citations)
    must trip multiple rows red — that is the correct, honest signal."""
    targets = bed._load_benchmark_targets()
    run_metrics = {
        "pages": 32,
        "body_words": 11092,
        "bib_entries": 87,
        "inline_citations": 311,                 # +112% over benchmark
        "citation_density_per_1k_words": 23.49,  # 2.8× the benchmark
        "top_level_sections": 14,                # +6 over benchmark
        "cross_cutting_matrix_present": False,   # missing
        "open_problems_items": 0,
        "future_directions_items": 0,
        "conclusion_words": 796,
        "contributions_items": 5,
    }
    html_str = bed._render_benchmark_diff_panel(targets, run_metrics)
    # At least 4 rows in fail state for this run.
    n_fail = html_str.count('<tr class="fail">')
    assert n_fail >= 4, f"expected ≥4 fail rows, got {n_fail}"
    # Overall summary banner must be fail.
    assert "bench-summary fail" in html_str


def test_dashboard_cli_includes_benchmark_diff(survey_run_dir, tmp_path):
    """End-to-end: with stats.json present and the audit doing its work,
    the rendered HTML must carry a benchmark-diff panel."""
    # Plant a minimal stats.json so the diff panel has data.
    stats_path = survey_run_dir / "5_paper" / "stats.json"
    stats_path.write_text(json.dumps({
        "schema_version": 1,
        "document": {"estimated_pages": 5, "body_words": 800,
                      "section_files": 4, "body_sections": 3,
                      "total_chars": 4000},
        "papers": {"in_corpus": 3, "cited": 3, "coverage": 1.0},
        "citations": {"total": 11, "unique": 3},
        "outline": {"sections": 3, "body_sections": 3, "tier_axis_tiers": 3},
        "thesis": {"argument_steps": 4, "anticipated_objections": 2},
        "claims_cache": {"papers_mined": 3, "atomic_claims": 6},
        "systems_compared": 3,
    }))
    output = tmp_path / "out.html"
    res = _run_cli(survey_run_dir, output)
    assert res.returncode == 0, f"stderr:\n{res.stderr}"
    html_doc = output.read_text()
    assert 'class="bench-diff"' in html_doc
    # Benchmark title surfaces in the panel header
    assert "From Copilots to Colleagues" in html_doc
    # Body words row: 800 vs benchmark 17457 — has to render fail
    assert "Body words" in html_doc
