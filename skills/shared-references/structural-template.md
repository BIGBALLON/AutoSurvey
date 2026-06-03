# Structural Template — AutoSurvey

This file codifies the **structural invariants** every AutoSurvey
output must satisfy. They are derived from a strong human-edited
NeurIPS-style benchmark survey and are the difference between a paper
that reads like that benchmark and one that reads like a model dump.
The exact threshold numbers live in
`skills/shared-references/benchmark-targets.json`.

The 8 invariants below are referenced by `outline_sketch` (which
produces the section structure) and by `audit_writing.py
--check-structural-template` (which gates the final assembled draft).

> **These are quality *floors*, not a fixed skeleton.** They constrain
> *quality* (synthesis over summary, navigable nesting, honest density,
> a re-framing conclusion) — they do **not** prescribe a single
> section menu, ordering, or naming every survey must copy. The agent
> chooses how many body sections to write (within the window), what to
> call them, how to order them, and which optional sections (e.g.
> Trends & Trajectories, governed by `brief.configuration.trends_section`)
> to include — driven by the **thesis's argument arc and the brief's
> scope**, not by a template. Two surveys on different topics should not
> come out looking structurally identical; if they do, the structure was
> copied rather than derived.

## Invariant 1 — Two-level nesting

The outline must produce **6–12 top-level sections**, of which
**≥ 4 carry 3 or more subsections**. Flat outlines (no `x.y`
subsection nesting) are rejected. The window's upper bound is wide on
purpose: a broad or historical brief (many dimensions, eras, or
paradigm transitions) should give each its own section rather than
merging everything back into the same ~8-section shape — that merging
is what made every survey look templated and capped its length.

A reader should be able to scan the TOC in 5 seconds and see a clear
"introduction → core taxonomy → patterns → systems → problems"
rhythm. That rhythm comes from subsection nesting, not from raw
section count.

## Invariant 2 — Inline citation density ≤ 12 / 1 K body words

The benchmark inlines 8.4 citations per 1 K body words. Naive
generation drifts to 28+ ("stuff every related work into the
parenthetical"). The audit caps density at **12 / 1 K body words**
(1.5 × the benchmark, leaves headroom for genuinely citation-dense
sections like Related Work) and flags individual sentences with **>
3 citations** unless every cited work is named in prose.

## Invariant 3 — Annotated bibliography

Every entry in `5_paper/references.bib` must carry an `annote =
{...}` field with **a 1–2-sentence factual description** of what the
work contributes. The annotation comes verbatim from the card's
`_decision_summary` field; we do not LLM-rewrite it (re-generation
introduces drift).

The audit requires **≥ 80 % of bib entries** to be annotated. The
remaining 20 % cushion handles cards that legitimately lack a
`_decision_summary` (e.g. a venue-template citation with no AutoSurvey
card).

The benchmark example (Anthropic Claude 3 entry):

```bibtex
@misc{anthropic2024claude3,
  author = {Anthropic},
  title  = {The Claude 3 model family: Opus, Sonnet, and Haiku},
  year   = {2024},
  annote = {Family of models with strong instruction-following and
    agentic capabilities used in Claude Code and other agent
    systems.}
}
```

## Invariant 4 — Exactly one cross-cutting comparison matrix

The outline must declare **exactly one** `cross_cutting_matrix` slot.
The writing stage must emit a single full-page table satisfying it
(typically `N systems × M dimensions`, where `N ≥ 8` and `3 ≤ M ≤ 8`).
This is the table the whole paper points back to.

**Per-section auxiliary tables are still allowed but capped at 3.**
Nine small per-section tables (the easy default) dilute the global
matrix and signal a paper that is afraid to commit to a unified
framework.

## Invariant 5 — Relationship-to-existing-surveys subsection

Section 1 (Introduction) or Section 2 (Background) must contain a
subsection titled **"Relationship to existing surveys"** (or a clearly
equivalent name). It must name **≥ 3 adjacent surveys** by author and
state the delta in 1–2 sentences each.

The benchmark survey places this subsection at the end of §1 as an
inline `\paragraph{Relationship to Existing Surveys.}`-style block;
that is also acceptable. The audit looks for the title regex anywhere
in the assembled .tex, not at a fixed structural position.

## Invariant 6 — Open-problems × future-directions pairing

The outline emits Open Problems and Future Directions as
**roughly parallel lists** (each in the 5–8-item window;
counts may differ by at most 1, since an "orthogonal" open problem
may legitimately have no matching future direction). At least
**80 % of open-problem items** carry a `paired_direction_id` that
points at one future-direction item.

The pairing makes the final pages feel actionable rather than
diffuse. A reader who skims the last 4 pages should see "here are 6
problems, here are 6 paired directions, here are 3 candidate research
agendas" — not "here are some thoughts on the future."

## Invariant 7 — Conclusion is a re-frame, not a summary

The conclusion is **400–700 words**, structured as:

1. **One opener paragraph** that names the survey's central thesis in
   the strongest available terms (do not paraphrase the abstract).
2. **Three to five bold-lead findings paragraphs**, each beginning
   with a 2–4-word lead in `\textbf{}` (e.g. `\textbf{Taxonomy and
   Definitions.}`) that names a cross-cutting pattern.
3. **One closing paragraph** (typically `\textbf{A Call to Action.}`)
   that names what next steps the field should take and the
   conditions under which the thesis would have to be revised.

A bullet-list summary, an `\itemize`/`\enumerate` block, or a
section-by-section recap fails this invariant. The verbatim reference
example lives at `shared-references/conclusion-template.md`.

## Invariant 8 — Numbered contributions carry section cross-refs

Every item in the Introduction's contributions enumeration ends with
a parenthesised section pointer: `(§2)`, `(\S\,3)`, `(Section 4)`,
`(Sec.\ 6)`. The pointer turns the contributions list from marketing
into a navigation aid: a skim-reader can jump from a one-sentence
claim to the section that defends it.

The audit accepts both the `\begin{enumerate}` style (the benchmark
uses 4 `\item`s, each ending with `(§N)`) and the inline
`\textbf{(N)}` markers (with the same `(§N)` discipline). At least
**75 % of items** must carry a section reference — the remaining
cushion handles a "we organise the survey as follows" item that
legitimately does not point at one section.

---

## Auditable summary

| # | Invariant | Audit signal |
|---|---|---|
| 1 | 6–12 top-level sections, ≥ 4 with ≥ 3 subsections | `\section`/`\subsection` count |
| 2 | ≤ 12 inline citations / 1 K body words; ≤ 3 in any one sentence unless named | `\cite*` per-sentence + per-section density |
| 3 | ≥ 80 % bib entries carry `annote` | grep `annote =` in `references.bib` |
| 4 | `cross_cutting_matrix` slot declared in outline; every labelled auxiliary table is referenced from prose via `\ref{}`/`\autoref{}` (load-bearing, not decoration). No count cap — number of tables is an editorial decision. | outline.json + `\label{tab:…}` ↔ `\ref{tab:…}` cross-check |
| 5 | "Relationship to existing surveys" subsection / paragraph with ≥ 3 named surveys | `\subsection`/`\paragraph` title regex + author count |
| 6 | Open-problems and future-directions parallel (5 ≤ n ≤ 8; counts within ±1; ≥ 80 % paired) | outline.json |
| 7 | Conclusion 400–700 words, no bulleted summary | word count + bullet detector |
| 8 | ≥ 75 % of numbered contributions end with `(§N)` cross-ref | regex over enumerate / `\textbf{(N)}` items |

The mechanical implementation of these signals lives in
`tools/audit_writing.py --check-structural-template`. The audit runs
on every `/survey-run` and FAILs the compile gate if any invariant is
violated.
