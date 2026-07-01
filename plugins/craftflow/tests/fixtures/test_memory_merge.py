#!/usr/bin/env python3
"""
Fixture-based unit tests for craftflow_memory_merge.py

Run from the plugin root:
    python3 tests/fixtures/test_memory_merge.py
"""
import sys
import os

# Allow importing scripts from scripts/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../scripts"))

from craftflow_memory_merge import (
    parse_confidence,
    strip_confidence_suffix,
    merge_bullet,
    apply_retractions,
)

PASS = 0
FAIL = 0


def check(name: str, actual, expected):
    global PASS, FAIL
    if actual == expected:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        print(f"    expected: {expected!r}")
        print(f"    actual:   {actual!r}")
        FAIL += 1


# --- parse_confidence ---
print("\n[parse_confidence]")
check("parses (conf: 0.9)", parse_confidence("- insight (conf: 0.9)"), 0.9)
check("parses (conf: 0.75)", parse_confidence("- insight (conf: 0.75)"), 0.75)
check("defaults to 0.8 when absent", parse_confidence("- plain bullet"), 0.8)
check("defaults to 0.8 on empty", parse_confidence(""), 0.8)

# --- strip_confidence_suffix ---
print("\n[strip_confidence_suffix]")
check("strips (conf: 0.9)", strip_confidence_suffix("- insight (conf: 0.9)"), "- insight")
check("no-op when absent", strip_confidence_suffix("- plain bullet"), "- plain bullet")
check("handles trailing space before suffix", strip_confidence_suffix("- text (conf: 0.8)"), "- text")

# --- merge_bullet: drop low confidence ---
print("\n[merge_bullet: low confidence drop]")
result = merge_bullet([], "guess", 0.5)
check("confidence < 0.7 drops note", result, [])

result = merge_bullet(["- existing (conf: 0.9)"], "guess", 0.6)
check("confidence < 0.7 does not modify existing", result, ["- existing (conf: 0.9)"])

# --- merge_bullet: append when no match ---
print("\n[merge_bullet: append]")
result = merge_bullet([], "new insight", 0.9)
check("append to empty list", result, ["- new insight (conf: 0.9)"])

result = merge_bullet(["- other thing (conf: 0.8)"], "new insight", 0.9)
check("append when no match found", result, ["- other thing (conf: 0.8)", "- new insight (conf: 0.9)"])

# --- merge_bullet: supersede when match and new >= existing ---
print("\n[merge_bullet: supersede]")
result = merge_bullet(["- node 22 required (conf: 0.8)"], "node 22 required", 0.9)
check("supersede when new confidence >= existing", result, ["- node 22 required (conf: 0.9)"])

result = merge_bullet(["- node 22 required (conf: 0.9)"], "node 22 required", 0.7)
check("keep old when new confidence < existing", result, ["- node 22 required (conf: 0.9)"])

# Back-compat: existing bullet without (conf: x) suffix — defaults to 0.8
result = merge_bullet(["- node 22 required"], "node 22 required", 0.9)
check("supersede plain bullet (back-compat, old defaults to 0.8)", result, ["- node 22 required (conf: 0.9)"])

# --- apply_retractions ---
print("\n[apply_retractions]")
body = "- old insight (conf: 0.8)\n- keep this (conf: 0.9)"
result = apply_retractions(body, ["old insight"])
check("removes matching bullet", result, "- keep this (conf: 0.9)")

body = "- plain bullet\n- keep this"
result = apply_retractions(body, ["plain bullet"])
check("removes plain bullet (back-compat)", result, "- keep this")

body = "- keep this (conf: 0.9)"
result = apply_retractions(body, ["nonexistent"])
check("no-op when retraction not found", result, "- keep this (conf: 0.9)")

body = ""
result = apply_retractions(body, ["anything"])
check("no-op on empty section", result, "")

# --- Summary ---
print(f"\n{'='*40}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    print("FAIL")
    sys.exit(1)
else:
    print("PASS")
    sys.exit(0)
