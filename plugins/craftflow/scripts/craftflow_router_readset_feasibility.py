#!/usr/bin/env python3
"""Reach feasibility of a projected router tree (plan P2 REM-FIX 2 and 3, doubt-verify findings F1, F3 and G1-G6).

The cut-plan projection counts bytes; it does not ask whether the P0 rule inventory would accept the projected tree. This
module does: each tier's projected files go through the P0 accounting (`craftflow_rule_inventory.diff_inventory`, called as a
library on the in-memory tree, with the projected read map) and the report states

  unreachable / unmapped_removed / readmap_widened / row_errors   the P0 `--diff` counters for that tree, against the repo's
                                                                  committed policy map (so unmapped_removed is what the
                                                                  relocation phases must still row-sign);
  reach_lost_occurrences, reach_lost_bytes                       baseline occurrences whose unit still exists but is read later
                                                                  (kernel/start = 2 > happy-path stage = 1 > conditional stage = 0);
  reach_loss_by_move                                              the same, per source file -> destination file;
  reach_loss_split                                                every reach-lost unit classified by what each of its source
                                                                  (workflow, variant) pairs still reads, in units and bytes. The best
                                                                  single supplied copy decides: a covered one wherever it sits, then the
                                                                  copy that drops the fewest pairs (REM-FIX 4, K3):
                                                                  pure_2_to_1_covered      every pair now reads it on the happy path
                                                                                           (a REACH-B candidate),
                                                                  drops_to_0               some pair falls to a conditional stage file,
                                                                  drops_workflow_or_variant some workflow/variant does not read the unit at all
                                                                                           (a destination that is happy-path for its own
                                                                                           workflows is not coverage for the others);
  variant_split                                                   the part of drops_workflow_or_variant where the copies together still
                                                                  give every pair its start reach (the unit was split over files);
  signing_burden                                                  the rows the relocation phases would sign (craftflow_router_readset_burden):
                                                                  disjoint per-occurrence buckets, rows as ids and files next to occurrences,
                                                                  REACH-A, REACH-B and the D17-B waiver, with the formula;
  single_token_units                                              reach-lost units with fewer than two words: no `@reach`
                                                                  Applicability quote (>= 2 whole words) can ever cover them;
  reach_a_kernel_penalty_bytes                                    bytes of the kernel sections holding such units: under REACH-A they can
                                                                  never be signed, so those sections stay in the kernel.

It computes and discloses; it changes neither the P0 reach model nor the policy map (those are P2C decisions: REACH-A keeps
per-unit signing, REACH-B would let a section-move row cover a reach drop into a happy-path file). Python 3.9 compatible.
"""
from __future__ import annotations

import collections
import re
from pathlib import Path
from typing import Dict, List

import craftflow_router_corpus as cor
import craftflow_router_readset_core as core
from craftflow_router_readset_burden import burden, move_summary
from craftflow_rule_inventory_readmap import RANK

FIX = "tests/fixtures/"
KERNEL = "skills/craftflow-router/SKILL.md"
COUNTERS = ("unreachable", "unmapped_removed", "readmap_widened", "row_errors", "present", "policy_mapped", "reworded", "rationale")
TIERS = ("guaranteed", "conditional", "with_contingencies", "conditional_if_reach_amended", "with_contingencies_if_reach_amended",
         "effective", "effective_without_infeasible_rows")
CLASSES = ("pure_2_to_1_covered", "drops_to_0", "drops_workflow_or_variant")
NOTE = ("l3_upper_bound is the conditional tree with whole non-normative units subtracted from the byte count only, so it has the "
        "conditional row. Counters come from the P0 diff run on the projected tree with the committed policy map: unmapped_removed "
        "is every baseline occurrence no longer present at its place (the removal the relocation phases still have to row-sign); "
        "unreachable is the part of it that stays in place but is read later or by fewer workflows (a read-map narrowing). "
        "reach_lost_* count baseline occurrences whose unit still exists in the projected tree but is read later than before, "
        "whether or not a policy row exists yet; reach_loss_split classifies each by what every source (workflow, variant) pair still "
        "reads (the best single copy: a covered one first, then the one that drops the fewest pairs); variant_split is the part of the "
        "workflow/variant drops whose copies together still give every pair its start reach. signing_burden counts rows (unit ids, files) "
        "next to occurrences, over disjoint buckets; the moved-without-loss occurrences share the section-move rows (41 move ops are 41-49 "
        "signable sections, 53 counting the unheaded bake-off span's five; p10-pav-verifier-telemetry has no heading at either end and "
        "needs a destination heading). REACH-B (a section-move row covers a pure 2 -> 1 drop) would also have to cover the rewrite rows inside a moved section "
        "(a reworded parent item changes its child's parent_ctx). Under REACH-A a single-token unit can never be signed (no 2-word "
        "@reach quote exists), so the kernel section holding it stays in the kernel: reach_a_kernel_penalty_bytes. This block computes "
        "and discloses; it implements neither option.")


def load_context(root) -> dict:
    """Pinned baseline inventory, baseline read map, committed policy map and rationale, allowlist (fail closed on any missing file)."""
    import craftflow_rule_inventory as inv
    root = Path(root)
    docs = root.parents[1] / "docs"
    rmb = root / FIX / "router-readmap.baseline.json"
    lock = root / FIX / "router-baseline.lock.json"
    base = inv.verify_lock(root / FIX / "router-rule-inventory.baseline.json", rmb, lock)
    rat = docs / "router-rationale.md"
    return {"inv": inv, "root": root, "base": base, "readmap_baseline": inv.load_readmap(rmb),
            "baseline_sha": cor.load_json(lock)["baseline_sha"],
            "policy": (docs / "router-policy-map.md").read_text(encoding="utf-8"),
            "rationale": rat.read_text(encoding="utf-8") if rat.is_file() else "",
            "allow": inv._allowlist(None)}


def runtime_corpus(state: dict) -> Dict[str, str]:
    """The files the inventory reads: runtime router files of the projected tree, minus the files the map marks `maintainer`."""
    maintainer = {e["file"] for e in state["readmap"]["files"] if e.get("class") == "maintainer"}
    return {f: t for f, t in sorted(state["files"].items()) if cor._is_runtime(f) and f not in maintainer}


def _words(text: str) -> int:
    return len(re.findall(r"\w+", re.sub(r"[`*_]", " ", text)))


def _have(copies) -> Dict[tuple, int]:
    """The best reach each (workflow, variant) pair gets from a set of supplied copies."""
    have: Dict[tuple, int] = {}
    for cov in copies:
        for w, v, r in cov:
            have[(w, v)] = max(have.get((w, v), -1), RANK[r])
    return have


def classify(req: frozenset, supplies: list) -> str:
    """What the best single supplied copy gives each (workflow, variant) pair that read the unit at `req` reach. Only a copy whose every
    shortfall is 2 -> 1 is covered (a pair that read it at start now reads it on every run); any pair with no row at all in the copy
    (the destination is not read by that workflow/variant) makes it drops_workflow_or_variant, else a fall to a conditional file is
    drops_to_0. The best copy is a covered one wherever it sits in the list (REM-FIX 4, K3: the first copy used to win ties), then the
    copy with the fewest dropped pairs, then the first."""
    best = None
    for cov, *_ in supplies:
        have = _have([cov])
        short = [(RANK[r], have.get((w, v), -1)) for w, v, r in req if have.get((w, v), -1) < RANK[r]]
        key = (0 if set(short) <= {(2, 1)} else 1, len(short))
        if best is None or key < best[0]:
            best = (key, set(short))
    if best[1] <= {(2, 1)}:
        return CLASSES[0]
    return CLASSES[2] if any(b == -1 for _, b in best[1]) else CLASSES[1]


def is_variant_split(req: frozenset, supplies: list) -> bool:
    """True when no pair loses anything if the copies are read together, yet no single copy supplies the whole unit (P0 needs one copy):
    the unit was split over files whose workflows or variants together still read it at its start reach."""
    have = _have([s[0] for s in supplies])
    return all(have.get((w, v), -1) >= RANK[r] for w, v, r in req)


def section_bytes(text: str, heading: str) -> int:
    """Bytes of the markdown section titled `heading` (normalised), up to the next heading of the same or a higher level."""
    lines = text.splitlines(keepends=True)
    head = [(i, len(m.group(1)), m.group(2).strip().lower()) for i, ln in enumerate(lines) if (m := re.match(r"(#+)\s+(.*)", ln))]
    for k, (i, level, title) in enumerate(head):
        if title == heading or title.endswith(heading):
            end = next((j for j, lv, _ in head[k + 1:] if lv <= level), len(lines))
            return len("".join(lines[i:end]).encode("utf-8"))
    return 0


def kernel_penalty(single: List[tuple], ctx: dict) -> int:
    """Bytes of the distinct baseline kernel sections that hold a reach-lost single-token unit (REACH-A cannot sign them)."""
    if not single:
        return 0
    from craftflow_router_readset_guard import read_exact
    text = read_exact(ctx["root"], KERNEL, ctx["baseline_sha"])
    return sum(section_bytes(text, h) for h in {h for f, h in single if f == KERNEL})


def reach_report(state: dict, ctx: dict) -> dict:
    """The P0 counters plus the reach-loss analysis for one projected tree."""
    inv = ctx["inv"]
    corpus, rm = runtime_corpus(state), state["readmap"]
    res = inv.diff_inventory(ctx["base"], corpus, rm, ctx["policy"], ctx["rationale"], ctx["root"], allowlist=ctx["allow"],
                             baseline_readmap=ctx["readmap_baseline"])
    out = {k: (len(res[k]) if isinstance(res[k], (list, tuple, dict, set)) else res[k]) for k in COUNTERS}
    supply: Dict[str, list] = collections.defaultdict(list)
    for e in inv.build_inventory(corpus, rm, ctx["allow"])["entries"]:
        supply[e["id"]].extend((frozenset(map(tuple, o["applicability"])), o["file"], tuple(o["heading_path"])) for o in e["occurrences"])
    units, size = collections.Counter(), collections.Counter()
    by_class = {c: collections.Counter() for c in CLASSES}
    split = {c: {"units": 0, "bytes": 0} for c in CLASSES}
    single_where: List[tuple] = []
    single = 0
    records: List[tuple] = []  # (id, bucket, class) per baseline occurrence that is gone or read later (burden.py)
    variant = {"units": 0, "bytes": 0}
    for e in ctx["base"]["entries"]:
        sup = supply.get(e["id"])
        nbytes = len(inv._text(e).encode("utf-8"))
        for o in e["occurrences"]:
            if not sup:
                records.append((e["id"], "vanishing", None))  # the id is gone from the tree: a rewrite or dedupe row, not a reach question
                continue
            req = frozenset(map(tuple, o["applicability"]))
            if any(inv.covers(s[0], req) for s in sup):
                continue  # still read as early as before (a moved section needs only its section-move row)
            key = (core.short_path(o["file"]), core.short_path(sorted({s[1] for s in sup})[0]))
            kind = classify(req, sup)
            # the unit still sits at its baseline file and heading path (P0 counts these in `unreachable`) or it moved
            place = "in_place" if any(s[1] == o["file"] and s[2] == tuple(o["heading_path"]) for s in sup) else "moved"
            records.append((e["id"], place, {CLASSES[0]: "cov", CLASSES[1]: "d0", CLASSES[2]: "wf"}[kind]))
            units[key] += 1
            size[key] += nbytes
            by_class[kind][key] += 1
            split[kind]["units"] += 1
            split[kind]["bytes"] += nbytes
            if is_variant_split(req, sup):
                variant["units"] += 1
                variant["bytes"] += nbytes
            if _words(inv._text(e)) < 2:
                single += 1
                single_where.append((o["file"], o["heading_ctx"]))
    by_move = [{"from": a, "to": b, "units": n, "bytes": size[(a, b)], **{c: by_class[c][(a, b)] for c in CLASSES}}
               for (a, b), n in sorted(units.items(), key=lambda kv: (-kv[1], kv[0]))]
    out.update({"reach_lost_occurrences": sum(units.values()), "reach_lost_bytes": sum(size.values()), "reach_loss_split": split, "variant_split": variant,
                "single_token_units": single, "reach_a_kernel_penalty_bytes": kernel_penalty(single_where, ctx),
                "reach_loss_by_move": by_move})
    van = sum(1 for r in records if r[1] == "vanishing")
    moved_lost = sum(1 for r in records if r[1] == "moved")
    # disjoint buckets (REM-FIX 4, K1): `unmapped_removed` holds the in-place narrowings too, so they must not be subtracted again
    mwl = max(0, out["unmapped_removed"] - out["unreachable"] - moved_lost - van)
    wfiles = {m.group(1) for e in res["row_errors"] for m in [re.match(r"read map widens (\S+) to ", e)] if m}
    out["signing_burden"] = burden(records, mwl, out["readmap_widened"], len(wfiles), move_summary(state.get("move_log", [])))
    return out


def reach_feasibility(states: Dict[str, dict], ctx: dict) -> dict:
    """The `reach_feasibility` block of router-projection.json, from project(..., with_states=True)'s trees."""
    return {"note": NOTE, "tiers": {t: reach_report(states[t], ctx) for t in TIERS}}
