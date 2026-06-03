---
name: survey-pivot
description: Use when re-running an AutoSurvey paper with a different thesis, or when /survey-pivot is invoked. Reuses search corpus + claims_cache.jsonl from an existing run; rebuilds outline-sketch + Phase 2 + Phase 3 only. Multiple thesis pivots are preserved side-by-side as pivots/<thesis-slug>/main.pdf for comparison.
---

# /survey-pivot

The single auxiliary command in AutoSurvey. Section / status /
estimate / etc. are all derivable from `state.json` and `outline.json`
without dedicated commands, so they are intentionally not exposed as
slash-commands.

`/survey-pivot` answers a real research workflow need: *"I have an
existing survey draft; what would the same corpus look like under a
different thesis?"* Useful for:

- Comparing multiple thesis candidates beyond the 2-3 sample chapters
  the initial /survey-thesis stage offers
- Responding to reviewer demand "have you considered argument X?"
  by rebuilding the survey under thesis X without losing thesis A
- Producing a side-by-side comparison for a workshop / blog post

The pivot reuses the most expensive artifacts (search + claims) and
rebuilds only the cheap-to-rerun stages.

---

## Invocation

```
/survey-pivot --resume <run-id>
              [--new-thesis <text> | --new-thesis-id <A|B|C>]
              [--auto-confirm]
```

- `--new-thesis <text>`: free-form text; agent uses this as the seed
  for a fresh /survey-thesis call (which re-proposes 2-3 candidates
  biased toward the seed)
- `--new-thesis-id A|B|C`: pick a thesis directly from the existing
  `2_thesis/thesis.json:candidates[]` (skip the re-proposal step)
- exactly one of the two MUST be supplied

---

## Prerequisites

- `<run_dir>/state.json` exists and carries the canonical `phases` dict
  (drafting / arguing / polishing)
- `<run_dir>/1_search/filtered.jsonl` exists
- `<run_dir>/2_thesis/thesis.json` exists (the existing thesis we're
  pivoting away from)
- `<run_dir>/1_search/claims_cache.jsonl` may or may not exist;
  pivot will reuse it if present and lazy-mine missing keys when
  /survey-write needs them

---

## Steps

### Step 1 — Set up the pivot directory

```python
existing_thesis = json.loads((run_dir / "2_thesis" / "thesis.json").read_text())
pivot_slug = re.sub(r"[^a-z0-9]+", "-",
                    (args.new_thesis or args.new_thesis_id).lower()
                   ).strip("-")[:32]
pivot_dir = run_dir / "pivots" / pivot_slug
pivot_dir.mkdir(parents=True, exist_ok=True)
```

The pivot is a **subdirectory** of the original run, not a new run.
This keeps `1_search/` shared (read-only from the pivot's perspective)
and produces a side-by-side comparison structure:

```
.autosurvey/runs/<run_id>/        # under your current working directory
├── main.pdf # original thesis
├── 2_thesis/thesis.json
├── 4_outline/
├── 5_paper/
├── 1_search/ # SHARED (filtered + claims_cache)
└── pivots/
    ├── <pivot_slug_A>/
    │ ├── main.pdf
    │ ├── 2_thesis/thesis.json
    │ ├── 4_outline/
    │ └── 5_paper/
    └── <pivot_slug_B>/
        └── ...
```

### Step 2 — Resolve the new thesis

```python
if args.new_thesis_id:
    chosen = next(c for c in existing_thesis["candidates"]
                  if c["id"] == args.new_thesis_id)
    # Promote that candidate to the chosen position
    new_thesis_doc = {
        "thesis": chosen["thesis"],
        "thesis_id_chosen": chosen["id"],
        "argument_steps": chosen["argument_steps"],
        "anticipated_objections": chosen.get("anticipated_objections", []),
        "scope_disclaimers": chosen.get("scope_disclaimers", []),
        "candidates": existing_thesis["candidates"],
    }
else:
    # --new-thesis text → invoke /survey-thesis with the seed
    # The skill produces fresh candidates biased toward the seed
    invoke_skill("/survey-thesis",
                 run_id=run_id, run_subdir=str(pivot_dir),
                 thesis_seed=args.new_thesis,
                 auto_confirm=args.auto_confirm)
    new_thesis_doc = json.loads((pivot_dir / "2_thesis" / "thesis.json").read_text())
```

Validate the new thesis with `tools/validate_artifacts.py` (in the
pivot dir context) before proceeding.

### Step 3 — Rebuild outline-sketch + Phase 2 + Phase 3

Run the substeps that depend on thesis:

```bash
# In pivot_dir context, with 1_search shared via symlink or relative path
ln -sfn "$run_dir/1_search" "$pivot_dir/1_search"
# 2_thesis lives directly in pivot_dir from Step 2

# outline-sketch
invoke_skill("/survey-outline", run_id=run_id, run_subdir=pivot_dir)

# Phase 2 — write each section (lazy claim mining reuses shared claims_cache)
for sec in outline.sections:
    invoke_skill("/survey-write", run_id=run_id, run_subdir=pivot_dir,
                 section=sec.id, auto_confirm=args.auto_confirm)

# Phase 3 — review + audits + compile + evidence dashboard
invoke_skill("/survey-review", run_id=run_id, run_subdir=pivot_dir,
             auto_confirm=args.auto_confirm)
invoke_skill("/survey-verify", run_id=run_id, run_subdir=pivot_dir,
             assurance=args.assurance)
run_compile(pivot_dir)
run_evidence_dashboard(pivot_dir)
```

The `run_subdir` parameter (used internally by the orchestrator) tells
each skill to use `pivot_dir` instead of the canonical run_dir for
reads/writes EXCEPT for `1_search/` which remains shared.

### Step 4 — Update pivot_log.json

After compile, append an entry to `<run_dir>/pivots/pivot_log.json`:

```json
{
  "pivots": [
    {
      "id": "<pivot_slug>",
      "thesis": "<full thesis text>",
      "thesis_id_chosen": "<A|B|C|new>",
      "created_at": "2026-05-29T...Z",
      "main_pdf": "pivots/<slug>/main.pdf",
      "evidence_html": "pivots/<slug>/survey.evidence.html",
      "outline_principle": "<argument-step bound | time-period bound | objection-grouped>",
      "compile_status": "success | failed",
      "audit_summary": {
        "argument_anchors_score": 0.92,
        "narrative_score": 1.0,
        "thesis_coherence_score": 0.95
      }
    }
  ]
}
```

### Step 5 — Print comparison summary

```
✅ Pivot complete: pivots/<slug>/main.pdf
   New thesis: "<first 100 chars>"
   Compared to original: pivots/pivot_log.json

Side-by-side comparison:
   original main.pdf → ~30 pages, anchor-score 0.95
   pivots/<slug>/main.pdf → ~28 pages, anchor-score 0.92
   pivots/<other_slug>/main.pdf → ~31 pages, anchor-score 0.93

Open all three PDFs to compare voice, argument, and emphasis.
```

---

## Cost characteristics

- Search: skipped (reuses parent run's filtered.jsonl)
- Thesis (when `--new-thesis-id` is used): skipped; just promotes a candidate
- Thesis (when `--new-thesis` text is used): one /survey-thesis call,
  including 2-3 sample chapters (~3 LLM calls × ~900 tokens)
- Outline-sketch: one call
- Phase 2 (per-section): expensive in worst case, but lazy claim
  mining reuses `claims_cache.jsonl` from the parent run, so only
  newly-needed papers (typically 0-5) are mined fresh
- Phase 3: full review + audits + compile

Typical wall time: **15-25% of a fresh run**.

---

## Error Conditions

| Error | Response |
|---|---|
| `<run_dir>/state.json` missing or its `phases` dict isn't `{drafting, arguing, polishing}` | FAIL — refuse to pivot a run that doesn't match the schema |
| Both `--new-thesis` and `--new-thesis-id` given | FAIL — pick exactly one |
| `--new-thesis-id X` but no candidate `X` in existing thesis.json | FAIL — list available ids |
| New thesis fails validate_artifacts | FAIL — print specific schema violations |

---

## See Also

- `skills/survey-thesis/SKILL.md` — invoked when `--new-thesis` text is given
- `skills/survey-write/SKILL.md` — Phase 2 driver, lazy claim mining
  reuses `1_search/claims_cache.jsonl`
- `tools/build_evidence_dashboard.py` — produces per-pivot evidence dashboards
