# Resume and hydration

After memory load:

```text
TaskList()
```

In task-tool fallback mode (`references/task-tool-fallback.md → ## Resume fallback`), skip `TaskList()` and resume from the workflow artifact.

Hydration rules:
- Find active parent workflow tasks by subject prefix `CRAFTFLOW BUILD:`, `CRAFTFLOW DEBUG:`, `CRAFTFLOW REVIEW:`, `CRAFTFLOW PLAN:`.
- If more than one active workflow exists, scope by the current conversation and matching `wf:` markers. Do not resume a workflow you cannot scope confidently.
- Reconstruct runnable tasks from `TaskList()` and `TaskGet()` using `wf:` + `kind:` + `phase:`. Do not rely on stored task IDs for correctness.
- Read and write only the state namespace. Ignore legacy `.craftflow/*.md` and `.craftflow/workflows/*` state during hydration. State lives under `.craftflow/state/`.
- `[craftflow-internal] memory_task_id` in `activeContext.md` is only a transient optimization. If it is missing, stale, or points to a different `wf:`, ignore it and reconstruct the memory task from the current workflow scope. [EASY TO MISS: stale memory_task_id is the #1 cause of cross-workflow pollution]
- Never use an unscoped fallback like "first pending Memory Update task". [EASY TO MISS: unscoped lookups silently pick up orphan tasks from prior workflows]
- **Session re-stamp on resume.** After scoping the resumed workflow, run `python3 "$CLAUDE_PLUGIN_ROOT/scripts/craftflow_workflow_id.py" --session-id`. An empty line means the id is unavailable or invalid: change nothing. Otherwise re-stamp (set `session_id` to the printed id, bump `updated_at`, append a `session_rebound` event `{"ts":"{iso_timestamp}","wf":"{workflow_uuid}","event":"session_rebound","agent":"router","from":"{old_session_id|null}","to":"{session_id}"}` to `events.jsonl`) ONLY when (a) the artifact `session_id` is null or absent, or (b) the user explicitly resumes this workflow (not status/review hydration) AND the artifact `updated_at` is older than 15 minutes. In every other case leave `session_id` as is and never rebind a workflow another live session may own.

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
- Interrupted jev-auto-decide: if `## Decisions` instead contains a
  `[jev-auto-decided: wf:{workflow_uuid} choice:{choice} confidence:{confidence}]` marker (the
  `### Scope resolution` procedure's Jev auto-decide branch writes this marker, creates the
  REM-FIX, then rewrites the marker a second time to append `consumed:true` — a session
  interrupted between the write and that final `consumed:true` rewrite leaves the marker without
  the tag) that does **not** already end in `consumed:true`, treat it as an interrupted
  auto-decide, not a pending human ask.
  - **Correlation, not bare existence:** do not treat "some `kind:remfix` task exists anywhere in
    this `wf:`" as proof this marker was already handled. A single workflow can legitimately
    accumulate multiple sequential REM-FIX cycles over its lifetime (this workflow's own
    `remediation_history` already has several), so a workflow-wide existence check is vacuously
    satisfied by any unrelated remfix task and would silently skip a genuinely interrupted marker.
    The `consumed:true` tag lives on the marker itself and is written only once *this exact
    marker's* REM-FIX is confirmed created — that tag, not a task-list search, is the
    correlation signal.
  - do not ask the user — the marker already records a resolved Jev decision
  - **Existence check before create (closes the remaining interruption window):** the
    create-then-tag sequence below is itself two separate, non-atomic steps — a session
    interrupted between "create the REM-FIX" and "rewrite the marker to append `consumed:true`"
    would otherwise cause this branch to create a *second* REM-FIX for the same already-created
    one on resume. Before creating, check for an existing `kind:remfix` task in this `wf:` whose
    description's `reason:` line references this exact marker's `choice`/`confidence` values and
    was created after this marker was written. If found, reuse that task as the REM-FIX (skip
    creation, go straight to the `consumed:true` rewrite below) instead of creating a duplicate.
  - create the REM-FIX now (only if no matching task was found above) using the `scope`/`choice`
    already recorded in the marker (`scope:ALL_ISSUES` if `choice:all_issues`, else
    `scope:CRITICAL_ONLY`) through the normal Circuit breaker and rule `1a` procedure
  - immediately rewrite the marker to append `consumed:true`, exactly as the happy path in
    `### Scope resolution` does, so a later resume does not re-process the same marker again
  - block downstream re-review / re-hunt / verifier tasks as normal
  - stop after task creation so the next turn resumes from task state, not from repeated prose
    parsing
  - leave the `[jev-auto-decided: ... consumed:true]` marker in place (it is not a pending-ask
    marker — it is already-consumed history, matching how a normal
    `[SCOPE-DECISION-PENDING: ...]` marker is only removed once its own REM-FIX exists)

Safety rules:
- If a task list is shared across sessions, always scope by `wf:` before resuming.
- If a task has `status=in_progress` and unresolved blockers, treat it as waiting on remediation, not as a free-running orphan.
- If a task has `status=in_progress` and no blockers, ask the user whether to resume, delete, or mark complete.
- If legacy tasks exist with subjects starting `BUILD:`, `DEBUG:`, `REVIEW:`, or `PLAN:` without the `CRAFTFLOW` prefix, ask whether to resume the legacy workflow or start a fresh CRAFTFLOW workflow.

## Artifact session_id rules

  - Context-nudge binding: the nudge binds a payload `session_id` to the artifact whose `session_id` matches exactly. After `/clear` or a new session the nudge sees `no_live_candidate` for stamped artifacts until the router restamps.
  - PreToolUse: `latest_live_workflow_file` still falls back to the newest live artifact when nothing matches the session (pre-existing limitation; MEDIUM-4, deferred to a later change). Only non-empty string stamps trigger its widen scan.
  - The plan-then-build ambiguity (A4): a plan workflow and the build workflow that follows it can run in the same session, so the chosen artifact's `session_id` equalling the payload `session_id` does not by itself identify which of the two is meant.

Task creation while resuming (the REM-FIX created after a scope decision, a doubt-verify or re-verify task, a research task): every `TaskCreate()` description starts with the metadata lines of `references/workflow-start.md → ## 3. Task Metadata Contract`; read that section before the first one.
