#!/usr/bin/env python3
"""
craftflow_runtime_benchmark.py

Runtime complexity benchmark: context load, orchestration chain depth,
gate overhead, parallelism signals, and (for craftflow) real telemetry
from workflow event logs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
REF_ROOT = ROOT / "ref-(dont-read-unless-specificly-mentioned)"
STATE_ROOT = ROOT.parents[1] / ".craftflow" / "state"  # project root
OUT_DIR = ROOT / "docs" / "benchmarks"
TODAY = date.today().isoformat()

TARGETS = [
    ("craftflow", ROOT / "plugins" / "craftflow"),
    ("cc10x",     REF_ROOT / "cc10x" / "plugins" / "cc10x"),
    ("superpowers", REF_ROOT / "superpowers"),
]


# ---------------------------------------------------------------------------
# 1. Context load — how much text each harness injects per agent turn
# ---------------------------------------------------------------------------

def agent_files(plugin_root: Path) -> list[Path]:
    candidates = []
    for pat in ("agents/*.md", "skills/*/SKILL.md", "skills/*.md"):
        candidates.extend(plugin_root.glob(pat))
    # also top-level CLAUDE.md if it's the harness definition
    for p in (plugin_root.parent / "CLAUDE.md", plugin_root / "CLAUDE.md"):
        if p.exists():
            candidates.append(p)
    return [p for p in candidates if p.is_file()]


def context_load(plugin_root: Path) -> dict[str, Any]:
    files = agent_files(plugin_root)
    sizes = {p.name: p.stat().st_size for p in files}
    total_bytes = sum(sizes.values())
    # estimate tokens: ~4 bytes/token for English markdown
    total_tokens = total_bytes // 4
    agent_md = [p for p in files if "agents" in p.parts]
    skill_md  = [p for p in files if "skills" in p.parts]
    return {
        "agent_files": len(agent_md),
        "skill_files": len(skill_md),
        "total_files": len(files),
        "total_bytes": total_bytes,
        "estimated_tokens": total_tokens,
        "largest_file": max(sizes, key=lambda k: sizes[k]) if sizes else None,
        "largest_bytes": max(sizes.values()) if sizes else 0,
    }


# ---------------------------------------------------------------------------
# 2. Orchestration chain depth — agents dispatched per workflow type
# ---------------------------------------------------------------------------

CHAIN_PATTERNS = {
    "BUILD":  [
        r"component.builder|build.implement",
        r"code.reviewer|build.review",
        r"silent.failure.hunter|build.hunt",
        r"integration.verifier|build.verify",
        r"doc.syncer|build.doc",
    ],
    "DEBUG":  [
        r"bug.investigator|debug.investigate",
        r"code.reviewer|debug.review",
        r"integration.verifier|debug.verify",
    ],
    "PLAN":   [
        r"planner|plan.create",
        r"plan.gap.reviewer|gap.review",
    ],
    "REVIEW": [
        r"code.reviewer|review.audit",
    ],
}


def chain_depth(plugin_root: Path) -> dict[str, int]:
    blob = ""
    for p in plugin_root.rglob("*.md"):
        try:
            blob += p.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            pass
    depths: dict[str, int] = {}
    for wf, patterns in CHAIN_PATTERNS.items():
        depths[wf] = sum(1 for pat in patterns if re.search(pat, blob))
    return depths


# ---------------------------------------------------------------------------
# 3. Gate overhead — how many enforcement gates exist
# ---------------------------------------------------------------------------

GATE_PATTERNS = {
    "plan_trust_gate":    r"plan_trust_gate|open_decisions.*block|trust.*gate",
    "phase_exit_gate":    r"phase_exit_gate|phase.cursor|exit.criteria",
    "failure_stop_gate":  r"failure_stop_gate|stop.immediately|fail.closed",
    "scope_decision_gate":r"scope.decision|critical_only|all_issues",
    "memory_sync_gate":   r"memory_sync|memory.finali|persist.*state",
    "skill_precedence_gate": r"skill_precedence|outrank|user.*standards.*take.precedence",
    "doubt_verify_gate":  r"doubt.verif|confidence.*gate|re.verify",
    "review_loop":        r"re.review|remediation.loop|remfix",
    "hunt_loop":          r"re.hunt|silent.failure|hunt.*loop",
}


def gate_count(plugin_root: Path) -> dict[str, bool]:
    blob = ""
    for p in plugin_root.rglob("*.md"):
        try:
            blob += p.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            pass
    return {name: bool(re.search(pat, blob)) for name, pat in GATE_PATTERNS.items()}


# ---------------------------------------------------------------------------
# 4. Context management — compression, eviction, summarization signals
# ---------------------------------------------------------------------------

CONTEXT_MGMT_PATTERNS = {
    "compact_hook":        r"precompact|postcompact|context.compac",
    "state_persist":       r"stop.persist|session.persist|activecontext",
    "context_eviction":    r"evict|prune|trim.*context|context.*budget",
    "summarization":       r"summari[sz]e.*context|outline.mode|symbol.mode",
    "context_resume":      r"resume.*workflow|hydrat|reconstruct.*context",
    "token_tracking":      r"usage.ledger|token.count|cost.*per|usd.actual",
}


def context_mgmt(plugin_root: Path) -> dict[str, bool]:
    blob = ""
    for p in plugin_root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".md", ".py", ".json", ".sh", ".ts", ".toml"}:
            continue
        try:
            blob += p.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            pass
    return {name: bool(re.search(pat, blob)) for name, pat in CONTEXT_MGMT_PATTERNS.items()}


# ---------------------------------------------------------------------------
# 5. Parallelism — can the harness fan out agents?
# ---------------------------------------------------------------------------

PARALLEL_PATTERNS = {
    "parallel_agents":   r"parallel.*agent|dispatch.*parallel|fan.out",
    "worktree_isolation":r"git.worktree|worktree.isolat|worktree_mode|worktree_branch",
    "subagent_dispatch": r"dispatch.*subagent|subagent.driven|spawn.*agent",
    "concurrent_phases": r"concurrent.*phase|phase.*parallel|run.*simultaneously",
}


def parallelism(plugin_root: Path) -> dict[str, bool]:
    blob = ""
    for p in plugin_root.rglob("*.md"):
        try:
            blob += p.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            pass
    return {name: bool(re.search(pat, blob)) for name, pat in PARALLEL_PATTERNS.items()}


# ---------------------------------------------------------------------------
# 6. Real telemetry from craftflow workflow artifacts
# ---------------------------------------------------------------------------

def real_telemetry(state_root: Path) -> dict[str, Any]:
    wf_dir = state_root / "workflows"
    if not wf_dir.exists():
        return {"available": False}

    jsons = list(wf_dir.glob("*.json"))
    jsonls = list(wf_dir.glob("*.events.jsonl"))

    total = len(jsons)
    by_type: dict[str, int] = {}
    loop_counts: dict[str, list[int]] = {"re_review": [], "re_hunt": [], "re_verify": []}
    event_counts: list[int] = []

    for f in jsons:
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        wt = d.get("workflow_type", "unknown")
        by_type[wt] = by_type.get(wt, 0) + 1
        loops = d.get("telemetry", {}).get("loop_counts", {})
        for k in loop_counts:
            v = loops.get(k, 0)
            if isinstance(v, int):
                loop_counts[k].append(v)

    for f in jsonls:
        try:
            lines = [l for l in f.read_text().splitlines() if l.strip()]
            event_counts.append(len(lines))
        except Exception:
            pass

    def stats(vals: list[int]) -> dict[str, float]:
        if not vals:
            return {}
        s = sorted(vals)
        return {
            "mean": round(sum(s) / len(s), 2),
            "median": s[len(s) // 2],
            "max": max(s),
            "nonzero": sum(1 for v in s if v > 0),
            "nonzero_pct": round(100 * sum(1 for v in s if v > 0) / len(s), 1),
        }

    return {
        "available": True,
        "total_workflows": total,
        "by_type": by_type,
        "loop_stats": {k: stats(v) for k, v in loop_counts.items()},
        "events_per_workflow": stats(event_counts),
    }


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def score_dict(d: dict[str, bool]) -> tuple[int, int]:
    return sum(d.values()), len(d)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report() -> dict[str, Any]:
    results = []
    for name, plugin_root in TARGETS:
        if not plugin_root.exists():
            results.append({"repo": name, "available": False})
            continue
        cl   = context_load(plugin_root)
        cd   = chain_depth(plugin_root)
        gc   = gate_count(plugin_root)
        cm   = context_mgmt(plugin_root)
        par  = parallelism(plugin_root)
        # Supplemental: scan workflow JSON artifacts for worktree_mode field
        # (craftflow stores worktree_mode in .json artifacts, not in .md files)
        if name == "craftflow" and not par.get("worktree_isolation"):
            wf_dir = STATE_ROOT / "workflows"
            if wf_dir.exists():
                for jf in wf_dir.glob("*.json"):
                    try:
                        jtext = jf.read_text(encoding="utf-8", errors="ignore")
                        if '"worktree_mode": "auto_created"' in jtext or '"worktree_mode": "existing_worktree"' in jtext:
                            par = dict(par)
                            par["worktree_isolation"] = True
                            break
                    except OSError:
                        pass
        tel  = real_telemetry(STATE_ROOT) if name == "craftflow" else {"available": False}
        results.append({
            "repo":             name,
            "available":        True,
            "context_load":     cl,
            "chain_depth":      cd,
            "gates":            gc,
            "gates_score":      score_dict(gc),
            "context_mgmt":     cm,
            "context_mgmt_score": score_dict(cm),
            "parallelism":      par,
            "parallelism_score": score_dict(par),
            "real_telemetry":   tel,
        })
    return {"date": TODAY, "repos": results}


def render_md(data: dict[str, Any]) -> str:
    lines = [
        "# Runtime Complexity Benchmark",
        "",
        f"Date: {data['date']}",
        "",
        "> Structural proxies for runtime cost: context load, chain depth, gates, context management, parallelism.",
        "> ai-craft/craftflow also includes real telemetry from workflow event logs.",
        "",
    ]

    repos = [r for r in data["repos"] if r.get("available")]

    # ---- Context load table ----
    lines += ["## 1. Context Load (estimated tokens injected per turn)", ""]
    lines += [f"| Repo | Agent files | Skill files | Total bytes | Est. tokens | Largest file |"]
    lines += [f"|------|-------------|-------------|-------------|-------------|--------------|"]
    for r in repos:
        cl = r["context_load"]
        lines.append(
            f"| {r['repo']} | {cl['agent_files']} | {cl['skill_files']} | "
            f"{cl['total_bytes']:,} | {cl['estimated_tokens']:,} | {cl['largest_file']} ({cl['largest_bytes']:,}B) |"
        )
    lines.append("")

    # ---- Chain depth ----
    lines += ["## 2. Orchestration Chain Depth (agents per workflow type)", ""]
    lines += ["| Repo | BUILD | DEBUG | PLAN | REVIEW |"]
    lines += ["|------|-------|-------|------|--------|"]
    for r in repos:
        cd = r["chain_depth"]
        lines.append(
            f"| {r['repo']} | {cd.get('BUILD', 0)} | {cd.get('DEBUG', 0)} | "
            f"{cd.get('PLAN', 0)} | {cd.get('REVIEW', 0)} |"
        )
    lines.append("")

    # ---- Gates ----
    gate_names = list(GATE_PATTERNS.keys())
    lines += ["## 3. Enforcement Gates", ""]
    lines += ["| Gate | " + " | ".join(r["repo"] for r in repos) + " |"]
    lines += ["|------" + "|------" * len(repos) + "|"]
    for g in gate_names:
        row = f"| `{g}` |"
        for r in repos:
            v = r["gates"].get(g, False)
            row += " ✓ |" if v else " — |"
        lines.append(row)
    lines.append("")
    lines += ["**Gate scores:**", ""]
    for r in repos:
        s, t = r["gates_score"]
        lines.append(f"- {r['repo']}: **{s}/{t}**")
    lines.append("")

    # ---- Context management ----
    cm_names = list(CONTEXT_MGMT_PATTERNS.keys())
    lines += ["## 4. Context Management", ""]
    lines += ["| Signal | " + " | ".join(r["repo"] for r in repos) + " |"]
    lines += ["|--------" + "|--------" * len(repos) + "|"]
    for cm in cm_names:
        row = f"| `{cm}` |"
        for r in repos:
            v = r["context_mgmt"].get(cm, False)
            row += " ✓ |" if v else " — |"
        lines.append(row)
    lines.append("")
    lines += ["**Context management scores:**", ""]
    for r in repos:
        s, t = r["context_mgmt_score"]
        lines.append(f"- {r['repo']}: **{s}/{t}**")
    lines.append("")

    # ---- Parallelism ----
    par_names = list(PARALLEL_PATTERNS.keys())
    lines += ["## 5. Parallelism", ""]
    lines += ["| Signal | " + " | ".join(r["repo"] for r in repos) + " |"]
    lines += ["|--------" + "|--------" * len(repos) + "|"]
    for p in par_names:
        row = f"| `{p}` |"
        for r in repos:
            v = r["parallelism"].get(p, False)
            row += " ✓ |" if v else " — |"
        lines.append(row)
    lines.append("")
    lines += ["**Parallelism scores:**", ""]
    for r in repos:
        s, t = r["parallelism_score"]
        lines.append(f"- {r['repo']}: **{s}/{t}**")
    lines.append("")

    # ---- Real telemetry (craftflow only) ----
    for r in repos:
        tel = r.get("real_telemetry", {})
        if not tel.get("available"):
            continue
        lines += [f"## 6. Real Telemetry — {r['repo']} ({tel['total_workflows']} workflows)", ""]
        by_type = tel.get("by_type", {})
        lines.append("**Workflow distribution:** " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
        lines.append("")
        lines += ["**Remediation loop rates** (re_review / re_hunt / re_verify):", ""]
        for lk, ls in tel.get("loop_stats", {}).items():
            if ls:
                lines.append(
                    f"- `{lk}`: mean={ls['mean']}, median={ls['median']}, max={ls['max']}, "
                    f"triggered in {ls['nonzero']}/{tel['total_workflows']} runs ({ls['nonzero_pct']}%)"
                )
        lines.append("")
        ep = tel.get("events_per_workflow", {})
        if ep:
            lines.append(
                f"**Events per workflow** (proxy for agent turn count): "
                f"mean={ep.get('mean')}, median={ep.get('median')}, max={ep.get('max')}"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = build_report()

    json_path = OUT_DIR / f"{TODAY}-runtime-complexity.json"
    md_path   = OUT_DIR / f"{TODAY}-runtime-complexity.md"

    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    md_path.write_text(render_md(data), encoding="utf-8")

    print(json.dumps({"json": str(json_path.relative_to(ROOT)), "md": str(md_path.relative_to(ROOT))}, indent=2))


if __name__ == "__main__":
    main()
