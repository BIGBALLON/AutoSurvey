#!/usr/bin/env bash
# fetch_venue_template.sh — download official venue style files into the run.
#
# Usage:
#   fetch_venue_template.sh <run_dir> [venue]
#
# Supported venues (default: neurips):
#   neurips      → downloads neurips_2024.sty from media.neurips.cc
#   iclr         → downloads iclr2024_conference.sty from openreview
#   icml         → downloads icml2024.sty from icml.cc
#   acl          → downloads acl.sty from aclweb.org
#
# Style files are placed in <run_dir>/5_paper/ next to main.tex so LaTeX picks
# them up automatically (no system-level install needed).

set -e

RUN_DIR="$1"
VENUE="${2:-neurips}"

if [ -z "$RUN_DIR" ] || [ ! -d "$RUN_DIR" ]; then
    echo "Usage: $0 <run_dir> [neurips|iclr|icml|acl]" >&2
    exit 1
fi

PAPER_DIR="$RUN_DIR/5_paper"
mkdir -p "$PAPER_DIR"

case "$VENUE" in
    neurips)
        # Try local cache first (shipped in skills/survey-write/templates/)
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
        LOCAL="$SCRIPT_DIR/../skills/survey-write/templates/neurips_2024.sty"
        if [ -f "$LOCAL" ]; then
            cp "$LOCAL" "$PAPER_DIR/neurips_2024.sty"
            echo "✅ Copied neurips_2024.sty (local cache) → $PAPER_DIR/"
            exit 0
        fi
        # Otherwise download
        TMP=$(mktemp -d)
        echo "Fetching NeurIPS 2024 styles from media.neurips.cc..."
        curl -sL "https://media.neurips.cc/Conferences/NeurIPS2024/Styles.zip" -o "$TMP/styles.zip"
        unzip -q -o "$TMP/styles.zip" -d "$TMP"
        cp "$TMP/Styles/neurips_2024.sty" "$PAPER_DIR/"
        echo "✅ Downloaded neurips_2024.sty → $PAPER_DIR/"
        ;;
    iclr)
        echo "ICLR template fetcher not yet implemented." >&2
        echo "Download manually from openreview.net and place in $PAPER_DIR/" >&2
        exit 2
        ;;
    icml)
        echo "ICML template fetcher not yet implemented." >&2
        exit 2
        ;;
    acl)
        echo "ACL template fetcher not yet implemented." >&2
        exit 2
        ;;
    *)
        echo "Unknown venue: $VENUE" >&2
        echo "Supported: neurips, iclr (manual), icml (manual), acl (manual)" >&2
        exit 1
        ;;
esac
