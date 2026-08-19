#!/usr/bin/env python3
"""PostToolUse advisory guard: nudge when the agent runs the exact same tool
call several times in a row (backlog item 3 -- borrowed from
deepseek-harness's `guard/repeat-tool-reminder`).

Craftflow's existing guard family (protected-write, destructive-command,
safe-shell, denial-tracker) all deny or escalate on a *policy* violation.
None of them cover the different failure mode this closes: the agent is not
violating any policy, it is just stuck -- retrying an identical Bash command,
Edit, or Read that isn't getting it anywhere. Since PostToolUse fires after
the tool already executed, there is nothing left to block; this can only
ever add advisory context, never deny (see `posttool_context()` in
craftflow_hooklib.py).

Registered with no `matcher` in hooks.json, so it observes every tool call,
not just Edit/Write/Bash -- a stuck loop can just as easily be three
identical Reads or Greps as three identical Bash commands.
"""
from craftflow_hooklib import (
    load_input,
    log_event,
    posttool_context,
    record_tool_call,
    tool_call_signature,
)


def main() -> int:
    data = load_input()
    tool_name = data.get("tool_name")
    if not tool_name:
        return 0

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    session_id = data.get("session_id")
    signature = tool_call_signature(tool_name, tool_input)
    count, should_notify = record_tool_call(session_id, signature)

    if not should_notify:
        return 0

    log_event(
        "plugin_posttooluse_repeat_guard",
        {
            "session_id": session_id,
            "tool_name": tool_name,
            "count": count,
            "event": "repeat_tool_call_nudge",
            "decision": "advisory",
            "reason": f"{count} consecutive identical {tool_name} calls",
        },
    )

    posttool_context(
        f"CRAFTFLOW notice: this exact {tool_name} call has now run {count} "
        "times in a row with the same input this session. If it keeps "
        "failing the same way, stop and re-diagnose instead of retrying "
        "unchanged -- see the debugging-patterns skill (root-cause-"
        "playbooks.md and the sandbox-vs-real-failure section in "
        "investigation-hygiene.md) before trying again."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
