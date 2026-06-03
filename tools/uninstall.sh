#!/usr/bin/env bash
# AutoSurvey skill-pack uninstaller.
#
# Removes only the symlinks created by tools/install.sh:
#   ~/.claude/skills/survey-*  →  <repo>/skills/survey-*
#   ~/.codex/skills/survey-*   →  <repo>/skills/survey-*
#   ~/.cursor/skills/survey-*  →  <repo>/skills/survey-*
#
# Leaves the AUTOSURVEY_TOOLS export in your shell profile alone (you may
# still want it). Remove it manually from your profile (e.g. ~/.bashrc on
# Linux, ~/.zshrc on macOS-with-zsh, ~/.bash_profile on macOS-with-bash) if
# you want it gone too.
#
# Usage:
#   bash tools/uninstall.sh             # remove from all agents
#   bash tools/uninstall.sh --dry-run   # print what would be removed

set -euo pipefail

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)  DRY_RUN=1 ;;
    -h|--help)  sed -n '2,16p' "$0"; exit 0 ;;
    *)          echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'DRY-RUN  %s\n' "$*"
  else
    "$@"
  fi
}

removed_total=0

remove_for_agent() {
  local agent_name="$1"
  local target_dir="$2"

  if [ ! -d "$target_dir" ]; then
    echo "[$agent_name] skipped — $target_dir does not exist"
    return 0
  fi

  local removed=0
  for entry in "$target_dir"/survey-*; do
    [ -L "$entry" ] || continue           # only touch symlinks we created
    run rm "$entry"
    echo "[$agent_name] removed $entry"
    removed=$((removed + 1))
  done
  echo "[$agent_name] removed $removed link(s)"
  removed_total=$((removed_total + removed))
}

remove_for_agent claude "$HOME/.claude/skills"
remove_for_agent codex  "$HOME/.codex/skills"
remove_for_agent cursor "$HOME/.cursor/skills"

cat <<EOF

Uninstall complete. $removed_total symlink(s) removed.

The AUTOSURVEY_TOOLS export in your shell profile (if any) is left in place.
To remove it, edit your shell profile and delete the line that starts with:
    export AUTOSURVEY_TOOLS=
Common locations:  ~/.bashrc  ~/.zshrc  ~/.bash_profile  ~/.profile
EOF
