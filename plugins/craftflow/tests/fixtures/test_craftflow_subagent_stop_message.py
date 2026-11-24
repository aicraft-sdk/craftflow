#!/usr/bin/env python3
"""SubagentStop audit: when Claude Code sends an empty `last_assistant_message`, the hook reads the agent's final text
from its transcript, so the CONTRACT check, agent_usage and the Stage B `machine_shadow` row still see the verdict.

Seen live on 2026-11-23: 38 of 40 craftflow agent stops in one Windows project arrived with an empty message, so every
shadow row said "unmodeled verdict None" and Stage B could not count a single workflow.

Run from anywhere: python3 tools/craftflow-plugin/plugins/craftflow/tests/fixtures/test_craftflow_subagent_stop_message.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "scripts" / "craftflow_subagent_stop_audit.py"
FAILS = []
CONTRACT = 'CONTRACT {"s":"PASS","b":false}\n## Build: Pass'


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


def assistant(mid, text):
    return json.dumps({"type": "assistant", "timestamp": "2026-11-23T10:00:00Z", "message": {
        "role": "assistant", "id": mid, "model": "claude-test",
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}}})


def run(tmp, message, transcript_lines, with_workflow=True):
    project = tmp / "project"
    state = project / ".craftflow" / "state"
    (state / "workflows").mkdir(parents=True, exist_ok=True)
    if with_workflow:
        (state / "workflows" / "wf-test.json").write_text(json.dumps(
            {"workflow_uuid": "wf-test", "workflow_type": "BUILD", "build_mode": "standard"}), encoding="utf-8")
    transcript = tmp / "agent.jsonl"
    if transcript_lines is not None:
        transcript.write_text("\n".join(transcript_lines) + "\n", encoding="utf-8")
    payload = {"agent_type": "craftflow:component-builder", "agent_id": "a1", "session_id": "s1",
               "agent_transcript_path": str(transcript), "last_assistant_message": message,
               "stop_hook_active": False}
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True,
                          env=env, timeout=60)
    log = state / "craftflow-hook-events.log"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if log.exists() else []
    return proc.returncode, {r.get("event"): r for r in rows}


def case(label, message, transcript_lines, **kw):
    with tempfile.TemporaryDirectory(prefix="cf_sa_msg_") as tmp:
        code, rows = run(Path(tmp), message, transcript_lines, **kw)
    check(f"{label}: exit 0", code == 0, code)
    return rows


print("[empty hook message, transcript has the contract]")
rows = case("fallback", "", [assistant("m1", "working on it"), assistant("m2", CONTRACT)])
stop, usage, shadow = rows.get("subagent_stop", {}), rows.get("agent_usage", {}), rows.get("machine_shadow", {})
check("subagent_stop sees the contract", stop.get("contract_found") is True, stop)
check("subagent_stop says where the message came from", stop.get("message_source") == "transcript_final_text", stop)
check("agent_usage classifies the envelope", usage.get("contract_shape") == "envelope", usage)
check("agent_usage names the transcript as the source", usage.get("contract_source") == "transcript_final_text", usage)
check("machine_shadow reads the verdict", shadow.get("verdict") == "PASS", shadow)
check("machine_shadow is no longer unmodeled", not str(shadow.get("note") or "").startswith("unmodeled"), shadow)

print("[a non-empty hook message wins over the transcript]")
rows = case("hook first", CONTRACT, [assistant("m1", "something else")])
check("source is the hook message", rows.get("subagent_stop", {}).get("message_source") == "hook_last_message",
      rows.get("subagent_stop"))
check("verdict from the hook message", rows.get("machine_shadow", {}).get("verdict") == "PASS",
      rows.get("machine_shadow"))

print("[fail-open]")
rows = case("missing transcript", "", None)
check("still logs subagent_stop with no contract", rows.get("subagent_stop", {}).get("contract_found") is False, rows)
check("source stays hook_last_message", rows.get("subagent_stop", {}).get("message_source") == "hook_last_message",
      rows.get("subagent_stop"))
rows = case("corrupt transcript", "", ["{not json"])
check("corrupt transcript still logs", rows.get("subagent_stop", {}).get("contract_found") is False, rows)

print()
print("FAILED: " + ", ".join(FAILS) if FAILS else "ALL PASSED")
sys.exit(1 if FAILS else 0)
