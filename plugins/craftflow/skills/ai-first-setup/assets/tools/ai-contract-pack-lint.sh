#!/usr/bin/env bash
# Zero-dep shell validator: replicates core behavior of ai-contract-pack-lint.js.
# Checks each project directory under scanRoots for required AI Context Pack files.
#
# Parity caveats vs the Node version:
#   - designPathPatterns (conditional DESIGN.md requirement) is not enforced here.
#   - exemptions from .agents-md-validator.json are not applied.
#   - JSON parsing uses python3 (preferred) or a sed fallback; malformed JSON falls back to defaults.
#   Use the Node variant when designPathPatterns or exemptions are needed.
#
# Usage: bash tools/scripts/ai-contract-pack-lint.sh
# Exit 0 on pass (or when severity=warn). Exit 1 when severity=error and issues found.

set -uo pipefail

CONFIG_FILE=".agents-md-validator.json"
ISSUES=0

# --- Config parsing ---

get_severity() {
  if [ ! -f "$CONFIG_FILE" ]; then
    echo "warn"
    return
  fi
  local val
  val=$(sed -n 's/.*"severity"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CONFIG_FILE" 2>/dev/null | head -1)
  echo "${val:-warn}"
}

get_scan_roots() {
  if [ ! -f "$CONFIG_FILE" ]; then
    echo "."
    return
  fi
  # Prefer python3 for reliable JSON parsing
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$CONFIG_FILE" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as f:
        d = json.load(f)
    roots = d.get("aiContractPack", {}).get("scanRoots", ["."])
    print("\n".join(roots))
except Exception:
    print(".")
PYEOF
  else
    # sed fallback: extract string values between "scanRoots" and the closing "]"
    # Works for the standard single-line-per-value format emitted by this skill.
    awk '
      /"scanRoots"/ { in_array = 1; next }
      in_array && /\[/ { next }
      in_array && /"[^"]+"/ {
        match($0, /"([^"]+)"/, a)
        if (a[1] != "") print a[1]
      }
      in_array && /\]/ { exit }
    ' "$CONFIG_FILE" 2>/dev/null || echo "."
  fi
}

SEVERITY=$(get_severity)

# Collect scan roots into array
scan_roots=()
while IFS= read -r root; do
  [ -n "$root" ] && scan_roots+=("$root")
done < <(get_scan_roots)
[ ${#scan_roots[@]} -eq 0 ] && scan_roots=(".")

# --- Project directory check ---

check_project_dir() {
  local dir="$1"
  [ -f "$dir/AGENTS.md" ] || return 0   # not a project dir

  local dir_ok=true

  if [ ! -f "$dir/AI.md" ]; then
    echo "WARNING: $dir/AI.md is missing" >&2
    dir_ok=false
  fi

  if [ ! -f "$dir/TESTS.md" ]; then
    echo "WARNING: $dir/TESTS.md is missing" >&2
    dir_ok=false
  fi

  if [ "$dir_ok" = "false" ]; then
    ISSUES=$((ISSUES + 1))
  fi
}

# --- Scan ---

for scan_root in "${scan_roots[@]}"; do
  if [ ! -d "$scan_root" ]; then
    echo "WARNING: scanRoot '$scan_root' does not exist — skipping" >&2
    continue
  fi

  # Scan first-level subdirectories of each scanRoot
  while IFS= read -r -d '' subdir; do
    check_project_dir "$subdir"
  done < <(find "$scan_root" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)
done

# --- Result ---

if [ "$ISSUES" -gt 0 ]; then
  if [ "$SEVERITY" = "error" ]; then
    echo "ERROR: AI contract pack lint failed — ${ISSUES} project directory/directories missing required files" >&2
    exit 1
  else
    echo "WARNING: AI contract pack lint found ${ISSUES} issue(s) (severity=warn — not blocking)" >&2
  fi
fi

echo "AI contract pack lint passed"
