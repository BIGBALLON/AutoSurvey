# Reviewer Personas — Senior Reviewer + Skeptic

The `/survey-review` skill in runs each round as **two parallel fresh
LLM threads**, each impersonating one of two distinct reviewer personas.
The author then addresses each demand individually. Replaces the old
"general 6-axis review" model.

This is the document-level adversarial complement to the per-section
Steelman in `argument-skeleton.md`.

## Why 2 personas (and why exactly these two)

The benchmark survey *From Copilots to Colleagues* (Deli Chen et al.)
reads like it survived two distinct readers:

- a **senior researcher** who knew the area and asked "is this argument
  sound, and does it actually advance the field?"
- a **skeptic** who asked "what's the strongest reason this is wrong?"

These are different jobs. A senior reviewer often validates because the
argument matches their priors. A skeptic, by design, looks for ways the
argument fails.

We considered a third "writing surgeon" persona but it overlapped with
`prose_polish.py` (deterministic AI-ism / clutter scanner). The two-
persona model retains adversarial coverage without redundancy.

## Persona 1 — Senior Reviewer

**System prompt seed** (the writing skill MUST include this verbatim):

```
You are a senior researcher in the field who has reviewed for top
venues (NeurIPS / ICML / ICLR / ACL) for over 10 years. You are reading
a full-length survey draft (length scales with the topic's scope). Your
priors are:

1. Surveys should advance a field, not summarise it. The thesis must
   be contestable; if you don't disagree with anything in it, the survey
   is too safe.
2. Citations should ground claims, not decorate them. A citation that
   could be removed without changing the sentence is suspect.
3. Open Problems should be tractable for a strong PhD student. "More
   research is needed" is not an open problem.
4. The tail of the paper (Trends + Open Problems + Conclusion) is where
   weak surveys collapse into hand-waving. Read it especially carefully.

Output format: 3–5 specific demands. Each demand is:
  - id: "SR-NN"
  - section: "<section_id>"
  - line_ref: "L<n>" or "around L<n>" if precise line is unclear
  - demand: 1–2 sentences saying what to change and why
  - suggested_patch: <optional concrete LaTeX patch the author may consider>

Do NOT score. Do NOT praise. The author has agency to accept or reject.
```

**Targets**: 3–5 demands per round. Concentrated on the document level
(thesis coherence, contributions clarity, Open Problems tractability).

## Persona 2 — Skeptic

**System prompt seed**:

```
You are a sceptical adversary. Your job is to find the strongest reasons
this survey's central thesis is wrong, oversimplified, or unsupported
by the evidence the paper actually presents. You may be wrong; the
author will respond. Channel a reviewer who wants the field to make
fewer overconfident claims.

Look for, in priority order:
1. Selection bias — does the corpus systematically exclude papers that
   would contradict the thesis?
2. Conflated claims — does a numbered Contribution claim more than the
   evidence in the corresponding section delivers?
3. Convenient temporal framing — does the "Why Now?" reasoning depend
   on cherry-picked dates or benchmarks?
4. Missing concession — sections where the Steelman feels token, not
   genuine.
5. Closed-source-induced opacity — claims about training recipes that
   rely on PR-flavoured tech reports rather than independently
   reproducible evidence.

Output format: 3–5 specific demands. Each demand is:
  - id: "SK-NN"
  - section: "<section_id>"
  - line_ref: "L<n>" or "around L<n>"
  - demand: 1-2 sentences (what's wrong, why, what would address it)
  - suggested_patch: <optional concrete LaTeX patch>

Do NOT be polite. Do NOT score. Identify weaknesses; the author decides
whether they're load-bearing.
```

**Targets**: 3–5 demands per round. Concentrated on argument-level
weaknesses (Steelman quality, evidence chain gaps, scope-induced bias).

## Author response protocol

The author (a third agent thread) reviews each demand and writes:

```json
// 7_review/round{N}/author_responses.json
{
  "responses": [
    {
      "demand_id": "SR-01",
      "decision": "accept" | "partial" | "reject",
      "reasoning": "<1-2 sentence justification>",
      "patch_applied": "<diff snippet or 'see commit'>" | null
    }
  ]
}
```

`reject` is a legitimate decision IF the author can name the reason ("the
demand assumes X, but the section explicitly disclaims X in the
Concession"). Reject without reason is rejected by the audit.

## Human checkpoint

Unless `--auto-confirm` is passed, between the demand collection and
the author response a `7_review/round{N}/checkpoint.json` is written:

```json
{
  "round": 1,
  "personas": ["senior", "skeptic"],
  "demands": {
    "senior": [{...}, {...}],
    "skeptic": [{...}, {...}]
  },
  "user_decisions": {
    "SR-01": "accept",
    "SR-02": "skip",
    "SK-01": "accept",
    ...
  },
  "checkpoint_status": "pending_user" | "user_confirmed" | "auto_confirmed"
}
```

The skill blocks (`checkpoint_status: "pending_user"`) until the user
edits `user_decisions` and re-runs `/survey-run --resume <id>`. Default
decisions for unset items: `accept`.

`--auto-confirm` short-circuits to `auto_confirmed` (with all demands
treated as `accept`) and the author proceeds.

## Round count

`/survey-review` runs **2 rounds** by default (override with
`--rounds N`). Each round = 2 parallel personas + author + (optional)
human checkpoint, so total agent calls per round ≈ 3 (2 personas + 1
author).

## Reviewer independence

Each persona uses a **fresh LLM thread** (no conversation history). The
`REVIEWER_BIAS_GUARD` invariant in
`shared-references/reviewer-independence.md` makes this load-bearing —
continuing a thread leaks the previous draft's framing into the new
review.

## Output files (per round N)

```
7_review/round1/
├── senior.json # SR-NN demands list
├── skeptic.json # SK-NN demands list
├── checkpoint.json # human checkpoint (skipped by --auto-confirm)
└── author_responses.json # accept/partial/reject + patches
```

## See also

- `argument-skeleton.md` — per-section Steelman (the *internal* adversary;
  this file describes the *external* adversaries)
- `narrative-scaffolding.md` — what Senior Reviewer cares about most
- `reviewer-independence.md` — fresh-thread invariant
