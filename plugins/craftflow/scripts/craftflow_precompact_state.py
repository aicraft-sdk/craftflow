#!/usr/bin/env python3
"""Save workflow state snapshot before context compaction."""
import json

from craftflow_hooklib import (
    load_input,
    log_event,
    now_iso,
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
