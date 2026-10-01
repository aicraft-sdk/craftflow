#!/usr/bin/env python3
"""Class-A live proof driver for the stop gate, shadow mode (SPEC-0018 / ADR-0055).

Runs real `claude -p --plugin-dir <plugin> --model haiku` sessions in scratch git projects and checks the
plan's Live Verification Strategy: a load probe, then LV-1 (shipped plugin is inert), LV-2 (audit writes one
shadow row, the agent still stops), LV-3 (hard rule wins), LV-4 (a repository-set text consent never egresses
to a loopback Jev stub), LV-5 (push relay through a TEST-ONLY consent override in a scratch plugin copy) and
LV-6 (stop_hook_active chain probe, recorded not judged).

Safety: Jev is only ever a loopback stub (reached via a jev-endpoint.json in an ISOLATED scratch home, injected
through a scratch-plugin hook wrapper; the env override no longer exists); the real TypeSafe API is never called. The
real consent file (passwd home .claude/craftflow/stop-gate.json) is never read for content or written: its
presence and sha256 are recorded before/after and must be equal. LV-5 points the hook's consent lookup at a
scratch home through a scratch-plugin hook command; the worktree plugin is never modified (config sha256
before/after -> plugin_config_unchanged).

Env isolation: nested `claude -p` gets os.environ minus CLAUDE_PLUGIN_ROOT, CLAUDE_CODE_SESSION_ID,
CLAUDE_PROJECT_DIR and CURSOR_PLUGIN_ROOT, plus CLAUDE_PROJECT_DIR=<scratch project> and
CRAFTFLOW_STOP_GATE_USER_CONFIG=<user file>. Prompts go on stdin. Never shell=True; 180 s per call.

Prints one summary JSON line on stdout. Exit 0 only when every step is PASS (lv5 may be the allowed
LIMITATION) and the config/consent guards hold; exit 1 otherwise or when the claude CLI is missing.

Run: python3 tests/live/stop_gate_roundtrip.py --plugin-dir <plugin dir> [--keep]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CALL_TIMEOUT_S = 180
MODEL = "haiku"
LOAD_MARKER = "from --plugin-dir overrides installed version"
STRIPPED_ENV = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR", "CURSOR_PLUGIN_ROOT")
WF = "wf-live-sg-0001"
PROMPT = ("Reply exactly: Phase P1 of craftflow workflow %s is done and checks pass. "
          "Shall I continue to Phase P2?" % WF)
LV5_LIMITATION = "LIMITATION:push_tool_unavailable_headless"
# Observed live: PushNotification is a deferred tool, so the relay turn first calls ToolSearch to load its
# schema. ToolSearch has no side effect, so it is tolerated (and logged); any other tool use fails LV-5.
RELAY_SCHEMA_TOOLS = ("ToolSearch",)
JEV_STUB_SLEEP_S = 5.0
STOP_GATE_HOOK_FRAGMENT = "craftflow_stop_gate.py"


def log(msg):
    print(msg, file=sys.stderr)


# --- process helpers ---------------------------------------------------------------------------------
def run(cmd, env, cwd, data=None):
    """(returncode, stdout, stderr); never shell=True; timeout -> returncode 124."""
    try:
        p = subprocess.run(cmd, input=data, env=env, cwd=str(cwd), capture_output=True, text=True,
                           timeout=CALL_TIMEOUT_S)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or "") if isinstance(exc.stdout, str) else "", "timeout"


def nested_env(project, user_file, extra=None):
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    env["CLAUDE_PROJECT_DIR"] = str(project)
    env["CRAFTFLOW_STOP_GATE_USER_CONFIG"] = str(user_file)
    env.update(extra or {})
    return env


def claude(plugin, project, user_file, prompt, sid, extra_args=(), extra_env=None):
    cmd = ["claude", "-p", "--plugin-dir", str(plugin), "--model", MODEL, "--session-id", sid, *extra_args]
    return run(cmd, nested_env(project, user_file, extra_env), project, data=prompt)


# --- fixtures ----------------------------------------------------------------------------------------
def git(project, *args):
    cmd = ["git", "-C", str(project), "-c", "user.name=live", "-c", "user.email=live@example.invalid", *args]
    rc, out, err = run(cmd, os.environ.copy(), project)
    if rc != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), (out + err)[:200]))


def make_project(scr, name, pending_gate=False):
    project = scr / name
    (project / ".craftflow" / "state" / "workflows").mkdir(parents=True)
    (project / ".gitignore").write_text(".craftflow/\n", encoding="utf-8")
    (project / "README.md").write_text("scratch\n", encoding="utf-8")
    git(project, "init", "-q")
    git(project, "add", ".gitignore", "README.md")
    git(project, "commit", "-q", "-m", "init")
    artifact = {
        "workflow_uuid": WF, "workflow_type": "build", "phase_cursor": "P2",
        "phase_status": {"P1": "completed", "P2": "pending"},
        "normalized_phases": [{"id": "P1", "title": "Parser"}, {"id": "P2", "title": "Formatter"}],
        "plan_file": "docs/plans/live-sg-plan.md",
        "pending_gate": "user_build_approval" if pending_gate else None,
    }
    (project / ".craftflow" / "state" / "workflows" / (WF + ".json")).write_text(
        json.dumps(artifact), encoding="utf-8")
    return project


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def copy_plugin(plugin_dir, dest):
    shutil.copytree(str(plugin_dir), str(dest), ignore=shutil.ignore_patterns("__pycache__"))


def config_hashes(plugin_dir):
    out = {}
    for p in sorted(Path(plugin_dir, "config").rglob("*")):
        if p.is_file():
            out[str(p.relative_to(plugin_dir))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def real_consent_state():
    """(present, sha256|None) of the REAL consent file; content is hashed, never parsed or printed."""
    try:
        import pwd
        home = pwd.getpwuid(os.getuid()).pw_dir
    except Exception:  # noqa: BLE001 - home unresolved: treated as absent
        return False, None
    path = os.path.join(home, ".claude", "craftflow", "stop-gate.json")
    if not os.path.lexists(path):
        return False, None
    try:
        return True, hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return True, "unreadable"


def real_consent_grants_jev_text():
    """True when the real consent file (if any) grants jevText: LV-4's negative proof is then not meaningful."""
    try:
        import pwd
        path = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".claude", "craftflow", "stop-gate.json")
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
        return isinstance(obj, dict) and obj.get("jevText") is True
    except Exception:  # noqa: BLE001 - absent or unreadable: does not grant
        return False


# --- observation helpers -----------------------------------------------------------------------------
def read_lines(path):
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def gate_rows(project):
    rows = []
    for line in read_lines(Path(project) / ".craftflow" / "state" / "stop-gate" / "events.jsonl"):
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def hook_log_gate_rows(project):
    rows = []
    for line in read_lines(Path(project) / ".craftflow" / "state" / "craftflow-hook-events.log"):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("event") == "plugin_stop_gate":
            rows.append(row)
    return rows


def transcript_records(sid):
    hits = glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "projects", "*", sid + ".jsonl"))
    if not hits:
        return []
    recs = []
    for line in read_lines(hits[0]):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            recs.append(rec)
    return recs


def _blocks(rec):
    content = (rec.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def assistant_text_turns(sid):
    """Number of assistant transcript records that carry a non-empty text block."""
    return sum(1 for r in transcript_records(sid)
               if r.get("type") == "assistant" and any(b.get("type") == "text" and (b.get("text") or "").strip()
                                                       for b in _blocks(r)))


def tool_uses(sid):
    return [b.get("name") for r in transcript_records(sid) if r.get("type") == "assistant"
            for b in _blocks(r) if b.get("type") == "tool_use"]


# --- Jev loopback stub (LV-4) ------------------------------------------------------------------------
class JevStub:
    """Loopback HTTP stub that records whether each request carried a stop_kind question, then sleeps."""

    def __init__(self, sleep_s):
        self.requests = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                with stub._lock:
                    stub.requests.append(b"stop_kind" in body)
                threading.Event().wait(sleep_s)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b"{}")
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

    @property
    def stop_kind_requests(self):
        with self._lock:
            return sum(1 for hit in self.requests if hit)


# --- steps -------------------------------------------------------------------------------------------
def load_probe(plugin_dir, p1, user_file, debug_file):
    rc, out, err = claude(plugin_dir, p1, user_file, "Reply OK", str(uuid.uuid4()),
                          ("--debug-file", str(debug_file)))
    try:
        debug_text = Path(debug_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        debug_text = ""
    hits = (out + err + debug_text).count(LOAD_MARKER)
    log("load probe: rc=%s marker_hits=%d" % (rc, hits))
    return rc == 0 and hits >= 1


def lv1(plugin_dir, p1, absent_user_file):
    sid = str(uuid.uuid4())
    rc, _out, _err = claude(plugin_dir, p1, absent_user_file, "Reply OK.", sid)
    if rc != 0:
        return "FAIL:claude_rc=%s" % rc
    if (p1 / ".craftflow" / "state" / "stop-gate").exists():
        return "FAIL:stop_gate_dir_exists"
    n = len(hook_log_gate_rows(p1))
    return "PASS" if n == 0 else "FAIL:plugin_stop_gate_rows=%d" % n


def lv2(plugin_dir, p2, user_audit):
    sid = str(uuid.uuid4())
    rc, out, _err = claude(plugin_dir, p2, user_audit, PROMPT, sid)
    rows = [r for r in gate_rows(p2) if r.get("session_id") == sid]
    if rc != 0:
        return "FAIL:claude_rc=%s" % rc
    if len(rows) != 1:
        return "FAIL:rows=%d(out=%r)" % (len(rows), out[:80])
    row = rows[0]
    want = {"binding_reason": "single_candidate", "rule_hits": [],
            "heuristic_kind": "phase_done_awaiting_continue", "verdict_source": "heuristic",
            "act_eligible": False}
    bad = {k: row.get(k) for k, v in want.items() if row.get(k) != v}
    if bad:
        return "FAIL:row_mismatch=%r(verdict=%r)" % (bad, row.get("verdict"))
    turns = assistant_text_turns(sid)
    if turns != 1:
        return "FAIL:assistant_turns=%d" % turns
    if not (isinstance(row.get("message_chars"), int) and row["message_chars"] > 0):
        return "FAIL:message_chars=%r" % row.get("message_chars")
    log("lv2: message_chars=%s verdict=%s hook_ms=%s out=%r" % (
        row["message_chars"], row.get("verdict"), row.get("hook_ms"), out[:100]))
    return "PASS"


def lv3(plugin_dir, p3, user_audit):
    sid = str(uuid.uuid4())
    rc, _out, _err = claude(plugin_dir, p3, user_audit, PROMPT, sid)
    rows = [r for r in gate_rows(p3) if r.get("session_id") == sid]
    if rc != 0 or len(rows) != 1:
        return "FAIL:rc=%s rows=%d" % (rc, len(rows))
    row = rows[0]
    if row.get("verdict") != "needs_human" or "H03_pending_gate" not in (row.get("rule_hits") or []):
        return "FAIL:verdict=%r rule_hits=%r" % (row.get("verdict"), row.get("rule_hits"))
    return "PASS"


def lv4(scr, plugin_dir, user_jev):
    if real_consent_grants_jev_text():
        return "LIMITATION:real_consent_file_grants_jevText"
    plugin = scr / "plugin-jev"
    copy_plugin(plugin_dir, plugin)
    jev_cfg = json.loads((plugin / "config" / "jev.json").read_text(encoding="utf-8"))
    jev_cfg["enabled"] = True
    jev_cfg["features"] = {"routingHint": "off", "skillHint": "off", "remediationScope": "off",
                           "riskGate": "off"}
    write_json(plugin / "config" / "jev.json", jev_cfg)
    p4 = make_project(scr, "p4")
    sid = str(uuid.uuid4())
    with JevStub(JEV_STUB_SLEEP_S) as stub:
        # The Jev client no longer honours an endpoint env var: it reads ~/.claude/craftflow/jev-endpoint.json
        # from the PASSWD home. Isolate that home: the scratch plugin's Stop hook runs the real gate under a
        # wrapper that points both the consent home and the client's home at a scratch dir holding only the
        # loopback endpoint file (no consent file -> the gate must not egress). The real home is never written.
        jev_home = scr / "jev-home"
        write_json(jev_home / ".claude" / "craftflow" / "jev-endpoint.json", {"endpoint": stub.url})
        os.chmod(str(jev_home / ".claude" / "craftflow" / "jev-endpoint.json"), 0o600)
        if patch_stop_gate_command(plugin / "hooks" / "hooks.json", jev_wrapper_command(plugin, jev_home)) != 1:
            return "FAIL:could_not_patch_scratch_hooks"
        rc, _out, _err = claude(plugin, p4, user_jev, PROMPT, sid, extra_env={
            "TYPESAFE_API_KEY": "fake-live-proof-key"})
        stop_kind_hits, total = stub.stop_kind_requests, len(stub.requests)
    rows = [r for r in gate_rows(p4) if r.get("session_id") == sid]
    log("lv4: stub total_requests=%d stop_kind_requests=%d" % (total, stop_kind_hits))
    if rc != 0 or len(rows) != 1:
        return "FAIL:rc=%s rows=%d" % (rc, len(rows))
    row = rows[0]
    if stop_kind_hits != 0:
        return "FAIL:stop_kind_requests=%d" % stop_kind_hits
    if row.get("jev_status") != "not_consented" or row.get("jev_text_source") != "ignored_seam":
        return "FAIL:jev_status=%r jev_text_source=%r" % (row.get("jev_status"), row.get("jev_text_source"))
    if not (isinstance(row.get("hook_ms"), int) and row["hook_ms"] < 1000):
        return "FAIL:hook_ms=%r" % row.get("hook_ms")
    return "PASS"


def jev_wrapper_command(plugin, home):
    """Stop-gate hook command (SCRATCH plugin only) pinning the consent home AND the Jev client's passwd home."""
    return ('python3 -c "import sys; sys.path.insert(0, \'%s\'); import craftflow_stop_gate as g; '
            'import craftflow_jev_client as j; g._consent_home=lambda: \'%s\'; j._passwd_home=lambda: \'%s\'; '
            'raise SystemExit(g.main())"' % (plugin / "scripts", home, home))


def relay_wrapper_command(plugin, consent_home):
    return ('python3 -c "import sys; sys.path.insert(0, \'%s\'); import craftflow_stop_gate as g; '
            'g._consent_home=lambda: \'%s\'; raise SystemExit(g.main())"' % (plugin / "scripts", consent_home))


def patch_stop_gate_command(hooks_path, new_command):
    """Replace the stop-gate Stop hook command in a SCRATCH hooks.json; returns the number replaced."""
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    replaced = 0
    for group in hooks["hooks"]["Stop"]:
        for hook in group.get("hooks", []):
            if STOP_GATE_HOOK_FRAGMENT in hook.get("command", ""):
                hook["command"] = new_command
                replaced += 1
    hooks_path.write_text(json.dumps(hooks, indent=1), encoding="utf-8")
    return replaced


def lv5(scr, plugin_dir, user_relay):
    plugin = scr / "plugin-relay"
    copy_plugin(plugin_dir, plugin)
    consent_home = scr / "consent-home"
    write_json(consent_home / ".claude" / "craftflow" / "stop-gate.json", {"notify": "push"})
    if patch_stop_gate_command(plugin / "hooks" / "hooks.json", relay_wrapper_command(plugin, consent_home)) != 1:
        return "FAIL:could_not_patch_scratch_hooks"
    p5 = make_project(scr, "p5", pending_gate=True)
    sid = str(uuid.uuid4())
    rc, out, err = claude(plugin, p5, user_relay, PROMPT, sid, ("--allowedTools=PushNotification",))
    rows = [r for r in gate_rows(p5) if r.get("session_id") == sid]
    tools = tool_uses(sid)
    log("lv5: rc=%s rows=%r tools=%r out=%r err=%r" % (
        rc, [(r.get("row_kind"), r.get("verdict"), r.get("notify_status")) for r in rows], tools,
        out[:160], err[:120]))
    relay_recs = [r for r in transcript_records(sid)
                  if r.get("type") == "user" and "craftflow stop-gate:" in json.dumps(r.get("message"))]
    if relay_recs:
        rec = relay_recs[0]
        log("lv5: relay record shape: keys=%s isMeta=%r content_type=%s" % (
            sorted(rec.keys()), rec.get("isMeta"), type((rec.get("message") or {}).get("content")).__name__))
    kinds = [(r.get("row_kind"), r.get("verdict"), r.get("notify_status")) for r in rows]
    if ("stop", "needs_human", "relay") not in kinds:
        return "FAIL:no_relay_row(kinds=%r)" % kinds
    if not any(k[0] == "relay_followup" for k in kinds):
        # the relay block was printed, so the model's next turn should end in a followup row
        return "FAIL:no_relay_followup(kinds=%r)" % kinds
    side_effect_tools = [t for t in tools if t not in RELAY_SCHEMA_TOOLS]
    if side_effect_tools == ["PushNotification"]:
        log("lv5: relay turn tool_uses (schema loads tolerated): %r" % tools)
        return "PASS"
    if not tools:
        return LV5_LIMITATION  # the model could not or would not call a push tool in headless mode
    return "FAIL:unexpected_tool_uses=%r" % tools


def lv6(scr, plugin_dir, user_audit):
    plugin = scr / "plugin-probe"
    copy_plugin(plugin_dir, plugin)
    probe_log = scr / "probe-stops.jsonl"
    hooks_path = plugin / "hooks" / "hooks.json"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    hooks["hooks"]["Stop"].append({"hooks": [{
        "type": "command", "timeout": 5,
        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/tests/live/probes/stop_probe.py" "%s"' % probe_log}]})
    hooks_path.write_text(json.dumps(hooks, indent=1), encoding="utf-8")
    p6 = make_project(scr, "p6")
    rc, out, _err = claude(plugin, p6, user_audit, "Reply OK.", str(uuid.uuid4()))
    stops = []
    for line in read_lines(probe_log):
        try:
            stops.append(json.loads(line))
        except ValueError:
            continue
    log("lv6: rc=%s stops=%r out=%r" % (rc, stops, out[:80]))
    if len(stops) >= 3:
        return "RECORDED", str(stops[2].get("stop_hook_active")).lower()
    return "RECORDED", "unobserved(stops=%d)" % len(stops)


def all_ok(summary):
    for key, val in summary.items():
        if key == "evidence_class" or key == "lv5_limitation":
            continue
        if key == "plugin_config_unchanged":
            if val is not True:
                return False
        elif key == "lv6":
            if val != "RECORDED":
                return False
        elif key == "stop_hook_active_chain":
            if str(val).startswith("unobserved"):
                return False
        elif not (val == "PASS" or (key == "lv5" and val == LV5_LIMITATION)):
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
    scr = Path(tempfile.mkdtemp(prefix="cf-stop-gate-"))
    summary = {"load_probe": "FAIL", "lv1": "NOT_RUN", "lv2": "NOT_RUN", "lv3": "NOT_RUN", "lv4": "NOT_RUN",
               "lv5": "NOT_RUN", "lv5_limitation": "push_requires_consent_file", "lv6": "NOT_RUN",
               "stop_hook_active_chain": "NOT_RUN", "plugin_config_unchanged": False,
               "evidence_class": "B-SIMULATED"}
    cfg_before = config_hashes(plugin_dir)
    consent_before = real_consent_state()
    try:
        user_audit = scr / "user-audit.json"
        user_jev = scr / "user-jev.json"
        user_relay = scr / "user-relay.json"
        absent = scr / "absent-user-config.json"
        write_json(user_audit, {"mode": "audit"})
        write_json(user_jev, {"mode": "audit", "jevText": True})
        # notify must be "push" in the user layer too: the consent file only CONFIRMS it (core.parse_settings)
        write_json(user_relay, {"mode": "audit", "notify": "push", "notifyMinTurnSeconds": 0})
        p1, p2, p3 = (make_project(scr, "p1"), make_project(scr, "p2"), make_project(scr, "p3", True))
        if not load_probe(plugin_dir, p1, absent, scr / "load-probe-debug.txt"):
            print(json.dumps(summary))
            log("load probe failed: evidence class B-SIMULATED; live steps not run")
            return 1
        summary.update(load_probe="PASS", evidence_class="A")
        summary["lv1"] = lv1(plugin_dir, p1, absent)
        summary["lv2"] = lv2(plugin_dir, p2, user_audit)
        summary["lv3"] = lv3(plugin_dir, p3, user_audit)
        summary["lv4"] = lv4(scr, plugin_dir, user_jev)
        summary["lv5"] = lv5(scr, plugin_dir, user_relay)
        summary["lv6"], summary["stop_hook_active_chain"] = lv6(scr, plugin_dir, user_audit)
        summary["plugin_config_unchanged"] = config_hashes(plugin_dir) == cfg_before
        consent_after = real_consent_state()
        if consent_after != consent_before:
            log("FAIL: real consent file changed: before=%r after=%r" % (consent_before, consent_after))
            print(json.dumps(summary))
            return 1
        log("real consent file unchanged: present=%s sha256=%s" % consent_before)
        print(json.dumps(summary))
        log("scratch Claude sessions are left under ~/.claude/projects/ (slug of %s)" % scr)
        return 0 if all_ok(summary) else 1
    except Exception as exc:  # noqa: BLE001 - driver reports a clean FAIL row, never a traceback
        log("FAIL: unexpected error: %s: %s" % (type(exc).__name__, exc))
        summary["plugin_config_unchanged"] = config_hashes(plugin_dir) == cfg_before
        print(json.dumps(summary))
        return 1
    finally:
        if args.keep:
            log("kept scratch dir: %s" % scr)
        else:
            shutil.rmtree(scr, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
