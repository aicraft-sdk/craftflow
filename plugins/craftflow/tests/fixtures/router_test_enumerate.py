#!/usr/bin/env python3
"""Enumerates the tests that read the runtime router text, so `router-test-anchors.md` can be checked against them.

Run: python3 tests/fixtures/router_test_enumerate.py [--names]
Prints the per-file count of router-reading `def test_` functions in the hook suite (scripts/craftflow_hook_unit_tests.py)
and in tests/fixtures/test_*.py. With --names it also prints one `<file>::<test>` line per test.

A test reads the router when its source, or the source of a module-level helper or constant it uses (followed
transitively inside the same file), names a router path or one of the corpus helpers (ROUTER_MARKERS). Excluded:
test_rule_inventory*.py and test_router_test_anchors.py (synthetic corpora, or the guard itself); in test_router_xrefs.py
only the tests that read the real tree (PLUGIN_ROOT) count, the rest build synthetic corpora from string constants.
Static analysis only (ast): nothing is imported or executed. Python 3.9 stdlib.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Dict, List, Set

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parents[1]
HOOK_SUITE = PLUGIN_ROOT / "scripts" / "craftflow_hook_unit_tests.py"

ROUTER_MARKERS = (
    "craftflow-router",
    "router-protocol",
    "craftflow_router_corpus",
    "router_corpus",
    "_router_corpus_text",
    "_router_section_any",
    "_shared",
)
REAL_TREE_ONLY_FILE = "test_router_xrefs.py"  # only its tests that read the real tree (PLUGIN_ROOT) count
EXCLUDED_FILE_PREFIXES = ("test_rule_inventory", "test_router_test_anchors")


def _segment(lines: List[str], node: ast.AST) -> str:
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])  # type: ignore[attr-defined]


def _names_used(node: ast.AST) -> Set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def router_reading_tests(path: Path) -> List[str]:
    """Names of the top-level `test_*` functions in `path` that read the router corpus."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    tests: Dict[str, ast.AST] = {}
    helpers: Dict[str, ast.AST] = {}  # module-level non-test functions and assigned constants
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            (tests if node.name.startswith("test_") else helpers)[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    helpers[target.id] = node
    reading = {name for name, node in helpers.items() if any(m in _segment(lines, node) for m in ROUTER_MARKERS)}
    changed = True
    while changed:  # a helper that uses a router helper reads the router too
        changed = False
        for name, node in helpers.items():
            if name not in reading and _names_used(node) & reading:
                reading.add(name)
                changed = True
    found = []
    for name, node in tests.items():
        if any(m in _segment(lines, node) for m in ROUTER_MARKERS) or _names_used(node) & reading:
            found.append(name)
    if path.name == REAL_TREE_ONLY_FILE:  # its other tests build synthetic corpora from string constants
        found = [n for n in found if "PLUGIN_ROOT" in _segment(lines, tests[n])]
    return found


def enumerate_router_tests() -> Dict[str, List[str]]:
    """{short file label: [test names]} for the hook suite (`hook`) and each tests/fixtures/test_*.py (`fx::<file>`)."""
    result: Dict[str, List[str]] = {"hook": router_reading_tests(HOOK_SUITE)}
    for path in sorted(HERE.glob("test_*.py")):
        if path.name.startswith(EXCLUDED_FILE_PREFIXES):
            continue
        names = router_reading_tests(path)
        if names:
            result[f"fx::{path.name}"] = names
    return result


def main(argv: List[str]) -> int:
    enumerated = enumerate_router_tests()
    total = 0
    for label, names in enumerated.items():
        print(f"{label}: {len(names)}")
        total += len(names)
        if "--names" in argv:
            for name in names:
                print(f"  {label}::{name}")
    print(f"total: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
