#!/usr/bin/env python3
"""
craftflow_arch_rule_selftest.py

Proof step for arch-lint rules: `craftflow_arch_lint.py --self-test` and `--check-proposal FILE` run through here (the
CLI entry stays in craftflow_arch_lint.py). Nothing in this module matches code itself: every example is written to a
throwaway directory OUTSIDE the project and checked with craftflow_arch_lint.lint(), the same function a real scan uses.

--self-test        for every rule in the registry: each `examples.bad` entry must produce at least one finding for the
                   rule, each `examples.good` entry none. Per-rule status: PASS, FAIL, or NO-EXAMPLES. Does not scan the
                   project and never creates or writes the registry: a MISSING registry is exit 1 (a clear message), an
                   empty one is exit 0 ("no rules").
--check-proposal   validates ONE proposed rule object (a JSON file holding the rule itself, not a registry) with the
                   registry's schema (blocking needs origin and owner), then self-tests it. Unlike --self-test it
                   requires both a bad and a good example, because proving the rule is the point of the command. It
                   never reads or writes the registry; an agent runs it on its own proposal, then hands the JSON to a
                   human, because the pre-tool-use guard denies agent writes to the registry.

Exit codes: 0 proven; 1 tool/schema error (missing registry or file, bad JSON, invalid rule, an example whose path is
not matched by the rule's scope_globs); 2 an example behaves wrongly, a `blocking` rule lacks a bad or a good example, or
a proposal lacks one. Python 3.9, standard library only.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

import craftflow_arch_lint as al

STATUS_PASS, STATUS_FAIL, STATUS_NONE = "PASS", "FAIL", "NO-EXAMPLES"
NEXT_STEP = (
    "NEXT STEP (human): this proposal is proven. Add the object to the \"rules\" array of "
    ".craftflow/state/project/arch-rules.json (agents cannot write that file: the pre-tool-use guard denies it; check the id "
    "is not already used), keep it \"mode\": \"advisory\" until it has run clean, then confirm with: "
    "python3 <plugin>/scripts/craftflow_arch_lint.py --self-test"
)
BLOCKING_NOTE = (
    " This proposal is already \"blocking\": prefer adding it as advisory first and promote it only after it has run clean."
)


def _example_findings(rule: dict, example: dict) -> list:
    """Findings of `rule` on one example, materialised alone in a temp dir outside the project."""
    with tempfile.TemporaryDirectory(prefix="craftflow-arch-selftest-") as tmp:
        root = Path(tmp)
        target = root / example["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(example["content"], encoding="utf-8")
        if target not in al._iter_scope_files(root, rule["scope_globs"]):
            raise al.RulesCorruptError(
                f"rule '{rule['id']}': example path {example['path']!r} is not matched by its scope_globs "
                f"{rule['scope_globs']}; give the example a path the rule would actually scan"
            )
        findings, _skipped = al.lint(root, {"rules": [rule]})
    return [f for f in findings if f.get("rule_id") == rule["id"]]


def prove_rule(rule: dict, require_both: bool = False) -> Dict[str, object]:
    """{id, mode, status, bad, good, problems, gate_fail}; raises RulesCorruptError for a schema problem."""
    examples = rule.get("examples") or {}
    bad, good = examples.get("bad", []), examples.get("good", [])
    problems: List[Dict[str, str]] = []
    for kind, items in (("bad", bad), ("good", good)):
        for n, example in enumerate(items):
            hits = _example_findings(rule, example)
            label = f"{kind} example #{n} ({example['path']})"
            if kind == "bad" and not hits:
                problems.append({
                    "what": f"rule '{rule['id']}': {label} produced no finding, so the rule does not fire on the mistake it exists to catch.",
                    "fix": "Make the example a real violation (the detector only matches `from '<prefix>`, `require('<prefix>`, "
                           "`import('<prefix>` and `import '<prefix>'` for a prefix in forbidden_import_prefixes), or fix the rule's prefixes/scope_globs.",
                })
            if kind == "good" and hits:
                lines = ", ".join(str(h["line"]) for h in hits)
                problems.append({
                    "what": f"rule '{rule['id']}': {label} produced {len(hits)} finding(s) (line {lines}); the rule fires on code that is allowed.",
                    "fix": "Narrow forbidden_import_prefixes or scope_globs so the allowed code stays silent, or, if the example does violate the rule, move it to examples.bad.",
                })
    wrong = len(problems)
    blocking = rule.get("mode") == al.MODE_BLOCKING
    missing = [k for k, items in (("bad", bad), ("good", good)) if not items]
    if (blocking or require_both) and missing:
        why = "a blocking rule may not block until it is proven" if blocking else "a proposal must be proven"
        problems.append({
            "what": f"rule '{rule['id']}': no {' and no '.join(missing)} example; {why} to fire on a bad example and stay silent on a good one.",
            "fix": f"Add examples.{' and examples.'.join(missing)} ({{\"path\": ..., \"content\": ...}}) matching scope_globs.",
        })
    has_examples = bool(bad or good)
    status = STATUS_FAIL if (wrong or (problems and has_examples)) else (STATUS_PASS if has_examples else STATUS_NONE)
    gate_fail = bool(problems)
    return {"id": rule["id"], "mode": rule.get("mode", al.MODE_ADVISORY), "status": status, "bad": len(bad), "good": len(good),
            "problems": problems, "gate_fail": gate_fail}


def _print_results(results: List[Dict[str, object]], fmt: str, extra: dict, tail: str) -> None:
    failed = [r for r in results if r["gate_fail"]]
    if fmt == "json":
        print(json.dumps(dict(extra, results=results, summary={"rules": len(results), "failed": len(failed)}), indent=2))
        return
    for r in results:
        note = " -- blocking without proof: exit 2" if r["gate_fail"] and r["status"] == STATUS_NONE else ""
        print(f"[{r['status']}] {r['id']} (mode {r['mode']}; {r['bad']} bad, {r['good']} good example(s)){note}")
        for p in r["problems"]:
            print(f"  WHAT: {p['what']}\n  FIX: {p['fix']}")
    print(f"Arch self-test: {len(results)} rule(s), {len(failed)} failing.")
    if tail:
        print(tail)


def _error(message: str) -> int:
    print(json.dumps({"error": message}), file=sys.stderr)
    return al.EXIT_TOOL_ERROR


def self_test_main(rules_path: Path, fmt: str) -> int:
    if not rules_path.exists():
        return _error(f"no rules file at {rules_path}; --self-test never creates one and does not scan the project. "
                      "Pass --rules PATH (or --project-root/--state-dir) pointing at an existing arch-rules.json.")
    try:
        data = json.loads(rules_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
            raise al.RulesCorruptError(f"rules file at {rules_path} exists but has an invalid schema shape")
        al._validate_rules_shape(data, rules_path)
        results = [prove_rule(rule) for rule in data["rules"]]
    except (OSError, ValueError, al.RulesCorruptError) as exc:
        return _error(f"self-test cannot run: {exc}")
    if not results:
        print("Arch self-test: no rules in the registry; nothing to prove.")
        return al.EXIT_OK
    _print_results(results, fmt, {"rules_file": str(rules_path)}, "")
    return al.EXIT_BLOCKING_FINDINGS if any(r["gate_fail"] for r in results) else al.EXIT_OK


def check_proposal_main(path: Path, fmt: str) -> int:
    try:
        rule = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(rule, dict) and "rules" in rule:
            raise al.RulesCorruptError("expected ONE rule object (id, scope_globs, ...), not a registry with a 'rules' list")
        al.validate_rule(rule, 0, f"proposal {path}")
        result = prove_rule(rule, require_both=True)
    except (OSError, ValueError, al.RulesCorruptError) as exc:
        return _error(f"proposal cannot be checked: {exc}")
    ok = not result["gate_fail"]
    tail = NEXT_STEP + (BLOCKING_NOTE if result["mode"] == al.MODE_BLOCKING else "") if ok else (
        "Proposal NOT proven: fix the items above and run --check-proposal again before handing it to a human.")
    _print_results([result], fmt, {"proposal": str(path), "next_step": NEXT_STEP if ok else None}, tail)
    return al.EXIT_OK if ok else al.EXIT_BLOCKING_FINDINGS
