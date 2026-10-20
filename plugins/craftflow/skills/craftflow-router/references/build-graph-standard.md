# BUILD task graph: standard

### BUILD task graph

BUILD is sequential in v10:
- one approved executable phase at a time
- one builder run for the current phase only
- review, hunt, and verify validate that phase before `phase_cursor` advances
- if phase exit evidence is incomplete, record `partial` or `blocked`, persist state, and stop
- after `phase_exit_gate` passes for a phase (standard or fast path) and before `phase_cursor` advances or memory-finalize begins, run the advisory context-boundary check per `references/context-boundary.md`

```text
TaskCreate({
  subject: "CRAFTFLOW component-builder: Execute phase {phase_id}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-implement\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Execute approved phase\n\nExecute ONLY the phase at phase_cursor. Recover objective, inputs, expected artifacts, required checks, checkpoint type, and exit criteria from the approved phase. Stop if blocked, partial, or proof remains incomplete.\n\n## Worktree\nWORKTREE_PATH: {worktree_path | 'main tree'}\nWhen WORKTREE_PATH is a real path (not 'main tree'): all file reads, edits, and writes must use paths rooted at WORKTREE_PATH. Do not modify files outside WORKTREE_PATH during this phase.",
  activeForm: "Building components"
}) -> builder_task_id

**Arch Lint (advisory):** before dispatching code-reviewer, run the check per `references/harness-self-checks.md § Arch Lint` and fold any findings into its dispatch prompt.

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
- `STATUS: COMPLETE` → apply the Skill-Distill Approval Flow (§ 8, `_shared/skill-distill-approval.md`) before Memory Update proceeds.
- `STATUS: SKIPPED` → passing state — proceed straight to Memory Update, no `AskUserQuestion`.
- `STATUS: FAIL` or no return (stuck/timeout) → `skill-author` is NOT a `kind:remfix` origin; do not create a REM-FIX task and do not block the chain. Log an event describing the failure, leave the candidate's ledger status unchanged (flagged for retry next time the gate fires), and proceed straight to Memory Update.

**Feature Backlog (advisory):** before creating the Memory Update task below, when `integration-verifier` returned PASS on the final phase, run the complete + VCR calls per `references/harness-self-checks.md § Feature Backlog`.

**Clean-State Check (advisory):** when `worktree_mode != "auto_created"` (no worktree exists — `references/worktree.md` covers the worktree-exists case) and `integration-verifier` returned PASS on the final phase, also run the clean-state check per `references/harness-self-checks.md § Clean-State Check` before creating the Memory Update task below.

```text
TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist workflow learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Persist captured Memory Notes. ROUTER ONLY: execute inline. Follow references/memory-finalize.md § Memory Update task instructions (read it now if not in context). Never spawn Agent() for this task.",
  activeForm: "Persisting workflow learnings"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [chain_tail_task_id] })
```

`chain_tail_task_id` at this point is `skill_distill_task_id` if the Skill-Distill Gate fired, else `learn_distill_task_id` if the Learn-Distill Gate fired, else `doc_sync_task_id` (or `verifier_task_id` if doc-sync itself was skipped) — i.e. `addBlockedBy: [skill_distill_task_id]` in the common case where both gates fire.

### doc-syncer SKIPPED state

If doc-syncer returns `STATUS: SKIPPED` (i.e., `IMPACT_LEVEL: none`), the router treats it as a passing state — equivalent to `COMPLETE` for workflow-advance purposes. The router must not block Memory Update when the SKIPPED contract is present and `SKIP_REASON` is non-empty. Advance to Memory Update immediately.

