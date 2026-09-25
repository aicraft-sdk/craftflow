#!/usr/bin/env python3
"""Tests for craftflow_jev_report.py.

Run: python3 tests/fixtures/test_craftflow_jev_report.py
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
REPORT_SCRIPT = SCRIPTS / "craftflow_jev_report.py"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_report import (
    FEATURES,
    _heuristic_choice,
    _jev_choice,
    _read_lines,
    _sanitize_json_value,
    aggregate,
    build_arg_parser,
    verdict,
)  # noqa: E402

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
# aggregate()
# ---------------------------------------------------------------------------


def test_aggregate_empty_rows_returns_zero_n_for_both_features() -> None:
    summary = aggregate([])
    routing = summary["features"]["routing"]
    skill = summary["features"]["skill"]
    if (
        summary["malformed"] == 0
        and routing["n"] == 0
        and routing["agreement"] == 0.0
        and routing["agree_risk"] == 0.0
        and routing["disagreements"] == []
        and skill["n"] == 0
        and "agree_risk" not in skill
    ):
        ok("aggregate([]) returns n=0 for both features, no malformed")
    else:
        fail("aggregate-empty", f"summary={summary!r}")


def test_aggregate_skips_and_counts_malformed_lines() -> None:
    lines = [
        "not json at all",
        json.dumps({"feature": "unknown-feature", "agree": True}),
        json.dumps(_routing_row()),
        "",  # blank lines are ignored, not malformed
        "   ",
    ]
    summary = aggregate(lines)
    if summary["malformed"] == 2 and summary["features"]["routing"]["n"] == 1:
        ok("aggregate() skips malformed/unrecognized-feature lines and counts them separately")
    else:
        fail("aggregate-malformed", f"summary={summary!r}")


def test_aggregate_agreement_latency_tokens_injected_cache_hits_and_disagreements() -> None:
    rows = [
        _routing_row(call_id="c1", ts="t1", agree=True, agree_risk=True, latency_ms=100,
                     usage={"input_tokens": 10, "output_tokens": 2}, injected=True, cache_hit=False),
        _routing_row(call_id="c2", ts="t2", agree=True, agree_risk=False, latency_ms=200,
                     usage={"input_tokens": 20, "output_tokens": 4}, injected=False, cache_hit=True),
        _routing_row(call_id="c3", ts="t3", agree=False, agree_risk=True, latency_ms=300,
                     usage={"input_tokens": 30, "output_tokens": 6}, injected=False, cache_hit=False,
                     answers={"choice": "BUILD", "confidence": 0.6},
                     heuristic_result={"workflow": "PLAN", "risk_signals": []}),
        _routing_row(call_id="c4", ts="t4", agree=False, agree_risk=False, latency_ms=400,
                     usage={"input_tokens": 40, "output_tokens": 8}, injected=True, cache_hit=True,
                     answers={"choice": "REVIEW", "confidence": 0.55},
                     heuristic_result={"workflow": "DEBUG", "risk_signals": []}),
    ]
    lines = [json.dumps(row) for row in rows]
    feat = aggregate(lines)["features"]["routing"]
    expected_disagreements = [
        {"call_id": "c3", "ts": "t3", "jev": "BUILD", "heuristic": "PLAN"},
        {"call_id": "c4", "ts": "t4", "jev": "REVIEW", "heuristic": "DEBUG"},
    ]
    checks = (
        feat["n"] == 4,
        feat["agreement"] == 0.5,
        feat["agree_risk"] == 0.5,
        feat["mean_latency_ms"] == 250.0,
        abs(feat["p95_latency_ms"] - 385.0) < 1e-9,
        feat["input_tokens"] == 100,
        feat["output_tokens"] == 20,
        feat["injected"] == 2,
        feat["cache_hits"] == 2,
        feat["disagreements"] == expected_disagreements,
    )
    if all(checks):
        ok("aggregate() computes agreement/agree_risk/latency/tokens/injected/cache_hits/disagreements")
    else:
        fail("aggregate-stats", f"feat={feat!r} checks={checks!r}")


def test_aggregate_skill_feature_never_has_agree_risk_key() -> None:
    rows = [
        _skill_row(call_id="s1", agree=True, heuristic_result="craftflow:frontend-patterns",
                   answers={"choice": "craftflow:frontend-patterns", "confidence": 0.8}),
        _skill_row(call_id="s2", agree=False, heuristic_result="none",
                   answers={"choice": "craftflow:architecture-patterns", "confidence": 0.75}),
    ]
    lines = [json.dumps(row) for row in rows]
    feat = aggregate(lines)["features"]["skill"]
    expected_disagreements = [
        {"call_id": "s2", "ts": "t0", "jev": "craftflow:architecture-patterns", "heuristic": "none"},
    ]
    if "agree_risk" not in feat and feat["n"] == 2 and feat["agreement"] == 0.5 and feat["disagreements"] == expected_disagreements:
        ok("aggregate() skill feature has no agree_risk key; heuristic_result is a bare string")
    else:
        fail("aggregate-skill-no-agree-risk", f"feat={feat!r}")


# ---------------------------------------------------------------------------
# Dual-gate accuracy (ground_truth_by_call_id) -- _summarize_feature() / aggregate()
# ---------------------------------------------------------------------------


def test_summarize_feature_routing_computes_accuracy_with_manifest() -> None:
    """3 routing rows, 2/3 Jev-correct, 1/3 heuristic-correct against a
    supplied ground-truth lookup -- mirrors craftflow_jev_ab_report.py's own
    test_aggregate_ab_computes_routing_accuracy_and_agreement fixture shape."""
    rows = [
        _routing_row(call_id="c1", answers={"choice": "DEBUG", "confidence": 0.9},
                     heuristic_result={"workflow": "BUILD", "risk_signals": []}, agree=False),
        _routing_row(call_id="c2", answers={"choice": "PLAN", "confidence": 0.9},
                     heuristic_result={"workflow": "DEBUG", "risk_signals": []}, agree=False),
        _routing_row(call_id="c3", answers={"choice": "BUILD", "confidence": 0.9},
                     heuristic_result={"workflow": "REVIEW", "risk_signals": []}, agree=False),
    ]
    ground_truth = {"c1": "DEBUG", "c2": "PLAN", "c3": "REVIEW"}
    lines = [json.dumps(row) for row in rows]
    feat = aggregate(lines, [{"call_id": k, "workflow_type": v} for k, v in ground_truth.items()])["features"]["routing"]
    checks = (
        feat["n"] == 3,
        feat["n_ground_truth"] == 3,
        abs(feat["jev_accuracy"] - (2 / 3)) < 1e-9,
        abs(feat["heuristic_accuracy"] - (1 / 3)) < 1e-9,
        abs(feat["accuracy_improvement"] - (1 / 3)) < 1e-9,
    )
    if all(checks):
        ok("aggregate()/_summarize_feature() compute routing jev_accuracy=2/3, heuristic_accuracy=1/3 with a manifest")
    else:
        fail("summarize-routing-accuracy", f"feat={feat!r} checks={checks!r}")


def test_summarize_feature_routing_no_manifest_omits_accuracy_keys() -> None:
    """No manifest at all (aggregate(lines) 1-arg call) -- byte-identical to
    pre-dual-gate output, no accuracy keys present at all."""
    rows = [_routing_row(call_id="c1")]
    lines = [json.dumps(r) for r in rows]
    feat = aggregate(lines)["features"]["routing"]
    keys = ("n_ground_truth", "jev_accuracy", "heuristic_accuracy", "accuracy_improvement")
    if all(k not in feat for k in keys):
        ok("aggregate(lines) with no manifest arg omits all 4 accuracy keys from routing summary")
    else:
        fail("summarize-routing-no-manifest", f"feat={feat!r}")


def test_summarize_feature_routing_empty_manifest_omits_accuracy_keys() -> None:
    """--manifest given but the file parsed to zero rows -- same fallback as
    no manifest at all (design's Error Handling section)."""
    rows = [_routing_row(call_id="c1")]
    lines = [json.dumps(r) for r in rows]
    feat = aggregate(lines, [])["features"]["routing"]
    keys = ("n_ground_truth", "jev_accuracy", "heuristic_accuracy", "accuracy_improvement")
    if all(k not in feat for k in keys):
        ok("aggregate(lines, []) (empty manifest) omits all 4 accuracy keys from routing summary")
    else:
        fail("summarize-routing-empty-manifest", f"feat={feat!r}")


def test_summarize_feature_routing_non_matching_manifest_omits_accuracy_keys() -> None:
    """Manifest has real rows, but none match this run's routing call_ids --
    same fallback as empty/no manifest (design's Error Handling section)."""
    rows = [_routing_row(call_id="orphan1"), _routing_row(call_id="orphan2")]
    lines = [json.dumps(r) for r in rows]
    manifest_rows = [{"call_id": "unrelated-1", "workflow_type": "DEBUG"}]
    feat = aggregate(lines, manifest_rows)["features"]["routing"]
    keys = ("n_ground_truth", "jev_accuracy", "heuristic_accuracy", "accuracy_improvement")
    if all(k not in feat for k in keys):
        ok("aggregate() with a non-matching manifest omits all 4 accuracy keys from routing summary")
    else:
        fail("summarize-routing-non-matching-manifest", f"feat={feat!r}")


def test_summarize_feature_routing_null_workflow_type_excluded_from_ground_truth() -> None:
    """A manifest row matched by call_id but with workflow_type: null must
    not count toward n_ground_truth (design's Error Handling section)."""
    rows = [
        _routing_row(call_id="c1", answers={"choice": "DEBUG", "confidence": 0.9},
                     heuristic_result={"workflow": "DEBUG", "risk_signals": []}, agree=True),
        _routing_row(call_id="c2", answers={"choice": "BUILD", "confidence": 0.9},
                     heuristic_result={"workflow": "BUILD", "risk_signals": []}, agree=True),
    ]
    manifest_rows = [
        {"call_id": "c1", "workflow_type": "DEBUG"},
        {"call_id": "c2", "workflow_type": None},
    ]
    lines = [json.dumps(r) for r in rows]
    feat = aggregate(lines, manifest_rows)["features"]["routing"]
    checks = (
        feat["n"] == 2,
        feat["n_ground_truth"] == 1,
        feat["jev_accuracy"] == 1.0,
    )
    if all(checks):
        ok("aggregate() excludes null-workflow_type manifest matches from n_ground_truth")
    else:
        fail("summarize-routing-null-ground-truth", f"feat={feat!r} checks={checks!r}")


def test_jev_choice_and_heuristic_choice_match_ab_reports_routing_only_helpers() -> None:
    """Guards Durable Decision D2: craftflow_jev_report.py deliberately
    reuses its own local _jev_choice/_heuristic_choice("routing") instead
    of importing craftflow_jev_ab_report.py's AB-only
    _routing_jev_choice/_routing_heuristic_choice. This test proves the two
    pairs stay semantically identical -- if either file's helper is ever
    edited to diverge, this fails loudly instead of silently computing
    wrong routing accuracy numbers."""
    import craftflow_jev_ab_report

    rows = [
        _routing_row(call_id="c1", answers={"choice": "DEBUG", "confidence": 0.9},
                     heuristic_result={"workflow": "BUILD", "risk_signals": []}),
        _routing_row(call_id="c2", answers={"choice": None, "confidence": 0.9},
                     heuristic_result={"workflow": None, "risk_signals": []}),
    ]
    all_match = all(
        _jev_choice(r) == craftflow_jev_ab_report._routing_jev_choice(r)
        and _heuristic_choice(r, "routing") == craftflow_jev_ab_report._routing_heuristic_choice(r)
        for r in rows
    )
    if all_match:
        ok("_jev_choice/_heuristic_choice('routing') stay semantically identical to the AB-only equivalents")
    else:
        fail("jev-choice-equivalence", f"rows={rows!r}")


def test_summarize_feature_skill_and_remfix_scope_never_get_accuracy_keys_even_with_manifest() -> None:
    """skill/remfix_scope have no ground-truth join defined -- accuracy keys
    must stay absent even when a real, matching manifest is supplied
    (proves the "only routing" rule, not just "no manifest given")."""
    skill_rows = [_skill_row(call_id="s1")]
    remfix_rows = [{"feature": "remfix_scope", "call_id": "s1", "agree": True,
                     "answers": {"choice": "critical_only"}, "heuristic_result": "critical_only",
                     "latency_ms": 100, "usage": {}, "cache_hit": False, "injected": False}]
    lines = [json.dumps(r) for r in skill_rows + remfix_rows]
    manifest_rows = [{"call_id": "s1", "workflow_type": "BUILD"}]
    summary = aggregate(lines, manifest_rows)["features"]
    keys = ("n_ground_truth", "jev_accuracy", "heuristic_accuracy", "accuracy_improvement")
    if all(k not in summary["skill"] for k in keys) and all(k not in summary["remfix_scope"] for k in keys):
        ok("skill/remfix_scope never get accuracy keys, even with a real matching manifest supplied")
    else:
        fail("summarize-skill-remfix-no-accuracy", f"skill={summary['skill']!r} remfix={summary['remfix_scope']!r}")


# ---------------------------------------------------------------------------
# remfix_scope feature (Phase 4: FEATURES tuple + CLI flag + _min_agreement_for)
# ---------------------------------------------------------------------------


def test_features_tuple_includes_remfix_scope() -> None:
    if "remfix_scope" in FEATURES and len(FEATURES) == 3:
        ok("FEATURES tuple includes remfix_scope")
    else:
        fail("features-tuple", f"FEATURES={FEATURES!r}")


def test_cli_min_agreement_remfix_scope_flag_defaults_to_080() -> None:
    args = build_arg_parser().parse_args([])
    if args.min_agreement_remfix_scope == 0.80:
        ok("--min-agreement-remfix-scope defaults to 0.80")
    else:
        fail("min-agreement-remfix-scope-default", f"args={args!r}")


def test_aggregate_and_verdict_promote_remfix_scope_rows() -> None:
    rows = [
        {"feature": "remfix_scope", "agree": True, "answers": {"choice": "critical_only"}, "heuristic_result": "critical_only", "latency_ms": 100, "usage": {}, "cache_hit": False, "injected": False}
        for _ in range(100)
    ]
    summary = aggregate([json.dumps(r) for r in rows])
    feat = summary["features"]["remfix_scope"]
    result, _reason = verdict(feat, min_n=100, min_agreement=0.80)
    if feat["n"] == 100 and feat["agreement"] == 1.0 and result == "PROMOTE" and "agree_risk" not in feat:
        ok("remfix_scope rows aggregate and PROMOTE exactly like routing/skill (no agree_risk key)")
    else:
        fail("remfix-scope-promote", f"feat={feat!r} result={result!r}")


def test_routing_and_skill_verdicts_unchanged_by_third_feature() -> None:
    # Regression guard: adding remfix_scope must not change routing/skill's own bars.
    args = build_arg_parser().parse_args([])
    if args.min_agreement_routing == 0.80 and args.min_agreement_skill == 0.60:
        ok("routing/skill CLI defaults unchanged by remfix_scope addition")
    else:
        fail("routing-skill-defaults-unchanged", f"args={args!r}")


# ---------------------------------------------------------------------------
# verdict()
# ---------------------------------------------------------------------------


def test_verdict_promote_when_n_and_agreement_meet_thresholds() -> None:
    result, reason = verdict({"n": 150, "agreement": 0.85}, min_n=100, min_agreement=0.8)
    if result == "PROMOTE" and "150" in reason and "0.85" in reason:
        ok("verdict() PROMOTEs when n and agreement both meet threshold")
    else:
        fail("verdict-promote", f"result={result!r} reason={reason!r}")


def test_verdict_hold_when_n_below_minimum() -> None:
    result, reason = verdict({"n": 40, "agreement": 0.95}, min_n=100, min_agreement=0.8)
    if result == "HOLD" and "40" in reason and "100" in reason:
        ok("verdict() HOLDs when n is below min_n even if agreement is high")
    else:
        fail("verdict-hold-n", f"result={result!r} reason={reason!r}")


def test_verdict_hold_when_agreement_below_minimum() -> None:
    result, reason = verdict({"n": 150, "agreement": 0.5}, min_n=100, min_agreement=0.8)
    if result == "HOLD" and "0.50" in reason and "0.80" in reason:
        ok("verdict() HOLDs when agreement is below min_agreement even with enough n")
    else:
        fail("verdict-hold-agreement", f"result={result!r} reason={reason!r}")


def test_verdict_hold_no_data_when_n_zero() -> None:
    result, reason = verdict({"n": 0, "agreement": 0.0}, min_n=100, min_agreement=0.8)
    if result == "HOLD" and reason == "no data":
        ok("verdict() HOLDs with reason 'no data' when n == 0")
    else:
        fail("verdict-hold-no-data", f"result={result!r} reason={reason!r}")


# ---------------------------------------------------------------------------
# verdict() dual-gate (accuracy OR agreement)
# ---------------------------------------------------------------------------


def test_verdict_backward_compatible_3arg_call_unchanged() -> None:
    """Old 3-positional-arg call sites (no min_accuracy_margin) must behave
    byte-identically to before this change -- min_accuracy_margin defaults."""
    result, reason = verdict({"n": 150, "agreement": 0.85}, min_n=100, min_agreement=0.8)
    if result == "PROMOTE" and reason == "n=150, agreement=0.85":
        ok("verdict() 3-arg backward-compat call is byte-identical to pre-dual-gate behavior")
    else:
        fail("verdict-backward-compat", f"result={result!r} reason={reason!r}")


def test_verdict_dual_gate_agreement_passes_accuracy_fails_promotes() -> None:
    feat = {
        "n": 150, "agreement": 0.85,
        "n_ground_truth": 150, "jev_accuracy": 0.60, "heuristic_accuracy": 0.58,
        "accuracy_improvement": 0.02,
    }
    result, reason = verdict(feat, min_n=100, min_agreement=0.8, min_accuracy_margin=0.10)
    if result == "PROMOTE" and "agreement=0.85>=0.80" in reason and "accuracy:" in reason:
        ok("verdict() dual-gate: agreement passes, accuracy fails margin -> PROMOTE, both gates named")
    else:
        fail("verdict-dual-agreement-only", f"result={result!r} reason={reason!r}")


def test_verdict_dual_gate_accuracy_passes_agreement_fails_promotes() -> None:
    feat = {
        "n": 150, "agreement": 0.50,
        "n_ground_truth": 150, "jev_accuracy": 0.74, "heuristic_accuracy": 0.55,
        "accuracy_improvement": 0.19,
    }
    result, reason = verdict(feat, min_n=100, min_agreement=0.8, min_accuracy_margin=0.10)
    if (
        result == "PROMOTE"
        and "agreement=0.50<0.80" in reason
        and "jev=0.74" in reason and "heuristic=0.55" in reason and "+0.19>=0.10" in reason
    ):
        ok("verdict() dual-gate: accuracy passes margin, agreement fails -> PROMOTE, both gates named")
    else:
        fail("verdict-dual-accuracy-only", f"result={result!r} reason={reason!r}")


def test_verdict_dual_gate_both_fail_holds() -> None:
    feat = {
        "n": 150, "agreement": 0.50,
        "n_ground_truth": 150, "jev_accuracy": 0.60, "heuristic_accuracy": 0.58,
        "accuracy_improvement": 0.02,
    }
    result, reason = verdict(feat, min_n=100, min_agreement=0.8, min_accuracy_margin=0.10)
    if result == "HOLD" and "agreement=0.50<0.80" in reason and "+0.02<0.10" in reason:
        ok("verdict() dual-gate: both gates fail -> HOLD, both gates named")
    else:
        fail("verdict-dual-both-fail", f"result={result!r} reason={reason!r}")


def test_verdict_dual_gate_both_pass_promotes() -> None:
    feat = {
        "n": 150, "agreement": 0.85,
        "n_ground_truth": 150, "jev_accuracy": 0.74, "heuristic_accuracy": 0.55,
        "accuracy_improvement": 0.19,
    }
    result, reason = verdict(feat, min_n=100, min_agreement=0.8, min_accuracy_margin=0.10)
    if result == "PROMOTE" and "agreement=0.85>=0.80" in reason and "+0.19>=0.10" in reason:
        ok("verdict() dual-gate: both gates pass -> PROMOTE, both gates named")
    else:
        fail("verdict-dual-both-pass", f"result={result!r} reason={reason!r}")


def test_verdict_accuracy_gate_own_floor_not_met_falls_back_to_agreement_alone() -> None:
    """n_ground_truth (5) is below min_n (100) even though the raw accuracy
    improvement (0.40) would clear the margin easily -- the accuracy gate
    must not fire; only the agreement gate decides."""
    feat = {
        "n": 150, "agreement": 0.50,
        "n_ground_truth": 5, "jev_accuracy": 0.80, "heuristic_accuracy": 0.40,
        "accuracy_improvement": 0.40,
    }
    result, reason = verdict(feat, min_n=100, min_agreement=0.8, min_accuracy_margin=0.10)
    if result == "HOLD" and "n_ground_truth=5<100" in reason and "insufficient sample" in reason:
        ok("verdict() accuracy gate's own n_ground_truth floor blocks a large accuracy_improvement from firing")
    else:
        fail("verdict-accuracy-floor-not-met", f"result={result!r} reason={reason!r}")


# ---------------------------------------------------------------------------
# Malformed-telemetry crash guards (silent-failure-hunter remediation)
# ---------------------------------------------------------------------------


def test_aggregate_excludes_nan_and_negative_latency_from_stats() -> None:
    rows = [
        _routing_row(call_id="c1", ts="t1", latency_ms=100),
        _routing_row(call_id="c2", ts="t2", latency_ms=float("nan")),
        _routing_row(call_id="c3", ts="t3", latency_ms=-500),
        _routing_row(call_id="c4", ts="t4", latency_ms=200),
    ]
    lines = [json.dumps(row) for row in rows]
    feat = aggregate(lines)["features"]["routing"]
    checks = (
        feat["n"] == 4,
        feat["mean_latency_ms"] == 150.0,
        abs(feat["p95_latency_ms"] - 195.0) < 1e-9,
        feat.get("invalid_latency") == 2,
    )

    with tempfile.TemporaryDirectory() as tmp:
        events = Path(tmp) / "events.jsonl"
        events.write_text("\n".join(lines) + "\n")
        proc = _run_cli(["--events", str(events), "--json"], cwd=tmp)
    cli_ok = proc.returncode == 0 and "NaN" not in proc.stdout

    if all(checks) and cli_ok:
        ok(
            "aggregate() excludes NaN/negative latency_ms from mean/p95, counts them as "
            "invalid_latency, and --json emits no bare NaN token"
        )
    else:
        fail(
            "aggregate-nan-negative-latency",
            f"feat={feat!r} checks={checks!r} cli_code={proc.returncode} cli_out={proc.stdout!r}",
        )


def test_aggregate_excludes_infinity_latency_from_stats() -> None:
    rows = [
        _routing_row(call_id="c1", ts="t1", latency_ms=100),
        _routing_row(call_id="c2", ts="t2", latency_ms=float("inf")),
        _routing_row(call_id="c3", ts="t3", latency_ms=float("-inf")),
        _routing_row(call_id="c4", ts="t4", latency_ms=200),
    ]
    lines = [json.dumps(row) for row in rows]
    feat = aggregate(lines)["features"]["routing"]
    checks = (
        feat["n"] == 4,
        feat["mean_latency_ms"] == 150.0,
        abs(feat["p95_latency_ms"] - 195.0) < 1e-9,
        feat.get("invalid_latency") == 2,
        math.isfinite(feat["mean_latency_ms"]),
        math.isfinite(feat["p95_latency_ms"]),
    )

    with tempfile.TemporaryDirectory() as tmp:
        events = Path(tmp) / "events.jsonl"
        events.write_text("\n".join(lines) + "\n")
        proc = _run_cli(["--events", str(events), "--json"], cwd=tmp)
    cli_ok = (
        proc.returncode == 0
        and "inf" not in proc.stdout.lower()
        and "Infinity" not in proc.stdout
    )

    if all(checks) and cli_ok:
        ok(
            "aggregate() excludes +Infinity/-Infinity latency_ms from mean/p95, counts them as "
            "invalid_latency, and --json exits 0 with no inf/Infinity token in stdout"
        )
    else:
        fail(
            "aggregate-infinity-latency",
            f"feat={feat!r} checks={checks!r} cli_code={proc.returncode} cli_out={proc.stdout!r}",
        )


def test_aggregate_does_not_crash_on_nan_or_infinity_usage_tokens() -> None:
    rows = [
        _routing_row(call_id="c1", ts="t1", usage={"input_tokens": float("nan"), "output_tokens": 5}),
        _routing_row(call_id="c2", ts="t2", usage={"input_tokens": 5, "output_tokens": float("inf")}),
    ]
    lines = [json.dumps(row) for row in rows]
    try:
        feat = aggregate(lines)["features"]["routing"]
    except (ValueError, OverflowError) as exc:
        fail("aggregate-nan-inf-usage-tokens", f"aggregate() crashed instead of excluding: {exc!r}")
        return
    checks = (
        feat["n"] == 2,
        feat["input_tokens"] == 5,
        feat["output_tokens"] == 5,
    )
    if all(checks):
        ok("aggregate() treats NaN/Infinity usage token counts as 0 instead of crashing")
    else:
        fail("aggregate-nan-inf-usage-tokens", f"feat={feat!r} checks={checks!r}")


def test_aggregate_does_not_crash_on_non_string_feature_value() -> None:
    rows_json = [
        json.dumps({"feature": ["routing"], "agree": True}),  # list feature -- unhashable
        json.dumps({"feature": {"k": "v"}, "agree": True}),  # dict feature -- unhashable
        json.dumps(_routing_row(call_id="c1", ts="t1")),
    ]
    try:
        summary = aggregate(rows_json)
    except TypeError as exc:
        fail("aggregate-non-string-feature", f"aggregate() crashed instead of skipping: {exc!r}")
        return
    checks = (
        summary["malformed"] == 2,
        summary["features"]["routing"]["n"] == 1,
        summary["features"]["skill"]["n"] == 0,
    )
    if all(checks):
        ok("aggregate() treats a non-string feature (list/dict) as malformed instead of crashing")
    else:
        fail("aggregate-non-string-feature", f"summary={summary!r} checks={checks!r}")


def test_aggregate_sanitizes_nan_infinity_in_disagreement_fields() -> None:
    rows = [
        _routing_row(
            call_id=float("nan"),
            ts=float("inf"),
            agree=False,
            answers={"choice": float("-inf"), "confidence": 0.5},
            heuristic_result={"workflow": float("nan"), "risk_signals": []},
        ),
    ]
    lines = [json.dumps(row) for row in rows]
    feat = aggregate(lines)["features"]["routing"]
    expected_disagreement = {"call_id": None, "ts": None, "jev": None, "heuristic": None}
    checks = (
        feat["n"] == 1,
        feat["disagreements"] == [expected_disagreement],
    )

    with tempfile.TemporaryDirectory() as tmp:
        events = Path(tmp) / "events.jsonl"
        events.write_text("\n".join(lines) + "\n")
        proc = _run_cli(["--events", str(events), "--json"], cwd=tmp)
    cli_ok = proc.returncode == 0 and "Traceback" not in proc.stderr
    if cli_ok:
        try:
            json.loads(proc.stdout)
        except json.JSONDecodeError:
            cli_ok = False

    if all(checks) and cli_ok:
        ok(
            "aggregate() coerces NaN/Infinity call_id/ts/answers.choice/heuristic_result.workflow "
            "to None in disagreements, and --json exits 0 with valid JSON"
        )
    else:
        fail(
            "aggregate-nan-inf-disagreement-fields",
            f"feat={feat!r} checks={checks!r} cli_code={proc.returncode} "
            f"cli_out={proc.stdout!r} cli_err={proc.stderr!r}",
        )


def test_aggregate_sanitizes_nested_nan_infinity_in_disagreement_fields() -> None:
    """8th crash variant: call_id/ts/answers.choice/heuristic_result.workflow can
    themselves be a list/dict containing a nested NaN/Infinity, e.g.
    {"call_id": [1, NaN]}. _sanitize_json_value only coerced bare non-finite
    floats to None, so a nested NaN/Infinity inside a container passed through
    unchanged and still reached json.dumps(..., allow_nan=False) in --json mode."""
    rows = [
        _routing_row(
            call_id=[1, float("nan")],
            ts={"a": float("inf")},
            agree=False,
            answers={"choice": [float("-inf")], "confidence": 0.5},
            heuristic_result={"workflow": {"x": float("nan")}, "risk_signals": []},
        ),
    ]
    lines = [json.dumps(row) for row in rows]
    feat = aggregate(lines)["features"]["routing"]
    expected_disagreement = {"call_id": None, "ts": None, "jev": None, "heuristic": None}
    checks = (
        feat["n"] == 1,
        feat["disagreements"] == [expected_disagreement],
    )

    with tempfile.TemporaryDirectory() as tmp:
        events = Path(tmp) / "events.jsonl"
        events.write_text("\n".join(lines) + "\n")
        proc = _run_cli(["--events", str(events), "--json"], cwd=tmp)
    cli_ok = proc.returncode == 0 and "Traceback" not in proc.stderr
    if cli_ok:
        try:
            json.loads(proc.stdout)
        except json.JSONDecodeError:
            cli_ok = False

    if all(checks) and cli_ok:
        ok(
            "aggregate() coerces nested list/dict-wrapped NaN/Infinity in "
            "call_id/ts/answers.choice/heuristic_result.workflow to None in "
            "disagreements, and --json exits 0 with valid JSON"
        )
    else:
        fail(
            "aggregate-nested-nan-inf-disagreement-fields",
            f"feat={feat!r} checks={checks!r} cli_code={proc.returncode} "
            f"cli_out={proc.stdout!r} cli_err={proc.stderr!r}",
        )


def test_aggregate_excludes_huge_int_latency_from_stats() -> None:
    """9th crash variant: `math.isfinite(value)` raises `OverflowError: int too
    large to convert to float` for a plain JSON integer with magnitude >=
    ~1.8e308 (309+ digits) -- unlike a JSON float literal like 1e400, which
    json.loads silently coerces to math.inf with no exception."""
    huge_int = 10**400
    rows = [
        _routing_row(call_id="c1", ts="t1", latency_ms=100),
        _routing_row(call_id="c2", ts="t2", latency_ms=huge_int),
        _routing_row(call_id="c3", ts="t3", latency_ms=200),
    ]
    lines = [json.dumps(row) for row in rows]
    try:
        feat = aggregate(lines)["features"]["routing"]
    except OverflowError as exc:
        fail("aggregate-huge-int-latency", f"aggregate() crashed instead of excluding: {exc!r}")
        return
    checks = (
        feat["n"] == 3,
        feat["mean_latency_ms"] == 150.0,
        feat.get("invalid_latency") == 1,
    )
    if all(checks):
        ok("aggregate() excludes a 309+ digit int latency_ms from mean/p95 instead of crashing with OverflowError")
    else:
        fail("aggregate-huge-int-latency", f"feat={feat!r} checks={checks!r}")


def test_aggregate_does_not_crash_on_huge_int_usage_tokens() -> None:
    """9th crash variant, usage.tokens call site: same OverflowError from
    math.isfinite() on a 309+ digit int, this time in _usage_tokens()."""
    huge_int = 10**400
    rows = [
        _routing_row(call_id="c1", ts="t1", usage={"input_tokens": huge_int, "output_tokens": 5}),
        _routing_row(call_id="c2", ts="t2", usage={"input_tokens": 5, "output_tokens": huge_int}),
    ]
    lines = [json.dumps(row) for row in rows]
    try:
        feat = aggregate(lines)["features"]["routing"]
    except OverflowError as exc:
        fail("aggregate-huge-int-usage-tokens", f"aggregate() crashed instead of excluding: {exc!r}")
        return
    checks = (
        feat["n"] == 2,
        feat["input_tokens"] == 5,
        feat["output_tokens"] == 5,
    )
    if all(checks):
        ok("aggregate() treats a 309+ digit int usage token count as 0 instead of crashing with OverflowError")
    else:
        fail("aggregate-huge-int-usage-tokens", f"feat={feat!r} checks={checks!r}")


def test_aggregate_excludes_inf_from_sum_overflow_in_mean_and_p95() -> None:
    """14th crash variant: each latency_ms=1.5e308 individually passes
    _is_finite(value) and value >= 0 (both rows are valid, finite floats
    below math.inf), so both land in valid_latencies unfiltered. But
    sum(valid_latencies) itself overflows to Python float inf once the two
    finite values are added together -- the per-row guard never re-checks
    the aggregate. That inf then flows unguarded into mean_latency_ms (and
    p95_latency_ms's linear interpolation) past every existing guard, and
    crashes json.dumps(..., allow_nan=False) with ValueError: Out of range
    float values are not JSON compliant: inf in --json mode."""
    with tempfile.TemporaryDirectory() as tmp:
        events = Path(tmp) / "events.jsonl"
        rows = [
            _routing_row(call_id="a1", latency_ms=1.5e308),
            _routing_row(call_id="a2", latency_ms=1.5e308),
        ]
        events.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        proc = _run_cli(["--events", str(events), "--json"], cwd=tmp)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            fail(
                "cli-latency-sum-overflow",
                f"code={proc.returncode} invalid JSON: {exc}; stdout={proc.stdout!r} err={proc.stderr!r}",
            )
            return
        routing = payload.get("features", {}).get("routing", {})
        p95 = routing.get("p95_latency_ms")
        # mean_latency_ms = sum(both)/2 overflows to inf -> coerced to None.
        # p95_latency_ms's linear interpolation over these 2 equal values
        # stays in float range (each interpolation weight is < 1), so it is
        # legitimately finite here -- not a second None case. The real proof
        # this scenario no longer crashes is that json.loads(proc.stdout)
        # above already succeeded: allow_nan=False would have raised
        # ValueError had either field still been inf.
        if (
            proc.returncode == 0
            and routing.get("n") == 2
            and routing.get("mean_latency_ms") is None
            and isinstance(p95, (int, float))
            and math.isfinite(p95)
        ):
            ok(
                "CLI --json: two individually-finite latency_ms values whose sum "
                "overflows to inf do not crash json.dumps (mean coerced to None)"
            )
        else:
            fail("cli-latency-sum-overflow", f"code={proc.returncode} payload={payload!r} err={proc.stderr!r}")


def test_aggregate_does_not_crash_on_invalid_utf8_events_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        events = Path(tmp) / "events.jsonl"
        events.write_bytes(b"\xff\xfe\x00not valid utf8 \x80\x81\n")
        proc = _run_cli(["--events", str(events)], cwd=tmp)
        if proc.returncode == 0 and "Traceback" not in proc.stderr:
            ok("CLI: invalid UTF-8 events file runs to completion (exit 0, no traceback)")
        else:
            fail("cli-invalid-utf8", f"code={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}")


def test_read_lines_returns_empty_list_on_memory_error() -> None:
    """MemoryError is a direct subclass of Exception, not OSError, so
    Path.read_text() raising it (a runaway/oversized events.jsonl that
    cannot be allocated as a string) must be caught the same way the
    OSError/UnicodeDecodeError cases already are -- graceful "unreadable
    file -> empty list -> HOLD", not a raw crash."""
    with patch.object(Path, "read_text", side_effect=MemoryError("cannot allocate memory")):
        result = _read_lines(Path("/does/not/matter"))
    if result == []:
        ok("_read_lines: MemoryError from Path.read_text() returns [] instead of raising")
    else:
        fail("read-lines-memory-error", f"expected [], got {result!r}")


class _EvilStr(str):
    """A str subclass whose .encode() raises MemoryError, simulating an
    allocation failure during UTF-8 encoding (worst case is 4x the codepoint
    count in bytes)."""

    def encode(self, *args, **kwargs):  # noqa: D401
        raise MemoryError("simulated OOM during utf-8 encode")


def test_sanitize_json_value_returns_none_on_memory_error_during_encode() -> None:
    """12th crash variant: _sanitize_json_value()'s str.encode("utf-8") probe
    (added to catch lone UTF-16 surrogates, see the 10th-variant test below)
    only caught UnicodeEncodeError. str.encode() can also raise MemoryError
    when the encoded buffer allocation fails -- the same exception class
    already guarded at the file-read boundary in this file (_read_lines,
    see test_read_lines_returns_empty_list_on_memory_error above). Called
    from _summarize_feature -> aggregate() -> main() with no try/except
    anywhere in that chain, so an uncaught MemoryError here crashes the
    whole CLI."""
    try:
        result = _sanitize_json_value(_EvilStr("hostile"))
    except MemoryError as exc:
        fail("sanitize-json-value-memory-error", f"_sanitize_json_value() raised instead of returning None: {exc!r}")
        return
    if result is None:
        ok("_sanitize_json_value: MemoryError from str.encode() returns None instead of raising")
    else:
        fail("sanitize-json-value-memory-error", f"expected None, got {result!r}")


class _EvilStripStr(str):
    """A str subclass whose .strip() raises MemoryError, simulating an
    allocation failure during the strip() call's new-string copy."""

    def strip(self, *args, **kwargs):  # noqa: D401
        raise MemoryError("simulated OOM during strip")


def test_aggregate_counts_memory_error_from_line_strip_as_malformed() -> None:
    """aggregate()'s `line = raw_line.strip()` sits BEFORE/outside the
    adjacent try/except that only wraps json.loads(line). str.strip()
    allocates a new string copy and can raise MemoryError under allocation
    pressure -- uncaught, this crashes aggregate() -> main() with no
    try/except anywhere in that chain, the same failure class already
    guarded at the file-read boundary (_read_lines) and the sanitize
    boundary (_sanitize_json_value, see the two MemoryError tests above).
    A line that raises MemoryError during strip() must be counted as
    malformed instead of propagating."""
    try:
        result = aggregate([_EvilStripStr("hostile")])
    except MemoryError as exc:
        fail("aggregate-strip-memory-error", f"aggregate() raised instead of counting malformed: {exc!r}")
        return
    if result.get("malformed") == 1:
        ok("aggregate(): MemoryError from raw_line.strip() counted as malformed instead of raising")
    else:
        fail("aggregate-strip-memory-error", f"expected malformed=1, got {result!r}")


def test_cli_lone_surrogate_strings_do_not_crash_json_or_text_mode() -> None:
    """10th crash variant: a JSON string containing a lone UTF-16 surrogate
    code point (e.g. "\\ud800") is valid per json.loads (JSON doesn't validate
    UTF-16 well-formedness) but crashes sys.stdout.write() in text-mode output
    with UnicodeEncodeError: 'utf-8' codec can't encode character ...
    surrogates not allowed. --json mode survives because
    json.dumps(..., ensure_ascii=True) escapes it safely; text mode has no
    equivalent guard until fixed. call_id, ts, answers.choice, and
    heuristic_result.workflow can all carry this."""
    lone_surrogate = "\ud800"
    rows = [
        _routing_row(
            call_id=lone_surrogate,
            ts=lone_surrogate,
            agree=False,
            answers={"choice": lone_surrogate, "confidence": 0.5},
            heuristic_result={"workflow": lone_surrogate, "risk_signals": []},
        ),
    ]
    lines = [json.dumps(row) for row in rows]
    with tempfile.TemporaryDirectory() as tmp:
        events = Path(tmp) / "events.jsonl"
        events.write_text("\n".join(lines) + "\n", encoding="utf-8")

        json_proc = _run_cli(["--events", str(events), "--json"], cwd=tmp)
        json_ok = json_proc.returncode == 0 and "Traceback" not in json_proc.stderr
        if json_ok:
            try:
                json.loads(json_proc.stdout)
            except json.JSONDecodeError:
                json_ok = False

        text_proc = _run_cli(["--events", str(events)], cwd=tmp)
        text_ok = text_proc.returncode == 0 and "Traceback" not in text_proc.stderr

    if json_ok and text_ok:
        ok("CLI: lone surrogate call_id/ts/answers.choice/heuristic_result.workflow exits 0 in both --json and text mode")
    else:
        fail(
            "cli-lone-surrogate",
            f"json_code={json_proc.returncode} json_err={json_proc.stderr!r} "
            f"text_code={text_proc.returncode} text_err={text_proc.stderr!r}",
        )


# ---------------------------------------------------------------------------
# Broad adversarial regression: close the whole crash-bug class in one test
# rather than requiring an 11th hunt pass to find a 12th variant.
# ---------------------------------------------------------------------------

_HOSTILE_VALUES = (
    float("nan"),
    float("inf"),
    float("-inf"),
    10**400,
    [1, 2, {"nested": float("nan")}],
    {"nested": "dict"},
    "\ud800",
    "",
    None,
)

_HOSTILE_SCALAR_FIELDS = (
    "call_id",
    "ts",
    "answers.choice",
    "heuristic_result.workflow",
    "latency_ms",
    "usage.input_tokens",
    "usage.output_tokens",
    "feature",
)


def _apply_hostile_value(row: Dict[str, Any], field: str, value: Any) -> None:
    if field == "answers.choice":
        row["answers"] = dict(row["answers"])
        row["answers"]["choice"] = value
    elif field == "heuristic_result.workflow":
        row["heuristic_result"] = dict(row["heuristic_result"])
        row["heuristic_result"]["workflow"] = value
    elif field == "usage.input_tokens":
        row["usage"] = dict(row["usage"])
        row["usage"]["input_tokens"] = value
    elif field == "usage.output_tokens":
        row["usage"] = dict(row["usage"])
        row["usage"]["output_tokens"] = value
    else:
        row[field] = value


def test_cli_never_crashes_on_any_hostile_value_in_any_scalar_field() -> None:
    all_clean = True
    with tempfile.TemporaryDirectory() as tmp:
        for field in _HOSTILE_SCALAR_FIELDS:
            for value in _HOSTILE_VALUES:
                row = _routing_row(call_id="c1", ts="t1", agree=False)
                _apply_hostile_value(row, field, value)
                try:
                    line = json.dumps(row)
                except (ValueError, TypeError) as exc:
                    all_clean = False
                    fail(
                        "cli-hostile-value-matrix",
                        f"json.dumps() itself failed for field={field} value={value!r}: {exc!r}",
                    )
                    continue
                events = Path(tmp) / "events.jsonl"
                events.write_text(line + "\n", encoding="utf-8")

                for mode_args in (["--json"], []):
                    proc = _run_cli(["--events", str(events), *mode_args], cwd=tmp)
                    if proc.returncode != 0 or "Traceback" in proc.stderr:
                        all_clean = False
                        fail(
                            "cli-hostile-value-matrix",
                            f"field={field} value={value!r} mode={mode_args!r} "
                            f"code={proc.returncode} err={proc.stderr!r}",
                        )
    if all_clean:
        ok(
            "CLI never crashes (exit 0, no traceback) in --json or text mode for any "
            "hostile value (NaN/Infinity/-Infinity/huge int/nested container/lone "
            "surrogate/empty string/None) across all untrusted scalar fields"
        )


# ---------------------------------------------------------------------------
# CLI (subprocess, real script)
# ---------------------------------------------------------------------------


def _run_cli(args, cwd) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(REPORT_SCRIPT), *args],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_cli_manifest_flag_defaults_to_none() -> None:
    args = build_arg_parser().parse_args([])
    if args.manifest is None:
        ok("--manifest defaults to None (optional, unlike craftflow_jev_ab_report.py's required flag)")
    else:
        fail("cli-manifest-default", f"args.manifest={args.manifest!r}")


def test_cli_min_accuracy_margin_flag_defaults_to_010() -> None:
    args = build_arg_parser().parse_args([])
    if args.min_accuracy_margin == 0.10:
        ok("--min-accuracy-margin defaults to 0.10")
    else:
        fail("cli-min-accuracy-margin-default", f"args={args!r}")


def _write_manifest_fixture(tmp: str):
    events_rows = [
        _routing_row(call_id="c1", agree=False, answers={"choice": "DEBUG", "confidence": 0.9},
                     heuristic_result={"workflow": "BUILD", "risk_signals": []}),
        _routing_row(call_id="c2", agree=False, answers={"choice": "PLAN", "confidence": 0.9},
                     heuristic_result={"workflow": "DEBUG", "risk_signals": []}),
        _routing_row(call_id="c3", agree=False, answers={"choice": "BUILD", "confidence": 0.9},
                     heuristic_result={"workflow": "REVIEW", "risk_signals": []}),
    ]
    manifest_rows = [
        {"call_id": "c1", "workflow_type": "DEBUG"},
        {"call_id": "c2", "workflow_type": "PLAN"},
        {"call_id": "c3", "workflow_type": "REVIEW"},
    ]
    events_path = Path(tmp) / "events.jsonl"
    manifest_path = Path(tmp) / "replay_manifest.jsonl"
    events_path.write_text("\n".join(json.dumps(r) for r in events_rows) + "\n")
    manifest_path.write_text("\n".join(json.dumps(r) for r in manifest_rows) + "\n")
    return events_path, manifest_path


def test_cli_manifest_flag_wires_through_to_accuracy_gate_promote() -> None:
    """End-to-end: agreement=0.0 (all 3 disagree) would HOLD alone, but
    jev_accuracy=2/3 vs heuristic_accuracy=1/3 (+33pp, >= default 10pp
    margin) fires the accuracy gate -- n_ground_truth=3 is below the
    default min_n=100, so use --min-n 3 to exercise a realistic PROMOTE."""
    with tempfile.TemporaryDirectory() as tmp:
        events_path, manifest_path = _write_manifest_fixture(tmp)
        proc = _run_cli(
            ["--events", str(events_path), "--manifest", str(manifest_path), "--min-n", "3", "--json"],
            cwd=tmp,
        )
        payload = json.loads(proc.stdout)
        routing = payload["features"]["routing"]
        if (
            proc.returncode == 0
            and routing["n_ground_truth"] == 3
            and abs(routing["jev_accuracy"] - (2 / 3)) < 1e-9
            and abs(routing["heuristic_accuracy"] - (1 / 3)) < 1e-9
            and routing["agreement"] == 0.0
            and routing["verdict"] == "PROMOTE"
            and "accuracy:" in routing["reason"]
        ):
            ok("CLI --manifest wires through end-to-end: accuracy gate alone PROMOTEs despite 0% agreement")
        else:
            fail("cli-manifest-wiring", f"payload={payload!r}")


def test_cli_manifest_omitted_routing_has_no_accuracy_keys() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        events_path, _manifest_path = _write_manifest_fixture(tmp)
        proc = _run_cli(["--events", str(events_path), "--json"], cwd=tmp)
        payload = json.loads(proc.stdout)
        routing = payload["features"]["routing"]
        keys = ("n_ground_truth", "jev_accuracy", "heuristic_accuracy", "accuracy_improvement")
        if proc.returncode == 0 and all(k not in routing for k in keys):
            ok("CLI without --manifest: routing has no accuracy keys, byte-identical to pre-dual-gate JSON shape")
        else:
            fail("cli-manifest-omitted", f"payload={payload!r}")


def test_cli_nonexistent_manifest_path_errors_loudly() -> None:
    """Mirrors craftflow_jev_ab_report.py's existing precedent for its own
    required --manifest flag: an explicitly-supplied bad path must fail
    loudly, not silently degrade to "no ground truth"."""
    with tempfile.TemporaryDirectory() as tmp:
        events_path, _manifest_path = _write_manifest_fixture(tmp)
        missing_manifest = Path(tmp) / "does-not-exist.jsonl"
        proc = _run_cli(["--events", str(events_path), "--manifest", str(missing_manifest)], cwd=tmp)
        if proc.returncode != 0 and proc.stdout == "" and str(missing_manifest) in proc.stderr:
            ok("CLI exits non-zero with a stderr message naming the path when --manifest does not exist")
        else:
            fail("cli-manifest-missing-path", f"code={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}")


def test_cli_text_mode_shows_accuracy_lines_only_when_manifest_supplied() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        events_path, manifest_path = _write_manifest_fixture(tmp)
        with_manifest = _run_cli(
            ["--events", str(events_path), "--manifest", str(manifest_path), "--min-n", "3"], cwd=tmp
        )
        without_manifest = _run_cli(["--events", str(events_path)], cwd=tmp)
        if (
            with_manifest.returncode == 0
            and "n_ground_truth:" in with_manifest.stdout
            and "jev_accuracy:" in with_manifest.stdout
            and without_manifest.returncode == 0
            and "n_ground_truth:" not in without_manifest.stdout
        ):
            ok("CLI text mode shows accuracy lines only when --manifest is supplied")
        else:
            fail(
                "cli-text-accuracy-lines",
                f"with={with_manifest.stdout!r} without={without_manifest.stdout!r}",
            )


def test_cli_missing_events_file_prints_hold_no_data_and_exits_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "does-not-exist" / "events.jsonl"
        proc = _run_cli(["--events", str(missing)], cwd=tmp)
        if (
            proc.returncode == 0
            and proc.stdout.count("HOLD (no data)") == len(FEATURES)
            and "n: 0" in proc.stdout
        ):
            ok("CLI: missing events file -> n=0, HOLD (no data), exit 0 for all features")
        else:
            fail("cli-missing-file", f"code={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}")


def test_cli_empty_events_file_prints_hold_no_data_and_exits_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        events = Path(tmp) / "events.jsonl"
        events.write_text("")
        proc = _run_cli(["--events", str(events)], cwd=tmp)
        if proc.returncode == 0 and proc.stdout.count("HOLD (no data)") == len(FEATURES):
            ok("CLI: empty events file -> n=0, HOLD (no data), exit 0")
        else:
            fail("cli-empty-file", f"code={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}")


def test_cli_json_flag_outputs_valid_json_with_verdict_and_reason() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        events = Path(tmp) / "events.jsonl"
        rows = [_routing_row(call_id=f"c{i}", agree=True, agree_risk=True) for i in range(5)]
        rows += [_skill_row(call_id=f"s{i}", agree=True) for i in range(5)]
        events.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        proc = _run_cli(["--events", str(events), "--json"], cwd=tmp)
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            fail("cli-json-flag", f"invalid JSON: {exc}; stdout={proc.stdout!r}")
            return
        routing = payload.get("features", {}).get("routing", {})
        skill = payload.get("features", {}).get("skill", {})
        if (
            proc.returncode == 0
            and payload.get("malformed") == 0
            and routing.get("n") == 5
            and routing.get("verdict") == "HOLD"
            and "reason" in routing
            and skill.get("n") == 5
            and "agree_risk" not in skill
        ):
            ok("CLI --json prints a valid JSON payload with per-feature verdict + reason")
        else:
            fail("cli-json-flag", f"payload={payload!r}")


def test_cli_default_events_path_uses_state_root_no_crash() -> None:
    # No --events flag: must fall back to state_root()/jev/events.jsonl derived from
    # CLAUDE_PROJECT_DIR without crashing when that file does not exist (DD-12b: the
    # CLI reads only the events file by path, never stdin).
    import os

    with tempfile.TemporaryDirectory() as tmp:
        env = {k: v for k, v in os.environ.items()}
        env["CLAUDE_PROJECT_DIR"] = tmp
        proc = subprocess.run(
            [sys.executable, str(REPORT_SCRIPT)],
            cwd=tmp,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        if proc.returncode == 0 and "HOLD (no data)" in proc.stdout:
            ok("CLI without --events falls back to state_root()/jev/events.jsonl")
        else:
            fail("cli-default-path", f"code={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}")


def main() -> int:
    print("test_craftflow_jev_report: running")
    test_aggregate_empty_rows_returns_zero_n_for_both_features()
    test_aggregate_skips_and_counts_malformed_lines()
    test_aggregate_agreement_latency_tokens_injected_cache_hits_and_disagreements()
    test_aggregate_skill_feature_never_has_agree_risk_key()
    test_summarize_feature_routing_computes_accuracy_with_manifest()
    test_summarize_feature_routing_no_manifest_omits_accuracy_keys()
    test_summarize_feature_routing_empty_manifest_omits_accuracy_keys()
    test_summarize_feature_routing_non_matching_manifest_omits_accuracy_keys()
    test_summarize_feature_routing_null_workflow_type_excluded_from_ground_truth()
    test_summarize_feature_skill_and_remfix_scope_never_get_accuracy_keys_even_with_manifest()
    test_jev_choice_and_heuristic_choice_match_ab_reports_routing_only_helpers()
    test_features_tuple_includes_remfix_scope()
    test_cli_min_agreement_remfix_scope_flag_defaults_to_080()
    test_aggregate_and_verdict_promote_remfix_scope_rows()
    test_routing_and_skill_verdicts_unchanged_by_third_feature()
    test_verdict_promote_when_n_and_agreement_meet_thresholds()
    test_verdict_hold_when_n_below_minimum()
    test_verdict_hold_when_agreement_below_minimum()
    test_verdict_hold_no_data_when_n_zero()
    test_verdict_backward_compatible_3arg_call_unchanged()
    test_verdict_dual_gate_agreement_passes_accuracy_fails_promotes()
    test_verdict_dual_gate_accuracy_passes_agreement_fails_promotes()
    test_verdict_dual_gate_both_fail_holds()
    test_verdict_dual_gate_both_pass_promotes()
    test_verdict_accuracy_gate_own_floor_not_met_falls_back_to_agreement_alone()
    test_aggregate_excludes_nan_and_negative_latency_from_stats()
    test_aggregate_excludes_infinity_latency_from_stats()
    test_aggregate_does_not_crash_on_nan_or_infinity_usage_tokens()
    test_aggregate_does_not_crash_on_non_string_feature_value()
    test_aggregate_sanitizes_nan_infinity_in_disagreement_fields()
    test_aggregate_sanitizes_nested_nan_infinity_in_disagreement_fields()
    test_aggregate_excludes_huge_int_latency_from_stats()
    test_aggregate_does_not_crash_on_huge_int_usage_tokens()
    test_aggregate_excludes_inf_from_sum_overflow_in_mean_and_p95()
    test_aggregate_does_not_crash_on_invalid_utf8_events_file()
    test_read_lines_returns_empty_list_on_memory_error()
    test_sanitize_json_value_returns_none_on_memory_error_during_encode()
    test_aggregate_counts_memory_error_from_line_strip_as_malformed()
    test_cli_lone_surrogate_strings_do_not_crash_json_or_text_mode()
    test_cli_never_crashes_on_any_hostile_value_in_any_scalar_field()
    test_cli_manifest_flag_defaults_to_none()
    test_cli_min_accuracy_margin_flag_defaults_to_010()
    test_cli_manifest_flag_wires_through_to_accuracy_gate_promote()
    test_cli_manifest_omitted_routing_has_no_accuracy_keys()
    test_cli_nonexistent_manifest_path_errors_loudly()
    test_cli_text_mode_shows_accuracy_lines_only_when_manifest_supplied()
    test_cli_missing_events_file_prints_hold_no_data_and_exits_zero()
    test_cli_empty_events_file_prints_hold_no_data_and_exits_zero()
    test_cli_json_flag_outputs_valid_json_with_verdict_and_reason()
    test_cli_default_events_path_uses_state_root_no_crash()

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
