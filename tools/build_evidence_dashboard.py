#!/usr/bin/env python3
"""build_evidence_dashboard.py — HTML evidence dashboard for an AutoSurvey run.

Produces a single static HTML file (no backend) listing every \\cite{} in
the assembled survey alongside its supporting atomic_claim quotes from
claims_cache.jsonl and an arXiv URL for click-through verification.

Spec: skills/shared-references/claims-contract.md (evidence layer);
called by skills/survey-run/SKILL.md after tectonic compile.

CLI:
    build_evidence_dashboard.py <run_dir> --output <html_path>

Exit codes:
    0  — HTML written
    2  — input error
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

# The dashboard pulls the 8 structural invariants directly from
# audit_writing.audit_structural_template so the HTML stays in lock-step
# with whatever the audit gate enforces — no second source of truth.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_writing as _aw  # noqa: E402


_CITE_RE = re.compile(r"\\cite[tp]?\*?\{([^}]+)\}")
_SECTION_RE = re.compile(r"\\section\*?\s*\{([^}]+)\}")
# Allow only HTTPS arXiv abs URLs through (no other protocols / hosts).
_ARXIV_URL_RE = re.compile(r"^https://arxiv\.org/abs/[\w./-]+$")


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


def _safe_arxiv_url(paper: dict[str, Any]) -> str | None:
    url = paper.get("url") or paper.get("arxiv_url")
    if isinstance(url, str) and _ARXIV_URL_RE.match(url):
        return url
    arxiv_id = paper.get("arxiv_id") or paper.get("eprint")
    if isinstance(arxiv_id, str) and re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", arxiv_id):
        return f"https://arxiv.org/abs/{arxiv_id}"
    return None


# Generic HTTPS source allow-list (host whitelist) for click-through links.
# Many corpora are OpenAlex / DOI sourced and carry no arXiv id, so the
# dashboard must still link every cited paper to *some* verifiable source —
# not just the arXiv subset.
_SAFE_HOST_RE = re.compile(
    r"^https://(?:[\w.-]+\.)?"
    r"(?:arxiv\.org|openalex\.org|doi\.org|aclanthology\.org|"
    r"semanticscholar\.org|huggingface\.co|github\.com|openreview\.net)/",
    re.IGNORECASE,
)


def _safe_source_url(paper: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(url, label)`` for a click-through link, preferring arXiv,
    then DOI, then OpenAlex / other whitelisted hosts. ``None`` if no safe
    URL is available."""
    arxiv = _safe_arxiv_url(paper)
    if arxiv:
        return arxiv, "arXiv"
    doi = paper.get("doi")
    if isinstance(doi, str) and doi.strip():
        d = doi.strip()
        if d.lower().startswith("http"):
            if _SAFE_HOST_RE.match(d):
                return d, "DOI"
        elif re.match(r"^10\.\d{4,}/\S+$", d):
            return f"https://doi.org/{d}", "DOI"
    for cand in (paper.get("url"), paper.get("oa_url"), paper.get("openalex_id")):
        if not isinstance(cand, str):
            continue
        cand = cand.strip()
        if _SAFE_HOST_RE.match(cand):
            host = cand.split("/")[2].lower()
            return cand, ("OpenAlex" if "openalex" in host else "source")
        # Generic fallback: any https URL from the (trusted, curated) corpus
        # — e.g. lab/blog tech-report links such as qwenlm.github.io. The
        # dashboard is a local file and renders links escaped + noopener, so
        # a host whitelist is not required for these vetted sources.
        if re.match(r"^https://[\w.-]+\.[a-z]{2,}/", cand, re.IGNORECASE):
            return cand, "source"
    return None


def _extract_cite_contexts(section_text: str) -> list[dict[str, Any]]:
    """For each \\cite in the section, return its surrounding sentence."""
    # Strip LaTeX comments
    cleaned = "\n".join(line.split("%", 1)[0] for line in section_text.splitlines())
    out: list[dict[str, Any]] = []
    for m in _CITE_RE.finditer(cleaned):
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        # Capture the surrounding sentence (look back to last . or paragraph break)
        start = max(0, m.start() - 250)
        end = min(len(cleaned), m.end() + 200)
        window = cleaned[start:end]
        # Find sentence containing m within the window
        rel = m.start() - start
        before = window[:rel]
        after = window[rel:]
        # Find last sentence break before, first sentence break after
        prev = max(before.rfind("."), before.rfind("?"), before.rfind("!"),
                   before.rfind("\n\n"))
        nxt_candidates = [after.find(c) for c in (".", "?", "!") if after.find(c) >= 0]
        nxt = min(nxt_candidates) if nxt_candidates else len(after)
        sentence = (before[prev + 1:] + after[:nxt + 1]).strip()
        sentence = re.sub(r"\s+", " ", sentence)
        out.append({"cite_keys": keys, "sentence": sentence})
    return out


# Path resolved at import-time so tests and CLI both find the same file.
_BENCHMARK_TARGETS_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills" / "shared-references" / "benchmark-targets.json"
)


def _load_benchmark_targets(path: Path | None = None) -> dict[str, Any] | None:
    """Read benchmark-targets.json. Returns None on any error so the
    dashboard degrades cleanly to "no diff panel" rather than blowing up."""
    p = path or _BENCHMARK_TARGETS_PATH
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _extract_run_metrics(
    stats: dict[str, Any] | None,
    structural_audit: dict[str, Any] | None,
    sections: dict[str, str] | None,
    bib_path: Path | None = None,
) -> dict[str, Any]:
    """Pull the small set of numbers the diff panel needs out of stats +
    the structural audit. Missing data → key absent (so the renderer
    can render '—' and skip the comparison).

    ``bib_path`` (optional) lets the caller point at the assembled
    ``references.bib``; if supplied, the entry count there overrides
    ``stats.papers.in_corpus``. The two figures can diverge when the
    writer hand-extends the bib with adjacent literature beyond the
    closed paper pool, and the bib count is the figure benchmark
    comparison should use.
    """
    out: dict[str, Any] = {}
    s = stats or {}
    doc = s.get("document") or {}
    papers = s.get("papers") or {}
    cites = s.get("citations") or {}
    outline = s.get("outline") or {}

    if doc.get("estimated_pages"):
        out["pages"] = doc["estimated_pages"]
    if doc.get("body_words"):
        out["body_words"] = doc["body_words"]
    # Prefer references.bib entry count (what bibtex actually compiles
    # against) over the filtered.jsonl count (what /survey-search
    # gathered before bib hand-extension).
    bib_count: int | None = None
    if bib_path is not None and bib_path.is_file():
        try:
            bib_text = bib_path.read_text(encoding="utf-8")
            bib_count = len(re.findall(
                r"^\s*@\w+\s*\{", bib_text, re.MULTILINE,
            ))
        except OSError:
            bib_count = None
    if bib_count:
        out["bib_entries"] = bib_count
    elif papers.get("in_corpus"):
        out["bib_entries"] = papers["in_corpus"]
    if cites.get("total"):
        out["inline_citations"] = cites["total"]
    if outline.get("sections"):
        out["top_level_sections"] = outline["sections"]

    # Citation density: parse "23.49" out of audit.invariants.citation_density.value
    invs = (structural_audit or {}).get("invariants") or {}
    cd = invs.get("citation_density")
    if isinstance(cd, dict):
        try:
            out["citation_density_per_1k_words"] = float(cd.get("value"))
        except (TypeError, ValueError):
            pass
    # Cross-cutting matrix presence: parse "matrix=yes" from the audit value.
    cc = invs.get("cross_cutting_matrix")
    if isinstance(cc, dict):
        out["cross_cutting_matrix_present"] = (
            "yes" in str(cc.get("value", "")).lower()
        )
    # Open/future counts and conclusion words come from the same audit pass.
    op = invs.get("open_problems_pairing")
    if isinstance(op, dict):
        m = re.search(r"open=(\d+),\s*future=(\d+)", str(op.get("value", "")))
        if m:
            out["open_problems_items"] = int(m.group(1))
            out["future_directions_items"] = int(m.group(2))
    cr = invs.get("conclusion_reframe")
    if isinstance(cr, dict):
        m = re.search(r"(\d+)\s*words", str(cr.get("value", "")))
        if m:
            out["conclusion_words"] = int(m.group(1))
    csr = invs.get("contributions_section_refs")
    if isinstance(csr, dict):
        m = re.search(r"\d+/(\d+)", str(csr.get("value", "")))
        if m:
            out["contributions_items"] = int(m.group(1))
    return out


def _diff_status(
    metric_key: str,
    observed: Any,
    benchmark: Any,
    tolerance: dict[str, Any],
) -> tuple[str, str]:
    """Return ``(status, delta_str)``.

    Status is one of ``"ok"``, ``"warn"``, ``"fail"`` for the panel CSS;
    ``delta_str`` is the human-readable Δ (e.g. ``"−13"``, ``"+36 %"``).
    Boolean metrics use direct equality.
    """
    if observed is None:
        return ("warn", "—")
    if isinstance(benchmark, bool):
        ok = bool(observed) == benchmark
        return (("ok" if ok else "fail"),
                ("✓" if observed else "✗"))
    try:
        obs = float(observed)
        bm = float(benchmark)
    except (TypeError, ValueError):
        return ("warn", "—")
    delta = obs - bm
    if "abs" in tolerance:
        tol = float(tolerance["abs"])
        in_window = abs(delta) <= tol
        close = abs(delta) <= 2 * tol
    elif "rel" in tolerance:
        tol = float(tolerance["rel"]) * abs(bm) if bm else float(tolerance["rel"])
        in_window = abs(delta) <= tol
        close = abs(delta) <= 2 * tol
    else:
        in_window = delta == 0
        close = False
    status = "ok" if in_window else ("warn" if close else "fail")
    if "rel" in tolerance and bm:
        delta_str = f"{delta:+.0f} ({delta / bm:+.0%})"
    else:
        # Whole-number metrics render without trailing ".0"
        delta_str = (f"{int(delta):+d}" if delta == int(delta)
                     else f"{delta:+.1f}")
    return (status, delta_str)


def _render_benchmark_diff_panel(
    targets: dict[str, Any] | None,
    run_metrics: dict[str, Any],
) -> str:
    """Render the 'this run vs. benchmark' comparison table.

    Reads the benchmark constants and the per-metric tolerances from
    ``targets`` (parsed shared-references/benchmark-targets.json) and
    emits one row per metric the run can fill. Metrics the run cannot
    fill (data missing from stats + audit) show as ``—`` with a warn
    status — the gap surfaces honestly rather than silently skipping.
    """
    if not targets or not run_metrics:
        return ""
    bench = targets.get("benchmark") or {}
    tol = targets.get("tolerance") or {}
    metrics = targets.get("metrics") or []
    if not bench or not metrics:
        return ""

    # Infer benchmark cross_cutting_matrix_present from the nested object.
    if "cross_cutting_matrix" in bench and isinstance(
        bench["cross_cutting_matrix"], dict
    ):
        bench = {
            **bench,
            "cross_cutting_matrix_present":
                bool(bench["cross_cutting_matrix"].get("present")),
        }

    rows_html: list[str] = []
    n_metrics = 0
    n_ok = 0
    for spec in metrics:
        key = spec.get("key")
        if not key or key not in bench:
            continue
        n_metrics += 1
        observed = run_metrics.get(key)
        bm = bench[key]
        status, delta_str = _diff_status(key, observed, bm, tol.get(key) or {})
        if status == "ok":
            n_ok += 1
        unit = spec.get("unit") or ""
        label = spec.get("label") or key
        obs_str = "—" if observed is None else (
            "✓" if observed is True else
            "✗" if observed is False else
            f"{observed}{unit}"
        )
        bm_str = (
            "✓" if bm is True else
            "✗" if bm is False else
            f"{bm}{unit}"
        )
        rows_html.append(
            f'<tr class="{status}">'
            f'<td class="name">{html.escape(label)}</td>'
            f'<td class="value">{html.escape(obs_str)}</td>'
            f'<td class="bench">{html.escape(bm_str)}</td>'
            f'<td class="delta">{html.escape(delta_str)}</td>'
            f'</tr>'
        )

    if not rows_html:
        return ""

    summary_class = (
        "ok" if n_ok == n_metrics
        else "warn" if n_ok >= n_metrics * 0.6
        else "fail"
    )
    title = html.escape(bench.get("title", "benchmark"))
    parts: list[str] = ['<div class="bench-diff">']
    parts.append(
        f'<div class="bench-summary {summary_class}">'
        f'<strong>vs. benchmark:</strong> '
        f'{n_ok}/{n_metrics} metrics on target '
        f'<span class="bench-ref"> — {title}</span>'
        f'</div>'
    )
    parts.append('<table class="bench-table">')
    parts.append(
        '<thead><tr><th>Metric</th><th>This run</th>'
        '<th>Benchmark</th><th>Δ</th></tr></thead><tbody>'
    )
    parts.extend(rows_html)
    parts.append('</tbody></table></div>')
    return "".join(parts)


def _render_structural_panel(audit: dict[str, Any] | None) -> str:
    """Compact panel showing the 8 structural-template invariants from
    audit_writing.audit_structural_template. Each invariant becomes
    one row with its name, observed value, and pass/fail mark.

    When ``audit`` is None or has no invariants (e.g. the run hasn't
    been audited yet), this returns an empty string so the rest of
    the dashboard renders unaffected.
    """
    if not audit or not audit.get("invariants"):
        return ""
    out: list[str] = ['<div class="invariants">']
    score = audit.get("score", 0.0)
    passing = audit.get("passing", 0)
    total = audit.get("total", 8)
    summary_class = "ok" if passing >= 7 else ("warn" if passing >= 5 else "fail")
    out.append(
        f'<div class="invariants-summary {summary_class}">'
        f'<strong>Structural template:</strong> '
        f'{passing}/{total} invariants pass '
        f'(score {score})'
        f'</div>'
    )
    out.append('<table class="invariants-table">')
    out.append(
        '<thead><tr><th></th><th>Invariant</th>'
        '<th>Value</th><th>Issue</th></tr></thead><tbody>'
    )
    for name, inv in audit["invariants"].items():
        mark = "✓" if inv.get("ok") else "✗"
        cls = "pass" if inv.get("ok") else "fail"
        out.append(
            f'<tr class="{cls}">'
            f'<td class="mark">{mark}</td>'
            f'<td class="name">{html.escape(name)}</td>'
            f'<td class="value">{html.escape(str(inv.get("value", "")))}</td>'
            f'<td class="issue">{html.escape(str(inv.get("issue", "")))}</td>'
            f'</tr>'
        )
    out.append('</tbody></table></div>')
    return "".join(out)


def _render_meta_banner(stats: dict[str, Any]) -> str:
    """6-tile banner inspired by the L1-L5 benchmark survey's
    'N papers / N citations / N pages' trust-scaffold opener.

    Reads the schema produced by tools/build_run_stats.py. Tolerant
    to missing sub-keys (renders only what's available so partial
    stats files still work)."""
    p = stats.get("papers") or {}
    c = stats.get("citations") or {}
    t = stats.get("thesis") or {}
    d = stats.get("document") or {}
    tiles: list[tuple[str, str]] = []
    if p.get("in_corpus"):
        cov = int((p.get("coverage") or 0) * 100)
        tiles.append((f"{p['in_corpus']}",
                      f"papers ({p.get('cited', 0)} cited, {cov}% cov.)"))
    if c.get("total"):
        tiles.append((f"{c['total']}",
                      f"citations ({c.get('unique', 0)} unique)"))
    if t.get("argument_steps"):
        tiles.append((f"{t['argument_steps']}", "argument steps"))
    if t.get("anticipated_objections"):
        tiles.append((f"{t['anticipated_objections']}", "objections answered"))
    if stats.get("systems_compared"):
        tiles.append((f"{stats['systems_compared']}", "systems compared"))
    if d.get("estimated_pages"):
        tiles.append((f"~{d['estimated_pages']}", "pages"))
    if not tiles:
        return ""
    out = ['<div class="meta-banner">']
    for big, small in tiles:
        out.append(
            f'<div class="tile"><div class="big">{html.escape(big)}</div>'
            f'<div class="small">{html.escape(small)}</div></div>'
        )
    out.append('</div>')
    return "".join(out)


def build_html(
    sections: dict[str, str],
    claims_by_key: dict[str, dict[str, Any]],
    papers_by_key: dict[str, dict[str, Any]],
    thesis_doc: dict[str, Any] | None,
    run_id: str,
    stats: dict[str, Any] | None = None,
    structural_audit: dict[str, Any] | None = None,
    benchmark_targets: dict[str, Any] | None = None,
    bib_path: Path | None = None,
) -> str:
    """Compose the single-file HTML dashboard."""
    rows: list[dict[str, Any]] = []
    for sid in sorted(sections.keys()):
        # Section title from the first \section{} or fall back to id
        sec_text = sections[sid]
        title_m = _SECTION_RE.search(sec_text)
        sec_title = title_m.group(1) if title_m else sid
        for ctx in _extract_cite_contexts(sec_text):
            for key in ctx["cite_keys"]:
                claim_record = claims_by_key.get(key) or {}
                paper = papers_by_key.get(key) or {}
                rows.append({
                    "section_id":    sid,
                    "section_title": sec_title,
                    "cite_key":      key,
                    "sentence":      ctx["sentence"],
                    "paper_title":   paper.get("title", ""),
                    "arxiv_url":     _safe_arxiv_url(paper),
                    "source_url":    (su := _safe_source_url(paper)) and su[0],
                    "source_label":  su[1] if su else None,
                    "atomic_claims": claim_record.get("atomic_claims", []),
                    "what_argues":   claim_record.get("what_paper_argues", ""),
                })

    thesis_text = (thesis_doc or {}).get("thesis", "")
    n_rows = len(rows)
    n_cite_keys = len({r["cite_key"] for r in rows})
    n_sections = len(sections)
    n_with_claims = sum(1 for r in rows if r["atomic_claims"])

    rows_json = json.dumps(rows, ensure_ascii=False)

    parts: list[str] = []
    parts.append("<!doctype html>")
    parts.append('<html lang="en"><head>')
    parts.append('<meta charset="utf-8">')
    parts.append(f"<title>{html.escape(run_id)} — Evidence Dashboard</title>")
    parts.append("<style>")
    parts.append(_CSS)
    parts.append("</style>")
    parts.append("</head><body>")
    parts.append('<header>')
    parts.append('<h1>Evidence Dashboard</h1>')
    parts.append(f'<p class="run-id"><strong>Run:</strong> '
                 f'<code>{html.escape(run_id)}</code></p>')
    if thesis_text:
        parts.append(f'<p class="thesis"><strong>Thesis:</strong> '
                     f'{html.escape(thesis_text)}</p>')
    parts.append('<div class="stats">')
    parts.append(f'<span><strong>{n_rows}</strong> citation contexts</span>')
    parts.append(f'<span><strong>{n_cite_keys}</strong> distinct cite_keys</span>')
    parts.append(f'<span><strong>{n_sections}</strong> sections</span>')
    # Disambiguate: this counts citation contexts (rows above), NOT sections.
    # Previously, "X sections / Y with atomic claims" read as "Y of the X
    # sections have atomic claims" — wrong.
    parts.append(
        f'<span><strong>{n_with_claims}</strong> citation '
        f'contexts with atomic claims</span>'
    )
    parts.append('</div>')
    if stats:
        parts.append(_render_meta_banner(stats))
    if benchmark_targets:
        run_metrics = _extract_run_metrics(
            stats, structural_audit, sections, bib_path=bib_path,
        )
        parts.append(_render_benchmark_diff_panel(benchmark_targets, run_metrics))
    if structural_audit:
        parts.append(_render_structural_panel(structural_audit))
    parts.append('</header>')
    parts.append('<div class="filters">')
    parts.append('<input type="search" id="q" placeholder="Filter by cite_key, section, sentence text…" autofocus>')
    parts.append('<select id="claim_type">')
    parts.append('<option value="">all claim types</option>')
    for ct in ("empirical", "theoretical", "methodological", "critique"):
        parts.append(f'<option value="{ct}">{ct}</option>')
    parts.append('</select>')
    parts.append('</div>')
    parts.append('<div id="results"></div>')
    parts.append('<script>')
    parts.append(f"const ROWS = {rows_json};")
    parts.append(_JS)
    parts.append('</script>')
    parts.append('</body></html>')
    return "".join(parts)


_CSS = r"""
:root {
  --bg: #fafafa; --panel: #ffffff; --border: #e3e3e3; --accent: #4338ca;
  --muted: #6b7280; --code: #f3f4f6; --highlight: #fef3c7;
}
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
       margin: 0; padding: 24px; max-width: 1100px; margin: 0 auto; color: #111;
       background: var(--bg); }
header { padding-bottom: 16px; border-bottom: 2px solid var(--accent); margin-bottom: 16px; }
h1 { margin: 0 0 8px 0; font-size: 24px; }
.run-id, .thesis { margin: 4px 0; color: var(--muted); }
.thesis { font-style: italic; max-width: 80ch; }
.stats { display: flex; gap: 18px; margin-top: 8px; flex-wrap: wrap; }
.stats span { background: var(--code); padding: 4px 10px; border-radius: 4px; font-size: 13px; }
.meta-banner { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
                gap: 12px; margin-top: 16px; }
.meta-banner .tile { background: linear-gradient(180deg, #fff, var(--code));
                     border: 1px solid var(--border); border-radius: 8px;
                     padding: 12px 14px; text-align: center; }
.meta-banner .tile .big { font-size: 28px; font-weight: 700; color: var(--accent);
                          line-height: 1.1; }
.meta-banner .tile .small { font-size: 12px; color: var(--muted); margin-top: 4px;
                             text-transform: uppercase; letter-spacing: 0.04em; }
.filters { display: flex; gap: 10px; margin-bottom: 16px; }
#q { flex: 1; padding: 10px 14px; font-size: 15px;
     border: 1px solid var(--border); border-radius: 6px; }
#claim_type { padding: 10px 14px; font-size: 15px;
              border: 1px solid var(--border); border-radius: 6px; }
.row { background: var(--panel); border: 1px solid var(--border); border-radius: 6px;
       padding: 14px 16px; margin-bottom: 12px; }
.row .meta { display: flex; gap: 12px; align-items: center;
             margin-bottom: 8px; flex-wrap: wrap; }
.row .meta .key { font-family: ui-monospace, "SF Mono", Menlo, monospace;
                  background: var(--code); padding: 2px 8px; border-radius: 4px; }
.row .meta .section { color: var(--muted); font-size: 13px; }
.row .meta a { color: var(--accent); text-decoration: none; font-size: 13px; }
.row .meta a:hover { text-decoration: underline; }
.row .sentence { line-height: 1.55; margin: 6px 0 10px 0; }
.row .sentence em { background: var(--highlight); font-style: normal;
                    padding: 1px 4px; border-radius: 2px; }
.row .what-argues { font-size: 13px; color: var(--muted); margin: 4px 0 8px 0;
                    border-left: 3px solid var(--border); padding-left: 10px; }
.row .claims { margin: 0; padding: 0; list-style: none; }
.row .claims li { background: #fcfcfc; border-left: 3px solid var(--accent);
                  padding: 8px 12px; margin: 6px 0; border-radius: 0 4px 4px 0; }
.row .claims li .quote { font-style: italic; color: #333; }
.row .claims li .anchor { color: var(--muted); font-size: 12px;
                          font-family: ui-monospace, monospace; }
.row .claims li .ctype { display: inline-block; padding: 1px 6px;
                         background: var(--accent); color: white;
                         font-size: 11px; border-radius: 3px; margin-right: 6px; }
.empty { color: var(--muted); padding: 32px; text-align: center; }
.invariants { margin-top: 16px; border: 1px solid var(--border);
              border-radius: 8px; overflow: hidden; }
.invariants-summary { padding: 10px 14px; font-size: 14px;
                      border-bottom: 1px solid var(--border); }
.invariants-summary.ok   { background: #ecfdf5; color: #065f46; }
.invariants-summary.warn { background: #fef3c7; color: #92400e; }
.invariants-summary.fail { background: #fee2e2; color: #991b1b; }
.invariants-table { width: 100%; border-collapse: collapse;
                    font-size: 13px; background: #fff; }
.invariants-table th { text-align: left; padding: 6px 10px;
                       background: var(--code); color: var(--muted);
                       font-weight: 500; font-size: 12px;
                       text-transform: uppercase; letter-spacing: 0.04em; }
.invariants-table td { padding: 6px 10px;
                       border-top: 1px solid var(--border);
                       vertical-align: top; }
.invariants-table tr.pass td.mark { color: #059669; font-weight: 700; }
.invariants-table tr.fail td.mark { color: #dc2626; font-weight: 700; }
.invariants-table td.name { font-family: ui-monospace, "SF Mono", Menlo, monospace;
                            font-size: 12px; color: #374151; white-space: nowrap; }
.invariants-table td.value { color: var(--muted); font-size: 12px; }
.invariants-table td.issue { color: #b91c1c; font-size: 12px; }
.bench-diff { margin-top: 16px; border: 1px solid var(--border);
              border-radius: 8px; overflow: hidden; }
.bench-summary { padding: 10px 14px; font-size: 14px;
                 border-bottom: 1px solid var(--border); }
.bench-summary.ok   { background: #ecfdf5; color: #065f46; }
.bench-summary.warn { background: #fef3c7; color: #92400e; }
.bench-summary.fail { background: #fee2e2; color: #991b1b; }
.bench-summary .bench-ref { color: var(--muted); font-style: italic;
                            font-weight: normal; font-size: 12px; }
.bench-table { width: 100%; border-collapse: collapse;
               font-size: 13px; background: #fff; }
.bench-table th { text-align: left; padding: 6px 10px;
                  background: var(--code); color: var(--muted);
                  font-weight: 500; font-size: 12px;
                  text-transform: uppercase; letter-spacing: 0.04em; }
.bench-table td { padding: 6px 10px;
                  border-top: 1px solid var(--border); }
.bench-table tr.ok   td.delta { color: #059669; font-weight: 600; }
.bench-table tr.warn td.delta { color: #b45309; font-weight: 600; }
.bench-table tr.fail td.delta { color: #dc2626; font-weight: 700; }
.bench-table td.name  { font-family: ui-monospace, "SF Mono", Menlo, monospace;
                        font-size: 12px; color: #374151; }
.bench-table td.value { font-weight: 600; }
.bench-table td.bench { color: var(--muted); }
"""


_JS = r"""
function escapeHtml(s) {
  const map = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'};
  return String(s).replace(/[&<>"']/g, m => map[m]);
}
function highlightCite(sentence, key) {
  // Replace \cite{...} containing this key with an emphasised span
  const re = /\\\\cite[tp]?\*?\{[^}]*\}/g;
  return escapeHtml(sentence).replace(re, m =>
    m.includes(escapeHtml(key)) ? '<em>' + m + '</em>' : m
  );
}
function render(rows) {
  const out = document.getElementById('results');
  if (!rows.length) {
    out.innerHTML = '<div class="empty">No matching evidence rows.</div>';
    return;
  }
  const html = rows.map(r => {
    const url = r.source_url
      ? `<a href="${escapeHtml(r.source_url)}" target="_blank" rel="noopener">${escapeHtml(r.source_label || 'source')} ↗</a>`
      : '<span style="color: #aaa">no source URL</span>';
    const claims = (r.atomic_claims || []).map(c => `
      <li>
        <span class="ctype">${escapeHtml(c.claim_type || '')}</span>
        <span class="quote">"${escapeHtml(c.quote || '')}"</span>
        <span class="anchor">— ${escapeHtml(c.anchor || '')}</span>
      </li>
    `).join('');
    const claimsBlock = claims
      ? `<ul class="claims">${claims}</ul>`
      : '<div style="color:#aaa;font-size:13px">No atomic_claims mined for this paper yet.</div>';
    const argues = r.what_argues
      ? `<div class="what-argues"><strong>What this paper argues:</strong> ${escapeHtml(r.what_argues)}</div>`
      : '';
    return `
      <article class="row">
        <div class="meta">
          <span class="key">${escapeHtml(r.cite_key)}</span>
          <span class="section">${escapeHtml(r.section_id)} — ${escapeHtml(r.section_title)}</span>
          ${url}
        </div>
        <div class="sentence">${highlightCite(r.sentence, r.cite_key)}</div>
        ${argues}
        ${claimsBlock}
      </article>`;
  }).join('');
  out.innerHTML = html;
}
function applyFilter() {
  const q = document.getElementById('q').value.toLowerCase().trim();
  const ct = document.getElementById('claim_type').value;
  let rows = ROWS;
  if (q) {
    rows = rows.filter(r =>
      r.cite_key.toLowerCase().includes(q) ||
      r.section_id.toLowerCase().includes(q) ||
      r.section_title.toLowerCase().includes(q) ||
      r.sentence.toLowerCase().includes(q) ||
      (r.what_argues || '').toLowerCase().includes(q)
    );
  }
  if (ct) {
    rows = rows.filter(r =>
      (r.atomic_claims || []).some(c => c.claim_type === ct)
    );
  }
  render(rows);
}
document.getElementById('q').addEventListener('input', applyFilter);
document.getElementById('claim_type').addEventListener('change', applyFilter);
render(ROWS);
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run_dir", type=Path)
    p.add_argument("--output", required=True, type=Path,
                   help="Where to write the single HTML file")
    args = p.parse_args(argv)

    run_dir: Path = args.run_dir.expanduser().resolve()
    sections_dir = run_dir / "5_paper" / "sections"
    if not sections_dir.exists():
        print(f"ERROR: sections dir not found: {sections_dir}", file=sys.stderr)
        return 2

    sections = {f.stem: f.read_text(encoding="utf-8")
                for f in sorted(sections_dir.glob("*.tex"))}

    claims = _load_jsonl(run_dir / "1_search" / "claims_cache.jsonl")
    claims_by_key = {r["cite_key"]: r for r in claims if r.get("cite_key")}

    papers = _load_jsonl(run_dir / "1_search" / "filtered.jsonl")
    papers_by_key = {(p.get("cite_key") or p.get("paper_id")): p
                     for p in papers if (p.get("cite_key") or p.get("paper_id"))}

    thesis_doc = None
    th_path = run_dir / "2_thesis" / "thesis.json"
    if th_path.exists():
        thesis_doc = json.loads(th_path.read_text(encoding="utf-8"))

    # Optional: trust-scaffold banner from build_run_stats.py output.
    stats = None
    stats_path = run_dir / "5_paper" / "stats.json"
    if stats_path.exists():
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stats = None  # silently degrade on a malformed stats file

    # Live structural-template invariants. Computed from the same code
    # path the audit gate uses so the dashboard cannot drift from the
    # gate verdict.
    outline_doc = None
    outline_path = run_dir / "4_outline" / "outline.json"
    if outline_path.exists():
        try:
            outline_doc = json.loads(outline_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            outline_doc = None
    bib_path = run_dir / "5_paper" / "references.bib"
    try:
        structural_audit = _aw.audit_structural_template(
            sections, bib_path if bib_path.exists() else None, outline_doc,
        )
    except Exception:  # noqa: BLE001 — audit is decorative, never block render
        structural_audit = None

    run_id = run_dir.name

    # benchmark-targets.json drives the 'this run vs. benchmark' diff
    # panel. Lives in shared-references/ so docs and the dashboard read
    # the same constants.
    benchmark_targets = _load_benchmark_targets()

    html_doc = build_html(sections, claims_by_key, papers_by_key, thesis_doc,
                          run_id, stats=stats,
                          structural_audit=structural_audit,
                          benchmark_targets=benchmark_targets,
                          bib_path=bib_path if bib_path.exists() else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html_doc, encoding="utf-8")
    print(f"✅ Evidence dashboard → {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
