#!/usr/bin/env python3
"""Tests for craftflow_jev_session_cache.py.

Run: python3 tests/fixtures/test_craftflow_jev_session_cache.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_session_cache import (  # noqa: E402
    session_cache_path,
    last_status_path,
    read_session_status,
    write_session_status,
    read_last_status,
    write_last_status,
    status_changed,
)

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def test_session_cache_path_is_stable_hash_of_session_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        p1 = session_cache_path(root, "abc-123")
        p2 = session_cache_path(root, "abc-123")
        p3 = session_cache_path(root, "different-session")
        if p1 == p2 and p1 != p3 and p1.parent == root / "jev" / "sessions":
            ok("session_cache_path is a stable hash of session_id under jev/sessions/")
        else:
            fail("session-cache-path-stable-hash", f"p1={p1!r} p2={p2!r} p3={p3!r}")


def test_write_then_read_session_status_roundtrips() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_session_status(root, "sess-a", active=True, reason=None)
        entry = read_session_status(root, "sess-a")
        if (
            entry is not None
            and entry.get("session_id") == "sess-a"
            and entry.get("active") is True
            and entry.get("reason") is None
            and "checked_at" in entry
        ):
            ok("write_session_status then read_session_status roundtrips")
        else:
            fail("write-then-read-roundtrips", f"entry={entry!r}")


def test_read_session_status_returns_none_on_missing_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        entry = read_session_status(root, "never-written")
        if entry is None:
            ok("read_session_status returns None on missing file")
        else:
            fail("read-none-on-missing-file", f"entry={entry!r}")


def test_read_session_status_returns_none_on_corrupt_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = session_cache_path(root, "sess-corrupt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        entry = read_session_status(root, "sess-corrupt")
        if entry is None:
            ok("read_session_status returns None on corrupt JSON")
        else:
            fail("read-none-on-corrupt-json", f"entry={entry!r}")


def test_read_session_status_returns_none_on_session_id_mismatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_session_status(root, "sess-a", active=True, reason=None)
        path = session_cache_path(root, "sess-a")
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry["session_id"] = "sess-b"
        path.write_text(json.dumps(entry), encoding="utf-8")
        result = read_session_status(root, "sess-a")
        if result is None:
            ok("read_session_status returns None on session_id mismatch (hash collision defense)")
        else:
            fail("read-none-on-session-id-mismatch", f"result={result!r}")


def test_status_changed_true_on_first_ever_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if status_changed(root, True, None) is True:
            ok("status_changed is True on first ever run (no last-status file)")
        else:
            fail("status-changed-true-on-first-run", "expected True")


def test_status_changed_false_when_identical_to_last() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_last_status(root, True, "ok")
        if status_changed(root, True, "ok") is False:
            ok("status_changed is False when identical to last")
        else:
            fail("status-changed-false-when-identical", "expected False")


def test_status_changed_true_when_active_flips() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_last_status(root, True, "ok")
        if status_changed(root, False, "ok") is True:
            ok("status_changed is True when active flips")
        else:
            fail("status-changed-true-when-active-flips", "expected True")


def test_status_changed_true_when_reason_changes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_last_status(root, True, "ok")
        if status_changed(root, True, "different-reason") is True:
            ok("status_changed is True when reason changes")
        else:
            fail("status-changed-true-when-reason-changes", "expected True")


def test_status_changed_true_on_corrupt_last_status_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = last_status_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        if status_changed(root, True, "ok") is True:
            ok("status_changed is True (fail toward notifying) on corrupt last-status file")
        else:
            fail("status-changed-true-on-corrupt-file", "expected True")


def test_write_last_status_then_read_roundtrips() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_last_status(root, True, "reason-x")
        data = read_last_status(root)
        if data == {"active": True, "reason": "reason-x"}:
            ok("write_last_status then read_last_status roundtrips")
        else:
            fail("write-last-status-roundtrips", f"data={data!r}")


def test_write_session_status_survives_unwritable_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        unwritable_root = Path(tmp) / "not-a-directory"
        unwritable_root.write_text("i am a file, not a directory", encoding="utf-8")
        try:
            write_session_status(unwritable_root, "sess-x", active=True, reason=None)
            ok("write_session_status survives an unwritable directory without raising")
        except Exception as exc:  # pragma: no cover - the failure path this test guards against
            fail("write-session-status-survives-unwritable-dir", f"raised {type(exc).__name__}: {exc}")


def main() -> int:
    print("test_craftflow_jev_session_cache: running")
    test_session_cache_path_is_stable_hash_of_session_id()
    test_write_then_read_session_status_roundtrips()
    test_read_session_status_returns_none_on_missing_file()
    test_read_session_status_returns_none_on_corrupt_json()
    test_read_session_status_returns_none_on_session_id_mismatch()
    test_status_changed_true_on_first_ever_run()
    test_status_changed_false_when_identical_to_last()
    test_status_changed_true_when_active_flips()
    test_status_changed_true_when_reason_changes()
    test_status_changed_true_on_corrupt_last_status_file()
    test_write_last_status_then_read_roundtrips()
    test_write_session_status_survives_unwritable_directory()

    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
