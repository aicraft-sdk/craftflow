#!/usr/bin/env python3
"""Timing driver for the stop gate hook (SPEC-0018 / ADR-0055), load-aware like ADR-0054 DD-16.

Interleaves, per run: a bare `python3 -c pass`, the hook in mode `off` and the hook in mode `audit` (no Jev),
as subprocesses with the real Stop payload shape. Audit runs against a 1 MiB transcript, a 90 KB workflow
artifact and a git repo with 200 files. Never prints transcript content.

Gates:
  off_delta_median_ms   <= 10   (hook off minus bare python)
  audit_delta_median_ms <= 150  (hook audit, no Jev, minus bare python)
Both are evaluated only when the 1-minute load average is at most the CPU count; otherwise
`absolute_gate` is "inconclusive_load" (reported honestly, never converted to a pass). Only "fail" blocks:
gate_pass = correctness checks ok AND absolute_gate != "fail". A hook that misbehaves (non-zero exit,
unexpected stdout, no row in audit, a row/dir in off) exits 1 with an error.

Run: python3 tests/live/stop_gate_timing.py [--runs N]
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

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "craftflow_stop_gate.py"
WF = "wf-timing-0001"
OFF_GATE_MS = 10.0
AUDIT_GATE_MS = 150.0
TRANSCRIPT_BYTES = 1048576
ARTIFACT_BYTES = 90000
REPO_FILES = 200
SEAM_ENV = "CRAFTFLOW_STOP_GATE_USER_CONFIG"
STRIPPED_ENV = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR", "CURSOR_PLUGIN_ROOT")
LAST_MESSAGE = ("Phase P1 of craftflow workflow %s is done and checks pass. Shall I continue to Phase P2?" % WF)


def timed(cmd, env, cwd, data=None):
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, input=data, env=env, cwd=str(cwd), capture_output=True, timeout=30)
    return (time.perf_counter() - t0) * 1000.0, proc


def git(project, *args):
    subprocess.run(["git", "-C", str(project), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                   capture_output=True, check=True, timeout=60)


def make_transcript(path):
    """~1 MiB JSONL: padding records, then a genuine human line and the closing assistant message."""
    lines = []
    size = 0
    i = 0
    while size < TRANSCRIPT_BYTES:
        i += 1
        rec = json.dumps({"type": "assistant" if i % 2 else "user", "timestamp": "2026-10-01T08:00:00.000Z",
                          "message": {"role": "assistant" if i % 2 else "user",
                                      "content": [{"type": "text", "text": "padding record %d " % i + "x" * 400}]}})
        lines.append(rec)
        size += len(rec) + 1
    lines.append(json.dumps({"type": "user", "timestamp": "2026-10-01T08:10:00.000Z",
                             "message": {"role": "user", "content": "go on %s" % WF}}))
    lines.append(json.dumps({"type": "assistant", "timestamp": "2026-10-01T08:10:05.000Z",
                             "message": {"role": "assistant", "content": [{"type": "text", "text": LAST_MESSAGE}]}}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_project(tmp):
    project = tmp / "project"
    (project / ".craftflow" / "state" / "workflows").mkdir(parents=True)
    (project / "src").mkdir()
    for n in range(REPO_FILES):
        (project / "src" / ("f%03d.txt" % n)).write_text("file %d\n" % n, encoding="utf-8")
    (project / ".gitignore").write_text(".craftflow/\n", encoding="utf-8")
    git(project, "init", "-q")
    git(project, "add", "-A")
    git(project, "commit", "-q", "-m", "init")
    pad = [{"event": "phase_started", "note": "x" * 100} for _ in range(ARTIFACT_BYTES // 120)]
    (project / ".craftflow" / "state" / "workflows" / (WF + ".json")).write_text(json.dumps({
        "workflow_uuid": WF, "workflow_type": "build", "phase_cursor": "P2",
        "phase_status": {"P1": "completed", "P2": "pending"},
        "normalized_phases": [{"id": "P1", "title": "Parser"}, {"id": "P2", "title": "Formatter"}],
        "plan_file": "docs/plans/t.md", "pending_gate": None, "status_history": pad}), encoding="utf-8")
    return project


def hook_env(plugin, project, seam):
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    env.update(CLAUDE_PLUGIN_ROOT=str(plugin), CLAUDE_PROJECT_DIR=str(project))
    env[SEAM_ENV] = str(seam)
    return env


def absolute_gate(off_delta, audit_delta, load_1m, cpus):
    """pass/fail on a non-overloaded host, else inconclusive_load."""
    if load_1m > cpus:
        return "inconclusive_load"
    return "pass" if off_delta <= OFF_GATE_MS and audit_delta <= AUDIT_GATE_MS else "fail"


def measure(runs):
    tmp = Path(tempfile.mkdtemp(prefix="sg-timing-"))
    try:
        project = make_project(tmp)
        transcript = tmp / "t.jsonl"
        make_transcript(transcript)
        for name in ("plugin-off", "plugin-audit"):
            (tmp / name / "config").mkdir(parents=True)
        (tmp / "plugin-off" / "config" / "stop-gate.json").write_text(json.dumps({"mode": "off"}), encoding="utf-8")
        (tmp / "plugin-audit" / "config" / "stop-gate.json").write_text(json.dumps({"mode": "audit"}),
                                                                         encoding="utf-8")
        seam = tmp / "absent-user-config.json"
        env_off = hook_env(tmp / "plugin-off", project, seam)
        env_audit = hook_env(tmp / "plugin-audit", project, seam)
        payload = json.dumps({"hook_event_name": "Stop", "session_id": "timing", "transcript_path": str(transcript),
                              "cwd": str(project), "stop_hook_active": False,
                              "last_assistant_message": LAST_MESSAGE}).encode("utf-8")
        cmd = [sys.executable, str(SCRIPT)]
        timed(cmd, env_audit, tmp, payload)  # warm file caches and seed the session record
        rows_path = project / ".craftflow" / "state" / "stop-gate" / "events.jsonl"
        rows_before = len(rows_path.read_text(encoding="utf-8").splitlines())
        bare, off, audit = [], [], []
        for _ in range(runs):
            bare.append(timed([sys.executable, "-c", "pass"], env_off, tmp)[0])
            ms, proc = timed(cmd, env_off, tmp, payload)
            if proc.returncode != 0 or proc.stdout:
                raise RuntimeError("off run misbehaved: rc=%s stdout=%r" % (proc.returncode, proc.stdout[:80]))
            off.append(ms)
            ms, proc = timed(cmd, env_audit, tmp, payload)
            if proc.returncode != 0 or proc.stdout:
                raise RuntimeError("audit run misbehaved: rc=%s stdout=%r" % (proc.returncode, proc.stdout[:80]))
            audit.append(ms)
        rows_after = [json.loads(x) for x in rows_path.read_text(encoding="utf-8").splitlines()]
        if len(rows_after) - rows_before != runs:
            raise RuntimeError("audit rows added=%d expected=%d" % (len(rows_after) - rows_before, runs))
        last = rows_after[-1]
        if last.get("row_kind") != "stop" or last.get("binding_reason") is None or last.get("wf") != WF:
            raise RuntimeError("audit row not bound as expected: %r" % {
                k: last.get(k) for k in ("row_kind", "binding_reason", "wf", "settings_tags")})
        off_d = [h - b for h, b in zip(off, bare)]
        audit_d = [h - b for h, b in zip(audit, bare)]
        return {
            "transcript_bytes": transcript.stat().st_size,
            "bare_median_ms": round(statistics.median(bare), 3),
            "off_median_ms": round(statistics.median(off), 3),
            "audit_median_ms": round(statistics.median(audit), 3),
            "off_delta_median_ms": round(statistics.median(off_d), 3),
            "off_delta_max_ms": round(max(off_d), 3),
            "audit_delta_median_ms": round(statistics.median(audit_d), 3),
            "audit_delta_max_ms": round(max(audit_d), 3),
            "audit_hook_ms_median": round(statistics.median(r["hook_ms"] for r in rows_after[-runs:]), 1),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=20)
    args = ap.parse_args(argv)
    if args.runs < 3:
        print(json.dumps({"error": "invalid_runs", "detail": "--runs must be >= 3"}))
        return 2
    try:
        result = {"runs": args.runs}
        result.update(measure(args.runs))
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": "measurement_failed", "detail": str(exc)[:200]}))
        return 1
    load = os.getloadavg()[0]
    cpus = os.cpu_count() or 1
    result.update(off_gate_ms=OFF_GATE_MS, audit_gate_ms=AUDIT_GATE_MS, load_avg_1m=round(load, 2), cpu_count=cpus,
                  absolute_gate=absolute_gate(result["off_delta_median_ms"], result["audit_delta_median_ms"],
                                              load, cpus))
    result["gate_pass"] = result["absolute_gate"] != "fail"
    print(json.dumps(result))
    return 0 if result["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
