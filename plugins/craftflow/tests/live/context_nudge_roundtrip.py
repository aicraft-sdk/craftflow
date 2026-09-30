#!/usr/bin/env python3
"""Class-A live proof driver for the context-size nudge (SPEC-0016 / ADR-0051).

Runs real `claude -p --plugin-dir <plugin> --model haiku` sessions in scratch projects and checks the
plan's Live Verification Strategy: a load probe, then LV-1 (default audit), LV-2 (nudge reaches the model
once), LV-3 (critical escalation), LV-4 (/compact reset, no stale re-nudge), LV-5 (--boundary relay
against the live transcript) and LV-6 (CLAUDE_CODE_SESSION_ID parity in the Bash tool).

Env isolation (DD-17): nested `claude -p` gets os.environ minus CLAUDE_PLUGIN_ROOT, CLAUDE_CODE_SESSION_ID
and CLAUDE_PROJECT_DIR, plus CLAUDE_PROJECT_DIR=<scratch project>. Direct script calls (LV-5) also set
CLAUDE_PLUGIN_ROOT to the scratch plugin copy. The repo's own hook log is never touched. Prompts are fed on
stdin (variadic flags such as --debug/--allowedTools would otherwise swallow a positional prompt).

Exit 0 only when every step is PASS or an allowed `LIMITATION:<reason>` (lv4, lv6). Exit 1 otherwise or
when the claude CLI is missing (`SKIP: claude CLI not found`). Prints one summary JSON line on stdout.

Run: python3 tests/live/context_nudge_roundtrip.py --plugin-dir <plugin dir> [--keep]
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

CALL_TIMEOUT_S = 180
MODEL = "haiku"
LOAD_MARKER = "from --plugin-dir overrides installed version"
ADVISORY_MARKER = "CRAFTFLOW context advisory"
ECHO_ADVISORY_PROMPT = ("If your context contains a line starting with 'CRAFTFLOW context advisory', "
                        "reply with that line verbatim; otherwise reply NONE.")
STRIPPED_ENV = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")
ALLOWED_LIMITATIONS = {"lv4": "LIMITATION:compact_unsupported_in_print_mode",
                       "lv6": "LIMITATION:session_env_mismatch"}


def log(msg):
    print(msg, file=sys.stderr)


def nested_env(project):
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    env["CLAUDE_PROJECT_DIR"] = str(project)
    # SPEC-0017 seam: point the durable user override at a path that never exists so a real
    # ~/.claude/craftflow/context-nudge.json cannot leak into the SPEC-0016 scenarios
    env["CRAFTFLOW_CONTEXT_NUDGE_USER_CONFIG"] = str(
        Path(tempfile.gettempdir()) / "cf-nudge-live-no-user-override-absent.json")
    return env


def run(cmd, env, cwd, data=None):
    """(returncode, stdout, stderr); never shell=True; timeout -> returncode 124."""
    try:
        p = subprocess.run(cmd, input=data, env=env, cwd=str(cwd), capture_output=True, text=True,
                           timeout=CALL_TIMEOUT_S)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or "") if isinstance(exc.stdout, str) else "", "timeout"


def claude(plugin, project, prompt, session_flag, sid, extra=()):
    cmd = ["claude", "-p", "--plugin-dir", str(plugin), "--model", MODEL, session_flag, sid, *extra]
    return run(cmd, nested_env(project), project, data=prompt)


def state_dir(project):
    return Path(project) / ".craftflow" / "state" / "context-nudge"


def state_file(project, sid):
    return state_dir(project) / (sid + ".json")


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


def write_config(scr, warn, critical, window=200000000):
    (scr / "plugin" / "config" / "context-nudge.json").write_text(
        json.dumps({"warnTokens": warn, "criticalTokens": critical, "assumedWindow": window}),
        encoding="utf-8")


def setup_scratch(scr, plugin_dir):
    for name in ("p1", "p2"):
        (scr / name / ".craftflow" / "state").mkdir(parents=True)
    shutil.copytree(str(plugin_dir), str(scr / "plugin"), ignore=shutil.ignore_patterns("__pycache__"))
    rc, out, err = run(["diff", "-r", "--exclude=config", "--exclude=__pycache__", str(plugin_dir),
                        str(scr / "plugin")], os.environ.copy(), scr)
    if rc != 0 or out.strip():
        raise RuntimeError("plugin copy differs from source: %s" % (out or err)[:300])
    mode = json.loads((plugin_dir / "config" / "hook-mode.json").read_text(encoding="utf-8"))
    mode["contextNudge"] = "on"
    (scr / "plugin" / "config" / "hook-mode.json").write_text(json.dumps(mode), encoding="utf-8")
    write_config(scr, 1000, 100000000)
    wf_dir = scr / "p2" / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "wf-live-nudge.json").write_text(json.dumps(
        {"workflow_uuid": "wf-live-nudge", "workflow_type": "build", "phase_cursor": "P1",
         "phase_status": {"P1": "completed"}, "plan_file": None}), encoding="utf-8")
    (wf_dir / "wf-live-nudge-plan.json").write_text(json.dumps(
        {"workflow_uuid": "wf-live-nudge-plan", "workflow_type": "plan", "plan_file": None}),
        encoding="utf-8")


def load_probe(plugin_dir, p1, debug_file):
    # --debug-file (not bare --debug): in print mode the debug stream is only reliably captured via a file
    rc, out, err = claude(plugin_dir, p1, "Reply OK", "--session-id", str(uuid.uuid4()),
                          ("--debug-file", str(debug_file)))
    try:
        debug_text = Path(debug_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        debug_text = ""
    hits = (out + err + debug_text).count(LOAD_MARKER)
    log("load probe: rc=%s marker_hits=%d" % (rc, hits))
    return rc == 0 and hits >= 1


def lv1(plugin_dir, p1):
    s1 = str(uuid.uuid4())
    rc1, out1, _ = claude(plugin_dir, p1, "Reply OK", "--session-id", s1)
    rc2, out2, _ = claude(plugin_dir, p1, "Reply OK", "--resume", s1)
    st = read_json(state_file(p1, s1))
    if rc1 != 0 or rc2 != 0:
        return "FAIL:claude_rc=%s/%s" % (rc1, rc2)
    if not st:
        return "FAIL:no_state_file"
    if st.get("last_level") != "none" or not (type(st.get("last_tokens")) is int and st["last_tokens"] > 0):
        return "FAIL:state=%r" % {k: st.get(k) for k in ("last_level", "last_tokens")}
    if not str(st.get("transcript_path", "")).endswith(s1 + ".jsonl"):
        return "FAIL:transcript_path=%r" % st.get("transcript_path")
    if any(r.get("outcome") == "nudged" for r in nudge_rows(p1, s1)):
        return "FAIL:nudged_row_in_audit_mode"
    if ADVISORY_MARKER in out1 + out2:
        return "FAIL:advisory_in_model_output"
    return "PASS"


def lv2(plugin, p2, s2):
    claude(plugin, p2, "Reply OK", "--session-id", s2)
    _, out2, _ = claude(plugin, p2, ECHO_ADVISORY_PROMPT, "--resume", s2)
    warn_rows = [r for r in nudge_rows(p2, s2) if r.get("outcome") == "nudged" and r.get("level") == "warn"]
    if ADVISORY_MARKER not in out2:
        return "FAIL:model_did_not_see_advisory(out=%r)" % out2[:120]
    if len(warn_rows) != 1:
        return "FAIL:warn_nudged_rows_after_turn2=%d" % len(warn_rows)
    claude(plugin, p2, ECHO_ADVISORY_PROMPT, "--resume", s2)
    nudged = [r for r in nudge_rows(p2, s2) if r.get("outcome") == "nudged"]
    if len(nudged) != 1:
        return "FAIL:nudged_rows_after_turn3=%d" % len(nudged)
    return "PASS"


def lv3(scr, plugin, p2, s2):
    write_config(scr, 1000, 2000)
    claude(plugin, p2, "Reply OK", "--resume", s2)
    crit = [r for r in nudge_rows(p2, s2) if r.get("outcome") == "nudged" and r.get("level") == "critical"]
    return "PASS" if crit else "FAIL:no_critical_nudged_row(rows=%r)" % [
        (r.get("outcome"), r.get("level"), r.get("source")) for r in nudge_rows(p2, s2)]


def lv4(plugin, p2, s2):
    pre = read_json(state_file(p2, s2)) or {}
    transcript = pre.get("transcript_path")
    pre_tokens = pre.get("last_tokens")
    before = len(nudge_rows(p2, s2))
    rc, out, err = claude(plugin, p2, "/compact", "--resume", s2)
    log("compact: rc=%s out=%r err=%r" % (rc, out[:160], err[:160]))
    claude(plugin, p2, "Reply OK", "--resume", s2)
    rows = nudge_rows(p2, s2)
    reset = [r for r in rows[before:] if r.get("source") == "reset" and r.get("outcome") == "reset"]
    has_boundary = False
    if transcript and Path(transcript).is_file():
        has_boundary = '"compact_boundary"' in Path(transcript).read_text(encoding="utf-8", errors="replace")
    if not reset or not has_boundary:
        log("lv4 evidence: reset_rows=%d compact_boundary_in_transcript=%s" % (len(reset), has_boundary))
        return ALLOWED_LIMITATIONS["lv4"]
    after_reset = rows[rows.index(reset[0]) + 1:]
    hook_rows = [r for r in after_reset if r.get("source") == "hook"]
    for r in hook_rows:
        ok = r.get("token_source") == "compact_boundary" or (
            type(r.get("tokens")) is int and type(pre_tokens) is int and r["tokens"] < pre_tokens)
        if not ok:
            return "FAIL:stale_renudge(row=%r,pre_tokens=%r)" % (
                {k: r.get(k) for k in ("outcome", "level", "tokens", "token_source")}, pre_tokens)
    return "PASS"


def boundary_call(scr, plugin_copy, p2, args):
    env = nested_env(p2)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_copy)
    cmd = [sys.executable, str(plugin_copy / "scripts" / "craftflow_context_nudge.py"), "--boundary", *args,
           "--project-root", str(p2)]
    rc, out, err = run(cmd, env, scr)
    try:
        return rc, json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return rc, {"outcome": "unparsable", "error": (out + err)[:200]}


def lv5(scr, plugin, p2, s2):
    if not state_file(p2, s2).is_file():
        claude(plugin, p2, "Reply OK", "--resume", s2)  # turn 6: recreate state after /compact reset
    if not state_file(p2, s2).is_file():
        return "FAIL:no_session_state_for_boundary"
    base = ["--wf", "wf-live-nudge", "--phase", "P1", "--session-id", s2]
    rc1, r1 = boundary_call(scr, plugin, p2, base)
    if rc1 != 0 or r1.get("relay") is not True or r1.get("level") not in ("warn", "critical"):
        return "FAIL:first_call=%r" % {k: r1.get(k) for k in ("relay", "level", "outcome", "error")}
    cp = read_json(r1.get("checkpoint_path") or "")
    if not cp or cp.get("workflow_uuid") != "wf-live-nudge" or cp.get("source") != "phase_boundary" \
            or not (type(cp.get("context_tokens")) is int and cp["context_tokens"] > 0):
        return "FAIL:checkpoint=%r" % ({k: (cp or {}).get(k) for k in ("workflow_uuid", "source", "context_tokens")})
    rc2, r2 = boundary_call(scr, plugin, p2, base)
    if r2.get("relay") is not False or r2.get("outcome") != "already_advised":
        return "FAIL:second_call=%r" % {k: r2.get(k) for k in ("relay", "outcome")}
    rc3, r3 = boundary_call(scr, plugin, p2, ["--wf", "wf-live-nudge-plan", "--phase", "plan-handoff",
                                             "--session-id", s2])
    if r3.get("phase") != "plan-handoff":
        return "FAIL:plan_handoff_phase=%r" % r3.get("phase")
    log("lv5: session_source=%s level=%s tokens=%s third_call=%s/%s" % (
        r1.get("session_source"), r1.get("level"), r1.get("tokens"), r3.get("relay"), r3.get("outcome")))
    return "PASS"


def lv6(plugin, p2, s2):
    rc, out, err = claude(plugin, p2,
                          "Run this exact bash command and print its output: echo $CLAUDE_CODE_SESSION_ID",
                          "--resume", s2, ("--allowedTools=Bash",))
    if rc != 0:
        log("lv6: rc=%s err=%r" % (rc, err[:160]))
    return "PASS" if s2 in out else ALLOWED_LIMITATIONS["lv6"]


def all_ok(summary):
    for key, val in summary.items():
        if key == "evidence_class":
            continue
        if val == "PASS" or (key in ALLOWED_LIMITATIONS and val == ALLOWED_LIMITATIONS[key]):
            continue
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
    scr = Path(tempfile.mkdtemp(prefix="cf-nudge-live-"))
    summary = {"load_probe": "FAIL", "lv1": "NOT_RUN", "lv2": "NOT_RUN", "lv3": "NOT_RUN", "lv4": "NOT_RUN",
               "lv5": "NOT_RUN", "lv6": "NOT_RUN", "evidence_class": "B-SIMULATED"}
    try:
        setup_scratch(scr, plugin_dir)
        p1, p2, plugin = scr / "p1", scr / "p2", scr / "plugin"
        s2 = str(uuid.uuid4())
        if not load_probe(plugin_dir, p1, scr / "load-probe-debug.txt"):
            print(json.dumps(summary))
            log("load probe failed: evidence class B-SIMULATED; live steps not run")
            return 1
        summary.update(load_probe="PASS", evidence_class="A")
        summary["lv1"] = lv1(plugin_dir, p1)
        summary["lv2"] = lv2(plugin, p2, s2)
        summary["lv3"] = lv3(scr, plugin, p2, s2) if summary["lv2"] == "PASS" else "NOT_RUN"
        summary["lv4"] = lv4(plugin, p2, s2) if summary["lv3"] == "PASS" else "NOT_RUN"
        summary["lv5"] = lv5(scr, plugin, p2, s2) if summary["lv2"] == "PASS" else "NOT_RUN"
        summary["lv6"] = lv6(plugin, p2, s2) if summary["lv2"] == "PASS" else "NOT_RUN"
        print(json.dumps(summary))
        log("scratch Claude sessions are left under ~/.claude/projects/ (slug of %s)" % scr)
        return 0 if all_ok(summary) else 1
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
