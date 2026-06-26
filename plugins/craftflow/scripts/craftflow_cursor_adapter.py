#!/usr/bin/env python3
"""
craftflow_cursor_adapter.py

Thin shim that bridges Cursor's hook I/O contract to Claude's hook I/O contract
so the existing 22 Python scripts run unchanged.

Usage:
    python3 craftflow_cursor_adapter.py <target_script_path> [--tool Tool1,Tool2] [--event EventName]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Craftflow Cursor adapter shim")
    parser.add_argument("target_script", help="Path to the target Claude hook script")
    parser.add_argument("--tool", default="", help="Comma-separated tool names to match (empty = all)")
    parser.add_argument("--event", default="", help="Event name to inject as hook_event_name")
    return parser.parse_args()


def bridge_env():
    """Set environment variables so existing Claude scripts resolve paths correctly."""
    # Self-resolve plugin root from adapter's own location (parents[1] = craftflow plugin dir)
    plugin_root = str(Path(__file__).resolve().parents[1])

    os.environ.setdefault("CLAUDE_PLUGIN_ROOT", plugin_root)
    os.environ.setdefault("CURSOR_PLUGIN_ROOT", plugin_root)

    # Map Cursor project root → Claude project dir
    cursor_project_root = os.environ.get("CURSOR_PROJECT_ROOT", "")
    if cursor_project_root:
        os.environ.setdefault("CLAUDE_PROJECT_DIR", cursor_project_root)
    else:
        os.environ.setdefault("CLAUDE_PROJECT_DIR", os.getcwd())


def read_cursor_stdin():
    """Read and parse Cursor's stdin JSON. Returns empty dict on missing/invalid input."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return {}


def normalize_input(cursor_payload, event_override):
    """
    Remap Cursor's hook payload to Claude's expected format.

    Cursor sends:  { "event": "...", "toolName": "...", "toolInput": {...}, ... }
    Claude expects: { "hook_event_name": "...", "tool_name": "...", "tool_input": {...}, ... }
    """
    normalized = dict(cursor_payload)  # pass-through all fields

    # hook_event_name: prefer --event arg, fall back to payload "event" field
    normalized["hook_event_name"] = event_override or cursor_payload.get("event", "")

    # tool_name: remap from Cursor's "toolName"
    if "toolName" in cursor_payload:
        normalized["tool_name"] = cursor_payload["toolName"]
    normalized.setdefault("tool_name", "")

    # tool_input: remap from Cursor's "toolInput"
    if "toolInput" in cursor_payload:
        normalized["tool_input"] = cursor_payload["toolInput"]
    normalized.setdefault("tool_input", {})

    return normalized


def tool_matches(normalized_input, tool_filter):
    """Return True if we should proceed (no filter, or tool_name is in the filter list)."""
    if not tool_filter:
        return True
    allowed = [t.strip() for t in tool_filter.split(",") if t.strip()]
    return normalized_input.get("tool_name", "") in allowed


def delegate(target_script, normalized_input):
    """Run the target Claude script with normalized JSON on stdin. Returns CompletedProcess."""
    return subprocess.run(
        [sys.executable, target_script],
        input=json.dumps(normalized_input),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def translate_output(result):
    """
    Map Claude's stdout JSON → Cursor exit code + output.

    Claude emits: { "hookSpecificOutput": { "permissionDecision": "deny", "additionalContext": "..." } }
    Cursor reads:
      - exit 2 → block the action
      - stdout → injected context for the agent
      - stderr → logged but not blocking
    """
    # Pass stderr through always
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")

    # If target script failed, fail-open (never block on hook errors)
    if result.returncode != 0:
        return 0

    # Parse Claude's stdout
    try:
        output = json.loads(result.stdout) if result.stdout.strip() else {}
    except (json.JSONDecodeError, ValueError):
        # Non-JSON stdout: pass through as-is and exit 0
        if result.stdout:
            print(result.stdout, end="")
        return 0

    hook_out = output.get("hookSpecificOutput", {})

    # Permission deny → Cursor block (exit 2)
    if hook_out.get("permissionDecision") == "deny":
        reason = (
            hook_out.get("permissionDecisionReason")
            or hook_out.get("reason")
            or hook_out.get("additionalContext")
            or "Action blocked by Craftflow hook"
        )
        print(reason, file=sys.stderr)
        return 2

    # Additional context → pass to Cursor's agent as stdout
    if hook_out.get("additionalContext"):
        print(hook_out["additionalContext"], end="")

    return 0


def main():
    args = parse_args()

    # 1. Bridge environment variables
    bridge_env()

    # 2. Read and normalize Cursor's input
    cursor_payload = read_cursor_stdin()
    normalized = normalize_input(cursor_payload, args.event)

    # 3. Tool/event filter — exit 0 (no-op) when this hook doesn't apply
    if not tool_matches(normalized, args.tool):
        sys.exit(0)

    # 4. Delegate to target Claude hook script
    result = delegate(args.target_script, normalized)

    # 5. Translate output and exit with the appropriate Cursor exit code
    exit_code = translate_output(result)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
