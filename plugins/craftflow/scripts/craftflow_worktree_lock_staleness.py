#!/usr/bin/env python3
"""Staleness/contention decision for the BUILD worktree merge-safety lock
(SKILL.md step 4b, "### Worktree Isolation (BUILD Default)"). Invoked directly
by SKILL.md's bash via `python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_worktree_lock_staleness.py" <lock_metadata_path> <project_root> [current_workflow_uuid]`
and, identically, by the proof script (craftflow_worktree_merge_guard_check.py)
via subprocess -- there is exactly one copy of this decision logic.

Prints exactly one line to stdout: one of
  SELF_RECLAIM <workflow_uuid>
  STALE_WORKTREE_GONE <workflow_uuid>
  STALE_INACTIVE <workflow_uuid>
  STALE_INACTIVE unknown
  CONTENDED <workflow_uuid>
  CONTENDED_UNKNOWN_HOLDER unknown
  LOCK_READ_ERROR <exception_class_name>
  GIT_WORKTREE_LIST_ERROR <exception_class_name>
  GIT_WORKTREE_LIST_ERROR NonZeroExit(rc=<n>): <stderr>

`STALE_INACTIVE unknown` is distinct from `STALE_INACTIVE <workflow_uuid>`: it
is emitted when there is no holder identity to name at all -- either (a)
`metadata.json` does not exist yet (a process was killed between `mkdir`
succeeding and the `printf > metadata.json` write that follows it in
SKILL.md), or (b) `metadata.json` exists and is readable but its JSON body is
corrupt/truncated/non-dict (e.g. a crash mid-`printf`-write). Both cases fall
back to the lock DIRECTORY's own mtime as a substitute for `acquired_at`
(`_stale_by_dir_mtime()`) so a genuinely stale, holder-less lock directory
must still be reclaimable via the age check rather than becoming a permanent,
un-reclaimable `CONTENDED_UNKNOWN_HOLDER` dead-end (no recent-activity
corroboration is possible without a holder identity, so age alone gates this
case -- conservative, since the ceiling is 2 hours). A genuinely FRESH lock
directory in either case (younger than `STALE_AGE_SECONDS`) correctly stays
`CONTENDED_UNKNOWN_HOLDER unknown`, not reclaimed.

`GIT_WORKTREE_LIST_ERROR <exception_class_name>` is emitted when the
`git worktree list --porcelain` subprocess call itself fails to even run
(e.g. `git` not on PATH, or another OSError) -- this is a local
environment/execution problem, not evidence of another live workflow, and
(like `LOCK_READ_ERROR`) intentionally does not match any reclaim pattern in
SKILL.md's case statement: it fails closed and waits out the budget the same
way `LOCK_READ_ERROR` does.

`GIT_WORKTREE_LIST_ERROR NonZeroExit(rc=<n>): <stderr>` is emitted when the
subprocess itself launched fine but `git worktree list --porcelain` exited
non-zero (e.g. `project_root` is not a git repository, a corrupt `.git`,
a permission error, or index-lock contention) -- a *run* failure rather than
a *launch* failure. Without this check, `live_paths` would silently stay
`None` and the decision would fall through to the ordinary
worktree-undetermined path -- reporting a fresh lock as plain `CONTENDED`
(wrong-cause "another workflow holds the lock" message) or, worse, an old
lock as `STALE_INACTIVE` (an unwarranted auto-reclaim, since the
worktree-liveness check that would normally gate that decision never ran at
all). This outcome carries the real exit code and `stderr` text (newlines
collapsed to spaces to preserve the one-line-of-stdout contract) so the
eventual gate message can name the actual local cause instead of implying a
live concurrent workflow. Like the launch-failure form, it intentionally does
not match any reclaim pattern in SKILL.md's case statement: it fails closed
and waits out the budget the same way `LOCK_READ_ERROR` does.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

STALE_AGE_SECONDS = 7200
RECENT_ACTIVITY_SECONDS = 900


def parse_iso(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _stale_by_dir_mtime(lock_meta_path):
    """Age-based fallback shared by the "no metadata.json at all" and the
    "metadata.json exists but is unparseable" cases -- neither has a holder
    identity to recover, so both fall back to the lock DIRECTORY's own mtime
    as a substitute for `acquired_at` and route through the same
    `STALE_AGE_SECONDS` age check a valid-but-old lock would get, rather than
    becoming a permanent, un-reclaimable `CONTENDED_UNKNOWN_HOLDER` dead-end.
    Returns "STALE_INACTIVE unknown" if the directory is older than the
    staleness ceiling, else "CONTENDED_UNKNOWN_HOLDER unknown" (covers both a
    genuinely fresh lock -- the benign, brief gap between `mkdir` and the
    metadata write -- and the case where even the directory's own mtime can't
    be read)."""
    try:
        dir_mtime = os.stat(os.path.dirname(lock_meta_path)).st_mtime
    except OSError:
        return "CONTENDED_UNKNOWN_HOLDER unknown"
    age_seconds = datetime.now(timezone.utc).timestamp() - dir_mtime
    if age_seconds > STALE_AGE_SECONDS:
        return "STALE_INACTIVE unknown"
    return "CONTENDED_UNKNOWN_HOLDER unknown"


def decide(lock_meta_path, project_root, current_workflow_uuid=None):
    try:
        with open(lock_meta_path) as f:
            raw = f.read()
    except FileNotFoundError:
        # Benign race: mkdir succeeded but the holder hasn't written
        # metadata.json yet -- OR a real crash between mkdir and the printf
        # write, in which case metadata.json will never appear. Fall back to
        # the same dir-mtime-based age check the corrupt-JSON branch below
        # uses, so a genuinely stale, holder-less lock dir can still
        # self-heal via the age check instead of being permanently stuck. A
        # FRESH lock dir (the normal, brief mkdir->printf gap) is younger
        # than STALE_AGE_SECONDS and correctly stays CONTENDED_UNKNOWN_HOLDER,
        # not reclaimed.
        return _stale_by_dir_mtime(lock_meta_path)
    except (PermissionError, OSError) as exc:
        # A real filesystem/permission problem, distinct from ordinary
        # contention -- must not be reported to the user as "another workflow
        # holds the lock" when the real cause has nothing to do with any other
        # workflow.
        return f"LOCK_READ_ERROR {exc.__class__.__name__}"

    try:
        meta = json.loads(raw)
        if not isinstance(meta, dict):
            raise ValueError("metadata.json did not parse to a JSON object")
    except Exception:
        # Corrupt/truncated/malformed metadata.json (e.g. a crash mid-
        # `printf`-write) must not become a permanent, un-reclaimable lock.
        # There is no holder identity to recover from unparseable content --
        # same shared age-fallback as the "metadata.json missing entirely"
        # case above.
        return _stale_by_dir_mtime(lock_meta_path)

    holder_wf = meta.get("workflow_uuid", "unknown")
    holder_worktree_raw = meta.get("worktree_path", "") or ""
    holder_worktree = os.path.realpath(holder_worktree_raw) if holder_worktree_raw else ""
    acquired_at = meta.get("acquired_at", "")

    is_self = bool(current_workflow_uuid and holder_wf == current_workflow_uuid)

    live_paths = None
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # `git` not on PATH, or another failure to even launch the
        # subprocess -- a local environment problem, not evidence of another
        # live workflow. Must not propagate as an unhandled exception (this
        # script's own documented contract is to print exactly one line to
        # stdout) and must not silently masquerade as ordinary contention.
        return f"GIT_WORKTREE_LIST_ERROR {exc.__class__.__name__}"
    if result.returncode != 0:
        # The subprocess launched fine but the git command itself failed
        # (corrupt .git, project_root not a git repo, permission error,
        # index-lock contention, etc.) -- a *run* failure, distinct from the
        # *launch* failure handled above. Must not silently leave
        # `live_paths` as None and fall through to the ordinary
        # worktree-undetermined path (which could wrongly report plain
        # CONTENDED, or even auto-reclaim via STALE_INACTIVE, based on a
        # worktree-liveness check that never actually ran).
        stderr_summary = result.stderr.strip().replace("\n", " ") if result.stderr else ""
        return f"GIT_WORKTREE_LIST_ERROR NonZeroExit(rc={result.returncode}): {stderr_summary}"
    live_paths = {
        os.path.realpath(line.split(" ", 1)[1])
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    }

    worktree_confirmed_gone = bool(
        holder_worktree and live_paths is not None and holder_worktree not in live_paths
    )

    if is_self and worktree_confirmed_gone:
        # This workflow is resuming after crashing while it itself held the
        # lock, AND the lock's recorded worktree is positively confirmed gone
        # -- real proof the prior run holding this workflow_uuid is dead, not
        # just an identity match. Reclaim immediately, skipping the
        # age/inactivity window (there is nothing left to wait for once the
        # worktree that would prove the prior run still alive doesn't exist).
        #
        # A same-workflow_uuid resume whose worktree STILL exists gets NO
        # shortcut here -- it falls through to the exact same
        # STALE_WORKTREE_GONE / STALE_INACTIVE / CONTENDED reasoning a
        # stranger's lock would get, below. Two concurrent processes sharing
        # one workflow_uuid must never be able to steal the lock from each
        # other on identity alone -- that would let both run git merge /
        # git worktree remove / git branch -d concurrently, exactly the
        # corruption this lock exists to prevent.
        return f"SELF_RECLAIM {holder_wf}"

    if worktree_confirmed_gone:
        return f"STALE_WORKTREE_GONE {holder_wf}"

    acquired_dt = parse_iso(acquired_at)
    if acquired_dt and (datetime.now(timezone.utc) - acquired_dt).total_seconds() > STALE_AGE_SECONDS:
        artifact_path = os.path.join(project_root, ".craftflow", "state", "workflows", f"{holder_wf}.json")
        recent = False
        try:
            with open(artifact_path) as f:
                art = json.load(f)
            updated_dt = parse_iso(art.get("updated_at", ""))
            if updated_dt and (datetime.now(timezone.utc) - updated_dt).total_seconds() < RECENT_ACTIVITY_SECONDS:
                recent = True
        except Exception:
            recent = False
        if not recent:
            return f"STALE_INACTIVE {holder_wf}"

    # Falls through here for a same-workflow_uuid resume whose worktree still
    # exists and shows no other staleness signal -- it waits/gates exactly
    # like a stranger's lock would; identity alone is never proof of death.
    return f"CONTENDED {holder_wf}"


def main():
    if len(sys.argv) < 3:
        print(
            "usage: craftflow_worktree_lock_staleness.py <lock_metadata_path> <project_root> [current_workflow_uuid]",
            file=sys.stderr,
        )
        return 2
    lock_meta_path = sys.argv[1]
    project_root = sys.argv[2]
    current_workflow_uuid = sys.argv[3] if len(sys.argv) > 3 else None
    print(decide(lock_meta_path, project_root, current_workflow_uuid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
