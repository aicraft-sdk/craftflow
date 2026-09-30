#!/usr/bin/env python3
"""Timing driver for the context nudge (SPEC-0016 / SPEC-0017 DD-16): in-process current_context_tokens,
the hook subprocess delta over a bare `python3 -c pass`, and A/B pairs against a pinned baseline script
(DD-17 env isolation, including the user-override env seam).

Default transcript = the largest *.jsonl under ~/.claude/projects/. Never prints transcript content.
Gates (exit 1 otherwise; gate_pass in the JSON):
  inproc_median_ms < 50 (kept verbatim); boundary_median_ms < 1000, boundary_max_ms < 2600
  (BOUNDARY_RUNS `--boundary` audit-mode runs with a real-size activeContext.md);
  G-REL   ab_nochange_absent_median_ms <= 8 and ab_nochange_override_median_ms <= 8 (new - baseline);
  G-NUDGE ab_nudge_median_ms <= 15, lookup_median_ms <= 10, lookup_max_ms <= 40;
  G-ABS   delta_median_ms < 50 vs bare python, evaluated only when load_avg_1m <= cpu_count;
          absolute_gate is "pass" | "fail" | "inconclusive_load"; only "fail" blocks (OD-1 option b).
The A/B needs --baseline-ref (the driver reads the shipped script with `git show`); without it the run
exits 1 with error baseline_ref_required.

Run: python3 tests/live/context_nudge_timing.py [--runs N] [--ab-pairs N] [--baseline-ref REF] [--transcript PATH]
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
import craftflow_context_nudge_compact as cc  # noqa: E402

INPROC_ITERATIONS = 50
GATE_MS = 50.0
BOUNDARY_RUNS = 10
BOUNDARY_MEDIAN_GATE_MS = 1000.0
BOUNDARY_MAX_GATE_MS = 2600.0  # 2 s digest timeout + interpreter/IO headroom
SCRIPT = SCRIPTS / "craftflow_context_nudge.py"
SEAM_ENV = "CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG"
BASELINE_REL_PATH = "tools/craftflow-plugin/plugins/craftflow/scripts/craftflow_context_nudge.py"
AB_PAIRS_DEFAULT = 30
G_REL_MS = 8.0
G_NUDGE_MS = 15.0
LOOKUP_MEDIAN_MS = 10.0
LOOKUP_MAX_MS = 40.0
ARTIFACT_BYTES = 90000
AB_VARIANTS = ("nochange_absent", "nochange_override", "nudge")


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


def _base_env(plugin, project, seam):
    """DD-17: explicit plugin/project roots, no session id, and the override seam pointing at `seam`."""
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update(CLAUDE_PLUGIN_ROOT=str(plugin), CLAUDE_PROJECT_DIR=str(project))
    env[SEAM_ENV] = str(seam)
    return env


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
        env = _base_env(tmp / "plugin", tmp / "project", tmp / "absent-user-override.json")
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


def _synthetic_active_context(target_bytes=300000):
    """Real-size activeContext.md (the repo's own is ~312 KB) so _narrative_digest() does real work."""
    headings = ("Current Focus", "Recent Changes", "Next Steps", "Decisions", "Learnings")
    parts = ["# Active Context\n"]
    for heading in headings:
        parts.append("\n## %s\n\n" % heading)
        size, n = 0, 0
        while size < target_bytes // len(headings):
            n += 1
            entry = ("%s entry %d: synthetic paragraph describing a decision, its rationale and "
                     "the verification evidence gathered for it during the workflow.\n\n" % (heading, n))
            parts.append(entry)
            size += len(entry)
    return "".join(parts)


def boundary_timing(path, runs):
    """Run `--boundary` in audit mode `runs` times (DD-17 env; digest runs against a real-size file)."""
    tmp = Path(tempfile.mkdtemp(prefix="cn-boundary-"))
    try:
        (tmp / "plugin" / "config").mkdir(parents=True)
        project = tmp / "project"
        state = project / ".craftflow" / "state"
        (state / "project").mkdir(parents=True)
        (state / "workflows").mkdir()
        (state / "context-nudge").mkdir()
        (tmp / "plugin" / "config" / "hook-mode.json").write_text(
            json.dumps({"contextNudge": "audit"}), encoding="utf-8")
        (tmp / "plugin" / "config" / "context-nudge.json").write_text(
            json.dumps(cn.DEFAULTS), encoding="utf-8")
        (state / "project" / "activeContext.md").write_text(_synthetic_active_context(), encoding="utf-8")
        (state / "workflows" / "wf-timing.json").write_text(json.dumps(
            {"workflow_uuid": "wf-timing", "workflow_type": "build", "phase_cursor": "P1",
             "phase_status": {"P1": "completed"}, "plan_file": None}), encoding="utf-8")
        (state / "context-nudge" / "timing.json").write_text(json.dumps(
            {"schema": 1, "session_id": "timing", "last_level": "none", "boundary_level": "none",
             "last_tokens": 1, "transcript_path": str(path), "updated_at": "2026-01-01T00:00:00Z"}),
            encoding="utf-8")
        env = _base_env(tmp / "plugin", project, tmp / "absent-user-override.json")
        cmd = [sys.executable, str(SCRIPT), "--boundary", "--wf", "wf-timing", "--phase", "P1",
               "--session-id", "timing", "--project-root", str(project)]
        samples = []
        for i in range(runs):
            ms, proc = _timed(cmd, env, str(tmp))
            out = json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
            if proc.returncode != 0 or out.get("outcome") == "error" or not out.get("checkpoint_path"):
                raise RuntimeError("boundary run %d misbehaved: rc=%s out=%r" % (i, proc.returncode, out))
            samples.append(ms)
        return {"boundary_median_ms": round(statistics.median(samples), 3),
                "boundary_max_ms": round(max(samples), 3)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def prepare_baseline(ref, tmp):
    """Write the shipped script at `ref` (via `git show`) next to the current helpers; return its path or None.

    Raises ValueError("baseline_not_old_code") when the blob is the working-tree script or already imports
    the compact module, so an A/B against the new code can never pass by comparing it with itself."""
    try:
        top = subprocess.run(["git", "-C", str(SCRIPTS), "rev-parse", "--show-toplevel"],
                             capture_output=True, timeout=30, check=True).stdout.decode("utf-8").strip()
        blob = subprocess.run(["git", "-C", top, "show", "%s:%s" % (ref, BASELINE_REL_PATH)],
                              capture_output=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if blob == SCRIPT.read_bytes() or b"craftflow_context_nudge_compact" in blob:
        raise ValueError("baseline_not_old_code")
    base = Path(tmp) / "base_scripts"
    base.mkdir(parents=True, exist_ok=True)
    (base / "craftflow_context_nudge.py").write_bytes(blob)
    for name in ("craftflow_hooklib.py", "craftflow_transcript_usage.py"):
        shutil.copyfile(str(SCRIPTS / name), str(base / name))
    return base / "craftflow_context_nudge.py"


def _seed_workflows(tmp, path):
    """5 live ~90 KB artifacts named after real mentions in the transcript tail (fallback: wf-timing-0..4).

    Returns (workflows_dir, transcript_path_to_use). Never prints transcript content."""
    with open(str(path), "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        handle.seek(max(0, size - cc.MENTION_TAIL_BYTES))
        data = handle.read()
    ids = cc.wf_mentions(data)
    transcript = Path(path)
    if len(ids) >= 5:
        ids = ids[-5:]
    else:
        ids = ["wf-timing-%d" % i for i in range(5)]
        transcript = Path(tmp) / "t-with-mentions.jsonl"
        shutil.copyfile(str(path), str(transcript))
        with open(str(transcript), "ab") as handle:
            handle.write(("\n" + "\n".join(json.dumps({"type": "user", "message": {"content": "on " + i}})
                                            for i in ids) + "\n").encode("utf-8"))
    wdir = Path(tmp) / "project" / ".craftflow" / "state" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    pad = [{"event": "phase_started", "note": "x" * 100} for _ in range(ARTIFACT_BYTES // 120)]
    for wf in ids:
        (wdir / (wf + ".json")).write_text(json.dumps(
            {"workflow_type": "build", "phase_cursor": "P1", "plan_file": "docs/plans/x.md",
             "status_history": pad}), encoding="utf-8")
    return wdir, transcript


def ab_timing(path, pairs, variant, base_script):
    """Median of (new - baseline) over `pairs` interleaved, order-alternated runs (same env and payload)."""
    tmp = Path(tempfile.mkdtemp(prefix="cn-ab-"))
    try:
        (tmp / "plugin" / "config").mkdir(parents=True)
        project = tmp / "project"
        project.mkdir()
        nudge = variant == "nudge"
        mode = "on" if nudge else "audit"
        (tmp / "plugin" / "config" / "hook-mode.json").write_text(
            json.dumps({"contextNudge": mode}), encoding="utf-8")
        cfg = ({"warnTokens": 1000, "criticalTokens": 100000000, "assumedWindow": 200000000}
               if nudge else dict(cn.DEFAULTS))
        (tmp / "plugin" / "config" / "context-nudge.json").write_text(json.dumps(cfg), encoding="utf-8")
        seam = tmp / "absent-user-override.json"
        if variant == "nochange_override":
            seam = tmp / "user-override.json"
            seam.write_text(json.dumps({"contextNudge": "audit", "warnTokens": 120000,
                                        "criticalTokens": 160000, "assumedWindow": 200000}), encoding="utf-8")
        transcript = Path(path)
        if nudge:
            _wdir, transcript = _seed_workflows(tmp, path)
        env = _base_env(tmp / "plugin", project, seam)
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "timing",
                              "transcript_path": str(transcript), "prompt": "x"}).encode("utf-8")
        state_file = project / ".craftflow" / "state" / "context-nudge" / "timing.json"
        scripts = {"base": [sys.executable, str(base_script)], "new": [sys.executable, str(SCRIPT)]}

        def run(which):
            if nudge and state_file.exists():
                state_file.unlink()  # outside the timed region: every nudge run starts from a fresh state
            ms, proc = _timed(scripts[which], env, str(tmp), payload)
            if proc.returncode != 0 or bool(proc.stdout) != nudge:
                raise RuntimeError("%s %s run misbehaved: rc=%s stdout_bytes=%d"
                                   % (variant, which, proc.returncode, len(proc.stdout)))
            return ms

        if not nudge:
            run("base")  # seed the state to the current level so measured runs are the no-change path
        base_ms, new_ms = [], []
        for i in range(pairs):
            order = ("base", "new") if i % 2 == 0 else ("new", "base")
            got = {which: run(which) for which in order}
            base_ms.append(got["base"])
            new_ms.append(got["new"])
        deltas = [n - b for n, b in zip(new_ms, base_ms)]
        q1, _q2, q3 = statistics.quantiles(deltas, n=4)
        return {"ab_%s_median_ms" % variant: round(statistics.median(deltas), 3),
                "ab_%s_delta_iqr_ms" % variant: round(q3 - q1, 3),
                "ab_%s_delta_min_ms" % variant: round(min(deltas), 3),
                "ab_%s_delta_max_ms" % variant: round(max(deltas), 3),
                "ab_%s_base_median_ms" % variant: round(statistics.median(base_ms), 3),
                "ab_%s_new_median_ms" % variant: round(statistics.median(new_ms), 3)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def lookup_timing(path, pairs):
    """In-process resolve_active_workflow over the seeded workflows dir (5 x ~90 KB artifacts)."""
    tmp = Path(tempfile.mkdtemp(prefix="cn-lookup-"))
    try:
        wdir, transcript = _seed_workflows(tmp, path)
        samples = []
        for _ in range(pairs):
            t0 = time.perf_counter()
            cc.resolve_active_workflow(str(wdir), str(transcript), "timing", str(tmp / "project"), time.time())
            samples.append((time.perf_counter() - t0) * 1000.0)
        return {"lookup_median_ms": round(statistics.median(samples), 3),
                "lookup_max_ms": round(max(samples), 3)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def absolute_gate(delta_median_ms, load_avg_1m, cpu_count):
    """G-ABS: pass/fail only on a non-overloaded host, else inconclusive_load."""
    if load_avg_1m > cpu_count:
        return "inconclusive_load"
    return "pass" if delta_median_ms < GATE_MS else "fail"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--ab-pairs", type=int, default=AB_PAIRS_DEFAULT)
    ap.add_argument("--baseline-ref", default=None)
    ap.add_argument("--transcript", default=None)
    args = ap.parse_args(argv)
    if args.runs < 1:
        print(json.dumps({"error": "invalid_runs", "detail": "--runs must be >= 1"}))
        return 2
    if args.ab_pairs < 3:
        print(json.dumps({"error": "invalid_ab_pairs", "detail": "--ab-pairs must be >= 3"}))
        return 2
    path = Path(args.transcript) if args.transcript else largest_transcript()
    if path is None or not path.is_file():
        print(json.dumps({"error": "no_transcript_found"}))
        return 2
    base_tmp = Path(tempfile.mkdtemp(prefix="cn-base-"))
    try:
        return _run(args, path, base_tmp)
    finally:
        shutil.rmtree(base_tmp, ignore_errors=True)


def _run(args, path, base_tmp):
    try:
        base_script = prepare_baseline(args.baseline_ref, base_tmp) if args.baseline_ref else None
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    if args.baseline_ref and base_script is None:
        print(json.dumps({"error": "baseline_ref_unresolved"}))
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
    result.update(runs=args.runs, ab_pairs=args.ab_pairs)
    result.update(subprocess_timing(path, args.runs))
    result.update(boundary_timing(path, BOUNDARY_RUNS))
    result["gate_ms"] = GATE_MS
    ab_keys = ["ab_%s_median_ms" % v for v in AB_VARIANTS] + ["lookup_median_ms", "lookup_max_ms"]
    if base_script is not None:
        result["load_avg_ab_start_1m"] = round(os.getloadavg()[0], 2)
        for variant in AB_VARIANTS:
            result.update(ab_timing(path, args.ab_pairs, variant, base_script))
        result.update(lookup_timing(path, args.ab_pairs))
    else:
        result.update({k: None for k in ab_keys})
        result["error"] = "baseline_ref_required"
    load = os.getloadavg()[0]
    cpus = os.cpu_count() or 1
    result.update(load_avg_1m=round(load, 2), cpu_count=cpus,
                  absolute_gate=absolute_gate(result["delta_median_ms"], load, cpus))
    ab_ok = (base_script is not None
             and result["ab_nochange_absent_median_ms"] <= G_REL_MS
             and result["ab_nochange_override_median_ms"] <= G_REL_MS
             and result["ab_nudge_median_ms"] <= G_NUDGE_MS
             and result["lookup_median_ms"] <= LOOKUP_MEDIAN_MS
             and result["lookup_max_ms"] <= LOOKUP_MAX_MS)
    result["gate_pass"] = bool(inproc < GATE_MS and ab_ok
                               and result["boundary_median_ms"] < BOUNDARY_MEDIAN_GATE_MS
                               and result["boundary_max_ms"] < BOUNDARY_MAX_GATE_MS
                               and result["absolute_gate"] != "fail")
    print(json.dumps(result))
    return 0 if result["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
