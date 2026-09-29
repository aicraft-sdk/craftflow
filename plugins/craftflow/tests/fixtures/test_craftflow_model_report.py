#!/usr/bin/env python3
"""Tests for craftflow_model_report.py (pure core: price table, cost, aggregate).

Run: python3 tests/fixtures/test_craftflow_model_report.py
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import craftflow_model_report as cmr  # noqa: E402

PRICES_PATH = PLUGIN_ROOT / "config" / "model-prices.json"

_passes = 0
_errors = []


def ok(name):
    global _passes
    _passes += 1
    print("  PASS: " + name)


def fail(name, reason):
    _errors.append("FAIL [" + name + "]: " + reason)
    print("  FAIL: " + name + ": " + reason)


def _prices():
    with open(str(PRICES_PATH), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _close(a, b):
    return abs(a - b) < 1e-9


def _bucket(inp=0, out=0, cr=0, w5=0, w1=0, msgs=1, fast=0):
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cr,
        "cache_creation_input_tokens": w5 + w1,
        "cache_write_5m": w5,
        "cache_write_1h": w1,
        "messages": msgs,
        "fast_messages": fast,
    }


def _row(agent, aid, models, primary=None, wf=None, error=None, partial=False, **extra):
    row = {
        "event": "agent_usage",
        "agent_type": agent,
        "agent_id": aid,
        "attribution_agent": "",
        "models": models,
        "primary_model": primary,
        "workflow_id": wf,
        "error": error,
        "partial": partial,
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# price table
# ---------------------------------------------------------------------------

def test_price_table_is_valid_estimate():
    p = _prices()
    assert p["schema_version"] == 1
    assert p["label"].startswith("ESTIMATE")
    assert p["as_of"] == "2026-09-25"
    assert p["family_rank"] == ["haiku", "sonnet", "opus", "fable"]
    for name, row in p["models"].items():
        assert sorted(row) == ["cache_read", "cache_write_1h", "cache_write_5m", "input", "output"], name


# ---------------------------------------------------------------------------
# normalize / cost
# ---------------------------------------------------------------------------

def test_normalize_model_id():
    n = cmr.normalize_model_id
    assert n("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert n("claude-sonnet-4-6@20250929") == "claude-sonnet-4-6"
    assert n("claude-sonnet-5") == "claude-sonnet-5"
    assert n(None) == "unknown"
    assert n("") == "unknown"
    assert n("<synthetic>") == "<synthetic>"


def test_estimate_cost_exact():
    row = _prices()["models"]["claude-sonnet-5"]
    usage = _bucket(inp=1000000, out=1000000, cr=1000000, w5=1000000, w1=1000000)
    # cache_creation_input_tokens (2M) must NOT be used; only w5 + w1
    cost = cmr.estimate_cost(usage, row)
    assert cost is not None and _close(cost, 18.70), cost


def test_estimate_cost_ignores_cache_creation_input_tokens():
    row = _prices()["models"]["claude-sonnet-5"]
    usage = _bucket(w5=0, w1=0)
    usage["cache_creation_input_tokens"] = 5000000
    assert _close(cmr.estimate_cost(usage, row), 0.0)


def test_estimate_cost_unknown_model_null():
    assert cmr.estimate_cost(_bucket(inp=10), None) is None


def test_estimate_cost_fast_bucket_null():
    row = _prices()["models"]["claude-sonnet-5"]
    assert cmr.estimate_cost(_bucket(inp=10, fast=1), row) is None


def test_cost_linearity():
    row = _prices()["models"]["claude-opus-5-5"]
    u1 = _bucket(inp=123456, out=7890, cr=555555, w5=4321, w1=99)
    u2 = _bucket(inp=246912, out=15780, cr=1111110, w5=8642, w1=198)
    c1 = cmr.estimate_cost(u1, row)
    c2 = cmr.estimate_cost(u2, row)
    assert _close(c2, 2 * c1), (c1, c2)
    assert cmr.estimate_cost(u2, None) is None


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------

def _golden_rows():
    sonnet = "claude-sonnet-5"
    opus = "claude-opus-5-5"
    return [
        # earlier re-fire of b1: overwritten (E21 last wins)
        _row("craftflow:component-builder", "b1", {sonnet: _bucket(inp=1, out=1)},
             primary=sonnet, wf="wf-a", contract_shape="none"),
        _row("craftflow:component-builder", "b1",
             {sonnet: _bucket(inp=500000, out=100000, cr=1000000, w5=200000)},
             primary=sonnet, wf="wf-a", contract_shape="yaml_block", contract_valid=True),
        _row("craftflow:component-builder", "b2", {sonnet: _bucket(inp=1000000)},
             primary=sonnet, wf="wf-a", contract_shape="yaml_block", contract_valid=False),
        _row("craftflow:doubt-verifier", "d1", {opus: _bucket(inp=250000, out=50000)},
             primary=opus, wf="wf-b", contract_shape="envelope"),
        _row("craftflow:code-reviewer", "e1", {}, error="transcript_missing"),
    ]


def test_aggregate_golden():
    agg = cmr.aggregate(_golden_rows(), [{"event": "subagent_stop"}] * 7, {}, _prices())
    assert sorted(agg) == sorted([
        "by_agent_model", "by_model", "by_workflow", "totals", "data_quality",
        "contract_by_agent_model", "price_meta"]), sorted(agg)

    bam = agg["by_agent_model"]
    assert len(bam) == 2, bam
    b, d = bam
    assert b["agent"] == "craftflow:component-builder" and b["model"] == "claude-sonnet-5"
    assert b["dispatches"] == 2
    assert b["input_tokens"] == 1500000 and b["output_tokens"] == 100000
    assert b["cache_read_input_tokens"] == 1000000
    assert b["cache_write_5m"] == 200000 and b["cache_write_1h"] == 0
    # b1: 1.0 + 1.0 + 0.2 + 0.5 = 2.7 ; b2: 2.0
    assert _close(b["est_cost_usd"], 4.7), b["est_cost_usd"]
    assert d["agent"] == "craftflow:doubt-verifier" and d["model"] == "claude-opus-5-5"
    assert d["dispatches"] == 1
    assert _close(d["est_cost_usd"], 2.0)

    bm = {r["model"]: r for r in agg["by_model"]}
    assert sorted(bm) == ["claude-opus-5-5", "claude-sonnet-5"]
    assert bm["claude-sonnet-5"]["dispatches"] == 2
    assert _close(bm["claude-sonnet-5"]["est_cost_usd"], 4.7)

    bw = {r["workflow_id"]: r for r in agg["by_workflow"]}
    assert sorted(bw) == ["wf-a", "wf-b"]
    assert bw["wf-a"]["dispatches"] == 2 and _close(bw["wf-a"]["est_cost_usd"], 4.7)
    assert _close(bw["wf-b"]["est_cost_usd"], 2.0)

    t = agg["totals"]
    assert _close(t["est_cost_usd"], 6.7), t
    assert t["dispatches"] == 3

    dq = agg["data_quality"]
    assert dq["records"] == 4
    assert dq["error_records"] == 1
    assert dq["partial_records"] == 0
    assert dq["attributed_records"] == 3
    assert dq["distinct_workflows"] == 2
    assert _close(dq["priced_token_share"], 1.0)
    assert _close(dq["contract_classified_share"], 1.0)
    assert dq["stop_records"] == 7

    assert agg["price_meta"] == {
        "label": _prices()["label"], "as_of": "2026-09-25", "source": _prices()["source"]}


def test_aggregate_null_costs_sort_last_and_priced_share():
    rows = [
        _row("craftflow:planner", "p1", {"claude-unknown-9": _bucket(inp=1000000)},
             primary="claude-unknown-9"),
        _row("craftflow:code-reviewer", "r1", {"claude-sonnet-5": _bucket(inp=1000000)},
             primary="claude-sonnet-5"),
        _row("craftflow:web-researcher", "w1",
             {"claude-sonnet-5": _bucket(inp=1000000, fast=1)}, primary="claude-sonnet-5"),
    ]
    agg = cmr.aggregate(rows, [], {}, _prices())
    costs = [(r["agent"], r["est_cost_usd"]) for r in agg["by_agent_model"]]
    # r1 priced first (2.0), then nulls ordered by agent
    assert costs[0][0] == "craftflow:code-reviewer" and _close(costs[0][1], 2.0)
    assert [c[0] for c in costs[1:]] == ["craftflow:planner", "craftflow:web-researcher"]
    assert costs[1][1] is None and costs[2][1] is None
    assert _close(agg["data_quality"]["priced_token_share"], 1.0 / 3.0)


def test_aggregate_normalizes_dated_model_ids():
    rows = [_row("craftflow:doc-syncer", "x", {"claude-haiku-4-5-20251001": _bucket(inp=1000000)},
                 primary="claude-haiku-4-5-20251001")]
    agg = cmr.aggregate(rows, [], {}, _prices())
    assert agg["by_agent_model"][0]["model"] == "claude-haiku-4-5"
    assert _close(agg["by_agent_model"][0]["est_cost_usd"], 1.0)


def test_agent_fallback_to_attribution():
    m = {"claude-sonnet-5": _bucket(inp=1)}
    rows = [
        _row("", "a1", m, primary="claude-sonnet-5", attribution_agent="craftflow:planner"),
        _row("", "a2", m, primary="claude-sonnet-5", attribution_agent=""),
    ]
    rows[0]["attribution_agent"] = "craftflow:planner"
    agg = cmr.aggregate(rows, [], {}, _prices())
    agents = sorted(r["agent"] for r in agg["by_agent_model"])
    assert agents == ["craftflow:planner", "unknown"], agents


def test_aggregate_contract_breakdown():
    sonnet = "claude-sonnet-5"
    m = {sonnet: _bucket(inp=1)}
    rows = [
        _row("craftflow:component-builder", "c1", m, primary=sonnet,
             contract_shape="yaml_block", contract_valid=True),
        _row("craftflow:component-builder", "c2", m, primary=sonnet,
             contract_shape="yaml_block", contract_valid=True),
        _row("craftflow:component-builder", "c3", m, primary=sonnet,
             contract_shape="yaml_block", contract_valid=False),
        _row("craftflow:component-builder", "c4", m, primary=sonnet),  # pre-DD-13, no fields
        _row("craftflow:code-reviewer", "r1", m, primary=sonnet, contract_shape="envelope"),
        _row("craftflow:code-reviewer", "r2", m, primary=sonnet, contract_shape="envelope"),
        _row("craftflow:code-reviewer", "r3", m, primary=sonnet, contract_shape="envelope"),
        _row("craftflow:learn-distiller", "l1", m, primary=sonnet, contract_shape="heading_other"),
        _row("craftflow:doc-syncer", "s1", m, primary=sonnet, contract_shape="none"),
        _row("craftflow:planner", "e1", {}, primary=None, error="transcript_missing"),
    ]
    agg = cmr.aggregate(rows, [], {}, _prices())
    cb = agg["contract_by_agent_model"]
    keys = ["agent", "model", "dispatches", "classified", "yaml_block", "yaml_valid",
            "envelope", "heading_other", "none", "unclassified", "note"]
    for r in cb:
        assert sorted(r) == sorted(keys), sorted(r)
    assert [(r["agent"], r["model"]) for r in cb] == [
        ("craftflow:code-reviewer", sonnet),
        ("craftflow:component-builder", sonnet),
        ("craftflow:doc-syncer", sonnet),
        ("craftflow:learn-distiller", sonnet),
    ], [(r["agent"], r["model"]) for r in cb]
    by = {r["agent"]: r for r in cb}

    b = by["craftflow:component-builder"]
    assert (b["dispatches"], b["classified"], b["yaml_block"], b["yaml_valid"],
            b["envelope"], b["heading_other"], b["none"], b["unclassified"]) == (4, 3, 3, 2, 0, 0, 0, 1)
    assert b["note"] is None

    r = by["craftflow:code-reviewer"]
    assert (r["dispatches"], r["classified"], r["yaml_block"], r["envelope"], r["unclassified"]) == (3, 3, 0, 3, 0)
    assert r["note"] == "envelope validity not assessed"

    assert by["craftflow:learn-distiller"]["note"] == "validity not assessable by shape"
    assert by["craftflow:doc-syncer"]["note"] == "validity not assessable by shape"
    assert by["craftflow:doc-syncer"]["none"] == 1

    # classified share over non-error dispatches: 9 dispatches, 8 classified
    assert _close(agg["data_quality"]["contract_classified_share"], 8.0 / 9.0)


def test_aggregate_empty_input():
    agg = cmr.aggregate([], [], {}, _prices())
    assert agg["by_agent_model"] == [] and agg["contract_by_agent_model"] == []
    assert agg["data_quality"]["records"] == 0
    assert agg["data_quality"]["priced_token_share"] == 0.0
    assert agg["totals"]["est_cost_usd"] is None


def test_module_import_is_cheap():
    heavy = [m for m in ("craftflow_hooklib", "craftflow_transcript_usage") if m in sys.modules]
    assert heavy == [], heavy


# ---------------------------------------------------------------------------
# P4.1 log reader and workflow loader
# ---------------------------------------------------------------------------

import os  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402


def _tmpdir():
    return tempfile.mkdtemp(prefix="cmr-test-")


def _write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


def test_iter_log_rows_prefilter_exact_match():
    d = _tmpdir()
    log = os.path.join(d, "events.log")
    usage = {"event": "agent_usage", "ts": "2026-09-28T10:00:00Z", "agent_id": "a1"}
    stop = {"event": "subagent_stop", "ts": "2026-09-28T10:00:01Z", "agent_id": "a1"}
    guard = {"event": "pretool_guard", "command": 'echo "event": "agent_usage" done'}
    _write_lines(log, [
        json.dumps(usage),
        "{not json at all agent_usage",
        json.dumps(guard),
        json.dumps(stop),
    ])
    got = [(ev, row["agent_id"]) for ev, row in cmr.iter_log_rows(log)]
    assert got == [("agent_usage", "a1"), ("subagent_stop", "a1")], got
    only = [ev for ev, _r in cmr.iter_log_rows(log, events=("subagent_stop",))]
    assert only == ["subagent_stop"], only


def test_iter_log_rows_since_filters_by_ts_string():
    d = _tmpdir()
    log = os.path.join(d, "events.log")
    rows = [
        {"event": "agent_usage", "ts": "2026-09-01T00:00:00Z", "agent_id": "old"},
        {"event": "agent_usage", "ts": "2026-09-20T00:00:00Z", "agent_id": "new"},
    ]
    _write_lines(log, [json.dumps(r) for r in rows])
    got = [r["agent_id"] for _ev, r in cmr.iter_log_rows(log, since="2026-09-10")]
    assert got == ["new"], got


def test_load_workflows_reads_rigor_and_loops():
    d = _tmpdir()
    with open(os.path.join(d, "wf-a.json"), "w") as fh:
        json.dump({"verification_rigor": "critical_path",
                   "telemetry": {"loop_counts": {"remfix": 2}}}, fh)
    with open(os.path.join(d, "wf-b.json"), "w") as fh:
        json.dump({"telemetry": {}}, fh)
    with open(os.path.join(d, "wf-a.events.jsonl"), "w") as fh:
        fh.write("{}\n")
    with open(os.path.join(d, "wf-bad.json"), "w") as fh:
        fh.write("{corrupt")
    with open(os.path.join(d, "wf-list.json"), "w") as fh:
        fh.write("[1, 2]")
    got = cmr.load_workflows(d)
    assert got == {
        "wf-a": {"verification_rigor": "critical_path", "loop_counts": {"remfix": 2}},
        "wf-b": {"verification_rigor": None, "loop_counts": {}},
    }, got


# ---------------------------------------------------------------------------
# P4.2 rubric
# ---------------------------------------------------------------------------

_T = {"min_records": 10, "min_workflows": 4, "min_remfix_workflows": 4,
      "min_contract_samples": 10, "min_critical_path_workflows": 3}
_VERDICTS = ("GO", "NO_GO", "INSUFFICIENT_DATA", "DATA_GO_PENDING_ADR0046_EVAL")
_seq = [0]


def _urow(agent, model, wf, out=1000, remfix=False, shape="yaml_block", valid=True,
          error=None, primary="same", **extra):
    _seq[0] += 1
    prim = model if primary == "same" else primary
    models = {model: _bucket(inp=1000, out=out)} if model else {}
    return _row("craftflow:" + agent, "id-" + str(_seq[0]), models, primary=prim, wf=wf,
                error=error, is_remfix=remfix, contract_shape=shape, contract_valid=valid,
                **extra)


def _a_rows(remfix_model="claude-sonnet-5", opus_n=10, opus_bad=0, opus_shape_kw=None):
    rows = []
    for i in range(6):
        rows.append(_urow("component-builder", remfix_model, "wf" + str(i), remfix=True))
        rows.append(_urow("component-builder", "claude-sonnet-5", "wf" + str(i)))
    for j in range(opus_n):
        kw = dict(opus_shape_kw or {})
        if j < opus_bad:
            kw.update(valid=False)
        rows.append(_urow("component-builder", "claude-opus-5", "wf-opus", out=200, **kw))
    return rows


def _rub(rows, workflows=None, stops=None, thresholds=None):
    th = dict(_T)
    th.update(thresholds or {})
    prices = _prices()
    summary = cmr.aggregate(rows, stops or [], workflows or {}, prices)
    result = cmr.rubric(summary, stops or [], workflows or {}, th, usage_rows=rows, prices=prices)
    for arm in ("escalate_on_failure", "doubt_verifier_diversity"):
        assert result[arm]["verdict"] in _VERDICTS, result[arm]["verdict"]
        for crit in result[arm]["criteria"]:
            assert sorted(crit) == ["description", "id", "pass", "threshold", "value"], crit
    return result


def _crit(result, arm, cid):
    found = [c for c in result[arm]["criteria"] if c["id"] == cid]
    assert len(found) == 1, (cid, [c["id"] for c in result[arm]["criteria"]])
    return found[0]


def _b_rows(specs):
    """specs: list of (builder_model, verifier_model, verifier_out); one critical_path wf each."""
    rows = []
    workflows = {}
    for i, (bm, vm, vout) in enumerate(specs):
        wf = "cp" + str(i)
        workflows[wf] = {"verification_rigor": "critical_path", "loop_counts": {}}
        rows.append(_urow("component-builder", bm, wf, out=4000))
        rows.append(_urow("doubt-verifier", vm, wf, out=vout))
    return rows, workflows


def test_rubric_insufficient_data_when_below_min_records():
    rows = _a_rows()[:5]
    res = cmr.rubric(cmr.aggregate(rows, [], {}, _prices()), [], {}, {}, usage_rows=rows, prices=_prices())
    assert res["escalate_on_failure"]["verdict"] == "INSUFFICIENT_DATA"
    assert res["doubt_verifier_diversity"]["verdict"] == "INSUFFICIENT_DATA"
    assert res["data_quality"]["pass"] is False
    d1 = [c for c in res["data_quality"]["criteria"] if c["id"] == "D1"][0]
    assert d1["value"] == 5 and d1["threshold"] == 200 and d1["pass"] is False, d1


def test_rubric_escalate_go():
    res = _rub(_a_rows())
    arm = res["escalate_on_failure"]
    assert res["data_quality"]["pass"] is True, res["data_quality"]
    assert arm["verdict"] == "GO", [c for c in arm["criteria"] if c["pass"] is not True]
    assert [c["id"] for c in arm["criteria"]] == ["A1", "A2", "A3", "A4"]
    assert all(c["pass"] is True for c in arm["criteria"])


def test_rubric_escalate_no_go_no_headroom():
    res = _rub(_a_rows(remfix_model="claude-opus-5"))
    arm = res["escalate_on_failure"]
    a2 = _crit(res, "escalate_on_failure", "A2")
    assert a2["pass"] is False and a2["value"] == 0.0, a2
    assert arm["verdict"] == "NO_GO", arm["verdict"]


def test_rubric_escalate_no_go_contract_regression():
    res = _rub(_a_rows(opus_bad=1))  # opus 1/10 bad = 0.10 vs sonnet 0 -> delta 0.10 > 0.05
    a4 = _crit(res, "escalate_on_failure", "A4")
    assert a4["pass"] is False, a4
    assert res["escalate_on_failure"]["verdict"] == "NO_GO"
    res_ok = _rub(_a_rows(opus_bad=0))
    assert _crit(res_ok, "escalate_on_failure", "A4")["pass"] is True


def test_rubric_a4_insufficient_below_min_contract_samples():
    res = _rub(_a_rows(opus_n=9))
    a4 = _crit(res, "escalate_on_failure", "A4")
    assert a4["pass"] is None, a4
    assert res["escalate_on_failure"]["verdict"] == "INSUFFICIENT_DATA"


def test_rubric_a4_ignores_old_hook_fields():
    rows = _a_rows(opus_n=0)
    for _ in range(12):
        row = _urow("component-builder", "claude-opus-5", "wf-opus", out=200)
        row.pop("contract_shape")
        row.update(contract_found=True, contract_valid=True)
        rows.append(row)
    stops = [{"event": "subagent_stop", "agent_id": "x", "contract_found": True,
              "contract_valid": True, "reason": "contract_present"}]
    res = _rub(rows, stops=stops)
    a4 = _crit(res, "escalate_on_failure", "A4")
    assert a4["pass"] is None, a4
    assert "contract_shape" in a4["description"] and "subagent_stop" in a4["description"], a4["description"]
    assert res["escalate_on_failure"]["verdict"] == "INSUFFICIENT_DATA"


def test_rubric_escalate_insufficient_no_target_family_data():
    res = _rub(_a_rows(opus_n=0))
    assert _crit(res, "escalate_on_failure", "A4")["pass"] is None
    assert res["escalate_on_failure"]["verdict"] == "INSUFFICIENT_DATA"


def test_rubric_diversity_capped_pending_adr0046():
    rows, wfs = _b_rows([("claude-opus-5", "claude-opus-5", 100)] * 6)
    res = _rub(rows, workflows=wfs)
    arm = res["doubt_verifier_diversity"]
    assert [c["id"] for c in arm["criteria"]] == ["B1", "B0", "B2", "B3", "B4"], [c["id"] for c in arm["criteria"]]
    assert arm["verdict"] == "DATA_GO_PENDING_ADR0046_EVAL", [c for c in arm["criteria"] if c["pass"] is not True]
    assert _crit(res, "doubt_verifier_diversity", "B4")["pass"] is None


def test_rubric_diversity_no_go_spend():
    rows, wfs = _b_rows([("claude-opus-5", "claude-opus-5", 4000)] * 6)
    res = _rub(rows, workflows=wfs)
    b3 = _crit(res, "doubt_verifier_diversity", "B3")
    assert b3["pass"] is False and b3["value"] > 0.10, b3
    assert res["doubt_verifier_diversity"]["verdict"] == "NO_GO"


def test_rubric_diversity_no_go_default_diverse():
    rows, wfs = _b_rows([("claude-sonnet-5", "claude-opus-5", 100)] * 6)
    res = _rub(rows, workflows=wfs)
    b0 = _crit(res, "doubt_verifier_diversity", "B0")
    assert b0["value"] == 1.0 and b0["threshold"] == 0.95 and b0["pass"] is False, b0
    assert res["doubt_verifier_diversity"]["verdict"] == "NO_GO"


def test_rubric_diversity_residual_same_model():
    specs = [("claude-sonnet-5", "claude-opus-5", 100)] * 3 + [("claude-opus-5", "claude-opus-5", 100)] * 3
    rows, wfs = _b_rows(specs)
    res = _rub(rows, workflows=wfs)
    b0 = _crit(res, "doubt_verifier_diversity", "B0")
    assert b0["value"] == 0.5 and b0["pass"] is True, b0
    assert res["doubt_verifier_diversity"]["verdict"] == "DATA_GO_PENDING_ADR0046_EVAL"


def test_rubric_diversity_insufficient_below_min_critical_path():
    rows, wfs = _b_rows([("claude-opus-5", "claude-opus-5", 100)] * 2)
    res = _rub(rows, workflows=wfs, thresholds={"min_records": 2, "min_workflows": 1})
    assert _crit(res, "doubt_verifier_diversity", "B1")["pass"] is False
    assert res["doubt_verifier_diversity"]["verdict"] == "INSUFFICIENT_DATA"


def test_rubric_never_targets_down():
    # ceiling below the current family: headroom criterion fails, never a downward target
    res = _rub(_a_rows(), thresholds={"escalation_ceiling_family": "sonnet"})
    a2 = _crit(res, "escalate_on_failure", "A2")
    assert a2["pass"] is False and a2["value"] == 0.0, a2
    assert res["escalate_on_failure"]["verdict"] == "NO_GO"


def test_rubric_monotone_data_gate():
    base = _a_rows()  # 22 records, min_records=20
    th = {"min_records": 20}
    small = _rub(base[:19], thresholds=th)
    grown = _rub(base[:19] + [_urow("component-builder", "claude-sonnet-5", "wf0")], thresholds=th)
    before = {c["id"] for c in small["data_quality"]["criteria"] if c["pass"] is True}
    after = {c["id"] for c in grown["data_quality"]["criteria"] if c["pass"] is True}
    assert "D1" not in before and "D1" in after, (before, after)
    assert before <= after, (before, after)


# ---------------------------------------------------------------------------
# P4.3 backfill
# ---------------------------------------------------------------------------

def _assistant_line(mid, model, text=None, out=50):
    content = [{"type": "text", "text": text}] if text is not None else [{"type": "tool_use", "name": "Bash"}]
    return json.dumps({
        "type": "assistant",
        "timestamp": "2026-09-27T22:30:57.563Z",
        "attributionAgent": "craftflow:component-builder",
        "message": {"role": "assistant", "id": mid, "model": model, "content": content,
                    "usage": {"input_tokens": 10, "output_tokens": out,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}},
    })


def _transcript(text=None, name="agent.jsonl"):
    path = os.path.join(_tmpdir(), name)
    _write_lines(path, [_assistant_line("m1", "claude-sonnet-5", text=text)])
    return path


def _stop(aid, path, agent_type="craftflow:component-builder", reason="contract_missing"):
    return {"event": "subagent_stop", "ts": "2026-09-28T10:00:00Z", "agent_type": agent_type,
            "agent_id": aid, "agent_transcript_path": path, "reason": reason}


def _builder_contract():
    import craftflow_contract_validate as ccv
    fields = list(ccv.REQUIRED_FIELDS["component-builder"])
    body = ["STATUS: COMPLETE"] + [f + ": x" for f in fields if f != "STATUS"]
    return "Done.\n\n### Router Contract (MACHINE-READABLE)\n```yaml\n" + "\n".join(body) + "\n```\n"


def test_backfill_uses_subagent_stop_paths():
    path = _transcript()
    rows, stats = cmr.backfill([_stop("a1", path)], set())
    assert stats == {"backfill_parsed": 1, "backfill_missing": 0, "backfill_errors": 0}, stats
    assert len(rows) == 1
    r = rows[0]
    assert r["event"] == "agent_usage" and r["source"] == "backfill", r
    assert r["agent_type"] == "craftflow:component-builder" and r["agent_id"] == "a1"
    assert r["ts"] == "2026-09-28T10:00:00Z" and r["schema"] == 1
    assert r["primary_model"] == "claude-sonnet-5" and r["models"]["claude-sonnet-5"]["output_tokens"] == 50


def test_backfill_skips_ids_already_recorded():
    path = _transcript()
    rows, stats = cmr.backfill([_stop("a1", path), _stop("a2", path)], {"a1"})
    assert [r["agent_id"] for r in rows] == ["a2"], rows
    assert stats["backfill_parsed"] == 1, stats


def test_backfill_missing_transcript_counted():
    gone = os.path.join(_tmpdir(), "gone.jsonl")
    wrong = os.path.join(_tmpdir(), "notes.txt")
    with open(wrong, "w") as fh:
        fh.write("x")
    rows, stats = cmr.backfill([_stop("a1", gone), _stop("a2", wrong), _stop("a3", "")], set())
    assert rows == [], rows
    assert stats == {"backfill_parsed": 0, "backfill_missing": 2, "backfill_errors": 1}, stats


def test_backfill_skips_non_craftflow():
    path = _transcript()
    stops = [
        _stop("keep-prefix", path),
        _stop("keep-legacy", path, agent_type="", reason="contract_present"),
        _stop("drop-other", path, agent_type="Explore"),
        _stop("drop-empty-other-reason", path, agent_type="", reason="something_else"),
    ]
    rows, _stats = cmr.backfill(stops, set())
    assert sorted(r["agent_id"] for r in rows) == ["keep-legacy", "keep-prefix"], rows


def test_backfill_classifies_contract_from_final_text():
    path = _transcript(text=_builder_contract())
    rows, _stats = cmr.backfill([_stop("a1", path)], set())
    r = rows[0]
    assert r["contract_shape"] == "yaml_block" and r["contract_valid"] is True, r
    assert r["contract_source"] == "transcript_final_text" and r["contract_errors"] == [], r
    assert "final_text" not in r


def test_backfill_row_has_no_final_text_key_when_transcript_lacks_text():
    path = _transcript(text=None)
    rows, _stats = cmr.backfill([_stop("a1", path)], set())
    r = rows[0]
    assert "final_text" not in r
    assert r["contract_shape"] == "none" and r["contract_valid"] is None, r


# ---------------------------------------------------------------------------
# P4.4 CLI
# ---------------------------------------------------------------------------

SCRIPT = str(SCRIPTS / "craftflow_model_report.py")


def _cli_fixture():
    d = _tmpdir()
    log = os.path.join(d, "events.log")
    wfdir = os.path.join(d, "workflows")
    os.mkdir(wfdir)
    with open(os.path.join(wfdir, "wf-1.json"), "w") as fh:
        json.dump({"verification_rigor": "standard", "telemetry": {"loop_counts": {}}}, fh)
    transcript = _transcript()
    lines = [
        json.dumps(_urow("component-builder", "claude-sonnet-5", "wf-1", ts="2026-09-28T10:00:00Z")),
        json.dumps(_stop("only-stop", transcript)),
        "garbage line",
    ]
    _write_lines(log, lines)
    return d, log, wfdir


def _run_cli(*args):
    proc = subprocess.run([sys.executable, SCRIPT] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def test_cli_json_golden():
    _d, log, wfdir = _cli_fixture()
    code, out, err = _run_cli("--log", log, "--workflows-dir", wfdir, "--prices", str(PRICES_PATH), "--json")
    assert code == 0, (code, err)
    data = json.loads(out)
    assert sorted(data) == ["backfill", "rubric", "since", "summary"], sorted(data)
    assert data["backfill"] is None and data["since"] is None
    summary = data["summary"]
    assert summary["totals"]["dispatches"] == 1, summary["totals"]
    assert [(r["agent"], r["model"]) for r in summary["by_agent_model"]] == [
        ("craftflow:component-builder", "claude-sonnet-5")], summary["by_agent_model"]
    assert summary["price_meta"]["as_of"] == "2026-09-25"
    assert data["rubric"]["escalate_on_failure"]["verdict"] == "INSUFFICIENT_DATA"
    assert data["rubric"]["doubt_verifier_diversity"]["verdict"] == "INSUFFICIENT_DATA"


def test_cli_threshold_flags_change_rubric_input():
    _d, log, wfdir = _cli_fixture()
    code, out, err = _run_cli("--log", log, "--workflows-dir", wfdir, "--prices", str(PRICES_PATH),
                              "--json", "--min-records", "1", "--min-workflows", "1")
    assert code == 0, (code, err)
    d1 = [c for c in json.loads(out)["rubric"]["data_quality"]["criteria"] if c["id"] == "D1"][0]
    assert d1["threshold"] == 1 and d1["pass"] is True, d1


def test_cli_backfill_adds_rows_and_reports_counts():
    _d, log, wfdir = _cli_fixture()
    code, out, err = _run_cli("--log", log, "--workflows-dir", wfdir, "--prices", str(PRICES_PATH),
                              "--json", "--backfill-from-transcripts")
    assert code == 0, (code, err)
    data = json.loads(out)
    assert data["backfill"] == {"backfill_parsed": 1, "backfill_missing": 0, "backfill_errors": 0}, data["backfill"]
    assert data["summary"]["totals"]["dispatches"] == 2, data["summary"]["totals"]
    assert data["summary"]["data_quality"]["records"] == 2


def test_cli_text_mentions_estimate_label():
    _d, log, wfdir = _cli_fixture()
    code, out, err = _run_cli("--log", log, "--workflows-dir", wfdir, "--prices", str(PRICES_PATH))
    assert code == 0, (code, err)
    first = out.splitlines()[0]
    assert first.startswith("ESTIMATE:") and "2026-09-25" in first, first
    assert "claude-sonnet-5" in out and "escalate_on_failure" in out and "INSUFFICIENT_DATA" in out


def test_cli_exit_2_when_inputs_unreadable():
    _d, log, wfdir = _cli_fixture()
    missing = os.path.join(_d, "nope")
    code, _out, err = _run_cli("--log", missing, "--workflows-dir", wfdir, "--prices", str(PRICES_PATH))
    assert code == 2 and "--log" in err, (code, err)
    code, _out, err = _run_cli("--log", log, "--workflows-dir", wfdir, "--prices", missing)
    assert code == 2 and "--prices" in err, (code, err)


def test_cli_help_exits_zero():
    code, out, _err = _run_cli("--help")
    assert code == 0 and "--min-records" in out and "--backfill-from-transcripts" in out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    print("test_craftflow_model_report: running")
    names = [n for n in list(globals()) if n.startswith("test_")]
    for name in names:
        fn = globals()[name]
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - test harness reports all failures
            fail(name, type(exc).__name__ + ": " + str(exc) + "\n" + traceback.format_exc(limit=3))
        else:
            ok(name)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print("FAIL (" + str(_passes) + " passed, " + str(len(_errors)) + " failed)", file=sys.stderr)
        return 1
    print("OK (" + str(_passes) + " passed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
