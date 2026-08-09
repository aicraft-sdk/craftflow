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


def test_record_evidence_appends_entry_to_matching_gate() -> None:
    print("\n[--record-evidence]")
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--seed"], state_dir)
        result = run_cli(
            [
                "--record-evidence", "fix-verify-evidence-completeness",
                "--wf", "wf-abc", "--outcome", "pass", "--note", "cycle 1",
            ],
            state_dir,
        )
        data = json.loads(ledger_path(state_dir).read_text())
        gate = next(g for g in data["gates"] if g["id"] == "fix-verify-evidence-completeness")
        entry = gate["evidenceRuns"][-1] if gate["evidenceRuns"] else {}
        if (
            result.returncode == 0
            and entry.get("wf") == "wf-abc"
            and entry.get("outcome") == "pass"
            and entry.get("note") == "cycle 1"
            and "ts" in entry
        ):
            ok("record-evidence appends a well-formed entry to the matching gate")
        else:
            fail("record-evidence-append", f"exit={result.returncode} entry={entry}")


def test_record_evidence_unknown_gate_id_fails_cleanly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--seed"], state_dir)
        before = ledger_path(state_dir).read_text()
        result = run_cli(
            ["--record-evidence", "nonexistent-gate", "--wf", "wf-x", "--outcome", "pass"],
            state_dir,
        )
        after = ledger_path(state_dir).read_text()
        if result.returncode != 0 and before == after:
            ok("unknown gate_id fails closed, no write")
        else:
            fail("unknown-gate-id", f"exit={result.returncode} changed={before != after}")


def test_record_evidence_without_seed_fails_cleanly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        result = run_cli(
            ["--record-evidence", "worktree-merge-safety", "--wf", "wf-x", "--outcome", "pass"],
            state_dir,
        )
        if result.returncode != 0 and not ledger_path(state_dir).exists():
            ok("record-evidence against an unseeded ledger fails closed, creates nothing")
        else:
            fail("no-seed-yet", f"exit={result.returncode} exists={ledger_path(state_dir).exists()}")


def test_record_evidence_invalid_outcome_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--seed"], state_dir)
        result = run_cli(
            ["--record-evidence", "worktree-merge-safety", "--wf", "wf-x", "--outcome", "maybe"],
            state_dir,
        )
        if result.returncode != 0:
            ok("--outcome maybe rejected by argparse choices")
        else:
            fail("invalid-outcome", f"expected nonzero exit, got {result.returncode}")


def test_record_evidence_concurrent_appends_no_lost_writes() -> None:
    print("\n[concurrency]")
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--seed"], state_dir)

        results = []

        def worker(i: int) -> None:
            r = run_cli(
                [
                    "--record-evidence", "worktree-merge-safety",
                    "--wf", f"wf-concurrent-{i}", "--outcome", "pass",
                ],
                state_dir,
            )
            results.append(r.returncode)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        data = json.loads(ledger_path(state_dir).read_text())
        gate = next(g for g in data["gates"] if g["id"] == "worktree-merge-safety")
        wf_ids = {e["wf"] for e in gate["evidenceRuns"]}
        expected_ids = {f"wf-concurrent-{i}" for i in range(5)}
        if all(rc == 0 for rc in results) and wf_ids == expected_ids:
            ok("5 concurrent --record-evidence calls: all 5 entries land, no lost update")
        else:
            fail("concurrent-appends", f"results={results} wf_ids={wf_ids}")


def test_promote_transitions_maturity_and_logs_history() -> None:
    print("\n[--promote]")
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--seed"], state_dir)
        result = run_cli(["--promote", "worktree-merge-safety", "--to", "soak"], state_dir)
        data = json.loads(ledger_path(state_dir).read_text())
        gate = next(g for g in data["gates"] if g["id"] == "worktree-merge-safety")
        history_entry = gate["maturity_history"][-1] if gate["maturity_history"] else {}
        if (
            result.returncode == 0
            and gate["maturity"] == "soak"
            and history_entry.get("from") == "experimental"
            and history_entry.get("to") == "soak"
        ):
            ok("promote transitions maturity and appends maturity_history entry")
        else:
            fail("promote-transitions", f"exit={result.returncode} gate={gate}")


def test_promote_never_touches_evidence_runs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--seed"], state_dir)
        run_cli(
            ["--record-evidence", "worktree-merge-safety", "--wf", "wf-1", "--outcome", "pass"],
            state_dir,
        )
        run_cli(["--promote", "worktree-merge-safety", "--to", "blocking"], state_dir)
        data = json.loads(ledger_path(state_dir).read_text())
        gate = next(g for g in data["gates"] if g["id"] == "worktree-merge-safety")
        if len(gate["evidenceRuns"]) == 1 and gate["evidenceRuns"][0]["wf"] == "wf-1":
            ok("promote never mutates evidenceRuns")
        else:
            fail("promote-purity", f"evidenceRuns={gate['evidenceRuns']}")


def test_promote_unknown_gate_id_fails_cleanly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--seed"], state_dir)
        result = run_cli(["--promote", "nonexistent-gate", "--to", "soak"], state_dir)
        if result.returncode != 0:
            ok("promote against unknown gate_id fails cleanly")
        else:
            fail("promote-unknown-gate", f"exit={result.returncode}")


def test_list_prints_gate_summary() -> None:
    print("\n[--list]")
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--seed"], state_dir)
        result = run_cli(["--list"], state_dir)
        try:
            summary = json.loads(result.stdout)
        except json.JSONDecodeError:
            summary = None
        if (
            result.returncode == 0
            and isinstance(summary, list)
            and len(summary) == 3
            and all("id" in g and "maturity" in g and "evidence_count" in g for g in summary)
        ):
            ok("--list prints a 3-gate compact summary")
        else:
            fail("list-summary", f"exit={result.returncode} stdout={result.stdout!r}")


def test_query_prints_full_ledger() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".craftflow" / "state"
        run_cli(["--seed"], state_dir)
        result = run_cli(["--query"], state_dir)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            data = None
        if result.returncode == 0 and isinstance(data, dict) and len(data.get("gates", [])) == 3:
            ok("--query prints the full raw ledger")
        else:
            fail("query-full-ledger", f"exit={result.returncode} stdout={result.stdout!r}")


def main() -> int:
    print("test_reliability_gates: running")
    test_seed_creates_file_with_three_gates()
    test_seed_idempotent_does_not_overwrite_existing_evidence()
    test_corrupted_json_fails_closed_no_overwrite()
    test_record_evidence_appends_entry_to_matching_gate()
    test_record_evidence_unknown_gate_id_fails_cleanly()
    test_record_evidence_without_seed_fails_cleanly()
    test_record_evidence_invalid_outcome_rejected()
    test_record_evidence_concurrent_appends_no_lost_writes()
    test_promote_transitions_maturity_and_logs_history()
    test_promote_never_touches_evidence_runs()
    test_promote_unknown_gate_id_fails_cleanly()
    test_list_prints_gate_summary()
    test_query_prints_full_ledger()
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
