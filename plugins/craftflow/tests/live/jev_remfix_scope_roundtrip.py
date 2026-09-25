#!/usr/bin/env python3
"""Live proof driver for the jev-canary manifest's "REM-FIX scope audit
round-trip against the real API" scenario. Runs the real
craftflow_jev_remfix_scope.py script as a subprocess against the real
TYPESAFE_API_KEY -- never mocks craftflow_jev_client.call.

Run: python3 plugins/craftflow/tests/live/jev_remfix_scope_roundtrip.py
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


def run_script(args: list, env: dict) -> tuple[int, str, str]:
    merged_env = {**os.environ, **env}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_jev_remfix_scope.py"), *args],
        capture_output=True, text=True, env=merged_env, timeout=15,
    )
    return result.returncode, result.stdout.strip(), result.stderr


def main() -> int:
    try:
        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            print("SKIP: TYPESAFE_API_KEY not set", file=sys.stderr)
            return 1

        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            project = root / "project"
            project.mkdir(parents=True)
            state_dir = root / "state"
            config_path = root / "jev.json"

            cfg = json.loads((PLUGIN_ROOT / "config" / "jev.json").read_text())
            cfg["enabled"] = True
            cfg["features"]["remediationScope"] = "audit"
            config_path.write_text(json.dumps(cfg))

            critical_text = "NullPointerException in payment webhook handler"
            high_text = "Missing input validation on /api/webhook route"

            try:
                code, out, err = run_script(
                    ["--critical", critical_text, "--high", high_text,
                     "--workflow-uuid", "wf-live-canary",
                     "--config", str(config_path), "--state-dir", str(state_dir)],
                    {
                        "TYPESAFE_API_KEY": api_key,
                        "CLAUDE_PROJECT_DIR": str(project),
                        "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT),
                    },
                )
            except subprocess.TimeoutExpired:
                print("FAIL: script did not exit within 15s", file=sys.stderr)
                return 1

            failures: list[str] = []
            if code != 0:
                failures.append(f"exit code {code} != 0 (stderr={err!r})")

            payload = None
            try:
                payload = json.loads(out)
            except json.JSONDecodeError:
                failures.append(f"stdout not valid JSON: {out!r}")

            if payload is not None:
                if payload.get("decision") != "logged":
                    failures.append(f"expected decision=logged, got {payload!r}")
                if payload.get("choice") not in ("critical_only", "all_issues"):
                    failures.append(f"unexpected choice: {payload!r}")

            events_path = state_dir / "jev" / "events.jsonl"
            raw_text = events_path.read_text(encoding="utf-8") if events_path.exists() else ""
            if critical_text in raw_text or high_text in raw_text:
                failures.append("events.jsonl contains raw finding text")

            rows = [json.loads(l) for l in raw_text.splitlines() if l.strip()] if raw_text else []
            if len(rows) != 1:
                failures.append(f"expected 1 telemetry row, got {len(rows)}")
            elif rows[0].get("feature") != "remfix_scope":
                failures.append(f"unexpected feature: {rows[0].get('feature')!r}")

            if failures:
                print("FAIL: " + "; ".join(failures), file=sys.stderr)
                return 1

            print(f"OK: remfix_scope audit round-trip against the real API (decision={payload.get('decision')}, choice={payload.get('choice')})")
            return 0
    except Exception as exc:
        print(f"FAIL: unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
