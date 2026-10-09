#!/usr/bin/env python3
"""Regression tests for the P0 re-review 3 / re-hunt 3 findings (P0 REM-FIX 4): R-H1, H-H1, R-H2, R-H3, R-M1/H-M1,
H-M2, H-M3/R-M2 and the H-L1..H-L4 / R-L1 lows. Run: python3 tests/fixtures/test_rule_inventory_rows2.py

Each test reproduces the finding's attack and asserts the net now fails closed. Fixture-level tests drive
diff_inventory(); the e2e tests edit a scratch copy of the real router corpus and run the real CLI.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_rule_inventory_doubt as d  # noqa: E402  (shared fixtures; importing it runs nothing)

inv = d.inv
K, FP, RR, BW, PW, WHO, RMX = d.K, d.FP, d.RR, d.BW, d.PW, d.WHO, d.RMX
PLUGIN_ROOT = d.PLUGIN_ROOT
REPO = PLUGIN_ROOT.parents[3]

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


def uid(text: str) -> str:
    return inv.make_id(inv.normalize(text))


def unit_id(text: str, kind: str, nth: int = 0) -> str:
    return [u["id"] for u in inv.extract_units(text, "x.md") if u["kind"] == kind][nth]


def rw(old: str, new: str, kind: str, who: str = WHO, evidence: str = "") -> str:
    return f"| {old} | {new} | {kind} | moved | same rule | {who} |" + (f" {evidence} |" if evidence else "")


def run(before: dict, after: dict, **rows) -> dict:
    base = inv.build_inventory(before, RMX, set(), ref="fixture")
    return inv.diff_inventory(base, after, RMX, d.doc(**rows), plugin_root=PLUGIN_ROOT, baseline_readmap=RMX)


def failed(res: dict) -> bool:
    return inv.exit_code(res) == 1


def errs(res: dict, word: str) -> bool:
    return any(word in e for e in res["row_errors"])


# ------------------------------------------------------------------------------------------------ R-H1


RULE = "- Never spawn Memory Update as a sub-agent."


def test_rh1_every_non_heading_kind_checks_reach() -> None:
    before = {K: "## Gate\n\n" + RULE + "\n"}
    to_conditional = {K: "## Gate\n\nOther words here.\n", RR: "## Gate\n\n" + RULE + "\n"}
    to_kernel = {K: "## Gate\n\nOther words here.\n\n## Moved\n\n" + RULE + "\n"}
    rid = uid(RULE)
    out = {}
    for kind in ("dedupe", "xref", "pointer", "trigger", "split", "literal-to-key"):
        bad = run(before, to_conditional, rewrites=[rw(rid, rid, kind)])
        good = run(before, to_kernel, rewrites=[rw(rid, rid, kind)])
        out[kind] = (failed(bad) and bad["unmapped_removed"] >= 1 and bad["reworded"] == 0,
                     not failed(good) and good["reworded"] == 1)
    check("R-H1 a kernel rule moved to a conditional stage file with a signed row of any non-heading kind fails closed",
          all(v[0] for v in out.values()), repr(out))
    check("R-H1 the same row on a move that keeps the kernel reach still passes (reworded 1)",
          all(v[1] for v in out.values()), repr(out))


def test_rh1_pointer_unit_must_itself_be_read_at_the_original_reach() -> None:
    before = {K: "## Gate\n\n" + RULE + "\n"}
    dest = "## Gate Rules\n\n" + RULE + "\n"
    ptr_in_rr = "- Gate: see Gate Rules in remediation-and-research.md."
    after = {K: "## Other\n\nOther words here.\n", RR: dest + "\n## Gate\n\n" + ptr_in_rr + "\n"}
    res = run(before, after, rewrites=[rw(uid(RULE), uid(ptr_in_rr), "pointer")])
    check("R-H1 a pointer unit that sits in the conditional file does not carry a kernel rule", failed(res), repr(res))


def test_rh1_e2e_minimal_repro_kernel_rule_to_remediation_with_xref_row() -> None:
    rule = RULE + "\n"
    rid = uid(RULE)
    with tempfile.TemporaryDirectory() as tmp:
        root = d.scratch(tmp)
        text = (root / K).read_text(encoding="utf-8")
        chain = d.heading_chain(text, text[:text.index(rule)].count("\n") + 1)
        d.edit(root, K, rule, "")
        with open(root / RR, "a", encoding="utf-8") as fh:
            fh.write("\n" + chain + "\n" + rule)
        pm = Path(tmp) / "pm.md"
        pm.write_text(d.doc(rewrites=[rw(rid, rid, "xref")]), encoding="utf-8")
        r = d.cli("--diff", "--plugin-root", root, "--baseline", d.BASELINE, "--policy-map", pm, "--readmap",
                  root / "config" / "router-readmap.json")
        res = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    check("R-H1 e2e: `| id | id | xref |` + a valid signature no longer launders a kernel->conditional move",
          r.returncode == 1 and res.get("reworded") == 0 and res.get("unmapped_removed", 0) >= 1, f"rc={r.returncode} {res}")


# ------------------------------------------------------------------------------------------------ H-H1


TABLE_HEAD = ("## Agent Dispatch Table\n\n| Phase | Agent | Model | Effort | Notes | Owner | Cost | Scope | Gate | Mode |\n"
              "|---|---|---|---|---|---|---|---|---|---|\n")


def test_hh1_same_header_unrelated_body_is_not_related() -> None:
    old_row, gib = "| build | component-builder |", "| zzzzzz | yyyyyyyyyyyyyyyyyyy |"
    before, after = {FP: TABLE_HEAD + old_row + "\n"}, {FP: TABLE_HEAD + gib + "\n"}
    oid = unit_id(before[FP], "table_row")
    nid = unit_id(after[FP], "table_row")
    res = run(before, after, rewrites=[rw(oid, nid, "xref")])
    check("H-H1 a row whose body is gibberish under the same header is 'unrelated' (the header must not carry the overlap)",
          failed(res) and errs(res, "overlap"), repr(res))
    empty = {FP: TABLE_HEAD + "| x |\n"}
    res2 = run(before, empty, rewrites=[rw(oid, unit_id(empty[FP], "table_row"), "xref")])
    check("H-H1 a row whose body was deleted is rejected", failed(res2) and errs(res2, "overlap"), repr(res2))
    near = {FP: TABLE_HEAD + "| build | component-builder agent |\n"}
    ok = run(before, near, rewrites=[rw(oid, unit_id(near[FP], "table_row"), "xref")])
    check("H-H1 control: a genuine reword of the row body still relates", not failed(ok) and ok["reworded"] == 1, repr(ok))


# ------------------------------------------------------------------------------------------------ R-H2


def test_rh2_reviewer_shape_and_evidence_column() -> None:
    good = ["David Gracia (2026-10-09)", "José García-López (2026-10-09)", "Mary-Ann O'Neil (2026-10-09)",
            "Zoë de la Cruz (2026-10-09)"]
    bad = ["David", "david gracia (2026-10-09)", "doubt-verifier@wf:wf-abc", "David Gracia", "David Gracia (10-09)", ""]
    check("R-H2 accented, hyphenated and multi-token names are signatures", all(inv.REVIEWER.match(g) for g in good),
          repr([g for g in good if not inv.REVIEWER.match(g)]))
    check("R-H2 an agent id, a bare name, a lower-case name or a missing date is not a signature",
          not any(inv.REVIEWER.match(b) for b in bad), repr([b for b in bad if inv.REVIEWER.match(b)]))
    check("R-H2 every policy-map table header ends with an Evidence column",
          all(h.rstrip().rstrip("|").rstrip().endswith("Evidence") for h in inv.HEADERS.values()), repr(inv.HEADERS))
    old_hdr = inv.HEADERS["rationale"].replace(" | Evidence", "")
    old_doc = d.doc().replace(inv.HEADERS["rationale"], old_hdr)
    _, e = inv.parse_tables(old_doc)
    check("R-H2 a policy map with the old (pre-Evidence) header fails closed", any("header differs" in x for x in e), repr(e))
    oid, nid = uid("Run the old gate before dispatch."), uid("Run the new gate before dispatch.")
    base = {K: "## G\n\n- Run the old gate before dispatch.\n"}
    after = {K: "## G\n\n- Run the new gate before dispatch.\n"}
    res = run(base, after, rewrites=[rw(oid, nid, "xref", evidence="docs/plans/review-verdict.md")])
    check("R-H2 a row carrying an Evidence cell parses and passes when signed by a human",
          not failed(res) and res["reworded"] == 1 and res["rows_missing_review"] == 0, repr(res))
    agent = run(base, after, rewrites=[rw(oid, nid, "xref", who="doubt-verifier@wf:wf-x", evidence="docs/x.md")])
    check("R-H2 an agent id in Reviewed by is not a signature even with Evidence", agent["rows_missing_review"] == 1 and failed(agent),
          repr(agent))
    rm = (PLUGIN_ROOT.parents[1] / "docs" / "router-policy-map.md").read_text(encoding="utf-8")
    _, e2 = inv.parse_tables(rm)
    check("R-H2 the committed policy map parses against the new headers", not e2, repr(e2))
    check("R-H2 the policy map documents that the shape check is not the control", "human PR review" in rm and "policy-map v2" in rm,
          "missing text")


# ------------------------------------------------------------------------------------------------ R-H3


G1, G2 = "- Never alpha beta gamma delta epsilon.", "- Always zeta eta theta iota kappa."
N1, N2 = "- Never alpha beta gamma delta epsilon now.", "- Always zeta eta theta iota kappa now."
JUNK = "- Totally unrelated words appear here instead."


def test_rh3_group_row_many_to_many() -> None:
    before = {K: "## A\n\n" + G1 + "\n" + G2 + "\n"}
    after = {K: "## B\n\n" + N1 + "\n" + N2 + "\n" + JUNK + "\n"}
    old = f"{uid(G1)}, {uid(G2)}"
    ok = run(before, after, rewrites=[rw(old, f"{uid(N1)}, {uid(N2)}", "group")])
    check("R-H3 a group row relating two old units to two new units passes (one row, no per-unit rows)",
          not failed(ok) and ok["reworded"] == 2, repr(ok))
    bad = run(before, after, rewrites=[rw(old, f"{uid(N1)}, {uid(N2)}, {uid(JUNK)}", "group")])
    check("R-H3 a group row with one unrelated new id fails", failed(bad) and errs(bad, "group"), repr(bad))
    orphan = run(before, {K: "## B\n\n" + N1 + "\n" + JUNK + "\n"}, rewrites=[rw(old, f"{uid(N1)}, {uid(JUNK)}", "group")])
    check("R-H3 a group row where an old id relates to no new id fails", failed(orphan), repr(orphan))
    moved = run(before, {K: "## B\n\nOther.\n", RR: "## B\n\n" + N1 + "\n" + N2 + "\n"},
                rewrites=[rw(old, f"{uid(N1)}, {uid(N2)}", "group")])
    check("R-H3 a group row cannot lose reach (kernel -> conditional stage)", failed(moved) and moved["reworded"] == 0, repr(moved))
    neg = run({K: "## A\n\n" + G1 + "\n"}, {K: "## B\n\n- Alpha beta gamma delta epsilon now.\n"},
              rewrites=[rw(uid(G1), uid("- Alpha beta gamma delta epsilon now."), "group")])
    check("R-H3 a group row that drops a negation token is not a relation", failed(neg), repr(neg))


SEC = ("## 3 Build\n\n### Worktree Isolation\n\nSetup rules:\n- Never alpha one two.\n- Always beta three four.\n"
       "- Must gamma five six. [EASY TO MISS: keep the tree clean.]\n")
MOVED = SEC.replace("## 3 Build\n\n### Worktree Isolation", "## Worktree Isolation")


def sm_row(dst: str = K, who: str = WHO) -> str:
    return rw(f"{BW}#3 build > worktree isolation", f"{dst}#worktree isolation", "section-move", who)


def test_rh3_section_move_one_row_moves_a_whole_section_across_files() -> None:
    before = {BW: SEC}
    ok = run(before, {BW: "## Other\n\nx y z.\n", K: MOVED}, rewrites=[sm_row()])
    base_units = len(inv.extract_units(SEC, BW))
    check("R-H3 a 3-bullet + EASY-TO-MISS section moved across files (depth 2->1) with ONE signed section-move row passes",
          not failed(ok) and ok["unmapped_removed"] == 0 and base_units == 5, f"units={base_units} {ok}")
    dropped = MOVED.replace("- Always beta three four.\n", "")
    bad = run(before, {BW: "## Other\n\nx y z.\n", K: dropped}, rewrites=[sm_row()])
    check("R-H3 the same move with one bullet dropped fails", failed(bad) and bad["unmapped_removed"] == 1, repr(bad))
    ctl = run(before, {BW: "## Other\n\nx y z.\n", K: MOVED})
    check("R-H3 control: the same move without a section-move row fails (heading path changed)",
          failed(ctl) and ctl["unmapped_removed"] >= 5, repr(ctl))
    nodrop = run(before, {BW: "## Other\n\nx y z.\n", K: MOVED.replace(" [EASY TO MISS: keep the tree clean.]", "")},
                 rewrites=[sm_row()])
    check("R-H3 dropping only the EASY-TO-MISS span under a section-move row fails", failed(nodrop), repr(nodrop))
    unsigned = run(before, {BW: "## Other\n\nx y z.\n", K: MOVED}, rewrites=[sm_row(who="doubt-verifier@wf:x")])
    check("R-H3 a section-move row needs a human signature", unsigned["rows_missing_review"] == 1 and failed(unsigned), repr(unsigned))
    cond = run(before, {BW: "## Other\n\nx y z.\n", RR: MOVED}, rewrites=[sm_row(RR)])
    check("R-H3 a section-move into a conditional-stage file loses reach and fails unless narrowed",
          failed(cond) and cond["unmapped_removed"] >= 1, repr(cond))
    nosrc = run(before, {BW: SEC, K: MOVED}, rewrites=[rw(f"{BW}#no such > section", f"{K}#worktree isolation", "section-move")])
    check("R-H3 a section-move whose source path names nothing in the baseline is a row error", errs(nosrc, "section-move"), repr(nosrc))
    nodst = run(before, {BW: "## Other\n\nx y z.\n", K: MOVED}, rewrites=[rw(f"{BW}#3 build > worktree isolation",
                f"{K}#missing heading", "section-move")])
    check("R-H3 a section-move whose destination heading does not exist is a row error", errs(nodst, "section-move") and failed(nodst),
          repr(nodst))


# ------------------------------------------------------------------------------------------------ R-M1 / H-M1


OLD_R = "- Never ship the plan unreviewed today please."


def pointer_run(ptr: str) -> dict:
    before = {K: "## Gate\n\n" + OLD_R + "\n"}
    after = {K: "## Gate\n\n" + ptr + "\n", RR: "## Remediation Steps\n\n" + OLD_R + "\n"}
    return run(before, after, rewrites=[rw(uid(OLD_R), uid(ptr), "pointer")])


def test_rm1_pointer_must_name_destination_file_and_an_existing_heading() -> None:
    good = pointer_run("- Gate: see Remediation Steps in remediation-and-research.md.")
    check("R-M1 a pointer naming the destination file and an existing heading in it passes", not failed(good) and good["reworded"] == 1,
          repr(good))
    only_heading = pointer_run("- Gate: moved elsewhere.")
    check("R-M1 a pointer that names only the old heading is not a pointer", failed(only_heading) and errs(only_heading, "overlap"),
          repr(only_heading))
    ghost = pointer_run("- Gate: see Nonexistent Heading in remediation-and-research.md.")
    check("R-M1 a pointer naming a heading that does not exist in the named file is rejected", failed(ghost), repr(ghost))
    nofile = pointer_run("- Gate: see Remediation Steps somewhere.")
    check("R-M1 a pointer naming a heading but no file is rejected", failed(nofile), repr(nofile))


def test_hm1_dropping_a_negation_is_never_a_relation() -> None:
    new = "- Ship the plan unreviewed today please."
    for kind in ("xref", "dedupe", "split"):
        res = run({K: "## G\n\n" + OLD_R + "\n"}, {K: "## G\n\n" + new + "\n"}, rewrites=[rw(uid(OLD_R), uid(new), kind)])
        check(f"H-M1 a `{kind}` row that drops `never` (still >= 60% overlap) fails", failed(res) and errs(res, "overlap"), repr(res))
    keep = "- Never ship the plan unreviewed today please now."
    ok = run({K: "## G\n\n" + OLD_R + "\n"}, {K: "## G\n\n" + keep + "\n"}, rewrites=[rw(uid(OLD_R), uid(keep), "xref")])
    check("H-M1 control: a reword that keeps the negation still relates", not failed(ok), repr(ok))


# ------------------------------------------------------------------------------------------------ H-M2


def test_hm2_unparseable_frontmatter_is_not_skipped() -> None:
    def raises(text: str) -> str:
        try:
            inv.extract_units(text, "x.md")
        except ValueError as exc:
            return str(exc)
        return ""

    body = "---\n{}\n---\n\n## A\n\nbody text\n"
    good = inv.extract_units(body.format("name: x\ndescription: hi there"), "x.md")
    check("H-M2 control: a frontmatter with a description is a unit", any(u["kind"] == "frontmatter" for u in good), repr(good))
    check("H-M2 frontmatter without a description raises", "frontmatter" in raises(body.format("name: x")), "no raise")
    check("H-M2 an empty description raises", "frontmatter" in raises(body.format("name: x\ndescription:")), "no raise")
    check("H-M2 a list-valued description raises", "frontmatter" in raises(body.format("description:\n  - a\n  - b")), "no raise")
    check("H-M2 an unaccounted frontmatter line raises", "frontmatter" in raises(body.format("description: hi\njust some words")),
          "no raise")
    check("H-M2 a leading thematic break with no closing `---` is not frontmatter", raises("---\n\n## A\n\nbody\n") == "", "raised")


# ------------------------------------------------------------------------------------------------ H-M3 / R-M2


def test_hm3_verify_lock_requires_the_pinned_commit_to_exist() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fx = Path(tmp)
        base = json.loads((HERE / "router-rule-inventory.baseline.json").read_text(encoding="utf-8"))
        ghost = "1" * 40
        base["ref"] = ghost
        bp, rp, lp = fx / "b.json", fx / "rm.json", fx / "lock.json"
        bp.write_text(json.dumps(base), encoding="utf-8")
        shutil.copy(HERE / "router-readmap.baseline.json", rp)
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
        lp.write_text(json.dumps({"baseline_sha": ghost, "baseline_sha256": sha(bp), "readmap_baseline_sha256": sha(rp)}),
                      encoding="utf-8")
        try:
            inv.verify_lock(bp, rp, lp)
            msg = "accepted"
        except ValueError as exc:
            msg = str(exc)
    check("H-M3 a lock whose commit is not in the clone fails closed and says fetch-depth 0 is required",
          "fetch-depth 0" in msg, msg)
    real = inv.verify_lock(HERE / "router-rule-inventory.baseline.json", HERE / "router-readmap.baseline.json",
                           HERE / "router-baseline.lock.json")
    check("H-M3 the committed lock still verifies (the pinned commit is present)", real["ref"].startswith("0439c339"), repr(real["ref"]))


def test_hm3_policy_map_documents_the_commit_check_and_rm2_gate() -> None:
    pm = (PLUGIN_ROOT.parents[1] / "docs" / "router-policy-map.md").read_text(encoding="utf-8")
    check("H-M3 the policy map describes the `git cat-file` commit check and the [rebaseline] CI gate",
          "cat-file" in pm and "[rebaseline]" in pm and "CODEOWNERS" in pm, "missing text")


def test_rm2_ci_gate_and_codeowners() -> None:
    ci = (REPO / ".github" / "workflows" / "craftflow-ci.yml").read_text(encoding="utf-8")
    co = (REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8") if (REPO / ".github" / "CODEOWNERS").is_file() else ""
    lock = "tools/craftflow-plugin/plugins/craftflow/tests/fixtures/router-baseline.lock.json"
    check("R-M2 craftflow-ci runs --diff and compares the lock with origin/main unless the PR is tagged [rebaseline]",
          "craftflow_rule_inventory.py" in ci and "--diff" in ci and "origin/main" in ci and "[rebaseline]" in ci
          and "github.event.pull_request.title" in ci and "github.event.pull_request.body" in ci and "router-baseline.lock.json" in ci,
          "missing step")
    check("R-M2 the rebaseline gate passes the PR text through env, not inline shell interpolation",
          not re.search(r"run:[^\n]*\$\{\{\s*github\.event\.pull_request\.(title|body)", ci), "inline interpolation")
    owners = {ln.split()[0] for ln in co.splitlines() if ln.strip() and not ln.startswith("#") and "@volkz" in ln}
    need = {lock, lock.replace("router-baseline.lock.json", "router-rule-inventory.baseline.json"),
            lock.replace("router-baseline.lock.json", "router-readmap.baseline.json")}
    check("R-M2 CODEOWNERS names @volkz for the lock, the baseline and the read-map baseline", need <= owners, repr(owners))


# ------------------------------------------------------------------------------------------------ lows


def test_hl1_baseline_readmap_is_required() -> None:
    p = inspect.signature(inv.diff_inventory).parameters["baseline_readmap"]
    check("H-L1 diff_inventory(baseline_readmap=...) has no default", p.default is inspect.Parameter.empty, repr(p))


def test_hl2_main_passes_the_allowlist_through() -> None:
    seen = {}
    real = inv.diff_inventory

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    inv.diff_inventory = spy
    try:
        with open("/dev/null", "w") as null:
            old, sys.stdout = sys.stdout, null
            try:
                inv.main(["--diff", "--baseline", str(d.BASELINE), "--policy-map", str(d.POLICY_MAP)])
            finally:
                sys.stdout = old
    finally:
        inv.diff_inventory = real
    check("H-L2 main() hands the default allowlist to diff_inventory", bool(seen.get("allowlist")) and
          frozenset(seen["allowlist"]) == frozenset(inv.load_allowlist(inv.DEFAULT_ALLOWLIST)), repr(sorted(seen)))


def test_hl3_happy_path_is_validated_on_every_class() -> None:
    def rm_with(value) -> dict:
        rm = json.loads(json.dumps(RMX))
        rm["files"][0]["happy_path"] = value
        return rm

    try:
        inv.coverage(rm_with("yes"))
        msg = "accepted"
    except ValueError as exc:
        msg = str(exc)
    check("H-L3 a non-boolean happy_path on a kernel entry is rejected", "happy_path" in msg, msg)
    check("H-L3 a boolean (or absent) happy_path on a kernel entry is fine", bool(inv.coverage(rm_with(False))), "rejected")


def test_hl4_heading_rows_are_scoped_to_a_file_and_the_new_heading_must_exist() -> None:
    before = {K: "## Gate\n\n- Never skip it please.\n", FP: "## Gate\n\n- Never skip it please too.\n"}
    after = {K: "## Gate Rules\n\n- Never skip it please.\n", FP: "## Gate\n\n- Never skip it please too.\n"}
    hr = lambda f, old, new: f"| {f}#{old} | {f}#{new} | heading | renamed | same | {WHO} |"  # noqa: E731
    ok = run(before, after, rewrites=[hr(K, "gate", "gate rules")])
    check("H-L4 a heading row scoped to the file that holds the rename passes and renames only that file",
          not failed(ok) and ok["unmapped_removed"] == 0, repr(ok))
    wrong = run(before, after, rewrites=[hr(FP, "gate", "gate rules")])
    check("H-L4 a heading row naming another file does not cover the rename and its new heading is checked there",
          failed(wrong) and errs(wrong, "heading"), repr(wrong))
    ghost = run(before, after, rewrites=[hr(K, "gate", "gate rule")])
    check("H-L4 a heading row whose new heading does not exist in that file is a row error", failed(ghost) and errs(ghost, "heading"),
          repr(ghost))
    nofile = run(before, after, rewrites=[f"| gate | gate rules | heading | renamed | same | {WHO} |"])
    check("H-L4 a heading row without a file is a row error", failed(nofile) and errs(nofile, "heading"), repr(nofile))


def test_rl1_adr_count_text() -> None:
    adr = (REPO / "docs" / "ai" / "decisions" / "0073-craftflow-policy-first-router-kernel.md").read_text(encoding="utf-8")
    flat = " ".join(adr.split())
    check("R-L1 ADR-0073 states 12 table_header + 1 frontmatter + 1 design_file split = +14, not '13 table-header units'",
          "12 table_header + 1 frontmatter + 1 design_file split = +14" in flat and "13 table-header units" not in flat,
          "count text")


# ------------------------------------------------------------------------------------------------ REM-FIX 5


LONG_RULE = ("- Never spawn Memory Update as a sub-agent because the router runs it inline at the end of every workflow "
             "before the final reply is emitted to the user.")
FRAGMENT = "- Never spawn Memory Update."


def test_rf5_group_row_fragment_does_not_retain_the_old_rule() -> None:
    res = run({K: "## Gate\n\n" + LONG_RULE + "\n"}, {K: "## Gate\n\n" + FRAGMENT + "\n"},
              rewrites=[rw(uid(LONG_RULE), uid(FRAGMENT), "group")])
    check("RF5 a group row that reduces a 36-token rule to a 4-token fragment fails (under 60% retention)",
          failed(res) and res["reworded"] == 0 and errs(res, "under 60% retention"), repr(res))


def test_rf5_group_row_legitimate_reflow_passes() -> None:
    a, b = "- Never alpha beta gamma delta epsilon zeta eta.", "- Always iota kappa lambda mu nu xi."
    n1, n2, n3 = "- Never alpha beta gamma delta epsilon.", "- Zeta eta alpha.", "- Always iota kappa lambda mu nu."
    res = run({K: "## A\n\n" + a + "\n" + b + "\n"}, {K: "## B\n\n" + n1 + "\n" + n2 + "\n" + n3 + "\n"},
              rewrites=[rw(f"{uid(a)}, {uid(b)}", f"{uid(n1)}, {uid(n2)}, {uid(n3)}", "group")])
    check("RF5 a 2-old -> 3-new group reflow where every old unit is >= 60% retained still passes",
          not failed(res) and res["reworded"] == 2, repr(res))


def test_rf5_group_row_new_unit_mostly_new_words_fails() -> None:
    a = "- Never alpha beta gamma delta epsilon zeta eta."
    n1, n2 = "- Never alpha beta gamma delta epsilon zeta eta.", "- Alpha one two three four five six."
    res = run({K: "## A\n\n" + a + "\n"}, {K: "## B\n\n" + n1 + "\n" + n2 + "\n"},
              rewrites=[rw(uid(a), f"{uid(n1)}, {uid(n2)}", "group")])
    check("RF5 a group row with a new unit under 60% drawn from old units fails", failed(res), repr(res))


def test_rf5_pointer_to_unrelated_section_fails() -> None:
    old = "- Never spawn Memory Update as a sub-agent."
    ptr = "- Gate: see remediation-and-research.md Overview."
    before = {K: "## Gate\n\n" + old + "\n"}
    bad = run(before, {K: "## Gate\n\n" + ptr + "\n", RR: "## Overview\n\nUnrelated words about something else entirely.\n"},
              rewrites=[rw(uid(old), uid(ptr), "pointer")])
    check("RF5 a pointer to an existing but unrelated heading of the named file fails", failed(bad) and bad["reworded"] == 0
          and errs(bad, "overlap"), repr(bad))
    good = run(before, {K: "## Gate\n\n" + ptr + "\n", RR: "## Overview\n\n" + old + "\n"},
               rewrites=[rw(uid(old), uid(ptr), "pointer")])
    check("RF5 a pointer to a section that holds the rule passes", not failed(good) and good["reworded"] == 1, repr(good))


def _dedupe(old: str, new: str) -> dict:
    return run({K: "## Gate\n\n" + old + "\n"}, {K: "## Gate\n\n" + new + "\n"}, rewrites=[rw(uid(old), uid(new), "dedupe")])


def test_rf5_negation_guard_is_two_way() -> None:
    cases = {
        "must -> must not": ("- Memory Update must run inline.", "- Memory Update must not run inline."),
        "cannot -> can": ("- You cannot skip the review step now.", "- You can skip the review step now."),
        "runs -> never runs": ("- The router runs memory update inline.", "- The router never runs memory update inline."),
        "don't -> do": ("- You don't skip the review step now.", "- You do skip the review step now."),
        "dropped without": ("- Run it without the cache layer now.", "- Run it with the cache layer now."),
        "gained unless": ("- Run it with the cache layer now.", "- Run it unless the cache layer now."),
        "count lost": ("- Never skip and never retry the review step.", "- Never skip and retry the review step."),
    }
    for name, (old, new) in cases.items():
        res = _dedupe(old, new)
        check(f"RF5 negation {name} is not a relation", failed(res) and res["reworded"] == 0 and errs(res, "overlap"), repr(res))
    same = "- The router never runs memory update inline."
    ok = _dedupe(same, same.replace("inline.", "inline now."))
    check("RF5 control: the same negation on both sides still relates", not failed(ok) and ok["reworded"] == 1, repr(ok))
    ok2 = _dedupe("- You don't skip the review step now.", "- You don't skip the review step today.")
    check("RF5 control: the same n't form on both sides still relates", not failed(ok2) and ok2["reworded"] == 1, repr(ok2))


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
