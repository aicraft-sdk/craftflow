#!/usr/bin/env bash
# install-cursor.sh
# Installs Craftflow into Cursor AI for a team member.
# Safe to re-run: re-creates symlink, overwrites MDC rules, preserves AIDLC backup.
# Run from anywhere — resolves plugin path relative to this script's location.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$SCRIPT_DIR/plugins/craftflow"
CURSOR_SKILLS_DIR="$HOME/.cursor/skills"
CURSOR_RULES_DIR="$HOME/.cursor/rules/core"

# ── 1. Symlink cursor-router skill ─────────────────────────────────────────
echo "→ cursor-router skill..."
SKILL_SRC="$PLUGIN_DIR/skills/cursor-router"
SKILL_LINK="$CURSOR_SKILLS_DIR/cursor-router"

if [ ! -f "$SKILL_SRC/SKILL.md" ]; then
  echo "  ERROR: skill source not found at $SKILL_SRC/SKILL.md"
  echo "  Run Phase 1 of the Cursor integration plan first (creates cursor-router/SKILL.md)."
  exit 1
fi

mkdir -p "$CURSOR_SKILLS_DIR"
# Remove stale symlink if present (idempotent)
[ -L "$SKILL_LINK" ] && rm "$SKILL_LINK"
ln -s "$SKILL_SRC" "$SKILL_LINK"
echo "  ✓ $SKILL_LINK → $SKILL_SRC"

# ── 2. Copy MDC rules (copy, not symlink — paths embed into MDC content) ───
echo "→ MDC rules..."
mkdir -p "$CURSOR_RULES_DIR"

cp "$PLUGIN_DIR/rules/craftflow-router.mdc" "$CURSOR_RULES_DIR/craftflow-router.mdc"
echo "  ✓ craftflow-router.mdc"

cp "$PLUGIN_DIR/rules/craftflow-state.mdc" "$CURSOR_RULES_DIR/craftflow-state.mdc"
echo "  ✓ craftflow-state.mdc"

# ── 3. Retire AIDLC ────────────────────────────────────────────────────────
echo "→ Retiring AIDLC..."
AIDLC_DIR="$CURSOR_SKILLS_DIR/aidlc"
AIDLC_BAK="$CURSOR_SKILLS_DIR/aidlc.bak"
AIDLC_RULE="$CURSOR_RULES_DIR/aidlc-routing.mdc"
AIDLC_ARCHIVED=false

if [ -d "$AIDLC_DIR" ] && [ ! -L "$AIDLC_DIR" ]; then
  # Archive (not delete) — preserves rollback option
  [ -d "$AIDLC_BAK" ] && rm -rf "$AIDLC_BAK"
  mv "$AIDLC_DIR" "$AIDLC_BAK"
  AIDLC_ARCHIVED=true
  echo "  ✓ aidlc/ archived to aidlc.bak/ (rollback: mv ~/.cursor/skills/aidlc.bak ~/.cursor/skills/aidlc)"
elif [ -d "$AIDLC_BAK" ]; then
  echo "  — aidlc.bak already present (skipped re-archive)"
else
  echo "  — aidlc not found (skipped)"
fi

if [ -f "$AIDLC_RULE" ]; then
  rm "$AIDLC_RULE"
  echo "  ✓ aidlc-routing.mdc removed"
else
  echo "  — aidlc-routing.mdc not present (skipped)"
fi

# ── 4. Confirmation summary ─────────────────────────────────────────────────
echo ""
echo "Craftflow Cursor install complete."
echo ""
echo "Installed:"
echo "  ~/.cursor/skills/cursor-router  →  $SKILL_SRC"
echo "  ~/.cursor/rules/core/craftflow-router.mdc"
echo "  ~/.cursor/rules/core/craftflow-state.mdc"
echo ""
if [ "$AIDLC_ARCHIVED" = true ]; then
  echo "Retired:"
  echo "  AIDLC archived to ~/.cursor/skills/aidlc.bak/ (rollback preserved)"
  echo "  aidlc-routing.mdc removed from ~/.cursor/rules/core/"
  echo ""
fi
echo "Verify: open Cursor on any project and send a dev request."
echo "        The agent should start by reading cursor-router/SKILL.md."
