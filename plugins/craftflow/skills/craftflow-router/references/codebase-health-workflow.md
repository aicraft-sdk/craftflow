### CODEBASE-HEALTH start

Read `_shared/route-codebase-health.md` first; this file is only the Claude Code task graph. Mint the workflow with the `references/workflow-start.md` "Parent workflow creation" helper, passing `--workflow-type CODEBASE-HEALTH --phase codebase-health` (the parent task's `phase:` is `codebase-health` too), then create the scanner task below. A pre-minted handoff child never takes this path.

### CODEBASE-HEALTH task graph

```text
TaskCreate({
  subject: "CRAFTFLOW architecture-scanner: Scan {subject} for deepening candidates",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:codebase-health\nplan:N/A\nscope:N/A\nreason:Advisory architecture scan\n\nScan for deepening candidates and write .craftflow/codebase-health/{workflow_uuid}/report.html.",
  activeForm: "Scanning the architecture"
}) -> scanner_task_id
```

The dispatcher row `codebase-health` maps the task to the `architecture-scanner` agent. Put the user's request and any named subject in `## Requirements`, and pass `Parent Workflow ID:` so the agent can name the report folder. Completion is router-owned under task-tool fallback: when `capabilities.task_tools_available` is false follow `references/task-tool-fallback.md`, and mark the scanner task completed from its validated Router Contract.

### CODEBASE-HEALTH after the scanner

1. Validate the contract (`{plugin_root}/scripts/craftflow_contract_validate_pilot.py --agent architecture-scanner --workflow-uuid {workflow_uuid}`, never the installed copy) and handle a failure as `_shared/route-codebase-health.md` `### CODEBASE-HEALTH contract validation` says: `architecture_scanner_failed` is answered "retry" or "end" only.
2. Show the report only after step 1 passed, so `REPORT_PATH` is exactly the pinned path: run `open "{REPORT_PATH}"` (quoted) only on darwin, otherwise print the path. The agent never opens it. Showing the report is not a reply point: under JUST_GO continue immediately to step 3's pick without replying; outside JUST_GO the only reply is the choice question.
3. Under JUST_GO pick `TOP_RECOMMENDATION` now (else the first `Strong` candidate, else candidate 1) and log `candidate_picked` in `## Decisions`. Run the choice, the grill and the terminal states of the route file. The gate stays `candidate_choice` until the design file is saved. Under JUST_GO do not end the turn after the report: pick, grill, save the design and continue to step 4 in the same turn.
4. Create the Memory Update task only at a terminal state (completed, declined, `NO_CANDIDATES` or `workflow_failed`), never while a gate or failure stop is pending. A memory target file that does not exist yet (the workflow-tier files of a fresh workflow, or an unseeded project file) is created first from the `craftflow:session-memory` template, and is never a reason to stop before `memory_finalized`:

```text
TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist codebase-health learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:N/A\nscope:N/A\nreason:Persist captured Memory Notes. ROUTER ONLY: execute inline. Follow references/memory-finalize.md § Memory Update task instructions (read it now if not in context). Never spawn Agent() for this task.",
  activeForm: "Persisting codebase-health learnings"
}) -> memory_task_id
```

5. Hand off now, in the same turn and before the final reply, once `memory_finalized` is logged and a design was saved:

```bash
python3 {plugin_root}/scripts/craftflow_workflow_handoff.py --project {PROJECT_ROOT} --parent {workflow_uuid} --target PLAN --input {design_file} --input-kind design --source-route CODEBASE-HEALTH
```

`{plugin_root}` is the router skill's Base directory + `/../..` (the loaded plugin, never the installed copy). Announce `-> handing off to PLAN (design: {path})`, read the `child_workflow_uuid=` line and adopt the child as `_shared/route-advisory.md` `### Adopting the handoff child` says. Asking whether to start the PLAN is wrong: it is automatic in normal and JUST_GO mode. A declined choice, `NO_CANDIDATES` or a failure stop mints nothing.
