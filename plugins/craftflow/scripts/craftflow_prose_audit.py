#!/usr/bin/env python3
"""Advisory prose audit: passages said twice, and sentences that only restate a default (P7).

Scans the markdown under `agents/`, `skills/` (including `skills/_shared/`) of the plugin root. Two findings:

    duplicates  a paragraph or list item of at least MIN_CHARS characters whose normalised text appears in two or more
                files: the same meaning in two places drifts, so one should own it and the other point to it
    defaults    a sentence built on a phrase that tells a capable agent what it would do anyway ("make sure to",
                "best practices", "be careful"): cut it, or replace it with the specific check it stands for

Advisory only: it always exits 0 (2 on bad arguments). File the findings as cleanup tasks; never let this fail a run.

Usage:
    craftflow_prose_audit.py [--root PLUGIN_ROOT] [--format json|md]

Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DIRS = ("agents", "skills")
MIN_CHARS = 80
MAX_DEFAULTS = 200
DEFAULT_PHRASES = (
    r"make sure to", r"be sure to", r"it is important to", r"it's important to", r"best practices?",
    r"be careful", r"be thorough", r"as needed", r"as appropriate", r"when appropriate", r"use your judg(?:e)?ment",
    r"always ensure", r"ensure that you", r"in a clear and concise", r"high[- ]quality code", r"clean code",
)
DEFAULT_RE = re.compile(r"\b(?:" + "|".join(DEFAULT_PHRASES) + r")\b", re.I)
FENCE = re.compile(r"^```.*?^```", re.M | re.S)


def _units(text: str) -> List[str]:
    """Paragraphs and list items, with markdown markers and whitespace normalised; code fences dropped."""
    text = FENCE.sub("", text)
    units = []
    for block in re.split(r"\n\s*\n", text):
        for item in re.split(r"\n(?=\s*(?:[-*]|\d+\.)\s)", block):
            norm = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", item.strip())
            norm = re.sub(r"\s+", " ", norm).strip().lower()
            if norm and not norm.startswith(("#", "|")):
                units.append(norm)
    return units


def _files(root: Path) -> List[Path]:
    return sorted(p for d in DIRS for p in (root / d).rglob("*.md") if p.is_file())


def audit(root: Path) -> Dict[str, object]:
    seen: Dict[str, List[str]] = {}
    defaults = []
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for unit in set(_units(text)):
            if len(unit) >= MIN_CHARS:
                seen.setdefault(unit, []).append(rel)
        for lineno, line in enumerate(text.splitlines(), 1):
            if DEFAULT_RE.search(line) and len(defaults) < MAX_DEFAULTS:
                defaults.append({"where": f"{rel}:{lineno}", "text": line.strip()[:200]})
    duplicates = [{"text": unit[:200], "files": sorted(set(files))} for unit, files in sorted(seen.items())
                  if len(set(files)) > 1]
    duplicates.sort(key=lambda d: (-len(d["files"]), d["text"]))
    return {"files_scanned": len(_files(root)), "duplicates": duplicates, "defaults": defaults}


def to_markdown(report: Dict[str, object]) -> str:
    lines = ["# Prose audit", "", f"Files scanned: {report['files_scanned']}. Advisory: file findings as cleanup tasks.",
             "", f"## Duplicated passages ({len(report['duplicates'])})", ""]
    for d in report["duplicates"]:
        lines.append(f"- {', '.join(d['files'])}: \"{d['text']}\"")
    lines += ["", f"## Sentences that restate a default ({len(report['defaults'])})", ""]
    for d in report["defaults"]:
        lines.append(f"- `{d['where']}`: {d['text']}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Advisory audit of duplicated and default-restating prose.")
    parser.add_argument("--root", default=str(PLUGIN_ROOT))
    parser.add_argument("--format", choices=["json", "md"], default="json")
    args = parser.parse_args(argv)
    report = audit(Path(args.root))
    print(to_markdown(report) if args.format == "md" else json.dumps(report, sort_keys=True), end="" if args.format == "md" else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
