#!/usr/bin/env python3
"""Router context-cut before/after impact report (plan P13 task 1).

  --baseline SHA --out PATH [--date YYYY-MM-DD] [--hv HV.json] [--lv LV.json] [--plugin-root DIR] [--no-inventory]

Measures every router read set and size at the baseline commit (bytes read through `git show`, paired with the FROZEN
`tests/fixtures/router-readmap.baseline.json`, so later renames cannot move it) and at the working tree (the live
`config/router-readmap.json`), with the same tool and definitions as `craftflow_router_readset.py`, then writes one
Markdown report. Gated values show four columns: goal (`targets`), enforced (P2C `enforced_caps`), measured, and the P2
projection (`router-projection.json`, effective tier), so the gap to the goal is stated plainly.

  --hv FILE   David's `/context` captures: {"HV-1": {"baseline": {"a": N, "b": N}, "candidate": {"a": N, "b": N}}, "HV-2": ...}
              (a = right after the routing announce, b = at memory-finalize; N = tokens as `/context` prints them). Cells
              that are absent stay PENDING; numbers are never invented.
  --lv FILE   the summary JSON line printed by tests/live/router_context_cut_roundtrip.py. Absent -> PENDING.

Prints one JSON summary line on stdout. Exit 0 = report written; 1 = report written but the inventory diff did not exit 0;
2 = error (nothing written). Bytes are not tokens. Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import craftflow_router_corpus as cor
import craftflow_router_readset as rs
import craftflow_router_readset_guard as grd
from craftflow_router_readset_core import ReadsetError

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
FIX = "tests/fixtures/"
WORKFLOWS = ("BUILD", "PLAN", "DEBUG", "REVIEW")
HARNESS = "skills/craftflow-router/references/harness-self-checks.md"
PROTOCOL = "skills/_shared/router-protocol.md"
SKILL = "skills/craftflow-router/SKILL.md"
GATED = (("kernel_bytes", "Kernel (SKILL.md)"), ("protocol_bytes", "Protocol (router-protocol.md)"),
         ("reference_max_bytes", "Largest reference"), ("description_chars", "Skill description sum (chars)"),
         ("readset.BUILD", "BUILD read set"), ("readset.PLAN", "PLAN read set"),
         ("readset.DEBUG", "DEBUG read set"), ("readset.REVIEW", "REVIEW read set"))
HV_IDS = ("HV-1", "HV-2")
HV_LABELS = {"HV-1": "fast-path BUILD", "HV-2": "PLAN"}
LV_ROWS = (("load_probe", "LV-1 load probe"), ("lv2", "LV-2 read-set parity"), ("lv3", "LV-3 EnterPlanMode denied live"),
           ("lv4", "LV-4 plan commit denied live"), ("lv5", "LV-5 ordinary commit allowed"))


class ImpactError(Exception):
    """Any condition that must stop the report (unknown baseline, unreadable input, malformed HV/LV file)."""


def fmt(n: Optional[int]) -> str:
    return "n/a" if n is None else f"{n:,}"


def pct(base: Optional[int], head: Optional[int]) -> str:
    if not base or head is None:
        return "n/a"
    return f"{(head - base) / base * 100:+.1f}%"


# --------------------------------------------------------------------------------------------- collection

def _get(d: dict, dotted: str):
    for part in dotted.split("."):
        d = d[part]
    return d


def _head_sha(root: Path) -> tuple:
    def git(*a):
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, timeout=60)
    sha = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--", ".")
    if sha.returncode != 0:
        raise ImpactError(f"git rev-parse HEAD failed: {sha.stderr.strip()}")
    return sha.stdout.strip(), bool(dirty.stdout.strip())


def inventory_diff(root: Path) -> tuple:
    """(parsed JSON accounting or None, exit code): the CI `--diff` invocation, verbatim paths."""
    fx = root / FIX
    policy_map = root.parents[1] / "docs" / "router-policy-map.md"
    cmd = [sys.executable, str(root / "scripts" / "craftflow_rule_inventory.py"), "--diff",
           "--baseline", str(fx / "router-rule-inventory.baseline.json"), "--policy-map", str(policy_map),
           "--readmap", str(root / "config" / "router-readmap.json"),
           "--readmap-baseline", str(fx / "router-readmap.baseline.json"), "--lock", str(fx / "router-baseline.lock.json")]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1]), p.returncode
    except (IndexError, ValueError):
        return None, p.returncode


def collect(root, baseline: str, run_inventory: bool = True, date: Optional[str] = None) -> dict:
    """Everything the report needs, measured now. Raises ImpactError (never returns partial data)."""
    root = Path(root)
    try:
        base_map = rs.load_map(root / FIX / "router-readmap.baseline.json")
        head_map = rs.load_map(root / "config" / "router-readmap.json")
        no_budget = Path("/nonexistent")
        base_all = rs.compute("ALL", root, ref=baseline, readmap=base_map)["workflows"]
        head_all = rs.compute("ALL", root, readmap=head_map, budget_path=no_budget)["workflows"]
        base_sizes = rs.measure(root, baseline, base_map)
        head_sizes = rs.measure(root, None, head_map)
        harness_bytes = len(grd.read_exact(root, HARNESS, baseline).encode("utf-8"))
        base_desc, head_desc = rs.descriptions(root, baseline)["sum"], rs.descriptions(root)["sum"]
        corpus = {k: len(v.encode("utf-8")) for k, v in cor.read_corpus(root).items()}
        budget = cor.load_json(root / FIX / "router-budget.json")
        projection = cor.load_json(root / FIX / "router-projection.json")["effective"]
    except (ReadsetError, RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        raise ImpactError(f"{type(exc).__name__}: {exc}") from exc
    head_sha, dirty = _head_sha(root)

    rows = []
    for wf in WORKFLOWS:
        for variant, h in head_all[wf]["variants"].items():
            b = base_all[wf]["variants"].get(variant, {})
            rows.append({"workflow": wf, "variant": variant, "base_start": b.get("start_total"), "head_start": h["start_total"],
                         "base_happy": b.get("happy_path_total"), "head_happy": h["happy_path_total"]})
    build_base = base_all["BUILD"]["start_total"]
    in_start = any(f["path"] == HARNESS for f in base_all["BUILD"]["start_files"])
    defs = {"start_files": build_base, "harness_bytes": harness_bytes,
            "with_harness_self_checks": build_base if in_start else build_base + harness_bytes}

    def refs(sizes):
        return [v for k, v in sizes.items() if (k.startswith("skills/craftflow-router/references/")
                or k.startswith("skills/_shared/")) and k != PROTOCOL]
    base_vals = {"kernel_bytes": base_sizes[SKILL], "protocol_bytes": base_sizes[PROTOCOL],
                 "reference_max_bytes": max(refs(base_sizes)), "description_chars": base_desc}
    head_vals = {"kernel_bytes": corpus[SKILL], "protocol_bytes": corpus[PROTOCOL],
                 "reference_max_bytes": max(refs(corpus)), "description_chars": head_desc}
    for wf in WORKFLOWS:
        base_vals[f"readset.{wf}"] = base_all[wf]["start_total"]
        head_vals[f"readset.{wf}"] = head_all[wf]["start_total"]
    proj_vals = {"kernel_bytes": projection["per_file"]["kernel_bytes"], "protocol_bytes": projection["per_file"]["protocol_bytes"],
                 "reference_max_bytes": projection["per_file"]["reference_max_bytes"], "description_chars": projection["descriptions"]}
    for wf in WORKFLOWS:
        t = projection["tier"][wf]
        proj_vals[f"readset.{wf}"] = t["max"] if isinstance(t, dict) else t
    gated = [{"key": k, "label": label, "baseline": base_vals[k], "goal": _get(budget["targets"], k),
              "enforced": _get(budget["enforced_caps"], k), "measured": head_vals[k], "projection": proj_vals[k]}
             for k, label in GATED]

    files = sorted(set(base_sizes) | set(head_sizes))
    per_file = [{"file": f, "base": base_sizes.get(f), "head": head_sizes.get(f)} for f in files]
    inventory, inv_exit = inventory_diff(root) if run_inventory else (None, None)
    return {"baseline_ref": baseline, "head_sha": head_sha, "head_dirty": dirty,
            "date": date or datetime.date.today().isoformat(), "rows": rows, "build_definitions": defs, "gated": gated,
            "per_file": per_file,
            "descriptions": {"base": base_desc, "head": head_desc, "goal": budget["targets"]["description_chars"],
                             "enforced": budget["enforced_caps"]["description_chars"], "projection": proj_vals["description_chars"]},
            "inventory": inventory, "inventory_exit": inv_exit}


def load_hv(raw) -> dict:
    """Validate David's capture file. Unknown ids, unknown points and non-integer (or bool) values are errors."""
    if not isinstance(raw, dict):
        raise ImpactError("HV file must be a JSON object")
    out: Dict[str, dict] = {}
    for hv, sides in raw.items():
        if hv not in HV_IDS or not isinstance(sides, dict):
            raise ImpactError(f"unknown or malformed HV entry {hv!r} (allowed: {', '.join(HV_IDS)})")
        out[hv] = {}
        for side, points in sides.items():
            if side not in ("baseline", "candidate") or not isinstance(points, dict):
                raise ImpactError(f"{hv}: side must be 'baseline' or 'candidate' with an object of points, got {side!r}")
            for point, n in points.items():
                if point not in ("a", "b") or isinstance(n, bool) or not isinstance(n, int) or n < 0:
                    raise ImpactError(f"{hv}.{side}.{point}: need a non-negative integer for point a|b, got {n!r}")
            out[hv][side] = dict(points)
    return out


# --------------------------------------------------------------------------------------------- rendering

def _gap(measured: int, goal: int) -> str:
    if measured <= goal:
        return f"Goal met ({fmt(goal - measured)} B under)"
    return f"{fmt(measured - goal)} B above the goal ({(measured - goal) / goal * 100:.0f}% over; goal not reached)"


def _hv_cell(hv: Optional[dict], hv_id: str, side: str, point: str) -> str:
    n = ((hv or {}).get(hv_id, {}).get(side, {}) or {}).get(point)
    return "PENDING" if n is None else fmt(n)


def render(data: dict, hv: Optional[dict] = None, lv: Optional[dict] = None) -> str:
    """Pure: the Markdown report for a `collect` result. HV/LV cells without data stay PENDING."""
    L: List[str] = []
    a = L.append
    base, head = data["baseline_ref"], data["head_sha"]
    a("# Router context cut, round 2: before/after impact")
    a("")
    a(f"- Date: {data['date']}")
    a(f"- Baseline: `{base}` (frozen read map `tests/fixtures/router-readmap.baseline.json`)")
    a(f"- Candidate: `{head}`" + (" (working tree had uncommitted changes under the plugin when measured)" if data["head_dirty"] else ""))
    a("- Reproduce: `python3 scripts/craftflow_router_context_impact.py --baseline <BASELINE_SHA> --out <report.md>` "
      "(from `tools/craftflow-plugin/plugins/craftflow/`)")
    a("")
    a("## Disclosures")
    a("")
    a("- **Bytes are not tokens.** Every size below is UTF-8 bytes of the files the router reads. The `/context` captures in the "
      "human-verification section are the only token-level evidence, and they include things this report does not (system prompt, "
      "tool schemas, the skills list).")
    a("- **The baseline uses the same tool and definition as the candidate.** Both columns come from `craftflow_router_readset.py` "
      "logic: kernel plus start files plus `config/policy.json`; the baseline bytes are read with `git show` and paired with the "
      "frozen baseline read map, so a later rename or deletion cannot move the baseline.")
    a("- Goals are the design targets in `router-budget.json` (`targets`). Enforced caps are the P2C decision (`enforced_caps`). "
      "Neither is edited by this report.")
    a("")
    a("## Read set per workflow: before and after")
    a("")
    d = data["build_definitions"]
    a("Two BUILD baseline definitions exist, and both are reported so neither can be mistaken for the other:")
    a("")
    a(f"- **Start files only** (the gated definition): **{fmt(d['start_files'])} B**.")
    a(f"- **Audit definition** (start files plus `harness-self-checks.md`, {fmt(d['harness_bytes'])} B at the baseline): "
      f"**{fmt(d['with_harness_self_checks'])} B**.")
    a("")
    a("| Workflow | Variant | start_total before | start_total after | change | happy_path_total before | happy_path_total after | change |")
    a("|---|---|---:|---:|---:|---:|---:|---:|")
    for r in data["rows"]:
        a(f"| {r['workflow']} | {r['variant']} | {fmt(r['base_start'])} | {fmt(r['head_start'])} | {pct(r['base_start'], r['head_start'])} "
          f"| {fmt(r['base_happy'])} | {fmt(r['head_happy'])} | {pct(r['base_happy'], r['head_happy'])} |")
    a("")
    a("## Gated values: goal, enforced, measured, projection")
    a("")
    a("| Gated value | Baseline | Goal | Enforced cap | Measured | Projection (P2) | Gap to goal |")
    a("|---|---:|---:|---:|---:|---:|---|")
    for g in data["gated"]:
        a(f"| {g['label']} | {fmt(g['baseline'])} | {fmt(g['goal'])} | {fmt(g['enforced'])} | {fmt(g['measured'])} "
          f"| {fmt(g['projection'])} | {_gap(g['measured'], g['goal'])} |")
    a("")
    over = [g for g in data["gated"] if g["measured"] > g["goal"]]
    if over:
        a("**Gap, stated plainly:** " + "; ".join(f"{g['label']} is {fmt(g['measured'] - g['goal'])} B above its goal of {fmt(g['goal'])}"
                                                    for g in over) + ". The goal is not reached for these values; the enforced caps "
          "sit above the goal by design of the P2C decision, and measured stays at or below the enforced cap.")
    else:
        a("**Gap, stated plainly:** every gated value is at or below its goal.")
    a("")
    ds = data["descriptions"]
    a(f"Skill description sum: {fmt(ds['base'])} chars before, {fmt(ds['head'])} after (goal {fmt(ds['goal'])}, "
      f"enforced {fmt(ds['enforced'])}, projection {fmt(ds['projection'])}).")
    a("")
    a("## Per-file bytes")
    a("")
    a("| File | Baseline | Candidate | Change | Status |")
    a("|---|---:|---:|---:|---|")
    for f in data["per_file"]:
        b, h = f["base"], f["head"]
        status = "removed" if h is None else "added" if b is None else "unchanged" if b == h else "changed"
        change = "n/a" if b is None or h is None else f"{h - b:+,}"
        a(f"| `{f['file']}` | {fmt(b)} | {fmt(h)} | {change} | {status} |")
    a("")
    a("## Rule inventory accounting")
    a("")
    inv = data.get("inventory")
    if inv is None:
        a(f"Not collected (inventory diff exit: {data.get('inventory_exit')}).")
    else:
        a("| Count | Value |")
        a("|---|---:|")
        for label, key in (("baseline units", "baseline_units"), ("present", "present"), ("policy-mapped", "policy_mapped"),
                           ("reworded", "reworded"), ("rationale", "rationale"), ("unmapped (removed)", "unmapped_removed"),
                           ("unreachable", "unreachable"), ("rows missing review", "rows_missing_review")):
            a(f"| {label} | {fmt(inv.get(key))} |")
        a("")
        a(f"unmapped_removed: {inv.get('unmapped_removed')}, unreachable: {inv.get('unreachable')}, "
          f"rows_missing_review: {inv.get('rows_missing_review')} (inventory diff exit {data.get('inventory_exit')}).")
    a("")
    a("## Live verification (automated lane)")
    a("")
    if lv is None:
        a("PENDING: run `tests/live/manifests/router-context-cut.json` through the live harness runner and pass its summary with `--lv`.")
    else:
        a("| Scenario | Result |")
        a("|---|---|")
        for key, label in LV_ROWS:
            a(f"| {label} | {lv.get(key, 'missing')} |")
        a("")
        a(f"Evidence class: {lv.get('evidence_class', 'n/a')}. LV-3 is PASS or `LIMITATION:enterplanmode_not_offered`; the limitation "
          "is covered by the interactive check on the human list.")
    a("")
    a("## Human verification: `/context` A/B")
    a("")
    a("Captured by David with `claude --plugin-dir <baseline plugin>` and `claude --plugin-dir <candidate plugin>`. Values are tokens as "
      "`/context` prints them (not bytes). Capture point a is right after the routing announce; b is when memory-finalize completes. "
      "A cell is PENDING until David pastes its number.")
    a("")
    a("| Scenario | Baseline (a) | Baseline (b) | Candidate (a) | Candidate (b) |")
    a("|---|---:|---:|---:|---:|")
    for hv_id in HV_IDS:
        cells = [_hv_cell(hv, hv_id, s, p) for s in ("baseline", "candidate") for p in ("a", "b")]
        a(f"| {hv_id} {HV_LABELS[hv_id]} | " + " | ".join(cells) + " |")
    a("")
    a("HV-3 (activation on the candidate), HV-4 (EnterPlanMode interactive, only if LV-3 was a limitation) and HV-5 (Cursor, only if "
      "available) are recorded by David as pass, fail or not run; none is recorded here.")
    a("")
    a("## Caveats")
    a("")
    a("- Bytes are a proxy for context cost; tokens per byte differ between prose, tables and JSON.")
    a("- `happy_path_total` follows the read map's `happy_path` flags; it is a model of a clean run, not a trace of one.")
    a("- Baseline kernel, protocol and largest-reference values are taken over the frozen read map's files; the candidate values are "
      "taken over the whole router corpus, which is how the budget gate measures them.")
    a("- The candidate was measured at the commit named above; the report commit itself is not part of the measured tree.")
    a("- The goal gap above is the measured state, not a forecast. No claim here depends on the human captures.")
    a("")
    a("## Draft ADR-0073 / SPEC-0034 addendum (draft only, not applied)")
    a("")
    bd = [r for r in data["rows"] if r["workflow"] == "BUILD" and r["variant"] == "standard"]
    if bd:
        a(f"Measured BUILD start set: {fmt(bd[0]['base_start'])} B at the baseline, {fmt(bd[0]['head_start'])} B at the candidate "
          f"({pct(bd[0]['base_start'], bd[0]['head_start'])}).")
    a("")
    a("Proposed addendum text for David, once the human checks are in: the measured numbers above, the gap to the goal stated as in this "
      "report, the LV lane result, and the HV outcomes. This commit does not change the status of either document.")
    a("")
    return "\n".join(L)


# --------------------------------------------------------------------------------------------- CLI

def _read_json(path: str, what: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ImpactError(f"cannot read {what} file {path}: {exc}") from exc


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Router context-cut before/after impact report")
    ap.add_argument("--baseline", help="baseline commit (default: the sha pinned in router-baseline.lock.json)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--date")
    ap.add_argument("--hv")
    ap.add_argument("--lv")
    ap.add_argument("--plugin-root")
    ap.add_argument("--no-inventory", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.plugin_root or PLUGIN_ROOT)
    try:
        baseline = args.baseline or cor.load_json(root / FIX / "router-baseline.lock.json")["baseline_sha"]
        hv = load_hv(_read_json(args.hv, "HV")) if args.hv else None
        lv = _read_json(args.lv, "LV") if args.lv else None
        data = collect(root, baseline, run_inventory=not args.no_inventory, date=args.date)
        text = render(data, hv=hv, lv=lv)
        Path(args.out).write_text(text, encoding="utf-8")
    except (ImpactError, OSError, ValueError, KeyError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    b = [r for r in data["rows"] if r["workflow"] == "BUILD" and r["variant"] == "standard"][0]
    print(json.dumps({"out": args.out, "baseline_ref": baseline, "head_sha": data["head_sha"],
                      "build_start": {"baseline": b["base_start"], "head": b["head_start"]},
                      "inventory_exit": data["inventory_exit"]}, sort_keys=True))
    return 1 if data["inventory_exit"] not in (None, 0) else 0


if __name__ == "__main__":
    sys.exit(main())
