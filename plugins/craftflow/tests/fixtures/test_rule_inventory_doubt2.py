#!/usr/bin/env python3
"""Regression tests for the doubt-verify cycle 2 findings (P0 REM-FIX 6): R1 branch-context identity, F1/F2/F3/F4/F5/F6
strict frontmatter, AQ applicability quote rule, GT guarded tokens, BO bigram order and HD heading text.
Run: python3 tests/fixtures/test_rule_inventory_doubt2.py

Each test reproduces the attack and asserts the net fails closed (RED on a879cca2). Fixture-level tests drive
diff_inventory(); the e2e test edits a scratch copy of the real SKILL.md and runs the real CLI.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_rule_inventory_doubt as d  # noqa: E402  (shared fixtures; importing it runs nothing)

inv = d.inv
K, FP, RR, BW, WHO = d.K, d.FP, d.RR, d.BW, d.WHO

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


def rw(old: str, new: str, kind: str, who: str = WHO) -> str:
    return f"| {old} | {new} | {kind} | moved | same rule | {who} |"


def failed(res: dict) -> bool:
    return inv.exit_code(res) == 1


def errs(res: dict, word: str) -> bool:
    return any(word in e for e in res["row_errors"])


# ------------------------------------------------------------------------------------------------ R1

MERGE_BEFORE = ("## Merge\n\n- **If the merge failed** (exit non-zero).\n  - Do NOT delete the branch.\n"
                "- **If the merge succeeded**.\n  - Delete the branch.\n")
MERGE_SWAPPED = ("## Merge\n\n- **If the merge failed** (exit non-zero).\n  - Delete the branch.\n"
                 "- **If the merge succeeded**.\n  - Do NOT delete the branch.\n")


def test_r1_minimal_children_swapped_between_branches() -> None:
    res = d.diff({K: MERGE_BEFORE}, {K: MERGE_SWAPPED})
    check("R1 children swapped between two `.`-terminated condition parents is a lost occurrence",
          failed(res) and res["unmapped_removed"] >= 1, repr(res))
    ok = d.diff({K: MERGE_BEFORE}, {K: MERGE_BEFORE})
    check("R1 control: the unchanged branches pass", not failed(ok) and ok["unmapped_removed"] == 0, repr(ok))
    texts = [u for u in inv.extract_units(MERGE_BEFORE, "x.md") if u["kind"] == "list_item"]
    check("R1 a list item records its ancestor chain as parent_ctx (empty for the top-level item)",
          all("parent_ctx" in u for u in texts) and texts[0]["parent_ctx"] == [] and len(texts[1]["parent_ctx"]) == 1,
          repr([u.get("parent_ctx") for u in texts]))


def test_r1_intro_paragraph_not_ending_in_colon() -> None:
    before = ("## Merge\n\nIf the merge failed, do this.\n\n- Do NOT delete the branch.\n\n"
              "If the merge succeeded, do this.\n\n- Delete the branch.\n")
    after = ("## Merge\n\nIf the merge failed, do this.\n\n- Delete the branch.\n\n"
             "If the merge succeeded, do this.\n\n- Do NOT delete the branch.\n")
    res = d.diff({K: before}, {K: after})
    check("R1 lists swapped between intro paragraphs that do not end in a colon is a lost occurrence",
          failed(res) and res["unmapped_removed"] >= 1, repr(res))


def test_r1_real_skill_4e_branch_swap_e2e() -> None:
    head = "      - **If `COPY_FALLBACK_EXIT != 0`**"
    tail = "      - **If `COPY_FALLBACK_EXIT == 0`**"
    stop = "      - **If `MERGE_EXIT == 0` AND"
    with tempfile.TemporaryDirectory() as tmp:
        root = d.scratch(tmp)
        control, _ = d.real_diff(root)
        text = (root / K).read_text(encoding="utf-8")
        a, b = text.index(head), text.index(tail)
        c = text.index(stop, b)
        success = text[b:c]
        children_start = text.index("        - Release the lock", a)
        swapped = text[:children_start] + success + text[children_start:b] + text[c:]
        (root / K).write_text(swapped, encoding="utf-8")
        r, res = d.real_diff(root)
    check("R1 control: the unmodified scratch copy passes", control.returncode == 0, control.stdout[-200:] + control.stderr[-200:])
    check("R1 e2e: SKILL.md 4e `COPY_FALLBACK_EXIT == 0` item moved above the failure children fails closed",
          r.returncode == 1 and res.get("unmapped_removed", 0) >= 1, f"rc={r.returncode} {res} {r.stderr[-200:]}")


# ------------------------------------------------------------------------------------------------ F


def fm(body: str) -> str:
    return "---\n" + body + "\n---\n\n## A\n\nbody text\n"


def raises(text: str) -> str:
    try:
        inv.extract_units(text, "x.md")
    except ValueError as exc:
        return str(exc)
    return ""


def test_f3_block_scalar_indent_must_be_consistent() -> None:
    msg = raises(fm("name: x\ndescription: |\n    THE ONLY ENTRY\n  rest of it"))
    check("F3 a block scalar whose later lines are indented less than its first line is rejected",
          "frontmatter" in msg, repr(msg))
    ok = raises(fm("name: x\ndescription: |\n  THE ONLY ENTRY\n    nested deeper\n  rest of it"))
    check("F3 control: a deeper continuation line is fine", ok == "", repr(ok))


def test_f1_duplicate_key_rejected() -> None:
    msg = raises(fm("name: x\ndescription: first\ndescription: second"))
    check("F1 a duplicate `description:` key is rejected", "frontmatter" in msg and "duplicate" in msg, repr(msg))


def test_f_unknown_key_rejected_and_listed() -> None:
    msg = raises(fm("name: x\ndescription: hi\nbogus-key: 1\nother: 2"))
    check("F unknown frontmatter keys are rejected and named", "bogus-key" in msg and "other" in msg, repr(msg))


def test_f2_f6_whole_frontmatter_is_a_unit() -> None:
    base = fm("name: craftflow-router\ndescription: hi there")
    units = inv.extract_units(base, "x.md")
    check("F2/F6 the whole frontmatter is one unit besides the description unit",
          sorted(u["kind"] for u in units if u["kind"].startswith("frontmatter")) == ["frontmatter", "frontmatter_keys"],
          repr([u["kind"] for u in units]))
    flag = d.diff({K: base}, {K: fm("name: craftflow-router\ndescription: hi there\ndisable-model-invocation: true")})
    check("F2 adding `disable-model-invocation: true` is a lost frontmatter unit", failed(flag) and flag["unmapped_removed"] >= 1, repr(flag))
    renamed = d.diff({K: base}, {K: fm("name: craftflow-router-x\ndescription: hi there")})
    check("F6 renaming the skill (`name:`) is a lost frontmatter unit", failed(renamed) and renamed["unmapped_removed"] >= 1, repr(renamed))
    same = d.diff({K: base}, {K: fm("description: hi there\nname: craftflow-router")})
    check("F control: reordering the keys is not a change", not failed(same), repr(same))


def test_f4_folded_block_scalar_passes() -> None:
    msg = raises(fm("name: x\ndescription: >\n  folded line one\n  folded line two"))
    check("F4 a `>` folded description passes", msg == "", repr(msg))


def test_f5_second_yaml_block_is_rejected() -> None:
    msg = raises("---\nname: x\ndescription: hi\n---\n---\ndisable-model-invocation: true\n---\n\n## A\n\nbody\n")
    check("F5 a second `---` block after the frontmatter is rejected", msg != "", "no raise")


# ------------------------------------------------------------------------------------------------ AQ

HARD = "## Hard Rules\n\n- Never skip the review gate.\n- Never ship the plan unreviewed.\n"
SECTION = f"build-workflow.md#hard rules"


def aq(*rows: str) -> dict:
    return d.diff({BW: HARD}, {RR: HARD}, applicability=list(rows))


def test_aq_single_character_quote_is_rejected() -> None:
    row = f"| {SECTION} | BUILD | BUILD@conditional_stage | e | {WHO} |"
    res = aq(row)
    check("AQ a section row quoting `e` no longer lowers the reach of every Hard Rules bullet",
          failed(res) and errs(res, "quote"), repr(res))
    one_word = aq(f"| {SECTION} | BUILD | BUILD@conditional_stage | \"Never\" | {WHO} |")
    check("AQ a one-word quote is rejected", failed(one_word) and errs(one_word, "quote"), repr(one_word))


def test_aq_per_rule_two_word_quote_passes() -> None:
    rows = [f"| {uid('- Never skip the review gate.')} | BUILD | BUILD@conditional_stage | \"review gate\" | {WHO} |",
            f"| {uid('- Never ship the plan unreviewed.')} | BUILD | BUILD@conditional_stage | \"ship the plan\" | {WHO} |"]
    res = aq(*rows)
    check("AQ one row per rule with a genuine two-word scope quote passes", not failed(res), repr(res))


def test_aq_section_row_must_quote_every_targeted_rule() -> None:
    res = aq(f"| {SECTION} | BUILD | BUILD@conditional_stage | \"review gate\" | {WHO} |")
    check("AQ a section row whose quote is in only one of its rules fails", failed(res) and errs(res, "quote"), repr(res))
    both = d.diff({BW: "## Hard Rules\n\n- Never skip the review gate now.\n- Never skip the review gate later.\n"},
                  {RR: "## Hard Rules\n\n- Never skip the review gate now.\n- Never skip the review gate later.\n"},
                  applicability=[f"| {SECTION} | BUILD | BUILD@conditional_stage | \"review gate\" | {WHO} |"])
    check("AQ control: a section row whose quote is in every targeted rule passes", not failed(both), repr(both))


# ------------------------------------------------------------------------------------------------ GT


def one_rule(old: str, new: str, kind: str = "dedupe") -> dict:
    return d.diff({K: "## G\n\n- " + old + "\n"}, {K: "## G\n\n- " + new + "\n"}, rewrites=[rw(uid(old), uid(new), kind)])


def test_gt_guarded_words_and_numbers_are_two_way() -> None:
    cases = {
        "3 -> 30": ("Retry the failed task up to 3 times before giving up on the workflow.",
                    "Retry the failed task up to 30 times before giving up on the workflow."),
        "required -> optional": ("The reviewer sign-off is required before the merge proceeds today.",
                                 "The reviewer sign-off is optional before the merge proceeds today."),
        "before -> after": ("Release the lock before removing the worktree directory for the task.",
                            "Release the lock after removing the worktree directory for the task."),
        ">= -> >": ("Advance only when the count is >= 3 across all of the checks run.",
                    "Advance only when the count is > 3 across all of the checks run."),
        "fail closed -> fail open": ("The gate must fail closed when the config file is missing entirely.",
                                     "The gate must fail open when the config file is missing entirely."),
    }
    for name, (old, new) in cases.items():
        res = one_rule(old, new)
        check(f"GT {name} is rejected as a dedupe", failed(res) and errs(res, "relates ids"), repr(res))
    old = "Retry the failed task up to 3 times before giving up on the workflow."
    ok = one_rule(old, "Retry the failed task up to 3 times before giving up on the whole workflow.")
    check("GT control: a reword with identical guarded tokens passes", not failed(ok), repr(ok))


# ------------------------------------------------------------------------------------------------ BO

SWAP_OLD = "Builder writes tests while reviewer reads code before planner approves design."
SWAP_NEW = "Planner writes code while builder reads design before reviewer approves tests."


def test_bo_subject_object_swap_is_rejected() -> None:
    for kind in ("dedupe", "split", "literal-to-key"):
        res = one_rule(SWAP_OLD, SWAP_NEW, kind)
        check(f"BO a subject/object swap as {kind} is rejected by the bigram order check", failed(res) and errs(res, "relates ids"), repr(res))
    grp = d.diff({K: "## G\n\n- " + SWAP_OLD + "\n"}, {K: "## G\n\n- " + SWAP_NEW + "\n"},
                 rewrites=[rw(uid(SWAP_OLD), uid(SWAP_NEW), "group")])
    check("BO a subject/object swap as a group row is rejected", failed(grp) and errs(grp, "group row"), repr(grp))
    reflow = "Builder writes tests while the reviewer reads code before the planner approves design."
    ok = one_rule(SWAP_OLD, reflow)
    check("BO control: a reflow that keeps at least half of the bigrams passes", not failed(ok), repr(ok))


# ------------------------------------------------------------------------------------------------ HD


def test_hd_heading_row_text_is_checked() -> None:
    before = "## Hard Rules\n\n- Never skip the gate.\n"
    soft = "Soft Suggestions (ignore when inconvenient)"
    bad = d.diff({K: before}, {K: f"## {soft}\n\n- Never skip the gate.\n"},
                 rewrites=[f"| {K}#hard rules | {K}#{soft.lower()} | heading | renamed | same | {WHO} |"])
    check("HD a heading row rewording `Hard Rules` into `Soft Suggestions (...)` is rejected",
          failed(bad) and errs(bad, "keeps under 60%"), repr(bad))
    ok = d.diff({K: before}, {K: "## Hard Rules And Notes\n\n- Never skip the gate.\n"},
                rewrites=[f"| {K}#hard rules | {K}#hard rules and notes | heading | renamed | same | {WHO} |"])
    check("HD control: a rename that keeps the old words passes", not failed(ok), repr(ok))
    guard = d.diff({K: "## Never Skip\n\n- Never skip the gate.\n"}, {K: "## Skip Gate Always\n\n- Never skip the gate.\n"},
                   rewrites=[f"| {K}#never skip | {K}#skip gate always | heading | renamed | same | {WHO} |"])
    check("HD a heading rename that drops `never` and adds `always` is rejected", failed(guard) and errs(guard, "heading"), repr(guard))


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
