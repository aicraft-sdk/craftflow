#!/usr/bin/env python3
"""PP-3 (static): advisory routes never write product code, and ORIENT dispatches nothing. H5: the handoff matrix.

Run: python3 tests/fixtures/test_route_law_static.py   (non-zero exit on any FAIL)
Reads the dispatcher table of `_shared/router-protocol.md` and the advisory route files. The checks are
functions of the text so each has a control that proves it can fail.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = PLUGIN_ROOT / "skills" / "_shared" / "router-protocol.md"
WRITE_AGENTS = ("component-builder", "bug-investigator")
ADVISORY_FILES = (
    "skills/_shared/route-orient.md",
    "skills/_shared/route-advisory.md",
    "skills/_shared/route-triage.md",
    "skills/_shared/route-codebase-health.md",
    "skills/_shared/route-qa.md",
    "skills/craftflow-router/references/triage-workflow.md",
    "skills/craftflow-router/references/codebase-health-workflow.md",
)
ADVISORY_GLOBS = ("skills/craftflow-router/references/qa-*.md",)
NEGATION_RE = re.compile(r"\b(?:no|not|never|without|refuse[sd]?|refusing|cannot)\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"`([a-z][a-z0-9]*(?:-[a-z0-9]+)*|kind:[a-z]+|origin:[a-z-]+)`")

_passes = 0
_errors: List[str] = []


def check(name: str, cond: bool, reason: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason}")
        print(f"  FAIL: {name}: {reason}")


def parse_dispatcher(text: str) -> Dict[str, str]:
    """{phase token or kind/origin cell: agent cell} from the Explicit Dispatcher table."""
    start = text.index("## Explicit Dispatcher")
    end = text.find("\n## ", start + 1)
    rows: Dict[str, str] = {}
    for line in text[start:end if end != -1 else len(text)].splitlines():
        if not line.startswith("|") or line.startswith("|--") or "Task Phase" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        for token in TOKEN_RE.findall(cells[0]) or [cells[0]]:
            rows[token] = cells[1]
    return rows


def orient_dispatch_problems(table: Dict[str, str]) -> List[str]:
    return [f"dispatcher row {k!r} routes ORIENT work to {v!r}" for k, v in table.items()
            if "orient" in k.lower()]


def advisory_write_problems(table: Dict[str, str], name: str, text: str) -> List[str]:
    """Tokens an advisory file schedules (outside negations) that the dispatcher maps to a write agent."""
    problems = []
    for line in text.splitlines():
        for match in TOKEN_RE.finditer(line):
            token = match.group(1)
            if NEGATION_RE.search(line[:match.start()]):  # a whole-word negation earlier in the line exempts the mention
                continue
            agent = table.get(token, "")
            if any(w in agent for w in WRITE_AGENTS):
                problems.append(f"{name}: `{token}` maps to {agent}")
    return problems


def harness_builder_problems(table: Dict[str, str]) -> List[str]:
    return [f"{k!r} -> {v!r}" for k, v in table.items()
            if "qa-harness-builder" in k and "component-builder" in v]


ADVISORY = PLUGIN_ROOT / "skills" / "_shared" / "route-advisory.md"
KERNEL = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
DETAIL_VALUES = ("tool_unavailable", "tool_error", "no_answer")


def matrix_rows(text: str) -> Dict[str, List[str]]:
    """{source cell: [target, mode, JUST_GO]} from the `### Handoff targets and modes` table."""
    start = text.find("### Handoff targets and modes")
    end = text.find("\n### ", start + 1)
    rows: Dict[str, List[str]] = {}
    for line in text[start:end if end != -1 else len(text)].splitlines() if start != -1 else []:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 4 and cells[0] != "Source" and not cells[0].startswith("-"):
            rows[cells[0]] = cells[1:]
    return rows


def matrix_problems(text: str) -> List[str]:
    """PLAN and the QA re-run are automatic (also under JUST_GO); BUILD/DEBUG are asked once and asked under JUST_GO."""
    rows, problems = matrix_rows(text), []
    want = (("CODEBASE-HEALTH", "PLAN", "auto", "auto"), ("TRIAGE", "BUILD / DEBUG", "ask", "asked"),
            ("QA bug candidate", "DEBUG", "ask", "asked"), ("QA-seeded DEBUG", "QA re-run", "auto", "auto"))
    for source, target, mode, jg in want:
        row = next((v for k, v in rows.items() if k.startswith(source)), None)
        if row is None or target not in row[0]:
            problems.append(f"no matrix row {source} -> {target}")
        elif not row[1].startswith(mode) or not row[2].startswith(jg):
            problems.append(f"{source} -> {target}: mode {row[1]!r} / JUST_GO {row[2]!r}, want {mode}/{jg}")
    return problems


def detection_problems(text: str) -> List[str]:
    """Non-interactive detection: three triggers (a)-(c) and the three `detail` values, recorded with --record-decline."""
    start = text.find("### Asking about a write handoff")
    body = text[start:text.find("\n### ", start + 1) if text.find("\n### ", start + 1) != -1 else len(text)] if start != -1 else ""
    problems = [f"detection lacks {n!r}" for n in ("(a)", "(b)", "(c)", "tool_unavailable|tool_error|no_answer",
                                                    "--record-decline --reason non_interactive --detail") if n not in body]
    return problems + [f"detection lacks the detail {d!r}" for d in DETAIL_VALUES if d not in body]


def advisory_files() -> List[Path]:
    paths = [PLUGIN_ROOT / rel for rel in ADVISORY_FILES]
    for pattern in ADVISORY_GLOBS:
        paths += sorted(PLUGIN_ROOT.glob(pattern))
    return [p for p in paths if p.is_file()]


def main() -> int:
    table = parse_dispatcher(PROTOCOL.read_text(encoding="utf-8"))
    check("dispatcher table parsed", len(table) >= 15 and "component-builder" in table.get("build-implement", ""),
          f"rows={len(table)}")
    check("no dispatcher row serves ORIENT", orient_dispatch_problems(table) == [], str(orient_dispatch_problems(table)))
    check("origin:qa-harness-builder never reaches component-builder", harness_builder_problems(table) == [],
          str(harness_builder_problems(table)))

    orient = (PLUGIN_ROOT / "skills" / "_shared" / "route-orient.md").read_text(encoding="utf-8")
    check("route-orient.md names no agent, Task or TaskCreate call",
          not any(bad in orient for bad in ("craftflow:", "subagent_type", "TaskCreate(", "Task(")), "dispatch syntax found")
    check("route-orient.md schedules no write-agent phase", advisory_write_problems(table, "route-orient.md", orient) == [], "")

    files = advisory_files()
    check("advisory route files found", len(files) >= 5, str(len(files)))
    for path in files:
        problems = advisory_write_problems(table, path.name, path.read_text(encoding="utf-8"))
        check(f"{path.name}: no phase reaches component-builder or bug-investigator", problems == [], str(problems))

    # Controls: each check must be able to fail.
    bad = dict(table, orient="`craftflow:component-builder`")
    check("control: an orient dispatcher row is caught", orient_dispatch_problems(bad) != [], "")
    check("control: a scheduled build-implement is caught",
          advisory_write_problems(table, "x", "Then dispatch `build-implement` for the fix.") != [], "")
    check("control: a negated mention is allowed",
          advisory_write_problems(table, "x", "Never dispatch `build-implement` here.") == [], "")
    check("control: 'piano' is not a negation, so a scheduled build-implement is caught",
          advisory_write_problems(table, "x", "Use the piano then dispatch `build-implement`.") != [], "")
    check("control: 'cannot dispatch' is a negation",
          advisory_write_problems(table, "x", "The route cannot dispatch `build-implement`.") == [], "")
    check("control: a negation after the token does not exempt it",
          advisory_write_problems(table, "x", "Dispatch `build-implement` now, not later.") != [], "")
    check("control: harness-builder to component-builder is caught",
          harness_builder_problems({"`origin:qa-harness-builder`": "craftflow:component-builder"}) != [], "")

    # H5: the handoff matrix and the non-interactive detection, read from the live files.
    advisory = ADVISORY.read_text(encoding="utf-8")
    check("route-advisory.md matrix: PLAN and the QA re-run automatic, BUILD/DEBUG ask-first", matrix_problems(advisory) == [],
          str(matrix_problems(advisory)))
    check("route-advisory.md defines non-interactive detection with the three detail values", detection_problems(advisory) == [],
          str(detection_problems(advisory)))
    check("route-advisory.md has ### Adopting the handoff child", "\n### Adopting the handoff child\n" in "\n" + advisory, "")
    kernel = KERNEL.read_text(encoding="utf-8")
    carve = next((l for l in kernel.splitlines() if "handoff question" in l and "JUST_GO" in l), "")
    check("kernel JUST_GO carve-out names BUILD and DEBUG", "BUILD or DEBUG handoff question" in carve and "never auto-accepted" in carve, carve)
    check("control: BUILD marked automatic is caught",
          matrix_problems(advisory.replace("| ask once | asked; non-interactive = declined |", "| auto | auto |", 1)) != [], "")
    check("control: PLAN marked ask-first is caught",
          matrix_problems(advisory.replace("| PLAN | auto, announce", "| PLAN | ask once, announce", 1)) != [], "")
    check("control: an empty file has no matrix", matrix_problems("") != [], "")
    check("control: a detection missing no_answer is caught", detection_problems(advisory.replace("no_answer", "x")) != [], "")

    print(f"\n{_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
