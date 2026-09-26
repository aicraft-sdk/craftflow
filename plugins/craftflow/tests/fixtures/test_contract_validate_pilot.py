#!/usr/bin/env python3
"""
Fixture-based unit tests for craftflow_contract_validate_pilot.py

Distinct from tests/fixtures/test_contract_validate.py, which tests the
older, still-dormant flat-envelope craftflow_contract_validate.py. Do not
merge these two test files or their fixtures.

Run from the plugin root:
    python3 tests/fixtures/test_contract_validate_pilot.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../scripts"))

import craftflow_contract_validate_pilot as validator_mod
from craftflow_contract_validate_pilot import (
    parse_block_yaml,
    validate_contract,
    SCHEMAS_DIR,
)

PASS = 0
FAIL = 0


def check(name, actual, expected):
    global PASS, FAIL
    if actual == expected:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        print(f"    expected: {expected!r}")
        print(f"    actual:   {actual!r}")
        FAIL += 1


def violation_fields(result):
    return sorted(v["field"] for v in result["violations"])


def load_schema(agent):
    return json.loads((SCHEMAS_DIR / f"{agent}.json").read_text(encoding="utf-8"))


BUILDER_SCHEMA = load_schema("component-builder")
PLANNER_SCHEMA = load_schema("planner")

VALID_BUILDER_YAML = """\
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
  SUMMARY: "built the contract validator fixture"
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

VALID_PLANNER_YAML = """\
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
  SUMMARY: "planned the thing"
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

# ---------------------------------------------------------------------------
# 1. Valid contract passes (both agents)
# ---------------------------------------------------------------------------
print("\n[valid contracts pass]")
result = validate_contract(parse_block_yaml(VALID_BUILDER_YAML), BUILDER_SCHEMA)
check("valid component-builder contract passes", result["valid"], True)
check("valid component-builder contract has zero violations", result["violations"], [])

result = validate_contract(parse_block_yaml(VALID_PLANNER_YAML), PLANNER_SCHEMA)
check("valid planner contract passes", result["valid"], True)
check("valid planner contract has zero violations", result["violations"], [])

# ---------------------------------------------------------------------------
# 1b. Inline non-empty flow-style arrays parse as real lists, not strings
#     (regression test for a parser bug caught during this plan's own
#     adversarial review -- see plan's Fresh Review Resolution: a naive
#     empty-`[]`-only scalar coercion silently misclassified a genuinely
#     valid, common LLM-written shape like `ASSUMPTIONS: ["a", "b"]` as a
#     wrong-typed string instead of an array)
# ---------------------------------------------------------------------------
print("\n[inline non-empty flow arrays parse as lists]")
INLINE_ARRAY_BUILDER_YAML = VALID_BUILDER_YAML.replace(
    "  ASSUMPTIONS: []\n",
    '  ASSUMPTIONS: ["short list assumption", "another one"]\n',
)
parsed = parse_block_yaml(INLINE_ARRAY_BUILDER_YAML)
check(
    "inline non-empty ASSUMPTIONS parses as a real list",
    parsed["rationale"]["ASSUMPTIONS"],
    ["short list assumption", "another one"],
)
result = validate_contract(parsed, BUILDER_SCHEMA)
check("contract with inline non-empty ASSUMPTIONS still validates", result["valid"], True)

# ---------------------------------------------------------------------------
# 2. Missing required verdict field fails
# ---------------------------------------------------------------------------
print("\n[missing required verdict field fails]")
missing_status = VALID_BUILDER_YAML.replace("  STATUS: PASS\n", "")
result = validate_contract(parse_block_yaml(missing_status), BUILDER_SCHEMA)
check("missing verdict.STATUS fails", result["valid"], False)
check("missing verdict.STATUS reports STATUS violation", "STATUS" in violation_fields(result), True)

# ---------------------------------------------------------------------------
# 3. Wrong-typed field fails
# ---------------------------------------------------------------------------
print("\n[wrong-typed field fails]")
wrong_type = VALID_BUILDER_YAML.replace("  CONFIDENCE: 90\n", '  CONFIDENCE: "high"\n')
result = validate_contract(parse_block_yaml(wrong_type), BUILDER_SCHEMA)
check("wrong-typed CONFIDENCE fails", result["valid"], False)
check("wrong-typed CONFIDENCE reports CONFIDENCE violation", "CONFIDENCE" in violation_fields(result), True)

# ---------------------------------------------------------------------------
# 4. Invalid enum value fails
# ---------------------------------------------------------------------------
print("\n[invalid enum value fails]")
bad_enum = VALID_BUILDER_YAML.replace("  STATUS: PASS\n", "  STATUS: MAYBE\n")
result = validate_contract(parse_block_yaml(bad_enum), BUILDER_SCHEMA)
check("invalid STATUS enum value fails", result["valid"], False)
check("invalid STATUS enum reports STATUS violation", "STATUS" in violation_fields(result), True)

bad_plan_mode = VALID_PLANNER_YAML.replace("  PLAN_MODE: execution_plan\n", "  PLAN_MODE: yolo\n")
result = validate_contract(parse_block_yaml(bad_plan_mode), PLANNER_SCHEMA)
check("invalid planner PLAN_MODE enum value fails", result["valid"], False)
check("invalid PLAN_MODE enum reports PLAN_MODE violation", "PLAN_MODE" in violation_fields(result), True)

# ---------------------------------------------------------------------------
# 5. Field nested under the wrong parent key fails
# ---------------------------------------------------------------------------
print("\n[wrong-parent-key fails]")
WRONG_PARENT_YAML = """\
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
  SUMMARY: "built the thing"
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
result = validate_contract(parse_block_yaml(WRONG_PARENT_YAML), BUILDER_SCHEMA)
check("PHASE_EXIT_READY nested under rationale (wrong parent) fails", result["valid"], False)
check(
    "wrong-parent PHASE_EXIT_READY reports PHASE_EXIT_READY violation",
    "PHASE_EXIT_READY" in violation_fields(result),
    True,
)

# ---------------------------------------------------------------------------
# 6. Malformed / non-contract input fails closed
# ---------------------------------------------------------------------------
print("\n[malformed input fails closed]")
result = validate_contract(parse_block_yaml("not even yaml: [unbalanced"), BUILDER_SCHEMA)
check("garbage input (no verdict/rationale) fails", result["valid"], False)

# ---------------------------------------------------------------------------
# 7. Missing/corrupt schema file fails closed (main()-level, in-process)
# ---------------------------------------------------------------------------
print("\n[missing schema file fails closed]")


def run_main_with_stdin(argv, stdin_text):
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin_text)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = validator_mod.main(argv)
    finally:
        sys.stdin = old_stdin
    return rc, json.loads(buf.getvalue().strip())


with tempfile.TemporaryDirectory() as tmp:
    original_schemas_dir = validator_mod.SCHEMAS_DIR
    validator_mod.SCHEMAS_DIR = Path(tmp)  # empty dir -- no component-builder.json present
    try:
        rc, result = run_main_with_stdin(["--agent", "component-builder", "--stdin"], VALID_BUILDER_YAML)
    finally:
        validator_mod.SCHEMAS_DIR = original_schemas_dir
check("missing schema file fails closed (exit code 1)", rc, 1)
check("missing schema file fails closed (valid=false)", result["valid"], False)

# ---------------------------------------------------------------------------
# 8. CLI subprocess smoke test (exit codes + stdout JSON shape)
# ---------------------------------------------------------------------------
print("\n[CLI subprocess smoke test]")
SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "../../scripts/craftflow_contract_validate_pilot.py")


def run_cli(agent, text):
    proc = subprocess.run(
        [sys.executable, SCRIPT_PATH, "--agent", agent, "--stdin"],
        input=text,
        capture_output=True,
        text=True,
    )
    return proc.returncode, json.loads(proc.stdout)


rc, result = run_cli("component-builder", VALID_BUILDER_YAML)
check("CLI exit code 0 for valid component-builder contract", rc, 0)
check("CLI reports valid=true for valid component-builder contract", result["valid"], True)

rc, result = run_cli("component-builder", bad_enum)
check("CLI exit code 1 for invalid contract", rc, 1)
check("CLI reports valid=false for invalid contract", result["valid"], False)

rc, result = run_cli("planner", VALID_PLANNER_YAML)
check("CLI exit code 0 for valid planner contract", rc, 0)
check("CLI reports valid=true for valid planner contract", result["valid"], True)

# ---------------------------------------------------------------------------
# 9. Real-template-shaped fixture (component-builder) parses and validates
# ---------------------------------------------------------------------------
print("\n[real component-builder template shape validates]")
REAL_SHAPE_BUILDER_YAML = """\
verdict:
  STATUS: PASS
  CONFIDENCE: 95
  PHASE_ID: "phase-2-restructure"
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
  SUMMARY: "restructured component-builder contract into verdict/rationale"
  INPUTS: []
  EXPECTED_ARTIFACTS: ["agents/component-builder.md"]
  SCENARIOS:
    - name: "harness audit still passes after nesting"
      given: "component-builder.md has the new nested YAML block"
      when: "craftflow_harness_audit.py runs"
      then: "exits 0"
      command: "python3 scripts/craftflow_harness_audit.py"
      expected: "craftflow_harness_audit: OK"
      actual: "craftflow_harness_audit: OK"
      exit_code: 0
      status: PASS
  ASSUMPTIONS: []
  DECISIONS: ["nest fields under verdict/rationale without renaming any field"]
  BLOCKED_ITEMS: []
  SKIPPED_ITEMS: []
  SCOPE_INCREASES: []
  REMEDIATION_REASON: null
  MEMORY_NOTES:
    learnings: ["field-name substring checks are indentation-insensitive"]
    patterns: []
    verification: ["harness_audit: OK", "worldclass_benchmark: OK"]
    deferred: []
"""
result = validate_contract(parse_block_yaml(REAL_SHAPE_BUILDER_YAML), BUILDER_SCHEMA)
check("real-shape component-builder fixture passes", result["valid"], True)
check("real-shape component-builder fixture has zero violations", result["violations"], [])

# ---------------------------------------------------------------------------
# 10. Real-template-shaped fixture (planner) parses and validates
# ---------------------------------------------------------------------------
print("\n[real planner template shape validates]")
REAL_SHAPE_PLANNER_YAML = """\
verdict:
  STATUS: PLAN_CREATED
  PLAN_MODE: execution_plan
  VERIFICATION_RIGOR: standard
  CONFIDENCE: 88
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
  SUMMARY: "planned the schema-validation pilot"
  PLAN_FILE: "docs/plans/2026-09-18-schema-validation-router-contrac-plan.md"
  LIVING_SPEC_IMPACTED: null
  SCENARIOS:
    - name: "valid contract passes"
      given: "a well-formed nested contract"
      when: "the validator runs"
      then: "valid=true, zero violations"
  ASSUMPTIONS: []
  DECISIONS: ["nest fields under verdict/rationale without renaming any field"]
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
result = validate_contract(parse_block_yaml(REAL_SHAPE_PLANNER_YAML), PLANNER_SCHEMA)
check("real-shape planner fixture passes", result["valid"], True)
check("real-shape planner fixture has zero violations", result["violations"], [])

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
