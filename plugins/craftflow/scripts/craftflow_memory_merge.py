#!/usr/bin/env python3
"""
craftflow_memory_merge.py

Deterministic, confidence-aware markdown bullet merger.

Usage (CLI mode):
    python3 craftflow_memory_merge.py < payload.json

Input JSON (stdin) — section-anchored mode (preferred; Python owns the
section boundary instead of trusting an LLM-supplied span):
    {
      "file_text": "full content of the memory file",
      "section": "Common Gotchas",
      "notes": [
        {"text": "new insight", "confidence": 0.9}
      ],
      "retractions": ["old note to remove"],
      "max_bullets": 60
    }
    Output (stdout): the FULL file text, with only the named section's body
    replaced.

Input JSON (stdin) — legacy mode (caller supplies the section span directly;
kept working for callers that have not yet migrated to section-anchored):
    {
      "section_text": "current content of the target ## section",
      "notes": [
        {"text": "new insight", "confidence": 0.9}
      ],
      "retractions": ["old note to remove"],
      "max_bullets": 60
    }
    Output (stdout): merged section_text string.

"max_bullets" is optional in both modes; when present, the bullet list is
capped post-merge with oldest-first eviction.

Exit 0 on success, 1 on error.
"""
import json
import re
import sys

from craftflow_hooklib import extract_bullets, normalize_bullet


def parse_confidence(line: str) -> float:
    """Extract (conf: N.N) suffix from a bullet line. Returns float or 0.8 if absent."""
    match = re.search(r"\(conf:\s*([\d.]+)\)\s*$", line)
    if match:
        return float(match.group(1))
    return 0.8


def strip_confidence_suffix(line: str) -> str:
    """Remove ' (conf: N.N)' suffix if present. Returns clean text."""
    return re.sub(r"\s*\(conf:\s*[\d.]+\)\s*$", "", line)


def merge_bullet(existing_bullets: list, new_text: str, confidence: float) -> list:
    """
    Merge a new note into the existing bullet list with confidence-aware superseding.

    - confidence < 0.7: drop (return existing unchanged)
    - If normalize_bullet match found:
        - new confidence >= existing confidence: replace existing line
        - new confidence < existing confidence: skip (keep old)
    - If no match: append new bullet with (conf: x) suffix
    """
    if confidence < 0.7:
        return existing_bullets

    # Strip any pre-existing confidence suffix from new_text before normalizing
    # or formatting — symmetric with how existing bullets are handled below.
    # Without this, an already-suffixed note (e.g. text ending "(conf: 0.9)")
    # never matches its existing counterpart and acquires a second suffix.
    clean_new = strip_confidence_suffix(new_text)
    norm_new = normalize_bullet(clean_new)
    result = list(existing_bullets)

    for i, bullet in enumerate(result):
        existing_clean = strip_confidence_suffix(bullet)
        norm_existing = normalize_bullet(existing_clean)
        if norm_existing == norm_new:
            existing_conf = parse_confidence(bullet)
            if confidence >= existing_conf:
                result[i] = f"- {clean_new} (conf: {confidence})"
            # If new confidence < existing, keep old — do nothing
            return result

    # No match found — append
    result.append(f"- {clean_new} (conf: {confidence})")
    return result


def apply_retractions(section_body: str, retractions: list) -> str:
    """
    Remove bullets from section_body whose normalized text matches any retraction.

    - Split section_body into lines
    - For each bullet line, normalize its text (strip confidence suffix first)
    - If it matches any retraction's normalized form, remove the line
    - Rejoin with newline and return
    """
    if not retractions or not section_body:
        return section_body

    norm_retractions = {normalize_bullet(r) for r in retractions}
    lines = section_body.split("\n")
    kept = []
    for line in lines:
        if line.lstrip().startswith("- "):
            clean = normalize_bullet(strip_confidence_suffix(line))
            if clean in norm_retractions:
                continue
        kept.append(line)

    # Rejoin and strip trailing empty lines introduced by removal
    result = "\n".join(kept)
    # Remove leading/trailing blank lines
    return result.strip()


def _reconstruct_section(section_text: str, merged_bullets: list) -> str:
    """
    Replace the bullet lines in section_text with merged_bullets,
    preserving non-bullet lines in order.
    """
    lines = section_text.split("\n") if section_text else []
    non_bullet_lines = [
        line for line in lines if not line.lstrip().startswith("- ")
    ]
    # Combine non-bullet lines (above the bullet block) with merged bullets
    all_lines = non_bullet_lines + merged_bullets
    # Strip leading/trailing blank lines
    result = "\n".join(all_lines).strip()
    return result


def apply_cap(bullets: list, max_bullets) -> list:
    """
    Trim a bullet list to at most max_bullets entries, evicting oldest-first.

    Bullets are stored in chronological (oldest-first) order — earlier
    entries were added first, later entries appended after. Eviction removes
    from the front of the list. On ties in age (not possible given unique
    list positions, but kept for determinism), lowest confidence is evicted
    first.

    - max_bullets is None: no-op, return bullets unchanged
    - len(bullets) <= max_bullets: no-op, return bullets unchanged
    """
    if max_bullets is None or len(bullets) <= max_bullets:
        return bullets

    excess = len(bullets) - max_bullets
    indexed = sorted(
        range(len(bullets)), key=lambda i: (i, parse_confidence(bullets[i]))
    )
    evict_indices = set(indexed[:excess])
    return [bullet for i, bullet in enumerate(bullets) if i not in evict_indices]


def _normalize_notes(raw_notes: list) -> list:
    """Normalize raw note entries (plain strings or dicts) to {"text", "confidence"} dicts."""
    notes = []
    for item in raw_notes:
        if isinstance(item, str):
            notes.append({"text": item, "confidence": 0.8})
        elif isinstance(item, dict):
            notes.append({
                "text": item.get("text", ""),
                "confidence": float(item.get("confidence", 0.8)),
            })
    return notes


def _merge_notes_into_body(
    section_body: str, notes: list, retractions: list, max_bullets
) -> str:
    """
    Apply retractions, merge notes, enforce the cap, and reconstruct a
    section body — shared by both the legacy section_text path and the
    section-anchored path.
    """
    if retractions:
        section_body = apply_retractions(section_body, retractions)

    current_bullets = extract_bullets(section_body) if section_body else []

    for note in notes:
        current_bullets = merge_bullet(current_bullets, note["text"], note["confidence"])

    if max_bullets is not None:
        current_bullets = apply_cap(current_bullets, max_bullets)

    return _reconstruct_section(section_body, current_bullets)


def _find_section_span(file_text: str, section: str):
    """
    Locate the body span of a `## <section>` heading in file_text.

    Returns (body_start, body_end) offsets for the section's body — the text
    between the heading line and the next `^## ` heading (or end of file).
    Returns None if the heading is not found.
    """
    heading_pattern = re.compile(rf"^## {re.escape(section)}[ \t]*$", re.MULTILINE)
    match = heading_pattern.search(file_text)
    if not match:
        return None

    body_start = match.end()
    if body_start < len(file_text) and file_text[body_start] == "\n":
        body_start += 1

    next_heading = re.compile(r"^## ", re.MULTILINE)
    next_match = next_heading.search(file_text, body_start)
    body_end = next_match.start() if next_match else len(file_text)
    return body_start, body_end


def merge_section_anchored(
    file_text: str,
    section: str,
    raw_notes: list,
    retractions: list = None,
    max_bullets=None,
) -> str:
    """
    Locate the `## <section>` heading in file_text deterministically (Python
    owns the boundary, never an LLM-supplied span), merge notes only within
    that section's body, and return the FULL file text with only that
    section's body replaced. Content before the heading and after the next
    `^## ` heading is never touched.

    Returns None if the section heading is not found in file_text.
    """
    span = _find_section_span(file_text, section)
    if span is None:
        return None

    body_start, body_end = span
    section_body = file_text[body_start:body_end]
    notes = _normalize_notes(raw_notes)
    merged_body = _merge_notes_into_body(
        section_body, notes, retractions or [], max_bullets
    )
    return file_text[:body_start] + merged_body + "\n" + file_text[body_end:]


def main() -> int:
    """
    CLI entry point: read JSON from stdin, write merged output to stdout.

    Two mutually-compatible modes:
    - Section-anchored (preferred): "file_text" + "section" — the script
      locates the `## <section>` heading in file_text itself and returns the
      FULL file text with only that section's body replaced. The boundary is
      never LLM-supplied.
    - Legacy: "section_text" — the caller passes the current section body
      directly; the script returns the merged section_text string. Kept
      working for callers that have not yet migrated.

    Optional "max_bullets" caps the bullet list post-merge (oldest-first
    eviction) in either mode.
    """
    raw = sys.stdin.read()
    if not raw.strip():
        sys.stderr.write("Error: empty input\n")
        return 1

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"Error: invalid JSON: {exc}\n")
        return 1

    raw_notes = payload.get("notes", [])
    retractions = payload.get("retractions", [])
    max_bullets = payload.get("max_bullets")
    notes = _normalize_notes(raw_notes)

    file_text = payload.get("file_text")
    section = payload.get("section")

    if file_text is not None and section:
        result = merge_section_anchored(
            file_text, section, notes, retractions, max_bullets
        )
        if result is None:
            sys.stderr.write(f"Error: section '{section}' not found in file_text\n")
            return 1
        print(result)
        return 0

    # Legacy path: section_text field, caller-supplied span.
    section_text = payload.get("section_text", "")
    result = _merge_notes_into_body(section_text, notes, retractions, max_bullets)
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
