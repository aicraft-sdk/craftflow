#!/usr/bin/env python3
"""Deterministic guards for repositories where AI agents do the work.

Installed into a target repo by the craftflow `agent-native-repo` skill (as tools/agent_guards.py) and run by its
CI workflows. Python 3.9+, standard library only.

Subcommands:
    diff-check --base REF [--labels a,b] [--config FILE]
        Scan `git diff REF...HEAD` for ways an agent can make a gate pass without doing the work: deleted test files,
        fewer assertions, skip/only markers, new lint/type suppressions, and edits to harness files (AGENTS.md, CI,
        lint config) without the human-applied label (default `harness-change`). The label excuses harness edits only.
    task-ref --pr-json FILE --open-prs-json FILE [--config FILE]
        The PR must reference a task (`Closes #N`, `Fixes #N`, `Resolves #N`, or `Task: <id>`), and no other open PR
        may reference the same task: one task, one writer.
    sweep --issues-json FILE --prs-json FILE [--now ISO] [--ttl-hours N] [--config FILE]
        Print the issue numbers whose claim went stale: labelled in-progress, quiet for longer than the TTL, and no
        open PR referencing them was updated within the TTL. The workflow releases them.

Each subcommand prints one JSON object. Exit codes: 0 clean, 1 tool error, 2 findings.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

EXIT_OK, EXIT_TOOL_ERROR, EXIT_FINDINGS = 0, 1, 2

DEFAULT_CONFIG: Dict[str, Any] = {
    "harness_label": "harness-change",
    "in_progress_label": "in-progress",
    "claim_ttl_hours": 48,
    "test_name_globs": ["test_*.py", "*_test.py", "*_test.go", "*.test.*", "*.spec.*", "*Test.java", "*Tests.cs"],
    "test_dirs": ["tests", "test", "__tests__", "spec"],
    "harness_globs": [
        "AGENTS.md", "*/AGENTS.md", "CLAUDE.md", "*/CLAUDE.md", ".claude/*", ".cursor/*", ".agents/guards.json",
        ".github/workflows/*", "CODEOWNERS", ".github/CODEOWNERS", ".husky/*", ".githooks/*", "tools/agent_guards.py", "tools/claude-stop-gate.sh",
        ".eslintrc*", "eslint.config.*", "ruff.toml", ".ruff.toml", "tsconfig*.json", ".golangci.*", "biome.json",
    ],
    "text_globs": ["*.md", "*.mdx", "*.txt", "*.rst"],
    "scan_exempt_globs": ["tools/agent_guards.py"],
}

SUPPRESSIONS = [
    re.compile(r"#\s*noqa(?!\s*:\s*[A-Z]+\d)"),
    re.compile(r"#\s*type:\s*ignore(?!\[)"),
    re.compile(r"@ts-(ignore|nocheck)\b"),
    re.compile(r"eslint-disable"),
    re.compile(r"//\s*nolint(?!:)"),
    re.compile(r"#\s*pylint:\s*disable"),
    re.compile(r"@SuppressWarnings\b"),
]
SKIP_MARKERS = re.compile(
    r"(\b(it|test|describe|context)\.(skip|only|todo)\s*\(|\bx(it|describe|test)\s*\(|\bf(it|describe)\s*\("
    r"|pytest\.mark\.(skip|xfail)|pytest\.skip\s*\(|unittest\.skip|\bt\.Skip(Now|f)?\s*\(|#\[ignore\]|@Disabled\b|@Ignore\b)"
)
ASSERTIONS = re.compile(r"\bassert|\bexpect\s*\(|\.should\b|\bt\.(Error|Errorf|Fatal|Fatalf)\s*\(|\brequire\.\w+\(")
TASK_PATTERNS = [
    re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)", re.IGNORECASE),
    re.compile(r"^\s*Task:\s*([A-Za-z][\w.]*-[\w.]+)\s*$", re.IGNORECASE | re.MULTILINE),
]


class ToolError(Exception):
    pass


def is_test_path(path: str, cfg: Dict[str, Any]) -> bool:
    parts = path.split("/")
    if any(p in cfg["test_dirs"] for p in parts[:-1]):
        return True
    return any(fnmatch.fnmatch(parts[-1], g) for g in cfg["test_name_globs"])


def is_harness_path(path: str, cfg: Dict[str, Any]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in cfg["harness_globs"])


def _split_files(diff_text: str) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    old_path = ""
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            cur = {"path": "", "deleted": False, "added": [], "removed": []}
            files.append(cur)
        elif cur is None:
            continue
        elif line.startswith("--- "):
            old_path = line[4:].strip()
            old_path = old_path[2:] if old_path.startswith("a/") else old_path
        elif line.startswith("+++ "):
            new_path = line[4:].strip()
            cur["deleted"] = new_path == "/dev/null"
            cur["path"] = old_path if cur["deleted"] else (new_path[2:] if new_path.startswith("b/") else new_path)
        elif line.startswith("+"):
            cur["added"].append(line[1:])
        elif line.startswith("-"):
            cur["removed"].append(line[1:])
    return [f for f in files if f["path"]]


def _finding(kind: str, path: str, detail: str) -> Dict[str, str]:
    return {"kind": kind, "file": path, "detail": detail}


def _scan_file(f: Dict[str, Any], cfg: Dict[str, Any], labelled: bool) -> List[Dict[str, str]]:
    path, out = f["path"], []
    test = is_test_path(path, cfg)
    if any(fnmatch.fnmatch(path, g) for g in cfg["text_globs"] + cfg["scan_exempt_globs"]):
        test, f = False, dict(f, added=[])
    if test and f["deleted"]:
        out.append(_finding("test-file-deleted", path, "a test file was removed"))
    for line in f["added"]:
        if any(p.search(line) for p in SUPPRESSIONS):
            out.append(_finding("suppression-added", path, line.strip()[:160]))
        if test and SKIP_MARKERS.search(line):
            out.append(_finding("test-skipped", path, line.strip()[:160]))
    if test:
        before = sum(1 for x in f["removed"] if ASSERTIONS.search(x))
        after = sum(1 for x in f["added"] if ASSERTIONS.search(x))
        if before > after:
            out.append(_finding("assertions-reduced", path, f"{before} assertion lines removed, {after} added"))
    if not labelled and is_harness_path(path, cfg):
        out.append(_finding("harness-change-unlabelled", path, f"needs the `{cfg['harness_label']}` label from a human"))
    return out


def scan_diff(diff_text: str, cfg: Dict[str, Any], labels: Iterable[str]) -> List[Dict[str, str]]:
    labelled = cfg["harness_label"] in set(labels)
    findings: List[Dict[str, str]] = []
    for f in _split_files(diff_text):
        findings.extend(_scan_file(f, cfg, labelled))
    return findings


def task_refs(text: Optional[str]) -> Set[str]:
    refs: Set[str] = set()
    for i, pattern in enumerate(TASK_PATTERNS):
        for m in pattern.finditer(text or ""):
            refs.add(f"#{m.group(1)}" if i == 0 else m.group(1))
    return refs


def check_task_ref(pr: Dict[str, Any], open_prs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    mine = task_refs(pr.get("body"))
    if not mine:
        return [{"kind": "missing-task-ref", "detail": "add `Closes #N` or `Task: <id>` to the PR description"}]
    findings = []
    for other in open_prs:
        if other.get("number") == pr.get("number"):
            continue
        for task in sorted(mine & task_refs(other.get("body"))):
            findings.append({"kind": "task-already-claimed", "task": task, "other_pr": other.get("number"),
                             "detail": f"open PR #{other.get('number')} already works on {task}"})
    return findings


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def stale_claims(issues: List[Dict[str, Any]], prs: List[Dict[str, Any]], now: str, ttl_hours: float,
                 label: str) -> List[int]:
    cutoff = _ts(now).timestamp() - ttl_hours * 3600
    recent: Set[str] = set()
    for pr in prs:
        if _ts(pr["updated_at"]).timestamp() >= cutoff:
            recent |= task_refs(pr.get("body"))
    stale = []
    for issue in issues:
        if "pull_request" in issue or label not in {lb.get("name") for lb in issue.get("labels", [])}:
            continue
        if _ts(issue["updated_at"]).timestamp() < cutoff and f"#{issue['number']}" not in recent:
            stale.append(issue["number"])
    return sorted(stale)


def load_config(path: Optional[str]) -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if path and Path(path).exists():
        try:
            cfg.update(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise ToolError(f"unreadable config {path}: {exc}") from exc
    return cfg


def _read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"unreadable JSON {path}: {exc}") from exc


def _git_diff(base: str) -> str:
    proc = subprocess.run(["git", "diff", "--no-color", "--no-ext-diff", "-M", f"{base}...HEAD"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise ToolError(f"git diff {base}...HEAD failed: {proc.stderr.strip()}")
    return proc.stdout


def run(args: argparse.Namespace) -> Dict[str, Any]:
    cfg = load_config(args.config)
    if args.cmd == "diff-check":
        labels = [x.strip() for x in (args.labels or "").split(",") if x.strip()]
        return {"findings": scan_diff(_git_diff(args.base), cfg, labels)}
    if args.cmd == "task-ref":
        return {"findings": check_task_ref(_read_json(args.pr_json), _read_json(args.open_prs_json))}
    now = args.now or datetime.now(timezone.utc).isoformat()
    ttl = args.ttl_hours if args.ttl_hours is not None else cfg["claim_ttl_hours"]
    issues, prs = _read_json(args.issues_json), _read_json(args.prs_json)
    return {"release": stale_claims(issues, prs, now, ttl, cfg["in_progress_label"]), "findings": []}


def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="agent-native repo guards")
    parser.add_argument("--config", default=".agents/guards.json")
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diff-check")
    d.add_argument("--base", required=True)
    d.add_argument("--labels", default="")
    t = sub.add_parser("task-ref")
    t.add_argument("--pr-json", required=True)
    t.add_argument("--open-prs-json", required=True)
    s = sub.add_parser("sweep")
    s.add_argument("--issues-json", required=True)
    s.add_argument("--prs-json", required=True)
    s.add_argument("--now")
    s.add_argument("--ttl-hours", type=float)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        report = run(args)
    except ToolError as exc:
        print(json.dumps({"error": str(exc)}))
        return EXIT_TOOL_ERROR
    print(json.dumps(report, indent=2))
    return EXIT_FINDINGS if report["findings"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
