#!/usr/bin/env python3
"""
craftflow_clean_state_check.py

Scan of a BUILD/DEBUG workflow's OWN diff (uncommitted worktree changes plus
untracked new files) for debug artifacts left behind before memory-finalize.
Only scans ADDED lines -- never the whole tree -- so a large pre-existing
codebase never floods findings.

Two classes of finding:

1. Advisory (always, exactly as before): console logging, debugger
   statements, ticketless to-do markers and commented-out code blocks. These
   never affect the exit code.
2. Unexplained suppressions (`unexplained-suppression`): a NEWLY ADDED Python
   suppression comment that lacks a specific rule code or a written reason.
   Existing suppressions are never judged -- only added lines, and a line
   whose suppression comment is carried over unchanged from a removed line in
   the same hunk is not "new". Whether this class blocks is a switch (below).

Accepted forms (Python files only, `.py` / `.pyi`; comments are found with
the tokenizer, so a suppression-looking string literal or docstring is never
flagged):

    noqa          rule code(s) AND a reason, e.g.
                      `# noqa: E402 - why the late import is needed`
                  A bare noqa, or codes with no reason, are violations.
    type: ignore  bracketed code(s) AND a reason, e.g.
                      `# type: ignore[attr-defined] - why the checker is wrong`
                  A bare type-ignore, or no reason, are violations.

A reason is introduced by ` - `, ` -- `, an en/em dash, a second `#`, or a
parenthesised note `(why)`, and needs at least three letters. This matches the
house style already used across the repo (` - reason`, ` -- reason`,
`(reason)`). Python only: eslint-disable / ts-ignore style comments are NOT
judged by this check (they are only excluded from the commented-code
heuristic, as before).

Switch (follows the hook-mode.json audit/block convention):
    The `cleanStateSuppressions` key of config/hook-mode.json, "audit"
    (shipped default) or "block", or `--suppression-mode audit|block`, which
    wins over the file. A missing file or key means "audit". An unreadable file
    or an unrecognised value also falls back to "audit" but is reported
    (`config_warning` in json, stderr in text) -- never a silent downgrade.
    In "audit" the suppression findings are reported with severity "advisory"
    and the exit code is unaffected, so every existing caller keeps its
    contract. In "block" they get severity "blocking" and the run exits 2.

Usage:
    craftflow_clean_state_check.py [--project-root PATH] [--format text|json]
        [--suppression-mode audit|block]

Exit codes:
    0  completed scan (findings, if any, are advisory).
    1  genuine tool error: not a git repository, git binary missing, or a
       malformed command line (argparse usage errors are mapped to 1 so they
       cannot be mistaken for 2).
    2  completed scan with at least one BLOCKING finding (only possible in
       "block" mode). The full report is still printed on stdout; a caller
       that gates on it must treat 2 as "violations found", not a crash.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

_CONSOLE_LOG_RE = re.compile(r"console\.log\(")
_DEBUGGER_RE = re.compile(r"\bdebugger\b")
_TODO_RE = re.compile(r"\bTODO\b")
# Anchored to TODO itself -- requires the ticket ref to appear immediately
# after TODO (optionally inside parens and/or after a colon), e.g.
# "TODO(#123)", "TODO #123", "TODO: #123", or "TODO: JIRA-456". A bare match
# anywhere on the line (the old _TICKET_RE) wrongly treated an unrelated
# hash-number elsewhere in the comment (e.g. "ref line #42") as a ticket
# reference; the colon tolerance was added after "TODO: #123"/"TODO: JIRA-456"
# (the common colon-separated ticket style) were found still being wrongly
# flagged as ticketless. The leading \b (mirroring _TODO_RE's \bTODO\b) is
# required -- without it this pattern matches mid-identifier, e.g. the
# embedded "TODO(#123" substring inside "MEGATODO(#123)", wrongly treating
# an unrelated bare TODO elsewhere on the same line as ticketed.
_TODO_TICKET_RE = re.compile(r"\bTODO\(?:?\s*(?:#\d+|[A-Z]{2,}-\d+)")
_COMMENT_LINE_RE = re.compile(r"^\s*(//|#)\s*(.*)$")
_CODE_TOKEN_RE = re.compile(r"[=(){};]")
_DISABLE_COMMENT_RE = re.compile(
    r"eslint-disable|@ts-|prettier-ignore|noqa|type:\s*ignore|pylint:\s*disable"
)
_BLOCK_COMMENT_START_RE = re.compile(r"/\*")
_BLOCK_COMMENT_END_RE = re.compile(r"\*/")


# --- Unexplained-suppression detection (Python comments only) -------------
# Matched against the text of a real COMMENT token (never a string literal).
# The marker may sit anywhere in the comment, like flake8/mypy search it.
_NOQA_RE = re.compile(r"#\s*(?:flake8\s*[:=]\s*)?noqa\b(?P<rest>.*)", re.IGNORECASE)
_TYPE_IGNORE_RE = re.compile(r"#\s*type:\s*ignore\b(?P<rest>.*)")
_NOQA_CODES_RE = re.compile(r"\s*:\s*(?P<codes>[A-Za-z]+\d+(?:[\s,]+[A-Za-z]+\d+)*)(?P<tail>.*)$")
_TYPE_IGNORE_CODES_RE = re.compile(r"\s*\[\s*[\w.\-]+(?:\s*,\s*[\w.\-]+)*\s*\](?P<tail>.*)$")
# A reason must follow an explicit delimiter: a dash run, an en/em dash, a
# second `#`, or a parenthesised note; and carry at least three letters.
_REASON_RE = re.compile(r"^\s*(?:(?:-{1,2}|\u2013|\u2014|#)\s*(?P<dash>.*)|\((?P<paren>.*)\)\s*)$")
_SUPPRESSION_FILE_SUFFIXES = (".py", ".pyi")

SUPPRESSION_FIX_TEXT = (
    "Fix: name the specific rule code(s) AND give a written reason, e.g. "
    "`# noqa: E402 - why the late import is needed` or "
    "`# type: ignore[attr-defined] - why the checker is wrong`. A reason follows "
    "` - `, ` -- `, an en/em dash, a second `#`, or sits in parentheses `(why)`, "
    "and needs at least three letters. If the underlying linter or type checker "
    "is reporting a false positive, do not suppress it: report it to the user "
    "instead."
)

SUPPRESSION_MODES = ("audit", "block")
DEFAULT_SUPPRESSION_MODE = "audit"
SUPPRESSION_CONFIG_KEY = "cleanStateSuppressions"
EXIT_OK = 0
EXIT_TOOL_ERROR = 1
EXIT_BLOCKING_FINDINGS = 2


class GitError(Exception):
    pass


class _ToolArgumentParser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, which would collide with this
    script's exit 2 (blocking findings). Report usage errors as a tool error
    (exit 1) instead."""

    def error(self, message: str):
        print(json.dumps({"error": f"usage error: {message}"}), file=sys.stderr)
        raise SystemExit(EXIT_TOOL_ERROR)


def _has_reason(tail: str) -> bool:
    match = _REASON_RE.match(tail)
    if match is None:
        return False
    text = match.group("dash") if match.group("dash") is not None else match.group("paren")
    return len(re.findall(r"[^\W\d_]", text or "")) >= 3


def _suppression_problem(comment: str):
    """Returns (kind, problem) for the first malformed suppression in a
    comment's text, or None when every suppression in it is well formed (or it
    holds none)."""
    noqa = _NOQA_RE.search(comment)
    if noqa is not None:
        codes = _NOQA_CODES_RE.match(noqa.group("rest"))
        if codes is None:
            return "noqa", "noqa without a specific rule code"
        if not _has_reason(codes.group("tail")):
            return "noqa", "noqa without a written reason"
    type_ignore = _TYPE_IGNORE_RE.search(comment)
    if type_ignore is not None:
        codes = _TYPE_IGNORE_CODES_RE.match(type_ignore.group("rest"))
        if codes is None:
            return "type-ignore", "type: ignore without a bracketed error code"
        if not _has_reason(codes.group("tail")):
            return "type-ignore", "type: ignore without a written reason"
    return None


def _suppression_markers(text: str) -> set:
    """The exact suppression-comment spellings on one raw line, used only to
    recognise a suppression carried over unchanged from a removed line."""
    found = set()
    for regex in (_NOQA_RE, _TYPE_IGNORE_RE):
        match = regex.search(text)
        if match is not None:
            found.add(match.group(0).strip())
    return found


def _comments_by_line(text: str):
    """{line_no: comment text} for every real comment token in Python source,
    or None when the source cannot be tokenized."""
    comments: dict = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                comments[tok.start[0]] = tok.string
    except (tokenize.TokenError, SyntaxError):
        return None
    return comments


def _resolve_suppression_mode(cli_value) -> tuple:
    """(mode, warning). Precedence: --suppression-mode, then the
    `cleanStateSuppressions` key of config/hook-mode.json, then "audit"."""
    if cli_value:
        return cli_value, None
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    config = (Path(root) if root else Path(__file__).resolve().parents[1]) / "config" / "hook-mode.json"
    if not config.exists():
        return DEFAULT_SUPPRESSION_MODE, None
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return DEFAULT_SUPPRESSION_MODE, f"{config} unreadable ({exc}); {SUPPRESSION_CONFIG_KEY} defaulted to audit"
    value = data.get(SUPPRESSION_CONFIG_KEY) if isinstance(data, dict) else None
    if value is None:
        return DEFAULT_SUPPRESSION_MODE, None
    if value not in SUPPRESSION_MODES:
        return DEFAULT_SUPPRESSION_MODE, (
            f"{config}: {SUPPRESSION_CONFIG_KEY}={value!r} is not one of {', '.join(SUPPRESSION_MODES)}; defaulted to audit"
        )
    return value, None


def _run_git(args: list, project_root: Path) -> str:
    try:
        result = subprocess.run(["git", "-C", str(project_root), *args], capture_output=True, text=True)
    except FileNotFoundError as exc:
        # subprocess.run(["git", ...]) raises a raw FileNotFoundError when the
        # git binary itself is missing from PATH -- re-raise as GitError so
        # main() converts this into a clean JSON error + exit 1, matching
        # this module's own documented contract, instead of an uncaught
        # traceback propagating past main().
        raise GitError(f"git binary not found: {exc}") from exc
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _added_lines(project_root: Path, carried_out: "set | None" = None) -> tuple:
    """Returns ([(file, line_no, content), ...], [{"file":..., "error":...}, ...])
    for every added line across uncommitted tracked-file changes AND new
    untracked files, plus any untracked files that could not be scanned.
    Diff-scoped, not whole-tree-scoped -- this is what keeps the scan cheap
    and relevant. Unreadable files are surfaced in the second list rather
    than silently skipped, so a scan gap is never mistaken for a clean
    result.

    `carried_out`, when given, receives (file, line_no) for every added
    tracked-file line whose suppression comment appears unchanged on a removed
    line of the same hunk (an edit that left the suppression alone)."""
    added: list = []
    skipped: list = []
    removed_markers: set = set()

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
            removed_markers = set()
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("-"):
            removed_markers |= _suppression_markers(line[1:])
            continue
        if line.startswith("+") and current_file is not None and current_line is not None:
            added.append((current_file, current_line, line[1:]))
            if carried_out is not None and _suppression_markers(line[1:]) & removed_markers:
                carried_out.add((current_file, current_line))
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
            if full.is_dir():
                # git structurally never descends into an embedded .git,
                # regardless of --untracked-files=all -- an untracked
                # directory that itself contains a .git (e.g. an
                # accidentally-vendored dependency) always collapses to one
                # `?? dir/` line and can never be scanned. Surface the gap
                # rather than silently dropping it from both findings and
                # skipped.
                skipped.append({"file": rel, "error": "nested git repository -- not scanned"})
            else:
                # Neither is_file() nor is_dir(): a broken/dangling symlink,
                # a symlink loop, a socket, a FIFO, or a device file. There
                # was no else branch here before -- it fell through to
                # `continue` with nothing recorded in findings OR skipped, a
                # silent scan gap indistinguishable from a genuinely clean
                # result. Surface it explicitly instead.
                reason = "broken symlink" if full.is_symlink() else "special file"
                skipped.append({
                    "file": rel,
                    "error": f"not a regular file or directory ({reason}) -- not scanned",
                })
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
    prev_file = None

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
        if file_ != prev_file:
            # A block comment can never legitimately span files -- reset all
            # in-progress state at every file boundary so an unclosed /* in
            # one file can never bleed into (and misattribute) a later,
            # unrelated file's content. An unclosed block must still be
            # FLUSHED here before the reset: the end-of-scan `if in_block:
            # flush()` below only rescues a block that is still open when the
            # ENTIRE scan ends (i.e. only when it's in the LAST file) -- an
            # unclosed block in any non-last file was previously discarded
            # silently by this reset (reset without flush), never reaching
            # findings or skipped.
            if in_block:
                flush()
            in_block = False
            interior = []
            block_start = None
            prev_file = file_
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

    if in_block:
        # A block that opens with /* and never closes anywhere in the
        # scanned diff must still be flushed -- otherwise its interior lines
        # are silently discarded, the exact "invisible commented-out code"
        # symptom this pass exists to catch.
        flush()

    return findings


def _scan_suppressions(project_root: Path, lines: list, carried: set, blocking: bool) -> tuple:
    """Judge NEWLY ADDED Python suppression comments (see module docstring).
    Returns (findings, skipped); a Python file that cannot be tokenized is
    surfaced in `skipped`, never silently treated as clean."""
    findings: list = []
    skipped: list = []
    added_by_file: dict = {}
    for file_, line_no, content in lines:
        if file_.endswith(_SUPPRESSION_FILE_SUFFIXES):
            added_by_file.setdefault(file_, []).append((line_no, content))
    for file_, entries in added_by_file.items():
        # Cheap pre-filter: no marker on any added line, nothing to tokenize.
        if not any(_NOQA_RE.search(c) or _TYPE_IGNORE_RE.search(c) for _, c in entries):
            continue
        try:
            text = (project_root / file_).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            skipped.append({"file": file_, "error": f"suppression check skipped: {exc}"})
            continue
        comments = _comments_by_line(text)
        if comments is None:
            skipped.append({"file": file_, "error": "suppression check skipped: could not tokenize Python source"})
            continue
        for line_no, content in entries:
            comment = comments.get(line_no)
            if comment is None or (file_, line_no) in carried:
                continue
            problem = _suppression_problem(comment)
            if problem is None:
                continue
            kind, why = problem
            findings.append({
                "pattern": "unexplained-suppression",
                "kind": kind,
                "file": file_,
                "line": line_no,
                "snippet": content.strip(),
                "severity": "blocking" if blocking else "advisory",
                "message": f"Newly added suppression is not allowed: {why}. {SUPPRESSION_FIX_TEXT}",
            })
    return findings, skipped


def scan(project_root: Path, suppression_mode: str = DEFAULT_SUPPRESSION_MODE) -> tuple:
    """Returns (findings, skipped). `suppression_mode` "audit" (default, the
    pre-existing contract) reports unexplained suppressions as advisory;
    "block" marks them severity "blocking". Every other finding is advisory."""
    findings: list = []
    carried: set = set()
    lines, skipped = _added_lines(project_root, carried)

    comment_run: list = []
    prev_comment_run_file = None

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
        if file_ != prev_comment_run_file:
            # Mirrors the block-comment reset in _scan_block_comments(): a
            # run of consecutive commented code-like lines can never
            # legitimately span files. Without this reset, a comment run
            # left open at the end of one file (below the 3-line threshold
            # on its own) silently combines with the next file's leading
            # comment lines, crossing the threshold and producing a finding
            # misattributed to the first file.
            flush_comment_run()
            prev_comment_run_file = file_
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

    for finding in findings:
        finding.setdefault("severity", "advisory")
    sup_findings, sup_skipped = _scan_suppressions(project_root, lines, carried, suppression_mode == "block")
    findings.extend(sup_findings)
    skipped.extend(sup_skipped)

    return findings, skipped


def _build_parser() -> argparse.ArgumentParser:
    parser = _ToolArgumentParser(description="Craftflow clean-state-exit diff scanner (advisory; newly added unexplained suppressions can block).")
    parser.add_argument("--project-root", default=".", help="Project root (default: .)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--suppression-mode",
        choices=SUPPRESSION_MODES,
        default=None,
        help=f"Override config/hook-mode.json {SUPPRESSION_CONFIG_KEY} (default there: {DEFAULT_SUPPRESSION_MODE})",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    suppression_mode, config_warning = _resolve_suppression_mode(args.suppression_mode)

    try:
        findings, skipped = scan(project_root, suppression_mode)
    except GitError as exc:
        print(json.dumps({"error": f"git error: {exc}"}), file=sys.stderr)
        return EXIT_TOOL_ERROR

    blocking = sum(1 for f in findings if f.get("severity") == "blocking")
    summary = {"total": len(findings), "blocking": blocking, "advisory": len(findings) - blocking}

    if args.format == "json":
        payload = {
            "findings": findings,
            "skipped": skipped,
            "summary": summary,
            "suppression_mode": suppression_mode,
        }
        if config_warning:
            payload["config_warning"] = config_warning
        print(json.dumps(payload, indent=2))
    else:
        if not findings:
            print("Clean-state check: 0 findings.")
        for f in findings:
            if f["severity"] == "blocking":
                print(f"BLOCKING {f['pattern']}: {f['file']}:{f['line']}: {f['snippet']}")
                print(f"  {f['message']}")
            else:
                print(f"{f['pattern']}: {f['file']}:{f['line']}: {f['snippet']}")
        if findings:
            print(
                f"Clean-state check: {summary['total']} finding(s): "
                f"{summary['blocking']} blocking, {summary['advisory']} advisory."
            )
        for s_ in skipped:
            print(f"SKIPPED (unreadable): {s_['file']}: {s_['error']}", file=sys.stderr)
        if config_warning:
            print(f"CONFIG WARNING: {config_warning}", file=sys.stderr)
    return EXIT_BLOCKING_FINDINGS if blocking else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
