#!/usr/bin/env python3
"""Tests for run_fixture_suite.sh, the CI fixture-loop verdict (P0 review finding H-H5 / R-L2).

Run: python3 tests/fixtures/test_ci_fixture_runner.py
Dry-runs the wrapper against throwaway fixture directories with CRAFTFLOW_CI_KNOWN_ENV_FAILURES set.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "run_fixture_suite.sh"
WORKFLOW = HERE.parents[5] / ".github" / "workflows" / "craftflow-ci.yml"
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


def make_fixture(directory: Path, name: str, failed: int) -> None:
    body = f"print('Results: 4 passed, {failed} failed')\nraise SystemExit({1 if failed else 0})\n"
    (directory / name).write_text(body, encoding="utf-8")


def run(files: dict, known: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        fixtures = Path(tmp) / "fixtures"
        fixtures.mkdir()
        for name, failed in files.items():
            make_fixture(fixtures, name, failed)
        env = dict(os.environ, FIXTURE_DIR=str(fixtures), RUNNER_TEMP=tmp, CRAFTFLOW_CI_KNOWN_ENV_FAILURES=known)
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


def test_workflow_uses_the_wrapper_with_a_pinned_count() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    check("workflow runs the wrapper and pins the failure count", "run_fixture_suite.sh" in text
          and "test_agent_structure.py | 11 |" in text and "continue-on-error:" not in text,
          "workflow not wired to run_fixture_suite.sh")


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
