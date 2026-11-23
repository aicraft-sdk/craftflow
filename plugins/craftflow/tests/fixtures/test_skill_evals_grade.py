#!/usr/bin/env python3
"""Tests for the skill behavior-eval graders (Wave B item 7). Canned streams, no claude CLI needed.

Run from anywhere: python3 tools/craftflow-plugin/plugins/craftflow/tests/fixtures/test_skill_evals_grade.py
"""
import importlib
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "tests/live"))
ev = importlib.import_module("skill_evals_roundtrip")

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


def stream(*calls, result=""):
    rows = [json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": n, "input": i}]}}) for n, i in calls]
    return "\n".join(rows + ["not json", json.dumps({"type": "result", "result": result})])


def grade(case, text, tests_pass=True):
    calls, result = ev.parse_stream(text)
    return ev.grade(case, calls, result, tests_pass)


W_TEST = ("Write", {"file_path": "/p/test_calc.py"})
E_SRC = ("Edit", {"file_path": "/p/calc.py"})
RUN = ("Bash", {"command": "python3 -m pytest -q"})

print("[parse]")
calls, result = ev.parse_stream(stream(W_TEST, RUN, result="done"))
check("tool calls come back in order with the final result", [c["name"] for c in calls] == ["Write", "Bash"]
      and result == "done", (calls, result))

print("[tdd]")
check("test, red run, source, passing tests is a PASS", grade("tdd", stream(W_TEST, RUN, E_SRC, RUN)) == [])
check("source before test fails", "source edited before the test" in grade("tdd", stream(E_SRC, W_TEST, RUN)))
check("no run between test and source fails", any("red step" in r for r in grade("tdd", stream(W_TEST, E_SRC, RUN))))
check("failing final tests fail", "final tests do not pass" in grade("tdd", stream(W_TEST, RUN, E_SRC), False))

print("[debug]")
check("reproduce then fix is a PASS", grade("debug", stream(RUN, E_SRC, RUN)) == [])
check("fixing before reproducing fails", grade("debug", stream(E_SRC, RUN)) != [])

print("[review]")
check("naming the injection with no edits is a PASS",
      grade("review", stream(("Read", {"file_path": "/p/change.diff"}), result="SQL injection on line 3"), None) == [])
check("an edit during review fails",
      "review edited a file" in grade("review", stream(E_SRC, result="SQL injection"), None))
check("missing the injection fails", grade("review", stream(result="Looks good"), None) != [])

print("[verify]")
check("running tests and reporting the failure is a PASS",
      grade("verify", stream(RUN, result="Not complete: test_double fails (7 != 6)."), None) == [])
check("claiming completion fails", grade("verify", stream(RUN, result="All tests pass. Done."), None) != [])
check("not running tests fails", "tests never run" in grade("verify", stream(result="It fails, I think"), None))

print("[cli]")
check("every case has files and a prompt naming its craftflow skill",
      all(c["files"] and "craftflow:" in c["prompt"] for c in ev.CASES.values()), ev.CASES.keys())

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
