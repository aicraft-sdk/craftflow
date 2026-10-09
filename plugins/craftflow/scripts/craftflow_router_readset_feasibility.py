#!/usr/bin/env python3
"""Reach feasibility of a projected router tree (plan P2 REM-FIX 2, doubt-verify cycle 1 findings F1 and F3).

The cut-plan projection counts bytes; it does not ask whether the P0 rule inventory would accept the projected tree. This
module does: each tier's projected files go through the P0 accounting (`craftflow_rule_inventory.diff_inventory`, called as a
library on the in-memory tree, with the projected read map) and the report states

  unreachable / unmapped_removed / readmap_widened / row_errors   the P0 `--diff` counters for that tree, against the repo's
                                                                  committed policy map (so unmapped_removed is what the
                                                                  relocation phases must still row-sign);
  reach_lost_occurrences, reach_lost_bytes                       baseline occurrences whose unit still exists but is read later
                                                                  (kernel/start = 2 > happy-path stage = 1 > conditional stage = 0);
  reach_loss_by_move                                              the same, per source file -> destination file, with whether the
                                                                  destination is read on every run of the workflows that read the source;
  single_token_units                                              reach-lost units with fewer than two words: no `@reach`
                                                                  Applicability quote (>= 2 whole words) can ever cover them.

It computes and discloses; it changes neither the P0 reach model nor the policy map (those are P2C decisions: REACH-A keeps
per-unit signing, REACH-B would let a section-move row cover a reach drop into a happy-path file). Python 3.9 compatible.
"""
from __future__ import annotations

import collections
import re
from pathlib import Path
from typing import Dict

import craftflow_router_corpus as cor
import craftflow_router_readset_core as core

FIX = "tests/fixtures/"
COUNTERS = ("unreachable", "unmapped_removed", "readmap_widened", "row_errors", "present", "policy_mapped", "reworded", "rationale")
TIERS = ("guaranteed", "conditional", "with_contingencies", "conditional_if_reach_amended", "with_contingencies_if_reach_amended",
         "effective", "effective_without_infeasible_rows")
NOTE = ("l3_upper_bound is the conditional tree with whole non-normative units subtracted from the byte count only, so it has the "
        "conditional row. Counters come from the P0 diff run on the projected tree with the committed policy map: unmapped_removed "
        "is the removal the relocation phases still have to row-sign, unreachable the removals already rowed but read later. "
        "reach_lost_* count baseline occurrences whose unit still exists in the projected tree but is read later than before, "
        "whether or not a policy row exists yet.")


def load_context(root) -> dict:
    """Pinned baseline inventory, baseline read map, committed policy map and rationale, allowlist (fail closed on any missing file)."""
    import craftflow_rule_inventory as inv
    root = Path(root)
    docs = root.parents[1] / "docs"
    rmb = root / FIX / "router-readmap.baseline.json"
    base = inv.verify_lock(root / FIX / "router-rule-inventory.baseline.json", rmb, root / FIX / "router-baseline.lock.json")
    rat = docs / "router-rationale.md"
    return {"inv": inv, "root": root, "base": base, "readmap_baseline": inv.load_readmap(rmb),
            "policy": (docs / "router-policy-map.md").read_text(encoding="utf-8"),
            "rationale": rat.read_text(encoding="utf-8") if rat.is_file() else "",
            "allow": inv._allowlist(None)}


def runtime_corpus(state: dict) -> Dict[str, str]:
    """The files the inventory reads: runtime router files of the projected tree, minus the files the map marks `maintainer`."""
    maintainer = {e["file"] for e in state["readmap"]["files"] if e.get("class") == "maintainer"}
    return {f: t for f, t in sorted(state["files"].items()) if cor._is_runtime(f) and f not in maintainer}


def _happy_dst(readmap: dict, file: str) -> bool:
    """True when every read-map row of `file` is kernel/start or a happy-path stage row, i.e. the file is read on every run."""
    rows = [e for e in readmap["files"] if e["file"] == file and e["class"] != "maintainer"]
    return bool(rows) and all(e["class"] in ("kernel", "start") or e.get("happy_path") for e in rows)


def _words(text: str) -> int:
    return len(re.findall(r"\w+", re.sub(r"[`*_]", " ", text)))


def reach_report(state: dict, ctx: dict) -> dict:
    """The P0 counters plus the reach-loss analysis for one projected tree."""
    inv = ctx["inv"]
    corpus, rm = runtime_corpus(state), state["readmap"]
    res = inv.diff_inventory(ctx["base"], corpus, rm, ctx["policy"], ctx["rationale"], ctx["root"], allowlist=ctx["allow"],
                             baseline_readmap=ctx["readmap_baseline"])
    out = {k: (len(res[k]) if isinstance(res[k], (list, tuple, dict, set)) else res[k]) for k in COUNTERS}
    supply: Dict[str, list] = collections.defaultdict(list)
    for e in inv.build_inventory(corpus, rm, ctx["allow"])["entries"]:
        supply[e["id"]].extend((frozenset(map(tuple, o["applicability"])), o["file"]) for o in e["occurrences"])
    units, size = collections.Counter(), collections.Counter()
    single = 0
    for e in ctx["base"]["entries"]:
        sup = supply.get(e["id"])
        for o in e["occurrences"]:
            if not sup or any(inv.covers(c, frozenset(map(tuple, o["applicability"]))) for c, _ in sup):
                continue  # removed outright (counted by unmapped_removed / policy rows) or still read as early as before
            key = (core.short_path(o["file"]), core.short_path(sorted({f for _, f in sup})[0]))
            units[key] += 1
            size[key] += len(inv._text(e).encode("utf-8"))
            single += _words(inv._text(e)) < 2
    dst_of = {core.short_path(e["file"]): e["file"] for e in rm["files"]}
    by_move = [{"from": a, "to": b, "units": n, "bytes": size[(a, b)], "happy_path_dst": _happy_dst(rm, dst_of.get(b, b))}
               for (a, b), n in sorted(units.items(), key=lambda kv: (-kv[1], kv[0]))]
    out.update({"reach_lost_occurrences": sum(units.values()), "reach_lost_bytes": sum(size.values()), "single_token_units": single,
                "reach_loss_by_move": by_move})
    return out


def reach_feasibility(states: Dict[str, dict], ctx: dict) -> dict:
    """The `reach_feasibility` block of router-projection.json, from project(..., with_states=True)'s trees."""
    return {"note": NOTE, "tiers": {t: reach_report(states[t], ctx) for t in TIERS}}
