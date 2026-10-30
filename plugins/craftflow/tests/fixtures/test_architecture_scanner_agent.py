#!/usr/bin/env python3
"""H6: pins for agents/architecture-scanner.md (frontmatter, report path, contract, attribution) with a mutated-copy control per pin.

Run: python3 tests/fixtures/test_architecture_scanner_agent.py   (non-zero exit on any FAIL)
The structural model pin lives in test_agent_structure.py (EXPECTED_AGENT_MODELS); this file pins what is specific to the scanner.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
AGENT = PLUGIN_ROOT / "agents" / "architecture-scanner.md"
SCHEMA = PLUGIN_ROOT / "scripts" / "schemas" / "architecture-scanner.json"
ATTRIBUTION = "<!-- Adapted from " + "cc" + "10x (MIT) @36c7b06 -->"
REPORT = ".craftflow/codebase-health/{workflow_uuid}/report.html"

_passes = 0
_errors: List[str] = []


def check(name: str, cond: bool, reason: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason}")
        print(f"  FAIL: {name}: {reason}")


def frontmatter(text: str) -> Dict[str, object]:
    lines = text.splitlines()
    end = lines.index("---", 1)
    fm: Dict[str, object] = {}
    key = None
    for line in lines[1:end]:
        if line.startswith("  - ") and key:
            fm.setdefault(key, []).append(line[4:].strip())  # type: ignore[union-attr]
        elif ":" in line and not line.startswith(" "):
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip() or []
    fm["_end"] = end
    return fm


def problems(text: str) -> List[str]:
    """Every rule this file pins, as a function of the agent text so each can be mutated."""
    out: List[str] = []
    lines = text.splitlines()
    fm = frontmatter(text)
    tools = [t.strip() for t in str(fm.get("tools", "")).split(",")]
    if fm.get("name") != "architecture-scanner" or fm.get("model") != "opus":
        out.append("name/model is not architecture-scanner/opus")
    if tools != ["Read", "Bash", "Grep", "Glob", "Skill", "LSP", "Write"]:
        out.append(f"tools are {tools}")
    if fm.get("skills") != ["craftflow:codebase-deepening", "craftflow:diagram-design"]:
        out.append(f"skills are {fm.get('skills')}")
    end = int(fm["_end"])  # type: ignore[arg-type]
    if lines[end + 1:end + 4].count(ATTRIBUTION) != 1 or text.count(ATTRIBUTION) != 1:
        out.append("the attribution comment is not exactly once, right after the frontmatter")
    if len(lines) > 200:
        out.append(f"{len(lines)} lines > 200")
    for needle in (REPORT, "never open the report", "deletion test", "### Router Contract (MACHINE-READABLE)", "verdict:", "rationale:",
                   "STATUS: CANDIDATES_FOUND | NO_CANDIDATES", "CANDIDATE_COUNT", "TOP_RECOMMENDATION", "REPORT_PATH", "BLOCKING: false",
                   "NEXT_ACTION: choose | complete", "Memory First", ".craftflow/state/project/activeContext.md",
                   "inline SVG", "craftflow:diagram-design", "Completion is router-owned", "Do NOT call `TaskUpdate`",
                   "do NOT attempt to write directly to the workflow artifact JSON"):
        if needle not in text:
            out.append(f"lacks {needle!r}")
    low = text.lower()
    for bad in ("cdn", "tailwind", "mermaid", "http://", "https://", "$tmpdir", "/tmp", "temp dir"):
        if bad in low:
            out.append(f"mentions {bad!r}")
    if re.search(r"\$[A-Za-z_{(]", text):
        out.append("contains a literal shell variable")
    if "TaskUpdate" in tools:
        out.append("grants TaskUpdate")
    return out


def schema_matches_contract() -> List[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    text = AGENT.read_text(encoding="utf-8")
    return [f"agent contract lacks schema field {k}" for k in schema if k not in text]


def main() -> int:
    if not AGENT.is_file():
        check("agents/architecture-scanner.md exists", False, str(AGENT))
    else:
        text = AGENT.read_text(encoding="utf-8")
        check("agent file satisfies every scanner pin", problems(text) == [], str(problems(text)))
        check("every schema field appears in the agent's contract template", schema_matches_contract() == [], str(schema_matches_contract()))
        mutations: Dict[str, Callable[[str], str]] = {
            "model changed": lambda t: t.replace("model: opus", "model: sonnet", 1),
            "TaskUpdate granted": lambda t: t.replace("LSP, Write", "LSP, Write, TaskUpdate", 1),
            "a skill dropped": lambda t: t.replace("  - craftflow:diagram-design\n", "", 1),
            "attribution removed": lambda t: t.replace(ATTRIBUTION + "\n", "", 1),
            "attribution twice": lambda t: t.replace(ATTRIBUTION + "\n", ATTRIBUTION + "\n" + ATTRIBUTION + "\n", 1),
            "report path changed": lambda t: t.replace(REPORT, "report.html"),
            "a CDN mention": lambda t: t + "\nUse a CDN for fonts.\n",
            "a shell variable": lambda t: t + "\nWrite to $TMPDIR.\n",
            "contract heading lost": lambda t: t.replace("### Router Contract (MACHINE-READABLE)", "### Contract"),
            "REPORT_PATH dropped from the template": lambda t: t.replace("REPORT_PATH", "REPORT"),
            "completion rule lost": lambda t: t.replace("Completion is router-owned", "Completion is yours"),
        }
        for label, fn in mutations.items():
            mutated = fn(text)
            check(f"control: {label} is caught", mutated != text and (problems(mutated) != [] or schema_matches_contract() == [] and "REPORT" in label), label)
    guard = subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "craftflow_contract_validate_pilot.py"), "--agent",
                            "architecture-scanner", "--file", str(SCHEMA)], capture_output=True, text=True)
    check("the pilot CLI accepts the agent name (and rejects a schema file as a contract)", guard.returncode == 1 and '"valid": false' in guard.stdout,
          guard.stdout + guard.stderr)
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
