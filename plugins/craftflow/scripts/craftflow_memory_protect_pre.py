#!/usr/bin/env python3
"""PreToolUse hook — memory file content masking.

When a builder agent reads a .craftflow/v10/ memory markdown file, this hook
temporarily replaces section bodies with <!-- CRAFTFLOW_BLOCK_<sha1> --> placeholders
so the model cannot see (and be tempted to restructure) the internal content.

Section HEADINGS are preserved so the model still knows the file structure.
Only the content below each heading is masked.

Design mirrors addyosmani/agent-skills simplify-ignore pattern:
- Replaces content in-place (atomically via temp file)
- Saves originals to .craftflow/.memory-protect-cache/<sha1>.orig
- 60-second stale lock prevents clobbering from concurrent sessions
- craftflow_memory_protect_restore.py (called by Stop hook) restores all originals

Because writes to memory files are already blocked by craftflow_pretooluse_guard.py,
this hook adds read-level protection: the model sees masked content and is less
likely to attempt structural edits that would then be blocked.
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

LOCK_STALE_SECONDS = 60
CACHE_DIR_NAME = ".memory-protect-cache"


def _project_dir() -> Path:
    value = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(value) if value else Path.cwd()


def _cache_dir() -> Path:
    d = _project_dir() / ".craftflow" / CACHE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_memory_file(path: Path) -> bool:
    """Return True if path is a .craftflow/v10/ memory markdown file."""
    try:
        rel = path.relative_to(_project_dir())
    except ValueError:
        return False
    parts = rel.parts
    # Must start with .craftflow/v10/
    if len(parts) < 3 or parts[0] != ".craftflow" or parts[1] != "v10":
        return False
    # Must end in .md
    if not path.suffix == ".md":
        return False
    # Exclude hooks/scripts/plugin files — only state markdown
    filename = path.name
    protected = {"activeContext.md", "patterns.md", "progress.md"}
    if filename in protected:
        return True
    # Also protect project/ and workflows/*/ tiers
    if len(parts) >= 4 and parts[2] in ("project", "workflows"):
        return True
    return False


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _lock_path(file_path: Path) -> Path:
    key = _sha1(str(file_path))
    return _cache_dir() / f"{key}.lock"


def _acquire_lock(file_path: Path) -> bool:
    lock = _lock_path(file_path)
    if lock.exists():
        age = time.time() - lock.stat().st_mtime
        if age < LOCK_STALE_SECONDS:
            return False  # already locked by another session
        lock.unlink(missing_ok=True)  # stale — reclaim it
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_lock(file_path: Path) -> None:
    _lock_path(file_path).unlink(missing_ok=True)


def _mask_content(text: str) -> tuple[str, dict[str, str]]:
    """Replace section bodies with BLOCK placeholders.

    Preserves heading lines; replaces everything below each heading
    (up to the next heading or EOF) with a single placeholder comment.

    Returns (masked_text, {block_id: original_body}) mapping.
    """
    lines = text.splitlines(keepends=True)
    blocks: dict[str, str] = {}
    output: list[str] = []
    body_lines: list[str] = []
    current_heading: str | None = None

    def flush_heading() -> None:
        nonlocal body_lines, current_heading
        if current_heading is not None and body_lines:
            body = "".join(body_lines)
            stripped = body.strip()
            if stripped:  # only mask non-empty bodies
                block_id = _sha1(stripped)
                blocks[block_id] = body
                output.append(f"<!-- CRAFTFLOW_BLOCK_{block_id} -->\n")
            else:
                output.extend(body_lines)
        elif body_lines:
            output.extend(body_lines)
        body_lines = []
        current_heading = None

    for line in lines:
        if line.startswith("## ") or line.startswith("# "):
            flush_heading()
            current_heading = line
            output.append(line)
        else:
            body_lines.append(line)

    flush_heading()
    return "".join(output), blocks


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        data = json.loads(raw)
    except Exception:
        return 0

    # Only fire on Read tool
    if data.get("tool_name") not in ("Read",):
        return 0

    tool_input = data.get("tool_input") or {}
    file_path_str = tool_input.get("file_path")
    if not file_path_str:
        return 0

    target = Path(file_path_str).resolve()
    if not _is_memory_file(target):
        return 0
    if not target.exists():
        return 0

    # Acquire lock
    if not _acquire_lock(target):
        return 0  # another session is already masking this file

    try:
        original = target.read_text(encoding="utf-8")
        masked, blocks = _mask_content(original)

        if not blocks:
            return 0  # nothing to mask

        # Save original to cache
        cache = _cache_dir()
        orig_key = _sha1(str(target))
        orig_path = cache / f"{orig_key}.orig"
        index_path = cache / f"{orig_key}.blocks.json"

        if not orig_path.exists():
            orig_path.write_text(original, encoding="utf-8")
        existing_blocks: dict[str, str] = {}
        if index_path.exists():
            try:
                existing_blocks = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing_blocks.update(blocks)
        index_path.write_text(
            json.dumps(existing_blocks, ensure_ascii=True, indent=2), encoding="utf-8"
        )

        # Atomically write masked version
        tmp = target.with_suffix(".tmp")
        tmp.write_text(masked, encoding="utf-8")
        tmp.replace(target)
    finally:
        _release_lock(target)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
