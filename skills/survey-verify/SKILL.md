---
name: survey-verify
description: Use when running citation integrity / claim audit on an AutoSurvey draft, or when /survey-verify is invoked. emits CITATION_VERIFY.json (hard gate + claim_audit + numeric_grounding + kill_argument) AND runs validate_artifacts.py (thesis/claims/cite-key closed-set/decision_summary schemas) + audit_writing.py (5-anchor + narrative 4-pillar + thesis coherence + Open-Problems 4-bucket + claim-grounding upgrade). All five gates are checked by verify_survey_audits.sh before compile.
---

# /survey-verify

Citation + writing integrity verification. Two layers :

1. **Citation integrity** (existing) — `CITATION_VERIFY.json`
   - Hard gate: phantom-cite check (always on)
   - claim_audit: sentence vs cited abstract (polished + submission)
   - numeric_grounding: per-section numeric-grounded paragraph ratio
   - kill_argument: adversarial review (submission only)

2. **schema + writing audits** (NEW)
   - `tools/validate_artifacts.py` — thesis schema + claims schema +
     cite_key closed-set + _decision_summary
   - `tools/audit_writing.py` — 5-anchor argument-skeleton +
     4-pillar narrative scaffolding + thesis coherence (abstract +
     conclusion restate thesis; argument_steps coverage) +
     Open-Problems 4-bucket + claim-grounding (sentence-vs-(abstract ∪
     atomic_claims.quote))

`verify_survey_audits.sh` runs all five gates and returns non-zero
if any fail at the chosen assurance level. `submission` is the
strictest:

| audit | draft | polished | submission |
|---|---|---|---|
| hard_gate (phantom cites) | block | block | block |
| claim_audit | skip | block | block |
| kill_argument | skip | skip | block |
| numeric_grounding | info | info | block on FAIL |
| evidence_fulltext (quotes + numbers vs full text) | info | warn | block on FAIL |
| trend_audit | skip | block on FAIL | block on FAIL+WARN |
| **validate_artifacts** | warn | warn | block on ERROR |
| **audit_writing** | warn | warn | block when score < 0.9 |

The audits run AFTER the existing CITATION_VERIFY checks. The
agent generates CITATION_VERIFY.json during this skill's execution
(the agent layer is responsible for the LLM-driven claim_audit and
kill_argument; the deterministic checks run from `verify_survey_audits.sh`).

---

## Invocation

```
/survey-verify [--run-id <id>]
```

All audits run at the strictest level (`submission`); there is no flag to relax them.

---

## Prerequisites

- `5_paper/sections/*.tex` must exist
- `1_search/filtered.jsonl` must exist with `cite_key` field
- `5_paper/references.bib` must exist
- State: `review.status == "completed"` or `"skipped"` in `state.json`

---

## Step 1 — Hard Gate (always, all assurance levels)

**This step is non-negotiable. It always runs. Any failure immediately blocks compile.**

### 1a — Extract all cite keys used in sections

```python
import re
from pathlib import Path

def extract_cite_keys(sections_dir: str) -> dict:
    """Returns {section_id: [(cite_key, context_sentence), ...]}"""
    result = {}
    for tex_file in sorted(Path(sections_dir).glob("*.tex")):
        section_id = tex_file.stem
        text = tex_file.read_text()
        # Extract sentences containing \cite{...}
        # Simple heuristic: sentence ends at .?! or newline
        triples = []
        for match in re.finditer(r'\\cite\{([^}]+)\}', text):
            keys_raw = match.group(1)
            for key in keys_raw.split(','):
                key = key.strip()
                # Get surrounding context (up to 200 chars)
                start = max(0, match.start() - 100)
                end = min(len(text), match.end() + 100)
                ctx = text[start:end].replace('\n', ' ').strip()
                triples.append((key, ctx))
        result[section_id] = triples
    return result
```

### 1b — Load allowed cite keys

```python
import json
cite_keys_map = json.loads(Path("5_paper/references.cite_keys.json").read_text())
allowed_keys = set(cite_keys_map.values())
```

### 1c — Check for phantom keys

```python
all_used = {} # key → list of sections where used
for section_id, triples in section_cite_data.items():
    for key, ctx in triples:
        all_used.setdefault(key, []).append(section_id)

phantom_keys = [k for k in all_used if k not in allowed_keys]
```

If `phantom_keys` is non-empty:
```
❌ HARD GATE FAILED — phantom citation keys detected:
   {key1} — used in sections: {section_list}
   {key2} — used in sections: {section_list}
   ...

These keys do not appear in filtered.jsonl. They must be removed before compile.

To fix: open the affected .tex files and either:
  a) Replace with a valid cite key from references.bib, OR
  b) Remove the \cite{} and rewrite the claim without citing

After fixing, re-run /survey-verify.
```

**STOP. Write `CITATION_VERIFY.json` with `"hard_gate": "FAIL"` and exit.**
The compile gate (`verify_survey_audits.sh`) will block the compile stage.

If no phantom keys → `hard_gate = PASS`. Continue to Step 2.

---

## Step 2 — Coverage Score (always)

For each section in `outline.json`:
```python
section_pool = set(section["primary_papers"] + section["secondary_papers"])
# Map paper_ids to cite_keys
section_keys = {cite_keys_map[pid] for pid in section_pool if pid in cite_keys_map}

used_in_section = {key for key, _ in section_cite_data.get(section_id, [])}
coverage = len(used_in_section & section_keys) / len(section_keys) if section_keys else 1.0
```

Log per-section coverage scores.
If any section (except Intro/Conclusion/Open Problems) has coverage < 0.6 → WARN.

---

## Step 3 — Soft Claim Audit (assurance ≥ `polished`)

For each `(section_id, cite_key, context_sentence)` triple:
1. Look up the cited paper's abstract from `filtered.jsonl`
2. Open a fresh LLM thread (see reviewer-independence.md)

```
System: You are an independent fact-checker for a survey paper.

User: The following sentence appears in a survey paper:
"{context_sentence}"

It cites this paper:
Title: {cited_paper_title}
Abstract: {cited_paper_abstract}

Does the sentence accurately represent what the cited paper contributes?

Respond with ONLY one of:
- KEEP: the claim is supported by the abstract
- SOFTEN: the claim overstates; suggest a weaker phrasing: "{suggestion}"
- REMOVE: the claim is not supported by this paper; no citation should be used here
```

**Process in batches of 20 triples per LLM call** to reduce API calls.

Apply verdicts:
- `KEEP` → no change
- `SOFTEN` → replace the sentence with the suggested weaker phrasing; log WARN
- `REMOVE` → strip `\cite{key}` from sentence, rewrite without citation; log WARN

Save all verdicts to `6_verify/claim_audit.json`.

Compute overall claim_audit verdict:
- All `KEEP` → `PASS`
- ≥1 `SOFTEN` applied → `WARN`
- ≥5 `REMOVE` applied (>10% of audited citations) → `FAIL`

---

## Step 3.5 — Numeric Grounding Audit (NEW)

This audit measures whether body sections actually use the per-paper detail
cards (numeric specifics, exact hyperparameters) when citing, or whether
they're pure narration. **It is informational at all assurance levels
except for one extreme-case gate at submission FAIL.**

The audit exists to surface "is this Survey mostly narrating, or actually
using card data?" — without forcing every paragraph into a number-dump that
would ruin prose flow. Detail-driven discipline lives in (a) the
comparison tables, (b) brief.Style + cards in the write prompt, and (c)
review's depth axis. This audit is just a regression detector for the
truly broken case where a section ends up with zero detail use.

### Procedure

1. **Split each body section** into paragraphs (blank-line delimited).
2. **Identify cite-containing paragraphs** — those with ≥1 `\cite` /
   `\citet` / `\citep` macro.
3. **For each cite-paragraph**, check whether ANY substring from the
   cited papers' `cards.jsonl` schema field values is referenced
   (case-insensitive substring match; numbers match on digit sequence
   with common unit variants — "16B" / "16 billion" / "16,000,000,000"
   all considered equivalent).
4. **Compute per-section grounding ratio** =
   `grounded_count / cite_paragraph_count`.
5. **Intro / Open Problems / Conclusion are EXEMPT** (their job is
   narrative, not detail-driven comparison).

### Verdicts (intentionally lenient)

- **PASS** — every body section with ≥3 cite paragraphs has at least 1
  grounded paragraph (no body section is "all narrative, zero detail").
- **WARN** — some body section with ≥3 cite paragraphs has a low ratio
  (< 0.30) but still ≥1 grounded paragraph.
- **FAIL** — some body section has ≥3 cite paragraphs and **zero**
  grounded paragraphs (the pure-fluff extreme case).
- **NOT_APPLICABLE** — no `cards.jsonl` produced (extract stage
  skipped/failed) OR every body section has <3 cite paragraphs.

### Implementation sketch

```python
import json, re, unicodedata
from pathlib import Path

cards = {}
cards_path = Path(RUN_DIR) / "1_search" / "cards.jsonl"
if not cards_path.exists():
    numeric_grounding = "NOT_APPLICABLE"
else:
    for line in cards_path.read_text().splitlines():
        if line.strip():
            c = json.loads(line)
            cards[c.get("cite_key") or c.get("paper_id")] = c

NUMERIC_RE = re.compile(r"\d[\d,\.]*\s*[KMBkmb]?", re.IGNORECASE)
EXEMPT = {"intro", "introduction", "open_problems", "conclusion"}

def number_variants(s):
    """Generate matchable variants for '16B' / '16 billion' / '16000000000'."""
    # ... see spec §4.8 for full details

def is_grounded(paragraph, cited_keys):
    # Pull every short-string / numeric value from each cited card,
    # case-insensitive substring match into the paragraph
    ...

ratios, fluff, ungrounded = {}, [], []
for sec_id, paras in body_sections.items():
    if sec_id.lower() in EXEMPT:
        continue
    cite_paras = [p for p in paras if "\\cite" in p]
    if len(cite_paras) < 3:
        continue
    grounded = sum(1 for p in cite_paras if is_grounded(p, extract_cites(p)))
    ratios[sec_id] = grounded / len(cite_paras)
    if grounded == 0:
        fluff.append(sec_id)
    for idx, p in enumerate(cite_paras):
        if not is_grounded(p, extract_cites(p)):
            ungrounded.append({"section": sec_id, "para_idx": idx,
                               "first_sentence": p.split(".")[0][:120]})

if not ratios:
    numeric_grounding = "NOT_APPLICABLE"
elif fluff:
    numeric_grounding = "FAIL"
elif any(r < 0.30 for r in ratios.values()):
    numeric_grounding = "WARN"
else:
    numeric_grounding = "PASS"
```

### Compile gate behaviour

- `assurance == draft`: never blocks on `numeric_grounding`.
- `assurance == polished`: never blocks on `numeric_grounding`. WARN/FAIL
  reported in the run summary as informational.
- `assurance == submission`: blocks compile only on
  `numeric_grounding == FAIL` (the pathological case). WARN does not block.

This gating is implemented in `tools/verify_survey_audits.sh` (see that
file for the exact case statement).

---

## Step 3.6 — Full-text evidence verification (NEW)

Step 3.5 (and audit_writing's claim-grounding) check prose against the
**abstract** plus the agent's **self-mined quotes** — but nothing verifies
those quotes are real or that an exact benchmark number actually appears in
the source. This step closes both holes using the full text the pipeline
already fetched into `1_search/.cache/<cite_key>.txt`
(`extract_paper_card.py --fetch-all`).

```bash
python3 "$AUTOSURVEY_TOOLS/verify_evidence.py" "$RUN_DIR" \
    --report "$RUN_DIR/6_verify/evidence_fulltext.json"
# add --strict at submission so it returns non-zero on FAIL
```

It reports three things:
- **quote verification** — each `atomic_claims[].quote` must appear verbatim
  or near-verbatim in the cited paper's source text. An unverified quote is a
  likely hallucination and must not be used to ground a numeric claim.
- **numeric grounding vs full text** — every quantitative number in a
  numeric+cited sentence must appear in *some* cited paper's full text
  (years and small structural integers are ignored). A number found in no
  cited source (`unsourced_examples`) is fabricated or mis-attributed — fix
  the number or the citation.
- **full_text_coverage** — share of cited keys with cached full text. If this
  is low, run `extract_paper_card.py --fetch-all` first; otherwise the check
  silently falls back to abstracts and runs at reduced strength (still
  catches hallucinated quotes, but can't confirm numbers absent from the
  abstract).

Verdicts: **block on FAIL at submission** (quote-verified < 0.80 or
numeric-grounded < 0.70), **warn at polished**, **info at draft**. This is the
evidence-fidelity gate: it turns "topically plausible" into "verifiably
stated in the source."

---

## Step 3.7 — Trend-claim audit (assurance ≥ `polished`)

Specifically audits forward-looking claims in the Trends & Trajectories
section. Distinct from the general claim audit (Step 3, which checks each
cite against the cited paper's abstract), this step checks
**claim-vs-data alignment** for trend prose by reading `cards.jsonl`.

Skip entirely if
`brief.parsed.json.configuration.trends_section == "skip"` — emit
`trend_audit = NOT_APPLICABLE`.

### Procedure

For each forward-looking claim sentence in the Trends section:

1. **Extract the claim sentence.**
2. **Identify cited cite_keys** from `\cite{...}` / `\citet{...}` /
   `\citep{...}` macros within the sentence.
3. **Identify the numeric / categorical anchor** — the assertion's
   specific data point: a number, a region, a year, a model name.
4. **Look up cited papers in `cards.jsonl`.**
5. **Verdict per claim:**
   - **KEEP** — the anchor matches at least one cited card's data
     (numeric match with unit-variant tolerance, or categorical/string
     substring match).
   - **SOFTEN** — the anchor is plausible but not directly supported by
     the cited cards; suggest weaker phrasing (e.g. "may suggest" instead
     of "indicates").
   - **REMOVE** — the anchor contradicts the cited cards; the claim must
     be deleted.

### Apply verdicts

- `KEEP` → no change.
- `SOFTEN` → rewrite the claim with hedged phrasing; log WARN with
  `{sentence, anchor, reason}`.
- `REMOVE` → delete the claim sentence; log WARN with
  `{sentence, anchor, reason}`.

### Aggregate `trend_audit` verdict

- All `KEEP` → `PASS`
- ≥1 `SOFTEN` applied → `WARN`
- ≥3 `REMOVE` applied → `FAIL`
- `trends_section == "skip"` or no Trends section present →
  `NOT_APPLICABLE`

### Recorded fields in `CITATION_VERIFY.json`

```json
{
  "...": "...",
  "trend_audit": "PASS|WARN|FAIL|NOT_APPLICABLE",
  "trend_verdicts": {"KEEP": 0, "SOFTEN": 0, "REMOVE": 0},
  "trend_unverified_claims": [
    {"sentence": "...", "anchor": "...", "reason": "..."}
  ]
}
```

### Compile gate behaviour (Trend-claim audit)

- `assurance == draft`: never blocks.
- `assurance == polished`: blocks only on `trend_audit == FAIL`.
- `assurance == submission`: blocks on `trend_audit ∈ {WARN, FAIL}`.

This gating is implemented in `tools/verify_survey_audits.sh` (mirrors
the `numeric_grounding` gate pattern).

---

## Step 4 — Adversarial Kill-Argument Review (assurance `submission` only)

Open a fresh LLM thread with adversarial persona.

```
System: You are an adversarial reviewer trying to reject this survey paper.
        Your goal is to find the strongest arguments AGAINST publishing this survey.

User: Read the following survey paper and answer these three questions:

Files to read:
- Survey sections: {RUN_DIR}/5_paper/sections/
- Taxonomy: {RUN_DIR}/3_taxonomy.json
- Paper corpus: {RUN_DIR}/1_search/filtered.jsonl

Questions:
1. MISSING COVERAGE: What important subtopics in "{topic}" are completely absent from this survey?
   For each gap, estimate how significant it is (High/Medium/Low).

2. INCORRECT/OVERSIMPLIFIED CLAIMS: What claims in this survey are likely wrong, imprecise,
   or oversimplified? Cite the section and offending sentence.

3. SELECTION BIAS: What systematic bias exists in the paper coverage?
   (e.g., over-represents one research group, ignores non-English work, misses a key venue)

Output valid JSON:
{
  "missing_coverage": [{"subtopic": "...", "significance": "High|Med|Low", "evidence": "..."}],
  "incorrect_claims": [{"section": "...", "sentence": "...", "issue": "..."}],
  "selection_bias": [{"type": "...", "description": "...", "evidence": "..."}]
}
```

Save to `6_verify/kill_argument.json` and `6_verify/kill_argument.md`.

### Executor addresses each finding

For each HIGH-significance finding:
- **Missing coverage**: add a paragraph to the most relevant section OR add to Open Problems
  with a note "This aspect is outside the scope of the current survey due to [reason]"
- **Incorrect claim**: fix or soften the claim (same process as claim audit SOFTEN)
- **Selection bias**: add a "Limitations" paragraph to the Conclusion acknowledging the bias

For MEDIUM findings: address or explicitly scope-out with a one-sentence note.
For LOW findings: log as acknowledged, no action required.

After addressing: update `kill_argument.json`:
```json
{
  "status": "ADDRESSED",
  "items_addressed": 3,
  "items_scoped_out": 1,
  "items_low_priority": 2
}
```

Set `kill_argument` verdict:
- All HIGH+MED addressed or scoped-out → `ADDRESSED`
- Any HIGH unresolved → `PENDING` (blocks compile at submission assurance)

---

## Step 5 — Emit CITATION_VERIFY.json

```json
{
  "hard_gate": "PASS|FAIL",
  "coverage": "PASS|WARN",
  "claim_audit": "PASS|WARN|FAIL|NOT_APPLICABLE",
  "numeric_grounding": "PASS|WARN|FAIL|NOT_APPLICABLE",
  "trend_audit": "PASS|WARN|FAIL|NOT_APPLICABLE",
  "trend_verdicts": {"KEEP": 0, "SOFTEN": 0, "REMOVE": 0},
  "trend_unverified_claims": [],
  "kill_argument": "ADDRESSED|PENDING|NOT_APPLICABLE",
  "coverage_scores": {
    "attention_mechanisms": 0.92,
    "sparse_attention": 0.67,
    ...
  },
  "grounding_ratios": {
    "attention_mechanisms": 0.83,
    "sparse_attention": 0.40,
    ...
  },
  "fluff_sections": [],
  "ungrounded_paragraphs": [
    {"section": "sparse_attention", "para_idx": 3, "first_sentence": "Several recent works extend ..."}
  ],
  "phantom_keys": [],
  "total_citations_audited": 0,
  "claim_verdicts": {"KEEP": 0, "SOFTEN": 0, "REMOVE": 0},
  "generated_at": "2026-...",
  "assurance_level": "draft|polished|submission",
  "run_id": "..."
}
```

Save to `6_verify/CITATION_VERIFY.json`.

---

## Step 6 — Run compile gate check

```bash
AUTOSURVEY_TOOLS="${AUTOSURVEY_TOOLS:-$(git rev-parse --show-toplevel 2>/dev/null)/tools}"
VERIFIER="$AUTOSURVEY_TOOLS/verify_survey_audits.sh"

if [ -f "$VERIFIER" ]; then
    bash "$VERIFIER" "$RUN_DIR" --assurance "$ASSURANCE"
else
    echo "WARN: verify_survey_audits.sh not found — performing inline check" >&2
    # Inline check: read CITATION_VERIFY.json and verify hard_gate == "PASS"
    python3 -c "
import json, sys
data = json.load(open('$RUN_DIR/6_verify/CITATION_VERIFY.json'))
if data['hard_gate'] != 'PASS':
    print('FAIL: hard_gate =', data['hard_gate'], file=sys.stderr)
    sys.exit(1)
print('PASS: hard_gate verified inline')
"
fi
```

---

## Update state.json

```json
{
  "stages": {
    "verify": {
      "status": "completed",
      "hard_gate": "PASS",
      "coverage": "WARN",
      "claim_audit": "WARN",
      "numeric_grounding": "PASS",
      "kill_argument": "NOT_APPLICABLE",
      "assurance": "draft"
    }
  }
}
```

---

## Report

```
✅ Verification complete (assurance=polished)

📋 Audit results:
   [PASS] Hard gate — 0 phantom keys, 312 valid citations
   [WARN] Coverage — 1 section below 60% (retrieval_methods: 0.50)
   [WARN] Claim audit — 4 softened, 1 removed (of 312 audited)
   [PASS] Numeric grounding — every body section has ≥1 grounded cite paragraph
   [N/A] Kill-argument — not run at polished assurance

   Coverage scores: attention_mechanisms: 0.92 | sparse_attention: 0.67 | ...
   Grounding ratios: attention_mechanisms: 0.83 | sparse_attention: 0.40 | ...

✅ Compile gate: PASS — compile stage may proceed.

Next: tectonic / latexmk compile (handled automatically by /survey-run)
```

---

## Output Files

| File | Contents |
|---|---|
| `6_verify/CITATION_VERIFY.json` | Machine-readable audit verdicts (incl. numeric_grounding) |
| `6_verify/claim_audit.json` | Per-triple claim audit verdicts |
| `6_verify/kill_argument.json` | Adversarial review findings + resolution status |
| `6_verify/kill_argument.md` | Human-readable adversarial review |
| `state.json` | Updated verify completion |

---

## Error Conditions

| Condition | Action |
|---|---|
| `hard_gate = FAIL` | STOP immediately. List phantom keys. Block compile. |
| `claim_audit` still FAIL after 2 fix passes | STOP. Report unresolved citations. |
| `numeric_grounding = FAIL` at submission | STOP. List `fluff_sections` — body section is pure narrative. |
| `numeric_grounding = FAIL` at draft / polished | INFO only; compile proceeds. |
| `trend_audit = FAIL` at polished or submission | STOP. List `trend_unverified_claims` — Trends section claims contradict cards. |
| `trend_audit = WARN` at submission | STOP. SOFTEN-applied claims need review at submission assurance. |
| `kill_argument = PENDING` at submission | STOP. List unresolved HIGH findings. |
| `6_verify/CITATION_VERIFY.json` missing | compile gate will fail (requires this file) |
