#!/usr/bin/env python3
"""craftflow_retro.py -- deterministic signal extractor for one workflow retrospective.

Reads one workflow artifact (<state-dir>/workflows/<id>.json) and its events log
(<state-dir>/workflows/<id>.events.jsonl) and prints friction signals with evidence.

Usage:
  python3 craftflow_retro.py --wf <workflow-id> [--state-dir .craftflow/state]

Exit codes:
  0  success (JSON on stdout; friction_found may be false)
  1  fail-closed error (empty stdout; stderr is one line "ERROR: <code>: <message>")
  2  usage error (argparse)

IMPORTANT: read-only. This module never writes files and never calls subprocess.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# One-way import: the signals module is a leaf and never imports this module.
from craftflow_retro_signals import (  # noqa: F401
    EXCERPT_MAX, EXTRACTORS, RetroError, _excerpt, _history_entries, _type_name,
)

SCHEMA_VERSION = 1
DEFAULT_STATE_DIR = ".craftflow/state"
WF_ID_RE = re.compile(r"^wf-[A-Za-z0-9][A-Za-z0-9-]*$")
AMBIGUITY_WINDOW_SECONDS = 900
LIST_LIMIT = 5

# Optional containers: None means absent; a wrong non-null type is artifact_shape.
_LIST_FIELDS = ("remediation_history", "status_history")
_DICT_FIELDS = ("telemetry", "circuit_breaker", "phase_status")


def load_artifact(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RetroError("artifact_not_found", f"no workflow artifact at {path}")
    except OSError as exc:
        raise RetroError("artifact_unparseable", f"cannot read {path}: {exc}")
    except UnicodeDecodeError as exc:
        raise RetroError("artifact_unparseable", f"{path} is not valid UTF-8: {exc.reason} at byte {exc.start}")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RetroError("artifact_unparseable", f"{path} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise RetroError("artifact_shape", f"artifact is {_type_name(data)}, expected object")
    if not (data.get("workflow_uuid") or data.get("workflow_id")):
        raise RetroError("artifact_shape", "artifact has neither workflow_uuid nor workflow_id")
    for field in _LIST_FIELDS:
        if data.get(field) is not None and not isinstance(data[field], list):
            raise RetroError("artifact_shape", f"{field} is {_type_name(data[field])}, expected list")
    for field in _DICT_FIELDS:
        if data.get(field) is not None and not isinstance(data[field], dict):
            raise RetroError("artifact_shape", f"{field} is {_type_name(data[field])}, expected object")
    telemetry = data.get("telemetry") or {}
    for field in ("loop_counts", "agent_wall_clock_seconds"):
        if telemetry.get(field) is not None and not isinstance(telemetry[field], dict):
            raise RetroError("artifact_shape", f"telemetry.{field} is {_type_name(telemetry[field])}, expected object")
    return data


def load_events(path: Path) -> list:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise RetroError("events_not_found", f"no events log at {path}")
    except OSError as exc:
        raise RetroError("events_unparseable", f"line 0: cannot read {path}: {exc}")
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        lineno = data[: exc.start].count(b"\n") + 1
        raise RetroError("events_unparseable", f"line {lineno}: not valid UTF-8: {exc.reason} at byte {exc.start}")
    events = []
    # Split on "\n" only: str.splitlines() also breaks on U+2028/U+0085/form-feed inside JSON strings.
    for lineno, line in enumerate(raw.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError as exc:
            raise RetroError("events_unparseable", f"line {lineno}: {exc}")
        if not isinstance(obj, dict):
            raise RetroError("events_shape", f"line {lineno}: event is {_type_name(obj)}, expected object")
        events.append((lineno, obj))
    return events


def extract_signals(artifact: dict, events: list) -> dict:
    """Pure: artifact dict + [(line, event)] -> result dict (selection/inputs filled by caller)."""
    gaps: list = []
    _history_entries(artifact, "status_history", gaps)  # report non-dict entries once
    if not events:
        gaps.append("events log is empty")
    signals = []
    for signal_id, fn in EXTRACTORS:
        sig = fn(artifact, events, gaps)
        if sig is not None:
            if sig.get("id") != signal_id:
                raise RetroError("artifact_shape", f"extractor {signal_id} returned id {sig.get('id')!r}")
            signals.append(sig)
    signals.sort(key=lambda s: (-s["weight"], s["id"]))
    wf_id = artifact.get("workflow_uuid") or artifact.get("workflow_id")
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow": {
            "workflow_uuid": wf_id,
            "workflow_type": artifact.get("workflow_type"),
            "user_request": _excerpt(artifact.get("user_request") or "")[:EXCERPT_MAX],
            "created_at": artifact.get("created_at"),
            "updated_at": artifact.get("updated_at"),
        },
        "selection": {"mode": "explicit"},
        "inputs": {"events_lines": len(events)},
        "friction_found": bool(signals),
        "signals": signals,
        "data_gaps": gaps,
    }


def run(wf_id: str, state_dir: str) -> dict:
    if not WF_ID_RE.fullmatch(wf_id):
        raise RetroError("invalid_wf_id", f"invalid workflow id {wf_id!r}")
    base = Path(state_dir) / "workflows"
    artifact_path = base / f"{wf_id}.json"
    events_path = base / f"{wf_id}.events.jsonl"
    artifact = load_artifact(artifact_path)
    events = load_events(events_path)
    result = extract_signals(artifact, events)
    result["inputs"] = {
        "artifact": str(artifact_path),
        "events": str(events_path),
        "events_lines": len(events),
    }
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only workflow retrospective signal extractor.")
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    parser.add_argument("--wf", required=True, help="workflow id (wf-...)")
    args = parser.parse_args(argv)
    try:
        result = run(args.wf, args.state_dir)
    except RetroError as exc:
        sys.stderr.write(f"ERROR: {exc.code}: {exc.message}\n")
        return 1
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
