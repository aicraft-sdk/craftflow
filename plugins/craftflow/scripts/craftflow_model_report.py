#!/usr/bin/env python3
"""craftflow_model_report: per agent x model token and estimated-cost report.

Operator report over the `agent_usage` records that the SubagentStop hook
appends to craftflow-hook-events.log (SPEC-0014, ADR-0048).

Pure core (no I/O, no clock, no env):

  normalize_model_id(model)                       -> canonical price-table key
  estimate_cost(model_usage, price_row)           -> USD float, or None
  aggregate(usage_rows, stop_rows, workflows, prices) -> report dict
  rubric(summary, stop_rows, workflows, thresholds, usage_rows, prices)
                                                  -> Phase 2 gate verdicts per arm

I/O shell (read-only; the only output is stdout):

  iter_log_rows(path, events, since)   streaming, substring-prefiltered log reader
  load_workflows(dir)                  rigor and loop counts from workflow artifacts
  backfill(stop_rows, recorded_ids)    usage rows synthesized from transcripts
  build_arg_parser() / main(argv)      CLI (text or --json)

Cost is an ESTIMATE from list prices in config/model-prices.json (DD-6): an
unknown model, a synthetic model or a bucket holding fast-mode messages gives
`None`, never a guessed value. Cache writes are costed from `cache_write_5m`
plus `cache_write_1h`, never from `cache_creation_input_tokens` (that field is
their sum and would double count).

Import-cheap and side-effect free (craftflow_hook_selfcheck imports every
scripts/craftflow_*.py under bare python3 3.9): stdlib only, craftflow_hooklib
and craftflow_transcript_usage are imported lazily inside functions, and
`main()` runs only under `__main__`.
"""
from __future__ import annotations

import json
import os
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


# ---------------------------------------------------------------------------
# I/O: streaming log reader, workflow loader
# ---------------------------------------------------------------------------

def iter_log_rows(path, events=("agent_usage", "subagent_stop"), since=None):
    """Stream `(event, row)` pairs from the hook events log, line by line.

    The log can be tens of MB, so each line first passes a cheap substring
    prefilter; only survivors are parsed, and the parsed `event` must match
    exactly (a `pretool_guard` line quoting the text is rejected). `since` is a
    string `ts >=` compare; rows without a `ts` are dropped when it is set.
    """
    needles = tuple('"event": "' + ev + '"' for ev in events)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not any(needle in line for needle in needles):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            event = row.get("event")
            if event not in events:
                continue
            if since is not None:
                ts = row.get("ts")
                if not isinstance(ts, str) or ts < since:
                    continue
            yield event, row


def load_workflows(directory):
    """`{workflow_id: {verification_rigor, loop_counts}}` from `*.json` artifacts."""
    out = {}
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        telemetry = data.get("telemetry") if isinstance(data.get("telemetry"), dict) else {}
        loops = telemetry.get("loop_counts")
        rigor = data.get("verification_rigor")
        out[name[:-len(".json")]] = {
            "verification_rigor": rigor if isinstance(rigor, str) else None,
            "loop_counts": loops if isinstance(loops, dict) else {},
        }
    return out


# ---------------------------------------------------------------------------
# Phase 2 rubric (pure; SPEC-0014 / plan "Phase 2 Gate")
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS = {
    "min_records": 200,
    "min_workflows": 15,
    "max_error_rate": 0.05,
    "min_attribution": 0.90,
    "min_remfix_workflows": 10,
    "min_headroom_share": 0.50,
    "min_remfix_cost_share": 0.15,
    "min_priced_token_share": 0.90,
    "max_contract_missing_delta": 0.05,
    "min_contract_samples": 10,
    "min_critical_path_workflows": 5,
    "max_projected_cost_increase": 0.10,
    "min_default_diverse_share": 0.95,
    "escalation_ceiling_family": "opus",
}
_DEFAULT_FAMILY_RANK = ["haiku", "sonnet", "opus", "fable"]
_BUILDER = "component-builder"
_VERIFIER = "doubt-verifier"
_EPS = 1e-12


def _crit(cid, description, value, threshold, passed):
    return {"id": cid, "description": description, "value": value,
            "threshold": threshold, "pass": passed}


def _slug(row):
    return _agent_of(row).split(":")[-1]


def _wf_of(row):
    wf = row.get("workflow_id")
    return wf if isinstance(wf, str) and wf else None


def _family_of(model, rank):
    """First `family_rank` entry that is a `-<family>-` / `-<family>` segment, else None."""
    norm = normalize_model_id(model)
    if norm == "unknown":
        return None
    for fam in rank:
        if re.search(r"-" + re.escape(fam) + r"(?:-|$)", norm):
            return fam
    return None


def _row_cost(row, price_models):
    """Estimated USD for one usage row (sum of known per-model costs), or None."""
    models = row.get("models") if isinstance(row.get("models"), dict) else {}
    per_model = {}
    for raw_model, bucket in models.items():
        if isinstance(bucket, dict):
            _add_usage(per_model.setdefault(normalize_model_id(raw_model), _empty_usage()), bucket)
    return _sum_costs([estimate_cost(u, price_models.get(m)) for m, u in per_model.items()])


def _mode(values):
    """Most common value; ties resolve to the smallest (deterministic)."""
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    if not counts:
        return None
    return sorted(counts, key=lambda v: (-counts[v], v))[0]


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _share(num, den):
    return (num / den) if den else None


def _contract_bad(row):
    return row.get("contract_shape") != "yaml_block" or row.get("contract_valid") is not True


def _data_gate(dq, th):
    records = dq.get("records", 0)
    non_error = records - dq.get("error_records", 0)
    err_rate = _share(dq.get("error_records", 0) + dq.get("partial_records", 0), records)
    attribution = _share(dq.get("attributed_records", 0), records)
    workflows = dq.get("distinct_workflows", 0)
    priced = dq.get("priced_token_share", 0.0)
    return [
        _crit("D1", "non-error agent_usage records (live plus backfill)",
              non_error, th["min_records"], non_error >= th["min_records"]),
        _crit("D1b", "distinct workflow_ids across those records",
              workflows, th["min_workflows"], workflows >= th["min_workflows"]),
        _crit("D2", "(error + partial) / all records",
              err_rate, th["max_error_rate"], err_rate is not None and err_rate <= th["max_error_rate"]),
        _crit("D3", "records with a workflow_id / all records",
              attribution, th["min_attribution"],
              attribution is not None and attribution >= th["min_attribution"]),
        _crit("D4", "priced tokens / all tokens (cost criteria need priced models)",
              priced, th["min_priced_token_share"],
              records > 0 and priced >= th["min_priced_token_share"]),
    ]


def _arm_a(ok_rows, workflows, th, rank, price_models):
    builders = [r for r in ok_rows if _slug(r) == _BUILDER]
    remfix = [r for r in builders if r.get("is_remfix") is True]
    seen_wf = {_wf_of(r) for r in ok_rows} - {None}

    remfix_wf = {_wf_of(r) for r in ok_rows if r.get("is_remfix") is True} - {None}
    for wid, info in workflows.items():
        loops = info.get("loop_counts") if isinstance(info, dict) else None
        loops = loops if isinstance(loops, dict) else {}
        total = sum(v for v in loops.values() if isinstance(v, (int, float)) and not isinstance(v, bool))
        if wid in seen_wf and total > 0:
            remfix_wf.add(wid)
    a1 = _crit("A1", "workflows with a REM-FIX dispatch or loop_counts > 0",
               len(remfix_wf), th["min_remfix_workflows"], len(remfix_wf) >= th["min_remfix_workflows"])

    ceiling = th["escalation_ceiling_family"]
    ceiling_rank = rank.index(ceiling) if ceiling in rank else None
    famed = [(r, _family_of(r.get("primary_model"), rank)) for r in remfix]
    famed = [(r, f) for r, f in famed if f is not None]
    headroom = [(r, f) for r, f in famed if ceiling_rank is not None and rank.index(f) < ceiling_rank]
    a2_val = _share(len(headroom), len(famed))
    a2 = _crit("A2", "share of component-builder REM-FIX dispatches below the ceiling family (" + str(ceiling) + ")",
               a2_val, th["min_headroom_share"],
               None if a2_val is None else a2_val >= th["min_headroom_share"])

    remfix_cost = _sum_costs([_row_cost(r, price_models) for r in remfix])
    builder_cost = _sum_costs([_row_cost(r, price_models) for r in builders])
    a3_val = _share(remfix_cost or 0.0, builder_cost) if builder_cost else None
    a3 = _crit("A3", "REM-FIX estimated cost / all component-builder estimated cost",
               a3_val, th["min_remfix_cost_share"],
               None if a3_val is None else a3_val >= th["min_remfix_cost_share"])

    # current family = most common family among headroom REM-FIX builders; target is
    # always exactly one rank up, so an escalation can never point downward.
    counts = {}
    for _r, fam in headroom:
        counts[fam] = counts.get(fam, 0) + 1
    current = sorted(counts, key=lambda f: (-counts[f], rank.index(f)))[0] if counts else None
    target = None
    if current is not None and rank.index(current) + 1 < len(rank):
        target = rank[rank.index(current) + 1]
    a4_desc = ("contract-bad rate delta (target family minus current family), component-builder only; "
               "reads contract_shape/contract_valid from agent_usage rows, NOT the old subagent_stop "
               "contract_found/contract_valid fields; rows without contract_shape are excluded; "
               "needs >= " + str(th["min_contract_samples"]) + " classified dispatches per family")
    a4_val = None
    a4_pass = None
    if current is not None and target is not None:
        classified = [r for r in builders if isinstance(r.get("contract_shape"), str)]
        cur_rows = [r for r in classified if _family_of(r.get("primary_model"), rank) == current]
        tgt_rows = [r for r in classified if _family_of(r.get("primary_model"), rank) == target]
        if len(cur_rows) >= th["min_contract_samples"] and len(tgt_rows) >= th["min_contract_samples"]:
            a4_val = (sum(1 for r in tgt_rows if _contract_bad(r)) / len(tgt_rows)
                      - sum(1 for r in cur_rows if _contract_bad(r)) / len(cur_rows))
            a4_pass = a4_val <= th["max_contract_missing_delta"] + _EPS
    a4 = _crit("A4", a4_desc, a4_val, th["max_contract_missing_delta"], a4_pass)
    return [a1, a2, a3, a4]


def _arm_a_verdict(gate_ok, criteria):
    a1, rest = criteria[0], criteria[1:]
    if not gate_ok or a1["pass"] is not True:
        return "INSUFFICIENT_DATA"
    if any(c["pass"] is False for c in rest):
        return "NO_GO"
    if any(c["pass"] is None for c in rest):
        return "INSUFFICIENT_DATA"
    return "GO"


def _verifier_cost_fraction(rows, rank, price_models):
    """Projected cost fraction if this workflow's doubt-verifier ran one family up, or None."""
    vrows = [r for r in rows if _slug(r) == _VERIFIER]
    vmodel = _mode([normalize_model_id(r.get("primary_model")) for r in vrows if r.get("primary_model")])
    fam = _family_of(vmodel, rank)
    cur_price = price_models.get(vmodel) if vmodel else None
    cur_in = cur_price.get("input") if isinstance(cur_price, dict) else None
    if fam is None or not isinstance(cur_in, (int, float)) or cur_in <= 0:
        return None
    up = rank.index(fam) + 1
    inputs = []
    for name, price in price_models.items():
        pf = _family_of(name, rank)
        if pf is not None and rank.index(pf) == up and isinstance(price, dict):
            val = price.get("input")
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                inputs.append(val)
    v_cost = _sum_costs([_row_cost(r, price_models) for r in vrows])
    w_cost = _sum_costs([_row_cost(r, price_models) for r in rows])
    if not inputs or v_cost is None or not w_cost:
        return None
    return v_cost * (min(inputs) / cur_in - 1) / w_cost


def _arm_b(ok_rows, all_rows, workflows, th, rank, price_models):
    cp_wfs = {wid for wid, info in workflows.items()
              if isinstance(info, dict) and info.get("verification_rigor") == "critical_path"}
    by_wf = {}
    for r in ok_rows:
        wid = _wf_of(r)
        if wid in cp_wfs:
            by_wf.setdefault(wid, []).append(r)
    b1_wfs = sorted(w for w, rs in by_wf.items() if any(_slug(r) == _VERIFIER for r in rs))
    b1 = _crit("B1", "critical_path workflows containing a doubt-verifier dispatch",
               len(b1_wfs), th["min_critical_path_workflows"],
               len(b1_wfs) >= th["min_critical_path_workflows"])

    def model_of(rows, slug):
        return _mode([normalize_model_id(r.get("primary_model")) for r in rows
                      if _slug(r) == slug and r.get("primary_model")])

    diverse = set()
    for wid in b1_wfs:
        bm, vm = model_of(by_wf[wid], _BUILDER), model_of(by_wf[wid], _VERIFIER)
        bf, vf = _family_of(bm, rank), _family_of(vm, rank)
        if bm and vm and bm != vm and bf and vf and rank.index(vf) >= rank.index(bf):
            diverse.add(wid)
    residual = [w for w in b1_wfs if w not in diverse]
    share = _share(len(diverse), len(b1_wfs))
    b0 = _crit("B0", "share of B1 workflows where the doubt-verifier model differs from and is "
                     "family-rank >= the component-builder model already (pass = below threshold, "
                     "so an override is still needed for the residual)",
               share, th["min_default_diverse_share"],
               None if share is None else share < th["min_default_diverse_share"])

    # B2/B3 cover only the residual workflows: the override targets only those.
    b2_val, b2_pass = None, None
    if residual:
        residual_set = set(residual)
        builders = [r for r in all_rows if _slug(r) == _BUILDER and _wf_of(r) in residual_set]
        good = [r for r in builders if r.get("primary_model") and not r.get("error")]
        covered = all(any(_slug(r) == _BUILDER and _wf_of(r) == w for r in builders) for w in residual)
        b2_val = _share(len(good), len(builders)) if builders else 0.0
        b2_pass = bool(covered and builders and len(good) == len(builders))
    b2 = _crit("B2", "residual workflows: share of component-builder dispatches with a known primary_model and no error",
               b2_val, 1.0, b2_pass)

    b3_val, b3_pass = None, None
    if residual:
        fractions = [_verifier_cost_fraction(by_wf[w], rank, price_models) for w in residual]
        if all(f is not None for f in fractions):
            b3_val = _median(fractions)
            b3_pass = b3_val <= th["max_projected_cost_increase"] + _EPS
    b3 = _crit("B3", "median projected workflow cost increase if doubt-verifier ran one family up",
               b3_val, th["max_projected_cost_increase"], b3_pass)
    b4 = _crit("B4", "external: ADR-0046 seeded-defect comparison; not computable from telemetry",
               None, None, None)
    return [b1, b0, b2, b3, b4]


def _arm_b_verdict(gate_ok, criteria):
    b1, b0, b2, b3 = criteria[0], criteria[1], criteria[2], criteria[3]
    if not gate_ok or b1["pass"] is not True:
        return "INSUFFICIENT_DATA"
    if b0["pass"] is False or b2["pass"] is False or b3["pass"] is False:
        return "NO_GO"
    if b0["pass"] is None or b2["pass"] is None or b3["pass"] is None:
        return "INSUFFICIENT_DATA"
    # never GO from telemetry alone: B4 needs human evidence (ADR-0046)
    return "DATA_GO_PENDING_ADR0046_EVAL"


def rubric(summary, stop_rows, workflows, thresholds, usage_rows=(), prices=None):
    """Phase 2 gate verdicts for both arms. Pure; `stop_rows` is not read for A4 (DD-13).

    `summary` is the `aggregate()` dict; `usage_rows` and `prices` supply the
    row-level facts (REM-FIX flag, primary model, contract shape, costs) that
    the summary does not carry. Evaluate on the `--since`-filtered rows.
    """
    th = dict(DEFAULT_THRESHOLDS)
    th.update({k: v for k, v in (thresholds or {}).items() if v is not None})
    prices = prices if isinstance(prices, dict) else {}
    price_models = prices.get("models") if isinstance(prices.get("models"), dict) else {}
    rank = prices.get("family_rank") if isinstance(prices.get("family_rank"), list) else _DEFAULT_FAMILY_RANK
    workflows = workflows if isinstance(workflows, dict) else {}
    all_rows = _dedupe_last_wins(usage_rows)
    ok_rows = [r for r in all_rows if not r.get("error")]

    gate = _data_gate(summary.get("data_quality", {}), th)
    gate_ok = all(c["pass"] is True for c in gate)
    unfamilied = sum(1 for r in ok_rows
                     if r.get("primary_model") and _family_of(r.get("primary_model"), rank) is None)

    a_crit = _arm_a(ok_rows, workflows, th, rank, price_models)
    b_crit = _arm_b(ok_rows, all_rows, workflows, th, rank, price_models)
    return {
        "escalate_on_failure": {"verdict": _arm_a_verdict(gate_ok, a_crit), "criteria": a_crit},
        "doubt_verifier_diversity": {"verdict": _arm_b_verdict(gate_ok, b_crit), "criteria": b_crit},
        "data_quality": {"pass": gate_ok, "criteria": gate, "unfamilied_records": unfamilied},
    }


# ---------------------------------------------------------------------------
# Backfill from transcripts (impure, read-only)
# ---------------------------------------------------------------------------

_MISSING_ERRORS = ("no_transcript_path", "transcript_missing")
_LEGACY_REASONS = ("contract_present", "contract_missing")
_BACKFILL_DEADLINE_S = 30.0


def _is_craftflow_stop(row):
    """Match the population the SubagentStop hook logs (craftflow-filtered stops)."""
    agent_type = row.get("agent_type")
    agent_type = agent_type if isinstance(agent_type, str) else ""
    if agent_type.startswith("craftflow:"):
        return True
    return agent_type == "" and row.get("reason") in _LEGACY_REASONS


def backfill(stop_rows, recorded_ids):
    """Synthesize `agent_usage` rows from transcripts of stops that lack one.

    Read-only: transcripts are only read, and `final_text` is consumed for the
    contract classification and never emitted. Missing transcripts are counted,
    never fatal. Returns `(rows, stats)`.
    """
    # lazy: keep `import craftflow_model_report` cheap for the hook selfcheck
    from craftflow_transcript_usage import (  # noqa: PLC0415
        SCHEMA_VERSION,
        classify_contract,
        summarize_transcript,
    )

    stats = {"backfill_parsed": 0, "backfill_missing": 0, "backfill_errors": 0}
    rows = []
    for stop in stop_rows:
        if not isinstance(stop, dict) or not _is_craftflow_stop(stop):
            continue
        agent_id = stop.get("agent_id")
        if isinstance(agent_id, str) and agent_id and agent_id in recorded_ids:
            continue
        summary = summarize_transcript(stop.get("agent_transcript_path"), deadline_s=_BACKFILL_DEADLINE_S,
                                       want_final_text=True)
        error = summary.get("error")
        if error in _MISSING_ERRORS:
            stats["backfill_missing"] += 1
            continue
        if error is not None:
            stats["backfill_errors"] += 1
            continue
        final_text = summary.pop("final_text", "")
        agent_type = stop.get("agent_type") if isinstance(stop.get("agent_type"), str) else ""
        slug = agent_type.split(":")[-1]
        contract = classify_contract(final_text, slug)
        row = {
            "event": "agent_usage",
            "ts": stop.get("ts"),
            "schema": SCHEMA_VERSION,
            "source": "backfill",
            "agent_type": agent_type,
            "agent_id": agent_id if isinstance(agent_id, str) else "",
            "contract_source": "transcript_final_text",
            "contract_shape": contract["contract_shape"],
            "contract_valid": contract["contract_valid"],
            "contract_errors": contract["contract_errors"],
        }
        row.update(summary)
        rows.append(row)
        stats["backfill_parsed"] += 1
    return rows, stats


# ---------------------------------------------------------------------------
# text rendering
# ---------------------------------------------------------------------------

def _fmt_cost(value):
    return "n/a" if value is None else "$" + format(value, ",.4f")


def _fmt_num(value):
    return format(value, ",")


def _table(headers, rows, left=1):
    """Fixed-width table; the first `left` columns are left aligned."""
    cells = [headers] + rows
    widths = [max(len(str(r[i])) for r in cells) for i in range(len(headers))]
    out = []
    for r in cells:
        parts = []
        for i, cell in enumerate(r):
            text = str(cell)
            parts.append(text.ljust(widths[i]) if i < left else text.rjust(widths[i]))
        out.append("  ".join(parts).rstrip())
    return out


_USAGE_HEADERS = ["dispatches", "input", "output", "cache_read", "cache_write", "est_cost"]


def _usage_cells(row):
    return [_fmt_num(row["dispatches"]), _fmt_num(row["input_tokens"]), _fmt_num(row["output_tokens"]),
            _fmt_num(row["cache_read_input_tokens"]),
            _fmt_num(row["cache_write_5m"] + row["cache_write_1h"]), _fmt_cost(row["est_cost_usd"])]


def _fmt_criterion(crit):
    return ("    " + str(crit["id"]) + ": " + str(crit["description"]) + " (value=" + str(crit["value"])
            + ", threshold=" + str(crit["threshold"]) + ", pass=" + str(crit["pass"]) + ")")


def format_text(report, top_workflows=10):
    summary, rub = report["summary"], report["rubric"]
    meta = summary["price_meta"]
    lines = [str(meta.get("label")) + " (prices as of " + str(meta.get("as_of")) + ")"]
    if meta.get("source"):
        lines.append("source: " + str(meta["source"]))
    if report.get("since"):
        lines.append("since: " + str(report["since"]))
    lines += ["", "By agent x model"]
    lines += _table(["agent", "model"] + _USAGE_HEADERS,
                    [[r["agent"], r["model"]] + _usage_cells(r) for r in summary["by_agent_model"]], left=2)
    lines += ["", "By model"]
    lines += _table(["model"] + _USAGE_HEADERS,
                    [[r["model"]] + _usage_cells(r) for r in summary["by_model"]])
    totals = summary["totals"]
    lines += ["", "Total: " + _fmt_num(totals["dispatches"]) + " dispatches, est "
              + _fmt_cost(totals["est_cost_usd"])]
    lines += ["", "Top " + str(top_workflows) + " workflows by estimated cost"]
    lines += _table(["workflow_id", "dispatches", "est_cost"],
                    [[w["workflow_id"], _fmt_num(w["dispatches"]), _fmt_cost(w["est_cost_usd"])]
                     for w in summary["by_workflow"][:top_workflows]])
    lines += ["", "Data quality"]
    for key, value in summary["data_quality"].items():
        lines.append("  " + key + ": " + (format(value, ".3f") if isinstance(value, float) else str(value)))
    lines += ["", "Phase 2 rubric", "  data gate: " + ("PASS" if rub["data_quality"]["pass"] else "FAIL")]
    for crit in rub["data_quality"]["criteria"]:
        if crit["pass"] is not True:
            lines.append(_fmt_criterion(crit))
    for arm in ("escalate_on_failure", "doubt_verifier_diversity"):
        lines.append("  " + arm + ": " + rub[arm]["verdict"])
        for crit in rub[arm]["criteria"]:
            if crit["pass"] is not True:
                lines.append(_fmt_criterion(crit))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    import argparse  # noqa: PLC0415 - keep module import cheap

    parser = argparse.ArgumentParser(
        prog="craftflow_model_report",
        description="Per agent x model token and ESTIMATED cost report over agent_usage telemetry "
                    "(SPEC-0014), with the Phase 2 escalation/diversity rubric.")
    parser.add_argument("--log", help="hook events log (default: <state_root>/craftflow-hook-events.log)")
    parser.add_argument("--workflows-dir", help="workflow artifacts dir (default: <state_root>/workflows)")
    parser.add_argument("--prices", help="price table (default: <plugin>/config/model-prices.json)")
    parser.add_argument("--since", help="only rows with ts >= this string (ISO date or timestamp); "
                                        "use the pin release ts so Phase 2 arms see post-pin data only")
    parser.add_argument("--backfill-from-transcripts", action="store_true",
                        help="synthesize usage rows from subagent_stop transcripts lacking an agent_usage row "
                             "(read-only)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--top-workflows", type=int, default=10, help="workflows shown by cost (default 10)")
    for name, default in DEFAULT_THRESHOLDS.items():
        parser.add_argument("--" + name.replace("_", "-"), "--" + name, dest=name, type=type(default),
                            default=default, help="rubric threshold (default %(default)s)")
    return parser


def _load_prices(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("price table must be a JSON object")
    return data


def build_report(usage_rows, stop_rows, workflows, prices, thresholds, since=None, backfill_stats=None,
                 top_workflows=10):
    summary = aggregate(usage_rows, stop_rows, workflows, prices)
    if backfill_stats:
        summary["data_quality"].update(backfill_stats)
    summary["by_workflow"] = summary["by_workflow"][:max(top_workflows, 0)]
    result = rubric(summary, stop_rows, workflows, thresholds, usage_rows=usage_rows, prices=prices)
    return {"summary": summary, "rubric": result, "since": since, "backfill": backfill_stats}


def main(argv=None):
    import sys  # noqa: PLC0415

    args = build_arg_parser().parse_args(argv)
    defaults = {}
    if not (args.log and args.workflows_dir and args.prices):
        # lazy: hooklib is heavy and resolves the project state directory
        import craftflow_hooklib as hooklib  # noqa: PLC0415
        defaults = {
            "log": str(hooklib.state_root() / "craftflow-hook-events.log"),
            "workflows_dir": str(hooklib.workflows_dir()),
            "prices": str(hooklib.plugin_config_dir() / "model-prices.json"),
        }
    log = args.log or defaults["log"]
    workflows_dir = args.workflows_dir or defaults["workflows_dir"]
    prices_path = args.prices or defaults["prices"]

    try:
        prices = _load_prices(prices_path)
    except (OSError, ValueError) as exc:
        print("craftflow_model_report: cannot read --prices " + prices_path + ": " + str(exc), file=sys.stderr)
        return 2
    if not os.path.isfile(log) or not os.access(log, os.R_OK):
        print("craftflow_model_report: cannot read --log " + log, file=sys.stderr)
        return 2

    usage_rows, stop_rows = [], []
    try:
        for event, row in iter_log_rows(log, since=args.since):
            (usage_rows if event == "agent_usage" else stop_rows).append(row)
    except OSError as exc:
        print("craftflow_model_report: cannot read --log " + log + ": " + str(exc), file=sys.stderr)
        return 2

    stats = None
    if args.backfill_from_transcripts:
        recorded = {r.get("agent_id") for r in usage_rows if isinstance(r.get("agent_id"), str)}
        extra, stats = backfill(stop_rows, recorded)
        usage_rows.extend(extra)

    thresholds = {name: getattr(args, name) for name in DEFAULT_THRESHOLDS}
    report = build_report(usage_rows, stop_rows, load_workflows(workflows_dir), prices, thresholds,
                          since=args.since, backfill_stats=stats, top_workflows=args.top_workflows)
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(format_text(report, args.top_workflows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
