#!/usr/bin/env python3
"""Fail-closed release gate for the craftflow plugin.

Runs a fixed list of checks in order, each in its own process group with a wall-clock cap, and prints a last line that is
exactly `RELEASE GATE: OK` only when every step ran and passed. Any other outcome exits non-zero:

    RELEASE GATE: FAIL (<steps>)      a step failed, timed out, or was not run because the total budget was spent
    RELEASE GATE: PARTIAL (<...>)     a step was skipped (--allow-no-claude) or --only left steps out
    RELEASE GATE: NOTHING RAN         every selected step was skipped

The gate leaves no tracked or non-ignored file in the repository (hook logs under the ignored .craftflow/ may
appear); the publish job checks that with a clean-tree step.
Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

# <repo>/tools/craftflow-plugin/plugins/craftflow/scripts/craftflow_release_gate.py
ROOT = Path(__file__).resolve().parents[5]
PLUGIN = ROOT / "tools/craftflow-plugin/plugins/craftflow"
assert PLUGIN == Path(__file__).resolve().parents[1], "release gate is not at its documented path"

STEP_CAP_S = 600
TOTAL_BUDGET_S = 1200
TIMED_OUT_RC = -1002


@dataclass(frozen=True)
class Step:
    id: str
    argv: List[str]
    cwd: Path
    env_set: Dict[str, str] = field(default_factory=dict)
    env_drop: Sequence[str] = ()
    needs_claude: bool = False


def build_steps() -> List[Step]:
    py = sys.executable
    tests = PLUGIN / "tests/fixtures"
    inventory = [
        py, str(PLUGIN / "scripts/craftflow_rule_inventory.py"), "--diff",
        "--baseline", str(tests / "router-rule-inventory.baseline.json"),
        "--policy-map", str(ROOT / "tools/craftflow-plugin/docs/router-policy-map.md"),
        "--readmap", str(PLUGIN / "config/router-readmap.json"),
        "--readmap-baseline", str(tests / "router-readmap.baseline.json"),
        "--lock", str(tests / "router-baseline.lock.json"),
    ]
    return [
        Step("version_consistency", [py, str(PLUGIN / "scripts/craftflow_version_consistency_check.py")], ROOT),
        Step("hook_unit_tests", [py, str(PLUGIN / "scripts/craftflow_hook_unit_tests.py")], ROOT),
        # The suite exceeded 450 s on one process, so it runs as two round-robin shards (FIXTURE_SHARD=i/2).
        # CRAFTFLOW_PERF is dropped so opt-in timing tests never run inside the gate.
        Step("fixture_suite_a", ["bash", "tests/fixtures/run_fixture_suite.sh"], PLUGIN,
             env_set={"CI": "true", "FIXTURE_SHARD": "1/2"}, env_drop=("CRAFTFLOW_PERF",)),
        Step("fixture_suite_b", ["bash", "tests/fixtures/run_fixture_suite.sh"], PLUGIN,
             env_set={"CI": "true", "FIXTURE_SHARD": "2/2"}, env_drop=("CRAFTFLOW_PERF",)),
        Step("rule_inventory_diff", inventory, ROOT),
        Step("workflow_replay_check", [py, str(PLUGIN / "scripts/craftflow_workflow_replay_check.py")], ROOT),
        Step("graph_check", ["node", "scripts/gen-craftflow-graph.mjs", "--check"], ROOT),
        Step("event_contract", ["node", "scripts/verify-craftflow-event-contract.mjs"], ROOT),
        Step("decisions", ["node", "scripts/verify-decision-format.mjs"], ROOT),
        Step("docs_rot", [py, str(PLUGIN / "scripts/craftflow_docs_rot.py")], ROOT),
        Step("plugin_validate", ["claude", "plugin", "validate", "plugins/craftflow"],
             ROOT / "tools/craftflow-plugin", needs_claude=True),
    ]


def step_env(step: Step, environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if environ is None else environ)
    for name in step.env_drop:
        env.pop(name, None)
    env.update(step.env_set)
    return env


def kill_group(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # macOS answers EPERM, not ESRCH, when the group holds only unreaped zombies.
        pass
    p.wait()


def run_step(step: Step, cap: float) -> int:
    print("$ " + " ".join(step.argv), flush=True)
    try:
        # Own session: a timeout kills grandchildren too (node, bash and claude all fork).
        p = subprocess.Popen(step.argv, cwd=str(step.cwd), env=step_env(step), start_new_session=True)
    except OSError as exc:
        print(f"FAIL: cannot execute {step.argv[0]}: {exc}", flush=True)
        return 1
    try:
        rc = p.wait(timeout=cap)
    except subprocess.TimeoutExpired:
        kill_group(p)
        return TIMED_OUT_RC
    except BaseException:
        kill_group(p)  # Ctrl-C no longer reaches a child in its own session
        raise
    kill_group(p)  # a background member left by a passing step must not outlive it
    return rc


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="craftflow release gate runner")
    parser.add_argument("--list", action="store_true", help="print step ids and exit")
    parser.add_argument("--only", action="append", default=[], metavar="STEP", help="run only this step id (repeatable)")
    parser.add_argument("--allow-no-claude", action="store_true", help="skip plugin_validate when claude is not on PATH")
    parser.add_argument("--total-seconds", type=float, default=TOTAL_BUDGET_S, help="total wall-clock budget (default 1200)")
    parser.add_argument("--max-step-seconds", type=float, default=STEP_CAP_S, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def run_gate(steps: Sequence[Step], args: argparse.Namespace, runner: Callable[[Step, float], int] = run_step,
             which: Callable[[str], Optional[str]] = shutil.which, clock: Callable[[], float] = time.monotonic) -> int:
    started = clock()
    failed: List[str] = []
    skipped: List[str] = []
    not_run: List[str] = []
    ran = 0
    for s in steps:
        if args.only and s.id not in args.only:
            not_run.append(s.id)
            continue
        print(f"== {s.id}", flush=True)
        ran += 1
        remaining = args.total_seconds - (clock() - started)
        if remaining <= 0:
            print(f"FAIL {s.id}: total budget exhausted", flush=True)
            failed.append(s.id)
            continue
        if s.needs_claude and not which("claude"):
            if args.allow_no_claude:
                print(f"SKIPPED {s.id}: claude not on PATH (--allow-no-claude)", flush=True)
                skipped.append(s.id)
            else:
                print(f"FAIL {s.id}: claude not on PATH (use --allow-no-claude to skip)", flush=True)
                failed.append(s.id)
            continue
        cap = min(args.max_step_seconds, remaining)
        rc = runner(s, cap)
        if rc == TIMED_OUT_RC:
            print(f"FAIL {s.id}: timed out after {max(1, round(cap))} s", flush=True)
            failed.append(s.id)
        elif rc != 0:
            failed.append(s.id)
    print(f"gate wall time: {clock() - started:.1f} s", flush=True)
    if failed:
        print(f"RELEASE GATE: FAIL ({', '.join(failed)})")
        return 1
    if skipped and len(skipped) == ran:
        print(f"RELEASE GATE: NOTHING RAN (SKIPPED: {', '.join(skipped)})")
        return 1
    parts = list(skipped)
    if not_run:
        parts.append(f"not run: {', '.join(not_run)}")
    if parts:
        print(f"RELEASE GATE: PARTIAL ({'; '.join(parts)})")
        return 1
    print("RELEASE GATE: OK")
    return 0


def _raise_exit(signum, frame) -> None:
    # SIGTERM/SIGHUP no longer reach a step in its own session; SystemExit unwinds through run_step's cleanup.
    raise SystemExit(128 + signum)


def main(argv: Optional[Sequence[str]] = None, steps: Optional[Sequence[Step]] = None,
         runner: Callable[[Step, float], int] = run_step) -> int:
    args = parse_args(argv)
    table = list(steps) if steps is not None else build_steps()
    ids = [s.id for s in table]
    if args.list:
        print("\n".join(ids))
        return 0
    unknown = [s for s in args.only if s not in ids]
    if unknown:
        print(f"unknown step id(s): {', '.join(unknown)}; valid: {', '.join(ids)}", file=sys.stderr)
        return 2
    previous = {sig: signal.signal(sig, _raise_exit) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        return run_gate(table, args, runner)
    finally:
        for sig, handler in previous.items():
            if handler is not None:
                signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
