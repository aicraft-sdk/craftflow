#!/usr/bin/env python3
"""Tests for craftflow_stop_gate_core.py and the craftflow_stop_gate.py hook shell (SPEC-0018 / ADR-0055).

Run: python3 tests/fixtures/test_craftflow_stop_gate.py
"""
from __future__ import annotations

import atexit
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import craftflow_stop_gate as gate  # noqa: E402
import craftflow_stop_gate_core as core  # noqa: E402

# Hermetic: never read the developer's real user settings or consent file. The env seam points at an
# absent path and every consent lookup in this fixture goes through a scratch home, never the real one.
os.environ["CRAFTFLOW_STOP_GATE_USER_CONFIG"] = str(
    Path(tempfile.gettempdir()) / ("sg-test-no-user-override-%d-absent.json" % os.getpid()))

_passes = 0
_errors = []
_scratch = []

MEANINGS = ("finished_count", "one_based_current")


def ok(name):
    global _passes
    _passes += 1
    print("  PASS: " + name)


def fail(name, reason):
    _errors.append("FAIL [" + name + "]: " + reason)
    print("  FAIL: " + name + ": " + reason)


def scratch_dir():
    path = tempfile.mkdtemp(prefix="sg-test-")
    _scratch.append(path)
    return path


atexit.register(lambda: [shutil.rmtree(p, ignore_errors=True) for p in _scratch])


def write_consent(home, obj, mode=0o600):
    """Write a consent file under a scratch passwd-home built from the core's path segments."""
    seg = core.USER_OVERRIDE_SEGMENTS
    folder = os.path.join(home, seg[0], seg[1])
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, seg[2])
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle)
    os.chmod(path, mode)
    return path


def phase(pid, **extra):
    row = {"phase_id": pid, "title": "t-" + str(pid), "objective": "", "checks": [], "files": []}
    row.update(extra)
    return row


def wf(**kw):
    """workflow_facts over a payload with a stubbed isdir."""
    payload = {"workflow_type": "BUILD", "plan_file": "docs/plans/x.md"}
    payload.update(kw)
    return core.workflow_facts(payload, lambda p: True)


# ---------------------------------------------------------------------------
# Task 2.1: settings
# ---------------------------------------------------------------------------

def test_parse_settings_defaults_off():
    settings, tags = core.parse_settings(None, None)
    assert settings["mode"] == "off" and settings["notify"] == "off", settings
    assert settings["jevText"] is False and settings["jev_text_source"] == "off", settings
    assert settings["intCursorMeaning"] == "finished_count", settings
    assert settings["notifyMinTurnSeconds"] == 300 and settings["tailChars"] == 1500, settings
    assert settings["jevKindThreshold"] == 0.9 and settings["jevNeedsHumanMax"] == 0.2, settings
    assert settings["jevTimeoutSeconds"] == 2.0 and settings["maxAutoContinuesPerSession"] == 5, settings
    assert tags == [], tags


def test_parse_settings_on_downgraded_to_audit_with_tag():
    settings, tags = core.parse_settings({"mode": "off"}, {"mode": "on"}, "passwd")
    assert settings["mode"] == "audit", settings
    assert tags == ["act_not_available"], tags
    settings, tags = core.parse_settings({"mode": "audit"}, None)
    assert settings["mode"] == "audit" and tags == [], (settings, tags)


def test_parse_settings_invalid_values_dropped_per_key():
    user = {"mode": "AUDIT", "notify": "desktop", "notifyMinTurnSeconds": -1, "tailChars": 199,
            "jevKindThreshold": 0.4, "jevNeedsHumanMax": 0.5, "jevTimeoutSeconds": True,
            "maxAutoContinuesPerSession": 51, "bogus": 1}
    settings, tags = core.parse_settings({"mode": "audit"}, user, "passwd")
    assert settings["mode"] == "audit", settings  # invalid user value falls back to the plugin value
    assert settings["notify"] == "desktop", settings  # the valid key in the same file still applies
    assert settings["notifyMinTurnSeconds"] == 300 and settings["tailChars"] == 1500, settings
    assert settings["jevKindThreshold"] == 0.9, settings
    assert settings["jevNeedsHumanMax"] == 0.5, settings  # the upper boundary is valid
    assert settings["jevTimeoutSeconds"] == 2.0, settings  # bool is not a number
    assert settings["maxAutoContinuesPerSession"] == 5, settings
    for expected in ("invalid:mode", "invalid:notifyMinTurnSeconds", "invalid:tailChars",
                     "invalid:jevKindThreshold", "invalid:jevTimeoutSeconds",
                     "invalid:maxAutoContinuesPerSession", "unknown_key:bogus"):
        assert expected in tags, (expected, tags)
    settings, _tags = core.parse_settings(None, {"jevKindThreshold": 0.5, "tailChars": 4000,
                                                 "notifyMinTurnSeconds": 0}, "passwd")
    assert settings["jevKindThreshold"] == 0.5 and settings["tailChars"] == 4000, settings
    assert settings["notifyMinTurnSeconds"] == 0, settings


def test_jev_text_only_from_passwd_home_consent_file():
    # plugin file: ignored
    settings, tags = core.parse_settings({"jevText": True}, None, "absent", None)
    assert settings["jevText"] is False and "jev_text_plugin_ignored" in tags, (settings, tags)
    assert settings["jev_text_source"] == "ignored_plugin", settings
    # seam file with an absent consent file: text off, the rest of the seam file still applies
    seam = {"mode": "audit", "jevText": True}
    settings, tags = core.parse_settings({"mode": "off"}, seam, "seam", None)
    assert settings["jevText"] is False and settings["mode"] == "audit", settings
    assert "jev_text_seam_ignored" in tags and settings["jev_text_source"] == "ignored_seam", (settings, tags)
    assert core.jev_text_consent("seam", seam, None) == (False, "jev_text_seam_ignored")
    # HOME differing from the passwd home is the same as the seam
    assert core.jev_text_consent("home", seam, None) == (False, "jev_text_seam_ignored")
    # exhaustive table: consent value decides, whatever the other sources say
    for source in ("seam", "home", "passwd", "absent"):
        for file_val in (True, False, None):
            for consent in (True, False, None):
                obj = None if file_val is None else {"jevText": file_val}
                cons = None if consent is None else {"jevText": consent}
                got = core.jev_text_consent(source, obj, cons)[0]
                assert got is (consent is True), (source, file_val, consent, got)
    settings, tags = core.parse_settings({"mode": "off"}, seam, "seam", {"jevText": True})
    assert settings["jevText"] is True and settings["jev_text_source"] == "consent_file", settings
    assert "jev_text_seam_ignored" not in tags, tags


def test_user_override_path_env_seam_and_home():
    forced = {core.USER_OVERRIDE_ENV: "/tmp/forced-stop-gate.json", "HOME": "/h"}
    assert core.user_override_path(forced) == "/tmp/forced-stop-gate.json"
    assert core.user_override_path({"HOME": "/h"}) == "/h/.claude/craftflow/stop-gate.json"
    assert core.user_override_path({"HOME": "relative"}) is None
    # an empty seam is not a seam
    assert core.user_override_path({core.USER_OVERRIDE_ENV: "  ", "HOME": "/h"}) == (
        "/h/.claude/craftflow/stop-gate.json")
    assert core.override_source({core.USER_OVERRIDE_ENV: "/x.json", "HOME": "/p"}, "/p") == "seam"
    assert core.override_source({"HOME": "/other"}, "/p") == "home"
    assert core.override_source({"HOME": "/p"}, "/p") == "passwd"
    assert core.override_source({}, "/p") == "passwd"
    assert core.override_source({}, None) == "absent"


def test_load_user_override_absent_corrupt_too_large_not_object():
    folder = scratch_dir()
    assert core.load_json_file(None) == (None, "absent", "home_unresolved")
    assert core.load_json_file(os.path.join(folder, "nope.json")) == (None, "absent", None)
    corrupt = os.path.join(folder, "corrupt.json")
    Path(corrupt).write_text("{not json", encoding="utf-8")
    assert core.load_json_file(corrupt) == (None, "error", "corrupt")
    big = os.path.join(folder, "big.json")
    Path(big).write_text('{"mode":"audit","pad":"' + "x" * 70000 + '"}', encoding="utf-8")
    assert core.load_json_file(big) == (None, "error", "too_large")
    arr = os.path.join(folder, "arr.json")
    Path(arr).write_text("[1]", encoding="utf-8")
    assert core.load_json_file(arr) == (None, "error", "not_object")
    assert core.load_json_file(folder) == (None, "error", "unreadable")  # a directory is not a file
    good = os.path.join(folder, "good.json")
    Path(good).write_bytes(b"\xef\xbb\xbf" + b'{"mode":"audit"}')  # utf-8-sig
    assert core.load_json_file(good) == ({"mode": "audit"}, "ok", None)


def test_committed_plugin_config_is_off():
    path = PLUGIN_ROOT / "config" / "stop-gate.json"
    obj = json.loads(path.read_text(encoding="utf-8"))
    assert obj["mode"] == "off" and obj["notify"] == "off", obj
    assert obj.get("jevText", False) is False, obj
    assert obj["intCursorMeaning"] == "finished_count", obj
    settings, tags = core.parse_settings(obj, None)
    defaults, _ = core.parse_settings(None, None)
    assert settings == defaults and tags == [], (settings, tags)


def test_workflow_facts_allowlist_only():
    payload = {
        "workflow_type": "build", "plan_file": "docs/plans/x.md", "phase_cursor": "P1",
        "phase_status": {"P1": {"status": "pending", "notes": "SECRET-NOTE"}},
        "normalized_phases": [phase("P1", title="Parser", objective="goal text")],
        "pending_gate": None, "intent": {"goal": "SECRET-GOAL", "open_decisions": ["a", "b"]},
        "user_request": "SECRET-REQUEST", "memory_notes": ["SECRET-MEMORY"],
        "circuit_breaker": {"broken": False, "reason": "SECRET-REASON"},
        "status_history": [{"event": "phase_started", "note": "SECRET-HISTORY"}],
        "worktree_path": "/w", "worktree_mode": "active", "session_id": "SECRET-SESSION",
    }
    facts = core.workflow_facts(payload, lambda p: True)
    assert set(facts) == set(core.WORKFLOW_FACT_KEYS), sorted(facts)
    assert facts["workflow_type"] == "BUILD" and facts["open_decisions"] == 2, facts
    assert facts["last_event"] == "phase_started" and facts["circuit_broken"] is False, facts
    assert facts["phase_status"] == {"P1": "pending"}, facts
    dumped = json.dumps(facts)
    for marker in ("SECRET-NOTE", "SECRET-GOAL", "SECRET-REQUEST", "SECRET-MEMORY", "SECRET-REASON",
                   "SECRET-HISTORY", "SECRET-SESSION"):
        assert marker not in dumped, marker
    # terminal comes from the compact module with the injected isdir
    gone = core.workflow_facts({"worktree_path": "/gone"}, lambda p: False)
    assert gone["terminal"] is True, gone
    assert core.workflow_facts("not a dict", lambda p: True)["terminal"] is True


def test_consent_file_rejects_symlink_and_foreign_owner():
    uid = os.getuid()
    # pure stat decision, with a fake foreign uid (a real foreign-owned file needs root)
    assert core.consent_stat_ok(0o100600, uid, 10, uid) == (True, None)
    assert core.consent_stat_ok(0o100600, uid + 1, 10, uid) == (False, "consent_file_foreign_owner")
    assert core.consent_stat_ok(0o040700, uid, 10, uid) == (False, "consent_file_not_regular")
    assert core.consent_stat_ok(0o100600, uid, 65537, uid) == (False, "consent_file_too_large")
    # real files in a scratch passwd-home
    good = scratch_dir()
    write_consent(good, {"jevText": True})
    assert core.read_consent_file(good) == ({"jevText": True}, None)
    assert core.read_consent_file(scratch_dir()) == (None, None)  # absent is not a violation
    # symlinked consent file
    linked = scratch_dir()
    real = os.path.join(scratch_dir(), "real.json")
    Path(real).write_text('{"jevText": true}', encoding="utf-8")
    seg = core.USER_OVERRIDE_SEGMENTS
    os.makedirs(os.path.join(linked, seg[0], seg[1]))
    os.symlink(real, os.path.join(linked, seg[0], seg[1], seg[2]))
    assert core.read_consent_file(linked) == (None, "consent_file_symlink")
    # symlinked parent directory
    parent = scratch_dir()
    target = scratch_dir()
    write_consent(target, {"jevText": True})
    os.symlink(os.path.join(target, seg[0]), os.path.join(parent, seg[0]))
    assert core.read_consent_file(parent) == (None, "consent_file_symlink")


def test_notify_push_only_from_consent_file():
    plugin = {"mode": "off"}
    settings, tags = core.parse_settings(plugin, {"notify": "push"}, "seam", None)
    assert settings["notify"] == "desktop" and "notify_push_seam_ignored" in tags, (settings, tags)
    settings, tags = core.parse_settings({"notify": "push"}, None, "absent", None)
    assert settings["notify"] == "desktop" and "notify_push_seam_ignored" in tags, (settings, tags)
    settings, tags = core.parse_settings(plugin, {"notify": "push"}, "seam", {"notify": "push"})
    assert settings["notify"] == "push" and "notify_push_seam_ignored" not in tags, (settings, tags)
    # a consent file alone never switches push on when the effective value is not push
    settings, _tags = core.parse_settings(plugin, {"notify": "off"}, "seam", {"notify": "push"})
    assert settings["notify"] == "off", settings
    # the repository can still set desktop through the seam
    settings, tags = core.parse_settings(plugin, {"notify": "desktop"}, "seam", None)
    assert settings["notify"] == "desktop" and tags == [], (settings, tags)


# ---------------------------------------------------------------------------
# Task 2.1: real artifact shapes (DD-7a)
# ---------------------------------------------------------------------------

def test_artifact_shapes_table():
    # phase id key: phase_id, id, int ids, plain strings; entries without an id are malformed
    cases = (
        ([{"phase_id": "P1"}, {"phase_id": "P2"}], ["P1", "P2"]),
        ([{"id": "A"}, {"id": "B"}], ["A", "B"]),
        ([{"id": 1}, {"phase_id": 2}, {"id": 3}, {"id": 4}], ["1", "2", "3", "4"]),
        (["P1", "P2"], ["P1", "P2"]),
    )
    for raw, ids in cases:
        got = wf(normalized_phases=raw)
        assert [p["id"] for p in got["phases"]] == ids, (raw, got["phases"])
        assert got["phases_malformed"] is False, got
    assert wf(normalized_phases=[{"phase_id": "P1"}, {"title": "no id"}])["phases_malformed"] is True
    assert wf(normalized_phases=[{"phase_id": "P1"}, {"id": "P1"}])["phases_malformed"] is True
    assert wf(normalized_phases=[{"phase_id": True}])["phases_malformed"] is True
    assert wf(normalized_phases=[{"phase_id": 1}, {"id": 2}])["phases"][0]["title"] == ""
    # phase_id wins over id
    assert core.normalize_phases([{"phase_id": "A", "id": "B"}])[0]["id"] == "A"
    # file key aliases
    aliases = (({"files": ["a.py"]}, ["a.py"]), ({"files_surfaces": ["b.py"]}, ["b.py"]),
               ({"files/surfaces": ["c.py"]}, ["c.py"]), ({"files": "not-a-list"}, []), ({}, []))
    for extra, files in aliases:
        row = {"phase_id": "P1"}
        row.update(extra)
        assert core.normalize_phases([row])[0]["files"] == files, (extra, files)
    # string cursor and integer cursor equal to an id
    phases = core.normalize_phases([{"id": i} for i in (1, 2, 3, 4)])
    assert core.resolve_cursor("3", phases, "finished_count") == (2, "id_match")
    assert core.resolve_cursor(3, phases, "one_based_current") == (2, "id_match")  # id match wins
    assert core.resolve_cursor("zzz", phases, "finished_count") == (None, "unresolved")
    # integer cursor that equals no id: the setting decides, both values are supported
    phases = core.normalize_phases([{"phase_id": "a%d" % i} for i in range(1, 5)])
    expected = {"finished_count": (2, "int_finished_count"), "one_based_current": (1, "int_one_based")}
    for meaning in MEANINGS:
        assert core.resolve_cursor(2, phases, meaning) == expected[meaning], meaning
    # len(phases) under finished_count: every phase is finished, nothing is next
    assert core.resolve_cursor(4, phases, "finished_count") == (None, "unresolved")
    assert core.resolve_cursor(4, phases, "one_based_current") == (3, "int_one_based")
    assert core.resolve_cursor(0, phases, "one_based_current") == (None, "unresolved")
    assert core.resolve_cursor(True, phases, "finished_count") == (None, "unresolved")
    assert core.resolve_cursor(None, phases, "finished_count") == (None, "unresolved")
    # extra phase_status keys do not decide a phase; a failed sub-step key does not make P1 clean
    status = {"P1": "completed", "memory-finalize": "pending", "remfix": "failed"}
    assert core.phase_state(status, "P1") == "DONE"
    assert core.phase_state(status, "P2") == "PENDING"
    assert core.non_phase_failures(status, ["P1", "P2"]) == ["remfix"]
    # sub-step aggregation
    sub = {"phase-1-implement": "completed", "phase-1-verify": "completed"}
    assert core.phase_state(sub, "phase-1") == "DONE"
    assert core.phase_state({"phase-1-implement": "completed", "phase-1-verify": "failed"}, "phase-1") == "FAILED"
    assert core.phase_state({"phase-1-implement": "completed", "phase-1-verify": "in_progress"},
                            "phase-1") == "IN_PROGRESS"
    # numeric ids also read phase-N and the anchored phase-N- prefix; exact key wins over sub-steps
    assert core.phase_state({"phase-1": "in_progress"}, "1") == "IN_PROGRESS"
    assert core.phase_state({"1": "completed", "phase-1": "in_progress"}, "1") == "DONE"
    assert core.phase_state({"phase-1-build-implement": "completed", "phase-1-build-review": "completed"},
                            "1") == "DONE"
    # guard: phase-10- never affects phase 1
    assert core.phase_state({"phase-10-implement": "failed"}, "1") == "PENDING"
    assert core.phase_state({"phase-10": "failed"}, "1") == "PENDING"
    # real shape 1: ids 0..6, cursor 1, phase-0 completed, phase-1 in_progress
    real1 = wf(phase_cursor=1, normalized_phases=[{"id": i} for i in range(0, 7)],
               phase_status={"phase-0": "completed", "phase-1": "in_progress"})
    idx, how = core.resolve_cursor(real1["phase_cursor"], real1["phases"], "finished_count")
    assert (idx, how) == (1, "id_match"), (idx, how)
    assert core.phase_state(real1["phase_status"], real1["phases"][idx]["id"]) == "IN_PROGRESS"
    assert core.phase_state(real1["phase_status"], "0") == "DONE"
    # real shape 2: phase_id 1..7, cursor 7, phase-N-build-* completed for 1..6, nothing for 7
    keys = {}
    for n in range(1, 7):
        keys["phase-%d-build-implement" % n] = "completed"
        keys["phase-%d-build-review" % n] = "completed"
    real2 = wf(phase_cursor=7, normalized_phases=[{"phase_id": i} for i in range(1, 8)], phase_status=keys)
    idx, how = core.resolve_cursor(7, real2["phases"], "finished_count")
    assert (idx, how) == (6, "id_match"), (idx, how)
    assert [core.phase_state(keys, str(n)) for n in range(1, 8)] == ["DONE"] * 6 + ["PENDING"]


# ---------------------------------------------------------------------------
# Task 2.3: hard rules
# ---------------------------------------------------------------------------

def payload(**kw):
    """A clean BUILD artifact payload: cursor P2 pending, P1 completed."""
    base = {"workflow_type": "BUILD", "plan_file": "docs/plans/x.md", "phase_cursor": "P2",
            "phase_status": {"P1": "completed", "P2": "pending"},
            "normalized_phases": [phase("P1", files=["a.py"]), phase("P2", files=["b.py"])]}
    base.update(kw)
    return base


def facts_for(payload_kw=None, **kw):
    work = core.workflow_facts(payload(**(payload_kw or {})), lambda p: True)
    return core.base_facts(workflow=work, **kw)


def rules(payload_kw=None, **kw):
    return core.hard_rules(facts_for(payload_kw, **kw))


def test_hard_rules_each_rule_fires_alone():
    assert core.hard_rules(core.base_facts()) == [], core.hard_rules(core.base_facts())
    assert rules() == []
    git = {"ok": True, "in_progress_op": None, "unmerged": False, "dirty_paths": [], "head": "0" * 40}
    two_status = {"P1": "completed", "P2": "pending"}
    table = (
        ("H00_message_missing", {}, {"message": ""}),
        ("H00_message_missing", {}, {"message": None}),
        ("H01_no_bound_workflow", {}, {"binding_reason": "ambiguous"}),
        ("H01_no_bound_workflow", {}, {"binding_reason": "no_mention"}),
        ("H01_no_bound_workflow", {}, {"workflow": None}),
        ("H02_not_build_or_no_plan", {"workflow_type": "PLAN"}, {}),
        ("H02_not_build_or_no_plan", {"plan_file": ""}, {}),
        ("H03_pending_gate", {"pending_gate": "user_build_approval"}, {}),
        ("H03_pending_gate", {"pending_gate": {"kind": "scope"}}, {}),
        ("H04_open_decisions", {"intent": {"open_decisions": ["q"]}}, {}),
        ("H05_phase_status_not_clean", {"phase_status": {"P1": "in_progress", "P2": "pending"}}, {}),
        ("H05_phase_status_not_clean", {"phase_status": dict(two_status, remfix="failed")}, {}),
        ("H06_circuit_breaker", {"circuit_breaker": {"broken": True}}, {}),
        ("H07_failure_or_revert_event", {"status_history": [{"event": "Phase_Reverted"}]}, {}),
        ("H08_workflow_terminal", {"worktree_mode": "merged_and_removed"}, {}),
        ("H09_no_next_phase", {"phase_cursor": "zzz"}, {}),
        ("H09_no_next_phase", {"phase_cursor": 2}, {}),  # finished_count == len(phases)
        ("H10_next_phase_outward",
         {"normalized_phases": [phase("P1"), phase("P2", title="Open the PR")]}, {}),
        ("H10_next_phase_outward",
         {"normalized_phases": [phase("P1"), phase("P2", checks=["npm publish works"])]}, {}),
        ("H11_git_unsafe", {}, {"git": dict(git, ok=False)}),
        ("H11_git_unsafe", {}, {"git": dict(git, in_progress_op="merge")}),
        ("H11_git_unsafe", {}, {"git": dict(git, unmerged=True)}),
        ("H12_secret_signal", {}, {"message": "export API_KEY=abc123 now"}),
        ("H12_secret_signal", {}, {"git": dict(git, dirty_paths=["src/a.py", "config/.env.local"])}),
        ("H13_permission_mode_plan", {}, {"permission_mode": "plan"}),
        ("H14_question_needs_choice", {}, {"message": "A) use a queue\nB) use a thread"}),
        ("H15_outward_request", {}, {"message": "Want me to push and open the PR?"}),
    )
    for code, payload_kw, kw in table:
        if "workflow" in kw:
            got = core.hard_rules(core.base_facts(**kw))
        else:
            got = rules(payload_kw, **kw)
        # H05 on a non-phase key and H09 on a cursor-less workflow are the only ones allowed beside it
        assert got == [code], (code, payload_kw, kw, got)
    # a cursor on an in_progress phase is a hit, together with H09 (nothing pending is next)
    got = rules({"phase_status": {"P1": "completed", "P2": "in_progress"}})
    assert got == ["H05_phase_status_not_clean", "H09_no_next_phase"], got
    # both H09 cursor cases are NON-hits
    assert core.next_phase(facts_for())["case"] == "at_next"
    on_done = facts_for({"phase_cursor": "P1"})
    assert core.next_phase(on_done)["case"] == "on_completed" and core.hard_rules(on_done) == []
    # non-phase pending/in_progress keys are ignored
    assert rules({"phase_status": dict(two_status, **{"memory-finalize": "pending", "review": "in_progress"})}) == []
    # integer cursor that matches no id: H10 checks BOTH candidates under both meanings
    outward = [phase("a"), phase("b", title="Deploy to prod"), phase("c")]
    for meaning in MEANINGS:
        settings = dict(core.DEFAULTS, intCursorMeaning=meaning, jevText=False, jev_text_source="off")
        got = rules({"phase_cursor": 1, "phase_status": {}, "normalized_phases": outward}, settings=settings)
        assert got == ["H10_next_phase_outward"], (meaning, got)
    # real artifact shape 1: ids 0..6, cursor 1, phase-1 in_progress
    real1 = {"phase_cursor": 1, "normalized_phases": [{"id": i} for i in range(0, 7)],
             "phase_status": {"phase-0": "completed", "phase-1": "in_progress"}}
    f1 = facts_for(real1)
    nxt = core.next_phase(f1)
    assert nxt["resolution"] == "id_match" and nxt["case"] == "none", nxt
    assert "H05_phase_status_not_clean" in core.hard_rules(f1), core.hard_rules(f1)
    # real artifact shape 2: phase_id 1..7, cursor 7, phase-1..6-build-* completed, no phase-7 key
    keys = {}
    for n in range(1, 7):
        keys["phase-%d-build-implement" % n] = "completed"
        keys["phase-%d-build-review" % n] = "completed"
    f2 = facts_for({"phase_cursor": 7, "normalized_phases": [{"phase_id": i} for i in range(1, 8)],
                    "phase_status": keys})
    nxt = core.next_phase(f2)
    assert nxt["case"] == "at_next" and nxt["resolution"] == "id_match", nxt
    assert core.hard_rules(f2) == [], core.hard_rules(f2)
    # guard: a failed phase-10- sub-step is a NON-phase failure here (H05), and never marks phase 1
    f3 = facts_for({"phase_cursor": 1, "normalized_phases": [{"id": i} for i in range(1, 4)],
                    "phase_status": {"phase-10-implement": "failed"}})
    assert core.phase_state(f3["workflow"]["phase_status"], "1") == "PENDING"


def test_hard_rules_order_and_all_hits_collected():
    got = rules({"pending_gate": "g", "workflow_type": "DEBUG"}, message="", permission_mode="plan",
                binding_reason="ambiguous")
    expected = ["H00_message_missing", "H01_no_bound_workflow", "H02_not_build_or_no_plan",
                "H03_pending_gate", "H13_permission_mode_plan"]
    assert got == expected, got
    assert got == sorted(got, key=lambda c: int(c[1:3])), got
    # every rule at once, in order
    git = {"ok": False, "in_progress_op": "rebase", "unmerged": True, "dirty_paths": ["id_rsa"], "head": "0" * 40}
    got = rules({"pending_gate": "g", "workflow_type": "DEBUG", "phase_cursor": "zzz",
                 "intent": {"open_decisions": ["q"]}, "circuit_breaker": {"broken": True},
                 "status_history": [{"event": "failed"}], "phase_status": {"P1": "failed"},
                 "worktree_mode": "merged"},
                message="", permission_mode="plan", binding_reason="ambiguous", git=git)
    assert [c[:3] for c in got] == ["H00", "H01", "H02", "H03", "H04", "H05", "H06", "H07", "H08",
                                    "H09", "H11", "H12", "H13"], got


def test_phase_status_vocab_done_pending_other():
    done = ("completed", "complete", "verified_passed", "verified_pass", "verified", "passed", "COMPLETED",
            {"status": "completed"})
    pending = ("pending", "not_started", "queued", "Pending")
    other = ("in_progress", "builder_complete", "merged", "weird", "", {"status": "builder_complete"}, {}, None)
    failed = ("failed", "blocked", "needs_human", "gap_found", "reverted", "remediation", "error",
              "aborted", {"status": "failed"})
    for value in done:
        assert core.normalize_phase_status(value) == "DONE", value
        assert rules({"phase_status": {"P1": value, "P2": "pending"}}) == [], value
    for value in pending:
        assert core.normalize_phase_status(value) == "PENDING", value
        assert rules({"phase_status": {"P1": value, "P2": "pending"}}) == [], value
    for value in other:
        assert core.normalize_phase_status(value) == "IN_PROGRESS", value
        got = rules({"phase_status": {"P1": value, "P2": "pending"}})
        assert got == ["H05_phase_status_not_clean"], (value, got)
    for value in failed:
        assert core.normalize_phase_status(value) == "FAILED", value
        got = rules({"phase_status": {"P1": value, "P2": "pending"}})
        assert got == ["H05_phase_status_not_clean"], (value, got)
    # on a NON-phase key only a failure value hits
    assert rules({"phase_status": {"P1": "completed", "P2": "pending", "extra": "builder_complete"}}) == []
    assert rules({"phase_status": {"P1": "completed", "P2": "pending", "extra": "blocked"}}) == [
        "H05_phase_status_not_clean"]


def test_pending_gate_empty_forms_not_hit():
    for empty in (None, "", "none", "NULL", "n/a", "false", "-", {"kind": "none"}, {}):
        assert rules({"pending_gate": empty}) == [], empty
    for real in ("user_build_approval", {"kind": "scope_gate"}, "yes"):
        assert rules({"pending_gate": real}) == ["H03_pending_gate"], real


def test_outward_re_matches_and_non_matches():
    for text in ("Open the PR", "merge worktree", "npm publish", "Push to origin", "gh pr create",
                 "deploy it", "cut a release", "git tag v1", "force push"):
        assert core.OUTWARD_RE.search(text), text
    for text in ("Implement parser", "emerge", "pushing aside", "attagged", "Continue to Phase 3"):
        assert not core.OUTWARD_RE.search(text), text


def test_choice_re():
    for text in ("A) use a queue\nB) use a thread", "- 1. first\n- 2. second", "Option A: x\nOption B: y",
                 "Which approach should I take?", "Do you prefer tabs or spaces?",
                 "Would you rather keep it or drop it?", "which one do you want"):
        assert core.has_choice(text), text
    for text in ("Continue to Phase 3?", "A) only one option line", "Phase 2 is done. Shall I continue?",
                 "", None):
        assert not core.has_choice(text), text


def test_secret_text_and_secret_paths():
    for text in ("-----BEGIN RSA PRIVATE KEY-----\nabc", "key AKIAABCDEFGHIJKLMNOP end",
                 "token ghp_" + "a" * 24, "API_KEY=abc", "db_password = hunter2", "sk-" + "b" * 24,
                 "xoxb-12345", "export GITHUB_TOKEN=x"):
        assert core.has_secret_text(text), text
    for text in ("Phase 2 done.", "the token is explained in docs", "API key rotation is out of scope", "",
                 None):
        assert not core.has_secret_text(text), text
    for path in (".env.local", ".env", "src/id_rsa.pub", "certs/cert.pem", "a/b/server.key", "x.p12",
                 "deep/.ENV"):
        assert core.secret_path_hit(path), path
    for path in ("docs/env.md", "src/keyboard.py", "README.md", "id_rsa_notes_dir/readme.txt"):
        assert not core.secret_path_hit(path), path


def test_commit_blockers_table():
    def blockers(payload_kw=None, dirty=None, **kw):
        git = {"ok": True, "in_progress_op": None, "unmerged": False, "dirty_paths": dirty or [],
               "head": "0" * 40}
        return core.commit_blockers(facts_for(payload_kw, git=git, **kw))

    assert blockers() == ["C1_no_dirty_paths"]  # nothing to commit
    empty = {"normalized_phases": [phase("P1"), phase("P2")]}
    assert blockers(empty, ["a.py"]) == ["C2_empty_plan_file_union"]
    assert blockers(empty) == ["C1_no_dirty_paths", "C2_empty_plan_file_union"]
    assert blockers(None, ["z.py"]) == ["C3_dirty_outside_plan"]
    assert blockers(None, ["a.py", "b.py"]) == []
    assert blockers({"normalized_phases": [phase("P1", files=["src/"]), phase("P2")]}, ["src/x/y.py"]) == []
    assert blockers({"normalized_phases": [phase("P1", files=["src"]), phase("P2")]}, ["srcx/y.py"]) == [
        "C3_dirty_outside_plan"]
    # file-key aliases feed the union
    for key in ("files_surfaces", "files/surfaces"):
        aliased = {"normalized_phases": [{"phase_id": "P1", key: ["a.py"]}, {"phase_id": "P2", key: ["b.py"]}]}
        assert blockers(aliased, ["a.py"]) == [], key
    # a phase that is neither DONE nor current does not contribute files
    later = {"normalized_phases": [phase("P1", files=["a.py"]), phase("P2", files=["b.py"]),
                                   phase("P3", files=["c.py"])]}
    assert blockers(later, ["c.py"]) == ["C3_dirty_outside_plan"]
    # commit blockers never change hard_rules()
    clean = facts_for()
    without = core.hard_rules(clean)
    with_blockers = dict(clean, commit_blockers=["C1_no_dirty_paths", "C2_empty_plan_file_union"])
    assert core.hard_rules(with_blockers) == without == []
    # the exit-criteria example: gate set, P2 in progress, P3 outward, empty file union
    work = core.workflow_facts({
        "workflow_type": "BUILD", "plan_file": "p.md", "phase_cursor": "P1", "phase_status": {"P1": "pending"},
        "normalized_phases": [phase("P1", title="x")]}, lambda p: True)
    assert core.commit_blockers(core.base_facts(workflow=work)) == ["C1_no_dirty_paths", "C2_empty_plan_file_union"]


# ---------------------------------------------------------------------------
# Task 3.1: heuristic taxonomy and verdict combiner
# ---------------------------------------------------------------------------

HEURISTIC_TABLE = (
    ("phase_done_awaiting_continue", (
        "Phase 2 is done and all checks pass. Shall I continue with Phase 3?",
        "Ready for the next phase?",
        "Parser is finished and tests are green. Want me to proceed to Phase 2?",
        "Continue to Phase 3?")),
    ("awaiting_commit", (
        "Should I commit these changes?",
        "Everything is staged. Want me to commit?",
        "Ready to commit the formatter work?")),
    ("awaiting_push_or_pr", (
        "Want me to push and open the PR?",
        "Shall I merge the branch?",
        "Should I publish this release?")),
    ("asking_decision", (
        "Should I use option A or option B?",
        "Which approach do you prefer?",
        "Do you want the parser strict or lenient?\nA) strict\nB) lenient")),
    ("blocked_by_error", (
        "Tests fail with ImportError: no module named foo.",
        "The build failed and I cannot proceed without the missing credentials.",
        "I am blocked: the migration errors out on step 3.")),
    ("work_complete", (
        "All done. Summary: the formatter now handles tabs.",
        "All phases are complete. Summary of changes below.",
        "The work is complete.")),
)


def test_heuristic_kind_table():
    for kind, phrases in HEURISTIC_TABLE:
        assert len(phrases) >= 3, kind
        for text in phrases:
            got, signals = core.heuristic_kind(text)
            assert got == kind, (kind, text, got)
            assert isinstance(signals, list) and signals, (text, signals)
    # only the last 600 chars count
    long_text = "Should I commit these changes?" + (" filler" * 200) + "\nNothing else to say."
    assert core.heuristic_kind(long_text)[0] == "other"
    assert core.heuristic_kind("x" * 1000 + " Ready for the next phase?")[0] == "phase_done_awaiting_continue"
    # CHOICE_RE / OUTWARD_RE take precedence over a continue phrasing
    assert core.heuristic_kind("Phase 1 done. Continue, or push first?")[0] == "awaiting_push_or_pr"
    assert core.heuristic_kind("Phase 1 done. Which approach do you want for Phase 2, shall I continue?")[0] \
        == "asking_decision"


def test_heuristic_other_on_empty():
    for text in ("", "   ", None, 7, "Thanks."):
        assert core.heuristic_kind(text) == ("other", []), text
    assert set(core.STOP_KINDS) == {k for k, _ in HEURISTIC_TABLE} | {"other"}


def decide_inputs(**kw):
    args = {"rule_hits": [], "commit_blockers": [], "heuristic": "phase_done_awaiting_continue",
            "jev": None, "settings": core.parse_settings(None, None)[0]}
    args.update(kw)
    return args


def run_decide(**kw):
    a = decide_inputs(**kw)
    return core.decide(a["rule_hits"], a["commit_blockers"], a["heuristic"], a["jev"], a["settings"])


def jev_ok(kind="phase_done_awaiting_continue", conf=0.95, needs=0.05):
    return {"status": "ok", "kind": kind, "kind_conf": conf, "needs_human": needs}


def test_decide_rules_dominate_any_classifier():
    import random
    rnd = random.Random(55)
    codes = ["H%02d_x" % i for i in range(16)]
    statuses = ("ok", "timeout", "http_error", "failed", "malformed", "not_consented", "inactive")
    for _ in range(2000):
        hits = rnd.sample(codes, rnd.randint(1, 5))
        jev = None if rnd.random() < 0.3 else {
            "status": rnd.choice(statuses), "kind": rnd.choice(core.STOP_KINDS),
            "kind_conf": rnd.random(), "needs_human": rnd.random()}
        blockers = rnd.sample(["C1_no_dirty_paths", "C2_empty_plan_file_union"], rnd.randint(0, 2))
        got = run_decide(rule_hits=hits, commit_blockers=blockers, heuristic=rnd.choice(core.STOP_KINDS), jev=jev)
        assert got["verdict"] == "needs_human" and got["verdict_source"] == "hard_rule", (hits, jev, got)
        assert got["act_eligible"] is False and got["would_action"] == "none", got
        assert got["reasons"] == hits, got


def test_decide_commit_requires_no_commit_blockers():
    commit = run_decide(heuristic="awaiting_commit", commit_blockers=[])
    assert (commit["verdict"], commit["would_action"], commit["verdict_source"]) == (
        "would_commit", "commit", "heuristic"), commit
    blocked = run_decide(heuristic="awaiting_commit", commit_blockers=["C1_no_dirty_paths"])
    assert blocked["verdict"] == "needs_human" and blocked["would_action"] == "none", blocked
    cont = run_decide(heuristic="phase_done_awaiting_continue", commit_blockers=["C1_no_dirty_paths"])
    assert cont["verdict"] == "would_continue" and cont["would_action"] == "continue", cont
    jev_commit = run_decide(jev=jev_ok("awaiting_commit"), commit_blockers=[])
    assert (jev_commit["verdict"], jev_commit["verdict_source"]) == ("would_commit", "jev"), jev_commit
    jev_blocked = run_decide(jev=jev_ok("awaiting_commit"), commit_blockers=["C3_dirty_outside_plan"])
    assert jev_blocked["verdict"] == "needs_human", jev_blocked
    verdicts = set()
    for kind in core.STOP_KINDS:
        for blockers in ([], ["C1_no_dirty_paths"]):
            verdicts.add(run_decide(heuristic=kind, commit_blockers=blockers)["verdict"])
            verdicts.add(run_decide(jev=jev_ok(kind), commit_blockers=blockers)["verdict"])
    assert verdicts <= {"needs_human", "would_continue", "would_commit"} and "done" not in verdicts, verdicts


def test_decide_jev_thresholds_boundaries():
    at = run_decide(jev=jev_ok(conf=0.90, needs=0.20), heuristic="asking_decision")
    assert (at["verdict"], at["verdict_source"], at["act_eligible"]) == ("would_continue", "jev", True), at
    for conf, needs in ((0.8999, 0.05), (0.95, 0.2001)):
        got = run_decide(jev=jev_ok(conf=conf, needs=needs), heuristic="phase_done_awaiting_continue")
        assert got["verdict"] == "needs_human" and got["verdict_source"] == "jev", (conf, needs, got)
        assert got["act_eligible"] is False, got
    other = run_decide(jev=jev_ok("asking_decision"), heuristic="phase_done_awaiting_continue")
    assert other["verdict"] == "needs_human" and other["verdict_source"] == "jev", other
    custom = core.parse_settings(None, {"jevKindThreshold": 0.7, "jevNeedsHumanMax": 0.4}, "passwd")[0]
    got = run_decide(jev=jev_ok(conf=0.7, needs=0.4), settings=custom)
    assert got["verdict"] == "would_continue", got


def test_decide_jev_missing_uses_heuristic_not_act_eligible():
    for jev in (None, {"status": "timeout"}, {"status": "http_error"}, {"status": "malformed"},
                {"status": "inactive"}, {"status": "not_consented"}):
        got = run_decide(jev=jev, heuristic="phase_done_awaiting_continue")
        assert got["verdict"] == "would_continue" and got["verdict_source"] == "heuristic", (jev, got)
        assert got["act_eligible"] is False and got["would_action"] == "continue", got
    none = run_decide(heuristic="work_complete")
    assert none["verdict"] == "needs_human" and none["act_eligible"] is False, none
    assert set(none) >= {"verdict", "verdict_source", "act_eligible", "would_action", "reasons"}, none


# ---------------------------------------------------------------------------
# Task 3.3: notify text, relay, loop guards, session math, shadow row
# ---------------------------------------------------------------------------

def test_notify_text_allowlist_and_shape():
    import random
    text = core.notify_text("needs_human", "wf-approved-plan-12345678", "P3", ["H03_pending_gate", "H05_x"])
    assert text == "Craftflow: needs you - 12345678 P3 (H03_pending_gate)", text
    assert core.notify_text("needs_human", None, None, []) == "Craftflow: needs you"
    assert "%s" in core.PUSH_RELAY_TEMPLATE and core.PUSH_RELAY_TEMPLATE.count("%") == 1
    assert (core.PUSH_RELAY_TEMPLATE % text).count(text) == 1
    rnd = random.Random(7)
    hostile = ["\n", "'", '"', "</x>", "\r\n", "é中", "$(rm -rf /)", "%s", "EVIL\n"]
    for _ in range(300):
        junk = "".join(rnd.choice(hostile) for _ in range(rnd.randint(1, 30)))
        big = junk * rnd.choice((1, 400))
        wf_id = rnd.choice(["wf-" + big, big, "wf-ok-" + big[:12] + "abcdefgh"])
        out = core.notify_text("needs_human", wf_id, rnd.choice([big, "P1" + big, "P" + junk]),
                               [rnd.choice([big, "H01_" + big, "H01_no_bound_workflow"])])
        assert out.isascii() and "\n" not in out and "\r" not in out and len(out) <= 180, repr(out)
        assert out.startswith("Craftflow: "), out
        for bad in ("EVIL", "</x>", "'", '"', "$(", "%"):
            assert bad not in out, (bad, out)
    ten_kb = core.notify_text("needs_human", "wf-" + "a" * 10000, "P" + "9" * 10000, ["H01_" + "x" * 10000])
    assert len(ten_kb) <= 180 and ten_kb == "Craftflow: needs you", ten_kb


def relay_args(**kw):
    args = {"settings": {"notify": "push"}, "verdict": "needs_human", "stop_hook_active": False,
            "session_state": {}, "tail_sha": "t1"}
    args.update(kw)
    return args


def test_relay_decision_never_when_stop_hook_active():
    assert core.relay_decision(**relay_args()) is True
    for notify in ("off", "desktop", "push"):
        for verdict in ("needs_human", "would_continue", "would_commit", None):
            for pending in (False, True):
                for sha in ("t1", "t2", None):
                    got = core.relay_decision(
                        {"notify": notify}, verdict, True, {"relay_pending": pending}, sha)
                    assert got is False, (notify, verdict, pending, sha)
    assert core.relay_decision(**relay_args(settings={"notify": "desktop"})) is False
    assert core.relay_decision(**relay_args(settings={"notify": "off"})) is False
    assert core.relay_decision(**relay_args(verdict="would_continue")) is False
    assert core.relay_decision(**relay_args(session_state={"relay_pending": True})) is False
    assert core.relay_decision(**relay_args(session_state=None)) is True


def test_relay_decision_once_per_tail_sha():
    state = core.session_update({}, "needs_human", "h", "P1", "t1", True, 10.0)
    assert state["relay_pending"] is True and state["last_notified_tail_sha"] == "t1", state
    done = dict(state, relay_pending=False)  # the follow-up stop cleared the pending flag
    assert core.relay_decision(**relay_args(session_state=done, tail_sha="t1")) is False
    # R3 (stale requirement, tightened): a new tail alone no longer re-arms a relay, a new human turn does
    assert core.relay_decision(**relay_args(session_state=done, tail_sha="t2")) is False
    assert core.relay_decision(**relay_args(session_state=done, tail_sha="t2", last_human_ts="B")) is True
    assert core.relay_decision(**relay_args(session_state=state, tail_sha="t2", last_human_ts="B")) is False


def loop_facts(head="h1", cursor="P2", active=True, human="A"):
    git = {"ok": True, "in_progress_op": None, "unmerged": False, "dirty_paths": [], "head": head}
    return dict(facts_for({"phase_cursor": cursor}, stop_hook_active=active, git=git), last_human_ts=human)


def test_loop_guards_and_session_update():
    state = {}
    for n in range(1, 6):
        state = core.session_update(state, "would_continue", "h1", "P2", "t%d" % n, False, 100.0 + n,
                                    last_human_ts="A")
        assert state["would_continue_since_human"] == n, state
    before = dict(state)
    assert core.session_update(state, "needs_human", "h1", "P2", "t", False, 1.0,
                               last_human_ts="A")["would_continue_since_human"] == 5
    assert state == before  # session_update never mutates its input
    reset = core.session_update(state, "needs_human", "h1", "P2", "t", False, 1.0, last_human_ts="B")
    assert reset["would_continue_since_human"] == 0 and reset["last_human_ts"] == "B", reset
    again = core.session_update(state, "would_continue", "h1", "P2", "t", False, 1.0, last_human_ts="B")
    assert again["would_continue_since_human"] == 1, again
    assert (reset["last_head"], reset["last_cursor"], reset["updated_at"]) == ("h1", "P2", 1.0), reset
    # L2: a tag at count 5 (== maxAutoContinuesPerSession), never at 4, never a hard rule, no verdict effect
    facts = loop_facts(active=False)
    assert "L2_budget" in core.loop_guards(facts, state)
    four = dict(state, would_continue_since_human=4)
    assert core.loop_guards(facts, four) == []
    assert core.loop_guards(loop_facts(active=False, human="C"), state) == []  # a new human turn resets
    assert not any(code.startswith("L") for code in core.hard_rules(facts))
    base = run_decide(heuristic="phase_done_awaiting_continue")
    assert base["verdict"] == "would_continue" and "L2_budget" not in base["reasons"], base
    # L1: needs stop_hook_active, a recorded continue or pending relay, and unchanged HEAD and cursor
    recorded = {"would_continue_since_human": 1, "last_human_ts": "A", "last_head": "h1", "last_cursor": "P2"}
    assert core.loop_guards(loop_facts(), recorded) == ["L1_no_progress"]
    assert core.loop_guards(loop_facts(active=False), recorded) == []
    assert core.loop_guards(loop_facts(head="h2"), recorded) == []
    assert core.loop_guards(loop_facts(cursor="P1"), recorded) == []
    pending = {"relay_pending": True, "last_human_ts": "A", "last_head": "h1", "last_cursor": "P2"}
    assert core.loop_guards(loop_facts(), pending) == ["L1_no_progress"]
    assert core.loop_guards(loop_facts(), {"last_human_ts": "A", "last_head": "h1", "last_cursor": "P2"}) == []
    assert core.loop_guards(loop_facts(), {}) == [] and core.loop_guards({}, None) == []


EXPECTED_ROW_KEYS = (
    "schema", "row_kind", "ts", "session_id", "transcript_path", "mode", "mode_tag", "stop_hook_active",
    "permission_mode", "wf", "binding_reason", "phase_cursor", "cursor_case", "cursor_resolution", "rule_hits",
    "loop_guards", "commit_blockers", "jev_text_source", "last_human_ts", "heuristic_kind", "heuristic_verdict",
    "jev_status", "jev_kind", "jev_kind_conf", "jev_needs_human", "jev_latency_ms", "jev_usage", "verdict",
    "verdict_source", "act_eligible", "would_action", "notify", "notify_status", "turn_seconds",
    "turn_seconds_lower_bound", "message_chars", "tail_sha", "hook_ms", "settings_tags")


def test_build_row_key_set_frozen():
    assert tuple(core.ROW_KEYS) == EXPECTED_ROW_KEYS, core.ROW_KEYS
    row = core.build_row()
    assert tuple(row) == EXPECTED_ROW_KEYS, tuple(row)
    assert row["schema"] == 1 and row["row_kind"] == "stop", row
    assert row["rule_hits"] == [] and row["loop_guards"] == [] and row["commit_blockers"] == [], row
    assert row["act_eligible"] is False and row["would_action"] == "none", row
    full = core.build_row(row_kind="relay_followup", verdict=None, wf="wf-a", rule_hits=["H01_x"],
                          jev_usage={"tokens": 3}, unknown_key="dropped")
    assert tuple(full) == EXPECTED_ROW_KEYS and "unknown_key" not in full, full
    assert full["row_kind"] == "relay_followup" and full["rule_hits"] == ["H01_x"], full
    assert full["jev_usage"] == {"tokens": 3}, full
    json.dumps(full)  # serialisable


def test_build_row_never_contains_message_marker():
    import random
    rnd = random.Random(99)
    for i in range(200):
        marker = "MSGMARK%06d" % rnd.randint(0, 999999)
        message = "x" * rnd.randint(0, 50) + marker + "y" * rnd.randint(0, 50) + "\nOption A\nOption B"
        facts = facts_for(message=message)
        kind, signals = core.heuristic_kind(message)
        hits = core.hard_rules(facts)
        verdict = core.decide(hits, core.commit_blockers(facts), kind, None, facts["settings"])
        row = core.build_row(
            message=message, last_assistant_message=message, text=message, tail=message,
            row_kind="stop", rule_hits=hits, commit_blockers=core.commit_blockers(facts),
            loop_guards=core.loop_guards(facts, {}), heuristic_kind=kind, heuristic_verdict=verdict["verdict"],
            verdict=verdict["verdict"], verdict_source=verdict["verdict_source"],
            act_eligible=verdict["act_eligible"], would_action=verdict["would_action"],
            message_chars=len(message), stop_hook_active=False, mode="audit")
        blob = json.dumps(row, ensure_ascii=True)
        assert marker not in blob and "Option A" not in blob, (i, blob)
        assert row["message_chars"] == len(message), row
    long_value = core.build_row(wf="wf-" + "z" * 5000, tail_sha="s" * 5000)
    assert len(long_value["wf"]) <= 200 and len(long_value["tail_sha"]) <= 200, long_value


# ---------------------------------------------------------------------------
# Hook shell (phase 4): subprocess tests through run_gate
# ---------------------------------------------------------------------------

GATE = SCRIPTS / "craftflow_stop_gate.py"
WF_ID = "wf-sg-0001"
CONTINUE_TEXT = "Phase 1 done, checks pass. Continue to Phase 2?"
ISO = "%Y-%m-%dT%H:%M:%S.000Z"


def iso_ago(seconds):
    return time.strftime(ISO, time.gmtime(time.time() - seconds))


def git(root, *args):
    cmd = ["git", "-C", str(root), "-c", "user.email=t@example.com", "-c", "user.name=t",
           "-c", "commit.gpgsign=false"] + list(args)
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout


def make_project():
    """A clean scratch git repo whose committed .gitignore hides .craftflow/ (A10)."""
    root = Path(scratch_dir())
    git(root, "init", "-q")
    (root / ".gitignore").write_text(".craftflow/\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "commit", "-q", "-m", "init")
    return root


def seed_artifact(root, wf=WF_ID, **overrides):
    payload = {
        "workflow_type": "BUILD", "plan_file": "docs/plans/x.md", "phase_cursor": "P2",
        "phase_status": {"P1": "completed", "P2": "pending"},
        "normalized_phases": [{"phase_id": "P1", "title": "Parser"}, {"phase_id": "P2", "title": "Formatter"}],
        "pending_gate": None,
    }
    payload.update(overrides)
    folder = root / ".craftflow" / "state" / "workflows"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (wf + ".json")
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def user_line(content, ts, **extra):
    row = {"type": "user", "timestamp": ts, "message": {"role": "user", "content": content}}
    row.update(extra)
    return row


def assistant_line(text, ts):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def write_transcript(root, lines, name="t.jsonl"):
    path = Path(scratch_dir()) / name
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


def mention_transcript(root, wf=WF_ID, extra=()):
    lines = [user_line("start " + wf, iso_ago(600)), assistant_line(CONTINUE_TEXT, iso_ago(60))]
    return write_transcript(root, lines + list(extra))


def audit_config():
    path = Path(scratch_dir()) / "user-stop-gate.json"
    path.write_text(json.dumps({"mode": "audit"}), encoding="utf-8")
    return path


def run_gate(payload, root, env_extra=None, raw=None, timeout=20, script=None):
    """Run the hook as a subprocess. Returns (code, stdout, stderr, elapsed)."""
    env = dict(os.environ)
    env.pop("CURSOR_PLUGIN_ROOT", None)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    for key, value in (env_extra or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    stdin = raw if raw is not None else json.dumps(payload).encode("utf-8")
    started = time.monotonic()
    done = subprocess.run([sys.executable, str(script or GATE)], input=stdin, capture_output=True, env=env,
                          cwd=str(root), timeout=timeout)
    return (done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace"),
            time.monotonic() - started)


def stop_payload(transcript, root, message=CONTINUE_TEXT, **extra):
    payload = {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(transcript),
               "cwd": str(root), "permission_mode": "default", "stop_hook_active": False}
    if message is not None:
        payload["last_assistant_message"] = message
    payload.update(extra)
    return payload


def rows_of(root):
    path = Path(root) / ".craftflow" / "state" / "stop-gate" / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def audit_env():
    return {"CRAFTFLOW_STOP_GATE_USER_CONFIG": str(audit_config())}


def one_row(root, payload, env_extra=None, **kw):
    env = audit_env()
    env.update(env_extra or {})
    code, out, err, _elapsed = run_gate(payload, root, env, **kw)
    assert code == 0, (code, err)
    assert out == "", out
    rows = rows_of(root)
    assert len(rows) >= 1, "no row written; stderr=" + err
    return rows[-1]


def test_hook_off_is_inert():
    root = make_project()
    seed_artifact(root)
    t = mention_transcript(root)
    code, out, err, _ = run_gate(stop_payload(t, root), root)
    assert (code, out) == (0, ""), (code, out, err)
    assert not (root / ".craftflow" / "state" / "stop-gate").exists()


def test_hook_off_fifo_transcript_does_not_block():
    root = make_project()
    fifo = Path(scratch_dir()) / "t.fifo"
    os.mkfifo(str(fifo))
    code, out, err, elapsed = run_gate(stop_payload(fifo, root), root, timeout=3)
    assert (code, out) == (0, ""), (code, out, err)
    assert elapsed < 3, elapsed
    assert not (root / ".craftflow" / "state" / "stop-gate").exists()


def test_hook_non_stop_event_noop():
    root = make_project()
    seed_artifact(root)
    t = mention_transcript(root)
    for name in ("SubagentStop", "stop", "", None):
        payload = stop_payload(t, root)
        if name is None:
            payload.pop("hook_event_name")
        else:
            payload["hook_event_name"] = name
        code, out, err, _ = run_gate(payload, root, audit_env())
        assert (code, out) == (0, ""), (name, code, out, err)
    assert rows_of(root) == []


def test_hook_cursor_env_noop():
    root = make_project()
    seed_artifact(root)
    t = mention_transcript(root)
    env = audit_env()
    env["CURSOR_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    code, out, err, _ = run_gate(stop_payload(t, root), root, env)
    assert (code, out) == (0, ""), (code, out, err)
    assert rows_of(root) == []


def test_hook_bad_stdin_exit0():
    root = make_project()
    for raw in (b"\xff\xfe not json", b"[1, 2, 3]", b"", b'{"hook_event_name": "Stop"'):
        code, out, err, _ = run_gate(None, root, audit_env(), raw=raw)
        assert (code, out) == (0, ""), (raw, code, out, err)
    assert rows_of(root) == []


def test_hook_audit_would_continue_row():
    root = make_project()
    seed_artifact(root)
    t = mention_transcript(root)
    row = one_row(root, stop_payload(t, root))
    assert tuple(row) == core.ROW_KEYS or set(row) == set(core.ROW_KEYS), sorted(set(row) ^ set(core.ROW_KEYS))
    assert row["row_kind"] == "stop" and row["mode"] == "audit", row
    assert row["verdict"] == "would_continue" and row["verdict_source"] == "heuristic", row
    assert row["act_eligible"] is False and row["rule_hits"] == [], row
    assert row["cursor_case"] == "at_next" and row["binding_reason"] == "single_candidate", row
    assert row["commit_blockers"] == ["C1_no_dirty_paths", "C2_empty_plan_file_union"], row
    assert row["wf"] == WF_ID and row["session_id"] == "s1", row
    assert row["message_chars"] == len(CONTINUE_TEXT) and row["hook_ms"] >= 0, row
    assert "Continue to Phase 2" not in json.dumps(row), "message text leaked into the row"
    # variant: cursor still on the completed phase
    root2 = make_project()
    seed_artifact(root2, phase_cursor="P1")
    t2 = mention_transcript(root2)
    row2 = one_row(root2, stop_payload(t2, root2))
    assert row2["verdict"] == "would_continue" and row2["cursor_case"] == "on_completed", row2


def test_hook_audit_pending_gate_row():
    root = make_project()
    seed_artifact(root, pending_gate="user_build_approval")
    t = mention_transcript(root)
    row = one_row(root, stop_payload(t, root))
    assert row["verdict"] == "needs_human" and row["verdict_source"] == "hard_rule", row
    assert "H03_pending_gate" in row["rule_hits"], row


def test_hook_ambiguous_binding_needs_human():
    root = make_project()
    first = seed_artifact(root, wf="wf-sg-0001")
    second = seed_artifact(root, wf="wf-sg-0002")
    now = time.time()
    os.utime(str(first), (now, now))
    os.utime(str(second), (now - 120, now - 120))  # most recently mentioned, but not the newest file
    lines = [user_line("a wf-sg-0001", iso_ago(300)), assistant_line("then wf-sg-0002 " + CONTINUE_TEXT, iso_ago(60))]
    t = write_transcript(root, lines)
    row = one_row(root, stop_payload(t, root))
    assert row["verdict"] == "needs_human" and "H01_no_bound_workflow" in row["rule_hits"], row
    assert row["binding_reason"] == "ambiguous" and row["wf"] is None, row


def test_hook_git_merge_in_progress_h11():
    root = make_project()
    seed_artifact(root)
    head = git(root, "rev-parse", "HEAD").strip()
    (root / ".git" / "MERGE_HEAD").write_text(head + "\n", encoding="utf-8")
    t = mention_transcript(root)
    row = one_row(root, stop_payload(t, root))
    assert row["verdict"] == "needs_human" and "H11_git_unsafe" in row["rule_hits"], row


def test_hook_last_message_missing_uses_transcript_tail():
    root = make_project()
    seed_artifact(root)
    t = mention_transcript(root)
    row = one_row(root, stop_payload(t, root, message=None))
    assert "H00_message_missing" not in row["rule_hits"], row
    assert row["verdict"] == "would_continue" and row["message_chars"] == len(CONTINUE_TEXT), row
    # no payload message and an empty transcript: H00
    root2 = make_project()
    seed_artifact(root2)
    empty = write_transcript(root2, [user_line("hi wf-sg-0001", iso_ago(30))])
    row2 = one_row(root2, stop_payload(empty, root2, message=None))
    assert "H00_message_missing" in row2["rule_hits"] and row2["verdict"] == "needs_human", row2


def test_hook_deadline_bounds_git():
    root = make_project()
    seed_artifact(root)
    t = mention_transcript(root)
    fake = Path(scratch_dir())
    script = fake / "git"
    script.write_text("#!/bin/sh\nexec sleep 0.9\n", encoding="utf-8")
    script.chmod(0o755)
    env = audit_env()
    env["PATH"] = str(fake) + os.pathsep + os.environ.get("PATH", "")
    code, out, err, elapsed = run_gate(stop_payload(t, root), root, env)
    assert (code, out) == (0, ""), (code, out, err)
    assert elapsed < 4.6, elapsed
    row = rows_of(root)[-1]
    assert "H11_git_unsafe" in row["rule_hits"] and row["verdict"] == "needs_human", row
    assert isinstance(row["hook_ms"], int) and 0 <= row["hook_ms"] < 4600, row["hook_ms"]


def test_genuine_human_line_definition():
    root = make_project()
    seed_artifact(root)
    genuine = iso_ago(500)
    later = [
        user_line([{"type": "tool_result", "tool_use_id": "x", "content": "ok"}], iso_ago(400)),
        user_line("meta text", iso_ago(390), isMeta=True),
        user_line("compact summary", iso_ago(380), isCompactSummary=True),
        user_line("<command-name>/clear</command-name>", iso_ago(370)),
        user_line("Stop hook feedback: keep going", iso_ago(360)),
        user_line("craftflow stop-gate: please call the tool", iso_ago(350)),
        user_line("tool output", iso_ago(340), toolUseResult={"stdout": "x"}),
        user_line([{"type": "text", "text": "mixed"}, {"type": "tool_result", "content": "y"}], iso_ago(330)),
    ]
    lines = [user_line("start " + WF_ID, iso_ago(900)), user_line("please go on", genuine),
             assistant_line(CONTINUE_TEXT, iso_ago(450))] + later
    t = write_transcript(root, lines)
    row = one_row(root, stop_payload(t, root))
    assert row["last_human_ts"] == genuine, row["last_human_ts"]
    assert row["turn_seconds_lower_bound"] is False, row
    assert 480 <= row["turn_seconds"] <= 560, row["turn_seconds"]
    # near-miss positive: a list of text/image blocks IS a genuine human line
    newest = iso_ago(100)
    lines.append(user_line([{"type": "text", "text": "ok continue"}, {"type": "image", "source": {}}], newest))
    t2 = write_transcript(root, lines, name="t2.jsonl")
    row2 = one_row(root, stop_payload(t2, root))
    assert row2["last_human_ts"] == newest and 80 <= row2["turn_seconds"] <= 160, row2


def test_turn_seconds_lower_bound():
    root = make_project()
    seed_artifact(root)
    oldest = iso_ago(900)
    lines = [assistant_line("working on " + WF_ID, oldest),
             user_line([{"type": "tool_result", "tool_use_id": "x", "content": "ok"}], iso_ago(500)),
             assistant_line(CONTINUE_TEXT, iso_ago(60))]
    t = write_transcript(root, lines)
    row = one_row(root, stop_payload(t, root))
    assert row["last_human_ts"] is None, row
    assert row["turn_seconds_lower_bound"] is True, row
    assert 880 <= row["turn_seconds"] <= 960, row["turn_seconds"]
    # no timestamps at all: unknown
    root2 = make_project()
    seed_artifact(root2)
    bare = write_transcript(root2, [{"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "wf-sg-0001 " + CONTINUE_TEXT}]}}])
    row2 = one_row(root2, stop_payload(bare, root2))
    assert row2["turn_seconds"] is None, row2


# ---------------------------------------------------------------------------
# Phase 5: optional Jev text classification (DD-11, DD-5a)
# ---------------------------------------------------------------------------

OK_ANSWERS = {"answers": {"stop_kind": {"choice": "phase_done_awaiting_continue", "confidence": 0.95},
                          "needs_human": {"noul": 0.05}}}


@contextlib.contextmanager
def env_patch(**values):
    """Set (or delete, for None) env vars and restore them: the Jev client reads os.environ directly."""
    saved = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class Stub:
    """Loopback-only Jev stub. Records every request body; answers after ``delay`` seconds."""

    def __init__(self, reply=None, delay=0.0):
        self.bodies = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    outer.bodies.append(json.loads(raw.decode("utf-8")))
                except ValueError:
                    outer.bodies.append({})
                if delay:
                    time.sleep(delay)
                payload = json.dumps(reply if reply is not None else OK_ANSWERS).encode("utf-8")
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = "http://127.0.0.1:%d/v1/systemone" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop_kind_requests(self):
        return [b for b in self.bodies if isinstance(b.get("questions"), dict) and "stop_kind" in b["questions"]]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@contextlib.contextmanager
def stub_server(reply=None, delay=0.0):
    stub = Stub(reply, delay)
    try:
        yield stub
    finally:
        stub.close()


def jev_plugin():
    """Scratch plugin dir whose config/jev.json is enabled with every other Jev feature off (A10)."""
    folder = Path(scratch_dir())
    (folder / "config").mkdir()
    (folder / "config" / "jev.json").write_text(json.dumps({
        "enabled": True, "model": "jev-latest", "timeoutSeconds": 2.5,
        "features": {"routingHint": "off", "skillHint": "off", "remediationScope": "off", "riskGate": "off"},
        "consent": {"status": "unset", "ts": None}}), encoding="utf-8")
    return folder


def run_inproc(stub, message=CONTINUE_TEXT, consent=True, key="fake-key", **artifact):
    """Run gate.run() in-process against a clean scratch repo; consent comes from a scratch passwd home."""
    root = make_project()
    seed_artifact(root, **artifact)
    transcript = mention_transcript(root)
    home = scratch_dir()
    if consent:
        write_consent(home, {"jevText": True})
    plugin = jev_plugin()
    seam = audit_config()
    saved = gate._consent_home
    gate._consent_home = lambda: home
    started = time.monotonic()
    try:
        with env_patch(CRAFTFLOW_JEV_ENDPOINT=stub.url, TYPESAFE_API_KEY=key, CLAUDE_PLUGIN_ROOT=str(plugin),
                       CLAUDE_PROJECT_DIR=str(root), CRAFTFLOW_STOP_GATE_USER_CONFIG=str(seam)):
            out, row = gate.run(stop_payload(transcript, root, message=message), dict(os.environ))
    finally:
        gate._consent_home = saved
    return out, row, time.monotonic() - started


def test_should_call_jev_truth_table():
    for consent in (True, False):
        for active in (True, False):
            for hits in ([], ["H03_pending_gate"]):
                for remaining in (core.JEV_MIN_REMAINING_S, core.JEV_MIN_REMAINING_S - 0.01):
                    want = consent and active and hits == [] and remaining >= 1.5
                    got = core.should_call_jev(consent, active, hits, remaining)
                    assert got is want, (consent, active, hits, remaining, got)
    assert core.should_call_jev(True, True, [], 4.2) is True
    assert core.should_call_jev(True, True, [], None) is False  # unknown time: no egress
    assert core.should_call_jev("yes", True, [], 4.0) is False  # only a real True grants consent


def test_jev_tail_redacts_before_truncate():
    value = "Zq8mK2pL9xW4vN7cR5tY1bH6jD3fG0sA"
    for offset in range(-40, 41):
        suffix = " ok" * ((200 - (32 - offset)) // 3 + 1)
        text = "word " * 60 + "OPENAI_KEY=" + value + suffix
        cut_start = len(text) - 200
        assert cut_start > 0
        tail, truncated = core.jev_tail(text, 200)
        assert truncated is True and len(tail) <= 200, (offset, truncated, len(tail))
        for i in range(len(value) - 7):
            assert value[i:i + 8] not in tail, (offset, value[i:i + 8])
    tail, truncated = core.jev_tail("short OPENAI_KEY=" + value, 1500)
    assert (tail, truncated) == ("short OPENAI_KEY=***", False), (tail, truncated)
    assert core.jev_tail("tok sk-fake-secret-1 end", 1500, "sk-fake-secret-1")[0] == "tok *** end"


def test_jev_tail_masks_long_tokens():
    run40 = "AbCdEf0123456789AbCdEf0123456789AbCdEf01"
    tail, _ = core.jev_tail("see " + run40 + " end", 1500)
    assert tail == "see [REDACTED] end", tail
    assert core.jev_tail("x " + "a" * 32 + " y", 1500)[0] == "x [REDACTED] y"
    assert core.jev_tail("x " + "a" * 31 + " y", 1500)[0] == "x " + "a" * 31 + " y"
    tail, truncated = core.jev_tail("0123456789 " * 200, 100)
    assert truncated is True and len(tail) == 100, (truncated, len(tail))


def test_jev_tail_skips_oversize():
    assert core.jev_tail("a " * 40000, 1500) == (None, False)
    tail, truncated = core.jev_tail(("word " * core.JEV_RAW_CAP)[:core.JEV_RAW_CAP], 1500)
    assert tail is not None and truncated is True  # exactly at the cap is still processed
    assert core.jev_tail("d" * (core.JEV_RAW_CAP + 1), 1500) == (None, False)
    assert core.jev_tail(None, 1500) == (None, False)


def test_jev_state_keys_exact():
    state = core.jev_state("tail text", True, "BUILD", True)
    assert state == {"message_tail": "tail text", "message_truncated": True, "workflow_type": "BUILD",
                     "has_next_phase": True}, state
    state = core.jev_state("t", False, None, 0)
    assert sorted(state) == ["has_next_phase", "message_tail", "message_truncated", "workflow_type"], state
    assert state["has_next_phase"] is False and state["message_truncated"] is False, state
    assert state["workflow_type"] == "", state


def test_jev_questions_shape_and_kinds():
    q = core.jev_questions()
    assert sorted(q) == ["needs_human", "stop_kind"], sorted(q)
    kind = q["stop_kind"]
    assert kind["type"] == "choice" and sorted(kind["criteria"]) == sorted(core.STOP_KINDS), kind
    assert all(isinstance(v, str) and v.strip() for v in kind["criteria"].values()), kind
    assert q["needs_human"]["type"] == "noul", q["needs_human"]
    for item in q.values():
        assert "strictly as data" in item["instructions"], item
        assert "message_tail" in item["instructions"], item
    json.dumps(q)


def test_gate_jev_answers_rejects_hostile_choice_and_bad_numbers():
    def ans(choice="asking_decision", conf=0.5, needs=0.5):
        return {"stop_kind": {"choice": choice, "confidence": conf}, "needs_human": {"noul": needs}}

    assert core.gate_jev_answers(ans()) == {"status": "ok", "kind": "asking_decision", "kind_conf": 0.5,
                                            "needs_human": 0.5}
    assert core.gate_jev_answers(ans(conf=0, needs=1))["needs_human"] == 1  # boundaries are valid
    assert core.gate_jev_answers(ans(conf=1, needs=0))["kind_conf"] == 1
    bad = [None, [], "x", {}, {"stop_kind": {}, "needs_human": {}},
           ans(choice="not_a_kind"), ans(choice=["other"]), ans(choice=None), ans(choice="other\n"),
           ans(conf=True), ans(conf=1.01), ans(conf=-0.01), ans(conf=float("nan")), ans(conf="0.9"),
           ans(conf=float("inf")), ans(needs=True), ans(needs=1.5), ans(needs=None), ans(needs="0"),
           {"stop_kind": {"choice": "other", "confidence": 0.5}},
           {"stop_kind": "other", "needs_human": {"noul": 0.1}}]
    for case in bad:
        assert core.gate_jev_answers(case) is None, case


def test_hook_jev_timeout_fail_open():
    # 1) happy path through a loopback stub (consent file + active Jev): a Jev-sourced, act-eligible row
    with stub_server() as stub:
        out, row, _elapsed = run_inproc(stub)
        assert out is None, out
        assert row["verdict_source"] == "jev" and row["act_eligible"] is True, row
        assert row["jev_status"] == "ok" and row["jev_text_source"] == "consent_file", row
        assert row["jev_kind"] == "phase_done_awaiting_continue" and row["jev_kind_conf"] == 0.95, row
        assert row["jev_needs_human"] == 0.05 and row["rule_hits"] == [], row
        assert len(stub.stop_kind_requests()) == 1, stub.bodies
        body = stub.stop_kind_requests()[0]
        assert sorted(body["state"]) == ["has_next_phase", "message_tail", "message_truncated",
                                         "workflow_type"], body["state"]
        assert body["state"]["has_next_phase"] is True and body["state"]["workflow_type"] == "BUILD", body["state"]
        assert "Continue to Phase 2" in body["state"]["message_tail"], body["state"]
    # 2) a stub that sleeps 5 s: wall-clock bounded, fail open, never act-eligible
    with stub_server(delay=5.0) as stub:
        out, row, elapsed = run_inproc(stub)
        assert elapsed < 3.5, elapsed
        assert out is None and row["jev_status"] == "timeout", row
        assert row["act_eligible"] is False and row["verdict_source"] == "heuristic", row
        assert row["verdict"] == "would_continue", row  # the heuristic is kept for calibration
    # 3) redaction probe: two secrets H12 does not catch, one straddling the tail cut
    value = "Zq8mK2pL9xW4vN7cR5tY1bH6jD3fG0sA"
    run40 = "AbCdEf0123456789AbCdEf0123456789AbCdEf01"
    tail_after_value = 1500 - 16  # the cut falls 16 chars into the 32-char value
    closing = " " + run40 + " " + CONTINUE_TEXT
    suffix = ("note " * 400)[:tail_after_value - len(closing)] + closing
    assert len(suffix) == tail_after_value, len(suffix)
    message = "intro " * 40 + "OPENAI_KEY=" + value + suffix
    assert len(message) - 1500 > 0 and message[-1500:].startswith(value[16:]), "probe must cut inside the value"
    assert not core.has_secret_text(message), "H12 must not catch the probe"
    with stub_server() as stub:
        out, row, _elapsed = run_inproc(stub, message=message)
        assert row["rule_hits"] == [] and row["jev_status"] == "ok", row  # Jev really was called
        sent = stub.stop_kind_requests()
        assert len(sent) == 1, stub.bodies
        raw = json.dumps(sent[0]) + json.dumps(sent[0], ensure_ascii=False)
        for secret in (value, run40):
            for i in range(len(secret) - 7):
                assert secret[i:i + 8] not in raw, ("leaked", secret[i:i + 8])
        assert len(sent[0]["state"]["message_tail"]) <= 1500, len(sent[0]["state"]["message_tail"])
        assert sent[0]["state"]["message_truncated"] is True, sent[0]["state"]


def test_hook_jev_not_called_when_rule_hits():
    with stub_server() as stub:
        out, row, _elapsed = run_inproc(stub, pending_gate="user_build_approval")
        assert row["verdict"] == "needs_human" and row["verdict_source"] == "hard_rule", row
        assert row["jev_status"] == "not_needed" and "H03_pending_gate" in row["rule_hits"], row
        assert stub.stop_kind_requests() == [], stub.bodies
    with stub_server() as stub:  # no consent: not called, even though Jev is active
        out, row, _elapsed = run_inproc(stub, consent=False)
        assert row["jev_status"] == "not_consented" and row["verdict_source"] == "heuristic", row
        assert stub.stop_kind_requests() == [], stub.bodies


def test_hook_jev_text_from_seam_or_home_env_never_egresses():
    wrapper_dir = Path(scratch_dir())

    def wrapper(consent_home):
        path = wrapper_dir / ("wrap-%d.py" % len(list(wrapper_dir.iterdir())))
        path.write_text(
            "import sys\nsys.path.insert(0, %r)\nimport craftflow_stop_gate as g\n"
            "g._consent_home = lambda: %r\nsys.exit(g.main())\n" % (str(SCRIPTS), consent_home), encoding="utf-8")
        return path

    def jev_env(stub, key="fake-key"):
        return {"CRAFTFLOW_JEV_ENDPOINT": stub.url, "TYPESAFE_API_KEY": key,
                "CLAUDE_PLUGIN_ROOT": str(jev_plugin())}

    def fresh():
        root = make_project()
        seed_artifact(root)
        return root, mention_transcript(root)

    with stub_server() as stub:
        # 1) seam user file claims jevText, consent home EMPTY
        root, t = fresh()
        seam = Path(scratch_dir()) / "seam.json"
        seam.write_text(json.dumps({"mode": "audit", "jevText": True}), encoding="utf-8")
        env = jev_env(stub)
        env["CRAFTFLOW_STOP_GATE_USER_CONFIG"] = str(seam)
        code, _out, err, _ = run_gate(stop_payload(t, root), root, env, script=wrapper(scratch_dir()))
        assert code == 0, err
        row = rows_of(root)[-1]
        assert row["jev_text_source"] == "ignored_seam" and row["jev_status"] == "not_consented", row
        assert stub.stop_kind_requests() == [], "seam file must not grant text egress"
        # 2) same claim through a HOME that differs from the passwd home
        root, t = fresh()
        fake_home = scratch_dir()
        write_consent(fake_home, {"mode": "audit", "jevText": True})
        env = jev_env(stub)
        env.update({"HOME": fake_home, "CRAFTFLOW_STOP_GATE_USER_CONFIG": None})
        code, _out, err, _ = run_gate(stop_payload(t, root), root, env, script=wrapper(scratch_dir()))
        assert code == 0, err
        row = rows_of(root)[-1]
        assert row["jev_text_source"] == "ignored_seam" and row["mode"] == "audit", row
        assert stub.stop_kind_requests() == [], "HOME file must not grant text egress"
        # 3) real consent, seam gives only the mode, Jev inactive (no key)
        root, t = fresh()
        consent_home = scratch_dir()
        write_consent(consent_home, {"jevText": True})
        env = jev_env(stub, key=None)
        env["CRAFTFLOW_STOP_GATE_USER_CONFIG"] = str(audit_config())
        code, _out, err, _ = run_gate(stop_payload(t, root), root, env, script=wrapper(consent_home))
        assert code == 0, err
        row = rows_of(root)[-1]
        assert row["jev_text_source"] == "consent_file" and row["jev_status"] == "inactive", row
        assert stub.stop_kind_requests() == [], "an inactive Jev must not be called"
        assert stub.bodies == [], stub.bodies


# ---------------------------------------------------------------------------
# Phase 6: notifications (desktop sender, consent-gated push relay) (DD-12, DD-5a, A8, A9)
# ---------------------------------------------------------------------------

NOTIFIER = "osascript" if sys.platform == "darwin" else "notify-send"
RELAY_PREFIX = "craftflow stop-gate: the user asked to be notified"


def fake_notifier(sleep_git=False):
    """(bin_dir, record_path): a fake notifier that records argv (one arg per line, then ---CALL), plus an
    optional slow fake git. Putting bin_dir first on PATH means a real desktop notification can never fire."""
    folder = Path(scratch_dir())
    record = folder / "calls.txt"
    script = folder / NOTIFIER
    script.write_text("#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\" >> " + str(record)
                      + "; done\nprintf '%s\\n' ---CALL >> " + str(record) + "\n", encoding="utf-8")
    script.chmod(0o755)
    if sleep_git:
        slow = folder / "git"
        slow.write_text("#!/bin/sh\nexec sleep 0.9\n", encoding="utf-8")
        slow.chmod(0o755)
    return str(folder), record


def recorded_calls(record):
    """Argv lists the fake notifier saw."""
    if not Path(record).exists():
        return []
    calls, current = [], []
    for line in Path(record).read_text(encoding="utf-8").splitlines():
        if line == "---CALL":
            calls.append(current)
            current = []
        else:
            current.append(line)
    return calls


def notify_wrapper(consent_home):
    """Test-only entry point: the real hook main() with the consent lookup pointed at a scratch home."""
    path = Path(scratch_dir()) / "wrap.py"
    path.write_text("import sys\nsys.path.insert(0, %r)\nimport craftflow_stop_gate as g\n"
                    "g._consent_home = lambda: %r\nsys.exit(g.main())\n" % (str(SCRIPTS), consent_home),
                    encoding="utf-8")
    return path


def notify_run(root, transcript, seam, consent=None, bin_dir=None, timeout=20, **payload):
    """One subprocess run through the wrapper. The real consent file is never read."""
    seam_path = Path(scratch_dir()) / "seam.json"
    seam_path.write_text(json.dumps(seam), encoding="utf-8")
    home = scratch_dir()
    if consent is not None:
        write_consent(home, consent)
    env = {"CRAFTFLOW_STOP_GATE_USER_CONFIG": str(seam_path)}
    if bin_dir:
        env["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    code, out, err, elapsed = run_gate(stop_payload(transcript, root, **payload), root, env,
                                       script=notify_wrapper(home), timeout=timeout)
    assert code == 0, (code, err)
    return out, err, elapsed


PENDING = {"pending_gate": "user_build_approval"}
NOTIFY_SEAM = {"mode": "audit", "notifyMinTurnSeconds": 0}
# The core only lets the consent file AUTHORISE a requested push (it never switches push on alone), so a push
# run needs the user layer (here the seam, in production the same stop-gate.json) to ask for it too.
PUSH_SEAM = dict(NOTIFY_SEAM, notify="push")


def test_notify_push_relay_block_once():
    root = make_project()
    seed_artifact(root, **PENDING)
    t = mention_transcript(root)
    bin_dir, record = fake_notifier()
    seam = PUSH_SEAM
    out, err, _ = notify_run(root, t, seam, consent={"notify": "push"}, bin_dir=bin_dir)
    obj = json.loads(out)
    assert out.strip().count("\n") == 0 and obj["decision"] == "block", out
    assert obj["reason"].startswith(RELAY_PREFIX), obj["reason"]
    assert obj["reason"] == core.PUSH_RELAY_TEMPLATE % core.notify_text(
        "needs_human", WF_ID, "P2", ["H03_pending_gate"]), obj["reason"]
    row = rows_of(root)[-1]
    assert row["notify"] == "push" and row["notify_status"] == "relay" and row["row_kind"] == "stop", row
    assert recorded_calls(record) == [], "push must not also fire a desktop notification"
    # follow-up stop inside the relay turn: silent, followup row
    out, _, _ = notify_run(root, t, seam, consent={"notify": "push"}, bin_dir=bin_dir, stop_hook_active=True)
    assert out == "", out
    follow = rows_of(root)[-1]
    assert follow["row_kind"] == "relay_followup" and set(follow) == set(core.ROW_KEYS), follow
    assert follow["verdict"] is None and follow["would_action"] == "none", follow
    assert follow["notify_status"] == "relay_followup", follow
    for key in ("rule_hits", "loop_guards", "commit_blockers"):
        assert follow[key] == [], (key, follow)
    # same tail again: deduped, no second relay
    out, _, _ = notify_run(root, t, seam, consent={"notify": "push"}, bin_dir=bin_dir)
    assert out == "", out
    # control: push in the seam file is ignored -> desktop, no block
    root2 = make_project()
    seed_artifact(root2, **PENDING)
    t2 = mention_transcript(root2)
    bin2, record2 = fake_notifier()
    out, _, _ = notify_run(root2, t2, dict(NOTIFY_SEAM, notify="push"), consent=None, bin_dir=bin2)
    assert out == "", out
    row2 = rows_of(root2)[-1]
    assert row2["notify"] == "desktop" and "notify_push_seam_ignored" in row2["settings_tags"], row2
    sent_calls = recorded_calls(record2)  # under heavy host load the sender may time out: then it is recorded
    assert row2["notify_status"] in ("sent", "timeout", "skipped_deadline"), row2
    if row2["notify_status"] == "sent":
        assert len(sent_calls) == 1, sent_calls


def test_notify_push_never_on_stop_hook_active():
    root = make_project()
    seed_artifact(root, **PENDING)
    t = mention_transcript(root)
    bin_dir, record = fake_notifier()
    out, _, _ = notify_run(root, t, PUSH_SEAM, consent={"notify": "push"}, bin_dir=bin_dir,
                           stop_hook_active=True)
    assert out == "", out
    row = rows_of(root)[-1]
    assert row["row_kind"] == "stop" and row["notify_status"] == "skipped_hook_active", row
    assert recorded_calls(record) == [], "a refused relay must not fall back to a desktop banner"


def test_notify_skipped_below_min_turn_seconds():
    root = make_project()
    seed_artifact(root, **PENDING)
    t = mention_transcript(root)  # the human line is ~600 s old
    bin_dir, record = fake_notifier()
    out, _, _ = notify_run(root, t, dict(NOTIFY_SEAM, notify="desktop", notifyMinTurnSeconds=3600),
                           bin_dir=bin_dir)
    assert out == "" and rows_of(root)[-1]["notify_status"] == "skipped_min_turn", rows_of(root)[-1]
    assert recorded_calls(record) == [], recorded_calls(record)
    out, _, _ = notify_run(root, t, dict(PUSH_SEAM, notifyMinTurnSeconds=3600),
                           consent={"notify": "push"}, bin_dir=bin_dir)
    assert out == "" and rows_of(root)[-1]["notify_status"] == "skipped_min_turn", rows_of(root)[-1]
    # near miss: a threshold under the turn length sends
    out, _, _ = notify_run(root, t, dict(NOTIFY_SEAM, notify="desktop", notifyMinTurnSeconds=300),
                           bin_dir=bin_dir, message="Ready for review?")
    assert rows_of(root)[-1]["notify_status"] == "sent", rows_of(root)[-1]
    # a non-needs_human verdict never notifies
    root2 = make_project()
    seed_artifact(root2)
    t2 = mention_transcript(root2)
    bin2, record2 = fake_notifier()
    notify_run(root2, t2, dict(NOTIFY_SEAM, notify="desktop"), bin_dir=bin2)
    row = rows_of(root2)[-1]
    assert row["verdict"] == "would_continue" and row["notify_status"] == "not_needed", row
    assert recorded_calls(record2) == [], recorded_calls(record2)


def test_notify_desktop_fake_osascript_argv():
    root = make_project()
    seed_artifact(root, **PENDING)
    t = mention_transcript(root)
    bin_dir, record = fake_notifier()
    out, _, _ = notify_run(root, t, dict(NOTIFY_SEAM, notify="desktop"), bin_dir=bin_dir)
    assert out == "", out
    row = rows_of(root)[-1]
    want = core.notify_text("needs_human", row["wf"], row["phase_cursor"], row["rule_hits"])
    calls = recorded_calls(record)
    assert row["notify_status"] == "sent" and len(calls) == 1, (row, calls)
    assert calls[0][-1] == want and want.startswith("Craftflow: needs you - "), (calls, want)
    if NOTIFIER == "osascript":
        assert calls[0][:3] == ["-e", "on run argv", "-e"], calls[0]
    # the same stop is not announced twice
    notify_run(root, t, dict(NOTIFY_SEAM, notify="desktop"), bin_dir=bin_dir)
    assert len(recorded_calls(record)) == 1, recorded_calls(record)
    assert rows_of(root)[-1]["notify_status"] == "skipped_duplicate", rows_of(root)[-1]


def test_notify_unsupported_platform_status():
    root = make_project()
    seed_artifact(root, **PENDING)
    t = mention_transcript(root)
    seam = Path(scratch_dir()) / "seam.json"
    seam.write_text(json.dumps(dict(NOTIFY_SEAM, notify="desktop")), encoding="utf-8")
    saved = sys.platform
    try:
        sys.platform = "plan9"
        with env_patch(CLAUDE_PROJECT_DIR=str(root), CRAFTFLOW_STOP_GATE_USER_CONFIG=str(seam)):
            out, row = gate.run(stop_payload(t, root), dict(os.environ))
    finally:
        sys.platform = saved
    assert out is None and row["notify_status"] == "unsupported_platform", row


def test_hook_source_has_no_shell_true():
    import re
    for name in ("craftflow_stop_gate.py", "craftflow_stop_gate_core.py", "craftflow_stop_gate_report.py"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert not re.search(r"shell\s*=\s*True", text), name + ": shell=True is forbidden"
        assert "os.system" not in text and "os.popen" not in text, name + ": no shell helpers"
    source = (SCRIPTS / "craftflow_stop_gate.py").read_text(encoding="utf-8")
    assert re.search(r"shutil\.which", source) and "sys.platform" in source, "sender must use which + platform"


def test_relay_followup_row_shape():
    root = make_project()
    seed_artifact(root, **PENDING)
    t = mention_transcript(root)
    bin_dir, _record = fake_notifier()
    consent = {"notify": "push"}
    notify_run(root, t, PUSH_SEAM, consent=consent, bin_dir=bin_dir)
    assert gate.read_session(root, "s1")[0]["relay_pending"] is True
    out, _, _ = notify_run(root, t, PUSH_SEAM, consent=consent, bin_dir=bin_dir, stop_hook_active=True)
    assert out == "", out
    row = rows_of(root)[-1]
    assert row["row_kind"] == "relay_followup" and list(row) == list(core.ROW_KEYS), row
    for key in ("heuristic_kind", "heuristic_verdict", "jev_status", "jev_kind", "jev_kind_conf",
                "jev_needs_human", "jev_latency_ms", "jev_usage", "jev_text_source", "verdict",
                "verdict_source", "cursor_case", "cursor_resolution"):
        assert row[key] is None, (key, row[key])
    assert row["act_eligible"] is False and row["stop_hook_active"] is True, row
    assert row["session_id"] == "s1" and row["mode"] == "audit", row
    assert gate.read_session(root, "s1")[0]["relay_pending"] is False, "followup clears relay_pending"
    assert [r["row_kind"] for r in rows_of(root)] == ["stop", "relay_followup"], rows_of(root)


def test_relay_refused_while_pending_and_cleared_on_fresh_stop():
    root = make_project()
    seed_artifact(root, **PENDING)
    t = mention_transcript(root)
    bin_dir, _record = fake_notifier()
    consent = {"notify": "push"}
    stale = {"would_continue_since_human": 0, "last_human_ts": None, "last_head": None, "last_cursor": None,
             "last_notified_tail_sha": None, "relay_pending": True, "updated_at": 0}
    assert gate.write_session(root, "s1", stale)
    # a needs-human stop inside the relay turn: refused (and recorded as the followup)
    out, _, _ = notify_run(root, t, PUSH_SEAM, consent=consent, bin_dir=bin_dir, stop_hook_active=True,
                           message="Should I use A or B?")
    assert out == "" and rows_of(root)[-1]["row_kind"] == "relay_followup", (out, rows_of(root)[-1])
    # a fresh stop (hook not active) with a stale pending flag clears it first, then may relay
    assert gate.write_session(root, "s1", stale)
    out, _, _ = notify_run(root, t, PUSH_SEAM, consent=consent, bin_dir=bin_dir, message="Which option?")
    assert json.loads(out)["decision"] == "block", out
    assert rows_of(root)[-1]["row_kind"] == "stop" and rows_of(root)[-1]["notify_status"] == "relay"


def test_notify_desktop_under_deadline_fake_osascript():
    root = make_project()
    seed_artifact(root)
    t = mention_transcript(root)
    bin_dir, record = fake_notifier(sleep_git=True)  # every git call burns 0.8 s, H11 makes it needs_human
    out, _, elapsed = notify_run(root, t, dict(NOTIFY_SEAM, notify="desktop"), bin_dir=bin_dir)
    assert out == "" and elapsed < 4.6, (out, elapsed)
    row = rows_of(root)[-1]
    assert "H11_git_unsafe" in row["rule_hits"] and row["verdict"] == "needs_human", row
    calls = recorded_calls(record)
    assert row["notify_status"] in ("sent", "skipped_deadline", "timeout"), row  # timeout: host overload only
    if row["notify_status"] == "sent":
        assert len(calls) == 1 and calls[0][-1].startswith("Craftflow: needs you"), calls
    elif row["notify_status"] == "skipped_deadline":
        assert calls == [], calls
    # deterministic bound: with 1.19 s left the sender refuses, with plenty left it sends
    bin2, record2 = fake_notifier()
    assert gate.send_desktop("Craftflow: needs you", lambda: 1.19, bin2) == "skipped_deadline"
    assert recorded_calls(record2) == []
    assert gate.send_desktop("Craftflow: needs you", lambda: 3.0, bin2) == "sent"
    assert recorded_calls(record2)[0][-1] == "Craftflow: needs you"


# ---------------------------------------------------------------------------
# P7: calibration report and offline replay
# ---------------------------------------------------------------------------
import craftflow_stop_gate_report as report  # noqa: E402

REPORT_SCRIPT = SCRIPTS / "craftflow_stop_gate_report.py"
SAMPLE_DIR = PLUGIN_ROOT / "tests" / "fixtures" / "stop_gate"


def test_label_reply_table():
    for text in ("continue", "Yes.", "go ahead", "commit"):
        assert core.label_reply(text) == "rubber_stamp", text
    assert core.label_reply("no, wait") == "negative"
    assert core.label_reply("use option B") == "human_input"
    assert core.label_reply("") == "unlabeled"
    assert core.label_reply(None) == "unlabeled"


def _tline(kind, ts, content, **extra):
    rec = {"type": kind, "timestamp": ts, "message": {"role": kind, "content": content}}
    rec.update(extra)
    return json.dumps(rec)


def test_join_next_user_reply_after_ts():
    lines = [
        _tline("user", "2026-10-01T10:00:00Z", "before the stop"),
        _tline("assistant", "2026-10-01T10:01:00Z", [{"type": "text", "text": "done. continue?"}]),
        _tline("user", "2026-10-01T10:01:05Z", [{"type": "tool_result", "content": "x"}]),
        _tline("user", "2026-10-01T10:01:06Z", "meta line", isMeta=True),
        _tline("user", "2026-10-01T10:01:07Z", "Stop hook feedback: something"),
        _tline("user", "2026-10-01T10:01:08Z", "craftflow stop-gate: the user asked to be notified"),
        _tline("user", "2026-10-01T10:02:00Z", "go ahead"),
        _tline("user", "2026-10-01T10:03:00Z", "later message"),
    ]
    records = list(report.iter_records("\n".join(lines).encode("utf-8")))
    assert report.join_next_user_reply(records, "2026-10-01T10:01:30Z") == "go ahead"
    assert report.join_next_user_reply(records, "2026-10-01T10:01:00Z") == "go ahead"
    assert report.join_next_user_reply(records, "2026-10-01T10:03:30Z") is None
    assert report.join_next_user_reply(records, "not-a-timestamp") is None
    rows = [
        {"row_kind": "stop", "verdict": "would_continue"},
        {"row_kind": "relay_followup", "verdict": None},
        {"row_kind": "error", "verdict": None},
        "garbage",
    ]
    assert report.metric_rows(rows) == [rows[0]]


def test_sweep_precision_math():
    def row(label, conf, needs=0.1, hits=()):
        return {"label": label, "jev_status": "ok", "jev_kind": "phase_done_awaiting_continue",
                "jev_kind_conf": conf, "jev_needs_human": needs, "heuristic_verdict": "would_continue",
                "rule_hits": list(hits)}
    rows = [row("rubber_stamp", 0.95), row("rubber_stamp", 0.95), row("rubber_stamp", 0.95),
            row("negative", 0.95), row("rubber_stamp", 0.75), row("rubber_stamp", 0.99, hits=["H03_pending_gate"]),
            row("unlabeled", 0.99)]
    out = core.sweep(rows, kind_thresholds=[0.7, 0.9], needs_maxes=[0.05, 0.2])
    cells = {(c["kind_threshold"], c["needs_human_max"]): c for c in out["jev"]}
    assert len(cells) == 4
    assert cells[(0.9, 0.2)] == {"kind_threshold": 0.9, "needs_human_max": 0.2, "n": 4, "rubber_stamp": 3,
                                 "precision": 0.75, "coverage": 0.8}
    assert cells[(0.7, 0.2)]["n"] == 5 and cells[(0.7, 0.2)]["precision"] == 0.8
    assert cells[(0.7, 0.05)]["n"] == 0 and cells[(0.7, 0.05)]["precision"] is None
    assert out["heuristic"]["n"] == 5 and out["heuristic"]["rubber_stamp"] == 4
    assert out["heuristic"]["precision"] == 0.8
    default = core.sweep([])
    assert len(default["jev"]) == 30 * 10


def test_go_criteria_rd3():
    good = {"would_continue_labeled": 50, "sessions": 5, "precision": 0.95, "negatives": 0,
            "jev_failure_rate": 0.05, "hook_p90_ms": 1500, "label_coverage": 0.8, "jev_calls": 20}
    verdict = core.go_criteria(good)
    assert verdict["met"] is True and verdict["act"] == "RD-3 met: Slice 2 may be planned"
    assert core.RD3["label_coverage"] == 0.8 and core.RD3["jev_calls_min"] == 20
    for key, bad in (("would_continue_labeled", 49), ("sessions", 4), ("precision", 0.94), ("negatives", 1),
                     ("jev_failure_rate", 0.06), ("hook_p90_ms", 1501), ("label_coverage", 0.79),
                     ("jev_calls", 19)):
        worse = core.go_criteria(dict(good, **{key: bad}))
        crit = "jev_calls_min" if key == "jev_calls" else key
        assert worse["met"] is False, key
        assert worse["criteria"][crit]["met"] is False, key
        assert worse["act"] == "NO-GO", key
    empty = core.go_criteria({})
    assert empty["met"] is False and empty["act"] == "NO-GO"
    # an absent Jev failure rate is NOT vacuously met, even when the call count would otherwise pass
    none_rate = core.go_criteria(dict(good, jev_failure_rate=None))
    assert none_rate["met"] is False and none_rate["criteria"]["jev_failure_rate"]["met"] is False, none_rate
    assert core.go_criteria(dict(good, label_coverage=None))["met"] is False


def _run_report(args, cwd=None):
    return subprocess.run([sys.executable, str(REPORT_SCRIPT)] + args, capture_output=True, text=True,
                          timeout=30, cwd=cwd or str(PLUGIN_ROOT))


def test_report_cli_sample():
    proc = _run_report(["--events", "tests/fixtures/stop_gate/events-sample.jsonl",
                        "--transcripts-root", "tests/fixtures/stop_gate/transcripts"])
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for key in ("rows", "labeled", "by_verdict", "rule_hits", "jev", "heuristic", "sweep", "latency_ms",
                "usage_total", "go_criteria"):
        assert key in out, key
    assert out["go_criteria"]["met"] is False and out["go_criteria"]["act"] == "NO-GO"
    assert out["rows"] > 0 and out["labeled"] > 0
    assert out["by_verdict"].get("would_continue", 0) >= 1
    assert "relay_followup" not in json.dumps(out["by_verdict"])
    assert out["rows"] == sum(out["by_verdict"].values())


def test_replay_never_prints_text():
    scr = scratch_dir()
    marker = "ZZPLANTEDMARKERZZ"
    lines = [
        _tline("user", "2026-10-01T10:00:00Z", "start"),
        _tline("assistant", "2026-10-01T10:01:00Z", [{"type": "text", "text": marker + " shall we continue?"}]),
        _tline("user", "2026-10-01T10:02:00Z", "continue"),
        _tline("assistant", "2026-10-01T10:03:00Z", [{"type": "text", "text": marker + " all done."}]),
        _tline("user", "2026-10-01T10:04:00Z", marker + " different request"),
    ]
    Path(scr, "s1.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    proc = _run_report(["--replay", "--transcripts-root", scr])
    assert proc.returncode == 0, proc.stderr
    assert marker not in proc.stdout and marker not in proc.stderr
    out = json.loads(proc.stdout)
    assert out["turns"] == 2 and out["rubber_stamp"] == 1
    assert out["question_end"] == 1 and out["question_end_rubber_stamp"] == 1
    assert out["heuristic_confusion"]["would_continue"] == {"rubber_stamp": 1, "other": 0}
    assert out["heuristic_confusion"]["needs_human"] == {"rubber_stamp": 0, "other": 1}


def test_report_handles_missing_transcripts():
    scr = scratch_dir()
    events = Path(scr, "events.jsonl")
    row = core.build_row(row_kind="stop", ts="2026-10-01T10:01:00Z", session_id="nope",
                         transcript_path="/nonexistent/x.jsonl", verdict="would_continue",
                         verdict_source="heuristic", heuristic_verdict="would_continue", rule_hits=[])
    events.write_text(json.dumps(row) + "\nnot json\n", encoding="utf-8")
    proc = _run_report(["--events", str(events), "--transcripts-root", os.path.join(scr, "absent")])
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["rows"] == 1 and out["labeled"] == 0
    assert out["go_criteria"]["met"] is False
    missing = _run_report(["--events", os.path.join(scr, "no-such.jsonl")])
    assert missing.returncode == 0
    assert json.loads(missing.stdout)["rows"] == 0



# ---------------------------------------------------------------------------
# Review remediation (R1..R12)
# ---------------------------------------------------------------------------
import io  # noqa: E402


@contextlib.contextmanager
def patched(module, **attrs):
    saved = {key: getattr(module, key) for key in attrs}
    try:
        for key, value in attrs.items():
            setattr(module, key, value)
        yield
    finally:
        for key, value in saved.items():
            setattr(module, key, value)


def _boom(*_args, **_kw):
    raise RuntimeError("PLANTEDMARKER secret message text")


def main_inproc(root, payload, seam=None, env_extra=None, consent=None, **patches):
    """gate.main() in-process (stdin, log_event and consent home replaced). Returns (code, stdout, logged)."""
    logged = []
    home = scratch_dir()
    if consent is not None:
        write_consent(home, consent)
    env = {"CLAUDE_PROJECT_DIR": str(root), "CURSOR_PLUGIN_ROOT": None,
           "CRAFTFLOW_STOP_GATE_USER_CONFIG": str(seam or audit_config())}
    env.update(env_extra or {})
    buf = io.StringIO()
    with env_patch(**env):
        with patched(gate, load_input=lambda: payload, log_event=lambda n, p: logged.append((n, p)),
                     _consent_home=lambda: home, **patches):
            with contextlib.redirect_stdout(buf):
                code = gate.main()
    return code, buf.getvalue(), logged


def test_r1_hook_exception_leaves_error_row():
    root = make_project()
    seed_artifact(root)
    t = mention_transcript(root)
    code, out, _logged = main_inproc(root, stop_payload(t, root), scan_transcript=_boom)
    assert (code, out) == (0, ""), (code, out)
    rows = rows_of(root)
    assert len(rows) == 1, rows
    row = rows[0]
    assert set(row) == set(core.ROW_KEYS) and row["row_kind"] == "error", row
    assert "hook_error:RuntimeError" in row["settings_tags"], row
    assert row["mode"] == "audit" and row["session_id"] == "s1", row
    assert "PLANTEDMARKER" not in json.dumps(row), "exception message text leaked into the row"
    assert report.metric_rows(rows) == [], "error rows never count toward metrics"


def test_r1_unwritable_events_logs_error_event_exit0():
    root = make_project()
    seed_artifact(root)
    t = mention_transcript(root)
    (root / ".craftflow" / "state" / "stop-gate" / "events.jsonl").mkdir(parents=True)
    code, out, logged = main_inproc(root, stop_payload(t, root))
    assert (code, out) == (0, ""), (code, out)
    assert ("plugin_stop_gate_error", {"exc": "IsADirectoryError"}) in logged, logged
    # and the real subprocess still exits 0 without a traceback
    root2 = make_project()
    seed_artifact(root2)
    (root2 / ".craftflow" / "state" / "stop-gate" / "events.jsonl").mkdir(parents=True)
    code, out, err, _ = run_gate(stop_payload(mention_transcript(root2), root2), root2, audit_env())
    assert (code, out) == (0, ""), (code, out, err)
    assert "Traceback" not in err, err


def test_r1_row_appended_before_relay_printed():
    root = make_project()
    seed_artifact(root, **PENDING)
    t = mention_transcript(root)
    seam = Path(scratch_dir()) / "seam.json"
    seam.write_text(json.dumps(PUSH_SEAM), encoding="utf-8")
    seen = []
    real = gate.append_row

    def spy(row):
        seen.append(sys.stdout.getvalue())
        real(row)

    code, out, _ = main_inproc(root, stop_payload(t, root), seam=seam, consent={"notify": "push"}, append_row=spy)
    assert code == 0 and json.loads(out)["decision"] == "block", out
    assert seen == [""], "the relay block must not be printed before the row is appended: " + repr(seen)
    # a row that cannot be written suppresses the relay (no side effect without a trace)
    root2 = make_project()
    seed_artifact(root2, **PENDING)
    code, out, _ = main_inproc(root2, stop_payload(mention_transcript(root2), root2), seam=seam,
                               consent={"notify": "push"}, append_row=_boom)
    assert (code, out) == (0, ""), (code, out)


def test_r2_report_attributes_reply_to_latest_preceding_stop_row():
    scr = scratch_dir()
    lines = [_tline("user", "2026-10-01T07:59:00Z", "start"),
             _tline("assistant", "2026-10-01T08:10:30Z", [{"type": "text", "text": "done. continue?"}]),
             _tline("user", "2026-10-01T08:11:00Z", "yes")]
    Path(scr, "s1.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = [core.build_row(row_kind="stop", ts="2026-10-01T08:%02d:00Z" % m, session_id="s1",
                           transcript_path="/gone/s1.jsonl", verdict="would_continue",
                           heuristic_verdict="would_continue") for m in (0, 5, 10)]
    out = report.build_report(rows, scr)
    assert out["rows"] == 3 and out["labeled"] == 1, out
    assert out["unlabeled"] == 2 and out["label_coverage"] == round(1 / 3, 4), out
    # a hook-active (follow-up) stop is excluded from the precision denominator
    rows2 = [core.build_row(row_kind="stop", ts="2026-10-01T08:00:00Z", session_id="s1",
                            transcript_path="/gone/s1.jsonl", verdict="would_continue", stop_hook_active=False),
             core.build_row(row_kind="stop", ts="2026-10-01T08:05:00Z", session_id="s1",
                            transcript_path="/gone/s1.jsonl", verdict="would_continue", stop_hook_active=True)]
    out2 = report.build_report(rows2, scr)
    assert out2["labeled"] == 1, out2
    assert out2["go_criteria"]["stats"]["would_continue_labeled"] == 0, out2["go_criteria"]["stats"]


def test_r3_push_relay_capped_per_human_turn():
    root = make_project()
    seed_artifact(root, **PENDING)
    t = mention_transcript(root)
    bin_dir, _record = fake_notifier()
    consent = {"notify": "push"}
    outs = [notify_run(root, t, PUSH_SEAM, consent=consent, bin_dir=bin_dir, message=m)[0]
            for m in ("Which option?", "Which one, A or B?", "Should I use X or Y?")]
    assert len([o for o in outs if o]) == 1, outs
    assert [r["notify_status"] for r in rows_of(root)] == ["relay", "skipped_duplicate", "skipped_duplicate"]
    # a new genuine human turn re-arms exactly one relay
    t2 = mention_transcript(root, extra=[user_line("ok go on", iso_ago(5))])
    again = notify_run(root, t2, PUSH_SEAM, consent=consent, bin_dir=bin_dir, message="Which approach?")[0]
    assert json.loads(again)["decision"] == "block", again


def test_r3_push_relay_refused_without_session_id():
    root = make_project()
    seed_artifact(root, **PENDING)
    t = mention_transcript(root)
    bin_dir, _record = fake_notifier()
    for _ in range(2):
        out, _, _ = notify_run(root, t, PUSH_SEAM, consent={"notify": "push"}, bin_dir=bin_dir, session_id="")
        assert out == "", out
    assert [r["notify_status"] for r in rows_of(root)] == ["skipped_no_session"] * 2, rows_of(root)


def _plugin_with_audit():
    folder = Path(scratch_dir())
    (folder / "config").mkdir()
    (folder / "config" / "stop-gate.json").write_text(json.dumps({"mode": "audit"}), encoding="utf-8")
    return folder


def test_r4_corrupt_user_config_surfaced_and_fails_open():
    root = make_project()
    seed_artifact(root)
    t = mention_transcript(root)
    bad = Path(scratch_dir()) / "u.json"
    bad.write_text('{"mode":"audit",}', encoding="utf-8")
    code, out, logged = main_inproc(root, stop_payload(t, root), seam=bad)
    assert (code, out) == (0, "") and rows_of(root) == [], (code, out)  # mode stays off: fail open
    errs = [p for n, p in logged if n == "plugin_stop_gate" and "config_error" in p]
    assert [e["config_error"] for e in errs] == ["corrupt"], logged
    big = Path(scratch_dir()) / "big.json"
    big.write_text('{"mode":"audit","pad":"' + "x" * 70000 + '"}', encoding="utf-8")
    _c, _o, logged = main_inproc(root, stop_payload(t, root), seam=big)
    assert [p["config_error"] for n, p in logged if n == "plugin_stop_gate" and "config_error" in p] == ["too_large"]
    # an absent user file is not an error
    absent = Path(scratch_dir()) / "nope.json"
    _c, _o, logged = main_inproc(root, stop_payload(t, root), seam=absent)
    assert not [1 for n, p in logged if "config_error" in p], logged
    # when the plugin file keeps the hook on, the row carries a settings tag
    plugin = _plugin_with_audit()
    code, out, logged = main_inproc(root, stop_payload(t, root), seam=bad,
                                    env_extra={"CLAUDE_PLUGIN_ROOT": str(plugin)})
    assert (code, out) == (0, ""), (code, out)
    assert "user_config_error:corrupt" in rows_of(root)[-1]["settings_tags"], rows_of(root)[-1]
    assert len([1 for n, p in logged if "config_error" in p]) == 1, logged  # logged once per Stop


def test_r4_corrupt_plugin_file_not_surfaced():
    root = make_project()
    t = mention_transcript(root)
    plugin = Path(scratch_dir())
    (plugin / "config").mkdir()
    (plugin / "config" / "stop-gate.json").write_text("{not json", encoding="utf-8")
    absent = Path(scratch_dir()) / "nope.json"
    _c, _o, logged = main_inproc(root, stop_payload(t, root), seam=absent,
                                 env_extra={"CLAUDE_PLUGIN_ROOT": str(plugin)})
    assert not [1 for n, p in logged if "config_error" in p], logged


def test_r5_git_status_lists_untracked_files_individually():
    root = make_project()
    (root / "newdir").mkdir()
    (root / "newdir" / "a.txt").write_text("x", encoding="utf-8")
    facts = gate.git_facts(str(root), lambda: 4.0)
    assert facts["ok"] is True and facts["dirty_paths"] == ["newdir/a.txt"], facts
    # so a secret-looking file inside a new directory is seen by H12
    (root / "newdir" / ".env").write_text("K=1", encoding="utf-8")
    paths = gate.git_facts(str(root), lambda: 4.0)["dirty_paths"]
    assert "newdir/.env" in paths and any(core.secret_path_hit(p) for p in paths), paths


def test_r6_dirty_path_cap_fails_closed():
    root = make_project()
    folder = root / "many"
    folder.mkdir()
    for n in range(gate.STATUS_DIRTY_CAP):
        (folder / ("f%03d.txt" % n)).write_text("x", encoding="utf-8")
    exact = gate.git_facts(str(root), lambda: 4.0)
    assert exact["ok"] is True and len(exact["dirty_paths"]) == gate.STATUS_DIRTY_CAP, len(exact["dirty_paths"])
    (folder / "extra.txt").write_text("x", encoding="utf-8")
    over = gate.git_facts(str(root), lambda: 4.0)
    assert over["ok"] is False and over.get("skip") == "truncated", over
    assert len(over["dirty_paths"]) <= gate.STATUS_DIRTY_CAP
    # hook level (cap lowered for speed): H11 and a tag
    root2 = make_project()
    seed_artifact(root2)
    t = mention_transcript(root2)
    for n in range(5):
        (root2 / ("g%d.txt" % n)).write_text("x", encoding="utf-8")
    main_inproc(root2, stop_payload(t, root2), STATUS_DIRTY_CAP=3)
    row = rows_of(root2)[-1]
    assert "H11_git_unsafe" in row["rule_hits"] and row["verdict"] == "needs_human", row
    assert "git_dirty_truncated" in row["settings_tags"], row


def test_r7_deadline_starvation_tagged_distinct_from_unsafe():
    root = make_project()
    seed_artifact(root)
    t = mention_transcript(root)
    starved = gate.git_facts(str(root), lambda: 0.5)
    assert starved["ok"] is False and starved.get("skip") == "deadline", starved
    main_inproc(root, stop_payload(t, root), DEADLINE_S=0.5)
    row = rows_of(root)[-1]
    assert "H11_git_unsafe" in row["rule_hits"] and row["verdict"] == "needs_human", row
    assert "git_skipped_deadline" in row["settings_tags"], row
    # a healthy run carries neither tag
    root2 = make_project()
    seed_artifact(root2)
    main_inproc(root2, stop_payload(mention_transcript(root2), root2))
    tags = rows_of(root2)[-1]["settings_tags"]
    assert "git_skipped_deadline" not in tags and "git_dirty_truncated" not in tags, tags


def test_r7_l1_not_fired_when_head_unknown():
    git = {"ok": False, "in_progress_op": None, "unmerged": False, "dirty_paths": [], "head": None}
    facts = dict(facts_for({"phase_cursor": "P2"}, stop_hook_active=True, git=git), last_human_ts="A")
    recorded = {"would_continue_since_human": 1, "last_human_ts": "A", "last_head": None, "last_cursor": "P2"}
    assert core.loop_guards(facts, recorded) == []
    assert core.loop_guards(facts, {"relay_pending": True, "last_human_ts": "A", "last_cursor": "P2"}) == []


def test_r8_interrupt_reply_labels_negative():
    for text in ("[Request interrupted by user]", "[Request interrupted by user for tool use]",
                 "  [Request interrupted by user]  "):
        assert core.label_reply(text) == "negative", text
    assert core.label_reply("continue") == "rubber_stamp"
    # still a genuine human line for the human-turn clock, and joined by the report
    rec = json.loads(_tline("user", "2026-10-01T10:02:00Z", "[Request interrupted by user]"))
    assert gate.is_genuine_human(rec) is True
    recs = [rec]
    assert core.label_reply(report.join_next_user_reply(recs, "2026-10-01T10:01:00Z")) == "negative"


def test_r9_nudge_private_names_contract():
    import craftflow_context_nudge_compact as cc
    for name in ("_has_gate", "_PHASE_RE", "_load_artifact", "payload_terminal", "resolve_active_workflow",
                 "WF_ID_RE"):
        assert hasattr(cc, name), "nudge module dropped " + name + " (stop-gate depends on it)"
    assert cc._has_gate({"kind": "user_build_approval"}) is True and cc._has_gate("user_build_approval") is True
    assert cc._has_gate({"kind": "none"}) is False and cc._has_gate("") is False and cc._has_gate(None) is False
    assert cc._PHASE_RE.fullmatch("P2") and cc._PHASE_RE.fullmatch("phase-2.1")
    assert cc._PHASE_RE.fullmatch("two words") is None and cc._PHASE_RE.fullmatch("") is None
    assert cc.WF_ID_RE.fullmatch("wf-abc-123") and cc.WF_ID_RE.fullmatch("abc") is None
    assert cc.WF_ID_RE.fullmatch("wf-x\n") is None
    assert cc.payload_terminal({"worktree_mode": "merged"}) is True
    assert cc.payload_terminal("not a dict") is True
    assert cc.payload_terminal({"phase_cursor": "P2", "workflow_type": "BUILD"}, isdir=lambda p: True) is False
    root = make_project()
    path = seed_artifact(root)
    mtime, size, payload = cc._load_artifact(str(path))
    assert isinstance(mtime, float) and size > 0 and payload["phase_cursor"] == "P2", (mtime, size, payload)
    assert cc._load_artifact(str(root / "missing.json")) is None
    t = mention_transcript(root)
    snapshot, reason, bound = cc.resolve_active_workflow(
        str(root / ".craftflow" / "state" / "workflows"), str(t), "s1", str(root), time.time())
    assert bound == WF_ID and reason in core.BOUND_REASONS, (snapshot, reason, bound)


def test_r10_label_coverage_and_jev_volume_in_report():
    out = report.build_report([], None)
    stats = out["go_criteria"]["stats"]
    assert out["unlabeled"] == 0 and "label_coverage" in stats and "jev_calls" in stats, out
    assert out["go_criteria"]["criteria"]["jev_failure_rate"]["met"] is False
    assert out["go_criteria"]["criteria"]["jev_calls_min"]["met"] is False
    sample = _run_report(["--events", "tests/fixtures/stop_gate/events-sample.jsonl",
                          "--transcripts-root", "tests/fixtures/stop_gate/transcripts"])
    got = json.loads(sample.stdout)
    assert got["unlabeled"] == got["rows"] - got["labeled"], got
    assert got["go_criteria"]["act"] == "NO-GO"


def test_r11_report_reads_tail_when_capped():
    scr = scratch_dir()
    rows = [core.build_row(row_kind="stop", ts="2026-10-01T08:0%d:00Z" % n, session_id="s%d" % n,
                           verdict=("would_continue" if n < 3 else "needs_human")) for n in range(6)]
    events = Path(scr, "events.jsonl")
    events.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    line = len(json.dumps(rows[0])) + 1
    with patched(report, MAX_EVENTS_BYTES=line * 2 + 5):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert report.main(["--events", str(events)]) == 0
    out = json.loads(buf.getvalue())
    assert out["truncated"] is True, out
    assert out["by_verdict"] == {"needs_human": 2}, out  # the newest rows survive, not the oldest
    full = io.StringIO()
    with contextlib.redirect_stdout(full):
        report.main(["--events", str(events)])
    assert json.loads(full.getvalue())["truncated"] is False


def test_r12_all_scripts_free_of_legacy_literal():
    needle = ".claude" + "/craftflow"
    for name in ("craftflow_stop_gate.py", "craftflow_stop_gate_core.py", "craftflow_stop_gate_report.py"):
        assert needle not in (SCRIPTS / name).read_text(encoding="utf-8"), name


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    print("test_craftflow_stop_gate: running")
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
