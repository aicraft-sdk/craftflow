#!/usr/bin/env python3
"""Tests for P8 Stage B: the workflow machine in shadow mode (ADR-0082, SPEC-0037).

After each craftflow agent returns, the SubagentStop audit hook asks scripts/craftflow_machine_shadow.py what the
machine expects next and whether the agent that just returned is one the machine expected. It only logs
(`machine_shadow` rows); routing never reads it. `craftflow_machine.py shadow-report` turns the log into the Stage B
exit check: an agreed number of workflows with zero disagreements.

Run from anywhere: python3 tools/craftflow-plugin/plugins/craftflow/tests/fixtures/test_craftflow_machine_shadow.py
"""
import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "scripts"))
shadow = importlib.import_module("craftflow_machine_shadow")
machine = importlib.import_module("craftflow_machine")

FAILS = []
MACHINES = machine.load_all(PLUGIN / "config/machines")
POLICY = json.loads((PLUGIN / "config/policy.json").read_text(encoding="utf-8"))


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


def run(steps, workflow_type="BUILD", build_mode="standard"):
    state, rows = {}, []
    for agent, verdict in steps:
        row, state = shadow.observe(MACHINES, POLICY, state, workflow_type, build_mode, agent, verdict)
        rows.append(row)
    return rows


print("[verdicts]")
check("reads the CONTRACT envelope", shadow.verdict_of('CONTRACT {"s":"APPROVE","b":false,"cr":0}\n## Review') == "APPROVE")
check("reads a YAML STATUS line", shadow.verdict_of("```yaml\nverdict:\n  STATUS: PASS\n```") == "PASS")
check("no verdict reads as None", shadow.verdict_of("no contract here") is None)

print("[machine choice]")
check("BUILD standard", shadow.machine_for("BUILD", "standard") == "build-standard")
check("BUILD fast path", shadow.machine_for("BUILD", "fast_path") == "build-fast")
check("legacy BUILD with no build_mode is standard", shadow.machine_for("BUILD", None) == "build-standard")
check("REVIEW", shadow.machine_for("REVIEW", None) == "review")
check("an unmodeled route has no machine", shadow.machine_for("ORIENT", None) is None)

print("[a standard BUILD that follows the machine]")
rows = run([("component-builder", "PASS"), ("code-reviewer", "APPROVE"), ("code-reviewer", "APPROVE"),
            ("silent-failure-hunter", "CLEAN"), ("integration-verifier", "PASS"), ("doc-syncer", "COMPLETE")])
check("every step agrees", [r["agree"] for r in rows] == [None, True, True, True, True, True], rows)
check("the parallel step advances only when all returned (two reviewer axes and the hunter)",
      rows[1]["state"] == "review-and-hunt" and [r["next"] for r in rows[1:4]] == [None, None, "build-verify"], rows[1:4])
check("rows name the machine", {r["machine"] for r in rows} == {"build-standard"}, rows)

print("[disagreements]")
rows = run([("component-builder", "PASS"), ("integration-verifier", "PASS")])
check("skipping review in a standard BUILD disagrees", rows[1]["agree"] is False and "code-reviewer" in rows[1]["expected"], rows[1])
rows = run([("component-builder", "PASS"), ("integration-verifier", "PASS")], build_mode="fast_path")
check("the same steps agree on the fast path", rows[1]["agree"] is True, rows[1])
rows = run([("component-builder", "PASS"), ("code-reviewer", "CHANGES_REQUESTED"), ("code-reviewer", "APPROVE"),
            ("silent-failure-hunter", "CLEAN"), ("component-builder", "PASS")])
check("findings route to remediation, and the builder is expected", rows[3]["next"] == "remfix" and rows[4]["agree"] is True, rows)

print("[unmodeled input never raises]")
rows = run([("component-builder", "PASS"), ("code-reviewer", "SOMETHING_ELSE")])
check("an unknown verdict is logged as unmodeled", rows[1]["agree"] is True and rows[1]["note"].startswith("unmodeled"), rows[1])
rows = run([("planner", "PLAN_CREATED")], workflow_type="ORIENT")
check("an unmodeled route is logged as unmodeled", rows[0]["agree"] is None and rows[0]["machine"] is None, rows[0])
rows = run([("research-agent", "DONE")])
check("an agent no state names is unmodeled", rows[0]["note"].startswith("unmodeled"), rows[0])

print("[report]")
log = Path(tempfile.mkdtemp(prefix="shadow-")) / "craftflow-hook-events.log"
lines = []
for i in range(3):
    for r in run([("component-builder", "PASS"), ("code-reviewer", "APPROVE"), ("code-reviewer", "APPROVE"),
                  ("silent-failure-hunter", "CLEAN")]):
        lines.append({"event": "machine_shadow", "workflow": f"wf-{i}", **r})
for r in run([("component-builder", "PASS"), ("integration-verifier", "PASS")]):
    lines.append({"event": "machine_shadow", "workflow": "wf-bad", **r})
lines.append({"event": "subagent_stop", "workflow": "wf-0"})
log.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
rep = shadow.report(log, need=3)
check("counts workflows with zero disagreements", rep["clean_workflows"] == 3, rep)
check("lists disagreements", rep["disagreements"] == 1 and rep["disagreeing_workflows"] == ["wf-bad"], rep)
check("the exit criterion needs zero disagreements", rep["exit_criterion_met"] is False, rep)
check("the criterion holds when the disagreeing workflow is absent", shadow.report_rows(
    [x for x in lines if x.get("workflow") != "wf-bad"], need=3)["exit_criterion_met"] is True)
print("[report: divergence measures, report only]")
timed = [
    {"event": "machine_shadow", "workflow": "a", "agree": True, "ts": "2026-11-20T10:00:00Z"},
    {"event": "machine_shadow", "workflow": "a", "agree": True, "ts": "2026-11-20T11:00:00Z"},
    {"event": "machine_shadow", "workflow": "b", "agree": False, "ts": "2026-11-22T09:00:00Z"},
    {"event": "machine_shadow", "workflow": "b", "agree": None, "ts": "2026-11-22T09:05:00Z"},
    {"event": "machine_shadow", "workflow": "c", "agree": True},
]
m = shadow.report_rows(timed, need=1)
check("counts agree, disagree and unmodeled rows", (m["agree_rows"], m["disagree_rows"], m["unmodeled_rows"]) == (3, 1, 1), m)
check("divergence rate is disagreements over judged rows", m["divergence_rate"] == 0.25, m)
check("first and last row dates come from ts", (m["first_row"], m["last_row"]) == ("2026-11-20", "2026-11-22"), m)
check("days with rows are counted", m["active_days"] == 2, m)
check("the new fields never change the exit criterion", m["exit_criterion_met"] is False and shadow.report_rows(
    [x for x in timed if x["workflow"] != "b"], need=1)["exit_criterion_met"] is True, m)
empty = shadow.report_rows([], need=20)
check("an empty log has no rate and no dates", (empty["divergence_rate"], empty["first_row"], empty["last_row"],
                                                 empty["active_days"]) == (None, None, None, 0), empty)

proc = subprocess.run([sys.executable, str(PLUGIN / "scripts/craftflow_machine.py"), "shadow-report", "--log", str(log),
                       "--need", "3"], capture_output=True, text=True)
check("cli shadow-report prints the report and exits 0", proc.returncode == 0 and json.loads(proc.stdout)["clean_workflows"] == 3,
      proc.stdout + proc.stderr)

print("[backfill check: dry run over older logs, never counted]")
bf = Path(tempfile.mkdtemp(prefix="shadow-backfill-"))
(bf / "workflows").mkdir()
(bf / "workflows/w1.json").write_text(json.dumps(
    {"workflow_uuid": "w1", "workflow_type": "BUILD", "build_mode": "standard", "session_id": "s1"}), encoding="utf-8")
(bf / "workflows/w2a.json").write_text(json.dumps({"workflow_uuid": "w2a", "workflow_type": "BUILD", "session_id": "s2"}))
(bf / "workflows/w2b.json").write_text(json.dumps({"workflow_uuid": "w2b", "workflow_type": "BUILD", "session_id": "s2"}))


def _transcript(name, text):
    path = bf / f"{name}.jsonl"
    path.write_text(json.dumps({"type": "assistant", "message": {
        "id": name, "role": "assistant", "model": "claude-sonnet", "usage": {"input_tokens": 1, "output_tokens": 1},
        "content": [{"type": "text", "text": text}]}}) + "\n", encoding="utf-8")
    return str(path)


old = []
for i, (agent, text) in enumerate((("component-builder", "STATUS: PASS"), ("integration-verifier", "STATUS: PASS"))):
    aid = f"a{i}"
    old.append({"event": "subagent_stop", "agent_id": aid, "agent_transcript_path": _transcript(aid, text),
                "ts": f"2026-11-0{i + 1}T10:00:00Z"})
    old.append({"event": "agent_usage", "agent_type": "craftflow:" + agent, "agent_id": aid, "session_id": "s1",
                "ts": f"2026-11-0{i + 1}T10:00:00Z"})
old.append({"event": "subagent_stop", "agent_id": "gone", "agent_transcript_path": str(bf / "missing.jsonl")})
old.append({"event": "agent_usage", "agent_type": "craftflow:code-reviewer", "agent_id": "gone", "session_id": "s3"})
(bf / "workflows/w3.json").write_text(json.dumps({"workflow_uuid": "w3", "workflow_type": "BUILD", "session_id": "s3"}))
old.append({"event": "agent_usage", "agent_type": "craftflow:planner", "agent_id": "x", "session_id": "s2"})
old.append({"event": "agent_usage", "agent_type": "craftflow:planner", "agent_id": "y", "session_id": "nope"})
old.append({"event": "agent_usage", "agent_type": "Explore", "agent_id": "z", "session_id": "s1"})
bf_log = bf / "craftflow-hook-events.log"
bf_log.write_text("\n".join(json.dumps(x) for x in old) + "\n", encoding="utf-8")
before = bf_log.read_bytes()
b = shadow.backfill_check(bf_log, bf / "workflows", MACHINES, POLICY)
check("counts craftflow agent returns only", b["craftflow_returns"] == 5, b)
check("a return joins a workflow by session id", b["with_workflow"] == 3, b)
check("a session with two workflows is ambiguous, not guessed", b["ambiguous_session"] == 1, b)
check("a verdict needs the transcript still on disk", b["with_verdict"] == 2, b)
check("only workflows with every return recovered are replayable",
      (b["replayable_workflows"], b["incomplete_workflows"]) == (1, 1), b)
check("dry-run rows are judged with the live machine", b["dry_run"]["disagree_rows"] == 1, b)
check("the dry run is marked as not counted toward Stage B", b["counts_toward_stage_b"] is False, b)
check("the backfill check never writes the log", bf_log.read_bytes() == before)
proc = subprocess.run([sys.executable, str(PLUGIN / "scripts/craftflow_machine.py"), "backfill-check", "--log", str(bf_log),
                       "--workflows", str(bf / "workflows")], capture_output=True, text=True)
check("cli backfill-check prints the report and exits 0",
      proc.returncode == 0 and json.loads(proc.stdout)["craftflow_returns"] == 5, proc.stdout + proc.stderr)

print("[hook wiring]")
root = Path(tempfile.mkdtemp(prefix="shadow-hook-"))
(root / ".craftflow/state/workflows").mkdir(parents=True)
(root / ".craftflow/state/workflows/wf-1.json").write_text(json.dumps(
    {"workflow_uuid": "wf-1", "workflow_type": "BUILD", "build_mode": "standard"}), encoding="utf-8")
env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
for agent, msg in (("craftflow:component-builder", "### Router Contract\n```yaml\nverdict:\n  STATUS: PASS\n```"),
                   ("craftflow:integration-verifier", 'CONTRACT {"s":"PASS","b":false,"cr":0}\n## Verification: PASS')):
    subprocess.run([sys.executable, str(PLUGIN / "scripts/craftflow_subagent_stop_audit.py")], cwd=root, env=env, text=True,
                   input=json.dumps({"hook_event_name": "SubagentStop", "agent_type": agent, "last_assistant_message": msg}),
                   capture_output=True)
events = [json.loads(x) for x in (root / ".craftflow/state/craftflow-hook-events.log").read_text().splitlines() if x.strip()]
rows = [e for e in events if e.get("event") == "machine_shadow"]
check("the audit hook logs one machine_shadow row per agent", len(rows) == 2, events)
check("the hook row flags the skipped review", rows[-1:] and rows[-1]["agree"] is False and rows[-1]["workflow"] == "wf-1", rows)
mode = json.loads((PLUGIN / "config/hook-mode.json").read_text(encoding="utf-8"))
check("hook-mode.json has machineShadow: audit", mode.get("machineShadow") == "audit", mode)

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
