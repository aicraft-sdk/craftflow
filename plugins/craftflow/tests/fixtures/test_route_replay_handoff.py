#!/usr/bin/env python3
"""H5: the handoff replay checks go RED on a hollow or reverted law (controls for craftflow_route_replay_handoff.py).

Run: python3 tests/fixtures/test_route_replay_handoff.py   (non-zero exit on any FAIL)
Literal-step controls for PP-4 and PP-5, then mutated copies of the plugin tree: each mutation must turn the named
replay check RED while an untouched copy passes.
"""
from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_route_replay_check as rc_check  # noqa: E402
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
    cli = "scripts/craftflow_workflow_handoff.py"
    claim, already = 'claim.acquire_claim(self.lock, lambda: logged_child(self.log))', 'return ("already", event["child_workflow_uuid"])'
    red("a CLI that mints twice (no claim answer, no already rule) fails the live adopt check",
        {cli: lambda t: t.replace(claim, '"acquired", None').replace(already, "continue")},
        "live CLI: one mint, already_handed_off, decline")
    red("reverting the --record-accept --just-go sentence fails the doc pins",
        {adv: lambda t: t.replace("Never pass `--just-go` to `--record-accept`; only the user's own reply is an accept, in normal and JUST_GO "
                                  "mode. The CLI exits 3 if it is passed.", "`--record-accept` with `--just-go` exits 3, because JUST_GO never accepts.")},
        "doc pins")
    red("dropping the two-paused rule from route-advisory.md fails the doc pins (resume.md still has it)",
        {adv: lambda t: t.replace("When two advisory or QA workflows are paused, ask which to resume first; resume neither without an answer. ", "")},
        "doc pins")
    red("a PLAN adopt that never runs plan_start_reads fails the adopt transcript",
        {"tests/fixtures/handoff-adopt-child.json": lambda t: t.replace(', "plan_start_reads"', "")}, "adopt transcript")


def shape_controls() -> None:
    fx = "tests/fixtures/handoff-linked-workflow.json"
    res = replay_on_copy({fx: lambda t: t.replace('"matrix_source": "CODEBASE-HEALTH"', '"matrix_source": ""')})
    check("a mis-shaped fixture (empty matrix_source) stops the replay at the shape phase",
          res.returncode == 1 and "FAIL: fixture shape" in res.stdout and "route replay: stopped, fixture shape is invalid" in res.stdout
          and "FAIL: doc pins" not in res.stdout and "matrix_source" in res.stdout, f"rc={res.returncode} {res.stdout[-500:]}")
    res = replay_on_copy({"tests/fixtures/handoff-adopt-child.json": lambda t: t.replace('"op": "adopt"', '"kind": "adopt"')})
    check("a step without `op` stops the replay at the shape phase", res.returncode == 1 and "route replay: stopped" in res.stdout, f"rc={res.returncode} {res.stdout[-300:]}")
    res = replay_on_copy({"tests/fixtures/advisory-pause-resume.json": lambda t: t.replace('"paused": ["wf-a-20261030-100000-aaaaaaaa", "wf-b-20261030-100001-bbbbbbbb"]', '"paused": 5')})
    check("`paused: 5` is a shape failure, not a traceback", res.returncode == 1 and "FAIL: fixture shape" in res.stdout and "paused" in res.stdout
          and "Traceback" not in res.stderr + res.stdout, f"rc={res.returncode} {res.stdout[-400:]}{res.stderr[-400:]}")
    res = replay_on_copy({"tests/fixtures/handoff-adopt-child.json": lambda t: t.replace('"appends": "workflow_adopted"', '"appends": 5', 1)})
    check("a mis-shaped LAST fixture stops the replay before any fixture is run",
          res.returncode == 1 and "FAIL: fixture shape" in res.stdout and "PASS: doc pins" not in res.stdout and "FAIL: doc pins" not in res.stdout,
          f"rc={res.returncode} {res.stdout[-500:]}")
    boom = lambda fx, root=None: (_ for _ in ()).throw(KeyError("pending_gate"))
    saved = rc_check.registry
    rc_check.registry = lambda: {"orient-inline.json": (lambda fx: [], boom)}
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = rc_check.main(["--fixture", str(PLUGIN_ROOT / "tests" / "fixtures" / "orient-inline.json")])
    finally:
        rc_check.registry = saved
    check("an exception in the run phase becomes `FAIL: <fixture>: <Type>: <msg>` with exit 1", code == 1
          and "FAIL: orient-inline.json: KeyError: 'pending_gate'" in out.getvalue(), f"{code} {out.getvalue()}")
    check("matrix_mode with an empty source is none, not the first row", rh.matrix_mode([("TRIAGE", "BUILD", "ask once", "asked")], "", "BUILD", False) == "none", "")
    with tempfile.TemporaryDirectory() as tmp:
        stray = Path(tmp) / "stray.json"
        shutil.copy(PLUGIN_ROOT / "tests" / "fixtures" / "orient-inline.json", stray)
        res = subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "craftflow_route_replay_check.py"), "--fixture", str(stray)],
                             capture_output=True, text=True)
        check("an unregistered fixture name is an error, not an ORIENT fallback",
              res.returncode == 1 and "Error: unregistered fixture stray.json" in res.stderr, f"rc={res.returncode} {res.stdout[-200:]}{res.stderr[-200:]}")
    try:
        rh.Lab(PLUGIN_ROOT, tempfile.mkdtemp(), "NOT-A-TYPE")
        check("a Lab whose parent init fails raises", False, "no RuntimeError")
    except RuntimeError as exc:
        check("a Lab whose parent init fails raises", "parent init failed" in str(exc), str(exc))
    check("a live check turns a lab failure into a problem", any("could not run" in p for p in rh.live(lambda r: rh.Lab(r, tempfile.mkdtemp(), "NOT-A-TYPE") and [], PLUGIN_ROOT)), "")


def main() -> int:
    literal_controls()
    mutated_copies()
    shape_controls()
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
