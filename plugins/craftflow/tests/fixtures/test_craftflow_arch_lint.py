#!/usr/bin/env python3
"""Tests for craftflow_arch_lint.py.

Run: python3 tests/fixtures/test_craftflow_arch_lint.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_arch_lint.py"

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def run_cli(args: list, project_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-root", str(project_root), *args],
        capture_output=True,
        text=True,
    )


def test_missing_rules_file_self_bootstraps_and_finds_nothing_clean() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools" / "pkg").mkdir(parents=True)
        (root / "tools" / "pkg" / "clean.ts").write_text("import { x } from './local';\n")
        result = run_cli(["--format", "json"], root)
        if result.returncode != 0:
            fail("bootstrap_clean", f"exit {result.returncode}: {result.stderr}")
            return
        data = json.loads(result.stdout)
        if data["findings"] == [] and data["rule_count"] == 1:
            ok("bootstrap_clean")
        else:
            fail("bootstrap_clean", f"expected empty findings/rule_count=1, got {data}")
        if (root / ".craftflow" / "state" / "project" / "arch-rules.json").exists():
            ok("bootstrap_writes_seed_file")
        else:
            fail("bootstrap_writes_seed_file", "seed file was not created")


def test_real_violation_reports_what_why_fix() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools" / "pkg").mkdir(parents=True)
        (root / "tools" / "pkg" / "bad.ts").write_text(
            "import { helper } from '@ai-craft/agent-loop';\n"
        )
        result = run_cli(["--format", "json"], root)
        data = json.loads(result.stdout)
        if len(data["findings"]) == 1 and data["findings"][0]["rule_id"] == "no-tools-import-packages" \
           and data["findings"][0]["line"] == 1 and data["findings"][0]["what"] and data["findings"][0]["why"] and data["findings"][0]["fix"]:
            ok("real_violation_reports_what_why_fix")
        else:
            fail("real_violation_reports_what_why_fix", f"got {data}")


def test_comment_only_mention_is_not_a_false_positive() -> None:
    # Regression for the exact real-repo false positive found during PLAN
    # verification: tools/craftdeck/src/observer-bridge-core.ts:1 mentions
    # "@ai-craft/agent-observer" only in a comment, never as a real import.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools" / "pkg").mkdir(parents=True)
        (root / "tools" / "pkg" / "commented.ts").write_text(
            "// bridge -> @ai-craft/agent-observer\n"
            "// Structurally matches @ai-craft/agent-observer's shape\n"
        )
        result = run_cli(["--format", "json"], root)
        data = json.loads(result.stdout)
        if data["findings"] == []:
            ok("comment_only_mention_is_not_a_false_positive")
        else:
            fail("comment_only_mention_is_not_a_false_positive", f"expected no findings, got {data}")


def test_rule_with_misspelled_key_fails_loudly() -> None:
    # HIGH #1 regression: a rule dict with a misspelled key (scope_glob instead
    # of scope_globs) must NOT silently degrade to a no-op scan that still
    # reports rule_count=1/findings=[] -- indistinguishable from a genuinely
    # clean scan -- even though a real forbidden import exists in-scope.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools" / "pkg").mkdir(parents=True)
        (root / "tools" / "pkg" / "bad.ts").write_text(
            "import { helper } from '@ai-craft/agent-loop';\n"
        )
        rules_path = root / "custom-rules.json"
        rules_path.write_text(json.dumps({
            "schema_version": 1,
            "rules": [
                {
                    "id": "no-tools-import-packages",
                    "scope_glob": ["tools/**/*.ts"],
                    "forbidden_import_prefixes": ["@ai-craft/"],
                }
            ],
        }))
        result = run_cli(["--format", "json", "--rules", str(rules_path)], root)
        if result.returncode == 1 and "no-tools-import-packages" in result.stderr:
            ok("rule_with_misspelled_key_fails_loudly")
        else:
            fail(
                "rule_with_misspelled_key_fails_loudly",
                f"expected exit 1 naming the offending rule, got exit={result.returncode} "
                f"stdout={result.stdout!r} stderr={result.stderr!r}",
            )


def test_bare_side_effect_import_detected() -> None:
    # HIGH #2 regression: the valid bare side-effect import form
    # `import '@ai-craft/x';` (no `from` clause) must be detected, not missed.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools" / "pkg").mkdir(parents=True)
        (root / "tools" / "pkg" / "bad.ts").write_text("import '@ai-craft/agent-loop';\n")
        result = run_cli(["--format", "json"], root)
        data = json.loads(result.stdout)
        hits = [f for f in data["findings"] if f["rule_id"] == "no-tools-import-packages"]
        if len(hits) == 1 and hits[0]["line"] == 1:
            ok("bare_side_effect_import_detected")
        else:
            fail("bare_side_effect_import_detected", f"got {data}")


def test_unreadable_file_reported_in_skipped_array() -> None:
    # MEDIUM #1 regression: a file that cannot be read (permission error,
    # TOCTOU-deleted, broken symlink) must be surfaced in a "skipped" array,
    # not silently swallowed by `except OSError: continue`.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools" / "pkg").mkdir(parents=True)
        bad = root / "tools" / "pkg" / "noaccess.ts"
        bad.write_text("import { helper } from '@ai-craft/agent-loop';\n")
        bad.chmod(0o000)
        try:
            result = run_cli(["--format", "json"], root)
            data = json.loads(result.stdout)
            skipped = data.get("skipped", [])
            hits = [s for s in skipped if s.get("path", "").endswith("noaccess.ts")]
            if len(hits) == 1 and hits[0].get("reason"):
                ok("unreadable_file_reported_in_skipped_array")
            else:
                fail("unreadable_file_reported_in_skipped_array", f"got {data}")
        finally:
            bad.chmod(0o644)


def test_clean_scan_reports_empty_skipped_array() -> None:
    # Schema-completeness regression: "skipped" must always be present, even
    # on a genuinely clean scan with nothing to skip.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools" / "pkg").mkdir(parents=True)
        (root / "tools" / "pkg" / "clean.ts").write_text("import { x } from './local';\n")
        result = run_cli(["--format", "json"], root)
        data = json.loads(result.stdout)
        if data.get("skipped") == []:
            ok("clean_scan_reports_empty_skipped_array")
        else:
            fail("clean_scan_reports_empty_skipped_array", f"got {data}")


def test_corrupt_rules_json_fails_loudly() -> None:
    # MINOR (c): the pre-existing RulesCorruptError (corrupt-JSON) exit-1 path
    # had zero test coverage even though the code path exists.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rules_path = root / "corrupt-rules.json"
        rules_path.write_text("{ not valid json")
        result = run_cli(["--format", "json", "--rules", str(rules_path)], root)
        if result.returncode == 1 and "error" in (result.stderr or ""):
            ok("corrupt_rules_json_fails_loudly")
        else:
            fail(
                "corrupt_rules_json_fails_loudly",
                f"expected exit 1 with an error message, got exit={result.returncode} "
                f"stderr={result.stderr!r}",
            )


def _write_rules(root: Path, rules: list) -> Path:
    rules_path = root / "custom-rules.json"
    rules_path.write_text(json.dumps({"schema_version": 1, "rules": rules}))
    return rules_path


def _rule(**extra) -> dict:
    rule = {
        "id": "no-tools-import-packages",
        "scope_globs": ["tools/**/*.ts"],
        "forbidden_import_prefixes": ["@ai-craft/"],
        "what": "imports from @ai-craft/*",
        "why": "boundary",
        "fix": "move it",
    }
    rule.update(extra)
    return rule


def _violating_root(root: Path) -> None:
    (root / "tools" / "pkg").mkdir(parents=True)
    (root / "tools" / "pkg" / "bad.ts").write_text("import { helper } from '@ai-craft/agent-loop';\n")


def test_advisory_only_findings_exit_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _violating_root(root)
        rules = _write_rules(root, [_rule(mode="advisory")])
        result = run_cli(["--format", "json", "--rules", str(rules)], root)
        data = json.loads(result.stdout) if result.stdout else {}
        if (
            result.returncode == 0
            and len(data.get("findings", [])) == 1
            and data["findings"][0].get("mode") == "advisory"
            and data.get("summary") == {"total": 1, "blocking": 0, "advisory": 1}
        ):
            ok("advisory_only_findings_exit_zero")
        else:
            fail("advisory_only_findings_exit_zero", f"exit={result.returncode} out={result.stdout!r} err={result.stderr!r}")


def test_blocking_finding_exits_two_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _violating_root(root)
        rules = _write_rules(root, [_rule(mode="blocking")])
        result = run_cli(["--format", "json", "--rules", str(rules)], root)
        data = json.loads(result.stdout) if result.stdout else {}
        if (
            result.returncode == 2
            and len(data.get("findings", [])) == 1
            and data["findings"][0].get("mode") == "blocking"
            and data.get("summary") == {"total": 1, "blocking": 1, "advisory": 0}
        ):
            ok("blocking_finding_exits_two_json")
        else:
            fail("blocking_finding_exits_two_json", f"exit={result.returncode} out={result.stdout!r} err={result.stderr!r}")


def test_blocking_finding_exits_two_text_shows_mode_and_counts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _violating_root(root)
        rules = _write_rules(root, [
            _rule(mode="blocking"),
            _rule(id="advisory-too", mode="advisory"),
        ])
        result = run_cli(["--format", "text", "--rules", str(rules)], root)
        out = result.stdout
        if (
            result.returncode == 2
            and "MODE: blocking" in out
            and "MODE: advisory" in out
            and "2 finding(s): 1 blocking, 1 advisory" in out
        ):
            ok("blocking_finding_exits_two_text_shows_mode_and_counts")
        else:
            fail("blocking_finding_exits_two_text_shows_mode_and_counts", f"exit={result.returncode} out={out!r}")


def test_mixed_rules_advisory_does_not_mask_blocking() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _violating_root(root)
        rules = _write_rules(root, [_rule(id="a", mode="advisory"), _rule(id="b", mode="blocking")])
        result = run_cli(["--format", "json", "--rules", str(rules)], root)
        data = json.loads(result.stdout) if result.stdout else {}
        if result.returncode == 2 and data.get("summary") == {"total": 2, "blocking": 1, "advisory": 1}:
            ok("mixed_rules_advisory_does_not_mask_blocking")
        else:
            fail("mixed_rules_advisory_does_not_mask_blocking", f"exit={result.returncode} out={result.stdout!r}")


def test_blocking_rule_without_findings_exits_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools" / "pkg").mkdir(parents=True)
        (root / "tools" / "pkg" / "clean.ts").write_text("import { x } from './local';\n")
        rules = _write_rules(root, [_rule(mode="blocking")])
        result = run_cli(["--format", "json", "--rules", str(rules)], root)
        if result.returncode == 0 and json.loads(result.stdout)["findings"] == []:
            ok("blocking_rule_without_findings_exits_zero")
        else:
            fail("blocking_rule_without_findings_exits_zero", f"exit={result.returncode} out={result.stdout!r}")


def test_invalid_mode_is_a_schema_error() -> None:
    # Misspelled, wrong-case and non-string modes must all fail loudly (exit 1,
    # naming the rule and the bad value) rather than degrade to advisory.
    for label, bad in (("typo", "blocker"), ("case", "Blocking"), ("empty", ""), ("null", None), ("number", 2)):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _violating_root(root)
            rules = _write_rules(root, [_rule(mode=bad)])
            result = run_cli(["--format", "json", "--rules", str(rules)], root)
            if (
                result.returncode == 1
                and result.stdout == ""
                and "no-tools-import-packages" in result.stderr
                and "mode" in result.stderr
            ):
                ok(f"invalid_mode_is_a_schema_error[{label}]")
            else:
                fail(
                    f"invalid_mode_is_a_schema_error[{label}]",
                    f"exit={result.returncode} out={result.stdout!r} err={result.stderr!r}",
                )


def test_missing_mode_defaults_to_advisory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _violating_root(root)
        rules = _write_rules(root, [_rule()])  # no "mode" key at all (pre-existing registry shape)
        result = run_cli(["--format", "json", "--rules", str(rules)], root)
        data = json.loads(result.stdout) if result.stdout else {}
        if (
            result.returncode == 0
            and len(data.get("findings", [])) == 1
            and data["findings"][0].get("mode") == "advisory"
        ):
            ok("missing_mode_defaults_to_advisory")
        else:
            fail("missing_mode_defaults_to_advisory", f"exit={result.returncode} out={result.stdout!r} err={result.stderr!r}")


def test_seed_rule_is_advisory_and_seeded_violation_exits_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _violating_root(root)
        result = run_cli(["--format", "json"], root)
        seeded = json.loads((root / ".craftflow" / "state" / "project" / "arch-rules.json").read_text())
        seed_modes = {r.get("mode", "advisory") for r in seeded["rules"]}
        if result.returncode == 0 and seed_modes == {"advisory"}:
            ok("seed_rule_is_advisory_and_seeded_violation_exits_zero")
        else:
            fail("seed_rule_is_advisory_and_seeded_violation_exits_zero", f"exit={result.returncode} modes={seed_modes}")


def test_bootstrap_is_surfaced_so_a_deleted_registry_is_visible() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        first = json.loads(run_cli(["--format", "json"], root).stdout)
        second = json.loads(run_cli(["--format", "json"], root).stdout)
        text = run_cli(["--format", "text"], root)
        (root / ".craftflow" / "state" / "project" / "arch-rules.json").unlink()
        text_after_delete = run_cli(["--format", "text"], root)
        if (
            first.get("bootstrapped") is True
            and second.get("bootstrapped") is False
            and "was missing" not in text.stdout
            and "was missing" in text_after_delete.stdout
        ):
            ok("bootstrap_is_surfaced_so_a_deleted_registry_is_visible")
        else:
            fail("bootstrap_is_surfaced_so_a_deleted_registry_is_visible", f"{first} {second} {text_after_delete.stdout!r}")


def test_usage_error_is_exit_one_not_two() -> None:
    # argparse's own usage-error exit is 2, which would be indistinguishable
    # from "blocking findings present".
    with tempfile.TemporaryDirectory() as tmp:
        result = run_cli(["--format", "xml"], Path(tmp))
        if result.returncode == 1 and "usage error" in result.stderr:
            ok("usage_error_is_exit_one_not_two")
        else:
            fail("usage_error_is_exit_one_not_two", f"exit={result.returncode} err={result.stderr!r}")


def main() -> int:
    print("test_craftflow_arch_lint: running")
    test_missing_rules_file_self_bootstraps_and_finds_nothing_clean()
    test_real_violation_reports_what_why_fix()
    test_comment_only_mention_is_not_a_false_positive()
    test_rule_with_misspelled_key_fails_loudly()
    test_bare_side_effect_import_detected()
    test_unreadable_file_reported_in_skipped_array()
    test_clean_scan_reports_empty_skipped_array()
    test_corrupt_rules_json_fails_loudly()
    test_advisory_only_findings_exit_zero()
    test_blocking_finding_exits_two_json()
    test_blocking_finding_exits_two_text_shows_mode_and_counts()
    test_mixed_rules_advisory_does_not_mask_blocking()
    test_blocking_rule_without_findings_exits_zero()
    test_invalid_mode_is_a_schema_error()
    test_missing_mode_defaults_to_advisory()
    test_seed_rule_is_advisory_and_seeded_violation_exits_zero()
    test_bootstrap_is_surfaced_so_a_deleted_registry_is_visible()
    test_usage_error_is_exit_one_not_two()
    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
