#!/usr/bin/env python3
"""
craftflow_clean_state_check.py

Advisory scan of a BUILD/DEBUG workflow's OWN diff (uncommitted worktree
changes plus untracked new files) for debug artifacts left behind before
memory-finalize. Only scans ADDED lines -- never the whole tree -- so a large
pre-existing codebase never floods findings. Never blocks a workflow.

Usage:
    craftflow_clean_state_check.py [--project-root PATH] [--format text|json]

Exit 0 on a completed scan, even with findings. Exit 1 only on a genuine git
error (not a git repository, git binary missing).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_CONSOLE_LOG_RE = re.compile(r"console\.log\(")
_DEBUGGER_RE = re.compile(r"\bdebugger\b")
_TODO_RE = re.compile(r"\bTODO\b")
# Anchored to TODO itself -- requires the ticket ref to appear immediately
# after TODO (optionally inside parens), e.g. "TODO(#123)" or "TODO #123".
# A bare match anywhere on the line (the old _TICKET_RE) wrongly treated an
# unrelated hash-number elsewhere in the comment (e.g. "ref line #42") as a
# ticket reference.
_TODO_TICKET_RE = re.compile(r"TODO\(?\s*(?:#\d+|[A-Z]{2,}-\d+)")
_COMMENT_LINE_RE = re.compile(r"^\s*(//|#)\s*(.*)$")
_CODE_TOKEN_RE = re.compile(r"[=(){};]")
_DISABLE_COMMENT_RE = re.compile(
    r"eslint-disable|@ts-|prettier-ignore|noqa|type:\s*ignore|pylint:\s*disable"
)
_BLOCK_COMMENT_START_RE = re.compile(r"/\*")
_BLOCK_COMMENT_END_RE = re.compile(r"\*/")


class GitError(Exception):
    pass


def _run_git(args: list, project_root: Path) -> str:
    result = subprocess.run(["git", "-C", str(project_root), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _added_lines(project_root: Path) -> tuple:
    """Returns ([(file, line_no, content), ...], [{"file":..., "error":...}, ...])
    for every added line across uncommitted tracked-file changes AND new
    untracked files, plus any untracked files that could not be scanned.
    Diff-scoped, not whole-tree-scoped -- this is what keeps the scan cheap
    and relevant. Unreadable files are surfaced in the second list rather
    than silently skipped, so a scan gap is never mistaken for a clean
    result."""
    added: list = []
    skipped: list = []

    diff_output = _run_git(["diff", "--unified=0", "HEAD"], project_root)
    current_file = None
    current_line = None
    for line in diff_output.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            current_line = int(m.group(1)) if m else None
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") and current_file is not None and current_line is not None:
            added.append((current_file, current_line, line[1:]))
            current_line += 1

    # --untracked-files=all lists every individual file inside a new
    # directory instead of collapsing it to one `?? newdir/` line -- without
    # this flag, a brand-new untracked directory is entirely invisible to
    # this scan (the is_file() check below is False for a directory entry,
    # so it would just be skipped, not even surfaced in `skipped`).
    status_output = _run_git(["status", "--porcelain", "--untracked-files=all"], project_root)
    for status_line in status_output.splitlines():
        if not status_line.startswith("??"):
            continue
        rel = status_line[3:].strip()
        full = project_root / rel
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            skipped.append({"file": rel, "error": str(exc)})
            continue
        for line_no, content in enumerate(text.splitlines(), start=1):
            added.append((rel, line_no, content))

    return added, skipped


def _scan_block_comments(lines: list) -> list:
    """Second detection pass for /* ... */ block comments -- independent of
    the // and # line-comment run tracked in scan(), since a block comment
    is a single multi-line comment token, not a run of separately-prefixed
    lines. Counts interior lines (between the /* and */ markers) against the
    same 3+-line threshold used for line comments. A single-line /* ... */
    (opened and closed on the same line) never counts -- it isn't a
    multi-line commented-out block."""
    findings: list = []
    in_block = False
    block_start = None
    interior: list = []

    def flush() -> None:
        if len(interior) >= 3 and block_start is not None:
            file_, start_line = block_start
            findings.append({
                "pattern": "commented-code-block",
                "file": file_,
                "line": start_line,
                "snippet": f"{len(interior)} lines inside a /* */ block comment",
            })
        interior.clear()

    for file_, line_no, content in lines:
        if not in_block:
            start_match = _BLOCK_COMMENT_START_RE.search(content)
            if start_match is None:
                continue
            if _BLOCK_COMMENT_END_RE.search(content[start_match.end():]):
                continue  # closes on the same line -- not a multi-line block
            in_block = True
            block_start = (file_, line_no)
            interior = []
            continue
        if _BLOCK_COMMENT_END_RE.search(content):
            in_block = False
            flush()
            block_start = None
            continue
        interior.append(content)

    return findings


def scan(project_root: Path) -> tuple:
    findings: list = []
    lines, skipped = _added_lines(project_root)

    comment_run: list = []

    def flush_comment_run() -> None:
        if len(comment_run) >= 3:
            file_, start_line, _ = comment_run[0]
            findings.append({
                "pattern": "commented-code-block",
                "file": file_,
                "line": start_line,
                "snippet": f"{len(comment_run)} consecutive commented code-like lines",
            })
        comment_run.clear()

    for file_, line_no, content in lines:
        if _CONSOLE_LOG_RE.search(content):
            findings.append({"pattern": "console.log", "file": file_, "line": line_no, "snippet": content.strip()})
        if _DEBUGGER_RE.search(content):
            findings.append({"pattern": "debugger", "file": file_, "line": line_no, "snippet": content.strip()})
        if _TODO_RE.search(content) and not _TODO_TICKET_RE.search(content):
            findings.append({"pattern": "todo-without-ticket", "file": file_, "line": line_no, "snippet": content.strip()})

        comment_match = _COMMENT_LINE_RE.match(content)
        is_code_like_comment = (
            comment_match is not None
            and not _DISABLE_COMMENT_RE.search(content)
            and bool(_CODE_TOKEN_RE.search(comment_match.group(2)))
        )
        if is_code_like_comment:
            comment_run.append((file_, line_no, content))
        else:
            flush_comment_run()
    flush_comment_run()

    findings.extend(_scan_block_comments(lines))

    return findings, skipped


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Craftflow advisory clean-state-exit diff scanner.")
    parser.add_argument("--project-root", default=".", help="Project root (default: .)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    try:
        findings, skipped = scan(project_root)
    except GitError as exc:
        print(json.dumps({"error": f"git error: {exc}"}), file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps({"findings": findings, "skipped": skipped}, indent=2))
    else:
        if not findings:
            print("Clean-state check: 0 findings.")
        for f in findings:
            print(f"{f['pattern']}: {f['file']}:{f['line']}: {f['snippet']}")
        for s in skipped:
            print(f"SKIPPED (unreadable): {s['file']}: {s['error']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
