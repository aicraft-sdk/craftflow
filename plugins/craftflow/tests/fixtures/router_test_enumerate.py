#!/usr/bin/env python3
"""Enumerates the tests that read the runtime router text, so `router-test-anchors.md` can be checked against them.

Run: python3 tests/fixtures/router_test_enumerate.py [--names]
Prints the per-file count of router-reading `def test_` functions in the hook suite (scripts/craftflow_hook_unit_tests.py)
and in tests/fixtures/test_*.py. With --names it also prints one `<file>::<test>` line per test.

A test reads the router when its source, or the source of a module-level helper or constant it uses (followed
transitively inside the same file), names a router path or one of the corpus helpers (ROUTER_MARKERS), or uses a name
bound by an import of the corpus loader (module alias or imported API member, `as` aliases included). Excluded: the
guard itself (test_router_test_anchors.py); in test_router_xrefs.py only the tests that read the real tree (PLUGIN_ROOT)
count, the rest build synthetic corpora from string constants. The test_rule_inventory*.py fixtures are enumerated: their
end-to-end tests read the live router at run time, the synthetic ones are excluded one by one with a reason.
Static analysis only (ast): nothing is imported or executed. Python 3.9 stdlib.

Covered shapes: top-level `def`/`async def test_*` and `test_*` methods of top-level classes; helpers and constants bound
by `=`, `: T =` and `+=` at module level, followed transitively inside the same file; names bound by `import x [as y]`
and `from x import a [as b]` of the corpus loader (`craftflow_router_corpus`, or its API: read_corpus, corpus_text,
section_any, find_section, runtime_files). Known limits, so the guard is a convention check for the in-repo style and not
a proof: a helper imported from a module that is not the corpus loader and hides the loader behind its own name (`from
helper import read_router`), a router file reached through a glob or a computed path with no marker and no loader
import in the test's own source, and tests defined in nested functions or built dynamically are not seen. Fixtures that
run checks at module level without a `def test_` (test_agent_structure.py) and tests/live/* are outside the enumeration;
a SyntaxError in any test_*.py fixture aborts the enumeration with a traceback (no `Results:` line).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parents[1]
HOOK_SUITE = PLUGIN_ROOT / "scripts" / "craftflow_hook_unit_tests.py"

ROUTER_MARKERS = (
    "craftflow-router",
    "router-protocol",
    "SKILL.md",
    "craftflow_router_corpus",
    "router_corpus",
    "_router_corpus_text",
    "_router_section_any",
    "_router_section_or_none",
    "_router_section_first",
    "_shared",
    # The corpus loader's public API: a test that calls it reads the runtime router even when its own source names no
    # router path (REM-FIX 3: test_router_xrefs.py::test_corpus_xrefs_resolve built its corpus this way).
    "read_corpus",
    "corpus_text",
    "section_any",
    "find_section",
    "runtime_files",
)
# Identifier markers: an import of one of these (`import craftflow_router_corpus as rc`, `from craftflow_router_corpus
# import read_corpus as rd`) makes the bound alias a router-reading name even though the alias hides the marker.
IMPORT_API_NAMES = ("read_corpus", "corpus_text", "section_any", "find_section", "runtime_files")
IMPORT_MODULE_MARKERS = ("craftflow_router_corpus", "router_corpus")
REAL_TREE_ONLY_FILE = "test_router_xrefs.py"  # only its tests that read the real tree (PLUGIN_ROOT) count
# Exact filenames, not prefixes: a new fixture must never be exempted by accident because of its name. Only the guard
# itself is skipped (it quotes router paths in its own self-test fixtures); the test_rule_inventory*.py fixtures are
# enumerated, because their end-to-end tests read the live router (the rest are excluded one by one, with a reason, in
# router-test-anchors.md).
EXCLUDED_FILES = (
    "test_router_test_anchors.py",
)


def _names_router(segment: str) -> bool:
    """True when `segment` holds a router marker. The corpus-loader API names match as whole identifiers (so
    `memory_merge._find_section_span` is not `find_section`); the other markers match as substrings."""
    api = IMPORT_API_NAMES
    if any(m in segment for m in ROUTER_MARKERS if m not in api):
        return True
    return any(re.search(r"(?<![A-Za-z0-9_])" + re.escape(m) + r"(?![A-Za-z0-9_])", segment) for m in api)


def _segment(lines: List[str], node: ast.AST) -> str:
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])  # type: ignore[attr-defined]


def _names_used(node: ast.AST) -> Set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _router_import_names(tree: ast.Module) -> Set[str]:
    """Names bound by module-level imports of the corpus loader (module aliases and imported API members, `as` aliases
    included), so `rc.read_corpus(...)` or `rd(...)` is seen even when the test's own source names no router path."""
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(m in alias.name for m in IMPORT_MODULE_MARKERS):
                    names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            from_loader = any(m in (node.module or "") for m in IMPORT_MODULE_MARKERS)
            for alias in node.names:
                if from_loader or alias.name in IMPORT_API_NAMES or any(m in alias.name for m in IMPORT_MODULE_MARKERS):
                    names.add(alias.asname or alias.name)
    names.discard("*")
    return names


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
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name.startswith("test_"):
                    tests[member.name] = member
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    helpers[target.id] = node
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            helpers[node.target.id] = node
    reading = {name for name, node in helpers.items() if _names_router(_segment(lines, node))}
    reading |= _router_import_names(tree)
    changed = True
    while changed:  # a helper that uses a router helper reads the router too
        changed = False
        for name, node in helpers.items():
            if name not in reading and _names_used(node) & reading:
                reading.add(name)
                changed = True
    found = []
    for name, node in tests.items():
        if _names_router(_segment(lines, node)) or _names_used(node) & reading:
            found.append(name)
    if path.name == REAL_TREE_ONLY_FILE:  # its other tests build synthetic corpora from string constants
        found = [n for n in found if "PLUGIN_ROOT" in _segment(lines, tests[n])]
    return found


def enumerate_router_tests() -> Dict[str, List[str]]:
    """{short file label: [test names]} for the hook suite (`hook`) and each tests/fixtures/test_*.py (`fx::<file>`)."""
    result: Dict[str, List[str]] = {"hook": router_reading_tests(HOOK_SUITE)}
    for path in sorted(HERE.glob("test_*.py")):
        if path.name in EXCLUDED_FILES:
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
