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

from craftflow_hooklib import log_event

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
        parsed = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as exc:
        # REM-FIX (silent-failure-hunter re-hunt, cycle 3, HIGH): every other
        # except branch in this file logs the degradation; this one was
        # silent. A corrupt/unreadable blocks.json is not fatal (the caller
        # falls through to the .orig fallback / returns False), but the
        # failure must be visible.
        log_event(
            "plugin_memory_protect_restore",
            {
                "event": "memory_protect_restore",
                "path": repr(str(index_path))[:512],
                "decision": "skip",
                "reason": "unresolvable-protect-restore-blocks-index",
                "error": repr(exc),
            },
        )
        return {}
    # REM-FIX (REM-FIX cycle 4, CRITICAL, both reviewer and hunter
    # independently live-reproduced): this function is type-hinted
    # `-> dict[str, str]` but the hint is not runtime-enforced. Syntactically
    # VALID JSON that is NOT a dict (a top-level array, string, number, null,
    # or a dict with non-string values) sailed past json.loads() untouched --
    # no exception, guard never engaged. `restore_file()`'s `blocks.get(...)`
    # then crashed with AttributeError (or `re.sub()`'s replacement crashed
    # with TypeError on a non-string value). Live-reproduced: a top-level
    # array crashes with `AttributeError: 'list' object has no attribute
    # 'get'`, exit 1.
    if not isinstance(parsed, dict) or not all(isinstance(v, str) for v in parsed.values()):
        log_event(
            "plugin_memory_protect_restore",
            {
                "event": "memory_protect_restore",
                "path": repr(str(index_path))[:512],
                "decision": "skip",
                "reason": "unresolvable-protect-restore-blocks-index-type",
                "error": repr(type(parsed).__name__),
            },
        )
        return {}
    return parsed


def _sha1_key(path: Path) -> str:
    import hashlib
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]


def restore_file(target: Path) -> bool:
    """Expand any CRAFTFLOW_BLOCK placeholders in target back to original content.

    Returns True if any substitution was made.
    """
    if not target.exists():
        return False
    # REM-FIX (silent-failure-hunter re-hunt, cycle 2): read_text() was
    # unguarded here. Reachable from main()'s PostToolUse Edit|Write branch
    # (fires on EVERY Edit/Write, after the .resolve() guard added in
    # commit 9a7702c) and from restore_all()'s Pass 2 rglob sweep (a single
    # non-UTF-8 .md file previously aborted the whole sweep). Any binary or
    # non-UTF-8-encoded file crashed this with UnicodeDecodeError -- degrade
    # to "not restored" instead, with the failure logged for visibility.
    try:
        text = target.read_text(encoding="utf-8")
    except Exception as exc:
        log_event(
            "plugin_memory_protect_restore",
            {
                "event": "memory_protect_restore",
                "path": repr(str(target))[:512],
                "decision": "skip",
                "reason": "unresolvable-protect-restore-content",
                "error": repr(exc),
            },
        )
        return False
    if "CRAFTFLOW_BLOCK_" not in text:
        return False

    key = _sha1_key(target)
    blocks = _load_blocks(key)
    if not blocks:
        # Try restoring from .orig backup directly
        orig_path = _cache_dir() / f"{key}.orig"
        if orig_path.exists():
            # REM-FIX (silent-failure-hunter re-hunt, cycle 3, CRITICAL #1):
            # this read was unguarded -- same UnicodeDecodeError crash class
            # as the target.read_text() site above, but on the cache's
            # backup copy. Degrade to "not restored" instead of crashing.
            try:
                backup = orig_path.read_text(encoding="utf-8")
            except Exception as exc:
                log_event(
                    "plugin_memory_protect_restore",
                    {
                        "event": "memory_protect_restore",
                        "path": repr(str(orig_path))[:512],
                        "decision": "skip",
                        "reason": "unresolvable-protect-restore-backup",
                        "error": repr(exc),
                    },
                )
                return False
            # REM-FIX (silent-failure-hunter re-hunt, cycle 3, CRITICAL #2):
            # the write-back was unguarded -- a read-only state directory (or
            # any other PermissionError/OSError) crashed the hook instead of
            # degrading. Restore_all()'s Pass 1 already wraps its own,
            # equivalent write-back this way; mirror that shape here.
            try:
                tmp = target.with_suffix(".tmp")
                tmp.write_text(backup, encoding="utf-8")
                tmp.replace(target)
            except Exception as exc:
                log_event(
                    "plugin_memory_protect_restore",
                    {
                        "event": "memory_protect_restore",
                        "path": repr(str(target))[:512],
                        "decision": "skip",
                        # REM-FIX (REM-FIX cycle 4, MEDIUM): this reason was
                        # previously identical to the block-substitution
                        # write-back below ("unresolvable-protect-restore-write"),
                        # making the two write-back failure paths
                        # indistinguishable in the structured log. Distinct
                        # reason for the .orig-fallback path.
                        "reason": "unresolvable-protect-restore-write-fallback",
                        "error": repr(exc),
                    },
                )
                return False
            return True
        return False

    def replace_block(m: re.Match) -> str:
        block_id = m.group(1)
        return blocks.get(block_id, m.group(0))  # leave placeholder if not found

    restored = BLOCK_RE.sub(replace_block, text)
    if restored == text:
        return False

    # REM-FIX (silent-failure-hunter re-hunt, cycle 3, CRITICAL #2): second,
    # separate unguarded write-back site (block-substitution path instead of
    # the .orig-fallback path above). Same PermissionError/OSError vector.
    try:
        tmp = target.with_suffix(".tmp")
        tmp.write_text(restored, encoding="utf-8")
        tmp.replace(target)
    except Exception as exc:
        log_event(
            "plugin_memory_protect_restore",
            {
                "event": "memory_protect_restore",
                "path": repr(str(target))[:512],
                "decision": "skip",
                # REM-FIX (REM-FIX cycle 4, MEDIUM): this reason was
                # previously identical to the .orig-fallback write-back above
                # ("unresolvable-protect-restore-write"), making the two
                # write-back failure paths indistinguishable in the
                # structured log. Distinct reason for the block-substitution
                # path.
                "reason": "unresolvable-protect-restore-write-substitution",
                "error": repr(exc),
            },
        )
        return False
    return True


def restore_all() -> int:
    """Restore every .orig file in cache back to its source path."""
    cache = _cache_dir()
    if not cache.exists():
        return 0
    count = 0

    # Pass 1: for each .orig backup, find the matching state/ file by sha1 key
    # and restore it directly, then clean up the backup artifacts.
    state_base = _project_dir() / ".craftflow" / "state"
    if state_base.exists():
        for orig_path in cache.glob("*.orig"):
            key = orig_path.stem
            for md_file in state_base.rglob("*.md"):
                if _sha1_key(md_file) == key:
                    try:
                        orig_content = orig_path.read_text(encoding="utf-8")
                        tmp = md_file.with_suffix(".tmp")
                        tmp.write_text(orig_content, encoding="utf-8")
                        tmp.replace(md_file)
                        orig_path.unlink(missing_ok=True)
                        (cache / f"{key}.blocks.json").unlink(missing_ok=True)
                        (cache / f"{key}.lock").unlink(missing_ok=True)
                        count += 1
                    except Exception as e:
                        # REM-FIX (silent-failure-hunter re-hunt, cycle 3,
                        # HIGH): this failure was reported via stderr only,
                        # inconsistent with every other failure path in this
                        # file (log_event). Stderr from a Stop hook is not
                        # durably captured anywhere, so the same failure
                        # silently repeats on every subsequent Stop hook
                        # invocation with no structured log record. Keep the
                        # stderr print (existing behavior) and add the
                        # structured log alongside it.
                        print(f"CRAFTFLOW restore_all: failed to restore {md_file}: {e}", file=sys.stderr)
                        log_event(
                            "plugin_memory_protect_restore",
                            {
                                "event": "memory_protect_restore",
                                "path": repr(str(md_file))[:512],
                                "decision": "skip",
                                "reason": "unresolvable-protect-restore-all-pass1-write",
                                "error": repr(e),
                            },
                        )
                    break

    # Pass 2: belt-and-suspenders scan for any remaining placeholder text
    # (covers files whose .orig was already cleaned up or never written).
    state_root = _project_dir() / ".craftflow" / "state"
    if not state_root.exists():
        return count
    for md_file in state_root.rglob("*.md"):
        if restore_file(md_file):
            count += 1
    return count


def main() -> int:
    raw = sys.stdin.read()
    data: dict = {}
    if raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception as exc:
            # REM-FIX (silent-failure-hunter re-hunt, cycle 3, MEDIUM):
            # malformed stdin JSON was silently swallowed -- `data` stays
            # `{}`, which is a safe default (falls into the full-restore
            # branch, not a masked security error), but unlike every other
            # except branch in this file, it went unlogged.
            log_event(
                "plugin_memory_protect_restore",
                {
                    "event": "memory_protect_restore",
                    "decision": "default-full-restore",
                    "reason": "unresolvable-protect-restore-stdin",
                    "error": repr(exc),
                },
            )
        else:
            # REM-FIX (silent-failure-hunter re-hunt, cycle 3, sibling gap
            # found during the mandated exhaustive final sweep, not one of
            # the originally-enumerated findings): syntactically VALID JSON
            # that is not a dict (e.g. a top-level array) sailed past the
            # except above untouched, then crashed on `data.get(...)` below
            # with AttributeError. Live-reproduced: exit 1.
            if isinstance(parsed, dict):
                data = parsed
            else:
                log_event(
                    "plugin_memory_protect_restore",
                    {
                        "event": "memory_protect_restore",
                        "decision": "default-full-restore",
                        "reason": "unresolvable-protect-restore-stdin-type",
                        "error": repr(type(parsed).__name__),
                    },
                )

    hook_event = data.get("hook_event_name", "")

    if hook_event in ("Stop", "StopFailure", "SubagentStop", "") or not hook_event:
        # Full restore on session end or unknown context
        restore_all()
        return 0

    # PostToolUse — check if the written file has leaked placeholders
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    # REM-FIX (silent-failure-hunter re-hunt, cycle 3, THIRD sibling gap
    # found during the mandated exhaustive final sweep): `or {}` only guards
    # a falsy `tool_input` (None, "", etc.) -- a truthy non-dict value (e.g.
    # a JSON string) sails through and crashes on `.get("file_path")` below
    # with AttributeError. Live-reproduced: exit 1.
    if not isinstance(tool_input, dict):
        log_event(
            "plugin_memory_protect_restore",
            {
                "event": "memory_protect_restore",
                "tool_name": tool_name,
                "decision": "skip",
                "reason": "unresolvable-protect-restore-tool-input-type",
                "error": repr(type(tool_input).__name__),
            },
        )
        return 0
    file_path_str = tool_input.get("file_path")
    if file_path_str and tool_name in ("Edit", "Write"):
        # REM-FIX sibling finding (doubt-verifier, Phase 1 fix-verify cycle of
        # the ADR 0035 deferred-crash plan): this `.resolve()` was unguarded.
        # This script runs on PostToolUse for every Edit/Write -- AFTER the
        # write already happened -- so `target` here is only used to check
        # THIS ONE file for a leaked CRAFTFLOW_BLOCK_ placeholder
        # (belt-and-suspenders; the real masking/restore state lives in
        # restore_all()'s Stop-hook sweep, unaffected by this branch). It is
        # not load-bearing for the rest of main(), which returns 0 either
        # way. A self-referential symlink (or NUL-byte) `file_path` raising
        # RuntimeError/ValueError uncaught crashed this whole hook -- skip
        # just this one file's restore-check instead, but log it so the
        # failure has visibility rather than a silent skip.
        try:
            target = Path(file_path_str).resolve()
        except Exception as exc:
            log_event(
                "plugin_memory_protect_restore",
                {
                    "event": "memory_protect_restore",
                    "tool_name": tool_name,
                    "path": repr(file_path_str)[:512],
                    "decision": "skip",
                    "reason": "unresolvable-protect-restore-target",
                    "error": repr(exc),
                },
            )
            return 0
        restore_file(target)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
