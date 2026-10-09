#!/usr/bin/env python3
"""Relocation-drill guard for the router tests (P1 REM-FIX 3).

Run: python3 tests/fixtures/test_router_relocation_drills.py [--drills p9_memfin,p10_parent] [--jobs N]
Applies the plan's P9/P10/P11 section moves on scratch copies (router_relocation_drills.py) and runs the hook suite and
the router-reading fixtures against each copy. For every drill it asserts: no C-row test fails, no failing test is
unclassified, every failing K/F row's owner names the drill's phase, and no suite crashes. A test that passes in the
repo but fails after a planned move without an owner is exactly what the P1 classification must prevent.
The self-tests below prove the harness itself: the classifier, the owner parser and a move that must be caught.
Prints `Results: N passed, M failed`; exits 1 on any failure. Takes about 4-6 minutes (all drills in parallel).
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import router_relocation_drills as drills  # noqa: E402

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


def test_owner_parser() -> None:
    check("never-move and none name no phase", drills.owner_phases("never-move (P10 keeps both)") == set()
          and drills.owner_phases("none") == set(), "never-move/none parsed as a phase")
    check("single and combined owners", drills.owner_phases("P9 and P11") == {9, 11}, str(drills.owner_phases("P9 and P11")))
    check("a range owner covers each phase", drills.owner_phases("every relocation phase (P8–P12): rebaseline") == {8, 9, 10, 11, 12},
          str(drills.owner_phases("every relocation phase (P8–P12): rebaseline")))
    check("TBD-by-mover lists its phases", drills.owner_phases("TBD-by-mover (P9, P10 and P11 each update)") == {9, 10, 11},
          str(drills.owner_phases("TBD-by-mover (P9, P10 and P11 each update)")))


def test_classifier() -> None:
    rows = {"hook::t_c": ("C", "none"), "hook::t_k": ("K", "P10"), "hook::t_f": ("F", "P9 and P11")}
    check("a failing C row is never acceptable", drills.classify("hook::t_c", 10, rows)[2] is False, "C accepted")
    check("a failing K row owned by the drill phase is acceptable", drills.classify("hook::t_k", 10, rows)[2] is True, "K/P10 rejected")
    check("a failing K row owned by another phase is not", drills.classify("hook::t_k", 9, rows)[2] is False, "K/P10 accepted at P9")
    check("an F row owned by 'P9 and P11' is acceptable at P11", drills.classify("hook::t_f", 11, rows)[2] is True, "F rejected at P11")
    check("an unknown test is UNCLASSIFIED and unacceptable", drills.classify("hook::t_x", 10, rows) == ("UNCLASSIFIED", "-", False),
          str(drills.classify("hook::t_x", 10, rows)))


def test_every_drill_applies_and_moves_text() -> None:
    base = Path(tempfile.mkdtemp(prefix="drill-apply-"))
    try:
        for name, (phase, _, fn) in drills.DRILLS.items():
            if name == "control":
                continue
            root = drills.copy_plugin(base / name)
            tree = drills.Tree(root)
            before = {p: p.read_text(encoding="utf-8") for p in root.rglob("*.md") if "skills" in p.parts}
            try:
                fn(tree)
            except drills.DrillError as err:
                check(f"drill {name} applies", False, str(err))
                continue
            after = {p: p.read_text(encoding="utf-8") for p in root.rglob("*.md") if "skills" in p.parts}
            changed = [p for p in after if before.get(p) != after[p]]
            created = [p for p in after if p not in before]
            check(f"drill {name} changes a router file and moves the text into another file",
                  len(changed) >= 2 or (created and changed), f"changed={len(changed)} created={len(created)}")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_drills_end_to_end(selected: List[str], jobs: int) -> None:
    rows = drills.anchors_rows(drills.ANCHORS.read_text(encoding="utf-8"))
    base = Path(tempfile.mkdtemp(prefix="router-drills-"))
    try:
        from concurrent.futures import ThreadPoolExecutor
        names = ["control"] + selected
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            futures = {n: pool.submit(drills.run_drill, n, base, 3) for n in names}
            runs = {n: f.result() for n, f in futures.items()}
        for n in selected:
            report, violations = drills.evaluate(n, runs[n], runs["control"], rows)
            for line in report:
                print(f"    {n}: {line.strip()}")
            check(f"drill {n}: no C failure, no unclassified failure, owners name P{drills.DRILLS[n][0]}, no crash",
                  not violations, "; ".join(violations))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main(argv: List[str]) -> int:
    selected = [n for n in drills.DRILLS if n != "control"]
    jobs = 4
    for i, arg in enumerate(argv):
        if arg == "--drills" and i + 1 < len(argv):
            selected = [n for n in argv[i + 1].split(",") if n]
        if arg == "--jobs" and i + 1 < len(argv):
            jobs = int(argv[i + 1])
    unknown = [n for n in selected if n not in drills.DRILLS]
    if unknown:
        print(f"unknown drills: {unknown}", file=sys.stderr)
        return 2
    test_owner_parser()
    test_classifier()
    test_every_drill_applies_and_moves_text()
    test_drills_end_to_end(selected, jobs)
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
