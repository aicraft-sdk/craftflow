#!/usr/bin/env python3
"""In-process timing driver for craftflow_context_nudge.current_context_tokens (SPEC-0016).

Default transcript = the largest *.jsonl under ~/.claude/projects/. Never prints transcript content.
Subprocess (bare-interpreter delta) timing is added in a later phase.

Run: python3 tests/live/context_nudge_timing.py [--runs N] [--transcript PATH]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import craftflow_context_nudge as cn  # noqa: E402

INPROC_ITERATIONS = 50


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
    print(json.dumps({
        "transcript_bytes": path.stat().st_size,
        "inproc_median_ms": round(statistics.median(samples), 3),
        "inproc_p95_ms": round(p95, 3),
        "tokens": last["tokens"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
