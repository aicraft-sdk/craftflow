#!/usr/bin/env python3
"""Operator CLI -- joins replay telemetry (.craftflow/state/jev/events.jsonl
DD-8 rows, or an isolated replay events.jsonl from craftflow_jev_replay.py)
against replay_manifest.jsonl ground truth (also from craftflow_jev_replay.py:
{"call_id": str, "source_workflow_uuid": str, "workflow_type": str}) to produce
a Jev-on-vs-Jev-off A/B comparison: agreement for both features, accuracy for
routing only (no ground truth exists for which skill "should" have been
chosen), added latency, and added token cost.

Reads events/manifest by path only -- never stdin. Import-cheap: no file
reads, env lookups, network, or argparse parsing at import time; all of that
happens inside main(), which only runs under `if __name__ == "__main__"`.

Run: python3 scripts/craftflow_jev_ab_report.py --events <path> --manifest <path> [--json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from craftflow_hooklib import state_root
from craftflow_jev_report import _is_number, _percentile, _usage_tokens

FEATURES = ("routing", "skill")

_NO_GROUND_TRUTH = "not available (no ground truth)"


# ---------------------------------------------------------------------------
# Pure aggregation (unit-testable directly -- no I/O).
# ---------------------------------------------------------------------------


def _is_finite(value: Any) -> bool:
    """Local copy of craftflow_jev_report._is_finite (not in the plan's
    DRY-reuse import list, which names only _is_number/_percentile/
    _usage_tokens) -- math.isfinite() raises OverflowError for a plain JSON
    integer too large to convert to a C double; treat that like NaN/Infinity:
    not finite."""
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _routing_jev_choice(row: Dict[str, Any]) -> Optional[str]:
    answers = row.get("answers")
    return answers.get("choice") if isinstance(answers, dict) else None


def _routing_heuristic_choice(row: Dict[str, Any]) -> Optional[str]:
    heuristic_result = row.get("heuristic_result")
    return heuristic_result.get("workflow") if isinstance(heuristic_result, dict) else None


def _manifest_by_call_id(manifest_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    lookup: Dict[str, Any] = {}
    for row in manifest_rows:
        call_id = row.get("call_id")
        if isinstance(call_id, str) and call_id:
            lookup[call_id] = row.get("workflow_type")
    return lookup


def _added_latency_ms(rows: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    valid_latencies: List[float] = []
    for r in rows:
        value = r.get("latency_ms")
        if _is_number(value) and _is_finite(value) and value >= 0:
            valid_latencies.append(value)
    latencies = sorted(valid_latencies)
    if not latencies:
        return {"mean": 0.0, "p95": 0.0}
    mean_latency = sum(latencies) / len(latencies)
    p95_latency = _percentile(latencies, 95.0)
    return {
        "mean": mean_latency if _is_finite(mean_latency) else None,
        "p95": p95_latency if _is_finite(p95_latency) else None,
    }


def _added_tokens(rows: List[Dict[str, Any]]) -> int:
    return sum(_usage_tokens(r, "input_tokens") + _usage_tokens(r, "output_tokens") for r in rows)


def _summarize_feature_ab(rows: List[Dict[str, Any]], feature: str, manifest_by_call_id: Dict[str, Any]) -> Dict[str, Any]:
    n = len(rows)
    result: Dict[str, Any] = {
        "n": n,
        "n_unmatched": 0,
        "agreement": 0.0,
        "jev_added_latency_ms": {"mean": 0.0, "p95": 0.0},
        "jev_added_tokens": 0,
        "heuristic_added_latency_ms": 0,
        "heuristic_added_tokens": 0,
    }
    if feature == "routing":
        result["jev_accuracy"] = 0.0
        result["heuristic_accuracy"] = 0.0
    else:
        result["accuracy"] = _NO_GROUND_TRUTH
    if n == 0:
        return result

    result["agreement"] = sum(1 for r in rows if r.get("agree") is True) / n
    result["jev_added_latency_ms"] = _added_latency_ms(rows)
    result["jev_added_tokens"] = _added_tokens(rows)

    n_unmatched = 0
    if feature == "routing":
        n_ground_truth = 0
        jev_correct = 0
        heuristic_correct = 0
        for r in rows:
            call_id = r.get("call_id")
            if not isinstance(call_id, str) or call_id not in manifest_by_call_id:
                n_unmatched += 1
                continue
            workflow_type = manifest_by_call_id[call_id]
            if workflow_type is None:
                continue
            n_ground_truth += 1
            if _routing_jev_choice(r) == workflow_type:
                jev_correct += 1
            if _routing_heuristic_choice(r) == workflow_type:
                heuristic_correct += 1
        result["jev_accuracy"] = (jev_correct / n_ground_truth) if n_ground_truth else 0.0
        result["heuristic_accuracy"] = (heuristic_correct / n_ground_truth) if n_ground_truth else 0.0
    else:
        for r in rows:
            call_id = r.get("call_id")
            if not isinstance(call_id, str) or call_id not in manifest_by_call_id:
                n_unmatched += 1
    result["n_unmatched"] = n_unmatched
    return result


def aggregate_ab(events_rows: List[Dict[str, Any]], manifest_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure: joins DD-8 telemetry rows (already parsed, one dict per row) to
    replay_manifest.jsonl ground-truth rows by call_id. A telemetry row naming
    a feature other than routing/skill is ignored."""
    manifest_by_call_id = _manifest_by_call_id(manifest_rows)
    by_feature: Dict[str, List[Dict[str, Any]]] = {feature: [] for feature in FEATURES}
    for row in events_rows:
        feature = row.get("feature")
        if isinstance(feature, str) and feature in by_feature:
            by_feature[feature].append(row)
    return {
        "features": {
            feature: _summarize_feature_ab(rows, feature, manifest_by_call_id) for feature, rows in by_feature.items()
        }
    }


# ---------------------------------------------------------------------------
# CLI (impure: argparse + file I/O; only reachable from main()).
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Jev-on-vs-Jev-off A/B comparison: joins replay telemetry (events.jsonl) "
        "against replay_manifest.jsonl ground truth -- agreement for both features, accuracy "
        "for routing only, added latency, and added token cost."
    )
    parser.add_argument("--events", type=str, default=None, help="Path to events.jsonl (default: state_root()/jev/events.jsonl)")
    parser.add_argument("--manifest", type=str, required=True, help="Path to replay_manifest.jsonl (from craftflow_jev_replay.py)")
    parser.add_argument("--json", action="store_true", help="Print a JSON payload instead of a text table")
    return parser


def _read_lines(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError, MemoryError):
        return []


def _parse_jsonl_rows(lines: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _json_payload(events_path: Path, manifest_path: Path, summary: Dict[str, Any]) -> Dict[str, Any]:
    return {"events": str(events_path), "manifest": str(manifest_path), **summary}


def _fmt_latency(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _fmt_accuracy(feat: Dict[str, Any], key: str) -> str:
    if key not in feat:
        return _NO_GROUND_TRUTH
    value = feat[key]
    return _NO_GROUND_TRUTH if isinstance(value, str) else f"{value * 100:.2f}%"


def _format_ab_report_text(events_path: Path, manifest_path: Path, summary: Dict[str, Any]) -> str:
    lines = [
        "Jev A/B comparison report",
        f"  events: {events_path}",
        f"  manifest: {manifest_path}",
        "",
    ]
    for feature in FEATURES:
        feat = summary["features"][feature]
        lines.append(f"{feature}:")
        lines.append(f"  n: {feat['n']}")
        lines.append(f"  n_unmatched: {feat['n_unmatched']}")
        lines.append(f"  agreement: {feat['agreement'] * 100:.2f}%")
        if "jev_accuracy" in feat:
            lines.append(f"  jev_accuracy: {_fmt_accuracy(feat, 'jev_accuracy')}")
            lines.append(f"  heuristic_accuracy: {_fmt_accuracy(feat, 'heuristic_accuracy')}")
        else:
            lines.append(f"  accuracy: {feat['accuracy']}")
        jev_latency = feat["jev_added_latency_ms"]
        lines.append(
            f"  jev_added_latency_ms: mean={_fmt_latency(jev_latency['mean'])} p95={_fmt_latency(jev_latency['p95'])}"
        )
        lines.append(f"  jev_added_tokens: {feat['jev_added_tokens']}")
        lines.append(f"  heuristic_added_latency_ms: {feat['heuristic_added_latency_ms']}")
        lines.append(f"  heuristic_added_tokens: {feat['heuristic_added_tokens']}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    events_path = Path(args.events) if args.events else (state_root() / "jev" / "events.jsonl")
    manifest_path = Path(args.manifest)
    events_rows = _parse_jsonl_rows(_read_lines(events_path))
    manifest_rows = _parse_jsonl_rows(_read_lines(manifest_path))
    summary = aggregate_ab(events_rows, manifest_rows)
    if args.json:
        print(json.dumps(_json_payload(events_path, manifest_path, summary), indent=2, ensure_ascii=True, allow_nan=False))
    else:
        sys.stdout.write(_format_ab_report_text(events_path, manifest_path, summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
