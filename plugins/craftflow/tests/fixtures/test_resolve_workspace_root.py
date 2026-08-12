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
import os
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_resolve_workspace_root import (  # noqa: E402
    find_repo_candidates,
    match_request_text,
    read_workspace_writable_paths,
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


def test_find_repo_candidates_path_resolve_runtimeerror_from_real_symlink_loop_excludes_child() -> None:
    # A child whose `git rev-parse --show-toplevel` succeeds, but where the
    # final `child.resolve()` call then raises RuntimeError, must be
    # excluded from candidates -- same outcome as "not a repo" -- but the
    # exception must NOT be silently swallowed. A stderr diagnostic must
    # name the child so a real environment problem (symlink cycle) is
    # distinguishable from "git says no".
    #
    # RuntimeError -- NOT OSError -- is the real exception pathlib.Path.resolve()
    # raises for a genuine on-disk symlink loop on CPython 3.9-3.12 (live-
    # confirmed: "Symlink loop from '<path>'"). A prior version of this test
    # mocked OSError(62, ...), which does not match reality and would have
    # let an unbroadened `except OSError` clause pass incorrectly. This
    # version constructs a REAL on-disk symlink loop (os.symlink(link, link))
    # and lets Python's own resolve() algorithm raise the real exception --
    # it is never fabricated by hand. `git -C <child>` and `child.samefile()`
    # are faked to reach the final `child.resolve()` call deterministically,
    # because a genuine loop directly on `child`'s own path would make the
    # `git -C child` subprocess call itself fail first (same kernel-level
    # path resolution), before ever reaching this line.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good_repo = root / "good-repo"
        _init_repo(good_repo)
        broken_child = root / "broken-child"
        broken_child.mkdir()

        loop_link = root / "real-symlink-loop"
        os.symlink(loop_link, loop_link)

        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if str(broken_child) in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=str(broken_child) + "\n", stderr="")
            return real_run(cmd, *args, **kwargs)

        real_samefile = Path.samefile

        def fake_samefile(self, other):
            if str(self) == str(broken_child):
                return True
            return real_samefile(self, other)

        real_resolve = Path.resolve

        def fake_resolve(self, *args, **kwargs):
            if str(self) == str(broken_child):
                return real_resolve(loop_link, *args, **kwargs)  # genuinely raises RuntimeError
            return real_resolve(self, *args, **kwargs)

        stderr_capture = io.StringIO()
        with mock.patch("craftflow_resolve_workspace_root.subprocess.run", side_effect=fake_run):
            with mock.patch.object(Path, "samefile", fake_samefile):
                with mock.patch.object(Path, "resolve", fake_resolve):
                    with contextlib.redirect_stderr(stderr_capture):
                        result = find_repo_candidates(root)

        stderr_output = stderr_capture.getvalue()
        if result == [good_repo.resolve()] and "broken-child" in stderr_output:
            ok("real on-disk symlink-loop RuntimeError from child.resolve() excluded + stderr diagnostic")
        else:
            fail(
                "path-resolve-runtimeerror-diagnostic",
                f"result={result}, stderr={stderr_output!r}",
            )


def test_find_repo_candidates_child_permission_error_excludes_child_not_whole_scan() -> None:
    # A sibling directory with restrictive permissions/ACLs can make its own
    # is_dir()/is_symlink() probe raise PermissionError (an OSError subclass)
    # -- errno 13 EACCES is NOT swallowed by pathlib internally the way
    # ENOENT/ENOTDIR/EBADF/ELOOP are. Prior to the fix this propagated through
    # the single try/except wrapping the whole `sorted(cwd.iterdir()...)`
    # generator and aborted the ENTIRE scan (re-raised as RuntimeError),
    # silently discarding an otherwise-valid neighboring repo. A real
    # ACL-based repro isn't portably constructible in a fast cross-platform
    # unit test, so Path.is_dir is mocked to raise for the specific denied
    # child -- matching this file's convention of mocking specific
    # OS-boundary calls (subprocess.run, Path.samefile, Path.resolve above).
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good_repo = root / "good-repo"
        _init_repo(good_repo)
        denied_child = root / "denied-child"
        denied_child.mkdir()

        real_is_dir = Path.is_dir

        def fake_is_dir(self):
            if str(self) == str(denied_child):
                raise PermissionError(13, "Permission denied")
            return real_is_dir(self)

        stderr_capture = io.StringIO()
        with mock.patch.object(Path, "is_dir", fake_is_dir):
            with contextlib.redirect_stderr(stderr_capture):
                result = find_repo_candidates(root)

        stderr_output = stderr_capture.getvalue()
        if result == [good_repo.resolve()] and "denied-child" in stderr_output:
            ok("permission-denied child excluded + stderr diagnostic + valid sibling still found")
        else:
            fail(
                "child-permission-error",
                f"result={result}, stderr={stderr_output!r}",
            )


def test_git_toplevel_case_mismatch_uses_filesystem_identity_not_string_equality() -> None:
    # On a case-insensitive/case-preserving filesystem (macOS/APFS), git's
    # reported toplevel reflects the TRUE on-disk case, while the caller's
    # --cwd-derived child path preserves whatever case the caller happened to
    # use -- these can differ in string form while still being the SAME real
    # directory. A string-equality identity check spuriously excludes this
    # real repo; filesystem-identity comparison (samefile(), inode+device via
    # os.stat) must not. A real cross-case directory can't be portably
    # constructed in a unit test (behavior is filesystem-dependent), so this
    # is simulated deterministically: subprocess.run is mocked so git
    # "reports" a different-case string for the same real directory, and
    # Path.samefile is mocked to model case-insensitive filesystem identity
    # for that pair (mirroring this file's convention of mocking
    # subprocess.run/Path.resolve for OS-boundary edge cases).
    print("\n[_git_toplevel case-mismatch identity]")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "MyRepo"
        _init_repo(repo)

        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if str(repo) in cmd:
                # git reports the TRUE on-disk case ("myrepo"), which differs
                # in string form from the caller-cased child path ("MyRepo")
                # even though both refer to the same real directory.
                lower_reported = str(repo).replace("MyRepo", "myrepo")
                return subprocess.CompletedProcess(cmd, 0, stdout=lower_reported + "\n", stderr="")
            return real_run(cmd, *args, **kwargs)

        real_samefile = Path.samefile

        def fake_samefile(self, other):
            other_path = other if isinstance(other, Path) else Path(other)
            if str(self).lower() == str(other_path).lower():
                return True
            return real_samefile(self, other)

        with mock.patch("craftflow_resolve_workspace_root.subprocess.run", side_effect=fake_run):
            with mock.patch.object(Path, "samefile", fake_samefile):
                result = find_repo_candidates(root)

        if result == [repo.resolve()]:
            ok("case-mismatched git-reported path still resolves to the real repo via samefile identity")
        else:
            fail("case-mismatch-identity", f"expected [{repo.resolve()}], got {result}")


def _write_workspace_config(root: Path, writable_paths) -> None:
    (root / ".craftflow-workspace.json").write_text(
        json.dumps({"writable_paths": writable_paths}), encoding="utf-8"
    )


def test_read_workspace_writable_paths_returns_empty_when_no_config_file() -> None:
    print("\n[read_workspace_writable_paths]")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        validated, dropped = read_workspace_writable_paths(root, [])
        if validated == [] and dropped == []:
            ok("no config file -> ([], [])")
        else:
            fail("no-config", f"expected ([], []), got ({validated}, {dropped})")


def test_read_workspace_writable_paths_valid_direct_child_entry_accepted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_workspace_config(root, ["CONTRACTS.md"])
        validated, dropped = read_workspace_writable_paths(root, [])
        expected = [(root / "CONTRACTS.md").resolve()]
        if validated == expected and dropped == []:
            ok("valid direct-child entry accepted")
        else:
            fail("valid-entry", f"expected ({expected}, []), got ({validated}, {dropped})")


def test_read_workspace_writable_paths_rejects_entry_with_path_separator() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_workspace_config(root, ["sub/CONTRACTS.md"])
        validated, dropped = read_workspace_writable_paths(root, [])
        if validated == [] and len(dropped) == 1:
            ok("entry with path separator rejected")
        else:
            fail("path-separator", f"got ({validated}, {dropped})")


def test_read_workspace_writable_paths_rejects_dotdot_entry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_workspace_config(root, [".."])
        validated, dropped = read_workspace_writable_paths(root, [])
        if validated == [] and len(dropped) == 1:
            ok("'..' entry rejected")
        else:
            fail("dotdot", f"got ({validated}, {dropped})")


def test_read_workspace_writable_paths_rejects_absolute_path_entry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_workspace_config(root, ["/etc/passwd"])
        validated, dropped = read_workspace_writable_paths(root, [])
        if validated == [] and len(dropped) == 1:
            ok("absolute-path entry rejected")
        else:
            fail("absolute", f"got ({validated}, {dropped})")


def test_read_workspace_writable_paths_rejects_entry_resolving_inside_nested_repo() -> None:
    # A symlink at the workspace root pointing INSIDE a nested repo: syntactically a bare
    # filename (passes the direct-child check), but its resolved real path lands inside a
    # candidate -- must still be dropped (the load-bearing check per the design).
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        nested_repo = root / "ai-platform-core"
        nested_repo.mkdir()
        target_inside = nested_repo / "secret.md"
        target_inside.write_text("x", encoding="utf-8")
        symlink_path = root / "sneaky-link"
        symlink_path.symlink_to(target_inside)
        _write_workspace_config(root, ["sneaky-link"])
        validated, dropped = read_workspace_writable_paths(root, [nested_repo.resolve()])
        if validated == [] and len(dropped) == 1:
            ok("symlink resolving inside a nested repo rejected")
        else:
            fail("symlink-into-nested-repo", f"got ({validated}, {dropped})")


def test_read_workspace_writable_paths_rejects_entry_naming_a_nested_repo_directory_itself() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        nested_repo = root / "ai-platform-core"
        nested_repo.mkdir()
        _write_workspace_config(root, ["ai-platform-core"])
        validated, dropped = read_workspace_writable_paths(root, [nested_repo.resolve()])
        if validated == [] and len(dropped) == 1:
            ok("entry naming a nested repo directory itself rejected")
        else:
            fail("names-nested-repo", f"got ({validated}, {dropped})")


def test_read_workspace_writable_paths_rejects_entry_matching_nested_repo_by_case_only() -> None:
    # macOS APFS default: case-insensitive but case-preserving. An entry that
    # differs from a real nested-repo directory's name only in CASE resolves
    # to the SAME on-disk inode as that nested repo (samefile() == True) even
    # though `==`/`.parents` string comparison says False. Must be REJECTED,
    # not validated -- string-equality-based "inside_nested_repo" checks were
    # the load-bearing bug (see _git_toplevel()'s own docstring for the same
    # invariant, already handled correctly there via samefile()).
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        nested_repo = root / "NestedRepo"
        nested_repo.mkdir()
        _write_workspace_config(root, ["nestedrepo"])
        validated, dropped = read_workspace_writable_paths(root, [nested_repo.resolve()])
        if validated == [] and len(dropped) == 1 and dropped[0].get("reason") == "resolves_inside_nested_repo":
            ok("entry differing from nested repo dir only in case rejected (samefile identity)")
        else:
            fail("case-only-nested-repo-match", f"got ({validated}, {dropped})")


def test_read_workspace_writable_paths_rejects_entry_matching_nested_repo_by_unicode_normalization_only() -> None:
    # On default macOS APFS, a normalization-insensitive filesystem, an entry
    # that is the NFD-decomposed Unicode form of a real nested-repo
    # directory's NFC-composed name resolves to the SAME on-disk inode
    # (samefile() == True), even though `==`/`.parents` string comparison
    # (raw code-point sequences) says False. Must be REJECTED, not validated.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        nfc_name = unicodedata.normalize("NFC", "CaféRepo")  # precomposed e-acute
        nfd_name = unicodedata.normalize("NFD", nfc_name)  # decomposed e + combining acute
        if nfc_name == nfd_name:
            # Nothing to prove on a platform where NFC/NFD collapse to the same code
            # points for this string -- the case-only test above already covers the
            # underlying samefile()-based mechanism.
            ok("unicode-normalization-only-nested-repo-match (skipped: NFC == NFD here)")
            return
        nested_repo = root / nfc_name
        nested_repo.mkdir()
        _write_workspace_config(root, [nfd_name])
        validated, dropped = read_workspace_writable_paths(root, [nested_repo.resolve()])
        if validated == [] and len(dropped) == 1 and dropped[0].get("reason") == "resolves_inside_nested_repo":
            ok("entry differing from nested repo dir only in Unicode normalization form rejected (samefile identity)")
        else:
            fail("unicode-normalization-nested-repo-match", f"got ({validated}, {dropped})")


def test_read_workspace_writable_paths_embedded_null_byte_entry_dropped_individually_not_crash() -> None:
    # A JSON string entry containing an embedded NUL byte makes
    # (workspace_root / entry).resolve() raise ValueError: embedded null byte
    # (NOT OSError/RuntimeError). Must be caught and the entry dropped
    # individually with a resolve_failed:ValueError diagnostic -- never crash
    # the whole function (violates the function's own documented contract:
    # "Never raises... dropped on its own... never discarding the whole
    # file"). A valid sibling entry must still be accepted.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_workspace_config(root, ["CONTRACTS.md", "evil\x00name"])
        validated, dropped = read_workspace_writable_paths(root, [])
        expected = [(root / "CONTRACTS.md").resolve()]
        if (
            validated == expected
            and len(dropped) == 1
            and dropped[0].get("reason") == "resolve_failed:ValueError"
        ):
            ok("embedded null byte entry dropped individually with resolve_failed:ValueError diagnostic")
        else:
            fail("embedded-null-byte", f"got ({validated}, {dropped})")


def test_read_workspace_writable_paths_malformed_json_degrades_to_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".craftflow-workspace.json").write_text("{not valid json", encoding="utf-8")
        validated, dropped = read_workspace_writable_paths(root, [])
        if validated == [] and len(dropped) == 1:
            ok("malformed JSON degrades to empty, logged, no crash")
        else:
            fail("malformed-json", f"got ({validated}, {dropped})")


def test_read_workspace_writable_paths_missing_writable_paths_key_degrades_to_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".craftflow-workspace.json").write_text("{}", encoding="utf-8")
        validated, dropped = read_workspace_writable_paths(root, [])
        if validated == [] and len(dropped) == 1:
            ok("missing writable_paths key degrades to empty")
        else:
            fail("missing-key", f"got ({validated}, {dropped})")


def test_read_workspace_writable_paths_non_list_writable_paths_degrades_to_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".craftflow-workspace.json").write_text(
            json.dumps({"writable_paths": "CONTRACTS.md"}), encoding="utf-8"
        )
        validated, dropped = read_workspace_writable_paths(root, [])
        if validated == [] and len(dropped) == 1:
            ok("non-list writable_paths degrades to empty")
        else:
            fail("non-list", f"got ({validated}, {dropped})")


def test_read_workspace_writable_paths_non_string_entry_dropped_individually() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_workspace_config(root, ["CONTRACTS.md", 42])
        validated, dropped = read_workspace_writable_paths(root, [])
        expected = [(root / "CONTRACTS.md").resolve()]
        if validated == expected and len(dropped) == 1:
            ok("non-string entry dropped individually, valid siblings still accepted")
        else:
            fail("non-string-entry", f"got ({validated}, {dropped})")


def test_resolve_includes_workspace_writable_paths_when_config_present() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "only-repo"
        _init_repo(repo)
        _write_workspace_config(root, ["CONTRACTS.md"])
        result = resolve(root, "anything")
        if result.get("workspace_writable_paths") == [str((root / "CONTRACTS.md").resolve())]:
            ok("resolve() includes workspace_writable_paths when config present")
        else:
            fail("resolve-includes", f"got {result}")


def test_resolve_omits_workspace_writable_paths_key_when_no_config() -> None:
    # Regression: zero behavior change / zero new keys when no .craftflow-workspace.json exists
    # -- preserves this file's pre-existing exact-dict-equality tests untouched.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / "only-repo"
        _init_repo(repo)
        result = resolve(root, "anything")
        if "workspace_writable_paths" not in result and "workspace_writable_paths_dropped" not in result:
            ok("resolve() omits workspace_writable_paths keys entirely when no config exists")
        else:
            fail("resolve-omits", f"got {result}")


def test_resolve_no_repo_found_never_reads_config() -> None:
    # Scope boundary per design: NO_REPO_FOUND never reads .craftflow-workspace.json at all,
    # even if one exists in that directory.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_workspace_config(root, ["CONTRACTS.md"])
        result = resolve(root, "anything")
        if result == {"outcome": "NO_REPO_FOUND"}:
            ok("NO_REPO_FOUND stays byte-identical even with a config file present")
        else:
            fail("no-repo-found-scope", f"expected exactly {{'outcome': 'NO_REPO_FOUND'}}, got {result}")


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


def test_resolve_workspace_root_cli_symlink_loop_cwd_exits_cleanly_not_traceback() -> None:
    # A REAL on-disk symlink loop passed as --cwd makes
    # `Path(args.cwd).resolve()` raise RuntimeError ("Symlink loop from
    # ...", real CPython 3.9-3.12 behavior, live-confirmed). Prior to the
    # fix, that call sat OUTSIDE main()'s `try/except` block wrapping
    # `resolve(cwd, args.request)`, so a raw Python traceback went to
    # stderr instead of the documented clean
    # "craftflow_resolve_workspace_root: {exc}" diagnostic + exit 1 that
    # every other failure mode (e.g. unreadable --cwd, above) already uses.
    with tempfile.TemporaryDirectory() as tmp:
        loop_link = Path(tmp) / "self"
        os.symlink(loop_link, loop_link)
        script = str(SCRIPTS / "craftflow_resolve_workspace_root.py")
        result = subprocess.run(
            [sys.executable, script, "--cwd", str(loop_link), "--request", "x"],
            capture_output=True,
            text=True,
        )
        clean_diagnostic = result.stderr.strip().startswith("craftflow_resolve_workspace_root:")
        no_traceback = "Traceback (most recent call last)" not in result.stderr
        if (
            result.returncode != 0
            and result.stdout.strip() == ""
            and clean_diagnostic
            and no_traceback
        ):
            ok("CLI exits cleanly (exit 1, clean diagnostic, no traceback) for symlink-loop --cwd")
        else:
            fail(
                "cli-symlink-loop-cwd",
                f"exit={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}",
            )


def main() -> int:
    print("test_resolve_workspace_root: running")

    test_find_repo_candidates_zero_when_no_children()
    test_find_repo_candidates_zero_when_children_not_repos()
    test_find_repo_candidates_finds_nested_repos()
    test_find_repo_candidates_excludes_child_nested_in_ancestor_repo()
    test_find_repo_candidates_excludes_symlinked_child()
    test_find_repo_candidates_git_missing_logs_diagnostic_and_excludes_child()
    test_find_repo_candidates_path_resolve_runtimeerror_from_real_symlink_loop_excludes_child()
    test_find_repo_candidates_child_permission_error_excludes_child_not_whole_scan()
    test_git_toplevel_case_mismatch_uses_filesystem_identity_not_string_equality()
    test_read_workspace_writable_paths_returns_empty_when_no_config_file()
    test_read_workspace_writable_paths_valid_direct_child_entry_accepted()
    test_read_workspace_writable_paths_rejects_entry_with_path_separator()
    test_read_workspace_writable_paths_rejects_dotdot_entry()
    test_read_workspace_writable_paths_rejects_absolute_path_entry()
    test_read_workspace_writable_paths_rejects_entry_resolving_inside_nested_repo()
    test_read_workspace_writable_paths_rejects_entry_naming_a_nested_repo_directory_itself()
    test_read_workspace_writable_paths_rejects_entry_matching_nested_repo_by_case_only()
    test_read_workspace_writable_paths_rejects_entry_matching_nested_repo_by_unicode_normalization_only()
    test_read_workspace_writable_paths_embedded_null_byte_entry_dropped_individually_not_crash()
    test_read_workspace_writable_paths_malformed_json_degrades_to_empty()
    test_read_workspace_writable_paths_missing_writable_paths_key_degrades_to_empty()
    test_read_workspace_writable_paths_non_list_writable_paths_degrades_to_empty()
    test_read_workspace_writable_paths_non_string_entry_dropped_individually()
    test_resolve_includes_workspace_writable_paths_when_config_present()
    test_resolve_omits_workspace_writable_paths_key_when_no_config()
    test_resolve_no_repo_found_never_reads_config()
    test_match_request_text_unique_token_match()
    test_match_request_text_no_match_returns_none()
    test_match_request_text_multiple_names_returns_none()
    test_resolve_no_repo_found()
    test_resolve_deterministic_single_candidate_no_text_match()
    test_resolve_deterministic_via_unique_text_match()
    test_resolve_ambiguous_when_no_unique_match()
    test_resolve_workspace_root_cli()
    test_resolve_workspace_root_cli_unreadable_cwd_exits_nonzero()
    test_resolve_workspace_root_cli_symlink_loop_cwd_exits_cleanly_not_traceback()

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
