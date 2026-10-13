#!/usr/bin/env python3
"""Cut-plan projection (BC-11, PP-10): applies cut-plan rows to the baseline bytes and reports four tiers.

A cut plan is {"rows": [...]}. Each row has id, phase (P2-P13), op, condition and op-specific fields; the ops are move,
dedupe, delete, insert, replace, readmap, contingency and descriptions (see the module-level docs in
tests/fixtures/test_router_readset.py for the row fields). Anchors are exact lines (a multi-line anchor is a run of
consecutive lines) and must match exactly one place in the projected file at the moment the row applies; otherwise
ProjectionError (exit 2). A row with status `needs_anchor` is not applied; it must state a direction and a size estimate, and
is listed under `unanchored` with the net `bias` of all such rows. Unknown phase ids, condition names and flag values, a move or
insert that creates a file the projected read map does not name, a whole-file replace of an unknown file and a read-map edit with
no table markers to re-render are errors, not silent no-ops (see craftflow_router_readset_guard.py). A row tagged `feasibility` needs a
reach-model amendment (the P0 reach rule would refuse it): it is left out of `tiers` unless `assume_reach_amendment`, and
`tiers_if_reach_amended` carries the other reading. Every tier also reports `happy_path_total` (start set plus the stage files read on
every run). The reach report of each projected tree is craftflow_router_readset_feasibility.py. Python 3.9 compatible.
"""
from __future__ import annotations

import copy
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import craftflow_router_corpus as cor
import craftflow_router_readset_core as core
import craftflow_router_readset_guard as grd
from craftflow_router_readset_guard import ALL_PHASES, ProjectionError  # noqa: F401 - re-exported for callers and tests

SKILL = "skills/craftflow-router/SKILL.md"
PROTOCOL = "skills/_shared/router-protocol.md"
POLICY = "config/policy.json"
TIERS = ("guaranteed", "conditional", "l3_upper_bound", "with_contingencies")
GATED = re.compile(r"^skills/(craftflow-router/references|_shared)/[^/]+\.md$")


def is_gated(path: str) -> bool:
    return path in (SKILL, POLICY) or bool(GATED.match(path))


def make_state(files: Dict[str, str], readmap: dict, descriptions: Optional[Dict[str, str]] = None) -> dict:
    return {"files": dict(files), "readmap": copy.deepcopy(readmap), "descriptions": dict(descriptions or {}), "notes": []}


def load_baseline_state(plugin_root, ref: str, readmap_path) -> dict:
    root = Path(plugin_root)
    files = {rel: grd.read_exact(root, rel, ref) for rel in cor.runtime_files(root, ref)}
    descs = {}
    for rel in (n for n in grd.ls_tree(root, ref, "skills") if re.fullmatch(r"skills/[^/]+/SKILL\.md", n)):
        descs[rel.split("/")[1]] = core.effective_description(grd.read_exact(root, rel, ref))
    return make_state(files, cor.load_json(readmap_path), descs)


# ---------------------------------------------------------------------------------------------- anchors


def _lines(text: str) -> List[str]:
    return text.splitlines(keepends=True)


def _find(lines: List[str], anchor: str, where: str, lo: int = 0) -> List[int]:
    want = anchor.split("\n")
    n = len(want)
    plain = [x.rstrip("\n") for x in lines]
    return [i for i in range(lo, len(plain) - n + 1) if plain[i:i + n] == want]


def _one(lines: List[str], anchor: str, where: str, lo: int = 0) -> int:
    if not isinstance(anchor, str) or not anchor:
        raise ProjectionError(f"{where}: empty anchor")
    hits = _find(lines, anchor, where, lo)
    if len(hits) != 1:
        raise ProjectionError(f"{where}: anchor {anchor[:70]!r} matches {len(hits)} places (exactly one required)")
    return hits[0]


def _span(lines: List[str], row: dict, where: str) -> tuple:
    start = _one(lines, row.get("start_anchor"), where)
    k_start = len(row["start_anchor"].split("\n"))
    end_anchor = row.get("end_anchor")
    if not end_anchor:
        return start, start + k_start
    end = _one(lines, end_anchor, where + " (end)", start + k_start - 1)
    return start, end + len(end_anchor.split("\n"))


def _file(state: dict, name: str, where: str) -> str:
    if name not in state["files"]:
        raise ProjectionError(f"{where}: file {name!r} does not exist in the projected tree")
    return state["files"][name]


# ---------------------------------------------------------------------------------------------- operations


def apply_readmap_row(readmap: dict, r: dict) -> None:
    files, action, spec = readmap["files"], r.get("action"), r.get("row") or {}
    match = r.get("match") or {"file": spec.get("file"), "class": spec.get("class")}
    idx = [i for i, e in enumerate(files) if e.get("file") == match.get("file") and e.get("class") == match.get("class")]
    if action == "add":
        if idx:
            raise ProjectionError(f"{r['id']}: read map already has {match}")
        files.append(copy.deepcopy(spec))
    elif action in ("update", "remove"):
        if len(idx) != 1:
            raise ProjectionError(f"{r['id']}: read map {action} matches {len(idx)} entries for {match}")
        if action == "remove":
            del files[idx[0]]
        else:
            files[idx[0]].update(copy.deepcopy(spec))
    else:
        raise ProjectionError(f"{r['id']}: unknown readmap action {action!r}")


def _text(r: dict, key: str = "text") -> str:
    if not isinstance(r.get(key), str):
        raise ProjectionError(f"{r.get('id')}: {r.get('op')} row needs a string {key}")
    return r[key]


def _insert(state: dict, r: dict) -> None:
    f, pos, text = r.get("file"), r.get("position"), _text(r)
    if pos == "new_file":
        if f in state["files"]:
            raise ProjectionError(f"{r['id']}: new_file target {f} already exists")
        state["files"][f] = text
    elif pos == "end_of_file":
        cur = _file(state, f, r["id"])
        state["files"][f] = cur + ("" if cur.endswith("\n") or not cur else "\n") + text
    elif pos in ("after_anchor", "before_anchor"):
        lines = _lines(_file(state, f, r["id"]))
        i = _one(lines, r.get("anchor"), r["id"])
        i += len(r["anchor"].split("\n")) if pos == "after_anchor" else 0
        state["files"][f] = "".join(lines[:i]) + text + "".join(lines[i:])
    else:
        raise ProjectionError(f"{r['id']}: unknown insert position {pos!r}")


def _replace(state: dict, r: dict, key: str = "text") -> None:
    f, text = r.get("file"), _text(r, key)
    if r.get("whole_file"):
        _file(state, f, r["id"])  # replacing a file the tree does not have is an insert new_file, not a replace
        state["files"][f] = text
        return
    lines = _lines(_file(state, f, r["id"]))
    a, b = _span(lines, r, r["id"])
    state["files"][f] = "".join(lines[:a]) + text + "".join(lines[b:])


def _delete(state: dict, r: dict) -> None:
    f = r.get("file") or r.get("src")
    lines = _lines(_file(state, f, r["id"]))
    a, b = _span(lines, r, r["id"])
    state["files"][f] = "".join(lines[:a] + lines[b:])


def _move(state: dict, r: dict) -> None:
    src, dst = r.get("src"), r.get("dst")
    lines = _lines(_file(state, src, r["id"]))
    a, b = _span(lines, r, r["id"])
    moved, pointer = "".join(lines[a:b]), r.get("pointer_text", "")
    if not isinstance(pointer, str):
        raise ProjectionError(f"{r['id']}: move needs a string pointer_text (empty string for none)")
    state["files"][src] = "".join(lines[:a]) + pointer + "".join(lines[b:])
    state.setdefault("move_log", []).append(grd.move_facts(r, moved))  # section-move rows applied to this tree (feasibility.signing_burden)
    cur =state["files"].get(dst, "")
    sep = "" if cur.endswith("\n") or not cur else "\n"
    state["files"][dst] = cur + sep + r.get("dst_header", "") + moved


def _descriptions(state: dict, r: dict) -> None:
    f, text = r.get("file", ""), _text(r)
    m = re.fullmatch(r"skills/([^/]+)/SKILL\.md", f)
    if not m:
        raise ProjectionError(f"{r['id']}: descriptions row must name a skills/<name>/SKILL.md file")
    state["descriptions"][m.group(1)] = text
    if f == SKILL:
        state["files"][SKILL] = core.description_edit(_file(state, SKILL, r["id"]), text)


def apply_row(state: dict, r: dict) -> None:
    if r.get("status") == "needs_anchor":
        return  # reported by project() under `unanchored`; its bytes are in no tier
    op = r.get("op")
    if op == "insert":
        _insert(state, r)
    elif op == "replace":
        _replace(state, r)
    elif op == "dedupe":
        _replace(state, r, "replacement_text")
    elif op == "delete":
        _delete(state, r)
    elif op == "move":
        _move(state, r)
    elif op == "readmap":
        apply_readmap_row(state["readmap"], r)
    elif op == "descriptions":
        _descriptions(state, r)
    elif op == "contingency":
        _insert(state, r) if "position" in r else _replace(state, r)
    else:
        raise ProjectionError(f"{r.get('id')}: unknown op {op!r}")


def apply_rows(state: dict, rows: Iterable[dict]) -> dict:
    """Apply rows in the given order. After a run of rows of one phase that edited the read map, the kernel's Read Map
    table is re-rendered by the same renderer the kernel uses, so its bytes are exact."""
    dirty, prev = False, None
    for r in rows:
        if prev is not None and r["phase"] != prev and dirty:
            _rerender(state)
            dirty = False
        apply_row(state, r)
        dirty = dirty or r.get("op") == "readmap"
        prev = r["phase"]
    if dirty:
        _rerender(state)
    return state


def _rerender(state: dict) -> None:
    if SKILL not in state["files"]:
        return
    text = state["files"][SKILL]
    if core.table_span(text) is not None:
        state["files"][SKILL] = core.rerender_table(text, state["readmap"])
        return
    if not state.get("markers_optional"):
        raise ProjectionError("read-map rows changed the map but the kernel has no readmap markers to re-render "
                              "(the P2 table insert must be part of the projection)")
    note = "kernel table re-render skipped: the kernel has no readmap markers (P2 is not in --phases)"
    if note not in state["notes"]:
        state["notes"].append(note)


def _check_new_files_mapped(state: dict, state0: dict, label: str) -> None:
    """A file the rows create but the projected read map does not name would drop its bytes from every read set."""
    mapped = {e["file"] for e in state["readmap"]["files"]}
    new = sorted(set(state["files"]) - set(state0["files"]) - mapped - {POLICY})  # policy.json is a start-set extra, not a map entry
    if new:
        raise ProjectionError(f"{label}: rows create {new} but the projected read map names no such file "
                              "(its bytes would vanish from the read set)")


# ---------------------------------------------------------------------------------------------- selection and tiers


def select_rows(cutplan: dict, phases: Optional[Iterable[str]] = None) -> List[dict]:
    """Rows of the given phases in canonical order (phase number, then file order). None selects every phase."""
    grd.check_phase_ids([r.get("phase") for r in cutplan["rows"]], "cut-plan rows", rowless_ok=False)
    if phases is not None:
        grd.check_phase_ids(phases, "--phases", rowless_ok=True)
    want = set(ALL_PHASES) if phases is None else set(phases)
    indexed = [(int(r["phase"][1:]), i, r) for i, r in enumerate(cutplan["rows"]) if r["phase"] in want]
    return [r for _, _, r in sorted(indexed, key=lambda t: (t[0], t[1]))]


def _sizes(state: dict) -> Dict[str, int]:
    return {k: len(v.encode("utf-8")) for k, v in state["files"].items()}


@lru_cache(maxsize=64)
def _nonnormative(text: str, name: str, allow: frozenset) -> int:
    import craftflow_rule_inventory as inv
    return inv.report_nonnormative({name: text}, [name], allow)["total"]


def _desc_sum(state: dict) -> int:
    d = dict(state["descriptions"])
    if SKILL in state["files"]:
        d["craftflow-router"] = core.effective_description(state["files"][SKILL])
    return sum(len(v) for v in d.values())


def measure_state(state: dict, l3_allowlist: Optional[frozenset] = None) -> dict:
    """One tier's numbers: per-workflow totals per variant, gated per-file bytes and the three headline sizes.
    With `l3_allowlist` the whole non-normative units of each start-set file are subtracted (the L3 upper bound)."""
    sizes = _sizes(state)
    rm = state["readmap"]
    start_files = {e["file"] for e in rm["files"] if e["class"] in ("kernel", "start")}
    if l3_allowlist is not None:
        for f in start_files:
            if f in sizes and f in state["files"]:
                sizes[f] -= _nonnormative(state["files"][f], f, l3_allowlist)
    extra = {POLICY: sizes[POLICY]} if POLICY in sizes else None
    per_wf, variants, happy = {}, {}, {}
    for wf in core.variants_of(rm):
        res = core.compute_from_sizes(rm, sizes, wf, extra=extra)
        variants[wf] = {v: d["start_total"] for v, d in res["variants"].items()}
        per_wf[wf] = ({**variants[wf], "max": res["start_total"]} if wf == "BUILD" else res["start_total"])
        hp = {v: d["happy_path_total"] for v, d in res["variants"].items()}  # start set plus the stage files read on every run
        happy[wf] = {**hp, "max": max(hp.values())} if wf == "BUILD" else max(hp.values())
    gated = {k: v for k, v in sizes.items() if is_gated(k)}
    refs = [v for k, v in gated.items() if GATED.match(k) and k != PROTOCOL]
    return {"tier": per_wf, "variants": variants, "happy": happy, "per_file": {**dict(sorted(gated.items())), "kernel_bytes": sizes.get(SKILL, 0),
            "protocol_bytes": sizes.get(PROTOCOL, 0), "reference_max_bytes": max(refs, default=0)},
            "descriptions": _desc_sum(state)}


def _capped_descriptions(state: dict, rows: List[dict]) -> int:
    d = dict(state["descriptions"])
    for r in rows:
        m = re.fullmatch(r"skills/([^/]+)/SKILL\.md", r.get("file", ""))
        if r.get("op") == "descriptions" and r.get("status") == "needs_anchor" and m and r.get("max_chars"):
            d[m.group(1)] = d[m.group(1)][:r["max_chars"]] if m.group(1) in d else ""
    if SKILL in state["files"]:
        d["craftflow-router"] = core.effective_description(state["files"][SKILL])
    return sum(len(v) for v in d.values())


def project(cutplan: dict, state0: dict, phases: Optional[Iterable[str]] = None, conditions: Optional[dict] = None,
            l3_allowlist: Optional[frozenset] = None, assume_reach_amendment: bool = False, with_states: bool = False,
            reach_amendment: Optional[str] = None) -> dict:
    """BC-11 output. `effective` honours the recorded conditions (the per-PR check); the four tiers use the plan's own.
    Rows tagged `feasibility` (they need a reach-model amendment) are left out of `tiers` unless `assume_reach_amendment`;
    `tiers_if_reach_amended` always carries the other reading. `effective` leaves them out too unless `reach_amendment` is "D17-B"
    (the value router-budget.json records once David decides it). `with_states` returns (output, every projected tree) for the reach report."""
    flags = grd.check_flags({} if conditions is None else conditions)
    grd.check_row_conditions(cutplan["rows"])
    rows = select_rows(cutplan, phases)
    markers_optional = "P2" not in (ALL_PHASES if phases is None else set(phases))
    plain = [r for r in rows if r["op"] != "contingency"]
    sets = grd.tier_row_sets(plain, [r for r in rows if r["op"] == "contingency"], flags, reach_amendment)
    in_eff = {r["id"] for r in sets["effective"]}
    tagged_on = sorted(r["id"] for r in plain if r.get("feasibility") and (r["condition"] == "always" or flags.get(r["condition"])))
    allow = l3_allowlist if l3_allowlist is not None else frozenset()
    cache: Dict[str, dict] = {}

    def state(name: str) -> dict:
        if name not in cache:
            st = copy.deepcopy(state0)
            st["markers_optional"] = markers_optional
            apply_rows(st, sets[name])
            _check_new_files_mapped(st, state0, "projection")
            cache[name] = st
        return cache[name]

    def tiers_of(sfx: str) -> dict:
        return {"guaranteed": measure_state(state("guaranteed" + sfx)), "conditional": measure_state(state("conditional" + sfx)),
                "l3_upper_bound": measure_state(state("conditional" + sfx), allow),
                "with_contingencies": measure_state(state("with_contingencies" + sfx), allow)}

    amend = "_if_reach_amended"
    main = tiers_of(amend if assume_reach_amendment else "")
    alt = main if assume_reach_amendment else tiers_of(amend)  # the reading that includes the rows needing the amendment
    eff = measure_state(state("effective"))
    unanchored = grd.unanchored_report(rows, state0["descriptions"])
    out = {"phases": sorted(set(ALL_PHASES) if phases is None else set(phases), key=lambda p: (int(p[1:].rstrip("C")), p.endswith("C"))),
           "assume_reach_amendment": bool(assume_reach_amendment),
           "tiers": {t: v["tier"] for t, v in main.items()}, "tiers_if_reach_amended": {t: v["tier"] for t, v in alt.items()},
           "happy_path_total": {t: v["happy"] for t, v in main.items()},
           "happy_path_total_if_reach_amended": {t: v["happy"] for t, v in alt.items()},
           "variant_totals": {t: v["variants"] for t, v in main.items()},
           "per_file": {t: v["per_file"] for t, v in main.items()},
           "per_file_if_reach_amended": {t: v["per_file"] for t, v in alt.items()},
           "descriptions_by_tier": {t: v["descriptions"] for t, v in main.items()},
           "descriptions_by_tier_if_reach_amended": {t: v["descriptions"] for t, v in alt.items()},
           "descriptions_sum": main["guaranteed"]["descriptions"], "descriptions_bound": _capped_descriptions(state("guaranteed"), plain),
           "effective": {"tier": eff["tier"], "variants": eff["variants"], "per_file": eff["per_file"], "descriptions": eff["descriptions"],
                         "happy_path_total": eff["happy"], "infeasible_rows_included": [i for i in tagged_on if i in in_eff]},
           "effective_conditions": {**dict(sorted(flags.items())), "reach_amendment": reach_amendment,
                                    "infeasible_rows_excluded": [i for i in tagged_on if i not in in_eff]}, "notes": state("conditional_if_reach_amended")["notes"],
           "unanchored": unanchored, "bias": grd.bias_summary(unanchored), "rows_applied": len(rows) - len(unanchored)}
    if not with_states:
        return out
    return out, {name: state(name) for name in sets}


def _flatten(tier: dict, per_file: dict, desc: int) -> Dict[str, int]:
    out = {"BUILD_fast_path": tier["BUILD"]["fast_path"], "BUILD_standard": tier["BUILD"]["standard"] if "standard" in tier["BUILD"] else 0,
           "BUILD_max": tier["BUILD"]["max"], "PLAN": tier["PLAN"], "DEBUG": tier["DEBUG"], "REVIEW": tier["REVIEW"],
           "kernel_bytes": per_file["kernel_bytes"], "protocol_bytes": per_file["protocol_bytes"],
           "reference_max_bytes": per_file["reference_max_bytes"], "descriptions": desc}
    return out


def check_monotonic(out: dict) -> List[str]:
    """E74: guaranteed >= conditional >= l3_upper_bound >= with_contingencies for every headline value and every gated file."""
    flat = {t: _flatten(out["tiers"][t], out["per_file"][t], out["descriptions_by_tier"][t]) for t in TIERS}
    bad = []
    for a, b in zip(TIERS, TIERS[1:]):
        bad += [f"{k}: {a}={flat[a][k]} < {b}={flat[b][k]}" for k in flat[a] if flat[a][k] < flat[b][k]]
        pa, pb = out["per_file"][a], out["per_file"][b]
        bad += [f"{k}: {a}={pa.get(k, 0)} < {b}={pb[k]}" for k in sorted(pb) if is_gated(k) and pa.get(k, 0) < pb[k]]
    return bad
