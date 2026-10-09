#!/usr/bin/env python3
"""Coverage guard for router-test-anchors.md (P1 REM-FIX 1, finding L4).

Run: python3 tests/fixtures/test_router_test_anchors.py
Fails when a test that reads the runtime router text (as enumerated by router_test_enumerate.py) is neither a row of
router-test-anchors.md nor excluded by a line `Not listed: <label>::<test> — <reason>` (qualified name, non-empty
reason; a bare test name or a reasonless line classifies nothing and is reported), so a newly written router-reading
test cannot slip past the classification a later phase relies on. Also fails on stale entries (a row or exclusion whose
test no longer exists in its file) and checks that the "Rows: N (C=.., K=.., F=..)" line matches the table.
Limits are those of the enumerator: a convention check for the in-repo style, not a proof (see its docstring).
Prints `Results: N passed, M failed`; exits 1 on any failure.
"""
from __future__ import annotations

import ast
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Set, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import router_relocation_drills as drills  # noqa: E402
import router_test_enumerate as enum  # noqa: E402

ANCHORS = HERE / "router-test-anchors.md"
_passes = 0
_errors: List[str] = []


def check(name: str, cond: bool, reason: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason}")
        print(f"  FAIL: {name}: {reason or 'assertion false'}")


ROW = re.compile(r"^\| (?P<test>(?:hook|fx)::[^|]+?) \| (?P<kind>[CKF]) \|", re.M)


def table_rows(doc: str) -> List[Tuple[str, str]]:
    """(test cell, kind) for every table row."""
    return [(m.group("test"), m.group("kind")) for m in ROW.finditer(doc)]


def cell_key(cell: str) -> str:
    """Qualified `label::test` for a table cell: `hook::t`, `fx::file.py::t`, `fx::file.py::t (:54)`, `fx::file.py [t]`."""
    cell = re.sub(r"\s*\(:\d+\)$", "", cell.strip())
    bracket = re.match(r"^(fx::[^\s\[]+)\s*\[(\w+)\]$", cell)
    return f"{bracket.group(1)}::{bracket.group(2)}" if bracket else cell


EXCLUSION = re.compile(r"^(?:- )?Not listed: (?P<key>(?:hook|fx)::\S+) — (?P<reason>\S.*)$")


def exclusions(doc: str) -> Dict[str, str]:
    """{qualified `label::test`: reason} for every well-formed `Not listed: <label>::<test> — <reason>` line."""
    found: Dict[str, str] = {}
    for line in doc.splitlines():
        match = EXCLUSION.match(line)
        if match:
            found[match.group("key")] = match.group("reason")
    return found


def exclusion_problems(doc: str) -> List[str]:
    """Lines that start like an exclusion but lack a qualified `label::test` or a non-empty reason."""
    problems = []
    for line in doc.splitlines():
        text = line[2:] if line.startswith("- ") else line
        if text.startswith(("Not listed", "Also not listed")) and not EXCLUSION.match(line):
            no_reason = re.match(r"^(?:- )?Not listed: (?:hook|fx)::\S+(?: —\s*)?$", line)
            why = "has no reason after the dash" if no_reason else "is not `Not listed: <label>::<test> — <reason>`"
            problems.append(f"{line[:90]!r} {why}")
    return problems


def classified_names(doc: str) -> Set[str]:
    """Qualified `label::test` names that are a table row or a well-formed 'Not listed' exclusion."""
    return {cell_key(cell) for cell, _ in table_rows(doc)} | set(exclusions(doc))


def unclassified(enumerated: Dict[str, List[str]], doc: str) -> List[str]:
    known = classified_names(doc)
    return [f"{label}::{name}" for label, names in enumerated.items() for name in names if f"{label}::{name}" not in known]


def _defined(key: str) -> bool:
    """True when the file named by `label::test` defines `test` (any `def`, any depth) or carries the `[test]` section label."""
    label, _, name = key.rpartition("::")
    path = enum.HOOK_SUITE if label == "hook" else HERE / label.partition("::")[2]
    if not path.is_file():
        return False
    source = path.read_text(encoding="utf-8")
    defs = {n.name for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return name in defs or f"[{name}]" in source


def stale_rows(doc: str, enumerated: Dict[str, List[str]]) -> List[str]:
    """Table rows and exclusions whose test neither is enumerated nor is defined in its file (renamed or removed)."""
    seen = {f"{label}::{name}" for label, names in enumerated.items() for name in names}
    keys = [cell_key(cell) for cell, _ in table_rows(doc)] + list(exclusions(doc))
    return [key for key in keys if key not in seen and not _defined(key)]


def count_line_problem(doc: str) -> str:
    rows = table_rows(doc)
    kinds = {k: sum(1 for _, kk in rows if kk == k) for k in "CKF"}
    want = f"Rows: {len(rows)} (C={kinds['C']}, K={kinds['K']}, F={kinds['F']})."
    return "" if want in doc else f"expected the line {want!r}"


def test_every_router_reading_test_is_classified() -> None:
    doc = ANCHORS.read_text(encoding="utf-8")
    missing = unclassified(enum.enumerate_router_tests(), doc)
    check("every router-reading test is a row or in the Not listed note", not missing, f"unclassified: {missing}")


def test_row_count_line_matches_table() -> None:
    problem = count_line_problem(ANCHORS.read_text(encoding="utf-8"))
    check("row count line matches the table", not problem, problem)


def test_guard_detects_a_missing_row() -> None:
    doc = "| hook::test_a | C | corpus | none |\nNot listed: hook::test_b — reads another skill.\n"
    found = unclassified({"hook": ["test_a", "test_b", "test_c"]}, doc)
    check("guard flags only the test that is neither a row nor excluded", found == ["hook::test_c"], str(found))


def test_guard_detects_a_stale_count_line() -> None:
    doc = "Rows: 2 (C=1, K=0, F=0).\n| hook::test_a | C | corpus | none |\n"
    check("guard flags a stale count line", bool(count_line_problem(doc)), "stale count line accepted")


_N1_FIXTURE = '''import pathlib
from pathlib import Path

ROOT = Path(".")
SK: pathlib.Path = ROOT / "skills" / "craftflow-router" / "SKILL.md"


def test_ann_constant():
    assert SK.read_text()


async def test_async_ann_constant():
    assert SK.read_text()


class TestX:
    def test_h(self):
        assert Path("skills/craftflow-router/SKILL.md").read_text()


def test_marker_skill_md():
    assert Path("other/SKILL.md").read_text()


def test_unrelated():
    assert 1 + 1 == 2
'''


def test_enumerator_flags_annassign_async_and_class_methods() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "test_scratch.py"
        scratch.write_text(_N1_FIXTURE, encoding="utf-8")
        found = sorted(enum.router_reading_tests(scratch))
    want = ["test_ann_constant", "test_async_ann_constant", "test_h", "test_marker_skill_md"]
    check("enumerator flags AnnAssign constants, async tests, class methods and the SKILL.md marker", found == want, str(found))


_N2_FIXTURE = '''import craftflow_router_corpus as rc
import craftflow_router_corpus
from craftflow_router_corpus import read_corpus as rd
from craftflow_router_corpus import section_any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_module_alias():
    assert rc.corpus_text(rc.read_corpus(ROOT))


def test_from_import_alias_with_computed_path():
    base = ROOT.joinpath("sk" + "ills")
    assert rd(base.parent)


def test_plain_member_import():
    assert section_any("## 1.", None, plugin_root=ROOT)


def test_module_without_alias():
    assert craftflow_router_corpus.runtime_files(ROOT)


def test_bare_api_name_without_import():
    assert find_section("x", "## y")


def test_lookalike_is_not_the_api():
    assert memory_merge._find_section_span("a", "b") and not_read_corpus_here


def test_unrelated():
    assert 1 + 1 == 2
'''


def test_enumerator_flags_loader_import_aliases_and_api_names() -> None:
    """The test_zz_bypass.py shape (REM-FIX 3): an aliased import of the corpus loader plus a computed path, no marker in the test."""
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "test_zz_bypass.py"
        scratch.write_text(_N2_FIXTURE, encoding="utf-8")
        found = sorted(enum.router_reading_tests(scratch))
    want = ["test_bare_api_name_without_import", "test_from_import_alias_with_computed_path", "test_module_alias",
            "test_module_without_alias", "test_plain_member_import"]
    check("enumerator flags aliased loader imports, from-imports and bare API names, not look-alike identifiers", found == want, str(found))


def test_enumerator_covers_the_rule_inventory_fixtures() -> None:
    check("the rule-inventory fixtures are enumerated (only the guard itself is excluded)",
          enum.EXCLUDED_FILES == ("test_router_test_anchors.py",) and any(
              label.startswith("fx::test_rule_inventory") for label in enum.enumerate_router_tests()),
          f"EXCLUDED_FILES={enum.EXCLUDED_FILES}")


def test_bare_name_exclusion_no_longer_classifies() -> None:
    doc = "Not listed: test_whatever.\n"
    found = unclassified({"hook": ["test_whatever"]}, doc)
    check("a bare-name exclusion does not classify", found == ["hook::test_whatever"], str(found))
    check("a bare-name exclusion is reported as malformed", bool(exclusion_problems(doc)), "no exclusion problem reported")


def test_exclusion_without_reason_fails() -> None:
    doc = "Not listed: hook::test_x\n"
    problems = exclusion_problems(doc)
    check("an exclusion without a reason is reported", len(problems) == 1 and "reason" in problems[0], str(problems))
    check("an exclusion without a reason does not classify", unclassified({"hook": ["test_x"]}, doc) == ["hook::test_x"], "classified")


def test_qualified_exclusion_classifies_only_its_label() -> None:
    doc = "Not listed: hook::test_x — mentions a path only in a comment.\n"
    found = unclassified({"hook": ["test_x"], "fx::a.py": ["test_x"]}, doc)
    check("a qualified exclusion classifies only hook::test_x", found == ["fx::a.py::test_x"], str(found))
    check("a well-formed exclusion has no problems", exclusion_problems(doc) == [], str(exclusion_problems(doc)))


def test_ghost_row_is_stale() -> None:
    doc = "| hook::test_ghost | C | x | none |\n"
    found = stale_rows(doc, enum.enumerate_router_tests())
    check("a ghost row is reported as stale", found == ["hook::test_ghost"], f"stale_rows: {found}")


def test_real_exclusions_and_rows_are_well_formed() -> None:
    doc = ANCHORS.read_text(encoding="utf-8")
    check("every exclusion line is qualified and reasoned", exclusion_problems(doc) == [], str(exclusion_problems(doc)))
    stale = stale_rows(doc, enum.enumerate_router_tests())
    check("no table row or exclusion names a test that does not exist", not stale, f"stale_rows: {stale}")


def test_heading_text_is_unique_across_the_router_corpus() -> None:
    """REM-FIX 4 invariant: every heading text occurs once in the runtime corpus, except the frozen pre-existing duplicates."""
    problems = drills.heading_problems(drills.rc.read_corpus(drills.PLUGIN_ROOT))
    check("no heading repeats across the runtime router corpus beyond the frozen allowlist", not problems, "; ".join(problems))


def test_heading_allowlist_is_exact_so_it_can_only_shrink() -> None:
    """A phase that removes a pre-existing duplicate must delete its allowlist entry (exact match, not an upper bound)."""
    counts = {h: len(files) for h, files in drills.heading_occurrences(drills.rc.read_corpus(drills.PLUGIN_ROOT)).items() if len(files) > 1}
    check("the allowlist equals the live duplicate set", counts == drills.HEADING_DUP_ALLOWLIST,
          f"live-only={sorted(set(counts) - set(drills.HEADING_DUP_ALLOWLIST))} stale={sorted(set(drills.HEADING_DUP_ALLOWLIST) - set(counts))}")


def test_heading_check_flags_a_new_duplicate_and_a_grown_one() -> None:
    corpus = {"a.md": "# A\n\n## 2. Same\n", "b.md": "# B\n\n## 2. same\n\n### `Dup` heading\n", "c.md": "### Dup heading\n"}
    found = drills.heading_problems(corpus, allowlist={})
    check("a repeated heading (case, backticks, # level ignored) is reported once per heading", len(found) == 2 and any("2. same" in f for f in found) and any("dup heading" in f for f in found), str(found))
    check("an allowlisted duplicate passes until it grows", drills.heading_problems(corpus, allowlist={"2. same": 2, "dup heading": 2}) == [], "allowlisted duplicate rejected")
    grown = dict(corpus, **{"d.md": "## 2. Same\n"})
    check("an allowlisted duplicate that gains a third copy is reported", len(drills.heading_problems(grown, allowlist={"2. same": 2, "dup heading": 2})) == 1, "growth accepted")


def main() -> int:
    test_every_router_reading_test_is_classified()
    test_row_count_line_matches_table()
    test_guard_detects_a_missing_row()
    test_guard_detects_a_stale_count_line()
    test_enumerator_flags_annassign_async_and_class_methods()
    test_enumerator_flags_loader_import_aliases_and_api_names()
    test_enumerator_covers_the_rule_inventory_fixtures()
    test_bare_name_exclusion_no_longer_classifies()
    test_exclusion_without_reason_fails()
    test_qualified_exclusion_classifies_only_its_label()
    test_ghost_row_is_stale()
    test_real_exclusions_and_rows_are_well_formed()
    test_heading_text_is_unique_across_the_router_corpus()
    test_heading_allowlist_is_exact_so_it_can_only_shrink()
    test_heading_check_flags_a_new_duplicate_and_a_grown_one()
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
