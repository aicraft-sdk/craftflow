#!/usr/bin/env python3
"""Docs-rot ratchet for the craftflow plugin.

Runs the pure checks D1..D10 (craftflow_docs_rot_checks.py) against the repository and compares the violation ids with
tests/fixtures/docs-rot-baseline.json:

    NEW <id>     a violation that is not in the baseline          -> exit 1
    STALE <id>   a baseline id that is no longer violated          -> exit 1
    --strict     also fails while the baseline is non-empty
    --print      list today's violation ids and exit 0 (used to seed the baseline)

The baseline may only shrink: it must be a subset of the baseline at `git merge-base HEAD origin/main` (a baseline absent
there is a first introduction and is allowed). The check is never skipped; an unresolvable base ref fails closed.
Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))
import craftflow_docs_rot_checks as checks  # noqa: E402

ROOT = Path(__file__).resolve().parents[5]
PLUGIN_REL = "tools/craftflow-plugin/plugins/craftflow"
BASELINE_REL = f"{PLUGIN_REL}/tests/fixtures/docs-rot-baseline.json"


def _literal_keys(source: str, name: str) -> Set[str]:
    """Keys of the module-level dict literal `name` (plain or annotated assignment), read without importing the module."""
    for node in ast.parse(source).body:
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        if isinstance(target, ast.Name) and target.id == name and getattr(node, "value", None) is not None:
            return set(ast.literal_eval(node.value))
    return set()


def gather(root: Path) -> dict:
    plugin = root / PLUGIN_REL

    def read(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    agents = sorted((plugin / "agents").glob("*.md"))
    skills = sorted(p.parent.name for p in (plugin / "skills").glob("*/SKILL.md"))
    readmap = json.loads(read(plugin / "config/router-readmap.json"))
    return {
        "agent_stems": [p.stem for p in agents],
        "skills_by_agent": {p.stem: checks.agent_skills(read(p)) for p in agents},
        "skill_names": skills,
        "top_text": read(root / checks.README_TOP),
        "plugin_text": read(root / checks.README_PLUGIN),
        "protocol": read(plugin / "skills/_shared/router-protocol.md"),
        "cursor_text": read(plugin / "skills/cursor-router/SKILL.md"),
        "install_text": read(plugin / "install-cursor.sh"),
        "hooks_readme": read(plugin / "hooks/README.md"),
        "claude_hooks": read(plugin / "hooks/hooks.json"),
        "cursor_hooks": read(plugin / "hooks.json"),
        "existing_scripts": [p.name for p in (plugin / "scripts").iterdir() if p.is_file()],
        "plugin_version": json.loads(read(plugin / ".claude-plugin/plugin.json"))["version"],
        "marketplace": json.loads(read(root / ".claude-plugin/marketplace.json")),
        "changelog": read(root / "tools/craftflow-plugin/CHANGELOG.md"),
        "readmap_variants": readmap["variants"],
        "jev_labels": _literal_keys(read(plugin / "scripts/craftflow_jev_prompt_hint.py"), "WORKFLOWS"),
        "chain_keys": _literal_keys(read(plugin / "scripts/craftflow_status_report.py"), "_CHAINS"),
    }


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def merge_base_baseline(root: Path, base_ref: str) -> Optional[Set[str]]:
    """The baseline ids at `git merge-base HEAD <base_ref>`; None when the file is absent there (first introduction)."""
    mb = _git(root, "merge-base", "HEAD", base_ref)
    if mb.returncode != 0 or not mb.stdout.strip():
        raise RuntimeError(f"cannot resolve the merge-base of HEAD and {base_ref}: {mb.stderr.strip() or 'no common ancestor'}")
    spec = f"{mb.stdout.strip()}:{BASELINE_REL}"
    if _git(root, "cat-file", "-e", spec).returncode != 0:
        return None
    shown = _git(root, "show", spec)
    if shown.returncode != 0:
        raise RuntimeError(f"cannot read {spec}: {shown.stderr.strip()}")
    return set(json.loads(shown.stdout)["ids"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="craftflow docs-rot ratchet")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this checkout)")
    parser.add_argument("--print", action="store_true", dest="print_ids", help="print the current violation ids and exit 0")
    parser.add_argument("--strict", action="store_true", help="also fail while the baseline is non-empty")
    parser.add_argument("--base-ref", default="origin/main", help="ref whose merge-base bounds the baseline (default origin/main)")
    args = parser.parse_args(argv)

    current = checks.run_all(gather(args.root))
    if args.print_ids:
        print("\n".join(current))
        return 0

    baseline_path = args.root / BASELINE_REL
    if not baseline_path.is_file():
        print(f"FAIL baseline file missing: {BASELINE_REL}")
        return 1
    baseline = set(json.loads(baseline_path.read_text(encoding="utf-8"))["ids"])
    failed = False

    try:
        allowed = merge_base_baseline(args.root, args.base_ref)
    except RuntimeError as exc:
        print(f"FAIL {exc}")
        return 1
    if allowed is not None and baseline - allowed:
        print(f"FAIL baseline grew past {args.base_ref}: {', '.join(sorted(baseline - allowed))}")
        failed = True

    for rot_id in sorted(set(current) - baseline):
        print(f"NEW {rot_id}")
        failed = True
    for rot_id in sorted(baseline - set(current)):
        print(f"STALE {rot_id}")
        failed = True
    if args.strict and baseline:
        print(f"FAIL --strict: the baseline still lists {len(baseline)} id(s)")
        failed = True

    print(f"docs-rot: {'FAIL' if failed else 'OK'} ({len(current)} violation(s), {len(baseline)} baselined)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
