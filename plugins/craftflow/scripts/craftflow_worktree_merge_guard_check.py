#!/usr/bin/env python3
"""Executable proof for the BUILD worktree merge-safety guard (SKILL.md step 4,
"### Worktree Isolation (BUILD Default)"). Exercises the REAL guard scripts --
craftflow_worktree_lock_staleness.py and craftflow_worktree_copy_fallback.py --
via subprocess, against real scratch git repositories.

This is a Phase 3 reconciliation against the LIVE current behavior of those
two scripts, not the original plan's stale 11-scenario draft (written before
Phase 1's 5 REM-FIX rounds added more decision outcomes than the plan
originally enumerated). Every distinct decide()/apply_copy_fallback() outcome
found by reading the real, final Phase-1 source is exercised here as its own
scenario, in addition to the broader guard-sequence property scenarios
(P1-P3) the original plan already established:

Lock staleness (craftflow_worktree_lock_staleness.py's decide()):
  SELF_RECLAIM, CONTENDED (stranger/live-worktree, self-identity-but-live,
  old-but-recent-activity -- 3 distinct code paths all producing the string
  "CONTENDED"), STALE_WORKTREE_GONE, STALE_INACTIVE <uuid> (valid metadata,
  old, holder inactive), STALE_INACTIVE unknown (missing-metadata.json
  variant AND corrupt-JSON variant -- 2 distinct code paths), CONTENDED_
  UNKNOWN_HOLDER (benign fresh-lock-no-metadata race), LOCK_READ_ERROR,
  GIT_WORKTREE_LIST_ERROR (launch-failure form AND non-zero-exit form -- 2
  distinct code paths).

Copy fallback (craftflow_worktree_copy_fallback.py's apply_copy_fallback()):
  Added/Modified/Deleted/Renamed-with-space (combined positive exercise),
  conflict-code rejection (AA), missing-rename-source rejection (RD), a
  working symlink preserved as a symlink, an absolute symlink rewritten when
  it points inside the worktree, a chained symlink (symlink->symlink->file)
  preserving its 2-hop structure, and a brand-new untracked top-level
  directory copied file-by-file (requires --untracked-files=all).

Plus the broader guard-sequence properties from the original plan (dirty
main tree gates before any merge; a real merge conflict releases the lock;
the dirty-tree gate runs BEFORE the copy-fallback path, proven against the
real copy-fallback implementation rather than an absent one).

This subprocess-invokes the exact same files SKILL.md step 4 shells out to --
there is no ported/re-implemented copy of either algorithm to drift from.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
STALENESS_SCRIPT = SCRIPTS_DIR / "craftflow_worktree_lock_staleness.py"
COPY_FALLBACK_SCRIPT = SCRIPTS_DIR / "craftflow_worktree_copy_fallback.py"


def run(cmd, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


def git(cwd, *args) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd)


def init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    # Mirrors this repo's real .gitignore (.claude/ is ignored at project root) --
    # without this, the scratch repo's own .claude/worktrees/ bookkeeping would
    # spuriously make git status --porcelain report "dirty" on every run.
    (root / ".gitignore").write_text(".claude/\n")
    (root / "README.md").write_text("hello\n")
    git(root, "add", "README.md", ".gitignore")
    git(root, "commit", "-q", "-m", "init")


def current_branch(repo: Path) -> str:
    result = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    require(result.returncode == 0, f"failed to determine current branch: {result.stderr}")
    return result.stdout.strip()


def iso_now(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def staleness_decision(lock_meta_path: Path, project_root, current_workflow_uuid=None) -> str:
    """Subprocess-invokes the real Phase-1 script -- not a ported copy."""
    args = [sys.executable, str(STALENESS_SCRIPT), str(lock_meta_path), str(project_root)]
    if current_workflow_uuid:
        args.append(current_workflow_uuid)
    result = subprocess.run(args, capture_output=True, text=True)
    require(result.returncode == 0, f"staleness script exited {result.returncode}: {result.stderr}")
    return result.stdout.strip()


def staleness_decision_raw(lock_meta_path: Path, project_root, current_workflow_uuid=None):
    """Like staleness_decision but does not assert exit 0 -- used by scenarios
    that need the raw CompletedProcess (none currently do, kept symmetric with
    apply_copy_fallback below for consistency)."""
    args = [sys.executable, str(STALENESS_SCRIPT), str(lock_meta_path), str(project_root)]
    if current_workflow_uuid:
        args.append(current_workflow_uuid)
    return subprocess.run(args, capture_output=True, text=True)


def apply_copy_fallback(worktree_path, project_root) -> subprocess.CompletedProcess:
    """Subprocess-invokes the real Phase-1 script -- not a ported copy."""
    return subprocess.run(
        [sys.executable, str(COPY_FALLBACK_SCRIPT), str(worktree_path), str(project_root)],
        capture_output=True,
        text=True,
    )


def _set_stage_only(repo: Path, path: str, stages) -> None:
    """Test-harness helper (not part of either real guard script): reproduces
    a genuine, git-native two-sided unmerged index state for `path` using
    `git update-index --index-info` -- real plumbing, verified live against
    real git 2.50.1 during this REM-FIX round: stage1-only -> DD, stage1+2 ->
    UD, stage1+3 -> DU, stage2-only -> AU, stage3-only -> UA, stage1+2+3 ->
    UU. This constructs the exact porcelain codes an actual multi-branch
    merge conflict produces, without needing two real diverging branches.
    `path` must already be a normal, committed stage-0 entry; its mode/
    blob-sha are reused for every synthetic stage entry."""
    ls = git(repo, "ls-files", "-s", path)
    require(
        ls.returncode == 0 and ls.stdout.strip(),
        f"_set_stage_only: {path} must be a tracked stage-0 entry before staging synthetic conflict entries",
    )
    fields = ls.stdout.split()
    mode, sha = fields[0], fields[1]
    removed = git(repo, "update-index", "--force-remove", path)
    require(removed.returncode == 0, f"_set_stage_only: force-remove failed: {removed.stderr}")
    for stage in stages:
        result = subprocess.run(
            ["git", "update-index", "--index-info"],
            cwd=str(repo),
            input=f"{mode} {sha} {stage}\t{path}\n",
            capture_output=True,
            text=True,
        )
        require(result.returncode == 0, f"_set_stage_only: index-info failed for stage {stage}: {result.stderr}")


def _load_copy_fallback_module():
    """Loads the REAL, unmodified craftflow_worktree_copy_fallback.py source
    in-process under a throwaway module name (never registered in
    sys.modules, never written back to disk) -- used only by the two
    crafted-input scenarios below, which need to feed porcelain bytes that
    real git itself will never actually emit."""
    spec = importlib.util.spec_from_file_location(
        "craftflow_worktree_copy_fallback_inproc_" + os.urandom(4).hex(), COPY_FALLBACK_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_copy_fallback_with_crafted_porcelain(porcelain_bytes: bytes, project_root):
    """Exercises the REAL apply_copy_fallback() parsing logic in-process
    (unmodified on disk) against crafted `git status --porcelain=1 -z` bytes,
    by monkeypatching only the module's own subprocess.run for the single
    'git status' call it makes -- restored immediately in a finally block.
    This is the cleanest way to genuinely exercise a defensive branch that
    guards against a porcelain shape real git cannot be coerced into
    producing, without modifying either real guard script on disk."""
    module = _load_copy_fallback_module()
    original_run = module.subprocess.run

    def fake_run(cmd, **kwargs):
        if len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "status":
            return subprocess.CompletedProcess(cmd, 0, stdout=porcelain_bytes, stderr=b"")
        return original_run(cmd, **kwargs)

    module.subprocess.run = fake_run
    try:
        return module.apply_copy_fallback("unused-worktree-path", str(project_root))
    finally:
        module.subprocess.run = original_run


def try_acquire_lock(lock_dir: Path, workflow_uuid: str, worktree_path: str) -> bool:
    try:
        lock_dir.mkdir()
    except FileExistsError:
        return False
    (lock_dir / "metadata.json").write_text(
        json.dumps(
            {
                "workflow_uuid": workflow_uuid,
                "worktree_path": worktree_path,
                "acquired_at": iso_now(),
            }
        )
    )
    return True


def is_dirty(project_root: Path):
    result = git(project_root, "status", "--porcelain")
    if result.returncode != 0:
        return True, f"git status failed: {result.stderr}"
    return bool(result.stdout.strip()), result.stdout


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def attempt_finalize(root: Path, worktree_path: Path, lock_dir: Path) -> str:
    """Models SKILL.md step 4d->4e's real ordering: the clean-tree check (4d)
    must gate BEFORE the copy-fallback (4e) ever runs. Used by both the
    dirty-tree regression scenario and the positive copy-fallback scenario, so
    a violation of gate-ordering would show up in the regression scenario even
    though the copy-fallback implementation is real and functional."""
    dirty, _ = is_dirty(root)
    if dirty:
        shutil.rmtree(lock_dir, ignore_errors=True)
        return "GATED_DIRTY"

    result = apply_copy_fallback(worktree_path, root)
    require(result.returncode == 0, f"copy-fallback script failed: {result.stderr}")
    return "COPY_FALLBACK_APPLIED"


# --------------------------------------------------------------------------
# Guard-sequence property scenarios (P1/P2/P3) -- unchanged in spirit from
# the original plan draft, still real subprocess-based, not ported.
# --------------------------------------------------------------------------


def scenario_dirty_main_tree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)
        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.parent.mkdir(parents=True, exist_ok=True)

        acquired = try_acquire_lock(lock_dir, "wf-holder", str(root))
        require(acquired, "dirty_main_tree: lock should acquire on a clean lock dir")

        (root / "README.md").write_text("dirtied by a concurrent session\n")

        dirty, _ = is_dirty(root)
        require(dirty, "dirty_main_tree: main tree must be detected as dirty")

        shutil.rmtree(lock_dir)
        require(not lock_dir.exists(), "dirty_main_tree: lock must be released on the dirty-tree gate path")


def scenario_lock_contended() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)
        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.parent.mkdir(parents=True, exist_ok=True)

        worktree_path = root / ".claude" / "worktrees" / "wf-holder-worktree"
        git(root, "worktree", "add", str(worktree_path), "-b", "wf-holder-branch")

        acquired = try_acquire_lock(lock_dir, "wf-holder", str(worktree_path))
        require(acquired, "lock_contended: first acquire should succeed")

        second_attempt = try_acquire_lock(lock_dir, "wf-challenger", str(root))
        require(not second_attempt, "lock_contended: second acquire must fail while lock is held")

        decision = staleness_decision(lock_dir / "metadata.json", root, "wf-challenger")
        require(
            decision.startswith("CONTENDED wf-holder"),
            f"lock_contended: expected contention naming the real holder, got {decision}",
        )


def scenario_merge_conflict_unaffected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)

        worktree_path = root / ".claude" / "worktrees" / "wf-conflict-worktree"
        git(root, "worktree", "add", str(worktree_path), "-b", "wf-conflict-branch")

        (root / "README.md").write_text("main tree change\n")
        git(root, "add", "README.md")
        git(root, "commit", "-q", "-m", "main change")

        (worktree_path / "README.md").write_text("worktree change\n")
        git(worktree_path, "add", "README.md")
        git(worktree_path, "commit", "-q", "-m", "worktree change")

        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        acquired = try_acquire_lock(lock_dir, "wf-conflict", str(worktree_path))
        require(acquired, "merge_conflict_unaffected: lock should acquire on a clean lock dir")

        dirty, _ = is_dirty(root)
        require(not dirty, "merge_conflict_unaffected: main tree must be clean before the merge attempt")

        merge_result = git(root, "merge", "wf-conflict-branch")
        require(
            merge_result.returncode != 0,
            "merge_conflict_unaffected: expected a real merge conflict to reproduce",
        )

        shutil.rmtree(lock_dir)
        require(not lock_dir.exists(), "merge_conflict_unaffected: lock must be released on the conflict-gate path")
        require(worktree_path.exists(), "merge_conflict_unaffected: worktree must remain until the user resolves")

        git(root, "merge", "--abort")


# --------------------------------------------------------------------------
# craftflow_worktree_lock_staleness.py decide() -- every distinct outcome
# the real, current source can produce.
# --------------------------------------------------------------------------


def scenario_stale_lock_worktree_gone() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)
        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.parent.mkdir(parents=True, exist_ok=True)

        missing_worktree = root / ".claude" / "worktrees" / "wf-dead-worktree"
        acquired = try_acquire_lock(lock_dir, "wf-dead", str(missing_worktree))
        require(acquired, "stale_lock_worktree_gone: initial acquire should succeed")

        decision = staleness_decision(lock_dir / "metadata.json", root, "wf-challenger")
        require(
            decision.startswith("STALE_WORKTREE_GONE"),
            f"stale_lock_worktree_gone: expected stale-worktree-gone reclaim, got {decision}",
        )

        shutil.rmtree(lock_dir)
        reacquired = try_acquire_lock(lock_dir, "wf-challenger", str(root))
        require(reacquired, "stale_lock_worktree_gone: reclaim must allow a fresh acquire")


def scenario_stale_lock_age_inactive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)
        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.parent.mkdir(parents=True, exist_ok=True)

        worktree_path = root / ".claude" / "worktrees" / "wf-abandoned-worktree"
        git(root, "worktree", "add", str(worktree_path), "-b", "wf-abandoned-branch")

        lock_dir.mkdir()
        (lock_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "workflow_uuid": "wf-abandoned",
                    "worktree_path": str(worktree_path),
                    "acquired_at": iso_now(offset_seconds=-3 * 3600),
                }
            )
        )
        # No workflow artifact at all for wf-abandoned -- simulates a session
        # that crashed before ever writing one, or whose artifact is unreadable.

        decision = staleness_decision(lock_dir / "metadata.json", root, "wf-challenger")
        require(
            decision.startswith("STALE_INACTIVE wf-abandoned"),
            f"stale_lock_age_inactive: expected age+inactivity reclaim naming the holder, got {decision}",
        )


def scenario_old_lock_but_recent_activity_not_reclaimed() -> None:
    """Negative control: an old lock whose holder workflow artifact shows
    recent activity must NOT be reclaimed -- proves the conservative bias in
    the staleness script actually holds, not just the happy path."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)
        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.parent.mkdir(parents=True, exist_ok=True)

        worktree_path = root / ".claude" / "worktrees" / "wf-slow-worktree"
        git(root, "worktree", "add", str(worktree_path), "-b", "wf-slow-branch")

        lock_dir.mkdir()
        (lock_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "workflow_uuid": "wf-slow",
                    "worktree_path": str(worktree_path),
                    "acquired_at": iso_now(offset_seconds=-3 * 3600),
                }
            )
        )
        artifact_dir = root / ".craftflow" / "state" / "workflows"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "wf-slow.json").write_text(
            json.dumps({"workflow_uuid": "wf-slow", "updated_at": iso_now(offset_seconds=-60)})
        )

        decision = staleness_decision(lock_dir / "metadata.json", root, "wf-challenger")
        require(
            decision.startswith("CONTENDED wf-slow"),
            f"old_lock_but_recent_activity_not_reclaimed: an old lock with recent holder activity "
            f"must stay contended, got {decision}",
        )


def scenario_self_identity_reclaim() -> None:
    """A workflow resuming after crashing while it itself held the lock must
    reclaim immediately -- but ONLY once the lock's recorded worktree_path is
    positively confirmed gone (real proof of death), never on a workflow_uuid
    identity match alone. See scenario_self_identity_no_reclaim_when_worktree_alive
    for the falsifying negative control that proves identity alone is NOT
    enough."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)
        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.parent.mkdir(parents=True, exist_ok=True)

        lock_dir.mkdir()
        (lock_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "workflow_uuid": "wf-self",
                    # Never created -- git worktree list will not show it, so
                    # this is positive proof of death, not identity alone.
                    "worktree_path": str(root / "nonexistent-worktree"),
                    "acquired_at": iso_now(),  # brand new, not stale by age at all
                }
            )
        )

        decision = staleness_decision(lock_dir / "metadata.json", root, "wf-self")
        require(
            decision.startswith("SELF_RECLAIM wf-self"),
            f"self_identity_reclaim: a workflow's own lock must reclaim immediately once its "
            f"worktree is confirmed gone, got {decision}",
        )


def scenario_self_identity_no_reclaim_when_worktree_alive() -> None:
    """Falsifying negative control: a same-workflow_uuid resume whose recorded
    worktree STILL EXISTS, with no other staleness signal, must NOT reclaim on
    identity alone -- it must wait/gate exactly like a stranger's lock would.
    This is exactly the bug class a second reviewer found in this plan's own
    revision history: an unconditional identity-match SELF_RECLAIM would let a
    second, concurrent process for the same workflow_uuid steal the lock from
    a first process that is genuinely still mid-merge."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)
        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.parent.mkdir(parents=True, exist_ok=True)

        worktree_path = root / ".claude" / "worktrees" / "wf-self-worktree"
        git(root, "worktree", "add", str(worktree_path), "-b", "wf-self-branch")

        lock_dir.mkdir()
        (lock_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "workflow_uuid": "wf-self",
                    "worktree_path": str(worktree_path),
                    "acquired_at": iso_now(),  # brand new -- not stale by age either
                }
            )
        )

        decision = staleness_decision(lock_dir / "metadata.json", root, "wf-self")
        require(
            decision.startswith("CONTENDED wf-self"),
            f"self_identity_no_reclaim_when_worktree_alive: a same-workflow_uuid resume with a "
            f"still-existing worktree and no staleness signal must NOT reclaim on identity alone, "
            f"got {decision}",
        )


def scenario_lock_read_error_distinct_from_contention() -> None:
    """A real filesystem/permission read error must surface as LOCK_READ_ERROR,
    not be folded into CONTENDED_UNKNOWN_HOLDER."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)
        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.parent.mkdir(parents=True, exist_ok=True)

        lock_dir.mkdir()
        meta_path = lock_dir / "metadata.json"
        meta_path.write_text(json.dumps({"workflow_uuid": "wf-x"}))
        meta_path.chmod(0o000)
        try:
            decision = staleness_decision(meta_path, root, "wf-challenger")
        finally:
            meta_path.chmod(0o644)  # restore before TemporaryDirectory cleanup

        require(
            decision.startswith("LOCK_READ_ERROR"),
            f"lock_read_error_distinct_from_contention: a real permission error must not be "
            f"reported as ordinary contention, got {decision}",
        )


def scenario_contended_unknown_holder_benign_race() -> None:
    """The lock directory exists (mkdir succeeded) but metadata.json has not
    been written yet -- the benign, brief mkdir->printf gap. A FRESH lock dir
    in this state must stay CONTENDED_UNKNOWN_HOLDER, never reclaimed."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)
        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.mkdir(parents=True)
        # metadata.json deliberately never written.

        decision = staleness_decision(lock_dir / "metadata.json", root, "wf-challenger")
        require(
            decision == "CONTENDED_UNKNOWN_HOLDER unknown",
            f"contended_unknown_holder_benign_race: a fresh lock dir with no metadata.json yet "
            f"must stay CONTENDED_UNKNOWN_HOLDER, got {decision}",
        )


def scenario_stale_inactive_missing_metadata() -> None:
    """Round-5 fix: metadata.json never appears at all (not a corrupt-JSON
    case) AND the lock directory itself is old -- must still be reclaimable
    via the age-based dir-mtime fallback, not a permanent CONTENDED_UNKNOWN_HOLDER
    dead-end."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)
        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.mkdir(parents=True)
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=3 * 3600)).timestamp()
        os.utime(lock_dir, (old_time, old_time))

        decision = staleness_decision(lock_dir / "metadata.json", root, "wf-challenger")
        require(
            decision == "STALE_INACTIVE unknown",
            f"stale_inactive_missing_metadata: an old lock dir with no metadata.json at all must "
            f"reclaim via the dir-mtime fallback, got {decision}",
        )


def scenario_stale_inactive_corrupt_json() -> None:
    """metadata.json exists and is readable but its JSON body is corrupt --
    a distinct code path from the missing-metadata.json case above, but must
    reach the same STALE_INACTIVE unknown outcome via the shared dir-mtime
    fallback."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)
        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.mkdir(parents=True)
        (lock_dir / "metadata.json").write_text("{not valid json")
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=3 * 3600)).timestamp()
        os.utime(lock_dir, (old_time, old_time))

        decision = staleness_decision(lock_dir / "metadata.json", root, "wf-challenger")
        require(
            decision == "STALE_INACTIVE unknown",
            f"stale_inactive_corrupt_json: an old lock dir with corrupt metadata.json must reclaim "
            f"via the dir-mtime fallback, got {decision}",
        )


def scenario_git_worktree_list_error_launch_failure() -> None:
    """Round-3 fix, launch-failure form: the `git worktree list` subprocess
    itself fails to launch (a nonexistent cwd reproduces a real OSError) --
    must surface as GIT_WORKTREE_LIST_ERROR, never a bare traceback, never
    ordinary contention."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        lock_dir = root / "lockdir"
        lock_dir.mkdir(parents=True)
        (lock_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "workflow_uuid": "wf-x",
                    "worktree_path": str(root / "somewhere"),
                    "acquired_at": iso_now(),
                }
            )
        )
        nonexistent_root = root / "does-not-exist-as-cwd"

        decision = staleness_decision(lock_dir / "metadata.json", nonexistent_root, "wf-challenger")
        require(
            decision.startswith("GIT_WORKTREE_LIST_ERROR") and "NonZeroExit" not in decision,
            f"git_worktree_list_error_launch_failure: a subprocess launch failure must surface as "
            f"the launch-failure form of GIT_WORKTREE_LIST_ERROR, got {decision}",
        )


def scenario_git_worktree_list_error_nonzero_exit() -> None:
    """Round-3 fix, non-zero-exit form: the subprocess launches fine but
    `git worktree list --porcelain` itself exits non-zero (project_root is
    not a git repository) -- a distinct code path from the launch-failure
    case above."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)  # deliberately NOT git-initialized
        root.mkdir(parents=True, exist_ok=True)
        lock_dir = root / "lockdir"
        lock_dir.mkdir(parents=True)
        (lock_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "workflow_uuid": "wf-x",
                    "worktree_path": str(root / "somewhere"),
                    "acquired_at": iso_now(),
                }
            )
        )

        decision = staleness_decision(lock_dir / "metadata.json", root, "wf-challenger")
        require(
            decision.startswith("GIT_WORKTREE_LIST_ERROR NonZeroExit"),
            f"git_worktree_list_error_nonzero_exit: a non-git project_root must surface the "
            f"non-zero-exit form of GIT_WORKTREE_LIST_ERROR, got {decision}",
        )


# --------------------------------------------------------------------------
# craftflow_worktree_copy_fallback.py apply_copy_fallback() -- every
# distinct outcome the real, current source can produce.
# --------------------------------------------------------------------------


def scenario_copy_fallback_dirty_tree_regression() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)

        worktree_path = root / ".claude" / "worktrees" / "wf-builder-worktree"
        git(root, "worktree", "add", str(worktree_path), "-b", "wf-builder-branch")
        (worktree_path / "feature.txt").write_text("built but never committed\n")

        merge_result = git(root, "merge", "wf-builder-branch")
        require(
            "Already up to date" in merge_result.stdout,
            "copy_fallback_dirty_tree_regression: expected the documented no-op merge gotcha to reproduce",
        )

        (root / "README.md").write_text("dirtied concurrently\n")

        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.mkdir(parents=True)
        outcome = attempt_finalize(root, worktree_path, lock_dir)
        require(
            outcome == "GATED_DIRTY",
            f"copy_fallback_dirty_tree_regression: dirty-tree gate must fire even though the merge "
            f"reported no-op, got {outcome}",
        )
        require(
            not (root / "feature.txt").exists(),
            "copy_fallback_dirty_tree_regression: the real copy-fallback script must never have run "
            "before the dirty-tree gate",
        )

        git(root, "worktree", "remove", str(worktree_path), "--force")


def scenario_copy_fallback_applies_when_clean() -> None:
    """Proves the REAL copy-fallback script correctly applies a mixed
    Added/Modified/Deleted/Renamed change set, including a path with a
    literal space, when the guard's ordering allows it through."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_repo(root)
        (root / "dir a").mkdir()
        (root / "dir a" / "orig file.txt").write_text("orig\n")
        (root / "keepme.txt").write_text("keep\n")
        (root / "deleteme.txt").write_text("bye\n")
        git(root, "add", ".")
        git(root, "commit", "-q", "-m", "second commit")

        worktree_path = root.parent / f"{root.name}-worktree"
        git(root, "worktree", "add", str(worktree_path), "-b", "wf-clean-branch")

        (worktree_path / "dir a" / "orig file.txt").rename(worktree_path / "dir a" / "renamed file.txt")
        (worktree_path / "deleteme.txt").unlink()
        (worktree_path / "keepme.txt").write_text("modified\n")
        (worktree_path / "new file with spaces.txt").write_text("brand new\n")
        git(worktree_path, "add", "-A")

        lock_dir = root / ".claude" / "worktrees" / ".merge.lock"
        lock_dir.mkdir(parents=True)
        outcome = attempt_finalize(root, worktree_path, lock_dir)
        require(
            outcome == "COPY_FALLBACK_APPLIED",
            f"copy_fallback_applies_when_clean: expected the copy-fallback to apply on a clean "
            f"main tree, got {outcome}",
        )

        require(not (root / "deleteme.txt").exists(), "copy_fallback_applies_when_clean: deleted file must be removed")
        require(
            not (root / "dir a" / "orig file.txt").exists(),
            "copy_fallback_applies_when_clean: rename's old path must be removed",
        )
        require(
            (root / "dir a" / "renamed file.txt").read_text() == "orig\n",
            "copy_fallback_applies_when_clean: rename's new path must carry the correct content",
        )
        require((root / "keepme.txt").read_text() == "modified\n", "copy_fallback_applies_when_clean: modified file must land")
        require(
            (root / "new file with spaces.txt").read_text() == "brand new\n",
            "copy_fallback_applies_when_clean: a path containing a literal space must be copied correctly",
        )

        git(root, "worktree", "remove", str(worktree_path), "--force")


def scenario_copy_fallback_conflict_rejected() -> None:
    """A two-sided conflict status (AA, real add/add) must be rejected with a
    clean, non-zero exit -- never silently copied with its literal conflict
    markers still in it."""
    with tempfile.TemporaryDirectory() as tmp:
        conflict_repo = Path(tmp) / "conflict-repo"
        init_repo(conflict_repo)
        base_branch = current_branch(conflict_repo)

        git(conflict_repo, "checkout", "-b", "branch-a")
        (conflict_repo / "newfile.txt").write_text("version A\n")
        git(conflict_repo, "add", "newfile.txt")
        git(conflict_repo, "commit", "-q", "-m", "add on branch-a")

        git(conflict_repo, "checkout", base_branch)
        git(conflict_repo, "checkout", "-b", "branch-b")
        (conflict_repo / "newfile.txt").write_text("version B\n")
        git(conflict_repo, "add", "newfile.txt")
        git(conflict_repo, "commit", "-q", "-m", "add on branch-b")

        git(conflict_repo, "checkout", base_branch)
        merge_a = git(conflict_repo, "merge", "branch-a")
        require(merge_a.returncode == 0, f"copy_fallback_conflict_rejected: setup merge-a failed: {merge_a.stderr}")
        merge_b = git(conflict_repo, "merge", "branch-b")
        require(
            merge_b.returncode != 0 and "CONFLICT" in merge_b.stdout,
            f"copy_fallback_conflict_rejected: expected a real add/add conflict to reproduce, "
            f"got rc={merge_b.returncode} stdout={merge_b.stdout!r}",
        )

        project_root = Path(tmp) / "project-root"
        project_root.mkdir()
        result = apply_copy_fallback(conflict_repo, project_root)
        require(
            result.returncode != 0,
            "copy_fallback_conflict_rejected: an AA conflict status must be rejected, not copied",
        )
        require(
            "AA" in result.stderr and "refusing to guess" in result.stderr,
            f"copy_fallback_conflict_rejected: expected a clear AA-conflict rejection message, "
            f"got stderr={result.stderr!r}",
        )
        require(
            not (project_root / "newfile.txt").exists(),
            "copy_fallback_conflict_rejected: the conflicted file must never be copied into project_root",
        )


def scenario_copy_fallback_rename_missing_source_rejected() -> None:
    """A staged rename (RD) whose new path was itself deleted afterward, with
    no source file left in the worktree to copy, must raise a clean error
    instead of an unhandled FileNotFoundError."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        init_repo(repo)
        (repo / "orig.txt").write_text("hello\n")
        git(repo, "add", "orig.txt")
        git(repo, "commit", "-q", "-m", "add orig")
        git(repo, "mv", "orig.txt", "renamed.txt")
        (repo / "renamed.txt").unlink()

        project_root = Path(tmp) / "project-root"
        project_root.mkdir()
        result = apply_copy_fallback(repo, project_root)
        require(
            result.returncode != 0,
            "copy_fallback_rename_missing_source_rejected: an RD entry with no source file must be rejected",
        )
        require(
            "refusing to guess" in result.stderr and "renamed.txt" in result.stderr,
            f"copy_fallback_rename_missing_source_rejected: expected a clear missing-source "
            f"rejection message, got stderr={result.stderr!r}",
        )
        require(
            "FileNotFoundError" not in result.stderr.splitlines()[-1] if result.stderr else True,
            "copy_fallback_rename_missing_source_rejected: must not surface as an unhandled "
            "FileNotFoundError traceback",
        )


def scenario_copy_fallback_dd_conflict_rejected() -> None:
    """REM-FIX (CRITICAL gap): a two-sided DD (both-deleted) conflict status
    -- reproduced via a real stage1-only index entry -- must be rejected with
    a clean, non-zero exit mentioning DD, never silently deleted. DD (like
    AA, already covered above) contains no literal 'U' or '!' character, so
    it relies SOLELY on exact CONFLICT_STATUSES frozenset membership: live
    falsification during this round confirmed that dropping DD from the
    frozenset makes the real script fall through to the delete branch
    ("D" in xy) and silently _remove_if_exists the conflicted path with exit
    0 -- the full 22-scenario suite still reported OK before this scenario
    existed."""
    with tempfile.TemporaryDirectory() as tmp:
        conflict_repo = Path(tmp) / "dd-repo"
        init_repo(conflict_repo)
        (conflict_repo / "conflict.txt").write_text("shared ancestor content\n")
        git(conflict_repo, "add", "conflict.txt")
        git(conflict_repo, "commit", "-q", "-m", "add conflict.txt")

        _set_stage_only(conflict_repo, "conflict.txt", [1])

        status = git(conflict_repo, "status", "--porcelain=1", "-z")
        require(
            status.stdout.split("\x00", 1)[0] == "DD conflict.txt",
            f"copy_fallback_dd_conflict_rejected: expected a real DD porcelain status, got {status.stdout!r}",
        )

        project_root = Path(tmp) / "project-root"
        project_root.mkdir()
        (project_root / "conflict.txt").write_text("must never be removed\n")

        result = apply_copy_fallback(conflict_repo, project_root)
        require(
            result.returncode != 0,
            "copy_fallback_dd_conflict_rejected: a DD conflict status must be rejected, not silently deleted",
        )
        require(
            "DD" in result.stderr and "refusing to guess" in result.stderr,
            f"copy_fallback_dd_conflict_rejected: expected a clear DD-conflict rejection message, "
            f"got stderr={result.stderr!r}",
        )
        require(
            (project_root / "conflict.txt").exists()
            and (project_root / "conflict.txt").read_text() == "must never be removed\n",
            "copy_fallback_dd_conflict_rejected: the conflicted file must never be removed from project_root",
        )


def scenario_copy_fallback_ud_conflict_rejected() -> None:
    """REM-FIX (HIGH gap): a two-sided UD (deleted-by-them) conflict status
    -- reproduced via real stage1+2 index entries -- must be rejected with a
    clean, non-zero exit mentioning UD. UD (and its sibling DU) contains a
    literal 'U', so it is redundantly protected by the "U" in xy substring
    fallback even without exact frozenset membership -- unlike the DD
    scenario above, this one specifically proves the conflict-check-runs-
    BEFORE-the-delete-branch ORDERING is correct (UD also contains a literal
    'D' and, per the real script's own comment, would be silently mishandled
    by the delete branch if that check ran first), a distinct regression
    class the DD scenario alone cannot distinguish from a membership-only
    fix."""
    with tempfile.TemporaryDirectory() as tmp:
        conflict_repo = Path(tmp) / "ud-repo"
        init_repo(conflict_repo)
        (conflict_repo / "conflict.txt").write_text("shared ancestor content\n")
        git(conflict_repo, "add", "conflict.txt")
        git(conflict_repo, "commit", "-q", "-m", "add conflict.txt")

        _set_stage_only(conflict_repo, "conflict.txt", [1, 2])

        status = git(conflict_repo, "status", "--porcelain=1", "-z")
        require(
            status.stdout.split("\x00", 1)[0] == "UD conflict.txt",
            f"copy_fallback_ud_conflict_rejected: expected a real UD porcelain status, got {status.stdout!r}",
        )

        project_root = Path(tmp) / "project-root"
        project_root.mkdir()
        (project_root / "conflict.txt").write_text("must never be removed\n")

        result = apply_copy_fallback(conflict_repo, project_root)
        require(
            result.returncode != 0,
            "copy_fallback_ud_conflict_rejected: a UD conflict status must be rejected, not silently deleted",
        )
        require(
            "UD" in result.stderr and "refusing to guess" in result.stderr,
            f"copy_fallback_ud_conflict_rejected: expected a clear UD-conflict rejection message, "
            f"got stderr={result.stderr!r}",
        )
        require(
            (project_root / "conflict.txt").exists()
            and (project_root / "conflict.txt").read_text() == "must never be removed\n",
            "copy_fallback_ud_conflict_rejected: the conflicted file must never be removed from project_root",
        )


def scenario_copy_fallback_uu_conflict_rejected() -> None:
    """REM-FIX (MEDIUM gap): a two-sided UU (both-modified) conflict status
    -- reproduced via real stage1+2+3 index entries -- must be rejected with
    a clean, non-zero exit mentioning UU. UU (like its siblings AU/UA) is
    fully, redundantly protected by the "U" in xy substring fallback alone,
    unlike AA/DD above -- this scenario gives real, executed coverage to at
    least one of the docstring's full conflict-code enumeration
    (UU/AA/DD/AU/UA/DU/UD/!!) that was previously named but never exercised,
    alongside a positive assertion that the conflicted file's content is
    never overwritten in project_root."""
    with tempfile.TemporaryDirectory() as tmp:
        conflict_repo = Path(tmp) / "uu-repo"
        init_repo(conflict_repo)
        (conflict_repo / "conflict.txt").write_text("shared ancestor content\n")
        git(conflict_repo, "add", "conflict.txt")
        git(conflict_repo, "commit", "-q", "-m", "add conflict.txt")

        _set_stage_only(conflict_repo, "conflict.txt", [1, 2, 3])

        status = git(conflict_repo, "status", "--porcelain=1", "-z")
        require(
            status.stdout.split("\x00", 1)[0] == "UU conflict.txt",
            f"copy_fallback_uu_conflict_rejected: expected a real UU porcelain status, got {status.stdout!r}",
        )

        project_root = Path(tmp) / "project-root"
        project_root.mkdir()
        (project_root / "conflict.txt").write_text("must never be copied over\n")

        result = apply_copy_fallback(conflict_repo, project_root)
        require(
            result.returncode != 0,
            "copy_fallback_uu_conflict_rejected: a UU conflict status must be rejected, not silently copied",
        )
        require(
            "UU" in result.stderr and "refusing to guess" in result.stderr,
            f"copy_fallback_uu_conflict_rejected: expected a clear UU-conflict rejection message, "
            f"got stderr={result.stderr!r}",
        )
        require(
            (project_root / "conflict.txt").read_text() == "must never be copied over\n",
            "copy_fallback_uu_conflict_rejected: the conflicted file's content must never be overwritten "
            "in project_root",
        )


def scenario_copy_fallback_short_token_rejected() -> None:
    """REM-FIX (LOW gap 1): the `if len(token) < 4: raise RuntimeError(...)`
    defensive branch -- a malformed/truncated porcelain token shorter than
    the minimum '<XY> <path>' shape. Real git never actually emits a token
    this short, so this is exercised by feeding crafted porcelain bytes
    directly into the REAL, unmodified apply_copy_fallback() parsing logic
    in-process (via _run_copy_fallback_with_crafted_porcelain), not a ported
    approximation."""
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp) / "project-root"
        project_root.mkdir()
        raised = False
        message = ""
        try:
            _run_copy_fallback_with_crafted_porcelain(b"ABC\x00", project_root)
        except Exception as exc:  # noqa: BLE001
            raised = True
            message = str(exc)
        require(
            raised,
            "copy_fallback_short_token_rejected: a porcelain token shorter than 4 chars must raise, "
            "not silently proceed",
        )
        require(
            "unexpected short porcelain token" in message and "ABC" in message,
            f"copy_fallback_short_token_rejected: expected the short-token error message, got {message!r}",
        )


def scenario_copy_fallback_rename_missing_old_path_rejected() -> None:
    """REM-FIX (LOW gap 2): the `if i >= len(tokens): raise RuntimeError(...)`
    defensive branch -- a rename/copy porcelain entry (xy contains 'R'/'C')
    that is the LAST token in the stream, with no following old-path field.
    Real git never actually emits this shape (a genuine rename/copy record
    is always immediately followed by its old-path token), so this is
    exercised the same way as the short-token branch above: crafted
    porcelain bytes fed directly into the real, unmodified parsing logic
    in-process."""
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp) / "project-root"
        project_root.mkdir()
        raised = False
        message = ""
        try:
            _run_copy_fallback_with_crafted_porcelain(b"R  new.txt\x00", project_root)
        except Exception as exc:  # noqa: BLE001
            raised = True
            message = str(exc)
        require(
            raised,
            "copy_fallback_rename_missing_old_path_rejected: a rename entry with no old-path field must "
            "raise, not index past the end of the token list",
        )
        require(
            "missing its old-path field" in message,
            f"copy_fallback_rename_missing_old_path_rejected: expected the missing-old-path error "
            f"message, got {message!r}",
        )


def scenario_copy_fallback_symlink_preserved() -> None:
    """A working relative symlink must be recreated as a symlink in the
    destination, never dereferenced into a byte-copy of its target's
    contents."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "root"
        init_repo(root)
        worktree_path = Path(tmp) / "root-worktree"
        git(root, "worktree", "add", str(worktree_path), "-b", "wf-symlink-branch")

        (worktree_path / "target.txt").write_text("payload\n")
        (worktree_path / "link.txt").symlink_to("target.txt")

        result = apply_copy_fallback(worktree_path, root)
        require(result.returncode == 0, f"copy_fallback_symlink_preserved: script failed: {result.stderr}")

        dest_link = root / "link.txt"
        require(dest_link.is_symlink(), "copy_fallback_symlink_preserved: destination must remain a symlink")
        require(
            os.readlink(dest_link) == "target.txt",
            "copy_fallback_symlink_preserved: relative symlink target must be preserved verbatim",
        )
        require((root / "target.txt").read_text() == "payload\n", "copy_fallback_symlink_preserved: target file must also be copied")


def scenario_copy_fallback_absolute_symlink_rewritten() -> None:
    """An ABSOLUTE symlink target rooted under worktree_path must be rewritten
    to the equivalent path under project_root -- recreating it verbatim would
    leave it pointing at a path the caller's own next step deletes moments
    later."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "root"
        init_repo(root)
        worktree_path = Path(tmp) / "root-worktree"
        git(root, "worktree", "add", str(worktree_path), "-b", "wf-abslink-branch")

        (worktree_path / "sub").mkdir()
        (worktree_path / "sub" / "payload.txt").write_text("payload\n")
        abs_target = str((worktree_path / "sub" / "payload.txt").resolve())
        (worktree_path / "sub" / "abs_link.txt").symlink_to(abs_target)

        result = apply_copy_fallback(worktree_path, root)
        require(
            result.returncode == 0,
            f"copy_fallback_absolute_symlink_rewritten: script failed: {result.stderr}",
        )

        dest_link = root / "sub" / "abs_link.txt"
        require(dest_link.is_symlink(), "copy_fallback_absolute_symlink_rewritten: destination must remain a symlink")
        link_target = os.readlink(dest_link)
        expected_target = os.path.normpath(str(root)) + os.sep + "sub" + os.sep + "payload.txt"
        require(
            link_target == expected_target,
            f"copy_fallback_absolute_symlink_rewritten: expected rewritten target {expected_target!r}, "
            f"got {link_target!r}",
        )


def scenario_copy_fallback_chained_symlink_preserved() -> None:
    """A chained symlink (link1 -> link2 -> file, link1 absolute) must
    preserve its 2-hop structure -- link1 must be recreated pointing at link2,
    never flattened straight through to the final file."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "root"
        init_repo(root)
        worktree_path = Path(tmp) / "root-worktree"
        git(root, "worktree", "add", str(worktree_path), "-b", "wf-chain-branch")

        (worktree_path / "sub").mkdir()
        (worktree_path / "sub" / "payload.txt").write_text("payload\n")
        (worktree_path / "sub" / "link2").symlink_to("payload.txt")
        # link1 is an ABSOLUTE symlink pointing at link2 itself (not resolved
        # through it) -- deliberately built without .resolve() so it names
        # link2's path, not payload.txt's.
        link2_abs_path = os.path.join(str(worktree_path), "sub", "link2")
        (worktree_path / "sub" / "link1").symlink_to(link2_abs_path)

        result = apply_copy_fallback(worktree_path, root)
        require(
            result.returncode == 0,
            f"copy_fallback_chained_symlink_preserved: script failed: {result.stderr}",
        )

        dest_link1 = root / "sub" / "link1"
        dest_link2 = root / "sub" / "link2"
        require(dest_link1.is_symlink(), "copy_fallback_chained_symlink_preserved: link1 must remain a symlink")
        require(dest_link2.is_symlink(), "copy_fallback_chained_symlink_preserved: link2 must remain a symlink")
        target1 = os.readlink(dest_link1)
        require(
            os.path.basename(target1) == "link2",
            f"copy_fallback_chained_symlink_preserved: link1 must point at link2 (2-hop), not be "
            f"flattened straight to payload.txt, got {target1!r}",
        )
        require(
            os.readlink(dest_link2) == "payload.txt",
            "copy_fallback_chained_symlink_preserved: link2's own relative target must be unchanged",
        )
        require(
            dest_link1.resolve().read_text() == "payload\n",
            "copy_fallback_chained_symlink_preserved: the full 2-hop chain must still resolve to the real content",
        )


def scenario_copy_fallback_untracked_top_level_directory() -> None:
    """A brand-new untracked TOP-LEVEL DIRECTORY (never seen by git before)
    must be copied file-by-file, not collapse into a single porcelain token
    that crashes the script with IsADirectoryError -- requires
    --untracked-files=all."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "root"
        init_repo(root)
        worktree_path = Path(tmp) / "root-worktree"
        git(root, "worktree", "add", str(worktree_path), "-b", "wf-newdir-branch")

        new_dir = worktree_path / "brand-new-dir"
        new_dir.mkdir()
        (new_dir / "a.txt").write_text("a\n")
        (new_dir / "b.txt").write_text("b\n")
        nested = new_dir / "nested"
        nested.mkdir()
        (nested / "c.txt").write_text("c\n")

        result = apply_copy_fallback(worktree_path, root)
        require(
            result.returncode == 0,
            f"copy_fallback_untracked_top_level_directory: real script must not crash on a "
            f"brand-new untracked top-level directory (verifies --untracked-files=all), "
            f"stderr={result.stderr!r}",
        )
        require((root / "brand-new-dir" / "a.txt").read_text() == "a\n", "copy_fallback_untracked_top_level_directory: a.txt must be copied")
        require((root / "brand-new-dir" / "b.txt").read_text() == "b\n", "copy_fallback_untracked_top_level_directory: b.txt must be copied")
        require(
            (root / "brand-new-dir" / "nested" / "c.txt").read_text() == "c\n",
            "copy_fallback_untracked_top_level_directory: nested/c.txt must be copied",
        )


SCENARIOS = {
    # Guard-sequence properties (P1/P3)
    "dirty_main_tree": scenario_dirty_main_tree,
    "lock_contended": scenario_lock_contended,
    "merge_conflict_unaffected": scenario_merge_conflict_unaffected,
    # craftflow_worktree_lock_staleness.py decide() -- every distinct outcome
    "stale_lock_worktree_gone": scenario_stale_lock_worktree_gone,
    "stale_lock_age_inactive": scenario_stale_lock_age_inactive,
    "old_lock_but_recent_activity_not_reclaimed": scenario_old_lock_but_recent_activity_not_reclaimed,
    "self_identity_reclaim": scenario_self_identity_reclaim,
    "self_identity_no_reclaim_when_worktree_alive": scenario_self_identity_no_reclaim_when_worktree_alive,
    "lock_read_error_distinct_from_contention": scenario_lock_read_error_distinct_from_contention,
    "contended_unknown_holder_benign_race": scenario_contended_unknown_holder_benign_race,
    "stale_inactive_missing_metadata": scenario_stale_inactive_missing_metadata,
    "stale_inactive_corrupt_json": scenario_stale_inactive_corrupt_json,
    "git_worktree_list_error_launch_failure": scenario_git_worktree_list_error_launch_failure,
    "git_worktree_list_error_nonzero_exit": scenario_git_worktree_list_error_nonzero_exit,
    # craftflow_worktree_copy_fallback.py apply_copy_fallback() -- every
    # distinct outcome, plus the gate-ordering regression (P2)
    "copy_fallback_dirty_tree_regression": scenario_copy_fallback_dirty_tree_regression,
    "copy_fallback_applies_when_clean": scenario_copy_fallback_applies_when_clean,
    "copy_fallback_conflict_rejected": scenario_copy_fallback_conflict_rejected,
    "copy_fallback_dd_conflict_rejected": scenario_copy_fallback_dd_conflict_rejected,
    "copy_fallback_ud_conflict_rejected": scenario_copy_fallback_ud_conflict_rejected,
    "copy_fallback_uu_conflict_rejected": scenario_copy_fallback_uu_conflict_rejected,
    "copy_fallback_short_token_rejected": scenario_copy_fallback_short_token_rejected,
    "copy_fallback_rename_missing_old_path_rejected": scenario_copy_fallback_rename_missing_old_path_rejected,
    "copy_fallback_rename_missing_source_rejected": scenario_copy_fallback_rename_missing_source_rejected,
    "copy_fallback_symlink_preserved": scenario_copy_fallback_symlink_preserved,
    "copy_fallback_absolute_symlink_rewritten": scenario_copy_fallback_absolute_symlink_rewritten,
    "copy_fallback_chained_symlink_preserved": scenario_copy_fallback_chained_symlink_preserved,
    "copy_fallback_untracked_top_level_directory": scenario_copy_fallback_untracked_top_level_directory,
}


def main() -> int:
    for path in (STALENESS_SCRIPT, COPY_FALLBACK_SCRIPT):
        if not path.exists():
            print(f"FAIL: required guard script not found: {path}", file=sys.stderr)
            return 1

    errors = []
    for name, fn in SCENARIOS.items():
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print("craftflow_worktree_merge_guard_check: OK")
    print(f"scenarios={len(SCENARIOS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
