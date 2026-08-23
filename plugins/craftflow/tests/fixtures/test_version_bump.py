#!/usr/bin/env python3
"""
Fixture-based unit tests for craftflow_version_bump.py: the pure core
(classify_subject, max_bump, next_version, parse_semver, group_commits,
render_changelog_section, insert_changelog_section, extract_changelog_section,
rewrite_readme_version, rewrite_description_version, set_json_version) plus
the apply_bump no-op/write edge, exercised against a real temp plugin tree.

Run from the plugin root:
    python3 tests/fixtures/test_version_bump.py
"""
import hashlib
import itertools
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Allow importing scripts from scripts/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../scripts"))

from craftflow_version_bump import (
    CHANGED_FILE_PATHS,
    apply_bump,
    classify_subject,
    extract_changelog_section,
    group_commits,
    insert_changelog_section,
    max_bump,
    next_version,
    parse_semver,
    render_changelog_section,
    rewrite_description_version,
    rewrite_readme_version,
    set_json_version,
)
from craftflow_version_consistency_check import build_snapshot, evaluate_consistency

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

# --- render_changelog_section ---
print("\n[render_changelog_section: header + ordered groups, empty groups omitted]")
rcs_groups = {
    "feat": [{"subject": "feat: add x", "body": ""}],
    "fix": [{"subject": "fix: bug y", "body": ""}],
}
section = render_changelog_section("1.1.0", "2026-08-22", rcs_groups)
check_true(
    "starts with '## [1.1.0] - 2026-08-22'",
    section.startswith("## [1.1.0] - 2026-08-22"),
)
check_true(
    "Features rendered before Fixes (GROUP_ORDER)",
    "### Features" in section and "### Fixes" in section
    and section.index("### Features") < section.index("### Fixes"),
)
check_true("feat bullet content included", "add x" in section)
check_true("fix bullet content included", "bug y" in section)
check_true(
    "empty groups omitted entirely (no ### Documentation)",
    "### Documentation" not in section,
)

# --- insert_changelog_section ---
print("\n[insert_changelog_section: new section before first existing '## [' line]")
existing_changelog = (
    "# Changelog\n\n"
    "All notable changes to the craftflow plugin are documented in this file.\n\n"
    "## [1.0.0] - 2026-06-14\n\n"
    "### Added\n\n- Initial tracked release.\n"
)
new_section = "## [1.1.0] - 2026-08-22\n\n### Features\n\n- add x"
inserted = insert_changelog_section(existing_changelog, new_section)
check_true(
    "header preserved verbatim before new section",
    inserted.startswith(
        "# Changelog\n\n"
        "All notable changes to the craftflow plugin are documented in this file.\n\n"
    ),
)
check_true(
    "new section appears before old '## [1.0.0]' section",
    inserted.index("## [1.1.0]") < inserted.index("## [1.0.0]"),
)
check_true("old section content preserved", "Initial tracked release." in inserted)

# --- extract_changelog_section ---
print("\n[extract_changelog_section: body between version header and next '## [']")
two_section_changelog = (
    "## [1.1.0] - 2026-08-22\n\n### Features\n\n- add x\n\n"
    "## [1.0.0] - 2026-06-14\n\n### Added\n\n- Initial tracked release.\n"
)
extracted = extract_changelog_section(two_section_changelog, "1.1.0")
check_true("extracted body contains '### Features'", "### Features" in extracted)
check_true("extracted body contains its own bullet", "- add x" in extracted)
check_true(
    "extracted body excludes the next version's section",
    "1.0.0" not in extracted and "Initial tracked release." not in extracted,
)
check(
    "absent version returns empty string",
    extract_changelog_section(two_section_changelog, "9.9.9"),
    "",
)

# --- rewrite_readme_version ---
print("\n[rewrite_readme_version: rewrite current-version line, ValueError if absent]")
check(
    "rewrites '**Current version:**' line",
    rewrite_readme_version("**Current version:** 1.0.0", "1.1.0"),
    "**Current version:** 1.1.0",
)
try:
    rewrite_readme_version("no version line in this README", "1.1.0")
    check_true("raises ValueError when no version line present", False)
except ValueError:
    check_true("raises ValueError when no version line present", True)

# --- rewrite_description_version ---
print("\n[rewrite_description_version: rewrite embedded version, unchanged if absent]")
check(
    "rewrites embedded 'craftflow vX.Y.Z' version",
    rewrite_description_version("craftflow v1.0.0 — orchestration flow", "1.1.0"),
    "craftflow v1.1.0 — orchestration flow",
)
check(
    "unchanged when no version substring present",
    rewrite_description_version("no version embedded here", "1.1.0"),
    "no version embedded here",
)

# --- set_json_version ---
print("\n[set_json_version: top-level and nested dict/list paths]")
check(
    "top-level 'version' field",
    set_json_version({"version": "1.0.0"}, ["version"], "1.1.0"),
    {"version": "1.1.0"},
)
check(
    "nested 'metadata.version' field",
    set_json_version(
        {"metadata": {"version": "1.0.0"}}, ["metadata", "version"], "1.1.0"
    ),
    {"metadata": {"version": "1.1.0"}},
)
check(
    "nested 'plugins[0].version' field",
    set_json_version(
        {"plugins": [{"version": "1.0.0"}]}, ["plugins", 0, "version"], "1.1.0"
    ),
    {"plugins": [{"version": "1.1.0"}]},
)


def _build_temp_plugin_tree(base_version: str = "1.0.0") -> Path:
    """Builds a throwaway repo-root-shaped tree (fake repo root containing
    tools/craftflow-plugin as the subtree, mirroring the real ai-craft
    layout) for exercising apply_bump/write_all without touching the real
    working tree. Returns the SUBTREE root (repo_root/tools/craftflow-plugin)
    -- exactly what the real script receives as --subtree-root. The 7th
    version-bearing file (the repo-root marketplace.json) lives two levels
    above the returned path, so callers must clean up via
    shutil.rmtree(returned_path.parent.parent), not the returned path alone."""
    repo_root = Path(tempfile.mkdtemp(prefix="version-bump-fixture-"))
    tmp = repo_root / "tools" / "craftflow-plugin"
    (tmp / "plugins/craftflow/.claude-plugin").mkdir(parents=True)
    (tmp / "plugins/craftflow/.cursor-plugin").mkdir(parents=True)
    (tmp / ".claude-plugin").mkdir(parents=True)
    (repo_root / ".claude-plugin").mkdir(parents=True)

    plugin = {"name": "craftflow", "version": base_version}
    (tmp / "plugins/craftflow/.claude-plugin/plugin.json").write_text(
        json.dumps(plugin, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (tmp / "plugins/craftflow/.cursor-plugin/plugin.json").write_text(
        json.dumps(plugin, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    marketplace = {
        "metadata": {
            "description": f"craftflow v{base_version} — test",
            "version": base_version,
        },
        "plugins": [{"version": base_version, "source": "./plugins/craftflow"}],
    }
    cursor_marketplace = {
        "metadata": {
            "description": f"craftflow v{base_version} — test",
            "version": base_version,
        },
        "plugins": [{"version": base_version, "source": "."}],
    }
    root_marketplace = {
        "metadata": {
            "description": f"craftflow v{base_version} — test",
            "version": base_version,
        },
        "plugins": [
            {
                "version": base_version,
                "source": "./tools/craftflow-plugin/plugins/craftflow",
            }
        ],
    }
    (tmp / ".claude-plugin/marketplace.json").write_text(
        json.dumps(marketplace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (tmp / "plugins/craftflow/.cursor-plugin/marketplace.json").write_text(
        json.dumps(cursor_marketplace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (repo_root / ".claude-plugin/marketplace.json").write_text(
        json.dumps(root_marketplace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (tmp / "README.md").write_text(
        f"# Craftflow\n\n**Current version:** {base_version}\n", encoding="utf-8"
    )
    (tmp / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{base_version}] - 2026-01-01\n\n### Added\n\n- Baseline.\n",
        encoding="utf-8",
    )
    return tmp


# --- P1: no-op purity ---
print("\n[P1: no-op purity — apply_bump(bump=None) writes nothing]")
p1_tmp = _build_temp_plugin_tree()
try:
    before_hashes = {
        path: hashlib.sha256((p1_tmp / path).read_bytes()).hexdigest()
        for path in CHANGED_FILE_PATHS
    }
    changed = apply_bump(p1_tmp, None, "1.0.0", {}, "2026-01-01")
    after_hashes = {
        path: hashlib.sha256((p1_tmp / path).read_bytes()).hexdigest()
        for path in CHANGED_FILE_PATHS
    }
    check("apply_bump(bump=None) returns no changed files", changed, [])
    check_true(
        "all 7 files byte-identical (SHA-256) after no-op apply, including "
        "the root marketplace.json",
        before_hashes == after_hashes,
    )
finally:
    shutil.rmtree(p1_tmp.parent.parent)

# --- P3: total consistency ---
print("\n[P3: total consistency — minor bump writes all 13 fields, gate passes]")
p3_tmp = _build_temp_plugin_tree()
try:
    p3_groups = {
        "feat": [{"subject": "feat: add x", "body": ""}],
        "fix": [{"subject": "fix: bug y", "body": ""}],
    }
    changed = apply_bump(p3_tmp, "minor", "1.1.0", p3_groups, "2026-01-02")
    check(
        "apply_bump returns all 7 changed file paths",
        sorted(changed),
        sorted(CHANGED_FILE_PATHS),
    )

    snapshot, load_errors = build_snapshot(p3_tmp)
    check("no load errors reading back the applied tree", load_errors, [])
    errors = evaluate_consistency(snapshot)
    check("evaluate_consistency finds zero errors after apply", errors, [])

    cursor_plugin_version = json.loads(
        (p3_tmp / "plugins/craftflow/.cursor-plugin/plugin.json").read_text(
            encoding="utf-8"
        )
    )["version"]

    check("field 1/13: plugin.json version", snapshot["version"], "1.1.0")
    check(
        "field 2/13: .cursor-plugin/plugin.json version",
        cursor_plugin_version,
        "1.1.0",
    )
    check(
        "field 3/13: marketplace.json metadata.version",
        snapshot["marketplace"]["metadata"]["version"],
        "1.1.0",
    )
    check(
        "field 4/13: marketplace.json metadata.description embeds new version",
        snapshot["marketplace"]["metadata"]["description"],
        "craftflow v1.1.0 — test",
    )
    check(
        "field 5/13: marketplace.json plugins[0].version",
        snapshot["marketplace"]["plugins"][0]["version"],
        "1.1.0",
    )
    check(
        "field 6/13: cursor marketplace.json metadata.version",
        snapshot["cursor_marketplace"]["metadata"]["version"],
        "1.1.0",
    )
    check(
        "field 7/13: cursor marketplace.json metadata.description embeds new version",
        snapshot["cursor_marketplace"]["metadata"]["description"],
        "craftflow v1.1.0 — test",
    )
    check(
        "field 8/13: cursor marketplace.json plugins[0].version",
        snapshot["cursor_marketplace"]["plugins"][0]["version"],
        "1.1.0",
    )
    check_true(
        "field 9/13: README current version is 1.1.0",
        "**Current version:** 1.1.0" in snapshot["readme"],
    )
    check_true(
        "field 10/13: CHANGELOG has new release section",
        "## [1.1.0]" in snapshot["changelog"],
    )
    check(
        "field 11/13: root marketplace.json metadata.version",
        snapshot["root_marketplace"]["metadata"]["version"],
        "1.1.0",
    )
    check(
        "field 12/13: root marketplace.json metadata.description embeds new version",
        snapshot["root_marketplace"]["metadata"]["description"],
        "craftflow v1.1.0 — test",
    )
    check(
        "field 13/13: root marketplace.json plugins[0].version",
        snapshot["root_marketplace"]["plugins"][0]["version"],
        "1.1.0",
    )
finally:
    shutil.rmtree(p3_tmp.parent.parent)

# --- Direct field-by-field write test: root marketplace.json ---
print("\n[write_all: root marketplace.json 3-field write, direct check]")
direct_tmp = _build_temp_plugin_tree()
try:
    apply_bump(direct_tmp, "patch", "1.0.1", {}, "2026-01-03")
    root_marketplace_json_path = direct_tmp.parent.parent / ".claude-plugin" / "marketplace.json"
    root_marketplace = json.loads(root_marketplace_json_path.read_text(encoding="utf-8"))
    check(
        "root marketplace.json metadata.version written",
        root_marketplace["metadata"]["version"],
        "1.0.1",
    )
    check(
        "root marketplace.json metadata.description version substring rewritten",
        root_marketplace["metadata"]["description"],
        "craftflow v1.0.1 — test",
    )
    check(
        "root marketplace.json plugins[0].version written",
        root_marketplace["plugins"][0]["version"],
        "1.0.1",
    )
    check(
        "root marketplace.json plugins[0].source left untouched by the write "
        "(write_all never touches source)",
        root_marketplace["plugins"][0]["source"],
        "./tools/craftflow-plugin/plugins/craftflow",
    )
finally:
    shutil.rmtree(direct_tmp.parent.parent)

# --- Summary ---
print(f"\n{'='*40}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    print("FAIL")
    sys.exit(1)
else:
    print("PASS")
    sys.exit(0)
