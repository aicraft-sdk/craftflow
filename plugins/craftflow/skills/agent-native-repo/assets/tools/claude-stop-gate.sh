#!/bin/sh
# Claude Code Stop hook: block "done" while the test suite fails. Installed by craftflow agent-native-repo.
# Exit 2 sends the failure tail back to Claude so it keeps working. When Claude is already continuing because of
# this hook (stop_hook_active), let it stop: CI remains the binding gate, and this avoids an endless loop.
input=$(cat)
case "$input" in
  *'"stop_hook_active":true'*|*'"stop_hook_active": true'*) exit 0 ;;
esac
log="${TMPDIR:-/tmp}/claude-stop-gate.$$.log"
if {{TEST_CMD}} > "$log" 2>&1; then
  rm -f "$log"
  exit 0
fi
echo "Tests fail, so the task is not done. Fix the code (never the tests) and try again. Last lines:" >&2
tail -n 40 "$log" >&2
rm -f "$log"
exit 2
