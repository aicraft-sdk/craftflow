#!/usr/bin/env python3
"""Tests for scripts/craftflow_local_gate.py. Steps are data, so most cases use fake steps (`--steps-file`) instead of
the real checks.

Run from anywhere:
    python3 tests/fixtures/test_craftflow_local_gate.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import craftflow_local_gate as gate

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "craftflow_local_gate.py"
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
PASS = 0
FAIL = 0


def check(name, actual, expected):
    global PASS, FAIL
    if actual == expected:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}\n    expected: {expected!r}\n    actual:   {actual!r}")
        FAIL += 1


def py(name, code, **extra):
    return gate.make_step(name, [sys.executable, "-c", code], **extra)


OK = "print('fine')"
BAD = "import sys; print('line one'); print('the real problem'); sys.exit(1)"
WORK = Path(tempfile.mkdtemp(prefix="gate-test-"))
EMPTY_BIN = WORK / "emptybin"
EMPTY_BIN.mkdir()


def run(steps, *args, env=None):
    path = WORK / "steps.json"
    path.write_text(json.dumps([{k: v for k, v in s.items() if v not in (None, "", [])} for s in steps]))
    full = {k: v for k, v in os.environ.items() if k != "CI"}
    full.update(env or {})
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--steps-file", str(path), *args], capture_output=True, text=True, env=full)
    return proc.returncode, proc.stdout + proc.stderr


def fake_ruff(exit_code):
    d = Path(tempfile.mkdtemp(prefix="fake-ruff-", dir=WORK))
    exe = d / "ruff"
    exe.write_text(f"#!/bin/sh\necho fake ruff\nexit {exit_code}\n")
    exe.chmod(0o755)
    return str(d)


print("[pass / fail / continue]")
code, out = run([py("a", OK), py("b", OK)])
check("all steps pass -> 0", code, 0)
check("summary says GATE PASSED", "GATE PASSED" in out and "2 passed, 0 FAILED" in out, True)
code, out = run([py("a", OK), py("b", BAD), py("c", OK)])
check("one step fails -> 2", code, 2)
check("steps after the failure still run (documented: no fail-fast)", "[PASS] c" in out, True)
check("failing step output tail is shown", "the real problem" in out, True)
check("summary names the failed step", "GATE FAILED: b" in out, True)
code, out = run([dict(name="nostart", argv=["/nonexistent/binary"])])
check("a step that cannot start is FAIL (fail-closed)", (code, "could not start" in out), (2, True))
res = gate.run_step(py("slow", "import time; time.sleep(30)"), timeout=1)
check("a step that times out is FAIL", (res["status"], "timed out" in res["reason"]), ("fail", True))
check("tool errors exit 1: bad steps file", run([{"name": "x"}])[0], 1)
check("tool errors exit 1: unknown option", subprocess.run([sys.executable, "-B", str(SCRIPT), "--nope"], capture_output=True).returncode, 1)

print("[missing ruff]")
ruff_step = gate.make_step("ruff", ["ruff", "check"], needs="ruff", hint="pip install ruff")
no_ruff = {"PATH": str(EMPTY_BIN)}
code, out = run([ruff_step, py("b", OK)], env=no_ruff)
check("missing ruff locally -> SKIP, gate exits 0", code, 0)
check("SKIP is visible with the install hint", "[SKIP] ruff" in out and "pip install ruff" in out, True)
check("summary notes skipped steps", "marked SKIP did not run" in out, True)
check("--require-ruff -> FAIL, exit 2", run([ruff_step], "--require-ruff", env=no_ruff)[0], 2)
code, out = run([ruff_step], env=dict(no_ruff, CI="true"))
check("CI=true -> FAIL, exit 2", (code, "required but missing" in out), (2, True))
check("ruff present and clean -> PASS", run([ruff_step], env={"PATH": fake_ruff(0)})[0], 0)
check("ruff present and failing -> exit 2", run([ruff_step], env={"PATH": fake_ruff(1)})[0], 2)

print("[flags are loud]")
steps = [py("a", OK), py("b", BAD), py("c", OK, group="suites")]
code, out = run(steps, "--skip", "b")
check("--skip b -> 0 but loud", (code, "[SKIPPED BY FLAG] b" in out, "NOT the full gate" in out), (0, True, True))
code, out = run(steps, "--only", "a")
check("--only a flags every other step", (code, "[SKIPPED BY FLAG] b" in out, "[SKIPPED BY FLAG] c" in out), (0, True, True))
check("--skip a group", "[SKIPPED BY FLAG] c" in run(steps, "--skip", "suites")[1], True)
check("unknown step name -> 1", run(steps, "--skip", "nope")[0], 1)
check("flags refused when CI=true -> 1", run(steps, "--skip", "b", env={"CI": "true"})[0], 1)
check("--fast refused when CI=true -> 1", run(steps, "--fast", env={"CI": "true"})[0], 1)
code, out = run([py("a", OK), py("big", BAD, tags=["slow"])], "--fast")
check("--fast skips slow steps loudly", (code, "[SKIPPED BY FLAG] big" in out, "--fast" in out), (0, True, True))
check("--list prints and exits 0", run(steps, "--list")[0], 0)

print("[json shape]")
code, out = run([py("a", OK), py("b", BAD), py("c", OK, needs="ruff", hint="h")], "--format", "json", env=no_ruff)
data = json.loads(out)
check("json exit matches", (code, data["exit"]), (2, 2))
check("json summary", {k: data["summary"][k] for k in ("passed", "failed", "skipped", "skipped_by_flag")}, {"passed": 1, "failed": 1, "skipped": 1, "skipped_by_flag": 0})
check("json steps carry name/status/returncode/seconds/reason/output_tail", sorted(data["steps"][0]), ["name", "output_tail", "reason", "returncode", "seconds", "status"])
check("json failing step has its tail", "the real problem" in data["steps"][1]["output_tail"], True)
check("json statuses", [s["status"] for s in data["steps"]], ["pass", "fail", "skip"])

print("[root-sensitive suites]")
opts = {"only": [], "skip": [], "fast": False, "is_root": True, "which": lambda _: "x", "require": {}}
root_step = py("suite:r", OK, tags=["root"])
res = gate.run_gate([root_step], opts)
check("uid 0 -> root suite is SKIP with a reason, not PASS", (res[0]["status"], "uid 0" in res[0]["reason"]), ("skip", True))
res = gate.run_gate([root_step], dict(opts, is_root=False))
check("non-root -> it runs", res[0]["status"], "pass")
check("arch-lint is registered as root-sensitive", ("test_craftflow_arch_lint", "root") in gate.SUITES, True)

print("[real step list]")
args = gate.parse_args([])
saved = os.environ.pop("CI", None)
steps = gate.build_steps(gate.ROOT, args)
names = [s["name"] for s in steps]
check("fixed leading order", names[:6], ["ruff", "suppression-ratchet", "file-size-ratchet", "clean-state", "baseline-integrity", "py-compile"])
check("guardrail-refs follows py-compile and is in the pre-commit subset", (names[6], "--only guardrail-refs" in (gate.ROOT / ".githooks" / "pre-commit").read_text()), ("guardrail-refs", True))
check("guardrail-refs suite is listed", ("test_craftflow_guardrail_refs", "") in gate.SUITES, True)
check("no duplicate suites", len(gate.SUITES), len({s for s, _ in gate.SUITES}))
check("every listed suite exists", [s for s, _ in gate.SUITES if not (PLUGIN / "tests" / "fixtures" / f"{s}.py").is_file()], [])
check("clean-state runs in block mode", "block" in steps[3]["argv"], True)
check("local run allows a missing base", "--allow-missing-base" in steps[4]["argv"], True)
check("--require-base drops --allow-missing-base", "--allow-missing-base" in gate.build_steps(gate.ROOT, gate.parse_args(["--require-base"]))[4]["argv"], False)
os.environ["CI"] = "true"
check("CI=true drops --allow-missing-base", "--allow-missing-base" in gate.build_steps(gate.ROOT, gate.parse_args([]))[4]["argv"], False)
del os.environ["CI"]
check("--base-ref reaches baseline-integrity", "--base-ref" in gate.build_steps(gate.ROOT, gate.parse_args(["--base-ref", "origin/main"]))[4]["argv"], True)
if saved is not None:
    os.environ["CI"] = saved

print("[clean-state over the PR diff]")


def git(root, *a):
    subprocess.run(["git", "-C", str(root), *a], capture_output=True, check=True, env=GIT_ENV)


repo = Path(tempfile.mkdtemp(prefix="gate-pr-", dir=WORK))
git(repo, "init", "-q", "-b", "main")
(repo / "ok.py").write_text("x = 1\n")
git(repo, "add", "-A")
git(repo, "commit", "-q", "-m", "base")
git(repo, "checkout", "-q", "-b", "pr")
(repo / "bad.py").write_text("import os  # noqa\n")
git(repo, "add", "-A")
git(repo, "commit", "-q", "-m", "pr adds a bare noqa")
check("committed bare noqa is judged against the base -> 2", gate.clean_state_pr_diff("main", repo), 2)
git(repo, "reset", "-q", "--hard", "main")
(repo / "good.py").write_text("import os  # noqa: F401 - re-exported for callers\n")
git(repo, "add", "-A")
git(repo, "commit", "-q", "-m", "pr with an explained noqa")
check("explained suppression in the PR diff -> 0", gate.clean_state_pr_diff("main", repo), 0)
check("unresolvable base -> 1", gate.clean_state_pr_diff("no-such-ref", repo), 1)
check("worktree is cleaned up", subprocess.run(["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True).stdout.count("\n"), 1)

print(f"\nResults: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
