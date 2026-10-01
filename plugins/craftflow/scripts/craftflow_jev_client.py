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
- DD-14 No env/repo-controlled endpoint: `ENDPOINT` is the default. The
  `CRAFTFLOW_JEV_ENDPOINT` env var is NOT consulted (a repo
  `.claude/settings.json` env block could otherwise redirect the bearer key
  to a local listener). The only override is the user-level file
  `~/.claude/craftflow/jev-endpoint.json` (`{"endpoint": "<url>"}`), located
  via the passwd-database home (never `$HOME`), read only on the network
  path (after a cache miss), and honored solely when its host is loopback
  (127.0.0.1, localhost, ::1). A non-loopback value is ignored and logged as
  `endpoint_override_ignored`; a missing/unreadable/malformed file yields
  `ENDPOINT` (fail open).
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
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
_MAX_ENDPOINT_FILE_BYTES = 4096


def _passwd_home() -> Optional[str]:
    """Home from the passwd database; ignores $HOME. None when unavailable."""
    try:
        import pwd  # lazy: POSIX only

        return pwd.getpwuid(os.getuid()).pw_dir
    except Exception:  # fail open: unresolved home
        return None


def _default_endpoint_path() -> Optional[Path]:
    home = _passwd_home()
    if not home:
        return None
    return Path(home) / ".claude" / "craftflow" / "jev-endpoint.json"


class _UnreadableEndpointFile(Exception):
    """Internal: carries a non-sensitive reason token (never file contents)."""


def _log_safely(log, decision: str, error: Optional[str] = None) -> None:
    payload: Dict[str, Any] = {"event": "jev_call", "decision": decision}
    if error is not None:
        payload["error"] = error
    try:
        log("plugin_jev_client", payload)
    except Exception:
        pass  # a hostile log callable must never break endpoint resolution


def _read_endpoint_file(path: Optional[Path], log=None) -> Optional[str]:
    """Return the `endpoint` string from the user file, or None on any problem.

    A missing file is silent. Any other problem (non-regular file, oversized,
    unreadable, malformed, wrong shape) logs exactly one
    `endpoint_file_unreadable` event carrying only an exception type name or
    reason token -- never the path contents or value.
    """
    if path is None:
        return None
    try:
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(st.st_mode):
            raise _UnreadableEndpointFile("not_regular_file")
        # O_NONBLOCK: never block on a FIFO swapped in after the stat;
        # O_NOFOLLOW: refuse a symlink swapped in after the stat.
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise _UnreadableEndpointFile("not_regular_file")
            raw = b""
            while len(raw) <= _MAX_ENDPOINT_FILE_BYTES:
                chunk = os.read(fd, _MAX_ENDPOINT_FILE_BYTES + 1 - len(raw))
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(fd)
        if len(raw) > _MAX_ENDPOINT_FILE_BYTES:
            raise _UnreadableEndpointFile("too_large")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise _UnreadableEndpointFile("not_object")
        value = data.get("endpoint")
        if not isinstance(value, str) or not value:
            raise _UnreadableEndpointFile("endpoint_not_string")
        return value
    except _UnreadableEndpointFile as exc:
        reason = str(exc)
    except Exception as exc:  # unreadable/malformed -> fail open, token only
        reason = type(exc).__name__
    if log is not None:
        _log_safely(log, "endpoint_file_unreadable", reason)
    return None


def _is_safe_loopback_url(url: str) -> bool:
    """True only for an unambiguous http(s) loopback URL (DD-14).

    urlsplit and urllib.request.Request must agree on the host, and anything
    parser-differential bait (userinfo, backslash, whitespace, control chars)
    is rejected outright.
    """
    try:
        if any(ch == "\\" or ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in url):
            return False
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return False
        if "@" in parts.netloc or parts.username is not None or parts.password is not None:
            return False
        hostname = parts.hostname
        if hostname not in _LOOPBACK_HOSTS:
            return False
        req_host = urllib.request.Request(url).host
        if req_host.startswith("["):
            req_host = req_host[1:].split("]", 1)[0]
        else:
            req_host = req_host.rsplit(":", 1)[0] if ":" in req_host else req_host
        return req_host.lower() == hostname
    except Exception:
        return False


def _resolve_endpoint(log, config_path: Optional[Path] = None) -> str:
    """Return ENDPOINT unless the user-level file names a loopback URL (DD-14)."""
    path = config_path if config_path is not None else _default_endpoint_path()
    if path is None:
        _log_safely(log, "endpoint_home_unresolved")
        return ENDPOINT
    override = _read_endpoint_file(path, log)
    if not override:
        return ENDPOINT
    if _is_safe_loopback_url(override):
        return override
    _log_safely(log, "endpoint_override_ignored")
    return ENDPOINT


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Fail any 3xx so the bearer key can never be forwarded to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen(req, timeout=None):
    return _OPENER.open(req, timeout=timeout)


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
    endpoint_config_path: Optional[Path] = None,
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

        endpoint = _resolve_endpoint(log, endpoint_config_path)
        req = _build_request(endpoint, state, questions, model, api_key)

        start = time.monotonic()
        for attempt in (1, 2):
            try:
                remaining = total_budget_seconds - (time.monotonic() - start)
                attempt_timeout = min(timeout, remaining)
                with _urlopen(req, timeout=attempt_timeout) as resp:
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
