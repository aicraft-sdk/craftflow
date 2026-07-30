### DEBUG preparation

1. If the user explicitly asks for research or the bug clearly depends on external post-2024 behavior, allow a research round before the first investigator run.
2. Immediately write `[DEBUG-RESET: wf:{workflow_uuid}]` once the workflow id exists.
3. Preserve failed attempt counting semantics: the investigator counts `[DEBUG-N]:` entries after the most recent reset marker.

### DEBUG task graph

```text
TaskCreate({
  subject: "CRAFTFLOW bug-investigator: Investigate {error}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:debug-investigate\nplan:N/A\nscope:N/A\nreason:Find root cause\n\nFind the root cause and apply the fix.",
  activeForm: "Investigating bug"
}) -> investigator_task_id

TaskCreate({
  subject: "CRAFTFLOW code-reviewer: Review fix",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:debug-review\nplan:N/A\nscope:N/A\nreason:Review the fix\n\nReview the debug fix quality.",
  activeForm: "Reviewing fix"
}) -> reviewer_task_id
TaskUpdate({ taskId: reviewer_task_id, addBlockedBy: [investigator_task_id] })

TaskCreate({
  subject: "CRAFTFLOW integration-verifier: Verify fix",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:debug-verify\nplan:N/A\nscope:N/A\nreason:Verify the fix\n\nVerify the fix works end-to-end.",
  activeForm: "Verifying fix"
}) -> verifier_task_id
TaskUpdate({ taskId: verifier_task_id, addBlockedBy: [reviewer_task_id] })
```

DEBUG has no doc-sync step, so `chain_tail_task_id` starts as `verifier_task_id` directly (unlike BUILD, where it starts as `doc_sync_task_id`). Apply the SAME Learn-Distill Gate and Skill-Distill Gate documented in `build-workflow.md` (identical gate checks, identical `phase:learn-distill` / `phase:skill-distill` `TaskCreate` shape, identical `chain_tail_task_id` update rule) before creating Memory Update:

- `references/fast-path.md`'s own Learn-Distill Gate section already states `learn-distill` is dispatched "at the end of BUILD (standard and fast-path) and DEBUG workflows" — DEBUG was already in scope for that gate by design, it simply had no `TaskCreate` to back it, exactly like BUILD's dead-wiring gap.
- The Skill-Distill Gate's eligibility check (`craftflow_skill_ledger.py --query`) is workflow-type agnostic — it reads a project-wide ledger, not anything DEBUG-specific — so there is no reason to exclude DEBUG from it once BUILD has it.

```text
TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist debug learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:N/A\nscope:N/A\nreason:Persist captured Memory Notes\n\nROUTER ONLY: execute inline. Read the workflow artifact and THIS task description payload, persist to .craftflow/state/*.md,\nBefore persisting each MEMORY_NOTES field, run:\n  python3 {plugin_root}/scripts/craftflow_memory_merge.py\nwith a JSON payload of {"section_text": "<current section>", "notes": [...], "retractions": []}\non stdin; use stdout as the replacement section content.\nConfidence <0.7 notes are dropped. Retractions remove matching bullets. New bullets get a (conf: x) suffix.\nthen remove the matching [craftflow-internal] memory_task_id line from activeContext.md ## References. Never spawn Agent() for this task.",
  activeForm: "Persisting debug learnings"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [chain_tail_task_id] })
```
