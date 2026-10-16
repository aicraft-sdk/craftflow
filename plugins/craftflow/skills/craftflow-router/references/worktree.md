# Worktree finalize (BUILD)

Read this after `integration-verifier` returns PASS on the final phase and before memory-finalize, when `worktree_mode == "auto_created"`, and on resume when `pending_gate` holds a worktree gate.

Immediately before this step (and immediately before memory-finalize when no worktree exists), run the clean-state check per `references/harness-self-checks.md § Clean-State Check` and fold any findings AND any skipped (unscannable) entries into the deferred memory notes.

## The finalize call

`python3 "$CRAFTFLOW_SCRIPTS/craftflow_worktree_finalize.py" --wf {workflow_uuid} --project-root "$PROJECT_ROOT"` prints one JSON line `{"outcome","pending_gate","message","worktree_mode","details"}`. Finalize does everything step 4 used to: the merge lock, the clean-tree check, `git merge`, the copy fallback, the worktree and branch cleanup, and the artifact write (`worktree_mode`, `pending_gate`), composing `craftflow_worktree_lock_staleness.py` and `craftflow_worktree_copy_fallback.py`; `craftflow_worktree_merge_guard_check.py` is its proof harness, not a runtime step. Do none of it by hand.

Exit code 0: act on `outcome` (table below). Exit 3: outcome `artifact_unreadable`. Exit 4: the outcome is real but finalize could not write the workflow artifact (`details.artifact_write_error`); treat it as NOT a success, whatever the outcome says: do not continue to memory-finalize, show `message` verbatim (it ends with a WARNING), ask the user to repair the workflow artifact (set `worktree_mode` and `pending_gate` to match the outcome), then resume. Any other exit code (exit 2 is a usage error) or no parseable JSON line: last row.

## Outcomes

| outcome | pending_gate | router action | message source |
|---|---|---|---|
| `merged_and_removed` | `null` | continue to doc-sync/memory-finalize; finalize already set `worktree_mode: "merged_and_removed"` and cleared a stale worktree `pending_gate`, so write neither; `details.recovered` means an earlier run finished and only the record was missing; if `details.lock_release_failed` is set, show the WARNING in `message` (delete `.claude/worktrees/.merge.lock` by hand before the next BUILD) and still continue | message |
| `skipped_not_auto_created` | `null` | continue to memory-finalize (nothing to merge) | message |
| `worktree_path_missing` | `null` | continue on the main tree; finalize already logged the event | event |
| `worktree_branch_missing` | `null` | continue on the main tree; finalize already logged the event | event |
| `worktree_merge_locked` | `worktree_merge_locked` | stop before memory-finalize; show `message` verbatim; resume re-runs the call | message |
| `worktree_dirty_main_tree` | `worktree_dirty_main_tree` | stop before memory-finalize; show `message` verbatim; resume re-runs the call | message |
| `worktree_merge_conflict` | `worktree_merge_conflict` | stop before memory-finalize; show `message` verbatim; resume re-runs the call | message |
| `worktree_merge_unrecognized_failure` | `worktree_merge_unrecognized_failure` | stop before memory-finalize; show `message` verbatim (it also covers a `worktree_path` finalize refused as not `<project root>/.claude/worktrees/<name>`: repair it in the artifact, then resume); resume re-runs the call | message |
| `worktree_copy_fallback_failed` | `worktree_copy_fallback_failed` | stop before memory-finalize; show `message` verbatim; resume re-runs the call | message |
| `worktree_cleanup_failed` | `worktree_cleanup_failed` | stop before memory-finalize; show `message` verbatim; resume re-runs the call | message |
| `policy_invalid` | `policy_invalid` | stop; fix `config/policy.json`; resume re-runs the call | message |
| `artifact_unreadable` | none (exit 3) | stop; repair or restore the workflow artifact; resume | message |
| non-zero exit or unparseable | `worktree_merge_unrecognized_failure` | persist `pending_gate: worktree_merge_unrecognized_failure`, say finalize returned no outcome, ask the user to inspect `git status` in `$PROJECT_ROOT` and the events log, stop | none |

Gate rows stop before memory-finalize and show `message` verbatim; resuming re-runs the call. A gate has already persisted its `pending_gate` and released the merge lock it held (the message says what was left in place); `details.lock_release_failed` on a gate means the lock directory is still there and must be deleted by hand.

## Safety

Safety: Worktree creates are idempotent in the event log. If a resume finds `worktree_mode: "auto_created"` already set, re-use `worktree_path` from the artifact rather than creating a new worktree. If `worktree_path` is null despite `worktree_mode: "auto_created"`, treat as fallback and proceed with main tree. If a resume finds `pending_gate` set to `worktree_merge_locked`, `worktree_dirty_main_tree`, `worktree_merge_conflict`, `worktree_merge_unrecognized_failure`, `worktree_copy_fallback_failed`, or `worktree_cleanup_failed`, re-enter this step by calling finalize again (the old step 4a) — none of these gates require any bespoke resume branch beyond the generic resume algorithm in `SKILL.md`'s `## 4. Resume And Hydration`.

## Worktree gate summary

- BUILD's worktree merge-safety guard uses 6 `pending_gate` values (canonical logic, formerly `SKILL.md` step 4, lives in
  `scripts/craftflow_worktree_finalize.py`; the outcome table above is the router contract; this is a
  summary, not a duplicate of the logic):
  - `worktree_merge_conflict` — a real textual git merge conflict. Ask the user to resolve it in
    the main tree, then resume.
  - `worktree_merge_locked` — another BUILD workflow currently holds
    `.claude/worktrees/.merge.lock`, OR the lock's metadata could not be read due to a real
    filesystem/permission error, OR the local `git worktree list` check itself failed (distinct
    conditions — see the holder in the finalize `message`). Wait for the other workflow to finish, fix the underlying
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
