#!/usr/bin/env python3
"""Fail-closed gate policy loader (ADR-0073, router context cut r2, P4).

One policy source, `config/policy.json`, validated against `scripts/schemas/policy.json`. A file that omits a top-level
key takes that key's default (BC-1), so each phase ships only the keys it reads. A missing file gives the defaults (status
"defaults"); an unreadable, oversized, non-UTF-8, non-JSON, non-object, unknown-key or schema-invalid file gives the
defaults with status "invalid" (callers then take their most restrictive branch; `memory_write` is document-only).

Pure stdlib, Python 3.9 compatible. `load_policy` never writes to stdout or stderr. It logs one `policy_invalid` hook
event per process, and only inside an existing `.craftflow` project, so loading never creates state directories.

CLI: --validate | --get KEY | --hash | --print-table   (--validate exits 1 only for status "invalid"; --get on an invalid
policy still exits 0 and prints the default, but writes the first error to stderr)
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
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


def _clip(text: str, limit: int = 120) -> str:
    """ASCII-safe and at most `limit` chars, so an error string never echoes a huge or hostile key or value."""
    text = text.encode("ascii", "backslashreplace").decode("ascii")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _read_regular(target: Path) -> bytes:
    """At most MAX_POLICY_BYTES + 1 bytes of a regular file. Opened O_NONBLOCK so a FIFO cannot block the open, and checked
    with fstat before any read so a FIFO, device or /dev/zero is never read."""
    # hasattr, not getattr: the conductor import-closure extractor treats getattr as an opaque dynamic import.
    nonblock = os.O_NONBLOCK if hasattr(os, "O_NONBLOCK") else 0
    fd = os.open(target, os.O_RDONLY | nonblock)
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise ValueError("not a regular file")
        return fh.read(MAX_POLICY_BYTES + 1)


def _log_invalid(path: Path, errors: List[str]) -> None:
    """Best effort, once per process after a successful write (hooklib.log_event returns False on a failed one, so a later
    load retries), and only where a `.craftflow` project already exists (no state dirs are created)."""
    global _logged_invalid
    if _logged_invalid:
        return
    try:
        import craftflow_hooklib as hooklib

        if (hooklib.project_dir() / ".craftflow").is_dir():
            _logged_invalid = hooklib.log_event("policy_invalid", {"path": str(path), "errors": errors[:5]})
    except Exception:  # never fail a hook because the log line could not be written
        pass


def load_policy_with_sha(path: Optional[Path] = None, log: bool = True) -> Tuple[Dict[str, Any], str, List[str], str]:
    """(policy, status, errors, sha256). TOTAL: never raises. The sha is of the very bytes that were validated;
    "defaults" for a missing file; "unreadable" when no (or not all) bytes were read. Missing file -> "defaults"; any other
    failure (unreadable, a directory, a FIFO, oversized, deeply nested JSON, ...) -> "invalid"."""
    errors: List[str] = []
    obj: Any = None
    sha = "unreadable"
    target = Path("policy.json")
    try:
        target = Path(path) if path is not None else policy_path()
        try:
            raw = _read_regular(target)
        except FileNotFoundError:
            return copy.deepcopy(DEFAULTS), "defaults", [], "defaults"
        if len(raw) > MAX_POLICY_BYTES:
            errors.append(f"policy: file is over the {MAX_POLICY_BYTES} byte limit")
        else:
            sha = hashlib.sha256(raw).hexdigest()
            obj = json.loads(raw.decode("utf-8"))
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            errors = validate_policy(obj, schema)
    except Exception as exc:  # RecursionError on deep nesting is not a ValueError; the loader must be total
        errors = [f"policy: cannot load {target.name}: {type(exc).__name__}: {exc}"]
    errors = [_clip(e) for e in errors]
    if errors:
        if log:
            _log_invalid(target, errors)
        return copy.deepcopy(DEFAULTS), "invalid", errors, sha
    return _merge(DEFAULTS, obj), "ok", [], sha


def load_policy(path: Optional[Path] = None, log: bool = True) -> Tuple[Dict[str, Any], str, List[str]]:
    """(policy, status, errors); status is "ok" | "defaults" | "invalid". The policy is never None and never aliases DEFAULTS."""
    policy, status, errors, _sha = load_policy_with_sha(path, log)
    return policy, status, errors


def policy_value(policy: Dict[str, Any], dotted: str) -> Any:
    """Value at a dotted key (`worktree_merge.lock_max_attempts`). KeyError for an unknown key, so a typo fails loudly."""
    node: Any = policy
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def policy_sha256(path: Optional[Path] = None) -> str:
    """sha256 of the policy file's bytes; "defaults" when the file is missing, "unreadable" when it cannot be read."""
    return load_policy_with_sha(path, log=False)[3]


def enter_plan_mode_decision(native_plan_mode: Any, status: str) -> Tuple[str, str]:
    """("deny" | "allow", reason prefix) for an EnterPlanMode call. Takes the VALUE, not the policy (Advisory F)."""
    if status == "invalid":
        return "deny", "craftflow-policy: policy-invalid —"
    if native_plan_mode == "allow":
        return "allow", ""
    return "deny", "craftflow-policy: native_plan_mode=never —"


# (key, enforced by, prose location): the kernel's `### Policy table` is printed from this and the effective policy.
TABLE_ROWS = (
    ("native_plan_mode", "`craftflow_pretooluse_guard.py`", "§ 14 EnterPlanMode rule"),
    ("plan_commit", "`craftflow_pretooluse_bash_guard.py`", "§ 14 plan-artifact rule"),
    ("memory_write", "`craftflow_pretooluse_guard.py`", "`references/memory-finalize.md → ## 13. Memory Finalization`"),
    ("worktree_merge", "`craftflow_pretooluse_bash_guard.py` (clean tree)", "worktree.md"),
    ("remediation_cycles_max", "`craftflow_gate_check.py`", "§ 14 remediation loop rule"),
    ("doubt_cycles_max", "`craftflow_gate_check.py`", "verify-dispatch.md Doubt-Verify"),
    ("fix_verify_cycles_max", "`craftflow_gate_check.py`", "verify-dispatch.md Fix-Verify"),
    ("plan_review_passes_max", "`craftflow_gate_check.py`", "`references/plan-workflow.md`"),
)


def _render(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k}={_render(v)}" for k, v in value.items())
    return value if isinstance(value, str) else json.dumps(value)


def print_table(policy: Optional[Dict[str, Any]] = None) -> str:
    """Markdown Policy table of the EFFECTIVE policy (the file merged over DEFAULTS, so a key not shipped yet prints its default)."""
    if policy is None:
        policy = load_policy(log=False)[0]
    lines = ["| key | value | enforced by | prose location |", "|---|---|---|---|"]
    for key, enforced_by, prose in TABLE_ROWS:
        lines.append(f"| `{key}` | `{_render(policy_value(policy, key))}` | {enforced_by} | {prose} |")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Craftflow gate policy loader")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--validate", action="store_true", help="print {status, errors, sha256}; exit 1 only when invalid")
    group.add_argument("--get", metavar="KEY", help="print the effective value of a dotted key (invalid policy: exit 0, first error on stderr)")
    group.add_argument("--hash", action="store_true", help="print the policy file's sha256 (or 'defaults' / 'unreadable')")
    group.add_argument("--print-table", action="store_true", help="print the Policy table markdown")
    args = parser.parse_args(argv)
    if args.print_table:
        print(print_table())
        return 0
    policy, status, errors, sha = load_policy_with_sha(log=False)
    if args.hash:
        print(sha)
        return 0
    if args.validate:
        print(json.dumps({"errors": errors, "sha256": sha, "status": status}, sort_keys=True))
        return 1 if status == "invalid" else 0
    if status == "invalid":
        sys.stderr.write(f"craftflow_policy: policy.json invalid: {errors[0]}\n")
    try:
        value = policy_value(policy, args.get)
    except KeyError:
        sys.stderr.write(f"craftflow_policy: unknown key {args.get!r}\n")
        return 1
    print(value if isinstance(value, str) else json.dumps(value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
