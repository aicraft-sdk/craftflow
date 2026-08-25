#!/usr/bin/env python3
"""
Structural assertions for craftflow agent markdown files.

Checks that agent .md files document required optional-section handling
without needing a live dispatch. Run from the plugin root:

    python3 tests/fixtures/test_agent_structure.py
"""
import os

_FIXTURES_DIR = os.path.dirname(__file__)
PLANNER_MD_PATH = os.path.join(_FIXTURES_DIR, "../../agents/planner.md")

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


def check_contains(name: str, haystack: str, needle: str):
    global PASS, FAIL
    if needle in haystack:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        print(f"    expected to find: {needle!r}")
        FAIL += 1


with open(PLANNER_MD_PATH, "r", encoding="utf-8") as f:
    planner_text = f.read()

# ---------------------------------------------------------------------------
# test_planner_documents_target_plan_file_override
# ---------------------------------------------------------------------------
print("\n[test_planner_documents_target_plan_file_override]")

check_contains(
    "planner.md contains '## Target Plan File' optional-section heading",
    planner_text,
    "## Target Plan File",
)

check_contains(
    "Process step 14 (Save plan) references Target Plan File override",
    planner_text,
    "Save plan** - use `## Target Plan File`",
)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*40}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    print("FAIL")
    raise SystemExit(1)
else:
    print("PASS")
    raise SystemExit(0)
