#!/usr/bin/env python3
"""Tests for craftflow_jev_corpus_build.py.

Run: python3 tests/fixtures/test_craftflow_jev_corpus_build.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_corpus_build import build_corpus, _read_workflow_artifacts  # noqa: E402

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


# ---------------------------------------------------------------------------
# build_corpus()
# ---------------------------------------------------------------------------


def test_build_corpus_dedups_and_caps() -> None:
    records = [
        ("wf01", "BUILD", "req A"),
        ("wf02", "BUILD", "req B"),
        ("wf03", "PLAN", "req C"),
        ("wf04", "PLAN", "req D"),
        ("wf05", "DEBUG", "req E"),
        ("wf06", "DEBUG", "req F"),
        ("wf07", "REVIEW", "req G"),
        ("wf08", "REVIEW", "req H"),
        ("wf09", "BUILD", "req A"),  # exact-duplicate user_request of wf01
        ("wf10", None, "req I"),  # workflow_type: None -- must be excluded
    ]
    result = build_corpus(records, limit=5)
    result_uuids = {row["workflow_uuid"] for row in result}
    result_requests = [row["user_request"] for row in result]
    checks = (
        len(result) <= 5,
        "wf09" not in result_uuids,
        "wf10" not in result_uuids,
        len(result_requests) == len(set(result_requests)),  # no duplicate requests
        all(row["workflow_type"] is not None for row in result),
    )
    if all(checks):
        ok(
            "build_corpus() excludes the None-type record and the exact-duplicate "
            "user_request, result <= limit"
        )
    else:
        fail("build-corpus-dedup-cap", f"result={result!r} checks={checks!r}")


# ---------------------------------------------------------------------------
# _read_workflow_artifacts()
# ---------------------------------------------------------------------------


def test_read_workflow_artifacts_skips_malformed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workflows_dir = Path(tmp)
        (workflows_dir / "wf-a.json").write_text(
            json.dumps(
                {
                    "workflow_uuid": "wf-a",
                    "workflow_type": "BUILD",
                    "user_request": "do the thing",
                }
            ),
            encoding="utf-8",
        )
        (workflows_dir / "wf-b.json").write_text(
            json.dumps(
                {
                    "workflow_uuid": "wf-b",
                    "workflow_type": "PLAN",
                    "user_request": "plan the thing",
                }
            ),
            encoding="utf-8",
        )
        (workflows_dir / "wf-c.json").write_text("{not valid json", encoding="utf-8")

        try:
            records = _read_workflow_artifacts(workflows_dir)
        except Exception as exc:  # pragma: no cover -- the whole point of this test
            fail(
                "read-workflow-artifacts-skips-malformed",
                f"_read_workflow_artifacts() raised: {exc!r}",
            )
            return

    if len(records) == 2 and {r[0] for r in records} == {"wf-a", "wf-b"}:
        ok(
            "_read_workflow_artifacts() returns exactly the 2 valid records and "
            "never raises on corrupt JSON"
        )
    else:
        fail("read-workflow-artifacts-skips-malformed", f"records={records!r}")


def main() -> int:
    print("test_craftflow_jev_corpus_build: running")
    test_build_corpus_dedups_and_caps()
    test_read_workflow_artifacts_skips_malformed()

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
    raise SystemExit(main())
