#!/usr/bin/env python3
"""Live proof driver for the jev-canary manifest's "Audit round-trip against
the real API" scenario (Task 5.4).

Unlike tests/fixtures/test_craftflow_jev_prompt_hint.py, this driver does
NOT mock craftflow_jev_prompt_hint.jev_call -- it runs the real
craftflow_jev_prompt_hint.py hook as a subprocess against the real
TYPESAFE_API_KEY in the environment (required_env in jev-canary.json), and
asserts the "Audit round-trip" scenario from the plan's Live Verification
Strategy:

  Given config/jev.json enabled:true with both features audit in a temp
  plugin root, When the real hook is fed
  {"hook_event_name":"UserPromptSubmit","prompt":"fix the crash in the login
  form","cwd":"<tmp>"}, Then exit 0, empty stdout, and events.jsonl has 2
  rows with feature in {routing, skill}, heuristic.workflow == "DEBUG", and
  no prompt text.

Exit 0 on success; prints a diagnostic to stderr and exits 1 on any
assertion failure (never raises, so the live harness records a clean FAIL
row instead of a traceback).

Run: python3 plugins/craftflow/tests/live/jev_audit_roundtrip.py
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

PROMPT_TEXT = "fix the crash in the login form"


def run_hook(payload: dict, env: dict) -> tuple[int, str, str]:
    """Same subprocess technique as craftflow_hook_unit_tests.py:57-67 and
    tests/fixtures/test_craftflow_jev_prompt_hint.py's run_hook() -- copied,
    not imported, per the plan's "do not import the monolith" rule."""
    merged_env = {**os.environ, **env}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_jev_prompt_hint.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged_env,
    )
    return result.returncode, result.stdout.strip(), result.stderr


def main() -> int:
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
        cfg["enabled"] = True
        cfg["features"] = {"routingHint": "audit", "skillHint": "audit"}
        (plugin / "config" / "jev.json").write_text(json.dumps(cfg))

        env = {
            "CLAUDE_PROJECT_DIR": str(project),
            "CLAUDE_PLUGIN_ROOT": str(plugin),
            "TYPESAFE_API_KEY": api_key,
        }
        code, out, err = run_hook(
            {"hook_event_name": "UserPromptSubmit", "prompt": PROMPT_TEXT, "cwd": str(project)},
            env,
        )

        failures: list[str] = []
        if code != 0:
            failures.append(f"exit code {code} != 0 (stderr={err!r})")
        if out != "":
            failures.append(f"stdout not empty: {out!r}")

        events_path = project / ".craftflow" / "state" / "jev" / "events.jsonl"
        raw_text = events_path.read_text(encoding="utf-8") if events_path.exists() else ""
        if PROMPT_TEXT in raw_text:
            failures.append("events.jsonl contains prompt text")

        rows: list[dict] = []
        if raw_text:
            for line in raw_text.splitlines():
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    failures.append(f"malformed telemetry row: {exc}")

        if len(rows) != 2:
            failures.append(f"expected 2 telemetry rows, got {len(rows)}")

        features = {row.get("feature") for row in rows}
        if not features.issubset({"routing", "skill"}):
            failures.append(f"unexpected feature values: {features!r}")

        routing_rows = [row for row in rows if row.get("feature") == "routing"]
        if routing_rows:
            heuristic = routing_rows[0].get("heuristic_result") or {}
            if heuristic.get("workflow") != "DEBUG":
                failures.append(f"heuristic.workflow != DEBUG: {heuristic!r}")
        else:
            failures.append("no routing row to check heuristic.workflow")

        if failures:
            print("FAIL: " + "; ".join(failures), file=sys.stderr)
            return 1

        print(f"OK: audit round-trip against the real API ({len(rows)} telemetry rows, no prompt text)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
