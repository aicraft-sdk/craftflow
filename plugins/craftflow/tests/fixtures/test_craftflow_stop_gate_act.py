#!/usr/bin/env python3
"""Tests for the stop gate ACT slice (SPEC-0019 / ADR-0056): off fast path (P2) and later phases.

Run: python3 tests/fixtures/test_craftflow_stop_gate_act.py
"""
from __future__ import annotations

import atexit
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
