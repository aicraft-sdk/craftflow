#!/usr/bin/env python3
"""Live proof driver for the jev-canary manifest's "Risk-gate audit round-trip against the
real API" scenario. Runs the real craftflow_jev_risk_gate.py hook as a subprocess against the
real TYPESAFE_API_KEY -- never mocks craftflow_jev_client.call.

Same subprocess + temp-plugin-root + env-var-override technique as
tests/live/jev_audit_roundtrip.py (NOT jev_remfix_scope_roundtrip.py's --config CLI-flag
technique, since craftflow_jev_risk_gate.py has no CLI flags -- it is a stdin-JSON-driven
PreToolUse hook).

Given config/jev.json enabled:true with features.riskGate:audit in a temp plugin root, and a
real TYPESAFE_API_KEY in env, When the real craftflow_jev_risk_gate.py hook is fed a PreToolUse
payload for an rm -rf Bash command, Then the hook exits 0 with empty stdout and events.jsonl
gains exactly 1 telemetry row (feature=risk_gate, category=rm_rf) containing no raw command
text.

Exit 0 on success; prints a diagnostic to stderr and exits 1 on any assertion failure (never
raises, so the live harness records a clean FAIL row instead of a traceback) -- including when
TYPESAFE_API_KEY is missing from the environment (fail fast, not a silent no-op: exit 1 with a
clear stderr message).

Run: python3 plugins/craftflow/tests/live/jev_risk_gate_roundtrip.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"

RISKY_COMMAND = "rm -rf /tmp/craftflow-live-canary-scratch"


def run_hook(payload: dict, env: dict) -> tuple[int, str, str]:
    """Same subprocess technique as jev_audit_roundtrip.py's run_hook() -- copied, not
    imported, per the plan's "do not import the monolith" rule.

    `timeout=15` is comfortably above the client's 4s call budget plus process startup, and
    well below the 900s live-harness ceiling -- a hung hook raises `subprocess.TimeoutExpired`
    instead of blocking the driver indefinitely (caught explicitly in main())."""
    merged_env = {**os.environ, **env}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_jev_risk_gate.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=15,
    )
    return result.returncode, result.stdout.strip(), result.stderr


def main() -> int:
    try:
        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            print(
                "FAIL: TYPESAFE_API_KEY is required but not set in the environment -- "
                "this live driver cannot run without a real key (not a silent skip)",
                file=sys.stderr,
            )
            return 1

        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            project = root / "project"
            plugin = root / "plugin"
            project.mkdir(parents=True)
            (plugin / "config").mkdir(parents=True)

            cfg = json.loads((PLUGIN_ROOT / "config" / "jev.json").read_text())
            cfg["enabled"] = True
            cfg["features"]["riskGate"] = "audit"
            (plugin / "config" / "jev.json").write_text(json.dumps(cfg))

            env = {
                "CLAUDE_PROJECT_DIR": str(project),
                "CLAUDE_PLUGIN_ROOT": str(plugin),
                "TYPESAFE_API_KEY": api_key,
            }
            try:
                code, out, err = run_hook(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "tool_input": {"command": RISKY_COMMAND},
                        "cwd": str(project),
                    },
                    env,
                )
            except subprocess.TimeoutExpired:
                print("FAIL: hook did not exit within 15s", file=sys.stderr)
                return 1

            failures: list[str] = []
            if code != 0:
                failures.append(f"exit code {code} != 0 (stderr={err!r})")
            if out != "":
                failures.append(f"stdout not empty: {out!r}")

            events_path = project / ".craftflow" / "state" / "jev" / "events.jsonl"
            raw_text = events_path.read_text(encoding="utf-8") if events_path.exists() else ""
            if RISKY_COMMAND in raw_text:
                failures.append("events.jsonl contains raw command text")

            rows: list[dict] = []
            if raw_text:
                for line in raw_text.splitlines():
                    if not line.strip():
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        failures.append(f"malformed telemetry row: {exc}")

            if len(rows) != 1:
                failures.append(f"expected 1 telemetry row, got {len(rows)}")
            elif rows[0].get("feature") != "risk_gate" or rows[0].get("category") != "rm_rf":
                failures.append(f"unexpected row shape: {rows[0]!r}")

            if failures:
                print("FAIL: " + "; ".join(failures), file=sys.stderr)
                return 1

            print(
                f"OK: risk_gate audit round-trip against the real API "
                f"({len(rows)} telemetry row, no raw command text)"
            )
            return 0
    except Exception as exc:
        print(f"FAIL: unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
