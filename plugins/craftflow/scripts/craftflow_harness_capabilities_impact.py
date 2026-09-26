#!/usr/bin/env python3
"""
craftflow_harness_capabilities_impact.py

Before/after impact comparison for the 3 harness capabilities shipped on main
in commit 519718a5:
    - craftflow_arch_lint.py           (advisory import-boundary linter)
    - craftflow_clean_state_check.py   (advisory diff-scoped debug-artifact scanner)
    - craftflow_feature_backlog.py     (durable feature backlog + VCR metric)

This script does NOT re-implement or re-test any of the three tools' own
detection logic -- that is already covered by their own dedicated fixture
test files (test_craftflow_arch_lint.py, test_craftflow_clean_state_check.py,
test_craftflow_feature_backlog.py). It imports the real shipped functions
(`lint`, `scan`, `_compute_vcr`) directly and runs them against a small set
of representative fixtures built inline in this file (never the actual
tests/fixtures/ files, which are test-scoped, not benchmark-scoped).

Honesty disclosure (read before trusting any number below): none of these 3
capabilities existed in any form before this BUILD. There is no real legacy
tool to benchmark against, so every "old path" below is an honestly-labeled
status-quo PROXY, not a resurrection of real prior code:

  - arch-lint:        old path = no automated import-direction check of any
                       kind existed. The proxy always reports zero
                       violations regardless of input.
  - clean-state-check: old path = no automated scan existed; manual code
                       review before memory-finalize was the only prior
                       mechanism, and a human reviewer's detection rate
                       cannot be deterministically simulated. The proxy
                       always reports zero findings regardless of input --
                       this is disclosed explicitly rather than fabricating
                       a fake manual-review detection rate.
  - feature-backlog:  old path = no backlog file and no VCR metric existed
                       at all. There is no numeric detection-rate delta to
                       compute for this capability -- only a boolean
                       capability-existed-before (False) vs
                       capability-exists-now (True) comparison, plus
                       real-tool correctness checks on representative
                       backlog states.

Usage:
    python3 craftflow_harness_capabilities_impact.py

Writes docs/benchmarks/2026-09-26-harness-capabilities-impact.md (repo root)
and prints the same summary to stdout. Always exits 0 -- this is a reporting
script, not a pass/fail gate, matching every other script in this family
(craftflow_contract_validate_impact.py, craftflow_reference_benchmark.py,
craftflow_worldclass_benchmark.py).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from craftflow_arch_lint import lint  # noqa: E402
from craftflow_clean_state_check import scan  # noqa: E402
from craftflow_feature_backlog import _compute_vcr  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parents[4]
REPORT_PATH = REPO_ROOT / "docs" / "benchmarks" / "2026-09-26-harness-capabilities-impact.md"


# ---------------------------------------------------------------------------
# Arch lint
# ---------------------------------------------------------------------------
ARCH_LINT_HONESTY = (
    "No automated import-direction/architecture-boundary check existed "
    "before this BUILD. Boundaries such as 'tools/ must never import "
    "@ai-craft/*' were enforced only informally (code review by eye, if at "
    "all). `arch_lint_old_path_no_check` below is an honest proxy for that "
    "status quo: it always reports zero violations regardless of input, "
    "because there was no automated check of any kind to run -- not a naive "
    "partial check, an absence of one. Every 'old path' result for this "
    "capability is a simulation of 'no tooling existed', not a bug found in "
    "real legacy code."
)

ARCH_LINT_REGISTRY = {
    "schema_version": 1,
    "rules": [
        {
            "id": "no-tools-import-packages",
            "scope_globs": ["tools/**/*.ts"],
            "forbidden_import_prefixes": ["@ai-craft/"],
            "what": "imports from @ai-craft/* inside tools/",
            "why": "tools/ is internal-only; packages/ is the publishable surface.",
            "fix": "move shared logic into a package under packages/.",
        }
    ],
}

ARCH_LINT_FIXTURES: List[dict] = [
    {
        "name": "arch-lint-clean-local-import",
        "kind": "clean",
        "files": {"tools/pkg/clean.ts": "import { x } from './local';\n"},
    },
    {
        "name": "arch-lint-clean-comment-only-mention",
        "kind": "clean",
        "files": {"tools/pkg/commented.ts": "// see @ai-craft/agent-observer for context\n"},
    },
    {
        "name": "arch-lint-violation-named-import",
        "kind": "violation",
        "files": {"tools/pkg/bad.ts": "import { helper } from '@ai-craft/agent-loop';\n"},
    },
    {
        "name": "arch-lint-violation-bare-side-effect-import",
        "kind": "violation",
        "files": {"tools/pkg/bad2.ts": "import '@ai-craft/agent-loop';\n"},
    },
]


def arch_lint_old_path_no_check(project_root: Path, registry: dict) -> dict:
    """Honest pre-BUILD status-quo proxy for arch-lint: no automated
    import-direction check existed at all, so this always reports a clean
    scan regardless of input. See ARCH_LINT_HONESTY."""
    return {"findings": [], "reason": "no automated check existed before this BUILD"}


def _write_files(root: Path, files: Dict[str, str]) -> None:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def run_arch_lint_comparison() -> List[dict]:
    rows = []
    for fx in ARCH_LINT_FIXTURES:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_files(root, fx["files"])
            findings, _skipped = lint(root, ARCH_LINT_REGISTRY)
            old_result = arch_lint_old_path_no_check(root, ARCH_LINT_REGISTRY)
            rows.append({
                "name": fx["name"],
                "kind": fx["kind"],
                "new_detected": len(findings) > 0,
                "old_detected": len(old_result["findings"]) > 0,
            })
    return rows


# ---------------------------------------------------------------------------
# Clean-state check
# ---------------------------------------------------------------------------
CLEAN_STATE_HONESTY = (
    "No automated diff-scoped debug-artifact scan existed before this BUILD. "
    "The only prior mechanism was manual human review of a diff before "
    "memory-finalize, which cannot be deterministically simulated -- there "
    "is no fixed 'manual reviewer' detection rate to reproduce here. "
    "`clean_state_old_path_no_check` below is an honest proxy that always "
    "reports zero findings regardless of input, disclosing that gap rather "
    "than fabricating a fake manual-review detection rate. Do not read the "
    "old-path numbers below as a measured human baseline."
)

CLEAN_STATE_FIXTURES: List[dict] = [
    {
        "name": "clean-state-clean-innocuous-addition",
        "kind": "clean",
        "append": "export const extra = 2;\n",
    },
    {
        "name": "clean-state-violation-console-log",
        "kind": "violation",
        "append": "console.log('debug');\n",
    },
    {
        "name": "clean-state-violation-debugger",
        "kind": "violation",
        "append": "debugger;\n",
    },
    {
        "name": "clean-state-violation-todo-without-ticket",
        "kind": "violation",
        "append": "// TODO fix this later\n",
    },
]


def clean_state_old_path_no_check(project_root: Path) -> dict:
    """Honest pre-BUILD status-quo proxy for clean-state-check: no automated
    scan existed; manual review was the only mechanism and cannot be
    deterministically simulated. Always reports zero findings regardless of
    input. See CLEAN_STATE_HONESTY."""
    return {
        "findings": [],
        "reason": "no automated scan existed; manual review cannot be deterministically simulated",
    }


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "benchmark@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Benchmark"], cwd=root, check=True)
    (root / "base.ts").write_text("export const base = 1;\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)


def run_clean_state_comparison() -> List[dict]:
    rows = []
    for fx in CLEAN_STATE_FIXTURES:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)
            with (root / "base.ts").open("a", encoding="utf-8") as f:
                f.write(fx["append"])
            findings, _skipped = scan(root)
            old_result = clean_state_old_path_no_check(root)
            rows.append({
                "name": fx["name"],
                "kind": fx["kind"],
                "new_detected": len(findings) > 0,
                "old_detected": len(old_result["findings"]) > 0,
            })
    return rows


def build_binary_summary(rows: List[dict]) -> dict:
    """Shared summary builder for arch-lint and clean-state-check: both have
    a 'does the real tool detect a known violation / correctly report clean'
    shape, contrasted against an old path that never existed at all."""
    violation_rows = [r for r in rows if r["kind"] == "violation"]
    clean_rows = [r for r in rows if r["kind"] == "clean"]
    violations_total = len(violation_rows)
    new_detected = sum(1 for r in violation_rows if r["new_detected"])
    new_false_positives = sum(1 for r in clean_rows if r["new_detected"])
    old_detected = sum(1 for r in violation_rows if r["old_detected"])
    return {
        "total": len(rows),
        "violations_total": violations_total,
        "clean_total": len(clean_rows),
        "new_detected": new_detected,
        "new_false_positives": new_false_positives,
        "old_detected": old_detected,
        "old_missed": violations_total - old_detected,
    }


# ---------------------------------------------------------------------------
# Feature backlog + VCR
# ---------------------------------------------------------------------------
FEATURE_BACKLOG_HONESTY = (
    "No feature-backlog file and no VCR (Verified Completion Rate) metric "
    "existed before this BUILD -- there was no prior mechanism to compare "
    "against at all, numeric or informal. This capability's 'old path' is a "
    "boolean presence/absence check (no backlog visibility existed before), "
    "not a numeric detection-rate delta. The rows below instead confirm the "
    "real, shipped VCR computation (`_compute_vcr`) produces the correct "
    "result on representative backlog states."
)

FEATURE_BACKLOG_FIXTURES: List[dict] = [
    {
        "name": "feature-backlog-empty",
        "features": [],
        "expected_vcr": {"passing": 0, "activated": 0, "ratio": None, "display": "N/A (no activated features yet)"},
    },
    {
        "name": "feature-backlog-all-not-started",
        "features": [
            {"id": "f1", "status": "not_started"},
            {"id": "f2", "status": "not_started"},
        ],
        "expected_vcr": {"passing": 0, "activated": 0, "ratio": None, "display": "N/A (no activated features yet)"},
    },
    {
        "name": "feature-backlog-mixed-active-passing",
        "features": [
            {"id": "f1", "status": "passing"},
            {"id": "f2", "status": "active"},
            {"id": "f3", "status": "not_started"},
        ],
        "expected_vcr": {"passing": 1, "activated": 2, "ratio": 0.5, "display": "1/2"},
    },
    {
        "name": "feature-backlog-all-passing",
        "features": [
            {"id": "f1", "status": "passing"},
            {"id": "f2", "status": "passing"},
        ],
        "expected_vcr": {"passing": 2, "activated": 2, "ratio": 1.0, "display": "2/2"},
    },
]


def run_feature_backlog_comparison() -> List[dict]:
    rows = []
    for fx in FEATURE_BACKLOG_FIXTURES:
        actual_vcr = _compute_vcr(fx["features"])
        rows.append({
            "name": fx["name"],
            "actual_vcr_display": actual_vcr["display"],
            "expected_vcr_display": fx["expected_vcr"]["display"],
            "correct": actual_vcr == fx["expected_vcr"],
            "capability_existed_before": False,
            "capability_exists_now": True,
        })
    return rows


def build_backlog_summary(rows: List[dict]) -> dict:
    return {
        "total": len(rows),
        "correct": sum(1 for r in rows if r["correct"]),
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------
def _binary_table(rows: List[dict]) -> List[str]:
    lines = [
        "| Fixture | Kind | New Path (real tool) | Old Path (honest proxy) |",
        "|---|---|---|---|",
    ]
    for r in rows:
        new_label = "detected" if r["new_detected"] else "clean"
        old_label = "detected" if r["old_detected"] else "clean"
        lines.append(f"| {r['name']} | {r['kind']} | {new_label} | {old_label} |")
    return lines


def render_report(
    arch_rows: List[dict], arch_summary: dict,
    clean_rows: List[dict], clean_summary: dict,
    backlog_rows: List[dict], backlog_summary: dict,
) -> str:
    lines = [
        "# Harness Capabilities Impact: Before/After the 3 Shipped Capabilities",
        "",
        "**Date:** 2026-09-26",
        "**Shipped commit:** `519718a5` (craftflow_arch_lint.py, "
        "craftflow_clean_state_check.py, craftflow_feature_backlog.py)",
        "",
        "None of these 3 capabilities existed in any form before this BUILD. "
        "Each section below discloses honestly what its 'old path' proxy is "
        "and is not -- read the disclosure before the numbers.",
        "",
        "## Arch Lint",
        "",
        ARCH_LINT_HONESTY,
        "",
        *_binary_table(arch_rows),
        "",
        f"- Total fixtures: {arch_summary['total']} "
        f"({arch_summary['violations_total']} violation, {arch_summary['clean_total']} clean)",
        f"- Correctly detected by the new tool (real, shipped): "
        f"{arch_summary['new_detected']} / {arch_summary['violations_total']}",
        f"- False positives on clean fixtures: {arch_summary['new_false_positives']} / {arch_summary['clean_total']}",
        f"- Missed by the old-path proxy (no tooling existed): "
        f"{arch_summary['old_missed']} / {arch_summary['violations_total']} "
        "(expected: all of them, since no automated check ever ran)",
        "",
        "## Clean-State Check",
        "",
        CLEAN_STATE_HONESTY,
        "",
        *_binary_table(clean_rows),
        "",
        f"- Total fixtures: {clean_summary['total']} "
        f"({clean_summary['violations_total']} violation, {clean_summary['clean_total']} clean)",
        f"- Correctly detected by the new tool (real, shipped): "
        f"{clean_summary['new_detected']} / {clean_summary['violations_total']}",
        f"- False positives on clean fixtures: {clean_summary['new_false_positives']} / {clean_summary['clean_total']}",
        f"- Missed by the old-path proxy (no tooling existed; manual review "
        f"not simulated): {clean_summary['old_missed']} / {clean_summary['violations_total']} "
        "(expected: all of them -- see honesty disclosure above)",
        "",
        "## Feature Backlog + VCR",
        "",
        FEATURE_BACKLOG_HONESTY,
        "",
        "| Fixture | Real VCR (shipped `_compute_vcr`) | Expected | Capability Existed Before? | Capability Exists Now? |",
        "|---|---|---|---|---|",
    ]
    for r in backlog_rows:
        match = "match" if r["correct"] else "MISMATCH"
        lines.append(
            f"| {r['name']} | {r['actual_vcr_display']} | {r['expected_vcr_display']} ({match}) | "
            f"{r['capability_existed_before']} | {r['capability_exists_now']} |"
        )
    lines.extend([
        "",
        f"- Total fixtures: {backlog_summary['total']}",
        f"- Real VCR computation correct on representative states: "
        f"{backlog_summary['correct']} / {backlog_summary['total']}",
        "- No numeric old-path comparison is meaningful for this capability "
        "(see honesty disclosure above): before this BUILD there was no "
        "backlog visibility and no VCR metric at all, i.e. "
        "capability-existed-before=False for every row.",
        "",
        "## Summary",
        "",
        f"- Arch lint: {arch_summary['new_detected']}/{arch_summary['violations_total']} "
        f"violations caught by the real tool; old path (no tooling) misses "
        f"{arch_summary['old_missed']}/{arch_summary['violations_total']}.",
        f"- Clean-state check: {clean_summary['new_detected']}/{clean_summary['violations_total']} "
        f"violations caught by the real tool; old path (no tooling, manual "
        f"review not simulated) misses {clean_summary['old_missed']}/{clean_summary['violations_total']}.",
        f"- Feature backlog + VCR: capability did not exist before this BUILD "
        f"(boolean presence/absence comparison only); real VCR computation "
        f"correct on {backlog_summary['correct']}/{backlog_summary['total']} representative states.",
        "",
        "**Reproducing this report:** "
        "`python3 scripts/craftflow_harness_capabilities_impact.py` "
        "(run from `tools/craftflow-plugin/plugins/craftflow/`).",
        "",
    ])
    return "\n".join(lines)


def print_summary(arch_summary: dict, clean_summary: dict, backlog_summary: dict) -> None:
    print("Harness capabilities impact summary:")
    print(
        f"  arch-lint:          {arch_summary['new_detected']}/{arch_summary['violations_total']} "
        f"caught, old-path misses {arch_summary['old_missed']}/{arch_summary['violations_total']}"
    )
    print(
        f"  clean-state-check:  {clean_summary['new_detected']}/{clean_summary['violations_total']} "
        f"caught, old-path misses {clean_summary['old_missed']}/{clean_summary['violations_total']}"
    )
    print(
        f"  feature-backlog:    {backlog_summary['correct']}/{backlog_summary['total']} VCR states correct "
        "(no old-path metric existed)"
    )


def main() -> int:
    arch_rows = run_arch_lint_comparison()
    arch_summary = build_binary_summary(arch_rows)

    clean_rows = run_clean_state_comparison()
    clean_summary = build_binary_summary(clean_rows)

    backlog_rows = run_feature_backlog_comparison()
    backlog_summary = build_backlog_summary(backlog_rows)

    report_text = render_report(
        arch_rows, arch_summary,
        clean_rows, clean_summary,
        backlog_rows, backlog_summary,
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    print_summary(arch_summary, clean_summary, backlog_summary)
    print(f"Report written to: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
