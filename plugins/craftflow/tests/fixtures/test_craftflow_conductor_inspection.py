#!/usr/bin/env python3
"""Wiring tests for Conductor-provisioned Cursor agents running the inspection skills
(retro, status, failure-digest) from the workspace copy (SPEC-0033).

Run: python3 tests/fixtures/test_craftflow_conductor_inspection.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PLUGIN_ROOT.parents[3]
PROVISION_TS = REPO_ROOT / "packages/repo-conductor/src/craftflow/provision.ts"
SKILLS = {
    "retro": "craftflow_retro.py",
    "status": "craftflow_status_report.py",
    "failure-digest": "craftflow_learn_scan.py",
}
WS = "tools/craftflow-plugin/plugins/craftflow"
RETRO_FIXTURES = PLUGIN_ROOT / "tests" / "fixtures" / "retro"
CURSOR_ROUTER = PLUGIN_ROOT / "skills" / "cursor-router" / "SKILL.md"
DECOY = "aaaa0004"
STATE_ARG = '--state-dir "<WS_ROOT>/.craftflow/state"'
PROJECT_ARG = '--project "<WS_ROOT>"'
FALLBACK_ALLOWLIST = [
    "craftflow_retro.py",
    "craftflow_retro_signals.py",
    "craftflow_status_report.py",
    "craftflow_hooklib.py",
    "craftflow_learn_scan.py",
]

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


def step1(s: str) -> str:
    text = (PLUGIN_ROOT / "skills" / s / "SKILL.md").read_text(encoding="utf-8")
    return section(frontmatter(text)[1], "Step 1")


def parse_allowlist(source: str) -> "list[str] | None":
    """Names in the INSPECTION_SCRIPTS array of provision.ts source, or None when the declaration does not match."""
    m = re.search(r"INSPECTION_SCRIPTS\s*(?::[^=]*)?=\s*\[(.*?)\]", source, re.S)
    if not m:
        return None
    names = re.findall(r"'(craftflow_[a-z0-9_]+\.py)'", m.group(1))
    return names or None


def allowlist() -> list[str]:
    """Allowlisted script names from provision.ts. The documented 5 are used only when the source file is absent
    (a plugin-only checkout); a source that no longer matches the declaration FAILS instead of falling back."""
    if not PROVISION_TS.exists():
        return list(FALLBACK_ALLOWLIST)
    names = parse_allowlist(PROVISION_TS.read_text(encoding="utf-8"))
    if names is None:
        fail("allowlist_declaration_parses", "INSPECTION_SCRIPTS declaration in provision.ts no longer matches the parser")
        return []
    return names


def extract_oneliner(s: str) -> str:
    for line in step1(s).splitlines():
        if re.match(r'^python3 -c ".*parents\[2\].*" "<SKILL_FILE>"$', line):
            return line
    return ""


def make_ws(wt: Path, s: str, with_scripts: bool) -> None:
    ws = wt / WS
    (ws / "skills").mkdir(parents=True)
    shutil.copytree(PLUGIN_ROOT / "skills" / s, ws / "skills" / s)
    if with_scripts:
        (ws / "scripts").mkdir()
        for name in allowlist():
            shutil.copy(PLUGIN_ROOT / "scripts" / name, ws / "scripts" / name)


def run_oneliner(wt: Path, s: str) -> str:
    cmd = extract_oneliner(s).replace("<SKILL_FILE>", f"{WS}/skills/{s}/SKILL.md")
    r = subprocess.run(["bash", "-c", cmd], cwd=wt, capture_output=True, text=True)
    return r.stdout.strip()


def snapshot(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def copy_fixture(pattern: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for f in RETRO_FIXTURES.iterdir():
        if f.is_file() and pattern in f.name:
            shutil.copy(f, dest / f.name)


def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    return env


def script_cmd(s: str) -> list[str]:
    script = f"{WS}/scripts/{SKILLS[s]}"
    if s == "status":
        return [sys.executable, script, "--all"]
    if s == "retro":
        return [sys.executable, script, "--state-dir", ".craftflow/state", "--latest"]
    return [sys.executable, script, "--state-dir", ".craftflow/state"]


def test_text(s: str) -> None:
    s1 = step1(s)
    ws_skill = f"{WS}/skills/{s}/SKILL.md"
    check(f"ws_fallback_text_{s}", f"`<WS_ROOT>/{ws_skill}`" in s1 and "In Cursor only" in s1,
          "workspace fallback paragraph (anchored to <WS_ROOT>) missing")
    check(f"ws_root_derivation_{s}", "`git rev-parse --show-toplevel`" in s1,
          "Step 1 must derive WS_ROOT with git rev-parse --show-toplevel")
    arg = STATE_ARG if s != "status" else PROJECT_ARG
    check(f"ws_anchored_arg_{s}", arg in s1, f"Step 1 must tell the agent to pass {arg}")
    a = s1.find(f"~/.cursor/skills/{s}/SKILL.md")
    b = s1.find(f"<WS_ROOT>/{ws_skill}")
    c = s1.find("installed_plugins.json")
    check(f"ws_fallback_order_{s}", 0 <= a < b < c, f"a={a} b={b} c={c}")


def test_oneliner(s: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / "wt"
        make_ws(wt, s, True)
        want = str((wt / WS / "scripts" / SKILLS[s]).resolve())
        got = run_oneliner(wt, s)
        check(f"oneliner_resolves_ws_{s}", got == want, f"got={got!r} want={want!r}")
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / "wt"
        make_ws(wt, s, False)
        got = run_oneliner(wt, s)
        check(f"oneliner_misses_without_scripts_{s}", got == "", f"got={got!r}")


def test_run_readonly(s: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp) / "parent"
        copy_fixture("wf-retro-breaker", parent / ".craftflow" / "state" / "workflows")
        wt = parent / "wt"
        make_ws(wt, s, True)
        copy_fixture("wf-retro-clean", wt / ".craftflow" / "state" / "workflows")
        copy_fixture("wf-retro-remfix", wt / ".craftflow" / "state" / "workflows")
        before = (snapshot(wt / ".craftflow"), snapshot(parent / ".craftflow"))
        r = subprocess.run(script_cmd(s), cwd=wt, env=clean_env(), capture_output=True, text=True)
        after = (snapshot(wt / ".craftflow"), snapshot(parent / ".craftflow"))
        check(f"ws_run_readonly_{s}",
              r.returncode == 0 and bool(r.stdout.strip()) and before == after
              and DECOY not in r.stdout and DECOY not in r.stderr,
              f"rc={r.returncode} same={before == after} stderr={r.stderr[:200]}")
        if s == "failure-digest":
            check("ws_run_failure_digest_reads_workspace", clusters(r.stdout) != [],
                  f"failure-digest returned no clusters from the workspace state: {r.stdout[:120]!r}")


def test_status_no_walkup_leak() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp) / "parent"
        copy_fixture("wf-retro-breaker", parent / ".craftflow" / "state" / "workflows")
        wt = parent / "wt"
        make_ws(wt, "status", True)
        (wt / ".craftflow" / "state" / "workflows").mkdir(parents=True)
        r = subprocess.run(script_cmd("status"), cwd=wt, env=clean_env(), capture_output=True, text=True)
        check("status_no_walkup_leak",
              r.returncode == 1 and "No workflows found." in r.stderr
              and DECOY not in r.stdout and DECOY not in r.stderr,
              f"rc={r.returncode} stdout={r.stdout[:100]!r} stderr={r.stderr[:200]!r}")
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp) / "parent"
        copy_fixture("wf-retro-breaker", parent / ".craftflow" / "state" / "workflows")
        wt = parent / "wt"
        make_ws(wt, "status", True)
        r = subprocess.run(script_cmd("status"), cwd=wt, env=clean_env(), capture_output=True, text=True)
        check("status_walkup_leak_control",
              r.returncode == 0 and DECOY in (r.stdout + r.stderr),
              f"rc={r.returncode}: control must show the leak without the pre-created dir")


def clusters(stdout: str) -> list:
    try:
        data = json.loads(stdout)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def git_root(cwd: Path) -> str:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def anchored_cmd(s: str, root: str) -> list[str]:
    """The Step 1 command with <WS_ROOT> substituted, as the skill text instructs for a workspace-copy SCRIPT."""
    script = f"{root}/{WS}/scripts/{SKILLS[s]}"
    state = f"{root}/.craftflow/state"
    if s == "status":
        return [sys.executable, script, "--project", root, "--all"]
    if s == "retro":
        return [sys.executable, script, "--state-dir", state, "--latest"]
    return [sys.executable, script, "--state-dir", state]


def nested_workspace(tmp: str, s: str) -> tuple[Path, Path]:
    parent = Path(tmp) / "parent"
    copy_fixture("wf-retro-breaker", parent / ".craftflow" / "state" / "workflows")
    wt = parent / "wt"
    make_ws(wt, s, True)
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    copy_fixture("wf-retro-clean", wt / ".craftflow" / "state" / "workflows")
    copy_fixture("wf-retro-remfix", wt / ".craftflow" / "state" / "workflows")
    deep = wt / "sub" / "deep"
    deep.mkdir(parents=True)
    return wt, deep


def test_nested_cwd(s: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wt, deep = nested_workspace(tmp, s)
        root = git_root(deep)
        check(f"nested_git_root_{s}", root != "" and Path(root).resolve() == wt.resolve(), f"root={root!r}")
        cmd = extract_oneliner(s).replace("<SKILL_FILE>", f"{root}/{WS}/skills/{s}/SKILL.md")
        got = subprocess.run(["bash", "-c", cmd], cwd=deep, capture_output=True, text=True).stdout.strip()
        want = str((wt / WS / "scripts" / SKILLS[s]).resolve())
        check(f"nested_oneliner_resolves_{s}", got == want, f"got={got!r} want={want!r}")
        r = subprocess.run(anchored_cmd(s, root), cwd=deep, env=clean_env(), capture_output=True, text=True)
        out = r.stdout + r.stderr
        good = r.returncode == 0 and DECOY not in out
        if s == "failure-digest":
            good = good and clusters(r.stdout) != []
        else:
            good = good and ("aaaa0001" in out or "aaaa0002" in out)  # --latest may pick either seeded workflow
        check(f"nested_run_reads_workspace_state_{s}", good, f"rc={r.returncode} out={out[:200]!r}")
        if s == "failure-digest":
            old = subprocess.run([sys.executable, f"{root}/{WS}/scripts/{SKILLS[s]}", "--state-dir", ".craftflow/state"],
                                 cwd=deep, env=clean_env(), capture_output=True, text=True)
            check("nested_relative_state_dir_control_is_empty", clusters(old.stdout) == [] and old.returncode == 0,
                  "control: the old cwd-relative command must silently return [] from a nested dir")


def test_status_project_not_shadowed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp) / "parent"
        copy_fixture("wf-retro-breaker", parent / ".craftflow" / "state" / "workflows")
        wt = parent / "wt"
        make_ws(wt, "status", True)
        subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
        root = git_root(wt)
        # Control first: --project creates the workspace's own state dir (hooklib's own-project mkdir), which would
        # stop the walk-up for any later run.
        bare = subprocess.run(script_cmd("status"), cwd=wt, env=clean_env(), capture_output=True, text=True)
        check("status_without_project_flag_leaks_control", DECOY in (bare.stdout + bare.stderr),
              "control: without --project and without the pre-created dir the walk-up reads the ancestor")
        shutil.rmtree(wt / ".craftflow", ignore_errors=True)
        r = subprocess.run(anchored_cmd("status", root), cwd=wt, env=clean_env(), capture_output=True, text=True)
        check("status_project_flag_blocks_ancestor_leak",
              DECOY not in r.stdout and DECOY not in r.stderr,
              f"rc={r.returncode}: --project must not read an ancestor's workflows when the workspace dir is absent")


def test_allowlist_parser() -> None:
    check("allowlist_parser_rejects_unmatched_source", parse_allowlist("export const OTHER = [];") is None,
          "a source without the declaration must not parse")
    check("allowlist_parser_reads_names",
          parse_allowlist("export const INSPECTION_SCRIPTS = ['craftflow_a.py', 'craftflow_b.py'] as const;")
          == ["craftflow_a.py", "craftflow_b.py"], "names not parsed")


def router_block() -> str:
    text = CURSOR_ROUTER.read_text(encoding="utf-8")
    i = text.find("### Router-exempt inspection skills")
    j = text.find("Route using the first matching signal:", i)
    return text[i:j] if i >= 0 and j > i else ""


def test_router() -> None:
    block = router_block()
    needle = ("read the workspace copy `tools/craftflow-plugin/plugins/craftflow/skills/<skill>/SKILL.md` instead")
    check("router_ws_sentence", needle in block, "workspace-copy sentence missing from § 1")
    kept = ["Only these three skills are exempt", "install-cursor.sh", "overrides § 2"]
    kept += [f"`~/.cursor/skills/{s}/SKILL.md`" for s in SKILLS]
    missing = [n for n in kept if n not in block]
    check("router_needles_kept", bool(block) and not missing, f"missing={missing}")


def import_closure(entries: list[str]) -> set[str]:
    seen: set[str] = set()
    todo = list(entries)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        text = (PLUGIN_ROOT / "scripts" / name).read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*(?:import|from)\s+(craftflow_[a-z0-9_]+)", text, re.M):
            todo.append(m.group(1) + ".py")
    return seen


def test_allowlist() -> None:
    if not PROVISION_TS.exists():
        print("  SKIP: allowlist (no repo-conductor source)")
    else:
        listed = set(allowlist())
        closure = import_closure(list(SKILLS.values()))
        check("allowlist_matches_import_closure", listed == closure,
              f"only_listed={sorted(listed - closure)} only_closure={sorted(closure - listed)}")
    missing = [n for n in allowlist() if not (PLUGIN_ROOT / "scripts" / n).is_file()]
    check("allowlist_files_exist", not missing, f"missing={missing}")


def main() -> int:
    for s in SKILLS:
        test_text(s)
        test_oneliner(s)
        test_run_readonly(s)
        test_nested_cwd(s)
    test_status_no_walkup_leak()
    test_status_project_not_shadowed()
    test_allowlist_parser()
    test_router()
    test_allowlist()
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for e in _errors:
        print(e)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
