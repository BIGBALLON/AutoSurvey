---
name: survey-search
description: Use when collecting papers for an AutoSurvey run, or when /survey-search is invoked. Multi-source literature search (arXiv + Semantic Scholar + OpenAlex + ACL + PubMed + tech reports + blogs + websites) with brief.scope LLM filter, dedup, paper-existence verification, and references.bib generation.
---

# /survey-search

Multi-source literature search for AutoSurvey. Retrieves, deduplicates, quality-filters,
and verifies papers, then generates a BibTeX file ready for LaTeX compilation.

---

## Invocation

```
/survey-search [--run-id <id>] [--max-papers 200] [--year-start YYYY] [--sources auto]
```

- `--run-id <id>`: write to existing run directory (used by `/survey-run`).
  The skill reads `brief.parsed.json` from the run dir to drive every step
  (topic, scope.include / .exclude, dimensions, sources.categories,
  year_range). When invoked outside a run, a `--brief` argument is also
  accepted and the skill creates a fresh run directory.
- `--max-papers 200`: total cap after deduplication
- `--year-start YYYY`: override `brief.parsed.json.sources.year_range[0]`
- `--sources auto` (default): activates sources from `brief.parsed.json.sources.categories`

---

## Prerequisites

1. AutoSurvey repo cloned (all helpers live in `tools/`)
2. At least one search source reachable (arXiv is always available)

---

## Tool Resolution

All AutoSurvey helpers live in `<repo>/tools/`. Resolve once at the top of the skill:

```bash
AUTOSURVEY_TOOLS="${AUTOSURVEY_TOOLS:-$(git rev-parse --show-toplevel 2>/dev/null)/tools}"
[ -d "$AUTOSURVEY_TOOLS" ] || AUTOSURVEY_TOOLS="$(dirname "$(realpath "$0")")/../../tools"

VERIFY_PAPERS="$AUTOSURVEY_TOOLS/verify_papers.py"
ARXIV_FETCH="$AUTOSURVEY_TOOLS/arxiv_fetch.py"
S2_FETCH="$AUTOSURVEY_TOOLS/semantic_scholar_fetch.py"
OPENALEX_FETCH="$AUTOSURVEY_TOOLS/openalex_fetch.py"
TECH_REPORT_FETCH="$AUTOSURVEY_TOOLS/tech_report_fetch.py"
BLOG_FETCH="$AUTOSURVEY_TOOLS/blog_fetch.py"
WEBSITE_FETCH="$AUTOSURVEY_TOOLS/website_fetch.py"
BIB_GEN="$AUTOSURVEY_TOOLS/bib_generator.py"
```

Override `AUTOSURVEY_TOOLS` if running from a non-standard location.

---

## Search Sources

### Always-on (no API key required)

| Source | Tool | Notes |
|---|---|---|
| arXiv | `arxiv_fetch.py` | Primary CS/ML/physics preprint source |
| Semantic Scholar | `semantic_scholar_fetch.py` | Free; API key unlocks higher rate limit |
| OpenAlex | `openalex_fetch.py` | Free, broad coverage across all fields |

> **Rate limits / 429.** arXiv and the unauthenticated S2 tier throttle hard
> from a shared egress IP. Both fetchers retry with backoff + `Retry-After`
> internally, and `arxiv_fetch.py search` degrades to an empty `[]` (exit 0)
> rather than a traceback when a 429 persists — so do **not** wrap them in a
> shell `timeout` (which isn't even present on macOS). If a source stays
> throttled, lean on OpenAlex (no key, real indexed works only) as the
> backbone and top up with the non-paper sources below; set
> `SEMANTIC_SCHOLAR_API_KEY` for a higher S2 limit.

### Domain-auto (topic keyword detection, no key)

> Note: ACL Anthology and PubMed coverage is provided by OpenAlex's
> broad indexing — there are no separate fetchers for them.

### Brief-driven non-paper sources (curated registry + brief URLs)

| Source | Tool | Notes |
|---|---|---|
| Tech reports / lab blogs (Tier 1+2) | `tech_report_fetch.py` | RSS / atom / HTML scrape from `tools/source_registry.json` |
| Personal / individual blogs (Tier 3) | `blog_fetch.py` | RSS feeds (Lil'Log, Raschka, Yan, Lambert) |
| GitHub READMEs / HF model cards / generic websites | `website_fetch.py` | Reads brief-listed URL lists |

> Note: arXiv + Semantic Scholar + OpenAlex together provide
> sufficient coverage; no separate neural-web-search fetcher is
> wired up.

---

## Steps

### Step 1 — Determine run directory and load brief

```
RUN_DIR = ${AUTOSURVEY_RUNS_DIR:-$PWD/.autosurvey/runs}/<topic-slug>-YYYYMMDD-HHMMSS/
```

If `--run-id` given, use that existing directory. The brief is read from
`$RUN_DIR/brief.parsed.json` (produced by Stage 0 `refine_brief`):

```python
import json
brief = json.loads((Path(RUN_DIR) / "brief.parsed.json").read_text())
TOPIC = brief["topic"]
SCOPE_INCLUDE = brief.get("scope", {}).get("include", [])
SCOPE_EXCLUDE = brief.get("scope", {}).get("exclude", [])
DIMENSIONS = brief.get("dimensions", []) # [{name, description}]
SOURCE_CATS = brief.get("sources", {}).get("categories", [])
YEAR_START, YEAR_END = brief.get("sources", {}).get("year_range", [None, None])
GITHUB_REPOS = brief.get("sources", {}).get("github_repos", [])
MODEL_CARDS = brief.get("sources", {}).get("model_cards", [])
WEBSITES = brief.get("sources", {}).get("websites", [])
```

If no run dir exists yet:
```bash
TOPIC_SLUG=$(echo "$TOPIC" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | cut -c1-40)
RUN_ID="${TOPIC_SLUG}-$(date +%Y%m%d-%H%M%S)"
RUN_DIR="${AUTOSURVEY_RUNS_DIR:-$PWD/.autosurvey/runs}/$RUN_ID"
mkdir -p "$RUN_DIR/1_search"
```

Write `query.json`:
```json
{
  "topic": "<topic from brief.parsed.json>",
  "scope_include": ["..."],
  "scope_exclude": ["..."],
  "dimensions": [{"name": "...", "description": "..."}],
  "max_papers": 200,
  "year_range": [2021, 2026],
  "sources_requested": ["arxiv", "semantic_scholar", "openalex", "tech_reports", "blogs"],
  "run_id": "<run_id>",
  "timestamp": "2026-..."
}
```

Initialise `state.json` (if not resuming):
```json
{
  "run_id": "<run_id>",
  "topic": "<topic>",
  "stages": {
    "search": {"status": "in_progress"}
  }
}
```

### Step 2 — Generate query variants (dimension-aware)

Use the LLM (single call) to produce 4–8 search query variants. The prompt
now includes `scope.include` and the dimension list so variants cover the
brief's full thematic surface, not just the topic string.

```
Generate 6 distinct search query strings for a survey on the topic: "{topic}"

Scope INCLUDE rules (the survey must cover these):
{scope_include_bullets}

Dimensions (the survey will have a section per dimension; queries should
collectively cover all of them):
{dimensions_table} ← name + description for each

Rules:
- Each query targets a different dimension or sub-facet
- Use academic phrasing (not conversational)
- Mix broad and specific formulations; include a query per dimension where possible
- At least one query should include canonical author / paper / model names
- **At least one query must explicitly target adjacent surveys** (e.g.
  `"<topic> survey OR review OR overview"`). The structural-template
  invariant 5 requires ≥ 3 named adjacent surveys in the
  Background section; if the corpus contains zero adjacent surveys,
  the writer cannot satisfy that invariant.
- **At least one query must target the latest frontier** (the trailing
  12–18 months), so the survey is timely. Run it year-filtered to
  `[current_year-1, current_year]` with **relevance sort**.

Output: JSON array of strings only, no explanation.
```

> **Recency pitfall (do NOT date-sort for discovery).** On OpenAlex,
> `--sort date` returns the newest works matching *any* query token and
> is dominated by cross-field noise (a KV-cache query returns
> service-orchestration papers). For recency, keep the default
> **relevance** sort and constrain with `--year <start>-<end>`; reserve
> `--sort date` for verifying a single known-recent work, never for
> topic discovery. Frontier long-context capability is frequently
> documented in **model cards / lab blogs (Qwen2.5-1M, DeepSeek, Llama 4)
> before any indexed paper**, so the non-paper fetchers in Step 3.5 are
> load-bearing for timeliness — run and scope-filter them, do not skip.

Parse JSON array. On failure → fallback to
`[topic, topic + " survey", topic + " review"] +
[topic + " " + d["name"] for d in DIMENSIONS]` — note the survey/review
fallbacks are mandatory, not optional.

### Step 3 — Parallel fetch (Policy D2 — aggregate all resolved sources)

```
sources_used = []
sources_count = 0
```

For each resolved source, run its fetcher with `asyncio.gather` (or sequential with
source-level error isolation):

```python
import asyncio, subprocess, json
from pathlib import Path

async def run_fetcher(cmd: list, output_file: Path):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        output_file.write_bytes(stdout)
        return True
    return False
```

For each query variant × each source:
- `python3 $ARXIV_FETCH --query "..." --max-results 50`
- `python3 $S2_FETCH --query "..." --max-results 50`
- `python3 $OPENALEX_FETCH --query "..." --max-results 50`

Append all results to `1_search/papers.jsonl`.

**Policy D2 check:** If `sources_count == 0` after all fetches → FAIL:
```
ERROR: No sources contributed results.
Check API keys and network access.
```

### Step 3.5 — Fetch non-paper sources (tech reports, blogs, X, websites)

For each non-paper category enabled in `brief.parsed.json.sources.categories`,
invoke the matching fetcher. Each tool reads `tools/source_registry.json`
(filtered by tier / scope_hint) plus the brief's URL lists.

Build a CSV of scope.exclude bullets for blog filtering (lab posts whose
keywords match excluded topics get a stricter threshold downstream):

```bash
BRIEF_SCOPE_EXCLUDE_CSV=$(jq -r '.scope.exclude // [] | join(",")' "$RUN_DIR/brief.parsed.json")

# Tier 1 + Tier 2 lab/vendor blogs (technical reports)
if echo "$SOURCE_CATS" | grep -qE "tech_reports|blogs"; then
    python3 "$TECH_REPORT_FETCH" \
        --output "$RUN_DIR/1_search/tech_reports.jsonl" \
        --year-start "${YEAR_START:-2021}" \
        --year-end "${YEAR_END:-$(date +%Y)}"
fi

# Tier 3 personal / individual blogs
if echo "$SOURCE_CATS" | grep -qE "blogs"; then
    python3 "$BLOG_FETCH" \
        --output "$RUN_DIR/1_search/blogs.jsonl" \
        --year-start "${YEAR_START:-2021}" \
        --year-end "${YEAR_END:-$(date +%Y)}" \
        --exclude-keywords "$BRIEF_SCOPE_EXCLUDE_CSV"
fi

# GitHub READMEs / HF model cards / generic websites (brief-driven)
if [ -n "$GITHUB_REPOS$MODEL_CARDS$WEBSITES" ] || \
   echo "$SOURCE_CATS" | grep -qE "github_readmes|model_cards|websites"; then
    python3 "$WEBSITE_FETCH" \
        --output "$RUN_DIR/1_search/websites.jsonl" \
        --github-repos-file <(jq -r '.sources.github_repos[]?' "$RUN_DIR/brief.parsed.json") \
        --model-cards-file <(jq -r '.sources.model_cards[]?' "$RUN_DIR/brief.parsed.json") \
        --websites-file <(jq -r '.sources.websites[]?' "$RUN_DIR/brief.parsed.json") \
        --year-start "${YEAR_START:-2021}" \
        --year-end "${YEAR_END:-$(date +%Y)}"
fi
```

Each tool writes its own JSONL with a `source_type` field (one of
`tech_report` / `blog` / `model_card` / `github_readme` /
`website`). After all fetches, **merge into the unified
`papers.jsonl`** so downstream dedup, scope filter, and verification treat
every record uniformly. The unified pipeline distinguishes records via
`source_type`.

```bash
# Merge non-paper records into papers.jsonl (preserving source_type)
for extra in tech_reports blogs websites; do
    [ -s "$RUN_DIR/1_search/$extra.jsonl" ] && \
        cat "$RUN_DIR/1_search/$extra.jsonl" >> "$RUN_DIR/1_search/papers.jsonl"
done
```

### Step 3.6 — Citation-graph snowball (recall expansion)

Query-only search misses canonical work that uses different terminology
than the brief — the single biggest recall gap. Snowballing closes it: from
the seeds found so far, walk the citation graph one hop (each seed's
**references** + its **citing papers**) and keep candidates that connect to
*several* seeds. A paper many of your seeds cite, or that cites many of your
seeds, is almost certainly on-topic even if no query surfaced it.

```bash
# Build a provisional corpus first so snowball has seeds to expand from.
# (Dedup is cheap; or point --seeds-file at papers.jsonl directly.)
python3 "$AUTOSURVEY_TOOLS/snowball_citations.py" "$RUN_DIR" \
    --seeds-file "$RUN_DIR/1_search/papers.jsonl" \
    --max-seeds 20 --min-overlap 2

cat "$RUN_DIR/1_search/snowball_candidates.jsonl" >> "$RUN_DIR/1_search/papers.jsonl"
```

Notes:
- Backend is OpenAlex (polite pool, no key). Snowball needs **OpenAlex-indexed
  seeds** (an `openalex_id` or DOI) — run the OpenAlex fetcher in Step 3 so
  the seeds carry ids. arXiv/S2-only seeds without ids are skipped.
- Candidates are emitted in the **same shape as an OpenAlex search hit**, so
  they flow through Dedup (Step 4) → Quality (Step 5) → **Scope filter
  (Step 5.5)** like every other paper. **Snowball is a recall tool; precision
  is the scope filter's job.** Co-citation inevitably surfaces a few generic
  hubs (broad LLM reviews co-cited by many topical papers); Step 5.5 prunes
  them — do not add a token filter here, which would defeat the whole point
  of catching differently-worded canonical work.
- More seeds → stronger signal: with ~20 seeds, genuinely topical papers reach
  higher overlap and rise above incidental 2-seed hubs. Raise `--min-overlap`
  to 3 only if Step 5.5 is overwhelmed.

### Step 4 — Deduplicate

Load all papers from `papers.jsonl`. Deduplicate by priority:
1. DOI exact match → keep one with most metadata
2. arXiv ID exact match → keep one with most metadata
3. Title fuzzy similarity ≥ 0.92 (use `difflib.SequenceMatcher`) → keep higher-cited

```python
from difflib import SequenceMatcher

def title_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()
```

After dedup, apply `--max-papers` cap (sort by citation_count desc before truncating).

> **Derive the anchor list (Step 6.6) BEFORE this cap and protect anchor
> matches from truncation.** Canonical works are often older or weakly
> matched by a single query, so a naive citation-sorted cut drops them — then
> the Step 6.6 gate fails and you waste rounds re-searching + force-including
> them (with spurious fuzzy matches). Build the anchor set up front and treat
> an anchor match as an always-KEEP exemption alongside the venue/recency
> rules below.

### Step 5 — Dynamic quality filter

**Venue whitelist** — always KEEP (regardless of citation count):
```
NeurIPS, ICML, ICLR, ACL, EMNLP, NAACL, CVPR, ICCV, ECCV, AAAI, IJCAI,
SIGKDD, WWW, SIGIR, VLDB, OSDI, SOSP, Science, Nature, Cell, PNAS,
IEEE Transactions, ACM CSUR, Journal of Machine Learning Research
```

**Recency exemption** — always KEEP if published within 18 months of today.

**Anchor exemption** — always KEEP any record whose title matches a Step 6.6
anchor (canonical system / benchmark / method named in the brief), regardless
of citation count. These are the must-haves the coverage gate checks for;
never let the citation threshold cut them.

**Dynamic threshold** for remaining papers:
```python
import statistics, datetime

today = datetime.date.today()
cutoff_date = today.replace(year=today.year - 1, month=today.month) # ~18 months

mature_papers = [p for p in papers
                 if p.get("year") and int(p["year"]) < cutoff_date.year - 1
                 and not is_whitelisted_venue(p)]
if mature_papers:
    median_cit = statistics.median(p.get("citation_count", 0) for p in mature_papers)
    threshold = max(2, int(median_cit * 0.05))
else:
    threshold = 2
```

Apply: remove papers with `citation_count < threshold` unless venue-whitelisted, recent, or an anchor match.

Log filter stats: total_before, venue_kept, recency_kept, threshold, removed_below_threshold.

### Step 5.5 — Scope.exclude LLM filter (NEW)

For each remaining record (paper / tech report / blog / X post / website),
have an LLM-as-filter check the title + abstract / body snippet against
`brief.scope.exclude`. Batch 25 records per call.

```
System: You are a scope filter for a literature survey. The survey's brief
defines what's in/out of scope. For each paper title+abstract, return one of:
in_scope, out_of_scope, unclear.

User: Brief scope:
  Include rules:
  {scope_include_bullets}

  Exclude rules (anything matching these is OUT of scope):
  {scope_exclude_bullets}

Records (JSON list of {paper_id, title, abstract|body_snippet, source_type, source_name}):
{batch_25}

Output JSON: [{"paper_id": "...", "verdict": "in_scope|out_of_scope|unclear", "reason": "..."}]
```

**Threshold modulation:** when a record's `source_name` matches a registry
entry whose `warn_on_exclude` array intersects `brief.scope.exclude`, append
to the prompt: "STRICTER MODE: this source is biased toward an excluded
topic; mark borderline cases as out_of_scope." This implements the
`warn_on_exclude` semantics for Tier 3 blogs (e.g. Nathan Lambert posts
when brief excludes RLHF).

**Apply verdicts:**
- `in_scope` → keep.
- `out_of_scope` → drop. Append to `1_search/scope_dropped.jsonl` with reason.
- `unclear` → keep but flag (`scope_review_needed: true` field) so the
  outline / write stage can sanity-check the inclusion. Logged in state.json
  for human review.

### Step 5.6 — Affiliation enrichment (NEW)

For each record (papers only — non-paper sources skip this step), parse
the author list and add an `affiliation` string field — best-effort, first
author's primary affiliation:

```python
for paper in records:
    if paper.get("source_type", "paper") != "paper":
        paper["affiliation"] = ""
        continue
    authors = paper.get("authors", [])
    aff = ""
    if authors:
        first = authors[0]
        if isinstance(first, dict):
            aff = first.get("affiliation", "") or first.get("affiliations", [""])[0]
        # Some sources expose affiliations at paper level
    paper["affiliation"] = aff or ""
```

Affiliation values are recorded for downstream use (e.g.,
`gen_scaling_plot.py` at the figure stage, mapped to regions via
`tools/affiliation_to_region.json`). Empty / missing → handled as "Unknown"
downstream (not dropped).

### Step 6 — Verify papers

If `VERIFY_PAPERS` resolved:
```bash
python3 "$VERIFY_PAPERS" --input filtered_draft.jsonl --output 1_search/filtered.jsonl
```

The 3-layer existence check applies to records with `source_type == "paper"`.
For tech reports / blogs / websites, verification is a degenerate
"URL fetched & non-empty body" single-layer check (already done at fetch
time); they pass through with `verified: true, method: "url_fetch"`.

If not resolved → fallback: copy draft to `filtered.jsonl`, tag all `verified: false`, emit WARN:
```
WARN: verify_papers.py not found at $AUTOSURVEY_TOOLS — papers unverified
      (status=unverified, method=none). Reinstall AutoSurvey to restore.
```

### Step 6.6 — Anchor-coverage gate (search collected the must-haves?)

Search is the load-bearing first stage: a corpus missing a topic's
canonical methods/models/benchmarks dooms every later stage. Before
proceeding, verify coverage of the must-have anchors with a measurable
gate rather than eyeballing.

1. Derive the anchor list — the works/systems/benchmarks a competent
   survey of this topic MUST cover — from `brief.scope.include` +
   `brief.sources` (the brief usually names them explicitly) plus the
   dimension names. 15–40 anchors is typical.
2. Run the check across the unified corpus (papers + non-paper sources):

```bash
python3 "$AUTOSURVEY_TOOLS/check_anchor_coverage.py" \
    --corpus "$RUN_DIR/1_search/filtered.jsonl" \
    --anchors "YaRN,LongRoPE,StreamingLLM,H2O,SnapKV,KIVI,MLA,RULER,LongBench,..." \
    --min 0.7 \
    --json "$RUN_DIR/1_search/anchor_coverage.json"
```

3. If coverage `< --min` (exit 1), the report's `missing[]` names the
   gaps. **Do not proceed**: add targeted queries for the missing
   anchors (including their full-name spellings — the matcher honours
   word boundaries for acronyms like `NSA`/`MLA`, so search the expanded
   name too), or add a source / wait out a throttled one, then re-run
   Step 3 onward. Only continue once coverage clears the threshold.

This converts "is the corpus good enough?" into a deterministic check,
catching a throttled or mis-queried source here instead of in the final
PDF.

### Step 7 — Generate BibTeX

```bash
mkdir -p "$RUN_DIR/5_paper"
python3 "$BIB_GEN" 1_search/filtered.jsonl --output 5_paper/references.bib
```

This writes:
- `1_search/filtered.jsonl` with `cite_key` field added to each paper
- `5_paper/references.bib`
- `5_paper/references.cite_keys.json` (paper_id → cite_key map)

### Step 8 — Update state.json

```json
{
  "stages": {
    "search": {
      "status": "completed",
      "papers_raw": <N>,
      "papers_after_dedup": <M>,
      "papers_filtered": <K>,
      "papers_verified": <J>,
      "sources_used": ["arxiv", "semantic_scholar", ...],
      "threshold_used": <threshold>
    }
  }
}
```

### Step 9 — Report

```
✅ Search complete
   Sources: arxiv, semantic_scholar, openalex
   Raw results: 847 → after dedup: 312 → after filter: 143 → verified: 138
   Quality threshold: 3 citations (≥2, median×0.05)
   Output: $RUN_DIR/1_search/filtered.jsonl
           $RUN_DIR/5_paper/references.bib

Next: /survey-thesis (or /survey-run continues automatically)
```

---

## Output Files

| File | Contents |
|---|---|
| `1_search/papers.jsonl` | Raw merged results from all sources (papers + tech reports + blogs + websites; distinguished via `source_type`) |
| `1_search/tech_reports.jsonl` | Lab/vendor blog posts (Tier 1+2) |
| `1_search/blogs.jsonl` | Curated personal blogs (Tier 3) |
| `1_search/websites.jsonl` | GitHub READMEs / HF model cards / generic websites |
| `1_search/scope_dropped.jsonl` | Records dropped by the scope.exclude filter (with reason) |
| `1_search/filtered.jsonl` | Deduplicated + filtered + verified, with `cite_key` and `affiliation` fields |
| `5_paper/references.bib` | BibTeX entries for all filtered records (`@misc{...}` for non-papers) |
| `5_paper/references.cite_keys.json` | `paper_id` → `cite_key` lookup |
| `query.json` | Original search parameters (incl. brief scope/dimensions) |
| `state.json` | Updated search stage completion |

---

## Error Conditions

| Error | Response |
|---|---|
| Zero sources resolved | FAIL — print D2 error, stop |
| All fetchers return 0 results | FAIL — network/key issue |
| `filtered.jsonl` has < 10 papers | WARN — proceed but note low coverage |
| `bib_generator.py` not found | FAIL — required tool, block |
