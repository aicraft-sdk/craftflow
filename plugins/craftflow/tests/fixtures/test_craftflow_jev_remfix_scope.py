#!/usr/bin/env python3
"""Tests for craftflow_jev_remfix_scope.py.

Run: python3 tests/fixtures/test_craftflow_jev_remfix_scope.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_remfix_scope import build_state, build_questions, decide  # noqa: E402

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def test_build_state_caps_combined_text_and_counts() -> None:
    state = build_state(["c1", "c2"], ["h1"], max_chars=10)
    if state["critical_count"] == 2 and state["high_count"] == 1 and len(state["critical_issues"]) <= 5 and len(state["high_issues"]) <= 5:
        ok("build_state caps text and preserves counts")
    else:
        fail("build-state-caps", f"state={state!r}")


def test_build_questions_shape() -> None:
    q = build_questions()
    if q["scope"]["type"] == "choice" and set(q["scope"]["criteria"]) == {"critical_only", "all_issues"}:
        ok("build_questions returns a single scope choice question")
    else:
        fail("build-questions-shape", f"q={q!r}")


def test_decide_audit_mode_always_logged_never_applied() -> None:
    d, c, conf = decide("audit", {"choice": "all_issues", "confidence": 0.99}, threshold=0.5)
    if d == "logged" and c == "all_issues" and conf == 0.99:
        ok("audit mode never auto-applies regardless of confidence")
    else:
        fail("decide-audit-never-applies", f"d={d!r} c={c!r} conf={conf!r}")


def test_decide_advise_applies_at_or_above_threshold() -> None:
    at = decide("advise", {"choice": "critical_only", "confidence": 0.85}, threshold=0.85)
    below = decide("advise", {"choice": "critical_only", "confidence": 0.849999}, threshold=0.85)
    if at[0] == "applied" and below[0] == "below_threshold":
        ok("advise mode applies at >= threshold, falls back just below it")
    else:
        fail("decide-advise-threshold-boundary", f"at={at!r} below={below!r}")


def test_decide_rejects_malformed_answer_as_no_decision() -> None:
    cases = [
        decide("advise", None, threshold=0.5),
        decide("advise", {}, threshold=0.5),
        decide("advise", {"choice": "maybe", "confidence": 0.9}, threshold=0.5),
        decide("advise", {"choice": "critical_only", "confidence": "high"}, threshold=0.5),
        decide("advise", {"choice": "critical_only", "confidence": True}, threshold=0.5),  # bool is an int subclass
        decide("advise", {"choice": "critical_only", "confidence": 1.5}, threshold=0.5),
        decide("advise", {"choice": "critical_only", "confidence": float("nan")}, threshold=0.5),
    ]
    if all(c[0] == "no_decision" and c[1] is None and c[2] is None for c in cases):
        ok("decide() rejects every malformed answer shape as no_decision")
    else:
        fail("decide-rejects-malformed", f"cases={cases!r}")


def main() -> int:
    print("test_craftflow_jev_remfix_scope: running")
    test_build_state_caps_combined_text_and_counts()
    test_build_questions_shape()
    test_decide_audit_mode_always_logged_never_applied()
    test_decide_advise_applies_at_or_above_threshold()
    test_decide_rejects_malformed_answer_as_no_decision()

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
