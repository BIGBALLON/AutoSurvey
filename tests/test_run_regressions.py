"""Regression tests for bugs surfaced by a real end-to-end survey run
(long-context-extension brief, 2026-06).

Each test pins a fix that a green unit suite previously missed because no
test exercised the relevant tool on realistic inputs:

1. openalex_fetch._parse_work crashed on OpenAlex's explicit ``null``
   nested objects (primary_location / open_access / author / topics),
   and the exception discarded the *entire* query's results.
2. scaffold_cross_cutting_matrix selected rows by citation count, so
   foundation models / off-topic papers leaked into the comparison
   matrix instead of the surveyed methods.
3. validate_outline flagged non-body sections (background / future /
   conclusion) as "missing argues_for_thesis_step" via an id-prefix
   heuristic instead of respecting section_type.
4. gen_taxonomy_tikz (matrix layout) emitted invalid xcolor expressions
   like ``blue!18!40`` that fail to compile.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import audit_writing  # noqa: E402
import bib_generator as bg  # noqa: E402
import bib_hygiene  # noqa: E402
import build_dimension_tables as bdt  # noqa: E402
import build_evidence_dashboard as bed  # noqa: E402
import build_run_stats as brs  # noqa: E402
import check_anchor_coverage as cac  # noqa: E402
import gen_timeline  # noqa: E402
import openalex_fetch  # noqa: E402
import scaffold_cross_cutting_matrix as scm  # noqa: E402
import validate_outline  # noqa: E402


# ---------------------------------------------------------------------------
# 1. OpenAlex null-safety
# ---------------------------------------------------------------------------

def test_openalex_parse_work_handles_explicit_nulls():
    """A work whose nested objects are explicitly ``null`` must parse
    without raising (the bug crashed and dropped the whole query)."""
    client = openalex_fetch.OpenAlexClient(email="test@example.com")
    work = {
        "id": "https://openalex.org/W42",
        "display_name": "A Paper With Null Everything",
        "primary_location": None,
        "open_access": None,
        "authorships": [{"author": None}],
        "topics": None,
        "keywords": None,
        "publication_year": 2024,
        "cited_by_count": 7,
    }
    rec = client._parse_work(work)  # must not raise
    assert rec["openalex_id"] == "W42"
    assert rec["venue"] == "Unknown"
    assert rec["authors"] == ["Unknown"]
    assert rec["cited_by_count"] == 7


def test_openalex_default_polite_pool_user_agent():
    """The client always sets a mailto User-Agent so bursts hit the
    polite pool rather than the throttled anonymous pool."""
    client = openalex_fetch.OpenAlexClient()
    ua = client.session.headers.get("User-Agent", "")
    assert "mailto:" in ua


# ---------------------------------------------------------------------------
# 2. Cross-cutting matrix draws rows from surveyed methods, not citations
# ---------------------------------------------------------------------------

def test_matrix_prefers_outline_methods_over_high_citation():
    slot = {"col_labels": ["Family", "Reach"], "row_label": "Method",
            "expected_rows": 10}
    cards = [
        # An off-topic foundation model with a huge citation count and a
        # complete card — exactly what used to win a row slot.
        {"cite_key": "touvron2023llama", "title": "LLaMA",
         "_completeness": 1.0, "citation_count": 99999},
        {"cite_key": "peng2023yarn", "title": "YaRN", "_completeness": 0.4},
        {"cite_key": "zhenyu2023h2o", "title": "H2O", "_completeness": 0.4},
    ]
    tex = scm.render_matrix_tex(
        slot, cards, preferred_keys=["peng2023yarn", "zhenyu2023h2o"])
    assert "peng2023yarn" in tex
    assert "zhenyu2023h2o" in tex
    # The high-citation non-method must NOT appear as a row.
    assert "touvron2023llama" not in tex


def test_matrix_preserves_outline_order():
    slot = {"col_labels": ["X"], "row_label": "Method", "expected_rows": 10}
    cards = [{"cite_key": "b", "title": "B"}, {"cite_key": "a", "title": "A"}]
    tex = scm.render_matrix_tex(slot, cards, preferred_keys=["a", "b"])
    assert tex.index("citep{a}") < tex.index("citep{b}")


# ---------------------------------------------------------------------------
# 3. validate_outline exempts non-body sections by section_type
# ---------------------------------------------------------------------------

def test_validate_thesis_schema_exempts_non_body_sections():
    thesis = {"argument_steps": [{"step_id": "S1", "claim": "c"}]}
    outline = {
        "sections": [
            {"id": "01_intro", "section_type": "intro",
             "argues_for_thesis_step": None},
            {"id": "02_background", "section_type": "background",
             "argues_for_thesis_step": None},
            {"id": "03_body", "section_type": "body",
             "argues_for_thesis_step": "S1",
             "argument_skeleton": {"claim": "c", "steelman": "s",
                                   "concession": "x", "so_what": "y",
                                   "evidence_claim_keys": []}},
            {"id": "08_future", "section_type": "future_directions",
             "argues_for_thesis_step": None},
            {"id": "09_conclusion", "section_type": "conclusion",
             "argues_for_thesis_step": None},
        ]
    }
    violations = validate_outline.validate_thesis_schema(outline, thesis)
    assert not any("missing argues_for_thesis_step" in v for v in violations), violations


def test_validate_thesis_schema_still_flags_untyped_body():
    """A genuine body section (digit-prefixed, no section_type, no
    binding) is still flagged."""
    thesis = {"argument_steps": [{"step_id": "S1", "claim": "c"},
                                 {"step_id": "S2", "claim": "c2"}]}
    outline = {
        "sections": [
            {"id": "01_intro", "section_type": "intro",
             "argues_for_thesis_step": None},
            {"id": "03_body", "argues_for_thesis_step": "S1",
             "argument_skeleton": {"claim": "c", "steelman": "s",
                                   "concession": "x", "so_what": "y",
                                   "evidence_claim_keys": []}},
            {"id": "04_unbound"},  # no section_type, no binding → body
            {"id": "05_body2", "argues_for_thesis_step": "S2",
             "argument_skeleton": {"claim": "c", "steelman": "s",
                                   "concession": "x", "so_what": "y",
                                   "evidence_claim_keys": []}},
            {"id": "09_conclusion", "section_type": "conclusion",
             "argues_for_thesis_step": None},
        ]
    }
    violations = validate_outline.validate_thesis_schema(outline, thesis)
    assert any("04_unbound" in v and "missing argues_for_thesis_step" in v
               for v in violations), violations


# ---------------------------------------------------------------------------
# 5. Closed-set accepts cite_key even when paper_id is a source id
# ---------------------------------------------------------------------------

def test_validate_outline_closed_set_accepts_cite_key():
    """When filtered.jsonl carries a source ``paper_id`` (arXiv/OpenAlex)
    but the outline references the ``cite_key``, the paper must NOT be
    stripped as hallucinated."""
    papers = [
        {"paper_id": "W123456789", "cite_key": "peng2023yarn",
         "title": "YaRN", "citation_count": 18},
        {"paper_id": "W987654321", "cite_key": "ding2024longrope",
         "title": "LongRoPE", "citation_count": 15},
    ]
    outline = {
        "sections": [
            {"id": "03_positional", "section_type": "body",
             "argues_for_thesis_step": "S1",
             "argument_skeleton": {"claim": "c", "steelman": "s",
                                   "concession": "x", "so_what": "y",
                                   "evidence_claim_keys": []},
             "primary_papers": ["peng2023yarn", "ding2024longrope"],
             "secondary_papers": []},
        ]
    }
    repaired, repairs = validate_outline.validate_outline(outline, papers, {})
    kept = repaired["sections"][0]["primary_papers"]
    assert kept == ["peng2023yarn", "ding2024longrope"], kept
    assert repairs["removed_total"] == 0


# ---------------------------------------------------------------------------
# 6. Source-registry health — every entry is well-formed
# ---------------------------------------------------------------------------

def test_source_registry_entries_well_formed():
    """Catch dead/malformed feed definitions structurally (no network):
    a stale feed_url that 404s degrades search silently, which is what
    happened to the Meta AI / AllenAI feeds. Every entry must carry a
    name, an http(s) feed_url, a known type, and an int tier."""
    registry = json.loads(
        (ROOT / "tools" / "source_registry.json").read_text(encoding="utf-8"))
    # The registry is keyed by tier category (tier1_official, tier2_*, …),
    # each mapping to a list of source entries; flatten all list values.
    if isinstance(registry, dict):
        sources = [e for v in registry.values() if isinstance(v, list) for e in v]
    else:
        sources = registry
    assert isinstance(sources, list) and sources, "registry has no sources"
    allowed_types = {"rss", "atom", "html_scrape"}
    for entry in sources:
        name = entry.get("name")
        assert name, f"entry missing name: {entry}"
        url = entry.get("feed_url", "")
        assert url.startswith(("http://", "https://")), f"{name}: bad feed_url {url!r}"
        assert entry.get("type") in allowed_types, f"{name}: bad type {entry.get('type')!r}"
        assert isinstance(entry.get("tier"), int), f"{name}: tier must be int"


# ---------------------------------------------------------------------------
# 7. Comparison-matrix table rows don't trip the per-sentence citation cap
# ---------------------------------------------------------------------------

def test_citation_cap_ignores_table_rows():
    """A cross-cutting matrix legitimately carries one \\citep per row
    (invariant 4); the per-sentence citation cap must not count the table
    body as a single over-cited sentence."""
    table = r"""\begin{table*}[t]
\caption{Comparison of methods (see \S6).}
\begin{tabular}{l l}
\toprule
YaRN~\citep{a} & positional \\
H2O~\citep{b} & kv \\
SnapKV~\citep{c} & kv \\
KIVI~\citep{d} & kv \\
MLA~\citep{e} & kv \\
\bottomrule
\end{tabular}
\end{table*}
"""
    sentences = audit_writing._split_sentences_for_citation_audit(table)
    worst = max((len(re.findall(r"\\cite[a-z]*\b", s)) for s in sentences),
                default=0)
    assert worst <= audit_writing.SENTENCE_CITATION_CAP, (
        f"table rows counted as {worst}-cite sentence")


# ---------------------------------------------------------------------------
# 8. Anchor-coverage gate (search collected the must-have material?)
# ---------------------------------------------------------------------------

def test_anchor_coverage_detects_missing_and_acronym_boundaries(tmp_path):
    corpus = tmp_path / "filtered.jsonl"
    records = [
        {"title": "YaRN: Efficient Context Window Extension", "abstract": "rotary scaling"},
        {"title": "H2O: Heavy-Hitter Oracle for KV cache", "abstract": "eviction"},
        {"title": "RULER benchmark", "abstract": "length controlled"},
    ]
    corpus.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    rep = cac.check(
        anchors=["YaRN", "H2O", "RULER", "LongRoPE"],  # LongRoPE absent
        corpus_paths=[corpus], min_ratio=0.7)
    assert "LongRoPE" in rep["missing"]
    assert set(rep["hit"]) == {"YaRN", "H2O", "RULER"}
    assert rep["coverage"] == round(3 / 4, 3)
    assert rep["ok"] is True  # 0.75 >= 0.7

    # Acronym must match on word boundary, not as a substring.
    assert cac.covered("H2O", ["h2o heavy hitter oracle for kv cache eviction"])
    assert not cac.covered("NSA", ["native sparse attention hardware aligned"])
    assert not cac.covered("KV", ["the skv encoder is unrelated"])

    # Below-threshold corpus fails the gate.
    rep2 = cac.check(anchors=["A", "B", "C", "D"], corpus_paths=[corpus], min_ratio=0.7)
    assert rep2["ok"] is False


# ---------------------------------------------------------------------------
# 9. bib_hygiene scans figures/ so table-only citations aren't pruned
# ---------------------------------------------------------------------------

def test_bib_hygiene_collects_figure_and_table_citations(tmp_path):
    paper = tmp_path / "5_paper"
    (paper / "sections").mkdir(parents=True)
    (paper / "figures" / "tables").mkdir(parents=True)
    (paper / "main.tex").write_text(r"\input{sections/01}", encoding="utf-8")
    (paper / "sections" / "01.tex").write_text(
        r"Prose cites \citep{prose_key}.", encoding="utf-8")
    # keys that live ONLY in a figure and a table fragment
    (paper / "figures" / "00_taxonomy.tex").write_text(
        r"\citep{figure_only_key}", encoding="utf-8")
    (paper / "figures" / "tables" / "03_comp.tex").write_text(
        r"\citet{table_only_key} & x \\", encoding="utf-8")

    cited = bib_hygiene.collect_cited_keys(tmp_path)
    assert {"prose_key", "figure_only_key", "table_only_key"} <= cited, (
        f"figure/table citations not collected: {cited}")


# ---------------------------------------------------------------------------
# 10. Milestone timeline renders a reference-style single-axis figure
# ---------------------------------------------------------------------------

def test_milestone_timeline_renders(tmp_path):
    out = tmp_path / "timeline.pdf"
    milestones = [
        {"label": "RoPE", "date": "2021-04", "category": "Positional"},
        {"label": "FlashAttention", "date": "2022-05", "category": "Systems"},
        {"label": "YaRN", "date": "2023-09", "category": "Positional"},
        {"label": "StreamingLLM", "date": "2023-09", "category": "Sparse"},
        {"label": "MLA", "date": "2024-05", "category": "KV-cache"},
        {"label": "CoPE", "date": "2026-02", "category": "Positional"},
    ]
    n = gen_timeline.plot_milestones(milestones, out, title="t")
    assert n == 6
    assert out.exists() and out.stat().st_size > 1000

    # Empty input is a no-op, not a crash.
    assert gen_timeline.plot_milestones([], tmp_path / "x.pdf", title="t") == 0


def test_year_tick_intervals_thin_for_long_spans():
    """Long histories must thin the labelled year ticks (no per-year smear)."""
    assert gen_timeline._year_tick_intervals(5) == (1, None)
    assert gen_timeline._year_tick_intervals(20) == (2, 1)
    assert gen_timeline._year_tick_intervals(50) == (5, 1)
    # A 1950-2026 span (76 yrs) labels once a decade with 5-yr minor ticks.
    assert gen_timeline._year_tick_intervals(76) == (10, 5)


def test_long_span_milestone_timeline_renders(tmp_path):
    """A 1950-2026 milestone set (the conversational-AI history case) renders
    without erroring on the very long, sparse-then-dense time axis."""
    out = tmp_path / "long_timeline.pdf"
    milestones = [
        {"label": "Turing Test", "date": "1950", "category": "Foundational"},
        {"label": "ELIZA", "date": "1966", "category": "Rule-based"},
        {"label": "PARRY", "date": "1972", "category": "Rule-based"},
        {"label": "word2vec", "date": "2013", "category": "Statistical"},
        {"label": "Transformer", "date": "2017-06", "category": "Pretraining"},
        {"label": "GPT-3", "date": "2020-05", "category": "Pretraining"},
        {"label": "ChatGPT", "date": "2022-11", "category": "Alignment"},
        {"label": "GPT-5.5", "date": "2026", "category": "Frontier"},
    ]
    n = gen_timeline.plot_milestones(milestones, out, title="history")
    assert n == 8
    assert out.exists() and out.stat().st_size > 1000


# ---------------------------------------------------------------------------
# 11. Table cell escaping handles < and > (text-mode safe)
# ---------------------------------------------------------------------------

def test_dimension_table_escapes_angle_brackets():
    """`<`/`>` in a card value must not render as inverted punctuation in
    text mode; escape_latex maps them to \\textless{} / \\textgreater{}."""
    out = bdt.escape_latex("effective << claimed, a<4-bit")
    assert "<" not in out and ">" not in out
    assert r"\textless{}" in out


# ---------------------------------------------------------------------------
# 12. Evidence dashboard links every cited paper (not just arXiv ones)
# ---------------------------------------------------------------------------

def test_evidence_dashboard_source_url_fallback():
    f = bed._safe_source_url
    assert f({"url": "https://arxiv.org/abs/2309.00071"}) == (
        "https://arxiv.org/abs/2309.00071", "arXiv")
    assert f({"arxiv_id": "2402.13753"})[1] == "arXiv"
    assert f({"doi": "10.48550/arxiv.2309.00071"})[0].startswith("https://doi.org/")
    assert f({"url": "https://openalex.org/W123"}) == (
        "https://openalex.org/W123", "OpenAlex")
    # curated lab/blog host → generic https fallback, labelled "source"
    assert f({"url": "https://qwenlm.github.io/blog/qwen2.5-1m/"}) == (
        "https://qwenlm.github.io/blog/qwen2.5-1m/", "source")
    # non-https / empty → no link
    assert f({"url": "ftp://evil/x"}) is None
    assert f({}) is None


# ---------------------------------------------------------------------------
# 13. run-stats reports citations per *cited* paper, not per corpus paper
# ---------------------------------------------------------------------------

def test_run_stats_reports_per_cited_not_per_corpus():
    stats = {
        "papers": {"in_corpus": 169, "cited": 49, "coverage": 0.29},
        "citations": {"total": 90, "unique": 49,
                      "per_paper_avg": 0.53, "per_cited_avg": 1.84},
        "thesis": {"argument_steps": 4, "anticipated_objections": 3},
        "document": {"section_files": 12, "body_sections": 8,
                     "estimated_pages": 18, "total_chars": 60000},
        "systems_compared": 16,
    }
    para = brs.render_paragraph(stats)
    assert "1.8 per cited paper" in para        # 90 / 49 reuse rate
    assert "0.5 per cited paper" not in para     # not 90 / 169 (corpus)
    # Date parsing accepts year-only and YYYY-MM.
    assert gen_timeline._parse_milestone_date("2025") is not None
    assert gen_timeline._parse_milestone_date("2025-02") is not None
    assert gen_timeline._parse_milestone_date("not-a-date") is None


# ---------------------------------------------------------------------------
# 4. gen_taxonomy_tikz matrix layout emits valid xcolor
# ---------------------------------------------------------------------------

# A percentage followed by a bare number (e.g. ``blue!18!40``) is invalid
# xcolor: after ``c!p`` the next token must be a colour name, not a number.
_INVALID_XCOLOR_RE = re.compile(r"![0-9]+![0-9]")


def test_taxonomy_matrix_emits_valid_xcolor(survey_run_dir: Path):
    out = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_taxonomy_tikz.py"),
         str(survey_run_dir), "--layout", "matrix"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    tex_path = survey_run_dir / "5_paper" / "figures" / "00_taxonomy.tex"
    assert tex_path.exists(), "taxonomy tex not written"
    tex = tex_path.read_text(encoding="utf-8")
    bad = _INVALID_XCOLOR_RE.findall(tex)
    assert not bad, f"invalid xcolor expressions emitted: {bad[:5]}"
    # And the old literal bug must be gone.
    assert "!40," not in tex


# ---------------------------------------------------------------------------
# 14. cross-cutting matrix row names must not shred hyphenated method names
#     (surfaced by the MoE run: "ST-MoE" became "ST", "Auxiliary-Loss-Free"
#     became "Auxiliary", because the namer split on the hyphen).
# ---------------------------------------------------------------------------

def test_matrix_row_name_preserves_hyphenated_methods():
    # Colon separates name from subtitle → keep the name, hyphen intact.
    assert scm._short_system_name({"title": "ST-MoE: Designing Stable Models"}) == "ST-MoE"
    assert scm._short_system_name({"title": "DeepSeek-V3 Technical Report"}).startswith("DeepSeek-V3")
    # No colon, hyphenated method must stay whole, not collapse to first token.
    name = scm._short_system_name({"title": "Auxiliary-Loss-Free Load Balancing Strategy"})
    assert name.startswith("Auxiliary-Loss-Free"), name
    # Leading article is dropped from descriptive titles.
    assert not scm._short_system_name(
        {"title": "A Theoretical Framework for Load Balancing"}).startswith("A ")
    # An explicit name field wins over the title heuristic.
    assert scm._short_system_name(
        {"title": "DeepSeekMoE: Towards Ultimate Expert Specialization",
         "short_name": "DeepSeek-MoE"}) == "DeepSeek-MoE"


# ---------------------------------------------------------------------------
# 15. cross-cutting matrix must width-fit so a wide table never overflows
# ---------------------------------------------------------------------------

def test_matrix_render_wraps_in_resizebox():
    slot = {"col_labels": ["Routing", "Experts", "Balancing", "Precision",
                           "Quality", "Failure mode"],
            "row_label": "System", "expected_rows": 2}
    cards = [{"cite_key": "a2024x", "title": "Method-One: a long descriptive subtitle"},
             {"cite_key": "b2024y", "title": "Method-Two: another long subtitle"}]
    tex = scm.render_matrix_tex(slot, cards, preferred_keys=["a2024x", "b2024y"])
    assert r"\resizebox{\textwidth}{!}{" in tex
    # The resizebox must close after the tabular.
    assert tex.index(r"\resizebox") < tex.index(r"\begin{tabular}")
    assert tex.index(r"\end{tabular}") < tex.rindex("}")


# ---------------------------------------------------------------------------
# 16. bib_generator must convert Unicode author names to LaTeX so the
#     bibliography compiles under pdflatex's T1 font (surfaced by MoE authors
#     like "Pióró", "Król", and Turkish dotless-i "ı").
# ---------------------------------------------------------------------------

def test_bib_generator_escapes_unicode_authors():
    paper = {"title": "Scaling Laws", "year": 2024,
             "authors": [{"name": "Michał Pióró"}, {"name": "Kamil Król"},
                         {"name": "İlhan Yıldız"}]}
    entry = bg.paper_to_bibtex(paper, "pioro2024scaling")
    # No raw non-ASCII letters should remain in the emitted entry.
    assert all(ord(c) < 128 for c in entry), \
        [c for c in entry if ord(c) >= 128]
    # Accents become LaTeX commands; the dotless-i maps to \i.
    assert "\\'o" in entry or "\\'{o}" in entry
    assert r"{\l}" in entry      # Michał
    assert r"{\i}" in entry      # Yıldız
    # The standalone converter is idempotent on pure ASCII.
    assert bg._unicode_to_latex("Switch Transformer") == "Switch Transformer"
