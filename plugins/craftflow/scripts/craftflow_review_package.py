#!/usr/bin/env python3
"""Write one review package for a BUILD or DEBUG phase: the commit list, the stat summary and
the net diff of BASE..HEAD, the uncommitted hunks against HEAD, and the untracked files, so a
reviewer reads every change of the phase in a single call and the diff never travels through the
router's context.

Usage: craftflow_review_package.py BASE [--repo DIR] [--out-dir DIR]
  BASE       the sha recorded in results.git_base_sha BEFORE the phase's build-implement task ran
  --repo     the git tree to diff (default: the current directory)
  --out-dir  where to write the package (default: <repo toplevel>/.craftflow/review)

Writes review-<base7>..<head7>.diff and prints 'wrote <path>: <n> commit(s), <bytes> bytes'.
Exit 2: BASE is `unavailable`, does not resolve, is not an ancestor of HEAD, or git fails.

BASE must be the recorded sha, NEVER HEAD~1: HEAD~1 silently drops all but the last commit of a
multi-commit phase, so the reviewer would approve work it never read. BASE == HEAD is a valid
package (0 commits): the uncommitted and untracked sections still carry the phase's changes, and
a clean tree yields an honest empty package rather than an error.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

UNTRACKED_LINE_CAP = 200
CONTEXT = "-U10"


class PackageError(Exception):
    """A refusal that maps to exit 2 with the message on stderr."""


def git(repo: Optional[str], *args: str, check: bool = True) -> subprocess.CompletedProcess:
    prefix = ["git", "-C", repo] if repo else ["git"]
    proc = subprocess.run([*prefix, *args], capture_output=True, text=True, errors="replace", check=False)
    if check and proc.returncode != 0:
        raise PackageError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip() or proc.returncode}")
    return proc


def text(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout or "(none)\n"


def resolve(repo: Optional[str], ref: str) -> Optional[str]:
    proc = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def untracked_section(repo: Optional[str], top: Path, out_dir: Path) -> str:
    names = git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
    parts: List[str] = []
    for name in sorted(n for n in names if n):
        path = top / name
        try:  # the package never lists itself or its earlier siblings
            path.resolve().relative_to(out_dir.resolve())
            continue
        except ValueError:
            pass
        try:
            raw = path.read_bytes()
        except OSError as exc:
            parts.append(f"### {name}\n(unreadable: {exc.strerror or exc})\n")
            continue
        if b"\0" in raw:
            parts.append(f"### {name}\n(binary file, {len(raw)} bytes, content not shown)\n")
            continue
        lines = raw.decode("utf-8", errors="replace").splitlines()
        shown = "\n".join(lines[:UNTRACKED_LINE_CAP])
        more = len(lines) - UNTRACKED_LINE_CAP
        marker = f"\n... [truncated: {more} more line(s) not shown]" if more > 0 else ""
        parts.append(f"### {name}\n{shown}{marker}\n")
    return "".join(parts) or "(none)\n"


def build_package(repo: Optional[str], base: str, out_dir: Optional[str]) -> str:
    if base.strip() in ("", "unavailable"):
        raise PackageError("Error: no recorded BASE")
    base_sha = resolve(repo, base)
    if base_sha is None:
        raise PackageError(f"Error: BASE {base} does not resolve to a commit")
    head_sha = resolve(repo, "HEAD")
    if head_sha is None:
        raise PackageError("Error: HEAD does not resolve to a commit")
    if git(repo, "merge-base", "--is-ancestor", base_sha, head_sha, check=False).returncode != 0:
        raise PackageError(f"Error: BASE {base_sha[:7]} is not an ancestor of HEAD {head_sha[:7]}")
    top = Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip())
    target = Path(out_dir) if out_dir else top / ".craftflow" / "review"
    rng = f"{base_sha}..{head_sha}"
    commits = git(repo, "rev-list", "--count", rng).stdout.strip()
    body = (
        "# Review package\n"
        f"\nRange: {base_sha[:7]}..{head_sha[:7]} (BASE..HEAD), plus uncommitted and untracked changes\n"
        f"\n## Commits\n{text(git(repo, 'log', '--oneline', rng))}"
        f"\n## Files changed\n{text(git(repo, 'diff', '--stat', rng))}"
        f"\n## Diff\n{text(git(repo, 'diff', CONTEXT, rng))}"
        f"\n## Uncommitted changes\n{text(git(repo, 'diff', CONTEXT, 'HEAD'))}"
        f"\n## Untracked\n{untracked_section(repo, top, target)}"
    )
    try:
        target.mkdir(parents=True, exist_ok=True)
        out = target / f"review-{base_sha[:7]}..{head_sha[:7]}.diff"
        out.write_text(body, encoding="utf-8")
    except OSError as exc:
        raise PackageError(f"Error: cannot write review package under {target}: {exc}")
    return f"wrote {out}: {commits} commit(s), {len(body.encode('utf-8'))} bytes"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="craftflow_review_package.py", description=__doc__.splitlines()[0])
    parser.add_argument("base", help="sha recorded before the phase started (results.git_base_sha)")
    parser.add_argument("--repo", default=None, help="git tree to diff (default: current directory)")
    parser.add_argument("--out-dir", default=None, help="output dir (default: <repo>/.craftflow/review)")
    args = parser.parse_args(argv)
    try:
        print(build_package(args.repo, args.base, args.out_dir))
    except PackageError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError:
        print("Error: git not found on PATH", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
