# Claims Contract — `claims_cache.jsonl` Schema

Lazy-mined "atomic claims" — verbatim quotes extracted from primary papers
and used by `/survey-write` as the principal evidence input (replacing the
older `cards.jsonl` field-extraction model as the *primary* writing source;
cards are still emitted for `build_dimension_tables.py` but downgraded to
a side-product).

## Why claims, not cards?

`cards.jsonl` flattens each paper into ~30 schema fields ("hidden_size",
"top_k", "training_tokens"). Useful for tables, useless for argument: the
agent writing a section doesn't see *what the paper actually argues*, only
its parameters.

A **claim** is the unit of argumentative content the writing stage really
needs:

- `claim`: a 1-sentence assertion the paper makes
- `quote`: the verbatim passage that supports it
- `anchor`: where in the paper (page or section) the quote lives
- `claim_type`: empirical / theoretical / methodological / critique

Lazy mining means claims are *only* extracted for papers that the writing
stage actually cites, dramatically reducing wasted LLM cycles. Mined
claims are written incrementally into `claims_cache.jsonl` and re-used
across pivots.

## Per-paper schema (one JSON object per line)

```json
{
  "cite_key": "deepseek2024v3",
  "what_paper_argues": "<1 paragraph freeform: the paper's core argument in their own framing>",
  "atomic_claims": [
    {
      "claim_id": "deepseek2024v3#1",
      "claim": "<1 sentence assertion>",
      "quote": "<verbatim passage from the paper>",
      "anchor": "p.7 / Section 4.2",
      "claim_type": "empirical"
    }
    // 2-5 claims per paper
  ],
  "_first_used_in_section": "02_architecture",
  "_mined_at": "2026-05-29T15:23:00Z"
}
```

## Field semantics

| Field | Required | Notes |
|---|---|---|
| `cite_key` | yes | Must exist in `1_search/filtered.jsonl` (closed-set hard gate) |
| `what_paper_argues` | yes | 1 paragraph. Distinct from the abstract — captures the author's own framing of their contribution (often inferable from intro + conclusion, not the abstract bullet list) |
| `atomic_claims` | yes | 2–5 entries; ≥ 2 required even for "support" papers |
| `atomic_claims[].claim_id` | yes | Format: `<cite_key>#<n>` (1-indexed within paper) |
| `atomic_claims[].claim` | yes | One sentence; should be a *concrete* assertion that can be cited as `(deepseek-establishes that <claim>)` |
| `atomic_claims[].quote` | yes | Verbatim from paper text. Whitespace normalisation allowed but no rewording. ≥ 6 words. |
| `atomic_claims[].anchor` | yes | One of: `"p.<N>"`, `"Section <X.Y>"`, `"Table <N>"`, `"Figure <N>"`, or combined `"p.7 / Section 4.2"` |
| `atomic_claims[].claim_type` | yes | One of: `empirical` (numerical/measurement), `theoretical` (formal result), `methodological` (technique introduced), `critique` (negative/contrarian claim) |
| `_first_used_in_section` | yes | The section that triggered the lazy mining (for resume tracking) |
| `_mined_at` | yes | ISO8601 UTC; used for cache-staleness detection |

## Validation rules (validate_artifacts.py)

1. **Closed-set cite_key**. Every line's `cite_key` must be in
   `filtered.jsonl`. Any deviation → exit 2.
2. **Verbatim quote check**. For each `atomic_claim`:
   - Normalise both the quote and the source paper text (collapse internal
     whitespace, strip punctuation differences in unicode dashes/quotes)
   - If the normalised quote is NOT a substring of the normalised paper
     text, mark `unverified: true` and emit a WARN
   - In `--strict` mode, `unverified: true` causes exit 1
3. **Quote length**. ≥ 6 whitespace-separated tokens (anything shorter is
   likely paraphrase, not quotation).
4. **claim_type enum**. Must be one of the 4 values listed above.
5. **Atomic count**. 2 ≤ `len(atomic_claims)` ≤ 5.
6. **No duplicate claim_ids** across the entire jsonl.

## Lazy mining cache strategy

Triggered by `/survey-write` when entering a new section:

```python
# pseudocode — actually implemented in survey-write/SKILL.md inner loop
mining_targets = section.primary_papers - claims_cache.cite_keys
for paper in mining_targets:
    mined = agent.mine_claims_from_paper(paper) # 1 LLM call per paper
    append_to_jsonl(claims_cache, mined)
```

Cache is append-only; never rewritten. A pivot (`/survey-pivot --new-thesis`)
re-uses the cache verbatim — claims are paper-property, not thesis-property.

## Closed-set citation invariant

Every `\cite{key}` in a section .tex MUST resolve to either:
1. A paper in `filtered.jsonl` (existing closed-set rule), AND
2. A paper that has been mined into `claims_cache.jsonl` *if* the cite
   sentence references a specific number, claim, or quote (not just a
   generic mention of the paper).

The second rule is enforced by `audit_writing.py`'s **claim-grounding
audit**: any sentence that cites a paper AND contains a numeric token
(year, count, percentage, "$N$"-pattern) must be supported by either:
- The paper's abstract in `filtered.jsonl`, OR
- An atomic_claim's `quote` for that cite_key

Checking both the abstract and the atomic_claim quotes avoids
false-positive failures when the supporting fact lives in the paper
body rather than the abstract.

## Anti-patterns

- **Paraphrased quote**: `quote: "they used FP8 across the model"` when
  the paper says "we apply E4M3 FP8 to all matrix multiplications". Reject;
  must be verbatim.
- **Trivial claim**: `claim: "DeepSeek-V3 is a language model"` — adds no
  argumentative value. Aim for claims a sceptical reader would find
  contestable or surprising.
- **Cross-paper claim**: a single `atomic_claim` summarising 3 papers is
  not allowed; one paper, one claim record, one quote.

## Card-side schema (`cards.jsonl`) — minimum surface

The writing stage's primary input is `claims_cache.jsonl` (above). A
**parallel** `cards.jsonl` is still emitted — but only as a side-product
of the inner loop, used by `tools/build_dimension_tables.py --mode
decision`. The card schema is deliberately tiny:

```jsonc
{
  "cite_key": "deepseekai2024deepseek-v3",
  "title": "...",
  "year": 2024,
  // Group 1 — populated by /survey-write per-section inner loop
  "_decision_summary": {
    "one_line_role": "Open-weight 671B MoE foundation model",
    "key_capability": "FP8 throughout, MLA attention, 14.8T tokens",
    "primary_limitation": "Closed pretraining data mix",
    "availability": "Apache-2.0 weights, dataset closed",
    "tier": "frontier"
  },
  // Group 2 — side-product summary of mined claims
  "_atomic_claims_summary": {
    "what_paper_argues": "MLA + auxiliary-loss-free MoE scale to FP8 frontier",
    "claim_count": 7,
    "claim_types": ["empirical", "methodological"]
  }
}
```

Validation rules live in `tools/validate_artifacts.py` (see the
`decision_summary` audit). Topic-specific extra groups are tolerated;
only `_decision_summary` is consulted by the dimension-table builder.

One minimal card schema is sufficient because the writing stage reads
claims, not fields — topic-fitted fields don't help the writing.
Topic-specific field needs are surfaced inline by `/survey-write` when
the agent decides a section needs a particular comparison.
