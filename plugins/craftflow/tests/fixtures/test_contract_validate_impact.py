#!/usr/bin/env python3
"""
Fixture-based unit tests for craftflow_contract_validate_impact.py's OWN
comparison logic (old_path_naive_presence_check + the would-have-missed-it
contrast). Distinct from tests/fixtures/test_contract_validate_pilot.py,
which tests craftflow_contract_validate_pilot.py's real validator. This file
does not re-test that validator.

Run from the plugin root:
    python3 tests/fixtures/test_contract_validate_impact.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../scripts"))

from craftflow_contract_validate_pilot import (
    SCHEMAS_DIR,
    parse_block_yaml,
    validate_contract,
)
from craftflow_contract_validate_impact import (
    old_path_naive_presence_check,
    run_comparison,
    build_summary,
    FIXTURES,
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


def load_schema(agent):
    return json.loads((SCHEMAS_DIR / f"{agent}.json").read_text(encoding="utf-8"))


BUILDER_SCHEMA = load_schema("component-builder")

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
  SCENARIOS: []
  ASSUMPTIONS: []
  DECISIONS: []
  BLOCKED_ITEMS: []
  SKIPPED_ITEMS: []
  SCOPE_INCREASES: []
  REMEDIATION_REASON: null
  MEMORY_NOTES: {}
"""

# ---------------------------------------------------------------------------
# 1. Missing required field: old path should ALSO say invalid (both agree).
#    Confirms old_path_naive_presence_check correctly flags an actually-
#    absent key -- it is not broken in the direction of over-flagging.
# ---------------------------------------------------------------------------
print("\n[missing field: both paths agree it's invalid]")
missing_tdd_red_exit = VALID_BUILDER_YAML.replace("  TDD_RED_EXIT: 1\n", "")
old_result = old_path_naive_presence_check(missing_tdd_red_exit, BUILDER_SCHEMA)
new_result = validate_contract(parse_block_yaml(missing_tdd_red_exit), BUILDER_SCHEMA)
check("old path reports invalid when key substring is truly absent", old_result["valid"], False)
check("new path also reports invalid for the same fixture", new_result["valid"], False)
check(
    "old path reason names the missing key",
    "TDD_RED_EXIT" in old_result["reason"],
    True,
)

# ---------------------------------------------------------------------------
# 2. Wrong-typed field, key name still present as substring: old path says
#    "valid" (missed), new path says "invalid" (caught). Core
#    "would-have-missed-it" case.
# ---------------------------------------------------------------------------
print("\n[wrong type: old path misses it, new path catches it]")
wrong_type = VALID_BUILDER_YAML.replace("  CONFIDENCE: 90\n", '  CONFIDENCE: "very high"\n')
old_result = old_path_naive_presence_check(wrong_type, BUILDER_SCHEMA)
new_result = validate_contract(parse_block_yaml(wrong_type), BUILDER_SCHEMA)
check("old path reports valid despite wrong-typed CONFIDENCE (missed)", old_result["valid"], True)
check("new path reports invalid for wrong-typed CONFIDENCE (caught)", new_result["valid"], False)

# ---------------------------------------------------------------------------
# 3. Bad enum value, key name still present as substring: old path says
#    "valid" (missed), new path says "invalid" (caught). Another
#    "would-have-missed-it" case.
# ---------------------------------------------------------------------------
print("\n[bad enum: old path misses it, new path catches it]")
bad_enum = VALID_BUILDER_YAML.replace("  STATUS: PASS\n", "  STATUS: MAYBE\n")
old_result = old_path_naive_presence_check(bad_enum, BUILDER_SCHEMA)
new_result = validate_contract(parse_block_yaml(bad_enum), BUILDER_SCHEMA)
check("old path reports valid despite invalid STATUS enum (missed)", old_result["valid"], True)
check("new path reports invalid for invalid STATUS enum (caught)", new_result["valid"], False)

# ---------------------------------------------------------------------------
# 4. run_comparison() / build_summary() wiring: the would_have_missed flag
#    and aggregate counts reflect the per-fixture old/new contrast correctly
#    across the full real fixture batch (not just the 3 hand-checked cases
#    above).
# ---------------------------------------------------------------------------
print("\n[run_comparison + build_summary wiring over the real fixture batch]")
rows = run_comparison()
check("run_comparison produces one row per fixture", len(rows), len(FIXTURES))

for row in rows:
    expected_missed = row["old_valid"] and not row["new_valid"]
    check(
        f"would_have_missed matches old_valid+new_valid for {row['name']}",
        row["would_have_missed"],
        expected_missed,
    )

summary = build_summary(rows)
new_caught = sum(1 for r in rows if not r["new_valid"])
old_missed = sum(1 for r in rows if r["would_have_missed"])
check("build_summary total matches fixture count", summary["total"], len(rows))
check("build_summary new_caught matches manual count", summary["new_caught"], new_caught)
check("build_summary old_missed matches manual count", summary["old_missed"], old_missed)
check("at least one fixture demonstrates a real miss (non-zero old_missed)", old_missed > 0, True)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
