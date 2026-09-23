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
        if data.get("active") is True and data.get("reason") == "reason-x":
            ok("write_last_status then read_last_status roundtrips")
        else:
            fail("write-last-status-roundtrips", f"data={data!r}")


def test_read_session_status_returns_none_for_non_string_session_id() -> None:
    # REM-FIX (CRITICAL #1): session_cache_path()'s .encode() used to be
    # called OUTSIDE read_session_status's try block, so a non-string
    # session_id (int/list) would crash uncaught, breaking the module's
    # "never raises" contract on every subsequent prompt in the session.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result_int = read_session_status(root, 12345)  # type: ignore[arg-type]
        result_list = read_session_status(root, ["a", "b"])  # type: ignore[arg-type]
        if result_int is None and result_list is None:
            ok("read_session_status returns None (not raises) for non-string session_id")
        else:
            fail("read-none-for-non-string-session-id", f"int={result_int!r} list={result_list!r}")


def test_write_session_status_skips_stale_write_with_older_checked_at() -> None:
    # REM-FIX (CRITICAL #2): a delayed/hung canary from an earlier
    # SessionStart firing must never clobber a fresher result that already
    # landed. Compare-and-skip on checked_at (caller-overridable via
    # **fields) enforces last-newest-checked_at-wins ordering.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_session_status(root, "sess-a", active=False, reason="timeout", checked_at=200.0)
        write_session_status(root, "sess-a", active=True, reason=None, checked_at=100.0)  # stale, arrives late
        entry = read_session_status(root, "sess-a")
        if (
            entry is not None
            and entry.get("active") is False
            and entry.get("reason") == "timeout"
            and entry.get("checked_at") == 200.0
        ):
            ok("write_session_status skips a stale write with an older checked_at")
        else:
            fail("write-session-status-skips-stale-write", f"entry={entry!r}")


def test_write_last_status_skips_stale_write_with_older_checked_at() -> None:
    # REM-FIX (CRITICAL #4): same stale-write race, applied to the
    # cross-session last-status.json file.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_last_status(root, False, "timeout", checked_at=200.0)
        write_last_status(root, True, None, checked_at=100.0)  # stale, arrives late
        data = read_last_status(root)
        if data.get("active") is False and data.get("reason") == "timeout" and data.get("checked_at") == 200.0:
            ok("write_last_status skips a stale write with an older checked_at")
        else:
            fail("write-last-status-skips-stale-write", f"data={data!r}")


def test_status_changed_two_different_sessions_same_status_no_spurious_change() -> None:
    # REM-FIX (HIGH #3): DD-8 explicitly chose a CROSS-session (not
    # session-scoped) last-status.json so a healthy, unchanged status
    # doesn't re-notify on every newly opened session -- this is the
    # notify-storm-prevention behavior DD-8 exists for, and it is
    # incompatible with a purely per-session comparison (a per-session-only
    # fix would make every brand-new session_id look like "first ever run"
    # and re-notify, defeating DD-8's purpose). What IS fixable at this
    # layer: two different session_ids observing the SAME true status must
    # not spuriously report a change for either one individually.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_session_status(root, "sess-a", active=True, reason="ok")
        changed_a = status_changed(root, True, "ok")
        write_last_status(root, True, "ok")
        write_session_status(root, "sess-b", active=True, reason="ok")
        changed_b = status_changed(root, True, "ok")
        write_last_status(root, True, "ok")
        if changed_a is True and changed_b is False:
            ok("two different session_ids observing the same status: no spurious change for either")
        else:
            fail(
                "status-changed-two-sessions-no-spurious-change",
                f"changed_a={changed_a!r} changed_b={changed_b!r}",
            )


def test_write_session_status_noop_for_empty_or_none_session_id() -> None:
    # REM-FIX (MEDIUM #5): None and "" used to collide into the same hash
    # bucket via `(session_id or "").encode(...)`. Empty/missing session_id
    # must now disable the cache (no-op) instead of writing into a shared
    # bucket.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_session_status(root, "", active=True, reason=None)
        write_session_status(root, None, active=True, reason=None)  # type: ignore[arg-type]
        empty_bucket_path = session_cache_path(root, "")
        if not empty_bucket_path.exists():
            ok("write_session_status is a no-op for empty/None session_id (no shared bucket write)")
        else:
            fail("write-session-status-noop-empty-session-id", f"path exists: {empty_bucket_path}")


def test_read_session_status_returns_none_for_empty_session_id() -> None:
    # REM-FIX (MEDIUM #5): even if something else wrote into the empty-string
    # bucket (e.g. pre-fix data on disk), reading with an empty session_id
    # must now be treated as cache-disabled, not a hash-bucket hit.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = session_cache_path(root, "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"session_id": "", "active": True}), encoding="utf-8")
        result = read_session_status(root, "")
        if result is None:
            ok("read_session_status returns None for empty session_id (cache disabled)")
        else:
            fail("read-none-for-empty-session-id", f"result={result!r}")


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
    test_read_session_status_returns_none_for_non_string_session_id()
    test_write_session_status_skips_stale_write_with_older_checked_at()
    test_write_last_status_skips_stale_write_with_older_checked_at()
    test_status_changed_two_different_sessions_same_status_no_spurious_change()
    test_write_session_status_noop_for_empty_or_none_session_id()
    test_read_session_status_returns_none_for_empty_session_id()
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
