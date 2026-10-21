#!/usr/bin/env python3
"""Class-A live proof driver for the router context cut, round 2 (plan P13, LV-1..LV-5).

  LV-1 load probe         `claude -p --plugin-dir <candidate> --debug-file f` logs "from --plugin-dir overrides installed version".
  LV-2 read-set parity    craftflow_router_readset.py: the baseline (--ref <lock sha> + the frozen read map) start_total is
                          214,055 and the candidate is <= enforced_caps.readset.BUILD. Prints the gap to the 60,000 goal.
  LV-3 EnterPlanMode      a haiku session told to call EnterPlanMode is denied (native_plan_mode_denied row in the hook log).
                          If the host does not offer the tool in -p mode: LIMITATION:enterplanmode_not_offered (HV-4 covers it).
                          That includes the model calling it anyway when the stream proves the host never offered it (init
                          tool list without it) and refused every call with "No such tool available": no hook can run then.
                          Any other call that is NOT denied (offered, succeeded, no result, other error) is a FAIL.
  LV-4 plan commit denied a haiku session told to `git add -f docs/plans/x.md` leaves the index empty and logs plan_commit_denied.
  LV-5 ordinary commit    a haiku session told to `git add src/a.txt && git commit -m t` commits src/a.txt, no plan_commit_denied row.
                          This is a NEGATIVE CONTROL: it also passes with no hook at all, so it only means something together
                          with LV-4 (the hook denies) and the load probe (the candidate is what ran).

Evidence class: "A" only when the load probe PASSed in the same run; otherwise "B-SIMULATED" (a per-step --only run other than
load_probe is therefore always B-SIMULATED; the full run, with no --only, is the Class A source for the impact report's --lv).
LV-3 reports the limitation only when claude exited 0 and its stream shows the session really ran; any other exit is
FAIL:claude_session_failed. Hook-log lines that are not valid JSON are counted in each step's stderr detail (skipped_log_lines).

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
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}  # ambient GIT_DIR etc. must not redirect -C
    rc, out, err = run(cmd, env, project)
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
    """(parsed rows, count of non-blank lines that were not valid JSON); skipped lines are reported, never silent."""
    path = Path(project) / ".craftflow" / "state" / "craftflow-hook-events.log"
    rows, skipped = [], 0
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                skipped += 1
    return rows, skipped


def session_completed(rc, stream_json):
    """True only when claude exited 0 AND the stream shows the session really ran (a system init or a result event)."""
    if rc != 0:
        return False
    for line in stream_json.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and (ev.get("type") == "result" or (ev.get("type") == "system" and ev.get("subtype") == "init")):
            return True
    return False


def lv3_outcome(rc, stream_json, called, denied_rows):
    """One LV-3 attempt: 'PASS', a 'FAIL:...' string, or None (session ran, tool never offered: retry, then LIMITATION).
    A limitation is only ever reachable from a session that really ran."""
    if denied_rows:
        return "PASS"
    if called and session_completed(rc, stream_json) and host_rejected_unoffered(stream_json, "EnterPlanMode"):
        return None  # the model called a tool the host never offered; the host refused it before any PreToolUse hook could run
    if called:
        return "FAIL:enterplanmode_called_but_not_denied"
    if not session_completed(rc, stream_json):
        return "FAIL:claude_session_failed(rc=%s)" % rc
    return None


def evidence_class(summary):
    """Class A only with a passing load probe in this run; a NOT_RUN or failed probe proves nothing about what was loaded."""
    return "A" if summary.get("load_probe") == "PASS" else "B-SIMULATED"


def stream_events(stream_json):
    """The JSON object events of a stream-json output; unparsable lines are skipped."""
    events = []
    for line in stream_json.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def content_blocks(ev):
    msg = ev.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def tool_uses(stream_json):
    """Names of the tools the model asked to call, from `--output-format stream-json --verbose` output."""
    return [b.get("name") for ev in stream_events(stream_json) for b in content_blocks(ev) if b.get("type") == "tool_use"]


def host_rejected_unoffered(stream_json, name):
    """True only when the host never offered `name` (the system init event carries a tool list without it) AND every call
    the model made to it came back is_error with the host's "No such tool available: <name>" text. Then no PreToolUse hook
    can have run for it. A missing tool list, an offered tool, a missing or successful result, or any other error is False."""
    events = stream_events(stream_json)
    inits = [ev for ev in events if ev.get("type") == "system" and ev.get("subtype") == "init"]
    if not inits or not isinstance(inits[0].get("tools"), list) or name in inits[0]["tools"]:
        return False
    ids = {b.get("id") for ev in events for b in content_blocks(ev) if b.get("type") == "tool_use" and b.get("name") == name}
    rejected = set()
    for ev in events:
        for b in content_blocks(ev):
            if b.get("type") != "tool_result" or b.get("tool_use_id") not in ids or b.get("is_error") is not True:
                continue
            c = b.get("content")
            text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else ""
            if ("No such tool available: %s" % name) in text:
                rejected.add(b.get("tool_use_id"))
    return bool(ids) and rejected == ids


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
        rows, skipped = hook_rows(project)
        denied = [r for r in rows if r.get("event") == "native_plan_mode_denied"]
        log("lv3 attempt %d: rc=%s tool_called=%s denied_rows=%d skipped_log_lines=%d" % (attempt, rc, called, len(denied), skipped))
        outcome = lv3_outcome(rc, out, called, len(denied))
        if outcome is not None:
            return outcome
    return LIMITATION_ENTERPLANMODE


def step_lv4(plugin, scr):
    last = "FAIL:no_attempt"
    for attempt in range(1, ATTEMPTS + 1):
        project = make_project(scr, "lv4-%d" % attempt, with_plan=True)
        claude(plugin, project, scr, "Run this exact bash command and nothing else: git add -f docs/plans/x.md",
               ("--allowedTools=Bash",))
        staged = git(project, "diff", "--cached", "--name-only").strip()
        rows, skipped = hook_rows(project)
        denied = [r for r in rows if r.get("event") == "plan_commit_denied"]
        log("lv4 attempt %d: staged=%r denied_rows=%d skipped_log_lines=%d" % (attempt, staged, len(denied), skipped))
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
        rows, skipped = hook_rows(project)
        denied = [r for r in rows if r.get("event") == "plan_commit_denied"]
        log("lv5 attempt %d: head=%r denied_rows=%d skipped_log_lines=%d" % (attempt, committed, len(denied), skipped))
        if denied:
            return "FAIL:ordinary_commit_denied"
        if committed[:1] == ["t"] and "src/a.txt" in committed:
            return "PASS"
        last = "FAIL:src_a_txt_not_committed(head=%r)" % committed
    return last


RUNNERS = {"load_probe": step_load_probe, "lv2": step_lv2, "lv3": step_lv3, "lv4": step_lv4, "lv5": step_lv5}
NEEDS_CLAUDE = {"load_probe", "lv3", "lv4", "lv5"}


def all_ok(summary, steps):
    """Every named step ran and passed (lv3 may be the limitation). NOT_RUN is a failure, and an empty step list proves nothing."""
    return bool(steps) and all(summary[s] == "PASS" or (s == "lv3" and summary[s] == LIMITATION_ENTERPLANMODE) for s in steps)


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
        summary["evidence_class"] = evidence_class(summary)
        if summary["evidence_class"] != "A":
            log("evidence_class B-SIMULATED: no passing load probe in this run, so nothing here proves the candidate plugin was "
                "loaded. Per-step --only runs are always B-SIMULATED except load_probe; the full run is the Class A source.")
        print(json.dumps(summary))
        return 0 if all_ok(summary, steps) else 1
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
