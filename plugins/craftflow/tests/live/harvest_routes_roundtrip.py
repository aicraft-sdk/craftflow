#!/usr/bin/env python3
"""Class-A live proof driver for the harvested workflow routes (plan H3; later phases register more scenarios).

  load_probe  `claude -p --plugin-dir <candidate> --debug-file f` logs "from --plugin-dir overrides installed version".
  LV-ORIENT   (H3) in the scratch repo `claude -p "how does the calc facade work?"`: the transcript announces
              `-> ORIENT workflow`, no `.craftflow/state/workflows/*.json` is created, and the model never uses
              Task, Agent, Edit, Write, MultiEdit or NotebookEdit.

Not yet registered (added by their phases, listed so `--only` names them honestly): LV-CH (H6), LV-TRIAGE (H7), LV-QA (H11),
LV-GUARD (H10), LV-ROUTE (H13). `--only` with one of them exits 1 with `NOT_REGISTERED`.

Scratch repo (`tempfile.mkdtemp(prefix="cf-harvest-live-")`): calc/core.py, calc/facade.py, calc/__main__.py (`python -m calc add 1 2`),
tests/test_core.py (unittest), ISSUE-12.md, ISSUE-13.md, `.craftflow/state/project/activeContext.md` with `AUTO_PROCEED: true`, and a
repo-local git identity. Each scenario runs in a fresh copy of that seed.

Env isolation: nested `claude -p` gets os.environ minus CLAUDE_PLUGIN_ROOT, CLAUDE_CODE_SESSION_ID, CLAUDE_PROJECT_DIR and
TYPESAFE_API_KEY (so no Jev consent block reaches the probe; the project
dir is then set to the scenario copy); the stop-gate and context-nudge user overrides point at paths that never exist;
CRAFTFLOW_QA_HOME=<scratch>/qa-home. Model `sonnet` unless `--model`. Prompts go on stdin.

Evidence class "A" only when load_probe PASSed in the same run, otherwise "B-SIMULATED". Evidence JSON (one entry per scenario plus
the transcript path) is written to <scratch>/evidence.json; keep the scratch with --keep.

Exit 0 only when every scenario run is PASS. Prints one summary JSON line on stdout, e.g.
{"load_probe":"PASS","LV-ORIENT":"PASS","class":"A"}.

Run:     python3 tests/live/harvest_routes_roundtrip.py --plugin-dir <plugin dir> [--only load_probe,LV-ORIENT] [--keep] [--model M]
Setup:   ... --plugin-dir <plugin dir> --setup-only --keep      (prints the seeded scratch dir)
Cleanup: python3 tests/live/harvest_routes_roundtrip.py --cleanup <dir>   (only basenames starting with cf-harvest-live-)
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
DEFAULT_MODEL = "sonnet"
SCRATCH_PREFIX = "cf-harvest-live-"
LOAD_MARKER = "from --plugin-dir overrides installed version"
STRIPPED_ENV = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR", "TYPESAFE_API_KEY")  # last: no Jev consent block in the probe
REGISTERED = ("load_probe", "LV-ORIENT")
NOT_REGISTERED = ("LV-CH", "LV-TRIAGE", "LV-QA", "LV-GUARD", "LV-ROUTE")
FORBIDDEN_TOOLS = ("Task", "Agent", "Edit", "Write", "MultiEdit", "NotebookEdit")
ORIENT_ANNOUNCE = "-> ORIENT workflow"
ORIENT_PROMPT = "how does the calc facade work?"

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


def log(msg):
    print(msg, file=sys.stderr)


def run(cmd, env, cwd, data=None):
    """(returncode, stdout, stderr); never shell=True; timeout -> returncode 124."""
    try:
        p = subprocess.run(cmd, input=data, env=env, cwd=str(cwd), capture_output=True, text=True, timeout=CALL_TIMEOUT_S)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or "") if isinstance(exc.stdout, str) else "", "timeout"


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


def claude(plugin, project, scr, model, prompt, extra=()):
    cmd = ["claude", "-p", "--plugin-dir", str(plugin), "--model", model, "--session-id", str(uuid.uuid4()), *extra]
    return run(cmd, nested_env(project, scr), project, data=prompt)


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


def workflow_files(project):
    d = Path(project) / ".craftflow" / "state" / "workflows"
    return sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []


def judge_orient(rc, stream_json, before, after):
    """('PASS' | 'FAIL:<reason>', detail dict). Pure: every verdict input is a parameter."""
    forbidden = sorted({t for t in tool_uses(stream_json) if t in FORBIDDEN_TOOLS})
    new_files = sorted(set(after) - set(before))
    announced = ORIENT_ANNOUNCE in assistant_text(stream_json)
    detail = {"announced": announced, "new_workflow_files": new_files, "forbidden_tools": forbidden, "rc": rc}
    if not session_ran(rc, stream_json):
        return "FAIL:claude_session_failed(rc=%s)" % rc, detail
    if not announced:
        return "FAIL:no_orient_announce", detail
    if new_files:
        return "FAIL:artifact_minted(%s)" % ",".join(new_files), detail
    if forbidden:
        return "FAIL:forbidden_tool_use(%s)" % ",".join(forbidden), detail
    return "PASS", detail


def evidence_class(summary):
    """Class A only with a passing load probe in this run."""
    return "A" if summary.get("load_probe") == "PASS" else "B-SIMULATED"


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
    rc, out, err = claude(ctx["plugin"], project, ctx["scr"], ctx["model"], ORIENT_PROMPT,
                          ("--output-format", "stream-json", "--verbose"))
    tdir = ctx["scr"] / "transcripts"
    tdir.mkdir(exist_ok=True)
    transcript = tdir / "LV-ORIENT.jsonl"
    transcript.write_text(out, encoding="utf-8")
    verdict, detail = judge_orient(rc, out, before, workflow_files(project))
    detail["transcript"] = str(transcript)
    detail["stderr_tail"] = err[-300:]
    log("LV-ORIENT: %s %s" % (verdict, json.dumps(detail)))
    return verdict, detail


RUNNERS = {"load_probe": step_load_probe, "LV-ORIENT": step_orient}


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
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args(argv)
    if args.cleanup:
        return clean_scratch(args.cleanup)
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
    entries = {}
    try:
        seed = make_seed(scr)
        if args.setup_only:
            print(json.dumps({"scratch": str(scr), "seed": str(seed)}))
            return 0
        ctx = {"scr": scr, "seed": seed, "plugin": plugin, "model": args.model}
        for s in ids:
            summary[s], entries[s] = RUNNERS[s](ctx)
            if s == "load_probe" and summary[s] != "PASS":
                break  # the candidate may not be what is loaded: nothing after this is evidence
        summary["class"] = evidence_class(summary)
        if summary["class"] != "A":
            log("class B-SIMULATED: no passing load probe in this run, so nothing here proves the candidate plugin was loaded.")
        (scr / "evidence.json").write_text(json.dumps({"summary": summary, "scenarios": entries, "model": args.model}, indent=2),
                                           encoding="utf-8")
        log("evidence: %s" % (scr / "evidence.json"))
        print(json.dumps(summary, separators=(",", ":")))
        return 0 if all(summary[s] == "PASS" for s in ids) else 1
    except Exception as exc:  # noqa: BLE001 - driver reports a clean FAIL row, never a traceback
        log("FAIL: unexpected error: %s: %s" % (type(exc).__name__, exc))
        print(json.dumps(summary))
        return 1
    finally:
        if args.keep or args.setup_only:
            log("kept scratch dir: %s" % scr)
        else:
            shutil.rmtree(str(scr), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
