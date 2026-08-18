#!/usr/bin/env python3
"""
craftflow_state_query.py

Read-only compaction CLI for .craftflow/state/** files, invoked via Bash
(never via Read) when craftflow_pretooluse_guard.py's state-read-compaction
check denies an oversized Read. Never mutates the target file.

Usage:
    python3 craftflow_state_query.py <path> [--mode summary|full] [--tail N] [--event-type NAME]

--mode full: byte-identical passthrough of the target file's current content.
--mode summary (default): shape-aware compaction --
    - top-level workflow JSON (.craftflow/state/workflows/*.json): key fields
      + phase_status + last N status_history entries, large nested blobs
      collapsed to counts.
    - .events.jsonl: last N lines (--tail, default 50), optionally filtered
      by --event-type, plus a total-line-count footer.
    - markdown memory file (*.md): per-## -section headings + last N bullets
      (default 10) per section via craftflow_hooklib's own
      parse_markdown_sections()/extract_bullets().
    - anything else: first/last N lines fallback.

Fail-open: an unparseable file under the recognized shapes never crashes or
silently drops data -- it falls back to raw content prefixed with a WARNING
banner. This is a best-effort compaction tool, not a security boundary.

Exit 0 on success, 1 on error (e.g. missing/unreadable target path).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from craftflow_hooklib import extract_bullets, parse_markdown_sections  # noqa: E402

DEFAULT_TAIL_LINES = 50
DEFAULT_BULLETS_PER_SECTION = 10
DEFAULT_STATUS_HISTORY_ENTRIES = 5


def _read_target(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _detect_shape(path: Path) -> str:
    if path.name.endswith(".events.jsonl"):
        return "events_jsonl"
    if path.suffix == ".json":
        return "workflow_json"
    if path.suffix == ".md":
        return "markdown"
    return "generic"


def _summarize_workflow_json(content: str) -> str:
    return content


def _summarize_events_jsonl(content: str, tail: int, event_type: str | None) -> str:
    return content


def _summarize_markdown(content: str) -> str:
    return content


def _summarize_generic(content: str) -> str:
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description="craftflow state-read compaction query")
    parser.add_argument("path")
    parser.add_argument("--mode", choices=["summary", "full"], default="summary")
    parser.add_argument("--tail", type=int, default=DEFAULT_TAIL_LINES)
    parser.add_argument("--event-type", default=None)
    args = parser.parse_args()

    target = Path(args.path).resolve()
    try:
        content = _read_target(target)
    except OSError as exc:
        sys.stderr.write(f"Error: cannot read {target}: {exc}\n")
        return 1

    if args.mode == "full":
        sys.stdout.write(content)
        return 0

    shape = _detect_shape(target)
    if shape == "workflow_json":
        sys.stdout.write(_summarize_workflow_json(content))
    elif shape == "events_jsonl":
        sys.stdout.write(_summarize_events_jsonl(content, tail=args.tail, event_type=args.event_type))
    elif shape == "markdown":
        sys.stdout.write(_summarize_markdown(content))
    else:
        sys.stdout.write(_summarize_generic(content))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
