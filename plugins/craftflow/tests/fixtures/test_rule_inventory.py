#!/usr/bin/env python3
"""Tests for craftflow_rule_inventory.py (per-occurrence, per-reachable-workflow rule accounting).

Run: python3 tests/fixtures/test_rule_inventory.py
Covers DD-4 / BC-9 and plan cases E59, E60, E63, E63b-E63h.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_rule_inventory as inv  # noqa: E402

SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_rule_inventory.py"
FIXTURES = PLUGIN_ROOT / "tests" / "fixtures"
POLICY_MAP = PLUGIN_ROOT.parents[1] / "docs" / "router-policy-map.md"
BASELINE_JSON = FIXTURES / "router-rule-inventory.baseline.json"
LOCK = FIXTURES / "router-baseline.lock.json"
BASELINE_SHA = "0439c33932463d257b881ce50f136ab7661c0a39"  # hard-pinned; changes only by a deliberate rebaseline

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


K = "skills/craftflow-router/SKILL.md"
FP = "skills/craftflow-router/references/fast-path.md"
STD = "skills/craftflow-router/references/build-graph-standard.md"
DBG = "skills/craftflow-router/references/debug-workflow.md"
RR = "skills/craftflow-router/references/remediation-and-research.md"
RAT = "skills/craftflow-router/references/router-rationale-fixture.md"
RM = {
    "version": 1,
    "files": [
        {"file": K, "class": "kernel", "workflows": ["ALL"]},
        {"file": FP, "class": "start", "workflows": ["BUILD"]},
        {"file": STD, "class": "start", "workflows": ["BUILD"], "variant": "standard"},
        {"file": DBG, "class": "start", "workflows": ["DEBUG"]},
        {"file": RR, "class": "stage", "workflows": ["ALL"], "happy_path": False},
    ],
}
GATE = "- Run the Learn-Distill Gate after the verifier returns PASS."
INC = "- Increment `planning_review_runs += 1` before dispatch."


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def table(header: str, rows: list) -> str:
    cols = header.count("|") - 1
    sep = "|" + "|".join(["---"] * cols) + "|"
    return "\n".join([header, sep] + rows) + "\n"


def policy_doc(policy=(), rewrites=(), applic=(), rationale=(), widening=()) -> str:
    return (
        "# Router policy map\n\n"
        "## Policy-mapped deletions\n\n" + table(inv.HEADERS["policy"], list(policy)) +
        "\n## Rewrites\n\n" + table(inv.HEADERS["rewrites"], list(rewrites)) +
        "\n## Applicability\n\n" + table(inv.HEADERS["applicability"], list(applic)) +
        "\n## Rationale\n\n" + table(inv.HEADERS["rationale"], list(rationale)) +
        "\n## Read-map widening\n\n" + table(inv.HEADERS["widening"], list(widening)) +
        "\n## Worktree finalize equivalence\n\n"
    )


def baseline_of(corpus: dict) -> dict:
    return inv.build_inventory(corpus, RM, set(), ref="fixture")


def run_diff(before: dict, after: dict, doc: str = None, rationale: str = "", root=None) -> dict:
    return inv.diff_inventory(baseline_of(before), after, RM, doc if doc is not None else policy_doc(),
                              rationale_text=rationale, plugin_root=root, baseline_readmap=RM)


def passes(res: dict) -> bool:
    return inv.exit_code(res) == 0


def id_of(text: str) -> str:
    return sha(inv.normalize(text))


def test_normalize_strips_bullets_and_keeps_ordinals() -> None:
    check("normalize_strips_bullets_and_keeps_ordinals",
          inv.normalize("3. Run x") == "3. run x" and inv.normalize("- **Run**  __x__") == "run x",
          repr(inv.normalize("3. Run x")))


def test_normalize_collapses_whitespace_and_crlf() -> None:
    got = inv.normalize("Run\r\n  the   `X`\r\n")
    check("normalize_collapses_whitespace_and_crlf", got == "run the `X`", repr(got))


def test_fence_is_one_block_entry() -> None:
    text = "Intro line.\n\n```bash\necho 1\n\necho 2\n```\n"
    units = inv.extract_units(text, K)
    fences = [u for u in units if u["kind"] == "fence"]
    check("fence_is_one_block_entry", len(units) == 2 and len(fences) == 1 and "echo 2" in fences[0]["raw"],
          repr([u["kind"] for u in units]))


def test_table_row_is_entry() -> None:
    text = "| a | b |\n|---|---|\n| x | y |\n| z | w |\n"
    units = inv.extract_units(text, K)
    rows = [u["norm"] for u in units if u["kind"] == "table_row"]
    heads = [u["norm"] for u in units if u["kind"] == "table_header"]
    check("table_row_is_entry", heads == ["| a | b |"] and rows == ["| a | b | // | x | y |", "| a | b | // | z | w |"],
          repr((heads, rows)))


def test_easy_to_miss_bracket_is_entry() -> None:
    text = "- Do the thing [EASY TO MISS: keep\n  this [nested] bit] and go.\n"
    units = inv.extract_units(text, K)
    etm = [u["norm"] for u in units if u["kind"] == "easy_to_miss"]
    check("easy_to_miss_bracket_is_entry", etm == ["[easy to miss: keep this [nested] bit]"] and len(units) == 2,
          repr([(u["kind"], u["norm"]) for u in units]))


def test_classify_normative() -> None:
    words = ["never", "must", "do not", "always", "only", "required", "requires", "shall", "fail closed"]
    verbs = ["skips", "stops", "blocks", "denies", "fails", "advances", "halts", "aborts"]
    bad = []
    for w in words + verbs:
        u = inv.extract_units(f"The router {w} here.\n", K)[0]
        if not inv.classify_normative(u, frozenset()):
            bad.append(w)
    prose = inv.extract_units("This section describes how things look.\n", K)[0]
    if inv.classify_normative(prose, frozenset()):
        bad.append("prose-sentence-must-be-false")
    imperative = inv.extract_units("Read the file.\n", K)[0]
    if not inv.classify_normative(imperative, frozenset()):
        bad.append("imperative-Read")
    check("classify_normative", not bad, repr(bad))


def test_documentation_only_is_a_scope_phrase_not_the_obligation_word() -> None:
    """P11 rebaseline (David chose option (a) for edf4681b29aa): `documentation only` scopes a note and is not the obligation word `only`.
    The word still marks a rule anywhere else, another keyword in the same unit still wins, and the committed baseline changed in exactly one
    unit (1,221 normative / 56 non-normative became 1,220 / 57; a second silent reclassification would move these counts)."""
    def norm(text: str) -> bool:
        return inv.classify_normative(inv.extract_units(text, K)[0], frozenset())
    note = norm("[NOTE — documentation only, no code implied by this section]: today there are exactly 2 routers.\n")
    cased = norm("[NOTE — Documentation Only, No Code implied]: background about the two routers.\n")
    # P11 remediation 2 (verifier A1): the exemption is the exact scope phrase `documentation only, no code`, not every `documentation only`.
    # The first control began with an imperative (`Use`), so it stayed normative even if `only` were dropped from KEYWORDS; this one has no
    # imperative first word and no other keyword, so `only` alone makes it normative.
    rule = norm("The router reads only the shared doc.\n")
    bare_phrase = norm("[NOTE — documentation only]: background about the two routers.\n")  # outside the allowed scope: still the obligation word
    rule_phrase = norm("The router reads the documentation only when the user asks.\n")  # a rule whose object is documentation: still normative
    cased_bare = norm("[NOTE — Documentation Only]: background about the two routers.\n")
    other_keyword = norm("[NOTE — documentation only, no code]: the router must still read it.\n")
    near_miss = norm("The documentation is the only source.\n")  # `only` not directly after `documentation`
    baseline = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
    flags = {e["id"]: e["normative"] for e in baseline["entries"]}
    counts = (sum(flags.values()), len(flags) - sum(flags.values()))
    check("documentation_only_is_a_scope_phrase_not_the_obligation_word",
          not note and not cased and rule and bare_phrase and rule_phrase and cased_bare and other_keyword and near_miss
          and flags["edf4681b29aa"] is False and counts == (1220, 57),
          f"note={note} cased={cased} rule={rule} bare_phrase={bare_phrase} rule_phrase={rule_phrase} cased_bare={cased_bare} "
          f"other_keyword={other_keyword} near_miss={near_miss} edf4681b29aa={flags.get('edf4681b29aa')} counts={counts}")


def test_allowlist_forces_normative() -> None:
    u = inv.extract_units("Hunter findings are ignored.\n", K)[0]
    off = inv.classify_normative(u, frozenset(), keywords=False)
    allow = frozenset([inv.normalize("Hunter findings are ignored.")])
    on = inv.classify_normative(u, allow, keywords=False)
    check("allowlist_forces_normative", (not off) and on, f"off={off} on={on}")


def test_ids_stable() -> None:
    a = inv.extract_units("- Run   x\n", K)[0]["id"]
    b = inv.extract_units("* run x\n", K)[0]["id"]
    c = inv.extract_units("1. Run   x\n", K)[0]["id"]
    check("ids_stable", a == b == sha("run x") and c == sha("1. run x") != a, f"{a} {b} {c} {sha('run x')}")


def test_duplicate_sentence_one_id_many_occurrences() -> None:
    corpus = {K: "## A\n\n- Run it.\n", FP: "## B\n\n- Run it.\n"}
    entries = [e for e in baseline_of(corpus)["entries"] if e["id"] == id_of("Run it.")]
    check("duplicate_sentence_one_id_many_occurrences",
          len(entries) == 1 and len(entries[0]["occurrences"]) == 2, repr(entries))


def test_occurrence_deficit_detected() -> None:
    four = "## Loop\n\n" + "\n".join([INC] * 4) + "\n"
    one = "## Loop\n\n" + INC + "\n"
    res = run_diff({RR: four}, {RR: one})
    check("occurrence_deficit_detected",
          res["occurrence_deficit"] == 3 and res["unmapped_removed"] == 3 and inv.exit_code(res) == 1, repr(res))


def test_unreachable_detected() -> None:
    before = {FP: "## Gate\n\n" + GATE + "\n"}
    after = {STD: "## Gate\n\n" + GATE + "\n"}
    res = run_diff(before, after)
    check("unreachable_detected", res["unreachable"] >= 1 and inv.exit_code(res) == 1, repr(res))


def test_dedupe_into_non_covering_file_fails() -> None:
    before = {K: "## Gate\n\n" + GATE + "\n", FP: "## Gate\n\n" + GATE + "\n"}
    row_ok = f"| {id_of(GATE)} | {id_of(GATE)} | dedupe | same gate | kept copy in the kernel covers all | David Gracia (2026-10-08) |"
    good = run_diff(before, {K: before[K]}, policy_doc(rewrites=[row_ok]))
    bad_before = {STD: "## Gate\n\n" + GATE + "\n", FP: "## Gate\n\n" + GATE + "\n"}
    bad = run_diff(bad_before, {STD: bad_before[STD]}, policy_doc(rewrites=[row_ok]))
    check("dedupe_into_non_covering_file_fails", passes(good) and not passes(bad),
          f"good={good} bad={bad}")


def test_rewrite_row_requires_equivalence_and_reviewer() -> None:
    old, new = "- Run the old gate.", "- Run the new gate."
    before, after = {K: "## G\n\n" + old + "\n"}, {K: "## G\n\n" + new + "\n"}
    row = lambda note, who: f"| {id_of(old)} | {id_of(new)} | pointer | reworded | {note} | {who} |"
    full = run_diff(before, after, policy_doc(rewrites=[row("same rule", "David Gracia (2026-10-08)")]))
    no_note = run_diff(before, after, policy_doc(rewrites=[row("", "David Gracia (2026-10-08)")]))
    no_who = run_diff(before, after, policy_doc(rewrites=[row("same rule", "")]))
    check("rewrite_row_requires_equivalence_and_reviewer",
          passes(full) and full["reworded"] == 1 and no_note["rows_missing_review"] == 1
          and no_who["rows_missing_review"] == 1 and not passes(no_note) and not passes(no_who),
          f"full={full} no_note={no_note} no_who={no_who}")


def test_allowlisted_rule_to_rationale_fails() -> None:
    text = "DEBUG skips hunter findings."
    allow = {inv.normalize(text)}
    before = {K: "## Handoff\n\n" + text + "\n"}
    base = inv.build_inventory(before, RM, allow, ref="fixture")
    row = f"| {id_of(text)} | router-rationale.md | moved for size | David Gracia (2026-10-08) |"
    res = inv.diff_inventory(base, {K: "## Handoff\n\nOther.\n"}, RM, policy_doc(rationale=[row]),
                             rationale_text=text + "\n", baseline_readmap=RM)
    check("allowlisted_rule_to_rationale_fails",
          res["normative_in_rationale"] == 1 and inv.exit_code(res) == 1, repr(res))


def test_applicability_row_requires_quote() -> None:
    text = "- After `integration-verifier` returns PASS, run the Learn-Distill Gate."
    before = {FP: "## G\n\n" + text + "\n"}
    after = {STD: "## G\n\n" + text + "\n"}
    row = lambda q: f"| {id_of(text)} | BUILD | BUILD:standard | {q} | David Gracia (2026-10-08) |"
    good = run_diff(before, after, policy_doc(applic=[row('"After `integration-verifier` returns PASS"')]))
    bad = run_diff(before, after, policy_doc(applic=[row("")]))
    check("applicability_row_requires_quote",
          passes(good) and not passes(bad) and bool(bad["row_errors"]), f"good={good} bad={bad}")


def test_heading_rename_requires_row() -> None:
    before = {K: "## Old Name\n\n" + GATE + "\n"}
    after = {K: "## Old Name Updated\n\n" + GATE + "\n"}  # REM-FIX 6 (HD): a heading row must keep >= 60% of the old words
    row = f"| {K}#old name | {K}#old name updated | heading | rename | same section | David Gracia (2026-10-08) |"  # file-scoped (H-L4)
    bad = run_diff(before, after)
    good = run_diff(before, after, policy_doc(rewrites=[row]))
    check("heading_rename_requires_row", not passes(bad) and bad["unmapped_removed"] == 1 and passes(good),
          f"bad={bad} good={good}")


def test_applicability_from_baseline_readmap() -> None:
    corpus = {K: "## A\n\n- Run kernel rule.\n", FP: "## B\n\n- Run build rule.\n"}
    ents = {e["id"]: e for e in baseline_of(corpus)["entries"]}
    kern = ents[id_of("Run kernel rule.")]["occurrences"][0]["applicability"]
    bld = ents[id_of("Run build rule.")]["occurrences"][0]["applicability"]
    check("applicability_from_baseline_readmap",
          len(kern) == 5 and {t[2] for t in kern} == {"kernel"}
          and bld == [["BUILD", "fast_path", "start"], ["BUILD", "standard", "start"]], f"{kern} {bld}")


def test_extract_is_deterministic() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "skills" / "craftflow-router").mkdir(parents=True)
        (root / "skills" / "craftflow-router" / "SKILL.md").write_text("## A\n\n- Run it.\n\nNever stop.\n")
        (root / "rm.json").write_text(json.dumps({"version": 1, "files": [RM["files"][0]]}))
        (root / "al.json").write_text(json.dumps({"version": 1, "entries": []}))
        outs = []
        for i in range(2):
            out = root / f"o{i}.json"
            subprocess.run([sys.executable, str(SCRIPT), "--emit", "--plugin-root", str(root), "--readmap-baseline",
                            str(root / "rm.json"), "--allowlist", str(root / "al.json"), "--out", str(out)],
                           check=True, capture_output=True)
            outs.append(out.read_bytes())
    check("extract_is_deterministic", outs[0] == outs[1] and len(outs[0]) > 20, "bytes differ")


def test_presence_after_rewrap() -> None:
    before = {K: "## A\n\n- Run the long sentence that wraps across lines.\n"}
    after = {K: "## A\n\n- Run the long\n  sentence that wraps\n  across lines.\n"}
    res = run_diff(before, after)
    check("presence_after_rewrap", passes(res) and res["unmapped_removed"] == 0, repr(res))


def test_split_rule_requires_rewrites_row() -> None:
    whole = "- Run the gate and then the sweep."
    p1, p2 = "- Run the gate.", "- Then the sweep."
    before = {K: "## A\n\n" + whole + "\n"}
    after = {K: "## A\n\n" + p1 + "\n\n## B\n\n" + p2 + "\n"}  # both halves stay kernel-read: a split may not lose reach (R-H1)
    row = f"| {id_of(whole)} | {id_of(p1)}, {id_of(p2)} | split | halves | union equals the whole | David Gracia (2026-10-08) |"
    bad = run_diff(before, after)
    good = run_diff(before, after, policy_doc(rewrites=[row]))
    check("split_rule_requires_rewrites_row", not passes(bad) and passes(good), f"bad={bad} good={good}")


def test_normative_in_rationale_fails() -> None:
    text = "- You must never skip the gate."
    before = {K: "## A\n\n" + text + "\n"}
    row = f"| {id_of(text)} | router-rationale.md | too long | David Gracia (2026-10-08) |"
    res = run_diff(before, {K: "## A\n\nOther text here.\n"}, policy_doc(rationale=[row]), rationale=text + "\n")
    check("normative_in_rationale_fails", res["normative_in_rationale"] == 1 and not passes(res), repr(res))


def test_rationale_row_covers_non_normative_unit() -> None:
    text = "This paragraph explains why the design looks like this."
    before = {K: "## A\n\n" + text + "\n"}
    row = f"| {id_of(text)} | router-rationale.md | explanatory | David Gracia (2026-10-08) |"
    res = run_diff(before, {K: "## A\n\nOther text here.\n"}, policy_doc(rationale=[row]), rationale=text + "\n")
    check("rationale_row_covers_non_normative_unit",
          passes(res) and res["rationale"] == 1, repr(res))


def test_policy_row_requires_existing_script_and_test() -> None:
    text = "- Never enter plan mode."
    before = {K: "## A\n\n" + text + "\n"}
    after = {K: "## A\n\nOther text here.\n"}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "scripts").mkdir()
        (root / "tests" / "fixtures").mkdir(parents=True)
        (root / "scripts" / "guard.py").write_text("def decide():\n    return 1\n")
        (root / "tests" / "fixtures" / "test_guard.py").write_text("def test_decide():\n    pass\n")
        row = lambda test: (f"| {id_of(text)} | enter_plan_mode | scripts/guard.py::decide | {test} | deleted | true | David Gracia (2026-10-08) |")
        good = run_diff(before, after, policy_doc(policy=[row("tests/fixtures/test_guard.py::test_decide")]), root=root)
        missing = run_diff(before, after, policy_doc(policy=[row("tests/fixtures/test_gone.py::test_decide")]), root=root)
    check("policy_row_requires_existing_script_and_test",
          passes(good) and good["policy_mapped"] == 1 and not passes(missing) and bool(missing["row_errors"]),
          f"good={good} missing={missing}")


def test_report_nonnormative_counts_whole_units_only() -> None:
    text = ("Why this exists is long background text that explains history.\n\n"
            "You must never skip this step, because history shows what happened before.\n")
    rep = inv.report_nonnormative({K: text}, [K], set())
    expected = len("Why this exists is long background text that explains history.".encode("utf-8"))
    check("report_nonnormative_counts_whole_units_only",
          rep["total"] == expected and rep["files"][K]["units"] == 1, repr(rep))


def test_baseline_lock_pins_the_baseline() -> None:
    """D3: the baseline, its frozen read map and the commit are pinned; a mismatch fails and is never skipped."""
    try:
        inv.verify_lock(BASELINE_JSON, FIXTURES / "router-readmap.baseline.json", LOCK)
        problem = ""
    except ValueError as exc:
        problem = str(exc)
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    check("baseline_lock_pins_the_baseline", not problem and lock["baseline_sha"] == BASELINE_SHA, problem or repr(lock))


def test_baseline_matches_ref() -> None:
    have = subprocess.run(["git", "-C", str(PLUGIN_ROOT), "cat-file", "-e", f"{BASELINE_SHA}^{{commit}}"], capture_output=True)
    if have.returncode != 0:  # a clone without the baseline commit proves nothing about the baseline: fail, never skip (D3)
        fail("baseline_matches_ref", f"pinned baseline commit {BASELINE_SHA} is not in this clone: fetch-depth 0 required")
        return
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "regen.json"
        r = subprocess.run([sys.executable, str(SCRIPT), "--emit", "--ref", BASELINE_SHA, "--readmap-baseline",
                            str(FIXTURES / "router-readmap.baseline.json"), "--allowlist",
                            str(FIXTURES / "router-normative-allowlist.json"), "--out", str(out)],
                           capture_output=True, text=True)
        same = r.returncode == 0 and out.read_bytes() == BASELINE_JSON.read_bytes()
    check("baseline_matches_ref", same, r.stderr[-300:] if r.returncode else "regenerated bytes differ")


def test_diff_zero_unmapped() -> None:
    r = subprocess.run([sys.executable, str(SCRIPT), "--diff", "--baseline", str(BASELINE_JSON), "--policy-map",
                        str(POLICY_MAP), "--readmap", str(PLUGIN_ROOT / "config" / "router-readmap.json")],
                       capture_output=True, text=True)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        fail("diff_zero_unmapped", f"no JSON: {r.stdout[-200:]} {r.stderr[-200:]}")
        return
    check("diff_zero_unmapped", r.returncode == 0 and res["unmapped_removed"] == 0 and res["unreachable"] == 0,
          repr(res))
    print(f"  unmapped_removed={res['unmapped_removed']} unreachable={res['unreachable']} "
          f"baseline_units={res['baseline_units']} baseline_occurrences={res['baseline_occurrences']}")


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
