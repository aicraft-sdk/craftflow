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
    except (OSError, RuntimeError) as exc:
        # RuntimeError is the real exception pathlib.Path.resolve() raises
        # for a genuine on-disk symlink loop on CPython 3.9-3.12
        # ("Symlink loop from ..."), not OSError -- both must be caught.
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
    for deterministic output.

    A child's own is_dir()/is_symlink() probe can raise OSError (e.g. errno
    13 EACCES for a permission-denied sibling -- not swallowed by pathlib
    the way ENOENT/ENOTDIR/EBADF/ELOOP are). That must exclude only the
    offending child, not abort the whole scan -- a denied sibling has
    nothing to do with whether some OTHER neighboring directory is a valid
    repo. Only a genuine failure of cwd.iterdir() itself (cwd unreadable)
    aborts the scan."""
    try:
        entries = list(cwd.iterdir())
    except OSError as exc:
        raise RuntimeError(f"cannot list cwd children: {exc}") from exc
    children: list[Path] = []
    for entry in entries:
        try:
            is_candidate_dir = entry.is_dir() and not entry.is_symlink()
        except OSError as exc:
            print(
                f"craftflow_resolve_workspace_root: permission check failed for {entry}: {exc}",
                file=sys.stderr,
            )
            continue
        if is_candidate_dir:
            children.append(entry)
    candidates: list[Path] = []
    for child in sorted(children):
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


_MAX_ENTRY_LEN = 255  # defensive sanity bound only, not a design-mandated cap


def _is_direct_child_filename(entry: str) -> bool:
    """True iff `entry`, taken literally, names a direct child of the
    workspace root: non-empty, not '.'/'..' , not absolute, no '~'
    expansion, and no path-separator characters (POSIX '/' or Windows
    '\\') anywhere in the string. This is the syntactic half of the
    design's validation -- the resolved-path checks in
    read_workspace_writable_paths() below are the semantic half (defends
    against a symlink defeating this syntactic check alone)."""
    if not entry or len(entry) > _MAX_ENTRY_LEN:
        return False
    if entry in (".", ".."):
        return False
    if entry.startswith("/") or entry.startswith("~"):
        return False
    if "/" in entry or "\\" in entry:
        return False
    return True


def read_workspace_writable_paths(
    workspace_root: Path, candidates: list[Path]
) -> tuple[list[Path], list[dict]]:
    """Read {workspace_root}/.craftflow-workspace.json (if present) and
    return (validated_absolute_paths, dropped_entries). Never raises --
    a missing file, unreadable file, malformed JSON, or a
    non-list/wrong-shape `writable_paths` all degrade to ([], [<one
    diagnostic entry>]). An individually-invalid entry within an
    otherwise-valid list is dropped on its own (with its own diagnostic
    entry), never discarding the whole file (Behavior Contract: fail
    toward the stricter, more conservative outcome, never a whole-file
    failure that masks otherwise-valid siblings).

    `candidates` is the SAME nested-repo-toplevel list
    find_repo_candidates() already computes for this workspace_root --
    reused here (never re-scanned) to reject an entry whose resolved
    path lands inside (or IS) a nested repo, defending against a
    workspace-root-level symlink that points into a sibling repo even
    though its own un-resolved name passes the syntactic direct-child
    check above."""
    config_path = workspace_root / ".craftflow-workspace.json"
    if not config_path.exists():
        return [], []
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [], [{"entry": None, "reason": f"unreadable_or_malformed_json:{exc.__class__.__name__}"}]

    entries = raw.get("writable_paths") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return [], [{"entry": None, "reason": "writable_paths_missing_or_not_a_list"}]

    resolved_workspace_root = workspace_root.resolve()
    validated: list[Path] = []
    dropped: list[dict] = []
    for entry in entries:
        if not isinstance(entry, str):
            dropped.append({"entry": repr(entry), "reason": "not_a_string"})
            continue
        if not _is_direct_child_filename(entry):
            dropped.append({"entry": entry, "reason": "not_a_direct_workspace_root_child"})
            continue
        try:
            resolved_entry = (workspace_root / entry).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            # ValueError ("embedded null byte") is raised by the OS-boundary
            # calls resolve() makes when `entry` contains a literal NUL
            # character -- not an OSError/RuntimeError -- and must be caught
            # here like any other individually-invalid entry, never left to
            # crash the whole function.
            dropped.append({"entry": entry, "reason": f"resolve_failed:{exc.__class__.__name__}"})
            continue
        if resolved_entry.parent != resolved_workspace_root:
            dropped.append({"entry": entry, "reason": "resolves_outside_workspace_root"})
            continue
        inside_nested_repo = False
        for c in candidates:
            if resolved_entry == c or c in resolved_entry.parents:
                inside_nested_repo = True
                break
            try:
                # Filesystem-identity check (samefile(), inode+device via
                # os.stat), NOT string/component equality -- on a case-
                # insensitive/normalization-insensitive filesystem (macOS/
                # APFS default), an entry that differs from a nested repo's
                # directory name only in case or Unicode normalization form
                # can resolve to the SAME on-disk inode as that nested repo
                # while still failing the `==`/`.parents` string comparison
                # above. Mirrors _git_toplevel()'s established samefile()
                # pattern for the identical class of bug. samefile()
                # requires both paths to exist and raises OSError otherwise
                # -- a nonexistent resolved_entry or candidate can't be
                # identity-compared, so that failure is treated as "not the
                # same" for this candidate and the loop continues to the
                # next one (the string-based check above still applies).
                if resolved_entry.samefile(c):
                    inside_nested_repo = True
                    break
            except OSError:
                continue
        if inside_nested_repo:
            dropped.append({"entry": entry, "reason": "resolves_inside_nested_repo"})
            continue
        validated.append(resolved_entry)
    return validated, dropped


def resolve(cwd: Path, request_text: str) -> dict:
    """Pure(-ish, read-only-subprocess) resolution. See module docstring for
    the returned outcome shapes. A single candidate is ALWAYS DETERMINISTIC
    -- there is no real ambiguity when only one nested repo exists.

    DETERMINISTIC/AMBIGUOUS outcomes additionally read+validate
    {cwd}/.craftflow-workspace.json (workspace-root file allowlist, see
    docs/plans/2026-08-12-craftflow-workspace-root-allowlist-design.md)
    and, ONLY when non-empty, include `workspace_writable_paths` (and,
    only when any entry was dropped, `workspace_writable_paths_dropped`)
    in the returned dict. NO_REPO_FOUND is explicitly OUT of this scope
    per the design -- it never reads the config file at all, and its
    output stays exactly {"outcome": "NO_REPO_FOUND"}, byte-identical to
    before this function existed."""
    candidates = find_repo_candidates(cwd)
    if not candidates:
        return {"outcome": "NO_REPO_FOUND"}

    extra: dict = {}
    writable_paths, dropped = read_workspace_writable_paths(cwd, candidates)
    if writable_paths:
        extra["workspace_writable_paths"] = [str(p) for p in writable_paths]
    if dropped:
        extra["workspace_writable_paths_dropped"] = dropped

    if len(candidates) == 1:
        return {"outcome": "DETERMINISTIC", "project_root": str(candidates[0]), **extra}
    matched = match_request_text(candidates, request_text)
    if matched is not None:
        return {"outcome": "DETERMINISTIC", "project_root": str(matched), **extra}
    return {"outcome": "AMBIGUOUS", "candidates": [str(c) for c in candidates], **extra}


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

    try:
        cwd = Path(args.cwd).resolve()
        result = resolve(cwd, args.request)
    except (OSError, RuntimeError) as exc:
        # RuntimeError covers a genuine on-disk symlink loop in --cwd itself
        # (Path.resolve() real CPython 3.9-3.12 behavior); OSError covers
        # other resolve()/scan failures (e.g. unreadable path).
        print(f"craftflow_resolve_workspace_root: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
