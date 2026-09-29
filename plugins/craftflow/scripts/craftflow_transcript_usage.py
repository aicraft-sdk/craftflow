#!/usr/bin/env python3
"""craftflow_transcript_usage.py

Pure, stdlib-only summarizer for Claude Code subagent transcripts (JSONL).
Used by the SubagentStop audit hook and the model report CLI. It never
imports craftflow_hooklib and never logs: callers own all side effects.

Dedupe rule (DD-4): assistant records sharing a ``message.id`` are one API
message (streamed chunks); take the per-field maximum across duplicates.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone

SCHEMA_VERSION = 1

_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "cache_write_5m",
    "cache_write_1h",
)
_MAX_TOKEN = 10 ** 15
_DEADLINE_CHECK_EVERY = 200
_MAX_FINAL_TEXT = 200000
_ERR_MAX = 120
_ERR_COUNT = 3
_FALLBACK_HEADING = re.compile(r"###\s+Router\s+Contract\s+\(MACHINE-READABLE\)", re.IGNORECASE)
_OTHER_HEADING = re.compile(r"(?im)^#{2,3}\s+Router\s+Contract\b")


def _coerce_token(value):
    """Return (int, valid). bool/negative/float/str/huge values are invalid -> 0."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0, False
    if value < 0 or value >= _MAX_TOKEN:
        return 0, False
    return value, True


def _merge_max(a, b):
    """Per-field maximum of two usage dicts (commutative, idempotent)."""
    out = dict(a)
    for key, val in b.items():
        if key == "fast":
            out[key] = bool(out.get(key)) or bool(val)
        else:
            out[key] = max(out.get(key, 0), val)
    return out


def _parse_ts(value):
    """Parse an ISO-8601 timestamp to epoch seconds, or None."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


_MAX_SCAN_CHARS = 200000
_PHASE_MAX = 80
_WF_PATTERNS = (
    re.compile(r"Parent Workflow ID:\s*`?(wf-[A-Za-z0-9-]+)"),
    re.compile(r"Workflow Scope:\s*`?wf:(wf-[A-Za-z0-9-]+)"),
    re.compile(r"(?m)^\s*wf:\s*(wf-[A-Za-z0-9-]+)"),
)
_PHASE_PATTERNS = (
    re.compile(r"Task Phase:\s*([^\n]+)"),
    re.compile(r"(?m)^\s*phase:\s*(\S+)"),
)
_REMFIX_KIND = re.compile(r"(?m)^\s*kind:\s*remfix\b")
_REMFIX_LABEL = re.compile(r"rem-?fix", re.IGNORECASE)


def extract_dispatch_metadata(text):
    """Extract workflow id, phase label and REM-FIX flag from a dispatch prompt."""
    out = {"workflow_id": None, "dispatch_phase": None, "is_remfix": False}
    if not isinstance(text, str):
        return out
    text = text[:_MAX_SCAN_CHARS]
    for pattern in _WF_PATTERNS:
        match = pattern.search(text)
        if match:
            out["workflow_id"] = match.group(1)
            break
    for pattern in _PHASE_PATTERNS:
        match = pattern.search(text)
        if match:
            phase = match.group(1).strip().strip("`")
            out["dispatch_phase"] = phase[:_PHASE_MAX] or None
            break
    out["is_remfix"] = bool(
        _REMFIX_KIND.search(text) or _REMFIX_LABEL.search(out["dispatch_phase"] or "")
    )
    return out


def _text_blocks(content):
    """Text strings of assistant content blocks of type text."""
    if not isinstance(content, list):
        return []
    return [b["text"] for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]


def _load_validator():
    """Lazily import the contract validator (sibling script). May raise ImportError."""
    import craftflow_contract_validate  # noqa: PLC0415 - deliberate lazy import
    return craftflow_contract_validate


def classify_contract(text, agent_slug):
    """Classify the Router Contract shape of a final message (DD-13). Never raises."""
    out = {"contract_shape": "none", "contract_valid": None, "contract_errors": []}
    if not isinstance(text, str):
        return out
    try:
        validator = _load_validator()
        heading = validator._HEADING_PATTERN
    except Exception:  # noqa: BLE001 - validator is optional; fail open
        validator = None
        heading = _FALLBACK_HEADING
    try:
        if heading.search(text):
            out["contract_shape"] = "yaml_block"
            if validator is None:
                out["contract_errors"] = ["validator_unavailable"]
                return out
            verdict = validator.validate_contract(text, agent_slug if isinstance(agent_slug, str) else "")
            out["contract_valid"] = bool(verdict.get("valid"))
            out["contract_errors"] = [str(e)[:_ERR_MAX] for e in verdict.get("errors", [])[:_ERR_COUNT]]
        elif "CONTRACT {" in text:
            out["contract_shape"] = "envelope"
        elif _OTHER_HEADING.search(text):
            out["contract_shape"] = "heading_other"
    except Exception:  # noqa: BLE001 - never raise from telemetry classification
        out["contract_valid"] = None
        out["contract_errors"] = ["classify_error"]
    return out


def _content_text(content):
    """Flatten user message content (str or list of blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def _empty_result():
    return {
        "models": {},
        "primary_model": None,
        "turns": 0,
        "duration_s": None,
        "effort": None,
        "attribution_agent": None,
        "workflow_id": None,
        "dispatch_phase": None,
        "is_remfix": False,
        "unkeyed_messages": 0,
        "corrupt_lines": 0,
        "invalid_usage_values": 0,
        "partial": False,
        "error": None,
    }


def _extract_usage(usage, counters):
    """Coerce a message.usage dict into the internal usage dict."""
    out = {}
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens"):
        val, valid = _coerce_token(usage.get(key, 0))
        if not valid and key in usage:
            counters["invalid_usage_values"] += 1
        out[key] = val
    split = usage.get("cache_creation")
    if isinstance(split, dict):
        out["cache_write_5m"], ok5 = _coerce_token(split.get("ephemeral_5m_input_tokens", 0))
        out["cache_write_1h"], ok1 = _coerce_token(split.get("ephemeral_1h_input_tokens", 0))
        if not (ok5 and ok1):
            counters["invalid_usage_values"] += 1
    else:
        out["cache_write_5m"] = out["cache_creation_input_tokens"]
        out["cache_write_1h"] = 0
    out["fast"] = usage.get("speed") == "fast"
    return out


def summarize_lines(lines, clock=None, deadline_s=None, want_final_text=False):
    result = _empty_result()
    counters = {"invalid_usage_values": 0}
    keyed = {}     # id -> (model, usage)
    order = []     # ids in first-seen order
    unkeyed = []   # (model, usage)
    model_first_seen = []
    efforts = Counter()
    seen_user = False
    text_blocks = {}   # id -> [text, ...] (want_final_text only)
    last_text_id = None
    ts_min = None
    ts_max = None
    tick = clock or time.monotonic
    started = tick() if deadline_s is not None else None
    for index, raw in enumerate(lines):
        if (deadline_s is not None and index and index % _DEADLINE_CHECK_EVERY == 0
                and tick() - started > deadline_s):
            result["partial"] = True
            result["error"] = "time_budget_exceeded"
            break
        if isinstance(raw, str) and not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except (ValueError, TypeError):
            result["corrupt_lines"] += 1
            continue
        if not isinstance(row, dict):
            continue
        ts = _parse_ts(row.get("timestamp"))
        if ts is not None:
            ts_min = ts if ts_min is None else min(ts_min, ts)
            ts_max = ts if ts_max is None else max(ts_max, ts)
        rtype = row.get("type")
        if rtype == "user" and not seen_user:
            seen_user = True
            umsg = row.get("message")
            if isinstance(umsg, dict):
                meta = extract_dispatch_metadata(_content_text(umsg.get("content")))
                result.update(meta)
            continue
        if rtype != "assistant":
            continue
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        raw_model = message.get("model")
        model = raw_model if isinstance(raw_model, str) and raw_model else "unknown"
        u = _extract_usage(usage, counters)
        mid = message.get("id")
        if want_final_text:
            blocks = _text_blocks(message.get("content"))
            if blocks:
                tkey = mid if isinstance(mid, str) and mid else "\0unkeyed-" + str(index)
                bucket_texts = text_blocks.setdefault(tkey, [])
                for block in blocks:
                    if block not in bucket_texts:
                        bucket_texts.append(block)
                last_text_id = tkey
        if isinstance(mid, str) and mid:
            if mid in keyed:
                prev_model, prev_u = keyed[mid]
                keyed[mid] = (prev_model, _merge_max(prev_u, u))
            else:
                keyed[mid] = (model, u)
                order.append(mid)
        else:
            unkeyed.append((model, u))
            result["unkeyed_messages"] += 1
        if model not in model_first_seen:
            model_first_seen.append(model)
        eff = row.get("effort")
        if isinstance(eff, str) and eff:
            efforts[eff] += 1
        if result["attribution_agent"] is None:
            attr = row.get("attributionAgent")
            if isinstance(attr, str) and attr:
                result["attribution_agent"] = attr
    result["invalid_usage_values"] = counters["invalid_usage_values"]

    models = {}
    entries = [keyed[i] for i in order] + unkeyed
    for model, u in entries:
        bucket = models.setdefault(model, {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "cache_write_5m": 0, "cache_write_1h": 0,
            "messages": 0, "fast_messages": 0,
        })
        for key in _USAGE_FIELDS:
            bucket[key] += u.get(key, 0)
        bucket["messages"] += 1
        if u.get("fast"):
            bucket["fast_messages"] += 1
    result["models"] = models
    result["turns"] = len(entries)
    primary = None
    best = -1
    for model in model_first_seen:
        out = models[model]["output_tokens"]
        if out > best:
            best = out
            primary = model
    result["primary_model"] = primary
    if ts_min is not None:
        result["duration_s"] = round(ts_max - ts_min, 1)
    if efforts:
        result["effort"] = efforts.most_common(1)[0][0]
    if want_final_text:
        joined = "\n".join(text_blocks.get(last_text_id, [])) if last_text_id is not None else ""
        result["final_text"] = joined[:_MAX_FINAL_TEXT]
    return result


def _failed(error, want_final_text):
    result = _empty_result()
    result["error"] = error
    if want_final_text:
        result["final_text"] = ""
    return result


def summarize_transcript(path, max_bytes=67108864, deadline_s=3.0, clock=None,
                         want_final_text=False):
    """Impure shell: stat/open/read a transcript file and summarize it. Never raises."""
    try:
        if not path or not isinstance(path, (str, os.PathLike)):
            return _failed("no_transcript_path", want_final_text)
        p = str(path)
        if not os.path.exists(p):
            return _failed("transcript_missing", want_final_text)
        if not p.endswith(".jsonl") or not os.path.isfile(p):
            return _failed("transcript_not_regular_jsonl", want_final_text)
        if os.stat(p).st_size > max_bytes:
            return _failed("transcript_too_large", want_final_text)
        with open(p, "r", encoding="utf-8", errors="replace") as handle:
            return summarize_lines(handle, clock=clock,
                                   deadline_s=deadline_s,
                                   want_final_text=want_final_text)
    except Exception as exc:  # noqa: BLE001 - fail-open contract: never raise
        return _failed("unexpected:" + type(exc).__name__, want_final_text)


def last_turn_context_tokens(lines):
    """Context size of the last main-chain turn (SPEC-0016 DD-4). Pure; never raises.
    Returns {"tokens": int|None, "model": str|None, "source": "assistant"|"compact_boundary"|None}."""
    out = {"tokens": None, "model": None, "source": None}
    try:
        for raw in lines:
            try:
                row = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else None
            except (ValueError, TypeError):
                continue
            if not isinstance(row, dict) or row.get("isSidechain") is True:
                continue
            if row.get("type") == "system" and row.get("subtype") == "compact_boundary":
                meta = row.get("compactMetadata")
                post, valid = _coerce_token(meta.get("postTokens")) if isinstance(meta, dict) else (0, False)
                out = {"tokens": post if valid and post > 0 else None, "model": None, "source": "compact_boundary"}
                continue
            if row.get("type") != "assistant":
                continue
            message = row.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
                continue
            model = message.get("model")
            if model == "<synthetic>":
                continue
            usage = message["usage"]
            total = sum(_coerce_token(usage.get(k, 0))[0] for k in
                        ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            if total <= 0:
                continue
            out = {"tokens": total, "model": model if isinstance(model, str) and model else None,
                   "source": "assistant"}
    except Exception:  # noqa: BLE001 - totality (P3)
        return out
    return out
