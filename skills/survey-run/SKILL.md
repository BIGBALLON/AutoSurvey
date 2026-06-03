---
name: survey-run
description: Use when generating a full survey paper from a structured brief, or when /survey-run is invoked. End-to-end AutoSurvey orchestrator — runs the resumable phase-driven pipeline (Phase 1 Drafting: init → refine_brief → search → thesis → outline-sketch; Phase 2 Arguing: per-section claim-mining + 5-step skeleton; Phase 3 Polishing: 2-persona review → audits → compile + evidence dashboard) tracked in state.json.
---

# /survey-run

End-to-end orchestrator for AutoSurvey. Runs the full pipeline —
refine_brief → search → thesis → outline_sketch → arguing → review →
audits → compile — across the three phases (Drafting / Arguing /
Polishing), tracking stage state in `state.json` to support resumable
runs.

---

## Invocation

```
/survey-run --brief <path/to/brief.md>
            [--sources auto]
            [--max-papers 200]
            [--year-start YYYY]
            [--venue generic|neurips|acl|ieee]
            [--resume <run-id>]
            [--from-stage refine_brief|search|thesis|outline_sketch|arguing|review|audits|compile]
            [--interactive] [--auto-confirm]
```

**`--brief` is REQUIRED.** A free-form markdown file describing the topic,
scope, dimensions, sources, and style. There is no positional `<topic>`
form. See `examples/briefs/` and `README.md` "Writing a Brief" for
authoring guidance.

**Defaults:**
- `--sources auto`
- `--max-papers 200` (API budget; the survey itself is not page-capped)
- `--venue generic`

The pipeline always runs at full strength: every audit fires, every
review round runs, every fetcher tries the full priority chain.
`--auto-confirm` skips the human checkpoint between review rounds for
CI use; nothing else is gated.

---

## Brief Required — Fail-Fast Check

At the very top of the skill, before any directory creation, validate the
invocation:

```python
if not brief_path and not resume_id:
    print(
        "Error: --brief is required.\n\n"
        "AutoSurvey needs a structured brief to produce a high-quality survey.\n"
        "See examples/briefs/ or docs section \"Writing a Brief\" in README.md.\n\n"
        "Quickstart:\n"
        " cp examples/briefs/long-context-extension.md ~/my-brief.md && edit it",
        file=sys.stderr,
    )
    sys.exit(1)
```

When `--resume` is supplied, the existing run directory's `brief.md` /
`brief.parsed.json` are reused; no `--brief` argument is required at resume
time.

---

## Startup: Create or Resume Run Directory

### New run

```python
import os, re, datetime, json, shutil
from pathlib import Path

brief_path = Path("<--brief argument>")

# Topic for slug derivation comes from brief.parsed.json AFTER refine_brief
# runs. For the run directory, slugify the brief filename as a placeholder
# until refine_brief produces brief.parsed.json with the canonical topic.
slug_seed = brief_path.stem
slug = re.sub(r'[^a-z0-9]+', '-', slug_seed.lower()).strip('-')[:40]
timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
run_id = f"{slug}-{timestamp}"

# Output lands under the CURRENT WORKING DIRECTORY by default
# (./.autosurvey/runs/<id>/), so each project keeps its surveys local
# and self-contained. Override the base with AUTOSURVEY_RUNS_DIR if you
# want a fixed central location. Resume must run from the same base.
RUNS_BASE = Path(os.environ.get("AUTOSURVEY_RUNS_DIR") or (Path.cwd() / ".autosurvey" / "runs"))
run_dir = RUNS_BASE / run_id
run_dir.mkdir(parents=True, exist_ok=True)

# Copy the brief into the run dir so the run is self-contained / resumable
shutil.copy(brief_path, run_dir / "brief.md")

# Initialize state.json (phase-model schema)
#
# Three phases group the substeps. Each phase has a status field plus
# a substeps dict; substeps still execute in a deterministic linear
# order (Phase 1 → 2 → 3). Phase-2 (Arguing) is a per-section inner
# loop; iteration tracking is recorded in state["phases"]["arguing"]["iterations"].
state = {
    "run_id": run_id,
    "brief_path": str(brief_path),
    "started_at": datetime.datetime.now().isoformat(),
    "phases": {
        "drafting": {
            "status": "pending",
            "substeps": {
                "refine_brief": {"status": "pending"},
                "search": {"status": "pending"},
                "thesis": {"status": "pending"},
                "outline_sketch": {"status": "pending"}
            }
        },
        "arguing": {
            "status": "pending",
            # iterations[] is populated per-section by /survey-write
            "iterations": []
        },
        "polishing": {
            "status": "pending",
            "substeps": {
                "review": {"status": "pending"},
                "checkpoint": {"status": "pending"},
                "audits": {"status": "pending"},
                "compile": {"status": "pending"}
            }
        }
    }
}
(run_dir / "state.json").write_text(json.dumps(state, indent=2))
```

Print: `🚀 AutoSurvey run started: {run_id}`
Print: ` Directory: {run_dir}/` (resolved absolute path)
Print: ` Brief: {brief_path}`

### Resuming (`--resume <run-id>`)

```python
RUNS_BASE = Path(os.environ.get("AUTOSURVEY_RUNS_DIR") or (Path.cwd() / ".autosurvey" / "runs"))
run_dir = RUNS_BASE / resume_id
if not (run_dir / "state.json").is_file():
    print(
        f"Error: no run '{resume_id}' under {RUNS_BASE}.\n"
        "Resume from the same directory you started the run in, or set "
        "AUTOSURVEY_RUNS_DIR to the base that holds it.",
        file=sys.stderr,
    )
    sys.exit(1)
state = json.loads((run_dir / "state.json").read_text())

# Schema sanity check: state.json must carry the canonical phases dict
# (drafting / arguing / polishing). Anything else is either corrupted
# or written by a different tool and we refuse to resume against it.
required_phases = {"drafting", "arguing", "polishing"}
phases = state.get("phases")
if not isinstance(phases, dict) or set(phases) != required_phases:
    print(
        "Error: state.json does not match the AutoSurvey schema "
        "(expected a 'phases' dict with drafting / arguing / polishing).\n"
        "Start a new run with /survey-run --brief <brief.md>.",
        file=sys.stderr,
    )
    sys.exit(1)

run_id = state["run_id"]
# Brief is already copied into the run dir; topic comes from brief.parsed.json
# (populated by drafting.refine_brief on the original run).
```

Print: `♻️ Resuming run: {run_id}`
Print: ` Phases: drafting={state['phases']['drafting']['status']}, arguing={...}, polishing={...}`

### `--from-stage` flag

If `--from-stage X` is provided, forcibly reset substep X (and all
downstream substeps in the linear order below) to `"pending"` regardless
of their current status. Short aliases (`outline`, `write`, `verify`)
are accepted as ergonomic shorthand for the canonical substep names.

```python
# Linear substep order across the 3 phases. Phase-2 (arguing) is a single
# logical substep here even though internally it is a per-section loop;
# Inner-loop tracking is recorded via state["phases"]["arguing"]["iterations"].
SUBSTEP_ORDER = [
    # Phase 1 — Drafting
    "refine_brief", # tools/refine_brief.py → brief.parsed.json
    "search", # /survey-search → 1_search/filtered.jsonl
    "thesis", # /survey-thesis → 2_thesis/thesis.json (+ sample chapters)
    "outline_sketch", # /survey-outline → 4_outline/outline.json
    # Phase 2 — Arguing (per-section inner loop)
    "arguing", # /survey-write → 5_paper/sections/*.{skeleton.md,tex}
    # Phase 3 — Polishing
    "review", # /survey-review (2 personas + author)
    "checkpoint", # human checkpoint between review rounds (skipped by --auto-confirm)
    "audits", # /survey-verify (validate_artifacts + audit_writing + hard_gate)
    "compile", # tectonic + build_evidence_dashboard
]

# Short aliases — ergonomic shorthand for SUBSTEP_ORDER names.
SUBSTEP_ALIAS = {
    "outline": "outline_sketch",
    "write": "arguing",
    "verify": "audits",
}

if from_stage:
    canonical = SUBSTEP_ALIAS.get(from_stage, from_stage)
    if canonical not in SUBSTEP_ORDER:
        print(f"Error: --from-stage {from_stage!r} is not a valid substep.", file=sys.stderr)
        print(f" Valid substeps: {SUBSTEP_ORDER}", file=sys.stderr)
        sys.exit(2)
    idx = SUBSTEP_ORDER.index(canonical)
    for substep in SUBSTEP_ORDER[idx:]:
        # Reset both the substep status and its containing phase status
        for phase in ("drafting", "arguing", "polishing"):
            phase_state = state["phases"][phase]
            if substep == "arguing" and phase == "arguing":
                phase_state["status"] = "pending"
                phase_state["iterations"] = []
            elif substep in phase_state.get("substeps", {}):
                phase_state["substeps"][substep]["status"] = "pending"
                phase_state["status"] = "pending"
    # save state
```

---

## Pipeline Execution (— Phase Orchestration)

The orchestrator runs three phases in sequence. Within Phase 2 (Arguing)
the writing step is a per-section inner loop with self-review.

For each substep: if its `status == "completed"` → **skip** (print
`⏭️ Skipping {phase}/{substep} (already completed)`).

```
Phase 1 — Drafting
   refine_brief → tools/refine_brief.py (parses + validates the user's brief.md)
   search → /survey-search
   thesis → /survey-thesis ← LOAD-BEARING: blocks for user pick
   outline_sketch → /survey-outline ← merges old cluster + outline

Phase 2 — Arguing (per-section inner loop)
   for section in outline.sections:
       /survey-write --section {id}
         step a: lazy-mine claims for primary_papers not yet in claims_cache.jsonl
         step b: write sections/{id}.skeleton.md (5 H3 buckets)
         step c: compose sections/{id}.tex with % [CLAIM]/[STEELMAN]/...anchors
         step d: self-review (one fresh-thread agent call); if FAIL, retry b+c once
       record state["phases"]["arguing"]["iterations"].append({...})
       on-demand: gen_taxonomy_tikz / gen_timeline / gen_scaling_plot /
                  build_dimension_tables / scaffold_cross_cutting_matrix
                  invoked inside step c when the section needs a figure/table

Phase 3 — Polishing
   review → /survey-review (2 personas: senior + skeptic)
   checkpoint → block until 7_review/round{N}/checkpoint.json user_decisions are
                filled. --auto-confirm short-circuits.
   audits → /survey-verify
                  → tools/validate_artifacts.py (thesis + claims + cite-key closed-set)
                  → tools/audit_writing.py (5-anchor + narrative + thesis coherence)
                  → existing hard_gate (verify_papers + bib phantom + numeric_grounding)
   compile → tectonic main.tex
                tools/build_evidence_dashboard.py → survey.evidence.html
                copy main.pdf to run root
```

### Phase orchestrator pseudocode

```python
def run_phase_drafting(state, run_dir, args):
    p = state["phases"]["drafting"]
    p["status"] = "in_progress"
    if p["substeps"]["refine_brief"]["status"] != "completed":
        if not args.brief:
            print("Error: --brief <path> is required.", file=sys.stderr)
            print("       Copy examples/briefs/long-context-extension.md "
                  "and edit, then re-run.", file=sys.stderr)
            sys.exit(2)
        run_substep_refine_brief(state, run_dir, args)
        p["substeps"]["refine_brief"]["status"] = "completed"
    save(state)

    for substep, skill in [("search", "/survey-search"),
                           ("thesis", "/survey-thesis"),
                           ("outline_sketch", "/survey-outline")]:
        if p["substeps"][substep]["status"] != "completed":
            invoke_skill(skill, run_id=state["run_id"],
                         auto_confirm=args.auto_confirm)
        save(state)

    p["status"] = "completed"
    save(state)


def run_phase_arguing(state, run_dir, args):
    p = state["phases"]["arguing"]
    p["status"] = "in_progress"
    save(state)

    outline = json.loads((run_dir / "4_outline" / "outline.json").read_text())
    done_section_ids = {it["section_id"] for it in p["iterations"]
                        if it["write_status"] == "completed"}

    for sec in outline["sections"]:
        sid = sec["id"]
        if sid in done_section_ids:
            print(f"⏭️ Skipping arguing/{sid} (already completed)")
            continue
        invoke_skill("/survey-write", run_id=state["run_id"], section=sid,
                     auto_confirm=args.auto_confirm)
        # /survey-write appends an iteration record to state.phases.arguing
        save(state)

    p["status"] = "completed"
    save(state)


def run_phase_polishing(state, run_dir, args):
    p = state["phases"]["polishing"]
    p["status"] = "in_progress"
    save(state)

    if p["substeps"]["review"]["status"] != "completed":
        invoke_skill("/survey-review", run_id=state["run_id"],
                     auto_confirm=args.auto_confirm)
        save(state)

    # Human checkpoint between review rounds (unless --auto-confirm)
    if not args.auto_confirm:
        cp = run_dir / "7_review" / "round1" / "checkpoint.json"
        if cp.exists():
            doc = json.loads(cp.read_text())
            if doc.get("checkpoint_status") == "pending_user":
                print(
                    "⏸️ Human checkpoint pending. Edit user_decisions in:\n"
                    f" {cp}\n"
                    "Then re-run: /survey-run --resume {state['run_id']}"
                )
                p["substeps"]["checkpoint"]["status"] = "pending_user"
                save(state)
                sys.exit(0)
    p["substeps"]["checkpoint"]["status"] = "completed"

    if p["substeps"]["audits"]["status"] != "completed":
        invoke_skill("/survey-verify", run_id=state["run_id"])
        save(state)

    if p["substeps"]["compile"]["status"] != "completed":
        run_substep_compile(state, run_dir) # tectonic + evidence dashboard
        save(state)

    p["status"] = "completed"
    save(state)


# Top-level orchestration
run_phase_drafting(state, run_dir, args)
run_phase_arguing (state, run_dir, args)
run_phase_polishing(state, run_dir, args)
print_final_report(state, run_dir)
```

### --auto-confirm chaining

When set:
- `/survey-thesis` picks candidate A (or thesis_seed-most-similar)
- Phase 3 `checkpoint` short-circuits to `auto_confirmed`

### Compile substep — evidence dashboard

After tectonic produces `main.pdf`, run:

```bash
python3 "$AUTOSURVEY_TOOLS/build_evidence_dashboard.py" \
    "$RUN_DIR" \
    --output "$RUN_DIR/survey.evidence.html"
```

The dashboard is a single HTML file (no backend) listing every `\cite{}`
context alongside the supporting `atomic_claims[].quote` from
`claims_cache.jsonl`, with arXiv URLs for click-through verification.
See t13.

---

# Substep implementation details

The numbered Stage blocks below carry the deterministic logic each
substep runs internally — `refine_brief` validation rules, search
source fan-out, the compile-gate engine resolution, etc. When a
substep is invoked, the orchestrator runs the corresponding Stage
block.

Stage → substep mapping:

| Stage | substep | Phase |
|---|---|---|
| Stage 0 (refine_brief) | `refine_brief` | drafting |
| Stage 1 (survey-search) | `search` | drafting |
| Stage 7 (survey-review) | `review` | polishing |
| Stage 8 (survey-verify) | `audits` | polishing |
| Stage 9 (compile) | `compile` | polishing |

(Outline-sketch and writing run via the per-skill SKILL.md files
`survey-outline/SKILL.md` and `survey-write/SKILL.md`; their substep
implementations are not duplicated here. Cluster, extract, and figure
generation are absorbed into outline_sketch / arguing as inner steps.)

---

### Stage 0 — Refine the brief

```
Status check: state["phases"]["drafting"]["substeps"]["refine_brief"]["status"]
```

This stage extracts structured JSON from the user's free-form `brief.md`.
The agent (you, the Claude Code interpreter of this SKILL) performs the
extraction inline; the Python tool only validates + writes + displays.

**Why agent-driven:** AutoSurvey runs without external LLM API keys. The
Python tool handles deterministic IO; the structural reasoning (extracting
from prose) is the agent's job.

If pending:

1. **Update state:** `refine_brief.status = "in_progress"`.

2. **Resolve the tool path:**
   ```bash
   AUTOSURVEY_TOOLS="${AUTOSURVEY_TOOLS:-$(git rev-parse --show-toplevel 2>/dev/null)/tools}"
   [ -d "$AUTOSURVEY_TOOLS" ] || AUTOSURVEY_TOOLS="$(dirname "$(realpath "$0")")/../../tools"
   ```

3. **Read the brief.md** content.

4. **Verify minimum length** (≥50 words). If not, error out per
   `refine_brief.py` validation.

5. **Extract structured JSON** by reading the brief and producing this shape
   (see `skills/shared-references/brief-contract.md` for the canonical
   schema):

   ```json
   {
     "topic": "<one-line subject>",
     "scope": {"include": [...], "exclude": [...]},
     "sources": {
       "categories": [...], // any of: arxiv, semantic_scholar, openalex, acl_anthology, pubmed, tech_reports, blogs, model_cards, github_readmes, websites
       "year_range": [start, end],
       "github_repos": [...],
       "model_cards": [...]
     },
     "dimensions": [{"name": "...", "description": "..."}, ...], // 3-12 entries
     "style": ["..."],
     "configuration": {
       "trends_section": "include" | "skip"
     },
     "_uncertainties": ["..."] // any low-confidence inferences
   }
   ```

   Default behaviours when the brief is silent:
   - `sources.categories` defaults to
     `["arxiv", "semantic_scholar", "openalex", "tech_reports", "blogs"]`.
   - `sources.year_range` defaults to `[current_year - 5, current_year]`.
   - `configuration.trends_section` defaults to `"include"`.
   - `github_repos` / `model_cards` default to `[]`.

6. **Save the candidate JSON** to `$RUN_DIR/.refine_candidate.json` (use the
   Write tool).

7. **Run the validator:**
   ```bash
   python3 "$AUTOSURVEY_TOOLS/refine_brief.py" \
       --brief "$RUN_DIR/brief.md" \
       --candidate "$RUN_DIR/.refine_candidate.json" \
       --output "$RUN_DIR/brief.parsed.json" \
       --auto-confirm \
       ${INTERACTIVE:+--interactive}
   ```

8. **On validation failure**, the validator returns non-zero with a clear
   error (e.g. "fewer than 3 dimensions", "could not identify a topic").
   Re-extract with the feedback, write a new candidate, re-run. If after a
   second attempt validation still fails (≤50 words, no topic, <3 dimensions
   and user declines synthesised additions): print the error returned by
   `refine_brief.py` verbatim, set `refine_brief.status = "failed"`,
   **STOP pipeline**.

9. **On success:** read `brief.parsed.json`, capture `topic`, `dimensions[]`,
   `scope.include / .exclude`, `style[]`, `configuration{}`, `sources{}`,
   surface them to subsequent stages. Update
   `refine_brief.status = "completed"`.

Print: `[0/9] 📝 Brief refined — topic="{topic}", {N} dimensions, {M} sources`

---

### Stage 1 — Survey Search

```
Status check: state["phases"]["drafting"]["substeps"]["search"]["status"]
```

If pending:
1. Update state: `search.status = "in_progress"`
2. Invoke `/survey-search --run-id {run_id} --max-papers {max_papers}` (the
   search skill reads `brief.parsed.json` for topic, scope, dimensions,
   sources, year range; the bare `--year-start` / `--sources` overrides
   are still accepted on the command line).
3. On success: update state `search.status = "completed"`, capture `papers_verified`, `papers_filtered`
4. On failure: update `search.status = "failed"`, print error, **STOP pipeline**

Print: `[1/9] 🔍 Search complete — {papers_filtered} papers retrieved`

(Stage 3 — `outline_sketch` — and Stage 6 — `arguing` — run via
`skills/survey-outline/SKILL.md` and `skills/survey-write/SKILL.md`
respectively. The orchestrator drives them via the substep status in
`state.json`; their internal mechanics are not duplicated here.)

---

### Stage 7 — Survey Review

If pending:
1. Verify `write.status == "completed"`
2. Update `review.status = "in_progress"`
3. Invoke `/survey-review` (default 2 rounds; `--rounds N` overrides)
4. On success: update `review.status = "completed"`

Print: `[7/9] 📝 Review complete — {rounds} rounds, {fixes} fixes applied`

---

### Stage 8 — Survey Verify

If pending:
1. Verify `write.status == "completed"` AND (`review.status == "completed"` OR `review.status == "skipped"`)
2. Update `verify.status = "in_progress"`
3. Invoke `/survey-verify`
4. Read `{run_dir}/6_verify/CITATION_VERIFY.json`

```python
verify_result = json.loads((run_dir / "6_verify" / "CITATION_VERIFY.json").read_text())
hard_gate = verify_result["hard_gate"]
```

5. If `hard_gate == "FAIL"`:
   ```
   ❌ HARD GATE FAILED — phantom citations detected.
      These keys must be fixed before compile:
      {phantom_keys}

   Fix: Open the affected .tex files and remove/replace phantom \cite{} keys.
   Then re-run: /survey-run --resume {run_id} --from-stage verify
   ```
   Update `verify.status = "failed"`. **STOP.**

6. If `hard_gate == "PASS"`:
   Update `verify.status = "completed"`, capture audit verdicts.

7. Run compile gate check:

```bash
AUTOSURVEY_TOOLS="${AUTOSURVEY_TOOLS:-$(git rev-parse --show-toplevel 2>/dev/null)/tools}"
VERIFIER="$AUTOSURVEY_TOOLS/verify_survey_audits.sh"

if [ -f "$VERIFIER" ]; then
    bash "$VERIFIER" "$RUN_DIR"
    EXIT_CODE=$?
else
    python3 -c "
import json, sys
data = json.load(open('$RUN_DIR/6_verify/CITATION_VERIFY.json'))
if data['hard_gate'] != 'PASS':
    print('FAIL:', data['hard_gate'], file=sys.stderr)
    sys.exit(1)
print('PASS')
"
    EXIT_CODE=$?
fi
```

If `EXIT_CODE != 0`: print gate failure details, **STOP pipeline**.

Print: `[8/9] ✅ Verification complete — hard_gate=PASS, {N} citations audited`

---

### Stage 9 — Compile

If pending:
1. Verify `verify.status == "completed"`
2. Update `compile.status = "in_progress"`
3. Resolve LaTeX engine in this preference order (any one is sufficient):

```bash
# Engine resolution: tectonic preferred (single binary, auto-fetches packages),
# pdflatex / xelatex / lualatex acceptable, latexmk wrapping any of them is also fine.
ENGINE=""
for cmd in tectonic latexmk pdflatex xelatex lualatex; do
    if command -v "$cmd" &>/dev/null; then
        ENGINE="$cmd"
        break
    fi
done

if [ -z "$ENGINE" ]; then
    echo "ERROR: No LaTeX engine found. Install one of:" >&2
    echo " brew install tectonic # macOS, single 50MB binary, auto-fetches packages" >&2
    echo " apt-get install tectonic # Linux" >&2
    echo " brew install --cask mactex # full TeX Live (3 GB)" >&2
    echo " apt-get install texlive-full" >&2
    # state.json: compile.status = "blocked", record "no_latex_engine"
    exit 1
fi
```

4. Compile (preference: tectonic for portability, fallback to latexmk for traditional setups):

```bash
cd "$RUN_DIR/5_paper"
case "$ENGINE" in
    tectonic)
        # tectonic auto-runs LaTeX/BibTeX/LaTeX/LaTeX as needed; downloads missing packages
        tectonic -X compile main.tex
        ;;
    latexmk)
        # Multi-pass with bibtex; uses pdflatex by default
        latexmk -pdf -interaction=nonstopmode main.tex
        ;;
    pdflatex|xelatex|lualatex)
        # Manual multi-pass (slower; tectonic is preferred above)
        for _ in 1 2 3; do
            "$ENGINE" -interaction=nonstopmode main.tex
        done
        bibtex main || true
        for _ in 1 2; do
            "$ENGINE" -interaction=nonstopmode main.tex
        done
        ;;
esac
```

5. After compile, copy outputs to run root:
   ```bash
   cp "$RUN_DIR/5_paper/main.pdf" "$RUN_DIR/main.pdf"
   ```

6. Try HTML render (optional, for web preview):
   ```bash
   if command -v pandoc &>/dev/null; then
       pandoc "$RUN_DIR/5_paper/main.tex" -o "$RUN_DIR/survey.html" 2>&1 || \
         echo "WARN: pandoc HTML conversion failed — PDF only"
   else
       echo "WARN: render-html skipped — install pandoc for HTML preview"
   fi
   ```

7. On compile success: update `compile.status = "completed"` (record engine used,
   page count, file size).
8. On compile failure: update `compile.status = "failed"`, print LaTeX errors, **STOP**.

Print: `[9/9] 📄 Compile complete — main.pdf generated (N pages, X KB, engine={tectonic|latexmk|...})`

---

## Final Report

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ AutoSurvey complete!

Topic: {topic} (extracted by refine_brief from --brief)
Run ID: {run_id}

📄 PDF: {run_dir}/main.pdf ({N} pages)
🌐 HTML: {run_dir}/survey.html

📊 Stats:
   Brief dimensions: {dim_count}
   Papers retrieved: {papers_retrieved}
   Papers cited: {papers_cited}
   Cards extracted: {cards_count} (avg completeness {pct}%)
   Sections: {section_count}
   Review rounds: {rounds}
   Phantom fixes: {phantom_stripped} (stripped)
   Citation audits: {citation_audited}
   Hard gate: PASS
   Claim audit: {claim_audit_verdict}
   Numeric grounding: {numeric_grounding_verdict}
   Kill argument: {kill_argument_verdict}

⏱️ Total time: ~{elapsed_min} minutes
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## State.json Final Schema

The orchestrator writes a `state.json` with three phases (drafting /
arguing / polishing). The example below shows a completed run; mid-run
files have `status: "in_progress"` / `"pending"` for the unfinished
substeps. `--resume` performs a sanity check that the `phases` dict
contains exactly those three keys and refuses to operate on anything
else (see the "Resuming" section above).

```json
{
  "run_id": "long-context-extension-20260528-143022",
  "brief_path": "/path/to/brief.md",
  "topic": "Long-Context Extension Methods for Pretrained Language Models",
  "started_at": "2026-05-28T14:30:22",
  "completed_at": "2026-05-28T15:12:45",
  "phases": {
    "drafting": {
      "status": "completed",
      "substeps": {
        "refine_brief": {"status": "completed", "dimensions": 9, "uncertainties": 0},
        "search":       {"status": "completed", "papers_retrieved": 210, "papers_filtered": 178},
        "thesis":       {"status": "completed", "argument_steps": 5, "objections": 3},
        "outline_sketch": {"status": "completed", "sections": 11,
                            "organising_principle": "B", "tier_axis_present": true}
      }
    },
    "arguing": {
      "status": "completed",
      "iterations": [
        {"section_id": "01_intro",   "iteration": 1, "phantom_stripped": 0},
        {"section_id": "02_routing", "iteration": 2, "phantom_stripped": 1}
      ]
    },
    "polishing": {
      "status": "completed",
      "substeps": {
        "review":     {"status": "completed", "rounds": 2, "fixes": 4},
        "checkpoint": {"status": "completed"},
        "audits":     {"status": "completed", "hard_gate": "PASS",
                       "numeric_grounding": "PASS", "citations_audited": 287},
        "compile":    {"status": "completed", "pages": 22, "pdf": "main.pdf"}
      }
    }
  }
}
```

---

## Error Handling Summary

| Substep (phase) | Failure condition | Action |
|---|---|---|
| refine_brief (drafting) | brief <50 words, no topic, dimensions<3 (and user declines additions) | STOP — print refine_brief error |
| search (drafting) | 0 results from all sources | STOP — check API keys |
| search (drafting) | fetch error | WARN, continue with partial results |
| thesis (drafting) | LLM parse failure on candidate generation | Retry once; STOP if second failure |
| outline_sketch (drafting) | no sections generated, or thesis-binding closed-set check fails twice | STOP |
| arguing (per-section, /survey-write) | per-paper card synthesis fails twice | STOP — fall back to template; agent rerun with `--from-stage arguing` |
| arguing (per-section, /survey-write) | phantom >20% in any section | STOP — manual fix required |
| arguing (per-section, /survey-write) | required figure-render tool unavailable for a figure the agent chose | WARN, drop the figure; proceed |
| review (polishing) | reviewer-independence violated | STOP — restart from a fresh thread |
| audits (polishing) | hard_gate=FAIL | STOP — list phantom keys |
| audits (polishing) | claim_audit=FAIL (≥10% REMOVE) | STOP — fix unsupported claims |
| audits (polishing) | numeric_grounding=FAIL at submission | STOP — pure-narrative body section |
| audits (polishing) | kill_argument=PENDING at submission | STOP — address HIGH findings |
| audits (polishing) | structural-template invariant 1–8 FAIL | STOP — fix per the invariant message |
| compile (polishing) | LaTeX errors | Print errors; STOP |

---

## Resume Examples

```bash
# Resume after taxonomy gate (user selected taxonomy, pipeline can continue)
/survey-run --resume long-context-extension-20260528-143022

# Re-run brief refinement after editing brief.md
/survey-run --resume long-context-extension-20260528-143022 --from-stage refine_brief

# Re-run writing stage only (after manual phantom fix)
/survey-run --resume long-context-extension-20260528-143022 --from-stage write

# Re-run verify (audits at the strictest level by default)
/survey-run --resume long-context-extension-20260528-143022 --from-stage verify

# Skip to compile (everything else done)
/survey-run --resume long-context-extension-20260528-143022 --from-stage compile
```

---

## Output Files

| File | Description |
|---|---|
| `state.json` | Full run state — stage statuses, stats, timestamps |
| `brief.md` | Copy of the user's input brief |
| `brief.parsed.json` | Structured form of the brief (refine_brief output) |
| `brief.derived_schema.json` | Per-run extraction schema (refine_brief output) |
| `main.pdf` | Final survey paper (root of run dir) |
| `survey.html` | HTML version (root of run dir) |
| `1_search/filtered.jsonl` | Verified paper corpus |
| `1_search/cards.jsonl` | Per-paper detail cards (extract stage) |
| `3_taxonomy.json` | Chosen taxonomy |
| `4_outline/outline.json` | Section structure |
| `5_paper/main.tex` | LaTeX source |
| `6_verify/CITATION_VERIFY.json` | Citation audit verdicts (incl. numeric_grounding) |
