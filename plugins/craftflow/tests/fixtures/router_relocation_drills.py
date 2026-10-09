#!/usr/bin/env python3
"""Router relocation drills (P1 REM-FIX 3): simulate the plan's P9/P10/P11 section moves on scratch copies.

Run: python3 tests/fixtures/router_relocation_drills.py [--drills a,b] [--list] [--jobs N] [--keep] [--verbose]

For each drill the harness copies `tools/craftflow-plugin` (minus `ref-*`) to a temp dir, moves verbatim router sections
between files the way the plan's P9/P10/P11 file lists do, runs the hook suite and the router-reading fixtures on the
copy, and prints every failing test with its `router-test-anchors.md` row (`kind/owner`, or `UNCLASSIFIED`).
A `control` run (no move) is executed first; whatever fails there is environmental (the copy is not a git checkout of
the repo: ADR-exists tests, git-lock rule-inventory tests) and is subtracted from every drill.

A drill passes when every failing test is a K/F row whose owner names the drill's phase, no C row fails, no failing
test is unclassified, and no suite crashes (no summary line, or a fixture traceback with no `Results:` line).
Temp dirs follow $TMPDIR (set it to keep the copies inside a given workspace). Python 3.9 stdlib.
"""
from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
sys.path.insert(0, str(HERE))
import craftflow_router_corpus as rc  # noqa: E402

ANCHORS = HERE / "router-test-anchors.md"
SELF_NAMES = ("test_router_relocation_drills.py", "router_relocation_drills.py")
# Fixtures that never read router text (they take 40+ s together); everything else that names a router marker runs.
FIXTURE_MARKERS = ("craftflow-router", "router-protocol", "SKILL.md", "_shared", "rule_inventory", "router_corpus",
                   "router-readmap", "router-policy")
SKIP_FIXTURES = ("test_router_test_anchors.py", "test_router_relocation_drills.py")
ROW = re.compile(r"^\| (?P<test>(?:hook|fx)::[^|]+?) \| (?P<kind>[CKF]) \| (?P<where>[^|]*) \| (?P<owner>[^|]*) \|", re.M)


# ------------------------------------------------------------------------------------------------ move primitives


class DrillError(RuntimeError):
    """The drill could not find the text it is supposed to move (the router changed shape)."""


def _read(path: Path) -> List[str]:
    return path.read_text(encoding="utf-8").split("\n")


def _write(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def span(lines: List[str], heading: str, nth: int = 0) -> Tuple[int, int]:
    """(start, end) line indexes of the section whose heading matches, `end` exclusive (next heading of <= level)."""
    level, want = rc._split_hash(heading)
    hits = [h for h in rc.headings("\n".join(lines)) if rc.norm_text(h[2]) == rc.norm_text(want) and (level is None or h[1] == level)]
    if len(hits) <= nth:
        raise DrillError(f"heading not found: {heading!r}")
    start, lvl, _ = hits[nth]
    end = next((h[0] for h in rc.headings("\n".join(lines)) if h[0] > start and h[1] <= lvl), len(lines))
    return start, end


def line_index(lines: List[str], prefix: str, after: int = 0) -> int:
    for i in range(after, len(lines)):
        if lines[i].startswith(prefix):
            return i
    raise DrillError(f"no line starting with {prefix!r}")


def cut(path: Path, start: int, end: int, replacement: List[str]) -> List[str]:
    """Remove lines [start, end) from `path` (replaced by `replacement`) and return the removed block."""
    lines = _read(path)
    block = lines[start:end]
    if not "".join(block).strip():
        raise DrillError(f"empty block cut from {path.name} [{start}:{end}]")
    _write(path, lines[:start] + replacement + lines[end:])
    return block


def put(path: Path, block: List[str], title: Optional[str] = None) -> None:
    """Append `block` to `path` (created when missing, with `title` as its first line)."""
    existing = _read(path) if path.exists() else ([title, ""] if title else [])
    sep = [""] if existing and existing[-1].strip() else []
    _write(path, existing + sep + block + [""])


class Tree:
    """Paths inside one scratch plugin copy."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skill = root / "skills" / "craftflow-router" / "SKILL.md"
        self.refs = root / "skills" / "craftflow-router" / "references"
        self.shared = root / "skills" / "_shared"
        self.protocol = self.shared / "router-protocol.md"


def d_control(t: Tree) -> None:
    return None


def d_p9_memfin(t: Tree) -> None:
    """P9: `## 13. Memory Finalization` moves verbatim to references/memory-finalize.md; the kernel keeps a pointer."""
    lines = _read(t.skill)
    s, e = span(lines, "## 13. Memory Finalization")
    block = cut(t.skill, s, e, ["Memory finalization: read `references/memory-finalize.md` when a memory task is runnable.", ""])
    put(t.refs / "memory-finalize.md", block, "# Memory finalize (P9)")


def d_p10_parent(t: Tree) -> None:
    """P10: `### Parent workflow creation` (SKILL.md § 6) moves to references/workflow-start.md."""
    lines = _read(t.skill)
    s, e = span(lines, "### Parent workflow creation")
    block = cut(t.skill, s, e, ["Parent workflow creation: read `references/workflow-start.md` before creating a workflow.", ""])
    put(t.refs / "workflow-start.md", block, "# Workflow start (P10)")


def d_p10_resume(t: Tree) -> None:
    """P10: the body of `## 4. Resume And Hydration` moves to references/resume.md; the kernel heading stays as the trigger."""
    lines = _read(t.skill)
    s, e = span(lines, "## 4. Resume And Hydration")
    block = cut(t.skill, s + 1, e, ["", "Trigger: when resuming, read `references/resume.md`.", ""])
    put(t.refs / "resume.md", ["# Resume And Hydration (body of the kernel `4.` trigger)", ""] + block)


def d_p10_0a(t: Tree) -> None:
    """P10: the `## 0a.` body moves to references/workflow-start.md under the same heading; the kernel keeps heading + trigger."""
    lines = _read(t.skill)
    s, e = span(lines, "## 0a. Task Tool Capability Detection")
    block = cut(t.skill, s + 1, e, ["", "Trigger: before the first workflow artifact write of a session, read `references/workflow-start.md`.", ""])
    put(t.refs / "workflow-start.md", [lines[s], ""] + block, "# Workflow start (P10)")


def d_p10_section3(t: Tree) -> None:
    """P10: `## 3. Task Metadata Contract` moves verbatim to references/workflow-start.md."""
    lines = _read(t.skill)
    s, e = span(lines, "## 3. Task Metadata Contract")
    block = cut(t.skill, s, e, ["Task metadata contract: read `references/workflow-start.md` before creating tasks.", ""])
    put(t.refs / "workflow-start.md", block, "# Workflow start (P10)")


def d_p10_worktree13(t: Tree) -> None:
    """P10: Fast Path Detection and Worktree Isolation steps 1-3 move to references/build-workflow.md."""
    lines = _read(t.skill)
    s, e = span(lines, "### Fast Path Detection")
    fpd = cut(t.skill, s, e, ["Fast Path Detection: see `references/build-workflow.md`.", ""])
    lines = _read(t.skill)
    ws, _ = span(lines, "### Worktree Isolation (BUILD Default)")
    first = line_index(lines, "1. At BUILD start", ws)
    step4 = line_index(lines, "4. After `integration-verifier` returns PASS", first)
    steps = cut(t.skill, first, step4, ["(Steps 1-3: see `references/build-workflow.md`.)", ""])
    put(t.refs / "build-workflow.md", fpd + ["### Worktree Isolation (BUILD Default)", ""] + steps)


def d_p11_proto_split(t: Tree) -> None:
    """P11: step 1a of `## Resolve Project Root` moves to _shared/router-protocol-multirepo.md."""
    lines = _read(t.protocol)
    s = line_index(lines, "   **1a. Multi-repo workspace root resolution**")
    e = line_index(lines, "(Host-specific note: both hosts run this exact algorithm", s)
    block = cut(t.protocol, s, e, ["   **1a.** Multi-repo workspace root resolution: see `_shared/router-protocol-multirepo.md`.", ""])
    put(t.shared / "router-protocol-multirepo.md", block, "# Multi-repo workspace root resolution (step 1a)")


def d_p11_build_split(t: Tree) -> None:
    """P11: references/build-workflow.md splits into build-graph-standard, build-graph-fast and build-fast-path-escalation."""
    path = t.refs / "build-workflow.md"
    lines = _read(path)
    std, _ = span(lines, "### BUILD task graph")
    fast, fast_end = span(lines, "### BUILD task graph — fast path")
    esc, esc_end = span(lines, "### Fast Path Escalation")
    parts = {"build-graph-standard.md": lines[std:fast], "build-graph-fast.md": lines[fast:esc],
             "build-fast-path-escalation.md": lines[esc:esc_end]}
    if esc_end != len(lines) or fast_end > esc:
        raise DrillError("build-workflow.md no longer ends with the escalation section")
    _write(path, lines[:std] + ["BUILD task graphs: see `build-graph-standard.md`, `build-graph-fast.md` and `build-fast-path-escalation.md`.", ""])
    for name, block in parts.items():
        put(t.refs / name, block)


# name -> (phase, one-line description, function)
DRILLS: Dict[str, Tuple[int, str, Callable[[Tree], None]]] = {
    "control": (0, "no move (environmental baseline)", d_control),
    "p9_memfin": (9, "## 13 Memory Finalization -> references/memory-finalize.md", d_p9_memfin),
    "p10_parent": (10, "### Parent workflow creation -> references/workflow-start.md", d_p10_parent),
    "p10_resume": (10, "## 4 body -> references/resume.md, kernel heading kept as trigger", d_p10_resume),
    "p10_0a": (10, "## 0a body -> references/workflow-start.md, trigger stays under ## 0a", d_p10_0a),
    "p10_section3": (10, "## 3 Task Metadata Contract -> references/workflow-start.md", d_p10_section3),
    "p10_worktree13": (10, "Fast Path Detection + Worktree steps 1-3 -> references/build-workflow.md", d_p10_worktree13),
    "p11_proto_split": (11, "step 1a -> _shared/router-protocol-multirepo.md", d_p11_proto_split),
    "p11_build_split": (11, "build-workflow.md -> graph-standard / graph-fast / escalation", d_p11_build_split),
}


# ------------------------------------------------------------------------------------------------ anchors lookup


def anchors_rows(doc: str) -> Dict[str, Tuple[str, str]]:
    """{qualified `label::test` or `label::[section]`: (kind, owner)} for every table row."""
    rows: Dict[str, Tuple[str, str]] = {}
    for m in ROW.finditer(doc):
        cell = re.sub(r"\s*\(:\d+\)$", "", m.group("test").strip())
        bracket = re.match(r"^(fx::[^\s\[]+)\s*\[(\w+)\]$", cell)
        key = f"{bracket.group(1)}::{bracket.group(2)}" if bracket else cell
        rows[key] = (m.group("kind"), m.group("owner").strip())
    return rows


def owner_phases(owner: str) -> Set[int]:
    """Phase numbers an owner cell names. `none` and `never-move` name no phase, whatever follows them."""
    text = owner.strip().strip("`")
    if text.startswith(("none", "never-move")):
        return set()
    phases: Set[int] = set()
    if "every relocation phase" in text:
        phases.update(range(8, 13))
    for lo, hi in re.findall(r"P(\d+)\s*[–-]\s*P?(\d+)", text):
        phases.update(range(int(lo), int(hi) + 1))
    phases.update(int(n) for n in re.findall(r"\bP(\d+)\b", text))
    return phases


# ------------------------------------------------------------------------------------------------ failure parsing


def _strings(node: ast.AST) -> List[str]:
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
    return out


def _test_defs(path: Path) -> Dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")}


def hook_names_to_tests(path: Path) -> Dict[str, str]:
    """{`name = "..."` literal: test function}; the hook suite reports failures by that literal."""
    mapping: Dict[str, str] = {}
    for fn_name, node in _test_defs(path).items():
        for n in ast.walk(node):
            if (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "name" for t in n.targets)
                    and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)):
                mapping.setdefault(n.value.value, fn_name)
    return mapping


def locate_test(path: Path, check_name: str) -> Optional[str]:
    """The test function a failing check belongs to: the function of that name, else the one function whose string
    literals equal the check's name, else the one whose literals are contained in it (None when ambiguous or absent)."""
    defs = _test_defs(path)
    if check_name in defs:
        return check_name
    exact = [fn for fn, node in defs.items() if check_name in _strings(node)]
    if len(exact) == 1:
        return exact[0]
    loose = [fn for fn, node in defs.items() if any(len(s) > 10 and s in check_name for s in _strings(node))]
    return loose[0] if not exact and len(loose) == 1 else None


class Run:
    """Outcome of one drill: failing test keys plus crash notes."""

    def __init__(self) -> None:
        self.failed: Dict[str, str] = {}  # key (`hook::t`, `fx::f.py::t`, `fx::f.py[section]`, `fx::f.py::?`) -> reason
        self.crashes: List[str] = []


def _run(cmd: List[str], cwd: Path, timeout: int) -> Tuple[int, str]:
    try:
        res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"
    return res.returncode, (res.stdout or "") + "\n" + (res.stderr or "")


def run_hook_suite(root: Path, out: Run) -> None:
    suite = root / "scripts" / "craftflow_hook_unit_tests.py"
    code, text = _run([sys.executable, str(suite)], root, 900)
    if not re.search(r"^craftflow_hook_unit_tests: (OK|FAIL)", text, re.M):
        out.crashes.append(f"hook suite crashed (exit {code}): {text.strip().splitlines()[-1][:200] if text.strip() else ''}")
        return
    names = hook_names_to_tests(suite)
    for m in re.finditer(r"^FAIL \[(?P<name>[^\]]+)\]: (?P<reason>.*)$", text, re.M):
        fn = names.get(m.group("name")) or locate_test(suite, m.group("name"))
        out.failed[f"hook::{fn}" if fn else f"hook::?{m.group('name')}"] = m.group("reason")[:160]


SUMMARY_LINE = re.compile(r"^(Results: \d+ passed|FAIL \(\d+ passed|OK \(\d+ passed)", re.M)


def run_fixture(path: Path, out: Run) -> None:
    """Failing checks of one fixture. A check is attributed to (1) the test function whose literals hold its name, else
    (2) the `[section]` header printed before it (module-level fixtures), else (3) the file (`fx::f.py::?name`)."""
    code, text = _run([sys.executable, str(path)], path.parent, 600)
    if code == 0:
        return
    label = f"fx::{path.name}"
    fails = 0
    section = ""
    for line in text.splitlines():
        header = re.match(r"^\[(\w+)\]$", line.strip())
        if header:
            section = header.group(1)
        m = re.match(r"^\s*FAIL(?: \[(?P<br>[^\]]+)\]|:)\s*(?P<rest>.*)$", line)
        if not m:
            continue
        raw = m.group("br") or m.group("rest").split(": ", 1)[0]
        reason = m.group("rest")[:160]
        fn = locate_test(path, raw) if raw else None
        if fn:
            out.failed[f"{label}::{fn}"] = reason
        elif section:
            out.failed[f"{label}::{section}"] = reason
        else:
            out.failed[f"{label}::?{raw[:60]}"] = reason
        fails += 1
    if fails == 0 or (not SUMMARY_LINE.search(text) and "Traceback" in text):
        tail = text.strip().splitlines()[-1][:200] if text.strip() else ""
        out.crashes.append(f"{path.name} exited {code} without a summary line or a parsable FAIL line: {tail}")


def fixture_files(root: Path) -> List[Path]:
    files = []
    for p in sorted((root / "tests" / "fixtures").glob("test_*.py")):
        if p.name in SKIP_FIXTURES:
            continue
        if any(m in p.read_text(encoding="utf-8") for m in FIXTURE_MARKERS):
            files.append(p)
    return files


def copy_plugin(dst: Path) -> Path:
    """Copy tools/craftflow-plugin (minus ref-*) to dst/tools/craftflow-plugin; returns the plugin root of the copy."""
    src = PLUGIN_ROOT.parents[1]
    target = dst / "tools" / "craftflow-plugin"
    shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__", "node_modules", "ref-*", ".git"))
    return target / "plugins" / "craftflow"


def run_drill(name: str, base: Path, jobs: int) -> Run:
    phase, _, fn = DRILLS[name]
    out = Run()
    work = Path(tempfile.mkdtemp(prefix=f"drill-{name}-", dir=str(base)))
    root = copy_plugin(work)
    try:
        fn(Tree(root))
    except DrillError as err:
        out.crashes.append(f"drill {name} could not be applied: {err}")
        return out
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(run_hook_suite, root, out)] + [pool.submit(run_fixture, p, out) for p in fixture_files(root)]
        for f in futures:
            f.result()
    return out


# ------------------------------------------------------------------------------------------------ classification


def classify(key: str, phase: int, rows: Dict[str, Tuple[str, str]]) -> Tuple[str, str, bool]:
    """(kind, owner, acceptable) for one failing test key."""
    if key.startswith("hook::?"):
        return "UNCLASSIFIED", "-", False
    if "::?" in key:  # a check the harness could not tie to one test: judged by every row of its file (worst case)
        prefix = key.split("::?", 1)[0] + "::"
        file_rows = [v for k, v in rows.items() if k.startswith(prefix)]
        if not file_rows:
            return "UNCLASSIFIED", "-", False
        if any(kind == "C" for kind, _ in file_rows):
            return "C", "file has a C row", False
        bad = [owner for _, owner in file_rows if phase not in owner_phases(owner)]
        return "file", "; ".join(sorted({o[:30] for _, o in file_rows}))[:60], not bad
    row = rows.get(key)
    if row is None:
        return "UNCLASSIFIED", "-", False
    kind, owner = row
    if kind == "C":
        return kind, owner, False
    return kind, owner, phase in owner_phases(owner)


def evaluate(name: str, run: Run, control: Run, rows: Dict[str, Tuple[str, str]]) -> Tuple[List[str], List[str]]:
    """(report lines, violation lines) for one drill, after subtracting the control's environmental failures."""
    phase = DRILLS[name][0]
    report: List[str] = []
    env_crashes = {c.split(" ")[0] for c in control.crashes}  # crash keys: the fixture file name, or `hook`
    violations: List[str] = [f"CRASH: {c}" for c in run.crashes if name == "control" or c.split(" ")[0] not in env_crashes]
    per_file: Dict[str, int] = {}
    for key in sorted(k for k in run.failed if k not in control.failed):
        kind, owner, ok = classify(key, phase, rows)
        if "::?" in key and key.startswith("fx::"):  # one line per file for checks that could not be tied to a single test
            file_key = key.split("::?", 1)[0] + "::?"
            per_file[file_key] = per_file.get(file_key, 0) + 1
            if file_key not in {v.split(" (")[0] for v in violations} and not ok:
                violations.append(f"{file_key} ({kind}/{owner[:60]}) fails under {name}")
            continue
        report.append(f"  {'ok ' if ok else 'BAD'} {kind}/{owner[:60]}  {key}")
        if not ok:
            violations.append(f"{key} ({kind}/{owner[:60]}) fails under {name}")
    for file_key, count in sorted(per_file.items()):
        kind, owner, ok = classify(file_key, phase, rows)
        report.append(f"  {'ok ' if ok else 'BAD'} {kind}/{owner[:60]}  {file_key[:-3]} ({count} checks not tied to one test)")
    return report, violations


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--drills", default="", help="comma-separated drill names (default: all)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--jobs", type=int, default=4, help="drills run in parallel; each also runs its fixtures in parallel")
    ap.add_argument("--keep", action="store_true", help="keep the scratch copies")
    ap.add_argument("--verbose", action="store_true", help="print the failure reason of every failing test")
    args = ap.parse_args(argv)
    if args.list:
        for n, (phase, desc, _) in DRILLS.items():
            print(f"{n}\tP{phase}\t{desc}")
        return 0
    wanted = [n for n in args.drills.split(",") if n] or [n for n in DRILLS if n != "control"]
    unknown = [n for n in wanted if n not in DRILLS]
    if unknown:
        print(f"unknown drills: {unknown}", file=sys.stderr)
        return 2
    rows = anchors_rows(ANCHORS.read_text(encoding="utf-8"))
    base = Path(tempfile.mkdtemp(prefix="router-drills-"))
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            names = ["control"] + [n for n in wanted if n != "control"]
            futures = {n: pool.submit(run_drill, n, base, 3) for n in names}
            runs = {n: f.result() for n, f in futures.items()}
        control = runs["control"]
        all_violations: List[str] = []
        print(f"control (environmental failures, subtracted from every drill): {len(control.failed)}"
              + "".join(f"\n  env {k}" for k in sorted(control.failed)) + "".join(f"\n  env CRASH {c}" for c in control.crashes))
        for n in names[1:]:
            report, violations = evaluate(n, runs[n], control, rows)
            print(f"drill {n} (P{DRILLS[n][0]}): {DRILLS[n][1]} -> {len(report)} failing test(s)")
            for line in report:
                print(line)
            for v in violations:
                if v.startswith("CRASH"):
                    print(f"  BAD {v}")
            if args.verbose:
                for k in sorted(k for k in runs[n].failed if k not in control.failed):
                    print(f"    reason {k}: {runs[n].failed[k]}")
            all_violations.extend(f"{n}: {v}" for v in violations)
        print(f"VIOLATIONS: {len(all_violations)}")
        for v in all_violations:
            print(f"  {v}")
        return 1 if all_violations else 0
    finally:
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
