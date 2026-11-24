#!/usr/bin/env python3
from craftflow_hooklib import load_input, load_mode, log_event


def _final_message(data, agent_transcript_path):
    """(message, source). Claude Code sometimes sends an empty `last_assistant_message` (38 of 40 craftflow agent
    stops in one project on 2026-11-23), which hid every CONTRACT from the audit and from shadow mode. Fall back to the
    agent transcript's final text then. Never raises."""
    message = data.get("last_assistant_message", "") or ""
    if message or not agent_transcript_path:
        return message, "hook_last_message"
    try:
        from craftflow_transcript_usage import summarize_transcript

        text = summarize_transcript(agent_transcript_path, want_final_text=True).get("final_text") or ""
    except Exception:  # fail-open: keep the empty hook message
        text = ""
    return (text, "transcript_final_text") if text else ("", "hook_last_message")


def _log_agent_usage(data, mode, agent_type, agent_id, agent_transcript_path, message,
                     source="hook_last_message") -> None:
    """Log one agent_usage record (SPEC-0014). Fail-open: never raises.

    DD-2: the payload must not contain an "event" key (log_event spreads the
    payload after the event name, so it would silently overwrite it).
    """
    try:
        raw = mode.get("agentUsageTelemetry", "audit")
        if raw == "off":
            return
        mode_str = raw if raw == "audit" else "audit-unrecognized-config-value"

        from craftflow_transcript_usage import (
            SCHEMA_VERSION,
            classify_contract,
            summarize_transcript,
        )

        slug = agent_type.split(":")[-1] if ":" in agent_type else agent_type
        try:
            contract = classify_contract(message, slug)
        except Exception:  # noqa: BLE001 - classifier failure must not drop the usage record
            contract = {
                "contract_shape": None,
                "contract_valid": None,
                "contract_errors": ["classifier_error"],
            }
        summary = summarize_transcript(agent_transcript_path, want_final_text=False)
        log_event(
            "agent_usage",
            {
                "schema": SCHEMA_VERSION,
                "agent_type": agent_type,
                "agent_id": agent_id,
                "session_id": data.get("session_id") or None,
                "stop_hook_active": bool(data.get("stop_hook_active", False)),
                "mode": mode_str,
                "contract_source": source,
                "contract_shape": contract["contract_shape"],
                "contract_valid": contract["contract_valid"],
                "contract_errors": contract["contract_errors"],
                **summary,
            },
        )
    except Exception as exc:  # noqa: BLE001 - telemetry is fail-open by design (DD-3)
        try:
            log_event(
                "agent_usage",
                {
                    "schema": 1,
                    "agent_type": agent_type,
                    "agent_id": agent_id,
                    "error": "unexpected:" + type(exc).__name__,
                },
            )
        except Exception:  # noqa: BLE001 - last resort; still fail-open
            pass


def _log_machine_shadow(mode, agent_type, message) -> None:
    """P8 Stage B (ADR-0082): log what the workflow machine expected next. Audit only; routing never reads it.
    Fail-open: never raises."""
    try:
        if mode.get("machineShadow", "audit") == "off":
            return
        from pathlib import Path

        from craftflow_hooklib import latest_workflow_payload, logs_dir
        from craftflow_machine import POLICY_PATH, load_all
        from craftflow_machine_shadow import hook_observe

        workflow = latest_workflow_payload()
        if not isinstance(workflow, dict) or not workflow.get("workflow_uuid"):
            return
        import json

        policy = json.loads(Path(POLICY_PATH).read_text(encoding="utf-8"))
        row = hook_observe(logs_dir() / "craftflow-hook-events.log", workflow, agent_type, message, load_all(), policy)
        log_event("machine_shadow", row)
    except Exception as exc:  # shadow mode is fail-open by design (ADR-0082)
        try:
            log_event("machine_shadow", {"agent": agent_type, "error": "unexpected:" + type(exc).__name__})
        except Exception:  # last resort; still fail-open
            pass


def main() -> int:
    data = load_input()
    mode = load_mode()
    agent_type = data.get("agent_type", "") or ""
    agent_id = data.get("agent_id", "") or ""
    agent_transcript_path = data.get("agent_transcript_path", "") or ""
    stop_hook_active = data.get("stop_hook_active", False)
    message, message_source = _final_message(data, agent_transcript_path)
    contract_found = "CONTRACT {" in message

    # Only audit craftflow agents to suppress noise from unrelated subagents
    is_craftflow_agent = (
        agent_type.startswith("craftflow:")
        or "CRAFTFLOW" in message
        or "Router Contract" in message
    )
    if not is_craftflow_agent:
        return 0

    contract_valid = None
    contract_errors = []
    contract_warnings = []
    if contract_found:
        validation_mode = mode.get("contractValidation", "audit")
        try:
            from craftflow_contract_validate import validate_contract
            result = validate_contract(message, agent_type.split(":")[-1] if ":" in agent_type else agent_type)
            contract_valid = result.get("valid", False)
            contract_errors = result.get("errors", [])
            contract_warnings = result.get("warnings", [])
        except Exception as exc:
            contract_errors = [f"validator_error: {exc}"]
            contract_valid = None

    log_event(
        "plugin_subagent_stop_audit",
        {
            "agent_type": agent_type,
            "agent_id": agent_id,
            "agent_transcript_path": agent_transcript_path,
            "stop_hook_active": stop_hook_active,
            "contract_found": contract_found,
            "contract_valid": contract_valid,
            "contract_errors": contract_errors,
            "contract_warnings": contract_warnings,
            "message_len": len(message),
            "message_source": message_source,
            "mode": mode.get("subagentStopAudit", "audit"),
            "task_id": None,
            "agent": agent_type,
            "event": "subagent_stop",
            "decision": "logged",
            "reason": "contract_present" if contract_found else "contract_missing",
        },
    )

    if contract_found and contract_valid is False:
        log_event(
            "contract_invalid",
            {
                "agent_type": agent_type,
                "agent_id": agent_id,
                "errors": contract_errors,
                "mode": mode.get("contractValidation", "audit"),
            },
        )

    # Runs after the existing events so they are written even if this is slow.
    _log_agent_usage(data, mode, agent_type, agent_id, agent_transcript_path, message, message_source)
    _log_machine_shadow(mode, agent_type, message)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
