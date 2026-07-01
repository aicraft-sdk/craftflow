#!/usr/bin/env python3
"""
Fixture-based unit tests for craftflow_contract_validate.py

Run from the plugin root:
    python3 tests/fixtures/test_contract_validate.py
"""
import sys
import os

# Allow importing scripts from scripts/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../scripts"))

from craftflow_contract_validate import (
    extract_yaml_block,
    parse_yaml_fields,
    validate_contract,
)

PASS = 0
FAIL = 0


def check(name: str, actual, expected):
    global PASS, FAIL
    if actual == expected:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        print(f"    expected: {expected!r}")
        print(f"    actual:   {actual!r}")
        FAIL += 1


def check_contains(name: str, haystack, needle):
    """Check that needle appears somewhere in haystack (list or string)."""
    global PASS, FAIL
    found = False
    if isinstance(haystack, list):
        found = any(needle in item for item in haystack)
    elif isinstance(haystack, str):
        found = needle in haystack
    if found:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        print(f"    expected to find: {needle!r}")
        print(f"    in: {haystack!r}")
        FAIL += 1


# ---------------------------------------------------------------------------
# Shared test fixtures — agent output text fragments
# ---------------------------------------------------------------------------

BUILDER_YAML = """\
STATUS: PASS
CONFIDENCE: 90
PHASE_ID: track3
PHASE_STATUS: completed
PHASE_EXIT_READY: true
PROOF_STATUS: passed
TDD_RED_EXIT: 1
TDD_GREEN_EXIT: 0
SCENARIOS:
  - name: "valid builder test"
BLOCKING: false
REMEDIATION_NEEDED: false
"""

BUILDER_TEXT = """\
## Built: contract validator

Some prose here.

### Router Contract (MACHINE-READABLE)
```yaml
""" + BUILDER_YAML + """```
"""

BUILDER_TEXT_NO_STATUS = """\
### Router Contract (MACHINE-READABLE)
```yaml
CONFIDENCE: 90
PHASE_ID: track3
PHASE_STATUS: completed
PHASE_EXIT_READY: true
PROOF_STATUS: passed
TDD_RED_EXIT: 1
TDD_GREEN_EXIT: 0
SCENARIOS:
  - name: "test"
BLOCKING: false
REMEDIATION_NEEDED: false
```
"""

BUILDER_TEXT_NO_PHASE_ID = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: PASS
CONFIDENCE: 90
PHASE_STATUS: completed
PHASE_EXIT_READY: true
PROOF_STATUS: passed
TDD_RED_EXIT: 1
TDD_GREEN_EXIT: 0
SCENARIOS:
  - name: "test"
BLOCKING: false
REMEDIATION_NEEDED: false
```
"""

VERIFIER_TEXT = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: PASS
SCENARIOS:
  - name: "verification test"
BLOCKING: false
REMEDIATION_NEEDED: false
```
"""

PLANNER_TEXT = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: PASS
PLAN_FILE: docs/plans/my-plan.md
PLAN_MODE: full
CONFIDENCE: 85
GATE_PASSED: true
OPEN_DECISIONS: []
SCENARIOS:
  - name: "planner test"
BLOCKING: false
REMEDIATION_NEEDED: false
```
"""

PLAIN_TEXT = """\
Some output without any router contract section.
Just regular prose here.
"""

BUILDER_TEXT_INVALID_STATUS = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: INVALID_VALUE
CONFIDENCE: 90
PHASE_ID: track3
PHASE_STATUS: completed
PHASE_EXIT_READY: true
PROOF_STATUS: passed
TDD_RED_EXIT: 1
TDD_GREEN_EXIT: 0
SCENARIOS:
  - name: "test"
BLOCKING: false
REMEDIATION_NEEDED: false
```
"""

UNKNOWN_KIND_TEXT = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: PASS
```
"""

# ---------------------------------------------------------------------------
# Tests for extract_yaml_block
# ---------------------------------------------------------------------------
print("\n[extract_yaml_block]")

block = extract_yaml_block(BUILDER_TEXT)
check("returns non-None for valid block", block is not None, True)
check("block contains STATUS", "STATUS:" in (block or ""), True)

check("returns None for plain text", extract_yaml_block(PLAIN_TEXT), None)

# ---------------------------------------------------------------------------
# Tests for parse_yaml_fields
# ---------------------------------------------------------------------------
print("\n[parse_yaml_fields]")

fields = parse_yaml_fields(BUILDER_YAML)
check("parses STATUS", fields.get("STATUS"), "PASS")
check("parses CONFIDENCE", fields.get("CONFIDENCE"), "90")
check("parses PHASE_ID", fields.get("PHASE_ID"), "track3")
check("SCENARIOS is a list", isinstance(fields.get("SCENARIOS"), list), True)

# ---------------------------------------------------------------------------
# test_valid_builder_contract
# ---------------------------------------------------------------------------
print("\n[test_valid_builder_contract]")

result = validate_contract(BUILDER_TEXT, "builder")
check("valid=True for complete builder contract", result["valid"], True)
check("errors=[] for complete builder contract", result["errors"], [])

# ---------------------------------------------------------------------------
# test_missing_status
# ---------------------------------------------------------------------------
print("\n[test_missing_status]")

result = validate_contract(BUILDER_TEXT_NO_STATUS, "builder")
check("valid=False when STATUS missing", result["valid"], False)
check_contains("errors mention STATUS", result["errors"], "STATUS")

# ---------------------------------------------------------------------------
# test_missing_phase_id
# ---------------------------------------------------------------------------
print("\n[test_missing_phase_id]")

result = validate_contract(BUILDER_TEXT_NO_PHASE_ID, "builder")
check("valid=False when PHASE_ID missing", result["valid"], False)
check_contains("errors mention PHASE_ID", result["errors"], "PHASE_ID")

# ---------------------------------------------------------------------------
# test_valid_verifier_contract
# ---------------------------------------------------------------------------
print("\n[test_valid_verifier_contract]")

result = validate_contract(VERIFIER_TEXT, "verifier")
check("valid=True for complete verifier contract", result["valid"], True)
check("errors=[] for verifier contract", result["errors"], [])

# ---------------------------------------------------------------------------
# test_valid_planner_contract
# ---------------------------------------------------------------------------
print("\n[test_valid_planner_contract]")

result = validate_contract(PLANNER_TEXT, "planner")
check("valid=True for complete planner contract", result["valid"], True)
check("errors=[] for planner contract", result["errors"], [])

# ---------------------------------------------------------------------------
# test_no_yaml_block
# ---------------------------------------------------------------------------
print("\n[test_no_yaml_block]")

result = validate_contract(PLAIN_TEXT, "builder")
check("valid=False when no YAML block", result["valid"], False)
check("errors=['no Router Contract YAML block found']",
      result["errors"], ["no Router Contract YAML block found"])

# ---------------------------------------------------------------------------
# test_invalid_status_value
# ---------------------------------------------------------------------------
print("\n[test_invalid_status_value]")

result = validate_contract(BUILDER_TEXT_INVALID_STATUS, "builder")
check("valid=False for invalid STATUS value", result["valid"], False)
check_contains("errors mention STATUS", result["errors"], "STATUS")

# ---------------------------------------------------------------------------
# test_unknown_kind
# ---------------------------------------------------------------------------
print("\n[test_unknown_kind]")

result = validate_contract(UNKNOWN_KIND_TEXT, "unknown_kind")
check("unknown kind: valid=True when STATUS present and valid",
      result["valid"], True)

# unknown kind with missing STATUS
result = validate_contract(BUILDER_TEXT_NO_STATUS, "unknown_kind")
check("unknown kind: valid=False when STATUS missing", result["valid"], False)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*40}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    print("FAIL")
    sys.exit(1)
else:
    print("PASS")
    sys.exit(0)
