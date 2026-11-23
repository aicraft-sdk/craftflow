#!/usr/bin/env python3
"""Tests for the provenance-and-prune report (Wave B item 2; after Harness as Living Artifact and Hermes Curator).

The report is read-only. It lists router rule units that cite no reason to exist, and shipped skills, references and
agents that transcripts show unused for 30 or 90 days. It never edits or deletes anything: removing router prose still
needs a signed policy-map row.

Run from anywhere: python3 tools/craftflow-plugin/plugins/craftflow/tests/fixtures/test_craftflow_prune_report.py
"""
import importlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "scripts"))
prune = importlib.import_module("craftflow_prune_report")

FAILS = []
NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


def _plugin(root: Path) -> Path:
    for rel in ("skills/craftflow-router/SKILL.md", "skills/retro/SKILL.md", "skills/retro/references/mistake.md",
                "skills/old/SKILL.md", "skills/never/SKILL.md", "skills/_shared/router-protocol.md",
                "agents/planner.md", "agents/skill-author.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n", encoding="utf-8")
    return root


def _line(ts: datetime, name: str, inp: dict) -> str:
    return json.dumps({"type": "assistant", "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name, "input": inp}]}})


print("[provenance]")
units = [
    {"id": "a1", "text": "Run the gate (ADR-0074).", "occurrences": [{"file": "skills/craftflow-router/SKILL.md",
                                                                       "heading_path": ["router", "gate"]}]},
    {"id": "a2", "text": "Use `craftflow_plan_rules.py` to lint.", "occurrences": [{"file": "f", "heading_path": []}]},
    {"id": "a3", "text": "Keep the reply short.", "occurrences": [{"file": "skills/craftflow-router/SKILL.md",
                                                                   "heading_path": ["router", "style"]}]},
    {"id": "a4", "text": "Never push to main.", "occurrences": [{"file": "f", "heading_path": ["SPEC-0034 rules"]}]},
    {"id": "a5", "text": "Plain rule with a signed row.", "occurrences": [{"file": "f", "heading_path": []}]},
    {"id": "a6", "text": "Incident 2026-08-10: guard the import.", "occurrences": [{"file": "f", "heading_path": []}]},
]
prov = prune.provenance(units, policy_ids={"a5"})
check("a unit citing an ADR, a script, a spec heading or a dated incident has provenance",
      prov["with_provenance"] == 5, prov)
check("a unit with no citation and no policy-map row is listed", [u["id"] for u in prov["without_provenance"]] == ["a3"],
      prov)
check("a listed unit carries its file, heading and excerpt",
      prov["without_provenance"][0]["file"] == "skills/craftflow-router/SKILL.md"
      and prov["without_provenance"][0]["heading"] == "router > style"
      and prov["without_provenance"][0]["excerpt"].startswith("Keep the reply"), prov)

print("[usage]")
tmp = Path(tempfile.mkdtemp(prefix="prune-"))
plugin = _plugin(tmp / "plugin")
tx = tmp / "transcripts" / "proj"
tx.mkdir(parents=True)
lines = [
    _line(NOW - timedelta(days=2), "Skill", {"skill": "craftflow:retro"}),
    _line(NOW - timedelta(days=3), "Read", {"file_path": "/x/plugins/craftflow/skills/retro/references/mistake.md"}),
    _line(NOW - timedelta(days=45), "Skill", {"skill": "craftflow:old"}),
    _line(NOW - timedelta(days=1), "Agent", {"subagent_type": "craftflow:planner"}),
    _line(NOW - timedelta(days=200), "Task", {"subagent_type": "craftflow:skill-author"}),
    "not json",
    json.dumps({"type": "user", "message": {"content": "hi"}}),
]
(tx / "s1.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
usage = prune.usage(plugin, [tmp / "transcripts"], NOW)
by = {item["name"]: item for item in usage["items"]}
check("a Skill tool call counts as use", by["skill:retro"]["uses"] == 1 and by["skill:retro"]["days_since"] == 2, by)
check("a Read of a reference counts as use", by["reference:retro/mistake.md"]["uses"] == 1, by)
check("an Agent or Task dispatch counts as agent use",
      by["agent:planner"]["uses"] == 1 and by["agent:skill-author"]["uses"] == 1, by)
check("unused 30 days is stale", usage["stale_30d"] == ["skill:old"], usage)
check("unused 90 days is an archive candidate", usage["archive_90d"] == ["agent:skill-author"], usage)
check("never seen is listed separately", "skill:never" in usage["never_seen"] and "skill:old" not in usage["never_seen"],
      usage)
check("the router and shared files are pinned, never candidates",
      not any("craftflow-router" in n or "_shared" in n for n in usage["stale_30d"] + usage["archive_90d"]
              + usage["never_seen"]), usage)
check("bad lines are counted, not fatal", usage["bad_lines"] == 1, usage)

print("[cli]")
before = sorted(str(p) for p in plugin.rglob("*"))
proc = subprocess.run([sys.executable, str(PLUGIN / "scripts/craftflow_prune_report.py"), "--plugin-root", str(plugin),
                       "--transcripts", str(tmp / "transcripts"), "--now", "2026-11-23"],
                      capture_output=True, text=True)
out = json.loads(proc.stdout or "{}")
check("cli prints one JSON report and exits 0", proc.returncode == 0 and "usage" in out and "provenance" in out,
      proc.stdout + proc.stderr)
check("the report never changes the plugin tree", sorted(str(p) for p in plugin.rglob("*")) == before)
real = subprocess.run([sys.executable, str(PLUGIN / "scripts/craftflow_prune_report.py")], capture_output=True, text=True)
real_out = json.loads(real.stdout or "{}")
check("on the real plugin, every router unit is classified",
      real.returncode == 0 and real_out["provenance"]["units"] == real_out["provenance"]["with_provenance"]
      + len(real_out["provenance"]["without_provenance"]), real.stdout[:300] + real.stderr)

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
