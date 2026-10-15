#!/usr/bin/env python3
"""Loop-cap gate check at the points where today's router prose already decides (ADR-0073 BC-6, DD-13, P6).

    craftflow_gate_check.py --gate {remediation_cycles_max|doubt_cycles_max|fix_verify_cycles_max|plan_review_passes_max}
        --point {pre_create|post_verdict} --wf ID --project-root DIR [--cycle N] [--pass N] [--verdict PASS|FINDINGS]
        [--policy-file F]

Valid combinations (anything else exits 2 with {"error": ...}):
  remediation_cycles_max  pre_create    reads circuit_breaker; `allow` is "broken is not true" (the count is reported only)
  doubt_cycles_max        post_verdict  --cycle N (the dispatched `Cycle:`); N >= max -> advance, never read from a counter
  fix_verify_cycles_max   post_verdict  --cycle N, same rule
  plan_review_passes_max  post_verdict  --pass N --verdict V; FINDINGS with N >= max -> human_checkpoint (cap-reached)

Prints exactly one sorted-key JSON line {gate, point, allow, action, count, max, reason, policy_status, ...} and exits 0.
`action` is proceed | advance | human_checkpoint. Any `human_checkpoint` whose reason is not `cap-reached` (policy-invalid,
artifact-unreadable, invalid-count) is a failed check for the router (`loop_cap_check_failed`, fail closed).

Read-only: it never increments a counter, never sets `broken` and never writes the artifact. Its one write is the
`gate_check` event appended to `{wf}.events.jsonl` under flock. `event_error` means only that this audit append failed: the decision
is still valid and exit stays 0, so it is best-effort and never a `loop_cap_check_failed` trigger.
Python 3.9 compatible; stdlib plus craftflow_policy.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from craftflow_policy import load_policy, policy_value

# Advisory F: one literal policy_value call per cap, selected by --gate; never policy_value(policy, args.gate).
CAPS = {
    "remediation_cycles_max": lambda policy: policy_value(policy, "remediation_cycles_max"),
    "doubt_cycles_max": lambda policy: policy_value(policy, "doubt_cycles_max"),
    "fix_verify_cycles_max": lambda policy: policy_value(policy, "fix_verify_cycles_max"),
    "plan_review_passes_max": lambda policy: policy_value(policy, "plan_review_passes_max"),
}
POINTS = {"remediation_cycles_max": "pre_create", "doubt_cycles_max": "post_verdict",
          "fix_verify_cycles_max": "post_verdict", "plan_review_passes_max": "post_verdict"}
INPUTS = {"remediation_cycles_max": set(), "doubt_cycles_max": {"cycle"}, "fix_verify_cycles_max": {"cycle"},
          "plan_review_passes_max": {"pass_n", "verdict"}}
LOOP_COUNTER = {"doubt_cycles_max": "doubt_verify", "fix_verify_cycles_max": "fix_verify"}
FLAGS = {"cycle": "--cycle", "pass_n": "--pass", "verdict": "--verdict"}
WF_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_MISSING = object()


class Usage(Exception):
    """A malformed invocation: exit 2."""


class Unusable(Exception):
    """The inputs cannot give a decision: human_checkpoint with `reason`."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # argparse would print usage to stderr; the contract is one JSON line on stdout
        raise Usage(message)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--gate", required=True, choices=sorted(CAPS))
    parser.add_argument("--point", required=True, choices=["pre_create", "post_verdict"])
    parser.add_argument("--wf", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--cycle")  # raw text: "0", "-1", "1.5", "true" must reach us as invalid-count, not as a usage error
    parser.add_argument("--pass", dest="pass_n")
    parser.add_argument("--verdict", choices=["PASS", "FINDINGS"])
    parser.add_argument("--policy-file")
    args = parser.parse_args(argv)
    if args.point != POINTS[args.gate]:
        raise Usage(f"{args.gate} is evaluated at {POINTS[args.gate]}, not {args.point}")
    if not WF_ID.fullmatch(args.wf):
        raise Usage("--wf must be a workflow id (letters, digits, '.', '_', '-')")
    given = {name for name in FLAGS if getattr(args, name) is not None}
    for name in sorted(INPUTS[args.gate] - given):
        raise Usage(f"{FLAGS[name]} is required for {args.gate}")
    for name in sorted(given - INPUTS[args.gate]):
        raise Usage(f"{FLAGS[name]} does not apply to {args.gate}")
    return args


def read_artifact(path: Path) -> Dict[str, Any]:
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("not a regular file")
        raw = path.read_bytes()
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError("artifact over the size limit")
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # any read or parse failure is the same fail-closed outcome
        raise Unusable("artifact-unreadable", f"{type(exc).__name__}: {exc}"[:120])
    if not isinstance(data, dict):
        raise Unusable("artifact-unreadable", "artifact is not a JSON object")
    return data


def dig(node: Any, path: List[str]) -> Any:
    """Value at `path`, `_MISSING` when a key is absent; a present non-object on the way is invalid."""
    for key in path:
        if not isinstance(node, dict):
            raise Unusable("invalid-count", "/".join(path) + " is under a non-object")
        if key not in node:
            return _MISSING
        node = node[key]
    return node


def counter(value: Any, name: str) -> int:
    if value is _MISSING:
        return 0  # a missing counter is 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Unusable("invalid-count", f"{name} must be a non-negative integer")
    return value


def positive(text: str, flag: str) -> int:
    if not re.fullmatch(r"[0-9]{1,9}", text) or int(text) < 1:  # bounded text: never an unbounded string in the output or the event
        raise Unusable("invalid-count", f"{flag} must be a positive integer")
    return int(text)


def evaluate(args: argparse.Namespace, artifact: Dict[str, Any], cap: int) -> Dict[str, Any]:
    """The decision for one gate at its evaluation point (BC-6). Pure: reads `artifact`, returns the gate-specific fields."""
    if args.gate == "remediation_cycles_max":
        count = counter(dig(artifact, ["circuit_breaker", "remfix_count"]), "circuit_breaker.remfix_count")
        broken = dig(artifact, ["circuit_breaker", "broken"])
        if broken is not _MISSING and not isinstance(broken, bool):
            raise Unusable("invalid-count", "circuit_breaker.broken must be a boolean")
        allow = broken is not True  # the count is observability only: broken=false with count >= max proceeds, as today
        return {"allow": allow, "action": "proceed" if allow else "human_checkpoint", "count": count,
                "reason": "not-broken" if allow else "cap-reached", "trips_on_next_increment": count + 1 >= cap}
    if args.gate in LOOP_COUNTER:
        reported = counter(dig(artifact, ["telemetry", "loop_counts", LOOP_COUNTER[args.gate]]), "telemetry.loop_counts")
        cycle = positive(args.cycle, "--cycle")  # never the counter: it is not reset per phase
        capped = cycle >= cap
        return {"allow": not capped, "action": "advance" if capped else "proceed", "count": cycle,
                "reason": "cap-reached" if capped else "below-cap", "counter": reported}
    reported = counter(dig(artifact, ["planning_review_runs"]), "planning_review_runs")
    pass_n = positive(args.pass_n, "--pass")  # the pass identity selects today's block; the counter is reported only
    capped = args.verdict == "FINDINGS" and pass_n >= cap
    reason = "cap-reached" if capped else ("verdict-pass" if args.verdict == "PASS" else "below-cap")
    return {"allow": not capped, "action": "human_checkpoint" if capped else "proceed", "count": pass_n, "reason": reason,
            "counter": reported, "runs_matches_pass": reported == pass_n}


def log_event(name: str, payload: Dict[str, Any], path: Path) -> None:
    """Append one line to the workflow's events.jsonl under flock. Raises OSError; never creates directories."""
    record = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "event": name, **payload}
    with open(path, "a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n")
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run(argv: List[str]) -> Tuple[int, str]:
    """(exit code, the single stdout line)."""
    try:
        args = parse_args(argv)
    except Usage as exc:
        return 2, json.dumps({"error": str(exc)}, sort_keys=True)
    policy, status, _errors = load_policy(Path(args.policy_file) if args.policy_file else None, log=False)
    cap = CAPS[args.gate](policy)
    out: Dict[str, Any] = {"gate": args.gate, "point": args.point, "allow": False, "action": "human_checkpoint", "count": None,
                           "max": cap, "reason": "", "policy_status": status}
    workflows = Path(args.project_root) / ".craftflow" / "state" / "workflows"
    try:
        if status == "invalid":
            raise Unusable("policy-invalid", "policy.json is invalid; gated decisions fail closed")
        out.update(evaluate(args, read_artifact(workflows / f"{args.wf}.json"), cap))
    except Unusable as exc:
        out["reason"] = exc.reason
    event = {"wf": args.wf, **{k: out[k] for k in ("gate", "point", "count", "max", "allow", "action", "reason", "policy_status")}}
    try:
        log_event("gate_check", event, workflows / f"{args.wf}.events.jsonl")
    except OSError as exc:
        out["event_error"] = f"{type(exc).__name__}: {exc}"[:120].encode("ascii", "backslashreplace").decode("ascii")
    return 0, json.dumps(out, sort_keys=True)


def main(argv: Optional[List[str]] = None) -> int:
    code, line = run(sys.argv[1:] if argv is None else argv)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
