# LV-R5: Cursor end-to-end check for craftflow:retro (HUMAN-VERIFY, pre-merge).
#
# Usage (David, ONE interactive zsh/bash terminal, cwd = MAIN checkout root):
#   source <WT>/tools/craftflow-plugin/plugins/craftflow/tests/live/lvr5-cursor-e2e.sh
#   lvr5_setup          # expect first line "pending state: clear", last line "LV-R5 setup: OK"
#   cursor-agent        # type: retro wf-adr-0044-audit-only-opt-pre-20260927-130317-27b13b3e
#                       # approve only the python3 ...craftflow_retro.py / python3 -c commands; then exit it
#   lvr5_check; lvr5_restore
# The restore target is persisted to $LVR5_TMP/lvr5-old-router (default /tmp), so lvr5_restore still works
# after re-sourcing this file or opening a new shell. Sourcing never resets that file or a non-empty OLD_ROUTER.
# If anything fails after "LV-R5 setup: OK", still run lvr5_restore (unconditional).
# If everything is lost, run: ln -sfn <"restore target:" value> ~/.cursor/skills/cursor-router && rm -f ~/.cursor/skills/retro
# This file defines functions only; sourcing it changes nothing. It never uses `exit`. Works in bash and zsh.

WT="${WT:-/Users/david.gracia/Desktop/projects/own/ai-craft/.claude/worktrees/it-execute-plan-docs-plans-2026-24c2182b}"
SK="$HOME/.cursor/skills"; PW="$WT/tools/craftflow-plugin/plugins/craftflow/skills"; OLD_ROUTER="${OLD_ROUTER:-}"
LVR5_TMP="${LVR5_TMP:-/tmp}"
lvr5_clean() {
  # Explicit names + existence tests: no globs, so zsh `nomatch` can never fire.
  local n
  for n in lvr5-git-before lvr5-wf-before lvr5-cwf-before lvr5-old-router; do
    if [ -e "$LVR5_TMP/$n" ]; then rm -f "$LVR5_TMP/$n"; fi
  done
  return 0
}
lvr5_setup() {
  python3 -c 'import json,os,sys; p=".craftflow/state/cursor-wf.json"; d=json.load(open(p)) if os.path.exists(p) else {}; bad=[k for k in ("pending_skill_approval","pending_gate") if isinstance(d,dict) and d.get(k) is not None]; print("STOP: cursor-wf.json has non-null "+", ".join(bad)+"; answer the pending question in Cursor first") if bad else print("pending state: clear"); sys.exit(1 if bad else 0)' || return 1
  test -L "$SK/cursor-router" || { echo "STOP: $SK/cursor-router is not a symlink"; return 1; }
  { test ! -e "$SK/retro" && test ! -L "$SK/retro"; } || { echo "STOP: $SK/retro already exists"; return 1; }
  OLD_ROUTER="$(readlink "$SK/cursor-router")"; echo "restore target: $OLD_ROUTER"
  printf '%s\n' "$OLD_ROUTER" > "$LVR5_TMP/lvr5-old-router" || { echo "STOP: cannot persist restore target to $LVR5_TMP/lvr5-old-router"; return 1; }
  ln -sfn "$PW/cursor-router" "$SK/cursor-router" && ln -s "$PW/retro" "$SK/retro" || { echo "STOP: relink failed; run lvr5_restore"; return 1; }
  git status --porcelain > "$LVR5_TMP/lvr5-git-before"; ls -t .craftflow/state/workflows | head -3 > "$LVR5_TMP/lvr5-wf-before"
  { stat -f %m .craftflow/state/cursor-wf.json 2>/dev/null || echo absent; } > "$LVR5_TMP/lvr5-cwf-before"
  echo "LV-R5 setup: OK"
}
lvr5_check() {
  local n
  for n in lvr5-git-before lvr5-wf-before lvr5-cwf-before; do
    test -f "$LVR5_TMP/$n" || { echo "STOP: run lvr5_setup first (snapshot files missing)"; return 1; }
  done
  git status --porcelain | diff "$LVR5_TMP/lvr5-git-before" - && ls -t .craftflow/state/workflows | head -3 | diff "$LVR5_TMP/lvr5-wf-before" - && { stat -f %m .craftflow/state/cursor-wf.json 2>/dev/null || echo absent; } | diff "$LVR5_TMP/lvr5-cwf-before" - && echo "LV-R5 read-only: OK"
}
lvr5_restore() {
  local target="$OLD_ROUTER"
  if [ -z "$target" ] && [ -f "$LVR5_TMP/lvr5-old-router" ]; then target="$(head -n 1 "$LVR5_TMP/lvr5-old-router")"; fi
  test -n "$target" || { echo "STOP: restore target is empty (no OLD_ROUTER and no $LVR5_TMP/lvr5-old-router; nothing to restore)"; return 1; }
  ln -sfn "$target" "$SK/cursor-router"; rm -f "$SK/retro"
  if test "$(readlink "$SK/cursor-router")" = "$target" && test ! -e "$SK/retro" && test ! -L "$SK/retro"; then
    echo "LV-R5 restore: OK"; OLD_ROUTER=""; lvr5_clean; return 0
  fi
  echo "STOP: restore not verified; run: ln -sfn $target $SK/cursor-router && rm -f $SK/retro"
  return 1
}
