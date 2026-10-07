#!/usr/bin/env python3
"""Wiring tests for Cursor parity of craftflow:status and craftflow:failure-digest.

Run: python3 tests/fixtures/test_craftflow_cursor_exempt_wiring.py
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SKILLS = {"status": "craftflow_status_report.py", "failure-digest": "craftflow_learn_scan.py"}
INSTALL = PLUGIN_ROOT / "install-cursor.sh"
CURSOR_ROUTER = PLUGIN_ROOT / "skills" / "cursor-router" / "SKILL.md"
README_PLUGIN = PLUGIN_ROOT / "README.md"
DOCS_CRAFTFLOW = PLUGIN_ROOT.parents[3] / "docs" / "craftflow.md"
RETRO_FIXTURES = PLUGIN_ROOT / "tests" / "fixtures" / "retro"

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def check(name: str, cond: bool, reason: str) -> None:
    if cond:
        ok(name)
    else:
        fail(name, reason)


def frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_text, body) split on the first two '---' lines."""
    lines = text.splitlines()
    idx = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if len(idx) < 2 or idx[0] != 0:
        return "", text
    return "\n".join(lines[1:idx[1]]), "\n".join(lines[idx[1] + 1:])


def section(body: str, heading_prefix: str) -> str:
    m = re.search(r"^## " + re.escape(heading_prefix) + r".*?$", body, re.M)
    if not m:
        return ""
    rest = body[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def skill_body(s: str) -> str:
    text = (PLUGIN_ROOT / "skills" / s / "SKILL.md").read_text(encoding="utf-8")
    return frontmatter(text)[1]


def test_step1_resolution(s: str, script: str) -> None:
    s1 = section(skill_body(s), "Step 1")
    a = s1.find("resolve().parents[2]")
    b = s1.find("installed_plugins.json")
    check(f"step1_skill_relative_first_{s}", 0 <= a < b, f"a={a} b={b}")
    check(f"step1_existence_guard_{s}", "is_file()" in s1 and "test -f" in s1, "existence guard missing")
    check(f"step1_not_found_message_{s}", f"{script} not found" in s1, "not-found message missing")
    check(f"step1_install_cursor_{s}", "install-cursor.sh" in s1, "install-cursor.sh hint missing")
    check(f"step1_no_manual_fallback_{s}", "Never fall back to reading" in s1, "no-manual-fallback rule missing")
    check(f"step1_cursor_skill_file_{s}", f"~/.cursor/skills/{s}/SKILL.md" in s1, "cursor SKILL_FILE missing")


def test_symlink_realpath(s: str, script: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sk = Path(tmp) / "skills"
        sk.mkdir()
        (sk / s).symlink_to(PLUGIN_ROOT / "skills" / s)
        reached = (sk / s / "SKILL.md").resolve().parents[2].joinpath("scripts", script).exists()
        check(f"symlink_reaches_script_{s}", reached, "script not reached")
    with tempfile.TemporaryDirectory() as tmp2:
        sk = Path(tmp2) / "skills"
        sk.mkdir()
        shutil.copytree(PLUGIN_ROOT / "skills" / s, sk / s)
        missed = not (sk / s / "SKILL.md").resolve().parents[2].joinpath("scripts", script).exists()
        check(f"copy_misses_script_{s}", missed, "copy unexpectedly resolves")


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_symlink_run_readonly(s: str, script: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        wfdir = proj / ".craftflow" / "state" / "workflows"
        wfdir.mkdir(parents=True)
        for f in RETRO_FIXTURES.iterdir():
            if f.is_file():
                shutil.copy(f, wfdir / f.name)
        sk = Path(tmp) / "skills"
        sk.mkdir()
        (sk / s).symlink_to(PLUGIN_ROOT / "skills" / s)
        script_path = (sk / s / "SKILL.md").resolve().parents[2] / "scripts" / script
        before = _snapshot(proj)
        if s == "status":
            cmd = [sys.executable, str(script_path), "--project", str(proj), "--all"]
        else:
            cmd = [sys.executable, str(script_path), "--state-dir", str(proj / ".craftflow" / "state")]
        r = subprocess.run(cmd, cwd=proj, capture_output=True, text=True)
        good = r.returncode == 0 and bool(r.stdout.strip())
        if good and s == "failure-digest":
            try:
                good = isinstance(json.loads(r.stdout), list)
            except ValueError:
                good = False
        check(f"symlink_run_{s}", good, f"rc={r.returncode} stderr={r.stderr[:200]}")
        after = _snapshot(proj)
        check(f"symlink_run_readonly_{s}", before == after, "script wrote files under project")


def main() -> int:
    for s, script in SKILLS.items():
        test_step1_resolution(s, script)
        test_symlink_realpath(s, script)
        test_symlink_run_readonly(s, script)
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for e in _errors:
        print(e)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
