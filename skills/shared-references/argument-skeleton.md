# Argument Skeleton — 5-Step Section Writing Discipline

The structure every body section in an AutoSurvey paper MUST follow.
Enforced by `audit_writing.py`'s anchor scanner (5 LaTeX comment markers
per section, in order). The Phase 2 inner loop in `/survey-write` writes
each section as a `.skeleton.md` first (5 H3 sections) then composes the
`.tex` retaining the anchors as comments.

## The 5 steps

| Step | LaTeX anchor | What it is | Length |
|---|---|---|---|
| **Claim** | `% [CLAIM]` | The single sentence the section asks the reader to believe. Must connect to `outline.argues_for_thesis_step`. | 1 sentence |
| **Steelman** | `% [STEELMAN]` | The strongest reason the Claim might be wrong. The agent must *genuinely* try to refute itself. | 1 paragraph |
| **Evidence** | `% [EVIDENCE]` | The actual chain of `\cite`-grounded claims that supports the Claim despite the Steelman. Pulls from `claims_cache.jsonl`. | 2–5 paragraphs |
| **Concession** | `% [CONCESSION]` | The boundary of the argument: where it does NOT apply, what assumption it depends on. | 1 paragraph |
| **So-what** | `% [SO-WHAT]` | The implication that connects to the next section / advances the thesis. | 1 sentence |

The 5 steps form the *structure* of the section, not the literal headings.
Subsections may be inserted within Evidence to organise long evidence
chains, but Claim/Steelman/Concession/So-what each remain a single visible
prose unit.

## Why this skeleton?

The benchmark survey *From Copilots to Colleagues* (Deli Chen et al.)
exhibits this pattern in §3.1 (ReAct):

> *"The key insight of ReAct is that reasoning without acting leads to
> hallucination, while acting without reasoning leads to inefficient
> exploration. The synergy between these modes—where thoughts guide action
> selection and observations ground subsequent reasoning—is now considered
> a foundational principle of agent design."*

The two clauses ("reasoning without acting → X / acting without reasoning
→ Y") form the Claim + implicit Steelman + Resolution. Our 5-step skeleton
makes this discipline explicit and auditable.

## Skeleton format (`.skeleton.md` H3 headings)

```markdown
### Claim
The standard 2024 transformer block — pre-norm, RMSNorm, SwiGLU, RoPE — has stabilised
across all open frontier releases.

### Steelman
This claim risks survivorship bias: every release that diverged (e.g. xLSTM,
state-space models) is excluded from the corpus, so what looks like
convergence might be selection.

### Evidence
- claim_id touvron2023llama#1 — Llama-2 establishes pre-norm + RMSNorm + SwiGLU + RoPE as the dense-block default.
- claim_id deepseek2024v3#3 — DeepSeek-V3 retains the same block primitives despite switching the attention head to MLA.
- claim_id team2024qwen2#2 — Qwen2.5 documents identical primitive choices.
... (3–5 claims, each one sentence + the claim_id)

### Concession
The convergence is on the *block primitive*, not the *attention head* or
*sparsity structure*; both still vary widely (§2.2, §2.3).

### So-what
With the block primitive settled, the next axis of variation is the
attention mechanism — which we examine in §2.2.
```

## .tex composition rules

When `/survey-write` composes the `.tex` from the skeleton:

1. Each H3 section → 1 LaTeX paragraph (or paragraphs) preceded by the
   anchor comment. Comments are LaTeX `%` lines so they never render but
   ARE detectable by `audit_writing.py`.
2. The 5 anchors must appear **in order**: CLAIM → STEELMAN → EVIDENCE →
   CONCESSION → SO-WHAT. Wrong order = audit FAIL.
3. Subsections (`\subsection{}`) MAY be inserted inside the EVIDENCE block.
   Anchors live at the section-level only, not per-subsection.
4. Citations in EVIDENCE must reference the `claim_id`s listed in the
   skeleton. The `\cite{cite_key}` (where the cite_key is the prefix of
   the claim_id, before `#`) goes in the prose.

Example .tex output:

```latex
\section{Architectures: Dense, MoE, and Attention Innovations}

% [CLAIM]
The standard 2024 transformer block---pre-norm, RMSNorm, SwiGLU, RoPE---
has stabilised across all open frontier releases \citep{touvron2023llama,
zhang2019root, shazeer2020glu, su2021rope}.

% [STEELMAN]
A reasonable counter is that we observe convergence only because divergent
designs (xLSTM, state-space models) are filtered out of our corpus by the
language-modelling-only scope. ...

% [EVIDENCE]
Three lines of evidence support convergence-at-the-primitive-level. First,
\citet{touvron2023llama} establishes pre-norm + RMSNorm + SwiGLU + RoPE as
the dense baseline... Second, \citet{deepseek2024v3} retains the same
primitives despite switching to Multi-head Latent Attention... Third,
\citet{team2024qwen2}'s technical report documents identical choices.

% [CONCESSION]
This convergence is on the *block primitive*, not the *attention head* or
*sparsity structure*; both still vary widely (\S\ref{sec:attn-variants},
\S\ref{sec:moe}).

% [SO-WHAT]
With the block primitive settled, the next axis of variation is the
attention mechanism---which we examine next.
```

## Anti-patterns

- **Missing Steelman** ("This is obviously true because..."). Audit FAIL.
- **Hand-wave Steelman** ("Some might argue otherwise"). Audit will pass
  the anchor check but `prose_polish` flags vague hedge phrases.
- **Concession as restatement** ("In summary, X holds.") instead of
  identifying real boundaries. Reviewer-skeptic persona will challenge.
- **So-what disconnected** from next section. Reviewer-senior will reject.
- **Evidence without claim_ids**. Cite_keys without quote-grounding can be
  hand-waved; force citation back to `claims_cache.jsonl` entries.

## Audit (audit_writing.py)

Per section, scan `5_paper/sections/*.tex`:

- Find all `% [TAG]` lines where `TAG ∈ {CLAIM, STEELMAN, EVIDENCE, CONCESSION, SO-WHAT}`
- Check: all 5 present, exactly once each, in canonical order
- Check: each anchor is followed by ≥ 1 non-empty prose line before the next anchor (no empty buckets)
- Output: `argument_anchors_score = passing_sections / total_sections`
- Submission gate: `score < 0.9` → exit 1

## See also

- `narrative-scaffolding.md` — the document-level narrative (Hook, Why
  Now, Relationship to Existing Surveys, Contributions)
- `reviewer-personas.md` — the 2 reviewer personas that audit each
  section's argument quality
- `thesis-contract.md` — how each section's Claim ties to one
  `argument_step.step_id` from the thesis
