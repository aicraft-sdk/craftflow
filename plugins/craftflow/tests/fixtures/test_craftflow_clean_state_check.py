#!/usr/bin/env python3
"""Tests for craftflow_clean_state_check.py.

Run: python3 tests/fixtures/test_craftflow_clean_state_check.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_clean_state_check.py"

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)


def run_cli(project_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(project_root), "--format", "json"],
        capture_output=True,
        text=True,
    )


def test_console_log_detected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text("function f() {\n  console.log('debug');\n}\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "console.log"]
        if len(hits) == 1 and hits[0]["line"] == 2:
            ok("console_log_detected")
        else:
            fail("console_log_detected", f"got {data}")


def test_debugger_detected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text("function f() {\n  debugger;\n}\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "debugger"]
        if len(hits) == 1 and hits[0]["line"] == 2:
            ok("debugger_detected")
        else:
            fail("debugger_detected", f"got {data}")


def test_clean_diff_reports_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text("function f() {\n  return 1;\n}\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        if data["findings"] == []:
            ok("clean_diff_reports_nothing")
        else:
            fail("clean_diff_reports_nothing", f"expected no findings, got {data}")


def test_unreadable_untracked_file_is_surfaced_not_swallowed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        unreadable = root / "secret.ts"
        unreadable.write_text("console.log('unreachable');\n")
        unreadable.chmod(0o000)
        try:
            result = run_cli(root)
            data = json.loads(result.stdout)
            skipped = data.get("skipped", [])
            hits = [s for s in skipped if s.get("file") == "secret.ts"]
            if len(hits) == 1 and "error" in hits[0]:
                ok("unreadable_untracked_file_is_surfaced_not_swallowed")
            else:
                fail("unreadable_untracked_file_is_surfaced_not_swallowed", f"got {data}")
        finally:
            unreadable.chmod(0o644)


def main() -> int:
    print("test_craftflow_clean_state_check: running")
    test_console_log_detected()
    test_debugger_detected()
    test_clean_diff_reports_nothing()
    test_unreadable_untracked_file_is_surfaced_not_swallowed()
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
