#!/usr/bin/env python3
"""
craftflow_contract_validate.py

Validates a craftflow agent's Router Contract YAML block against the
agent_contract.schema.json kind overlays.

Usage (CLI mode):
    python3 craftflow_contract_validate.py --kind builder < agent_output.txt
    python3 craftflow_contract_validate.py --kind verifier < agent_output.txt

Output JSON: {"valid": true/false, "errors": [...]}
Exit 0 on success (even if invalid — exit is about parse success, not validity).
Exit 1 on usage error.

When called as library:
    from craftflow_contract_validate import validate_contract
    result = validate_contract(agent_text, kind)
    # result: {"valid": bool, "errors": list[str]}
"""

import argparse
import json
import re
import sys


# ---------------------------------------------------------------------------
# Required fields per agent kind
# ---------------------------------------------------------------------------

REQUIRED_FIELDS: dict = {
    "builder": [
        "STATUS", "CONFIDENCE", "PHASE_ID", "PHASE_STATUS", "PHASE_EXIT_READY",
        "PROOF_STATUS", "TDD_RED_EXIT", "TDD_GREEN_EXIT", "SCENARIOS",
        "BLOCKING", "REMEDIATION_NEEDED",
    ],
    "verifier": ["STATUS", "SCENARIOS", "BLOCKING", "REMEDIATION_NEEDED"],
    "reviewer": ["STATUS", "BLOCKING", "REMEDIATION_NEEDED"],
    "planner": [
        "STATUS", "PLAN_FILE", "PLAN_MODE", "CONFIDENCE", "GATE_PASSED",
        "OPEN_DECISIONS", "SCENARIOS", "BLOCKING", "REMEDIATION_NEEDED",
    ],
    "investigator": [
        "STATUS", "ROOT_CAUSE", "TDD_RED_EXIT", "TDD_GREEN_EXIT",
        "BLOCKING", "REMEDIATION_NEEDED",
    ],
    "doc_syncer": ["STATUS", "IMPACT_LEVEL", "DOC_LAYERS_EVALUATED", "BLOCKING"],
    "researcher": ["STATUS", "FILE_PATH", "QUALITY_LEVEL", "BLOCKING"],
}

VALID_STATUSES = {"COMPLETE", "PASS", "BLOCKED", "SKIPPED", "FAIL", "FIXED"}

# Heading that introduces the YAML block
_HEADING_PATTERN = re.compile(
    r"###\s+Router\s+Contract\s+\(MACHINE-READABLE\)", re.IGNORECASE
)


def extract_yaml_block(text: str):
    """
    Find the fenced YAML block under '### Router Contract (MACHINE-READABLE)'.

    Returns the content between the opening ```yaml and closing ``` fences,
    or None if not found.
    """
    # Find the heading
    heading_match = _HEADING_PATTERN.search(text)
    if not heading_match:
        return None

    after_heading = text[heading_match.end():]

    # Find the opening ```yaml fence (allow optional whitespace)
    open_match = re.search(r"```yaml\s*\n", after_heading)
    if not open_match:
        return None

    content_start = open_match.end()
    remaining = after_heading[content_start:]

    # Find the closing ``` fence
    close_match = re.search(r"\n```", remaining)
    if close_match:
        return remaining[: close_match.start()]

    # Fallback: no explicit close, return everything after the open fence
    return remaining.rstrip()


def parse_yaml_fields(yaml_text: str) -> dict:
    """
    Minimal line-by-line parser.  Handles top-level KEY: value pairs only.

    - Keys are returned as-is.
    - Values are stripped strings.
    - List items (lines starting with two or more spaces + '- ') are collected
      under the current key as a list of strings.
    - Nested objects (non-list indented lines) are skipped.
    - Returns dict of {KEY: value_or_list}.
    """
    result: dict = {}
    current_key: str | None = None

    for raw_line in yaml_text.splitlines():
        # Top-level key: value  (no leading whitespace)
        if raw_line and not raw_line[0].isspace():
            if ":" in raw_line:
                key, _, value = raw_line.partition(":")
                key = key.strip()
                value = value.strip()
                result[key] = value
                current_key = key
            # else: continuation or malformed — skip
            continue

        # Indented line
        stripped = raw_line.lstrip()
        if not stripped:
            continue

        # List item
        if stripped.startswith("- ") or stripped == "-":
            if current_key is not None:
                item_text = stripped[2:].strip() if stripped.startswith("- ") else ""
                existing = result.get(current_key)
                if isinstance(existing, list):
                    existing.append(item_text)
                else:
                    # First list item — convert scalar to list
                    result[current_key] = [item_text]
            continue

        # Non-list indented: nested object line — skip

    return result


def validate_contract(agent_text: str, kind: str) -> dict:
    """
    Validate the Router Contract YAML block embedded in agent_text.

    Returns {"valid": bool, "errors": list[str]}.
    """
    errors: list = []

    # 1. Extract YAML block
    yaml_block = extract_yaml_block(agent_text)
    if yaml_block is None:
        return {"valid": False, "errors": ["no Router Contract YAML block found"]}

    # 2. Parse fields
    fields = parse_yaml_fields(yaml_block)

    # 3. Determine required fields — unknown kinds fall back to base ["STATUS"]
    required = REQUIRED_FIELDS.get(kind, ["STATUS"])

    # 4. Check required fields
    for field in required:
        if field not in fields:
            errors.append(f"missing required field: {field}")

    # 5. Validate STATUS value when present
    if "STATUS" in fields:
        status_value = fields["STATUS"]
        if status_value not in VALID_STATUSES:
            errors.append(
                f"STATUS value {status_value!r} is not one of "
                f"{sorted(VALID_STATUSES)}"
            )

    return {"valid": len(errors) == 0, "errors": errors}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a craftflow agent Router Contract YAML block."
    )
    parser.add_argument(
        "--kind",
        required=True,
        help="Agent kind (builder, verifier, reviewer, planner, investigator, "
             "doc_syncer, researcher, or any custom kind).",
    )
    args = parser.parse_args()

    agent_text = sys.stdin.read()
    result = validate_contract(agent_text, args.kind)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
