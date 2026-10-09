#!/usr/bin/env python3
"""Tests for craftflow_router_readset.py (read-set measurer) and its cut-plan projection (BC-11, PP-10).

Run: python3 tests/fixtures/test_router_readset.py
Covers plan P2 task 1 and cases E64-E66, E71-E74. The synthetic tests build a small world (files + read map) in
memory; the real-tree tests read the live router, the frozen baseline (via the lock file) and the committed cut plan.

Cut-plan row status. A row may carry `"status": "needs_anchor"` only together with a non-empty `note` that quotes the
plan text that is too imprecise to anchor exactly (plan P2 task 3: anchors are never guessed). Such a row is not applied
by the projector and its bytes are missing from every tier; `cutplan_rows_complete` tolerates it for that reason only,
and the projection output lists the ids under `unanchored`.
"""
from __future__ import annotations

import copy
import itertools
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_router_corpus as cor  # noqa: E402
import craftflow_router_readset as rs  # noqa: E402
import craftflow_router_readset_core as core  # noqa: E402
import craftflow_router_readset_project as prj  # noqa: E402

FIXTURES = PLUGIN_ROOT / "tests" / "fixtures"
SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_router_readset.py"
LIVE_MAP = PLUGIN_ROOT / "config" / "router-readmap.json"
BASE_MAP = FIXTURES / "router-readmap.baseline.json"
PROJECTED_MAP = FIXTURES / "router-readmap.projected.json"
CUTPLAN = FIXTURES / "router-cut-plan.json"
BUDGET = FIXTURES / "router-budget.json"
LOCK = FIXTURES / "router-baseline.lock.json"
SKILL = "skills/craftflow-router/SKILL.md"

_passes = 0
_skips = 0
_errors: list = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def skip(name: str, reason: str) -> None:
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


def baseline_sha() -> str:
    return json.loads(LOCK.read_text(encoding="utf-8"))["baseline_sha"]


def run_cli(*args: str, plugin: Path = PLUGIN_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(plugin / "scripts" / "craftflow_router_readset.py"), *args],
                          capture_output=True, text=True)


def cli_json(*args: str) -> dict:
    res = run_cli(*args)
    if res.returncode != 0:
        raise AssertionError(f"exit {res.returncode}: {res.stderr.strip()[:300]}")
    return json.loads(res.stdout.strip().splitlines()[-1])


def raises(fn, exc=Exception) -> bool:
    try:
        fn()
    except exc:
        return True
    return False


# ---------------------------------------------------------------------------------------------- synthetic world

REF = "skills/craftflow-router/references/"
KER, PRO = SKILL, "skills/_shared/router-protocol.md"
ART, BW, PW, DW, RW = (REF + n for n in ("workflow-artifact-and-hook-policy.md", "build-workflow.md", "plan-workflow.md",
                                         "debug-workflow.md", "review-workflow.md"))
HSC = REF + "harness-self-checks.md"
POLICY = "config/policy.json"


def syn_map() -> dict:
    return {"version": 1, "variants": {"BUILD": ["fast_path", "standard"], "PLAN": ["standard"], "DEBUG": ["standard"],
                                       "REVIEW": ["standard"]},
            "files": [
                {"file": KER, "class": "kernel", "workflows": ["ALL"], "trigger": "skill load", "happy_path": True},
                {"file": PRO, "class": "kernel", "workflows": ["ALL"], "trigger": "read before routing", "happy_path": True},
                {"file": ART, "class": "start", "workflows": ["ALL"], "trigger": "workflow start", "happy_path": True},
                {"file": BW, "class": "start", "workflows": ["BUILD"], "trigger": "BUILD preparation", "happy_path": True},
                {"file": PW, "class": "start", "workflows": ["PLAN"], "trigger": "PLAN preparation", "happy_path": True},
                {"file": DW, "class": "start", "workflows": ["DEBUG"], "trigger": "DEBUG preparation", "happy_path": True},
                {"file": RW, "class": "start", "workflows": ["REVIEW"], "trigger": "REVIEW preparation", "happy_path": True},
                {"file": HSC, "class": "stage", "workflows": ["BUILD"], "trigger": "phase exit", "happy_path": True}]}


def syn_files(rm: dict) -> dict:
    body = "".join(f"line {i} of {name}\n" for name in ("A", "B") for i in range(1, 6))
    return {KER: "# Kernel\n\nintro\n\n" + core.table_block(rm) + "\n## 1. Rest\n\n" + body,
            PRO: "# Protocol\n\n" + body, ART: "## Art\n\n" + body, BW: "### BW\n\n" + body,
            PW: "### PW\n\n" + body, DW: "### DW\n\n" + body, RW: "### RW\n\n" + body, HSC: "### HSC\n\n" + body}


def syn_state() -> dict:
    rm = syn_map()
    return prj.make_state(syn_files(rm), rm, {})


def row(rid: str, phase: str, op: str, **kw) -> dict:
    return {"id": rid, "phase": phase, "op": op, "condition": kw.pop("condition", "always"), **kw}


def apply(rows: list, state=None) -> dict:
    st = copy.deepcopy(state or syn_state())
    prj.apply_rows(st, rows)
    return st


def sizes_of(state: dict) -> dict:
    return {k: len(v.encode("utf-8")) for k, v in state["files"].items()}


def start_total(state: dict, wf: str) -> int:
    return core.compute_from_sizes(state["readmap"], sizes_of(state), wf)["start_total"]


# ---------------------------------------------------------------------------------------------- measurer tests


def test_parses_readmap() -> None:
    rm = rs.load_map(LIVE_MAP)
    names = [e["file"] for e in rm["files"]]
    bad = copy.deepcopy(rm)
    bad["files"][0]["bogus"] = 1
    tmp = Path(tempfile.mkdtemp())
    (tmp / "m.json").write_text(json.dumps(bad), encoding="utf-8")
    check("parses_readmap", SKILL in names and len(names) >= 12 and raises(lambda: rs.load_map(tmp / "m.json"), rs.ReadsetError),
          f"names={len(names)}")
    shutil.rmtree(tmp, ignore_errors=True)


def test_every_reference_and_shared_file_mapped_once() -> None:
    rm = rs.load_map(LIVE_MAP)
    mapped = {e["file"] for e in rm["files"]}
    corpus = cor.read_corpus(PLUGIN_ROOT)
    missing = sorted(set(corpus) - mapped)
    seen, dup = {}, []
    for e in rm["files"]:
        for pair in core.entry_pairs(rm, e):
            if (e["file"], pair) in seen:
                dup.append((e["file"], pair))
            seen[(e["file"], pair)] = e["class"]
    synthetic = copy.deepcopy(rm)
    synthetic["files"].append(dict(rm["files"][3]))  # the same file/pairs twice must be caught
    again = []
    s2 = {}
    for e in synthetic["files"]:
        for pair in core.entry_pairs(synthetic, e):
            if (e["file"], pair) in s2:
                again.append(pair)
            s2[(e["file"], pair)] = 1
    check("every_reference_and_shared_file_mapped_once", not missing and not dup and again,
          f"unmapped={missing} duplicate_pairs={dup} control_detects={bool(again)}")


def test_readmap_files_exist() -> None:
    rm = rs.load_map(LIVE_MAP)
    live_ok = not [e["file"] for e in rm["files"] if e["class"] != "maintainer" and not (PLUGIN_ROOT / e["file"]).is_file()]
    bad = copy.deepcopy(rm)
    bad["files"].append({"file": REF + "ghost.md", "class": "stage", "workflows": ["BUILD"], "trigger": "x", "happy_path": False})
    control = raises(lambda: rs.check_map_files(bad, {e["file"] for e in rm["files"]}), rs.ReadsetError)
    check("readmap_files_exist", live_ok and control, f"live_ok={live_ok} control={control}")


def test_kernel_mentions_are_mapped() -> None:
    rm = rs.load_map(LIVE_MAP)
    text = (PLUGIN_ROOT / SKILL).read_text(encoding="utf-8")
    unmapped = core.unmapped_kernel_mentions(text, rm)
    control = core.unmapped_kernel_mentions(text + "\nsee `references/not-a-file.md` and `_shared/ghost.md`\n", rm)
    check("kernel_mentions_are_mapped", not unmapped and sorted(control) == ["_shared/ghost.md", "references/not-a-file.md"],
          f"unmapped={unmapped} control={control}")


def test_kernel_table_equals_generated() -> None:
    rm = rs.load_map(LIVE_MAP)
    text = (PLUGIN_ROOT / SKILL).read_text(encoding="utf-8")
    block = core.extract_table(text)
    printed = run_cli("--print-table")
    check("kernel_table_equals_generated",
          block is not None and block == core.table_block(rm) and printed.returncode == 0 and printed.stdout == block,
          "kernel markers missing or table differs from --print-table")


def test_build_variants_max() -> None:
    rm = syn_map()
    rm["files"].append({"file": REF + "build-graph-standard.md", "class": "start", "workflows": ["BUILD"],
                        "variant": "standard", "trigger": "standard", "happy_path": True})
    sizes = {e["file"]: 1000 for e in rm["files"]}
    sizes[REF + "build-graph-standard.md"] = 5000
    res = core.compute_from_sizes(rm, sizes, "BUILD")
    fast = core.compute_from_sizes(rm, sizes, "BUILD", "fast_path")["start_total"]
    std = core.compute_from_sizes(rm, sizes, "BUILD", "standard")["start_total"]
    live = cli_json("--workflow", "BUILD", "--json")
    lv = live["variants"]
    check("build_variants_max", res["start_total"] == max(fast, std) == std and std - fast == 5000
          and live["start_total"] == max(v["start_total"] for v in lv.values()) and set(lv) == {"fast_path", "standard"},
          f"fast={fast} std={std} live={live['start_total']}")


def test_json_shape() -> None:
    res = cli_json("--workflow", "BUILD", "--json")
    keys = {"workflow", "variant", "start_files", "start_total", "budget", "within_budget", "stage_files", "happy_path_total", "variants"}
    sf = res["start_files"][0]
    st = res["stage_files"][0] if res["stage_files"] else {"path": 0, "bytes": 0, "trigger": 0, "happy_path": 0}
    check("json_shape", set(res) == keys and set(sf) == {"path", "bytes"} and set(st) == {"path", "bytes", "trigger", "happy_path"}
          and res["workflow"] == "BUILD" and res["start_total"] == sum(f["bytes"] for f in res["start_files"])
          and isinstance(res["within_budget"], (bool, type(None))), f"keys={sorted(res)}")


def test_ref_mode_uses_baseline_readmap() -> None:
    res = cli_json("--workflow", "BUILD", "--ref", baseline_sha(), "--readmap", str(BASE_MAP), "--json")
    plan = cli_json("--workflow", "PLAN", "--ref", baseline_sha(), "--readmap", str(BASE_MAP), "--json")
    check("ref_mode_uses_baseline_readmap", res["start_total"] == 214055 and plan["start_total"] == 199880,
          f"BUILD={res['start_total']} PLAN={plan['start_total']}")


def test_ref_mode_missing_file_exits_2() -> None:
    rm = json.loads(BASE_MAP.read_text(encoding="utf-8"))
    rm["files"].append({"file": REF + "does-not-exist.md", "class": "start", "workflows": ["BUILD"], "trigger": "x", "happy_path": True})
    tmp = Path(tempfile.mkdtemp())
    (tmp / "m.json").write_text(json.dumps(rm), encoding="utf-8")
    res = run_cli("--workflow", "BUILD", "--ref", baseline_sha(), "--readmap", str(tmp / "m.json"), "--json")
    malformed = tmp / "bad.json"
    malformed.write_text("{not json", encoding="utf-8")
    res2 = run_cli("--workflow", "BUILD", "--readmap", str(malformed), "--json")
    shutil.rmtree(tmp, ignore_errors=True)
    check("ref_mode_missing_file_exits_2", res.returncode == 2 and res.stdout == "" and res2.returncode == 2,
          f"missing={res.returncode} malformed={res2.returncode}")


NODE_FIXTURES = {
    "single": "---\nname: a\ndescription: One line description\n---\nbody\n",
    "block": "---\nname: b\ndescription: |\n  First paragraph.\n\n  Second paragraph.\nallowed-tools: Read\n---\nbody\n",
    "folded_dash": "---\nname: c\ndescription: >-\n  Folded text\n  continues here.\nskills:\n  - x\n---\nbody\n",
    "quoted": "---\nname: d\ndescription: \"Quoted value\"\n---\nbody\n",
}


def _node_parser() -> "str | None":
    mjs = PLUGIN_ROOT.parents[2].parent / "scripts" / "gen-craftflow-graph.mjs"
    if not mjs.is_file() or not shutil.which("node"):
        return None
    src = mjs.read_text(encoding="utf-8")
    m = re.search(r"^function parseFrontmatter\(text\) \{.*?^\}\n", src, re.S | re.M)
    return m.group(0) if m else None


def test_descriptions_match_js_parser() -> None:
    node_src = _node_parser()
    py = {k: rs.js_description(v) for k, v in NODE_FIXTURES.items()}
    expected_shape = py["single"] == "One line description" and py["block"] == "First paragraph.\n\nSecond paragraph." \
        and py["folded_dash"] == ">-" and py["quoted"] == "Quoted value"
    eff = {k: core.effective_description(v) for k, v in NODE_FIXTURES.items()}
    differs = eff["folded_dash"] == "Folded text continues here." and eff["block"] == py["block"] and eff["single"] == py["single"]
    if node_src is None:
        skip("descriptions_match_js_parser:node", "node or scripts/gen-craftflow-graph.mjs unavailable")
    else:
        got = {}
        for k, v in NODE_FIXTURES.items():
            res = subprocess.run(["node", "-e", node_src + "console.log(JSON.stringify(parseFrontmatter(require('fs').readFileSync(0,'utf8')).description??''))"],
                                 input=v, capture_output=True, text=True)
            got[k] = json.loads(res.stdout) if res.returncode == 0 else f"node-error:{res.stderr[:80]}"
        check("descriptions_match_js_parser:node_parity", got == py, f"node={got} py={py}")
    live = run_cli("--descriptions")
    data = json.loads(live.stdout) if live.returncode == 0 else {}
    check("descriptions_match_js_parser", expected_shape and differs and data.get("js_parser_sum", 0) > 0
          and data.get("sum", 0) >= data.get("js_parser_sum", 1) and sum(data.get("per_skill", {}).values()) == data.get("sum"),
          f"py={py} eff={eff} out={str(data)[:160]}")


# ---------------------------------------------------------------------------------------------- projection tests


def real_cutplan() -> dict:
    return json.loads(CUTPLAN.read_text(encoding="utf-8"))


def real_state0() -> dict:
    return prj.load_baseline_state(PLUGIN_ROOT, baseline_sha(), BASE_MAP)


def test_projection_identity_at_baseline() -> None:
    st0 = real_state0()
    want = {w: core.compute_from_sizes(st0["readmap"], {k: len(v.encode()) for k, v in st0["files"].items()}, w)["start_total"]
            for w in ("BUILD", "PLAN", "DEBUG", "REVIEW")}
    out_empty = prj.project({"rows": []}, st0, phases=[])
    out_none = prj.project(real_cutplan(), st0, phases=[])
    ok_tiers = all(out["tiers"][t]["BUILD"]["max"] == 214055 and out["tiers"][t]["PLAN"] == 199880
                   and out["tiers"][t]["DEBUG"] == 179196 and out["tiers"][t]["REVIEW"] == 177258
                   for out in (out_empty, out_none) for t in ("guaranteed", "conditional"))
    cli = cli_json_args_project([] , phases="")
    check("projection_identity_at_baseline", want == {"BUILD": 214055, "PLAN": 199880, "DEBUG": 179196, "REVIEW": 177258}
          and ok_tiers and cli["tiers"]["guaranteed"]["BUILD"]["max"] == 214055, f"want={want}")


def cli_json_args_project(extra: list, phases: "str | None" = None) -> dict:
    args = ["--project", str(CUTPLAN), "--json"]
    if phases is not None:
        args += ["--phases", phases]
    return cli_json(*args, *extra)


def measured_tree(root: Path, ref: "str | None") -> dict:
    """Per-file bytes and per-workflow/variant start totals of a tree (working tree or a commit)."""
    rm = rs.load_map(root / "config" / "router-readmap.json") if ref is None else json.loads(
        subprocess.run(["git", "-C", str(root), "show", f"{ref}:./config/router-readmap.json"], capture_output=True, text=True, check=True).stdout)
    sizes = rs.measure(root, ref, rm)
    out = {"files": sizes, "readsets": {}}
    for w in ("BUILD", "PLAN", "DEBUG", "REVIEW"):
        res = core.compute_from_sizes(rm, sizes, w)
        out["readsets"][w] = {v: d["start_total"] for v, d in res["variants"].items()}
    return out


def test_projection_p2_equals_measured_head() -> None:
    budget = json.loads(BUDGET.read_text(encoding="utf-8"))
    snap = budget.get("p2_measured")
    proj = prj.project(real_cutplan(), real_state0(), phases=["P2"])
    g = proj["tiers"]["guaranteed"]
    cur = measured_tree(PLUGIN_ROOT, None)
    from_proj = proj["variant_totals"]["guaranteed"]
    same_vs_tree = all(from_proj[w][v] == cur["readsets"][w][v] for w in cur["readsets"] for v in cur["readsets"][w])
    last = (budget.get("merged_phases") or [None])[-1]
    # while P2 is the newest phase the working tree IS the P2 tree; afterwards the committed snapshot is the reference
    ref_ok = (same_vs_tree if last == "P2" else True)
    snap_ok = isinstance(snap, dict) and all(from_proj[w][v] == snap["readsets"][w][v] for w in snap["readsets"] for v in snap["readsets"][w])
    per_file_ok = all(proj["per_file"]["guaranteed"].get(f) == b for f, b in (snap or {}).get("files", {}).items()
                      if f.startswith("skills/"))
    sha = budget.get("p2_sha")
    verdict = p2_sha_verdict(sha, snap)
    if verdict == "absent":  # never a silent True: say so, and let CI (fetch-depth 0, which has the commit) refuse a skip
        skip("projection_p2_equals_measured_head:p2_sha", f"commit {sha} is absent from this clone (CI must not skip)")
    check("projection_p2_equals_measured_head", ref_ok and snap_ok and per_file_ok and verdict in ("match", "absent") and g["PLAN"] > 0,
          f"tree={same_vs_tree} snapshot={snap_ok} per_file={per_file_ok} sha={verdict} last={last}")


def p2_sha_verdict(sha, snap) -> str:
    """'none' without a recorded sha, 'absent' when the commit is not in this clone, else 'match' or 'mismatch' of the snapshot."""
    if not sha:
        return "none"
    have = subprocess.run(["git", "-C", str(PLUGIN_ROOT), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True).returncode == 0
    if not have:
        return "absent"
    return "match" if measured_tree(PLUGIN_ROOT, sha) == snap else "mismatch"


def test_p2_sha_absent_is_reported_not_passed() -> None:
    budget = json.loads(BUDGET.read_text(encoding="utf-8"))
    snap = budget["p2_measured"]
    bad_snap = copy.deepcopy(snap)
    bad_snap["files"]["skills/craftflow-router/SKILL.md"] += 1
    real = budget["p2_sha"]
    check("p2_sha_absent_is_reported_not_passed",
          p2_sha_verdict("0" * 40, snap) == "absent" and p2_sha_verdict(None, snap) == "none"
          and p2_sha_verdict(real, snap) == "match" and p2_sha_verdict(real, bad_snap) == "mismatch",
          f"absent={p2_sha_verdict('0' * 40, snap)} none={p2_sha_verdict(None, snap)}")


def test_insert_bytes_exact() -> None:
    base = syn_state()
    text = "ZZZ-inserted\n"
    n = len(text.encode())
    after = apply([row("i1", "P4", "insert", file=BW, position="end_of_file", text=text)], base)
    stage = apply([row("i2", "P4", "insert", file=HSC, position="end_of_file", text=text)], base)
    ok1 = start_total(after, "BUILD") - start_total(base, "BUILD") == n and start_total(after, "PLAN") == start_total(base, "PLAN")
    ok2 = all(start_total(stage, w) == start_total(base, w) for w in ("BUILD", "PLAN", "DEBUG", "REVIEW")) \
        and sizes_of(stage)[HSC] - sizes_of(base)[HSC] == n
    newf = apply([row("i3", "P8", "insert", file=REF + "worktree.md", position="new_file", text="# W\n")], base)
    dup = raises(lambda: apply([row("i4", "P8", "insert", file=BW, position="new_file", text="x\n")], base), prj.ProjectionError)
    anchor = apply([row("i5", "P4", "insert", file=BW, position="after_anchor", anchor="line 2 of A", text="X\n"),
                    row("i6", "P4", "insert", file=BW, position="before_anchor", anchor="line 3 of A", text="Y\n")], base)
    lines = anchor["files"][BW].split("\n")
    pos_ok = lines[lines.index("line 2 of A") + 1] == "X" and lines[lines.index("line 3 of A") - 1] == "Y"
    check("insert_bytes_exact", ok1 and ok2 and newf["files"][REF + "worktree.md"] == "# W\n" and dup and pos_ok,
          f"start={ok1} stage={ok2} new={dup} pos={pos_ok}")


def test_replace_delta_exact() -> None:
    base = syn_state()
    before = sizes_of(base)[BW]
    r1 = apply([row("r1", "P6", "replace", file=BW, start_anchor="line 2 of A", end_anchor="line 4 of A", text="short\n")], base)
    span = "".join(f"line {i} of A\n" for i in (2, 3, 4))
    ok1 = sizes_of(r1)[BW] - before == len("short\n") - len(span.encode())
    r2 = apply([row("r2", "P6", "replace", file=BW, start_anchor="line 1 of B", text="line one B (= 3)\n")], base)
    ok2 = sizes_of(r2)[BW] - before == len("line one B (= 3)\n") - len("line 1 of B\n")
    base["files"][POLICY] = "{}\n"  # a whole-file replace needs the file to exist (a missing one is ProjectionError); new files are `insert new_file`
    r3 = apply([row("r3", "P5", "replace", file=POLICY, whole_file=True, text='{"a": 1}\n')], base)
    r4 = apply([row("r4", "P5", "replace", file=BW, whole_file=True, text="whole\n")], base)
    ok3 = r3["files"][POLICY] == '{"a": 1}\n' and r4["files"][BW] == "whole\n"
    check("replace_delta_exact", ok1 and ok2 and ok3, f"span={ok1} single={ok2} whole={ok3}")


def test_readmap_rerenders_table() -> None:
    base = syn_state()
    newrow = {"file": REF + "worktree.md", "class": "stage", "workflows": ["BUILD"], "trigger": "after the final verifier pass", "happy_path": True}
    st = apply([row("m1", "P8", "insert", file=newrow["file"], position="new_file", text="# W\n"),
                row("m2", "P8", "readmap", action="add", row=newrow)], base)
    d_kernel = sizes_of(st)[KER] - sizes_of(base)[KER]
    before_t = core.table_block(base["readmap"])
    after_t = core.table_block(st["readmap"])
    want = len(after_t.encode()) - len(before_t.encode())
    check("readmap_rerenders_table", d_kernel == want and want > 0 and core.extract_table(st["files"][KER]) == after_t
          and any(e["file"] == newrow["file"] for e in st["readmap"]["files"]) and start_total(st, "BUILD") != 0,
          f"d_kernel={d_kernel} want={want}")


def test_anchor_resolves_after_earlier_insert() -> None:
    base = syn_state()
    seed = row("a1", "P2", "insert", file=BW, position="after_anchor", anchor="line 1 of A", text="<!-- marker:begin -->\n")
    late = row("a2", "P6", "insert", file=BW, position="after_anchor", anchor="<!-- marker:begin -->", text="late\n")
    good = apply([seed, late], base)["files"][BW]
    early = raises(lambda: apply([late], base), prj.ProjectionError)
    check("anchor_resolves_after_earlier_insert", "<!-- marker:begin -->\nlate\n" in good and early, f"early_fails={early}")


def test_projection_order_independent_for_parallel_phases() -> None:
    base = syn_state()
    base["files"][POLICY] = '{\n  "memory_write": "finalize-only",\n  "$comment": "c"\n}\n'
    p5 = row("o5", "P5", "insert", file=POLICY, position="after_anchor", anchor="{", text='  "native_plan_mode": "never",\n')
    p6 = row("o6", "P6", "insert", file=POLICY, position="after_anchor", anchor='  "memory_write": "finalize-only",', text='  "doubt_cycles_max": 3,\n')
    p7 = row("o7", "P7", "insert", file=POLICY, position="before_anchor", anchor='  "$comment": "c"', text='  "worktree_merge": {},\n')
    results = set()
    for perm in itertools.permutations([p5, p6, p7]):
        results.add(apply(list(perm), base)["files"][POLICY])
    # the control: overlapping whole-file replaces are order dependent, so the permutation harness would notice
    w5, w6 = (row("w5", "P5", "replace", file=POLICY, whole_file=True, text="five\n"), row("w6", "P6", "replace", file=POLICY, whole_file=True, text="six\n"))
    control = {apply(list(perm), base)["files"][POLICY] for perm in itertools.permutations([w5, w6])}
    # the real cut plan: P5, P6 and P7 applied in every order (rows in their given order, no canonical sorting) after P2-P4
    plan = real_cutplan()
    pre = real_state0()
    prj.apply_rows(pre, prj.select_rows(plan, ["P2", "P3", "P4"]))
    by_phase = {p: prj.select_rows(plan, [p]) for p in ("P5", "P6", "P7")}
    real = {}
    for perm in itertools.permutations(("P5", "P6", "P7")):
        st = copy.deepcopy(pre)
        prj.apply_rows(st, [r for p in perm for r in by_phase[p]])
        real[perm] = json.dumps({"files": st["files"], "readmap": st["readmap"]}, sort_keys=True)
    touched = {r.get("file") for p in by_phase for r in by_phase[p]}
    check("projection_order_independent_for_parallel_phases",
          len(results) == 1 and len(control) == 2 and len(real) == 6 and len(set(real.values())) == 1 and POLICY in touched and KER in touched,
          f"distinct={len(results)} control_distinct={len(control)} real_orders={len(real)} real_distinct={len(set(real.values()))}")


def test_anchor_must_match_exactly_one_line() -> None:
    base = syn_state()
    zero = raises(lambda: apply([row("e0", "P4", "insert", file=BW, position="after_anchor", anchor="nope", text="x\n")], base), prj.ProjectionError)
    base["files"][BW] += "line 1 of A\n"
    two = raises(lambda: apply([row("e2", "P4", "insert", file=BW, position="after_anchor", anchor="line 1 of A", text="x\n")], base), prj.ProjectionError)
    multi = apply([row("e3", "P4", "insert", file=BW, position="after_anchor", anchor="line 1 of A\nline 2 of A", text="x\n")], syn_state())
    tmp = Path(tempfile.mkdtemp())
    plan = {"rows": [row("c1", "P4", "insert", file=BW, position="after_anchor", anchor="nope", text="x\n")]}
    (tmp / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    res = run_cli("--project", str(tmp / "plan.json"), "--json")
    shutil.rmtree(tmp, ignore_errors=True)
    check("anchor_must_match_exactly_one_line", zero and two and "line 2 of A\nx\n" in multi["files"][BW] and res.returncode == 2,
          f"zero={zero} two={two} cli={res.returncode}")


def test_move_conserves_bytes() -> None:
    base = syn_state()
    total = sum(sizes_of(base).values())
    st = apply([row("v1", "P10", "move", src=BW, start_anchor="line 2 of A", end_anchor="line 4 of A", dst=REF + "moved.md",
                    pointer_text="")], base)
    moved = st["files"][REF + "moved.md"]
    check("move_conserves_bytes", sum(sizes_of(st).values()) == total and moved == "line 2 of A\nline 3 of A\nline 4 of A\n"
          and "line 3 of A" not in st["files"][BW], f"total={sum(sizes_of(st).values())} vs {total}")


def test_pointer_and_replacement_bytes_counted() -> None:
    base = syn_state()
    total = sum(sizes_of(base).values())
    pointer, header = "See moved.md for the lines.\n", "# Moved\n\n"
    st = apply([row("p1", "P10", "move", src=BW, start_anchor="line 2 of A", end_anchor="line 4 of A", dst=REF + "moved.md",
                    pointer_text=pointer, dst_header=header)], base)
    st2 = apply([row("p2", "P9", "dedupe", file=BW, start_anchor="line 2 of B", end_anchor="line 3 of B", replacement_text="ptr\n")], base)
    d2 = sum(sizes_of(st2).values()) - total
    check("pointer_and_replacement_bytes_counted",
          sum(sizes_of(st).values()) - total == len(pointer.encode()) + len(header.encode())
          and d2 == len("ptr\n") - len("line 2 of B\nline 3 of B\n") and "ptr\n" in st2["files"][BW],
          f"move={sum(sizes_of(st).values()) - total} dedupe={d2}")


def test_tiers_monotonic() -> None:
    st0 = real_state0()
    out = prj.project(real_cutplan(), st0)
    good = prj.check_monotonic(out)
    # normative text (keyword "must"), so the L3 tier cannot subtract it again and hide the added bytes
    bad_plan = {"rows": [row("c9", "P12", "contingency", contingency="C9", file=BW, position="end_of_file",
                             text="The router must keep this rule. " * 40 + "\n")]}
    bad = prj.check_monotonic(prj.project(bad_plan, syn_state()))
    check("tiers_monotonic", good == [] and bad != [], f"real_violations={good} control={bad}")


def test_phases_filter_rows() -> None:
    rows = [row("f2", "P2", "insert", file=BW, position="end_of_file", text="a\n"),
            row("f4", "P4", "insert", file=BW, position="end_of_file", text="b\n"),
            row("f5", "P5", "insert", file=BW, position="end_of_file", text="c\n")]
    picked = lambda ph: [r["id"] for r in prj.select_rows({"rows": rows}, ph)]  # noqa: E731
    out = prj.project({"rows": rows}, syn_state(), phases=["P4"])
    base_b = start_total(syn_state(), "BUILD")
    check("phases_filter_rows", picked(["P2", "P5"]) == ["f2", "f5"] and picked([]) == [] and picked(None) == ["f2", "f4", "f5"]
          and out["tiers"]["guaranteed"]["BUILD"]["max"] == base_b + 2, f"picked={picked(['P2', 'P5'])}")


def test_projection_deterministic() -> None:
    a = json.dumps(prj.project(real_cutplan(), real_state0()), sort_keys=True)
    b = json.dumps(prj.project(real_cutplan(), real_state0()), sort_keys=True)
    c1, c2 = (run_cli("--project", str(CUTPLAN), "--json").stdout for _ in range(2))
    check("projection_deterministic", a == b and c1 == c2 and c1.count("\n") == 1, "two runs differ")


def test_projected_readmap_equals_applied_rows() -> None:
    st0 = real_state0()
    st = copy.deepcopy(st0)
    rows = [r for r in real_cutplan()["rows"] if r["op"] == "readmap" and r.get("status") != "needs_anchor"]
    for r in sorted(rows, key=lambda r: int(r["phase"][1:])):
        prj.apply_readmap_row(st["readmap"], r)
    projected = json.loads(PROJECTED_MAP.read_text(encoding="utf-8"))
    new_files = {e["file"] for e in projected["files"]} - {e["file"] for e in json.loads(BASE_MAP.read_text(encoding="utf-8"))["files"]}
    check("projected_readmap_equals_applied_rows", projected == st["readmap"] and len(new_files) >= 10,
          f"new_files={len(new_files)}")


# ---------------------------------------------------------------------------------------------- cut plan tests

PHASES = {f"P{i}" for i in range(2, 14)}
OPS = {"move", "dedupe", "delete", "insert", "replace", "readmap", "contingency", "descriptions"}
CONDITIONS = {"always", "difference_17", "live_verified_enterplanmode"}


def row_problems(r: dict, projected_files: set) -> list:
    probs = []
    for k in ("id", "phase", "op", "condition"):
        if not isinstance(r.get(k), str) or not r.get(k):
            probs.append(f"missing {k}")
    if r.get("phase") not in PHASES:
        probs.append(f"phase {r.get('phase')!r} outside P2-P13")
    if r.get("op") not in OPS:
        probs.append(f"op {r.get('op')!r}")
    if r.get("condition") not in CONDITIONS:
        probs.append(f"condition {r.get('condition')!r}")
    if r.get("status") == "needs_anchor":
        if not (isinstance(r.get("note"), str) and r["note"].strip()):
            probs.append("needs_anchor without a note")
        return probs  # tolerated: documented, unprojected
    if r.get("status") not in (None, "drafted"):
        probs.append(f"status {r.get('status')!r}")
    if r.get("op") in ("insert", "replace", "contingency") and not isinstance(r.get("text"), str):
        probs.append("insert/replace/contingency without string text")
    if r.get("op") == "dedupe" and not isinstance(r.get("replacement_text"), str):
        probs.append("dedupe without replacement_text")
    if r.get("op") == "descriptions" and not (isinstance(r.get("text"), str) and str(r.get("file", "")).endswith("SKILL.md")):
        probs.append("descriptions row must name its SKILL.md file and carry text")
    if r.get("op") == "move" and r.get("dst") not in projected_files:
        probs.append(f"move dst {r.get('dst')!r} is not mapped by the projected read map")
    return probs


def test_cutplan_rows_complete() -> None:
    plan = real_cutplan()
    projected = json.loads(PROJECTED_MAP.read_text(encoding="utf-8"))
    mapped = {e["file"] for e in projected["files"]}
    ids = [r.get("id") for r in plan["rows"]]
    probs = {r.get("id"): row_problems(r, mapped) for r in plan["rows"]}
    bad = {k: v for k, v in probs.items() if v}
    controls = [row_problems({"id": "x", "phase": "P2C", "op": "insert", "condition": "always", "text": "x"}, mapped),
                row_problems({"id": "x", "phase": "P1", "op": "insert", "condition": "always", "text": "x"}, mapped),
                row_problems({"id": "x", "phase": "P4", "op": "insert", "condition": "always"}, mapped),
                row_problems({"id": "x", "phase": "P4", "op": "replace", "condition": "always", "status": "needs_anchor"}, mapped),
                row_problems({"id": "x", "phase": "P8", "op": "move", "condition": "always", "dst": "nowhere.md"}, mapped)]
    check("cutplan_rows_complete", len(ids) == len(set(ids)) and plan["rows"] and not bad and all(controls),
          f"dup={len(ids) - len(set(ids))} bad={dict(list(bad.items())[:3])} controls={[bool(c) for c in controls]}")


def test_cutplan_covers_relocation_phases() -> None:
    have = {r["phase"] for r in real_cutplan()["rows"] if r["op"] in ("move", "dedupe", "delete", "replace", "insert", "readmap")}
    missing = [p for p in ("P8", "P9", "P10", "P11", "P12") if p not in have]
    check("cutplan_covers_relocation_phases", not missing, f"missing={missing}")


def test_cutplan_covers_additive_phases() -> None:
    rows = real_cutplan()["rows"]
    have = {r["phase"] for r in rows if r["op"] in ("insert", "replace", "readmap")}
    p7 = [r for r in rows if r["phase"] == "P7" and r["op"] in ("insert", "replace") and r.get("file") == POLICY]
    missing = [p for p in ("P2", "P4", "P5", "P6", "P7") if p not in have]
    check("cutplan_covers_additive_phases", not missing and p7, f"missing={missing} p7={len(p7)}")


def _gated(path: str) -> bool:
    return path == POLICY or path == SKILL or bool(re.match(r"skills/(craftflow-router/references|_shared)/[^/]+\.md$", path)) \
        or bool(re.match(r"skills/[^/]+/SKILL\.md$", path))


def test_changed_gated_files_have_rows() -> None:
    budget = json.loads(BUDGET.read_text(encoding="utf-8"))
    last = (budget.get("merged_phases") or [None])[-1]
    repo = PLUGIN_ROOT.parents[2]
    probe = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "origin/main"], capture_output=True, text=True)
    if probe.returncode != 0:
        skip("changed_gated_files_have_rows", "no origin/main ref (CI must not skip)")
        return
    names = set()
    for args in (["diff", "--name-only", "origin/main...HEAD"], ["diff", "--name-only", "HEAD"], ["ls-files", "--others", "--exclude-standard"]):
        res = subprocess.run(["git", "-C", str(repo), *args, "--", "tools/craftflow-plugin/plugins/craftflow"], capture_output=True, text=True)
        if res.returncode != 0:
            skip("changed_gated_files_have_rows", "no origin/main ref: git diff failed (CI must not skip)")
            return
        names |= set(res.stdout.split())
    prefix = "tools/craftflow-plugin/plugins/craftflow/"
    changed = {n[len(prefix):] for n in names if n.startswith(prefix) and _gated(n[len(prefix):])}
    covered = set()
    for r in real_cutplan()["rows"]:
        if r["phase"] == last:
            covered |= {r[k] for k in ("file", "src", "dst") if isinstance(r.get(k), str)}
    missing = sorted(changed - covered)
    check("changed_gated_files_have_rows", not missing, f"changed gated files with no {last} row: {missing}")


# ---------------------------------------------------------------------------------------------- strict inputs (P2 REM-FIX 1)


def plan_file(rows: list, tmp: Path) -> Path:
    path = tmp / "plan.json"
    path.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    return path


def test_unknown_condition_name_is_an_error() -> None:
    """H1: a row naming a condition outside {always} + the budget flags used to read as silently false."""
    old_name = [row("h1", "P12", "delete", condition="live_verified", file=BW, start_anchor="line 2 of A")]
    exc = raises(lambda: prj.project({"rows": old_name}, syn_state()), prj.ProjectionError)
    canon = row("h2", "P12", "delete", condition="live_verified_enterplanmode", file=BW, start_anchor="line 2 of A")
    off = prj.project({"rows": [canon]}, syn_state(), conditions={"live_verified_enterplanmode": False})
    on = prj.project({"rows": [canon]}, syn_state(), conditions={"live_verified_enterplanmode": True})
    drop = len("line 2 of A\n")
    tmp = Path(tempfile.mkdtemp())
    res = run_cli("--project", str(plan_file(old_name, tmp)), "--json")
    shutil.rmtree(tmp, ignore_errors=True)
    real_conditions = {r["condition"] for r in real_cutplan()["rows"]}
    check("unknown_condition_name_is_an_error", exc and res.returncode == 2 and res.stdout == ""
          and off["effective"]["tier"]["BUILD"]["max"] - on["effective"]["tier"]["BUILD"]["max"] == drop
          and real_conditions <= {"always", "difference_17", "live_verified_enterplanmode"},
          f"raised={exc} cli={res.returncode} real={sorted(real_conditions)}")


def test_conditions_input_is_strict() -> None:
    """H1/M2: only difference_17 / live_verified_enterplanmode with true|false are accepted, as bools."""
    rows = [row("c1", "P4", "insert", file=BW, position="end_of_file", text="x\n", condition="difference_17")]
    str_flag = raises(lambda: prj.project({"rows": rows}, syn_state(), conditions={"difference_17": "false"}), prj.ProjectionError)
    unknown = raises(lambda: prj.project({"rows": rows}, syn_state(), conditions={"difference_18": True}), prj.ProjectionError)
    tmp = Path(tempfile.mkdtemp())
    plan = str(plan_file(rows, tmp))
    codes = {c: run_cli("--project", plan, "--conditions", c, "--json").returncode
             for c in ("difference_17=1", "difference_17=True", "difference_17=yes", "bogus=true", "difference_17",
                       "difference_17=true,difference_17=false")}
    good = run_cli("--project", plan, "--conditions", "difference_17=true,live_verified_enterplanmode=false", "--json")
    shutil.rmtree(tmp, ignore_errors=True)
    eff = json.loads(good.stdout)["effective_conditions"] if good.returncode == 0 else None
    check("conditions_input_is_strict", str_flag and unknown and set(codes.values()) == {2}
          and eff == {"difference_17": True, "live_verified_enterplanmode": False},
          f"str={str_flag} unknown={unknown} codes={codes} eff={eff}")


def test_effective_block_uses_the_recorded_conditions() -> None:
    """L6: router-projection.json's `effective` block follows the budget's recorded flags (difference_17 is true there)."""
    budget = json.loads(BUDGET.read_text(encoding="utf-8"))
    recorded = cli_json_args_project([])
    all_off = cli_json_args_project(["--conditions", "difference_17=false,live_verified_enterplanmode=false"])
    check("effective_block_uses_the_recorded_conditions",
          recorded["effective_conditions"] == budget["conditions"] and recorded["effective_conditions_source"] == "router-budget.json"
          and recorded["effective"]["tier"] != recorded["tiers"]["guaranteed"]
          and all_off["effective"]["tier"] == all_off["tiers"]["guaranteed"] and all_off["effective_conditions_source"] == "cli",
          f"recorded={recorded['effective_conditions']} src={recorded['effective_conditions_source']}")


def test_unknown_phase_ids_are_errors() -> None:
    """M2: a row phase or a --phases id outside P2-P13 (+ the row-less P2C) used to select nothing silently."""
    rows = lambda ph: {"rows": [row("u1", ph, "insert", file=BW, position="end_of_file", text="a\n")]}  # noqa: E731
    bad_rows = {ph: raises(lambda ph=ph: prj.project(rows(ph), syn_state()), prj.ProjectionError) for ph in ("P14", "p4", "P1", "P2C")}
    bad_arg = raises(lambda: prj.project(rows("P4"), syn_state(), phases=["P99"]), prj.ProjectionError)
    rowless = prj.project(rows("P4"), syn_state(), phases=["P4", "P2C"])
    p99 = run_cli("--project", str(CUTPLAN), "--phases", "P99", "--json")
    p2c = run_cli("--project", str(CUTPLAN), "--phases", "P2,P2C", "--json")
    check("unknown_phase_ids_are_errors", all(bad_rows.values()) and bad_arg and rowless["rows_applied"] == 1 and p99.returncode == 2
          and p2c.returncode == 0, f"rows={bad_rows} arg={bad_arg} p99={p99.returncode} p2c={p2c.returncode}")


def test_monotonic_compares_per_file() -> None:
    """M4: totals can stay ordered while one file's bytes rise from a stricter tier to a looser one."""
    out = prj.project(real_cutplan(), real_state0(), phases=["P2"])
    clean = prj.check_monotonic(out)
    bent = copy.deepcopy(out)
    f = "skills/craftflow-router/references/build-workflow.md"
    bent["per_file"]["l3_upper_bound"][f] = bent["per_file"]["conditional"][f] + 5
    bad = prj.check_monotonic(bent)
    check("monotonic_compares_per_file", clean == [] and any(f in b for b in bad), f"clean={clean} bad={bad}")


def test_unanchored_rows_and_bias_are_disclosed() -> None:
    """M3: unanchored rows carry a direction and a size estimate; the projection reports them and the net bias."""
    out = prj.project(real_cutplan(), real_state0())
    un = {u["id"]: u for u in out["unanchored"]}
    keys_ok = all(isinstance(u, dict) and {"id", "phase", "direction", "est_bytes", "note"} <= set(u) and u["direction"] in ("add", "cut")
                  and isinstance(u["est_bytes"], int) and u["est_bytes"] > 0 for u in out["unanchored"])
    b = out["bias"]
    wf_ok = all(isinstance(b.get(w), int) for w in ("BUILD", "PLAN", "DEBUG", "REVIEW")) and b["kernel"] == 290
    undisclosed = row("n1", "P4", "insert", file=BW, position="end_of_file", text="x\n", status="needs_anchor", note="n")
    refuses = raises(lambda: prj.project({"rows": [undisclosed]}, syn_state()), prj.ProjectionError)
    sized = dict(undisclosed, direction="cut", est_bytes=40, affects=["PLAN"])
    small = prj.project({"rows": [sized]}, syn_state())
    on_disk = json.loads((FIXTURES / "router-projection.json").read_text(encoding="utf-8"))
    check("unanchored_rows_and_bias_are_disclosed", keys_ok and {"p3-kernel-when-to-use", "p10-xref-unqualified-moved", "p12-c4-second-rationale-pass"} <= set(un)
          and un["p3-kernel-when-to-use"]["direction"] == "add" and un["p12-c4-second-rationale-pass"]["direction"] == "cut"
          and wf_ok and refuses and small["bias"]["PLAN"] == -40 and small["bias"]["BUILD"] == 0
          and on_disk["unanchored"] == out["unanchored"] and on_disk["bias"] == out["bias"],
          f"keys_ok={keys_ok} wf_ok={wf_ok} refuses={refuses}")


def test_projector_refuses_rows_that_lose_bytes_silently() -> None:
    """Hunter item 2: a move to an unmapped file, a whole-file replace of an unknown file and a missing table marker."""
    st = syn_state()
    move = row("g1", "P10", "move", src=BW, start_anchor="line 2 of A", end_anchor="line 4 of A", dst=REF + "unmapped.md", pointer_text="")
    unmapped = raises(lambda: prj.project({"rows": [move]}, st), prj.ProjectionError)
    new_entry = {"file": REF + "unmapped.md", "class": "stage", "workflows": ["BUILD"], "trigger": "t", "happy_path": False}
    mapped_row = row("g2", "P10", "readmap", action="add", row=new_entry)
    fine = prj.project({"rows": [mapped_row, move]}, st)
    whole = row("g3", "P5", "replace", file=REF + "ghost.md", whole_file=True, text="x\n")
    ghost = raises(lambda: apply([whole]), prj.ProjectionError)
    nomark = syn_state()
    nomark["files"][KER] = "# Kernel\n\nno table here\n"
    w_entry = {"file": REF + "w.md", "class": "stage", "workflows": ["BUILD"], "trigger": "t", "happy_path": False}
    add = row("g4", "P8", "readmap", action="add", row=w_entry)
    mk = row("g5", "P8", "insert", file=REF + "w.md", position="new_file", text="# W\n")
    raised = raises(lambda: prj.project({"rows": [mk, add]}, nomark, phases=["P2", "P8"]), prj.ProjectionError)
    said = prj.project({"rows": [mk, add]}, nomark, phases=["P8"])
    check("projector_refuses_rows_that_lose_bytes_silently", unmapped and fine["rows_applied"] == 2 and ghost and raised
          and any("re-render" in n for n in said["notes"]), f"unmapped={unmapped} ghost={ghost} raised={raised} notes={said.get('notes')}")


def test_measurer_inputs_are_strict() -> None:
    """L2-L4: unreadable policy, malformed budget and --ref without --readmap are errors, never silent defaults."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "config").mkdir()
    (tmp / "config" / "policy.json").mkdir()  # present but not readable as a file
    unreadable = raises(lambda: rs.policy_bytes(tmp, None), rs.ReadsetError)
    absent = rs.policy_bytes(Path(tempfile.mkdtemp()), None) == {}
    bad = tmp / "bad-budget.json"
    bad.write_text("{not json", encoding="utf-8")
    nokey = tmp / "nokey-budget.json"
    nokey.write_text("{}", encoding="utf-8")
    codes = [run_cli("--workflow", "PLAN", "--budget", str(p), "--json").returncode for p in (bad, nokey)]
    missing_budget = run_cli("--workflow", "PLAN", "--budget", str(tmp / "none.json"), "--json").returncode
    no_map = run_cli("--workflow", "PLAN", "--ref", baseline_sha(), "--json")
    shutil.rmtree(tmp, ignore_errors=True)
    check("measurer_inputs_are_strict", unreadable and absent and codes == [2, 2] and missing_budget == 0 and no_map.returncode == 2
          and "--readmap" in no_map.stderr, f"unreadable={unreadable} absent={absent} codes={codes} missing={missing_budget} ref={no_map.returncode}")


def test_reads_are_byte_exact() -> None:
    """L5: bytes are decoded as UTF-8 and counted raw; CR bytes or invalid UTF-8 fail closed instead of being normalised."""
    import craftflow_router_readset_guard as grd
    tmp = Path(tempfile.mkdtemp())

    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(tmp), "-c", "user.email=t@t", "-c", "user.name=t", *a], capture_output=True, check=True)

    git("init", "-q")
    (tmp / "ok.md").write_bytes("café → x\n".encode("utf-8"))
    (tmp / "cr.md").write_bytes(b"a\r\nb\r\n")
    (tmp / "bin.md").write_bytes(b"\xff\xfe\n")
    git("add", ".")
    git("commit", "-q", "-m", "x")
    good = grd.read_exact(tmp, "ok.md", "HEAD")
    wt = grd.read_exact(tmp, "ok.md", None)
    cr = [raises(lambda r=r: grd.read_exact(tmp, "cr.md", r), rs.ReadsetError) for r in ("HEAD", None)]
    inval = [raises(lambda r=r: grd.read_exact(tmp, "bin.md", r), rs.ReadsetError) for r in ("HEAD", None)]
    gone = raises(lambda: grd.read_exact(tmp, "nope.md", "HEAD"), rs.ReadsetError)
    shutil.rmtree(tmp, ignore_errors=True)
    check("reads_are_byte_exact", good == wt == "café → x\n" and len(good.encode()) == 12 and all(cr) and all(inval) and gone,
          f"good={good!r} cr={cr} invalid={inval} gone={gone}")


def main() -> int:
    for name in sorted(n for n in globals() if n.startswith("test_")):
        try:
            globals()[name]()
        except Exception as exc:  # noqa: BLE001 - every unexpected error is a failure of that test, reported by name
            fail(name, f"{type(exc).__name__}: {exc}")
    print()
    for e in _errors:
        print(e)
    print(f"Results: {_passes} passed, {len(_errors)} failed, {_skips} skipped")
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
