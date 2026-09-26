#!/usr/bin/env python3
"""
craftflow_contract_validate_impact.py

Before/after comparison demonstrating the measurable improvement from the
shipped Router Contract schema-validation pilot
(craftflow_contract_validate_pilot.py, commit 53ac77fc) versus the honest
pre-pilot status quo, for the two agents it covers (component-builder,
planner).

This script does NOT re-implement or re-test the validator itself -- that is
already covered by tests/fixtures/test_contract_validate_pilot.py (29
assertions). It imports the real SCHEMAS_DIR, parse_block_yaml, and
validate_contract from craftflow_contract_validate_pilot.py and reuses them
against a batch of intentionally malformed fixtures, contrasting the real
validator's result ("new path") against a naive presence-only proxy for the
pre-pilot status quo ("old path" -- see old_path_naive_presence_check below
for the honesty disclosure on what that proxy is and is not).

Usage:
    python3 craftflow_contract_validate_impact.py

Writes docs/benchmarks/2026-09-26-contract-validation-impact.md (repo root)
and prints the same summary to stdout. Always exits 0 -- this is a reporting
script, not a pass/fail gate. If the shipped validator ever regresses and
fails to catch a malformed fixture, that shows up honestly in the printed
and written summary rather than crashing the script.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, List

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from craftflow_contract_validate_pilot import (  # noqa: E402
    SCHEMAS_DIR,
    parse_block_yaml,
    validate_contract,
)

REPO_ROOT = SCRIPT_DIR.parents[4]
REPORT_PATH = REPO_ROOT / "docs" / "benchmarks" / "2026-09-26-contract-validation-impact.md"

HONESTY_DISCLOSURE = (
    "No deterministic type/enum/nesting validator existed for "
    "component-builder or planner's dispatched Router Contract output "
    "before this pilot. The router (an LLM) read the YAML block as prose "
    "and applied SKILL.md's Contract Overrides table by eye, with zero code "
    "enforcement. `old_path_naive_presence_check` below is a best-effort "
    "deterministic *proxy* for that informal, eyeball status quo -- built "
    "from the one class of mechanical check that has precedent elsewhere in "
    "this codebase (`craftflow_harness_audit.py`'s substring-presence style "
    "checks, applied there to agent *template* files). It is not a "
    "resurrection of literal legacy code: no such code ever ran against "
    "these two agents' dispatched output. Treat every 'old path' result "
    "below as an honest simulation of the informal status quo, not as a bug "
    "found in real legacy code."
)


def load_schema(agent: str) -> dict:
    return json.loads((SCHEMAS_DIR / f"{agent}.json").read_text(encoding="utf-8"))


SCHEMAS = {
    "component-builder": load_schema("component-builder"),
    "planner": load_schema("planner"),
}


# ---------------------------------------------------------------------------
# Old path: the only mechanical check with any precedent in this codebase for
# this class of problem, applied to contract *output* text instead of agent
# *template* text. See HONESTY_DISCLOSURE above for what this is and is not.
# ---------------------------------------------------------------------------
def old_path_naive_presence_check(text: str, schema: dict) -> dict:
    """Pre-pilot status-quo proxy: for each required field key in `schema`,
    check ONLY whether that key name appears as a bare substring anywhere in
    the raw fixture text. Zero type checking, zero enum checking, zero
    nesting/parent-key awareness -- a field's key name being present
    anywhere in the text (regardless of its value or which section it sits
    under) is treated as "present and fine". Returns
    {"valid": bool, "reason": str}; valid=True means every required key's
    name was found as a substring somewhere in the text."""
    missing = sorted(
        name for name, spec in schema.items()
        if spec.get("required", False) and name not in text
    )
    if missing:
        return {
            "valid": False,
            "reason": f"missing required key substring(s): {', '.join(missing)}",
        }
    return {
        "valid": True,
        "reason": "every required key name found as a substring somewhere in "
                   "the text; no type/enum/nesting check performed",
    }


# ---------------------------------------------------------------------------
# Fixtures: ~20 intentionally malformed nested verdict:/rationale: contracts,
# spanning both schemas and all 4 violation categories the pilot's own test
# suite already proves it catches. Each mutates a valid base template by
# exactly one defect so the category/field under test is unambiguous.
# ---------------------------------------------------------------------------
BUILDER_BASE = """\
verdict:
  STATUS: PASS
  CONFIDENCE: 90
  PHASE_ID: "phase-1"
  PHASE_STATUS: completed
  PHASE_EXIT_READY: true
  CHECKPOINT_TYPE: none
  PROOF_STATUS: passed
  TDD_RED_EXIT: 1
  TDD_GREEN_EXIT: 0
  CRITICAL_ISSUES: 0
  BLOCKING: false
  NEXT_ACTION: review
  REMEDIATION_NEEDED: false
  REQUIRES_REMEDIATION: false
  REMEDIATION_SCOPE_REQUESTED: N/A
rationale:
  SUMMARY: "fixture baseline for impact comparison"
  INPUTS: []
  EXPECTED_ARTIFACTS: []
  SCENARIOS:
    - name: "scenario one"
      given: "state"
      when: "action"
      then: "result"
      command: "npm test"
      expected: "pass"
      actual: "pass"
      exit_code: 0
      status: PASS
  ASSUMPTIONS: []
  DECISIONS: []
  BLOCKED_ITEMS: []
  SKIPPED_ITEMS: []
  SCOPE_INCREASES: []
  REMEDIATION_REASON: null
  MEMORY_NOTES:
    learnings: []
    patterns: []
    verification: []
    deferred: []
"""

PLANNER_BASE = """\
verdict:
  STATUS: PLAN_CREATED
  PLAN_MODE: execution_plan
  VERIFICATION_RIGOR: standard
  CONFIDENCE: 85
  PHASES: 5
  RISKS_IDENTIFIED: 5
  PLANNING_REVIEW_STATUS: passed
  PLANNING_REVIEW_RUNS: 0
  BLOCKING: false
  NEXT_ACTION: build
  REMEDIATION_NEEDED: false
  REQUIRES_REMEDIATION: false
  GATE_PASSED: true
  REMEDIATION_SCOPE_REQUESTED: N/A
rationale:
  SUMMARY: "planned the fixture baseline for impact comparison"
  PLAN_FILE: "docs/plans/example-plan.md"
  LIVING_SPEC_IMPACTED: null
  SCENARIOS:
    - name: "scenario one"
      given: "state"
      when: "action"
      then: "result"
  ASSUMPTIONS: []
  DECISIONS: []
  OPEN_DECISIONS: []
  DIFFERENCES_FROM_AGREEMENT: []
  RECOMMENDED_DEFAULTS: []
  ALTERNATIVES: []
  DRAWBACKS: []
  PROVABLE_PROPERTIES: []
  REMEDIATION_REASON: null
  USER_INPUT_NEEDED: []
  MEMORY_NOTES:
    learnings: []
    patterns: []
    verification: []
"""

CB_WRONG_PARENT_PHASE_EXIT_READY = """\
verdict:
  STATUS: PASS
  CONFIDENCE: 90
  PHASE_ID: "phase-1"
  PHASE_STATUS: completed
  CHECKPOINT_TYPE: none
  PROOF_STATUS: passed
  TDD_RED_EXIT: 1
  TDD_GREEN_EXIT: 0
  CRITICAL_ISSUES: 0
  BLOCKING: false
  NEXT_ACTION: review
  REMEDIATION_NEEDED: false
  REQUIRES_REMEDIATION: false
  REMEDIATION_SCOPE_REQUESTED: N/A
rationale:
  SUMMARY: "wrong-parent fixture: PHASE_EXIT_READY misplaced"
  INPUTS: []
  EXPECTED_ARTIFACTS: []
  SCENARIOS: []
  ASSUMPTIONS: []
  DECISIONS: []
  BLOCKED_ITEMS: []
  SKIPPED_ITEMS: []
  SCOPE_INCREASES: []
  REMEDIATION_REASON: null
  MEMORY_NOTES: {}
  PHASE_EXIT_READY: true
"""

CB_WRONG_PARENT_REMEDIATION_REASON = """\
verdict:
  STATUS: PASS
  CONFIDENCE: 90
  PHASE_ID: "phase-1"
  PHASE_STATUS: completed
  PHASE_EXIT_READY: true
  CHECKPOINT_TYPE: none
  PROOF_STATUS: passed
  TDD_RED_EXIT: 1
  TDD_GREEN_EXIT: 0
  CRITICAL_ISSUES: 0
  BLOCKING: false
  NEXT_ACTION: review
  REMEDIATION_NEEDED: false
  REQUIRES_REMEDIATION: false
  REMEDIATION_SCOPE_REQUESTED: N/A
  REMEDIATION_REASON: null
rationale:
  SUMMARY: "wrong-parent fixture: REMEDIATION_REASON misplaced"
  INPUTS: []
  EXPECTED_ARTIFACTS: []
  SCENARIOS: []
  ASSUMPTIONS: []
  DECISIONS: []
  BLOCKED_ITEMS: []
  SKIPPED_ITEMS: []
  SCOPE_INCREASES: []
  MEMORY_NOTES: {}
"""

CB_WRONG_PARENT_BLOCKING = """\
verdict:
  STATUS: PASS
  CONFIDENCE: 90
  PHASE_ID: "phase-1"
  PHASE_STATUS: completed
  PHASE_EXIT_READY: true
  CHECKPOINT_TYPE: none
  PROOF_STATUS: passed
  TDD_RED_EXIT: 1
  TDD_GREEN_EXIT: 0
  CRITICAL_ISSUES: 0
  NEXT_ACTION: review
  REMEDIATION_NEEDED: false
  REQUIRES_REMEDIATION: false
  REMEDIATION_SCOPE_REQUESTED: N/A
rationale:
  SUMMARY: "wrong-parent fixture: BLOCKING misplaced"
  INPUTS: []
  EXPECTED_ARTIFACTS: []
  SCENARIOS: []
  ASSUMPTIONS: []
  DECISIONS: []
  BLOCKED_ITEMS: []
  SKIPPED_ITEMS: []
  SCOPE_INCREASES: []
  REMEDIATION_REASON: null
  MEMORY_NOTES: {}
  BLOCKING: false
"""

PL_WRONG_PARENT_GATE_PASSED = """\
verdict:
  STATUS: PLAN_CREATED
  PLAN_MODE: execution_plan
  VERIFICATION_RIGOR: standard
  CONFIDENCE: 85
  PHASES: 5
  RISKS_IDENTIFIED: 5
  PLANNING_REVIEW_STATUS: passed
  PLANNING_REVIEW_RUNS: 0
  BLOCKING: false
  NEXT_ACTION: build
  REMEDIATION_NEEDED: false
  REQUIRES_REMEDIATION: false
  REMEDIATION_SCOPE_REQUESTED: N/A
rationale:
  SUMMARY: "wrong-parent fixture: GATE_PASSED misplaced"
  PLAN_FILE: "docs/plans/example-plan.md"
  LIVING_SPEC_IMPACTED: null
  SCENARIOS: []
  ASSUMPTIONS: []
  DECISIONS: []
  OPEN_DECISIONS: []
  DIFFERENCES_FROM_AGREEMENT: []
  RECOMMENDED_DEFAULTS: []
  ALTERNATIVES: []
  DRAWBACKS: []
  PROVABLE_PROPERTIES: []
  REMEDIATION_REASON: null
  USER_INPUT_NEEDED: []
  MEMORY_NOTES: {}
  GATE_PASSED: true
"""

PL_WRONG_PARENT_PLAN_FILE = """\
verdict:
  STATUS: PLAN_CREATED
  PLAN_MODE: execution_plan
  VERIFICATION_RIGOR: standard
  CONFIDENCE: 85
  PHASES: 5
  RISKS_IDENTIFIED: 5
  PLANNING_REVIEW_STATUS: passed
  PLANNING_REVIEW_RUNS: 0
  BLOCKING: false
  NEXT_ACTION: build
  REMEDIATION_NEEDED: false
  REQUIRES_REMEDIATION: false
  GATE_PASSED: true
  REMEDIATION_SCOPE_REQUESTED: N/A
  PLAN_FILE: "docs/plans/example-plan.md"
rationale:
  SUMMARY: "wrong-parent fixture: PLAN_FILE misplaced"
  LIVING_SPEC_IMPACTED: null
  SCENARIOS: []
  ASSUMPTIONS: []
  DECISIONS: []
  OPEN_DECISIONS: []
  DIFFERENCES_FROM_AGREEMENT: []
  RECOMMENDED_DEFAULTS: []
  ALTERNATIVES: []
  DRAWBACKS: []
  PROVABLE_PROPERTIES: []
  REMEDIATION_REASON: null
  USER_INPUT_NEEDED: []
  MEMORY_NOTES: {}
"""


def _remove_line(base: str, line: str) -> str:
    assert line in base, f"fixture setup error: line not found: {line!r}"
    return base.replace(line, "", 1)


def _replace_line(base: str, old_line: str, new_line: str) -> str:
    assert old_line in base, f"fixture setup error: line not found: {old_line!r}"
    return base.replace(old_line, new_line, 1)


FIXTURES: List[dict] = [
    # -- category: missing required field (5) --
    {
        "name": "cb-missing-tdd-red-exit",
        "agent": "component-builder",
        "category": "missing required field",
        "text": _remove_line(BUILDER_BASE, "  TDD_RED_EXIT: 1\n"),
    },
    {
        "name": "cb-missing-checkpoint-type",
        "agent": "component-builder",
        "category": "missing required field",
        "text": _remove_line(BUILDER_BASE, "  CHECKPOINT_TYPE: none\n"),
    },
    {
        "name": "cb-missing-critical-issues",
        "agent": "component-builder",
        "category": "missing required field",
        "text": _remove_line(BUILDER_BASE, "  CRITICAL_ISSUES: 0\n"),
    },
    {
        "name": "pl-missing-plan-mode",
        "agent": "planner",
        "category": "missing required field",
        "text": _remove_line(PLANNER_BASE, "  PLAN_MODE: execution_plan\n"),
    },
    {
        "name": "pl-missing-risks-identified",
        "agent": "planner",
        "category": "missing required field",
        "text": _remove_line(PLANNER_BASE, "  RISKS_IDENTIFIED: 5\n"),
    },
    # -- category: wrong type (5) --
    {
        "name": "cb-wrong-type-confidence",
        "agent": "component-builder",
        "category": "wrong type",
        "text": _replace_line(BUILDER_BASE, '  CONFIDENCE: 90\n', '  CONFIDENCE: "very high"\n'),
    },
    {
        "name": "cb-wrong-type-phase-exit-ready",
        "agent": "component-builder",
        "category": "wrong type",
        "text": _replace_line(BUILDER_BASE, "  PHASE_EXIT_READY: true\n", '  PHASE_EXIT_READY: "yes"\n'),
    },
    {
        "name": "cb-wrong-type-critical-issues",
        "agent": "component-builder",
        "category": "wrong type",
        "text": _replace_line(BUILDER_BASE, "  CRITICAL_ISSUES: 0\n", '  CRITICAL_ISSUES: "none"\n'),
    },
    {
        "name": "pl-wrong-type-confidence",
        "agent": "planner",
        "category": "wrong type",
        "text": _replace_line(PLANNER_BASE, "  CONFIDENCE: 85\n", '  CONFIDENCE: "eighty five"\n'),
    },
    {
        "name": "pl-wrong-type-phases",
        "agent": "planner",
        "category": "wrong type",
        "text": _replace_line(PLANNER_BASE, "  PHASES: 5\n", '  PHASES: "five"\n'),
    },
    # -- category: invalid enum value (5) --
    {
        "name": "cb-invalid-enum-status",
        "agent": "component-builder",
        "category": "invalid enum value",
        "text": _replace_line(BUILDER_BASE, "  STATUS: PASS\n", "  STATUS: MAYBE\n"),
    },
    {
        "name": "cb-invalid-enum-phase-status",
        "agent": "component-builder",
        "category": "invalid enum value",
        "text": _replace_line(BUILDER_BASE, "  PHASE_STATUS: completed\n", "  PHASE_STATUS: kinda_done\n"),
    },
    {
        "name": "cb-invalid-enum-next-action",
        "agent": "component-builder",
        "category": "invalid enum value",
        "text": _replace_line(BUILDER_BASE, "  NEXT_ACTION: review\n", "  NEXT_ACTION: rollback\n"),
    },
    {
        "name": "pl-invalid-enum-plan-mode",
        "agent": "planner",
        "category": "invalid enum value",
        "text": _replace_line(PLANNER_BASE, "  PLAN_MODE: execution_plan\n", "  PLAN_MODE: yolo\n"),
    },
    {
        "name": "pl-invalid-enum-next-action",
        "agent": "planner",
        "category": "invalid enum value",
        "text": _replace_line(PLANNER_BASE, "  NEXT_ACTION: build\n", "  NEXT_ACTION: retry\n"),
    },
    # -- category: field nested under wrong parent key (5) --
    {
        "name": "cb-wrong-parent-phase-exit-ready",
        "agent": "component-builder",
        "category": "wrong parent key",
        "text": CB_WRONG_PARENT_PHASE_EXIT_READY,
    },
    {
        "name": "cb-wrong-parent-remediation-reason",
        "agent": "component-builder",
        "category": "wrong parent key",
        "text": CB_WRONG_PARENT_REMEDIATION_REASON,
    },
    {
        "name": "cb-wrong-parent-blocking",
        "agent": "component-builder",
        "category": "wrong parent key",
        "text": CB_WRONG_PARENT_BLOCKING,
    },
    {
        "name": "pl-wrong-parent-gate-passed",
        "agent": "planner",
        "category": "wrong parent key",
        "text": PL_WRONG_PARENT_GATE_PASSED,
    },
    {
        "name": "pl-wrong-parent-plan-file",
        "agent": "planner",
        "category": "wrong parent key",
        "text": PL_WRONG_PARENT_PLAN_FILE,
    },
]


def run_comparison() -> List[dict]:
    """Run every fixture through both the new (real validator) and old
    (naive presence-only proxy) paths. Returns one row dict per fixture."""
    rows = []
    for fx in FIXTURES:
        schema = SCHEMAS[fx["agent"]]
        parsed = parse_block_yaml(fx["text"])
        new_result = validate_contract(parsed, schema)
        old_result = old_path_naive_presence_check(fx["text"], schema)
        would_have_missed = old_result["valid"] and not new_result["valid"]
        rows.append({
            "name": fx["name"],
            "agent": fx["agent"],
            "category": fx["category"],
            "new_valid": new_result["valid"],
            "old_valid": old_result["valid"],
            "would_have_missed": would_have_missed,
        })
    return rows


def build_summary(rows: List[dict]) -> dict:
    total = len(rows)
    new_caught = sum(1 for r in rows if not r["new_valid"])
    old_missed = sum(1 for r in rows if r["would_have_missed"])
    return {"total": total, "new_caught": new_caught, "old_missed": old_missed}


def render_report(rows: List[dict], summary: dict) -> str:
    lines = [
        "# Contract Validation Impact: Before/After the Schema-Validation Pilot",
        "",
        "**Date:** 2026-09-26",
        "**Pilot commit:** `53ac77fc` (`craftflow_contract_validate_pilot.py`)",
        "",
        "## Honesty Disclosure",
        "",
        HONESTY_DISCLOSURE,
        "",
        "## Per-Fixture Results",
        "",
        "| Fixture | Agent | Violation Category | New Path (real validator) | "
        "Old Path (naive presence proxy) | Would Old Path Have Missed It? |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        new_label = "valid" if r["new_valid"] else "invalid"
        old_label = "valid" if r["old_valid"] else "invalid"
        missed_label = "yes" if r["would_have_missed"] else "no"
        lines.append(
            f"| {r['name']} | {r['agent']} | {r['category']} | {new_label} | "
            f"{old_label} | {missed_label} |"
        )
    lines.extend([
        "",
        "## Summary",
        "",
        f"- Total malformed fixtures: {summary['total']}",
        f"- Correctly caught by the new validator (real, shipped): "
        f"{summary['new_caught']} / {summary['total']}",
        f"- Falsely reported valid by the naive old-path proxy (would have "
        f"been missed pre-pilot): {summary['old_missed']} / {summary['total']}",
        "",
        "**Reproducing this report:** "
        "`python3 scripts/craftflow_contract_validate_impact.py` "
        "(run from `tools/craftflow-plugin/plugins/craftflow/`).",
        "",
    ])
    return "\n".join(lines)


def print_summary(summary: dict) -> None:
    print("Contract validation impact summary:")
    print(f"  total malformed fixtures:        {summary['total']}")
    print(f"  caught by new validator:         {summary['new_caught']} / {summary['total']}")
    print(f"  missed by naive old-path proxy:  {summary['old_missed']} / {summary['total']}")


def main() -> int:
    rows = run_comparison()
    summary = build_summary(rows)
    report_text = render_report(rows, summary)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    print_summary(summary)
    print(f"Report written to: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
