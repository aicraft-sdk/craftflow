#!/usr/bin/env python3
"""Tests for rule check levels and targeted test runs (adapted from testectomy's spec rules, MIT; see NOTICE).

Every plan rule (`SC-###`) says how it is checked: `type`, `read` (the default) or `test`, and a `test` rule needs a
keep-bar reason from the test-value gate. scripts/craftflow_plan_rules.py reads those lines from a plan, refuses a
bad one, and prints the test filters for a phase so the verifier runs only the tests that phase cites.

Run from anywhere: python3 plugins/craftflow/tests/fixtures/test_plan_check_levels.py
"""
import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "craftflow_plan_rules.py"
sys.path.insert(0, str(PLUGIN / "scripts"))
rules = importlib.import_module("craftflow_plan_rules")

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


PLAN = """# Coupon plan

### Acceptance Checks
- SC-001 [check: test | phase: P1 | keep: a negative total charges the customer] A coupon larger than the subtotal makes the order free.
- SC-002 [check: read | phase: P1] The receipt lists the coupon code.
- SC-003 [check: type | phase: P2] A coupon amount is a whole number of cents.
- SC-004 [phase: P2] Expired coupons are refused with a message.
- SC-005 [check: test | phase: P2 | keep: double use loses money] A coupon works once per order.
"""

print("[parse]")
parsed = rules.parse_rules(PLAN)
check("reads every SC line", [r["id"] for r in parsed] == ["SC-001", "SC-002", "SC-003", "SC-004", "SC-005"], parsed)
check("check level defaults to read", parsed[3]["check"] == "read", parsed[3])
check("keeps the phase", parsed[0]["phase"] == "P1" and parsed[4]["phase"] == "P2", parsed)
check("keeps the keep-bar reason", parsed[0]["keep"] == "a negative total charges the customer", parsed[0])
check("keeps the rule text", parsed[1]["text"] == "The receipt lists the coupon code.", parsed[1])

print("[lint]")
check("a well-formed plan has no errors", rules.lint(parsed) == [], rules.lint(parsed))
bad = rules.parse_rules("- SC-009 [check: test | phase: P1] Totals never go negative.\n")
check("a test rule with no keep reason is an error", any("keep" in e for e in rules.lint(bad)), rules.lint(bad))
bad = rules.parse_rules("- SC-010 [check: unit] Totals never go negative.\n")
check("an unknown check level is an error", any("check level" in e for e in rules.lint(bad)), rules.lint(bad))
bad = rules.parse_rules("- SC-011 [check: read] `apply_coupon()` returns zero for big coupons.\n")
check("a rule naming an internal function is an error", any("names code" in e for e in rules.lint(bad)), rules.lint(bad))
dup = rules.parse_rules("- SC-001 [check: read] One.\n- SC-001 [check: read] Two.\n")
check("a duplicated id is an error", any("SC-001" in e and "twice" in e for e in rules.lint(dup)), rules.lint(dup))

print("[test filters]")
f = rules.filters(parsed, "P1")
check("only the phase's test rules are selected", f["ids"] == ["SC-001"], f)
check("pytest filter", f["pytest"] == "-k 'SC_001'", f)
check("jest/vitest filter", f["jest"] == "-t 'SC-001'", f)
check("go filter", f["go"] == "-run 'SC001'", f)
check("read rules are listed for one evidence line each", f["read"] == ["SC-002"], f)
f2 = rules.filters(parsed, "P2")
check("several ids join with or", f2["pytest"] == "-k 'SC_005'" and f2["type"] == ["SC-003"] and f2["read"] == ["SC-004"], f2)
check("a phase with no test rules selects nothing", rules.filters(parsed, "P9")["ids"] == [], rules.filters(parsed, "P9"))
two = rules.parse_rules("- SC-001 [check: test | phase: P1 | keep: x] A.\n- SC-002 [check: test | phase: P1 | keep: y] B.\n")
check("pytest joins two ids with or", rules.filters(two, "P1")["pytest"] == "-k 'SC_001 or SC_002'", rules.filters(two, "P1"))
check("jest joins two ids with a regex", rules.filters(two, "P1")["jest"] == "-t 'SC-001|SC-002'", rules.filters(two, "P1"))

print("[cli]")
tmp = Path(tempfile.mkdtemp(prefix="plan-rules-"))
(tmp / "plan.md").write_text(PLAN, encoding="utf-8")
proc = subprocess.run([sys.executable, str(SCRIPT), "--plan", str(tmp / "plan.md"), "--phase", "P1"], capture_output=True, text=True)
check("cli exits 0 and prints one JSON line", proc.returncode == 0 and len(proc.stdout.strip().splitlines()) == 1, proc.stderr)
check("cli prints the phase filters", json.loads(proc.stdout)["filters"]["ids"] == ["SC-001"], proc.stdout)
(tmp / "bad.md").write_text("- SC-009 [check: test] Totals never go negative.\n", encoding="utf-8")
proc = subprocess.run([sys.executable, str(SCRIPT), "--plan", str(tmp / "bad.md")], capture_output=True, text=True)
check("cli exits 1 on lint errors", proc.returncode == 1 and json.loads(proc.stdout)["errors"], proc.stdout)
proc = subprocess.run([sys.executable, str(SCRIPT), "--plan", str(tmp / "missing.md")], capture_output=True, text=True)
check("cli exits 2 on a missing plan", proc.returncode == 2, proc.returncode)

print("[wiring]")
planner = (PLUGIN / "agents/planner.md").read_text(encoding="utf-8")
gate = (PLUGIN / "skills/plan-review-gate/SKILL.md").read_text(encoding="utf-8")
builder = (PLUGIN / "agents/component-builder.md").read_text(encoding="utf-8")
verifier = (PLUGIN / "agents/integration-verifier.md").read_text(encoding="utf-8")
check("planner writes check levels", "[check: type | read | test" in planner)
check("planner rules never name internal functions", "never names an internal function" in planner)
check("plan-review gate runs the rule lint", "craftflow_plan_rules.py" in gate)
check("plan-review gate blocks a test rule with no keep reason", "no `keep:` reason" in gate)
check("builder names tests after the SC id", "test_SC_012" in builder and "-t SC-012" in builder)
check("builder writes code-vs-intent tests from intent", "from intent" in builder)
check("verifier runs only the phase's cited tests", "craftflow_plan_rules.py" in verifier and "--phase" in verifier)
check("verifier runs the full suite once on the final phase", "full suite once" in verifier)
check("verifier gives read rules one evidence line", "re-read against the diff" in verifier)
for name, text in (("planner", planner), ("builder", builder), ("verifier", verifier)):
    check(f"{name} stays under the 500-line cap", text.count("\n") < 500, text.count("\n"))

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
