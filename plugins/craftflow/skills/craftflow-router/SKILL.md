---
name: craftflow-router
description: |
  THE ONLY ENTRY POINT FOR CRAFTFLOW. Activate this skill for build, debug, review, and plan requests.

  Use when the user asks to implement, fix, review, plan, test, refactor, or continue code work.

  Trigger keywords: build, implement, create, write, add, review, audit, debug, fix, error, bug, broken, plan, design, architect, spec, brainstorm, test, refactor, optimize, update, change, research, craftflow, craftflow.

  CRITICAL: Route and execute immediately. Do not stop at describing capabilities.
---

# craftflow Router

**Runtime contract only.** v10 restores trust-first orchestration: route intent, hydrate workflow state, write workflow artifacts, execute the task graph, validate agent output, and fail closed on ambiguity, skipped work, or missing persistence.

## 1. Intent Routing

Route using the first matching signal:

| Priority | Signal | Keywords | Workflow | Chain |
|----------|--------|----------|----------|-------|
| 1 | ERROR | error, bug, fix, broken, crash, fail, debug, troubleshoot, issue | DEBUG | bug-investigator -> code-reviewer -> integration-verifier |
| 2 | PLAN | plan, design, architect, roadmap, strategy, spec, brainstorm | PLAN | brainstorming -> planner -> bounded fresh review loop |
| 3 | REVIEW | review, audit, analyze, assess, "is this good" | REVIEW | code-reviewer |
| 4 | DEFAULT | Everything else | BUILD | fast path: builder -> verifier -> memory (default); full chain: builder -> [code-reviewer \|\| silent-failure-hunter] -> verifier -> memory (when risk keywords match) |

Rules:
- NEVER use Claude Code's native plan mode (EnterPlanMode). Craftflow owns planning. All "plan", "design", "architect", "brainstorm" requests route to the Craftflow PLAN workflow — not to the built-in plan mode tool. EnterPlanMode bypasses Craftflow orchestration, memory, workflow artifacts, and verification entirely.
- ERROR always wins over BUILD.
- REVIEW is advisory only. Never let REVIEW create code-changing tasks.
- BUILD uses fast path (builder → verifier → memory) by default when no risk keywords are detected in the request. Full chain (builder → reviewer → hunter → verifier → memory) is used when risk keywords match. See `references/fast-path.md` for detection rules.
- Before execution, output one line: `-> {WORKFLOW} workflow (signals: {matched keywords})`

## 0. Resolve Project Root

[EASY TO MISS: `## 0.` sits between `## 1.` and `## 2.` intentionally — a literal "0"
heading, not a full renumber of `## 2.`-`## 7.`, matching the approved design's own "§0"
terminology and minimizing blast radius on an already 1000+-line file.]

1. At the start of every workflow (PLAN/DEBUG/REVIEW/BUILD), before any
   `.craftflow/state/...` path is touched, resolve the project root:
   ```bash
   PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
   TOPLEVEL_EXIT=$?
   ```
   [EASY TO MISS: Use `git rev-parse --show-toplevel` for the absolute path — never a bare
   `~/.claude/worktrees/` because `~` may not expand correctly in all shell contexts, and a
   relative path resolves against cwd, not the project root.]

   - **If `TOPLEVEL_EXIT == 0`**: `PROJECT_ROOT` is set — this is the unchanged single-repo
     path. `PROJECT_ROOT` is now set for the remainder of this session — every later step in
     this document (memory load, workflow-artifact creation, resume, and — for BUILD only —
     worktree creation) consumes this same value. Skip step 1a.
   - **If `TOPLEVEL_EXIT != 0`**: cwd itself is not a git repo. This happens when a session is
     launched at a multi-repo workspace root (a directory that is not itself a git repo but
     contains several independently git-initialized nested repos, e.g. `ai-infra/` containing
     `ai-platform-core/`, `genai-platform-dev/`, etc.). Run **step 1a** below before deciding
     whether to proceed.

   **1a. Multi-repo workspace root resolution** (only reached when `TOPLEVEL_EXIT != 0`):
   ```bash
   CRAFTFLOW_INSTALL=$(python3 -c "
   import json, pathlib
   reg = json.loads(pathlib.Path.home().joinpath('.claude/plugins/installed_plugins.json').read_text())
   print(reg['plugins']['craftflow@craftflow'][0]['installPath'])
   ")
   CRAFTFLOW_INSTALL_EXIT=$?
   RESOLVE_RESULT=$(python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_resolve_workspace_root.py" \
     --cwd "$(pwd)" \
     --request "USER_REQUEST_SHELL_ESCAPED")
   RESOLVE_EXIT=$?
   ```
   (A non-zero `CRAFTFLOW_INSTALL_EXIT` here is not handled as a separate branch — it surfaces
   downstream as a non-zero `RESOLVE_EXIT` from the next command, an empty/unusable
   `$CRAFTFLOW_INSTALL` path, which the existing `RESOLVE_EXIT != 0` handling below already
   catches.)
   Replace `USER_REQUEST_SHELL_ESCAPED` with the actual user request, properly shell-quoted
   (same convention as **Parent workflow creation** step 1). The script never mutates git or
   filesystem state — it only reads cwd's immediate child directories and runs non-mutating
   `git rev-parse --show-toplevel` calls.

   - **If `RESOLVE_EXIT != 0`** (the script itself could not complete the scan, e.g. cwd
     unreadable): do not parse `$RESOLVE_RESULT`. Treat identically to `NO_REPO_FOUND` below.
   - **Otherwise**, parse the outcome:
     ```bash
     RESOLVE_OUTCOME=$(printf '%s' "$RESOLVE_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['outcome'])")
     ```
     - **`DETERMINISTIC`** (exactly one candidate nested repo exists, or the request text
       uniquely names one among several):
       ```bash
       PROJECT_ROOT=$(printf '%s' "$RESOLVE_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['project_root'])")
       ```
       `PROJECT_ROOT` is now set for the remainder of this session — every later step in this
       document (memory load, workflow-artifact creation, resume, and — for BUILD only —
       worktree creation) consumes this same value.
     - **`AMBIGUOUS`** (2+ candidate nested repos exist and the request text does not uniquely
       name one): ask the user once via `AskUserQuestion`, with one option per path in the
       `candidates` array from `$RESOLVE_RESULT` (optionally enrich each option's label with
       that repo's `git -C <candidate> log -1 --format=%s` first line, if available). Set
       `PROJECT_ROOT` to the chosen candidate's absolute path. `PROJECT_ROOT` is now set for
       the remainder of this session — every later step in this document (memory load,
       workflow-artifact creation, resume, and — for BUILD only — worktree creation) consumes
       this same value.
       [EASY TO MISS: this `AskUserQuestion` gate is NEVER auto-defaulted under `JUST_GO=true`
       (§ 2 `JUST_GO` rule) — cross-repo routing has no safe "recommended" default the way an
       ordinary implementation-choice gate does, so it is treated the same as an unresolved
       plan **Open Decision**: always stop and ask, even in `JUST_GO` mode.]
     - **`NO_REPO_FOUND`** (or `RESOLVE_EXIT != 0` above): no git-repo children exist under
       cwd at all (or the resolver script itself could not complete the scan). Set
       `PROJECT_ROOT=$(pwd)` as the fallback — this raw-cwd value still resolves correctly for
       the intended purpose; the single-repo case was already handled above, and this branch
       only covers a workspace root with no nested git repos, or a scan failure, where cwd is
       the only available candidate.
       [EASY TO MISS: `## 0.` runs BEFORE `workflow_uuid` is minted (minted later, at
       **Parent workflow creation** in `## 6.`), so no per-workflow event log
       (`$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.events.jsonl`) can exist yet
       at this point — it is filename-keyed on `workflow_uuid`, which literally cannot exist
       yet. Do not append an event to "the event log" here; there is nothing to append to.
       Instead, keep `reason:"NO_REPO_FOUND"` (resolver returned that outcome) or
       `reason:"RESOLVE_SCRIPT_ERROR"` (`RESOLVE_EXIT != 0`) in-session, and fold it into
       `## 6.`'s own initial `status_history`/event-log write once the workflow artifact is
       created — e.g.
       `{"event":"project_root_resolution_fallback","reason":"NO_REPO_FOUND"|"RESOLVE_SCRIPT_ERROR"}`
       alongside `workflow_started`. This is necessarily a best-effort, undurable signal at
       this early stage: `PROJECT_ROOT` is already correctly resolved via the raw-cwd fallback
       regardless of whether the reason is ever durably recorded, so nothing functional is lost
       if a workflow artifact never ends up being created (e.g. the session ends before `## 6.`
       runs).] This is an event, not a `pending_gate` — there is nothing to resume or retry.
       The router continues immediately with this fallback `PROJECT_ROOT` value.

## 2. Memory Load And Template Validation

Always run this before routing or resuming. Memory is organized in two tiers:
- **project/** — long-lived cross-workflow state (architecture decisions, durable patterns, ongoing blockers). Always load first.
- **workflows/{wf-id}/** — per-workflow isolated state (current focus, active phase, in-flight tasks). Load only when a `workflow_uuid` is already known (resume path).

```text
1. Bash("mkdir -p \"$PROJECT_ROOT/.craftflow/state/project\"")
2. Read("$PROJECT_ROOT/.craftflow/state/project/activeContext.md")
3. Read("$PROJECT_ROOT/.craftflow/state/project/patterns.md")
4. Read("$PROJECT_ROOT/.craftflow/state/project/progress.md")
5. Read("$PROJECT_ROOT/.craftflow/state/project/constitution.md") — skip gracefully if absent; when present, MUST constraints are active for this session
6. If workflow_uuid is known (resume path):
   a. Bash("mkdir -p \"$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}\"")
   b. Read("$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/activeContext.md")
   c. Read("$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/patterns.md")
   d. Read("$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/progress.md")
   Merge: workflow-scoped values override project-scoped for current-focus
   fields (## Current Focus, ## Next Steps, ## Tasks) only.
7. Fallback: If project/ files are missing or empty, also read the root-flat
   files ($PROJECT_ROOT/.craftflow/state/activeContext.md etc.) and merge content into project/
   before proceeding. Root-flat files are the backward-compat layer.
```

Do not parallelize step 1 with reads.

If a project/ memory file is missing:
- Create it using the `craftflow:session-memory` template.
- Read it before continuing.

Required sections:

| File | Required Sections |
|------|-------------------|
| `activeContext.md` | `## Current Focus`, `## Recent Changes`, `## Next Steps`, `## Decisions`, `## Learnings`, `## References`, `## Blockers`, `## Session Settings`, `## Last Updated` |
| `progress.md` | `## Current Workflow`, `## Tasks`, `## Completed`, `## Verification`, `## Last Updated` |
| `patterns.md` | `## User Standards`, `## Common Gotchas`, `## Project SKILL_HINTS`, `## Last Updated` |

Auto-heal rule:
- Insert missing sections before `## Last Updated`.
- After every `Edit(...)`, immediately `Read(...)` and verify the new section exists.

JUST_GO:
- Read `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Session Settings`.
- If `AUTO_PROCEED: true`, set `JUST_GO=true`.
- While `JUST_GO=true`, auto-default all non-REVERT AskUserQuestion gates to the recommended option and log the choice in `## Decisions`.

v10 trust rule:
- `JUST_GO` never overrides explicit user/project standards, open plan decisions, or failure-stop gates — including any gate that explicitly documents its own exception, such as the multi-repo AMBIGUOUS resolution gate in Worktree Isolation.
- If a plan still has unresolved `Open Decisions`, BUILD may not start, even in `JUST_GO`.

## 2a. Workflow Artifact And Hook Policy

Core law:
- Durable router state lives under `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.json`
- Companion event log lives under `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.events.jsonl`
- Router-owned gates still include `plan_trust_gate`, `phase_exit_gate`, `failure_stop_gate`, `memory_sync_gate`, and `skill_precedence_gate`

Mandatory reference read:
- Before workflow creation, artifact mutation, hook policy changes, or resume logic that depends on artifact fields, immediately read `references/workflow-artifact-and-hook-policy.md`.
- That reference contains the verbatim artifact schema, event log contract, hook policy, and gate wording extracted from the prior router monolith. Treat it as load-bearing orchestration law, not optional background.

## 3. Task Metadata Contract

Every CRAFTFLOW task description starts with normalized metadata lines:

```text
wf:{workflow_uuid}
kind:{workflow|agent|remfix|memory|reverify|research}
origin:{router|component-builder|bug-investigator|code-reviewer|silent-failure-hunter|integration-verifier|planner}
phase:{build|build-implement|build-review|build-hunt|build-verify|build-doc-sync|debug|debug-investigate|debug-review|debug-verify|review|review-audit|plan|plan-create|plan-review-gap-1|plan-review-gap-2|memory-finalize|re-review|re-hunt|re-verify|re-plan|research-web|research-github}
plan:{path|N/A}
scope:{ALL_ISSUES|CRITICAL_ONLY|N/A}
reason:{short reason or N/A}
```

Rules:
- `wf:` is mandatory on every child task.
- Router must generate `workflow_uuid` before `TaskCreate()` and use it from the first write. `wf:PENDING_SELF` is retired in v10.
- `kind:` is mandatory and drives resume, routing, and counting logic.
- `origin:` is mandatory on every `kind:remfix` task.
- `plan:` is required on workflow, agent, reverify, and memory tasks.
- `reason:` is required on remediation and research tasks.
- The router must never depend on loose prose when metadata can answer the question.

## 4. Resume And Hydration

After memory load:

```text
TaskList()
```

Hydration rules:
- Find active parent workflow tasks by subject prefix `CRAFTFLOW BUILD:`, `CRAFTFLOW DEBUG:`, `CRAFTFLOW REVIEW:`, `CRAFTFLOW PLAN:`.
- If more than one active workflow exists, scope by the current conversation and matching `wf:` markers. Do not resume a workflow you cannot scope confidently.
- Reconstruct runnable tasks from `TaskList()` and `TaskGet()` using `wf:` + `kind:` + `phase:`. Do not rely on stored task IDs for correctness.
- Read and write only the state namespace. Ignore legacy `.craftflow/*.md` and `.craftflow/workflows/*` state during hydration. State lives under `.craftflow/state/`.
- `[craftflow-internal] memory_task_id` in `activeContext.md` is only a transient optimization. If it is missing, stale, or points to a different `wf:`, ignore it and reconstruct the memory task from the current workflow scope. [EASY TO MISS: stale memory_task_id is the #1 cause of cross-workflow pollution]
- Never use an unscoped fallback like "first pending Memory Update task". [EASY TO MISS: unscoped lookups silently pick up orphan tasks from prior workflows]

Resume algorithm:
1. Identify the active parent workflow.
2. Extract `workflow_uuid` from the `wf:` line.
3. Read all CRAFTFLOW tasks whose descriptions contain that `wf:`.
4. Derive runnable tasks from `status` and `blockedBy`.
5. Reconstruct the memory task as the unique pending/in_progress `kind:memory` task in the same `wf:`.

Scope-decision resume:
- Before normal routing, check `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Decisions` for a live marker:
  - `[SCOPE-DECISION-PENDING: wf:{workflow_uuid} reason:{...}]`
- If present, treat the current user reply as the answer to that pending BUILD scope gate:
  - `critical only` -> create the pending REM-FIX with `scope:CRITICAL_ONLY`
  - `all issues` -> create the pending REM-FIX with `scope:ALL_ISSUES`
  - anything else -> ask again with the same two options and stop
- After consuming a valid answer:
  - remove the pending marker from `## Decisions`
  - create the scoped REM-FIX
  - block downstream re-review / re-hunt / verifier tasks as normal
  - stop after task creation so the next turn resumes from task state, not from repeated prose parsing
  - [EASY TO MISS: When persisting user decisions, use the user's exact words. Paraphrasing introduces drift that compounds across resume cycles.]

Safety rules:
- If a task list is shared across sessions, always scope by `wf:` before resuming.
- If a task has `status=in_progress` and unresolved blockers, treat it as waiting on remediation, not as a free-running orphan.
- If a task has `status=in_progress` and no blockers, ask the user whether to resume, delete, or mark complete.
- If legacy tasks exist with subjects starting `BUILD:`, `DEBUG:`, `REVIEW:`, or `PLAN:` without the `CRAFTFLOW` prefix, ask whether to resume the legacy workflow or start a fresh CRAFTFLOW workflow.

## 5. Workflow Preparation

### Shared preparation

Before creating a new workflow:
- Read `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## References` to discover `Plan`, `Design`, and prior `Research` files.
- Read `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Decisions` for prior planner/build clarifications.
- Read `$PROJECT_ROOT/.craftflow/state/project/progress.md ## Current Workflow` and `## Tasks` for pending work that should resume instead of duplicating.
- Read the latest `$PROJECT_ROOT/.craftflow/state/workflows/*.json` artifact if one exists for the current conversation.

**Intent Readiness Gate (MANDATORY before PLAN or BUILD):**
Before dispatching to planner or builder, verify the intent contract meets three conditions:
1. **Context-bounded:** The full intent (goal + constraints + acceptance criteria) fits within the agent's prompt scaffold without truncation. If the intent requires loading more than 5 source files to be understood, decompose first (switch to PLAN).
2. **Contradiction-free:** No acceptance criterion contradicts a stated constraint or non-goal. If contradictions exist, halt and persist `pending_gate="intent_contradiction"`.
3. **Sufficiently specific:** Every acceptance criterion maps to at least one verifiable scenario. If a criterion is unverifiable ("make it better" without a metric), halt and ask for specificity.

Router-owned interface fields:
- `plan_mode`: `direct` | `execution_plan` | `decision_rfc`
- `verification_rigor`: `standard` | `critical_path`
- `checkpoint_type`: `none` | `human_verify` | `decision` | `human_action`
- `proof_status`: `passed` | `gaps_found` | `human_needed`

### BUILD preparation

- Before any BUILD-specific readiness decision or child-task creation, immediately read `references/build-workflow.md`.
- Use the `### BUILD preparation` and `### BUILD task graph` blocks in that file as the canonical BUILD law.
- Before fast-path routing (performed during BUILD preparation), read `references/fast-path.md` for the canonical keyword table, agent dispatch table, gate table, and escalation protocol.

### Fast Path Detection

Before BUILD child task creation, read `references/fast-path.md` (contains canonical keyword table and escalation protocol).

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

1. At BUILD start, `PROJECT_ROOT` was already resolved once by `## 0. Resolve Project Root`
   at session start — reuse it directly. Do not re-run `git rev-parse --show-toplevel` or
   invoke the workspace-root resolver script a second time.

   [EASY TO MISS: if `## 0.` fell back to `NO_REPO_FOUND`/`RESOLVE_SCRIPT_ERROR` (no git-repo
   children under cwd, or the resolver script itself failed), `PROJECT_ROOT` is already set to
   `$(pwd)` from that fallback — Worktree Isolation still proceeds with the `mkdir`/`git
   worktree add` block below using that same fallback value, exactly like the single-repo
   path. There is no separate NO_REPO_FOUND branch here anymore; `## 0.` already handled it.]

   With `PROJECT_ROOT` reused from `## 0.` above, capture both commands' output and exit code,
   mirroring the `MERGE_EXIT`/`COPY_FALLBACK_EXIT`/`WORKTREE_REMOVE_EXIT` pattern used later in
   this section — never leave either command's exit code unchecked:
   ```bash
   MKDIR_OUTPUT=$(mkdir -p "$PROJECT_ROOT/.claude/worktrees" 2>&1)
   MKDIR_EXIT=$?
   WORKTREE_ADD_OUTPUT=$(git worktree add "$PROJECT_ROOT/.claude/worktrees/{worktree_dir}" -b {worktree_branch} 2>&1)
   WORKTREE_ADD_EXIT=$?
   ```
   where `worktree_dir` and `worktree_branch` come from the `craftflow_workflow_id.py` helper
   output (see step 1 of **Parent workflow creation** above).
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

4. After `integration-verifier` returns PASS on the final phase and BEFORE memory-finalize, run
   the pre-merge safety guard, then finalize. The guard applies identically whether finalize ends
   up running a real `git merge` or falling back to the copy script (4e) — never skip it because a
   fallback path is expected. If `worktree_mode != "auto_created"`, skip this entire step (nothing
   to merge).

   a. **Resolve project root, the plugin install path, and this workflow's own identity:**
      **Guard first, before anything else in this step:** read `worktree_path` from the current
      workflow artifact. If `worktree_path` is null or empty despite `worktree_mode ==
      "auto_created"` (a corrupted or partially-written artifact), treat it identically to
      `worktree_mode != "auto_created"` at the top of step 4 — skip the rest of step 4 entirely.
      Do NOT run the `dirname` derivation below or any of 4b-4e (no `git status`, no `git merge`,
      no `git worktree remove`, no `git branch -d`). Append `{"event":"worktree_path_missing"}`
      to the event log and proceed straight to doc-sync/memory-finalize, exactly like the
      ordinary no-worktree case. [EASY TO MISS: `dirname` on an empty/unset string silently
      returns `.` (the current working directory) at every nesting level — no error, no
      sentinel. Without this guard, `PROJECT_ROOT` would silently become an arbitrary directory
      and every downstream command in 4b-4e would run against the wrong root with no failure
      signal.]

      Once `worktree_path` is confirmed present, read `workflow_uuid` and `worktree_branch` from
      the current workflow artifact (already set in step 2) — `PROJECT_ROOT` is derived from
      `worktree_path`, never re-derived via `git rev-parse --show-toplevel`. A workflow whose
      worktree was created via the step 1a multi-repo resolver still has a cwd that does not
      resolve to a git repo at merge time either; re-running `git rev-parse --show-toplevel` here
      would fail again for exactly the same reason it failed at worktree-creation time.

      **Guard also required for `worktree_branch`** (identical corrupted/partial-artifact threat
      model as the `worktree_path` guard above): if `worktree_branch` is null or empty despite
      `worktree_mode == "auto_created"`, treat it identically to `worktree_mode != "auto_created"`
      at the top of step 4 — skip the rest of step 4 entirely. Do NOT run the `dirname`
      derivation below or any of 4b-4e (no `git status`, no `git merge`, no `git worktree
      remove`, no `git branch -d`). Append `{"event":"worktree_branch_missing"}` to the event log
      and proceed straight to doc-sync/memory-finalize, exactly like the ordinary no-worktree
      case. [EASY TO MISS: an empty `{worktree_branch}` substituted directly into `git merge
      {worktree_branch}` in step 4e would not fail with a clear, recognizable error — it risks
      matching an unrelated ref or producing a confusing generic git error that obscures the real
      missing-artifact problem. Without this guard, the failure would surface deep inside 4e
      instead of being caught at the earliest possible point, the same way an unguarded
      `worktree_path` would silently corrupt `PROJECT_ROOT` via `dirname`.]
      ```bash
      # worktree_path = "{project_root}/.claude/worktrees/{worktree_dir}" (set in step 2) --
      # three levels down from PROJECT_ROOT (.claude/worktrees/{worktree_dir}), so its
      # great-grandparent directory is always PROJECT_ROOT, in both the single-repo and
      # multi-repo-resolved cases.
      PROJECT_ROOT=$(dirname "$(dirname "$(dirname "{worktree_path}")")")
      CRAFTFLOW_INSTALL=$(python3 -c "
      import json, pathlib
      reg = json.loads(pathlib.Path.home().joinpath('.claude/plugins/installed_plugins.json').read_text())
      print(reg['plugins']['craftflow@craftflow'][0]['installPath'])
      ")
      CRAFTFLOW_INSTALL_EXIT=$?
      LOCK_DIR="$PROJECT_ROOT/.claude/worktrees/.merge.lock"
      ```
      (A non-zero `CRAFTFLOW_INSTALL_EXIT` here is not handled as a separate branch — it surfaces
      downstream as an empty/unusable `$CRAFTFLOW_INSTALL` path in whichever script it is used to
      invoke next, e.g. the lock-staleness `DECISION_EXIT` capture and default `case` arm in step
      4b below, or `COPY_FALLBACK_EXIT` in step 4e.)

   b. **Acquire the merge lock.** The lock is a *directory*, not a plain file — `mkdir` is
      atomic on POSIX filesystems, which a bare existence check is not. The staleness/contention
      decision is delegated to a real script file, `craftflow_worktree_lock_staleness.py`
      (installed alongside `craftflow_workflow_id.py`, invoked the same way) — never an inline
      heredoc:
      ```bash
      ATTEMPT=0
      MAX_ATTEMPTS=9   # ~45s total wait at 5s per attempt
      LOCK_ACQUIRED=false

      while [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; do
        if mkdir "$LOCK_DIR" 2>/dev/null; then
          printf '{"workflow_uuid":"%s","worktree_path":"%s","acquired_at":"%s"}' \
            "{workflow_uuid}" "{worktree_path}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
            > "$LOCK_DIR/metadata.json"
          LOCK_ACQUIRED=true
          break
        fi

        METADATA_BEFORE=$(cat "$LOCK_DIR/metadata.json" 2>/dev/null)

        DECISION=$(python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_worktree_lock_staleness.py" \
          "$LOCK_DIR/metadata.json" "$PROJECT_ROOT" "{workflow_uuid}")
        DECISION_EXIT=$?

        case "$DECISION" in
          STALE_WORKTREE_GONE*|STALE_INACTIVE*|SELF_RECLAIM*)
            # TOCTOU guard: `decide()` above is a pure snapshot read with no
            # synchronization -- re-read the metadata NOW, immediately before
            # deleting, and compare it byte-for-byte against what was
            # captured immediately BEFORE `decide()` ran. Only delete if it
            # is unchanged. If it changed (e.g. the original holder released
            # and a third process won a fresh `mkdir` with its own live
            # metadata in the window between the two reads), do NOT delete --
            # a fresh, live lock must never be destroyed based on a stale
            # decision about a DIFFERENT lock occupant. This does not need a
            # separate `decide()` re-run: an unchanged byte-for-byte
            # metadata.json between the two reads means nothing relevant to
            # the decision could have changed either.
            METADATA_AFTER=$(cat "$LOCK_DIR/metadata.json" 2>/dev/null)
            if [ -n "$METADATA_AFTER" ] && [ "$METADATA_BEFORE" = "$METADATA_AFTER" ]; then
              rm -rf "$LOCK_DIR"
              continue   # reclaimed -- retry mkdir immediately, no sleep
            fi
            # Metadata changed underneath us -- fall through to the normal
            # wait/retry path below, exactly like ordinary contention.
            ;;
          *)
            # Unrecognized $DECISION -- covers ordinary CONTENDED*/LOCK_READ_ERROR*/
            # GIT_WORKTREE_LIST_ERROR* outcomes (no reclaim action needed, just wait/retry)
            # AND a genuinely unknown/garbled value (e.g. empty output from a non-zero
            # DECISION_EXIT, or an unexpected outcome word this router doesn't recognize).
            # Both are treated identically to CONTENDED_UNKNOWN_HOLDER: fail closed, never
            # reclaim on an unrecognized state, fall through to the wait/retry path below.
            ;;
        esac

        ATTEMPT=$((ATTEMPT + 1))
        sleep 5
      done
      ```
      [EASY TO MISS: the `METADATA_BEFORE`/`METADATA_AFTER` byte-for-byte compare immediately
      before `rm -rf "$LOCK_DIR"` closes a real TOCTOU window — without it, a stale-looking lock
      could legitimately be released and re-acquired by a third process between the staleness
      script's read and this loop's `rm -rf`, and the reclaiming process would delete that THIRD
      process's brand-new, live lock instead of the one it actually evaluated. This is the
      smallest correct fix: it reuses the same metadata content the staleness decision itself was
      based on rather than re-invoking `decide()` a second time, and it still has one
      irreducible-but-negligible window (between the second `cat` and the `rm -rf`) that a full
      atomic compare-and-delete primitive would close — POSIX shell has no such primitive for
      directories, so this is the practical minimum-diff mitigation, not a claim of perfect
      atomicity.]
      [EASY TO MISS: `craftflow_worktree_lock_staleness.py` never reclaims on unreadable/corrupt
      lock metadata (`CONTENDED_UNKNOWN_HOLDER`) or on a genuine filesystem/permission read error
      (`LOCK_READ_ERROR`, distinct from contention) — both fail closed, always waiting out the
      budget rather than risk stealing a live lock. It DOES reclaim immediately, skipping the
      age/inactivity window, when the lock's recorded `workflow_uuid` matches this workflow's own
      `{workflow_uuid}` **AND** the lock's recorded `worktree_path` is positively confirmed gone
      (`SELF_RECLAIM`) — a workflow resuming after crashing while it itself held the lock must
      never wait out its own dead lock once that proof exists. If the same `workflow_uuid` resumes
      while its own worktree still exists (e.g. two concurrent processes for the same workflow,
      genuinely still mid-merge), it is NOT given this shortcut — it waits/gates exactly like a
      stranger's lock would, because a `workflow_uuid` match alone is never treated as proof the
      prior holder is dead. A `metadata.json` that exists but fails to parse as a JSON object
      (corrupt/truncated, e.g. a crash mid-`printf`-write) is NOT a permanent
      `CONTENDED_UNKNOWN_HOLDER` dead-end either — it falls back to the lock directory's own mtime
      as a substitute `acquired_at` and can still reclaim via the age check, surfacing as
      `STALE_INACTIVE unknown` (matches the `STALE_INACTIVE*` reclaim pattern above). A failure of
      the `git worktree list --porcelain` subprocess call itself (e.g. `git` not on PATH) surfaces
      as `GIT_WORKTREE_LIST_ERROR <exception_class_name>` — like `LOCK_READ_ERROR`, this
      deliberately does NOT match any reclaim pattern above and fails closed, waiting out the
      budget. This script is the single source of truth for this decision — `SKILL.md` and
      `scripts/craftflow_worktree_merge_guard_check.py` both invoke the exact same file; there is
      no separate copy to keep in sync.]

   c. **If the lock was never acquired** (loop exhausted at `MAX_ATTEMPTS` while still contended):
      - Persist `pending_gate: "worktree_merge_locked"` on the workflow artifact, naming the
        last-known holder as follows, based on the final `$DECISION`'s outcome word:
        - `CONTENDED <workflow_uuid>` → record that `workflow_uuid` as the holder.
        - `CONTENDED_UNKNOWN_HOLDER` → record `"unknown"` as the holder.
        - `LOCK_READ_ERROR <exception_class_name>` → record the exception class name itself
          (e.g. `"PermissionError"`) as the holder value — **not** `"unknown"` and **not** a
          `workflow_uuid` — since this outcome is not evidence of any other workflow at all, and
          recording it as `"unknown"` would blur it back into ordinary contention.
        - `GIT_WORKTREE_LIST_ERROR <exception_class_name>` → same treatment as `LOCK_READ_ERROR`:
          record the exception class name itself as the holder value, never `"unknown"` — this
          outcome means the local `git worktree list --porcelain` call itself failed to run (e.g.
          `git` not on PATH), not that another workflow holds the lock.
      - Do NOT touch the main tree, the worktree, or the branch.
      - Stop before memory-finalize. If the final `$DECISION` started with `LOCK_READ_ERROR` or
        `GIT_WORKTREE_LIST_ERROR`, use a distinct message template that makes clear this is a local
        filesystem/permission/environment problem, not another workflow: "Failed to evaluate the
        merge lock due to a local error (`{exception_class_name}`) — this is NOT evidence of
        another live workflow. For `LOCK_READ_ERROR`, check filesystem permissions on
        `.claude/worktrees/.merge.lock`; for `GIT_WORKTREE_LIST_ERROR`, confirm `git` is on `PATH`
        and runnable. Then resume this workflow to retry." Otherwise (a `CONTENDED` or
        `CONTENDED_UNKNOWN_HOLDER` outcome) tell the user: "Another BUILD workflow
        ({holder_workflow_uuid_or_unknown}) currently holds the merge lock. Wait for it to finish,
        then resume this workflow to retry. If that workflow is actually dead (not just slow), you
        can manually delete `.claude/worktrees/.merge.lock` and resume — only after confirming it
        isn't still running."
      - Resuming this workflow re-enters this step from 4a.

   d. **Clean-tree check** (only reached once the lock is held):
      ```bash
      DIRTY_STATUS=$(git -C "$PROJECT_ROOT" status --porcelain 2>&1)
      DIRTY_EXIT=$?
      ```
      - If `DIRTY_EXIT != 0` (the `git status` command itself failed) OR `DIRTY_STATUS` is
        non-empty (uncommitted changes exist):
        - Release the lock: `rm -rf "$LOCK_DIR"; RELEASE_LOCK_EXIT=$?` (a non-zero exit here is
          self-detecting via the next workflow's contention path in step 4b — not treated as
          fatal here)
        - Persist `pending_gate: "worktree_dirty_main_tree"` (include `$DIRTY_STATUS` in the
          event log for visibility).
        - Do NOT run `git merge`, do NOT copy any files from the worktree, do NOT remove the
          worktree or branch.
        - Stop before memory-finalize. Tell the user: "The main tree has uncommitted changes
          this BUILD did not make (likely a concurrent DEBUG/PLAN/REVIEW session, or an
          unrelated manual edit). Commit, stash, or otherwise resolve those changes, then resume
          this workflow to retry the merge."
        - Resuming this workflow re-enters this step from 4a.
      - If clean (empty output, exit 0): proceed to 4e.

   e. **Finalize** (destination behavior unchanged from before this fix, now guarded — and now
      covers the copy-fallback path explicitly and executably for the first time):
      - Attempt (capture both output and exit code, mirroring the `TOPLEVEL_EXIT`/`RESOLVE_EXIT`/
        `DIRTY_EXIT` pattern used earlier in this section — never leave `git merge`'s own exit
        code unchecked):
        ```bash
        MERGE_OUTPUT=$(git merge {worktree_branch} 2>&1)
        MERGE_EXIT=$?
        ```
        (from `$PROJECT_ROOT`; read `worktree_branch` from the workflow artifact.)
      - **If `MERGE_EXIT == 0` AND `$MERGE_OUTPUT` contains `"Already up to date"`**: this means
        the worktree's builder edited files but never committed them on `{worktree_branch}` — a
        real, frequently-observed gotcha in this repo, not a sign there's nothing to merge.
        Recover the actual changes directly from the worktree's own uncommitted state via the
        real copy-fallback script, never via free-form manual copying — capture both its output
        and its own exit code, mirroring the `MERGE_EXIT` pattern immediately above (never leave
        this script's exit code unchecked either):
        ```bash
        COPY_FALLBACK_OUTPUT=$(python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_worktree_copy_fallback.py" \
          "{worktree_path}" "$PROJECT_ROOT" 2>&1)
        COPY_FALLBACK_EXIT=$?
        ```
        This script parses `git status --porcelain=1 -z` in `{worktree_path}` (NUL-delimited,
        unquoted paths — chosen to correctly handle renamed/copied entries and paths containing
        spaces) and applies Added/Modified/untracked entries as copies, Deleted entries as
        removals, and Renamed/Copied entries as an old-path removal plus new-path copy. It does
        not `git add` or commit — the changes land in the main tree exactly as uncommitted
        changes, matching this router's existing practice.
        [EASY TO MISS: this fallback only ever runs after the clean-tree check in 4d has already
        passed for `$PROJECT_ROOT` — it never bypasses that check, because it is reached only
        from this already-guarded branch.]
      - **If `COPY_FALLBACK_EXIT != 0`** (the copy-fallback script itself exits non-zero): the
        script's own documented guarantee is partial-apply-but-diagnosable, not atomic — earlier
        tokens in the same run that already applied successfully remain applied in the main tree
        even if a later token fails, so a non-zero exit does NOT mean nothing landed.
        - Release the lock: `rm -rf "$LOCK_DIR"; RELEASE_LOCK_EXIT=$?` (a non-zero exit here is
          self-detecting via the next workflow's contention path in step 4b — not treated as
          fatal here)
        - Persist `pending_gate: "worktree_copy_fallback_failed"` (include `$COPY_FALLBACK_OUTPUT`
          in the event log for visibility).
        - Do NOT remove the worktree, do NOT delete the branch — the worktree still holds the
          uncommitted source of truth for whatever did not land.
        - Stop before memory-finalize. Tell the user: "The copy-fallback script failed while
          applying the worktree's uncommitted changes to the main tree: `{copy_fallback_output}`.
          This may be a partial apply — earlier files in this run may have already landed. Run
          `git status --porcelain` in the main tree to see exactly what applied before resuming.
          Resolve the underlying issue (e.g. a filesystem/permission problem, or a genuine
          conflict/rename edge case the script refused to guess on), then resume this workflow to
          retry."
        - Resuming this workflow re-enters this step from 4a.
      - **If `COPY_FALLBACK_EXIT == 0`**: the fallback applied cleanly. Proceed to the final
        cleanup below exactly as a successful `git merge` would.
      - **If `MERGE_EXIT == 0` AND `$MERGE_OUTPUT` does not contain `"Already up to date"`** (a
        real merge succeeded with actual content merged, no conflicts): nothing further needed
        here.
      - **If `MERGE_EXIT != 0` AND `$MERGE_OUTPUT` contains a conflict marker (`"CONFLICT"`)**
        (existing, unchanged outcome, now gated on `MERGE_EXIT` + output text rather than assumed
        by exclusion):
        - Release the lock: `rm -rf "$LOCK_DIR"; RELEASE_LOCK_EXIT=$?` (a non-zero exit here is
          self-detecting via the next workflow's contention path in step 4b — not treated as
          fatal here)
        - Persist `pending_gate: "worktree_merge_conflict"`, ask user to resolve before
          memory-finalize.
        - Do NOT remove the worktree or delete the branch while conflicts are unresolved.
        - Resuming this workflow re-enters this step from 4a once the user has resolved the
          conflict.
      - **If `MERGE_EXIT != 0` AND `$MERGE_OUTPUT` matches neither `"Already up to date"` nor a
        conflict marker** (an unrecognized merge failure — e.g. an invalid ref, an
        unrelated-histories error, or an uncommitted-changes-would-be-overwritten error that
        slipped past the 4d clean-tree check): this is the explicit 4th branch closing the
        by-exclusion gap — nothing was actually merged, so nothing may be treated as if it had
        merged.
        - Release the lock: `rm -rf "$LOCK_DIR"; RELEASE_LOCK_EXIT=$?` (a non-zero exit here is
          self-detecting via the next workflow's contention path in step 4b — not treated as
          fatal here)
        - Persist `pending_gate: "worktree_merge_unrecognized_failure"` (include `$MERGE_OUTPUT`
          in the event log for visibility).
        - Do NOT remove the worktree, do NOT delete the branch, do NOT run the copy-fallback
          script — nothing has been confirmed merged, so the worktree's committed/uncommitted
          content remains the only source of truth.
        - Stop before memory-finalize. Tell the user: "`git merge {worktree_branch}` failed with
          an error this router does not recognize as either 'already up to date' or a merge
          conflict: `{merge_output}`. Nothing has been merged, and the worktree/branch have not
          been touched. Inspect the error above, resolve the underlying issue in `$PROJECT_ROOT`,
          then resume this workflow to retry."
        - Resuming this workflow re-enters this step from 4a.
      - On successful merge or successful copy-fallback, clean up — capture the exit code of
        each command, mirroring the `MERGE_EXIT`/`COPY_FALLBACK_EXIT` pattern above (never leave
        either cleanup command's exit code unchecked):
        ```bash
        WORKTREE_REMOVE_OUTPUT=$(git worktree remove {worktree_path} --force 2>&1)
        WORKTREE_REMOVE_EXIT=$?
        ```
        - **If `WORKTREE_REMOVE_EXIT != 0`** (e.g. the worktree is busy/locked — a process still
          has an open handle inside it): do NOT run `git branch -d`, do NOT set
          `worktree_mode → "merged_and_removed"`.
          - Release the lock: `rm -rf "$LOCK_DIR"; RELEASE_LOCK_EXIT=$?` (a non-zero exit here
            is self-detecting via the next workflow's contention path in step 4b — not treated
            as fatal here)
          - Persist `pending_gate: "worktree_cleanup_failed"` (include `$WORKTREE_REMOVE_OUTPUT`
            and the failing command, `git worktree remove`, in the event log for visibility).
          - Stop before memory-finalize. Tell the user: "The merge/copy-fallback succeeded, but
            `git worktree remove {worktree_path} --force` failed: `{worktree_remove_output}`. The
            worktree and its branch are still present and untouched. Resolve the underlying issue
            (e.g. a process still holding a handle inside the worktree), then resume this
            workflow to retry cleanup."
          - Resuming this workflow re-enters this step from 4a.
        - **If `WORKTREE_REMOVE_EXIT == 0`**, proceed to delete the branch:
          ```bash
          BRANCH_DELETE_OUTPUT=$(git branch -d {worktree_branch} 2>&1)
          BRANCH_DELETE_EXIT=$?
          ```
          - **If `BRANCH_DELETE_EXIT != 0`** (e.g. `git branch -d` refuses because the branch is
            not fully merged into the current branch — a real correctness signal, not a cosmetic
            failure): do NOT set `worktree_mode → "merged_and_removed"`.
            - Release the lock: `rm -rf "$LOCK_DIR"; RELEASE_LOCK_EXIT=$?` (a non-zero exit here
              is self-detecting via the next workflow's contention path in step 4b — not
              treated as fatal here)
            - Persist `pending_gate: "worktree_cleanup_failed"` (include `$BRANCH_DELETE_OUTPUT`
              and the failing command, `git branch -d`, in the event log for visibility).
            - Stop before memory-finalize. Tell the user: "The merge/copy-fallback succeeded and
              the worktree was removed, but `git branch -d {worktree_branch}` failed:
              `{branch_delete_output}`. This can mean the branch is not fully merged — a real
              correctness signal, not a cosmetic failure. Inspect `git log {worktree_branch}` in
              `$PROJECT_ROOT`, resolve the discrepancy (or delete the branch manually with
              `git branch -D` once you've confirmed nothing is lost), then resume this workflow
              to retry cleanup."
            - Resuming this workflow re-enters this step from 4a.
          - **If `BRANCH_DELETE_EXIT == 0`**: both cleanup commands succeeded.
            - Update artifact: `worktree_mode → "merged_and_removed"`
            - Release the lock: `rm -rf "$LOCK_DIR"; RELEASE_LOCK_EXIT=$?` (a non-zero exit here
              is self-detecting via the next workflow's contention path in step 4b — not
              treated as fatal here)
            - Continue to doc-sync/memory-finalize as today.

Safety: Worktree creates are idempotent in the event log. If a resume finds `worktree_mode: "auto_created"` already set, re-use `worktree_path` from the artifact rather than creating a new worktree. If `worktree_path` is null despite `worktree_mode: "auto_created"`, treat as fallback and proceed with main tree. If a resume finds `pending_gate` set to `worktree_merge_locked`, `worktree_dirty_main_tree`, `worktree_merge_conflict`, `worktree_merge_unrecognized_failure`, `worktree_copy_fallback_failed`, or `worktree_cleanup_failed`, re-enter this step from 4a — none of these gates require any bespoke resume branch beyond the generic resume algorithm in `## 4. Resume And Hydration`.

### DEBUG preparation

- Before any DEBUG-specific readiness decision or child-task creation, immediately read `references/debug-workflow.md`.
- Use the `### DEBUG preparation` and `### DEBUG task graph` blocks in that file as the canonical DEBUG law.

### REVIEW preparation

- Before any REVIEW-specific readiness decision or child-task creation, immediately read `references/review-workflow.md`.
- Use the `### REVIEW preparation` and `### REVIEW task graph` blocks in that file as the canonical REVIEW law.

### PLAN preparation

- Before any PLAN-specific readiness decision or child-task creation, immediately read `references/plan-workflow.md`.
- Use the `### PLAN preparation` and `### PLAN task graph` blocks in that file as the canonical PLAN law.
- If planner clarification, review-loop findings, or plan remediation rules trigger later in the workflow, also read `references/remediation-and-research.md` before continuing.

## 6. Workflow Task Graphs

### Parent workflow creation

Use this pattern for every new workflow:

1. Generate a stable workflow UUID, worktree names, and `iso_timestamp` before `TaskCreate()` by running the minting helper:

```bash
# Locate the helper via the plugin registry
CRAFTFLOW_INSTALL=$(python3 -c "
import json, pathlib
reg = json.loads(pathlib.Path.home().joinpath('.claude/plugins/installed_plugins.json').read_text())
print(reg['plugins']['craftflow@craftflow'][0]['installPath'])
")

# Mint the id — pass the user request; the helper auto-detects the current git branch
WF_INFO=$(python3 "${CRAFTFLOW_INSTALL}/scripts/craftflow_workflow_id.py" \
  --request "USER_REQUEST_SHELL_ESCAPED" \
  --project "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" \
  --json)
```

Replace `USER_REQUEST_SHELL_ESCAPED` with the actual user request, properly shell-quoted.
Then parse the JSON to bind: `workflow_uuid` · `iso_timestamp` · `worktree_dir` · `worktree_branch`.

```bash
workflow_uuid=$(printf '%s' "$WF_INFO" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['workflow_uuid'])")
iso_timestamp=$(printf '%s' "$WF_INFO" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['iso_timestamp'])")
worktree_dir=$(printf '%s' "$WF_INFO"  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['worktree_dir'])")
worktree_branch=$(printf '%s' "$WF_INFO" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['worktree_branch'])")
```

ID format: `wf-{slug}-{YYYYMMDD-HHMMSS}-{8hex}`.
Slug = slugified git branch name (if a genuine feature branch, i.e. not main/master/develop/dev/trunk or a craftflow-generated `wf-`/`worktree-` branch) — otherwise slugified request text.
The `iso_timestamp` from the helper is the authoritative creation timestamp — use it for **all** `{iso_timestamp}` placeholders in the artifact Write below (no separate time derivation needed).

2. Create the parent workflow task with that UUID from the first write:

```text
TaskCreate({
  subject: "CRAFTFLOW {WORKFLOW}: {summary}",
  description: "wf:{workflow_uuid}\nkind:workflow\norigin:router\nphase:{build|debug|review|plan}\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:User request\n\nUser request: {request}\nChain: {chain description}",
  activeForm: "{workflow active form}"
})
```

3. Immediately write the v10 artifact and event log:

```text
Write(
  file_path="$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.json",
  content="{\"workflow_uuid\":\"{workflow_uuid}\",\"workflow_id\":\"{workflow_uuid}\",\"workflow_type\":\"{WORKFLOW}\",\"state_root\":\".craftflow/state\",\"user_request\":\"{request}\",\"plan_file\":null,\"design_file\":null,\"research_files\":[],\"approved_decisions\":[],\"plan_mode\":null,\"verification_rigor\":\"standard\",\"proof_status\":\"gaps_found\",\"traceability\":{\"requirements\":[],\"phases\":[],\"verification\":[],\"remediation\":[]},\"intent\":{\"goal\":null,\"non_goals\":[],\"constraints\":[],\"acceptance_criteria\":[],\"open_decisions\":[]},\"normalized_phases\":[],\"phase_cursor\":null,\"capabilities\":{\"brightdata_available\":\"unknown\",\"octocode_available\":\"unknown\",\"websearch_available\":\"unknown\",\"webfetch_available\":\"unknown\"},\"research_rounds\":[],\"research_backend_history\":[],\"research_quality\":{\"web\":\"none\",\"github\":\"none\",\"overall\":\"none\"},\"task_ids\":{\"planner_create\":null,\"planning_review_pass1\":null,\"planner_replan\":null,\"planning_review_pass2\":null,\"memory_finalize\":null},\"phase_status\":{},\"results\":{\"builder\":null,\"investigator\":null,\"reviewer\":null,\"hunter\":null,\"verifier\":null,\"planner\":null,\"planning_reviewer\":null,\"research\":{\"web\":null,\"github\":null,\"synthesis\":null}},\"evidence\":{\"builder\":[],\"investigator\":[],\"reviewer\":[],\"hunter\":[],\"verifier\":[],\"planning_reviewer\":[]},\"telemetry\":{\"task_metrics_available\":\"unknown\",\"workflow_wall_clock_seconds\":0,\"agent_wall_clock_seconds\":{\"builder\":0,\"investigator\":0,\"reviewer\":0,\"hunter\":0,\"verifier\":0,\"planner\":0},\"loop_counts\":{\"re_review\":0,\"re_hunt\":0,\"re_verify\":0},\"verifier\":{\"phase_exit_proof_runs\":0,\"extended_audit_runs\":0,\"workload_seconds\":{\"tests\":0,\"build\":0,\"scan\":0,\"reconcile\":0,\"reasoning\":0}}},\"quality\":{\"confidence\":null,\"evidence_complete\":false,\"scenario_coverage\":0,\"research_quality\":\"none\",\"convergence_state\":\"pending\"},\"planning_review_runs\":0,\"planning_review_findings\":[],\"planning_review_status\":\"not_started\",\"build_mode\":null,\"fast_path_risk_signals\":[],\"fast_path_escalated\":false,\"worktree_mode\":null,\"worktree_path\":null,\"worktree_branch\":null,\"memory_notes\":[],\"pending_gate\":null,\"status_history\":[{\"event\":\"workflow_started\",\"ts\":\"{iso_timestamp}\",\"phase\":\"{build|debug|review|plan}\"}],\"remediation_history\":[],\"created_at\":\"{iso_timestamp}\",\"updated_at\":\"{iso_timestamp}\"}"
)
Write(
  file_path="$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.events.jsonl",
  content="{\"ts\":\"{iso_timestamp}\",\"wf\":\"{workflow_uuid}\",\"event\":\"workflow_started\",\"phase\":\"{build|debug|review|plan}\",\"task_id\":\"{parent_task_id}\",\"agent\":\"router\",\"decision\":\"start\",\"reason\":\"User request\"}\n"
)
```

**Conditional — only if `## 0.` recorded a `project_root_resolution_fallback` reason for this
session** (i.e. `TOPLEVEL_EXIT != 0` and the outcome was `NO_REPO_FOUND` or
`RESOLVE_SCRIPT_ERROR`): append a second `status_history` entry and a second events.jsonl line
alongside `workflow_started`, using the same `{workflow_uuid}`/`{iso_timestamp}` values as step
3 above — `{"event":"project_root_resolution_fallback","ts":"{iso_timestamp}","reason":"NO_REPO_FOUND"|"RESOLVE_SCRIPT_ERROR"}`.
If `## 0.` did not fall back (the common, single-repo case), skip this — there is nothing to
append.

4. Immediately after artifact creation, initialize the per-workflow state directory:

```text
Bash("mkdir -p \"$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}\"")
```

This directory is where the memory-finalize task will write workflow-scoped
memory (activeContext.md, patterns.md, progress.md for this workflow only).

Only create child tasks after the v10 artifact and state directory exist.

### BUILD task graph

- See `references/build-workflow.md` and apply its `### BUILD task graph` block verbatim before creating BUILD child tasks.

### DEBUG task graph

- See `references/debug-workflow.md` and apply its `### DEBUG task graph` block verbatim before creating DEBUG child tasks.

### REVIEW task graph

- See `references/review-workflow.md` and apply its `### REVIEW task graph` block verbatim before creating REVIEW child tasks.

### PLAN task graph

- See `references/plan-workflow.md` and apply its `### PLAN task graph` block verbatim before creating PLAN child tasks.

### Research tasks

- When a workflow explicitly triggers research task creation, immediately read `references/remediation-and-research.md`.
- Use the `## 10. Research Orchestration`, `## Research Quality`, and `## Research Files` blocks there before creating or consuming research tasks.

### Marker rules

- BUILD writes `[BUILD-START: wf:{workflow_uuid}]`
- DEBUG writes `[DEBUG-RESET: wf:{workflow_uuid}]`
- PLAN writes `[PLAN-START: wf:{workflow_uuid}]`

## 7. Dispatcher And Agent Prompt Contract

### Explicit dispatcher

| Task Phase / Kind | Agent |
|-------------------|-------|
| `build-implement` | `craftflow:component-builder` |
| `debug-investigate` | `craftflow:bug-investigator` |
| `build-review`, `debug-review`, `review-audit`, `re-review` | `craftflow:code-reviewer` |
| `build-hunt`, `re-hunt` | `craftflow:silent-failure-hunter` |
| `build-verify`, `debug-verify`, `re-verify` | `craftflow:integration-verifier` |
| `doubt-verify` | `craftflow:doubt-verifier` |
| `plan-create`, `re-plan` | `craftflow:planner` |
| `plan-review-gap-1`, `plan-review-gap-2` | `craftflow:plan-gap-reviewer` |
| `research-web` | `craftflow:web-researcher` |
| `research-github` | `craftflow:github-researcher` |
| `kind:remfix` + `origin:bug-investigator` | `craftflow:bug-investigator` |
| `build-doc-sync` | `craftflow:doc-syncer` |
| `learn-distill` | `craftflow:learn-distiller` |
| `kind:remfix` + `origin:code-reviewer|silent-failure-hunter|integration-verifier|router` | `craftflow:component-builder` |

### Prompt scaffold for every agent

```text
## Task Context
- Task ID: {task_id}
- Parent Workflow ID: {workflow_uuid}
- Task Phase: {phase}
- Plan File: {plan_file or 'None'}
- Workflow Scope: wf:{workflow_uuid}
- Workflow Artifact: $PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.json
- Effort Directive: {low|medium|high — from fast-path.md dispatch table; append one-line steering per §4 Effort Steering Directives}

## User Request
{request}

## Requirements
{clarified requirements or 'See plan/design files'}

## Memory Summary
{brief activeContext summary}

## Project Patterns
{User Standards + Common Gotchas, trimmed if needed}

## Domain Context
{If UBIQUITOUS_LANGUAGE.md, DOMAIN_GLOSSARY.md, docs/domain/*.md, or project-context.md exist, include content. Otherwise omit section.}

## SKILL_HINTS
{router-detected skill list or "None"}
```

Optional sections:
- `## Pre-Answered Requirements` for BUILD when router already gathered decisions.
- `## Intent Contract` when a plan or design already defined goal, constraints, acceptance criteria, and named scenarios.
- `## Research Files` only when at least one research file exists.
- `## Research Quality` only when at least one research result exists.
- `## Design File` only for planner.
- `## Planning Review Findings` only for `re-plan`.
- `## Original User Request` only for `plan-gap-reviewer`.
- `## Approved Context Files` only for `plan-gap-reviewer`.
- `## Previous Agent Findings` only for integration-verifier and only after review/hunt phases.

### Prompt assembly rule

- Every routed prompt must be self-contained from the workflow artifact, approved files, and the current task contract.
- Do not rely on prior chat turns or completed-phase narrative when the same fact already exists in the workflow artifact, plan, design, or research files.
- Include only the current-phase objective, live blockers, approved decisions, and directly relevant evidence. Omit unrelated completed-phase detail.

### Effort Dispatch Rule

Before dispatching each agent, look up the phase in the `references/fast-path.md → #### Agent Dispatch Table → Effort` column. Append the corresponding steering directive (from `#### Effort Steering Directives`) as the last line of the `## Task Context` section in the agent scaffold.

- fast-path `build-implement` → `low`
- standard/escalated `build-implement` → `medium`
- `build-review`, `build-hunt` → `medium`
- `build-verify`, all re-verify → `high`
- `planner`, `plan-gap-reviewer`, `doubt-verifier`, `bug-investigator` → `high`
- `build-doc-sync`, `memory-finalize`, `learn-distill` → `low`
- All research tasks → `medium`

Record the assigned effort in `telemetry.effort.{agent}` in the workflow artifact and in the `agent_started` event log entry.

### Deterministic skill hints

- Router is the only authority allowed to load internal CRAFTFLOW skills.
- Agents may not self-activate `frontend-patterns`, `architecture-patterns`, or `debugging-patterns`.
- Include `craftflow:frontend-patterns` only when the request, changed files, plan, or design clearly targets UI/frontend work.
- Include `craftflow:architecture-patterns` only for multi-component, API, schema, auth, or integration-heavy work.
- Include `craftflow:research` only when planner or investigator receives `## Research Files`.
- Include project/domain skills only from `patterns.md ## Project SKILL_HINTS`.
- Skill precedence is strict:
  1. explicit user prompt
  2. project `CLAUDE.md` / repo standards / user standards
  3. approved plan and design docs
  4. domain-specific external skills
  5. internal CRAFTFLOW skills
  6. model heuristics

### Previous Agent Findings handoff

When invoking `integration-verifier`, pass:

```text
## Previous Agent Findings

### Code Reviewer
**Verdict:** {Approve|Changes Requested}
**Critical Issues:**
{reviewer critical issues or "None"}

### Silent Failure Hunter
**Critical Issues:**
{hunter critical issues or "None / not in this workflow"}
```

DEBUG skips hunter findings.

### Doubt-Verify Dispatch Rule

After `integration-verifier` returns PASS, dispatch `craftflow:doubt-verifier` when ANY of these conditions are true:
- `verification_rigor: critical_path` is set in the workflow artifact
- The phase included irreversible operations: data migration, schema change, auth flow, payment path, secret rotation
- The phase changed ≥3 files across ≥2 directories (cross-module blast radius)

**Do NOT dispatch doubt-verifier when:**
- `verification_rigor: standard` and no irreversible ops
- Integration verifier returned FAIL (remediate first)
- A doubt cycle already returned DOUBT_THEATER (escalate instead)

**Doubt-verify task shape:**
```text
TaskCreate({
  subject: "CRAFTFLOW doubt-verifier: Adversarial review cycle {N}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:doubt-verify\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:critical_path adversarial pass\n\nCycle: {N}\n\n## Artifact\n{artifact description: plan name, diff summary, or scenario table}\n\n## Contract\n{acceptance criteria from plan or integration-verifier exit criteria}",
  activeForm: "Adversarial doubt review"
})
```

**After doubt-verifier returns:**
- `DOUBT_VERDICT: CONFIRMED` → advance phase cursor, continue workflow
- `DOUBT_VERDICT: REFUTED` → create REM-FIX task (same as normal verification failure), increment loop counter
- `DOUBT_VERDICT: DOUBT_THEATER` → log event, skip further doubt cycles, advance to Memory Update with advisory note
- `CYCLE_COMPLETE: true` on cycle 3 → advance regardless of verdict (3-cycle hard stop)

**Doubt cycle counter:** Track in workflow artifact under `telemetry.loop_counts.doubt_verify`. Max 3 cycles; router enforces the hard stop.

### Task metrics and timing telemetry

Timing telemetry is measurement only. It must never bypass gates, phase exit, or remediation rules.

**Router-owned timestamp instrumentation:**
- When dispatching an agent task, append `"agent_started"` to the event log with `"ts": "{iso_now}"` before invoking the agent.
- When capturing agent output, append `"agent_completed"` to the event log with `"ts": "{iso_now}"`.
- At memory-finalize, for each agent that ran in this workflow, compute `completed_ts - started_ts` in seconds and write to `telemetry.agent_wall_clock_seconds.{agent}`.
- Compute `workflow_wall_clock_seconds` as the delta between the `workflow_started` event `ts` and the `memory_finalized` event `ts`.
- ISO timestamps are always UTC. Use `new Date().toISOString()` or equivalent. If the runtime does not expose a clock, omit the field rather than writing 0.

**Legacy path (Claude Code task metrics):**
- If Claude Code exposes task duration metrics via `TaskGet()`, use those as the primary source.
- If unavailable, use the event log delta above.
- Keep `task_metrics_available="unknown"` until one of these paths succeeds; set to `"event_log"` when using delta computation.

**Verifier workload telemetry (unchanged):**
- When `integration-verifier` reports a `### Timing & Workload` section, persist:
  - `telemetry.verifier.phase_exit_proof_runs`
  - `telemetry.verifier.extended_audit_runs`
  - `telemetry.verifier.workload_seconds`

Use telemetry to explain latency. Do not use it to auto-reduce verification scope.

## 8. Post-Agent Validation

### Read-only contracts

Primary signal:
- Line 1: `CONTRACT {"s":"...","b":...,"cr":...}`

Fallback heading on line 2:
- `## Review: Approve|Changes Requested`
- `## Error Handling Audit: CLEAN|ISSUES_FOUND`
- `## Verification: PASS|FAIL`
- `## Planning Review: Pass|Findings`

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
| component-builder | `STATUS`, `CONFIDENCE`, `PHASE_ID`, `PHASE_STATUS`, `PHASE_EXIT_READY`, `CHECKPOINT_TYPE`, `PROOF_STATUS`, `INPUTS`, `EXPECTED_ARTIFACTS`, `TDD_RED_EXIT`, `TDD_GREEN_EXIT`, `SCENARIOS`, `ASSUMPTIONS`, `DECISIONS`, `BLOCKED_ITEMS`, `SKIPPED_ITEMS`, `SCOPE_INCREASES`, `BLOCKING`, `NEXT_ACTION`, `REMEDIATION_NEEDED`, `REQUIRES_REMEDIATION`, `REMEDIATION_REASON`, `MEMORY_NOTES` |
| bug-investigator | `STATUS`, `VERIFICATION_RIGOR`, `CONFIDENCE`, `ROOT_CAUSE`, `TDD_RED_EXIT`, `TDD_GREEN_EXIT`, `VARIANTS_COVERED`, `BLAST_RADIUS_SCAN`, `SCENARIOS`, `ASSUMPTIONS`, `DECISIONS`, `BLOCKING`, `NEXT_ACTION`, `REMEDIATION_NEEDED`, `REQUIRES_REMEDIATION`, `REMEDIATION_REASON`, `NEEDS_EXTERNAL_RESEARCH`, `RESEARCH_REASON`, `MEMORY_NOTES` |
| planner | `STATUS`, `PLAN_MODE`, `VERIFICATION_RIGOR`, `CONFIDENCE`, `PLAN_FILE`, `PHASES`, `RISKS_IDENTIFIED`, `SCENARIOS`, `ASSUMPTIONS`, `DECISIONS`, `OPEN_DECISIONS`, `DIFFERENCES_FROM_AGREEMENT`, `RECOMMENDED_DEFAULTS`, `ALTERNATIVES`, `DRAWBACKS`, `PROVABLE_PROPERTIES`, `BLOCKING`, `NEXT_ACTION`, `REMEDIATION_NEEDED`, `REQUIRES_REMEDIATION`, `REMEDIATION_REASON`, `GATE_PASSED`, `USER_INPUT_NEEDED`, `MEMORY_NOTES` |
| web-researcher | `STATUS`, `FILE_PATH`, `BACKEND_MODE`, `SOURCES_ATTEMPTED`, `SOURCES_USED`, `QUALITY_LEVEL`, `KEY_FINDINGS_COUNT`, `WHAT_CHANGED_RECOMMENDATION`, `MEMORY_NOTES` |
| github-researcher | `STATUS`, `FILE_PATH`, `BACKEND_MODE`, `SOURCES_ATTEMPTED`, `SOURCES_USED`, `QUALITY_LEVEL`, `IMPLEMENTATIONS_FOUND`, `WHAT_CHANGED_RECOMMENDATION`, `MEMORY_NOTES` |
| doc-syncer | `STATUS`, `IMPACT_LEVEL`, `DOC_LAYERS_EVALUATED`, `DOC_FILES_UPDATED`, `DOC_FILES_SKIPPED`, `SKIP_REASON`, `AUDIT_DOCS_CREATED`, `AUDIT_DOCS_UPDATED`, `MEMORY_NOTES` |

If the YAML block is missing or malformed:
- Treat the task as invalid output.
- Do not continue the workflow based on prose alone.
- Re-run inline verification and fail safe.

### Intent Interview Gate (PLAN only)

Before brainstorming, check whether the task description is under-specified:

**Dispatch `Skill(skill="craftflow:intent-interview")` when ALL of:**
- `AUTO_PROCEED` is NOT `true` in `activeContext.md ## Session Settings`
- No saved plan file exists for this request in `activeContext.md ## References`
- At least ONE of:
  - Request is ≤3 sentences with no success definition
  - Request contains scope-ambiguous terms: "improve", "refactor", "clean up", "make it better", "fix things"
  - Request touches ≥2 systems or modules with no stated boundary

**Skip the interview (run brainstorming directly) when ANY of:**
- `AUTO_PROCEED: true` (JUST_GO mode — auto-skip)
- A saved plan file is already referenced
- Request explicitly names the file, function, or behavior to change

After `Skill(skill="craftflow:intent-interview")` completes, read the `## Intent Contract (From Interview)` block it emits. Persist it into:
- Workflow artifact `intent` field
- Planner prompt under `## Intent Contract (Pre-Interview)` (planner treats it as user-approved input)

### Inline brainstorming handoff

After `Skill(skill="craftflow:brainstorming")`, parse the fenced YAML block under
`### Brainstorming Handoff (MACHINE-READABLE)`.

Required field:
- `DESIGN_FILE`

If present:
- persist it into workflow artifact `design_file`
- pass it to planner as `## Design File`
- do not require `activeContext.md` to be updated first

### Contract overrides

| Agent | Override |
|-------|----------|
| component-builder | `STATUS=PASS` requires `TDD_RED_EXIT=1`, `TDD_GREEN_EXIT=0`, `PHASE_STATUS=completed`, `PHASE_EXIT_READY=true`, `PROOF_STATUS=passed`, empty `BLOCKED_ITEMS`, and a non-empty `SCENARIOS` array with at least one passing scenario. That passing scenario must include non-empty `name`, `command`, `expected`, `actual`, and `exit_code`. |
| bug-investigator | `STATUS=FIXED` requires `VERIFICATION_RIGOR` to be explicit, `TDD_RED_EXIT=1`, `TDD_GREEN_EXIT=0`, `VARIANTS_COVERED>=1`, a non-empty `BLAST_RADIUS_SCAN`, and a non-empty `SCENARIOS` array unless it explicitly set `NEEDS_EXTERNAL_RESEARCH=true`. At least one scenario name must start with `Regression:` and one with `Variant:`. Both required scenarios must include non-empty `command`, `expected`, `actual`, and `exit_code`. |
| code-reviewer | `APPROVE` + critical issues becomes `CHANGES_REQUESTED` |
| code-reviewer | `APPROVE` with zero findings across ALL dimensions AND fewer than 3 file:line evidence citations → trigger fallback inline verification. Rubber-stamp approvals without substantive analysis are invalid. |
| silent-failure-hunter | `CLEAN` + critical issues becomes `ISSUES_FOUND` |
| silent-failure-hunter | `CLEAN` with zero error-handling sites inspected OR zero files scanned → trigger fallback inline verification. A CLEAN verdict requires stated scope. |
| integration-verifier | `PASS` + critical issues becomes `FAIL`; scenario totals must reconcile with the scenario table and evidence array; every counted scenario must map to a concrete evidence row; every scenario row must contain non-empty `Expected` and `Actual` values |
| planner | `PLAN_CREATED` or `DECISION_RFC_CREATED` requires non-empty `PLAN_FILE`, explicit `PLAN_MODE`, explicit `VERIFICATION_RIGOR`, `CONFIDENCE>=50`, `GATE_PASSED=true`, a non-empty `SCENARIOS` array, `OPEN_DECISIONS=[]`, and `DIFFERENCES_FROM_AGREEMENT` explicitly present. `PLAN_MODE=decision_rfc` also requires non-empty `ALTERNATIVES` and `DRAWBACKS`; `VERIFICATION_RIGOR=critical_path` requires non-empty `PROVABLE_PROPERTIES`. |
| doc-syncer | `STATUS=COMPLETE` requires `DOC_LAYERS_EVALUATED` non-empty and at least one entry in `DOC_FILES_UPDATED` or `AUDIT_DOCS_CREATED`; `STATUS=SKIPPED` requires non-empty `SKIP_REASON` — `DOC_LAYERS_EVALUATED` MAY be empty (fast-path classifier exits before per-layer evaluation when `IMPACT_LEVEL=none` is detected immediately); `STATUS=PARTIAL` requires at least one entry in `DOC_FILES_UPDATED` or `AUDIT_DOCS_CREATED` and at least one layer in `DOC_LAYERS_EVALUATED` — router advances to Memory Update and persists `doc_sync_partial=true` in `results.doc_syncer`; `STATUS=FAIL` blocks workflow. |
| plan-gap-reviewer | `PASS` requires `BLOCKING_FINDINGS_COUNT=0` and `REPLAN_NEEDED=false`; `FINDINGS` requires explicit finding buckets and a non-empty `REPLAN_REASON` when blocking findings exist. |

Convergence rule:
- If evidence is incomplete, contradictory, or missing for a required pass path, do not advance the workflow.
- Set the workflow artifact `quality.convergence_state` to `needs_iteration` and stop on the appropriate remediation or clarification gate instead of treating the task as good enough.

## 9. Remediation And Workflow Rules

- When remediation, scope resolution, review-to-build escalation, planner clarification, investigation continuation, or the verifier REVERT gate is in play, immediately read `references/remediation-and-research.md`.
- Use the `## 9. Remediation And Workflow Rules` block there as canonical router law.

## 10. Research Orchestration

- See `references/remediation-and-research.md` and apply its `## 10. Research Orchestration`, `## Research Quality`, and `## Research Files` blocks whenever research is triggered or consumed.

## Research Quality

- See `references/remediation-and-research.md` and apply its `## Research Quality` block whenever research quality must be summarized or persisted.

## Research Files

- See `references/remediation-and-research.md` and apply its `## Research Files` block whenever research file paths are handed to planner or investigator.

## 11. Re-Review Loop

- See `references/remediation-and-research.md` and apply its `## 11. Re-Review Loop` block whenever a `kind:remfix` task completes.

## 12. Chain Execution Loop

```text
1. TaskList()
2. Select tasks in the active `wf:` where:
   - status is pending or in_progress
   - blockedBy is empty or all blockers are completed
3. If the runnable task kind is memory:
   - execute inline in the main context
   - persist workflow artifact results + Memory Notes from the task description
   - append `memory_finalized` to `$PROJECT_ROOT/.craftflow/state/workflows/{wf}.events.jsonl`
   - clean up the matching [craftflow-internal] memory_task_id entry
   - mark the memory task completed
   - mark the parent workflow task completed
   - continue
4. Otherwise, map each runnable task through the dispatcher table.
4a. If `build_mode == "fast_path"` in the workflow artifact for this BUILD:
   - If `build_mode` is null (legacy artifact or non-BUILD workflow): treat as `"standard"` — skip this step, continue to step 5.
   - code-reviewer and silent-failure-hunter tasks were NOT created — skip step 5 parallel dispatch check for them
   - When integration-verifier completes with FAIL:
     - Read `references/build-workflow.md → ### Fast Path Escalation` block and execute it
     - Do not advance phase cursor
5. If `code-reviewer` and `silent-failure-hunter` are both ready in BUILD (build_mode == "standard" only):
   - mark both in_progress first
   - invoke them in the same message
   - If parallel invocation fails or is unavailable (API error, rate limit): fall back to sequential execution (reviewer first, then hunter). Never block a workflow because parallelism is unavailable. Log `event=parallel_fallback` in the workflow event log.
5a. If both `research-web` and `research-github` tasks are runnable in the same round (PLAN workflow):
   - Mark both in_progress first
   - Dispatch them in the same message (same pattern as reviewer+hunter in BUILD)
   - Both are read-only with no file-write overlap — safe to parallelize
   - If parallel invocation fails (rate limit, API error): fall back to sequential (web first, then github). Log `event=parallel_fallback` in the event log.
   - Wait for BOTH to complete before proceeding to planner or investigator.
6. After each agent returns:
   - capture memory payload immediately
   - validate output
   - persist task-state side effects
   - if BUILD review and hunt are both complete for the current phase, write one router-owned merged findings summary into the existing workflow results before verifier handoff
   - apply workflow rules
   - for BUILD, run `phase_exit_gate`; if the current phase is not complete, persist `phase_status={partial|blocked}` and stop
   - never advance to the next phase or workflow step on apology prose alone
   - if two agents in the same phase return contradictory verdicts (e.g., reviewer approves but verifier fails on the same evidence), treat the stricter verdict as authoritative and do not average or reconcile the signals. Log the contradiction in `status_history`.
   - doc-syncer `STATUS=SKIPPED` is a passing state; advance to Memory Update immediately
   - doc-syncer STATUS=PARTIAL: soft pass; advance to Memory Update; persist doc_sync_partial=true in workflow artifact results.doc_syncer for user review
   - On fast path (`build_mode == "fast_path"` or `"fast_path_escalated"`): doc-syncer task was never created; when verifier PASS, advance directly to Memory Update (no doc-sync step)
7. Repeat until all tasks in the active `wf:` are completed.
```

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
6. Persist `[craftflow-internal] memory_task_id: {memory_task_id} wf:{workflow_uuid}` only if it matches the active workflow.

### Verifier findings handoff

Before invoking `integration-verifier` in BUILD:
- Read `results.reviewer` and `results.hunter` from the workflow artifact.
- Build `## Previous Agent Findings` exactly in the format verifier expects.
- Never invoke verifier without that section when review/hunt already ran.
- **fast-path exception:** When `build_mode == "fast_path"`, omit `## Previous Agent Findings` from the verifier prompt entirely — no reviewer or hunter ran. The verifier must run independent scenario coverage. When `build_mode == "fast_path_escalated"` (after escalation), the merged findings handoff IS required using the standard format above.

## 13. Memory Finalization

The memory task executes inline only. Never spawn it as a sub-agent.

Memory is written to two tiers. Route each `MEMORY_NOTES` field as follows:

| MEMORY_NOTES field | Write destination | Rationale |
|--------------------|-------------------|-----------|
| `learnings` | `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/activeContext.md ## Learnings` | Workflow-specific causal insights |
| `patterns` | `$PROJECT_ROOT/.craftflow/state/project/patterns.md ## Common Gotchas` | Durable conventions that apply to all future workflows |
| `verification` | `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/progress.md ## Verification` | Proof evidence scoped to this build/debug/review run |
| `deferred` | `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/activeContext.md` as `[Deferred]: ...` | Non-blocking follow-ups scoped to this workflow |

Cross-workflow promotion rule: If a `learnings` item is a project-wide constraint
(not specific to the current task), also copy it to
`$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Learnings`.
Use judgment: workflow-local observations stay in
`$PROJECT_ROOT/.craftflow/state/workflows/{wf}/`; durable project truths belong in
`$PROJECT_ROOT/.craftflow/state/project/`.

Memory finalization permit (required before any `.md` memory write):
- The `craftflow_pretooluse_guard.py` hook blocks direct writes to protected memory files.
- Before writing the first memory file, create the permit token:
  ```
  Bash("printf '%s' '{workflow_uuid}' > \"$PROJECT_ROOT/.craftflow/state/.memory-finalize\"")
  ```
- After all memory files are written, clear the permit:
  ```
  Bash("rm -f \"$PROJECT_ROOT/.craftflow/state/.memory-finalize\"")
  ```
- If workflow_uuid is unavailable (fallback path), omit the permit steps — the guard will audit-log but not block in that case.

The memory task also:
- Replaces `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/progress.md ## Tasks` with the active workflow snapshot.
- Keeps only the most recent 10 items in `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/progress.md ## Completed`.
- Updates `$PROJECT_ROOT/.craftflow/state/project/progress.md ## Completed` with a one-line summary of the finished workflow.
- Removes the matching `[craftflow-internal] memory_task_id` line from `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## References`.
- If any artifact or memory write fails, stop immediately (clear the permit first). Never advance the workflow after a failed persistence write.

Fallback: If `workflow_uuid` is unavailable, write to root-flat files
(`$PROJECT_ROOT/.craftflow/state/activeContext.md`, `$PROJECT_ROOT/.craftflow/state/patterns.md`,
`$PROJECT_ROOT/.craftflow/state/progress.md`) as in prior versions.

For PLAN:
- Ensure `- Plan: {plan_file}` remains correct in `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## References`.
- Ensure `- Design: {design_file}` remains correct in `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## References` when a design exists.
- If a plan exists, record `Plan saved: {plan_file}` in `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Recent Changes`.
- If a plan exists, set `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Next Steps` to `1. Execute plan: {plan_file}` unless the workflow ended in clarification-needed state.

For DEBUG:
- Preserve the latest `[DEBUG-RESET: wf:{workflow_task_id}]` section in `## Recent Changes` and summarize the final result beneath it.

## 14. Hard Rules

- Router must run in the main Claude Code session, never inside a sub-agent.
- Router is the only orchestration state owner. Agents may propose remediation or next actions, but only the router creates, blocks, unblocks, reuses, or completes orchestration tasks.
- Never stop after one agent if the workflow chain has more runnable tasks.
- Never rely on prose when `wf:`, `kind:`, `origin:`, `phase:`, or `scope:` can answer the question.
- Never use an unscoped task lookup in critical paths.
- Never treat stored task IDs as durable truth across workflows.
- Never spawn Memory Update as a sub-agent.
- Never create `CRAFTFLOW TODO:` tasks. Non-blocking discoveries go into `**Deferred:**` memory notes.
- Never let REVIEW create implementation tasks without an explicit router/user transition into BUILD.
- Never report a workflow outcome (pass, fixed, complete) to the user without first confirming the verification evidence that supports that claim. "I believe it works" is not evidence. [EASY TO MISS: "I ran the tests and they passed" without showing command output, exit codes, or scenario evidence is also not evidence. Require concrete proof artifacts, not agent assertions.]
- Never let a remediation loop run more than 3 cycles without a human checkpoint. Drift accumulates silently in long chains.
- Only parallelize agents whose file-write surfaces do not overlap. Reviewer and hunter are read-only and safe to parallelize. Two write agents on overlapping files must be serialized. [EASY TO MISS: Each parallel agent must have a distinct phase value and unique task description. Identical prompts cause agents to duplicate work or silently clobber each other's output.]
- Agents must never inherit raw conversation context. They receive only the structured scaffold from the dispatcher. Leaking conversation history into agent prompts causes scope pollution and non-reproducible behavior.
- Maintain professional objectivity in all routing decisions. Do not rationalize a failing workflow as "close enough" or downgrade critical findings to avoid remediation. The router exists to enforce quality, not to please.
- `DIFF_DRIVEN_DOCS: skip` in Session Settings disables doc-syncer for projects that manage documentation separately; when present, skip `build-doc-sync` task creation and block Memory Update on `verifier_task_id` directly.
- Agents must never reference or read internal skill files from other agents or skills (e.g., component-builder must never read code-review-patterns/SKILL.md). Cross-agent knowledge flows exclusively through router-mediated scaffolds and workflow artifacts.
- Never use EnterPlanMode. Claude Code's native plan mode is incompatible with Craftflow. Planning requests go through the Craftflow PLAN workflow (brainstorming → planner → bounded fresh review → memory finalization), which provides orchestration state, workflow artifacts, intent contracts, and verification. Native plan mode provides none of these.
