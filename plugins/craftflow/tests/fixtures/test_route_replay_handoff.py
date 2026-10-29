#!/usr/bin/env python3
"""H5: the handoff replay checks go RED on a hollow or reverted law (controls for craftflow_route_replay_handoff.py).

Run: python3 tests/fixtures/test_route_replay_handoff.py   (non-zero exit on any FAIL)
Literal-step controls for PP-4 and PP-5, then mutated copies of the plugin tree: each mutation must turn the named
replay check RED while an untouched copy passes.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_route_replay_handoff as rh  # noqa: E402

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


def step(op: str, **kw) -> dict:
    return {"op": op, **kw}


def literal_controls() -> None:
    ok4 = [step("gate", pending_gate="needs_info"), step("answer"), step("status", value="completed"),
           step("memory_task_created"), step("memory_finalized")]
    check("PP-4: gate, answer, terminal, memory is clean", rh.pp4_problems(ok4) == [], str(rh.pp4_problems(ok4)))
    check("PP-4: a memory task before any terminal status is caught",
          rh.pp4_problems([step("memory_task_created"), step("status", value="completed")]) != [], "")
    check("PP-4: a memory event while a gate is pending is caught",
          rh.pp4_problems([step("status", value="completed"), step("gate", pending_gate="needs_info"), step("memory_finalized")]) != [], "")
    check("PP-4: a re-asked (non-matching) answer does not clear the gate",
          rh.pp4_problems([step("gate", pending_gate="triage_agent_failed"), step("answer", outcome="re_ask"),
                           step("status", value="completed"), step("memory_task_created")]) != [], "")
    full = [step("offer"), step("answer", text="yes"), step("handoff_accepted"), step("mint", target="BUILD")]
    check("PP-5: offer, answer, accept, mint is clean", rh.pp5_problems(full) == [], str(rh.pp5_problems(full)))
    check("PP-5: a BUILD mint with no accept is caught", rh.pp5_problems([step("offer"), step("answer"), step("mint", target="BUILD")]) != [], "")
    check("PP-5: a DEBUG mint with no offer is caught", rh.pp5_problems([step("handoff_accepted"), step("mint", target="DEBUG")]) != [], "")
    check("PP-5: an accept with no user answer is caught", rh.pp5_problems([step("offer"), step("handoff_accepted")]) != [], "")
    check("PP-5: a PLAN mint needs no question", rh.pp5_problems([step("mint", target="PLAN")]) == [], "")
    check("PP-5: mint before the accept (wrong order) is caught",
          rh.pp5_problems([step("offer"), step("answer"), step("mint", target="BUILD"), step("handoff_accepted")]) != [], "")


def replay_on_copy(mutations: Dict[str, Callable[[str], str]]) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "craftflow"
        for sub in ("skills", "config", "scripts"):
            shutil.copytree(PLUGIN_ROOT / sub, root / sub, ignore=shutil.ignore_patterns("__pycache__"))
        (root / "tests" / "fixtures").mkdir(parents=True)
        for name in ("orient-inline.json", *rh.CHECKS):
            shutil.copy(PLUGIN_ROOT / "tests" / "fixtures" / name, root / "tests" / "fixtures" / name)
        for rel, fn in mutations.items():
            path = root / rel
            text = path.read_text(encoding="utf-8")
            new = fn(text)
            assert new != text, f"mutation of {rel} changed nothing"
            path.write_text(new, encoding="utf-8")
        return subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "craftflow_route_replay_check.py"),
                               "--plugin-root", str(root)], capture_output=True, text=True)


def red(name: str, mutations: Dict[str, Callable[[str], str]], failing: str) -> None:
    res = replay_on_copy(mutations)
    check(name, res.returncode == 1 and f"FAIL: {failing}" in res.stdout, f"rc={res.returncode} want 'FAIL: {failing}' in {res.stdout[-600:]}{res.stderr[-300:]}")


def mutated_copies() -> None:
    res = replay_on_copy({})
    check("an untouched plugin copy passes all four fixtures", res.returncode == 0 and "0 failed" in res.stdout, f"rc={res.returncode} {res.stdout[-400:]}{res.stderr[-300:]}")
    adv, ker = "skills/_shared/route-advisory.md", "skills/craftflow-router/SKILL.md"
    red("a hollow route-advisory.md fails the doc pins", {adv: lambda t: "### Advisory law\n\nNothing here.\n"}, "doc pins")
    red("BUILD/DEBUG marked automatic in the matrix fails the matrix check",
        {adv: lambda t: t.replace("| ask once | asked; non-interactive = declined |", "| auto | auto |", 1)}, "matrix and PP-5 transcript")
    red("PLAN marked ask-first in the matrix fails the matrix check",
        {adv: lambda t: t.replace("| PLAN | auto, announce", "| PLAN | ask once, announce", 1)}, "matrix and PP-5 transcript")
    red("a reverted kernel carve-out fails the doc pins",
        {ker: lambda t: t.replace("a BUILD or DEBUG handoff question", "a question")}, "doc pins")
    red("a reverted kernel hard rule fails the doc pins",
        {ker: lambda t: t.replace("BUILD/DEBUG targets need a recorded `handoff_accepted`, never JUST_GO.", "")}, "doc pins")
    red("losing a detail value fails the doc pins",
        {adv: lambda t: t.replace("tool_unavailable|tool_error|no_answer", "tool_unavailable|tool_error")}, "doc pins")
    red("losing the never-re-offer rule fails the doc pins",
        {adv: lambda t: t.replace("the same handoff is never offered again", "the handoff may be offered again")}, "doc pins")
    red("a CLI that skips the accept check fails the live PP-5 check",
        {"scripts/craftflow_workflow_handoff.py": lambda t: t.replace("if not accepted_event(parent_events, answer, target):", "if False:")},
        "live CLI: PP-5 and PP-6")
    red("a CLI that mints twice fails the live adopt check",
        {"scripts/craftflow_workflow_handoff.py": lambda t: t.replace('return ("already", event["child_workflow_uuid"])', "continue")},
        "live CLI: one mint, already_handed_off, decline")


def main() -> int:
    literal_controls()
    mutated_copies()
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
