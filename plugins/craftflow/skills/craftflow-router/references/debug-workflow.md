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

DEBUG has no doc-sync step, so `chain_tail_task_id` starts as `verifier_task_id` directly (unlike BUILD, where it starts as `doc_sync_task_id`). Apply the SAME Learn-Distill Gate and Skill-Distill Gate documented in `build-graph-standard.md` (identical gate checks, identical `phase:learn-distill` / `phase:skill-distill` `TaskCreate` shape, identical `chain_tail_task_id` update rule) before creating Memory Update:

- `references/fast-path.md`'s own Learn-Distill Gate section already states `learn-distill` is dispatched "at the end of BUILD (standard and fast-path) and DEBUG workflows" — DEBUG was already in scope for that gate by design, it simply had no `TaskCreate` to back it, exactly like BUILD's dead-wiring gap.
- The Skill-Distill Gate's eligibility check (`craftflow_skill_ledger.py --query`) is workflow-type agnostic — it reads a project-wide ledger, not anything DEBUG-specific — so there is no reason to exclude DEBUG from it once BUILD has it.

**Clean-State Check (advisory):** before creating the Memory Update task below, run the check per `references/harness-self-checks.md § Clean-State Check` and fold any findings AND any skipped (unscannable) entries into this task's own deferred memory notes.

```text
TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist debug learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:N/A\nscope:N/A\nreason:Persist captured Memory Notes. ROUTER ONLY: execute inline. Follow references/memory-finalize.md § Memory Update task instructions (read it now if not in context). Never spawn Agent() for this task.",
  activeForm: "Persisting debug learnings"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [chain_tail_task_id] })
```
