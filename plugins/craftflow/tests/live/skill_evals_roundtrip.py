#!/usr/bin/env python3
"""Behavior evals for the four busiest skills (Wave B item 7; after Skillgrade: scripted cases, deterministic graders).

Each case builds a tiny scratch project, asks a haiku `claude -p --plugin-dir <plugin>` session to use one skill, and
grades what the session DID (the ordered tool calls in its stream-json output, and the project's tests afterwards),
not what it said about itself.

    tdd      test-driven-development: a test file is written before the source, the tests run between the two
             (the red step), and the final tests pass.
    debug    systematic-debugging: the failing test is run before the source is first edited (reproduce first), and
             the final tests pass.
    review   code-review-patterns: the review names the SQL injection in the diff and edits nothing.
    verify   verification-before-completion: the session runs the tests and does not claim the work is complete
             while a test fails.

The graders are pure functions (tests/fixtures/test_skill_evals_grade.py runs them on canned streams in CI). The live
run needs the claude CLI; without it every case prints `SKIP: claude CLI not found` and exits 1, which the live harness
reports as BLOCKED. Env isolation as in router_context_cut_roundtrip.py.

Run: python3 tests/live/skill_evals_roundtrip.py --plugin-dir <plugin dir> [--only tdd|debug|review|verify] [--keep]
Prints one summary JSON line. Exit 0 only when every case run is PASS. Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

CALL_TIMEOUT_S = 420
MODEL = "haiku"
STRIPPED_ENV = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_TEST_RUN_RE = re.compile(r"\bpytest\b|\bunittest\b|\btest_\w+\.py\b")
_COMPLETE_RE = re.compile(r"\b(all tests pass(?:ed)?|work is complete|is complete and verified|task complete)\b", re.I)

CASES = {
    "tdd": {
        "files": {"calc.py": "def add(a, b):\n    return a + b\n"},
        "prompt": "Use the craftflow:test-driven-development skill. Add a function is_even(n) to calc.py. Put the "
                  "tests in test_calc.py and run them with python3 -m pytest. Do not ask questions.",
    },
    "debug": {
        "files": {"calc.py": "def average(xs):\n    return sum(xs) / (len(xs) + 1)\n",
                  "test_calc.py": "from calc import average\n\n\ndef test_average():\n"
                                  "    assert average([2, 4]) == 3\n"},
        "prompt": "Use the craftflow:systematic-debugging skill. python3 -m pytest fails in this project. Find the "
                  "root cause and fix it. Do not ask questions.",
    },
    "review": {
        "files": {"change.diff": "--- a/users.py\n+++ b/users.py\n@@ -1,3 +1,4 @@\n def find(db, name):\n"
                                 "-    return db.execute('SELECT * FROM users WHERE name = ?', (name,))\n"
                                 "+    query = f\"SELECT * FROM users WHERE name = '{name}'\"\n"
                                 "+    return db.execute(query)\n"},
        "prompt": "Use the craftflow:code-review-patterns skill to review change.diff. Report findings only; do not "
                  "change any file.",
    },
    "verify": {
        "files": {"calc.py": "def double(n):\n    return n + n + 1\n",
                  "test_calc.py": "from calc import double\n\n\ndef test_double():\n    assert double(3) == 6\n",
                  "NOTES.md": "Implemented double(). Done.\n"},
        "prompt": "Use the craftflow:verification-before-completion skill. I implemented double() in calc.py. "
                  "Confirm the work is complete. Do not change any file.",
    },
}


# ---- grading (pure) -------------------------------------------------------------------------------------------------

def parse_stream(text: str) -> Tuple[List[dict], str]:
    """(ordered tool_use blocks as {"name", "input"}, final result text) from `claude -p --output-format stream-json`."""
    calls, result = [], ""
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") == "result":
            result = str(row.get("result") or "")
        content = (row.get("message") or {}).get("content") if row.get("type") == "assistant" else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append({"name": str(block.get("name")), "input": block.get("input") or {}})
    return calls, result


def _target(call: dict) -> str:
    inp = call["input"]
    return Path(str(inp.get("file_path") or inp.get("notebook_path") or "")).name


def _first(calls: List[dict], pred) -> Optional[int]:
    return next((i for i, c in enumerate(calls) if pred(c)), None)


def _edits(name_pred):
    return lambda c: c["name"] in EDIT_TOOLS and name_pred(_target(c))


def _test_run(c: dict) -> bool:
    return c["name"] == "Bash" and bool(_TEST_RUN_RE.search(str(c["input"].get("command") or "")))


def grade(case: str, calls: List[dict], result: str, tests_pass: Optional[bool]) -> List[str]:
    """Failure reasons for one case; [] means PASS. tests_pass is the scratch project's final test run (None: n/a)."""
    fails = []
    if case == "tdd":
        test_at = _first(calls, _edits(lambda n: n.startswith("test_")))
        src_at = _first(calls, _edits(lambda n: n == "calc.py"))
        if test_at is None or src_at is None:
            fails.append("no test edit or no source edit")
        elif test_at > src_at:
            fails.append("source edited before the test")
        elif not any(_test_run(c) for c in calls[test_at:src_at]):
            fails.append("tests not run between test and source (no red step)")
    elif case == "debug":
        run_at = _first(calls, _test_run)
        src_at = _first(calls, _edits(lambda n: n == "calc.py"))
        if src_at is None:
            fails.append("source never edited")
        elif run_at is None or run_at > src_at:
            fails.append("source edited before the failure was reproduced")
    elif case == "review":
        if any(c["name"] in EDIT_TOOLS for c in calls):
            fails.append("review edited a file")
        if not re.search(r"sql[\s-]*injection|injection", result, re.I):
            fails.append("review did not name the SQL injection")
    elif case == "verify":
        if any(c["name"] in EDIT_TOOLS for c in calls):
            fails.append("verification edited a file")
        if not any(_test_run(c) for c in calls):
            fails.append("tests never run")
        if _COMPLETE_RE.search(result) or not re.search(r"\bfail", result, re.I):
            fails.append("claimed completion while a test fails")
    else:
        fails.append(f"unknown case {case}")
    if case in ("tdd", "debug") and tests_pass is not True:
        fails.append("final tests do not pass")
    return fails


# ---- live run -------------------------------------------------------------------------------------------------------

def _scratch(case: str, root: Path) -> Path:
    project = root / case
    (project / ".craftflow/state").mkdir(parents=True)
    for rel, body in CASES[case]["files"].items():
        (project / rel).write_text(body, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(project), check=False)
    return project


def run_case(case: str, plugin: Path, root: Path) -> dict:
    project = _scratch(case, root)
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    env.update(CLAUDE_PROJECT_DIR=str(project),
               CRAFTFLOW_STOP_GATE_USER_CONFIG=str(root / "absent-stop-gate.json"),
               CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG=str(root / "absent-context-nudge.json"))
    cmd = ["claude", "-p", "--plugin-dir", str(plugin), "--model", MODEL, "--session-id", str(uuid.uuid4()),
           "--output-format", "stream-json", "--verbose", "--allowedTools", "Skill", "Read", "Glob", "Grep", "Edit",
           "Write", "Bash(python3:*)", "Bash(pytest:*)"]
    try:
        proc = subprocess.run(cmd, input=CASES[case]["prompt"], env=env, cwd=str(project), capture_output=True,
                              text=True, timeout=CALL_TIMEOUT_S)
        stream, rc = proc.stdout, proc.returncode
    except subprocess.TimeoutExpired:
        return {"status": "FAIL", "reasons": ["timeout"]}
    calls, result = parse_stream(stream)
    final = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=str(project), capture_output=True, text=True)
    reasons = ([] if rc == 0 else [f"claude exited {rc}"]) + grade(case, calls, result, final.returncode == 0)
    return {"status": "PASS" if not reasons else "FAIL", "reasons": reasons,
            "tools": [c["name"] for c in calls][:40]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plugin-dir", required=True)
    parser.add_argument("--only", choices=sorted(CASES))
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)
    cases = [args.only] if args.only else list(CASES)
    if shutil.which("claude") is None:
        print("SKIP: claude CLI not found")
        print(json.dumps({c: "SKIP" for c in cases}))
        return 1
    root = Path(tempfile.mkdtemp(prefix="skill-evals-"))
    try:
        out = {c: run_case(c, Path(args.plugin_dir).resolve(), root) for c in cases}
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
    print(json.dumps(out, sort_keys=True))
    return 0 if all(v["status"] == "PASS" for v in out.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
