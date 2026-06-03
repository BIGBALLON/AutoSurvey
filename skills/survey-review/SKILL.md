---
name: survey-review
description: Use when running auto-improvement rounds on a written AutoSurvey draft, or when /survey-review is invoked. each round runs **2 reviewer personas** (Senior Reviewer + Skeptic) in parallel fresh threads; the author addresses every demand with accept/partial/reject + reason. The demands are presented as a human checkpoint between rounds (--auto-confirm skips it for CI). 2 rounds by default.
---

# /survey-review (— 2 personas + author + optional human checkpoint)

The Phase 3 (Polishing) review substep runs two distinct reviewer
personas in parallel:

- **Senior Reviewer** — domain-aware demands focused on document-level
  arc (thesis coherence, contribution clarity, Open-Problems
  tractability)
- **Skeptic** — adversarial demands hunting for selection bias,
  conflated claims, missing concessions, opaque-source overreach

Both run as **fresh LLM threads** (REVIEWER_BIAS_GUARD), never
continuations of the writing thread, per
`shared-references/reviewer-independence.md`.

The author then addresses every demand individually with
`accept | partial | reject + reason` — and the result becomes a
re-runnable patch list. Unless `--auto-confirm` is set, the user
inserts themselves into the loop via a human checkpoint between
demand collection and author response.

Spec: `shared-references/reviewer-personas.md` (verbatim system
prompts for each persona, demand schema, author-response schema,
checkpoint protocol).

## invocation

```
/survey-review [--rounds N] [--run-id <id>] [--auto-confirm]
```

- `--rounds N`: override the default of 2 rounds.
- `--auto-confirm`: skip the human checkpoint (CI-friendly; treats every
  demand as `accept` on the user's behalf).

## Prerequisites

- `5_paper/sections/*.tex` exist (Phase 2 / `/survey-write` complete)
- `5_paper/main.tex` exists
- `2_thesis/thesis.json` exists (so reviewers can check thesis
  coherence)
- State: `phases.arguing.status == "completed"`

## round protocol

```python
for round_num in range(1, MAX_ROUNDS + 1):
    # Step A — backup
    snapshot_sections_to(f"7_review/round{round_num - 1}_snapshot/")

    # Step B — collect demands from 2 personas (fresh threads, parallel)
    senior_demands = run_persona("senior_reviewer", round_num) # 3-5 demands
    skeptic_demands = run_persona("skeptic", round_num) # 3-5 demands
    write(f"7_review/round{round_num}/senior.json", senior_demands)
    write(f"7_review/round{round_num}/skeptic.json", skeptic_demands)

    # Step C — human checkpoint (skipped only by --auto-confirm)
    if not auto_confirm:
        cp = {
            "round": round_num,
            "personas": ["senior", "skeptic"],
            "demands": {"senior": senior_demands, "skeptic": skeptic_demands},
            "user_decisions": {d["id"]: "accept" for d in senior_demands + skeptic_demands},
            "checkpoint_status": "pending_user",
        }
        write(f"7_review/round{round_num}/checkpoint.json", cp)
        if user_has_not_yet_edited(cp_file):
            print(f"⏸️ Checkpoint pending: edit user_decisions in {cp_file}")
            print(f" Then re-run /survey-run --resume <run_id>")
            sys.exit(0)
        # Read user-edited decisions
        cp = json.load(open(cp_file))
    else:
        cp = {
            "user_decisions": {d["id"]: "accept" for d in senior_demands + skeptic_demands},
            "checkpoint_status": "auto_confirmed",
        }

    # Step D — author addresses every demand
    accepted_demands = [d for d in senior_demands + skeptic_demands
                        if cp["user_decisions"].get(d["id"]) in ("accept", "partial")]
    author_responses = run_author(accepted_demands, round_num)
    write(f"7_review/round{round_num}/author_responses.json", author_responses)

    # Step E — re-run prose_polish (always; fast)
    invoke("python3 tools/prose_polish.py <run_dir> --fix")

    # Step F — re-run structural sanity (5-anchor / audit_writing;
    #          /survey-review does not invoke reverse_outline.py — that
    #          runs in /survey-write Step 9)
    # The equivalent is the 5-anchor check inside prose_polish
    # (--strict-narrative) and the audit_writing.py check in Phase 3 audits.

    # Step G — append to SURVEY_IMPROVEMENT_LOG.md
    append_to_log(round_num, senior_demands, skeptic_demands, author_responses)

    # Step H — early-stop heuristic: if both personas produced 0 demands, stop
    if not senior_demands and not skeptic_demands:
        print("✅ No further demands; review converged.")
        break

# Final state update
state["phases"]["polishing"]["substeps"]["review"]["status"] = "completed"
state["phases"]["polishing"]["substeps"]["review"]["rounds"] = round_num
state["phases"]["polishing"]["substeps"]["review"]["personas"] = ["senior", "skeptic"]
```

## Persona invocation

For each persona, the agent runs **a fresh LLM thread** (no prior
context) with:

1. The verbatim system prompt seed from
   `shared-references/reviewer-personas.md`
2. The full draft (assembled `5_paper/main.tex` + `sections/*.tex`,
   typically ~30 pages compiled = ~12 K tokens)
3. The thesis (`2_thesis/thesis.json`) — necessary context for
   judging coherence
4. The closed paper list (`1_search/filtered.jsonl` cite_keys) —
   so the persona's `suggested_patch` references stay closed-set

Output schema (per persona):

```json
[
  {"id": "SR-01", "section": "02_architecture", "line_ref": "L42",
   "demand": "...", "suggested_patch": "..."},
  ...
]
```

## Author invocation

Single fresh thread with:

1. The accepted demands (from the checkpoint)
2. The current section files (relevant sections only — the agent
   reads from disk during execution)
3. Closed-set rule reminder

Output: `author_responses.json` per `reviewer-personas.md`. The agent
writes the actual .tex changes using normal file I/O.

## Default round count

`/survey-review` runs **2 rounds** by default. Override with
`--rounds N`. Total agent calls per round = 3 (2 personas + 1 author).

## Output Files (per round N)

```
7_review/round1/
├── senior.json # SR-NN demands
├── skeptic.json # SK-NN demands
├── checkpoint.json # human checkpoint (skipped by --auto-confirm)
└── author_responses.json # accept/partial/reject + patches
```

Plus a cumulative `5_paper/SURVEY_IMPROVEMENT_LOG.md` across rounds.

## tools that survey-review does NOT invoke

- `reverse_outline.py` runs in `/survey-write` Step 9 to produce
  `4_outline/reverse_outline.md` (topic-sentence skeleton consumed by
  the evidence dashboard). `/survey-review` does not re-run it; the
  narrative-coherence checks at this phase are
  `prose_polish.py --strict-narrative` (4 narrative pillars + 5-anchor
  scan) and `audit_writing.py` (the Phase 3 submission gate).
- A numeric-score rubric (Coverage / Coherence / etc.) is intentionally
  not used here. Score-without-action is noise; demands-with-patches
  are the signal we audit and act on.

---

# Implementation reference (deterministic pieces)

The Steps below specify the deterministic mechanics the round protocol
above relies on: REVIEWER_BIAS_GUARD (fresh-thread reviewer), snapshot
pattern, executor pseudo-code, and the `SURVEY_IMPROVEMENT_LOG.md`
format. The author / persona prompts and demand-response schema live
at the top of this file (see "## round protocol").

---



## Invocation

```
/survey-review [--rounds N] [--run-id <id>] [--human-checkpoint]
```

- `--rounds N` overrides the default of 2 rounds
- `--human-checkpoint` pauses after each round's review for user approval

---

## Prerequisites

- `5_paper/sections/*.tex` exist and at least one round of `/survey-write` has run
- `5_paper/main.tex` exists (assembled by `/survey-write`)
- `state.json` shows `write.status == "completed"`

---

## Constants

- **MAX_ROUNDS** — defaults to 2; override with `--rounds N`
- **REVIEWER_BIAS_GUARD = true** — every round uses a FRESH LLM thread; never use
  conversation continuation. Empirically, continuing a thread inflates scores
  (the model rationalizes the previous draft as "improved enough"). Fresh threads
  recover an honest assessment.
- **REVIEW_LOG = `SURVEY_IMPROVEMENT_LOG.md`** — cumulative log across rounds
- **HUMAN_CHECKPOINT = false** — when `true`, pause after each round's review for
  user input
- **MIN_SCORE_TO_STOP_EARLY = 8.0** — if a round's score is ≥ this, skip remaining rounds

---

## Workflow

```
For round N in 1..MAX_ROUNDS:
    Step A Backup current draft (snapshot 5_paper/sections/ + main.pdf)
    Step B Collect demands from 2 personas (Senior + Skeptic) — fresh
           parallel LLM threads, REVIEWER_BIAS_GUARD enforced
    Step C Human checkpoint (skipped only by --auto-confirm); user
           edits accept/partial/reject in checkpoint.json
    Step D Author addresses every accepted demand (closed-set guard,
           phantom-check) and writes the actual .tex edits
    Step E Re-run prose_polish.py to verify no quality regressions
    Step F Re-run structural sanity (5-anchor check / audit_writing)
    Step G Append round artifacts to SURVEY_IMPROVEMENT_LOG.md
    Step H Early-stop: if both personas produced 0 demands → break
After all rounds: produce final summary
```

Early-stop condition: if both personas produced 0 demands in a
round, the loop exits without further rounds.

---

## Step A — Backup current draft

Before each round, snapshot the current state:

```bash
ROUND="<N>"
RUN_DIR="<run_dir>"
SECTIONS_DIR="$RUN_DIR/5_paper/sections"

# Snapshot section files
mkdir -p "$RUN_DIR/5_paper/round_snapshots"
cp -r "$SECTIONS_DIR" "$RUN_DIR/5_paper/round_snapshots/round${ROUND_PREV}_sections"
# If PDF exists, snapshot it
[ -f "$RUN_DIR/main.pdf" ] && cp "$RUN_DIR/main.pdf" "$RUN_DIR/5_paper/round_snapshots/main_round${ROUND_PREV}.pdf"
```

---

## Step B — Fresh-thread reviewer (REVIEWER_BIAS_GUARD)

**Critical invariant:** the reviewer must NOT see prior round artifacts. Pass ONLY
the current draft files. No continuation prompts. No "in the previous round we did X"
context.

The reviewer reads the following files:

```
5_paper/main.tex
5_paper/sections/*.tex
3_taxonomy.json
4_outline/outline.json
4_outline/reverse_outline.md # auto-generated by survey-write
5_paper/prose_polish_report.json # auto-generated by survey-write
1_search/filtered.jsonl # for ground-truth corpus context
```

NOT shown to the reviewer:
- Prior `SURVEY_IMPROVEMENT_LOG.md` rounds
- Prior round snapshots
- The user's writing-style preferences

The 2-persona system prompts and demand-response schema (Senior Reviewer +
Skeptic, each emitting 3–5 actionable demands; author responds with
accept / partial / reject + reason) are specified at the top of this
file under "## round protocol", with verbatim text in
`shared-references/reviewer-personas.md`.

---

## Step E — Executor implements fixes

For each fix in `actionable_fixes` (in order, CRITICAL → MAJOR → MINOR):

1. **Read the current section file**
2. **Apply the fix** — usually a section rewrite, paragraph addition, or
   terminology normalization
3. **Closed-set guard:** any new `\cite{}` introduced by the fix MUST be from
   the section's allowed key pool (see `survey-write/SKILL.md` Step 4c).
   Reject fixes that introduce phantom cites; log to `review_round_{N}_rejected.json`.
4. **Save the fixed file**

Fix-implementation MUST run a phantom-citation check after every section edit. If
any phantom is introduced, rollback that single fix and continue.

```python
# Pseudo-code:
for fix in sorted(fixes, key=lambda f: severity_rank(f["severity"])):
    sid = fix["section"]
    fpath = f"5_paper/sections/{find_section_file(sid)}"
    before = read(fpath)
    after = apply_fix(before, fix) # delegated to LLM with section's key pool
    if introduces_phantom_cite(after, allowed_keys):
        log_rejection(fix, reason="phantom_cite_introduced")
        continue
    write(fpath, after)
```

---

## Step F — Re-run determinist quality checks

```bash
python3 tools/prose_polish.py "$RUN_DIR" --check # warn-only here, not --fix
# /survey-review does not re-run reverse_outline.py; narrative-coherence
# is covered by prose_polish.py --strict-narrative and the audit_writing.py
# submission gate. See "## tools that survey-review does NOT invoke" above.
```

Findings are appended to `SURVEY_IMPROVEMENT_LOG.md` for the round.

---

## Step G — Append to SURVEY_IMPROVEMENT_LOG.md

```markdown
# Survey Improvement Log

## Round 1 — Score: 6.2/10 → 7.5/10

### Reviewer Verdicts (pre-fix)
- Coverage: PASS
- Coherence: PASS
- Structure: WARN (abstract opens with "In recent years")
- Balance: PASS
- Depth: WARN (multimodal section narrates without numbers)
- Foresight: WARN (trends section claims not anchored to figures)

### Fixes Applied (4)
- [CRITICAL] Rewrite abstract opener to start with specific scope
- [MAJOR] Multimodal section: weave card-extracted parameter counts into paras 3–5
- [MAJOR] Trends: anchor each forward claim to ≥2 cite_keys + a figure
- [MINOR] Strip "delve" from intro paragraph 2

### Fixes Rejected (1)
- [MAJOR] phantom_cite_introduced — proposed cite of 'liu2024hyenadna' not in pool

### Post-fix quality checks
- prose_polish: 0 AI-isms, 1 clutter (down from 5/3)
- reverse_outline: 1 weak topic sentence (down from 2)

## Round 2 — Score: 7.5/10 → 8.4/10
...
```

Save to `5_paper/SURVEY_IMPROVEMENT_LOG.md`.

---

## Step H — Early-stop check

After Step G:
- If `overall_score >= MIN_SCORE_TO_STOP_EARLY` AND no CRITICAL fixes were
  rejected in this round → exit loop early, log "early-stop on score".
- Otherwise, continue to next round (if any rounds remain).

---

## Final state.json update

```json
{
  "stages": {
    "review": {
      "status": "completed",
      "rounds_run": 1,
      "rounds_max": 1,
      "early_stop": false,
      "scores": [6.2, 7.5],
      "final_score": 7.5,
      "round_artifacts": [
        "5_paper/review_round_1.json"
      ],
      "improvement_log": "5_paper/SURVEY_IMPROVEMENT_LOG.md",
      "snapshots": "5_paper/round_snapshots/"
    }
  }
}
```

If both personas produced 0 demands in round 1, the loop exits early
and `rounds_run = 1`.

---

## Final report

```
✅ Survey review complete

  Rounds: 2
  Initial score: 6.2/10
  Final score: 7.5/10 (+1.3)
  CRITICAL fixes: 1 applied, 0 rejected
  MAJOR fixes: 1 applied, 1 rejected (phantom cite)
  MINOR fixes: 2 applied
  Snapshots: 5_paper/round_snapshots/round0_sections/
                        5_paper/round_snapshots/round0_main.pdf
  Log: 5_paper/SURVEY_IMPROVEMENT_LOG.md

  Next: /survey-verify (citation hard gate, etc.)
```

---

## Output Files

| File | Contents |
|---|---|
| `5_paper/review_round_{N}.json` | Reviewer verdict for each round |
| `5_paper/SURVEY_IMPROVEMENT_LOG.md` | Cumulative log |
| `5_paper/round_snapshots/round{N}_sections/` | Pre-fix snapshot per round |
| `5_paper/round_snapshots/main_round{N}.pdf` | Pre-fix PDF (if compiled) |
| `state.json` | Updated review stage completion + scores |

---

## Key Rules

- **Reviewer independence is non-negotiable.** Each round = fresh thread. No
  continuation prompts. No prior-round context. (REVIEWER_BIAS_GUARD).
- **Phantom-cite guard during fixes.** Closed-set is enforced again at fix-time;
  a fix that introduces a phantom is rejected, not silently approved.
- **Snapshot before fix.** Every round saves `round_snapshots/round{N-1}_*` so
  a regression can be rolled back.
- **Surveys do not have experiments.** Do NOT include experimental claim audits
  or numerical-result audits in this skill — those belong to `/survey-verify`'s
  Step 3 (claim audit) and are about citation/abstract alignment, not experimental
  numbers.
- **No mid-pipeline reviewer escalation.** If the reviewer's score is < 4/10 in
  Round 1 with CRITICAL findings, FLAG to user and pause — do not silently apply
  20+ fixes that may rewrite the entire paper.

---

## Empirical Motivation

Two design choices in this skill come from observed failure modes:

1. **REVIEWER_BIAS_GUARD = fresh threads.** Continuation-style prompts ("since
   last round we improved X, please re-review") inflate scores from real 5/10 →
   fake 8/10. Fresh threads recover honest assessment.

2. **Phantom-cite guard at fix-time.** When the reviewer says "add citation to a
   recent SSM paper", the executor's first instinct is to write `\cite{gu2024hyena}`
   even when that key isn't in the section's pool. Closed-set enforcement at
   fix-time catches this; without it, the survey-verify hard gate later would
   fail and require another full pass.

---

## See Also

- `skills/shared-references/reviewer-independence.md` — REVIEWER_BIAS_GUARD invariant
- `skills/shared-references/survey-writing-principles.md` — synthesis patterns,
  banana rule, what each section should look like
- `skills/survey-write/SKILL.md` — produces the artifacts this skill reviews
- `skills/survey-verify/SKILL.md` — the post-review citation hard gate
