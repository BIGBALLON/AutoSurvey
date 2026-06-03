# Reviewer Independence Protocol

## Core Principle

**Survey content must reach the reviewer unfiltered. The executor points to files and sets the review task; the reviewer reads and judges independently.**

Cross-model adversarial collaboration only works if the reviewer forms its own assessment from primary artifacts. If the executor pre-digests, summarizes, or interprets content before passing it to the reviewer, the reviewer is evaluating the executor's framing — not the actual work.

## What CAN be passed to the reviewer

- **Role/persona** — e.g., "Review as a senior survey editor"
- **Review objective** — e.g., "Evaluate coverage across taxonomy nodes"
- **File paths** — let the reviewer read file contents directly
- **Structural metadata** — e.g., "The survey has 8 sections", "Taxonomy has 6 leaf nodes"
- **Venue constraints** — e.g., "ACM CSUR format, 30-page limit"

## What CANNOT be passed (counts as "subjective interference")

- ❌ Executor's summary or paraphrase of section contents
- ❌ Executor's interpretation of coverage gaps (e.g., "I think Section 3 is weak")
- ❌ Executor's recommendations (e.g., "I suggest expanding the RL section")
- ❌ Key findings extracted by the executor
- ❌ Leading questions (e.g., "Is the coverage balanced?")
- ❌ Previous review rounds' feedback (let the reviewer assess current state fresh)
- ❌ Executor's description of what was changed since last round
- ❌ Statements asserting the current approach's strengths

## Why this matters

| With filtering | Without filtering |
|---|---|
| Reviewer sees executor's framing | Reviewer sees raw artifacts |
| Correlated blind spots persist | Genuinely independent assessment |
| Executor can "coach" favorable review | Review probes real weaknesses |
| Defeats purpose of cross-model | Achieves adversarial collaboration |

## Correct pattern

```
mcp__llm-chat__...:
  prompt: |
    Review the following survey paper as a senior editor of an IEEE survey journal.

    Files to read:
    - Taxonomy: ~/.autosurvey/runs/<id>/3_taxonomy.json
    - Outline: ~/.autosurvey/runs/<id>/4_outline/outline.json
    - Sections: ~/.autosurvey/runs/<id>/5_paper/sections/
    - Paper corpus: ~/.autosurvey/runs/<id>/1_search/filtered.jsonl (titles/abstracts)

    Please read all files yourself and evaluate the survey on:
    1. Coverage (each taxonomy node represented?)
    2. Coherence (consistent terminology, no contradictions)
    3. Structure (intro roadmap ↔ sections ↔ conclusion)
    4. Balance (section lengths proportional to taxonomy weight)
```

## When to apply

This protocol applies to all cross-model review calls in AutoSurvey:
- `survey-outline` — outline-sketch + structural outline review
- `survey-review` — content quality review (all rounds)
- `survey-verify` — claim audit (Step 3) and kill-argument (Step 4)

## Exception

Multi-round review within the SAME thread may reference the reviewer's own previous
feedback to check resolution — but still must not include executor interpretations.
