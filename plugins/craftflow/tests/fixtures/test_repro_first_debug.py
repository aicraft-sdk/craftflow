#!/usr/bin/env python3
"""Pins for repro-first DEBUG (adapted from mattpocock/skills diagnosing-bugs, MIT; see NOTICE).

The bug-investigator must run one command that goes red on the user's exact symptom before it hypothesises, rank 3 to
5 falsifiable hypotheses, tag temporary logs and prove they are gone, and report REPRO_* fields. The validator checks
those fields when present and warns (does not fail) when a FIXED contract omits them, for one release.

Run from anywhere: python3 plugins/craftflow/tests/fixtures/test_repro_first_debug.py
"""
import importlib
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "scripts"))
cv = importlib.import_module("craftflow_contract_validate")

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


def read(rel):
    path = PLUGIN / rel
    return path.read_text(encoding="utf-8") if path.is_file() else ""


agent = read("agents/bug-investigator.md")
loops = read("skills/debugging-patterns/references/feedback-loops.md")
patterns_core = read("skills/debugging-patterns/SKILL.md")
verifier = read("agents/integration-verifier.md")
audit = read("scripts/craftflow_subagent_stop_audit.py")

print("[bug-investigator process]")
loop_at, hyp_at = agent.find("**Feedback loop"), agent.find("**Hypothesis**")
check("a Feedback loop step exists", loop_at != -1)
check("the Feedback loop step comes before Hypothesis", -1 < loop_at < hyp_at, (loop_at, hyp_at))
check("the loop must go red on the user's exact symptom", "exact symptom" in agent)
check("the repro is minimised", "Minimise" in agent)
check("3 to 5 falsifiable hypotheses with a prediction", "3 to 5" in agent and "prediction" in agent)
check("temporary logs get a DBG tag distinct from DEBUG-N", "[DBG-" in agent)
check("tagged logs are proven gone with a grep", "grep" in agent and "DBG-" in agent)
check("no correct seam is a reported finding", "no correct seam" in agent.lower())
check("no loop means BLOCKED with no_feedback_loop", "no_feedback_loop" in agent)
check("points at the feedback-loops reference", "references/feedback-loops.md" in agent)

print("[contract block]")
block = cv.extract_yaml_block(agent) or ""
for field in ("REPRO_COMMAND", "REPRO_RED_EXIT", "REPRO_GREEN_EXIT"):
    check(f"contract template carries {field}", f"{field}:" in block)

print("[feedback-loops reference]")
check("reference exists", bool(loops))
for way in ("Failing test", "HTTP script", "CLI invocation", "Headless browser", "Replay a captured",
            "Throwaway harness", "Property", "Bisection", "Differential", "Human in the loop"):
    check(f"loop construction: {way}", way in loops)
check("debugging-patterns core points at it", "references/feedback-loops.md" in patterns_core)

print("[verifier]")
check("fix-verify reruns REPRO_COMMAND", "REPRO_COMMAND" in verifier)

FIXED = """### Router Contract (MACHINE-READABLE)
```yaml
STATUS: FIXED
SUMMARY: "fixed the negative total"
ROOT_CAUSE: "coupon subtraction had no floor"
TDD_RED_EXIT: 1
TDD_GREEN_EXIT: 0
BLOCKING: false
REMEDIATION_NEEDED: false
{extra}```
"""

print("[validator]")
r = cv.validate_contract(FIXED.format(extra=""), "bug-investigator")
check("FIXED without REPRO fields stays valid for one release", r["valid"], r)
check("... but warns about REPRO_COMMAND", any("REPRO_COMMAND" in w for w in r.get("warnings", [])), r)
good = 'REPRO_COMMAND: "python3 -m pytest tests/test_cart.py -k SC_003"\nREPRO_RED_EXIT: 1\nREPRO_GREEN_EXIT: 0\n'
r = cv.validate_contract(FIXED.format(extra=good), "bug-investigator")
check("a complete repro is valid with no warnings", r["valid"] and r.get("warnings") == [], r)
r = cv.validate_contract(FIXED.format(extra=good.replace("REPRO_RED_EXIT: 1", "REPRO_RED_EXIT: 0")), "bug-investigator")
check("a repro that never went red is an error", not r["valid"] and any("REPRO_RED_EXIT" in e for e in r["errors"]), r)
r = cv.validate_contract(FIXED.format(extra=good.replace("REPRO_GREEN_EXIT: 0", "REPRO_GREEN_EXIT: 1")), "bug-investigator")
check("FIXED with a repro still red is an error", not r["valid"] and any("REPRO_GREEN_EXIT" in e for e in r["errors"]), r)
r = cv.validate_contract(FIXED.format(extra=good.replace('"python3 -m pytest tests/test_cart.py -k SC_003"', '""')),
                         "bug-investigator")
check("an empty REPRO_COMMAND is an error", not r["valid"] and any("REPRO_COMMAND" in e for e in r["errors"]), r)
investigating = FIXED.format(extra="").replace("STATUS: FIXED", "STATUS: INVESTIGATING")
r = cv.validate_contract(investigating, "bug-investigator")
check("INVESTIGATING without a repro gets no warning", r["valid"] and r.get("warnings") == [], r)
r = cv.validate_contract(FIXED.format(extra=""), "doc-syncer")
check("other kinds always carry an empty warnings list", r.get("warnings") == [], r)

print("[subagent stop audit]")
check("the audit logs contract_warnings", "contract_warnings" in audit)

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
