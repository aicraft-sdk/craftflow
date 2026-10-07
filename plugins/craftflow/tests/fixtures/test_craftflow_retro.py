#!/usr/bin/env python3
"""Tests for craftflow_retro.py.

Run: python3 tests/fixtures/test_craftflow_retro.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

SCRIPT = SCRIPTS / "craftflow_retro.py"
FIXTURES = Path(__file__).resolve().parent

CLEAN = "wf-retro-clean-20261006-000000-aaaa0001"
REMFIX = "wf-retro-remfix-20261006-000000-aaaa0002"

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def check(name: str, cond: bool, reason: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, reason)


def run_cli(args: list, state_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--state-dir", str(state_dir)],
        capture_output=True,
        text=True,
    )


def materialize(tmp: Path, names: list) -> Path:
    wf = tmp / ".craftflow" / "state" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    for n in names:
        for suffix in (".json", ".events.jsonl"):
            shutil.copy(FIXTURES / "retro" / (n + suffix), wf / (n + suffix))
    return tmp / ".craftflow" / "state"


def write_wf(state: Path, name: str, artifact, events_text) -> None:
    wf = state / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    if artifact is not None:
        text = artifact if isinstance(artifact, str) else json.dumps(artifact)
        (wf / (name + ".json")).write_text(text)
    if events_text is not None:
        (wf / (name + ".events.jsonl")).write_text(events_text)


def base_artifact(name: str) -> dict:
    art = json.loads((FIXTURES / "retro" / (CLEAN + ".json")).read_text())
    art["workflow_uuid"] = name
    art["workflow_id"] = name
    return art


def expect_error(name: str, proc: subprocess.CompletedProcess, code: str, extra: str = "") -> None:
    err = proc.stderr.strip()
    good = (
        proc.returncode == 1
        and proc.stdout == ""
        and err.startswith(f"ERROR: {code}:")
        and "\n" not in err
        and extra in err
    )
    check(name, good, f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}")


def test_clean_no_friction() -> None:
    print("\n[clean]")
    with tempfile.TemporaryDirectory() as tmp:
        state = materialize(Path(tmp), [CLEAN])
        p = run_cli(["--wf", CLEAN], state)
        out = json.loads(p.stdout) if p.returncode == 0 else {}
        check("clean exits 0", p.returncode == 0, p.stderr)
        check("clean schema_version 1", out.get("schema_version") == 1)
        check("clean no friction", out.get("friction_found") is False and out.get("signals") == [], repr(out))
        check("clean key order", list(out) == ["schema_version", "workflow", "selection", "inputs",
                                               "friction_found", "signals", "data_gaps"], repr(list(out)))
        check("clean selection explicit", out.get("selection") == {"mode": "explicit"})
        check("clean inputs", out.get("inputs") == {
            "artifact": f"{state}/workflows/{CLEAN}.json",
            "events": f"{state}/workflows/{CLEAN}.events.jsonl",
            "events_lines": 3}, repr(out.get("inputs")))
        check("clean workflow block", out.get("workflow", {}).get("workflow_uuid") == CLEAN
              and out["workflow"].get("workflow_type") == "BUILD")


def test_remfix_heavy_signal() -> None:
    print("\n[remfix]")
    with tempfile.TemporaryDirectory() as tmp:
        state = materialize(Path(tmp), [REMFIX])
        p = run_cli(["--wf", REMFIX], state)
        out = json.loads(p.stdout) if p.returncode == 0 else {}
        sigs = out.get("signals") or [{}]
        s = sigs[0]
        check("remfix friction", out.get("friction_found") is True, p.stderr)
        check("remfix id/count/weight", (s.get("id"), s.get("count"), s.get("weight")) == ("remfix_cycles", 3, 9), repr(s))
        check("remfix by_phase", s.get("details", {}).get("by_phase") == {"phase_2": 3}, repr(s.get("details")))
        ev = s.get("evidence", [])
        check("remfix evidence total", s.get("evidence_total") == 6 and len(ev) == 6, repr(s.get("evidence_total")))
        check("remfix artifact evidence first",
              [e.get("key") for e in ev[:3]] == [f"remediation_history[{i}]" for i in range(3)], repr(ev[:3]))
        check("remfix event evidence by line",
              [(e.get("source"), e.get("line"), e.get("event")) for e in ev[3:]]
              == [("events", 2, "remediation_created"), ("events", 4, "remediation_created"),
                  ("events", 5, "remfix_created")], repr(ev[3:]))
        check("remfix excerpt format", ev[0].get("excerpt", "").startswith("phase_2 cycle=1 scope=builder origin="),
              repr(ev[0]))


def test_missing_artifact() -> None:
    print("\n[errors]")
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / ".craftflow" / "state"
        expect_error("missing artifact", run_cli(["--wf", "wf-nope"], state), "artifact_not_found")


def test_missing_events() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_wf(state, "wf-a", base_artifact("wf-a"), None)
        expect_error("missing events", run_cli(["--wf", "wf-a"], state), "events_not_found")


def test_artifact_unparseable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_wf(state, "wf-a", "{not json", "")
        expect_error("artifact unparseable", run_cli(["--wf", "wf-a"], state), "artifact_unparseable")


def test_artifact_not_object() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_wf(state, "wf-a", "[]", "")
        expect_error("artifact not object", run_cli(["--wf", "wf-a"], state), "artifact_shape")


def test_artifact_no_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        a = base_artifact("wf-a")
        del a["workflow_uuid"], a["workflow_id"]
        write_wf(state, "wf-a", a, "")
        expect_error("artifact no id", run_cli(["--wf", "wf-a"], state), "artifact_shape")


def test_history_not_list() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        a = base_artifact("wf-a")
        a["remediation_history"] = {}
        write_wf(state, "wf-a", a, "")
        expect_error("history not list", run_cli(["--wf", "wf-a"], state), "artifact_shape")
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        a = base_artifact("wf-a")
        a["telemetry"] = []
        write_wf(state, "wf-a", a, "")
        expect_error("telemetry not object", run_cli(["--wf", "wf-a"], state), "artifact_shape")


def test_events_unparseable_line() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_wf(state, "wf-a", base_artifact("wf-a"), '{"event":"x"}\n{bad\n')
        expect_error("events unparseable", run_cli(["--wf", "wf-a"], state), "events_unparseable", "line 2")


def test_events_non_object_line() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_wf(state, "wf-a", base_artifact("wf-a"), "[1]\n")
        expect_error("events non-object", run_cli(["--wf", "wf-a"], state), "events_shape", "line 1")


def test_events_empty_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_wf(state, "wf-a", base_artifact("wf-a"), "")
        p = run_cli(["--wf", "wf-a"], state)
        out = json.loads(p.stdout) if p.returncode == 0 else {}
        check("events empty file", p.returncode == 0 and out.get("inputs", {}).get("events_lines") == 0
              and "events log is empty" in out.get("data_gaps", []), p.stderr + p.stdout)


def test_events_blank_lines_ignored() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_wf(state, "wf-a", base_artifact("wf-a"), '\n{"event":"workflow_started"}\n\n   \n')
        p = run_cli(["--wf", "wf-a"], state)
        out = json.loads(p.stdout) if p.returncode == 0 else {}
        check("events blank lines ignored", p.returncode == 0 and out.get("inputs", {}).get("events_lines") == 1
              and out.get("data_gaps") == [], p.stderr + p.stdout)


def test_non_dict_history_entry_skipped() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        a = base_artifact("wf-a")
        a["remediation_history"] = [{"phase": "p1", "cycle": 1}, "oops"]
        write_wf(state, "wf-a", a, "")
        p = run_cli(["--wf", "wf-a"], state)
        out = json.loads(p.stdout) if p.returncode == 0 else {}
        check("non-dict history skipped",
              "remediation_history[1] is str, skipped" in out.get("data_gaps", [])
              and out.get("signals", [{}])[0].get("count") == 1, p.stderr + p.stdout)


def test_invalid_wf_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        for bad in ("../x", "wf-a/b", "nope", ""):
            expect_error(f"invalid wf id {bad!r}", run_cli(["--wf", bad], state), "invalid_wf_id")


def test_missing_selector_usage_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = run_cli([], Path(tmp))
        check("missing selector exits 2", p.returncode == 2 and p.stdout == "", f"rc={p.returncode}")


def test_extractors_match_catalog() -> None:
    import craftflow_retro as cr
    pairs = cr.EXTRACTORS
    check("EXTRACTORS tuple of (id, fn) pairs",
          isinstance(pairs, tuple) and all(isinstance(p, tuple) and len(p) == 2 and callable(p[1]) for p in pairs)
          and [p[0] for p in pairs] == ["remfix_cycles"], repr(pairs))


def test_output_deterministic() -> None:
    print("\n[properties]")
    with tempfile.TemporaryDirectory() as tmp:
        state = materialize(Path(tmp), [REMFIX])
        a = run_cli(["--wf", REMFIX], state)
        b = run_cli(["--wf", REMFIX], state)
        check("output deterministic", a.returncode == 0 and a.stdout == b.stdout and a.stdout != "")


def _snapshot(root: Path) -> list:
    snap = []
    for dirpath, dirnames, filenames in os.walk(root):
        for n in sorted(dirnames + filenames):
            p = Path(dirpath) / n
            st = p.stat()
            snap.append((str(p.relative_to(root)), st.st_size, st.st_mtime_ns))
    return sorted(snap)


def test_read_only_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = materialize(Path(tmp), [CLEAN, REMFIX])
        before = _snapshot(Path(tmp))
        run_cli(["--wf", REMFIX], state)
        run_cli(["--wf", CLEAN], state)
        run_cli(["--wf", "wf-missing"], state)
        after = _snapshot(Path(tmp))
        check("read-only snapshot unchanged", before == after and len(before) > 0)


def test_import_cheap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SCRIPTS)
        t0 = time.monotonic()
        proc = subprocess.run([sys.executable, "-c", "import craftflow_retro"], cwd=tmp,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=10)
        elapsed = time.monotonic() - t0
        listing = os.listdir(tmp)
        check("import cheap", proc.returncode == 0 and proc.stdout == "" and elapsed < 0.5 and listing == [],
              f"rc={proc.returncode} out={proc.stdout!r} err={proc.stderr!r} t={elapsed:.2f} ls={listing}")


def main() -> int:
    tests = [
        test_clean_no_friction, test_remfix_heavy_signal, test_missing_artifact, test_missing_events,
        test_artifact_unparseable, test_artifact_not_object, test_artifact_no_id, test_history_not_list,
        test_events_unparseable_line, test_events_non_object_line, test_events_empty_file,
        test_events_blank_lines_ignored, test_non_dict_history_entry_skipped, test_invalid_wf_id,
        test_missing_selector_usage_error, test_extractors_match_catalog, test_output_deterministic,
        test_read_only_snapshot, test_import_cheap,
    ]
    for t in tests:
        try:
            t()
        except Exception as exc:  # a crashing test is a failing test, never a silent pass
            fail(t.__name__, f"{type(exc).__name__}: {exc}")
    print(f"\nResults: {_passes} passed, {len(_errors)} failed")
    for e in _errors:
        print(e)
    print("PASS" if not _errors else "FAIL")
    return 0 if not _errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
