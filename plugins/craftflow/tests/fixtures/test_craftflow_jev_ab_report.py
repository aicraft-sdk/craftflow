#!/usr/bin/env python3
"""Tests for craftflow_jev_ab_report.py.

Run: python3 tests/fixtures/test_craftflow_jev_ab_report.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
AB_REPORT_SCRIPT = SCRIPTS / "craftflow_jev_ab_report.py"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_ab_report import aggregate_ab, build_arg_parser  # noqa: E402

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def _routing_row(**overrides):
    row = {
        "ts": "t0",
        "call_id": "c0",
        "session_id": "s0",
        "feature": "routing",
        "mode": {"routingHint": "audit", "skillHint": "audit"},
        "model": "jev-latest",
        "latency_ms": 100,
        "cache_hit": False,
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "answers": {"choice": "DEBUG", "confidence": 0.9},
        "confidence": 0.9,
        "heuristic_result": {"workflow": "DEBUG", "risk_signals": []},
        "agree": True,
        "agree_risk": True,
        "injected": False,
        "prompt_chars": 20,
        "prompt_truncated": False,
        "roster_size": 17,
    }
    row.update(overrides)
    return row


def _skill_row(**overrides):
    row = {
        "ts": "t0",
        "call_id": "c0",
        "session_id": "s0",
        "feature": "skill",
        "mode": {"routingHint": "audit", "skillHint": "audit"},
        "model": "jev-latest",
        "latency_ms": 100,
        "cache_hit": False,
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "answers": {"choice": "none", "confidence": 0.9},
        "confidence": 0.9,
        "heuristic_result": "none",
        "agree": True,
        "injected": False,
        "prompt_chars": 20,
        "prompt_truncated": False,
        "roster_size": 17,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# aggregate_ab() -- routing accuracy/agreement (Task 3.1/3.2)
# ---------------------------------------------------------------------------


def test_aggregate_ab_computes_routing_accuracy_and_agreement() -> None:
    """3 routing rows, matching manifest rows: 2/3 have workflow_type matching
    Jev's answers.choice; the remaining 1/3 matches the heuristic instead."""
    events_rows = [
        _routing_row(
            call_id="c1", latency_ms=100, usage={"input_tokens": 10, "output_tokens": 2},
            answers={"choice": "DEBUG", "confidence": 0.9},
            heuristic_result={"workflow": "BUILD", "risk_signals": []},
            agree=False,
        ),
        _routing_row(
            call_id="c2", latency_ms=200, usage={"input_tokens": 10, "output_tokens": 2},
            answers={"choice": "PLAN", "confidence": 0.9},
            heuristic_result={"workflow": "DEBUG", "risk_signals": []},
            agree=False,
        ),
        _routing_row(
            call_id="c3", latency_ms=300, usage={"input_tokens": 10, "output_tokens": 2},
            answers={"choice": "BUILD", "confidence": 0.9},
            heuristic_result={"workflow": "REVIEW", "risk_signals": []},
            agree=False,
        ),
    ]
    manifest_rows = [
        {"call_id": "c1", "source_workflow_uuid": "wf1", "workflow_type": "DEBUG"},
        {"call_id": "c2", "source_workflow_uuid": "wf2", "workflow_type": "PLAN"},
        {"call_id": "c3", "source_workflow_uuid": "wf3", "workflow_type": "REVIEW"},
    ]
    summary = aggregate_ab(events_rows, manifest_rows)
    routing = summary["features"]["routing"]
    checks = (
        routing["n"] == 3,
        abs(routing["jev_accuracy"] - (2 / 3)) < 1e-9,
        abs(routing["heuristic_accuracy"] - (1 / 3)) < 1e-9,
        routing["agreement"] == 0.0,
        routing["jev_added_latency_ms"]["mean"] == 200.0,
        abs(routing["jev_added_latency_ms"]["p95"] - 290.0) < 1e-9,
        routing["jev_added_tokens"] == 36,
        routing["heuristic_added_latency_ms"] == 0,
        routing["heuristic_added_tokens"] == 0,
    )
    if all(checks):
        ok("aggregate_ab() computes routing jev_accuracy=2/3, heuristic_accuracy=1/3, agreement, and cost fields")
    else:
        fail("aggregate-ab-routing-accuracy", f"routing={routing!r} checks={checks!r}")


def test_aggregate_ab_handles_missing_manifest_row() -> None:
    """An events row whose call_id has no matching manifest row is excluded
    from accuracy, counted in n_unmatched, and does not crash aggregate_ab()."""
    events_rows = [
        _routing_row(
            call_id="matched", latency_ms=100, usage={"input_tokens": 10, "output_tokens": 2},
            answers={"choice": "DEBUG", "confidence": 0.9},
            heuristic_result={"workflow": "DEBUG", "risk_signals": []},
            agree=True,
        ),
        _routing_row(
            call_id="orphan", latency_ms=150, usage={"input_tokens": 5, "output_tokens": 1},
            answers={"choice": "PLAN", "confidence": 0.9},
            heuristic_result={"workflow": "BUILD", "risk_signals": []},
            agree=False,
        ),
    ]
    manifest_rows = [
        {"call_id": "matched", "source_workflow_uuid": "wf1", "workflow_type": "DEBUG"},
    ]
    try:
        summary = aggregate_ab(events_rows, manifest_rows)
    except Exception as exc:  # noqa: BLE001 -- proving no crash is the point of this test
        fail("aggregate-ab-missing-manifest-row", f"aggregate_ab() raised instead of excluding: {exc!r}")
        return
    routing = summary["features"]["routing"]
    checks = (
        routing["n"] == 2,
        routing["n_unmatched"] == 1,
        routing["jev_accuracy"] == 1.0,  # only the matched row counts toward the denominator
        routing["heuristic_accuracy"] == 1.0,
    )
    if all(checks):
        ok("aggregate_ab() excludes unmatched-manifest rows from accuracy, counts n_unmatched, no crash")
    else:
        fail("aggregate-ab-missing-manifest-row", f"routing={routing!r} checks={checks!r}")


def test_aggregate_ab_skill_feature_reports_accuracy_sentinel_not_omitted() -> None:
    """skill has no ground truth for which skill "should" have been chosen --
    it reports agreement only, with an explicit sentinel string under the
    "accuracy" key rather than silently omitting the key."""
    events_rows = [
        _skill_row(call_id="s1", agree=True, heuristic_result="craftflow:frontend-patterns",
                   answers={"choice": "craftflow:frontend-patterns", "confidence": 0.8}),
        _skill_row(call_id="s2", agree=False, heuristic_result="none",
                   answers={"choice": "craftflow:architecture-patterns", "confidence": 0.75}),
    ]
    manifest_rows = [
        {"call_id": "s1", "source_workflow_uuid": "wf1", "workflow_type": "BUILD"},
        {"call_id": "s2", "source_workflow_uuid": "wf2", "workflow_type": "BUILD"},
    ]
    summary = aggregate_ab(events_rows, manifest_rows)
    skill = summary["features"]["skill"]
    checks = (
        skill["n"] == 2,
        skill["agreement"] == 0.5,
        "jev_accuracy" not in skill,
        "heuristic_accuracy" not in skill,
        skill.get("accuracy") == "not available (no ground truth)",
    )
    if all(checks):
        ok("aggregate_ab() skill feature reports agreement only, with explicit accuracy sentinel string")
    else:
        fail("aggregate-ab-skill-sentinel", f"skill={skill!r} checks={checks!r}")


# ---------------------------------------------------------------------------
# CLI (subprocess, real script) -- Task 3.4
# ---------------------------------------------------------------------------


def _run_cli(args, cwd) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(AB_REPORT_SCRIPT), *args],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _write_fixture(tmp: str):
    events_rows = [
        _routing_row(call_id="c1", latency_ms=100, usage={"input_tokens": 10, "output_tokens": 2},
                     answers={"choice": "DEBUG", "confidence": 0.9},
                     heuristic_result={"workflow": "BUILD", "risk_signals": []}, agree=False),
        _routing_row(call_id="c2", latency_ms=200, usage={"input_tokens": 10, "output_tokens": 2},
                     answers={"choice": "PLAN", "confidence": 0.9},
                     heuristic_result={"workflow": "PLAN", "risk_signals": []}, agree=True),
        _skill_row(call_id="s1", agree=True, heuristic_result="craftflow:frontend-patterns",
                   answers={"choice": "craftflow:frontend-patterns", "confidence": 0.8}),
        _skill_row(call_id="s2", agree=False, heuristic_result="none",
                   answers={"choice": "craftflow:architecture-patterns", "confidence": 0.75}),
    ]
    manifest_rows = [
        {"call_id": "c1", "source_workflow_uuid": "wf1", "workflow_type": "DEBUG"},
        {"call_id": "c2", "source_workflow_uuid": "wf2", "workflow_type": "PLAN"},
    ]
    events_path = Path(tmp) / "events.jsonl"
    manifest_path = Path(tmp) / "replay_manifest.jsonl"
    events_path.write_text("\n".join(json.dumps(r) for r in events_rows) + "\n")
    manifest_path.write_text("\n".join(json.dumps(r) for r in manifest_rows) + "\n")
    return events_path, manifest_path


def test_cli_json_flag_outputs_routing_accuracy_and_skill_sentinel() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        events_path, manifest_path = _write_fixture(tmp)
        proc = _run_cli(["--events", str(events_path), "--manifest", str(manifest_path), "--json"], cwd=tmp)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            fail("cli-json-flag", f"invalid JSON: {exc}; stdout={proc.stdout!r} err={proc.stderr!r}")
            return
        routing = payload.get("features", {}).get("routing", {})
        skill = payload.get("features", {}).get("skill", {})
        if (
            proc.returncode == 0
            and routing.get("n") == 2
            and abs(routing.get("jev_accuracy", -1) - 1.0) < 1e-9
            and abs(routing.get("heuristic_accuracy", -1) - 0.5) < 1e-9
            and routing.get("n_unmatched") == 0
            and skill.get("n") == 2
            and skill.get("accuracy") == "not available (no ground truth)"
            and "jev_accuracy" not in skill
        ):
            ok("CLI --json prints routing accuracy + skill accuracy sentinel from real events/manifest files")
        else:
            fail("cli-json-flag", f"payload={payload!r} err={proc.stderr!r}")


def test_cli_text_mode_does_not_crash_and_prints_both_features() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        events_path, manifest_path = _write_fixture(tmp)
        proc = _run_cli(["--events", str(events_path), "--manifest", str(manifest_path)], cwd=tmp)
        if (
            proc.returncode == 0
            and "routing:" in proc.stdout
            and "skill:" in proc.stdout
            and "accuracy: not available (no ground truth)" in proc.stdout
            and "Traceback" not in proc.stderr
        ):
            ok("CLI text mode exits 0 and prints both features with skill accuracy sentinel")
        else:
            fail("cli-text-mode", f"code={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}")


def test_cli_manifest_flag_is_required() -> None:
    args = build_arg_parser()
    try:
        args.parse_args(["--events", "/tmp/does-not-matter.jsonl"])
    except SystemExit:
        ok("--manifest is a required CLI flag")
    else:
        fail("cli-manifest-required", "parse_args() did not exit when --manifest was omitted")


def main() -> int:
    print("test_craftflow_jev_ab_report: running")
    test_aggregate_ab_computes_routing_accuracy_and_agreement()
    test_aggregate_ab_handles_missing_manifest_row()
    test_aggregate_ab_skill_feature_reports_accuracy_sentinel_not_omitted()
    test_cli_json_flag_outputs_routing_accuracy_and_skill_sentinel()
    test_cli_text_mode_does_not_crash_and_prints_both_features()
    test_cli_manifest_flag_is_required()

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
    raise SystemExit(main())
