#!/usr/bin/env python3
"""Relocation-drill guard for the router tests (P1 REM-FIX 3, extended in REM-FIX 4).

Run: python3 tests/fixtures/test_router_relocation_drills.py [--drills a,b] [--jobs N] [--list] [--help]
Applies planned section moves on scratch copies (none remain after P11) (router_relocation_drills.py) and runs the hook suite and
the router-reading fixtures against each copy. For every drill it asserts: no C-row test fails, no failing test is
unclassified, every failing K/F row's owner names the drill's phase, and no suite crashes. A failure counts only when the
test passes on the control copy, fails in the drill and is not a wall-clock budget test (DRILL_IGNORED_TESTS). Every drill
must also leave every heading text unique across the runtime corpus (the heading-uniqueness invariant), and a corpus that repeats
a heading must be rejected by that check (exercised on synthetic corpora since the P10 variant retired).
The self-tests below prove the harness itself: the classifier, the owner parser, the ignore rule and the heading check.
Prints `Results: N passed, M failed`; exits 1 on any failure. Takes several minutes (all drills in parallel, jobs default
min(4, cpus // 2)); `--help` and `--list` run nothing.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List

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
    check("every phase an owner cell names is an owner, including a ';' clause",
          drills.owner_phases("P11 (the build split moves anchors); P9 and P10 fill their own entries") == {9, 10, 11},
          str(drills.owner_phases("P11 (the build split moves anchors); P9 and P10 fill their own entries")))


def test_classifier() -> None:
    rows = {"hook::t_c": ("C", "none"), "hook::t_k": ("K", "P10"), "hook::t_f": ("F", "P9 and P11")}
    check("a failing C row is never acceptable", drills.classify("hook::t_c", 10, rows)[2] is False, "C accepted")
    check("a failing K row owned by the drill phase is acceptable", drills.classify("hook::t_k", 10, rows)[2] is True, "K/P10 rejected")
    check("a failing K row owned by another phase is not", drills.classify("hook::t_k", 9, rows)[2] is False, "K/P10 accepted at P9")
    check("an F row owned by 'P9 and P11' is acceptable at P11", drills.classify("hook::t_f", 11, rows)[2] is True, "F rejected at P11")
    check("an unknown test is UNCLASSIFIED and unacceptable", drills.classify("hook::t_x", 10, rows) == ("UNCLASSIFIED", "-", False),
          str(drills.classify("hook::t_x", 10, rows)))


def test_no_drill_remains_after_p11() -> None:
    have = {n for n in drills.DRILLS if n != "control"}
    check("DRILLS holds only the control (the p8_step4 drill retired when P8 moved step 4; p9_memfin, p9_memfin_pointer and p9_fallback_labels when P9 moved section 13 and the fallback paragraphs; the seven p10_* drills when P10 made those moves; the three p11_* drills when P11 made its moves)",
          have == set(), f"extra={sorted(have)}")


def test_every_drill_applies_and_moves_text() -> None:
    base = Path(tempfile.mkdtemp(prefix="drill-apply-"))
    try:
        for name, (phase, _, fn) in drills.DRILLS.items():
            if name == "control":
                continue
            root = drills.copy_plugin(base / name)
            tree = drills.Tree(root)
            before = {p: p.read_text(encoding="utf-8") for p in root.rglob("*.md") if "skills" in p.parts or "hooks" in p.parts}
            try:
                fn(tree)
            except drills.DrillError as err:
                check(f"drill {name} applies", False, str(err))
                continue
            after = {p: p.read_text(encoding="utf-8") for p in root.rglob("*.md") if "skills" in p.parts or "hooks" in p.parts}
            changed = [p for p in after if before.get(p) != after[p]]
            created = [p for p in after if p not in before]
            check(f"drill {name} changes a router file and moves the text into another file",
                  len(changed) >= 2 or (created and changed), f"changed={len(changed)} created={len(created)}")
            problems = drills.heading_problems(drills.rc.read_corpus(root))
            check(f"drill {name} leaves every heading unique", not problems, "; ".join(problems))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_duplicate_heading_variants_are_rejected() -> None:
    """The heading-uniqueness invariant rejects a repeated heading by construction (synthetic corpora: the registered variant retired in P10)."""
    check("no duplicate-heading variant is registered (x_p10_memload_dup retired with the P10 drills)", sorted(drills.VARIANTS) == [], str(sorted(drills.VARIANTS)))
    clean = {"skills/craftflow-router/SKILL.md": "## 2. Memory Load\n\ntext\n", "skills/craftflow-router/references/a.md": "## Other\n"}
    dup = {"skills/craftflow-router/SKILL.md": "## 2. Memory Load\n\ntext\n", "skills/craftflow-router/references/a.md": "## 2. Memory Load\n\nmoved\n"}
    check("a corpus that repeats a moved heading is rejected", bool(drills.heading_problems(dup)), "duplicate heading accepted")
    check("a corpus with unique headings passes", not drills.heading_problems(clean), str(drills.heading_problems(clean)))


# the self-tests of `evaluate` need a registered drill name for the phase lookup; this one is never run end to end
drills.ALL_DRILLS.setdefault("p11_synthetic", (11, "synthetic self-test entry (not a drill)", drills.d_control))


def _run_with(failed: Dict[str, str]) -> "drills.Run":
    run = drills.Run()
    run.failed.update(failed)
    return run


def test_timing_tests_and_control_failures_are_not_violations() -> None:
    rows = {"hook::t_c": ("C", "none"), "hook::t_k": ("K", "P11")}
    timing = ["fx::test_craftflow_jev_prompt_hint.py::test_new_modules_are_import_cheap",
              "fx::test_craftflow_context_nudge.py::test_module_import_cheap", "fx::test_other.py::test_x_import_cheap_budget"]
    report, violations = drills.evaluate("p11_synthetic", _run_with({k: "0.7s > 0.5s" for k in timing}), drills.Run(), rows)
    check("a wall-clock import-budget failure is not a violation", violations == [], str(violations))
    check("an ignored failure is logged, not hidden", sum("ignored" in line for line in report) == 3, str(report))
    report, violations = drills.evaluate("p11_synthetic", _run_with({"hook::t_c": "x"}), _run_with({"hook::t_c": "x"}), rows)
    check("a failure that also fails on the control copy is environmental", violations == [], str(violations))
    report, violations = drills.evaluate("p11_synthetic", _run_with({"hook::t_c": "x"}), drills.Run(), rows)
    check("a C row that passes on control and fails in the drill is a violation", len(violations) == 1, str(violations))
    check("a hook test with timing in its name is not ignored",
          not drills.is_ignored_test("hook::test_router_documents_task_metrics_and_timing_telemetry"), "timing fragment ignored a router test")
    heading_run = drills.Run()
    heading_run.heading_problems.append("duplicate heading '2. x' x2 (allowed 1) in ['a.md', 'b.md']")
    report, violations = drills.evaluate("p11_synthetic", heading_run, drills.Run(), rows)
    check("a run that repeats a heading is a violation", len(violations) == 1 and "duplicate heading" in violations[0], str(violations))


def test_default_jobs_is_half_the_cpus_capped_at_four() -> None:
    real = drills.os.cpu_count
    got = []
    try:
        for cpus in (None, 1, 2, 4, 8, 64):
            drills.os.cpu_count = lambda c=cpus: c
            got.append(drills.default_jobs())
    finally:
        drills.os.cpu_count = real
    check("default jobs = max(1, min(4, cpus // 2))", got == [1, 1, 1, 2, 4, 4], str(got))


def test_cli_help_and_list_do_not_run_drills() -> None:
    import subprocess
    start = time.time()
    helped = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--help"], capture_output=True, text=True, timeout=60)
    listed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--list"], capture_output=True, text=True, timeout=60)
    check("--help prints usage with --jobs and --drills, exits 0 and does not run drills",
          helped.returncode == 0 and "--jobs" in helped.stdout and "--drills" in helped.stdout and time.time() - start < 30,
          f"rc={helped.returncode} out={helped.stdout[:80]!r} err={helped.stderr[:120]!r}")
    names = [line.split("\t")[0] for line in listed.stdout.splitlines()]
    check("--list prints the control and the variants without running any",
          listed.returncode == 0 and len(names) == 1 + len(drills.VARIANTS), f"rc={listed.returncode} names={names} err={listed.stderr[:120]!r}")


def test_drills_end_to_end(selected: List[str], jobs: int) -> None:
    if not selected:
        check("no drills registered: the end-to-end run has nothing to apply", True)
        return
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
            check(f"drill {n}: no C failure, no unclassified failure, owners name P{drills.DRILLS[n][0]}, no crash, headings unique",
                  not violations, "; ".join(violations))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Relocation-drill guard for the router tests: self-tests plus every planned section move.")
    ap.add_argument("--drills", default="", help="comma-separated drill names to run end to end (default: every registered drill; none after P11)")
    ap.add_argument("--list", action="store_true", help="print the drills and variants, run nothing")
    ap.add_argument("--jobs", type=int, default=drills.default_jobs(), help="drills run in parallel (default min(4, cpus // 2))")
    args = ap.parse_args(argv)
    if args.list:
        for n, (phase, desc, _) in {**drills.DRILLS, **drills.VARIANTS}.items():
            print(f"{n}\tP{phase}\t{desc}")
        return 0
    selected = [n for n in args.drills.split(",") if n] or [n for n in drills.DRILLS if n != "control"]
    unknown = [n for n in selected if n not in drills.DRILLS]
    if unknown:
        print(f"unknown drills: {unknown}", file=sys.stderr)
        return 2
    test_owner_parser()
    test_classifier()
    test_no_drill_remains_after_p11()
    test_every_drill_applies_and_moves_text()
    test_duplicate_heading_variants_are_rejected()
    test_timing_tests_and_control_failures_are_not_violations()
    test_default_jobs_is_half_the_cpus_capped_at_four()
    test_cli_help_and_list_do_not_run_drills()
    test_drills_end_to_end(selected, max(1, args.jobs))
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
