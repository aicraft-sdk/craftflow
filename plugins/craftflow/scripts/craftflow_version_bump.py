#!/usr/bin/env python3
"""Conventional-commit version bump + CHANGELOG generator for the craftflow plugin.

Run: python3 scripts/craftflow_version_bump.py --dry-run
     python3 scripts/craftflow_version_bump.py --apply --expect-version X.Y.Z
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

# NOTE: deliberately NOT named PLUGIN_ROOT or ROOT. craftflow_harness_audit.py in this
# same directory already uses BOTH: PLUGIN_ROOT = parents[1] (plugins/craftflow) and
# ROOT = parents[3] (tools/craftflow-plugin). This is parents[3] — the published subtree
# root — so PLUGIN_ROOT would name a different directory here than it does one file over.
SUBTREE_ROOT_DEFAULT = Path(__file__).resolve().parents[3]  # tools/craftflow-plugin

SUBJECT_RE = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?: (?P<desc>.+)$"
)
BREAKING_BODY_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
LEVELS = {"patch": 1, "minor": 2, "major": 3}
TYPE_LEVEL = {"feat": "minor", "fix": "patch", "perf": "patch", "revert": "patch"}
GROUP_ORDER = [
    ("breaking", "Breaking Changes"),
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("perf", "Performance"),
    ("revert", "Reverts"),
    ("refactor", "Refactoring"),
    ("docs", "Documentation"),
    ("test", "Tests"),
    ("build", "Build"),
    ("ci", "CI"),
    ("chore", "Chores"),
    ("other", "Other"),
]
KNOWN_GROUP_TYPES = {key for key, _ in GROUP_ORDER if key not in ("breaking", "other")}


def classify_subject(subject: str, body: str) -> str | None:
    """Classify a conventional-commit subject + body into a bump level.

    Returns "major" if the subject has a '!' bang or the body contains a
    line-anchored BREAKING CHANGE / BREAKING-CHANGE marker. Otherwise returns
    the level mapped from the subject's type (feat/fix/perf/revert), or None
    for non-conforming subjects and types with no bump level (docs, chore,
    test, refactor, style, ci, build, etc.).
    """
    match = SUBJECT_RE.match(subject)
    if match and (match.group("bang") or BREAKING_BODY_RE.search(body)):
        return "major"
    if not match:
        return None
    return TYPE_LEVEL.get(match.group("type"))


def max_bump(levels: list[str]) -> str | None:
    """Return the highest-severity bump level under major > minor > patch."""
    if not levels:
        return None
    return max(levels, key=lambda level: LEVELS[level])


def parse_semver(version: str) -> tuple[int, int, int]:
    """Parse "X.Y.Z" into a comparable (major, minor, patch) tuple."""
    major, minor, patch = (int(part) for part in version.split("."))
    return (major, minor, patch)


def next_version(current: str, bump: str) -> str:
    """Compute the next semver string for the given bump level."""
    major, minor, patch = parse_semver(current)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def group_commits(commits: list[dict]) -> dict[str, list[dict]]:
    """Group commit records by their conventional-commit type.

    Each commit dict must have a "subject" key. Commits with a recognized
    type (per GROUP_ORDER) are grouped under that type key; commits with an
    unrecognized type or a non-conforming subject are grouped under "other".
    """
    groups: dict[str, list[dict]] = {}
    for commit in commits:
        match = SUBJECT_RE.match(commit.get("subject", ""))
        type_key = match.group("type") if match else None
        if type_key not in KNOWN_GROUP_TYPES:
            type_key = "other"
        groups.setdefault(type_key, []).append(commit)
    return groups


README_VERSION_LINE_RE = re.compile(r"^\*\*Current version:\*\* .+$", re.MULTILINE)
DESCRIPTION_VERSION_RE = re.compile(r"craftflow v\d+\.\d+\.\d+")
CHANGELOG_SECTION_HEADER_RE = re.compile(r"^## \[", re.MULTILINE)

# The 6 version-bearing files, relative to the subtree root, in the exact
# order write_all() writes them.
CHANGED_FILE_PATHS = [
    "plugins/craftflow/.claude-plugin/plugin.json",
    "plugins/craftflow/.cursor-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "plugins/craftflow/.cursor-plugin/marketplace.json",
    "README.md",
    "CHANGELOG.md",
]


def render_changelog_section(version: str, date: str, groups: dict[str, list[dict]]) -> str:
    """Render a Keep-a-Changelog-style release section for `version`/`date`.

    Groups are rendered in GROUP_ORDER; groups absent from `groups` (or with
    an empty list) are omitted entirely — no empty `### Heading` blocks.
    """
    lines = [f"## [{version}] - {date}", ""]
    for key, label in GROUP_ORDER:
        commits = groups.get(key)
        if not commits:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for commit in commits:
            subject = commit.get("subject", "")
            match = SUBJECT_RE.match(subject)
            desc = match.group("desc") if match else subject
            lines.append(f"- {desc}")
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def insert_changelog_section(existing: str, section: str) -> str:
    """Insert `section` immediately before the first existing `## [` release
    header, preserving everything above it (the file header) verbatim. If no
    release header exists yet, append the section at the end."""
    match = CHANGELOG_SECTION_HEADER_RE.search(existing)
    if not match:
        return existing.rstrip("\n") + "\n\n" + section + "\n"
    idx = match.start()
    header = existing[:idx]
    rest = existing[idx:]
    return header + section + "\n\n" + rest


def extract_changelog_section(text: str, version: str) -> str:
    """Return the body of the release section for `version` — everything
    between its `## [version] ...` header and the next `## [` header (or end
    of file) — with no leading/trailing blank-line noise. Returns "" if no
    section for `version` exists."""
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return ""
    return match.group(1).strip("\n")


def rewrite_readme_version(readme_text: str, new_version: str) -> str:
    """Rewrite the `**Current version:** X.Y.Z` line to `new_version`.

    Raises ValueError if no such line exists — never silently no-ops."""
    if not README_VERSION_LINE_RE.search(readme_text):
        raise ValueError("README has no '**Current version:**' line to rewrite")
    return README_VERSION_LINE_RE.sub(
        f"**Current version:** {new_version}", readme_text, count=1
    )


def rewrite_description_version(description: str, new_version: str) -> str:
    """Rewrite an embedded 'craftflow vX.Y.Z' substring to `new_version`.

    Returns `description` unchanged if it embeds no version substring."""
    if not DESCRIPTION_VERSION_RE.search(description):
        return description
    return DESCRIPTION_VERSION_RE.sub(
        f"craftflow v{new_version}", description, count=1
    )


def set_json_version(obj, path: list, new_version: str):
    """Return a deep copy of `obj` with the value at `path` (a list of
    dict keys / list indices) set to `new_version`. Does not mutate `obj`."""
    result = copy.deepcopy(obj)
    cursor = result
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = new_version
    return result


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_all(subtree_root: Path, next_version: str, section: str) -> None:
    """Write all 6 version-bearing files under `subtree_root`, in the same
    order as CHANGED_FILE_PATHS."""
    plugin_json_path = subtree_root / CHANGED_FILE_PATHS[0]
    cursor_plugin_json_path = subtree_root / CHANGED_FILE_PATHS[1]
    marketplace_json_path = subtree_root / CHANGED_FILE_PATHS[2]
    cursor_marketplace_json_path = subtree_root / CHANGED_FILE_PATHS[3]
    readme_path = subtree_root / CHANGED_FILE_PATHS[4]
    changelog_path = subtree_root / CHANGED_FILE_PATHS[5]

    # 1. plugins/craftflow/.claude-plugin/plugin.json -> version
    plugin = json.loads(plugin_json_path.read_text(encoding="utf-8"))
    _write_json(plugin_json_path, set_json_version(plugin, ["version"], next_version))

    # 2. plugins/craftflow/.cursor-plugin/plugin.json -> version
    cursor_plugin = json.loads(cursor_plugin_json_path.read_text(encoding="utf-8"))
    _write_json(
        cursor_plugin_json_path,
        set_json_version(cursor_plugin, ["version"], next_version),
    )

    # 3. .claude-plugin/marketplace.json -> metadata.version, metadata.description,
    #    plugins[0].version
    marketplace = json.loads(marketplace_json_path.read_text(encoding="utf-8"))
    marketplace = set_json_version(marketplace, ["metadata", "version"], next_version)
    marketplace = set_json_version(
        marketplace,
        ["metadata", "description"],
        rewrite_description_version(
            marketplace.get("metadata", {}).get("description", ""), next_version
        ),
    )
    marketplace = set_json_version(marketplace, ["plugins", 0, "version"], next_version)
    _write_json(marketplace_json_path, marketplace)

    # 4. plugins/craftflow/.cursor-plugin/marketplace.json -> same three fields
    cursor_marketplace = json.loads(cursor_marketplace_json_path.read_text(encoding="utf-8"))
    cursor_marketplace = set_json_version(
        cursor_marketplace, ["metadata", "version"], next_version
    )
    cursor_marketplace = set_json_version(
        cursor_marketplace,
        ["metadata", "description"],
        rewrite_description_version(
            cursor_marketplace.get("metadata", {}).get("description", ""), next_version
        ),
    )
    cursor_marketplace = set_json_version(
        cursor_marketplace, ["plugins", 0, "version"], next_version
    )
    _write_json(cursor_marketplace_json_path, cursor_marketplace)

    # 5. README.md -> **Current version:** line
    readme = readme_path.read_text(encoding="utf-8")
    readme_path.write_text(rewrite_readme_version(readme, next_version), encoding="utf-8")

    # 6. CHANGELOG.md -> new section inserted
    changelog = changelog_path.read_text(encoding="utf-8")
    changelog_path.write_text(
        insert_changelog_section(changelog, section), encoding="utf-8"
    )


def apply_bump(
    subtree_root: Path,
    bump: str | None,
    next_version: str,
    groups: dict[str, list[dict]],
    date: str,
) -> list[str]:
    """Write the 6 version-bearing files when a bump is due. No-op (returns an
    empty list, writes nothing) when `bump` is None — this function only acts
    on an already-decided bump; it never classifies commits itself."""
    if bump is None:
        return []
    section = render_changelog_section(next_version, date, groups)
    write_all(subtree_root, next_version, section)
    return list(CHANGED_FILE_PATHS)


class BaselineTagNotFoundError(RuntimeError):
    """Raised when no craftflow-v* tag is reachable from HEAD and --since was
    not given. There is deliberately no fallback to a full-history scan."""


def resolve_base_ref(repo_root: Path, since: str | None) -> str:
    """Return the base ref to diff against: `since` if given, otherwise the
    most recent reachable `craftflow-v*` tag. Raises BaselineTagNotFoundError
    (after printing the standard stderr message) when neither is available."""
    if since:
        return since
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "describe",
            "--tags",
            "--match",
            "craftflow-v*",
            "--abbrev=0",
            "HEAD",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "ERROR: no craftflow-v* baseline tag reachable from HEAD. "
            "Bootstrap one first (see docs/ai/decisions/0029-*.md).",
            file=sys.stderr,
        )
        raise BaselineTagNotFoundError()
    return result.stdout.strip()


def _derive_prefix(repo_root: Path, subtree_root: Path) -> str:
    """The subtree path relative to the repo root, for use as git log's
    pathspec — always derived, never hardcoded."""
    return str(subtree_root.resolve().relative_to(repo_root.resolve()))


RECORD_SEP = "\x1e"
FIELD_SEP = "\x1f"


def read_commit_range(repo_root: Path, base_ref: str, prefix: str) -> list[dict]:
    """Read non-merge commits in `base_ref..HEAD` touching `prefix`, as a list
    of {"sha", "subject", "body"} dicts in commit order."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "log",
            "--no-merges",
            f"--format=%H{FIELD_SEP}%s{FIELD_SEP}%b{RECORD_SEP}",
            f"{base_ref}..HEAD",
            "--",
            prefix,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    commits = []
    for record in result.stdout.split(RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(FIELD_SEP)
        sha = parts[0]
        subject = parts[1] if len(parts) > 1 else ""
        body = parts[2].strip("\n") if len(parts) > 2 else ""
        commits.append({"sha": sha, "subject": subject, "body": body})
    return commits


def _read_current_version(subtree_root: Path) -> str:
    plugin_json_path = subtree_root / CHANGED_FILE_PATHS[0]
    return json.loads(plugin_json_path.read_text(encoding="utf-8"))["version"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Conventional-commit version bump + CHANGELOG generator "
        "for the craftflow plugin."
    )
    parser.add_argument(
        "--subtree-root",
        type=Path,
        default=SUBTREE_ROOT_DEFAULT,
        help="Root of the craftflow-plugin subtree (default: this checkout).",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Base ref to diff against. Defaults to the most recent craftflow-v* tag.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Compute and print only.")
    mode.add_argument("--apply", action="store_true", help="Compute and write the 6 files.")
    mode.add_argument(
        "--emit-notes",
        metavar="VERSION",
        default=None,
        help="Print the CHANGELOG section body for VERSION and exit.",
    )
    parser.add_argument(
        "--expect-version",
        default=None,
        help="Assert the computed next_version equals this value. Never pass empty.",
    )
    args = parser.parse_args(argv)

    subtree_root = args.subtree_root.resolve()

    if args.emit_notes is not None:
        changelog_path = subtree_root / CHANGED_FILE_PATHS[5]
        changelog = changelog_path.read_text(encoding="utf-8")
        print(extract_changelog_section(changelog, args.emit_notes))
        return 0

    toplevel = subprocess.run(
        ["git", "-C", str(subtree_root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    repo_root = Path(toplevel.stdout.strip())

    try:
        base_ref = resolve_base_ref(repo_root, args.since)
    except BaselineTagNotFoundError:
        return 2

    current_version = _read_current_version(subtree_root)
    prefix = _derive_prefix(repo_root, subtree_root)
    commits = read_commit_range(repo_root, base_ref, prefix)

    levels = []
    unclassified = []
    for commit in commits:
        level = classify_subject(commit.get("subject", ""), commit.get("body", ""))
        if level is None:
            unclassified.append(commit["subject"])
        else:
            levels.append(level)

    bump = max_bump(levels)
    groups = group_commits(commits)
    computed_next_version = next_version(current_version, bump) if bump else current_version

    if args.expect_version == "":
        print(
            "ERROR: --expect-version was given as an empty string; this is "
            "malformed (omit the flag to leave it unset, never pass an empty value).",
            file=sys.stderr,
        )
        return 3
    if args.expect_version is not None and args.expect_version != computed_next_version:
        print(
            f"ERROR: --expect-version ({args.expect_version!r}) does not match "
            f"the computed next_version ({computed_next_version!r}).",
            file=sys.stderr,
        )
        return 3

    changed_files: list[str] = []
    if args.apply:
        date = dt.date.today().isoformat()
        changed_files = apply_bump(subtree_root, bump, computed_next_version, groups, date)

    output = {
        "schema": "craftflow-version-bump/1",
        "base_ref": base_ref,
        "current_version": current_version,
        "bump": bump,
        "next_version": computed_next_version,
        "commit_count": len(commits),
        "typed_commit_count": len(levels),
        "unclassified": unclassified,
        "groups": {key: [c["subject"] for c in items] for key, items in groups.items()},
        "changed_files": changed_files,
        "applied": bool(changed_files),
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
