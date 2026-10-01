#!/usr/bin/env python3
"""Tests for the stop gate ACT slice (SPEC-0019 / ADR-0056): off fast path (P2) and later phases.

Run: python3 tests/fixtures/test_craftflow_stop_gate_act.py
"""
from __future__ import annotations

import atexit
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
GATE = SCRIPTS / "craftflow_stop_gate.py"
sys.path.insert(0, str(SCRIPTS))

import craftflow_stop_gate as gate  # noqa: E402
import craftflow_stop_gate_core as core  # noqa: E402

_passes = 0
_errors = []
_scratch = []

STOP = json.dumps({"hook_event_name": "Stop", "session_id": "s-1", "transcript_path": ""}).encode()
HEAVY = ("calendar", "shutil", "subprocess", "craftflow_hooklib", "craftflow_stop_gate_core",
         "craftflow_context_nudge_compact")


def ok(name):
    global _passes
    _passes += 1
    print("  PASS: " + name)


def fail(name, reason):
    _errors.append("FAIL [" + name + "]: " + reason)
    print("  FAIL: " + name + ": " + reason)


def scratch_dir():
    path = tempfile.mkdtemp(prefix="sga-test-")
    _scratch.append(path)
    return path


atexit.register(lambda: [shutil.rmtree(p, ignore_errors=True) for p in _scratch])


def plugin_with_mode(mode):
    """A scratch plugin root whose config/stop-gate.json carries the given mode."""
    root = scratch_dir()
    os.makedirs(os.path.join(root, "config"))
    with open(os.path.join(root, "config", "stop-gate.json"), "w", encoding="utf-8") as handle:
        json.dump({"mode": mode}, handle)
    return root


def absent_user_path():
    return os.path.join(scratch_dir(), "no-such-user-config.json")


def env_for(plugin_root, user_path, **extra):
    env = {"CLAUDE_PLUGIN_ROOT": plugin_root, "CRAFTFLOW_STOP_GATE_USER_CONFIG": user_path}
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# P2: off fast path
# ---------------------------------------------------------------------------

def test_fast_path_inert_matrix():
    off = plugin_with_mode("off")
    audit = plugin_with_mode("audit")
    absent = absent_user_path()
    present = os.path.join(scratch_dir(), "user.json")
    with open(present, "w", encoding="utf-8") as handle:
        handle.write("{}")
    dangling = os.path.join(scratch_dir(), "dangling.json")
    os.symlink(os.path.join(scratch_dir(), "nowhere"), dangling)
    other = json.dumps({"hook_event_name": "SessionStart"}).encode()
    cases = [
        ("off + absent user file + Stop", STOP, env_for(off, absent), True),
        ("off + absent user file + other event", other, env_for(off, absent), True),
        ("audit plugin + absent user file", STOP, env_for(audit, absent), False),
        ("off plugin + user file present", STOP, env_for(off, present), False),
        ("off plugin + dangling symlink user file", STOP, env_for(off, dangling), False),
        ("audit plugin + other event", other, env_for(audit, absent), True),
        ("plugin config missing", STOP, env_for(scratch_dir(), absent), False),
    ]
    for label, raw, env, expected in cases:
        got = gate._fast_inert(raw, env)
        assert got is expected, "%s: expected %r got %r" % (label, expected, got)
    broken = plugin_with_mode("off")
    with open(os.path.join(broken, "config", "stop-gate.json"), "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert gate._fast_inert(STOP, env_for(broken, absent)) is False  # any error means not inert


def test_fast_path_cursor_guard():
    audit = plugin_with_mode("audit")
    present = os.path.join(scratch_dir(), "user.json")
    with open(present, "w", encoding="utf-8") as handle:
        handle.write("{}")
    env = env_for(audit, present, CURSOR_PLUGIN_ROOT="/somewhere")
    assert gate._fast_inert(STOP, env) is True


def test_fast_path_bad_stdin():
    audit = plugin_with_mode("audit")
    env = env_for(audit, absent_user_path())
    for raw in (b"", b"   ", b"not json", b"[1, 2, 3]", b"\xff\xfe\x00bad", b"123", b"null"):
        assert gate._fast_inert(raw, env) is True, raw  # parses to {} -> not a Stop event -> inert
    for raw in (b"", b"not json", b"[1, 2, 3]", b"\xff\xfe\x00bad"):
        proc = subprocess.run([sys.executable, str(GATE)], input=raw, capture_output=True,
                              env=dict(os.environ, **env), timeout=30)
        assert proc.returncode == 0, (raw, proc.returncode, proc.stderr)
        assert proc.stdout == b"", (raw, proc.stdout)


def test_fast_path_home_unset_falls_through():
    off = plugin_with_mode("off")
    assert gate._fast_inert(STOP, {"CLAUDE_PLUGIN_ROOT": off}) is False
    home = scratch_dir()
    assert gate._fast_inert(STOP, {"CLAUDE_PLUGIN_ROOT": off, "HOME": home}) is True
    folder = os.path.join(home, ".claude", "craftflow")
    os.makedirs(folder)
    with open(os.path.join(folder, "stop-gate.json"), "w", encoding="utf-8") as handle:
        handle.write("{}")
    assert gate._fast_inert(STOP, {"CLAUDE_PLUGIN_ROOT": off, "HOME": home}) is False


def test_off_mode_imports_nothing_heavy():
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PLUGIN_ROOT", "CURSOR_PLUGIN_ROOT")}
    env["CRAFTFLOW_STOP_GATE_USER_CONFIG"] = absent_user_path()
    proc = subprocess.run([sys.executable, "-X", "importtime", str(GATE)], input=STOP, capture_output=True,
                          env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == b""
    imported = set()
    for line in proc.stderr.decode("utf-8", "replace").splitlines():
        if "|" in line:
            imported.add(line.rsplit("|", 1)[1].strip())
    assert "json" in imported, sorted(imported)[:20]  # the probe actually saw imports
    leaked = [name for name in HEAVY if name in imported]
    assert not leaked, "off mode imported " + ", ".join(leaked)


def test_fast_path_constants_match_core():
    assert gate._USER_OVERRIDE_ENV == core.USER_OVERRIDE_ENV
    assert gate._USER_OVERRIDE_SEGMENTS == core.USER_OVERRIDE_SEGMENTS


def test_fast_path_user_path_matches_core():
    off = plugin_with_mode("off")
    home = scratch_dir()
    seam = os.path.join(scratch_dir(), "seam.json")
    matrix = [
        {"CRAFTFLOW_STOP_GATE_USER_CONFIG": seam},
        {"CRAFTFLOW_STOP_GATE_USER_CONFIG": "   ", "HOME": home},
        {"CRAFTFLOW_STOP_GATE_USER_CONFIG": "rel/seam.json"},
        {"HOME": home},
        {"HOME": "relative/home"},
        {},
    ]
    for extra in matrix:
        env = dict(extra, CLAUDE_PLUGIN_ROOT=off)
        want = core.user_override_path(env)
        if "HOME" not in env and not env.get("CRAFTFLOW_STOP_GATE_USER_CONFIG"):
            # core would resolve the real passwd home here: never write there, only check the fall-through
            assert gate._fast_inert(STOP, env) is False, extra
            continue
        # Make the user file present at the path core resolves; the fast path must then fall through.
        if want is not None and os.path.isabs(want):
            os.makedirs(os.path.dirname(want), exist_ok=True)
            with open(want, "w", encoding="utf-8") as handle:
                handle.write("{}")
            assert gate._fast_inert(STOP, env) is False, extra
            os.remove(want)
            assert gate._fast_inert(STOP, env) is True, extra
        elif want is not None:  # relative seam: resolves against cwd, absent here
            assert gate._fast_inert(STOP, env) is (not os.path.lexists(want)), extra
        else:  # core cannot resolve a path: the fast path must not claim inert
            assert gate._fast_inert(STOP, env) is False, extra


def test_fast_path_not_inert_where_full_path_acts():
    absent = absent_user_path()
    for label, root in (("on", plugin_with_mode("on")), ("audit", plugin_with_mode("audit")),
                        ("invalid", plugin_with_mode("bogus")), ("upper", plugin_with_mode("OFF"))):
        assert gate._fast_inert(STOP, env_for(root, absent)) is False, label
    off = plugin_with_mode("off")
    assert gate._fast_inert(STOP, {"CLAUDE_PLUGIN_ROOT": off, "CRAFTFLOW_STOP_GATE_USER_CONFIG": "  "}) is False
    assert gate._fast_inert(STOP, {"CLAUDE_PLUGIN_ROOT": off, "HOME": "relative/home"}) is False


def test_fast_path_unreadable_stdin_fails_open():
    env = dict(os.environ, CRAFTFLOW_STOP_GATE_USER_CONFIG=absent_user_path())
    env.pop("CURSOR_PLUGIN_ROOT", None)
    closed = subprocess.run(["sh", "-c", 'exec "$1" "$2" <&-', "sh", sys.executable, str(GATE)],
                            capture_output=True, env=env, timeout=30)
    assert (closed.returncode, closed.stdout) == (0, b""), (closed.returncode, closed.stdout, closed.stderr)
    devnull = subprocess.run([sys.executable, str(GATE)], stdin=subprocess.DEVNULL, capture_output=True,
                             env=env, timeout=30)
    assert (devnull.returncode, devnull.stdout) == (0, b""), (devnull.returncode, devnull.stderr)


def test_audit_mode_through_guard_writes_exactly_one_row():
    plugin = plugin_with_mode("audit")
    project = scratch_dir()
    home = scratch_dir()
    env = {k: v for k, v in os.environ.items() if k != "CURSOR_PLUGIN_ROOT"}
    env.update({"CLAUDE_PLUGIN_ROOT": plugin, "CLAUDE_PROJECT_DIR": project, "HOME": home,
                "CRAFTFLOW_STOP_GATE_USER_CONFIG": absent_user_path()})
    payload = json.dumps({"hook_event_name": "Stop", "session_id": "s-1", "transcript_path": "",
                          "stop_hook_active": False, "last_assistant_message": "All done."}).encode()
    proc = subprocess.run([sys.executable, str(GATE)], input=payload, capture_output=True, env=env,
                          cwd=project, timeout=30)
    assert proc.returncode == 0, proc.stderr
    path = Path(project) / ".craftflow" / "state" / "stop-gate" / "events.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1, rows
    assert rows[0]["mode"] == "audit", rows[0]


# ---------------------------------------------------------------------------
# P4: pure core (rules H16-H19, act blockers, arm status, reason, decision, row schema 2)
# ---------------------------------------------------------------------------

NOW = 1_800_000_000.0
HUMAN_EPOCH = NOW - 7200.0
HUMAN_TS = "2027-01-15T06:00:00Z"
PROJECT = "/work/proj"
WF = "wf-sg-0001"
JEV_YES = {"status": "ok", "kind": "phase_done_awaiting_continue", "kind_conf": 0.97, "needs_human": 0.05}


def iso(epoch):
    stamp = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def on_settings(**user):
    return core.parse_settings(None, dict({"mode": "on"}, **user), "passwd")[0]


def phase_payload(types=("none", "none"), cursor="P2", status=None):
    phases = []
    for number, kind in enumerate(types, 1):
        entry = {"phase_id": "P%d" % number, "files": ["f%d.py" % number]}
        if kind is not None:
            entry["checkpoint_type"] = kind
        phases.append(entry)
    return {"workflow_type": "BUILD", "plan_file": "docs/plans/x.md", "phase_cursor": cursor,
            "phase_status": status or {"P1": "completed", "P2": "pending"}, "normalized_phases": phases}


def act_facts(payload=None, **over):
    work = core.workflow_facts(payload or phase_payload(), lambda p: True)
    facts = core.base_facts(workflow=work, binding_reason="session_match", settings=on_settings())
    facts.update(session_id="sid-00000001", artifact_session_id="sid-00000001", wf=WF, tail_sha="aa11",
                 override_source="passwd", last_human_ts=HUMAN_TS, stop_reason="end_turn")
    facts.update(over)
    return facts


def make_arm(**over):
    go = {"computedAt": iso(NOW - 3600), "met": True, "scope": "jev", "jevKindThreshold": 0.9,
          "jevNeedsHumanMax": 0.2, "wouldContinueLabeled": 60, "sessions": 6, "precision": 0.97}
    entry = {"version": 1, "armedAt": iso(NOW - 3600), "expiresAt": iso(NOW + 7 * 3600), "projectRoot": PROJECT,
             "workflow": None, "maxAutoContinuesPerSession": 5, "go": go}
    entry.update(over)
    return entry


def arm_state(entry=None, ctime=HUMAN_EPOCH - 60, settings=None, human=HUMAN_EPOCH, wf=WF, project=PROJECT):
    consent = {"actContinue": make_arm() if entry is None else entry}
    return core.arm_status(consent, ctime, NOW, project, settings or on_settings(), human, wf)


def clean_blockers(**over):
    facts = over.pop("facts", None) or act_facts()
    verdict = over.pop("verdict", None) or core.decide([], [], "other", JEV_YES, facts["settings"])
    kw = dict(arm="armed", arm_cap=5)
    kw.update(over)
    return core.act_blockers(facts, verdict, **kw)


def test_checkpoint_type_normalised_and_h18_next_and_prev():
    work = core.workflow_facts(phase_payload(types=("none", "human_verify")), lambda p: True)
    assert [p["checkpoint_type"] for p in work["phases"]] == ["none", "human_verify"], work["phases"]
    odd = core.normalize_phases([{"phase_id": "A", "checkpoint_type": 7}, {"phase_id": "B", "checkpoint_type": " Decision "},
                                 {"phase_id": "C"}, {"phase_id": "D", "checkpoint_type": ""}])
    assert [p["checkpoint_type"] for p in odd] == [None, "decision", None, None], odd
    assert core.hard_rules(act_facts()) == []
    nxt = act_facts(phase_payload(types=("none", "human_verify")))  # next phase wants a human
    assert core.hard_rules(nxt) == ["H18_checkpoint_phase"], core.hard_rules(nxt)
    prev = act_facts(phase_payload(types=("decision", "none")))  # just-completed phase was a checkpoint
    assert core.hard_rules(prev) == ["H18_checkpoint_phase"], core.hard_rules(prev)
    missing = act_facts(phase_payload(types=(None, None)))  # missing is A04, never H18
    assert core.hard_rules(missing) == []
    assert "A04_checkpoint_type_missing" in clean_blockers(facts=missing)
    on_done = act_facts(phase_payload(types=("none", "none", "human_verify"), cursor="P2",
                                      status={"P1": "completed", "P2": "completed", "P3": "pending"}))
    assert core.hard_rules(on_done) == ["H18_checkpoint_phase"], core.hard_rules(on_done)


def test_h16_no_progress_hard_rule():
    session = {"would_continue_since_human": 1, "last_human_ts": HUMAN_TS, "last_head": "0" * 40,
               "last_cursor": "P2"}
    stuck = act_facts(stop_hook_active=True, session=session)
    assert core.hard_rules(stuck) == ["H16_no_progress"], core.hard_rules(stuck)
    assert "L1_no_progress" in core.loop_guards(stuck, session)  # the tag is still logged
    assert core.hard_rules(act_facts(stop_hook_active=False, session=session)) == []
    moved = dict(session, last_cursor="P1")
    assert core.hard_rules(act_facts(stop_hook_active=True, session=moved)) == []


def test_h17_continue_budget_at_limit_and_max_zero():
    def with_count(n, **user):
        session = {"would_continue_since_human": n, "last_human_ts": HUMAN_TS}
        return act_facts(session=session, settings=on_settings(**user))
    assert core.hard_rules(with_count(4)) == []
    assert core.hard_rules(with_count(5)) == ["H17_continue_budget"]
    assert core.hard_rules(with_count(2, maxAutoContinuesPerSession=2)) == ["H17_continue_budget"]
    assert core.hard_rules(with_count(0, maxAutoContinuesPerSession=0)) == []  # max 0 disables ACT, not H17
    assert core.hard_rules(with_count(9, maxAutoContinuesPerSession=0)) == []
    stale = act_facts(session={"would_continue_since_human": 9, "last_human_ts": "older"})
    assert core.hard_rules(stale) == []  # a new human line resets the count


def test_h19_stop_reason_not_end_turn():
    for reason in ("max_tokens", "tool_use", "", "END_TURN"):
        got = core.hard_rules(act_facts(stop_reason=reason))
        assert got == ["H19_stop_reason_not_end_turn"], (reason, got)
    for reason in ("end_turn", None, 5):
        assert core.hard_rules(act_facts(stop_reason=reason)) == [], reason
    assert core.base_facts()["stop_reason"] is None


def test_null_ts_keeps_counter_and_resets_on_new_ts():
    state = core.session_update({}, "would_continue", "h", "P2", "t1", False, 1.0, last_human_ts="A")
    state = core.session_update(state, "would_continue", "h", "P2", "t2", False, 2.0, last_human_ts=None)
    assert (state["would_continue_since_human"], state["last_human_ts"]) == (2, "A"), state
    state = core.session_update(state, "would_continue", "h", "P2", "t3", False, 3.0, last_human_ts=None)
    assert (state["would_continue_since_human"], state["last_human_ts"]) == (3, "A"), state
    state = core.session_update(state, "needs_human", "h", "P2", "t4", False, 4.0, last_human_ts="B")
    assert (state["would_continue_since_human"], state["last_human_ts"]) == (0, "B"), state
    facts = act_facts(last_human_ts=None, session={"would_continue_since_human": 5, "last_human_ts": "A"})
    assert core.hard_rules(facts) == ["H17_continue_budget"]  # null ts does not hide the count


def test_act_blockers_clean_case_and_table():
    assert clean_blockers() == []
    base = act_facts()

    def blocked(code, **over):
        facts = over.pop("facts", base)
        got = clean_blockers(facts=facts, **over)
        assert got == [code], (code, got)

    blocked("A01_mode_not_on", facts=act_facts(settings=core.parse_settings(None, {"mode": "audit"}, "passwd")[0]))
    blocked("A02_not_armed", arm="not_armed")
    blocked("A03_binding_not_exact", facts=act_facts(binding_reason="single_candidate"))
    blocked("A03_binding_not_exact", facts=act_facts(artifact_session_id="other-session-id"))
    blocked("A03_binding_not_exact", facts=act_facts(artifact_session_id=None))
    blocked("A04_checkpoint_type_missing", facts=act_facts(phase_payload(types=("none", None))))
    blocked("A05_not_jev_verdict", verdict=core.decide([], [], "phase_done_awaiting_continue", None, base["settings"]))
    blocked("A05_not_jev_verdict", verdict=core.decide([], [], "other", dict(JEV_YES, kind="asking_decision"),
                                                       base["settings"]))
    blocked("A06_human_turn_unknown", facts=act_facts(last_human_ts=None))
    blocked("A07_stop_verify_enabled", stop_verify=True)
    blocked("A08_disarmed_after_negative", facts=act_facts(session={"act_disarmed": True, "last_human_ts": HUMAN_TS}))
    blocked("A09_jev_endpoint_override", endpoint_override=True)
    blocked("A10_no_session_id", facts=act_facts(session_id="", artifact_session_id=""))
    blocked("A11_tail_already_acted", facts=act_facts(session={"last_acted_tail_sha": "aa11",
                                                                "last_human_ts": HUMAN_TS}))
    blocked("A12_phase_id_unrenderable", facts=act_facts(wf="wf-bad id"))
    blocked("A12_phase_id_unrenderable", facts=act_facts(phase_payload(types=("none", "none")), workflow=None))
    blocked("A13_session_state_unreliable", tags=["session_state_reset"])
    blocked("A13_session_state_unreliable", session_write_ok=False)
    blocked("A14_settings_not_from_user_layer", facts=act_facts(override_source="seam"))
    blocked("A14_settings_not_from_user_layer", facts=act_facts(override_source="home"))
    blocked("A15_budget_zero", arm_cap=0)
    blocked("A15_budget_zero", facts=act_facts(settings=on_settings(maxAutoContinuesPerSession=0)))
    blocked("A15_budget_zero", arm_cap=None)


def test_arm_status_table():
    assert arm_state() == "armed"
    assert core.arm_status(None, 1.0, NOW, PROJECT, on_settings(), HUMAN_EPOCH, WF) == "not_armed"
    assert core.arm_status({}, 1.0, NOW, PROJECT, on_settings(), HUMAN_EPOCH, WF) == "not_armed"
    assert arm_state("junk") == "arm_malformed"
    assert arm_state(make_arm(version=2)) == "arm_malformed"
    assert arm_state(make_arm(armedAt="yesterday")) == "arm_malformed"
    assert arm_state(make_arm(maxAutoContinuesPerSession=None)) == "arm_malformed"
    assert arm_state(make_arm(armedAt=iso(NOW + 3600), expiresAt=iso(NOW + 7200))) == "arm_future"
    assert arm_state(make_arm(expiresAt=iso(NOW + 25 * 3600))) == "arm_too_long"
    assert arm_state(make_arm(armedAt=iso(NOW - 7200), expiresAt=iso(NOW - 60))) == "arm_expired"
    assert arm_state(make_arm(projectRoot="/elsewhere")) == "arm_other_project"
    assert arm_state(make_arm(workflow="wf-other-0002")) == "arm_other_workflow"
    assert arm_state(make_arm(workflow=WF)) == "armed"
    no_go = make_arm()
    del no_go["go"]
    assert arm_state(no_go) == "go_missing"
    assert arm_state(make_arm(go=dict(make_arm()["go"], met=False))) == "go_not_met"
    assert arm_state(make_arm(go=dict(make_arm()["go"], scope="all"))) == "go_scope_mismatch"
    assert arm_state(make_arm(go=dict(make_arm()["go"], jevKindThreshold=0.95))) == "thresholds_looser"
    assert arm_state(make_arm(go=dict(make_arm()["go"], jevNeedsHumanMax=0.1))) == "thresholds_looser"
    assert arm_state(settings=on_settings(jevKindThreshold=0.8)) == "thresholds_looser"
    assert arm_state(settings=on_settings(jevKindThreshold=0.95, jevNeedsHumanMax=0.1)) == "armed"
    assert arm_state(human=None) == "human_turn_unknown"
    assert arm_state(ctime=HUMAN_EPOCH + 1) == "arm_after_last_human"
    assert arm_state(ctime=None) == "arm_after_last_human"


def test_arm_status_ctime_rule_survives_utime_backdating():
    home = scratch_dir()
    path = os.path.join(home, "consent.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"actContinue": make_arm()}, handle)
    old = os.stat(path).st_ctime - 10 * 86400
    os.utime(path, (old, old))  # the agent can backdate mtime, not ctime
    st = os.stat(path)
    assert st.st_mtime < st.st_ctime - 86400, (st.st_mtime, st.st_ctime)
    entry = make_arm(armedAt=iso(st.st_ctime - 3600), expiresAt=iso(st.st_ctime + 7 * 3600))
    now = st.st_ctime + 5
    status = core.arm_status({"actContinue": entry}, st.st_ctime, now, PROJECT, on_settings(), st.st_ctime - 30, WF)
    assert status == "arm_after_last_human", status  # armed after the last human line, whatever mtime says
    status = core.arm_status({"actContinue": entry}, st.st_ctime, now, PROJECT, on_settings(), st.st_ctime + 3, WF)
    assert status == "armed", status


def test_arm_read_only_from_consent_object_and_ctime_returned():
    seam = {"mode": "on", "actContinue": make_arm()}
    for source in ("seam", "home"):
        settings, tags = core.parse_settings(None, seam, source)
        assert settings["mode"] == "on" and "actContinue" not in settings, settings
        assert "act_arm_seam_ignored" in tags and not any(t.startswith("unknown_key") for t in tags), tags
    settings, tags = core.parse_settings(None, seam, "passwd")
    assert "act_arm_seam_ignored" not in tags and not any(t.startswith("unknown_key") for t in tags), tags
    assert core.parse_settings({"mode": "on"}, None)[0]["mode"] == "on"
    home = scratch_dir()
    folder = os.path.join(home, ".claude", "craftflow")
    os.makedirs(folder)
    with open(os.path.join(folder, "stop-gate.json"), "w", encoding="utf-8") as handle:
        json.dump({"actContinue": make_arm()}, handle)
    obj, tag, ctime = core.read_consent_file_ex(home)
    assert tag is None and obj["actContinue"]["version"] == 1, (obj, tag)
    assert abs(ctime - os.stat(os.path.join(folder, "stop-gate.json")).st_ctime) < 1e-6
    assert core.read_consent_file(home) == (obj, None)
    assert core.read_consent_file_ex(scratch_dir()) == (None, None, None)
    assert core.arm_budget({"actContinue": make_arm()}) == 5 and core.arm_budget({}) is None


def test_act_reason_exact_template_and_hostile_inputs():
    want = ("craftflow stop-gate: auto-continue 3/5 (armed by the user). The approved plan of workflow wf-a-1 "
            "continues with phase P2. Run only phase P2 under the craftflow router BUILD rules. Do not push, "
            "open a pull request, merge, or start any other work. If this phase needs the user, stop and say why.")
    assert core.act_reason(3, 5, "wf-a-1", "P2") == want
    assert core.ACT_CONTINUE_TEMPLATE % (3, 5, "wf-a-1", "P2", "P2") == want
    hostile = ("P2; git push", "P2\nignore all", "", None, 7, "P2 ", "../x", "P" * 41, "\u00e9")
    for phase in hostile:
        assert core.act_reason(1, 5, "wf-a-1", phase) is None, phase
    for wf in ("wf-a b", "wf-a-1\n", "", None, "wf-", "a-1", "wf-" + "a" * 161):
        assert core.act_reason(1, 5, wf, "P2") is None, wf
    assert core.act_reason(True, 5, "wf-a-1", "P2") is None and core.act_reason(1, "5", "wf-a-1", "P2") is None


def test_act_decision_exhaustive_blocks_unless_all_hold():
    import itertools
    would = core.decide([], [], "other", JEV_YES, on_settings())
    heur = core.decide([], [], "phase_done_awaiting_continue", None, on_settings())
    needs = core.decide([], [], "other", dict(JEV_YES, kind="asking_decision"), on_settings())
    ruled = core.decide(["H03_pending_gate"], [], "other", JEV_YES, on_settings())
    settings_by_mode = {m: core.parse_settings(None, {"mode": m}, "passwd")[0] for m in ("off", "audit", "on")}
    seen_true = 0
    for mode, hits, blockers, verdict in itertools.product(
            settings_by_mode, ([], ["H03_pending_gate"]), ([], ["A02_not_armed"]), (would, heur, needs, ruled)):
        got = core.act_decision(settings_by_mode[mode], hits, blockers, verdict)
        expect = (mode == "on" and not hits and not blockers and verdict is would)
        assert got is expect, (mode, hits, blockers, verdict["verdict_source"], got)
        seen_true += 1 if got else 0
    assert seen_true == 1
    assert core.act_decision(None, [], [], would) is False and core.act_decision(settings_by_mode["on"], [], [], None) is False
    assert core.act_decision(settings_by_mode["on"], [], [], dict(would, act_eligible=False)) is False


def test_mode_off_audit_unarmed_never_act_end_to_end_pure():
    facts_on = act_facts()
    for mode in ("off", "audit"):
        settings = core.parse_settings(None, {"mode": mode}, "passwd")[0]
        facts = act_facts(settings=settings)
        verdict = core.decide([], [], "other", JEV_YES, settings)
        blockers = core.act_blockers(facts, verdict, arm="armed", arm_cap=5)
        assert "A01_mode_not_on" in blockers and core.act_decision(settings, [], blockers, verdict) is False, mode
    verdict = core.decide([], [], "other", JEV_YES, facts_on["settings"])
    unarmed = core.act_blockers(facts_on, verdict, arm=arm_state(ctime=HUMAN_EPOCH + 5), arm_cap=5)
    assert unarmed == ["A02_not_armed"], unarmed
    assert core.act_decision(facts_on["settings"], [], unarmed, verdict) is False
    armed = core.act_blockers(facts_on, verdict, arm=arm_state(), arm_cap=5)
    assert armed == [] and core.act_decision(facts_on["settings"], core.hard_rules(facts_on), armed, verdict) is True
    ruled = act_facts(stop_reason="max_tokens")
    assert core.act_decision(ruled["settings"], core.hard_rules(ruled), armed, verdict) is False  # rules win


def test_effective_budget_is_min_of_arm_and_settings():
    assert core.effective_budget(on_settings(), 3) == 3
    assert core.effective_budget(on_settings(maxAutoContinuesPerSession=2), 9) == 2
    assert core.effective_budget(on_settings(maxAutoContinuesPerSession=0), 9) == 0
    assert core.effective_budget(on_settings(), None) == 0 and core.effective_budget(on_settings(), True) == 0
    spent = act_facts(session={"acted_since_human": 3, "last_human_ts": HUMAN_TS})
    assert clean_blockers(facts=spent, arm_cap=3) == ["A15_budget_zero"]
    assert clean_blockers(facts=spent, arm_cap=4) == []
    loose = act_facts(session={"acted_since_human": 3, "last_human_ts": "older"})  # new human line resets
    assert clean_blockers(facts=loose, arm_cap=3) == []


def test_session_update_act_fields_and_disarm():
    state = core.session_update({}, "would_continue", "h", "P2", "t1", False, 1.0, last_human_ts="A", acted=True)
    assert (state["acted_since_human"], state["last_acted_tail_sha"], state["act_disarmed"]) == (1, "t1", False), state
    state = core.session_update(state, "would_continue", "h", "P2", "t2", False, 2.0, last_human_ts="A", acted=True)
    assert state["acted_since_human"] == 2 and state["last_acted_tail_sha"] == "t2", state
    before = dict(state)
    kept = core.session_update(state, "needs_human", "h", "P2", "t3", False, 3.0, last_human_ts="A",
                               negative_reply=True)
    assert kept["act_disarmed"] is False and kept["acted_since_human"] == 2, kept  # same human turn: no disarm
    stamped = core.session_update(state, "needs_human", "h", "P2", "t3", False, 3.0, last_human_ts="B",
                                  negative_reply=False)
    assert stamped["acted_since_human"] == 0 and stamped["act_disarmed"] is False, stamped
    negative = core.session_update(state, "needs_human", "h", "P2", "t3", False, 3.0, last_human_ts="B",
                                   negative_reply=True)
    assert negative["act_disarmed"] is True and negative["acted_since_human"] == 0, negative
    later = core.session_update(negative, "needs_human", "h", "P2", "t4", False, 4.0, last_human_ts="C")
    assert later["act_disarmed"] is True, later  # sticky for the session
    fresh = core.session_update({}, "needs_human", "h", "P2", "t1", False, 1.0, last_human_ts="B", negative_reply=True)
    assert fresh["act_disarmed"] is False, fresh  # nothing was acted: nothing to disarm
    assert state == before


def test_row_schema_2_keys():
    extra = ("act_blockers", "acted", "arm_status", "stop_reason", "continues_since_human")
    assert tuple(core.ROW_KEYS[-len(extra):]) == extra, core.ROW_KEYS[-6:]
    row = core.build_row()
    assert row["schema"] == 2 and row["acted"] is False and row["act_blockers"] == [], row
    full = core.build_row(acted=True, act_blockers=["A02_not_armed"], arm_status="armed", stop_reason="end_turn",
                          continues_since_human=1)
    assert (full["acted"], full["arm_status"], full["stop_reason"], full["continues_since_human"]) == (
        True, "armed", "end_turn", 1), full
    assert full["act_blockers"] == ["A02_not_armed"]
    json.dumps(full)


# ---------------------------------------------------------------------------
# P4 hardening (hunter F1-F9, reviewer items)
# ---------------------------------------------------------------------------

def test_f1_negative_reply_disarms_on_the_very_first_stop():
    old = {"acted_since_human": 1, "last_human_ts": "older"}
    facts = act_facts(session=old)
    assert clean_blockers(facts=facts) == []  # no negative reply: the new human turn just resets
    got = clean_blockers(facts=facts, negative_reply=True)
    assert got == ["A08_disarmed_after_negative"], got
    same_turn = act_facts(session={"acted_since_human": 1, "last_human_ts": HUMAN_TS})
    assert clean_blockers(facts=same_turn, negative_reply=True) == []  # not a new turn, nothing to disarm
    idle = act_facts(session={"acted_since_human": 0, "last_human_ts": "older"})
    assert clean_blockers(facts=idle, negative_reply=True) == []
    assert core.effective_disarmed(old, HUMAN_TS, True) is True
    assert core.effective_disarmed(old, HUMAN_TS, False) is False
    assert core.effective_disarmed(old, None, True) is False
    assert core.effective_disarmed({"act_disarmed": True}, None, False) is True


def test_f2_corrupt_counters_fail_closed():
    for bad in (-100, 5.0, True, "9", [], {}):
        for key in ("acted_since_human", "would_continue_since_human"):
            session = {key: bad, "last_human_ts": HUMAN_TS}
            facts = act_facts(session=session)
            hits = core.hard_rules(facts)
            if key == "would_continue_since_human":
                assert "H17_continue_budget" in hits, (bad, hits)
            got = clean_blockers(facts=facts)
            assert "A13_session_state_unreliable" in got, (bad, key, got)
            if key == "acted_since_human":
                assert "A15_budget_zero" in got, (bad, got)
    assert core.hard_rules(act_facts(session={"would_continue_since_human": None, "last_human_ts": HUMAN_TS})) == []
    assert clean_blockers(facts=act_facts(session={"acted_since_human": 0, "last_human_ts": HUMAN_TS})) == []


def test_f3_act_decision_requires_real_empty_lists():
    would = core.decide([], [], "other", JEV_YES, on_settings())
    on = on_settings()
    assert core.act_decision(on, [], [], would) is True
    for bad in (None, "", 0, (), {}, False):
        assert core.act_decision(on, bad, [], would) is False, ("rule_hits", bad)
        assert core.act_decision(on, [], bad, would) is False, ("blockers", bad)


def test_f4_h16_fails_closed_when_loop_guards_would_swallow():
    session = {"would_continue_since_human": 1, "last_human_ts": HUMAN_TS, "last_head": "0" * 40,
               "last_cursor": "P2"}
    facts = act_facts(stop_hook_active=True, session=session, settings=dict(on_settings(),
                                                                          maxAutoContinuesPerSession="5"))
    assert "H16_no_progress" in core.hard_rules(facts), core.hard_rules(facts)
    assert "H17_continue_budget" in core.hard_rules(facts)  # an unusable budget is a hit, not a pass


def test_f5_non_finite_and_wrong_types_are_malformed():
    nan, inf = float("nan"), float("inf")
    assert arm_state(make_arm(version=1.0)) == "arm_malformed"
    assert arm_state(make_arm(version=True)) == "arm_malformed"
    assert arm_state(make_arm(go=dict(make_arm()["go"], jevKindThreshold=nan))) == "thresholds_looser"
    assert arm_state(make_arm(go=dict(make_arm()["go"], jevNeedsHumanMax=inf))) == "thresholds_looser"
    assert arm_state(human=nan) == "human_turn_unknown"
    assert arm_state(human=inf) == "human_turn_unknown"
    assert arm_state(ctime=nan) == "arm_after_last_human"
    assert core.arm_status({"actContinue": make_arm()}, HUMAN_EPOCH - 60, nan, PROJECT, on_settings(),
                           HUMAN_EPOCH, WF) == "arm_malformed"


def test_f6_any_non_false_disarm_value_disarms():
    for value in (True, 1, "yes", "false", 0, [], {}):
        facts = act_facts(session={"act_disarmed": value, "last_human_ts": HUMAN_TS})
        assert "A08_disarmed_after_negative" in clean_blockers(facts=facts), value
        kept = core.session_update({"act_disarmed": value}, "needs_human", "h", "P2", "t", False, 1.0,
                                   last_human_ts=HUMAN_TS)
        assert kept["act_disarmed"] is True, value
    for value in (None, False):
        facts = act_facts(session={"act_disarmed": value, "last_human_ts": HUMAN_TS})
        assert clean_blockers(facts=facts) == [], value
    assert clean_blockers(facts=act_facts(session={"last_human_ts": HUMAN_TS})) == []


def test_f9_missing_workflow_key_and_exact_flag_types():
    entry = make_arm()
    del entry["workflow"]
    assert arm_state(entry) == "arm_malformed"
    for bad in (None, 0, 1, "", "yes", [], 2):
        assert clean_blockers(stop_verify=bad) == ["A07_stop_verify_enabled"], ("stop_verify", bad)
        assert clean_blockers(endpoint_override=bad) == ["A09_jev_endpoint_override"], ("endpoint", bad)
    for bad in (None, "session_state_reset", {"a": 1}, 5):
        assert clean_blockers(tags=bad) == ["A13_session_state_unreliable"], ("tags", bad)
    assert clean_blockers(tags=("x",)) == [] and clean_blockers(tags=["x"]) == []


def test_blocker_eval_error_has_its_own_code():
    got = core.act_blockers({"settings": None}, {}, "armed", 5)
    assert got == ["A99_blocker_eval_error"], got
    assert core.act_blockers(None, None, "armed", 5) == ["A99_blocker_eval_error"]


def test_a14_unknown_sources_and_a03_pinned():
    for source in (None, "bogus", "", 5):
        got = clean_blockers(facts=act_facts(override_source=source))
        assert got == ["A14_settings_not_from_user_layer"], (source, got)
    got = clean_blockers(facts=act_facts(binding_reason="mention_mtime_agree"))
    assert got == ["A03_binding_not_exact"], got
    assert act_facts(binding_reason="mention_mtime_agree")["session_id"] == \
        act_facts(binding_reason="mention_mtime_agree")["artifact_session_id"]


# ---------------------------------------------------------------------------
# P5: report --scope jev and the arm CLI (DD-15: scratch homes only, never the real consent file)
# ---------------------------------------------------------------------------

def _tline(kind, ts, content):
    return json.dumps({"type": kind, "timestamp": ts, "message": {"role": kind, "content": content}})


def go_fixture(sessions=5, per=10, negative_in_first=False, schema=2):
    """(project root, events path, transcripts root) holding sessions*per labelled would_continue Jev rows."""
    root = os.path.realpath(scratch_dir())
    troot = os.path.join(root, "transcripts")
    os.makedirs(troot)
    rows = []
    for s in range(sessions):
        sid = "sess-%d" % s
        lines = [_tline("user", "2026-10-01T07:00:00Z", "start")]
        for m in range(per):
            row_ts = "2026-10-01T08:%02d:00Z" % (m * 2)
            reply_ts = "2026-10-01T08:%02d:30Z" % (m * 2)
            reply = "no, stop" if (negative_in_first and s == 0 and m == 0) else "yes"
            lines.append(_tline("assistant", row_ts, [{"type": "text", "text": "phase done. continue?"}]))
            lines.append(_tline("user", reply_ts, reply))
            rows.append(core.build_row(row_kind="stop", ts=row_ts, session_id=sid, schema=schema,
                                       transcript_path="/gone/" + sid + ".jsonl", verdict="would_continue",
                                       jev_status="ok", hook_ms=100))
        Path(troot, sid + ".jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    events = os.path.join(root, ".craftflow", "state", "stop-gate", "events.jsonl")
    os.makedirs(os.path.dirname(events))
    Path(events).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return root, events, troot


def arm_mod():
    import craftflow_stop_gate_arm as arm
    return arm


def run_arm(argv, root, home, troot=None, events=None, tty=True, confirm=None, now=None, cwd=None, env_extra=None,
            streams=None):
    """(exit code, parsed stdout JSON) of arm.main in-process with a scratch home."""
    import io
    from contextlib import redirect_stdout
    arm = arm_mod()
    env = {"CLAUDE_PROJECT_DIR": root} if root else {}
    env.update(env_extra or {})
    extra = (["--transcripts-root", troot] if troot else []) + (["--events", events] if events else [])
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = arm.main(argv + extra, env=env, home=home, tty=None if streams else tty, streams=streams,
                        confirm=confirm or (lambda _prompt: os.path.basename(root)), now=now, cwd=cwd or root)
    text = buf.getvalue().strip()
    return code, (json.loads(text) if text else None)


NOW = 1790000000.0  # fixed clock for the arm tests


def test_report_scope_jev_counts_schema2_rows_only():
    root, events, troot = go_fixture(sessions=2, per=3)
    mixed = [json.loads(line) for line in Path(events).read_text(encoding="utf-8").splitlines()]
    legacy = [dict(r, schema=1, session_id="old") for r in mixed[:2]]
    import craftflow_stop_gate_report as report
    allrep = report.build_report(mixed + legacy, troot, scope="all")
    jevrep = report.build_report(mixed + legacy, troot, scope="jev")
    assert allrep["rows"] == 8 and allrep["scope"] == "all", allrep["rows"]
    assert jevrep["rows"] == 6 and jevrep["scope"] == "jev", jevrep["rows"]
    assert jevrep["go_criteria"]["criteria"]["would_continue_labeled"]["value"] == 6
    assert jevrep["act"] == {"acted": 0, "chains": 0, "chain_negatives": 0}, jevrep["act"]
    # acted rows join the reply without the next-row bound
    acted = [core.build_row(row_kind="stop", ts="2026-10-01T08:00:00Z", session_id="sess-0", acted=True,
                            transcript_path="/gone/sess-0.jsonl", verdict="would_continue", jev_status="ok"),
             core.build_row(row_kind="stop", ts="2026-10-01T08:00:10Z", session_id="sess-0", acted=True,
                            stop_hook_active=True, transcript_path="/gone/sess-0.jsonl",
                            verdict="would_continue", jev_status="ok")]
    out = report.build_report(acted, troot, scope="jev")
    assert out["labeled"] == 2 and out["act"]["acted"] == 2 and out["act"]["chains"] == 1, out["act"]


def test_report_go_passes_with_enough_good_rows_and_fails_on_a_negative():
    import craftflow_stop_gate_report as report
    root, events, troot = go_fixture()
    rows, _cut = report.load_events_ex(events)
    good = report.build_report(rows, troot, scope="jev")
    assert good["go_criteria"]["met"] is True, good["go_criteria"]
    root2, events2, troot2 = go_fixture(negative_in_first=True)
    bad = report.build_report(report.load_events_ex(events2)[0], troot2, scope="jev")
    assert bad["go_criteria"]["met"] is False and bad["go_criteria"]["stats"]["negatives"] == 1, bad["go_criteria"]
    root3, events3, troot3 = go_fixture(schema=1)  # schema 1 rows never count toward the jev scope
    none = report.build_report(report.load_events_ex(events3)[0], troot3, scope="jev")
    assert none["rows"] == 0 and none["go_criteria"]["met"] is False, none["rows"]


def test_arm_cli_without_a_tty_exits_2_no_tty():
    scratch = scratch_dir()
    proc = subprocess.run([sys.executable, str(SCRIPTS / "craftflow_stop_gate_arm.py"), "arm"],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
                          env=dict(os.environ, CLAUDE_PROJECT_DIR=scratch))
    assert proc.returncode == 2 and json.loads(proc.stdout) == {"error": "no_tty"}, (proc.returncode, proc.stdout)


def test_arm_cli_arm_refuses_without_go_then_writes_entry_in_scratch_home():
    root, events, troot = go_fixture()
    home = os.path.realpath(scratch_dir())
    bad_root, bad_events, bad_troot = go_fixture(negative_in_first=True)
    code, out = run_arm(["arm", "--workflow", "wf-a-1"], bad_root, home, bad_troot, bad_events, now=NOW)
    assert code == 2 and out["error"] == "go_not_met", (code, out)
    assert not os.path.lexists(os.path.join(home, ".claude", "craftflow", "stop-gate.json"))
    code, out = run_arm(["arm", "--workflow", "wf-a-1", "--hours", "3"], root, home, troot, events, now=NOW,
                        confirm=lambda _prompt: "wrong-name")
    assert code == 2 and out["error"] == "not_confirmed", (code, out)
    code, out = run_arm(["arm", "--workflow", "wf-a-1", "--hours", "3"], root, home, troot, events, now=NOW)
    assert code == 0 and out["armed"] is True, (code, out)
    path = os.path.join(home, ".claude", "craftflow", "stop-gate.json")
    assert (os.stat(path).st_mode & 0o777) == 0o600 and (os.stat(os.path.dirname(path)).st_mode & 0o777) == 0o700
    obj, tag, ctime = core.read_consent_file_ex(home)
    entry = obj["actContinue"]
    assert tag is None and entry["projectRoot"] == root and entry["workflow"] == "wf-a-1", entry
    assert entry["go"]["met"] is True and entry["go"]["scope"] == "jev" and entry["go"]["schemas"] == [2]
    assert entry["maxAutoContinuesPerSession"] == 5 and entry["version"] == 1
    assert core.iso_epoch(entry["expiresAt"]) - core.iso_epoch(entry["armedAt"]) == 3 * 3600
    settings, _tags = core.parse_settings({}, obj, "passwd", obj)
    assert core.arm_status(obj, ctime, NOW + 5, root, settings, ctime + 10, "wf-a-1") == "armed"
    # arming from a subdirectory of a git checkout resolves the same project root
    repo = os.path.realpath(scratch_dir())
    subprocess.run(["git", "init", "-q", repo], check=True, timeout=30)
    os.makedirs(os.path.join(repo, "sub"))
    arm = arm_mod()
    assert arm.project_root({}, os.path.join(repo, "sub")) == repo
    sub = os.path.join(repo, "sub")
    assert arm.project_root({"CLAUDE_PROJECT_DIR": sub}, sub) == sub


def test_arm_cli_disarm_removes_only_the_entry():
    root, events, troot = go_fixture()
    home = os.path.realpath(scratch_dir())
    cdir = os.path.join(home, ".claude", "craftflow")
    os.makedirs(cdir, mode=0o700)
    path = os.path.join(cdir, "stop-gate.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"notify": "push"}, handle)
    assert run_arm(["arm", "--workflow", "wf-a-1"], root, home, troot, events, now=NOW)[0] == 0
    code, out = run_arm(["disarm"], root, home, tty=False)
    assert code == 0 and out == {"armed": False, "removed": True}, (code, out)
    obj, tag, _ctime = core.read_consent_file_ex(home)
    assert obj == {"notify": "push"} and tag is None, obj
    assert run_arm(["disarm"], root, home, tty=False)[1] == {"armed": False, "removed": False}


def test_arm_cli_status_reports_arm_status():
    root, events, troot = go_fixture()
    home = os.path.realpath(scratch_dir())
    on = {"CLAUDE_PLUGIN_ROOT": plugin_with_mode("on")}
    code, out = run_arm(["status", "--workflow", "wf-a-1"], root, home, tty=False, now=NOW, env_extra=on)
    assert code == 0 and out["status"] == "not_armed", (code, out)
    assert run_arm(["arm", "--workflow", "wf-a-1"], root, home, troot, events, now=NOW)[0] == 0
    code, out = run_arm(["status", "--workflow", "wf-a-1"], root, home, tty=False, now=NOW + 60, env_extra=on)
    assert code == 0 and out["status"] == "armed" and out["projectRoot"] == root, out
    assert out["mode"] == "on" and out["effective_budget"] == 5, out
    assert out["caveat"] == "human_turn_unchecked" and out["ctimeRule"] == "unchecked_until_next_prompt", out
    # the shipped plugin mode is off: the hook would refuse, so status must not say "armed"
    off = run_arm(["status", "--workflow", "wf-a-1"], root, home, tty=False, now=NOW + 60)[1]
    assert off["status"] == "mode_not_on" and off["mode"] == "off", off
    assert run_arm(["status", "--workflow", "wf-other"], root, home, tty=False, now=NOW + 60,
                   env_extra=on)[1]["status"] == "arm_other_workflow"
    assert run_arm(["status", "--workflow", "wf-a-1"], root, home, tty=False, now=NOW + 9 * 3600,
                   env_extra=on)[1]["status"] == "arm_expired"


def test_arm_cli_duration_bounds_one_to_twenty_four_hours_default_eight():
    arm = arm_mod()
    settings = dict(core.DEFAULTS)
    go = {"met": True}
    for bad in (0, 25, -1, True, 1.5, "8"):
        try:
            arm.build_arm_entry(NOW, bad, "/p", "wf-a-1", settings, go)
        except ValueError:
            continue
        raise AssertionError("hours accepted: " + repr(bad))
    for hours in (1, 24):
        entry = arm.build_arm_entry(NOW, hours, "/p", "wf-a-1", settings, go)
        assert core.iso_epoch(entry["expiresAt"]) - core.iso_epoch(entry["armedAt"]) == hours * 3600
    root, events, troot = go_fixture()
    home = os.path.realpath(scratch_dir())
    for hours in ("0", "25"):
        code, out = run_arm(["arm", "--workflow", "wf-a-1", "--hours", hours], root, home, troot, events, now=NOW)
        assert code == 2 and out["error"] == "bad_hours", (code, out)
    code, _out = run_arm(["arm", "--workflow", "wf-a-1"], root, home, troot, events, now=NOW)
    obj = core.read_consent_file(home)[0]
    assert code == 0 and core.iso_epoch(obj["actContinue"]["expiresAt"]) - NOW == 8 * 3600
    code, out = run_arm(["arm", "--workflow", "bad id; rm"], root, home, troot, events, now=NOW)
    assert code == 2 and out["error"] == "bad_workflow", (code, out)


def test_arm_cli_lock_stale_tmp_and_preserved_keys():
    root, events, troot = go_fixture()
    home = os.path.realpath(scratch_dir())
    cdir = os.path.join(home, ".claude", "craftflow")
    os.makedirs(cdir, mode=0o700)
    path = os.path.join(cdir, "stop-gate.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"notify": "push", "jevText": True}, handle)
    stale = "%s.tmp.%d" % (path, os.getpid())
    Path(stale).write_text("stale", encoding="utf-8")  # a crashed earlier run left this behind
    assert run_arm(["arm", "--workflow", "wf-a-1"], root, home, troot, events, now=NOW)[0] == 0
    assert run_arm(["arm", "--workflow", "wf-a-1"], root, home, troot, events, now=NOW + 1)[0] == 0
    obj = core.read_consent_file(home)[0]
    assert obj["notify"] == "push" and obj["jevText"] is True and "actContinue" in obj, obj
    lock = [n for n in os.listdir(cdir) if n.endswith(".lock")]
    assert len(lock) == 1 and (os.stat(os.path.join(cdir, lock[0])).st_mode & 0o777) == 0o600, os.listdir(cdir)
    assert Path(stale).read_text(encoding="utf-8") == "stale"
    assert run_arm(["disarm"], root, home, tty=False)[1]["removed"] is True
    assert core.read_consent_file(home)[0] == {"notify": "push", "jevText": True}
    assert not [n for n in os.listdir(cdir) if ".tmp" in n and n != os.path.basename(stale)], os.listdir(cdir)


def test_arm_cli_confirm_prompt_shows_go_stats_and_inputs():
    root, events, troot = go_fixture()
    home = os.path.realpath(scratch_dir())
    seen = []

    def confirm(prompt):
        seen.append(prompt)
        return os.path.basename(root)

    assert run_arm(["arm", "--workflow", "wf-a-1", "--hours", "4"], root, home, troot, events, now=NOW,
                   confirm=confirm)[0] == 0
    text = seen[0]
    for needle in ("50", "precision", "sessions", events, troot, "wf-a-1", root, "4"):
        assert needle in text, (needle, text)


def test_arm_cli_refuses_symlinked_dir_and_non_tty_stdout():
    root, events, troot = go_fixture()
    home = os.path.realpath(scratch_dir())
    target = os.path.realpath(scratch_dir())
    os.symlink(target, os.path.join(home, ".claude"))
    code, out = run_arm(["arm", "--workflow", "wf-a-1"], root, home, troot, events, now=NOW)
    assert code == 1 and out["error"] == "consent_file_refused", (code, out)
    assert os.listdir(target) == []

    class Stream:
        def __init__(self, tty):
            self.tty = tty

        def isatty(self):
            return self.tty

    home2 = os.path.realpath(scratch_dir())
    code, out = run_arm(["arm", "--workflow", "wf-a-1"], root, home2, troot, events, now=NOW,
                        streams=(Stream(True), Stream(False)))
    assert (code, out) == (2, {"error": "no_tty"}), (code, out)
    code, _out = run_arm(["arm", "--workflow", "wf-a-1"], root, home2, troot, events, now=NOW,
                         streams=(Stream(True), Stream(True)))
    assert code == 0


def test_arm_cli_project_root_worktree_and_bad_project_root():
    arm = arm_mod()
    repo = os.path.realpath(scratch_dir())
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run(git + ["init", "-q", repo], check=True, timeout=30)
    subprocess.run(git + ["-C", repo, "commit", "-q", "--allow-empty", "-m", "x"], check=True, timeout=30)
    wt = os.path.join(os.path.realpath(scratch_dir()), "wt")
    subprocess.run(git + ["-C", repo, "worktree", "add", "-q", wt], check=True, timeout=30)
    os.makedirs(os.path.join(wt, "deep"))
    assert arm.project_root({}, os.path.join(wt, "deep")) == os.path.realpath(wt)
    root, events, troot = go_fixture()
    home = os.path.realpath(scratch_dir())
    elsewhere = os.path.realpath(scratch_dir())
    for env_dir, cwd in ((os.path.join(root, "missing"), root), (events, root), (root, elsewhere)):
        code, out = run_arm(["status"], root, home, tty=False, cwd=cwd, env_extra={"CLAUDE_PROJECT_DIR": env_dir})
        assert code == 2 and out["error"] == "bad_project_root", (env_dir, cwd, code, out)


def test_arm_cli_bad_arguments_print_one_json_error_and_exit_2():
    root, _events, _troot = go_fixture()
    home = os.path.realpath(scratch_dir())
    for argv in (["arm", "--hours", "abc"], ["bogus"], []):
        code, out = run_arm(argv, root, home)
        assert code == 2 and out["error"] == "bad_arguments", (argv, code, out)
    import craftflow_stop_gate_report as report
    rows = [core.build_row(row_kind="stop", ts="2026-10-01T08:00:00Z", session_id="s")]
    assert report.build_report([dict(rows[0], schema=2.0), dict(rows[0], schema=True)], None, scope="jev")["rows"] == 0
    assert report.build_report([dict(rows[0], schema=2)], None, scope="jev")["rows"] == 1


# ---------------------------------------------------------------------------
# P6: hook ACT wiring (subprocess, scratch passwd-home, patched Jev layer)
# ---------------------------------------------------------------------------

ACT_SID = "sid-00000001"
HUMAN_OFF = 60  # seconds after base; slack so a loaded host cannot push the consent ctime past it
ACT_TEXT = "Phase P1 is done and verified. Ready for the next phase."
ACT_DRIVER = """
import os, sys
sys.path.insert(0, os.environ["SGA_SCRIPTS"])
import craftflow_stop_gate as gate
import craftflow_stop_gate_core as core
home = os.environ["SGA_HOME"]
gate._consent_home = lambda: home
core.passwd_home = lambda: home
kind = os.environ.get("SGA_KIND", "phase_done_awaiting_continue")
answer = {"status": "ok", "kind": kind, "kind_conf": 0.97, "needs_human": 0.05}
fields = {"jev_status": "ok", "jev_kind": answer["kind"], "jev_kind_conf": 0.97, "jev_needs_human": 0.05,
          "jev_latency_ms": 1, "jev_usage": None}
gate.jev_layer = lambda *a, **k: (answer, fields)
if os.environ.get("SGA_RACE"):
    orig_write = gate.write_session
    def racy(root, sid, record):
        done = orig_write(root, sid, record)
        orig_write(root, sid, dict(record, acted_since_human=record["acted_since_human"] + 7, updated_at=0))
        return done
    gate.write_session = racy
if os.environ.get("SGA_RAISE"):
    def boom(*a, **k):
        raise RuntimeError("boom")
    core.act_decision = boom
sys.exit(gate.main(sys.stdin.buffer.read()))
"""


def act_git_project():
    root = Path(scratch_dir())
    for args in (["init", "-q"], ["add", ".gitignore"], ["commit", "-q", "-m", "init"]):
        if args[0] == "add":
            (root / ".gitignore").write_text(".craftflow/\n", encoding="utf-8")
        done = subprocess.run(["git", "-C", str(root), "-c", "user.email=t@example.com", "-c", "user.name=t",
                               "-c", "commit.gpgsign=false"] + args, capture_output=True, timeout=30)
        assert done.returncode == 0, done.stderr
    return root


def act_line(kind, ts, content, **extra):
    row = {"type": kind, "message": {"role": kind, "content": content}}
    if ts is not None:
        row["timestamp"] = iso(ts)
    row["message"].update(extra)
    return row


def act_run(mode="on", base=None, arm="ok", arm_over=None, artifact_sid=ACT_SID, extra_lines=(), session=None,
            stop_reason="end_turn", plugin=None, env_extra=None, events=None, phase_ids=("P1", "P2"),
            cursor="P2", consent_late=False, seam_arm=False, hook_active=False, raw_session=None,
            sessions_mode=None, raise_in_act=False, consent_extra=None, kind=None, pad_bytes=0, events_pad=0, race=False):
    """Run the hook once as a subprocess against a fully armed scratch setup. Returns a namespace."""
    base = time.time() if base is None else base
    root = act_git_project()
    home = scratch_dir()
    entry = make_arm(armedAt=iso(base - 3600), expiresAt=iso(base + 7 * 3600),
                     projectRoot=os.path.realpath(str(root)))
    entry.update(arm_over or {})
    consent = dict({"mode": mode}, **(consent_extra or {}))
    if arm == "ok":
        consent["actContinue"] = entry
    folder = Path(home) / ".claude" / "craftflow"
    folder.mkdir(parents=True)
    path = folder / "stop-gate.json"
    seam_path = None
    if seam_arm:
        seam_path = Path(scratch_dir()) / "seam.json"
        seam_path.write_text(json.dumps({"mode": mode, "actContinue": entry}), encoding="utf-8")
        consent = {}

    def write_consent_file():
        path.write_text(json.dumps(consent), encoding="utf-8")
        os.chmod(str(path), 0o600)

    if not consent_late:
        write_consent_file()
    status = {phase_ids[0]: "completed"}
    status.update({pid: "pending" for pid in phase_ids[1:]})
    artifact = {"workflow_type": "BUILD", "plan_file": "docs/plans/x.md", "phase_cursor": cursor,
                "phase_status": status, "session_id": artifact_sid, "pending_gate": None,
                "normalized_phases": [{"phase_id": pid, "title": "t-" + pid, "files": ["f.py"],
                                       "checkpoint_type": "none"} for pid in phase_ids]}
    wfdir = root / ".craftflow" / "state" / "workflows"
    wfdir.mkdir(parents=True)
    (wfdir / (WF + ".json")).write_text(json.dumps(artifact), encoding="utf-8")
    if events is not None:
        pad = [{"ts": iso(base - 100), "wf": WF, "event": "note", "x": "y" * 1000}] * (events_pad // 1000)
        (wfdir / (WF + ".events.jsonl")).write_text("".join(json.dumps(e) + "\n" for e in pad + list(events)),
                                                    encoding="utf-8")
    human_ts = iso(base + HUMAN_OFF)
    lines = [act_line("assistant", base + 1, [{"type": "text", "text": "x" * pad_bytes}])] if pad_bytes else []
    lines += [act_line("user", base + HUMAN_OFF, "start " + WF),
              act_line("assistant", base + HUMAN_OFF + 2, [{"type": "text", "text": ACT_TEXT}],
                       stop_reason=stop_reason)]
    lines += list(extra_lines)
    transcript = Path(scratch_dir()) / "t.jsonl"
    transcript.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    if consent_late:
        write_consent_file()
    spath = gate.session_path(root, ACT_SID)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    session = {k: (head if v == "@HEAD" else v) for k, v in session.items()} if session is not None else None
    if session is not None or raw_session is not None:
        spath.parent.mkdir(parents=True)
        spath.write_text(raw_session if raw_session is not None else
                         json.dumps(dict({"last_human_ts": human_ts}, **session)), encoding="utf-8")
    if sessions_mode is not None:
        spath.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(str(spath.parent), sessions_mode)
    env = {k: v for k, v in os.environ.items()
           if k not in ("CURSOR_PLUGIN_ROOT", "CRAFTFLOW_JEV_ENDPOINT", "CRAFTFLOW_STOP_GATE_USER_CONFIG")}
    env.update({"CLAUDE_PLUGIN_ROOT": plugin or str(PLUGIN_ROOT), "CLAUDE_PROJECT_DIR": str(root),
                "HOME": home, "SGA_HOME": home, "SGA_SCRIPTS": str(SCRIPTS)})
    if seam_path:
        env["CRAFTFLOW_STOP_GATE_USER_CONFIG"] = str(seam_path)
    if raise_in_act:
        env["SGA_RAISE"] = "1"
    if race:
        env["SGA_RACE"] = "1"
    if kind:
        env["SGA_KIND"] = kind
    env.update(env_extra or {})
    payload = {"hook_event_name": "Stop", "session_id": ACT_SID, "transcript_path": str(transcript),
               "cwd": str(root), "permission_mode": "default", "stop_hook_active": hook_active,
               "last_assistant_message": ACT_TEXT}
    done = subprocess.run([sys.executable, "-c", ACT_DRIVER], input=json.dumps(payload).encode(),
                          capture_output=True, env=env, cwd=str(root), timeout=60)
    rows_path = root / ".craftflow" / "state" / "stop-gate" / "events.jsonl"
    rows = ([json.loads(x) for x in rows_path.read_text(encoding="utf-8").splitlines() if x.strip()]
            if rows_path.exists() else [])
    stored = None
    if spath.exists():
        try:
            stored = json.loads(spath.read_text(encoding="utf-8"))
        except ValueError:
            stored = "corrupt"
    for target in (spath.parent,):
        if target.exists():
            os.chmod(str(target), 0o755)
    return type("Run", (), {"code": done.returncode, "out": done.stdout.decode("utf-8"),
                            "err": done.stderr.decode("utf-8"), "rows": rows, "root": root, "session": stored,
                            "human_ts": human_ts})()


def act_quiet(run, blocker=None, arm_state_=None):
    """Assertions shared by every 'no block' case: exit 0, empty stdout, one row, optionally the reason."""
    assert run.code == 0 and run.out == "", (run.code, run.out, run.err)
    assert len(run.rows) == 1, run.rows
    row = run.rows[0]
    assert row["acted"] is False, row
    if blocker:
        assert any(code.startswith(blocker) for code in row["act_blockers"]), row["act_blockers"]
    if arm_state_:
        assert row["arm_status"] == arm_state_, row["arm_status"]
    return row


def test_p6_armed_clean_stop_prints_exactly_the_block_json_and_logs_acted():
    run = act_run()
    want = {"decision": "block", "reason": core.act_reason(1, 5, WF, "P2")}
    assert run.code == 0 and run.err == "", (run.code, run.err)
    assert run.out == json.dumps(want, ensure_ascii=True), run.out
    row = run.rows[0]
    assert (row["schema"], row["acted"], row["binding_reason"], row["continues_since_human"],
            row["arm_status"], row["act_blockers"], row["verdict"], row["stop_reason"]) == (
        2, True, "session_match", 1, "armed", [], "would_continue", "end_turn"), row
    assert run.session["acted_since_human"] == 1 and run.session["last_acted_tail_sha"] == row["tail_sha"]


def test_p6_chain_counts_up_and_stops_at_the_budget():
    base = time.time()
    chain = act_run(base=base, hook_active=True, session={"acted_since_human": 4, "would_continue_since_human": 4,
                                                          "last_head": "x"})
    assert json.loads(chain.out)["reason"] == core.act_reason(5, 5, WF, "P2"), chain.out
    spent = act_run(base=base, session={"acted_since_human": 5})
    act_quiet(spent, "A15", "armed")


def test_p6_same_tail_already_acted_does_not_act_twice():
    first = act_run()
    sha = first.rows[0]["tail_sha"]
    again = act_run(session={"acted_since_human": 1, "last_acted_tail_sha": sha})
    act_quiet(again, "A11")


def test_p6_unarmed_on_prints_nothing_and_behaves_like_audit():
    run = act_run(arm="none")
    row = act_quiet(run, "A02", "not_armed")
    assert row["mode"] == "on" and row["verdict"] == "would_continue", row
    assert run.session["acted_since_human"] == 0, run.session


def test_p6_expired_arm_prints_nothing():
    base = time.time()
    run = act_run(base=base, arm_over={"armedAt": iso(base - 10 * 3600), "expiresAt": iso(base - 3600)})
    act_quiet(run, "A02", "arm_expired")


def test_p6_other_project_arm_prints_nothing():
    act_quiet(act_run(arm_over={"projectRoot": "/somewhere/else"}), "A02", "arm_other_project")


def test_p6_disarmed_session_prints_nothing():
    act_quiet(act_run(session={"act_disarmed": True, "acted_since_human": 1}), "A08")


def test_p6_off_and_audit_modes_print_nothing():
    off = act_run(mode="off")
    assert (off.code, off.out, off.rows) == (0, "", []), (off.code, off.out, off.rows)
    audit = act_run(mode="audit")
    row = act_quiet(audit, "A01")
    assert row["mode"] == "audit", row


def test_p6_seam_only_arm_prints_nothing():
    run = act_run(seam_arm=True)
    act_quiet(run, "A02", "not_armed")
    assert "A14_settings_not_from_user_layer" in run.rows[0]["act_blockers"], run.rows[0]["act_blockers"]


def test_p6_arm_written_after_the_last_human_line_prints_nothing():
    act_quiet(act_run(consent_late=True, base=time.time() - 120), "A02", "arm_after_last_human")


def test_p6_session_write_failure_means_no_block_b1():
    run = act_run(sessions_mode=0o555)
    assert run.code == 0 and run.out == "", (run.out, run.err)
    row = run.rows[0]
    assert row["acted"] is False and "A13_session_state_unreliable" in row["act_blockers"], row
    assert run.session is None, run.session


def test_p6_corrupt_session_file_means_no_block_b1():
    run = act_run(raw_session="{not json")
    row = act_quiet(run, "A13")
    assert "session_state_reset" in row["settings_tags"], row["settings_tags"]


def test_p6_negative_reply_right_before_the_stop_disarms_and_does_not_act():
    base = time.time()
    run = act_run(base=base, session={"acted_since_human": 1},
                  extra_lines=[act_line("user", base + HUMAN_OFF + 4, "no, stop that")])
    row = act_quiet(run, "A08")
    assert run.session["act_disarmed"] is True, run.session
    assert row["last_human_ts"] == iso(base + HUMAN_OFF + 4), row["last_human_ts"]


def test_p6_interrupt_then_new_prompt_also_disarms():
    base = time.time()
    extra = [act_line("user", base + HUMAN_OFF + 4, "[Request interrupted by user]"),
             act_line("user", base + HUMAN_OFF + 6, "ok carry on please")]
    act_quiet(act_run(base=base, session={"acted_since_human": 1}, extra_lines=extra), "A08")


def test_p6_stale_artifact_session_id_means_no_block():
    act_quiet(act_run(artifact_sid="sid-other-0001"), "A03")


def test_p6_session_rebound_newer_than_the_last_human_line_means_no_block():
    base = time.time()
    events = [{"ts": iso(base + HUMAN_OFF + 1), "wf": WF, "event": "session_rebound", "from": None, "to": ACT_SID}]
    act_quiet(act_run(base=base, events=events), "A03")
    old = [{"ts": iso(base - 5), "wf": WF, "event": "session_rebound", "from": None, "to": ACT_SID}]
    assert act_run(base=base, events=old).out.startswith('{"decision": "block"')


def test_p6_hostile_phase_id_means_no_block():
    run = act_run(phase_ids=("P1", "P2 && git push"), cursor="P2 && git push")
    assert run.code == 0 and run.out == "", (run.out, run.err)
    assert any(c.startswith("A12") for c in run.rows[0]["act_blockers"]), run.rows[0]["act_blockers"]


def test_p6_real_stop_reason_from_the_transcript_feeds_h19():
    run = act_run(stop_reason="max_tokens")
    assert run.out == "" and run.rows[0]["stop_reason"] == "max_tokens", run.rows[0]
    assert "H19_stop_reason_not_end_turn" in run.rows[0]["rule_hits"], run.rows[0]["rule_hits"]


def test_p6_stop_verify_enabled_means_no_block():
    plugin = scratch_dir()
    shutil.copytree(str(PLUGIN_ROOT / "config"), os.path.join(plugin, "config"))
    Path(plugin, "config", "stop-verify.json").write_text(
        json.dumps({"enabled": True, "command": "true"}), encoding="utf-8")
    act_quiet(act_run(plugin=plugin), "A07")


def test_p6_jev_endpoint_override_means_no_block():
    act_quiet(act_run(env_extra={"CRAFTFLOW_JEV_ENDPOINT": "http://127.0.0.1:9/x"}), "A09")


def test_p6_exception_in_the_act_step_is_silent_and_exits_zero():
    run = act_run(raise_in_act=True)
    assert run.code == 0 and run.out == "", (run.code, run.out, run.err)


# --- P6 remediation: negative-reply detection, ts guards, A16/A17, race, relay exclusivity ------------

def _negative_run(text, content=None):
    base = time.time()
    line = act_line("user", base + HUMAN_OFF + 4, content if content is not None else text)
    return base, act_run(base=base, session={"acted_since_human": 1}, extra_lines=[line])


def test_p6r_curly_apostrophe_dont_is_negative():
    _base, run = _negative_run("don’t do that")
    act_quiet(run, "A08")


def test_p6r_lexicon_nope_not_yet_never_mind_hold_on_undo():
    for text in ("nope", "Not yet", "never mind", "ok, hold on a sec", "please undo that", "revert it"):
        act_quiet(_negative_run(text)[1], "A08")


def test_p6r_negative_word_anywhere_in_the_first_80_chars():
    act_quiet(_negative_run("hmm, I think we should wait before going on")[1], "A08")
    far = _negative_run("carry on with the next phase and keep the tests green. " * 3 + "nope")[1]
    assert far.out.startswith('{"decision"'), far.out  # beyond 80 chars: not a negative reply


def test_p6r_ide_prefixed_content_list_is_a_human_line_and_negative():
    blocks = [{"type": "text", "text": "<ide_selection>The user selected x</ide_selection>"},
              {"type": "text", "text": "no, stop"}]
    base, run = _negative_run("", content=blocks)
    row = act_quiet(run, "A08")
    assert row["last_human_ts"] == iso(base + HUMAN_OFF + 4), row["last_human_ts"]


def test_p6r_human_text_filters():
    mk = lambda c, **kw: dict({"type": "user", "message": {"content": c}}, **kw)  # noqa: E731
    assert gate._human_text(mk([{"type": "tool_result", "content": "x"}])) is None
    assert gate._human_text(mk([{"type": "text", "text": "<system-reminder>x</system-reminder>"}])) is None
    assert gate._human_text(mk("craftflow stop-gate: auto-continue 1/5 (armed by the user).")) is None
    assert gate._human_text(mk("no, craftflow stop-gate: is wrong")) == "no, craftflow stop-gate: is wrong"
    assert gate._human_text(mk([{"type": "image"}, {"type": "text", "text": "go"}])) == "go"


def test_p6r_human_line_without_timestamp_is_unknown_and_blocks():
    base = time.time()
    run = act_run(base=base, session={"acted_since_human": 1},
                  extra_lines=[act_line("user", None, "carry on")])
    row = act_quiet(run, "A06")
    assert row["last_human_ts"] is None, row["last_human_ts"]
    scan = gate.scan_transcript(json.dumps(act_line("user", None, "stop")).encode())
    assert scan["human_lines"] == [(float("inf"), "stop")] and gate.negative_since(scan, iso(base))


def test_p6r_future_human_timestamp_is_unknown():
    base = time.time() + 400
    run = act_run(base=base, arm_over={"armedAt": iso(time.time() - 60)})
    act_quiet(run, "A06")


def test_p6r_a16_stop_reason_unknown_blocks_and_is_pure_in_core():
    act_quiet(act_run(stop_reason=None), "A16")
    assert "A16_stop_reason_unknown" in clean_blockers(facts=act_facts(stop_reason=None))
    assert "A16_stop_reason_unknown" in clean_blockers(facts=act_facts(stop_reason=7))
    assert clean_blockers() == []


def test_p6r_truncated_transcript_tail_after_an_act_fails_closed():
    big = 1_200_000
    held = act_run(session={"acted_since_human": 1}, pad_bytes=big)
    act_quiet(held, "A17")
    fresh = act_run(pad_bytes=big)
    assert fresh.out.startswith('{"decision"'), (fresh.out, fresh.rows[0]["act_blockers"])
    scan = gate.scan_transcript(b"")
    assert scan["tail_truncated"] is False


def test_p6r_truncated_events_tail_after_an_act_fails_closed():
    base = time.time()
    run = act_run(base=base, session={"acted_since_human": 1}, events=[], events_pad=1_200_000)
    act_quiet(run, "A03")


def test_p6r_concurrent_session_writer_means_no_block_m2():
    run = act_run(race=True)
    row = act_quiet(run, "A13")
    assert run.session["acted_since_human"] == 8, run.session


def test_p6r_broken_stdout_logs_a_compensating_event_and_exits_zero():
    row = core.build_row(row_kind="stop", mode="on", verdict="would_continue", acted=True)
    seen = []
    saved = (gate.run, gate.append_row, gate.log_event, sys.stdout)

    class Broken:
        def write(self, _text):
            raise BrokenPipeError()

        def flush(self):
            pass

    gate.run = lambda *a, **k: ({"decision": "block", "reason": "r"}, row)
    gate.append_row = lambda r: seen.append(("row", r["acted"]))
    gate.log_event = lambda name, data: seen.append((name, data))
    sys.stdout = Broken()
    try:
        code = gate.main(b"{}")
    finally:
        gate.run, gate.append_row, gate.log_event, sys.stdout = saved
    assert code == 0, code
    assert seen[0] == ("row", True) and seen[-1][0] == "plugin_stop_gate_error", seen
    assert seen[-1][1].get("acted_undelivered") is True, seen


def test_p6r_read_tail_caps_the_bytes_it_returns():
    path = Path(scratch_dir()) / "big.bin"
    path.write_bytes((b"y" * 99 + b"\n") * 20000)
    data, truncated = gate.read_tail_ex(str(path))
    assert truncated is True and len(data) <= gate.TAIL_BYTES, (truncated, len(data))


def test_p6r_push_relay_on_needs_human_does_not_act():
    run = act_run(kind="asking_decision", base=time.time() - 3600,
                  consent_extra={"notify": "push", "notifyMinTurnSeconds": 0})
    row = run.rows[0]
    assert run.code == 0 and json.loads(run.out)["decision"] == "block", run.out
    assert json.loads(run.out)["reason"].startswith("craftflow stop-gate: the user asked"), run.out
    assert (row["acted"], row["verdict"], row["notify_status"]) == (False, "needs_human", "relay"), row


def test_p6r_needs_human_with_an_armed_setup_never_acts():
    run = act_run(kind="asking_decision")
    act_quiet(run, "A05")
    assert run.rows[0]["verdict"] == "needs_human"


def test_p6r_no_progress_chain_h16_blocks():
    run = act_run(hook_active=True, session={"acted_since_human": 1, "would_continue_since_human": 1,
                                             "last_head": "@HEAD", "last_cursor": "P2"})
    assert run.code == 0 and run.out == "", (run.out, run.err)
    assert "H16_no_progress" in run.rows[0]["rule_hits"], run.rows[0]["rule_hits"]
    assert run.rows[0]["acted"] is False


def main():
    print("test_craftflow_stop_gate_act: running")
    names = [n for n in list(globals()) if n.startswith("test_")]
    for name in names:
        fn = globals()[name]
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - test harness reports all failures
            fail(name, type(exc).__name__ + ": " + str(exc) + "\n" + traceback.format_exc(limit=3))
        else:
            ok(name)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print("FAIL (" + str(_passes) + " passed, " + str(len(_errors)) + " failed)", file=sys.stderr)
        return 1
    print("OK (" + str(_passes) + " passed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
