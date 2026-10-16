#!/usr/bin/env python3
"""Regression tests for the doubt-verify cycle 3 findings (P0 REM-FIX 7): B1 branch context survives fences and
continuation paragraphs, B2 every unit kind carries branch/label/anchor context, B3 block-scalar whitespace strictness,
B4 CODEOWNERS covers the corpus loader and util, plus A-3 (an HTML comment spanning a normative unit is a removal).
Run: python3 tests/fixtures/test_rule_inventory_doubt3.py

Fixture-level tests drive diff_inventory(); the e2e tests edit a scratch copy of the real SKILL.md and run the real CLI.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_rule_inventory_doubt as d  # noqa: E402  (shared fixtures; importing it runs nothing)

inv, K = d.inv, d.K
REPO = d.PLUGIN_ROOT.parents[3]

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


def lost(before: str, after: str) -> dict:
    return d.diff({K: before}, {K: after})


def is_lost(res: dict) -> bool:
    return d.failed(res) and res["unmapped_removed"] >= 1


# ------------------------------------------------------------------------------------------------ B1


def test_b1_fence_inside_branch_keeps_parent_chain() -> None:
    before = ("## Merge\n\n- If the merge failed:\n  ```bash\n  a\n  ```\n  - Do NOT delete the branch.\n"
              "- If the merge succeeded:\n  ```bash\n  b\n  ```\n  - Delete the branch.\n")
    after = ("## Merge\n\n- If the merge failed:\n  ```bash\n  a\n  ```\n  - Delete the branch.\n"
             "- If the merge succeeded:\n  ```bash\n  b\n  ```\n  - Do NOT delete the branch.\n")
    check("B1 control: unchanged branches with fences pass", not d.failed(lost(before, before)))
    res = lost(before, after)
    check("B1 children swapped between branches that each hold a fence is a lost occurrence", is_lost(res), repr(res))


def test_b1_continuation_paragraph_keeps_parent_chain() -> None:
    before = ("## Merge\n\n- If the merge failed:\n\n  Check the log first.\n\n  - Do NOT delete the branch.\n"
              "- If the merge succeeded:\n\n  Check the log first.\n\n  - Delete the branch.\n")
    after = ("## Merge\n\n- If the merge failed:\n\n  Check the log first.\n\n  - Delete the branch.\n"
             "- If the merge succeeded:\n\n  Check the log first.\n\n  - Do NOT delete the branch.\n")
    check("B1 control: unchanged branches with continuation paragraphs pass", not d.failed(lost(before, before)))
    res = lost(before, after)
    check("B1 children swapped between branches that each hold a continuation paragraph is a lost occurrence",
          is_lost(res), repr(res))


# ------------------------------------------------------------------------------------------------ B2


def test_b2_paragraph_swap_between_labels() -> None:
    before = "## Merge\n\n**If the merge failed:**\n\nDo NOT delete the branch.\n\n**If the merge succeeded:**\n\nDelete the branch.\n"
    after = "## Merge\n\n**If the merge failed:**\n\nDelete the branch.\n\n**If the merge succeeded:**\n\nDo NOT delete the branch.\n"
    check("B2 control: unchanged label paragraphs pass", not d.failed(lost(before, before)))
    res = lost(before, after)
    check("B2 rule paragraphs swapped between two label paragraphs is a lost occurrence", is_lost(res), repr(res))


def test_b2_table_row_swap_between_labels() -> None:
    def page(top: str, bottom: str) -> str:
        return ("## Gate\n\nWhen BUILD fails:\n\n| Step | Action |\n|---|---|\n| one | " + top + " |\n\n"
                "When BUILD passes:\n\n| Step | Action |\n|---|---|\n| one | " + bottom + " |\n")
    before, after = page("stop the line", "ship the build"), page("ship the build", "stop the line")
    check("B2 control: unchanged tables pass", not d.failed(lost(before, before)))
    res = lost(before, after)
    check("B2 table rows swapped between two tables with the same header is a lost occurrence", is_lost(res), repr(res))


def test_b2_fence_swap_under_different_items() -> None:
    def page(first: str, second: str) -> str:
        return ("## Finalize\n\n- Attempt the merge:\n  ```bash\n  " + first + "\n  ```\n"
                "- If it was already up to date:\n  ```bash\n  " + second + "\n  ```\n")
    before, after = page("git merge x", "copy-fallback y"), page("copy-fallback y", "git merge x")
    check("B2 control: unchanged fences pass", not d.failed(lost(before, before)))
    res = lost(before, after)
    check("B2 same-indent fences swapped under different list items is a lost occurrence", is_lost(res), repr(res))


def test_b2_fence_swap_under_anchors() -> None:
    def page(first: str, second: str) -> str:
        return ("## Finalize\n\nRun the merge step.\n\n```bash\n" + first + "\n```\n\n"
                "Run the cleanup step.\n\n```bash\n" + second + "\n```\n")
    before, after = page("git merge x", "rm -rf y"), page("rm -rf y", "git merge x")
    check("B2 control: unchanged anchored fences pass", not d.failed(lost(before, before)))
    res = lost(before, after)
    check("B2 sibling fences swapped across their preceding paragraphs is a lost occurrence", is_lost(res), repr(res))


def test_b2_units_record_branch_context() -> None:
    text = "## S\n\n- If failed:\n  ```bash\n  a\n  ```\n  - Child.\n"
    units = {u["kind"]: u for u in inv.extract_units(text, "x.md")}
    check("B2 a fence nested under a list item records that item as parent_ctx",
          len(units["fence"]["parent_ctx"]) == 1, repr(units["fence"]["parent_ctx"]))
    check("B2 a child list item after a fence keeps the item as parent_ctx", len(units["list_item"]["parent_ctx"]) >= 1)


# ------------------------------------------------------------------------------------------------ real SKILL.md e2e


def _lines_of(root: Path, rel: str = K) -> List[str]:
    return (root / rel).read_text(encoding="utf-8").split("\n")


def _fence_span(lines: List[str], needle: str) -> Tuple[int, int]:
    at = next(i for i, x in enumerate(lines) if needle in x)
    start = next(i for i in range(at, -1, -1) if lines[i].strip().startswith("```"))
    end = next(i for i in range(at, len(lines)) if lines[i].strip() == "```")
    return start, end


def _run(root: Path, lines: List[str], rel: str = K):
    (root / rel).write_text("\n".join(lines), encoding="utf-8")
    return d.real_diff(root)


def test_b1_real_skill_branch_delete_move_e2e() -> None:
    """P8 retarget (see test_r1_real_skill_4e_branch_swap_e2e): step 4's branch-delete items left SKILL.md, so the attack moves a real failure
    branch of `## Resolve Project Root` in the shared protocol: the `RESOLVE_EXIT != 0` item is lifted out from under step 1a and placed
    before the `TOPLEVEL_EXIT != 0` item one level up."""
    with tempfile.TemporaryDirectory() as tmp:
        root = d.scratch(tmp)
        control, _ = d.real_diff(root)
        lines = _lines_of(root, d.PROTO)
        a = next(i for i, x in enumerate(lines) if x.strip().startswith("- **If `RESOLVE_EXIT != 0`**"))
        b = next(i for i in range(a + 1, len(lines)) if lines[i].strip().startswith("- **Otherwise**, parse the outcome"))
        dest = next(i for i, x in enumerate(lines) if x.strip().startswith("- **If `TOPLEVEL_EXIT != 0`**"))
        seg = [x[2:] if x.startswith("  ") else x for x in lines[a:b]]
        moved = lines[:dest] + seg + lines[dest:a] + lines[b:]
        r, res = _run(root, moved, d.PROTO)
    check("B1 e2e control: the unmodified scratch copy passes", control.returncode == 0, control.stdout[-200:])
    check("B1 e2e: the `RESOLVE_EXIT != 0` failure item moved before the `TOPLEVEL_EXIT != 0` item fails closed",
          r.returncode == 1 and res.get("unmapped_removed", 0) >= 1, f"rc={r.returncode} {res} {r.stderr[-200:]}")


def test_b2_real_skill_fence_swap_e2e() -> None:
    """P8 retarget: the `git merge` / copy-fallback fences of step 4 left SKILL.md; the same fence swap runs on the two fences of
    `## Resolve Project Root` step 1 and 1a (`git rev-parse --show-toplevel` and the `CRAFTFLOW_INSTALL` resolver call)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = d.scratch(tmp)
        lines = _lines_of(root, d.PROTO)
        m0, m1 = _fence_span(lines, "PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)")
        c0, c1 = _fence_span(lines, "CRAFTFLOW_INSTALL=$(python3")
        swapped = lines[:m0] + lines[c0:c1 + 1] + lines[m1 + 1:c0] + lines[m0:m1 + 1] + lines[c1 + 1:]
        r, res = _run(root, swapped, d.PROTO)
    check("B2 e2e: the `git rev-parse` fence swapped with the resolver-call fence fails closed",
          r.returncode == 1 and res.get("unmapped_removed", 0) >= 1, f"rc={r.returncode} {res} {r.stderr[-200:]}")


# ------------------------------------------------------------------------------------------------ B3


def fm(body: str) -> str:
    return "---\n" + body + "\n---\n\n## A\n\nbody text\n"


def raises(text: str) -> str:
    try:
        inv.extract_units(text, "x.md")
    except ValueError as exc:
        return str(exc)
    return ""


def test_b3_block_scalar_whitespace_only_lines() -> None:
    a = raises(fm("name: x\ndescription: |\n    \n  real text\n  more text"))
    check("B3 a whitespace-only line before the first content line with more spaces than the indent is rejected",
          "line" in a and "whitespace" in a, repr(a))
    b = raises(fm("name: x\ndescription: |\n  real text\n\t\n  more text"))
    check("B3 a tab-only line inside a block scalar is rejected", "line" in b and "tab" in b, repr(b))
    ok = raises(fm("name: x\ndescription: |\n  \n  real text\n\n  more text\n  "))
    check("B3 control: whitespace-only lines no longer than the content indent, before and inside the block, pass",
          ok == "", repr(ok))


def test_b3_real_skill_whitespace_e2e() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = d.scratch(tmp)
        lines = _lines_of(root)
        at = next(i for i, x in enumerate(lines) if x.startswith("description: |"))
        ra, _ = _run(root, lines[:at + 1] + ["    "] + lines[at + 1:])
        blank = next(i for i in range(at + 1, len(lines)) if not lines[i].strip())
        rb, _ = _run(root, lines[:blank] + ["\t"] + lines[blank + 1:])
    check("B3 e2e (a): 4 spaces right after `description: |` makes the real --diff fail", ra.returncode == 1, ra.stdout[-200:])
    check("B3 e2e (b): a tab-only line inside the real description makes --diff fail", rb.returncode == 1, rb.stdout[-200:])


# ------------------------------------------------------------------------------------------------ B4


def test_b4_codeowners_cover_corpus_and_util() -> None:
    text = (REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    owned = [x.split()[0] for x in text.splitlines() if x.strip() and not x.startswith("#")]
    base = "tools/craftflow-plugin/plugins/craftflow/scripts/"
    for name in ("craftflow_router_corpus.py", "craftflow_router_util.py", "craftflow_rule_inventory_frontmatter.py"):
        check(f"B4 CODEOWNERS owns {name}", base + name in owned or (base + "craftflow_rule_inventory*.py" in owned
                                                                       and name.startswith("craftflow_rule_inventory")),
              repr(owned))


def test_b4_after_corpus_is_the_working_tree() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = d.scratch(tmp)
        control, base = d.real_diff(root)
        lines = _lines_of(root)
        at = next(i for i, x in enumerate(lines) if x.startswith("## 14. Hard Rules"))
        r, res = _run(root, lines[:at] + lines[at + 1:])
    check("B4 sentinel: control present plus policy-mapped deletions plus signed rewrites equals the baseline occurrence count",
          control.returncode == 0
          and base.get("present", 0) + base.get("policy_mapped", 0) + base.get("reworded", 0) == base.get("baseline_occurrences"),
          repr(base))
    check("B4 sentinel: a scratch edit to a corpus file changes `present` (the after corpus is the working tree)",
          res.get("present", 0) < base.get("present", 0) or r.returncode == 1, f"{res.get('present')} vs {base.get('present')}")


# ------------------------------------------------------------------------------------------------ A-3


def test_a3_html_comment_spanning_a_unit_is_a_removal() -> None:
    before = "## Hard Rules\n\n- Never skip the gate.\n\nAfter.\n"
    after = "<!--\n## Hard Rules\n\n- Never skip the gate.\n\n-->\n\nAfter.\n"
    res = lost(before, after)
    check("A-3 a multi-line HTML comment wrapped around a rule is a removal", is_lost(res), repr(res))
    msg = raises("## A\n\n<!-- never closed\n\n- Never skip the gate.\n")
    check("A-3 an unclosed HTML comment fails closed", "comment" in msg, repr(msg))
    inline = lost(before, "## Hard Rules\n\n- Never skip the gate. <!-- why -->\n\nAfter.\n")
    check("A-3 control: a single-line comment is not a span", "comment" not in " ".join(inline["row_errors"]), repr(inline))


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
