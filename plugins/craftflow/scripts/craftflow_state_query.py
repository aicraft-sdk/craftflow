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
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return f"WARNING: could not parse as workflow JSON; showing raw content.\n\n{content}"
    if not isinstance(data, dict):
        return f"WARNING: workflow JSON top level is not an object; showing raw content.\n\n{content}"

    summary: dict = {}
    scalar_keys = (
        "workflow_uuid", "workflow_id", "workflow_type", "plan_mode",
        "verification_rigor", "proof_status", "pending_gate", "worktree_path",
        "build_mode",
    )
    for key in scalar_keys:
        if key in data:
            summary[key] = data[key]

    if "phase_status" in data:
        summary["phase_status"] = data["phase_status"]

    status_history = data.get("status_history")
    if isinstance(status_history, list):
        summary["status_history_tail"] = status_history[-DEFAULT_STATUS_HISTORY_ENTRIES:]
        summary["status_history_total_entries"] = len(status_history)

    for large_key in ("normalized_phases", "telemetry", "evidence", "traceability"):
        value = data.get(large_key)
        if isinstance(value, list):
            summary[f"{large_key}_entry_count"] = len(value)
        elif isinstance(value, dict):
            summary[f"{large_key}_key_count"] = len(value)

    summary["_note"] = (
        "This is a compacted summary. Run --mode full for complete, "
        "byte-identical content of this workflow artifact."
    )
    return json.dumps(summary, indent=2, ensure_ascii=True) + "\n"


def _summarize_events_jsonl(content: str, tail: int, event_type: str | None) -> str:
    lines = content.splitlines()
    valid: list = []
    malformed_count = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            malformed_count += 1
            continue
        if event_type is not None and parsed.get("event") != event_type:
            continue
        valid.append(parsed)

    tailed = valid[-tail:] if tail > 0 else valid
    out_lines = [json.dumps(entry, ensure_ascii=True) for entry in tailed]
    footer = (
        f"# {len(lines)} total lines, {len(valid)} valid entries matching filter, "
        f"{malformed_count} malformed lines skipped, showing last {len(tailed)}. "
        "Run --mode full for the complete file."
    )
    return "\n".join(out_lines) + ("\n" if out_lines else "") + footer + "\n"


def _summarize_markdown(content: str) -> str:
    sections = parse_markdown_sections(content)
    if not sections:
        return (
            "WARNING: no '## ' headings found; falling back to generic "
            "first/last-line summary.\n\n" + _summarize_generic(content)
        )

    out: list = ["# (compacted summary -- run --mode full for complete content)\n"]
    for heading, body in sections.items():
        out.append(f"## {heading}")
        bullets = extract_bullets(body)
        if not bullets:
            # Non-bullet section body (e.g. free text like "## Current Focus")
            # -- keep it verbatim, it is usually already short.
            out.append(body.strip())
            continue
        shown = bullets[-DEFAULT_BULLETS_PER_SECTION:]
        out.extend(shown)
        if len(bullets) > len(shown):
            out.append(f"... ({len(bullets)} total bullets, {len(shown)} most recent shown)")
        out.append("")
    return "\n".join(out) + "\n"


def _summarize_generic(content: str) -> str:
    lines = content.splitlines()
    head_n, tail_n = 20, 20
    if len(lines) <= head_n + tail_n:
        return content
    head = lines[:head_n]
    tail = lines[-tail_n:]
    omitted = len(lines) - head_n - tail_n
    return (
        "\n".join(head)
        + f"\n\n... ({omitted} lines omitted) ...\n\n"
        + "\n".join(tail)
        + "\n\nRun --mode full for the complete content.\n"
    )


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
