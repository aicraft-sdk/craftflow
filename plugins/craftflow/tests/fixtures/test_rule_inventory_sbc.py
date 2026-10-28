#!/usr/bin/env python3
"""Tests for signed behavior change rows (AD-7, AD-10; craftflow_rule_inventory_sbc.py).

Run: python3 tests/fixtures/test_rule_inventory_sbc.py
Synthetic corpora only: each case builds a baseline from a literal corpus and diffs it against a literal after corpus.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_rule_inventory as inv  # noqa: E402
import craftflow_rule_inventory_sbc as sbc  # noqa: E402

SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_rule_inventory.py"
FIXTURES = PLUGIN_ROOT / "tests" / "fixtures"
POLICY_MAP = PLUGIN_ROOT.parents[1] / "docs" / "router-policy-map.md"

_passes = 0
_errors: list = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def check(name: str, cond: bool, reason: str = "") -> None:
    ok(name) if cond else fail(name, reason or "assertion false")


P = "skills/_shared/router-protocol.md"
REF = "skills/craftflow-router/references/later.md"
RM1 = {"version": 1, "files": [{"file": P, "class": "kernel", "workflows": ["ALL"]}]}
RM9 = {"version": 1, "files": [{"file": P, "class": "kernel", "workflows": ["ALL"]},
                               {"file": REF, "class": "stage", "workflows": ["ALL"], "happy_path": False}]}
DAVID = "David Gracia (2026-10-28)"
ADRS = {"ADR-0075": "proposed", "ADR-0001": "accepted", "ADR-0002": "rejected"}


def adr_lookup(adr: str):
    """Injected ADR lookup: the status of an existing ADR, None for a missing one."""
    return ADRS.get(adr)


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def id_of(text: str) -> str:
    return sha(inv.normalize(text))


def table(header: str, rows: list) -> str:
    sep = "|" + "|".join(["---"] * (header.count("|") - 1)) + "|"
    return "\n".join([header, sep] + rows) + "\n"


def policy_doc(rewrites=(), sbc_rows=None) -> str:
    """A policy map with every required section (empty tables), optionally with `## Signed behavior changes`."""
    section = "" if sbc_rows is None else (
        "\n## Signed behavior changes\n\nRules paragraph.\n\n" + table(sbc.SBC_HEADER, list(sbc_rows)))
    return (
        "# Router policy map\n\n"
        "## Policy-mapped deletions\n\n" + table(inv.HEADERS["policy"], []) +
        "\n## Rewrites\n\n" + table(inv.HEADERS["rewrites"], list(rewrites)) +
        "\n## Applicability\n\n" + table(inv.HEADERS["applicability"], []) +
        "\n## Rationale\n\n" + table(inv.HEADERS["rationale"], []) +
        "\n## Read-map widening\n\n" + table(inv.HEADERS["widening"], []) + section +
        "\n## Worktree finalize equivalence\n\n"
    )


def run(before: dict, after: dict, doc: str, rm: dict = RM1, adr_status=adr_lookup, sbc_rows_on: bool = True) -> dict:
    base = inv.build_inventory(before, rm, set(), ref="fixture")
    return inv.diff_inventory(base, after, rm, doc, baseline_readmap=rm, adr_status=adr_status, sbc_rows_on=sbc_rows_on)


def esc_row(cells: list) -> str:
    return "| " + " | ".join(sbc.escape_cell(c) for c in cells) + " |"


def sbc_row(old: str, new: str, *, who=DAVID, adr="ADR-0075", sup="—", bid=None, nid=None, old_cell=None, new_cell=None) -> str:
    return esc_row([bid or id_of(old), nid or id_of(new), sup, inv.normalize(old) if old_cell is None else old_cell,
                    inv.normalize(new) if new_cell is None else new_cell, adr, who, ""])


def rw_row(old_id: str, new_id: str, kind="dedupe", who="David Gracia (2026-10-21)") -> str:
    return f"| {old_id} | {new_id} | {kind} | reworded | same rule | {who} |"


def errs_with(res: dict, text: str) -> bool:
    return any(text in e for e in res["row_errors"])


OLD_B, NEW_B = "- ERROR always wins over BUILD.", "- ERROR wins only when the deliverable is a fix."
OLD1 = "## Intent Routing\n\nRules:\n" + OLD_B + "\n- REVIEW is advisory only.\n"
NEW1 = "## Intent Routing\n\nRules:\n" + NEW_B + "\n- REVIEW is advisory only.\n"
B1, A1 = {P: OLD1}, {P: NEW1}


def test_case1_row_types_for_a_guarded_rewrite() -> None:
    none = run(B1, A1, policy_doc())
    ordinary = run(B1, A1, policy_doc(rewrites=[rw_row(id_of(OLD_B), id_of(NEW_B), who=DAVID)]))
    signed = run(B1, A1, policy_doc(sbc_rows=[sbc_row(OLD_B, NEW_B)]))
    check("1a: no row -> unmapped_removed == 1, exit 1", none["unmapped_removed"] == 1 and inv.exit_code(none) == 1, repr(none))
    check("1b: an ordinary Rewrites row -> 'changed guarded word', exit 1",
          errs_with(ordinary, "changed guarded word") and inv.exit_code(ordinary) == 1, repr(ordinary))
    check("1c: a David-signed behavior change row -> exit 0, reworded 1, signed_behavior_changes 1",
          inv.exit_code(signed) == 0 and signed["reworded"] == 1 and signed["signed_behavior_changes"] == 1
          and signed["unmapped_removed"] == 0 and signed["row_errors"] == [], repr(signed))


def test_case2_signature_gates_the_exit_code_only() -> None:
    for name, who in (("PENDING (David)", "PENDING (David)"), ("well-formed non-signer", "Some Agent (2026-10-28)")):
        res = run(B1, A1, policy_doc(sbc_rows=[sbc_row(OLD_B, NEW_B, who=who)]))
        check(f"2: {name} -> rows_missing_review 1, row still applied, exit 1",
              res["rows_missing_review"] == 1 and res["unmapped_removed"] == 0 and res["signed_behavior_changes"] == 1
              and inv.exit_code(res) == 1, repr(res))


def test_case3_texts_must_match_verbatim() -> None:
    old_n, new_n = inv.normalize(OLD_B), inv.normalize(NEW_B)
    old_bad = run(B1, A1, policy_doc(sbc_rows=[sbc_row(OLD_B, NEW_B, old_cell=old_n[:-1] + "!")]))
    new_bad = run(B1, A1, policy_doc(sbc_rows=[sbc_row(OLD_B, NEW_B, new_cell=new_n + "x")]))
    check("3: old text one character off -> 'old text differs'", errs_with(old_bad, "old text differs") and inv.exit_code(old_bad) == 1, repr(old_bad))
    check("3: new text different -> 'new text differs'", errs_with(new_bad, "new text differs") and inv.exit_code(new_bad) == 1, repr(new_bad))


def test_case4_id_cells_and_duplicates() -> None:
    two = run(B1, A1, policy_doc(sbc_rows=[sbc_row(OLD_B, NEW_B, bid=id_of(OLD_B) + " " + id_of("- REVIEW is advisory only."))]))
    absent = run(B1, A1, policy_doc(sbc_rows=[sbc_row(OLD_B, NEW_B, nid="0" * 12)]))
    both = run(B1, A1, policy_doc(rewrites=[rw_row(id_of(OLD_B), id_of(NEW_B))], sbc_rows=[sbc_row(OLD_B, NEW_B)]))
    twice = run(B1, A1, policy_doc(sbc_rows=[sbc_row(OLD_B, NEW_B), sbc_row(OLD_B, NEW_B)]))
    check("4: two ids in the Baseline ID cell -> error", errs_with(two, "exactly one baseline id") and inv.exit_code(two) == 1, repr(two))
    check("4: a New ID absent from the after corpus -> error", errs_with(absent, "not in the live corpus") and inv.exit_code(absent) == 1, repr(absent))
    check("4: the same baseline id also in a Rewrites row -> error", errs_with(both, "also named by a Rewrites row") and inv.exit_code(both) == 1, repr(both))
    check("4: a second behavior change row for the same id -> error",
          errs_with(twice, "more than one signed behavior change row") and inv.exit_code(twice) == 1, repr(twice))


def test_case5_adr_cell() -> None:
    cases = {"ADR-9999": False, "ADR-0075": True, "ADR-0001": True, "ADR-0002": False, "ADR-75": False}
    bad = []
    for adr, want_ok in cases.items():
        res = run(B1, A1, policy_doc(sbc_rows=[sbc_row(OLD_B, NEW_B, adr=adr)]))
        if (inv.exit_code(res) == 0) != want_ok or (not want_ok and not errs_with(res, adr)):
            bad.append((adr, res["row_errors"]))
    check("5: ADR must exist (injected lookup) and be neither rejected nor superseded; proposed is accepted", not bad, repr(bad))
    check("5: the default lookup reads docs/ai/decisions status frontmatter", sbc.adr_status("ADR-0075") == "proposed"
          and sbc.adr_status("ADR-9999") is None, f"{sbc.adr_status('ADR-0075')!r} {sbc.adr_status('ADR-9999')!r}")


FENCE_OLD = "```text\nphase:{build|plan-review-gap-2}\n```"
FENCE_NEW = "```text\nphase:{build|plan-review-gap-2|qa-plan-review-2}\n```"


def only_unit(corpus: dict, rm: dict, kind: str) -> dict:
    return next(e for e in inv.build_inventory(corpus, rm, set())["entries"] if e["kind"] == kind)


def test_case6_numbered_token_fence() -> None:
    before, after = {P: "## Metadata\n\n" + FENCE_OLD + "\n"}, {P: "## Metadata\n\n" + FENCE_NEW + "\n"}
    old_e, new_e = only_unit(before, RM1, "fence"), only_unit(after, RM1, "fence")
    ordinary = run(before, after, policy_doc(rewrites=[rw_row(old_e["id"], new_e["id"], "literal-to-key", DAVID)]))
    line = sbc.render_sbc_row(old_e, new_e, inv._body, "ADR-0075").replace("PENDING (David)", DAVID)
    signed = run(before, after, policy_doc(sbc_rows=[line]))
    check("6: adding a numbered token fails as a Rewrites row", inv.exit_code(ordinary) == 1 and errs_with(ordinary, "changed guarded word or number"), repr(ordinary))
    check("6: the same fence passes as a signed behavior change row", inv.exit_code(signed) == 0 and signed["reworded"] == 1, repr(signed))


def test_case7_label_paragraph_rename_keeps_table_rows() -> None:
    old_l, new_l = "Route using the first matching signal:", "Route by the primary deliverable:"
    tbl = "\n\n| Priority | Signal |\n|---|---|\n| 1 | ERROR |\n| 2 | PLAN |\n"
    before, after = {P: "## Routing\n\n" + old_l + tbl}, {P: "## Routing\n\n" + new_l + tbl}
    without = run(before, after, policy_doc())
    signed = run(before, after, policy_doc(sbc_rows=[sbc_row(old_l, new_l)]))
    check("7: without the row the label rewrite leaves unmapped units", without["unmapped_removed"] > 0, repr(without))
    check("7: with the row both table rows count as present (label rename applied)",
          inv.exit_code(signed) == 0 and signed["present"] == 3 and signed["reworded"] == 1, repr(signed))


def test_case8_last_bullet_rewrite_keeps_following_paragraph() -> None:
    para = "Host note stays put."
    tail = "\n\n" + para + "\n"
    before = {P: "## Rules\n\nRules:\n- First rule stays.\n" + OLD_B + tail}
    after = {P: "## Rules\n\nRules:\n- First rule stays.\n" + NEW_B + tail}
    without = run(before, after, policy_doc())
    signed = run(before, after, policy_doc(sbc_rows=[sbc_row(OLD_B, NEW_B)]))
    check("8: without the row the following paragraph is unmapped too", without["unmapped_removed"] == 2, repr(without))
    check("8: with the row the paragraph counts as present (anchor rename applied)",
          inv.exit_code(signed) == 0 and signed["present"] >= 3 and signed["unmapped_removed"] == 0, repr(signed))


def test_case9_the_row_never_widens_reach() -> None:
    filler = "## Later\n\nFiller reference.\n"
    moved = "ERROR wins only when the deliverable is a fix."
    before = {P: OLD1, REF: filler}
    after = {P: "## Intent Routing\n\nRules:\n- REVIEW is advisory only.\n", REF: filler + "\n" + moved + "\n"}
    res = run(before, after, policy_doc(sbc_rows=[sbc_row(OLD_B, moved)]), rm=RM9)
    check("9: a replacement read only as a conditional stage never accounts a kernel unit",
          res["unmapped_removed"] == 1 and res["reworded"] == 0 and res["unreachable"] == 0 and inv.exit_code(res) == 1, repr(res))


def test_case10_cell_escaping_round_trip() -> None:
    old_t = "## Chain\n\n| Priority | Chain |\n|---|---|\n| 4 | fast path: a \\|\\| b |\n"
    new_t = old_t.replace("fast path", "slow path")
    old_e, new_e = only_unit({P: old_t}, RM1, "table_row"), only_unit({P: new_t}, RM1, "table_row")
    line = sbc.render_sbc_row(old_e, new_e, inv._body, "ADR-0075")
    errors: list = []
    rows = sbc.parse_sbc_rows(policy_doc(sbc_rows=[line]), errors)
    check("10: a table-row unit with \\|\\| round-trips through render_sbc_row and parse_sbc_rows",
          errors == [] and len(rows) == 1 and rows[0][3] == inv._body(old_e) and rows[0][4] == inv._body(new_e)
          and "\\|\\|" in rows[0][3] and rows[0][6] == "PENDING (David)", f"rows={rows!r} errors={errors!r}")


def test_case11_additive_controls() -> None:
    base = "## S\n\nIntro paragraph stays.\n\n- Item one stays.\n- Item two stays.\n\n## T\n\nNext section text.\n"
    appended = base.replace("\n## T", "\nNew paragraph appended.\n\n## T")
    inserted = base.replace("- Item one", "New paragraph inserted.\n\n- Item one")
    good = run({P: base}, {P: appended}, policy_doc(sbc_rows=[]))
    bad = run({P: base}, {P: inserted}, policy_doc(sbc_rows=[]))
    check("11: a paragraph appended at the end of a section passes with no rows",
          inv.exit_code(good) == 0 and good["unmapped_removed"] == 0 and good["signed_behavior_changes"] == 0, repr(good))
    check("11: the same paragraph inserted between a paragraph and its list is unmapped",
          bad["unmapped_removed"] > 0 and inv.exit_code(bad) == 1, repr(bad))


X_TXT = "Dispatch the first agent before anything else."
Y_TXT = "Dispatch the first agent before everything else."
Z_TXT = "Dispatch the first agent only after the gate opens."
W_TXT = "A second paragraph stays put."
B13 = {P: "## Dispatch\n\n" + X_TXT + "\n\n" + W_TXT + "\n"}
A13 = {P: "## Dispatch\n\n" + Z_TXT + "\n\n" + W_TXT + "\n"}
X, Y, W = id_of(X_TXT), id_of(Y_TXT), id_of(W_TXT)
SUP = f"Rewrites row {X}"


def test_case12_missing_section_and_switch() -> None:
    errors: list = []
    zero = sbc.parse_sbc_rows(policy_doc(), errors)
    check("12: a policy map without the section has zero rows and no error", zero == [] and errors == [], f"{zero!r} {errors!r}")
    res = run(B1, A1, policy_doc(sbc_rows=[sbc_row(OLD_B, NEW_B)]), sbc_rows_on=False)
    check("12: sbc_rows_on=False ignores a present section (no SBC rows)",
          res["unmapped_removed"] == 1 and res["signed_behavior_changes"] == 0 and res["superseded_rewrites"] == [], repr(res))
    off = run(B13, A13, policy_doc(rewrites=[rw_row(X, Y)], sbc_rows=[sbc_row(X_TXT, Z_TXT, sup=SUP)]), sbc_rows_on=False)
    check("12: sbc_rows_on=False applies no supersede skip",
          errs_with(off, "rewrite row names replacement ids that do not exist") and off["superseded_rewrites"] == [], repr(off))


def test_case13_supersede_accepted() -> None:
    res = run(B13, A13, policy_doc(rewrites=[rw_row(X, Y)], sbc_rows=[sbc_row(X_TXT, Z_TXT, sup=SUP)]))
    check("13: a David-signed SBC row superseding the signed Rewrites row -> exit 0",
          inv.exit_code(res) == 0 and res["superseded_rewrites"] == [X] and res["signed_behavior_changes"] == 1
          and res["row_errors"] == [], repr(res))


def test_case14_dangling_target_without_supersede() -> None:
    res = run(B13, A13, policy_doc(rewrites=[rw_row(X, Y)]))
    check("14: the dangling Rewrites target without an SBC row -> error, X unmapped, exit 1",
          errs_with(res, "rewrite row names replacement ids that do not exist: " + Y) and X in res["unmapped_ids"]
          and inv.exit_code(res) == 1, repr(res))


def test_case15_supersede_of_an_unsigned_row() -> None:
    res = run(B13, A13, policy_doc(rewrites=[rw_row(X, Y, who="PENDING (David)")], sbc_rows=[sbc_row(X_TXT, Z_TXT, sup=SUP)]))
    check("15: superseding an unsigned Rewrites row -> error plus the dangling-target error, exit 1",
          errs_with(res, "supersedes an unsigned Rewrites row") and errs_with(res, "replacement ids that do not exist: " + Y)
          and inv.exit_code(res) == 1 and res["superseded_rewrites"] == [], repr(res))


def test_case16_supersede_of_an_unnamed_row() -> None:
    other = "ab" * 6
    unknown = run(B13, A13, policy_doc(rewrites=[rw_row(X, Y)], sbc_rows=[sbc_row(X_TXT, Z_TXT, sup=f"Rewrites row {other}")]))
    different = run(B13, A13, policy_doc(rewrites=[rw_row(X, Y), rw_row(W, W)], sbc_rows=[sbc_row(X_TXT, Z_TXT, sup=f"Rewrites row {W}")]))
    empty = run(B13, A13, policy_doc(rewrites=[rw_row(X, Y)], sbc_rows=[sbc_row(X_TXT, Z_TXT, sup="—")]))
    dangling = "replacement ids that do not exist: " + Y
    check("16: a Supersedes id with no Rewrites row -> 'supersedes no single Rewrites row'",
          errs_with(unknown, "supersedes no single Rewrites row") and errs_with(unknown, dangling) and inv.exit_code(unknown) == 1, repr(unknown))
    check("16: a Supersedes id of a different row -> the cell must read its own baseline id",
          errs_with(different, f"Supersedes cell must read 'Rewrites row {X}'") and errs_with(different, dangling)
          and inv.exit_code(different) == 1, repr(different))
    check("16: an empty Supersedes cell while a Rewrites row exists -> rule (d) error",
          errs_with(empty, "also named by a Rewrites row") and errs_with(empty, dangling) and inv.exit_code(empty) == 1, repr(empty))


def test_case17_superseded_row_deleted() -> None:
    res = run(B13, A13, policy_doc(sbc_rows=[sbc_row(X_TXT, Z_TXT, sup=SUP)]))
    check("17: a Supersedes cell whose Rewrites row was deleted -> error, exit 1",
          errs_with(res, "supersedes no single Rewrites row") and inv.exit_code(res) == 1, repr(res))


def test_case18_supersede_of_a_heading_row() -> None:
    res = run(B13, A13, policy_doc(rewrites=[rw_row(X, Y, kind="heading")], sbc_rows=[sbc_row(X_TXT, Z_TXT, sup=SUP)]))
    check("18: superseding a heading Rewrites row -> error, exit 1",
          errs_with(res, "supersedes a heading") and inv.exit_code(res) == 1 and res["superseded_rewrites"] == [], repr(res))


def test_case19_sbc_row_cli_prints_a_pending_row() -> None:
    baseline = json.loads((FIXTURES / "router-rule-inventory.baseline.json").read_text(encoding="utf-8"))
    unit = next(e for e in baseline["entries"] if "review is advisory only" in e["excerpt"] and e["kind"] == "list_item")
    cmd = [sys.executable, str(SCRIPT), "--sbc-row", "--baseline", str(FIXTURES / "router-rule-inventory.baseline.json"),
           "--old-id", unit["id"], "--new-id", unit["id"], "--adr", "ADR-0075"]
    plain = subprocess.run(cmd, capture_output=True, text=True)
    sup = subprocess.run(cmd + ["--supersedes"], capture_output=True, text=True)
    errors: list = []
    rows = sbc.parse_sbc_rows(policy_doc(sbc_rows=[plain.stdout.strip()]), errors)
    check("19: --sbc-row prints one escaped row with PENDING (David) and the unit's body for both texts",
          plain.returncode == 0 and errors == [] and len(rows) == 1 and rows[0][:3] == [unit["id"], unit["id"], "—"]
          and rows[0][3] == rows[0][4] != "" and rows[0][5] == "ADR-0075" and rows[0][6] == "PENDING (David)",
          f"rc={plain.returncode} out={plain.stdout!r} err={plain.stderr!r}")
    check("19: --supersedes fills 'Rewrites row <old id>'", sup.returncode == 0 and f"| Rewrites row {unit['id']} |" in sup.stdout,
          f"rc={sup.returncode} out={sup.stdout!r} err={sup.stderr!r}")


def test_case20_partially_superseded_rewrites_row_is_not_skipped() -> None:
    original = inv.sbc.superseded_targets
    inv.sbc.superseded_targets = lambda rows, rewrite_rows, errors: {X}  # X alone is superseded; the row below names X and W
    try:
        res = run(B13, A13, policy_doc(rewrites=[rw_row(f"{X} {W}", Y, who="PENDING (David)")], sbc_rows=[sbc_row(X_TXT, Z_TXT, sup=SUP)]))
    finally:
        inv.sbc.superseded_targets = original
    check("20: a Rewrites row naming one superseded and one live id is still processed (counted unsigned)",
          res["rows_missing_review"] >= 1, repr(res))


def test_case21_adr_status_is_strict() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        def adr(n: str, status_line: str) -> str:
            Path(d, f"{n}-x.md").write_text("---\nadr: \"" + n + "\"\n" + status_line + "\ndate: \"2026-10-28\"\n---\n\nBody.\n", encoding="utf-8")
            return f"ADR-{n}"
        want = {"proposed": "proposed", "accepted      # proposed | accepted | rejected": "accepted", "\"superseded\"": "superseded",
                "deprecated": "deprecated", "rejected": "rejected"}
        got = {k: sbc.adr_status(adr(f"{i:04d}", "status: " + k), d) for i, k in enumerate(want, 1)}
        check("21: known statuses parse, inline # comments and quotes are stripped",
              list(got.values()) == list(want.values()), repr(got))
        for i, bad in enumerate(["status:", "status: Accepted", "status: draft", "status: accepted proposed"], 10):
            n = adr(f"{i:04d}", bad)
            try:
                value = sbc.adr_status(n, d)
                check(f"21: {bad!r} is an error", False, f"returned {value!r}")
            except ValueError:
                check(f"21: {bad!r} is an error", True)
        Path(d, "0020-x.md").write_text("---\nadr: \"0020\"\n---\n\nBody.\n", encoding="utf-8")
        try:
            value = sbc.adr_status("ADR-0020", d)
            check("21: a missing status line is an error", False, f"returned {value!r}")
        except ValueError:
            check("21: a missing status line is an error", True)
    res = run(B1, A1, policy_doc(sbc_rows=[sbc_row(OLD_B, NEW_B)]), adr_status=lambda a: (_ for _ in ()).throw(ValueError("unknown status 'draft'")))
    check("21: a row whose ADR status is unreadable is a row error, not a crash or a silent pass",
          errs_with(res, "unknown status") and inv.exit_code(res) == 1, repr(res))


def sbc_doc_with(section_body: str) -> str:
    return policy_doc().replace("\n## Worktree finalize equivalence", "\n## Signed behavior changes\n\n" + section_body + "\n## Worktree finalize equivalence")


def test_case22_malformed_section_fails_loudly() -> None:
    good_row = sbc_row(OLD_B, NEW_B)
    sep = "|" + "|".join(["---"] * 8) + "|"
    typo = sbc.SBC_HEADER.replace("Supersedes", "Supercedes")
    cases = {
        "header typo": f"{typo}\n{sep}\n{good_row}\n",
        "missing separator row": f"{sbc.SBC_HEADER}\n{good_row}\n",
        "sub-heading inside the section": f"{sbc.SBC_HEADER}\n{sep}\n{good_row}\n\n### Notes\n\n{good_row}\n",
        "sub-heading before the table": f"### Notes\n\n{sbc.SBC_HEADER}\n{sep}\n{good_row}\n",
    }
    for name, body in cases.items():
        errors: list = []
        rows = sbc.parse_sbc_rows(sbc_doc_with(body), errors)
        res = run(B1, A1, sbc_doc_with(body))
        check(f"22: {name} -> a parse error and exit 1, never an empty parse",
              len(errors) == 1 and rows == [] and inv.exit_code(res) == 1 and res["row_errors"] != [], f"errors={errors!r} rows={rows!r} res={res!r}")


def main() -> int:
    print("test_rule_inventory_sbc: running")
    test_case1_row_types_for_a_guarded_rewrite()
    test_case2_signature_gates_the_exit_code_only()
    test_case3_texts_must_match_verbatim()
    test_case4_id_cells_and_duplicates()
    test_case5_adr_cell()
    test_case6_numbered_token_fence()
    test_case7_label_paragraph_rename_keeps_table_rows()
    test_case8_last_bullet_rewrite_keeps_following_paragraph()
    test_case9_the_row_never_widens_reach()
    test_case10_cell_escaping_round_trip()
    test_case11_additive_controls()
    test_case12_missing_section_and_switch()
    test_case13_supersede_accepted()
    test_case14_dangling_target_without_supersede()
    test_case15_supersede_of_an_unsigned_row()
    test_case16_supersede_of_an_unnamed_row()
    test_case17_superseded_row_deleted()
    test_case18_supersede_of_a_heading_row()
    test_case19_sbc_row_cli_prints_a_pending_row()
    test_case20_partially_superseded_rewrites_row_is_not_skipped()
    test_case21_adr_status_is_strict()
    test_case22_malformed_section_fails_loudly()
    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
