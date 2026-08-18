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

  echo ""
  echo "→ Craftflow write-guard hooks (hooks.json) for the current project..."

  # Cursor's PreToolUse/PostToolUse/etc. enforcement is per-project, not global: it reads
  # a `.cursor/hooks.json` inside EACH workspace folder you open (in addition to whatever
  # is in the machine's global ~/.cursor/hooks.json), and additively merges every source's
  # entries per event — it never lets one source override another. There is no global
  # "install once, guarded everywhere" hook mechanism the way ~/.cursor/skills/ works for
  # routing. This step must therefore be re-run (or its output copied) into every project
  # you want craftflow's write-guard actually enforced in.
  #
  # This also fixes a real bug in the shipped template: PLUGIN_ROOT/hooks.json's commands
  # reference a `${CURSOR_PLUGIN_ROOT}` shell variable. Cursor DOES resolve this itself, but
  # only for hooks it loads via its own native "claude-plugin" hook source (auto-imported
  # Claude Code plugin manifests, gated behind the `thirdPartyExtensibilityEnabled` setting)
  # -- NOT for a project-local .cursor/hooks.json like the one this step writes (source
  # "project"), which gets no placeholder substitution at all. Copying the template in as-is
  # to a project hooks.json would make every hook fail with "No such file or directory" on
  # an empty-string-expanded path. This step resolves it to a real absolute path first.
  HOOKS_TEMPLATE="$PLUGIN_ROOT/hooks.json"
  if [ ! -f "$HOOKS_TEMPLATE" ]; then
    echo "  ✗ error: expected hooks template not found at $HOOKS_TEMPLATE" >&2
    echo "    (shallow or partial checkout? cannot install Cursor write-guard hooks)" >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "  ⚠ python3 not found — skipping hooks.json install (write-guard enforcement will" >&2
    echo "    NOT be active in Cursor for this project). Install python3 and re-run this script." >&2
  else
    HOOKS_TARGET_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
    HOOKS_TARGET_DIR="$HOOKS_TARGET_ROOT/.cursor"
    HOOKS_TARGET="$HOOKS_TARGET_DIR/hooks.json"
    mkdir -p "$HOOKS_TARGET_DIR" || {
      echo "  ✗ failed to create $HOOKS_TARGET_DIR (unwritable project root?)" >&2
      exit 1
    }
    python3 - "$HOOKS_TEMPLATE" "$PLUGIN_ROOT" "$HOOKS_TARGET" <<'PYEOF'
import json
import sys

template_path, plugin_root, target_path = sys.argv[1:4]

with open(template_path, encoding="utf-8") as f:
    template = json.load(f)

# Cursor does not resolve ${CURSOR_PLUGIN_ROOT} for a project-local .cursor/hooks.json
# (only for its own natively-imported "claude-plugin" hook source) -- resolve it to a real
# absolute path now, since the template ships it as a placeholder for exactly this step.
def resolve(entry):
    entry = dict(entry)
    entry["command"] = entry["command"].replace("${CURSOR_PLUGIN_ROOT}", plugin_root)
    return entry

# NOTE: dedup below is by exact `command` string, which embeds this absolute plugin_root
# path. If this project is later re-provisioned from a DIFFERENT checkout location (repo
# moved, or plugin reinstalled under a different cache dir), the newly-resolved commands
# won't match the old ones and will be appended as duplicates rather than replacing them.
# Delete .cursor/hooks.json and re-run this script if the checkout location ever changes.

resolved_hooks = {
    step: [resolve(e) for e in entries]
    for step, entries in template["hooks"].items()
}

try:
    with open(target_path, encoding="utf-8") as f:
        existing = json.load(f)
    if not isinstance(existing, dict) or not isinstance(existing.get("hooks"), dict):
        existing = {"version": template["version"], "hooks": {}}
except (FileNotFoundError, json.JSONDecodeError):
    existing = {"version": template["version"], "hooks": {}}

existing.setdefault("version", template["version"])
existing.setdefault("hooks", {})

added = 0
for step, entries in resolved_hooks.items():
    bucket = existing["hooks"].setdefault(step, [])
    existing_commands = {e.get("command") for e in bucket if isinstance(e, dict)}
    for entry in entries:
        if entry["command"] not in existing_commands:
            bucket.append(entry)
            existing_commands.add(entry["command"])
            added += 1

with open(target_path, "w", encoding="utf-8") as f:
    json.dump(existing, f, indent=2)
    f.write("\n")

print(f"  ✓ {target_path} ({added} new hook entries added, existing entries preserved)")
PYEOF
    echo "    Re-run this script (or copy the merge above) inside any other project you want"
    echo "    craftflow's write-guard enforced in — this step is per-project, not global."
  fi
else
  echo "  ⚠ Not running from a local file (likely curl-piped) — skipping the skills symlink."
  echo "    Cursor's router entry point still needs ~/.cursor/skills/cursor-router pointed"
  echo "    at a real local craftflow plugin checkout to pick up craftflow content. Run:"
  echo "      ln -s /path/to/your/craftflow-plugin/skills/cursor-router ~/.cursor/skills/cursor-router"
  echo ""
  echo "  ⚠ Also skipping Cursor write-guard hooks.json install for the same reason — it"
  echo "    needs a real local checkout of this plugin to resolve script paths from. Re-run"
  echo "    this script from a local clone (not curl-piped) inside each project you want"
  echo "    craftflow's write-guard actually enforced in."
fi
