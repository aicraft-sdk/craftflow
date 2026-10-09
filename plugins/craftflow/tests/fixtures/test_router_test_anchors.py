#!/usr/bin/env python3
"""Coverage guard for router-test-anchors.md (P1 REM-FIX 1, finding L4).

Run: python3 tests/fixtures/test_router_test_anchors.py
Fails when a test that reads the runtime router text (as enumerated by router_test_enumerate.py) is neither a row of
router-test-anchors.md nor named in its "Not listed" note, so a newly written router-reading test cannot slip past the
classification a later phase relies on. Also checks that the "Rows: N (C=.., K=.., F=..)" line matches the table.
Prints `Results: N passed, M failed`; exits 1 on any failure.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
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


def classified_names(doc: str) -> Set[str]:
    """Bare test names that are a table row or are named in the 'Not listed' note."""
    names: Set[str] = set()
    for cell, _ in table_rows(doc):
        names.update(re.findall(r"test_\w+", cell))
    for line in doc.splitlines():
        if line.startswith("Not listed") or line.startswith("Also not listed") or " Also not listed" in line:
            names.update(re.findall(r"test_\w+", line))
    return names


def unclassified(enumerated: Dict[str, List[str]], doc: str) -> List[str]:
    known = classified_names(doc)
    return [f"{label}::{name}" for label, names in enumerated.items() for name in names if name not in known]


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
    doc = "| hook::test_a | C | corpus | none |\nNot listed (x): `test_b`.\n"
    found = unclassified({"hook": ["test_a", "test_b", "test_c"]}, doc)
    check("guard flags only the test that is neither a row nor excluded", found == ["hook::test_c"], str(found))


def test_guard_detects_a_stale_count_line() -> None:
    doc = "Rows: 2 (C=1, K=0, F=0).\n| hook::test_a | C | corpus | none |\n"
    check("guard flags a stale count line", bool(count_line_problem(doc)), "stale count line accepted")


def main() -> int:
    test_every_router_reading_test_is_classified()
    test_row_count_line_matches_table()
    test_guard_detects_a_missing_row()
    test_guard_detects_a_stale_count_line()
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
