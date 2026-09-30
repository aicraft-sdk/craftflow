#!/usr/bin/env python3
"""Class-A live proof driver for the /compact line and the durable user override (SPEC-0017 / ADR-0054).

Runs real `claude -p --plugin-dir <plugin> --model haiku` sessions in scratch projects:
  load probe  hard precondition: `--plugin-dir` must override the installed craftflow (one nudge writer only)
  LVC-1       user file enables the nudge on an UNMODIFIED (audit) plugin; the bound /compact line reaches the model
  LVC-2       no workflow to bind -> the generic /compact line
  LVC-3       corrupt user file falls back to the plugin values (override error "corrupt")
  LVC-4       user file "off" silences an "on" plugin
  LVC-5       --boundary relay carries the same bound line and writes it into the checkpoint

Env isolation (DD-17): nested `claude -p` gets os.environ minus CLAUDE_PLUGIN_ROOT, CLAUDE_CODE_SESSION_ID and
CLAUDE_PROJECT_DIR, plus CLAUDE_PROJECT_DIR=<scratch project> and CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG=<scenario
user file>. The real ~/.claude/craftflow is never written and the repo's own hook log is never touched. Prompts
go on stdin.

Exit 0 only when every step is PASS or the allowed `LIMITATION:env_not_propagated_to_hooks` (lvc1). Exit 1
otherwise (also when the claude CLI is missing: `SKIP: claude CLI not found`). One summary JSON line on stdout.

Run: python3 tests/live/context_nudge_compact_roundtrip.py --plugin-dir <plugin dir> [--keep]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

CALL_TIMEOUT_S = 180
MODEL = "haiku"
LOAD_MARKER = "from --plugin-dir overrides installed version"
SEAM_ENV = "CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG"
STRIPPED_ENV = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")
ECHO_PROMPT = ("If your context contains a command starting with '/compact ', reply with that command "
               "verbatim; otherwise reply NONE.")
WF = "wf-live-compact-0001"
BOUND_PREFIX = "/compact Keep craftflow workflow " + WF
USER_KEYS = ["assumedWindow", "contextNudge", "criticalTokens", "warnTokens"]
LIMITATION_LVC1 = "LIMITATION:env_not_propagated_to_hooks"


def log(msg):
    print(msg, file=sys.stderr)


def nested_env(project, user_file):
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    env["CLAUDE_PROJECT_DIR"] = str(project)
    env[SEAM_ENV] = str(user_file)
    return env


def run(cmd, env, cwd, data=None):
    """(returncode, stdout, stderr); never shell=True; timeout -> returncode 124."""
    try:
        p = subprocess.run(cmd, input=data, env=env, cwd=str(cwd), capture_output=True, text=True,
                           timeout=CALL_TIMEOUT_S)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or "") if isinstance(exc.stdout, str) else "", "timeout"


def claude(plugin, project, user_file, prompt, session_flag, sid, extra=()):
    cmd = ["claude", "-p", "--plugin-dir", str(plugin), "--model", MODEL, session_flag, sid, *extra]
    return run(cmd, nested_env(project, user_file), project, data=prompt)


def load_constant(plugin_dir, name):
    spec = importlib.util.spec_from_file_location(
        "cf_ctx_compact", str(plugin_dir / "scripts" / "craftflow_context_nudge_compact.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, name)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def nudge_rows(project, sid=None):
    path = Path(project) / ".craftflow" / "state" / "craftflow-hook-events.log"
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("event") == "context_nudge" and (sid is None or row.get("session_id") == sid):
            rows.append(row)
    return rows


def count_log_rows(path):
    try:
        return sum(1 for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
                   if "context_nudge" in line)
    except OSError:
        return 0


def config_hashes(plugin_dir):
    out = {}
    for p in sorted(Path(plugin_dir, "config").rglob("*")):
        if p.is_file():
            out[str(p.relative_to(plugin_dir))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def copy_plugin(plugin_dir, dest):
    shutil.copytree(str(plugin_dir), str(dest), ignore=shutil.ignore_patterns("__pycache__"))
    rc, out, err = run(["diff", "-r", "--exclude=config", "--exclude=__pycache__", str(plugin_dir), str(dest)],
                       os.environ.copy(), dest.parent)
    if rc != 0 or out.strip():
        raise RuntimeError("plugin copy differs from source: %s" % (out or err)[:300])
    mode = json.loads((plugin_dir / "config" / "hook-mode.json").read_text(encoding="utf-8"))
    mode["contextNudge"] = "on"
    (dest / "config" / "hook-mode.json").write_text(json.dumps(mode), encoding="utf-8")
    (dest / "config" / "context-nudge.json").write_text(
        json.dumps({"warnTokens": 1000, "criticalTokens": 100000000, "assumedWindow": 200000000}),
        encoding="utf-8")


def setup_scratch(scr, plugin_dir):
    for name in ("p3", "p4", "p5", "p6", "probe1", "probe2"):
        (scr / name / ".craftflow" / "state").mkdir(parents=True)
    copy_plugin(plugin_dir, scr / "plugin")
    user = {"contextNudge": "on", "warnTokens": 1000, "criticalTokens": 100000000, "assumedWindow": 200000000}
    (scr / "user-on.json").write_text(json.dumps(user), encoding="utf-8")
    (scr / "user-corrupt.json").write_text("{not json", encoding="utf-8")
    (scr / "user-off.json").write_text(json.dumps({"contextNudge": "off"}), encoding="utf-8")
    wf_dir = scr / "p3" / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    # no session_id on purpose: an artifact for another session is never bound (session filter), this one
    # binds through the single-candidate path
    (wf_dir / (WF + ".json")).write_text(json.dumps(
        {"workflow_uuid": WF, "workflow_type": "BUILD", "phase_cursor": "P2",
         "pending_gate": "user_build_approval", "plan_file": "docs/plans/live-compact-plan.md",
         "design_file": "docs/plans/live-compact-design.md", "worktree_path": None,
         "status_history": [{"event": "workflow_started"}]}), encoding="utf-8")


def load_probe(plugin, project, user_file, debug_file):
    # --debug-file (not bare --debug): in print mode the debug stream is only reliably captured via a file
    rc, out, err = claude(plugin, project, user_file, "Reply OK", "--session-id", str(uuid.uuid4()),
                          ("--debug-file", str(debug_file)))
    try:
        debug_text = Path(debug_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        debug_text = ""
    hits = (out + err + debug_text).count(LOAD_MARKER)
    log("load probe (%s): rc=%s marker_hits=%d" % (Path(plugin).name, rc, hits))
    return rc == 0 and hits >= 1


def foreign_rows(project):
    """Rows without the `override` key can only come from a second (older) craftflow copy."""
    return [r for r in nudge_rows(project) if "override" not in r]


def two_turns(plugin, project, user_file, sid, first, second):
    rc1, _, e1 = claude(plugin, project, user_file, first, "--session-id", sid)
    rc2, out2, e2 = claude(plugin, project, user_file, second, "--resume", sid)
    if rc1 != 0 or rc2 != 0:
        log("claude rc=%s/%s err=%r" % (rc1, rc2, (e1 + e2)[:160]))
    return rc1, rc2, out2


def lvc1(plugin_dir, scr, sid):
    p3, uf = scr / "p3", scr / "user-on.json"
    rc1, rc2, out = two_turns(plugin_dir, p3, uf, sid,
                              "Reply OK. Context: we are working on craftflow workflow %s." % WF, ECHO_PROMPT)
    if rc1 != 0 or rc2 != 0:
        return "FAIL:claude_rc=%s/%s" % (rc1, rc2), out
    rows = [r for r in nudge_rows(p3, sid) if r.get("outcome") == "nudged"]
    if not rows:
        return "NO_NUDGE", out
    if len(rows) != 1:
        return "FAIL:nudged_rows=%d" % len(rows), out
    r = rows[0]
    want = {"override": "applied", "compact_source": "workflow", "compact_reason": "single_candidate",
            "compact_wf": WF, "mode": "on"}
    got = {k: r.get(k) for k in want}
    if got != want or sorted(r.get("override_keys") or []) != USER_KEYS:
        return "FAIL:row=%r keys=%r" % (got, r.get("override_keys")), out
    for needle in (BOUND_PREFIX, "phase P2", "pending gate user_build_approval"):
        if needle not in out:
            return "FAIL:model_output_missing=%r(out=%r)" % (needle, out[:200]), out
    return "PASS", out


def lvc1_direct_hook(plugin_dir, scr, sid):
    """Fallback when the seam env never reached the hooks: call the hook script on the real transcript."""
    hits = glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "projects", "*", sid + ".jsonl"))
    if not hits:
        return "FAIL:no_transcript_for_direct_hook"
    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": sid,
                          "transcript_path": hits[0], "prompt": "direct"})
    env = nested_env(scr / "p3", scr / "user-on.json")
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_dir)
    rc, out, err = run([sys.executable, str(plugin_dir / "scripts" / "craftflow_context_nudge.py")], env,
                       scr, data=payload)
    rows = [r for r in nudge_rows(scr / "p3", sid) if r.get("outcome") == "nudged"]
    if rc != 0 or not rows:
        return "FAIL:direct_hook_rc=%s rows=%d" % (rc, len(rows))
    r = rows[-1]
    if r.get("override") != "applied" or r.get("compact_wf") != WF or r.get("compact_source") != "workflow":
        return "FAIL:direct_row=%r" % {k: r.get(k) for k in ("override", "compact_wf", "compact_source")}
    return LIMITATION_LVC1


def lvc2(plugin_dir, scr, generic_prefix):
    p4, sid = scr / "p4", str(uuid.uuid4())
    rc1, rc2, out = two_turns(plugin_dir, p4, scr / "user-on.json", sid, "Reply OK", ECHO_PROMPT)
    if rc1 != 0 or rc2 != 0:
        return "FAIL:claude_rc=%s/%s" % (rc1, rc2)
    rows = [r for r in nudge_rows(p4, sid) if r.get("outcome") == "nudged"]
    if len(rows) != 1:
        return "FAIL:nudged_rows=%d" % len(rows)
    r = rows[0]
    if r.get("compact_source") != "generic" or r.get("compact_reason") != "no_mention":
        return "FAIL:row=%r" % {k: r.get(k) for k in ("compact_source", "compact_reason")}
    if generic_prefix not in out:
        return "FAIL:generic_prefix_missing(out=%r)" % out[:200]
    return "PASS"


def lvc3(plugin, scr):
    p5, sid = scr / "p5", str(uuid.uuid4())
    rc1, rc2, out = two_turns(plugin, p5, scr / "user-corrupt.json", sid, "Reply OK", ECHO_PROMPT)
    if rc1 != 0 or rc2 != 0:
        return "FAIL:claude_rc=%s/%s" % (rc1, rc2)
    rows = [r for r in nudge_rows(p5, sid) if r.get("outcome") == "nudged"]
    if len(rows) != 1:
        return "FAIL:nudged_rows=%d" % len(rows)
    r = rows[0]
    if r.get("override") != "error" or r.get("override_error") != "corrupt":
        return "FAIL:row=%r" % {k: r.get(k) for k in ("override", "override_error")}
    if "/compact " not in out:
        return "FAIL:no_compact_in_output(out=%r)" % out[:200]
    return "PASS"


def lvc4(plugin, scr):
    p6, sid = scr / "p6", str(uuid.uuid4())
    rc1, rc2, out = two_turns(plugin, p6, scr / "user-off.json", sid, "Reply OK", ECHO_PROMPT)
    if rc1 != 0 or rc2 != 0:
        return "FAIL:claude_rc=%s/%s" % (rc1, rc2)
    state = p6 / ".craftflow" / "state" / "context-nudge" / (sid + ".json")
    if state.exists():
        return "FAIL:state_file_created"
    if nudge_rows(p6, sid):
        return "FAIL:context_nudge_rows=%d" % len(nudge_rows(p6, sid))
    if "/compact" in out and "NONE" not in out:
        return "FAIL:compact_in_output(out=%r)" % out[:200]
    return "PASS"


def lvc5(plugin_dir, scr, sid):
    p3 = scr / "p3"
    env = nested_env(p3, scr / "user-on.json")
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_dir)
    cmd = [sys.executable, str(plugin_dir / "scripts" / "craftflow_context_nudge.py"), "--boundary",
           "--wf", WF, "--phase", "P2", "--session-id", sid, "--project-root", str(p3)]

    def call():
        rc, out, err = run(cmd, env, scr)
        try:
            return rc, json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return rc, {"outcome": "unparsable", "error": (out + err)[:200]}

    rc1, r1 = call()
    if rc1 != 0 or r1.get("relay") is not True:
        return "FAIL:first_call=%r" % {k: r1.get(k) for k in ("relay", "outcome", "error")}
    advisory = r1.get("advisory") or ""
    cp = read_json(r1.get("checkpoint_path") or "")
    line = (cp or {}).get("compact_command")
    if not isinstance(line, str) or not line.startswith(BOUND_PREFIX):
        return "FAIL:checkpoint_compact_command=%r" % (line if not isinstance(line, str) else line[:80])
    if not advisory.endswith(line):
        return "FAIL:advisory_does_not_end_with_checkpoint_line(tail=%r)" % advisory[-120:]
    rows = [r for r in nudge_rows(p3, sid) if r.get("compact_reason") == "explicit_wf"]
    if not rows or rows[-1].get("override") != "applied":
        return "FAIL:boundary_row=%r" % [{k: r.get(k) for k in ("outcome", "compact_reason", "override")}
                                         for r in rows]
    rc2, r2 = call()
    if r2.get("relay") is not False or r2.get("outcome") != "already_advised":
        return "FAIL:second_call=%r" % {k: r2.get(k) for k in ("relay", "outcome")}
    return "PASS"


def all_ok(summary):
    for key, val in summary.items():
        if key == "second_copy_detected":
            if val:
                return False
        elif key == "plugin_config_unchanged":
            if val is not True:
                return False
        elif key == "evidence_class":
            continue
        elif not (val == "PASS" or (key == "lvc1" and val == LIMITATION_LVC1)):
            return False
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plugin-dir", required=True)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)
    if shutil.which("claude") is None:
        print("SKIP: claude CLI not found", file=sys.stderr)
        return 1
    plugin_dir = Path(args.plugin_dir).resolve()
    scr = Path(tempfile.mkdtemp(prefix="cf-nudge-compact-"))
    summary = {"load_probe": "FAIL", "lvc1": "NOT_RUN", "lvc2": "NOT_RUN", "lvc3": "NOT_RUN",
               "lvc4": "NOT_RUN", "lvc5": "NOT_RUN", "second_copy_detected": False,
               "plugin_config_unchanged": False, "evidence_class": "B-SIMULATED"}
    before = config_hashes(plugin_dir)
    try:
        generic_prefix = load_constant(plugin_dir, "GENERIC_COMPACT_LINE")[:len("/compact Preserve the current goal")]
        setup_scratch(scr, plugin_dir)
        uo = scr / "user-on.json"
        ok1 = load_probe(plugin_dir, scr / "probe1", uo, scr / "probe1-debug.txt")
        ok2 = load_probe(scr / "plugin", scr / "probe2", uo, scr / "probe2-debug.txt")
        if not (ok1 and ok2):
            summary["plugin_config_unchanged"] = config_hashes(plugin_dir) == before
            print(json.dumps(summary))
            log("load probe failed: evidence class B-SIMULATED; no LVC scenario run")
            return 1
        summary["load_probe"] = "PASS"
        s3 = str(uuid.uuid4())
        summary["lvc1"], out1 = lvc1(plugin_dir, scr, s3)
        summary["lvc2"] = lvc2(plugin_dir, scr, generic_prefix)
        summary["lvc3"] = lvc3(scr / "plugin", scr)
        summary["lvc4"] = lvc4(scr / "plugin", scr)
        if summary["lvc1"] == "NO_NUDGE":
            # the plugin-driven LVC-3 nudged while LVC-1 (env seam) did not: the env never reached the hooks
            if summary["lvc3"] == "PASS":
                summary["lvc1"] = lvc1_direct_hook(plugin_dir, scr, s3)
            else:
                summary["lvc1"] = "FAIL:no_nudge_and_lvc3_did_not_nudge"
        summary["lvc5"] = lvc5(plugin_dir, scr, s3) if summary["lvc1"] in ("PASS", LIMITATION_LVC1) else "NOT_RUN"
        summary["second_copy_detected"] = any(foreign_rows(scr / n) for n in ("p3", "p4", "p5", "p6"))
        summary["plugin_config_unchanged"] = config_hashes(plugin_dir) == before
        summary["evidence_class"] = "A" if summary["lvc1"] == "PASS" else "A-partial/B-override"
        print(json.dumps(summary))
        log("scratch Claude sessions are left under ~/.claude/projects/ (slug of %s)" % scr)
        if summary["lvc1"] == LIMITATION_LVC1:
            log("manual check: in your own terminal create ~/.claude/craftflow/context-nudge.json with "
                '{"contextNudge":"on","warnTokens":1000}, send two prompts in a scratch session, then delete it')
        return 0 if all_ok(summary) else 1
    except Exception as exc:  # noqa: BLE001 - driver reports a clean FAIL row, never a traceback
        log("FAIL: unexpected error: %s: %s" % (type(exc).__name__, exc))
        summary["plugin_config_unchanged"] = config_hashes(plugin_dir) == before
        print(json.dumps(summary))
        return 1
    finally:
        if args.keep:
            log("kept scratch dir: %s" % scr)
        else:
            shutil.rmtree(scr, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
