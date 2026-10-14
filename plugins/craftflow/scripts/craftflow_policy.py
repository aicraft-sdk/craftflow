#!/usr/bin/env python3
"""Fail-closed gate policy loader (ADR-0073, router context cut r2, P4).

One policy source, `config/policy.json`, validated against `scripts/schemas/policy.json`. A file that omits a top-level
key takes that key's default (BC-1), so each phase ships only the keys it reads. A missing file gives the defaults (status
"defaults"); an unreadable, oversized, non-UTF-8, non-JSON, non-object, unknown-key or schema-invalid file gives the
defaults with status "invalid" (callers then take their most restrictive branch; `memory_write` is document-only).

Pure stdlib, Python 3.9 compatible. `load_policy` never writes to stdout or stderr. It logs one `policy_invalid` hook
event per process, and only inside an existing `.craftflow` project, so loading never creates state directories.

CLI: --validate | --get KEY | --hash | --print-table   (--validate exits 1 only for status "invalid")
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# DD-3: today's prose values. Every key here has a schema entry and (once its phase lands) a literal policy_value reader.
DEFAULTS: Dict[str, Any] = {
    "native_plan_mode": "never",
    "plan_commit": "never",
    "memory_write": "finalize-only",
    "worktree_merge": {
        "require_clean_tree": True,
        "lock_max_attempts": 9,
        "lock_sleep_seconds": 5,
        "copy_fallback_on_up_to_date": True,
    },
    "remediation_cycles_max": 3,
    "doubt_cycles_max": 3,
    "fix_verify_cycles_max": 3,
    "plan_review_passes_max": 2,
}
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "policy.json"
MAX_POLICY_BYTES = 64 * 1024
_logged_invalid = False


def policy_path() -> Path:
    """`<plugin root>/config/policy.json`. No policy-specific override (DD-8): CLAUDE_PLUGIN_ROOT, else this file's root."""
    value = os.environ.get("CLAUDE_PLUGIN_ROOT")
    root = Path(value) if value else Path(__file__).resolve().parents[1]
    return root / "config" / "policy.json"


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)  # bool is not an int here (E7)
    if kind == "string":
        return isinstance(value, str)
    if kind == "object":
        return isinstance(value, dict)
    return False


def _same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


def _validate_value(value: Any, spec: Dict[str, Any], where: str) -> List[str]:
    kind = spec.get("type", "")
    if not _type_ok(value, kind):
        return [f"{where}: expected {kind}, got {type(value).__name__}"]
    errors: List[str] = []
    if "const" in spec and not _same(value, spec["const"]):
        errors.append(f"{where}: must be {json.dumps(spec['const'])}, got {json.dumps(value)}")
    if "enum" in spec and value not in spec["enum"]:
        errors.append(f"{where}: must be one of {json.dumps(spec['enum'])}, got {json.dumps(value)}")
    if kind == "integer":
        if "minimum" in spec and value < spec["minimum"]:
            errors.append(f"{where}: {value} is below the minimum {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            errors.append(f"{where}: {value} is above the maximum {spec['maximum']}")
    if kind == "object":
        errors.extend(_validate_object(value, spec, where))
    return errors


def _validate_object(obj: Dict[str, Any], spec: Dict[str, Any], where: str) -> List[str]:
    props = spec.get("properties", {})
    ignored = spec.get("ignore_keys", [])
    prefix = f"{where}." if where else ""
    errors: List[str] = []
    for key in spec.get("required", []):
        if key not in obj:
            errors.append(f"{prefix}{key}: required key is missing")
    for key, value in obj.items():
        if key in ignored:
            if not isinstance(value, str):
                errors.append(f"{prefix}{key}: expected string, got {type(value).__name__}")
        elif key in props:
            errors.extend(_validate_value(value, props[key], f"{prefix}{key}"))
        elif spec.get("additionalProperties") is False:
            errors.append(f"{prefix}{key}: unknown key")
    return errors


def validate_policy(obj: Any, schema: Dict[str, Any]) -> List[str]:
    """Error strings for `obj` against the custom schema shape; [] when valid."""
    if not isinstance(obj, dict):
        return [f"policy: expected a JSON object, got {type(obj).__name__}"]
    return _validate_object(obj, schema, "")


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if key == "$comment":
            continue
        out[key] = copy.deepcopy(value)
    return out


def _log_invalid(path: Path, errors: List[str]) -> None:
    """Once per process, best effort, and only where a `.craftflow` project already exists (no state dirs are created)."""
    global _logged_invalid
    if _logged_invalid:
        return
    _logged_invalid = True
    try:
        import craftflow_hooklib as hooklib

        if (hooklib.project_dir() / ".craftflow").is_dir():
            hooklib.log_event("policy_invalid", {"path": str(path), "errors": errors[:5]})
    except Exception:  # never fail a hook because the log line could not be written
        pass


def load_policy(path: Optional[Path] = None, log: bool = True) -> Tuple[Dict[str, Any], str, List[str]]:
    """(policy, status, errors); status is "ok" | "defaults" | "invalid". The policy is never None and never aliases DEFAULTS."""
    target = Path(path) if path is not None else policy_path()
    errors: List[str] = []
    obj: Any = None
    if not target.exists():
        return copy.deepcopy(DEFAULTS), "defaults", []
    try:
        raw = target.read_bytes()
        if len(raw) > MAX_POLICY_BYTES:
            errors.append(f"policy: file is {len(raw)} bytes, over the {MAX_POLICY_BYTES} byte limit")
        else:
            obj = json.loads(raw.decode("utf-8"))
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            errors = validate_policy(obj, schema)
    except (OSError, ValueError) as exc:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        errors = [f"policy: cannot read {target.name}: {type(exc).__name__}: {exc}"]
    if errors:
        if log:
            _log_invalid(target, errors)
        return copy.deepcopy(DEFAULTS), "invalid", errors
    return _merge(DEFAULTS, obj), "ok", []


def policy_value(policy: Dict[str, Any], dotted: str) -> Any:
    """Value at a dotted key (`worktree_merge.lock_max_attempts`). KeyError for an unknown key, so a typo fails loudly."""
    node: Any = policy
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def policy_sha256(path: Optional[Path] = None) -> str:
    """sha256 of the policy file's bytes, or "defaults" when the file is missing or unreadable."""
    try:
        return hashlib.sha256((Path(path) if path is not None else policy_path()).read_bytes()).hexdigest()
    except OSError:
        return "defaults"


def enter_plan_mode_decision(native_plan_mode: Any, status: str) -> Tuple[str, str]:
    """("deny" | "allow", reason prefix) for an EnterPlanMode call. Takes the VALUE, not the policy (Advisory F)."""
    if status == "invalid":
        return "deny", "craftflow-policy: policy-invalid —"
    if native_plan_mode == "allow":
        return "allow", ""
    return "deny", "craftflow-policy: native_plan_mode=never —"


def _leaves(node: Dict[str, Any], prefix: str = "") -> List[str]:
    out: List[str] = []
    for key, value in node.items():
        out.extend(_leaves(value, f"{prefix}{key}.") if isinstance(value, dict) else [f"{prefix}{key}"])
    return out


def _allowed(spec: Dict[str, Any]) -> str:
    if "const" in spec:
        return f"only {json.dumps(spec['const'])}"
    if "enum" in spec:
        return "one of " + ", ".join(json.dumps(v) for v in spec["enum"])
    return f"integer {spec.get('minimum')}-{spec.get('maximum')}"


def print_table() -> str:
    """Markdown table of every leaf key with its default and allowed values (rendered from DEFAULTS and the schema)."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    lines = ["| Key | Default | Allowed |", "|---|---|---|"]
    for key in _leaves(DEFAULTS):
        spec: Any = schema
        for part in key.split("."):
            spec = spec["properties"][part]
        lines.append(f"| `{key}` | `{json.dumps(policy_value(DEFAULTS, key))}` | {_allowed(spec)} |")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Craftflow gate policy loader")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--validate", action="store_true", help="print {status, errors, sha256}; exit 1 only when invalid")
    group.add_argument("--get", metavar="KEY", help="print the effective value of a dotted key")
    group.add_argument("--hash", action="store_true", help="print the policy file's sha256 (or 'defaults')")
    group.add_argument("--print-table", action="store_true", help="print the Policy table markdown")
    args = parser.parse_args(argv)
    if args.print_table:
        print(print_table())
        return 0
    if args.hash:
        print(policy_sha256())
        return 0
    policy, status, errors = load_policy(log=False)
    if args.validate:
        print(json.dumps({"errors": errors, "sha256": policy_sha256(), "status": status}, sort_keys=True))
        return 1 if status == "invalid" else 0
    try:
        value = policy_value(policy, args.get)
    except KeyError:
        sys.stderr.write(f"craftflow_policy: unknown key {args.get!r}\n")
        return 1
    print(value if isinstance(value, str) else json.dumps(value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
