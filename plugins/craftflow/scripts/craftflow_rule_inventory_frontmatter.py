#!/usr/bin/env python3
"""Strict-subset skill frontmatter validator for craftflow_rule_inventory_units.py (stdlib only, no PyYAML).

The router's frontmatter is loaded into context on every turn, and a changed `name` or an added
`disable-model-invocation` would unload every kernel rule at runtime. The lenient parser in craftflow_router_util
accepts text that a real YAML loader rejects, so the inventory validates the block itself and fails closed on anything
outside this subset (P0 REM-FIX 6, F1-F6):

- top-level `key: value` lines only, keys limited to ALLOWED_KEYS, no duplicate keys, no tabs in indentation;
- a value is an inline scalar (a quoted string must close on its line), a `key:` followed by `- item` lines, or a block
  scalar (`|`, `>` with an optional `-`/`+`): the first non-blank content line sets the indent, every later non-blank
  line must be indented at least as far, and the block ends at the first non-blank line indented less;
- a plain inline scalar holds no `: ` and does not start with a YAML indicator character.

Python 3.9 compatible.
"""
from __future__ import annotations

import re
from typing import Dict, List, Union

ALLOWED_KEYS = ("name", "description", "allowed-tools", "user-invocable", "disable-model-invocation", "model", "skills")
KEY_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?:[ ]+(.*?))?[ ]*$")
BLOCK_INDICATOR = re.compile(r"^[|>][-+]?$")
ITEM = re.compile(r"^[ ]*-[ ]+(\S.*?)[ ]*$")
BAD_START = frozenset("@`%&*!{?|>#")
Value = Union[str, List[str]]


def _scalar(rest: str, key: str, n: int) -> str:
    if rest[0] in "\"'":
        if len(rest) < 2 or rest[-1] != rest[0]:
            raise ValueError(f"line {n}: the quoted value of '{key}' does not close on its line")
        return rest[1:-1]
    if rest[0] in BAD_START or ": " in rest or rest.endswith(":") or " #" in rest:
        raise ValueError(f"line {n}: the plain value of '{key}' is not valid YAML (quote it)")
    return rest


def _block(lines: List[str], i: int, key: str) -> "tuple[str, int]":
    """The block scalar whose content starts at lines[i]; returns (text, index of the first line after it)."""
    indent, body, j = None, [], i
    while j < len(lines):
        raw = lines[j]
        if not raw.strip():
            body.append("")
            j += 1
            continue
        lead = len(raw) - len(raw.lstrip(" "))
        if indent is None:
            if lead == 0:
                break  # an empty block scalar: the next key follows immediately
            indent = lead
        if lead < indent:
            break
        body.append(raw[indent:])
        j += 1
    return "\n".join(body).strip(), j


def parse_strict(block_lines: List[str]) -> Dict[str, Value]:
    """Parse the lines between the `---` markers; raises ValueError (message starts at 'line N') on anything outside
    the subset. Line numbers are relative to the first line after the opening marker."""
    fields: Dict[str, Value] = {}
    unknown: List[str] = []
    i = 0
    while i < len(block_lines):
        raw, n = block_lines[i], i + 1
        if not raw.strip():
            i += 1
            continue
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            raise ValueError(f"line {n}: tab in indentation")
        m = KEY_LINE.match(raw)
        if not m:
            raise ValueError(f"line {n}: not a top-level 'key: value' line or an indented continuation of a block: {raw.strip()[:40]!r}")
        key, rest = m.group(1), m.group(2) or ""
        if key not in ALLOWED_KEYS:
            unknown.append(key)  # keep parsing so the error names every unknown key at once
        if key in fields:
            raise ValueError(f"line {n}: duplicate key '{key}'")
        if not rest:
            items, j = [], i + 1
            while j < len(block_lines) and ITEM.match(block_lines[j]):
                items.append(ITEM.match(block_lines[j]).group(1))
                j += 1
            fields[key], i = (items if items else ""), j
        elif rest[0] in "|>":
            if not BLOCK_INDICATOR.match(rest):
                raise ValueError(f"line {n}: unsupported block scalar indicator {rest!r} for '{key}'")
            fields[key], i = _block(block_lines, i + 1, key)
        else:
            fields[key], i = _scalar(rest, key, n), i + 1
    if unknown:
        raise ValueError(f"unknown key(s) {', '.join(unknown)} (allowed: {', '.join(ALLOWED_KEYS)})")
    return fields


def keys_unit_text(fields: Dict[str, Value]) -> str:
    """The text of the whole-frontmatter unit: every key and value sorted by key. The description is a unit of its own,
    so only its presence is recorded here (an edit to it must not also change this unit)."""
    parts = []
    for key in sorted(fields):
        value = fields[key]
        flat = "*" if key == "description" else (", ".join(value) if isinstance(value, list) else str(value))
        parts.append(f"{key}={' '.join(flat.split())}")
    return "frontmatter " + "; ".join(parts)
