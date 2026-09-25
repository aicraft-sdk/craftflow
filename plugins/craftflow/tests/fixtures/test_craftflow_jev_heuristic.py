#!/usr/bin/env python3
"""Tests for craftflow_jev_heuristic.py.

Run: python3 tests/fixtures/test_craftflow_jev_heuristic.py

P7 (heuristic parity): the two parity tests below parse the two source-of-truth
markdown tables at test time and assert they equal the module's literal
constants -- if either doc's table ever drifts from the module, these tests
catch it (DD-10).
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_heuristic import (  # noqa: E402
    INTENT_TABLE,
    RISK_KEYWORDS,
    SKILL_RULES,
    classify,
    parse_intent_table,
    parse_risk_table,
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


def test_parity_with_router_protocol_markdown() -> None:
    text = (PLUGIN_ROOT / "skills" / "_shared" / "router-protocol.md").read_text(encoding="utf-8")
    parsed = parse_intent_table(text)
    if parsed == INTENT_TABLE:
        ok("intent table parity with router-protocol.md")
    else:
        fail("parity-router-protocol", f"parsed={parsed!r} INTENT_TABLE={INTENT_TABLE!r}")


def test_parity_with_fast_path_markdown() -> None:
    text = (PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / "fast-path.md").read_text(encoding="utf-8")
    parsed = parse_risk_table(text)
    if parsed == RISK_KEYWORDS:
        ok("risk keyword table parity with fast-path.md")
    else:
        fail("parity-fast-path", f"parsed={parsed!r} RISK_KEYWORDS={RISK_KEYWORDS!r}")


def test_priority_error_wins_over_plan() -> None:
    r = classify("plan how to fix the crash")
    if r["workflow"] == "DEBUG" and "fix" in r["matched"] and "crash" in r["matched"]:
        ok("ERROR priority wins over PLAN keyword in same prompt")
    else:
        fail("priority-error-wins", f"r={r!r}")


def test_default_is_build_and_word_boundaries() -> None:
    r1 = classify("add a prefix helper")
    r2 = classify("review this PR")
    if r1["workflow"] == "BUILD" and r2["workflow"] == "REVIEW":
        ok("default BUILD workflow with word-boundary matching ('fix' inside 'prefix' excluded)")
    else:
        fail("default-build-word-boundaries", f"r1={r1!r} r2={r2!r}")


def test_risk_signals_and_skill_rule() -> None:
    r = classify("add oauth login to the react form")
    checks = (
        "oauth" in r["risk_signals"],
        r["skill"] == "craftflow:frontend-patterns",
        classify("debug the failing test")["skill"] == "craftflow:debugging-patterns",
        classify("rename a variable")["skill"] == "none",
    )
    if all(checks):
        ok("risk signal detection and skill rule precedence (debug > frontend > architecture > none)")
    else:
        fail("risk-signals-and-skill-rule", f"checks={checks!r} r={r!r}")


def test_skill_rule_source_lines_still_present() -> None:
    text = (PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md").read_text(encoding="utf-8")
    checks = (
        "Include `craftflow:frontend-patterns` only when" in text,
        "Include `craftflow:architecture-patterns` only for multi-component, API, schema, auth, or integration-heavy work." in text,
    )
    if all(checks) and SKILL_RULES:
        ok("skill-rule source lines still present in craftflow-router/SKILL.md")
    else:
        fail("skill-rule-source-lines", f"checks={checks!r}")


def test_remfix_scope_heuristic_matches_documented_recommendation() -> None:
    from craftflow_jev_heuristic import classify_remfix_scope, REMFIX_SCOPE_RECOMMENDED
    doc = (PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / "remediation-and-research.md").read_text()
    if (
        classify_remfix_scope() == "critical_only" == REMFIX_SCOPE_RECOMMENDED
        and "Fix critical only (Recommended)" in doc
    ):
        ok("classify_remfix_scope() matches the literal, still-documented recommendation")
    else:
        fail("remfix-scope-heuristic-drift", f"classify={classify_remfix_scope()!r}")


def main() -> int:
    print("test_craftflow_jev_heuristic: running")
    test_parity_with_router_protocol_markdown()
    test_parity_with_fast_path_markdown()
    test_priority_error_wins_over_plan()
    test_default_is_build_and_word_boundaries()
    test_risk_signals_and_skill_rule()
    test_skill_rule_source_lines_still_present()
    test_remfix_scope_heuristic_matches_documented_recommendation()

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
