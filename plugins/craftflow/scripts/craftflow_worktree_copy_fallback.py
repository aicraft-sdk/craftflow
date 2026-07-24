#!/usr/bin/env python3
"""Copy-fallback file sync for the BUILD worktree merge-safety guard's
"Already up to date" no-op-merge case (SKILL.md step 4e, "### Worktree
Isolation (BUILD Default)"). Invoked directly by SKILL.md's bash via
`python3 "$CRAFTFLOW_INSTALL/scripts/craftflow_worktree_copy_fallback.py" <worktree_path> <project_root>`
and, identically, by the proof script via subprocess -- there is exactly one
copy of this logic.

Parses `git status --porcelain=1 -z --untracked-files=all` (NUL-delimited,
unquoted paths) rather than the non -z form, specifically because: (a)
renamed/copied paths are reported as two NUL-separated fields with no ' -> '
separator to misparse if a path itself legitimately contains that literal
substring, and (b) -z never quotes literal spaces or other characters in
paths, removing any residual quoting ambiguity for paths containing spaces.
`--untracked-files=all` is required regardless of this repo's own
`status.showUntrackedFiles` config (confirmed unset, locally and globally) --
without it, a brand-new, entirely untracked top-level directory collapses
into a single `?? <dir>/` porcelain entry instead of one entry per file,
which `_copy_one()` then tries to `open()` as a regular file, raising
`IsADirectoryError` (re-wrapped by `_safe_call` into a `RuntimeError`, but
never actually caught by `main()` -- a real, live-reproduced crash, not a
theoretical one). `--untracked-files=all` makes git list every file inside a
new untracked directory individually (`?? <dir>/file.txt`), which this
script's existing per-file copy logic already handles correctly.

Handles: Added/Modified/untracked entries (copied into the project root,
creating parent directories as needed), Deleted entries (removed from the
project root), and Renamed/Copied entries (old path removed from the project
root if present, new path copied with its current worktree content).

Symlinks are recreated as symlinks in the destination (never dereferenced
into a plain-file byte-copy of the target's contents) -- a working symlink
copied as a regular file would silently turn a symlink in the main tree into
a plain file, a silent semantic change this script must never make. An
ABSOLUTE symlink target that is itself rooted under `worktree_path` is
rewritten to the equivalent path under `project_root` before being recreated
(`_rewrite_target_if_under_worktree()`) -- recreating it verbatim would leave
a symlink pointing at a path SKILL.md's own next step
(`git worktree remove {worktree_path} --force`) deletes moments later,
producing a dangling symlink with zero warning. A relative symlink target, or
an absolute target pointing anywhere else, needs no rewrite and is recreated
unchanged.

Presence checks (for deciding whether to remove a destination path, and for
verifying a rename/copy entry's new-path source exists in the worktree) use
`os.path.lexists()`, not `os.path.exists()` -- `exists()` follows symlinks
and returns False for a broken symlink even though the symlink itself is
still present in the tree, which would silently no-op a delete/rename-old-path
removal, or wrongly hard-stop a rename whose new path is a valid (if
broken-target) symlink. A path's mere presence in the tree, not its target's,
is what matters here -- matching how `git status --porcelain` itself treats
symlinks (never dereferenced).

Refuses to guess -- raising a clean RuntimeError, never an unhandled
exception -- on:
  (a) any two-sided conflict status (UU, AA, DD, AU, UA, DU, UD) or an
      ignored ('!!') status, checked explicitly by exact code (not just a
      substring check for 'U'/'!', which misses AA and DD -- neither
      contains a literal 'U' or '!' and would otherwise silently fall
      through to a plain copy or delete, corrupting the main tree with
      unresolved conflict markers); and
  (b) a rename/copy entry whose new-path source file is missing from the
      worktree (e.g. a staged rename whose new path was itself deleted
      afterward, porcelain status 'RD') -- checked via an explicit
      existence check before the copy, rather than letting the underlying
      `open()` raise an unhandled FileNotFoundError.
These should never occur in an uncommitted BUILD worktree and are treated as
a hard error rather than silently mishandled.

Any other exception raised by the actual file operations inside the per-token
loop (permission errors, disk-full, a race where a file disappears between
the porcelain scan and the copy, etc.) is caught and re-raised as a clean
RuntimeError naming the token and the underlying exception, per this module's
own documented guarantee -- never an unhandled exception. This does NOT
provide full transactional rollback: earlier tokens in the same call that
already applied successfully remain applied in the main tree if a later
token fails, leaving a partial-but-diagnosable apply rather than an atomic
all-or-nothing one. Full rollback is not implemented by this fix; callers
that need atomicity must add it at a higher level (e.g. `git status
--porcelain` on the main tree after a failure to see exactly what landed).
"""
import os
import subprocess
import sys

CONFLICT_STATUSES = frozenset({"UU", "AA", "DD", "AU", "UA", "DU", "UD", "!!"})


def apply_copy_fallback(worktree_path, project_root):
    result = subprocess.run(
        ["git", "status", "--porcelain=1", "-z", "--untracked-files=all"],
        cwd=worktree_path,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git status failed in worktree: {result.stderr.decode(errors='replace')}")

    tokens = result.stdout.split(b"\x00")
    if tokens and tokens[-1] == b"":
        tokens = tokens[:-1]

    applied = []
    i = 0
    while i < len(tokens):
        token = tokens[i].decode("utf-8", errors="surrogateescape")
        if len(token) < 4:
            raise RuntimeError(f"unexpected short porcelain token: {token!r}")
        xy = token[0:2]
        path = token[3:]
        i += 1

        # Conflict-code check MUST run first, before the rename/copy and
        # delete branches: DD/DU/UD each contain a literal 'D' and would
        # otherwise be caught by the delete branch below and silently
        # mishandled (confirmed by real repro -- see Codebase Reality Check).
        if xy in CONFLICT_STATUSES or "U" in xy or "!" in xy:
            raise RuntimeError(
                f"unsupported/conflict porcelain status {xy!r} for {path!r} -- refusing to guess"
            )

        if "R" in xy or "C" in xy:
            if i >= len(tokens):
                raise RuntimeError(f"rename/copy entry {token!r} missing its old-path field")
            old_path = tokens[i].decode("utf-8", errors="surrogateescape")
            i += 1
            new_src = os.path.join(worktree_path, path)
            if not os.path.lexists(new_src):
                raise RuntimeError(
                    f"rename/copy entry {token!r} has no source file at {new_src!r} in the "
                    f"worktree (e.g. a staged rename whose new path was itself deleted "
                    f"afterward, porcelain status 'RD') -- refusing to guess"
                )
            _safe_call(_remove_if_exists, os.path.join(project_root, old_path),
                       action_desc=f"removing old path {old_path!r} for rename/copy entry {token!r}")
            _safe_call(_copy_one, worktree_path, project_root, path,
                       action_desc=f"copying new path {path!r} for rename/copy entry {token!r}")
            applied.append(("rename_or_copy", old_path, path))
            continue

        if "D" in xy:
            _safe_call(_remove_if_exists, os.path.join(project_root, path),
                       action_desc=f"deleting {path!r}")
            applied.append(("deleted", path))
            continue

        _safe_call(_copy_one, worktree_path, project_root, path,
                   action_desc=f"copying {path!r}")
        applied.append(("copied", path))

    return applied


def _rewrite_target_if_under_worktree(target, worktree_path, project_root):
    """An ABSOLUTE symlink target that happens to point inside
    `worktree_path` itself (e.g. `sub/abs_link -> {worktree_path}/sub/payload.txt`)
    must not be recreated verbatim in `project_root` -- it would still point
    at the worktree path, which SKILL.md's own next step
    (`git worktree remove {worktree_path} --force`) deletes moments later,
    leaving a dangling symlink with zero warning. Rewrite it to the
    equivalent path under `project_root` instead. An absolute target
    pointing anywhere else (a legitimate external absolute symlink) is
    returned unchanged -- there's no ambiguity about what it should point
    to.

    The boundary check resolves `worktree_path`, and only the DIRECTORY
    portion of `target` (never `target`'s own final path component, so a
    chained symlink whose leaf IS itself a symlink is never dereferenced
    through), with `os.path.realpath()` before comparing -- matching the
    sibling script's
    (`craftflow_worktree_lock_staleness.py`'s `decide()`) existing pattern for
    the equivalent worktree-identity comparison -- specifically to survive
    OS-level symlinked mount points. On macOS, `tempfile.mkdtemp()`'s default
    location (`/var/folders/...`) resolves via `realpath()` to
    `/private/var/folders/...`; `worktree_path` is passed to this script in
    its unresolved textual form (straight from `sys.argv`), so a target
    recorded in the resolved form would false-negative against a plain
    `normpath()` comparison, leave the symlink unrewritten, and reproduce
    exactly the dangling-symlink defect this helper exists to prevent. The
    rewritten destination is rebuilt onto `project_root`'s ORIGINAL,
    unresolved form (never a resolved variant) -- every other file operation
    in this script (`_copy_one`, `_remove_if_exists`) writes under the
    caller's literal `project_root`, and the recreated symlink must point at
    that same literal path to stay consistent with everything else this
    script writes."""
    real_worktree = os.path.realpath(worktree_path)
    # Resolve only the DIRECTORY portion of `target` for the mount-point-
    # aliasing comparison -- the actual bug this boundary check exists to fix
    # (macOS /var -> /private/var style directory symlinking) -- and
    # reattach `target`'s own unresolved final path component (basename), so
    # an intermediate symlink named as the final leaf component is never
    # itself dereferenced. A naive `os.path.realpath(target)` fully
    # dereferences THROUGH a chained symlink (e.g. sub/link1 pointing
    # absolutely at sub/link2, itself a relative symlink to payload.txt),
    # collapsing the rewritten destination straight to payload.txt's real
    # path instead of the equivalent 2-hop structure (project_root/sub/link2)
    # this function's own docstring promises.
    real_target = os.path.join(
        os.path.realpath(os.path.dirname(target)), os.path.basename(target)
    )
    if real_target == real_worktree or real_target.startswith(real_worktree + os.sep):
        suffix = real_target[len(real_worktree):]
        return os.path.normpath(project_root) + suffix
    return target


def _copy_one(worktree_path, project_root, relative_path):
    src = os.path.join(worktree_path, relative_path)
    dst = os.path.join(project_root, relative_path)
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.islink(src):
        # Recreate the symlink itself -- never dereference it into a
        # byte-copy of its target's contents, which would silently turn a
        # symlink in the main tree into a plain file.
        _remove_if_exists(dst)
        target = os.readlink(src)
        if os.path.isabs(target):
            target = _rewrite_target_if_under_worktree(target, worktree_path, project_root)
        os.symlink(target, dst)
        return
    with open(src, "rb") as f:
        data = f.read()
    with open(dst, "wb") as f:
        f.write(data)


def _remove_if_exists(path):
    # lexists(), not exists(): exists() follows symlinks and returns False
    # for a broken symlink even though the symlink itself is still present
    # in the tree -- using exists() here would silently no-op the removal.
    if os.path.lexists(path):
        os.remove(path)


def _safe_call(func, *args, action_desc):
    """Run a file-operation helper (`_copy_one`/`_remove_if_exists`), turning
    any exception it raises into a clean RuntimeError rather than letting an
    unhandled exception (or a raw OSError/PermissionError/etc.) propagate --
    per this module's own documented guarantee. Does not provide rollback of
    already-applied tokens; see the module docstring's documented limitation.
    """
    try:
        return func(*args)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"copy-fallback failed while {action_desc}: {exc.__class__.__name__}: {exc}"
        ) from exc


def main():
    if len(sys.argv) != 3:
        print("usage: craftflow_worktree_copy_fallback.py <worktree_path> <project_root>", file=sys.stderr)
        return 2
    applied = apply_copy_fallback(sys.argv[1], sys.argv[2])
    for action in applied:
        print(" ".join(action))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
