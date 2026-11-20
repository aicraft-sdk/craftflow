#!/usr/bin/env python3
"""Pins for the two-axis review (adapted from the code-review skill in mattpocock/skills, MIT; see NOTICE).

The code-reviewer judges Spec (does the diff do what the plan says) and Standards (does it follow the repo's rules)
as separate axes that are never reranked against each other. `REVIEW_AXIS: spec | standards` narrows one dispatch to
one axis, so the router can run them in isolated contexts. The router wiring needs signed policy-map rows and a
ceiling envelope, so this change ships the agent side plus a drafted router change for signature.

Run from anywhere: python3 plugins/craftflow/tests/fixtures/test_two_axis_review.py
"""
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
REPO = PLUGIN.parents[1]
FAILS = []


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


agent = (PLUGIN / "agents/code-reviewer.md").read_text(encoding="utf-8")
draft_path = REPO / "docs/proposals/two-axis-review-router-change.md"
draft = draft_path.read_text(encoding="utf-8") if draft_path.is_file() else ""

print("[reviewer axis input]")
check("takes REVIEW_AXIS: spec | standards", "REVIEW_AXIS: spec | standards" in agent)
check("no REVIEW_AXIS runs both axes, Spec first", "No `REVIEW_AXIS`" in agent and "Spec first" in agent)
check("axes are never reranked against each other", "never reranked" in agent)
check("spec axis quotes the spec line for each gap", "quote the spec line" in agent)
for gap in ("missing", "partial", "unrequested", "contradicting"):
    check(f"spec axis gap kind: {gap}", gap in agent)
check("spec axis leaves running tests to the verifier", "leave running tests to the integration-verifier" in agent)
for source in ("CODING_STANDARDS.md", "CONTRIBUTING.md", "AGENTS.md", "project/constitution.md"):
    check(f"standards axis reads {source}", source in agent)
check("standards axis uses the Fowler baseline", "Fowler Smell Baseline" in agent)
check("a documented repo rule overrides a smell", "repo rule overrides" in agent)

print("[reviewer output]")
for heading in ("### Spec Axis", "### Standards Axis"):
    check(f"output has {heading}", heading in agent)
check("each axis names its worst issue", agent.count("Worst issue:") >= 2)
check("BLOCKING when either axis has a critical finding", "either axis" in agent)
check("file stays under the 500-line cap", agent.count("\n") < 500, agent.count("\n"))

print("[drafted router change]")
check("draft exists", bool(draft))
for target in ("build-graph-standard.md", "review-workflow.md", "post-agent-validation.md", "craftflow-router/SKILL.md",
               "cursor-router/SKILL.md", "router-policy-map.md", "router-budget.json"):
    check(f"draft names {target}", target in draft)
check("draft says the rows need a human signature", "signature" in draft.lower())
check("draft keeps the fast path unchanged", "Fast path is unchanged" in draft)

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
