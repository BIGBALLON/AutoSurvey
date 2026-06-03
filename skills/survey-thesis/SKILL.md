---
name: survey-thesis
description: Use when generating thesis candidates for an AutoSurvey run, or when /survey-thesis is invoked. Reads brief.parsed.json + filtered.jsonl (top-30 by citation), proposes 2-3 contestable thesis candidates, generates a 1-2 page sample chapter PDF preview for each candidate, then waits for user selection. Writes 2_thesis/thesis.json. The thesis-first paradigm is the load-bearing innovation of AutoSurvey.
---

# /survey-thesis

The thesis-first stage of the pipeline. Runs **after** /survey-search
has produced `1_search/filtered.jsonl` and **before** /survey-outline,
/survey-write, etc.

The agent (you, the Claude Code interpreter of this SKILL) proposes
2-3 candidate theses, each with a fully-rendered sample chapter PDF, so
the user picks not based on a 200-word abstract but on what each thesis
actually feels like in print.

This is the load-bearing innovation of the thesis is selected
**before** the corpus is organised into a taxonomy, so the taxonomy
serves the thesis (not the other way around).

---

## Invocation

```
/survey-thesis [--run-id <id>] [--auto-confirm] [--skip-thesis-preview]
```

- `--run-id <id>`: required if not invoked by /survey-run
- `--auto-confirm`: pick candidate A automatically; do not block on user
- `--skip-thesis-preview`: skip sample-chapter generation; user picks
  from text-only summaries (saves ~3 LLM calls × 900 tokens; useful in
  CI)

---

## Prerequisites

| File | Purpose |
|---|---|
| `<run_dir>/brief.parsed.json` | Topic, dimensions, style. Optionally `thesis_seed` when invoked from `/survey-pivot --new-thesis "<seed>"`; absent on first run. |
| `<run_dir>/1_search/filtered.jsonl` | Verified paper corpus (search complete) |
| `state.json` | `phases.drafting.substeps.search.status == "completed"` |

If any prerequisite is missing, halt with a precise error.

---

## Tool Resolution

```bash
AUTOSURVEY_TOOLS="${AUTOSURVEY_TOOLS:-$(git rev-parse --show-toplevel 2>/dev/null)/tools}"
[ -d "$AUTOSURVEY_TOOLS" ] || AUTOSURVEY_TOOLS="$(dirname "$(realpath "$0")")/../../tools"

VALIDATE_ARTIFACTS="$AUTOSURVEY_TOOLS/validate_artifacts.py" # built in t12
```

Note: `validate_artifacts.py` is built in t12. Until then, this skill
ships its own minimal in-line schema check; the tool call is preferred
once available.

---

## Steps

### Step 1 — Load brief + corpus

```python
import json
from pathlib import Path

brief = json.loads((run_dir / "brief.parsed.json").read_text())
TOPIC = brief["topic"]
SCOPE_INCL = brief.get("scope", {}).get("include", [])
SCOPE_EXCL = brief.get("scope", {}).get("exclude", [])
DIMENSIONS = brief.get("dimensions", [])
STYLE = brief.get("style", [])
THESIS_SEED = brief.get("thesis_seed")  # set by /survey-pivot; None on first run

papers = []
for line in (run_dir / "1_search" / "filtered.jsonl").read_text().splitlines():
    if line.strip():
        papers.append(json.loads(line))

# Top-30 papers as the anchor set the agent reasons over
papers.sort(key=lambda p: p.get("citation_count", 0) or 0, reverse=True)
anchor = papers[:30]
```

### Step 2 — Propose 2-3 candidate theses

The agent reads the brief + anchor abstracts and produces 2-3
candidates in JSON. Each candidate must be **contestable**: see the
heuristic in `shared-references/thesis-contract.md` (comparative or
negation or judgment marker). Use `THESIS_SEED` as a strong prior if
present — the seed becomes candidate A nearly verbatim, candidates B/C
are alternatives.

Required fields per candidate (see `thesis-contract.md` for full
schema):

```json
{
  "id": "A",
  "thesis": "<1-2 sentences with one contestable claim>",
  "argument_steps": [
    {"step_id": "S1", "claim": "...", "evidence_categories": ["empirical", "systems"]}
    // 3-6 steps
  ],
  "anticipated_objections": [
    {"objection": "...", "rebuttal": "..."}
    // >= 2
  ],
  "scope_disclaimers": ["..."]
}
```

**Diversity requirement** for B and C: each must differ from A in at
least one of:
- The claim's polarity (e.g. "X has consolidated" vs "X is fragmenting")
- The argument structure (steps organised by time-period vs by
  technical axis vs by counter-example)
- The "kill argument" anchor (the single fact that, if untrue, would
  invalidate the thesis)

Save the candidate set to `<run_dir>/2_thesis/candidates_draft.json`.

### Step 3 — Generate sample chapters (unless --skip-thesis-preview)

For each candidate, the agent writes a **1-2 page sample chapter** that
demonstrates the thesis's voice on a single, representative section of
the eventual paper. The sample chapter should use the 5-step argument
skeleton from `shared-references/argument-skeleton.md` so the user sees
the actual prose discipline, not just an abstract.

**Length**: 700–900 words (≈ 1.5 pages compiled).

**Choice of sample section**: pick a section the agent thinks will be
the **highest-disagreement section under this thesis**. Different
theses pick different sample sections — that itself signals to the
user how the thesis reshapes the paper.

**Compose**: write `<run_dir>/2_thesis/sample_chapters/<id>/preview.tex`
with the following preamble (using the existing NeurIPS template + the
already-generated `references.bib` from search):

```latex
\documentclass{article}
\usepackage[margin=1in]{geometry}
\usepackage{hyperref}
\usepackage{natbib}
\title{Sample Chapter: \emph{<topic>} (Thesis Candidate <id>)}
\author{AutoSurvey}
\date{}
\begin{document}
\maketitle

\begin{abstract}
This is a 1-page preview chapter to help select between thesis
candidates. The full survey will be ~30 pages with all sections.

\textbf{Thesis (Candidate <id>):} <thesis text>
\end{abstract}

\section{<chosen sample section title>}

% [CLAIM]
...

% [STEELMAN]
...

% [EVIDENCE]
\citet{cite_key} ...

% [CONCESSION]
...

% [SO-WHAT]
...

\bibliographystyle{plainnat}
\bibliography{../../5_paper/references}

\end{document}
```

**Compile** each sample with tectonic:

```bash
mkdir -p "$RUN_DIR/2_thesis/sample_chapters/$CAND_ID"
cd "$RUN_DIR/2_thesis/sample_chapters/$CAND_ID"
# bib file lives 2 levels up, inside 5_paper/
ln -sf ../../../5_paper/references.bib references.bib

if command -v tectonic >/dev/null 2>&1; then
    tectonic -X compile preview.tex 2>&1 | tail -10
elif command -v latexmk >/dev/null 2>&1; then
    latexmk -pdf -interaction=nonstopmode preview.tex
else
    echo "WARN: no LaTeX engine; sample_chapters will be .tex only"
fi
```

If the LaTeX engine is missing or compile fails, log the warning and
preserve the `.tex` (so the user can compile manually); do not abort
the thesis stage on preview failure.

Closed-set citation rule applies inside the sample chapter: every
`\cite{key}` must resolve to `1_search/filtered.jsonl`. Phantom
citations are caught by tectonic's bibtex pass (undefined references)
or by a quick grep before compile.

### Step 4 — Present to user (unless --auto-confirm)

Print a compact summary table:

```
🎯 Thesis Candidates

  A. <thesis text first 100 chars...>
     Argument steps: 5 | Objections: 3 | Sample chapter: 2_thesis/sample_chapters/A/preview.pdf

  B. <thesis text>
     ...

  C. <thesis text>
     ...

📄 Open the sample PDFs side-by-side, then enter A / B / C
   (or "edit:A" to revise A's thesis text, "regen" to regenerate all)
```

Then **block** waiting for user input. The user types one of:

- `A` / `B` / `C` — pick the candidate (proceeds to Step 5)
- `edit:A` — re-prompt agent to revise candidate A based on user
  comments; loop back to Step 3 for that candidate only
- `regen` — start Step 2 over with broader exploration
- `Ctrl-C` — abort; partial state preserved

When `--auto-confirm` is set, automatically pick `A` (or the candidate
whose thesis has highest semantic similarity to `THESIS_SEED` if present).

### Step 5 — Write final thesis.json

```python
chosen = candidates[chosen_id]
thesis_doc = {
    "thesis": chosen["thesis"],
    "thesis_id_chosen": chosen_id,
    "argument_steps": chosen["argument_steps"],
    "anticipated_objections": chosen["anticipated_objections"],
    "scope_disclaimers": chosen.get("scope_disclaimers", []),
    "candidates": [
        {
            **c,
            "sample_chapter": {
                "chapter_title": <picked title>,
                "preview_path": f"2_thesis/sample_chapters/{c['id']}/preview.tex",
                "preview_pdf": f"2_thesis/sample_chapters/{c['id']}/preview.pdf",
                "word_count": <wc>,
            } if not skip_preview else None
        }
        for c in candidates
    ]
}
(run_dir / "2_thesis").mkdir(exist_ok=True)
(run_dir / "2_thesis" / "thesis.json").write_text(json.dumps(thesis_doc, indent=2))
```

Validate the result with the schema rules in `thesis-contract.md` (or
`validate_artifacts.py` once t12 lands). On schema failure, halt with
a precise error.

### Step 6 — Update state.json

```python
state["phases"]["drafting"]["substeps"]["thesis"]["status"] = "completed"
state["phases"]["drafting"]["substeps"]["thesis"]["chosen_id"] = chosen_id
state["phases"]["drafting"]["substeps"]["thesis"]["candidates_count"] = len(candidates)
```

Print:

```
✅ Thesis selected: candidate <id>
   "<thesis first 100 chars>..."
   Argument steps: <n> | Objections: <m>
   2_thesis/thesis.json written ({size} bytes)

Next: /survey-outline (or /survey-run continues automatically)
```

---

## Output Files

| File | Contents |
|---|---|
| `2_thesis/thesis.json` | Final thesis doc (chosen + all candidates kept for audit) |
| `2_thesis/candidates_draft.json` | The 2-3 raw candidates from Step 2 (input to Step 3) |
| `2_thesis/sample_chapters/<id>/preview.tex` | Sample chapter source per candidate |
| `2_thesis/sample_chapters/<id>/preview.pdf` | Compiled preview (if LaTeX available) |
| `state.json` | `phases.drafting.substeps.thesis.status = completed` |

---

## Error Conditions

| Error | Response |
|---|---|
| `1_search/filtered.jsonl` missing or empty | FAIL — re-run /survey-search |
| Agent produces only 1 candidate | FAIL — diversity requirement violated; retry once with explicit "produce 2 alternatives" instruction; second failure → STOP |
| User picks `regen` more than 3 times | WARN — print "agent has produced 4 sets; consider tightening `scope.exclude` in brief.md, or re-running with `/survey-pivot --new-thesis '<seed>'` to bias candidate generation toward a specific claim" |
| LaTeX compile fails on a sample preview | WARN — preserve .tex, mark candidate as "preview unavailable", let user pick from text |
| Final thesis fails schema (e.g. < 2 objections) | FAIL — re-prompt agent for the missing field, retry; second failure → STOP |

---

## See Also

- `shared-references/thesis-contract.md` — full schema + contestable claim heuristic
- `shared-references/argument-skeleton.md` — used inside sample chapters
- `skills/survey-pivot/SKILL.md` — `/survey-pivot --new-thesis` reuses the same machinery
