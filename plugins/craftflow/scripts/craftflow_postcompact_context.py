#!/usr/bin/env python3
import json
from datetime import datetime, timezone

from craftflow_hooklib import (
    latest_workflow_payload,
    load_input,
    load_mode,
    log_event,
    workflows_dir,
)
import craftflow_status_report as status_report

# PostCompact's registered timeout is 10s (hooks/hooks.json) -- more
# headroom than PreCompact's since this hook does comparatively little
# other work. Drift-guard test:
# test_postcompact_context_usage_budget_stays_under_registered_hook_timeout
# (craftflow_hook_unit_tests.py) reads the real hooks/hooks.json value.
POSTCOMPACT_CONTEXT_USAGE_TIMEOUT_SECONDS = 2


def _build_event(wf: str, trigger: str, summary: str, context_usage) -> dict:
    """Pure event-shape builder (isolated for direct unit testing)."""
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "wf": wf,
        "event": "compact_occurred",
        "phase": "unknown",
        "task_id": None,
        "agent": "hook",
        "decision": "logged",
        "reason": trigger,
        "details": summary[:200] if summary else None,
        # Best-effort measured context % AFTER this compaction completed,
        # alongside `details` (compact_summary) -- paired with
        # craftflow_precompact_state.py's before-snapshot forms a measured
        # before/after delta. None when tokentracker is unavailable/failed.
        "context_usage": context_usage,
    }


def main() -> int:
    data = load_input()
    trigger = data.get("trigger", "auto")
    summary = data.get("compact_summary", "") or ""
    mode = load_mode()

    payload = latest_workflow_payload()
    if not payload:
        return 0

    wf = payload.get("workflow_uuid") or payload.get("workflow_id")
    events_path = workflows_dir() / f"{wf}.events.jsonl"

    try:
        context_usage = status_report._context_usage(timeout=POSTCOMPACT_CONTEXT_USAGE_TIMEOUT_SECONDS)
    except Exception:
        context_usage = None

    event = _build_event(wf, trigger, summary, context_usage)
    try:
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=True) + "\n")
    except Exception:
        pass  # never fail the hook

    log_event(
        "plugin_postcompact_context",
        {
            "wf": wf,
            "trigger": trigger,
            "summary_len": len(summary),
            "mode": mode.get("postcompactAudit", "audit"),
            "task_id": None,
            "agent": "hook",
            "event": "compact_occurred",
            "decision": "logged",
            "reason": trigger,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
