#!/usr/bin/env python3
"""UserPromptSubmit hook -- optional Jev (TypeSafe AI) routing + skill hint.
OFF BY DEFAULT: inert unless config/jev.json has enabled:true AND TYPESAFE_API_KEY is set.
Never blocks (no decision/blockReason, exit 0 always). Design: docs/plans/2026-09-19-jev-routing-hint-design.md
"""
from __future__ import annotations
import json, os, re, sys, uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from craftflow_hooklib import (
    extract_bullets,
    json_print,
    load_input,
    log_event,
    now_iso,
    parse_markdown_sections,
    plugin_config_dir,
    plugin_root,
    project_dir,
    read_latest_workflow_state,
    state_root,
)
from craftflow_jev_config import api_key, consent_status, is_active, load_config
from craftflow_jev_client import call as jev_call
from craftflow_jev_heuristic import classify
from craftflow_jev_session_cache import read_session_status
from craftflow_skill_promote import parse_frontmatter


def main() -> int:
    try:
        data = load_input()
        if data.get("hook_event_name") not in (None, "UserPromptSubmit"):
            return 0
        cfg, decisions = load_config(plugin_config_dir() / "jev.json")
        if ("config", "config_unparseable") in decisions:      # file exists but is corrupt: greppable even when off
            log_event("plugin_jev_prompt_hint", {"event": "jev_config", "key": "config", "decision": "config_unparseable"})
        session_id = data.get("session_id") if isinstance(data.get("session_id"), str) else None
        if not session_is_active(cfg, os.environ, session_id):
            return 0                      # design: no call, no output, no log
        for key, decision in decisions:   # per-key decisions logged once we know the feature is on
            if (key, decision) != ("config", "config_unparseable"):
                log_event("plugin_jev_prompt_hint", {"event": "jev_config", "key": key, "decision": decision})
        return run_active(data, cfg)      # Phase 3
    except Exception as exc:              # never stall the session
        log_event("plugin_jev_prompt_hint", {"event": "jev_hook", "decision": "hook_error", "error": type(exc).__name__})
        return 0


def session_is_active(cfg: Dict[str, Any], env: Dict[str, str], session_id: Optional[str]) -> bool:
    """DD-2/DD-5 auto-detect OR-gate. `is_active()` (unchanged) is checked
    FIRST so the manual `enabled:true` path is fully preserved. Never raises
    -- a missing/corrupt/mismatched session cache degrades to False.

    CRITICAL fix (silent-failure-hunter on commit f85bccf): current consent
    status is re-checked LIVE on every call (`cfg` is reloaded fresh per
    prompt by `main()`), BEFORE the session cache is even consulted. A
    session-cache entry can only ever be written `active: True` while
    consent was `granted` (see `craftflow_jev_session_check.py`'s
    precedence order), but that cached value is never re-validated against
    the CURRENT consent status on its own -- without this check, revoking
    consent mid-session (`--record-consent declined`) would not stop
    prompts from still being sent to Jev for the rest of that session,
    since a stale `active: true` cache entry from before the revocation
    would keep satisfying the OR-gate. This check must run on every call,
    not be cached itself, so a mid-session revocation takes effect on the
    very next prompt regardless of what is in the session cache.

    HIGH fix (re-hunt on commit 0143ce6): gate on `!= "granted"`, not
    `== "declined"`. The sole writer of `active: True` cache entries
    (`_run_canary`) only ever runs when status is exactly "granted" --
    checking the inverse polarity is symmetric with that precondition and
    closes both the explicit-decline case AND the case where a corrupted/
    malformed config/jev.json makes craftflow_jev_config.normalize() fail
    consent.status open to "unset" (not "declined") mid-session, which
    would otherwise still let a stale cache entry through."""
    if is_active(cfg, env):
        return True
    if consent_status(cfg) != "granted":
        return False
    key = api_key(env)
    if not key or not session_id:
        return False
    try:
        cached = read_session_status(state_root(), session_id)
    except Exception:
        # MEDIUM fix (silent-failure-hunter): state_root()/read_session_status()
        # can raise (e.g. OSError on an unwritable directory) -- this function
        # is documented as "never raises" and must be genuinely exception-safe
        # on its own, not merely safe by virtue of main()'s outer try/except.
        return False
    return bool(cached and cached.get("active") is True)


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

# Security fix (doubt-verifier finding): a roster-sourced skill id is only
# membership-validated downstream (gate_answers checks `choice in roster_ids`),
# never content-validated. A hostile id containing a newline and the literal
# `</craftflow_routing_hint>` tag can break out of the injected block and add
# fabricated instructions to the agent's context. Reject any candidate id
# (skill id or patterns.md hint bullet) at roster-build time -- before it ever
# enters `roster_ids` -- if it contains a newline, carriage return, an angle
# bracket, or the literal substring "craftflow_routing_hint" (case-insensitive).
# Fail-open: a rejected entry is simply excluded from the roster, never a crash.
_HOSTILE_TAG_SUBSTRING = "craftflow_routing_hint"


def _is_safe_roster_id(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if "\n" in value or "\r" in value or "<" in value or ">" in value:
        return False
    if _HOSTILE_TAG_SUBSTRING in value.lower():
        return False
    return True

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
    project_safe = [(name, desc) for name, desc in project if _is_safe_roster_id(name)]
    project_sorted = sorted(project_safe, key=lambda item: item[0])
    plugin_filtered = [
        (name, desc)
        for name, desc in plugin
        if not (desc or "").strip().startswith("Internal skill")
        and name not in HOST_OPS_SKILLS
        and _is_safe_roster_id(name)
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
        if _is_safe_roster_id(bullet) and bullet not in seen:
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
        and workflow["choice"] in WORKFLOWS
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


_CLOSE_TAG_RE = re.compile(r"</\s*craftflow_routing_hint\s*>", re.IGNORECASE)


def render_block(lines: List[str], model: str) -> str:
    # Defensive escaping (belt-and-suspenders): even if a hostile value ever
    # reached this function directly, no single line may inject a raw newline
    # (which could fabricate additional lines / tags) or the closing tag
    # substring (which could break out of the block early).
    safe_lines: List[str] = []
    for line in lines:
        text = str(line).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        text = _CLOSE_TAG_RE.sub("", text)
        safe_lines.append(text)
    body = "\n".join(safe_lines)
    # `model` is untrusted too -- it is echoed straight from the Jev API JSON
    # response (see run_active(): result.get("model") or cfg["model"]) with no
    # validation. Sanitize the same way `lines` are sanitized above: strip
    # newlines (tag-count-parity breakout) and strip `<`/`>`/`"` (quote-
    # attribute breakout that escapes the model="..." attribute entirely).
    safe_model = (
        str(model)
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace('"', "'")
        .replace("<", "")
        .replace(">", "")
    )
    return (
        f'<craftflow_routing_hint source="jev" model="{safe_model}">\n'
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


# ---------------------------------------------------------------------------
# Task 3.2: run_active() -- the I/O shell around the pure core above.
# Endpoint resolution lives in craftflow_jev_client (DD-14), not here.
# ---------------------------------------------------------------------------


def _read_skill_dir(skills_root: Path) -> List[Tuple[str, str]]:
    """Read every `<skills_root>/*/SKILL.md`, returning [(name, description)].
    Never raises -- an unreadable/malformed file is simply skipped."""
    found: List[Tuple[str, str]] = []
    try:
        paths = sorted(skills_root.glob("*/SKILL.md"))
    except Exception:
        return []
    for path in paths:
        try:
            fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue
        raw_name = fm.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else path.parent.name
        if not _is_safe_roster_id(name):
            continue  # build-time sanitization: hostile id, fail-open (skip, don't crash)
        desc = fm.get("description") if isinstance(fm.get("description"), str) else ""
        found.append((name, desc))
    return found


def _read_roster_sources() -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]], List[str]]:
    """DD-7 sources: project `.claude/skills/*/SKILL.md`, plugin
    `skills/*/SKILL.md`, and `patterns.md ## Project SKILL_HINTS` bullets
    (session-memory contract, finding 10): the durable project tier
    (`state_root()/project/patterns.md`) is read first; only when that file
    is absent is the root-flat back-compat fallback (`state_root()/patterns.md`)
    read instead -- never both, never merged."""
    project_skills = _read_skill_dir(project_dir() / ".claude" / "skills")
    plugin_skills = _read_skill_dir(plugin_root() / "skills")

    hint_bullets: List[str] = []
    try:
        project_patterns = state_root() / "project" / "patterns.md"
        if project_patterns.exists():
            text = project_patterns.read_text(encoding="utf-8")
        else:
            root_patterns = state_root() / "patterns.md"
            text = root_patterns.read_text(encoding="utf-8") if root_patterns.exists() else ""
        section = parse_markdown_sections(text).get("Project SKILL_HINTS", "")
        for line in extract_bullets(section):
            stripped = line.lstrip().lstrip("-").strip()
            token = stripped.split()[0] if stripped else ""
            if token and _is_safe_roster_id(token):  # same injection surface as skill ids
                hint_bullets.append(token)
    except Exception:
        hint_bullets = []

    return project_skills, plugin_skills, hint_bullets


def _append_events(path: Path, rows: List[Dict[str, Any]]) -> None:
    """Append telemetry rows to `.craftflow/state/jev/events.jsonl`. Never
    raises -- a write failure here must not fail the hook."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=True) + "\n")
    except Exception:
        pass


def run_active(data: Dict[str, Any], cfg: Dict[str, Any]) -> int:
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or prompt.lstrip().startswith("/"):
        return 0
    modes = cfg.get("features") if isinstance(cfg.get("features"), dict) else {}
    if modes.get("routingHint") == "off" and modes.get("skillHint") == "off":
        return 0
    key = api_key(os.environ)
    if not key:                                # defense-in-depth: main() already gates on this
        return 0

    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else str(project_dir())
    payload, _, _ = read_latest_workflow_state()               # never raises; {} when none
    wf_type = payload.get("workflow_type") if isinstance(payload, dict) else None

    project_skills, plugin_skills, hint_bullets = _read_roster_sources()
    roster, truncated = build_roster(project_skills, plugin_skills, hint_bullets, with_truncation_flag=True)
    if truncated:
        log_event("plugin_jev_prompt_hint", {"event": "jev_roster", "decision": "roster_truncated", "size": len(roster)})

    state = build_state(prompt, cwd=cwd, workflow_type=wf_type, max_chars=cfg["maxStateChars"])
    questions = build_questions(roster)

    result = jev_call(
        state,
        questions,
        api_key=key,
        model=cfg["model"],
        timeout=cfg["timeoutSeconds"],
        cache_dir=state_root() / "jev" / "cache",
    )
    if result is None:
        return 0

    heuristic = classify(prompt)
    roster_ids = {entry["id"] for entry in roster}
    lines = gate_answers(result.get("answers"), cfg, roster_ids)
    session_id = data.get("session_id") if isinstance(data.get("session_id"), str) else None
    meta = {
        "call_id": uuid.uuid4().hex,
        "session_id": session_id,
        "prompt_chars": len(prompt),
        "prompt_truncated": state["prompt_truncated"],
        "roster_size": len(roster),
        "injected": bool(lines),
    }
    rows = telemetry_rows(result, heuristic, cfg, meta)
    _append_events(state_root() / "jev" / "events.jsonl", rows)

    if lines:
        json_print({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": render_block(lines, result.get("model") or cfg["model"]),
            }
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
