#!/usr/bin/env python3
from craftflow_hooklib import load_input, load_mode, log_event


def _log_agent_usage(data, mode, agent_type, agent_id, agent_transcript_path, message) -> None:
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
                "contract_source": "hook_last_message",
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


def main() -> int:
    data = load_input()
    mode = load_mode()
    agent_type = data.get("agent_type", "") or ""
    agent_id = data.get("agent_id", "") or ""
    agent_transcript_path = data.get("agent_transcript_path", "") or ""
    stop_hook_active = data.get("stop_hook_active", False)
    message = data.get("last_assistant_message", "") or ""
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
    if contract_found:
        validation_mode = mode.get("contractValidation", "audit")
        try:
            from craftflow_contract_validate import validate_contract
            result = validate_contract(message, agent_type.split(":")[-1] if ":" in agent_type else agent_type)
            contract_valid = result.get("valid", False)
            contract_errors = result.get("errors", [])
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
            "message_len": len(message),
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
    _log_agent_usage(data, mode, agent_type, agent_id, agent_transcript_path, message)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
