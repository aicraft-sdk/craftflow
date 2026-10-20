# Router protocol: multi-repo workspace root resolution

   **1a. Multi-repo workspace root resolution** (only reached when `TOPLEVEL_EXIT != 0`):
   ```bash
   CRAFTFLOW_INSTALL=$(python3 -c "import json,pathlib;print(json.loads(pathlib.Path.home().joinpath('.claude/plugins/installed_plugins.json').read_text())['plugins']['craftflow@craftflow'][0]['installPath'])")
   CRAFTFLOW_INSTALL_EXIT=$?
   REQUEST_FILE="{session_scratchpad_dir}/cf-request.txt"
   RESOLVE_RESULT=$(python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_resolve_workspace_root.py" \
     --cwd "$(pwd)" \
     --request-file "$REQUEST_FILE")
   RESOLVE_EXIT=$?
   ```
   (A non-zero `CRAFTFLOW_INSTALL_EXIT` here is not handled as a separate branch — it surfaces
   downstream as a non-zero `RESOLVE_EXIT` from the next command, an empty/unusable
   `$CRAFTFLOW_INSTALL` path, which the existing `RESOLVE_EXIT != 0` handling below already
   catches.)
   Before this call, write the user request VERBATIM with the `Write` tool to `$REQUEST_FILE`, where
   `{session_scratchpad_dir}` is the session scratchpad directory given in the session environment (the Write guard
   denies the system temp dir from a non-repo cwd; the scratchpad is the one location it allows there) — never splice
   request text into a shell string. The script never mutates git or
   filesystem state — it only reads cwd's immediate child directories and runs non-mutating
   `git rev-parse --show-toplevel` calls.

   - **If `RESOLVE_EXIT != 0`** (the script itself could not complete the scan, e.g. cwd
     unreadable): do not parse `$RESOLVE_RESULT`. Treat identically to `NO_REPO_FOUND` below.
   - **Otherwise**, parse the outcome:
     ```bash
     RESOLVE_OUTCOME=$(printf '%s' "$RESOLVE_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['outcome'])")
     ```
     Also capture the workspace-root file allowlist `$RESOLVE_RESULT` may carry — safe and inert
     for every outcome reachable in this branch (`DETERMINISTIC`, `AMBIGUOUS`, or `NO_REPO_FOUND`
     reached via a successful `RESOLVE_EXIT == 0` scan), since the resolver script's
     `resolve()` never includes these keys for `NO_REPO_FOUND` at all — `.get(..., [])` degrades to
     an empty list in that case, exactly as intended. This capture is explicitly UNREACHABLE when
     `RESOLVE_EXIT != 0` (the branch immediately above this one, which never parses
     `$RESOLVE_RESULT` at all):
     ```bash
     WORKSPACE_WRITABLE_PATHS_JSON=$(printf '%s' "$RESOLVE_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('workspace_writable_paths', [])))")
     WORKSPACE_WRITABLE_PATHS_DROPPED_JSON=$(printf '%s' "$RESOLVE_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('workspace_writable_paths_dropped', [])))")
     ```
     `WORKSPACE_WRITABLE_PATHS_JSON` is held in-session — it is written into the workflow artifact
     at **Parent workflow creation** (`## 6.`; Claude Code: `references/workflow-start.md`) below, the same ordering constraint the
     `project_root_resolution_fallback` reason already works around (the artifact doesn't exist
     yet at this point in `## 0.`). If `WORKSPACE_WRITABLE_PATHS_DROPPED_JSON` is non-empty (`!=
     '[]'`), fold it into `## 6.`'s own initial event-log write alongside `workflow_started`, the
     same conditional pattern as `project_root_resolution_fallback` below —
     `{"event":"workspace_writable_paths_entries_dropped","dropped":<value>}`.
     **Caveat — the two in-session values are NOT equivalent in what is lost if they never reach
     `## 6.`:** `project_root_resolution_fallback` is a diagnostic string only — its own note above
     already documents that nothing functional is lost if it never gets durably recorded.
     `WORKSPACE_WRITABLE_PATHS_JSON` is a functional payload — nothing else re-echoes or reuses it
     between this capture and `## 6.`'s write, so if it is lost in-session (e.g. the session ends
     before `## 6.` runs), the workspace-root allowlist is silently disabled for that workflow
     (the artifact's `workspace_writable_paths` stays at its `[]` default). The downstream guard
     in `craftflow_hooklib.py`/`craftflow_pretooluse_guard.py` still fails closed regardless — an
     empty allowlist only denies writes to the workspace-root files it would otherwise have
     permitted, it never grants anything extra.
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
