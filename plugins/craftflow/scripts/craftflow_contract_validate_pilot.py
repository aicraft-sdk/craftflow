#!/usr/bin/env python3
"""
craftflow_contract_validate_pilot.py

Schema-validates the nested `verdict:`/`rationale:` Router Contract YAML body
emitted by the two pilot write-agents (component-builder, planner) against a
per-agent JSON schema table under scripts/schemas/.

Distinct from, and NOT a replacement for, craftflow_contract_validate.py --
the pre-existing, still-dormant validator for the flat, kind-keyed envelope
format used by read-only agents. That script's REQUIRED_FIELDS/VALID_STATUSES
tables and this script's schemas/*.json tables describe two different
contract shapes for two different agent families. Do not merge them.

Usage (CLI mode):
    python3 craftflow_contract_validate_pilot.py --agent component-builder --stdin < body.yaml
    python3 craftflow_contract_validate_pilot.py --agent planner --file body.yaml

Input is the *already-extracted* YAML body of the fenced ```yaml ... ``` block
under an agent's "### Router Contract (MACHINE-READABLE)" heading -- i.e.
text starting with `verdict:` -- not the full agent response. The router
extracts that block first (existing mechanism, SKILL.md Post-Agent
Validation), then feeds only the block body here (recommended: write it to a
scratch file and pass --file, to avoid shell heredoc quoting hazards with
YAML's colons/quotes).

Output: exactly one line of JSON on stdout:
    {"valid": bool, "violations": [{"field": str, "reason": str}, ...]}
Exit code mirrors `valid`: 0 = valid, 1 = invalid OR any internal failure
(schema missing/corrupt, unparseable input, bad CLI usage). All failure
paths are fail-closed -- never fail open and report valid=true on error.

When called as a library:
    from craftflow_contract_validate_pilot import parse_block_yaml, validate_contract
    parsed = parse_block_yaml(yaml_body_text)
    result = validate_contract(parsed, schema_dict)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMAS_DIR = SCRIPT_DIR / "schemas"

_LIST_ITEM_RE = re.compile(r"^-\s?(.*)$")


def _split_flow_items(inner: str) -> List[str]:
    """Split the inner content of an inline flow sequence `[a, b, c]` on
    top-level commas, respecting quoted strings and nested brackets so a
    comma inside a quoted string or a nested `[]`/`{}` does not split the
    sequence early. This exists because real agent output (an LLM writing
    a short ASSUMPTIONS/DECISIONS list) commonly uses inline flow style
    (`["a thing", "another thing"]`) rather than block `- item` style --
    verified against this session's own adversarial test run (see plan's
    Fresh Review Resolution) that a naive empty-`[]`-only check produces a
    false-negative "wrong type" violation against exactly this common,
    valid, real-world shape."""
    items: List[str] = []
    current: List[str] = []
    depth = 0
    in_quote: Optional[str] = None
    for ch in inner:
        if in_quote:
            current.append(ch)
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ("'", '"'):
            in_quote = ch
            current.append(ch)
            continue
        if ch in "[{":
            depth += 1
            current.append(ch)
            continue
        if ch in "]}":
            depth -= 1
            current.append(ch)
            continue
        if ch == "," and depth == 0:
            items.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        items.append("".join(current))
    return items


def _coerce_scalar(raw: str) -> Any:
    """Convert a bare YAML scalar token to a Python value. Best-effort --
    unrecognized shapes fall back to the raw string. Inline flow sequences
    (`[]`, `[a, b]`, `["a", "b"]`) are fully supported via
    _split_flow_items, since real agent output routinely writes short
    arrays inline rather than as a block `- item` list. Only inline flow
    *mappings* with content (`{k: v}`) are NOT supported -- every agent
    template in this plugin emits `MEMORY_NOTES` (the only object-typed
    field) as either empty `{}` or a full block, never `{k: v}` inline, so
    this narrower limitation is a documented, accepted gap, not a silent
    one."""
    raw = raw.strip()
    if raw == "":
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    if raw in ("null", "~"):
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if inner == "":
            return []
        return [_coerce_scalar(item.strip()) for item in _split_flow_items(inner)]
    if raw == "{}":
        return {}
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def parse_block_yaml(text: str) -> dict:
    """Minimal indentation-stack parser for the two-level `verdict:`/
    `rationale:` block-YAML subset this contract shape uses. Not a general
    YAML parser: no anchors/aliases, no non-empty inline flow *mappings*
    (`{k: v}`) -- inline flow *sequences* (`[a, b]`) ARE supported via
    _coerce_scalar/_split_flow_items. List items may be bare scalars or
    open an inline mapping (`- key: value`,
    with further same-item keys at a deeper indent than the dash); whether
    a `KEY:` line opens a list or a mapping is decided by peeking at the
    next deeper-indented line. Malformed/unrecognized lines are skipped,
    not raised -- a genuinely unparseable stream still can't crash this
    function for any str input; callers (validate_contract / main) treat a
    parse result missing `verdict`/`rationale` as violations, and main()
    additionally wraps the call in try/except for defense in depth.
    """
    raw_lines = text.split("\n")
    lines: List[tuple] = []
    for line in raw_lines:
        if line.strip() == "" or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        lines.append((indent, line.strip()))

    root: dict = {}
    stack: List[tuple] = [(-1, root)]

    def top_container(indent: int) -> Any:
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        return stack[-1][1]

    n = len(lines)
    i = 0
    while i < n:
        indent, content = lines[i]
        container = top_container(indent)

        m = _LIST_ITEM_RE.match(content)
        if m:
            if isinstance(container, list):
                item_content = m.group(1)
                if ":" in item_content:
                    key, _, value = item_content.partition(":")
                    key = key.strip()
                    value = value.strip()
                    item_dict: dict = {}
                    if value:
                        item_dict[key] = _coerce_scalar(value)
                    container.append(item_dict)
                    stack.append((indent, item_dict))
                else:
                    container.append(_coerce_scalar(item_content))
            i += 1
            continue

        if ":" not in content:
            i += 1
            continue

        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()

        if isinstance(container, dict):
            if value == "":
                next_is_list = (
                    i + 1 < n
                    and lines[i + 1][0] > indent
                    and (lines[i + 1][1].startswith("- ") or lines[i + 1][1] == "-")
                )
                child: Any = [] if next_is_list else {}
                container[key] = child
                stack.append((indent, child))
            else:
                container[key] = _coerce_scalar(value)
        i += 1

    return root


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return False


def _value_matches_type(value: Any, type_spec: Any) -> bool:
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    return any(_type_matches(value, t) for t in types)


def validate_contract(parsed: dict, schema: dict) -> dict:
    """Check every field declared in `schema` against `parsed` (the output
    of parse_block_yaml). Returns {"valid": bool, "violations": [...]}.
    Does not evaluate cross-field business rules (e.g. "at least one
    passing scenario", "OPEN_DECISIONS must be empty") -- those remain the
    router's existing Contract Overrides responsibility, unchanged by this
    validator (see design's Success Criteria: this validator checks
    missing/wrong-type/invalid-enum only)."""
    violations: List[dict] = []

    verdict = parsed.get("verdict")
    rationale = parsed.get("rationale")
    verdict_ok = isinstance(verdict, dict)
    rationale_ok = isinstance(rationale, dict)
    if not verdict_ok:
        violations.append({"field": "verdict", "reason": "missing or not a mapping"})
    if not rationale_ok:
        violations.append({"field": "rationale", "reason": "missing or not a mapping"})

    sections = {
        "verdict": verdict if verdict_ok else {},
        "rationale": rationale if rationale_ok else {},
    }

    for field_name, spec in schema.items():
        nested_under = spec["nested_under"]
        other_key = "rationale" if nested_under == "verdict" else "verdict"
        home = sections[nested_under]
        other = sections[other_key]

        if field_name not in home:
            if field_name in other:
                violations.append({
                    "field": field_name,
                    "reason": f"found under '{other_key}', expected under '{nested_under}'",
                })
            elif spec.get("required", False):
                violations.append({"field": field_name, "reason": "missing required field"})
            continue

        value = home[field_name]

        if not _value_matches_type(value, spec["type"]):
            violations.append({
                "field": field_name,
                "reason": f"expected type {spec['type']!r}, got {type(value).__name__}",
            })
            continue

        if "enum" in spec and value not in spec["enum"]:
            violations.append({
                "field": field_name,
                "reason": f"value {value!r} not in allowed set {spec['enum']}",
            })

    return {"valid": len(violations) == 0, "violations": violations}


def _fail_closed(reason: str) -> dict:
    return {"valid": False, "violations": [{"field": "<contract>", "reason": reason}]}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Schema-validate a pilot write-agent's nested verdict/rationale "
                     "Router Contract YAML body."
    )
    parser.add_argument("--agent", required=True, choices=["component-builder", "planner"])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stdin", action="store_true")
    group.add_argument("--file")
    args = parser.parse_args(argv)

    schema_path = SCHEMAS_DIR / f"{args.agent}.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(json.dumps(_fail_closed(f"failed to load schema {schema_path.name}: {exc}")))
        return 1

    try:
        text = sys.stdin.read() if args.stdin else Path(args.file).read_text(encoding="utf-8")
    except Exception as exc:
        print(json.dumps(_fail_closed(f"failed to read input: {exc}")))
        return 1

    try:
        parsed = parse_block_yaml(text)
    except Exception as exc:
        print(json.dumps(_fail_closed(f"failed to parse contract body: {exc}")))
        return 1

    result = validate_contract(parsed, schema)
    print(json.dumps(result))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
