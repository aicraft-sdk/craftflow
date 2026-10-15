#!/usr/bin/env python3
"""Regression tests for the doubt-verifier attacks on the zero-rule-loss net (P0 REM-FIX 3, classes D1-D6).

Run: python3 tests/fixtures/test_rule_inventory_doubt.py
Each test reproduces one attack by its doubt-verifier id (A2, A7b, attack-8, A3, A4, A4b, A10, A6, A9, A8r, A1) and
asserts the net now fails closed. Fixture-level tests drive diff_inventory(); the e2e tests edit a scratch copy of the
real router corpus and run the real CLI against the committed baseline.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_rule_inventory as inv  # noqa: E402

SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_rule_inventory.py"
BASELINE = HERE / "router-rule-inventory.baseline.json"
POLICY_MAP = PLUGIN_ROOT.parents[1] / "docs" / "router-policy-map.md"
SKILL = "skills/craftflow-router/SKILL.md"
REFS = "skills/craftflow-router/references/"
K, FP, RR = SKILL, REFS + "fast-path.md", REFS + "remediation-and-research.md"
BW, PW = REFS + "build-workflow.md", REFS + "plan-workflow.md"
WHO = "David Gracia (2026-10-08)"
RMX = {"version": 1, "files": [
    {"file": K, "class": "kernel", "workflows": ["ALL"], "trigger": "skill load", "happy_path": True},
    {"file": BW, "class": "start", "workflows": ["BUILD"], "trigger": "BUILD preparation", "happy_path": True},
    {"file": PW, "class": "start", "workflows": ["PLAN"], "trigger": "PLAN preparation", "happy_path": True},
    {"file": FP, "class": "start", "workflows": ["BUILD"], "trigger": "fast-path routing", "happy_path": True},
    {"file": FP, "class": "stage", "workflows": ["PLAN", "DEBUG", "REVIEW"], "trigger": "effort lookup", "happy_path": True},
    {"file": RR, "class": "stage", "workflows": ["ALL"], "trigger": "remediation", "happy_path": False},
]}

_passes = 0
_errors: list = []


def check(name: str, cond: bool, reason: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason}")
        print(f"  FAIL: {name}: {reason or 'assertion false'}")


def doc(**rows) -> str:
    """A policy map with every section the script requires, with the given rows per section key."""
    out = "# Router policy map\n\n"
    for key, title in inv.SECTIONS.items():
        hdr = inv.HEADERS[key]
        out += f"## {title.capitalize()}\n\n{hdr}\n|" + "---|" * (hdr.count("|") - 1) + "\n"
        out += "".join(r + "\n" for r in rows.get(key, [])) + "\n"
    return out


def diff(before: dict, after: dict, readmap=RMX, **rows) -> dict:
    base = inv.build_inventory(before, RMX, set(), ref="fixture")
    return inv.diff_inventory(base, after, readmap, doc(**rows), plugin_root=PLUGIN_ROOT, baseline_readmap=RMX)


def failed(res: dict) -> bool:
    return inv.exit_code(res) == 1


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def scratch(tmp: str) -> Path:
    root = Path(tmp) / "plugin"
    # scripts and tests/fixtures too: a signed policy row must find the script and test it names (symbol_ok)
    for d in ("skills/craftflow-router", "skills/_shared", "config", "scripts", "tests/fixtures"):
        shutil.copytree(PLUGIN_ROOT / d, root / d, ignore=shutil.ignore_patterns("__pycache__"))
    return root


def edit(root: Path, rel: str, old: str, new: str) -> None:
    p = root / rel
    text = p.read_text(encoding="utf-8")
    assert old in text, f"scratch edit target not found in {rel}: {old[:50]!r}"
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def real_diff(root: Path, *extra):
    r = cli("--diff", "--plugin-root", root, "--baseline", BASELINE, "--policy-map", POLICY_MAP, "--readmap",
            root / "config" / "router-readmap.json", *extra)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        res = {}
    return r, res


def heading_chain(text: str, line_no: int) -> str:
    stack: list = []
    for i, level, raw in sorted(inv.cor.headings(text)):
        if i + 1 > line_no:
            break
        stack = [h for h in stack if h[0] < level] + [(level, raw)]
    return "".join("\n" + "#" * lv + " " + raw + "\n" for lv, raw in stack)


# ------------------------------------------------------------------------------------------------ D1


def test_d1_a2_minimal_repro_kernel_rule_moved_to_conditional_stage_e2e() -> None:
    rule = "- Never spawn Memory Update as a sub-agent.\n"
    with tempfile.TemporaryDirectory() as tmp:
        root = scratch(tmp)
        control, _ = real_diff(root)
        text = (root / K).read_text(encoding="utf-8")
        chain = heading_chain(text, text[:text.index(rule)].count("\n") + 1)  # same heading path, so only reach differs
        edit(root, K, rule, "")
        with open(root / RR, "a", encoding="utf-8") as fh:
            fh.write("\n" + chain + "\n" + rule)
        r, res = real_diff(root)
    check("A2 control: the unmodified scratch copy passes", control.returncode == 0, control.stdout[-200:] + control.stderr[-200:])
    check("A2 kernel hard rule moved into happy_path:false remediation-and-research.md fails closed",
          r.returncode == 1 and res.get("unreachable", 0) >= 1, f"rc={r.returncode} {res}")


def test_d1_start_rule_moved_into_effort_lookup_stage_reach() -> None:
    rule = "- Never ship the plan unreviewed.\n"
    res = diff({PW: "## Gate\n\n" + rule}, {FP: "## Gate\n\n" + rule})
    check("D1 a PLAN start-file rule moved into fast-path.md (PLAN reads it only for the effort lookup) is unreachable",
          failed(res) and res["unreachable"] >= 1, repr(res))
    ok = diff({FP: "## Gate\n\n" + rule}, {K: "## Gate\n\n" + rule})
    check("D1 moving a rule into the kernel keeps reach", not failed(ok), repr(ok))


# ------------------------------------------------------------------------------------------------ D2


def test_d2_a7b_live_read_map_widening_e2e() -> None:
    old = "- Empty → skip `learn-distill` task creation entirely. `chain_tail_task_id` stays unchanged.\n"
    with tempfile.TemporaryDirectory() as tmp:
        root = scratch(tmp)
        text = (root / BW).read_text(encoding="utf-8")
        chain = heading_chain(text, text[:text.index(old)].count("\n") + 1)
        edit(root, BW, old, "")
        with open(root / PW, "a", encoding="utf-8") as fh:
            fh.write("\n" + chain + "\n" + old)
        rm_path = root / "config" / "router-readmap.json"
        rm = json.loads(rm_path.read_text(encoding="utf-8"))
        for e in rm["files"]:
            if e["file"] == PW:
                e["workflows"] = ["ALL"]
        rm_path.write_text(json.dumps(rm), encoding="utf-8")
        r, res = real_diff(root)
    check("A7b learn-distill bullet moved into plan-workflow.md with the live map widened to ALL fails closed",
          r.returncode == 1 and res.get("readmap_widened", 0) >= 1, f"rc={r.returncode} {res} {r.stderr[-200:]}")


# ------------------------------------------------------------------------------------------------ D3


def test_d3_attack8_self_referential_baseline_e2e() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root, fx = scratch(tmp), Path(tmp) / "fx"
        fx.mkdir()
        for name in ("router-readmap.baseline.json", "router-normative-allowlist.json", "router-rule-inventory.baseline.json"):
            shutil.copy(HERE / name, fx / name)
        edit(root, K, "- Never spawn Memory Update as a sub-agent.\n", "")
        edit(root, K, "- Never use an unscoped task lookup in critical paths.\n", "")
        forced = cli("--emit", "--force", "--plugin-root", root, "--readmap-baseline", fx / "router-readmap.baseline.json",
                     "--allowlist", fx / "router-normative-allowlist.json", "--out", fx / "router-rule-inventory.baseline.json")
        (fx / "router-rule-inventory.baseline.json").unlink()
        cli("--emit", "--plugin-root", root, "--readmap-baseline", fx / "router-readmap.baseline.json",
            "--allowlist", fx / "router-normative-allowlist.json", "--out", fx / "router-rule-inventory.baseline.json")
        r = cli("--diff", "--plugin-root", root, "--baseline", fx / "router-rule-inventory.baseline.json", "--policy-map",
                POLICY_MAP, "--readmap", root / "config" / "router-readmap.json")
    check("attack-8 --emit --force without an explicit --ref is refused", forced.returncode != 0 and "ref" in forced.stderr,
          f"rc={forced.returncode} {forced.stdout[-200:]} {forced.stderr[-200:]}")
    check("attack-8 a baseline re-emitted from the edited tree (ref null) is rejected by the lock, not diffed against itself",
          r.returncode == 1 and "lock" in r.stderr.lower(), f"rc={r.returncode} {r.stdout[-200:]} {r.stderr[-200:]}")


def test_d2_widening_needs_a_signed_row_and_the_real_baseline_coverage() -> None:
    rule = "## Gate\n\n- Never ship the plan unreviewed.\n"
    wide = json.loads(json.dumps(RMX))
    next(e for e in wide["files"] if e["file"] == PW)["workflows"] = ["ALL"]
    base = inv.build_inventory({PW: rule}, RMX, set(), ref="f")
    run = lambda *rows: inv.diff_inventory(base, {PW: rule}, wide, doc(widening=list(rows)), plugin_root=PLUGIN_ROOT, baseline_readmap=RMX)
    row = lambda said, who: f"| {PW} | {said} | BUILD, PLAN, DEBUG, REVIEW | the plan file is now read by every workflow | {who} |"
    check("D2 an unsigned-by-row widening of the live map is counted and fails", failed(run()) and run()["readmap_widened"] >= 1, repr(run()))
    good = run(row("PLAN", WHO))
    check("D2 a signed widening row that states the real baseline coverage allows it", not failed(good) and good["readmap_widened"] == 0, repr(good))
    wrong = run(row("BUILD", WHO))
    check("D2 a widening row stating the wrong baseline coverage does not allow it", failed(wrong) and wrong["readmap_widened"] >= 1, repr(wrong))
    unsigned = run(row("PLAN", "David"))
    check("D2 an unsigned widening row fails", failed(unsigned) and unsigned["rows_missing_review"] == 1, repr(unsigned))


def test_d3_lock_rejects_tampered_or_missing_pins() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fx = Path(tmp)
        for name in ("router-readmap.baseline.json", "router-rule-inventory.baseline.json", "router-baseline.lock.json"):
            shutil.copy(HERE / name, fx / name)
        args = (fx / "router-rule-inventory.baseline.json", fx / "router-readmap.baseline.json", fx / "router-baseline.lock.json")
        inv.verify_lock(*args)
        results = {}
        for label, mutate in (("baseline bytes", lambda: args[0].write_bytes(args[0].read_bytes() + b" ")),
                              ("readmap bytes", lambda: args[1].write_bytes(args[1].read_bytes() + b" ")),
                              ("missing lock", lambda: args[2].unlink())):
            mutate()
            try:
                inv.verify_lock(*args)
                results[label] = "accepted"
            except ValueError as exc:
                results[label] = str(exc)
            shutil.copy(HERE / "router-rule-inventory.baseline.json", args[0])
            shutil.copy(HERE / "router-readmap.baseline.json", args[1])
            shutil.copy(HERE / "router-baseline.lock.json", args[2])
        lock = json.loads(args[2].read_text(encoding="utf-8"))
        args[2].write_text(json.dumps(dict(lock, baseline_sha="0439c339")), encoding="utf-8")
        try:
            inv.verify_lock(*args)
            results["short sha"] = "accepted"
        except ValueError as exc:
            results["short sha"] = str(exc)
    check("D3 a changed baseline, a changed read map, a missing lock or a short sha is rejected, none accepted",
          all(v != "accepted" for v in results.values()) and "lock" in results["missing lock"], repr(results))


# ------------------------------------------------------------------------------------------------ D4


def test_d4_a3_table_header_swap() -> None:
    before = "## Agent Dispatch Table\n\n| Phase | Agent |\n|---|---|\n| build | component-builder |\n"
    after = "## Agent Dispatch Table\n\n| Agent | Phase |\n|---|---|\n| build | component-builder |\n"
    res = diff({FP: before}, {FP: after})
    check("A3 swapping the columns of a dispatch table header is a lost rule", failed(res), repr(res))


def test_d4_a4_bullet_moved_between_labelled_lists() -> None:
    before = "## Memory\n\nIf a file is missing:\n- Create it.\n- Read it.\n\nJUST_GO:\n- Set it.\n"
    after = "## Memory\n\nIf a file is missing:\n- Read it.\n\nJUST_GO:\n- Set it.\n- Create it.\n"
    res = diff({K: before}, {K: after})
    check("A4 a bullet moved from one labelled list to another under the same heading is a lost rule", failed(res), repr(res))


def test_d4_a4b_fence_join_and_renumbering() -> None:
    fence_before = "## Run\n\n```bash\necho a\necho b\n```\n"
    fence_after = "## Run\n\n```bash\necho a echo b\n```\n"
    joined = diff({K: fence_before}, {K: fence_after})
    steps = "## Steps\n\n1. Run alpha.\n2. Run beta.\n3. Run gamma.\n"
    swapped = diff({K: steps}, {K: "## Steps\n\n3. Run alpha.\n2. Run beta.\n1. Run gamma.\n"})
    check("A4b joining two fence lines into one is a lost rule", failed(joined), repr(joined))
    check("A4b renumbering steps 1 and 3 is a lost rule", failed(swapped), repr(swapped))


def test_d4_a10_heading_path_not_just_last_heading() -> None:
    before = "# Top A\n\n## Gate\n\n- Never skip it.\n\n# Top B\n\n## Gate\n\n- Stay.\n"
    after = "# Top A\n\n## Gate\n\n- Stay.\n\n# Top B\n\n## Gate\n\n- Never skip it.\n"
    res = diff({K: before}, {K: after})
    check("A10 a rule moved under a same-named heading of another parent is a lost rule", failed(res), repr(res))


# ------------------------------------------------------------------------------------------------ D5


def test_d5_a6_frontmatter_description_rule_removed() -> None:
    fm = "---\nname: x\ndescription: |\n  Route now.\n\n  CRITICAL: Route and execute immediately.\n---\n\n## A\n\nbody text\n"
    res = diff({K: fm}, {K: fm.replace("\n  CRITICAL: Route and execute immediately.\n", "")})
    check("A6 removing a rule from the SKILL.md frontmatter description is a lost rule", failed(res), repr(res))
    with tempfile.TemporaryDirectory() as tmp:
        root = scratch(tmp)
        edit(root, K, "  CRITICAL: Route and execute immediately. Do not stop at describing capabilities.\n", "")  # whole line: keep `---` at column 0
        r, res = real_diff(root)
    check("A6 e2e: the real SKILL.md frontmatter CRITICAL line is covered", r.returncode == 1 and res.get("unmapped_removed", 0) >= 1,
          f"rc={r.returncode} {res}")


# ------------------------------------------------------------------------------------------------ D6


def test_d6_a9_policy_and_rationale_rows_need_a_reviewer() -> None:
    text = "- Never enter plan mode."
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "scripts").mkdir()
        (root / "tests" / "fixtures").mkdir(parents=True)
        (root / "scripts" / "guard.py").write_text("def decide():\n    return 1\n")
        (root / "tests" / "fixtures" / "test_guard.py").write_text("def test_decide():\n    pass\n")
        row = lambda who: (f"| {inv.make_id(inv.normalize(text))} | k | scripts/guard.py::decide | "
                           f"tests/fixtures/test_guard.py::test_decide | deleted | true | {who} |")
        base = inv.build_inventory({K: "## A\n\n" + text + "\n"}, RMX, set(), ref="f")
        after = {K: "## A\n\nOther text here.\n"}
        unsigned = inv.diff_inventory(base, after, RMX, doc(policy=[row("")]), plugin_root=root,
                                    baseline_readmap=RMX)
        signed = inv.diff_inventory(base, after, RMX, doc(policy=[row(WHO)]), plugin_root=root,
                                  baseline_readmap=RMX)
    check("A9 an unsigned policy-mapped deletion row is counted and fails", unsigned["rows_missing_review"] == 1 and failed(unsigned),
          repr(unsigned))
    check("A9 a signed policy-mapped deletion row passes", not failed(signed) and signed["policy_mapped"] == 1, repr(signed))
    para = "This paragraph explains why the design looks like this."
    pid = inv.make_id(inv.normalize(para))
    rbase = inv.build_inventory({K: "## A\n\n" + para + "\n"}, RMX, set(), ref="f")
    rrow = lambda who: f"| {pid} | router-rationale.md | explanatory | {who} |"
    run = lambda who: inv.diff_inventory(rbase, {K: "## A\n\nOther text here.\n"}, RMX, doc(rationale=[rrow(who)]),
                                         rationale_text=para + "\n", plugin_root=PLUGIN_ROOT,
                                         baseline_readmap=RMX)
    check("A9 an unsigned rationale row is counted and fails", run("")["rows_missing_review"] == 1 and failed(run("")), repr(run("")))
    check("A9 a signed rationale row passes", not failed(run(WHO)), repr(run(WHO)))


def test_d6_a8r_rewrite_row_must_relate_old_and_new() -> None:
    old, new = "Run the old gate before dispatch.", "Something totally different appears here instead."
    oid, nid = inv.make_id(inv.normalize(old)), inv.make_id(inv.normalize(new))
    row = f"| {oid} | {nid} | pointer | reworded | same rule | {WHO} |"
    bad = diff({K: "## G\n\n- " + old + "\n"}, {K: "## G\n\n- " + new + "\n"}, rewrites=[row])
    check("A8r a rewrite row pointing at an unrelated new unit is a row error and fails",
          failed(bad) and any("overlap" in e for e in bad["row_errors"]), repr(bad))
    near = "Run the new gate before dispatch."
    good_row = f"| {oid} | {inv.make_id(inv.normalize(near))} | pointer | reworded | same rule | {WHO} |"
    good = diff({K: "## G\n\n- " + old + "\n"}, {K: "## G\n\n- " + near + "\n"}, rewrites=[good_row])
    check("A8r a genuine reword (>= 60% token overlap) still passes", not failed(good) and good["reworded"] == 1, repr(good))


def test_d6_a1_bare_name_is_not_a_reviewer() -> None:
    old, new = "Run the old gate before dispatch.", "Run the new gate before dispatch."
    oid, nid = inv.make_id(inv.normalize(old)), inv.make_id(inv.normalize(new))
    res = {who: diff({K: "## G\n\n- " + old + "\n"}, {K: "## G\n\n- " + new + "\n"},
                     rewrites=[f"| {oid} | {nid} | pointer | reworded | same rule | {who} |"])
           for who in ("David", "David Gracia", "david (2026-10-08)", "x@wf:1", WHO)}
    check("A1 a bare 'David' (or a name without a date, or lower case) is not a signature",
          all(res[w]["rows_missing_review"] == 1 and failed(res[w]) for w in list(res)[:4]), repr(res))
    check("A1 'Name Surname (YYYY-MM-DD)' is a signature", res[WHO]["rows_missing_review"] == 0 and not failed(res[WHO]), repr(res[WHO]))


# ------------------------------------------------------------------------------------------------ advisories


def test_a5_added_normative_near_a_kept_rule_is_reported() -> None:
    before = "## Delete\n\n- Never delete the file.\n"
    after = before + "- The user may override this, and then you must delete the file when asked.\n"
    res = diff({K: before}, {K: after})
    check("A5 an added normative unit under a heading that holds a baseline rule is reported (non-failing)",
          res.get("added_normative_near_rule") == 1 and not failed(res), repr(res))


def test_a11_case_of_code_spans_is_significant() -> None:
    check("A11 a case-only edit to a backticked literal changes the unit id",
          inv.normalize("Run `Foo` now") != inv.normalize("Run `foo` now") and inv.normalize("RUN `Foo` NOW") == "run `Foo` now",
          repr((inv.normalize("Run `Foo` now"), inv.normalize("Run `foo` now"))))


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # report per-test crashes without hiding the rest
                check(name, False, f"{type(exc).__name__}: {exc}")
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    for e in _errors:
        print(e)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
