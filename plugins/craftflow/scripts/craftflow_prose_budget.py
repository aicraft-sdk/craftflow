#!/usr/bin/env python3
"""Prose budget ratchet: always-loaded prose may only shrink (P7; after Lauren Tan's talk and writing-for-agents).

Measures the bytes of every agent prompt (`agents/*.md`) and every skill's always-loaded part (`skills/*/SKILL.md`,
frontmatter description included) under the plugin root, and compares them with the committed baseline
`tests/fixtures/prose-budget-baseline.json`:

    in the baseline, bytes > ceiling     -> GREW  (exit 2)
    not in the baseline                  -> NEW   (exit 2): a new always-loaded file is a human decision
    in the baseline, bytes <= ceiling    -> pass; a lower size is reported under `lowerable`

The router's own read set has its separate shrink-only budget (router-budget.json); references/ files are loaded on
demand and are not counted here. `--update-baseline` lowers ceilings and drops files that no longer exist, and refuses
while any file grew or is new. `--init-baseline` writes the first baseline and refuses to overwrite one. Raising a
ceiling is a person editing the JSON, and craftflow_baseline_integrity.py reports it.

Usage:
    craftflow_prose_budget.py [--root PLUGIN_ROOT] [--baseline PATH] [--update-baseline | --init-baseline]

Prints one JSON line. Exit codes: 0 within budget, 1 tool error (missing or corrupt baseline, refused init),
2 GREW or NEW. Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BASELINE_REL = "tests/fixtures/prose-budget-baseline.json"
GLOBS = ("agents/*.md", "skills/*/SKILL.md")
NOTE = ("Byte ceilings for always-loaded prose (craftflow_prose_budget.py). Shrink-only: lower it with "
        "--update-baseline; adding a file or raising a number is a human decision made by editing it.")


def measure(root: Path) -> Dict[str, int]:
    sizes = {}
    for pattern in GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                sizes[path.relative_to(root).as_posix()] = len(path.read_bytes())
    return dict(sorted(sizes.items()))


def compare(sizes: Dict[str, int], ceilings: Dict[str, int]) -> Dict[str, object]:
    findings: List[str] = []
    lowerable: Dict[str, int] = {}
    for rel, n in sizes.items():
        if rel not in ceilings:
            findings.append(f"NEW {rel}: {n} bytes, not in the baseline")
        elif n > ceilings[rel]:
            findings.append(f"GREW {rel}: {ceilings[rel]} -> {n} bytes (+{n - ceilings[rel]}); cut prose or turn the rule into a check")
        elif n < ceilings[rel]:
            lowerable[rel] = n
    missing = sorted(set(ceilings) - set(sizes))
    return {"exit": 2 if findings else 0, "findings": findings, "lowerable": lowerable, "missing": missing,
            "total_bytes": sum(sizes.values()), "files": len(sizes)}


def _write(path: Path, files: Dict[str, int]) -> None:
    path.write_text(json.dumps({"version": 1, "_note": NOTE, "files": dict(sorted(files.items()))}, indent=2) + "\n",
                    encoding="utf-8")


def _load(path: Path) -> Optional[Dict[str, int]]:
    try:
        files = json.loads(path.read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(files, dict) or not all(isinstance(v, int) and not isinstance(v, bool) for v in files.values()):
        return None
    return files


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Shrink-only byte ceilings for always-loaded prose.")
    parser.add_argument("--root", default=str(PLUGIN_ROOT))
    parser.add_argument("--baseline")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--update-baseline", action="store_true")
    mode.add_argument("--init-baseline", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root)
    path = Path(args.baseline) if args.baseline else root / BASELINE_REL
    sizes = measure(root)
    if args.init_baseline:
        if path.exists():
            print(json.dumps({"error": f"{path} already exists; use --update-baseline"}))
            return 1
        _write(path, sizes)
        print(json.dumps({"initialized": str(path), "files": len(sizes)}))
        return 0
    ceilings = _load(path)
    if ceilings is None:
        print(json.dumps({"error": f"baseline missing or corrupt: {path}"}))
        return 1
    report = compare(sizes, ceilings)
    if args.update_baseline and report["exit"] == 0:
        _write(path, {rel: n for rel, n in sizes.items() if rel in ceilings})
        report["updated"] = True
    print(json.dumps(report, sort_keys=True))
    return int(report["exit"])


if __name__ == "__main__":
    sys.exit(main())
