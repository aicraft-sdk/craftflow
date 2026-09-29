#!/usr/bin/env python3
"""Timing driver for the context nudge (SPEC-0016): in-process current_context_tokens plus the hook
subprocess delta over a bare `python3 -c pass` (DD-17 env isolation, no-change path).

Default transcript = the largest *.jsonl under ~/.claude/projects/. Never prints transcript content.
Gate: delta_median_ms < 50 and inproc_median_ms < 50 (exit 1 otherwise).

Run: python3 tests/live/context_nudge_timing.py [--runs N] [--transcript PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import craftflow_context_nudge as cn  # noqa: E402

INPROC_ITERATIONS = 50
GATE_MS = 50.0
SCRIPT = SCRIPTS / "craftflow_context_nudge.py"


def largest_transcript():
    root = Path.home() / ".claude" / "projects"
    best, best_size = None, -1
    if root.is_dir():
        for p in root.rglob("*.jsonl"):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > best_size:
                best, best_size = p, size
    return best


def _timed(cmd, env, cwd, data=None):
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, input=data, env=env, cwd=cwd, capture_output=True, timeout=30)
    return (time.perf_counter() - t0) * 1000.0, proc


def subprocess_timing(path, runs):
    """Interleave bare interpreter and hook subprocess runs in an isolated temp plugin/project."""
    tmp = Path(tempfile.mkdtemp(prefix="cn-timing-"))
    try:
        (tmp / "plugin" / "config").mkdir(parents=True)
        (tmp / "project").mkdir()
        (tmp / "plugin" / "config" / "hook-mode.json").write_text(
            json.dumps({"contextNudge": "audit"}), encoding="utf-8")
        (tmp / "plugin" / "config" / "context-nudge.json").write_text(
            json.dumps(cn.DEFAULTS), encoding="utf-8")
        env = dict(os.environ)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        env.update(CLAUDE_PLUGIN_ROOT=str(tmp / "plugin"), CLAUDE_PROJECT_DIR=str(tmp / "project"))
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "timing",
                              "transcript_path": str(path), "prompt": "x"}).encode("utf-8")
        hook_cmd = [sys.executable, str(SCRIPT)]
        _timed(hook_cmd, env, str(tmp), payload)  # seed state to the current level: measured runs are no-change
        bare, hook = [], []
        for _ in range(runs):
            bare.append(_timed([sys.executable, "-c", "pass"], env, str(tmp))[0])
            ms, proc = _timed(hook_cmd, env, str(tmp), payload)
            if proc.returncode != 0 or proc.stdout:
                raise RuntimeError("hook subprocess misbehaved: rc=%s stdout=%r" % (proc.returncode, proc.stdout[:80]))
            hook.append(ms)
        deltas = [h - b for h, b in zip(hook, bare)]
        return {
            "bare_median_ms": round(statistics.median(bare), 3),
            "hook_median_ms": round(statistics.median(hook), 3),
            "delta_median_ms": round(statistics.median(deltas), 3),
            "delta_max_ms": round(max(deltas), 3),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--transcript", default=None)
    args = ap.parse_args(argv)
    path = Path(args.transcript) if args.transcript else largest_transcript()
    if path is None or not path.is_file():
        print(json.dumps({"error": "no_transcript_found"}))
        return 2
    samples, last = [], None
    for _ in range(INPROC_ITERATIONS):
        t0 = time.perf_counter()
        last = cn.current_context_tokens(str(path))
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
    inproc = statistics.median(samples)
    result = {
        "transcript_bytes": path.stat().st_size,
        "inproc_median_ms": round(inproc, 3),
        "inproc_p95_ms": round(p95, 3),
        "tokens": last["tokens"],
    }
    result.update(subprocess_timing(path, args.runs))
    result["gate_ms"] = GATE_MS
    result["gate_pass"] = bool(inproc < GATE_MS and result["delta_median_ms"] < GATE_MS)
    print(json.dumps(result))
    return 0 if result["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
