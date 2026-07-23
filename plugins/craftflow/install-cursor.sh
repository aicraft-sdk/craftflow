#!/usr/bin/env bash
# install-cursor.sh — install Craftflow MDC rules into Cursor AI
#
# Safe to re-run (idempotent). Downloads rules directly; no git clone needed.
# Run via:  curl -fsSL https://raw.githubusercontent.com/aicraft-sdk/craftflow/main/install-cursor.sh | bash

set -euo pipefail

CRAFTFLOW_REPO="https://raw.githubusercontent.com/aicraft-sdk/craftflow/main"
CURSOR_RULES_DIR="$HOME/.cursor/rules/core"
# Override target for testing only — production (no override set) is unaffected.
CURSOR_SKILLS_DIR="${CURSOR_SKILLS_DIR:-$HOME/.cursor/skills}"

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

echo ""
echo "→ Craftflow skills entry point (cursor-router)..."

# Only wire up the skills symlink when running from a real local file — a
# curl-piped invocation (curl ... | bash) has no accessible plugin checkout to
# link to.
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  ROUTER_TARGET="$PLUGIN_ROOT/skills/cursor-router"
  ROUTER_LINK="$CURSOR_SKILLS_DIR/cursor-router"

  if [ ! -d "$ROUTER_TARGET" ]; then
    echo "  ✗ error: expected skill directory not found at $ROUTER_TARGET" >&2
    echo "    (shallow or partial checkout? cannot link cursor-router skill)" >&2
    exit 1
  fi

  mkdir -p "$CURSOR_SKILLS_DIR" || {
    echo "  ✗ failed to create $CURSOR_SKILLS_DIR (unwritable HOME?)" >&2
    exit 1
  }

  # Canonicalize the link target to its physical path (portable — no GNU
  # readlink -f). Needed because the checkout may sit under a symlinked
  # ancestor (e.g. /tmp -> /private/tmp on macOS, or an iCloud-synced
  # ~/Desktop), which would otherwise make two logical spellings of the same
  # real directory compare unequal below.
  ROUTER_TARGET_REAL="$(cd "$ROUTER_TARGET" && pwd -P)"

  EXISTING_TARGET_REAL=""
  if [ -L "$ROUTER_LINK" ]; then
    EXISTING_TARGET="$(readlink "$ROUTER_LINK")"
    EXISTING_TARGET_DIR="$(dirname "$EXISTING_TARGET")"
    if [ -d "$EXISTING_TARGET_DIR" ]; then
      EXISTING_TARGET_REAL="$(cd "$EXISTING_TARGET_DIR" && pwd -P)/$(basename "$EXISTING_TARGET")"
    fi
  fi

  if [ -n "$EXISTING_TARGET_REAL" ] && [ "$EXISTING_TARGET_REAL" = "$ROUTER_TARGET_REAL" ]; then
    echo "  ✓ $ROUTER_LINK already correctly linked to $ROUTER_TARGET"
  else
    if [ -e "$ROUTER_LINK" ] || [ -L "$ROUTER_LINK" ]; then
      BACKUP="$ROUTER_LINK.stale-backup-$(date -u +%Y%m%d-%H%M%S)-$$"
      mv "$ROUTER_LINK" "$BACKUP" || {
        echo "  ✗ failed to back up existing $ROUTER_LINK to $BACKUP" >&2
        exit 1
      }
      echo "  ⚠ found existing $ROUTER_LINK (not correctly linked) — backed up to $BACKUP"
    fi
    ln -s "$ROUTER_TARGET" "$ROUTER_LINK"
    echo "  ✓ linked $ROUTER_LINK -> $ROUTER_TARGET"
  fi
else
  echo "  ⚠ Not running from a local file (likely curl-piped) — skipping the skills symlink."
  echo "    Cursor's router entry point still needs ~/.cursor/skills/cursor-router pointed"
  echo "    at a real local craftflow plugin checkout to pick up craftflow content. Run:"
  echo "      ln -s /path/to/your/craftflow-plugin/skills/cursor-router ~/.cursor/skills/cursor-router"
fi
