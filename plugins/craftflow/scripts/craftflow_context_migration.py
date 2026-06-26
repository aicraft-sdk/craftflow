#!/usr/bin/env python3
"""Context migration script for CRAFTFLOW version upgrades.

Merges valuable content from previous state version directories into the
current state root on first SessionStart after an upgrade.  Idempotent
via a ``.migrated`` marker file inside the target state root.

On-disk path change (v10 → state):
  If .craftflow/state/ does not exist but .craftflow/v10/ does, this script
  copies everything from v10/ into state/ before proceeding. A .schema-version
  marker is written to record the schema version in the directory rather than
  encoding it in the directory name.
"""
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Set

from craftflow_hooklib import (
    STATE_VERSION,
    extract_bullets,
    load_input,
    log_event,
    normalize_bullet,
    now_iso,
    parse_markdown_sections,
    project_dir,
    session_context,
    state_root,
)

# ---------------------------------------------------------------------------
# Contract headings that are safe to merge into
# ---------------------------------------------------------------------------

PATTERNS_HEADINGS: Set[str] = {
    "User Standards",
    "Architecture Patterns",
    "Code Conventions",
    "File Structure",
    "Testing Patterns",
    "Common Gotchas",
    "API Patterns",
    "Error Handling",
    "Dependencies",
    "Project SKILL_HINTS",
}

ACTIVE_CONTEXT_HEADINGS: Set[str] = {
    "Decisions",
    "Learnings",
}

PROGRESS_HEADINGS: Set[str] = {
    "Completed",
    "Verification",
}

# ---------------------------------------------------------------------------
# Templates — used when a target file does not yet exist
# ---------------------------------------------------------------------------

ACTIVE_CONTEXT_TEMPLATE = """\
# Active Context
<!-- CRAFTFLOW: Do not rename headings. Used as Edit anchors. -->

## Current Focus

## Recent Changes

## Next Steps

## Decisions

## Learnings

## References

## Blockers

## Session Settings
# AUTO_PROCEED: false

## Last Updated
"""

PATTERNS_TEMPLATE = """\
# Project Patterns
<!-- CRAFTFLOW MEMORY CONTRACT: Do not rename headings. Used as Edit anchors. -->

## User Standards

## Architecture Patterns

## Code Conventions

## File Structure

## Testing Patterns

## Common Gotchas

## API Patterns

## Error Handling

## Dependencies

## Project SKILL_HINTS

## Last Updated
"""

PROGRESS_TEMPLATE = """\
# Progress Tracking
<!-- CRAFTFLOW: Do not rename headings. Used as Edit anchors. -->

## Current Workflow

## Tasks

## Completed

## Verification

## Last Updated
"""

# ---------------------------------------------------------------------------
# Schema-version marker helpers
# ---------------------------------------------------------------------------


def _schema_version_path(state_dir: Path) -> Path:
    return state_dir / ".schema-version"


def _read_schema_version(state_dir: Path) -> Dict[str, Any]:
    """Read .schema-version marker, or return empty dict if missing/corrupt."""
    marker = _schema_version_path(state_dir)
    if not marker.exists():
        return {}
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_schema_version(
    state_dir: Path, version: str, migrated_from: List[str]
) -> None:
    """Write (or update) the .schema-version marker file."""
    existing = _read_schema_version(state_dir)
    # Preserve existing migrated_from list and extend it
    prior_sources: List[str] = existing.get("migrated_from", [])
    combined: List[str] = list(dict.fromkeys(prior_sources + migrated_from))
    marker = _schema_version_path(state_dir)
    data = {
        "version": version,
        "migrated_from": combined,
        "migrated_at": now_iso(),
    }
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
    tmp.replace(marker)


def _schema_version_number(state_dir: Path) -> int:
    """Return the integer version from .schema-version (e.g. "v10" -> 10).

    Falls back to 0 if the marker is absent or unparseable.
    """
    info = _read_schema_version(state_dir)
    ver = info.get("version", "")
    if isinstance(ver, str) and ver.startswith("v") and ver[1:].isdigit():
        return int(ver[1:])
    return 0


# ---------------------------------------------------------------------------
# Legacy directory migration (v10 → state)
# ---------------------------------------------------------------------------


def _migrate_legacy_dirs_to_state(craftflow_base: Path, state_dir: Path) -> List[str]:
    """Copy .craftflow/v10/ (and v10-from-cc10x-root/) into .craftflow/state/.

    This is a one-time migration triggered when state/ does not yet exist.
    Returns a list of source labels that were merged.

    The .schema-version marker is written here (atomically with the copy) so
    that a failure in the caller after copytree succeeds cannot leave state/
    in a marker-less state that causes migrated_from to be lost on the next run.
    """
    migrated_from: List[str] = []

    legacy_v10 = craftflow_base / "v10"
    if legacy_v10.is_dir():
        state_dir.mkdir(parents=True, exist_ok=True)
        # Copy all files from v10/ into state/, overwriting nothing that
        # already exists (state/ was just created, so dirs will be new).
        shutil.copytree(str(legacy_v10), str(state_dir), dirs_exist_ok=True)
        migrated_from.append("v10")
        log_event(
            "legacy_dir_migration",
            {
                "source": str(legacy_v10),
                "target": str(state_dir),
                "decision": "copytree",
                "reason": "state_root_rename_v10_to_state",
            },
        )

    # Companion dir from cc10x-root (only files not already in state/)
    legacy_cc10x = craftflow_base / "v10-from-cc10x-root"
    if legacy_cc10x.is_dir() and state_dir.exists():
        _merge_dir_no_overwrite(legacy_cc10x, state_dir)
        migrated_from.append("v10-from-cc10x-root")
        log_event(
            "legacy_dir_migration",
            {
                "source": str(legacy_cc10x),
                "target": str(state_dir),
                "decision": "merge_no_overwrite",
                "reason": "companion_dir_fold_in",
            },
        )

    # Write schema-version marker atomically with the copy so that a crash
    # between copytree() and the caller's _write_schema_version() call cannot
    # leave state/ without a marker (which would cause migrated_from to be lost).
    if migrated_from or state_dir.exists():
        state_dir.mkdir(parents=True, exist_ok=True)
        _write_schema_version(state_dir, STATE_VERSION, migrated_from)

    return migrated_from


def _merge_dir_no_overwrite(src: Path, dst: Path) -> None:
    """Recursively copy files from src into dst, skipping files that already exist."""
    for item in src.rglob("*"):
        if item.is_file():
            rel = item.relative_to(src)
            target = dst / rel
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(item), str(target))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _version_sort_key(dirname: str) -> int:
    """Extract numeric version from ``vN`` directory name."""
    if dirname.startswith("v") and dirname[1:].isdigit():
        return int(dirname[1:])
    return -1  # legacy (root) sorts first


def _current_version_number() -> int:
    """Return the integer portion of STATE_VERSION (e.g. ``v10`` -> 10)."""
    if STATE_VERSION.startswith("v") and STATE_VERSION[1:].isdigit():
        return int(STATE_VERSION[1:])
    return 0


def _migrate_flat_to_project(target_root: Path) -> int:
    """One-time: copy root-flat memory files into project/ if not yet done.

    This is an intra-state migration for repos that existed before the
    project/ namespace was introduced. It copies (not moves) so that the
    root-flat fallback remains intact for backward compatibility.
    """
    marker = target_root / ".project-namespace-migrated"
    if marker.exists():
        return 0
    project_path = target_root / "project"
    project_path.mkdir(exist_ok=True)
    count = 0
    for name in ("activeContext.md", "patterns.md", "progress.md"):
        src = target_root / name
        dst = project_path / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            count += 1
    marker.write_text(now_iso(), encoding="utf-8")
    return count


def _discover_sources(craftflow_base: Path) -> List[Dict[str, Any]]:
    """Find all migration sources ordered oldest-first.

    Note: since the state root is now named 'state' (not a versioned dir),
    we look at .schema-version inside it to determine the current version number.
    """
    sources: List[Dict[str, Any]] = []
    current_num = _current_version_number()

    # Legacy root-level files
    if (craftflow_base / "activeContext.md").exists():
        sources.append({"label": "legacy", "path": craftflow_base, "sort_key": -1})

    # Versioned directories (skip namespace subdirs and the new 'state' dir)
    _SKIP_DIRS = {"project", "workflows", "state"}
    if craftflow_base.is_dir():
        for child in sorted(craftflow_base.iterdir()):
            if not child.is_dir():
                continue
            name = child.name
            if name in _SKIP_DIRS:
                continue
            if not (name.startswith("v") and name[1:].isdigit()):
                continue
            ver_num = int(name[1:])
            if ver_num >= current_num:
                continue  # skip current and future versions
            # Must have at least one memory file
            if any((child / f).exists() for f in ("activeContext.md", "patterns.md", "progress.md")):
                sources.append({"label": name, "path": child, "sort_key": ver_num})

    sources.sort(key=lambda s: s["sort_key"])
    return sources


def _load_migrated(target_root: Path) -> Dict[str, Any]:
    """Load the .migrated marker file, or return empty structure."""
    marker = target_root / ".migrated"
    if not marker.exists():
        return {"version": STATE_VERSION, "migrations": []}
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return {"version": STATE_VERSION, "migrations": []}


def _save_migrated(target_root: Path, data: Dict[str, Any]) -> None:
    """Atomically write the .migrated marker."""
    marker = target_root / ".migrated"
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
    tmp.replace(marker)


def _already_migrated(migrated: Dict[str, Any], label: str) -> bool:
    """Check if a source has already been migrated."""
    return any(m.get("source") == label for m in migrated.get("migrations", []))


def _ensure_file(path: Path, template: str) -> None:
    """Create a memory file from template if it doesn't exist."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template, encoding="utf-8")


def _backup(path: Path) -> None:
    """Create a .pre-migration.bak copy if the file exists."""
    if path.exists():
        bak = path.with_suffix(".pre-migration.bak")
        if not bak.exists():  # don't overwrite an existing backup
            shutil.copy2(path, bak)


def _merge_sections(
    source_text: str,
    target_text: str,
    allowed_headings: Set[str],
) -> tuple[str, int]:
    """Merge bullets from source sections into target sections.

    Returns the updated target text and the count of bullets added.
    """
    source_sections = parse_markdown_sections(source_text)
    target_sections = parse_markdown_sections(target_text)

    total_added = 0

    for heading in allowed_headings:
        if heading not in source_sections:
            continue
        source_bullets = extract_bullets(source_sections[heading])
        if not source_bullets:
            continue

        # Build dedup set from existing target bullets
        existing: Set[str] = set()
        if heading in target_sections:
            for b in extract_bullets(target_sections[heading]):
                existing.add(normalize_bullet(b))

        new_bullets: List[str] = []
        for bullet in source_bullets:
            norm = normalize_bullet(bullet)
            if norm and norm not in existing:
                new_bullets.append(bullet.rstrip())
                existing.add(norm)

        if not new_bullets:
            continue

        total_added += len(new_bullets)

        # Insert new bullets into the target text after the heading line
        anchor = f"## {heading}"
        if anchor in target_text:
            insert_block = "\n".join(new_bullets)
            target_text = target_text.replace(
                anchor, f"{anchor}\n{insert_block}", 1
            )
        # If the heading doesn't exist in target but is in the contract,
        # insert it before ## Last Updated
        elif "## Last Updated" in target_text:
            section_block = f"## {heading}\n" + "\n".join(new_bullets) + "\n\n"
            target_text = target_text.replace(
                "## Last Updated", f"{section_block}## Last Updated", 1
            )

    return target_text, total_added


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_migration() -> int:
    """Run migration. Idempotent — safe to call on every SessionStart."""
    data = load_input()
    _ = data  # consumed for hook contract compliance

    craftflow_base = project_dir() / ".craftflow"
    state_dir = craftflow_base / "state"

    # ------------------------------------------------------------------
    # Step 1: Auto-migrate legacy .craftflow/v10/ → .craftflow/state/
    # This only triggers when state/ does not yet exist.
    # _migrate_legacy_dirs_to_state writes the schema-version marker
    # atomically with the copy, so no separate call is needed here.
    # ------------------------------------------------------------------
    newly_created = False
    if not state_dir.exists():
        _migrate_legacy_dirs_to_state(craftflow_base, state_dir)
        newly_created = True
    else:
        # Sentinel: state/ exists but .schema-version is missing (partial
        # migration recovery — e.g. previous run crashed between copytree
        # and the marker write). Self-heal by writing the marker now.
        sv = _read_schema_version(state_dir)
        if not sv:
            _write_schema_version(state_dir, STATE_VERSION, [])

    # Now derive target_root from the hooklib function (resolves to state/)
    target_root = state_root()

    # ------------------------------------------------------------------
    # Step 2: Intra-state migration — promote root-flat files into project/
    # ------------------------------------------------------------------
    promoted = _migrate_flat_to_project(target_root)
    if promoted > 0:
        log_event(
            "context_migration",
            {
                "source": "flat-root",
                "target": "state/project",
                "files_merged": ["activeContext.md", "patterns.md", "progress.md"],
                "bullets_added": 0,
                "decision": "copy",
                "reason": "project_namespace_init",
            },
        )

    # ------------------------------------------------------------------
    # Step 3: Legacy claude-path migration (old .claude/craftflow/ path)
    # ------------------------------------------------------------------
    claude_craftflow_base = project_dir() / ".claude" / "craftflow"

    if not claude_craftflow_base.exists():
        return 0  # no legacy .claude/craftflow path present

    sources = _discover_sources(claude_craftflow_base)
    if not sources:
        return 0  # nothing to migrate

    migrated = _load_migrated(target_root)
    pending = [s for s in sources if not _already_migrated(migrated, s["label"])]

    if not pending:
        return 0  # all sources already migrated — idempotent

    # Ensure target files exist with proper templates
    _ensure_file(target_root / "patterns.md", PATTERNS_TEMPLATE)
    _ensure_file(target_root / "activeContext.md", ACTIVE_CONTEXT_TEMPLATE)
    _ensure_file(target_root / "progress.md", PROGRESS_TEMPLATE)

    # Back up target files before any modifications
    _backup(target_root / "patterns.md")
    _backup(target_root / "activeContext.md")
    _backup(target_root / "progress.md")

    total_bullets = 0
    migrated_labels: List[str] = []

    for source in pending:
        source_path: Path = source["path"]
        label: str = source["label"]
        source_bullets = 0
        files_merged: List[str] = []

        # --- patterns.md (full section merge) ---
        src_patterns = source_path / "patterns.md"
        if src_patterns.exists():
            tgt_text = (target_root / "patterns.md").read_text(encoding="utf-8")
            src_text = src_patterns.read_text(encoding="utf-8")
            updated, count = _merge_sections(src_text, tgt_text, PATTERNS_HEADINGS)
            if count > 0:
                tmp = (target_root / "patterns.md").with_suffix(".tmp")
                tmp.write_text(updated, encoding="utf-8")
                tmp.replace(target_root / "patterns.md")
                files_merged.append("patterns.md")
                source_bullets += count

        # --- activeContext.md (Decisions + Learnings only) ---
        src_active = source_path / "activeContext.md"
        if src_active.exists():
            tgt_text = (target_root / "activeContext.md").read_text(encoding="utf-8")
            src_text = src_active.read_text(encoding="utf-8")
            updated, count = _merge_sections(src_text, tgt_text, ACTIVE_CONTEXT_HEADINGS)
            if count > 0:
                tmp = (target_root / "activeContext.md").with_suffix(".tmp")
                tmp.write_text(updated, encoding="utf-8")
                tmp.replace(target_root / "activeContext.md")
                files_merged.append("activeContext.md")
                source_bullets += count

        # --- progress.md (Completed + Verification only) ---
        src_progress = source_path / "progress.md"
        if src_progress.exists():
            tgt_text = (target_root / "progress.md").read_text(encoding="utf-8")
            src_text = src_progress.read_text(encoding="utf-8")
            updated, count = _merge_sections(src_text, tgt_text, PROGRESS_HEADINGS)
            if count > 0:
                tmp = (target_root / "progress.md").with_suffix(".tmp")
                tmp.write_text(updated, encoding="utf-8")
                tmp.replace(target_root / "progress.md")
                files_merged.append("progress.md")
                source_bullets += count

        # Record migration
        migration_record = {
            "source": label,
            "timestamp": now_iso(),
            "files_merged": files_merged,
            "bullets_added": source_bullets,
        }
        migrated["migrations"].append(migration_record)
        _save_migrated(target_root, migrated)

        total_bullets += source_bullets
        migrated_labels.append(label)

        log_event(
            "context_migration",
            {
                "source": label,
                "target": "state",
                "files_merged": files_merged,
                "bullets_added": source_bullets,
                "decision": "merge",
                "reason": "version_upgrade",
            },
        )

    # Emit session context so Claude knows migration occurred
    if total_bullets > 0:
        sources_str = ", ".join(migrated_labels)
        session_context(
            f"CRAFTFLOW context migration: merged {total_bullets} items from "
            f"[{sources_str}] into {STATE_VERSION}. "
            f"Historical decisions, learnings, patterns, and verification "
            f"evidence have been preserved."
        )

    return 0


def main() -> int:
    return run_migration()


if __name__ == "__main__":
    raise SystemExit(main())
