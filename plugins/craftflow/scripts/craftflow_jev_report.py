#!/usr/bin/env python3
"""Operator CLI -- summarize .craftflow/state/jev/events.jsonl (DD-8 telemetry
rows written by craftflow_jev_prompt_hint.py) into per-feature agreement,
latency, and token stats, plus a DD-11 PROMOTE/HOLD verdict.

Reads the events file by path only -- never stdin (DD-12b). Import-cheap: no
file reads, env lookups, network, or argparse parsing at import time; all of
that happens inside main(), which only runs under `if __name__ == "__main__"`.

Run: python3 scripts/craftflow_jev_report.py --events <path> [--json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from craftflow_hooklib import state_root

FEATURES = ("routing", "skill")


# ---------------------------------------------------------------------------
# Pure aggregation (unit-testable directly -- no I/O).
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _percentile(sorted_values: List[float], pct: float) -> float:
    """Linear-interpolation percentile over an already-sorted list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * (pct / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(sorted_values[int(k)])
    d0 = sorted_values[int(lo)] * (hi - k)
    d1 = sorted_values[int(hi)] * (k - lo)
    return float(d0 + d1)


def _usage_tokens(row: Dict[str, Any], key: str) -> int:
    usage = row.get("usage")
    if not isinstance(usage, dict):
        return 0
    value = usage.get(key)
    if not _is_number(value) or not math.isfinite(value):
        return 0
    return int(value)


def _sanitize_json_value(value: Any) -> Any:
    """DD-8/--json guard: a raw NaN/Infinity float crashes `allow_nan=False`,
    and so does one nested inside a list/dict (e.g. {"call_id": [1, NaN]}).
    Coerce non-finite floats to None; also coerce any list/dict container to
    None outright rather than walking it recursively -- call_id/ts/
    answers.choice/heuristic_result.workflow are always plain strings from
    every legitimate producer, so an unexpected container shape is malformed
    data, same "validate expected shape, else None" idiom used for feature/
    usage/heuristic_result guards elsewhere in this file. Non-float, non-
    container values (str, None, ...) pass through unchanged, same treatment
    as latency_ms/usage tokens above."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (dict, list)):
        return None
    return value


def _jev_choice(row: Dict[str, Any]) -> Optional[str]:
    answers = row.get("answers")
    return answers.get("choice") if isinstance(answers, dict) else None


def _heuristic_choice(row: Dict[str, Any], feature: str) -> Optional[str]:
    heuristic_result = row.get("heuristic_result")
    if feature == "routing":
        return heuristic_result.get("workflow") if isinstance(heuristic_result, dict) else None
    return heuristic_result if isinstance(heuristic_result, str) else None


def _summarize_feature(rows: List[Dict[str, Any]], feature: str) -> Dict[str, Any]:
    n = len(rows)
    result: Dict[str, Any] = {
        "n": n,
        "agreement": 0.0,
        "mean_latency_ms": 0.0,
        "p95_latency_ms": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "injected": 0,
        "cache_hits": 0,
        "disagreements": [],
        "invalid_latency": 0,
    }
    if feature == "routing":
        result["agree_risk"] = 0.0
    if n == 0:
        return result

    result["agreement"] = sum(1 for r in rows if r.get("agree") is True) / n
    valid_latencies: List[float] = []
    invalid_latency = 0
    for r in rows:
        value = r.get("latency_ms")
        if not _is_number(value):
            continue
        # NaN (self-inequality), +/-Infinity, and negative values are rejected
        # rather than silently averaged in -- they poison mean/p95 otherwise.
        if math.isfinite(value) and value >= 0:
            valid_latencies.append(value)
        else:
            invalid_latency += 1
    result["invalid_latency"] = invalid_latency
    latencies = sorted(valid_latencies)
    if latencies:
        result["mean_latency_ms"] = sum(latencies) / len(latencies)
        result["p95_latency_ms"] = _percentile(latencies, 95.0)
    result["input_tokens"] = sum(_usage_tokens(r, "input_tokens") for r in rows)
    result["output_tokens"] = sum(_usage_tokens(r, "output_tokens") for r in rows)
    result["injected"] = sum(1 for r in rows if r.get("injected") is True)
    result["cache_hits"] = sum(1 for r in rows if r.get("cache_hit") is True)
    result["disagreements"] = [
        {
            "call_id": _sanitize_json_value(r.get("call_id")),
            "ts": _sanitize_json_value(r.get("ts")),
            "jev": _sanitize_json_value(_jev_choice(r)),
            "heuristic": _sanitize_json_value(_heuristic_choice(r, feature)),
        }
        for r in rows
        if r.get("agree") is not True
    ]
    if feature == "routing":
        result["agree_risk"] = sum(1 for r in rows if r.get("agree_risk") is True) / n
    return result


def aggregate(lines: List[str]) -> Dict[str, Any]:
    """Pure: one DD-8 telemetry row per JSON line. A line that fails to parse,
    is not a JSON object, or names a feature other than routing/skill is
    skipped and counted in "malformed" rather than failing the report."""
    malformed = 0
    by_feature: Dict[str, List[Dict[str, Any]]] = {feature: [] for feature in FEATURES}
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            malformed += 1
            continue
        if not isinstance(row, dict):
            malformed += 1
            continue
        feature = row.get("feature")
        # `feature` must be a string before the `in` membership test below --
        # a list/dict value is unhashable and raises TypeError against a dict.
        if not isinstance(feature, str) or feature not in by_feature:
            malformed += 1
            continue
        by_feature[feature].append(row)
    return {
        "malformed": malformed,
        "features": {feature: _summarize_feature(rows, feature) for feature, rows in by_feature.items()},
    }


def verdict(feature_summary: Dict[str, Any], min_n: int, min_agreement: float) -> Tuple[str, str]:
    """DD-11 promotion rule for ONE feature. Caller supplies the feature's own
    min_agreement (0.80 routing / 0.60 skill); the skill feature's "plus a
    human read of the disagreement list" clause is not machine-checkable and
    is surfaced via the printed disagreement list instead."""
    n = feature_summary.get("n", 0) if isinstance(feature_summary, dict) else 0
    if not n:
        return "HOLD", "no data"
    agreement = feature_summary.get("agreement", 0.0)
    if n < min_n:
        return "HOLD", f"n={n} < {min_n}"
    if agreement < min_agreement:
        return "HOLD", f"agreement={agreement:.2f} < {min_agreement:.2f}"
    return "PROMOTE", f"n={n}, agreement={agreement:.2f}"


# ---------------------------------------------------------------------------
# CLI (impure: argparse + file I/O; only reachable from main()).
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize Jev telemetry (.craftflow/state/jev/events.jsonl): "
        "per-feature agreement, latency, tokens, and DD-11 PROMOTE/HOLD verdict."
    )
    parser.add_argument("--events", type=str, default=None, help="Path to events.jsonl (default: state_root()/jev/events.jsonl)")
    parser.add_argument("--min-n", type=int, default=100)
    parser.add_argument("--min-agreement-routing", type=float, default=0.80)
    parser.add_argument("--min-agreement-skill", type=float, default=0.60)
    parser.add_argument("--json", action="store_true", help="Print a JSON payload instead of a text table")
    return parser


def _read_lines(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def _min_agreement_for(feature: str, args: argparse.Namespace) -> float:
    return args.min_agreement_routing if feature == "routing" else args.min_agreement_skill


def _json_payload(events_path: Path, summary: Dict[str, Any], verdicts: Dict[str, Tuple[str, str]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"events": str(events_path), "malformed": summary["malformed"], "features": {}}
    for feature in FEATURES:
        feat = dict(summary["features"][feature])
        result, reason = verdicts[feature]
        feat["verdict"] = result
        feat["reason"] = reason
        payload["features"][feature] = feat
    return payload


def _format_report_text(events_path: Path, summary: Dict[str, Any], verdicts: Dict[str, Tuple[str, str]]) -> str:
    lines = [
        "Jev telemetry report",
        f"  events: {events_path}",
        f"  malformed lines skipped: {summary['malformed']}",
        "",
    ]
    for feature in FEATURES:
        feat = summary["features"][feature]
        result, reason = verdicts[feature]
        lines.append(f"{feature}:")
        lines.append(f"  n: {feat['n']}")
        lines.append(f"  agreement: {feat['agreement'] * 100:.2f}%")
        if "agree_risk" in feat:
            lines.append(f"  agree_risk: {feat['agree_risk'] * 100:.2f}%")
        lines.append(f"  mean_latency_ms: {feat['mean_latency_ms']:.1f}")
        lines.append(f"  p95_latency_ms: {feat['p95_latency_ms']:.1f}")
        lines.append(f"  tokens: input={feat['input_tokens']} output={feat['output_tokens']}")
        lines.append(f"  injected: {feat['injected']}")
        lines.append(f"  cache_hits: {feat['cache_hits']}")
        lines.append(f"  disagreements: {len(feat['disagreements'])}")
        for d in feat["disagreements"]:
            lines.append(f"    - call_id={d['call_id']} ts={d['ts']} jev={d['jev']} heuristic={d['heuristic']}")
        lines.append(f"  verdict: {result} ({reason})")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    events_path = Path(args.events) if args.events else (state_root() / "jev" / "events.jsonl")
    summary = aggregate(_read_lines(events_path))
    verdicts: Dict[str, Tuple[str, str]] = {
        feature: verdict(summary["features"][feature], args.min_n, _min_agreement_for(feature, args))
        for feature in FEATURES
    }
    if args.json:
        print(json.dumps(_json_payload(events_path, summary, verdicts), indent=2, ensure_ascii=True, allow_nan=False))
    else:
        sys.stdout.write(_format_report_text(events_path, summary, verdicts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
