#!/usr/bin/env python3
"""Operator CLI for the optional Jev (TypeSafe AI) routing + skill hint feature.

Reads/writes `config/jev.json` -- the same file `craftflow_jev_config.py`
loads at hook time -- and runs a canary call through
`craftflow_jev_client.call()` to prove a `TYPESAFE_API_KEY` actually works
before flipping `enabled` on.

DD-12b: this CLI reads NO stdin anywhere -- it is argument- and
environment-driven only (never `sys.stdin.read()` / `craftflow_hooklib.load_input()`).
That means it is automatically covered by two shared, unmodified tests:
- `test_no_unguarded_or_mis_guarded_stdin_read_call_sites` (craftflow_hook_unit_tests.py):
  an AST scan over every `craftflow_*.py` sibling -- zero `sys.stdin.read()`
  call sites in this file is a pass by construction.
- `test_new_modules_are_import_cheap` (test_craftflow_jev_prompt_hint.py):
  globs `craftflow_jev_*.py` and imports each in a fresh subprocess with a
  <0.5s budget and no side effects -- every branch below lives inside a
  function, executed only from `main()` / `if __name__ == "__main__"`, so
  importing this module alone does nothing.

Exit codes: 0 ok, 2 missing TYPESAFE_API_KEY, 3 canary call failed,
4 config write failed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from craftflow_hooklib import now_iso, plugin_config_dir
from craftflow_jev_client import call
from craftflow_jev_config import DEFAULTS, api_key as get_api_key, load_config

SETUP_URL = "https://console.typesafe.ai/keys"

PRIVACY_NOTE_TEMPLATE = (
    "PRIVACY: when enabled, each prompt you submit in Claude Code (first {max_state_chars} chars), the project folder name,\n"
    "and the active craftflow workflow type are sent to api.typesafe.ai (TypeSafe AI) for classification.\n"
    "Nothing else is sent; no prompt text is stored locally; telemetry rows contain only answers/latency/usage.\n"
    "Disable at any time: python3 {script} --disable"
)


def default_config_path() -> Path:
    return plugin_config_dir() / "jev.json"


def privacy_note(max_state_chars: Any) -> str:
    script = sys.argv[0] if sys.argv and sys.argv[0] else "scripts/craftflow_jev_setup.py"
    return PRIVACY_NOTE_TEMPLATE.format(max_state_chars=max_state_chars, script=script)


def format_status(cfg: Dict[str, Any], config_path: Path, env: Dict[str, str]) -> str:
    features = cfg.get("features", {}) or {}
    thresholds = cfg.get("thresholds", {}) or {}
    lines = [
        f"config: {config_path}",
        f"enabled: {'true' if cfg.get('enabled') else 'false'}",
        f"model: {cfg.get('model')}",
        f"features.routingHint: {features.get('routingHint')}",
        f"features.skillHint: {features.get('skillHint')}",
        f"thresholds.routing: {thresholds.get('routing')}",
        f"thresholds.skill: {thresholds.get('skill')}",
        f"maxStateChars: {cfg.get('maxStateChars')}",
        f"timeoutSeconds: {cfg.get('timeoutSeconds')}",
        f"key: {'present' if get_api_key(env) else 'absent'}",
    ]
    return "\n".join(lines)


def _read_raw_dict(path: Path) -> Dict[str, Any]:
    """Best-effort raw (unnormalized) JSON dict read, used so --enable/
    --disable rewrite ONLY the `enabled` key and preserve everything else --
    including keys craftflow_jev_config.normalize() does not know about.
    Falls back to a fresh DEFAULTS copy on any missing/corrupt file, matching
    craftflow_jev_config.load_config()'s own fail-open posture."""
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULTS))


def _write_enabled_flag(path: Path, value: bool, consent_status: Optional[str] = None) -> bool:
    try:
        raw = _read_raw_dict(path)
        raw["enabled"] = value
        if consent_status is not None:
            raw["consent"] = {"status": consent_status, "ts": now_iso()}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _write_consent(path: Path, status: str) -> bool:
    try:
        raw = _read_raw_dict(path)
        raw["consent"] = {"status": status, "ts": now_iso()}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def run_record_consent(config_path: Path, status: str) -> int:
    """Record the user's one-time consent decision. Never runs a canary and
    never writes a session-cache entry (DD-10/Option B) -- activation begins
    only at the next SessionStart firing (Phase 5), never immediately here."""
    if _write_consent(config_path, status):
        print(f"consent: {status}")
        return 0
    print("ERROR: failed to write consent record", file=sys.stderr)
    return 4


def run_check(cfg: Dict[str, Any], env: Dict[str, str]) -> int:
    """Print the privacy note and run the canary call. Never writes config.
    Returns 0 ok, 2 missing key, 3 canary failed."""
    key = get_api_key(env)
    if not key:
        print(
            f"ERROR: TYPESAFE_API_KEY is not set. Get a key at {SETUP_URL}",
            file=sys.stderr,
        )
        return 2
    print(privacy_note(cfg.get("maxStateChars", DEFAULTS["maxStateChars"])))
    result = call(
        state={"ping": "craftflow jev-setup canary"},
        questions={"ok": {"type": "noul", "instructions": "Is `ping` a short greeting-like string?"}},
        api_key=key,
        model=cfg.get("model", DEFAULTS["model"]),
        timeout=cfg.get("timeoutSeconds", DEFAULTS["timeoutSeconds"]),
        cache_dir=None,
    )
    if result is None:
        print("ERROR: canary call failed -- key rejected or endpoint unreachable", file=sys.stderr)
        return 3
    print("OK: canary call succeeded")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="craftflow_jev_setup.py",
        description="Operator setup for the optional Jev (TypeSafe AI) routing hint.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="run a canary call and print the privacy note; never writes config")
    group.add_argument("--enable", action="store_true", help="run --check, then set enabled:true on success")
    group.add_argument("--disable", action="store_true", help="set enabled:false")
    group.add_argument("--status", action="store_true", help="print the effective config (never the key value)")
    group.add_argument(
        "--record-consent",
        choices=["granted", "declined"],
        default=None,
        help="record the user's one-time consent decision for the auto-detect path; internal -- invoked by the assistant after AskUserQuestion, not a manual operator step",
    )
    parser.add_argument("--config", default=None, help="override the config file path (default: plugin config dir)")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config) if args.config else default_config_path()
    env = os.environ
    cfg, _decisions = load_config(config_path)

    if args.status:
        print(format_status(cfg, config_path, env))
        return 0

    if args.record_consent:
        return run_record_consent(config_path, args.record_consent)

    if args.check:
        return run_check(cfg, env)

    if args.enable:
        exit_code = run_check(cfg, env)
        if exit_code != 0:
            return exit_code
        if _write_enabled_flag(config_path, True, consent_status="granted"):
            print("enabled: true")
            return 0
        print("ERROR: failed to write config", file=sys.stderr)
        return 4

    if args.disable:
        if _write_enabled_flag(config_path, False, consent_status="declined"):
            print("enabled: false")
            return 0
        print("ERROR: failed to write config", file=sys.stderr)
        return 4

    return 0  # unreachable: the mutually exclusive group above is required=True


if __name__ == "__main__":
    raise SystemExit(main())
