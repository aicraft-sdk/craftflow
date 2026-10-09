#!/usr/bin/env bash
# Runs every tests/fixtures/test_*.py and decides the CI verdict. Used by .github/workflows/craftflow-ci.yml.
#
# CRAFTFLOW_CI_KNOWN_ENV_FAILURES: one entry per line, "<test file> | <expected failed count> | <reason>".
# A listed file is excused ONLY while it fails with exactly that many failed checks (its "N failed" summary).
# The count is read only from the final "Results: N passed, M failed" line of the file's output. Each file may be listed
# once, and only a test_*.py name is accepted.
# A different count, an unparseable summary, or a listed file that now passes (or no longer exists) fails the job:
# the entry must be updated or removed. File names are compared literally, never as a regex.
#
# With CI=true a fixture that exits 0 but printed a `SKIP: ... CI must not skip` line fails the job (a skipped origin/main
# comparison is fine on a laptop, never in CI).
#
# FIXTURE_DIR defaults to this script's directory; RUNNER_TEMP to $TMPDIR or /tmp.
set -u

dir="${FIXTURE_DIR:-$(cd "$(dirname "$0")" && pwd)}"
tmp="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
known="${CRAFTFLOW_CI_KNOWN_ENV_FAILURES:-}"
out="$tmp/fixture.out"
failed=0
seen=""

# expected_for <file>: prints the pinned failed count for an exact file name, or nothing when it is not listed.
expected_for() {
  NAME="$1" awk -F'|' '
    { for (i = 1; i <= NF; i++) { gsub(/^[ \t]+|[ \t]+$/, "", $i) } }
    $1 == ENVIRON["NAME"] { print $2; exit }' <<EOF
$known
EOF
}

# Reject malformed entries up front so a typo can never silently widen the excuse.
while IFS= read -r line; do
  [ -z "$(printf '%s' "$line" | tr -d '[:space:]')" ] && continue
  name="$(printf '%s' "$line" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $1); print $1}')"
  count="$(printf '%s' "$line" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}')"
  case "$count" in
    '' | *[!0-9]*)
      echo "::error::CRAFTFLOW_CI_KNOWN_ENV_FAILURES entry needs '<file> | <failed count> | <reason>': $line"
      failed=1
      ;;
  esac
  case "$name" in
    test_*.py) ;;
    *)
      echo "::error::CRAFTFLOW_CI_KNOWN_ENV_FAILURES entry '$name' is not a test_*.py fixture; it would never run"
      failed=1
      ;;
  esac
  case "$seen" in
    *"|$name|"*)
      echo "::error::CRAFTFLOW_CI_KNOWN_ENV_FAILURES lists $name more than once; keep a single entry"
      failed=1
      ;;
  esac
  seen="$seen|$name|"
  if [ ! -f "$dir/$name" ]; then
    echo "::error::CRAFTFLOW_CI_KNOWN_ENV_FAILURES lists $name but no such fixture exists; remove the entry"
    failed=1
  fi
done <<EOF
$known
EOF

set -- "$dir"/test_*.py
if [ ! -e "$1" ]; then
  echo "::error::no test_*.py fixtures found in $dir"
  exit 1
fi

for t in "$dir"/test_*.py; do
  name="$(basename "$t")"
  want="$(expected_for "$name")"
  if python3 "$t" >"$out" 2>&1; then
    if [ -n "$want" ]; then
      echo "::error::$name passed but is listed in CRAFTFLOW_CI_KNOWN_ENV_FAILURES; remove the entry"
      failed=1
    fi
    # CI checks out with fetch-depth 0, so a comparison that skipped itself ("... CI must not skip") skipped a real check.
    if [ "${CI:-}" = "true" ] && grep -q 'SKIP: .*CI must not skip' "$out"; then
      echo "::error::$name skipped a check that CI must not skip (origin/main or the P2 commit is missing from the checkout):"
      grep 'SKIP: .*CI must not skip' "$out"
      failed=1
    fi
    continue
  fi
  if [ "$name" = "test_craftflow_stop_gate.py" ] && python3 "$t" >"$out" 2>&1; then
    echo "::warning::$name passed on rerun (known notify-timeout flake)"
    continue
  fi
  if [ -n "$want" ]; then
    got="$(grep -E '^Results: [0-9]+ passed, [0-9]+ failed' "$out" | tail -1 | sed -E 's/^Results: [0-9]+ passed, ([0-9]+) failed.*/\1/')"
    if [ "$got" = "$want" ]; then
      echo "::warning::$name failed $got check(s) as pinned in CRAFTFLOW_CI_KNOWN_ENV_FAILURES"
      continue
    fi
    echo "::error::$name failed ${got:-an unknown number of} check(s); CRAFTFLOW_CI_KNOWN_ENV_FAILURES pins $want"
  else
    echo "::error::$name failed"
  fi
  tail -40 "$out"
  failed=1
done

exit "$failed"
