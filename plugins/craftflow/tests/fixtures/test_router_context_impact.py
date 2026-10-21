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
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_router_context_impact as imp  # noqa: E402

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
    lv = {"load_probe": "PASS", "lv1": "PASS", "lv2": "PASS", "lv3": "LIMITATION:enterplanmode_not_offered", "lv4": "PASS", "lv5": "PASS"}
    text = imp.render(synthetic(), lv=lv)
    sect = text.split("## Live verification")[1].split("\n## ")[0]
    check("render_lv_filled", "LIMITATION:enterplanmode_not_offered" in sect and "PENDING" not in sect.split("HV")[0], sect[:400])


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
    check("collect_head_equals_readset_tool", got == want, f"{got} != {want}")


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


def test_render_is_pure() -> None:
    d = synthetic()
    before = copy.deepcopy(d)
    imp.render(d)
    check("render_does_not_mutate_input", d == before, "render mutated its input")


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
