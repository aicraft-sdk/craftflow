#!/usr/bin/env python3
"""Cursor router section 1 points at the shared routing law instead of copying it (ADR-0075, DEC-8).

Run: python3 tests/fixtures/test_cursor_routing_pointer.py
The shared `## Intent Routing` table and rules in skills/_shared/router-protocol.md are the one source; cursor-router/SKILL.md
section 1 keeps only the Cursor-only parts (pending-answer precedence, inspection-skill exemptions, extra risk keywords,
announce line) and `Read()`s the shared doc, the five route files and the Cursor binding file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CURSOR = PLUGIN_ROOT / "skills" / "cursor-router" / "SKILL.md"
ROUTES = PLUGIN_ROOT / "skills" / "cursor-router" / "references" / "routes.md"
ROOT_PREFIX = "tools/craftflow-plugin/plugins/craftflow/skills/"
ROUTE_FILES = ("route-orient.md", "route-advisory.md", "route-triage.md", "route-codebase-health.md", "route-qa.md")
# Route gates answered by `### Cursor route gate answers`; each later route phase appends its own (H6, H7, H11).
ROUTE_GATES = ("architecture_scanner_failed", "triage_agent_failed", "qa_researcher_failed", "qa_harness_builder_failed",
               "qa_executor_failed", "planner_failed", "plan_gap_reviewer_failed", "code_reviewer_failed",
               "silent_failure_hunter_failed")
# `candidate_choice` is answered by its own reply|action table: five rows for one gate, so it cannot live in the dict keyed by gate.
CANDIDATE_CHOICE_REPLIES = ("any reply once a candidate is picked (the grill is running)", "a listed candidate number", '"None"', "a fresh unrelated request", "anything else")
POINTER = "Route by the shared doc's `## Intent Routing` table and rules (already read above); Cursor-only additions follow."

_passes = 0
_errors: list = []


def check(name: str, cond: bool, reason: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason}")
        print(f"  FAIL: {name}: {reason}")


def section1(text: str) -> str:
    start = text.find("\n## 1. Intent Routing")
    end = text.find("\n## 2. Memory Load", start)
    return text[start:end] if start != -1 and end != -1 else ""


def block(text: str, heading: str) -> str:
    """Lines from `heading` up to the next heading of the same or higher level ('' when the heading is absent)."""
    lines = text.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    for i, line in enumerate(lines):
        if line.strip() == heading:
            body = [line]
            for nxt in lines[i + 1:]:
                if nxt.startswith("#") and len(nxt) - len(nxt.lstrip("#")) <= level:
                    break
                body.append(nxt)
            return "\n".join(body)
    return ""


def table_rows(text: str) -> list:
    return [[c.strip() for c in line.strip().strip("|").split("|")] for line in text.splitlines()
            if line.startswith("|") and not line.startswith("|---")]


def has_routing_table(sec: str) -> bool:
    return any(line.lstrip().startswith("|") and "Keywords" in line and "Workflow" in line for line in sec.splitlines())


def reads(sec: str, rel: str) -> bool:
    """A `Read()` instruction and the path share a line."""
    return any("`Read()`" in line and rel in line for line in sec.splitlines())


def main() -> int:
    text = CURSOR.read_text(encoding="utf-8")
    sec = section1(text)
    check("section_1_found", bool(sec), "cursor-router/SKILL.md has no '## 1. Intent Routing' .. '## 2. Memory Load' span")
    check("(a) no copied routing table", not has_routing_table(sec), "section 1 still holds a Keywords/Workflow table")
    check("(a) control: the old copied table is detected",
          has_routing_table("| Priority | Signal | Keywords | Workflow | Chain |\n|---|---|---|---|---|\n"),
          "the table detector missed a Keywords/Workflow header")
    check("(b) Read() of the shared protocol and the Intent Routing phrase",
          reads(sec, "_shared/router-protocol.md") and "## Intent Routing" in sec, "missing Read() of router-protocol.md or the phrase")
    precedence, exempt = sec.find("Pending-answer precedence check"), sec.find("### Router-exempt inspection skills")
    pointer, rules = sec.find(POINTER), sec.find("\nRules:\n")
    check("(c) precedence < exemption block < pointer sentence < Rules",
          -1 < precedence < exempt < pointer < rules, f"offsets precedence={precedence} exempt={exempt} pointer={pointer} rules={rules}")
    check("(c) exemption table, risk keywords and announce line stay",
          all(k in sec for k in ("| retro |", "| status |", "| failure-digest |", "`concurrent`", "`race`", "`rollback`", "| Security |",
                                 "-> {WORKFLOW} workflow (signals: {matched keywords})")),
          "an exemption row, risk keyword table or the announce line is missing from section 1")
    check("(c) the deleted copy is gone", "Route using the first matching signal:" not in sec and "ERROR always wins over BUILD." not in sec,
          "the old label or ERROR rule is still in section 1")
    check("(d) Read() of the Cursor routes binding file",
          reads(sec, "cursor-router/references/routes.md") and ROUTES.is_file(), f"routes.md exists={ROUTES.is_file()}")
    missing = [n for n in ROUTE_FILES if not reads(sec, ROOT_PREFIX + "_shared/" + n)]
    check("each shared route file is Read() from section 1", not missing, f"missing Read() lines for {missing}")
    # (e) section 1 names pending_handoff in the precedence check and sends non-phase_ gates to routes.md.
    precedence_block = sec[:sec.find("### Router-exempt inspection skills")]
    check("(e) precedence check names pending_handoff and the all-null sentence",
          "pending_handoff" in precedence_block
          and "Only when `pending_skill_approval`, `pending_gate` and `pending_handoff` are all null" in precedence_block,
          "section 1's precedence check lacks pending_handoff or the 'Only when ... all null' sentence")
    check("(e) a non-phase_ pending_gate value goes to ### Cursor route gate answers",
          "does not start with `phase_`" in precedence_block and "### Cursor route gate answers" in precedence_block,
          "the pending_gate bullet does not route non-phase_ values to routes.md")
    check("(e) the handoff question is answered per ### Cursor handoff answers, re-asked verbatim",
          "### Cursor handoff answers" in precedence_block and "re-asking verbatim" in precedence_block, "")
    check("(e) control: the old 'Only when both' sentence is gone", "Only when both `pending_skill_approval` and `pending_gate`" not in text, "")
    # (f) section 6 limits retry/skip/stop to phase_{phase}_failed gates.
    nonmatch = block(text, "### Non-matching reply (pending_gate answers)")
    check("(f) section 6 limits its three options to phase_{phase}_failed gates and excludes route chains",
          "answer only `phase_{phase}_failed` gates" in nonmatch and "Route chains" in nonmatch and "never use this section" in nonmatch
          and "`{agent}_failed`" in nonmatch and "skip is never an answer for a route phase" in nonmatch, nonmatch[-400:])
    # (g) routes.md: the handoff answer table and the route gate table.
    routes = ROUTES.read_text(encoding="utf-8")
    handoff = block(routes, "### Cursor handoff answers")
    rows = [r for r in table_rows(handoff) if len(r) == 2 and r[0] != "Reply"]
    yes = next((r[1] for r in rows if r[0].startswith('"yes"')), "")
    no = next((r[1] for r in rows if r[0].startswith('"no"')), "")
    other = next((r[1] for r in rows if r[0].lower().startswith("anything else")), "")
    check("(g) yes -> --record-accept then --host cursor ... --accepted-answer, pending_handoff cleared",
          -1 < yes.find("--record-accept") < yes.find("--host cursor") < yes.find("--accepted-answer") and "pending_handoff: null" in yes, yes)
    check("(g) no -> --record-decline --reason user_declined, pending_handoff cleared",
          "--record-decline --reason user_declined" in no and "pending_handoff: null" in no, no)
    check("(g) anything else -> re-ask verbatim", "re-ask" in other.lower() and "verbatim" in other, other)
    gates = block(routes, "### Cursor route gate answers")
    all_rows = table_rows(gates)
    header = next((r for r in all_rows if r[0] == "`pending_gate`"), [])
    gate_rows = {r[0].strip("`"): r for r in all_rows if len(r) == 3 and r[0] != "`pending_gate`"}
    missing = [g for g in ROUTE_GATES if g not in gate_rows]
    check("(g) a retry | end row for each route gate value", not missing and header[1:] == ['"retry"', '"end"'],
          f"missing={missing} header={header}")
    check("(g) no row offers skip, and skip is re-asked verbatim",
          all("skip" not in " ".join(r).lower() for r in gate_rows.values()) and '"skip"' in gates and "re-asked verbatim" in gates, gates[-300:])
    choice_rows = [r for r in all_rows if r[0] == "`candidate_choice`"]
    check("(g) the candidate_choice table has exactly five rows, one per reply cell",
          len(choice_rows) == 5 and [r[1] for r in choice_rows if len(r) == 3] == list(CANDIDATE_CHOICE_REPLIES), str(choice_rows))
    check("(g) no candidate_choice row offers skip", all("skip" not in " ".join(r).lower() for r in choice_rows), str(choice_rows))
    check("(g) control: an absent gate value is reported missing", "bogus_agent_failed" not in gate_rows, "")
    print()
    for e in _errors:
        print(e)
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
