#!/usr/bin/env python3
"""Throwaway Stop hook for the LV-6 `stop_hook_active` chain probe (SPEC-0018 / ADR-0055).

Registered ONLY inside a scratch plugin copy by stop_gate_roundtrip.py; it is never part of hooks.json.
Usage (hook command): stop_probe.py <log-file>

Each stop appends one JSON line {"n", "stop_hook_active", "has_last_assistant_message"} to <log-file>.
The first BLOCK_FIRST stops of the session are blocked with a fixed reason so the driver can observe whether
`stop_hook_active` stays true across chained continuations. Later stops are allowed. Fail open: any error
exits 0 without output.
"""
import json
import sys

BLOCK_FIRST = 2
REASON = "Reply with the single word NEXT."


def main(argv):
    if len(argv) < 2:
        return 0
    path = argv[1]
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict) or payload.get("hook_event_name") != "Stop":
            return 0
        try:
            with open(path, "r", encoding="utf-8") as handle:
                seen = sum(1 for line in handle if line.strip())
        except FileNotFoundError:
            seen = 0
        row = {"n": seen + 1, "stop_hook_active": payload.get("stop_hook_active"),
               "has_last_assistant_message": isinstance(payload.get("last_assistant_message"), str)}
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        if seen < BLOCK_FIRST:
            sys.stdout.write(json.dumps({"decision": "block", "reason": REASON}))
    except (OSError, ValueError):
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
