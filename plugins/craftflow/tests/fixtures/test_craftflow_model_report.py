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
