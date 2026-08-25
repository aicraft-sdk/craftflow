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
SKILL_MD_PATH = os.path.join(_FIXTURES_DIR, "../../skills/craftflow-router/SKILL.md")

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
# test_skill_md_write_literal_declares_bakeoff_fields (Phase 3 Step 1b/Step 8)
# ---------------------------------------------------------------------------
print("\n[test_skill_md_write_literal_declares_bakeoff_fields]")

with open(SKILL_MD_PATH, "r", encoding="utf-8") as f:
    skill_text = f.read()

_write_literal_line = None
for _line in skill_text.splitlines():
    if "workflow_started" in _line and '\\"task_ids\\"' in _line:
        _write_literal_line = _line
        break

check(
    "found the § 6 Parent Workflow Creation Write() literal line "
    "(contains both workflow_started and task_ids)",
    _write_literal_line is not None,
    True,
)

_WRITE_LITERAL_FIELDS = [
    '\\"plan_file_stem\\":null',
    '\\"bakeoff_n\\":null',
    '\\"bakeoff_n_requested\\":null',
    '\\"bakeoff_models\\":[]',
    '\\"bakeoff_triggered\\":false',
    '\\"bakeoff_all_failed\\":false',
    '\\"bakeoff_candidate_failures\\":[]',
    '\\"plan_bakeoff_candidates\\":{}',
    '\\"plan_bakeoff_judge\\":null',
    '\\"bakeoff\\":[]',
]

for _field in _WRITE_LITERAL_FIELDS:
    check_contains(
        f"Write() literal declares {_field}",
        _write_literal_line or "",
        _field,
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
