#!/usr/bin/env python3
"""P8 Stage B: the workflow machine in shadow mode (ADR-0080, SPEC-0037).

The SubagentStop audit hook calls `hook_observe()` after each craftflow agent returns. It works out which machine
models the workflow, which agents that machine expected next, and whether the agent that just returned is one of them,
then logs one `machine_shadow` row. Routing never reads these rows: the router keeps deciding from its own prose.
`craftflow_machine.py shadow-report` turns the rows into the Stage B exit check (an agreed number of workflows with zero
disagreements).

Shadow state lives in the rows themselves (`shadow_state`), so no new state file is written: the next call reads the
last row of the same workflow back from the hook event log.

Unmodeled input (a route with no machine, a verdict no state handles, an agent no state names) is logged with a note
that starts with "unmodeled" and never raises. Python 3.9, stdlib only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ALIASES = {"APPROVE": "CLEAN", "CHANGES_REQUESTED": "FINDINGS", "ISSUES_FOUND": "FINDINGS", "FAIL": "FINDINGS",
           "REFUTED": "FINDINGS"}
# Agentless states whose phases still dispatch agents (recheck re-runs review, hunt and verify as needed).
PHASE_AGENTS = {"re-review": "code-reviewer", "re-hunt": "silent-failure-hunter", "re-verify": "integration-verifier"}
LOG_TAIL_BYTES = 2_000_000
ROUTES = {"PLAN": "plan", "DEBUG": "debug", "REVIEW": "review"}


def verdict_of(message: str) -> Optional[str]:
    """The verdict an agent returned: the CONTRACT envelope's "s", else a YAML `STATUS:` line, else None."""
    match = re.search(r"CONTRACT\s*(\{[^\n]*\})", message or "")
    if match:
        try:
            value = json.loads(match.group(1)).get("s")
            if isinstance(value, str) and value:
                return value
        except (ValueError, AttributeError):
            pass
    match = re.search(r"^\s*STATUS:\s*([A-Z_]+)", message or "", re.MULTILINE)
    return match.group(1) if match else None


def machine_for(workflow_type: Optional[str], build_mode: Optional[str]) -> Optional[str]:
    if workflow_type == "BUILD":
        return "build-fast" if build_mode == "fast_path" else "build-standard"
    return ROUTES.get(str(workflow_type))


def _agents(state: dict) -> List[str]:
    if state.get("agent"):
        return state["agent"].split("+")
    return [PHASE_AGENTS[p] for p in state.get("phases", []) if p in PHASE_AGENTS]


def _targets(state: dict) -> List[str]:
    out = []
    for target in (state.get("on") or {}).values():
        out.append(target["target"] if isinstance(target, dict) else target)
    return out


def _expected(machine: dict, at: str, waiting: List[str]) -> Dict[str, str]:
    """Agent name -> the state that would run it, for every agent the machine allows next from `at`."""
    states = machine["states"]
    if waiting:
        return {agent: at for agent in waiting}
    out: Dict[str, str] = {}
    seen, frontier = set(), [at]
    while frontier:
        name = frontier.pop(0)
        if name in seen or name not in states:
            continue
        seen.add(name)
        state = states[name]
        for agent in _agents(state):
            out.setdefault(agent, name)
        if not state.get("agent"):  # agentless: the next agent may come from any successor
            frontier.extend(_targets(state))
    return out


def _event(state: dict, verdict: Optional[str]) -> Optional[str]:
    handled = state.get("on") or {}
    if verdict in handled:
        return verdict
    alias = ALIASES.get(str(verdict))
    return alias if alias in handled else None


def _locate(machine: dict, agent: str) -> Optional[str]:
    states = machine["states"]
    if agent in _agents(states[machine["initial"]]):
        return machine["initial"]
    return next((name for name, state in states.items() if agent in _agents(state)), None)


def _row(machine_id, state, nxt, agree, expected, note, agent, verdict) -> dict:
    return {"machine": machine_id, "state": state, "next": nxt, "agree": agree, "expected": sorted(expected),
            "note": note, "agent": agent, "verdict": verdict}


def _step(machine: dict, policy: dict, shadow: dict, name: str, agent: str, verdict: Optional[str]
          ) -> Tuple[Optional[str], Optional[str], dict]:
    """Apply one agent result at state `name`. Returns (next state or None while waiting, note, new shadow state)."""
    from craftflow_machine import MachineError, next_state  # same directory; imported lazily for the hook's start-up

    state = machine["states"][name]
    event = _event(state, verdict)
    if event is None:
        return None, f"unmodeled verdict {verdict!r} at {name}", {}
    parallel = state.get("agent", "").count("+") > 0
    if parallel:
        pending = shadow.get("waiting") if shadow.get("at") == name and shadow.get("waiting") else _agents(state)
        pending = [a for a in pending if a != agent]
        results = (shadow.get("results") if shadow.get("at") == name else None) or []
        results = results + [event]
        if pending:
            return None, None, {"at": name, "waiting": pending, "results": results, "counters": shadow.get("counters", {})}
        event = "FINDINGS" if "FINDINGS" in results else event
    elif not state.get("agent") and event not in ("FINDINGS",):
        # a recheck agent came back clean: the remaining rechecks, or the state's successors, come next
        return None, None, {"at": name, "waiting": [], "counters": shadow.get("counters", {})}
    try:
        nxt, _effects, counters = next_state(machine, name, event, shadow.get("counters", {}), policy)
    except MachineError as exc:
        return None, f"unmodeled: {exc}", {}
    return nxt, None, {"at": nxt, "waiting": [], "counters": counters}


def observe(machines: Dict[str, dict], policy: dict, shadow: dict, workflow_type: Optional[str],
            build_mode: Optional[str], agent: str, verdict: Optional[str]) -> Tuple[dict, dict]:
    """One returned agent -> (machine_shadow row, new shadow state). Never raises on unmodeled input."""
    agent = agent.split(":")[-1]
    machine_id = machine_for(workflow_type, build_mode)
    machine = machines.get(machine_id) if machine_id else None
    if machine is None:
        return _row(None, None, None, None, [], f"unmodeled route {workflow_type!r}", agent, verdict), {}
    at = shadow.get("at")
    if at not in machine["states"]:
        name = _locate(machine, agent)
        if name is None:
            return _row(machine_id, None, None, None, [], f"unmodeled agent {agent!r}", agent, verdict), {}
        nxt, note, new = _step(machine, policy, {}, name, agent, verdict)
        return _row(machine_id, name, nxt, None, [], note, agent, verdict), new
    expected = _expected(machine, at, shadow.get("waiting") or [])
    if agent in expected:
        name, agree = expected[agent], True
        carry = shadow if name == at else {"counters": shadow.get("counters", {})}
    else:
        name, agree = _locate(machine, agent), False
        if name is None:  # an agent the machine never names (a research helper, say): not a disagreement
            return _row(machine_id, at, None, None, expected, f"unmodeled agent {agent!r}", agent, verdict), shadow
        carry = {"counters": shadow.get("counters", {})}
    nxt, note, new = _step(machine, policy, carry, name, agent, verdict)
    return _row(machine_id, name, nxt, agree, expected, note, agent, verdict), new


def report_rows(rows: List[dict], need: int) -> dict:
    agreed: Dict[str, bool] = {}
    bad: Dict[str, int] = {}
    for row in rows:
        if row.get("event") != "machine_shadow" or not row.get("workflow"):
            continue
        wf = str(row["workflow"])
        if row.get("agree") is False:
            bad[wf] = bad.get(wf, 0) + 1
        elif row.get("agree") is True:
            agreed[wf] = True
    clean = sorted(wf for wf in agreed if wf not in bad)
    disagreements = sum(bad.values())
    return {"clean_workflows": len(clean), "need": need, "disagreements": disagreements,
            "disagreeing_workflows": sorted(bad), "exit_criterion_met": len(clean) >= need and disagreements == 0}


def _read_rows(log: Path, tail: Optional[int] = None) -> List[dict]:
    try:
        with log.open("rb") as fh:
            if tail:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - tail))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("event") == "machine_shadow":
            rows.append(row)
    return rows


def report(log: Path, need: int) -> dict:
    return report_rows(_read_rows(log), need)


def hook_observe(log: Path, workflow: dict, agent: str, message: str, machines: Dict[str, dict], policy: dict) -> dict:
    """Build the machine_shadow row for one returned agent of `workflow` (the latest workflow artifact)."""
    uuid = workflow.get("workflow_uuid")
    shadow: dict = {}
    for row in reversed(_read_rows(log, LOG_TAIL_BYTES)):
        if row.get("workflow") == uuid:
            shadow = row.get("shadow_state") or {}
            break
    row, new = observe(machines, policy, shadow, workflow.get("workflow_type"), workflow.get("build_mode"), agent,
                       verdict_of(message))
    return {"workflow": uuid, **row, "shadow_state": new}
