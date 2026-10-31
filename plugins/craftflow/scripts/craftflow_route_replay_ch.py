#!/usr/bin/env python3
"""H6 replay checks for the CODEBASE-HEALTH route (used by craftflow_route_replay_check.py).

One fixture, `codebase-health-candidate-choice.json`: scenarios whose `steps` are the router's observable ops (announce, mint,
scanner contract, report shown, gate, question, answer, grill, design saved, terminal status, memory, handoff). The checks read
the LIVE route law, graph, kernel pointer, protocol dispatcher row, read map and Cursor binding of the plugin root being checked:
  routing         every scenario request routes to CODEBASE-HEALTH under the routing law.
  doc pins        the law sentences the transcript relies on appear verbatim in the live files (`pin_problems`).
  transcript      the scanner contract outcome follows the validation rule; the choice question, the JUST_GO pick, the grill
                  (gate stays `candidate_choice`), the design capture, terminal-only memory (PP-4) and the PLAN handoff order hold.
  registry        the two files are registered in the read map, are no longer stubs and fit the byte cap; the dispatcher row exists.
  Cursor          the CODEBASE-HEALTH chain and the `candidate_choice` gate row exist in routes.md.
  live CLI        a CODEBASE-HEALTH parent mints, finalizes and hands off to a linked PLAN child through the real scripts.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import craftflow_route_replay_handoff as hand  # noqa: E402
import craftflow_routing_law as law  # noqa: E402

ROUTE = "skills/_shared/route-codebase-health.md"
GRAPH = "skills/craftflow-router/references/codebase-health-workflow.md"
KERNEL = "skills/craftflow-router/SKILL.md"
PROTOCOL = "skills/_shared/router-protocol.md"
START = "skills/craftflow-router/references/workflow-start.md"
CURSOR_ROUTES = "skills/cursor-router/references/routes.md"
FILE_CAP = 18_500
DESIGN_RE = re.compile(r"^docs/plans/\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*-design\.md$")
FAILED = "architecture_scanner_failed"

# (file, pinned text): each must appear verbatim in the live file; each is proven by a mutated-copy control in the test.
DOC_PINS = (
    (ROUTE, "### CODEBASE-HEALTH preparation"),
    (ROUTE, "### CODEBASE-HEALTH candidate choice"),
    (ROUTE, "### CODEBASE-HEALTH grill"),
    (ROUTE, "### CODEBASE-HEALTH terminal and handoff"),
    (ROUTE, "### CODEBASE-HEALTH contract validation"),
    (ROUTE, "### CODEBASE-HEALTH phases"),
    (ROUTE, "The only CODEBASE-HEALTH phase tokens are `codebase-health` and `memory-finalize`."),
    (ROUTE, "Dispatch the scanner with effort `high`."),
    (ROUTE, "Ask one question that lists every candidate by number in its question text, with \"None\" as an option"),
    (ROUTE, "with more than 3 candidates the options are \"1\", \"2\", \"3\", \"None\" and any other number is typed as free text"),
    (ROUTE, "Under JUST_GO the router asks no question and picks `TOP_RECOMMENDATION`, else the first `Strong` candidate, "
            "else the first candidate, and logs the pick in `## Decisions`."),
    (ROUTE, "the request for the health check authorizes the whole route, so do not stop at the report to ask whether to continue"),
    (ROUTE, "Under JUST_GO the grill is not skipped: answer each brainstorming question with its recommended option"),
    (ROUTE, "A reply that is neither a listed number nor \"None\" is re-asked verbatim, except a fresh unrelated request, "
            "which counts as a decline."),
    (ROUTE, "Seed `craftflow:brainstorming` with the picked candidate card"),
    (ROUTE, "Run every `scripts/` helper named here from the loaded plugin"),
    (ROUTE, "a reply that ends with a saved design and no handoff is a failed route"),
    (ROUTE, "docs/plans/YYYY-MM-DD-<slug>-design.md"),
    (ROUTE, "results.codebase_health.design_file"),
    (ROUTE, "The gate stays `candidate_choice` through the grill: while the grill runs every reply is a grill answer, "
            "and `pending_gate` clears only when the design file is saved."),
    (ROUTE, "\"None\" ends the workflow as completed-declined: terminal memory, no handoff."),
    (ROUTE, "`NO_CANDIDATES` completes the workflow with no gate and no handoff."),
    (ROUTE, "-> handing off to PLAN (design: {path})"),
    (ROUTE, "`CANDIDATES_FOUND` is valid only when the file at `REPORT_PATH` exists on disk and `CANDIDATE_COUNT` equals "
            "`len(CANDIDATES)` and is at least 1"),
    (ROUTE, "`NO_CANDIDATES` is valid only with `CANDIDATE_COUNT` 0 and an empty `CANDIDATES`."),
    (ROUTE, "`TOP_RECOMMENDATION` must be null or between 1 and `CANDIDATE_COUNT`."),
    (ROUTE, "set `pending_gate` to `architecture_scanner_failed`"),
    (ROUTE, "--workflow-uuid {workflow_uuid}"),
    (ROUTE, "`REPORT_PATH` must equal `.craftflow/codebase-health/{workflow_uuid}/report.html` exactly (project-relative); "
            "any other value is `architecture_scanner_failed`."),
    (GRAPH, "--workflow-type CODEBASE-HEALTH --phase codebase-health"),
    (GRAPH, "kind:agent\\norigin:router\\nphase:codebase-health\\nplan:N/A"),
    (GRAPH, "open \"{REPORT_PATH}\""),
    (GRAPH, "only on darwin"),
    (GRAPH, "only after step 1 passed"),
    (GRAPH, "--workflow-uuid {workflow_uuid}"),
    (GRAPH, "Hand off now, in the same turn and before the final reply"),
    (GRAPH, "craftflow_workflow_handoff.py --project {PROJECT_ROOT} --parent {workflow_uuid} --target PLAN --input {design_file} --input-kind design --source-route CODEBASE-HEALTH"),
    (GRAPH, "Asking whether to start the PLAN is wrong"),
    (GRAPH, "craftflow_contract_validate_pilot.py --agent architecture-scanner"),
    (GRAPH, "Under JUST_GO do not end the turn after the report"),
    (GRAPH, "Create the Memory Update task only at a terminal state"),
    (GRAPH, "is created first from the `craftflow:session-memory` template, and is never a reason to stop before `memory_finalized`"),
    (GRAPH, "Completion is router-owned under task-tool fallback"),
    (KERNEL, "### CODEBASE-HEALTH pointer"),
    (KERNEL, "first print `-> CODEBASE-HEALTH workflow (signals: ...)`"),
    (KERNEL, "then `Read()` `skills/_shared/route-codebase-health.md` and `references/codebase-health-workflow.md`."),
    (PROTOCOL, "| `codebase-health` | `craftflow:architecture-scanner` |"),
)
# The four `candidate_choice` rows of `### Cursor route gate answers`: reply cell -> text its action cell must carry.
CHOICE_ROWS = (
    ("any reply once a candidate is picked (the grill is running)", ("grill answer", "stays `candidate_choice`")),
    ("a listed candidate number", ("run the grill",)),
    ("\"None\"", ("no handoff", "Memory Update", "clear `pending_gate`")),
    ("anything else", ("re-ask", "verbatim", "keep `pending_gate`")),
)
CHOICE_REPLIES = tuple(reply for reply, _ in CHOICE_ROWS)
CURSOR_PINS = ("generalPurpose", "plain text", "`candidate_choice`", "`cursor-wf.json`", "--host cursor",
               "route-codebase-health.md", "route-advisory.md", "\"None\"")

STR, NSTR, LIST, DICT, BOOL, INT, NINT = (str,), (str, type(None)), (list,), (dict,), (bool,), (int,), (int, type(None))
STEP_KEYS: Dict[str, Dict[str, Tuple[type, ...]]] = {
    "announce": {"text": STR}, "mint": {"workflow_type": STR, "phase": STR},
    "scanner": {"status": STR, "candidate_count": INT, "strengths": LIST, "top": NINT, "report_exists": BOOL, "outcome": STR},
    "scanner_failed": {}, "report_shown": {"how": STR, "platform": STR}, "gate": {"pending_gate": NSTR}, "question": {"options": LIST},
    "answer": {"text": STR, "outcome": STR}, "auto_pick": {"candidate": INT}, "grill": {"candidate": INT, "pending_gate": STR},
    "design_saved": {"path": STR, "captured_in": STR}, "status": {"value": STR}, "memory_task_created": {}, "memory_finalized": {},
    "handoff": {"target": STR, "input_kind": STR, "announce": STR},
}
SCENARIO_KEYS = {"name": STR, "request": STR, "just_go": BOOL, "expected": DICT, "steps": LIST}


def rd(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def pin_problems(read: Callable[[str], str]) -> List[str]:
    """Every pinned sentence must appear in the live text of its file (`read(rel)` returns the text)."""
    return [f"{rel} lacks the pinned text {pin!r}" for rel, pin in DOC_PINS if pin not in read(rel)]


def section(text: str, heading: str) -> str:
    """Body of the `### heading` line (exact match) up to the next `### ` heading, or ''."""
    match = re.search(r"^### " + re.escape(heading) + r"[ \t]*$", text, re.MULTILINE)
    if not match:
        return ""
    nxt = re.search(r"^#{1,3} ", text[match.end():], re.MULTILINE)
    return text[match.end():match.end() + nxt.start()] if nxt else text[match.end():]


def contract_outcome(step: Dict[str, Any]) -> str:
    """The `### CODEBASE-HEALTH contract validation` rule applied to a scanner step: 'ok' or the failure gate value."""
    count, strengths, top = step["candidate_count"], step["strengths"], step["top"]
    if top is not None and not 1 <= top <= count:
        return FAILED
    if step["status"] == "NO_CANDIDATES":
        return "ok" if count == 0 and not strengths else FAILED
    if step["status"] == "CANDIDATES_FOUND":
        return "ok" if step["report_exists"] and count == len(strengths) and count >= 1 else FAILED
    return FAILED


def expected_pick(step: Dict[str, Any]) -> int:
    """The JUST_GO rule: TOP_RECOMMENDATION, else the first Strong candidate, else the first."""
    if step["top"] is not None:
        return step["top"]
    return next((i for i, s in enumerate(step["strengths"], 1) if s == "Strong"), 1)


def shape(fixture: Any) -> List[str]:
    if not isinstance(fixture, dict) or not isinstance(fixture.get("scenarios"), list) or not fixture["scenarios"]:
        return ["fixture needs a non-empty `scenarios` list"]
    problems: List[str] = []
    for sc in fixture["scenarios"]:
        if not isinstance(sc, dict) or not isinstance(sc.get("name"), str) or not sc["name"]:
            problems.append(f"scenario {sc!r} lacks a `name`")
            continue
        label = f"scenario {sc['name']}"
        missing = [k for k in SCENARIO_KEYS if k not in sc]
        if missing or not sc.get("steps"):
            problems.append(f"{label} lacks {missing or ['non-empty steps']}")
            continue
        problems += hand.typed_problems(label, sc, SCENARIO_KEYS)
        if isinstance(sc["expected"], dict) and sc["expected"].get("path") not in ("handoff", "completed_no_handoff", "failed"):
            problems.append(f"{label} expected.path must be handoff, completed_no_handoff or failed")
        for i, step in enumerate(sc["steps"]):
            if not isinstance(step, dict) or step.get("op") not in STEP_KEYS:
                problems.append(f"{label} step {i} has no known `op` ({step!r}; expected one of {', '.join(STEP_KEYS)})")
                continue
            missing = [k for k in STEP_KEYS[step["op"]] if k not in step]
            problems += [f"{label} step {i} ({step['op']}) lacks `{k}`" for k in missing]
            problems += hand.typed_problems(f"{label} step {i} ({step['op']})", step, STEP_KEYS[step["op"]])
    if problems:
        return problems
    return coverage_problems(fixture["scenarios"])


def coverage_problems(scenarios: List[Dict[str, Any]]) -> List[str]:
    """The exit criteria's five paths must each have a scenario, so deleting one is a shape failure."""
    ops = lambda sc: [s["op"] for s in sc["steps"]]  # noqa: E731
    scans = lambda sc: [s for s in sc["steps"] if s["op"] == "scanner"]  # noqa: E731
    wants = (
        ("a CANDIDATES_FOUND -> choice -> grill -> terminal -> PLAN handoff scenario",
         lambda sc: not sc["just_go"] and sc["expected"]["path"] == "handoff" and "question" in ops(sc) and "handoff" in ops(sc)),
        ("a NO_CANDIDATES scenario", lambda sc: any(s["status"] == "NO_CANDIDATES" for s in scans(sc))),
        ("a declined (\"None\") scenario",
         lambda sc: any(s["op"] == "answer" and s["outcome"] == "declined" and s["text"] == "None" for s in sc["steps"])),
        ("a JUST_GO scenario that picks the top recommendation",
         lambda sc: sc["just_go"] and any(s["top"] is not None for s in scans(sc)) and "auto_pick" in ops(sc)),
        ("a JUST_GO scenario with no top recommendation", lambda sc: sc["just_go"] and any(s["top"] is None and s["strengths"] for s in scans(sc))),
        ("a more-than-3-candidates scenario asking 1, 2, 3, None", lambda sc: any(s["op"] == "scanner" and s["candidate_count"] > 3 for s in sc["steps"])
         and any(s["op"] == "question" and s["options"] == ["1", "2", "3", "None"] for s in sc["steps"])),
        ("a scanner failure scenario", lambda sc: any(s["op"] == "gate" and s["pending_gate"] == FAILED for s in sc["steps"])),
    )
    return [f"the fixture lacks {label}" for label, ok in wants if not any(ok(sc) for sc in scenarios)]


def sequence_problems(sc: Dict[str, Any]) -> List[str]:
    steps, jg, exp, name = sc["steps"], sc["just_go"], sc["expected"], sc["name"]
    out: List[str] = []
    first, second = steps[0], steps[1] if len(steps) > 1 else {}
    if first["op"] != "announce" or not first["text"].startswith("-> CODEBASE-HEALTH workflow (signals: "):
        out.append("the first step must announce `-> CODEBASE-HEALTH workflow (signals: ...)`")
    if second.get("op") != "mint" or (second.get("workflow_type"), second.get("phase")) != ("CODEBASE-HEALTH", "codebase-health"):
        out.append("the second step must mint CODEBASE-HEALTH with phase codebase-health")
    scan: Optional[Dict[str, Any]] = None
    pending: Optional[str] = None
    picked: Optional[int] = None
    grilling = grilled = unshown = False
    saved: Optional[str] = None
    memory = terminal = False
    handoffs = 0
    final = ""
    for i, s in enumerate(steps):
        op = s["op"]
        if unshown and op != "report_shown":
            out.append(f"step {i}: {op} before report_shown (a valid scan shows its report first)")
            unshown = False
        if op == "scanner":
            if contract_outcome(s) != s["outcome"]:
                out.append(f"step {i}: the validation rule gives {contract_outcome(s)!r}, the fixture says {s['outcome']!r}")
            scan = s if s["outcome"] == "ok" else None
            unshown = scan is not None
        elif op == "report_shown":
            want = "open" if s["platform"] == "darwin" else "print"
            if scan is None or s["how"] != want:
                out.append(f"step {i}: the report is shown with {want!r} on {s['platform']} and only after a valid scan")
            unshown = False
        elif op == "gate":
            pending = s["pending_gate"]
            if pending == "candidate_choice" and (jg or scan is None or scan["status"] != "CANDIDATES_FOUND"):
                out.append(f"step {i}: candidate_choice needs a valid CANDIDATES_FOUND scan and no JUST_GO")
            if pending == FAILED and scan is not None:
                out.append(f"step {i}: {FAILED} after a valid scan")
        elif op == "question":
            numbers = [str(n) for n in range(1, (scan or {"candidate_count": 0})["candidate_count"] + 1)]
            want_opts = numbers[:3] + ["None"]  # AskUserQuestion takes 4 options: a number past 3 is typed as free text
            if jg or s["options"] != want_opts:
                out.append(f"step {i}: the single question must offer {want_opts} (all numbers in its text) and never run under JUST_GO")
        elif op == "auto_pick":
            if not jg or scan is None or s["candidate"] != expected_pick(scan):
                out.append(f"step {i}: JUST_GO must pick {expected_pick(scan) if scan else None}, got {s['candidate']}")
            picked, grilling, grilled = s["candidate"], True, False
        elif op == "answer":
            if pending == "candidate_choice":
                numbers = [str(n) for n in range(1, (scan or {"candidate_count": 0})["candidate_count"] + 1)]
                if s["outcome"] == "picked" and s["text"] in numbers:
                    picked, grilling, grilled, pending = int(s["text"]), True, False, "candidate_choice"
                elif s["outcome"] == "declined" and s["text"] not in numbers:
                    pending = None
                else:
                    out.append(f"step {i}: answer {s['text']!r} with outcome {s['outcome']!r} is not a pick of {numbers} or a decline")
            elif pending == FAILED:
                if s["text"] in ("skip", "(b)") and s["outcome"] != "re_ask":
                    out.append(f"step {i}: {s['text']!r} was accepted at {FAILED}")
                elif s["outcome"] in ("retried", "ended") and s["text"] in ("retry", "end"):
                    pending = None
        elif op == "grill":
            if s["candidate"] != picked or s["pending_gate"] != "candidate_choice":
                out.append(f"step {i}: the grill must run on the picked candidate {picked} with the gate still candidate_choice")
            grilled = True
        elif op == "design_saved":
            if not DESIGN_RE.match(s["path"]) or s["captured_in"] != "results.codebase_health.design_file" or not grilling:
                out.append(f"step {i}: the design must be docs/plans/YYYY-MM-DD-<slug>-design.md, captured after a grill")
            if not grilled:
                out.append(f"step {i}: the design was saved with no grill step between the pick and design_saved")
            saved, grilling, grilled, pending = s["path"], False, False, None
        elif op in ("status", "memory_task_created", "memory_finalized"):
            if grilling:
                out.append(f"step {i}: {op} while the grill runs (the gate must stay candidate_choice)")
            if op == "status":
                terminal, final = True, s["value"]
            else:
                memory = memory or op == "memory_finalized"
                if not terminal:
                    out.append(f"step {i}: {op} before a terminal status")
        elif op == "handoff":
            handoffs += 1
            if not (memory and saved and s["target"] == "PLAN" and s["input_kind"] == "design"
                    and s["announce"] == f"-> handing off to PLAN (design: {saved})"):
                out.append(f"step {i}: the handoff is PLAN, after memory_finalized, announced with the saved design path: {s}")
    if unshown:
        out.append("the scenario ends after a valid scan without report_shown")
    if pending and pending != FAILED:
        out.append(f"the scenario ends with {pending} still pending")
    path = "handoff" if handoffs else "failed" if final == "failed" else "completed_no_handoff"
    if path != exp["path"]:
        out.append(f"the transcript ends on path {path!r}, the fixture expects {exp['path']!r}")
    if bool(handoffs) != bool(exp.get("child_minted")):
        out.append(f"child minted={bool(handoffs)}, the fixture expects {exp.get('child_minted')!r}")
    if "candidate_picked" in exp and exp["candidate_picked"] != picked:
        out.append(f"candidate picked {picked}, the fixture expects {exp['candidate_picked']}")
    if handoffs > 1:
        out.append("more than one handoff")
    if saved and not handoffs:
        out.append("the saved design was never handed off to PLAN")
    return [f"{name}: {p}" for p in out + hand.pp4_problems(steps)]


def routing_problems(fixture: Dict[str, Any]) -> List[str]:
    return [f"{sc['name']}: {sc['request']!r} routes to {got}, not CODEBASE-HEALTH" for sc in fixture["scenarios"]
            if (got := law.route(sc["request"])["workflow"]) != "CODEBASE-HEALTH"]


def registry_problems(root: Path) -> List[str]:
    readmap = json.loads(rd(root, "config/router-readmap.json"))
    problems: List[str] = []
    if readmap.get("variants", {}).get("CODEBASE-HEALTH") != ["standard"]:
        problems.append("read map variants.CODEBASE-HEALTH is not ['standard']")
    for rel, cls in ((ROUTE, "start"), (GRAPH, "start")):
        entries = [e for e in readmap.get("files", []) if e.get("file") == rel]
        if len(entries) != 1 or entries[0].get("class") != cls or entries[0].get("workflows") != ["CODEBASE-HEALTH"]:
            problems.append(f"read map needs exactly one {cls} entry for {rel} limited to CODEBASE-HEALTH: {entries!r}")
        text = rd(root, rel)
        if "not yet available" in text.lower() or "(pending)" in text or len(text.encode("utf-8")) > FILE_CAP:
            problems.append(f"{rel} is still a stub or over {FILE_CAP} B ({len(text.encode('utf-8'))})")
    phases = section(rd(root, ROUTE), "CODEBASE-HEALTH phases")
    enum = next((l for l in rd(root, START).splitlines() if l.startswith("phase:{")), "")
    for token in ("codebase-health", "memory-finalize"):
        if f"`{token}`" not in phases or token not in enum:
            problems.append(f"phase token {token} is missing from `### CODEBASE-HEALTH phases` or the task-metadata enum")
    table = {}
    for line in rd(root, PROTOCOL).split("## Explicit Dispatcher", 1)[-1].splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 2:
            table[cells[0]] = cells[1]
    if table.get("`codebase-health`") != "`craftflow:architecture-scanner`":
        problems.append(f"dispatcher maps `codebase-health` to {table.get('`codebase-health`')!r}")
    return problems


def cursor_problems(root: Path) -> List[str]:
    routes = rd(root, CURSOR_ROUTES)
    body = section(routes, "CODEBASE-HEALTH")
    problems = [f"{CURSOR_ROUTES} ### CODEBASE-HEALTH lacks {p!r}" for p in CURSOR_PINS if p not in body]
    gates = section(routes, "Cursor route gate answers")
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in gates.splitlines() if l.startswith("| `candidate_choice`")]
    if len(rows) != len(CHOICE_ROWS):
        problems.append(f"the `candidate_choice` gate has {len(rows)} rows, expected {len(CHOICE_ROWS)}")
    for reply, needs in CHOICE_ROWS:
        action = next((r[2] for r in rows if len(r) == 3 and r[1] == reply), None)
        if action is None:
            problems.append(f"the `candidate_choice` gate has no row for the reply {reply!r}")
        else:
            problems += [f"the `candidate_choice` row for {reply!r} lacks {n!r}" for n in needs if n not in action]
    if "skip" in " ".join(" ".join(r) for r in rows).lower():
        problems.append("a candidate_choice gate row offers skip")
    if "AskUserQuestion" in body:
        problems.append("the Cursor chain uses AskUserQuestion")
    return problems


def live_problems(root: Path) -> List[str]:
    """A CODEBASE-HEALTH parent mints with phase codebase-health, finalizes, and hands a saved design to a linked PLAN child."""
    problems: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        lab = hand.Lab(root, os.path.join(tmp, "ch"), "CODEBASE-HEALTH")
        parent = json.loads((lab.dir / f"{lab.parent}.json").read_text(encoding="utf-8"))
        started = next((h for h in parent.get("status_history", []) if h.get("event") == "workflow_started"), {})
        if (parent.get("workflow_type"), started.get("phase")) != ("CODEBASE-HEALTH", "codebase-health"):
            problems.append(f"parent artifact is {parent.get('workflow_type')} started in phase {started.get('phase')}")
        code, out, err = lab.cli(*hand.PLAN_ARGS)
        child = hand.value(out, "child_workflow_uuid")
        if code != 0 or not child:
            return problems + [f"PLAN handoff: exit {code}, out {out.strip()!r}, err {err.strip()!r}"]
        art = json.loads((lab.dir / f"{child}.json").read_text(encoding="utf-8"))
        link = art.get("handoff") or {}
        got = (art.get("workflow_type"), art.get("parent_workflow_uuid"), link.get("source_route"), link.get("input_kind"), link.get("input_path"))
        if got != ("PLAN", lab.parent, "CODEBASE-HEALTH", "design", "d.md"):
            problems.append(f"child artifact link is {got}")
        names = [e.get("event") for e in lab.events(lab.parent)]
        if "handoff_started" not in names:
            problems.append(f"parent log lacks handoff_started: {names}")
    return problems


def check_ch(fixture: Any, root: Path) -> List[Tuple[str, List[str]]]:
    seq: List[str] = []
    for sc in fixture["scenarios"]:
        seq += sequence_problems(sc)
    return [
        ("routing law sends the requests to CODEBASE-HEALTH", routing_problems(fixture)),
        ("doc pins", pin_problems(lambda rel: rd(root, rel))),
        ("scanner contract, choice, grill, terminal and handoff transcript", seq),
        ("registry, stub removal, phases and dispatcher row", registry_problems(root)),
        ("Cursor chain and candidate_choice gate row", cursor_problems(root)),
        ("live CLI: CODEBASE-HEALTH mint and PLAN handoff", hand.live(live_problems, root)),
    ]


# fixture name -> (shape, run(fixture, root)); same registry shape as craftflow_route_replay_handoff.CHECKS
CHECKS: Dict[str, Tuple[Callable[[Any], List[str]], Callable[[Any, Path], List[Tuple[str, List[str]]]]]] = {
    "codebase-health-candidate-choice.json": (shape, check_ch),
}
