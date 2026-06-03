#!/usr/bin/env bash
# AutoSurvey skill-pack installer.
#
# What it does:
#   1. Symlinks every <repo>/skills/<name> directory into your agent's
#      user-level skills dir:
#        Claude Code → ~/.claude/skills/<name>
#        Codex CLI   → ~/.codex/skills/<name>
#        Cursor      → ~/.cursor/skills/<name>
#   2. Pins AUTOSURVEY_TOOLS in your shell profile so every skill can find
#      <repo>/tools/ from any working directory.
#
# Why a symlink (not a copy)? Editing or `git pull`-ing the repo updates
# your skills automatically.
#
# Idempotent: re-running replaces stale symlinks and skips already-pinned
# environment exports. Refuses to clobber a real (non-symlink) file.
#
# Usage:
#   bash tools/install.sh                # install for whichever agents are present
#   bash tools/install.sh --claude-only  # only ~/.claude/skills
#   bash tools/install.sh --codex-only   # only ~/.codex/skills
#   bash tools/install.sh --cursor-only  # only ~/.cursor/skills
#   bash tools/install.sh --dry-run      # print what would be done

set -euo pipefail

# ------------------------------------------------------------------ resolve repo
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOLS_DIR="$REPO_ROOT/tools"
SKILLS_DIR="$REPO_ROOT/skills"

if [ ! -d "$SKILLS_DIR" ] || [ ! -d "$TOOLS_DIR" ]; then
  echo "error: $REPO_ROOT does not look like an AutoSurvey checkout" >&2
  echo "       (missing skills/ or tools/ next to install.sh)" >&2
  exit 1
fi

# ------------------------------------------------------------------ flag parsing
INSTALL_CLAUDE=1
INSTALL_CODEX=1
INSTALL_CURSOR=1
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --claude-only) INSTALL_CODEX=0; INSTALL_CURSOR=0 ;;
    --codex-only)  INSTALL_CLAUDE=0; INSTALL_CURSOR=0 ;;
    --cursor-only) INSTALL_CLAUDE=0; INSTALL_CODEX=0 ;;
    --dry-run)     DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,24p' "$0"
      exit 0
      ;;
    *)
      echo "unknown flag: $arg" >&2
      exit 2
      ;;
  esac
done

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'DRY-RUN  %s\n' "$*"
  else
    "$@"
  fi
}

# ------------------------------------------------------------------ link skills
link_for_agent() {
  local agent_name="$1"
  local target_dir="$2"          # …/.claude/skills or …/.codex/skills
  local agent_root
  agent_root="$(dirname "$target_dir")"

  if [ ! -d "$agent_root" ]; then
    echo "[$agent_name] skipped — $agent_root does not exist (agent not installed?)"
    return 0
  fi

  echo "[$agent_name] linking skills into $target_dir"
  run mkdir -p "$target_dir"

  local linked=0 skipped=0
  for skill_dir in "$SKILLS_DIR"/survey-*; do
    [ -d "$skill_dir" ] || continue
    local name="$(basename "$skill_dir")"
    local dst="$target_dir/$name"

    # Refuse to clobber a real directory; replace stale symlinks.
    if [ -e "$dst" ] && [ ! -L "$dst" ]; then
      echo "  refuse $dst — exists and is not a symlink (delete it manually if you want to overwrite)"
      skipped=$((skipped + 1))
      continue
    fi
    [ -L "$dst" ] && run rm "$dst"
    run ln -s "$skill_dir" "$dst"
    echo "  /$name → $skill_dir"
    linked=$((linked + 1))
  done
  echo "[$agent_name] linked $linked skill(s)${skipped:+, skipped $skipped}"
}

[ "$INSTALL_CLAUDE" -eq 1 ] && link_for_agent claude "$HOME/.claude/skills"
[ "$INSTALL_CODEX"  -eq 1 ] && link_for_agent codex  "$HOME/.codex/skills"
[ "$INSTALL_CURSOR" -eq 1 ] && link_for_agent cursor "$HOME/.cursor/skills"

# ------------------------------------------------------------------ pin env var
#
# Shell-profile detection follows what users actually have on Linux + macOS:
#   bash on Linux: ~/.bashrc is sourced for interactive non-login shells
#   bash on macOS: terminal sessions are login shells, which read
#                  ~/.bash_profile (or ~/.profile) but NOT ~/.bashrc by default
#   zsh:           ~/.zshrc for interactive shells (both platforms)
#   fish:          ~/.config/fish/config.fish (we print, don't write)
#
# Strategy for bash: write to whichever of (.bashrc | .bash_profile | .profile)
# already exists; if none exist, create ~/.bashrc and tell the user. Source
# both .bashrc AND .bash_profile from each other if you want full coverage.

pick_bash_profile() {
  for candidate in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    [ -f "$candidate" ] && { echo "$candidate"; return; }
  done
  echo "$HOME/.bashrc"   # default to creating .bashrc
}

pin_env_var() {
  local profile="$1"
  if grep -q '^[[:space:]]*export AUTOSURVEY_TOOLS=' "$profile" 2>/dev/null; then
    echo "AUTOSURVEY_TOOLS already pinned in $profile"
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY-RUN  append 'export AUTOSURVEY_TOOLS=$TOOLS_DIR' to $profile"
    return 0
  fi
  if [ ! -f "$profile" ]; then
    : > "$profile"
    echo "created $profile"
  fi
  {
    printf '\n# AutoSurvey: where survey-* skills look for python helpers\n'
    printf 'export AUTOSURVEY_TOOLS=%q\n' "$TOOLS_DIR"
  } >> "$profile"
  echo "appended AUTOSURVEY_TOOLS=$TOOLS_DIR → $profile"
}

shell_name="$(basename "${SHELL:-bash}")"
case "$shell_name" in
  zsh)
    pin_env_var "$HOME/.zshrc"
    ;;
  bash)
    profile="$(pick_bash_profile)"
    pin_env_var "$profile"
    # macOS hint: login shells read .bash_profile but not .bashrc; if we wrote
    # to .bashrc and .bash_profile exists, remind the user to source it.
    if [ "$profile" = "$HOME/.bashrc" ] && [ -f "$HOME/.bash_profile" ] \
       && ! grep -q 'bashrc' "$HOME/.bash_profile" 2>/dev/null; then
      echo "note: ~/.bash_profile exists but does not source ~/.bashrc;"
      echo "      add 'source ~/.bashrc' to ~/.bash_profile so login shells see AUTOSURVEY_TOOLS"
    fi
    ;;
  fish)
    echo "fish detected; AutoSurvey assumes posix-shell exports."
    echo "  manually add to ~/.config/fish/config.fish:"
    echo "    set -gx AUTOSURVEY_TOOLS $TOOLS_DIR"
    ;;
  *)
    echo "unrecognised shell ($shell_name); manually add to your shell profile:"
    echo "    export AUTOSURVEY_TOOLS=$TOOLS_DIR"
    ;;
esac

# ------------------------------------------------------------------ verify hint
cat <<EOF

Install complete. Next steps:

  1. Reload your shell so AUTOSURVEY_TOOLS is exported in this session:
       exec \$SHELL -l
       # or just open a new terminal

  2. Verify:
       echo \$AUTOSURVEY_TOOLS         # → $TOOLS_DIR
       ls   \$AUTOSURVEY_TOOLS/refine_brief.py

  3. Smoke run from any working directory (Claude Code or Codex):
       /survey-run --brief $REPO_ROOT/examples/briefs/long-context-extension.md \\
                   --max-papers 20

To uninstall: bash $REPO_ROOT/tools/uninstall.sh
EOF
