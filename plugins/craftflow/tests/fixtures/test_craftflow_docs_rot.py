#!/usr/bin/env python3
"""Tests for scripts/craftflow_docs_rot_checks.py (pure checks D1..D10) and scripts/craftflow_docs_rot.py (ratchet shell).

Run from anywhere:
    python3 tests/fixtures/test_craftflow_docs_rot.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN / "scripts"
REPO = PLUGIN.parents[3]
sys.path.insert(0, str(SCRIPTS))

import craftflow_docs_rot_checks as rot  # noqa: E402

PASS = 0
FAIL = 0


def check(name, actual, expected):
    global PASS, FAIL
    if actual == expected:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        print(f"    expected: {expected!r}")
        print(f"    actual:   {actual!r}")
        FAIL += 1


# --- literal texts for the pure checks --------------------------------------------
TOP = "## Agents\n\n| Agent | Model | Role |\n|---|---|---|\n| `alpha` | opus | a |\n| `beta` / `gamma` | sonnet | b |\n\n## Next\n"
PLUGIN_README = (
    "## What it does\n\n- **Agent chain** - 3 specialized agents: alpha\n- **2 skills** - x\n\n"
    "### Default agent models\n\n| Agent | Default model | Why |\n|---|---|---|\n| `alpha` | opus | a |\n| `beta` | sonnet | b |\n"
    "| `gamma` | sonnet | c |\n\n## Workflow types\n\n| Signal | Workflow | Agent chain |\n|---|---|---|\n"
    "| error | DEBUG | x |\n| else | BUILD | y |\n\n## Other\n"
)
PROTOCOL = (
    "## Intent Routing\n\n| Priority | Signal | Keywords | Workflow | Chain |\n|---|---|---|---|---|\n"
    "| 1 | ERROR | error | DEBUG | a -> b |\n| 4 | DEFAULT | rest | BUILD | fast \\|\\| full |\n\nRules:\n\n"
    "## Explicit Dispatcher (Phase-to-Agent Table)\n\n| Phase | Agent |\n|---|---|\n| `x` | `craftflow:alpha` |\n"
    "| `y`, `z` | `craftflow:beta` |\n"
)
CURSOR = "## Agent File Paths\n\n| Agent role | File path |\n|---|---|\n| alpha | `agents/alpha.md` |\n\n## 1. Intent\n"
STEMS = ["alpha", "beta", "gamma"]

print("D1 agent tables and count")
check("clean", rot.d1(STEMS, TOP, PLUGIN_README), [])
top_missing = TOP.replace("| `alpha` | opus | a |\n", "")
check("agent row deleted from the top README", rot.d1(STEMS, top_missing, PLUGIN_README), ["rot:D1:missing-in-top:alpha"])
check("a row naming no agent file", rot.d1(STEMS, TOP + "", PLUGIN_README.replace("| `gamma` | sonnet | c |", "| `delta` | sonnet | c |")),
      ["rot:D1:missing-in-plugin:gamma", "rot:D1:unknown-in-plugin:delta"])
check("count claim differs", rot.d1(STEMS, TOP, PLUGIN_README.replace("3 specialized", "9 specialized")), ["rot:D1:count"])
check("count claim absent", rot.d1(STEMS, TOP, PLUGIN_README.replace("3 specialized agents", "agents")), ["rot:D1:count-missing"])

print("D2 explicit dispatcher")
check("clean", rot.d2(PROTOCOL, STEMS, {"gamma": "manual only"}), [])
check("agent file never dispatched", rot.d2(PROTOCOL, STEMS, {}), ["rot:D2:undispatched:gamma"])
check("dispatcher names a missing file", rot.d2(PROTOCOL + "| `q` | `craftflow:ghost` |\n", STEMS, {"gamma": "x"}), ["rot:D2:missing-file:ghost"])
check("stale exemption", rot.d2(PROTOCOL, STEMS, {"gamma": "x", "alpha": "y"}), ["rot:D2:stale-undispatched:alpha"])

print("D3 cursor agent paths")
check("clean", rot.d3({"alpha", "beta"}, CURSOR, {"beta": "claude only"}), [])
check("dispatched agent absent from cursor", rot.d3({"alpha", "beta"}, CURSOR, {}), ["rot:D3:missing:beta"])
check("stale claude-only entry", rot.d3({"alpha"}, CURSOR, {"alpha": "x"}), ["rot:D3:stale-claude-only:alpha"])

print("D4 skill count")
check("clean", rot.d4(PLUGIN_README, 2), [])
check("count differs", rot.d4(PLUGIN_README, 5), ["rot:D4:count"])
check("claim absent", rot.d4("nothing", 5), ["rot:D4:count-missing"])

print("D5 hook scripts")
CLAUDE_HOOKS = json.dumps({"hooks": {"Stop": [{"hooks": [{"command": 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/craftflow_a.py"'}]}]}})
CURSOR_HOOKS = json.dumps({"hooks": {"stop": [{"command": 'python3 "${CURSOR_PLUGIN_ROOT}/scripts/craftflow_cursor_adapter.py" "${CURSOR_PLUGIN_ROOT}/scripts/craftflow_b.py"'}]}})
EXISTING = {"craftflow_a.py", "craftflow_b.py", "craftflow_cursor_adapter.py"}
check("clean", rot.d5(CLAUDE_HOOKS, CURSOR_HOOKS, EXISTING, "uses craftflow_a.py"), [])
check("script path that does not exist", rot.d5(CLAUDE_HOOKS, CURSOR_HOOKS, EXISTING - {"craftflow_b.py"}, "craftflow_a.py"),
      ["rot:D5:missing-script:cursor:craftflow_b.py"])
check("hook script not in the hooks README", rot.d5(CLAUDE_HOOKS, CURSOR_HOOKS, EXISTING, "nothing"), ["rot:D5:undocumented:craftflow_a.py"])

print("D6 versions")
MARKET = {"metadata": {"version": "1.2.3"}, "plugins": [{"name": "craftflow", "version": "1.2.3"}]}
CHANGELOG = "# Changelog\n\n## [1.2.3] - 2026-01-01\n\n## [1.2.2] - 2025-01-01\n"
READMES = {"README_TOP": "**Current version:** 1.2.3\n", "README_PLUGIN": "no version here"}
check("clean", rot.d6("1.2.3", MARKET, CHANGELOG, READMES), [])
check("every drifting surface is named without its value",
      rot.d6("1.2.4", MARKET, CHANGELOG, {"README_TOP": "**Current version:** 1.2.0\n", "README_PLUGIN": "**Current version:** 1.2.9"}),
      sorted(["rot:D6:changelog", "rot:D6:marketplace-metadata", "rot:D6:marketplace-plugin", "rot:D6:README_PLUGIN", "rot:D6:README_TOP"]))

print("D7 routing table coverage")
VARIANTS = {"DEBUG": ["standard"], "BUILD": ["standard"]}
check("clean", rot.d7(PROTOCOL, VARIANTS, {"DEBUG", "BUILD"}, {"DEBUG", "BUILD"}, {}), [])
check("workflow missing everywhere", rot.d7(PROTOCOL.replace("| 4 | DEFAULT | rest | BUILD", "| 4 | X | rest | ORIENT"), VARIANTS, {"DEBUG", "BUILD"}, {"DEBUG", "BUILD"}, {}),
      ["rot:D7:chain:ORIENT", "rot:D7:jev:ORIENT", "rot:D7:readmap:ORIENT"])
check("ORIENT may lack only a chain key",
      rot.d7(PROTOCOL.replace("BUILD", "ORIENT"), {"DEBUG": ["s"], "ORIENT": ["standard"]}, {"DEBUG", "ORIENT"}, {"DEBUG"},
             {"ORIENT": "creates no artifact"}), [])
check("an exemption for any other workflow is itself a violation",
      rot.d7(PROTOCOL, VARIANTS, {"DEBUG", "BUILD"}, {"DEBUG"}, {"BUILD": "because"}), ["rot:D7:exemption:BUILD"])
check("the shipped exemption table holds only ORIENT", sorted(rot.NO_CHAIN_WORKFLOWS), ["ORIENT"])

print("D8 agent skills")
check("clean", rot.d8({"alpha": ["tdd"]}, {"tdd"}), [])
check("unknown skill", rot.d8({"alpha": ["tdd", "nope"]}, {"tdd"}), ["rot:D8:alpha:nope"])
check("frontmatter parse strips the plugin prefix",
      rot.agent_skills("---\nname: a\nskills:\n  - craftflow:tdd\n  - craftflow:x\nmodel: opus\n---\nbody\n  - craftflow:no\n"), ["tdd", "x"])

print("D9 cursor install list")
INSTALL = "for SKILL_NAME in cursor-router retro status; do\n  link\ndone\n"
check("clean", rot.d9(INSTALL, {"retro", "status"}), [])
check("extra and missing", rot.d9(INSTALL, {"retro", "failure-digest"}), ["rot:D9:extra:status", "rot:D9:missing:failure-digest"])
check("loop absent", rot.d9("nothing", {"retro"}), ["rot:D9:loop-missing"])

print("D10 README workflow table")
check("clean", rot.d10(PROTOCOL, PLUGIN_README), [])
check("table missing a workflow and naming an unknown one",
      rot.d10(PROTOCOL, PLUGIN_README.replace("| else | BUILD | y |", "| odd | REVIEW | y |")), ["rot:D10:missing:BUILD", "rot:D10:unknown:REVIEW"])

# --- shell cases (temp repo copies of the real surfaces) --------------------------
print("shell")
GATE_DIR = "tools/craftflow-plugin/plugins/craftflow"
BASELINE_REL = f"{GATE_DIR}/tests/fixtures/docs-rot-baseline.json"
SHELL = str(SCRIPTS / "craftflow_docs_rot.py")
COPY = [
    "tools/craftflow-plugin/README.md", "tools/craftflow-plugin/CHANGELOG.md", ".claude-plugin/marketplace.json",
    f"{GATE_DIR}/README.md", f"{GATE_DIR}/.claude-plugin/plugin.json", f"{GATE_DIR}/hooks.json", f"{GATE_DIR}/hooks/hooks.json",
    f"{GATE_DIR}/hooks/README.md", f"{GATE_DIR}/install-cursor.sh", f"{GATE_DIR}/config/router-readmap.json",
    f"{GATE_DIR}/skills/_shared/router-protocol.md", f"{GATE_DIR}/skills/cursor-router/SKILL.md",
    f"{GATE_DIR}/scripts/craftflow_status_report.py", f"{GATE_DIR}/scripts/craftflow_jev_prompt_hint.py",
]


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr}")
    return r.stdout.strip()


def make_root():
    root = Path(tempfile.mkdtemp(prefix="docs-rot-"))
    for rel in COPY:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, root / rel)
    for d in (PLUGIN / "agents").glob("*.md"):
        (root / GATE_DIR / "agents").mkdir(parents=True, exist_ok=True)
        shutil.copy(d, root / GATE_DIR / "agents" / d.name)
    for d in (PLUGIN / "skills").glob("*/SKILL.md"):
        (root / GATE_DIR / "skills" / d.parent.name).mkdir(parents=True, exist_ok=True)
        (root / GATE_DIR / "skills" / d.parent.name / "SKILL.md").write_text("x")
    (root / BASELINE_REL).parent.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "t")
    git(root, "config", "user.email", "t@example.invalid")
    return root


def write_baseline(root, ids):
    (root / BASELINE_REL).write_text(json.dumps({"version": 1, "ids": sorted(ids)}))


def commit(root, msg):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "HEAD")


def run_shell(root, *args):
    r = subprocess.run([sys.executable, "-I", SHELL, "--root", str(root), *args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def print_ids(root):
    rc, out = run_shell(root, "--print")
    return rc, [line for line in out.splitlines() if line.startswith("rot:")]


root = make_root()
commit(root, "base, no baseline file yet")
git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
rc, ids = print_ids(root)
check("--print exits 0", rc, 0)
check("--print lists only rot:D ids", all(i.startswith("rot:D") for i in ids), True)
write_baseline(root, ids)
rc, out = run_shell(root)
check("first introduction of the baseline (absent at the merge-base) is allowed: exit 0", rc, 0)
rc, out = run_shell(root, "--strict")
check("--strict: non-empty baseline fails, empty passes", rc, 1 if ids else 0)

# mutation control: delete one README agent row
top = root / "tools/craftflow-plugin/README.md"
original = top.read_text()
lines = [ln for ln in original.splitlines(keepends=True) if not ln.startswith("| `planner` |")]
top.write_text("".join(lines))
rc, mutated = print_ids(root)
check("mutation: deleting a README agent row yields a rot:D1 id", "rot:D1:missing-in-top:planner" in mutated, True)
rc, out = run_shell(root)
check("an unlisted violation: exit 1", rc, 1)
check("an unlisted violation: NEW line", "NEW rot:D1:missing-in-top:planner" in out.splitlines(), True)
top.write_text(original)
write_baseline(root, ids + ["rot:D1:ghost-entry"])
rc, out = run_shell(root)
check("a stale baseline id: exit 1", rc, 1)
check("a stale baseline id: STALE line", "STALE rot:D1:ghost-entry" in out.splitlines(), True)

# baseline subset rule (Behavior contract 8)
two = sorted(set(mutated))
top.write_text("".join(lines))
write_baseline(root, two)
c_pr_base = commit(root, "main commit with the violation baselined")
git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
rc, out = run_shell(root)
check("on main (merge-base is HEAD) the subset check passes: exit 0", rc, 0)
top.write_text(original)
write_baseline(root, ids)
c_shrink = commit(root, "pr child shrinks the baseline")
rc, out = run_shell(root)
check("PR shaped: a baseline that shrank past origin/main passes: exit 0", rc, 0)
git(root, "update-ref", "refs/remotes/origin/main", c_shrink)
top.write_text("".join(lines))
write_baseline(root, two)
c_grow = commit(root, "pr child grows the baseline")
rc, out = run_shell(root)
check("PR shaped: a baseline that grew past origin/main fails: exit 1", rc, 1)
check("PR shaped: the message names the id that grew", "grew" in out and "rot:D1:missing-in-top:planner" in out, True)

# resume control: HEAD is an older commit whose baseline is a superset of origin/main's
git(root, "checkout", "-q", "--detach", c_pr_base)
rc, out = run_shell(root)
check("resume: an older tag with a superset baseline passes subset, NEW and STALE: exit 0", rc, 0)
write_baseline(root, [])
rc, out = run_shell(root)
check("resume: the NEW check still runs", rc == 1 and "NEW rot:D1:missing-in-top:planner" in out.splitlines(), True)
git(root, "checkout", "-q", "--", ".")

rc, out = run_shell(root, "--base-ref", "refs/remotes/origin/nope")
check("an unresolvable base ref fails closed: exit 1", rc, 1)

shutil.rmtree(root, ignore_errors=True)

print(f"\nResults: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
