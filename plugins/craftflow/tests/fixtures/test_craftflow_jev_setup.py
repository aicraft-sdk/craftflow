#!/usr/bin/env python3
"""Tests for craftflow_jev_setup.py.

Run: python3 tests/fixtures/test_craftflow_jev_setup.py

All network I/O is mocked via mock.patch("craftflow_jev_setup.call") --
the canary call itself is craftflow_jev_client.call, already covered by
test_craftflow_jev_client.py. This CLI is never given real network access
in these tests, and every test config lives under a temp directory --
the committed config/jev.json is never touched.
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

from craftflow_jev_setup import main, privacy_note  # noqa: E402
from craftflow_jev_config import DEFAULTS  # noqa: E402
from craftflow_skill_promote import parse_frontmatter  # noqa: E402

SKILL_MD = PLUGIN_ROOT / "skills" / "jev-setup" / "SKILL.md"

_passes = 0
_errors: list[str] = []

SENTINEL_KEY = "sk-jev-test-key-should-never-leak"
COMMITTED_CONFIG = PLUGIN_ROOT / "config" / "jev.json"


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def run_cli(argv: list, env: dict) -> tuple:
    """Run main(argv) with os.environ patched to env (not merged) and
    stdout/stderr captured. Returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict("os.environ", env, clear=True), contextlib.redirect_stdout(
        out
    ), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def default_config_dict() -> dict:
    return json.loads(json.dumps(DEFAULTS))


# ---------------------------------------------------------------------------
# --status
# ---------------------------------------------------------------------------


def test_status_prints_effective_config_and_key_absent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        write_config(cfg_path, default_config_dict())
        code, out, err = run_cli(["--status", "--config", str(cfg_path)], {})

    if (
        code == 0
        and "enabled: false" in out
        and "model: jev-latest" in out
        and "key: absent" in out
        and err == ""
    ):
        ok("--status prints effective config with key: absent when no key set")
    else:
        fail("status-key-absent", f"code={code} out={out!r} err={err!r}")


def test_status_prints_key_present_but_never_the_value() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        write_config(cfg_path, default_config_dict())
        code, out, err = run_cli(
            ["--status", "--config", str(cfg_path)], {"TYPESAFE_API_KEY": SENTINEL_KEY}
        )

    if code == 0 and "key: present" in out and SENTINEL_KEY not in out and SENTINEL_KEY not in err:
        ok("--status prints key: present without ever printing the key value")
    else:
        fail("status-key-present-no-value", f"code={code} out={out!r} err={err!r}")


def test_status_output_includes_remediation_scope() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        write_config(cfg_path, default_config_dict())
        code, out, err = run_cli(["--status", "--config", str(cfg_path)], {})

    if (
        code == 0
        and "features.remediationScope: off" in out
        and "thresholds.remediationScope: 0.85" in out
        and err == ""
    ):
        ok("--status output includes remediationScope feature+threshold")
    else:
        fail("status-remediation-scope", f"code={code} out={out!r} err={err!r}")


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------


def test_check_no_key_exits_2_names_env_var_and_setup_url() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        write_config(cfg_path, default_config_dict())
        code, out, err = run_cli(["--check", "--config", str(cfg_path)], {})

    if (
        code == 2
        and "TYPESAFE_API_KEY" in err
        and "https://console.typesafe.ai/keys" in err
    ):
        ok("--check with no key exits 2 and names TYPESAFE_API_KEY + setup URL")
    else:
        fail("check-no-key", f"code={code} out={out!r} err={err!r}")


def test_check_success_prints_privacy_note_and_exits_0() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        write_config(cfg_path, default_config_dict())
        with mock.patch(
            "craftflow_jev_setup.call",
            return_value={"answers": {"ok": True}, "usage": {}, "model": "jev-latest", "latency_ms": 5, "cache_hit": False},
        ):
            code, out, err = run_cli(
                ["--check", "--config", str(cfg_path)], {"TYPESAFE_API_KEY": SENTINEL_KEY}
            )

    expected_note = privacy_note(DEFAULTS["maxStateChars"])
    if code == 0 and "PRIVACY:" in out and expected_note in out and SENTINEL_KEY not in out and SENTINEL_KEY not in err:
        ok("--check with mocked client success prints privacy note and exits 0")
    else:
        fail("check-success", f"code={code} out={out!r} err={err!r} expected_note={expected_note!r}")


def test_check_canary_failure_exits_3_and_leaves_config_unchanged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        cfg_data = default_config_dict()
        write_config(cfg_path, cfg_data)
        before = cfg_path.read_text(encoding="utf-8")
        with mock.patch("craftflow_jev_setup.call", return_value=None):
            code, out, err = run_cli(
                ["--check", "--config", str(cfg_path)], {"TYPESAFE_API_KEY": SENTINEL_KEY}
            )
        after = cfg_path.read_text(encoding="utf-8")

    if code == 3 and before == after and SENTINEL_KEY not in out and SENTINEL_KEY not in err:
        ok("--check canary failure exits 3 and never writes config")
    else:
        fail("check-canary-failure", f"code={code} out={out!r} err={err!r} before={before!r} after={after!r}")


# ---------------------------------------------------------------------------
# --enable / --disable
# ---------------------------------------------------------------------------


def test_enable_runs_check_then_writes_enabled_true_preserving_keys_and_indent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        cfg_data = default_config_dict()
        cfg_data["customNote"] = "keep me"
        write_config(cfg_path, cfg_data)
        with mock.patch(
            "craftflow_jev_setup.call",
            return_value={"answers": {"ok": True}, "usage": {}, "model": "jev-latest", "latency_ms": 5, "cache_hit": False},
        ):
            code, out, err = run_cli(
                ["--enable", "--config", str(cfg_path)], {"TYPESAFE_API_KEY": SENTINEL_KEY}
            )
        written = cfg_path.read_text(encoding="utf-8")
        parsed = json.loads(written)

    if (
        code == 0
        and parsed.get("enabled") is True
        and parsed.get("customNote") == "keep me"
        and parsed.get("consent", {}).get("status") == "granted"
        and isinstance(parsed.get("consent", {}).get("ts"), str)
        and parsed["consent"]["ts"]
        and written.startswith("{\n  ")
        and SENTINEL_KEY not in written
        and SENTINEL_KEY not in out
    ):
        ok("--enable checks then writes enabled:true + consent:granted, preserving other keys + 2-space indent")
    else:
        fail(
            "enable-writes-preserving-keys",
            f"code={code} out={out!r} err={err!r} written={written!r} parsed={parsed!r}",
        )


def test_enable_skips_write_when_check_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        cfg_data = default_config_dict()
        write_config(cfg_path, cfg_data)
        before = cfg_path.read_text(encoding="utf-8")
        with mock.patch("craftflow_jev_setup.call", return_value=None):
            code, out, err = run_cli(
                ["--enable", "--config", str(cfg_path)], {"TYPESAFE_API_KEY": SENTINEL_KEY}
            )
        after = cfg_path.read_text(encoding="utf-8")

    if code == 3 and before == after:
        ok("--enable skips the write entirely when the canary check fails")
    else:
        fail("enable-skips-write-on-check-failure", f"code={code} out={out!r} err={err!r} before={before!r} after={after!r}")


def test_disable_writes_enabled_false() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        cfg_data = default_config_dict()
        cfg_data["enabled"] = True
        write_config(cfg_path, cfg_data)
        code, out, err = run_cli(["--disable", "--config", str(cfg_path)], {})
        written = json.loads(cfg_path.read_text(encoding="utf-8"))

    if (
        code == 0
        and written["enabled"] is False
        and written.get("consent", {}).get("status") == "declined"
        and isinstance(written.get("consent", {}).get("ts"), str)
        and written["consent"]["ts"]
    ):
        ok("--disable sets enabled:false + consent:declined")
    else:
        fail("disable-sets-enabled-false", f"code={code} out={out!r} err={err!r} written={written!r}")


def test_write_failure_exits_4() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # A path that is a directory can never be written as the config file --
        # write_text() raises IsADirectoryError, exercising the exit-4 path.
        cfg_path = Path(tmp) / "a-directory-not-a-file"
        cfg_path.mkdir()
        code, out, err = run_cli(["--disable", "--config", str(cfg_path)], {})

    if code == 4 and "ERROR" in err:
        ok("config write failure (unwritable path) exits 4")
    else:
        fail("write-failure-exit-4", f"code={code} out={out!r} err={err!r}")


def test_write_failure_after_atomic_write_begins_leaves_original_content_unchanged() -> None:
    """REM-FIX regression test (silent-failure-hunter HIGH finding, commit
    1a172de): a mid-write failure must never truncate/destroy the existing
    config -- only fail cleanly. test_write_failure_exits_4 above exercises a
    failure BEFORE any write begins (path is a directory); this test
    exercises the harder case -- the read + temp-file write both succeed,
    and the failure happens at the final commit step (os.replace), which is
    only reachable once the config is rewritten to use the atomic
    temp-file + os.replace() pattern. Proves the ORIGINAL file content
    survives byte-for-byte, not just that an error is reported."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        cfg_data = default_config_dict()
        cfg_data["enabled"] = True
        cfg_data["customNote"] = "must survive a mid-write os.replace failure"
        write_config(cfg_path, cfg_data)
        before = cfg_path.read_text(encoding="utf-8")
        with mock.patch("craftflow_jev_setup.os.replace", side_effect=OSError("disk full")):
            code, out, err = run_cli(["--disable", "--config", str(cfg_path)], {})
        after = cfg_path.read_text(encoding="utf-8")
        leftover_tmp_files = [
            p for p in cfg_path.parent.iterdir() if p.name != cfg_path.name
        ]

    if code == 4 and before == after and "ERROR" in err and leftover_tmp_files == []:
        ok("a write failure at the atomic-commit step leaves the original config byte-for-byte unchanged")
    else:
        fail(
            "write-failure-after-atomic-write-begins-preserves-original",
            f"code={code} out={out!r} err={err!r} before={before!r} after={after!r} leftover={leftover_tmp_files!r}",
        )


def test_write_enabled_flag_rejects_invalid_consent_status() -> None:
    """REM-FIX regression test (silent-failure-hunter MEDIUM finding):
    _write_enabled_flag's consent_status param was previously unvalidated at
    the write site -- only enforced later by normalize() on next load. A
    future direct (non-CLI) caller could write an invalid value straight to
    disk with no guard. This exercises that internal contract directly,
    since the CLI itself never reaches this path (argparse `choices` already
    restricts --record-consent, and --enable/--disable pass fixed literals)."""
    from craftflow_jev_setup import _write_enabled_flag  # noqa: E402 (whitebox internal-contract test)

    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        write_config(cfg_path, default_config_dict())
        before = cfg_path.read_text(encoding="utf-8")
        raised = False
        try:
            _write_enabled_flag(cfg_path, True, consent_status="yes-please")
        except ValueError:
            raised = True
        after = cfg_path.read_text(encoding="utf-8")

    if raised and before == after:
        ok("_write_enabled_flag rejects an invalid consent_status and never writes")
    else:
        fail(
            "write-enabled-flag-rejects-invalid-consent-status",
            f"raised={raised} before={before!r} after={after!r}",
        )


def test_write_consent_rejects_invalid_status() -> None:
    """Same guard as above, for the sibling _write_consent helper used by
    --record-consent."""
    from craftflow_jev_setup import _write_consent  # noqa: E402 (whitebox internal-contract test)

    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        write_config(cfg_path, default_config_dict())
        before = cfg_path.read_text(encoding="utf-8")
        raised = False
        try:
            _write_consent(cfg_path, "yes-please")
        except ValueError:
            raised = True
        after = cfg_path.read_text(encoding="utf-8")

    if raised and before == after:
        ok("_write_consent rejects an invalid status and never writes")
    else:
        fail(
            "write-consent-rejects-invalid-status",
            f"raised={raised} before={before!r} after={after!r}",
        )


# ---------------------------------------------------------------------------
# --record-consent
# ---------------------------------------------------------------------------


def test_record_consent_granted_writes_consent_status_granted_exits_0() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        write_config(cfg_path, default_config_dict())
        code, out, err = run_cli(["--record-consent", "granted", "--config", str(cfg_path)], {})
        written = json.loads(cfg_path.read_text(encoding="utf-8"))

    if (
        code == 0
        and written["consent"]["status"] == "granted"
        and isinstance(written["consent"]["ts"], str)
        and written["consent"]["ts"]
        and "consent: granted" in out
    ):
        ok("--record-consent granted writes consent.status=granted and exits 0")
    else:
        fail("record-consent-granted", f"code={code} out={out!r} err={err!r} written={written!r}")


def test_record_consent_declined_writes_consent_status_declined_exits_0() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        write_config(cfg_path, default_config_dict())
        code, out, err = run_cli(["--record-consent", "declined", "--config", str(cfg_path)], {})
        written = json.loads(cfg_path.read_text(encoding="utf-8"))

    if (
        code == 0
        and written["consent"]["status"] == "declined"
        and isinstance(written["consent"]["ts"], str)
        and written["consent"]["ts"]
        and "consent: declined" in out
    ):
        ok("--record-consent declined writes consent.status=declined and exits 0")
    else:
        fail("record-consent-declined", f"code={code} out={out!r} err={err!r} written={written!r}")


def test_record_consent_preserves_other_keys_and_enabled_flag() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        cfg_data = default_config_dict()
        cfg_data["enabled"] = True
        cfg_data["customNote"] = "keep me too"
        write_config(cfg_path, cfg_data)
        code, out, err = run_cli(["--record-consent", "declined", "--config", str(cfg_path)], {})
        written = json.loads(cfg_path.read_text(encoding="utf-8"))

    if (
        code == 0
        and written["enabled"] is True
        and written["customNote"] == "keep me too"
        and written["consent"]["status"] == "declined"
    ):
        ok("--record-consent never touches enabled or other unrelated keys")
    else:
        fail("record-consent-preserves-keys", f"code={code} out={out!r} err={err!r} written={written!r}")


def test_record_consent_write_failure_exits_4() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "a-directory-not-a-file"
        cfg_path.mkdir()
        code, out, err = run_cli(["--record-consent", "granted", "--config", str(cfg_path)], {})

    if code == 4 and "ERROR" in err:
        ok("--record-consent write failure (unwritable path) exits 4")
    else:
        fail("record-consent-write-failure-exit-4", f"code={code} out={out!r} err={err!r}")


def test_record_consent_granted_never_triggers_network_or_session_cache_write() -> None:
    with tempfile.TemporaryDirectory() as project_root_str:
        project_root = Path(project_root_str)
        cfg_path = project_root / "jev.json"
        write_config(cfg_path, default_config_dict())
        cwd_before = os.getcwd()
        os.chdir(project_root)
        try:
            with mock.patch(
                "craftflow_jev_setup.call",
                side_effect=AssertionError("--record-consent must never trigger a network call"),
            ):
                code, out, err = run_cli(
                    ["--record-consent", "granted", "--config", str(cfg_path)], {}
                )
        finally:
            os.chdir(cwd_before)
        sessions_dir = project_root / ".craftflow" / "state" / "jev" / "sessions"

    if code == 0 and not sessions_dir.exists():
        ok("--record-consent granted never calls the network and never writes a session-cache entry")
    else:
        fail(
            "record-consent-no-network-or-session-cache",
            f"code={code} out={out!r} err={err!r} sessions_dir_exists={sessions_dir.exists()}",
        )


# ---------------------------------------------------------------------------
# --config override never touches the committed file
# ---------------------------------------------------------------------------


def test_config_override_never_touches_committed_file() -> None:
    before = COMMITTED_CONFIG.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "jev.json"
        write_config(cfg_path, default_config_dict())
        with mock.patch(
            "craftflow_jev_setup.call",
            return_value={"answers": {"ok": True}, "usage": {}, "model": "jev-latest", "latency_ms": 5, "cache_hit": False},
        ):
            run_cli(["--enable", "--config", str(cfg_path)], {"TYPESAFE_API_KEY": SENTINEL_KEY})
    after = COMMITTED_CONFIG.read_text(encoding="utf-8")

    if before == after:
        ok("--config override is honored; committed config/jev.json is never touched")
    else:
        fail("config-override-committed-untouched", f"before={before!r} after={after!r}")


def test_jev_setup_skill_frontmatter_and_steps() -> None:
    content = SKILL_MD.read_text(encoding="utf-8")
    fm = parse_frontmatter(content)

    checks = []
    checks.append(bool(fm) and fm.get("name") == "jev-setup")
    checks.append(
        bool(fm)
        and isinstance(fm.get("description"), str)
        and fm["description"].startswith("Use when the user asks to")
    )
    checks.append("--check" in content and "--enable" in content and "--disable" in content)
    checks.append(
        "ask the user to confirm the privacy note before running --enable" in content
    )

    if all(checks):
        ok("jev-setup SKILL.md frontmatter parses and body has the required steps")
    else:
        fail(
            "jev-setup-skill-frontmatter-and-steps",
            f"fm={fm!r} checks={checks!r}",
        )


def main_tests() -> int:
    print("test_craftflow_jev_setup: running")
    test_status_prints_effective_config_and_key_absent()
    test_status_prints_key_present_but_never_the_value()
    test_status_output_includes_remediation_scope()
    test_check_no_key_exits_2_names_env_var_and_setup_url()
    test_check_success_prints_privacy_note_and_exits_0()
    test_check_canary_failure_exits_3_and_leaves_config_unchanged()
    test_enable_runs_check_then_writes_enabled_true_preserving_keys_and_indent()
    test_enable_skips_write_when_check_fails()
    test_disable_writes_enabled_false()
    test_write_failure_exits_4()
    test_write_failure_after_atomic_write_begins_leaves_original_content_unchanged()
    test_write_enabled_flag_rejects_invalid_consent_status()
    test_write_consent_rejects_invalid_status()
    test_record_consent_granted_writes_consent_status_granted_exits_0()
    test_record_consent_declined_writes_consent_status_declined_exits_0()
    test_record_consent_preserves_other_keys_and_enabled_flag()
    test_record_consent_write_failure_exits_4()
    test_record_consent_granted_never_triggers_network_or_session_cache_write()
    test_config_override_never_touches_committed_file()
    test_jev_setup_skill_frontmatter_and_steps()

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
