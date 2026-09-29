#!/usr/bin/env python3
"""Tests for craftflow_context_nudge.py (SPEC-0016 / ADR-0051), pure core + state/transcript I/O.

Run: python3 tests/fixtures/test_craftflow_context_nudge.py
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
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


def test_import_has_no_side_effects_and_main_stub_returns_zero():
    assert cn.SCHEMA_VERSION == 1 and cn.LEVELS == ("none", "warn", "critical")
    assert cn.TAIL_BYTES == 262144 and cn.RETRY_TAIL_BYTES == 4194304
    assert cn.BOUNDARY_STATE_MAX_AGE_S == 43200 and cn.STATE_PRUNE_AGE_S == 1209600
    assert cn.STATE_DIRNAME == "context-nudge"
    assert cn.main([]) == 0


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
