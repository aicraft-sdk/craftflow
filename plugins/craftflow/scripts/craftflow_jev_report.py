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
from craftflow_jev_report_lib import _manifest_by_call_id

FEATURES = ("routing", "skill", "remfix_scope")


# ---------------------------------------------------------------------------
# Pure aggregation (unit-testable directly -- no I/O).
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite(value: Any) -> bool:
    """math.isfinite() raises OverflowError for a plain JSON integer whose
    magnitude is too large to convert to a C double (>= ~1.8e308, i.e. a
    309+ digit int) -- unlike a JSON float literal like 1e400, which
    json.loads silently coerces to math.inf with no exception. Treat an
    out-of-float-range int exactly like NaN/Infinity: not finite."""
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


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
    if not _is_number(value) or not _is_finite(value):
        return 0
    return int(value)


def _sanitize_json_value(value: Any) -> Any:
    """Shared sanitization boundary for untrusted scalar fields (call_id/ts/
    answers.choice/heuristic_result.workflow) reused by BOTH the --json path
    (_json_payload) and the text path (_format_report_text) -- one guard
    feeding both, not two differently-behaving guards.

    - A raw NaN/Infinity float crashes `allow_nan=False`, and so does one
      nested inside a list/dict (e.g. {"call_id": [1, NaN]}). Coerce
      non-finite floats to None; also coerce any list/dict container to None
      outright rather than walking it recursively -- these fields are always
      plain strings from every legitimate producer, so an unexpected
      container shape is malformed data, same "validate expected shape, else
      None" idiom used for feature/usage/heuristic_result guards elsewhere in
      this file.
    - A string containing a lone/unpaired UTF-16 surrogate code point (e.g.
      "\\ud800") is valid per json.loads (JSON doesn't validate UTF-16
      well-formedness) but cannot round-trip through UTF-8 encoding --
      json.dumps(ensure_ascii=True) escapes it safely, but sys.stdout.write()
      in text mode crashes with UnicodeEncodeError. Coerce any string that
      cannot UTF-8-encode to None so both output paths get identical,
      already-sanitized data. str.encode() can also raise MemoryError when
      the encoded buffer allocation fails (UTF-8 worst case is 4x the
      codepoint count in bytes) -- the same exception class already caught
      at the file-read boundary in this file (_read_lines); treat it the
      same way here.

    Non-float, non-container, round-trippable values (str, None, ...) pass
    through unchanged, same treatment as latency_ms/usage tokens above."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (dict, list)):
        return None
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except (UnicodeEncodeError, MemoryError):
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


def _summarize_feature(
    rows: List[Dict[str, Any]],
    feature: str,
    ground_truth_by_call_id: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
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
        # NaN (self-inequality), +/-Infinity, out-of-float-range ints, and
        # negative values are rejected rather than silently averaged in --
        # they poison mean/p95 otherwise.
        if _is_finite(value) and value >= 0:
            valid_latencies.append(value)
        else:
            invalid_latency += 1
    result["invalid_latency"] = invalid_latency
    latencies = sorted(valid_latencies)
    if latencies:
        # Each value in `latencies` passed the per-row _is_finite() guard
        # above, but the aggregate sum()/percentile computed from them is
        # not automatically finite -- e.g. two rows each with
        # latency_ms=1.5e308 are individually finite yet sum to Python
        # float inf. Re-validate at this aggregation boundary rather than
        # trying to prevent the sum from overflowing (not practical: any
        # sufficiently large but individually-valid inputs can sum to inf).
        mean_latency_ms = sum(latencies) / len(latencies)
        p95_latency_ms = _percentile(latencies, 95.0)
        result["mean_latency_ms"] = mean_latency_ms if _is_finite(mean_latency_ms) else None
        result["p95_latency_ms"] = p95_latency_ms if _is_finite(p95_latency_ms) else None
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
    if feature == "routing" and ground_truth_by_call_id:
        n_ground_truth = 0
        jev_correct = 0
        heuristic_correct = 0
        for r in rows:
            call_id = r.get("call_id")
            if not isinstance(call_id, str) or call_id not in ground_truth_by_call_id:
                continue
            workflow_type = ground_truth_by_call_id[call_id]
            if workflow_type is None:
                # Matched to a manifest row with no ground truth -- excluded
                # from n_ground_truth, same null-handling as
                # craftflow_jev_ab_report.py's _summarize_feature_ab().
                continue
            n_ground_truth += 1
            if _jev_choice(r) == workflow_type:
                jev_correct += 1
            if _heuristic_choice(r, feature) == workflow_type:
                heuristic_correct += 1
        if n_ground_truth:
            # Only add these 4 keys when at least one row resolved ground
            # truth. This single condition uniformly covers every "no
            # accuracy signal" case: no manifest at all (the outer `and`
            # short-circuits since ground_truth_by_call_id is None), an
            # empty/unparseable manifest (_manifest_by_call_id returns {},
            # falsy), and a non-empty manifest whose rows never match this
            # run's routing call_ids (n_ground_truth stays 0 here) -- all
            # three leave this summary byte-identical to the pre-dual-gate
            # shape.
            jev_accuracy = jev_correct / n_ground_truth
            heuristic_accuracy = heuristic_correct / n_ground_truth
            result["n_ground_truth"] = n_ground_truth
            result["jev_accuracy"] = jev_accuracy
            result["heuristic_accuracy"] = heuristic_accuracy
            result["accuracy_improvement"] = jev_accuracy - heuristic_accuracy
    return result


def aggregate(
    lines: List[str], manifest_rows: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Pure: one DD-8 telemetry row per JSON line. A line that fails to parse,
    is not a JSON object, or names a feature other than routing/skill/
    remfix_scope is skipped and counted in "malformed" rather than failing
    the report. manifest_rows is an optional list of already-parsed
    replay_manifest.jsonl rows (dicts); None (the default) means no
    manifest was supplied and preserves the exact pre-dual-gate output
    shape for every feature."""
    malformed = 0
    by_feature: Dict[str, List[Dict[str, Any]]] = {feature: [] for feature in FEATURES}
    for raw_line in lines:
        try:
            line = raw_line.strip()
            if not line:
                continue
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
    ground_truth_by_call_id = (
        _manifest_by_call_id(manifest_rows) if manifest_rows is not None else None
    )
    return {
        "malformed": malformed,
        "features": {
            feature: _summarize_feature(rows, feature, ground_truth_by_call_id)
            for feature, rows in by_feature.items()
        },
    }


def verdict(
    feature_summary: Dict[str, Any],
    min_n: int,
    min_agreement: float,
    min_accuracy_margin: float = 0.10,
) -> Tuple[str, str]:
    """DD-11 promotion rule for ONE feature, extended with a second,
    independent accuracy-improvement-over-heuristic gate: PROMOTE fires if
    EITHER the agreement gate OR the accuracy gate passes. The accuracy
    gate only ever has a signal when aggregate() was given a manifest AND
    the feature is "routing" AND at least one row resolved ground truth --
    see _summarize_feature()'s `if n_ground_truth:` guard. Every other case
    (no manifest, skill/remfix_scope, or a manifest that matched nothing)
    leaves feature_summary without an "accuracy_improvement" key, so this
    function's behavior and reason-string wording for those cases are
    byte-identical to the pre-dual-gate implementation below."""
    n = feature_summary.get("n", 0) if isinstance(feature_summary, dict) else 0
    if not n:
        return "HOLD", "no data"

    agreement = feature_summary.get("agreement", 0.0)
    agreement_pass = n >= min_n and agreement >= min_agreement

    if "accuracy_improvement" not in feature_summary:
        # No accuracy signal available -- identical to pre-dual-gate behavior.
        if n < min_n:
            return "HOLD", f"n={n} < {min_n}"
        if agreement < min_agreement:
            return "HOLD", f"agreement={agreement:.2f} < {min_agreement:.2f}"
        return "PROMOTE", f"n={n}, agreement={agreement:.2f}"

    # Accuracy signal available (routing + manifest supplied + >=1 row
    # resolved ground truth) -- name both gates' values in the reason.
    n_ground_truth = feature_summary.get("n_ground_truth", 0)
    accuracy_improvement = feature_summary.get("accuracy_improvement")
    accuracy_pass = (
        n_ground_truth >= min_n
        and accuracy_improvement is not None
        and accuracy_improvement >= min_accuracy_margin
    )

    agreement_clause = (
        f"agreement={agreement:.2f}>={min_agreement:.2f}"
        if agreement >= min_agreement
        else f"agreement={agreement:.2f}<{min_agreement:.2f}"
    )
    agreement_clause += f", n={n}"

    if accuracy_improvement is None:
        # Not reachable given _summarize_feature's contract (accuracy_improvement
        # is only ever set to a real float when n_ground_truth > 0, which is the
        # only way "accuracy_improvement" ends up in feature_summary at all) --
        # kept as a defensive fallback consistent with this file's existing
        # None-sentinel handling for mean_latency_ms/p95_latency_ms.
        accuracy_clause = "accuracy: not available (no ground-truthed rows matched)"
    elif n_ground_truth < min_n:
        accuracy_clause = f"accuracy: n_ground_truth={n_ground_truth}<{min_n} (insufficient sample)"
    else:
        jev_accuracy = feature_summary.get("jev_accuracy", 0.0)
        heuristic_accuracy = feature_summary.get("heuristic_accuracy", 0.0)
        op = ">=" if accuracy_improvement >= min_accuracy_margin else "<"
        accuracy_clause = (
            f"accuracy: jev={jev_accuracy:.2f} heuristic={heuristic_accuracy:.2f} "
            f"{accuracy_improvement:+.2f}{op}{min_accuracy_margin:.2f}, n_ground_truth={n_ground_truth}"
        )

    reason = f"{agreement_clause}; {accuracy_clause}"
    result = "PROMOTE" if (agreement_pass or accuracy_pass) else "HOLD"
    return result, reason


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
    parser.add_argument("--min-agreement-remfix-scope", type=float, default=0.80)
    parser.add_argument(
        "--manifest",
        type=str,
        default=None,
        help="Optional path to replay_manifest.jsonl (from craftflow_jev_replay.py) for routing ground-truth accuracy",
    )
    parser.add_argument("--min-accuracy-margin", type=float, default=0.10)
    parser.add_argument("--json", action="store_true", help="Print a JSON payload instead of a text table")
    return parser


def _read_lines(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError, MemoryError):
        return []


def _parse_jsonl_rows(lines: List[str]) -> List[Dict[str, Any]]:
    """Local copy of craftflow_jev_ab_report.py's _parse_jsonl_rows -- a
    small, generic JSONL-parsing helper (not one of the manifest-JOIN
    helpers reused via craftflow_jev_report_lib), kept local to avoid a
    second cross-import between the two sibling scripts."""
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


def _min_agreement_for(feature: str, args: argparse.Namespace) -> float:
    if feature == "routing":
        return args.min_agreement_routing
    if feature == "remfix_scope":
        return args.min_agreement_remfix_scope
    return args.min_agreement_skill


def _json_payload(events_path: Path, summary: Dict[str, Any], verdicts: Dict[str, Tuple[str, str]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"events": str(events_path), "malformed": summary["malformed"], "features": {}}
    for feature in FEATURES:
        feat = dict(summary["features"][feature])
        result, reason = verdicts[feature]
        feat["verdict"] = result
        feat["reason"] = reason
        payload["features"][feature] = feat
    return payload


def _fmt_latency(value: Optional[float]) -> str:
    """mean_latency_ms/p95_latency_ms are None when the aggregate sum/
    percentile overflowed to inf despite every individual latency_ms being
    finite (see _summarize_feature) -- format that sentinel as "n/a" rather
    than crashing ":.1f" formatting on None."""
    return "n/a" if value is None else f"{value:.1f}"


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
        if "n_ground_truth" in feat:
            lines.append(f"  n_ground_truth: {feat['n_ground_truth']}")
            lines.append(f"  jev_accuracy: {feat['jev_accuracy'] * 100:.2f}%")
            lines.append(f"  heuristic_accuracy: {feat['heuristic_accuracy'] * 100:.2f}%")
            lines.append(f"  accuracy_improvement: {feat['accuracy_improvement'] * 100:+.2f}pp")
        lines.append(f"  mean_latency_ms: {_fmt_latency(feat['mean_latency_ms'])}")
        lines.append(f"  p95_latency_ms: {_fmt_latency(feat['p95_latency_ms'])}")
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
    manifest_rows: Optional[List[Dict[str, Any]]] = None
    if args.manifest is not None:
        manifest_path = Path(args.manifest)
        # Explicitly user-supplied path that doesn't exist must fail loudly,
        # not silently degrade to "no ground truth" -- same rationale/pattern
        # as craftflow_jev_ab_report.py's own --manifest existence check
        # (reused, not reinvented).
        if not manifest_path.is_file():
            sys.stderr.write(f"error: --manifest path does not exist: {manifest_path}\n")
            return 1
        manifest_rows = _parse_jsonl_rows(_read_lines(manifest_path))
    summary = aggregate(_read_lines(events_path), manifest_rows)
    verdicts: Dict[str, Tuple[str, str]] = {
        feature: verdict(
            summary["features"][feature],
            args.min_n,
            _min_agreement_for(feature, args),
            args.min_accuracy_margin,
        )
        for feature in FEATURES
    }
    if args.json:
        print(json.dumps(_json_payload(events_path, summary, verdicts), indent=2, ensure_ascii=True, allow_nan=False))
    else:
        sys.stdout.write(_format_report_text(events_path, summary, verdicts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
