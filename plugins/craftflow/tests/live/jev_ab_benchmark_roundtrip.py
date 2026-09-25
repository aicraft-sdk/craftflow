#!/usr/bin/env python3
"""Live proof driver for the jev-ab-benchmark manifest's "A/B replay smoke
round-trip against the real API" scenario (Phase 3b, Task 3b.1 of
docs/plans/2026-09-25-jev-vs-no-jev-benchmark-plan.md).

Runs the real craftflow_jev_replay.py and craftflow_jev_ab_report.py scripts
as subprocesses -- the copy-not-import isolation technique already proven by
tests/live/jev_audit_roundtrip.py and jev_remfix_scope_roundtrip.py -- over a
small, fixed 5-prompt corpus (2 DEBUG-shaped, 1 PLAN-shaped, 1 REVIEW-shaped,
1 BUILD-shaped, chosen to exercise every workflow_type bucket). This is
distinct from, and much cheaper than, the full 150-prompt benchmark run
(Phase 4).

  Given an isolated temp plugin root with config/jev.json forced enabled:true
  / both features audit, and the fixed 5-prompt corpus below, When the real
  replay driver (craftflow_jev_replay.py) runs all 5 prompts through the real
  hook and then craftflow_jev_ab_report.py runs over the resulting isolated
  events.jsonl + replay_manifest.jsonl, Then: replay produces exactly 10
  telemetry rows (5 routing + 5 skill, no prompt text), the manifest has
  exactly 5 rows (no prompt text), and the A/B report exits 0 with
  routing.n == 5 and every routing row's heuristic_result.workflow
  deterministic (re-derivable from craftflow_jev_heuristic.classify() on the
  same 5 fixed prompt strings, joined back via manifest call_id ->
  source_workflow_uuid -> the originating corpus prompt).

Exit 0 on success; prints a diagnostic to stderr and exits 1 on any
assertion failure (never raises, so the live harness records a clean FAIL
row instead of a traceback).

Run: python3 plugins/craftflow/tests/live/jev_ab_benchmark_roundtrip.py
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
REPLAY_SCRIPT = SCRIPTS / "craftflow_jev_replay.py"
AB_REPORT_SCRIPT = SCRIPTS / "craftflow_jev_ab_report.py"

# craftflow_jev_heuristic.classify() is a pure, no-I/O helper module -- not
# one of the two scripts the plan forbids importing (craftflow_jev_replay /
# craftflow_jev_ab_report), and importing it here is required to re-derive
# the deterministic heuristic answer per the exit criteria above.
sys.path.insert(0, str(SCRIPTS))
from craftflow_jev_heuristic import classify  # noqa: E402

# Fixed 5-prompt corpus: 2 DEBUG-shaped, 1 PLAN-shaped, 1 REVIEW-shaped, 1
# BUILD-shaped -- each chosen so craftflow_jev_heuristic.classify() lands on
# exactly the labeled workflow_type deterministically (verified against
# INTENT_TABLE's ERROR > PLAN > REVIEW > BUILD-default priority order).
CORPUS = [
    {
        "workflow_uuid": "jev-ab-smoke-debug-1",
        "workflow_type": "DEBUG",
        "user_request": "fix the crash in the login form",
    },
    {
        "workflow_uuid": "jev-ab-smoke-debug-2",
        "workflow_type": "DEBUG",
        "user_request": "debug why the payment webhook is failing",
    },
    {
        "workflow_uuid": "jev-ab-smoke-plan-1",
        "workflow_type": "PLAN",
        "user_request": "design the architecture for the new onboarding flow",
    },
    {
        "workflow_uuid": "jev-ab-smoke-review-1",
        "workflow_type": "REVIEW",
        "user_request": "review the pull request for security issues",
    },
    {
        "workflow_uuid": "jev-ab-smoke-build-1",
        "workflow_type": "BUILD",
        "user_request": "add a new export button to the dashboard",
    },
]

PROMPT_TEXTS = [row["user_request"] for row in CORPUS]
CORPUS_BY_UUID = {row["workflow_uuid"]: row["user_request"] for row in CORPUS}


def run_script(script: Path, args: list, env: dict, timeout: int) -> tuple[int, str, str]:
    """Same subprocess technique as jev_audit_roundtrip.py's run_hook() /
    jev_remfix_scope_roundtrip.py's run_script() -- copied, not imported."""
    merged_env = {**os.environ, **env}
    result = subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=timeout,
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
            corpus_path = root / "corpus.jsonl"
            events_path = root / "events.jsonl"
            manifest_path = root / "replay_manifest.jsonl"

            corpus_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=True) for row in CORPUS) + "\n",
                encoding="utf-8",
            )

            # 5 sequential real API calls, each budgeted at up to 4.0s
            # (TOTAL_BUDGET_SECONDS in craftflow_jev_client.py) plus retry
            # backoff and process startup -- 90s is comfortably above the
            # worst case (~50s) and well below the 900s live-harness ceiling.
            try:
                replay_code, replay_out, replay_err = run_script(
                    REPLAY_SCRIPT,
                    [
                        "--corpus", str(corpus_path),
                        "--events-out", str(events_path),
                        "--manifest-out", str(manifest_path),
                    ],
                    {"TYPESAFE_API_KEY": api_key},
                    timeout=90,
                )
            except subprocess.TimeoutExpired:
                print(
                    "FAIL: replay did not exit within 90s (possible hang across 5 prompts)",
                    file=sys.stderr,
                )
                return 1

            failures: list[str] = []
            if replay_code != 0:
                failures.append(f"replay exit code {replay_code} != 0 (stderr={replay_err!r})")

            raw_events = events_path.read_text(encoding="utf-8") if events_path.exists() else ""
            raw_manifest = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else ""

            for text in PROMPT_TEXTS:
                if text in raw_events:
                    failures.append(f"events.jsonl contains prompt text: {text!r}")
                if text in raw_manifest:
                    failures.append(f"replay_manifest.jsonl contains prompt text: {text!r}")

            event_rows: list[dict] = []
            for line in raw_events.splitlines():
                if not line.strip():
                    continue
                try:
                    event_rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    failures.append(f"malformed telemetry row: {exc}")

            if len(event_rows) != 10:
                failures.append(
                    f"expected 10 telemetry rows (5 routing + 5 skill), got {len(event_rows)} "
                    f"(replay stdout={replay_out!r})"
                )

            routing_rows = [row for row in event_rows if row.get("feature") == "routing"]
            skill_rows = [row for row in event_rows if row.get("feature") == "skill"]
            if len(routing_rows) != 5:
                failures.append(f"expected 5 routing rows, got {len(routing_rows)}")
            if len(skill_rows) != 5:
                failures.append(f"expected 5 skill rows, got {len(skill_rows)}")

            manifest_rows: list[dict] = []
            for line in raw_manifest.splitlines():
                if not line.strip():
                    continue
                try:
                    manifest_rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    failures.append(f"malformed manifest row: {exc}")

            if len(manifest_rows) != 5:
                failures.append(f"expected 5 manifest rows, got {len(manifest_rows)}")

            manifest_by_call_id = {
                row.get("call_id"): row.get("source_workflow_uuid")
                for row in manifest_rows
                if isinstance(row.get("call_id"), str)
            }

            # Determinism check: for every routing row, re-derive the
            # expected heuristic answer from the SAME fixed prompt string
            # (joined back via call_id -> source_workflow_uuid -> corpus
            # prompt, since the telemetry row itself never carries prompt
            # text) and assert it matches what the real hook actually wrote.
            for row in routing_rows:
                call_id = row.get("call_id")
                workflow_uuid = manifest_by_call_id.get(call_id) if isinstance(call_id, str) else None
                if workflow_uuid is None or workflow_uuid not in CORPUS_BY_UUID:
                    failures.append(
                        f"routing row call_id {call_id!r} has no matching manifest entry -- "
                        "cannot verify heuristic determinism"
                    )
                    continue
                expected_workflow = classify(CORPUS_BY_UUID[workflow_uuid])["workflow"]
                heuristic_result = row.get("heuristic_result") or {}
                actual_workflow = heuristic_result.get("workflow")
                if actual_workflow != expected_workflow:
                    failures.append(
                        f"routing row for {workflow_uuid!r}: heuristic_result.workflow "
                        f"{actual_workflow!r} != classify()-derived {expected_workflow!r}"
                    )

            if failures:
                print("FAIL: " + "; ".join(failures), file=sys.stderr)
                return 1

            try:
                ab_code, ab_out, ab_err = run_script(
                    AB_REPORT_SCRIPT,
                    [
                        "--events", str(events_path),
                        "--manifest", str(manifest_path),
                        "--json",
                    ],
                    {},
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                print("FAIL: A/B report did not exit within 30s", file=sys.stderr)
                return 1

            ab_failures: list[str] = []
            if ab_code != 0:
                ab_failures.append(f"A/B report exit code {ab_code} != 0 (stderr={ab_err!r})")

            ab_payload = None
            try:
                ab_payload = json.loads(ab_out)
            except json.JSONDecodeError:
                ab_failures.append(f"A/B report stdout not valid JSON: {ab_out!r}")

            if ab_payload is not None:
                routing_summary = ab_payload.get("features", {}).get("routing", {})
                if routing_summary.get("n") != 5:
                    ab_failures.append(f"routing.n != 5: {routing_summary.get('n')!r}")

            if ab_failures:
                print("FAIL: " + "; ".join(ab_failures), file=sys.stderr)
                return 1

            print(
                "OK: A/B replay smoke round-trip against the real API "
                f"({len(event_rows)} telemetry rows, {len(manifest_rows)} manifest rows, "
                "routing.n=5, heuristic determinism verified)"
            )
            return 0
    except Exception as exc:
        print(f"FAIL: unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
