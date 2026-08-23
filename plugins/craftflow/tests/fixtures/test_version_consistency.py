#!/usr/bin/env python3
"""
Fixture-based unit tests for craftflow_version_consistency_check.py

Run from the plugin root:
    python3 tests/fixtures/test_version_consistency.py
"""
import os
import sys

# Allow importing scripts from scripts/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../scripts"))

from craftflow_version_consistency_check import evaluate_consistency

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


def check_errors(name: str, errors, expected_count: int, contains: str = None):
    """Assert errors has exactly expected_count entries, and (if given) that at
    least one entry contains `contains`."""
    global PASS, FAIL
    ok = len(errors) == expected_count
    if ok and contains is not None:
        ok = any(contains in e for e in errors)
    if ok:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        print(f"    expected_count: {expected_count}, contains: {contains!r}")
        print(f"    actual errors ({len(errors)}): {errors!r}")
        FAIL += 1


VERSION = "1.2.3"


def make_snapshot(version: str = VERSION) -> dict:
    """Baseline snapshot with all 13 version-bearing fields consistent at
    `version`. Each test case deep-copies this and mutates exactly one field."""
    return {
        "version": version,
        "readme": f"# Craftflow Plugin\n\n**Current version:** {version}\n",
        "changelog": f"## [{version}] - 2026-01-01\n\n### Added\n\n- stuff\n",
        "marketplace": {
            "metadata": {
                "version": version,
                "description": f"craftflow v{version} — owned orchestration flow.",
            },
            "plugins": [
                {
                    "name": "craftflow",
                    "version": version,
                    "source": "./plugins/craftflow",
                }
            ],
        },
        "cursor_plugin": {"name": "craftflow", "version": version},
        "cursor_marketplace": {
            "metadata": {
                "version": version,
                "description": f"craftflow v{version} — owned orchestration flow.",
            },
            "plugins": [
                {
                    "name": "craftflow",
                    "version": version,
                    "source": ".",
                }
            ],
        },
        # ai-craft repo-ROOT .claude-plugin/marketplace.json -- a SEPARATE
        # github-based-marketplace-install manifest, two levels above the
        # tools/craftflow-plugin subtree. Its plugins[0].source legitimately
        # differs from both files above ("./tools/craftflow-plugin/plugins/craftflow"),
        # so the source assertion must not apply to it (see case 24).
        "root_marketplace": {
            "metadata": {
                "version": version,
                "description": f"craftflow v{version} — owned orchestration flow.",
            },
            "plugins": [
                {
                    "name": "craftflow",
                    "version": version,
                    "source": "./tools/craftflow-plugin/plugins/craftflow",
                }
            ],
        },
    }


# --- Case 1: all 13 fields consistent ---
print("\n[case 1: all 13 fields at 1.2.3]")
snap = make_snapshot()
check_errors("no errors when everything matches", evaluate_consistency(snap), 0)

# --- Case 2: README missing the line ---
print("\n[case 2: README missing the '**Current version:**' line]")
snap = make_snapshot()
snap["readme"] = "# Craftflow Plugin\n\nNo version line here.\n"
check_errors(
    "1 error naming README.md", evaluate_consistency(snap), 1, contains="README.md"
)

# --- Case 3: README has two version lines ---
print("\n[case 3: README has two '**Current version:**' lines]")
snap = make_snapshot()
snap["readme"] = (
    f"# Craftflow Plugin\n\n**Current version:** {VERSION}\n"
    f"**Current version:** {VERSION}\n"
)
check_errors(
    "1 error about ambiguity", evaluate_consistency(snap), 1, contains="ambiguous"
)

# --- Case 4: CHANGELOG absent, default mode ---
print("\n[case 4: CHANGELOG is None, default mode]")
snap = make_snapshot()
snap["changelog"] = None
check_errors(
    "1 error naming CHANGELOG.md",
    evaluate_consistency(snap),
    1,
    contains="CHANGELOG.md",
)

# --- Case 5: CHANGELOG absent, allow_missing_changelog=True ---
print("\n[case 5: CHANGELOG is None, allow_missing_changelog=True]")
snap = make_snapshot()
snap["changelog"] = None
check_errors(
    "no errors when changelog is allowed to be missing",
    evaluate_consistency(snap, allow_missing_changelog=True),
    0,
)

# --- Case 6: CHANGELOG present, no matching release section ---
print("\n[case 6: CHANGELOG present, no '## [1.2.3]' section]")
snap = make_snapshot()
snap["changelog"] = "## [0.9.0] - 2025-01-01\n\n### Added\n\n- old stuff\n"
check_errors("1 error", evaluate_consistency(snap), 1, contains="CHANGELOG.md")

# --- Case 7: top-level metadata.version drift ---
print("\n[case 7: top-level marketplace metadata.version drift]")
snap = make_snapshot()
snap["marketplace"]["metadata"]["version"] = "9.9.9"
check_errors("1 error", evaluate_consistency(snap), 1, contains="marketplace.json")

# --- Case 8: top-level plugins[0].version drift ---
print("\n[case 8: top-level marketplace plugins[0].version drift]")
snap = make_snapshot()
snap["marketplace"]["plugins"][0]["version"] = "9.9.9"
check_errors("1 error", evaluate_consistency(snap), 1, contains="marketplace.json")

# --- Case 9: top-level plugins empty ---
print("\n[case 9: top-level marketplace plugins empty]")
snap = make_snapshot()
snap["marketplace"]["plugins"] = []
check_errors(
    "1 error",
    evaluate_consistency(snap),
    1,
    contains="marketplace.json has no plugins entries",
)

# --- Case 10: top-level plugins[0].source changed ---
print("\n[case 10: top-level marketplace plugins[0].source changed]")
snap = make_snapshot()
snap["marketplace"]["plugins"][0]["source"] = "./somewhere-else"
check_errors("1 error", evaluate_consistency(snap), 1, contains="marketplace.json")

# --- Case 11: top-level description embeds stale version ---
print("\n[case 11: top-level metadata.description embeds craftflow v9.9.9]")
snap = make_snapshot()
snap["marketplace"]["metadata"]["description"] = "craftflow v9.9.9 — stale."
check_errors(
    "1 error", evaluate_consistency(snap), 1, contains="metadata.description"
)

# --- Case 12: top-level description embeds no version at all ---
print("\n[case 12: top-level metadata.description embeds no version]")
snap = make_snapshot()
snap["marketplace"]["metadata"]["description"] = "no version mentioned here."
check_errors("no errors", evaluate_consistency(snap), 0)

# --- Case 13: .cursor-plugin/plugin.json version drift ---
print("\n[case 13: .cursor-plugin/plugin.json version drift]")
snap = make_snapshot()
snap["cursor_plugin"]["version"] = "9.9.9"
check_errors(
    "1 error naming .cursor-plugin/plugin.json",
    evaluate_consistency(snap),
    1,
    contains=".cursor-plugin/plugin.json",
)

# --- Case 14: .cursor-plugin/marketplace.json metadata.version drift ---
print("\n[case 14: .cursor-plugin/marketplace.json metadata.version drift]")
snap = make_snapshot()
snap["cursor_marketplace"]["metadata"]["version"] = "9.9.9"
check_errors(
    "1 error",
    evaluate_consistency(snap),
    1,
    contains=".cursor-plugin/marketplace.json",
)

# --- Case 15: .cursor-plugin/marketplace.json plugins[0].version drift ---
print("\n[case 15: .cursor-plugin/marketplace.json plugins[0].version drift]")
snap = make_snapshot()
snap["cursor_marketplace"]["plugins"][0]["version"] = "9.9.9"
check_errors(
    "1 error",
    evaluate_consistency(snap),
    1,
    contains=".cursor-plugin/marketplace.json",
)

# --- Case 16: .cursor-plugin/marketplace.json source == "." must NOT error ---
print("\n[case 16: .cursor-plugin/marketplace.json plugins[0].source == '.']")
snap = make_snapshot()
# baseline already has source "." for cursor_marketplace; assert it stays clean
check_errors(
    "no errors -- source assertion must not apply to the cursor file",
    evaluate_consistency(snap),
    0,
)

# --- Case 17: three simultaneous drifts, none short-circuited ---
print("\n[case 17: three simultaneous drifts]")
snap = make_snapshot()
snap["readme"] = "# Craftflow Plugin\n\nNo version line here.\n"
snap["cursor_plugin"]["version"] = "9.9.9"
snap["marketplace"]["plugins"][0]["version"] = "9.9.9"
check_errors("exactly 3 errors, all reported", evaluate_consistency(snap), 3)

# --- Case 18: expect_version given and mismatched ---
print("\n[case 18: expect_version given and != plugin.json version]")
snap = make_snapshot()
check_errors(
    "1 error",
    evaluate_consistency(snap, expect_version="9.9.9"),
    1,
    contains="--expect-version",
)

# --- Case 19: expect_version == "" (empty string, malformed) ---
print("\n[case 19: expect_version='' (empty string, not None)]")
snap = make_snapshot()
check_errors(
    "1 error naming the empty value",
    evaluate_consistency(snap, expect_version=""),
    1,
    contains="--expect-version",
)

# --- Case 20: expect_version is None (genuinely unset) ---
print("\n[case 20: expect_version=None (genuinely unset)]")
snap = make_snapshot()
check_errors(
    "no errors -- None means not requested",
    evaluate_consistency(snap, expect_version=None),
    0,
)

# --- Case 21: root marketplace.json metadata.version drift ---
print("\n[case 21: root marketplace.json metadata.version drift]")
snap = make_snapshot()
snap["root_marketplace"]["metadata"]["version"] = "9.9.9"
check_errors(
    "1 error naming root marketplace.json",
    evaluate_consistency(snap),
    1,
    contains="root marketplace.json",
)

# --- Case 22: root marketplace.json plugins[0].version drift ---
print("\n[case 22: root marketplace.json plugins[0].version drift]")
snap = make_snapshot()
snap["root_marketplace"]["plugins"][0]["version"] = "9.9.9"
check_errors(
    "1 error naming root marketplace.json",
    evaluate_consistency(snap),
    1,
    contains="root marketplace.json",
)

# --- Case 23: root marketplace.json metadata.description embeds stale version ---
print("\n[case 23: root marketplace.json metadata.description embeds craftflow v9.9.9]")
snap = make_snapshot()
snap["root_marketplace"]["metadata"]["description"] = "craftflow v9.9.9 — stale."
check_errors(
    "1 error naming root marketplace.json's description",
    evaluate_consistency(snap),
    1,
    contains="root marketplace.json metadata.description",
)

# --- Case 24: root marketplace.json plugins[0].source must NOT error ---
print("\n[case 24: root marketplace.json plugins[0].source changed -- must not error]")
snap = make_snapshot()
snap["root_marketplace"]["plugins"][0]["source"] = "./somewhere-else"
check_errors(
    "no errors -- the source assertion does not apply to the root marketplace file",
    evaluate_consistency(snap),
    0,
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
