#!/usr/bin/env python3
"""Deterministic model of the primary-deliverable routing law (ADR-0075, H2).

This is the reference classifier the routing corpus
(`tests/fixtures/routing-cases.json`) and the Jev heuristic use. The markdown
source of truth is the `## Intent Routing` table and rules in
`skills/_shared/router-protocol.md`; `parse_routing_table()` re-derives the
table from that file at test time so a drift fails loudly.

Law (Behavior contract 1): a keyword only nominates a row; the request's
primary deliverable decides. Evaluation order:
  1. change-verb override (first word is a change verb and no ERROR, PLAN or
     QA keyword sits in the lead clause -> BUILD; the hint is not consulted);
  2. an optional hint is one more nomination at position 0, dropped when an
     ERROR keyword sits in the lead clause;
  3. candidates are the nominations inside the lead clause (else all of them);
     the earliest position wins, equal positions go to the lower priority
     number; no nomination -> BUILD;
  4. R4: when QA wins and an ERROR keyword sits in the lead clause, DEBUG wins
     (repairing is the deliverable, wherever the QA keyword sits).

Keyword matching is case-insensitive and word-bounded exactly like
`craftflow_jev_heuristic._find_matches`, with one extension: a keyword that
ends in a non-alphanumeric character (`look at #`, `triage #`) takes no
trailing boundary, so `#12` matches. No I/O, network or env access at import.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# (priority, signal, keywords, workflow) -- literal copy of the protocol's
# Intent Routing table. The DEFAULT row carries no keywords.
ROUTING_TABLE: List[Tuple[int, str, List[str], str]] = [
    (1, "ERROR", ["error", "bug", "fix", "broken", "crash", "fail", "debug", "troubleshoot", "issue"], "DEBUG"),
    (2, "PLAN", ["plan", "design", "architect", "roadmap", "strategy", "spec", "brainstorm"], "PLAN"),
    (3, "REVIEW", ["review", "audit", "analyze", "assess", "is this good"], "REVIEW"),
    (4, "ORIENT", ["zoom out", "explain", "understand", "how does X work", "walk me through",
                   "where is", "what does this do", "map this"], "ORIENT"),
    (5, "QA", ["test plan", "e2e", "end-to-end", "integration test", "test coverage", "regression",
               "smoke test", "verify my feature", "prove it works", "QA"], "QA"),
    (6, "TRIAGE", ["triage", "incoming issues", "look at #", "triage #"], "TRIAGE"),
    (7, "CODEBASE-HEALTH", ["codebase health", "improve architecture", "deepening", "ball of mud",
                            "shallow modules", "architecture audit"], "CODEBASE-HEALTH"),
    (8, "DEFAULT", [], "BUILD"),
]

CHANGE_VERBS = frozenset({
    "add", "implement", "create", "build", "refactor", "rewrite", "change", "update", "remove",
    "rename", "migrate", "make",
})

# Pre-harvest order: the first of these with any keyword match wins (priority
# order, not position); otherwise BUILD.
LEGACY_TABLE: List[Tuple[str, List[str], str]] = [
    ("ERROR", ["error", "bug", "fix", "broken", "crash", "fail", "debug", "troubleshoot", "issue"], "DEBUG"),
    ("PLAN", ["plan", "design", "architect", "roadmap", "strategy", "spec", "brainstorm"], "PLAN"),
    ("REVIEW", ["review", "audit", "analyze", "assess", "is this good"], "REVIEW"),
]

_PRIORITY = {workflow: prio for prio, _signal, _kws, workflow in ROUTING_TABLE}
_HOW_DOES_X_WORK = "how does X work"
_LEAD_END = re.compile(r" and | then |,|;|\n|[.!?](?=\s|$)", re.IGNORECASE)
_FIRST_WORD = re.compile(r"\s*([A-Za-z]+)")


def find_keyword(text: str, keyword: str) -> int:
    """Start index of the first word-bounded, case-insensitive match, else -1."""
    if keyword == _HOW_DOES_X_WORK:
        body = r"how does .+ work"
    else:
        body = re.escape(keyword.lower())
    tail = r"(?![a-z0-9])" if keyword[-1:].isalnum() else ""
    found = re.search(r"(?<![a-z0-9])" + body + tail, text.lower())
    return found.start() if found else -1


def lead_clause(text: str) -> str:
    """The request up to its first ' and ', ' then ', comma, semicolon, newline or
    sentence end ('.', '!' or '?' followed by whitespace or the end of the text)."""
    found = _LEAD_END.search(text)
    return text[: found.start()] if found else text


def _matches(text: str, keywords: List[str]) -> List[str]:
    return [kw for kw in keywords if find_keyword(text, kw) >= 0]


def _nominations(text: str) -> List[Tuple[int, int, str, List[str]]]:
    """(position, priority, workflow, matched keywords) per row with a match."""
    found: List[Tuple[int, int, str, List[str]]] = []
    for prio, _signal, keywords, workflow in ROUTING_TABLE:
        positions = [find_keyword(text, kw) for kw in keywords]
        hits = [p for p in positions if p >= 0]
        if hits:
            found.append((min(hits), prio, workflow, _matches(text, keywords)))
    return found


def route(text: str, hint: Optional[str] = None) -> Dict[str, Any]:
    """Route a request. Returns {"workflow", "matched"}; `matched` lists the
    winning row's keywords present in the text (empty for a verb, hint or
    default decision)."""
    lead = lead_clause(text)
    nominated = _nominations(text)
    in_lead = [n for n in nominated if n[0] < len(lead)]

    first = _FIRST_WORD.match(text)
    if first and first.group(1).lower() in CHANGE_VERBS:
        if not any(n[2] in ("DEBUG", "PLAN", "QA") for n in in_lead):
            return {"workflow": "BUILD", "matched": []}

    if hint in _PRIORITY and not any(n[2] == "DEBUG" for n in in_lead):
        hint_nomination = (0, _PRIORITY[hint], hint, [])
        nominated = [hint_nomination] + nominated
        in_lead = [hint_nomination] + in_lead if len(lead) > 0 else in_lead

    candidates = in_lead or nominated
    if not candidates:
        return {"workflow": "BUILD", "matched": []}
    _pos, _prio, workflow, matched = min(candidates, key=lambda n: (n[0], n[1]))
    if workflow == "QA":  # R4: DEBUG beats QA when repairing is the deliverable (an ERROR keyword in the lead clause)
        repairs = [n for n in in_lead if n[2] == "DEBUG"]
        if repairs:
            _pos, _prio, workflow, matched = min(repairs, key=lambda n: (n[0], n[1]))
    return {"workflow": workflow, "matched": matched}


def legacy_route_of(text: str) -> str:
    """Pre-harvest route: first of ERROR, PLAN, REVIEW with a keyword match,
    else BUILD. Ignores any hint."""
    for _signal, keywords, workflow in LEGACY_TABLE:
        if _matches(text, keywords):
            return workflow
    return "BUILD"


def _split_cells(line: str) -> List[str]:
    cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [c.replace("\\|", "|").strip() for c in cells]


def _split_keywords(cell: str) -> List[str]:
    if cell == "Everything else":
        return []
    return [p.strip().strip('"').strip("`").strip() for p in cell.split(",") if p.strip()]


def parse_routing_table(md: str) -> List[Tuple[int, str, List[str], str]]:
    """Read the `## Intent Routing` table into ROUTING_TABLE's row shape."""
    start = md.find("\n## Intent Routing")
    if start == -1:
        return []
    end = md.find("\n## ", start + 1)
    rows: List[Tuple[int, str, List[str], str]] = []
    for line in md[start : end if end != -1 else len(md)].splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _split_cells(line)
        if len(cells) >= 4 and cells[0].isdigit():
            rows.append((int(cells[0]), cells[1], _split_keywords(cells[2]), cells[3]))
    return rows
