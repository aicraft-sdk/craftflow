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
        {"text": "new insight", "confidence": 0.9, "provenance": "imported"}
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
        {"text": "new insight", "confidence": 0.9, "provenance": "imported"}
      ],
      "retractions": ["old note to remove"],
      "max_bullets": 60
    }
    Output (stdout): merged section_text string.

"max_bullets" is optional in both modes; when present, the bullet list is
capped post-merge with oldest-first eviction.

Each note's "provenance" field is optional: "organic" | "imported", defaults to "imported" when
absent or unrecognized (fail-safe — an incoming note is never treated as organic unless
explicitly marked). An existing bullet marked organic is never auto-replaced by an incoming
note, regardless of confidence; the incoming note is appended as a new bullet instead.

Known limitation: a bullet whose own prose legitimately ends with a substring shaped like the
confidence/provenance suffix (e.g. "...(conf: 0.9, organic)") will be mis-parsed as carrying
that confidence/provenance — this is an inherent residual risk of encoding metadata as a suffix
within the same string as the bullet's text, not a structural defect worth fixing here.

Exit 0 on success, 1 on error.
"""
import json
import re
import sys

from craftflow_hooklib import extract_bullets, normalize_bullet


def parse_confidence(line: str) -> float:
    """Extract (conf: N.N[, organic]) suffix from a bullet line. Returns float or 0.8 if absent.

    Whitespace-tolerant around the comma and case-insensitive on "organic" so a malformed
    suffix (stray space before the comma, or unexpected capitalization) is still recognized
    consistently with parse_provenance/strip_confidence_suffix -- an unrecognized suffix shape
    would otherwise fail to strip, causing duplicate-bullet accumulation via a different root
    cause than the organic-match branch bug."""
    match = re.search(r"\(conf:\s*([\d.]+)(?:\s*,\s*organic)?\)\s*$", line, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return 0.8


def parse_provenance(line: str) -> str:
    """Extract provenance from a bullet's suffix. Returns 'organic' when the line ends with
    '(conf: N.N, organic)' (whitespace-tolerant around the comma, case-insensitive on
    "organic"); otherwise 'imported' -- the safe default for unmarked legacy bullets
    (see design Questions Resolved: an unmarked bullet must NOT silently gain organic
    supersede-immunity)."""
    match = re.search(r"\(conf:\s*[\d.]+\s*,\s*organic\)\s*$", line, re.IGNORECASE)
    return "organic" if match else "imported"


def strip_confidence_suffix(line: str) -> str:
    """Remove ' (conf: N.N[, organic])' suffix if present. Returns clean text.

    Whitespace-tolerant around the comma and case-insensitive on "organic", matching
    parse_confidence/parse_provenance -- keeps all three suffix-parsing functions consistent
    on the same malformed-suffix shapes."""
    return re.sub(
        r"\s*\(conf:\s*[\d.]+(?:\s*,\s*organic)?\)\s*$", "", line, flags=re.IGNORECASE
    )


def _render_bullet(text: str, confidence: float, provenance: str) -> str:
    """Render a bullet line. The provenance marker is included only for organic notes --
    imported stays the existing unmarked '(conf: x)' shape so every pre-existing bullet in every
    managed file remains valid and unchanged (backward compatibility)."""
    if provenance == "organic":
        return f"- {text} (conf: {confidence}, organic)"
    return f"- {text} (conf: {confidence})"


def merge_bullet(existing_bullets: list, new_text: str, confidence: float, provenance: str = "imported") -> list:
    """
    Merge a new note into the existing bullet list with confidence-aware superseding.

    - confidence < 0.7: drop (return existing unchanged), emitting a non-silent stderr
      warning -- a dropped note (including an organic one) must never disappear without
      signal.
    - If normalize_bullet match found:
        - existing bullet is marked organic: NEVER replaced, regardless of confidence --
          the incoming note is appended as a new bullet instead (supersede-immunity guard;
          closes the hand-edited-bullet-overwrite risk).
        - existing bullet is imported (default for unmarked legacy bullets):
          new confidence >= existing confidence -> replace; else skip (keep old)
    - If no match: append new bullet with (conf: x[, organic]) suffix
    """
    if confidence < 0.7:
        sys.stderr.write(
            f"Warning: dropping note below confidence threshold (0.7): {new_text!r} (confidence={confidence})\n"
        )
        return existing_bullets

    # Strip any pre-existing confidence/provenance suffix from new_text before normalizing
    # or formatting -- symmetric with how existing bullets are handled below. Without this,
    # an already-suffixed note never matches its existing counterpart and acquires a second
    # suffix.
    clean_new = strip_confidence_suffix(new_text)
    norm_new = normalize_bullet(clean_new)
    result = list(existing_bullets)

    for i, bullet in enumerate(result):
        existing_clean = strip_confidence_suffix(bullet)
        norm_existing = normalize_bullet(existing_clean)
        if norm_existing == norm_new:
            if parse_provenance(bullet) == "organic":
                # Supersede-immunity: never overwrite an organic bullet -- but this
                # protection applies only to the FIRST occurrence (i, found above).
                # Before unconditionally appending a new duplicate, check whether a
                # duplicate from a PREVIOUS call already exists later in the list --
                # regardless of THAT duplicate's own provenance (it may itself be
                # organic-marked if a prior call's incoming note carried
                # provenance="organic") -- and if so, apply the normal confidence-based
                # supersede/skip logic to THAT entry instead. Without this check, repeat
                # merges of the same organic-matching note accumulate unbounded
                # duplicates (imported OR organic-marked), and oldest-first cap eviction
                # would evict the original organic bullet first, defeating the
                # supersede-immunity guard entirely.
                for j in range(i + 1, len(result)):
                    other_clean = strip_confidence_suffix(result[j])
                    if (
                        normalize_bullet(other_clean) == norm_new
                        and parse_provenance(result[j]) in ("imported", "organic")
                    ):
                        other_conf = parse_confidence(result[j])
                        if confidence >= other_conf:
                            result[j] = _render_bullet(clean_new, confidence, provenance)
                        # If new confidence < existing imported duplicate, keep old -- do nothing
                        return result
                # No pre-existing imported duplicate found -- append as a distinct new
                # bullet, never silently dropped, never silently overwritten.
                result.append(_render_bullet(clean_new, confidence, provenance))
                return result
            existing_conf = parse_confidence(bullet)
            if confidence >= existing_conf:
                result[i] = _render_bullet(clean_new, confidence, provenance)
            # If new confidence < existing, keep old -- do nothing
            return result

    # No match found -- append
    result.append(_render_bullet(clean_new, confidence, provenance))
    return result


def apply_retractions(section_body: str, retractions: list) -> str:
    """
    Remove bullets from section_body whose normalized text matches any retraction.

    - Split section_body into lines
    - For each bullet line, normalize its text (strip confidence suffix first)
    - If it matches any retraction's normalized form, remove the line
    - Rejoin with newline and return

    Provenance awareness: dropping a bullet whose provenance is 'organic' emits a
    stderr warning so the removal is never silent -- this script is invoked by
    craftflow-router's own Memory Finalization step against real production memory
    files, and a permanent, unsignaled deletion of a hand-verified note is a real risk.
    The bullet is still removed either way; this is a non-blocking warning, not a
    guard (retractions are an explicit, caller-requested removal, unlike merge_bullet's
    supersede path).
    """
    if not retractions or not section_body:
        return section_body
    if not isinstance(retractions, list):
        raise TypeError(f"'retractions' must be a list, got {type(retractions).__name__}")

    norm_retractions = {normalize_bullet(r) for r in retractions}
    lines = section_body.split("\n")
    kept = []
    for line in lines:
        if line.lstrip().startswith("- "):
            clean = normalize_bullet(strip_confidence_suffix(line))
            if clean in norm_retractions:
                if parse_provenance(line) == "organic":
                    sys.stderr.write(
                        f"Warning: retracting organic-marked bullet: {line.strip()}\n"
                    )
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
    Trim a bullet list to at most max_bullets entries, evicting oldest-first --
    provenance-aware: imported bullets are evicted before any organic bullet, so
    routine section growth never silently breaks the organic supersede-immunity
    guarantee when a non-organic alternative is available to evict instead.

    Bullets are stored in chronological (oldest-first) order — earlier
    entries were added first, later entries appended after. Within each
    provenance group (imported, then organic), eviction removes from the
    front of that group.

    - max_bullets is None: no-op, return bullets unchanged
    - len(bullets) <= max_bullets: no-op, return bullets unchanged
    - An organic bullet is only evicted once zero imported bullets remain to evict instead.
    - Evicting an organic-marked bullet emits a stderr warning (never silent), matching
      apply_retractions' existing warn-on-organic-loss pattern in this file.
    """
    if max_bullets is None or len(bullets) <= max_bullets:
        return bullets

    excess = len(bullets) - max_bullets
    imported_indices = [
        i for i, bullet in enumerate(bullets) if parse_provenance(bullet) != "organic"
    ]
    organic_indices = [
        i for i, bullet in enumerate(bullets) if parse_provenance(bullet) == "organic"
    ]
    evict_order = imported_indices + organic_indices
    evict_indices = set(evict_order[:excess])

    # Consistent with every other removal path in this file (apply_retractions):
    # evicting an organic-marked bullet must never be silent, even though eviction
    # only reaches an organic bullet once no imported alternative remains.
    for i in sorted(evict_indices):
        if parse_provenance(bullets[i]) == "organic":
            sys.stderr.write(f"Warning: cap eviction removed organic-marked bullet: {bullets[i].strip()}\n")

    return [bullet for i, bullet in enumerate(bullets) if i not in evict_indices]


def apply_cap_with_archive(bullets: list, max_bullets, archive: bool, archive_path: str = None) -> tuple:
    """Like apply_cap, but when archive=True, excess bullets (same
    imported-before-organic evict_order as apply_cap) are returned as a
    second list instead of being dropped, and a single organic pointer
    bullet ("- Older entries archived: see <archive_path> (conf: 1.0,
    organic)") is appended to the kept list in their place. archive=False is
    byte-identical to calling apply_cap directly (backward compatible,
    zero output-shape change for existing callers that never opt in).

    archive_path is optional: this function is pure and has no knowledge of
    where the caller will actually write the archive file, so a generic
    placeholder ("the archive") is used when omitted. The CLI envelope path
    (main(), via merge_section_anchored_with_archive) passes the real,
    already-computed archive_path so the pointer bullet names the actual
    monthly archive file.

    Returns (kept_bullets, archived_bullets). archived_bullets is always []
    when archive=False or when no eviction is needed.
    """
    if not archive:
        return apply_cap(bullets, max_bullets), []
    if max_bullets is None or len(bullets) <= max_bullets:
        return bullets, []

    excess = len(bullets) - max_bullets
    imported_indices = [
        i for i, bullet in enumerate(bullets) if parse_provenance(bullet) != "organic"
    ]
    organic_indices = [
        i for i, bullet in enumerate(bullets) if parse_provenance(bullet) == "organic"
    ]
    evict_order = imported_indices + organic_indices
    evict_indices = set(evict_order[:excess])

    archived_bullets = [bullets[i] for i in sorted(evict_indices)]
    kept_bullets = [bullet for i, bullet in enumerate(bullets) if i not in evict_indices]
    pointer_target = archive_path or "the archive"
    kept_bullets.append(f"- Older entries archived: see {pointer_target} (conf: 1.0, organic)")
    return kept_bullets, archived_bullets


def _normalize_provenance(value) -> str:
    """Fail-safe: only the literal string 'organic' grants organic protection. Anything else
    (missing, None, unrecognized string) normalizes to 'imported' -- never crash on malformed
    payload shape, and never silently grant organic protection to untrusted input."""
    return "organic" if value == "organic" else "imported"


def _normalize_notes(raw_notes: list) -> list:
    """Normalize raw note entries (plain strings or dicts) to {"text", "confidence", "provenance"} dicts.

    A malformed entry (neither str nor dict -- e.g. None, a number, a nested list) is dropped,
    but never silently: a stderr warning is emitted first, matching apply_retractions' existing
    warn-on-organic-loss pattern in this file. Well-formed entries in the same list are still
    normalized normally.

    raw_notes itself must be a list -- a string value (e.g. a caller typo omitting the "[...]"
    wrapper) would otherwise be silently iterated character-by-character, injecting one garbage
    bullet per character with zero warning.
    """
    if not isinstance(raw_notes, list):
        raise TypeError(f"'notes' must be a list, got {type(raw_notes).__name__}")

    notes = []
    for item in raw_notes:
        if isinstance(item, str):
            notes.append({"text": item, "confidence": 0.8, "provenance": "imported"})
        elif isinstance(item, dict):
            text = item.get("text", "")
            if not isinstance(text, str) or not text.strip():
                sys.stderr.write(
                    f"Warning: dropping malformed note entry (missing/empty text): {item!r}\n"
                )
                continue
            notes.append({
                "text": text,
                "confidence": float(item.get("confidence", 0.8)),
                "provenance": _normalize_provenance(item.get("provenance")),
            })
        else:
            sys.stderr.write(f"Warning: dropping malformed note entry (not str/dict): {item!r}\n")
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
        current_bullets = merge_bullet(current_bullets, note["text"], note["confidence"], note["provenance"])

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


def _reject_json_constant(constant: str):
    """json.loads' parse_constant hook -- called for the non-standard NaN/Infinity/-Infinity
    tokens it accepts by default. Raising here (instead of returning float('nan')/float('inf'))
    stops a NaN confidence value from ever reaching merge_bullet, where `nan < 0.7` is always
    False and would silently bypass the drop threshold."""
    raise ValueError(f"invalid numeric literal in JSON: {constant}")


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
        # allow_nan is on by default in json.loads, which would let a NaN/Infinity
        # confidence value silently bypass merge_bullet's `< 0.7` drop threshold
        # (nan < 0.7 is always False) and get embedded verbatim in persisted output.
        # Reject those non-standard tokens at the JSON boundary instead.
        payload = json.loads(raw, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        sys.stderr.write(f"Error: invalid JSON: {exc}\n")
        return 1

    raw_notes = payload.get("notes", [])
    retractions = payload.get("retractions", [])
    max_bullets = payload.get("max_bullets")

    try:
        file_text = payload.get("file_text")
        section = payload.get("section")

        if file_text is not None:
            # Require section explicitly rather than a truthy check: an empty-string
            # "section" combined with a present "file_text" must fail cleanly, not
            # silently fall through to the legacy section_text path (which would
            # discard file_text's content entirely on output).
            if not isinstance(section, str) or not section.strip():
                sys.stderr.write(
                    "Error: 'section' must be a non-empty string when 'file_text' is provided\n"
                )
                return 1
            result = merge_section_anchored(
                file_text, section, raw_notes, retractions, max_bullets
            )
            if result is None:
                sys.stderr.write(f"Error: section '{section}' not found in file_text\n")
                return 1
            print(result)
            return 0

        # Legacy path: section_text field, caller-supplied span. Only this path
        # needs the separately-normalized notes list -- merge_section_anchored
        # above normalizes raw_notes itself.
        notes = _normalize_notes(raw_notes)
        section_text = payload.get("section_text", "")
        result = _merge_notes_into_body(section_text, notes, retractions, max_bullets)
        print(result)
        return 0
    except (TypeError, ValueError) as exc:
        # Malformed field types (e.g. a non-numeric "confidence" value that fails
        # float() in _normalize_notes, or a non-integer "max_bullets" that fails
        # apply_cap's comparison) must fail cleanly like every other error branch
        # in this function, not crash with a raw unhandled traceback.
        sys.stderr.write(f"Error: invalid payload field: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
