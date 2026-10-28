#!/usr/bin/env python3
"""Tests for scripts/craftflow_release_gate.py (the fail-closed release gate runner).

Run from anywhere:
    python3 tests/fixtures/test_craftflow_release_gate.py
"""
import contextlib
import io
import os
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import craftflow_release_gate as gate  # noqa: E402

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


def check_true(name, condition, detail=""):
    check(name + (f" [{detail}]" if detail and not condition else ""), bool(condition), True)


def step(step_id, needs_claude=False):
    return gate.Step(id=step_id, argv=["true"], cwd=Path("."), needs_claude=needs_claude)


def run(steps, argv, runner, which=lambda name: "/usr/bin/claude"):
    """Run the gate with an injected step table; return (exit code, stdout lines)."""
    args = gate.parse_args(argv)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = gate.run_gate(steps, args, runner, which=which)
    return rc, buf.getvalue().splitlines()


def fake(results):
    """A runner that returns a preset rc per step id (default 0) and records the caps."""
    caps = {}

    def runner(s, cap):
        caps[s.id] = cap
        return results.get(s.id, 0)

    runner.caps = caps
    return runner


# --- verdicts ---------------------------------------------------------------------
print("verdicts")
rc, out = run([step("a"), step("b")], [], fake({}))
check("all pass: exit 0", rc, 0)
check("all pass: last line", out[-1], "RELEASE GATE: OK")

rc, out = run([step("a"), step("x"), step("c")], [], fake({"x": 1}))
check("one fails: exit 1", rc, 1)
check("one fails: last line", out[-1], "RELEASE GATE: FAIL (x)")

runner = fake({"x": gate.TIMED_OUT_RC})
rc, out = run([step("x"), step("y")], [], runner)
check("timeout: exit 1", rc, 1)
check("timeout: message", "FAIL x: timed out after 600 s" in out, True)
check("timeout: gate continues with the next step", "y" in runner.caps, True)
check("timeout: last line", out[-1], "RELEASE GATE: FAIL (x)")

rc, out = run([step("a"), step("plugin_validate", needs_claude=True)], ["--allow-no-claude"], fake({}), which=lambda n: None)
check("claude absent with flag: exit 1", rc, 1)
check("claude absent with flag: PARTIAL", out[-1], "RELEASE GATE: PARTIAL (plugin_validate)")

rc, out = run([step("a"), step("plugin_validate", needs_claude=True)], [], fake({}), which=lambda n: None)
check("claude absent without flag: exit 1", rc, 1)
check("claude absent without flag: FAIL", out[-1], "RELEASE GATE: FAIL (plugin_validate)")

rc, out = run([step("p", needs_claude=True), step("q", needs_claude=True)], ["--allow-no-claude"], fake({}), which=lambda n: None)
check("all skipped: exit 1", rc, 1)
check("all skipped: NOTHING RAN", out[-1].startswith("RELEASE GATE: NOTHING RAN"), True)

rc, out = run([step("a"), step("b")], ["--only", "a"], fake({}))
check("subset run is never OK: exit 1", rc, 1)
check("subset run: PARTIAL names the steps not run", out[-1], "RELEASE GATE: PARTIAL (not run: b)")

# --- CLI shell --------------------------------------------------------------------
print("cli")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = gate.main(["--list"])
check("--list: exit 0", rc, 0)
check(
    "--list prints the step ids in the fixed order",
    buf.getvalue().split(),
    [
        "version_consistency", "hook_unit_tests", "fixture_suite", "rule_inventory_diff", "workflow_replay_check",
        "graph_check", "event_contract", "decisions", "docs_rot", "plugin_validate",
    ],
)
err = io.StringIO()
with contextlib.redirect_stderr(err):
    rc = gate.main(["--only", "nope"])
check("unknown --only: exit 2", rc, 2)
check("unknown --only: names the valid ids", "valid:" in err.getvalue() and "docs_rot" in err.getvalue(), True)

# --- layout and step table --------------------------------------------------------
print("layout")
me = Path(gate.__file__).resolve()
check("ROOT holds the gate script at its documented path",
      gate.ROOT / "tools/craftflow-plugin/plugins/craftflow/scripts/craftflow_release_gate.py", me)
check("PLUGIN is the parent of scripts/", gate.PLUGIN, me.parents[1])
steps = {s.id: s for s in gate.build_steps()}
check("fixture_suite runs the suite from PLUGIN", (steps["fixture_suite"].argv, steps["fixture_suite"].cwd),
      (["bash", "tests/fixtures/run_fixture_suite.sh"], gate.PLUGIN))
for node_step, script in (("graph_check", "gen-craftflow-graph.mjs"), ("event_contract", "verify-craftflow-event-contract.mjs"),
                          ("decisions", "verify-decision-format.mjs")):
    check(f"{node_step} runs node in ROOT", (steps[node_step].argv[:2], steps[node_step].cwd),
          (["node", f"scripts/{script}"], gate.ROOT))
check("graph_check passes --check", steps["graph_check"].argv[-1], "--check")
check("plugin_validate runs claude from tools/craftflow-plugin",
      (steps["plugin_validate"].argv, steps["plugin_validate"].cwd, steps["plugin_validate"].needs_claude),
      (["claude", "plugin", "validate", "plugins/craftflow"], gate.ROOT / "tools/craftflow-plugin", True))
inv = steps["rule_inventory_diff"].argv
check("rule_inventory_diff is the --diff invocation", inv[1].endswith("craftflow_rule_inventory.py") and "--diff" in inv, True)
check_true("rule_inventory_diff passes the lock and the policy map", "--lock" in inv and "--policy-map" in inv)
env = gate.step_env(steps["fixture_suite"], {"CRAFTFLOW_PERF": "1", "PATH": "/bin", "CI": "false"})
check("fixture_suite env: CI=true", env.get("CI"), "true")
check("fixture_suite env: CRAFTFLOW_PERF removed even when the caller sets it", "CRAFTFLOW_PERF" in env, False)
check("other steps keep the caller env", gate.step_env(steps["decisions"], {"CRAFTFLOW_PERF": "1"}).get("CRAFTFLOW_PERF"), "1")

# --- budgets (real subprocesses) --------------------------------------------------
print("budgets")
py = sys.executable
tmp = Path(tempfile.mkdtemp(prefix="gate-test-"))
pidfile = tmp / "grandchild.pid"
spawner = (
    "import subprocess, sys, time\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "open(sys.argv[1], 'w').write(str(p.pid))\n"
    "time.sleep(60)\n"
)
sleeper = gate.Step(id="spawner", argv=[py, "-c", spawner, str(pidfile)], cwd=tmp)
buf = io.StringIO()
started = time.monotonic()
with contextlib.redirect_stdout(buf):
    rc = gate.main(["--max-step-seconds", "1"], steps=[sleeper])
elapsed = time.monotonic() - started
check("hung step: gate fails", rc, 1)
check("hung step: timeout message", "FAIL spawner: timed out after 1 s" in buf.getvalue().splitlines(), True)
check_true("hung step: killed promptly", elapsed < 10, f"{elapsed:.1f} s")
gone = False
if pidfile.exists():
    pid = int(pidfile.read_text())
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            gone = True
            break
        time.sleep(0.1)
check("grandchild pid no longer exists after the cap", gone, True)

nap = "import time; time.sleep(2)"
three = [gate.Step(id=f"step{i}", argv=[py, "-c", nap], cwd=tmp) for i in (1, 2, 3)]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = gate.main(["--total-seconds", "3"], steps=three)
lines = buf.getvalue().splitlines()
check("total budget: exit 1", rc, 1)
check("total budget: step 2 capped at the remaining second", "FAIL step2: timed out after 1 s" in lines, True)
check("total budget: step 3 never runs", "FAIL step3: total budget exhausted" in lines, True)
check("total budget: step 3 has no command line", "$ " + " ".join([py, "-c", nap]) in lines and
      lines.count("$ " + " ".join([py, "-c", nap])) == 2, True)
check("total budget: verdict", lines[-1], "RELEASE GATE: FAIL (step2, step3)")
check("wall time line precedes the verdict",
      lines[-2].startswith("gate wall time: ") and lines[-2].endswith(" s"), True)

rc, out = run([step("a")], [], fake({}))
check("wall time is printed on a passing run too", out[-2].startswith("gate wall time: "), True)

print(f"\nResults: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
