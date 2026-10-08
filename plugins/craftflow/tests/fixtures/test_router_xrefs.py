#!/usr/bin/env python3
"""Tests for craftflow_router_corpus.py (corpus loader and cross-reference resolver).

Run: python3 tests/fixtures/test_router_xrefs.py
Covers DD-14 / BC-10 and plan cases E67-E70h, plus the A2 ambiguous-heading rule.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_router_corpus as cor  # noqa: E402

FIXTURES = PLUGIN_ROOT / "tests" / "fixtures"
ALLOWLIST = FIXTURES / "router-xref-allowlist.json"
MANUAL = FIXTURES / "router-xref-manual-review.json"
BUDGET = FIXTURES / "router-budget.json"

_passes = 0
_skips = 0
_errors: list = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def skip(name: str, reason: str) -> None:
    """A skipped check is not a pass: it is counted separately and printed in the summary line."""
    global _skips
    _skips += 1
    print(f"  SKIP: {name}: {reason}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def check(name: str, cond: bool, reason: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, reason or "assertion false")


SK = "skills/craftflow-router/SKILL.md"
FPM = "skills/craftflow-router/references/fast-path.md"
BW = "skills/craftflow-router/references/build-workflow.md"
HSC = "skills/craftflow-router/references/harness-self-checks.md"
AP = "skills/craftflow-router/references/workflow-artifact-and-hook-policy.md"
PR = "skills/_shared/router-protocol.md"
CUR = "skills/cursor-router/SKILL.md"
DV = "agents/doubt-verifier.md"
PROTO_PATH = "tools/craftflow-plugin/plugins/craftflow/skills/_shared/router-protocol.md"


def verdict(text: str, corpus: dict, file: str = SK) -> list:
    return [cor.resolve(x, corpus) for x in cor.find_xrefs(text, file)]


def test_e67_gate_table_resolves_and_removal_fails() -> None:
    text = "Scan per `references/fast-path.md → Gate Table` before dispatch.\n"
    with_t = verdict(text, {SK: text, FPM: "#### Gate Table\n\nrows\n"})
    without = verdict(text, {SK: text, FPM: "#### Other Table\n\nrows\n"})
    check("e67_gate_table_resolves_and_removal_fails", with_t == [True] and without == [False], f"{with_t} {without}")


def test_e68_bare_numbered_ref_resolves_in_own_file() -> None:
    text = "see (`## 8. Post-Agent Validation → Contract overrides`) for the rule.\n"
    skill = "## 8. Post-Agent Validation\n\n### Contract overrides\n\ntext\n"
    in_skill = verdict(text, {SK: skill}, SK)
    in_other = verdict(text, {SK: skill, BW: text}, BW)
    fixed = "see SKILL.md's `## 8. Post-Agent Validation → Contract overrides` for the rule.\n"
    named = verdict(fixed, {SK: skill, BW: fixed}, BW)
    sub_missing = verdict(text, {SK: "## 8. Post-Agent Validation\n\ntext\n"}, SK)
    check("e68_bare_numbered_ref_resolves_in_own_file",
          in_skill == [True] and in_other == [False] and named == [True] and sub_missing == [False],
          f"{in_skill} {in_other} {named} {sub_missing}")


def test_e69_duplicate_headings_resolve_by_named_file() -> None:
    text = "Read `references/build-workflow.md → ### BUILD preparation` first.\n"
    both = {SK: "### BUILD preparation\n", BW: "### BUILD preparation\n"}
    only_skill = {SK: "### BUILD preparation\n", BW: "### Other\n"}
    check("e69_duplicate_headings_resolve_by_named_file",
          verdict(text, both) == [True] and verdict(text, only_skill) == [False],
          f"{verdict(text, both)} {verdict(text, only_skill)}")


def test_e70_allowlist_blocks_new_danglers_and_growth() -> None:
    bad = cor.find_xrefs("See `references/fast-path.md → Nope Table` here.\n", SK)
    entry = {"file": SK, "line": 1, "raw": bad[0].raw, "target": bad[0].target_file, "reason": "fixture"}
    unmatched_empty = cor.diff_allowlist(bad, [])
    unmatched_listed = cor.diff_allowlist(bad, [entry])
    grown = len([entry, entry]) > len([entry])
    check("e70_allowlist_blocks_new_danglers_and_growth",
          len(unmatched_empty) == 1 and unmatched_listed == [] and grown, f"{unmatched_empty} {unmatched_listed}")


def test_e70b_wrapped_reference() -> None:
    text = f"canonical text lives in\n`{PROTO_PATH}` §\n\"Previous Agent Findings Handoff\". DEBUG skips hunter findings.\n"
    xs = cor.find_xrefs(text, SK)
    good = verdict(text, {SK: text, PR: "## Previous Agent Findings Handoff\n"})
    bad = verdict(text, {SK: text, PR: "## Something Else\n"})
    check("e70b_wrapped_reference", len(xs) == 1 and xs[0].line == 2 and good == [True] and bad == [False],
          f"{[(x.line, x.heading) for x in xs]} {good} {bad}")


def test_e70c_heading_split_inside_quotes() -> None:
    text = f"lives in `{PROTO_PATH}` § \"Dispatch\nPrompt Scaffold\" (full field list).\n"
    xs = cor.find_xrefs(text, SK)
    good = verdict(text, {PR: "## Dispatch Prompt Scaffold\n"})
    check("e70c_heading_split_inside_quotes",
          len(xs) == 1 and xs[0].heading == "dispatch prompt scaffold" and good == [True], f"{xs} {good}")


def test_e70d_cursor_shared_doc_alias() -> None:
    text = "- **Fast path:** omit the section, same logic given in the shared doc's\n  § \"Verifier Findings Handoff\".\n"
    xs = cor.find_xrefs(text, CUR)
    good = verdict(text, {CUR: text, PR: "## Verifier Findings Handoff\n"}, CUR)
    moved = verdict(text, {CUR: text, PR: "## Other\n", "skills/_shared/verifier-handoff.md": "## Verifier Findings Handoff\n"}, CUR)
    check("e70d_cursor_shared_doc_alias",
          len(xs) == 1 and xs[0].target_file.endswith("router-protocol.md") and good == [True] and moved == [False],
          f"{xs} {good} {moved}")


def test_e70e_backticked_section_sign() -> None:
    text = "(`build-workflow.md` § `### BUILD task graph`), and REVIEW (`review-workflow.md` §\n`### REVIEW task graph`).\n"
    corpus = {HSC: text, BW: "### BUILD task graph\n", "skills/craftflow-router/references/review-workflow.md": "### REVIEW task graph\n"}
    xs = cor.find_xrefs(text, HSC)
    check("e70e_backticked_section_sign", len(xs) == 2 and verdict(text, corpus, HSC) == [True, True], f"{xs}")


def test_e70f_plugin_root_paths_and_suffixed_heading() -> None:
    text = ("canonical in `skills/craftflow-router/SKILL.md` → \"### Worktree Isolation (BUILD Default)\" step 4; also\n"
            "(see `agents/doubt-verifier.md → ## Fix-Verify Contract`):\n")
    corpus = {AP: text, SK: "### Worktree Isolation (BUILD Default)\n", DV: "## Fix-Verify Contract (phase:fix-verify)\n"}
    xs = cor.find_xrefs(text, AP)
    miss = verdict(text, {AP: text, SK: "### Other\n", DV: "## Fix-Verify Contract (phase:fix-verify)\n"}, AP)
    check("e70f_plugin_root_paths_and_suffixed_heading",
          len(xs) == 2 and verdict(text, corpus, AP) == [True, True] and miss == [False, True], f"{xs} {miss}")


def test_e70g_in_that_file_refs() -> None:
    text = ("- Immediately read `references/build-workflow.md`.\n"
            "- Use the `### BUILD preparation` and `### BUILD task graph` blocks in that file as the canonical law.\n")
    xs = cor.find_xrefs(text, SK)
    corpus = {SK: text, BW: "### BUILD preparation\n\n### BUILD task graph\n"}
    check("e70g_in_that_file_refs",
          len(xs) == 2 and all(x.target_file.endswith("build-workflow.md") for x in xs) and verdict(text, corpus) == [True, True],
          f"{[(x.target_file, x.heading) for x in xs]}")


def test_e70h_fenced_blocks_ignored() -> None:
    text = "```\nsee § \"Nope\" and `references/fast-path.md → Nope`\n```\n"
    check("e70h_fenced_blocks_ignored", cor.find_xrefs(text, SK) == [], repr(cor.find_xrefs(text, SK)))


def test_find_section_ambiguous_raises() -> None:
    text = "### BUILD preparation\n\none\n\n### BUILD preparation\n\ntwo\n"
    try:
        cor.find_section(text, "### BUILD preparation")
    except cor.AmbiguousHeading:
        ok("find_section_ambiguous_raises")
        return
    fail("find_section_ambiguous_raises", "no AmbiguousHeading")


def test_section_any_ambiguous_without_hint_raises() -> None:
    corpus = cor.read_corpus(PLUGIN_ROOT)
    try:
        cor.section_any("### BUILD preparation", corpus=corpus)
    except cor.AmbiguousHeading:
        ok("section_any_ambiguous_without_hint_raises")
        return
    fail("section_any_ambiguous_without_hint_raises", "no AmbiguousHeading (SKILL.md and build-workflow.md both have it)")


def test_section_any_with_hint_resolves() -> None:
    corpus = cor.read_corpus(PLUGIN_ROOT)
    f1, s1 = cor.section_any("### BUILD preparation", file_hint="build-workflow.md", corpus=corpus)
    f2, s2 = cor.section_any("### BUILD preparation", file_hint="SKILL.md", corpus=corpus)
    check("section_any_with_hint_resolves",
          f1.endswith("references/build-workflow.md") and f2.endswith("craftflow-router/SKILL.md")
          and s1.startswith("### BUILD preparation") and s1 != s2, f"{f1} {f2}")


def test_runtime_files_sorted_and_excludes_maintainer() -> None:
    files = cor.runtime_files(PLUGIN_ROOT)
    readmap = {"files": [{"file": "skills/craftflow-router/references/context-boundary.md", "class": "maintainer",
                          "workflows": []}]}
    less = cor.runtime_files(PLUGIN_ROOT, readmap=readmap)
    check("runtime_files_sorted_and_excludes_maintainer",
          files == sorted(files) and "skills/craftflow-router/SKILL.md" in files and "skills/_shared/router-protocol.md" in files
          and "skills/craftflow-router/references/context-boundary.md" not in less and len(less) == len(files) - 1,
          f"{len(files)} {len(less)}")


def test_read_corpus_from_ref_matches_tree() -> None:
    head = subprocess.run(["git", "-C", str(PLUGIN_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.returncode != 0:
        ok("read_corpus_from_ref_matches_tree (skip: no git)")
        return
    from_ref = cor.read_corpus(PLUGIN_ROOT, ref=head.stdout.strip())
    check("read_corpus_from_ref_matches_tree",
          set(from_ref) == set(cor.read_corpus(PLUGIN_ROOT)) and bool(from_ref[SK]), f"{sorted(from_ref)[:3]}")


def test_corpus_text_separators() -> None:
    got = cor.corpus_text({"b.md": "B", "a.md": "A"})
    check("corpus_text_separators", got == "\n<!-- file: a.md -->\nA\n<!-- file: b.md -->\nB", repr(got))


def test_skill_descriptions_block_scalar() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "skills" / "demo"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: demo\ndescription: |\n  Line one.\n\n  Line two.\n---\nbody\n")
        e = Path(tmp) / "skills" / "plain"
        e.mkdir()
        (e / "SKILL.md").write_text("---\nname: plain\ndescription: \"A quoted one\"\n---\n")
        got = cor.skill_descriptions(Path(tmp))
    check("skill_descriptions_block_scalar", got == {"demo": "Line one.\n\nLine two.", "plain": "A quoted one"}, repr(got))


def test_manual_review_sites_reviewed() -> None:
    data = json.loads(MANUAL.read_text(encoding="utf-8"))
    merged = []
    if BUDGET.exists():
        merged = json.loads(BUDGET.read_text(encoding="utf-8")).get("merged_phases", [])
    corpus = cor.read_corpus(PLUGIN_ROOT)
    problems = []
    for e in data["entries"]:
        if e["owning_phase"] in merged and not (e["outcome"] and e["reviewed_by"]):
            problems.append(f"unreviewed after owning phase merged: {e['anchor_text'][:40]}")
        if not e["outcome"]:
            flat = " ".join(corpus.get(e["file"], "").split())
            if " ".join(e["anchor_text"].split()) not in flat:
                problems.append(f"anchor moved before review: {e['anchor_text'][:40]}")
    check("manual_review_sites_reviewed", len(data["entries"]) >= 9 and not problems, "; ".join(problems))


KNOWN = [
    (SK, "intent routing", "router-protocol.md", 1),
    (SK, "resolve project root", "router-protocol.md", 1),
    (SK, "memory file required sections", "router-protocol.md", 1),
    (SK, "explicit dispatcher (phase-to-agent table)", "router-protocol.md", 1),
    (SK, "dispatch prompt scaffold", "router-protocol.md", 2),
    (SK, "previous agent findings handoff", "router-protocol.md", 1),
    (SK, "agent verdict headings", "router-protocol.md", 1),
    (SK, "skill-distill approval flow", "router-protocol.md", 1),
    (SK, "verifier findings handoff", "router-protocol.md", 1),
    (SK, "build preparation", "build-workflow.md", 1),
    (SK, "build task graph", "build-workflow.md", 2),
    (SK, "fast path escalation", "build-workflow.md", 1),
    (SK, "gate table", "fast-path.md", 1),
    (SK, "fix_verify_keyword_scan — keyword table", "fast-path.md", 1),
    (SK, "risk_keyword_scan — keyword table", "fast-path.md", 1),
    (SK, "fix-verify contract", "agents/doubt-verifier.md", 1),
    (SK, "8. post-agent validation", "SKILL.md", 2),
    (AP, "worktree isolation (build default)", "skills/craftflow-router/SKILL.md", 1),
    (HSC, "build task graph", "build-workflow.md", 1),
    (HSC, "fast path escalation", "build-workflow.md", 1),
    (HSC, "review task graph", "review-workflow.md", 1),
    (HSC, "dispatch prompt scaffold", "router-protocol.md", 1),
    (CUR, "resolve project root", "router-protocol.md", 1),
    (CUR, "dispatch prompt scaffold", "router-protocol.md", 1),
    (CUR, "previous agent findings handoff", "router-protocol.md", 1),
    (CUR, "verifier findings handoff", "router-protocol.md", 1),
    (CUR, "skill-distill approval flow", "router-protocol.md", 1),
]


def test_known_sites_are_parsed() -> None:
    xs = cor.all_xrefs(PLUGIN_ROOT)
    missing = []
    for file, heading, target, minimum in KNOWN:
        n = sum(1 for x in xs if x.file == file and x.heading == heading and x.target_file.endswith(target))
        if n < minimum:
            missing.append(f"{Path(file).name}:{heading}:{target} have {n} need {minimum}")
    check("known_sites_are_parsed", not missing, "; ".join(missing))


def test_cursor_router_shared_refs_checked() -> None:
    xs = [x for x in cor.all_xrefs(PLUGIN_ROOT) if x.file == CUR and "_shared/" in x.target_file or
          (x.file == CUR and x.target_file.endswith("router-protocol.md"))]
    corpus = cor.read_corpus(PLUGIN_ROOT)
    bad = [x.raw for x in xs if not cor.resolve(x, corpus, loader=cor.plugin_loader(PLUGIN_ROOT))]
    check("cursor_router_shared_refs_checked", len(xs) >= 5 and not bad, f"{len(xs)} refs; unresolved {bad}")


def test_allowlist_only_shrinks() -> None:
    rel = "tools/craftflow-plugin/plugins/craftflow/tests/fixtures/router-xref-allowlist.json"
    shown = subprocess.run(["git", "-C", str(PLUGIN_ROOT), "show", f"origin/main:{rel}"], capture_output=True, text=True)
    if shown.returncode != 0:
        skip("allowlist_only_shrinks", "no origin/main in this clone")
        return
    origin = json.loads(shown.stdout)["entries"]
    now = json.loads(ALLOWLIST.read_text(encoding="utf-8"))["entries"]
    check("allowlist_only_shrinks", len(now) <= len(origin), f"{len(now)} > {len(origin)}")


def test_corpus_xrefs_resolve() -> None:
    xs = cor.all_xrefs(PLUGIN_ROOT)
    corpus = cor.read_corpus(PLUGIN_ROOT)
    loader = cor.plugin_loader(PLUGIN_ROOT)
    unresolved = [x for x in xs if not cor.resolve(x, corpus, loader=loader)]
    entries = json.loads(ALLOWLIST.read_text(encoding="utf-8"))["entries"]
    unmatched = cor.diff_allowlist(unresolved, entries)
    stale = cor.stale_allowlist(unresolved, entries)
    check("corpus_xrefs_resolve", not unmatched, "; ".join(f"{x.file}:{x.line} {x.raw}" for x in unmatched[:10]))
    check("corpus_allowlist_has_no_stale_entries", not stale, "; ".join(f"{e['file']} {e['raw']}" for e in stale[:10]))
    print(f"  xrefs={len(xs)} unresolved={len(unresolved)} allowlist={len(entries)}")


def test_h_m2_ambiguous_target_heading_is_unresolved() -> None:
    text = "See `references/build-workflow.md → ### BUILD preparation` first.\n"
    dup = {SK: text, BW: "### BUILD preparation\n\na\n\n### BUILD preparation\n\nb\n"}
    one = {SK: text, BW: "### BUILD preparation\n\na\n"}
    xs = cor.find_xrefs(text, SK)
    check("H-M2 duplicate target headings are ambiguous, not resolved",
          cor.resolution(xs[0], dup) == "ambiguous" and not cor.resolve(xs[0], dup) and cor.resolve(xs[0], one),
          f"{cor.resolution(xs[0], dup)} {cor.resolve(xs[0], one)}")


def test_m2_numbered_and_parenthetical_ambiguity() -> None:
    num = cor.Xref(SK, 1, BW, "2. foo", "`2. Foo`", num="2")
    two = {BW: "## 2. Foo\n\na\n\n## 2. Foo\n\nb\n"}
    one = {BW: "## 2. Foo\n\na\n\n## 3. Bar\n\nb\n"}
    check("M2 two identical numbered headings are ambiguous, not ok",
          cor.resolution(num, two) == "ambiguous" and cor.resolution(num, one) == "ok",
          f"{cor.resolution(num, two)} {cor.resolution(num, one)}")
    sub = cor.Xref(SK, 1, BW, "2. foo", "`2. Foo -> Step`", sub="step", num="2")
    check("M2 numbered ambiguity is reported even with a sub-heading",
          cor.resolution(sub, {BW: two[BW] + "### Step\n"}) == "ambiguous", cor.resolution(sub, {BW: two[BW] + "### Step\n"}))
    bare = cor.Xref(SK, 1, BW, "2.", "`2.`", num="2")
    check("M2 bare number matching two headings is ambiguous",
          cor.resolution(bare, {BW: "## 2. Foo\n\na\n\n## 2. Bar\n\nb\n"}) == "ambiguous")
    plain = cor.Xref(SK, 1, BW, "bar", "`bar`")
    paren = {BW: "## Bar (x)\n\na\n\n## Bar (y)\n\nb\n"}
    check("M2 two parenthetical variants of one heading are ambiguous", cor.resolution(plain, paren) == "ambiguous",
          cor.resolution(plain, paren))
    check("M2 one parenthetical variant still resolves", cor.resolution(plain, {BW: "## Bar (x)\n\na\n"}) == "ok")


def test_h_m3_allowlist_keyed_by_target_and_reports_stale() -> None:
    xs = cor.find_xrefs("See `references/fast-path.md → Nope Table` here.\n", SK)
    right = {"file": SK, "line": 1, "raw": xs[0].raw, "target": xs[0].target_file, "reason": "fixture"}
    wrong = dict(right, target="skills/_shared/router-protocol.md")
    gone = dict(right, raw="`## Removed Heading`")
    check("H-M3 an entry for another target does not cover the xref", cor.diff_allowlist(xs, [wrong]) == xs)
    check("H-M3 a covering entry is consumed", cor.diff_allowlist(xs, [right]) == [] and cor.stale_allowlist(xs, [right]) == [])
    check("H-M3 entries matching no unresolved xref are reported stale", cor.stale_allowlist(xs, [right, gone]) == [gone])
    try:
        cor.diff_allowlist(xs, ["not a dict"])
        bad = False
    except ValueError:
        bad = True
    check("H-L3 a non-dict allowlist entry is a ValueError", bad)


def _run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "craftflow_router_corpus.py"), *map(str, args)],
                          capture_output=True, text=True)


def test_h_h1_empty_corpus_and_cli_errors() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        empty = _run_cli("--plugin-root", tmp)
        check("H-H1 empty plugin root exits non-zero with a structured stderr error",
              empty.returncode == 1 and empty.stdout == "" and "error" in json.loads(empty.stderr.strip()), empty.stdout + empty.stderr)
        miss = _run_cli("--plugin-root", PLUGIN_ROOT, "--allowlist", Path(tmp) / "gone.json")
        check("H-L2 missing allowlist file is a structured error, not a traceback",
              miss.returncode == 1 and "Traceback" not in miss.stderr and "error" in miss.stderr, miss.stderr)
        bad = Path(tmp) / "bad.json"
        bad.write_text(json.dumps({"entries": ["x"]}))
        nondict = _run_cli("--plugin-root", PLUGIN_ROOT, "--allowlist", bad)
        check("H-L3 non-dict allowlist entry is a structured error", nondict.returncode == 1 and "Traceback" not in nondict.stderr, nondict.stderr)
    with_list = _run_cli("--plugin-root", PLUGIN_ROOT, "--allowlist", ALLOWLIST, "--strict")
    check("H-L1 --strict with the committed allowlist exits 0", with_list.returncode == 0, with_list.stdout[-200:] + with_list.stderr)
    strict = _run_cli("--plugin-root", PLUGIN_ROOT, "--strict")
    check("H-L1 --strict without an allowlist exits 1 while danglers exist", strict.returncode == 1, strict.stdout[-200:])
    plain = _run_cli("--plugin-root", PLUGIN_ROOT)
    check("H-L1 default stays exit 0 (report only)", plain.returncode == 0)


def test_h_l5_nan_rejected_in_fixture_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "a.json"
        p.write_text('{"entries": [], "x": NaN}')
        try:
            cor.load_json(p)
            rejected = False
        except ValueError:
            rejected = True
    check("H-L5 NaN in a fixture is rejected", rejected)


def test_h_m4_unclosed_fence_reported() -> None:
    mask, unclosed = cor.fence_scan(["a", "```bash", "echo"])
    mask2, unclosed2 = cor.fence_scan(["a", "```bash", "echo", "```", "b"])
    check("H-M4 fence_scan reports the opener of an unclosed fence", unclosed == 2 and unclosed2 is None and mask2 == [False, True, True, True, False])


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:  # report per-test crashes without hiding the rest
                fail(name, f"{type(exc).__name__}: {exc}")
    print(f"Results: {_passes} passed, {len(_errors)} failed, {_skips} skipped")
    for e in _errors:
        print(e)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
