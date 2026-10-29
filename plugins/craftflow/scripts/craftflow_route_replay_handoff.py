#!/usr/bin/env python3
"""H5 replay checks for the advisory pause law and the handoff law (used by craftflow_route_replay_check.py).

Three fixtures, each a list of scenarios whose `steps` are the router's observable ops:
  handoff-linked-workflow.json  the binding matrix (PP-5): PLAN automatic, BUILD/DEBUG asked, decline, JUST_GO.
  advisory-pause-resume.json    pause, resume, two paused, the failure law, terminal-only memory (PP-4).
  handoff-adopt-child.json      one mint plus one `workflow_adopted`, crash recovery, non-interactive decline.
Every check reads the LIVE `route-advisory.md`, kernel, `workflow-start.md` and `resume.md` text for what it pins, and the
live handoff CLI is run in a temp project for the parts a transcript cannot prove (PP-5, PP-6, one mint).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

ADVISORY = "skills/_shared/route-advisory.md"
KERNEL = "skills/craftflow-router/SKILL.md"
START = "skills/craftflow-router/references/workflow-start.md"
RESUME = "skills/craftflow-router/references/resume.md"
POLICY = "skills/craftflow-router/references/workflow-artifact-and-hook-policy.md"
PROTOCOL = "skills/_shared/router-protocol.md"
SESSION = "0123abcd-4567-89ab"
WRITE_TARGETS = ("BUILD", "DEBUG")
PAUSE_GATES = ("candidate_choice", "needs_info", "needs_grilling", "wontfix_confirm", "qa_test_plan_approval",
               "qa_grant_needed", "qa_preflight_human_prerequisites", "qa_source_contradiction")
FAILED_GATES = ("architecture_scanner_failed", "triage_agent_failed", "qa_researcher_failed", "qa_harness_builder_failed",
                "qa_executor_failed", "planner_failed", "plan_gap_reviewer_failed", "code_reviewer_failed",
                "silent_failure_hunter_failed")
DETAILS = "tool_unavailable|tool_error|no_answer"
TERMINAL = ("completed", "failed", "ended")

# (file, pinned text): each must appear verbatim in the live file.
DOC_PINS = (
    *((ADVISORY, h) for h in ("### Advisory law", "### Advisory pauses", "### Handoff targets and modes", "### Handoff timing",
                              "### Handoff inputs", "### Never re-offer", "### Handoff failure",
                              "### Asking about a write handoff", "### Adopting the handoff child",
                              "### Cursor finalization signal")),
    (ADVISORY, "They never write product code"),
    (ADVISORY, "Memory Update runs only at a terminal state"),
    (ADVISORY, 'There is no "skip"'),
    (ADVISORY, '"skip" or "(b)" is a non-matching reply that is re-asked verbatim'),
    (ADVISORY, "JUST_GO never auto-accepts them"),
    (ADVISORY, "refuses either target without a recorded `handoff_accepted` event"),
    (ADVISORY, "--record-decline --reason non_interactive --detail " + DETAILS),
    (ADVISORY, "There is no environment-variable heuristic"),
    (ADVISORY, "the same handoff is never offered again"),
    (ADVISORY, "--record-accept --parent {wf} --target BUILD|DEBUG --answer"),
    (ADVISORY, 'skip only `references/workflow-start.md` "Parent workflow creation" step 1 (the mint)'),
    (ADVISORY, "append `workflow_adopted`"),
    (ADVISORY, "`already_handed_off=<child>`"),
    (ADVISORY, "A second mint never happens."),
    (ADVISORY, "A Cursor parent is finalized by `workflow_completed`"),
    (ADVISORY, "`--host cursor`"),
    (KERNEL, "Advisory and QA routes hand off only through `_shared/route-advisory.md` and "
             "`scripts/craftflow_workflow_handoff.py`; BUILD/DEBUG targets need a recorded `handoff_accepted`, never JUST_GO."),
    (KERNEL, "a BUILD or DEBUG handoff question"),
    (KERNEL, "never auto-accepted"),
    (START, "A child minted by `craftflow_workflow_handoff.py` in this turn is adopted, not minted again: skip step 1 only "
            "and run every other start step for its type; see `_shared/route-advisory.md` `### Adopting the handoff child`."),
    (RESUME, "When two advisory or QA workflows are paused, ask which to resume"),
    *((POLICY, f"`{t}`") for t in ("parent_workflow_uuid", "handoff", "handoff_started", "handoff_accepted",
                                   "handoff_declined", "handoff_failed", "workflow_adopted")),
    (PROTOCOL, "`## Handoff Context`"),
)


def rd(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def pin_problems(root: Path) -> List[str]:
    return [f"{rel} lacks the pinned text {pin!r}" for rel, pin in DOC_PINS if pin not in rd(root, rel)]


def section(text: str, heading: str) -> str:
    start = text.find(heading)
    if start == -1:
        return ""
    end = text.find("\n### ", start + len(heading))
    return text[start:end if end != -1 else len(text)]


def matrix_rows(route_text: str) -> List[Tuple[str, str, str, str]]:
    """(source, target, mode cell, JUST_GO cell) of the `### Handoff targets and modes` table, from the live file."""
    rows = []
    for line in section(route_text, "### Handoff targets and modes").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 4 and cells[0] not in ("Source",) and not cells[0].startswith("-"):
            rows.append((cells[0], cells[1], cells[2], cells[3]))
    return rows


def matrix_mode(rows: List[Tuple[str, str, str, str]], source: str, target: str, just_go: bool) -> str:
    for src, tgt, mode, jg in rows:
        if src.startswith(source) and target in tgt:
            cell = jg if just_go else mode
            return "auto" if cell.startswith("auto") else "asked" if cell.startswith("ask") else cell
    return "none"


def pp4_problems(steps: List[Dict[str, Any]]) -> List[str]:
    """PP-4: a memory task or event only after a terminal status, and never while a gate is pending."""
    problems, pending, terminal = [], None, False
    for i, step in enumerate(steps):
        op = step.get("op")
        if op == "gate":
            pending = step.get("pending_gate")
        elif op == "answer" and step.get("outcome") != "re_ask":
            pending = None
        elif op == "status":
            terminal = terminal or step.get("value") in TERMINAL
        elif op in ("memory_task_created", "memory_finalized"):
            if not terminal:
                problems.append(f"step {i}: {op} before a terminal status")
            if pending:
                problems.append(f"step {i}: {op} while {pending} is pending")
    return problems


def pp5_problems(steps: List[Dict[str, Any]]) -> List[str]:
    """PP-5: a BUILD/DEBUG child only after offer, a real answer, and a recorded accept, in that order."""
    problems, seen = [], []
    for i, step in enumerate(steps):
        op = step.get("op")
        if op == "mint" and step.get("target") in WRITE_TARGETS:
            for need in ("offer", "answer", "handoff_accepted"):
                if need not in seen:
                    problems.append(f"step {i}: {step.get('target')} mint without a prior {need}")
        if op == "handoff_accepted" and "answer" not in seen:
            problems.append(f"step {i}: handoff_accepted without a user answer")
        seen.append(op)
    return problems


def shape(fixture: Any, kind: str) -> List[str]:
    if not isinstance(fixture, dict) or not isinstance(fixture.get("scenarios"), list) or not fixture["scenarios"]:
        return [f"{kind} fixture needs a non-empty `scenarios` list"]
    problems = []
    for sc in fixture["scenarios"]:
        if not isinstance(sc, dict) or not sc.get("name") or not isinstance(sc.get("steps"), list) or not sc["steps"]:
            problems.append(f"scenario {sc.get('name') if isinstance(sc, dict) else sc!r} lacks `name` or `steps`")
        elif any(not isinstance(s, dict) or "op" not in s for s in sc["steps"]):
            problems.append(f"scenario {sc['name']} has a step without `op`")
    return problems


class Lab:
    """A temp project with one finalized parent, driven through the live scripts of `root`."""

    def __init__(self, root: Path, tmp: str, parent_type: str, finalized: bool = True) -> None:
        base = Path(tmp)
        base.mkdir(parents=True, exist_ok=True)
        self.root, self.project = root, base / "project"
        (self.project / ".craftflow" / "state").mkdir(parents=True)
        self.env = dict(os.environ, CLAUDE_CODE_SESSION_ID=SESSION)
        res = subprocess.run([sys.executable, str(root / "scripts" / "craftflow_workflow_id.py"), "--init-artifact",
                              "--project", str(self.project), "--workflow-type", parent_type, "--phase", parent_type.lower(),
                              "--request", "Review the router", "--task-tools-available", "false"],
                             capture_output=True, text=True, env=self.env, cwd=str(self.project))
        self.parent = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
        self.dir = self.project / ".craftflow" / "state" / "workflows"
        (self.project / "d.md").write_text("# design\n", encoding="utf-8")
        if finalized:
            with (self.dir / f"{self.parent}.events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": "2026-10-30T00:00:00Z", "wf": self.parent, "event": "memory_finalized"}) + "\n")

    def sha(self) -> str:
        return hashlib.sha256((self.dir / f"{self.parent}.json").read_bytes()).hexdigest()

    def cli(self, *args: str) -> Tuple[int, str, str]:
        res = subprocess.run([sys.executable, str(self.root / "scripts" / "craftflow_workflow_handoff.py"),
                              "--project", str(self.project), "--parent", self.parent, *args],
                             capture_output=True, text=True, env=self.env, cwd=str(self.project))
        return res.returncode, res.stdout, res.stderr

    def children(self) -> List[str]:
        return sorted(p.stem for p in self.dir.glob("wf-*.json") if p.stem != self.parent)

    def events(self, wf: str) -> List[Dict[str, Any]]:
        path = self.dir / f"{wf}.events.jsonl"
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def value(out: str, key: str) -> str:
    return next((l.split("=", 1)[1] for l in out.splitlines() if l.startswith(key + "=")), "")


PLAN_ARGS = ("--target", "PLAN", "--input", "d.md", "--input-kind", "design", "--source-route", "CODEBASE-HEALTH")
BUILD_ARGS = ("--target", "BUILD", "--input", "d.md", "--input-kind", "brief", "--source-route", "TRIAGE")


def live_matrix_problems(root: Path) -> List[str]:
    """PP-5 and PP-6 on the real CLI: PLAN mints, BUILD/DEBUG refuse without an accept (also under --just-go), a decline
    leaves no child, and the parent artifact never changes."""
    problems: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        lab = Lab(root, os.path.join(tmp, "triage"), "TRIAGE")
        before = lab.sha()
        for label, args in (("BUILD without accept", BUILD_ARGS),
                            ("--just-go DEBUG without accept", ("--just-go", "--target", "DEBUG", "--input", "d.md"))):
            code, _, err = lab.cli(*args)
            if code != 3 or lab.children():
                problems.append(f"{label}: exit {code} (want 3), children {lab.children()}, stderr {err.strip()!r}")
        code = lab.cli("--record-accept", "--just-go", "--target", "BUILD", "--answer", "yes")[0]
        if code != 3:
            problems.append(f"--record-accept --just-go: exit {code} (want 3)")
        declined = lab.cli("--record-decline", "--reason", "user_declined")[0]
        refused = lab.cli(*BUILD_ARGS)[0]
        if declined != 0 or refused != 3 or lab.children():
            problems.append(f"decline then BUILD: decline exit {declined}, BUILD exit {refused}, children {lab.children()}")
        if lab.sha() != before:
            problems.append("PP-6: the parent artifact sha256 changed")
        plan = Lab(root, os.path.join(tmp, "plan"), "CODEBASE-HEALTH")
        before = plan.sha()
        code, out, err = plan.cli(*PLAN_ARGS)
        if code != 0 or not value(out, "child_workflow_uuid").startswith("wf-") or plan.sha() != before:
            problems.append(f"PLAN handoff: exit {code}, out {out.strip()!r}, err {err.strip()!r}, parent unchanged {plan.sha() == before}")
    return problems


def check_linked(fixture: Any, root: Path) -> List[Tuple[str, List[str]]]:
    rows = matrix_rows(rd(root, ADVISORY))
    problems: List[str] = []
    for sc in fixture.get("scenarios", []):
        exp, steps = sc.get("expected", {}), sc.get("steps", [])
        jg = bool(sc.get("just_go"))
        mode = matrix_mode(rows, sc.get("matrix_source", ""), sc.get("target", ""), jg)
        if mode != exp.get("mode"):
            problems.append(f"{sc['name']}: the live matrix says {mode!r}, the fixture expects {exp.get('mode')!r}")
        offered = any(s["op"] == "offer" for s in steps)
        if offered != (mode == "asked") or offered != exp.get("offered"):
            problems.append(f"{sc['name']}: offered={offered} but the live matrix mode is {mode!r} (fixture offered={exp.get('offered')!r})")
        minted = any(s["op"] == "mint" for s in steps)
        if minted != exp.get("child_minted"):
            problems.append(f"{sc['name']}: child_minted={minted}, fixture expects {exp.get('child_minted')!r}")
        if any(s["op"] == "handoff_declined" for s in steps) and minted:
            problems.append(f"{sc['name']}: a declined handoff minted a child")
        if jg and any(s["op"] == "handoff_accepted" for s in steps):
            problems.append(f"{sc['name']}: JUST_GO auto-accepted a write handoff")
        problems += [f"{sc['name']}: {p}" for p in pp5_problems(steps)]
    return [("fixture shape", shape(fixture, "handoff")), ("doc pins", pin_problems(root)),
            ("matrix and PP-5 transcript", problems), ("live CLI: PP-5 and PP-6", live_matrix_problems(root))]


def check_pause(fixture: Any, root: Path) -> List[Tuple[str, List[str]]]:
    advisory, resume = rd(root, ADVISORY), rd(root, RESUME)
    law, pauses = section(advisory, "### Advisory law"), section(advisory, "### Advisory pauses")
    problems: List[str] = []
    for gate in PAUSE_GATES:
        if f"`{gate}`" not in pauses:
            problems.append(f"### Advisory pauses does not list `{gate}`")
    for gate in FAILED_GATES:
        if f"`{gate}`" not in law:
            problems.append(f"### Advisory law does not list `{gate}`")
    for sc in fixture.get("scenarios", []):
        steps, pending = sc.get("steps", []), None
        for step in steps:
            op = step["op"]
            if op == "gate":
                pending = step.get("pending_gate")
                if pending not in PAUSE_GATES + FAILED_GATES:
                    problems.append(f"{sc['name']}: unknown gate {pending!r}")
            elif op == "resume" and "paused" in step:
                if len(step["paused"]) > 1 and step.get("asks") != "which":
                    problems.append(f"{sc['name']}: two paused workflows must ask which")
            elif op == "resume" and step.get("re_asks") != pending:
                problems.append(f"{sc['name']}: resume re-asks {step.get('re_asks')!r}, pending is {pending!r}")
            elif op == "answer" and pending and pending.endswith("_failed") and step.get("text") in ("skip", "(b)") \
                    and step.get("outcome") != "re_ask":
                problems.append(f"{sc['name']}: a skip answer to {pending} was accepted")
            elif op == "answer" and step.get("outcome") != "re_ask":
                pending = None
        problems += [f"{sc['name']}: {p}" for p in pp4_problems(steps)]
        if any(s["op"] == "memory_task_created" for s in steps) and "pending_gate" in json.dumps(sc.get("final", {})):
            problems.append(f"{sc['name']}: ends with a gate pending yet created a memory task")
    if "ask which to resume" not in resume:
        problems.append("resume.md lacks the two-paused rule")
    return [("fixture shape", shape(fixture, "pause")), ("doc pins", pin_problems(root)), ("pause, resume and PP-4 transcript", problems)]


def live_adopt_problems(root: Path) -> List[str]:
    problems: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        lab = Lab(root, os.path.join(tmp, "plan"), "CODEBASE-HEALTH")
        before = lab.sha()
        code, out, err = lab.cli(*PLAN_ARGS)
        child = value(out, "child_workflow_uuid")
        again = lab.cli(*PLAN_ARGS)
        started = [e for e in lab.events(child) if e.get("event") == "workflow_started"] if child else []
        if code != 0 or not child:
            problems.append(f"mint: exit {code}, stdout {out.strip()!r}, stderr {err.strip()!r}")
        if again[0] != 0 or value(again[1], "already_handed_off") != child or lab.children() != [child] or len(started) != 1:
            problems.append(f"second call: {again[:2]}, children {lab.children()}, child workflow_started count {len(started)}")
        if lab.sha() != before:
            problems.append("PP-6: the parent artifact sha256 changed")
        lab2 = Lab(root, os.path.join(tmp, "triage"), "TRIAGE")
        rc, _, _ = lab2.cli("--record-decline", "--reason", "non_interactive", "--detail", "tool_unavailable", "--target", "BUILD")
        declined = [e for e in lab2.events(lab2.parent) if e.get("event") == "handoff_declined"]
        if rc != 0 or [(e.get("reason"), e.get("detail")) for e in declined] != [("non_interactive", "tool_unavailable")]:
            problems.append(f"non-interactive decline not recorded with detail tool_unavailable: rc={rc} {declined}")
        if lab2.cli(*BUILD_ARGS)[0] != 3 or lab2.children():
            problems.append("a declined BUILD handoff was minted or not refused with exit 3")
    return problems


def check_adopt(fixture: Any, root: Path) -> List[Tuple[str, List[str]]]:
    adopting = section(rd(root, ADVISORY), "### Adopting the handoff child")
    problems: List[str] = []
    for sc in fixture.get("scenarios", []):
        steps, exp = sc.get("steps", []), sc.get("expected", {})
        adopts = [s for s in steps if s["op"] == "adopt"]
        mints = [s for s in steps if s["op"] == "mint_call"]
        cli_mints = [s for s in steps if s["op"] == "cli" and s.get("prints") == "child_workflow_uuid"]
        if len(adopts) != exp.get("workflow_adopted", 0):
            problems.append(f"{sc['name']}: {len(adopts)} adopt steps, fixture expects {exp.get('workflow_adopted')}")
        if mints:
            problems.append(f"{sc['name']}: the router called the mint helper again ({len(mints)} mint_call steps)")
        if len(cli_mints) != exp.get("child_workflow_started", 0):
            problems.append(f"{sc['name']}: {len(cli_mints)} child mints, fixture expects {exp.get('child_workflow_started')}")
        for step in adopts:
            if step.get("skips") != ["mint"] or step.get("appends") != "workflow_adopted":
                problems.append(f"{sc['name']}: adopt must skip only the mint and append workflow_adopted: {step}")
            for phrase in ("skip only", "workflow_adopted", "every other start step"):
                if phrase not in adopting:
                    problems.append(f"### Adopting the handoff child lacks {phrase!r}")
        if sc.get("crash") and not any(s["op"] == "cli" and s.get("prints") == "already_handed_off" for s in steps):
            problems.append(f"{sc['name']}: the crash scenario never replays the CLI to already_handed_off")
        for step in steps:
            if step["op"] == "handoff_declined" and (step.get("reason"), step.get("detail")) != ("non_interactive", "tool_unavailable"):
                problems.append(f"{sc['name']}: the non-interactive decline must carry detail tool_unavailable: {step}")
        if any(s["op"] == "handoff_declined" for s in steps) and any(s["op"] in ("mint", "cli") for s in steps):
            problems.append(f"{sc['name']}: a declined offer minted a child")
    return [("fixture shape", shape(fixture, "adopt")), ("doc pins", pin_problems(root)),
            ("adopt transcript", problems), ("live CLI: one mint, already_handed_off, decline", live_adopt_problems(root))]


CHECKS: Dict[str, Callable[[Any, Path], List[Tuple[str, List[str]]]]] = {
    "handoff-linked-workflow.json": check_linked,
    "advisory-pause-resume.json": check_pause,
    "handoff-adopt-child.json": check_adopt,
}
