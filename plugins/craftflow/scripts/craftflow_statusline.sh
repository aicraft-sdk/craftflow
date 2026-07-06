#!/usr/bin/env bash
# craftflow_statusline.sh
#
# Statusline wrapper: composes claude-hud output with a live Craftflow
# workflow-progress segment (⚡ feature-name 60% · 🟢 phase_2 (3/5)).
#
# Set as the statusLine.command in ~/.claude/settings.json:
#   {
#     "statusLine": {
#       "type": "command",
#       "command": "bash /path/to/craftflow_statusline.sh"
#     }
#   }
#
# Claude Code pipes a session JSON object on stdin each render (~every 300ms).
# This script reads it once, passes it to claude-hud, and appends a Craftflow
# segment derived from the project's live .craftflow/state/workflows/ state.
#
# Graceful degradation:
#   - No .craftflow/ or no active workflow → segment empty → only hud shown
#   - claude-hud missing or errors → hud empty → segment (or nothing) shown
#   - This script NEVER exits non-zero; any failure produces only empty output
#
# Revert to plain claude-hud by restoring ~/.claude/settings.json statusLine.command to:
#   bash -c 'plugin_dir=$(ls -d "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/plugins/cache/claude-hud/claude-hud/*/ 2>/dev/null | awk -F/ '"'"'{ print $(NF-1) "\t" $0 }'"'"' | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n | tail -1 | cut -f2-); exec "/Users/david.gracia/.nvm/versions/node/v20.19.0/bin/node" "${plugin_dir}dist/index.js"'

# ── Locate this script's directory (for finding sibling craftflow scripts) ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
STATUS_SCRIPT="${SCRIPT_DIR}/craftflow_status_report.py"

# ── Read stdin once (session JSON from Claude Code) ────────────────────────
input="$(cat 2>/dev/null)" || input=""

# ── Locate claude-hud (exact version-sort logic preserved from settings.json) ─
plugin_dir=""
plugin_dir="$(ls -d "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache/claude-hud/claude-hud/"*/ 2>/dev/null \
  | awk -F/ '{ print $(NF-1) "\t" $0 }' \
  | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n \
  | tail -1 \
  | cut -f2- 2>/dev/null)" 2>/dev/null || plugin_dir=""

# ── Resolve node executable ────────────────────────────────────────────────
node="$(command -v node 2>/dev/null)" || node=""
if [[ -z "$node" ]]; then
  # Fallback to the path used by the original settings.json command
  node="/Users/david.gracia/.nvm/versions/node/v20.19.0/bin/node"
fi

# ── Run claude-hud (feed the captured stdin) ──────────────────────────────
hud=""
if [[ -n "$plugin_dir" && -f "${plugin_dir}dist/index.js" && -x "$node" ]]; then
  hud="$(printf '%s' "$input" | "$node" "${plugin_dir}dist/index.js" 2>/dev/null)" || hud=""
fi

# ── Extract cwd from session JSON (locates the project's .craftflow/) ─────
cwd=""
if [[ -n "$input" ]]; then
  cwd="$(printf '%s' "$input" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin).get("cwd",""))' \
    2>/dev/null)" || cwd=""
fi

# ── Run craftflow status segment (~40ms, read-only) ───────────────────────
seg=""
if [[ -f "$STATUS_SCRIPT" && -n "$cwd" ]]; then
  seg="$(python3 "$STATUS_SCRIPT" --statusline --project "$cwd" 2>/dev/null)" || seg=""
fi

# ── Compose output: hud + optional craftflow segment ─────────────────────
printf '%s' "$hud"
[[ -n "$seg" ]] && printf '  %s' "$seg"
printf '\n'
