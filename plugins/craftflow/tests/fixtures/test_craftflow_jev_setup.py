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

    expected_dict = dict(cfg_data)
    expected_dict["enabled"] = True
    expected_text = json.dumps(expected_dict, indent=2) + "\n"

    if code == 0 and written == expected_text and SENTINEL_KEY not in written and SENTINEL_KEY not in out:
        ok("--enable checks then writes enabled:true, preserving other keys + 2-space indent")
    else:
        fail(
            "enable-writes-preserving-keys",
            f"code={code} out={out!r} err={err!r} written={written!r} expected_text={expected_text!r}",
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

    if code == 0 and written["enabled"] is False:
        ok("--disable sets enabled:false")
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
    test_check_no_key_exits_2_names_env_var_and_setup_url()
    test_check_success_prints_privacy_note_and_exits_0()
    test_check_canary_failure_exits_3_and_leaves_config_unchanged()
    test_enable_runs_check_then_writes_enabled_true_preserving_keys_and_indent()
    test_enable_skips_write_when_check_fails()
    test_disable_writes_enabled_false()
    test_write_failure_exits_4()
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
