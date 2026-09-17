#!/usr/bin/env python3
from craftflow_hooklib import (
    latest_workflow_payload,
    load_input,
    log_event,
    read_precompact_snapshot,
    session_context,
)


def main() -> int:
    data = load_input()
    source = data.get("source", "startup")
    payload = latest_workflow_payload()
    if not payload:
        return 0

    pending = payload.get("pending_gate") or "none"
    phase_status = payload.get("phase_status") or {}
    incomplete = [
        name
        for name, status in phase_status.items()
        if status not in {"completed", "skipped"}
    ]
    overall_quality = (payload.get("research_quality") or {}).get("overall", "none")
    workflow_uuid = payload.get("workflow_uuid") or payload.get("workflow_id")
    message = (
        f"CRAFTFLOW v10 workflow context ({source}): "
        f"wf={workflow_uuid} type={payload.get('workflow_type')} "
        f"plan={payload.get('plan_file') or 'N/A'} design={payload.get('design_file') or 'N/A'} "
        f"phase_cursor={payload.get('phase_cursor') or 'none'} "
        f"research_quality={overall_quality} pending_gate={pending} "
        f"incomplete_phases={', '.join(incomplete) if incomplete else 'none'}."
    )

    if source == "compact":
        try:
            snapshot = read_precompact_snapshot()
        except Exception:
            snapshot = {}
        # Explicit non-empty guard: workflow_uuid may be None/"" on BOTH
        # sides (a malformed current payload and/or a malformed/foreign
        # snapshot) -- None == None must never be treated as a match.
        if workflow_uuid and snapshot.get("workflow_uuid") == workflow_uuid:
            digest = snapshot.get("narrative_digest")
            if digest:
                message = f"{message}\n\n{digest}"

    log_event(
        "plugin_sessionstart_context",
        {
            "wf": workflow_uuid,
            "phase": ",".join(incomplete) if incomplete else "none",
            "task_id": None,
            "agent": "router",
            "event": "session_context",
            "decision": "inject",
            "reason": source,
        },
    )
    session_context(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
