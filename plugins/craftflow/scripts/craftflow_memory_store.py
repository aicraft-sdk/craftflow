#!/usr/bin/env python3
"""The cloud harness memory-store Edit/Write grant (ADR 0084). Standard library only; never raises.

In a cloud project thread the harness mounts a project-memory folder outside the working directory and names it only
in the Claude Code process environment, which hooks inherit (an agent's Bash tool cannot change a hook's
environment):

    CLAUDE_COWORK_MEMORY_PATH_OVERRIDE   the memory root, e.g. /tmp/claude/memory
    CLAUDE_MEMORY_STORES                 JSON list of {"mount", "mode", ...}

A store directory is `<root>/<mount>` or `<root>/<scope>/<mount>` (one scope segment, such as `team`). This is the
one confinement grant read from the environment: ADR 0035 anchors identity on the payload cwd because environment
identity can go stale, but a memory store has no cwd-derived form. The grant stays narrow instead: rw stores only,
plain mount names only, `.md` files directly inside a store directory only, compared on the resolved target.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List, Mapping, Optional

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def harness_memory_store_dirs(env: Optional[Mapping[str, str]] = None) -> List[Path]:
    """Writable store directories, or [] (fail closed). Kept: mode "rw", a plain mount name, an absolute root that
    is not a symlink and resolves to itself, and a store directory that exists and resolves to itself."""
    env = os.environ if env is None else env
    try:
        root_raw = env.get("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE")
        stores_raw = env.get("CLAUDE_MEMORY_STORES")
        if not root_raw or not stores_raw:
            return []
        root = Path(root_raw)
        if not root.is_absolute() or root.is_symlink() or not root.is_dir() or root.resolve() != root:
            return []
        stores = json.loads(stores_raw)
        if not isinstance(stores, list):
            return []
        scopes = [p for p in sorted(root.iterdir())
                  if _NAME_RE.fullmatch(p.name) and p.is_dir() and not p.is_symlink()]
        out: List[Path] = []
        for store in stores:
            if not isinstance(store, dict) or store.get("mode") != "rw":
                continue
            mount = store.get("mount")
            if not isinstance(mount, str) or _NAME_RE.fullmatch(mount) is None:
                continue
            for cand in [root / mount] + [scope / mount for scope in scopes]:
                if cand.is_dir() and not cand.is_symlink() and cand.resolve() == cand and cand not in out:
                    out.append(cand)
        return out
    except Exception:  # any failure means no grant
        return []


def harness_memory_grant(resolved: Path, env: Optional[Mapping[str, str]] = None) -> bool:
    """True when the RESOLVED Edit/Write target is a `.md` file directly inside a writable store. Edit/Write only:
    callers never use it for Bash confinement or the protected-path lanes. Never raises."""
    try:
        return resolved.suffix == ".md" and resolved.parent in harness_memory_store_dirs(env)
    except Exception:  # never raise: no grant
        return False
