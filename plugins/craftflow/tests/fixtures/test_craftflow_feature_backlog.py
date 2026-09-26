#!/usr/bin/env python3
"""Tests for craftflow_feature_backlog.py.

Run: python3 tests/fixtures/test_craftflow_feature_backlog.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
SCRIPT = SCRIPTS / "craftflow_feature_backlog.py"

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def run_cli(args: list, state_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--state-dir", str(state_dir)],
        capture_output=True,
        text=True,
    )


def test_register_creates_not_started_entry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        result = run_cli(["--register", json.dumps({"id": "feat-a", "title": "Feature A", "plan_file": "docs/plans/x.md"})], state_dir)
        if result.returncode != 0:
            fail("register_creates_not_started_entry", f"exit {result.returncode}: {result.stderr}")
            return
        report = run_cli(["--report", "json"], state_dir)
        data = json.loads(report.stdout)
        entry = next((f for f in data["features"] if f["id"] == "feat-a"), None)
        if entry and entry["status"] == "not_started":
            ok("register_creates_not_started_entry")
        else:
            fail("register_creates_not_started_entry", f"got {data}")


def test_activate_then_complete_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--register", json.dumps({"id": "feat-b", "title": "Feature B"})], state_dir)
        act = run_cli(["--activate", "feat-b"], state_dir)
        if act.returncode != 0:
            fail("activate_then_complete_lifecycle", f"activate failed: {act.stderr}")
            return
        comp = run_cli(["--complete", "feat-b"], state_dir)
        if comp.returncode != 0:
            fail("activate_then_complete_lifecycle", f"complete failed: {comp.stderr}")
            return
        report = run_cli(["--report", "json"], state_dir)
        data = json.loads(report.stdout)
        entry = next((f for f in data["features"] if f["id"] == "feat-b"), None)
        if entry and entry["status"] == "passing":
            ok("activate_then_complete_lifecycle")
        else:
            fail("activate_then_complete_lifecycle", f"got {data}")


def test_vcr_zero_activated_is_explicit_na_not_silent_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--register", json.dumps({"id": "feat-c", "title": "Feature C"})], state_dir)
        report = run_cli(["--report", "json"], state_dir)
        data = json.loads(report.stdout)
        if data["vcr"]["activated"] == 0 and data["vcr"]["ratio"] is None and "N/A" in data["vcr"]["display"]:
            ok("vcr_zero_activated_is_explicit_na_not_silent_zero")
        else:
            fail("vcr_zero_activated_is_explicit_na_not_silent_zero", f"got {data['vcr']}")


def test_vcr_mixed_statuses_computes_correct_ratio() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--register", json.dumps({"id": "a"})], state_dir)
        run_cli(["--register", json.dumps({"id": "b"})], state_dir)
        run_cli(["--register", json.dumps({"id": "c"})], state_dir)
        run_cli(["--activate", "a"], state_dir)
        run_cli(["--activate", "b"], state_dir)
        run_cli(["--complete", "b"], state_dir)
        # c stays not_started -- must NOT count toward activated
        report = run_cli(["--report", "json"], state_dir)
        data = json.loads(report.stdout)
        if data["vcr"]["passing"] == 1 and data["vcr"]["activated"] == 2 and data["vcr"]["display"] == "1/2":
            ok("vcr_mixed_statuses_computes_correct_ratio")
        else:
            fail("vcr_mixed_statuses_computes_correct_ratio", f"got {data['vcr']}")


def test_report_on_corrupted_backlog_fails_closed() -> None:
    # A backlog file that EXISTS but fails to parse must fail closed (exit 1,
    # clear stderr error) -- never silently treated as an empty store. This
    # mirrors craftflow_reliability_gates.py's real LedgerCorruptError
    # behavior, which the module docstring claims to mirror.
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        backlog_path = state_dir / "project" / "feature-backlog.json"
        backlog_path.parent.mkdir(parents=True)
        backlog_path.write_text("{not valid json")
        report = run_cli(["--report", "json"], state_dir)
        if report.returncode == 1 and report.stdout == "" and report.stderr.strip():
            ok("report_on_corrupted_backlog_fails_closed")
        else:
            fail(
                "report_on_corrupted_backlog_fails_closed",
                f"exit={report.returncode} stdout={report.stdout!r} stderr={report.stderr!r}",
            )


def test_corrupted_backlog_fails_closed_and_preserves_existing_entries() -> None:
    # Regression for the live-reproduced data-loss bug: seed real entries
    # (including one already passing), corrupt the file, then attempt a
    # write op. The pre-existing entries must NOT be silently destroyed by
    # the next write persisting an empty store over the corrupted file.
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        backlog_path = state_dir / "project" / "feature-backlog.json"

        run_cli(["--register", json.dumps({"id": "feat-real", "title": "Real Feature"})], state_dir)
        run_cli(["--activate", "feat-real"], state_dir)
        run_cli(["--complete", "feat-real"], state_dir)
        pre_report = run_cli(["--report", "json"], state_dir)
        pre_data = json.loads(pre_report.stdout)
        pre_entry = next((f for f in pre_data["features"] if f["id"] == "feat-real"), None)
        if not (pre_entry and pre_entry["status"] == "passing"):
            fail(
                "corrupted_backlog_fails_closed_and_preserves_existing_entries",
                f"seed setup failed: {pre_data}",
            )
            return

        corrupted_bytes = "{not valid json"
        backlog_path.write_text(corrupted_bytes)

        write_result = run_cli(["--register", json.dumps({"id": "feat-new"})], state_dir)
        on_disk = backlog_path.read_text()

        if write_result.returncode == 1 and on_disk == corrupted_bytes:
            ok("corrupted_backlog_fails_closed_and_preserves_existing_entries")
        else:
            fail(
                "corrupted_backlog_fails_closed_and_preserves_existing_entries",
                f"write exit={write_result.returncode} stderr={write_result.stderr!r} on_disk={on_disk!r}",
            )


def test_report_skips_malformed_list_entries_and_computes_valid_vcr() -> None:
    # One bad record anywhere in the list must never take down --report for
    # every OTHER valid entry. Mirrors find_feature()'s existing
    # isinstance(feat, dict) skip-guard, applied consistently at every other
    # site that iterates the same shared "features" list.
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        backlog_path = state_dir / "project" / "feature-backlog.json"
        backlog_path.parent.mkdir(parents=True)
        backlog_path.write_text(json.dumps({
            "schema_version": 1,
            "features": [
                {"id": "feat-ok", "title": "Good Feature", "status": "passing"},
                "not-a-dict-entry",
                {"title": "Missing id field", "status": "active"},
            ],
        }))

        json_result = run_cli(["--report", "json"], state_dir)
        if json_result.returncode != 0:
            fail("report_skips_malformed_json", f"exit {json_result.returncode}: {json_result.stderr}")
        else:
            data = json.loads(json_result.stdout)
            vcr = data["vcr"]
            if vcr["passing"] == 1 and vcr["activated"] == 1 and vcr["display"] == "1/1":
                ok("report_skips_malformed_json")
            else:
                fail("report_skips_malformed_json", f"got {vcr}")

        text_result = run_cli(["--report", "text"], state_dir)
        if text_result.returncode != 0:
            fail("report_skips_malformed_text", f"exit {text_result.returncode}: {text_result.stderr}")
        elif "feat-ok" in text_result.stdout and "VCR: 1/1" in text_result.stdout:
            ok("report_skips_malformed_text")
        else:
            fail("report_skips_malformed_text", f"got stdout={text_result.stdout!r}")


def test_complete_unknown_id_exits_1() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        result = run_cli(["--complete", "never-registered"], state_dir)
        if result.returncode == 1:
            ok("complete_unknown_id_exits_1")
        else:
            fail("complete_unknown_id_exits_1", f"expected exit 1, got {result.returncode}")


def test_activate_empty_string_produces_clear_error_not_help_dump() -> None:
    # argparse leaves unset string options as None; an explicitly-given empty
    # string ("") is falsy but IS a value. Dispatch must be precise
    # (`is not None`), not truthiness -- otherwise this falls through every
    # branch to a generic parser.print_help() usage dump with zero indication
    # the real problem is an empty id.
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        result = run_cli(["--activate", ""], state_dir)
        stderr_lower = result.stderr.lower()
        if result.returncode == 1 and "non-empty" in stderr_lower and "usage:" not in stderr_lower:
            ok("activate_empty_string_produces_clear_error_not_help_dump")
        else:
            fail(
                "activate_empty_string_produces_clear_error_not_help_dump",
                f"exit={result.returncode} stderr={result.stderr!r}",
            )


def test_activate_unknown_id_exits_1() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        result = run_cli(["--activate", "never-registered"], state_dir)
        if result.returncode == 1:
            ok("activate_unknown_id_exits_1")
        else:
            fail("activate_unknown_id_exits_1", f"expected exit 1, got {result.returncode}")


def main() -> int:
    print("test_craftflow_feature_backlog: running")
    test_register_creates_not_started_entry()
    test_activate_then_complete_lifecycle()
    test_vcr_zero_activated_is_explicit_na_not_silent_zero()
    test_vcr_mixed_statuses_computes_correct_ratio()
    test_report_on_corrupted_backlog_fails_closed()
    test_corrupted_backlog_fails_closed_and_preserves_existing_entries()
    test_report_skips_malformed_list_entries_and_computes_valid_vcr()
    test_complete_unknown_id_exits_1()
    test_activate_unknown_id_exits_1()
    test_activate_empty_string_produces_clear_error_not_help_dump()
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
