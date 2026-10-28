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
    print()
    for e in _errors:
        print(e)
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
