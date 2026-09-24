#!/usr/bin/env python3
"""Tests for craftflow_jev_session_check.py.

Run: python3 tests/fixtures/test_craftflow_jev_session_check.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_session_check import main, CONSENT_REQUEST_TEMPLATE  # noqa: E402
from craftflow_jev_session_cache import read_session_status  # noqa: E402
from craftflow_jev_setup import privacy_note  # noqa: E402
from craftflow_jev_config import DEFAULTS  # noqa: E402

_passes = 0
_errors: list[str] = []

SENTINEL_KEY = "sk-jev-session-check-test-key-should-never-leak"


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


@contextlib.contextmanager
def _env(overrides: dict):
    """Temporarily set/delete os.environ entries (None value == delete)."""
    _missing = object()
    saved = {}
    for key, value in overrides.items():
        saved[key] = os.environ.get(key, _missing)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is _missing:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _setup(*, cfg_overrides: dict | None = None):
    """Create a temp project/plugin dir pair, optionally writing a jev.json
    config with `cfg_overrides` merged over DEFAULTS. Returns
    (tmp, project, plugin, sessions_dir)."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    project = root / "project"
    plugin = root / "plugin"
    project.mkdir(parents=True)
    (plugin / "config").mkdir(parents=True)
    if cfg_overrides is not None:
        cfg = json.loads(json.dumps(DEFAULTS))
        cfg.update(cfg_overrides)
        (plugin / "config" / "jev.json").write_text(json.dumps(cfg), encoding="utf-8")
    sessions_dir = project / ".craftflow" / "state" / "jev" / "sessions"
    return tmp, project, plugin, sessions_dir


def _run_main(payload: dict | None, env_overrides: dict) -> tuple:
    """Feed `payload` as stdin JSON, run main() with os.environ overrides
    applied on top of the current environment, and capture stdout."""
    text = "" if payload is None else json.dumps(payload)
    out = io.StringIO()
    with _env(env_overrides):
        with mock.patch("sys.stdin", io.StringIO(text)):
            with contextlib.redirect_stdout(out):
                code = main()
    return code, out.getvalue()


# ---------------------------------------------------------------------------
# Task 5.1: no-op branches + hooks.json registration
# ---------------------------------------------------------------------------


def test_no_key_is_silent_noop() -> None:
    tmp, project, plugin, sessions_dir = _setup()
    with tmp:
        code, out = _run_main(
            {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"},
            {"CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(plugin), "TYPESAFE_API_KEY": None},
        )
    if code == 0 and out == "" and not sessions_dir.exists():
        ok("no TYPESAFE_API_KEY: silent no-op, no session-cache write")
    else:
        fail("no-key-noop", f"code={code} out={out!r} sessions_dir_exists={sessions_dir.exists()}")


def test_enabled_true_is_silent_noop_manual_path_untouched() -> None:
    tmp, project, plugin, sessions_dir = _setup(
        cfg_overrides={"enabled": True, "consent": {"status": "unset", "ts": None}}
    )
    with tmp:
        code, out = _run_main(
            {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"},
            {"CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(plugin), "TYPESAFE_API_KEY": SENTINEL_KEY},
        )
    if code == 0 and out == "" and not sessions_dir.exists():
        ok("enabled:true is a silent no-op (manual path untouched, does not even react to consent:unset)")
    else:
        fail("enabled-true-noop", f"code={code} out={out!r} sessions_dir_exists={sessions_dir.exists()}")


def test_consent_declined_is_silent_noop() -> None:
    tmp, project, plugin, sessions_dir = _setup(
        cfg_overrides={"enabled": False, "consent": {"status": "declined", "ts": "2026-01-01T00:00:00Z"}}
    )
    with tmp:
        code, out = _run_main(
            {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"},
            {"CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(plugin), "TYPESAFE_API_KEY": SENTINEL_KEY},
        )
    if code == 0 and out == "" and not sessions_dir.exists():
        ok("consent.status=declined is a total silent no-op")
    else:
        fail("consent-declined-noop", f"code={code} out={out!r} sessions_dir_exists={sessions_dir.exists()}")


def test_no_session_id_is_silent_noop() -> None:
    tmp, project, plugin, sessions_dir = _setup()
    with tmp:
        code, out = _run_main(
            {"hook_event_name": "SessionStart", "source": "startup"},
            {"CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(plugin), "TYPESAFE_API_KEY": SENTINEL_KEY},
        )
    if code == 0 and out == "" and not sessions_dir.exists():
        ok("missing session_id is a silent no-op")
    else:
        fail("no-session-id-noop", f"code={code} out={out!r} sessions_dir_exists={sessions_dir.exists()}")


def test_hooks_json_registers_sessionstart_jev_session_check_with_timeout_5() -> None:
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
    entries = hooks["hooks"].get("SessionStart", [])
    cmds = [h for e in entries for h in e.get("hooks", [])]
    matches = [
        h for h in cmds
        if "craftflow_jev_session_check.py" in h.get("command", "") and h.get("timeout") == 5
    ]
    matcher_ok = any(
        "craftflow_jev_session_check.py" in h.get("command", "")
        for e in entries if e.get("matcher") == "startup|resume|compact"
        for h in e.get("hooks", [])
    )
    if matches and matcher_ok:
        ok("hooks.json registers a SessionStart entry for craftflow_jev_session_check.py, timeout=5, matcher startup|resume|compact")
    else:
        fail("hooks-json-registration", f"entries={entries!r}")


# ---------------------------------------------------------------------------
# Task 5.3/5.4: consent-ask branch (idempotent per session, P1)
# ---------------------------------------------------------------------------


def test_consent_unset_first_time_injects_consent_request_with_exact_recorder_commands() -> None:
    tmp, project, plugin, sessions_dir = _setup(
        cfg_overrides={"enabled": False, "consent": {"status": "unset", "ts": None}}
    )
    with tmp:
        code, out = _run_main(
            {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"},
            {"CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(plugin), "TYPESAFE_API_KEY": SENTINEL_KEY},
        )
        payload = json.loads(out) if out.strip() else None
    context = (
        payload.get("hookSpecificOutput", {}).get("additionalContext", "")
        if isinstance(payload, dict)
        else ""
    )
    expected_note = privacy_note(DEFAULTS["maxStateChars"])
    checks = (
        code == 0,
        "<craftflow_jev_consent_request>" in context,
        "AskUserQuestion" in context,
        "--record-consent granted" in context,
        "--record-consent declined" in context,
        expected_note in context,
        "next" in context and "session" in context,
        SENTINEL_KEY not in context,
    )
    if all(checks):
        ok("consent.status=unset first firing injects the consent-request contract with exact recorder commands")
    else:
        fail(
            "consent-unset-first-time-injects",
            f"code={code} out={out!r} checks={checks!r}",
        )


def test_consent_unset_second_sessionstart_same_session_does_not_reask() -> None:
    tmp, project, plugin, sessions_dir = _setup(
        cfg_overrides={"enabled": False, "consent": {"status": "unset", "ts": None}}
    )
    with tmp:
        env = {"CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(plugin), "TYPESAFE_API_KEY": SENTINEL_KEY}
        payload = {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"}
        code1, out1 = _run_main(payload, env)
        code2, out2 = _run_main(payload, env)
    if code1 == 0 and out1.strip() != "" and code2 == 0 and out2 == "":
        ok("a second SessionStart firing for the SAME session never re-asks (P1)")
    else:
        fail(
            "consent-unset-no-reask",
            f"code1={code1} out1={out1!r} code2={code2} out2={out2!r}",
        )


def test_consent_unset_writes_already_asked_flag_before_any_response() -> None:
    tmp, project, plugin, sessions_dir = _setup(
        cfg_overrides={"enabled": False, "consent": {"status": "unset", "ts": None}}
    )
    with tmp:
        code, out = _run_main(
            {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"},
            {"CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(plugin), "TYPESAFE_API_KEY": SENTINEL_KEY},
        )
        entry = read_session_status(project / ".craftflow" / "state", "s1")
    if code == 0 and entry is not None and entry.get("already_asked_consent") is True:
        ok("consent-ask writes already_asked_consent:true structurally (DD-4)")
    else:
        fail("consent-unset-writes-already-asked-flag", f"code={code} out={out!r} entry={entry!r}")


# ---------------------------------------------------------------------------
# Task 5.5/5.6: canary branch -- 5-way failure taxonomy + budget +
# change-only notification (DD-8)
# ---------------------------------------------------------------------------


def _granted_env(project: Path, plugin: Path) -> dict:
    return {"CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(plugin), "TYPESAFE_API_KEY": SENTINEL_KEY}


def test_consent_granted_canary_success_first_run_writes_active_and_injects_note() -> None:
    tmp, project, plugin, sessions_dir = _setup(
        cfg_overrides={"enabled": False, "consent": {"status": "granted", "ts": "2026-01-01T00:00:00Z"}}
    )
    with tmp:
        fake_result = {"answers": {"ok": True}, "usage": {}, "model": "jev-latest", "latency_ms": 5, "cache_hit": False}
        with mock.patch("craftflow_jev_session_check.jev_call", return_value=fake_result):
            code, out = _run_main(
                {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"},
                _granted_env(project, plugin),
            )
        entry = read_session_status(project / ".craftflow" / "state", "s1")
    payload = json.loads(out) if out.strip() else None
    context = payload.get("hookSpecificOutput", {}).get("additionalContext", "") if payload else ""
    if (
        code == 0
        and entry is not None
        and entry.get("active") is True
        and "active this session" in context
    ):
        ok("consent.status=granted first firing runs the canary, writes active:true, and injects a note")
    else:
        fail(
            "canary-success-first-run",
            f"code={code} out={out!r} entry={entry!r}",
        )


def test_consent_granted_canary_success_second_identical_run_stays_silent() -> None:
    tmp, project, plugin, sessions_dir = _setup(
        cfg_overrides={"enabled": False, "consent": {"status": "granted", "ts": "2026-01-01T00:00:00Z"}}
    )
    with tmp:
        fake_result = {"answers": {"ok": True}, "usage": {}, "model": "jev-latest", "latency_ms": 5, "cache_hit": False}
        env = _granted_env(project, plugin)
        payload = {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"}
        with mock.patch("craftflow_jev_session_check.jev_call", return_value=fake_result):
            code1, out1 = _run_main(payload, env)
            code2, out2 = _run_main(payload, env)
    if code1 == 0 and out1.strip() != "" and code2 == 0 and out2 == "":
        ok("DD-8: an unchanged healthy canary result across two firings stays silent on the second firing")
    else:
        fail(
            "canary-success-second-run-silent",
            f"code1={code1} out1={out1!r} code2={code2} out2={out2!r}",
        )


def test_consent_granted_canary_failure_variants() -> None:
    import socket
    import urllib.error

    def _http_error(status):
        def _raise(*args, **kwargs):
            raise urllib.error.HTTPError("http://x", status, "err", {}, None)
        return _raise

    def _url_error(*args, **kwargs):
        raise urllib.error.URLError("boom")

    def _socket_timeout(*args, **kwargs):
        raise socket.timeout("timed out")

    def _value_error(*args, **kwargs):
        raise ValueError("malformed response")

    variants = {
        "http_401": _http_error(401),
        "http_429": _http_error(429),
        "http_529": _http_error(529),
        "url_error": _url_error,
        "socket_timeout": _socket_timeout,
        "value_error": _value_error,
    }
    failures = []
    for name, side_effect in variants.items():
        tmp, project, plugin, sessions_dir = _setup(
            cfg_overrides={"enabled": False, "consent": {"status": "granted", "ts": "2026-01-01T00:00:00Z"}}
        )
        with tmp:

            def _fake_call(*args, failure_reason_out=None, **kwargs):
                try:
                    side_effect()
                except Exception as exc:
                    if failure_reason_out is not None:
                        failure_reason_out["error"] = type(exc).__name__
                        failure_reason_out["status"] = exc.code if isinstance(exc, urllib.error.HTTPError) else None
                    return None

            with mock.patch("craftflow_jev_session_check.jev_call", side_effect=_fake_call):
                code, out = _run_main(
                    {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"},
                    _granted_env(project, plugin),
                )
            entry = read_session_status(project / ".craftflow" / "state", "s1")
        if not (
            code == 0
            and entry is not None
            and entry.get("active") is False
            and entry.get("reason")
        ):
            failures.append((name, code, out, entry))
    if not failures:
        ok("all 5 canary failure variants (401/429/529/URLError/socket.timeout/ValueError) write active:false with a non-crashing reason")
    else:
        fail("canary-failure-variants", f"failures={failures!r}")


def test_canary_uses_own_short_budget_not_default_4s() -> None:
    tmp, project, plugin, sessions_dir = _setup(
        cfg_overrides={"enabled": False, "consent": {"status": "granted", "ts": "2026-01-01T00:00:00Z"}}
    )
    captured = {}

    def _capture(*args, **kwargs):
        captured.update(kwargs)
        return {"answers": {"ok": True}, "usage": {}, "model": "jev-latest", "latency_ms": 5, "cache_hit": False}

    with tmp:
        with mock.patch("craftflow_jev_session_check.jev_call", side_effect=_capture):
            _run_main(
                {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"},
                _granted_env(project, plugin),
            )
    if captured.get("total_budget_seconds") == 2.0 and captured.get("timeout", 999) <= 2.0:
        ok("the canary call uses its own SESSION_CHECK_TOTAL_BUDGET_SECONDS=2.0, not the unrelated 4.0s default")
    else:
        fail("canary-own-budget", f"captured={captured!r}")


def test_canary_never_makes_a_second_network_call_if_session_cache_already_active_this_run() -> None:
    tmp, project, plugin, sessions_dir = _setup(
        cfg_overrides={"enabled": False, "consent": {"status": "granted", "ts": "2026-01-01T00:00:00Z"}}
    )
    calls = []

    def _count(*args, **kwargs):
        calls.append(1)
        return {"answers": {"ok": True}, "usage": {}, "model": "jev-latest", "latency_ms": 5, "cache_hit": False}

    with tmp:
        with mock.patch("craftflow_jev_session_check.jev_call", side_effect=_count):
            _run_main(
                {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"},
                _granted_env(project, plugin),
            )
    if len(calls) == 1:
        ok("exactly one jev_call invocation per main() call, never two")
    else:
        fail("canary-single-call-per-invocation", f"calls={len(calls)!r}")


# ---------------------------------------------------------------------------
# Task 5.7: malformed-stdin resilience
# ---------------------------------------------------------------------------


def test_malformed_stdin_variants_exit_zero_silently() -> None:
    tmp, project, plugin, sessions_dir = _setup(
        cfg_overrides={"enabled": False, "consent": {"status": "granted", "ts": "2026-01-01T00:00:00Z"}}
    )
    with tmp:
        env = _granted_env(project, plugin)
        variants = [
            "",
            "[1]",
            "{",
            "null",
            '{"session_id": 5}',
            '{"hook_event_name":"Stop"}',
            '{"hook_event_name":"SessionStart"}',
            '{"hook_event_name":"SessionStart","session_id":""}',
        ]
        failures = []
        for variant in variants:
            out = io.StringIO()
            with _env(env):
                with mock.patch("sys.stdin", io.StringIO(variant)):
                    with contextlib.redirect_stdout(out):
                        code = main()
            if code != 0 or out.getvalue().strip() != "":
                failures.append((variant, code, out.getvalue()))
    if not failures:
        ok("8 malformed/no-op-triggering stdin variants all exit 0 silently")
    else:
        fail("malformed-stdin-variants", f"failures={failures!r}")


def main_tests() -> int:
    print("test_craftflow_jev_session_check: running")
    test_no_key_is_silent_noop()
    test_enabled_true_is_silent_noop_manual_path_untouched()
    test_consent_declined_is_silent_noop()
    test_no_session_id_is_silent_noop()
    test_hooks_json_registers_sessionstart_jev_session_check_with_timeout_5()
    test_consent_unset_first_time_injects_consent_request_with_exact_recorder_commands()
    test_consent_unset_second_sessionstart_same_session_does_not_reask()
    test_consent_unset_writes_already_asked_flag_before_any_response()
    test_consent_granted_canary_success_first_run_writes_active_and_injects_note()
    test_consent_granted_canary_success_second_identical_run_stays_silent()
    test_consent_granted_canary_failure_variants()
    test_canary_uses_own_short_budget_not_default_4s()
    test_canary_never_makes_a_second_network_call_if_session_cache_already_active_this_run()
    test_malformed_stdin_variants_exit_zero_silently()

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
    raise SystemExit(main_tests())
