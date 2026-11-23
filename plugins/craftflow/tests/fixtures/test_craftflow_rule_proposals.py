#!/usr/bin/env python3
"""Tests for mistake-to-rule proposals (Wave B item 5; after 60-day mistake linting and Claude Doctor).

`craftflow_learn_scan.py --propose` keeps the digest read-only and adds, for each failure signature seen twice or
more, the mechanism skills/retro/references/mistake-to-rule.md would pick and who acts on it. `--transcripts DIR`
adds two structural session signals from Claude Code transcripts: edit thrashing and error loops. Without the new
flags the output is unchanged.

Run from anywhere: python3 tools/craftflow-plugin/plugins/craftflow/tests/fixtures/test_craftflow_rule_proposals.py
"""
import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "scripts"))
rp = importlib.import_module("craftflow_rule_proposals")

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


def cluster(sig, n=2):
    return {"signature": sig, "occurrences": n, "example_reasons": [sig], "event_types": ["remediation_created"]}


print("[mechanism]")
cases = (
    ("imports from packages inside tools", "arch-lint rule"),
    ("ruff unused import left in the diff", "lint rule or clean-state check"),
    ("integration verifier skipped before completion", "hook"),
    ("regression test failed on the refund total", "regression test"),
    ("naming felt unclear to the reviewer", "reviewer prose"),
)
for sig, mech in cases:
    got = rp.propose([cluster(sig)])[0]["proposal"]["mechanism"]
    check(f"'{sig}' maps to {mech}", got == mech, got)
one = rp.propose([cluster("regression test failed", n=1)])[0]["proposal"]
check("a first occurrence gets no mechanism yet", one["mechanism"] is None and "record" in one["who"], one)
p = rp.propose([cluster("integration verifier skipped before completion", n=3)])[0]["proposal"]
check("a proposal names who acts and points at the guide",
      p["who"] and "mistake-to-rule.md" in p["draft"] and "3x" in p["draft"], p)
check("hooks stay a human decision", "human" in p["who"], p)

print("[session signals]")
tmp = Path(tempfile.mkdtemp(prefix="proposals-"))
tx = tmp / "proj"
tx.mkdir()


def tool_use(name, inp):
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}})


def tool_result(err):
    return json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "is_error": err}]}})


lines = [tool_use("Edit", {"file_path": "/r/a.py"}) for _ in range(5)] + [tool_use("Edit", {"file_path": "/r/b.py"})]
lines += [tool_result(True), tool_result(True), tool_result(True), tool_result(False), tool_result(True)]
(tx / "s1.jsonl").write_text("\n".join(lines) + "\nnot json\n", encoding="utf-8")
(tx / "s2.jsonl").write_text("\n".join([tool_result(True), tool_result(True)]) + "\n", encoding="utf-8")
sig = rp.session_signals([tmp])
check("a file edited 5 or more times in one session is thrashing",
      sig["edit_thrashing"] == [{"session": "s1.jsonl", "file": "/r/a.py", "edits": 5}], sig)
check("3 or more tool errors in a row is an error loop",
      sig["error_loops"] == [{"session": "s1.jsonl", "longest_run": 3}], sig)
check("sessions are counted", sig["sessions"] == 2, sig)

print("[cli]")
state = tmp / "state" / "workflows"
state.mkdir(parents=True)
events = [{"event": "remediation_created", "reason": "integration verifier skipped before completion", "ts": "t1"},
          {"event": "remediation_created", "reason": "integration verifier skipped before completion", "ts": "t2"}]
(state / "w.events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
script = PLUGIN / "scripts/craftflow_learn_scan.py"
plain = subprocess.run([sys.executable, str(script), "--state-dir", str(tmp / "state")], capture_output=True, text=True)
check("without --propose the output is the plain cluster list",
      isinstance(json.loads(plain.stdout), list) and "proposal" not in json.loads(plain.stdout)[0], plain.stdout)
prop = subprocess.run([sys.executable, str(script), "--state-dir", str(tmp / "state"), "--propose",
                       "--transcripts", str(tx)], capture_output=True, text=True)
out = json.loads(prop.stdout or "{}")
check("--propose adds proposals and --transcripts adds session signals",
      prop.returncode == 0 and out["clusters"][0]["proposal"]["mechanism"] == "hook"
      and out["session_signals"]["sessions"] == 2, prop.stdout + prop.stderr)
check("nothing is written under the state dir",
      sorted(p.name for p in (tmp / "state").rglob("*")) == ["w.events.jsonl", "workflows"])

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
