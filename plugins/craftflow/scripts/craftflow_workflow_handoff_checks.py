#!/usr/bin/env python3
"""Pure checks and the exclusive claim used by craftflow_workflow_handoff.py (H5).

  parent_host / is_finalized / accepted_event / current_accept / declined_blocks   facts read from the parent log
  validate_flags / resolve_input                                                    input validation (exit 2 / exit 4)
  acquire_claim / mark_claim / release_claim                                        `{parent}.handoff.lock`, an O_EXCL file
                                                                                    that is kept after a mint as the
                                                                                    durable "handed off" marker
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

WRITE_TARGETS = ("BUILD", "DEBUG")
INPUT_KINDS = ("design", "brief", "bug-context")
SOURCE_ROUTES = ("TRIAGE", "CODEBASE-HEALTH", "QA", "DEBUG")  # also the parent types that may hand off
STALE_SECONDS = 120
Failure = Tuple[int, str]
Events = List[Dict[str, Any]]


def parent_host(events: Events) -> str:
    """`cursor` when the parent's first `workflow_started` says so, else `claude`; the --host flag never decides this."""
    started = next((e for e in events if e.get("event") == "workflow_started"), {})
    return "cursor" if started.get("host") == "cursor" else "claude"


def is_finalized(events: Events) -> bool:
    """A finalization event (`memory_finalized`; for a Cursor parent also `workflow_completed`, D-12) with no
    `workflow_started` after the last one: a restarted workflow is not finalized."""
    marks = ("memory_finalized", "workflow_completed") if parent_host(events) == "cursor" else ("memory_finalized",)
    last = max((i for i, e in enumerate(events) if e.get("event") in marks), default=-1)
    return last >= 0 and not any(e.get("event") == "workflow_started" for e in events[last + 1:])


def accepted_event(events: Events, event_id: Optional[str], target: str) -> bool:
    return bool(event_id) and any(e.get("event") == "handoff_accepted" and e.get("event_id") == event_id
                                  and e.get("target") == target for e in events)


def _last_decline(events: Events, target: str) -> int:
    """Log position of the last decline that covers `target`; a decline without a target covers the write targets."""
    return max((i for i, e in enumerate(events) if e.get("event") == "handoff_declined"
                and (e.get("target") == target or (not e.get("target") and target in WRITE_TARGETS))), default=-1)


def declined_blocks(events: Events, target: str) -> bool:
    """A decline for `target` with no later `handoff_accepted` for it (ordering by log position)."""
    last = _last_decline(events, target)
    return last >= 0 and not any(e.get("event") == "handoff_accepted" and e.get("target") == target for e in events[last + 1:])


def current_accept(events: Events, target: str) -> Optional[str]:
    """The id of a `handoff_accepted` for exactly this target that no later decline has withdrawn."""
    live = events[_last_decline(events, target) + 1:]
    return next((e.get("event_id") for e in live if e.get("event") == "handoff_accepted" and e.get("target") == target), None)


def validate_flags(input_kind: Optional[str], source_route: Optional[str]) -> Optional[Failure]:
    if input_kind is not None and input_kind not in INPUT_KINDS:
        return 2, f"invalid --input-kind {input_kind!r} (expected {'|'.join(INPUT_KINDS)})"
    if source_route is not None and source_route not in SOURCE_ROUTES:
        return 2, f"invalid --source-route {source_route!r} (expected {'|'.join(SOURCE_ROUTES)})"
    return None


def resolve_input(project: Path, value: str) -> Tuple[Optional[Failure], Optional[str]]:
    """(failure, None) or (None, path relative to the project). Only a regular file inside the resolved project, no symlink."""
    candidate = project / value
    if not os.path.lexists(str(candidate)):
        return (4, f"handoff input not found: {value}"), None
    problem = (4, "input must be a regular file inside the project")
    if candidate.is_symlink() or not candidate.is_file():
        return problem, None
    try:
        return None, candidate.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return problem, None


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_claim(lock: Path) -> Dict[str, Any]:
    try:
        value = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # missing, or caught between the O_EXCL create and the write
    return value if isinstance(value, dict) else {}


def _alive(pid: Any) -> bool:
    if not isinstance(pid, int):
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def acquire_claim(lock: Path, logged_child: Callable[[], Optional[str]]) -> Tuple[str, Optional[str]]:
    """`("acquired", None)` once this process created the claim exclusively, or `("already", child)` when another
    process finished the handoff. A held claim without a result is waited on; it is replaced when its process is gone or it
    is older than STALE_SECONDS and no child is linked. OSError other than "exists" propagates (the caller maps it to exit 5)."""
    while True:
        try:
            fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"pid": os.getpid(), "ts": _stamp()}))
            return "acquired", None
        held = _read_claim(lock)
        child = held.get("child") or logged_child()
        if child:
            return "already", str(child)
        try:
            age = time.time() - lock.stat().st_mtime
        except FileNotFoundError:
            continue  # released while we looked: try to take it
        if age > STALE_SECONDS or not _alive(held.get("pid")):
            lock.unlink(missing_ok=True)
            continue
        time.sleep(0.05)


def mark_claim(lock: Path, child: str) -> None:
    """Record the minted child in the claim (atomic replace), so a later call answers without the parent log line."""
    tmp = lock.with_name(lock.name + ".tmp")
    tmp.write_text(json.dumps({**_read_claim(lock), "child": child}), encoding="utf-8")
    os.replace(str(tmp), str(lock))


def release_claim(lock: Path) -> None:
    lock.unlink(missing_ok=True)


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value


def find_child(wf_dir: Path, parent: str) -> Optional[str]:
    """A child artifact whose `parent_workflow_uuid` is `parent` (covers a crash before the parent log line)."""
    for path in sorted(wf_dir.glob("wf-*.json")):
        try:
            if parent.encode() not in path.read_bytes():
                continue
            art = read_json(path)
        except (OSError, ValueError):
            continue
        if art.get("parent_workflow_uuid") == parent:
            return str(art.get("workflow_uuid") or path.stem)
    return None


def remove_child(wf_dir: Path, child: str) -> None:
    """Delete everything a mint of `child` may have written; an OSError that is not "already gone" is reported."""
    for path in (wf_dir / f"{child}.events.jsonl", wf_dir / f"{child}.json", wf_dir / f"{child}.json.tmp", wf_dir / child):
        try:
            path.rmdir() if path.name == child else path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            sys.stderr.write(f"Warning: could not remove {path.name}: {exc}\n")
