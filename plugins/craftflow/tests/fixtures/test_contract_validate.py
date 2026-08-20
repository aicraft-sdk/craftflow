#!/usr/bin/env python3
"""
Fixture-based unit tests for craftflow_contract_validate.py

Run from the plugin root:
    python3 tests/fixtures/test_contract_validate.py
"""
import sys
import os
import json

# Allow importing scripts from scripts/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../scripts"))

from craftflow_contract_validate import (
    extract_yaml_block,
    parse_yaml_fields,
    validate_contract,
    VALID_GAP_TYPES,
    VALID_GAP_SEVERITIES,
    REQUIRED_FIELDS,
)

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "../../contracts/agent_contract.schema.json"
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
SUMMARY: "built the contract validator fixture"
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
SUMMARY: "planned the contract validator fix"
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

result = validate_contract(BUILDER_TEXT, "component-builder")
check("valid=True for complete builder contract", result["valid"], True)
check("errors=[] for complete builder contract", result["errors"], [])

# ---------------------------------------------------------------------------
# test_missing_status
# ---------------------------------------------------------------------------
print("\n[test_missing_status]")

result = validate_contract(BUILDER_TEXT_NO_STATUS, "component-builder")
check("valid=False when STATUS missing", result["valid"], False)
check_contains("errors mention STATUS", result["errors"], "STATUS")

# ---------------------------------------------------------------------------
# test_missing_phase_id
# ---------------------------------------------------------------------------
print("\n[test_missing_phase_id]")

result = validate_contract(BUILDER_TEXT_NO_PHASE_ID, "component-builder")
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

result = validate_contract(BUILDER_TEXT_INVALID_STATUS, "component-builder")
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
# test_gap_classification_constants
# ---------------------------------------------------------------------------
print("\n[test_gap_classification_constants]")

check("VALID_GAP_TYPES contains Missing", "Missing" in VALID_GAP_TYPES, True)
check("VALID_GAP_TYPES contains Partial", "Partial" in VALID_GAP_TYPES, True)
check("VALID_GAP_TYPES contains Contradicts", "Contradicts" in VALID_GAP_TYPES, True)
check("VALID_GAP_TYPES contains Unrequested", "Unrequested" in VALID_GAP_TYPES, True)
check("VALID_GAP_SEVERITIES contains CRITICAL", "CRITICAL" in VALID_GAP_SEVERITIES, True)
check("VALID_GAP_SEVERITIES contains HIGH", "HIGH" in VALID_GAP_SEVERITIES, True)

# ---------------------------------------------------------------------------
# test_gap_classification_valid
# ---------------------------------------------------------------------------
print("\n[test_gap_classification_valid]")

VERIFIER_WITH_GAPS = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: PASS
SCENARIOS:
  - name: "verification test"
BLOCKING: false
REMEDIATION_NEEDED: false
GAP_CLASSIFICATION:
  - Missing | CRITICAL | FR-001 not implemented
  - Unrequested | HIGH | extra /admin endpoint added without plan approval
```
"""

result = validate_contract(VERIFIER_WITH_GAPS, "verifier")
check("valid=True for verifier with valid GAP_CLASSIFICATION", result["valid"], True)
check("errors=[] for verifier with valid GAP_CLASSIFICATION", result["errors"], [])

# ---------------------------------------------------------------------------
# test_gap_classification_missing_type
# ---------------------------------------------------------------------------
print("\n[test_gap_classification_missing_type]")

VERIFIER_GAP_NO_TYPE = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: PASS
SCENARIOS:
  - name: "test"
BLOCKING: false
REMEDIATION_NEEDED: false
GAP_CLASSIFICATION:
  - CRITICAL | FR-001 not implemented
```
"""

result = validate_contract(VERIFIER_GAP_NO_TYPE, "verifier")
check("valid=False when GAP_CLASSIFICATION item missing type", result["valid"], False)
check_contains("errors mention GAP_CLASSIFICATION type", result["errors"], "GAP_CLASSIFICATION")

# ---------------------------------------------------------------------------
# test_gap_classification_missing_severity
# ---------------------------------------------------------------------------
print("\n[test_gap_classification_missing_severity]")

VERIFIER_GAP_NO_SEVERITY = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: PASS
SCENARIOS:
  - name: "test"
BLOCKING: false
REMEDIATION_NEEDED: false
GAP_CLASSIFICATION:
  - Missing | FR-001 not implemented
```
"""

result = validate_contract(VERIFIER_GAP_NO_SEVERITY, "verifier")
check("valid=False when GAP_CLASSIFICATION item missing severity", result["valid"], False)
check_contains("errors mention severity", result["errors"], "severity")

# ---------------------------------------------------------------------------
# test_gap_classification_no_false_positive_on_partial_word
# ---------------------------------------------------------------------------
print("\n[test_gap_classification_no_false_positive_on_partial_word]")

# "partially done" must NOT match the "Partial" gap type — only exact first-token match counts
VERIFIER_GAP_FALSE_POSITIVE = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: PASS
SCENARIOS:
  - name: "test"
BLOCKING: false
REMEDIATION_NEEDED: false
GAP_CLASSIFICATION:
  - partially done | CRITICAL | FR-001 not fully implemented
```
"""

result = validate_contract(VERIFIER_GAP_FALSE_POSITIVE, "verifier")
check("valid=False when type is 'partially done' (substring match must not fire)",
      result["valid"], False)
check_contains("errors flag the bad type token", result["errors"], "GAP_CLASSIFICATION")

# ---------------------------------------------------------------------------
# test_valid_skill_author_contract
# ---------------------------------------------------------------------------
print("\n[test_valid_skill_author_contract]")

SKILL_AUTHOR_COMPLETE_TEXT = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: COMPLETE
SUMMARY: "proposed a new skill for repo-conductor lock retry backoff"
CANDIDATE_ID: "cand-042"
DEDUP_RESULT: new
PROPOSAL_PATH: ".craftflow/state/project/skill-proposals/cand-042/"
SKIP_REASON: ""
```
"""

result = validate_contract(SKILL_AUTHOR_COMPLETE_TEXT, "skill-author")
check("valid=True for complete skill-author contract", result["valid"], True)
check("errors=[] for complete skill-author contract", result["errors"], [])

# ---------------------------------------------------------------------------
# test_skipped_skill_author_missing_proposal_path_still_valid
# ---------------------------------------------------------------------------
print("\n[test_skipped_skill_author_missing_proposal_path_still_valid]")

SKILL_AUTHOR_SKIPPED_TEXT = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: SKIPPED
SUMMARY: "no candidate was gate-eligible this round"
CANDIDATE_ID: "null"
DEDUP_RESULT: none
SKIP_REASON: "rubric rule 3 failed: pattern seen in only 1 example"
```
"""

result = validate_contract(SKILL_AUTHOR_SKIPPED_TEXT, "skill-author")
check("valid=True for SKIPPED skill-author with no PROPOSAL_PATH (conditional field)",
      result["valid"], True)
check("errors=[] for SKIPPED skill-author with no PROPOSAL_PATH", result["errors"], [])

# ---------------------------------------------------------------------------
# test_valid_researcher_contracts
# ---------------------------------------------------------------------------
print("\n[test_valid_researcher_contracts]")

WEB_RESEARCHER_TEXT = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: COMPLETE
SUMMARY: "researched retry-backoff libraries and found exponential-backoff to be the standard"
FILE_PATH: docs/research/2026-08-20-retry-backoff.md
QUALITY_LEVEL: high
BLOCKING: false
```
"""

GITHUB_RESEARCHER_TEXT = """\
### Router Contract (MACHINE-READABLE)
```yaml
STATUS: COMPLETE
SUMMARY: "surveyed 3 OSS implementations of lock-retry backoff on GitHub"
FILE_PATH: docs/research/2026-08-20-lock-retry-survey.md
QUALITY_LEVEL: high
BLOCKING: false
```
"""

result = validate_contract(WEB_RESEARCHER_TEXT, "web-researcher")
check("valid=True for complete web-researcher contract", result["valid"], True)
check("errors=[] for complete web-researcher contract", result["errors"], [])

result = validate_contract(GITHUB_RESEARCHER_TEXT, "github-researcher")
check("valid=True for complete github-researcher contract", result["valid"], True)
check("errors=[] for complete github-researcher contract", result["errors"], [])

# ---------------------------------------------------------------------------
# test_required_fields_matches_json_schema (structural cross-check)
# ---------------------------------------------------------------------------
print("\n[test_required_fields_matches_json_schema]")

with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
    _schema = json.load(f)

_schema_overlays = _schema.get("kind_overlays", {})

check(
    "REQUIRED_FIELDS kind keys match agent_contract.schema.json kind_overlays keys",
    sorted(REQUIRED_FIELDS.keys()),
    sorted(_schema_overlays.keys()),
)

_mismatched_kinds = []
for _kind, _fields in REQUIRED_FIELDS.items():
    _overlay = _schema_overlays.get(_kind, {})
    _schema_fields = _overlay.get("required_fields", [])
    if sorted(_fields) != sorted(_schema_fields):
        _mismatched_kinds.append(_kind)

check(
    "every kind's required_fields list matches between .py and .json (no mismatched kinds)",
    _mismatched_kinds,
    [],
)

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
