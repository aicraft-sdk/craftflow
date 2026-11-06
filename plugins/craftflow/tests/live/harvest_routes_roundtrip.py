#!/usr/bin/env python3
"""Class-A live proof driver for the harvested workflow routes (plan H3; later phases register more scenarios).

  load_probe  `claude -p --plugin-dir <candidate> --debug-file f` logs "from --plugin-dir overrides installed version".
  LV-ORIENT   (H3) in the scratch repo `claude -p "how does the calc facade work?"`: the transcript announces
              `-> ORIENT workflow`, no `.craftflow/state/workflows/*.json` is created, and the model never uses
              Task, Agent, Edit, Write, MultiEdit or NotebookEdit.
  LV-CH       (H6) `claude -p "run a codebase health check on calc"` (AUTO_PROCEED seed = JUST_GO): the judge is `harvest_routes_ch.py`
              (announce, one artifact, event order, a report page with <html and <svg, a saved design, a PLAN child whose `handoff`
              block links that design, exactly one `open` of the parent's report path, no product change against the seed commit).
              The mint IS expected here (the opposite of LV-ORIENT). The run is polled and stopped as soon as the child exists (the PLAN
              itself is not run), within CH_TIMEOUT_S; the scanner is pinned opus, so one run costs real money. A shim `open` first on
              PATH records the router's `open "{REPORT_PATH}"` in <scratch>/open-calls.log instead of launching a browser.

Not yet registered (added by their phases, listed so `--only` names them honestly): LV-TRIAGE (H7), LV-QA (H11),
LV-GUARD (H10), LV-ROUTE (H13). `--only` with one of them exits 1 with `NOT_REGISTERED`.

Scratch repo (`tempfile.mkdtemp(prefix="cf-harvest-live-")`): calc/core.py, calc/facade.py, calc/__main__.py (`python -m calc add 1 2`),
tests/test_core.py (unittest), ISSUE-12.md, ISSUE-13.md, `.craftflow/state/project/activeContext.md` with `AUTO_PROCEED: true` plus section-only patterns.md and progress.md, and a
repo-local git identity. Each scenario runs in a fresh copy of that seed.

Env isolation: nested `claude -p` gets os.environ minus CLAUDE_PLUGIN_ROOT, CLAUDE_CODE_SESSION_ID, CLAUDE_PROJECT_DIR and
TYPESAFE_API_KEY (so no Jev consent block reaches the probe; the project
dir is then set to the scenario copy); the stop-gate and context-nudge user overrides point at paths that never exist;
CRAFTFLOW_QA_HOME=<scratch>/qa-home. Model `sonnet` unless `--model`. Prompts go on stdin.

Evidence class "A" only when load_probe PASSed in the same run, otherwise "B-SIMULATED". Evidence JSON (one entry per scenario plus
the transcript path) is written to <scratch>/evidence.json; keep the scratch with --keep (it is kept anyway on any non-PASS result).

Exit codes: 0 every scenario PASS and class "A"; 1 a scenario failed (or NOT_REGISTERED / no claude CLI / unexpected error);
2 bad arguments; 3 every scenario PASSed but the class is not "A" (no passing load_probe in this run: not evidence).
Prints one summary JSON line on stdout, e.g. {"load_probe":"PASS","LV-ORIENT":"PASS","class":"A"}.
LV-ORIENT also fails on a Bash command that mentions craftflow_workflow_id or .craftflow/state/workflows (a mint attempt), on any
new entry under workflows/ (*.json, *.events.jsonl, directories) and on any `git status --porcelain` change of the scratch repo.
evidence.json is written even when a scenario raises; the scratch dir is kept (and printed) on any non-PASS result.

Run:     python3 tests/live/harvest_routes_roundtrip.py --plugin-dir <plugin dir> [--only load_probe,LV-ORIENT] [--keep] [--model M]
Setup:   ... --plugin-dir <plugin dir> --setup-only --keep      (prints the seeded scratch dir)
Cleanup: python3 tests/live/harvest_routes_roundtrip.py --cleanup <dir>   (only basenames starting with cf-harvest-live-)
Rejudge: python3 tests/live/harvest_routes_roundtrip.py --rejudge <kept dir>   (the LV-CH judge on a kept run; no claude; exit 0 on PASS)
Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harvest_routes_ch as chj  # noqa: E402  (the LV-CH judge)

CALL_TIMEOUT_S = 240
DEFAULT_MODEL = "sonnet"
SCRATCH_PREFIX = "cf-harvest-live-"
LOAD_MARKER = "from --plugin-dir overrides installed version"
STRIPPED_ENV = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR", "TYPESAFE_API_KEY")  # last: no Jev consent block in the probe
REGISTERED = ("load_probe", "LV-ORIENT", "LV-CH")
NOT_REGISTERED = ("LV-TRIAGE", "LV-QA", "LV-GUARD", "LV-ROUTE")
FORBIDDEN_TOOLS = ("Task", "Agent", "Edit", "Write", "MultiEdit", "NotebookEdit", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList")
MINT_MARKERS = ("craftflow_workflow_id", ".craftflow/state/workflows")  # a Bash command naming either is a mint attempt
EXIT_NOT_CLASS_A = 3
ORIENT_ANNOUNCE = "-> ORIENT workflow"
ORIENT_PROMPT = "how does the calc facade work?"
CH_ANNOUNCE, CH_PROMPT = chj.CH_ANNOUNCE, chj.CH_PROMPT
CH_TIMEOUT_S = 1500
CH_POLL_S = 5
CH_GRACE_S = 5
CH_TOOLS = "--allowedTools=Bash,Write,Edit,Read,Glob,Grep,Skill,Task,Agent,TaskCreate,TaskUpdate,TaskGet,TaskList,LSP"

SEED = {
    "calc/__init__.py": "",
    "calc/core.py": (
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n\n\n"
        "OPS = {'add': add, 'sub': sub}\n\n\ndef apply(op, a, b):\n    if op not in OPS:\n"
        "        raise ValueError('unknown op: %s' % op)\n    return OPS[op](a, b)\n"
    ),
    "calc/facade.py": "from calc.core import apply\n\n\ndef calculate(op, a, b):\n    return apply(op, a, b)\n",
    "calc/__main__.py": (
        "import sys\n\nfrom calc.facade import calculate\n\n\ndef main(argv):\n    op, a, b = argv[0], int(argv[1]), int(argv[2])\n"
        "    print(calculate(op, a, b))\n\n\nif __name__ == '__main__':\n    main(sys.argv[1:])\n"
    ),
    "tests/__init__.py": "",
    "tests/test_core.py": (
        "import unittest\n\nfrom calc.core import add, apply\n\n\nclass CoreTest(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n\n"
        "    def test_apply_unknown(self):\n        with self.assertRaises(ValueError):\n            apply('nope', 1, 2)\n\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n"
    ),
    "ISSUE-12.md": "# Issue 12: add a mod operation\n\nEnhancement: calc should support `python -m calc mod 7 3` printing 1.\n",
    "ISSUE-13.md": "# Issue 13\n\ncalc gives wrong answers sometimes.\n",
    ".gitignore": ".craftflow/\n",
}
ACTIVE_CONTEXT = "# Active Context\n\n## Session Settings\n\nAUTO_PROCEED: true\n"
# A craftflow-managed project already holds its project-tier memory files (the workflow tier is created per workflow).
MEMORY_SEED = {
    "patterns.md": "# Patterns\n\n## Common Gotchas\n\n## User Standards\n\n## Architecture Patterns\n",
    "progress.md": "# Progress\n\n## Current Workflow\n\n## Verification\n\n## Last Updated\n",
}


def log(msg):
    print(msg, file=sys.stderr)


def decode_partial(raw):
    """Partial output of a timed-out child: bytes are decoded, None becomes ''."""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw or ""


def run(cmd, env, cwd, data=None):
    """(returncode, stdout, stderr); never shell=True; own process group; timeout -> kill the group, returncode 124."""
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(cwd),
                         text=True, start_new_session=True)
    try:
        out, err = p.communicate(input=data, timeout=CALL_TIMEOUT_S)
        return p.returncode, out, err
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass  # group already gone (macOS answers EPERM for a group of unreaped zombies)
        out, err = p.communicate()
        return 124, decode_partial(exc.stdout) or decode_partial(out), "timeout" + (": " + decode_partial(err)[-200:] if err else "")


def run_until(cmd, env, cwd, data, done, timeout):
    """(returncode, stdout, stderr, reason). Output goes to temp files so it survives a kill. Polls done() every CH_POLL_S and stops
    the child's process group CH_GRACE_S after done() turns true ('done') or at the timeout ('timeout'); 'exited' otherwise. The group
    is killed in a `finally` even after a normal exit, so a raising done(), Ctrl-C or an orphaned grandchild never outlives the call."""
    with tempfile.TemporaryFile("w+", encoding="utf-8") as out_f, tempfile.TemporaryFile("w+", encoding="utf-8") as err_f:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out_f, stderr=err_f, env=env, cwd=str(cwd), text=True, start_new_session=True)
        deadline, reason = time.monotonic() + timeout, "exited"
        try:
            try:
                p.stdin.write(data)
                p.stdin.close()
            except BrokenPipeError:
                pass  # the child already exited; its exit code speaks
            while p.poll() is None:
                if done():
                    time.sleep(CH_GRACE_S)
                    reason = "done"
                    break
                if time.monotonic() > deadline:
                    reason = "timeout"
                    break
                time.sleep(min(CH_POLL_S, max(0.2, deadline - time.monotonic())))
        finally:
            try:
                os.killpg(p.pid, signal.SIGKILL)  # always: a grandchild outlives its exited leader
            except (ProcessLookupError, PermissionError):
                pass  # group already gone
            p.wait()
        out_f.seek(0)
        err_f.seek(0)
        return p.returncode, out_f.read(), err_f.read(), reason


def git(project, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}  # ambient GIT_DIR etc. must not redirect -C
    rc, out, err = run(["git", "-C", str(project), *args], env, project)
    if rc != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), (out + err)[:200]))
    return out


def make_seed(scr):
    """The seeded scratch repo every scenario copies: files above, the AUTO_PROCEED state file, a local git identity, one commit."""
    seed = scr / "seed"
    for rel, body in SEED.items():
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        (seed / rel).write_text(body, encoding="utf-8")
    state = seed / ".craftflow" / "state" / "project"
    state.mkdir(parents=True)
    (state / "activeContext.md").write_text(ACTIVE_CONTEXT, encoding="utf-8")
    for name, body in MEMORY_SEED.items():
        (state / name).write_text(body, encoding="utf-8")
    git(seed, "init", "-q")
    git(seed, "config", "init.defaultBranch", "main")
    git(seed, "config", "user.name", "live")
    git(seed, "config", "user.email", "live@example.invalid")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    return seed


def fresh_copy(scr, seed, name):
    dest = scr / name
    shutil.copytree(str(seed), str(dest))
    return dest


def nested_env(project, scr):
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    env["CLAUDE_PROJECT_DIR"] = str(project)
    env["CRAFTFLOW_STOP_GATE_USER_CONFIG"] = str(scr / "absent-stop-gate.json")
    env["CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG"] = str(scr / "absent-context-nudge.json")
    env["CRAFTFLOW_QA_HOME"] = str(scr / "qa-home")
    return env


def claude_cmd(plugin, model, extra=()):
    return ["claude", "-p", "--plugin-dir", str(plugin), "--model", model, "--session-id", str(uuid.uuid4()), *extra]


def claude(plugin, project, scr, model, prompt, extra=()):
    return run(claude_cmd(plugin, model, extra), nested_env(project, scr), project, data=prompt)


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
    """Names of the tools the model asked to call."""
    return [b.get("name") for ev in stream_events(stream_json) for b in content_blocks(ev) if b.get("type") == "tool_use"]


def assistant_text(stream_json):
    """All assistant text blocks joined, plus the final result text."""
    parts = [b.get("text", "") for ev in stream_events(stream_json) if ev.get("type") == "assistant"
             for b in content_blocks(ev) if b.get("type") == "text"]
    parts += [ev.get("result", "") for ev in stream_events(stream_json) if ev.get("type") == "result" and isinstance(ev.get("result"), str)]
    return "\n".join(parts)


def session_ran(rc, stream_json):
    """True only when claude exited 0 AND the stream shows a result event (the session really ran)."""
    return rc == 0 and any(ev.get("type") == "result" for ev in stream_events(stream_json))


def bash_commands(stream_json):
    """The command string of every Bash tool_use."""
    return [str((b.get("input") or {}).get("command", "")) for ev in stream_events(stream_json)
            for b in content_blocks(ev) if b.get("type") == "tool_use" and b.get("name") == "Bash"]


def workflow_files(project):
    """Every entry under .craftflow/state/workflows/ (*.json, *.events.jsonl, directories as 'name/')."""
    d = Path(project) / ".craftflow" / "state" / "workflows"
    return sorted(p.name + ("/" if p.is_dir() else "") for p in d.iterdir()) if d.is_dir() else []


def git_porcelain(project):
    return git(project, "status", "--porcelain")


def judge_orient(rc, stream_json, before, after, git_before, git_after):
    """('PASS' | 'FAIL:<reason>', detail dict). Pure: every verdict input is a parameter (all required: no default reads as success)."""
    forbidden = sorted({t for t in tool_uses(stream_json) if t in FORBIDDEN_TOOLS})
    new_files = sorted(set(after) - set(before))
    mints = [c for c in bash_commands(stream_json) if any(m in c for m in MINT_MARKERS)]
    announced = ORIENT_ANNOUNCE in assistant_text(stream_json)
    detail = {"announced": announced, "new_workflow_files": new_files, "forbidden_tools": forbidden, "mint_commands": mints,
              "git_changed": git_before != git_after, "rc": rc}
    if not session_ran(rc, stream_json):
        return "FAIL:claude_session_failed(rc=%s)" % rc, detail
    if not announced:
        return "FAIL:no_orient_announce", detail
    if mints:
        return "FAIL:mint_attempt(%s)" % " | ".join(c[:120] for c in mints), detail
    if new_files:
        return "FAIL:artifact_minted(%s)" % ",".join(new_files), detail
    if forbidden:
        return "FAIL:forbidden_tool_use(%s)" % ",".join(forbidden), detail
    if git_before != git_after:
        return "FAIL:scratch_repo_changed(before=%r,after=%r)" % (git_before[:120], git_after[:120]), detail
    return "PASS", detail


ch_state = chj.ch_state


def judge_ch(stream_json, state, changes, open_calls):
    """('PASS' | 'FAIL:<reason>', detail dict): the stream facts plus `harvest_routes_ch.judge_ch` (see its docstring for the rules)."""
    return chj.judge_ch(CH_ANNOUNCE in assistant_text(stream_json), bool(stream_events(stream_json)), state, changes, open_calls)


def stream_facts(stream_json):
    return CH_ANNOUNCE in assistant_text(stream_json), bool(stream_events(stream_json))


def rejudge_dir(scr):
    """The LV-CH judge on a kept scratch dir (LV-CH/ project, transcripts/LV-CH.jsonl, open-calls.log), offline."""
    return chj.rejudge(scr, git, stream_facts)


def evidence_class(summary):
    """Class A only with a passing load probe in this run."""
    return "A" if summary.get("load_probe") == "PASS" else "B-SIMULATED"


def exit_for(summary, ids):
    """0 only when every scenario PASSed AND the class is A; 1 on any non-PASS; 3 for all-PASS under class B."""
    if not all(summary.get(s) == "PASS" for s in ids):
        return 1
    return 0 if summary.get("class") == "A" else EXIT_NOT_CLASS_A


# --- scenarios -----------------------------------------------------------------------------------------
def step_load_probe(ctx):
    project = fresh_copy(ctx["scr"], ctx["seed"], "load_probe")
    dbg = ctx["scr"] / "load_probe-debug.txt"
    rc, out, err = claude(ctx["plugin"], project, ctx["scr"], ctx["model"], "Reply OK", ("--debug-file", str(dbg)))
    try:
        text = dbg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    hits = (out + err + text).count(LOAD_MARKER)
    log("load_probe: rc=%s marker_hits=%d" % (rc, hits))
    return "PASS" if rc == 0 and hits >= 1 else "FAIL:rc=%s,marker_hits=%d" % (rc, hits), {"marker_hits": hits}


def step_orient(ctx):
    project = fresh_copy(ctx["scr"], ctx["seed"], "LV-ORIENT")
    before = workflow_files(project)
    git_before = git_porcelain(project)
    rc, out, err = claude(ctx["plugin"], project, ctx["scr"], ctx["model"], ORIENT_PROMPT,
                          ("--output-format", "stream-json", "--verbose"))
    tdir = ctx["scr"] / "transcripts"
    tdir.mkdir(exist_ok=True)
    transcript = tdir / "LV-ORIENT.jsonl"
    transcript.write_text(out, encoding="utf-8")
    verdict, detail = judge_orient(rc, out, before, workflow_files(project), git_before, git_porcelain(project))
    detail["transcript"] = str(transcript)
    detail["stderr_tail"] = err[-300:]
    log("LV-ORIENT: %s %s" % (verdict, json.dumps(detail)))
    return verdict, detail


def open_shim(scr):
    """A PATH dir whose `open` appends its arguments to <scr>/open-calls.log and exits 0 (no browser in a live proof)."""
    shim = scr / "shim-bin"
    shim.mkdir(exist_ok=True)
    (shim / "open").write_text('#!/bin/sh\necho "$@" >> "%s"\n' % (scr / "open-calls.log"), encoding="utf-8")
    (shim / "open").chmod(0o755)
    return shim


def step_ch(ctx):
    project = fresh_copy(ctx["scr"], ctx["seed"], "LV-CH")
    partial = ctx.setdefault("partial", {}).setdefault("LV-CH", {})  # evidence survives a raise (main merges it into evidence.json)
    partial.update({"project": str(project), "seed_sha": ctx["seed_sha"], "stop_reason": "started"})
    try:
        env = nested_env(project, ctx["scr"])
        env["PATH"] = str(open_shim(ctx["scr"])) + os.pathsep + env.get("PATH", "")
        extra = ("--output-format", "stream-json", "--verbose", "--max-turns", "80", CH_TOOLS)
        rc, out, err, why = run_until(claude_cmd(ctx["plugin"], ctx["model"], extra), env, project, CH_PROMPT,
                                      lambda: (lambda s: bool(s["children"]) and "handoff_started" in s["events"])(ch_state(project)),
                                      CH_TIMEOUT_S)
        tdir = ctx["scr"] / "transcripts"
        tdir.mkdir(exist_ok=True)
        transcript = tdir / "LV-CH.jsonl"
        transcript.write_text(out, encoding="utf-8")
        partial.update({"transcript": str(transcript), "stop_reason": why, "rc": rc})
        state, changes, calls = chj.collect(project, ctx["scr"], git, ctx["seed_sha"])
        verdict, detail = judge_ch(out, state, changes, calls)
    except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised for main's clean FAIL row
        partial["error"] = "%s: %s" % (type(exc).__name__, exc)
        raise
    detail.update({"transcript": str(transcript), "stop_reason": why, "rc": rc, "stderr_tail": err[-300:], "project": str(project),
                   "seed_sha": ctx["seed_sha"], "tools_used": sorted(set(t for t in tool_uses(out) if t))})
    log("LV-CH: %s stop=%s %s" % (verdict, why, json.dumps(detail)[:600]))
    return verdict, detail


RUNNERS = {"load_probe": step_load_probe, "LV-ORIENT": step_orient, "LV-CH": step_ch}


def clean_scratch(path):
    """Remove one scratch dir; refuses anything whose basename does not start with the scratch prefix. Returns an exit code."""
    p = Path(path).resolve()
    if not p.name.startswith(SCRATCH_PREFIX):
        log("refusing to remove %s: basename must start with %s" % (p, SCRATCH_PREFIX))
        return 1
    if p.is_dir():
        shutil.rmtree(str(p), ignore_errors=True)
    return 0


def parse_only(raw):
    ids = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [s for s in ids if s not in REGISTERED and s not in NOT_REGISTERED]
    pending = [s for s in ids if s in NOT_REGISTERED]
    return ids, unknown, pending


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plugin-dir")
    ap.add_argument("--only", help="comma-separated scenario ids")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--setup-only", action="store_true")
    ap.add_argument("--cleanup", metavar="DIR")
    ap.add_argument("--rejudge", metavar="DIR", help="judge a kept LV-CH scratch dir offline (no claude); exit 0 on PASS")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args(argv)
    if args.cleanup:
        return clean_scratch(args.cleanup)
    if args.rejudge:
        verdict, detail = rejudge_dir(args.rejudge)
        print(json.dumps({"LV-CH": verdict, "seed_sha": detail.get("seed_sha"), "product_changes": detail.get("product_changes"),
                          "open_calls": detail.get("open_calls")}, separators=(",", ":")))
        return 0 if verdict == "PASS" else 1
    if not args.plugin_dir:
        ap.error("--plugin-dir is required")
    ids, unknown, pending = parse_only(args.only) if args.only else (list(REGISTERED), [], [])
    if unknown or (args.only and not ids):
        log("unknown scenario ids: %s (registered: %s)" % (unknown or args.only, ", ".join(REGISTERED)))
        return 2
    if pending:
        print(json.dumps({s: "NOT_REGISTERED" for s in pending}))
        return 1
    if not args.setup_only and shutil.which("claude") is None:
        print("SKIP: claude CLI not found", file=sys.stderr)
        return 1
    plugin = Path(args.plugin_dir).resolve()
    scr = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX))
    summary = {s: "NOT_RUN" for s in ids}
    entries, partial = {}, {}
    code = 1  # anything that skips the explicit return below is a failure
    try:
        seed = make_seed(scr)
        if args.setup_only:
            print(json.dumps({"scratch": str(scr), "seed": str(seed)}))
            return 0
        ctx = {"scr": scr, "seed": seed, "plugin": plugin, "model": args.model, "partial": partial,
               "seed_sha": git(seed, "rev-parse", "HEAD").strip()}
        for s in ids:
            summary[s], entries[s] = RUNNERS[s](ctx)
            if s == "load_probe" and summary[s] != "PASS":
                break  # the candidate may not be what is loaded: nothing after this is evidence
        summary["class"] = evidence_class(summary)
        if summary["class"] != "A":
            log("class B-SIMULATED: no passing load probe in this run, so nothing here proves the candidate plugin was loaded.")
        print(json.dumps(summary, separators=(",", ":")))
        code = exit_for(summary, ids)
        return code
    except Exception as exc:  # noqa: BLE001 - driver reports a clean FAIL row, never a traceback
        log("FAIL: unexpected error: %s: %s" % (type(exc).__name__, exc))
        print(json.dumps(summary))
        code = 1
        return code
    finally:
        if not args.setup_only:
            for sid, part in partial.items():
                entries.setdefault(sid, part)  # a scenario that raised still leaves its entry
            summary.setdefault("class", evidence_class(summary))
            (scr / "evidence.json").write_text(json.dumps({"summary": summary, "scenarios": entries, "model": args.model}, indent=2),
                                               encoding="utf-8")
        keep = args.keep or args.setup_only or code != 0
        if keep:
            log("kept scratch dir: %s (evidence: %s)" % (scr, scr / "evidence.json"))
        else:
            shutil.rmtree(str(scr), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
