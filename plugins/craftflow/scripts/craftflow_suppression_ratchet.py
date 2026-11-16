#!/usr/bin/env python3
"""Suppression-count ratchet for the craftflow plugin's Python files.

Counts the lint/type-check suppression comments in every tracked (and untracked-but-not-ignored) `.py` file, using the
tokenizer so strings, docstrings, regexes and test-fixture text containing "# noqa" are never counted, and compares them
per file with the committed baseline `tests/fixtures/suppression-baseline.json`:

    # noqa: CODE      counted per code            key `noqa:CODE`
    # noqa            (bare)                      key `noqa:bare`
    # ruff: noqa      (file-level)                key `noqa:file`
    # type: ignore    (any form)                  key `type-ignore`
    any of the above lacking a rule code OR a reason (Phase 1's accepted format from craftflow_clean_state_check.py)
                                                  key `unexplained`

A count may only go DOWN. Any per-key count in a file that is higher than its baseline (a file absent from the baseline
has a baseline of 0 for every key) is a violation. The baseline may only shrink: `--update-baseline` lowers entries and
removes zeroed ones but never adds a file/key and never raises a count; raising one is a human editing the JSON.

Usage:
    craftflow_suppression_ratchet.py [--root PATH] [--baseline PATH] [--format text|json]
    craftflow_suppression_ratchet.py --update-baseline    lower the baseline to today's counts (refuses on any rise)
    craftflow_suppression_ratchet.py --init-baseline      write the first baseline (refuses if one already exists)

Exit codes:
    0  every count is at or below its baseline.
    1  tool error: baseline missing/corrupt, git unavailable, unreadable or untokenizable file, bad command line
       (argparse usage errors are mapped to 1 so they cannot be mistaken for 2).
    2  at least one count rose above its baseline (or --update-baseline was asked to raise something).
Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import craftflow_clean_state_check as csc  # Phase 1's comment detection and accepted format

ROOT = Path(__file__).resolve().parents[3]
BASELINE_REL = "plugins/craftflow/tests/fixtures/suppression-baseline.json"
BASELINE_VERSION = 1
EXIT_OK, EXIT_TOOL_ERROR, EXIT_VIOLATION = 0, 1, 2
KEY_UNEXPLAINED = "unexplained"
_FILE_NOQA_RE = re.compile(r"#\s*ruff\s*:\s*noqa\b", re.IGNORECASE)

FIX_TEXT = (
    "FIX: remove the suppression by fixing the code it hides (a suppression is never the fix). If the linter or type "
    "checker is WRONG about this line, do not add or widen a suppression: stop and report the false positive to the "
    "user, who decides whether a baseline may be raised by hand. Do not delete or rewrite comments to dodge the "
    "counter, and do not edit the baseline JSON yourself. If you moved code that already carried suppressions into a "
    "new file (for example splitting an oversized module), that needs a human baseline edit: ask the user."
)
WHY_TEXT = (
    "WHY: suppressions silence the linter without fixing anything; they only ever accumulate unless a ratchet stops "
    "them. This count may only fall."
)


class ToolError(Exception):
    """Anything that is not a verdict about the tree: bad baseline, git or file-read failure."""


class _ToolArgumentParser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse would exit 2, which means "violations" here
        print(json.dumps({"error": f"usage error: {message}"}), file=sys.stderr)
        raise SystemExit(EXIT_TOOL_ERROR)


Counts = Dict[str, Dict[str, int]]


def classify(comment: str) -> List[str]:
    """Baseline keys contributed by one real COMMENT token (empty when it holds no suppression)."""
    keys: List[str] = []
    noqa = csc._NOQA_RE.search(comment)
    if noqa is not None:
        codes = csc._NOQA_CODES_RE.match(noqa.group("rest"))
        if codes is None:
            keys.append("noqa:bare")
        else:
            keys.extend("noqa:" + c.upper() for c in re.split(r"[\s,]+", codes.group("codes")) if c)
    if _FILE_NOQA_RE.search(comment):
        keys.append("noqa:file")
    if csc._TYPE_IGNORE_RE.search(comment):
        keys.append("type-ignore")
    if keys and (csc._suppression_problem(comment) is not None or "noqa:file" in keys):
        keys.append(KEY_UNEXPLAINED)
    return keys


def list_python_files(root: Path) -> List[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*.py"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ToolError(f"git ls-files failed in {root}: {proc.stderr.strip() or 'not a git repository?'}")
    return sorted({p for p in proc.stdout.split("\0") if p and (root / p).is_file()})


def scan(root: Path, files: Sequence[str]):
    """(counts, lines): counts[path][key] -> n, lines[path][key] -> [line numbers]; files without suppressions omitted."""
    counts: Counts = {}
    lines: Dict[str, Dict[str, List[int]]] = {}
    for rel in files:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ToolError(f"cannot read {rel}: {exc}")
        comments = csc._comments_by_line(text)
        if comments is None:
            raise ToolError(f"cannot tokenize {rel}: fix the syntax error first (it cannot be counted)")
        for line_no in sorted(comments):
            for key in classify(comments[line_no]):
                counts.setdefault(rel, {}).setdefault(key, 0)
                counts[rel][key] += 1
                lines.setdefault(rel, {}).setdefault(key, []).append(line_no)
    return counts, lines


def load_baseline(path: Path) -> Counts:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"baseline unreadable or corrupt ({path}): {exc}")
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION or not isinstance(files, dict):
        raise ToolError(f"baseline {path} must be an object with version {BASELINE_VERSION} and a `files` object")
    for rel, keys in files.items():
        ok = isinstance(keys, dict) and all(
            isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v > 0 for k, v in keys.items())
        if not ok:
            raise ToolError(f"baseline {path}: entry for {rel!r} must map keys to positive integers")
    return files


def violations(baseline: Counts, counts: Counts, lines) -> List[dict]:
    found = []
    for rel in sorted(counts):
        base = baseline.get(rel, {})
        for key, n in sorted(counts[rel].items()):
            if n > base.get(key, 0):
                found.append({"file": rel, "key": key, "baseline": base.get(key, 0), "current": n,
                              "new_file": rel not in baseline, "lines": lines[rel][key]})
    return found


def lowered(baseline: Counts, counts: Counts) -> Counts:
    """The baseline lowered to today's counts: only existing entries, never raised, zeroed entries dropped."""
    out: Counts = {}
    for rel, keys in baseline.items():
        kept = {k: min(v, counts.get(rel, {}).get(k, 0)) for k, v in keys.items()}
        kept = {k: v for k, v in kept.items() if v > 0}
        if kept:
            out[rel] = kept
    return out


def write_baseline(path: Path, files: Counts) -> None:
    doc = {
        "version": BASELINE_VERSION,
        "_note": ("Suppression counts per file (craftflow_suppression_ratchet.py). This file may only shrink: lower it "
                  "with --update-baseline; raising a number or adding a file is a human decision made by editing it."),
        "files": {rel: dict(sorted(files[rel].items())) for rel in sorted(files)},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def total(files: Counts) -> int:
    return sum(v for keys in files.values() for k, v in keys.items() if k != KEY_UNEXPLAINED)


def report_text(found: List[dict], counts: Counts, baseline: Counts) -> str:
    out = []
    for v in found:
        where = ", ".join(str(n) for n in v["lines"][:12]) + (" ..." if len(v["lines"]) > 12 else "")
        out.append(f"RISE {v['file']} [{v['key']}] baseline {v['baseline']} -> current {v['current']} (lines {where})"
                   + ("  [file not in baseline]" if v["new_file"] else ""))
    if found:
        out.append("WHAT: the number of suppression comments in the file(s) above went UP (`unexplained` means a "
                   "suppression without a rule code or without a written reason, e.g. `# noqa: E402 - why`).")
        out.append(WHY_TEXT)
        out.append(FIX_TEXT)
    below = sum(1 for rel, keys in baseline.items() for k, v in keys.items() if counts.get(rel, {}).get(k, 0) < v)
    if not found and below:
        out.append(f"note: {below} baseline entr{'y is' if below == 1 else 'ies are'} above today's count; run "
                   "--update-baseline to lock in the improvement.")
    out.append(f"suppression-ratchet: {'FAIL' if found else 'OK'} ({total(counts)} suppression(s) in {len(counts)} "
               f"file(s); baseline {total(baseline)} in {len(baseline)} file(s))")
    return "\n".join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _ToolArgumentParser(description="craftflow suppression-count ratchet")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this checkout)")
    parser.add_argument("--baseline", type=Path, default=None, help=f"baseline JSON (default: <root>/{BASELINE_REL})")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--update-baseline", action="store_true", help="lower the baseline to today's counts; never raises")
    mode.add_argument("--init-baseline", action="store_true", help="write the first baseline; refuses if it exists")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    path = args.baseline or (root / BASELINE_REL)
    try:
        counts, lines = scan(root, list_python_files(root))
        if args.init_baseline:
            if path.exists():
                raise ToolError(f"{path} already exists; --init-baseline only creates the first baseline")
            write_baseline(path, counts)
            print(f"suppression-ratchet: baseline created with {total(counts)} suppression(s) in {len(counts)} file(s)")
            return EXIT_OK
        baseline = load_baseline(path)
    except ToolError as exc:
        print(f"suppression-ratchet: ERROR {exc}", file=sys.stderr)
        return EXIT_TOOL_ERROR

    found = violations(baseline, counts, lines)
    if args.update_baseline and not found:
        new = lowered(baseline, counts)
        if new != baseline:
            write_baseline(path, new)
        print(f"suppression-ratchet: baseline {'lowered' if new != baseline else 'already current'} "
              f"({total(new)} suppression(s) in {len(new)} file(s))")
        return EXIT_OK
    if args.format == "json":
        print(json.dumps({"ok": not found, "violations": found, "suppressions": total(counts),
                          "baseline_suppressions": total(baseline), "files": len(counts)}, indent=2))
    else:
        print(report_text(found, counts, baseline))
        if args.update_baseline:
            print("suppression-ratchet: --update-baseline refused: it never raises a count. " + FIX_TEXT)
    return EXIT_VIOLATION if found else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
