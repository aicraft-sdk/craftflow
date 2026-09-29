#!/usr/bin/env python3
"""craftflow_context_nudge.py -- context-size nudge (SPEC-0016 / ADR-0051).
Modes: (no flag) UserPromptSubmit hook on stdin; --reset SessionStart(compact) on stdin;
--boundary --wf <id> [--phase ID] [--project-root DIR] [--session-id ID] router CLI (one JSON line)."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from craftflow_transcript_usage import last_turn_context_tokens

SCHEMA_VERSION = 1
LEVELS = ("none", "warn", "critical")
DEFAULTS = {"warnTokens": 120000, "criticalTokens": 160000, "assumedWindow": 200000}
TAIL_BYTES = 262144
RETRY_TAIL_BYTES = 4194304
BOUNDARY_STATE_MAX_AGE_S = 43200
STATE_PRUNE_AGE_S = 1209600
STATE_DIRNAME = "context-nudge"
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_WF_RE = re.compile(r"^wf-[A-Za-z0-9-]{1,160}$")
_BOUNDARY_KEYS = ("mode", "level", "tokens", "threshold", "assumed_window", "relay", "advisory",
                  "checkpoint_path", "session_id", "session_source", "phase", "outcome", "error")


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------

def rank(level):
    """Index of a level in LEVELS; 0 for anything unknown."""
    try:
        return LEVELS.index(level)
    except (ValueError, TypeError):
        return 0


def _level_or_none(level):
    return level if isinstance(level, str) and level in LEVELS else "none"


def classify(tokens, warn, critical):
    """none | warn | critical using tokens >= threshold. Non-int / non-positive -> none."""
    try:
        if type(tokens) is not int or tokens <= 0:
            return "none"
        if tokens >= critical:
            return "critical"
        if tokens >= warn:
            return "warn"
    except Exception:  # noqa: BLE001 - totality (P3)
        pass
    return "none"


def resolve_mode(raw):
    """(effective_mode, logged_mode) for the contextNudge toggle (DD-6)."""
    if raw == "off":
        return "off", "off"
    if raw == "on":
        return "on", "on"
    if raw == "audit" or raw is None:
        return "audit", "audit"
    return "audit", "audit-unrecognized-config-value"


def decide(level, last_level, mode):
    """Hook decision (DD-5). new_last_level is always the observed level."""
    level = _level_or_none(level)
    if mode == "off":
        action = "off"
    elif rank(level) > rank(last_level):
        action = "nudge" if mode == "on" else "would_nudge"
    elif rank(level) < rank(last_level):
        action = "rearmed"
    else:
        action = "below_threshold" if level == "none" else "suppressed"
    return {"action": action, "level": level, "new_last_level": level}


def decide_boundary(level, boundary_level, mode, measured):
    """Boundary relay decision (DD-11); `relay` is a literal bool computed here."""
    level = _level_or_none(level)
    kept = _level_or_none(boundary_level)
    if not measured:
        return {"relay": False, "new_boundary_level": kept, "outcome": "no_session_state"}
    if mode == "off":
        return {"relay": False, "new_boundary_level": kept, "outcome": "off"}
    if level == "none":
        return {"relay": False, "new_boundary_level": kept, "outcome": "below_threshold"}
    if rank(level) > rank(kept):
        if mode == "on":
            return {"relay": True, "new_boundary_level": level, "outcome": "advised"}
        return {"relay": False, "new_boundary_level": kept, "outcome": "would_advise"}
    return {"relay": False, "new_boundary_level": kept, "outcome": "already_advised"}


def validate_config(obj):
    """(cfg, None) or (None, "config_invalid") per DD-7."""
    try:
        if not isinstance(obj, dict):
            return None, "config_invalid"
        cfg = {}
        for key in DEFAULTS:
            v = obj.get(key)
            if type(v) is not int or v <= 0:
                return None, "config_invalid"
            cfg[key] = v
        if cfg["criticalTokens"] <= cfg["warnTokens"] or cfg["assumedWindow"] < cfg["criticalTokens"]:
            return None, "config_invalid"
        return cfg, None
    except Exception:  # noqa: BLE001 - totality (P3)
        return None, "config_invalid"


def safe_session_id(value):
    return value if isinstance(value, str) and _SESSION_RE.match(value) else None


def safe_wf(value):
    return value if isinstance(value, str) and _WF_RE.match(value) else None


def _fmt(n):
    try:
        return format(n, ",") if type(n) is int else str(n)
    except Exception:  # noqa: BLE001
        return "?"


def render_advisory(level, tokens, cfg):
    """Single-line advisory text (DD-10); empty string for level none / unknown."""
    try:
        cfg = cfg if isinstance(cfg, dict) else DEFAULTS
        window = _fmt(cfg.get("assumedWindow", DEFAULTS["assumedWindow"]))
        if level == "warn":
            return (f"CRAFTFLOW context advisory: this session's context is about {_fmt(tokens)} tokens "
                    f"(warn threshold {_fmt(cfg.get('warnTokens', DEFAULTS['warnTokens']))}; "
                    f"assumed window {window}). Tell the user once, in one sentence, that running "
                    "/compact at the next natural pause (for example a craftflow phase boundary) keeps "
                    "answer quality up; craftflow workflow state survives compaction. Do not stop the "
                    "current task because of this note.")
        if level == "critical":
            return (f"CRAFTFLOW context advisory: this session's context is about {_fmt(tokens)} tokens "
                    f"(critical threshold {_fmt(cfg.get('criticalTokens', DEFAULTS['criticalTokens']))}; "
                    f"assumed window {window}). Auto-compaction may fire soon: finish the current step, "
                    "checkpoint durable state, and ask the user to run /compact before starting new work.")
    except Exception:  # noqa: BLE001 - totality (P3)
        pass
    return ""


def boundary_result(**fields):
    """Boundary JSON dict with every DD-11 key present (missing -> None, relay False)."""
    out = {"schema": SCHEMA_VERSION}
    for key in _BOUNDARY_KEYS:
        out[key] = fields.get(key)
    out["relay"] = fields.get("relay") is True
    return out


# ---------------------------------------------------------------------------
# Impure shells (transcript + state + config). Each returns instead of raising.
# ---------------------------------------------------------------------------

def load_config(path):
    """(cfg, None) | (defaults, None) when the file is missing | (None, "config_invalid")."""
    try:
        p = str(path)
        if not os.path.exists(p):
            return dict(DEFAULTS), None
        with open(p, "r", encoding="utf-8") as handle:
            return validate_config(json.load(handle))
    except Exception:  # noqa: BLE001 - fail-open contract: invalid config, never raise
        return None, "config_invalid"


def read_tail_lines(path, max_bytes):
    """Last `max_bytes` of the file as lines; the possibly truncated first line is dropped."""
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        offset = max(0, size - max_bytes)
        handle.seek(offset)
        data = handle.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    if offset > 0 and lines:
        lines = lines[1:]
    return lines


def current_context_tokens(transcript_path):
    """Measure current context from the transcript tail (DD-4). Never raises."""
    out = {"tokens": None, "model": None, "source": None, "error": None}
    try:
        if not transcript_path or not isinstance(transcript_path, (str, os.PathLike)):
            out["error"] = "no_transcript_path"
            return out
        p = str(transcript_path)
        if not os.path.exists(p):
            out["error"] = "transcript_missing"
            return out
        if not p.endswith(".jsonl") or not os.path.isfile(p):
            out["error"] = "transcript_not_regular_jsonl"
            return out
        size = os.path.getsize(p)
        res = last_turn_context_tokens(read_tail_lines(p, TAIL_BYTES))
        if res["tokens"] is None and res["source"] is None and size > TAIL_BYTES:
            res = last_turn_context_tokens(read_tail_lines(p, RETRY_TAIL_BYTES))
        out.update(tokens=res["tokens"], model=res["model"], source=res["source"])
        if res["tokens"] is None:
            out["error"] = "post_tokens_missing" if res["source"] == "compact_boundary" else "no_usage"
        return out
    except Exception as exc:  # noqa: BLE001 - fail-open contract: never raise
        out["tokens"] = None
        out["error"] = "unexpected:" + type(exc).__name__
        return out


def state_path(state_dir, sid):
    return Path(state_dir) / (str(sid) + ".json")


def load_state(path):
    """Per-session state merged over the defaults; unknown levels -> none. Never raises."""
    state = {"last_level": "none", "boundary_level": "none"}
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            state.update(raw)
    except Exception:  # noqa: BLE001 - missing/corrupt state is treated as fresh
        pass
    state["last_level"] = _level_or_none(state.get("last_level"))
    state["boundary_level"] = _level_or_none(state.get("boundary_level"))
    return state


def save_state(path, state):
    """Atomic write (mkstemp + os.replace). Returns False on any failure, cleaning the temp."""
    tmp = None
    try:
        parent = os.path.dirname(str(path)) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".ctx-nudge-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(tmp, str(path))
        return True
    except Exception:  # noqa: BLE001 - fail-open contract
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def main(argv=None):
    """Stub: hook/reset/boundary entry points land in later phases (fail-open exit 0)."""
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
