#!/usr/bin/env python3
"""Stop-gate: optionally blocks session completion until a configured verify
command (e.g. a build or test command) passes.

Concept ported (not code) from xai-org/grok-build's xai-grok-hooks
stop-verify guard example (Apache-2.0). See this plugin's NOTICE.

Opt-in, OFF BY DEFAULT: fully inert unless a project explicitly configures
`config/stop-verify.json` (same directory/graceful-default-on-missing-file
convention as this plugin's existing hook-mode.json, see
craftflow_hooklib.load_mode) with `{"enabled": true, "command": "<cmd>"}`.
With no config file, a missing/malformed config, or `enabled: false`, this
hook returns 0 with no output -- identical to the pre-existing Stop hooks
(craftflow_stop_persist.py) that never block.

Config schema (config/stop-verify.json):
  {
    "enabled": bool,            # default false
    "command": str | null,      # shell command to run, e.g. "pnpm test"
    "cwd": str | null,          # working dir; defaults to the session's own
                                 # cwd (from stdin), else CLAUDE_PROJECT_DIR
    "timeoutSeconds": number    # default 120
  }

Decision output: per Claude Code's Stop-hook contract, block/continue is
communicated as JSON `{"decision": "block", "reason": "..."}` on stdout
(the pre-existing craftflow_stop_persist.py never emits this -- it never
blocks -- so this is the first Stop hook in this plugin to use it). Exit
code is always 0; blocking is signaled via the JSON decision, not the exit
code (mirrors this plugin's PreToolUse convention of JSON-decision-over-
exit-code, see craftflow_pretooluse_bash_guard.py).

Infinite-loop safety: `stop_hook_active` (set by Claude Code when a Stop
hook already forced continuation once for this stop) is always honored as
an unconditional allow, matching the standard Claude Code hooks convention
-- otherwise a persistently-failing verify command would trap the session
in a block loop forever.
"""
from __future__ import annotations

import json
import subprocess

from craftflow_hooklib import (
    json_print,
    load_input,
    log_event,
    plugin_config_dir,
    project_dir,
)

DEFAULT_TIMEOUT_SECONDS = 120


def load_stop_verify_config() -> dict:
    path = plugin_config_dir() / "stop-verify.json"
    defaults = {"enabled": False, "command": None, "cwd": None, "timeoutSeconds": DEFAULT_TIMEOUT_SECONDS}
    if not path.exists():
        return defaults
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return defaults
    merged = dict(defaults)
    merged.update({k: v for k, v in loaded.items() if k in defaults})
    return merged


def main() -> int:
    data = load_input()

    # Infinite-loop safety: never block again on a continuation stop.
    if data.get("stop_hook_active"):
        return 0

    config = load_stop_verify_config()
    if not config.get("enabled") or not config.get("command"):
        return 0

    command = config["command"]
    cwd = config.get("cwd") or data.get("cwd") or str(project_dir())
    timeout = config.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        passed = result.returncode == 0
        detail = (result.stderr or result.stdout or "").strip()[-500:]
        reason = None if passed else f"verify command '{command}' failed (exit {result.returncode}): {detail}"
    except subprocess.TimeoutExpired:
        passed = False
        reason = f"verify command '{command}' timed out after {timeout}s"
    except Exception as exc:
        passed = False
        reason = f"verify command '{command}' could not be run: {exc}"

    log_event(
        "plugin_stop_verify",
        {
            "event": "stop_verify",
            "command": command,
            "decision": "approve" if passed else "block",
            "reason": reason or "verify_passed",
        },
    )

    if not passed:
        json_print(
            {
                "decision": "block",
                "reason": reason,
                "systemMessage": "CRAFTFLOW stop-verify: configured verify command has not passed yet.",
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
