#!/usr/bin/env python3
"""
craftflow_arch_lint.py

Advisory architecture-boundary linter. Reads a rule registry
(.craftflow/state/project/arch-rules.json by default) and scans the repo for
violations, reporting WHAT/WHY/FIX per finding. Self-bootstraps the registry
with one seed rule if the file is missing. Never fails a workflow -- findings
are advisory input into code-reviewer's dispatch context only; the ROUTER
decides what to do with a non-empty findings list, this script never does.

Usage:
    craftflow_arch_lint.py [--project-root PATH] [--rules PATH]
        [--state-dir PATH] [--format text|json]

Exit 0 on a completed scan, even with findings (findings are not a failure
signal for this script). Exit 1 only on a genuine tool error: the rules file
exists but is corrupt JSON / has an invalid schema shape, a rule object is
missing/misspells a required key (never silently degrades to a no-op scan),
or a scope_glob scan itself raises an unexpected OSError.

Known syntax boundary: the import detector matches literal
`from '<prefix>`, `require('<prefix>`, `import('<prefix>`, and the bare
side-effect form `import '<prefix>'` -- it does NOT resolve non-literal
dynamic imports (e.g. `import(pkgVar)` where the module specifier is a
variable, not a string literal). This is a disclosed detection ceiling, not
full static-analysis coverage.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_STATE_DIR = ".craftflow/state"
DEFAULT_RULES_REL = "project/arch-rules.json"

SKIP_DIRS = {".git", "node_modules", ".next", "dist", "build", "__pycache__", ".venv", "venv"}

# Matches a real import/require statement referencing the prefix, never a bare
# substring occurrence -- this repo has 3 real comment-only false positives
# today (tools/craftdeck/src/observer-bridge-core.ts:1, tools/craftdeck/src/
# observer-bridge-core.ts:179, tools/craftdeck/src/observer-bridge-core.spec.ts:2,
# all plain prose mentioning "@ai-craft/agent-observer") that a substring match
# would wrongly flag. Verified via direct grep before writing this regex.
# Covers `from '<prefix>`, `require('<prefix>`, `import('<prefix>` (dynamic),
# and the bare side-effect form `import '<prefix>'` (no `from` clause). Does
# NOT resolve non-literal dynamic import(pkgVar) -- see module docstring.
_IMPORT_RE_TEMPLATE = (
    r"""(?:from\s+['"]{prefix}|require\(\s*['"]{prefix}|import\(\s*['"]{prefix}"""
    r"""|import\s+['"]{prefix})"""
)


class RulesCorruptError(Exception):
    """Rules file EXISTS but content cannot be trusted (parse failure,
    invalid top-level shape, or a rule object missing/misspelling a required
    key) -- mirrors craftflow_reliability_gates.py's LedgerCorruptError.
    Distinguished from "file does not exist" (benign, self-bootstraps
    instead). Never silently degrades a malformed rule to a no-op scan --
    that would be indistinguishable from a genuinely clean scan."""


def _seed_rules() -> list:
    return [
        {
            "id": "no-tools-import-packages",
            "scope_globs": ["tools/**/*.ts", "tools/**/*.tsx", "tools/**/*.js"],
            "forbidden_import_prefixes": ["@ai-craft/"],
            "what": "imports from the publishable @ai-craft/* package scope from inside tools/",
            "why": (
                "tools/ is internal-only (never published to npm); packages/ is the "
                "publishable @ai-craft/* surface. A tools/ -> packages/ import inverts the "
                "intended boundary and risks a circular release dependency."
            ),
            "fix": (
                "Move the shared logic into a package under packages/ that both sides "
                "depend on, or duplicate the narrow utility locally inside the tools/ "
                "package instead of importing @ai-craft/*."
            ),
        }
    ]


def _validate_rules_shape(data: dict, rules_path: Path) -> None:
    """Validate every rule object has the keys the scanner actually reads.
    A rule with a misspelled/missing key (e.g. scope_glob instead of
    scope_globs) must fail loudly here -- never silently degrade to a no-op
    scan that still reports a clean-looking rule_count/findings=[]."""
    for index, rule in enumerate(data.get("rules", [])):
        if not isinstance(rule, dict):
            raise RulesCorruptError(f"rules file at {rules_path}: rule at index {index} is not an object")
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            raise RulesCorruptError(f"rules file at {rules_path}: rule at index {index} missing non-empty 'id'")
        if not isinstance(rule.get("scope_globs"), list) or not rule.get("scope_globs"):
            raise RulesCorruptError(
                f"rules file at {rules_path}: rule '{rule_id}' (index {index}) missing non-empty 'scope_globs' list"
            )
        if not isinstance(rule.get("forbidden_import_prefixes"), list) or not rule.get("forbidden_import_prefixes"):
            raise RulesCorruptError(
                f"rules file at {rules_path}: rule '{rule_id}' (index {index}) missing non-empty "
                "'forbidden_import_prefixes' list"
            )


def load_or_seed_rules(rules_path: Path) -> dict:
    if not rules_path.exists():
        registry = {"schema_version": SCHEMA_VERSION, "rules": _seed_rules()}
        rules_path.parent.mkdir(parents=True, exist_ok=True)
        rules_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        _validate_rules_shape(registry, rules_path)
        return registry
    try:
        data = json.loads(rules_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RulesCorruptError(f"rules file at {rules_path} exists but failed to parse: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise RulesCorruptError(f"rules file at {rules_path} exists but has an invalid schema shape")
    _validate_rules_shape(data, rules_path)
    return data


def _iter_scope_files(project_root: Path, scope_globs: list) -> list:
    files: list = []
    for scope_glob in scope_globs:
        for path in project_root.glob(scope_glob):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(project_root).parts):
                continue
            files.append(path)
    return files


def lint(project_root: Path, registry: dict) -> tuple:
    """Returns (findings, skipped). `skipped` records every in-scope file that
    could not be read (permission error, TOCTOU-deleted, broken symlink) as
    {"path": ..., "reason": ...} so callers can distinguish "clean" from
    "clean but N files unreadable" -- never a silent, signal-free skip."""
    findings: list = []
    skipped: list = []
    for rule in registry.get("rules", []):
        patterns = [
            re.compile(_IMPORT_RE_TEMPLATE.format(prefix=re.escape(prefix)))
            for prefix in rule.get("forbidden_import_prefixes", [])
        ]
        for file_path in _iter_scope_files(project_root, rule.get("scope_globs", [])):
            rel = str(file_path.relative_to(project_root))
            try:
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                skipped.append({"path": rel, "reason": str(exc)})
                continue
            for line_no, line in enumerate(lines, start=1):
                if any(p.search(line) for p in patterns):
                    findings.append({
                        "rule_id": rule.get("id"),
                        "what": f"{rel}:{line_no} {rule.get('what', '')}",
                        "why": rule.get("why", ""),
                        "fix": rule.get("fix", ""),
                        "file": rel,
                        "line": line_no,
                    })
    return findings, skipped


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Craftflow advisory architecture-boundary linter.")
    parser.add_argument("--project-root", default=".", help="Project root to scan (default: .)")
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help=f"Craftflow state dir (default: {DEFAULT_STATE_DIR})")
    parser.add_argument("--rules", default=None, help="Rules file path (default: derived from --state-dir)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    rules_path = Path(args.rules) if args.rules else project_root / args.state_dir / DEFAULT_RULES_REL

    try:
        registry = load_or_seed_rules(rules_path)
        findings, skipped = lint(project_root, registry)
    except (OSError, UnicodeDecodeError, RulesCorruptError) as exc:
        print(json.dumps({"error": f"unexpected error: {exc}"}), file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(
            {"findings": findings, "rule_count": len(registry.get("rules", [])), "skipped": skipped},
            indent=2,
        ))
    else:
        if not findings:
            print("Arch lint: 0 findings.")
        for f in findings:
            print(f"WHAT: {f['what']}\nWHY: {f['why']}\nFIX: {f['fix']}\n")
        if skipped:
            print(f"Skipped {len(skipped)} unreadable file(s):")
            for s in skipped:
                print(f"  {s['path']}: {s['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
