#!/usr/bin/env python3
"""Pins for the test-value gate (adapted from testectomy, MIT; see NOTICE).

Every new or changed test must name the rule it guards, the regression that makes it fail, and why existing coverage
misses it. The gate lives in the TDD skill (core pointer + reference), the component-builder's Router Contract
(`TEST_GATE`, optional for one release) and the code-reviewer's `low-value-test` finding category. Coverage is
reported, not a default gate.

Run from anywhere: python3 plugins/craftflow/tests/fixtures/test_test_value_gate.py
"""
import importlib
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "scripts"))
_pilot = importlib.import_module("craftflow_contract_validate_pilot")
parse_block_yaml, validate_contract = _pilot.parse_block_yaml, _pilot.validate_contract

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


def read(rel):
    return (PLUGIN / rel).read_text(encoding="utf-8")


tdd_core = read("skills/test-driven-development/SKILL.md")
gate_ref_path = PLUGIN / "skills/test-driven-development/references/test-value-gate.md"
gate_ref = gate_ref_path.read_text(encoding="utf-8") if gate_ref_path.is_file() else ""
coverage_ref = read("skills/test-driven-development/references/coverage-and-design.md")
builder = read("agents/component-builder.md")
reviewer = read("agents/code-reviewer.md")
schema = json.loads(read("scripts/schemas/component-builder.json"))
hook_tests = read("scripts/craftflow_hook_unit_tests.py")
notice = (PLUGIN.parents[1] / "NOTICE").read_text(encoding="utf-8")

print("[tdd core]")
check("core points at references/test-value-gate.md", "references/test-value-gate.md" in tdd_core)
check("core names the gate", "test-value gate" in tdd_core)
check("core keeps the Iron Law", "NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST" in tdd_core)
check("core no longer makes 80% coverage the completion bar", "coverage >= 80%" not in tdd_core)
check("core stays inside its 3400-byte split-skill budget", len(tdd_core.encode("utf-8")) <= 3400,
      f"{len(tdd_core.encode('utf-8'))} bytes")
check("hook unit tests pin the new hot-path phrase", '"Before adding or changing a test: pass the test-value gate"' in hook_tests)
check("hook unit tests drop the old coverage phrase", '"Before claiming the phase complete: coverage >= 80%"' not in hook_tests)

print("[gate reference]")
check("reference exists", bool(gate_ref))
for q in ("Which rule does it guard?", "What credible regression makes it fail?",
          "Why doesn't existing coverage catch it?", "Does it need a production seam"):
    check(f"gate question: {q}", q in gate_ref)
for bar in ("External boundary", "Costly or irreversible failure", "Hard to verify by reading",
            "Large input space", "A regression that actually happened", "Public contract"):
    check(f"keep bar: {bar}", bar in gate_ref)
for junk in ("It depends on the past", "It depends on nothing", "It depends on the implementation",
             "It duplicates another test", "It passes by hiding a flake"):
    check(f"junk pattern: {junk}", junk in gate_ref)
check("reflex trap: no test that removed code stays gone", "stays gone" in gate_ref)
check("flake fix: no sleeps, retries or raised timeouts", "raised timeout" in gate_ref)
check("disagreement with intent becomes an expected failure, not a green test", "expected failure" in gate_ref)
check("credits testectomy", "testectomy" in gate_ref)
check("NOTICE credits testectomy", "felipebasurto/testectomy" in notice)

print("[coverage reference]")
check("coverage is advisory, not a completion gate", "Below threshold? Add missing tests before claiming completion." not in coverage_ref)
check("coverage reference points at the gate", "test-value-gate.md" in coverage_ref)

print("[component-builder]")
check("builder contract carries TEST_GATE", "TEST_GATE:" in builder)
check("builder process runs the gate before RED", "test-value gate" in builder)
spec = schema.get("TEST_GATE", {})
check("schema: TEST_GATE is an optional rationale array",
      spec == {"type": "array", "required": False, "nested_under": "rationale"}, repr(spec))

print("[pilot validator]")
# Same body as test_contract_validate_pilot.py's VALID_BUILDER_YAML (copied, not imported: importing runs that suite).
BODY = """\
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
without = validate_contract(parse_block_yaml(BODY), schema)
check("a contract without TEST_GATE stays valid (optional for one release)", without["valid"], without)
with_list = BODY.replace("rationale:\n", 'rationale:\n  TEST_GATE: ["tests/test_cart.py::test_SC_001 | SC-001 | total goes negative | 2 money"]\n', 1)
ok = validate_contract(parse_block_yaml(with_list), schema)
check("a TEST_GATE list is valid", ok["valid"], ok)
with_str = BODY.replace("rationale:\n", 'rationale:\n  TEST_GATE: "none"\n', 1)
bad = validate_contract(parse_block_yaml(with_str), schema)
check("a TEST_GATE string is rejected", not bad["valid"] and any(v["field"] == "TEST_GATE" for v in bad["violations"]), bad)

print("[code-reviewer]")
check("reviewer has the low-value-test category", "low-value-test" in reviewer)

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
