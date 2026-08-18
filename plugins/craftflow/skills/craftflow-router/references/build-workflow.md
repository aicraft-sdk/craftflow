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
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-implement\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Execute approved phase\n\nExecute ONLY the phase at phase_cursor. Recover objective, inputs, expected artifacts, required checks, checkpoint type, and exit criteria from the approved phase. Stop if blocked, partial, or proof remains incomplete.\n\n## Worktree\nWORKTREE_PATH: {worktree_path | 'main tree'}\nWhen WORKTREE_PATH is a real path (not 'main tree'): all file reads, edits, and writes must use paths rooted at WORKTREE_PATH. Do not modify files outside WORKTREE_PATH during this phase.",
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
```

Track `chain_tail_task_id` starting as `doc_sync_task_id` (or `verifier_task_id` directly if `DIFF_DRIVEN_DOCS: skip` skipped doc-sync entirely per the opt-out check above). Both gates below update it in place before Memory Update is created.

#### Learn-Distill Gate

Fixes the pre-existing dead-wiring gap: `learn-distill` was already documented in `SKILL.md`'s dispatcher table and Effort Dispatch Rule list, but had no `TaskCreate` anywhere in this file — it has never fired once in this repo's history. `learn-distill` runs at the end of BUILD (standard and fast-path) ONLY when `remediation_history` in the workflow artifact is non-empty (at least one remediation cycle already ran this workflow).

**Gate check:** Read `remediation_history` from the workflow artifact.

- Empty → skip `learn-distill` task creation entirely. `chain_tail_task_id` stays unchanged.
- Non-empty →

```text
TaskCreate({
  subject: "CRAFTFLOW learn-distiller: Distill recurring failure signatures",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:learn-distill\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Non-empty remediation_history triggered the recurrence gate\n\nRun `python3 {plugin_root}/scripts/craftflow_learn_scan.py` via Bash, read this workflow's remediation_history and event log, and distill any recurring failure signature into project/patterns.md ## Common Gotchas per your own agent contract. Emit a Router Contract when done.",
  activeForm: "Distilling recurring failure signatures"
}) -> learn_distill_task_id
TaskUpdate({ taskId: learn_distill_task_id, addBlockedBy: [chain_tail_task_id] })
```

Set `chain_tail_task_id = learn_distill_task_id`. After `learn-distiller` completes, append `learn_distilled` to the event log.

#### Skill-Distill Gate

A separate, more specific gate than Learn-Distill's above: it fires only when the skill-candidate ledger already has at least one gate-eligible candidate not already `promoted`/`rejected` — not on every workflow, and independent of whether the Learn-Distill Gate fired.

**Gate check** (run once, regardless of whether the Learn-Distill Gate above fired):

```bash
python3 {plugin_root}/scripts/craftflow_skill_ledger.py --query --ledger .craftflow/state/project/skill-candidates.json
```

Parse the returned `candidates` array. A candidate is eligible for this gate when BOTH:
- `distinct_workflows >= 2` (the same threshold `gate_eligible()` applies internally for `--backtest`; plain `--query` does not pre-filter, so the router applies this filter itself)
- `status == "candidate"` (not `"proposed"`, `"promoted"`, or `"rejected"` — a candidate already `"proposed"` is mid-review and must not be re-proposed). `status` is one of `candidate|proposed|promoted|rejected` only — "stale" is never an assignable status value; stale `candidate` entries (>90 days, no new evidence) are pruned (deleted outright) by `--prune`, never transitioned to a "stale" status.

- Zero candidates satisfy both → skip `skill-distill` task creation entirely. `chain_tail_task_id` stays unchanged.
- One or more satisfy both → pick the one with the highest `distinct_workflows` (ties broken by earliest `first_seen`) and create:

```text
TaskCreate({
  subject: "CRAFTFLOW skill-author: Propose skill from candidate {candidate_id}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:skill-distill\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Ledger has a gate_eligible candidate not already promoted/rejected\n\nCandidate id: {candidate_id}\n\nRead this candidate from the ledger, apply the anti-slop rubric, and stage a proposal or emit STATUS: SKIPPED per your own agent contract.",
  activeForm: "Distilling a project skill proposal"
}) -> skill_distill_task_id
TaskUpdate({ taskId: skill_distill_task_id, addBlockedBy: [chain_tail_task_id] })
```

Set `chain_tail_task_id = skill_distill_task_id`. After `skill-author` returns:
- `STATUS: COMPLETE` → apply the Skill-Distill Approval Flow (`SKILL.md` § 8) before Memory Update proceeds.
- `STATUS: SKIPPED` → passing state — proceed straight to Memory Update, no `AskUserQuestion`.
- `STATUS: FAIL` or no return (stuck/timeout) → `skill-author` is NOT a `kind:remfix` origin; do not create a REM-FIX task and do not block the chain. Log an event describing the failure, leave the candidate's ledger status unchanged (flagged for retry next time the gate fires), and proceed straight to Memory Update.

```text
TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist workflow learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Persist captured Memory Notes\n\nROUTER ONLY: execute inline. Read the workflow artifact and THIS task description payload, persist to .craftflow/state/*.md,\nBefore persisting each MEMORY_NOTES field, resolve its destination file and section from SKILL.md Section 13's routing table, then obtain the FULL destination file content via:\n  python3 {plugin_root}/scripts/craftflow_state_query.py <destination_file_path> --mode full\n(never a raw Read -- the destination files are exactly the .craftflow/state/**\nfiles the state-read-compaction guard may deny once oversized; --mode full is\nthis script's byte-identical full-content path) and pipe that output into:\n  python3 {plugin_root}/scripts/craftflow_memory_merge.py\nwith a JSON payload of {"file_text": "<full destination file content>", "section": "<target section, e.g. Common Gotchas>", "notes": [...], "retractions": [], "max_bullets": <cap per routing table, e.g. 60 for patterns -> project/patterns.md ## Common Gotchas; omit for learnings -> workflows/{workflow_uuid}/activeContext.md ## Learnings and verification -> workflows/{workflow_uuid}/progress.md ## Verification, which are workflow-scoped and need no cap>}\non stdin; use the FULL stdout as the replacement file content -- section-anchored mode returns the whole file with only the target section's body replaced, not just a section body.\nOmit max_bullets entirely (do not pass it) if the destination file's memory contract sections are known to still be structurally corrupted; do not silently evict existing content when a section's heading structure is broken (see Phase 3 of this plan for the corrupted-file repair).\nConfidence <0.7 notes are dropped. Retractions remove matching bullets. New bullets get a (conf: x) suffix.\nthen remove the matching [craftflow-internal] memory_task_id line from activeContext.md ## References. Never spawn Agent() for this task.",
  activeForm: "Persisting workflow learnings"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [chain_tail_task_id] })
```

`chain_tail_task_id` at this point is `skill_distill_task_id` if the Skill-Distill Gate fired, else `learn_distill_task_id` if the Learn-Distill Gate fired, else `doc_sync_task_id` (or `verifier_task_id` if doc-sync itself was skipped) — i.e. `addBlockedBy: [skill_distill_task_id]` in the common case where both gates fire.

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
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-implement\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Execute approved phase (fast path)\n\nFAST-PATH BUILD: Execute ONLY the phase at phase_cursor. Recover objective, inputs, expected artifacts, required checks, checkpoint type, and exit criteria from the approved phase. Stop if blocked, partial, or proof remains incomplete.\n\n## Worktree\nWORKTREE_PATH: {worktree_path | 'main tree'}\nWhen WORKTREE_PATH is a real path (not 'main tree'): all file reads, edits, and writes must use paths rooted at WORKTREE_PATH. Do not modify files outside WORKTREE_PATH during this phase.",
  activeForm: "Building components"
}) -> builder_task_id

TaskCreate({
  subject: "CRAFTFLOW integration-verifier: Verify integration (fast path)",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-verify\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Phase exit verification (fast path — no Previous Agent Findings)\n\nFAST-PATH BUILD: Run required checks for the current phase. IMPORTANT: Previous Agent Findings section is OMITTED — no reviewer or hunter ran. Run your own independent scenario coverage. Report whether truths, artifacts, wiring, and phase exit criteria are all satisfied.",
  activeForm: "Verifying integration"
}) -> verifier_task_id
TaskUpdate({ taskId: verifier_task_id, addBlockedBy: [builder_task_id] })
```

Track `chain_tail_task_id` starting as `verifier_task_id` (no doc-sync on fast path). Apply the SAME Learn-Distill Gate and Skill-Distill Gate described above in the standard BUILD task graph — identical gate checks, identical `phase:learn-distill` / `phase:skill-distill` `TaskCreate` shape, identical `chain_tail_task_id` update rule — the only difference is the starting chain tail (`verifier_task_id` here vs `doc_sync_task_id` in standard BUILD). On a clean fast-path pass, `remediation_history` is empty so the Learn-Distill Gate never fires (see `fast-path.md`'s own Learn-Distill Gate note); the Skill-Distill Gate is independent of `remediation_history` and can still fire on a clean fast-path pass if the ledger has an eligible candidate. On an escalated fast path (verifier FAIL → reviewer/hunter/REM-FIX → re-verify), `remediation_history` is populated and `learn-distill` runs exactly like standard BUILD.

```text
TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist workflow learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Persist captured Memory Notes\n\nROUTER ONLY: execute inline. Read the workflow artifact and THIS task description payload, persist to .craftflow/state/*.md,\nBefore persisting each MEMORY_NOTES field, resolve its destination file and section from SKILL.md Section 13's routing table, then obtain the FULL destination file content via:\n  python3 {plugin_root}/scripts/craftflow_state_query.py <destination_file_path> --mode full\n(never a raw Read -- the destination files are exactly the .craftflow/state/**\nfiles the state-read-compaction guard may deny once oversized; --mode full is\nthis script's byte-identical full-content path) and pipe that output into:\n  python3 {plugin_root}/scripts/craftflow_memory_merge.py\nwith a JSON payload of {"file_text": "<full destination file content>", "section": "<target section, e.g. Common Gotchas>", "notes": [...], "retractions": [], "max_bullets": <cap per routing table, e.g. 60 for patterns -> project/patterns.md ## Common Gotchas; omit for learnings -> workflows/{workflow_uuid}/activeContext.md ## Learnings and verification -> workflows/{workflow_uuid}/progress.md ## Verification, which are workflow-scoped and need no cap>}\non stdin; use the FULL stdout as the replacement file content -- section-anchored mode returns the whole file with only the target section's body replaced, not just a section body.\nOmit max_bullets entirely (do not pass it) if the destination file's memory contract sections are known to still be structurally corrupted; do not silently evict existing content when a section's heading structure is broken (see Phase 3 of this plan for the corrupted-file repair).\nConfidence <0.7 notes are dropped. Retractions remove matching bullets. New bullets get a (conf: x) suffix.\nthen remove the matching [craftflow-internal] memory_task_id line from activeContext.md ## References. Never spawn Agent() for this task.",
  activeForm: "Persisting workflow learnings"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [chain_tail_task_id] })
```

**Verifier PASS on fast path:** Advance `phase_exit_gate` → run the Learn-Distill Gate and Skill-Distill Gate → proceed to memory-finalize.

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
    - Re-verify PASS → set `chain_tail_task_id = re_verify_task_id`, then run the Learn-Distill Gate and Skill-Distill Gate (identical gate checks, identical `TaskCreate` shape, identical `chain_tail_task_id` update rule as described in `### BUILD task graph — fast path` above — `remediation_history` is now populated by this escalation cycle, so the Learn-Distill Gate fires) BEFORE creating the Memory Update task, then proceed to memory-finalize
    - Re-verify FAIL → failure_stop_gate fires → stop with BLOCKING: true
    - No further escalation cycles permitted (max one REM-FIX after escalation)
```

**Doc-syncer on escalated path:** SKIP. Fast-path work is unlikely to have doc impact significant enough to warrant it. Do not create a `build-doc-sync` task even after escalation.
