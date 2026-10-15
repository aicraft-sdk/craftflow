#!/usr/bin/env python3
"""Tests for scripts/craftflow_gate_check.py (P6 of the router context cut, ADR-0073, BC-6, DD-13, PP-5).

`gate_check` evaluates the four loop caps at the points where today's router prose already decides: remediation before
creating a REM-FIX, doubt and fix-verify after the verdict (keyed on the dispatched cycle), plan review after the
pass's own increment (keyed on the pass identity). It never increments a counter, never sets `broken`, never mutates the
artifact, prints exactly one JSON line and appends one `gate_check` event under flock.

Run: python3 tests/fixtures/test_gate_check.py
Prints `Results: N passed, M failed`; exits 1 on any failure.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
SCRIPT = SCRIPTS / "craftflow_gate_check.py"
sys.path.insert(0, str(SCRIPTS))

_passes = 0
_errors = []

VERDICTS_DOUBT = ("CONFIRMED", "REFUTED", "DOUBT_THEATER")
VERDICTS_FIX = ("LOAD_BEARING", "NOT_LOAD_BEARING", "SIBLING_FOUND")
MAX_CAP = 3


def ok(name):
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name, reason):
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def check(name, cond, reason=""):
    if cond:
        ok(name)
    else:
        fail(name, reason or "assertion false")


try:
    import craftflow_gate_check as gc
except Exception as exc:  # RED: the module does not exist yet
    gc = None
    fail("import craftflow_gate_check", repr(exc))


class Project:
    """A scratch project root with one workflow artifact (or none) and an optional policy file."""

    def __init__(self, tmp, artifact="absent", policy=None, wf="wf-x"):
        self.root = Path(tmp)
        self.wf = wf
        self.dir = self.root / ".craftflow" / "state" / "workflows"
        self.dir.mkdir(parents=True)
        self.artifact_path = self.dir / f"{wf}.json"
        self.events_path = self.dir / f"{wf}.events.jsonl"
        self.policy_path = None
        if artifact != "absent":
            data = artifact if isinstance(artifact, bytes) else json.dumps(artifact).encode("utf-8")
            self.artifact_path.write_bytes(data)
        if policy is not None:
            self.policy_path = self.root / "policy.json"
            self.policy_path.write_bytes(policy if isinstance(policy, bytes) else json.dumps(policy).encode("utf-8"))

    def argv(self, *args):
        out = list(args) + ["--wf", self.wf, "--project-root", str(self.root)]
        if self.policy_path is not None:
            out += ["--policy-file", str(self.policy_path)]
        return out

    def sha(self):
        return hashlib.sha256(self.artifact_path.read_bytes()).hexdigest() if self.artifact_path.exists() else None

    def events(self):
        if not self.events_path.exists():
            return []
        return [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines()]


def call(artifact, *args, policy=None):
    """Run gate_check in-process. Returns (exit code, parsed first stdout line or None, stdout text, Project, sha_unchanged)."""
    with tempfile.TemporaryDirectory() as tmp:
        project = Project(tmp, artifact, policy)
        before = project.sha()
        code, line = gc.run(project.argv(*args))
        unchanged = project.sha() == before
        try:
            parsed = json.loads(line)
        except ValueError:
            parsed = None
        return code, parsed, line, project.events(), unchanged


def cli(artifact, *args, policy=None, env_extra=None):
    """Run gate_check as a subprocess against a scratch project (no --policy-file unless policy is given)."""
    tmp = tempfile.TemporaryDirectory()
    try:
        project = Project(tmp.name, artifact, policy)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
        env.update(env_extra or {})
        res = subprocess.run([sys.executable, str(SCRIPT)] + project.argv(*args), capture_output=True, text=True, env=env)
        return res, project.events()
    finally:
        tmp.cleanup()


def gate_args(gate, **kw):
    point = "pre_create" if gate == "remediation_cycles_max" else "post_verdict"
    out = ["--gate", gate, "--point", point]
    for flag in ("cycle", "pass_", "verdict"):
        if kw.get(flag) is not None:
            out += ["--" + flag.rstrip("_"), str(kw[flag])]
    return out


def breaker(count=None, broken=None):
    cb = {}
    if count is not None:
        cb["remfix_count"] = count
    if broken is not None:
        cb["broken"] = broken
    return {"circuit_breaker": cb}


def doubt_art(n):
    return {"telemetry": {"loop_counts": {"doubt_verify": n}}}


# ------------------------------------------------------------------------------------------------ PP-5 grid


def test_doubt_grid():
    print("\n[doubt_cycles_max post_verdict, SKILL.md call line above the :978-981 bullets]")
    bad = []
    for cycle in (1, 2, MAX_CAP - 1, MAX_CAP, MAX_CAP + 1, MAX_CAP + 2):
        code, out, _line, _ev, unchanged = call(doubt_art(1), *gate_args("doubt_cycles_max", cycle=cycle))
        want = "proceed" if cycle < MAX_CAP else "advance"
        if not (code == 0 and out and out["action"] == want and out["max"] == MAX_CAP and out["count"] == cycle
                and out["allow"] == (cycle < MAX_CAP) and unchanged):
            bad.append((cycle, code, out))
    check("doubt_grid_cycles_1_to_max_plus_2", not bad, str(bad))
    code, out, *_ = call(doubt_art(2), *gate_args("doubt_cycles_max", cycle=3))
    check("doubt_refuted_at_cycle_max_advances_skill_md_981",
          code == 0 and out["action"] == "advance" and out["reason"] == "cap-reached" and out["allow"] is False, str(out))
    code, out, *_ = call(doubt_art(0), *gate_args("doubt_cycles_max", cycle=1))
    check("doubt_confirmed_below_max_proceeds_skill_md_978", code == 0 and out["action"] == "proceed" and out["allow"] is True, str(out))
    for verdict in VERDICTS_DOUBT:
        code, out, *_ = call(doubt_art(2), *gate_args("doubt_cycles_max", cycle=3))
        name = f"doubt_{verdict.lower()}_at_cycle_max_advances_skill_md_980_981"
        check(name, code == 0 and out["action"] == "advance", str(out))


def test_fix_verify_grid():
    print("\n[fix_verify_cycles_max post_verdict, SKILL.md call line above the :1046-1063 bullets]")
    bad = []
    for cycle in (1, 2, MAX_CAP - 1, MAX_CAP, MAX_CAP + 1, MAX_CAP + 2):
        art = {"telemetry": {"loop_counts": {"fix_verify": 1}}}
        code, out, _line, _ev, unchanged = call(art, *gate_args("fix_verify_cycles_max", cycle=cycle))
        want = "proceed" if cycle < MAX_CAP else "advance"
        if not (code == 0 and out and out["action"] == want and out["max"] == MAX_CAP and unchanged):
            bad.append((cycle, code, out))
    check("fix_verify_grid_cycles_1_to_max_plus_2", not bad, str(bad))
    code, out, *_ = call({}, *gate_args("fix_verify_cycles_max", cycle=3))
    check("fix_not_load_bearing_at_cycle_max_advances_skill_md_1065", code == 0 and out["action"] == "advance", str(out))
    for verdict in VERDICTS_FIX:
        code, out, *_ = call({}, *gate_args("fix_verify_cycles_max", cycle=3))
        name = f"fix_{verdict.lower()}_at_cycle_max_advances_skill_md_1046"
        check(name, code == 0 and out["action"] == "advance", str(out))
    code, out, *_ = call({}, *gate_args("fix_verify_cycles_max", cycle=1))
    check("fix_below_max_proceeds_skill_md_1046", code == 0 and out["action"] == "proceed", str(out))


def test_fallback_counter_never_used_for_cycle():
    code, out, *_ = call(doubt_art(5), *gate_args("doubt_cycles_max", cycle=1))
    check("fallback_counter_never_used_for_cycle",
          code == 0 and out["action"] == "proceed" and out["counter"] == 5 and out["count"] == 1, str(out))
    code, out, *_ = call(doubt_art(0), *gate_args("doubt_cycles_max", cycle=3))
    check("fallback_counter_zero_does_not_hide_cap", code == 0 and out["action"] == "advance" and out["counter"] == 0, str(out))


def test_remediation_grid():
    print("\n[remediation_cycles_max pre_create, remediation-and-research.md:27-33]")
    code, out, *_ = call(breaker(3, True), *gate_args("remediation_cycles_max"))
    check("remediation_broken_true_checkpoint_rr_28",
          code == 0 and out["action"] == "human_checkpoint" and out["reason"] == "cap-reached" and out["allow"] is False and out["max"] == 3,
          str(out))
    code, out, *_ = call(breaker(3, False), *gate_args("remediation_cycles_max"))
    check("remediation_inconsistent_breaker_proceeds_rr_31",
          code == 0 and out["action"] == "proceed" and out["allow"] is True and out["trips_on_next_increment"] is True, str(out))
    code, out, *_ = call(breaker(2, False), *gate_args("remediation_cycles_max"))
    check("remediation_reports_trip_on_next_increment",
          code == 0 and out["action"] == "proceed" and out["trips_on_next_increment"] is True and out["count"] == 2, str(out))
    code, out, *_ = call(breaker(1, False), *gate_args("remediation_cycles_max"))
    check("remediation_below_trip_does_not_report_trip", code == 0 and out["action"] == "proceed" and out["trips_on_next_increment"] is False, str(out))
    bad = []
    for count in (None, 0, 1, 2, 3, 4):
        for broken in (None, False, True):
            code, out, _l, _e, unchanged = call(breaker(count, broken), *gate_args("remediation_cycles_max"))
            want = "human_checkpoint" if broken is True else "proceed"
            if not (code == 0 and out["action"] == want and unchanged and out["count"] == (count or 0)):
                bad.append((count, broken, out))
    check("remediation_action_depends_only_on_broken_e39", not bad, str(bad))
    code, out, *_ = call({}, *gate_args("remediation_cycles_max"))
    check("remediation_missing_breaker_is_zero_and_proceeds", code == 0 and out["action"] == "proceed" and out["count"] == 0, str(out))


def plan_art(runs):
    return {} if runs == "absent" else {"planning_review_runs": runs}


def test_plan_review_grid():
    print("\n[plan_review_passes_max post_verdict, remediation-and-research.md:145-177]")
    bad = []
    for verdict in ("PASS", "FINDINGS"):
        for pass_n in (1, 2, 3):
            for runs in ("absent", 0, 1, 2, 3):
                code, out, _l, _e, unchanged = call(plan_art(runs), *gate_args("plan_review_passes_max", pass_=pass_n, verdict=verdict))
                capped = verdict == "FINDINGS" and pass_n >= 2
                want = "human_checkpoint" if capped else "proceed"
                counter = 0 if runs == "absent" else runs
                if not (code == 0 and out["action"] == want and out["allow"] is (not capped) and unchanged
                        and out["counter"] == counter and out["runs_matches_pass"] is (counter == pass_n)
                        and out["max"] == 2 and out["count"] == pass_n and (out["reason"] == "cap-reached") is capped):
                    bad.append((verdict, pass_n, runs, out))
    check("plan_review_grid_verdict_x_pass_x_runs_e37c", not bad, str(bad)[:600])
    code, out, *_ = call(plan_art(1), *gate_args("plan_review_passes_max", pass_=1, verdict="FINDINGS"))
    check("plan_review_pass1_findings_proceeds_rr_153", code == 0 and out["action"] == "proceed" and out["runs_matches_pass"] is True, str(out))
    code, out, *_ = call(plan_art(2), *gate_args("plan_review_passes_max", pass_=1, verdict="FINDINGS"))
    check("plan_review_pass1_findings_with_runs_2_proceeds_rr_153",
          code == 0 and out["action"] == "proceed" and out["runs_matches_pass"] is False and out["counter"] == 2, str(out))
    code, out, *_ = call(plan_art(2), *gate_args("plan_review_passes_max", pass_=2, verdict="PASS"))
    check("plan_review_pass2_pass_proceeds_rr_161", code == 0 and out["action"] == "proceed", str(out))
    code, out, *_ = call(plan_art(2), *gate_args("plan_review_passes_max", pass_=2, verdict="FINDINGS"))
    check("plan_review_pass2_findings_checkpoint_rr_168",
          code == 0 and out["action"] == "human_checkpoint" and out["reason"] == "cap-reached", str(out))
    code, out, line, *_ = call(plan_art(1), "--gate", "plan_review_passes_max", "--point", "post_verdict", "--verdict", "PASS")
    check("plan_review_missing_pass_exit_2", code == 2 and out is not None and "error" in out, f"{code} {line}")


# ------------------------------------------------------------------------------------------------ E38-E40c


def test_bad_counters_and_cycle():
    print("\n[E38 bad counters and cycle]")
    bad = []
    for value in ("3", -1, 1.5, True, None, [], {}):
        art = {"circuit_breaker": {"remfix_count": value, "broken": False}}
        code, out, *_ = call(art, *gate_args("remediation_cycles_max"))
        if not (code == 0 and out["action"] == "human_checkpoint" and out["reason"] == "invalid-count" and out["allow"] is False):
            bad.append(("remfix_count", value, out))
        art = {"telemetry": {"loop_counts": {"doubt_verify": value}}}
        code, out, *_ = call(art, *gate_args("doubt_cycles_max", cycle=1))
        if not (code == 0 and out["action"] == "human_checkpoint" and out["reason"] == "invalid-count"):
            bad.append(("doubt_verify", value, out))
        code, out, *_ = call({"planning_review_runs": value}, *gate_args("plan_review_passes_max", pass_=1, verdict="PASS"))
        if not (code == 0 and out["action"] == "human_checkpoint" and out["reason"] == "invalid-count"):
            bad.append(("planning_review_runs", value, out))
    check("bad_counters_invalid_count", not bad, str(bad)[:600])
    bad = []
    for cycle in ("0", "-1", "1.5", "true", "abc", "", "+3", " 3", "3 "):
        code, out, *_ = call({}, "--gate", "doubt_cycles_max", "--point", "post_verdict", "--cycle", cycle)
        if not (code == 0 and out["action"] == "human_checkpoint" and out["reason"] == "invalid-count"):
            bad.append((cycle, code, out))
        code, out, *_ = call({}, "--gate", "plan_review_passes_max", "--point", "post_verdict", "--pass", cycle, "--verdict", "PASS")
        if not (code == 0 and out["action"] == "human_checkpoint" and out["reason"] == "invalid-count"):
            bad.append(("pass", cycle, code, out))
    check("bad_cycle_and_pass_values_invalid_count", not bad, str(bad)[:600])
    code, out, *_ = call({}, *gate_args("doubt_cycles_max", cycle="3"))
    check("cycle_string_digits_accepted", code == 0 and out["action"] == "advance", str(out))
    code, out, *_ = call({"circuit_breaker": "x"}, *gate_args("remediation_cycles_max"))
    check("non_dict_breaker_invalid_count", code == 0 and out["reason"] == "invalid-count" and out["action"] == "human_checkpoint", str(out))
    code, out, *_ = call({"circuit_breaker": {"remfix_count": 0, "broken": "true"}}, *gate_args("remediation_cycles_max"))
    check("non_bool_broken_fails_closed", code == 0 and out["action"] == "human_checkpoint" and out["reason"] == "invalid-count", str(out))


def test_missing_and_invalid_inputs():
    print("\n[E40 artifact, policy and event-file cases]")
    code, out, line, events, _ = call("absent", *gate_args("doubt_cycles_max", cycle=1))
    check("artifact_missing_checkpoint", code == 0 and out["action"] == "human_checkpoint" and out["reason"] == "artifact-unreadable"
          and out["allow"] is False, f"{code} {line}")
    for name, raw in (("not_json", b"{not json"), ("non_object", b"[1,2]"), ("non_utf8", b"\xff\xfe\x00"), ("empty", b"")):
        code, out, line, *_ = call(raw, *gate_args("doubt_cycles_max", cycle=1))
        check(f"artifact_{name}_checkpoint", code == 0 and out["reason"] == "artifact-unreadable" and out["action"] == "human_checkpoint", f"{code} {line}")
    code, out, *_ = call({}, *gate_args("plan_review_passes_max", pass_=1, verdict="PASS"))
    check("planning_review_runs_missing_is_zero_on_non_plan_workflow", code == 0 and out["action"] == "proceed" and out["counter"] == 0, str(out))
    bad_policies = {"corrupt": b"{oops", "unknown_key": {"bogus": 1}, "bool_cap": {"doubt_cycles_max": True},
                    "widened": {"doubt_cycles_max": 4}, "zero": {"doubt_cycles_max": 0}, "plan_passes_three": {"plan_review_passes_max": 3}}
    for name, pol in bad_policies.items():
        code, out, line, *_ = call(doubt_art(0), *gate_args("doubt_cycles_max", cycle=1), policy=pol)
        check(f"policy_invalid_{name}_checkpoint", code == 0 and out["action"] == "human_checkpoint" and out["reason"] == "policy-invalid"
              and out["policy_status"] == "invalid" and out["allow"] is False, f"{code} {line}")
    code, out, *_ = call(doubt_art(0), *gate_args("doubt_cycles_max", cycle=3), policy=b"")
    check("policy_empty_file_invalid", code == 0 and out["reason"] == "policy-invalid", str(out))
    code, out, *_ = call(doubt_art(0), "--gate", "doubt_cycles_max", "--point", "post_verdict", "--cycle", "3", "--policy-file", "/nonexistent/p.json")
    check("policy_missing_file_uses_defaults", code == 0 and out["action"] == "advance" and out["policy_status"] == "defaults", str(out))
    code, out, *_ = call(doubt_art(0), *gate_args("doubt_cycles_max", cycle=2), policy={"doubt_cycles_max": 2})
    check("tightened_cap_e40c_advances_at_two", code == 0 and out["action"] == "advance" and out["max"] == 2 and out["policy_status"] == "ok", str(out))
    code, out, *_ = call(doubt_art(0), *gate_args("doubt_cycles_max", cycle=1), policy={"doubt_cycles_max": 2})
    check("tightened_cap_e40c_proceeds_at_one", code == 0 and out["action"] == "proceed" and out["max"] == 2, str(out))
    code, out, *_ = call(breaker(1, False), *gate_args("remediation_cycles_max"), policy={"remediation_cycles_max": 1})
    check("tightened_remediation_reports_trip", code == 0 and out["action"] == "proceed" and out["trips_on_next_increment"] is True and out["max"] == 1, str(out))
    code, out, *_ = call(doubt_art(0), *gate_args("doubt_cycles_max", cycle=3), policy={"fix_verify_cycles_max": 1})
    check("other_gates_cap_does_not_leak", code == 0 and out["max"] == 3 and out["action"] == "advance", str(out))
    # the event file: absent -> created; unwritable -> event_error but the decision still prints and exit stays 0
    with tempfile.TemporaryDirectory() as tmp:
        project = Project(tmp, doubt_art(0))
        code, line = gc.run(project.argv(*gate_args("doubt_cycles_max", cycle=1)))
        check("event_file_created_when_absent", code == 0 and project.events_path.exists() and len(project.events()) == 1, f"{code}")
    with tempfile.TemporaryDirectory() as tmp:
        project = Project(tmp, doubt_art(0))
        project.events_path.mkdir()
        code, line = gc.run(project.argv(*gate_args("doubt_cycles_max", cycle=1)))
        out = json.loads(line)
        check("event_append_failure_prints_event_error_exit_0", code == 0 and out["action"] == "proceed" and "event_error" in out
              and line.count("\n") == 0, f"{code} {line}")
    with tempfile.TemporaryDirectory() as tmp:
        project = Project(tmp, doubt_art(0))
        project.dir.rename(project.root / "moved")
        code, line = gc.run(project.argv(*gate_args("doubt_cycles_max", cycle=1)))
        out = json.loads(line)
        check("no_workflows_dir_is_artifact_unreadable_and_creates_nothing",
              code == 0 and out["reason"] == "artifact-unreadable" and "event_error" in out and not (project.root / ".craftflow" / "state" / "workflows").exists(),
              f"{code} {line}")


def test_usage_errors():
    print("\n[E40b invalid combinations and usage]")
    cases = {
        "remediation_post_verdict": ["--gate", "remediation_cycles_max", "--point", "post_verdict"],
        "doubt_pre_create": ["--gate", "doubt_cycles_max", "--point", "pre_create", "--cycle", "1"],
        "doubt_without_cycle": ["--gate", "doubt_cycles_max", "--point", "post_verdict"],
        "fix_without_cycle": ["--gate", "fix_verify_cycles_max", "--point", "post_verdict"],
        "plan_review_without_verdict": ["--gate", "plan_review_passes_max", "--point", "post_verdict", "--pass", "1"],
        "plan_review_without_pass": ["--gate", "plan_review_passes_max", "--point", "post_verdict", "--verdict", "PASS"],
        "plan_review_pre_create": ["--gate", "plan_review_passes_max", "--point", "pre_create", "--pass", "1", "--verdict", "PASS"],
        "pass_on_doubt": ["--gate", "doubt_cycles_max", "--point", "post_verdict", "--cycle", "1", "--pass", "1"],
        "pass_on_remediation": ["--gate", "remediation_cycles_max", "--point", "pre_create", "--pass", "1"],
        "cycle_on_remediation": ["--gate", "remediation_cycles_max", "--point", "pre_create", "--cycle", "1"],
        "cycle_on_plan_review": ["--gate", "plan_review_passes_max", "--point", "post_verdict", "--pass", "1", "--verdict", "PASS", "--cycle", "1"],
        "verdict_on_doubt": ["--gate", "doubt_cycles_max", "--point", "post_verdict", "--cycle", "1", "--verdict", "PASS"],
        "bad_verdict_value": ["--gate", "plan_review_passes_max", "--point", "post_verdict", "--pass", "1", "--verdict", "MAYBE"],
        "unknown_point": ["--gate", "doubt_cycles_max", "--point", "whenever", "--cycle", "1"],
    }
    for name, args in cases.items():
        code, out, line, events, unchanged = call({}, *args)
        check(f"usage_{name}_exit_2", code == 2 and out is not None and set(out) == {"error"} and "\n" not in line and not events and unchanged,
              f"{code} {line}")
    res, _ = cli({}, "--gate", "bogus", "--point", "pre_create")
    lines = res.stdout.splitlines()
    check("unknown_gate_exit_2", res.returncode == 2 and len(lines) == 1 and "error" in json.loads(lines[0]), f"{res.returncode} {res.stdout!r} {res.stderr!r}")
    for bad in ("../x", "a/b", "", ".hidden", "x y"):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(tmp, {}, wf="wf-x")
            code, line = gc.run(["--gate", "doubt_cycles_max", "--point", "post_verdict", "--cycle", "1", "--wf", bad, "--project-root", str(project.root)])
            check(f"usage_bad_wf_id_{bad!r}_exit_2", code == 2 and "error" in json.loads(line), f"{code} {line}")
    res = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    check("usage_no_args_single_json_line_exit_2", res.returncode == 2 and len(res.stdout.splitlines()) == 1 and "error" in json.loads(res.stdout), f"{res.returncode} {res.stdout!r}")


# ------------------------------------------------------------------------------------------------ contract


def test_artifact_never_mutated():
    with tempfile.TemporaryDirectory() as tmp:
        art = {"circuit_breaker": {"remfix_count": 3, "broken": False}, "telemetry": {"loop_counts": {"doubt_verify": 2}}, "planning_review_runs": 2}
        project = Project(tmp, art)
        before, mtime = project.sha(), project.artifact_path.stat().st_mtime_ns
        for args in (gate_args("remediation_cycles_max"), gate_args("doubt_cycles_max", cycle=3), gate_args("fix_verify_cycles_max", cycle=3),
                     gate_args("plan_review_passes_max", pass_=2, verdict="FINDINGS")):
            gc.run(project.argv(*args))
        after = project.artifact_path.read_bytes()
        names = sorted(p.name for p in project.dir.iterdir())
        check("artifact_never_mutated", project.sha() == before and project.artifact_path.stat().st_mtime_ns == mtime and json.loads(after) == art
              and names == ["wf-x.events.jsonl", "wf-x.json"], f"names={names}")


def test_event_appended():
    code, out, _l, events, _ = call(doubt_art(2), *gate_args("doubt_cycles_max", cycle=3))
    ev = events[0] if events else {}
    keys = {"event", "gate", "point", "count", "max", "allow", "action"}
    check("event_shape", len(events) == 1 and keys <= set(ev) and ev["event"] == "gate_check" and ev["gate"] == "doubt_cycles_max"
          and ev["point"] == "post_verdict" and ev["count"] == 3 and ev["max"] == 3 and ev["allow"] is False and ev["action"] == "advance"
          and ev["wf"] == "wf-x" and isinstance(ev.get("ts"), str), str(events))


def test_event_appended_under_flock():
    with tempfile.TemporaryDirectory() as tmp:
        project = Project(tmp, doubt_art(0))
        results = []

        def work(cycle):
            results.append(gc.run(project.argv(*gate_args("doubt_cycles_max", cycle=cycle))))

        threads = [threading.Thread(target=work, args=(c,)) for c in (1, 3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        raw = project.events_path.read_text(encoding="utf-8").splitlines()
        parsed = [json.loads(x) for x in raw]
        check("event_appended_under_flock",
              len(raw) == 2 and sorted(e["count"] for e in parsed) == [1, 3] and all(code == 0 for code, _ in results), f"{raw}")
        src = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
        check("event_append_uses_flock", "fcntl.flock" in src and "LOCK_EX" in src and "LOCK_UN" in src, "no flock in source")


def test_single_line_stdout():
    res, events = cli(doubt_art(2), *gate_args("doubt_cycles_max", cycle=3))
    out = res.stdout
    parsed = json.loads(out) if out.count("\n") == 1 and out.endswith("\n") else None
    keys = {"gate", "point", "allow", "action", "count", "max", "reason", "policy_status"}
    check("single_line_stdout", res.returncode == 0 and parsed is not None and keys <= set(parsed) and res.stderr == ""
          and out == json.dumps(parsed, sort_keys=True) + "\n", f"rc={res.returncode} out={out!r} err={res.stderr!r}")
    check("cli_event_matches_stdout", len(events) == 1 and events[0]["action"] == parsed["action"], str(events))


def test_exit_criteria_scenarios():
    print("\n[P6 exit criteria, shipped policy.json, subprocess]")
    res, _ = cli(doubt_art(2), *gate_args("doubt_cycles_max", cycle=3))
    out = json.loads(res.stdout)
    check("exit_criterion_doubt_cycle_3_advance",
          res.returncode == 0 and out["action"] == "advance" and out["allow"] is False and out["max"] == 3
          and out["policy_status"] == "ok" and out["reason"] == "cap-reached", res.stdout)
    res, _ = cli({"circuit_breaker": {"remfix_count": 3, "broken": False}}, *gate_args("remediation_cycles_max"))
    out = json.loads(res.stdout)
    check("exit_criterion_remediation_proceeds_and_trips", out["action"] == "proceed" and out["trips_on_next_increment"] is True, res.stdout)
    res, _ = cli({"circuit_breaker": {"remfix_count": 3, "broken": True}}, *gate_args("remediation_cycles_max"))
    out = json.loads(res.stdout)
    check("exit_criterion_remediation_broken_checkpoint", out["action"] == "human_checkpoint" and out["reason"] == "cap-reached", res.stdout)
    res, _ = cli({"planning_review_runs": 2}, *gate_args("plan_review_passes_max", pass_=2, verdict="FINDINGS"))
    out = json.loads(res.stdout)
    check("exit_criterion_plan_review_pass2_findings", out["action"] == "human_checkpoint" and out["reason"] == "cap-reached", res.stdout)
    res, _ = cli({"planning_review_runs": 2}, *gate_args("plan_review_passes_max", pass_=2, verdict="PASS"))
    check("exit_criterion_plan_review_pass2_pass", json.loads(res.stdout)["action"] == "proceed", res.stdout)
    res, _ = cli({"planning_review_runs": 2}, *gate_args("plan_review_passes_max", pass_=1, verdict="FINDINGS"))
    out = json.loads(res.stdout)
    check("exit_criterion_plan_review_pass1_findings_runs_2", out["action"] == "proceed" and out["runs_matches_pass"] is False, res.stdout)
    res, _ = cli(doubt_art(2), *gate_args("doubt_cycles_max", cycle=3), env_extra={"CLAUDE_PLUGIN_ROOT": "/nonexistent-plugin-root"})
    out = json.loads(res.stdout)
    check("missing_plugin_config_falls_back_to_defaults", res.returncode == 0 and out["policy_status"] == "defaults" and out["action"] == "advance", res.stdout)


def test_reads_each_cap_through_a_literal_policy_value_call():
    src = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
    tree = ast.parse(src) if src else ast.parse("")
    keys = set()
    dynamic = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "policy_value":
            if len(node.args) == 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                keys.add(node.args[1].value)
            else:
                dynamic.append(ast.dump(node)[:80])
    want = {"remediation_cycles_max", "doubt_cycles_max", "fix_verify_cycles_max", "plan_review_passes_max"}
    check("reads_each_cap_through_a_literal_policy_value_call", keys == want and not dynamic, f"keys={sorted(keys)} dynamic={dynamic}")


# ------------------------------------------------------------------------------------------------ B4 differential


def baseline_doubt(verdict, cycle):
    """What the BASELINE_SHA bullets (SKILL.md :978-981) tell the router to do, read literally: every bullet that matches applies."""
    acts = set()
    if verdict == "CONFIRMED":
        acts.add("advance")
    if verdict == "REFUTED":
        acts |= {"create_remfix", "increment_loop_counter"}
    if verdict == "DOUBT_THEATER":
        acts |= {"log_event", "skip_further_cycles", "advance"}
    if cycle == MAX_CAP:  # `CYCLE_COMPLETE: true` on cycle 3 -> advance regardless of verdict
        acts.add("advance")
    return acts


def baseline_fix(verdict, cycle):
    """SKILL.md :1046-1049 with the :1065-1067 by-reference hard stop (same rule as the doubt counter)."""
    acts = set()
    if verdict == "LOAD_BEARING":
        acts.add("advance")
    else:
        acts |= {"create_remfix", "increment_loop_counter"}
    if cycle == MAX_CAP:
        acts.add("advance")
    return acts


def with_call_line(baseline_acts, action):
    """The call-line rule: proceed -> the verdict bullets; advance -> the verdict bullet minus the REM-FIX, then advance once."""
    if action == "proceed":
        return set(baseline_acts)
    assert action == "advance", action
    return (set(baseline_acts) - {"create_remfix"}) | {"advance"}


def test_b4_differential_against_baseline_prose():
    print("\n[B4: can any verdict at any cycle take a different branch than at BASELINE_SHA?]")
    differing = []
    for gate, verdicts, model in (("doubt_cycles_max", VERDICTS_DOUBT, baseline_doubt), ("fix_verify_cycles_max", VERDICTS_FIX, baseline_fix)):
        for cycle in (1, 2, 3):
            code, out, *_ = call({}, *gate_args(gate, cycle=cycle))
            for verdict in verdicts:
                base, new = model(verdict, cycle), with_call_line(model(verdict, cycle), out["action"])
                if base != new:
                    differing.append((gate, verdict, cycle, sorted(base - new), sorted(new - base)))
    want = [("doubt_cycles_max", "REFUTED", 3, ["create_remfix"], []),
            ("fix_verify_cycles_max", "NOT_LOAD_BEARING", 3, ["create_remfix"], []),
            ("fix_verify_cycles_max", "SIBLING_FOUND", 3, ["create_remfix"], [])]
    check("b4_only_at_cap_remfix_cells_differ_difference_33", sorted(differing) == sorted(want), str(differing))
    # remediation: baseline = "broken already true -> ask, otherwise create"; plan review: the four blocks select by pass identity
    bad = []
    for count in (0, 1, 2, 3, 4):
        for broken in (False, True):
            code, out, *_ = call(breaker(count, broken), *gate_args("remediation_cycles_max"))
            baseline_action = "ask_user" if broken else "create_remfix"
            if {"human_checkpoint": "ask_user", "proceed": "create_remfix"}[out["action"]] != baseline_action:
                bad.append((count, broken, out["action"]))
    check("b4_remediation_equals_baseline_for_every_count_and_broken", not bad, str(bad))
    block = {(1, "PASS"): "proceed", (1, "FINDINGS"): "proceed", (2, "PASS"): "proceed", (2, "FINDINGS"): "human_checkpoint"}
    bad = []
    for (pass_n, verdict), want_action in block.items():
        for runs in ("absent", 0, 1, 2, 3):
            code, out, *_ = call(plan_art(runs), *gate_args("plan_review_passes_max", pass_=pass_n, verdict=verdict))
            if out["action"] != want_action:
                bad.append((pass_n, verdict, runs, out["action"]))
    check("b4_plan_review_equals_the_four_baseline_blocks_for_every_runs_value", not bad, str(bad))


def test_script_hygiene():
    src = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
    check("script_at_most_300_lines", 0 < len(src.splitlines()) <= 300, f"{len(src.splitlines())} lines")
    try:
        ast.parse(src, feature_version=(3, 9))
        compiled = bool(src)
    except SyntaxError as exc:
        compiled = False
        print(exc)
    check("py39_compile", compiled, "does not parse as Python 3.9")
    tree = ast.parse(src) if src else ast.parse("")
    writes = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"
              and any(isinstance(a, ast.Constant) and isinstance(a.value, str) and any(c in a.value for c in "wax+") for a in n.args[1:2])]
    check("only_one_write_site_the_event_append", len(writes) == 1 and "events.jsonl" in src, f"{len(writes)} write-mode open() calls")
    check("never_touches_counters_or_broken", "remfix_count\"] =" not in src and "broken\"] =" not in src and "+= 1" not in src, "counter mutation text in source")


def main():
    print("test_gate_check: running")
    if gc is None:
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    for fn in (test_doubt_grid, test_fix_verify_grid, test_fallback_counter_never_used_for_cycle, test_remediation_grid, test_plan_review_grid,
               test_bad_counters_and_cycle, test_missing_and_invalid_inputs, test_usage_errors, test_artifact_never_mutated, test_event_appended,
               test_event_appended_under_flock, test_single_line_stdout, test_exit_criteria_scenarios,
               test_reads_each_cap_through_a_literal_policy_value_call, test_b4_differential_against_baseline_prose, test_script_hygiene):
        try:
            fn()
        except Exception as exc:  # a crashed test is a failed test, never a silent pass
            fail(fn.__name__, f"raised {exc!r}")
    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
