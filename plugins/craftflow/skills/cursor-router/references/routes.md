### routes (pending)

QA, TRIAGE and CODEBASE-HEALTH are not yet available in this release. For those, announce `-> {ROUTE} workflow (signals: …; pending)`, then route the request by the pre-harvest order ERROR → DEBUG, PLAN → PLAN, REVIEW → REVIEW, otherwise BUILD, and continue under that route's law.

### ORIENT

Announce `-> ORIENT workflow (signals: {matched keywords})`, then `Read()` `tools/craftflow-plugin/plugins/craftflow/skills/_shared/route-orient.md` and answer inline with the read-only tools you already have (search, read, symbol or reference lookup).

- No `cursor-wf.json` is written: ORIENT creates no workflow state.
- No `Task` dispatch, no subagent, no todo list and no Memory Update.
- Stop at understanding. A follow-up change request re-routes from scratch.

### Advisory handoff (Cursor)

`Read()` `tools/craftflow-plugin/plugins/craftflow/skills/_shared/route-advisory.md` for the matrix, timing and never-re-offer law; this section is only the Cursor binding. A Cursor parent is finalized by `workflow_completed`, so every handoff call passes `--host cursor`:

```text
python3 tools/craftflow-plugin/plugins/craftflow/scripts/craftflow_workflow_handoff.py --host cursor --project . --parent {parent_wf} --target {PLAN|BUILD|DEBUG|QA} --input {path} --input-kind {design|brief} --source-route {ROUTE}
```

`--task-tools-available` is inherited from the parent artifact; pass it only to override.

- PLAN and the QA re-run mint with no question. On `child_workflow_uuid=` or `already_handed_off=`, adopt the child (`route-advisory.md` `### Adopting the handoff child`) and rewrite `cursor-wf.json` for it as at any workflow start, with `pending_handoff: null`. `workflow_completed` (appended at § 10 step 8) is the finalization signal.
- BUILD and DEBUG are asked in plain text. Set `pending_handoff` in `cursor-wf.json` to `{parent_wf, target, input_path, question}`, ask the `question`, and stop. A `cursor-agent -p` run that ends with the question unanswered leaves `pending_handoff` set: it is pending, not declined, and the next user turn answers it.

### Cursor handoff answers

Applied by § 1's precedence check while `pending_handoff` is non-null. The first reply that matches a row wins; nothing else routes the message.

| Reply | Action |
|---|---|
| "yes", "(a)", "accept", "go ahead" | Run `craftflow_workflow_handoff.py --record-accept --parent {parent_wf} --target {target} --answer "<the reply, verbatim>"`, then `craftflow_workflow_handoff.py --project . --parent {parent_wf} --host cursor --target {target} --input {input_path} --accepted-answer <accepted_event_id>`; rewrite `cursor-wf.json` for the child with `pending_handoff: null` |
| "no", "(b)", "decline", "skip" | Run `craftflow_workflow_handoff.py --record-decline --reason user_declined --parent {parent_wf}`; set `pending_handoff: null` |
| anything else | Re-ask `pending_handoff.question` verbatim and keep `pending_handoff` |

### Cursor route gate answers

Applied by § 1's precedence check when `pending_gate` does not start with `phase_`. Each route phase whose agent fails sets `{agent}_failed`; later route phases add their own gate rows here. Any other reply, including "skip" and "(b)", is re-asked verbatim: no route gate offers skip, because every route phase is mandatory.

| `pending_gate` | "retry" | "end" |
|---|---|---|
| `architecture_scanner_failed` | re-dispatch the same task; clear `pending_gate` | record `workflow_failed`; run terminal memory; clear `pending_gate` |
| `triage_agent_failed` | re-dispatch the same task; clear `pending_gate` | record `workflow_failed`; run terminal memory; clear `pending_gate` |
| `qa_researcher_failed` | re-dispatch the same task; clear `pending_gate` | record `workflow_failed`; run terminal memory; clear `pending_gate` |
| `qa_harness_builder_failed` | re-dispatch the same task; clear `pending_gate` | record `workflow_failed`; run terminal memory; clear `pending_gate` |
| `qa_executor_failed` | re-dispatch the same task; clear `pending_gate` | record `workflow_failed`; run terminal memory; clear `pending_gate` |
| `planner_failed` | re-dispatch the same task; clear `pending_gate` | record `workflow_failed`; run terminal memory; clear `pending_gate` |
| `plan_gap_reviewer_failed` | re-dispatch the same task; clear `pending_gate` | record `workflow_failed`; run terminal memory; clear `pending_gate` |
| `code_reviewer_failed` | re-dispatch the same task; clear `pending_gate` | record `workflow_failed`; run terminal memory; clear `pending_gate` |
| `silent_failure_hunter_failed` | re-dispatch the same task; clear `pending_gate` | record `workflow_failed`; run terminal memory; clear `pending_gate` |
