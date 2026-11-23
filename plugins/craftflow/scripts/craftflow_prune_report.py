#!/usr/bin/env python3
"""Provenance-and-prune report: what in Craftflow no longer earns its place. Read-only; never edits or deletes.

Two lists, after "Harness as Living Artifact" (every line of a harness should trace back to a past failure) and
Hermes Curator (a skill unused for 30 days is stale, 90 days an archive candidate, with critical ones pinned):

    provenance   router rule units (from the rule-inventory baseline) whose text, heading and label cite no reason to
                 exist: no ADR, spec or FR/SC id, no dated incident, no enforcing script or test, no docs path, and no
                 row in docs/router-policy-map.md. These are the units to ask "which failure does this prevent?".
    usage        shipped skills (SKILL.md), skill references and agents, counted from Claude Code transcripts: a Skill
                 tool call or a Read of a skill file counts for the skill, a Read of a reference for the reference, an
                 Agent or Task dispatch for the agent. Reported as stale (30 to 89 days), archive (90 days or more) and
                 never seen. The router, cursor-router and _shared files are pinned and never listed.

Removing router prose still needs a signed policy-map row, and removing a skill or agent is a person's decision. This
report only says where to look.

Usage:
    craftflow_prune_report.py [--plugin-root DIR] [--baseline FILE] [--policy-map FILE]
        [--transcripts DIR ...] [--now YYYY-MM-DD]

Defaults: this plugin, its committed inventory baseline, the monorepo policy map, ~/.claude/projects. Prints one JSON
line and exits 0; exits 1 only when the baseline cannot be read. Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

PLUGIN = Path(__file__).resolve().parents[1]
PINNED_SKILLS = frozenset({"craftflow-router", "cursor-router", "_shared"})
STALE_DAYS, ARCHIVE_DAYS = 30, 90
_PROVENANCE_RE = re.compile(
    r"\bADR[- ]?\d{2,4}\b|\bSPEC[- ]?\d{2,4}\b|\b(?:FR|NFR|SC|DD|PH)-\d+\b|\bincident\b|\b20\d\d-\d\d-\d\d\b"
    r"|\bcraftflow_\w+\.(?:py|sh)\b|\btest_\w+|\bdocs/|\.github/|\bhooks\.json\b",
    re.IGNORECASE,
)
_UNIT_ID_RE = re.compile(r"\b[0-9a-f]{12}\b")
_SKILL_FILE_RE = re.compile(r"/skills/([^/]+)/(SKILL\.md|references/([^/]+\.md))$")


def provenance(units: Iterable[dict], policy_ids: Set[str]) -> dict:
    """Split rule units into those that cite a reason to exist and those that don't."""
    total, with_prov, without = 0, 0, []
    for unit in units:
        total += 1
        occ = (unit.get("occurrences") or [{}])[0]
        heading = " > ".join(occ.get("heading_path") or [])
        haystack = " ".join([unit.get("text") or "", heading, occ.get("label_ctx") or ""])
        if unit.get("id") in policy_ids or _PROVENANCE_RE.search(haystack):
            with_prov += 1
            continue
        without.append({"id": unit.get("id"), "file": occ.get("file"), "heading": heading,
                        "kind": unit.get("kind"), "excerpt": (unit.get("text") or "")[:120]})
    return {"units": total, "with_provenance": with_prov, "without_provenance": without}


def _items(plugin_root: Path) -> Dict[str, dict]:
    items: Dict[str, dict] = {}
    for skill_md in sorted(plugin_root.glob("skills/*/SKILL.md")):
        skill = skill_md.parent.name
        items[f"skill:{skill}"] = {"pinned": skill in PINNED_SKILLS}
        for ref in sorted(skill_md.parent.glob("references/*.md")):
            items[f"reference:{skill}/{ref.name}"] = {"pinned": skill in PINNED_SKILLS}
    for agent in sorted(plugin_root.glob("agents/*.md")):
        items[f"agent:{agent.stem}"] = {"pinned": False}
    for item in items.values():
        item.update(uses=0, last=None)
    return items


def _names_for(tool: str, inp: dict) -> List[str]:
    if tool == "Skill":
        skill = str(inp.get("skill") or inp.get("command") or "")
        return [f"skill:{skill.split(':')[-1]}"] if skill else []
    if tool in ("Agent", "Task"):
        agent = str(inp.get("subagent_type") or "")
        return [f"agent:{agent.split(':')[-1]}"] if agent.startswith("craftflow:") else []
    if tool == "Read":
        match = _SKILL_FILE_RE.search(str(inp.get("file_path") or "").replace("\\", "/"))
        if not match:
            return []
        return [f"reference:{match.group(1)}/{match.group(3)}"] if match.group(3) else [f"skill:{match.group(1)}"]
    return []


def _ts(raw) -> Optional[datetime]:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def usage(plugin_root: Path, transcript_dirs: Iterable[Path], now: datetime) -> dict:
    items = _items(plugin_root)
    files = bad = 0
    for base in transcript_dirs:
        for path in sorted(base.rglob("*.jsonl")) if base.is_dir() else []:
            files += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                content = (row.get("message") or {}).get("content") if isinstance(row, dict) else None
                if not isinstance(content, list):
                    continue
                when = _ts(row.get("timestamp"))
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    for name in _names_for(str(block.get("name")), block.get("input") or {}):
                        item = items.get(name)
                        if item is None:
                            continue
                        item["uses"] += 1
                        if when and (item["last"] is None or when > item["last"]):
                            item["last"] = when
    out_items, stale, archive, never = [], [], [], []
    for name, item in items.items():
        days = (now - item["last"]).days if item["last"] else None
        out_items.append({"name": name, "uses": item["uses"], "days_since": days, "pinned": item["pinned"]})
        if item["pinned"]:
            continue
        if item["uses"] == 0:
            never.append(name)
        elif days is not None and days >= ARCHIVE_DAYS:
            archive.append(name)
        elif days is not None and days >= STALE_DAYS:
            stale.append(name)
    return {"transcript_files": files, "bad_lines": bad, "items": out_items,
            "stale_30d": stale, "archive_90d": archive, "never_seen": never}


def _policy_ids(path: Path) -> Set[str]:
    try:
        return set(_UNIT_ID_RE.findall(path.read_text(encoding="utf-8")))
    except OSError:
        return set()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only provenance-and-prune report.")
    parser.add_argument("--plugin-root", default=str(PLUGIN))
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--policy-map", default=None)
    parser.add_argument("--transcripts", nargs="*", default=None)
    parser.add_argument("--now", default=None)
    args = parser.parse_args(argv)
    root = Path(args.plugin_root)
    baseline = Path(args.baseline) if args.baseline else PLUGIN / "tests/fixtures/router-rule-inventory.baseline.json"
    policy_map = Path(args.policy_map) if args.policy_map else PLUGIN.parents[1] / "docs/router-policy-map.md"
    dirs = [Path(d) for d in args.transcripts] if args.transcripts is not None else [Path.home() / ".claude/projects"]
    now = _ts(args.now + "T00:00:00Z") if args.now else datetime.now(timezone.utc)
    try:
        units = json.loads(baseline.read_text(encoding="utf-8")).get("entries", [])
    except (OSError, ValueError, AttributeError) as exc:
        print(json.dumps({"error": f"cannot read baseline {baseline}: {exc}"}))
        return 1
    report = {"provenance": provenance(units, _policy_ids(policy_map)),
              "usage": usage(root, dirs, now or datetime.now(timezone.utc)),
              "note": "read-only; removing router prose needs a signed policy-map row"}
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
