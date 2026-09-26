#!/usr/bin/env python3
"""Tests for craftflow_harness_capabilities_impact.py.

Covers: the script runs and exits 0, the report file is created, each
capability's section appears in the report, and the detection-rate numbers
produced by the script's own comparison functions are internally consistent
(detected counts never exceed the fixture totals they are drawn from).

This file does not re-test craftflow_arch_lint.py, craftflow_clean_state_
check.py, or craftflow_feature_backlog.py themselves -- those already have
their own dedicated fixture test files in this directory.

Run from the plugin root:
    python3 tests/fixtures/test_craftflow_harness_capabilities_impact.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_harness_capabilities_impact.py"
REPO_ROOT = PLUGIN_ROOT.parents[3]
REPORT_PATH = REPO_ROOT / "docs" / "benchmarks" / "2026-09-26-harness-capabilities-impact.md"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def test_script_runs_and_exits_zero() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    if result.returncode == 0:
        ok("script_runs_and_exits_zero")
    else:
        fail("script_runs_and_exits_zero", f"exit {result.returncode}: {result.stderr}")


def test_report_file_created() -> None:
    if REPORT_PATH.exists():
        ok("report_file_created")
    else:
        fail("report_file_created", f"{REPORT_PATH} does not exist")


def test_each_capability_section_appears_in_report() -> None:
    try:
        text = REPORT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        fail("section_present_report_readable", f"could not read {REPORT_PATH}: {exc}")
        return
    for heading in ("Arch Lint", "Clean-State Check", "Feature Backlog"):
        if heading in text:
            ok(f"section_present_{heading}")
        else:
            fail(f"section_present_{heading}", f"heading {heading!r} not found in report")


def test_detection_counts_internally_consistent() -> None:
    from craftflow_harness_capabilities_impact import (
        build_binary_summary,
        run_arch_lint_comparison,
        run_clean_state_comparison,
        run_feature_backlog_comparison,
    )

    arch_rows = run_arch_lint_comparison()
    arch_summary = build_binary_summary(arch_rows)
    if 0 <= arch_summary["new_detected"] <= arch_summary["violations_total"]:
        ok("arch_lint_detected_count_within_total")
    else:
        fail("arch_lint_detected_count_within_total", f"{arch_summary}")
    if arch_summary["violations_total"] > 0:
        ok("arch_lint_has_at_least_one_violation_fixture")
    else:
        fail("arch_lint_has_at_least_one_violation_fixture", f"{arch_summary}")

    clean_rows = run_clean_state_comparison()
    clean_summary = build_binary_summary(clean_rows)
    if 0 <= clean_summary["new_detected"] <= clean_summary["violations_total"]:
        ok("clean_state_detected_count_within_total")
    else:
        fail("clean_state_detected_count_within_total", f"{clean_summary}")
    if clean_summary["violations_total"] > 0:
        ok("clean_state_has_at_least_one_violation_fixture")
    else:
        fail("clean_state_has_at_least_one_violation_fixture", f"{clean_summary}")

    backlog_rows = run_feature_backlog_comparison()
    correct_count = sum(1 for r in backlog_rows if r["correct"])
    if len(backlog_rows) > 0 and 0 <= correct_count <= len(backlog_rows):
        ok("feature_backlog_correct_count_within_total")
    else:
        fail("feature_backlog_correct_count_within_total", f"{backlog_rows}")


def main() -> int:
    print("test_craftflow_harness_capabilities_impact: running")
    test_script_runs_and_exits_zero()
    test_report_file_created()
    test_each_capability_section_appears_in_report()
    test_detection_counts_internally_consistent()
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
