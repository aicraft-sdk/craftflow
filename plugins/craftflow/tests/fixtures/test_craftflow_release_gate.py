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
        "version_consistency", "hook_unit_tests", "fixture_suite_a", "fixture_suite_b", "rule_inventory_diff", "workflow_replay_check",
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
for shard, shard_step in (("1/2", "fixture_suite_a"), ("2/2", "fixture_suite_b")):
    check(f"{shard_step} runs shard {shard} of the suite from PLUGIN",
          (steps[shard_step].argv, steps[shard_step].cwd, steps[shard_step].env_set.get("FIXTURE_SHARD")),
          (["bash", "tests/fixtures/run_fixture_suite.sh"], gate.PLUGIN, shard))
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
for shard_step in ("fixture_suite_a", "fixture_suite_b"):
    env = gate.step_env(steps[shard_step], {"CRAFTFLOW_PERF": "1", "PATH": "/bin", "CI": "false", "FIXTURE_SHARD": "9/9"})
    check(f"{shard_step} env: CI=true", env.get("CI"), "true")
    check(f"{shard_step} env: CRAFTFLOW_PERF removed even when the caller sets it", "CRAFTFLOW_PERF" in env, False)
    check(f"{shard_step} env: FIXTURE_SHARD comes from the step, not the caller", env.get("FIXTURE_SHARD"), steps[shard_step].env_set["FIXTURE_SHARD"])
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

# --- workflow step bodies (extracted from the YAML and executed) -------------------
print("workflow steps")
import re  # noqa: E402
import subprocess  # noqa: E402

REPO = gate.ROOT
PUBLISH = (REPO / ".github/workflows/publish-craftflow-plugin.yml").read_text()
CI = (REPO / ".github/workflows/craftflow-ci.yml").read_text()


def step_run_body(text, name):
    """The dedented `run: |` body of the step called `name`, or None when absent."""
    m = re.search(r"^      - name: " + re.escape(name) + r"\s*\n((?:(?!      - ).*\n?)*)", text, re.MULTILINE)
    if not m:
        return None
    rm = re.search(r"^( +)run: \|\n((?:[ \t]*\n|\1[ \t]+.*\n?)*)", m.group(1), re.MULTILINE)
    if not rm:
        return None
    lines = rm.group(2).splitlines()
    indent = min(len(ln) - len(ln.lstrip()) for ln in lines if ln.strip())
    return "\n".join(ln[indent:] for ln in lines) + "\n"


def run_body(body, cwd, env=None):
    r = subprocess.run(["bash", "-c", body], cwd=str(cwd), capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


gate_body = step_run_body(PUBLISH, "Release gate")
check("publish workflow has a Release gate step body", gate_body is not None, True)
with tempfile.TemporaryDirectory() as empty:
    rc, out = run_body(gate_body or "false", empty)
check("resume path: script absent at the tag prints the pre-gate line and exits 1",
      (rc, out.strip().splitlines()[-1:]),
      (1, ["RELEASE GATE: FAIL (resume tag predates the release gate; publish a new version instead)"]))

clean_body = step_run_body(PUBLISH, "Release gate left the tree clean")
check("publish workflow has a clean-tree step body", clean_body is not None, True)
git_env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid")
with tempfile.TemporaryDirectory() as repo:
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, env=git_env)
    rc_clean, _ = run_body(clean_body or "false", repo, git_env)
    (Path(repo) / "stray-from-gate.txt").write_text("x")
    rc_dirty, out_dirty = run_body(clean_body or "false", repo, git_env)
check("clean tree: exit 0", rc_clean, 0)
check("dirty tree: exit 1 and the stray file is listed", (rc_dirty, "stray-from-gate.txt" in out_dirty), (1, True))
check("dirty tree: the refusal message is printed",
      "ERROR: the release gate changed the working tree; refusing to publish" in out_dirty, True)

ci_job = CI.split("\n  release-gate:\n", 1)[1] if "\n  release-gate:\n" in CI else ""
check("craftflow-ci has a release-gate job", bool(ci_job), True)
check("release-gate job: ubuntu, python 3.12, node from .nvmrc, pinned claude CLI, full history",
      all(s in ci_job for s in ("runs-on: ubuntu-latest", "python-version: '3.12'", "node-version-file: .nvmrc",
                               "@anthropic-ai/claude-code@2.1.311", "fetch-depth: 0")), True)
check("release-gate job: configures the CI git identity before the gate",
      "git config --global user.name craftflow-ci" in ci_job
      and ci_job.index("git config --global user.name craftflow-ci") < ci_job.index("craftflow_release_gate.py"), True)
check("release-gate job: runs the full gate and accepts only the exact last line",
      "craftflow_release_gate.py" in ci_job and 'test "$(tail -n1 /tmp/gate.log)" = "RELEASE GATE: OK"' in ci_job
      and "--allow-no-claude" not in ci_job and "--total-seconds" not in ci_job, True)
check("release-gate job: the clean-tree step follows the gate and there is no continue-on-error",
      "git status --porcelain" in ci_job and ci_job.index("git status --porcelain") > ci_job.index("craftflow_release_gate.py")
      and "continue-on-error" not in ci_job, True)

# --- the gate step cannot be neutralised (mutation controls on the PARSED workflow) -----
print("gate step guard")
import copy  # noqa: E402
import importlib.util  # noqa: E402

import yaml  # noqa: E402  (hard import: without PyYAML this group fails instead of skipping)

_spec = importlib.util.spec_from_file_location("publish_structure", REPO / ".github/test_publish_workflow_structure.py")
structure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(structure)

GATE_STEP = "      - name: Release gate\n        run: |\n"
CLEAN_STEP = "      - name: Release gate left the tree clean\n        run: |\n"
TEST_LINE = '          test "$(tail -n1 /tmp/gate.log)" = "RELEASE GATE: OK"\n'
STEP_RUN = "        run: |\n"
PY_LINE = '          python3 "$GATE" | tee /tmp/gate.log\n'
GATE_NAMES = ("Configure a CI git identity for the release gate", "Release gate", "Release gate left the tree clean")


def mutate(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def keyed(step, key_line):
    return step.replace(STEP_RUN, key_line + STEP_RUN)


def publish_defects(text_or_wf):
    wf = yaml.safe_load(text_or_wf) if isinstance(text_or_wf, str) else text_or_wf
    return structure.publish_guard_defects(wf)


def with_if(step_name, expr):
    """PUBLISH with the step's own `if:` replaced by expr.format(old if)."""
    m = re.search(r"      - name: " + re.escape(step_name) + r"\n(?:        (?!if:|run:).*\n)*?        if: (.*)\n", PUBLISH)
    assert m, step_name
    return PUBLISH[:m.start(1)] + expr.format(m.group(1)) + PUBLISH[m.end(1):]


def after_run(step_name, key_line):
    """PUBLISH with key_line appended to the step's mapping, AFTER its run: block."""
    start = PUBLISH.index("      - name: " + step_name + "\n")
    nxt = PUBLISH.index("\n      - ", start + 1)
    return PUBLISH[:nxt] + "\n" + key_line.rstrip("\n") + PUBLISH[nxt:]


def parsed(edit):
    wf = yaml.safe_load(PUBLISH)
    edit(wf)
    return wf


def step_named(wf, name):
    return next(s for s in wf["jobs"]["publish"]["steps"] if s.get("name") == name)


def move_gate_to_other_job(wf):
    steps = wf["jobs"]["publish"]["steps"]
    moved = [s for s in steps if s.get("name") in GATE_NAMES]
    wf["jobs"]["publish"]["steps"] = [s for s in steps if s.get("name") not in GATE_NAMES]
    wf["jobs"]["gates"] = {"runs-on": "ubuntu-latest", "steps": moved}


MIRROR = """
  mirror:
    runs-on: ubuntu-latest
    steps:
      - name: Mirror to craftflow-public
        env:
          TOKEN: ${{ secrets.CRAFTFLOW_PUBLIC_TOKEN }}
        run: |
          set -euo pipefail
          git push "https://x-access-token:${TOKEN}@github.com/aicraft-sdk/craftflow.git" HEAD:refs/heads/main
"""

check("unmodified publish workflow: no guard defects", publish_defects(PUBLISH), [])
mutants = {
    "continue-on-error on Release gate": mutate(PUBLISH, GATE_STEP, keyed(GATE_STEP, "        continue-on-error: true\n")),
    "continue-on-error on the clean-tree step": mutate(PUBLISH, CLEAN_STEP, keyed(CLEAN_STEP, "        continue-on-error: true\n")),
    "if: always() on Release gate": mutate(PUBLISH, GATE_STEP, keyed(GATE_STEP, "        if: always()\n")),
    "shell: true {0} on Release gate": mutate(PUBLISH, GATE_STEP, keyed(GATE_STEP, "        shell: true {0}\n")),
    "working-directory on Release gate": mutate(PUBLISH, GATE_STEP, keyed(GATE_STEP, "        working-directory: /tmp\n")),
    "env on Release gate": mutate(PUBLISH, GATE_STEP, keyed(GATE_STEP, "        env:\n          X: y\n")),
    "timeout-minutes on Release gate": mutate(PUBLISH, GATE_STEP, keyed(GATE_STEP, "        timeout-minutes: 1\n")),
    "shell: true {0} on the clean-tree step": mutate(PUBLISH, CLEAN_STEP, keyed(CLEAN_STEP, "        shell: true {0}\n")),
    "uses: on the identity step": mutate(PUBLISH, "      - name: Configure a CI git identity for the release gate\n",
                                         "      - name: Configure a CI git identity for the release gate\n        uses: ./noop\n"),
    "defaults shell: true {0}": mutate(PUBLISH, "    shell: bash\n", "    shell: true {0}\n"),
    "defaults working-directory": mutate(PUBLISH, "    shell: bash\n", "    shell: bash\n    working-directory: /tmp\n"),
    "early exit 0 before the verdict line": mutate(PUBLISH, TEST_LINE, "          exit 0\n" + TEST_LINE),
    "early exit 0 chained after then": mutate(PUBLISH, TEST_LINE, "          if true; then exit 0; fi\n" + TEST_LINE),
    "bare exit": mutate(PUBLISH, PY_LINE, "          exit\n" + PY_LINE),
    "exit;": mutate(PUBLISH, PY_LINE, "          exit;\n" + PY_LINE),
    'exit "0"': mutate(PUBLISH, PY_LINE, '          exit "0"\n' + PY_LINE),
    "[ -f x ] || exit": mutate(PUBLISH, PY_LINE, "          [ -f x ] || exit\n" + PY_LINE),
    "exec true": mutate(PUBLISH, PY_LINE, "          exec true\n" + PY_LINE),
    "set +e": mutate(PUBLISH, PY_LINE, "          set +e\n" + PY_LINE),
    "|| true on the python line": mutate(PUBLISH, PY_LINE, '          python3 "$GATE" | tee /tmp/gate.log || true\n'),
    "exit 0 after the verdict line": mutate(PUBLISH, TEST_LINE, TEST_LINE + "          exit 0\n"),
    "|| true on the clean-tree check": mutate(PUBLISH, '          DIRTY="$(git status --porcelain)"\n', '          DIRTY="$(git status --porcelain)" || true\n'),
    "set +e in the identity step": mutate(PUBLISH, "          git config --global user.name craftflow-ci\n", "          set +e\n          git config --global user.name craftflow-ci\n"),
    "always() && on Push to craftflow-public": with_if("Push to craftflow-public", "always() && {}"),
    "always() && on Commit and tag": with_if("Commit and tag", "always() && {}"),
    "failure() || on Subtree split": with_if("Subtree split", "failure() || {}"),
    "!cancelled() && on Create GitHub release": with_if("Create GitHub release on craftflow-public", '"!cancelled() && ({})"'),
    "success() || on Apply bump": with_if("Apply bump", "success() || {}"),
    "YAML-escaped always() on Subtree split": with_if("Subtree split", '"\\x61lways() && ({})"'),
    "if: true on Subtree split": with_if("Subtree split", "true"),
    "if: ${{ true }} on Push to craftflow-public": with_if("Push to craftflow-public", "${{{{ true }}}}"),
    "if: always() written after run: on Determinism cross-check": after_run("Determinism cross-check", "        if: always()"),
    "extra mirror job with no needs": PUBLISH.rstrip("\n") + "\n" + MIRROR,
    "gate steps moved into a separate job": parsed(move_gate_to_other_job),
    "duplicate step name": parsed(lambda wf: wf["jobs"]["publish"]["steps"].append(copy.deepcopy(step_named(wf, "Apply bump")))),
    "job-level if: always()": parsed(lambda wf: wf["jobs"]["publish"].update({"if": "always()"})),
    "job-level continue-on-error: true": parsed(lambda wf: wf["jobs"]["publish"].update({"continue-on-error": True})),
    "job-level defaults shell: sh": parsed(lambda wf: wf["jobs"]["publish"].update({"defaults": {"run": {"shell": "sh"}}})),
}
for label, mutant in mutants.items():
    check_true("structure check rejects " + label, bool(publish_defects(mutant)))

diff_defects = publish_defects(mutate(PUBLISH, PY_LINE, "          exit\n" + PY_LINE))
check_true("canonical-body mismatch prints a unified diff and the update hint",
           any("+exit" in d and "PUBLISH_GATE_BODIES" in d for d in diff_defects), diff_defects)

CI_WF = yaml.safe_load(CI)
check("craftflow-ci gate steps: no defects", structure.ci_guard_defects(CI_WF), [])
check_true("craftflow-ci gate step: early exit 0 rejected", bool(structure.ci_guard_defects(yaml.safe_load(
    mutate(CI, '          test "$(tail -n1 /tmp/gate.log)"', '          exit 0\n          test "$(tail -n1 /tmp/gate.log)"')))))
check_true("craftflow-ci gate step: shell: true {0} rejected", bool(structure.ci_guard_defects(yaml.safe_load(
    mutate(CI, GATE_STEP, keyed(GATE_STEP, "        shell: true {0}\n"))))))
check_true("craftflow-ci release-gate job: continue-on-error rejected", bool(structure.ci_guard_defects(yaml.safe_load(
    mutate(CI, "    name: release gate\n", "    name: release gate\n    continue-on-error: true\n")))))

print(f"\nResults: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
