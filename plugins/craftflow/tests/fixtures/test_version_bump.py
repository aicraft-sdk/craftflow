#!/usr/bin/env python3
"""
Fixture-based unit tests for craftflow_version_bump.py (pure core only —
classify_subject, max_bump, next_version, parse_semver, group_commits).

Run from the plugin root:
    python3 tests/fixtures/test_version_bump.py
"""
import itertools
import os
import sys

# Allow importing scripts from scripts/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../scripts"))

from craftflow_version_bump import (
    classify_subject,
    group_commits,
    max_bump,
    next_version,
    parse_semver,
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


def check_true(name: str, condition: bool):
    global PASS, FAIL
    if condition:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        print("    expected condition to be True")
        FAIL += 1


# --- classify_subject: type -> level mapping ---
print("\n[classify_subject: type -> level mapping]")
check("feat -> minor", classify_subject("feat(craftflow): x", ""), "minor")
check("fix -> patch", classify_subject("fix(craftflow): x", ""), "patch")
check("perf -> patch", classify_subject("perf: x", ""), "patch")
check("revert -> patch", classify_subject("revert: x", ""), "patch")

# --- classify_subject: bang -> major ---
print("\n[classify_subject: '!' bang forces major]")
check("feat! -> major", classify_subject("feat!: x", ""), "major")
check("fix(scope)! -> major", classify_subject("fix(scope)!: x", ""), "major")

# --- classify_subject: breaking body markers -> major ---
print("\n[classify_subject: line-anchored BREAKING CHANGE body markers]")
check(
    "BREAKING CHANGE: line -> major",
    classify_subject("feat: x", "BREAKING CHANGE: y"),
    "major",
)
check(
    "BREAKING-CHANGE: line -> major",
    classify_subject("feat: x", "BREAKING-CHANGE: y"),
    "major",
)
check(
    "mid-line 'BREAKING CHANGE:' is NOT a marker (line-anchored)",
    classify_subject("feat: x", "note: a BREAKING CHANGE: mid-line"),
    "minor",
)

# --- classify_subject: non-conforming / non-bumping types -> None ---
print("\n[classify_subject: non-bumping types and non-conforming subjects -> None]")
check("docs -> None", classify_subject("docs(craftflow): x", ""), None)
for t in ("chore", "test", "refactor", "style", "ci", "build"):
    check(f"{t} -> None", classify_subject(f"{t}: x", ""), None)
check(
    "non-conforming subject -> None",
    classify_subject("random text no type", ""),
    None,
)

# --- max_bump ---
print("\n[max_bump: highest-severity wins]")
check("patch+minor -> minor", max_bump(["patch", "minor"]), "minor")
check(
    "patch+major+minor -> major",
    max_bump(["patch", "major", "minor"]),
    "major",
)
check("empty -> None", max_bump([]), None)

# --- P6: max_bump is order-independent (highest-severity wins) ---
print("\n[P6: max_bump order-independence over every permutation]")
all_major = all(
    max_bump(list(perm)) == "major"
    for perm in itertools.permutations(["patch", "minor", "major"])
)
check_true("every permutation of all 3 levels reduces to 'major'", all_major)

# --- next_version ---
print("\n[next_version: semver arithmetic]")
check("1.0.0 + patch -> 1.0.1", next_version("1.0.0", "patch"), "1.0.1")
check("1.0.0 + minor -> 1.1.0", next_version("1.0.0", "minor"), "1.1.0")
check("1.2.3 + major -> 2.0.0", next_version("1.2.3", "major"), "2.0.0")
check(
    "1.2.3 + minor -> 1.3.0 (patch resets to 0)",
    next_version("1.2.3", "minor"),
    "1.3.0",
)

# --- P2: next_version is always strictly greater than current ---
print("\n[P2: strict monotonicity across current x bump-level matrix]")
currents = ["0.0.1", "1.0.0", "1.2.3", "9.9.9"]
levels = ["patch", "minor", "major"]
all_monotonic = all(
    parse_semver(next_version(current, level)) > parse_semver(current)
    for current, level in itertools.product(currents, levels)
)
check_true("next_version(current, level) > current for every combination", all_monotonic)

# --- group_commits ---
print("\n[group_commits: groups by type key, unknown types under 'other']")
commits = [
    {"subject": "feat: add x", "body": ""},
    {"subject": "fix: bug", "body": ""},
    {"subject": "docs: readme", "body": ""},
    {"subject": "wip: nothing", "body": ""},
    {"subject": "random text no type", "body": ""},
]
groups = group_commits(commits)
check("feat commit grouped under 'feat'", groups.get("feat"), [commits[0]])
check("fix commit grouped under 'fix'", groups.get("fix"), [commits[1]])
check("docs commit grouped under 'docs'", groups.get("docs"), [commits[2]])
check(
    "unrecognized type + non-conforming subject grouped under 'other'",
    groups.get("other"),
    [commits[3], commits[4]],
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
