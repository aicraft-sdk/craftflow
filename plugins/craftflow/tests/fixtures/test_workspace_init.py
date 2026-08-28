#!/usr/bin/env python3
"""Tests for craftflow_workspace_init.py.

Run: python3 tests/fixtures/test_workspace_init.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_workspace_init import init_workspace  # noqa: E402

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def test_creates_all_three_files_with_required_sections() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_workspace(root, north_star="Ship X across all repos")
        active = (root / ".craftflow/state/workspace/activeContext.md").read_text()
        patterns_exists = (root / ".craftflow/state/workspace/patterns.md").exists()
        progress_exists = (root / ".craftflow/state/workspace/progress.md").exists()
        if "## North Star" in active and "Ship X across all repos" in active and patterns_exists and progress_exists:
            ok("creates all three files with required sections")
        else:
            fail("creates-all-three", f"active={active!r}")


def test_idempotent_second_run_does_not_clobber_existing_content() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        init_workspace(root, north_star="Original")
        active_path = root / ".craftflow/state/workspace/activeContext.md"
        active_path.write_text(active_path.read_text() + "\n## Decisions\n- custom entry\n")
        init_workspace(root, north_star="Original")  # re-run
        if "custom entry" in active_path.read_text():
            ok("idempotent re-run preserves custom content")
        else:
            fail("idempotent-rerun", "custom entry lost on re-run")


def test_never_touches_craftflow_workspace_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = root / ".craftflow-workspace.json"
        config.write_text('{"writable_paths": ["FOO.md"]}')
        before = config.read_text()
        init_workspace(root, north_star="X")
        if config.read_text() == before:
            ok("never touches .craftflow-workspace.json")
        else:
            fail("workspace-json-untouched", "config file was modified")


def test_refuses_home_directory_as_workspace_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with mock.patch("craftflow_workspace_init.Path.home", return_value=root):
            try:
                init_workspace(root, north_star="X")
                fail("refuses-home", "did not raise SystemExit")
            except SystemExit as exc:
                if exc.code != 0:
                    ok("refuses $HOME as workspace root")
                else:
                    fail("refuses-home", f"exit code was 0")


def test_refuses_filesystem_root_as_workspace_root() -> None:
    try:
        init_workspace(Path("/"), north_star="X")
        fail("refuses-root", "did not raise SystemExit")
    except SystemExit as exc:
        if exc.code != 0:
            ok("refuses / as workspace root")
        else:
            fail("refuses-root", "exit code was 0")


def test_unwritable_target_degrades_gracefully_no_partial_file() -> None:
    # No existing precedent in tests/fixtures/ simulates an unwritable target via chmod
    # (chmod-based simulation is unreliable when tests run as root/CI). Instead, mock the
    # script's own atomic file-write helper to raise OSError -- a portable stdlib
    # `unittest.mock.patch` on an internal call, matching this file's own convention of
    # mocking specific OS-boundary calls (subprocess.run, Path.resolve, Path.samefile in
    # test_resolve_workspace_root.py) rather than manipulating real filesystem permissions.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with mock.patch(
            "craftflow_workspace_init._write_file_atomic",
            side_effect=OSError(13, "Permission denied"),
        ):
            try:
                init_workspace(root, north_star="X")
                fail("unwritable-target", "did not raise SystemExit")
            except SystemExit as exc:
                workspace_dir = root / ".craftflow/state/workspace"
                partial_files = list(workspace_dir.glob("*.md")) if workspace_dir.exists() else []
                if exc.code != 0 and partial_files == []:
                    ok("unwritable target degrades gracefully, no partial file")
                else:
                    fail("unwritable-target", f"exit={exc.code}, partial_files={partial_files}")


def main() -> int:
    print("test_workspace_init: running")
    test_creates_all_three_files_with_required_sections()
    test_idempotent_second_run_does_not_clobber_existing_content()
    test_never_touches_craftflow_workspace_json()
    test_refuses_home_directory_as_workspace_root()
    test_refuses_filesystem_root_as_workspace_root()
    test_unwritable_target_degrades_gracefully_no_partial_file()

    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
