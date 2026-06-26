### BUILD preparation

1. Read `- Plan:` from `activeContext.md ## References`.
2. If plan path is not `N/A`, `Read(...)` the plan file before creating tasks.
3. Run `plan_trust_gate` before BUILD:
   - `Open Decisions` must be empty or explicitly marked approved.
   - `Differences from agreement` must be present, even if empty.
   - `plan_mode` must be explicit when a plan artifact exists.
   - `verification_rigor` must be explicit when a plan artifact exists.
   - If `plan_mode` is `execution_plan` or `decision_rfc`: every phase in `normalized_phases` must carry non-empty `exit_criteria`, and `intent.acceptance_criteria` must be non-empty. Field presence is not enough — field completeness is required.
   - Cross-check `intent.constraints` against approved decisions. If any approved decision explicitly contradicts an `intent.constraint`, emit NOGO with the contradiction and ask the user to resolve before BUILD starts.
   - If any condition fails, ask for clarification and do not start BUILD.
4. If plan path is `N/A`, assess scope before dispatch:
   - **Trivial** (single concern, one file group, one failure mode) → continue directly to BUILD.
     Heuristic signals: touches 1-2 files, single logical change, one testable outcome, no cross-module wiring.
     [EASY TO MISS: When the task is clearly trivial, do not ask clarifying questions or suggest planning. Execute directly. Analysis paralysis on trivial work is a net negative.]
   - **Non-trivial** (spans multiple independent file groups, has separable concerns, or involves distinct failure modes) → ask: `Plan first (Recommended)` or `Build directly`.
     Heuristic signals: touches 3+ files across different directories, multiple independent concerns that could fail separately, changes to both interface and implementation, or new cross-module dependencies.
   - `Plan first` -> switch to PLAN workflow.
   - `Build directly` -> continue without a plan.
5. If the referenced plan file is missing:
   - Ask: `Build without plan` or `Re-plan first (Recommended)`.
   - `Build without plan` -> continue with `plan:N/A`
   - `Re-plan first` -> switch to PLAN workflow
6. Normalize planner phases into executable `normalized_phases` and initialize `phase_cursor` to the first incomplete phase.
7. Persist the approved `plan_mode` and `verification_rigor` from the planner contract into the workflow artifact.
8. Every normalized phase must carry:
   - `objective`
   - `inputs`
   - `files/surfaces`
   - `expected_artifacts`
   - `required_checks`
   - `checkpoint_type`
   - `exit_criteria`
9. Initialize workflow `proof_status` to `gaps_found` until the current phase is independently verified.
10. Clarify missing requirements before builder only when the plan and memory do not already answer them.
11. Persist pre-answered clarifications in `activeContext.md ## Decisions` using `Build clarification [{topic}]: {answer}`.
12. Builder may execute only the phase at `phase_cursor`.
13. Router handoff for the current BUILD phase must be phase-local:
   - include only the current phase objective, inputs, expected artifacts, required checks, checkpoint type, exit criteria, and approved clarifications still in force
   - include prior-phase detail only when it remains an active blocker, dependency, or unresolved finding
   - do not rehydrate broad historical narrative when the workflow artifact already captures it

### BUILD task graph

BUILD is sequential in v10:
- one approved executable phase at a time
- one builder run for the current phase only
- review, hunt, and verify validate that phase before `phase_cursor` advances
- if phase exit evidence is incomplete, record `partial` or `blocked`, persist state, and stop

```text
TaskCreate({
  subject: "CRAFTFLOW component-builder: Execute phase {phase_id}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-implement\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Execute approved phase\n\nExecute ONLY the phase at phase_cursor. Recover objective, inputs, expected artifacts, required checks, checkpoint type, and exit criteria from the approved phase. Stop if blocked, partial, or proof remains incomplete.",
  activeForm: "Building components"
}) -> builder_task_id

TaskCreate({
  subject: "CRAFTFLOW code-reviewer: Review implementation",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-review\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Review current phase quality\n\nReview only the files and scope of the current phase.",
  activeForm: "Reviewing code"
}) -> reviewer_task_id
TaskUpdate({ taskId: reviewer_task_id, addBlockedBy: [builder_task_id] })

TaskCreate({
  subject: "CRAFTFLOW silent-failure-hunter: Hunt edge cases",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-hunt\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Audit current phase blast radius\n\nFind silent failures and edge cases adjacent to the current phase.",
  activeForm: "Hunting failures"
}) -> hunter_task_id
TaskUpdate({ taskId: hunter_task_id, addBlockedBy: [builder_task_id] })

TaskCreate({
  subject: "CRAFTFLOW integration-verifier: Verify integration",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-verify\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Phase exit verification\n\nRun required checks for the current phase and report whether truths, artifacts, wiring, and phase exit criteria are all satisfied.",
  activeForm: "Verifying integration"
}) -> verifier_task_id
TaskUpdate({ taskId: verifier_task_id, addBlockedBy: [reviewer_task_id, hunter_task_id] })

**Opt-out check:** Before creating the doc-sync task, read `activeContext.md ## Session Settings`. If `DIFF_DRIVEN_DOCS: skip` is present, skip doc-sync task creation entirely and update Memory Update to block on `verifier_task_id` directly instead of `doc_sync_task_id`. Skip the remaining doc-sync task graph below.

TaskCreate({
  subject: "CRAFTFLOW doc-syncer: Sync documentation",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-doc-sync\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Sync docs to reflect diff\n\nAnalyze the diff from this BUILD phase. Classify doc impact. Update documentation across business, technical, and audit layers as applicable. Emit SKIPPED contract immediately if IMPACT_LEVEL=none.",
  activeForm: "Syncing documentation"
}) -> doc_sync_task_id
TaskUpdate({ taskId: doc_sync_task_id, addBlockedBy: [verifier_task_id] })

TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist workflow learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Persist captured Memory Notes\n\nROUTER ONLY: execute inline. Read the workflow artifact and THIS task description payload, persist to .craftflow/state/*.md, then remove the matching [craftflow-internal] memory_task_id line from activeContext.md ## References. Never spawn Agent() for this task.",
  activeForm: "Persisting workflow learnings"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [doc_sync_task_id] })
```

### doc-syncer SKIPPED state

If doc-syncer returns `STATUS: SKIPPED` (i.e., `IMPACT_LEVEL: none`), the router treats it as a passing state — equivalent to `COMPLETE` for workflow-advance purposes. The router must not block Memory Update when the SKIPPED contract is present and `SKIP_REASON` is non-empty. Advance to Memory Update immediately.

### BUILD task graph — fast path

When `build_mode == "fast_path"`, use this reduced task graph instead of the standard BUILD task graph above.

Agents skipped: code-reviewer, silent-failure-hunter, doc-syncer.
Gates surviving: phase_exit_gate, failure_stop_gate, memory_sync_gate.
Gates/rules dropped: 1a-SCOPE rule (no reviewer/hunter findings to scope), doubt_verify_gate.

```text
TaskCreate({
  subject: "CRAFTFLOW component-builder: Execute phase {phase_id}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-implement\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Execute approved phase (fast path)\n\nFAST-PATH BUILD: Execute ONLY the phase at phase_cursor. Recover objective, inputs, expected artifacts, required checks, checkpoint type, and exit criteria from the approved phase. Stop if blocked, partial, or proof remains incomplete.",
  activeForm: "Building components"
}) -> builder_task_id

TaskCreate({
  subject: "CRAFTFLOW integration-verifier: Verify integration (fast path)",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-verify\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Phase exit verification (fast path — no Previous Agent Findings)\n\nFAST-PATH BUILD: Run required checks for the current phase. IMPORTANT: Previous Agent Findings section is OMITTED — no reviewer or hunter ran. Run your own independent scenario coverage. Report whether truths, artifacts, wiring, and phase exit criteria are all satisfied.",
  activeForm: "Verifying integration"
}) -> verifier_task_id
TaskUpdate({ taskId: verifier_task_id, addBlockedBy: [builder_task_id] })

TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist workflow learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Persist captured Memory Notes\n\nROUTER ONLY: execute inline. Read the workflow artifact and THIS task description payload, persist to .craftflow/state/*.md, then remove the matching [craftflow-internal] memory_task_id line from activeContext.md ## References. Never spawn Agent() for this task.",
  activeForm: "Persisting workflow learnings"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [verifier_task_id] })
```

**Verifier PASS on fast path:** Advance `phase_exit_gate` → proceed to memory-finalize.

**Verifier FAIL on fast path:** Do NOT advance phase cursor. Trigger Fast Path Escalation (see `### Fast Path Escalation` below).

### Fast Path Escalation

When `build_mode == "fast_path"` AND integration-verifier returns FAIL:

```text
1. Update artifact: build_mode → "fast_path_escalated", fast_path_escalated → true
2. Append event: {"event":"fast_path_escalated","reason":"verifier FAIL on fast path","ts":"{iso_now}"}
3. Announce: "-> FAST-PATH BUILD [ESCALATED] (verifier FAIL — reviewer + hunter spawned)"
4. Spawn reviewer + hunter in parallel (identical to standard BUILD §5 pattern):

TaskCreate({
  subject: "CRAFTFLOW code-reviewer: Review implementation (escalated)",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:re-review\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Fast-path escalation — verifier FAIL triggered reviewer spawn\n\nReview the files and scope of the current phase. Verifier failed on fast path — this is the first reviewer pass.",
  activeForm: "Reviewing code (escalated)"
}) -> escalated_reviewer_task_id

TaskCreate({
  subject: "CRAFTFLOW silent-failure-hunter: Hunt edge cases (escalated)",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:re-hunt\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Fast-path escalation — verifier FAIL triggered hunter spawn\n\nFind silent failures and edge cases adjacent to the current phase. Verifier failed on fast path — this is the first hunter pass.",
  activeForm: "Hunting failures (escalated)"
}) -> escalated_hunter_task_id

5. Wait for BOTH reviewer + hunter to complete.
6. Build merged findings summary (standard BUILD §5 + §6 pattern from build-workflow.md).
7. Apply 1a-SCOPE rule (RESTORED on escalated path — same threshold as standard parallel review phase):
   - If totalCritical ≥ 1 AND totalHigh ≥ 1 (from escalated reviewer+hunter output) → write `[SCOPE-DECISION-PENDING: wf:{workflow_uuid} reason:{top reason}]` to activeContext.md ## Decisions, ask user, stop. Wait for reply before creating REM-FIX.
   - Otherwise → auto-proceed with ALL_ISSUES (standard rule 1a applies)
   - If totalCritical ≥ 1 AND totalHigh == 0: auto-proceed with ALL_ISSUES (no user scope gate) — this matches the canonical 1a-SCOPE rule: the gate fires only when BOTH signals are present.
8. Create REM-FIX task if needed (standard remediation-and-research.md rules).
9. Re-verify with merged findings. Create a new re-verify task:

TaskCreate({
  subject: "CRAFTFLOW integration-verifier: Re-verify integration (fast-path escalated)",
  description: "wf:{workflow_uuid}\nkind:reverify\norigin:router\nphase:re-verify\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Fast-path escalation re-verify after reviewer+hunter+REM-FIX\n\nESCALATED FAST-PATH RE-VERIFY: Previous Agent Findings section IS required — include the merged reviewer+hunter findings from the escalation round. Run full scenario coverage against the REM-FIX changes. Report whether all issues from the escalated review+hunt pass are resolved.",
  activeForm: "Re-verifying integration (escalated)"
}) -> re_verify_task_id
TaskUpdate({ taskId: re_verify_task_id, addBlockedBy: [remfix_task_id] })

10. Escalation cap enforcement:
    - Re-verify PASS → proceed to memory-finalize
    - Re-verify FAIL → failure_stop_gate fires → stop with BLOCKING: true
    - No further escalation cycles permitted (max one REM-FIX after escalation)
```

**Doc-syncer on escalated path:** SKIP. Fast-path work is unlikely to have doc impact significant enough to warrant it. Do not create a `build-doc-sync` task even after escalation.
