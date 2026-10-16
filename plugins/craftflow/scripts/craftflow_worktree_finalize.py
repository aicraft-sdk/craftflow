#!/usr/bin/env python3
"""BUILD worktree finalize: a port of SKILL.md "### Worktree Isolation (BUILD Default)" step 4a-4e (ADR-0073 BC-7, P7).

    craftflow_worktree_finalize.py --wf ID --project-root DIR [--policy-file F]

Order (the prose order, unchanged): load the artifact (flock) -> policy -> auto_created? -> worktree_path / worktree_branch guards
-> MERGE_ROOT = great-grandparent of worktree_path -> merge lock (mkdir, craftflow_worktree_lock_staleness.py, byte-equal TOCTOU
re-read before a reclaim, a reclaim costs no attempt) -> clean-tree check -> `git merge` -> "Already up to date" falls back to
craftflow_worktree_copy_fallback.py -> `git worktree remove --force` -> `git branch -d` -> mode merged_and_removed, lock released.

Prints exactly one sorted-key JSON line {outcome, pending_gate, message, worktree_mode, details} and exits 0 (3 for an unreadable
artifact, 2 for a usage error). `outcome` is one of 12 values; `pending_gate` is set exactly for the 7 gate outcomes and is written
to the artifact under `<artifact>.lock`. A gate outcome touches neither the worktree nor the branch; it releases the lock it held
and never undoes what the merge attempt itself did in the main tree (a conflict stays for the user; copy-fallback may be a partial
apply). The `worktree_finalize` event goes to `{wf}.events.jsonl` under flock. An unexpected exception releases a held lock and
propagates (non-zero exit, no JSON line); the router reads that as `worktree_merge_unrecognized_failure`.
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
LOCAL_ERRORS = ("LOCK_READ_ERROR", "GIT_WORKTREE_LIST_ERROR")
WF_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
GIT_TIMEOUT = 600
DASH = "—"
MESSAGES = {
    "merged_and_removed": "Worktree merged and removed.",
    "skipped_not_auto_created": "Worktree finalize skipped: worktree_mode is not auto_created (nothing to merge).",
    "worktree_path_missing": "Worktree finalize skipped: worktree_path is missing or empty in the workflow artifact, so nothing was merged or removed.",
    "worktree_branch_missing": "Worktree finalize skipped: worktree_branch is missing or invalid in the workflow artifact, so nothing was merged or removed.",
    "artifact_unreadable": "The workflow artifact is missing or unreadable ({error}), so worktree finalize touched nothing. Repair or restore it, then resume this workflow.",
    "policy_invalid": "config/policy.json is invalid, so worktree finalize touched nothing. Fix the policy file (first error: {error}), then resume this workflow to retry.",
    "locked": ("Another BUILD workflow ({holder}) currently holds the merge lock. Wait for it to finish, then resume this workflow to retry. "
               "If that workflow is actually dead (not just slow), you can manually delete `.claude/worktrees/.merge.lock` and resume "
               + DASH + " only after confirming it isn't still running."),
    "locked_local_error": ("Failed to evaluate the merge lock due to a local error (`{holder}`) " + DASH + " this is NOT evidence of another live workflow. "
                           "For `LOCK_READ_ERROR`, check filesystem permissions on `.claude/worktrees/.merge.lock`; for `GIT_WORKTREE_LIST_ERROR`, "
                           "confirm `git` is on `PATH` and runnable. Then resume this workflow to retry."),
    "worktree_dirty_main_tree": ("The main tree has uncommitted changes this BUILD did not make (likely a concurrent DEBUG/PLAN/REVIEW session, or an "
                                 "unrelated manual edit). Commit, stash, or otherwise resolve those changes, then resume this workflow to retry the merge."),
    "worktree_copy_fallback_failed": ("The copy-fallback script failed while applying the worktree's uncommitted changes to the main tree: "
                                      "`{copy_fallback_output}`. This may be a partial apply " + DASH + " earlier files in this run may have already landed. "
                                      "Run `git status --porcelain` in the main tree to see exactly what applied before resuming. Resolve the underlying "
                                      "issue (e.g. a filesystem/permission problem, or a genuine conflict/rename edge case the script refused to guess on), "
                                      "then resume this workflow to retry."),
    "worktree_merge_conflict": ("`git merge {worktree_branch}` stopped with merge conflicts. Resolve the conflicts in `$PROJECT_ROOT` (the worktree and "
                                "branch have been kept), then resume this workflow to retry."),
    "worktree_merge_unrecognized_failure": ("`git merge {worktree_branch}` failed with an error this router does not recognize as either 'already up to date' "
                                            "or a merge conflict: `{merge_output}`. Nothing has been merged, and the worktree/branch have not been touched. "
                                            "Inspect the error above, resolve the underlying issue in `$PROJECT_ROOT`, then resume this workflow to retry."),
    "cleanup_worktree": ("The merge/copy-fallback succeeded, but `git worktree remove {worktree_path} --force` failed: `{worktree_remove_output}`. "
                         "The worktree and its branch are still present and untouched. Resolve the underlying issue (e.g. a process still holding a "
                         "handle inside the worktree), then resume this workflow to retry cleanup."),
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
    """(exit code, stdout+stderr with trailing newlines stripped, like `$(git ... 2>&1)`). A launch failure is exit 127."""
    environ = dict(os.environ)
    environ.update(env or {})
    try:
        proc = subprocess.run(["git", "-C", str(cwd), *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                              errors="replace", env=environ, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout.rstrip("\n")


def default_staleness_runner(meta_path: str, project_root: str, wf: str) -> str:
    """The real staleness script, today's argv. Anything but a clean run gives "" (an unrecognized decision: wait, never reclaim)."""
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
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))


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

    def note(self, key: str, value: Any) -> None:
        self.details[key] = clip(value) if isinstance(value, str) else value
        if isinstance(value, str):
            self.vals[key] = clip(value)

    def conclude(self, outcome: str, template: str, *, gate: Optional[str] = None, merged: bool = False, code: int = 0) -> Tuple[int, str]:
        """Persist, log and build the single output line."""
        mode = "merged_and_removed" if merged else self.mode
        try:
            if gate is not None:
                update_artifact(self.artifact_path, lambda art: art.__setitem__("pending_gate", gate))
            elif merged:
                def done(art: Dict[str, Any]) -> None:
                    art["worktree_mode"] = "merged_and_removed"
                    if art.get("pending_gate") in GATES:  # only a worktree gate; a foreign pending_gate stays
                        art["pending_gate"] = None
                update_artifact(self.artifact_path, done)
        except (OSError, ValueError) as exc:
            self.details["artifact_write_error"] = clip(f"{type(exc).__name__}: {exc}", 200)
        message = MESSAGES[template].format(**{k: self.vals.get(k, "") for k in ("holder", "copy_fallback_output", "worktree_branch", "merge_output",
                                                                               "worktree_path", "worktree_remove_output", "branch_delete_output", "error")})
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
            try:
                meta.write_text(json.dumps({"workflow_uuid": ctx.wf, "worktree_path": wp, "acquired_at": now()}, separators=(",", ":")))
            except OSError as exc:
                ctx.note("lock_metadata_error", f"{type(exc).__name__}: {exc}")
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


def release_lock(ctx: Ctx, lock_dir: Path) -> None:
    try:
        _rmtree(lock_dir)
    except FileNotFoundError:
        pass
    except OSError as exc:  # self-detecting through the next workflow's contention path: not fatal
        ctx.note("release_lock_error", f"{type(exc).__name__}: {exc}")


def finalize(ctx: Ctx, policy_file: Optional[str], runner: StalenessRunner) -> Tuple[int, str]:
    try:
        art = read_artifact(ctx.artifact_path)
    except Unreadable as exc:
        ctx.note("error", str(exc))
        return ctx.conclude("artifact_unreadable", "artifact_unreadable", code=3)
    ctx.mode = art.get("worktree_mode")
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
    if ctx.mode != "auto_created":
        return ctx.conclude("skipped_not_auto_created", "skipped_not_auto_created")
    wp, branch = art.get("worktree_path"), art.get("worktree_branch")
    if not isinstance(wp, str) or not wp.strip():
        ctx.legacy_event("worktree_path_missing")
        return ctx.conclude("worktree_path_missing", "worktree_path_missing")
    if not isinstance(branch, str) or not branch.strip() or branch.startswith("-"):  # a dash branch would be read by git as an option
        ctx.legacy_event("worktree_branch_missing")
        return ctx.conclude("worktree_branch_missing", "worktree_branch_missing")
    merge_root = Path(wp).parent.parent.parent  # never `git rev-parse --show-toplevel`: worktree_path is <root>/.claude/worktrees/<dir>
    ctx.note("merge_root", str(merge_root))
    ctx.note("worktree_path", wp)
    ctx.note("worktree_branch", branch)
    if os.path.realpath(str(merge_root)) != os.path.realpath(ctx.project_root):
        ctx.details["root_mismatch"] = {"merge_root": str(merge_root), "project_root": ctx.project_root}
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
        release_lock(ctx, lock_dir)  # never leave the lock held behind a crash
        raise


def gate(ctx: Ctx, lock_dir: Path, outcome: str, template: Optional[str] = None) -> Tuple[int, str]:
    release_lock(ctx, lock_dir)
    return ctx.conclude(outcome, template or outcome, gate=outcome)


def merge_and_clean(ctx: Ctx, lock_dir: Path, merge_root: Path, wp: str, branch: str, require_clean: bool, copy_fallback: bool) -> Tuple[int, str]:
    if require_clean is not False:  # 4d: the schema pins this to true; anything else keeps the check
        rc, dirty = _git(["status", "--porcelain"], merge_root)
        if rc != 0 or dirty:
            ctx.note("dirty_status", dirty)
            return gate(ctx, lock_dir, "worktree_dirty_main_tree")
    rc, merge_output = _git(["merge", branch], merge_root, {"GIT_MERGE_AUTOEDIT": "no"})  # 4e
    if rc == 0 and "Already up to date" in merge_output:
        if copy_fallback:
            try:
                proc = subprocess.run([sys.executable, str(COPY_FALLBACK), wp, str(merge_root)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      universal_newlines=True, errors="replace", timeout=GIT_TIMEOUT)
                fb_rc, fb_out = proc.returncode, proc.stdout.rstrip("\n")
            except (OSError, subprocess.SubprocessError) as exc:
                fb_rc, fb_out = 127, f"{type(exc).__name__}: {exc}"
            if fb_rc != 0:  # partial apply is possible: the worktree and branch stay as the source of truth
                ctx.note("copy_fallback_output", fb_out)
                return gate(ctx, lock_dir, "worktree_copy_fallback_failed")
    elif rc != 0:
        ctx.note("merge_output", merge_output)
        if "CONFLICT" in merge_output:  # the conflict state is left in the main tree for the user to resolve
            return gate(ctx, lock_dir, "worktree_merge_conflict")
        return gate(ctx, lock_dir, "worktree_merge_unrecognized_failure")
    rc, out = _git(["worktree", "remove", wp, "--force"], merge_root)
    if rc != 0:
        ctx.note("worktree_remove_output", out)
        ctx.note("failing_command", "git worktree remove")
        return gate(ctx, lock_dir, "worktree_cleanup_failed", "cleanup_worktree")
    rc, out = _git(["branch", "-d", branch], merge_root)
    if rc != 0:
        ctx.note("branch_delete_output", out)
        ctx.note("failing_command", "git branch -d")
        return gate(ctx, lock_dir, "worktree_cleanup_failed", "cleanup_branch")
    release_lock(ctx, lock_dir)
    return ctx.conclude("merged_and_removed", "merged_and_removed", merged=True)


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
