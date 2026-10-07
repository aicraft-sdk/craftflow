#!/usr/bin/env python3
"""Tests for craftflow:retro v2 (read-only recurrence, LV-R4 widening).

Run: python3 tests/fixtures/test_craftflow_retro_v2.py
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
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

SCRIPT = SCRIPTS / "craftflow_retro.py"
FIXTURES = Path(__file__).resolve().parent

CLEAN = "wf-retro-clean-20261006-000000-aaaa0001"
REMFIX = "wf-retro-remfix-20261006-000000-aaaa0002"
ALL = "wf-retro-all-20261006-000000-aaaa0003"
BREAKER = "wf-retro-breaker-20261006-000000-aaaa0004"
NEAR = "wf-retro-near-20261006-000000-aaaa0005"

SKILL = PLUGIN_ROOT / "skills" / "retro" / "SKILL.md"
INSTALL = PLUGIN_ROOT / "install-cursor.sh"
MANIFEST = PLUGIN_ROOT / "tests" / "live" / "manifests" / "retro.json"
README_PLUGIN = PLUGIN_ROOT / "README.md"

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def check(name: str, cond: bool, reason: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, reason)


def run_cli(args: list, state_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--state-dir", str(state_dir)],
        capture_output=True,
        text=True,
    )


def materialize(tmp: Path, names: list) -> Path:
    wf = tmp / ".craftflow" / "state" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    for n in names:
        for suffix in (".json", ".events.jsonl"):
            shutil.copy(FIXTURES / "retro" / (n + suffix), wf / (n + suffix))
    return tmp / ".craftflow" / "state"


def write_wf(state: Path, name: str, artifact, events_text) -> None:
    wf = state / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    if artifact is not None:
        text = artifact if isinstance(artifact, str) else json.dumps(artifact)
        (wf / (name + ".json")).write_text(text)
    if events_text is not None:
        (wf / (name + ".events.jsonl")).write_text(events_text)


def base_artifact(name: str) -> dict:
    art = json.loads((FIXTURES / "retro" / (CLEAN + ".json")).read_text())
    art["workflow_uuid"] = name
    art["workflow_id"] = name
    return art


def expect_error(name: str, proc: subprocess.CompletedProcess, code: str, extra: str = "") -> None:
    err = proc.stderr.strip()
    good = (
        proc.returncode == 1
        and proc.stdout == ""
        and err.startswith(f"ERROR: {code}:")
        and "\n" not in err
        and extra in err
    )
    check(name, good, f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}")


def _snapshot(root: Path) -> list:
    snap = []
    for dirpath, dirnames, filenames in os.walk(root):
        for n in sorted(dirnames + filenames):
            p = Path(dirpath) / n
            st = p.stat()
            snap.append((str(p.relative_to(root)), st.st_size, st.st_mtime_ns))
    return sorted(snap)


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


# --- Phase 2: LV-R4 widened (SPEC-0032 FR-006) ---------------------------------


def test_manifest_valid_for_runner() -> None:
    import craftflow_live_harness_runner as runner

    m = runner.load_manifest(MANIFEST)
    runner.ensure_steps("scenarios", m["scenarios"], require_scenario_fields=True)
    runner.ensure_steps("healthcheck", m["healthcheck"])
    ok("manifest_valid_for_runner")


def test_lvr4_links() -> None:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sc = [s for s in m["scenarios"] if s["name"].startswith("LV-R4")]
    check("lvr4_present", len(sc) == 1, f"found {len(sc)} LV-R4 scenarios")
    if len(sc) != 1:
        return
    got_m = re.search(r"for n in ([^;]*); do", sc[0]["command"])
    want_m = re.search(r"^\s*for SKILL_NAME in ([^;\n]*); do", INSTALL.read_text(encoding="utf-8"), re.M)
    got = got_m.group(1).split() if got_m else None
    want = want_m.group(1).split() if want_m else None
    check("lvr4_links_match_installer", got is not None and got == want, f"manifest={got} installer={want}")
    text = sc[0]["name"] + " " + sc[0]["then"]
    check("lvr4_text_names_four", all(n in text for n in ("retro", "status", "failure-digest")),
          f"name/then lack a skill name: {text!r}")


def main() -> int:
    tests = [
        test_manifest_valid_for_runner,
        test_lvr4_links,
    ]
    for t in tests:
        try:
            t()
        except Exception as exc:  # a crashing test is a failing test, never a silent pass
            fail(t.__name__, f"{type(exc).__name__}: {exc}")
    print(f"\nResults: {_passes} passed, {len(_errors)} failed")
    for e in _errors:
        print(e)
    print("PASS" if not _errors else "FAIL")
    return 0 if not _errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
