### BUILD preparation

1. Read `- Plan:` from `activeContext.md ## References`.
2. If plan path is not `N/A`, `Read(...)` the plan file before creating tasks.
2a. Run the feature-backlog activate call per `references/harness-self-checks.md § Feature Backlog` when the workflow artifact's `plan_file` matches a `not_started` backlog entry.
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

**BUILD task graph, doc-syncer state, fast path and escalation:** read the graph file the Read Map names for `build_mode` (`build-graph-standard.md` or `build-graph-fast.md`); after a fast-path verifier FAIL read `build-fast-path-escalation.md`.


### Fast Path Detection

Before BUILD child task creation, read `references/fast-path.md` (contains canonical keyword table and escalation cap).

Perform `risk_keyword_scan`:
1. Scan request text (case-insensitive) against all keyword groups in `references/fast-path.md → risk_keyword_scan — Keyword Table`
2. Collect matched keywords into `fast_path_risk_signals`
3. Assign `build_mode`:
   - `fast_path_risk_signals == []` → `build_mode = "fast_path"`
   - `fast_path_risk_signals != []` → `build_mode = "standard"`
4. Write to workflow artifact: `build_mode`, `fast_path_risk_signals`, `fast_path_escalated: false`
5. Announce routing decision:
   - Fast path: `-> FAST-PATH BUILD (no risk signals)`
   - Standard: `-> FULL BUILD (risk signals: {matched keywords})`

### Worktree Isolation (BUILD Default)

Every new BUILD workflow attempts to isolate file writes in a dedicated git worktree:

1. At BUILD start, `PROJECT_ROOT` was already resolved once by `SKILL.md → ## 0. Resolve Project Root`
   at session start — reuse it directly. Do not re-run `git rev-parse --show-toplevel` or
   invoke the workspace-root resolver script a second time.

   [EASY TO MISS: if `## 0.` fell back to `NO_REPO_FOUND`/`RESOLVE_SCRIPT_ERROR` (no git-repo
   children under cwd, or the resolver script itself failed), `PROJECT_ROOT` is already set to
   `$(pwd)` from that fallback — Worktree Isolation still proceeds with the `mkdir`/`git
   worktree add` block below using that same fallback value, exactly like the single-repo
   path. There is no separate NO_REPO_FOUND branch here anymore; `## 0.` already handled it.]

   With `PROJECT_ROOT` reused from `## 0.` above, capture both commands' output and exit code,
   mirroring the exit-code handling of the finalize call in `references/worktree.md` —
   never leave either command's exit code unchecked:
   ```bash
   MKDIR_OUTPUT=$(mkdir -p "$PROJECT_ROOT/.claude/worktrees" 2>&1)
   MKDIR_EXIT=$?
   WORKTREE_ADD_OUTPUT=$(git worktree add "$PROJECT_ROOT/.claude/worktrees/{worktree_dir}" -b {worktree_branch} 2>&1)
   WORKTREE_ADD_EXIT=$?
   ```
   where `worktree_dir` and `worktree_branch` come from the `craftflow_workflow_id.py` helper
   output (see step 1 of **Parent workflow creation** in `workflow-start.md`).
   The trailing 8-hex suffix in both names ties the worktree back to the workflow id,
   guaranteeing concurrent same-feature workflows always get distinct dirs/branches.

   - **If `MKDIR_EXIT != 0` OR `WORKTREE_ADD_EXIT != 0`**: proceed to step 3 ("On failure") below.
     Use `$MKDIR_OUTPUT` as the `{error}` value if `MKDIR_EXIT != 0`; otherwise use
     `$WORKTREE_ADD_OUTPUT`.
   - **Otherwise** (`MKDIR_EXIT == 0` AND `WORKTREE_ADD_EXIT == 0`): proceed to step 2 ("On
     success") below.

2. On success:
   - Set `worktree_mode: "auto_created"` in the workflow artifact
   - Set `worktree_path: "{project_root}/.claude/worktrees/{worktree_dir}"` (absolute; `worktree_dir` from the helper)
   - Set `worktree_branch: "{worktree_branch}"` (from the helper)
   - Add `## Worktree` section to every builder and verifier task description:
     ```
     ## Worktree
     WORKTREE_PATH: {worktree_path}
     All file reads, edits, and writes must use paths rooted at WORKTREE_PATH.
     Do not modify files outside WORKTREE_PATH during this BUILD phase.
     ```

3. On failure (any git error — shallow clone, detached HEAD, path conflict):
   - Set `worktree_mode: null`
   - Append `{"event":"worktree_fallback","reason":"{error}"}` to the event log
   - Continue with main tree — never block a workflow over worktree failure
   - Omit the `## Worktree` section from task descriptions when in fallback mode

