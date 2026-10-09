#!/usr/bin/env python3
"""Regression tests for the P0 review findings (REM-FIX 1) against craftflow_rule_inventory.py.

Run: python3 tests/fixtures/test_rule_inventory_hardening.py
Each HIGH finding has a test named after its id (R-H1, R-H2, H-H1 ... H-H5); MEDIUM/LOW follow.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
sys.path.insert(0, str(HERE))

import craftflow_rule_inventory as inv  # noqa: E402
import test_rule_inventory as base  # noqa: E402
from test_rule_inventory import DBG, FP, GATE, K, RM, RR, STD, id_of, passes, policy_doc, run_diff  # noqa: E402

SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_rule_inventory.py"
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


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def raises(fn, exc=ValueError) -> str:
    try:
        fn()
    except exc as e:
        return str(e) or "raised"
    return ""


def mini_root(tmp: str, skill: str = "## A\n\n- Run it.\n\nNever stop.\n") -> Path:
    root = Path(tmp) / "plugin"
    (root / "skills" / "craftflow-router").mkdir(parents=True)
    (root / "skills" / "craftflow-router" / "SKILL.md").write_text(skill)
    (root / "rm.json").write_text(json.dumps({"version": 1, "files": [RM["files"][0]]}))
    (root / "al.json").write_text(json.dumps({"version": 1, "entries": []}))
    return root


# ------------------------------------------------------------------------------------------------ HIGH


def test_r_h1_cross_heading_dedupe_passes() -> None:
    before = {K: "## Gate A\n\n" + GATE + "\n", FP: "## Gate B\n\n" + GATE + "\n"}
    row = f"| {id_of(GATE)} | {id_of(GATE)} | dedupe | same gate | kept copy in the kernel covers all | David Gracia (2026-10-08) |"
    res = run_diff(before, {K: before[K]}, policy_doc(rewrites=[row]))
    no_row = run_diff(before, {K: before[K]})
    check("R-H1 cross-heading dedupe passes", passes(res) and res["reworded"] == 1 and res["unmapped_removed"] == 0,
          repr(res))
    check("R-H1 cross-heading removal without a dedupe row still fails", not passes(no_row), repr(no_row))


def test_r_h2_rationale_only_for_start_files() -> None:
    text = "This paragraph explains why the design looks like this."
    row = f"| {id_of(text)} | router-rationale.md | explanatory | David Gracia (2026-10-08) |"
    # the L3 basis is the eight files read at workflow start: the kernel files plus the start-class files
    for label, f, want in (("start file", FP, True), ("kernel file", K, True), ("stage file", RR, False)):
        res = run_diff({f: "## A\n\n" + text + "\n"}, {f: "## A\n\nOther text here.\n"},
                       policy_doc(rationale=[row]), rationale=text + "\n")
        check(f"R-H2 rationale row for a {label} -> {'ok' if want else 'rejected'}",
              passes(res) == want and (want or bool(res["row_errors"]) or res["unmapped_removed"] > 0), repr(res))


def test_r_h2_rule_verbs_are_normative() -> None:
    lines = ["Increment `planning_review_runs += 1` before dispatch.", "Update the artifact after each phase.",
             "Otherwise ask the user.", "Prefer the cached copy.", "Count >= 2 means ask the user.",
             "Create the file first."]
    bad = [t for t in lines if not inv.classify_normative(inv.extract_units(t + "\n", RR)[0], frozenset())]
    check("R-H2 increment/update/otherwise/prefer/count/create are rule verbs", not bad, repr(bad))


def test_h_h1_empty_corpus_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty"
        empty.mkdir()
        (empty / "rm.json").write_text(json.dumps({"version": 1, "files": [RM["files"][0]]}))
        (empty / "al.json").write_text(json.dumps({"version": 1, "entries": []}))
        out = Path(tmp) / "e.json"
        r = cli("--emit", "--plugin-root", empty, "--readmap-baseline", empty / "rm.json", "--allowlist",
                empty / "al.json", "--out", out)
        check("H-H1 empty plugin root --emit fails", r.returncode != 0 and not out.exists() and "error" in r.stderr,
              f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")
    zero = {"version": 2, "ref": None, "entries": []}
    check("H-H1 zero-entry baseline is rejected", bool(raises(lambda: inv.diff_inventory(zero, {K: "## A\n\nx\n"}, RM, policy_doc()))))
    good = base.baseline_of({K: "## A\n\n" + GATE + "\n"})
    check("H-H1 empty after corpus is rejected", bool(raises(lambda: inv.diff_inventory(good, {}, RM, policy_doc()))))


def test_h_h2_readmap_validation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "rm.json"
        p.write_text("{}")
        check("H-H2 '{}' read map fails to load", bool(raises(lambda: inv.load_readmap(p))))
        p.write_text(json.dumps({"version": 1, "files": []}))
        check("H-H2 read map with no files fails", bool(raises(lambda: inv.load_readmap(p))))
    typo = {"version": 1, "files": [{"file": K + "x", "class": "kernel", "workflows": ["ALL"]}]}
    check("H-H2 corpus file missing from the read map fails",
          bool(raises(lambda: inv.build_inventory({K: "## A\n\nx\n"}, typo, set()))))
    badvar = {"version": 1, "files": [{"file": K, "class": "start", "workflows": ["BUILD"], "variant": "fastpath"}]}
    check("H-H2 unknown variant fails", bool(raises(lambda: inv.coverage(badvar))))
    check("H-H2 read-map entry whose file is not in the corpus fails",
          bool(raises(lambda: inv.check_readmap_files(RM, [K, FP]))) and not raises(lambda: inv.check_readmap_files(RM, [K, FP, STD, DBG, RR])))
    with tempfile.TemporaryDirectory() as tmp:
        root = mini_root(tmp)
        (root / "rm.json").write_text("{}")
        r = cli("--emit", "--plugin-root", root, "--readmap-baseline", root / "rm.json", "--allowlist", root / "al.json",
                "--out", Path(tmp) / "o.json")
        check("H-H2 CLI --emit with '{}' read map exits non-zero", r.returncode != 0, r.stdout + r.stderr)


def test_h_h3_narrowing_scoped_to_occurrence() -> None:
    before = {FP: "## A\n\n" + GATE + "\n\n## B\n\n" + GATE + "\n"}
    after = {STD: before[FP]}
    q = '"Run the Learn-Distill Gate"'
    row_a = f"| {FP.split('/')[-1]}#A | BUILD | BUILD:standard | {q} | David Gracia (2026-10-08) |"
    res = run_diff(before, after, policy_doc(applic=[row_a]))
    check("H-H3 narrowing one section does not excuse a loss elsewhere",
          res["unmapped_removed"] == 1 and not passes(res), repr(res))
    row_both = f"| {FP.split('/')[-1]}#B | BUILD | BUILD:standard | {q} | David Gracia (2026-10-08) |"
    both = run_diff(before, after, policy_doc(applic=[row_a, row_both]))
    check("H-H3 narrowing both sections passes", passes(both), repr(both))
    dup = run_diff(before, after, policy_doc(applic=[row_a, row_a]))
    check("H-H3 duplicate narrowing rows for one occurrence are an error",
          any("duplicate" in e for e in dup["row_errors"]), repr(dup))


def test_h_h4_missing_allowlist_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = mini_root(tmp)
        rel = "skills/craftflow-router/SKILL.md"
        typo = cli("--report-nonnormative", "--plugin-root", root, "--files", rel, "--allowlist", "/typo.json")
        default = cli("--report-nonnormative", "--plugin-root", root, "--files", rel)
        dup = cli("--report-nonnormative", "--plugin-root", root, "--files", rel, rel)
        single = cli("--report-nonnormative", "--plugin-root", root, "--files", rel)
        check("H-H4 explicit missing --allowlist fails", typo.returncode != 0 and "error" in typo.stderr, typo.stdout + typo.stderr)
        check("H-H4 defaulted allowlist path still works", default.returncode == 0, default.stderr)
        check("H-L4 duplicate --files are counted once", dup.returncode == 0 and dup.stdout == single.stdout,
              dup.stdout + single.stdout)


# ---------------------------------------------------------------------------------------------- MEDIUM


def test_r_m2_quote_must_appear_in_the_rule() -> None:
    text = "- After `integration-verifier` returns PASS, run the Learn-Distill Gate."
    before, after = {FP: "## G\n\n" + text + "\n"}, {STD: "## G\n\n" + text + "\n"}
    row = lambda q: f"| {id_of(text)} | BUILD | BUILD:standard | {q} | David Gracia (2026-10-08) |"
    bogus = run_diff(before, after, policy_doc(applic=[row('"completely unrelated words"')]))
    check("R-M2 a quote that is not in the rule is rejected", not passes(bogus) and bool(bogus["row_errors"]), repr(bogus))


def test_r_m3_policy_symbol_and_live_verified() -> None:
    text = "- Never enter plan mode."
    before, after = {K: "## A\n\n" + text + "\n"}, {K: "## A\n\nOther text here.\n"}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "scripts").mkdir()
        (root / "tests" / "fixtures").mkdir(parents=True)
        (root / "scripts" / "guard.py").write_text("# decide is mentioned only here\ndef other():\n    return 1\n")
        (root / "scripts" / "real.py").write_text("def decide():\n    return 1\n")
        (root / "tests" / "fixtures" / "test_guard.py").write_text("def test_decide():\n    pass\n")
        row = lambda script, live="true": (f"| {id_of(text)} | k | scripts/{script} | tests/fixtures/test_guard.py::test_decide | deleted | {live} | David Gracia (2026-10-08) |")
        comment = run_diff(before, after, policy_doc(policy=[row("guard.py::decide")]), root=root)
        real = run_diff(before, after, policy_doc(policy=[row("real.py::decide")]), root=root)
        notlive = run_diff(before, after, policy_doc(policy=[row("real.py::decide", "false")]), root=root)
    check("R-M3 a name found only in a comment is not a function", not passes(comment), repr(comment))
    check("R-M3 a real def passes", passes(real), repr(real))
    check("R-M3 deleted requires live_verified true", not passes(notlive) and bool(notlive["row_errors"]), repr(notlive))


def test_h_m1_errors_go_to_stderr() -> None:
    r = cli("--report-nonnormative", "--files", "skills/craftflow-router/SKILL.md", "--allowlist", "/typo.json")
    check("H-M1 error JSON is on stderr, stdout stays clean", r.stdout == "" and json.loads(r.stderr.strip())["error"],
          f"out={r.stdout!r} err={r.stderr!r}")


def test_h_m4_classifier_gaps() -> None:
    cls = lambda t: inv.classify_normative(inv.extract_units(t, RR)[0], frozenset())
    check("H-M4 wrapped 'do\\n  not' is normative", cls("- Then do\n  not touch this file.\n"))
    nbsp = chr(160)
    check("H-M4 NBSP in 'do not' is normative", cls("- Then do" + nbsp + "not touch this file.\n"))
    check("H-M4 backtick-first imperative is normative", cls("`Read` the file.\n"))
    etm = [u["norm"] for u in inv.extract_units("- Do it [EASY TO MISS: keep\n\n  this bit] and go.\n", RR) if u["kind"] == "easy_to_miss"]
    check("H-M4 [EASY TO MISS spanning a blank line stays one unit", etm == ["[easy to miss: keep this bit]"], repr(etm))
    check("H-M4 unclosed fence fails loudly", "unclosed" in raises(lambda: inv.extract_units("Intro.\n\n```bash\necho 1\n", RR)))
    check("H-M4 setext heading is rejected", "setext" in raises(lambda: inv.extract_units("Title\n=====\n\nbody\n", RR)))


def test_h_m5_out_does_not_clobber_other_ref() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = mini_root(tmp)
        git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run(git + ["init", "-q"], check=True, capture_output=True)
        subprocess.run(git + ["add", "skills"], check=True, capture_output=True)
        subprocess.run(git + ["commit", "-q", "-m", "x"], check=True, capture_output=True)
        out = Path(tmp) / "baseline.json"
        out.write_text('{"entries":[],"ref":"deadbeef","version":2}\n')
        args = ["--emit", "--ref", "HEAD", "--plugin-root", root, "--readmap-baseline", root / "rm.json", "--allowlist",
                root / "al.json", "--out", out]
        refused = cli(*args)
        kept = out.read_text() == '{"entries":[],"ref":"deadbeef","version":2}\n'
        forced = cli(*args, "--force")
        check("H-M5 --out refuses to overwrite a baseline of another ref", refused.returncode != 0 and kept, refused.stdout + refused.stderr)
        check("H-M5 --force overwrites", forced.returncode == 0 and '"deadbeef"' not in out.read_text(), forced.stderr)


def test_h_m6_policy_table_parsing() -> None:
    row = "| " + " | ".join(["x"] * 6) + " |"
    nosep = "# P\n\n## Policy-mapped deletions\n\n" + inv.HEADERS["policy"] + "\n" + row + "\n"
    _, errs = inv.parse_tables(nosep + "\n## Rewrites\n\n")
    check("H-M6 missing separator row is an error", any("separator" in e for e in errs), repr(errs))
    fenced = policy_doc().replace("## Rewrites", "```\n" + row + "\n```\n\n## Rewrites", 1)
    rows, errs = inv.parse_tables(fenced)
    check("H-M6 table rows inside fenced examples are ignored", rows["policy"] == [] and not errs, repr((rows["policy"], errs)))


def test_h_m7_mode_incompatible_flags() -> None:
    bad = [["--out", "x"], ["--allowlist", "x"], ["--files", "x"], ["--list"]]
    codes = [cli("--diff", "--baseline", "b", "--policy-map", "p", *extra).returncode for extra in bad]
    check("H-M7 --diff rejects --out/--allowlist/--files/--list (argparse exit 2)", codes == [2, 2, 2, 2], repr(codes))
    r = cli("--emit", "--files", "x", "--out", "o", "--readmap-baseline", "r")
    check("H-M7 --emit rejects --files", r.returncode == 2, r.stderr)


# ------------------------------------------------------------------------------------------------ LOW


def test_r_l1_skip_is_not_a_pass() -> None:
    before = (base._passes, base._skips)
    base.skip("probe", "not counted as a pass")
    after = (base._passes, base._skips)
    base._skips -= 1  # keep the imported module's own counter honest
    check("R-L1 a skipped check increments the skip counter, not the pass counter",
          after == (before[0], before[1] + 1), repr((before, after)))


def test_low_allowlist_validation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "al.json"
        p.write_text(json.dumps({"version": 1, "entries": [{"text": "Not Normalized Text"}]}))
        check("allowlist entries must be idempotent under normalize", "normal" in raises(lambda: inv.load_allowlist(p)))
        p.write_text('{"version": 1, "entries": [{"text": NaN}]}')
        check("H-L5 NaN in a fixture is rejected", bool(raises(lambda: inv.load_allowlist(p))))
        root = mini_root(tmp)
        (root / "al.json").write_text(json.dumps({"version": 1, "entries": [{"text": "matches no unit at all"}]}))
        out = Path(tmp) / "o.json"
        r = cli("--emit", "--plugin-root", root, "--readmap-baseline", root / "rm.json", "--allowlist", root / "al.json", "--out", out)
        check("allowlist text matching no unit fails --emit", r.returncode != 0 and not out.exists() and "allowlist_unmatched" in r.stderr,
              r.stdout + r.stderr)
    inventory = inv.build_inventory({K: "## A\n\nx y z.\n"}, RM, {"matches no unit"})
    check("build_inventory reports allowlist_unmatched", inventory["allowlist_unmatched"] == ["matches no unit"], repr(inventory.get("allowlist_unmatched")))


# --------------------------------------------------------------------------------- REM-FIX 2 (re-hunt)

PL = "skills/craftflow-router/references/plan-x.md"
RM2 = {"version": 1, "files": [{"file": K, "class": "kernel", "workflows": ["ALL"]},
                               {"file": STD, "class": "start", "workflows": ["BUILD"], "variant": "standard"},
                               {"file": PL, "class": "start", "workflows": ["PLAN"]}]}
BETA = "- Never do the beta thing."


def diff2(row: str, after_file: str = PL, extra: str = "") -> dict:
    base2 = inv.build_inventory({K: "## K\n\nStay.\n", STD: "## r\n\n" + BETA + "\n"}, RM2, set(), ref="f")
    return inv.diff_inventory(base2, {K: "## K\n\nStay.\n", after_file: "## r\n\n" + BETA + "\n"}, RM2,
                              policy_doc(applic=[row] if row else []), plugin_root=PLUGIN_ROOT)


def test_m1_narrowing_validated_against_baseline_coverage() -> None:
    q, name = '"beta thing"', STD.split("/")[-1]
    disjoint = diff2(f"| {name}#r | BUILD:standard | PLAN:standard | {q} | David Gracia (2026-10-08) |")
    check("M1 narrowed coverage outside the baseline coverage is a row error and the loss is not forgiven",
          any("narrowed" in e for e in disjoint["row_errors"]) and disjoint["unmapped_removed"] == 1 and not passes(disjoint),
          repr(disjoint))
    garbage = diff2(f"| {name}#r | ZZZ | BUILD:standard | {q} | David Gracia (2026-10-08) |", STD)
    check("M1 garbage baseline coverage is a row error", bool(garbage["row_errors"]) and not passes(garbage), repr(garbage))
    wrong = diff2(f"| {name}#r | BUILD | BUILD:standard | {q} | David Gracia (2026-10-08) |", STD)
    check("M1 a baseline coverage cell that differs from the occurrence's real coverage is a row error",
          any("baseline coverage" in e for e in wrong["row_errors"]), repr(wrong))
    good = diff2(f"| {name}#r | BUILD:standard | BUILD:standard | {q} | David Gracia (2026-10-08) |", STD)
    check("M1 a truthful narrowing that matches the baseline still passes", passes(good), repr(good))


def test_m3_readmap_rejects_widening_unknown_keys_and_duplicates() -> None:
    ent = {"file": STD, "class": "start", "workflows": ["BUILD"]}
    with_v = lambda **kw: {"version": 1, "files": [dict(ent, **kw)]}
    check("M3 empty variant list is rejected, not widened to all variants",
          bool(raises(lambda: inv.coverage(with_v(variant=[])))))
    check("M3 misspelled key is rejected", "varient" in raises(lambda: inv.coverage(with_v(varient="standard"))))
    check("M3 extra class_ key is rejected", bool(raises(lambda: inv.coverage(with_v(class_="x")))))
    check("M3 duplicate entries for one file and class are rejected",
          bool(raises(lambda: inv.coverage({"version": 1, "files": [ent, dict(ent, variant="standard")]}))))
    check("M3 a variant list of strings still works",
          inv.coverage(with_v(variant=["standard"]))[STD] == {("BUILD", "standard", "start")})
    check("M3 the committed read map still validates", not raises(lambda: inv.load_readmap(PLUGIN_ROOT / "config" / "router-readmap.json")))


def test_l1_malformed_readmap_shapes_emit_the_json_error_contract() -> None:
    check("L1 a string files entry raises ValueError", bool(raises(lambda: inv.coverage({"version": 1, "files": ["x"]}))))
    check("L1 variants as a list raises ValueError",
          bool(raises(lambda: inv.coverage({"version": 1, "variants": [1], "files": [RM["files"][0]]}))))
    with tempfile.TemporaryDirectory() as tmp:
        root = mini_root(tmp)
        for label, body in (("string entry", {"version": 1, "files": ["x"]}),
                            ("variants list", {"version": 1, "variants": [1], "files": [RM["files"][0]]})):
            (root / "rm.json").write_text(json.dumps(body))
            r = cli("--emit", "--plugin-root", root, "--readmap-baseline", root / "rm.json", "--allowlist", root / "al.json",
                    "--out", Path(tmp) / "o.json")
            check(f"L1 CLI {label} exits 1 with a JSON error, no traceback",
                  r.returncode == 1 and "Traceback" not in r.stderr and "error" in json.loads(r.stderr.strip()), r.stderr)


def test_l2_report_mode_does_not_clobber_a_baseline() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = mini_root(tmp)
        out = Path(tmp) / "baseline.json"
        keep = '{"entries":[],"ref":"deadbeef","version":2}\n'
        out.write_text(keep)
        r = cli("--report-nonnormative", "--plugin-root", root, "--files", "skills/craftflow-router/SKILL.md", "--out", out)
        check("L2 --report-nonnormative --out refuses to overwrite a baseline-shaped file",
              r.returncode != 0 and out.read_text() == keep, r.stdout + r.stderr)
        plain = Path(tmp) / "report.json"
        ok1 = cli("--report-nonnormative", "--plugin-root", root, "--files", "skills/craftflow-router/SKILL.md", "--out", plain)
        ok2 = cli("--report-nonnormative", "--plugin-root", root, "--files", "skills/craftflow-router/SKILL.md", "--out", plain)
        check("L2 a report file can still be re-written", ok1.returncode == 0 and ok2.returncode == 0, ok2.stderr)


def test_l4_duplicate_rewrite_rows_for_one_id_are_an_error() -> None:
    before = {K: "## A\n\n" + GATE + "\n", FP: "## B\n\n" + GATE + "\n"}
    r1 = f"| {id_of(GATE)} | {id_of(GATE)} | dedupe | same | kept copy covers all | David Gracia (2026-10-08) |"
    r2 = f"| {id_of(GATE)} | {id_of(GATE)} | pointer | same | pointer | David Gracia (2026-10-08) |"
    res = run_diff(before, {K: before[K]}, policy_doc(rewrites=[r1, r2]))
    check("L4 two rewrite rows for one id are a row error", any("duplicate rewrite" in e for e in res["row_errors"]), repr(res))
    check("L4 a single rewrite row is fine", passes(run_diff(before, {K: before[K]}, policy_doc(rewrites=[r1]))))


def test_l5_duplicate_baseline_ids_are_rejected() -> None:
    b = base.baseline_of({K: "## A\n\n" + GATE + "\n"})
    b["entries"].append(json.loads(json.dumps(b["entries"][0])))
    check("L5 a baseline with a repeated unit id is rejected",
          "duplicate" in raises(lambda: inv.diff_inventory(b, {K: "## A\n\nx\n"}, RM, policy_doc())))


def test_policy_map_unclosed_fence_is_surfaced_first() -> None:
    _, errs = inv.parse_tables(policy_doc().replace("## Rewrites", "```\n\n## Rewrites", 1))
    check("policy map with an unclosed fence reports the fence, not missing sections",
          len(errs) == 1 and "fence" in errs[0], repr(errs))


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn) and fn.__module__ == __name__:
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
