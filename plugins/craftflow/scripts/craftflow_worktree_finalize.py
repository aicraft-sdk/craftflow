#!/usr/bin/env python3
"""BUILD worktree finalize: a port of SKILL.md "### Worktree Isolation (BUILD Default)" step 4a-4e (ADR-0073 BC-7, P7).

    craftflow_worktree_finalize.py --wf ID --project-root DIR [--policy-file F]

Order (the prose order, unchanged): load the artifact (flock) -> policy -> auto_created? -> worktree_path / worktree_branch guards
-> MERGE_ROOT = great-grandparent of worktree_path -> merge lock (mkdir, craftflow_worktree_lock_staleness.py, byte-equal TOCTOU
re-read before a reclaim, a reclaim costs no attempt) -> clean-tree check -> `git merge` -> "Already up to date" falls back to
craftflow_worktree_copy_fallback.py -> `git worktree remove --force` -> `git branch -d` -> mode merged_and_removed, lock released.

Prints exactly one sorted-key JSON line {outcome, pending_gate, message, worktree_mode, details} and exits 0 (2 for a usage error,
3 for an unreadable artifact, 4 when the outcome is real but the artifact write failed: the router must not read exit 4 as success
and must repair the artifact before it resumes). `outcome` is one of 12 values; `pending_gate` is set exactly for the 7 gate outcomes
and is written to the artifact under `<artifact>.lock`, BEFORE the merge lock is released. A gate outcome touches neither the
worktree nor the branch; it releases the lock it held (at most once per run, and only while the lock is still this workflow's) and
never undoes what the merge attempt itself did in the main tree (a conflict stays for the user; copy-fallback may be a partial
apply). `details.lock_release_failed` (plus a message warning) means the lock directory is still there and must be deleted by hand.
The `worktree_finalize` event goes to `{wf}.events.jsonl` under flock. An unexpected exception releases a held lock and
propagates (non-zero exit, no JSON line); the router reads that as `worktree_merge_unrecognized_failure`.
A non-empty worktree_path that is not `<root>/.claude/worktrees/<name>` under --project-root is a gate (worktree_merge_unrecognized_failure), not a skip.
A resume that finds the branch and the worktree both gone records merged_and_removed (`details.recovered`) without touching the main tree.
Python 3.9 compatible; stdlib plus craftflow_policy. It reads no router prose.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from craftflow_policy import load_policy, policy_value

HERE = Path(__file__).resolve().parent
STALENESS = HERE / "craftflow_worktree_lock_staleness.py"
COPY_FALLBACK = HERE / "craftflow_worktree_copy_fallback.py"
# The 6 legacy worktree gates plus `policy_invalid`: the only pending_gate values finalize writes, and the only ones success clears.
GATES = ("worktree_merge_locked", "worktree_dirty_main_tree", "worktree_copy_fallback_failed", "worktree_merge_conflict",
         "worktree_merge_unrecognized_failure", "worktree_cleanup_failed", "policy_invalid")
RECLAIM = ("STALE_WORKTREE_GONE", "STALE_INACTIVE", "SELF_RECLAIM")
LOCAL_ERRORS = ("LOCK_READ_ERROR", "LOCK_WRITE_ERROR", "GIT_WORKTREE_LIST_ERROR")
WF_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
GIT_TIMEOUT = 600
C_LOCALE = {"LC_ALL": "C", "LANGUAGE": "C"}  # "Already up to date" and "CONFLICT" are matched as English text
DASH = "—"
MESSAGES = {
    "merged_and_removed": "Worktree merged and removed.",
    "skipped_not_auto_created": "Worktree finalize skipped: worktree_mode is not auto_created (nothing to merge).",
    "worktree_path_missing": "Worktree finalize skipped: worktree_path is missing or empty in the workflow artifact, so nothing was merged or removed.",
    "worktree_path_invalid": ("Worktree finalize refused: worktree_path (`{worktree_path}`) is not `<project root>/.claude/worktrees/<name>` (absolute, no `..`, "
                              "under this project's root), so it was not trusted. Nothing was merged or removed, and any work committed in that worktree "
                              "is still where it was. Repair worktree_path in the workflow artifact (or merge and remove the worktree by hand), then resume "
                              "this workflow."),
    "worktree_branch_missing": "Worktree finalize skipped: worktree_branch is missing or invalid in the workflow artifact, so nothing was merged or removed.",
    "artifact_unreadable": "The workflow artifact is missing or unreadable ({error}), so worktree finalize touched nothing. Repair or restore it, then resume this workflow.",
    "policy_invalid": "config/policy.json is invalid, so worktree finalize touched nothing. Fix the policy file (first error: {error}), then resume this workflow to retry.",
    "merged_recovered": ("Worktree merged and removed (recovered: the branch and the worktree were already gone, so an earlier run is taken to have "
                         "finished the merge and cleanup but not recorded it; this run only recorded it)."),
    "locked": ("Another BUILD workflow ({holder}) currently holds the merge lock. Wait for it to finish, then resume this workflow to retry. "
               "If that workflow is actually dead (not just slow), you can manually delete `.claude/worktrees/.merge.lock` and resume "
               + DASH + " only after confirming it isn't still running."),
    "locked_local_error": ("Failed to evaluate the merge lock due to a local error (`{holder}`) " + DASH + " this is NOT evidence of another live workflow. "
                           "For `LOCK_READ_ERROR` or `LOCK_WRITE_ERROR`, check filesystem permissions on `.claude/worktrees/.merge.lock`; for `GIT_WORKTREE_LIST_ERROR`, "
                           "confirm `git` is on `PATH` and runnable. Then resume this workflow to retry."),
    "worktree_dirty_main_tree": ("The main tree has uncommitted changes this BUILD did not make (likely a concurrent DEBUG/PLAN/REVIEW session, or an "
                                 "unrelated manual edit). Commit, stash, or otherwise resolve those changes, then resume this workflow to retry the merge."),
    "worktree_copy_fallback_failed": ("The copy-fallback script failed while applying the worktree's uncommitted changes to the main tree: "
                                      "`{copy_fallback_output}`. This may be a partial apply " + DASH + " earlier files in this run may have already landed. "
                                      "Run `git status --porcelain` in the main tree to see exactly what applied before resuming. Resolve the underlying "
                                      "issue (e.g. a filesystem/permission problem, or a genuine conflict/rename edge case the script refused to guess on), "
                                      "then resume this workflow to retry."),
    "main_status_failed": ("`git status --porcelain` failed in the main tree (`{dirty_status}`), so it cannot be confirmed clean. Nothing has been merged. "
                           "Fix git or repository access in `$PROJECT_ROOT`, then resume this workflow to retry the merge."),
    "worktree_merge_conflict": ("`git merge {worktree_branch}` stopped with merge conflicts. Resolve the conflicts in `$PROJECT_ROOT` (the worktree and "
                                "branch have been kept), then resume this workflow to retry."),
    "worktree_merge_unrecognized_failure": ("`git merge {worktree_branch}` failed with an error this router does not recognize as either 'already up to date' "
                                            "or a merge conflict: `{merge_output}`. Nothing has been merged, and the worktree/branch have not been touched. "
                                            "Inspect the error above, resolve the underlying issue in `$PROJECT_ROOT`, then resume this workflow to retry."),
    "merge_state_unknown": ("`git merge {worktree_branch}` did not finish (`{merge_output}`), so the merge state is UNKNOWN: it may have been interrupted midway. "
                            "Inspect `git status` and `git log` in `$PROJECT_ROOT` (run `git merge --abort` if a merge is in progress), then resume "
                            "this workflow to retry. The worktree and branch have not been touched."),
    "cleanup_dirty": ("The merge succeeded, but the worktree `{worktree_path}` still has uncommitted or untracked changes that were not applied to the main "
                      "tree (`{worktree_status}`). Nothing was removed: the worktree and its branch are untouched. Commit them in the worktree or discard "
                      "them deliberately, then resume this workflow to retry cleanup."),
    "cleanup_status_failed": ("The merge succeeded, but `git status --porcelain` failed in the worktree `{worktree_path}` (`{worktree_status}`), so it cannot be "
                              "confirmed free of uncommitted changes. Nothing was removed. Resolve the underlying issue, then resume this workflow."),
    "cleanup_worktree": ("The merge/copy-fallback succeeded, but `git worktree remove {worktree_path} --force` failed: `{worktree_remove_output}`. "
                         "The worktree and its branch are still present and untouched. Resolve the underlying issue (e.g. a process still holding a "
                         "handle inside the worktree), then resume this workflow to retry cleanup."),
    "cleanup_branch_gone": ("The merge succeeded and no worktree is registered or present at `{worktree_path}` (removed earlier, or it never existed), but "
                            "`git branch -d {worktree_branch}` failed: `{branch_delete_output}`. This can mean the branch is not fully merged " + DASH + " a real "
                            "correctness signal, not a cosmetic failure. Inspect `git log {worktree_branch}` in `$PROJECT_ROOT`, resolve the discrepancy (or delete "
                            "the branch manually with `git branch -D` once you've confirmed nothing is lost), then resume this workflow to retry cleanup."),
    "cleanup_branch": ("The merge/copy-fallback succeeded and the worktree was removed, but `git branch -d {worktree_branch}` failed: "
                       "`{branch_delete_output}`. This can mean the branch is not fully merged " + DASH + " a real correctness signal, not a cosmetic failure. "
                       "Inspect `git log {worktree_branch}` in `$PROJECT_ROOT`, resolve the discrepancy (or delete the branch manually with `git branch -D` "
                       "once you've confirmed nothing is lost), then resume this workflow to retry cleanup."),
}
StalenessRunner = Callable[[str, str, str], str]


class Usage(Exception):
    """A malformed invocation: exit 2."""


class Unreadable(Exception):
    """The artifact cannot be loaded: outcome artifact_unreadable, exit 3."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:
        raise Usage(message)


def clip(text: Any, limit: int = 2000) -> str:
    text = str(text).encode("ascii", "backslashreplace").decode("ascii")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rmtree(path: Path) -> None:
    shutil.rmtree(str(path))


def _sleep(seconds: int) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _git(args: List[str], cwd: Path, env: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    """(exit code, stdout+stderr with trailing newlines stripped, like `$(git ... 2>&1)`). A launch failure or the timeout is exit 127."""
    environ = dict(os.environ)
    environ.update(C_LOCALE)
    environ.update(env or {})
    try:
        proc = subprocess.run(["git", "-C", str(cwd), *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                              errors="replace", env=environ, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout.rstrip("\n")


def default_staleness_runner(meta_path: str, project_root: str, wf: str) -> str:
    """The real staleness script, today's argv. Its stdout is the decision whatever its exit code; a launch failure or the timeout gives ""
    (an unrecognized decision, like any text acquire_lock does not match: wait, never reclaim)."""
    try:
        proc = subprocess.run([sys.executable, str(STALENESS), meta_path, project_root, wf], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.rstrip("\n")


class _ArtifactLock:
    def __init__(self, path: Path) -> None:
        self.path = path.with_name(path.name + ".lock")
        self.handle: Any = None

    def __enter__(self) -> "_ArtifactLock":
        self.handle = open(self.path, "a")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc: Any) -> None:
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


def _parse_artifact(path: Path) -> Dict[str, Any]:
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("artifact over the size limit")
    data = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("artifact is not a JSON object")
    return data


def read_artifact(path: Path) -> Dict[str, Any]:
    try:
        with _ArtifactLock(path):
            return _parse_artifact(path)
    except (OSError, ValueError) as exc:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        raise Unreadable(f"{type(exc).__name__}: {exc}"[:120])


def update_artifact(path: Path, mutate: Callable[[Dict[str, Any]], None]) -> None:
    """Read-modify-write under `<artifact>.lock`, atomic via a same-directory temp file and os.replace."""
    with _ArtifactLock(path):
        data = _parse_artifact(path)
        mutate(data)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(str(tmp), str(path))
        except BaseException:  # never leave the temp file behind; the original artifact is untouched
            try:
                os.unlink(str(tmp))
            except OSError:
                pass
            raise


def log_event(name: str, payload: Dict[str, Any], path: Path) -> None:
    """One line under flock. Creates the file, never the directory. Raises OSError."""
    line = json.dumps({"ts": now(), "event": name, **payload}, ensure_ascii=True, separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class Ctx:
    """One finalize run: where the artifact and events live, what the artifact said, and the details that go in the output."""

    def __init__(self, wf: str, project_root: str) -> None:
        self.wf, self.project_root = wf, project_root
        workflows = Path(project_root) / ".craftflow" / "state" / "workflows"
        self.artifact_path, self.events_path = workflows / f"{wf}.json", workflows / f"{wf}.events.jsonl"
        self.details: Dict[str, Any] = {}
        self.mode: Any = None
        self.vals: Dict[str, str] = {}
        self.released = False  # the merge lock is released at most once per run
        self.lock_created = False  # this run created the lock directory

    def note(self, key: str, value: Any) -> None:
        self.details[key] = clip(value) if isinstance(value, str) else value
        if isinstance(value, str):
            self.vals[key] = clip(value)

    def conclude(self, outcome: str, template: str, *, gate: Optional[str] = None, merged: bool = False, code: int = 0,
                 lock_dir: Optional[Path] = None, clear_stale: bool = False) -> Tuple[int, str]:
        """Persist, then release `lock_dir` (BC-7 item 12: artifact first), log and build the single output line. An artifact write
        failure is exit 4 plus a message warning; the outcome and worktree_mode stay what really happened."""
        mode = "merged_and_removed" if merged else self.mode
        try:
            if gate is not None:
                update_artifact(self.artifact_path, lambda art: art.__setitem__("pending_gate", gate))
            elif merged or clear_stale:
                def done(art: Dict[str, Any]) -> None:
                    if merged:
                        art["worktree_mode"] = "merged_and_removed"
                    if art.get("pending_gate") in GATES:  # only a worktree gate; a foreign pending_gate stays
                        art["pending_gate"] = None
                update_artifact(self.artifact_path, done)
        except (OSError, ValueError) as exc:
            self.details["artifact_write_error"] = clip(f"{type(exc).__name__}: {exc}", 200)
            code = code or 4
        if lock_dir is not None:
            release_lock(self, lock_dir)
        keys = ("holder", "copy_fallback_output", "worktree_branch", "merge_output", "worktree_path", "worktree_remove_output", "branch_delete_output",
                "error", "dirty_status", "worktree_status")
        message = MESSAGES[template].format(**{k: self.vals.get(k, "") for k in keys})
        if "artifact_write_error" in self.details:
            message += (f" WARNING: the workflow artifact could not be updated ({self.details['artifact_write_error']}), so this outcome is NOT recorded: "
                        "repair the artifact (set worktree_mode and pending_gate to match this outcome) before resuming. Exit code 4.")
        if self.details.get("lock_release_failed"):
            message += " WARNING: the merge lock could not be released; delete `.claude/worktrees/.merge.lock` by hand before the next BUILD."
        out = {"outcome": outcome, "pending_gate": gate, "message": message, "worktree_mode": mode, "details": self.details}
        try:
            log_event("worktree_finalize", {"wf": self.wf, "outcome": outcome, "pending_gate": gate, "worktree_mode": mode, "details": self.details},
                      self.events_path)
        except OSError as exc:
            self.details["event_error"] = clip(f"{type(exc).__name__}: {exc}", 120)
        return code, json.dumps(out, sort_keys=True, ensure_ascii=True)

    def legacy_event(self, name: str) -> None:
        try:
            log_event(name, {"wf": self.wf}, self.events_path)
        except OSError as exc:
            self.details["event_error"] = clip(f"{type(exc).__name__}: {exc}", 120)


def worktree_shape(wp: str) -> Optional[Tuple[Path, str]]:
    """(root, normalised path) when `wp` is absolute, free of `..` and, once normalised, exactly `<root>/.claude/worktrees/<name>`; else None."""
    if not os.path.isabs(wp) or ".." in wp.split(os.sep):
        return None
    norm = Path(os.path.normpath(wp))
    if norm.parent.name != "worktrees" or norm.parent.parent.name != ".claude" or not norm.name:
        return None
    return norm.parent.parent.parent, str(norm)


def holder_of(decision: str) -> Tuple[str, bool]:
    """(holder, is_local_error) from the final staleness decision (SKILL.md 4c). An unrecognized decision is holder "unknown"."""
    word, _, rest = decision.partition(" ")
    rest = rest.strip()
    if word in LOCAL_ERRORS:
        return clip(rest, 200) or "unknown", True
    if word == "CONTENDED" and rest:
        return rest.split()[0], False
    return "unknown", False


def acquire_lock(ctx: Ctx, lock_dir: Path, wp: str, merge_root: Path, attempts: int, sleep_s: int, runner: StalenessRunner) -> Tuple[bool, str]:
    """The 4b loop. (acquired, final decision). mkdir is the atomic lock; a reclaim does not consume an attempt."""
    meta = lock_dir / "metadata.json"
    decision, attempt = "", 0
    while attempt < attempts:
        try:
            os.mkdir(str(lock_dir))
        except OSError:  # held (FileExistsError) or not creatable: the staleness decision below tells which; fail closed either way
            pass
        else:
            ctx.lock_created = True
            try:
                _write_meta(meta, json.dumps({"workflow_uuid": ctx.wf, "worktree_path": wp, "acquired_at": now()}, separators=(",", ":")))
            except OSError as exc:  # a lock nobody can identify can never be reclaimed: drop it and report a local error
                _drop_lock(ctx, lock_dir)
                ctx.released = True
                return False, f"LOCK_WRITE_ERROR {type(exc).__name__}"
            return True, decision
        before = read_metadata(meta)
        decision = runner(str(meta), str(merge_root), ctx.wf)
        if decision.startswith(RECLAIM):
            after = read_metadata(meta)  # TOCTOU: delete only the very occupant the decision was about
            if after and before == after:
                try:
                    _rmtree(lock_dir)
                except OSError:
                    pass
                if not lock_dir.exists():
                    continue
        attempt += 1
        _sleep(sleep_s)
    return False, decision


def read_metadata(meta: Path) -> str:
    try:
        return meta.read_text(encoding="utf-8", errors="replace").rstrip("\n")  # `$(cat ...)` strips trailing newlines
    except OSError:
        return ""


def _write_meta(meta: Path, text: str) -> None:
    meta.write_text(text)


def _drop_lock(ctx: Ctx, lock_dir: Path) -> None:
    try:
        _rmtree(lock_dir)
    except FileNotFoundError:
        pass
    except OSError as exc:  # the next workflow sees a stale lock; this run must say so loudly (message warning + details)
        ctx.note("release_lock_error", f"{type(exc).__name__}: {exc}")
        ctx.details["lock_release_failed"] = True


def release_lock(ctx: Ctx, lock_dir: Path) -> None:
    """At most once per run, and only while the lock is still ours: a lock now held by another workflow is left alone."""
    if ctx.released:
        return
    ctx.released = True
    try:
        owner = json.loads(read_metadata(lock_dir / "metadata.json")).get("workflow_uuid")
    except (ValueError, AttributeError):
        owner = None
    if owner is not None and owner != ctx.wf:
        ctx.note("release_lock_skipped", f"the lock is now held by {clip(owner, 80)}")
        return
    if owner is None and not ctx.lock_created:
        return
    _drop_lock(ctx, lock_dir)


def finalize(ctx: Ctx, policy_file: Optional[str], runner: StalenessRunner) -> Tuple[int, str]:
    try:
        art = read_artifact(ctx.artifact_path)
    except Unreadable as exc:
        ctx.note("error", str(exc))
        return ctx.conclude("artifact_unreadable", "artifact_unreadable", code=3)
    ctx.mode = art.get("worktree_mode")
    if ctx.mode != "auto_created":  # the prose's first guard: the policy is read only where a step uses it (after the 4a guards)
        stale = ctx.mode == "merged_and_removed" and art.get("pending_gate") in GATES  # a gate a failed earlier write left behind; only this combination
        return ctx.conclude("skipped_not_auto_created", "skipped_not_auto_created", clear_stale=stale)
    wp, branch = art.get("worktree_path"), art.get("worktree_branch")
    if not isinstance(wp, str) or not wp:  # whitespace-only is a non-empty path we cannot trust: the shape gate below, like any other bad path
        ctx.legacy_event("worktree_path_missing")
        return ctx.conclude("worktree_path_missing", "worktree_path_missing")
    if not isinstance(branch, str) or not branch.strip() or branch.startswith("-"):  # a dash branch would be read by git as an option
        ctx.legacy_event("worktree_branch_missing")
        return ctx.conclude("worktree_branch_missing", "worktree_branch_missing")
    ctx.note("worktree_path", wp)
    ctx.note("worktree_branch", branch)
    shaped = worktree_shape(wp)
    merge_root = shaped[0] if shaped else Path(wp).parent.parent.parent  # never `git rev-parse --show-toplevel`: worktree_path is <root>/.claude/worktrees/<dir>
    ctx.note("merge_root", str(merge_root))
    if shaped is None or os.path.realpath(str(merge_root)) != os.path.realpath(ctx.project_root):  # fail closed: a path git would resolve elsewhere
        # a non-empty path we cannot trust may hide committed work in a worktree: a gate (the 12-outcome enum has no new value), never a silent skip
        ctx.details["root_mismatch"] = {"merge_root": str(merge_root), "project_root": ctx.project_root}
        return ctx.conclude("worktree_merge_unrecognized_failure", "worktree_path_invalid", gate="worktree_merge_unrecognized_failure")  # no lock exists yet
    wp = shaped[1]  # the normalised path is the one every later command uses
    policy, status, errors = load_policy(Path(policy_file) if policy_file else None, log=False)
    if status == "invalid":
        ctx.note("policy_status", status)
        ctx.details["policy_errors"] = errors[:5]
        ctx.note("error", errors[0] if errors else "invalid")
        return ctx.conclude("policy_invalid", "policy_invalid", gate="policy_invalid")
    require_clean = policy_value(policy, "worktree_merge.require_clean_tree")
    attempts = policy_value(policy, "worktree_merge.lock_max_attempts")
    sleep_s = policy_value(policy, "worktree_merge.lock_sleep_seconds")
    copy_fallback = policy_value(policy, "worktree_merge.copy_fallback_on_up_to_date")
    lock_dir = merge_root / ".claude" / "worktrees" / ".merge.lock"
    acquired, decision = acquire_lock(ctx, lock_dir, wp, merge_root, attempts, sleep_s, runner)
    if not acquired:
        holder, local = holder_of(decision)
        ctx.note("holder", holder)
        ctx.note("decision", decision)
        return ctx.conclude("worktree_merge_locked", "locked_local_error" if local else "locked", gate="worktree_merge_locked")
    try:
        return merge_and_clean(ctx, lock_dir, merge_root, wp, branch, require_clean, copy_fallback)
    except BaseException:
        release_lock(ctx, lock_dir)  # never leave the lock held behind a crash (a no-op when this run already released it)
        raise


def gate(ctx: Ctx, lock_dir: Path, outcome: str, template: Optional[str] = None) -> Tuple[int, str]:
    return ctx.conclude(outcome, template or outcome, gate=outcome, lock_dir=lock_dir)  # persists the gate, then releases


def worktree_gone(wp: str, merge_root: Path) -> bool:
    """True only when the directory is absent AND git no longer lists it; any doubt (a git failure) is False."""
    if os.path.lexists(wp):
        return False
    rc, listing = _git(["worktree", "list", "--porcelain"], merge_root)
    if rc != 0:
        return False
    listed = {os.path.realpath(line[len("worktree "):]) for line in listing.splitlines() if line.startswith("worktree ")}
    return os.path.realpath(wp) not in listed


def merge_and_clean(ctx: Ctx, lock_dir: Path, merge_root: Path, wp: str, branch: str, require_clean: bool, copy_fallback: bool) -> Tuple[int, str]:
    gone = worktree_gone(wp, merge_root)
    on_disk = os.path.lexists(wp)  # False for a gone worktree and for a prunable one (directory deleted by hand, git still lists it): nothing on disk to lose
    if gone and _git(["rev-parse", "--verify", "-q", f"refs/heads/{branch}"], merge_root)[0] == 1:
        # resume after an exit-4 artifact write (or a lost write): nothing is left to merge or clean, so record it; nothing in the main tree is touched
        ctx.details["recovered"] = True
        return ctx.conclude("merged_and_removed", "merged_recovered", merged=True, lock_dir=lock_dir)
    if require_clean is not False:  # 4d: the schema pins this to true; anything else keeps the check
        rc, dirty = _git(["status", "--porcelain"], merge_root)  # advisory: honours status.showUntrackedFiles like the prose; the worktree check below does not
        if rc != 0 or dirty:
            ctx.note("dirty_status", dirty)
            return gate(ctx, lock_dir, "worktree_dirty_main_tree", "main_status_failed" if rc != 0 else None)
    rc, merge_output = _git(["merge", branch], merge_root, {"GIT_MERGE_AUTOEDIT": "no"})  # 4e
    applied = False  # True once copy-fallback has carried the worktree's uncommitted edits into the main tree
    if rc == 0 and "Already up to date" in merge_output:
        if copy_fallback and on_disk:
            try:
                proc = subprocess.run([sys.executable, str(COPY_FALLBACK), wp, str(merge_root)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      universal_newlines=True, errors="replace", timeout=GIT_TIMEOUT)
                fb_rc, fb_out = proc.returncode, proc.stdout.rstrip("\n")
            except (OSError, subprocess.SubprocessError) as exc:
                fb_rc, fb_out = 127, f"{type(exc).__name__}: {exc}"
            if fb_rc != 0:  # partial apply is possible: the worktree and branch stay as the source of truth
                ctx.note("copy_fallback_output", fb_out)
                return gate(ctx, lock_dir, "worktree_copy_fallback_failed")
            applied = True
    elif rc != 0:
        ctx.note("merge_output", merge_output)
        if "CONFLICT" in merge_output:  # the conflict state is left in the main tree for the user to resolve
            return gate(ctx, lock_dir, "worktree_merge_conflict")
        return gate(ctx, lock_dir, "worktree_merge_unrecognized_failure", "merge_state_unknown" if rc == 127 or rc < 0 else None)  # 127: launch failure or timeout; < 0: killed by a signal, it may have applied
    if gone:  # resume after `git worktree remove` already ran: only the branch delete is left
        rc, out = _git(["branch", "-d", branch], merge_root)
        if rc != 0:
            ctx.note("branch_delete_output", out)
            ctx.note("failing_command", "git branch -d")
            return gate(ctx, lock_dir, "worktree_cleanup_failed", "cleanup_branch_gone")
        return ctx.conclude("merged_and_removed", "merged_and_removed", merged=True, lock_dir=lock_dir)
    if on_disk and not applied:  # deliberate deviation from the prose (which discards): `--force` below must never throw away uncommitted worktree edits
        # --untracked-files=all and --ignore-submodules=none: a user's status.showUntrackedFiles=no / ignore config must not hide work from `remove --force`
        rc, status_out = _git(["status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none"], Path(wp))
        if rc != 0 or status_out:
            ctx.note("worktree_status", status_out)
            ctx.note("failing_command", "git status (worktree)")
            return gate(ctx, lock_dir, "worktree_cleanup_failed", "cleanup_status_failed" if rc != 0 else "cleanup_dirty")
    rc, out = _git(["worktree", "remove", "--force", "--", wp], merge_root)
    if rc != 0:
        ctx.note("worktree_remove_output", out)
        ctx.note("failing_command", "git worktree remove")
        return gate(ctx, lock_dir, "worktree_cleanup_failed", "cleanup_worktree")
    rc, out = _git(["branch", "-d", branch], merge_root)
    if rc != 0:
        ctx.note("branch_delete_output", out)
        ctx.note("failing_command", "git branch -d")
        return gate(ctx, lock_dir, "worktree_cleanup_failed", "cleanup_branch")
    return ctx.conclude("merged_and_removed", "merged_and_removed", merged=True, lock_dir=lock_dir)  # artifact first, then the lock


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--wf", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--policy-file")
    args = parser.parse_args(argv)
    if not WF_ID.fullmatch(args.wf):
        raise Usage("--wf must be a workflow id (letters, digits, '.', '_', '-')")
    return args


def run(argv: List[str], *, staleness_runner: Optional[StalenessRunner] = None) -> Tuple[int, str]:
    """(exit code, the single stdout line). `staleness_runner(meta_path, project_root, wf) -> decision` is injectable for tests."""
    try:
        args = parse_args(argv)
    except Usage as exc:
        return 2, json.dumps({"error": str(exc)}, sort_keys=True)
    return finalize(Ctx(args.wf, args.project_root), args.policy_file, staleness_runner or default_staleness_runner)


def main(argv: Optional[List[str]] = None) -> int:
    code, line = run(sys.argv[1:] if argv is None else argv)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
