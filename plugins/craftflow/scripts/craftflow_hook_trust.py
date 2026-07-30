#!/usr/bin/env python3
"""Hook trust/provenance gate -- a standalone CLI that refuses (fail-closed)
to vouch for a repo-local hook script that isn't listed in a hash manifest,
or whose current content no longer matches its manifested hash.

Concept ported (not code) from xai-org/grok-build's xai-grok-hooks
`trust.rs` (Apache-2.0). See this plugin's NOTICE.

Purpose: protects against a malicious or tampered checked-in hook script
running under repo-conductor/sandbox dispatch, where a less-trusted
repo-local hook script (not this plugin's own installed scripts, which
Claude Code's harness already trusts by construction) might otherwise be
executed unexamined. This script is NOT itself wired into hooks.json --
unlike craftflow_safe_shell_guard.py/craftflow_stop_verify.py, it has no
corresponding Claude Code hook event of its own. It is a composable gate
intended for a FUTURE caller (e.g. repo-conductor's sandbox spawner, out of
this phase's scope) to invoke before executing a repo-local hook script
path it does not already inherently trust.

Manifest schema (.craftflow/hook-trust.json, project-root-relative):
  {"scripts": {"<path-relative-to-project-root>": "<sha256-hex-of-content>"}}

Isolation constraint (mirrors craftflow_hook_selfcheck.py's own "subprocess-
only, never import the checked script directly" principle, for the same
reason: a compromised or broken hook script must not be able to crash the
gate that's supposed to police it): this script deliberately avoids
depending on craftflow_hooklib.py at all. That shared module is itself one
of the sibling scripts that could plausibly need a trust check -- pulling
it in here to reuse its stdin/path helpers would already execute its
top-level code before this gate could ever refuse it, defeating the whole
point of a pre-execution trust gate. Trust evaluation here only ever reads
raw bytes off disk (hashlib.sha256 over Path.read_bytes()) -- it never
executes, imports, or subprocess-runs the script under evaluation, so no
additional subprocess isolation is needed for the hashing operation itself.

CLI:
  craftflow_hook_trust.py check <script-path> [--project-root DIR] [--manifest FILE]
      Prints {"trusted": bool, "reason": str, "path": str} to stdout.
      Exit 0 if trusted, exit 1 if untrusted (fail-closed).

  craftflow_hook_trust.py update [--project-root DIR] [--manifest FILE] [--scripts-dir DIR]
      (Re)computes sha256 hashes for every craftflow_*.py script in
      --scripts-dir (default: this script's own directory) and writes them
      to the manifest -- the seeding/refresh workflow for a currently-
      reviewed, presumed-trusted set of scripts. Exit 0 on success.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def compute_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_project_root() -> Path:
    value = os.environ.get("CLAUDE_PROJECT_DIR")
    if value:
        return Path(value)
    return Path.cwd()


def default_manifest_path(project_root: Path) -> Path:
    return project_root / ".craftflow" / "hook-trust.json"


def load_manifest(manifest_path: Path) -> dict:
    if not manifest_path.exists():
        return {"scripts": {}}
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {"scripts": {}}
    if not isinstance(loaded, dict) or not isinstance(loaded.get("scripts"), dict):
        return {"scripts": {}}
    return loaded


def relative_key(script_path: Path, project_root: Path) -> str:
    try:
        return str(script_path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return script_path.name


def check_script_trust(script_path: Path, project_root: Path, manifest_path: Path) -> tuple:
    """Return (trusted: bool, reason: str). Fail-closed on every ambiguous
    or unexpected condition (missing manifest, missing entry, hash
    mismatch, unreadable script)."""
    if not script_path.exists():
        return False, f"script does not exist: {script_path}"

    manifest = load_manifest(manifest_path)
    scripts = manifest.get("scripts", {})
    if not scripts:
        return False, f"no trust manifest found at {manifest_path}"

    key = relative_key(script_path, project_root)
    expected_hash = scripts.get(key)
    if expected_hash is None:
        return False, f"'{key}' is not listed in the trust manifest"

    try:
        actual_hash = compute_sha256(script_path)
    except Exception as exc:
        return False, f"could not read script to compute hash: {exc}"

    if actual_hash != expected_hash:
        return False, f"hash mismatch for '{key}' (script content no longer matches the manifested hash)"

    return True, f"'{key}' matches the manifested hash"


def generate_manifest(scripts_dir: Path, project_root: Path) -> dict:
    scripts: dict = {}
    for script_path in sorted(scripts_dir.glob("craftflow_*.py")):
        key = relative_key(script_path, project_root)
        scripts[key] = compute_sha256(script_path)
    return {"scripts": scripts}


def cmd_check(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root) if args.project_root else default_project_root()
    manifest_path = Path(args.manifest) if args.manifest else default_manifest_path(project_root)
    script_path = Path(args.script_path)

    trusted, reason = check_script_trust(script_path, project_root, manifest_path)
    print(json.dumps({"trusted": trusted, "reason": reason, "path": str(script_path)}, ensure_ascii=True))
    return 0 if trusted else 1


def cmd_update(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root) if args.project_root else default_project_root()
    manifest_path = Path(args.manifest) if args.manifest else default_manifest_path(project_root)
    scripts_dir = Path(args.scripts_dir) if args.scripts_dir else Path(__file__).resolve().parent

    manifest = generate_manifest(scripts_dir, project_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"updated": True, "count": len(manifest["scripts"]), "manifest_path": str(manifest_path)},
            ensure_ascii=True,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="CRAFTFLOW hook trust/provenance gate")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("script_path")
    check_parser.add_argument("--project-root")
    check_parser.add_argument("--manifest")
    check_parser.set_defaults(func=cmd_check)

    update_parser = subparsers.add_parser("update")
    update_parser.add_argument("--project-root")
    update_parser.add_argument("--manifest")
    update_parser.add_argument("--scripts-dir")
    update_parser.set_defaults(func=cmd_update)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
