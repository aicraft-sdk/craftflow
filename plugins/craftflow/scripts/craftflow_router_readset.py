#!/usr/bin/env python3
"""Router read-set measurer and cut-plan projection (plan P2, BC-11, DD-6, PP-10).

  --workflow {BUILD,PLAN,DEBUG,REVIEW,ALL} [--variant V] [--ref SHA --readmap PATH] [--json]
        bytes the router reads at workflow start (kernel + start files + config/policy.json) and on the happy path;
        with --ref the bytes come from that commit and --readmap is required (pass the frozen baseline map, so later
        renames cannot move it; the live map is never paired with baseline bytes by default)
  --descriptions [--ref SHA]       skill description sum (effective YAML text; js_parser_sum is gen-craftflow-graph.mjs's)
  --print-table [--readmap PATH]   the kernel's marker-wrapped Reference Read Map section
  --project CUTPLAN [--phases P2,P3] [--conditions k=true|false,...] [--assume-reach-amendment]   BC-11 projection over the baseline
        bytes; `effective` uses --conditions, else the `conditions` recorded in router-budget.json (stated in
        `effective_conditions[_source]`). Rows tagged `feasibility` (they need a reach-model amendment) are in `tiers_if_reach_amended`
        only, unless --assume-reach-amendment puts them into `tiers`; `effective` leaves them out too unless router-budget.json records
        `reach_amendment: "D17-B"` (or --assume-reach-amendment is given). A full projection (no --phases) adds `reach_feasibility`: the P0
        accounting run on every projected tree (craftflow_router_readset_feasibility.py); every projection carries `happy_path_total`.

Prints exactly one JSON line and exits 0; exits 2 on a malformed map or budget, a missing or unreadable file, an unknown phase,
condition or flag value, or an ambiguous anchor (fail closed). Bytes are counted raw: files are read as UTF-8 bytes and a CR byte
is an error (nothing is normalised). Description lengths follow the effective YAML text.
`compute(workflow)` is importable (P4's mint telemetry). Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

import craftflow_router_corpus as cor
import craftflow_router_readset_burden as bur
import craftflow_router_readset_core as core
import craftflow_router_readset_guard as grd
import craftflow_router_readset_project as prj
from craftflow_router_readset_core import ReadsetError

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
FIX = "tests/fixtures/"
POLICY = "config/policy.json"
WF_CHOICES = ("BUILD", "PLAN", "DEBUG", "REVIEW", "ALL")


def load_map(path) -> dict:
    """Read-map JSON, validated like the inventory's loader (unknown keys, classes, workflows and variants are errors)."""
    try:
        data = cor.load_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("files"), list) or not data["files"]:
            raise ReadsetError(f"read map must be an object with a non-empty 'files' list: {path}")
        known = {"file", "class", "workflows", "variant", "trigger", "happy_path"}
        for e in data["files"]:
            if not isinstance(e, dict) or set(e) - known:
                raise ReadsetError(f"malformed read map entry: {e!r}")
            core.entry_pairs(data, e)
    except (OSError, ValueError) as exc:
        raise ReadsetError(f"cannot read read map {path}: {exc}") from exc
    return data


def check_map_files(readmap: dict, available) -> None:
    missing = sorted({e["file"] for e in readmap["files"] if e["class"] != "maintainer"} - set(available))
    if missing:
        raise ReadsetError(f"read map names files that are not present: {missing}")


def measure(plugin_root, ref: Optional[str], readmap: dict) -> Dict[str, int]:
    """Bytes of every non-maintainer file the map names, from the working tree or from `ref` (via git show)."""
    sizes = {}
    for e in readmap["files"]:
        if e["class"] == "maintainer":
            continue
        sizes[e["file"]] = len(grd.read_exact(plugin_root, e["file"], ref).encode("utf-8"))
    return sizes


def policy_bytes(plugin_root, ref: Optional[str]) -> Dict[str, int]:
    """config/policy.json counts toward every start set. Only a file that is not there (before P4) counts 0; one that is there
    but cannot be read or decoded is an error, never a silent 0."""
    absent = not grd.ls_tree(plugin_root, ref, POLICY) if ref else not os.path.lexists(Path(plugin_root) / POLICY)
    return {} if absent else {POLICY: len(grd.read_exact(plugin_root, POLICY, ref).encode("utf-8"))}


def _budget(plugin_root, workflow: str, path) -> Optional[int]:
    p = Path(path) if path else Path(plugin_root) / FIX / "router-budget.json"
    if not p.is_file():
        return None
    try:
        v = cor.load_json(p)["ceilings"]["readset"][workflow]
    except (OSError, KeyError, TypeError, ValueError) as exc:  # a budget that exists but cannot be read is not "no budget"
        raise ReadsetError(f"cannot read the {workflow} read-set ceiling from {p}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(v, int) or isinstance(v, bool):
        raise ReadsetError(f"the {workflow} read-set ceiling in {p} must be an integer, got {v!r}")
    return v


def compute(workflow: str, plugin_root=None, ref: Optional[str] = None, readmap=None, variant: Optional[str] = None,
            budget_path=None) -> dict:
    """Read-set record for one workflow (or {"workflow": "ALL", "workflows": {...}}). Raises ReadsetError."""
    root = Path(plugin_root or PLUGIN_ROOT)
    if ref and readmap is None:
        raise ReadsetError("--ref measures the bytes of another commit and needs --readmap (the frozen baseline map for the baseline), "
                           "never the live map by default")
    rm = readmap if isinstance(readmap, dict) else load_map(readmap or root / "config" / "router-readmap.json")
    sizes = measure(root, ref, rm)
    extra = policy_bytes(root, ref)
    names = list(core.variants_of(rm)) if workflow == "ALL" else [workflow]
    out = {w: core.compute_from_sizes(rm, sizes, w, variant if workflow != "ALL" else None, extra,
                                      None if ref else _budget(root, w, budget_path)) for w in names}
    return out[workflow] if workflow != "ALL" else {"workflow": "ALL", "workflows": out}


def js_description(text: str) -> str:
    """The description gen-craftflow-graph.mjs's parseFrontmatter returns (`>-` stays the literal `>-`)."""
    d = cor.parse_frontmatter(text).get("description", "")
    return d if isinstance(d, str) else ""


def descriptions(plugin_root=None, ref: Optional[str] = None) -> dict:
    """`sum` counts each skill's effective description (what the harness loads). `js_parser_sum` is what the graph
    generator's parser sees; the two differ for skills written as `description: >-`, which that parser reads as 2 chars."""
    root = Path(plugin_root or PLUGIN_ROOT)
    if ref:
        rels = [n for n in grd.ls_tree(root, ref, "skills") if n.count("/") == 2 and n.endswith("/SKILL.md")]
    else:
        rels = sorted(p.relative_to(root).as_posix() for p in (root / "skills").glob("*/SKILL.md"))
    per, js = {}, {}
    for rel in rels:
        text = grd.read_exact(root, rel, ref)
        name = rel.split("/")[1]
        per[name], js[name] = core.effective_description(text), js_description(text)
    return {"sum": sum(len(v) for v in per.values()), "per_skill": {k: len(v) for k, v in per.items()},
            "js_parser_sum": sum(len(v) for v in js.values()),
            "js_parser_blind": sorted(k for k in per if len(js[k]) != len(per[k])), "parser": "effective-yaml"}


def recorded_conditions(root: Path) -> tuple:
    """(flags, source, reach_amendment): the `conditions` and `reach_amendment` of router-budget.json when no --conditions is given,
    so the `effective` block of router-projection.json describes the state David recorded (difference_17 true, the tagged rows in
    only with reach_amendment "D17-B"), not an all-false world."""
    path = root / FIX / "router-budget.json"
    if not path.is_file():
        return {}, "none", None
    try:
        budget = cor.load_json(path)
        flags, amendment = budget["conditions"], budget.get("reach_amendment")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ReadsetError(f"cannot read the recorded conditions from {path}: {type(exc).__name__}: {exc}") from exc
    return grd.check_flags(flags), "router-budget.json", grd.check_reach_amendment(amendment)


def run_projection(root: Path, args) -> dict:
    lock = cor.load_json(root / FIX / "router-baseline.lock.json")
    ref = args.ref or lock["baseline_sha"]
    state0 = prj.load_baseline_state(root, ref, args.readmap or root / FIX / "router-readmap.baseline.json")
    plan_bytes = Path(args.project).read_bytes()
    plan = json.loads(plan_bytes.decode("utf-8"))
    if not isinstance(plan, dict) or not isinstance(plan.get("rows"), list):
        raise ReadsetError("cut plan must be an object with a 'rows' list")
    phases = None if args.phases is None else [p for p in args.phases.split(",") if p]
    flags, source, amendment = grd.parse_conditions(args.conditions), "cli", None
    if not args.conditions:
        flags, source, amendment = recorded_conditions(root)
    amendment = "D17-B" if args.assume_reach_amendment else amendment  # the flag also stands in for the budget's reach_amendment
    from craftflow_rule_inventory_units import load_allowlist
    allow = root / FIX / "router-normative-allowlist.json"
    allowed = frozenset(load_allowlist(allow)) if allow.is_file() else frozenset()
    res = prj.project(plan, state0, phases, flags, allowed, args.assume_reach_amendment, with_states=phases is None,
                      reach_amendment=amendment)
    out, states = res if phases is None else (res, None)
    if states is not None:  # a full-plan projection also says whether the P0 accounting would accept each projected tree
        import craftflow_router_readset_feasibility as feas
        out["reach_feasibility"] = feas.reach_feasibility(states, feas.load_context(root))
        hsc = "skills/craftflow-router/references/harness-self-checks.md"
        out["bias"]["notes"] = bur.bias_notes(out["reach_feasibility"]["tiers"]["guaranteed"]["reach_loss_split"], len(state0["files"][hsc].encode("utf-8")))
    out["effective_conditions_source"] = source
    out["cutplan_sha256"] = hashlib.sha256(plan_bytes).hexdigest()
    out["baseline_ref"] = ref
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Router read-set measurer and cut-plan projection")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--workflow", choices=WF_CHOICES)
    mode.add_argument("--descriptions", action="store_true")
    mode.add_argument("--print-table", action="store_true")
    mode.add_argument("--project", metavar="CUTPLAN")
    ap.add_argument("--variant")
    ap.add_argument("--ref")
    ap.add_argument("--readmap")
    ap.add_argument("--budget")
    ap.add_argument("--phases", default=None, help="comma-separated phase ids; empty selects none")
    ap.add_argument("--conditions", default="", help="difference_17=true,live_verified_enterplanmode=false (true|false only)")
    ap.add_argument("--plugin-root")
    ap.add_argument("--assume-reach-amendment", action="store_true",
                    help="--project: put the rows tagged `feasibility` (they need a reach-model amendment) into the default tiers")
    ap.add_argument("--json", action="store_true", help="accepted for clarity: output is always one JSON line")
    args = ap.parse_args(argv)
    root = Path(args.plugin_root or PLUGIN_ROOT)
    try:
        if args.workflow:
            res = compute(args.workflow, root, args.ref, args.readmap, args.variant, args.budget)
        elif args.descriptions:
            res = descriptions(root, args.ref)
        elif args.print_table:
            sys.stdout.write(core.table_block(load_map(args.readmap or root / "config" / "router-readmap.json")))
            return 0
        else:
            res = run_projection(root, args)
    except (ReadsetError, RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(res, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
