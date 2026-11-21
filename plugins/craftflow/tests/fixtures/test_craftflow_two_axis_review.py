#!/usr/bin/env python3
"""Tests for P5: the standard BUILD chain runs the code reviewer as two isolated dispatches, one per axis.

`agents/code-reviewer.md` § Review Axis defines `REVIEW_AXIS: spec | standards`. The standard BUILD graph creates one
reviewer task per axis next to silent-failure-hunter, the verifier waits for all three, the kernel dispatches all three
in one message, the verifier handoff keeps one findings section per axis, and the Cursor router mirrors the chain. The
fast path and REVIEW are unchanged (REVIEW keeps one dispatch that runs both axes). The workflow machine models the
three-way parallel step, so the Stage B shadow log agrees with the new chain.

Run from anywhere: python3 tools/craftflow-plugin/plugins/craftflow/tests/fixtures/test_craftflow_two_axis_review.py
"""
import importlib
import json
import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "scripts"))
shadow = importlib.import_module("craftflow_machine_shadow")
machine = importlib.import_module("craftflow_machine")

FAILS = []
ROUTER = PLUGIN / "skills/craftflow-router"


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


def read(rel):
    return (PLUGIN / rel).read_text(encoding="utf-8")


print("[standard BUILD graph]")
graph = read("skills/craftflow-router/references/build-graph-standard.md")
check("the reviewer task names REVIEW_AXIS", "REVIEW_AXIS: {axis}" in graph, "no REVIEW_AXIS in build-graph-standard.md")
check("one reviewer task per axis, spec then standards", re.search(r"`spec`, then `standards`", graph) is not None)
check("each reviewer task is blocked by the builder",
      "spec_reviewer_task_id, standards_reviewer_task_id], addBlockedBy: [builder_task_id]" in graph
      or re.search(r"TaskUpdate\(\{ taskId: (each|<each)[^\n]*addBlockedBy: \[builder_task_id\]", graph) is not None)
check("the verifier waits for both axes and the hunter",
      "addBlockedBy: [spec_reviewer_task_id, standards_reviewer_task_id, hunter_task_id]" in graph)
check("no single-axis reviewer id is left", "[reviewer_task_id, hunter_task_id]" not in graph)

print("[kernel dispatch]")
kernel = read("skills/craftflow-router/SKILL.md")
check("step 5 dispatches both reviewer axes with the hunter", "both `code-reviewer` axis tasks" in kernel)
check("sequential fallback runs spec, standards, then hunter", "(spec, standards, then hunter)" in kernel)

print("[merged findings]")
pav = read("skills/_shared/verifier-handoff.md")
check("the merged summary keeps one section per axis", "one section per axis" in pav)
check("either axis blocks", "either axis" in pav)

print("[cursor router mirrors it]")
cursor = read("skills/cursor-router/SKILL.md")
check("cursor chain names both axes",
      "component-builder → code-reviewer (spec ‖ standards) ‖ silent-failure-hunter → integration-verifier" in cursor)
check("cursor parallel rule dispatches three Task calls", "three `Task` calls" in cursor)

print("[unchanged routes]")
fast = read("skills/craftflow-router/references/fast-path.md")
check("fast path still skips review", "| `build-review` | code-reviewer | ✓ | ✗ skip |" in fast)
review = read("skills/craftflow-router/references/review-workflow.md")
check("REVIEW keeps one reviewer dispatch", review.count("code-reviewer") >= 1 and "REVIEW_AXIS" not in review)

print("[machine and shadow]")
machines = machine.load_all(PLUGIN / "config/machines")
policy = json.loads(read("config/policy.json"))
pair = machines["build-standard"]["states"]["review-and-hunt"]["agent"]
check("the machine's parallel step holds two reviewers and the hunter",
      sorted(pair.split("+")) == ["code-reviewer", "code-reviewer", "silent-failure-hunter"], pair)
check("the machine stays sound", machine.validate(machines["build-standard"], policy) == [])


def run(steps):
    state, rows = {}, []
    for agent, verdict in steps:
        row, state = shadow.observe(machines, policy, state, "BUILD", "standard", agent, verdict)
        rows.append(row)
    return rows


rows = run([("component-builder", "PASS"), ("code-reviewer", "APPROVE"), ("code-reviewer", "APPROVE"),
            ("silent-failure-hunter", "CLEAN"), ("integration-verifier", "PASS")])
check("both axes and the hunter must return before the verifier",
      [r["next"] for r in rows[1:4]] == [None, None, "build-verify"] and rows[4]["agree"] is True, rows)
rows = run([("component-builder", "PASS"), ("code-reviewer", "CHANGES_REQUESTED"), ("code-reviewer", "APPROVE"),
            ("silent-failure-hunter", "CLEAN")])
check("a spec finding is not hidden by a clean standards axis", rows[3]["next"] == "remfix", rows[3])
rows = run([("component-builder", "PASS"), ("code-reviewer", "APPROVE"), ("silent-failure-hunter", "CLEAN"),
            ("integration-verifier", "PASS")])
check("a verifier after only one reviewer axis disagrees", rows[3]["agree"] is False, rows[3])

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
