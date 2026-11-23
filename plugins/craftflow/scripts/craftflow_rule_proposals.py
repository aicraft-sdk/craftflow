#!/usr/bin/env python3
"""Mistake-to-rule proposals for the failure digest. Pure and read-only; standard library only.

After "60-day mistake pattern linting" (turn repeated agent mistakes into lint rules, tests and types) and Claude
Doctor (structural session signals such as edit thrashing and error loops). The mechanism table is the one in
skills/retro/references/mistake-to-rule.md; this module only picks the likely row for a failure signature. A person
still checks the guide's four conditions, and hooks and lint config stay human decisions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, List

GUIDE = "skills/retro/references/mistake-to-rule.md"
EDIT_THRASH_MIN = 5
ERROR_LOOP_MIN = 3
# (pattern, mechanism, who acts), first match wins: lint before arch-lint, so "unused import" is a lint finding.
_RULES = (
    (r"\blint|\bruff\b|\bformat|\bsyntax|\bunused\b", "lint rule or clean-state check",
     "human (agents never edit ruff.toml)"),
    (r"\bimport|\blayer\b|\bboundar", "arch-lint rule",
     "agent drafts the rule for .craftflow/state/project/arch-rules.json; a human adds it"),
    (r"\bskip|\bwithout\b|\bmissing\b|\bnot run\b|\bforgot|\bbefore\b", "hook",
     "human reviews a hooks/hooks.json change; hooks gate agent behavior"),
    (r"\btest|\bassert|\bregress|\bcrash|\bexception|\berror\b|\bbug\b", "regression test",
     "agent, in the normal TDD flow: write it to fail first"),
)


def _mechanism(signature: str):
    for pattern, mechanism, who in _RULES:
        if re.search(pattern, signature, re.IGNORECASE):
            return mechanism, who
    return "reviewer prose", "agent proposes text for skills/code-review-patterns/SKILL.md; a human merges"


def propose(clusters: Iterable[dict]) -> List[dict]:
    """Each cluster plus a `proposal`. A signature seen once gets none yet (the guide's second-occurrence rule)."""
    out = []
    for cluster in clusters:
        n = int(cluster.get("occurrences") or 0)
        sig = str(cluster.get("signature") or "")
        if n < 2:
            proposal = {"mechanism": None, "who": "nobody yet: record it and wait for a second occurrence",
                        "draft": ""}
        else:
            mechanism, who = _mechanism(sig)
            example = (cluster.get("example_reasons") or [sig])[0]
            proposal = {"mechanism": mechanism, "who": who,
                        "draft": f"Recurring {n}x: {example!r}. Proposed mechanism: {mechanism}. Who acts: {who}. "
                                 f"Check the four conditions in {GUIDE} before writing it."}
        out.append({**cluster, "proposal": proposal})
    return out


def _blocks(row: dict) -> list:
    content = (row.get("message") or {}).get("content") if isinstance(row, dict) else None
    return content if isinstance(content, list) else []


def session_signals(transcript_dirs: Iterable[Path]) -> dict:
    """Edit thrashing (one file edited EDIT_THRASH_MIN+ times in a session) and error loops (ERROR_LOOP_MIN+ tool
    errors in a row), per transcript file."""
    sessions, thrash, loops = 0, [], []
    for base in transcript_dirs:
        for path in sorted(base.rglob("*.jsonl")) if base.is_dir() else []:
            sessions += 1
            edits: dict = {}
            run = longest = 0
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                for block in _blocks(row):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use" and block.get("name") in ("Edit", "Write", "MultiEdit"):
                        target = str((block.get("input") or {}).get("file_path") or "")
                        edits[target] = edits.get(target, 0) + 1
                    elif block.get("type") == "tool_result":
                        run = run + 1 if block.get("is_error") is True else 0
                        longest = max(longest, run)
            for target, count in sorted(edits.items()):
                if target and count >= EDIT_THRASH_MIN:
                    thrash.append({"session": path.name, "file": target, "edits": count})
            if longest >= ERROR_LOOP_MIN:
                loops.append({"session": path.name, "longest_run": longest})
    return {"sessions": sessions, "edit_thrashing": thrash, "error_loops": loops}
