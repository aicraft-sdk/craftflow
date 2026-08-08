## 2a. Workflow Artifact And Hook Policy

CRAFTFLOW durable orchestration state lives in:

```text
.craftflow/state/workflows/{workflow_uuid}.json
```

Artifact schema must include:
 - `workflow_uuid`
- `workflow_id`
- `workflow_type`
- `state_root`
- `user_request`
- `plan_file`
- `design_file`
- `research_files`
- `approved_decisions`
- `intent`
- `capabilities`
- `phase_cursor`
- `normalized_phases`
- `research_rounds`
- `research_backend_history`
- `research_quality`
- `task_ids`
- `phase_status`
- `results`
- `evidence`
- `telemetry`
- `quality`
- `planning_review_runs`
- `planning_review_findings`
- `planning_review_status`
- `build_mode`
- `fast_path_risk_signals`
- `fast_path_escalated`
- `memory_notes`
- `pending_gate`
- `status_history`
- `remediation_history`
- `created_at`
- `updated_at`

Rules:
- Router creates the workflows directory before the first workflow artifact write.
- Router writes or updates the artifact after workflow creation, every agent completion, every remediation decision, every clarification answer, every phase completion, every blocking stop, and memory finalization.
- Resume uses task metadata first, then workflow artifact, then memory markdown.
- Verifier handoff and memory finalization read structured data from the workflow artifact, not transient conversation recovery.
- The workflow UUID is generated independently of Claude task ids and is the canonical workflow identifier everywhere in v10.
- `workflow_id` remains as a compatibility alias and must equal `workflow_uuid` in new artifacts.
- `state_root` must equal `.craftflow/state`.
- `phase_cursor` points at the only BUILD phase that may run next.
- `normalized_phases` stores planner-approved executable phases with:
  - `phase_id`
  - `title`
  - `objective`
  - `files`
  - `checks`
  - `exit_criteria`
- Bright Data MCP and Octocode MCP are optional accelerators. Base CRAFTFLOW installs must continue to work with built-in Claude Code tools only.
- When optional user-configured Claude Code MCP servers are available, use the server names `brightdata` and `octocode` so the research agents can auto-detect them without prompt edits.
- `capabilities` records the session-level research backend availability model:
  - `brightdata_available`
  - `octocode_available`
  - `websearch_available`
  - `webfetch_available`
- `results.research` must be structured as `web`, `github`, and `synthesis`.
- `intent` stores the durable spec header for the workflow:
  - `goal`
  - `non_goals`
  - `constraints`
  - `acceptance_criteria`
  - `open_decisions`
- `approved_decisions` stores decisions explicitly approved by the user or already fixed in the saved plan.
- `evidence` stores proof-of-work grouped by agent:
  - `builder`
  - `investigator`
  - `reviewer`
  - `hunter`
  - `verifier`
- `quality` stores convergence state:
  - `confidence`
  - `evidence_complete`
  - `scenario_coverage`
  - `research_quality`
  - `convergence_state`
- PLAN-local fresh review tracking stores:
  - `planning_review_runs`
  - `planning_review_findings`
  - `planning_review_status`
- Fast-path routing fields (BUILD only; null/empty for non-BUILD workflows):
  - `build_mode`: `"fast_path"` | `"fast_path_escalated"` | `"standard"` | `null`
  - `fast_path_risk_signals`: matched risk keywords (`[]` when fast path taken)
  - `fast_path_escalated`: `false` | `true`
  - Migration note: Artifacts created before fast-path was implemented have `build_mode: null`. On resume, treat `build_mode: null` as `"standard"` — the standard BUILD chain applies.
- `telemetry` is informational only and must never drive routing decisions:
  - `task_metrics_available`
  - `workflow_wall_clock_seconds`
  - `agent_wall_clock_seconds`
  - `loop_counts`
  - `verifier`
- `telemetry.agent_wall_clock_seconds` stores per-agent wall-clock timings when task metrics or explicit telemetry are available:
  - `builder`
  - `investigator`
  - `reviewer`
  - `hunter`
  - `verifier`
  - `planner`
- `telemetry.effort` stores per-agent effort profile assigned at dispatch:
  - `builder`
  - `reviewer`
  - `hunter`
  - `verifier`
  - `planner`
  - `investigator`
  - `doc_syncer`
- `telemetry.loop_counts` stores:
  - `re_review`
  - `re_hunt`
  - `re_verify`
- `telemetry.verifier` stores:
  - `phase_exit_proof_runs`
  - `extended_audit_runs`
  - `workload_seconds`
- `telemetry.verifier.workload_seconds` stores:
  - `tests`
  - `build`
  - `scan`
  - `reconcile`
  - `reasoning`
- `pending_gate` is required whenever BUILD/PLAN/DEBUG is waiting on user clarification, scope selection, or persistence repair.
- BUILD's worktree merge-safety guard uses 6 `pending_gate` values (canonical logic lives in
  `skills/craftflow-router/SKILL.md` → "### Worktree Isolation (BUILD Default)" step 4; this is a
  summary, not a duplicate of the logic):
  - `worktree_merge_conflict` — a real textual git merge conflict. Ask the user to resolve it in
    the main tree, then resume.
  - `worktree_merge_locked` — another BUILD workflow currently holds
    `.claude/worktrees/.merge.lock`, OR the lock's metadata could not be read due to a real
    filesystem/permission error, OR the local `git worktree list` check itself failed (distinct
    conditions — see step 4c). Wait for the other workflow to finish, fix the underlying
    filesystem/permission/environment issue, or confirm the lock is actually dead before manually
    clearing it, then resume.
  - `worktree_dirty_main_tree` — the main tree had uncommitted changes at merge time that this
    BUILD did not make. The user must commit, stash, or otherwise resolve them, then resume.
  - `worktree_copy_fallback_failed` — the copy-fallback script (used when `git merge` reports
    "Already up to date" because the worktree's changes were never committed) failed while
    applying the worktree's uncommitted changes to the main tree; earlier files in the same run
    may already have landed. The user must inspect `git status --porcelain` in the main tree and
    resolve the underlying issue, then resume.
  - `worktree_merge_unrecognized_failure` — `git merge` exited non-zero with output that is
    neither "Already up to date" nor a conflict marker (e.g. an invalid ref, unrelated
    histories). Nothing was merged; the worktree and branch are left untouched. The user must
    inspect the error and resolve the underlying issue, then resume.
  - `worktree_cleanup_failed` — after a successful merge or copy-fallback, either
    `git worktree remove --force` or `git branch -d` failed (e.g. the worktree is busy/locked, or
    the branch is not fully merged — a real correctness signal). The user must resolve the
    underlying issue, then resume to retry cleanup.
- `status_history` and `remediation_history` are append-only summaries of major router decisions.

v10 router gates:
- `plan_trust_gate`
- `phase_exit_gate`
- `failure_stop_gate`
- `memory_sync_gate`
- `skill_precedence_gate`

These are router-owned checks, not advisory hints.

Workflow event log:
- For every workflow, keep a lightweight append-only companion file:

```text
.craftflow/state/workflows/{workflow_uuid}.events.jsonl
```

- Append event objects with at least:
  - `ts`
  - `wf`
  - `event`
  - `phase`
  - `task_id`
  - `agent`
  - `decision`
  - `reason`
- Optionally append:
  - `duration_seconds`
  - `work_category`
  - `details`
  - `effort`
- Event types:
  - `workflow_started`
  - `agent_started`
  - `agent_completed`
  - `contract_parsed`
  - `remediation_created`
  - `scope_decision_requested`
  - `scope_decision_resolved`
  - `memory_finalized`
  - `workflow_completed`
  - `workflow_failed`
  - `contract_invalid`
  - `learn_distilled`
  - `skill_candidates_observed`
  - `skill_candidates_pruned`
  - `skill_proposed`
  - `skill_promoted`
  - `skill_rejected`
  - `skill_distill_skipped`
  - `skill_distill_failed`

Hook policy:
- CRAFTFLOW plugin hooks live in the plugin bundle under `hooks/hooks.json` and should stay minimal:
  - `PreToolUse` for protected writes (Edit, Write, and Bash matchers; Read events are not intercepted) and for destructive Bash command denial (in-cwd and cwd/worktree-escaping)
  - `SessionStart` for resume context (fires on startup|resume|compact)
  - `PostToolUse` for workflow artifact integrity audit and memory placeholder restore (defensive, fires on Edit/Write)
  - `TaskCompleted` for task metadata checks (enforced: block mode)
  - `PostCompact` for compaction event capture in workflow event log (audit only)
  - `SubagentStop` for agent contract presence audit and memory placeholder restore
  - `PreCompact` for workflow state snapshot before compaction (persistence only)
  - `Stop` for workflow state snapshot and memory placeholder restore on session stop (never blocks)
- `StopFailure` for API error logging to workflow event log (async, telemetry only)
- `InstructionsLoaded` for instruction file load audit trail (async, telemetry only)
- Hook modes: `memoryWrites`, `protectedWrites`, `bashDestructiveTraversal`, and `taskMetadata` are enforced in block mode; all other hooks operate in audit mode. Each of the first three enum-validates its `hook-mode.json` value and fails closed (block) on a missing or malformed config. Do not rely on hooks as the only source of truth; the router still owns orchestration decisions.
- Repo-local `.claude/settings.json` is not part of the shipped CRAFTFLOW product.
- Optional accelerator MCPs are user-configured in Claude Code. CRAFTFLOW assumes the names `brightdata` and `octocode` if they are available, but must degrade to built-in research paths when they are absent.
