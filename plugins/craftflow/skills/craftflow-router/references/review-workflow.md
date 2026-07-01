### REVIEW preparation

1. REVIEW is advisory only.
2. Never create REM-FIX or implementation tasks directly from a REVIEW workflow.
3. If the final review verdict is `CHANGES_REQUESTED`, the router may offer `Start BUILD to fix (Recommended)` as a follow-up user choice.

### REVIEW task graph

```text
TaskCreate({
  subject: "CRAFTFLOW code-reviewer: Review {target}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:review-audit\nplan:N/A\nscope:N/A\nreason:Advisory review\n\nRun a scoped code review.",
  activeForm: "Reviewing code"
}) -> reviewer_task_id

TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist review learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:N/A\nscope:N/A\nreason:Persist captured Memory Notes\n\nROUTER ONLY: execute inline. Read the workflow artifact and THIS task description payload, persist to .craftflow/state/*.md,\nBefore persisting each MEMORY_NOTES field, run:\n  python3 {plugin_root}/scripts/craftflow_memory_merge.py\nwith a JSON payload of {"section_text": "<current section>", "notes": [...], "retractions": []}\non stdin; use stdout as the replacement section content.\nConfidence <0.7 notes are dropped. Retractions remove matching bullets. New bullets get a (conf: x) suffix.\nthen remove the matching [craftflow-internal] memory_task_id line from activeContext.md ## References. Never spawn Agent() for this task.",
  activeForm: "Persisting review learnings"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [reviewer_task_id] })
```
