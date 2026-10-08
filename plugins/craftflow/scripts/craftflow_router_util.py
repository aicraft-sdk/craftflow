#!/usr/bin/env python3
"""Shared helpers for the router corpus tooling: atomic writes, strict JSON, fence scanning, front matter.

Split out of craftflow_router_corpus.py (kept under its line cap); the corpus module re-exports every name here.
Python 3.9 compatible.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def reject_json_constant(constant: str):
    """json.loads parse_constant hook: refuse NaN/Infinity tokens in hand-edited fixtures (H-L5)."""
    raise ValueError(f"invalid numeric literal in JSON: {constant}")


def load_json(path):
    """Read a JSON file; NaN/Infinity are errors."""
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject_json_constant)


def atomic_write(path, data: bytes) -> None:
    """Write via mkstemp + os.replace while holding an flock on the directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    dfd = os.open(str(path.parent), os.O_RDONLY)
    try:
        fcntl.flock(dfd, fcntl.LOCK_EX)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
        try:
            os.fchmod(fd, 0o644)  # mkstemp creates 0600; these are committed, world-readable files
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, str(path))
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    finally:
        fcntl.flock(dfd, fcntl.LOCK_UN)
        os.close(dfd)


def fence_scan(lines: List[str], prose_text_fences: bool = False) -> Tuple[List[bool], Optional[int]]:
    """(mask, unclosed): mask is True for lines inside a fenced block or on its delimiters.

    `unclosed` is the 1-based line of a fence that never closes (it silently masks the rest of the file), else None.
    With `prose_text_fences`, the inner lines of a fence whose info string is `text` count as prose (the router
    keeps numbered rule lists in such fences, e.g. the Task*-tool fallback loop), so only its delimiters are masked."""
    mask, opener, prose, opened_at = [], None, False, 0
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if opener is None:
            m = re.match(r"^(`{3,}|~{3,})\s*(\w*)", s)
            if m:
                opener, prose, opened_at = m.group(1), prose_text_fences and m.group(2) == "text", n
            mask.append(opener is not None)
        else:
            closing = s.startswith(opener[0] * len(opener)) and not s.strip(opener[0])
            mask.append(closing or not prose)
            if closing:
                opener = None
    return mask, (opened_at if opener is not None else None)


def fence_mask(lines: List[str], prose_text_fences: bool = False) -> List[bool]:
    return fence_scan(lines, prose_text_fences)[0]


def parse_frontmatter(text: str) -> Dict[str, object]:
    """Port of gen-craftflow-graph.mjs parseFrontmatter (scalars, lists, `key: |` block scalars)."""
    m = re.match(r"^---\n([\s\S]*?)\n---\n", text)
    if not m:
        return {}
    lines, fields, i = m.group(1).split("\n"), {}, 0
    while i < len(lines):
        kv = re.match(r"^([a-zA-Z_-]+):\s*(.*)$", lines[i])
        if not kv:
            i += 1
            continue
        key, rest = kv.group(1), kv.group(2)
        if rest in ("|", ">"):
            block, i = [], i + 1
            while i < len(lines) and (lines[i].startswith("  ") or lines[i].strip() == ""):
                block.append(re.sub(r"^ {2}", "", lines[i]))
                i += 1
            fields[key] = "\n".join(block).strip()
            continue
        if rest == "":
            items, j = [], i + 1
            while j < len(lines) and re.match(r"^\s*-\s+", lines[j]):
                items.append(re.sub(r"^\s*-\s+", "", lines[j]).strip())
                j += 1
            fields[key], i = (items, j) if items else ("", i + 1)
            continue
        fields[key] = re.sub(r'^"(.*)"$', r"\1", rest.strip())
        i += 1
    return fields
