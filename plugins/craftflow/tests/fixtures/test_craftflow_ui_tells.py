#!/usr/bin/env python3
"""Tests for scripts/craftflow_ui_tells.py and its wiring into frontend-design-critique (adapted from unslop-ui, MIT).

The incident fixtures under ui-tells/incidents are false positives and misses that unslop-ui fixed; each names the
rule count the counter must produce for its source. The before/after examples prove the counter sees a cleanup.

Run from anywhere: python3 plugins/craftflow/tests/fixtures/test_craftflow_ui_tells.py
"""
import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent / "ui-tells"
SCRIPT = PLUGIN / "scripts" / "craftflow_ui_tells.py"
sys.path.insert(0, str(PLUGIN / "scripts"))
tells = importlib.import_module("craftflow_ui_tells")

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


def scan(source, suffix=".tsx", taste=None, surface=False):
    tmp = Path(tempfile.mkdtemp(prefix="tells-"))
    target = tmp / ("sample" + suffix)
    target.write_text(source, encoding="utf-8")
    bans = set()
    if taste is not None:
        (tmp / "taste.md").write_text(taste, encoding="utf-8")
        bans = tells.read_bans(str(tmp / "taste.md"))
    return tells.count([str(target)], surface=surface, bans=bans)


print("[incidents: false positives and misses stay fixed]")
for path in sorted((HERE / "incidents").glob("*.json")):
    case = json.loads(path.read_text(encoding="utf-8"))
    counter = case["counter"]
    result = scan(counter["source"], counter.get("suffix", ".tsx"), counter.get("taste"))
    for rule, want in counter["expect"].items():
        got = result.get(rule, {}).get("count")
        check(f"{case['id']}: {rule} == {want}", got == want, f"got {got}")

print("[real tells are counted]")
DEPLOYS = (
    '<p className="text-xs uppercase tracking-widest">Deployments</p>\n'
    '<h2>🚀 Recent deploys <span className="rounded-full px-2 text-xs">NEW</span></h2>\n'
    '<span className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />\n'
)
r = scan(DEPLOYS)
for rule in ("U1 eyebrow", "U11 emoji", "U9 badge word", "U10 status dot"):
    check(f"counts {rule}", r[rule]["count"] >= 1, r[rule])
check("examples are file:line", r["U1 eyebrow"]["examples"][0].endswith(":1"), r["U1 eyebrow"])
check("total sums the rules", r["total"] == sum(v["count"] for k, v in r.items() if k != "total"), r)
r = scan('<h3>Bar chart of revenue</h3>\n<p>Hover over a bar to see the value</p>\n')
check("counts a chart-type title", r["U12 chart-type title"]["count"] == 1, r)
check("counts reading instructions", r["U13 reading instructions"]["count"] == 1, r)
r = scan('<div className="bg-gradient-to-r font-black transition-all p-[13px]">x</div>\n', surface=True)
for rule in ("U17 gradient or glow", "U18 off-scale spacing", "U19 extra-heavy weight", "U20 motion"):
    check(f"--surface counts {rule}", r[rule]["count"] == 1, r.get(rule))
check("surface rules are off by default", "U17 gradient or glow" not in scan("<div className='bg-gradient-to-r'/>"))
check("comments are not scanned", scan("// 🚀 launch\n{/* NEW */}\n")["total"] == 0)

print("[before and after]")
before = tells.count([str(HERE / "before")])["total"]
after = tells.count([str(HERE / "after")])["total"]
check("the cleaned-up examples count fewer tells", after < before, (before, after))
check("the before examples count some tells", before >= 10, before)

print("[taste file]")
check("bans in the taste file switch on T rules", tells.read_bans_text("## Bans\n- Exclamation marks\n") == {"exclamation"})
check("a Keep section switches nothing on", tells.read_bans_text("## Keep\n- Uppercase table headers\n") == set())
check("a missing taste file bans nothing", tells.read_bans("/nonexistent/taste.md") == set())
check("default taste path is under .craftflow", tells.TASTE_FILE == ".craftflow/state/project/taste.md", tells.TASTE_FILE)
keep = tells.read_keeps_text("## Keep\n- Uppercase table headers\n- The rocket emoji in the logo\n")
check("Keep items are read so the critique can report them", keep == ["Uppercase table headers", "The rocket emoji in the logo"], keep)

print("[cli]")
tmp = Path(tempfile.mkdtemp(prefix="tells-cli-"))
(tmp / "a.tsx").write_text(DEPLOYS, encoding="utf-8")
proc = subprocess.run([sys.executable, str(SCRIPT), str(tmp)], capture_output=True, text=True)
check("cli exits 0 and prints JSON", proc.returncode == 0 and json.loads(proc.stdout)["total"] >= 4, proc.stderr)
proc = subprocess.run([sys.executable, str(SCRIPT), "--max", "0", str(tmp)], capture_output=True, text=True)
check("cli --max exits 1 over the limit", proc.returncode == 1, proc.returncode)
proc = subprocess.run([sys.executable, str(SCRIPT), str(tmp / "missing")], capture_output=True, text=True)
check("cli exits 2 on a missing target", proc.returncode == 2, proc.returncode)

print("[critique wiring]")
skill = (PLUGIN / "skills/frontend-design-critique/SKILL.md").read_text(encoding="utf-8")
check("Assessment B runs the built-in detector", "craftflow_ui_tells.py" in skill)
for tell in ("eyebrow", "decorative numbering", "subtitle", "botsplaining", "chart title"):
    check(f"ban-list names the copy tell: {tell}", tell in skill.lower())
check("the real-state test is stated", "real state" in skill.lower())
check("the taste file is read", ".craftflow/state/project/taste.md" in skill)
check("Keep items are reported, never flagged", "never flagged" in skill)
check("reworks carry a subtractive fix brief", "fix brief" in skill.lower() and "delete first" in skill.lower())
check("accessible names survive the fix brief", "aria-label" in skill)
check("taste-file template exists", (PLUGIN / "templates/taste.md").is_file())
notice = (PLUGIN.parents[1] / "NOTICE").read_text(encoding="utf-8")
check("NOTICE credits unslop-ui", "unslop-ui" in notice)

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
