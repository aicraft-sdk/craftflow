#!/usr/bin/env python3
"""
craftflow_state_digest.py

Builds ONE fixed-size, multi-tier memory digest for the router's "## 2. Memory Load"
step (invoked via `craftflow_state_query.py --mode digest`). Replaces ~10 raw Read()
calls (project/, constitution, workspace/, workflows/{wf}/) -- and the
state-read-compaction deny + retry that oversized files trigger -- with a single Bash
call whose output size does not depend on the size of the underlying files.

Read-only. Never mutates memory files. Stdlib + craftflow_hooklib only.

Output shape:
    # craftflow state digest ...
    ### {tier}/{file} [OK|EMPTY|MISSING|UNREADABLE]
    MISSING_SECTIONS: ...        (only when a required section is absent -> auto-heal)
    ## Memory Summary            (text for the dispatch scaffold's Memory Summary)
    ## Project Patterns          (text for the dispatch scaffold's Project Patterns)
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from craftflow_hooklib import extract_bullets, parse_markdown_sections  # noqa: E402

DIGEST_MAX_CHARS = 8500
FILE_BUDGET_CHARS = 700
CONSTITUTION_BUDGET_CHARS = 400
LINE_CAP_CHARS = 140
BULLETS_PER_SECTION = 2
SUMMARY_BULLETS = 3
MEMORY_FILES = ("activeContext.md", "patterns.md", "progress.md")

REQUIRED_SECTIONS = {
    "activeContext.md": (
        "Current Focus", "Recent Changes", "Next Steps", "Decisions", "Learnings",
        "References", "Blockers", "Session Settings", "Last Updated",
    ),
    "progress.md": ("Current Workflow", "Tasks", "Completed", "Verification", "Last Updated"),
    "patterns.md": ("User Standards", "Common Gotchas", "Project SKILL_HINTS", "Last Updated"),
}


def _cap(line: str, limit: int = LINE_CAP_CHARS) -> str:
    line = line.strip()
    return line if len(line) <= limit else line[: limit - 3] + "..."


def _section_lines_more(body: str, bullets: int) -> tuple[list[str], int]:
    """(kept lines, number of dropped bullets/lines)."""
    found = extract_bullets(body)
    if found:
        return [_cap(b) for b in found[-bullets:]], max(0, len(found) - bullets)
    prose = [ln for ln in body.splitlines() if ln.strip()]
    return [_cap(ln) for ln in prose[:bullets]], max(0, len(prose) - bullets)


def _section_lines(body: str, bullets: int) -> list[str]:
    return _section_lines_more(body, bullets)[0]


def _load(path: Path) -> tuple[str, dict[str, str]]:
    """Return (status, sections). status: OK | EMPTY | MISSING | UNREADABLE."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "MISSING", {}
    except (OSError, UnicodeDecodeError):
        return "UNREADABLE", {}
    if not text.strip():
        return "EMPTY", {}
    return "OK", parse_markdown_sections(text)


def _render_file(label: str, name: str, status: str, sections: dict[str, str],
                 budget: int) -> tuple[str, str]:
    """Return (head, body). head = header + MISSING_SECTIONS (never dropped); body = section text."""
    head = [f"### {label}/{name} [{status}]"]
    required = REQUIRED_SECTIONS.get(name)
    if status == "OK" and required:
        absent = [s for s in required if s not in sections]
        if absent:
            head.append("MISSING_SECTIONS: " + ", ".join(absent))
    used = len(head[0])
    out: list[str] = []
    omitted: list[str] = []
    for heading, body in sections.items():
        lines, more = _section_lines_more(body, BULLETS_PER_SECTION)
        block = [f"## {heading}", *lines]
        if more:
            block.append(f"(+{more} more)")
        size = sum(len(x) + 1 for x in block)
        if used + size > budget:
            omitted.append(heading)
            continue
        out.extend(block)
        used += size
    if omitted:
        out.append("(omitted for size: " + ", ".join(omitted) + ")")
    return "\n".join(head), "\n".join(out)


def _first_present(tiers: list[dict[str, dict[str, str]]], file: str, section: str) -> list[str]:
    """Highest-precedence tier (first in list) with a non-empty section wins."""
    for tier in tiers:
        body = tier.get(file, {}).get(section, "")
        if body.strip():
            return _section_lines(body, SUMMARY_BULLETS)
    return []


def build_digest(project_root: Path, workspace_root: Path | None, workflow_uuid: str | None) -> str:
    state = project_root / ".craftflow" / "state"
    tier_dirs: list[tuple[str, Path]] = [("project", state / "project")]
    if workspace_root is not None:
        tier_dirs.append(("workspace", workspace_root / ".craftflow" / "state" / "workspace"))
    if workflow_uuid:
        tier_dirs.append((f"workflows/{workflow_uuid}", state / "workflows" / workflow_uuid))

    loaded: dict[str, dict[str, tuple[str, dict[str, str]]]] = {}
    for label, base in tier_dirs:
        loaded[label] = {name: _load(base / name) for name in MEMORY_FILES}

    # Root-flat backward-compat layer: only when project/ has no usable files.
    if all(status in ("MISSING", "EMPTY") for status, _ in loaded["project"].values()):
        tier_dirs.append(("root-flat", state))
        loaded["root-flat"] = {name: _load(state / name) for name in MEMORY_FILES}

    title = ("# craftflow state digest (fixed-size; read any file in full via "
             "`craftflow_state_query.py <path> --mode full`)")
    entries: list[tuple[str, str, str]] = []  # (label, head, body)
    for label, base in tier_dirs:
        for name in MEMORY_FILES:
            status, sections = loaded[label][name]
            head, body = _render_file(label, name, status, sections, FILE_BUDGET_CHARS)
            entries.append((f"{label}/{name}", head, body))
        if label == "project":
            constitution, unreadable = "", False
            try:
                constitution = (base / "constitution.md").read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                pass
            except (OSError, UnicodeDecodeError):
                unreadable = True
            if unreadable:
                entries.append(("project/constitution.md", "### project/constitution.md [UNREADABLE]", ""))
            elif constitution:
                clipped = len(constitution) > CONSTITUTION_BUDGET_CHARS
                status = "OK, truncated" if clipped else "OK"
                body = constitution[:CONSTITUTION_BUDGET_CHARS] + ("..." if clipped else "")
                entries.append(("project/constitution.md", f"### project/constitution.md [{status}]", body))

    # Precedence for current-focus fields: workflow > project(/root-flat) > workspace.
    by_label = {label: {n: s for n, (st, s) in files.items()} for label, files in loaded.items()}
    project_like = [by_label["root-flat"]] if "root-flat" in by_label else [by_label["project"]]
    workflow_tiers = [v for k, v in by_label.items() if k.startswith("workflows/")]
    workspace_tiers = [by_label["workspace"]] if "workspace" in by_label else []
    focus_order = workflow_tiers + project_like + workspace_tiers
    durable_order = project_like + workspace_tiers

    summary = ["## Memory Summary"]
    for heading, file, section, order in (
        ("Current Focus", "activeContext.md", "Current Focus", focus_order),
        ("Next Steps", "activeContext.md", "Next Steps", focus_order),
        ("Tasks", "progress.md", "Tasks", focus_order),
        ("Decisions", "activeContext.md", "Decisions", durable_order),
    ):
        lines = _first_present(order, file, section)
        if lines:
            summary.append(f"**{heading}:**")
            summary.extend(lines)

    patterns = ["## Project Patterns"]
    for section in ("User Standards", "Common Gotchas", "Project SKILL_HINTS"):
        lines = _first_present(durable_order, "patterns.md", section)
        if lines:
            patterns.append(f"**{section}:**")
            patterns.extend(lines)

    tail = "\n".join(["\n".join(summary), "\n".join(patterns)]) + "\n"

    def _join(head: str, body: str) -> str:
        return head + ("\n" + body if body else "")

    full = "\n".join([title, *(_join(h, b) for _, h, b in entries)]) + "\n" + tail
    if len(full) <= DIGEST_MAX_CHARS:
        return full
    # Over budget: heads (header + MISSING_SECTIONS) and the tail are load-bearing and always kept;
    # bodies are kept in order while they fit and the rest are named in a truncation note.
    fixed = len(title) + sum(len(h) + 1 for _, h, _ in entries) + len(tail) + 200
    budget = DIGEST_MAX_CHARS - fixed
    kept: list[str] = [title]
    dropped: list[str] = []
    for label, head, body in entries:
        if body and len(body) + 1 <= budget:
            kept.append(_join(head, body))
            budget -= len(body) + 1
        else:
            kept.append(head)
            if body:
                dropped.append(label)
    if dropped:
        kept.append("(digest truncated for size; bodies dropped: " + ", ".join(dropped)
                    + " -- read them via `craftflow_state_query.py <path> --mode full`)")
    return "\n".join(kept) + "\n" + tail
