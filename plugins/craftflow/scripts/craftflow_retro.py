#!/usr/bin/env python3
"""craftflow_retro.py -- deterministic signal extractor for one workflow retrospective.

Reads one workflow artifact (<state-dir>/workflows/<id>.json) and its events log
(<state-dir>/workflows/<id>.events.jsonl) and prints friction signals with evidence.

Usage:
  python3 craftflow_retro.py (--wf <workflow-id> | --latest | --list) [--recurrence] [--state-dir .craftflow/state]
  --recurrence also reads every workflow and <state-dir>/project/skill-candidates.json (never writes it).

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
from datetime import datetime, timezone
from pathlib import Path

# One-way import: the signals module is a leaf and never imports this module.
from craftflow_retro_signals import (  # noqa: F401
    EXCERPT_MAX, EXTRACTORS, RetroError, _excerpt, _history_entries, _is_int, _type_name,
)

SCHEMA_VERSION = 1
DEFAULT_STATE_DIR = ".craftflow/state"
WF_ID_RE = re.compile(r"^wf-[A-Za-z0-9][A-Za-z0-9-]*$")
AMBIGUITY_WINDOW_SECONDS = 900
LIST_LIMIT = 5
LIST_REQUEST_MAX = 120
RECURRENCE_EXAMPLES = 3
LEDGER_LIST_LIMIT = 10
LEDGER_SIGNATURE_MAX = 120
LEDGER_GATE_MIN_WORKFLOWS = 2  # mirrors craftflow_skill_ledger.gate_eligible(); parity-tested

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


def run(wf_id: str, state_dir: str, mode: str = "explicit") -> dict:
    if not WF_ID_RE.fullmatch(wf_id):
        raise RetroError("invalid_wf_id", f"invalid workflow id {wf_id!r}")
    base = Path(state_dir) / "workflows"
    artifact_path = base / f"{wf_id}.json"
    events_path = base / f"{wf_id}.events.jsonl"
    artifact = load_artifact(artifact_path)
    events = load_events(events_path)
    result = extract_signals(artifact, events)
    result["selection"] = {"mode": mode}
    result["inputs"] = {
        "artifact": str(artifact_path),
        "events": str(events_path),
        "events_lines": len(events),
    }
    return result


def _sort_time(artifact: dict, path: Path) -> float:
    """Epoch seconds from artifact updated_at (naive = UTC), else file mtime."""
    raw = artifact.get("updated_at")
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
    return path.stat().st_mtime


def _candidates(workflows_dir: Path):
    """Return (candidates newest-first, skipped_unparseable). Candidates are dicts with 'stem', 'ts', 'artifact'."""
    found, skipped = [], 0
    if workflows_dir.is_dir():
        for path in sorted(workflows_dir.glob("wf-*.json")):
            if not path.is_file():
                continue
            try:
                artifact = load_artifact(path)
            except RetroError:
                skipped += 1
                continue
            wf_id = artifact.get("workflow_uuid") or artifact.get("workflow_id")
            found.append({"id": str(wf_id), "stem": path.name[: -len(".json")],
                          "ts": _sort_time(artifact, path), "artifact": artifact})
    found.sort(key=lambda c: (-c["ts"], c["id"]))
    return found, skipped


def _clip(text, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _is_ambiguous(cands: list) -> bool:
    return len(cands) >= 2 and cands[0]["ts"] - cands[1]["ts"] <= AMBIGUITY_WINDOW_SECONDS


def run_latest(state_dir: str) -> dict:
    cands, _ = _candidates(Path(state_dir) / "workflows")
    if not cands:
        raise RetroError("no_workflows", f"no workflow artifacts under {Path(state_dir) / 'workflows'}")
    if _is_ambiguous(cands):
        raise RetroError("ambiguous_selection",
                         f"{cands[0]['id']}, {cands[1]['id']} updated within {AMBIGUITY_WINDOW_SECONDS}s; "
                         f"rerun with --wf <id> (see --list)")
    return run(cands[0]["stem"], state_dir, mode="latest")


def run_list(state_dir: str) -> dict:
    cands, skipped = _candidates(Path(state_dir) / "workflows")
    return {
        "schema_version": SCHEMA_VERSION,
        "candidates": [
            {"workflow_uuid": c["id"], "workflow_type": c["artifact"].get("workflow_type"),
             "updated_at": c["artifact"].get("updated_at"),
             "user_request": _clip(c["artifact"].get("user_request") or "", LIST_REQUEST_MAX)}
            for c in cands[:LIST_LIMIT]
        ],
        "ambiguous": _is_ambiguous(cands),
        "skipped_unparseable": skipped,
    }


def scan_recurrence(state_dir: str, target_stem: str, fired_ids: list) -> dict:
    """Read-only: for each fired signal id, how many parseable workflows (target included) fire it."""
    base = Path(state_dir) / "workflows"
    cands, skipped = _candidates(base)
    hits = {sid: [] for sid in fired_ids}
    scanned = 1
    for c in cands:  # newest-first
        if c["stem"] == target_stem:
            continue
        try:
            out = extract_signals(c["artifact"], load_events(base / f"{c['stem']}.events.jsonl"))
        except (RetroError, ValueError, TypeError, KeyError, AttributeError):
            skipped += 1
            continue
        scanned += 1
        for s in out["signals"]:
            if s["id"] in hits:
                hits[s["id"]].append(c["stem"])
    return {
        "corpus": {"workflows_scanned": scanned, "skipped_unparseable": skipped},
        "signals": [{"id": sid, "workflows": 1 + len(hits[sid]),
                     "other_examples": hits[sid][:RECURRENCE_EXAMPLES]} for sid in fired_ids],
    }


def _skill_distill_eligible(c: dict) -> bool:
    dw = c.get("distinct_workflows")
    return _is_int(dw) and dw >= LEDGER_GATE_MIN_WORKFLOWS and c.get("status") == "candidate"


def ledger_crossref(state_dir: str, target_stem: str) -> dict:
    """Read-only view of skill-candidates.json entries this workflow fed. Never locks or writes."""
    path = Path(state_dir) / "project" / "skill-candidates.json"
    res = {"path": str(path), "status": "absent", "error": None, "candidates": [], "omitted": 0}
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return res
    except (OSError, UnicodeDecodeError) as exc:
        res.update(status="unreadable", error=f"cannot read: {exc}")
        return res
    try:
        data = json.loads(raw)
    except ValueError as exc:
        res.update(status="unreadable", error=f"not valid JSON: {exc}")
        return res
    cands = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(cands, list):
        res.update(status="unreadable", error="candidates is not a list")
        return res
    rows = []
    for c in cands:
        if not isinstance(c, dict) or not isinstance(c.get("workflows"), list) or target_stem not in c["workflows"]:
            continue
        dw = c.get("distinct_workflows")
        rows.append({"id": str(c.get("id")), "status": c.get("status") if isinstance(c.get("status"), str) else None,
                     "distinct_workflows": dw if _is_int(dw) else 0, "surface": _clip(c.get("surface") or "", 60),
                     "signature": _clip(c.get("signature") or "", LEDGER_SIGNATURE_MAX),
                     "skill_distill_eligible": _skill_distill_eligible(c)})
    rows.sort(key=lambda r: (-r["distinct_workflows"], r["id"]))
    res.update(status="ok", candidates=rows[:LEDGER_LIST_LIMIT], omitted=max(0, len(rows) - LEDGER_LIST_LIMIT))
    return res


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only workflow retrospective signal extractor.")
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--wf", help="workflow id (wf-...)")
    group.add_argument("--latest", action="store_true", help="newest workflow (fails if ambiguous)")
    group.add_argument("--list", action="store_true", help="list up to 5 newest candidates")
    parser.add_argument("--recurrence", action="store_true",
                        help="also report read-only cross-workflow recurrence and skill-ledger cross-reference (with --wf/--latest)")
    args = parser.parse_args(argv)
    if args.recurrence and args.list:
        parser.error("--recurrence cannot be used with --list")
    try:
        if args.list:
            result = run_list(args.state_dir)
        elif args.latest:
            result = run_latest(args.state_dir)
        else:
            result = run(args.wf, args.state_dir)
        if args.recurrence and not args.list:
            stem = Path(result["inputs"]["artifact"]).name[: -len(".json")]
            rec = scan_recurrence(args.state_dir, stem, [s["id"] for s in result["signals"]])
            rec["ledger"] = ledger_crossref(args.state_dir, stem)
            result["recurrence"] = rec
    except RetroError as exc:
        sys.stderr.write(f"ERROR: {exc.code}: {exc.message}\n")
        return 1
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
