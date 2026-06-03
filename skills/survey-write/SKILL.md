---
name: survey-write
description: Use when writing the survey paper sections in LaTeX for an AutoSurvey run, or when /survey-write is invoked. Phase 2 (Arguing) inner-loop driver — per-section lazy claim mining + 5-step argument skeleton (.skeleton.md → .tex with % [CLAIM]/[STEELMAN]/[EVIDENCE]/[CONCESSION]/[SO-WHAT] anchors) + on-demand figure/table generation + self-review. Closed-set citations + closed-set claim_ids enforced.
---

# /survey-write (Phase 2 driver)

The Phase 2 (Arguing) substep of the pipeline runs a **per-section inner
loop**:

```
for section in outline.sections:
    a) lazy_mine_claims(section.primary_papers) # only mine what we need
    b) write_skeleton(section) # 5 H3 buckets in .skeleton.md
    c) compose_tex(section) # .tex with % [TAG] anchors
    d) self_review(section) # one fresh-thread audit pass
    e) maybe regenerate figure/table # if section needs one
    record iteration in state["phases"]["arguing"]
```

Document-level review with two reviewer personas happens later, in
Phase 3 (`/survey-review`).

## invocation

```
/survey-write [--run-id <id>] [--section <id>] [--auto-confirm]
```

- `--section <id>`: write only that one section (used by survey-pivot
  and section-level redo). If omitted, iterates all sections.
- `--auto-confirm`: skip self-review prompts (one pass, no retry).

---

## Prerequisites

| File | Purpose |
|---|---|
| `<run_dir>/2_thesis/thesis.json` | Thesis text + argument_steps + objections |
| `<run_dir>/4_outline/outline.json` | Sections with `argues_for_thesis_step` + `argument_skeleton` drafts |
| `<run_dir>/1_search/filtered.jsonl` | Closed-set paper corpus |
| `<run_dir>/5_paper/references.bib` | BibTeX (produced in search/Stage 7) |
| `<run_dir>/brief.parsed.json` | Topic / scope / dimensions / style |
| State: `phases.drafting.status == "completed"` |

`<run_dir>/1_search/claims_cache.jsonl` is created on-the-fly during
the loop and may not exist initially.

---

## Per-section inner loop

### Step A — Lazy claim mining

For the section's `primary_papers`, check which cite_keys are missing
from `claims_cache.jsonl`. For each missing paper, the agent:

1. Fetches the paper text (use existing `tools/extract_paper_card.py
   --fetch-all` OR re-implements the simpler S2-OpenAccess → arXiv
   PDF → HTML scrape → abstract chain inline).
2. Reads the text and produces an entry conforming to
   `shared-references/claims-contract.md`:

```json
{
  "cite_key": "...",
  "what_paper_argues": "<1 paragraph>",
  "atomic_claims": [
    {"claim_id": "<cite_key>#1", "claim": "<1 sentence>",
     "quote": "<verbatim>", "anchor": "p.X / Sec Y",
     "claim_type": "empirical|theoretical|methodological|critique"}
    // 2-5 entries
  ],
  "_first_used_in_section": "<section.id>",
  "_mined_at": "<ISO8601>"
}
```

3. Appends to `claims_cache.jsonl` (append-only — survives pivots).

**Verbatim quote check** (inline, before append): each `quote` must be
a substring of the paper text after whitespace normalisation.
Quote-fail → mark `unverified: true` and log; do not block the section
unless ALL claims for the paper are unverified.

### Step B — Write `.skeleton.md`

Read the section's `argument_skeleton` from `outline.json`. The
agent produces `<run_dir>/5_paper/sections/<id>.skeleton.md`:

```markdown
### Claim
<the section's central claim — sharpens outline.argument_skeleton.claim>

### Steelman
<the strongest reason the Claim might be wrong, 1 paragraph>

### Evidence
- claim_id <cite_key>#<n> — <1-sentence framing of how this supports the Claim>
- claim_id <cite_key>#<n> — ...
- (3-5 entries; each MUST be a real claim_id from claims_cache.jsonl)

### Concession
<the boundary of the Claim — where it doesn't apply>

### So-what
<one sentence connecting to the next section / advancing the thesis>
```

The skeleton's H3 buckets MUST be present in this exact order. The
agent SHOULD draw evidence claim_ids exclusively from `claims_cache.jsonl`
entries for papers in this section's `primary_papers`.

### Step C — Compose `.tex` with anchors

The agent transforms the skeleton into LaTeX, **preserving** each H3
bucket as a `% [TAG]` LaTeX comment line. Output:
`<run_dir>/5_paper/sections/<id>.tex`.

Target form (per `shared-references/argument-skeleton.md`):

```latex
\section{<Section Name>}

% [CLAIM]
<1-sentence assertion, with closing \citep{} if the claim is itself a paper's framing>.

% [STEELMAN]
<1 paragraph genuinely articulating the strongest counter-argument>.

% [EVIDENCE]
<2-5 paragraphs of evidence chain with \citet/\citep grounded in atomic_claims;
prefer numbers and verbatim quotes from atomic_claims[].quote>.

% [CONCESSION]
<1 paragraph articulating the boundary of the argument>.

% [SO-WHAT]
<1 sentence linking to the next section or to the thesis>.
```

**Closed-set rule (extended ):**
- Every `\cite{key}` must resolve to a `cite_key` in `filtered.jsonl`
  (existing rule)
- Every `\cite{key}` whose surrounding sentence contains a numeric
  token (year, percentage, count) MUST also have that key in
  `claims_cache.jsonl` AND the cited fact MUST appear in either the
  paper's abstract (filtered.jsonl) or one of its `atomic_claims[].quote`s
  (NEW rule; enforced by `audit_writing.py` in Phase 3)

### Step D — Self-review

The agent invokes itself in a fresh thread with this prompt:

```
You are reviewing a single section .tex. Your only job is to check:
  1. All 5 anchors (CLAIM / STEELMAN / EVIDENCE / CONCESSION / SO-WHAT)
     are present, in order, each followed by ≥ 1 non-empty prose line.
  2. The Steelman section is genuine — not a token "some might argue"
     hedge. If the agent appears to have phoned it in, flag it.
  3. Each \cite{} is in the closed allowed list (provided below).
  4. The section's claim is recognisably a sharpening of
     outline.argument_skeleton.claim (not a wholly different claim).

Output: PASS or FAIL with reasons.
```

If FAIL: retry Step B+C up to `MAX_RETRIES_PER_SECTION` times (effort-
gated). If still FAIL after retries, log the failure to
`5_paper/sections/<id>.review.json` and proceed; final audits in
Phase 3 will catch the persistent issue.

### Step E — On-demand figure/table

Inside Step C, the agent decides whether the section warrants a
figure or table. Heuristics:

- If the section has ≥ 5 papers comparable along the same dimensions
  → emit a dimension table via
  `tools/build_dimension_tables.py --mode decision --section <id>`
  (the `--mode decision` mode is added in t10).
  - **For rich per-family tables, align schema groups to sections and
    enrich cards.** `build_dimension_tables.py` renders one table per body
    section whose id/title matches a `brief.derived_schema.json` group
    name, with one column per field in that group, looked up from
    `cards.jsonl`. So name the derived-schema groups after the body
    sections (e.g. `positional`, `sparse_kv`) and give each method's card
    the family's dimension fields (mechanism, reach, adaptation, failure
    mode, …). Without that alignment the tables come out empty / `N/R`.
    Keep cell strings short (`≤ ~28` chars) so they are not truncated.
- Section 1 (Intro) or the section that introduces the thesis's
  organising principle → emit the matrix figure via
  `tools/gen_taxonomy_tikz.py --layout matrix` (added in t10).
  Whether to emit happens **once per run**, in whichever section the
  agent decides is the right home for Figure 1.
- Trends section → emit timeline and/or scaling-trend via
  `tools/gen_timeline.py` and `tools/gen_scaling_plot.py` (separate
  helpers; the agent picks whichever fits the brief).
  - **Timeline: curate milestones, do NOT ship the year-bar count.** A
    "how many papers per year" bar chart is low-signal. Instead, the
    agent curates **12–20 representative / field-defining works** into
    `1_search/timeline_milestones.json` (each
    `{label, date: "YYYY-MM", category}`, plus a `category_colors` map
    and `title`), where `category` is the method family. `gen_timeline.py
    <run_dir>` auto-detects that file and renders the reference-style
    single-axis milestone timeline (coloured dots, leader-line labels,
    family legend); without it the tool falls back to the year-bar chart.
    Pick milestones that show the field's *trajectory* (one or two per
    family per era), not the highest-cited papers.

The figure/table .tex is written into `5_paper/figures/` and
`\input{}`-ed from the section .tex.

**Discipline (no count cap):** there is no fixed limit on the number
of aux figures or comparison tables. Audit invariant 4
(`cross_cutting_matrix`) instead requires every labelled aux table to
be `\ref{}` / `\autoref{}` / `\cref{}`-referenced from prose — anything
the reader cannot find a pointer to is treated as a load-bearing
violation. See `shared-references/narrative-scaffolding.md` for the
narrative rationale.

### Step F — Record iteration

After D passes (or final-FAIL is logged), append to
`state["phases"]["arguing"]["iterations"]`:

```json
{
  "section_id": "02_architecture",
  "claim_mining_count": 7,
  "write_status": "completed" | "failed_self_review",
  "self_review_passed": true | false,
  "retries": 0,
  "completed_at": "2026-05-29T...Z"
}
```

Save `state.json`.

---

## Special sections

The 5-step skeleton applies to all body sections. Three special
sections have additional rules:

### Abstract & Introduction (sections 00 / 01)

These do NOT use the per-section 5-step skeleton (the document-level
narrative pillars in `shared-references/narrative-scaffolding.md`
govern them):

- **Abstract**: 5-sentence form (Hook / Problem / Approach / Result /
  Implication); MUST contain the thesis verbatim or near-verbatim.
- **Introduction**: must satisfy all 4 narrative pillars — Hook,
  Why Now?, Relationship to Existing Surveys, Numbered Contributions.

`audit_writing.py` (Phase 3) checks both. The writing agent should
read `narrative-scaffolding.md` before composing these two sections.

#### Quantitative meta-narrative (Hook reinforcement)

After the **drafting** of `5_paper/sections/01_introduction.tex`, run
the stats aggregator and consider injecting its one-paragraph render
right after the Hook (as a separate sentence cluster, NOT as a bullet
list):

```bash
python3 tools/build_run_stats.py "$RUN_DIR" --print-paragraph
# → "This survey covers 86 papers (79 cited, 91% coverage), across
# 12 body sections (~27 pages), organised around 5 argument steps
# with 3 anticipated objections, and a 17-item comparison matrix,
# yielding 443 citations (avg 5.2 per cited paper)."
```

Why: the L1-L5 benchmark survey opens its Introduction with exactly
this kind of "trust-scaffold" sentence ("46 pages / 103 citations /
17 systems compared / 6 open problems"). Concrete numbers calibrate
reader expectation faster than any qualitative claim.

The `5_paper/stats.json` artefact also feeds the evidence dashboard's
`<div class="meta-banner">` automatically — no extra step needed.

### Open Problems (typically section N-2)

Each subsection MUST have 4 anchor comments:
`% [PROBLEM-STATEMENT]`, `% [EXISTING-APPROACHES]`, `% [LIMITATIONS]`,
`% [RESEARCH-DIRECTIONS]` — see narrative-scaffolding.md.

The 5-step body-section skeleton is replaced by the 4-bucket form for
Open-Problem subsections. Conclusion gets an explicit thesis restatement.

---

## Closed-set system prompt (unified)

This preamble is injected into EVERY section-writing agent call:

```
CLOSED CITATION RULE: You may ONLY use \cite{key} where <key> appears
in the following allowed list. Using any other key is a critical error
that will halt the pipeline.

Allowed cite keys for this section (primary + secondary papers):
{key_list}

CLAIM-GROUNDING RULE: Any sentence that cites a paper AND contains
a numeric token (year, percentage, count, "$N$" pattern) MUST be
supported by either:
  - The paper's abstract in filtered.jsonl, OR
  - One of the paper's atomic_claims[].quote in claims_cache.jsonl

A list of available atomic claim_ids and their quotes:
{atomic_claims_block}

If you want to make a claim but cannot find a supporting key in the
allowed list OR a supporting atomic_claim quote, write the claim WITHOUT
a citation rather than inventing one.

DO NOT invent paper IDs. DO NOT use cite keys from your training data
unless they appear in the list. DO NOT paraphrase atomic_claim quotes
into different numbers.
```

The `{atomic_claims_block}` is a compact rendering of all
`atomic_claims` for the section's `primary_papers`, no more than ~3000
tokens. The agent picks 3–5 of them as `evidence_claim_keys` in the
skeleton.

---

# Implementation reference (deterministic templates)

The numbered steps below are the deterministic prompt templates and
shell commands the per-section inner loop borrows verbatim. They cover:

- Step 0 — Backup-and-Clean (run once per section iteration)
- Step 1a/1b/1c — corpus + `math_commands.tex` + writing-principles bootstrap (once at the start of Phase 2)
- Step 2 / 3 — Abstract / Introduction prompts
- Step 4 / 4.5 — body-section and Trends & Trajectories prompts (selected per `outline.section_type`)
- Step 5 / 6 — Open Problems / Conclusion prompts
- Step 7 — bibliography hygiene (invoked on-demand inside the inner loop)
- Step 8 — prose polish (runs per-section as part of self-review)
- Step 9 — reverse outline → `4_outline/reverse_outline.md` for the evidence dashboard (the narrative-coherence *gate* lives in `audit_writing.py`'s per-section 5-anchor scan; the diagnostic file is still produced here)
- Step 10 — assemble `main.tex` (once at end of Phase 2)

Cross-model review is delegated entirely to Phase 3 `/survey-review`
(2-persona protocol); `/survey-write` itself does not invoke a separate
reviewer pass.

---



## Invocation

```
/survey-write [--venue neurips|acl|ieee|generic] [--run-id <id>]
```

- `--venue` selects the LaTeX `\documentclass` template (default: `generic`)
- `--run-id` resumes an existing run (defaults to latest)

---

## Prerequisites

| File | Purpose |
|---|---|
| `4_outline/outline.json` | Section structure with closed-set primary/secondary papers |
| `1_search/filtered.jsonl` | Paper corpus (title, abstract, year, venue, citation_count) |
| `1_search/cards.jsonl` | Per-paper detail cards built lazily by the inner loop; created on the fly when this skill first needs them |
| `brief.parsed.json` | Topic / Scope / Dimensions / Style / Configuration (injected into write prompt) |
| `5_paper/references.bib` | Bibliography (one entry per paper) |
| `5_paper/references.cite_keys.json` | `paper_id → cite_key` map |
| `state.json` | `phases.drafting.status == "completed"` (i.e. refine_brief / search / thesis / outline_sketch all done) |

If any prerequisite is missing, halt and surface a precise error.

---

## Constants

The pipeline always runs at full strength. There are no quality knobs;
every survey produced by `/survey-write` is held to the strictest
standard.

| Constant | Value |
|---|---|
| `WORDS_PER_SUBSECTION` | ≥ 550 (floor; deeper when the material supports it) |
| `SUBSECTIONS_PER_SECTION` | as many as the section's distinct sub-topics need — typically 3–6, not a fixed 4–5 |
| `TOKENS_PER_SECTION` | `WORDS_PER_SUBSECTION × SUBSECTIONS_PER_SECTION` worth of prose — a **floor that scales with the section's material, NOT a ceiling** |
| `MIN_CITATIONS_HINT` | 8 / section (hint, not gate; rationale below) |
| `SECTION_CONCURRENCY` | 5 |
| `MAX_CITE_FIX` | 2 |
| `RUN_REVERSE_OUTLINE` | true |
| `RUN_PROSE_POLISH_FIX` | true |

**Length follows scope — do not pin every survey to ~20 pages.** The
per-section budget is a *floor*, not a target to stop at. A section that
binds to a thesis step with many sub-topics, systems, or eras should run
longer than one with few; the total length is the sum of sections written
at full analytical depth, which for a broad brief lands near the ~45-pp
reference benchmark (`benchmark-targets.json`: ~17 K body words). The old
flat "~3 500 tokens, 4–5 subsections per section" reading made every paper
collapse to the same ~20 pages regardless of the brief — that is the
failure mode this constant set replaces.

Distinguish **padding** from **depth**. "Do not pad" means no filler
sentences, no restating a source, no throat-clearing — it does **not** mean
write short. A body section averaging well under ~550 words/subsection is
*under-written*: deepen it with more mechanism, more cross-system
comparison, and more evidence from the cards, rather than trimming to hit a
number. Compress only when the brief explicitly asks for it (e.g. a brief
that says "deliberately kept under ~N pages, favor analytical compression").

`MIN_CITATIONS_HINT` is a *hint* the writer prompt receives, not a
gate the audit enforces. A genuinely thin section may legitimately
have fewer references; padding it to satisfy a number degrades the
paper. The audit looks at citation *density* (≤ 12 / 1 K body words)
and *closed-set* compliance — not raw counts.

The cross-model review at `lite` is skipped to keep latency low; the reverse-outline
test and prose polish are always run because they are deterministic.

---

## Closed-Set System Prompt

This preamble is injected into EVERY section-writing LLM call:

```
CLOSED CITATION RULE: You may ONLY use \cite{key} where <key> appears in the
following allowed list. Using any other key is a critical error that will halt
the pipeline.

Allowed cite keys for this section:
{key_list}

If you want to make a claim but cannot find a supporting key in the list, write
the claim WITHOUT a citation rather than inventing a key.

DO NOT invent paper IDs. DO NOT use cite keys from your training data unless
they appear in the list.
```

---

## Brief & Cards Injection (NEW — central to detail-driven discipline)

Every section-writing LLM call **also** receives:

1. **Full `brief.parsed.json` content** at the top of the prompt — Topic,
   Scope, Dimensions, Style, Configuration. This anchors the LLM to the
   user's actual requirements every time, not just at outline.
2. **For each section being written: the full detail cards** of that
   section's primary papers from `cards.jsonl` — NOT just abstracts.
   Token budget = `TOKENS_PER_SECTION + sum(card_size for card in primary_papers)`.

The cards expose schema-extracted fields (architecture / recipe / scale /
data / insights) so the writer can weave specifics like "64 experts with
top-2 routing and 16% activation ratio" into the prose without inventing
numbers.

### Style discipline injection

`brief.parsed.json.style[]` bullets become explicit rules in the system
prompt. The detail-driven anti-pattern is included literally:

```
STYLE RULES (from brief.parsed.json.style):
{style_bullets}

ANTI-PATTERN — do NOT write list-style summaries that name papers without
their specifics. Concrete example:

  ✗ "Smith et al. propose a new MoE architecture."
  ✓ "Smith et al.'s MoE architecture uses 64 experts with top-2 routing
     and 16% activation ratio."

Use the per-paper cards (provided below) to source the numbers — do not
invent or paraphrase quantitative claims.
```

### NO post-generation rewriting loop — this is intentional

There is **no per-paragraph "must contain a number" rewriting gate**. Such
a gate was considered and rejected because it would force every cite-
containing paragraph to enumerate numbers, producing list-like prose that
breaks narrative flow. A good Survey mixes motivation / transitions /
synthesis / technical paragraphs; not every paragraph should be number-dense.

Detail-driven discipline is achieved through three softer channels instead:

1. **Comparison tables** (`build_dimension_tables.py`) — every body section
   has a wide table built from `cards.jsonl`; this is where the numbers
   live, exposed structurally.
2. **Brief.Style + detail cards in the writing prompt** (this section) —
   the writer LLM sees both the style guide and the full cards, so it
   naturally weaves specifics into the prose.
3. **`survey-review` Depth axis** (NEW) — an LLM-as-reviewer reads the full
   Survey and judges holistically whether body sections feel detail-driven.
   More flexible than any per-paragraph regex.

If a future contributor proposes adding a per-paragraph rewriting loop:
read this section, then `docs/superpowers/specs/2026-05-28-brief-driven-survey-design.md`
§2 and §4.6 — both spec it out as deliberately not in scope.

---

## Workflow Overview

```
Step 0 Backup-and-clean (preserve existing draft, clear stale section files)
Step 1 Initialize (load corpus + brief + cards, build per-section key pools, math_commands.tex)
Step 2 Write Abstract (5-part formula, no citations, full corpus context + brief)
Step 3 Write Introduction (hook → gap → scope → taxonomy → contributions → roadmap)
Step 3.5 Scaffold "Relationship to existing surveys" subsection in
       02_background.tex (tools/scaffold_related_surveys.py --inject;
       LLM fills the per-survey delta sentences inline during Step 4)
Step 4 Write body sections (synthesis-not-summary; closed-set; brief+cards in prompt; phantom repair)
Step 4.5 Write Trends & Trajectories ★ NEW (forward-looking; 4 sub-deliverables; cards + figures)
Step 5 Write Open Problems (concrete unresolved questions + promising directions)
Step 6 Write Conclusion (cross-cutting findings, NOT scope-restate)
Step 7 Bibliography hygiene (tools/bib_hygiene.py: dead-entry removal + char escapes)
Step 8 Prose polish (Pass 1) (tools/prose_polish.py --fix: AI-isms, clutter, dashes)
Step 9 Reverse outline test (tools/reverse_outline.py: topic-sentence chain coherence;
       findings are consumed by Phase 3 /survey-review's 2-persona protocol —
       /survey-write does not run a separate cross-model reviewer)
Step 10 Assemble main.tex (venue template + \input section files)
Step 11 Final checks (no stale files, all refs resolve, page-count sanity estimate — no cap)
Step 12 Update state.json (write.status = "completed")
Step 13 Report
```

> **Note:** there is **no post-generation rewriting loop**. See "Brief & Cards
> Injection" above for the rationale — detail-driven discipline lives in the
> tables, the prompt, and the review's depth axis, not per-paragraph
> rewriting gates.

---

## Step 0 — Backup-and-Clean

If `5_paper/sections/` already exists, back up to `5_paper/sections.bak.{timestamp}/`
before overwriting. Never silently destroy existing work.

**Stale-file detection** — if a previous run had different section IDs, delete
`5_paper/sections/*.tex` files NOT referenced by the current `outline.json`.
Stale files cause `\input{}` failures and confusion.

```bash
RUN_DIR="<run_dir>"
SECTIONS_DIR="$RUN_DIR/5_paper/sections"
if [ -d "$SECTIONS_DIR" ]; then
    TS=$(date +%Y%m%d_%H%M%S)
    cp -r "$SECTIONS_DIR" "$RUN_DIR/5_paper/sections.bak.$TS"
fi
mkdir -p "$SECTIONS_DIR"
```

---

## Step 1 — Initialize

### 1a — Load corpus, brief, cards, and build per-section key pools

```python
import json
from pathlib import Path

cite_map = json.loads(Path("5_paper/references.cite_keys.json").read_text())
allowed_keys = set(cite_map.values())
papers = [json.loads(l) for l in Path("1_search/filtered.jsonl").read_text().splitlines() if l.strip()]
papers_by_id = {p["paper_id"]: p for p in papers}
outline = json.loads(Path("4_outline/outline.json").read_text())

# NEW — load brief and cards for prompt injection
brief = json.loads(Path("brief.parsed.json").read_text())
cards = {}
cards_path = Path("1_search/cards.jsonl")
if cards_path.exists():
    for line in cards_path.read_text().splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        # cards are keyed by paper_id (or cite_key — schema fixes one)
        cards[c.get("paper_id") or c.get("cite_key")] = c

def section_key_pool(section: dict) -> list[str]:
    paper_ids = section["primary_papers"] + section["secondary_papers"]
    return [cite_map[pid] for pid in paper_ids if pid in cite_map]

def section_card_block(section: dict) -> str:
    """Return a formatted block of full detail cards for the section's primary papers."""
    blocks = []
    for pid in section["primary_papers"]:
        c = cards.get(pid)
        if c:
            blocks.append(f"--- card: {cite_map.get(pid, pid)} ---\n{json.dumps(c, indent=2)}")
    return "\n\n".join(blocks)
```

### 1b — Generate `math_commands.tex` (shared notation)

```latex
% math_commands.tex — shared notation for the survey
\newcommand{\R}{\mathbb{R}}
\newcommand{\E}{\mathbb{E}}
\DeclareMathOperator*{\softmax}{softmax}
\DeclareMathOperator*{\argmax}{arg\,max}
\DeclareMathOperator*{\argmin}{arg\,min}
% Domain-specific (auto-detect from outline if absent):
\newcommand{\attn}{\mathrm{Attn}}
\newcommand{\ssm}{\mathrm{SSM}}
```

Surveys often use less math than research papers; keep this file minimal but ensure
all symbols used in any section are defined here.

### 1c — Read writing principles

Before drafting, **read** `skills/shared-references/survey-writing-principles.md`. The
section-specific guidelines below are summaries of that document.

---

## Step 2 — Write Abstract

**Section-specific guideline (5-part formula):**

1. The field and its growth — what changed, why now (1–2 sentences)
2. The gap this survey addresses — what existing surveys miss (1 sentence)
3. The organizing lens — the taxonomy / framework (1 sentence)
4. What the survey covers — corpus size, time range, thematic scope (1 sentence)
5. The headline finding — the main trend / consensus / debate (1–2 sentences)

```
System: {CLOSED_SET_PREAMBLE — full corpus key list (citations forbidden in abstract)}
        You are writing the Abstract of a SURVEY paper.
        Read skills/shared-references/survey-writing-principles.md → "How to Write the Survey Abstract".

User: Topic: "{topic}"
       Corpus size: {N} papers ({year_min}–{year_max})
       Number of sections: {K}
       Section structure: {section_titles_list}
       Organizing lens: {taxonomy_description}

       Write a 200–250 word abstract following the 5-part formula:
       1. Field overview + why now
       2. Gap addressed
       3. Organizing lens
       4. What's covered
       5. Headline finding

       FORBIDDEN openers: "In recent years", "Recent advances", "Large language
       models have achieved remarkable success", "The field of X has...".
       Start with the SPECIFIC scope, not a generic field intro.

       Do NOT include any \cite{}. Do NOT include \begin{abstract} wrapper.
       Output ONLY the LaTeX abstract text.
```

Save to `5_paper/sections/00_abstract.tex`.

---

## Step 3 — Write Introduction

**Section-specific guideline (6-part survey intro):**

1. Field overview and motivation (1 paragraph)
2. The gap — why a new synthesis is needed (1 paragraph)
3. Scope and selection criteria — what is and isn't covered (1 paragraph)
4. The taxonomy — the organizing lens, with reference to Figure 1 (1 paragraph)
5. Contribution bullets — 3–5 specific, falsifiable items
6. Roadmap (1 short paragraph)

```
System: {CLOSED_SET_PREAMBLE — full corpus key list}
        You are writing the Introduction of a SURVEY paper.
        Read skills/shared-references/survey-writing-principles.md → "Introduction Structure for Surveys".

User: Topic: "{topic}"
       Corpus: {total_papers} papers ({year_min}–{year_max})
       Section structure:
       {outline_summary}
       Taxonomy: {taxonomy_description}

       Write a {TOKENS_PER_SECTION}-token Introduction following the 6-part
       survey-intro structure. Use Figure~\ref{fig:taxonomy} to reference the
       taxonomy figure. Use 3–5 \cite{} for foundational works (cite keys must
       be in the allowed list).

       Survey contributions MUST be in a numbered \begin{enumerate}
       block (or inline \textbf{(N)} markers) with FOUR contributions
       — exactly the benchmark survey's count. Read
       shared-references/reference-assets/intro_contributions.example.tex
       for the verbatim shape. Each item must:

       (a) open with a 2–4-word \textbf{Bold Lead.} naming the
           contribution category (e.g. \textbf{Comprehensive Autonomy
           Taxonomy.}, \textbf{Systematic Architecture Analysis.});
       (b) state the contribution in one sentence using concrete
           nouns (e.g. "L1–L5 hierarchy", not "a framework");
       (c) END with a section cross-reference in parentheses:
           (\S\,N), (§N), or (Section N). This is structural-template
           invariant 8 — at least 75% of items must carry the ref;
           the benchmark survey hits 4/4.

       Example item shape:
         \item \textbf{Feature-Annotated System Comparison.} We
               provide detailed analysis of 17 major systems across
               a six-dimensional feature matrix, revealing
               historical maturation patterns and identifying
               capability gaps (\S\,4).

       AVOID:
       - Bare bullet lists without bold leads.
       - "We survey the recent literature on X." (no bold lead, no §N).
       - "We provide a comprehensive overview." (vague, no §N).
       - Items that point at multiple sections — pick the *one*
         section that carries the contribution.
```

Save to `5_paper/sections/01_intro.tex`.

---

## Step 3.5 — Scaffold "Relationship to existing surveys"

Structural-template invariant 5 demands a `Relationship to existing
surveys` subsection in Section 2 (Background) that names ≥ 3 adjacent
surveys with a 1–2-sentence delta each. Run the deterministic
scaffolder before writing the Background section so the writer prompt
inherits the candidate list:

```bash
python3 "$AUTOSURVEY_TOOLS/scaffold_related_surveys.py" "$RUN_DIR" \
    --top 5 --inject
```

This:
- scans `1_search/filtered.jsonl` + `tech_reports.jsonl` for entries
  that look like surveys (type ∈ {survey, review, book} OR title
  contains "survey"/"review"/"overview"; PRISMA-style methodology
  papers and >40-author anthologies are filtered out);
- writes the top-5-by-citations-then-recency into a marker block in
  `5_paper/sections/02_background.tex` (idempotent: re-running
  replaces the previous block in place).

The writer's job during Step 4 is to **replace each `% TODO: state
the delta…` comment with one factual sentence** stating the
comparative scope. Do *not* paraphrase the cited surveys; state the
delta only.

If the scaffolder reports < 3 candidates, the corpus does not contain
enough adjacent surveys for invariant 5. The fix is upstream: re-run
`/survey-search` with at least one explicit `<topic> survey OR
review` query (Step 2 of survey-search now requires this). Do NOT
fabricate adjacent surveys to plug the gap.

---

## Step 3.6 — Scaffold the cross-cutting matrix

Structural-template invariant 4 demands exactly one cross-cutting
comparison matrix (the benchmark uses 17 systems × 6 dimensions). If
`outline.json` declares a slot with `section_type ==
"cross_cutting_matrix"`, run the scaffolder to emit a populated
LaTeX skeleton from `1_search/cards.jsonl`:

```bash
python3 "$AUTOSURVEY_TOOLS/scaffold_cross_cutting_matrix.py" "$RUN_DIR"
```

This:
- locates the matrix slot in `outline.json` (top-level field, section,
  or subsection — see `shared-references/structural-template.md`);
- emits a `\begin{table*}` block to
  `5_paper/sections/<slot_id>.tex` with one row per closed-set system,
  one column per `col_labels` entry, citations on every row, and
  `\textit{?}` markers for cells whose value the heuristic cannot fill;
- caps rows at the slot's `expected_rows` (default: all cards),
  preferring cards with higher `_completeness`.

**Give each row's card a `short_name`.** The row label defaults to the
method name parsed from the title (the part before the first colon, with
hyphenated names like `ST-MoE` / `Auxiliary-Loss-Free` kept whole). For
papers whose title does *not* lead with the method name (e.g. "A
Theoretical Framework for …", "Scaling Laws for Fine-Grained …"), set a
`short_name` (or `method`) field on the card so the matrix shows the real
system name instead of a truncated title. The same field also names rows
in the per-family `build_dimension_tables.py` output.

The writer's job during Step 4 is to **edit the `\textit{?}` cells in
place** with the values that the cards' free-text fields imply but
which the dotted-path heuristic in `_COLUMN_FIELD_MAP` could not
reach. Cells that are genuinely unknown for a given system stay as
`\textit{?}` — the audit tolerates them, but reviewers shouldn't.

If the slot is absent from `outline.json`, this step is a no-op:
invariant 4 only applies to surveys that declare a matrix. Add the
slot upstream during `/survey-outline` if it should apply.

---

## Step 4 — Write body sections (closed-set, synthesis-not-summary)

Process sections from `outline.json` (skipping Intro/Conclusion/Open Problems) in batches
of `SECTION_CONCURRENCY`. For each section:

### Step 4a — Build paper context for this section (cards-first)

```python
section_papers = [
    {
        "cite_key": cite_map[pid],
        "title": p["title"],
        "year": p.get("year", ""),
        "venue": p.get("venue", ""),
        "abstract": (p.get("abstract") or "")[:400], # truncate
        "citation_count": p.get("citation_count", 0),
    }
    for pid in section["primary_papers"] + section["secondary_papers"]
    if (p := papers_by_id.get(pid))
]
section_cards_block = section_card_block(section) # NEW: full schema-extracted cards
brief_block = json.dumps(brief, indent=2) # NEW: full brief.parsed.json
```

### Step 4b — Write section (synthesis-driven prompt with brief + cards)

```
System: {CLOSED_SET_PREAMBLE — section key pool only}
        You are writing a SECTION of a survey paper.
        Read skills/shared-references/survey-writing-principles.md → "Body Section Patterns: Synthesis, Not Summary".

        Brief context (drives every section's substance):
        {brief_block}

        Style discipline rules (from brief.style):
        {style_bullets}

        Anti-pattern reminder:
          ✗ "Smith et al. propose a new MoE architecture."
          ✓ "Smith et al.'s MoE architecture uses 64 experts with top-2 routing
             and 16% activation ratio."

User: Section {N}: "{section_title}"
       Topic: "{topic}"
       Maps to brief dimension: "{dimension_id}" — {dimension_description}

       Key points to cover (one per subsection):
       {key_points_list}

       Papers assigned to this section ({M} primary + {K} secondary):
       {paper_context_block}

       Per-paper detail cards (schema-extracted; use these to source specifics):
       {section_cards_block}

       WRITING RULES:
       - Open one \subsection{...} per distinct sub-topic the material supports
         ({SUBSECTIONS_PER_SECTION} — do not cap a content-rich section at 5,
         do not stretch a thin one to 5)
       - Depth FLOOR: ≥{WORDS_PER_SUBSECTION} words of substantive prose per
         subsection. This is a minimum, not a target to stop at — a sub-topic
         with more mechanism / systems / evidence should run longer. Write at
         full analytical depth; do NOT compress to a fixed page count (compress
         only if brief.style explicitly demands it).
       - Aim for ≥{MIN_CITATIONS_HINT} \cite{} references distributed across subsections
       - Use \label{sec:{section_id}} at the top
       - Where the dimension table for this section exists (figures/tables/...), reference
         it via \ref{tab:<section_id>} in at least one paragraph.

       SYNTHESIS DISCIPLINE (most important):
       This is a SURVEY section, not a sequence of book reports. Use these patterns:

       Pattern 1 — Define a design dimension, then place papers along it:
         "Efficient transformers fall into three families along the *attention pattern*
          axis: fixed-window methods retain locality \cite{...}; learnable-pattern
          methods permit global tokens \cite{...}; kernel-based linear attention
          sacrifices peakedness for O(n) complexity \cite{...}."

       Pattern 2 — Comparative claim with evidence from multiple papers:
         "Quantization-aware fine-tuning has consistently recovered near-full-precision
          quality across both 4-bit \cite{...} and 8-bit \cite{...} regimes."

       Pattern 3 — Trend statement with representative example:
         "Modern open foundation models converge on a near-identical recipe: RoPE,
          RMSNorm, SwiGLU, GQA. \cite{a}, \cite{b}, and \cite{c} differ in scale
          and data, not architecture."

       FORBIDDEN PATTERN (paper-by-paper book report):
         ❌ "Smith et al. (2021) propose method A. Jones et al. (2022) propose method B."
         If three consecutive paragraphs each open with a single paper, REWRITE as
         a synthesis paragraph.

       BANANA RULE: Use exactly the same term as defined in the taxonomy. Do not
       paraphrase ("self-attention" stays "self-attention", not "attention block").

       END WITH A CLOSING SUMMARY PARAGRAPH:
       The last paragraph of the section names the consensus, the open question,
       or the unresolved debate.
```

### Step 4c — Phantom citation check (existing logic, retained)

```python
import re
all_cited = set()
for m in re.finditer(r'\\cite\{([^}]+)\}', latex_text):
    for k in m.group(1).split(','):
        all_cited.add(k.strip())
pool = set(section_key_pool(section))
phantoms = all_cited - pool
```

If `phantoms` non-empty → repair loop (max `MAX_CITE_FIX` = 2 attempts):

```
The following cite keys are NOT in the allowed list and must be removed:
{phantom_keys}

Rewrite ONLY the sentences containing these phantom keys. Either:
1. Replace the \cite{key} with a cite key that IS in the allowed list, OR
2. Remove the \cite{} entirely and restate the claim without citing.

Output ONLY the corrected sentences (not the full section).
```

If phantoms remain after `MAX_CITE_FIX` tries:
- If `len(phantoms) / len(all_cited) > 0.20` → **HALT** with structural-problem error
- Otherwise: strip `\cite{key}` → bare claim, log WARN

### Step 4d — Save section + progress streaming

Save to `5_paper/sections/{NN}_{section_id}.tex`. Update `state.json`:

```json
{
  "write": {
    "status": "in_progress",
    "sections_done": ["abstract", "intro", "transformer_foundations"],
    "sections_total": 11
  }
}
```

Print `[3/11] Writing: Transformer Foundations... ✅`.

---

## Step 4.5 — Write Trends & Trajectories (NEW; only if outline includes a `section_type=="trends"` slot)

This section has its own writing prompt that takes brief.style + cards.jsonl
+ figure paths + dimensions list + scope.include and emits the four
sub-deliverables planned at outline time.

```
System: {CLOSED_SET_PREAMBLE — full corpus key list}
        You are writing the Trends & Trajectories section of a SURVEY paper.
        This section is FORWARD-LOOKING. Hedged language, not asserted-as-fact.

        Brief context:
        {brief_block}

        Style discipline rules (from brief.style):
        {style_bullets}

User: Topic: "{topic}"
       Section structure:
       {outline_summary}
       Dimensions covered by the body:
       {dimensions_table}
       Scope INCLUDE rules:
       {scope_include_bullets}

       Available trend figures:
         - figures/01_timeline.pdf (\ref{fig:timeline})
         - figures/02_scaling_trend.pdf (\ref{fig:scaling_trend})

       Per-paper detail cards across the corpus:
       {full_cards_block}

       The section MUST contain four sub-parts:

       1. **Quantitative trend analysis** — discuss what the scaling-trend
          and geographic-landscape figures show. Reference each figure
          via \ref{...}. Where the evolution-DAG figure exists, discuss
          method lineages.

       2. **Forward-looking propositions** — 3-5 hedged claims about where
          the field is heading. Each claim:
            - Anchored to ≥2 cite_keys from the allowed list, AND
            - References at least one trend figure as supporting evidence.
            - Hedged ("we expect", "the trajectory suggests", "current evidence
              points toward") — never asserted as fact.

       3. **Gap surfacing** — 2-3 paragraphs on what the trends imply but
          isn't being studied. DISTINCT from Open Problems (which collects
          known unknowns); this surfaces meta-patterns the field is
          systematically ignoring.

       4. **SOTA leaders by category** — explicit "best at X" comparisons
          along the brief's dimensions, with trade-offs. Examples
          appropriate to topic: "best dense design", "best MoE efficiency",
          "most effective long-context extension". Cite specifically.

       Use \label{sec:trends_trajectories} at the top.
       Target: ~{TOKENS_PER_SECTION * 1.5} tokens (denser-than-average).

       **Anti-hallucination requirements for Trends & Trajectories prose:**

       1. **Figure/table-anchoring.** Every forward-looking claim must
          reference a concrete artefact already present in the run:
          (a) a specific data point on a generated figure (e.g. the
              scaling-trend plot, a timeline lane, a taxonomy node),
          (b) a specific cell/row in the cross-cutting comparison
              matrix or any aux comparison table grounded in
              `cards.jsonl`,
          (c) a verbatim quote from `claims_cache.jsonl`.

          Claims without any such anchor must be removed or softened to a
          question ("Will the trend toward X continue?" rather than "We
          expect X to happen").

       2. **Hedged language required.** Every forward-looking sentence must
          contain at least one hedge marker from the list: "expect",
          "suggest", "likely", "appears", "trajectory", "trend", "if",
          "continues", "based on", "indicates". Strip absolute-future claims
          like "X will dominate" or "Y is the future".

          ✓ "We expect..."
          ✓ "The trajectory suggests..."
          ✓ "If current scaling trends continue, ..."
          ✓ "Based on N papers in the corpus, ..."
          ✗ "X will dominate by 2027" (asserts certainty)
          ✗ "MoE is the future" (unhedged absolute)

       3. **≥2 cite_keys per claim.** Every claim sentence is anchored to
          at least 2 paper cite_keys from the allowed list (existing rule,
          retained).

       4. **Hedge-self-check before saving.** Before writing the section,
          scan each claim sentence and verify it contains BOTH ≥1 hedge
          marker AND ≥1 figure or numeric anchor reference. If any claim
          fails either check, rewrite or delete it.

       Example claim (good):
         "If current scaling trends continue \citep{llama3,deepseek2024v3},
          the next generation of open-source frontier models is likely to
          push active parameters from the current 37-70B range toward 120B,
          based on the two-year doubling pattern observed in
          figure~\ref{fig:scaling}."

       Example claim (bad — would be flagged):
         "MoE will dominate next-generation LLMs
          \citep{deepseek2024v3,mixtral}."
         (No hedging; no figure/numeric anchor; assertive tone.)
```

Save to `5_paper/sections/{NN}_trends_trajectories.tex`. Skipped entirely
if the outline contains no `section_type=="trends"` slot
(`brief.configuration.trends_section == "skip"`).

---

## Step 5 — Write Open Problems

```
System: {CLOSED_SET_PREAMBLE — full corpus}
        You are writing the Open Problems section of a SURVEY paper.

User: Topic: "{topic}"
       Taxonomy nodes covered: {taxonomy_nodes_list}

       Identify 4–6 CONCRETE open problems. For EACH:
       - State the problem in one specific sentence (not "more research is needed")
       - Explain current state with evidence from the corpus (\cite{...})
       - Name 1–2 promising directions, each backed by emerging work or analogy

       FORBIDDEN: vague items like "scalability", "robustness", "ethics".
       REQUIRED: each open problem must name a specific unresolved question
       AND a non-trivial obstacle that prevents trivial solution.

       Use \subsection{...} for each open problem.
       Target: ~{TOKENS_PER_SECTION} tokens.
```

Save to `5_paper/sections/{NN}_open_problems.tex`.

---

## Step 6 — Write Conclusion

Before drafting, the writer agent **must read**
`shared-references/conclusion-template.md` (a 641-word verbatim
example from the benchmark survey, plus the line-by-line audit
calibration). The prompt below references it so re-running the
template is unnecessary — the agent inherits the example via the
shared-reference file rather than via prompt expansion.

```
System: {CLOSED_SET_PREAMBLE — full corpus}
        You are writing the Conclusion of a SURVEY paper.
        Read shared-references/conclusion-template.md for the
        verbatim reference structure (opener + 3–5 bold-lead findings
        + Call to Action).

User: Topic: "{topic}"
       Section structure: {outline_summary}

       The Conclusion is a RE-FRAME, not a summary. Use the
       'opener + bold-lead findings + call-to-action close'
       structure (see verbatim example in
       shared-references/conclusion-template.md):

       1. ONE opener paragraph that names the survey's central
          thesis in the strongest available terms. Do NOT paraphrase
          the abstract.
       2. THREE-TO-FIVE bold-lead findings paragraphs. Each begins
          with a 2–4-word italic / bold lead (e.g. \textbf{Taxonomy and
          Definitions.}, \textbf{Architectural Patterns.}, etc.) that
          names a cross-cutting finding. Each paragraph must state a
          pattern the reader could not have written before reading
          the survey.
       3. ONE closing paragraph titled \textbf{A Call to Action.}
          (or equivalent) that names (a) what concrete next steps the
          field should take and (b) the conditions under which the
          thesis would have to be revised.

       FORBIDDEN: bulleted lists or \itemize/\enumerate environments.
       FORBIDDEN: copy-paste of the introduction's contribution bullets.
       FORBIDDEN: "In conclusion, we have presented..." — get to the
                  takeaway directly.
       FORBIDDEN: a section-by-section recap.

       Target: 400–700 words (structural-template invariant 7), NO
               subsections, NO bullets. Use 4–8 \cite{} for foundational
               or representative works.
```

Save to `5_paper/sections/{NN}_conclusion.tex`.

---

## Step 7 — Bibliography hygiene + annotation

Run `tools/bib_hygiene.py` to:
1. Remove dead `@entries` (cite keys never used in any .tex)
2. Detect any phantom cites (should be zero — phantom-check at Step 4c is the gate)
3. Escape unescaped `&`, `%`, `#` in field values
4. Validate every entry has `author`, `title`, `year`
5. **Inject `annote = {…}` from each card's `design_rationale`**
   (structural-template invariant 3 — the audit demands ≥ 80 % of
   live entries carry an annotation; the writer prompt has already
   read these card lines in Step 1, so the annotations are by
   construction grounded in the same evidence as the prose).

```bash
python3 tools/bib_hygiene.py "$RUN_DIR" --fix --report "$RUN_DIR/5_paper/bib_hygiene_report.json"
```

If dead entries are removed: log to state.json.
If phantom cites are detected here (shouldn't happen, but defensive): **HALT** — Step 4c failed.
The annotation pass is best-effort: any entry without a
matching card or filtered-jsonl abstract is reported as
"no annotation available" and left without `annote`. The audit's
20 % cushion absorbs these cases.

---

## Step 8 — Prose polish (deterministic Pass 1)

Run `tools/prose_polish.py --fix` for AI-isms, clutter, and compile-readiness:

```bash
python3 tools/prose_polish.py "$RUN_DIR" --fix \
    --report "$RUN_DIR/5_paper/prose_polish_report.json"
```

This step:
- Strips AI-isms (delve, pivotal, landscape, tapestry, underscore, …)
- Replaces cluttered phrases ("due to the fact that" → "because", …)
- Normalizes unicode to LaTeX (em-dash → `---`, en-dash → `--`, smart quotes → `` `` `` ''`)
- **Flags** (without auto-fixing) sentences > 40 words and paragraphs > 8 sentences
- **Flags** synonym churn for the banana rule
- **Flags** sections with >40 passive-verb constructions per 1000 words

The flags are surfaced to Phase 3 `/survey-review`, whose 2-persona
protocol decides which to act on (the senior-reviewer persona consumes
the long-sentence and passive-density flags; the skeptic consumes the
banana-rule synonym churn).

---

## Step 9 — Reverse outline test

Run `tools/reverse_outline.py` to extract the first sentence of every paragraph and
verify the topic-sentence chain forms a coherent narrative:

```bash
python3 tools/reverse_outline.py "$RUN_DIR" \
    --report "$RUN_DIR/4_outline/reverse_outline.md" \
    --json "$RUN_DIR/4_outline/reverse_outline.json"
```

The report is human-readable; the JSON is for tooling. Findings include:
- **Weak topic sentences** (< 8 words, generic openers)
- **Repeated openers** (3+ consecutive paragraphs starting with the same word)

The reverse-outline report is consumed downstream by the Phase 3
`/survey-review` 2-persona protocol; `/survey-write` itself does not
run a cross-model reviewer pass.

---

## Step 10 — Assemble main.tex

Default layout: a **reader-oriented single-column survey template** (no
external `.sty` required). A conference style such as NeurIPS optimises
for blind-submission density; a survey optimises for *navigation and
sustained reading*, so the default adds a **table of contents**, **PDF
bookmarks**, coloured cross-references, styled section headings, and a
running header. Write `5_paper/main.tex` using this template:

```latex
% Reader-oriented survey template (single-column). Pure article + standard
% packages — tectonic auto-fetches them; no venue .sty to copy.
\documentclass[11pt]{article}
\usepackage[letterpaper,margin=1in]{geometry}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{microtype}
\usepackage{amsmath,amssymb,amsthm}
\usepackage{xcolor,graphicx,booktabs,makecell,adjustbox}
\usepackage{tikz}
\usetikzlibrary{shapes,positioning,arrows.meta,shadows.blur,calc,fit}
\usepackage{url,enumitem,titlesec,titling,fancyhdr,natbib}
\usepackage[colorlinks=true,bookmarks=true,bookmarksnumbered=true,
            pdfstartview=FitH]{hyperref}

\definecolor{accent}{HTML}{1F4E79}
\definecolor{rulegray}{HTML}{B0B7C0}
\hypersetup{linkcolor=accent, citecolor=accent, urlcolor=accent,
            pdftitle={<survey_title>}, pdfauthor={AutoSurvey}}

% Section heading styling
\titleformat{\section}{\Large\bfseries\sffamily\color{accent}}{\thesection}{0.6em}{}
\titleformat{\subsection}{\large\bfseries\sffamily}{\thesubsection}{0.5em}{}
\titleformat{\subsubsection}{\normalsize\bfseries\sffamily}{\thesubsubsection}{0.4em}{}
\titlespacing*{\section}{0pt}{1.6ex plus 0.6ex minus 0.2ex}{1.0ex}

% Running header
\pagestyle{fancy}\fancyhf{}
\fancyhead[L]{\small\itshape\color{accent!85!black} <short_running_title>}
\fancyhead[R]{\small\thepage}
\renewcommand{\headrulewidth}{0.4pt}

% Title block
\setlength{\droptitle}{-3em}
\pretitle{\begin{center}\LARGE\bfseries\sffamily}\posttitle{\par\end{center}\vskip 0.4em}
\preauthor{\begin{center}\large}\postauthor{\par\end{center}}
\predate{\begin{center}\small}\postdate{\par\end{center}}
\title{<survey_title>\\[0.2em]{\large\normalfont\itshape <optional_subtitle>}}
\author{AutoSurvey System}\date{\today}
\setcounter{tocdepth}{2}\linespread{1.04}

\begin{document}
\maketitle\thispagestyle{fancy}
\begin{abstract}\noindent
\input{sections/00_abstract_body} % body file has NO \begin/\end{abstract}
\end{abstract}

{\setlength{\parskip}{0pt}\small\tableofcontents}
\vspace{0.8em}\hrule height 0.4pt\vspace{1.0em}

\input{sections/01_intro}
\input{sections/02_<...>}
% Figures/tables are \input from inside section files (or via
% \input{figures/<name>}); main.tex prescribes only the section sequence.
\input{sections/<NN>_conclusion}

\bibliographystyle{abbrvnat} % natbib + abbrvnat
\bibliography{references}
\end{document}
```

**Notes.**
- The abstract body file (`sections/00_abstract_body.tex`) must NOT contain
  the `\begin{abstract}…\end{abstract}` wrapper — `main.tex` applies it.
- `tectonic` auto-fetches `titlesec`, `titling`, `fancyhdr`, `lmodern`,
  `enumitem`, `adjustbox`; no manual package install is needed.
- Compile twice (or let tectonic auto-rerun) so the table of contents and
  citations settle; tectonic's bibtex pass can lag the rendered TOC/refs by
  one pass.

### Other venues

The reader-oriented survey template above is the default. To target a
specific conference instead (e.g. for camera-ready submission), swap the
`\documentclass` / `\usepackage` preamble — the section files, citations,
and figures are venue-agnostic and need no change:

| Venue | Template line(s) | Notes |
|---|---|---|
| **Reader survey (default)** | `\documentclass[11pt]{article}` + TOC/hyperref/titlesec preamble above | no external `.sty`; TOC + bookmarks + running header |
| NeurIPS 2024 | `\usepackage[preprint]{neurips_2024}` | bundled in `templates/`; `fetch_venue_template.sh <run> neurips` |
| ICLR 2024 | `\usepackage{iclr2024_conference}` | manual download from openreview.net |
| ICML 2024 | `\usepackage[accepted]{icml2024}` | manual download from icml.cc |
| ACL | `\usepackage{acl}` | manual download from aclweb.org |
| IEEE Conference | `\documentclass[conference]{IEEEtran}` | in TeXLive (no fetch needed); **switch from natbib to `cite` package** |
| IEEE Journal | `\documentclass[journal]{IEEEtran}` | same as above |
| Generic article | `\documentclass[11pt,a4paper]{article}` + `\usepackage[hidelinks]{hyperref}` | for development / review |

For non-NeurIPS venues, drop the `.sty` / `.cls` next to `main.tex` and adjust
the `\documentclass` / `\usepackage` line. Cite syntax stays the same (`\citet`
/ `\citep` for natbib; `\cite{}` for IEEE).

---

## Step 11 — Final checks

Before returning, verify:

| Check | Tool / Method |
|---|---|
| All `\input{sections/...}` paths exist | filesystem walk |
| No stale `.tex` files outside `\input` | reverse-grep main.tex against `sections/*.tex` |
| Every `\cite{}` resolves to a bib entry | `bib_hygiene.py --check` (already PASS at Step 7) |
| All `\ref{}` and `\label{}` match | regex scan of all .tex files |
| Page-count sanity estimate (no cap; length follows scope) | `wc -w sections/*.tex` (rough: 250 words ≈ 1 page A4) |
| No `TODO` / `FIXME` / `DATA_NEEDED` markers | grep |
| `prose_polish.py --check` passes | run again |

Any failure → log warning in state.json (do not auto-halt; let user/operator decide).

---

## Step 12 — Update state.json

```json
{
  "stages": {
    "write": {
      "status": "completed",
      "sections_written": 11,
      "sections_list": [...],
      "phantom_cites_repaired": 0,
      "phantom_cites_stripped": 0,
      "bib_hygiene": {"dead_entries_removed": 0, "char_escapes_applied": 0},
      "prose_polish": {"ai_isms": 0, "clutter": 0, "compile_fixes": 0,
                       "long_sentences_flagged": 6, "passive_density_high_sections": []},
      "reverse_outline": {"weak_topic_sentences": 2, "repeated_openers": 0,
                          "report": "4_outline/reverse_outline.md"},
      "venue": "generic",
      "main_tex": "5_paper/main.tex"
    }
  }
}
```

---

## Step 13 — Report

```
✅ Survey draft complete

  Sections written: 11/11
  Citations: 30 unique cite keys (closed-set verified)
  Phantom cites: 0 (hard gate PASS)
  Bibliography: 30 entries, 0 dead, 2 char-escapes applied
  Prose polish: 5 AI-isms removed, 12 clutter phrases simplified
  Reverse outline: 2 weak topic sentences flagged → addressed

  Output:
    5_paper/main.tex
    5_paper/sections/*.tex (11 files)
    4_outline/reverse_outline.md
    5_paper/prose_polish_report.json
    5_paper/bib_hygiene_report.json

  Next: /survey-review (2-persona round protocol) or /survey-verify
```

---

## Output Files

| File | Contents |
|---|---|
| `5_paper/main.tex` | LaTeX document assembling all sections |
| `5_paper/sections/00_abstract.tex` | Abstract |
| `5_paper/sections/01_intro.tex` | Introduction |
| `5_paper/sections/NN_<slug>.tex` | Body sections |
| `5_paper/sections/NN_open_problems.tex` | Open Problems |
| `5_paper/sections/NN_conclusion.tex` | Conclusion |
| `5_paper/math_commands.tex` | Shared math notation |
| `5_paper/prose_polish_report.json` | Prose polish findings |
| `5_paper/bib_hygiene_report.json` | Bibliography hygiene findings |
| `4_outline/reverse_outline.md` | Topic-sentence chain narrative skeleton |

---

## Key Rules

- **Closed-set citation is non-negotiable.** Step 4c is the gate; anything beyond
  is defensive.
- **Synthesis, not summary.** Step 4b's prompt enforces this; the Phase 3
  `/survey-review` 2-persona protocol audits it on the assembled draft.
- **Banana rule.** Use the same term across all sections. Step 8 flags violations;
  `/survey-review` then enforces them via its skeptic-persona demands.
- **No fabricated content.** If a thin section has < `MIN_CITATIONS_HINT` papers,
  the section is allowed to be short — do NOT invent papers, do NOT inflate.
- **Backup before overwrite.** Step 0 always backs up.
- **Determinism first, then LLM.** Steps 7–9 are deterministic and run before
  any LLM reviewer is invoked. This catches the "easy" issues without
  burning reviewer tokens.

---

## Tool Resolution

All AutoSurvey helpers live in `<repo>/tools/`. Resolve once at the top of the skill:

```bash
AUTOSURVEY_TOOLS="${AUTOSURVEY_TOOLS:-$(git rev-parse --show-toplevel 2>/dev/null)/tools}"
```

Helpers used here:
- `$AUTOSURVEY_TOOLS/prose_polish.py`
- `$AUTOSURVEY_TOOLS/bib_hygiene.py`
- `$AUTOSURVEY_TOOLS/reverse_outline.py`
- `$AUTOSURVEY_TOOLS/validate_outline.py` (used at outline stage; defensive re-run is fine)

---

## See Also

- `skills/shared-references/survey-writing-principles.md` — synthesis patterns,
  banana rule, abstract / intro / conclusion templates
- `skills/shared-references/reviewer-independence.md` — REVIEWER_BIAS_GUARD invariant
- `skills/shared-references/assurance-contract.md` — verdict state machine
- `skills/survey-review/SKILL.md` — auto-improvement loop (post-write polish)
- `skills/survey-verify/SKILL.md` — citation hard gate (post-review verification)
