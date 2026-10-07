# LV-R5: Cursor end-to-end check for craftflow:retro (HUMAN-VERIFY, pre-merge).
#
# Usage (David, ONE interactive zsh/bash terminal, cwd = MAIN checkout root):
#   source <WT>/tools/craftflow-plugin/plugins/craftflow/tests/live/lvr5-cursor-e2e.sh
#   lvr5_setup          # expect first line "pending state: clear", last line "LV-R5 setup: OK"
#   cursor-agent        # type: retro wf-adr-0044-audit-only-opt-pre-20260927-130317-27b13b3e
#                       # approve only the python3 ...craftflow_retro.py / python3 -c commands; then exit it
#   lvr5_check; lvr5_restore
# Stay in the SAME shell session (the restore uses OLD_ROUTER / SK set here).
# If anything fails after "LV-R5 setup: OK", still run lvr5_restore (unconditional).
# If the session was lost, run: ln -sfn <"restore target:" value> ~/.cursor/skills/cursor-router && rm -f ~/.cursor/skills/retro
# This file defines functions only; sourcing it changes nothing. It never uses `exit`.

WT="${WT:-/Users/david.gracia/Desktop/projects/own/ai-craft/.claude/worktrees/it-execute-plan-docs-plans-2026-24c2182b}"
SK="$HOME/.cursor/skills"; PW="$WT/tools/craftflow-plugin/plugins/craftflow/skills"; OLD_ROUTER=""
lvr5_setup() {
  python3 -c 'import json,os,sys; p=".craftflow/state/cursor-wf.json"; d=json.load(open(p)) if os.path.exists(p) else {}; bad=[k for k in ("pending_skill_approval","pending_gate") if isinstance(d,dict) and d.get(k) is not None]; print("STOP: cursor-wf.json has non-null "+", ".join(bad)+"; answer the pending question in Cursor first") if bad else print("pending state: clear"); sys.exit(1 if bad else 0)' || return 1
  test -L "$SK/cursor-router" || { echo "STOP: $SK/cursor-router is not a symlink"; return 1; }
  { test ! -e "$SK/retro" && test ! -L "$SK/retro"; } || { echo "STOP: $SK/retro already exists"; return 1; }
  OLD_ROUTER="$(readlink "$SK/cursor-router")"; echo "restore target: $OLD_ROUTER"
  ln -sfn "$PW/cursor-router" "$SK/cursor-router" && ln -s "$PW/retro" "$SK/retro" || { echo "STOP: relink failed; run lvr5_restore"; return 1; }
  git status --porcelain > /tmp/lvr5-git-before; ls -t .craftflow/state/workflows | head -3 > /tmp/lvr5-wf-before
  { stat -f %m .craftflow/state/cursor-wf.json 2>/dev/null || echo absent; } > /tmp/lvr5-cwf-before
  echo "LV-R5 setup: OK"
}
lvr5_check() {
  git status --porcelain | diff /tmp/lvr5-git-before - && ls -t .craftflow/state/workflows | head -3 | diff /tmp/lvr5-wf-before - && { stat -f %m .craftflow/state/cursor-wf.json 2>/dev/null || echo absent; } | diff /tmp/lvr5-cwf-before - && echo "LV-R5 read-only: OK"
}
lvr5_restore() {
  test -n "$OLD_ROUTER" || { echo "STOP: OLD_ROUTER is empty (setup stopped before relinking; nothing to restore)"; rm -f /tmp/lvr5-*; return 1; }
  ln -sfn "$OLD_ROUTER" "$SK/cursor-router"; rm -f "$SK/retro"
  test "$(readlink "$SK/cursor-router")" = "$OLD_ROUTER" && test ! -e "$SK/retro" && test ! -L "$SK/retro" && echo "LV-R5 restore: OK"
  rm -f /tmp/lvr5-*
}
