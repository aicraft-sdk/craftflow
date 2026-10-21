#!/usr/bin/env python3
"""Class-A live proof driver for the router context cut, round 2 (plan P13, LV-1..LV-5).

  LV-1 load probe         `claude -p --plugin-dir <candidate> --debug-file f` logs "from --plugin-dir overrides installed version".
  LV-2 read-set parity    craftflow_router_readset.py: the baseline (--ref <lock sha> + the frozen read map) start_total is
                          214,055 and the candidate is <= enforced_caps.readset.BUILD. Prints the gap to the 60,000 goal.
  LV-3 EnterPlanMode      a haiku session told to call EnterPlanMode is denied (native_plan_mode_denied row in the hook log).
                          If the host does not offer the tool in -p mode: LIMITATION:enterplanmode_not_offered (HV-4 covers it).
                          The tool being called but NOT denied is a FAIL, never a limitation.
  LV-4 plan commit denied a haiku session told to `git add -f docs/plans/x.md` leaves the index empty and logs plan_commit_denied.
  LV-5 ordinary commit    a haiku session told to `git add src/a.txt && git commit -m t` commits src/a.txt, no plan_commit_denied row.

Env isolation: nested `claude -p` gets os.environ minus CLAUDE_PLUGIN_ROOT, CLAUDE_CODE_SESSION_ID and CLAUDE_PROJECT_DIR, plus
CLAUDE_PROJECT_DIR=<scratch project>; the stop-gate and context-nudge user overrides point at paths that never exist. The repo's own
hook log is never touched. Prompts go on stdin. Every scratch project is a fresh git repo with `.craftflow/state/`.

Exit 0 only when every step run is PASS (LV-3 may also be the allowed LIMITATION). Exit 1 otherwise, or when the claude CLI is missing
for a step that needs it (`SKIP: claude CLI not found`). Prints one summary JSON line on stdout.

Run: python3 tests/live/router_context_cut_roundtrip.py --plugin-dir <plugin dir> [--only load_probe|lv2|lv3|lv4|lv5] [--keep]
Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

CALL_TIMEOUT_S = 240
MODEL = "haiku"
LOAD_MARKER = "from --plugin-dir overrides installed version"
STRIPPED_ENV = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")
BASELINE_BUILD_START = 214055
GOAL_BUILD = 60000
LIMITATION_ENTERPLANMODE = "LIMITATION:enterplanmode_not_offered"
STEPS = ("load_probe", "lv2", "lv3", "lv4", "lv5")
ATTEMPTS = 2  # a model may decline to run a command once; the hook, not the model, is what is under test


def log(msg):
    print(msg, file=sys.stderr)


def run(cmd, env, cwd, data=None):
    """(returncode, stdout, stderr); never shell=True; timeout -> returncode 124."""
    try:
        p = subprocess.run(cmd, input=data, env=env, cwd=str(cwd), capture_output=True, text=True, timeout=CALL_TIMEOUT_S)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or "") if isinstance(exc.stdout, str) else "", "timeout"


def nested_env(project, scr):
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    env["CLAUDE_PROJECT_DIR"] = str(project)
    env["CRAFTFLOW_STOP_GATE_USER_CONFIG"] = str(scr / "absent-stop-gate.json")
    env["CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG"] = str(scr / "absent-context-nudge.json")
    return env


def claude(plugin, project, scr, prompt, extra=()):
    cmd = ["claude", "-p", "--plugin-dir", str(plugin), "--model", MODEL, "--session-id", str(uuid.uuid4()), *extra]
    return run(cmd, nested_env(project, scr), project, data=prompt)


def git(project, *args):
    cmd = ["git", "-C", str(project), *args]
    rc, out, err = run(cmd, os.environ.copy(), project)
    if rc != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), (out + err)[:200]))
    return out


def make_project(scr, name, with_plan):
    """A craftflow-managed scratch repo: .craftflow/state/ present, plans and state ignored, one initial commit."""
    project = scr / name
    (project / ".craftflow" / "state").mkdir(parents=True)
    (project / ".gitignore").write_text(".craftflow/\ndocs/plans/\n", encoding="utf-8")
    (project / "README.md").write_text("scratch\n", encoding="utf-8")
    git(project, "init", "-q")
    git(project, "config", "init.defaultBranch", "main")
    git(project, "config", "user.name", "live")
    git(project, "config", "user.email", "live@example.invalid")
    git(project, "add", ".gitignore", "README.md")
    git(project, "commit", "-q", "-m", "init")
    if with_plan:
        (project / "docs" / "plans").mkdir(parents=True)
        (project / "docs" / "plans" / "x.md").write_text("# plan\n", encoding="utf-8")
    return project


def hook_rows(project):
    path = Path(project) / ".craftflow" / "state" / "craftflow-hook-events.log"
    rows = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def tool_uses(stream_json):
    """Names of the tools the model asked to call, from `--output-format stream-json --verbose` output."""
    names = []
    for line in stream_json.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        msg = ev.get("message") if isinstance(ev, dict) else None
        for block in (msg or {}).get("content", []) if isinstance(msg, dict) and isinstance(msg.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                names.append(block.get("name"))
    return names


# --- steps ---------------------------------------------------------------------------------------------
def step_load_probe(plugin, scr):
    project = make_project(scr, "lv1", with_plan=False)
    dbg = scr / "lv1-debug.txt"
    rc, out, err = claude(plugin, project, scr, "Reply OK", ("--debug-file", str(dbg)))
    try:
        text = dbg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    hits = (out + err + text).count(LOAD_MARKER)
    log("lv1: rc=%s marker_hits=%d" % (rc, hits))
    return "PASS" if rc == 0 and hits >= 1 else "FAIL:rc=%s,marker_hits=%d" % (rc, hits)


def readset_json(plugin, *args):
    cmd = [sys.executable, str(plugin / "scripts" / "craftflow_router_readset.py"), "--workflow", "BUILD", "--json", *args]
    rc, out, err = run(cmd, os.environ.copy(), plugin)
    if rc != 0:
        raise RuntimeError("readset exit %s: %s" % (rc, (err or out)[:200]))
    return json.loads(out.strip().splitlines()[-1])


def step_lv2(plugin, scr):
    fx = plugin / "tests" / "fixtures"
    ref = json.loads((fx / "router-baseline.lock.json").read_text(encoding="utf-8"))["baseline_sha"]
    cap = json.loads((fx / "router-budget.json").read_text(encoding="utf-8"))["enforced_caps"]["readset"]["BUILD"]
    base = readset_json(plugin, "--ref", ref, "--readmap", str(fx / "router-readmap.baseline.json"))["start_total"]
    cand = readset_json(plugin)["start_total"]
    log("lv2: baseline start_total=%d candidate start_total=%d enforced_cap=%d goal=%d gap_to_goal=%d" % (
        base, cand, cap, GOAL_BUILD, cand - GOAL_BUILD))
    if base != BASELINE_BUILD_START:
        return "FAIL:baseline_start_total=%d(want %d)" % (base, BASELINE_BUILD_START)
    if cand > cap:
        return "FAIL:candidate_start_total=%d>enforced_cap=%d" % (cand, cap)
    return "PASS"


def step_lv3(plugin, scr):
    for attempt in range(1, ATTEMPTS + 1):
        project = make_project(scr, "lv3-%d" % attempt, with_plan=False)
        rc, out, err = claude(plugin, project, scr, "Call the EnterPlanMode tool now, nothing else",
                              ("--output-format", "stream-json", "--verbose"))
        called = "EnterPlanMode" in tool_uses(out)
        denied = [r for r in hook_rows(project) if r.get("event") == "native_plan_mode_denied"]
        log("lv3 attempt %d: rc=%s tool_called=%s denied_rows=%d" % (attempt, rc, called, len(denied)))
        if denied:
            return "PASS"
        if called:
            return "FAIL:enterplanmode_called_but_not_denied"
    return LIMITATION_ENTERPLANMODE


def step_lv4(plugin, scr):
    last = "FAIL:no_attempt"
    for attempt in range(1, ATTEMPTS + 1):
        project = make_project(scr, "lv4-%d" % attempt, with_plan=True)
        claude(plugin, project, scr, "Run this exact bash command and nothing else: git add -f docs/plans/x.md",
               ("--allowedTools=Bash",))
        staged = git(project, "diff", "--cached", "--name-only").strip()
        denied = [r for r in hook_rows(project) if r.get("event") == "plan_commit_denied"]
        log("lv4 attempt %d: staged=%r denied_rows=%d" % (attempt, staged, len(denied)))
        if staged:
            return "FAIL:plan_staged=%r" % staged
        if denied:
            return "PASS"
        last = "FAIL:no_plan_commit_denied_row(model_may_have_declined)"
    return last


def step_lv5(plugin, scr):
    last = "FAIL:no_attempt"
    for attempt in range(1, ATTEMPTS + 1):
        project = make_project(scr, "lv5-%d" % attempt, with_plan=True)
        (project / "src").mkdir()
        (project / "src" / "a.txt").write_text("a\n", encoding="utf-8")
        claude(plugin, project, scr, "Run this exact bash command and nothing else: git add src/a.txt && git commit -m t",
               ("--allowedTools=Bash",))
        committed = git(project, "show", "--name-only", "--format=%s", "HEAD").split()
        denied = [r for r in hook_rows(project) if r.get("event") == "plan_commit_denied"]
        log("lv5 attempt %d: head=%r denied_rows=%d" % (attempt, committed, len(denied)))
        if denied:
            return "FAIL:ordinary_commit_denied"
        if committed[:1] == ["t"] and "src/a.txt" in committed:
            return "PASS"
        last = "FAIL:src_a_txt_not_committed(head=%r)" % committed
    return last


RUNNERS = {"load_probe": step_load_probe, "lv2": step_lv2, "lv3": step_lv3, "lv4": step_lv4, "lv5": step_lv5}
NEEDS_CLAUDE = {"load_probe", "lv3", "lv4", "lv5"}


def all_ok(summary, steps):
    return all(summary[s] == "PASS" or (s == "lv3" and summary[s] == LIMITATION_ENTERPLANMODE) for s in steps)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plugin-dir", required=True)
    ap.add_argument("--only", choices=STEPS)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)
    steps = (args.only,) if args.only else STEPS
    if NEEDS_CLAUDE & set(steps) and shutil.which("claude") is None:
        print("SKIP: claude CLI not found", file=sys.stderr)
        return 1
    plugin = Path(args.plugin_dir).resolve()
    summary = {s: "NOT_RUN" for s in STEPS}
    summary["evidence_class"] = "B-SIMULATED"
    scr = Path(tempfile.mkdtemp(prefix="cf-rcc-live-"))
    try:
        for s in steps:
            summary[s] = RUNNERS[s](plugin, scr)
            if s == "load_probe" and summary[s] != "PASS":
                break  # the candidate may not be what is loaded: nothing after this is evidence
        if summary["load_probe"] in ("PASS", "NOT_RUN"):  # a failed probe means the candidate may not be what was loaded
            summary["evidence_class"] = "A"
        print(json.dumps(summary))
        return 0 if all_ok(summary, [s for s in steps if summary[s] != "NOT_RUN"] or steps) else 1
    except Exception as exc:  # noqa: BLE001 - driver reports a clean FAIL row, never a traceback
        log("FAIL: unexpected error: %s: %s" % (type(exc).__name__, exc))
        print(json.dumps(summary))
        return 1
    finally:
        if args.keep:
            log("kept scratch dir: %s" % scr)
        else:
            shutil.rmtree(scr, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
