#!/usr/bin/env python3
"""Tests for craftflow_router_context_impact.py (plan P13 task 1: before/after impact report).

Run: python3 tests/fixtures/test_router_context_impact.py
The renderer is pure (`render(data, ...)`), so most checks feed it a hand-built data dict; the real-tree checks read the
frozen baseline (via the lock file) and the live router, and the CLI checks run the script as a subprocess.
Python 3.9 compatible.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
sys.path.insert(0, str(PLUGIN_ROOT / "tests" / "live"))

import craftflow_router_context_impact as imp  # noqa: E402
import router_context_cut_roundtrip as drv  # noqa: E402

FIXTURES = PLUGIN_ROOT / "tests" / "fixtures"
SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_router_context_impact.py"
LOCK = FIXTURES / "router-baseline.lock.json"
BUDGET = FIXTURES / "router-budget.json"

_passes = 0
_errors: list = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print("  PASS: " + name)


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def check(name: str, cond: bool, reason: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, reason or "condition false")


def baseline_sha() -> str:
    return json.loads(LOCK.read_text(encoding="utf-8"))["baseline_sha"]


def good_lv(**over) -> dict:
    """A passing live-lane summary with the driver's real keys."""
    lv = {"load_probe": "PASS", "lv2": "PASS", "lv3": "PASS", "lv4": "PASS", "lv5": "PASS", "evidence_class": "A"}
    lv.update(over)
    return lv


def full_hv() -> dict:
    return {h: {"baseline": {"a": 1, "b": 2}, "candidate": {"a": 1, "b": 2}} for h in ("HV-1", "HV-2")}


def expect_impact_error(name: str, fn, needle: str = "") -> None:
    try:
        fn()
    except imp.ImpactError as exc:
        check(name, needle in str(exc), f"error did not mention {needle!r}: {exc}")
    except Exception as exc:  # noqa: BLE001
        fail(name, f"wrong exception {type(exc).__name__}: {exc}")
    else:
        fail(name, "no ImpactError")


def synthetic() -> dict:
    """A small hand-built `collect` result: BUILD 1000 -> 400, PLAN 900 -> 500."""
    def g(key, label, base, goal, enforced, measured, proj):
        return {"key": key, "label": label, "baseline": base, "goal": goal, "enforced": enforced, "measured": measured,
                "projection": proj}
    return {
        "baseline_ref": "a" * 40, "head_sha": "b" * 40, "head_dirty": False, "date": "2026-10-21",
        "rows": [
            {"workflow": "BUILD", "variant": "standard", "base_start": 1000, "head_start": 400, "base_happy": 1200, "head_happy": 700},
            {"workflow": "BUILD", "variant": "fast_path", "base_start": 1000, "head_start": 380, "base_happy": 1200, "head_happy": 650},
            {"workflow": "PLAN", "variant": "standard", "base_start": 900, "head_start": 500, "base_happy": 1100, "head_happy": 800},
        ],
        "build_definitions": {"start_files": 1000, "with_harness_self_checks": 1050, "harness_bytes": 50},
        "gated": [
            g("readset.BUILD", "BUILD read set", 1000, 300, 450, 400, 420),
            g("kernel_bytes", "Kernel (SKILL.md)", 600, 250, 300, 280, 290),
        ],
        "per_file": [
            {"file": "skills/a.md", "base": 600, "head": 280},
            {"file": "skills/gone.md", "base": 100, "head": None},
            {"file": "skills/new.md", "base": None, "head": 40},
        ],
        "descriptions": {"base": 5000, "head": 3793, "goal": 4000, "enforced": 5081, "projection": 3793},
        "inventory": {"present": 10, "policy_mapped": 2, "reworded": 3, "rationale": 1, "unmapped_removed": 0,
                      "unreachable": 0, "rows_missing_review": 0, "baseline_units": 16},
        "inventory_exit": 0,
    }


def test_fmt_thousands_separator() -> None:
    check("fmt_thousands_separator", imp.fmt(214055) == "214,055" and imp.fmt(None) == "n/a", repr(imp.fmt(214055)))


def test_render_states_bytes_are_not_tokens() -> None:
    text = imp.render(synthetic())
    check("render_discloses_bytes_not_tokens", "bytes are not tokens" in text.lower(), "disclosure sentence missing")
    check("render_discloses_same_tool_for_baseline", "same tool" in text.lower() and "frozen" in text.lower(),
          "baseline same-tool/frozen-map disclosure missing")


def test_render_both_build_definitions() -> None:
    text = imp.render(synthetic())
    check("render_has_both_build_baselines", "1,000" in text and "1,050" in text and "harness-self-checks" in text,
          "both BUILD baseline definitions must appear")


def test_render_per_workflow_before_after() -> None:
    text = imp.render(synthetic())
    row = [ln for ln in text.splitlines() if ln.startswith("| BUILD | fast_path")]
    check("render_variant_row_numbers", len(row) == 1 and "1,000" in row[0] and "380" in row[0] and "650" in row[0], str(row))
    check("render_delta_percent", "-60.0%" in text, "BUILD standard 1000 -> 400 is -60.0%")


def test_render_three_columns_and_gap() -> None:
    text = imp.render(synthetic())
    head = [ln for ln in text.splitlines() if ln.startswith("| Gated value")]
    check("render_gated_columns", len(head) == 1 and all(c in head[0] for c in ("Goal", "Enforced", "Measured", "Projection")), str(head))
    check("render_gap_stated_plainly", "100 B above the goal" in text or "above the goal" in text, "goal gap sentence missing")
    check("render_goal_not_reached", "not reached" in text.lower(), "must say plainly the goal is not reached")


def test_render_gap_when_goal_met() -> None:
    d = synthetic()
    d["gated"][0]["measured"] = 250
    text = imp.render(d)
    check("render_goal_met_wording", "Goal met" in text, "a measured value at or below the goal is reported as met")


def test_render_per_file_table_marks_added_and_removed() -> None:
    text = imp.render(synthetic())
    gone = [ln for ln in text.splitlines() if "skills/gone.md" in ln]
    new = [ln for ln in text.splitlines() if "skills/new.md" in ln]
    check("render_removed_file", len(gone) == 1 and "removed" in gone[0], str(gone))
    check("render_added_file", len(new) == 1 and "added" in new[0], str(new))


def test_render_inventory_accounting() -> None:
    text = imp.render(synthetic())
    check("render_inventory_counts", all(s in text for s in ("present", "policy-mapped", "reworded", "rationale", "unmapped")),
          "inventory accounting counts missing")
    check("render_unmapped_zero", "unmapped_removed: 0" in text or "unmapped removed | 0" in text.lower(), "unmapped=0 must be explicit")


def test_render_hv_cells_pending_by_default() -> None:
    text = imp.render(synthetic())
    cells = [ln for ln in text.splitlines() if ln.startswith("| HV-1") or ln.startswith("| HV-2")]
    check("render_hv_rows", len(cells) == 2, str(cells))
    check("render_hv_all_pending", all(ln.count("PENDING") == 4 for ln in cells), str(cells))
    check("render_hv_no_invented_numbers", not any(ch.isdigit() for ln in cells for ch in ln.replace("HV-1", "").replace("HV-2", "")),
          "HV rows must carry no digits other than the HV id while pending")


def test_render_hv_cells_filled_from_captures() -> None:
    caps = {"HV-1": {"baseline": {"a": 51000, "b": 70000}, "candidate": {"a": 30000, "b": 49000}}}
    text = imp.render(synthetic(), hv=caps)
    row1 = [ln for ln in text.splitlines() if ln.startswith("| HV-1")][0]
    row2 = [ln for ln in text.splitlines() if ln.startswith("| HV-2")][0]
    check("render_hv_filled", "51,000" in row1 and "49,000" in row1 and "PENDING" not in row1, row1)
    check("render_hv_other_row_still_pending", row2.count("PENDING") == 4, row2)


def test_render_lv_pending_and_filled() -> None:
    check("render_lv_pending", "PENDING" in imp.render(synthetic()).split("## Live verification")[1].split("##")[0], "LV section pending by default")
    lv = good_lv(lv3="LIMITATION:enterplanmode_not_offered")
    text = imp.render(synthetic(), lv=lv)
    sect = text.split("## Live verification")[1].split("\n## ")[0]
    rows = [ln for ln in sect.splitlines() if ln.startswith("| LV-")]
    check("render_lv_filled", len(rows) == 5 and "LIMITATION:enterplanmode_not_offered" in sect and "missing" not in sect
          and "PENDING" not in sect, sect[:500])
    check("render_lv_limitation_sentence_conditional", "limitation" in sect.lower() and "Evidence class: A" in sect, sect[-400:])


def test_render_lv_sentence_follows_results() -> None:
    sect = imp.render(synthetic(), lv=good_lv()).split("## Live verification")[1].split("\n## ")[0]
    check("render_lv_all_pass_no_limitation_claim", "LIMITATION" not in sect, sect[-300:])
    bad = imp.render(synthetic(), lv=good_lv(lv2="FAIL:x", load_probe="FAIL:rc=1"))
    sect = bad.split("## Live verification")[1].split("\n## ")[0]
    check("render_lv_fail_rows_shown", "FAIL:x" in sect and "FAIL:rc=1" in sect, sect[:400])


def test_render_addendum_is_draft() -> None:
    text = imp.render(synthetic())
    sect = text.split("## Draft ADR-0073 / SPEC-0034 addendum")[1]
    check("render_addendum_marked_draft", "not applied" in sect.lower() and "status: accepted" not in sect.split("\n## ")[0].replace("not set", ""),
          "addendum must say it is a draft and must not flip status")


def test_hv_loader_rejects_bad_shapes() -> None:
    try:
        imp.load_hv({"HV-9": {}})
    except imp.ImpactError:
        ok("hv_loader_rejects_unknown_id")
    else:
        fail("hv_loader_rejects_unknown_id", "no error")
    try:
        imp.load_hv({"HV-1": {"baseline": {"a": "lots"}}})
    except imp.ImpactError:
        ok("hv_loader_rejects_non_integer")
    else:
        fail("hv_loader_rejects_non_integer", "no error")
    try:
        imp.load_hv({"HV-1": {"baseline": {"a": True}}})
    except imp.ImpactError:
        ok("hv_loader_rejects_bool")
    else:
        fail("hv_loader_rejects_bool", "bool accepted as a byte count")


def test_hv_loader_rejects_zero_reading() -> None:
    expect_impact_error("hv_loader_rejects_zero", lambda: imp.load_hv({"HV-1": {"baseline": {"a": 0}}}))
    expect_impact_error("hv_loader_rejects_negative", lambda: imp.load_hv({"HV-1": {"baseline": {"a": -5}}}))
    check("hv_loader_accepts_one", imp.load_hv({"HV-1": {"baseline": {"a": 1}}}) == {"HV-1": {"baseline": {"a": 1}}}, "n=1 is valid")


def test_lv_loader_accepts_real_summary() -> None:
    lv = good_lv(lv3="LIMITATION:enterplanmode_not_offered", lv5="FAIL:src_a_txt_not_committed(head=[])")
    check("lv_loader_accepts_pass_fail_limitation", imp.load_lv(lv) == lv, "valid summary must round-trip")
    check("lv_loader_accepts_class_b", imp.load_lv(good_lv(evidence_class="B-SIMULATED"))["evidence_class"] == "B-SIMULATED", "")


def test_lv_loader_rejects_bad_shapes() -> None:
    expect_impact_error("lv_loader_rejects_list", lambda: imp.load_lv([]))
    expect_impact_error("lv_loader_rejects_empty_object", lambda: imp.load_lv({}))
    missing = good_lv()
    del missing["lv4"]
    expect_impact_error("lv_loader_rejects_missing_key", lambda: imp.load_lv(missing))
    expect_impact_error("lv_loader_rejects_unknown_key", lambda: imp.load_lv(good_lv(lv1="PASS")))
    expect_impact_error("lv_loader_rejects_bare_fail", lambda: imp.load_lv(good_lv(load_probe="FAIL")))
    expect_impact_error("lv_loader_rejects_not_run", lambda: imp.load_lv(good_lv(lv2="NOT_RUN")))
    expect_impact_error("lv_loader_rejects_limitation_outside_lv3", lambda: imp.load_lv(good_lv(lv4="LIMITATION:enterplanmode_not_offered")))
    expect_impact_error("lv_loader_rejects_non_string", lambda: imp.load_lv(good_lv(lv5=True)))
    expect_impact_error("lv_loader_rejects_bad_class", lambda: imp.load_lv(good_lv(evidence_class="C")))


def test_collect_baseline_is_frozen_214055() -> None:
    data = imp.collect(PLUGIN_ROOT, baseline_sha(), run_inventory=False)
    build = [r for r in data["rows"] if r["workflow"] == "BUILD" and r["variant"] == "standard"][0]
    check("collect_baseline_build_214055", build["base_start"] == 214055, str(build))
    check("collect_audit_definition_220063", data["build_definitions"]["with_harness_self_checks"] == 220063,
          str(data["build_definitions"]))
    plan = [r for r in data["rows"] if r["workflow"] == "PLAN"][0]
    check("collect_baseline_plan_199880", plan["base_start"] == 199880, str(plan))


def test_collect_head_matches_readset_tool() -> None:
    import craftflow_router_readset as rs
    data = imp.collect(PLUGIN_ROOT, baseline_sha(), run_inventory=False)
    want = rs.compute("BUILD", PLUGIN_ROOT, budget_path=Path("/nonexistent"))["start_total"]
    got = [r for r in data["rows"] if r["workflow"] == "BUILD" and r["variant"] == "standard"][0]["head_start"]
    check("collect_head_equals_readset_tool_self_consistency", got == want, f"{got} != {want}")
    # P13 task 4 (EnterPlanMode prose deleted, -680 B in every start set): 97,407 -> 96,727
    check("collect_head_build_standard_literal_96727", got == 96727, f"BUILD standard start_total is {got}; the BUILD standard literal is 96,727")


def test_collect_gated_measured_within_enforced() -> None:
    data = imp.collect(PLUGIN_ROOT, baseline_sha(), run_inventory=False)
    bad = [g["key"] for g in data["gated"] if g["measured"] > g["enforced"]]
    check("collect_measured_within_enforced_caps", not bad, f"over the enforced cap: {bad}")
    keys = {g["key"] for g in data["gated"]}
    want = {"kernel_bytes", "protocol_bytes", "reference_max_bytes", "description_chars",
            "readset.BUILD", "readset.PLAN", "readset.DEBUG", "readset.REVIEW"}
    check("collect_gated_keys_complete", keys == want, str(keys ^ want))


def test_collect_goal_and_enforced_come_from_budget() -> None:
    b = json.loads(BUDGET.read_text(encoding="utf-8"))
    data = imp.collect(PLUGIN_ROOT, baseline_sha(), run_inventory=False)
    g = [x for x in data["gated"] if x["key"] == "readset.BUILD"][0]
    check("collect_goal_from_targets", g["goal"] == b["targets"]["readset"]["BUILD"], str(g))
    check("collect_enforced_from_caps", g["enforced"] == b["enforced_caps"]["readset"]["BUILD"], str(g))


def test_collect_unknown_baseline_raises() -> None:
    try:
        imp.collect(PLUGIN_ROOT, "f" * 40, run_inventory=False)
    except imp.ImpactError:
        ok("collect_unknown_baseline_raises")
    else:
        fail("collect_unknown_baseline_raises", "no ImpactError for a commit that does not exist")


def test_collect_inventory_accounting() -> None:
    data = imp.collect(PLUGIN_ROOT, baseline_sha())
    inv = data["inventory"]
    check("collect_inventory_exit_zero", data["inventory_exit"] == 0, str(data["inventory_exit"]))
    check("collect_inventory_unmapped_zero", inv["unmapped_removed"] == 0 and inv["unreachable"] == 0 and inv["rows_missing_review"] == 0, str(inv))


def run_cli(*args: str):
    p = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout, p.stderr


def test_cli_smoke_writes_report() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "x.md"
        code, so, se = run_cli("--baseline", baseline_sha(), "--out", str(out))
        text = out.read_text(encoding="utf-8") if out.is_file() else ""
        check("cli_smoke_exit_zero", code == 0, se[-300:])
        check("cli_smoke_report_has_numbers", "214,055" in text and "220,063" in text and "PENDING" in text, text[:200])
        check("cli_smoke_summary_json", json.loads(so.strip().splitlines()[-1]).get("out") == str(out), so[-200:])


def test_cli_unknown_baseline_exits_2() -> None:
    with tempfile.TemporaryDirectory() as td:
        code, _so, se = run_cli("--baseline", "f" * 40, "--out", str(Path(td) / "x.md"))
        check("cli_unknown_baseline_exit_2", code == 2 and "error" in se.lower(), f"{code} {se[-200:]}")
        check("cli_unknown_baseline_writes_nothing", not (Path(td) / "x.md").exists(), "report written despite error")


def test_cli_bad_hv_file_exits_2() -> None:
    with tempfile.TemporaryDirectory() as td:
        hv = Path(td) / "hv.json"
        hv.write_text(json.dumps({"HV-1": {"baseline": {"a": "x"}}}), encoding="utf-8")
        code, _so, se = run_cli("--baseline", baseline_sha(), "--out", str(Path(td) / "x.md"), "--hv", str(hv))
        check("cli_bad_hv_exit_2", code == 2, f"{code} {se[-200:]}")


def test_render_status_draft_until_hv_complete() -> None:
    check("render_status_draft_when_pending", "Status: DRAFT" in imp.render(synthetic()), "pending HV cells must mark the report DRAFT")
    check("render_status_complete_needs_lv", "Status: COMPLETE" in imp.render(synthetic(), hv=full_hv(), lv=good_lv()), "HV full and LV all pass")


def status_line(text: str) -> str:
    return [ln for ln in text.splitlines() if ln.startswith("- Status:")][0]


def test_render_status_not_complete_with_only_hv() -> None:
    line = status_line(imp.render(synthetic(), hv=full_hv()))
    check("render_status_draft_hv_only", line.startswith("- Status: DRAFT") and "LV" in line, line)


def test_render_status_draft_on_failed_lv() -> None:
    for name, lv in (("fail_row", good_lv(lv2="FAIL:x")), ("fail_probe", good_lv(load_probe="FAIL:rc=1")),
                     ("class_b", good_lv(evidence_class="B-SIMULATED"))):
        line = status_line(imp.render(synthetic(), hv=full_hv(), lv=lv))
        check("render_status_draft_" + name, line.startswith("- Status: DRAFT"), line)
    ok_limit = imp.render(synthetic(), hv=full_hv(), lv=good_lv(lv3="LIMITATION:enterplanmode_not_offered"),
                          notes={"outcomes": {"HV-4": "PASS: denied interactively"}})
    check("render_status_complete_with_lv3_limitation", "Status: COMPLETE" in ok_limit, "the LV-3 limitation plus an HV-4 PASS is allowed")


def test_render_status_draft_when_inventory_failed() -> None:
    d = synthetic()
    d["inventory_exit"] = 1
    line = status_line(imp.render(d, hv=full_hv(), lv=good_lv()))
    check("render_status_draft_on_inventory_failure", line.startswith("- Status: DRAFT") and "inventory" in line.lower(), line)
    d = synthetic()
    d["inventory"], d["inventory_exit"] = None, 3
    line = status_line(imp.render(d, hv=full_hv(), lv=good_lv()))
    check("render_status_draft_on_unparsable_inventory", line.startswith("- Status: DRAFT") and "inventory" in line.lower(), line)


def test_render_non_lock_baseline_marked() -> None:
    d = synthetic()
    d["non_lock_baseline"] = True
    text = imp.render(d, hv=full_hv(), lv=good_lv())
    check("render_non_lock_header", "NON-LOCK BASELINE" in text.split("## Disclosures")[0], text[:400])
    check("render_non_lock_draft", status_line(text).startswith("- Status: DRAFT"), status_line(text))


def test_gap_uses_the_right_unit() -> None:
    d = synthetic()
    d["gated"].append({"key": "description_chars", "label": "Skill description sum (chars)", "baseline": 5000, "goal": 4000,
                       "enforced": 5081, "measured": 4207, "projection": 4100})
    row = [ln for ln in imp.render(d).splitlines() if ln.startswith("| Skill description sum")][0]
    check("gap_description_in_chars", "207 chars above the goal" in row and "207 B" not in row, row)
    d["gated"][2]["measured"] = 3900
    row = [ln for ln in imp.render(d).splitlines() if ln.startswith("| Skill description sum")][0]
    check("gap_description_met_in_chars", "100 chars under" in row, row)


def head_sha() -> str:
    return subprocess.run(["git", "-C", str(PLUGIN_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def test_check_baseline_resolves_against_lock() -> None:
    sha = baseline_sha()
    got = imp.check_baseline(PLUGIN_ROOT, sha, allow_non_lock=False)
    check("check_baseline_lock_ok", got == (sha, False), str(got))
    head = head_sha()
    expect_impact_error("check_baseline_non_lock_rejected", lambda: imp.check_baseline(PLUGIN_ROOT, head, allow_non_lock=False))
    check("check_baseline_non_lock_allowed", imp.check_baseline(PLUGIN_ROOT, head, allow_non_lock=True) == (head, True), "")
    expect_impact_error("check_baseline_unknown_commit", lambda: imp.check_baseline(PLUGIN_ROOT, "f" * 40, allow_non_lock=True))


def test_cli_non_lock_baseline_exits_2_unless_allowed() -> None:
    with tempfile.TemporaryDirectory() as td:
        code, _so, se = run_cli("--baseline", head_sha(), "--out", str(Path(td) / "x.md"), "--no-inventory")
        check("cli_non_lock_exit_2", code == 2 and "lock" in se.lower(), f"{code} {se[-200:]}")
        check("cli_non_lock_writes_nothing", not (Path(td) / "x.md").exists(), "report written")


def test_cli_lv_exit_2_on_bad_shapes() -> None:
    with tempfile.TemporaryDirectory() as td:
        for name, payload in (("list", []), ("empty", {}), ("missing_keys", {"lv2": "FAIL:x", "load_probe": "FAIL"})):
            lv = Path(td) / (name + ".json")
            lv.write_text(json.dumps(payload), encoding="utf-8")
            code, _so, se = run_cli("--baseline", baseline_sha(), "--out", str(Path(td) / "x.md"), "--no-inventory", "--lv", str(lv))
            check("cli_lv_" + name + "_exit_2", code == 2 and "Traceback" not in se, f"{code} {se[-200:]}")
        hv = Path(td) / "hv.json"
        hv.write_text("[]", encoding="utf-8")
        code, _so, se = run_cli("--baseline", baseline_sha(), "--out", str(Path(td) / "x.md"), "--no-inventory", "--hv", str(hv))
        check("cli_hv_list_exit_2", code == 2 and "Traceback" not in se, f"{code} {se[-200:]}")


def test_cli_zero_hv_rejected_and_failing_lv_never_complete() -> None:
    with tempfile.TemporaryDirectory() as td:
        hv = Path(td) / "hv.json"
        hv.write_text(json.dumps({h: {s: {"a": 0, "b": 0} for s in ("baseline", "candidate")} for h in ("HV-1", "HV-2")}), encoding="utf-8")
        code, _so, se = run_cli("--baseline", baseline_sha(), "--out", str(Path(td) / "x.md"), "--no-inventory", "--hv", str(hv))
        check("cli_zero_hv_exit_2", code == 2, f"{code} {se[-200:]}")
        lv = Path(td) / "lv.json"
        lv.write_text(json.dumps(good_lv(lv2="FAIL:x", load_probe="FAIL:rc=1")), encoding="utf-8")
        hv.write_text(json.dumps(full_hv()), encoding="utf-8")
        code, _so, se = run_cli("--baseline", baseline_sha(), "--out", str(Path(td) / "x.md"), "--no-inventory", "--hv", str(hv), "--lv", str(lv))
        text = (Path(td) / "x.md").read_text(encoding="utf-8") if (Path(td) / "x.md").is_file() else ""
        check("cli_failing_lv_stays_draft", code == 0 and "Status: DRAFT" in text and "Status: COMPLETE" not in text, f"{code} {se[-200:]}")


def test_timeout_maps_to_exit_2() -> None:
    real = subprocess.run

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=60)
    subprocess.run = boom
    try:
        with tempfile.TemporaryDirectory() as td:
            err = tempfile.TemporaryFile("w+")
            saved, sys.stderr = sys.stderr, err
            try:
                code = imp.main(["--baseline", baseline_sha(), "--out", str(Path(td) / "x.md"), "--no-inventory"])
            finally:
                sys.stderr = saved
            err.seek(0)
            msg = err.read()
            check("timeout_exit_2", code == 2 and "timed out" in msg.lower() and not (Path(td) / "x.md").exists(), f"{code} {msg[-200:]}")
    finally:
        subprocess.run = real


def test_collect_missing_or_empty_readmap_baseline() -> None:
    with tempfile.TemporaryDirectory(dir=str(PLUGIN_ROOT / "tests")) as td:  # inside the repo so the baseline sha still resolves
        root = Path(td)
        (root / "tests" / "fixtures").mkdir(parents=True)
        (root / "config").mkdir()
        # the lock (and the repo for sha resolution) must resolve, so a failure below is about the read map, not the lock
        shutil.copy(LOCK, root / "tests" / "fixtures" / "router-baseline.lock.json")
        expect_impact_error("collect_missing_readmap_baseline", lambda: imp.collect(root, baseline_sha(), run_inventory=False), "readmap")
        (root / "tests" / "fixtures" / "router-readmap.baseline.json").write_text("", encoding="utf-8")
        expect_impact_error("collect_empty_readmap_baseline", lambda: imp.collect(root, baseline_sha(), run_inventory=False), "readmap")
        (root / "tests" / "fixtures" / "router-readmap.baseline.json").write_text("{}", encoding="utf-8")
        expect_impact_error("collect_empty_object_readmap_baseline", lambda: imp.collect(root, baseline_sha(), run_inventory=False), "readmap")


def test_git_env_is_scrubbed() -> None:
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "r"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
        keys = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
        saved = {k: os.environ.get(k) for k in keys}
        for k in keys:
            os.environ[k] = str(Path(td) / "nope")
        try:
            out = drv.git(repo, "rev-parse", "--show-toplevel").strip()
            check("driver_git_ignores_ambient_git_env", Path(out).resolve() == repo.resolve(), out)
            sha, _dirty = imp._head_sha(PLUGIN_ROOT)
            check("impact_head_sha_ignores_ambient_git_env", len(sha) == 40, sha)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


STREAM_OK = '{"type":"system","subtype":"init"}\n{"type":"result","subtype":"success"}\n'


def test_driver_lv3_requires_a_real_session() -> None:
    check("driver_session_completed_ok", drv.session_completed(0, STREAM_OK), "rc 0 with init/result is a real session")
    check("driver_session_rc_nonzero", not drv.session_completed(124, STREAM_OK), "rc 124 is not a completed session")
    check("driver_session_no_events", not drv.session_completed(0, ""), "empty stream is not a completed session")
    check("driver_session_garbage_stream", not drv.session_completed(0, "not json\n"), "unparsable stream is not a session")
    for name, rc, out in (("timeout", 124, ""), ("auth_failure", 1, ""), ("no_events", 0, "")):
        got = str(drv.lv3_outcome(rc, out, False, 0))
        check("driver_lv3_" + name + "_is_fail", got.startswith("FAIL:claude_session_failed"), got)
    check("driver_lv3_no_tool_list_is_fail", drv.lv3_outcome(0, STREAM_OK, False, 0) == "FAIL:enterplanmode_no_tool_list",
          "a session without an init tool list cannot prove the tool was not offered")
    check("driver_lv3_called_not_denied", drv.lv3_outcome(0, STREAM_OK, True, 0) == "FAIL:enterplanmode_called_but_not_denied", "")
    check("driver_lv3_denied_passes", drv.lv3_outcome(0, STREAM_OK, True, 1) == "PASS", "")


def lv3_stream(tools, called, result=None, is_error=True) -> str:
    """A `-p --output-format stream-json --verbose` stream shaped like the real LV-3 runs (init tool list, tool_use, result)."""
    ev = [{"type": "system", "subtype": "init", "tools": tools}]
    if called:
        ev.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_1", "name": "EnterPlanMode", "input": {}}]}})
        if result is not None:
            ev.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": result, "is_error": is_error}]}})
    ev.append({"type": "result", "subtype": "success"})
    return "".join(json.dumps(e) + "\n" for e in ev)


HOST_UNKNOWN = ("<tool_use_error>Error: No such tool available: EnterPlanMode. EnterPlanMode is disabled for this session, "
                "in subagents as well as here.</tool_use_error>")


def test_driver_lv3_unoffered_tool_called_is_the_limitation() -> None:
    # Real -p behaviour (2026-10-21, r3): EnterPlanMode is absent from the init tool list, the model calls it anyway, and the
    # host answers "No such tool available" before any PreToolUse hook can run. That is the not-offered limitation, not a FAIL.
    s = lv3_stream(["Bash", "Read"], True, [{"type": "text", "text": HOST_UNKNOWN}])
    check("driver_lv3_unoffered_called_rejected_retries", drv.lv3_outcome(0, s, True, 0) is None, str(drv.lv3_outcome(0, s, True, 0)))
    s = lv3_stream(["Bash", "Read"], True, HOST_UNKNOWN)
    check("driver_lv3_unoffered_called_rejected_str_content", drv.lv3_outcome(0, s, True, 0) is None, "string tool_result content")
    s = lv3_stream(["Bash", "Read"], False)
    check("driver_lv3_unoffered_not_called_retries", drv.lv3_outcome(0, s, False, 0) is None, "provably not offered, never called")


def test_driver_lv3_never_called_needs_a_provably_absent_tool() -> None:
    got = drv.lv3_outcome(0, lv3_stream(["Bash", "EnterPlanMode"], False), False, 0)
    check("driver_lv3_offered_not_called_is_fail", got == "FAIL:enterplanmode_offered_not_called", str(got))
    got = drv.lv3_outcome(0, lv3_stream(["Bash"], False), False, 0)
    check("driver_lv3_absent_not_called_is_none", got is None, str(got))
    for name, s in (("no_init", '{"type":"result","subtype":"success"}\n'),
                    ("init_without_tools", STREAM_OK),
                    ("tools_not_a_list", '{"type":"system","subtype":"init","tools":"Bash"}\n{"type":"result","subtype":"success"}\n')):
        got = drv.lv3_outcome(0, s, False, 0)
        check("driver_lv3_" + name + "_never_called_is_fail", got == "FAIL:enterplanmode_no_tool_list", str(got))
    got = str(drv.lv3_outcome(1, lv3_stream(["Bash"], False), False, 0))
    check("driver_lv3_absent_not_called_failed_session_is_fail", got.startswith("FAIL:claude_session_failed"), got)
    check("driver_lv3_hook_row_wins_over_tool_list", drv.lv3_outcome(0, lv3_stream(["Bash", "EnterPlanMode"], False), False, 1) == "PASS", "")


def test_driver_lv3_called_without_host_rejection_stays_fail() -> None:
    want = "FAIL:enterplanmode_called_but_not_denied"
    cases = (
        ("offered_and_called", lv3_stream(["Bash", "EnterPlanMode"], True, HOST_UNKNOWN)),
        ("unoffered_tool_succeeded", lv3_stream(["Bash"], True, "Entered plan mode", is_error=False)),
        ("unoffered_no_tool_result", lv3_stream(["Bash"], True)),
        ("unoffered_other_error", lv3_stream(["Bash"], True, "<tool_use_error>permission denied</tool_use_error>")),
    )
    for name, s in cases:
        got = drv.lv3_outcome(0, s, True, 0)
        check("driver_lv3_" + name + "_is_fail", got == want, str(got))
    s = lv3_stream(["Bash"], True, HOST_UNKNOWN)
    got = str(drv.lv3_outcome(1, s, True, 0))
    check("driver_lv3_unoffered_rejected_needs_completed_session", got.startswith("FAIL:"), got)


def test_driver_step_lv3_reports_limitation_for_rejected_call() -> None:
    real = drv.claude
    drv.claude = lambda *a, **k: (0, lv3_stream(["Bash"], True, HOST_UNKNOWN), "")
    try:
        with tempfile.TemporaryDirectory() as td:
            got = drv.step_lv3(Path(td), Path(td))
    finally:
        drv.claude = real
    check("driver_step_lv3_rejected_call_is_limitation", got == drv.LIMITATION_ENTERPLANMODE, got)


def test_driver_evidence_class_needs_a_load_proof() -> None:
    base = {s: "NOT_RUN" for s in drv.STEPS}
    check("driver_class_a_only_with_load_pass", drv.evidence_class(dict(base, load_probe="PASS")) == "A", "")
    check("driver_class_b_when_not_run", drv.evidence_class(dict(base, lv3="PASS")) == "B-SIMULATED", "NOT_RUN load probe is no proof")
    check("driver_class_b_when_failed", drv.evidence_class(dict(base, load_probe="FAIL:rc=1,marker_hits=0")) == "B-SIMULATED", "")


def test_driver_all_ok_counts_not_run_as_failure() -> None:
    base = {s: "NOT_RUN" for s in drv.STEPS}
    check("driver_all_ok_single_step", drv.all_ok(dict(base, lv4="PASS"), ("lv4",)), "")
    check("driver_all_ok_lv3_limitation", drv.all_ok(dict(base, lv3=drv.LIMITATION_ENTERPLANMODE), ("lv3",)), "")
    check("driver_all_ok_rejects_not_run", not drv.all_ok(dict(base, load_probe="FAIL:x"), drv.STEPS), "unrun steps are not ok")
    check("driver_all_ok_rejects_empty", not drv.all_ok(base, ()), "no steps run proves nothing")


def test_driver_hook_rows_count_skipped_lines() -> None:
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / ".craftflow" / "state"
        log.mkdir(parents=True)
        (log / "craftflow-hook-events.log").write_text('{"event":"plan_commit_denied"}\nnot json\n\n{"event":"x"}\n', encoding="utf-8")
        rows, skipped = drv.hook_rows(td)
        check("driver_hook_rows_parsed_and_skipped", [r["event"] for r in rows] == ["plan_commit_denied", "x"] and skipped == 1,
              f"{rows} skipped={skipped}")
        rows, skipped = drv.hook_rows(Path(td) / "absent")
        check("driver_hook_rows_absent_log", rows == [] and skipped == 0, "")


def test_render_is_pure() -> None:
    d = synthetic()
    before = copy.deepcopy(d)
    imp.render(d)
    check("render_does_not_mutate_input", d == before, "render mutated its input")


def test_hv_notes_loader_and_render() -> None:
    good = {"method": "numbers taken from session transcripts.", "outcomes": {"HV-3": "PASS: router invoked", "HV-4": "PASS: denied"}}
    check("hv_notes_loader_accepts_good", imp.load_hv_notes(good) == good, "valid notes must round-trip")
    for name, bad in (("unknown_key", {"extra": "x"}), ("unknown_id", {"outcomes": {"HV-9": "x"}}), ("multiline", {"method": "a\nb"}),
                      ("empty", {"method": "  "}), ("too_long", {"method": "x" * 901}), ("not_object", ["x"])):
        expect_impact_error("hv_notes_loader_rejects_" + name, lambda b=bad: imp.load_hv_notes(b))
    plain = imp.render(synthetic())
    check("hv_default_text_unchanged_without_notes", "Captured by David with" in plain and "none is recorded here" in plain,
          "without notes the original sentences must remain")
    text = imp.render(synthetic(), notes=good)
    check("hv_notes_replace_method_sentence", "Method: numbers taken from session transcripts." in text and "Captured by David with" not in text,
          "method note must replace the hard-coded capture sentence")
    check("hv_notes_list_outcomes", "- HV-3: PASS: router invoked" in text and "- HV-4: PASS: denied" in text and "none is recorded here" not in text,
          "outcomes must replace the 'none is recorded' sentence")


LIMIT = "LIMITATION:enterplanmode_not_offered"


def test_render_lv3_limitation_needs_hv4_pass() -> None:
    lim = good_lv(lv3=LIMIT)
    cases = (
        ("missing_notes", None, False),
        ("missing_hv4", {"outcomes": {"HV-3": "PASS: ok"}}, False),
        ("fail_hv4", {"outcomes": {"HV-4": "FAIL: not denied"}}, False),
        ("pass_hv4", {"outcomes": {"HV-4": "PASS: denied"}}, True),
    )
    for name, notes, complete in cases:
        text = imp.render(synthetic(), hv=full_hv(), lv=lim, notes=notes)
        line = status_line(text)
        if complete:
            check("lv3_limit_" + name + "_complete", line.startswith("- Status: COMPLETE"), line)
            check("lv3_limit_" + name + "_sentence_covered", "covered by HV-4" in text and "not covered" not in text, "PASS HV-4 covers the limitation")
        else:
            check("lv3_limit_" + name + "_draft", line.startswith("- Status: DRAFT")
                  and "LV-3 is the not-offered limitation and HV-4 is not recorded as PASS" in line, line)
            check("lv3_limit_" + name + "_sentence_truthful", "not covered" in text and "covered by HV-4" not in text,
                  "without an HV-4 PASS the limitation sentence must not claim coverage")
    line = status_line(imp.render(synthetic(), hv=full_hv(), lv=good_lv(), notes=None))
    check("lv3_pass_needs_no_hv4", line.startswith("- Status: COMPLETE") and "HV-4" not in line, line)


def test_render_status_wording_matches_the_limitation() -> None:
    plain = status_line(imp.render(synthetic(), hv=full_hv(), lv=good_lv()))
    check("status_wording_not_false_claim", "every LV result passed or is the recorded LV-3 limitation" in plain, plain)
    lim = status_line(imp.render(synthetic(), hv=full_hv(), lv=good_lv(lv3=LIMIT), notes={"outcomes": {"HV-4": "PASS: x"}}))
    check("status_wording_names_hv4_when_limited", "HV-4 recorded as PASS" in lim, lim)


def test_render_method_note_defines_both_capture_points() -> None:
    text = imp.render(synthetic(), notes={"method": "a = first model call after the router skill result, b = last model call of the session."})
    line = [ln for ln in text.splitlines() if ln.startswith("Method:")][0]
    check("method_note_drops_fixed_point_sentence", "Capture point a is right after the routing announce" not in line
          and "memory-finalize" not in line and "Values are tokens (not bytes)." in line, line)
    check("method_note_keeps_notes_definition", "b = last model call of the session." in line, line)


def test_render_reproduce_line_names_the_inputs() -> None:
    line = [ln for ln in imp.render(synthetic()).splitlines() if ln.startswith("- Reproduce:")][0]
    for want in ("--hv ", "--lv ", "--hv-notes ", "docs/benchmarks/2026-10-21-router-context-cut-r2.inputs/hv.json",
                 "lv.json", "hv-notes.json", "--baseline", "--out"):
        check("reproduce_line_has_" + want.strip(), want in line, line)
    check("reproduce_line_repo_relative", "tools/craftflow-plugin/plugins/craftflow/scripts/craftflow_router_context_impact.py" in line, line)


def test_hv_notes_loader_rejects_markup_and_line_breaks() -> None:
    for name, ch in (("lt", "<"), ("gt", ">"), ("pipe", "|"), ("ls", "\u2028"), ("ps", "\u2029"), ("nel", "\u0085"),
                     ("vt", "\x0b"), ("ff", "\x0c"), ("fs", "\x1c"), ("gs", "\x1d"), ("rs", "\x1e")):
        expect_impact_error("hv_notes_rejects_" + name + "_in_method", lambda c=ch: imp.load_hv_notes({"method": "a" + c + "b"}))
        expect_impact_error("hv_notes_rejects_" + name + "_in_outcome", lambda c=ch: imp.load_hv_notes({"outcomes": {"HV-3": "a" + c + "b"}}))
    kept = {"method": "uses `claude -p` (see ADR-0073), a = first call; b: last; 50% fine, \"quoted\" & ok."}
    check("hv_notes_keeps_backticks_and_punctuation", imp.load_hv_notes(kept) == kept, "ordinary punctuation stays allowed")


def main() -> int:
    for name in sorted(n for n in globals() if n.startswith("test_")):
        try:
            globals()[name]()
        except Exception as exc:  # noqa: BLE001 - an unexpected error fails that test by name
            fail(name, f"{type(exc).__name__}: {exc}")
    print()
    for e in _errors:
        print(e)
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
