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


def test_install_loop() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    m = re.search(r"^\s*for SKILL_NAME in ([^;\n]*); do", text, re.M)
    names = m.group(1).split() if m else []
    check("install_loop_all_four", names == ["cursor-router", "retro", "status", "failure-digest"], f"names={names}")


def test_install_echo() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    check("install_echo_lists_four",
          "→ Craftflow skills (cursor-router, retro, status, failure-digest)..." in text, "header echo not updated")


def test_link_hermetic_new() -> None:
    import os
    text = INSTALL.read_text(encoding="utf-8")
    m = re.search(r"^# >>> link_cursor_skill\n(.*?)^# <<< link_cursor_skill", text, re.M | re.S)
    check("link_fn_markers", m is not None, "link_cursor_skill markers missing")
    if not m:
        return
    fn = m.group(1)
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "skills"
        env = {"PATH": os.environ["PATH"], "HOME": tmp, "PLUGIN_ROOT": str(PLUGIN_ROOT),
               "CURSOR_SKILLS_DIR": str(home)}
        cmd = fn + "\nlink_cursor_skill status; link_cursor_skill failure-digest\n"
        r1 = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True)
        check("link_fresh_exit", r1.returncode == 0, r1.stderr)
        for n in ("status", "failure-digest"):
            e = home / n
            good = e.is_symlink() and os.path.realpath(e) == os.path.realpath(PLUGIN_ROOT / "skills" / n)
            check(f"link_fresh_{n}", good, f"{n} not linked")
        r2 = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True)
        check("link_idempotent_new", r2.stdout.count("already correctly linked") == 2, r2.stdout)
    with tempfile.TemporaryDirectory() as tmp2:
        home = Path(tmp2) / "skills"
        (home / "failure-digest").mkdir(parents=True)
        env = {"PATH": os.environ["PATH"], "HOME": tmp2, "PLUGIN_ROOT": str(PLUGIN_ROOT),
               "CURSOR_SKILLS_DIR": str(home)}
        r3 = subprocess.run(["bash", "-c", fn + "\nlink_cursor_skill failure-digest\n"],
                            env=env, capture_output=True, text=True)
        backups = [p.name for p in home.iterdir() if p.name.startswith("failure-digest.stale-backup-")]
        relinked = (home / "failure-digest").is_symlink() and os.path.realpath(home / "failure-digest") == \
            os.path.realpath(PLUGIN_ROOT / "skills" / "failure-digest")
        check("link_stale_backup_failure-digest", len(backups) == 1 and relinked, f"backups={backups} {r3.stderr}")


def test_curl_hints() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    for s in SKILLS:
        check(f"curl_hint_{s}", f"skills/{s} ~/.cursor/skills/{s}" in text, f"hint for {s} missing")


def test_exempt_block() -> None:
    t = CURSOR_ROUTER.read_text(encoding="utf-8")
    i = t.find("### Router-exempt inspection skills")
    a = t.find("Only when both `pending_skill_approval` and `pending_gate` are null")
    b = t.find("Route using the first matching signal:")
    check("exempt_order", a != -1 and a < i < b, f"a={a} i={i} b={b}")
    if i == -1 or b == -1:
        return
    blk = t[i:b]
    for s in ("retro", "status", "failure-digest"):
        row = any(ln.startswith("| ") and f"`~/.cursor/skills/{s}/SKILL.md`" in ln for ln in blk.splitlines())
        check(f"exempt_table_row_{s}", row, f"no table row for {s}")
    check("exempt_no_retro_only_sentence",
          "exempts ONLY the retro skill" not in t
          and "or failure-digest requests, is still routed normally" not in t, "retro-only sentence still present")
    check("exempt_shared_precedence",
          "pending_skill_approval" in blk and "pending_gate" in blk
          and "never clears or consumes pending state" in blk and "cfstatus" in blk, "shared precedence text missing")
    check("exempt_not_exempt_rules",
          "implement, apply, or fix" in blk and "not a Craftflow workflow" in blk and "NOT exempt" in blk,
          "NOT-exempt rules missing")
    check("exempt_only_three",
          "Only these three skills are exempt" in blk and "install-cursor.sh" in blk and "overrides § 2" in blk
          and "§ 10" in blk and "Do not create a workflow artifact" in blk, "overrides/only-three text missing")


def test_carve_out() -> None:
    t = CURSOR_ROUTER.read_text(encoding="utf-8")
    i = t.find("## 10. Hard Rules (Cursor)")
    check("carve_out_present",
          i != -1 and '- The § 1 "Router-exempt inspection skills" path is not a workflow' in t[i:],
          "carve-out bullet missing")


def test_docs() -> None:
    docs = DOCS_CRAFTFLOW.read_text(encoding="utf-8")
    check("docs_craftflow_no_gap_note", "have no Cursor wiring yet" not in docs,
          "docs/craftflow.md still says status/failure-digest have no Cursor wiring")
    line = next((ln for ln in docs.splitlines() if "All 34 skills" in ln), "")
    check("docs_craftflow_lists_three", "run router-exempt in Cursor" in line,
          "All 34 skills line lacks 'run router-exempt in Cursor'")
    readme = README_PLUGIN.read_text(encoding="utf-8")
    check("readme_install_lists_four",
          "symlinks the `cursor-router`, `retro`, `status` and `failure-digest` skills" in readme,
          "README install text does not list four linked skills")
    check("readme_npx_note_all_three",
          "`craftflow:retro`, `craftflow:status` and `craftflow:failure-digest` run a script" in readme,
          "README npx note does not name all three script skills")
    check("readme_status_cursor_line", 'In Cursor, say "craftflow status"' in readme,
          "README lacks the Cursor status line")


def main() -> int:
    for s, script in SKILLS.items():
        test_step1_resolution(s, script)
        test_symlink_realpath(s, script)
        test_symlink_run_readonly(s, script)
    test_install_loop()
    test_install_echo()
    test_link_hermetic_new()
    test_curl_hints()
    test_exempt_block()
    test_carve_out()
    test_docs()
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for e in _errors:
        print(e)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
