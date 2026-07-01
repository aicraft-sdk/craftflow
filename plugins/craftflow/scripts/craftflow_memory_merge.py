#!/usr/bin/env python3
"""
craftflow_memory_merge.py

Deterministic, confidence-aware markdown bullet merger.

Usage (CLI mode):
    python3 craftflow_memory_merge.py < payload.json

Input JSON (stdin):
    {
      "section_text": "current content of the target ## section",
      "notes": [
        {"text": "new insight", "confidence": 0.9}
      ],
      "retractions": ["old note to remove"]
    }

Output (stdout): merged section_text string
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

    norm_new = normalize_bullet(new_text)
    result = list(existing_bullets)

    for i, bullet in enumerate(result):
        existing_clean = strip_confidence_suffix(bullet)
        norm_existing = normalize_bullet(existing_clean)
        if norm_existing == norm_new:
            existing_conf = parse_confidence(bullet)
            if confidence >= existing_conf:
                result[i] = f"- {new_text} (conf: {confidence})"
            # If new confidence < existing, keep old — do nothing
            return result

    # No match found — append
    result.append(f"- {new_text} (conf: {confidence})")
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


def main() -> int:
    """CLI entry point: read JSON from stdin, write merged section_text to stdout."""
    raw = sys.stdin.read()
    if not raw.strip():
        sys.stderr.write("Error: empty input\n")
        return 1

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"Error: invalid JSON: {exc}\n")
        return 1

    section_text = payload.get("section_text", "")
    raw_notes = payload.get("notes", [])
    retractions = payload.get("retractions", [])

    # Normalize notes: plain strings default to confidence 0.8
    notes = []
    for item in raw_notes:
        if isinstance(item, str):
            notes.append({"text": item, "confidence": 0.8})
        elif isinstance(item, dict):
            notes.append({
                "text": item.get("text", ""),
                "confidence": float(item.get("confidence", 0.8)),
            })

    # Step 1: apply retractions first (to avoid retracting just-added bullets)
    if retractions:
        section_text = apply_retractions(section_text, retractions)

    # Step 2: extract current bullets from (possibly retraction-reduced) section
    current_bullets = extract_bullets(section_text) if section_text else []

    # Step 3: merge each note
    for note in notes:
        current_bullets = merge_bullet(current_bullets, note["text"], note["confidence"])

    # Step 4: reconstruct section_text (preserve non-bullet lines, replace bullet block)
    result = _reconstruct_section(section_text, current_bullets)

    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
