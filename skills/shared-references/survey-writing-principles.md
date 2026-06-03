# Survey Writing Principles

Writing principles specific to **survey papers**. A survey is not a research paper:
instead of presenting one new result, it organizes a field into a coherent narrative.
This document captures the writing discipline that genre demands.

## When to Read

- Before drafting the Abstract, Introduction, or Conclusion of a survey.
- Before writing each body section that synthesizes a thematic cluster of papers.
- When a section reads as a paper-by-paper summary instead of a synthesis.
- When the prose feels generic, templated, or "AI-shaped".
- During the quality-audit pass after all sections are drafted.

## The Survey Narrative Principle

A survey is **a structured tour through a field**, organized around a taxonomy that the
author defends. By the end of the Introduction, the reader should know:

- **The What** — what subarea is being surveyed (and what is *not* covered)
- **The Why** — why the area matters now, and why an existing survey is not enough
- **The How** — the taxonomy / lens through which the survey organizes the work
- **The So What** — the main trends, debates, and open problems the reader will see

If the author cannot state the survey's organizing lens in one sentence, the framing has
not converged. Examples of a good lens:

- "We organize LLM architecture papers by the type of architectural innovation: attention
  mechanism, efficiency primitive, scale strategy, and adaptation method."
- "We taxonomize federated learning surveys by the threat model addressed."
- "We classify retrieval-augmented generation methods along three axes: what is retrieved,
  when retrieval happens, and how the retrieved context is integrated."

The lens determines the section structure, the comparison tables, and the open-problems
discussion. **Do not change lenses mid-paper.**

## Reviewer Reading Order for Surveys

Most survey readers (including reviewers) skim in this order:

1. Title
2. Abstract
3. Taxonomy figure (Figure 1)
4. Section headings and subheadings (the table of contents implicitly)
5. Comparison tables, if present
6. The text

This means the **structural artifacts** (taxonomy figure, section titles, comparison
tables) carry more weight in a survey than in a research paper. Spend disproportionate
effort on them.

## How to Write the Survey Abstract

A strong survey abstract follows a five-part flow:

1. **The field and its growth** — what changed in the area being surveyed and why now.
2. **The gap this survey addresses** — what existing surveys miss, what is fragmented,
   what taxonomy is missing.
3. **The organizing lens** — the taxonomy or framework this survey uses.
4. **What the survey covers** — corpus size, time range, thematic scope.
5. **The headline finding** — the main trend, consensus, or debate the reader will take
   away.

### A Good Survey Abstract Sketch

```text
Large language models have evolved from a research curiosity to a deployed
infrastructure spanning education, software engineering, and biomedicine.
Existing surveys cover applications and benchmarks, but no synthesis exists
of the architectural design space — attention mechanisms, efficiency
primitives, scale and pretraining strategies, parameter-efficient adaptation,
alignment, retrieval, and multimodal extensions.
This survey organizes 30 representative works (2018–2026) into eight thematic
clusters and traces the design decisions, trade-offs, and emerging consensus
within each.
We identify three cross-cutting themes: efficiency as a first-class design
concern, the convergence of scaling laws and data curation, and the rising
importance of post-training adaptation.
We close with open architectural challenges including length generalisation,
catastrophic forgetting, MoE load balancing, and multimodal grounding.
```

### Survey Abstract Anti-Patterns

- **Generic field opening**: "In recent years, X has become increasingly important..." —
  delete. Start from the specific scope.
- **Listing every section** instead of the survey's thesis.
- **Promising "comprehensive coverage"** without saying what was selected and why.
- **No mention of the organizing lens** — the reader cannot tell what makes this survey
  different from a Wikipedia article.

## Introduction Structure for Surveys

Surveys have a slightly different intro pattern than research papers because they pitch
a *taxonomy* rather than a *contribution*.

1. **Field overview and motivation** (1 paragraph)
   - What is the area and why does it matter now?
   - One concrete data point: corpus growth, deployment scale, citation trend.
2. **The gap** (1 paragraph)
   - What existing surveys cover and where they fall short.
   - Why a new synthesis is needed (e.g., new architectures, new applications,
     reorganization required).
3. **Scope and selection criteria** (1 paragraph)
   - Time range, venue coverage, inclusion criteria.
   - Equally important: **what is excluded** and why.
4. **The taxonomy** (1 paragraph + reference to taxonomy figure)
   - The organizing lens.
   - The N thematic clusters.
   - Why this taxonomy and not another.
5. **Survey contributions** (bullet list, 3–5 items)
   - "We provide the first synthesis of X across Y."
   - "We propose a taxonomy organizing N papers along K axes."
   - "We identify M open problems and emerging research directions."
6. **Roadmap** (1 short paragraph)
   - Section-by-section layout, signposting the reader through the rest.

### Survey Contribution Bullets: Good vs Bad

Good:
- We taxonomize 30 LLM architecture papers along an 8-node method axis (Section 2).
- We document three cross-cutting trends — efficiency, scale, adaptation — with
  evidence from each cluster.
- We identify five concrete open problems with corresponding research directions.

Bad:
- We survey the recent literature on LLM architecture.
- We provide a comprehensive overview of the field.
- We discuss several important topics.

The bad bullets are unfalsifiable. A reviewer cannot disagree with "we discuss several
important topics" — and that is exactly the problem.

## Body Section Patterns: Synthesis, Not Summary

The most common failure mode in machine-generated surveys is a paper-by-paper book
report:

> "Smith et al. (2021) propose method A, which uses X. They report Y on dataset Z.
> Jones et al. (2022) propose method B, which uses ..."

**A survey body section is a synthesis, not a sequence of book reports.** Use these
patterns instead:

### Pattern 1: Define a design dimension, then place papers along it

> "Efficient transformers fall into three families along the *attention pattern* axis:
> fixed-window methods retain strong locality bias \cite{...}; learnable-pattern methods
> permit global tokens to break out of the local view \cite{...}; and kernel-based linear
> attention sacrifices peakedness for $O(n)$ complexity \cite{...}. Empirically, no
> single family dominates: the best choice depends on the sequence length regime
> and the modality \cite{tay2022efficient}."

### Pattern 2: Comparative claim with evidence from multiple papers

> "Quantization-aware fine-tuning has consistently recovered near-full-precision quality
> across both 4-bit \cite{...} and 8-bit \cite{...} regimes, suggesting that the
> low-rank update structure is robust to aggressive weight quantization."

### Pattern 3: Trend statement with a representative example

> "Modern open foundation models converge on a near-identical recipe: RoPE positional
> embeddings, RMSNorm pre-norm, SwiGLU activations, and grouped-query attention. LLaMA
> \cite{touvron2023llama}, Falcon \cite{almazrouei2023falcon}, and DeepSeek-V3
> \cite{zhao2025insights} differ in scale and data, not architecture."

### Anti-Pattern: One paragraph per paper

If three consecutive paragraphs each open with "Smith et al. propose...", the section is
a book report, not a synthesis. Re-organize by design dimension or by trend.

### Each Body Section Should Have

- An **opening definition** of the section's scope (2–3 sentences).
- A **mid-section synthesis** that compares papers along design dimensions.
- A **closing summary paragraph** that names the consensus, the open question, or the
  unresolved debate.

## The Banana Rule for Surveys

In a survey, the same concept may be discussed in many sections. **Do not rename it.**

- If Section 2 calls it "self-attention", Sections 3, 6, 8 must also call it
  "self-attention" — not "attention mechanism", "attention", or "the attention block".
- If the taxonomy says "parameter-efficient fine-tuning (PEFT)", every section that
  references it must say PEFT, not "lightweight adaptation" or "adapter methods".
- Fix every synonym substitution for a defined term.

A survey reader is constantly cross-referencing sections. Synonym churn forces them to
ask "is this the same thing?" — which kills momentum.

## Sentence-Level Clarity

The standard sentence-clarity discipline applies (subject-verb proximity, important
info at end, context first, old-to-new flow, one unit one function, actions in verbs,
set the stage). The only survey-specific caveat: **do not bury the synthesis sentence**.
If a paragraph compares three papers, the synthesis sentence should usually come first
or last, not in the middle.

## Word Choice and Precision

### Strip AI-isms ruthlessly

These words are nearly always low-information:

| AI-ism | Replace with |
|--------|--------------|
| delve into | discuss / examine |
| pivotal | (delete or replace with specific role) |
| landscape | field / area / set |
| tapestry | range / variety |
| underscore | emphasize / show |
| noteworthy | (delete or replace with specific claim) |
| intriguingly | (delete or rephrase) |
| seamlessly | (delete) |
| robust | (replace with specific property) |
| navigate | use / handle |

### Replace vague terms with specific ones

| Vague | Specific |
|-------|----------|
| "many works" | "23 works" or "the majority of works in this cluster" |
| "recently" | "since 2023" |
| "significant improvement" | "15% reduction in perplexity" |
| "various approaches" | name three or call them out by category |
| "different methods" | name the methods or families |

### Stop hedging unnecessarily

Do not write "may", "can", "potentially" before every claim. If the cited evidence
supports a claim, state it. Reserve hedging for genuine uncertainty.

## Mathematical Writing in Surveys

Surveys generally include less math than research papers, but when math appears it must
be precise and pedagogical:

- **Define notation at first use**, even if the cited paper uses different notation —
  a survey reader is rarely the original author.
- **Pair formulas with intuition** — every equation should be accompanied by a sentence
  explaining what it computes and why the reader should care.
- **Keep notation consistent across sections** — if Section 2 uses $Q, K, V$ for
  attention queries/keys/values, every later section must use the same letters.

## Figure Design for Surveys

A survey typically needs three figure types:

### Figure 1: Taxonomy / Organizing Lens

This is the most important figure in the entire paper. Reviewers look at it before
reading the abstract. Requirements:

- Show the full taxonomy as a tree or radial diagram.
- Each leaf node should fit on one line.
- Annotate each node with paper count or representative paper names.
- Caption must be self-contained and explain the lens.

### Figure 2 (optional): Field Timeline

A papers-per-year bar chart, optionally segmented by taxonomy node. Useful for showing
where research effort has concentrated. Skip for narrow surveys (<50 papers).

### Comparison Tables

One per body section. Each table compares the section's primary papers along 4–6
dimensions (method, year, key contribution, complexity, accuracy if applicable). The
table is the section's synthesis — it should be readable in isolation.

### Caption Rules

A reviewer should understand the figure's point from the caption alone. State what is
being compared and what the reader should notice.

## Common Survey Mistakes

| Mistake | Fix |
|---------|-----|
| Paper-by-paper book report | Reorganize by design dimension, then place papers |
| Generic field-overview opening | Open with the specific scope and gap |
| No taxonomy figure | Add a Figure 1 showing the organizing lens |
| Vague "we discuss" contribution bullets | Replace with concrete, falsifiable bullets |
| Inconsistent terminology across sections | Apply the banana rule |
| Citation density imbalance | Aim for 3–8 citations per section, no orphans |
| AI-isms | Strip with prose-polish pass |
| Open problems section is a list of "more research needed" | Each open problem must name a concrete unresolved question and a promising direction |
| Conclusion repeats the introduction | Conclusion must summarize *findings* (cross-cutting trends), not *scope* |

## Pre-Submission Checklist for Surveys

### Narrative

- [ ] The organizing lens can be stated in one sentence.
- [ ] The Introduction makes the What / Why / How / So What clear.
- [ ] Every body section is a synthesis, not a paper-by-paper summary.

### Structure

- [ ] The abstract follows the five-part survey formula.
- [ ] The Introduction includes scope, taxonomy, contributions, roadmap.
- [ ] The taxonomy figure (Figure 1) is self-contained.
- [ ] Each body section has 3–8 citations, with no orphans.
- [ ] Open Problems section names concrete unresolved questions.
- [ ] Conclusion summarizes findings, not scope.

### Writing

- [ ] Terminology is consistent (banana rule).
- [ ] No generic field-background openings.
- [ ] AI-isms removed (delve, pivotal, landscape, tapestry, underscore, …).
- [ ] No paragraphs longer than 8 sentences.
- [ ] No sentences longer than 40 words (split if needed).
- [ ] Reverse outline test passes: topic sentences form a coherent narrative.

### Technical

- [ ] All `\cite{}` keys exist in `references.bib` (closed-set check).
- [ ] No phantom citations.
- [ ] All `\ref{}` resolve.
- [ ] Page count within target.
- [ ] No TODO / FIXME / DATA_NEEDED markers in final PDF.
- [ ] Bibliography contains only cited entries (no bloat).

## Final Sentence

**A survey is not a literature dump. It is a structured argument that the field, viewed
through one specific lens, makes more sense than it does without that lens.**
