# Conclusion Template — AutoSurvey

This file gives the writer (LLM or human) one verbatim reference
conclusion that satisfies structural-template invariant 7 (400–700
words, no bullets, re-frame-not-summary). It is taken from the
benchmark survey *From Copilots to Colleagues: A Survey of Autonomous
Research Agents* (45 pp, NeurIPS-style), which is the artefact the
audit suite calibrates against.

The writer prompt in `survey-write/SKILL.md` Step 6 references this
file by path, **not** by inlining the example, so the prompt budget
stays tight. The agent reading the prompt should fetch this file when
it is about to write the conclusion section.

## Structure

The pattern is **opener + bold-lead findings paragraphs +
call-to-action close**. Concretely:

1. **One opener paragraph** that names the survey's central thesis
   in the strongest available terms. Do not paraphrase the abstract;
   the abstract told the reader what is *in* the survey, the opener
   tells them what the survey *means*.
2. **Three to five bold-lead findings paragraphs.** Each begins with
   a 2–4-word lead in `\textbf{}` (e.g. `\textbf{Taxonomy and
   Definitions.}`, `\textbf{Architectural Patterns.}`) that names a
   cross-cutting finding the reader could not have written before
   reading the survey. The bolded lead tells the skim-reader
   *what* the paragraph re-frames; the paragraph itself states the
   pattern in 60–100 words.
3. **One closing call-to-action paragraph**, also bold-led
   (typically `\textbf{A Call to Action.}`). It names (a) what
   concrete next steps the field should take, and (b) the conditions
   under which the thesis would have to be revised. This is the
   intellectually honest move — what observation would invalidate
   the survey's framing.

## Verbatim example (641 words)

The benchmark conclusion below is reproduced verbatim. Counting
shows: 1 opener (43 words), 5 bold-lead findings (Taxonomy /
Architectural / System / Evaluation / Open Problems = 401 words
combined), 1 call-to-action close (197 words). 0 `\itemize` or
`\enumerate` environments. 0 bulleted lines.

```latex
\section{Conclusion}

This survey has provided a comprehensive analysis of autonomous
research agents---systems that independently formulate hypotheses,
design experiments, execute them, and iterate toward novel
discoveries. We conclude by synthesizing the key findings across our
six main contributions and identifying priorities for the research
community.

\textbf{Taxonomy and Definitions.} Our L1--L5 autonomy hierarchy
provides a precise vocabulary for an otherwise fragmented field.
Current frontier systems (the AI Scientist, SWE-Agent, Devin, Claude
Code) operate firmly at L4: they execute multi-step research
workflows with strategic self-direction within bounded problem
spaces. The transition to L5---agents that set their own research
agendas across open domains---remains aspirational, requiring
advances in persistent knowledge accumulation, self-directed
exploration, and robust self-evaluation that no existing system yet
demonstrates.

\textbf{Architectural Patterns.} We identified four dominant
paradigms---single-agent reasoning loops, multi-agent collaboration,
hierarchical orchestration, and tool-augmented execution---each with
characteristic trade-offs between capability ceiling, reliability,
cost, and interpretability. The trend is toward hybrid architectures
that combine hierarchical coordination with specialized tool-using
sub-agents, but no single design dominates across all application
contexts.

\textbf{System Landscape.} Our comparative analysis of 17 systems
across a six-dimensional feature matrix reveals rapid maturation:
from fragile prototypes (AutoGPT, 2023) to production-grade systems
resolving real engineering tasks at over 70\% success rates within
eighteen months. However, this progress concentrates in well-defined
domains (code, constrained optimization) while open-ended scientific
discovery remains largely at the demonstration stage.

\textbf{Evaluation Challenges.} The field's evaluation infrastructure
has advanced considerably, with benchmarks like SWE-bench providing
standardized measurement. Yet fundamental challenges persist:
open-ended research lacks ground truth, cost reporting is
inconsistently applied, and benchmark saturation at the top end
obscures meaningful capability differences. The critique by Kapoor et
al.\ (2024)---that many reported improvements reflect evaluation
optimization rather than genuine capability gains---demands ongoing
methodological vigilance.

\textbf{Open Problems.} Among the six challenges we identified,
three stand out as defining the research frontier. First, the
cognitive loop problem: agents still fail to recognize when they are
stuck, perseverating on failed strategies rather than seeking
fundamentally different approaches. Second, evaluation of novelty:
without reliable automated measures of research quality and
originality, we cannot close the loop on agent self-improvement.
Third, safety and alignment: as agents become more capable, the gap
between what they can do and what they should do widens, requiring
governance frameworks that do not yet exist at adequate maturity.

\textbf{A Call to Action.} The transition from copilots to colleagues
is neither inevitable nor uniformly beneficial. Realizing the promise
of autonomous research agents while managing their risks requires
coordinated effort across several fronts: developing principled
evaluation frameworks for open-ended research, establishing safety
standards for agent deployment in high-stakes domains, creating
shared infrastructure that democratizes access beyond well-resourced
institutions, and building the theoretical foundations (scaling laws,
formal verification, regret bounds) that can transform agent
development from an empirical art into an engineering discipline. The
pace of progress suggests that L5 autonomy---agents capable of
self-directed, long-horizon research programs---is a question of when
rather than whether. The research community's task is to ensure this
transition occurs with adequate understanding, appropriate
safeguards, and equitable distribution of benefits.
```

## What this example does NOT do

These are anti-patterns the writer prompt explicitly forbids:

- It does **not** open with "In conclusion, we have presented…".
  Earned by the bold-lead structure: the reader knows it is a
  conclusion because of where it sits, not because it announces
  itself.
- It does **not** restate the section structure. The opener names
  the *thesis*, not the *table of contents*.
- It does **not** use `\itemize` or `\enumerate`. Every finding
  paragraph stands as continuous prose with the bolded lead doing
  the navigational work that bullets would otherwise do — bullets
  in a conclusion are the writer admitting they did not bother to
  write transition prose.
- It does **not** close with "future work". The Call to Action
  names *what to do* and *what would invalidate the thesis*; "future
  work will tell" is unfalsifiable filler.

## Calibration

This template is the regression bar for `audit_writing.py`'s
`conclusion_reframe` invariant:

| Signal | Benchmark value | Audit rule |
|---|---|---|
| Word count | 641 | 400 ≤ N ≤ 700 |
| `\itemize`/`\enumerate` | 0 | 0 |
| Bullet lines (`\item`/`-`/`*`) | 0 | density < 0.5 / 50 words |
| Bold-lead paragraphs | 6 | not directly measured (signals re-frame structure) |

If a generated conclusion fails the audit, compare it against this
template paragraph-by-paragraph and identify the missing structural
move (opener? bold-leads? Call to Action?). Do not lower the audit
threshold; rewrite the conclusion.
