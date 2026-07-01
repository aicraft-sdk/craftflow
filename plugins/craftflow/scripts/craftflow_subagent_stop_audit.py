#!/usr/bin/env python3
from craftflow_hooklib import load_input, load_mode, log_event


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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
