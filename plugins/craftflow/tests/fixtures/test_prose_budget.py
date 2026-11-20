#!/usr/bin/env python3
"""Tests for the prose budget ratchet and the advisory prose audit (P7).

Always-loaded prose (every agents/*.md prompt and every skills/*/SKILL.md) gets a shrink-only byte ceiling per file in
tests/fixtures/prose-budget-baseline.json, guarded by craftflow_baseline_integrity.py like the file-size baseline.
craftflow_prose_audit.py finds passages duplicated across files and sentences that only restate a default; it is
advisory and never fails a run.

Run from anywhere: python3 plugins/craftflow/tests/fixtures/test_prose_budget.py
"""
import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
REPO = PLUGIN.parents[1]
sys.path.insert(0, str(PLUGIN / "scripts"))
budget = importlib.import_module("craftflow_prose_budget")
audit = importlib.import_module("craftflow_prose_audit")
integrity = importlib.import_module("craftflow_baseline_integrity")
BUDGET_SCRIPT = PLUGIN / "scripts" / "craftflow_prose_budget.py"
AUDIT_SCRIPT = PLUGIN / "scripts" / "craftflow_prose_audit.py"

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


def plugin(files):
    root = Path(tempfile.mkdtemp(prefix="prose-"))
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def baseline(root, files):
    path = root / "baseline.json"
    path.write_text(json.dumps({"version": 1, "files": files}), encoding="utf-8")
    return path


print("[measure]")
root = plugin({"agents/a.md": "x" * 100, "skills/s/SKILL.md": "y" * 50, "skills/s/references/r.md": "z" * 999})
sizes = budget.measure(root)
check("measures agent prompts and SKILL.md files only", sizes == {"agents/a.md": 100, "skills/s/SKILL.md": 50}, sizes)

print("[ratchet]")
r = budget.compare(sizes, {"agents/a.md": 100, "skills/s/SKILL.md": 50})
check("equal sizes pass", r["exit"] == 0 and r["findings"] == [], r)
r = budget.compare({"agents/a.md": 101, "skills/s/SKILL.md": 50}, {"agents/a.md": 100, "skills/s/SKILL.md": 50})
check("growth past the ceiling fails", r["exit"] == 2 and any("GREW" in f and "agents/a.md" in f for f in r["findings"]), r)
r = budget.compare({"agents/a.md": 100, "agents/new.md": 10, "skills/s/SKILL.md": 50}, {"agents/a.md": 100, "skills/s/SKILL.md": 50})
check("a new always-loaded file must be baselined", r["exit"] == 2 and any("NEW" in f for f in r["findings"]), r)
r = budget.compare({"agents/a.md": 90, "skills/s/SKILL.md": 50}, {"agents/a.md": 100, "skills/s/SKILL.md": 50})
check("shrinking passes and reports the lower ceiling", r["exit"] == 0 and r["lowerable"] == {"agents/a.md": 90}, r)

print("[update and init]")
path = baseline(root, {"agents/a.md": 120, "skills/s/SKILL.md": 50, "agents/gone.md": 7})
rc = budget.main(["--root", str(root), "--baseline", str(path), "--update-baseline"])
saved = json.loads(path.read_text())["files"]
check("--update-baseline lowers ceilings and drops missing files", rc == 0 and saved == {"agents/a.md": 100, "skills/s/SKILL.md": 50}, (rc, saved))
path = baseline(root, {"agents/a.md": 90, "skills/s/SKILL.md": 50})
rc = budget.main(["--root", str(root), "--baseline", str(path), "--update-baseline"])
check("--update-baseline refuses while a file grew", rc == 2 and json.loads(path.read_text())["files"]["agents/a.md"] == 90, rc)
rc = budget.main(["--root", str(root), "--baseline", str(root / "fresh.json"), "--init-baseline"])
check("--init-baseline writes the first baseline", rc == 0 and json.loads((root / "fresh.json").read_text())["files"] == sizes, rc)
rc = budget.main(["--root", str(root), "--baseline", str(root / "fresh.json"), "--init-baseline"])
check("--init-baseline refuses to overwrite", rc == 1, rc)

print("[cli]")
proc = subprocess.run([sys.executable, str(BUDGET_SCRIPT), "--root", str(root), "--baseline", str(root / "fresh.json")],
                      capture_output=True, text=True)
check("cli exits 0 within budget", proc.returncode == 0, proc.stdout + proc.stderr)
proc = subprocess.run([sys.executable, str(BUDGET_SCRIPT), "--root", str(root), "--baseline", str(root / "missing.json")],
                      capture_output=True, text=True)
check("cli exits 1 on a missing baseline", proc.returncode == 1, proc.returncode)

print("[the real plugin is within its baseline]")
proc = subprocess.run([sys.executable, str(BUDGET_SCRIPT)], capture_output=True, text=True)
check("every agent prompt and SKILL.md is at or under its ceiling", proc.returncode == 0, proc.stdout[-600:] + proc.stderr)
check("baseline integrity guards the prose baseline", "plugins/craftflow/tests/fixtures/prose-budget-baseline.json" in integrity.GUARDED)
check("raising a prose ceiling is a violation",
      integrity.COMPARATORS["plugins/craftflow/tests/fixtures/prose-budget-baseline.json"](
          json.dumps({"version": 1, "files": {"agents/a.md": 10}}),
          json.dumps({"version": 1, "files": {"agents/a.md": 11}})) != [])

print("[audit]")
para = "When a phase exits the router persists the workflow artifact before it advances the phase cursor again."
root = plugin({"agents/a.md": para + "\n\nUnique text one that is long enough to count as a passage for the audit here.\n",
               "skills/s/SKILL.md": "# S\n\n- " + para + "\n\nMake sure to follow best practices.\n",
               "skills/_shared/x.md": "Nothing repeated here, just a long enough paragraph of its own words for once.\n"})
report = audit.audit(root)
check("finds a passage duplicated across files", len(report["duplicates"]) == 1 and len(report["duplicates"][0]["files"]) == 2, report)
check("finds a sentence that restates a default", any("best practices" in d["text"] for d in report["defaults"]), report)
check("short passages are not duplicates", audit.audit(plugin({"agents/a.md": "Done.\n", "agents/b.md": "Done.\n"}))["duplicates"] == [])
proc = subprocess.run([sys.executable, str(AUDIT_SCRIPT), "--root", str(root), "--format", "md"], capture_output=True, text=True)
check("audit is advisory: exits 0 with findings", proc.returncode == 0 and "Duplicated passages" in proc.stdout, proc.stdout + proc.stderr)
check("first audit report is saved under docs/benchmarks", list((REPO / "docs/benchmarks").glob("*-prose-audit.md")) != [])

print("[rule-writing guidance]")
mtr = (PLUGIN / "skills/retro/references/mistake-to-rule.md").read_text(encoding="utf-8")
check("mistake-to-rule orders lint rule first, automation last", "lint rule first" in mtr and "automation last" in mtr)
check("lint messages name the root-cause fix", "root-cause fix" in mtr)
crp = (PLUGIN / "skills/code-review-patterns/SKILL.md").read_text(encoding="utf-8")
check("standards smell: justification comments", "justification comment" in crp)
check("standards smell: timer workarounds", "setTimeout" in crp and "requestAnimationFrame" in crp)

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
