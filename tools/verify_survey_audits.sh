#!/usr/bin/env bash
# verify_survey_audits.sh — Compile gate for AutoSurvey.
# Checks CITATION_VERIFY.json before allowing paper-compile to run.
#
# Usage:
#   bash verify_survey_audits.sh <run-dir> [--assurance draft|polished|submission]
#
# Exit codes:
#   0  — all required verdicts green, compile may proceed
#   1  — one or more blocking verdicts or missing file
#
# Called by survey-run Stage 6 before invoking /paper-compile.

set -euo pipefail

RUN_DIR="${1:-}"
ASSURANCE="${2:-}"  # optional override; if not provided, read from CITATION_VERIFY.json

if [ -z "$RUN_DIR" ]; then
    echo "ERROR: usage: verify_survey_audits.sh <run-dir> [--assurance draft|polished|submission]" >&2
    exit 1
fi
if [ ! -d "$RUN_DIR" ]; then
    echo "ERROR: run directory not found: $RUN_DIR" >&2
    exit 1
fi

# Parse --assurance flag if given as second arg
if [ "$ASSURANCE" = "--assurance" ] && [ -n "${3:-}" ]; then
    ASSURANCE="$3"
elif [[ "$ASSURANCE" == --assurance=* ]]; then
    ASSURANCE="${ASSURANCE#--assurance=}"
fi

VERIFY_JSON="$RUN_DIR/6_verify/CITATION_VERIFY.json"

if [ ! -f "$VERIFY_JSON" ]; then
    echo "FAIL: CITATION_VERIFY.json not found at $VERIFY_JSON" >&2
    echo "      Run /survey-verify before attempting compile." >&2
    exit 1
fi

# ── parse JSON fields with python3 ────────────────────────────────────────
read_field() {
    python3 -c "
import json, sys
data = json.load(open('$VERIFY_JSON'))
print(data.get('$1', 'MISSING'))
"
}

HARD_GATE=$(read_field "hard_gate")
CLAIM_AUDIT=$(read_field "claim_audit")
NUMERIC_GROUNDING=$(read_field "numeric_grounding")
TREND_AUDIT=$(read_field "trend_audit")
KILL_ARG=$(read_field "kill_argument")
ASSURANCE_IN_FILE=$(read_field "assurance_level")
PHANTOM_KEYS=$(python3 -c "
import json
data = json.load(open('$VERIFY_JSON'))
keys = data.get('phantom_keys', [])
print(', '.join(keys) if keys else '')
")

# Default to the strictest level. The CITATION_VERIFY.json file may carry
# a softer `assurance_level` for diagnostic reasons; this gate ignores it
# unless the caller explicitly asks for a softer pass via --assurance.
if [ -z "$ASSURANCE" ]; then
    ASSURANCE="submission"
fi

FAIL=0
REPORT=""

# ── Check 1: hard gate (always) ────────────────────────────────────────────
if [ "$HARD_GATE" != "PASS" ]; then
    REPORT="$REPORT\n  [FAIL] hard_gate = $HARD_GATE"
    if [ -n "$PHANTOM_KEYS" ]; then
        REPORT="$REPORT (phantom keys: $PHANTOM_KEYS)"
    fi
    FAIL=1
else
    REPORT="$REPORT\n  [PASS] hard_gate = PASS"
fi

# ── Check 2: claim audit (polished + submission) ───────────────────────────
if [ "$ASSURANCE" = "polished" ] || [ "$ASSURANCE" = "submission" ]; then
    case "$CLAIM_AUDIT" in
        PASS|WARN|NOT_APPLICABLE)
            REPORT="$REPORT\n  [PASS] claim_audit = $CLAIM_AUDIT" ;;
        FAIL|BLOCKED|ERROR)
            REPORT="$REPORT\n  [FAIL] claim_audit = $CLAIM_AUDIT"
            FAIL=1 ;;
        *)
            REPORT="$REPORT\n  [WARN] claim_audit = $CLAIM_AUDIT (unexpected value)" ;;
    esac
else
    REPORT="$REPORT\n  [SKIP] claim_audit (assurance=$ASSURANCE)"
fi

# ── Check 3: kill-argument (submission only) ──────────────────────────────
if [ "$ASSURANCE" = "submission" ]; then
    case "$KILL_ARG" in
        ADDRESSED|NOT_APPLICABLE)
            REPORT="$REPORT\n  [PASS] kill_argument = $KILL_ARG" ;;
        PENDING)
            REPORT="$REPORT\n  [FAIL] kill_argument = PENDING (adversarial issues unresolved)"
            FAIL=1 ;;
        *)
            REPORT="$REPORT\n  [WARN] kill_argument = $KILL_ARG (unexpected value)" ;;
    esac
else
    REPORT="$REPORT\n  [SKIP] kill_argument (assurance=$ASSURANCE)"
fi

# ── Check 4: numeric_grounding (informational; only blocks at submission FAIL) ──
case "$ASSURANCE" in
    draft|polished)
        # Informational only; never block
        case "$NUMERIC_GROUNDING" in
            PASS|NOT_APPLICABLE)
                REPORT="$REPORT\n  [PASS] numeric_grounding = $NUMERIC_GROUNDING" ;;
            WARN)
                REPORT="$REPORT\n  [WARN] numeric_grounding = WARN (some body section has low grounding ratio)" ;;
            FAIL)
                REPORT="$REPORT\n  [INFO] numeric_grounding = FAIL (not blocking at $ASSURANCE)" ;;
            *)
                REPORT="$REPORT\n  [WARN] numeric_grounding = $NUMERIC_GROUNDING (unexpected value)" ;;
        esac
        ;;
    submission)
        case "$NUMERIC_GROUNDING" in
            PASS|WARN|NOT_APPLICABLE)
                REPORT="$REPORT\n  [PASS] numeric_grounding = $NUMERIC_GROUNDING" ;;
            FAIL)
                REPORT="$REPORT\n  [FAIL] numeric_grounding = FAIL (a body section has zero grounded paragraphs)"
                FAIL=1 ;;
            *)
                REPORT="$REPORT\n  [WARN] numeric_grounding = $NUMERIC_GROUNDING (unexpected value)" ;;
        esac
        ;;
esac

# ── Check 5: trend_audit (Trends & Trajectories claim-vs-card alignment) ──
# Blocks: polished+submission on FAIL; submission additionally on WARN.
case "$ASSURANCE" in
    draft)
        case "$TREND_AUDIT" in
            PASS|WARN|NOT_APPLICABLE|MISSING)
                REPORT="$REPORT\n  [SKIP] trend_audit = $TREND_AUDIT (assurance=draft)" ;;
            FAIL)
                REPORT="$REPORT\n  [INFO] trend_audit = FAIL (not blocking at draft)" ;;
            *)
                REPORT="$REPORT\n  [WARN] trend_audit = $TREND_AUDIT (unexpected value)" ;;
        esac
        ;;
    polished)
        case "$TREND_AUDIT" in
            PASS|WARN|NOT_APPLICABLE|MISSING)
                REPORT="$REPORT\n  [PASS] trend_audit = $TREND_AUDIT" ;;
            FAIL)
                REPORT="$REPORT\n  [FAIL] trend_audit = FAIL (Trends claims contradict cards.jsonl)"
                FAIL=1 ;;
            *)
                REPORT="$REPORT\n  [WARN] trend_audit = $TREND_AUDIT (unexpected value)" ;;
        esac
        ;;
    submission)
        case "$TREND_AUDIT" in
            PASS|NOT_APPLICABLE|MISSING)
                REPORT="$REPORT\n  [PASS] trend_audit = $TREND_AUDIT" ;;
            WARN)
                REPORT="$REPORT\n  [FAIL] trend_audit = WARN (SOFTEN-applied claims need review at submission)"
                FAIL=1 ;;
            FAIL)
                REPORT="$REPORT\n  [FAIL] trend_audit = FAIL (Trends claims contradict cards.jsonl)"
                FAIL=1 ;;
            *)
                REPORT="$REPORT\n  [WARN] trend_audit = $TREND_AUDIT (unexpected value)" ;;
        esac
        ;;
esac

# ── Output ────────────────────────────────────────────────────────────────
echo ""
echo "=== AutoSurvey Compile Gate (assurance=$ASSURANCE) ==="
printf "%b\n" "$REPORT"
echo ""

# ── Schema + writing audits (post-CITATION_VERIFY) ───────────────────────
# validate_artifacts.py — schema-class checks (thesis, claims, cite-key
#                          closed-set, _decision_summary)
# audit_writing.py        — writing-class checks (5 anchor + narrative pillars
#                          + thesis coherence + Open-Problems 4-bucket
#                          + claim-grounding upgrade)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/validate_artifacts.py" ]; then
    echo "=== validate_artifacts ==="
    if ! python3 "$SCRIPT_DIR/validate_artifacts.py" "$RUN_DIR"; then
        if [ "$ASSURANCE" = "submission" ]; then
            FAIL=1
            echo "  [FAIL] validate_artifacts at submission level"
        else
            echo "  [WARN] validate_artifacts findings (not blocking at $ASSURANCE)"
        fi
    fi
    echo ""
fi

if [ -f "$SCRIPT_DIR/audit_writing.py" ]; then
    echo "=== audit_writing ==="
    if ! python3 "$SCRIPT_DIR/audit_writing.py" "$RUN_DIR" --assurance "$ASSURANCE"; then
        if [ "$ASSURANCE" = "submission" ]; then
            FAIL=1
            echo "  [FAIL] audit_writing at submission level"
        else
            echo "  [WARN] audit_writing findings (not blocking at $ASSURANCE)"
        fi
    fi
    echo ""
fi

if [ "$FAIL" -eq 1 ]; then
    echo "❌ COMPILE BLOCKED — resolve the FAIL items above, then re-run /survey-verify." >&2
    echo "   Run /survey-verify to regenerate CITATION_VERIFY.json after fixes." >&2
    exit 1
else
    echo "✅ All checks green — paper-compile may proceed."
    exit 0
fi
