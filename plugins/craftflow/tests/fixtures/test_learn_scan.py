#!/usr/bin/env python3
"""
Fixture-based unit tests for craftflow_learn_scan.py

Run from the plugin root:
    python3 tests/fixtures/test_learn_scan.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# Allow importing scripts from scripts/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../scripts"))

from craftflow_learn_scan import (
    normalize_reason,
    is_failure_event,
    scan_state_dir,
)

PASS = 0
FAIL = 0


def check(name: str, actual, expected):
    global PASS, FAIL
    if actual == expected:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        print(f"    expected: {expected!r}")
        print(f"    actual:   {actual!r}")
        FAIL += 1


def check_true(name: str, condition: bool):
    check(name, condition, True)


def write_events(wf_dir: Path, filename: str, events: list):
    """Write a list of event dicts to a .events.jsonl file."""
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / filename
    with open(path, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")
    return path


# ---------------------------------------------------------------------------
# test_empty_state_dir
# ---------------------------------------------------------------------------
print("\n[test_empty_state_dir]")
with tempfile.TemporaryDirectory() as tmp:
    state_dir = Path(tmp) / ".craftflow" / "state"
    wf_dir = state_dir / "workflows"
    wf_dir.mkdir(parents=True)
    result = scan_state_dir(state_dir)
    check("empty state dir returns []", result, [])


# ---------------------------------------------------------------------------
# test_remediation_created_clustered
# ---------------------------------------------------------------------------
print("\n[test_remediation_created_clustered]")
with tempfile.TemporaryDirectory() as tmp:
    state_dir = Path(tmp) / ".craftflow" / "state"
    wf_dir = state_dir / "workflows"

    evt = {
        "event": "remediation_created",
        "reason": "test timed out",
        "timestamp": "2026-06-01T10:00:00Z",
    }
    write_events(wf_dir, "wf-aaa.events.jsonl", [evt])
    write_events(wf_dir, "wf-bbb.events.jsonl", [evt])

    result = scan_state_dir(state_dir)
    check("two files with same reason → occurrences=2", len(result), 1)
    check("cluster occurrences == 2", result[0]["occurrences"], 2)
    check(
        "signature is normalized reason",
        result[0]["signature"],
        normalize_reason("test timed out"),
    )


# ---------------------------------------------------------------------------
# test_single_occurrence_included
# ---------------------------------------------------------------------------
print("\n[test_single_occurrence_included]")
with tempfile.TemporaryDirectory() as tmp:
    state_dir = Path(tmp) / ".craftflow" / "state"
    wf_dir = state_dir / "workflows"

    evt = {
        "event": "remediation_created",
        "reason": "unique failure reason only once",
        "timestamp": "2026-06-15T12:00:00Z",
    }
    write_events(wf_dir, "wf-ccc.events.jsonl", [evt])

    result = scan_state_dir(state_dir)
    check("single occurrence is still in output", len(result), 1)
    check("occurrences == 1", result[0]["occurrences"], 1)


# ---------------------------------------------------------------------------
# test_workflow_failed_signal
# ---------------------------------------------------------------------------
print("\n[test_workflow_failed_signal]")
with tempfile.TemporaryDirectory() as tmp:
    state_dir = Path(tmp) / ".craftflow" / "state"
    wf_dir = state_dir / "workflows"

    evt = {
        "event": "workflow_failed",
        "reason": "phase gate rejected output",
        "timestamp": "2026-06-20T09:00:00Z",
    }
    write_events(wf_dir, "wf-ddd.events.jsonl", [evt])

    result = scan_state_dir(state_dir)
    check("workflow_failed event is captured", len(result), 1)
    check("event_types contains workflow_failed", "workflow_failed" in result[0]["event_types"], True)


# ---------------------------------------------------------------------------
# test_re_verify_decision
# ---------------------------------------------------------------------------
print("\n[test_re_verify_decision]")
with tempfile.TemporaryDirectory() as tmp:
    state_dir = Path(tmp) / ".craftflow" / "state"
    wf_dir = state_dir / "workflows"

    evt = {
        "event": "agent_completed",
        "decision": "re_verify",
        "reason": "verification failed again",
        "timestamp": "2026-06-25T14:00:00Z",
    }
    write_events(wf_dir, "wf-eee.events.jsonl", [evt])

    result = scan_state_dir(state_dir)
    check("re_verify decision event is captured", len(result), 1)
    check(
        "event_types contains agent_completed for re_verify",
        "agent_completed" in result[0]["event_types"],
        True,
    )


# ---------------------------------------------------------------------------
# test_malformed_line_skipped
# ---------------------------------------------------------------------------
print("\n[test_malformed_line_skipped]")
with tempfile.TemporaryDirectory() as tmp:
    state_dir = Path(tmp) / ".craftflow" / "state"
    wf_dir = state_dir / "workflows"

    good_evt = {
        "event": "workflow_failed",
        "reason": "valid event after malformed line",
        "timestamp": "2026-06-28T08:00:00Z",
    }
    path = wf_dir / "wf-fff.events.jsonl"
    wf_dir.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("not valid json{\n")
        f.write(json.dumps(good_evt) + "\n")

    result = scan_state_dir(state_dir)
    check("malformed line skipped, valid event captured", len(result), 1)
    check("signature comes from good event", "valid event after malformed line" in result[0]["example_reasons"][0], True)


# ---------------------------------------------------------------------------
# test_signature_normalization
# ---------------------------------------------------------------------------
print("\n[test_signature_normalization]")
with tempfile.TemporaryDirectory() as tmp:
    state_dir = Path(tmp) / ".craftflow" / "state"
    wf_dir = state_dir / "workflows"

    evt1 = {
        "event": "remediation_created",
        "reason": "  Test  Timed  Out!  ",
        "timestamp": "2026-07-01T10:00:00Z",
    }
    evt2 = {
        "event": "remediation_created",
        "reason": "test timed out",
        "timestamp": "2026-07-01T11:00:00Z",
    }
    write_events(wf_dir, "wf-ggg.events.jsonl", [evt1, evt2])

    result = scan_state_dir(state_dir)
    check("two reasons normalize to same signature → one cluster", len(result), 1)
    check("cluster has occurrences == 2", result[0]["occurrences"], 2)
    check("example_reasons has at most 3 entries", len(result[0]["example_reasons"]) <= 3, True)


# ---------------------------------------------------------------------------
# Also test is_failure_event directly
# ---------------------------------------------------------------------------
print("\n[is_failure_event]")
check_true("remediation_created is failure", is_failure_event({"event": "remediation_created"}))
check_true("workflow_failed is failure", is_failure_event({"event": "workflow_failed"}))
check_true("re_verify decision is failure", is_failure_event({"event": "x", "decision": "re_verify"}))
check_true("re_review decision is failure", is_failure_event({"event": "x", "decision": "re_review"}))
check_true("re_hunt decision is failure", is_failure_event({"event": "x", "decision": "re_hunt"}))
check("normal event is not failure", is_failure_event({"event": "agent_completed"}), False)
check("no decision match is not failure", is_failure_event({"event": "x", "decision": "complete"}), False)


# ---------------------------------------------------------------------------
# Also test normalize_reason directly
# ---------------------------------------------------------------------------
print("\n[normalize_reason]")
check("lowercases", normalize_reason("UPPER CASE"), "upper case")
check("collapses whitespace", normalize_reason("too  many   spaces"), "too many spaces")
check("strips leading punctuation", normalize_reason("!test reason!"), "test reason")
check("strips trailing punctuation", normalize_reason("test reason."), "test reason")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*40}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    print("FAIL")
    sys.exit(1)
else:
    print("PASS")
    sys.exit(0)
