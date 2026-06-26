#!/usr/bin/env python3
"""Restore all memory files masked by craftflow_memory_protect_pre.py.

Called by the Stop hook (and optionally PostToolUse) to expand any
CRAFTFLOW_BLOCK_<sha1> placeholders back to their original content.

Also called defensively after any Edit/Write that touches a .craftflow/state/ file,
in case a placeholder leaked through the write-guard (belt-and-suspenders).
"""
import json
import os
import re
import sys
from pathlib import Path

CACHE_DIR_NAME = ".memory-protect-cache"
BLOCK_RE = re.compile(r"<!-- CRAFTFLOW_BLOCK_([0-9a-f]{12}) -->")


def _project_dir() -> Path:
    value = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(value) if value else Path.cwd()


def _cache_dir() -> Path:
    return _project_dir() / ".craftflow" / CACHE_DIR_NAME


def _load_blocks(orig_key: str) -> dict[str, str]:
    index_path = _cache_dir() / f"{orig_key}.blocks.json"
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sha1_key(path: Path) -> str:
    import hashlib
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]


def restore_file(target: Path) -> bool:
    """Expand any CRAFTFLOW_BLOCK placeholders in target back to original content.

    Returns True if any substitution was made.
    """
    if not target.exists():
        return False
    text = target.read_text(encoding="utf-8")
    if "CRAFTFLOW_BLOCK_" not in text:
        return False

    key = _sha1_key(target)
    blocks = _load_blocks(key)
    if not blocks:
        # Try restoring from .orig backup directly
        orig_path = _cache_dir() / f"{key}.orig"
        if orig_path.exists():
            backup = orig_path.read_text(encoding="utf-8")
            tmp = target.with_suffix(".tmp")
            tmp.write_text(backup, encoding="utf-8")
            tmp.replace(target)
            return True
        return False

    def replace_block(m: re.Match) -> str:
        block_id = m.group(1)
        return blocks.get(block_id, m.group(0))  # leave placeholder if not found

    restored = BLOCK_RE.sub(replace_block, text)
    if restored == text:
        return False

    tmp = target.with_suffix(".tmp")
    tmp.write_text(restored, encoding="utf-8")
    tmp.replace(target)
    return True


def restore_all() -> int:
    """Restore every .orig file in cache back to its source path."""
    cache = _cache_dir()
    if not cache.exists():
        return 0
    count = 0
    for orig_path in cache.glob("*.orig"):
        # The orig file's stem is the sha1 key of the target path.
        # We stored the target path implicitly — we need to find files that
        # currently contain CRAFTFLOW_BLOCK placeholders.
        pass

    # Scan .craftflow/state/ for any files with stale placeholders and restore them
    state_root = _project_dir() / ".craftflow" / "state"
    if not state_root.exists():
        return 0
    for md_file in state_root.rglob("*.md"):
        if restore_file(md_file):
            count += 1
    return count


def main() -> int:
    raw = sys.stdin.read()
    data: dict = {}
    if raw.strip():
        try:
            data = json.loads(raw)
        except Exception:
            pass

    hook_event = data.get("hook_event_name", "")

    if hook_event in ("Stop", "StopFailure", "") or not hook_event:
        # Full restore on session end or unknown context
        restore_all()
        return 0

    # PostToolUse — check if the written file has leaked placeholders
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    file_path_str = tool_input.get("file_path")
    if file_path_str and tool_name in ("Edit", "Write"):
        target = Path(file_path_str).resolve()
        restore_file(target)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
