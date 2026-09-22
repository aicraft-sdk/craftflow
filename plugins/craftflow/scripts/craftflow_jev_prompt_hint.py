#!/usr/bin/env python3
"""UserPromptSubmit hook -- optional Jev (TypeSafe AI) routing + skill hint.
OFF BY DEFAULT: inert unless config/jev.json has enabled:true AND TYPESAFE_API_KEY is set.
Never blocks (no decision/blockReason, exit 0 always). Design: docs/plans/2026-09-19-jev-routing-hint-design.md
"""
from __future__ import annotations
import os, sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from craftflow_hooklib import load_input, log_event, now_iso, plugin_config_dir
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


# ---------------------------------------------------------------------------
# Task 3.1: pure builders (no I/O, no network, no env access) -- roster,
# state, questions, gating, rendering, telemetry rows. DD-6/DD-7/DD-8/DD-9.
# ---------------------------------------------------------------------------

# DD-7 roster: host/ops skills that are never suggested even when their
# description does not start with "Internal skill" (session-memory IS
# self-described "Internal skill" and is already covered by that check).
HOST_OPS_SKILLS = frozenset({"craftflow-router", "cursor-router", "status", "update"})
ROSTER_CAP = 254  # + "none" = 255 (DD-7)
DESCRIPTION_CAP_CHARS = 200

# DD-6: workflow choice descriptions, copied from the protocol table's
# keyword columns (skills/_shared/router-protocol.md Intent Routing table).
WORKFLOWS: Dict[str, str] = {
    "DEBUG": "Fix an error, bug, crash, failing test, or broken behavior; troubleshoot an issue",
    "PLAN": "Plan, design, architect, roadmap, strategy, spec, or brainstorm",
    "REVIEW": "Review, audit, analyze, or assess existing code; advisory only",
    "BUILD": "Everything else: implement, add, create, write, change, refactor",
}

# DD-8: telemetry row keys shared by both features; routing rows additionally
# carry "agree_risk". Never includes prompt text.
TELEMETRY_KEYS = frozenset({
    "ts", "call_id", "session_id", "feature", "mode", "model", "latency_ms",
    "cache_hit", "usage", "answers", "confidence", "heuristic_result",
    "agree", "injected", "prompt_chars", "prompt_truncated", "roster_size",
})

_NEEDS_SKILL_THRESHOLD = 0.5
_RISK_NOUL_THRESHOLD = 0.5


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def build_roster(
    project: List[Tuple[str, str]],
    plugin: List[Tuple[str, str]],
    hint_bullets: List[str],
    with_truncation_flag: bool = False,
):
    """DD-7: project skills first, then plugin skills (excluding those whose
    description starts with "Internal skill" or whose name is a host/ops
    skill), both sorted by name, then deduped `patterns.md` hint bullets,
    capped at 254 real entries + trailing "none"."""
    project_sorted = sorted(project, key=lambda item: item[0])
    plugin_filtered = [
        (name, desc)
        for name, desc in plugin
        if not (desc or "").strip().startswith("Internal skill") and name not in HOST_OPS_SKILLS
    ]
    plugin_sorted = sorted(plugin_filtered, key=lambda item: item[0])

    entries: List[Dict[str, str]] = [
        {"id": name, "description": (desc or "")[:DESCRIPTION_CAP_CHARS]} for name, desc in project_sorted
    ]
    entries += [
        {"id": "craftflow:" + name, "description": (desc or "")[:DESCRIPTION_CAP_CHARS]}
        for name, desc in plugin_sorted
    ]

    seen = {entry["id"] for entry in entries}
    for bullet in hint_bullets:
        if isinstance(bullet, str) and bullet and bullet not in seen:
            entries.append({"id": bullet, "description": ""})
            seen.add(bullet)

    truncated = False
    if len(entries) > ROSTER_CAP:
        entries = entries[:ROSTER_CAP]
        truncated = True
    entries.append({"id": "none", "description": "No specialized skill fits this request"})

    if with_truncation_flag:
        return entries, truncated
    return entries


def build_state(prompt: str, *, cwd: str, workflow_type: Optional[str], max_chars: int) -> Dict[str, Any]:
    """DD-5: state sent to Jev -- capped prompt, cwd BASENAME only (data
    minimization, not the full path), and the active workflow type."""
    truncated = len(prompt) > max_chars
    return {
        "prompt": prompt[:max_chars],
        "prompt_truncated": truncated,
        "project": Path(cwd).name if cwd else "",
        "active_workflow_type": workflow_type,
    }


def build_questions(roster: List[Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
    """DD-6: one batched request, ids are caller-only, full question text
    goes in `instructions`. `skill.criteria` is built from the roster --
    Jev only ever picks from candidates built in code."""
    skill_criteria = {entry["id"]: entry["description"] for entry in roster}
    return {
        "workflow": {
            "type": "choice",
            "instructions": "Classify the intent of `prompt` into exactly one workflow.",
            "criteria": dict(WORKFLOWS),
        },
        "risk_full_chain": {
            "type": "noul",
            "instructions": (
                "How likely does `prompt` touch security, payments, schema/migration, or "
                "irreversible production data changes? 0 = no risk, 1 = certain full-chain risk."
            ),
        },
        "needs_skill": {
            "type": "noul",
            "instructions": (
                "How likely does `prompt` need one of the specialized skills listed in "
                "`skill.criteria`? 0 = no, 1 = certain."
            ),
        },
        "skill": {
            "type": "choice",
            "instructions": "Pick the single best-fit specialized skill for `prompt`, or `none`.",
            "criteria": skill_criteria,
        },
    }


def gate_answers(answers: Any, cfg: Dict[str, Any], roster_ids) -> List[str]:
    """DD-9 injection gate (advise only). Never raises on partial/malformed
    answers -- a gate that cannot be satisfied simply contributes no line."""
    if not isinstance(answers, dict) or not isinstance(cfg, dict):
        return []
    modes = cfg.get("features") if isinstance(cfg.get("features"), dict) else {}
    thresholds = cfg.get("thresholds") if isinstance(cfg.get("thresholds"), dict) else {}
    lines: List[str] = []

    workflow = answers.get("workflow")
    if (
        modes.get("routingHint") == "advise"
        and isinstance(workflow, dict)
        and isinstance(workflow.get("choice"), str)
        and _is_number(workflow.get("confidence"))
        and workflow["confidence"] >= thresholds.get("routing", 2.0)
    ):
        line = f"workflow: {workflow['choice']} (confidence {workflow['confidence']:.2f})"
        risk = answers.get("risk_full_chain")
        risk_noul = risk.get("noul") if isinstance(risk, dict) else None
        if _is_number(risk_noul):
            line += f" | risk_full_chain: {risk_noul:.2f}"
        lines.append(line)

    needs_skill = answers.get("needs_skill")
    skill = answers.get("skill")
    if (
        modes.get("skillHint") == "advise"
        and isinstance(needs_skill, dict)
        and _is_number(needs_skill.get("noul"))
        and needs_skill["noul"] >= _NEEDS_SKILL_THRESHOLD
        and isinstance(skill, dict)
        and isinstance(skill.get("choice"), str)
        and skill["choice"] != "none"
        and skill["choice"] in roster_ids
        and _is_number(skill.get("confidence"))
        and skill["confidence"] >= thresholds.get("skill", 2.0)
    ):
        lines.append(f"skill: {skill['choice']} ({skill['confidence']:.2f})")

    return lines


def render_block(lines: List[str], model: str) -> str:
    body = "\n".join(lines)
    return (
        f'<craftflow_routing_hint source="jev" model="{model}">\n'
        f"{body}\n"
        "Advisory. ERROR keyword signals still take precedence. Ignore if it does not fit the request.\n"
        "</craftflow_routing_hint>"
    )


def telemetry_rows(
    result: Dict[str, Any], heuristic: Dict[str, Any], cfg: Dict[str, Any], meta: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """DD-8: one row per feature per prompt. Never includes prompt text."""
    result = result if isinstance(result, dict) else {}
    answers = result.get("answers") if isinstance(result.get("answers"), dict) else {}
    heuristic = heuristic if isinstance(heuristic, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    modes = cfg.get("features") if isinstance(cfg, dict) and isinstance(cfg.get("features"), dict) else {}

    common = {
        "ts": now_iso(),
        "call_id": meta.get("call_id", ""),
        "session_id": meta.get("session_id"),
        "mode": modes,
        "model": result.get("model"),
        "latency_ms": result.get("latency_ms"),
        "cache_hit": result.get("cache_hit"),
        "usage": result.get("usage"),
        "injected": bool(meta.get("injected")),
        "prompt_chars": meta.get("prompt_chars"),
        "prompt_truncated": meta.get("prompt_truncated"),
        "roster_size": meta.get("roster_size"),
    }

    workflow = answers.get("workflow") if isinstance(answers.get("workflow"), dict) else {}
    risk = answers.get("risk_full_chain") if isinstance(answers.get("risk_full_chain"), dict) else {}
    risk_noul = risk.get("noul")
    jev_risk = _is_number(risk_noul) and risk_noul >= _RISK_NOUL_THRESHOLD
    heuristic_risk = bool(heuristic.get("risk_signals"))

    routing_row = dict(common)
    routing_row.update({
        "feature": "routing",
        "answers": workflow,
        "confidence": workflow.get("confidence") if _is_number(workflow.get("confidence")) else 0.0,
        "heuristic_result": {"workflow": heuristic.get("workflow"), "risk_signals": heuristic.get("risk_signals", [])},
        "agree": workflow.get("choice") == heuristic.get("workflow"),
        "agree_risk": jev_risk == heuristic_risk,
    })

    skill = answers.get("skill") if isinstance(answers.get("skill"), dict) else {}
    skill_row = dict(common)
    skill_row.update({
        "feature": "skill",
        "answers": skill,
        "confidence": skill.get("confidence") if _is_number(skill.get("confidence")) else 0.0,
        "heuristic_result": heuristic.get("skill"),
        "agree": skill.get("choice") == heuristic.get("skill"),
    })

    return [routing_row, skill_row]


def run_active(data, cfg) -> int:
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
