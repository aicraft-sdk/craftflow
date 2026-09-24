#!/usr/bin/env python3
"""Live proof driver for the jev-canary manifest's "Session-check round-trip"
scenario (Task 9.1).

Like jev_audit_roundtrip.py, this driver does NOT import or mock any of the
hook modules -- it runs the real craftflow_jev_session_check.py SessionStart
hook (and the real craftflow_jev_setup.py CLI) as subprocesses against the
real TYPESAFE_API_KEY in the environment (required_env in jev-canary.json),
and asserts the "Session-check round-trip" scenario from Phase 9 of
docs/plans/2026-09-23-jev-auto-detect-plan.md:

  Given a temp project + plugin root, config/jev.json starting at
  enabled:false / consent.status:"unset", a real TYPESAFE_API_KEY in env,
  and a synthetic session_id,

  When craftflow_jev_session_check.py is run as a subprocess with a
  SessionStart payload,
  Then exit 0, additionalContext contains <craftflow_jev_consent_request>
  plus the exact recorder commands, and the session-cache file now has
  already_asked_consent:true.

  And when craftflow_jev_setup.py --record-consent granted --config <temp
  config> is then run,
  Then exit 0, consent.status=="granted" on disk.

  And when craftflow_jev_session_check.py is run a SECOND time with the
  same session_id,
  Then exit 0, a REAL canary call succeeds against the real API, the
  session-cache file now has active:true, and additionalContext contains a
  one-line "active this session" note.

  And when run a THIRD time, same session_id,
  Then exit 0, empty additionalContext (DD-8 silence-on-unchanged).

  And when TYPESAFE_API_KEY is swapped for a garbage value and run a FOURTH
  time with a NEW session_id (consent already granted from the shared temp
  config),
  Then exit 0, the canary fails, session-cache active:false with a
  non-empty reason string, and additionalContext contains a one-line
  failure warning.

Exit 0 on success; prints a diagnostic to stderr and exits 1 on any
assertion failure (never raises, so the live harness records a clean FAIL
row instead of a traceback).

Run: python3 plugins/craftflow/tests/live/jev_session_check_roundtrip.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"

# A syntactically key-shaped but never-valid value -- deliberately rejected
# by the real API, matching the SENTINEL_KEY naming convention already used
# in tests/fixtures/test_craftflow_jev_session_check.py.
GARBAGE_API_KEY = "sk-jev-canary-garbage-key-should-always-fail"


def run_hook(payload: dict, env: dict) -> tuple[int, str, str]:
    """Same subprocess technique as jev_audit_roundtrip.py's run_hook() (and
    craftflow_hook_unit_tests.py:57-67 / test_craftflow_jev_prompt_hint.py's
    run_hook() before it) -- copied, not imported, per the plan's "do not
    import the monolith" rule.

    `timeout=15` mirrors jev_audit_roundtrip.py's own budget: comfortably
    above the session-check hook's SESSION_CHECK_TOTAL_BUDGET_SECONDS=2.0
    call budget plus process startup, well below the 900s live-harness
    ceiling."""
    merged_env = {**os.environ, **env}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_jev_session_check.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=15,
    )
    return result.returncode, result.stdout.strip(), result.stderr


def run_setup_cli(args: list[str], env: dict) -> tuple[int, str, str]:
    """Same subprocess technique as run_hook() above, targeting the real
    craftflow_jev_setup.py CLI instead of a hook. DD-12b: this CLI reads no
    stdin, so an explicit empty `input=""` is passed (never inherits this
    driver's own stdin)."""
    merged_env = {**os.environ, **env}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_jev_setup.py"), *args],
        input="",
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=15,
    )
    return result.returncode, result.stdout.strip(), result.stderr


def _additional_context(stdout: str) -> Optional[str]:
    """Extracts hookSpecificOutput.additionalContext from a hook's stdout,
    matching craftflow_hooklib.session_context()'s json_print() shape. None
    when stdout is empty (main() never called session_context()) or is not
    the expected JSON shape."""
    if not stdout:
        return None
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload.get("hookSpecificOutput", {}).get("additionalContext")


def _session_cache_path(project: Path, session_id: str) -> Path:
    """Mirrors craftflow_jev_session_cache.session_cache_path() exactly --
    reimplemented locally (not imported) to keep this driver self-contained,
    matching jev_audit_roundtrip.py's precedent of reading state files by
    known path rather than importing any script module."""
    digest = hashlib.sha256((session_id or "").encode("utf-8")).hexdigest()[:16]
    return project / ".craftflow" / "state" / "jev" / "sessions" / f"{digest}.json"


def _read_session_cache(project: Path, session_id: str) -> Optional[Dict[str, Any]]:
    path = _session_cache_path(project, session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _check_first_firing(
    code: int, out: str, err: str, project: Path, session_id: str, setup_script_path: str
) -> List[str]:
    failures: List[str] = []
    if code != 0:
        failures.append(f"step1: exit code {code} != 0 (stderr={err!r})")
        return failures
    ctx = _additional_context(out)
    if not ctx:
        failures.append(f"step1: no additionalContext in stdout (stdout={out!r})")
        return failures
    if "<craftflow_jev_consent_request>" not in ctx:
        failures.append("step1: consent-request tag missing from additionalContext")
    granted_cmd = f'python3 "{setup_script_path}" --record-consent granted'
    declined_cmd = f'python3 "{setup_script_path}" --record-consent declined'
    if granted_cmd not in ctx:
        failures.append(f"step1: exact granted recorder command missing: {granted_cmd!r}")
    if declined_cmd not in ctx:
        failures.append(f"step1: exact declined recorder command missing: {declined_cmd!r}")
    cache = _read_session_cache(project, session_id)
    if not cache or cache.get("already_asked_consent") is not True:
        failures.append(f"step1: session cache already_asked_consent != true: {cache!r}")
    return failures


def _check_record_consent(code: int, out: str, err: str, config_path: Path) -> List[str]:
    failures: List[str] = []
    if code != 0:
        failures.append(f"step2: --record-consent exit {code} != 0 (stderr={err!r})")
        return failures
    if "consent: granted" not in out:
        failures.append(f"step2: unexpected stdout: {out!r}")
    try:
        cfg_after = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        failures.append(f"step2: could not read config after write: {exc}")
        return failures
    if cfg_after.get("consent", {}).get("status") != "granted":
        failures.append(f"step2: consent.status != granted on disk: {cfg_after.get('consent')!r}")
    return failures


def _check_canary_activation(code: int, out: str, err: str, project: Path, session_id: str) -> List[str]:
    failures: List[str] = []
    if code != 0:
        failures.append(f"step3: exit code {code} != 0 (stderr={err!r})")
        return failures
    ctx = _additional_context(out)
    if not ctx:
        failures.append(f"step3: no additionalContext in stdout (expected activation note; stdout={out!r})")
    elif "active this session" not in ctx:
        failures.append(f"step3: activation note missing expected text: {ctx!r}")
    cache = _read_session_cache(project, session_id)
    if not cache or cache.get("active") is not True:
        failures.append(f"step3: session cache active != true after real canary call: {cache!r}")
    return failures


def _check_silent_unchanged(code: int, out: str, err: str, project: Path, session_id: str) -> List[str]:
    failures: List[str] = []
    if code != 0:
        failures.append(f"step4: exit code {code} != 0 (stderr={err!r})")
        return failures
    if out != "":
        failures.append(f"step4: expected empty stdout (DD-8 silence on unchanged), got: {out!r}")
    cache = _read_session_cache(project, session_id)
    if not cache or cache.get("active") is not True:
        failures.append(f"step4: session cache active != true (unexpected state change): {cache!r}")
    return failures


def _check_garbage_key_failure(code: int, out: str, err: str, project: Path, session_id: str) -> List[str]:
    failures: List[str] = []
    if code != 0:
        failures.append(f"step5: exit code {code} != 0 (stderr={err!r})")
        return failures
    ctx = _additional_context(out)
    if not ctx:
        failures.append(f"step5: no additionalContext in stdout (expected failure warning; stdout={out!r})")
    else:
        if "inactive this session" not in ctx:
            failures.append(f"step5: failure warning missing 'inactive this session': {ctx!r}")
        if "canary failed" not in ctx:
            failures.append(f"step5: failure warning missing 'canary failed': {ctx!r}")
    cache = _read_session_cache(project, session_id)
    if not cache or cache.get("active") is not False:
        failures.append(f"step5: session cache active != false with garbage key: {cache!r}")
    elif not cache.get("reason"):
        failures.append(f"step5: session cache reason is empty despite failure: {cache!r}")
    return failures


def main() -> int:
    try:
        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            print("SKIP: TYPESAFE_API_KEY not set", file=sys.stderr)
            return 1

        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            project = root / "project"
            plugin = root / "plugin"
            project.mkdir(parents=True)
            (plugin / "config").mkdir(parents=True)
            (plugin / "skills").mkdir(parents=True)

            cfg = json.loads((PLUGIN_ROOT / "config" / "jev.json").read_text())
            cfg["enabled"] = False
            cfg["consent"] = {"status": "unset", "ts": None}
            config_path = plugin / "config" / "jev.json"
            config_path.write_text(json.dumps(cfg))

            env_base = {"CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(plugin)}
            setup_script_path = str(plugin / "scripts" / "craftflow_jev_setup.py")
            session_a = f"jev-canary-{uuid.uuid4().hex[:12]}"
            session_b = f"jev-canary-{uuid.uuid4().hex[:12]}"

            # Step 1: first SessionStart firing -- consent-request injected.
            try:
                code, out, err = run_hook(
                    {"hook_event_name": "SessionStart", "session_id": session_a, "source": "startup"},
                    {**env_base, "TYPESAFE_API_KEY": api_key},
                )
            except subprocess.TimeoutExpired:
                print("FAIL: step 1 (first firing) hook did not exit within 15s", file=sys.stderr)
                return 1
            failures = _check_first_firing(code, out, err, project, session_a, setup_script_path)
            if failures:
                print("FAIL: " + "; ".join(failures), file=sys.stderr)
                return 1

            # Step 2: record consent via the real setup CLI.
            try:
                code, out, err = run_setup_cli(
                    ["--record-consent", "granted", "--config", str(config_path)], env_base
                )
            except subprocess.TimeoutExpired:
                print("FAIL: step 2 (--record-consent) did not exit within 15s", file=sys.stderr)
                return 1
            failures = _check_record_consent(code, out, err, config_path)
            if failures:
                print("FAIL: " + "; ".join(failures), file=sys.stderr)
                return 1

            # Step 3: second firing, same session -- real canary call activates.
            try:
                code, out, err = run_hook(
                    {"hook_event_name": "SessionStart", "session_id": session_a, "source": "startup"},
                    {**env_base, "TYPESAFE_API_KEY": api_key},
                )
            except subprocess.TimeoutExpired:
                print("FAIL: step 3 (second firing) hook did not exit within 15s", file=sys.stderr)
                return 1
            failures = _check_canary_activation(code, out, err, project, session_a)
            if failures:
                print("FAIL: " + "; ".join(failures), file=sys.stderr)
                return 1

            # Step 4: third firing, same session -- silent (DD-8, no state change).
            try:
                code, out, err = run_hook(
                    {"hook_event_name": "SessionStart", "session_id": session_a, "source": "startup"},
                    {**env_base, "TYPESAFE_API_KEY": api_key},
                )
            except subprocess.TimeoutExpired:
                print("FAIL: step 4 (third firing) hook did not exit within 15s", file=sys.stderr)
                return 1
            failures = _check_silent_unchanged(code, out, err, project, session_a)
            if failures:
                print("FAIL: " + "; ".join(failures), file=sys.stderr)
                return 1

            # Step 5: fourth firing, NEW session, garbage key -- inactive + warning.
            try:
                code, out, err = run_hook(
                    {"hook_event_name": "SessionStart", "session_id": session_b, "source": "startup"},
                    {**env_base, "TYPESAFE_API_KEY": GARBAGE_API_KEY},
                )
            except subprocess.TimeoutExpired:
                print(
                    "FAIL: step 5 (fourth firing, garbage key) hook did not exit within 15s",
                    file=sys.stderr,
                )
                return 1
            failures = _check_garbage_key_failure(code, out, err, project, session_b)
            if failures:
                print("FAIL: " + "; ".join(failures), file=sys.stderr)
                return 1

            print(
                "OK: session-check round-trip against the real API "
                "(consent-request -> granted -> canary active -> silent repeat -> canary failure; "
                f"session_a={session_a}, session_b={session_b})"
            )
            return 0
    except Exception as exc:
        print(f"FAIL: unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
