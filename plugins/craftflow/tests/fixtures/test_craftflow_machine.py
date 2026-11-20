#!/usr/bin/env python3
"""Tests for the workflow state machines (P8 Stage A: model and checks, no behavior change).

config/machines/*.json model each route's states (phase tokens), events (agent verdicts and user replies), gates and
loop caps. scripts/craftflow_machine.py proves each graph is sound (validate), steps through events without running
anything (simulate, next), checks a workflow's .events.jsonl against its machine (replay), and checks that every
replay fixture's expected next action is something its route's machine can do (fixtures).

Run from anywhere: python3 plugins/craftflow/tests/fixtures/test_craftflow_machine.py
"""
import copy
import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
REPO = PLUGIN.parents[1]
SCRIPT = PLUGIN / "scripts" / "craftflow_machine.py"
sys.path.insert(0, str(PLUGIN / "scripts"))
m = importlib.import_module("craftflow_machine")

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


POLICY = {"remediation_cycles_max": 3}
TINY = {
    "id": "tiny", "route": "BUILD", "version": 1, "initial": "work",
    "counters": {"fix": {"cap": "policy:remediation_cycles_max", "on_cap": "breaker"}},
    "states": {
        "work": {"phases": ["build-implement"], "on": {"PASS": "check", "BLOCKED": "blocked"}},
        "check": {"phases": ["build-verify"], "on": {"PASS": "memory-finalize", "FAIL": {"target": "work", "inc": "fix"}}},
        "blocked": {"type": "gate", "gate": "human_checkpoint", "on": {"USER_REPLY": "work", "ABORT": "memory-finalize"}},
        "breaker": {"type": "gate", "gate": "human_checkpoint", "on": {"USER_REPLY": "memory-finalize"}},
        "memory-finalize": {"phases": ["memory-finalize"], "effects": ["finalize_memory"], "on": {"DONE": "done"}},
        "done": {"type": "final"},
    },
}


def broken(edit):
    machine = copy.deepcopy(TINY)
    edit(machine)
    return m.validate(machine, POLICY)


print("[validate: a sound machine]")
check("the tiny machine is sound", m.validate(TINY, POLICY) == [], m.validate(TINY, POLICY))

print("[validate: each soundness rule fails closed]")
errs = broken(lambda x: x["states"].update({"orphan": {"on": {"PASS": "done"}}}))
check("an unreachable state is an error", any("unreachable" in e and "orphan" in e for e in errs), errs)
errs = broken(lambda x: x["states"]["check"].update({"on": {}}))
check("a non-final state with no exit is an error", any("no exit" in e and "check" in e for e in errs), errs)
errs = broken(lambda x: x["states"]["check"]["on"].update({"FAIL": "work"}))
check("a loop with no capped counter and no gate is an error", any("uncapped loop" in e for e in errs), errs)
errs = broken(lambda x: x["counters"]["fix"].update({"on_cap": "work"}))
check("a counter whose cap does not end in a gate is an error", any("on_cap" in e for e in errs), errs)
errs = broken(lambda x: x["counters"]["fix"].update({"cap": "policy:no_such_key"}))
check("a cap that names a missing policy key is an error", any("no_such_key" in e for e in errs), errs)
errs = broken(lambda x: x["states"]["blocked"].update({"on": {}}))
check("a gate with no resume event is an error", any("resume" in e and "blocked" in e for e in errs), errs)
errs = broken(lambda x: x["states"]["check"]["on"].update({"PASS": "done"}))
check("a final state reached without memory-finalize is an error", any("finalize" in e for e in errs), errs)
errs = broken(lambda x: x["states"]["work"]["on"].update({"PASS": "nowhere"}))
check("a transition to an unknown state is an error", any("nowhere" in e for e in errs), errs)
errs = broken(lambda x: x.update({"initial": "nope"}))
check("an unknown initial state is an error", any("initial" in e for e in errs), errs)
errs = broken(lambda x: x["states"]["blocked"].update({"gate": "made_up_gate"}))
check("an unknown gate name is an error", any("made_up_gate" in e for e in errs), errs)

print("[next and simulate]")
state, effects, counters = m.next_state(TINY, "check", "FAIL", {"fix": 0}, POLICY)
check("next follows a transition and bumps its counter", state == "work" and counters == {"fix": 1}, (state, counters))
state, effects, counters = m.next_state(TINY, "check", "FAIL", {"fix": 3}, POLICY)
check("at the cap, next goes to the counter's gate", state == "breaker", state)
state, effects, _ = m.next_state(TINY, "check", "PASS", {}, POLICY)
check("entering a state reports its effects", state == "memory-finalize" and effects == ["finalize_memory"], (state, effects))
try:
    m.next_state(TINY, "work", "NOT_AN_EVENT", {}, POLICY)
    check("an event the state does not handle is refused", False, "no error")
except m.MachineError:
    check("an event the state does not handle is refused", True)
trace = m.simulate(TINY, ["PASS", "FAIL", "PASS", "PASS", "DONE"], POLICY)
check("simulate walks the events to a final state", [t["state"] for t in trace] == ["check", "work", "check", "memory-finalize", "done"], trace)

print("[replay an event log]")
good = [
    {"event": "agent_completed", "phase": "build-implement", "decision": "PASS"},
    {"event": "agent_started", "phase": "build-verify"},
    {"event": "agent_completed", "phase": "build-verify", "decision": "FAIL"},
    {"event": "agent_started", "phase": "build-implement"},
]
check("a log that follows the machine has no disagreements", m.replay(TINY, good, POLICY)["disagreements"] == [], m.replay(TINY, good, POLICY))
bad = good[:2] + [{"event": "agent_completed", "phase": "build-verify", "decision": "PASS"},
                  {"event": "agent_started", "phase": "build-implement"}]
r = m.replay(TINY, bad, POLICY)
check("a log that skips the machine's path is a disagreement", len(r["disagreements"]) == 1, r)

print("[the shipped machines]")
policy = json.loads((PLUGIN / "config/policy.json").read_text(encoding="utf-8"))
machines = m.load_all(PLUGIN / "config/machines")
routes = {mach["route"] for mach in machines.values()}
check("machines cover BUILD, PLAN, DEBUG and REVIEW", {"BUILD", "PLAN", "DEBUG", "REVIEW"} <= routes, routes)
check("BUILD has a fast and a standard machine", {"build-fast", "build-standard"} <= set(machines), sorted(machines))
for name, mach in sorted(machines.items()):
    errs = m.validate(mach, policy)
    check(f"{name} is sound", errs == [], errs)
r = m.check_fixtures(machines, PLUGIN / "tests/fixtures")
check("every replay fixture's next action is one its route's machine can take", r["disagreements"] == [], r["disagreements"])
check("fixtures were actually checked", r["checked"] >= 25, r)

print("[cli]")
proc = subprocess.run([sys.executable, str(SCRIPT), "validate"], capture_output=True, text=True)
check("cli validate exits 0 on the shipped machines", proc.returncode == 0, proc.stdout + proc.stderr)
tmp = Path(tempfile.mkdtemp(prefix="machine-"))
bad_machine = copy.deepcopy(TINY)
bad_machine["states"]["check"]["on"]["FAIL"] = "work"
(tmp / "bad.json").write_text(json.dumps(bad_machine), encoding="utf-8")
proc = subprocess.run([sys.executable, str(SCRIPT), "validate", "--machine", str(tmp / "bad.json")], capture_output=True, text=True)
check("cli validate exits 1 on an unsound machine", proc.returncode == 1, proc.stdout)
proc = subprocess.run([sys.executable, str(SCRIPT), "next", "--machine", "build-standard", "--state", "build-implement", "--event", "PASS"],
                      capture_output=True, text=True)
check("cli next prints the next state and effects", proc.returncode == 0 and "state" in json.loads(proc.stdout), proc.stdout + proc.stderr)
log = tmp / "wf.events.jsonl"
log.write_text("\n".join(json.dumps(row) for row in good) + "\n", encoding="utf-8")
(tmp / "tiny.json").write_text(json.dumps(TINY), encoding="utf-8")
proc = subprocess.run([sys.executable, str(SCRIPT), "replay", "--machine", str(tmp / "tiny.json"), "--events-file", str(log)],
                      capture_output=True, text=True)
check("cli replay exits 0 on an agreeing log", proc.returncode == 0, proc.stdout + proc.stderr)
proc = subprocess.run([sys.executable, str(SCRIPT), "fixtures"], capture_output=True, text=True)
check("cli fixtures exits 0", proc.returncode == 0, proc.stdout[-400:] + proc.stderr)

print("[drafts]")
for rel in ("docs/proposals/ADR-draft-workflow-state-machine.md", "docs/proposals/SPEC-draft-workflow-state-machine.md"):
    text = (REPO / rel).read_text(encoding="utf-8") if (REPO / rel).is_file() else ""
    check(f"{rel} exists", bool(text))
    check(f"{rel} says Stage A changes no behavior", "no behavior change" in text.lower())

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
