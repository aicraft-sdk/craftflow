#!/usr/bin/env python3
"""Replay check for the harvest routes (H3: ORIENT).

Replays `tests/fixtures/orient-inline.json` against the live registry and docs: the routing law routes each
request to ORIENT, the protocol row derives an inline answer with no agents and no artifact, the read map
registers `route-orient.md`, the doc pins hold (no `TaskCreate`, never falls into BUILD, re-routes from
scratch), the Cursor binding is read-only, and `craftflow_workflow_id.py` refuses to mint an ORIENT artifact.

Exit 0 when every check passes, 1 otherwise (including a missing, unparsable or mis-shaped fixture).
Run: python3 scripts/craftflow_route_replay_check.py [--fixture PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_routing_law as law  # noqa: E402

FIXTURE = PLUGIN_ROOT / "tests" / "fixtures" / "orient-inline.json"
PROTOCOL = "skills/_shared/router-protocol.md"
ROUTE_FILE = "skills/_shared/route-orient.md"
KERNEL = "skills/craftflow-router/SKILL.md"
WORKFLOW_START = "skills/craftflow-router/references/workflow-start.md"
CURSOR_ROUTES = "skills/cursor-router/references/routes.md"
MINT = PLUGIN_ROOT / "scripts" / "craftflow_workflow_id.py"

# (file, pinned text): each must appear verbatim.
DOC_PINS = (
    (ROUTE_FILE, "no `TaskCreate`"),
    (ROUTE_FILE, "re-routes from scratch"),
    (ROUTE_FILE, "### ORIENT preparation"),
    (ROUTE_FILE, "### ORIENT procedure"),
    (ROUTE_FILE, "### ORIENT exit"),
    (ROUTE_FILE, "### ORIENT vs REVIEW"),
    (ROUTE_FILE, "### ORIENT phases"),
    (ROUTE_FILE, "inline and read-only"),
    (ROUTE_FILE, "it never falls into BUILD"),
    (ROUTE_FILE, "no agents"),
    (ROUTE_FILE, "Stop at understanding"),
    (ROUTE_FILE, "do not write any file"),
    (ROUTE_FILE, "Do not propose or apply edits"),
    (ROUTE_FILE, "fresh artifact"),
    (ROUTE_FILE, "The only ORIENT phase token is `orient`"),
    (PROTOCOL, "never falls into BUILD"),
    (KERNEL, "### ORIENT pointer"),
    (KERNEL, "skills/_shared/route-orient.md"),
    (WORKFLOW_START, "see the route file's `### {ROUTE} phases`"),
)


def read(rel: str) -> str:
    return (PLUGIN_ROOT / rel).read_text(encoding="utf-8")


def derive_outcome(protocol_text: str, route_text: str = "") -> Dict[str, Any]:
    """What the protocol's ORIENT row (and the route file's "no Memory Update") says happens, as the fixture's `expected` fields."""
    row = next((r for r in law.parse_routing_table(protocol_text) if r[3] == "ORIENT"), None)
    chain = ""
    for line in protocol_text.splitlines():
        if line.startswith("| 4 |") and "ORIENT" in line:
            chain = line.strip().strip("|").split("|")[-1].strip().lower()
    inline = row is not None and chain.startswith("inline answer")
    return {
        "workflow": row[3] if row else None,
        "next_action": "answer_inline" if inline else "dispatch",
        "task_creates": 0 if "no agents" in chain else None,
        "artifact_created": False if "no artifact" in chain else None,
        "agents_dispatched": 0 if "no agents" in chain else None,
        "memory_update": False if "no Memory Update" in route_text else None,
    }


def section_after(text: str, heading: str) -> Optional[str]:
    """Body of the first heading line containing `heading`, up to the next heading of the same or higher level."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#") and heading in line:
            level = len(line) - len(line.lstrip("#"))
            body: List[str] = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("#") and len(nxt) - len(nxt.lstrip("#")) <= level:
                    break
                body.append(nxt)
            return "\n".join(body)
    return None


EXPECTED_KEYS = ("next_action", "task_creates", "artifact_created", "agents_dispatched", "memory_update")


def check_fixture(fixture: Any) -> List[str]:
    """Shape problems: every key the other checks read must be present with the right value and type."""
    if not isinstance(fixture, dict):
        return ["fixture is not a JSON object"]
    exp = fixture.get("expected")
    if not isinstance(exp, dict):
        return ["fixture lacks an `expected` object"]
    problems: List[str] = [f"fixture lacks expected.{k}" for k in EXPECTED_KEYS if k not in exp]
    if fixture.get("workflow_type") != "ORIENT" or exp.get("workflow") != "ORIENT":
        problems.append("fixture does not describe an ORIENT replay")
    for key, want in (("next_action", "answer_inline"), ("task_creates", 0), ("artifact_created", False),
                      ("agents_dispatched", 0), ("memory_update", False)):
        if key in exp and (exp.get(key) != want or type(exp.get(key)) is not type(want)):
            problems.append(f"fixture expected.{key} must be {want!r}, got {exp.get(key)!r}")
    return problems


def check_routing(fixture: Dict[str, Any]) -> List[str]:
    requests = fixture.get("requests") or [fixture.get("request", "")]
    return [f"request {r!r} routes to {got}, not ORIENT" for r in requests
            if (got := law.route(r)["workflow"]) != "ORIENT"]


def check_outcome(fixture: Dict[str, Any]) -> List[str]:
    got = derive_outcome(read(PROTOCOL), read(ROUTE_FILE))
    exp = fixture.get("expected") or {}
    return [f"protocol ORIENT row derives {k}={got[k]!r}, fixture expects {exp.get(k)!r}"
            for k in ("workflow", *EXPECTED_KEYS) if got[k] != exp.get(k) or type(got[k]) is not type(exp.get(k))]


def check_registry() -> List[str]:
    readmap = json.loads(read("config/router-readmap.json"))
    problems: List[str] = []
    if readmap.get("variants", {}).get("ORIENT") != ["standard"]:
        problems.append("read map variants.ORIENT is not ['standard']")
    entries = [e for e in readmap.get("files", []) if e.get("file") == ROUTE_FILE]
    if len(entries) != 1 or entries[0].get("class") != "start" or entries[0].get("workflows") != ["ORIENT"]:
        problems.append(f"read map needs exactly one start entry for {ROUTE_FILE} limited to ORIENT: {entries!r}")
    if not (PLUGIN_ROOT / ROUTE_FILE).is_file():
        problems.append(f"{ROUTE_FILE} is missing")
    return problems


def check_pins() -> List[str]:
    return [f"{rel} lacks the pinned text {pin!r}" for rel, pin in DOC_PINS if pin not in read(rel)]


NEGATION_RE = re.compile(r"\b(?:no|not|never|without|refuse[sd]?|refusing|cannot)\b", re.IGNORECASE)
DISPATCH_SYNTAX = ("craftflow:", "subagent_type", "TaskCreate(", "Task(")  # never allowed, negated or not
DISPATCH_WORDS = re.compile(
    r"\bhand (?:the |this )?(?:request|work|task)\b|\bapply (?:the|a) (?:fix|change|patch)\b"
    r"|\bdispatch(?:es|ed|ing)?\b", re.IGNORECASE)


def dispatch_problems(text: str) -> List[str]:
    """Dispatch syntax, or a plain-English hand-off with no whole-word negation earlier in its sentence."""
    problems = [f"contains {bad!r}" for bad in DISPATCH_SYNTAX if bad in text]
    for sentence in re.split(r"(?<=[.;!?])\s+|\n", text):
        for match in DISPATCH_WORDS.finditer(sentence):
            if not NEGATION_RE.search(sentence[:match.start()]):
                problems.append(f"instructs a hand-off outside a negation: {match.group(0)!r} in {sentence.strip()!r}")
    return problems


def check_route_file_dispatches_nothing() -> List[str]:
    return [f"{ROUTE_FILE} {p}" for p in dispatch_problems(read(ROUTE_FILE))]


def check_cursor() -> List[str]:
    section = section_after(read(CURSOR_ROUTES), "ORIENT")
    if section is None:
        return [f"{CURSOR_ROUTES} has no ORIENT section"]
    problems = []
    if "cursor-wf.json" in section.lower().replace("no `cursor-wf.json`", "").replace("never `cursor-wf.json`", ""):
        problems.append("Cursor ORIENT section mentions cursor-wf.json outside a negation")
    for line in section.splitlines():
        low = line.lower()
        if "`task`" in low and not any(w in low for w in ("no ", "never", "not ", "without")):
            problems.append(f"Cursor ORIENT section uses `Task` outside a negation: {line.strip()!r}")
    return problems


def check_mint_refused() -> List[str]:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / ".craftflow" / "state").mkdir(parents=True)
        env = dict(os.environ, CLAUDE_CODE_SESSION_ID="0123abcd-4567-89ab")
        res = subprocess.run(
            [sys.executable, str(MINT), "--init-artifact", "--project", str(project), "--workflow-type", "ORIENT",
             "--phase", "orient", "--request", "x", "--task-tools-available", "false"],
            capture_output=True, text=True, env=env, cwd=tmp)
        left = sorted(p.name for p in (project / ".craftflow" / "state").iterdir())
    problems = []
    if res.returncode != 2 or "ORIENT creates no artifact" not in res.stderr:
        problems.append(f"ORIENT mint not refused: rc={res.returncode} stderr={res.stderr.strip()!r}")
    if left:
        problems.append(f"ORIENT mint left files behind: {left}")
    return problems


def load_fixture(path: Path) -> Any:
    """The parsed fixture; raises ValueError with a tidy message when it cannot be read or parsed."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot load fixture {path}: {type(exc).__name__}: {exc}") from exc


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Replay check for the ORIENT route.")
    ap.add_argument("--fixture", type=Path, default=None)
    ap.add_argument("--plugin-root", type=Path, default=None,
                    help="check this copy of the plugin tree instead of the one holding this script")
    args = ap.parse_args(argv)
    global PLUGIN_ROOT, MINT
    if args.plugin_root is not None:
        PLUGIN_ROOT = args.plugin_root.resolve()
        MINT = PLUGIN_ROOT / "scripts" / "craftflow_workflow_id.py"
    fixture_path = args.fixture or PLUGIN_ROOT / "tests" / "fixtures" / "orient-inline.json"
    try:
        fixture = load_fixture(fixture_path)
    except ValueError as exc:
        print(f"FAIL: fixture shape: {exc}")
        return 1
    shape = check_fixture(fixture)
    if shape:
        print("FAIL: fixture shape")
        for problem in shape:
            print("  - " + problem)
        print("route replay: stopped, fixture shape is invalid")
        return 1
    results = [
        ("fixture shape", shape),
        ("routing law sends the requests to ORIENT", check_routing(fixture)),
        ("protocol row derives the inline outcome", check_outcome(fixture)),
        ("read map registers the route file", check_registry()),
        ("doc pins", check_pins()),
        ("route file dispatches nothing", check_route_file_dispatches_nothing()),
        ("Cursor binding is read-only", check_cursor()),
        ("ORIENT mint is refused", check_mint_refused()),
    ]
    failed = 0
    for name, problems in results:
        print(("PASS: " if not problems else "FAIL: ") + name)
        for problem in problems:
            print("  - " + problem)
        failed += bool(problems)
    print(f"route replay: {len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
