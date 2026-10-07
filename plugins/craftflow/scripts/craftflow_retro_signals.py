#!/usr/bin/env python3
"""craftflow_retro_signals.py -- leaf module: the 11 retro signal extractors + shared helpers.

Imported one-way by craftflow_retro.py; this module never imports craftflow_retro.
Pure functions only: stdlib, no IO, never writes, never calls subprocess.
Each extractor is `_sig_<id>(artifact, events, gaps) -> signal dict | None`;
`events` is [(1-based line, event dict)]; `gaps` collects data_gaps strings.
"""
from __future__ import annotations

import json

EXCERPT_MAX = 200
EVIDENCE_CAP = 10
SLOW_AGENT_SECONDS = 900

REMFIX_EVENTS = ("remediation_created", "remfix_created")
PROOF_BAD_STATUS = ("gaps_found", "human_needed")
PROOF_BAD_TOKENS = ("partial", "fail", "blocked", "gap", "descope")
DOUBT_PREFIX = "doubt_verify_refuted"


class RetroError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _type_name(value) -> str:
    return type(value).__name__


def _excerpt(text) -> str:
    text = str(text)
    if len(text) <= EXCERPT_MAX:
        return text
    return text[: EXCERPT_MAX - 3] + "..."


def _ev(source: str, key_or_line, event=None, excerpt: str = "") -> dict:
    if source == "artifact":
        return {"source": "artifact", "key": key_or_line, "excerpt": _excerpt(excerpt)}
    return {"source": "events", "line": key_or_line, "event": event, "excerpt": _excerpt(excerpt)}


def _cap(evidence: list) -> tuple:
    return evidence[:EVIDENCE_CAP], len(evidence)


def _as_dict(value, key: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RetroError("artifact_shape", f"{key} is {_type_name(value)}, expected object")
    return value


def _as_list(value, key: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RetroError("artifact_shape", f"{key} is {_type_name(value)}, expected list")
    return value


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _history_entries(artifact: dict, field: str, gaps: list) -> list:
    """Return [(index, dict)] for dict entries; record skipped non-dict entries in gaps."""
    entries = []
    for i, item in enumerate(_as_list(artifact.get(field), field)):
        if isinstance(item, dict):
            entries.append((i, item))
        else:
            gaps.append(f"{field}[{i}] is {_type_name(item)}, skipped")
    return entries


def _event_hits(events: list, names) -> list:
    return [(n, e) for n, e in events if e.get("event") in names]


def _event_evidence(hits: list) -> list:
    return [_ev("events", n, e.get("event"), json.dumps(e, sort_keys=True)) for n, e in hits]


def _signal(sig_id: str, weight: int, count: int, summary: str, details: dict, evidence: list) -> dict:
    shown, total = _cap(evidence)
    return {"id": sig_id, "weight": weight, "count": count, "summary": summary,
            "details": details, "evidence_total": total, "evidence": shown}


def _status_hits(artifact: dict, match) -> list:
    """Evidence for status_history dict entries whose `event` satisfies match (gaps reported elsewhere)."""
    out = []
    for i, item in enumerate(_as_list(artifact.get("status_history"), "status_history")):
        if isinstance(item, dict) and isinstance(item.get("event"), str) and match(item["event"]):
            out.append(_ev("artifact", f"status_history[{i}]", excerpt=json.dumps(item, sort_keys=True)))
    return out


def _sig_remfix_cycles(artifact: dict, events: list, gaps: list):
    entries = _history_entries(artifact, "remediation_history", gaps)
    breaker = _as_dict(artifact.get("circuit_breaker"), "circuit_breaker").get("remfix_count")
    breaker_count = breaker if _is_int(breaker) else 0
    hits = _event_hits(events, REMFIX_EVENTS)
    count = max(len(entries), breaker_count, len(hits))
    if count < 1:
        return None
    by_phase: dict = {}
    evidence = []
    for i, entry in entries:
        phase = entry.get("phase") or entry.get("phase_id") or "unknown"
        by_phase[phase] = by_phase.get(phase, 0) + 1
        text = "{} cycle={} scope={} origin={}: {}".format(
            phase, entry.get("cycle"), entry.get("scope"), entry.get("origin"), entry.get("reason"))
        evidence.append(_ev("artifact", f"remediation_history[{i}]", excerpt=text))
    evidence.extend(_event_evidence(hits))
    summary = "{} REM-FIX cycles ({})".format(
        count, ", ".join(f"{p}: {n}" for p, n in sorted(by_phase.items())) or "no phase detail")
    details = {"history_entries": len(entries), "breaker_remfix_count": breaker_count,
               "event_count": len(hits), "by_phase": by_phase}
    return _signal("remfix_cycles", 3 * count, count, summary, details, evidence)


def _sig_circuit_breaker_tripped(artifact: dict, events: list, gaps: list):
    breaker = _as_dict(artifact.get("circuit_breaker"), "circuit_breaker")
    flagged = breaker.get("broken") is True or breaker.get("tripped") is True
    hits = _event_hits(events, ("circuit_breaker_tripped",))
    if not flagged and not hits:
        return None
    evidence = []
    if flagged:
        evidence.append(_ev("artifact", "circuit_breaker", excerpt=json.dumps(breaker, sort_keys=True)))
    evidence.extend(_event_evidence(hits))
    count = (1 if flagged else 0) + len(hits)
    return _signal("circuit_breaker_tripped", 10, count, "circuit breaker tripped",
                   {"artifact_flag": flagged, "event_count": len(hits)}, evidence)


def _sig_pending_gate(artifact: dict, events: list, gaps: list):
    gate = artifact.get("pending_gate")
    if gate is None or gate == "" or gate == "none":
        return None
    if isinstance(gate, dict):
        label = str(gate["kind"]) if gate.get("kind") else json.dumps(gate, sort_keys=True)
    else:
        label = str(gate)
    evidence = [_ev("artifact", "pending_gate", excerpt=json.dumps(gate, sort_keys=True))]
    return _signal("pending_gate", 5, 1, f"pending gate left open: {label}", {"gate": label}, evidence)


def _sig_loop_counts(artifact: dict, events: list, gaps: list):
    telemetry = _as_dict(artifact.get("telemetry"), "telemetry")
    loops = _as_dict(telemetry.get("loop_counts"), "telemetry.loop_counts")
    total = 0
    evidence = []
    by_loop: dict = {}
    for key, value in loops.items():
        if key == "remfix" or value is None:
            continue  # remfix is already covered by remfix_cycles
        if not _is_int(value):
            gaps.append(f"telemetry.loop_counts.{key} is {_type_name(value)}, skipped")
            continue
        if value > 0:
            total += value
            by_loop[key] = value
            evidence.append(_ev("artifact", f"telemetry.loop_counts.{key}", excerpt=f"{key}={value}"))
    if total < 1:
        return None
    summary = "{} review/verify loops ({})".format(total, ", ".join(f"{k}: {v}" for k, v in by_loop.items()))
    return _signal("loop_counts", total, total, summary, {"by_loop": by_loop}, evidence)


def _sig_doubt_refutations(artifact: dict, events: list, gaps: list):
    evidence = _status_hits(artifact, lambda e: e.startswith(DOUBT_PREFIX))
    hist = len(evidence)
    hits = [(n, e) for n, e in events if isinstance(e.get("event"), str) and e["event"].startswith(DOUBT_PREFIX)]
    evidence.extend(_event_evidence(hits))
    count = len(evidence)
    if count < 1:
        return None
    return _signal("doubt_refutations", 3 * count, count, f"{count} doubt-verify refutations",
                   {"history_entries": hist, "event_count": len(hits)}, evidence)


def _sig_proof_gaps(artifact: dict, events: list, gaps: list):
    evidence = []
    proof = artifact.get("proof_status")
    if proof in PROOF_BAD_STATUS:
        evidence.append(_ev("artifact", "proof_status", excerpt=str(proof)))
    for phase, value in _as_dict(artifact.get("phase_status"), "phase_status").items():
        if isinstance(value, str) and any(tok in value.lower() for tok in PROOF_BAD_TOKENS):
            evidence.append(_ev("artifact", f"phase_status.{phase}", excerpt=f"{phase}: {value}"))
    count = len(evidence)
    if count < 1:
        return None
    return _signal("proof_gaps", 4 * count, count, f"{count} proof gaps (proof_status={proof!r})",
                   {"proof_status": proof}, evidence)


def _sig_stop_failures(artifact: dict, events: list, gaps: list):
    hits = _event_hits(events, ("stop_failure",))
    if not hits:
        return None
    return _signal("stop_failures", 2 * len(hits), len(hits), f"{len(hits)} stop failures", {}, _event_evidence(hits))


def _sig_fallbacks(artifact: dict, events: list, gaps: list):
    hits = _event_hits(events, ("parallel_fallback", "worktree_fallback"))
    if not hits:
        return None
    return _signal("fallbacks", len(hits), len(hits), f"{len(hits)} parallel/worktree fallbacks", {},
                   _event_evidence(hits))


def _sig_slow_agents(artifact: dict, events: list, gaps: list):
    telemetry = _as_dict(artifact.get("telemetry"), "telemetry")
    clocks = _as_dict(telemetry.get("agent_wall_clock_seconds"), "telemetry.agent_wall_clock_seconds")
    evidence = []
    for agent, value in clocks.items():
        if value is None:
            continue
        if not _is_num(value):
            gaps.append(f"telemetry.agent_wall_clock_seconds.{agent} is {_type_name(value)}, skipped")
        elif value >= SLOW_AGENT_SECONDS:
            evidence.append(_ev("artifact", f"telemetry.agent_wall_clock_seconds.{agent}",
                                excerpt=f"{agent}={value}s"))
    hits = [(n, e) for n, e in events if _is_num(e.get("duration_seconds"))
            and e["duration_seconds"] >= SLOW_AGENT_SECONDS]
    evidence.extend(_event_evidence(hits))
    count = len(evidence)
    if count < 1:
        return None
    return _signal("slow_agents", count, count, f"{count} agent runs >= {SLOW_AGENT_SECONDS}s",
                   {"threshold_seconds": SLOW_AGENT_SECONDS}, evidence)


def _sig_contradictions(artifact: dict, events: list, gaps: list):
    evidence = _status_hits(artifact, lambda e: e == "contradiction_logged")
    evidence.extend(_event_evidence(_event_hits(events, ("contradiction_logged",))))
    count = len(evidence)
    if count < 1:
        return None
    return _signal("contradictions", 3 * count, count, f"{count} contradictions logged", {}, evidence)


def _sig_compactions(artifact: dict, events: list, gaps: list):
    hits = _event_hits(events, ("compact_occurred",))
    if not hits:
        return None
    return _signal("compactions", len(hits), len(hits), f"{len(hits)} context compactions", {}, _event_evidence(hits))


# Registry in catalog order: tuple of (signal_id, extractor). The skill reads the ids.
EXTRACTORS = (
    ("remfix_cycles", _sig_remfix_cycles),
    ("circuit_breaker_tripped", _sig_circuit_breaker_tripped),
    ("pending_gate", _sig_pending_gate),
    ("loop_counts", _sig_loop_counts),
    ("doubt_refutations", _sig_doubt_refutations),
    ("proof_gaps", _sig_proof_gaps),
    ("stop_failures", _sig_stop_failures),
    ("fallbacks", _sig_fallbacks),
    ("slow_agents", _sig_slow_agents),
    ("contradictions", _sig_contradictions),
    ("compactions", _sig_compactions),
)
