#!/usr/bin/env python3
"""Session-scoped + cross-session status cache for the Jev auto-detect
SessionStart hook (craftflow_jev_session_check.py), consumed by the
UserPromptSubmit hook (craftflow_jev_prompt_hint.py) so it never needs to
re-run a canary-equivalent check per prompt. See DD-2/DD-8,
docs/plans/2026-09-23-jev-auto-detect-plan.md.
"""
from __future__ import annotations

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
    precedent -- failures are not logged, same established convention).
    Atomic (temp file + os.replace()) and order-preserving: a write whose
    `checked_at` is older than what is already on disk is skipped, so a
    delayed/hung canary from an earlier SessionStart firing can never
    clobber a fresher result already written by a later firing (stale-write
    race). `checked_at` defaults to call-time but callers may pass an
    explicit `checked_at` kwarg to order by a different clock (e.g.
    check-start time instead of check-completion time)."""
    if not _is_valid_session_id(session_id):
        return
    try:
        path = session_cache_path(state_root, session_id)
        entry: Dict[str, Any] = {"session_id": session_id, "checked_at": time.time()}
        entry.update(fields)
        existing = _read_raw_entry(path)
        if (
            existing is not None
            and isinstance(existing.get("checked_at"), (int, float))
            and isinstance(entry.get("checked_at"), (int, float))
            and existing["checked_at"] > entry["checked_at"]
        ):
            return  # a fresher entry already landed -- do not clobber it
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
    """Best-effort, never raises. Same atomic + order-preserving contract as
    write_session_status (see there for rationale) applied to the
    cross-session last-status.json file."""
    try:
        path = last_status_path(state_root)
        entry: Dict[str, Any] = {
            "active": active,
            "reason": reason,
            "checked_at": checked_at if checked_at is not None else time.time(),
        }
        existing = _read_raw_entry(path)
        if (
            existing is not None
            and isinstance(existing.get("checked_at"), (int, float))
            and existing["checked_at"] > entry["checked_at"]
        ):
            return  # a fresher entry already landed -- do not clobber it
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
