#!/usr/bin/env python3
"""Tests for craftflow_jev_session_cache.py.

Run: python3 tests/fixtures/test_craftflow_jev_session_cache.py
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import craftflow_jev_session_cache  # noqa: E402
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
    # NOTE: checked_at values are near-now real-clock offsets (not
    # arbitrary small numbers like 100.0/200.0) so they stay within
    # read_session_status()'s _SESSION_CACHE_MAX_AGE_SECONDS bound -- this
    # test verifies write-ordering, not staleness rejection.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        now = time.time()
        write_session_status(root, "sess-a", active=False, reason="timeout", checked_at=now)
        write_session_status(root, "sess-a", active=True, reason=None, checked_at=now - 100)  # stale, arrives late
        entry = read_session_status(root, "sess-a")
        if (
            entry is not None
            and entry.get("active") is False
            and entry.get("reason") == "timeout"
            and entry.get("checked_at") == now
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


def test_read_session_status_returns_none_for_entry_past_max_age() -> None:
    # MEDIUM (silent-failure-hunter, defense-in-depth): no TTL/staleness
    # bound existed on session cache reads. Reject entries whose checked_at
    # is older than a generous bound (the session's own expected lifetime)
    # instead of trusting an arbitrarily old cached decision forever.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stale_checked_at = time.time() - craftflow_jev_session_cache._SESSION_CACHE_MAX_AGE_SECONDS - 1
        write_session_status(root, "sess-stale", active=True, checked_at=stale_checked_at)
        result = read_session_status(root, "sess-stale")
        if result is None:
            ok("read_session_status returns None for an entry past the max-age bound")
        else:
            fail("read-none-for-entry-past-max-age", f"result={result!r}")


def test_read_session_status_returns_entry_within_max_age() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_session_status(root, "sess-fresh", active=True)
        result = read_session_status(root, "sess-fresh")
        if result is not None and result.get("active") is True:
            ok("read_session_status still returns a freshly-written entry (within max age)")
        else:
            fail("read-entry-within-max-age", f"result={result!r}")


def test_write_session_status_survives_unwritable_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        unwritable_root = Path(tmp) / "not-a-directory"
        unwritable_root.write_text("i am a file, not a directory", encoding="utf-8")
        try:
            write_session_status(unwritable_root, "sess-x", active=True, reason=None)
            ok("write_session_status survives an unwritable directory without raising")
        except Exception as exc:  # pragma: no cover - the failure path this test guards against
            fail("write-session-status-survives-unwritable-dir", f"raised {type(exc).__name__}: {exc}")


def test_write_session_status_concurrent_writers_do_not_race() -> None:
    # REM-FIX cycle 2 (re-hunter repro on commit f9e4ff2): the read-decide-
    # write sequence as a WHOLE was not serialized -- only the final
    # os.replace() write step was atomic. Two concurrent writers could each
    # read the same "existing" state before either wrote, both decide
    # "I'm not stale," and both write -- last PHYSICAL write wins, not
    # last-checked_at-wins. This test forces that exact interleaving
    # deterministically: the "older-writer" thread's disk read is captured
    # first (finding nothing), then it is made to sleep -- simulating a
    # hung/delayed canary -- before it writes its (older) checked_at. Without
    # locking the full critical section, the "newer-writer" thread races in
    # during that sleep window, reads the same "nothing" state, and writes
    # its (newer) checked_at first -- which the older-writer then clobbers
    # when it wakes up and writes, based on its stale read. If the full
    # read-decide-write section is correctly serialized per target file,
    # the newer-writer instead blocks until the older-writer's entire
    # critical section (including its write) completes, then reads the
    # older-writer's real write, compares checked_at correctly, and the
    # newer checked_at always wins -- regardless of thread start order or
    # timing.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        original_read = craftflow_jev_session_cache._read_raw_entry

        def instrumented_read(p):
            result = original_read(p)
            if threading.current_thread().name == "older-writer":
                time.sleep(0.2)  # simulate a hung/delayed canary mid-critical-section
            return result

        craftflow_jev_session_cache._read_raw_entry = instrumented_read
        # Near-now real-clock offsets (not arbitrary small numbers like
        # 100.0/200.0) so the resulting entry stays within
        # read_session_status()'s _SESSION_CACHE_MAX_AGE_SECONDS bound --
        # this test verifies write-ordering under concurrency, not
        # staleness rejection.
        now = time.time()
        try:
            t_old = threading.Thread(
                name="older-writer",
                target=write_session_status,
                args=(root, "sess-race"),
                kwargs={"active": False, "reason": "stale", "checked_at": now - 100},
            )
            t_old.start()
            time.sleep(0.05)  # let older-writer read first, before it stalls
            t_new = threading.Thread(
                name="newer-writer",
                target=write_session_status,
                args=(root, "sess-race"),
                kwargs={"active": True, "reason": "fresh", "checked_at": now},
            )
            t_new.start()
            t_old.join(timeout=5)
            t_new.join(timeout=5)
        finally:
            craftflow_jev_session_cache._read_raw_entry = original_read

        entry = read_session_status(root, "sess-race")
        if (
            entry is not None
            and entry.get("checked_at") == now
            and entry.get("reason") == "fresh"
        ):
            ok("write_session_status: concurrent writers do not race -- newer checked_at always wins")
        else:
            fail("write-session-status-concurrent-writers-do-not-race", f"entry={entry!r}")


def test_write_last_status_concurrent_writers_do_not_race() -> None:
    # Same interleaving repro as above, applied to write_last_status's
    # cross-session last-status.json file (identical read-decide-write
    # shape, identical bug, identical fix).
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        original_read = craftflow_jev_session_cache._read_raw_entry

        def instrumented_read(p):
            result = original_read(p)
            if threading.current_thread().name == "older-writer":
                time.sleep(0.2)
            return result

        craftflow_jev_session_cache._read_raw_entry = instrumented_read
        try:
            t_old = threading.Thread(
                name="older-writer",
                target=write_last_status,
                args=(root, False, "stale"),
                kwargs={"checked_at": 100.0},
            )
            t_old.start()
            time.sleep(0.05)
            t_new = threading.Thread(
                name="newer-writer",
                target=write_last_status,
                args=(root, True, "fresh"),
                kwargs={"checked_at": 200.0},
            )
            t_new.start()
            t_old.join(timeout=5)
            t_new.join(timeout=5)
        finally:
            craftflow_jev_session_cache._read_raw_entry = original_read

        data = read_last_status(root)
        if data.get("checked_at") == 200.0 and data.get("reason") == "fresh":
            ok("write_last_status: concurrent writers do not race -- newer checked_at always wins")
        else:
            fail("write-last-status-concurrent-writers-do-not-race", f"data={data!r}")


def test_write_session_status_skips_when_lock_held_past_deadline() -> None:
    # REM-FIX cycle 3 (doubt-verifier REFUTED on commit 813434d): the old
    # _file_lock() did a blocking fcntl.flock(fd, LOCK_EX) with no timeout.
    # A stuck-but-alive holder (e.g. D-state/uninterruptible I/O on a slow
    # or unsynced iCloud/NFS path -- a risk craftflow_hook_selfcheck.py's
    # discover_sibling_scripts already names and bounds for SessionStart
    # hooks specifically) would hold the lock forever, hanging every future
    # writer indefinitely. _file_lock now polls LOCK_EX|LOCK_NB against a
    # bounded deadline and gives up instead of blocking forever. This test
    # overrides the module-level deadline to a short value (dependency
    # injection via module-attribute patch, matching this file's existing
    # _read_raw_entry monkeypatch style) so the suite stays fast -- it must
    # NOT sleep for the full production deadline.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = session_cache_path(root, "sess-locked")
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        holder_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(holder_fd, fcntl.LOCK_EX)  # hold the lock for the whole test

        original_timeout = craftflow_jev_session_cache._LOCK_ACQUIRE_TIMEOUT_SECONDS
        craftflow_jev_session_cache._LOCK_ACQUIRE_TIMEOUT_SECONDS = 0.2
        try:
            start = time.monotonic()
            write_session_status(root, "sess-locked", active=True, reason=None)
            elapsed = time.monotonic() - start
        finally:
            craftflow_jev_session_cache._LOCK_ACQUIRE_TIMEOUT_SECONDS = original_timeout
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

        entry = read_session_status(root, "sess-locked")
        if entry is None and elapsed < 2.0:
            ok("write_session_status skips (not hangs) when the lock is held past the deadline")
        else:
            fail(
                "write-session-status-skips-when-lock-held",
                f"entry={entry!r} elapsed={elapsed!r}",
            )


def test_write_last_status_skips_when_lock_held_past_deadline() -> None:
    # Same bounded-deadline contract as above, applied to write_last_status's
    # PROJECT-GLOBAL lock file (last-status.json.lock) -- the higher-blast-
    # radius case, since every session's write_last_status call shares this
    # one lock file, not a per-session one.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = last_status_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        holder_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(holder_fd, fcntl.LOCK_EX)

        original_timeout = craftflow_jev_session_cache._LOCK_ACQUIRE_TIMEOUT_SECONDS
        craftflow_jev_session_cache._LOCK_ACQUIRE_TIMEOUT_SECONDS = 0.2
        try:
            start = time.monotonic()
            write_last_status(root, True, "reason-x")
            elapsed = time.monotonic() - start
        finally:
            craftflow_jev_session_cache._LOCK_ACQUIRE_TIMEOUT_SECONDS = original_timeout
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

        data = read_last_status(root)
        if data == {} and elapsed < 2.0:
            ok("write_last_status skips (not hangs) when the lock is held past the deadline")
        else:
            fail(
                "write-last-status-skips-when-lock-held",
                f"data={data!r} elapsed={elapsed!r}",
            )


def test_write_session_status_merges_with_previously_persisted_fields() -> None:
    # REM-FIX (MEDIUM, silent-failure-hunter on commit 34493d9): the old
    # write_session_status did a full-dict overwrite
    # (`entry = {"session_id":..., "checked_at":...}; entry.update(fields)`),
    # not a merge with what was already on disk. Currently inert because the
    # ask/canary branches are mutually exclusive per invocation today, but a
    # field written by one call (e.g. already_asked_consent from the consent-
    # ask branch) must survive a LATER call for the SAME session_id that only
    # passes a different set of fields (e.g. the canary branch's active/
    # reason), the moment a future caller needs both to coexist.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_session_status(root, "sess-merge", already_asked_consent=True)
        write_session_status(root, "sess-merge", active=True, reason=None)
        entry = read_session_status(root, "sess-merge")
        if (
            entry is not None
            and entry.get("already_asked_consent") is True
            and entry.get("active") is True
            and entry.get("reason") is None
        ):
            ok("write_session_status merges new fields with previously persisted ones (not overwrite)")
        else:
            fail("write-session-status-merges-with-previous-fields", f"entry={entry!r}")


def test_write_last_status_preserves_unknown_existing_fields_across_calls() -> None:
    # Same full-dict-overwrite risk, applied to write_last_status. It does
    # not accept arbitrary **fields today (only active/reason/checked_at),
    # so this proves the merge directly against a hand-written extra field
    # already on disk, matching the "preserve other keys" convention
    # craftflow_jev_setup._write_enabled_flag() already uses.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = last_status_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"active": True, "reason": None, "checked_at": 100.0, "future_field": "keep-me"}),
            encoding="utf-8",
        )
        write_last_status(root, False, "canary_failed", checked_at=200.0)
        data = read_last_status(root)
        if (
            data.get("future_field") == "keep-me"
            and data.get("active") is False
            and data.get("reason") == "canary_failed"
            and data.get("checked_at") == 200.0
        ):
            ok("write_last_status preserves unknown previously-persisted fields across calls")
        else:
            fail("write-last-status-preserves-unknown-fields", f"data={data!r}")


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
    test_read_session_status_returns_none_for_entry_past_max_age()
    test_read_session_status_returns_entry_within_max_age()
    test_read_session_status_returns_none_for_non_string_session_id()
    test_write_session_status_skips_stale_write_with_older_checked_at()
    test_write_last_status_skips_stale_write_with_older_checked_at()
    test_status_changed_two_different_sessions_same_status_no_spurious_change()
    test_write_session_status_noop_for_empty_or_none_session_id()
    test_read_session_status_returns_none_for_empty_session_id()
    test_write_session_status_survives_unwritable_directory()
    test_write_session_status_concurrent_writers_do_not_race()
    test_write_last_status_concurrent_writers_do_not_race()
    test_write_session_status_skips_when_lock_held_past_deadline()
    test_write_last_status_skips_when_lock_held_past_deadline()
    test_write_session_status_merges_with_previously_persisted_fields()
    test_write_last_status_preserves_unknown_existing_fields_across_calls()

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
