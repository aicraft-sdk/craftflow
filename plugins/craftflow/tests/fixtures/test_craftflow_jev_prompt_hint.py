#!/usr/bin/env python3
"""Tests for craftflow_jev_prompt_hint.py (Phase 1: inert skeleton + registration).

Run: python3 tests/fixtures/test_craftflow_jev_prompt_hint.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def run_hook(payload: dict, env: dict) -> tuple[int, str, str]:
    """Run the real craftflow_jev_prompt_hint.py hook as a subprocess.

    Same technique as craftflow_hook_unit_tests.py:57-67's run_hook(), copied
    here (not imported) per the plan's "do not import the monolith" rule.
    `env` fully controls CLAUDE_PROJECT_DIR/CLAUDE_PLUGIN_ROOT/TYPESAFE_API_KEY;
    TYPESAFE_API_KEY is stripped from the inherited environment unless `env`
    re-adds it explicitly.
    """
    merged_env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
    merged_env.update(env)
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_jev_prompt_hint.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged_env,
    )
    return result.returncode, result.stdout.strip(), result.stderr


def _setup(enabled: bool) -> tuple[tempfile.TemporaryDirectory, Path, Path, dict]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    project = root / "project"
    plugin = root / "plugin"
    project.mkdir(parents=True)
    (plugin / "config").mkdir(parents=True)
    cfg = json.loads((PLUGIN_ROOT / "config" / "jev.json").read_text())
    if enabled:
        cfg["enabled"] = True
    (plugin / "config" / "jev.json").write_text(json.dumps(cfg))
    env = {"CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(plugin)}
    return tmp, project, plugin, env


def test_disabled_config_exits_silently_and_writes_nothing() -> None:
    tmp, project, plugin, env = _setup(enabled=False)
    with tmp:
        code, out, err = run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "fix the bug"}, env)
        jev_dir = project / ".craftflow/state/jev"
        log_path = project / ".craftflow/state/craftflow-hook-events.log"
        log_ok = (not log_path.exists()) or ("jev" not in log_path.read_text())
        if (code, out) == (0, "") and not jev_dir.exists() and log_ok:
            ok("disabled config exits silently and writes nothing")
        else:
            fail(
                "disabled-config-silent",
                f"code={code} out={out!r} err={err!r} jev_dir_exists={jev_dir.exists()} log_ok={log_ok}",
            )


def test_corrupt_config_file_logs_config_unparseable_but_stays_silent() -> None:
    tmp, project, plugin, env = _setup(enabled=False)
    with tmp:
        (plugin / "config" / "jev.json").write_text("{not json")
        code, out, err = run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "fix the bug"}, env)
        log_path = project / ".craftflow/state/craftflow-hook-events.log"
        log_text = log_path.read_text() if log_path.exists() else ""
        count = log_text.count('"decision": "config_unparseable"')
        if (code, out) == (0, "") and count == 1:
            ok("corrupt config logs config_unparseable exactly once but stays silent")
        else:
            fail(
                "corrupt-config-logs-once",
                f"code={code} out={out!r} err={err!r} count={count} log={log_text!r}",
            )


def test_enabled_but_no_key_exits_silently_no_network() -> None:
    tmp, project, plugin, env = _setup(enabled=True)
    with tmp:
        env_no_key = dict(env)
        # DD-14 loopback override: if any code path DID reach the network it would hit a
        # refused local port and log jev_call_failed -- the assertion below proves no such
        # line exists, i.e. is_active() correctly short-circuited before any network call.
        env_no_key["CRAFTFLOW_JEV_ENDPOINT"] = "http://127.0.0.1:9/"
        code, out, err = run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "fix the bug"}, env_no_key)
        jev_dir = project / ".craftflow/state/jev"
        log_path = project / ".craftflow/state/craftflow-hook-events.log"
        log_ok = (not log_path.exists()) or ("jev_call_failed" not in log_path.read_text())
        if (code, out) == (0, "") and not jev_dir.exists() and log_ok:
            ok("enabled but no API key exits silently, no network attempted")
        else:
            fail(
                "enabled-no-key-silent",
                f"code={code} out={out!r} err={err!r} jev_dir_exists={jev_dir.exists()} log_ok={log_ok}",
            )


def test_hooks_json_registers_userpromptsubmit_with_5s_timeout() -> None:
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
    entries = hooks["hooks"].get("UserPromptSubmit", [])
    cmds = [h for e in entries for h in e.get("hooks", [])]
    matches = [h for h in cmds if "craftflow_jev_prompt_hint.py" in h.get("command", "") and h.get("timeout") == 5]
    if matches:
        ok("hooks.json registers UserPromptSubmit -> craftflow_jev_prompt_hint.py with timeout 5")
    else:
        fail("hooks-json-registration", f"entries={entries!r}")


def test_contract_doc_discloses_hookeventname_carveout() -> None:
    hooklib = (PLUGIN_ROOT / "scripts" / "craftflow_hooklib.py").read_text()
    literal = hooklib.split("HookEventName = Literal[", 1)[1].split("]", 1)[0]
    members = re.findall(r'"([A-Za-z]+)"', literal)
    doc = (PLUGIN_ROOT / "docs" / "craftflow-event-contract.md").read_text()
    checks = (
        len(members) == 10,
        "UserPromptSubmit" not in members,
        "| `UserPromptSubmit` |" in doc,
        "`UserPromptSubmit` is intentionally absent from `HookEventName`" in doc,
    )
    if all(checks):
        ok("contract doc discloses the HookEventName 11-vs-10 carve-out")
    else:
        fail("contract-doc-carveout", f"checks={checks!r} members={members!r}")


def test_new_modules_are_import_cheap() -> None:
    # DD-12a: selfcheck imports every sibling under a 5s sweep budget; each new module must
    # import in well under 0.5s with no side effects (no files created, nothing on stdout).
    # Discovered by glob, so later phases add modules without touching this test.
    failures = []
    for mod_path in sorted(SCRIPTS.glob("craftflow_jev_*.py")):
        mod = mod_path.stem
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.monotonic()
            env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
            env["PYTHONPATH"] = str(SCRIPTS)
            proc = subprocess.run(
                [sys.executable, "-c", f"import {mod}"],
                cwd=tmp,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            elapsed = time.monotonic() - t0
            listing = os.listdir(tmp)
            if proc.returncode != 0 or proc.stdout != "" or elapsed >= 0.5 or listing != []:
                failures.append((mod, proc.returncode, proc.stdout, proc.stderr, elapsed, listing))
    if not failures:
        ok("all craftflow_jev_*.py modules import cheaply (no side effects, <0.5s)")
    else:
        fail("import-cheap", f"failures={failures!r}")


def main() -> int:
    print("test_craftflow_jev_prompt_hint: running")
    test_disabled_config_exits_silently_and_writes_nothing()
    test_corrupt_config_file_logs_config_unparseable_but_stays_silent()
    test_enabled_but_no_key_exits_silently_no_network()
    test_hooks_json_registers_userpromptsubmit_with_5s_timeout()
    test_contract_doc_discloses_hookeventname_carveout()
    test_new_modules_are_import_cheap()

    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
