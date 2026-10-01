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
                 override_source="passwd", last_human_ts=HUMAN_TS)
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
    kw = dict(arm="armed", arm_budget=5)
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
    blocked("A15_budget_zero", arm_budget=0)
    blocked("A15_budget_zero", facts=act_facts(settings=on_settings(maxAutoContinuesPerSession=0)))
    blocked("A15_budget_zero", arm_budget=None)


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
        blockers = core.act_blockers(facts, verdict, arm="armed", arm_budget=5)
        assert "A01_mode_not_on" in blockers and core.act_decision(settings, [], blockers, verdict) is False, mode
    verdict = core.decide([], [], "other", JEV_YES, facts_on["settings"])
    unarmed = core.act_blockers(facts_on, verdict, arm=arm_state(ctime=HUMAN_EPOCH + 5), arm_budget=5)
    assert unarmed == ["A02_not_armed"], unarmed
    assert core.act_decision(facts_on["settings"], [], unarmed, verdict) is False
    armed = core.act_blockers(facts_on, verdict, arm=arm_state(), arm_budget=5)
    assert armed == [] and core.act_decision(facts_on["settings"], core.hard_rules(facts_on), armed, verdict) is True
    ruled = act_facts(stop_reason="max_tokens")
    assert core.act_decision(ruled["settings"], core.hard_rules(ruled), armed, verdict) is False  # rules win


def test_effective_budget_is_min_of_arm_and_settings():
    assert core.effective_budget(on_settings(), 3) == 3
    assert core.effective_budget(on_settings(maxAutoContinuesPerSession=2), 9) == 2
    assert core.effective_budget(on_settings(maxAutoContinuesPerSession=0), 9) == 0
    assert core.effective_budget(on_settings(), None) == 0 and core.effective_budget(on_settings(), True) == 0
    spent = act_facts(session={"acted_since_human": 3, "last_human_ts": HUMAN_TS})
    assert clean_blockers(facts=spent, arm_budget=3) == ["A15_budget_zero"]
    assert clean_blockers(facts=spent, arm_budget=4) == []
    loose = act_facts(session={"acted_since_human": 3, "last_human_ts": "older"})  # new human line resets
    assert clean_blockers(facts=loose, arm_budget=3) == []


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
