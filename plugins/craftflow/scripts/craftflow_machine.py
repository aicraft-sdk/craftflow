#!/usr/bin/env python3
"""Workflow state machines as data: validate, next, simulate, replay, fixtures (P8 Stage A; see the ADR draft).

Each `config/machines/<name>.json` models one route variant:

    {"id", "route", "version", "initial",
     "counters": {name: {"cap": "policy:<config/policy.json key>" | "plan:phases", "on_cap": <gate state>}},
     "states": {name: {"type": "step" (default) | "choice" | "gate" | "final", "phases": [phase tokens],
                       "agent": str, "gate": <pending_gate value>, "effects": [str], "actions": [str],
                       "on": {EVENT: target | {"target": str, "inc": counter}}}}}

Stage A changes no behavior: the router does not call this. Stage B (craftflow_machine_shadow.py) only logs. Modes:

    validate [--machine NAME|PATH]   soundness: every state reachable, every non-final state has an exit, every loop
                                     passes a gate or a capped counter whose cap ends in a gate, every gate has a resume
                                     event, every final state is entered only from a state that finalizes memory, and
                                     every name it uses (state, gate, policy key) exists
    next --machine M --state S --event E [--counters JSON]   one transition, as data: next state, effects, counters
    simulate --machine M --events E1,E2,...                  walk events from the initial state, running nothing
    replay --machine M --events-file F.events.jsonl          check a workflow's agent_completed/agent_started rows
    fixtures [--dir DIR]                                     every replay fixture's expected next_action must be a
                                                             state or action of a machine for its workflow_type
    shadow-report [--log F] [--need N]                       Stage B exit check over the hook log's machine_shadow
                                                             rows: N clean workflows and at most
                                                             allowed_disagreements() disagreeing rows (always exit 0)
    backfill-check [--log F] [--workflows D] [--write]       replay pre-shadow logs through the machine; --write
                                                             appends the recovered rows tagged source=backfill

Prints one JSON line. Exit codes: 0 ok, 1 unsound machine or disagreements, 2 bad input. Python 3.9, stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MACHINES_DIR = PLUGIN_ROOT / "config" / "machines"
POLICY_PATH = PLUGIN_ROOT / "config" / "policy.json"
FIXTURES_DIR = PLUGIN_ROOT / "tests" / "fixtures"
TYPES = ("step", "choice", "gate", "final")
KNOWN_GATES = ("clarification", "scope_selection", "checkpoint_decision", "human_checkpoint", "loop_cap_check_failed",
               "persistence_repair", "policy_invalid", "candidate_choice", "needs_info", "build_transition")
FINALIZE = "finalize_memory"


class MachineError(Exception):
    """A malformed machine, an unknown state, or an event the state does not handle."""


def _edges(machine: dict) -> List[Tuple[str, str, str, Optional[str]]]:
    out = []
    for name, state in machine.get("states", {}).items():
        for event, target in (state.get("on") or {}).items():
            tgt, inc = (target.get("target"), target.get("inc")) if isinstance(target, dict) else (target, None)
            out.append((name, event, tgt, inc))
    return out


def _cap(machine: dict, counter: str, policy: dict) -> Optional[int]:
    source = machine.get("counters", {}).get(counter, {}).get("cap", "")
    if source.startswith("policy:"):
        value = policy.get(source[len("policy:"):])
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    return None  # plan:phases is known only at run time


def _sccs(nodes: List[str], succ: Dict[str, List[str]]) -> List[List[str]]:
    index, low, stack, on, out, counter = {}, {}, [], set(), [], [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in succ.get(v, []):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            out.append(comp)

    for v in nodes:
        if v not in index:
            visit(v)
    return out


def validate(machine: dict, policy: dict) -> List[str]:
    errors: List[str] = []
    states = machine.get("states")
    if not isinstance(states, dict) or not states:
        return ["machine has no states"]
    if machine.get("initial") not in states:
        return [f"initial state {machine.get('initial')!r} is not a state"]
    counters = machine.get("counters", {})
    for name, state in states.items():
        kind = state.get("type", "step")
        if kind not in TYPES:
            errors.append(f"{name}: unknown type {kind!r}")
        if kind == "gate" and state.get("gate") not in KNOWN_GATES:
            errors.append(f"{name}: unknown gate {state.get('gate')!r}")
        if kind != "final" and not state.get("on"):
            errors.append(f"{name}: non-final state has no exit" if kind != "gate" else f"{name}: gate has no resume event")
    edges = _edges(machine)
    for src, event, tgt, inc in edges:
        if tgt not in states:
            errors.append(f"{src} --{event}--> unknown state {tgt!r}")
        if inc is not None and inc not in counters:
            errors.append(f"{src} --{event}--> increments unknown counter {inc!r}")
    for cname, spec in counters.items():
        cap = spec.get("cap", "")
        if cap.startswith("policy:") and _cap(machine, cname, policy) is None:
            errors.append(f"counter {cname}: cap {cap!r} names no integer key in config/policy.json")
        elif not cap.startswith(("policy:", "plan:")):
            errors.append(f"counter {cname}: cap {cap!r} must be policy:<key> or plan:<field>")
        if states.get(spec.get("on_cap"), {}).get("type") != "gate":
            errors.append(f"counter {cname}: on_cap {spec.get('on_cap')!r} must be a gate state")
    if errors:
        return errors
    succ: Dict[str, List[str]] = {}
    for src, _event, tgt, _inc in edges:
        succ.setdefault(src, []).append(tgt)
    for cname, spec in counters.items():
        for src, _event, _tgt, inc in edges:
            if inc == cname:
                succ.setdefault(src, []).append(spec["on_cap"])
    seen, todo = set(), [machine["initial"]]
    while todo:
        v = todo.pop()
        if v not in seen:
            seen.add(v)
            todo.extend(succ.get(v, []))
    errors += [f"{name}: unreachable from {machine['initial']}" for name in states if name not in seen]
    # A loop is safe when it passes a gate (a person decides) or a capped counter. Drop gate states and capped edges:
    # any cycle left would spin with nobody deciding and nothing counting.
    free: Dict[str, List[str]] = {}
    for src, _event, tgt, inc in edges:
        if inc is None and states[src].get("type") != "gate" and states[tgt].get("type") != "gate":
            free.setdefault(src, []).append(tgt)
    for comp in _sccs(list(states), free):
        if len(comp) > 1 or comp[0] in free.get(comp[0], []):
            errors.append(f"uncapped loop through {sorted(comp)}: add a gate or a capped counter")
    for src, event, tgt, _inc in edges:
        if states[tgt].get("type") == "final" and FINALIZE not in states[src].get("effects", []):
            errors.append(f"{src} --{event}--> final {tgt} without memory-finalize ({FINALIZE})")
    return errors


def next_state(machine: dict, state: str, event: str, counters: Dict[str, int], policy: dict
               ) -> Tuple[str, List[str], Dict[str, int]]:
    states = machine.get("states", {})
    if state not in states:
        raise MachineError(f"unknown state {state!r}")
    target = (states[state].get("on") or {}).get(event)
    if target is None:
        raise MachineError(f"state {state!r} does not handle event {event!r}")
    tgt, inc = (target["target"], target.get("inc")) if isinstance(target, dict) else (target, None)
    counters = dict(counters)
    if inc:
        cap = _cap(machine, inc, policy)
        if cap is not None and counters.get(inc, 0) >= cap:
            tgt = machine["counters"][inc]["on_cap"]
        else:
            counters[inc] = counters.get(inc, 0) + 1
    return tgt, list(states[tgt].get("effects", [])), counters


def simulate(machine: dict, events: List[str], policy: dict) -> List[dict]:
    state, counters, trace = machine["initial"], {}, []
    for event in events:
        state, effects, counters = next_state(machine, state, event, counters, policy)
        trace.append({"event": event, "state": state, "effects": effects, "counters": counters})
    return trace


def _state_for_phase(machine: dict, phase: str) -> Optional[str]:
    for name, state in machine["states"].items():
        if phase in state.get("phases", []):
            return name
    return None


def replay(machine: dict, rows: List[dict], policy: dict) -> Dict[str, object]:
    disagreements, counters, expected, checked = [], {}, None, 0
    for i, row in enumerate(rows):
        if row.get("event") == "agent_started" and expected is not None:
            want = machine["states"][expected].get("phases", [])
            if want and row.get("phase") not in want:
                disagreements.append({"row": i, "expected_state": expected, "expected_phases": want, "got": row.get("phase")})
            checked += 1
            expected = None
        elif row.get("event") == "agent_completed":
            state = _state_for_phase(machine, str(row.get("phase")))
            if state is None:
                disagreements.append({"row": i, "error": f"phase {row.get('phase')!r} is in no state"})
                continue
            try:
                expected, _effects, counters = next_state(machine, state, str(row.get("decision")), counters, policy)
            except MachineError as exc:
                disagreements.append({"row": i, "error": str(exc)})
                expected = None
    return {"checked": checked, "disagreements": disagreements}


def check_fixtures(machines: Dict[str, dict], directory: Path) -> Dict[str, object]:
    by_route: Dict[str, set] = {}
    for machine in machines.values():
        names = by_route.setdefault(machine["route"], set())
        for name, state in machine["states"].items():
            names.add(name)
            names.update(state.get("actions", []))
    checked, no_machine, disagreements = 0, [], []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        action = data.get("expected", {}).get("next_action") if isinstance(data, dict) and isinstance(data.get("expected"), dict) else None
        if not action:
            continue
        route = data.get("workflow_type")
        if route not in by_route:
            no_machine.append(path.name)
            continue
        checked += 1
        if action not in by_route[route]:
            disagreements.append({"fixture": path.name, "route": route, "next_action": action})
    return {"checked": checked, "no_machine": no_machine, "disagreements": disagreements}


def load_all(directory: Path = MACHINES_DIR) -> Dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))}


def _load_machine(ref: str) -> dict:
    path = Path(ref) if ref.endswith(".json") else MACHINES_DIR / f"{ref}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate, step, simulate and replay workflow state machines.")
    parser.add_argument("mode", choices=["validate", "next", "simulate", "replay", "fixtures", "shadow-report",
                                         "backfill-check"])
    parser.add_argument("--machine")
    parser.add_argument("--state")
    parser.add_argument("--event")
    parser.add_argument("--events", default="")
    parser.add_argument("--events-file")
    parser.add_argument("--counters", default="{}")
    parser.add_argument("--dir", default=str(FIXTURES_DIR))
    parser.add_argument("--log", default=".craftflow/state/craftflow-hook-events.log")
    parser.add_argument("--need", type=int, default=20)
    parser.add_argument("--workflows", default=".craftflow/state/workflows")
    parser.add_argument("--write", action="store_true",
                        help="backfill-check: append the recovered rows to the log (tagged source=backfill)")
    args = parser.parse_args(argv)
    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        machines = {args.machine: _load_machine(args.machine)} if args.machine else load_all()
        if args.mode == "validate":
            report = {name: validate(mach, policy) for name, mach in machines.items()}
            print(json.dumps({"errors": report}, sort_keys=True))
            return 1 if any(report.values()) else 0
        if args.mode == "shadow-report":
            from craftflow_machine_shadow import report

            print(json.dumps(report(Path(args.log), args.need), sort_keys=True))
            return 0
        if args.mode == "backfill-check":
            from craftflow_machine_shadow import backfill_check

            print(json.dumps(backfill_check(Path(args.log), Path(args.workflows), load_all(), policy,
                                            write=args.write), sort_keys=True))
            return 0
        if args.mode == "fixtures":
            report = check_fixtures(load_all(), Path(args.dir))
            print(json.dumps(report, sort_keys=True))
            return 1 if report["disagreements"] else 0
        machine = next(iter(machines.values()))
        if args.mode == "next":
            state, effects, counters = next_state(machine, args.state, args.event, json.loads(args.counters), policy)
            print(json.dumps({"state": state, "effects": effects, "counters": counters}, sort_keys=True))
            return 0
        if args.mode == "simulate":
            print(json.dumps({"trace": simulate(machine, [e for e in args.events.split(",") if e], policy)}, sort_keys=True))
            return 0
        rows = [json.loads(line) for line in Path(args.events_file).read_text(encoding="utf-8").splitlines() if line.strip()]
        report = replay(machine, rows, policy)
        print(json.dumps(report, sort_keys=True))
        return 1 if report["disagreements"] else 0
    except (OSError, ValueError, TypeError, KeyError, MachineError, StopIteration) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
