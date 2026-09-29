#!/usr/bin/env python3
"""Tests for craftflow_transcript_usage.py.

Run: python3 tests/fixtures/test_craftflow_transcript_usage.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import craftflow_transcript_usage as ctu  # noqa: E402
from craftflow_transcript_usage import summarize_lines  # noqa: E402

_passes = 0
_errors = []


def ok(name):
    global _passes
    _passes += 1
    print("  PASS: " + name)


def fail(name, reason):
    _errors.append("FAIL [" + name + "]: " + reason)
    print("  FAIL: " + name + ": " + reason)


def _assistant(mid, model, out, inp=2, cr=0, cc=0, cc5=None, cc1=None,
               ts="2026-09-27T22:30:57.563Z", speed=None, effort="high",
               agent="craftflow:component-builder", content=None):
    """One JSON transcript line shaped like a real assistant record."""
    usage = {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cr,
        "cache_creation_input_tokens": cc,
    }
    if cc5 is not None or cc1 is not None:
        usage["cache_creation"] = {
            "ephemeral_5m_input_tokens": cc5 or 0,
            "ephemeral_1h_input_tokens": cc1 or 0,
        }
    if speed is not None:
        usage["speed"] = speed
    message = {"role": "assistant", "usage": usage}
    if model is not None:
        message["model"] = model
    if mid is not None:
        message["id"] = mid
    if content is not None:
        message["content"] = content
    return json.dumps({
        "type": "assistant",
        "timestamp": ts,
        "effort": effort,
        "attributionAgent": agent,
        "message": message,
    })


def _user(content, ts="2026-09-27T22:30:50.000Z"):
    return json.dumps({
        "type": "user",
        "timestamp": ts,
        "message": {"role": "user", "content": content},
    })


SONNET = "claude-sonnet-5"
OPUS = "claude-opus-5-5"


# ---------------------------------------------------------------------------
# P1.1 single model
# ---------------------------------------------------------------------------

def test_single_model_two_messages():
    lines = [
        _assistant("m1", SONNET, 100, inp=3, cr=10, cc=50),
        _assistant("m2", SONNET, 250, inp=4, cr=20, cc=0),
    ]
    r = summarize_lines(lines)
    m = r["models"][SONNET]
    assert m["output_tokens"] == 350, m
    assert m["input_tokens"] == 7, m
    assert m["cache_read_input_tokens"] == 30, m
    assert m["cache_creation_input_tokens"] == 50, m
    assert m["messages"] == 2, m
    assert r["turns"] == 2, r
    assert r["primary_model"] == SONNET
    assert r["error"] is None and r["partial"] is False
    assert r["effort"] == "high"
    assert r["attribution_agent"] == "craftflow:component-builder"


# ---------------------------------------------------------------------------
# P1.2 dedupe (E2, E3) and properties P1-P3
# ---------------------------------------------------------------------------

def test_duplicate_id_identical_counted_once():
    line = _assistant("m1", SONNET, 100, inp=3, cr=10, cc=50)
    r = summarize_lines([line, line])
    m = r["models"][SONNET]
    assert m["output_tokens"] == 100 and m["input_tokens"] == 3, m
    assert m["cache_read_input_tokens"] == 10 and m["cache_creation_input_tokens"] == 50, m
    assert m["messages"] == 1 and r["turns"] == 1, r


def test_duplicate_id_growing_output_takes_max():
    lines = [_assistant("m1", SONNET, n) for n in (6, 120, 480)]
    r = summarize_lines(lines)
    assert r["models"][SONNET]["output_tokens"] == 480, r["models"]
    assert r["models"][SONNET]["messages"] == 1


_SIX = [
    _assistant("a", SONNET, 5, inp=1, cr=7, cc=9),
    _assistant("a", SONNET, 50, inp=1, cr=7, cc=9),
    _assistant("b", SONNET, 20, inp=2, cr=3, cc=0),
    _assistant("c", OPUS, 30, inp=4, cr=0, cc=11),
    _assistant("c", OPUS, 60, inp=4, cr=0, cc=11),
    _assistant("d", OPUS, 1, inp=1),
]


def test_property_duplicate_append_idempotent():
    base = summarize_lines(_SIX)
    for rec in _SIX:
        again = summarize_lines(_SIX + [rec])
        assert again["models"] == base["models"], (rec, again["models"], base["models"])
        assert again["turns"] == base["turns"]


def test_property_permutation_within_id():
    import itertools
    chunks = [_assistant("z", SONNET, n, inp=2, cr=5) for n in (10, 90, 400)]
    expected = summarize_lines(chunks)["models"]
    assert expected[SONNET]["output_tokens"] == 400
    for perm in itertools.permutations(chunks):
        assert summarize_lines(list(perm))["models"] == expected


def test_property_dedup_le_naive():
    def naive(lines):
        tot = {}
        for ln in lines:
            msg = json.loads(ln)["message"]
            u = msg["usage"]
            b = tot.setdefault(msg["model"], {"input_tokens": 0, "output_tokens": 0,
                                              "cache_read_input_tokens": 0,
                                              "cache_creation_input_tokens": 0})
            for k in b:
                b[k] += u[k]
        return tot

    deduped = summarize_lines(_SIX)["models"]
    nv = naive(_SIX)
    for model, fields in nv.items():
        for k, v in fields.items():
            assert deduped[model][k] <= v, (model, k)
    assert deduped[SONNET]["output_tokens"] < nv[SONNET]["output_tokens"]
    unique = [_assistant("u" + str(i), SONNET, 10 * i, inp=i) for i in range(1, 5)]
    nv2 = naive(unique)
    d2 = summarize_lines(unique)["models"]
    for k, v in nv2[SONNET].items():
        assert d2[SONNET][k] == v, k


# ---------------------------------------------------------------------------
# P1.3 mixed models, unkeyed, cache split, fast, missing model
# ---------------------------------------------------------------------------

def test_mixed_models_two_buckets_primary_is_max_output():
    lines = [
        _assistant("a", SONNET, 100),
        _assistant("b", OPUS, 500),
        _assistant("c", SONNET, 150),
    ]
    r = summarize_lines(lines)
    assert sorted(r["models"]) == sorted([SONNET, OPUS]), r["models"]
    assert r["models"][SONNET]["output_tokens"] == 250
    assert r["models"][OPUS]["output_tokens"] == 500
    assert r["primary_model"] == OPUS
    assert r["turns"] == 3


def test_primary_model_tie_first_seen():
    r = summarize_lines([_assistant("a", OPUS, 100), _assistant("b", SONNET, 100)])
    assert r["primary_model"] == OPUS
    r2 = summarize_lines([_assistant("a", SONNET, 100), _assistant("b", OPUS, 100)])
    assert r2["primary_model"] == SONNET


def test_unkeyed_counted_individually():
    lines = [_assistant(None, SONNET, 10), _assistant(None, SONNET, 10),
             _assistant("k", SONNET, 5)]
    r = summarize_lines(lines)
    assert r["unkeyed_messages"] == 2, r
    assert r["models"][SONNET]["output_tokens"] == 25
    assert r["models"][SONNET]["messages"] == 3


def test_cache_creation_split_5m_1h():
    r = summarize_lines([_assistant("a", SONNET, 1, cc=300, cc5=100, cc1=200)])
    m = r["models"][SONNET]
    assert m["cache_creation_input_tokens"] == 300
    assert m["cache_write_5m"] == 100 and m["cache_write_1h"] == 200, m


def test_cache_creation_without_subdict_goes_5m():
    r = summarize_lines([_assistant("a", SONNET, 1, cc=300)])
    m = r["models"][SONNET]
    assert m["cache_write_5m"] == 300 and m["cache_write_1h"] == 0, m


def test_fast_speed_counted():
    r = summarize_lines([_assistant("a", SONNET, 1, speed="fast"),
                         _assistant("b", SONNET, 1, speed="standard"),
                         _assistant("c", SONNET, 1)])
    assert r["models"][SONNET]["fast_messages"] == 1, r["models"]
    assert r["models"][SONNET]["messages"] == 3


def test_missing_model_bucket_unknown():
    r = summarize_lines([_assistant("a", None, 7)])
    assert list(r["models"]) == ["unknown"], r["models"]
    assert r["models"]["unknown"]["output_tokens"] == 7
    assert r["primary_model"] == "unknown"


def test_synthetic_model_kept_literal():
    r = summarize_lines([_assistant("a", "<synthetic>", 0)])
    assert list(r["models"]) == ["<synthetic>"], r["models"]


# ---------------------------------------------------------------------------
# P1.4 corruption and invalid values
# ---------------------------------------------------------------------------

def test_corrupt_line_skipped_counted():
    r = summarize_lines(["{not json", _assistant("a", SONNET, 9), "", "]]"])
    assert r["corrupt_lines"] == 2, r["corrupt_lines"]  # blank lines are skipped, not corrupt
    assert r["models"][SONNET]["output_tokens"] == 9


def test_non_dict_rows_skipped():
    lines = [
        "[1,2]",
        '"str"',
        "42",
        "null",
        json.dumps({"type": "assistant", "message": []}),
        json.dumps({"type": "assistant", "message": {"id": "x", "model": SONNET, "usage": "x"}}),
        json.dumps({"type": "assistant"}),
        _assistant("ok", SONNET, 4),
    ]
    r = summarize_lines(lines)
    assert r["corrupt_lines"] == 0, r["corrupt_lines"]
    assert r["models"][SONNET]["output_tokens"] == 4
    assert r["models"][SONNET]["messages"] == 1


def test_invalid_usage_values_coerced():
    def rec(mid, val):
        return json.dumps({"type": "assistant", "message": {
            "id": mid, "model": SONNET, "usage": {"output_tokens": val, "input_tokens": 1}}})
    lines = [rec("a", True), rec("b", -5), rec("c", 1.5), rec("d", "10"),
             rec("e", int("9" * 400)), rec("f", 8)]
    r = summarize_lines(lines)
    assert r["models"][SONNET]["output_tokens"] == 8, r["models"]
    assert r["invalid_usage_values"] == 5, r["invalid_usage_values"]
    assert r["models"][SONNET]["input_tokens"] == 6


def test_no_assistant_records():
    r = summarize_lines([_user("hello")])
    assert r["models"] == {} and r["turns"] == 0 and r["error"] is None
    assert r["primary_model"] is None
    r2 = summarize_lines([])
    assert r2["models"] == {} and r2["turns"] == 0 and r2["error"] is None


def test_bad_timestamps_duration_none():
    r = summarize_lines([_assistant("a", SONNET, 1, ts="not-a-time"),
                         _assistant("b", SONNET, 1, ts="also bad")])
    assert r["duration_s"] is None, r["duration_s"]
    lines = [json.dumps({"type": "assistant", "message": {
        "id": "a", "model": SONNET, "usage": {"output_tokens": 1}}})]
    assert summarize_lines(lines)["duration_s"] is None
    good = summarize_lines([_assistant("a", SONNET, 1, ts="2026-09-27T22:30:50.000Z"),
                            _assistant("b", SONNET, 1, ts="2026-09-27T22:31:02.400Z")])
    assert good["duration_s"] == 12.4, good["duration_s"]


# ---------------------------------------------------------------------------
# P1.5 dispatch metadata extraction
# ---------------------------------------------------------------------------

def test_extract_parent_workflow_id():
    text = (
        "## Task Context\n- Task ID: 5\n"
        "- Parent Workflow ID: wf-plan-x-20260929-090925-e7f2a271\n"
        "- Task Phase: build-implement (phase P1)\n"
        "Earlier run was wf-other-20260101-000000-deadbeef for reference.\n"
    )
    md = ctu.extract_dispatch_metadata(text)
    assert md["workflow_id"] == "wf-plan-x-20260929-090925-e7f2a271", md
    assert md["dispatch_phase"] == "build-implement (phase P1)", md
    assert md["is_remfix"] is False


def test_extract_workflow_scope_only():
    text = "- Workflow Scope: wf:wf-craftflow-dispatch-20260929-102442-b80ce07e\n"
    md = ctu.extract_dispatch_metadata(text)
    assert md["workflow_id"] == "wf-craftflow-dispatch-20260929-102442-b80ce07e", md
    text2 = "- Workflow Scope: `wf:wf-a-20260929-102442-b80ce07e`\n"
    assert ctu.extract_dispatch_metadata(text2)["workflow_id"] == "wf-a-20260929-102442-b80ce07e"


def test_extract_bare_wf_line():
    text = "wf:wf-execute-plan-optional-jev-typesa-20260920-103306-86f39646\nkind:remfix\nphase:re-hunt"
    md = ctu.extract_dispatch_metadata(text)
    assert md["workflow_id"] == "wf-execute-plan-optional-jev-typesa-20260920-103306-86f39646", md
    assert md["dispatch_phase"] == "re-hunt", md
    assert md["is_remfix"] is True


def test_extract_task_phase_remfix_label():
    text = "Task Phase: " + ("build-implement (REM-FIX cycle 2) " + "x" * 200) + "\n"
    md = ctu.extract_dispatch_metadata(text)
    assert md["is_remfix"] is True, md
    assert len(md["dispatch_phase"]) == 80, len(md["dispatch_phase"])
    assert md["dispatch_phase"].startswith("build-implement (REM-FIX cycle 2)")


def test_extract_none():
    md = ctu.extract_dispatch_metadata("nothing to see here")
    assert md == {"workflow_id": None, "dispatch_phase": None, "is_remfix": False}, md
    assert ctu.extract_dispatch_metadata(None) == md
    assert ctu.extract_dispatch_metadata(12) == md


def test_first_user_content_list_blocks():
    content = [
        {"type": "tool_result", "content": "ignored"},
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "Parent Workflow ID: wf-abc-20260929-000000-0123abcd\nTask Phase: plan"},
    ]
    lines = [_user(content), _user("Parent Workflow ID: wf-later-20260929-000000-ffffffff"),
             _assistant("a", SONNET, 1)]
    r = summarize_lines(lines)
    assert r["workflow_id"] == "wf-abc-20260929-000000-0123abcd", r["workflow_id"]
    assert r["dispatch_phase"] == "plan"
    r2 = summarize_lines([_user("Parent Workflow ID: wf-str-20260929-000000-0123abcd"),
                          _assistant("a", SONNET, 1)])
    assert r2["workflow_id"] == "wf-str-20260929-000000-0123abcd"


# ---------------------------------------------------------------------------
# P1.6 file shell, deadline, totality
# ---------------------------------------------------------------------------

def _tmp_jsonl(data, name="agent-x.jsonl"):
    d = tempfile.mkdtemp(prefix="ctu-")
    path = os.path.join(d, name)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(data)
    return path


def test_summarize_transcript_reads_file():
    path = _tmp_jsonl("\n".join([_user("Parent Workflow ID: wf-f-20260929-000000-0123abcd"),
                                 _assistant("a", SONNET, 33)]) + "\n")
    r = ctu.summarize_transcript(path)
    assert r["error"] is None, r
    assert r["models"][SONNET]["output_tokens"] == 33
    assert r["workflow_id"] == "wf-f-20260929-000000-0123abcd"


def test_missing_file():
    r = ctu.summarize_transcript(os.path.join(tempfile.gettempdir(), "ctu-nope", "a.jsonl"))
    assert r["error"] == "transcript_missing", r
    assert r["models"] == {}


def test_empty_path():
    for value in ("", None):
        r = ctu.summarize_transcript(value)
        assert r["error"] == "no_transcript_path", r
        assert r["models"] == {}


def test_directory_path():
    d = tempfile.mkdtemp(prefix="ctu-dir-") 
    sub = os.path.join(d, "x.jsonl")
    os.mkdir(sub)
    r = ctu.summarize_transcript(sub)
    assert r["error"] == "transcript_not_regular_jsonl", r


def test_non_jsonl_suffix():
    path = _tmp_jsonl(_assistant("a", SONNET, 1) + "\n", name="agent-x.txt")
    r = ctu.summarize_transcript(path)
    assert r["error"] == "transcript_not_regular_jsonl", r
    assert r["models"] == {}


def test_too_large():
    path = _tmp_jsonl(_assistant("a", SONNET, 1) + "\n")
    r = ctu.summarize_transcript(path, max_bytes=10)
    assert r["error"] == "transcript_too_large", r
    assert r["models"] == {}


class _Clock(object):
    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def test_deadline_partial():
    lines = [_assistant("id" + str(i), SONNET, 1) for i in range(450)]
    r = summarize_lines(lines, clock=_Clock([0, 10]), deadline_s=1)
    assert r["partial"] is True, r["partial"]
    assert r["error"] == "time_budget_exceeded", r["error"]
    assert r["models"][SONNET]["messages"] == 200, r["models"]
    # no deadline: whole input is consumed
    full = summarize_lines(lines, clock=_Clock([0, 10]))
    assert full["partial"] is False and full["models"][SONNET]["messages"] == 450
    # generous deadline: not partial
    fast = summarize_lines(lines, clock=_Clock([0, 0.5]), deadline_s=1)
    assert fast["partial"] is False and fast["error"] is None


def test_deadline_partial_via_transcript():
    lines = [_assistant("id" + str(i), SONNET, 1) for i in range(450)]
    path = _tmp_jsonl("\n".join(lines) + "\n")
    r = ctu.summarize_transcript(path, clock=_Clock([0, 10]), deadline_s=1)
    assert r["partial"] is True and r["error"] == "time_budget_exceeded", r
    assert r["models"][SONNET]["messages"] == 200


def test_invalid_utf8_line():
    good = _assistant("a", SONNET, 5)
    bad_inside = good.replace('"role"', '"r\u00f6le"')
    data = (b"\xff\xfe{broken\n" + good.encode("utf-8") + b"\n"
            + b'{"type": "user", "message": {"content": "caf\xe9 \xff"}}\n')
    r = ctu.summarize_transcript(_tmp_jsonl(data))
    assert r["error"] is None, r
    assert r["corrupt_lines"] == 1, r["corrupt_lines"]
    assert r["models"][SONNET]["output_tokens"] == 5
    assert bad_inside  # keep flake quiet


def test_totality_never_raises():
    bad_inputs = [
        "", None, 0, [], {},
        "/definitely/not/here.jsonl",
        tempfile.gettempdir(),
        _tmp_jsonl("", name="empty.jsonl"),
        _tmp_jsonl("{{{{\n]]]]\n\x00\x01\n", name="junk.jsonl"),
        _tmp_jsonl(b"\xff\xff\xff\n", name="bytes.jsonl"),
        _tmp_jsonl("[1,2]\n\"s\"\n", name="nondict.jsonl"),
    ]
    for value in bad_inputs:
        r = ctu.summarize_transcript(value)
        assert isinstance(r, dict), value
        assert set(r) == set(ctu.summarize_lines([])), (value, sorted(r))
        assert isinstance(r["models"], dict)
    # empty file: no error, no models
    empty = ctu.summarize_transcript(bad_inputs[7])
    assert empty["error"] is None and empty["models"] == {} and empty["turns"] == 0
    # non-str line items never raise
    assert isinstance(summarize_lines([None, 5, b"x", object()]), dict)


def test_result_key_sets():
    expected = {"models", "primary_model", "turns", "duration_s", "effort",
                "attribution_agent", "workflow_id", "dispatch_phase", "is_remfix",
                "unkeyed_messages", "corrupt_lines", "invalid_usage_values",
                "partial", "error"}
    assert set(summarize_lines([])) == expected
    assert set(summarize_lines([], want_final_text=True)) == expected | {"final_text"}
    assert set(ctu.summarize_transcript("")) == expected
    assert set(ctu.summarize_transcript("", want_final_text=True)) == expected | {"final_text"}
    assert ctu.SCHEMA_VERSION == 1


# ---------------------------------------------------------------------------
# P1.7 contract classification (DD-13) and final-text capture
# ---------------------------------------------------------------------------

def _yaml_contract(fields, status="COMPLETE", heading="### Router Contract (MACHINE-READABLE)"):
    body = ["STATUS: " + status]
    body += [f + ": x" for f in fields if f != "STATUS"]
    return "Work done.\n\n" + heading + "\n```yaml\n" + "\n".join(body) + "\n```\n"


def _builder_fields():
    import craftflow_contract_validate as ccv
    return list(ccv.REQUIRED_FIELDS["component-builder"])


def test_classify_yaml_block_valid():
    text = _yaml_contract(_builder_fields())
    got = ctu.classify_contract(text, "component-builder")
    assert got == {"contract_shape": "yaml_block", "contract_valid": True,
                   "contract_errors": []}, got


def test_classify_yaml_block_missing_field_invalid():
    fields = [f for f in _builder_fields() if f != "PHASE_ID"]
    got = ctu.classify_contract(_yaml_contract(fields), "component-builder")
    assert got["contract_shape"] == "yaml_block"
    assert got["contract_valid"] is False, got
    assert got["contract_errors"] == ["missing required field: PHASE_ID"], got


def test_classify_yaml_block_errors_capped_and_truncated():
    got = ctu.classify_contract(_yaml_contract(["STATUS"]), "component-builder")
    assert got["contract_valid"] is False
    assert got["contract_errors"] == ["missing required field: " + f
                                      for f in _builder_fields()[1:4]], got["contract_errors"]
    long_status = _yaml_contract([], status="BOGUS_" + "Z" * 300)
    got2 = ctu.classify_contract(long_status, "learn-distiller")
    assert got2["contract_valid"] is False
    assert len(got2["contract_errors"]) == 1 and len(got2["contract_errors"][0]) == 120, got2


def test_classify_envelope_presence_only():
    got = ctu.classify_contract('Findings...\nCONTRACT {"s":"PASS","b":false}\n', "code-reviewer")
    assert got == {"contract_shape": "envelope", "contract_valid": None,
                   "contract_errors": []}, got


def test_classify_heading_other_learn_distiller():
    text = "## Router Contract\n```yaml\nSTATUS: COMPLETE\n```\n"
    got = ctu.classify_contract(text, "learn-distiller")
    assert got == {"contract_shape": "heading_other", "contract_valid": None,
                   "contract_errors": []}, got


def test_classify_none():
    got = ctu.classify_contract("just prose, no contract here", "component-builder")
    assert got == {"contract_shape": "none", "contract_valid": None,
                   "contract_errors": []}, got


def test_classify_both_prefers_yaml_block():
    text = _yaml_contract(_builder_fields()) + '\nSee CONTRACT {"x":1} in prose.\n'
    got = ctu.classify_contract(text, "component-builder")
    assert got["contract_shape"] == "yaml_block" and got["contract_valid"] is True, got


def test_classify_validator_unavailable_fail_open():
    original = ctu._load_validator

    def boom():
        raise ImportError("no validator")

    ctu._load_validator = boom
    try:
        got = ctu.classify_contract(_yaml_contract(_builder_fields()), "component-builder")
        env = ctu.classify_contract('CONTRACT {"s":1}', "code-reviewer")
    finally:
        ctu._load_validator = original
    assert got == {"contract_shape": "yaml_block", "contract_valid": None,
                   "contract_errors": ["validator_unavailable"]}, got
    assert env["contract_shape"] == "envelope" and env["contract_valid"] is None


def test_classify_unknown_slug_status_only():
    text = _yaml_contract([], status="COMPLETE")
    for slug in ("learn-distiller", ""):
        got = ctu.classify_contract(text, slug)
        assert got["contract_shape"] == "yaml_block" and got["contract_valid"] is True, (slug, got)
    for bad in (None, 5, b"bytes", ["x"]):
        got = ctu.classify_contract(bad, "component-builder")
        assert got == {"contract_shape": "none", "contract_valid": None,
                       "contract_errors": []}, (bad, got)


def _text_blocks(*texts):
    return [{"type": "text", "text": t} for t in texts]


def test_final_text_capture():
    lines = [
        _assistant("m1", SONNET, 5, content=_text_blocks("early words")),
        _assistant("m2", SONNET, 10, content=_text_blocks("first chunk")),
        _assistant("m2", SONNET, 20, content=[{"type": "tool_use", "id": "t", "name": "x", "input": {}}]),
        _assistant("m2", SONNET, 30, content=_text_blocks("second chunk")),
    ]
    r = summarize_lines(lines, want_final_text=True)
    assert r["final_text"] == "first chunk\nsecond chunk", repr(r["final_text"])
    # last id without any text block does not displace the last id that has text
    lines.append(_assistant("m3", SONNET, 1, content=[{"type": "tool_use", "id": "t2", "name": "x", "input": {}}]))
    assert summarize_lines(lines, want_final_text=True)["final_text"] == "first chunk\nsecond chunk"
    # only tool_use blocks -> empty string
    only_tools = [_assistant("m1", SONNET, 1, content=[{"type": "tool_use", "id": "t", "name": "x", "input": {}}])]
    assert summarize_lines(only_tools, want_final_text=True)["final_text"] == ""
    # hook path never carries final_text
    assert "final_text" not in summarize_lines(lines)
    assert "final_text" not in summarize_lines(lines, want_final_text=False)


def test_final_text_cap_and_duplicate_block():
    big = "x" * 250000
    r = summarize_lines([_assistant("m", SONNET, 1, content=_text_blocks(big))], want_final_text=True)
    assert len(r["final_text"]) == 200000, len(r["final_text"])
    dup = _assistant("m", SONNET, 1, content=_text_blocks("same"))
    assert summarize_lines([dup, dup], want_final_text=True)["final_text"] == "same"


def test_final_text_via_transcript_matches_classify():
    text = _yaml_contract(_builder_fields())
    path = _tmp_jsonl("\n".join([_user("go"),
                                 _assistant("m1", SONNET, 9, content=_text_blocks(text))]) + "\n")
    r = ctu.summarize_transcript(path, want_final_text=True)
    assert r["final_text"] == text
    assert ctu.classify_contract(r["final_text"], "component-builder")["contract_valid"] is True


def test_non_string_model_and_id_are_tolerated():
    def rec(mid, model):
        return json.dumps({"type": "assistant", "message": {
            "id": mid, "model": model, "usage": {"output_tokens": 3}}})
    r = summarize_lines([rec("a", ["x"]), rec({"k": 1}, {"m": 1}), rec("b", 7)])
    assert list(r["models"]) == ["unknown"], r["models"]
    assert r["models"]["unknown"]["output_tokens"] == 9
    assert r["unkeyed_messages"] == 1


# ---------------------------------------------------------------------------
# last_turn_context_tokens (SPEC-0016 DD-4)
# ---------------------------------------------------------------------------

_NONE_RESULT = {"tokens": None, "model": None, "source": None}


def _boundary(post=None, pre=966834, with_meta=True):
    row = {"type": "system", "subtype": "compact_boundary"}
    if with_meta:
        meta = {"preTokens": pre}
        if post is not None:
            meta["postTokens"] = post
        row["compactMetadata"] = meta
    return json.dumps(row)


def test_last_turn_tokens_sums_input_cache_read_cache_creation():
    r = ctu.last_turn_context_tokens([_assistant("m1", "claude-x", 5, inp=10, cr=1000, cc=220)])
    assert r == {"tokens": 1230, "model": "claude-x", "source": "assistant"}, r


def test_last_turn_tokens_takes_last_assistant():
    r = ctu.last_turn_context_tokens([_assistant("m1", "a", 1, inp=100), _assistant("m2", "b", 1, inp=5000)])
    assert r["tokens"] == 5000 and r["model"] == "b", r


def test_last_turn_tokens_skips_synthetic_zero_usage():
    r = ctu.last_turn_context_tokens([
        _assistant("m1", "real", 1, inp=5000),
        _assistant("m2", "<synthetic>", 0, inp=0),
    ])
    assert r == {"tokens": 5000, "model": "real", "source": "assistant"}, r


def test_last_turn_tokens_skips_sidechain():
    side = json.loads(_assistant("m2", "side", 1, inp=90000))
    side["isSidechain"] = True
    r = ctu.last_turn_context_tokens([_assistant("m1", "main", 1, inp=5000), json.dumps(side)])
    assert r["tokens"] == 5000 and r["model"] == "main", r


def test_last_turn_tokens_none_when_no_usage():
    r = ctu.last_turn_context_tokens([_user("hi"), _user("again")])
    assert r == _NONE_RESULT, r


def test_last_turn_tokens_corrupt_and_non_dict_lines_skipped():
    r = ctu.last_turn_context_tokens(["{bad", "[1]", "", _assistant("m1", "m", 1, inp=700)])
    assert r["tokens"] == 700, r


def test_last_turn_tokens_invalid_values_coerced_to_zero():
    r = ctu.last_turn_context_tokens([_assistant("m1", "m", 1, inp=True, cr=-5, cc=300)])
    assert r["tokens"] == 300, r


def test_last_turn_tokens_never_raises():
    for bad in (None, [None, 3, b"x"]):
        r = ctu.last_turn_context_tokens(bad)
        assert isinstance(r, dict) and "tokens" in r, r

    def gen():
        yield _assistant("m1", "m", 1, inp=42)
        raise RuntimeError("boom")

    r = ctu.last_turn_context_tokens(gen())
    assert isinstance(r, dict) and "tokens" in r, r


def test_last_turn_tokens_boundary_after_assistant_uses_post_tokens():
    r = ctu.last_turn_context_tokens([_assistant("m1", "m", 1, inp=300705), _boundary(post=21775)])
    assert r["tokens"] == 21775 and r["source"] == "compact_boundary", r


def test_last_turn_tokens_boundary_without_post_tokens_is_none():
    r = ctu.last_turn_context_tokens([_assistant("m1", "m", 1, inp=5000), _boundary(with_meta=False)])
    assert r["tokens"] is None and r["source"] == "compact_boundary", r


def test_last_turn_tokens_assistant_after_boundary_wins():
    r = ctu.last_turn_context_tokens([_boundary(post=21775), _assistant("m1", "m", 1, inp=83661)])
    assert r["tokens"] == 83661 and r["source"] == "assistant", r


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    print("test_craftflow_transcript_usage: running")
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
