#!/usr/bin/env python3
"""Save workflow state snapshot before context compaction."""
import json
import re
import subprocess
import sys
from pathlib import Path

from craftflow_hooklib import (
    extract_bullets,
    load_input,
    log_event,
    now_iso,
    parse_markdown_sections,
    project_state_dir,
    read_latest_workflow_state,
    state_root,
)
import craftflow_status_report as status_report

# PreCompact's registered timeout is 5s (hooks/hooks.json). This must leave
# headroom for the snapshot-write work this hook still needs to do after the
# subprocess call returns, so it stays well under that ceiling -- mirrors
# craftflow_hook_selfcheck.py's PER_SCRIPT_TIMEOUT_SECONDS-style documented
# arithmetic. Drift-guard test:
# test_precompact_context_usage_budget_stays_under_registered_hook_timeout
# (craftflow_hook_unit_tests.py) reads the real hooks/hooks.json value.
PRECOMPACT_CONTEXT_USAGE_TIMEOUT_SECONDS = 1.5

# Budget for the new narrative-digest subprocess call, alongside the
# existing 1.5s context-usage budget above. Combined (3.5s) leaves >=1.5s
# margin under PreCompact's registered 5s hook timeout (hooks/hooks.json)
# for this hook's own snapshot-write work. Drift-guard test:
# test_precompact_narrative_digest_budget_stays_under_registered_hook_timeout
PRECOMPACT_NARRATIVE_DIGEST_TIMEOUT_SECONDS = 2.0

NARRATIVE_DIGEST_MAX_CHARS = 800
NARRATIVE_DIGEST_TRUNCATION_MARKER = " …[digest truncated]"

# Which activeContext.md sections feed the digest, and how many of their
# most-recent entries to keep. `None` means "keep the whole (already
# summarizer-truncated) section body unchanged" -- Next Steps items are
# priority-ordered, not chronological, so trimming to "most recent N"
# would not make sense there the way it does for Current Focus/Decisions.
NARRATIVE_DIGEST_SECTIONS = (
    ("Current Focus", 1),
    ("Next Steps", None),
    ("Decisions", 3),
)


def _section_entries(body: str, limit: "int | None") -> str:
    """Return up to `limit` most-recent entries from a
    craftflow_state_query.py `--mode summary` section body.

    `limit=None` returns the whole (already-truncated-by-the-summarizer)
    body unchanged (stripped).

    Handles BOTH of craftflow_state_query.py's own markdown-summarizer
    output shapes without modifying that script: blank-line-separated
    paragraph blocks (activeContext.md's real Current Focus/Next
    Steps/Decisions convention today -- dated/numbered prose entries,
    newest first) are tried first; a "- "-bulleted body (extract_bullets()
    shape, used by other sections/files) is the defensive fallback,
    keeping the LAST `limit` bullets (matching craftflow_state_query.py's
    own bullets[-N:] "most recent" tail-truncation convention). Returns
    "" for an empty/whitespace-only body."""
    if limit is None:
        return body.strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    # Genuine multi-paragraph shape (blank-line separated, newest first) --
    # this repo's real activeContext.md convention.
    if len(paragraphs) > 1:
        return "\n\n".join(paragraphs[:limit])
    # No blank-line separation found: a bulleted body ("- " lines with no
    # blank lines between them) collapses to a single "paragraph" above, so
    # it must be distinguished here rather than trusting `paragraphs`
    # truthiness alone.
    bullets = extract_bullets(body)
    if bullets:
        return "\n".join(bullets[-limit:])
    if paragraphs:
        return "\n\n".join(paragraphs[:limit])
    return ""


def _build_snapshot(payload: dict, trigger: str, context_usage) -> dict:
    """Pure snapshot-shape builder (isolated for direct unit testing)."""
    wf = payload.get("workflow_uuid") or payload.get("workflow_id")
    return {
        "ts": now_iso(),
        "workflow_uuid": wf,
        "workflow_type": payload.get("workflow_type"),
        "phase_cursor": payload.get("phase_cursor"),
        "phase_status": payload.get("phase_status") or {},
        "plan_file": payload.get("plan_file"),
        "source": "precompact",
        "trigger": trigger,
        # Best-effort measured context % BEFORE this compaction fired (why it
        # fired). None when tokentracker is unavailable/failed -- never
        # blocks the snapshot itself.
        "context_usage": context_usage,
    }


def main() -> int:
    try:
        data = load_input()
    except Exception:
        return 0
    payload, _, parse_error = read_latest_workflow_state()
    if not payload or parse_error:
        return 0

    wf = payload.get("workflow_uuid") or payload.get("workflow_id")
    if not wf:
        return 0

    try:
        context_usage = status_report._context_usage(timeout=PRECOMPACT_CONTEXT_USAGE_TIMEOUT_SECONDS)
    except Exception:
        context_usage = None

    snapshot = _build_snapshot(payload, data.get("trigger", "auto"), context_usage)
    try:
        out = state_root() / "precompact-state.json"
        out.write_text(json.dumps(snapshot, ensure_ascii=True), encoding="utf-8")
    except Exception:
        pass  # never fail the hook

    log_event(
        "plugin_precompact_state",
        {
            "wf": wf,
            "phase": payload.get("phase_cursor") or "none",
            "task_id": None,
            "agent": "hook",
            "event": "precompact_state_saved",
            "decision": "saved",
            "reason": data.get("trigger", "auto"),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
