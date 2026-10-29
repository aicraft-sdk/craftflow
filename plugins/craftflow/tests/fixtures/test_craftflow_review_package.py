#!/usr/bin/env python3
"""H4: the BASE review package shows a reviewer every commit and change of a phase.

Run: python3 tests/fixtures/test_craftflow_review_package.py   (non-zero exit on any FAIL)
Covers: 3 commits + 1 uncommitted edit + 1 untracked file all land in the package, the stdout
line, the default output dir, the `unavailable` / non-ancestor / unresolvable / missing-argument
exits, the 200-line untracked cap, binary untracked files, the package not listing itself, and
BASE == HEAD (clean tree and dirty tree).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_review_package.py"
GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]

_passes = 0
_errors: list = []


def check(name: str, cond: bool, reason: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason}")
        print(f"  FAIL: {name}: {reason}")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *GIT_ID, *args], capture_output=True, text=True, check=True).stdout.strip()


def commit_file(repo: Path, name: str, text: str, subject: str) -> None:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", subject)


def new_repo(tmp: str, name: str) -> Path:
    repo = Path(tmp) / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    commit_file(repo, "base.txt", "base line\n", "chore: base")
    return repo


def run(*args: str, cwd: Path | None = None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=str(cwd) if cwd else None)


def test_full_package(tmp: str) -> None:
    repo = new_repo(tmp, "full")
    base = git(repo, "rev-parse", "HEAD")
    commit_file(repo, "a.txt", "alpha-hunk\n", "feat: add a")
    commit_file(repo, "b.txt", "bravo-hunk\n", "feat: add b")
    commit_file(repo, "c.txt", "charlie-hunk\n", "feat: add c")
    (repo / "base.txt").write_text("base line\ndelta-hunk\n")
    (repo / "e.txt").write_text("echo-untracked\n")
    out_dir = repo / ".craftflow" / "review"
    res = run(base, "--repo", str(repo), "--out-dir", str(out_dir))
    check("full: exits 0", res.returncode == 0, f"rc={res.returncode} {res.stderr}")
    head = git(repo, "rev-parse", "HEAD")
    path = out_dir / f"review-{base[:7]}..{head[:7]}.diff"
    check("full: file name is review-<base7>..<head7>.diff", path.is_file(), str(sorted(out_dir.glob('*'))))
    body = path.read_text() if path.is_file() else ""
    want = f"wrote {path}: 3 commit(s), {len(body.encode('utf-8'))} bytes"
    check("full: stdout line", res.stdout.strip() == want, f"{res.stdout.strip()!r} != {want!r}")
    for subject in ("feat: add a", "feat: add b", "feat: add c"):
        check(f"full: commit subject {subject}", subject in body, "missing")
    for hunk in ("alpha-hunk", "bravo-hunk", "charlie-hunk", "+delta-hunk"):
        check(f"full: hunk {hunk}", hunk in body, "missing")
    for heading in ("## Commits", "## Files changed", "## Diff", "## Untracked"):
        check(f"full: section {heading}", heading in body, "missing")
    untracked = body.split("## Untracked", 1)[-1]
    check("full: untracked e.txt listed with content", "e.txt" in untracked and "echo-untracked" in untracked, untracked[:200])
    # the package never lists itself, and a second run must not nest the first package
    res2 = run(base, "--repo", str(repo), "--out-dir", str(out_dir))
    body2 = path.read_text()
    check("full: rerun exits 0", res2.returncode == 0, res2.stderr)
    check("full: rerun does not list the review dir", ".craftflow/review" not in body2.split("## Untracked", 1)[-1], "package listed itself")


def test_default_out_dir(tmp: str) -> None:
    repo = new_repo(tmp, "default")
    base = git(repo, "rev-parse", "HEAD")
    commit_file(repo, "a.txt", "x\n", "feat: a")
    res = run(base, "--repo", str(repo), cwd=Path(tmp))
    check("default: exits 0", res.returncode == 0, res.stderr)
    found = list((repo / ".craftflow" / "review").glob("review-*..*.diff"))
    check("default: written under {repo}/.craftflow/review/", len(found) == 1, str(found))


def test_refusals(tmp: str) -> None:
    repo = new_repo(tmp, "refuse")
    commit_file(repo, "a.txt", "x\n", "feat: a")
    out_dir = repo / ".craftflow" / "review"
    res = run("unavailable", "--repo", str(repo), "--out-dir", str(out_dir))
    check("unavailable: exit 2", res.returncode == 2, f"rc={res.returncode}")
    check("unavailable: message", "Error: no recorded BASE" in res.stderr, res.stderr)
    check("unavailable: nothing written", not out_dir.exists() or not list(out_dir.iterdir()), "wrote a file")
    # a sibling branch commit is not an ancestor of HEAD
    git(repo, "checkout", "-q", "-b", "side", "HEAD~1")
    commit_file(repo, "s.txt", "s\n", "feat: side")
    side = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    res = run(side, "--repo", str(repo), "--out-dir", str(out_dir))
    check("non-ancestor: exit 2", res.returncode == 2, f"rc={res.returncode}")
    check("non-ancestor: message names the problem", "not an ancestor" in res.stderr, res.stderr)
    res = run("no-such-ref", "--repo", str(repo), "--out-dir", str(out_dir))
    check("unresolvable: exit 2", res.returncode == 2, f"rc={res.returncode}")
    res = run("--repo", str(repo))
    check("missing argument: exit 2 usage", res.returncode == 2 and "usage" in res.stderr.lower(), f"rc={res.returncode} {res.stderr}")
    res = run("HEAD", "--repo", str(Path(tmp) / "not-a-repo"))
    check("not a repo: exit 2", res.returncode == 2, f"rc={res.returncode}")


def test_untracked_cap(tmp: str) -> None:
    repo = new_repo(tmp, "cap")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "big.txt").write_text("".join(f"line-{i}\n" for i in range(1, 501)))
    (repo / "bin.dat").write_bytes(b"\x00\x01\x02binary")
    out_dir = repo / "pkg"
    res = run(base, "--repo", str(repo), "--out-dir", str(out_dir))
    check("cap: exits 0", res.returncode == 0, res.stderr)
    body = next(out_dir.glob("review-*.diff")).read_text()
    check("cap: line 200 kept", "line-200\n" in body, "line-200 missing")
    check("cap: line 201 dropped", "line-201\n" not in body, "line-201 present")
    check("cap: truncation marker", "truncated" in body and "300" in body, "marker missing")
    check("cap: binary file noted, not dumped", "bin.dat" in body and "binary" in body.lower() and "\x00" not in body, "binary handling")


def test_base_equals_head(tmp: str) -> None:
    repo = new_repo(tmp, "same")
    base = git(repo, "rev-parse", "HEAD")
    out_dir = repo / "pkg"
    res = run(base, "--repo", str(repo), "--out-dir", str(out_dir))
    check("BASE==HEAD clean: exits 0 with 0 commits", res.returncode == 0 and ": 0 commit(s)," in res.stdout, f"rc={res.returncode} {res.stdout} {res.stderr}")
    (repo / "wip.txt").write_text("wip-untracked\n")
    (repo / "base.txt").write_text("base line\nwip-edit\n")
    res = run(base, "--repo", str(repo), "--out-dir", str(out_dir))
    body = next(out_dir.glob("review-*.diff")).read_text()
    check("BASE==HEAD dirty: valid package", res.returncode == 0 and ": 0 commit(s)," in res.stdout, f"rc={res.returncode} {res.stderr}")
    check("BASE==HEAD dirty: edit and untracked shown", "+wip-edit" in body and "wip-untracked" in body, "missing")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        for fn in (test_full_package, test_default_out_dir, test_refusals, test_untracked_cap, test_base_equals_head):
            print(fn.__name__)
            fn(tmp)
    print(f"passes={_passes} failures={len(_errors)}")
    for err in _errors:
        print(err, file=sys.stderr)
    return 1 if _errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
