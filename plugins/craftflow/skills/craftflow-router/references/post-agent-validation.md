# Post-agent validation

### Read-only contracts

Primary signal and fallback heading conventions — shared with Cursor — canonical list
lives in `tools/craftflow-plugin/plugins/craftflow/skills/_shared/router-protocol.md` §
"Agent Verdict Headings". `Read()` that file now if you have not already this session.

Verdict extraction:
1. Try the envelope on line 1.
2. If envelope is missing or malformed, scan the first 5 lines for the heading.
3. Extract `CRITICAL_ISSUES` from `### Critical Issues`.
4. If output is too short or malformed, run inline verification rather than blindly approving.
5. Detect `SELF_REMEDIATED` from task state:
   - If the task remains `in_progress` and `blockedBy` is non-empty after the agent stops, treat it as self-remediated.
6. For integration-verifier, parse scenario accounting:
   - `SCENARIOS_TOTAL`
   - `SCENARIOS_PASSED`
   - `SCENARIOS_FAILED`
   - Fail validation if those counts do not reconcile with the evidence array.
   - Fail validation if any scenario omits explicit `Expected` or `Actual` evidence.

Read-only structured intent fields:
- `REMEDIATION_NEEDED: true|false`
- `REMEDIATION_REASON: ...`
- `REMEDIATION_SCOPE_REQUESTED: N/A|CRITICAL_ONLY|ALL_ISSUES`
- `REVERT_RECOMMENDED: true|false`
- `PLANNING_REVIEW_STATUS: PASS|FINDINGS`
- `BLOCKING_FINDINGS_COUNT: [number]`
- `REPLAN_NEEDED: true|false`
- `REPLAN_REASON: ...`

Compatibility rule:
- Accept legacy self-healed blocked task behavior during migration.
- Prefer the new structured remediation fields over task-state inference when both exist.

### Write-agent YAML contracts

For write agents, parse the final fenced YAML block under `### Router Contract (MACHINE-READABLE)`.

Expected fields:

| Agent | Required fields |
|-------|-----------------|
| component-builder | `STATUS`, `SUMMARY`, `CONFIDENCE`, `PHASE_ID`, `PHASE_STATUS`, `PHASE_EXIT_READY`, `CHECKPOINT_TYPE`, `PROOF_STATUS`, `INPUTS`, `EXPECTED_ARTIFACTS`, `TDD_RED_EXIT`, `TDD_GREEN_EXIT`, `SCENARIOS`, `ASSUMPTIONS`, `DECISIONS`, `BLOCKED_ITEMS`, `SKIPPED_ITEMS`, `SCOPE_INCREASES`, `BLOCKING`, `NEXT_ACTION`, `REMEDIATION_NEEDED`, `REQUIRES_REMEDIATION`, `REMEDIATION_REASON`, `MEMORY_NOTES` |
| bug-investigator | `STATUS`, `SUMMARY`, `VERIFICATION_RIGOR`, `CONFIDENCE`, `ROOT_CAUSE`, `TDD_RED_EXIT`, `TDD_GREEN_EXIT`, `VARIANTS_COVERED`, `BLAST_RADIUS_SCAN`, `SCENARIOS`, `ASSUMPTIONS`, `DECISIONS`, `BLOCKING`, `NEXT_ACTION`, `REMEDIATION_NEEDED`, `REQUIRES_REMEDIATION`, `REMEDIATION_REASON`, `NEEDS_EXTERNAL_RESEARCH`, `RESEARCH_REASON`, `MEMORY_NOTES` |
| planner | `STATUS`, `SUMMARY`, `PLAN_MODE`, `VERIFICATION_RIGOR`, `CONFIDENCE`, `PLAN_FILE`, `PHASES`, `RISKS_IDENTIFIED`, `SCENARIOS`, `ASSUMPTIONS`, `DECISIONS`, `OPEN_DECISIONS`, `DIFFERENCES_FROM_AGREEMENT`, `RECOMMENDED_DEFAULTS`, `ALTERNATIVES`, `DRAWBACKS`, `PROVABLE_PROPERTIES`, `BLOCKING`, `NEXT_ACTION`, `REMEDIATION_NEEDED`, `REQUIRES_REMEDIATION`, `REMEDIATION_REASON`, `GATE_PASSED`, `USER_INPUT_NEEDED`, `MEMORY_NOTES` |
| web-researcher | `STATUS`, `SUMMARY`, `FILE_PATH`, `BACKEND_MODE`, `SOURCES_ATTEMPTED`, `SOURCES_USED`, `QUALITY_LEVEL`, `KEY_FINDINGS_COUNT`, `WHAT_CHANGED_RECOMMENDATION`, `MEMORY_NOTES` |
| github-researcher | `STATUS`, `SUMMARY`, `FILE_PATH`, `BACKEND_MODE`, `SOURCES_ATTEMPTED`, `SOURCES_USED`, `QUALITY_LEVEL`, `IMPLEMENTATIONS_FOUND`, `WHAT_CHANGED_RECOMMENDATION`, `MEMORY_NOTES` |
| doc-syncer | `STATUS`, `SUMMARY`, `IMPACT_LEVEL`, `DOC_LAYERS_EVALUATED`, `DOC_FILES_UPDATED`, `DOC_FILES_SKIPPED`, `SKIP_REASON`, `AUDIT_DOCS_CREATED`, `AUDIT_DOCS_UPDATED`, `MEMORY_NOTES` |
| plan-bakeoff-judge | `STATUS`, `SUMMARY`, `PLAN_MODE`, `VERIFICATION_RIGOR`, `CONFIDENCE`, `PLAN_FILE`, `WINNING_MODEL`, `SYNTHESIZED`, `CANDIDATES_COMPARED`, `PHASES`, `RISKS_IDENTIFIED`, `SCENARIOS`, `OPEN_DECISIONS`, `DIFFERENCES_FROM_AGREEMENT`, `ALTERNATIVES`, `DRAWBACKS`, `PROVABLE_PROPERTIES`, `BLOCKING`, `REMEDIATION_NEEDED`, `REQUIRES_REMEDIATION`, `REMEDIATION_REASON`, `GATE_PASSED`, `MEMORY_NOTES` |

As of the schema-validation pilot, `component-builder` and `planner` nest every field in their
rows above under `verdict:`/`rationale:` and additionally emit `REMEDIATION_SCOPE_REQUESTED` —
see § "Write-agent contract schema validation (pilot: component-builder, planner)" immediately
below for the exact per-field split and the new field's current always-`N/A` status.

(`skill-author`'s required fields, including `SUMMARY`, are documented in
its own row in the Contract overrides table below rather than duplicated
here.)

`SUMMARY` is a single human-readable sentence describing what happened this
round — the router surfaces it directly in `status_history`/event-log
entries and memory distillation so a workflow's progress can be scanned
without re-parsing every structured field (borrowed from deepseek-harness's
Ralph-loop round handoff). It is additive to, not a replacement for, the
richer structured fields above (`SCENARIOS`, `ROOT_CAUSE`, `MEMORY_NOTES`,
etc.) — do not let a present `SUMMARY` substitute for the actual evidence
those fields carry.

If the YAML block is missing or malformed:
- Treat the task as invalid output.
- Do not continue the workflow based on prose alone.
- Re-run inline verification and fail safe.

### Write-agent contract schema validation (pilot: component-builder, planner)

`component-builder` and `planner` emit their Router Contract YAML body as two nested top-level
keys, `verdict:` (strictly typed: enums, booleans, numbers, nullable numbers) and `rationale:`
(free text: summaries, prose lists, `MEMORY_NOTES`) — see each agent's `.md` file for the exact
per-field split. Before applying any Contract Overrides row below for these two agents,
mechanically validate the extracted YAML body:

1. Write the extracted YAML block body (the text between the ```` ```yaml ```` fence markers,
   starting with `verdict:`) to a scratch file — do not pipe it through a shell heredoc, since
   the body routinely contains quotes and colons that are easy to mis-quote.
2. Run:
   ```bash
   python3 {plugin_root}/scripts/craftflow_contract_validate_pilot.py \
     --agent component-builder --file {scratch_path}
   ```
   (substitute `--agent planner` for the planner agent).
3. Parse the single-line stdout JSON: `{"valid": bool, "violations": [...]}`.
4. `valid: false` (including a non-zero exit with no parseable JSON, a missing/corrupt schema
   file, or any script crash) — treat identically to the "YAML block is missing or malformed"
   rule above: do not apply any Contract Override, re-run inline verification, fail safe.
5. `valid: true` — proceed to the Contract Overrides table below, reading fields as
   `verdict.FIELD` / `rationale.FIELD`.

This validator (`craftflow_contract_validate_pilot.py`) is a distinct script from the
pre-existing, still-dormant `craftflow_contract_validate.py` (flat, kind-keyed envelope format
for read-only agents) — do not confuse the two or merge their schema tables.

Field-to-section map for the two pilot agents (single source of truth:
`scripts/schemas/component-builder.json` / `scripts/schemas/planner.json`):

| Agent | `verdict` (typed) | `rationale` (free text) |
|-------|--------------------|--------------------------|
| component-builder | `STATUS`, `CONFIDENCE`, `PHASE_ID`, `PHASE_STATUS`, `PHASE_EXIT_READY`, `CHECKPOINT_TYPE`, `PROOF_STATUS`, `TDD_RED_EXIT`, `TDD_GREEN_EXIT`, `CRITICAL_ISSUES`, `BLOCKING`, `NEXT_ACTION`, `REMEDIATION_NEEDED`, `REQUIRES_REMEDIATION`, `REMEDIATION_SCOPE_REQUESTED` | `SUMMARY`, `INPUTS`, `EXPECTED_ARTIFACTS`, `SCENARIOS`, `ASSUMPTIONS`, `DECISIONS`, `BLOCKED_ITEMS`, `SKIPPED_ITEMS`, `SCOPE_INCREASES`, `REMEDIATION_REASON`, `MEMORY_NOTES` |
| planner | `STATUS`, `PLAN_MODE`, `VERIFICATION_RIGOR`, `CONFIDENCE`, `PHASES`, `RISKS_IDENTIFIED`, `PLANNING_REVIEW_STATUS`, `PLANNING_REVIEW_RUNS`, `BLOCKING`, `NEXT_ACTION`, `REMEDIATION_NEEDED`, `REQUIRES_REMEDIATION`, `GATE_PASSED`, `REMEDIATION_SCOPE_REQUESTED` | `SUMMARY`, `PLAN_FILE`, `LIVING_SPEC_IMPACTED`, `SCENARIOS`, `ASSUMPTIONS`, `DECISIONS`, `OPEN_DECISIONS`, `DIFFERENCES_FROM_AGREEMENT`, `RECOMMENDED_DEFAULTS`, `ALTERNATIVES`, `DRAWBACKS`, `PROVABLE_PROPERTIES`, `REMEDIATION_REASON`, `USER_INPUT_NEEDED`, `MEMORY_NOTES` |

`REMEDIATION_SCOPE_REQUESTED` is new on these two agents as of this pilot — both always emit
`N/A` today (neither agent drives the BUILD `1a-SCOPE` scope decision, which stays sourced from
the read-only reviewer/hunter agents per `references/remediation-and-research.md` § Scope
resolution); the field exists on the schema now so a future wiring change cannot ship an
unvalidated value.

**Known gap (not fixed by this pilot):** `skills/cursor-router/SKILL.md`'s own "Verdict by
agent" table still documents `component-builder`/`planner` using the old flat field names,
because Cursor dispatches the same `agents/component-builder.md`/`agents/planner.md` files and
will therefore receive the new nested output without its own routing doc being updated. This is
a known, tracked follow-up, not an oversight — see the schema-validation plan's Risks section.

Non-pilot agents (`bug-investigator`, `web-researcher`, `github-researcher`, `doc-syncer`,
`plan-bakeoff-judge`, `skill-author`) keep their existing flat YAML shape and are validated only
by the prose-level check above — no `craftflow_contract_validate_pilot.py` invocation for them
yet.

### Contract overrides

| Agent | Override |
|-------|----------|
| component-builder | `verdict.STATUS=PASS` requires `verdict.TDD_RED_EXIT=1`, `verdict.TDD_GREEN_EXIT=0`, `verdict.PHASE_STATUS=completed`, `verdict.PHASE_EXIT_READY=true`, `verdict.PROOF_STATUS=passed`, empty `rationale.BLOCKED_ITEMS`, a non-empty `rationale.SUMMARY`, and a non-empty `rationale.SCENARIOS` array with at least one passing scenario. That passing scenario must include non-empty `name`, `command`, `expected`, `actual`, and `exit_code`. |
| bug-investigator | `STATUS=FIXED` requires `VERIFICATION_RIGOR` to be explicit, `TDD_RED_EXIT=1`, `TDD_GREEN_EXIT=0`, `VARIANTS_COVERED>=1`, a non-empty `BLAST_RADIUS_SCAN`, a non-empty `SUMMARY`, and a non-empty `SCENARIOS` array unless it explicitly set `NEEDS_EXTERNAL_RESEARCH=true`. At least one scenario name must start with `Regression:` and one with `Variant:`. Both required scenarios must include non-empty `command`, `expected`, `actual`, and `exit_code`. |
| code-reviewer | `APPROVE` + critical issues becomes `CHANGES_REQUESTED` |
| code-reviewer | `APPROVE` with zero findings across ALL dimensions AND fewer than 3 file:line evidence citations → trigger fallback inline verification. Rubber-stamp approvals without substantive analysis are invalid. |
| silent-failure-hunter | `CLEAN` + critical issues becomes `ISSUES_FOUND` |
| silent-failure-hunter | `CLEAN` with zero error-handling sites inspected OR zero files scanned → trigger fallback inline verification. A CLEAN verdict requires stated scope. |
| integration-verifier | `PASS` + critical issues becomes `FAIL`; scenario totals must reconcile with the scenario table and evidence array; every counted scenario must map to a concrete evidence row; every scenario row must contain non-empty `Expected` and `Actual` values |
| planner | `verdict.STATUS=PLAN_CREATED` or `verdict.STATUS=DECISION_RFC_CREATED` requires non-empty `rationale.PLAN_FILE`, explicit `verdict.PLAN_MODE`, explicit `verdict.VERIFICATION_RIGOR`, `verdict.CONFIDENCE>=50`, `verdict.GATE_PASSED=true`, a non-empty `rationale.SUMMARY`, a non-empty `rationale.SCENARIOS` array, `rationale.OPEN_DECISIONS=[]`, and `rationale.DIFFERENCES_FROM_AGREEMENT` explicitly present. `verdict.PLAN_MODE=decision_rfc` also requires non-empty `rationale.ALTERNATIVES` and `rationale.DRAWBACKS`; `verdict.VERIFICATION_RIGOR=critical_path` requires non-empty `rationale.PROVABLE_PROPERTIES`. |
| plan-bakeoff-judge | `STATUS=PLAN_CREATED` or `STATUS=DECISION_RFC_CREATED` requires every threshold the `planner` override row already requires (non-empty `PLAN_FILE`, explicit `PLAN_MODE`, explicit `VERIFICATION_RIGOR`, `CONFIDENCE>=50`, `GATE_PASSED=true`, non-empty `SUMMARY`, non-empty `SCENARIOS`, `OPEN_DECISIONS=[]`, `DIFFERENCES_FROM_AGREEMENT` present; `PLAN_MODE=decision_rfc` also requires `ALTERNATIVES`/`DRAWBACKS`; `VERIFICATION_RIGOR=critical_path` requires `PROVABLE_PROPERTIES`) PLUS non-empty `WINNING_MODEL`, an explicit boolean `SYNTHESIZED`, and a `CANDIDATES_COMPARED` array whose length matches the number of candidates the router actually dispatched to it. A router-run `Glob` confirming zero surviving `docs/plans/{plan_file_stem}-candidate-*.md` files is required before accepting either `STATUS` value — a surviving candidate file downgrades the contract to invalid regardless of the agent's own claim (mirrors "APPROVE + critical issues becomes CHANGES_REQUESTED"). `STATUS=FAIL` is a hard stop, same posture as any other malformed write-agent output. |
| doc-syncer | `STATUS=COMPLETE` requires `DOC_LAYERS_EVALUATED` non-empty, a non-empty `SUMMARY`, and at least one entry in `DOC_FILES_UPDATED` or `AUDIT_DOCS_CREATED`; `STATUS=SKIPPED` requires non-empty `SKIP_REASON` and non-empty `SUMMARY` — `DOC_LAYERS_EVALUATED` MAY be empty (fast-path classifier exits before per-layer evaluation when `IMPACT_LEVEL=none` is detected immediately); `STATUS=PARTIAL` requires at least one entry in `DOC_FILES_UPDATED` or `AUDIT_DOCS_CREATED` and at least one layer in `DOC_LAYERS_EVALUATED` — router advances to Memory Update and persists `doc_sync_partial=true` in `results.doc_syncer`; `STATUS=FAIL` blocks workflow. |
| skill-author | `STATUS=COMPLETE` requires non-empty `PROPOSAL_PATH`, non-empty `CANDIDATE_ID`, and non-empty `SUMMARY`; `STATUS=SKIPPED` requires non-empty `SKIP_REASON` and non-empty `SUMMARY` — `SKIPPED` is explicitly a passing state (never blocks workflow advance), matching the doc-syncer `SKIPPED` precedent exactly; `STATUS=FAIL` blocks workflow. |
| plan-gap-reviewer | `PASS` requires `BLOCKING_FINDINGS_COUNT=0` and `REPLAN_NEEDED=false`; `FINDINGS` requires explicit finding buckets and a non-empty `REPLAN_REASON` when blocking findings exist. |
| doubt-verifier (phase:fix-verify) | `FIX_VERDICT: LOAD_BEARING` requires `PRE_FIX_COMMAND`, `PRE_FIX_OUTPUT`, `POST_FIX_COMMAND`, `POST_FIX_OUTPUT`, `SIBLING_SCAN_COMMAND`, `SIBLING_SCAN_RESULT` all non-empty; a `PRE_FIX_OUTPUT` that does not demonstrate the original defect (i.e., is not meaningfully different from `POST_FIX_OUTPUT` in the direction the fix claims to close) is downgraded to `NOT_LOAD_BEARING` regardless of the agent's own claim — mirrors the existing "APPROVE + critical issues becomes CHANGES_REQUESTED" override pattern. This is a distinct contract from `doubt-verifier`'s own `DOUBT_VERDICT` contract (`phase:doubt-verify`), which is unchanged and carries no override row of its own (pre-existing, out of scope for this plan). |

Convergence rule:
- If evidence is incomplete, contradictory, or missing for a required pass path, do not advance the workflow.
- Set the workflow artifact `quality.convergence_state` to `needs_iteration` and stop on the appropriate remediation or clarification gate instead of treating the task as good enough.

### Skill-Distill Approval Flow

**(shared) `_shared/skill-distill-approval.md` §
"Skill-Distill Approval Flow" (the four options (Approve / Approve + register in SKILL_HINTS / Reject / Defer), the exact
`craftflow_skill_promote.py`/`craftflow_skill_ledger.py` invocations, the non-matching-reply
re-ask rule, the JUST_GO carve-out (never auto-Approve), and the `STATUS: SKIPPED`/
`STATUS: FAIL` handling).** Claude Code needs no host-specific additions here — this gate
is presented via `AskUserQuestion` exactly as the shared doc describes; unlike Cursor's
`## 5a. Skill-Distill Gate`, no plain-text-chat fallback or `cursor-wf.json`
`pending_skill_approval` bookkeeping is needed.

### After every agent completion

Pre-check before processing agent output:
- Did the agent address the assigned scope (not a subset or superset)?
- Did tests, builds, or checks referenced in the contract actually run (not merely described)?
- Is follow-up work needed that the agent did not self-remediate?
If any answer is "no" or "unknown", treat as incomplete and apply the fallback validation path below.

0. Capture memory payload first, before validation or task-state mutation.
   - READ-ONLY agents: extract `### Memory Notes (For Workflow-Final Persistence)` immediately after return.
   - WRITE agents: extract `MEMORY_NOTES` from YAML immediately after return.
1. `TaskGet({ taskId })` or `TaskList()` to verify final task state.
2. WRITE agents:
   - They should already have called `TaskUpdate(status="completed")`.
   - Parse YAML before continuing.
3. READ-ONLY agents:
   - Router owns completion fallback for read-only tasks.
   - If the task is still not completed after agent return, router applies fallback `TaskUpdate(status="completed")`.
   - Blockers or findings may change workflow routing, but they never transfer orchestration ownership back to the read-only agent.

4. Memory payload was already captured in step 0:
   - READ-ONLY agents: append extracted notes to the memory task description.
   - WRITE agents: append deferred or supplemental payload needed by the memory task.
5. Update `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.json` with:
   - intent contract fields from planner output when available
   - task ids
   - phase status
   - phase cursor changes only after `phase_exit_gate` passes
   - structured agent results
   - the WRITE agent's `SUMMARY` field, into `results.{agent}.summary` and echoed into the
     matching `agent_completed` event-log entry as `"summary"` — gives a one-line round
     handoff scannable from `status_history`/the event log without re-parsing every
     structured field on every resume
   - scenario evidence grouped by agent
   - plan/design/research file paths
   - capabilities and chosen research backend path when applicable
   - research quality and round metadata when applicable
   - telemetry:
     - task metrics duration when available
     - loop counters
     - verifier workload classification when present
   - quality/convergence state
   - status_history and remediation_history entries when decisions change workflow state
   - pending gate if waiting on user input
6. Persist `[craftflow-internal] memory_task_id: {memory_task_id} wf:{workflow_uuid}` only if it matches the active workflow; first remove any existing matching `[craftflow-internal]` or legacy `[cc10x-internal]` `memory_task_id` line to avoid duplicate orphans.

**Verifier workload telemetry (unchanged):**
- When `integration-verifier` reports a `### Timing & Workload` section, persist:
  - `telemetry.verifier.phase_exit_proof_runs`
  - `telemetry.verifier.extended_audit_runs`
  - `telemetry.verifier.workload_seconds`

Use telemetry to explain latency. Do not use it to auto-reduce verification scope.

### Verifier findings handoff

(shared) `_shared/verifier-handoff.md` §
"Verifier Findings Handoff".

