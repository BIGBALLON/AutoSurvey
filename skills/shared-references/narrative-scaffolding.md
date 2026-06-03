# Narrative Scaffolding — 4 Pillars + Open Problems 4-Bucket

The document-level narrative discipline that separates a survey paper from
an annotated bibliography. Complementary to (and tighter than)
`argument-skeleton.md`: the skeleton is per-section; this is per-paper.

Enforced by `audit_writing.py` (regex/structure scans on the assembled
`5_paper/sections/*.tex`).

## Why narrative scaffolding?

Compare two opening sentences:

> "By pretraining we mean the unconditional next-token optimisation of a
> language model's parameters from a random initialisation." (textbook)

vs.

> "In 2022, AI systems served as sophisticated typewriters; by 2025 they
> had become colleagues that independently navigate codebases, design
> experiments, and produce research artifacts." (the reference standard)

The first defines a concept. The second **stages a transition** with a
year anchor + a metaphor + an implicit claim. Reading the first, you
absorb a definition; reading the second, you have already adopted the
author's point of view.

Four narrative pillars make this difference systematic and auditable.

## The 4 Pillars

### Pillar 1 — Hook

**Where**: First paragraph of the Introduction.

**Required**:
- ≥ 1 explicit year token (e.g. "In 2022", "early 2024", "By 2026")
- ≥ 1 numeric drama point (a percentage, a rate, a count, a ratio)
- ≥ 1 metaphor or comparison ("from X to Y", "like", "—")

**Reference exemplar**: *"the resolution rate on SWE-bench climbed from
under 5% to over 70% within eighteen months"*. Year (2024–2025), numbers
(5% → 70%, 18 months), comparison ("from … to …").

**Anti-pattern**: encyclopedic openings ("This survey covers X across Y
dimensions and Z themes"). These announce the table of contents.

### Pillar 2 — Why Now?

**Where**: Subsection inside Introduction, with that exact heading or one
of its accepted variants ("Why Now?", "Why now?", "The inflection
point", "Why this survey now").

**Required**: 3 enumerated, concrete reasons explaining why the survey is
timely, each anchored to ≥ 1 citation. The reference paper's three are:

1. Foundation models crossed capability thresholds (cited)
2. Agent architectures matured from prototypes (cited)
3. Evaluation infrastructure reached maturity (cited)

This pillar is what separates "this is interesting" (assertion) from
"this is interesting now because of A, B, C" (argument).

### Pillar 3 — Relationship to Existing Surveys

**Where**: Subsection at end of Introduction (before §1.4 or before §2),
heading: "Relationship to Existing Surveys" (or "Relationship to Prior
Surveys" / "Differences from Existing Surveys").

**Required**: Identify ≥ 2 prior surveys in the same area and explicitly
say what each does and does NOT do. Conclude with a one-sentence claim
about what this survey adds.

**Why it matters**: It pre-empts the most common reviewer complaint
("isn't this redundant with [X]?"). Stating it up-front is a sign of
seriousness.

**Closed-set rule**: All cited surveys must appear in `filtered.jsonl`.
The agent SHOULD search for `"survey of X"` / `"comprehensive review of
X"` queries during the search stage to ensure coverage.

### Pillar 4 — Numbered Contributions

**Where**: At the end of Introduction (before the Paper Organization
paragraph).

**Required**:
- A LaTeX `\begin{enumerate}` ... `\end{enumerate}` block (NOT itemize)
- ≥ 4 contributions
- Each contribution is **one specific declarative sentence** describing what the
  paper produces (a taxonomy / a comparative framework / an analysis of N
  systems / a research agenda)
- Each contribution should map to one or more body sections

**Anti-pattern**: 1 vague item ("we provide a comprehensive overview"); or
mixing contributions with motivation. Distinct from Pillar 2: Why Now is
about the field's readiness; Contributions are about THIS paper's
specific deliverables.

## Open Problems — the 4-bucket structure

The Open Problems chapter (typically §6 or §7, depending on the topic) is
where most surveys turn into a wishlist. AutoSurvey prevents this with
a strict 4-bucket structure for **each** open-problem subsection:

```
\subsection{The Memory Bottleneck} % example title

% [PROBLEM-STATEMENT]
What the problem is, in concrete terms with at least one number/example.

% [EXISTING-APPROACHES]
2-4 paragraphs, each describing an existing line of attack with citations.

% [LIMITATIONS]
Where each existing approach falls short. Be specific; "more research is
needed" is not a limitation.

% [RESEARCH-DIRECTIONS]
3 enumerated concrete directions, each phrased as a question or a
hypothesis a graduate student could pursue.
```

Each Open-Problem subsection becomes a **mini-survey-in-miniature**, not a
sentence. `audit_writing.py` checks the 4 anchor comments per
subsection. Failure to provide all four for any open-problem subsection
→ exit 1 (submission gate).

## Audit (audit_writing.py — narrative pillar checks)

| Pillar | Detector | Severity |
|---|---|---|
| Hook | Regex on Intro first paragraph: must match year (`\b(19\d\d|20\d\d)\b`) AND a number (`\b\d+(?:\.\d+)?(?:%\|×\|x)?\b`) AND a metaphor/comparison marker (`\bfrom\b.*\bto\b\|\b—\|like\b`) | critical at submission |
| Why Now | Regex on Intro: `\\subsection\{(Why Now\?\|The Inflection Point\|...)\}` AND ≥ 3 numbered/enumerated points within | critical |
| Relationship to Existing Surveys | Section heading match AND ≥ 2 `\citet`/`\citep` AND a "this survey adds X" claim | critical |
| Numbered Contributions | `\\begin\{enumerate\}` ... `\\end\{enumerate\}` block in Intro AND ≥ 4 `\\item`s | critical |
| Open-problem 4-bucket | For each subsection in Open Problems: 4 anchor comments present | critical |

Score = passing pillars / 5 (treating Open Problems as one pillar). Submission gate: ≥ 0.9.

## Anti-patterns (caught by `prose_polish.py` NARRATIVE_RULES)

| Anti-pattern | Why bad |
|---|---|
| "This survey provides a comprehensive overview of X" | Encyclopedic, no claim |
| "We hope to inspire future research" | Wishlist tone, not contribution |
| "It is worth noting that ..." | AI-ism / filler (already caught by AI_ISMS) |
| "the rich tapestry of X" | AI-ism / filler |
| "Recent advances ..." (without year + number) | Vague Hook |
| Open Problem section as 1-paragraph bullet list | Bypasses 4-bucket discipline |

## See also

- `argument-skeleton.md` — the per-section 5-step skeleton (CLAIM /
  STEELMAN / EVIDENCE / CONCESSION / SO-WHAT)
- `thesis-contract.md` — the integration point: thesis stated in Hook
  (one sentence), in numbered Contributions (paraphrased), and Conclusion
- `reviewer-personas.md` — 2 reviewer personas (senior + skeptic) that
  audit narrative quality at the document level
