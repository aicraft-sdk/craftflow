### REVIEW preparation

1. REVIEW is advisory only.
2. Never create REM-FIX or implementation tasks directly from a REVIEW workflow.
3. If the final review verdict is `CHANGES_REQUESTED`, the router may offer `Start BUILD to fix (Recommended)` as a follow-up user choice.

### REVIEW task graph

```text
**Arch Lint (advisory):** before dispatching code-reviewer, run the check per `references/harness-self-checks.md § Arch Lint` and fold any findings into its dispatch prompt.

TaskCreate({
  subject: "CRAFTFLOW code-reviewer: Review {target}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:review-audit\nplan:N/A\nscope:N/A\nreason:Advisory review\n\nRun a scoped code review.",
  activeForm: "Reviewing code"
}) -> reviewer_task_id

TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist review learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:N/A\nscope:N/A\nreason:Persist captured Memory Notes. ROUTER ONLY: execute inline. Follow references/memory-finalize.md § Memory Update task instructions (read it now if not in context). Never spawn Agent() for this task.",
  activeForm: "Persisting review learnings"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [reviewer_task_id] })
```
