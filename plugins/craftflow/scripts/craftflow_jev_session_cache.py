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
import time
from pathlib import Path
from typing import Any, Dict, Optional


def session_cache_path(state_root: Path, session_id: str) -> Path:
    digest = hashlib.sha256((session_id or "").encode("utf-8")).hexdigest()[:16]
    return state_root / "jev" / "sessions" / f"{digest}.json"


def last_status_path(state_root: Path) -> Path:
    return state_root / "jev" / "last-status.json"


def read_session_status(state_root: Path, session_id: str) -> Optional[Dict[str, Any]]:
    path = session_cache_path(state_root, session_id)
    try:
        if not path.exists():
            return None
        entry = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entry, dict) or entry.get("session_id") != session_id:
            return None
        return entry
    except Exception:
        return None


def write_session_status(state_root: Path, session_id: str, **fields: Any) -> None:
    try:
        path = session_cache_path(state_root, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"session_id": session_id, "checked_at": time.time()}
        entry.update(fields)
        path.write_text(json.dumps(entry, ensure_ascii=True), encoding="utf-8")
    except Exception:
        pass  # best-effort cache write, matches craftflow_jev_client._write_cache()


def read_last_status(state_root: Path) -> Dict[str, Any]:
    try:
        data = json.loads(last_status_path(state_root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_last_status(state_root: Path, active: bool, reason: Optional[str]) -> None:
    try:
        path = last_status_path(state_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"active": active, "reason": reason}, ensure_ascii=True), encoding="utf-8")
    except Exception:
        pass


def status_changed(state_root: Path, active: bool, reason: Optional[str]) -> bool:
    """Never raises. Fails TOWARD notifying (True) on a corrupt/missing prior
    status -- the opposite bias from fail-open -- because this only gates an
    advisory message, never a security decision (DD-8)."""
    try:
        last = read_last_status(state_root)
        if not last:
            return True
        return last.get("active") != active or last.get("reason") != reason
    except Exception:
        return True
