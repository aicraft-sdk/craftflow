#!/usr/bin/env python3
"""Tests for craftflow_stop_gate_core.py (SPEC-0018 / ADR-0055), pure core.

Run: python3 tests/fixtures/test_craftflow_stop_gate.py
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

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
    assert core.relay_decision(**relay_args(session_state=done, tail_sha="t2")) is True
    assert core.relay_decision(**relay_args(session_state=state, tail_sha="t2")) is False  # relay pending


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
