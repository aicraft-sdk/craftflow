#!/usr/bin/env python3
"""Tests for run_fixture_suite.sh, the CI fixture-loop verdict (P0 review finding H-H5 / R-L2).

Run: python3 tests/fixtures/test_ci_fixture_runner.py
Dry-runs the wrapper against throwaway fixture directories with CRAFTFLOW_CI_KNOWN_ENV_FAILURES set.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "run_fixture_suite.sh"
REPO = HERE.parents[5]
WORKFLOW = REPO / ".github" / "workflows" / "craftflow-ci.yml"
CODEOWNERS = REPO / ".github" / "CODEOWNERS"
PLUGIN_REL = "tools/craftflow-plugin/plugins/craftflow"
_passes = 0
_errors: list = []


def check(name: str, cond: bool, reason: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason}")
        print(f"  FAIL: {name}: {reason or 'assertion false'}")


def make_fixture(directory: Path, name: str, failed) -> None:
    if isinstance(failed, str):  # a raw script body
        (directory / name).write_text(failed, encoding="utf-8")
        return
    body = f"print('Results: 4 passed, {failed} failed')\nraise SystemExit({1 if failed else 0})\n"
    (directory / name).write_text(body, encoding="utf-8")


def run(files: dict, known: str, ci=None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        fixtures = Path(tmp) / "fixtures"
        fixtures.mkdir()
        for name, failed in files.items():
            make_fixture(fixtures, name, failed)
        env = dict(os.environ, FIXTURE_DIR=str(fixtures), RUNNER_TEMP=tmp, CRAFTFLOW_CI_KNOWN_ENV_FAILURES=known)
        env.pop("CI", None)
        if ci is not None:
            env["CI"] = ci
        return subprocess.run(["bash", str(RUNNER)], capture_output=True, text=True, env=env)


def test_pinned_failure_is_excused_only_with_the_pinned_count() -> None:
    known = "test_agent_structure.py | 11 | stale assertions\n"
    same = run({"test_agent_structure.py": 11, "test_ok.py": 0}, known)
    more = run({"test_agent_structure.py": 12, "test_ok.py": 0}, known)
    fewer = run({"test_agent_structure.py": 10, "test_ok.py": 0}, known)
    check("H-H5 listed file failing with exactly the pinned count passes", same.returncode == 0, same.stdout + same.stderr)
    check("H-H5 count mismatch (more failures) fails the job", more.returncode != 0 and "pins 11" in more.stdout, more.stdout)
    check("H-H5 count mismatch (fewer failures) fails the job", fewer.returncode != 0 and "pins 11" in fewer.stdout, fewer.stdout)


def test_unexpected_pass_fails() -> None:
    res = run({"test_agent_structure.py": 0}, "test_agent_structure.py | 11 | stale assertions\n")
    check("H-H5 a listed file that now passes fails the job", res.returncode != 0 and "remove the entry" in res.stdout, res.stdout)


def test_unlisted_failure_fails() -> None:
    res = run({"test_other.py": 1}, "test_agent_structure.py | 11 | stale assertions\n")
    check("an unlisted failing file fails the job (and the stale entry is reported)", res.returncode != 0 and "test_other.py failed" in res.stdout, res.stdout)


def test_name_is_not_a_regex() -> None:
    res = run({"test_zz.py": 2}, "test_.*\\.py | 2 | would excuse everything if treated as a regex\n")
    check("H-H5 the listed name is compared literally, not as a regex", res.returncode != 0 and "test_zz.py failed" in res.stdout, res.stdout)


def test_malformed_entry_fails() -> None:
    res = run({"test_a.py": 0}, "test_a.py | many | no numeric count\n")
    check("an entry without a numeric failed count fails the job", res.returncode != 0 and "needs" in res.stdout, res.stdout)


def test_no_env_means_no_excuses() -> None:
    check("an empty allowance passes when everything passes", run({"test_a.py": 0}, "").returncode == 0)
    check("an empty allowance fails on any failure", run({"test_a.py": 1}, "").returncode != 0)


def test_l3_count_is_read_from_the_final_results_line_only() -> None:
    known = "test_agent_structure.py | 11 | stale assertions\n"
    crash = "print('Traceback: 11 failed to import')\nraise SystemExit(1)\n"
    res = run({"test_agent_structure.py": crash}, known)
    check("L3a a crash printing '11 failed' without a Results line is not excused",
          res.returncode != 0 and "pins 11" in res.stdout, res.stdout)
    mixed = "print('note: 11 failed earlier')\nprint('Results: 4 passed, 3 failed')\nraise SystemExit(1)\n"
    res = run({"test_agent_structure.py": mixed}, known)
    check("L3a the final Results line wins over earlier '11 failed' text", res.returncode != 0 and "pins 11" in res.stdout, res.stdout)
    pinned = "print('3 failed in a log line')\nprint('Results: 4 passed, 11 failed')\nraise SystemExit(1)\n"
    res = run({"test_agent_structure.py": pinned}, known)
    check("L3a a matching final Results line is excused", res.returncode == 0, res.stdout)


def test_l3_duplicate_stale_and_empty() -> None:
    dup = "test_a.py | 1 | first\ntest_a.py | 2 | second\n"
    res = run({"test_a.py": 1}, dup)
    check("L3b duplicate entries for one file are an error", res.returncode != 0 and "more than once" in res.stdout, res.stdout)
    res = run({"test_a.py": 0, "helper.py": "print('x')\n"}, "helper.py | 1 | not a test file\n")
    check("L3c an entry naming a non-test file is an error", res.returncode != 0 and "test_*.py" in res.stdout, res.stdout)
    res = run({}, "")
    check("L3d an empty fixture directory says so and fails", res.returncode != 0 and "no test_*.py fixtures" in res.stdout, res.stdout)


def test_workflow_uses_the_wrapper_with_an_empty_known_failure_list() -> None:
    # P1 fixed the 11 stale test_agent_structure.py assertions, so the temporary "| 11 |" pin was removed from the
    # workflow (requirement changed, not loosened): every fixture must now pass with no excused failure.
    text = WORKFLOW.read_text(encoding="utf-8")
    check("workflow runs the wrapper with an empty known-failure list", "run_fixture_suite.sh" in text
          and 'CRAFTFLOW_CI_KNOWN_ENV_FAILURES: ""' in text and "test_agent_structure.py | 11 |" not in text
          and "continue-on-error:" not in text,
          "workflow not wired to run_fixture_suite.sh with an empty known-failure list")


SKIPPED = ("print('  SKIP: changed_gated_files_have_rows: no origin/main ref (CI must not skip)')\n"
           "print('Results: 4 passed, 0 failed, 1 skipped')\n")


def test_ci_refuses_a_skipped_origin_check() -> None:
    """M1: a fixture that skipped its origin/main comparison is fine on a laptop and a failure in CI (fetch-depth 0)."""
    local = run({"test_budget.py": SKIPPED}, "")
    ci = run({"test_budget.py": SKIPPED}, "", ci="true")
    other_skip = run({"test_node.py": "print('  SKIP: node_parity: node unavailable')\nprint('Results: 1 passed, 0 failed, 1 skipped')\n"}, "", ci="true")
    clean = run({"test_budget.py": 0}, "", ci="true")
    check("M1 a skip that CI must not take passes locally", local.returncode == 0, local.stdout)
    check("M1 the same skip fails the job when CI=true", ci.returncode != 0 and "must not skip" in ci.stdout and "test_budget.py" in ci.stdout, ci.stdout)
    check("M1 an unrelated skip (node missing) is not a CI failure", other_skip.returncode == 0, other_skip.stdout)
    check("M1 a clean run still passes under CI=true", clean.returncode == 0, clean.stdout)


def codeowners_patterns(text: str) -> list:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and len(line.split()) >= 2:
            out.append(line.split()[0])
    return out


def pattern_regex(pat: str) -> "re.Pattern":
    """CODEOWNERS/gitignore-style glob for a repo-relative path: `*` stays inside a segment, `**` crosses them, a trailing `/` owns a tree."""
    anchored = pat.startswith("/") or "/" in pat.rstrip("/")
    pat = pat.lstrip("/")
    body, i = "", 0
    while i < len(pat):
        if pat.startswith("**", i):
            body, i = body + ".*", i + 2
        elif pat[i] == "*":
            body, i = body + "[^/]*", i + 1
        elif pat[i] == "?":
            body, i = body + "[^/]", i + 1
        else:
            body, i = body + re.escape(pat[i]), i + 1
    if pat.endswith("/"):
        body += ".*"
    return re.compile(("^" if anchored else "^(?:.*/)?") + body + "$")


def uncovered(paths: list, patterns: list) -> list:
    regexes = [pattern_regex(p) for p in patterns]
    return sorted(p for p in paths if not any(r.match(p) for r in regexes))


def test_codeowners_cover_router_budget_fixtures_and_scripts() -> None:
    """H3: every router fixture JSON, router script and router test is owned, so a new one cannot be added uncovered."""
    plugin = REPO / PLUGIN_REL
    paths = sorted(f"{PLUGIN_REL}/{p.relative_to(plugin).as_posix()}"
                   for pat in ("tests/fixtures/router-*.json", "scripts/craftflow_router_*.py", "tests/fixtures/test_router_*.py")
                   for p in plugin.glob(pat))
    patterns = codeowners_patterns(CODEOWNERS.read_text(encoding="utf-8"))
    missing = uncovered(paths, patterns)
    named = {"router-budget.json", "router-cut-plan.json", "router-projection.json", "router-readmap.projected.json", "router-normative-allowlist.json",
             "test_router_budget.py", "test_router_readset.py", "craftflow_router_readset.py", "craftflow_router_readset_core.py",
             "craftflow_router_readset_project.py"}
    present = {p.rsplit("/", 1)[1] for p in paths}
    # negative controls: the matcher flags files an owner list does not name, and honours `*` only inside one segment
    sample = ["tools/x/a.json @o"]
    ctl_new = uncovered(["tools/x/a.json", "tools/x/b.json", "tools/x/sub/a.json"], codeowners_patterns("\n".join(sample)))
    ctl_glob = uncovered(["tools/x/a.json", "tools/x/sub/a.json"], codeowners_patterns("tools/x/*.json @o"))
    check("H3 CODEOWNERS covers every router fixture, script and router test", not missing and named <= present and len(paths) >= 20
          and ctl_new == ["tools/x/b.json", "tools/x/sub/a.json"] and ctl_glob == ["tools/x/sub/a.json"],
          f"uncovered={missing[:12]} ({len(missing)} total) named_present={named <= present} controls={ctl_new} {ctl_glob}")


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # report per-test crashes without hiding the rest
                check(name, False, f"{type(exc).__name__}: {exc}")
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    for e in _errors:
        print(e)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
