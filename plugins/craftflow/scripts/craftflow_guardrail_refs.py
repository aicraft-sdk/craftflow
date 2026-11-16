#!/usr/bin/env python3
"""
craftflow_guardrail_refs.py

Checks that every backticked path-like reference in a Markdown doc (default: the repo-root
AGENT_CRITICAL_GUARDRAILS.md) resolves in THIS repository. This repo is a published copy of one subtree of a larger
monorepo, so a doc can cite files (AI_FIRST.md, docs/ai/decisions/, tools/craftflow-plugin/**) that exist only there
and send an agent looking for something it can never find.

Usage:
    craftflow_guardrail_refs.py [--file PATH] [--root PATH] [--format text|json]

`--file` defaults to AGENT_CRITICAL_GUARDRAILS.md under `--root` (default: the repository root this script lives in), so
the checker can be pointed at other docs later.

What counts as a path-like reference (deliberately conservative). Only text inside single backticks on a line outside a
fenced code block is considered, and a token is path-like when, after trimming, it
  - contains `/`, or ends in a known file extension (EXTENSIONS below); and
  - does not start with `#` or `-`, and contains no `://`, `@`, `=` or `$`.
A backticked command line is checked via its path arguments: when the first word is a known command (COMMANDS below),
every later word that is path-like per the rule above is checked; any other multi-word token is ignored (prose,
`# noqa`, `# type: ignore`). Tokens with neither `/` nor a known extension (`main`, `MEMORY_NOTES`, `craftflow-router`)
are ignored.

Resolution: glob parts are cut at the first path segment containing `*`, `?` or `[` and the directory before it is
checked (`tools/x/**` checks `tools/x`; `.craftflow/state/*.md` checks `.craftflow/state`). A reference with a `/`
resolves if the path exists relative to the repository root. A bare name (no `/`) resolves if it exists at the root,
otherwise if exactly one tracked-or-untracked-but-not-ignored file in the repo has that basename; zero or several
matches fail.

ALLOWED_RUNTIME_PREFIXES is the only escape hatch: paths that exist in a consumer project at run time but never in a
clean checkout of this repository.

Exit codes: 0 every reference resolves; 1 tool error (missing/unreadable file, bad arguments; argparse usage errors are
mapped to 1 so they cannot be mistaken for 2); 2 at least one reference does not resolve. Python 3.9, stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

EXIT_OK, EXIT_TOOL_ERROR, EXIT_UNRESOLVED = 0, 1, 2
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TARGET = "AGENT_CRITICAL_GUARDRAILS.md"

EXTENSIONS = (".py", ".md", ".json", ".toml", ".yml", ".yaml", ".sh", ".txt", ".js", ".ts", ".tsx", ".cfg", ".ini")
COMMANDS = ("python3", "python", "sh", "bash", "node", "git", "pip", "ruff")

# Paths that exist at run time in a consumer project but never in a clean checkout of this repository. A prefix is
# matched on whole path segments. Keep this list tiny: a path that merely lives in the source monorepo is NOT a runtime
# path; reword the doc instead of adding it here.
ALLOWED_RUNTIME_PREFIXES = (
    ".craftflow/",  # consumer-project state dir (.craftflow/state/*.md memory files), created by the plugin at run time
)

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".ruff_cache"}
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_GLOB_CHARS = set("*?[")


class _ToolArgumentParser(argparse.ArgumentParser):
    """argparse exits 2 on usage errors, which would collide with 'unresolved reference'; report them as exit 1."""

    def error(self, message: str):
        print(json.dumps({"error": f"usage error: {message}"}), file=sys.stderr)
        raise SystemExit(EXIT_TOOL_ERROR)


def _looks_like_path(token: str) -> bool:
    if not token or token[0] in "#-" or any(s in token for s in ("://", "@", "=", "$")):
        return False
    return "/" in token or token.endswith(EXTENSIONS)


def extract_refs(text: str) -> List[Dict[str, object]]:
    """[{line, token, path}] for every path-like reference outside fenced code blocks; `token` is the backticked text."""
    refs: List[Dict[str, object]] = []
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in _CODE_SPAN.finditer(line):
            token = match.group(1).strip()
            words = token.split()
            if len(words) > 1:
                if words[0] not in COMMANDS:
                    continue
                candidates = [w for w in words[1:] if _looks_like_path(w)]
            else:
                candidates = [token] if _looks_like_path(token) else []
            refs.extend({"line": line_no, "token": token, "path": c} for c in candidates)
    return refs


def strip_glob(path: str) -> str:
    """The directory part before the first glob segment (`a/b/**` -> `a/b`, `.x/*.md` -> `.x`); unchanged without globs."""
    kept: List[str] = []
    for part in path.split("/"):
        if _GLOB_CHARS & set(part):
            break
        kept.append(part)
    return "/".join(kept) if len(kept) != len(path.split("/")) else path


def list_repo_files(root: Path) -> List[str]:
    """Relative paths of tracked plus untracked-not-ignored files (so a file you just created resolves before `git add`)."""
    try:
        proc = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
                              capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.splitlines()
    except OSError:
        pass
    found: List[str] = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        found.extend(os.path.relpath(os.path.join(base, f), root) for f in files)
    return found


def resolve(path: str, root: Path, files: List[str]) -> Optional[str]:
    """None when `path` resolves, otherwise the reason it does not."""
    target = strip_glob(path).rstrip("/")
    if any((target + "/").startswith(p) or target == p.rstrip("/") for p in ALLOWED_RUNTIME_PREFIXES):
        return None
    if "/" in target:
        return None if (root / target).exists() else "does not exist in this repository"
    if not target:
        return "has no path before its glob"
    if (root / target).exists():
        return None
    matches = sorted(f for f in files if f.rsplit("/", 1)[-1] == target)
    if len(matches) == 1:
        return None
    if not matches:
        return "matches no file in this repository"
    return f"is ambiguous: {len(matches)} files share that name ({', '.join(matches[:4])}{', ...' if len(matches) > 4 else ''})"


def check(target: Path, root: Path) -> List[Dict[str, object]]:
    text = target.read_text(encoding="utf-8")
    files = list_repo_files(root)
    problems = []
    for ref in extract_refs(text):
        reason = resolve(str(ref["path"]), root, files)
        if reason:
            problems.append(dict(ref, reason=reason))
    return problems


def _display(target: Path, root: Path) -> str:
    try:
        return str(target.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(target)


def main(argv: Optional[List[str]] = None) -> int:
    parser = _ToolArgumentParser(description="Check that backticked paths in a doc resolve in this repository.")
    parser.add_argument("--file", default=None, help=f"Markdown file to check (default: {DEFAULT_TARGET} under --root)")
    parser.add_argument("--root", default=str(ROOT), help="Repository root that references resolve against")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    target = Path(args.file) if args.file else root / DEFAULT_TARGET
    try:
        problems = check(target, root)
    except (OSError, UnicodeDecodeError) as exc:
        print(json.dumps({"error": f"cannot read {target}: {exc}"}), file=sys.stderr)
        return EXIT_TOOL_ERROR
    name = _display(target, root)
    if args.format == "json":
        print(json.dumps({"file": name, "unresolved": problems}, indent=2))
    else:
        for p in problems:
            print(
                f"WHAT: {name}:{p['line']} cites `{p['path']}`, which {p['reason']}.\n"
                "WHY: this repository is a published copy of one subtree of a larger monorepo; a reference that does not\n"
                "     resolve here sends an agent looking for a file it can never find.\n"
                f"FIX: edit line {p['line']} of {name}: point at a path that exists here, or drop the backticks and say in\n"
                "     prose that it lives in the source monorepo. Never create a placeholder file to satisfy this check.\n"
                "     (A consumer-project runtime path is allowlisted in ALLOWED_RUNTIME_PREFIXES; that is a human decision.)\n"
            )
        print(f"Guardrail refs: {name}: {len(problems)} unresolved reference(s)." if problems
              else f"Guardrail refs: {name}: all references resolve.")
    return EXIT_UNRESOLVED if problems else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
