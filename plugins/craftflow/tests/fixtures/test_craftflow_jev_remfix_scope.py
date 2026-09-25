#!/usr/bin/env python3
"""Tests for craftflow_jev_remfix_scope.py.

Run: python3 tests/fixtures/test_craftflow_jev_remfix_scope.py
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_remfix_scope import (  # noqa: E402
    build_state,
    build_questions,
    decide,
    telemetry_row,
    _append_event,
    main,
    build_arg_parser,
)

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def test_build_state_caps_combined_text_and_counts() -> None:
    state = build_state(["c1", "c2"], ["h1"], max_chars=10)
    if state["critical_count"] == 2 and state["high_count"] == 1 and len(state["critical_issues"]) <= 5 and len(state["high_issues"]) <= 5:
        ok("build_state caps text and preserves counts")
    else:
        fail("build-state-caps", f"state={state!r}")


def test_build_questions_shape() -> None:
    q = build_questions()
    if q["scope"]["type"] == "choice" and set(q["scope"]["criteria"]) == {"critical_only", "all_issues"}:
        ok("build_questions returns a single scope choice question")
    else:
        fail("build-questions-shape", f"q={q!r}")


def test_decide_audit_mode_always_logged_never_applied() -> None:
    d, c, conf = decide("audit", {"choice": "all_issues", "confidence": 0.99}, threshold=0.5)
    if d == "logged" and c == "all_issues" and conf == 0.99:
        ok("audit mode never auto-applies regardless of confidence")
    else:
        fail("decide-audit-never-applies", f"d={d!r} c={c!r} conf={conf!r}")


def test_decide_advise_applies_at_or_above_threshold() -> None:
    at = decide("advise", {"choice": "critical_only", "confidence": 0.85}, threshold=0.85)
    below = decide("advise", {"choice": "critical_only", "confidence": 0.849999}, threshold=0.85)
    if at[0] == "applied" and below[0] == "below_threshold":
        ok("advise mode applies at >= threshold, falls back just below it")
    else:
        fail("decide-advise-threshold-boundary", f"at={at!r} below={below!r}")


def test_decide_rejects_malformed_answer_as_no_decision() -> None:
    cases = [
        decide("advise", None, threshold=0.5),
        decide("advise", {}, threshold=0.5),
        decide("advise", {"choice": "maybe", "confidence": 0.9}, threshold=0.5),
        decide("advise", {"choice": "critical_only", "confidence": "high"}, threshold=0.5),
        decide("advise", {"choice": "critical_only", "confidence": True}, threshold=0.5),  # bool is an int subclass
        decide("advise", {"choice": "critical_only", "confidence": 1.5}, threshold=0.5),
        decide("advise", {"choice": "critical_only", "confidence": float("nan")}, threshold=0.5),
    ]
    if all(c[0] == "no_decision" and c[1] is None and c[2] is None for c in cases):
        ok("decide() rejects every malformed answer shape as no_decision")
    else:
        fail("decide-rejects-malformed", f"cases={cases!r}")


def test_telemetry_row_shape_and_never_carries_raw_finding_text() -> None:
    row = telemetry_row(
        decision="logged", choice="critical_only", confidence=0.9,
        result={"model": "jev-latest", "latency_ms": 120, "cache_hit": False, "usage": {"input_tokens": 5}},
        mode="audit", model="jev-latest", workflow_uuid="wf-test-1",
        critical_count=1, high_count=1,
    )
    checks = (
        row["feature"] == "remfix_scope",
        row["heuristic_result"] == "critical_only",
        row["agree"] is True,
        row["answers"] == {"choice": "critical_only", "confidence": 0.9},
        row["decision"] == "logged",
        row["workflow_uuid"] == "wf-test-1",
        "critical_issues" not in json.dumps(row),  # no raw finding text ever included
        "high_issues" not in json.dumps(row),
    )
    if all(checks):
        ok("telemetry_row has the right shape and never carries raw finding text")
    else:
        fail("telemetry-row-shape", f"row={row!r} checks={checks!r}")


def test_telemetry_row_no_decision_disagrees_with_heuristic() -> None:
    row = telemetry_row(
        decision="no_decision", choice=None, confidence=None, result=None,
        mode="audit", model="jev-latest", workflow_uuid=None, critical_count=1, high_count=1,
    )
    if row["agree"] is False and row["answers"] == {"choice": None, "confidence": None}:
        ok("no_decision rows never count as agreement")
    else:
        fail("telemetry-row-no-decision", f"row={row!r}")


def test_append_event_returns_true_on_success_and_false_on_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ok_path = Path(tmp) / "events.jsonl"
        succeeded = _append_event(ok_path, {"a": 1})
        # a path whose parent is actually a file makes mkdir fail
        blocked_parent = Path(tmp) / "not_a_dir"
        blocked_parent.write_text("x")
        failed = _append_event(blocked_parent / "events.jsonl", {"a": 1})
        # cf:shortcut: read must happen before the TemporaryDirectory context
        # exits (plan's original assertion read after teardown -> FileNotFoundError)
        if succeeded is True and ok_path.read_text().strip() and failed is False:
            ok("_append_event returns True on success, False on failure, never raises")
        else:
            fail("append-event-bool", f"succeeded={succeeded!r} failed={failed!r}")


def run_cli(argv: list, env: dict) -> tuple:
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict("os.environ", env, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def test_off_mode_makes_zero_calls_and_writes_zero_telemetry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "jev.json"
        config_path.write_text(json.dumps({"enabled": True, "features": {"remediationScope": "off"}}))
        events_path = Path(tmp) / "state" / "jev" / "events.jsonl"
        with mock.patch("craftflow_jev_remfix_scope.jev_call") as mocked:
            code, out, _err = run_cli(
                ["--critical", "c1", "--high", "h1", "--config", str(config_path), "--state-dir", str(Path(tmp) / "state")],
                {"TYPESAFE_API_KEY": "k"},
            )
        payload = json.loads(out.strip())
        if code == 0 and payload == {"decision": "off", "choice": None, "confidence": None} and not mocked.called and not events_path.exists():
            ok("off mode: zero jev_call invocations, zero telemetry, decision=off")
        else:
            fail("off-mode-zero-calls", f"code={code} out={out!r} mocked.called={mocked.called} events_exists={events_path.exists()}")


def test_missing_api_key_treated_as_off_even_when_mode_is_advise() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "jev.json"
        config_path.write_text(json.dumps({"enabled": True, "features": {"remediationScope": "advise"}}))
        with mock.patch("craftflow_jev_remfix_scope.jev_call") as mocked:
            code, out, _err = run_cli(
                ["--critical", "c1", "--high", "h1", "--config", str(config_path), "--state-dir", str(Path(tmp) / "state")],
                {},  # no TYPESAFE_API_KEY
            )
        payload = json.loads(out.strip())
        if payload["decision"] == "off" and not mocked.called:
            ok("missing API key falls open to off even when mode=advise")
        else:
            fail("missing-key-off", f"out={out!r} mocked.called={mocked.called}")


def test_advise_mode_applies_above_threshold_and_writes_one_row() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "jev.json"
        config_path.write_text(json.dumps({
            "enabled": True, "features": {"remediationScope": "advise"},
            "thresholds": {"remediationScope": 0.8},
        }))
        events_path = Path(tmp) / "state" / "jev" / "events.jsonl"
        fake_result = {"answers": {"scope": {"choice": "critical_only", "confidence": 0.95}}, "model": "jev-latest", "latency_ms": 50, "cache_hit": False, "usage": {}}
        with mock.patch("craftflow_jev_remfix_scope.jev_call", return_value=fake_result) as mocked:
            code, out, _err = run_cli(
                ["--critical", "c1", "--high", "h1", "--workflow-uuid", "wf-9", "--config", str(config_path), "--state-dir", str(events_path.parent.parent)],
                {"TYPESAFE_API_KEY": "k"},
            )
        payload = json.loads(out.strip())
        rows = [json.loads(l) for l in events_path.read_text().splitlines()] if events_path.exists() else []
        if code == 0 and payload == {"decision": "applied", "choice": "critical_only", "confidence": 0.95} and mocked.called and len(rows) == 1 and rows[0]["workflow_uuid"] == "wf-9":
            ok("advise mode applies above threshold, writes exactly 1 telemetry row")
        else:
            fail("advise-applies", f"payload={payload!r} rows={rows!r}")


def test_advise_mode_write_failure_forces_below_threshold_not_applied() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "jev.json"
        config_path.write_text(json.dumps({
            "enabled": True, "features": {"remediationScope": "advise"},
            "thresholds": {"remediationScope": 0.8},
        }))
        state_dir = Path(tmp) / "state"
        # make events.jsonl's parent unavailable: pre-create "jev" as a FILE, not a dir,
        # so _append_event's mkdir(parents=True, exist_ok=True) raises internally and
        # returns False.
        (state_dir).mkdir(parents=True)
        (state_dir / "jev").write_text("blocking file, not a directory")
        fake_result = {"answers": {"scope": {"choice": "critical_only", "confidence": 0.95}}, "model": "jev-latest", "latency_ms": 50, "cache_hit": False, "usage": {}}
        with mock.patch("craftflow_jev_remfix_scope.jev_call", return_value=fake_result):
            code, out, _err = run_cli(
                ["--critical", "c1", "--high", "h1", "--config", str(config_path), "--state-dir", str(state_dir)],
                {"TYPESAFE_API_KEY": "k"},
            )
        payload = json.loads(out.strip())
        if code == 0 and payload["decision"] == "below_threshold" and payload["choice"] == "critical_only":
            ok("advise mode: qualifying confidence but failed persist downgrades to below_threshold, never applied")
        else:
            fail("advise-persist-failure-forces-fallback", f"payload={payload!r}")


def test_jev_call_failure_falls_back_to_no_decision_but_still_logs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "jev.json"
        config_path.write_text(json.dumps({"enabled": True, "features": {"remediationScope": "audit"}}))
        events_path = Path(tmp) / "state" / "jev" / "events.jsonl"
        with mock.patch("craftflow_jev_remfix_scope.jev_call", return_value=None):
            code, out, _err = run_cli(
                ["--critical", "c1", "--config", str(config_path), "--state-dir", str(events_path.parent.parent)],
                {"TYPESAFE_API_KEY": "k"},
            )
        payload = json.loads(out.strip())
        rows = [json.loads(l) for l in events_path.read_text().splitlines()] if events_path.exists() else []
        if code == 0 and payload == {"decision": "no_decision", "choice": None, "confidence": None} and len(rows) == 1:
            ok("jev_call failure falls back to no_decision, still logs 1 row")
        else:
            fail("jev-call-failure", f"payload={payload!r} rows={rows!r}")


def test_no_critical_or_high_args_short_circuits_without_calling_jev() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "jev.json"
        config_path.write_text(json.dumps({"enabled": True, "features": {"remediationScope": "audit"}}))
        with mock.patch("craftflow_jev_remfix_scope.jev_call") as mocked:
            code, out, _err = run_cli(
                ["--config", str(config_path), "--state-dir", str(Path(tmp) / "state")],
                {"TYPESAFE_API_KEY": "k"},
            )
        payload = json.loads(out.strip())
        if payload["decision"] == "no_decision" and not mocked.called:
            ok("no --critical/--high args short-circuits before ever calling jev")
        else:
            fail("no-args-short-circuit", f"out={out!r} mocked.called={mocked.called}")


def test_main_never_raises_on_unexpected_exception() -> None:
    with mock.patch("craftflow_jev_remfix_scope.load_config", side_effect=RuntimeError("boom")):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--critical", "c1"])
        payload = json.loads(out.getvalue().strip())
    if code == 0 and payload["decision"] == "no_decision":
        ok("main() never raises -- unexpected exceptions fail open to no_decision")
    else:
        fail("main-never-raises", f"code={code} out={out.getvalue()!r}")


def main_tests() -> int:
    print("test_craftflow_jev_remfix_scope: running")
    test_build_state_caps_combined_text_and_counts()
    test_build_questions_shape()
    test_decide_audit_mode_always_logged_never_applied()
    test_decide_advise_applies_at_or_above_threshold()
    test_decide_rejects_malformed_answer_as_no_decision()

    test_telemetry_row_shape_and_never_carries_raw_finding_text()
    test_telemetry_row_no_decision_disagrees_with_heuristic()
    test_append_event_returns_true_on_success_and_false_on_failure()

    test_off_mode_makes_zero_calls_and_writes_zero_telemetry()
    test_missing_api_key_treated_as_off_even_when_mode_is_advise()
    test_advise_mode_applies_above_threshold_and_writes_one_row()
    test_advise_mode_write_failure_forces_below_threshold_not_applied()
    test_jev_call_failure_falls_back_to_no_decision_but_still_logs()
    test_no_critical_or_high_args_short_circuits_without_calling_jev()
    test_main_never_raises_on_unexpected_exception()

    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_tests())
