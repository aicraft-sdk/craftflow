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


def run_cli(project_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(project_root), "--format", "json"],
        capture_output=True,
        text=True,
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
    # idioms -- Python suppression comments (# noqa, # type: ignore,
    # # pylint: disable=...) are common in this Python-heavy plugin repo and
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


def main() -> int:
    print("test_craftflow_clean_state_check: running")
    test_console_log_detected()
    test_debugger_detected()
    test_debugger_without_semicolon_detected()
    test_clean_diff_reports_nothing()
    test_unreadable_untracked_file_is_surfaced_not_swallowed()
    test_new_untracked_directory_is_not_collapsed_and_hidden()
    test_nested_git_repo_directory_is_surfaced_in_skipped()
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
