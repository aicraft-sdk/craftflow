#!/usr/bin/env python3
"""The verbose status chain renders the QA, TRIAGE and CODEBASE-HEALTH workflows (ADR-0075, H2, D-13).

Run: python3 tests/fixtures/test_craftflow_status_chains.py
Each case writes a fixture events log (one agent_completed per chain agent, raw agent names, plus memory_finalized) under a
scratch project dir and renders the chain through craftflow_status_report._render_agent_chain.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_status_report as sr  # noqa: E402

# workflow type -> the raw agent names that complete its chain
RAW_AGENTS = {
    "QA": ["qa-researcher", "planner", "plan-gap-reviewer", "qa-harness-builder", "code-reviewer", "silent-failure-hunter", "qa-executor"],
    "TRIAGE": ["triage-agent"],
    "CODEBASE-HEALTH": ["architecture-scanner"],
}

_passes = 0
_errors: list = []


def check(name: str, cond: bool, reason: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason}")
        print(f"  FAIL: {name}: {reason}")


def render(wtype: str, agents: list, wf_id: str) -> list:
    """Render the chain of a fixture workflow whose events log holds one agent_completed per name in `agents`."""
    with tempfile.TemporaryDirectory() as tmp:
        wf_dir = Path(tmp) / ".craftflow" / "state" / "workflows"
        wf_dir.mkdir(parents=True)
        events = [{"event": "agent_completed", "agent": a, "status": "PASS"} for a in agents]
        events.append({"event": "memory_finalized"})
        (wf_dir / f"{wf_id}.events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
        old = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        try:
            return sr._render_agent_chain({"workflow_type": wtype}, wf_id)
        finally:
            if old is None:
                os.environ.pop("CLAUDE_PROJECT_DIR", None)
            else:
                os.environ["CLAUDE_PROJECT_DIR"] = old


def main() -> int:
    for wtype, agents in RAW_AGENTS.items():
        lines = render(wtype, agents, f"wf-chain-{wtype.lower()}")
        roles = sr._CHAINS.get(wtype)
        check(f"{wtype}: every chain role renders done",
              roles is not None and len(lines) == len(roles) and all(l.startswith(f"  ✓ {r}:") for l, r in zip(lines, roles)),
              f"chain={roles} lines={lines}")
    qa_missing = render("QA", [a for a in RAW_AGENTS["QA"] if a != "qa-executor"], "wf-chain-qa-missing")
    check("QA: a log without qa-executor renders it pending", "  ○ qa_executor: pending" in qa_missing
          and not any("qa_executor" in l and "✓" in l for l in qa_missing), f"lines={qa_missing}")
    known_roles = set(sr._AGENT_NAME_TO_ROLE.values()) | {"memory_finalize"}
    stray = {w: [r for r in sr._CHAINS.get(w, []) if r not in known_roles] for w in RAW_AGENTS}
    check("every new chain role is an agent role or memory_finalize", all(not v for v in stray.values()) and all(w in sr._CHAINS for w in RAW_AGENTS),
          f"stray={stray}")
    check("the five new agents map to role keys",
          all(sr._AGENT_NAME_TO_ROLE.get(a) == a.replace("-", "_") for a in
              ("qa-researcher", "qa-harness-builder", "qa-executor", "triage-agent", "architecture-scanner")),
          f"map={sr._AGENT_NAME_TO_ROLE}")
    check("ORIENT has no chain (it mints no artifact)", "ORIENT" not in sr._CHAINS, "ORIENT must stay chainless")
    check("existing chains are unchanged",
          sr._CHAINS["BUILD"] == ["builder", "reviewer", "hunter", "verifier", "doc_syncer", "memory_finalize"]
          and sr._CHAINS["REVIEW"] == ["reviewer", "memory_finalize"], f"chains={sr._CHAINS}")
    with tempfile.TemporaryDirectory() as tmp:  # H5: a handoff child shows its parent and how it was started
        (Path(tmp) / ".craftflow" / "state" / "workflows").mkdir(parents=True)
        old = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        try:
            child = sr._report_one("wf-child", {"workflow_type": "PLAN", "parent_workflow_uuid": "wf-parent",
                                                "handoff": {"mode": "asked", "source_route": "TRIAGE"}}, False)
            plain = sr._report_one("wf-plain", {"workflow_type": "PLAN"}, False)
        finally:
            if old is None:
                os.environ.pop("CLAUDE_PROJECT_DIR", None)
            else:
                os.environ["CLAUDE_PROJECT_DIR"] = old
    check("a handoff child reports its parent and mode", "  parent: wf-parent (handoff asked from TRIAGE)" in child, f"lines={child}")
    check("a workflow with no parent has no parent line", not any(l.startswith("  parent:") for l in plain), f"lines={plain}")
    print()
    for e in _errors:
        print(e)
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
