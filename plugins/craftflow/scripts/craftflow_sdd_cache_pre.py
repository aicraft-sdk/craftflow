#!/usr/bin/env python3
"""PreToolUse hook — URL freshness cache for WebFetch.

When a web-researcher or any agent issues a WebFetch, this hook checks whether
the URL was fetched before and validates freshness using HTTP conditional
requests (ETag/If-None-Match, Last-Modified/If-Modified-Since).

If the server returns 304 Not Modified, the hook injects the cached processed
reading directly into the session context (via stdout) and blocks the WebFetch
call, saving a full fetch round-trip.

Cache lives per-project at .craftflow/sdd-cache/<url-hash>.json
Each entry stores:
  - url: str
  - etag: str | null
  - last_modified: str | null
  - original_prompt: str (the WebFetch prompt parameter)
  - processed_reading: str (what the model extracted from the prior fetch)
  - cached_at: ISO timestamp

IMPORTANT: We only cache URLs that returned ETag or Last-Modified headers
(i.e., the server declared freshness semantics). URLs without these headers
are never cached — we cannot validate them cheaply.

Design mirrors addyosmani/agent-skills SDD cache pattern:
- Stores the model's processed reading, not raw HTML
- Uses HTTP conditional requests for freshness validation
- Entries without freshness validators are never cached
"""
import hashlib
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR_NAME = "sdd-cache"
MAX_CACHE_AGE_DAYS = 30


def _project_dir() -> Path:
    value = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(value) if value else Path.cwd()


def _cache_dir() -> Path:
    d = _project_dir() / ".craftflow" / CACHE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _url_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _cache_path(url: str) -> Path:
    return _cache_dir() / f"{_url_key(url)}.json"


def _load_entry(url: str) -> dict | None:
    path = _cache_path(url)
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        # Reject entries without freshness validators
        if not entry.get("etag") and not entry.get("last_modified"):
            return None
        # Reject entries older than MAX_CACHE_AGE_DAYS
        cached_at = entry.get("cached_at", "")
        if cached_at:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached_at)).days
                if age > MAX_CACHE_AGE_DAYS:
                    path.unlink(missing_ok=True)
                    return None
            except Exception:
                pass
        return entry
    except Exception:
        return None


def _validate_with_server(entry: dict) -> bool:
    """Send a HEAD request with conditional headers. Return True if 304."""
    url = entry["url"]
    headers = {}
    if entry.get("etag"):
        headers["If-None-Match"] = entry["etag"]
    if entry.get("last_modified"):
        headers["If-Modified-Since"] = entry["last_modified"]
    if not headers:
        return False
    try:
        req = urllib.request.Request(url, headers=headers, method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 304
    except urllib.error.HTTPError as e:
        return e.code == 304
    except Exception:
        return False


def _emit_cached_result(entry: dict) -> None:
    """Output a PreToolUse deny with the cached content as context."""
    cached_note = (
        f"[CRAFTFLOW SDD Cache HIT] URL: {entry['url']}\n"
        f"Cached: {entry.get('cached_at', 'unknown')}\n"
        f"Prompt used: {entry.get('original_prompt', '')}\n\n"
        f"--- Cached Processed Reading ---\n"
        f"{entry.get('processed_reading', '[content not available]')}\n"
        f"--- End of Cache ---\n\n"
        f"The server returned 304 Not Modified. Use the cached reading above "
        f"instead of fetching the URL again."
    )
    print(
        json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": cached_note,
            }
        }),
        ensure_ascii=True,
    )


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        data = json.loads(raw)
    except Exception:
        return 0

    if data.get("tool_name") != "WebFetch":
        return 0

    tool_input = data.get("tool_input") or {}
    url = tool_input.get("url", "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return 0

    entry = _load_entry(url)
    if entry is None:
        return 0  # not cached or no validators — let the fetch proceed

    if _validate_with_server(entry):
        _emit_cached_result(entry)
        return 0  # deny the WebFetch; model gets cached reading via deny reason

    # Server returned non-304 (content changed or validation failed) — let fetch proceed
    # The post hook will update the cache after the fresh fetch.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
