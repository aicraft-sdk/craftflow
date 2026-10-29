### Advisory law

TRIAGE, CODEBASE-HEALTH and QA are advisory routes. They never write product code: no phase of their graphs is served by the build or debug write agents, each graph runs once with no review loop, and the router (never an agent) owns every task and every gate.

Memory Update runs only at a terminal state: the workflow completed, `workflow_failed` was recorded, or the user ended it. No Memory Update task exists or is runnable while a pause gate, a failure stop or a human-action stop is pending.

Failure law: when the agent of a route phase fails, record `failure_stop_gate` and set `pending_gate` to `{agent}_failed`, the agent name with `_` for `-`. The nine values are `architecture_scanner_failed`, `triage_agent_failed`, `qa_researcher_failed`, `qa_harness_builder_failed`, `qa_executor_failed` and, for the reused agents in QA phases, `planner_failed`, `plan_gap_reviewer_failed`, `code_reviewer_failed` and `silent_failure_hunter_failed`. The answers are "retry" (re-dispatch the same task) and "end" (record `workflow_failed`, then run terminal memory). There is no "skip": every route phase is mandatory, `qa-review` and `qa-hunt` included, and "skip" or "(b)" is a non-matching reply that is re-asked verbatim.

Human-action stops are never automatic, in normal or JUST_GO mode, and no Memory Update runs while one is pending. `qa_rebuild_cap` (a third `re-qa-build` round): "another round: <notes>" runs one more round with the notes, recorded in `remediation_history` as user-authorized; "end" records `workflow_failed`. `qa_preflight_blocked_cap` (the third preflight BLOCKED): "done" re-runs preflight once; "end" records `workflow_failed`. `triage_grill_cap` (a second NEEDS_GRILLING): a reply with the user's domain answer runs one final `phase:triage` pass with it in `## Requirements`; "end" is terminal with state `ready-for-human`.

### Advisory pauses

A workflow pauses, with `pending_gate` set, at one of eight gates: `candidate_choice`, `needs_info`, `needs_grilling`, `wontfix_confirm`, `qa_test_plan_approval`, `qa_grant_needed`, `qa_preflight_human_prerequisites` and `qa_source_contradiction`. While paused, no Memory Update task exists or is runnable.

On resume the router re-asks the paused gate's question and nothing else. A fresh, unrelated request while `candidate_choice` is pending counts as a decline of the choice.

Under JUST_GO: `candidate_choice` auto-picks `TOP_RECOMMENDATION`, else the first `Strong`, else the first; grilling follows the PLAN brainstorming rule; `needs_info` and `wontfix_confirm` are never automatic; a QA gate follows `_shared/route-qa.md`.

### Handoff targets and modes

An advisory or QA workflow hands off by minting a linked child with `scripts/craftflow_workflow_handoff.py`. The parent stays terminal and is never reopened.

| Source | Target | Mode | JUST_GO |
|---|---|---|---|
| CODEBASE-HEALTH grilled design | PLAN | auto, announce `-> handing off to PLAN (design: {path})` | auto |
| TRIAGE `ready-for-agent` enhancement / bug | BUILD / DEBUG | ask once | asked; non-interactive = declined |
| QA bug candidate (`defect`) | DEBUG | ask once | asked; non-interactive = declined |
| QA-seeded DEBUG verified | QA re-run | auto | auto |
| ORIENT, REVIEW | none | n/a | n/a |

PLAN and the QA re-run start automatically, with one announce line and no question, in normal and JUST_GO mode. BUILD and DEBUG are asked once, and asked even under JUST_GO: JUST_GO never auto-accepts them, and the handoff CLI refuses either target without a recorded `handoff_accepted` event.

### Handoff timing

Hand off only after the parent is finalized: its log has `memory_finalized` (a Cursor parent: `workflow_completed`, see `### Cursor finalization signal`). Before that the CLI exits 4 and mints nothing, so run the parent's Memory Update first, then offer or mint.

### Handoff inputs

BUILD gets `## Pre-Answered Requirements` plus the brief path (`--input {path} --input-kind brief`). PLAN gets the saved design as `design_file` (`--input {path} --input-kind design`), so brainstorming is skipped exactly as for a saved design. DEBUG gets the brief, or `## QA Bug Context` for a QA bug candidate. The CLI exits 4 when the input path does not exist. The mint call is `scripts/craftflow_workflow_handoff.py --project {root} --parent {wf} --target PLAN|BUILD|DEBUG|QA --source-route {ROUTE}` plus the input flags; it prints `child_workflow_uuid=wf-...`.

### Never re-offer

After a decline (`handoff_declined`, whether `user_declined` or `non_interactive`) the same handoff is never offered again in that workflow or when it is resumed: check the parent log for `handoff_declined` and `handoff_started` before asking.

### Handoff failure

When the CLI exits 5 it has appended `handoff_failed` to the parent log and left no child. Report its `Error:` line; the parent stays terminal and nothing is retried. On exit 3 or 4, report the line and do not mint by any other path.

### Asking about a write handoff

Ask once, as a single yes/no question naming the target and the brief path.

Non-interactive detection (Claude Code): treat the host as non-interactive when (a) `AskUserQuestion` is not among your available tools (when unsure, call it and apply (b) or (c)), (b) the call returns an error, or (c) the call returns no selected option and no free-text answer. Then mint nothing and never ask again: record `--record-decline --reason non_interactive --detail tool_unavailable|tool_error|no_answer`, with the value matching (a), (b) or (c). There is no environment-variable heuristic.

Recording the answer: a clear yes is `--record-accept --parent {wf} --target BUILD|DEBUG --answer "<the user's reply, verbatim>"`, which prints `accepted_event_id=...`; then mint with `--accepted-answer <id>`. A clear no is `--record-decline --reason user_declined`. A reply that is neither is asked again. `--record-accept` with `--just-go` exits 3, because JUST_GO never accepts.

### Adopting the handoff child

When this turn's handoff CLI printed `child_workflow_uuid=wf-...` or `already_handed_off=wf-...`, adopt that child instead of starting a workflow: skip only `references/workflow-start.md` "Parent workflow creation" step 1 (the mint), run every other start step for the child's type (the task-tool capability value is reused, the workflow-tier memory load, the Task Metadata Contract and the type's preparation reads: PLAN reads `references/plan-workflow.md` with `design_file` set; the QA re-run reads `references/qa-bug-handoff.md`), and append `workflow_adopted` (`{parent_workflow_uuid}`) to the child log.

If the session ended after the mint but before the adopt, the next CLI call prints `already_handed_off=<child>` and mints nothing: adopt that child by the same steps, appending `workflow_adopted` only when its log has none yet. A second mint never happens.

### Cursor finalization signal

A Cursor parent is finalized by `workflow_completed` (its `workflow_started` event carries `"host":"cursor"`), not by `memory_finalized`. Run the handoff with `--host cursor`: the child's `workflow_started` carries the same host, a QA child may be minted without a session id, and the Cursor router then rewrites `cursor-wf.json` for the child.
