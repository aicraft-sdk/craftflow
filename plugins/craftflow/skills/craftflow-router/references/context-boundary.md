# Context Boundary Check (advisory)

Plain `Bash()` call, no `Task()`/`TaskCreate()`, so `capabilities.task_tools_available == false`
fallback mode is a non-issue. Never blocks, never retries, and you never run /compact yourself.

**When:** (1) BUILD, after `phase_exit_gate` passes for a phase (standard or fast path) and before
`phase_cursor` advances to the next phase or memory-finalize begins; (2) PLAN hand-off, when
`plan-gap-reviewer` pass 1 or pass 2 returns `PASS` (`references/remediation-and-research.md`),
after that handler's bookkeeping and before continuing to memory finalization. Not run when a
review returns `FINDINGS`, on planner clarification, or for DEBUG or REVIEW.

BUILD (use the id of the phase that just exited):

```bash
python3 {plugin_root}/scripts/craftflow_context_nudge.py --boundary --wf {workflow_uuid} --phase {phase_id} --project-root "$PROJECT_ROOT"
```

PLAN hand-off (PLAN has no phase id; always this literal label):

```bash
python3 {plugin_root}/scripts/craftflow_context_nudge.py --boundary --wf {workflow_uuid} --phase plan-handoff --project-root "$PROJECT_ROOT"
```

Parse the single stdout JSON line. Branch ONLY on `relay` being literally `true`:
- `relay` is literally `true` and `level == "warn"` → include `advisory` verbatim in your next
  user-facing message and continue the workflow.
- `relay` is literally `true` and `level == "critical"` → persist the workflow artifact first, show
  `advisory` verbatim plus "Run /compact, then say continue to resume from the workflow artifact", and end
  the turn (do not start the next phase or agent in this turn).
- Anything else (`relay` false/missing, `mode` `audit`/`off`, `outcome` `already_advised`) → say
  nothing about context; continue.

Non-zero exit or unparseable stdout: append
`{"event":"context_boundary_failed","error":"{stderr_content}","ts":"{iso_now}"}` to
`.craftflow/state/workflows/{workflow_uuid}.events.jsonl` and continue. The checkpoint file
(`checkpoint_path`) is informational and nothing reads it; the workflow artifact stays the durable
truth and is what "continue" resumes from. Cost: one visible Bash call per phase exit in every
mode; in `audit`/`on` it writes the checkpoint and runs a narrative digest bounded at 2 s.
