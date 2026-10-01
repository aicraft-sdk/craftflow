#!/usr/bin/env python3
"""Class-A live proof driver for the stop-gate continue ACT (SPEC-0019 / ADR-0056), scenarios LA-1..LA-8.

Runs real `claude -p --plugin-dir <plugin> --model haiku` sessions in scratch git projects:
  LA-1 shipped plugin (mode off) is inert         LA-2 helper `--session-id` equals the `--session-id` uuid
  LA-3 unarmed `on` never acts (A02, one row)     LA-4 armed: [acted true] then [H16, acted false]
  LA-5 max 2 with advance_cursor.py: acted [T,T,F], third row H17, stop_hook_active [F,T,T] (2 retries)
  LA-6 outward next phase: H10, never acts        LA-7 model touches the consent file: arm_after_last_human
  LA-8 RECORDED: stop_reason values and whether prompt_id stays stable across a chain

Scratch setup (disclosed LIMITATIONs act_requires_consent_file, act_go_from_seeded_events,
jev_stub_endpoint_test_allowance): a scratch plugin copy whose Stop hook command runs the real hook under a
wrapper that points the passwd home (consent file AND user layer) at a scratch home, sets HOME to it inside the
hook process only, and pins the Jev client's passwd home to that scratch home, where a jev-endpoint.json names the
loopback stub (the endpoint env override no longer exists, so A09 does not fire). The arm entry is written by the real arm CLI
(`craftflow_stop_gate_arm.arm`) against SEEDED GO events. A loopback stub answers phase_done 0.97 / needs_human
0.05; the key is fake. The real consent file (passwd home .claude/craftflow/stop-gate.json) is never written
or parsed: presence and sha256 are recorded before and after (real_consent_unchanged).

`--timing-only --runs N` skips claude: it runs the wrapped hook N times against fresh armed projects and reports
the ACT hook_ms distribution (the p90 <= 1500 gate).

Prints one summary JSON line on stdout; exit 0 only when la1..la7 PASS, la8 RECORDED and every guard holds.

Run: python3 tests/live/stop_gate_act_roundtrip.py --plugin-dir <plugin dir> [--keep] [--timing-only --runs 20]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stop_gate_roundtrip as base  # noqa: E402  (Slice-1 driver: process, fixture and observation helpers)

WF = "wf-live-act-0001"
HOOK_P90_GATE_MS = 1500
LA5_ATTEMPTS = 3  # first run + up to 2 retries (R9)
LIMITATIONS = ["act_requires_consent_file", "act_go_from_seeded_events", "jev_stub_endpoint_test_allowance"]
FORBIDDEN_TOOL_WORDS = ("git push", "gh pr", "merge")
PROMPT_P1 = ("Reply exactly: Phase P1 of craftflow workflow %s is done and checks pass. "
             "Shall I continue to Phase P2?" % WF)
LA5_STANDING = ("\nThis is a scripted hook test. Standing instruction: whenever a hook message tells you to continue "
                "with a phase, do NOT load skills, read files or plan; only run this Bash command: python3 %s %s "
                "-- then reply with exactly the one line it prints, nothing else.")
PHASES = ("P1", "P2", "P3", "P4", "P5")
TITLES = {"P1": "Parser", "P2": "Formatter", "P3": "Linter", "P4": "Reporter", "P5": "Writer"}
LOOPBACK_ANSWER = {"answers": {"stop_kind": {"choice": "phase_done_awaiting_continue", "confidence": 0.97},
                               "needs_human": {"noul": 0.05}},
                   "usage": {"input_tokens": 1, "output_tokens": 1}, "model": "jev-latest"}


def log(msg):
    print(msg, file=sys.stderr)


# --- Jev loopback stub (phase_done 0.97 / needs_human 0.05) ------------------------------------------
class ActStub:
    def __init__(self):
        self.count = 0
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                with stub._lock:
                    stub.count += 1
                body = json.dumps(LOOPBACK_ANSWER).encode("utf-8")
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return "http://127.0.0.1:%d/v1/systemone" % self.server.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()


# --- fixtures ----------------------------------------------------------------------------------------
def act_wrapper_command(plugin):
    """Stop-gate hook command for the scratch plugin. SG_ACT_HOME (env) is the scratch passwd home."""
    code = ("import os, sys; sys.path.insert(0, '%s'); import craftflow_stop_gate as g; "
            "import craftflow_stop_gate_core as c; h = os.environ['SG_ACT_HOME']; "
            "g._consent_home = lambda: h; c.passwd_home = lambda: h; os.environ['HOME'] = h; "
            "import craftflow_jev_client as j; j._passwd_home = lambda: h; raise SystemExit(g.main())"
            % (plugin / "scripts"))
    return 'python3 -c "%s"' % code


def make_act_plugin(scr, plugin_dir):
    """Scratch plugin copy: wrapped stop-gate hook, an extra record-only probe Stop hook, Jev enabled."""
    plugin = scr / "plugin-act"
    base.copy_plugin(plugin_dir, plugin)
    hooks_path = plugin / "hooks" / "hooks.json"
    if base.patch_stop_gate_command(hooks_path, act_wrapper_command(plugin)) != 1:
        raise RuntimeError("could not patch scratch hooks.json")
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    hooks["hooks"]["Stop"].append({"hooks": [{
        "type": "command", "timeout": 5,
        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/tests/live/probes/stop_probe.py" "$SG_ACT_PROBE_LOG" --record'}]})
    hooks_path.write_text(json.dumps(hooks, indent=1), encoding="utf-8")
    jev_cfg = json.loads((plugin / "config" / "jev.json").read_text(encoding="utf-8"))
    jev_cfg["enabled"] = True
    jev_cfg["features"] = {"routingHint": "off", "skillHint": "off", "remediationScope": "off", "riskGate": "off"}
    base.write_json(plugin / "config" / "jev.json", jev_cfg)
    return plugin


def make_act_project(scr, name, sid, p2_title=None):
    project = scr / name
    (project / ".craftflow" / "state" / "workflows").mkdir(parents=True)
    (project / ".gitignore").write_text(".craftflow/\n", encoding="utf-8")
    (project / "README.md").write_text("scratch\n", encoding="utf-8")
    base.git(project, "init", "-q")
    base.git(project, "add", ".gitignore", "README.md")
    base.git(project, "commit", "-q", "-m", "init")
    titles = dict(TITLES, P2=p2_title or TITLES["P2"])
    artifact = {
        "workflow_uuid": WF, "workflow_type": "BUILD", "session_id": sid, "phase_cursor": "P2",
        "phase_status": dict({"P1": "completed"}, **{p: "pending" for p in PHASES[1:]}),
        "normalized_phases": [{"phase_id": p, "title": titles[p], "files": ["f.py"], "checkpoint_type": "none"}
                              for p in PHASES],
        "plan_file": "docs/plans/live-act-plan.md", "pending_gate": None}
    (project / ".craftflow" / "state" / "workflows" / (WF + ".json")).write_text(json.dumps(artifact),
                                                                                 encoding="utf-8")
    return project


def seed_go(scr, name):
    """(events path, transcripts root) holding 5 sessions x 10 labelled schema-2 would_continue Jev rows."""
    import craftflow_stop_gate_core as core
    folder = scr / name
    troot = folder / "transcripts"
    troot.mkdir(parents=True)
    rows = []
    for s in range(5):
        sid = "sess-%d" % s
        lines = [json.dumps({"type": "user", "timestamp": "2026-10-01T07:00:00Z",
                             "message": {"role": "user", "content": "start"}})]
        for m in range(10):
            row_ts, reply_ts = "2026-10-01T08:%02d:00Z" % (m * 2), "2026-10-01T08:%02d:30Z" % (m * 2)
            lines.append(json.dumps({"type": "assistant", "timestamp": row_ts, "message": {
                "role": "assistant", "content": [{"type": "text", "text": "phase done. continue?"}]}}))
            lines.append(json.dumps({"type": "user", "timestamp": reply_ts,
                                     "message": {"role": "user", "content": "yes"}}))
            rows.append(core.build_row(row_kind="stop", ts=row_ts, session_id=sid,
                                       transcript_path="/gone/" + sid + ".jsonl", verdict="would_continue",
                                       jev_status="ok", hook_ms=100))
        (troot / (sid + ".jsonl")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    events = folder / "events.jsonl"
    events.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return events, troot


def consent_path(home):
    return Path(home) / ".claude" / "craftflow" / "stop-gate.json"


def write_consent(home, obj):
    path = consent_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(path.parent), 0o700)
    path.write_text(json.dumps(obj), encoding="utf-8")
    os.chmod(str(path), 0o600)


def arm_project(scr, plugin_dir, home, project, name, budget=None, armed=True):
    """Scratch consent file {mode: on, jevText: true[, max]} and, when armed, the entry written by the REAL arm CLI."""
    consent = {"mode": "on", "jevText": True}
    if budget is not None:
        consent["maxAutoContinuesPerSession"] = budget
    write_consent(home, consent)
    if not armed:
        return None
    sys.path.insert(0, str(plugin_dir / "scripts"))
    import craftflow_stop_gate_arm as arm
    events, troot = seed_go(scr, "go-" + name)
    root = os.path.realpath(str(project))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = arm.main(["arm", "--workflow", WF, "--hours", "8", "--events", str(events),
                         "--transcripts-root", str(troot)],
                        env={"CLAUDE_PROJECT_DIR": root, "CLAUDE_PLUGIN_ROOT": str(plugin_dir)}, home=str(home),
                        tty=True, confirm=lambda _prompt: os.path.basename(root), now=time.time(), cwd=root)
    out = json.loads(buf.getvalue().strip() or "{}")
    if code != 0 or out.get("armed") is not True:
        raise RuntimeError("arm CLI failed: code=%s out=%r" % (code, out))
    time.sleep(2)  # the consent ctime must be strictly before the human line the next prompt creates
    return out


def act_env(home, probe_log, stub_url):
    return {"SG_ACT_HOME": str(home), "SG_ACT_PROBE_LOG": str(probe_log), "TYPESAFE_API_KEY": "fake-live-proof-key"}


def write_jev_endpoint(home, stub_url):
    """Loopback stub endpoint file in the ISOLATED scratch passwd home (never the real ~/.claude/craftflow)."""
    path = Path(home) / ".claude" / "craftflow" / "jev-endpoint.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"endpoint": stub_url}), encoding="utf-8")
    os.chmod(str(path), 0o600)


def act_claude(plugin, project, prompt, sid, home, probe_log, stub_url, allowed=None):
    write_jev_endpoint(home, stub_url)
    env = base.nested_env(project, project / "unused-seam.json", act_env(home, probe_log, stub_url))
    env.pop("CRAFTFLOW_STOP_GATE_USER_CONFIG", None)  # the user layer must be the passwd-home file (A14)
    cmd = ["claude", "-p", "--plugin-dir", str(plugin), "--model", base.MODEL, "--session-id", sid]
    if allowed:
        cmd.append("--allowedTools=" + allowed)
    return base.run(cmd, env, project, data=prompt)


def sid_rows(project, sid):
    return [r for r in base.gate_rows(project) if r.get("session_id") == sid]


def tool_commands(sid):
    out = []
    for rec in base.transcript_records(sid):
        if rec.get("type") == "assistant":
            for block in base._blocks(rec):
                if block.get("type") == "tool_use":
                    out.append(json.dumps(block.get("input")))
    return out


def reason_lines(sid, needle):
    return [r for r in base.transcript_records(sid) if r.get("type") == "user"
            and needle in json.dumps(r.get("message"))]


def brief(row):
    return {k: row.get(k) for k in ("acted", "arm_status", "rule_hits", "act_blockers", "stop_hook_active",
                                    "continues_since_human", "stop_reason", "verdict", "jev_status", "hook_ms")}


# --- scenarios ---------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, scr, plugin_dir, plugin, stub):
        self.scr, self.plugin_dir, self.plugin, self.stub = scr, plugin_dir, plugin, stub
        self.acted_hook_ms = []
        self.la5_rows = []
        self.la5_sid = None
        self.la5_probe = None
        self.la5_transcript_prompt_ids = []

    def fresh(self, name, p2_title=None):
        sid = str(uuid.uuid4())
        project = make_act_project(self.scr, name, sid, p2_title)
        home = self.scr / ("home-" + name)
        home.mkdir()
        return sid, project, home, self.scr / (name + "-probe.jsonl")

    def run(self, name, prompt, budget=None, armed=True, allowed=None, p2_title=None, extra_prompt=None):
        sid, project, home, probe = self.fresh(name, p2_title)
        arm_project(self.scr, self.plugin_dir, home, project, name, budget, armed)
        text = prompt if extra_prompt is None else prompt + extra_prompt(project)
        rc, out, err = act_claude(self.plugin, project, text, sid, home, probe, self.stub.url, allowed)
        log("%s: rc=%s out=%r err=%r" % (name, rc, out[:120], err[:80]))
        return sid, project, home, probe, rc


def la1(plugin_dir, scr):
    sid = str(uuid.uuid4())
    project = make_act_project(scr, "la1", sid)
    return base.lv1(plugin_dir, project, scr / "absent-user-config.json")


def la2(plugin_dir, scr):
    sid = str(uuid.uuid4())
    project = make_act_project(scr, "la2", sid)
    helper = Path(plugin_dir) / "scripts" / "craftflow_workflow_id.py"
    cmd = "python3 %s --session-id" % helper
    prompt = "Run exactly this Bash command and reply with only its stdout, nothing else: %s" % cmd
    rc, out, _err = base.claude(plugin_dir, project, scr / "absent-user-config.json", prompt, sid,
                                ("--allowedTools=Bash(python3:*)",))
    results = []
    for rec in base.transcript_records(sid):
        if rec.get("type") == "user":
            for block in base._blocks(rec):
                if block.get("type") == "tool_result":
                    content = block.get("content")
                    results.append(content.strip() if isinstance(content, str) else json.dumps(content))
    log("la2: rc=%s sid=%s tool_results=%r out=%r" % (rc, sid, results, out[:80]))
    if rc != 0:
        return "FAIL:claude_rc=%s" % rc
    return "PASS" if sid in results else "FAIL:helper_output=%r" % results


def la3(ctx):
    sid, project, _home, _probe, rc = ctx.run("la3", PROMPT_P1, armed=False)
    rows = sid_rows(project, sid)
    if rc != 0 or len(rows) != 1:
        return "FAIL:rc=%s rows=%d" % (rc, len(rows))
    row = rows[0]
    if row.get("acted") is not False or "A02_not_armed" not in (row.get("act_blockers") or []) \
            or row.get("arm_status") != "not_armed" or "A05_not_jev_verdict" in (row.get("act_blockers") or []):
        return "FAIL:row=%r" % brief(row)
    if base.assistant_text_turns(sid) != 1:
        return "FAIL:assistant_turns=%d" % base.assistant_text_turns(sid)
    return "PASS"


@contextlib.contextmanager
def frozen_artifact(project):
    """Read-only workflows dir and artifact, so the continued model cannot rewrite the approved plan state
    (the user's global allow rules let a headless model write .craftflow/): no progress is then deterministic."""
    folder = Path(project) / ".craftflow" / "state" / "workflows"
    artifact = folder / (WF + ".json")
    os.chmod(str(artifact), 0o444)
    os.chmod(str(folder), 0o555)
    try:
        yield
    finally:
        os.chmod(str(folder), 0o755)
        os.chmod(str(artifact), 0o644)


def la4(ctx):
    sid, project, home, probe = ctx.fresh("la4")
    arm_project(ctx.scr, ctx.plugin_dir, home, project, "la4")
    with frozen_artifact(project):
        rc, out, _err = act_claude(ctx.plugin, project, PROMPT_P1, sid, home, probe, ctx.stub.url)
    log("la4: rc=%s out=%r" % (rc, out[:120]))
    rows = sid_rows(project, sid)
    # Rows after the chain (a later stop_hook_active=false stop, e.g. a background-task notification turn) may
    # exist; the contract is [acted, H16-stop] first and exactly one acted row overall.
    if rc != 0 or len(rows) < 2:
        return "FAIL:rc=%s rows=%d %r" % (rc, len(rows), [brief(r) for r in rows])
    first, second = rows[0], rows[1]
    if [r.get("acted") for r in rows].count(True) != 1:
        return "FAIL:acted_rows=%r" % [r.get("acted") for r in rows]
    if not (first.get("acted") is True and first.get("rule_hits") == [] and first.get("act_blockers") == []
            and first.get("binding_reason") == "session_match" and first.get("arm_status") == "armed"
            and first.get("continues_since_human") == 1 and first.get("verdict") == "would_continue"):
        return "FAIL:first=%r" % brief(first)
    if second.get("acted") is not False or "H16_no_progress" not in (second.get("rule_hits") or []):
        return "FAIL:second=%r" % brief(second)
    n_lines = len(reason_lines(sid, "auto-continue 1/5"))
    if n_lines != 1:
        return "FAIL:auto_continue_lines=%d" % n_lines
    bad = [c for c in tool_commands(sid) if any(w in c for w in FORBIDDEN_TOOL_WORDS)]
    if bad:
        return "FAIL:forbidden_tool_use=%r" % bad[:2]
    ctx.acted_hook_ms.append(first.get("hook_ms"))
    return "PASS"


def la5_once(ctx, attempt):
    adv = Path(ctx.plugin) / "tests" / "live" / "probes" / "advance_cursor.py"
    sid, project, _home, probe, rc = ctx.run(
        "la5-%d" % attempt, PROMPT_P1, budget=2, allowed="Bash(python3:*)",
        extra_prompt=lambda proj: LA5_STANDING % (adv, proj))
    rows = sid_rows(project, sid)
    log("la5 attempt %d: rc=%s rows=%r" % (attempt, rc, [brief(r) for r in rows]))
    ctx.la5_rows, ctx.la5_sid, ctx.la5_probe = rows, sid, probe
    if rc != 0 or len(rows) != 3:
        return "FAIL:rc=%s rows=%d" % (rc, len(rows))
    acted, active = [r.get("acted") for r in rows], [r.get("stop_hook_active") for r in rows]
    if acted != [True, True, False]:
        return "FAIL:acted=%r" % acted
    if active != [False, True, True]:
        return "FAIL:stop_hook_active=%r" % active
    if "H17_continue_budget" not in (rows[2].get("rule_hits") or []):
        return "FAIL:third_rule_hits=%r" % rows[2].get("rule_hits")
    bad = [c for c in tool_commands(sid) if any(w in c for w in FORBIDDEN_TOOL_WORDS)]
    if bad:
        return "FAIL:forbidden_tool_use=%r" % bad[:2]
    ctx.acted_hook_ms.extend(r.get("hook_ms") for r in rows[:2])
    return "PASS"


def la5(ctx):
    result = "FAIL:not_run"
    for attempt in range(1, LA5_ATTEMPTS + 1):
        result = la5_once(ctx, attempt)
        if result == "PASS":
            return result
        log("la5 attempt %d failed: %s" % (attempt, result))
    return result


def la6(ctx):
    sid, project, _home, _probe, rc = ctx.run("la6", PROMPT_P1, p2_title="Publish release notes")
    rows = sid_rows(project, sid)
    if rc != 0 or len(rows) != 1:
        return "FAIL:rc=%s rows=%d" % (rc, len(rows))
    row = rows[0]
    if row.get("acted") is not False or "H10_next_phase_outward" not in (row.get("rule_hits") or []):
        return "FAIL:row=%r" % brief(row)
    return "PASS" if base.assistant_text_turns(sid) == 1 else "FAIL:assistant_turns"


def la7(ctx):
    sid, project, home, probe = ctx.fresh("la7")
    arm_project(ctx.scr, ctx.plugin_dir, home, project, "la7")
    # Claude Code refuses `touch` on .claude/ paths as a sensitive file, so the touch is a plain utime call
    # (same effect on st_ctime, which is what the arm rule reads).
    cmd = "python3 -c \"import os; os.utime('%s')\"" % consent_path(home)
    prompt = "First run exactly this Bash command: %s -- then %s" % (cmd, PROMPT_P1)
    rc, out, err = act_claude(ctx.plugin, project, prompt, sid, home, probe, ctx.stub.url, "Bash(python3:*)")
    log("la7: rc=%s out=%r err=%r" % (rc, out[:120], err[:80]))
    rows = sid_rows(project, sid)
    log("la7 rows: %r tools=%r" % ([brief(r) for r in rows], tool_commands(sid)))
    if rc != 0 or len(rows) != 1:
        return "FAIL:rc=%s rows=%d" % (rc, len(rows))
    row = rows[0]
    if row.get("acted") is not False or row.get("arm_status") != "arm_after_last_human" \
            or "A02_not_armed" not in (row.get("act_blockers") or []):
        return "FAIL:row=%r" % brief(row)
    if not any("utime" in c for c in tool_commands(sid)):
        return "FAIL:model_did_not_touch"
    return "PASS"


def la8(ctx):
    """RECORDED: stop_reason values and prompt_id stability across the LA-5 chain (no judgement)."""
    reasons = sorted({str(r.get("stop_reason")) for r in ctx.la5_rows})
    probe_rows = []
    for line in base.read_lines(ctx.la5_probe or ""):
        try:
            probe_rows.append(json.loads(line))
        except ValueError:
            continue
    payload_ids = [p.get("prompt_id") for p in probe_rows]
    prompt_ids = [r.get("promptId") for r in base.transcript_records(ctx.la5_sid or "")
                  if r.get("type") == "user" and r.get("promptId")]
    if any(payload_ids):
        stable = "true" if len(set(payload_ids)) == 1 else "false"
    else:
        stable = "unobserved"
    transcript_stable = ("unobserved" if not prompt_ids
                         else "true" if len(set(prompt_ids)) == 1 else "false")
    keys = sorted({k for p in probe_rows for k in p.get("payload_keys", [])})
    log("la8: reasons=%r payload_ids=%r transcript_prompt_ids=%d distinct=%d keys=%r" % (
        reasons, payload_ids, len(prompt_ids), len(set(prompt_ids)), keys))
    return {"la8": "RECORDED" if ctx.la5_rows else "NOT_RUN", "stop_reason_values": reasons,
            "prompt_id_in_payload": any(payload_ids), "prompt_id_stable_payload": stable,
            "prompt_id_stable_transcript": transcript_stable, "stop_payload_keys": keys}


def p90(values):
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return None
    return vals[min(len(vals) - 1, int(round(0.9 * (len(vals) - 1) + 0.4999)))]


def all_ok(summary):
    for i in range(1, 8):
        if summary.get("la%d" % i) != "PASS":
            return False
    return (summary.get("la8") == "RECORDED" and summary.get("real_consent_unchanged") is True
            and summary.get("plugin_config_unchanged") is True and summary.get("load_probe") == "PASS"
            and isinstance(summary.get("act_hook_ms_p90"), (int, float))
            and summary["act_hook_ms_p90"] <= HOOK_P90_GATE_MS)


# --- model-free ACT timing ---------------------------------------------------------------------------
def timing_only(plugin_dir, scr, runs):
    plugin = make_act_plugin(scr, plugin_dir)
    ms = []
    with ActStub() as stub:
        for i in range(runs):
            name = "t%02d" % i
            sid = str(uuid.uuid4())
            project = make_act_project(scr, name, sid)
            home = scr / ("home-" + name)
            home.mkdir()
            arm_project(scr, plugin_dir, home, project, name)
            transcript = scr / (name + "-t.jsonl")
            ts = time.time()
            stamp = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"  # noqa: E731
            text = "Phase P1 of craftflow workflow %s is done and checks pass. Shall I continue to Phase P2?" % WF
            transcript.write_text(
                json.dumps({"type": "user", "timestamp": stamp(ts + 5),
                            "message": {"role": "user", "content": "go " + WF}}) + "\n"
                + json.dumps({"type": "assistant", "timestamp": stamp(ts + 7), "message": {
                    "role": "assistant", "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": text}]}}) + "\n", encoding="utf-8")
            payload = json.dumps({"hook_event_name": "Stop", "session_id": sid, "transcript_path": str(transcript),
                                  "cwd": str(project), "stop_hook_active": False,
                                  "last_assistant_message": text}).encode("utf-8")
            env = {k: v for k, v in os.environ.items() if k not in base.STRIPPED_ENV
                   and k != "CRAFTFLOW_STOP_GATE_USER_CONFIG"}
            env.update(act_env(home, scr / "p.jsonl", stub.url))
            env.update(CLAUDE_PLUGIN_ROOT=str(plugin), CLAUDE_PROJECT_DIR=str(project))
            subprocess.run(act_wrapper_command(plugin), shell=True, input=payload, env=env,  # noqa: S602 - fixed cmd
                           cwd=str(project), capture_output=True, timeout=30)
            rows = sid_rows(project, sid)
            if len(rows) != 1 or rows[0].get("acted") is not True:
                return {"error": "timing_run_did_not_act", "run": i, "rows": [brief(r) for r in rows]}, 1
            ms.append(rows[0]["hook_ms"])
    result = {"runs": runs, "act_hook_ms_median": statistics.median(ms), "act_hook_ms_p90": p90(ms),
              "act_hook_ms_max": max(ms), "act_gate_ms": HOOK_P90_GATE_MS,
              "load_avg_1m": round(os.getloadavg()[0], 2), "cpu_count": os.cpu_count()}
    return result, 0 if result["act_hook_ms_p90"] <= HOOK_P90_GATE_MS else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plugin-dir", required=True)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--timing-only", action="store_true")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--only", default="", help="debug: comma list of la3..la7 to run (summary is then partial)")
    args = ap.parse_args(argv)
    if not args.timing_only and shutil.which("claude") is None:
        print("SKIP: claude CLI not found", file=sys.stderr)
        return 1
    plugin_dir = Path(args.plugin_dir).resolve()
    sys.path.insert(0, str(plugin_dir / "scripts"))
    scr = Path(tempfile.mkdtemp(prefix="cf-act-"))
    summary = {"load_probe": "FAIL", **{"la%d" % i: "NOT_RUN" for i in range(1, 9)},
               "act_hook_ms_p90": None, "real_consent_unchanged": False, "plugin_config_unchanged": False,
               "evidence_class": "B-SIMULATED", "limitations": LIMITATIONS}
    cfg_before, consent_before = base.config_hashes(plugin_dir), base.real_consent_state()
    try:
        if args.timing_only:
            result, code = timing_only(plugin_dir, scr, args.runs)
            result["real_consent_unchanged"] = base.real_consent_state() == consent_before
            print(json.dumps(result))
            return code if result["real_consent_unchanged"] else 1
        absent = scr / "absent-user-config.json"
        p0 = make_act_project(scr, "p0", str(uuid.uuid4()))
        if not base.load_probe(plugin_dir, p0, absent, scr / "load-probe-debug.txt"):
            print(json.dumps(summary))
            log("load probe failed: evidence class B-SIMULATED; live steps not run")
            return 1
        summary.update(load_probe="PASS", evidence_class="A")
        summary["la1"] = la1(plugin_dir, scr)
        summary["la2"] = la2(plugin_dir, scr)
        plugin = make_act_plugin(scr, plugin_dir)
        with ActStub() as stub:
            ctx = Ctx(scr, plugin_dir, plugin, stub)
            only = [x for x in args.only.split(",") if x]
            for name, fn in (("la3", la3), ("la4", la4), ("la5", la5), ("la6", la6), ("la7", la7)):
                if not only or name in only:
                    summary[name] = fn(ctx)
            summary.update(la8(ctx))
            summary["jev_stub_requests"] = stub.count
        summary["act_hook_ms_p90"] = p90(ctx.acted_hook_ms)
        summary["acted_rows_hook_ms"] = ctx.acted_hook_ms
        summary["plugin_config_unchanged"] = base.config_hashes(plugin_dir) == cfg_before
        consent_after = base.real_consent_state()
        summary["real_consent_unchanged"] = consent_after == consent_before
        summary["real_consent_present"] = consent_before[0]
        summary["real_consent_sha256"] = consent_before[1]
        print(json.dumps(summary))
        log("scratch Claude sessions are left under ~/.claude/projects/ (slug of %s)" % scr)
        return 0 if all_ok(summary) else 1
    except Exception as exc:  # noqa: BLE001 - driver reports a clean FAIL row, never a traceback
        log("FAIL: unexpected error: %s: %s" % (type(exc).__name__, exc))
        summary["plugin_config_unchanged"] = base.config_hashes(plugin_dir) == cfg_before
        summary["real_consent_unchanged"] = base.real_consent_state() == consent_before
        print(json.dumps(summary))
        return 1
    finally:
        if args.keep:
            log("kept scratch dir: %s" % scr)
        else:
            shutil.rmtree(scr, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
