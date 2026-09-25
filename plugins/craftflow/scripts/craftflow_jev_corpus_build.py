#!/usr/bin/env python3
"""Corpus builder -- mine real historical `user_request` + `workflow_type`
pairs from .craftflow/state/workflows/*.json into a reproducible,
stratified, capped corpus JSONL for the jev-vs-no-jev A/B benchmark.

Read-only against workflow artifacts; never mutates them. Import-cheap: no
file reads, env lookups, or argparse parsing at import time; all of that
happens inside main(), which only runs under `if __name__ == "__main__"`.

Run: python3 scripts/craftflow_jev_corpus_build.py --limit 150 --out <path>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from craftflow_hooklib import state_root


# ---------------------------------------------------------------------------
# Pure builder (unit-testable directly -- no I/O).
# ---------------------------------------------------------------------------


def build_corpus(records: List[Tuple[str, "str | None", str]], limit: int) -> List[Dict[str, str]]:
    """Pure: drop rows where workflow_type is None, dedup by exact
    user_request string (keep the first occurrence sorted by workflow_uuid
    ascending), bucket the remaining rows by workflow_type, then
    round-robin across buckets (buckets visited in sorted workflow_type
    order, each bucket walked in workflow_uuid ascending order) until
    `limit` is reached or every bucket is exhausted."""
    typed = [row for row in records if row[1] is not None]
    typed.sort(key=lambda row: row[0])

    seen_requests: "set[str]" = set()
    deduped: List[Tuple[str, str, str]] = []
    for workflow_uuid, workflow_type, user_request in typed:
        if user_request in seen_requests:
            continue
        seen_requests.add(user_request)
        deduped.append((workflow_uuid, workflow_type, user_request))

    buckets: Dict[str, List[Tuple[str, str, str]]] = {}
    for row in deduped:
        buckets.setdefault(row[1], []).append(row)

    bucket_keys = sorted(buckets.keys())
    cursors = {key: 0 for key in bucket_keys}
    result: List[Dict[str, str]] = []
    progressed = True
    while len(result) < limit and progressed:
        progressed = False
        for key in bucket_keys:
            if len(result) >= limit:
                break
            idx = cursors[key]
            bucket = buckets[key]
            if idx >= len(bucket):
                continue
            workflow_uuid, workflow_type, user_request = bucket[idx]
            result.append(
                {
                    "workflow_uuid": workflow_uuid,
                    "workflow_type": workflow_type,
                    "user_request": user_request,
                }
            )
            cursors[key] = idx + 1
            progressed = True
    return result


# ---------------------------------------------------------------------------
# Impure reader + CLI (only reachable from main()).
# ---------------------------------------------------------------------------


def _read_workflow_artifacts(workflows_dir: Path) -> List[Tuple[str, "str | None", str]]:
    """Impure: reads every *.json file directly under workflows_dir. A file
    that fails to parse, is not a JSON object, or lacks a well-typed
    workflow_uuid/user_request is skipped -- this function never raises."""
    records: List[Tuple[str, "str | None", str]] = []
    try:
        paths = sorted(workflows_dir.glob("*.json"))
    except OSError:
        return records
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, MemoryError):
            continue
        if not isinstance(data, dict):
            continue
        workflow_uuid = data.get("workflow_uuid")
        workflow_type = data.get("workflow_type")
        user_request = data.get("user_request")
        if not isinstance(workflow_uuid, str) or not workflow_uuid:
            continue
        if not isinstance(user_request, str) or not user_request:
            continue
        if workflow_type is not None and not isinstance(workflow_type, str):
            continue
        records.append((workflow_uuid, workflow_type, user_request))
    return records


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mine real historical user_request + workflow_type pairs from "
        ".craftflow/state/workflows/*.json into a stratified, capped corpus JSONL "
        "for the jev-vs-no-jev A/B benchmark."
    )
    parser.add_argument("--limit", type=int, default=150)
    parser.add_argument("--out", type=str, required=True)
    parser.add_argument(
        "--workflows-dir",
        type=str,
        default=None,
        help="Override workflow artifacts directory (default: state_root()/workflows)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    workflows_dir = Path(args.workflows_dir) if args.workflows_dir else (state_root() / "workflows")
    records = _read_workflow_artifacts(workflows_dir)
    corpus = build_corpus(records, args.limit)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in corpus:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
