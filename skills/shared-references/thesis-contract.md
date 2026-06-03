# Thesis Contract — `thesis.json` Schema

Canonical structure of the thesis artifact produced by `/survey-thesis` and
consumed by `/survey-outline`, `/survey-write`, `audit_writing.py`. Every
run MUST have a thesis; without one, the pipeline halts.

## Why a thesis?

A survey is not a structured book report. The benchmark survey
*From Copilots to Colleagues* (Deli Chen et al.) opens by **claiming
something contestable**: *"frontier autonomous-research systems operate at
L4; L5 remains aspirational, and the missing piece is not raw model
capability but persistent knowledge accumulation."* Every section then
serves that one claim.

Without a thesis the writing stage degenerates into "X-class methods, then
Y-class methods, then Z-class methods" — boring and citation-dense without
being argument-dense. Thesis-first reverses this: organising principle
first, taxonomy second.

## Top-level schema

```json
{
  "thesis": "<1-2 sentences containing one contestable claim>",
  "thesis_id_chosen": "A" | "B" | "C",
  "argument_steps": [
    {
      "step_id": "S1",
      "claim": "<1 sentence>",
      "evidence_categories": ["empirical", "systems", ...]
    }
    // 3-6 steps total
  ],
  "anticipated_objections": [
    {"objection": "<1-2 sentences>", "rebuttal": "<1-2 sentences>"}
    // >= 2 entries
  ],
  "scope_disclaimers": ["<...>", ...],
  "non_obvious_findings": [
    {"finding": "<1-2 sentences — a counter-intuitive but defensible claim>",
     "section_id": "<id of the section that ships the % [INSIGHT] anchor>"}
    // 0..N entries; optional. When present, each must be backed by a
    // % [INSIGHT] LaTeX anchor in the named section (audit_writing.py
    // enforces; submission gate FAILS if anchored < total).
  ],
  "candidates": [
    {
      "id": "A" | "B" | "C",
      "thesis": "<1-2 sentences>",
      "argument_steps": [...],
      "anticipated_objections": [...],
      "sample_chapter": {
        "chapter_title": "<...>",
        "preview_path": "2_thesis/sample_chapters/A/preview.tex",
        "preview_pdf": "2_thesis/sample_chapters/A/preview.pdf",
        "word_count": 850
      }
    }
    // 2-3 candidates total
  ]
}
```

## Field semantics

| Field | Required | Notes |
|---|---|---|
| `thesis` | yes | The chosen candidate's `thesis` text, copied to top level for fast access |
| `thesis_id_chosen` | yes | Must equal `candidates[i].id` for some i |
| `argument_steps` | yes | 3–6 steps; each step will bind to one body section via `outline.json[].argues_for_thesis_step` |
| `argument_steps[].step_id` | yes | Globally unique within this thesis (e.g. "S1", "S2"); also used in outline & section anchors |
| `argument_steps[].claim` | yes | One sentence; the per-step claim (different from the per-section claim, which is more granular) |
| `argument_steps[].evidence_categories` | yes | Non-empty array; values from `{empirical, theoretical, methodological, systems, historical, critique}` |
| `anticipated_objections` | yes | ≥ 2 entries; the strongest dissents the thesis must answer |
| `scope_disclaimers` | no | Things deliberately NOT claimed (helps avoid scope-creep critique) |
| `non_obvious_findings` | no | Optional list of `{finding, section_id}` — counter-intuitive but defensible insights the survey must mark with `% [INSIGHT]` anchors. The mechanism the L1-L5 benchmark survey used to surface its 'bottleneck is knowledge accrual, not model capability' kind of insight. |
| `candidates` | yes | 2–3 candidates kept for audit + pivot reuse |
| `candidates[].sample_chapter` | yes | Each candidate gets a 1–2 page preview chapter |

## Validation rules (validate_artifacts.py)

1. **Contestable claim heuristic**. `thesis` must contain at least one of:
   - A comparative ("more X than Y", "outperforms", "consolidated around")
   - A negation/contradiction marker ("not X but Y", "fails to", "remains")
   - A judgment marker ("aspirational", "premature", "settled", "the dominant")
   Pure description ("This survey covers …") rejected.
2. **Argument steps coverage**. The chosen candidate's `argument_steps` count
   must be ≥ 3 and ≤ 6.
3. **Objections suffice**. `anticipated_objections.length >= 2`. Each
   objection must have non-empty `rebuttal`.
4. **Step IDs unique**. No duplicate `step_id` values.
5. **Sample chapter present.** Each candidate must have a
   `sample_chapter` with both `preview_path` and `preview_pdf` resolving to
   actual files.

Failure → exit 2 with a structured error message naming the offending
field.

## Writing-stage usage

Sections in `outline.json` reference the thesis via `argues_for_thesis_step`
(value ∈ `argument_steps[].step_id`). Together they form a contract:

- `thesis_coherence_audit` (inside `audit_writing.py`) verifies that:
  - Every `argument_step.step_id` is referenced by ≥ 1 outline section
  - Every section's body contains a back-reference to the thesis (regex on
    thesis keywords or explicit `\thesisRef` macro)
  - Abstract + Conclusion both restate the thesis once
- `narrative_scaffolding` recommends placing the thesis in:
  - Abstract (one sentence)
  - End of Introduction (Argument paragraph)
  - First sentence of Conclusion

## Pivot semantics

`/survey-pivot --new-thesis <id-or-text>` re-uses everything in `1_search/`
and overwrites `2_thesis/thesis.json` with the new chosen candidate, then
re-runs outline-sketch + Phase 2 + Phase 3 only. Multiple thesis pivots are
preserved as `pivots/<thesis-slug>/main.pdf` for side-by-side comparison.

## Anti-patterns

- **Encyclopedic thesis**: *"This survey covers X across architectures,
  data, and training stages."* — Not contestable.
- **Truism**: *"Larger models perform better."* — No one disputes this.
- **Multi-claim thesis**: *"X holds because of A and B, while also Z."* —
  Pick one. The remaining material becomes the Open Problems chapter.
- **Future-tense thesis**: *"In the next decade, …"* — Theses must claim
  something about the *present* corpus; speculation lives in Trends.
