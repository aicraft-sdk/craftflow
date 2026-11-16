#!/usr/bin/env python3
"""Baseline-integrity check: a ratchet baseline or lint/hook configuration may only get stricter, never looser.

The suppression ratchet, the file-size ratchet and ruff each pass trivially if the thing they compare against is edited
upward in the same change. This check closes that gap by comparing the working tree with the version at a base ref
(by default `git merge-base HEAD <default branch>`; override with --base-ref):

    tests/fixtures/suppression-baseline.json  any file/key added, or any count raised                -> violation
    tests/fixtures/file-size-baseline.json    any file added, any line count raised, or `limit` raised -> violation
    ruff.toml                                 `per-file-ignores`, `external`, `ignore`, `exclude` (and their `extend-`
                                              forms) gained an entry, or `select` lost one            -> violation
    config/hook-mode.json                     a key removed, or a value weakened (block -> audit/off,
                                              audit -> off)                                           -> violation

Lowering or removing entries always passes. A file that does not exist at the base ref is a first introduction: it is
reported as "new, not comparable" and passes with an explicit notice. docs-rot-baseline.json is NOT checked here:
craftflow_docs_rot.py already refuses a baseline that grew past its own merge-base.

Base ref cannot be resolved (typically a shallow clone): exit 1 with the fetch command; with --allow-missing-base the
check is SKIPPED (loudly, exit 0). Do not pass --allow-missing-base in CI.

Human override: `--approved-increase <repo-relative file>` (repeatable) downgrades that file's violations to a printed
"APPROVED" line. It exists for a person running this by hand after deciding the increase is legitimate; agents must
never pass it. CI does not pass it, so the CI check stays red until a repository admin deliberately merges.

ruff.toml is read with a small purpose-built parser (Python 3.9 has no tomllib): `[section]` headers, `key = value`
lines with values continued across lines until brackets balance, `#` comments outside strings, quoted/bare keys, and
quoted strings inside arrays. Triple-quoted strings and anything it cannot read are a tool error (exit 1), never a
silent pass. Other ruff.toml keys are not compared.

Usage:
    craftflow_baseline_integrity.py [--root PATH] [--base-ref REF] [--allow-missing-base]
        [--approved-increase FILE ...] [--format text|json]

Exit codes: 0 ok (or skipped/new), 1 tool error (bad arguments, base ref unresolvable, unreadable file), 2 violation.
Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[3]
SUPPRESSION_REL = "plugins/craftflow/tests/fixtures/suppression-baseline.json"
FILE_SIZE_REL = "plugins/craftflow/tests/fixtures/file-size-baseline.json"
RUFF_REL = "ruff.toml"
HOOK_MODE_REL = "plugins/craftflow/config/hook-mode.json"
GUARDED = (SUPPRESSION_REL, FILE_SIZE_REL, RUFF_REL, HOOK_MODE_REL)
DEFAULT_BASE_CANDIDATES = ("origin/HEAD", "origin/main", "origin/master", "main", "master")
EXIT_OK, EXIT_TOOL_ERROR, EXIT_VIOLATION = 0, 1, 2

# Config lists a change may never grow, and the one it may never shrink.
RUFF_NO_GROW = ("ignore", "extend-ignore", "external", "exclude", "extend-exclude")
RUFF_NO_SHRINK = ("select",)
HOOK_RANK = {"block": 2, "audit": 1}

FIX_TEXT = (
    "Baselines only shrink: they record debt that may be paid down, never grown. FIX: change the code so the "
    "underlying check passes (remove the suppression, shrink the file, restore the ruff/hook-mode setting) instead of "
    "editing the baseline or config. If you believe the increase is legitimate (for example splitting an oversized "
    "file moves its suppressions into new files), stop and ask the user: a human must approve it, either by running "
    "this check by hand with --approved-increase <file> or by an administrator merging the change knowingly. "
    "Agents must not pass --approved-increase."
)


class ToolError(Exception):
    """Not a verdict about the change: bad arguments, git failure, unreadable or unparseable input."""


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ToolError(f"git binary not found: {exc}") from exc


def _is_shallow(root: Path) -> bool:
    return git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"


def resolve_merge_base(root: Path, base_ref: Optional[str]) -> Tuple[str, str]:
    """Returns (ref used, merge-base sha). Raises ToolError with the fetch hint when it cannot be resolved."""
    candidates = [base_ref] if base_ref else list(DEFAULT_BASE_CANDIDATES)
    ref = next((c for c in candidates if git(root, "rev-parse", "--verify", "--quiet", f"{c}^{{commit}}").returncode == 0), None)
    shallow = " This is a shallow clone." if _is_shallow(root) else ""
    fetch = (
        "Fetch it: `git fetch --unshallow` (or `git fetch origin <default-branch>` plus enough depth), in CI use "
        "actions/checkout with fetch-depth: 0, or pass --base-ref <ref>."
    )
    if ref is None:
        tried = base_ref or ", ".join(DEFAULT_BASE_CANDIDATES)
        raise ToolError(f"cannot resolve a base ref (tried: {tried}).{shallow} {fetch}")
    mb = git(root, "merge-base", "HEAD", ref)
    if mb.returncode != 0 or not mb.stdout.strip():
        raise ToolError(f"cannot find a merge-base of HEAD and {ref}.{shallow} {fetch}")
    return ref, mb.stdout.strip()


def read_at(root: Path, sha: str, rel: str) -> Optional[str]:
    """File text at a commit, or None when it does not exist there."""
    if git(root, "cat-file", "-e", f"{sha}:{rel}").returncode != 0:
        return None
    shown = git(root, "show", f"{sha}:{rel}")
    if shown.returncode != 0:
        raise ToolError(f"cannot read {rel} at {sha[:12]}: {shown.stderr.strip()}")
    return shown.stdout


def read_now(root: Path, rel: str) -> Optional[str]:
    path = root / rel
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolError(f"cannot read {rel}: {exc}") from exc


def load_json(text: str, label: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ToolError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ToolError(f"{label} is not a JSON object")
    return data


def _flatten_counts(data: dict, label: str) -> Dict[Tuple[str, ...], int]:
    """{("files", path, key): n, ...} for suppression, {("files", path): n, ("limit",): n} for file-size."""
    flat: Dict[Tuple[str, ...], int] = {}
    if "limit" in data:
        flat[("limit",)] = _as_int(data["limit"], label)
    files = data.get("files", {})
    if not isinstance(files, dict):
        raise ToolError(f"{label}: `files` is not an object")
    for path, val in files.items():
        if isinstance(val, dict):
            for key, n in val.items():
                flat[("files", path, key)] = _as_int(n, label)
        else:
            flat[("files", path)] = _as_int(val, label)
    return flat


def _as_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"{label}: expected an integer count, found {value!r}")
    return value


def compare_counts(base_text: str, cur_text: str, label: str) -> List[str]:
    base = _flatten_counts(load_json(base_text, f"{label} at base"), f"{label} at base")
    cur = _flatten_counts(load_json(cur_text, label), label)
    out = []
    for key, n in sorted(cur.items()):
        name = " / ".join(key)
        if key not in base:
            if n > 0:
                out.append(f"entry added: {name} = {n}")
        elif n > base[key]:
            out.append(f"raised: {name} {base[key]} -> {n}")
    return out


# --- ruff.toml: minimal parser ------------------------------------------------------------------------------------

def _strip_comment(line: str) -> str:
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def _depth(text: str) -> int:
    depth, quote = 0, ""
    for ch in text:
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth


_HEADER_RE = re.compile(r"^\[\[?\s*([^\[\]]+?)\s*\]\]?$")
_KEY_RE = re.compile(r"""^("([^"]*)"|'([^']*)'|[A-Za-z0-9_.\-]+)\s*=\s*(.*)$""")
_STR_RE = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'([^']*)'")
_PAIR_RE = re.compile(r"(?:\"([^\"]*)\"|'([^']*)'|([A-Za-z0-9_.\-]+))\s*=\s*\[([^\]]*)\]")


def parse_toml_subset(text: str) -> Dict[str, Dict[str, str]]:
    """{section: {key: raw value text}}; the top level is section ''. Raises ToolError on constructs it cannot read."""
    sections: Dict[str, Dict[str, str]] = {"": {}}
    section, key, buf = "", None, ""
    for raw in text.splitlines():
        if '"""' in raw or "'''" in raw:
            raise ToolError("ruff.toml uses a triple-quoted string, which this parser does not support")
        line = _strip_comment(raw).strip()
        if key is not None:
            buf += " " + line
            if _depth(buf) <= 0:
                sections[section][key], key = buf.strip(), None
            continue
        if not line:
            continue
        header = _HEADER_RE.match(line)
        if header and "=" not in line:
            section = header.group(1).replace('"', "").replace("'", "")
            sections.setdefault(section, {})
            continue
        kv = _KEY_RE.match(line)
        if not kv:
            raise ToolError(f"ruff.toml: cannot parse line: {raw.strip()!r}")
        name = kv.group(2) if kv.group(2) is not None else kv.group(3) if kv.group(3) is not None else kv.group(1)
        value = kv.group(4)
        if _depth(value) > 0:
            key, buf = name, value
            sections[section][name] = value
        else:
            sections[section][name] = value.strip()
    if key is not None:
        raise ToolError("ruff.toml: unterminated array or table")
    return sections


def _strings(raw: str) -> List[str]:
    return [a if a else b for a, b in _STR_RE.findall(raw)]


def ruff_facts(text: str) -> Tuple[Set[Tuple[str, ...]], Set[Tuple[str, ...]]]:
    """(entries a change may not add, entries a change may not remove)."""
    sections = parse_toml_subset(text)
    grow: Set[Tuple[str, ...]] = set()
    shrink: Set[Tuple[str, ...]] = set()
    for sec in ("", "lint"):
        for name in RUFF_NO_GROW:
            grow.update((name, item) for item in _strings(sections.get(sec, {}).get(name, "")))
        for name in RUFF_NO_SHRINK:
            shrink.update((name, item) for item in _strings(sections.get(sec, {}).get(name, "")))
        for name in ("per-file-ignores", "extend-per-file-ignores"):
            for m in _PAIR_RE.finditer(sections.get(sec, {}).get(name, "")):
                path = m.group(1) or m.group(2) or m.group(3)
                grow.update(("per-file-ignores", path, code) for code in _strings(m.group(4)))
    for sec, keys in sections.items():
        if sec.split(".")[-1] in ("per-file-ignores", "extend-per-file-ignores"):
            for path, raw in keys.items():
                grow.update(("per-file-ignores", path, code) for code in _strings(raw))
    return grow, shrink


def compare_ruff(base_text: str, cur_text: str) -> List[str]:
    base_grow, base_shrink = ruff_facts(base_text)
    cur_grow, cur_shrink = ruff_facts(cur_text)
    out = [f"gained {' / '.join(f)}" for f in sorted(cur_grow - base_grow)]
    out += [f"lost {' / '.join(f)}" for f in sorted(base_shrink - cur_shrink)]
    return out


def compare_hook_mode(base_text: str, cur_text: str) -> List[str]:
    base = load_json(base_text, "hook-mode.json at base")
    cur = load_json(cur_text, "hook-mode.json")
    out = []
    for key, was in sorted(base.items()):
        if key not in cur:
            out.append(f"key removed: {key} (was {was!r})")
        elif HOOK_RANK.get(str(cur[key]), 0) < HOOK_RANK.get(str(was), 0):
            out.append(f"weakened: {key} {was!r} -> {cur[key]!r}")
    return out


COMPARATORS = {
    SUPPRESSION_REL: lambda b, c: compare_counts(b, c, "suppression-baseline.json"),
    FILE_SIZE_REL: lambda b, c: compare_counts(b, c, "file-size-baseline.json"),
    RUFF_REL: compare_ruff,
    HOOK_MODE_REL: compare_hook_mode,
}


def check(root: Path, sha: str, approved: Sequence[str]) -> List[dict]:
    """One result per guarded file: status ok | new | violation | approved, plus its findings."""
    results = []
    for rel in GUARDED:
        base_text, cur_text = read_at(root, sha, rel), read_now(root, rel)
        if base_text is None:
            results.append({"file": rel, "status": "new", "findings": ["new baseline, not comparable (absent at the base ref)"]})
        elif cur_text is None:
            findings = ["file deleted (it exists at the base ref)"]
            results.append({"file": rel, "status": "approved" if rel in approved else "violation", "findings": findings})
        else:
            findings = COMPARATORS[rel](base_text, cur_text)
            status = "ok" if not findings else "approved" if rel in approved else "violation"
            results.append({"file": rel, "status": status, "findings": findings})
    return results


def render(report: dict) -> str:
    lines = []
    if report["status"] == "skipped":
        return f"baseline-integrity: SKIPPED (no usable base ref; the baselines were NOT compared): {report['reason']}"
    lines.append(f"baseline-integrity: comparing the working tree with merge-base {report['base_sha'][:12]} of HEAD and {report['base_ref']}")
    for res in report["results"]:
        tag = {"ok": "OK", "new": "NOTICE", "violation": "VIOLATION", "approved": "APPROVED (human override)"}[res["status"]]
        lines.append(f"  {tag}: {res['file']}")
        for finding in res["findings"]:
            lines.append(f"      {finding}")
    if report["status"] == "violation":
        lines.append("baseline-integrity: FAIL")
        lines.append(FIX_TEXT)
    else:
        lines.append("baseline-integrity: OK")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> Tuple[int, dict]:
    root = Path(args.root).resolve()
    approved = list(args.approved_increase or [])
    for rel in approved:
        if rel not in GUARDED:
            raise ToolError(f"--approved-increase {rel}: not a guarded file; choose one of: {', '.join(GUARDED)}")
    try:
        ref, sha = resolve_merge_base(root, args.base_ref)
    except ToolError as exc:
        if args.allow_missing_base:
            return EXIT_OK, {"status": "skipped", "reason": str(exc), "results": []}
        raise
    results = check(root, sha, approved)
    bad = any(r["status"] == "violation" for r in results)
    report = {"status": "violation" if bad else "ok", "base_ref": ref, "base_sha": sha, "results": results}
    return (EXIT_VIOLATION if bad else EXIT_OK), report


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # argparse exits 2 on usage errors, which would read as "violation"
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        sys.exit(EXIT_TOOL_ERROR)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _Parser(description="craftflow baseline-integrity check (baselines and lint/hook config may only get stricter)")
    parser.add_argument("--root", default=str(ROOT), help="repository root (default: this checkout)")
    parser.add_argument("--base-ref", default=None, help="ref whose merge-base with HEAD is the comparison point (default: the default branch)")
    parser.add_argument("--allow-missing-base", action="store_true", help="SKIP (loudly, exit 0) when no base ref can be resolved; never use in CI")
    parser.add_argument("--approved-increase", action="append", metavar="FILE", help="human override for one guarded file (repeatable); agents must not use it")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    try:
        code, report = run(args)
    except ToolError as exc:
        print(f"baseline-integrity: ERROR: {exc}", file=sys.stderr)
        return EXIT_TOOL_ERROR
    print(json.dumps(report, indent=2) if args.format == "json" else render(report))
    return code


if __name__ == "__main__":
    sys.exit(main())
