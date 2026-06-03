---
name: survey-outline
description: Use when building the section structure of an AutoSurvey paper, or when /survey-outline is invoked. "outline-sketch" produces the section structure in a single thesis-aware pass — clustering and taxonomy proposal are inner steps, not separate substeps. Each section binds to one argument_step and ships an argument_skeleton (5-step CLAIM/STEELMAN/EVIDENCE/CONCESSION/SO-WHAT) that the writing stage will flesh out.
---

# /survey-outline (outline-sketch)

Generates the survey's section structure **conditioned on the chosen
thesis**. Clustering and taxonomy proposal happen as inner steps of
this skill, so the section structure is decided in one thesis-aware
pass.

Contract:

- Section structure is one of **3 organising principles** the agent
  proposes given the thesis (argument-step bound / time-period bound /
  objection-grouped); the user picks one.
- Each section **must** bind to one `argues_for_thesis_step` from
  `2_thesis/thesis.json`. The outline is itself a contract: every
  `argument_step.step_id` is covered by ≥ 1 section.
- Each section ships a **5-step argument_skeleton** draft (Claim,
  Steelman, evidence_claim_keys, Concession, So-what). Phase 2 fleshes
  these out per-paragraph; the outline already pins the arc.
- Optional `tier_axis` field on the outline (5–7 generation/scale tiers
  + per-tier cell entries) unlocks the matrix-layout taxonomy figure.
- Optional `maturity_tier` field per body section, one of
  `mature` / `frontier` / `speculative`. Orthogonal to `tier_axis`:
  `tier_axis` carves the *technical* dimension; `maturity_tier`
  carves the *epistemic* one (settled vs. contested vs. open). When
  used, ≥ 2 distinct tiers must appear so the survey forms a clear
  "mature → frontier → speculative" spectrum (validated by
  `tools/validate_outline.py`).

---

## Invocation

```
/survey-outline [--min-sections 6] [--max-sections 15] [--run-id <id>]
```

---

## Prerequisites

- `<run_dir>/2_thesis/thesis.json` must exist and be valid (chosen
  candidate present, ≥ 3 argument_steps, ≥ 2 anticipated_objections)
- `<run_dir>/1_search/filtered.jsonl` must exist
- `<run_dir>/brief.parsed.json` must exist
- State: `phases.drafting.substeps.thesis.status == "completed"`

The skill writes its outline directly to `4_outline/outline.json`.

---

## Step 0 — Load thesis + brief + papers

```python
import json
from pathlib import Path

thesis = json.loads((run_dir / "2_thesis" / "thesis.json").read_text())
THESIS_TEXT = thesis["thesis"]
ARGUMENT_STEPS = thesis["argument_steps"] # [{step_id, claim, evidence_categories}]
OBJECTIONS = thesis["anticipated_objections"]

brief = json.loads((run_dir / "brief.parsed.json").read_text())
TOPIC = brief["topic"]
DIMENSIONS = brief.get("dimensions", [])
STYLE = brief.get("style", [])
TRENDS_OPT = brief.get("configuration", {}).get("trends_section", "include")
INCLUDE_TRENDS = TRENDS_OPT != "skip"

papers = []
for line in (run_dir / "1_search" / "filtered.jsonl").read_text().splitlines():
    if line.strip():
        papers.append(json.loads(line))
```

## Step 1 — Propose 3 organising-principle candidates

The agent proposes 3 candidate section structures, each tied to a
different organising principle:

- **Candidate A — Argument-step bound** (default; recommended).
  Sections map 1:1 onto `argument_steps[]`. Each step gets one section
  whose Claim is essentially the step's claim, sharpened. Best when
  the thesis already structures the field well.
- **Candidate B — Time-period bound**. Sections divide papers by
  generation / era (e.g. "GPT-3 era 2020-22" / "Llama era 2023" /
  "MoE era 2024-26"). Best when the thesis is about a temporal
  transition. Each argument_step covered by ≥ 1 section but the
  mapping may be many:1.
- **Candidate C — Objection-grouped**. Sections organised around the
  thesis's `anticipated_objections` — each objection becomes a section
  that systematically engages and answers it. Best when the thesis is
  contrarian.

Save to `<run_dir>/4_outline/outline_candidates.json`. The user picks
one (or `--auto-confirm` selects A).

## Step 2 — Generate section list with argument_skeleton

For the chosen candidate, the agent produces the section list. Each
section MUST include:

```json
{
  "id": "02_architecture",
  "name": "Architectures: Dense, MoE, and Attention Innovations",
  "argues_for_thesis_step": "S1",
  "argument_skeleton": {
    "claim": "<1 sentence>",
    "steelman": "<1 paragraph>",
    "evidence_claim_keys": [],
    "concession": "<1 paragraph>",
    "so_what": "<1 sentence>"
  },
  "primary_papers": ["touvron2023llama", "deepseek2024v3", ...],
  "secondary_papers": [...],
  "key_points": [...]
}
```

`evidence_claim_keys` is empty at this stage (claim_ids are populated
by the lazy claim-mining loop in Phase 2 / `/survey-write`).
`primary_papers` IS populated now using closed-set selection from
`filtered.jsonl`.

The Intro and Conclusion sections do not have `argues_for_thesis_step`
(they cover the whole thesis, not one step). Trends & Open Problems
sections have `argues_for_thesis_step` if applicable, otherwise
`null`.

### Structural-template requirements

The outline must satisfy the structural invariants in
`shared-references/structural-template.md`. The reference outline
that the audit suite calibrates against lives at
`shared-references/reference-assets/outline.example.json` — read it
before sketching to see the exact field shape (8 sections, the
`cross_cutting_matrix` slot under `04_systems`, the paired
`open_problems` × `future_directions` items). Concretely:

1. **6–12 top-level sections**, of which **≥ 4 carry 3 or more
   subsections**. Use the wider end of the window when the brief's scope
   (many dimensions / eras / paradigm transitions) warrants giving each
   its own section rather than merging — do not force a broad topic back
   into ~8 sections. Flat outlines are rejected by `validate_outline.py
   --strict-thesis`.
2. **Exactly one `cross_cutting_matrix` slot.** Add this as a
   subsection on the body section that introduces the surveyed systems
   (typically `Key Systems` or equivalent). The reference shape lives
   at `shared-references/reference-assets/outline.example.json`
   (slot `04e_feature_matrix`); the LaTeX layout the writer should
   target is at
   `shared-references/reference-assets/cross_cutting_matrix.example.tex`:

   ```json
   {
     "id": "feature_matrix",
     "name": "Feature Comparison Matrix",
     "section_type": "cross_cutting_matrix",
     "matrix_axes": {"rows": "systems", "cols": "dimensions"},
     "expected_rows": 8,
     "expected_cols": 6
   }
   ```

3. **Paired `open_problems` and `future_directions` lists.** Both
   sections carry roughly parallel `items[]` (5 ≤ n ≤ 8 each;
   counts may differ by ≤ 1). At least 80% of `open_problems` items
   carry a `paired_direction_id` that points at one
   `future_directions` item:

   ```json
   {"id": "06_problems", "section_type": "open_problems",
    "items": [
       {"id": "OP1", "title": "Cognitive loop trap",
        "paired_direction_id": "FD1"},
       ...
    ]
   }
   ```

4. **Section 2 (Background) carries a "Relationship to existing
   surveys" subsection.** It names ≥ 3 adjacent surveys and states the
   delta in 1–2 sentences each. This subsection is NOT a body
   argument-step — set `argues_for_thesis_step: null`.

These requirements are audited at the end of the pipeline by
`audit_writing.py` (the `structural_template` audit area). Failing
them does not currently block compile, but the audit prints a diff so
the next iteration can fix them.

## Step 3 — (Optional) Tier-axis generation

If `--tier-axis` is enabled (default on),
also produce a `tier_axis` block on the outline doc (NOT per-section)
that the matrix-layout figure tool (t10) consumes:

```json
"tier_axis": {
  "name": "Pretraining Generation",
  "tiers": [
    {"id": "T1", "label": "GPT-3 era (2020–2022)", "description": "..."},
    {"id": "T2", "label": "Llama era (2023)", "description": "..."},
    ...
  ],
  "feature_columns": ["Architecture", "Data scale", "Key technique", "Limitation"],
  "cells": {
    "T1": {"Architecture": ["Dense", "MHA"], "Data scale": ["~300B tokens"], ...},
    ...
  },
  "key_insight": "<one-sentence takeaway shown at figure bottom>"
}
```

5–7 tiers; 4–7 feature columns. If the field is too young or too narrow
to support a tier_axis, the agent returns `tier_axis: null` and the
matrix figure step gracefully falls back to a tree layout.

## Step 4 — Validate via validate_outline.py

```bash
AUTOSURVEY_TOOLS="${AUTOSURVEY_TOOLS:-$(git rev-parse --show-toplevel 2>/dev/null)/tools}"
python3 "$AUTOSURVEY_TOOLS/validate_outline.py" "$RUN_DIR" \
    --strict-thesis --strict-template
```

The validator checks:

1. **Closed-set papers** — all `primary_papers` and `secondary_papers`
   resolve to a `cite_key` in `filtered.jsonl`.
2. **Argument-step coverage** — every `argument_step.step_id` from
   `2_thesis/thesis.json` is referenced by ≥ 1 section's
   `argues_for_thesis_step`.
3. **Argument-skeleton completeness** — every body section has all 5
   skeleton fields non-empty (claim/steelman/concession/so_what =
   strings; evidence_claim_keys may be empty list at outline time).
4. **Section count bounds** — `min_sections ≤ count ≤ max_sections`
   (defaults 6 ≤ N ≤ 15).
5. **Tier-axis schema** — if `tier_axis` present, all `tiers[].id`
   are unique, `cells` keys are subset of tier ids, `feature_columns`
   are referenced in `cells`.
6. **Structural-template invariants 1, 4, 6** (under
   `--strict-template`):
   * 6–12 top-level body sections, ≥ 4 of them with ≥ 3 subsections;
   * exactly one `cross_cutting_matrix` slot;
   * paired open-problems / future-directions lists (5–8 items each,
     every open-problem item carries a `paired_direction_id`
     resolving to a future-directions item).

On validation failure, the validator prints the specific violation +
the offending section, exits non-zero, and the orchestrator halts.

If only invariant 6 fails (open-problems and future-directions exist
but pairing is missing), run
`python3 "$AUTOSURVEY_TOOLS/pair_open_future.py" "$RUN_DIR"` to
auto-populate `paired_direction_id` based on title-Jaccard, then
re-run `validate_outline.py --strict-template`. If the auto-pairer
itself reports < 80 % pairing, the lists are topically misaligned —
re-run the outline-generation prompt rather than papering over with
weak pairings.

## Step 5 — Write outline.json + outline.md

```python
(run_dir / "4_outline").mkdir(exist_ok=True)
(run_dir / "4_outline" / "outline.json").write_text(json.dumps(outline_doc, indent=2))
```

Also generate `outline.md` (human-readable) and `outline_review.md`
(structural notes — why we chose Candidate X, deviations from the
default 1:1 mapping, etc).

## Step 6 — Update state.json

```python
state["phases"]["drafting"]["substeps"]["outline_sketch"]["status"] = "completed"
state["phases"]["drafting"]["substeps"]["outline_sketch"]["sections"] = len(outline_doc["sections"])
state["phases"]["drafting"]["substeps"]["outline_sketch"]["organising_principle"] = chosen_letter
state["phases"]["drafting"]["substeps"]["outline_sketch"]["tier_axis_present"] = bool(outline_doc.get("tier_axis"))
# Mark the entire drafting phase complete since outline_sketch is the last substep
state["phases"]["drafting"]["status"] = "completed"
```

Print:

```
✅ Outline complete — N sections (organising principle: <A|B|C>)
   Argument-step coverage: ✓ all S1..Sm covered
   Tier-axis: <present | null>
   4_outline/outline.json written

Next: /survey-write (Phase 2: Arguing inner loop)
```

---

## Output Files

| File | Contents |
|---|---|
| `4_outline/outline_candidates.json` | The 3 organising-principle proposals from Step 1 |
| `4_outline/outline.json` | Final outline (chosen candidate, populated) |
| `4_outline/outline.md` | Human-readable outline summary |
| `4_outline/outline_review.md` | Structural notes (chosen principle + justification) |
| `state.json` | `phases.drafting.substeps.outline_sketch.status = completed` |

---

## See Also

- `shared-references/thesis-contract.md` — the thesis schema this stage consumes
- `shared-references/argument-skeleton.md` — the 5-step skeleton each section ships
- `shared-references/narrative-scaffolding.md` — how outline-level structure interacts with the document-level Hook / Why Now / Contributions / Open Problems
- `tools/validate_outline.py` — checks the thesis-driven fields above

---
