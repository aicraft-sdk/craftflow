#!/usr/bin/env python3
"""Plan rule check levels and per-phase test filters (adapted from testectomy's spec rules, MIT; see NOTICE).

A plan's rules are list lines that start with an `SC-###` id and an optional bracket of `key: value` pairs:

    - SC-012 [check: test | phase: P2 | keep: a negative total charges the customer] A coupon never makes a total negative.

`check` is `type` (the type checker or compiler proves it), `read` (a reviewer re-reads it against the diff; the
default) or `test` (a test that cites the id proves it). A `test` rule needs a `keep:` reason from the test-value gate.
The rule text says what the user sees and never names an internal function, so a rename cannot stale it.

Usage:
    craftflow_plan_rules.py --plan PLAN.md [--phase P2]

Prints one JSON line: the parsed rules, lint errors, and with --phase the filters that select that phase's `test`
rules (pytest `-k`, jest/vitest `-t`, go `-run`) plus the phase's `read` and `type` ids. Exit codes: 0 clean, 1 lint
errors, 2 the plan is missing or unreadable. Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

LEVELS = ("type", "read", "test")
RULE = re.compile(r"^\s*[-*]\s+(SC-\d{3,})\b\s*(?:\[([^\]]*)\])?\s*(.*)$", re.M)
# Code in a rule: a call like `name()`, or a backticked identifier with an underscore, dot or camel hump.
CODE_NAME = re.compile(r"`[^`]*\(\)`|\b[a-z_][a-z0-9_]*\(\)|`[A-Za-z_]*(?:_|\.|[a-z][A-Z])[A-Za-z0-9_.]*`")


def parse_rules(text: str) -> List[Dict[str, str]]:
    rules = []
    for match in RULE.finditer(text):
        fields: Dict[str, str] = {}
        for part in (match.group(2) or "").split("|"):
            key, sep, value = part.partition(":")
            if sep:
                fields[key.strip().lower()] = value.strip()
        rules.append({"id": match.group(1), "check": fields.get("check", "read"), "phase": fields.get("phase", ""),
                      "keep": fields.get("keep", ""), "text": match.group(3).strip()})
    return rules


def lint(rules: List[Dict[str, str]]) -> List[str]:
    errors, seen = [], set()
    for rule in rules:
        rid = rule["id"]
        if rid in seen:
            errors.append(f"{rid}: the id is used twice")
        seen.add(rid)
        if rule["check"] not in LEVELS:
            errors.append(f"{rid}: unknown check level {rule['check']!r} (one of {', '.join(LEVELS)})")
        if rule["check"] == "test" and not rule["keep"]:
            errors.append(f"{rid}: a test rule needs a keep: reason from the test-value gate")
        if CODE_NAME.search(rule["text"]):
            errors.append(f"{rid}: the rule names code; say what the user sees so a rename cannot stale it")
    return errors


def filters(rules: List[Dict[str, str]], phase: str) -> Dict[str, object]:
    in_phase = [r for r in rules if r["phase"] == phase]
    ids = [r["id"] for r in in_phase if r["check"] == "test"]
    result: Dict[str, object] = {"phase": phase, "ids": ids,
                                 "read": [r["id"] for r in in_phase if r["check"] == "read"],
                                 "type": [r["id"] for r in in_phase if r["check"] == "type"],
                                 "pytest": "", "jest": "", "go": ""}
    if ids:
        result["pytest"] = "-k '" + " or ".join(i.replace("-", "_") for i in ids) + "'"
        result["jest"] = "-t '" + "|".join(ids) + "'"
        result["go"] = "-run '" + "|".join(i.replace("-", "") for i in ids) + "'"
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Lint plan rule check levels and print per-phase test filters.")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--phase", help="phase id whose test filters to print, e.g. P2")
    args = parser.parse_args(argv)
    try:
        text = Path(args.plan).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(json.dumps({"error": f"cannot read plan: {exc}"}))
        return 2
    rules = parse_rules(text)
    report: Dict[str, object] = {"plan": args.plan, "rules": rules, "errors": lint(rules)}
    if args.phase:
        report["filters"] = filters(rules, args.phase)
    print(json.dumps(report, sort_keys=True))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
