#!/usr/bin/env python3
"""Tests for scripts/craftflow_bite_check.py and the verifier's bite-check step (adapted from testectomy, MIT).

Each case builds a throwaway project in a temp dir: a production module, a test script that asserts on it, and a
mutation. The script must classify the run and always restore the production file byte-for-byte.

Run from anywhere: python3 plugins/craftflow/tests/fixtures/test_craftflow_bite_check.py
"""
import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "craftflow_bite_check.py"
sys.path.insert(0, str(PLUGIN / "scripts"))
bite = importlib.import_module("craftflow_bite_check")

FAILS = []
PY = sys.executable

PROD = "def total(subtotal, coupon):\n    return max(subtotal - coupon, 0)\n"
TEST = (
    "import sys\nsys.path.insert(0, '.')\nfrom pricing import total\n"
    "assert total(1000, 5000) == 0, 'SC-003: a coupon larger than the subtotal makes the order free'\n"
)
WEAK_TEST = "import sys\nsys.path.insert(0, '.')\nimport pricing\nassert pricing.total is not None\n"


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


def project(test_body=TEST):
    tmp = Path(tempfile.mkdtemp(prefix="bite-"))
    (tmp / "pricing.py").write_text(PROD, encoding="utf-8")
    (tmp / "test_pricing.py").write_text(test_body, encoding="utf-8")
    return tmp


def run(tmp, old, new, cmd=None, expect="fail", timeout=60.0, rel="pricing.py"):
    return bite.bite_check(tmp, rel, old, new, [PY, "test_pricing.py"] if cmd is None else cmd, expect, timeout)


print("[classification]")
tmp = project()
r = run(tmp, "max(subtotal - coupon, 0)", "subtotal - coupon")
check("a test that catches the mutation bites on an assertion", r["verdict"] == "bites" and r["failure_kind"] == "assertion", r)
check("the production file is restored after a bite", (tmp / "pricing.py").read_text() == PROD and r["restored"], r)

tmp = project(WEAK_TEST)
r = run(tmp, "max(subtotal - coupon, 0)", "subtotal - coupon")
check("a test that stays green survives (it guards nothing)", r["verdict"] == "survived" and r["exit"] == 0, r)

tmp = project()
r = run(tmp, "return max(subtotal - coupon, 0)", "return max(subtotal - coupon, 0")
check("a syntax error is an error, not a bite", r["verdict"] == "error" and r["failure_kind"] == "error", r)
check("the file is restored after an error", (tmp / "pricing.py").read_text() == PROD, r)

tmp = project()
SLOW_IF_BROKEN = "import sys, time; sys.path.insert(0, '.'); import pricing; time.sleep(5 if pricing.total(1000, 5000) else 0)"
r = run(tmp, "max(subtotal - coupon, 0)", "subtotal - coupon", cmd=[PY, "-c", SLOW_IF_BROKEN], timeout=2.0)
check("a timeout is an error", r["verdict"] == "error" and r["exit"] is None, r)
check("the file is restored after a timeout", (tmp / "pricing.py").read_text() == PROD, r)

tmp = project()
noisy = TEST.replace("assert total", "print('SyntaxError: shown in a log line, not raised')\nassert total")
(tmp / "test_pricing.py").write_text(noisy, encoding="utf-8")
r = run(tmp, "max(subtotal - coupon, 0)", "subtotal - coupon")
check("error words in the output make it an error without --match", r["verdict"] == "error", r)
r = bite.bite_check(tmp, "pricing.py", "max(subtotal - coupon, 0)", "subtotal - coupon", [PY, "test_pricing.py"],
                    match=r"SC-003")
check("--match on the rule id makes the same failure a bite", r["verdict"] == "bites", r)

print("[baseline]")
tmp = project()
(tmp / "pricing.py").write_text("def total(subtotal, coupon):\n    return subtotal - coupon\n", encoding="utf-8")
r = run(tmp, "return subtotal - coupon", "return subtotal + coupon")
check("refused when the test already fails on the unmutated code", r["verdict"] == "refused" and r["baseline_exit"] == 1, r)

print("[expected-failure tests bite the other way]")
tmp = project()
(tmp / "pricing.py").write_text("def total(subtotal, coupon):\n    return subtotal - coupon\n", encoding="utf-8")
r = run(tmp, "return subtotal - coupon", "return max(subtotal - coupon, 0)", expect="pass")
check("with --expect pass, a temporary fix that makes the test pass bites", r["verdict"] == "bites", r)

print("[refusals change nothing]")
tmp = project()
for label, old, new, rel in (
    ("--from not found", "no such text", "x", "pricing.py"),
    ("--from found twice", "subtotal", "x", "pricing.py"),
    ("identical --from and --to", "coupon, 0", "coupon, 0", "pricing.py"),
    ("a path outside --root", "x", "y", "../outside.py"),
    ("a missing file", "x", "y", "nope.py"),
):
    r = run(tmp, old, new, rel=rel)
    check(f"refused: {label}", r["verdict"] == "refused" and (tmp / "pricing.py").read_text() == PROD, r)
r = run(tmp, "max(subtotal - coupon, 0)", "subtotal - coupon", cmd=[])
check("refused: an empty test command", r["verdict"] == "refused", r)

print("[restore failures are loud]")
tmp = project()
check("_restore reports a digest mismatch", bite._restore(tmp / "pricing.py", PROD.encode(), "0" * 64) is False)
_real_restore = bite._restore
bite._restore = lambda *_args: False
try:
    rc = bite.main(["--root", str(tmp), "--file", "pricing.py", "--from", "max(subtotal - coupon, 0)",
                    "--to", "subtotal - coupon", "--test-cmd", f"{PY} test_pricing.py"])
finally:
    bite._restore = _real_restore
(tmp / "pricing.py").write_text(PROD, encoding="utf-8")
check("main exits 3 when the file could not be restored", rc == 3, rc)

print("[cli]")
tmp = project()
proc = subprocess.run([PY, str(SCRIPT), "--root", str(tmp), "--file", "pricing.py", "--from", "max(subtotal - coupon, 0)",
                       "--to", "subtotal - coupon", "--test-cmd", f"{PY} test_pricing.py"], capture_output=True, text=True)
lines = proc.stdout.strip().splitlines()
check("cli prints exactly one JSON line", len(lines) == 1, proc.stdout)
check("cli exits 0 on a bite", proc.returncode == 0 and json.loads(lines[0])["verdict"] == "bites", proc.stdout + proc.stderr)
proc = subprocess.run([PY, str(SCRIPT), "--root", str(tmp), "--file", "pricing.py", "--from", "nope", "--to", "x",
                       "--test-cmd", f"{PY} test_pricing.py"], capture_output=True, text=True)
check("cli exits 2 on a refusal", proc.returncode == 2, proc.stdout)

print("[verifier wiring]")
verifier = (PLUGIN / "agents/integration-verifier.md").read_text(encoding="utf-8")
check("verifier runs the bite-check script", "craftflow_bite_check.py" in verifier)
check("verifier evidence array has bite_checks rows", "bite_checks:" in verifier)
check("a new test with no bite row is a gap", "no `bite_checks` row" in verifier)

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
