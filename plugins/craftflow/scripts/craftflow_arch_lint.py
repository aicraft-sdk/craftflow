#!/usr/bin/env python3
"""
craftflow_arch_lint.py

Architecture-boundary linter. Reads a rule registry
(.craftflow/state/project/arch-rules.json by default) and scans the repo for
violations, reporting WHAT/WHY/FIX per finding. Self-bootstraps the registry
with one seed rule if the file is missing. Advisory by default: findings are
input into code-reviewer's dispatch context, and the ROUTER decides what to do
with a non-empty findings list. A rule can opt in to blocking with an explicit
per-rule `"mode": "blocking"`; only then does this script signal failure.

Usage:
    craftflow_arch_lint.py [--project-root PATH] [--rules PATH]
        [--state-dir PATH] [--format text|json]
    craftflow_arch_lint.py --self-test [--rules PATH | --project-root PATH --state-dir DIR] [--format text|json]
    craftflow_arch_lint.py --check-proposal FILE.json [--format text|json]

Optional per-rule provenance and proof fields (all backward compatible; a registry without them behaves as before):
    "origin"    non-empty string: the incident/evidence that motivated the rule.
    "owner"     non-empty string: who decides to keep, demote or delete it.
    "added"     ISO date string (YYYY-MM-DD).
    "examples"  {"bad": [{"path", "content"}, ...], "good": [...]}. `path` is relative and must match the rule's
                scope_globs. --self-test writes each example to a throwaway directory outside the project and runs the
                real lint() on it: every bad example must produce a finding for the rule, every good one none.
A rule with "mode": "blocking" MUST carry non-empty `origin` and `owner`, otherwise the registry is a rules-schema error
(exit 1). Advisory rules may omit them. NOTE: this makes an older registry whose blocking rules lack provenance exit 1
until a human adds the two fields.

--self-test / --check-proposal live in craftflow_arch_rule_selftest.py (this file stays under the size cap). Exit codes
there: 0 proven (or no rules with examples), 1 tool/schema error (missing registry, unreadable or invalid rule, an
example whose path is outside scope_globs), 2 an example behaves wrongly, a blocking rule lacks a bad or a good example,
or a proposal lacks either. --self-test never scans the project and never writes or bootstraps the registry.

Per-rule `mode` (optional string, default "advisory"):
    "advisory"  findings are reported, never affect the exit code.
    "blocking"  findings are reported AND make the run exit 2.
Any other value (wrong spelling, wrong case, non-string) is a rules-schema
error, handled exactly like a corrupt rule (exit 1) -- an unrecognised mode
never silently degrades to advisory, because that would let a typo turn an
enforced rule into a no-op. A registry with no `mode` keys behaves exactly as
before this field existed.

Exit codes (distinct on purpose, so a caller can tell a verdict from a crash):
    0  completed scan; no findings, or only advisory findings.
    1  genuine tool error: the rules file exists but is corrupt JSON / has an
       invalid schema shape, a rule object is missing/misspells a required
       key or carries an invalid `mode` (never silently degrades to a no-op
       scan), a scope_glob scan itself raises an unexpected OSError, or the
       command line is malformed (argparse usage errors are mapped to 1 so
       they cannot be mistaken for 2).
    2  completed scan with at least one finding from a "blocking" rule. The
       full report is still printed to stdout (text or json); stderr is not
       used. A caller that gates on the result must treat 2 as "violations
       found", never as a crash.

Output: every finding carries its rule's `mode`. JSON adds a top-level
`summary` ({"total", "blocking", "advisory"}); text ends with the same counts.
JSON also carries `bootstrapped` (true when the rules file was missing and the
advisory seed registry was written). A deleted registry therefore re-seeds as
advisory, which is visible here rather than a silent downgrade.

Known syntax boundary: the import detector matches literal
`from '<prefix>`, `require('<prefix>`, `import('<prefix>`, and the bare
side-effect form `import '<prefix>'` -- it does NOT resolve non-literal
dynamic imports (e.g. `import(pkgVar)` where the module specifier is a
variable, not a string literal). This is a disclosed detection ceiling, not
full static-analysis coverage.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1
MODE_ADVISORY = "advisory"
MODE_BLOCKING = "blocking"
VALID_MODES = (MODE_ADVISORY, MODE_BLOCKING)
EXIT_OK = 0
EXIT_TOOL_ERROR = 1
EXIT_BLOCKING_FINDINGS = 2
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


class _ToolArgumentParser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, which would collide with this
    script's exit 2 ("blocking findings"). Report usage errors as a tool
    error (exit 1) instead."""

    def error(self, message: str):
        print(json.dumps({"error": f"usage error: {message}"}), file=sys.stderr)
        raise SystemExit(EXIT_TOOL_ERROR)


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
            "mode": MODE_ADVISORY,
            "origin": "seed rule shipped with craftflow_arch_lint.py (v1.13.0): tools/ is internal-only and must not import the publishable @ai-craft/* scope",
            "owner": "craftflow maintainers",
            "added": "2026-09-26",
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
            "examples": {
                "bad": [{"path": "tools/pkg/bad.ts", "content": "import { run } from '@ai-craft/agent-loop';\n"}],
                "good": [{"path": "tools/pkg/good.ts", "content": "import { run } from './local';\n"}],
            },
        }
    ]


def validate_rule(rule: object, index: int, where: str) -> None:
    """Validate one rule object (`where` prefixes every message, e.g. "rules file at <path>"). Raises RulesCorruptError."""
    if not isinstance(rule, dict):
        raise RulesCorruptError(f"{where}: rule at index {index} is not an object")
    rule_id = rule.get("id")
    if not isinstance(rule_id, str) or not rule_id:
        raise RulesCorruptError(f"{where}: rule at index {index} missing non-empty 'id'")
    if not isinstance(rule.get("scope_globs"), list) or not rule.get("scope_globs"):
        raise RulesCorruptError(f"{where}: rule '{rule_id}' (index {index}) missing non-empty 'scope_globs' list")
    if not isinstance(rule.get("forbidden_import_prefixes"), list) or not rule.get("forbidden_import_prefixes"):
        raise RulesCorruptError(
            f"{where}: rule '{rule_id}' (index {index}) missing non-empty 'forbidden_import_prefixes' list"
        )
    if "mode" in rule and (not isinstance(rule["mode"], str) or rule["mode"] not in VALID_MODES):
        raise RulesCorruptError(
            f"{where}: rule '{rule_id}' (index {index}) has invalid 'mode' "
            f"{rule['mode']!r}; expected one of {', '.join(VALID_MODES)} (or omit it for advisory)"
        )
    _validate_provenance(rule, rule_id, index, where)
    _validate_examples(rule.get("examples"), rule_id, index, where)


def _validate_provenance(rule: dict, rule_id: str, index: int, where: str) -> None:
    head = f"{where}: rule '{rule_id}' (index {index})"
    for key in ("origin", "owner", "added"):
        if key in rule and not isinstance(rule[key], str):
            raise RulesCorruptError(f"{head} has non-string '{key}' {rule[key]!r}; it must be a string")
    if "added" in rule:
        try:
            datetime.date.fromisoformat(rule["added"])
        except ValueError as exc:
            raise RulesCorruptError(f"{head} has 'added' {rule['added']!r}, which is not an ISO date (YYYY-MM-DD)") from exc
    if rule.get("mode") == MODE_BLOCKING:
        missing = [k for k in ("origin", "owner") if not (isinstance(rule.get(k), str) and rule[k].strip())]
        if missing:
            raise RulesCorruptError(
                f"{head} has \"mode\": \"blocking\" but is missing non-empty {' and '.join(repr(k) for k in missing)}. "
                "Add 'origin' (the incident or evidence that motivated the rule) and 'owner' (who decides to keep or "
                "delete it), or set the rule to advisory; a blocking rule must say where it came from."
            )


def _validate_examples(examples: object, rule_id: str, index: int, where: str) -> None:
    if examples is None:
        return
    head = f"{where}: rule '{rule_id}' (index {index}) 'examples'"
    if not isinstance(examples, dict) or set(examples) - {"bad", "good"}:
        raise RulesCorruptError(f"{head} must be an object with only the keys 'bad' and 'good'")
    for kind, items in examples.items():
        if not isinstance(items, list):
            raise RulesCorruptError(f"{head}.{kind} must be a list of {{\"path\", \"content\"}} objects")
        for n, item in enumerate(items):
            ok = isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("content"), str)
            ok = ok and bool(item["path"]) and not item["path"].startswith("/") and ".." not in Path(item["path"]).parts
            if not ok:
                raise RulesCorruptError(
                    f"{head}.{kind}[{n}] must be {{\"path\": <relative path inside scope_globs>, \"content\": <file text>}}"
                )


def _validate_rules_shape(data: dict, rules_path: Path) -> None:
    """Validate every rule object has the keys the scanner actually reads.
    A rule with a misspelled/missing key (e.g. scope_glob instead of
    scope_globs) must fail loudly here -- never silently degrade to a no-op
    scan that still reports a clean-looking rule_count/findings=[]."""
    for index, rule in enumerate(data.get("rules", [])):
        validate_rule(rule, index, f"rules file at {rules_path}")


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
                        "mode": rule.get("mode", MODE_ADVISORY),
                    })
    return findings, skipped


def _build_parser() -> argparse.ArgumentParser:
    parser = _ToolArgumentParser(description="Craftflow architecture-boundary linter (advisory by default; per-rule opt-in blocking).")
    parser.add_argument("--project-root", default=".", help="Project root to scan (default: .)")
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help=f"Craftflow state dir (default: {DEFAULT_STATE_DIR})")
    parser.add_argument("--rules", default=None, help="Rules file path (default: derived from --state-dir)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--self-test", action="store_true", help="prove every rule's bad/good examples (no project scan)")
    parser.add_argument("--check-proposal", metavar="FILE", default=None,
                        help="validate and self-test one proposed rule object; never touches the registry")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    rules_path = Path(args.rules) if args.rules else project_root / args.state_dir / DEFAULT_RULES_REL

    if args.self_test or args.check_proposal:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import craftflow_arch_rule_selftest as selftest  # lazy: that module imports this one

        if args.self_test and args.check_proposal:
            print(json.dumps({"error": "usage error: --self-test and --check-proposal are mutually exclusive"}), file=sys.stderr)
            return EXIT_TOOL_ERROR
        if args.check_proposal:
            return selftest.check_proposal_main(Path(args.check_proposal), args.format)
        return selftest.self_test_main(rules_path, args.format)

    bootstrapped = not rules_path.exists()
    try:
        registry = load_or_seed_rules(rules_path)
        findings, skipped = lint(project_root, registry)
    except (OSError, UnicodeDecodeError, RulesCorruptError) as exc:
        print(json.dumps({"error": f"unexpected error: {exc}"}), file=sys.stderr)
        return EXIT_TOOL_ERROR

    blocking = sum(1 for f in findings if f.get("mode") == MODE_BLOCKING)
    summary = {"total": len(findings), "blocking": blocking, "advisory": len(findings) - blocking}

    if args.format == "json":
        print(json.dumps(
            {
                "findings": findings,
                "rule_count": len(registry.get("rules", [])),
                "skipped": skipped,
                "summary": summary,
                "bootstrapped": bootstrapped,
            },
            indent=2,
        ))
    else:
        if bootstrapped:
            print(f"Arch lint: rules file {rules_path} was missing; seeded the default advisory registry.")
        if not findings:
            print("Arch lint: 0 findings.")
        for f in findings:
            print(f"WHAT: {f['what']}\nMODE: {f['mode']}\nWHY: {f['why']}\nFIX: {f['fix']}\n")
        if findings:
            print(
                f"Arch lint: {summary['total']} finding(s): "
                f"{summary['blocking']} blocking, {summary['advisory']} advisory."
            )
            if blocking:
                print("Arch lint: BLOCKING findings present (exit 2); fix them or report a wrong rule to the user.")
        if skipped:
            print(f"Skipped {len(skipped)} unreadable file(s):")
            for s_ in skipped:
                print(f"  {s_['path']}: {s_['reason']}")
    return EXIT_BLOCKING_FINDINGS if blocking else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
