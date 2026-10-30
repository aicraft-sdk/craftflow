#!/usr/bin/env python3
"""Controls for the H3 live driver (`tests/live/harvest_routes_roundtrip.py`) and the route replay checker.

Run: python3 tests/fixtures/test_harvest_live_driver.py   (non-zero exit on any FAIL; no network, no `claude`)
Each failure mode the LV-ORIENT judge and the replay checker must catch has a control that proves it can fail.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))


def load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


driver = load("harvest_driver", PLUGIN_ROOT / "tests" / "live" / "harvest_routes_roundtrip.py")
replay = load("route_replay", PLUGIN_ROOT / "scripts" / "craftflow_route_replay_check.py")

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


def stream(*blocks: Dict[str, Any], text: str = "-> ORIENT workflow: the facade delegates to core") -> str:
    events = [{"type": "assistant", "message": {"content": [{"type": "text", "text": text}, *blocks]}},
              {"type": "result", "result": "done"}]
    return "\n".join(json.dumps(e) for e in events)


def tool(name: str, **inp: Any) -> Dict[str, Any]:
    return {"type": "tool_use", "name": name, "input": inp}


def judge(out: str, before=(), after=(), git_before="", git_after="") -> str:
    return driver.judge_orient(0, out, list(before), list(after), git_before, git_after)[0]


def driver_controls() -> None:
    check("clean transcript passes", judge(stream(tool("Read", file_path="calc/facade.py"))) == "PASS", "")
    for name in ("TaskCreate", "TaskUpdate"):
        v = judge(stream(tool(name, subject="x")))
        check(f"{name} tool_use is forbidden", v.startswith("FAIL:forbidden_tool_use") and name in v, v)
    v = judge(stream(tool("Bash", command="python3 scripts/craftflow_workflow_id.py --init-artifact --workflow-type ORIENT")))
    check("Bash mint attempt (craftflow_workflow_id) fails", v.startswith("FAIL:mint_attempt") and "craftflow_workflow_id" in v, v)
    v = judge(stream(tool("Bash", command="echo {} > .craftflow/state/workflows/x.json")))
    check("Bash write under workflows/ fails", v.startswith("FAIL:mint_attempt"), v)
    check("harmless Bash passes", judge(stream(tool("Bash", command="ls calc"))) == "PASS", "")
    v = judge(stream(), before=["a.json"], after=["a.json", "wf-1.events.jsonl"])
    check("a new .events.jsonl fails", v.startswith("FAIL:artifact_minted") and "wf-1.events.jsonl" in v, v)
    v = judge(stream(), before=[], after=["wf-1/"])
    check("a new workflows/<dir>/ fails", v.startswith("FAIL:artifact_minted"), v)
    v = judge(stream(), git_before="", git_after=" M calc/core.py\n")
    check("a dirty scratch tree fails", v.startswith("FAIL:scratch_repo_changed"), v)
    check("unchanged non-empty git status passes", judge(stream(), git_before="?? x\n", git_after="?? x\n") == "PASS", "")

    with tempfile.TemporaryDirectory() as tmp:
        wf = Path(tmp) / ".craftflow" / "state" / "workflows"
        (wf / "d").mkdir(parents=True)
        (wf / "a.json").write_text("{}")
        (wf / "a.events.jsonl").write_text("")
        names = driver.workflow_files(tmp)
        check("workflow_files lists json, events.jsonl and dirs", names == ["a.events.jsonl", "a.json", "d/"], str(names))

    p = subprocess.run([sys.executable, str(PLUGIN_ROOT / "tests" / "live" / "harvest_routes_roundtrip.py"), "--help"],
                       capture_output=True, text=True)
    check("--help documents exit code 3", p.returncode == 0 and "3" in p.stdout and "class" in p.stdout.lower(), p.stdout[-200:])
    check("exit_for: class B with all PASS is non-zero",
          driver.exit_for({"LV-ORIENT": "PASS", "class": "B-SIMULATED"}, ["LV-ORIENT"]) == 3, "")
    check("exit_for: class A all PASS is 0", driver.exit_for({"load_probe": "PASS", "class": "A"}, ["load_probe"]) == 0, "")
    check("exit_for: a FAIL is 1", driver.exit_for({"load_probe": "FAIL:x", "class": "B-SIMULATED"}, ["load_probe"]) == 1, "")
    out = driver.decode_partial(b"partial")
    check("decode_partial keeps bytes output", out == "partial" and driver.decode_partial(None) == "", out)


def ch_state(**kw: Any) -> Dict[str, Any]:
    base = {"parents": ["wf-a"], "parent": "wf-a", "events": ["workflow_started", "memory_finalized", "handoff_started"],
            "report_exists": True, "children": ["wf-b"]}
    return {**base, **kw}


def ch_controls() -> None:
    """LV-CH (H6): each failure mode of the judge has a control; the run loop stops on `done` and on timeout."""
    text = "-> CODEBASE-HEALTH workflow (signals: codebase health)"
    verdict = lambda s=stream(text=text), st=None, calc="": driver.judge_ch(s, st or ch_state(), calc)[0]  # noqa: E731
    check("LV-CH registered, not pending", "LV-CH" in driver.REGISTERED and "LV-CH" not in driver.NOT_REGISTERED, str(driver.REGISTERED))
    check("LV-CH clean run passes", verdict() == "PASS", verdict())
    check("LV-CH without the announce fails", verdict(stream(text="hello")).startswith("FAIL:no_ch_announce"), verdict(stream(text="hello")))
    check("LV-CH with an empty stream fails", verdict("").startswith("FAIL:claude_session_failed"), verdict(""))
    check("LV-CH without a CODEBASE-HEALTH artifact fails", verdict(st=ch_state(parents=[], parent=None)).startswith("FAIL:no_ch_artifact"), "")
    check("LV-CH with two CODEBASE-HEALTH artifacts fails", verdict(st=ch_state(parents=["wf-a", "wf-c"])).startswith("FAIL:no_ch_artifact"), "")
    for missing in ("memory_finalized", "handoff_started"):
        st = ch_state(events=[e for e in ch_state()["events"] if e != missing])
        check(f"LV-CH without {missing} fails", verdict(st=st).startswith("FAIL:events_missing") and missing in verdict(st=st), verdict(st=st))
    check("LV-CH without the report file fails", verdict(st=ch_state(report_exists=False)).startswith("FAIL:no_report"), "")
    check("LV-CH without a PLAN child fails", verdict(st=ch_state(children=[])).startswith("FAIL:no_plan_child"), "")
    check("LV-CH with changed product code fails", verdict(calc=" M calc/core.py\n").startswith("FAIL:product_code_changed"), "")
    with tempfile.TemporaryDirectory() as tmp:
        wf = Path(tmp) / ".craftflow" / "state" / "workflows"
        wf.mkdir(parents=True)
        (wf / "wf-a.json").write_text(json.dumps({"workflow_type": "CODEBASE-HEALTH"}))
        (wf / "wf-a.events.jsonl").write_text(json.dumps({"event": "memory_finalized"}) + "\n" + json.dumps({"event": "handoff_started"}) + "\n")
        (wf / "wf-b.json").write_text(json.dumps({"workflow_type": "PLAN", "parent_workflow_uuid": "wf-a"}))
        (wf / "wf-z.json").write_text(json.dumps({"workflow_type": "PLAN", "parent_workflow_uuid": "wf-other"}))
        (Path(tmp) / ".craftflow" / "codebase-health" / "wf-a").mkdir(parents=True)
        (Path(tmp) / ".craftflow" / "codebase-health" / "wf-a" / "report.html").write_text("<svg></svg>")
        st = driver.ch_state(tmp)
        check("ch_state reads the parent, its events, the report and only its own PLAN child",
              st["parent"] == "wf-a" and st["events"] == ["memory_finalized", "handoff_started"] and st["report_exists"] and st["children"] == ["wf-b"], str(st))
        (Path(tmp) / ".craftflow" / "codebase-health" / "wf-a" / "report.html").write_text("")
        check("ch_state treats an empty report as missing", driver.ch_state(tmp)["report_exists"] is False, "")
        shim = driver.open_shim(Path(tmp))
        rc = subprocess.run([str(shim / "open"), "/r/report.html"]).returncode
        check("the open shim records its argument and exits 0 (no browser in the proof)",
              rc == 0 and (Path(tmp) / "open-calls.log").read_text().strip() == "/r/report.html", str(rc))
        rc, out, err, why = driver.run_until([sys.executable, "-c", "import time; print('x', flush=True); time.sleep(30)"], dict(), tmp, "", lambda: True, 20)
        check("run_until stops a running child once done() is true", why == "done" and "x" in out and rc != 0, f"{rc} {why} {out!r}")
        rc, out, err, why = driver.run_until([sys.executable, "-c", "import time; time.sleep(30)"], dict(), tmp, "", lambda: False, 1)
        check("run_until stops a child at the timeout", why == "timeout", why)
        rc, out, err, why = driver.run_until([sys.executable, "-c", "import sys; print(sys.stdin.read())"], dict(), tmp, "hi", lambda: False, 20)
        check("run_until passes stdin and reports a normal exit", (rc, why, out.strip()) == (0, "exited", "hi"), f"{rc} {why} {out!r}")


def replay_controls() -> None:
    good = json.loads(replay.FIXTURE.read_text(encoding="utf-8"))
    check("real fixture shape is clean", replay.check_fixture(good) == [], str(replay.check_fixture(good)))
    bad = json.loads(json.dumps(good))
    bad["expected"]["memory_update"] = True
    check("fixture memory_update: true is caught (shape + outcome)",
          replay.check_fixture(bad) != [] or replay.check_outcome(bad) != [], "")
    bad = json.loads(json.dumps(good))
    del bad["expected"]["agents_dispatched"]
    probs = replay.check_fixture(bad)
    check("missing agents_dispatched reports the shape problem", any("agents_dispatched" in p for p in probs), str(probs))
    for key in ("expected", "memory_update", "next_action", "task_creates", "artifact_created"):
        broken = json.loads(json.dumps(good))
        if key == "expected":
            del broken["expected"]
        else:
            del broken["expected"][key]
        check(f"missing {key} is a shape problem", any(key in p for p in replay.check_fixture(broken)), key)
    check("non-dict expected is a shape problem", replay.check_fixture({"expected": []}) != [], "")
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "craftflow_route_replay_check.py"),
                            "--fixture", str(Path(tmp) / "nope.json")], capture_output=True, text=True)
        check("missing fixture file exits 1 without a traceback",
              p.returncode == 1 and "Traceback" not in p.stderr + p.stdout and "fixture" in (p.stdout + p.stderr).lower(),
              f"rc={p.returncode} {p.stdout[-150:]}{p.stderr[-150:]}")
        junk = Path(tmp) / "junk.json"
        junk.write_text("{not json")
        p = subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "craftflow_route_replay_check.py"),
                            "--fixture", str(junk)], capture_output=True, text=True)
        check("malformed fixture file exits 1 without a traceback",
              p.returncode == 1 and "Traceback" not in p.stderr + p.stdout, f"rc={p.returncode}")


HOLLOW_ROUTE = (
    "### ORIENT phases\n\n"
    "no `TaskCreate`, no Memory Update; re-routes from scratch. "
    "Then hand the request to the builder and apply the fix.\n"
)


def run_replay_on_copy(route_text: str = "") -> subprocess.CompletedProcess:
    """Run the replay checker against a copied plugin tree whose route-orient.md is optionally replaced."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "craftflow"
        for sub in ("skills", "config", "scripts"):
            shutil.copytree(PLUGIN_ROOT / sub, root / sub, ignore=shutil.ignore_patterns("__pycache__"))
        (root / "tests" / "fixtures").mkdir(parents=True)
        for name in replay.registry():  # every registered fixture: a bare run replays them all
            shutil.copy(replay.FIXTURE.parent / name, root / "tests" / "fixtures" / name)
        if route_text:
            (root / "skills" / "_shared" / "route-orient.md").write_text(route_text, encoding="utf-8")
        return subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "craftflow_route_replay_check.py"),
                               "--plugin-root", str(root)], capture_output=True, text=True)


def hollow_controls() -> None:
    p = run_replay_on_copy()
    check("an untouched plugin copy passes the replay", p.returncode == 0 and "0 failed" in p.stdout,
          f"rc={p.returncode} {p.stdout[-300:]}{p.stderr[-200:]}")
    p = run_replay_on_copy(HOLLOW_ROUTE)
    check("a hollow route-orient.md fails the replay (doc pins)",
          p.returncode == 1 and "FAIL: doc pins" in p.stdout and "### ORIENT preparation" in p.stdout,
          f"rc={p.returncode} {p.stdout[-300:]}{p.stderr[-200:]}")
    check("a hollow route-orient.md fails the dispatch scan (plain-English hand-off)",
          "FAIL: route file dispatches nothing" in p.stdout, p.stdout[-300:])
    for text in ("Then hand the request to the builder.", "Next apply the fix.", "Then dispatch the agent.",
                 "Call TaskCreate(subject) now.", "Run Task(x) for it.",
                 "Do not hesitate, call TaskCreate(x).", "If not obvious, use Task(x) to delegate."):
        check(f"bare instruction is flagged: {text}", replay.dispatch_problems(text) != [], text)
    for text in ("It never falls into BUILD and does not dispatch anything.", "No `TaskCreate` is ever issued.",
                 "Do not hand the request to the builder.", "ORIENT cannot apply the fix."):
        check(f"negated mention is allowed: {text}", replay.dispatch_problems(text) == [], str(replay.dispatch_problems(text)))
    check("the real route file has no dispatch problems",
          replay.dispatch_problems(replay.read(replay.ROUTE_FILE)) == [], "")


def main() -> int:
    driver_controls()
    ch_controls()
    replay_controls()
    hollow_controls()
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
