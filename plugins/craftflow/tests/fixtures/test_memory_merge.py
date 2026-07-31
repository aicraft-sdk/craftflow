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
    apply_cap,
    merge_section_anchored,
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

# --- merge_bullet: already-suffixed note must not double-suffix (bug fix) ---
print("\n[merge_bullet: already-suffixed note matches existing]")
result = merge_bullet(
    ["- long insight (conf: 0.9)"], "long insight (conf: 0.9)", 0.9
)
check(
    "note text already ending in (conf: 0.9) matches existing bullet, one suffix only",
    result,
    ["- long insight (conf: 0.9)"],
)

result = merge_bullet(
    ["- node 22 required (conf: 0.8)"], "node 22 required (conf: 0.9)", 0.9
)
check(
    "already-suffixed new note supersedes existing with exactly one suffix",
    result,
    ["- node 22 required (conf: 0.9)"],
)

# --- apply_cap ---
print("\n[apply_cap]")
bullets = ["- one (conf: 0.8)", "- two (conf: 0.8)", "- three (conf: 0.8)"]
check("no-op when under cap", apply_cap(bullets, 5), bullets)
check(
    "evicts oldest first when over cap",
    apply_cap(bullets, 2),
    ["- two (conf: 0.8)", "- three (conf: 0.8)"],
)
check(
    "evicts all excess down to 1, oldest first",
    apply_cap(bullets, 1),
    ["- three (conf: 0.8)"],
)
check("no-op when max_bullets is None", apply_cap(bullets, None), bullets)

# --- merge_section_anchored: no relocation past next heading (root-cause regression) ---
print("\n[merge_section_anchored: no relocation past next heading]")
file_text_a = (
    "## Common Gotchas\n"
    "- old gotcha (conf: 0.8)\n"
    "## Last Updated\n"
    "2026-07-30\n"
)
result = merge_section_anchored(
    file_text_a, "Common Gotchas", [{"text": "new gotcha", "confidence": 0.9}]
)
expected_a = (
    "## Common Gotchas\n"
    "- old gotcha (conf: 0.8)\n"
    "- new gotcha (conf: 0.9)\n"
    "## Last Updated\n"
    "2026-07-30\n"
)
check(
    "over-wide span never relocates merged bullets past the second heading",
    result,
    expected_a,
)
after_heading = result.split("## Last Updated\n", 1)[1]
check(
    "content after Last Updated heading is byte-identical, no bullets landed there",
    after_heading,
    "2026-07-30\n",
)

# --- merge_section_anchored: full file returned, only target section body replaced ---
print("\n[merge_section_anchored: full file replace, other sections untouched]")
file_text_b = (
    "## A\n"
    "- a1 (conf: 0.8)\n"
    "## B\n"
    "- b1 (conf: 0.8)\n"
    "## C\n"
    "- c1 (conf: 0.8)\n"
)
result = merge_section_anchored(
    file_text_b, "B", [{"text": "b2", "confidence": 0.9}]
)
expected_b = (
    "## A\n"
    "- a1 (conf: 0.8)\n"
    "## B\n"
    "- b1 (conf: 0.8)\n"
    "- b2 (conf: 0.9)\n"
    "## C\n"
    "- c1 (conf: 0.8)\n"
)
check(
    "full file text returned with only section B's body replaced",
    result,
    expected_b,
)

# --- merge_section_anchored: cap enforcement preserves non-bullet lines ---
print("\n[merge_section_anchored: cap enforcement preserves non-bullet lines]")
file_text_cap = (
    "## Notes\n"
    "Some preamble line.\n"
    "- one (conf: 0.8)\n"
    "- two (conf: 0.8)\n"
    "- three (conf: 0.8)\n"
    "## Other\n"
    "end\n"
)
result = merge_section_anchored(file_text_cap, "Notes", [], max_bullets=2)
expected_cap = (
    "## Notes\n"
    "Some preamble line.\n"
    "\n"
    "- two (conf: 0.8)\n"
    "- three (conf: 0.8)\n"
    "## Other\n"
    "end\n"
)
check(
    "cap trims oldest bullet, preserves non-bullet lines and other sections",
    result,
    expected_cap,
)

# --- Summary ---
print(f"\n{'='*40}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    print("FAIL")
    sys.exit(1)
else:
    print("PASS")
    sys.exit(0)
