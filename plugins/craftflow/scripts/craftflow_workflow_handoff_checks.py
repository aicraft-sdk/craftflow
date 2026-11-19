#!/usr/bin/env python3
"""Pure checks, child bookkeeping and the event log used by craftflow_workflow_handoff.py (H5); the claim is in
craftflow_workflow_handoff_claim.py.

  parent_host / is_finalized / accepted_event / current_accept / declined_blocks   facts read from the parent log
  validate_flags / resolve_input                                                    input validation (exit 2 / exit 4)
  scan_children / child_complete / remove_child                                     children of a parent; only a complete one
                                                                                    (handoff block + handoff_started) is adopted
  read_events / append_event / logged_child                                         the parent event log
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WRITE_TARGETS = ("BUILD", "DEBUG")
INPUT_KINDS = ("design", "brief", "bug-context")
SOURCE_ROUTES = ("TRIAGE", "CODEBASE-HEALTH", "QA", "DEBUG")  # also the parent types that may hand off
WF_RE = re.compile(r"wf-[A-Za-z0-9-]+")
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


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value


def child_complete(wf_dir: Path, child: str) -> bool:
    """A mint finished for `child`: its artifact has a `handoff` block with a `mode` and its log has `handoff_started`."""
    if not WF_RE.fullmatch(child):
        return False
    try:
        handoff = read_json(wf_dir / f"{child}.json").get("handoff")
        lines = (wf_dir / f"{child}.events.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return False
    if not (isinstance(handoff, dict) and handoff.get("mode")):
        return False
    for line in lines:
        try:
            if json.loads(line).get("event") == "handoff_started":
                return True
        except (ValueError, AttributeError):
            continue
    return False


def scan_children(wf_dir: Path, parent: str) -> Tuple[Optional[str], List[str]]:
    """(a complete child of `parent` or None, the ids of its incomplete orphans); covers a crash before the parent log line."""
    done, orphans = None, []
    for path in sorted(wf_dir.glob("wf-*.json")):
        try:
            if parent.encode() not in path.read_bytes():
                continue
            art = read_json(path)
        except (OSError, ValueError):
            continue
        if art.get("parent_workflow_uuid") != parent:
            continue
        if child_complete(wf_dir, path.stem):
            done = done or str(art.get("workflow_uuid") or path.stem)
        else:
            orphans.append(path.stem)
    return done, orphans


def find_child(wf_dir: Path, parent: str) -> Optional[str]:
    return scan_children(wf_dir, parent)[0]


def remove_child(wf_dir: Path, child: str) -> None:
    """Delete everything a mint of `child` may have written; an OSError that is not "already gone" is reported."""
    for path in (wf_dir / f"{child}.events.jsonl", wf_dir / f"{child}.json", *wf_dir.glob(f"{child}.json.tmp*"), wf_dir / child):
        try:
            path.rmdir() if path.name == child else path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            sys.stderr.write(f"Warning: could not remove {path.name}: {exc}\n")


def read_events(path: Path, warn: bool = True) -> Events:
    events, skipped = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            value = None
        if isinstance(value, dict):
            events.append(value)
        else:
            skipped += 1
    if skipped and warn:
        sys.stderr.write(f"Warning: skipped {skipped} malformed line(s) in {path.name}\n")
    return events


def append_event(path: Path, wf: str, event: str, **fields: Any) -> Dict[str, Any]:
    record = {"ts": _stamp(), "wf": wf, "event": event, "agent": "router", **fields}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return record


def logged_child(log: Path) -> Optional[str]:
    """The child named by a `handoff_started` line of the parent log (written only after the child is complete)."""
    try:
        events = read_events(log, warn=False)
    except OSError:
        return None
    return next((e["child_workflow_uuid"] for e in events if e.get("event") == "handoff_started" and e.get("child_workflow_uuid")), None)
