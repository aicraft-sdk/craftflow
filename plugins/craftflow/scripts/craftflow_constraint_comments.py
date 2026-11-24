#!/usr/bin/env python3
"""List the constraint comments a diff adds, and the check each could become. Read-only; standard library only.

After Cursor's no-comments skill: a comment that states a rule ("never null here", "keep in sync with X", "only call
after init") is a check nobody runs. The reviewer should ask for it to be encoded as a type, a runtime assertion, a
test or a lint rule, or kept with a reason when none of those can hold it. This script only finds the candidates and
proposes a channel; skills/code-review-patterns/references/code-review-heuristics.md says how to judge them.

Usage:
    craftflow_constraint_comments.py [DIFF]      a unified diff (e.g. a review package); stdin when omitted

Prints one JSON line: {"comments": [{"file", "line", "text", "channel"}], "scanned_files": N}. Exit 0 always, 2 when
DIFF cannot be read. Python 3.9.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List, Optional

_COMMENT_RE = re.compile(r"^\s*(?:#(?!!)|//|/\*+|\*(?!/)|--)\s?(.*?)\s*(?:\*/)?\s*$")
_CONSTRAINT_RE = re.compile(
    r"\b(?:must(?:\s+not)?|never|always|do\s+not|don't|keep\s+in\s+sync|in\s+sync\s+with|invariant|assumes?|"
    r"only\s+(?:call|use|valid)|cannot\s+be|can't\s+be|should\s+not|shouldn't|required\s+to|guaranteed|non-?null|"
    r"not\s+null|before\s+calling|after\s+calling)\b",
    re.IGNORECASE,
)
# First match wins: (pattern, channel).
_CHANNELS = (
    (r"\b(?:keep\s+in\s+sync|in\s+sync\s+with|mirror|same\s+as|matches?)\b", "test"),
    (r"\b(?:import|depend|layer|call(?:ed)?\s+from|only\s+from)\b", "lint"),
    (r"\b(?:null|none|nil|undefined|optional|type|int|str|string|enum|non-?empty)\b", "type"),
    (r"\b(?:positive|negative|zero|range|between|>=|<=|greater|less|at\s+least|at\s+most|before|after|once)\b",
     "runtime assertion"),
)
_SKIP_RE = re.compile(r"\b(?:noqa|type:\s*ignore|eslint-disable|pragma|cf:shortcut:)", re.IGNORECASE)


def channel_for(text: str) -> str:
    for pattern, channel in _CHANNELS:
        if re.search(pattern, text, re.IGNORECASE):
            return channel
    return "test"


def scan(diff: str) -> dict:
    comments: List[dict] = []
    files = set()
    current: Optional[str] = None
    line_no = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            current = None if path == "/dev/null" else re.sub(r"^b/", "", path)
            if current:
                files.add(current)
            continue
        hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", raw)
        if hunk:
            line_no = int(hunk.group(1))
            continue
        if current is None or raw.startswith("--- "):
            continue
        if raw.startswith("+"):
            match = _COMMENT_RE.match(raw[1:])
            text = match.group(1) if match else ""
            if text and _CONSTRAINT_RE.search(text) and not _SKIP_RE.search(text):
                comments.append({"file": current, "line": line_no, "text": text[:200], "channel": channel_for(text)})
            line_no += 1
        elif not raw.startswith("-"):
            line_no += 1
    return {"comments": comments, "scanned_files": len(files)}


def main(argv: Optional[List[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        text = Path(args[0]).read_text(encoding="utf-8", errors="replace") if args else sys.stdin.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(json.dumps({"error": f"cannot read {args[0] if args else 'stdin'}: {exc}"}))
        return 2
    print(json.dumps(scan(text), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
