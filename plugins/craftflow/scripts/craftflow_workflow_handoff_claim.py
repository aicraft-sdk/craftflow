#!/usr/bin/env python3
"""The exclusive handoff claim of craftflow_workflow_handoff.py (H5): `{parent}.handoff.lock`, an O_EXCL file that is kept
after a mint as the durable "handed off" marker. A stale claim is taken over under an O_EXCL guard, so N waiters that judged
the same claim never all proceed. `short_claim` holds a lock for one read-modify-append and always releases it.
"""
from __future__ import annotations

import contextlib
import json
import os
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

from craftflow_workflow_handoff_checks import child_complete

STALE_SECONDS = 120  # a claim this old with no linked child is stale even when its pid answers (covers PermissionError and pid reuse)
GARBAGE_SECONDS = 5  # an unreadable, empty or pid-less claim is stale after this long (the create-then-write window is far shorter)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ClaimPathError(Exception):
    """The claim path holds something that is not a regular file; waiting would never end."""


def _read_claim(lock: Path) -> Tuple[str, Dict[str, Any]]:
    """(raw text, parsed object); `("", {})` when missing or unreadable, `(raw, {})` when not a JSON object."""
    try:
        raw = lock.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return "", {}  # missing, or caught between the O_EXCL create and the write
    try:
        value = json.loads(raw)
    except ValueError:
        return raw, {}
    return raw, value if isinstance(value, dict) else {}


def _valid_pid(pid: Any) -> bool:
    return isinstance(pid, int) and not isinstance(pid, bool) and pid > 0


def _alive(pid: int) -> bool:
    """False only when no such process exists; PermissionError means it exists (the STALE_SECONDS age rule still ends it)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _is_stale(held: Dict[str, Any], age: float) -> bool:
    pid = held.get("pid")
    if not _valid_pid(pid):
        return age > GARBAGE_SECONDS
    return age > STALE_SECONDS or not _alive(pid)


def _take_over(lock: Path, judged: str) -> bool:
    """Remove a claim judged stale, serialized through the `{lock}.takeover` guard (O_EXCL, held for microseconds): of N
    waiters that judged the same claim only the guard holder looks again, and it removes the claim only when it still holds
    the judged content (an empty one only when also older than GARBAGE_SECONDS). A plain rename-and-compare cannot do this:
    the process that renamed a replacement claim must put it back, and in that gap a third process creates its own claim
    (measured: 2 of 10 barrier-released rounds minted twice). A guard older than GARBAGE_SECONDS belongs to a dead holder."""
    guard = lock.with_name(lock.name + ".takeover")
    try:
        os.close(os.open(str(guard), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
    except FileExistsError:
        try:
            if time.time() - os.lstat(str(guard)).st_mtime > GARBAGE_SECONDS:
                guard.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        return False
    try:
        try:
            if _read_claim(lock)[0] != judged or (judged == "" and time.time() - os.lstat(str(lock)).st_mtime <= GARBAGE_SECONDS):
                return False
        except FileNotFoundError:
            return True  # already gone: nothing to remove, and the O_EXCL create arbitrates
        lock.unlink(missing_ok=True)
        return True
    finally:
        guard.unlink(missing_ok=True)


def acquire_claim(lock: Path, logged_child: Callable[[], Optional[str]]) -> Tuple[str, Optional[str]]:
    """`("acquired", None)` once this process created the claim exclusively, or `("already", child)` when another
    process finished the handoff (the claim's child must have a complete artifact, else it is ignored). A held claim without
    a result is waited on; it is taken over (see `_take_over`) when its process is gone, it is older than STALE_SECONDS, it
    is unreadable or pid-less for more than GARBAGE_SECONDS, or it names a child that is not complete. OSError other than
    "exists" propagates (the caller maps it to exit 5); a non-regular file at the path raises ClaimPathError at once."""
    while True:
        try:
            fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"pid": os.getpid(), "ts": _stamp()}))
            return "acquired", None
        try:
            seen = os.lstat(str(lock))
        except FileNotFoundError:
            continue  # released while we looked: try to take it
        if not stat.S_ISREG(seen.st_mode):
            raise ClaimPathError(f"handoff claim path is not a regular file: {lock}")
        raw, held = _read_claim(lock)
        claimed = held.get("child")
        if isinstance(claimed, str) and child_complete(lock.parent, claimed):
            return "already", claimed
        logged = logged_child()
        if logged:
            return "already", str(logged)
        if claimed or _is_stale(held, time.time() - seen.st_mtime):
            _take_over(lock, raw)
            continue
        time.sleep(0.05)


def mark_claim(lock: Path, child: str) -> None:
    """Record the minted child in the claim (atomic replace), so a later call answers without the parent log line."""
    tmp = lock.with_name(f"{lock.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(json.dumps({**_read_claim(lock)[1], "child": child}), encoding="utf-8")
        os.replace(str(tmp), str(lock))
    finally:
        tmp.unlink(missing_ok=True)


def release_claim(lock: Path) -> None:
    lock.unlink(missing_ok=True)


@contextlib.contextmanager
def short_claim(lock: Path) -> Iterator[None]:
    """Hold `lock` (the same O_EXCL claim, taken for a moment and always released) around a read-modify-append."""
    acquire_claim(lock, lambda: None)
    try:
        yield
    finally:
        release_claim(lock)
