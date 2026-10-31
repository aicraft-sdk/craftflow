### CODEBASE-HEALTH preparation

CODEBASE-HEALTH answers a request for architectural discovery or advice ("codebase health check", "improve architecture", "find the ball of mud"). It is an advisory route: one read-only scan, one HTML report, one pick, one grill and a PLAN handoff. It never edits product code; a request that asks for the refactor itself is BUILD.

Announce `-> CODEBASE-HEALTH workflow (signals: ...)`. Run every `scripts/` helper named here from the loaded plugin (the router skill's Base directory + `/../../scripts`), never from the installed copy, which may be older. The Claude task graph is `references/codebase-health-workflow.md`, the Cursor chain is `cursor-router/references/routes.md`, and `_shared/route-advisory.md` owns pauses, failure stops, terminal-only memory and the handoff. Dispatch the scanner with effort `high`. Append the `high` steering directive as the last line of `## Task Context` and record it in `telemetry.effort`.

### CODEBASE-HEALTH scanner and report

The `architecture-scanner` agent scans the subject the request names (else the git hot spots), runs the deletion test and writes `.craftflow/codebase-health/{workflow_uuid}/report.html`: one self-contained page with a before/after diagram per candidate. The agent never opens it. Once the contract validates, show the report path (the graph file says how the router opens it) and summarize the candidates in the reply: number, name, files, one-line problem and strength.

### CODEBASE-HEALTH candidate choice

`CANDIDATES_FOUND` pauses at `pending_gate: candidate_choice`. Ask one question that lists every candidate by number in its question text, with "None" as an option (Claude Code: one `AskUserQuestion`, which takes at most 4 options, so with more than 3 candidates the options are "1", "2", "3", "None" and any other number is typed as free text; Cursor: plain text). A reply that is neither a listed number nor "None" is re-asked verbatim, except a fresh unrelated request, which counts as a decline. Under JUST_GO the router asks no question and picks `TOP_RECOMMENDATION`, else the first `Strong` candidate, else the first candidate, and logs the pick in `## Decisions`. JUST_GO is on when `activeContext.md ## Session Settings` has `AUTO_PROCEED: true`; the request for the health check authorizes the whole route, so do not stop at the report to ask whether to continue: go straight to the grill in the same turn.

`NO_CANDIDATES` completes the workflow with no gate and no handoff. "None" ends the workflow as completed-declined: terminal memory, no handoff.

### CODEBASE-HEALTH grill

Seed `craftflow:brainstorming` with the picked candidate card (files, problem, solution, benefits, strength) and run it in the main context, one unresolved high-impact question at a time. Under JUST_GO the grill is not skipped: answer each brainstorming question with its recommended option, log each answer in `## Decisions`, and save the design in the same turn. Its output is a saved design `docs/plans/YYYY-MM-DD-<slug>-design.md`: parse `### Brainstorming Handoff (MACHINE-READABLE)` and capture `DESIGN_FILE` in `results.codebase_health.design_file`.

The gate stays `candidate_choice` through the grill: while the grill runs every reply is a grill answer, and `pending_gate` clears only when the design file is saved. No Memory Update exists or runs before then. A grill that ends with no design file counts as a decline.

### CODEBASE-HEALTH terminal and handoff

The workflow is terminal when the design file is saved, the choice is declined, `NO_CANDIDATES` is returned, or the user ends a failure stop. Run the Memory Update at that point and not before. When the parent has logged `memory_finalized`, a saved design hands off to PLAN with no question, announced as `-> handing off to PLAN (design: {path})`, by the call and the adopt rule of `_shared/route-advisory.md` (`--target PLAN --input {path} --input-kind design --source-route CODEBASE-HEALTH`). Do it in the same turn: a reply that ends with a saved design and no handoff is a failed route, and offering the PLAN instead of minting it is wrong. Every other terminal state mints nothing.

### CODEBASE-HEALTH contract validation

Extract the scanner's `verdict:`/`rationale:` block and validate it with `scripts/craftflow_contract_validate_pilot.py --agent architecture-scanner --workflow-uuid {workflow_uuid}`, which also checks the rules below. `REPORT_PATH` must equal `.craftflow/codebase-health/{workflow_uuid}/report.html` exactly (project-relative); any other value is `architecture_scanner_failed`. `CANDIDATES_FOUND` is valid only when the file at `REPORT_PATH` exists on disk and `CANDIDATE_COUNT` equals `len(CANDIDATES)` and is at least 1. `NO_CANDIDATES` is valid only with `CANDIDATE_COUNT` 0 and an empty `CANDIDATES`. `TOP_RECOMMENDATION` must be null or between 1 and `CANDIDATE_COUNT`. A contract that is missing, rejected by the validator or breaks these rules is a scanner failure: set `pending_gate` to `architecture_scanner_failed` and follow the failure law of `_shared/route-advisory.md`.

### CODEBASE-HEALTH phases

The only CODEBASE-HEALTH phase tokens are `codebase-health` and `memory-finalize`. `codebase-health` is the scanner task and the mint phase; `memory-finalize` is the terminal Memory Update. The choice and the grill are router turns, not tasks.
