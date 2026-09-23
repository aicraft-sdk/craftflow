#!/usr/bin/env python3
"""HTTP client for the optional Jev (TypeSafe AI) routing hint.

Calls a single JSON endpoint (`ENDPOINT`) with the current session `state`
and a set of `questions`, returning the model's `answers`/`usage`/`model`
or `None` on any failure (fail-open — callers must never block on this).

Design constraints (see docs/plans/2026-09-19-plan-optional-jev-typesafe-routi-plan.md):
- DD-2 Secret hygiene: `api_key` is held only in a local variable and used
  solely to build the Authorization header. It never enters any dict that
  is logged or cached. Exceptions are logged as `type(exc).__name__` plus
  the HTTP status only -- never `repr(exc)`, never response bodies.
- DD-4 Time budget: total client deadline is `TOTAL_BUDGET_SECONDS` (4.0s),
  inside the 5s hook timeout. Attempt 1 uses `min(timeout, remaining)`.
  A retry happens only on `HTTPError` with a status in `RETRY_STATUSES`
  (429/529) -- never on `socket.timeout`/`URLError` -- and only when at
  least 1.0s of budget remains after a 0.3s backoff sleep.
- DD-14 No endpoint override in production: `ENDPOINT` is a constant. The
  `CRAFTFLOW_JEV_ENDPOINT` env override exists for tests only and is
  honored solely when its host is loopback (127.0.0.1, localhost, ::1).
  Any other value is ignored and logged as `endpoint_override_ignored`.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from craftflow_hooklib import log_event

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
TOTAL_BUDGET_SECONDS = 4.0
RETRY_STATUSES = (429, 529)
_RETRY_BACKOFF_SECONDS = 0.3
_MIN_REMAINING_AFTER_BACKOFF_SECONDS = 1.0
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _resolve_endpoint(env: Dict[str, str], log) -> str:
    """Return ENDPOINT unless a loopback-only test override is present (DD-14)."""
    override = env.get("CRAFTFLOW_JEV_ENDPOINT")
    if not override:
        return ENDPOINT
    try:
        hostname = urllib.parse.urlsplit(override).hostname
    except Exception:
        hostname = None
    if hostname in _LOOPBACK_HOSTS:
        return override
    log("plugin_jev_client", {"event": "jev_call", "decision": "endpoint_override_ignored"})
    return ENDPOINT


def _cache_key(state: Any, questions: Any, model: str) -> str:
    payload = json.dumps({"state": state, "questions": questions, "model": model}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_cache(path: Path, ttl_seconds: int) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        entry = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(entry, dict) or "answers" not in entry:
            return None
        cached_at = entry.get("cached_at")
        if not isinstance(cached_at, (int, float)):
            return None
        if time.time() - cached_at > ttl_seconds:
            return None
        return entry
    except Exception:
        return None


def _write_cache(path: Path, result: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "cached_at": time.time(),
            "answers": result["answers"],
            "usage": result.get("usage"),
            "model": result.get("model"),
        }
        path.write_text(json.dumps(entry, ensure_ascii=True), encoding="utf-8")
    except Exception:
        pass  # cache writes must never fail the call


def _build_request(endpoint: str, state: Any, questions: Any, model: str, api_key: str) -> urllib.request.Request:
    body = json.dumps({"state": state, "model": model, "questions": questions}, ensure_ascii=True).encode("utf-8")
    return urllib.request.Request(
        endpoint,
        data=body,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )


def call(
    state: Any,
    questions: Any,
    *,
    api_key: str,
    model: str,
    timeout: float,
    cache_dir: Optional[Path],
    ttl_seconds: int = 86400,
    log=log_event,
    total_budget_seconds: float = TOTAL_BUDGET_SECONDS,
    failure_reason_out: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Call the Jev endpoint and return {"answers","usage","model","latency_ms","cache_hit"} or None.

    Never raises. The whole function body (cache-key derivation through the
    end of the retry loop) runs under a single outer try/except Exception --
    ANY failure anywhere (including a broken clock, a hostile `log`
    callable, or an exception raised while deciding whether to retry) is
    caught there, logged as one `jev_call_failed` event (type(exc).__name__
    + status -- best-effort from an HTTPError, else None -- + attempt), and
    turned into a `None` return. The retry decision itself (429/529 only,
    DD-4 budget math) has its own inner except clause purely to decide
    retry-vs-terminal; it never itself swallows an exception -- a
    non-retryable HTTPError is re-raised so the single outer except is the
    only place that ever logs+returns None.

    `total_budget_seconds` (additive, default `TOTAL_BUDGET_SECONDS`)
    overrides the wall-clock retry-eligibility budget for this call only --
    callers that omit it get byte-identical behavior to before this
    parameter existed. `failure_reason_out` (additive, default `None`) is an
    optional caller-supplied dict that, on any failure, is populated with
    `{"error": type(exc).__name__, "status": <HTTPError.code or None>}` --
    never touched on success. The write is wrapped in its own `try/except
    Exception`, separate from the `log()` call's, so a hostile
    `failure_reason_out` can never prevent this function from returning
    `None`, and vice versa.
    """
    attempt = 0
    try:
        cache_path: Optional[Path] = None
        if cache_dir is not None:
            cache_path = Path(cache_dir) / f"{_cache_key(state, questions, model)}.json"
            cached = _read_cache(cache_path, ttl_seconds)
            if cached is not None:
                return {
                    "answers": cached["answers"],
                    "usage": cached.get("usage"),
                    "model": cached.get("model", model),
                    "latency_ms": 0,
                    "cache_hit": True,
                }

        endpoint = _resolve_endpoint(os.environ, log)
        req = _build_request(endpoint, state, questions, model, api_key)

        start = time.monotonic()
        for attempt in (1, 2):
            try:
                remaining = total_budget_seconds - (time.monotonic() - start)
                attempt_timeout = min(timeout, remaining)
                with urllib.request.urlopen(req, timeout=attempt_timeout) as resp:
                    body = resp.read()
                data = json.loads(body)
                if not isinstance(data, dict) or "answers" not in data:
                    raise ValueError("jev response missing 'answers'")
                result = {
                    "answers": data["answers"],
                    "usage": data.get("usage"),
                    "model": data.get("model", model),
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "cache_hit": False,
                }
                if cache_path is not None:
                    _write_cache(cache_path, result)
                return result
            except urllib.error.HTTPError as exc:
                # Retry decision only (DD-4): 429/529 on the first attempt,
                # with >=1.0s remaining after the 0.3s backoff, retries;
                # anything else (wrong status, already-retried, insufficient
                # budget) is a terminal failure -- re-raise so the single
                # outer except below is the only place that logs+returns.
                status = exc.code
                remaining_after_failure = total_budget_seconds - (time.monotonic() - start)
                if (
                    attempt == 1
                    and status in RETRY_STATUSES
                    and remaining_after_failure - _RETRY_BACKOFF_SECONDS >= _MIN_REMAINING_AFTER_BACKOFF_SECONDS
                ):
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                    continue
                raise
        return None  # unreachable: loop always returns or raises above
    except Exception as exc:
        # Single terminal safety net (DD-2/DD-4): covers pre-loop setup
        # failures (bad `state`/`questions`/`timeout`), a broken clock
        # source, every non-retryable or already-retried HTTPError
        # re-raised above, socket/URL errors, mid-body read failures
        # (IncompleteRead/ConnectionResetError/MemoryError/RecursionError),
        # and malformed responses. `status` is derived from `exc` itself
        # (not a stale outer-scope variable) so a non-HTTPError failure on
        # attempt 2 never inherits an HTTPError status from attempt 1;
        # every non-HTTPError failure logs status=None. The log call
        # itself is wrapped so a hostile `log` still can't prevent this
        # function from returning None.
        try:
            log(
                "plugin_jev_client",
                {
                    "event": "jev_call",
                    "decision": "jev_call_failed",
                    "status": exc.code if isinstance(exc, urllib.error.HTTPError) else None,
                    "error": type(exc).__name__,
                    "attempt": attempt,
                },
            )
        except Exception:
            pass
        if failure_reason_out is not None:
            try:
                failure_reason_out["error"] = type(exc).__name__
                failure_reason_out["status"] = exc.code if isinstance(exc, urllib.error.HTTPError) else None
            except Exception:
                pass
        return None
