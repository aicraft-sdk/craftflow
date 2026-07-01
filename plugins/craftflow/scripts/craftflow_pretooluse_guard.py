#!/usr/bin/env python3
from pathlib import Path

from craftflow_hooklib import (
    has_memory_finalize_permit,
    latest_workflow_payload,
    load_input,
    load_mode,
    log_event,
    pretool_deny,
    project_state_dir,
    state_root,
    workflows_dir,
)


PROTECTED_MEMORY_FILES = ("activeContext.md", "patterns.md", "progress.md")


def _protected_memory_paths() -> set:
    """Return all active memory locations that should be write-guarded."""
    paths: set = set()
    try:
        paths |= {(state_root() / name).resolve() for name in PROTECTED_MEMORY_FILES}
    except Exception:
        pass
    try:
        paths |= {(project_state_dir() / name).resolve() for name in PROTECTED_MEMORY_FILES}
    except Exception:
        pass
    try:
        wf_dir = workflows_dir()
        for name in PROTECTED_MEMORY_FILES:
            for candidate in wf_dir.glob(f"*/{name}"):
                paths.add(candidate.resolve())
    except Exception:
        pass
    return paths


def main() -> int:
    data = load_input()
    mode = load_mode()
    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return 0

    path = Path(file_path).resolve()
    violations = []

    protected_memory = _protected_memory_paths()

    if path in protected_memory:
        violations.append("memory-write")

    if not violations:
        return 0

    workflow = latest_workflow_payload()
    wf_uuid = workflow.get("workflow_uuid") or workflow.get("workflow_id")

    # Router-owned memory finalization: permit token lifts the block for the
    # active workflow so the router can write memory files inline.
    if "memory-write" in violations and has_memory_finalize_permit(wf_uuid):
        log_event(
            "plugin_pretooluse_guard",
            {
                "wf": wf_uuid,
                "phase": workflow.get("pending_gate") or "memory-finalize",
                "task_id": None,
                "agent": "router",
                "tool_name": data.get("tool_name"),
                "path": str(path),
                "event": "pretool_guard",
                "decision": "permit",
                "reason": "memory-write-permitted-by-finalize-token",
            },
        )
        return 0

    log_event(
        "plugin_pretooluse_guard",
        {
            "wf": wf_uuid,
            "phase": workflow.get("pending_gate") or "unknown",
            "task_id": None,
            "agent": "router",
            "tool_name": data.get("tool_name"),
            "path": str(path),
            "event": "pretool_guard",
            "decision": (
                "deny"
                if "memory-write" in violations and mode.get("memoryWrites") == "block"
                else "audit"
            ),
            "reason": ",".join(violations),
        },
    )

    should_block = "memory-write" in violations and mode.get("memoryWrites") == "block"
    if should_block:
        pretool_deny(
            "CRAFTFLOW plugin hook blocked a direct state memory markdown write. Use the router-owned memory finalization path."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
