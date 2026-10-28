#!/usr/bin/env python3
"""Controls for the H3 live driver (`tests/live/harvest_routes_roundtrip.py`) and the route replay checker.

Run: python3 tests/fixtures/test_harvest_live_driver.py   (non-zero exit on any FAIL; no network, no `claude`)
Each failure mode the LV-ORIENT judge and the replay checker must catch has a control that proves it can fail.
"""
from __future__ import annotations

import importlib.util
import json
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


def main() -> int:
    driver_controls()
    replay_controls()
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
