#!/usr/bin/env python3
"""craftflow_resolve_workspace_root.py

Resolves PROJECT_ROOT for craftflow-router's Worktree Isolation step when the
session's cwd does NOT itself resolve to a git repository (a multi-repo
workspace root, e.g. `ai-infra/` containing `ai-platform-core/`,
`genai-platform-dev/`, etc.). Called ONLY after the caller's own
`git rev-parse --show-toplevel` at cwd has already failed -- this script does
not re-validate that precondition itself.

Design: docs/plans/2026-07-29-multi-repo-workspace-worktree-design.md (Option A)

Usage:
  python3 craftflow_resolve_workspace_root.py --cwd DIR --request TEXT

Output (JSON to stdout on exit 0 -- including NO_REPO_FOUND, which is a
normal decision outcome, not a script failure):
  {"outcome": "DETERMINISTIC", "project_root": "<absolute path>"}
  {"outcome": "AMBIGUOUS", "candidates": ["<absolute path>", ...]}
  {"outcome": "NO_REPO_FOUND"}

Exit codes:
  0  A decision was reached (including NO_REPO_FOUND) -- valid JSON on stdout.
  1  The script could not even complete the scan (e.g. --cwd itself is
     unreadable/does not exist). Diagnostic text on stderr, no stdout JSON.
     Callers must treat this identically to NO_REPO_FOUND for fallback
     purposes (SKILL.md step 3's existing "On failure" path).

Read-only: only ever runs `git rev-parse --show-toplevel` (never a mutating
git command) and lists directory entries. No writes, no git state changes.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


_TOKEN_SPLIT_RE = re.compile(r"[\s/\\,:;()\[\]{}\"'`]+")


def _git_toplevel(child: Path) -> Path | None:
    """Return child's own resolved git toplevel iff child IS that toplevel.

    Returns None if `git -C child rev-parse --show-toplevel` fails, OR if it
    succeeds but identifies a DIFFERENT directory as the toplevel (child is
    merely nested inside an unrelated ancestor repo -- not itself an owning
    repo root, so it must not be offered as a candidate).

    Identity is compared via `Path.samefile()` (inode+device via os.stat),
    NOT string equality of resolved paths. On a case-insensitive/
    case-preserving filesystem (macOS/APFS), git's reported toplevel reflects
    the true on-disk case while `child.resolve()` preserves the caller's own
    cwd-derived case -- these can differ as strings while still being the
    same real directory. String equality would spuriously exclude such a
    child; samefile() is immune to case-representation differences.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(child), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(
            f"craftflow_resolve_workspace_root: git check failed for {child}: {exc}",
            file=sys.stderr,
        )
        return None
    if result.returncode != 0:
        return None
    reported = result.stdout.strip()
    if not reported:
        return None
    reported_path = Path(reported)
    try:
        is_same = child.samefile(reported_path)
    except OSError as exc:
        print(
            f"craftflow_resolve_workspace_root: identity check failed for {child} "
            f"(reported={reported}): {exc}",
            file=sys.stderr,
        )
        return None
    if not is_same:
        return None
    try:
        return child.resolve()
    except OSError as exc:
        print(
            f"craftflow_resolve_workspace_root: path resolve failed for {child}: {exc}",
            file=sys.stderr,
        )
        return None


def find_repo_candidates(cwd: Path) -> list[Path]:
    """Return immediate child directories of cwd that are themselves git
    repo toplevels (not merely nested inside some outer repo). Symlinked
    children are excluded -- the design's intent is "immediate child
    directories", not arbitrary symlink targets (a symlink could point
    anywhere on disk, including an unrelated real git repo, which would
    otherwise become a silent, unconfirmed DETERMINISTIC candidate). Sorted
    for deterministic output."""
    try:
        children = sorted(p for p in cwd.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError as exc:
        raise RuntimeError(f"cannot list cwd children: {exc}") from exc
    candidates: list[Path] = []
    for child in children:
        toplevel = _git_toplevel(child)
        if toplevel is not None:
            candidates.append(toplevel)
    return candidates


def match_request_text(candidates: list[Path], request_text: str) -> Path | None:
    """Return the single candidate whose directory basename appears as an
    exact token in request_text, or None if zero or 2+ candidates match."""
    if not request_text:
        return None
    tokens = {t for t in _TOKEN_SPLIT_RE.split(request_text) if t}
    matches = [c for c in candidates if c.name in tokens]
    if len(matches) == 1:
        return matches[0]
    return None


def resolve(cwd: Path, request_text: str) -> dict:
    """Pure(-ish, read-only-subprocess) resolution. See module docstring for
    the returned outcome shapes. A single candidate is ALWAYS DETERMINISTIC
    -- there is no real ambiguity when only one nested repo exists."""
    candidates = find_repo_candidates(cwd)
    if not candidates:
        return {"outcome": "NO_REPO_FOUND"}
    if len(candidates) == 1:
        return {"outcome": "DETERMINISTIC", "project_root": str(candidates[0])}
    matched = match_request_text(candidates, request_text)
    if matched is not None:
        return {"outcome": "DETERMINISTIC", "project_root": str(matched)}
    return {"outcome": "AMBIGUOUS", "candidates": [str(c) for c in candidates]}


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="craftflow-resolve-workspace-root",
        description=(
            "Resolve PROJECT_ROOT among immediate-child git repos of a "
            "non-repo workspace-root cwd. Called only after the caller's "
            "own `git rev-parse --show-toplevel` at cwd has already failed."
        ),
    )
    parser.add_argument("--cwd", required=True, metavar="DIR")
    parser.add_argument("--request", required=True, metavar="TEXT")
    args = parser.parse_args()

    cwd = Path(args.cwd).resolve()
    try:
        result = resolve(cwd, args.request)
    except RuntimeError as exc:
        print(f"craftflow_resolve_workspace_root: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
