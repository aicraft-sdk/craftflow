#!/usr/bin/env python3
"""craftflow_model_report: per agent x model token and estimated-cost report.

Operator report over the `agent_usage` records that the SubagentStop hook
appends to craftflow-hook-events.log (SPEC-0014, ADR-0048).

This module currently holds the PURE CORE only (no I/O, no clock, no env):

  normalize_model_id(model)                       -> canonical price-table key
  estimate_cost(model_usage, price_row)           -> USD float, or None
  aggregate(usage_rows, stop_rows, workflows, prices) -> report dict

Cost is an ESTIMATE from list prices in config/model-prices.json (DD-6): an
unknown model, a synthetic model or a bucket holding fast-mode messages gives
`None`, never a guessed value. Cache writes are costed from `cache_write_5m`
plus `cache_write_1h`, never from `cache_creation_input_tokens` (that field is
their sum and would double count).

Import-cheap and side-effect free (craftflow_hook_selfcheck imports every
scripts/craftflow_*.py under bare python3 3.9): stdlib only, nothing heavy at
import time, and any future `main()` runs only under `__main__`.
"""
from __future__ import annotations

import re

_DATE_SUFFIX = re.compile(r"-\d{8}$")
_TOKEN_CLASSES = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_write_5m",
    "cache_write_1h",
)
_SHAPES = ("yaml_block", "envelope", "heading_other", "none")
_NOTE_ENVELOPE = "envelope validity not assessed"
_NOTE_UNASSESSABLE = "validity not assessable by shape"
_UNATTRIBUTED = "unattributed"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def normalize_model_id(model):
    """Strip an `@pin` and a trailing `-YYYYMMDD` date; None/empty -> 'unknown'."""
    if not isinstance(model, str) or not model:
        return "unknown"
    base = re.sub(r"@.*$", "", model)
    base = _DATE_SUFFIX.sub("", base)
    return base or "unknown"


def _tok(value):
    """Non-negative int token count; anything else (bool, float, str, None) is 0."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _agent_of(row):
    for key in ("agent_type", "attribution_agent"):
        val = row.get(key)
        if isinstance(val, str) and val:
            return val
    return "unknown"


def _empty_usage():
    usage = {cls: 0 for cls in _TOKEN_CLASSES}
    usage["fast_messages"] = 0
    return usage


def _add_usage(into, bucket):
    for cls in _TOKEN_CLASSES:
        into[cls] += _tok(bucket.get(cls))
    into["fast_messages"] += _tok(bucket.get("fast_messages"))


def _total_tokens(usage):
    return sum(usage[cls] for cls in _TOKEN_CLASSES)


def estimate_cost(model_usage, price_row):
    """USD estimate for one model bucket, or None (unknown price / fast mode)."""
    if not isinstance(price_row, dict) or not isinstance(model_usage, dict):
        return None
    if _tok(model_usage.get("fast_messages")) > 0:
        return None
    try:
        total = (
            _tok(model_usage.get("input_tokens")) * float(price_row["input"])
            + _tok(model_usage.get("output_tokens")) * float(price_row["output"])
            + _tok(model_usage.get("cache_read_input_tokens")) * float(price_row["cache_read"])
            + _tok(model_usage.get("cache_write_5m")) * float(price_row["cache_write_5m"])
            + _tok(model_usage.get("cache_write_1h")) * float(price_row["cache_write_1h"])
        )
    except (KeyError, TypeError, ValueError):
        return None
    return total / 1e6


def _sum_costs(costs):
    """Sum the non-null costs; None when there are none."""
    known = [c for c in costs if c is not None]
    return sum(known) if known else None


def _cost_sort_key(row, *names):
    cost = row["est_cost_usd"]
    return (cost is None, -(cost or 0.0)) + tuple(row[n] for n in names)


def _dedupe_last_wins(usage_rows):
    """E21: a re-fired hook logs a second row per agent_id; the last one wins."""
    out = []
    index_by_id = {}
    for row in usage_rows:
        if not isinstance(row, dict):
            continue
        aid = row.get("agent_id")
        if isinstance(aid, str) and aid:
            if aid in index_by_id:
                out[index_by_id[aid]] = row
                continue
            index_by_id[aid] = len(out)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------

def _usage_view(usage, cost, dispatches):
    view = {"dispatches": dispatches}
    for cls in _TOKEN_CLASSES:
        view[cls] = usage[cls]
    view["est_cost_usd"] = cost
    return view


def _contract_note(counts):
    classified = sum(counts[s] for s in _SHAPES)
    if classified == 0:
        return None
    dominant = _SHAPES[0]
    for shape in _SHAPES[1:]:
        if counts[shape] > counts[dominant]:
            dominant = shape
    if dominant == "envelope":
        return _NOTE_ENVELOPE
    if dominant in ("none", "heading_other"):
        return _NOTE_UNASSESSABLE
    return None


def aggregate(usage_rows, stop_rows, workflows, prices):
    """Aggregate `agent_usage` rows into the report dict consumed by the CLI.

    `workflows` is accepted for the rubric (P4) and unused here; `stop_rows`
    contributes only its count to `data_quality.stop_records`.
    """
    prices = prices if isinstance(prices, dict) else {}
    price_models = prices.get("models") if isinstance(prices.get("models"), dict) else {}
    rows = _dedupe_last_wins(usage_rows)

    am = {}   # (agent, model) -> [usage, dispatches]
    mm = {}   # model -> [usage, dispatches]
    wf = {}   # workflow_id -> {"dispatches": n, "costs": [..]}
    contract = {}
    error_records = partial_records = attributed = 0
    non_error = classified_total = 0

    for row in rows:
        agent = _agent_of(row)
        is_error = bool(row.get("error"))
        if is_error:
            error_records += 1
        if row.get("partial") is True:
            partial_records += 1
        wf_id = row.get("workflow_id")
        has_wf = isinstance(wf_id, str) and bool(wf_id)
        if has_wf:
            attributed += 1

        models = row.get("models") if isinstance(row.get("models"), dict) else {}
        per_model = {}
        for raw_model, bucket in models.items():
            if not isinstance(bucket, dict):
                continue
            model = normalize_model_id(raw_model)
            _add_usage(per_model.setdefault(model, _empty_usage()), bucket)

        row_costs = []
        for model, usage in per_model.items():
            cost = estimate_cost(usage, price_models.get(model))
            row_costs.append(cost)
            a = am.setdefault((agent, model), [_empty_usage(), 0])
            _add_usage(a[0], usage)
            a[1] += 1
            m = mm.setdefault(model, [_empty_usage(), 0])
            _add_usage(m[0], usage)
            m[1] += 1
        if per_model:
            w = wf.setdefault(wf_id if has_wf else _UNATTRIBUTED, {"dispatches": 0, "costs": []})
            w["dispatches"] += 1
            w["costs"].extend(row_costs)

        if is_error:
            continue
        non_error += 1
        shape = row.get("contract_shape")
        classified = shape in _SHAPES
        if classified:
            classified_total += 1
        primary = normalize_model_id(row.get("primary_model"))
        c = contract.setdefault((agent, primary), {
            "dispatches": 0, "classified": 0, "yaml_block": 0, "yaml_valid": 0,
            "envelope": 0, "heading_other": 0, "none": 0, "unclassified": 0})
        c["dispatches"] += 1
        if classified:
            c["classified"] += 1
            c[shape] += 1
            if shape == "yaml_block" and row.get("contract_valid") is True:
                c["yaml_valid"] += 1
        else:
            c["unclassified"] += 1

    by_agent_model = []
    for (agent, model), (usage, n) in am.items():
        view = {"agent": agent, "model": model}
        view.update(_usage_view(usage, estimate_cost(usage, price_models.get(model)), n))
        by_agent_model.append(view)
    by_agent_model.sort(key=lambda r: _cost_sort_key(r, "agent", "model"))

    by_model = []
    for model, (usage, n) in mm.items():
        view = {"model": model}
        view.update(_usage_view(usage, estimate_cost(usage, price_models.get(model)), n))
        by_model.append(view)
    by_model.sort(key=lambda r: _cost_sort_key(r, "model"))

    by_workflow = [
        {"workflow_id": wid, "dispatches": w["dispatches"], "est_cost_usd": _sum_costs(w["costs"])}
        for wid, w in wf.items()
    ]
    by_workflow.sort(key=lambda r: _cost_sort_key(r, "workflow_id"))

    totals_usage = _empty_usage()
    priced_tokens = 0
    all_costs = []
    for usage, _n in mm.values():
        _add_usage(totals_usage, usage)
    for model, (usage, _n) in mm.items():
        cost = estimate_cost(usage, price_models.get(model))
        all_costs.append(cost)
    # priced share is per (agent, model) bucket, matching the printed cost column
    for (_agent, model), (usage, _n) in am.items():
        if estimate_cost(usage, price_models.get(model)) is not None:
            priced_tokens += _total_tokens(usage)
    total_tokens = _total_tokens(totals_usage)
    totals = {cls: totals_usage[cls] for cls in _TOKEN_CLASSES}
    totals["dispatches"] = sum(n for _u, n in am.values())
    totals["est_cost_usd"] = _sum_costs(all_costs)

    contract_rows = []
    for (agent, model), counts in contract.items():
        row = {"agent": agent, "model": model}
        row.update(counts)
        row["note"] = _contract_note(counts)
        contract_rows.append(row)
    contract_rows.sort(key=lambda r: (r["agent"], r["model"]))

    data_quality = {
        "records": len(rows),
        "error_records": error_records,
        "partial_records": partial_records,
        "attributed_records": attributed,
        "priced_token_share": (priced_tokens / total_tokens) if total_tokens else 0.0,
        "distinct_workflows": len([w for w in wf if w != _UNATTRIBUTED]),
        "contract_classified_share": (classified_total / non_error) if non_error else 0.0,
        "stop_records": len(stop_rows) if stop_rows is not None else 0,
    }

    return {
        "by_agent_model": by_agent_model,
        "by_model": by_model,
        "by_workflow": by_workflow,
        "totals": totals,
        "data_quality": data_quality,
        "contract_by_agent_model": contract_rows,
        "price_meta": {
            "label": prices.get("label"),
            "as_of": prices.get("as_of"),
            "source": prices.get("source"),
        },
    }
