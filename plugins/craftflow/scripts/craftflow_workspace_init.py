#!/usr/bin/env python3
"""craftflow_workspace_init.py

Provisions the `{workspace_root}/.craftflow/state/workspace/` memory tier: a
3-file template contract (`activeContext.md`/`patterns.md`/`progress.md`)
mirroring the existing `project/` tier's required-sections shape (see
`skills/_shared/router-protocol.md` § "Memory File Required Sections"), plus
a `## North Star` section inserted into `activeContext.md` immediately after
`## Current Focus`.

Design: docs/plans/2026-08-27-want-craftflow-be-able-setup-con-design.md
Plan:   docs/plans/2026-08-27-want-craftflow-be-able-setup-con-plan.md (Phase 1)

Usage (library, called by the `craftflow:workspace-setup` skill):
  from craftflow_workspace_init import init_workspace
  init_workspace(workspace_root, north_star="...")

Behavior:
  - Idempotent: re-running never clobbers existing file content. Missing
    required sections are auto-healed by inserting them before
    `## Last Updated` (or appending at end if that heading is itself
    missing); existing content -- including any section already present --
    is never rewritten or removed.
  - Never touches `.craftflow-workspace.json` (a fully separate artifact
    owned by the `ai-first-setup` skill).
  - Refuses to initialize `$HOME` or `/` as a workspace root -- exits
    non-zero via SystemExit with a diagnostic on stderr, before any file
    write is attempted.
  - An unwritable target degrades gracefully: any OSError raised while
    writing is caught, reported to stderr, and results in a non-zero
    SystemExit with no partially-written file left on disk (write to a
    `.tmp` file in the same directory, `os.replace()` into place only on
    full success -- mirrors `craftflow_skill_ledger.save_ledger_atomic`'s
    temp-file-then-os.replace pattern).

Read-only w.r.t. anything outside `{workspace_root}/.craftflow/state/workspace/`.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_REQUIRED_SECTIONS: dict[str, list[str]] = {
    "activeContext.md": [
        "## Current Focus",
        "## North Star",
        "## Recent Changes",
        "## Next Steps",
        "## Decisions",
        "## Learnings",
        "## References",
        "## Blockers",
        "## Session Settings",
        "## Last Updated",
    ],
    "patterns.md": [
        "## User Standards",
        "## Common Gotchas",
        "## Project SKILL_HINTS",
        "## Last Updated",
    ],
    "progress.md": [
        "## Current Workflow",
        "## Tasks",
        "## Completed",
        "## Verification",
        "## Last Updated",
    ],
}

_TITLES: dict[str, str] = {
    "activeContext.md": "# Workspace Active Context",
    "patterns.md": "# Workspace Patterns",
    "progress.md": "# Workspace Progress Tracking",
}


def _refuse_if_unsafe_root(workspace_root: Path) -> None:
    """Exit non-zero before any write if workspace_root is $HOME or / itself."""
    try:
        resolved = workspace_root.resolve()
    except (OSError, RuntimeError) as exc:
        print(f"craftflow_workspace_init: cannot resolve workspace root: {exc}", file=sys.stderr)
        raise SystemExit(1)

    unsafe_roots = [Path("/")]
    try:
        unsafe_roots.append(Path.home())
    except (OSError, RuntimeError):
        pass  # no resolvable $HOME on this platform -- fall through to the / check only

    for unsafe in unsafe_roots:
        try:
            unsafe_resolved = unsafe.resolve()
        except (OSError, RuntimeError):
            continue
        if resolved == unsafe_resolved:
            print(
                f"craftflow_workspace_init: refusing to initialize {resolved} as a "
                "workspace root (matches $HOME or /) -- this would scope workspace-tier "
                "memory over your entire home directory or filesystem.",
                file=sys.stderr,
            )
            raise SystemExit(1)


def _write_file_atomic(path: Path, content: str) -> None:
    """Write `content` to `path` atomically (temp file + os.replace()).

    Mirrors craftflow_skill_ledger.save_ledger_atomic's temp-file-then-
    os.replace pattern. On any failure, the temp file is removed and no
    partial file is left at `path`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".craftflow-workspace-init-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def _initial_content(filename: str, north_star: str) -> str:
    lines = [_TITLES[filename], ""]
    for section in _REQUIRED_SECTIONS[filename]:
        lines.append(section)
        if section == "## North Star":
            lines.append(north_star)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _heal_missing_sections(existing: str, filename: str) -> str:
    """Return `existing` with any required-but-missing sections inserted
    before `## Last Updated` (or appended at end if that heading is itself
    missing). Never removes or rewrites content already present.
    """
    missing = [s for s in _REQUIRED_SECTIONS[filename] if s not in existing]
    if not missing:
        return existing

    insertion = "".join(f"\n{section}\n" for section in missing if section != "## Last Updated")
    if not insertion:
        # only "## Last Updated" itself is missing -- append it at the very end
        return existing.rstrip("\n") + "\n\n## Last Updated\n"

    marker = "## Last Updated"
    idx = existing.find(marker)
    if idx == -1:
        return existing.rstrip("\n") + "\n" + insertion
    return existing[:idx] + insertion.lstrip("\n") + "\n" + existing[idx:]


def init_workspace(workspace_root: Path, north_star: str) -> None:
    """Provision the workspace-tier memory files under `workspace_root`.

    Idempotent, never clobbers existing content, never touches
    `.craftflow-workspace.json`, refuses `$HOME`/`/` as a workspace root,
    and degrades gracefully (non-zero SystemExit, stderr diagnostic, no
    partial file) on an unwritable target.
    """
    _refuse_if_unsafe_root(workspace_root)

    workspace_dir = workspace_root / ".craftflow" / "state" / "workspace"

    try:
        for filename in ("activeContext.md", "patterns.md", "progress.md"):
            path = workspace_dir / filename
            if path.exists():
                existing = path.read_text(encoding="utf-8")
                healed = _heal_missing_sections(existing, filename)
                if healed != existing:
                    _write_file_atomic(path, healed)
            else:
                _write_file_atomic(path, _initial_content(filename, north_star))
    except OSError as exc:
        print(f"craftflow_workspace_init: failed to write workspace-tier memory: {exc}", file=sys.stderr)
        raise SystemExit(1)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Provision workspace-tier Craftflow memory.")
    parser.add_argument("--workspace-root", required=True, help="Absolute path to the workspace root")
    parser.add_argument("--north-star", required=True, help="North Star text for activeContext.md")
    args = parser.parse_args()

    try:
        init_workspace(Path(args.workspace_root), north_star=args.north_star)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 1
    print(f"craftflow_workspace_init: provisioned workspace tier at {args.workspace_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
