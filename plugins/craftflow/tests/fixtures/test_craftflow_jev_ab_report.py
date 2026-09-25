#!/usr/bin/env python3
"""Tests for craftflow_jev_ab_report.py.

Run: python3 tests/fixtures/test_craftflow_jev_ab_report.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
AB_REPORT_SCRIPT = SCRIPTS / "craftflow_jev_ab_report.py"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_ab_report import _NO_GROUND_TRUTH, aggregate_ab, build_arg_parser  # noqa: E402

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


def test_manifest_by_call_id_is_reused_from_shared_lib_not_duplicated() -> None:
    """Guards Durable Decision D1: craftflow_jev_ab_report.py must import
    _manifest_by_call_id from the new craftflow_jev_report_lib module, not
    define its own local copy -- proves genuine reuse, not accidental
    re-duplication after the extraction."""
    import craftflow_jev_ab_report
    import craftflow_jev_report_lib

    if craftflow_jev_ab_report._manifest_by_call_id is craftflow_jev_report_lib._manifest_by_call_id:
        ok("craftflow_jev_ab_report._manifest_by_call_id is the same object as the shared lib's (genuine reuse)")
    else:
        fail("manifest-by-call-id-reuse", "craftflow_jev_ab_report._manifest_by_call_id is a distinct object -- reuse broken")


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


def test_cli_nonexistent_manifest_path_errors_loudly() -> None:
    """A typo'd or wrong --manifest path must not be silently treated as an
    empty (zero-row) manifest -- that fabricates a full-looking report for a
    file that was never read. Distinct from test_cli_manifest_flag_is_required,
    which only tests the flag being omitted entirely."""
    with tempfile.TemporaryDirectory() as tmp:
        events_path, _manifest_path = _write_fixture(tmp)
        missing_manifest = Path(tmp) / "does-not-exist.jsonl"
        proc = _run_cli(["--events", str(events_path), "--manifest", str(missing_manifest)], cwd=tmp)
        if (
            proc.returncode != 0
            and proc.stdout == ""
            and str(missing_manifest) in proc.stderr
        ):
            ok("CLI exits non-zero with a stderr message naming the path when --manifest does not exist")
        else:
            fail("cli-manifest-missing-path", f"code={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}")


def test_cli_nonexistent_events_path_errors_loudly() -> None:
    """Same silent-fabrication risk when --events is explicitly given and
    points at a missing file."""
    with tempfile.TemporaryDirectory() as tmp:
        _events_path, manifest_path = _write_fixture(tmp)
        missing_events = Path(tmp) / "does-not-exist-events.jsonl"
        proc = _run_cli(["--events", str(missing_events), "--manifest", str(manifest_path)], cwd=tmp)
        if (
            proc.returncode != 0
            and proc.stdout == ""
            and str(missing_events) in proc.stderr
        ):
            ok("CLI exits non-zero with a stderr message naming the path when explicit --events does not exist")
        else:
            fail("cli-events-missing-path", f"code={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}")


def test_cli_default_events_path_missing_is_self_documenting() -> None:
    """The default --events path (state_root()/jev/events.jsonl) may
    legitimately not exist on a fresh install, so it is not a hard failure
    -- but the resulting report must say so explicitly (events_file_found in
    JSON, a WARNING line in text) rather than looking identical to a genuine
    zero-row report, which was the exact ambiguity re-hunt found for this
    flag after the --manifest fix closed it for that flag."""
    with tempfile.TemporaryDirectory() as tmp:
        _events_path, manifest_path = _write_fixture(tmp)
        env = dict(os.environ)
        env.pop("CLAUDE_PROJECT_DIR", None)
        proc_json = subprocess.run(
            [sys.executable, str(AB_REPORT_SCRIPT), "--manifest", str(manifest_path), "--json"],
            cwd=str(tmp),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
        )
        try:
            payload = json.loads(proc_json.stdout)
        except json.JSONDecodeError as exc:
            fail("cli-default-events-missing-json", f"invalid JSON: {exc}; stdout={proc_json.stdout!r} err={proc_json.stderr!r}")
            return
        if proc_json.returncode == 0 and payload.get("events_file_found") is False:
            ok("JSON output sets events_file_found=false when the default events path is missing")
        else:
            fail(
                "cli-default-events-missing-json",
                f"code={proc_json.returncode} events_file_found={payload.get('events_file_found')!r}",
            )

        proc_text = subprocess.run(
            [sys.executable, str(AB_REPORT_SCRIPT), "--manifest", str(manifest_path)],
            cwd=str(tmp),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc_text.returncode == 0 and "WARNING" in proc_text.stdout and "events file does not exist" in proc_text.stdout:
            ok("Text output prints a WARNING line when the default events path is missing")
        else:
            fail("cli-default-events-missing-text", f"code={proc_text.returncode} stdout={proc_text.stdout!r}")


# ---------------------------------------------------------------------------
# aggregate_ab() -- routing 0/0 accuracy disambiguation (CRITICAL 2)
# ---------------------------------------------------------------------------


def test_aggregate_ab_routing_zero_rows_reports_sentinel_not_zero() -> None:
    """0 routing rows must not render as 0.0 (indistinguishable from '100%
    wrong')."""
    events_rows = [
        _skill_row(call_id="s1", agree=True, heuristic_result="craftflow:frontend-patterns",
                   answers={"choice": "craftflow:frontend-patterns", "confidence": 0.8}),
    ]
    manifest_rows = [{"call_id": "s1", "source_workflow_uuid": "wf1", "workflow_type": "BUILD"}]
    summary = aggregate_ab(events_rows, manifest_rows)
    routing = summary["features"]["routing"]
    checks = (
        routing["n"] == 0,
        routing["jev_accuracy"] == _NO_GROUND_TRUTH,
        routing["heuristic_accuracy"] == _NO_GROUND_TRUTH,
        routing["jev_accuracy"] != 0.0,
        routing["n_null_ground_truth"] == 0,
        routing["n_ground_truth"] == 0,
    )
    if all(checks):
        ok("aggregate_ab() reports sentinel (not 0.0) for routing accuracy when there are 0 routing rows")
    else:
        fail("aggregate-ab-routing-zero-rows", f"routing={routing!r} checks={checks!r}")


def test_aggregate_ab_routing_all_unmatched_reports_sentinel_not_zero() -> None:
    """Routing rows exist but none match a manifest call_id -- n_ground_truth
    stays 0, so accuracy must be the sentinel, not 0.0."""
    events_rows = [
        _routing_row(call_id="orphan1", answers={"choice": "DEBUG", "confidence": 0.9},
                     heuristic_result={"workflow": "DEBUG", "risk_signals": []}, agree=True),
        _routing_row(call_id="orphan2", answers={"choice": "BUILD", "confidence": 0.9},
                     heuristic_result={"workflow": "BUILD", "risk_signals": []}, agree=True),
    ]
    manifest_rows: list = []
    summary = aggregate_ab(events_rows, manifest_rows)
    routing = summary["features"]["routing"]
    checks = (
        routing["n"] == 2,
        routing["n_unmatched"] == 2,
        routing["n_ground_truth"] == 0,
        routing["n_null_ground_truth"] == 0,
        routing["jev_accuracy"] == _NO_GROUND_TRUTH,
        routing["heuristic_accuracy"] == _NO_GROUND_TRUTH,
        routing["n"] == routing["n_unmatched"] + routing["n_ground_truth"] + routing["n_null_ground_truth"],
    )
    if all(checks):
        ok("aggregate_ab() reports sentinel (not 0.0) when all routing rows are unmatched to the manifest")
    else:
        fail("aggregate-ab-routing-all-unmatched", f"routing={routing!r} checks={checks!r}")


def test_aggregate_ab_routing_null_ground_truth_reports_sentinel_and_is_counted() -> None:
    """Rows matched to a manifest entry whose workflow_type is null must not
    silently vanish -- they must be counted in n_null_ground_truth (not
    n_ground_truth, not n_unmatched) and accuracy must be the sentinel."""
    events_rows = [
        _routing_row(call_id="c1", answers={"choice": "DEBUG", "confidence": 0.9},
                     heuristic_result={"workflow": "DEBUG", "risk_signals": []}, agree=True),
        _routing_row(call_id="c2", answers={"choice": "BUILD", "confidence": 0.9},
                     heuristic_result={"workflow": "BUILD", "risk_signals": []}, agree=True),
    ]
    manifest_rows = [
        {"call_id": "c1", "source_workflow_uuid": "wf1", "workflow_type": None},
        {"call_id": "c2", "source_workflow_uuid": "wf2", "workflow_type": None},
    ]
    summary = aggregate_ab(events_rows, manifest_rows)
    routing = summary["features"]["routing"]
    checks = (
        routing["n"] == 2,
        routing["n_unmatched"] == 0,
        routing["n_ground_truth"] == 0,
        routing["n_null_ground_truth"] == 2,
        routing["jev_accuracy"] == _NO_GROUND_TRUTH,
        routing["heuristic_accuracy"] == _NO_GROUND_TRUTH,
        routing["n"] == routing["n_unmatched"] + routing["n_ground_truth"] + routing["n_null_ground_truth"],
    )
    if all(checks):
        ok("aggregate_ab() counts matched-but-null-ground-truth rows separately and reports sentinel accuracy")
    else:
        fail("aggregate-ab-routing-null-ground-truth", f"routing={routing!r} checks={checks!r}")


# ---------------------------------------------------------------------------
# _added_latency_ms() -- invalid_latency counter (HIGH)
# ---------------------------------------------------------------------------


def test_aggregate_ab_added_latency_tracks_invalid_latency_and_excludes_from_mean() -> None:
    """NaN and negative latency_ms values must be counted in invalid_latency
    and excluded from mean/p95 -- not silently dropped (which would make
    'zero added latency' and 'no valid latency data' look identical)."""
    events_rows = [
        _routing_row(call_id="c1", latency_ms=100, answers={"choice": "DEBUG", "confidence": 0.9},
                     heuristic_result={"workflow": "DEBUG", "risk_signals": []}, agree=True),
        _routing_row(call_id="c2", latency_ms=float("nan"), answers={"choice": "DEBUG", "confidence": 0.9},
                     heuristic_result={"workflow": "DEBUG", "risk_signals": []}, agree=True),
        _routing_row(call_id="c3", latency_ms=-5, answers={"choice": "DEBUG", "confidence": 0.9},
                     heuristic_result={"workflow": "DEBUG", "risk_signals": []}, agree=True),
        _routing_row(call_id="c4", latency_ms=300, answers={"choice": "DEBUG", "confidence": 0.9},
                     heuristic_result={"workflow": "DEBUG", "risk_signals": []}, agree=True),
    ]
    manifest_rows = [
        {"call_id": "c1", "source_workflow_uuid": "wf1", "workflow_type": "DEBUG"},
        {"call_id": "c2", "source_workflow_uuid": "wf2", "workflow_type": "DEBUG"},
        {"call_id": "c3", "source_workflow_uuid": "wf3", "workflow_type": "DEBUG"},
        {"call_id": "c4", "source_workflow_uuid": "wf4", "workflow_type": "DEBUG"},
    ]
    summary = aggregate_ab(events_rows, manifest_rows)
    latency = summary["features"]["routing"]["jev_added_latency_ms"]
    checks = (
        latency["invalid_latency"] == 2,
        latency["mean"] == 200.0,
        abs(latency["p95"] - 290.0) < 1e-9,
    )
    if all(checks):
        ok("aggregate_ab() jev_added_latency_ms tracks invalid_latency and computes mean/p95 over valid subset only")
    else:
        fail("aggregate-ab-invalid-latency", f"latency={latency!r} checks={checks!r}")


def main() -> int:
    print("test_craftflow_jev_ab_report: running")
    test_manifest_by_call_id_is_reused_from_shared_lib_not_duplicated()
    test_aggregate_ab_computes_routing_accuracy_and_agreement()
    test_aggregate_ab_handles_missing_manifest_row()
    test_aggregate_ab_skill_feature_reports_accuracy_sentinel_not_omitted()
    test_cli_json_flag_outputs_routing_accuracy_and_skill_sentinel()
    test_cli_text_mode_does_not_crash_and_prints_both_features()
    test_cli_manifest_flag_is_required()
    test_cli_nonexistent_manifest_path_errors_loudly()
    test_cli_nonexistent_events_path_errors_loudly()
    test_cli_default_events_path_missing_is_self_documenting()
    test_aggregate_ab_routing_zero_rows_reports_sentinel_not_zero()
    test_aggregate_ab_routing_all_unmatched_reports_sentinel_not_zero()
    test_aggregate_ab_routing_null_ground_truth_reports_sentinel_and_is_counted()
    test_aggregate_ab_added_latency_tracks_invalid_latency_and_excludes_from_mean()

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
