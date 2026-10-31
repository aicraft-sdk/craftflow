### routes (pending)

QA and TRIAGE are not yet available in this release. For those, announce `-> {ROUTE} workflow (signals: …; pending)`, then route the request by the pre-harvest order ERROR → DEBUG, PLAN → PLAN, REVIEW → REVIEW, otherwise BUILD, and continue under that route's law.

### ORIENT

Announce `-> ORIENT workflow (signals: {matched keywords})`, then `Read()` `tools/craftflow-plugin/plugins/craftflow/skills/_shared/route-orient.md` and answer inline with the read-only tools you already have (search, read, symbol or reference lookup).

- No `cursor-wf.json` is written: ORIENT creates no workflow state.
- No `Task` dispatch, no subagent, no todo list and no Memory Update.
- Stop at understanding. A follow-up change request re-routes from scratch.

### CODEBASE-HEALTH

Announce `-> CODEBASE-HEALTH workflow (signals: {matched keywords})`, then `Read()` `tools/craftflow-plugin/plugins/craftflow/skills/_shared/route-codebase-health.md` and `tools/craftflow-plugin/plugins/craftflow/skills/_shared/route-advisory.md`. Write `cursor-wf.json` as at any workflow start, with type `CODEBASE-HEALTH` and `phase:codebase-health`. The chain is advisory: it never edits product code.

1. Scanner: invoke `Task` once (`subagent_type: generalPurpose`, foreground) with the `architecture-scanner` agent brief (the file in this skill's Agent File Paths table), Task Phase `codebase-health` and the request in `## Requirements`. Validate the returned Router Contract by the route file's `### CODEBASE-HEALTH contract validation`; a failure sets `pending_gate: architecture_scanner_failed`.
2. Report: print the project-relative `REPORT_PATH`. Do not open it.
3. Choice: ask in plain text, listing every candidate by number plus "None". Set `pending_gate: candidate_choice` in `cursor-wf.json` and stop; the next user turn is answered by `### Cursor route gate answers`. Under JUST_GO there is no question: pick as the route file says and go to the grill.
4. Grill: run `craftflow:brainstorming` inline in your own turn (never dispatched), seeded with the picked candidate card. Record the saved design in `results.codebase_health.design_file`; `pending_gate` stays `candidate_choice` until then.
5. Terminal: run the Memory Update inline and append `workflow_completed` (§ 8 step 8). `NO_CANDIDATES` and a declined choice end here with no handoff.
6. Handoff: a saved design hands off to PLAN with no question, by `### Advisory handoff (Cursor)` with `--host cursor --target PLAN --input {design_file} --input-kind design --source-route CODEBASE-HEALTH`; announce `-> handing off to PLAN (design: {path})`.

### Advisory handoff (Cursor)

`Read()` `tools/craftflow-plugin/plugins/craftflow/skills/_shared/route-advisory.md` for the matrix, timing and never-re-offer law; this section is only the Cursor binding. A Cursor parent is finalized by `workflow_completed`, so every handoff call passes `--host cursor`:

```text
python3 tools/craftflow-plugin/plugins/craftflow/scripts/craftflow_workflow_handoff.py --host cursor --project . --parent {parent_wf} --target {PLAN|BUILD|DEBUG|QA} --input {path} --input-kind {design|brief} --source-route {ROUTE}
```

`--task-tools-available` is inherited from the parent artifact; pass it only to override.

- PLAN and the QA re-run mint with no question. On `child_workflow_uuid=` or `already_handed_off=`, adopt the child (`route-advisory.md` `### Adopting the handoff child`) and rewrite `cursor-wf.json` for it as at any workflow start, with `pending_handoff: null`. `workflow_completed` (appended at § 8 step 8) is the finalization signal.
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

| `pending_gate` | Reply | Action |
|---|---|---|
| `candidate_choice` | any reply once a candidate is picked (the grill is running) | the grill answer; `pending_gate` stays `candidate_choice` until the design file is saved |
| `candidate_choice` | a listed candidate number | record the pick and run the grill on that candidate |
| `candidate_choice` | "None" | terminal declined: run the Memory Update, append `workflow_completed`, no handoff; clear `pending_gate` |
| `candidate_choice` | anything else | re-ask the same question verbatim and keep `pending_gate` |
