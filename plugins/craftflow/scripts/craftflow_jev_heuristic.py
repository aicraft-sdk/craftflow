#!/usr/bin/env python3
"""Deterministic keyword-table port used as the "heuristic" side of the
optional Jev (TypeSafe AI) agreement telemetry (DD-10).

`INTENT_TABLE` and `RISK_KEYWORDS` are literal copies of the two markdown
source-of-truth tables:
  - `skills/_shared/router-protocol.md` -- Intent Routing table (priority
    ERROR > PLAN > REVIEW > BUILD-default; the DEFAULT/BUILD row is not a
    keyword row and is intentionally excluded).
  - `skills/craftflow-router/references/fast-path.md` -- the
    `risk_keyword_scan` keyword table.

`parse_intent_table` / `parse_risk_table` re-derive the same structures from
the markdown text at test time so a doc/module drift fails loudly (P7,
`test_craftflow_jev_heuristic.py`). `SKILL_RULES` ports the deterministic
skill-hint rules from `craftflow-router/SKILL.md` (frontend-patterns /
architecture-patterns bullets); the source sentences themselves are asserted
present verbatim by the same test module.

This module does no file I/O, network, or env access at import time --
constants are literal Python data, matching the DD-12a import-cheap
requirement enforced by `craftflow_hook_selfcheck.py`.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

# Literal copy of skills/_shared/router-protocol.md's Intent Routing table,
# priority order ERROR > PLAN > REVIEW (the DEFAULT/BUILD row carries no
# keywords and is not represented here -- classify() falls back to BUILD).
INTENT_TABLE: List[Tuple[str, List[str], str]] = [
    ("ERROR", ["error", "bug", "fix", "broken", "crash", "fail", "debug", "troubleshoot", "issue"], "DEBUG"),
    ("PLAN", ["plan", "design", "architect", "roadmap", "strategy", "spec", "brainstorm"], "PLAN"),
    ("REVIEW", ["review", "audit", "analyze", "assess", "is this good"], "REVIEW"),
]

# Literal copy of skills/craftflow-router/references/fast-path.md's
# risk_keyword_scan table.
RISK_KEYWORDS: Dict[str, List[str]] = {
    "Security": [
        "auth", "authz", "oauth", "jwt", "password", "credential", "secret",
        "cert", "ssl", "tls", "encrypt", "decrypt", "permission", "role",
        "session", "access control",
    ],
    "Database / schema": [
        "migration", "schema change", "alter table", "drop table", "seed",
        "remove column", "drop column", "export data", "data export",
    ],
    "Payment": ["payment", "billing", "stripe", "checkout", "subscription", "invoice"],
    "Explicit risk markers": [
        "critical path", "production data", "irreversible", "truncate",
        "delete all", "purge",
    ],
}

# Ports craftflow-router/SKILL.md's deterministic skill-hint bullets:
# "Include `craftflow:frontend-patterns` only when the request, changed
# files, plan, or design clearly targets UI/frontend work." and
# "Include `craftflow:architecture-patterns` only for multi-component, API,
# schema, auth, or integration-heavy work."
SKILL_RULES: Dict[str, List[str]] = {
    "frontend": [
        "ui", "frontend", "front-end", "css", "react", "component", "page",
        "layout", "form", "button", "modal", "tailwind", "vue", "svelte",
    ],
    "architecture": [
        "api", "endpoint", "schema", "auth", "integration", "service",
        "database", "migration", "multi-component", "microservice",
    ],
}


def _split_keywords(cell: str) -> List[str]:
    """Split a markdown table cell into keywords, stripping quotes/backticks."""
    cell = cell.strip()
    if cell == "Everything else":
        return []
    items: List[str] = []
    for part in cell.split(","):
        part = part.strip().strip('"').strip("`").strip()
        if part:
            items.append(part)
    return items


def parse_intent_table(text: str) -> List[Tuple[str, List[str], str]]:
    """Parse the `| n | SIGNAL | keywords | WORKFLOW | ... |` rows from
    router-protocol.md's Intent Routing table. Skips the DEFAULT row."""
    rows: List[Tuple[str, List[str], str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4:
            continue
        priority, signal, keywords_cell, workflow = cells[0], cells[1], cells[2], cells[3]
        if not priority.isdigit():
            continue
        if signal == "DEFAULT":
            continue
        rows.append((signal, _split_keywords(keywords_cell), workflow))
    return rows


def parse_risk_table(text: str) -> Dict[str, List[str]]:
    """Parse the `#### risk_keyword_scan` two-column table from
    fast-path.md, stopping at the next `####` heading."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("#### risk_keyword_scan"):
            start = i
            break
    if start is None:
        return {}
    result: Dict[str, List[str]] = {}
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("####"):
            break
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) != 2:
            continue
        group, keywords_cell = cells
        if group == "Group" or set(group) <= {"-"}:
            continue
        result[group] = _split_keywords(keywords_cell)
    return result


def _find_matches(text: str, keywords: List[str]) -> List[str]:
    lowered = text.lower()
    matched: List[str] = []
    for kw in keywords:
        pattern = r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, lowered):
            matched.append(kw)
    return matched


def classify(text: str) -> Dict[str, Any]:
    """Deterministic keyword-table classification -- the heuristic half of
    the jev agreement telemetry. Never raises; pure function of `text`."""
    workflow = "BUILD"
    matched: List[str] = []
    for _signal, keywords, table_workflow in INTENT_TABLE:
        hit = _find_matches(text, keywords)
        if hit:
            workflow = table_workflow
            matched = hit
            break

    risk_signals: List[str] = []
    for group_keywords in RISK_KEYWORDS.values():
        risk_signals.extend(_find_matches(text, group_keywords))

    if workflow == "DEBUG":
        skill = "craftflow:debugging-patterns"
    elif _find_matches(text, SKILL_RULES["frontend"]):
        skill = "craftflow:frontend-patterns"
    elif _find_matches(text, SKILL_RULES["architecture"]):
        skill = "craftflow:architecture-patterns"
    else:
        skill = "none"

    return {"workflow": workflow, "matched": matched, "risk_signals": risk_signals, "skill": skill}
