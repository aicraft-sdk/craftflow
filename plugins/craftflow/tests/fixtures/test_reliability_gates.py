#!/usr/bin/env python3
"""Tests for craftflow_reliability_gates.py.

Run: python3 tests/fixtures/test_reliability_gates.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

SCRIPT = SCRIPTS / "craftflow_reliability_gates.py"

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


def ledger_path(state_dir: Path) -> Path:
    return state_dir / "project" / "reliability-gates.json"


def test_seed_creates_file_with_three_gates() -> None:
    print("\n[--seed]")
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        result = run_cli(["--seed"], state_dir)
        data = json.loads(ledger_path(state_dir).read_text())
        ids = sorted(g["id"] for g in data["gates"])
        expected = sorted([
            "worktree-merge-safety",
            "memory-write-guard-symmetry",
            "fix-verify-evidence-completeness",
        ])
        if result.returncode == 0 and ids == expected and all(
            g["maturity"] == "experimental" and g["evidenceRuns"] == [] for g in data["gates"]
        ):
            ok("seed creates exactly 3 gates, all experimental, empty evidenceRuns")
        else:
            fail("seed-creates-file", f"exit={result.returncode} ids={ids} data={data}")


def test_seed_idempotent_does_not_overwrite_existing_evidence() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--seed"], state_dir)
        run_cli(
            ["--record-evidence", "worktree-merge-safety", "--wf", "wf-1", "--outcome", "pass"],
            state_dir,
        )
        run_cli(["--seed"], state_dir)
        data = json.loads(ledger_path(state_dir).read_text())
        gate = next(g for g in data["gates"] if g["id"] == "worktree-merge-safety")
        if len(gate["evidenceRuns"]) == 1:
            ok("re-running --seed does not clobber existing evidence")
        else:
            fail("seed-idempotent", f"expected 1 evidence entry, got {gate['evidenceRuns']}")


def test_corrupted_json_fails_closed_no_overwrite() -> None:
    print("\n[fail-closed on corruption]")
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--seed"], state_dir)
        path = ledger_path(state_dir)
        original = path.read_text()
        path.write_text("{not valid json")
        result = run_cli(
            ["--record-evidence", "worktree-merge-safety", "--wf", "wf-x", "--outcome", "pass"],
            state_dir,
        )
        after = path.read_text()
        if result.returncode != 0 and after == "{not valid json" and after != original:
            ok("corrupt ledger fails closed: non-zero exit, file left byte-for-byte unchanged")
        else:
            fail("corrupted-fail-closed", f"exit={result.returncode} after={after!r}")


def main() -> int:
    print("test_reliability_gates: running")
    test_seed_creates_file_with_three_gates()
    test_seed_idempotent_does_not_overwrite_existing_evidence()
    test_corrupted_json_fails_closed_no_overwrite()
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
