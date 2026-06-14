#!/usr/bin/env python3
"""PostToolUse hook — update SDD URL freshness cache after WebFetch.

After a successful WebFetch, this hook:
1. Extracts ETag and Last-Modified from the response headers (via output metadata)
2. Stores the processed reading placeholder for the router to fill in

Note: The "processed reading" is the model's extracted content from the fetched
page — not the raw HTML. Since the post hook runs before the model processes
the response, we store a sentinel that the router/agent can update via a
separate mechanism. The core value is the freshness headers (ETag/Last-Modified)
which enable the PreToolUse hook to serve 304s on future identical fetches.

Cache entry shape:
{
  "url": str,
  "etag": str | null,
  "last_modified": str | null,
  "original_prompt": str,
  "processed_reading": str,  -- placeholder; agent fills this after extraction
  "cached_at": ISO timestamp
}

We skip caching if the response headers do not include ETag or Last-Modified —
without those, we cannot validate freshness on the next request.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR_NAME = "sdd-cache"


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_headers(tool_response: dict) -> dict[str, str | None]:
    """Try to extract ETag and Last-Modified from WebFetch tool response."""
    headers: dict[str, str | None] = {"etag": None, "last_modified": None}
    # Claude Code exposes response metadata in tool_response under various keys;
    # the exact schema may vary. We attempt a best-effort extraction.
    content = tool_response.get("content", "")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content = block.get("content", "")
                break
    if isinstance(content, str):
        for line in content.splitlines():
            low = line.lower()
            if low.startswith("etag:"):
                headers["etag"] = line.split(":", 1)[1].strip()
            elif low.startswith("last-modified:"):
                headers["last_modified"] = line.split(":", 1)[1].strip()
    return headers


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

    prompt = tool_input.get("prompt", "")
    tool_response = data.get("tool_response") or {}

    headers = _extract_headers(tool_response)

    # Only cache if the server declared freshness semantics
    if not headers["etag"] and not headers["last_modified"]:
        return 0

    cache_path = _cache_path(url)

    # Load existing entry to preserve processed_reading if already set
    existing_reading = ""
    if cache_path.exists():
        try:
            existing = json.loads(cache_path.read_text(encoding="utf-8"))
            existing_reading = existing.get("processed_reading", "")
        except Exception:
            pass

    entry = {
        "url": url,
        "etag": headers["etag"],
        "last_modified": headers["last_modified"],
        "original_prompt": prompt,
        "processed_reading": existing_reading or "[pending — agent has not yet extracted reading]",
        "cached_at": _now_iso(),
    }

    tmp = cache_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=True), encoding="utf-8")
    tmp.replace(cache_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
