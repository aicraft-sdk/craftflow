#!/usr/bin/env python3
"""Tests for craftflow_resolve_workspace_root.py.

Covers:
  - find_repo_candidates(): zero/one/many real nested git repos, exclusion of
    non-repo dirs, exclusion of dirs nested inside an unrelated ancestor repo
  - match_request_text(): unique-token match, no-match, multi-match rejection
  - resolve(): DETERMINISTIC (single candidate, matched candidate),
    AMBIGUOUS, NO_REPO_FOUND
  - CLI (subprocess): JSON on stdout, exit codes

Run: python3 tests/fixtures/test_resolve_workspace_root.py
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_resolve_workspace_root import (  # noqa: E402
    find_repo_candidates,
    match_request_text,
    resolve,
)

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def test_find_repo_candidates_zero_when_no_children() -> None:
    print("\n[find_repo_candidates]")
    with tempfile.TemporaryDirectory() as tmp:
        result = find_repo_candidates(Path(tmp))
        if result == []:
            ok("zero children -> []")
        else:
            fail("zero-children", f"expected [], got {result}")


def test_find_repo_candidates_zero_when_children_not_repos() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "plain-dir").mkdir()
        result = find_repo_candidates(Path(tmp))
        if result == []:
            ok("non-repo children -> []")
        else:
            fail("non-repo-children", f"expected [], got {result}")


def test_find_repo_candidates_finds_nested_repos() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo_a = root / "ai-platform-core"
        repo_b = root / "genai-platform-dev"
        _init_repo(repo_a)
        _init_repo(repo_b)
        (root / "not-a-repo").mkdir()
        result = find_repo_candidates(root)
        expected = sorted([repo_a.resolve(), repo_b.resolve()])
        if result == expected:
            ok("2 nested repos found, plain dir excluded")
        else:
            fail("nested-repos", f"expected {expected}, got {result}")


def test_find_repo_candidates_excludes_child_nested_in_ancestor_repo() -> None:
    # workspace_root itself sits inside an OUTER git repo (root/.git) --
    # every child of workspace_root would report that outer repo as its own
    # "toplevel" via plain `git rev-parse --show-toplevel`; none of them are
    # actually independent owning repos and must be excluded.
    with tempfile.TemporaryDirectory() as tmp:
        outer = Path(tmp)
        _init_repo(outer)
        workspace_root = outer / "workspace"
        child = workspace_root / "looks-like-a-repo"
        child.mkdir(parents=True)
        result = find_repo_candidates(workspace_root)
        if result == []:
            ok("child nested inside unrelated ancestor repo excluded")
        else:
            fail("ancestor-repo-exclusion", f"expected [], got {result}")


def test_find_repo_candidates_excludes_symlinked_child() -> None:
    # A symlinked child pointing at an unrelated real git repo elsewhere on
    # disk must never be offered as a candidate -- the design's intent is
    # "immediate child directories", not arbitrary symlink targets. Prior to
    # the fix, `p.is_dir()` follows symlinks and this became a silent
    # single-candidate DETERMINISTIC bypass (no AskUserQuestion confirmation).
    with tempfile.TemporaryDirectory() as external_tmp, tempfile.TemporaryDirectory() as tmp:
        external_repo = Path(external_tmp) / "external-repo"
        _init_repo(external_repo)
        workspace_root = Path(tmp)
        symlink_child = workspace_root / "symlinked-repo"
        symlink_child.symlink_to(external_repo, target_is_directory=True)
        result = find_repo_candidates(workspace_root)
        if result == []:
            ok("symlinked child pointing to external real git repo excluded")
        else:
            fail("symlink-exclusion", f"expected [], got {result}")


def test_find_repo_candidates_git_missing_logs_diagnostic_and_excludes_child() -> None:
    # A child where `git` itself is missing/broken (FileNotFoundError, OSError,
    # or subprocess.TimeoutExpired) must be excluded from candidates (same
    # outcome as "not a repo"), but the exception must NOT be silently
    # swallowed -- a stderr diagnostic must be emitted so a real environment
    # problem (git missing/timed out) is distinguishable from "git says no".
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good_repo = root / "good-repo"
        _init_repo(good_repo)
        broken_child = root / "broken-child"
        broken_child.mkdir()

        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if str(broken_child) in cmd:
                raise FileNotFoundError("git not found on PATH")
            return real_run(cmd, *args, **kwargs)

        stderr_capture = io.StringIO()
        with mock.patch("craftflow_resolve_workspace_root.subprocess.run", side_effect=fake_run):
            with contextlib.redirect_stderr(stderr_capture):
                result = find_repo_candidates(root)

        stderr_output = stderr_capture.getvalue()
        if result == [good_repo.resolve()] and "broken-child" in stderr_output:
            ok("git-missing child excluded from result + stderr diagnostic produced")
        else:
            fail(
                "git-missing-diagnostic",
                f"result={result}, stderr={stderr_output!r}",
            )


def test_match_request_text_unique_token_match() -> None:
    print("\n[match_request_text]")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = (root / "ai-platform-core").resolve()
        b = (root / "genai-platform-dev").resolve()
        a.mkdir()
        b.mkdir()
        result = match_request_text([a, b], "fix the bug in ai-platform-core's auth module")
        if result == a:
            ok("unique token match picks correct candidate")
        else:
            fail("unique-match", f"expected {a}, got {result}")


def test_match_request_text_no_match_returns_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = (root / "ai-platform-core").resolve()
        b = (root / "genai-platform-dev").resolve()
        a.mkdir()
        b.mkdir()
        result = match_request_text([a, b], "fix the login bug")
        if result is None:
            ok("no candidate named in request text -> None")
        else:
            fail("no-match", f"expected None, got {result}")


def test_match_request_text_multiple_names_returns_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = (root / "ai-platform-core").resolve()
        b = (root / "genai-platform-dev").resolve()
        a.mkdir()
        b.mkdir()
        result = match_request_text([a, b], "touches both ai-platform-core and genai-platform-dev")
        if result is None:
            ok("two candidates both named -> None (not falsely deterministic)")
        else:
            fail("multi-match", f"expected None, got {result}")


def test_resolve_no_repo_found() -> None:
    print("\n[resolve]")
    with tempfile.TemporaryDirectory() as tmp:
        result = resolve(Path(tmp), "anything")
        if result == {"outcome": "NO_REPO_FOUND"}:
            ok("zero candidates -> NO_REPO_FOUND")
        else:
            fail("no-repo-found", f"got {result}")


def test_resolve_deterministic_single_candidate_no_text_match() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "only-repo"
        _init_repo(repo)
        result = resolve(root, "request text that never mentions the repo name")
        expected = {"outcome": "DETERMINISTIC", "project_root": str(repo.resolve())}
        if result == expected:
            ok("single candidate is DETERMINISTIC even without a text match")
        else:
            fail("single-candidate-deterministic", f"expected {expected}, got {result}")


def test_resolve_deterministic_via_unique_text_match() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = root / "ai-platform-core"
        b = root / "genai-platform-dev"
        _init_repo(a)
        _init_repo(b)
        result = resolve(root, "fix the bug in ai-platform-core")
        expected = {"outcome": "DETERMINISTIC", "project_root": str(a.resolve())}
        if result == expected:
            ok("2 candidates, unique text match -> DETERMINISTIC (not falsely AMBIGUOUS)")
        else:
            fail("multi-candidate-deterministic", f"expected {expected}, got {result}")


def test_resolve_ambiguous_when_no_unique_match() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = root / "ai-platform-core"
        b = root / "genai-platform-dev"
        _init_repo(a)
        _init_repo(b)
        result = resolve(root, "just fix the login bug")
        if result.get("outcome") == "AMBIGUOUS" and sorted(result.get("candidates", [])) == sorted(
            [str(a.resolve()), str(b.resolve())]
        ):
            ok("2 candidates, no unique match -> AMBIGUOUS with full candidate list")
        else:
            fail("ambiguous", f"got {result}")


def test_resolve_workspace_root_cli() -> None:
    print("\n[CLI]")
    script = str(SCRIPTS / "craftflow_resolve_workspace_root.py")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "only-repo"
        _init_repo(repo)
        result = subprocess.run(
            [sys.executable, script, "--cwd", str(root), "--request", "anything"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            fail("cli-exit-code", f"expected exit 0, got {result.returncode}: {result.stderr}")
            return
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            fail("cli-json", f"stdout not valid JSON: {exc}: {result.stdout!r}")
            return
        if payload.get("outcome") == "DETERMINISTIC" and payload.get("project_root") == str(repo.resolve()):
            ok("CLI emits DETERMINISTIC JSON for single-repo workspace")
        else:
            fail("cli-outcome", f"got {payload}")


def test_resolve_workspace_root_cli_unreadable_cwd_exits_nonzero() -> None:
    script = str(SCRIPTS / "craftflow_resolve_workspace_root.py")
    result = subprocess.run(
        [sys.executable, script, "--cwd", "/definitely/does/not/exist/anywhere", "--request", "x"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and result.stdout.strip() == "":
        ok("CLI exits non-zero with no stdout JSON on unreadable --cwd")
    else:
        fail("cli-error-path", f"exit={result.returncode} stdout={result.stdout!r}")


def main() -> int:
    print("test_resolve_workspace_root: running")

    test_find_repo_candidates_zero_when_no_children()
    test_find_repo_candidates_zero_when_children_not_repos()
    test_find_repo_candidates_finds_nested_repos()
    test_find_repo_candidates_excludes_child_nested_in_ancestor_repo()
    test_find_repo_candidates_excludes_symlinked_child()
    test_find_repo_candidates_git_missing_logs_diagnostic_and_excludes_child()
    test_match_request_text_unique_token_match()
    test_match_request_text_no_match_returns_none()
    test_match_request_text_multiple_names_returns_none()
    test_resolve_no_repo_found()
    test_resolve_deterministic_single_candidate_no_text_match()
    test_resolve_deterministic_via_unique_text_match()
    test_resolve_ambiguous_when_no_unique_match()
    test_resolve_workspace_root_cli()
    test_resolve_workspace_root_cli_unreadable_cwd_exits_nonzero()

    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1

    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
