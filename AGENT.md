---
name: autosurvey-agent-guide
description: Agent-facing guide for the AutoSurvey skill pack — pipeline, skills, tools, per-run layout, verification tests — plus the Karpathy behavioral guidelines every contributor agent follows.
license: MIT
---

# AutoSurvey — Agent Guide

AutoSurvey generates high-quality survey papers (50–200 citations, length scaled
to the brief's scope — typically ≈ 25–45+ pages, no page cap, LaTeX → PDF) from a
**structured brief** (free-form natural-language markdown file). It is built as
Claude Code / Codex skills (SKILL.md workflow files), with per-run state in
`./.autosurvey/runs/<id>/` (under the current working directory by default;
override the base with `AUTOSURVEY_RUNS_DIR`).

The pipeline is thesis-driven and runs in 3 phases (Drafting / Arguing /
Polishing) with a per-section inner loop in Phase 2. Key artifacts:
`2_thesis/thesis.json`, `1_search/claims_cache.jsonl`,
`4_outline/outline.json` (with `argues_for_thesis_step` +
`argument_skeleton` + optional `tier_axis`), and the
`survey.evidence.html` dashboard. The data contracts live under
`skills/shared-references/` — see `thesis-contract.md`, `claims-contract.md`,
`brief-contract.md`, `argument-skeleton.md`, `narrative-scaffolding.md`, and
`reviewer-personas.md`.

> This file is the single agent-facing entrypoint. The first part is the
> AutoSurvey reference; the **Karpathy Guidelines** at the end are the
> behavioral rules for any agent writing or refactoring code in this repo.

---

## Quick Start

The `/survey-*` examples below are typed inside a Claude Code or Codex
session, not in a shell. In **Cursor**, invoke a skill by name from chat
(e.g. "use the survey-run skill on <brief>") rather than as a slash
command — each `survey-*/SKILL.md` carries `name:` + `description:`
frontmatter for discovery, and `tools/install.sh` symlinks them into
`~/.cursor/skills/` alongside `~/.claude/skills/` and `~/.codex/skills/`.

```text
# Full end-to-end run (length scales with scope, ≈ 25–45+ pages, 20–60 minutes)
/survey-run --brief ~/my-brief.md

# Smaller corpus (paper count cap; pipeline depth unchanged)
/survey-run --brief ~/my-brief.md --max-papers 30

# Resume an interrupted run
/survey-run --brief ~/my-brief.md --resume moe-in-llms-20260527-143022

# Re-run from a specific stage (e.g. after editing brief.parsed.json)
/survey-run --brief ~/my-brief.md --resume <id> --from-stage refine_brief
```

Besides the `/survey-run` orchestrator, the 7 sub-skills can also be invoked
individually; each reads the current run's state:

```text
/survey-search --max-papers 100 # uses brief.parsed.json from current run
/survey-thesis                  # pick a contestable thesis
/survey-outline                 # outline-sketch (taxonomy + structure)
/survey-write                   # per-section inner loop (claims + figures)
/survey-review                  # auto-improvement loop (2 personas, 2 rounds)
/survey-verify                  # hard gate + claim audit + numeric_grounding
/survey-pivot                   # mid-run thesis pivot
```

`refine_brief` is **not** a slash command — it is the tool
`tools/refine_brief.py`, run by `/survey-run` as the first Drafting substep.
Invoke it directly (`python3 tools/refine_brief.py …`) or re-trigger it with
`/survey-run … --resume <id> --from-stage refine_brief`.

Valid `--from-stage` values: `refine_brief | search | thesis | outline_sketch
| arguing | review | audits | compile` (aliases: `outline`→`outline_sketch`,
`write`→`arguing`, `verify`→`audits`).

---

## Brief-Driven Invocation

AutoSurvey requires a structured brief as the canonical input:

```text
/survey-run --brief <path/to/brief.md>
```

**Stage 0 (`refine_brief`)** runs first: one LLM call extracts a structured
`brief.parsed.json` from the free-form `brief.md`. A second deterministic
pass synthesises the per-paper extraction schema
(`brief.derived_schema.json`) from the brief's natural-language
extraction hints (see `shared-references/claims-contract.md` for the
schema's minimal surface).

Both artifacts are user-editable JSON; modify them directly and re-run
with `--from-stage refine_brief` to regenerate both the parsed brief and the
derived schema downstream.

Calling `/survey-run` without `--brief` (or with the bare positional
topic-string form) fails fast and points the user at `examples/briefs/`
plus the README ["Writing a brief"](README.md#writing-a-brief) section.

---

## Pipeline (phase model)

```
/survey-run --brief brief.md
     │
     ├─ Phase 1 — Drafting
     │ ├─ refine_brief → brief.parsed.json (LLM extraction + terminal display)
     │ ├─ search → 1_search/filtered.jsonl + tech_reports.jsonl
     │ │ + blogs.jsonl + websites.jsonl
     │ │ + references.bib (incl. @misc{} for non-paper sources)
     │ │ [post] LLM-as-filter applies brief.scope.exclude
     │ ├─ thesis → 2_thesis/thesis.json (contestable claim + 5 argument
     │ │ steps + ≥2 anticipated objections; LOAD-BEARING)
     │ └─ outline_sketch → 4_outline/outline.json (sections bind to
     │ thesis argument_steps; declares the cross-cutting comparison
     │ matrix; optional tier_axis)
     │ [post] tools/validate_outline.py — closed-set repair
     │
     ├─ Phase 2 — Arguing (per-section inner loop, see survey-write/SKILL.md)
     │ for each section:
     │ ├─ lazy_mine_claims → 1_search/claims_cache.jsonl (only papers cited)
     │ ├─ write_skeleton → sections/<id>.skeleton.md (5 H3 buckets)
     │ ├─ compose_tex → sections/<id>.tex with % [CLAIM]/[STEELMAN]/
     │ │ [EVIDENCE]/[CONCESSION]/[SO-WHAT] anchors
     │ ├─ self_review → fresh-thread audit pass
     │ └─ maybe figure/table on demand — agent decides count and type;
     │ helpers available are gen_taxonomy_tikz / gen_timeline /
     │ gen_scaling_plot / build_dimension_tables /
     │ scaffold_cross_cutting_matrix. Every aux figure/table must be
     │ `\ref{}`'d from prose to pass invariant 4.
     │
     └─ Phase 3 — Polishing
         ├─ review → 2 reviewer personas (Senior + Skeptic) +
         │ author response with accept/partial/reject
         ├─ audits → verify_evidence.py (full-text quotes + numbers) +
         │ validate_artifacts.py + audit_writing.py +
         │ bib_hygiene.py + prose_polish.py +
         │ verify_papers.py
         │ [GATE] tools/verify_survey_audits.sh must exit 0
         └─ compile → main.pdf (tectonic preferred; latexmk/pdflatex
                                  acceptable) + 5_paper/survey.evidence.html
                                  dashboard + optional survey.html web
                                  preview (best-effort; skipped if pandoc
                                  is not installed)
```

---

## Skills (8 SKILL.md files)

`refine_brief` is **not** a skill — it is the tool `tools/refine_brief.py`
invoked by `/survey-run` as the first Drafting substep. See the Tools
section below.

| Skill | Purpose |
|---|---|
| `/survey-run` | Orchestrator — requires `--brief`; runs the 3 phases, manages state.json, supports `--resume`. The schema sanity check refuses to operate on a state.json whose `phases` dict is not exactly `{drafting, arguing, polishing}`. |
| `/survey-search` | Multi-source search (papers + tech reports + blogs + websites) + scope.exclude filter + dedup + bib |
| `/survey-thesis` | **LOAD-BEARING** — pick a contestable thesis from candidates, write argument_steps + anticipated_objections, gate on user pick |
| `/survey-outline` | Outline-sketch (cluster + taxonomy + outline merged); sections bind to thesis argument_steps; optional tier_axis |
| `/survey-write` | Per-section inner loop (Phase 2): claim mining + 5-anchor skeleton + on-demand figures + self-review |
| `/survey-review` | Polishing — 2 personas (Senior + Skeptic) + author response + REVIEWER_BIAS_GUARD; 2 rounds |
| `/survey-verify` | Hard gate + claim audit + numeric_grounding + full-text evidence verification + kill-argument |
| `/survey-pivot` | Mid-run thesis pivot (used when verify reveals an irreparable thesis flaw) |

---

## Tools

All helpers live in `tools/`. AutoSurvey is fully self-contained — no external
repo dependency.

### Brief, extraction & schema synthesis

| Tool | Purpose |
|---|---|
| `tools/refine_brief.py` | Stage 0 validator: takes the agent's candidate JSON + brief.md and writes brief.parsed.json (the agent does the LLM extraction; the tool only validates + persists). |
| `tools/extract_paper_card.py` | Paper-card backend with three deterministic sub-modes (`--validate-schema`, `--fetch-all`, `--write-cards`). Makes no LLM calls; the agent supplies the per-paper synthesis in `/survey-write`'s inner loop. |

### Quality, figures & bibliography

| Tool | Purpose |
|---|---|
| `tools/bib_generator.py` | Convert `filtered.jsonl` (and tech_reports / blogs / websites) → `references.bib`. Default is side-effect-free; `--update-input` opts in to writing `cite_key` back into the input file (used by pipeline drivers). |
| `tools/bib_hygiene.py` | Dead-entry removal, char escaping, unicode normalization in bib |
| `tools/build_dimension_tables.py` | Wide booktabs LaTeX comparison tables built from `cards.jsonl`; supports `--mode fields` (canonical) and `--mode decision` (decision-summary). Sentinel-pattern LaTeX escape preserves backslashes correctly. |
| `tools/gen_taxonomy_tikz.py` | TikZ taxonomy figure: tree, radial, or matrix layout (matrix requires `outline.tier_axis`). |
| `tools/gen_timeline.py` | matplotlib timeline. Three modes: `--milestones <json>` renders a reference-style single-axis curated milestone timeline (preferred for the survey's headline timeline figure); otherwise a lane plot when clusters are present; year-bar chart as fallback. |
| `tools/gen_scaling_plot.py` | Scaling-trend scatter (params/tokens × time × region). Fail-fast on missing inputs with actionable hint. |
| `tools/prose_polish.py` | Strip AI-isms, clutter, normalize unicode→LaTeX, flag long sentences; narrative-pillars + 5-anchor scan; advisory banner distinguishes auto-fix-clean from narrative-warning state. |
| `tools/reverse_outline.py` | Topic-sentence narrative coherence audit (`/survey-write` Step 9). |
| `tools/validate_outline.py` | Closed-set repair on outline.json + `argues_for_thesis_step` checks (outlines without thesis-bound fields pass through unchanged). |
| `tools/validate_artifacts.py` | **schema audit** — thesis schema + claims_cache schema + cite_key closed set + decision_summary. Collapses noise on runs without a derived schema. |
| `tools/audit_writing.py` | **writing-quality gate** — 5-anchor coverage, narrative pillars, thesis coherence, Open-Problems 4-bucket, claim grounding, and the 8 structural-template invariants. |
| `tools/verify_evidence.py` | **evidence-fidelity gate** — verifies every mined `quote` appears (verbatim/near) in the cited paper's *full text* (`1_search/.cache/`), and that each quantitative number in a numeric+cited sentence appears in some cited source. Catches hallucinated quotes and unsourced numbers the abstract-level grounding misses; falls back to abstracts (reduced strength) when full text wasn't fetched. |
| `tools/quality_eval.py` | **semantic quality standard** (LLM-as-judge). `prepare` assembles a judge packet (rubric + thesis + full prose + stats) and an empty verdict template; the agent scores each rubric dimension 1-5; `score` validates the verdict, computes a weighted 0-100 overall, and compares the regression bar. Measures thesis/synthesis/insight/evidence/coverage/structure/readability — what structure gates can't. |
| `tools/build_evidence_dashboard.py` | **click-through evidence dashboard** — single static `survey.evidence.html` listing every `\cite{}` with claim quotes. Reads `5_paper/stats.json` if present and renders a 6-tile meta-banner at the top. |
| `tools/build_run_stats.py` | **quantitative meta-narrative** — aggregates papers/citations/argument_steps/systems/pages into `5_paper/stats.json` plus a one-paragraph human preview the agent pastes into the Introduction Hook. Trust-scaffold for the dashboard banner and the abstract opener. |
| `tools/scaffold_cross_cutting_matrix.py` | Emits a populated cross-cutting comparison matrix `.tex` from `outline.tier_axis` + the cited systems set, satisfying invariant 4. |
| `tools/scaffold_related_surveys.py` | Generates the `Related Surveys` subsection scaffold from the closed-set anchor surveys list. |
| `tools/pair_open_future.py` | Pairs entries in `open_problems.tex` 1:1 with `future_directions.tex` to satisfy invariant 6 (open-problems pairing). |
| `tools/verify_survey_audits.sh` | Compile-gate verifier (reads `CITATION_VERIFY.json`; runs validate_artifacts + audit_writing at the strictest level). |
| `tools/_latex_text.py` | Shared LaTeX text-escaping helper used by the figure / table generators (internal). |

### Search & verification

| Tool | Purpose |
|---|---|
| `tools/arxiv_fetch.py` | arXiv API search |
| `tools/semantic_scholar_fetch.py` | Semantic Scholar search |
| `tools/openalex_fetch.py` | OpenAlex search (broad multi-disciplinary coverage; covers ACL Anthology and PubMed indexing) |
| `tools/tech_report_fetch.py` | Tier 1+2 lab/vendor blog feeds (HuggingFace, DeepMind, OpenAI, Anthropic, ...) |
| `tools/blog_fetch.py` | Tier 3 curated personal blogs (Lil'Log, Sebastian Raschka, Eugene Yan, Nathan Lambert) |
| `tools/website_fetch.py` | GitHub READMEs / HuggingFace model cards / generic websites |
| `tools/verify_papers.py` | 3-layer paper-existence verification |
| `tools/check_anchor_coverage.py` | Anchor-coverage gate — measures whether search collected a topic's must-have methods/models/benchmarks; fails below `--min` so an under-collected corpus is caught before downstream stages. |
| `tools/snowball_citations.py` | Citation-graph recall expansion — walks each seed's OpenAlex references + citers and emits candidates linked to several seeds (co-citation / bibliographic coupling), in OpenAlex-search shape so they flow through the normal dedup/scope/verify pipeline. Recall tool; precision is the scope filter's job. |

### Source registry

| File | Purpose |
|---|---|
| `tools/source_registry.json` | Tier 1+2+3 source whitelist (lab blogs / personal blogs) |
| `tools/affiliation_to_region.json` | Affiliation → region lookup (Stanford → US, Tsinghua → CN, ...) |

### Install / venue helpers

| Script | Purpose |
|---|---|
| `tools/install.sh` | Symlinks `skills/survey-*` into `~/.claude/skills/` + `~/.codex/skills/` + `~/.cursor/skills/` (whichever agent dirs exist; `--claude-only` / `--codex-only` / `--cursor-only` narrow it); writes `AUTOSURVEY_TOOLS` to your shell profile. |
| `tools/uninstall.sh` | Removes the symlinks. |
| `tools/fetch_venue_template.sh` | Copies the bundled venue `.sty` into a run dir. |

### LaTeX engine (system-installed)

`tectonic` is preferred (single binary, auto-fetches packages):
```bash
brew install tectonic # macOS
apt-get install tectonic # Linux
```

Acceptable alternatives: `latexmk`, `pdflatex`, `xelatex`, `lualatex` (all from
`texlive-full` or `mactex`).

---

## Shared References

Located in `skills/shared-references/`:

| File | Purpose |
|---|---|
| `benchmark-targets.json` | **Single source of truth** for structural-quality thresholds (citation density, matrix size, open/future counts, conclusion words …). Read by the audit gate and the dashboard diff panel. |
| `quality-rubric.json` | **Semantic quality rubric** for the LLM-judge (`tools/quality_eval.py`): 7 weighted dimensions (thesis / synthesis / insight / evidence / coverage / structure / readability, 1-5 with anchored descriptors) + the regression bar. The four content edges carry 70% of weight. |
| `structural-template.md` | The 8 submission-grade structural invariants the audit enforces. |
| `brief-contract.md` | Canonical schema for brief.parsed.json (refine_brief output). |
| `thesis-contract.md` | Schema for thesis.json (contestable claim + argument_steps + anticipated_objections). |
| `claims-contract.md` | Schema for claims_cache.jsonl + the derived per-paper extraction surface. |
| `argument-skeleton.md` | The 5-anchor (Claim / Steelman / Evidence / Concession / So-what) section skeleton. |
| `narrative-scaffolding.md` | Narrative-pillars + topic-sentence coherence patterns. |
| `reviewer-personas.md` | Senior + Skeptic reviewer definitions used in `/survey-review`. |
| `reviewer-independence.md` | REVIEWER_BIAS_GUARD invariant (fresh threads, no continuation). |
| `assurance-contract.md` | 6-state verdict schema (PASS/WARN/FAIL/NOT_APPLICABLE/BLOCKED/ERROR); pipeline runs at the strictest level. |
| `survey-writing-principles.md` | Synthesis-not-summary patterns, 5-part survey abstract, banana rule, AI-ism stripping. |
| `conclusion-template.md` | The re-framing conclusion shape (not a bullet-list summary). |

---

## No quality knobs

The pipeline is intentionally knob-free. There is no `--effort`, no
`--assurance`, no `--lite/balanced/max` switch. Every `/survey-run`:

- extracts the **full priority chain** for each paper (S2 OpenAccess
  → arXiv PDF → HTML scrape → abstract fallback) up to a 40 K char
  budget;
- writes each section at full analytical depth (≥ ~550 words per
  subsection as a *floor*, scaling with the section's material — not a
  flat per-section cap), 5 parallel workers, so total length follows the
  brief's scope toward the ~45-pp benchmark rather than collapsing every
  survey to the same ~20 pages;
- runs 2 review rounds with 2 personas each;
- runs **all** audits (hard gate, claim audit, numeric grounding,
  full-text evidence verification, trend audit, kill-argument) at the
  strictest level, blocking compile on any FAIL.

`--auto-confirm` skips the human checkpoint between review rounds for
CI use. That is the only remaining operator-facing knob.

The `tools/audit_writing.py` and `tools/verify_survey_audits.sh`
helpers still accept `--assurance draft|polished|submission` for
out-of-band debugging — the pipeline never selects anything other
than `submission`.

---

## Environment Variables

Primary LLM work (refine_brief / extract / outline / write / review) is done
by the host agent itself (Claude Code or Codex) — the user does **not** set
a separate `LLM_API_KEY` for those stages. The handful of env vars below are
all optional and only relevant for adjacent integrations:

| Variable | What it gates |
|---|---|
| `SEMANTIC_SCHOLAR_API_KEY` | Higher Semantic Scholar rate limit; not required |
| `AUTOSURVEY_VERIFY_EMAIL` | Identifies you to CrossRef during paper-existence verification (raises rate limit) |
| `GEMINI_API_KEY` | Gemini-rendered illustration option for `/survey-write`; without it, the standard figure set is still emitted |
| `AUTOSURVEY_TOOLS` | Pinned location of `tools/`. Required when skills are invoked from a directory that is not the AutoSurvey checkout (i.e. the user-level install path). `tools/install.sh` writes this to your shell profile automatically. |

A few standalone Python helpers under `tools/` (e.g. `python3
tools/refine_brief.py`) are the only consumers of the
`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` environment variables —
an alternative back-end for headless / non-agent runs that is not
exercised by the slash-command flow. Set them via `export` in your shell
or a `.env` file you manage yourself; the repo no longer ships an
`.env.example` template.

---

## Venue Templates

The default is the **reader-oriented single-column survey template**
(`\documentclass[11pt]{article}` + table of contents, PDF bookmarks, styled
headings, running header — no external `.sty` required). A conference style
optimises for blind-submission density; a survey optimises for navigation
and sustained reading, so the reader template is the default. `survey-write`
selects it via `--venue generic` (the default); the full preamble lives in
`skills/survey-write/SKILL.md` Step 10.

To target a conference instead, pass `--venue neurips|acl|ieee` (or edit the
`\documentclass` / `\usepackage` preamble by hand — the section files,
citations, and figures are venue-agnostic). The bundled NeurIPS 2024 `.sty`
ships in `skills/survey-write/templates/` and is copied into a run by
`tools/fetch_venue_template.sh`:

```bash
# Reader-oriented survey template (default) — no .sty needed.

# Opt into the bundled NeurIPS 2024 sty:
bash tools/fetch_venue_template.sh ~/.autosurvey/runs/<id> neurips

# Other venues: download manually + drop into <run>/5_paper/
# - ICLR: iclr2024_conference.sty (from openreview.net)
# - ICML: icml2024.sty (from icml.cc)
# - ACL: acl.sty (from aclweb.org)
# - IEEE: IEEEtran.cls (from CTAN, also in TeXLive)
```

---

## Tool Resolution

AutoSurvey skills resolve helpers through a single env var:

```bash
AUTOSURVEY_TOOLS="${AUTOSURVEY_TOOLS:-$(git rev-parse --show-toplevel)/tools}"
```

This points at `<repo>/tools/`. All helpers — search (arxiv / semantic_scholar
/ openalex / tech_report / blog / website), verification, brief refinement
(refine_brief), per-paper extraction (extract_paper_card), figures
(taxonomy / timeline / scaling / dimension tables / cross-cutting matrix
scaffold), quality, and the compile gate — live there. No external repo
is required.

If `AUTOSURVEY_TOOLS` is unset and `git rev-parse` cannot find the repo root,
each skill falls back to a path relative to its own location.

---

## Per-Run Directory Layout

All output lands in `<cwd>/.autosurvey/runs/<topic-slug>-YYYYMMDD-HHMMSS/`
by default — i.e. under the directory you launch the run from. Set
`AUTOSURVEY_RUNS_DIR` to pin a fixed central base instead. `--resume` looks
for the run under the same base, so resume from the same directory:

```
brief.md ← copied from --brief input
brief.parsed.json ← canonical structured form (refine_brief output)
brief.derived_schema.json ← per-run extraction schema (refine_brief output)
state.json ← phase + substep status, enables --resume;
                             must carry the canonical `phases` dict
                             (drafting / arguing / polishing) or --resume
                             refuses to touch it
1_search/
  papers.jsonl ← raw merged search results
  filtered.jsonl ← deduplicated + quality-filtered + verified + scope-filtered
  tech_reports.jsonl ← lab/vendor blog posts (Tier 1+2)
  blogs.jsonl ← curated personal blogs (Tier 3)
  websites.jsonl ← GitHub READMEs / HF model cards / generic sites
  cards/ ← per-paper detail cards (markdown, human-readable)
    <cite_key>.md
  cards.jsonl ← aggregated detail cards (machine-readable)
  claims_cache.jsonl ← lazy claim mining cache (atomic_claims +
                             verbatim quotes, append-only, survives pivots)
2_thesis/ ← LOAD-BEARING
  thesis.json ← contestable thesis + 5 argument_steps + ≥2 anticipated_objections
  candidates.json ← thesis candidates the user picked from (audit trail)
  sample_chapters/ ← thesis sanity-check sample sections
4_outline/
  outline.json ← machine-readable; carries argues_for_thesis_step
                             + argument_skeleton + optional tier_axis
  outline.md ← human-readable outline with [@cite_key] annotations
  outline_review.md ← cross-model review verdicts
  outline_repairs.json ← validate_outline.py log (hallucinated IDs stripped)
  reverse_outline.md ← topic-sentence narrative skeleton
5_paper/
  main.tex ← assembled LaTeX document
  references.bib ← bibliography (only cited entries; @misc for blog / website)
  references.cite_keys.json ← paper_id → cite_key map (closed-set authority)
  prose_polish_report.json
  bib_hygiene_report.json
  SURVEY_IMPROVEMENT_LOG.md ← survey-review per-round log
  survey.evidence.html ← click-through evidence dashboard
  figures/
    *.tex / *.pdf ← TikZ fragments and matplotlib-generated PDFs.
                    Number, type, and naming are per-section editorial
                    decisions; the project does not prescribe a fixed
                    figure set. The cross-cutting comparison matrix
                    (declared in outline.json) is the only artefact
                    audited as load-bearing; every other figure or
                    aux table must be `\ref{}`'d from the prose to
                    pass invariant 4.
  sections/
    00_abstract.tex
    01_intro.tex
    NN_<section>.skeleton.md ← 5-bucket H3 skeleton before .tex
    NN_<section>.tex ← .tex with % [CLAIM]/[STEELMAN]/[EVIDENCE]/
                                [CONCESSION]/[SO-WHAT] anchors
    NN_trends_trajectories.tex ← Trends & Trajectories (default-on)
    NX_open_problems.tex
    NY_conclusion.tex
  round_snapshots/ ← survey-review per-round backup
6_verify/
  CITATION_VERIFY.json ← machine-readable audit verdicts (incl. numeric_grounding,
                             trend_audit, kill_argument verdicts)
  claim_audit.json ← per-citation claim audit
  kill_argument.{md,json} ← adversarial review
7_review/
  round{N}/ ← 2-persona review artifacts per round
    senior.json
    skeptic.json
    author_responses.json
    checkpoint.json ← user-decision checkpoint between rounds
main.pdf ← FINAL OUTPUT
survey.html ← OPTIONAL web preview (pandoc; skipped if pandoc absent)
```

---

## Verification Tests

Shell commands (run from inside the repo checkout):

```bash
# 1. Brief refinement smoke
python3 tools/refine_brief.py --brief examples/briefs/long-context-extension.md \
    --output /tmp/brief.parsed.json --auto-confirm
# Expect: brief.parsed.json with topic, ≥3 dimensions, scope.exclude

# 2. Source registry smoke
pytest tests/test_tech_report_fetch.py tests/test_blog_fetch.py \
       tests/test_website_fetch.py -v
# Expect: all pass

# 3. Figure smoke
pytest tests/test_gen_scaling_plot.py tests/test_build_dimension_tables.py -v
# Expect: all pass

# 4. Full repo (~544 tests)
pytest -q
# Expect: all pass

# 9. Quality-tool standalone tests
python3 tools/validate_outline.py <run_dir> --dry-run # closed-set check on outline.json
python3 tools/validate_artifacts.py <run_dir> # thesis + claims + decision_summary schema
python3 tools/audit_writing.py <run_dir> # 5-anchor + narrative + claim grounding
python3 tools/prose_polish.py <run_dir> --check # AI-isms, clutter, dashes, anchors (advisory)
python3 tools/bib_hygiene.py <run_dir> --check # dead bib entries, char escapes
python3 tools/reverse_outline.py <run_dir> # topic-sentence coherence (run by /survey-write)
python3 tools/build_dimension_tables.py --cards <run_dir>/1_search/cards.jsonl \
                                         --outline <run_dir>/4_outline/outline.json \
                                         --output-dir <run_dir>/5_paper/figures/tables \
                                         --mode decision # decision-mode tables
python3 tools/gen_taxonomy_tikz.py <run_dir> # tree / radial / matrix
python3 tools/gen_timeline.py <run_dir>/1_search/filtered.jsonl --output /tmp/t.pdf
python3 tools/gen_scaling_plot.py --cards <run_dir>/1_search/cards.jsonl \
                                         --papers <run_dir>/1_search/filtered.jsonl \
                                         --output /tmp/scaling.pdf
python3 tools/build_run_stats.py <run_dir> --print-paragraph # meta-narrative + stats.json
python3 tools/build_evidence_dashboard.py --output <run_dir>/5_paper/survey.evidence.html <run_dir>
python3 tools/extract_paper_card.py --fetch-all \
                                         --filtered <run_dir>/1_search/filtered.jsonl \
                                         --cache-dir <run_dir>/1_search/.cache # paper-text fetch
python3 tools/extract_paper_card.py --write-cards \
                                         --filtered <run_dir>/1_search/filtered.jsonl \
                                         --extractions-dir <run_dir>/1_search/extractions \
                                         --schema <run_dir>/brief.derived_schema.json \
                                         --output-dir <run_dir>/1_search # writes cards/ + cards.jsonl
bash tools/verify_survey_audits.sh <run_dir> # compile gate (strictest level by default)
```

Slash-command exercises (typed inside Claude Code or Codex):

```text
# 5. Search smoke test
/survey-run --brief <path/to/AutoSurvey>/examples/briefs/long-context-extension.md --max-papers 20
# Expect: filtered.jsonl ≥15 entries, all verified=true, references.bib generated

# 6. Full run end-to-end
/survey-run --brief <path/to/AutoSurvey>/examples/briefs/long-context-extension.md --max-papers 30
# Expect: main.pdf with the agent's chosen figure/table mix (driven by
# the brief), the cross-cutting comparison matrix declared in
# outline.json, every aux figure/table `\ref{}`'d from prose,
# CITATION_VERIFY hard_gate=PASS, numeric_grounding != FAIL,
# no phantom cites.

# 7. Closed-set enforcement (negative test)
# Inject \cite{fake2099} into a section, then:
/survey-verify
# Expect: FAIL with "phantom key: fake2099", compile gate blocks

# 8. Resume test
# Kill mid-write, then re-run with /survey-run --brief <…> --resume <id>
# Expect: skips completed stages, picks up from in_progress write
```

---

## Known Limitations

- **`/survey-write` Gemini illustration** path activates only when
  `GEMINI_API_KEY` is set. Without it, the agent still chooses figures
  and tables from the standard helpers (`gen_taxonomy_tikz`,
  `gen_timeline`, `gen_scaling_plot`, `build_dimension_tables`,
  `scaffold_cross_cutting_matrix`); the cross-cutting comparison
  matrix is the only artefact audited as load-bearing, every other
  figure or aux table must be `\ref{}`'d from prose to pass invariant 4.
- **Length is emergent, not capped** — there is **no page gate** anywhere in the
  pipeline (`audit_writing.py` and `benchmark-targets.json` enforce citation
  density, section counts, conclusion words, etc., but never a page min/max). The
  writing prompts target per-section token counts; total length is a function of
  the brief's scope (number of body sections × full per-section depth), so a
  broad brief naturally yields a longer survey. `estimated_pages` in `stats.json`
  is a display-only estimate (chars ÷ 3000) used by the dashboard to compare
  against the ~45-pp reference benchmark — it is not a target the pipeline steers
  toward. Final PDF length also depends on the venue template (single-column
  reader/NeurIPS yields fewer pages than two-column IEEE). To get a longer, deeper
  survey, broaden the brief's scope and dimensions rather than looking for a knob.
- **Cross-model reviewer in `/survey-review`** requires LLM API access. Without it,
  the loop runs 0 rounds and emits `review.status = "skipped"`.
- **Affiliation → region coverage:** `affiliation_to_region.json` starts with
  ~80 entries; uncovered affiliations are reported as "Unknown" by any
  helper that consumes the lookup (no helper currently fails on missing
  entries).
- **`survey.html` web preview is best-effort** — it is generated by `pandoc`
  during the compile step and silently skipped when `pandoc` is not
  installed. `main.pdf` and `survey.evidence.html` are the guaranteed
  outputs; install `pandoc` if you want the HTML preview.

---

## Standalone Project

AutoSurvey is a fully self-contained Claude Code / Codex skill pack. All
Python helpers live in `tools/`; nothing external is required at runtime
beyond:

- The host agent (Claude Code or Codex) — provides the LLM for every stage
  driven by SKILL.md prompts, so users do not configure a separate API key
- A LaTeX engine (`tectonic` preferred; `latexmk`/`pdflatex`/`xelatex`/`lualatex`
  also work)
- Optional: `matplotlib` for the timeline / scaling figures
- Optional: `pandoc` for the `survey.html` web preview (skipped if absent)
- Optional: `GEMINI_API_KEY` for AI-rendered illustrations
- Optional: `AUTOSURVEY_VERIFY_EMAIL` for raised CrossRef rate limit

That's it. Clone the repo, run `bash tools/install.sh`, write a brief in
`examples/briefs/`-style, and `/survey-run --brief <path>` works.

---
---

# Karpathy Guidelines

Behavioral guidelines to reduce common LLM coding mistakes, derived from [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls. They apply to any agent writing, reviewing, or refactoring code in this repo.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.
