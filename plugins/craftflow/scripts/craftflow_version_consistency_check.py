#!/usr/bin/env python3
"""Standalone version-consistency gate for the craftflow plugin.

Extracted from craftflow_harness_audit.py's version block so it can run without
that script's ~900 unrelated (and currently failing) lines.

Run: python3 scripts/craftflow_version_consistency_check.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# NOTE: deliberately NOT named PLUGIN_ROOT or ROOT. craftflow_harness_audit.py in this
# same directory already uses BOTH: PLUGIN_ROOT = parents[1] (plugins/craftflow) and
# ROOT = parents[3] (tools/craftflow-plugin). This is parents[3] — the published subtree
# root — so PLUGIN_ROOT would name a different directory here than it does one file over.
SUBTREE_ROOT_DEFAULT = Path(__file__).resolve().parents[3]  # tools/craftflow-plugin

DESCRIPTION_VERSION_RE = re.compile(r"craftflow v(\d+\.\d+\.\d+)")
README_VERSION_RE = re.compile(r"^\*\*Current version:\*\* (.+)$", re.MULTILINE)

EXPECTED_MARKETPLACE_SOURCE = "./plugins/craftflow"


def _check_marketplace_block(block, version, label, errors, check_source):
    """Append errors for a marketplace.json-shaped dict (top-level or .cursor-plugin).

    `check_source` gates the plugins[0].source assertion: it only applies to the
    top-level marketplace.json (source must stay "./plugins/craftflow"); the
    .cursor-plugin/marketplace.json legitimately uses source "." and must never be
    flagged for that.
    """
    metadata = block.get("metadata") or {}
    marketplace_version = metadata.get("version")
    if marketplace_version != version:
        errors.append(
            f"{label} metadata.version ({marketplace_version!r}) does not match "
            f"plugin.json ({version!r})"
        )

    plugins = block.get("plugins") or []
    if not plugins:
        errors.append(f"{label} has no plugins entries")
        return

    entry = plugins[0]
    if entry.get("version") != version:
        errors.append(
            f"{label} plugin entry version ({entry.get('version')!r}) does not "
            f"match plugin.json ({version!r})"
        )
    if check_source and entry.get("source") != EXPECTED_MARKETPLACE_SOURCE:
        errors.append(
            f"{label} plugin source changed unexpectedly ({entry.get('source')!r})"
        )


def evaluate_consistency(snapshot, allow_missing_changelog=False, expect_version=None):
    """Pure. snapshot keys: version, readme, changelog (str|None),
    marketplace, cursor_plugin, cursor_marketplace. Returns list[str] of errors."""
    errors = []

    version = snapshot.get("version")
    readme = snapshot.get("readme") or ""
    changelog = snapshot.get("changelog")
    marketplace = snapshot.get("marketplace") or {}
    cursor_plugin = snapshot.get("cursor_plugin") or {}
    cursor_marketplace = snapshot.get("cursor_marketplace") or {}

    # README.md must carry exactly one unambiguous "**Current version:**" line
    # that matches plugin.json.
    readme_matches = README_VERSION_RE.findall(readme)
    if len(readme_matches) == 0:
        errors.append("README.md missing '**Current version:**' line")
    elif len(readme_matches) > 1:
        errors.append(
            "README.md has multiple '**Current version:**' lines (ambiguous)"
        )
    elif readme_matches[0].strip() != version:
        errors.append(
            f"README.md current version ({readme_matches[0].strip()!r}) does not "
            f"match plugin.json ({version!r})"
        )

    # CHANGELOG.md is optional (allow_missing_changelog) but must carry a release
    # section for the current version when present.
    if changelog is None:
        if not allow_missing_changelog:
            errors.append("CHANGELOG.md not found")
    elif f"## [{version}]" not in changelog:
        errors.append(f"CHANGELOG.md missing release section for {version}")

    # Top-level .claude-plugin/marketplace.json — source assertion applies here.
    _check_marketplace_block(
        marketplace, version, "marketplace.json", errors, check_source=True
    )

    # metadata.description may embed "craftflow vX.Y.Z"; when it does, it must
    # match. When it embeds no version at all, skip the check silently.
    description = (marketplace.get("metadata") or {}).get("description") or ""
    desc_match = DESCRIPTION_VERSION_RE.search(description)
    if desc_match and desc_match.group(1) != version:
        errors.append(
            f"marketplace.json metadata.description embeds stale version "
            f"({desc_match.group(1)!r}) vs plugin.json ({version!r})"
        )

    # .cursor-plugin/plugin.json version.
    cursor_plugin_version = cursor_plugin.get("version")
    if cursor_plugin_version != version:
        errors.append(
            f".cursor-plugin/plugin.json version ({cursor_plugin_version!r}) does "
            f"not match plugin.json ({version!r})"
        )

    # .cursor-plugin/marketplace.json — source assertion does NOT apply (its
    # plugin entry legitimately uses source ".").
    _check_marketplace_block(
        cursor_marketplace,
        version,
        ".cursor-plugin/marketplace.json",
        errors,
        check_source=False,
    )

    # --expect-version: None means "not requested" (fine). "" is a malformed
    # argument (never treat it as unset) and must always be flagged. A non-empty
    # mismatch is a plain consistency error.
    if expect_version == "":
        errors.append(
            "--expect-version was given as an empty string; this is malformed "
            "(omit the flag to leave it unset, never pass an empty value)"
        )
    elif expect_version is not None and expect_version != version:
        errors.append(
            f"--expect-version ({expect_version!r}) does not match plugin.json "
            f"({version!r})"
        )

    return errors


def _read_json(path: Path):
    """Returns (data, error). Only JSONDecodeError is converted to a readable
    error string here — a missing file is a real setup problem and should
    surface as a normal traceback rather than be silently swallowed."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"{path}: invalid JSON ({exc})"


def build_snapshot(subtree_root: Path):
    """Reads the live directory tree rooted at subtree_root into a snapshot
    dict matching evaluate_consistency's expected shape. Returns
    (snapshot, load_errors)."""
    plugin_root = subtree_root / "plugins" / "craftflow"

    plugin_json_path = plugin_root / ".claude-plugin" / "plugin.json"
    marketplace_json_path = subtree_root / ".claude-plugin" / "marketplace.json"
    cursor_plugin_json_path = plugin_root / ".cursor-plugin" / "plugin.json"
    cursor_marketplace_json_path = plugin_root / ".cursor-plugin" / "marketplace.json"
    readme_path = subtree_root / "README.md"
    changelog_path = subtree_root / "CHANGELOG.md"

    load_errors = []

    plugin, err = _read_json(plugin_json_path)
    if err:
        load_errors.append(err)
    marketplace, err = _read_json(marketplace_json_path)
    if err:
        load_errors.append(err)
    cursor_plugin, err = _read_json(cursor_plugin_json_path)
    if err:
        load_errors.append(err)
    cursor_marketplace, err = _read_json(cursor_marketplace_json_path)
    if err:
        load_errors.append(err)

    readme = readme_path.read_text(encoding="utf-8")
    changelog = (
        changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else None
    )

    snapshot = {
        "version": (plugin or {}).get("version"),
        "readme": readme,
        "changelog": changelog,
        "marketplace": marketplace or {},
        "cursor_plugin": cursor_plugin or {},
        "cursor_marketplace": cursor_marketplace or {},
    }
    return snapshot, load_errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Standalone version-consistency gate for the craftflow plugin."
    )
    parser.add_argument(
        "--subtree-root",
        type=Path,
        default=SUBTREE_ROOT_DEFAULT,
        help="Root of the craftflow-plugin subtree to audit (default: this checkout).",
    )
    parser.add_argument(
        "--expect-version",
        default=None,
        help="Assert plugin.json's version equals this value. Omit to skip the check.",
    )
    parser.add_argument(
        "--allow-missing-changelog",
        action="store_true",
        help="Do not error when CHANGELOG.md is absent.",
    )
    args = parser.parse_args(argv)

    snapshot, load_errors = build_snapshot(args.subtree_root)
    if load_errors:
        for error in load_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    errors = evaluate_consistency(
        snapshot,
        allow_missing_changelog=args.allow_missing_changelog,
        expect_version=args.expect_version,
    )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        # An empty --expect-version is a malformed argument, not an ordinary
        # consistency failure — exit 3 so CI logs can tell them apart.
        if args.expect_version == "":
            return 3
        return 1

    print(f"OK: all version fields consistent at {snapshot['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
