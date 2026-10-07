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


NEAR = "wf-retro-near-20261006-000000-aaaa0005"


def _inline(state: Path, name: str, updated_at) -> None:
    art = base_artifact(name)
    if updated_at is None:
        del art["updated_at"]
    else:
        art["updated_at"] = updated_at
    write_wf(state, name, art, '{"event": "workflow_started"}\n')


def test_latest_picks_newest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = materialize(Path(tmp), [CLEAN, REMFIX])
        p = run_cli(["--latest"], state)
        out = json.loads(p.stdout) if p.returncode == 0 else {}
        check("latest picks remfix", p.returncode == 0 and out["workflow"]["workflow_uuid"] == REMFIX
              and out["selection"] == {"mode": "latest"}, f"rc={p.returncode} {p.stderr}")
        e = run_cli(["--wf", REMFIX], state)
        check("explicit mode", json.loads(e.stdout)["selection"] == {"mode": "explicit"})


def test_latest_ambiguous_within_window() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = materialize(Path(tmp), [CLEAN, REMFIX, NEAR])
        p = run_cli(["--latest"], state)
        expect_error("ambiguous", p, "ambiguous_selection", REMFIX)
        check("ambiguous names both", NEAR in p.stderr, p.stderr)
        # exactly 900s apart is still ambiguous; 901s is not
        s2 = Path(tmp) / "s2"
        _inline(s2, "wf-a-1", "2026-10-06T12:00:00Z")
        _inline(s2, "wf-b-2", "2026-10-06T11:45:00Z")
        expect_error("boundary 900s", run_cli(["--latest"], s2), "ambiguous_selection")
        _inline(s2, "wf-b-2", "2026-10-06T11:44:59Z")
        check("901s ok", run_cli(["--latest"], s2).returncode == 0)


def test_list_shape_and_order() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = materialize(Path(tmp), [CLEAN, REMFIX, NEAR])
        p = run_cli(["--list"], state)
        out = json.loads(p.stdout)
        exp = [{"workflow_uuid": REMFIX, "workflow_type": "BUILD", "updated_at": "2026-10-06T12:00:00Z"},
               {"workflow_uuid": NEAR, "workflow_type": "BUILD", "updated_at": "2026-10-06T11:50:00Z"},
               {"workflow_uuid": CLEAN, "workflow_type": "BUILD", "updated_at": "2026-10-06T10:00:00Z"}]
        got = [{k: c[k] for k in ("workflow_uuid", "workflow_type", "updated_at")} for c in out["candidates"]]
        check("list order/fields", p.returncode == 0 and got == exp, str(got))
        check("list keys", list(out) == ["schema_version", "candidates", "ambiguous", "skipped_unparseable"]
              and out["ambiguous"] is True and out["skipped_unparseable"] == 0
              and all("user_request" in c for c in out["candidates"]), str(out))


def test_list_limit_5() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        for i in range(7):
            _inline(state, f"wf-x-{i}", f"2026-10-0{i + 1}T00:00:00Z")
        out = json.loads(run_cli(["--list"], state).stdout)
        ids = [c["workflow_uuid"] for c in out["candidates"]]
        check("limit 5 newest first", ids == [f"wf-x-{i}" for i in (6, 5, 4, 3, 2)] and out["ambiguous"] is False, str(ids))


def test_latest_skips_unparseable_and_counts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = materialize(Path(tmp), [CLEAN, REMFIX])
        bad = state / "workflows" / "wf-zzz-bad.json"
        bad.write_text("{bad")
        os.utime(bad, (4102444800, 4102444800))  # far-future mtime: would be newest
        p = run_cli(["--latest"], state)
        check("latest skips bad", p.returncode == 0 and json.loads(p.stdout)["workflow"]["workflow_uuid"] == REMFIX, p.stderr)
        check("list counts skipped", json.loads(run_cli(["--list"], state).stdout)["skipped_unparseable"] == 1)


def test_updated_at_fallback_mtime() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        _inline(state, "wf-has-ts", "2026-10-06T12:00:00Z")
        _inline(state, "wf-no-ts", None)
        nots = state / "workflows" / "wf-no-ts.json"
        t12 = 1791288000  # 2026-10-06T12:00:00Z
        os.utime(nots, (t12 + 100000, t12 + 100000))
        out = json.loads(run_cli(["--list"], state).stdout)
        ids = [c["workflow_uuid"] for c in out["candidates"]]
        check("mtime fallback ranks newer", ids == ["wf-no-ts", "wf-has-ts"], str(ids))
        os.utime(nots, (t12 - 100000, t12 - 100000))
        ids = [c["workflow_uuid"] for c in json.loads(run_cli(["--list"], state).stdout)["candidates"]]
        check("mtime fallback ranks older", ids == ["wf-has-ts", "wf-no-ts"], str(ids))


def test_no_workflows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        (state / "workflows").mkdir()
        expect_error("empty dir latest", run_cli(["--latest"], state), "no_workflows")
        expect_error("missing dir latest", run_cli(["--latest"], state / "nope"), "no_workflows")
        out = json.loads(run_cli(["--list"], state).stdout)
        check("empty list ok", out["candidates"] == [] and out["ambiguous"] is False and out["skipped_unparseable"] == 0, str(out))


def test_events_jsonl_not_listed_as_candidate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = materialize(Path(tmp), [CLEAN])
        ids = [c["workflow_uuid"] for c in json.loads(run_cli(["--list"], state).stdout)["candidates"]]
        out = json.loads(run_cli(["--list"], state).stdout)
        check("only artifact listed", ids == [CLEAN] and out["skipped_unparseable"] == 0, str(out))


def test_selectors_mutually_exclusive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for args in (["--wf", "wf-x", "--latest"], ["--latest", "--list"], ["--wf", "wf-x", "--list"]):
            p = run_cli(args, Path(tmp))
            check(f"exclusive {args}", p.returncode == 2 and p.stdout == "", f"rc={p.returncode}")


CATALOG_ORDER = ["remfix_cycles", "circuit_breaker_tripped", "pending_gate", "loop_counts", "doubt_refutations",
                 "proof_gaps", "stop_failures", "fallbacks", "slow_agents", "contradictions", "compactions"]
ALL = "wf-retro-all-20261006-000000-aaaa0003"
BREAKER = "wf-retro-breaker-20261006-000000-aaaa0004"


def test_extractors_match_catalog() -> None:
    print("\n[catalog]")
    import craftflow_retro as cr
    pairs = cr.EXTRACTORS
    check("EXTRACTORS tuple of (id, fn) pairs in catalog order",
          isinstance(pairs, tuple) and all(isinstance(p, tuple) and len(p) == 2 and callable(p[1]) for p in pairs)
          and [p[0] for p in pairs] == CATALOG_ORDER, repr([p[0] for p in pairs]))


def inline(mutate=None, events=None) -> tuple:
    """Run the CLI on a clean-based inline artifact; return (proc, parsed_out)."""
    tmp = tempfile.TemporaryDirectory()
    try:
        state = Path(tmp.name)
        art = base_artifact("wf-a")
        if mutate:
            mutate(art)
        ev = "" if events is None else "".join(json.dumps(e) + "\n" for e in events)
        write_wf(state, "wf-a", art, ev)
        p = run_cli(["--wf", "wf-a"], state)
        return p, (json.loads(p.stdout) if p.returncode == 0 else {})
    finally:
        tmp.cleanup()


def sig_of(out: dict, sid: str):
    return next((s for s in out.get("signals", []) if s["id"] == sid), None)


def _all_out() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        state = materialize(Path(tmp), [ALL])
        p = run_cli(["--wf", ALL], state)
        return json.loads(p.stdout) if p.returncode == 0 else {}


def test_sort_order_all_fixture() -> None:
    out = _all_out()
    got = [(s["id"], s["weight"]) for s in out.get("signals", [])]
    want = [("circuit_breaker_tripped", 10), ("remfix_cycles", 9), ("proof_gaps", 8), ("loop_counts", 7),
            ("contradictions", 6), ("doubt_refutations", 6), ("pending_gate", 5), ("fallbacks", 2),
            ("slow_agents", 2), ("stop_failures", 2), ("compactions", 1)]
    check("all fixture exact ranking", got == want, repr(got))
    check("all fixture data_gaps", out.get("data_gaps") == ["telemetry.loop_counts.weird is str, skipped"],
          repr(out.get("data_gaps")))


def test_all_fixture_evidence() -> None:
    out = _all_out()

    def first(sid):
        s = sig_of(out, sid) or {}
        e = (s.get("evidence") or [{}])[0]
        return (s.get("count"), e.get("key") or e.get("line"))
    check("all: breaker", first("circuit_breaker_tripped") == (1, "circuit_breaker"), repr(first("circuit_breaker_tripped")))
    check("all: pending_gate", first("pending_gate") == (1, "pending_gate"), repr(first("pending_gate")))
    check("all: loop_counts", first("loop_counts") == (7, "telemetry.loop_counts.re_review"), repr(first("loop_counts")))
    check("all: doubt", first("doubt_refutations") == (2, "status_history[2]"), repr(first("doubt_refutations")))
    check("all: proof_gaps", first("proof_gaps") == (2, "proof_status"), repr(first("proof_gaps")))
    check("all: stop_failures", first("stop_failures") == (1, 7), repr(first("stop_failures")))
    check("all: fallbacks", first("fallbacks") == (2, 8), repr(first("fallbacks")))
    check("all: slow_agents", first("slow_agents") == (2, "telemetry.agent_wall_clock_seconds.builder"),
          repr(first("slow_agents")))
    check("all: contradictions", first("contradictions") == (2, "status_history[1]"), repr(first("contradictions")))
    check("all: compactions", first("compactions") == (1, 11), repr(first("compactions")))
    loops = sig_of(out, "loop_counts") or {}
    check("all: loop_counts excludes remfix", "remfix" not in json.dumps(loops.get("evidence")), repr(loops))


def test_breaker_variant_key() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = materialize(Path(tmp), [BREAKER])
        p = run_cli(["--wf", BREAKER], state)
        out = json.loads(p.stdout) if p.returncode == 0 else {}
        check("breaker variant tripped only", [s["id"] for s in out.get("signals", [])] == ["circuit_breaker_tripped"],
              p.stderr + p.stdout)


def test_breaker_event_fires() -> None:
    _, out = inline(events=[{"event": "circuit_breaker_tripped"}])
    s = sig_of(out, "circuit_breaker_tripped") or {}
    check("breaker event fires", s.get("evidence", [{}])[-1].get("line") == 1, repr(s))


def test_pending_gate_variants() -> None:
    for val in ("none", "", None):
        _, out = inline(lambda a, v=val: a.__setitem__("pending_gate", v))
        check(f"pending_gate {val!r} ignored", sig_of(out, "pending_gate") is None, repr(out.get("signals")))
    _, out = inline(lambda a: a.__setitem__("pending_gate", {"kind": "dirty_tree", "x": 1}))
    s = sig_of(out, "pending_gate") or {}
    check("pending_gate dict uses kind", "dirty_tree" in s.get("summary", "") and s.get("weight") == 5, repr(s))
    _, out = inline(lambda a: a.__setitem__("pending_gate", {"x": 1}))
    check("pending_gate dict without kind uses json", '"x": 1' in (sig_of(out, "pending_gate") or {}).get("summary", ""),
          repr(out.get("signals")))


def test_loop_counts_excludes_remfix_and_flags_non_int() -> None:
    def mut(a):
        a["telemetry"]["loop_counts"] = {"remfix": 9, "re_review": 2, "bad": "x", "flag": True, "neg": -1}
    _, out = inline(mut)
    s = sig_of(out, "loop_counts") or {}
    check("loop_counts sum excludes remfix", (s.get("count"), s.get("weight")) == (2, 2), repr(s))
    gaps = out.get("data_gaps", [])
    check("loop_counts non-int gaps", "telemetry.loop_counts.bad is str, skipped" in gaps
          and "telemetry.loop_counts.flag is bool, skipped" in gaps, repr(gaps))
    _, out = inline(lambda a: a["telemetry"].__setitem__("loop_counts", {"remfix": 5}))
    check("loop_counts remfix-only no signal", sig_of(out, "loop_counts") is None, repr(out.get("signals")))


def test_proof_gaps_tokens() -> None:
    def mut(a):
        a["proof_status"] = "passed"
        a["phase_status"] = {"p1": "verified_passed_with_disclosed_gap", "p2": "completed_with_disclosed_descope",
                             "p3": "failed", "p4": "blocked", "p5": "completed", "p6": "verified_passed", "p7": 5}
    _, out = inline(mut)
    s = sig_of(out, "proof_gaps") or {}
    keys = [e["key"] for e in s.get("evidence", [])]
    check("proof_gaps tokens", s.get("count") == 4 and s.get("weight") == 16
          and keys == ["phase_status.p1", "phase_status.p2", "phase_status.p3", "phase_status.p4"], repr(s))
    _, out = inline(lambda a: a.__setitem__("proof_status", "human_needed"))
    check("proof_gaps human_needed", (sig_of(out, "proof_gaps") or {}).get("count") == 1, repr(out.get("signals")))
    _, out = inline(lambda a: a.__setitem__("proof_status", "passed"))
    check("proof_gaps passed none", sig_of(out, "proof_gaps") is None, repr(out.get("signals")))


def test_slow_agents_threshold() -> None:
    def mut(a):
        a["telemetry"]["agent_wall_clock_seconds"] = {"a": 899, "b": 900, "c": 0, "d": "x", "e": True}
    _, out = inline(mut, events=[{"event": "agent_completed", "duration_seconds": 899},
                                 {"event": "agent_completed", "duration_seconds": 900},
                                 {"event": "agent_completed", "duration_seconds": 0}])
    s = sig_of(out, "slow_agents") or {}
    check("slow_agents threshold", s.get("count") == 2 and [e.get("key") or e.get("line") for e in s["evidence"]]
          == ["telemetry.agent_wall_clock_seconds.b", 2], repr(s))


def test_contradictions_both_sources() -> None:
    def mut(a):
        a["status_history"].append({"event": "contradiction_logged"})
    _, out = inline(mut, events=[{"event": "contradiction_logged"}])
    s = sig_of(out, "contradictions") or {}
    check("contradictions both sources", (s.get("count"), s.get("weight"), s.get("evidence_total")) == (2, 6, 2)
          and s["evidence"][0].get("key") == "status_history[1]" and s["evidence"][1].get("line") == 1, repr(s))


def test_doubt_refutations_prefix() -> None:
    def mut(a):
        a["status_history"].append({"event": "doubt_verify_refuted_hard_cap"})
        a["status_history"].append({"event": "doubt_verify_passed"})
    _, out = inline(mut, events=[{"event": "doubt_verify_refuted"}, {"event": "doubt_verify_confirmed"}])
    s = sig_of(out, "doubt_refutations") or {}
    check("doubt_refutations prefix", (s.get("count"), s.get("weight")) == (2, 6), repr(s))


def test_fallbacks_both_events() -> None:
    _, out = inline(events=[{"event": "parallel_fallback"}, {"event": "worktree_fallback"}])
    s = sig_of(out, "fallbacks") or {}
    check("fallbacks both events", (s.get("count"), s.get("weight")) == (2, 2)
          and [e["line"] for e in s["evidence"]] == [1, 2], repr(s))


def test_compactions() -> None:
    _, out = inline(events=[{"event": "compact_occurred"}, {"event": "compact_occurred"}])
    s = sig_of(out, "compactions") or {}
    check("compactions", (s.get("count"), s.get("weight")) == (2, 2), repr(s))


def test_stop_failures() -> None:
    _, out = inline(events=[{"event": "stop_failure"}, {"event": "agent_completed"}, {"event": "stop_failure"}])
    s = sig_of(out, "stop_failures") or {}
    check("stop_failures", (s.get("count"), s.get("weight")) == (2, 4)
          and [e["line"] for e in s["evidence"]] == [1, 3], repr(s))


def test_evidence_cap_and_excerpt_truncation() -> None:
    _, out = inline(events=[{"event": "stop_failure", "note": "y" * 500} for _ in range(15)])
    s = sig_of(out, "stop_failures") or {}
    check("evidence cap", len(s.get("evidence", [])) == 10 and s.get("evidence_total") == 15 and s.get("count") == 15,
          repr(s)[:300])
    ex = s.get("evidence", [{}])[0].get("excerpt", "")
    check("excerpt truncated", len(ex) == 200 and ex.endswith("..."), repr(ex))


def test_zero_signals_friction_false() -> None:
    _, out = inline()
    check("zero signals", out.get("friction_found") is False and out.get("signals") == [], repr(out))


def test_leaf_shape_gaps() -> None:
    def mut(a):
        a["status_history"].append("oops")
    _, out = inline(mut)
    check("status_history non-dict gap", "status_history[1] is str, skipped" in out.get("data_gaps", []),
          repr(out.get("data_gaps")))


def test_f1_non_utf8_artifact() -> None:
    print("\n[F1-F3]")
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_wf(state, "wf-a", base_artifact("wf-a"), "")
        (state / "workflows" / "wf-a.json").write_bytes(b'{"a": "\xff\xfe"}')
        expect_error("F1 non-utf8 artifact", run_cli(["--wf", "wf-a"], state), "artifact_unparseable")


def test_f1_non_utf8_events() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_wf(state, "wf-a", base_artifact("wf-a"), "")
        (state / "workflows" / "wf-a.events.jsonl").write_bytes(b'{"event": "x"}\n{"event": "\xff"}\n')
        expect_error("F1 non-utf8 events", run_cli(["--wf", "wf-a"], state), "events_unparseable", "line ")


def test_f2_trailing_newline_wf_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        expect_error("F2 trailing newline wf id", run_cli(["--wf", "wf-a\n"], Path(tmp)), "invalid_wf_id")


def test_f3_unicode_line_separator_in_event() -> None:
    ev = '{"event": "stop_failure", "note": "a b\u0085c"}\n{"event": "stop_failure"}\n{bad\n'
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_wf(state, "wf-a", base_artifact("wf-a"), ev)
        expect_error("F3 U+2028 keeps physical line numbers", run_cli(["--wf", "wf-a"], state),
                     "events_unparseable", "line 3")
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        write_wf(state, "wf-a", base_artifact("wf-a"), ev.rsplit("{bad", 1)[0])
        p = run_cli(["--wf", "wf-a"], state)
        out = json.loads(p.stdout) if p.returncode == 0 else {}
        check("F3 valid event with U+2028 parses", p.returncode == 0 and out["inputs"]["events_lines"] == 2
              and (sig_of(out, "stop_failures") or {}).get("count") == 2, p.stderr)


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
    for mod in ("craftflow_retro", "craftflow_retro_signals"):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["PYTHONPATH"] = str(SCRIPTS)
            t0 = time.monotonic()
            proc = subprocess.run([sys.executable, "-c", f"import {mod}"], cwd=tmp,
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=10)
            elapsed = time.monotonic() - t0
            listing = os.listdir(tmp)
            check(f"import cheap {mod}", proc.returncode == 0 and proc.stdout == "" and elapsed < 0.5 and listing == [],
                  f"rc={proc.returncode} out={proc.stdout!r} err={proc.stderr!r} t={elapsed:.2f} ls={listing}")


def test_signals_module_is_leaf() -> None:
    src = (SCRIPTS / "craftflow_retro_signals.py").read_text()
    check("signals module never imports craftflow_retro",
          "import craftflow_retro\n" not in src and "from craftflow_retro " not in src
          and "from craftflow_retro import" not in src)


def main() -> int:
    tests = [
        test_clean_no_friction, test_remfix_heavy_signal, test_missing_artifact, test_missing_events,
        test_artifact_unparseable, test_artifact_not_object, test_artifact_no_id, test_history_not_list,
        test_events_unparseable_line, test_events_non_object_line, test_events_empty_file,
        test_events_blank_lines_ignored, test_non_dict_history_entry_skipped, test_invalid_wf_id,
        test_missing_selector_usage_error, test_extractors_match_catalog,
        test_latest_picks_newest, test_latest_ambiguous_within_window, test_list_shape_and_order, test_list_limit_5,
        test_latest_skips_unparseable_and_counts, test_updated_at_fallback_mtime, test_no_workflows,
        test_events_jsonl_not_listed_as_candidate, test_selectors_mutually_exclusive, test_output_deterministic,
        test_read_only_snapshot, test_import_cheap, test_signals_module_is_leaf,
        test_sort_order_all_fixture, test_all_fixture_evidence, test_breaker_variant_key, test_breaker_event_fires,
        test_pending_gate_variants, test_loop_counts_excludes_remfix_and_flags_non_int, test_proof_gaps_tokens,
        test_slow_agents_threshold, test_contradictions_both_sources, test_doubt_refutations_prefix,
        test_fallbacks_both_events, test_compactions, test_stop_failures, test_evidence_cap_and_excerpt_truncation,
        test_zero_signals_friction_false, test_leaf_shape_gaps, test_f1_non_utf8_artifact, test_f1_non_utf8_events,
        test_f2_trailing_newline_wf_id, test_f3_unicode_line_separator_in_event,
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
