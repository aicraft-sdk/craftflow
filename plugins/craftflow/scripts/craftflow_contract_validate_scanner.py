#!/usr/bin/env python3
"""
craftflow_contract_validate_scanner.py

Cross-field rules for the architecture-scanner Router Contract that the
field-level schema (schemas/architecture-scanner.json) cannot express. Used by
craftflow_contract_validate_pilot.py when `--workflow-uuid` is given:

- REPORT_PATH must equal `.craftflow/codebase-health/{workflow_uuid}/report.html`
  exactly (project-relative). The router opens that path, so it is pinned.
- CANDIDATE_COUNT equals len(CANDIDATES); the STATUS pairings hold
  (CANDIDATES_FOUND: count >= 1, NEXT_ACTION choose, TOP_RECOMMENDATION null
  or in 1..count, and with the pilot's optional `--project DIR` the report file exists under it; NO_CANDIDATES: count 0, empty list, NEXT_ACTION complete,
  TOP_RECOMMENDATION null).
- Every candidate carries name, files, problem, solution and a known strength.

`scanner_violations(parsed, workflow_uuid)` returns [{"field", "reason"}, ...];
it never raises for a malformed `parsed` (fail-closed: odd shapes are violations).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, List, Optional

REPORT_PATH_TEMPLATE = ".craftflow/codebase-health/{uuid}/report.html"
STRENGTHS = ("Strong", "Worth exploring", "Speculative")
_UUID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def expected_report_path(workflow_uuid: str) -> str:
    return REPORT_PATH_TEMPLATE.format(uuid=workflow_uuid)


def uuid_is_safe(workflow_uuid: Any) -> bool:
    return (
        isinstance(workflow_uuid, str)
        and _UUID_RE.fullmatch(workflow_uuid) is not None
        and ".." not in workflow_uuid
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _candidate_problem(index: int, item: Any) -> str:
    """'' when the candidate item is well formed, else the reason."""
    if not isinstance(item, dict):
        return f"candidate {index} is not a mapping"
    for key in ("name", "problem", "solution"):
        if not isinstance(item.get(key), str) or not item[key].strip():
            return f"candidate {index} has no non-empty string {key}"
    files = item.get("files")
    if not isinstance(files, list) or not files or not all(isinstance(f, str) and f for f in files):
        return f"candidate {index} files must be a non-empty list of strings"
    if item.get("strength") not in STRENGTHS:
        return f"candidate {index} strength {item.get('strength')!r} not in {list(STRENGTHS)}"
    return ""


def scanner_violations(parsed: dict, workflow_uuid: str, project: Optional[str] = None) -> List[dict]:
    out: List[dict] = []

    def bad(field: str, reason: str) -> None:
        out.append({"field": field, "reason": reason})

    if not uuid_is_safe(workflow_uuid):
        bad("<contract>", f"unsafe or empty --workflow-uuid {workflow_uuid!r}")
        return out
    verdict = parsed.get("verdict") if isinstance(parsed, dict) else None
    rationale = parsed.get("rationale") if isinstance(parsed, dict) else None
    if not isinstance(verdict, dict) or not isinstance(rationale, dict):
        return out  # the schema pass already reports the missing sections

    want = expected_report_path(workflow_uuid)
    if rationale.get("REPORT_PATH") != want:
        bad("REPORT_PATH", f"must equal {want!r} exactly, got {rationale.get('REPORT_PATH')!r}")

    cands = rationale.get("CANDIDATES")
    count = verdict.get("CANDIDATE_COUNT")
    status = verdict.get("STATUS")
    top = verdict.get("TOP_RECOMMENDATION")
    action = verdict.get("NEXT_ACTION")
    if not isinstance(cands, list) or not _is_int(count):
        return out  # type errors are the schema pass's job

    if count != len(cands):
        bad("CANDIDATE_COUNT", f"{count} does not equal len(CANDIDATES) {len(cands)}")
    if status == "CANDIDATES_FOUND":
        if count < 1:
            bad("CANDIDATE_COUNT", "CANDIDATES_FOUND needs at least 1 candidate")
        if action != "choose":
            bad("NEXT_ACTION", "CANDIDATES_FOUND needs NEXT_ACTION choose")
        if top is not None and (not _is_int(top) or not 1 <= top <= max(count, 0)):
            bad("TOP_RECOMMENDATION", f"must be null or an integer in 1..{count}, got {top!r}")
        if project is not None and rationale.get("REPORT_PATH") == want and not (Path(project) / want).is_file():
            bad("REPORT_PATH", f"CANDIDATES_FOUND needs the report file on disk at {want!r} under --project")
    elif status == "NO_CANDIDATES":
        if count != 0 or cands:
            bad("CANDIDATE_COUNT", "NO_CANDIDATES needs count 0 and an empty CANDIDATES list")
        if action != "complete":
            bad("NEXT_ACTION", "NO_CANDIDATES needs NEXT_ACTION complete")
        if top is not None:
            bad("TOP_RECOMMENDATION", "NO_CANDIDATES needs TOP_RECOMMENDATION null")

    for index, item in enumerate(cands, start=1):
        reason = _candidate_problem(index, item)
        if reason:
            bad("CANDIDATES", reason)
            break
    return out
