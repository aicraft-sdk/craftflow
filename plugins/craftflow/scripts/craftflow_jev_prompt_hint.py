#!/usr/bin/env python3
"""UserPromptSubmit hook -- optional Jev (TypeSafe AI) routing + skill hint.
OFF BY DEFAULT: inert unless config/jev.json has enabled:true AND TYPESAFE_API_KEY is set.
Never blocks (no decision/blockReason, exit 0 always). Design: docs/plans/2026-09-19-jev-routing-hint-design.md
"""
from __future__ import annotations
import os, sys
from craftflow_hooklib import load_input, log_event, plugin_config_dir
from craftflow_jev_config import is_active, load_config


def main() -> int:
    try:
        data = load_input()
        if data.get("hook_event_name") not in (None, "UserPromptSubmit"):
            return 0
        cfg, decisions = load_config(plugin_config_dir() / "jev.json")
        if ("config", "config_unparseable") in decisions:      # file exists but is corrupt: greppable even when off
            log_event("plugin_jev_prompt_hint", {"event": "jev_config", "key": "config", "decision": "config_unparseable"})
        if not is_active(cfg, os.environ):
            return 0                      # design: no call, no output, no log
        for key, decision in decisions:   # per-key decisions logged once we know the feature is on
            if (key, decision) != ("config", "config_unparseable"):
                log_event("plugin_jev_prompt_hint", {"event": "jev_config", "key": key, "decision": decision})
        return run_active(data, cfg)      # Phase 3
    except Exception as exc:              # never stall the session
        log_event("plugin_jev_prompt_hint", {"event": "jev_hook", "decision": "hook_error", "error": type(exc).__name__})
        return 0


def run_active(data, cfg) -> int:
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
