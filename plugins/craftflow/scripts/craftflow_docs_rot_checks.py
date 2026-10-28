#!/usr/bin/env python3
"""Pure docs-rot checks D1..D10 for the craftflow plugin.

Every check is a function of literal texts and collections and returns a sorted list of stable violation ids
`rot:<check>:<subject>`. Ids name the drifting surface, never a volatile value (a version or a count), so a baseline
entry survives a release. `craftflow_docs_rot.py` reads the repository, calls `run_all`, and runs the ratchet.
Python 3.9, standard library only.
"""
from __future__ import annotations

import json
import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

README_PLUGIN = "tools/craftflow-plugin/plugins/craftflow/README.md"
README_TOP = "tools/craftflow-plugin/README.md"

# Agents that have a file but no phase in the Explicit Dispatcher, each with the reason (none today).
UNDISPATCHED: Dict[str, str] = {}
# Dispatched agents the Cursor router does not list in Agent File Paths, each with the reason.
CLAUDE_ONLY_AGENTS: Dict[str, str] = {
    "doubt-verifier": "doubt-verify and fix-verify are deferred to v2 in cursor-router (no Cursor Task-Dispatch equivalent)",
    "doc-syncer": "the doc-syncer phase is deferred to v2 in cursor-router",
    "learn-distiller": "the learn-distill phase is deferred to v2 in cursor-router",
}
# Skills install-cursor.sh links besides cursor-router.
CURSOR_LINKED_SKILLS = {"retro", "status", "failure-digest"}
# The only workflow allowed to lack a status chain key. Any other entry is itself a violation.
NO_CHAIN_WORKFLOWS: Dict[str, str] = {"ORIENT": "creates no artifact, so status never renders it"}
ALLOWED_NO_CHAIN = {"ORIENT"}

_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_HEADING = re.compile(r"^(#{1,6})\s")


def split_row(line: str) -> List[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [c.strip().replace("\\|", "|") for c in _CELL_SPLIT.split(body)]


def section(text: str, heading: str) -> str:
    """Body under `heading` (for example "## Agents") up to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    body: List[str] = []
    found = in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if found:
            if m and len(m.group(1)) <= level:
                break
            body.append(line)
        elif m and (line.rstrip() == heading or line.startswith(heading + " ")):
            found = True
    return "\n".join(body)


def table(body: str) -> Tuple[List[str], List[List[str]]]:
    """(header cells, data rows) of the first pipe table in `body`; ([], []) when there is none."""
    rows = [split_row(ln) for ln in body.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 2:
        return [], []
    return rows[0], rows[2:]


def first_cells(body: str) -> List[str]:
    return [r[0] for r in table(body)[1] if r]


def backticked(cell: str) -> List[str]:
    return re.findall(r"`([^`]+)`", cell)


def routing_workflows(protocol: str) -> List[str]:
    header, rows = table(section(protocol, "## Intent Routing"))
    if "Workflow" not in header:
        return []
    idx = header.index("Workflow")
    seen: List[str] = []
    for r in rows:
        if len(r) > idx and r[idx] and r[idx] not in seen:
            seen.append(r[idx])
    return seen


def dispatched_agents(protocol: str) -> Set[str]:
    return set(re.findall(r"craftflow:([a-z0-9][a-z0-9-]*)", section(protocol, "## Explicit Dispatcher (Phase-to-Agent Table)")))


def agent_skills(agent_md: str) -> List[str]:
    m = re.match(r"---\n(.*?)\n---", agent_md, re.DOTALL)
    if not m:
        return []
    out: List[str] = []
    in_list = False
    for line in m.group(1).splitlines():
        if line.rstrip() == "skills:":
            in_list = True
        elif in_list:
            item = re.match(r"\s+-\s+(\S+)", line)
            if not item:
                break
            out.append(item.group(1).partition(":")[2] or item.group(1))
    return out


def d1(stems: Iterable[str], top_text: str, plugin_text: str) -> List[str]:
    stems = set(stems)
    ids: List[str] = []
    for label, cells in (
        ("top", first_cells(section(top_text, "## Agents"))),
        ("plugin", first_cells(section(plugin_text, "### Default agent models"))),
    ):
        named = {n for c in cells for n in backticked(c)}
        ids += [f"rot:D1:missing-in-{label}:{s}" for s in stems - named]
        ids += [f"rot:D1:unknown-in-{label}:{n}" for n in named - stems]
    m = re.search(r"(\d+) specialized agents", section(plugin_text, "## What it does"))
    if not m:
        ids.append("rot:D1:count-missing")
    elif int(m.group(1)) != len(stems):
        ids.append("rot:D1:count")
    return sorted(ids)


def d2(protocol: str, stems: Iterable[str], undispatched: Dict[str, str]) -> List[str]:
    stems = set(stems)
    dispatched = dispatched_agents(protocol)
    ids = [f"rot:D2:missing-file:{n}" for n in dispatched - stems]
    ids += [f"rot:D2:undispatched:{s}" for s in stems - dispatched - set(undispatched)]
    ids += [f"rot:D2:stale-undispatched:{n}" for n in set(undispatched) & dispatched]
    return sorted(ids)


def d3(dispatched: Iterable[str], cursor_text: str, claude_only: Dict[str, str]) -> List[str]:
    dispatched = set(dispatched)
    listed = {c.strip("`") for c in first_cells(section(cursor_text, "## Agent File Paths"))}
    ids = [f"rot:D3:missing:{n}" for n in dispatched - listed - set(claude_only)]
    ids += [f"rot:D3:stale-claude-only:{n}" for n in set(claude_only) & listed]
    return sorted(ids)


def d4(plugin_text: str, skill_count: int) -> List[str]:
    m = re.search(r"\*\*(\d+) skills\*\*", section(plugin_text, "## What it does"))
    if not m:
        return ["rot:D4:count-missing"]
    return [] if int(m.group(1)) == skill_count else ["rot:D4:count"]


def _hook_scripts(hooks_json: str) -> Set[str]:
    found: Set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "command" and isinstance(value, str):
                    found.update(re.findall(r"scripts/([\w.\-]+\.(?:py|sh))", value))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(json.loads(hooks_json))
    return found


def d5(claude_hooks: str, cursor_hooks: str, existing_scripts: Iterable[str], hooks_readme: str) -> List[str]:
    existing = set(existing_scripts)
    claude, cursor = _hook_scripts(claude_hooks), _hook_scripts(cursor_hooks)
    ids = [f"rot:D5:missing-script:claude:{n}" for n in claude - existing]
    ids += [f"rot:D5:missing-script:cursor:{n}" for n in cursor - existing]
    ids += [f"rot:D5:undocumented:{n}" for n in claude if n not in hooks_readme]
    return sorted(ids)


def d6(plugin_version: str, marketplace: dict, changelog: str, readmes: Dict[str, str]) -> List[str]:
    ids: List[str] = []
    if (marketplace.get("metadata") or {}).get("version") != plugin_version:
        ids.append("rot:D6:marketplace-metadata")
    mine = [p for p in marketplace.get("plugins") or [] if p.get("name") == "craftflow"]
    if not mine or any(p.get("version") != plugin_version for p in mine):
        ids.append("rot:D6:marketplace-plugin")
    top = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
    if not top or top.group(1) != plugin_version:
        ids.append("rot:D6:changelog")
    for name, text in readmes.items():
        versions = re.findall(r"\*\*Current version:\*\*\s*v?(\d+\.\d+\.\d+)", text) + re.findall(r"craftflow v(\d+\.\d+\.\d+)", text)
        if any(v != plugin_version for v in versions):
            ids.append(f"rot:D6:{name}")
    return sorted(ids)


def d7(protocol: str, readmap_variants: Dict[str, list], jev_labels: Iterable[str], chain_keys: Iterable[str],
       no_chain: Optional[Dict[str, str]] = None) -> List[str]:
    no_chain = NO_CHAIN_WORKFLOWS if no_chain is None else no_chain
    jev_labels, chain_keys = set(jev_labels), set(chain_keys)
    ids: List[str] = []
    for w in routing_workflows(protocol):
        if w not in readmap_variants:
            ids.append(f"rot:D7:readmap:{w}")
        if w not in jev_labels:
            ids.append(f"rot:D7:jev:{w}")
        if w not in chain_keys and w not in no_chain:
            ids.append(f"rot:D7:chain:{w}")
    ids += [f"rot:D7:exemption:{n}" for n in no_chain if n not in ALLOWED_NO_CHAIN]
    return sorted(ids)


def d8(skills_by_agent: Dict[str, List[str]], skill_names: Iterable[str]) -> List[str]:
    skill_names = set(skill_names)
    return sorted(f"rot:D8:{a}:{s}" for a, skills in skills_by_agent.items() for s in skills if s not in skill_names)


def d9(install_text: str, linked: Iterable[str]) -> List[str]:
    m = re.search(r"for SKILL_NAME in ([^;\n]+);\s*do", install_text)
    if not m:
        return ["rot:D9:loop-missing"]
    actual, expected = set(m.group(1).split()), {"cursor-router"} | set(linked)
    return sorted([f"rot:D9:extra:{n}" for n in actual - expected] + [f"rot:D9:missing:{n}" for n in expected - actual])


def d10(protocol: str, plugin_text: str) -> List[str]:
    header, rows = table(section(plugin_text, "## Workflow types"))
    shown = {r[header.index("Workflow")] for r in rows if "Workflow" in header and len(r) > header.index("Workflow")}
    expected = set(routing_workflows(protocol))
    return sorted([f"rot:D10:missing:{w}" for w in expected - shown] + [f"rot:D10:unknown:{w}" for w in shown - expected])


def run_all(s: dict) -> List[str]:
    """All violation ids for a surface snapshot (see craftflow_docs_rot.gather)."""
    ids = set()
    ids.update(d1(s["agent_stems"], s["top_text"], s["plugin_text"]))
    ids.update(d2(s["protocol"], s["agent_stems"], UNDISPATCHED))
    ids.update(d3(dispatched_agents(s["protocol"]), s["cursor_text"], CLAUDE_ONLY_AGENTS))
    ids.update(d4(s["plugin_text"], len(s["skill_names"])))
    ids.update(d5(s["claude_hooks"], s["cursor_hooks"], s["existing_scripts"], s["hooks_readme"]))
    ids.update(d6(s["plugin_version"], s["marketplace"], s["changelog"], {"README_TOP": s["top_text"], "README_PLUGIN": s["plugin_text"]}))
    ids.update(d7(s["protocol"], s["readmap_variants"], s["jev_labels"], s["chain_keys"]))
    ids.update(d8(s["skills_by_agent"], s["skill_names"]))
    ids.update(d9(s["install_text"], CURSOR_LINKED_SKILLS))
    ids.update(d10(s["protocol"], s["plugin_text"]))
    return sorted(ids)
