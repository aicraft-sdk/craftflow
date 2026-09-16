#!/usr/bin/env python3
"""
craftflow_workflow_metrics.py

Read-only metrics extraction over .craftflow/state/workflows/*.events.jsonl.
Computes, per workflow: wall-clock duration, time spent per phase, quality/
rework signals (clean-pass vs issues-found decision counts, remediation
count), agent invocation counts, and stop_failure counts. Prints a
per-workflow table plus aggregate stats across all workflows; --format json
emits the same data as structured JSON instead.

Complements craftflow_latency_audit.py (which reads telemetry already
persisted into workflow *.json artifacts, often absent for older or already
cleaned-up workflows) by deriving the same kind of numbers directly from the
append-only event log, which survives independently of artifact lifecycle.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def resolve_project_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fall back to the plugin's own local state (matches craftflow_latency_audit.py).
        return Path(__file__).resolve().parents[3]


ROOT = resolve_project_root()
RUNTIME_WORKFLOWS = ROOT / ".craftflow" / "state" / "workflows"

CLEAN_DECISIONS = {"CLEAN", "APPROVE", "PASS", "PASS_INDEPENDENTLY_VERIFIED", "COMPLETE"}
ISSUES_DECISIONS = {"CHANGES_REQUESTED", "ISSUES_FOUND", "REFUTED", "FAIL"}


def parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def load_events(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    parse_errors = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            parse_errors += 1
    return events, parse_errors


def normalize_agent(name: str | None) -> str:
    if not name:
        return "unknown"
    return name.removeprefix("craftflow:")


def compute_workflow_metrics(wf_id: str, events: list[dict[str, Any]], parse_errors: int) -> dict[str, Any]:
    start_ts = None
    end_ts = None
    agent_invocations: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    remediation_count = 0
    stop_failure_count = 0
    workflow_type = "unknown"

    # phase timing: pair agent_started -> agent_completed by (phase, agent), FIFO per key
    pending: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    phase_seconds: Counter[str] = Counter()

    for evt in events:
        ts = parse_ts(evt.get("ts", ""))
        event = evt.get("event")
        phase = evt.get("phase") or "unknown"
        agent = normalize_agent(evt.get("agent"))
        decision = (evt.get("decision") or "").strip().upper()

        if event == "workflow_started":
            if start_ts is None or (ts and ts < start_ts):
                start_ts = ts
            if evt.get("phase"):
                workflow_type = evt["phase"]
        elif event in ("workflow_completed", "memory_finalized"):
            if ts and (end_ts is None or ts > end_ts):
                end_ts = ts

        if event == "agent_started":
            agent_invocations[agent] += 1
            if ts:
                pending[(phase, agent)].append(ts)
        elif event == "agent_completed":
            key = (phase, agent)
            if ts and pending.get(key):
                started = pending[key].pop(0)
                phase_seconds[phase] += (ts - started).total_seconds()

        if event == "remediation_created":
            remediation_count += 1
        if event == "stop_failure":
            stop_failure_count += 1

        if decision:
            decision_counts[decision] += 1

    clean_pass_count = sum(n for d, n in decision_counts.items() if d in CLEAN_DECISIONS)
    issues_found_count = sum(n for d, n in decision_counts.items() if d in ISSUES_DECISIONS)
    total_signals = clean_pass_count + issues_found_count
    rework_rate = (issues_found_count / total_signals) if total_signals else None

    duration_seconds = None
    if start_ts and end_ts and end_ts >= start_ts:
        duration_seconds = (end_ts - start_ts).total_seconds()

    return {
        "workflow_uuid": wf_id,
        "workflow_type": workflow_type,
        "start_ts": start_ts.isoformat() if start_ts else None,
        "end_ts": end_ts.isoformat() if end_ts else None,
        "duration_seconds": duration_seconds,
        "event_count": len(events),
        "parse_errors": parse_errors,
        "agent_invocations": dict(agent_invocations),
        "agent_invocation_total": sum(agent_invocations.values()),
        "phase_seconds": dict(phase_seconds),
        "clean_pass_count": clean_pass_count,
        "issues_found_count": issues_found_count,
        "rework_rate": rework_rate,
        "remediation_count": remediation_count,
        "stop_failure_count": stop_failure_count,
    }


def discover_workflows(workflows_dir: Path) -> list[Path]:
    if not workflows_dir.exists():
        return []
    return sorted(workflows_dir.glob("*.events.jsonl"))


def compute_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [r["duration_seconds"] for r in rows if r["duration_seconds"] is not None]
    rework_rates = [r["rework_rate"] for r in rows if r["rework_rate"] is not None]
    invocation_totals = [r["agent_invocation_total"] for r in rows]

    agent_totals: Counter[str] = Counter()
    phase_totals: Counter[str] = Counter()
    for row in rows:
        agent_totals.update(row["agent_invocations"])
        phase_totals.update(row["phase_seconds"])

    def summary(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"count": 0}
        sorted_vals = sorted(values)
        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "mean": statistics.mean(values),
            "median": statistics.median(values),
            "p90": sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * 0.9))],
        }

    return {
        "workflow_count": len(rows),
        "total_stop_failures": sum(r["stop_failure_count"] for r in rows),
        "total_remediations": sum(r["remediation_count"] for r in rows),
        "duration_seconds": summary(durations),
        "rework_rate": summary(rework_rates),
        "agent_invocations_per_workflow": summary([float(v) for v in invocation_totals]),
        "agent_invocation_totals": dict(agent_totals.most_common()),
        "phase_seconds_totals": dict(phase_totals.most_common()),
    }


def fmt_seconds(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:.1f}s"


def print_table(rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> None:
    print("craftflow_workflow_metrics")
    print(f"workflows={len(rows)}")
    print()
    for row in rows:
        rework = "unknown" if row["rework_rate"] is None else f"{row['rework_rate']:.0%}"
        print(f"- {row['workflow_uuid']}")
        print(f"  type={row['workflow_type']} duration={fmt_seconds(row['duration_seconds'])} events={row['event_count']}")
        print(
            f"  agents={row['agent_invocation_total']} "
            f"clean_pass={row['clean_pass_count']} issues_found={row['issues_found_count']} "
            f"rework_rate={rework} remediations={row['remediation_count']} "
            f"stop_failures={row['stop_failure_count']}"
        )
        if row["phase_seconds"]:
            phase_str = ", ".join(f"{p}={fmt_seconds(s)}" for p, s in row["phase_seconds"].items())
            print(f"  phase_seconds: {phase_str}")

    print()
    print("== Aggregate ==")
    print(f"workflow_count={aggregate['workflow_count']}")
    print(f"total_stop_failures={aggregate['total_stop_failures']}")
    print(f"total_remediations={aggregate['total_remediations']}")
    d = aggregate["duration_seconds"]
    if d["count"]:
        print(
            f"duration_seconds: mean={d['mean']:.1f} median={d['median']:.1f} "
            f"p90={d['p90']:.1f} min={d['min']:.1f} max={d['max']:.1f} (n={d['count']})"
        )
    r = aggregate["rework_rate"]
    if r["count"]:
        print(f"rework_rate: mean={r['mean']:.0%} median={r['median']:.0%} (n={r['count']})")
    a = aggregate["agent_invocations_per_workflow"]
    if a["count"]:
        print(f"agent_invocations_per_workflow: mean={a['mean']:.1f} median={a['median']:.1f}")
    print(f"agent_invocation_totals: {aggregate['agent_invocation_totals']}")
    print(f"phase_seconds_totals: {aggregate['phase_seconds_totals']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract time/quality/cost metrics from CRAFTFLOW workflow event logs.")
    parser.add_argument("--workflows-dir", type=Path, default=RUNTIME_WORKFLOWS, help="Directory containing *.events.jsonl files")
    parser.add_argument("--format", choices=["table", "json", "csv"], default="table")
    parser.add_argument("--out", type=Path, default=None, help="Write output to this path instead of stdout (json/csv only)")
    args = parser.parse_args()

    paths = discover_workflows(args.workflows_dir)
    rows = []
    for path in paths:
        wf_id = path.name.removesuffix(".events.jsonl")
        events, parse_errors = load_events(path)
        rows.append(compute_workflow_metrics(wf_id, events, parse_errors))

    aggregate = compute_aggregate(rows)

    if args.format == "table":
        print_table(rows, aggregate)
        return 0

    if args.format == "json":
        payload = json.dumps({"workflows": rows, "aggregate": aggregate}, indent=2)
        if args.out:
            args.out.write_text(payload + "\n", encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(payload)
        return 0

    # csv
    import csv
    import io

    fieldnames = [
        "workflow_uuid", "workflow_type", "start_ts", "end_ts", "duration_seconds",
        "event_count", "parse_errors", "agent_invocation_total", "clean_pass_count",
        "issues_found_count", "rework_rate", "remediation_count", "stop_failure_count",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    output = buf.getvalue()
    if args.out:
        args.out.write_text(output, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
