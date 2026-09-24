#!/usr/bin/env python3
"""Session-scoped + cross-session status cache for the Jev auto-detect
SessionStart hook (craftflow_jev_session_check.py), consumed by the
UserPromptSubmit hook (craftflow_jev_prompt_hint.py) so it never needs to
re-run a canary-equivalent check per prompt. See DD-2/DD-8,
docs/plans/2026-09-23-jev-auto-detect-plan.md.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


def _is_valid_session_id(session_id: Any) -> bool:
    """Only a non-empty string is a usable session identifier. Guards two
    failure modes at once: (1) a non-string session_id (int/list/None)
    would otherwise raise inside session_cache_path()'s .encode() call;
    (2) None and "" previously collided into the SAME hash bucket via
    `(session_id or "").encode(...)`, silently sharing cache state across
    callers that never supplied a real session_id."""
    return isinstance(session_id, str) and bool(session_id)


def session_cache_path(state_root: Path, session_id: str) -> Path:
    digest = hashlib.sha256((session_id or "").encode("utf-8")).hexdigest()[:16]
    return state_root / "jev" / "sessions" / f"{digest}.json"


def last_status_path(state_root: Path) -> Path:
    return state_root / "jev" / "last-status.json"


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write `data` to `path` atomically: temp file in the same directory,
    then os.replace() into place, so a concurrent reader never observes a
    partially-written file. Mirrors the codebase's established atomic-write
    convention (craftflow_skill_ledger.save_ledger_atomic). Raises on
    failure -- callers needing best-effort semantics wrap this themselves
    (matches craftflow_jev_client._write_cache's precedent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".jev-cache-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=True)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


# Bounds _file_lock's exclusive-acquire wait. REM-FIX cycle 3 (doubt-verifier
# REFUTED on commit 813434d): a blocking fcntl.flock(LOCK_EX) has no ceiling
# on its own -- a stuck-but-alive holder (e.g. D-state/uninterruptible I/O on
# a slow or unsynced iCloud/NFS path, a risk craftflow_hook_selfcheck.py's
# discover_sibling_scripts already names and bounds for SessionStart hooks
# specifically) would hold the lock forever, hanging every future writer
# indefinitely. For write_last_status this is a SINGLE, PROJECT-GLOBAL lock
# file (see write_last_status's docstring), so a hang there blocks every
# session's writes, not just the stuck one -- a liveness regression worse
# than the read/write race it replaced. 2.0s mirrors this feature's own
# established SessionStart-adjacent budget precedent (DD-3's
# SESSION_CHECK_TOTAL_BUDGET_SECONDS = 2.0 in
# docs/plans/2026-09-23-jev-auto-detect-plan.md): these cache writes are a
# low-frequency, normally-sub-millisecond operation, so 2.0s is generous
# for the legitimate case while still bounding the pathological one. A
# module-level constant (read at call time, not bound into a default
# parameter value) so tests can override it via a module-attribute patch,
# matching this file's existing test-file convention of monkeypatching
# `_read_raw_entry` for deterministic interleaving.
_LOCK_ACQUIRE_TIMEOUT_SECONDS = 2.0
# Poll interval between non-blocking acquire attempts while waiting out the
# deadline above. Small relative to the deadline so the worst-case
# over-wait is negligible, not so small that it busy-spins.
_LOCK_POLL_INTERVAL_SECONDS = 0.05


class _LockTimeoutError(Exception):
    """Raised by `_file_lock` when the exclusive lock could not be acquired
    within `_LOCK_ACQUIRE_TIMEOUT_SECONDS`. Never escapes to callers of
    `write_session_status`/`write_last_status`: both already wrap their
    `with _file_lock(...):` block in a broad `except Exception: pass`
    (best-effort, never-raises contract), so a lock-acquisition timeout is
    treated identically to any other best-effort write failure -- the write
    is silently skipped, not retried, not raised."""


@contextlib.contextmanager
def _file_lock(target_path: Path):
    """Exclusive advisory lock serializing the FULL read-decide-write
    critical section for `target_path` against concurrent writers (e.g. two
    SessionStart hooks racing). Without this, two writers can each read the
    same `existing` state before either writes, both decide "I'm not
    stale," and both write -- the physically-last write wins, not the
    write with the newest `checked_at` (REM-FIX cycle 2: only the final
    os.replace() step was atomic before this fix; the read-compare-write
    sequence as a whole was not). Mirrors
    craftflow_skill_ledger._ledger_file_lock's fcntl.flock() pattern
    (available on macOS/Linux via Python's stdlib `fcntl`, even though the
    `flock` shell command isn't). Lock file lives alongside the target as
    `<name>.lock` and is never cleaned up (cheap, reused across calls).

    REM-FIX cycle 3: acquisition is now a bounded poll-with-deadline loop
    (fcntl.flock(LOCK_EX | LOCK_NB) retried every
    `_LOCK_POLL_INTERVAL_SECONDS` up to `_LOCK_ACQUIRE_TIMEOUT_SECONDS`
    total), NOT a plain blocking `fcntl.flock(fd, LOCK_EX)`. On timeout,
    raises `_LockTimeoutError` instead of blocking forever -- the fd is
    still closed before the exception propagates (no partial state is
    left open), and no lock is released via LOCK_UN since none was ever
    acquired."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target_path.with_suffix(target_path.suffix + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    acquired = False
    deadline = time.monotonic() + _LOCK_ACQUIRE_TIMEOUT_SECONDS
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise _LockTimeoutError(
                        f"could not acquire lock on {lock_path} within "
                        f"{_LOCK_ACQUIRE_TIMEOUT_SECONDS}s"
                    )
                time.sleep(_LOCK_POLL_INTERVAL_SECONDS)
        yield
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_raw_entry(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def read_session_status(state_root: Path, session_id: str) -> Optional[Dict[str, Any]]:
    if not _is_valid_session_id(session_id):
        return None
    try:
        path = session_cache_path(state_root, session_id)
        if not path.exists():
            return None
        entry = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entry, dict) or entry.get("session_id") != session_id:
            return None
        return entry
    except Exception:
        return None


def write_session_status(state_root: Path, session_id: str, **fields: Any) -> None:
    """Best-effort, never raises (matches craftflow_jev_client._write_cache
    precedent -- failures are not logged, same established convention). The
    full read-decide-write section is locked per target file (see
    `_file_lock`), so two concurrent writers can never both observe the
    same stale `existing` state and race past each other -- the
    `checked_at` comparison below is authoritative under concurrency, not
    just advisory. Within that lock, the write is also atomic (temp file +
    os.replace()). A write whose `checked_at` is older than what is already
    on disk is skipped, so a delayed/hung canary from an earlier
    SessionStart firing can never clobber a fresher result already written
    by a later firing (stale-write race). `checked_at` defaults to
    call-time but callers may pass an explicit `checked_at` kwarg to order
    by a different clock (e.g. check-start time instead of
    check-completion time).

    MERGES with whatever entry is already on disk (read inside the same
    lock as the staleness check) rather than overwriting it wholesale --
    matches the "preserve other keys" convention
    craftflow_jev_setup._write_enabled_flag() already uses. Currently inert
    in practice (the consent-ask and canary branches are mutually exclusive
    per SessionStart invocation today), but without this a future caller
    needing both `already_asked_consent` and `active`/`reason` to coexist
    for the same session_id would silently drop whichever field the
    previous call wrote."""
    if not _is_valid_session_id(session_id):
        return
    try:
        path = session_cache_path(state_root, session_id)
        checked_at = fields.pop("checked_at", None)
        if checked_at is None:
            checked_at = time.time()
        base_fields: Dict[str, Any] = {"session_id": session_id, "checked_at": checked_at}
        with _file_lock(path):
            existing = _read_raw_entry(path)
            if (
                existing is not None
                and isinstance(existing.get("checked_at"), (int, float))
                and isinstance(checked_at, (int, float))
                and existing["checked_at"] > checked_at
            ):
                return  # a fresher entry already landed -- do not clobber it
            entry: Dict[str, Any] = {**(existing or {}), **base_fields, **fields}
            _atomic_write_json(path, entry)
    except Exception:
        pass  # best-effort cache write, matches craftflow_jev_client._write_cache()


def read_last_status(state_root: Path) -> Dict[str, Any]:
    try:
        data = json.loads(last_status_path(state_root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_last_status(
    state_root: Path,
    active: bool,
    reason: Optional[str],
    checked_at: Optional[float] = None,
) -> None:
    """Best-effort, never raises. Same per-file-locked, atomic,
    order-preserving contract as write_session_status (see there for
    rationale) applied to the cross-session last-status.json file: the full
    read-decide-write section is locked per target file, so two concurrent
    writers can never both observe the same stale `existing` state and race
    past each other.

    BLAST RADIUS (deliberately kept, not narrowed): unlike
    write_session_status's per-`session_id`-hashed lock file, this
    function's lock file (`last-status.json.lock`) is a SINGLE,
    PROJECT-GLOBAL file shared by every session's `write_last_status` call
    -- there is only ever one `last-status.json`, by DD-8's own design (see
    `status_changed`'s docstring: the cross-session comparison is the
    entire point, so it cannot be scoped per-session without defeating that
    purpose). This means a lock-acquisition timeout here (see
    `_LOCK_ACQUIRE_TIMEOUT_SECONDS`) has broader impact than
    write_session_status's: a stuck holder blocks EVERY session's
    write_last_status call project-wide, not just one session's cache, for
    up to the bounded deadline. That bound (not blocking forever) is what
    makes this an acceptable tradeoff rather than a per-session split: this
    is an advisory, best-effort, low-frequency write (never a security
    gate), so a bounded worst-case delay across all sessions is preferable
    to the added complexity of a differently-scoped lock file for one
    function only.

    MERGES with whatever entry is already on disk (read inside the same
    lock as the staleness check) rather than overwriting it wholesale --
    same rationale/convention as write_session_status above. This function
    does not itself accept arbitrary **fields today, but any field a future
    caller (or a hand-authored on-disk entry) adds to this file must not be
    silently dropped by an unrelated active/reason update."""
    try:
        path = last_status_path(state_root)
        resolved_checked_at = checked_at if checked_at is not None else time.time()
        base_fields: Dict[str, Any] = {
            "active": active,
            "reason": reason,
            "checked_at": resolved_checked_at,
        }
        with _file_lock(path):
            existing = _read_raw_entry(path)
            if (
                existing is not None
                and isinstance(existing.get("checked_at"), (int, float))
                and existing["checked_at"] > resolved_checked_at
            ):
                return  # a fresher entry already landed -- do not clobber it
            entry: Dict[str, Any] = {**(existing or {}), **base_fields}
            _atomic_write_json(path, entry)
    except Exception:
        pass


def status_changed(state_root: Path, active: bool, reason: Optional[str]) -> bool:
    """Never raises. Fails TOWARD notifying (True) on a corrupt/missing prior
    status -- the opposite bias from fail-open -- because this only gates an
    advisory message, never a security decision (DD-8).

    DD-8 deliberately chose a CROSS-session (not session-scoped) last-status
    file: its purpose is to stop a healthy, unchanged status from
    re-notifying on every newly opened session (a purely per-session
    comparison would make every brand-new session_id look like "first ever
    run" and always notify, defeating that purpose). The accepted tradeoff
    of a single shared value is that two concurrent sessions racing to
    observe genuinely DIFFERENT transient results can each see the other's
    write as a "change" -- mitigated (not eliminated) by write_last_status's
    checked_at-ordered, atomic writes above, which at least guarantee no
    write is silently lost."""
    try:
        last = read_last_status(state_root)
        if not last:
            return True
        return last.get("active") != active or last.get("reason") != reason
    except Exception:
        return True
