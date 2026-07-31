#!/usr/bin/env python3
"""
craftflow_memory_repair.py

One-time repair for the three corrupted CRAFTFLOW project-tier memory files
(`patterns.md`, `progress.md`, `activeContext.md`) — Phase 3 of "Fix
Craftflow's memory persistence: section-anchored merge, enforced caps,
one-time repair".

Corruption fixed here:
  1. `patterns.md`: 4 contract-required anchors (`API Patterns`,
     `Error Handling`, `Dependencies`, `Project SKILL_HINTS`) were demoted
     to bare text (lost their `## ` prefix) by a pre-Phase-1 merger bug.
  2. `patterns.md` / `progress.md`: bullets that were relocated below the
     `## Last Updated` heading by the same bug are reattached to their
     home section (`## Common Gotchas` / `## Completed`).
  3. `patterns.md` / `progress.md`: bullets with chained `(conf: X) (conf: Y)`
     suffixes are collapsed to the single, most-recent suffix.
  4. `activeContext.md`: legacy `[cc10x-internal] memory_task_id: ...` orphan
     lines (pre-rename prefix) are deleted, and duplicate
     `- Plan: <path>` lines in `## References` are deduplicated (keep first).
  5. Caps: `patterns.md ## Common Gotchas` and `progress.md ## Completed`
     are each capped at 60 bullets (oldest-first eviction, via the same
     `apply_cap` Phase 1 already shipped).

Usage:
    python3 craftflow_memory_repair.py --target-dir DIR [--apply]

--target-dir is REQUIRED. This script has no way to distinguish a real,
live state directory from a scratch/test one — the caller is always
responsible for pointing it at the right place. There is no default and
no "find the project root for me" fallback; a missing or wrong
--target-dir on a destructive (--apply) run would silently overwrite
files that are gitignored and unrecoverable via git.

Default (no --apply): dry run. Prints a full unified diff per file to
stdout. Writes nothing to disk.

--apply: backs up all 3 target files first, to
    <parent-of-target-dir>/.memory-repair-backup-<UTC-ISO-compact>[-N]/
(a numeric suffix is appended if a backup dir for the same UTC-second
timestamp already exists, so two --apply runs never share/overwrite a
backup directory) then writes the repaired content. If the backup step
fails for any file, aborts before writing anything.

Exit 0 on success (dry-run or apply), non-zero on any error (e.g. a target
file doesn't exist, or a required heading can't be found).
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import os
import re
import shutil
import sys
from typing import List, Tuple

from craftflow_memory_merge import apply_cap

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FILES = ["patterns.md", "progress.md", "activeContext.md"]

DEMOTED_ANCHORS = ["API Patterns", "Error Handling", "Dependencies", "Project SKILL_HINTS"]

LAST_UPDATED_HEADING = "## Last Updated"

PATTERNS_TARGET_SECTION = "Common Gotchas"
PROGRESS_TARGET_SECTION = "Completed"
COMMON_GOTCHAS_CAP = 60
COMPLETED_CAP = 60

CC10X_ORPHAN_RE = re.compile(r"^\s*-\s*\[cc10x-internal\]\s*memory_task_id:")
PLAN_LINE_RE = re.compile(r"^- Plan: `([^`]+)`")
_CONF_SUFFIX_RE = re.compile(r"\(conf:\s*[\d.]+\)")
_SKILL_ID_PLACEHOLDER = "[full-skill-id]"
_WHITESPACE_RE = re.compile(r"\s")

BULLET_COUNT_RE = re.compile(r"(?m)^- ")


def is_bullet(line: str) -> bool:
    return line.lstrip().startswith("- ")


def is_skill_id_bullet(line: str) -> bool:
    """
    True if a bullet's text (after the leading '- ') is shaped like a real
    skill-id entry: a single token with no internal whitespace (e.g.
    `craftflow:some-skill-name`, `plugin:skill`), or the literal template
    placeholder `[full-skill-id]`. A bullet containing any whitespace (a
    full sentence/paragraph) is never a skill id.
    """
    if not is_bullet(line):
        return False
    text = line.lstrip()[2:].strip()
    if not text:
        return False
    if text == _SKILL_ID_PLACEHOLDER:
        return True
    return not _WHITESPACE_RE.search(text)


# ---------------------------------------------------------------------------
# Step 1: restore demoted anchors (literal bare-line match only)
# ---------------------------------------------------------------------------


def restore_demoted_anchors(lines: List[str], anchors: List[str] = None) -> List[str]:
    anchor_set = set(anchors if anchors is not None else DEMOTED_ANCHORS)
    return [f"## {line}" if line in anchor_set else line for line in lines]


# ---------------------------------------------------------------------------
# Heading helpers (surgical splicing on the restored/known heading text —
# not a reimplementation of bullet parsing; extract_bullets/normalize_bullet
# from craftflow_hooklib and apply_cap from craftflow_memory_merge are
# reused for the bullet-level operations below).
# ---------------------------------------------------------------------------


def _find_heading_index(lines: List[str], heading_text: str, last: bool = False):
    found = None
    for i, line in enumerate(lines):
        if line.rstrip() == heading_text:
            found = i
            if not last:
                return found
    return found


def _section_end(lines: List[str], start: int) -> int:
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            return j
    return len(lines)


# ---------------------------------------------------------------------------
# Shared splice helper: append bullets to the end of a section's bullet list
# ---------------------------------------------------------------------------


def _append_bullets_to_section(lines: List[str], section: str, bullets: List[str]) -> List[str]:
    """
    Insert bullets right after section's last existing bullet (or right
    after the heading if the section has none yet). Shared by
    move_stranded_bullets and relocate_misfiled_anchor_bullets so the
    splice mechanism isn't duplicated.
    """
    heading_line = f"## {section}"
    start = _find_heading_index(lines, heading_line)
    if start is None:
        raise ValueError(f"Section '{heading_line}' not found")
    end = _section_end(lines, start)

    last_bullet_idx = start
    for j in range(start + 1, end):
        if is_bullet(lines[j]):
            last_bullet_idx = j
    insert_at = last_bullet_idx + 1

    return lines[:insert_at] + bullets + lines[insert_at:]


# ---------------------------------------------------------------------------
# Step 2: reattach bullets stranded below the last "## Last Updated" heading
# ---------------------------------------------------------------------------


def move_stranded_bullets(lines: List[str], target_section: str) -> Tuple[List[str], List[str]]:
    """
    Move every '- ' bullet line sitting after the LAST '## Last Updated'
    heading to the end of target_section's bullet list (inserted right
    after that section's last existing bullet, preserving relative order
    of both the untouched section content and the moved bullets).

    Non-bullet lines after '## Last Updated' are left in place, in their
    original relative order.

    Returns (new_lines, moved_bullets).
    """
    lu_idx = _find_heading_index(lines, LAST_UPDATED_HEADING, last=True)
    if lu_idx is None:
        raise ValueError(f"'{LAST_UPDATED_HEADING}' heading not found")

    head = lines[: lu_idx + 1]
    tail = lines[lu_idx + 1 :]

    stranded = [line for line in tail if is_bullet(line)]
    kept_tail = [line for line in tail if not is_bullet(line)]

    new_lines = head + kept_tail
    if not stranded:
        return new_lines, stranded

    new_lines = _append_bullets_to_section(new_lines, target_section, stranded)
    return new_lines, stranded


# ---------------------------------------------------------------------------
# Step 2.5: relocate bullets misfiled under a restored (formerly demoted)
# anchor section that are NOT genuinely skill-id-shaped
# ---------------------------------------------------------------------------


def relocate_misfiled_anchor_bullets(
    lines: List[str], anchor_sections: List[str], target_section: str
) -> Tuple[List[str], List[str]]:
    """
    For each section named in anchor_sections, scan its bullet list and
    relocate every bullet that is NOT skill-id-shaped (per
    is_skill_id_bullet) to the end of target_section's bullet list.
    Skill-id-shaped bullets are left in place. Missing anchor sections are
    skipped (not an error) since not every corrupted file has all four.

    Returns (new_lines, relocated_bullets) in document order across all
    anchor_sections.
    """
    remove_indices = set()
    relocated: List[str] = []
    for section in anchor_sections:
        heading_line = f"## {section}"
        start = _find_heading_index(lines, heading_line)
        if start is None:
            continue
        end = _section_end(lines, start)
        for j in range(start + 1, end):
            if is_bullet(lines[j]) and not is_skill_id_bullet(lines[j]):
                remove_indices.add(j)
                relocated.append(lines[j])

    if not relocated:
        return lines, relocated

    kept_lines = [line for i, line in enumerate(lines) if i not in remove_indices]
    new_lines = _append_bullets_to_section(kept_lines, target_section, relocated)
    return new_lines, relocated


# ---------------------------------------------------------------------------
# Step 3: collapse chained "(conf: X) (conf: Y)" suffixes to the last one
# ---------------------------------------------------------------------------


def collapse_confidence_suffixes(line: str) -> str:
    """
    Collapse 2+ chained '(conf: X)' suffixes at the end of a bullet line to
    just the last (rightmost, most-recently-appended) one. No-op if 0 or 1
    suffix is present, or if the trailing text starting at the first
    suffix match is not itself a clean chain of suffixes.
    """
    matches = list(_CONF_SUFFIX_RE.finditer(line))
    if len(matches) < 2:
        return line

    tail = line[matches[0].start() :]
    if not re.fullmatch(r"(?:\s*\(conf:\s*[\d.]+\))+\s*", tail):
        return line

    base = line[: matches[0].start()].rstrip()
    last_conf = matches[-1].group(0)
    return f"{base} {last_conf}"


# ---------------------------------------------------------------------------
# Step 5: cap a section's bullet list (oldest-first eviction via apply_cap)
# ---------------------------------------------------------------------------


def cap_section_bullets(lines: List[str], section: str, max_bullets: int) -> Tuple[List[str], int]:
    heading_line = f"## {section}"
    start = _find_heading_index(lines, heading_line)
    if start is None:
        raise ValueError(f"Section '{heading_line}' not found")
    end = _section_end(lines, start)

    bullet_positions = [j for j in range(start + 1, end) if is_bullet(lines[j])]
    bullets = [lines[j] for j in bullet_positions]
    capped = apply_cap(bullets, max_bullets)
    evicted_count = len(bullets) - len(capped)
    if evicted_count <= 0:
        return lines, 0

    evict_positions = set(bullet_positions[:evicted_count])
    new_lines = [line for i, line in enumerate(lines) if i not in evict_positions]
    return new_lines, evicted_count


# ---------------------------------------------------------------------------
# Step 4: activeContext.md cleanup (orphans + duplicate Plan lines)
# ---------------------------------------------------------------------------


def remove_legacy_internal_orphans(lines: List[str]) -> Tuple[List[str], List[str]]:
    """Delete every '- [cc10x-internal] memory_task_id: ...' line entirely."""
    kept, removed = [], []
    for line in lines:
        if CC10X_ORPHAN_RE.match(line):
            removed.append(line)
        else:
            kept.append(line)
    return kept, removed


def dedupe_plan_lines(lines: List[str], section: str = "References") -> Tuple[List[str], List[str]]:
    """
    Deduplicate '- Plan: `<path>`' lines within the named section's body —
    if the exact same path appears 2+ times, keep only the first occurrence.
    Lines with genuinely different paths are untouched.
    """
    heading_line = f"## {section}"
    start = _find_heading_index(lines, heading_line)
    if start is None:
        raise ValueError(f"Section '{heading_line}' not found")
    end = _section_end(lines, start)

    seen = set()
    dupe_indices = []
    removed = []
    for i in range(start + 1, end):
        match = PLAN_LINE_RE.match(lines[i])
        if not match:
            continue
        path = match.group(1)
        if path in seen:
            dupe_indices.append(i)
            removed.append(lines[i])
        else:
            seen.add(path)

    dupe_set = set(dupe_indices)
    new_lines = [line for i, line in enumerate(lines) if i not in dupe_set]
    return new_lines, removed


# ---------------------------------------------------------------------------
# Per-file repair entry points
# ---------------------------------------------------------------------------


def _count_bullets(text: str) -> int:
    return len(BULLET_COUNT_RE.findall(text))


def repair_patterns_md(text: str) -> Tuple[str, dict]:
    original_bullet_count = _count_bullets(text)

    lines = text.split("\n")
    lines = restore_demoted_anchors(lines)
    lines, moved = move_stranded_bullets(lines, PATTERNS_TARGET_SECTION)
    lines, relocated = relocate_misfiled_anchor_bullets(lines, DEMOTED_ANCHORS, PATTERNS_TARGET_SECTION)
    lines = [
        collapse_confidence_suffixes(line) if is_bullet(line) else line for line in lines
    ]
    lines, evicted = cap_section_bullets(lines, PATTERNS_TARGET_SECTION, COMMON_GOTCHAS_CAP)

    new_text = "\n".join(lines)
    repaired_bullet_count = _count_bullets(new_text)
    report = {
        "moved_count": len(moved),
        "relocated_count": len(relocated),
        "evicted_count": evicted,
        "original_bullet_count": original_bullet_count,
        "repaired_bullet_count": repaired_bullet_count,
        "zero_data_loss_ok": original_bullet_count == repaired_bullet_count + evicted,
    }
    return new_text, report


def repair_progress_md(text: str) -> Tuple[str, dict]:
    original_bullet_count = _count_bullets(text)

    lines = text.split("\n")
    lines, moved = move_stranded_bullets(lines, PROGRESS_TARGET_SECTION)
    lines = [
        collapse_confidence_suffixes(line) if is_bullet(line) else line for line in lines
    ]
    lines, evicted = cap_section_bullets(lines, PROGRESS_TARGET_SECTION, COMPLETED_CAP)

    new_text = "\n".join(lines)
    repaired_bullet_count = _count_bullets(new_text)
    report = {
        "moved_count": len(moved),
        "evicted_count": evicted,
        "original_bullet_count": original_bullet_count,
        "repaired_bullet_count": repaired_bullet_count,
        "zero_data_loss_ok": original_bullet_count == repaired_bullet_count + evicted,
    }
    return new_text, report


def repair_active_context_md(text: str) -> Tuple[str, dict]:
    original_bullet_count = _count_bullets(text)

    lines = text.split("\n")
    lines, removed_orphans = remove_legacy_internal_orphans(lines)
    lines, removed_dupes = dedupe_plan_lines(lines)

    new_text = "\n".join(lines)
    repaired_bullet_count = _count_bullets(new_text)
    removed_count = len(removed_orphans) + len(removed_dupes)
    report = {
        "orphans_removed": len(removed_orphans),
        "duplicates_removed": len(removed_dupes),
        "original_bullet_count": original_bullet_count,
        "repaired_bullet_count": repaired_bullet_count,
        "zero_data_loss_ok": original_bullet_count == repaired_bullet_count + removed_count,
    }
    return new_text, report


REPAIRERS = {
    "patterns.md": repair_patterns_md,
    "progress.md": repair_progress_md,
    "activeContext.md": repair_active_context_md,
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _unique_backup_dir(parent_dir: str, timestamp: str) -> str:
    """
    Compute a backup directory path for the given UTC timestamp, appending
    a numeric suffix (-1, -2, ...) if a directory for that exact timestamp
    already exists, so two --apply runs within the same UTC second never
    share or overwrite a backup directory.
    """
    base = os.path.join(parent_dir, f".memory-repair-backup-{timestamp}")
    candidate = base
    suffix = 0
    while os.path.exists(candidate):
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One-time repair for craftflow project-tier memory files.")
    parser.add_argument(
        "--target-dir",
        required=True,
        help="Directory containing the 3 target files. REQUIRED — this script cannot tell a real "
        "state directory from a scratch one; the caller must always point it at the right place.",
    )
    parser.add_argument("--apply", action="store_true", help="Write repaired content (default: dry-run diff only).")
    args = parser.parse_args(argv)

    target_dir = args.target_dir

    results = []
    for fname in FILES:
        path = os.path.join(target_dir, fname)
        if not os.path.isfile(path):
            sys.stderr.write(f"Error: target file not found: {path}\n")
            return 1

        with open(path, "r", encoding="utf-8") as f:
            original_text = f.read()

        repairer = REPAIRERS[fname]
        try:
            repaired_text, report = repairer(original_text)
        except ValueError as exc:
            sys.stderr.write(f"Error repairing {fname}: {exc}\n")
            return 1

        results.append((path, fname, original_text, repaired_text, report))

    for path, fname, original_text, repaired_text, report in results:
        diff = difflib.unified_diff(
            original_text.splitlines(keepends=True),
            repaired_text.splitlines(keepends=True),
            fromfile=f"{path} (original)",
            tofile=f"{path} (repaired)",
        )
        print(f"=== {fname} ===")
        print(f"Report: {report}")
        sys.stdout.writelines(diff)
        print()

    if not args.apply:
        print("Dry run complete. No files written. Pass --apply to write changes.")
        return 0

    parent_dir = os.path.dirname(os.path.normpath(target_dir))
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = _unique_backup_dir(parent_dir, timestamp)

    try:
        os.makedirs(backup_dir, exist_ok=True)
        for _, fname, _, _, _ in results:
            src = os.path.join(target_dir, fname)
            dst = os.path.join(backup_dir, fname)
            shutil.copy2(src, dst)
    except OSError as exc:
        sys.stderr.write(f"Error: backup failed, aborting before any writes: {exc}\n")
        return 1

    for path, _, _, repaired_text, _ in results:
        with open(path, "w", encoding="utf-8") as f:
            f.write(repaired_text)

    print(f"Applied. Backup written to {backup_dir}")
    print(
        "IMPORTANT: this backup is only gitignored, not git-tracked — copy it "
        "outside the repository before running any git hygiene command that "
        "can remove gitignored files (e.g. `git clean -fdx`, unlike plain "
        "`git clean -fd` which respects .gitignore)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
