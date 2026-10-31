#!/usr/bin/env python3
"""
Fixture tests for the architecture-scanner cross-field rules (H6 remfix-1):
`craftflow_contract_validate_pilot.py --agent architecture-scanner --workflow-uuid U`
must pin REPORT_PATH to the exact project-relative report path and enforce the
count / pairing / candidate-item rules that a field-level schema cannot.

Run from the plugin root:
    python3 tests/fixtures/test_contract_validate_scanner.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(HERE, "../../scripts/craftflow_contract_validate_pilot.py")

PASS = 0
FAIL = 0
UUID = "wf-x"
GOOD_PATH = ".craftflow/codebase-health/wf-x/report.html"


def check(name, actual, expected):
    global PASS, FAIL
    if actual == expected:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}\n    expected: {expected!r}\n    actual:   {actual!r}")
        FAIL += 1


def candidate(i, strength="Strong"):
    return (
        f'    - name: "cand {i}"\n'
        f'      files: ["calc/m{i}.py"]\n'
        f'      problem: "p{i}"\n'
        f'      solution: "s{i}"\n'
        f"      strength: {strength}\n"
    )


def contract(status="CANDIDATES_FOUND", count=2, top="1", action="choose", cands=None, path=GOOD_PATH):
    if cands is None:
        cands = "".join(candidate(i) for i in range(1, count + 1)) if count else ""
    block = "  CANDIDATES:\n" + cands if cands else "  CANDIDATES: []\n"
    return (
        "verdict:\n"
        f"  STATUS: {status}\n"
        f"  CANDIDATE_COUNT: {count}\n"
        f"  TOP_RECOMMENDATION: {top}\n"
        "  BLOCKING: false\n"
        f"  NEXT_ACTION: {action}\n"
        "rationale:\n"
        '  SUMMARY: "scan"\n'
        f"{block}"
        f'  REPORT_PATH: "{path}"\n'
        "  MEMORY_NOTES:\n"
        "    learnings: []\n    patterns: []\n    verification: []\n    deferred: []\n"
    )


def run_cli(text, *extra):
    proc = subprocess.run(
        [sys.executable, SCRIPT_PATH, "--agent", "architecture-scanner", "--stdin", *extra],
        input=text, capture_output=True, text=True,
    )
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        out = {"valid": None, "violations": [{"field": "<stdout>", "reason": proc.stdout[:80]}]}
    return proc.returncode, out["valid"], sorted({v["field"] for v in out["violations"]})


def run_uuid(text):
    return run_cli(text, "--workflow-uuid", UUID)


print("[scanner cross-field validation]")
check("a well-formed contract passes with the uuid", run_uuid(contract()), (0, True, []))
check("a well-formed single-candidate contract passes", run_uuid(contract(count=1)), (0, True, []))
check("NO_CANDIDATES (count 0, empty list, complete, null) passes",
      run_uuid(contract("NO_CANDIDATES", 0, "null", "complete")), (0, True, []))
check("without --workflow-uuid the scanner contract fails closed, even a well-formed one (RR1)",
      run_cli(contract()), (1, False, ["--workflow-uuid"]))
check("without --workflow-uuid a stray REPORT_PATH is not accepted either (RR1)",
      run_cli(contract(path="docs/other.html"))[:2], (1, False))
proc = subprocess.run([sys.executable, SCRIPT_PATH, "--agent", "component-builder", "--stdin"], input="verdict:\n  STATUS: PASS\n",
                      capture_output=True, text=True)
check("other agents never need --workflow-uuid (control: no such violation)",
      "--workflow-uuid" not in {v["field"] for v in json.loads(proc.stdout)["violations"]}, True)

for label, path in (
    ("absolute REPORT_PATH", "/etc/passwd"),
    ("REPORT_PATH escaping with ../", "../../x/report.html"),
    ("empty REPORT_PATH", ""),
    ("REPORT_PATH in another directory", "docs/other.html"),
    ("REPORT_PATH of another workflow", ".craftflow/codebase-health/wf-y/report.html"),
    ("REPORT_PATH with a shell suffix", GOOD_PATH + "; touch pwned"),
    ("REPORT_PATH with a trailing slash", GOOD_PATH + "/"),
):
    check(f"rejects {label}", run_uuid(contract(path=path)), (1, False, ["REPORT_PATH"]))

check("rejects count above the list", run_uuid(contract(count=3, cands="".join(candidate(i) for i in (1, 2)))), (1, False, ["CANDIDATE_COUNT"]))
check("rejects count below the list", run_uuid(contract(count=1, cands="".join(candidate(i) for i in (1, 2)))), (1, False, ["CANDIDATE_COUNT"]))
check("rejects TOP_RECOMMENDATION 0", run_uuid(contract(top="0")), (1, False, ["TOP_RECOMMENDATION"]))
check("rejects TOP_RECOMMENDATION count+1", run_uuid(contract(top="3")), (1, False, ["TOP_RECOMMENDATION"]))
check("accepts TOP_RECOMMENDATION == count", run_uuid(contract(top="2")), (0, True, []))
check("rejects CANDIDATES_FOUND with TOP_RECOMMENDATION null", run_uuid(contract(top="null")), (1, False, ["TOP_RECOMMENDATION"]))
check("rejects CANDIDATES_FOUND with NEXT_ACTION complete", run_uuid(contract(action="complete")), (1, False, ["NEXT_ACTION"]))
check("rejects CANDIDATES_FOUND with count 0 and no list",
      run_uuid(contract("CANDIDATES_FOUND", 0, "null", "choose")), (1, False, ["CANDIDATE_COUNT", "TOP_RECOMMENDATION"]))
check("rejects NO_CANDIDATES with count 1", run_uuid(contract("NO_CANDIDATES", 1, "null", "complete")), (1, False, ["CANDIDATE_COUNT"]))
check("rejects NO_CANDIDATES with NEXT_ACTION choose", run_uuid(contract("NO_CANDIDATES", 0, "null", "choose")), (1, False, ["NEXT_ACTION"]))
check("rejects NO_CANDIDATES with TOP_RECOMMENDATION 1", run_uuid(contract("NO_CANDIDATES", 0, "1", "complete")), (1, False, ["TOP_RECOMMENDATION"]))
check("rejects NO_CANDIDATES with a non-empty list and count 0",
      run_uuid(contract("NO_CANDIDATES", 0, "null", "complete", cands=candidate(1))), (1, False, ["CANDIDATE_COUNT"]))
check("rejects a strength outside the badge set", run_uuid(contract(cands=candidate(1, "Mega") + candidate(2))), (1, False, ["CANDIDATES"]))
check("accepts every badge", run_uuid(contract(cands=candidate(1, "Worth exploring") + candidate(2, "Speculative"))), (0, True, []))
for key in ("name", "files", "problem", "solution", "strength"):
    victim = next(l for l in candidate(1).split("\n") if l.lstrip("- ").startswith(f"{key}:"))
    broken = candidate(1).replace(victim, "    - x: 1" if key == "name" else "      x: 1") + candidate(2)
    check(f"rejects a candidate with no {key}", run_uuid(contract(cands=broken)), (1, False, ["CANDIDATES"]))
check("rejects a candidate whose files is not a list of strings",
      run_uuid(contract(cands=candidate(1).replace('["calc/m1.py"]', "calc/m1.py") + candidate(2))), (1, False, ["CANDIDATES"]))
check("rejects an unsafe workflow uuid argument",
      run_cli(contract(), "--workflow-uuid", "../evil")[:2], (1, False))
check("rejects an empty workflow uuid argument", run_cli(contract(), "--workflow-uuid", "")[:2], (1, False))

print(f"\n{PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
