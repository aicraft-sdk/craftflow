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
