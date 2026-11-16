#!/usr/bin/env python3
"""File-size ratchet for the craftflow plugin's Python files (AGENT_CRITICAL_GUARDRAILS.md: 500-line cap).

Counts physical lines (`wc -l` style) of every tracked, or untracked-but-not-ignored, file matching the globs, and
compares them with the committed baseline `tests/fixtures/file-size-baseline.json`:

    over the limit, NOT in the baseline          -> NEW-OVERSIZED   (exit 2)
    in the baseline, lines > baselined lines     -> GREW            (exit 2)
    in the baseline, lines <= baselined lines    -> pass (it may shrink, never grow)
    in the baseline but at/under the limit now   -> pass, and --update-baseline drops the entry

No category is exempt: tests, generated-looking files and everything else over the limit are grandfathered by baseline
entry (including scripts/craftflow_hook_unit_tests.py, the largest). The baseline may only shrink: `--update-baseline`
lowers entries and removes ones now under the limit but never adds a file and never raises a number; raising one is a
human editing the JSON.

Usage:
    craftflow_file_size_ratchet.py [--root PATH] [--baseline PATH] [--limit 500] [--glob PATHSPEC ...]
                                   [--format text|json]
    craftflow_file_size_ratchet.py --update-baseline    lower/remove baseline entries (refuses on any violation)
    craftflow_file_size_ratchet.py --init-baseline      write the first baseline (refuses if one already exists)
`--glob` is a git pathspec (repeatable; default `*.py`). `--update-baseline` refuses a --limit that differs from the
limit recorded in the baseline, so the limit cannot be raised to shrink the baseline.

Exit codes:
    0  every file is within the limit or within its baselined size.
    1  tool error: baseline missing/corrupt, git unavailable, unreadable file, bad command line (argparse usage errors
       are mapped to 1 so they cannot be mistaken for 2).
    2  at least one NEW-OVERSIZED or GREW finding (or --update-baseline was asked while one exists).
Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[3]
BASELINE_REL = "plugins/craftflow/tests/fixtures/file-size-baseline.json"
BASELINE_VERSION = 1
DEFAULT_LIMIT = 500
DEFAULT_GLOBS = ("*.py",)
EXIT_OK, EXIT_TOOL_ERROR, EXIT_VIOLATION = 0, 1, 2

WHAT_TEXT = ("WHAT: a file is over the line limit and is not allowed to be: either it is new and oversized, or it is "
             "a grandfathered oversized file whose line count grew past its baselined size.")
WHY_TEXT = ("WHY: very large files cannot be read, reviewed or edited reliably by people or agents, and they only get "
            "larger unless something stops them. Grandfathered files may only shrink.")
FIX_TEXT = (
    "FIX: split the module along an existing seam (a group of related functions or classes, a section the file "
    "already separates with a banner comment, test groups that share a fixture) into a new sibling module and import "
    "it, keeping behaviour and every test unchanged. Do NOT compress code to fit: no joining lines with semicolons, "
    "packing statements into one line, collapsing blank lines or docstrings, moving code into data blobs or long "
    "strings, and do NOT delete tests or logic to get under the count. Do NOT edit the baseline JSON or raise the "
    "limit. If the file genuinely cannot be split, stop and ask the user. (Moving suppression comments into a new "
    "file needs a human baseline edit for the suppression ratchet: tell the user.)"
)


class ToolError(Exception):
    """Anything that is not a verdict about the tree: bad baseline, git or file-read failure."""


class _ToolArgumentParser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse would exit 2, which means "violations" here
        print(json.dumps({"error": f"usage error: {message}"}), file=sys.stderr)
        raise SystemExit(EXIT_TOOL_ERROR)


def count_lines(path: Path) -> int:
    """Physical line count like `wc -l`, plus one for a final line without a trailing newline."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ToolError(f"cannot read {path}: {exc}")
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def list_files(root: Path, globs: Sequence[str]) -> List[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *globs],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ToolError(f"git ls-files failed in {root}: {proc.stderr.strip() or 'not a git repository?'}")
    return sorted({p for p in proc.stdout.split("\0") if p and (root / p).is_file()})


def measure(root: Path, globs: Sequence[str]) -> Dict[str, int]:
    return {rel: count_lines(root / rel) for rel in list_files(root, globs)}


def load_baseline(path: Path):
    """(limit, {file: baselined line count})."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"baseline unreadable or corrupt ({path}): {exc}")
    files = data.get("files") if isinstance(data, dict) else None
    limit = data.get("limit") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get("version") != BASELINE_VERSION or not isinstance(files, dict)
            or not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise ToolError(f"baseline {path} must be an object with version {BASELINE_VERSION}, an integer `limit` and "
                        "a `files` object")
    for rel, n in files.items():
        if not isinstance(n, int) or isinstance(n, bool) or n <= limit:
            raise ToolError(f"baseline {path}: {rel!r} must be an integer line count above the limit {limit}")
    return limit, files


def findings(sizes: Dict[str, int], baseline: Dict[str, int], limit: int) -> List[dict]:
    found = []
    for rel in sorted(sizes):
        n = sizes[rel]
        if n <= limit:
            continue
        if rel not in baseline:
            found.append({"kind": "NEW-OVERSIZED", "file": rel, "lines": n, "limit": limit})
        elif n > baseline[rel]:
            found.append({"kind": "GREW", "file": rel, "lines": n, "baseline": baseline[rel], "limit": limit})
    return found


def lowered(sizes: Dict[str, int], baseline: Dict[str, int], limit: int) -> Dict[str, int]:
    """Existing entries only: dropped when gone or at/under the limit, otherwise the smaller of old and current."""
    out = {}
    for rel, old in baseline.items():
        n = sizes.get(rel)
        if n is not None and n > limit:
            out[rel] = min(old, n)
    return out


def write_baseline(path: Path, limit: int, files: Dict[str, int]) -> None:
    doc = {
        "version": BASELINE_VERSION,
        "limit": limit,
        "_note": ("Files over the limit that are grandfathered, with their line counts (craftflow_file_size_ratchet.py). "
                  "This file may only shrink: lower it with --update-baseline; adding a file or raising a number is a "
                  "human decision made by editing it."),
        "files": {rel: files[rel] for rel in sorted(files)},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def report_text(found: List[dict], sizes: Dict[str, int], baseline: Dict[str, int], limit: int) -> str:
    out = []
    for f in found:
        if f["kind"] == "NEW-OVERSIZED":
            out.append(f"NEW-OVERSIZED {f['file']}: {f['lines']} lines, limit {limit}, not in the baseline")
        else:
            out.append(f"GREW {f['file']}: {f['lines']} lines, baselined at {f['baseline']} (limit {limit})")
    if found:
        out.extend([WHAT_TEXT, WHY_TEXT, FIX_TEXT])
    stale = sum(1 for rel, old in baseline.items() if sizes.get(rel) is None or sizes[rel] < old)
    if not found and stale:
        out.append(f"note: {stale} baseline entr{'y is' if stale == 1 else 'ies are'} larger than the file now is (or "
                   "no longer needed); run --update-baseline to lock in the improvement.")
    over = sum(1 for n in sizes.values() if n > limit)
    out.append(f"file-size-ratchet: {'FAIL' if found else 'OK'} ({over} file(s) over {limit} lines, "
               f"{len(baseline)} baselined, {len(found)} violation(s))")
    return "\n".join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _ToolArgumentParser(description="craftflow file-size ratchet")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this checkout)")
    parser.add_argument("--baseline", type=Path, default=None, help=f"baseline JSON (default: <root>/{BASELINE_REL})")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"maximum lines (default {DEFAULT_LIMIT})")
    parser.add_argument("--glob", action="append", dest="globs", help="git pathspec to check (repeatable; default *.py)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--update-baseline", action="store_true", help="lower/remove entries; never adds or raises")
    mode.add_argument("--init-baseline", action="store_true", help="write the first baseline; refuses if it exists")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    path = args.baseline or (root / BASELINE_REL)
    limit = args.limit
    try:
        if limit < 1:
            raise ToolError("--limit must be at least 1")
        sizes = measure(root, args.globs or DEFAULT_GLOBS)
        if args.init_baseline:
            if path.exists():
                raise ToolError(f"{path} already exists; --init-baseline only creates the first baseline")
            entries = {rel: n for rel, n in sizes.items() if n > limit}
            write_baseline(path, limit, entries)
            print(f"file-size-ratchet: baseline created with {len(entries)} file(s) over {limit} lines")
            return EXIT_OK
        base_limit, baseline = load_baseline(path)
        if args.update_baseline and limit != base_limit:
            raise ToolError(f"--update-baseline needs --limit {base_limit} (the baseline's limit), got {limit}")
    except ToolError as exc:
        print(f"file-size-ratchet: ERROR {exc}", file=sys.stderr)
        return EXIT_TOOL_ERROR

    found = findings(sizes, baseline, limit)
    if args.update_baseline and not found:
        new = lowered(sizes, baseline, limit)
        if new != baseline:
            write_baseline(path, limit, new)
        print(f"file-size-ratchet: baseline {'lowered' if new != baseline else 'already current'} "
              f"({len(new)} file(s) over {limit} lines)")
        return EXIT_OK
    if args.format == "json":
        print(json.dumps({"ok": not found, "limit": limit, "findings": found, "baselined": len(baseline),
                          "over_limit": sum(1 for n in sizes.values() if n > limit)}, indent=2))
    else:
        print(report_text(found, sizes, baseline, limit))
        if args.update_baseline:
            print("file-size-ratchet: --update-baseline refused: it never adds or raises an entry. " + FIX_TEXT)
    return EXIT_VIOLATION if found else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
