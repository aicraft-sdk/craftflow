#!/usr/bin/env bash
# install-cursor.sh — install Craftflow MDC rules into Cursor AI
#
# Safe to re-run (idempotent). Downloads rules directly; no git clone needed.
# Run via:  curl -fsSL https://raw.githubusercontent.com/aicraft-sdk/craftflow/main/install-cursor.sh | bash

set -euo pipefail

CRAFTFLOW_REPO="https://raw.githubusercontent.com/aicraft-sdk/craftflow/main"
CURSOR_RULES_DIR="$HOME/.cursor/rules/core"

echo "→ Craftflow MDC rules..."
mkdir -p "$CURSOR_RULES_DIR"

curl -fsSL "$CRAFTFLOW_REPO/rules/craftflow-router.mdc" -o "$CURSOR_RULES_DIR/craftflow-router.mdc"
echo "  ✓ craftflow-router.mdc"

curl -fsSL "$CRAFTFLOW_REPO/rules/craftflow-state.mdc" -o "$CURSOR_RULES_DIR/craftflow-state.mdc"
echo "  ✓ craftflow-state.mdc"

# Retire AIDLC if present
AIDLC_RULE="$HOME/.cursor/rules/core/aidlc-routing.mdc"
if [ -f "$AIDLC_RULE" ]; then
  rm "$AIDLC_RULE"
  echo "  ✓ aidlc-routing.mdc removed"
fi

echo ""
echo "Craftflow MDC rules installed."
echo "  ~/.cursor/rules/core/craftflow-router.mdc"
echo "  ~/.cursor/rules/core/craftflow-state.mdc"
echo ""
echo "Craftflow will activate automatically on every dev request (alwaysApply: true)."
