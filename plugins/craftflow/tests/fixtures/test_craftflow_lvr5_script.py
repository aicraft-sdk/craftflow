#!/usr/bin/env python3
"""Hermetic tests for tests/live/lvr5-cursor-e2e.sh (bash AND zsh).

Everything runs against a scratch HOME, a fake cursor-router symlink, a fake
cursor-wf.json and a scratch git repo. The real ~/.cursor is never touched.

Run: python3 tests/fixtures/test_craftflow_lvr5_script.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN_ROOT / "tests" / "live" / "lvr5-cursor-e2e.sh"
SHELLS = [s for s in ("bash", "zsh") if shutil.which(s)]

_passes = 0
_errors: list[str] = []


def check(name: str, cond: bool, reason: str) -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason}")
        print(f"  FAIL: {name}: {reason}")


class Sandbox:
    """Scratch HOME + fake router + scratch repo standing in for the main checkout."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="lvr5-test-"))
        self.home = self.root / "home"
        self.old = self.root / "old-router"
        self.wt = self.root / "wt"
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        skills = self.home / ".cursor" / "skills"
        skills.mkdir(parents=True)
        self.old.mkdir()
        os.symlink(self.old, skills / "cursor-router")
        pw = self.wt / "tools/craftflow-plugin/plugins/craftflow/skills"
        (pw / "cursor-router").mkdir(parents=True)
        (pw / "retro").mkdir(parents=True)
        (self.repo / ".craftflow/state/workflows").mkdir(parents=True)
        (self.repo / ".craftflow/state/cursor-wf.json").write_text("{}")
        self.state.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        self.skills = skills

    def run(self, shell: str, body: str, extra_env: dict[str, str] | None = None):
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "WT": str(self.wt),
            "LVR5_TMP": str(self.state),
        }
        env.update(extra_env or {})
        return subprocess.run(
            [shell, "-c", f'source "{SCRIPT}"\n{body}'],
            cwd=self.repo, env=env, capture_output=True, text=True, timeout=60,
        )

    def router(self) -> str:
        return os.readlink(self.skills / "cursor-router")

    def retro_present(self) -> bool:
        return (self.skills / "retro").exists() or (self.skills / "retro").is_symlink()

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def with_sandbox(fn):
    def wrapper(shell: str) -> None:
        sb = Sandbox()
        try:
            fn(shell, sb)
        finally:
            sb.cleanup()
    return wrapper


@with_sandbox
def case_happy_path(shell: str, sb: Sandbox) -> None:
    r = sb.run(shell, 'lvr5_setup; lvr5_check; lvr5_restore; echo "rc=$?"')
    out = r.stdout
    n = f"happy[{shell}]"
    check(f"{n}_markers",
          all(m in out for m in ("LV-R5 setup: OK", "LV-R5 read-only: OK", "LV-R5 restore: OK")),
          f"missing marker; out={out!r} err={r.stderr!r}")
    check(f"{n}_rc0", "rc=0" in out, f"restore rc not 0; out={out!r}")
    check(f"{n}_restore_target_echo", f"restore target: {sb.old}" in out, f"out={out!r}")
    check(f"{n}_link_recovered", sb.router() == str(sb.old) and not sb.retro_present(),
          f"router={sb.router()} retro={sb.retro_present()}")
    check(f"{n}_no_noise", r.stderr == "", f"stderr={r.stderr!r}")
    check(f"{n}_state_cleaned", sorted(p.name for p in sb.state.iterdir()) == [],
          f"leftovers={sorted(p.name for p in sb.state.iterdir())}")


@with_sandbox
def case_lost_variable(shell: str, sb: Sandbox) -> None:
    n = f"lost_var[{shell}]"
    r1 = sb.run(shell, "lvr5_setup")
    check(f"{n}_setup_ok", "LV-R5 setup: OK" in r1.stdout, f"out={r1.stdout!r}")
    state_file = sb.state / "lvr5-old-router"
    check(f"{n}_state_persisted", state_file.is_file() and state_file.read_text().strip() == str(sb.old),
          f"state file missing/wrong: {state_file}")
    check(f"{n}_linked_to_worktree", sb.router() != str(sb.old) and sb.retro_present(),
          "setup did not relink")
    # fresh shell: variable lost, script re-sourced
    r2 = sb.run(shell, 'test -f "$LVR5_TMP/lvr5-old-router" && echo "state-survives-source"; lvr5_restore; echo "rc=$?"')
    check(f"{n}_source_keeps_state", "state-survives-source" in r2.stdout, f"out={r2.stdout!r}")
    check(f"{n}_restored_from_file",
          "LV-R5 restore: OK" in r2.stdout and "rc=0" in r2.stdout and sb.router() == str(sb.old)
          and not sb.retro_present(),
          f"out={r2.stdout!r} err={r2.stderr!r} router={sb.router()}")
    check(f"{n}_state_cleaned_after_restore", not state_file.exists(), "state file still present")


@with_sandbox
def case_source_keeps_nonempty_var(shell: str, sb: Sandbox) -> None:
    r = sb.run(shell, 'echo "OLD=$OLD_ROUTER"', extra_env={"OLD_ROUTER": "/keep/me"})
    check(f"keep_var[{shell}]", "OLD=/keep/me" in r.stdout, f"out={r.stdout!r}")


@with_sandbox
def case_restore_without_state(shell: str, sb: Sandbox) -> None:
    n = f"no_state[{shell}]"
    before = (sb.router(), sb.retro_present())
    r = sb.run(shell, 'lvr5_restore; echo "rc=$?"')
    check(f"{n}_refuses", "STOP:" in r.stdout and "rc=1" in r.stdout, f"out={r.stdout!r}")
    check(f"{n}_changes_nothing", (sb.router(), sb.retro_present()) == before, "state changed")
    check(f"{n}_no_ok_marker", "LV-R5 restore: OK" not in r.stdout, f"out={r.stdout!r}")


@with_sandbox
def case_restore_without_snapshots(shell: str, sb: Sandbox) -> None:
    n = f"no_snapshots[{shell}]"
    sb.run(shell, "lvr5_setup")
    for p in sb.state.glob("lvr5-*-before"):
        p.unlink()
    r = sb.run(shell, 'lvr5_restore; echo "rc=$?"')
    check(f"{n}_rc0_quiet", "rc=0" in r.stdout and r.stderr == "" and sb.router() == str(sb.old),
          f"out={r.stdout!r} err={r.stderr!r}")


@with_sandbox
def case_check_without_setup(shell: str, sb: Sandbox) -> None:
    n = f"check_no_setup[{shell}]"
    r = sb.run(shell, 'lvr5_check; echo "rc=$?"')
    check(f"{n}_stop_line", "STOP: run lvr5_setup first (snapshot files missing)" in r.stdout
          and "rc=1" in r.stdout, f"out={r.stdout!r}")
    check(f"{n}_no_diff_noise", "No such file" not in r.stdout + r.stderr, f"err={r.stderr!r}")


def case_syntax() -> None:
    for shell in SHELLS:
        r = subprocess.run([shell, "-n", str(SCRIPT)], capture_output=True, text=True)
        check(f"syntax[{shell} -n]", r.returncode == 0, r.stderr)
    text = SCRIPT.read_text()
    check("no_exit_statement",
          not any(ln.strip().startswith("exit") for ln in text.splitlines() if not ln.strip().startswith("#")),
          "script must use return, never exit")


def main() -> int:
    check("shells_available", "bash" in SHELLS, "bash missing")
    case_syntax()
    for shell in SHELLS:
        case_happy_path(shell)
        case_lost_variable(shell)
        case_source_keeps_nonempty_var(shell)
        case_restore_without_state(shell)
        case_restore_without_snapshots(shell)
        case_check_without_setup(shell)
    print(f"\n{_passes} passed, {len(_errors)} failed (shells: {', '.join(SHELLS)})")
    for e in _errors:
        print(e)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
