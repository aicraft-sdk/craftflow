#!/usr/bin/env bash
# Zero-dep shell validator: replicates agents-md-lint.js behavior for non-JS repos.
# Validates every AGENTS.md found in the repo.
# Usage: bash tools/scripts/agents-md-lint.sh
# Exit 0: all checks pass. Exit 1: first violation (message to stderr).

set -uo pipefail

REPO_ROOT="$(pwd)"
MAX_SIZE=8192

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

is_git_ignored() {
  git check-ignore --quiet -- "$1" 2>/dev/null && return 0 || return 1
}

check_byte_size() {
  local file="$1"
  local size
  size=$(wc -c < "$file")
  # strip leading whitespace from wc output (BSD wc adds padding)
  size="${size//[[:space:]]/}"
  if [ "$size" -gt "$MAX_SIZE" ]; then
    fail "$file exceeds 8 KB size limit (${size} bytes > ${MAX_SIZE})"
  fi
}

check_forbidden_literals() {
  local file="$1"
  for literal in "/src/" "/lib/" "/components/"; do
    if grep -qF -- "$literal" "$file"; then
      fail "$file contains forbidden literal '$literal' — use descriptive text instead (e.g. 'source files' or 'shared components')"
    fi
  done
}

# Root AGENTS.md must contain all five required sections.
# Patterns match headings with optional numeric prefix (e.g. "## 1. Project / Scope …")
check_required_sections() {
  local file="$1"

  grep -qiE "^#+[[:space:]]+(([0-9]+\.[[:space:]]+)?Project.*Scope)" "$file" ||
    fail "$file is missing required section 'Project / Scope'"

  grep -qiE "^#+[[:space:]]+(([0-9]+\.[[:space:]]+)?Non.Negotiable)" "$file" ||
    fail "$file is missing required section 'Non-Negotiable'"

  grep -qiE "^#+[[:space:]]+(([0-9]+\.[[:space:]]+)?Build)" "$file" ||
    fail "$file is missing required section 'Build'"

  grep -qiE "^#+[[:space:]]+(([0-9]+\.[[:space:]]+)?Security)" "$file" ||
    fail "$file is missing required section 'Security'"

  grep -qiE "^#+[[:space:]]+(([0-9]+\.[[:space:]]+)?Agent.*Operating)" "$file" ||
    fail "$file is missing required section 'Agent Operating'"
}

# Subfolder AGENTS.md files must not repeat the five root section titles.
check_no_root_sections() {
  local file="$1"
  for title in \
    "Project / Scope Identification" \
    "Non-Negotiable Constraints" \
    "Build, Run & Test" \
    "Security & Safety" \
    "Agent Operating Rules"; do
    if grep -qiF -- "$title" "$file"; then
      fail "Subfolder $file must not repeat root section title '${title}'"
    fi
  done
}

lint_file() {
  local file="$1"
  local rel="${file#"$REPO_ROOT/"}"

  is_git_ignored "$file" && return 0

  check_byte_size "$file"
  check_forbidden_literals "$file"

  if [ "$rel" = "AGENTS.md" ]; then
    check_required_sections "$file"
  else
    check_no_root_sections "$file"
  fi
}

# Require root AGENTS.md
if [ ! -f "$REPO_ROOT/AGENTS.md" ]; then
  fail "Root AGENTS.md not found at $REPO_ROOT/AGENTS.md"
fi

# Lint root first
lint_file "$REPO_ROOT/AGENTS.md"

# Walk all other AGENTS.md files (skip common build/tool dirs and gitignored paths)
while IFS= read -r -d '' file; do
  [ "$file" = "$REPO_ROOT/AGENTS.md" ] && continue
  lint_file "$file"
done < <(find "$REPO_ROOT" \
  \( -name ".git" \
  -o -name "node_modules" \
  -o -name ".next" \
  -o -name "dist" \
  -o -name "build" \
  -o -name "target" \
  -o -name ".cursor" \
  \) -prune \
  -o -name "AGENTS.md" -print0 2>/dev/null)

echo "AGENTS.md lint passed"
