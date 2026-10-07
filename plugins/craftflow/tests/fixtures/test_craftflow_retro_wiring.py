#!/usr/bin/env python3
"""Wiring tests for the craftflow:retro skill (skill structure).

Run: python3 tests/fixtures/test_craftflow_retro_wiring.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

SKILL = PLUGIN_ROOT / "skills" / "retro" / "SKILL.md"

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


def read_skill() -> str:
    return SKILL.read_text(encoding="utf-8")


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


def test_skill_exists() -> None:
    print("\n[skill file]")
    check("skill_exists", SKILL.is_file(), f"missing {SKILL}")


def test_frontmatter() -> None:
    print("\n[frontmatter]")
    if not SKILL.is_file():
        fail("frontmatter", "skill missing")
        return
    fm, _ = frontmatter(read_skill())
    check("fm_name_retro", re.search(r"^name: retro\s*$", fm, re.M) is not None, "name: retro missing")
    m = re.search(r"^allowed-tools:\s*(.*)$", fm, re.M)
    tools = m.group(1).strip() if m else ""
    check("fm_allowed_tools", tools == "Read, Bash, Glob", f"allowed-tools={tools!r}")
    check(
        "fm_no_write_tools",
        not re.search(r"Write|Edit|NotebookEdit", tools),
        "write tools present",
    )
    check(
        "fm_description_exempt",
        "does NOT go through craftflow-router" in fm,
        "description lacks 'does NOT go through craftflow-router'",
    )


def test_boundary_statement() -> None:
    print("\n[boundary]")
    if not SKILL.is_file():
        fail("boundary", "skill missing")
        return
    _, body = frontmatter(read_skill())
    check(
        "boundary_do_not_call",
        "**Do NOT call craftflow-router. Do NOT create tasks. Do NOT modify files.**" in body,
        "boundary sentence missing",
    )
    check("boundary_intro", "**Does NOT go through craftflow-router.**" in body, "intro boundary missing")
    check(
        "implement_not_exempt",
        "NOT exempt" in body and "implement" in body and "craftflow-router" in body,
        "implement/apply/fix not-exempt rule missing",
    )


def test_signal_ids_in_rubric() -> None:
    print("\n[signal ids]")
    if not SKILL.is_file():
        fail("signal_ids", "skill missing")
        return
    import craftflow_retro

    ids = [sid for sid, _ in craftflow_retro.EXTRACTORS]
    check("signal_ids_nonempty", len(ids) == 11, f"expected 11 ids, got {len(ids)}")
    _, body = frontmatter(read_skill())
    step5 = section(body, "Step 5")
    for sid in ids:
        check(f"rubric_has_{sid}", f"`{sid}`" in step5, f"{sid} not in Step 5 rubric")
    for cat in ("nav pointer", "deterministic check", "reviewer standard", "pruning", "tool/telemetry change"):
        check(f"category_{cat}", cat in step5, f"category {cat!r} missing")
    for row in range(1, 9):
        check(f"rubric_row_{row}", re.search(rf"^\|\s*{row}\s*\|", step5, re.M) is not None, f"row {row} missing")


def test_rubric_no_mappable_line() -> None:
    print("\n[rubric lines]")
    if not SKILL.is_file():
        fail("rubric_lines", "skill missing")
        return
    _, body = frontmatter(read_skill())
    check("no_mappable_line", "Signals with no mappable proposal:" in body, "line rule missing")
    check("no_friction_found", "No friction found" in body, "'No friction found' missing")
    check("proposal_cap", "at most 5 proposals" in body, "cap of 5 missing")
    check("cause_unclear", "cause unclear from evidence" in body, "'cause unclear' rule missing")


def test_skill_resolution_order() -> None:
    print("\n[step 1 resolution]")
    if not SKILL.is_file():
        fail("resolution_order", "skill missing")
        return
    _, body = frontmatter(read_skill())
    s1 = section(body, "Step 1")
    check("step1_present", s1 != "", "Step 1 section missing")
    i_rel = s1.find("resolve().parents[2]")
    i_reg = s1.find("installed_plugins.json")
    check("step1_skill_relative_first", 0 <= i_rel < i_reg, f"parents[2]@{i_rel} installed_plugins@{i_reg}")
    check("step1_existence_guard", "is_file()" in s1 and "test -f" in s1, "existence guard missing")
    check("step1_not_found_message", "craftflow_retro.py not found" in s1, "not-found message missing")
    check("step1_install_cursor", "install-cursor.sh" in s1, "install-cursor.sh hint missing")
    check("step1_stop_no_fallback", "Never fall back to reading the artifact by hand" in s1, "no-manual-fallback rule missing")


def test_steps_present() -> None:
    print("\n[steps]")
    if not SKILL.is_file():
        fail("steps", "skill missing")
        return
    text = read_skill()
    _, body = frontmatter(text)
    for n in range(1, 8):
        check(f"step_{n}", re.search(rf"^## Step {n} ", body, re.M) is not None, f"Step {n} missing")
    check("terminal_usage", "## Terminal Usage" in body and "alias cfretro=" in body, "terminal usage/alias missing")
    check("flags_wf_latest_list", all(f in body for f in ("--wf", "--latest", "--list")), "flags missing")
    check("ambiguous_selection", "ambiguous_selection" in body, "ambiguous_selection rule missing")
    check("line_count", len(text.splitlines()) < 400, f"{len(text.splitlines())} lines")


INSTALL = PLUGIN_ROOT / "install-cursor.sh"
CURSOR_ROUTER = PLUGIN_ROOT / "skills" / "cursor-router" / "SKILL.md"
README_PLUGIN = PLUGIN_ROOT / "README.md"
README_ROOT = PLUGIN_ROOT.parents[1] / "README.md"


def test_install_link_function_hermetic() -> None:
    import os
    import subprocess
    import tempfile
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
        cmd = fn + "\nlink_cursor_skill cursor-router; link_cursor_skill retro\n"
        r1 = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True)
        check("link_fresh_exit", r1.returncode == 0, r1.stderr)
        for n in ("cursor-router", "retro"):
            e = home / n
            check(f"link_fresh_{n}", e.is_symlink() and os.path.realpath(e) == os.path.realpath(PLUGIN_ROOT / "skills" / n), f"{n} not linked")
        r2 = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True)
        check("link_idempotent", r2.stdout.count("already correctly linked") == 2, r2.stdout)
    with tempfile.TemporaryDirectory() as tmp2:
        home = Path(tmp2) / "skills"
        (home / "retro").mkdir(parents=True)
        env = {"PATH": os.environ["PATH"], "HOME": tmp2, "PLUGIN_ROOT": str(PLUGIN_ROOT),
               "CURSOR_SKILLS_DIR": str(home)}
        r3 = subprocess.run(["bash", "-c", fn + "\nlink_cursor_skill retro\n"], env=env, capture_output=True, text=True)
        backups = [p.name for p in home.iterdir() if p.name.startswith("retro.stale-backup-")]
        check("link_stale_backup", len(backups) == 1, f"backups={backups} {r3.stderr}")
        check("link_stale_relinked", (home / "retro").is_symlink() and os.path.realpath(home / "retro") == os.path.realpath(PLUGIN_ROOT / "skills" / "retro"), "retro not relinked")


def test_install_curl_piped_hint_mentions_retro() -> None:
    check("curl_hint_retro", "skills/retro ~/.cursor/skills/retro" in INSTALL.read_text(encoding="utf-8"), "hint missing")


def test_install_loop_links_both_skills() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    m = re.search(r"^\s*for SKILL_NAME in ([^;\n]*); do", text, re.M)
    check("install_loop_present", m is not None, "for SKILL_NAME loop missing")
    if not m:
        return
    names = m.group(1).split()
    check("install_loop_cursor_router", "cursor-router" in names, f"loop names={names}")
    check("install_loop_retro", "retro" in names, f"loop names={names}")


def test_cursor_router_exemption_block() -> None:
    t = CURSOR_ROUTER.read_text(encoding="utf-8")
    i = t.find("### Router-exempt inspection skills")
    a = t.find("Only when both `pending_skill_approval` and `pending_gate` are null")
    b = t.find("Route using the first matching signal:")
    check("exempt_order", a != -1 and a < i < b, f"a={a} i={i} b={b}")
    if i == -1 or b == -1:
        return
    blk = t[i:b]
    for needle in ("~/.cursor/skills/retro/SKILL.md", "Do not create a workflow artifact",
                   "implement, apply, or fix", "pending_gate", "overrides § 2", "§ 10"):
        check(f"exempt_has_{needle[:20]}", needle in blk, f"missing {needle}")


def test_cursor_router_hard_rules_carve_out() -> None:
    t = CURSOR_ROUTER.read_text(encoding="utf-8")
    i = t.find("## 10. Hard Rules (Cursor)")
    check("carve_out", i != -1 and '- The § 1 "Router-exempt inspection skills" path is not a workflow' in t[i:], "carve-out bullet missing")


def test_readme_counts_and_install() -> None:
    n = len(list((PLUGIN_ROOT / "skills").glob("*/SKILL.md")))
    for rd in (README_ROOT, README_PLUGIN):
        t = rd.read_text(encoding="utf-8")
        check(f"no_29_{rd.parent.name}", "29 skill" not in t, "still says 29 skill")
        check(f"count_{rd.parent.name}", f"{n} skill" in t, f"no '{n} skill'")
    check("no_npx_retro", "--skill retro" not in README_PLUGIN.read_text(encoding="utf-8"), "npx retro line present")


def test_symlink_realpath_resolution() -> None:
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        sk = Path(tmp) / "skills"
        sk.mkdir()
        (sk / "retro").symlink_to(PLUGIN_ROOT / "skills" / "retro")
        check("symlink_reaches_script", (sk / "retro" / "SKILL.md").resolve().parents[2].joinpath("scripts", "craftflow_retro.py").exists(), "script not reached")
    with tempfile.TemporaryDirectory() as tmp2:
        sk = Path(tmp2) / "skills"
        sk.mkdir()
        shutil.copytree(PLUGIN_ROOT / "skills" / "retro", sk / "retro")
        check("copy_misses_script_step_c", not (sk / "retro" / "SKILL.md").resolve().parents[2].joinpath("scripts", "craftflow_retro.py").exists(), "copy unexpectedly resolves")
    t = read_skill()
    check("step_c_message", "craftflow_retro.py not found" in t and "install-cursor.sh" in t, "step (c) message missing")


def test_pruning_row_no_speculative_causation() -> None:
    text = read_skill()
    row = next((ln for ln in text.splitlines() if ln.startswith("| 4 |")), "")
    check("pruning_row_states_count",
          "observed compaction count" in text and "compact_occurred" in text,
          "rules must require the observed compaction count and cite the compact_occurred events")
    check("pruning_row_no_causation",
          "MUST NOT assert that memory or context caused or amplified" in text,
          "rules must forbid asserting that memory/context caused or amplified friction")
    check("pruning_row_cause_unclear",
          "pruning" in row
          and "cause unclear from evidence" in text
          and "unless the script's evidence itself shows the cause" in text,
          "pruning proposals must say 'cause unclear from evidence' unless the evidence shows the cause")


def main() -> int:
    test_skill_exists()
    test_frontmatter()
    test_boundary_statement()
    test_signal_ids_in_rubric()
    test_rubric_no_mappable_line()
    test_skill_resolution_order()
    test_steps_present()
    test_install_link_function_hermetic()
    test_install_curl_piped_hint_mentions_retro()
    test_install_loop_links_both_skills()
    test_cursor_router_exemption_block()
    test_cursor_router_hard_rules_carve_out()
    test_readme_counts_and_install()
    test_symlink_realpath_resolution()
    test_pruning_row_no_speculative_causation()
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for e in _errors:
        print(e)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
