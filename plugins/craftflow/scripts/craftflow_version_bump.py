#!/usr/bin/env python3
"""Conventional-commit version bump + CHANGELOG generator for the craftflow plugin.

Run: python3 scripts/craftflow_version_bump.py --dry-run
     python3 scripts/craftflow_version_bump.py --apply --expect-version X.Y.Z
"""
from __future__ import annotations

import re

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
