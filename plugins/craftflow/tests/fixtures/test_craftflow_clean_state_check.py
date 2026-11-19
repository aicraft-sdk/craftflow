#!/usr/bin/env python3
"""Tests for craftflow_clean_state_check.py.

Run: python3 tests/fixtures/test_craftflow_clean_state_check.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_clean_state_check.py"

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)


def run_cli(project_root: Path, extra: "list | None" = None, env: "dict | None" = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(project_root), "--format", "json", *(extra or [])],
        capture_output=True,
        text=True,
        env=env,
    )


def test_console_log_detected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text("function f() {\n  console.log('debug');\n}\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "console.log"]
        if len(hits) == 1 and hits[0]["line"] == 2:
            ok("console_log_detected")
        else:
            fail("console_log_detected", f"got {data}")


def test_debugger_detected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text("function f() {\n  debugger;\n}\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "debugger"]
        if len(hits) == 1 and hits[0]["line"] == 2:
            ok("debugger_detected")
        else:
            fail("debugger_detected", f"got {data}")


def test_debugger_without_semicolon_detected() -> None:
    # Regression: _DEBUGGER_RE required a trailing semicolon, missing valid
    # ASI forms like `if (x) debugger` (no semicolon) -- a real false negative.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text("function f(x) {\n  if (x) debugger\n}\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "debugger"]
        if len(hits) == 1 and hits[0]["line"] == 2:
            ok("debugger_without_semicolon_detected")
        else:
            fail("debugger_without_semicolon_detected", f"got {data}")


def test_clean_diff_reports_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text("function f() {\n  return 1;\n}\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        if data["findings"] == []:
            ok("clean_diff_reports_nothing")
        else:
            fail("clean_diff_reports_nothing", f"expected no findings, got {data}")


def test_unreadable_untracked_file_is_surfaced_not_swallowed() -> None:
    # chmod(0o000) is a no-op for root (common in containerized CI) -- root
    # can still read the file regardless of permission bits, so this test
    # would spuriously behave differently under a root-run CI. Skip rather
    # than produce a false pass/fail under that environment.
    if hasattr(os, "getuid") and os.getuid() == 0:
        print("  SKIP: unreadable_untracked_file_is_surfaced_not_swallowed (running as root; chmod 0o000 is a no-op)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        unreadable = root / "secret.ts"
        unreadable.write_text("console.log('unreachable');\n")
        unreadable.chmod(0o000)
        try:
            result = run_cli(root)
            data = json.loads(result.stdout)
            skipped = data.get("skipped", [])
            hits = [s for s in skipped if s.get("file") == "secret.ts"]
            if len(hits) == 1 and "error" in hits[0]:
                ok("unreadable_untracked_file_is_surfaced_not_swallowed")
            else:
                fail("unreadable_untracked_file_is_surfaced_not_swallowed", f"got {data}")
        finally:
            unreadable.chmod(0o644)


def test_new_untracked_directory_is_not_collapsed_and_hidden() -> None:
    # Regression: `git status --porcelain` (no --untracked-files=all) collapses
    # a brand-new untracked DIRECTORY to a single `?? newdir/` line. The old
    # `full.is_file()` check was False for that line, so the loop just
    # `continue`d -- a console.log inside a brand-new module directory was
    # completely invisible: not in findings, not even in skipped.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        newdir = root / "newmodule"
        newdir.mkdir()
        (newdir / "index.ts").write_text("function f() {\n  console.log('hi');\n}\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "console.log"]
        if len(hits) == 1 and hits[0]["file"] == "newmodule/index.ts" and hits[0]["line"] == 2:
            ok("new_untracked_directory_is_not_collapsed_and_hidden")
        else:
            fail("new_untracked_directory_is_not_collapsed_and_hidden", f"got {data}")


def test_nested_git_repo_directory_is_surfaced_in_skipped() -> None:
    # Regression: git structurally never descends into an embedded .git
    # regardless of --untracked-files=all -- an untracked directory that
    # itself contains a .git (e.g. an accidentally-vendored dependency)
    # still collapses to one `?? dir/` line. full.is_file() is False for
    # it, so it was silently dropped: not in findings, not in skipped.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        nested = root / "vendored-dep"
        nested.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=nested, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=nested, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=nested, check=True)
        (nested / "console.txt").write_text("console.log('inside nested repo');\n")
        subprocess.run(["git", "add", "."], cwd=nested, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "nested base"], cwd=nested, check=True)
        result = run_cli(root)
        data = json.loads(result.stdout)
        findings_hits = [f for f in data["findings"] if "vendored-dep" in f.get("file", "")]
        skipped_hits = [s for s in data.get("skipped", []) if "vendored-dep" in s.get("file", "")]
        if skipped_hits and not findings_hits:
            ok("nested_git_repo_directory_is_surfaced_in_skipped")
        else:
            fail(
                "nested_git_repo_directory_is_surfaced_in_skipped",
                f"expected vendored-dep/ in skipped (never silently absent from both), got findings={findings_hits} skipped={skipped_hits}",
            )


def test_broken_symlink_is_surfaced_in_skipped() -> None:
    # Regression: an untracked path that is neither is_file() nor is_dir()
    # (a dangling/broken symlink, a symlink loop, a socket, a FIFO, a device
    # file) fell through the if/if-dir chain with no `else` -- `continue`
    # silently dropped it from BOTH findings and skipped. A scan gap must
    # never be indistinguishable from a genuinely clean result.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        os.symlink("./does-not-exist.ts", root / "broken-link.ts")
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [s for s in data.get("skipped", []) if s.get("file") == "broken-link.ts"]
        if len(hits) == 1 and "error" in hits[0] and hits[0]["error"]:
            ok("broken_symlink_is_surfaced_in_skipped")
        else:
            fail("broken_symlink_is_surfaced_in_skipped", f"got {data}")


def test_missing_git_binary_produces_clean_error_not_traceback() -> None:
    # Regression: _run_git()'s subprocess.run(["git", ...]) raises a raw
    # FileNotFoundError when the git binary is missing from PATH, which
    # propagated uncaught out of main() instead of becoming a clean
    # GitError/JSON-error exit-1 -- contradicting the module docstring's
    # documented contract ("Exit 1 only on a genuine git error ... git
    # binary missing"). Strip PATH so the child process's own `git`
    # subprocess.run() lookup fails with FileNotFoundError.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        env = dict(os.environ)
        env["PATH"] = ""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--project-root", str(root), "--format", "json"],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 1:
            fail(
                "missing_git_binary_produces_clean_error_not_traceback",
                f"expected exit 1, got exit={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}",
            )
            return
        try:
            payload = json.loads(result.stderr)
        except json.JSONDecodeError:
            fail(
                "missing_git_binary_produces_clean_error_not_traceback",
                f"expected clean JSON error on stderr, got uncaught traceback: {result.stderr!r}",
            )
            return
        if "error" in payload and "git binary not found" in payload["error"]:
            ok("missing_git_binary_produces_clean_error_not_traceback")
        else:
            fail(
                "missing_git_binary_produces_clean_error_not_traceback",
                f"expected error mentioning 'git binary not found', got {payload}",
            )


def test_todo_without_ticket_detected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text("// TODO fix this later\nfunction f() { return 1; }\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "todo-without-ticket"]
        if len(hits) == 1:
            ok("todo_without_ticket_detected")
        else:
            fail("todo_without_ticket_detected", f"got {data}")


def test_todo_with_ticket_is_not_flagged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text("// TODO(#123): revisit after v2\nfunction f() { return 1; }\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "todo-without-ticket"]
        if hits == []:
            ok("todo_with_ticket_is_not_flagged")
        else:
            fail("todo_with_ticket_is_not_flagged", f"expected no todo findings, got {hits}")


def test_todo_with_unrelated_hash_number_is_still_flagged() -> None:
    # Regression: _TICKET_RE was checked against the WHOLE line and matched
    # ANY bare hash-number anywhere in the comment, not just an actual ticket
    # reference near TODO -- e.g. a line number mentioned elsewhere in the
    # same comment was wrongly treated as "has a ticket".
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text(
            "// TODO cleanup, ref line #42 not a real ticket\nfunction f() { return 1; }\n"
        )
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "todo-without-ticket"]
        if len(hits) == 1:
            ok("todo_with_unrelated_hash_number_is_still_flagged")
        else:
            fail("todo_with_unrelated_hash_number_is_still_flagged", f"got {data}")


def test_todo_with_colon_hash_ticket_is_not_flagged() -> None:
    # Regression: _TODO_TICKET_RE has no tolerance for a colon between TODO
    # and the ticket ref, so the common "TODO: #123" style was wrongly
    # treated as ticketless.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text("// TODO: #123 fix this\nfunction f() { return 1; }\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "todo-without-ticket"]
        if hits == []:
            ok("todo_with_colon_hash_ticket_is_not_flagged")
        else:
            fail("todo_with_colon_hash_ticket_is_not_flagged", f"expected no todo findings, got {hits}")


def test_todo_with_colon_jira_ticket_is_not_flagged() -> None:
    # Regression: same colon-tolerance gap for the JIRA-style ticket anchor.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text("// TODO: JIRA-456 revisit\nfunction f() { return 1; }\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "todo-without-ticket"]
        if hits == []:
            ok("todo_with_colon_jira_ticket_is_not_flagged")
        else:
            fail("todo_with_colon_jira_ticket_is_not_flagged", f"expected no todo findings, got {hits}")


def test_commented_code_block_detected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text(
            "// const x = 1;\n// doSomething(x);\n// return x + 1;\nfunction f() { return 1; }\n"
        )
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "commented-code-block"]
        if len(hits) == 1 and hits[0]["line"] == 1:
            ok("commented_code_block_detected")
        else:
            fail("commented_code_block_detected", f"got {data}")


def test_block_comment_code_detected() -> None:
    # Regression: _COMMENT_LINE_RE only recognized `//`/`#` line comments --
    # block comments (/* ... */) were completely invisible to the
    # commented-code-block detector, even though that's the most common way
    # to comment out a chunk of code.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text(
            "/*\nconst x = 1;\ndoSomething(x);\nreturn x + 1;\n*/\nfunction f() { return 1; }\n"
        )
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "commented-code-block"]
        if len(hits) == 1 and hits[0]["line"] == 1:
            ok("block_comment_code_detected")
        else:
            fail("block_comment_code_detected", f"got {data}")


def test_unclosed_block_comment_does_not_bleed_into_next_file() -> None:
    # Regression: _scan_block_comments() iterates the flattened cross-file
    # `lines` list with no reset of in_block/interior/block_start at file
    # boundaries. An unclosed /* in first.ts (only 1 interior line -- below
    # the 3-line threshold on its own) must not "borrow" second.ts's next 2
    # real code lines to cross the threshold and produce a finding that
    # misattributes second.ts's real code to first.ts. Neither file alone
    # has a genuine 3+-line commented block, so the correct result is zero
    # commented-code-block findings; a bleed produces exactly one, wrongly
    # attributed to first.ts:1.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "first.ts").write_text("/* unclosed comment starts here\nnever closes in this file\n")
        (root / "second.ts").write_text(
            "function real() {\n  doSomething();\n  doSomethingElse(); /* end note */\n  return 1;\n}\n"
        )
        result = run_cli(root)
        data = json.loads(result.stdout)
        block_hits = [f for f in data["findings"] if f["pattern"] == "commented-code-block"]
        if block_hits == []:
            ok("unclosed_block_comment_does_not_bleed_into_next_file")
        else:
            fail("unclosed_block_comment_does_not_bleed_into_next_file", f"cross-file bleed produced a misattributed finding: {block_hits}")


def test_unterminated_block_comment_at_end_of_scan_is_not_dropped() -> None:
    # Regression: flush() was only ever called from inside the block-close
    # branch. A block comment that opens with /* and never closes anywhere
    # in the scanned diff had its interior lines silently discarded -- the
    # exact "invisible commented-out code" symptom this pass exists to
    # catch. A finding (or an explicit skipped entry) must be produced, not
    # silence.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text(
            "/*\nconst x = 1;\ndoSomething(x);\nreturn x + 1;\nlogSomethingElse(x);\n"
        )
        result = run_cli(root)
        data = json.loads(result.stdout)
        block_hits = [f for f in data["findings"] if f["pattern"] == "commented-code-block"]
        skipped_hits = [s for s in data.get("skipped", []) if s.get("file") == "app.ts"]
        if len(block_hits) == 1 and block_hits[0]["line"] == 1:
            ok("unterminated_block_comment_at_end_of_scan_is_not_dropped")
        elif len(skipped_hits) == 1:
            ok("unterminated_block_comment_at_end_of_scan_is_not_dropped")
        else:
            fail("unterminated_block_comment_at_end_of_scan_is_not_dropped", f"unterminated block silently dropped: {data}")


def test_unclosed_block_in_first_of_three_files_is_still_flushed() -> None:
    # Regression: the file-boundary reset in _scan_block_comments() cleared
    # in_block/interior/block_start WITHOUT calling flush() first. The
    # end-of-scan `if in_block: flush()` only rescues an unclosed block that
    # is still open when the ENTIRE scan ends -- i.e. only when the unclosed
    # block sits in the LAST file. An unclosed block with a genuine 3+-line
    # interior sitting in a NON-last file (here, the first of three) was
    # silently dropped: not in findings, not in skipped. This is the exact
    # scenario existing tests never covered (they only exercise single-file
    # or last-file unclosed blocks).
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "aaa_first.ts").write_text(
            "/* unclosed\nline one\nline two\nline three\n"
        )
        (root / "bbb_middle.ts").write_text("function mid() { return 0; }\n")
        (root / "ccc_last.ts").write_text("function last() { return 0; }\n")
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "commented-code-block" and f["file"] == "aaa_first.ts"]
        if len(hits) == 1 and hits[0]["line"] == 1:
            ok("unclosed_block_in_first_of_three_files_is_still_flushed")
        else:
            fail("unclosed_block_in_first_of_three_files_is_still_flushed", f"unclosed block in non-last file silently dropped: {data}")


def test_line_comment_run_does_not_bleed_across_file_boundary() -> None:
    # Regression: the sibling // and # line-comment `comment_run` tracker in
    # scan() had NO file-boundary reset at all (unlike the block-comment
    # path). It is shared across the whole flattened cross-file `lines`
    # sequence and only flushed on a non-comment-like line, never on a file
    # transition. fileA ends with 2 code-like `//` lines and fileB begins
    # with 2 more -- neither alone crosses the 3-line threshold, but combined
    # they wrongly do, producing one false finding misattributed to fileA.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "aaa_file.ts").write_text(
            "function f() { return 1; }\n// const x = 1;\n// doSomething(x);\n"
        )
        (root / "bbb_file.ts").write_text(
            "// return x + 1;\n// console.log(x);\nfunction g() { return 2; }\n"
        )
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "commented-code-block"]
        if hits == []:
            ok("line_comment_run_does_not_bleed_across_file_boundary")
        else:
            fail("line_comment_run_does_not_bleed_across_file_boundary", f"cross-file bleed produced a misattributed finding: {hits}")


def test_todo_ticket_regex_does_not_match_mid_identifier() -> None:
    # Regression: _TODO_TICKET_RE had no leading \b boundary, unlike
    # _TODO_RE's \bTODO\b -- it could match mid-identifier. On a line like
    # "MEGATODO(#123) call site, TODO fix this bare one has no ticket",
    # _TODO_TICKET_RE.search() wrongly matched the embedded "TODO(#123"
    # substring inside "MEGATODO", causing the line's real, separate, bare
    # TODO to be wrongly suppressed.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text(
            "// MEGATODO(#123) call site, TODO fix this bare one has no ticket\nfunction f() { return 1; }\n"
        )
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "todo-without-ticket"]
        if len(hits) == 1:
            ok("todo_ticket_regex_does_not_match_mid_identifier")
        else:
            fail("todo_ticket_regex_does_not_match_mid_identifier", f"got {data}")


def test_eslint_disable_comments_are_not_false_positives() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.ts").write_text(
            "// eslint-disable-next-line no-console\n"
            "// eslint-disable no-unused-vars\n"
            "// @ts-ignore\n"
            "function f() { return 1; }\n"
        )
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "commented-code-block"]
        if hits == []:
            ok("eslint_disable_comments_are_not_false_positives")
        else:
            fail("eslint_disable_comments_are_not_false_positives", f"expected no findings, got {hits}")


def test_python_suppression_directives_are_not_false_positives() -> None:
    # Regression: _DISABLE_COMMENT_RE only recognized eslint/@ts-/prettier
    # idioms -- Python suppression comments (noqa, type-ignore and
    # pylint-disable directives) are common in this Python-heavy plugin repo and
    # would false-positive as a commented-code-block once they contain a
    # code-like token (e.g. `disable=`).
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "app.py").write_text(
            "# pylint: disable=invalid-name\n"
            "# pylint: disable=missing-docstring\n"
            "# pylint: disable=too-many-arguments\n"
            "x = 1\n"
        )
        result = run_cli(root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "commented-code-block"]
        if hits == []:
            ok("python_suppression_directives_are_not_false_positives")
        else:
            fail("python_suppression_directives_are_not_false_positives", f"expected no findings, got {hits}")


# ---------------------------------------------------------------------------
# Unexplained-suppression class (newly added Python noqa / type: ignore)
# ---------------------------------------------------------------------------
# Every expected-bad / expected-good source line below lives inside a Python
# string literal in THIS file. That is deliberate: the detector judges real
# comment tokens only, so these fixtures are never flagged as part of this
# repo's own diff (see test_detector_ignores_its_own_fixture_strings).

_SUPPRESSION_BAD = [
    ("bare_noqa", "import os  # noqa\n"),
    ("bare_noqa_uppercase", "import os  # NOQA\n"),
    ("noqa_empty_codes", "import os  # noqa:\n"),
    ("noqa_code_without_reason", "import os  # noqa: E402\n"),
    ("noqa_multi_code_without_reason", "import os  # noqa: E402,F401\n"),
    ("noqa_dash_with_empty_reason", "import os  # noqa: E402 -\n"),
    ("noqa_reason_too_short", "import os  # noqa: E402 - ok\n"),
    ("noqa_reason_without_delimiter", "import os  # noqa: E402 because reasons\n"),
    ("flake8_file_level_blanket", "# flake8: noqa\n"),
    ("bare_type_ignore", "x = 1  # type: ignore\n"),
    ("type_ignore_without_code", "x = 1  # type: ignore - checker is wrong here\n"),
    ("type_ignore_code_without_reason", "x = 1  # type: ignore[attr-defined]\n"),
]

_SUPPRESSION_GOOD = [
    ("noqa_dash_reason", "import os  # noqa: E402 - late import after path fixup\n"),
    ("noqa_double_dash_reason", "import os  # noqa: BLE001 -- fail-open by contract\n"),
    ("noqa_em_dash_reason", "import os  # noqa: E402 \u2014 late import after path fixup\n"),
    ("noqa_paren_reason", "import os  # noqa: E402  (shared fixtures; importing it runs nothing)\n"),
    ("noqa_hash_reason", "import os  # noqa: E402  # late import after path fixup\n"),
    ("noqa_multi_code_reason", "import os  # noqa: E402,F401 - re-exported after path fixup\n"),
    ("noqa_space_separated_codes", "import os  # noqa: E402 F401 - re-exported after path fixup\n"),
    ("flake8_file_level_with_code_and_reason", "# flake8: noqa: E501 - generated table, wrapping would corrupt it\n"),
    ("type_ignore_code_dash_reason", "x = 1  # type: ignore[attr-defined] - stub lacks the attribute\n"),
    ("type_ignore_multi_code_reason", "x = 1  # type: ignore[attr-defined, misc] - stub lacks the attribute\n"),
    ("type_ignore_hash_reason", "x = 1  # type: ignore[no-redef]  # fallback shim for missing module\n"),
    ("type_ignore_paren_reason", "x = 1  # type: ignore[arg-type] (deliberately passes a bad type)\n"),
    ("no_suppression_at_all", "x = 1  # plain comment\n"),
]


def _suppression_findings(source: str, extra: "list | None" = None, filename: str = "mod.py") -> tuple:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / filename).write_text(source)
        result = run_cli(root, extra)
        data = json.loads(result.stdout) if result.stdout else {}
        hits = [f for f in data.get("findings", []) if f.get("pattern") == "unexplained-suppression"]
        return result, data, hits


def test_unexplained_suppressions_rejected_in_block_mode() -> None:
    for label, source in _SUPPRESSION_BAD:
        result, _data, hits = _suppression_findings(source, ["--suppression-mode", "block"])
        msg = hits[0]["message"] if hits else ""
        if (
            result.returncode == 2
            and len(hits) == 1
            and hits[0]["severity"] == "blocking"
            and hits[0]["line"] == 1
            and "rule code" in msg
            and "written reason" in msg
            and "report it to the user" in msg
        ):
            ok(f"suppression_rejected_block[{label}]")
        else:
            fail(f"suppression_rejected_block[{label}]", f"exit={result.returncode} hits={hits} err={result.stderr!r}")


def test_explained_suppressions_accepted_in_block_mode() -> None:
    for label, source in _SUPPRESSION_GOOD:
        result, _data, hits = _suppression_findings(source, ["--suppression-mode", "block"])
        if result.returncode == 0 and hits == []:
            ok(f"suppression_accepted_block[{label}]")
        else:
            fail(f"suppression_accepted_block[{label}]", f"exit={result.returncode} hits={hits}")


def test_audit_mode_reports_but_never_blocks() -> None:
    result, data, hits = _suppression_findings("import os  # noqa\n", ["--suppression-mode", "audit"])
    if (
        result.returncode == 0
        and len(hits) == 1
        and hits[0]["severity"] == "advisory"
        and data.get("suppression_mode") == "audit"
        and data.get("summary") == {"total": 1, "blocking": 0, "advisory": 1}
    ):
        ok("suppression_audit_mode_reports_but_never_blocks")
    else:
        fail("suppression_audit_mode_reports_but_never_blocks", f"exit={result.returncode} data={data}")


def _plugin_root_with_mode(tmp: Path, value) -> dict:
    cfg = tmp / "plugin_root" / "config"
    cfg.mkdir(parents=True)
    body = {} if value is None else {"cleanStateSuppressions": value}
    (cfg / "hook-mode.json").write_text(json.dumps(body))
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(tmp / "plugin_root")
    return env


def test_mode_comes_from_hook_mode_json_and_flag_overrides_it() -> None:
    with tempfile.TemporaryDirectory() as plug, tempfile.TemporaryDirectory() as tmp:
        env = _plugin_root_with_mode(Path(plug), "block")
        root = Path(tmp)
        _init_repo(root)
        (root / "mod.py").write_text("import os  # noqa\n")
        from_config = run_cli(root, env=env)
        overridden = run_cli(root, ["--suppression-mode", "audit"], env=env)
        if from_config.returncode == 2 and overridden.returncode == 0:
            ok("suppression_mode_from_hook_mode_json_and_flag_overrides")
        else:
            fail("suppression_mode_from_hook_mode_json_and_flag_overrides",
                 f"config exit={from_config.returncode} override exit={overridden.returncode}")


def test_missing_or_bad_config_defaults_to_audit_and_is_reported() -> None:
    with tempfile.TemporaryDirectory() as plug, tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "mod.py").write_text("import os  # noqa\n")
        # key absent from a valid file: audit, no warning
        env = _plugin_root_with_mode(Path(plug), None)
        absent = run_cli(root, env=env)
        data = json.loads(absent.stdout)
        if absent.returncode == 0 and data["suppression_mode"] == "audit" and "config_warning" not in data:
            ok("suppression_config_key_absent_defaults_to_audit")
        else:
            fail("suppression_config_key_absent_defaults_to_audit", f"exit={absent.returncode} data={data}")
    with tempfile.TemporaryDirectory() as plug, tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "mod.py").write_text("import os  # noqa\n")
        env = _plugin_root_with_mode(Path(plug), "Block")  # wrong case: unrecognised
        typo = run_cli(root, env=env)
        data = json.loads(typo.stdout)
        if typo.returncode == 0 and data["suppression_mode"] == "audit" and "Block" in data.get("config_warning", ""):
            ok("suppression_config_unrecognised_value_defaults_to_audit_with_warning")
        else:
            fail("suppression_config_unrecognised_value_defaults_to_audit_with_warning", f"exit={typo.returncode} data={data}")
    with tempfile.TemporaryDirectory() as plug, tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        cfg = Path(plug) / "config"
        cfg.mkdir()
        (cfg / "hook-mode.json").write_text("{not json")
        env = dict(os.environ)
        env["CLAUDE_PLUGIN_ROOT"] = plug
        broken = run_cli(root, env=env)
        data = json.loads(broken.stdout)
        if broken.returncode == 0 and data["suppression_mode"] == "audit" and data.get("config_warning"):
            ok("suppression_config_unreadable_defaults_to_audit_with_warning")
        else:
            fail("suppression_config_unreadable_defaults_to_audit_with_warning", f"exit={broken.returncode} data={data}")


def test_shipped_default_is_audit() -> None:
    cfg = json.loads((PLUGIN_ROOT / "config" / "hook-mode.json").read_text())
    if cfg.get("cleanStateSuppressions") == "audit":
        ok("shipped_hook_mode_json_has_cleanStateSuppressions_audit")
    else:
        fail("shipped_hook_mode_json_has_cleanStateSuppressions_audit", f"got {cfg.get('cleanStateSuppressions')!r}")


def test_existing_suppressions_are_never_flagged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "legacy.py").write_text("import os  # noqa\nx = 1  # type: ignore\ny = 2  # noqa: E402\n")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)
        # untouched: nothing in the diff
        untouched = run_cli(root, ["--suppression-mode", "block"])
        # touched elsewhere in the file: the old suppression lines are not in the diff
        (root / "legacy.py").write_text(
            "import os  # noqa\nx = 1  # type: ignore\ny = 2  # noqa: E402\nz = 3\n"
        )
        appended = run_cli(root, ["--suppression-mode", "block"])
        # the code on a suppressed line is edited but its suppression comment is unchanged
        (root / "legacy.py").write_text(
            "import sys  # noqa\nx = 1  # type: ignore\ny = 2  # noqa: E402\n"
        )
        edited = run_cli(root, ["--suppression-mode", "block"])
        results = [r for r in (untouched, appended, edited)]
        if all(r.returncode == 0 and json.loads(r.stdout)["findings"] == [] for r in results):
            ok("existing_suppressions_never_flagged_even_when_line_is_edited")
        else:
            fail("existing_suppressions_never_flagged_even_when_line_is_edited",
                 f"exits={[r.returncode for r in results]} out={[r.stdout[:200] for r in results]}")
        # ...but a NEW bare suppression in the same file is still caught
        (root / "legacy.py").write_text(
            "import os  # noqa\nx = 1  # type: ignore\ny = 2  # noqa: E402\nw = 4  # noqa\n"
        )
        added = run_cli(root, ["--suppression-mode", "block"])
        hits = [f for f in json.loads(added.stdout)["findings"] if f["pattern"] == "unexplained-suppression"]
        if added.returncode == 2 and [h["line"] for h in hits] == [4]:
            ok("new_suppression_in_file_with_legacy_ones_is_flagged_alone")
        else:
            fail("new_suppression_in_file_with_legacy_ones_is_flagged_alone", f"exit={added.returncode} hits={hits}")


def test_detector_ignores_string_literals_and_docstrings() -> None:
    source = (
        'PATTERN = "import os  # noqa"\n'
        'OTHER = "x = 1  # type: ignore"\n'
        'DOC = """\n'
        "# noqa\n"
        "# type: ignore\n"
        '"""\n'
        "def f():\n"
        '    """Docs may say # noqa or # type: ignore freely."""\n'
        "    return 1\n"
    )
    result, _data, hits = _suppression_findings(source, ["--suppression-mode", "block"])
    if result.returncode == 0 and hits == []:
        ok("detector_ignores_string_literals_and_docstrings")
    else:
        fail("detector_ignores_string_literals_and_docstrings", f"exit={result.returncode} hits={hits}")


def test_detector_ignores_its_own_fixture_strings() -> None:
    # The script's own source and this very test file carry suppression-looking
    # text inside strings; scanning them as an untracked diff must stay clean.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        for src in (SCRIPT, Path(__file__).resolve()):
            (root / src.name).write_text(src.read_text(encoding="utf-8"))
        result = run_cli(root, ["--suppression-mode", "block"])
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "unexplained-suppression"]
        if result.returncode == 0 and hits == []:
            ok("detector_ignores_its_own_fixture_strings")
        else:
            fail("detector_ignores_its_own_fixture_strings", f"exit={result.returncode} hits={hits}")


def test_non_python_files_are_not_judged() -> None:
    result, _data, hits = _suppression_findings("echo hi  # noqa\n", ["--suppression-mode", "block"], filename="run.sh")
    ts_result, _d, ts_hits = _suppression_findings(
        "// eslint-disable-next-line no-console\n// @ts-ignore\nconst a = 1;\n",
        ["--suppression-mode", "block"], filename="a.ts",
    )
    if result.returncode == 0 and hits == [] and ts_result.returncode == 0 and ts_hits == []:
        ok("non_python_files_are_not_judged")
    else:
        fail("non_python_files_are_not_judged", f"sh exit={result.returncode} ts exit={ts_result.returncode}")


def test_untokenizable_python_is_surfaced_in_skipped_not_clean() -> None:
    result, data, hits = _suppression_findings("x = (  # noqa\n", ["--suppression-mode", "block"])
    skipped = [s for s in data.get("skipped", []) if s.get("file") == "mod.py" and "tokenize" in s.get("error", "")]
    if result.returncode == 0 and hits == [] and len(skipped) == 1:
        ok("untokenizable_python_is_surfaced_in_skipped_not_clean")
    else:
        fail("untokenizable_python_is_surfaced_in_skipped_not_clean", f"exit={result.returncode} data={data}")


def test_other_classes_stay_advisory_in_block_mode() -> None:
    source = "console.log('x');\n// TODO fix\n"
    result, data, _hits = _suppression_findings(source, ["--suppression-mode", "block"], filename="a.ts")
    sev = {f["pattern"]: f["severity"] for f in data.get("findings", [])}
    if result.returncode == 0 and sev.get("console.log") == "advisory" and sev.get("todo-without-ticket") == "advisory":
        ok("other_finding_classes_stay_advisory_in_block_mode")
    else:
        fail("other_finding_classes_stay_advisory_in_block_mode", f"exit={result.returncode} sev={sev}")


def test_tracked_modified_file_new_bare_noqa_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "mod.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add"], cwd=root, check=True)
        (root / "mod.py").write_text("x = 1\ny = 2  # noqa\n")
        result = run_cli(root, ["--suppression-mode", "block"])
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["pattern"] == "unexplained-suppression"]
        if result.returncode == 2 and len(hits) == 1 and hits[0]["file"] == "mod.py" and hits[0]["line"] == 2:
            ok("tracked_modified_file_new_bare_noqa_blocks")
        else:
            fail("tracked_modified_file_new_bare_noqa_blocks", f"exit={result.returncode} data={data}")


def test_text_output_states_the_fix_and_summary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _init_repo(root)
        (root / "mod.py").write_text("import os  # noqa\n")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--project-root", str(root), "--suppression-mode", "block"],
            capture_output=True, text=True,
        )
        out = result.stdout
        if (
            result.returncode == 2
            and "BLOCKING unexplained-suppression: mod.py:1" in out
            and "# noqa: E402 - " in out
            and "report it to the user" in out
            and "1 blocking, 0 advisory" in out
        ):
            ok("text_output_states_the_fix_and_summary")
        else:
            fail("text_output_states_the_fix_and_summary", f"exit={result.returncode} out={out!r}")


def test_usage_error_is_exit_one_not_two() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--suppression-mode", "maybe"], capture_output=True, text=True
    )
    if result.returncode == 1 and "usage error" in result.stderr:
        ok("usage_error_is_exit_one_not_two")
    else:
        fail("usage_error_is_exit_one_not_two", f"exit={result.returncode} err={result.stderr!r}")


def test_not_a_git_repo_is_still_exit_one() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = run_cli(Path(tmp), ["--suppression-mode", "block"])
        if result.returncode == 1 and "git error" in result.stderr:
            ok("not_a_git_repo_still_exit_one_in_block_mode")
        else:
            fail("not_a_git_repo_still_exit_one_in_block_mode", f"exit={result.returncode} err={result.stderr!r}")


def main() -> int:
    print("test_craftflow_clean_state_check: running")
    test_console_log_detected()
    test_debugger_detected()
    test_debugger_without_semicolon_detected()
    test_clean_diff_reports_nothing()
    test_unreadable_untracked_file_is_surfaced_not_swallowed()
    test_new_untracked_directory_is_not_collapsed_and_hidden()
    test_nested_git_repo_directory_is_surfaced_in_skipped()
    test_broken_symlink_is_surfaced_in_skipped()
    test_missing_git_binary_produces_clean_error_not_traceback()
    test_todo_without_ticket_detected()
    test_todo_with_ticket_is_not_flagged()
    test_todo_with_unrelated_hash_number_is_still_flagged()
    test_todo_with_colon_hash_ticket_is_not_flagged()
    test_todo_with_colon_jira_ticket_is_not_flagged()
    test_commented_code_block_detected()
    test_block_comment_code_detected()
    test_unclosed_block_comment_does_not_bleed_into_next_file()
    test_unterminated_block_comment_at_end_of_scan_is_not_dropped()
    test_unclosed_block_in_first_of_three_files_is_still_flushed()
    test_line_comment_run_does_not_bleed_across_file_boundary()
    test_todo_ticket_regex_does_not_match_mid_identifier()
    test_eslint_disable_comments_are_not_false_positives()
    test_python_suppression_directives_are_not_false_positives()
    test_unexplained_suppressions_rejected_in_block_mode()
    test_explained_suppressions_accepted_in_block_mode()
    test_audit_mode_reports_but_never_blocks()
    test_mode_comes_from_hook_mode_json_and_flag_overrides_it()
    test_missing_or_bad_config_defaults_to_audit_and_is_reported()
    test_shipped_default_is_audit()
    test_existing_suppressions_are_never_flagged()
    test_detector_ignores_string_literals_and_docstrings()
    test_detector_ignores_its_own_fixture_strings()
    test_non_python_files_are_not_judged()
    test_untokenizable_python_is_surfaced_in_skipped_not_clean()
    test_other_classes_stay_advisory_in_block_mode()
    test_tracked_modified_file_new_bare_noqa_blocks()
    test_text_output_states_the_fix_and_summary()
    test_usage_error_is_exit_one_not_two()
    test_not_a_git_repo_is_still_exit_one()
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
