#!/usr/bin/env python3
"""Tests for craftflow_context_nudge.py (SPEC-0016 / ADR-0051), pure core + state/transcript I/O.

Run: python3 tests/fixtures/test_craftflow_context_nudge.py
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import craftflow_context_nudge as cn  # noqa: E402
import craftflow_context_nudge_compact as cc  # noqa: E402

# DD-15: never read the developer's real ~/.claude/craftflow/context-nudge.json (absent path by default)
os.environ["CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG"] = str(
    Path(tempfile.gettempdir()) / ("cn-test-no-user-override-%d-absent.json" % os.getpid()))

_passes = 0
_errors = []

WARN, CRIT, WINDOW = 120000, 160000, 200000
CFG = {"warnTokens": WARN, "criticalTokens": CRIT, "assumedWindow": WINDOW}
LV = ("none", "warn", "critical")


def ok(name):
    global _passes
    _passes += 1
    print("  PASS: " + name)


def fail(name, reason):
    _errors.append("FAIL [" + name + "]: " + reason)
    print("  FAIL: " + name + ": " + reason)


def _assistant_line(total, model="claude-x", sidechain=False):
    row = {"type": "assistant", "message": {"model": model, "usage": {
        "input_tokens": total, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        "output_tokens": 1}}}
    if sidechain:
        row["isSidechain"] = True
    return json.dumps(row)


def _write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Subprocess helpers (DD-17 env isolation)
# ---------------------------------------------------------------------------

_BOXES = []


def _cleanup_boxes():
    for root in _BOXES:
        for dirpath, dirnames, _files in os.walk(root):
            for d in dirnames:
                try:
                    os.chmod(os.path.join(dirpath, d), 0o700)
                except OSError:
                    pass
        shutil.rmtree(root, ignore_errors=True)


atexit.register(_cleanup_boxes)


class _Box:
    """Temp plugin root + temp project + transcript dir for one scenario."""

    def __init__(self, mode, config=None):
        self.root = Path(tempfile.mkdtemp(prefix="cn-test-"))
        _BOXES.append(self.root)
        self.plugin = self.root / "plugin"
        self.project = self.root / "project"
        self.tdir = self.root / "transcripts"
        (self.plugin / "config").mkdir(parents=True)
        self.project.mkdir()
        self.tdir.mkdir()
        self.set_mode(mode)
        if config is not None:
            (self.plugin / "config" / "context-nudge.json").write_text(
                json.dumps(config), encoding="utf-8")

    def set_mode(self, mode):
        data = {} if mode is None else {"contextNudge": mode}
        (self.plugin / "config" / "hook-mode.json").write_text(json.dumps(data), encoding="utf-8")

    @property
    def state_dir(self):
        return self.project / ".craftflow" / "state" / "context-nudge"

    def transcript(self, tokens, name="t.jsonl", extra_rows=()):
        return write_transcript(self.tdir, tokens, extra_rows=extra_rows, name=name)


def write_transcript(directory, tokens, extra_rows=(), name="t.jsonl"):
    path = Path(directory) / name
    _write_lines(path, [_assistant_line(tokens)] + [json.dumps(r) for r in extra_rows])
    return path


def run_script(payload, *, mode="audit", config=None, args=(), env_extra=None, box=None, raw=None):
    """Run the script as a subprocess. Returns (code, stdout, stderr, rows, box)."""
    box = box or _Box(mode, config)
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update(CLAUDE_PLUGIN_ROOT=str(box.plugin), CLAUDE_PROJECT_DIR=str(box.project),
               PYTHONPATH=str(SCRIPTS))
    env.update(env_extra or {})
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPTS / "craftflow_context_nudge.py"), *args],
                          input=data, env=env, cwd=str(box.root), capture_output=True, timeout=10)
    return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"), log_rows(box), box)


def log_rows(box):
    log = box.project / ".craftflow" / "state" / "craftflow-hook-events.log"
    rows = []
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("event") == "context_nudge":
                rows.append(row)
    return rows


def _prompt(box_or_path, sid="s1", **extra):
    path = box_or_path if not isinstance(box_or_path, _Box) else box_or_path.tdir / "t.jsonl"
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": sid,
               "transcript_path": str(path), "prompt": "hi"}
    payload.update(extra)
    return payload


def _fs_snapshot(root):
    snap = {}
    for dirpath, dirnames, files in os.walk(root):
        for d in dirnames:
            snap[os.path.join(dirpath, d)] = "dir"
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                snap[p] = hashlib.sha256(fh.read()).hexdigest()
    return snap


# ---------------------------------------------------------------------------
# classify / rank / resolve_mode
# ---------------------------------------------------------------------------

def test_classify_boundaries():
    c = lambda t: cn.classify(t, WARN, CRIT)  # noqa: E731
    assert c(119999) == "none", c(119999)
    assert c(120000) == "warn", c(120000)
    assert c(159999) == "warn", c(159999)
    assert c(160000) == "critical", c(160000)
    assert c(10 ** 9) == "critical"
    for bad in (None, -1, 0, True, 1.5e5, "150000", [], {}):
        assert c(bad) == "none", (bad, c(bad))


def test_rank_orders_levels_and_unknown_is_zero():
    assert [cn.rank(x) for x in LV] == [0, 1, 2]
    assert cn.rank("bogus") == 0 and cn.rank(None) == 0 and cn.rank(5) == 0


def test_resolve_mode_matrix():
    r = cn.resolve_mode
    assert r("off") == ("off", "off")
    assert r("on") == ("on", "on")
    assert r("audit") == ("audit", "audit")
    assert r(None) == ("audit", "audit")
    assert r("On") == ("audit", "audit-unrecognized-config-value")
    assert r(5) == ("audit", "audit-unrecognized-config-value")


# ---------------------------------------------------------------------------
# decide / decide_boundary
# ---------------------------------------------------------------------------

def _decide_oracle(level, last, mode):
    if mode == "off":
        return "off"
    if level > last:
        return "nudge" if mode == "on" else "would_nudge"
    if level < last:
        return "rearmed"
    return "below_threshold" if level == 0 else "suppressed"


def test_decide_exhaustive_table():
    n = 0
    for mode in ("on", "audit", "off"):
        for level in range(3):
            for last in range(3):
                got = cn.decide(LV[level], LV[last], mode)
                want = _decide_oracle(level, last, mode)
                assert got == {"action": want, "level": LV[level], "new_last_level": LV[level]}, \
                    (mode, LV[level], LV[last], got, want)
                n += 1
    assert n == 27


def test_decide_none_to_critical_single_nudge():
    d = cn.decide("critical", "none", "on")
    assert d == {"action": "nudge", "level": "critical", "new_last_level": "critical"}, d
    d2 = cn.decide("critical", d["new_last_level"], "on")
    assert d2["action"] == "suppressed", d2


def test_decide_shrink_rearms_then_renudges():
    d = cn.decide("warn", "critical", "on")
    assert d["action"] == "rearmed" and d["new_last_level"] == "warn", d
    d = cn.decide("critical", d["new_last_level"], "on")
    assert d["action"] == "nudge", d


def _boundary_oracle(level, bl, mode):
    if mode == "off":
        return False, "off", bl
    if level == 0:
        return False, "below_threshold", bl
    if level > bl:
        if mode == "on":
            return True, "advised", LV[level]
        return False, "would_advise", LV[bl]
    return False, "already_advised", LV[bl]


def test_decide_boundary_exhaustive_table():
    n = 0
    for mode in ("on", "audit", "off"):
        for level in range(3):
            for bl in range(3):
                relay, outcome, nbl = _boundary_oracle(level, bl, mode)
                if mode == "off":
                    nbl = LV[bl]
                if outcome == "below_threshold":
                    nbl = LV[bl]
                got = cn.decide_boundary(LV[level], LV[bl], mode, True)
                assert got == {"relay": relay, "new_boundary_level": nbl, "outcome": outcome}, \
                    (mode, LV[level], LV[bl], got)
                n += 1
    assert n == 27


def test_decide_boundary_unmeasured_never_relays():
    for mode in ("on", "audit", "off"):
        for level in LV:
            for bl in LV:
                got = cn.decide_boundary(level, bl, mode, False)
                assert got["relay"] is False and got["outcome"] == "no_session_state", got
                assert got["new_boundary_level"] == bl, got


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------

def _sequence(rng):
    return [rng.randint(0, 250000) for _ in range(rng.randint(1, 40))]


def test_property_at_most_one_nudge_per_arming():
    rng = random.Random(1234)
    for _ in range(200):
        seq = _sequence(rng)
        last = "none"
        nudges = {"warn": 0, "critical": 0}
        drops = {"warn": 0, "critical": 0}
        for t in seq:
            level = cn.classify(t, WARN, CRIT)
            for lv in ("warn", "critical"):
                if cn.rank(level) < cn.rank(lv) <= cn.rank(last):
                    drops[lv] += 1
            d = cn.decide(level, last, "on")
            if d["action"] == "nudge":
                nudges[level] += 1
            last = d["new_last_level"]
        for lv in ("warn", "critical"):
            assert nudges[lv] <= 1 + drops[lv], (seq, lv, nudges, drops)


def test_property_boundary_relays_strictly_increasing():
    rng = random.Random(1234)
    for _ in range(200):
        seq = _sequence(rng)
        last, bl = "none", "none"
        relays = []
        for t in seq:
            level = cn.classify(t, WARN, CRIT)
            d = cn.decide(level, last, "on")
            if d["action"] == "rearmed":
                bl = level if cn.rank(level) < cn.rank(bl) else bl
            last = d["new_last_level"]
            b = cn.decide_boundary(level, bl, "on", True)
            if b["relay"]:
                relays.append(level)
            bl = b["new_boundary_level"]
        # relays are strictly increasing between shrinks: each relay must exceed the prior level
        # unless a shrink re-armed (boundary_level dropped) in between
        assert all(r in ("warn", "critical") for r in relays), relays
    for _ in range(200):
        seq = sorted(_sequence(rng))
        bl, relays = "none", []
        for t in seq:
            b = cn.decide_boundary(cn.classify(t, WARN, CRIT), bl, "on", True)
            if b["relay"]:
                relays.append(cn.rank(b["new_boundary_level"]))
            bl = b["new_boundary_level"]
        assert relays == sorted(set(relays)) and len(relays) <= 2, relays


def test_property_classify_monotone():
    rng = random.Random(1234)
    for _ in range(200):
        a, b = sorted((rng.randint(0, 300000), rng.randint(0, 300000)))
        assert cn.rank(cn.classify(a, WARN, CRIT)) <= cn.rank(cn.classify(b, WARN, CRIT)), (a, b)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def test_validate_config_matrix():
    v = cn.validate_config
    assert v(dict(CFG)) == (CFG, None)
    bad = [
        None, [], "x", 5,
        {"warnTokens": True, "criticalTokens": CRIT, "assumedWindow": WINDOW},
        {"warnTokens": 1.5e5, "criticalTokens": CRIT, "assumedWindow": WINDOW},
        {"warnTokens": "1", "criticalTokens": CRIT, "assumedWindow": WINDOW},
        {"warnTokens": 0, "criticalTokens": CRIT, "assumedWindow": WINDOW},
        {"warnTokens": -5, "criticalTokens": CRIT, "assumedWindow": WINDOW},
        {"warnTokens": 160000, "criticalTokens": 160000, "assumedWindow": WINDOW},
        {"warnTokens": 170000, "criticalTokens": 160000, "assumedWindow": WINDOW},
        {"warnTokens": WARN, "criticalTokens": CRIT, "assumedWindow": 150000},
        {"warnTokens": WARN, "criticalTokens": CRIT},
    ]
    for obj in bad:
        assert v(obj) == (None, "config_invalid"), (obj, v(obj))
    assert v({"warnTokens": WARN, "criticalTokens": CRIT, "assumedWindow": CRIT})[1] is None


def test_load_config_missing_defaults_and_corrupt_invalid():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "context-nudge.json"
        assert cn.load_config(p) == (CFG, None)
        p.write_text("{not json", encoding="utf-8")
        assert cn.load_config(p) == (None, "config_invalid")
        p.write_text(json.dumps({"warnTokens": 5, "criticalTokens": 9, "assumedWindow": 10}), encoding="utf-8")
        assert cn.load_config(p) == ({"warnTokens": 5, "criticalTokens": 9, "assumedWindow": 10}, None)
        p.write_text("[]", encoding="utf-8")
        assert cn.load_config(p) == (None, "config_invalid")


def test_safe_ids_matrix():
    for bad in ("../x", "a/b", "", "x" * 129, 5, None, "a b"):
        assert cn.safe_session_id(bad) is None, bad
    assert cn.safe_session_id("abc-123_X") == "abc-123_X"
    assert cn.safe_session_id("x" * 128) == "x" * 128
    for bad in ("../x", "wf/a", "", "x", 5, None, "wf-" + "a" * 161):
        assert cn.safe_wf(bad) is None, bad
    assert cn.safe_wf("wf-ok-1") == "wf-ok-1"


# ---------------------------------------------------------------------------
# advisory / boundary_result
# ---------------------------------------------------------------------------

def test_render_advisory_single_line_and_mentions_compact():
    w = cn.render_advisory("warn", 123456, CFG)
    c = cn.render_advisory("critical", 165432, CFG)
    for text, tok in ((w, "123,456"), (c, "165,432")):
        assert "\n" not in text and "/compact" in text and tok in text, text
        assert text.startswith("CRAFTFLOW context advisory:"), text
    assert "warn threshold 120,000" in w and "assumed window 200,000" in w
    assert "critical threshold 160,000" in c and "before starting new work" in c
    assert cn.render_advisory("none", 5, CFG) == ""


def test_boundary_result_fills_every_key():
    r = cn.boundary_result(mode="on", level="warn")
    assert set(r) == {"schema", "mode", "level", "tokens", "threshold", "assumed_window", "relay",
                      "advisory", "checkpoint_path", "session_id", "session_source", "phase",
                      "outcome", "error"}, sorted(r)
    assert r["schema"] == 1 and r["relay"] is False and r["mode"] == "on" and r["tokens"] is None
    assert cn.boundary_result(relay=True)["relay"] is True


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def test_state_roundtrip_atomic_no_temp_leftovers():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td) / "nested" / "context-nudge"
        p = cn.state_path(sd, "sess1")
        assert p == sd / "sess1.json"
        st = {"schema": 1, "session_id": "sess1", "last_level": "warn", "boundary_level": "none",
              "last_tokens": 130000, "transcript_path": "/x.jsonl", "updated_at": 1.0}
        assert cn.save_state(p, st) is True
        assert cn.load_state(p) == st
        assert sorted(os.listdir(sd)) == ["sess1.json"], os.listdir(sd)
        st["last_level"] = "critical"
        assert cn.save_state(p, st) is True
        assert cn.load_state(p)["last_level"] == "critical"
        assert sorted(os.listdir(sd)) == ["sess1.json"]


def test_state_corrupt_or_unknown_level_is_none():
    base = {"last_level": "none", "boundary_level": "none"}
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "s.json"
        assert cn.load_state(p) == base  # missing
        for content in ("{oops", "[]", "3", '"x"'):
            p.write_text(content, encoding="utf-8")
            assert cn.load_state(p) == base, content
        p.write_text(json.dumps({"last_level": "panic", "boundary_level": 7, "last_tokens": 9}), encoding="utf-8")
        got = cn.load_state(p)
        assert got["last_level"] == "none" and got["boundary_level"] == "none" and got["last_tokens"] == 9, got


def test_save_state_unwritable_returns_false():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return  # root ignores mode bits
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "ro"
        d.mkdir()
        os.chmod(d, 0o500)
        try:
            assert cn.save_state(d / "s.json", {"last_level": "warn"}) is False
            assert os.listdir(d) == []
        finally:
            os.chmod(d, 0o700)


# ---------------------------------------------------------------------------
# transcript measurement
# ---------------------------------------------------------------------------

def test_current_context_tokens_tail_reads_last_usage():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.jsonl"
        filler = [json.dumps({"type": "user", "message": {"content": "x" * 900}}) for _ in range(330)]
        _write_lines(p, [_assistant_line(999)] + filler + [_assistant_line(4321)])
        assert p.stat().st_size > cn.TAIL_BYTES
        r = cn.current_context_tokens(str(p))
        assert r["tokens"] == 4321 and r["source"] == "assistant" and r["error"] is None, r
        assert r["model"] == "claude-x", r


def test_current_context_tokens_retry_window():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.jsonl"
        big = json.dumps({"type": "user", "message": {"content": "y" * 400000}})
        _write_lines(p, [_assistant_line(777), big])
        r = cn.current_context_tokens(str(p))
        assert r["tokens"] == 777 and r["error"] is None, r


def test_current_context_tokens_error_codes():
    f = cn.current_context_tokens
    assert f(None)["error"] == "no_transcript_path" and f(None)["tokens"] is None
    assert f(5)["error"] == "no_transcript_path"
    with tempfile.TemporaryDirectory() as td:
        assert f(str(Path(td) / "nope.jsonl"))["error"] == "transcript_missing"
        assert f(td)["error"] == "transcript_not_regular_jsonl"
        txt = Path(td) / "t.txt"
        txt.write_text("{}", encoding="utf-8")
        assert f(str(txt))["error"] == "transcript_not_regular_jsonl"
        empty = Path(td) / "e.jsonl"
        _write_lines(empty, [json.dumps({"type": "user"}), _assistant_line(0), _assistant_line(50, model="<synthetic>")])
        r = f(str(empty))
        assert r["tokens"] is None and r["error"] == "no_usage", r
        side = Path(td) / "s.jsonl"
        _write_lines(side, [_assistant_line(5000, sidechain=True)])
        assert f(str(side))["error"] == "no_usage"
        cb = Path(td) / "c.jsonl"
        _write_lines(cb, [_assistant_line(9000),
                          json.dumps({"type": "system", "subtype": "compact_boundary"})])
        r = f(str(cb))
        assert r["tokens"] is None and r["source"] == "compact_boundary" and r["error"] == "post_tokens_missing", r
        cb2 = Path(td) / "c2.jsonl"
        _write_lines(cb2, [_assistant_line(9000), json.dumps(
            {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"postTokens": 25286}})])
        r = f(str(cb2))
        assert r["tokens"] == 25286 and r["source"] == "compact_boundary" and r["error"] is None, r


def test_read_tail_lines_drops_truncated_first_line():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.jsonl"
        p.write_bytes(b"AAAAAAAAAA\nBBBB\nCCCC\n")
        assert cn.read_tail_lines(str(p), 100) == ["AAAAAAAAAA", "BBBB", "CCCC"]
        assert cn.read_tail_lines(str(p), 11) == ["BBBB", "CCCC"]
        p.write_bytes(b"ok\n\xff\xfe bad\nlast\n")
        got = cn.read_tail_lines(str(p), 100)
        assert got[0] == "ok" and got[-1] == "last" and len(got) == 3, got


# ---------------------------------------------------------------------------
# totality / import hygiene / stub
# ---------------------------------------------------------------------------

def test_totality_never_raises():
    junk = [None, [], {}, "x", 10 ** 20, -1, True, 1.5, b"b", object()]
    fns = [
        lambda a: cn.rank(a), lambda a: cn.classify(a, WARN, CRIT), lambda a: cn.classify(1, a, a),
        lambda a: cn.resolve_mode(a), lambda a: cn.decide(a, a, a),
        lambda a: cn.decide_boundary(a, a, a, a), lambda a: cn.validate_config(a),
        lambda a: cn.safe_session_id(a), lambda a: cn.safe_wf(a),
        lambda a: cn.render_advisory(a, a, a), lambda a: cn.render_advisory("warn", a, CFG),
        lambda a: cn.boundary_result(mode=a, level=a), lambda a: cn.load_state(a),
        lambda a: cn.current_context_tokens(a), lambda a: cn.load_config(a),
    ]
    for f in fns:
        for j in junk:
            f(j)  # must not raise
    assert isinstance(cn.render_advisory("critical", None, None), str)


def test_import_constants_and_main_returns_zero_on_empty_stdin():
    # P4 changed: main() is the real entry point now (reads stdin), so it runs with an
    # empty stdin and a sandboxed project/plugin instead of the Phase 3 no-op stub.
    import io
    assert cn.SCHEMA_VERSION == 1 and cn.LEVELS == ("none", "warn", "critical")
    assert cn.TAIL_BYTES == 262144 and cn.RETRY_TAIL_BYTES == 4194304
    assert cn.BOUNDARY_STATE_MAX_AGE_S == 43200 and cn.STATE_PRUNE_AGE_S == 1209600
    assert cn.STATE_DIRNAME == "context-nudge"
    box = _Box("audit")
    saved_env = {k: os.environ.get(k) for k in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR")}
    saved_stdin = sys.stdin
    try:
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(box.plugin)
        os.environ["CLAUDE_PROJECT_DIR"] = str(box.project)
        sys.stdin = io.StringIO("")
        assert cn.main([]) == 0
    finally:
        sys.stdin = saved_stdin
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# Phase 4: hook_main / reset_main / main (subprocess-proven)
# ---------------------------------------------------------------------------

def _outcomes(rows):
    return [r.get("outcome") for r in rows]


def test_hook_off_mode_inert_fs_snapshot():
    box = _Box("off")
    box.transcript(130000)
    before = _fs_snapshot(box.root)
    code, out, _err, rows, _ = run_script(_prompt(box), box=box)
    assert code == 0 and out == "", (code, out)
    assert rows == []
    assert _fs_snapshot(box.root) == before


def test_hook_audit_mode_logs_would_nudge_no_stdout():
    box = _Box("audit")
    box.transcript(130000)
    code, out, _err, rows, _ = run_script(_prompt(box), box=box)
    assert code == 0 and out == "", (code, out)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["outcome"] == "would_nudge" and row["level"] == "warn" and row["tokens"] == 130000
    assert row["mode"] == "audit" and row["session_id"] == "s1" and row["threshold"] == WARN
    assert row["last_level"] == "none" and row["source"] == "hook" and row["error"] is None


def test_hook_missing_mode_key_defaults_audit():
    box = _Box(None)
    box.transcript(130000)
    code, out, _err, rows, _ = run_script(_prompt(box), box=box)
    assert code == 0 and out == ""
    assert _outcomes(rows) == ["would_nudge"] and rows[0]["mode"] == "audit"


def test_hook_on_mode_nudges_once_then_silent():
    box = _Box("on")
    box.transcript(130000)
    code, out, _err, rows, _ = run_script(_prompt(box), box=box)
    assert code == 0
    payload = json.loads(out)
    assert set(payload) == {"hookSpecificOutput", "systemMessage"}, payload
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert payload["systemMessage"] == payload["hookSpecificOutput"]["additionalContext"]
    assert "130,000" in payload["systemMessage"]
    assert out.count("\n") == 1
    assert _outcomes(rows) == ["nudged"]
    code2, out2, _e2, rows2, _ = run_script(_prompt(box), box=box)
    assert code2 == 0 and out2 == ""
    assert rows2 == rows, "second identical prompt must not add a row (P6)"


def test_hook_below_threshold_writes_state_no_row():
    box = _Box("on")
    tp = box.transcript(50000)
    code, out, _err, rows, _ = run_script(_prompt(box), box=box)
    assert code == 0 and out == "" and rows == []
    state = json.loads((box.state_dir / "s1.json").read_text(encoding="utf-8"))
    assert state["last_level"] == "none" and state["boundary_level"] == "none"
    assert state["transcript_path"] == str(tp) and state["session_id"] == "s1"
    assert state["schema"] == 1 and state["last_tokens"] == 50000


def test_hook_on_mode_warn_then_critical_nudges_again():
    box = _Box("on")
    box.transcript(130000)
    _c, out1, _e, _r, _ = run_script(_prompt(box), box=box)
    assert json.loads(out1)["systemMessage"]
    box.transcript(170000)
    _c, out2, _e, rows, _ = run_script(_prompt(box), box=box)
    assert "critical" in json.loads(out2)["systemMessage"]
    assert _outcomes(rows) == ["nudged", "nudged"]
    assert [r["level"] for r in rows] == ["warn", "critical"]
    assert rows[1]["last_level"] == "warn"


def test_hook_none_to_critical_single_nudge_at_critical():
    box = _Box("on")
    box.transcript(170000)
    _c, out, _e, rows, _ = run_script(_prompt(box), box=box)
    assert "critical" in json.loads(out)["systemMessage"]
    assert _outcomes(rows) == ["nudged"] and rows[0]["level"] == "critical"
    assert rows[0]["threshold"] == CRIT


def test_hook_on_mode_shrink_rearms_then_renudges():
    box = _Box("on")
    box.transcript(170000)
    run_script(_prompt(box), box=box)
    box.transcript(50000)
    _c, out, _e, rows, _ = run_script(_prompt(box), box=box)
    assert out == "" and _outcomes(rows) == ["nudged", "rearmed"]
    box.transcript(130000)
    _c, out, _e, rows, _ = run_script(_prompt(box), box=box)
    assert json.loads(out)["systemMessage"]
    assert _outcomes(rows) == ["nudged", "rearmed", "nudged"]


def test_hook_post_compact_boundary_does_not_renudge():
    box = _Box("on")
    box.transcript(130000)
    _c, out, _e, rows, _ = run_script(_prompt(box), box=box)
    assert json.loads(out)["systemMessage"] and _outcomes(rows) == ["nudged"]
    reset = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "s1"}
    code, rout, _e, _r, _ = run_script(reset, box=box, args=("--reset",))
    assert code == 0 and rout == ""
    box.transcript(130000, extra_rows=[{"type": "system", "subtype": "compact_boundary",
                                        "compactMetadata": {"preTokens": 130000, "postTokens": 20000}}])
    code, out, _e, rows, _ = run_script(_prompt(box), box=box)
    assert code == 0 and out == ""
    assert _outcomes(rows).count("nudged") == 1, rows


def test_hook_unrecognized_mode_behaves_as_audit():
    box = _Box("On")
    box.transcript(130000)
    code, out, _err, rows, _ = run_script(_prompt(box), box=box)
    assert code == 0 and out == ""
    assert _outcomes(rows) == ["would_nudge"]
    assert rows[0]["mode"] == "audit-unrecognized-config-value"


def test_hook_fail_open_matrix():
    def case(label, expect_outcome, expect_error, payload=None, raw=None, config=None, mode="on"):
        box = _Box(mode, config)
        code, out, _err, rows, _ = run_script(payload, box=box, raw=raw)
        assert code == 0 and out == "", (label, code, out)
        if expect_outcome is None:
            assert rows == [], (label, rows)
        else:
            assert len(rows) == 1, (label, rows)
            assert rows[0]["outcome"] == expect_outcome and rows[0]["error"] == expect_error, (label, rows)
        return box

    good = Path(tempfile.mkdtemp(prefix="cn-good-"))
    _BOXES.append(good)
    tp = write_transcript(good, 130000)
    case("E11 missing", "skipped", "transcript_missing", _prompt(good / "nope.jsonl"))
    (good / "dir.jsonl").mkdir()
    case("E11 dir", "error", "transcript_not_regular_jsonl", _prompt(good / "dir.jsonl"))
    for label, raw in (("empty", b""), ("nonjson", b"not json"), ("list", b"[1]"), ("badutf8", b"\xff\xfe")):
        case("E12 " + label, "error", "bad_session_id", raw=raw)
    case("E12b", "error", "no_transcript_path", {"session_id": "s1"})
    for label, sid in (("missing", None), ("int", 7), ("slash", "../x"), ("long", "a" * 129), ("empty", "")):
        payload = _prompt(tp)
        if sid is None:
            payload.pop("session_id")
        else:
            payload["session_id"] = sid
        case("E13 " + label, "error", "bad_session_id", payload)
    case("E17", "error", "config_invalid", _prompt(tp),
         config={"warnTokens": 5, "criticalTokens": 5, "assumedWindow": 10})
    case("E20", None, None, dict(_prompt(tp), hook_event_name="PreToolUse"))
    case("no-usage", "skipped", "no_usage", _prompt(_write_only(good, "empty.jsonl", ['{"type":"user"}'])))


def _write_only(directory, name, lines):
    path = Path(directory) / name
    _write_lines(path, lines)
    return path


def test_hook_state_dir_unwritable_still_nudges_once():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return  # root ignores directory permissions: skip-as-pass
    box = _Box("on")
    box.transcript(130000)
    box.state_dir.mkdir(parents=True)
    os.chmod(box.state_dir, 0o500)
    try:
        code, out, _err, rows, _ = run_script(_prompt(box), box=box)
        assert code == 0
        assert json.loads(out)["systemMessage"], out
        assert len(rows) == 1 and rows[0]["outcome"] == "nudged", rows
        assert rows[0]["error"] == "state_write_failed", rows
        assert os.listdir(box.state_dir) == [], os.listdir(box.state_dir)
    finally:
        os.chmod(box.state_dir, 0o700)


def test_hook_log_row_event_name_not_clobbered():
    box = _Box("audit")
    box.transcript(130000)
    run_script(_prompt(box), box=box)
    log = box.project / ".craftflow" / "state" / "craftflow-hook-events.log"
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert lines and all(x["event"] == "context_nudge" for x in lines), lines
    assert lines[0]["outcome"] == "would_nudge"


def test_reset_compact_deletes_state_then_renudges():
    box = _Box("on")
    box.transcript(130000)
    run_script(_prompt(box), box=box)
    assert (box.state_dir / "s1.json").exists()
    reset = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "s1"}
    code, out, _err, rows, _ = run_script(reset, box=box, args=("--reset",))
    assert code == 0 and out == ""
    assert not (box.state_dir / "s1.json").exists()
    reset_rows = [r for r in rows if r["outcome"] == "reset"]
    assert len(reset_rows) == 1 and reset_rows[0]["had_state"] is True
    assert reset_rows[0]["source"] == "reset" and reset_rows[0]["session_id"] == "s1"
    _c, out2, _e, rows2, _ = run_script(_prompt(box), box=box)
    assert json.loads(out2)["systemMessage"]
    assert _outcomes(rows2) == ["nudged", "reset", "nudged"]


def test_reset_non_compact_source_noop():
    box = _Box("on")
    box.transcript(130000)
    run_script(_prompt(box), box=box)
    before = _fs_snapshot(box.state_dir)
    for src in ("startup", "resume", None):
        payload = {"hook_event_name": "SessionStart", "session_id": "s1"}
        if src:
            payload["source"] = src
        code, out, _err, rows, _ = run_script(payload, box=box, args=("--reset",))
        assert code == 0 and out == ""
        assert _outcomes(rows) == ["nudged"]
    assert _fs_snapshot(box.state_dir) == before


def test_reset_bad_session_id_logs_error_and_off_is_inert():
    box = _Box("on")
    payload = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "../x"}
    code, out, _err, rows, _ = run_script(payload, box=box, args=("--reset",))
    assert code == 0 and out == ""
    assert len(rows) == 1 and rows[0]["outcome"] == "error" and rows[0]["error"] == "bad_session_id"
    off = _Box("off")
    before = _fs_snapshot(off.root)
    code, out, _err, rows, _ = run_script(dict(payload, session_id="s1"), box=off, args=("--reset",))
    assert code == 0 and out == "" and rows == [] and _fs_snapshot(off.root) == before


def test_reset_prunes_old_state_files():
    box = _Box("on")
    box.state_dir.mkdir(parents=True)
    now = time.time()
    files = {"old.json": 15 * 86400, "recent.json": 86400, "checkpoint-x.json": 15 * 86400}
    for name, age in files.items():
        p = box.state_dir / name
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (now - age, now - age))
    reset = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "s1"}
    code, out, _err, rows, _ = run_script(reset, box=box, args=("--reset",))
    assert code == 0 and out == ""
    assert sorted(os.listdir(box.state_dir)) == ["checkpoint-x.json", "recent.json"]
    assert rows[0]["outcome"] == "reset" and rows[0]["had_state"] is False


# ---------------------------------------------------------------------------
# --boundary (Phase 5)
# ---------------------------------------------------------------------------

WF = "wf-t-0001"


def _seed_wf(box, wf=WF, payload=None):
    d = box.project / ".craftflow" / "state" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    body = payload if payload is not None else {
        "workflow_uuid": wf, "workflow_type": "BUILD", "phase_cursor": "P1",
        "phase_status": {"P1": "in_progress"}, "plan_file": "docs/plans/x.md"}
    (d / (wf + ".json")).write_text(json.dumps(body), encoding="utf-8")


def _seed_session(box, sid, tokens, name=None, **state_extra):
    tpath = box.transcript(tokens, name=name or (sid + ".jsonl"))
    state = {"schema": 1, "session_id": sid, "last_level": "none", "boundary_level": "none",
             "last_tokens": tokens, "transcript_path": str(tpath), "updated_at": "2026-01-01T00:00:00Z"}
    state.update(state_extra)
    assert cn.save_state(box.state_dir / (sid + ".json"), state)
    return tpath


def _bnd(box, *args, env_extra=None, wf=WF, session="s1"):
    """Run --boundary; assert exit 0 + exactly one JSON line; return the parsed dict."""
    argv = ["--boundary"]
    if wf is not None:
        argv += ["--wf", wf]
    if session is not None:
        argv += ["--session-id", session]
    argv += list(args)
    argv += ["--project-root", str(box.project)]
    code, out, _err, _rows, _ = run_script({}, box=box, args=tuple(argv), env_extra=env_extra)
    assert code == 0, code
    assert out.endswith("\n") and out.count("\n") == 1, repr(out)
    parsed = json.loads(out)
    assert parsed["schema"] == 1 and isinstance(parsed["relay"], bool), parsed
    return parsed


def _checkpoint(box, wf=WF):
    return box.state_dir / ("checkpoint-" + wf + ".json")


def test_boundary_on_over_warn_relays_and_checkpoints():
    box = _Box("on")
    _seed_wf(box)
    _seed_session(box, "s1", 130000)
    r = _bnd(box, "--phase", "P1")
    assert r["relay"] is True and r["level"] == "warn" and r["mode"] == "on", r
    assert r["tokens"] == 130000 and r["threshold"] == WARN and r["assumed_window"] == WINDOW, r
    assert r["outcome"] == "advised" and r["error"] is None, r
    assert r["session_id"] == "s1" and r["session_source"] == "arg" and r["phase"] == "P1", r
    assert isinstance(r["advisory"], str) and "/compact" in r["advisory"], r
    assert r["checkpoint_path"] == str(_checkpoint(box)), r
    snap = json.loads(_checkpoint(box).read_text(encoding="utf-8"))
    assert snap["source"] == "phase_boundary" and snap["trigger"] == "phase_boundary", snap
    assert snap["workflow_uuid"] == WF and snap["phase"] == "P1" and snap["session_id"] == "s1", snap
    assert snap["context_tokens"] == 130000 and snap["context_level"] == "warn", snap
    assert snap["context_usage"] == {"percent_full": 65.0, "total": 130000, "model_context": WINDOW,
                                     "source": "transcript"}, snap["context_usage"]
    assert snap["phase_cursor"] == "P1" and snap["plan_file"] == "docs/plans/x.md", snap
    state = json.loads((box.state_dir / "s1.json").read_text(encoding="utf-8"))
    assert state["boundary_level"] == "warn", state


def test_boundary_second_call_same_level_already_advised():
    box = _Box("on")
    _seed_wf(box)
    _seed_session(box, "s1", 130000)
    assert _bnd(box, "--phase", "P1")["relay"] is True
    r = _bnd(box, "--phase", "P2")
    assert r["relay"] is False and r["outcome"] == "already_advised" and r["level"] == "warn", r
    assert r["advisory"] is None and r["checkpoint_path"] == str(_checkpoint(box)), r


def test_boundary_escalates_warn_then_critical():
    box = _Box("on")
    _seed_wf(box)
    tpath = _seed_session(box, "s1", 130000)
    assert _bnd(box)["level"] == "warn"
    _write_lines(tpath, [_assistant_line(170000)])
    r = _bnd(box)
    assert r["relay"] is True and r["level"] == "critical" and r["outcome"] == "advised", r
    assert r["threshold"] == CRIT and r["tokens"] == 170000, r
    r = _bnd(box)
    assert r["relay"] is False and r["outcome"] == "already_advised", r


def test_boundary_audit_checkpoints_but_never_relays():
    box = _Box("audit")
    _seed_wf(box)
    _seed_session(box, "s1", 130000)
    r = _bnd(box, "--phase", "P1")
    assert r["relay"] is False and r["outcome"] == "would_advise" and r["level"] == "warn", r
    assert r["advisory"] is None and r["mode"] == "audit", r
    assert _checkpoint(box).exists() and r["checkpoint_path"] == str(_checkpoint(box)), r
    state = json.loads((box.state_dir / "s1.json").read_text(encoding="utf-8"))
    assert state["boundary_level"] == "none", state


def test_boundary_below_threshold_no_relay():
    box = _Box("on")
    _seed_wf(box)
    _seed_session(box, "s1", 1000)
    r = _bnd(box)
    assert r["relay"] is False and r["level"] == "none" and r["outcome"] == "below_threshold", r
    assert r["tokens"] == 1000 and r["threshold"] is None, r
    assert _checkpoint(box).exists(), r


def test_boundary_off_mode_no_checkpoint_no_relay():
    box = _Box("off")
    _seed_wf(box)
    _seed_session(box, "s1", 130000)
    before = _fs_snapshot(box.root)
    r = _bnd(box, "--phase", "P1")
    assert r["mode"] == "off" and r["outcome"] == "off" and r["relay"] is False, r
    assert r["checkpoint_path"] is None and r["advisory"] is None, r
    assert _fs_snapshot(box.root) == before
    assert not _checkpoint(box).exists() and log_rows(box) == []


def test_boundary_missing_and_corrupt_workflow():
    cases = [("missing", None, "workflow_missing"), ("corrupt", "{bad", "workflow_unreadable"),
             ("list", "[]", "workflow_unreadable"), ("str", '"x"', "workflow_unreadable"),
             ("int", "3", "workflow_unreadable")]
    for label, raw, want in cases:
        box = _Box("on")
        _seed_session(box, "s1", 130000)
        if raw is not None:
            wdir = box.project / ".craftflow" / "state" / "workflows"
            wdir.mkdir(parents=True)
            (wdir / (WF + ".json")).write_text(raw, encoding="utf-8")
        r = _bnd(box)
        assert r["checkpoint_path"] is None and r["error"] == want, (label, r)
        assert r["relay"] is True and r["level"] == "warn" and r["outcome"] == "advised", (label, r)
        assert not _checkpoint(box).exists(), label


def test_boundary_bad_wf_rejected():
    box = _Box("on")
    _seed_wf(box)
    (box.project / ".craftflow" / "state" / "x.json").write_text("{}", encoding="utf-8")
    _seed_session(box, "s1", 130000)
    r = _bnd(box, wf="../x")
    assert r["error"] == "bad_wf" and r["checkpoint_path"] is None, r
    assert r["relay"] is True, r
    assert sorted(os.listdir(box.state_dir)) == ["s1.json"], os.listdir(box.state_dir)
    r = _bnd(box, wf=None)
    assert r["error"] == "bad_wf" and r["checkpoint_path"] is None, r


def test_boundary_session_resolution_order():
    box = _Box("on")
    _seed_wf(box)
    _seed_session(box, "sarg", 130000)
    _seed_session(box, "senv", 130000)
    _seed_session(box, "smtime", 130000)
    r = _bnd(box, session="sarg", env_extra={"CLAUDE_CODE_SESSION_ID": "senv"})
    assert r["session_id"] == "sarg" and r["session_source"] == "arg", r
    r = _bnd(box, session=None, env_extra={"CLAUDE_CODE_SESSION_ID": "senv"})
    assert r["session_id"] == "senv" and r["session_source"] == "env", r
    # explicit ids without a state file fall through to the next source
    r = _bnd(box, session="missing", env_extra={"CLAUDE_CODE_SESSION_ID": "senv"})
    assert r["session_id"] == "senv" and r["session_source"] == "env", r
    now = time.time()
    os.utime(box.state_dir / "sarg.json", (now - 7200, now - 7200))
    os.utime(box.state_dir / "senv.json", (now - 3600, now - 3600))
    os.utime(box.state_dir / "smtime.json", (now - 60, now - 60))
    r = _bnd(box, session=None, env_extra={"CLAUDE_CODE_SESSION_ID": "unknown"})
    assert r["session_id"] == "smtime" and r["session_source"] == "mtime_fallback", r
    # a newer checkpoint-* file must never be picked as a session
    _bnd(box, session="smtime")
    assert _checkpoint(box).exists()
    r = _bnd(box, session=None)
    assert r["session_id"] == "smtime" and r["session_source"] == "mtime_fallback", r


def test_boundary_no_or_stale_session_state():
    box = _Box("on")
    _seed_wf(box)
    r = _bnd(box, session=None)
    assert r["outcome"] == "no_session_state" and r["relay"] is False, r
    assert r["session_id"] is None and r["session_source"] is None and r["tokens"] is None, r
    assert r["checkpoint_path"] == str(_checkpoint(box)) and _checkpoint(box).exists(), r
    box = _Box("on")
    _seed_wf(box)
    _seed_session(box, "s1", 130000)
    old = time.time() - 13 * 3600
    os.utime(box.state_dir / "s1.json", (old, old))
    r = _bnd(box, session=None)
    assert r["outcome"] == "no_session_state" and r["relay"] is False, r
    assert r["session_source"] is None and _checkpoint(box).exists(), r


def test_boundary_remeasure_failure_falls_back_to_last_tokens():
    box = _Box("on")
    _seed_wf(box)
    tpath = _seed_session(box, "s1", 130000)
    os.unlink(tpath)
    r = _bnd(box)
    assert r["error"] == "remeasure_failed", r
    assert r["tokens"] == 130000 and r["level"] == "warn" and r["relay"] is True, r
    assert r["checkpoint_path"] is not None, r


def test_boundary_reset_rearms_relay():
    box = _Box("on")
    _seed_wf(box)
    tpath = _seed_session(box, "s1", 130000)
    assert _bnd(box)["relay"] is True
    assert _bnd(box)["relay"] is False
    reset = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "s1"}
    code, out, _err, _rows, _ = run_script(reset, box=box, args=("--reset",))
    assert code == 0 and out == ""
    # the hook recreates the state on the next prompt (keeps a state file present)
    code, out, _err, _rows, _ = run_script(_prompt(tpath), box=box)
    assert code == 0
    r = _bnd(box)
    assert r["relay"] is True and r["level"] == "warn", r


def test_boundary_bad_args_json_error_exit0():
    box = _Box("audit")
    code, out, _err, _rows, _ = run_script({}, box=box, args=("--boundary", "--bogus-flag"))
    assert code == 0
    parsed = json.loads(out)
    assert parsed["error"] == "bad_args" and parsed["relay"] is False and out.count("\n") == 1, parsed
    assert parsed["outcome"] == "error", parsed
    code, out, _err, _rows, _ = run_script({}, box=box, args=("--bogus-flag",))
    assert code == 0 and out == ""


def test_boundary_never_calls_tokentracker():
    box = _Box("on")
    _seed_wf(box)
    _seed_session(box, "s1", 130000)
    fake_dir = box.root / "fakebin"
    fake_dir.mkdir()
    marker = box.root / "tokentracker-called"
    fake = fake_dir / "tokentracker"
    fake.write_text("#!/bin/sh\ntouch '" + str(marker) + "'\necho '{}'\n", encoding="utf-8")
    fake.chmod(0o755)
    r = _bnd(box, env_extra={"PATH": str(fake_dir) + os.pathsep + os.environ.get("PATH", "")})
    assert r["relay"] is True, r
    assert not marker.exists()


def test_boundary_checkpoint_not_precompact_state():
    box = _Box("on")
    _seed_wf(box)
    _seed_session(box, "s1", 130000)
    _bnd(box)
    assert _checkpoint(box).exists()
    assert not (box.project / ".craftflow" / "state" / "precompact-state.json").exists()
    assert not list(box.root.rglob("precompact-state.json"))


def test_boundary_phase_label_passthrough():
    for phase in ("P3", "plan-handoff"):
        box = _Box("on")
        _seed_wf(box)
        _seed_session(box, "s1", 130000)
        r = _bnd(box, "--phase", phase)
        assert r["phase"] == phase, r
        assert json.loads(_checkpoint(box).read_text(encoding="utf-8"))["phase"] == phase
    box = _Box("on")
    _seed_wf(box)
    _seed_session(box, "s1", 130000)
    r = _bnd(box)
    assert r["phase"] is None and r["error"] is None, r
    assert json.loads(_checkpoint(box).read_text(encoding="utf-8"))["phase"] is None


def test_boundary_config_invalid_prints_error_line():
    box = _Box("on", config={"warnTokens": 5, "criticalTokens": 4, "assumedWindow": 3})
    _seed_wf(box)
    _seed_session(box, "s1", 130000)
    r = _bnd(box)
    assert r["outcome"] == "error" and r["error"] == "config_invalid" and r["relay"] is False, r
    assert not _checkpoint(box).exists()
    assert [x["error"] for x in log_rows(box)] == ["config_invalid"]


def test_boundary_logs_one_decision_row():
    box = _Box("on")
    _seed_wf(box)
    _seed_session(box, "s1", 130000)
    _bnd(box, "--phase", "P1")
    rows = log_rows(box)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["source"] == "boundary" and row["wf"] == WF and row["phase"] == "P1", row
    assert row["session_source"] == "arg" and row["relay"] is True and row["event"] == "context_nudge", row
    assert row["checkpoint_path"] == str(_checkpoint(box)), row


def test_module_import_cheap():
    box = _Box("audit")
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update(CLAUDE_PLUGIN_ROOT=str(box.plugin), CLAUDE_PROJECT_DIR=str(box.project),
               PYTHONPATH=str(SCRIPTS))
    t0 = time.perf_counter()
    proc = subprocess.run([sys.executable, "-c", "import craftflow_context_nudge"], env=env,
                          cwd=str(box.root), capture_output=True, timeout=10)
    elapsed = time.perf_counter() - t0
    assert proc.returncode == 0 and proc.stdout == b"", (proc.returncode, proc.stdout, proc.stderr)
    assert elapsed < 0.5, elapsed
    assert not (box.project / ".craftflow").exists()
    assert not (box.root / ".craftflow").exists()


def test_hooks_json_wiring():
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    ups = hooks["UserPromptSubmit"]
    assert len(ups) == 2, len(ups)
    assert ups[0] == {"hooks": [{"type": "command",
                                 "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/craftflow_jev_prompt_hint.py"',
                                 "timeout": 5,
                                 "statusMessage": "CRAFTFLOW optional Jev routing hint (inert unless config/jev.json enables it)"}]}
    assert len(ups[1]["hooks"]) == 1 and "matcher" not in ups[1]
    h = ups[1]["hooks"][0]
    assert h["type"] == "command" and h["timeout"] == 5
    assert h["command"] == 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/craftflow_context_nudge.py"'
    ss = hooks["SessionStart"]
    assert len(ss) == 5, len(ss)
    assert [e.get("matcher") for e in ss[:4]] == ["startup", "startup|resume|compact",
                                                  "startup|resume|compact", "startup|resume|compact"]
    order = ["craftflow_context_migration.py", "craftflow_sessionstart_context.py",
             "craftflow_hook_selfcheck.py", "craftflow_jev_session_check.py"]
    for entry, script in zip(ss[:4], order):
        assert script in entry["hooks"][0]["command"], (script, entry)
    assert ss[4]["matcher"] == "compact" and len(ss[4]["hooks"]) == 1
    assert ss[4]["hooks"][0]["command"] == 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/craftflow_context_nudge.py" --reset'
    assert ss[4]["hooks"][0]["timeout"] == 5 and ss[4]["hooks"][0]["type"] == "command"


def test_hook_mode_and_thresholds_config_shipped():
    cfg_dir = PLUGIN_ROOT / "config"
    mode = json.loads((cfg_dir / "hook-mode.json").read_text(encoding="utf-8"))
    assert mode["contextNudge"] == "audit", mode
    keys = list(mode)
    assert keys.index("contextNudge") == keys.index("agentUsageTelemetry") + 1, keys
    thresholds = json.loads((cfg_dir / "context-nudge.json").read_text(encoding="utf-8"))
    assert thresholds == {"warnTokens": 120000, "criticalTokens": 160000, "assumedWindow": 200000}
    assert cn.validate_config(thresholds) == (thresholds, None)


# ---------------------------------------------------------------------------
# Phase 6: router reference + docs anchors
# ---------------------------------------------------------------------------

REFS = PLUGIN_ROOT / "skills" / "craftflow-router" / "references"
_POINTER = "references/context-boundary.md"
_BUILD_CMD = ("python3 {plugin_root}/scripts/craftflow_context_nudge.py --boundary --wf {workflow_uuid} "
              "--phase {phase_id} --project-root \"$PROJECT_ROOT\"")
_PLAN_CMD = ("python3 {plugin_root}/scripts/craftflow_context_nudge.py --boundary --wf {workflow_uuid} "
             "--phase plan-handoff --project-root \"$PROJECT_ROOT\"")
_FINALIZE_MARKERS = (
    "craftflow_state_query.py <destination_file_path> --mode full",
    "write archive_path FIRST",
    "unit mode, notes are prepended as new raw-text entries",
    "phase:learn-distill",
    "phase:skill-distill",
    "circuit_breaker",
    "craftflow_jev_remfix_scope.py",
)


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def test_router_context_boundary_reference_contract():
    text = _read(REFS / "context-boundary.md")
    flat = " ".join(text.split())  # tolerate hard line wraps and indentation in prose
    for needle in (_BUILD_CMD, _PLAN_CMD, "`relay` is literally `true`", "end the turn",
                   "never run /compact yourself"):
        assert needle in flat, "missing: " + needle


def test_router_pointer_counts():
    assert _read(REFS / "build-workflow.md").count(_POINTER) == 2
    rr = _read(REFS / "remediation-and-research.md")
    assert rr.count(_POINTER) == 2
    assert _read(REFS / "plan-workflow.md").count(_POINTER) == 0
    lines = rr.splitlines()
    heads = [i for i, ln in enumerate(lines) if ln.startswith("When `plan-gap-reviewer`")]
    for title in ("When `plan-gap-reviewer` pass 1 returns `PASS`:",
                  "When `plan-gap-reviewer` pass 2 returns `PASS`:"):
        h = lines.index(title)
        nxt = min(i for i in heads if i > h)
        ptr = next(i for i in range(h + 1, nxt) if _POINTER in lines[i])
        cont = next(i for i in range(h + 1, nxt) if lines[i].startswith("- Continue to memory finalization"))
        assert ptr < cont < nxt, title


def test_router_pointers_avoid_memory_finalize_markers():
    texts = [_read(REFS / "context-boundary.md")]
    for name in ("build-workflow.md", "remediation-and-research.md"):
        texts.extend(ln for ln in _read(REFS / name).splitlines() if _POINTER in ln)
    for t in texts:
        for marker in _FINALIZE_MARKERS:
            assert marker not in t, "marker leaked: " + marker


def test_contract_doc_documents_context_nudge():
    doc = _read(PLUGIN_ROOT / "docs" / "craftflow-event-contract.md")
    assert "### Log event: `context_nudge`" in doc
    for field in ("schema", "source", "mode", "session_id", "level", "tokens", "token_source",
                  "threshold", "last_level", "had_state", "outcome", "error", "wf", "phase",
                  "session_source", "relay", "checkpoint_path"):
        assert "`" + field + "`" in doc.split("### Log event: `context_nudge`", 1)[1], field
    assert "| `UserPromptSubmit` |" in doc


def test_hook_inventory_docs_mention_context_nudge():
    assert "craftflow_context_nudge.py" in _read(PLUGIN_ROOT / "hooks" / "README.md")
    policy = _read(REFS / "workflow-artifact-and-hook-policy.md")
    assert "craftflow_context_nudge.py" in policy
    assert "`UserPromptSubmit` for the optional Jev" in policy


# ---------------------------------------------------------------------------
# Phase 2: user override settings (SPEC-0017 / ADR-0054), pure core + loader
# ---------------------------------------------------------------------------

_OVR_ENV = "CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG"
_OVR_REL = "/".join((".claude", "craftflow", "context-nudge.json"))


def test_user_override_path_resolution():
    p = cn.user_override_path({_OVR_ENV: "/x/y.json", "HOME": "/h"})
    assert p == Path("/x/y.json"), p
    for blank in ("  ", ""):
        assert cn.user_override_path({_OVR_ENV: blank, "HOME": "/h"}) == Path("/h/" + _OVR_REL)
    assert cn.user_override_path({"HOME": "relative"}) is None
    assert cn.user_override_path({"HOME": ""}) is None
    # never touches the file system: a nonexistent HOME still resolves
    assert cn.user_override_path({"HOME": "/nonexistent-cn-home"}) == Path("/nonexistent-cn-home/" + _OVR_REL)


def test_parse_user_override_per_key_matrix():
    def chk(obj, values, status, error, keys):
        r = cn.parse_user_override(obj)
        got = (r["values"], r["status"], r["error"], r["keys"])
        assert got == (values, status, error, keys), (obj, got)

    chk({"contextNudge": "on"}, {"contextNudge": "on"}, "applied", None, ["contextNudge"])
    for bad in ("On", True, 1):
        chk({"contextNudge": bad}, {}, "ignored", "invalid_value", [])
    for bad in (True, 1.5, "1000", 0, -5):
        chk({"warnTokens": bad}, {}, "ignored", "invalid_value", [])
    chk({"warnTokens": 1000, "x": 1}, {"warnTokens": 1000}, "partial", "unknown_key", ["warnTokens"])
    chk({"mode": "on"}, {}, "ignored", "unknown_key", [])
    chk({}, {}, "ignored", None, [])
    full = {"warnTokens": 1, "criticalTokens": 2, "assumedWindow": 3, "contextNudge": "audit"}
    chk(full, full, "applied", None, ["assumedWindow", "contextNudge", "criticalTokens", "warnTokens"])
    # invalid_value outranks unknown_key
    chk({"warnTokens": 0, "zzz": 1}, {}, "ignored", "invalid_value", [])


def test_merge_thresholds_and_mode_precedence():
    # Q4: user mode wins when valid; else exactly the plugin resolution
    for u in ("off", "audit", "on", None, "bad"):
        for p in ("off", "audit", "on", None, "weird"):
            ov = cn.parse_user_override({} if u is None else {"contextNudge": u})
            eff, logged, from_user = cn.resolve_mode_with_override(p, ov)
            if u in ("off", "audit", "on"):
                assert (eff, logged, from_user) == (u, u, True), (u, p)
            else:
                assert (eff, logged) == cn.resolve_mode(p) and from_user is False, (u, p)
    base = dict(cn.DEFAULTS)
    # partial user thresholds layered on a valid plugin config
    ov = cn.parse_user_override({"warnTokens": 1000})
    assert cn.merge_thresholds(base, None, ov) == (dict(base, warnTokens=1000), None, False)
    # no threshold keys -> plugin cfg untouched
    assert cn.merge_thresholds(base, None, cn.parse_user_override({"contextNudge": "on"})) == (base, None, False)
    # O12 merged triple inconsistent -> plugin thresholds, user mode kept
    ov = cn.parse_user_override({"contextNudge": "on", "warnTokens": 190000})
    m = cn.merge_thresholds(base, None, ov)
    assert m == (base, None, True), m
    assert cn.override_fields(ov, m) == ("partial", "inconsistent_thresholds", ["contextNudge"])
    # ... with no mode key -> ignored
    ov2 = cn.parse_user_override({"warnTokens": 190000})
    m2 = cn.merge_thresholds(base, None, ov2)
    assert cn.override_fields(ov2, m2) == ("ignored", "inconsistent_thresholds", [])
    # O13 plugin invalid + full valid user triple -> user triple
    trip = {"warnTokens": 10, "criticalTokens": 20, "assumedWindow": 30}
    ov3 = cn.parse_user_override(trip)
    assert cn.merge_thresholds(None, "config_invalid", ov3) == (trip, None, False)
    # O14 plugin invalid + partial user -> config_invalid preserved
    ov4 = cn.parse_user_override({"warnTokens": 10})
    r4 = cn.merge_thresholds(None, "config_invalid", ov4)
    assert r4[0] is None and r4[1] == "config_invalid", r4
    # plugin invalid + inconsistent full user triple -> config_invalid
    ov5 = cn.parse_user_override({"warnTokens": 30, "criticalTokens": 20, "assumedWindow": 10})
    r5 = cn.merge_thresholds(None, "config_invalid", ov5)
    assert r5[0] is None and r5[1] == "config_invalid", r5
    # priority: inconsistent_thresholds > invalid_value > unknown_key
    ov6 = cn.parse_user_override({"warnTokens": 190000, "criticalTokens": "x", "q": 1})
    assert cn.override_fields(ov6, cn.merge_thresholds(base, None, ov6))[1] == "inconsistent_thresholds"
    ov7 = cn.parse_user_override({"criticalTokens": "x", "q": 1})
    assert cn.override_fields(ov7, cn.merge_thresholds(base, None, ov7))[1] == "invalid_value"
    # exit-criterion example
    o = cn.parse_user_override({"contextNudge": "on", "warnTokens": 1000, "mode": "on"})
    t = cn.merge_thresholds(dict(cn.DEFAULTS), None, o)
    assert t[0] == {"warnTokens": 1000, "criticalTokens": 160000, "assumedWindow": 200000}
    assert cn.override_fields(o, t) == ("partial", "unknown_key", ["contextNudge", "warnTokens"])


def test_load_user_override_file_states():
    d = Path(tempfile.mkdtemp(prefix="cn-ovr-"))
    _BOXES.append(d)

    def put(name, data):
        p = d / name
        p.write_bytes(data)
        return p

    r = cn.load_user_override(d / "missing.json")
    assert (r["status"], r["error"]) == ("absent", None), r
    (d / "adir").mkdir()
    r = cn.load_user_override(d / "adir")
    assert (r["status"], r["error"]) == ("error", "unreadable"), r
    r = cn.load_user_override(put("big.json", b" " * 70000))
    assert (r["status"], r["error"]) == ("error", "too_large"), r
    r = cn.load_user_override(put("bad.json", b"{not json"))
    assert (r["status"], r["error"]) == ("error", "corrupt"), r
    r = cn.load_user_override(put("bin.json", b"\xff\xfe"))
    assert (r["status"], r["error"]) == ("error", "corrupt"), r
    r = cn.load_user_override(put("bom.json", b"\xef\xbb\xbf{\"contextNudge\":\"on\"}"))
    assert (r["status"], r["values"]) == ("applied", {"contextNudge": "on"}), r
    for i, raw in enumerate((b"[1]", b"\"x\"", b"3", b"null")):
        r = cn.load_user_override(put("nonobj%d.json" % i, raw))
        assert (r["status"], r["error"]) == ("error", "not_object"), (raw, r)
    if os.geteuid() != 0:
        p = put("noperm.json", b"{}")
        os.chmod(p, 0)
        r = cn.load_user_override(p)
        assert (r["status"], r["error"]) == ("error", "unreadable"), r
    ok_file = put("real.json", b"{\"warnTokens\": 5}")
    link = d / "link.json"
    os.symlink(str(ok_file), str(link))
    r = cn.load_user_override(link)
    assert (r["status"], r["values"]) == ("applied", {"warnTokens": 5}), r
    r = cn.load_user_override(None)
    assert (r["status"], r["error"]) == ("absent", "home_unresolved"), r


def test_override_totality_fuzz():
    rng = random.Random(4242)

    def val(depth=0):
        k = rng.randrange(9 if depth < 2 else 7)
        if k == 0:
            return None
        if k == 1:
            return rng.choice((True, False))
        if k == 2:
            return rng.randrange(-5, 300000)
        if k == 3:
            return rng.random() * 1e6
        if k == 4:
            return rng.choice(("on", "off", "audit", "On", "", "x" * 50))
        if k == 5:
            return rng.choice(("warnTokens", "mode", "contextNudge"))
        if k == 6:
            return rng.choice((0, 1, -1, 2 ** 70))
        if k == 7:
            return [val(depth + 1) for _ in range(rng.randrange(3))]
        keys = ("contextNudge", "warnTokens", "criticalTokens", "assumedWindow", "mode", "z")
        return {rng.choice(keys): val(depth + 1) for _ in range(rng.randrange(5))}

    for _ in range(300):
        a, b, c = val(), val(), val()
        ov = cn.parse_user_override(a)
        assert isinstance(ov, dict) and "status" in ov
        cn.resolve_mode_with_override(b, ov)
        m = cn.merge_thresholds(b, c, ov)
        cn.override_fields(ov, m)
        cn.override_fields(a, b)
        cn.merge_thresholds(a, b, c)
        cn.resolve_mode_with_override(a, b)


def test_user_override_path_independent_of_plugin_root():
    a = cn.user_override_path({"HOME": "/h", "CLAUDE_PLUGIN_ROOT": "/p1"})
    b = cn.user_override_path({"HOME": "/h", "CLAUDE_PLUGIN_ROOT": "/p2"})
    assert a == b == Path("/h/" + _OVR_REL)
    assert cn.USER_OVERRIDE_ENV == _OVR_ENV
    assert cn.USER_OVERRIDE_MAX_BYTES == 65536
    assert cn.USER_OVERRIDE_KEYS == ("contextNudge", "warnTokens", "criticalTokens", "assumedWindow")
    assert cn.USER_OVERRIDE_SEGMENTS == (".claude", "craftflow", "context-nudge.json")
    for mod in (cn, cc):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert (".claude/" + "craftflow") not in src, mod.__file__
        assert ('.claude" / "' + "craftflow") not in src, mod.__file__


# ---------------------------------------------------------------------------
# Phase 3: compact module (SPEC-0017 / ADR-0054)
# ---------------------------------------------------------------------------

_LIVE_PAYLOAD = {"workflow_type": "build", "phase_cursor": "P1", "worktree_mode": "active",
                 "status_history": [{"event": "phase_started"}], "pending_gate": None}
_BOUND_TAIL = ("Preserve its decisions, open questions, failing checks and next step; "
               "after compaction resume from .craftflow/state/workflows/%s.json.")


def test_wf_mentions_backward_scan():
    data = b'x wf-a-1 y "wf-b-2" zwf-c-3 wf-a-1 wf-d-4- wf-id'
    assert cc.wf_mentions(data) == ["wf-id", "wf-d-4", "wf-a-1", "wf-b-2"]
    many = b" ".join(b"wf-id-%d" % i for i in range(50))
    got = cc.wf_mentions(many)
    assert got == ["wf-id-%d" % i for i in range(49, 29, -1)], got
    assert len(got) <= 20
    # only the 40 most recent raw hits are examined: the older distinct id is never reached
    assert cc.wf_mentions(b"wf-old-1 " + b" wf-same" * 100) == ["wf-same"]
    assert cc.wf_mentions(b"") == [] and cc.wf_mentions(None) == []


def test_wf_mentions_json_escape_boundary():
    # raw JSONL keeps a newline/tab/CR as backslash + letter: that is a boundary, not an id char
    assert cc.wf_mentions(b'{"c":"line\\nwf-esc-1 x"}') == ["wf-esc-1"]
    assert cc.wf_mentions(b'{"c":"a\\twf-esc-2\\rwf-esc-3\\nwf-esc-4"}') == ["wf-esc-4", "wf-esc-3", "wf-esc-2"]
    # genuine longer tokens are still rejected
    assert cc.wf_mentions(b"zwf-c-3 xnwf-c-4 wf-ok-1") == ["wf-ok-1"]
    assert cc.wf_mentions(b"nwf-c-5") == []


def test_wf_mentions_existing_filter_survives_flood():
    real = b"wf-real-1 " + b" ".join(b"wf-ghost-%d" % i for i in range(30)) + b" end"
    # unfiltered: the 20-distinct cap loses the real (oldest) id
    assert "wf-real-1" not in cc.wf_mentions(real)
    # filtered by artifact existence during the backward scan: the real id is kept
    assert cc.wf_mentions(real, accept=lambda wf: wf == "wf-real-1") == ["wf-real-1"]
    # the raw-hit cap still bounds the scan
    far = b"wf-real-1 " + b" ".join(b"wf-ghost-%d" % i for i in range(60))
    assert cc.wf_mentions(far, accept=lambda wf: wf == "wf-real-1") == []
    # stops once MAX_CANDIDATES accepted ids are found
    many = b" ".join(b"wf-ok-%d" % i for i in range(30))
    assert cc.wf_mentions(many, accept=lambda wf: True) == ["wf-ok-%d" % i for i in range(29, 24, -1)]
    calls = []
    cc.wf_mentions(b"wf-a-1 wf-a-1 wf-a-1 wf-b-2", accept=lambda wf: calls.append(wf) or True)
    assert calls == ["wf-b-2", "wf-a-1"], calls  # each distinct id is checked once


def test_payload_terminal_matrix():
    live = dict(_LIVE_PAYLOAD)
    assert cc.payload_terminal(live) is False
    for mode in ("merged_and_removed", "removed_no_merge_needed", "removed_after_pr_merge", "merged"):
        assert cc.payload_terminal(dict(live, worktree_mode=mode)) is True, mode
    for ev in ("workflow_completed", "memory_finalized", "workflow_merged", "merged_to_main",
               "plan_closed", "debug_complete"):
        assert cc.payload_terminal(dict(live, status_history=[{"event": "x"}, {"event": ev}])) is True, ev
    # only the last history element counts
    assert cc.payload_terminal(dict(live, status_history=[{"event": "workflow_completed"}, {"event": "x"}])) is False
    for cur in ("complete", "done"):
        assert cc.payload_terminal(dict(live, phase_cursor=cur)) is True, cur
    gone = dict(live, worktree_path="/some/gone")
    assert cc.payload_terminal(gone, isdir=lambda p: False) is True
    assert cc.payload_terminal(gone, isdir=lambda p: True) is False
    assert cc.payload_terminal(dict(live, worktree_path=""), isdir=lambda p: False) is False
    # terminal signals beat a pending gate; a gate only keeps a non-terminal workflow live
    done = dict(live, worktree_mode="merged")
    for gate in ("user_build_approval", {"kind": "plan_approval"}):
        assert cc.payload_terminal(dict(done, pending_gate=gate)) is True, gate
        assert cc.payload_terminal(dict(live, phase_cursor="complete", pending_gate=gate)) is True, gate
        assert cc.payload_terminal(dict(live, status_history=[{"event": "workflow_completed"}],
                                        pending_gate=gate)) is True, gate
        assert cc.payload_terminal(dict(live, pending_gate=gate)) is False, gate
    gone_gated = dict(live, worktree_path="/some/gone", pending_gate="user_build_approval")
    assert cc.payload_terminal(gone_gated, isdir=lambda p: False) is False
    for gate in ("none", "", None, "null", "n/a", "N/A", "false", "-", {"kind": "none"}, {"kind": ""},
                 {"kind": "n/a"}, {}, {"kind": None}):
        assert cc.payload_terminal(dict(done, pending_gate=gate)) is True, gate
        assert cc.payload_terminal(dict(live, pending_gate=gate)) is False, gate
    for junk in (None, [], "x", 3):
        assert cc.payload_terminal(junk) is True, junk


def _cand(wf, mtime, **payload):
    return {"wf": wf, "mtime": mtime, "payload": dict(_LIVE_PAYLOAD, **payload)}


def test_choose_workflow_rules():
    assert cc.choose_workflow([], "s1") == (None, "no_live_candidate")
    assert cc.choose_workflow([_cand("wf-a", 10)], "s1") == ("wf-a", "single_candidate")
    # agree: the most recent mention (first) also has the strictly newest mtime
    two = [_cand("wf-b", 20), _cand("wf-a", 10)]
    assert cc.choose_workflow(two, "s1") == ("wf-b", "mention_mtime_agree")
    assert cc.choose_workflow([_cand("wf-b", 10), _cand("wf-a", 20)], "s1") == (None, "ambiguous")
    assert cc.choose_workflow([_cand("wf-b", 10), _cand("wf-a", 10)], "s1") == (None, "ambiguous")
    # session match wins over disagreeing mtimes; exactly one match required
    sm = [_cand("wf-b", 10), _cand("wf-a", 20, session_id="s1")]
    assert cc.choose_workflow(sm, "s1") == ("wf-a", "session_match")
    two_sm = [_cand("wf-b", 20, session_id="s1"), _cand("wf-a", 10, session_id="s1")]
    assert cc.choose_workflow(two_sm, "s1") == ("wf-b", "mention_mtime_agree")
    two_sm_dis = [_cand("wf-b", 10, session_id="s1"), _cand("wf-a", 20, session_id="s1")]
    assert cc.choose_workflow(two_sm_dis, "s1") == (None, "ambiguous")
    assert cc.choose_workflow(sm, None) == (None, "ambiguous")
    assert cc.choose_workflow("junk", "s1") == (None, "ambiguous")
    # H1: another session's workflow is never bound when our session id is known
    other = [_cand("wf-other", 10, session_id="OTHER")]
    assert cc.choose_workflow(other, "MINE") == (None, "no_live_candidate")
    assert cc.choose_workflow(other + [_cand("wf-plain", 5)], "MINE") == ("wf-plain", "single_candidate")
    # H2: an unmatched other-session candidate never wins the tie-break
    trio = [_cand("wf-other", 30, session_id="OTHER"), _cand("wf-a", 20, session_id="MINE"),
            _cand("wf-b", 10, session_id="MINE")]
    assert cc.choose_workflow(trio, "MINE") == ("wf-a", "mention_mtime_agree")
    trio_dis = [_cand("wf-other", 30, session_id="OTHER"), _cand("wf-a", 10, session_id="MINE"),
                _cand("wf-b", 20, session_id="MINE")]
    assert cc.choose_workflow(trio_dis, "MINE") == (None, "ambiguous")
    # a session-less candidate is only a fallback; a matching one beats it
    mixed = [_cand("wf-plain", 30), _cand("wf-mine", 10, session_id="MINE")]
    assert cc.choose_workflow(mixed, "MINE") == ("wf-mine", "session_match")


def test_workflow_snapshot_sanitizes():
    root = "/tmp/proj"
    keys = {"wf", "workflow_type", "phase_cursor", "pending_gate", "plan_file", "design_file"}
    full = cc.workflow_snapshot(
        {"workflow_type": "build", "phase_cursor": "P3", "pending_gate": None,
         "plan_file": "docs/plans/a-plan.md", "design_file": "docs/plans/a-design.md",
         "user_request": "sk-ant-SECRET", "intent": "line1\nline2", "workflow_uuid": "wf-other"},
        "wf-demo-1", root)
    assert full == {"wf": "wf-demo-1", "workflow_type": "BUILD", "phase_cursor": "P3", "pending_gate": None,
                    "plan_file": "docs/plans/a-plan.md", "design_file": "docs/plans/a-design.md"}, full
    assert "SECRET" not in repr(full)

    def snap(**kw):
        s = cc.workflow_snapshot(kw, "wf-t-1", root)
        assert set(s) == keys, s
        return s

    assert snap(phase_cursor=3)["phase_cursor"] == "3"
    assert snap(phase_cursor=True)["phase_cursor"] is None
    assert snap(phase_cursor=1.5)["phase_cursor"] is None
    assert snap(phase_cursor="P 1")["phase_cursor"] is None
    assert snap(phase_cursor="P1\n")["phase_cursor"] is None
    assert snap(pending_gate={"kind": "plan_approval"})["pending_gate"] == "plan_approval"
    assert snap(pending_gate="plan_approval")["pending_gate"] == "plan_approval"
    for g in ("none", "None", "NULL", "", None, {"kind": 3}, {"kind": "a b"}, ["x"], "x y"):
        assert snap(pending_gate=g)["pending_gate"] is None, g
    for bad in ("docs/plans/x.md\nrm", "../x.md", "/abs/out.md", "docs//p.md", "docs/p.txt", "docs/../p.md",
                "`x`.md", "docs/a b.md", "$(id).md", "/tmp/other/docs/p.md", "docs/p.md/", 7, ["docs/p.md"],
                "docs/" + "d" * 250 + ".md"):
        assert snap(plan_file=bad)["plan_file"] is None, bad
        assert snap(design_file=bad)["design_file"] is None, bad
    assert snap(plan_file="/tmp/proj/docs/p.md")["plan_file"] == "docs/p.md"
    assert snap(design_file="docs/p.md")["design_file"] == "docs/p.md"
    assert snap(workflow_type="weird")["workflow_type"] is None
    assert snap(workflow_type=["build"])["workflow_type"] is None
    assert snap(workflow_type="review")["workflow_type"] == "REVIEW"
    assert cc.workflow_snapshot({}, "bad wf", root) is None
    assert cc.workflow_snapshot({}, "wf-x\n", root) is None
    assert cc.workflow_snapshot("junk", "wf-x", root) is None


def test_build_compact_line_bound_and_generic():
    snap = cc.workflow_snapshot(
        {"workflow_type": "build", "phase_cursor": "P3", "pending_gate": None,
         "plan_file": "docs/plans/a-plan.md", "design_file": "docs/plans/a-design.md",
         "user_request": "sk-ant-SECRET"}, "wf-demo-1", "/nonexistent")
    want = ("/compact Keep craftflow workflow wf-demo-1 (BUILD; phase P3; plan docs/plans/a-plan.md; "
            "design docs/plans/a-design.md). " + _BOUND_TAIL % "wf-demo-1")
    assert cc.build_compact_line(snap) == want
    assert cc.build_compact_line({"wf": "wf-x"}) == "/compact Keep craftflow workflow wf-x. " + _BOUND_TAIL % "wf-x"
    gated = cc.build_compact_line({"wf": "wf-x", "pending_gate": "plan_approval"})
    assert gated == ("/compact Keep craftflow workflow wf-x (pending gate plan_approval). " + _BOUND_TAIL % "wf-x")
    generic = cc.GENERIC_COMPACT_LINE
    assert generic.startswith("/compact ") and len(generic) <= 600 and generic.isascii() and "\n" not in generic
    for bad in (None, {}, [], "wf-x", {"wf": None}, {"wf": "bad wf"}, {"wf": "wf-x\n"}, {"wf": "wf-" + "a" * 161},
                {"workflow_type": "BUILD"}):
        assert cc.build_compact_line(bad) == generic, bad


def _expected_parts(s):
    """Independent oracle for the allowlisted parts (mirrors DD-10/DD-11, not the implementation)."""
    import re
    parts = []
    if s.get("workflow_type") in ("BUILD", "PLAN", "DEBUG", "REVIEW"):
        parts.append(s["workflow_type"])
    ph = s.get("phase_cursor")
    if isinstance(ph, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,39}", ph):
        parts.append("phase " + ph)
    g = s.get("pending_gate")
    if isinstance(g, str) and g.lower() not in ("", "none", "null") \
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", g):
        parts.append("pending gate " + g)
    for key, label in (("plan_file", "plan "), ("design_file", "design ")):
        p = s.get(key)
        if isinstance(p, str) and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}", p) and p.endswith(".md") \
                and ".." not in p.split("/") and "" not in p.split("/"):
            parts.append(label + p)
    return parts


def _expected_line(s, generic):
    import re
    if not isinstance(s, dict):
        return generic
    wf = s.get("wf")
    if not (isinstance(wf, str) and re.fullmatch(r"wf-[A-Za-z0-9-]{1,160}", wf)):
        return generic
    parts = _expected_parts(s)
    for n in range(len(parts), -1, -1):
        paren = (" (" + "; ".join(parts[:n]) + ")") if n else ""
        line = "/compact Keep craftflow workflow " + wf + paren + ". " + _BOUND_TAIL % wf
        if len(line) <= 600:
            return line
    return generic


def test_build_compact_line_cap_degrades_in_order():
    wf = "wf-" + "a" * 160
    plan = "docs/" + "p" * 190 + ".md"
    design = "docs/" + "d" * 190 + ".md"
    snap = {"wf": wf, "workflow_type": "BUILD", "phase_cursor": "P" * 40, "pending_gate": "g" * 64,
            "plan_file": plan, "design_file": design}
    line = cc.build_compact_line(snap)
    assert len(line) <= 600 and line.startswith("/compact "), len(line)
    assert design not in line, "design is dropped first"
    assert wf in line and line.endswith(_BOUND_TAIL % wf)
    kept_prefix_lengths = set()
    for wf_len in (5, 60, 120, 160):
        for plan_len in (10, 60, 120, 190):
            for gate_len in (5, 64):
                w = "wf-" + "a" * wf_len
                s = {"wf": w, "workflow_type": "BUILD", "phase_cursor": "P" * 40, "pending_gate": "g" * gate_len,
                     "plan_file": "docs/" + "p" * plan_len + ".md", "design_file": "docs/" + "d" * 190 + ".md"}
                out = cc.build_compact_line(s)
                assert out == _expected_line(s, cc.GENERIC_COMPACT_LINE), (wf_len, plan_len, gate_len)
                assert len(out) <= 600 and w in out
                markers = ["BUILD", "phase P", "pending gate g", "plan docs/p", "design docs/d"]
                present = [m in out for m in markers]
                k = sum(present)
                assert present == [True] * k + [False] * (5 - k), ("kept parts must be a prefix", present)
                kept_prefix_lengths.add(k)
    assert 5 in kept_prefix_lengths and min(kept_prefix_lengths) <= 3, kept_prefix_lengths


def test_compact_line_shape_fuzz():
    rng = random.Random(4242)
    hostile = ["", "x", "wf-ok-1", "P1", "P" * 60, "a b", "x\ny", "`id`", "$(id)", '"q"', "'q'", "docs/../x.md",
               "docs//x.md", "/abs/x.md", "docs/x.md", "docs/plans/a-plan.md", "docs/x.txt", "é.md", "x" * 300,
               "../x.md", "BUILD", "build", "none", "None", "plan_approval", "wf-demo-1", "wf-" + "a" * 160,
               "wf-" + "a" * 161, "wf-bad\n", "docs/" + "d" * 250 + ".md", "P1\n", "docs/x.md\n"]
    vals = hostile + [None, True, 3, 1.5, [], {}, {"kind": "plan_approval"}, {"kind": "x y"}, ["a"]]
    keys = ["wf", "workflow_type", "phase_cursor", "pending_gate", "plan_file", "design_file", "user_request",
            "workflow_uuid", "worktree_path", "status_history", "session_id"]
    generic = cc.GENERIC_COMPACT_LINE

    def rand_dict():
        return {k: rng.choice(vals) for k in rng.sample(keys, rng.randrange(0, len(keys) + 1))}

    for _ in range(300):
        d = rand_dict()
        d["wf"] = rng.choice(["wf-demo-1", "wf-" + "b" * 160, "bad wf", None, "wf-x\n", "wf-ok"])
        blob = bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 40))) + rng.choice(
            [b"wf-", b"wf-a", b" wf-a-1 ", b"zwf-q", b"wf--"]) + bytes(rng.getrandbits(8) for _ in range(20))
        for m in cc.wf_mentions(blob):
            assert cc.WF_ID_RE.fullmatch(m), m
        assert isinstance(cc.payload_terminal(d), bool)
        cands = [{"wf": rng.choice(vals), "mtime": rng.choice([1, 2.5, None, "x", True]), "payload": rand_dict()}
                 for _ in range(rng.randrange(0, 4))]
        r = cc.choose_workflow(cands, rng.choice(vals))
        assert isinstance(r, tuple) and len(r) == 2
        snap = cc.workflow_snapshot(d, rng.choice(["wf-demo-1", "bad wf", None]), rng.choice(["/tmp/proj", None, 3]))
        assert snap is None or (isinstance(snap, dict) and len(snap) == 6), snap
        for candidate in (d, snap, rng.choice(vals)):
            line = cc.build_compact_line(candidate)
            assert line.startswith("/compact "), line
            assert "\n" not in line and "\r" not in line and len(line) <= 600 and line.isascii(), line
            assert line == _expected_line(candidate, generic), (candidate, line)


def _wf_env():
    root = Path(tempfile.mkdtemp(prefix="cn-p3-"))
    (root / "workflows").mkdir()
    return root


def _put_wf(root, wf, mtime=None, raw=None, **payload):
    p = root / "workflows" / (wf + ".json")
    if raw is None:
        raw = json.dumps(dict({"workflow_type": "build", "phase_cursor": "P1", "plan_file": "docs/plans/x.md"},
                              **payload)).encode("utf-8")
    p.write_bytes(raw)
    if mtime is not None:
        os.utime(str(p), (mtime, mtime))
    return p


def _put_transcript(root, text_by_line, pad=0):
    p = root / "t.jsonl"
    rows = [json.dumps({"type": "user", "message": {"content": t}}) for t in text_by_line]
    body = "\n".join(rows) + "\n"
    if pad:
        body += ("z" * 999 + "\n") * (pad // 1000)
    p.write_text(body, encoding="utf-8")
    return p


def test_resolve_active_workflow_real_files():
    now = time.time()
    root = _wf_env()
    try:
        wdir = str(root / "workflows")

        def run(tp, sid="s1"):
            return cc.resolve_active_workflow(wdir, str(tp), sid, "/tmp/proj", now)

        _put_wf(root, "wf-a-1", now)
        t = _put_transcript(root, ["Parent Workflow ID: wf-a-1"])
        snap, reason, wf = run(t)
        assert reason == "single_candidate" and wf == "wf-a-1", (snap, reason, wf)
        assert snap == {"wf": "wf-a-1", "workflow_type": "BUILD", "phase_cursor": "P1", "pending_gate": None,
                        "plan_file": "docs/plans/x.md", "design_file": None}, snap
        # stale
        _put_wf(root, "wf-a-1", now - 13 * 3600)
        assert run(t) == (None, "no_live_candidate", None)
        # terminal
        _put_wf(root, "wf-a-1", now, worktree_mode="merged_and_removed")
        assert run(t) == (None, "no_live_candidate", None)
        # oversize
        _put_wf(root, "wf-a-1", now, raw=b'{"workflow_type":"build","pad":"' + b"x" * 1100000 + b'"}')
        assert run(t) == (None, "no_live_candidate", None)
        # corrupt / non-dict
        _put_wf(root, "wf-a-1", now, raw=b"{not json")
        assert run(t) == (None, "no_live_candidate", None)
        _put_wf(root, "wf-a-1", now, raw=b"[1]")
        assert run(t) == (None, "no_live_candidate", None)
        # two live: latest mention (wf-b-2) is also the newest mtime -> agree
        _put_wf(root, "wf-a-1", now - 100)
        _put_wf(root, "wf-b-2", now - 10)
        t2 = _put_transcript(root, ["first wf-a-1", "later wf-b-2"])
        snap, reason, wf = run(t2)
        assert (reason, wf) == ("mention_mtime_agree", "wf-b-2") and snap["wf"] == "wf-b-2", (snap, reason, wf)
        # disagreeing
        _put_wf(root, "wf-a-1", now - 5)
        assert run(t2) == (None, "ambiguous", None)
        # no mention / placeholder ids / artifact absent
        t3 = _put_transcript(root, ["nothing here", "see wf-<id> and wf-unknown-9"])
        assert run(t3) == (None, "no_mention", None)
        # missing transcript never raises
        r = cc.resolve_active_workflow(wdir, str(root / "missing.jsonl"), "s1", "/tmp/proj", now)
        assert r[0] is None and r[1] in ("lookup_error", "no_mention") and r[2] is None, r
        assert cc.resolve_active_workflow(None, None, None, None, None) == (None, "lookup_error", None)
        # only mention is older than the last MiB window
        _put_wf(root, "wf-a-1", now)
        t4 = _put_transcript(root, ["old mention wf-a-1"], pad=1200000)
        assert t4.stat().st_size > 1048576
        assert run(t4) == (None, "no_mention", None)
    finally:
        shutil.rmtree(str(root), ignore_errors=True)


def test_resolve_binds_router_stamped_session_id():
    """Slice-2 P3: an artifact stamped by the router (session id shape from craftflow_workflow_id.py)
    binds to its own session, beating a newer unstamped artifact; a different session gets no binding."""
    now = time.time()
    root = _wf_env()
    try:
        wdir = str(root / "workflows")
        sid = "0123abcd-4567"
        _put_wf(root, "wf-stamped", now - 30, session_id=sid)
        _put_wf(root, "wf-unstamped", now - 10)
        t = _put_transcript(root, ["wf-stamped", "wf-unstamped"])
        snap, reason, wf = cc.resolve_active_workflow(wdir, str(t), sid, "/tmp/proj", now)
        assert (wf, reason) == ("wf-stamped", "session_match"), (wf, reason)
        # another session never binds the stamped artifact; the unstamped one stays a fallback
        other = cc.resolve_active_workflow(wdir, str(t), "ffffeeee-9999", "/tmp/proj", now)
        assert other[2] == "wf-unstamped", other
        # null stamp (env absent at creation) is a fallback, never a session_match
        assert cc.choose_workflow([_cand("wf-null", 20, session_id=None)], sid) == ("wf-null", "single_candidate")
    finally:
        shutil.rmtree(str(root), ignore_errors=True)


def test_resolve_never_binds_other_session_workflow():
    now = time.time()
    root = _wf_env()
    try:
        wdir = str(root / "workflows")
        _put_wf(root, "wf-other", now, session_id="OTHER")
        t = _put_transcript(root, ["about wf-other"])
        assert cc.resolve_active_workflow(wdir, str(t), "MINE", "/tmp/proj", now) == (None, "no_live_candidate", None)
        _put_wf(root, "wf-a", now - 30, session_id="MINE")
        _put_wf(root, "wf-b", now - 20, session_id="MINE")
        t2 = _put_transcript(root, ["wf-a", "wf-b", "wf-other"])
        snap, reason, wf = cc.resolve_active_workflow(wdir, str(t2), "MINE", "/tmp/proj", now)
        # wf-other (newest mention, other session) is ignored; the tie-break runs among ours only
        assert (wf, reason) == ("wf-b", "mention_mtime_agree"), (wf, reason)
    finally:
        shutil.rmtree(str(root), ignore_errors=True)


def test_resolve_survives_malformed_artifact_and_ghost_flood():
    now = time.time()
    root = _wf_env()
    try:
        wdir = str(root / "workflows")
        _put_wf(root, "wf-bad", now, raw=b"[" * 300000)
        _put_wf(root, "wf-good", now - 5)
        t = _put_transcript(root, ["wf-good", "wf-bad"])
        snap, reason, wf = cc.resolve_active_workflow(wdir, str(t), "s1", "/tmp/proj", now)
        assert wf == "wf-good" and reason == "single_candidate", (snap, reason, wf)
        # a real workflow older than 25 nonexistent ids is still found
        rows = ["wf-good"] + ["wf-ghost-%d" % i for i in range(25)]
        t2 = _put_transcript(root, rows)
        assert cc.resolve_active_workflow(wdir, str(t2), "s1", "/tmp/proj", now)[2] == "wf-good"
        # ids after an escaped newline inside one JSON string are seen
        t3 = root / "t3.jsonl"
        t3.write_text(json.dumps({"c": "intro\nParent: wf-good"}) + "\n", encoding="utf-8")
        assert cc.resolve_active_workflow(wdir, str(t3), "s1", "/tmp/proj", now)[2] == "wf-good"
    finally:
        shutil.rmtree(str(root), ignore_errors=True)


def test_wf_mentions_escape_parity():
    # an escaped boundary needs an ODD backslash run: "\\n" is an escaped backslash then a literal n
    assert cc.wf_mentions(b"a\\nwf-odd-1") == ["wf-odd-1"]
    assert cc.wf_mentions(b"a\\\\nwf-even-1") == []
    assert cc.wf_mentions(b"a\\\\\\nwf-odd-3") == ["wf-odd-3"]
    assert cc.wf_mentions(b"a\\\\\\\\twf-even-4") == []
    assert cc.wf_mentions(b"\\nwf-start-1") == ["wf-start-1"]
    assert cc.wf_mentions(b"nwf-bare-1") == []


def test_resolve_stale_and_terminal_mentions_do_not_fill_candidate_slots():
    now = time.time()
    root = _wf_env()
    try:
        wdir = str(root / "workflows")
        _put_wf(root, "wf-live", now - 50)
        rows = ["wf-live"]
        for i in range(4):
            _put_wf(root, "wf-stale-%d" % i, now - 13 * 3600)
            rows.append("wf-stale-%d" % i)
        for i in range(4):
            _put_wf(root, "wf-term-%d" % i, now, worktree_mode="merged_and_removed")
            rows.append("wf-term-%d" % i)
        t = _put_transcript(root, rows)
        snap, reason, wf = cc.resolve_active_workflow(wdir, str(t), "s1", "/tmp/proj", now)
        assert (wf, reason) == ("wf-live", "single_candidate"), (snap, reason, wf)
        assert snap["wf"] == "wf-live"
        # with nothing live the outcome stays no_live_candidate
        _put_wf(root, "wf-live", now - 13 * 3600)
        assert cc.resolve_active_workflow(wdir, str(t), "s1", "/tmp/proj", now) == (None, "no_live_candidate", None)
    finally:
        shutil.rmtree(str(root), ignore_errors=True)


def test_resolve_unstamped_artifacts_fall_back_best_effort():
    # the router does not stamp session_id into artifacts today: the session filter is inert and binding
    # is best-effort (single candidate, or newest-mention-equals-newest-mtime), else generic (SPEC-0017)
    now = time.time()
    root = _wf_env()
    try:
        wdir = str(root / "workflows")
        _put_wf(root, "wf-a-1", now - 100)
        t = _put_transcript(root, ["wf-a-1"])
        r = cc.resolve_active_workflow(wdir, str(t), "sess-now", "/tmp/proj", now)
        assert (r[1], r[2]) == ("single_candidate", "wf-a-1"), r
        _put_wf(root, "wf-b-2", now - 10)
        t2 = _put_transcript(root, ["wf-a-1", "wf-b-2"])
        r = cc.resolve_active_workflow(wdir, str(t2), "sess-now", "/tmp/proj", now)
        assert (r[1], r[2]) == ("mention_mtime_agree", "wf-b-2"), r
        _put_wf(root, "wf-a-1", now - 5)
        r = cc.resolve_active_workflow(wdir, str(t2), "sess-now", "/tmp/proj", now)
        assert r == (None, "ambiguous", None), r
    finally:
        shutil.rmtree(str(root), ignore_errors=True)


def test_resolve_fifo_artifact_does_not_block_and_fails_open():
    now = time.time()
    root = _wf_env()
    try:
        wdir = str(root / "workflows")
        os.mkfifo(str(root / "workflows" / "wf-fifo.json"))
        _put_wf(root, "wf-good", now - 5)
        t = _put_transcript(root, ["wf-good", "wf-fifo"])
        t0 = time.time()
        snap, reason, wf = cc.resolve_active_workflow(wdir, str(t), "s1", "/tmp/proj", now)
        assert time.time() - t0 < 5
        assert (wf, reason) == ("wf-good", "single_candidate"), (snap, reason, wf)
        t2 = _put_transcript(root, ["wf-fifo"])
        assert cc.resolve_active_workflow(wdir, str(t2), "s1", "/tmp/proj", now)[2] is None
    finally:
        shutil.rmtree(str(root), ignore_errors=True)


def test_resolve_symlink_artifact_is_not_followed():
    now = time.time()
    root = _wf_env()
    try:
        wdir = str(root / "workflows")
        target = _put_wf(root, "wf-real", now - 5)
        os.symlink(str(target), str(root / "workflows" / "wf-link.json"))
        t = _put_transcript(root, ["wf-link"])
        assert cc.resolve_active_workflow(wdir, str(t), "s1", "/tmp/proj", now)[2] is None
        t2 = _put_transcript(root, ["wf-real", "wf-link"])
        assert cc.resolve_active_workflow(wdir, str(t2), "s1", "/tmp/proj", now)[2] == "wf-real"
    finally:
        shutil.rmtree(str(root), ignore_errors=True)


def test_bound_line_names_relative_plan_under_worktree_root():
    wt = "/tmp/some-worktree/proj"
    snap = cc.workflow_snapshot({"workflow_type": "build", "phase_cursor": "P2", "plan_file": "docs/plans/x.md",
                                 "design_file": "docs/plans/d.md"}, "wf-demo-1", wt)
    line = cc.build_compact_line(snap)
    assert "plan docs/plans/x.md" in line and "design docs/plans/d.md" in line, line
    # absolute plan paths outside the project root are intentionally dropped
    out = cc.workflow_snapshot({"workflow_type": "build", "plan_file": "/elsewhere/docs/plans/x.md"},
                               "wf-demo-1", wt)
    assert out["plan_file"] is None
    inside = cc.workflow_snapshot({"workflow_type": "build", "plan_file": wt + "/docs/plans/x.md"}, "wf-demo-1", wt)
    assert inside["plan_file"] == "docs/plans/x.md"


def _run_timing_driver(argv):
    import contextlib
    import importlib.util
    import io
    spec = importlib.util.spec_from_file_location("cn_timing_driver", str(PLUGIN_ROOT / "tests" / "live" / "context_nudge_timing.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(argv)
    return rc, buf.getvalue().strip()


def test_timing_driver_rejects_bad_counts_and_new_baseline():
    root = Path(tempfile.mkdtemp(prefix="cn-drv-"))
    try:
        tr = root / "t.jsonl"
        tr.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n", encoding="utf-8")
        rc, out = _run_timing_driver(["--transcript", str(tr), "--runs", "0"])
        assert rc == 2 and json.loads(out)["error"] == "invalid_runs", (rc, out)
        rc, out = _run_timing_driver(["--transcript", str(tr), "--ab-pairs", "2", "--baseline-ref", "HEAD"])
        assert rc == 2 and json.loads(out)["error"] == "invalid_ab_pairs", (rc, out)
        # HEAD already carries the compact-module import: it is not the old code, so it must not pass as baseline
        rc, out = _run_timing_driver(["--transcript", str(tr), "--baseline-ref", "HEAD"])
        assert rc == 2 and json.loads(out)["error"] == "baseline_not_old_code", (rc, out)
    finally:
        shutil.rmtree(str(root), ignore_errors=True)


def test_compact_wf_regex_matches_main_script():
    assert cc.WF_ID_RE.pattern == cn._WF_RE.pattern
    assert cc.MAX_COMPACT_CHARS == 600
    assert cc.WORKFLOW_FRESH_S == 43200
    assert cc.MENTION_TAIL_BYTES == 1048576
    assert cc.MAX_CANDIDATES == 5
    assert cc.MAX_ARTIFACT_BYTES == 1048576
    for mod in (cn, cc):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert (".claude/" + "craftflow") not in src, mod.__file__
        assert ('.claude" / "' + "craftflow") not in src, mod.__file__


def test_compact_module_import_cheap():
    box = _Box("audit")
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update(CLAUDE_PLUGIN_ROOT=str(box.plugin), CLAUDE_PROJECT_DIR=str(box.project),
               PYTHONPATH=str(SCRIPTS))
    t0 = time.perf_counter()
    proc = subprocess.run([sys.executable, "-c", "import craftflow_context_nudge_compact"], env=env,
                          cwd=str(box.root), capture_output=True, timeout=10)
    elapsed = time.perf_counter() - t0
    assert proc.returncode == 0 and proc.stdout == b"", (proc.returncode, proc.stdout, proc.stderr)
    assert elapsed < 0.5, elapsed
    assert not (box.project / ".craftflow").exists()
    assert not (box.root / ".craftflow").exists()


# ---------------------------------------------------------------------------
# Phase 4: user override + /compact line wired into hook, reset and boundary
# ---------------------------------------------------------------------------

_LINE_MARK = "(do not run it): "


def _user_file(box, obj_or_bytes):
    """Write the scratch user-override file and return the env_extra that points the seam at it."""
    path = box.root / "user-override.json"
    if isinstance(obj_or_bytes, bytes):
        path.write_bytes(obj_or_bytes)
    else:
        path.write_text(json.dumps(obj_or_bytes), encoding="utf-8")
    return {"CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG": str(path)}


def _transcript_with_mention(box, tokens, *wfs):
    rows = [json.dumps({"type": "user", "message": {"content": "working on " + wf}}) for wf in wfs]
    rows.append(_assistant_line(tokens))
    path = box.tdir / "t.jsonl"
    _write_lines(path, rows)
    return path


def _advisory_line(msg):
    assert _LINE_MARK in msg, msg
    return msg.split(_LINE_MARK, 1)[1]


def _bound_line(wf=WF):
    return ("/compact Keep craftflow workflow " + wf + " (BUILD; phase P1; plan docs/plans/x.md). "
            "Preserve its decisions, open questions, failing checks and next step; after compaction "
            "resume from .craftflow/state/workflows/" + wf + ".json.")


def test_hook_user_on_beats_plugin_audit():
    box = _Box("audit")
    box.transcript(130000)
    env = _user_file(box, {"contextNudge": "on"})
    code, out, _err, rows, _ = run_script(_prompt(box), box=box, env_extra=env)
    assert code == 0, code
    payload = json.loads(out)
    msg = payload["systemMessage"]
    assert msg == payload["hookSpecificOutput"]["additionalContext"], payload
    assert _advisory_line(msg).startswith("/compact "), msg
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["outcome"] == "nudged" and row["override"] == "applied", row
    assert row["override_keys"] == ["contextNudge"] and row["mode"] == "on", row


def test_hook_user_off_beats_plugin_on_inert():
    box = _Box("on", config={"warnTokens": 1000, "criticalTokens": 100000, "assumedWindow": 200000})
    box.transcript(130000)
    env = _user_file(box, {"contextNudge": "off"})
    before = _fs_snapshot(box.project)
    code, out, _err, rows, _ = run_script(_prompt(box), box=box, env_extra=env)
    assert code == 0 and out == "" and rows == [], (code, out, rows)
    assert _fs_snapshot(box.project) == before


def test_hook_user_thresholds_layer_over_plugin():
    box = _Box("on")
    box.transcript(5000)
    env = _user_file(box, {"warnTokens": 1000})
    code, out, _err, rows, _ = run_script(_prompt(box), box=box, env_extra=env)
    assert code == 0 and json.loads(out)["systemMessage"], (code, out)
    assert len(rows) == 1 and rows[0]["outcome"] == "nudged" and rows[0]["level"] == "warn", rows
    assert rows[0]["threshold"] == 1000 and rows[0]["override_keys"] == ["warnTokens"], rows


def test_hook_user_corrupt_falls_back_and_logs():
    box = _Box("on", config={"warnTokens": 1000, "criticalTokens": 100000, "assumedWindow": 200000})
    box.transcript(5000)
    env = _user_file(box, b"{not json")
    code, out, _err, rows, _ = run_script(_prompt(box), box=box, env_extra=env)
    assert code == 0 and json.loads(out)["systemMessage"], (code, out)
    assert len(rows) == 1 and rows[0]["outcome"] == "nudged" and rows[0]["threshold"] == 1000, rows
    assert rows[0]["override"] == "error" and rows[0]["override_error"] == "corrupt", rows[0]


def test_hook_user_partial_invalid_value():
    box = _Box("audit")
    box.transcript(130000)
    env = _user_file(box, {"contextNudge": "on", "warnTokens": True})
    code, out, _err, rows, _ = run_script(_prompt(box), box=box, env_extra=env)
    assert code == 0 and json.loads(out)["systemMessage"], (code, out)
    row = rows[0]
    assert row["outcome"] == "nudged" and row["override"] == "partial", row
    assert row["override_error"] == "invalid_value" and row["override_keys"] == ["contextNudge"], row


def test_hook_user_inconsistent_thresholds_fall_back():
    box = _Box("on")
    box.transcript(130000)
    env = _user_file(box, {"warnTokens": 200000, "criticalTokens": 150000})
    code, out, _err, rows, _ = run_script(_prompt(box), box=box, env_extra=env)
    assert code == 0 and json.loads(out)["systemMessage"], (code, out)
    row = rows[0]
    assert row["outcome"] == "nudged" and row["level"] == "warn" and row["threshold"] == 120000, row
    assert row["override_error"] == "inconsistent_thresholds", row


def test_hook_nudge_line_bound_to_mentioned_workflow():
    box = _Box("on")
    _seed_wf(box)
    _transcript_with_mention(box, 130000, WF)
    code, out, _err, rows, _ = run_script(_prompt(box), box=box)
    assert code == 0, code
    msg = json.loads(out)["systemMessage"]
    assert msg.endswith(_LINE_MARK + _bound_line()), msg
    assert "\n" not in msg
    row = rows[0]
    assert row["compact_source"] == "workflow" and row["compact_reason"] == "single_candidate", row
    assert row["compact_wf"] == WF, row


def test_hook_nudge_generic_line_without_workflow():
    box = _Box("on")
    box.transcript(130000)
    code, out, _err, rows, _ = run_script(_prompt(box), box=box)
    assert code == 0, code
    msg = json.loads(out)["systemMessage"]
    assert msg.endswith(_LINE_MARK + cc.GENERIC_COMPACT_LINE), msg
    row = rows[0]
    assert row["compact_source"] == "generic" and row["compact_reason"] == "no_mention", row
    assert row["compact_wf"] is None, row


def test_hook_nudge_ambiguous_two_live_generic():
    box = _Box("on")
    _seed_wf(box, "wf-t-0001")
    _seed_wf(box, "wf-t-0002")
    wdir = box.project / ".craftflow" / "state" / "workflows"
    now = time.time()
    os.utime(str(wdir / "wf-t-0001.json"), (now - 10, now - 10))
    os.utime(str(wdir / "wf-t-0002.json"), (now - 100, now - 100))
    _transcript_with_mention(box, 130000, "wf-t-0001", "wf-t-0002")  # latest mention = older mtime
    code, out, _err, rows, _ = run_script(_prompt(box), box=box)
    assert code == 0, code
    assert json.loads(out)["systemMessage"].endswith(_LINE_MARK + cc.GENERIC_COMPACT_LINE)
    row = rows[0]
    assert row["compact_source"] == "generic" and row["compact_reason"] == "ambiguous", row
    assert row["compact_wf"] is None, row


def test_hook_audit_would_nudge_logs_binding_no_stdout():
    box = _Box("audit")
    _seed_wf(box)
    _transcript_with_mention(box, 130000, WF)
    code, out, _err, rows, _ = run_script(_prompt(box), box=box)
    assert code == 0 and out == "", (code, out)
    row = rows[0]
    assert row["outcome"] == "would_nudge" and row["compact_source"] == "workflow", row
    assert row["compact_reason"] == "single_candidate" and row["compact_wf"] == WF, row


def test_hook_no_change_path_never_looks_up():
    import contextlib
    import io
    box = _Box("on")
    tpath = box.transcript(130000)
    assert cn.save_state(box.state_dir / "s1.json", {
        "schema": 1, "session_id": "s1", "last_level": "warn", "boundary_level": "warn",
        "last_tokens": 130000, "transcript_path": str(tpath), "updated_at": "2026-01-01T00:00:00Z"})
    original = cc.resolve_active_workflow
    saved_env = {k: os.environ.get(k) for k in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR")}

    def boom(*_a, **_k):
        raise RuntimeError("lookup must not run")

    try:
        os.environ["CLAUDE_PLUGIN_ROOT"] = str(box.plugin)
        os.environ["CLAUDE_PROJECT_DIR"] = str(box.project)
        cc.resolve_active_workflow = boom
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert cn.hook_main(_prompt(box)) == 0
        assert buf.getvalue() == "" and log_rows(box) == [], (buf.getvalue(), log_rows(box))
        # a fresh crossing does look up; a raising lookup degrades to the generic line, never suppresses
        assert cn.save_state(box.state_dir / "s1.json", {
            "schema": 1, "session_id": "s1", "last_level": "none", "boundary_level": "none",
            "last_tokens": 1, "transcript_path": str(tpath), "updated_at": "2026-01-01T00:00:00Z"})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert cn.hook_main(_prompt(box)) == 0
        rows = log_rows(box)
        assert len(rows) == 1 and rows[0]["compact_reason"] == "lookup_error", rows
        assert rows[0]["compact_source"] == "generic" and rows[0]["compact_wf"] is None, rows
        assert json.loads(buf.getvalue())["systemMessage"].endswith(_LINE_MARK + cc.GENERIC_COMPACT_LINE)
    finally:
        cc.resolve_active_workflow = original
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_reset_honors_user_off():
    box = _Box("audit")
    box.transcript(130000)
    run_script(_prompt(box), box=box)
    assert (box.state_dir / "s1.json").exists()
    env = _user_file(box, {"contextNudge": "off"})
    reset = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "s1"}
    code, out, _err, rows, _ = run_script(reset, box=box, args=("--reset",), env_extra=env)
    assert code == 0 and out == "", (code, out)
    assert [r for r in rows if r.get("source") == "reset"] == [], rows
    assert (box.state_dir / "s1.json").exists()


def test_boundary_user_override_mode_and_thresholds():
    box = _Box("audit")
    _seed_wf(box)
    _seed_session(box, "s1", 5000)
    env = _user_file(box, {"contextNudge": "on", "warnTokens": 1000})
    r = _bnd(box, env_extra=env)
    assert r["relay"] is True and r["level"] == "warn" and r["threshold"] == 1000, r
    assert r["mode"] == "on" and r["outcome"] == "advised", r


def test_boundary_advisory_and_checkpoint_carry_line():
    box = _Box("on")
    _seed_wf(box)
    _seed_session(box, "s1", 130000)
    r = _bnd(box, "--phase", "P1")
    assert set(r.keys()) == {"schema", *cn._BOUNDARY_KEYS}, sorted(r)
    line = _bound_line()
    assert r["advisory"].endswith(_LINE_MARK + line), r["advisory"]
    snap = json.loads(_checkpoint(box).read_text(encoding="utf-8"))
    assert snap["compact_command"] == line, snap.get("compact_command")
    row = log_rows(box)[-1]
    assert row["compact_reason"] == "explicit_wf" and row["compact_source"] == "workflow", row
    assert row["compact_wf"] == WF, row


def test_boundary_missing_or_bad_wf_generic_line():
    box = _Box("on")
    _seed_session(box, "s1", 130000)
    r = _bnd(box)
    assert r["advisory"].endswith(_LINE_MARK + cc.GENERIC_COMPACT_LINE), r
    row = log_rows(box)[-1]
    assert row["compact_reason"] == "workflow_missing" and row["compact_source"] == "generic", row
    assert row["compact_wf"] is None, row
    box2 = _Box("on")
    _seed_session(box2, "s1", 130000)
    r2 = _bnd(box2, wf="../x")
    assert r2["error"] == "bad_wf" and r2["advisory"].endswith(_LINE_MARK + cc.GENERIC_COMPACT_LINE), r2
    assert log_rows(box2)[-1]["compact_reason"] == "bad_wf"
    box3 = _Box("on")
    _seed_session(box3, "s1", 130000)
    wdir = box3.project / ".craftflow" / "state" / "workflows"
    wdir.mkdir(parents=True)
    (wdir / (WF + ".json")).write_text("{bad", encoding="utf-8")
    r3 = _bnd(box3)
    assert r3["advisory"].endswith(_LINE_MARK + cc.GENERIC_COMPACT_LINE), r3
    assert log_rows(box3)[-1]["compact_reason"] == "workflow_unreadable"


def test_rows_carry_override_fields_no_event_key():
    box = _Box("on")
    _seed_wf(box)
    _transcript_with_mention(box, 130000, WF)
    run_script(_prompt(box), box=box)
    _bnd(box, "--phase", "P1")
    reset = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "s1"}
    run_script(reset, box=box, args=("--reset",))
    rows = log_rows(box)
    assert sorted(r["source"] for r in rows) == ["boundary", "hook", "reset"], rows
    for r in rows:
        assert r["event"] == "context_nudge", r
        for key in ("override", "override_error", "override_keys"):
            assert key in r, (key, r)
        if r["source"] in ("hook", "boundary"):
            for key in ("compact_source", "compact_reason", "compact_wf"):
                assert key in r, (key, r)


def test_env_seam_beats_real_home():
    fake_home = Path(tempfile.mkdtemp(prefix="cn-home-"))
    _BOXES.append(fake_home)
    target = Path(fake_home, *cn.USER_OVERRIDE_SEGMENTS)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"contextNudge": "on"}), encoding="utf-8")
    box = _Box("audit")
    box.transcript(130000)
    code, out, _err, rows, _ = run_script(_prompt(box), box=box, env_extra={"HOME": str(fake_home)})
    assert code == 0 and out == "", (code, out)  # the inherited absent seam beats the HOME file
    assert rows[0]["outcome"] == "would_nudge" and rows[0]["override"] == "absent", rows
    box2 = _Box("audit")
    box2.transcript(130000)
    code, out, _err, rows, _ = run_script(_prompt(box2), box=box2, env_extra={
        "HOME": str(fake_home), "CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG": ""})
    assert code == 0 and json.loads(out)["systemMessage"], (code, out)
    assert rows[0]["outcome"] == "nudged" and rows[0]["override"] == "applied", rows


# ---------------------------------------------------------------------------
# Phase 5: docs describe the /compact line and the durable user override
# ---------------------------------------------------------------------------

_USER_FILE_DOC = "~/.claude/craftflow/context-nudge.json"


def test_contract_doc_documents_override_and_compact_fields():
    doc = _read(PLUGIN_ROOT / "docs" / "craftflow-event-contract.md")
    section = doc.split("### Log event: `context_nudge`", 1)[1]
    needles = ["`override`", "`override_error`", "`override_keys`", "`compact_source`",
               "`compact_reason`", "`compact_wf`", "`inconsistent_thresholds`",
               "`mention_mtime_agree`", "`explicit_wf`", _USER_FILE_DOC]
    needles += ["`" + r + "`" for r in ("session_match", "single_candidate", "ambiguous", "no_mention",
                                        "no_live_candidate", "lookup_error", "workflow_missing",
                                        "workflow_unreadable", "bad_wf")]
    for needle in needles:
        assert needle in section, needle
    assert "another session" in section, "other-session workflow never bound must be documented"


def test_context_boundary_reference_ready_to_paste_line():
    flat = " ".join(_read(REFS / "context-boundary.md").split())
    assert "ready-to-paste" in flat and "on its own line" in flat
    for needle in ("`relay` is literally `true`", "end the turn", "never run /compact yourself",
                   "Run that /compact command, then say continue", "include `advisory` verbatim"):
        assert needle in flat, needle


def test_docs_name_user_override_path():
    for path in (PLUGIN_ROOT / "README.md", PLUGIN_ROOT / "hooks" / "README.md",
                 PLUGIN_ROOT / "skills" / "session-memory" / "references" / "context-budget-and-checkpointing.md",
                 REFS / "workflow-artifact-and-hook-policy.md"):
        assert _USER_FILE_DOC in _read(path), str(path)
    readme = _read(PLUGIN_ROOT / "README.md")
    assert "CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG" in readme and "survives plugin updates" in readme


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    print("test_craftflow_context_nudge: running")
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
