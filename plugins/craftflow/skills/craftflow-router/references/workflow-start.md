## Task tool capability detection

Runs once per session, immediately after `## 0.` resolves `PROJECT_ROOT` and before the first
workflow artifact write of that session (i.e. before `SKILL.md → ## 5. Workflow Preparation` creates a new
workflow, and before `SKILL.md → ## 4. Resume And Hydration` needs to decide whether `TaskList()` is safe to
call). Never re-probe within the same session once a value is recorded — reuse the in-context
result for every later workflow created or resumed this session. Always re-probe fresh in a new
session; never trust a `capabilities.task_tools_available` value carried over from a prior
session's resumed artifact (that value can go stale — tool availability has been observed to
reappear or disappear across restarts).

Detection procedure:
1. Call `ToolSearch(select: "TaskCreate,TaskList,TaskGet,TaskUpdate")` (or the top-level tool
   listing query if the `select` parameter is unsupported in the current Claude Code build).
2. All 4 names present in the result -> `task_tools_available = true`.
3. None of the 4 names present -> `task_tools_available = false`.
4. Any ambiguous, partial, or errored probe result -> fail-safe to `task_tools_available = false`.
   Never assume `true` without an unambiguous positive confirmation.
5. Record the boolean under `capabilities.task_tools_available` in the workflow artifact JSON
   (see `SKILL.md → ## 6. Workflow Task Graphs` and `Parent workflow creation` below for the `--task-tools-available`
   flag that stamps this field at artifact creation, and `SKILL.md → ## 2a. Workflow Artifact And Hook Policy` for the schema
   documentation this field is added to).

This section's content is inherently Claude-only vocabulary (`task_tools_available`,
`ToolSearch`) and always lives in `craftflow-router`, never in
`skills/_shared/router-protocol.md`, regardless of whether `## 0.`'s own project-root resolution
algorithm is shared or host-specific.

## 3. Task Metadata Contract

Every CRAFTFLOW task description starts with normalized metadata lines:

```text
wf:{workflow_uuid}
kind:{workflow|agent|remfix|memory|reverify|research}
origin:{router|component-builder|bug-investigator|code-reviewer|silent-failure-hunter|integration-verifier|planner}
phase:{build|build-implement|build-review|build-hunt|build-verify|build-doc-sync|learn-distill|skill-distill|debug|debug-investigate|debug-review|debug-verify|doubt-verify|review|review-audit|plan|plan-create|plan-review-gap-1|plan-review-gap-2|memory-finalize|re-review|re-hunt|re-verify|re-plan|research-web|research-github|plan-bakeoff-candidate-opus|plan-bakeoff-candidate-sonnet|plan-bakeoff-candidate-haiku|plan-bakeoff-candidate-fable|plan-bakeoff-judge}
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

## Parent workflow creation

Use this pattern for every new workflow:

1. Mint the id, worktree names and `iso_timestamp` AND write the v10 artifact + event log in one helper call, in three sub-steps. Never splice request text into a shell string (quotes, `$(...)` and backticks in it would be executed or corrupt the command); it travels through a per-session file instead.

   a. Resolve the install dir and the session id (no workflow id is needed for this). `CRAFTFLOW_INSTALL` is resolved by this one-line bootstrap, the canonical form that other sections reuse verbatim:

```bash
CRAFTFLOW_INSTALL=$(python3 -c "import json,pathlib;print(json.loads(pathlib.Path.home().joinpath('.claude/plugins/installed_plugins.json').read_text())['plugins']['craftflow@craftflow'][0]['installPath'])")
python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_workflow_id.py" --session-id
```

   b. Write the user request VERBATIM with the `Write` tool to `$PROJECT_ROOT/.craftflow/state/.router-request-{session_id}.txt` (`{session_id}` = the line printed above; use `nosession` when it is empty). The per-session name keeps concurrent sessions apart; the Write guard allows this path (`.craftflow/state/` is inside the project, unlike `/tmp`).

   c. Mint (the helper reads the file, mints, writes the artifact, then deletes the request file; it rejects a request file older than 120 s as a stale leftover, and an empty request):

```bash
CRAFTFLOW_INSTALL=$(python3 -c "import json,pathlib;print(json.loads(pathlib.Path.home().joinpath('.claude/plugins/installed_plugins.json').read_text())['plugins']['craftflow@craftflow'][0]['installPath'])")
python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_workflow_id.py" \
  --request-file "$PROJECT_ROOT/.craftflow/state/.router-request-{session_id}.txt" --project "$PROJECT_ROOT" \
  --emit-env --init-artifact \
  --workflow-type "{WORKFLOW}" --phase "{build|debug|review|plan}" \
  --task-tools-available "{true|false|unknown}"
```

`{WORKFLOW}` is `BUILD|DEBUG|REVIEW|PLAN` and `--task-tools-available` is the detected `capabilities.task_tools_available` value. The call prints raw `KEY=value` lines (split each on the first `=`; no quoting to undo); read `workflow_uuid` · `iso_timestamp` · `worktree_dir` · `worktree_branch` · `session_id` · `session_id_json` from that output and substitute them into later steps (do not `eval` the output: the safe-shell guard denies it). **Mint-failure stop rule:** if the call exits non-zero, or its output has no `workflow_uuid=` line, STOP — do not call `TaskCreate()`, do not create child tasks, and report the `Error:` line to the user; a failed mint writes nothing (the helper removes any file it created) and is never to be treated as success. The helper writes `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.json` (the v10 artifact, schema per `references/workflow-artifact-and-hook-policy.md`, including `circuit_breaker`), `{workflow_uuid}.events.jsonl` (the `workflow_started` line) and creates the `workflows/{workflow_uuid}/` directory; it refuses (non-zero exit, naming the conflicting file) to overwrite an existing artifact or events log. No artifact `Write()` and no separate `mkdir` are needed. Optional flags:

- `--task-id "{parent_task_id}"` is optional and normally omitted: the artifact is written before `TaskCreate()`, so the `workflow_started` event's `task_id` is `null`.
- **Only if `## 0.` recorded a `project_root_resolution_fallback` reason for this session** (`TOPLEVEL_EXIT != 0` and outcome `NO_REPO_FOUND` or `RESOLVE_SCRIPT_ERROR`): `--fallback-reason "NO_REPO_FOUND"` (or `"RESOLVE_SCRIPT_ERROR"`). This appends the `project_root_resolution_fallback` `status_history` entry and a second events.jsonl line. Skip in the common single-repo case.
- **Only if `## 0.` step 1a set `WORKSPACE_WRITABLE_PATHS_JSON` to something other than `[]`** (`TOPLEVEL_EXIT != 0` and the variable is set and `!= '[]'`): `--workspace-writable-paths-json "$WORKSPACE_WRITABLE_PATHS_JSON"` (fills `workspace_writable_paths`; default `[]`). Additionally, if `WORKSPACE_WRITABLE_PATHS_DROPPED_JSON` is non-empty, `--writable-paths-dropped-json "$WORKSPACE_WRITABLE_PATHS_DROPPED_JSON"` (appends the `workspace_writable_paths_entries_dropped` entry and events line).

ID format: `wf-{slug}-{YYYYMMDD-HHMMSS}-{8hex}`.
Slug = slugified git branch name (if a genuine feature branch, i.e. not main/master/develop/dev/trunk or a craftflow-generated `wf-`/`worktree-` branch) — otherwise slugified request text.
The `iso_timestamp` from the helper is the authoritative creation timestamp — use it for any later `{iso_timestamp}` placeholder. `session_id_json` is a raw JSON fragment (a double-quoted string, or `null` when the session id is absent); paste it as-is where a JSON value is needed, and `session_id` is empty in that case. The project's `.craftflow/` directory must already exist (the `## 2.` memory-load step creates it); otherwise the mint fails with `Error:` and writes nothing.

2. Create the parent workflow task with that UUID (after step 1, so the artifact exists first):

```text
TaskCreate({
  subject: "CRAFTFLOW {WORKFLOW}: {summary}",
  description: "wf:{workflow_uuid}\nkind:workflow\norigin:router\nphase:{build|debug|review|plan}\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:User request\n\nUser request: {request}\nChain: {chain description}",
  activeForm: "{workflow active form}"
})
```

Only create child tasks after the v10 artifact and state directory exist (step 1 guarantees both). The `workflows/{workflow_uuid}/` directory is where the memory-finalize task writes workflow-scoped memory (activeContext.md, patterns.md, progress.md for this workflow only).

