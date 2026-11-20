#!/usr/bin/env python3
"""Bite check: prove a test can fail on the bug its rule describes (adapted from testectomy, MIT; see NOTICE).

First runs the test command on the unmutated code: it must pass (with --expect pass: fail), or the check is refused.
Then applies ONE textual mutation to ONE production file (the `--from` text must occur exactly once), runs the test
command again, restores the file byte-for-byte, and classifies the run:

    bites     the test failed on an assertion (or, with --expect pass, passed: an expected-failure test bit back)
    survived  the test still passed (or, with --expect pass, still failed): it guards nothing for this mutation
    error     the run failed for another reason (import/syntax/collection error, timeout): proves nothing.
              A failure whose output matches --match REGEX (the rule id or the expected message) is an assertion.
    refused   bad arguments, a path outside --root, or --from not found exactly once; nothing was changed

The file is always restored, also on a timeout or an exception, and `restored` reports a SHA-256 comparison of the
bytes before and after. A failed restore is reported as `refused` with `restored: false` and exit 3, so a caller never
mistakes a dirty tree for a result.

Usage:
    craftflow_bite_check.py --root DIR --file REL_PATH --from TEXT --to TEXT --test-cmd "CMD ..." [--expect fail|pass]
                            [--timeout SECONDS] [--match REGEX]

For a strict expected-failure test, the mutation is a temporary fix and the command must run the test with its marker
disabled (for example `pytest --runxfail`), so the fixed code passes: use --expect pass.

The test command is split with shlex and run without a shell, with --root as the working directory. Prints exactly one
JSON line. Exit codes: 0 bites, 1 survived or error, 2 refused, 3 restore failed. Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

EXIT_BITES, EXIT_NO_BITE, EXIT_REFUSED, EXIT_RESTORE_FAILED = 0, 1, 2, 3
OUTPUT_TAIL = 1200

# A run that fails for one of these reasons never reached an assertion, so it proves nothing about the test.
ERROR_PATTERNS = [
    r"\bSyntaxError\b", r"\bIndentationError\b", r"\bImportError\b", r"\bModuleNotFoundError\b",
    r"\bNameError\b", r"Cannot find module", r"SyntaxError:", r"error during collection", r"errors? during collection",
    r"\bReferenceError\b", r"TypeScript error", r"\bcompil(?:e|ation) (?:error|failed)", r"undefined: ",
]
ASSERTION_PATTERNS = [
    r"\bAssertionError\b", r"\bassert(?:ion)?\b", r"\bexpect(?:ed)?\b", r"\bFAILED\b", r"\bFAIL\b", r"toBe|toEqual",
    r"--- FAIL", r"\bassert_eq!", r"panicked at",
]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _classify_failure(output: str, match: Optional[str] = None) -> str:
    if match and re.search(match, output):
        return "assertion"
    if any(re.search(p, output) for p in ERROR_PATTERNS):
        return "error"
    if any(re.search(p, output, re.IGNORECASE) for p in ASSERTION_PATTERNS):
        return "assertion"
    return "error"


def _verdict(exit_code: Optional[int], failure_kind: str, expect: str) -> str:
    if exit_code is None:
        return "error"
    if expect == "pass":
        return "bites" if exit_code == 0 else "survived"
    if exit_code == 0:
        return "survived"
    return "bites" if failure_kind == "assertion" else "error"


def _resolve_target(root: Path, rel: str) -> Optional[Path]:
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target if target.is_file() else None


def _restore(target: Path, original: bytes, digest: str) -> bool:
    try:
        target.write_bytes(original)
        return _sha(target.read_bytes()) == digest
    except OSError:
        return False


def _run(cmd: List[str], cwd: Path, timeout: float) -> Dict[str, object]:
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"exit": None, "output": f"timeout after {timeout}s"}
    except OSError as exc:
        return {"exit": None, "output": f"could not start: {exc}"}
    return {"exit": proc.returncode, "output": (proc.stdout or "") + (proc.stderr or "")}


def _precheck(root: Path, rel: str, old: str, new: str, cmd: List[str]) -> "tuple[Optional[Path], str]":
    target = _resolve_target(root, rel) if root.is_dir() else None
    if target is None:
        return None, "file is missing or outside --root"
    if not cmd:
        return None, "empty --test-cmd"
    if old == new:
        return None, "--from and --to are identical, so nothing would be mutated"
    count = target.read_bytes().decode("utf-8").count(old)
    if count != 1:
        return None, f"--from occurs {count} times in the file; it must occur exactly once"
    return target, ""


def bite_check(root: Path, rel: str, old: str, new: str, cmd: List[str], expect: str = "fail",
               timeout: float = 120.0, match: Optional[str] = None) -> Dict[str, object]:
    report: Dict[str, object] = {"file": rel, "from": old, "to": new, "test_cmd": cmd, "expect": expect,
                                 "baseline_exit": None, "exit": None, "failure_kind": None, "verdict": "refused",
                                 "restored": True, "reason": "", "output_tail": ""}
    root = root.resolve()
    target, reason = _precheck(root, rel, old, new, cmd)
    if target is None:
        report["reason"] = reason
        return report
    baseline = _run(cmd, root, timeout)
    report["baseline_exit"] = baseline["exit"]
    if (baseline["exit"] == 0) != (expect == "fail"):
        report["reason"] = ("the test does not pass on the unmutated code" if expect == "fail"
                            else "the test does not fail on the unmutated code")
        report["output_tail"] = str(baseline["output"])[-OUTPUT_TAIL:]
        return report
    original = target.read_bytes()
    before = _sha(original)
    result: Dict[str, object] = {"exit": None, "output": "the mutation could not be written"}
    try:
        target.write_bytes(original.decode("utf-8").replace(old, new, 1).encode("utf-8"))
        result = _run(cmd, root, timeout)
    finally:
        report["restored"] = _restore(target, original, before)
    if not report["restored"]:
        report["reason"] = "the production file could not be restored; fix it by hand before anything else"
        return report
    output, exit_code = str(result["output"]), result["exit"]
    failure_kind = None if exit_code == 0 else ("error" if exit_code is None else _classify_failure(output, match))
    report.update({"exit": exit_code, "failure_kind": failure_kind,
                   "verdict": _verdict(exit_code, failure_kind or "", expect), "output_tail": output[-OUTPUT_TAIL:]})
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Mutate one production file, run one test, restore, classify.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--file", required=True, help="production file, relative to --root")
    parser.add_argument("--from", dest="old", required=True)
    parser.add_argument("--to", dest="new", required=True)
    parser.add_argument("--test-cmd", required=True)
    parser.add_argument("--expect", choices=["fail", "pass"], default="fail")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--match", help="regex for the expected failure message; a match counts as an assertion")
    args = parser.parse_args(argv)
    report = bite_check(Path(args.root), args.file, args.old, args.new, shlex.split(args.test_cmd), args.expect,
                        args.timeout, args.match)
    print(json.dumps(report, sort_keys=True))
    if not report["restored"]:
        return EXIT_RESTORE_FAILED
    if report["verdict"] == "refused":
        return EXIT_REFUSED
    return EXIT_BITES if report["verdict"] == "bites" else EXIT_NO_BITE


if __name__ == "__main__":
    sys.exit(main())
