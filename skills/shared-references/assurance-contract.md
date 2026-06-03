# Assurance Contract — AutoSurvey

AutoSurvey's audits emit machine-readable verdicts. The pipeline runs
all audits at the strictest level (`submission`) by default. The
softer levels exist only as a debugging tool for tooling that needs
to inspect a borderline draft without failing it; they are never
selected by `/survey-run` itself.

This contract is referenced by `survey-verify`, `survey-write`, and
`verify_survey_audits.sh`.

## Levels

| Level | Citation hard gate | Claim audit | Kill-argument |
|---|---|---|---|
| `draft` | block | skip | skip |
| `polished` | block | block | skip |
| `submission` | block | block | block |

The hard gate (phantom citation check) always blocks compile; the
other two activate as the level rises. **`/survey-run` always uses
`submission`.** A direct `tools/audit_writing.py --assurance polished`
invocation is the only way to relax the gate, and it exists for
inspecting work-in-progress drafts.

## Verdict state machine

Every mandatory audit emits exactly one of these:

| Verdict | Meaning | Submission-blocking? |
|---|---|---|
| `PASS` | All checks passed | No |
| `WARN` | Issues found, none disqualifying | No |
| `FAIL` | Disqualifying issues found | **Yes** |
| `NOT_APPLICABLE` | Nothing to audit | No |
| `BLOCKED` | Should apply but prerequisites missing | **Yes** |
| `ERROR` | Audit invocation failed | **Yes** |

## Required artifact — `CITATION_VERIFY.json`

```json
{
  "hard_gate": "PASS|FAIL",
  "coverage": "PASS|WARN",
  "claim_audit": "PASS|WARN|FAIL|NOT_APPLICABLE",
  "kill_argument": "ADDRESSED|PENDING|NOT_APPLICABLE",
  "coverage_scores": {"section_id": 0.0},
  "phantom_keys": [],
  "generated_at": "2026-01-01T00:00:00Z",
  "assurance_level": "submission"
}
```

The `assurance_level` field records the level the audit ran at; the
compile gate (`verify_survey_audits.sh`) defaults to `submission`
regardless of what is recorded in the file.

## Compile gate

`verify_survey_audits.sh <run-dir>` is the single source of truth.
It checks `CITATION_VERIFY.json` for:

1. `hard_gate == "PASS"`
2. `claim_audit` not `FAIL` or `BLOCKED`
3. `kill_argument` not `PENDING`

Non-zero exit **blocks the compile stage**.

## Audit surface

Surveys have no experiments, so the audit surface is narrower than
for research papers:

```
Citation hard gate → survey-verify Step 1 (phantom-cite check)
Paper claim audit  → survey-verify Step 3 (claim vs. cited abstract)
Adversarial review → survey-verify Step 4 (kill-argument)
```

All three run on every `/survey-run`.

## See also

- `reviewer-independence.md` — cross-model review invariant
- `tools/verify_survey_audits.sh` — external verifier
