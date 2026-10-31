#!/usr/bin/env python3
"""H3: the new workflow types mint (or are refused) correctly, and the harvest hygiene checks hold.

Run: python3 tests/fixtures/test_craftflow_route_types.py   (non-zero exit on any FAIL)
Covers: QA/TRIAGE/CODEBASE-HEALTH mint, ORIENT refusal, QA without a session id fails closed with
nothing left behind, corpus heading uniqueness, `_clean_type`, the per-file size cap of the new
route files, and the DD-12 no-new-attribution-string grep with its control.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import craftflow_context_nudge_compact as nudge  # noqa: E402
import craftflow_router_corpus  # noqa: E402
import router_relocation_drills as drills  # noqa: E402

SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_workflow_id.py"
SESSION = "0123abcd-4567-89ab"
FILE_CAP = 18_500
NAME = "cc" + "10x"  # spelled in two parts so this file carries no hit itself
ATTRIBUTION_LINE = f"<!-- Adapted from {NAME} (MIT) @36c7b06 -->"
NEEDLE = NAME
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
# DD-12: files that already carried the string on origin/main, pinned to their count.
PREEXISTING_HITS = {
    "skills/craftflow-router/SKILL.md": 1,
    "skills/craftflow-router/references/memory-finalize.md": 1,
    "skills/craftflow-router/references/post-agent-validation.md": 1,
    "scripts/craftflow_memory_repair.py": 5,
    "scripts/craftflow_context_migration.py": 8,
    "scripts/craftflow_runtime_benchmark.py": 3,
    "scripts/craftflow_worldclass_benchmark.py": 7,
    "tests/fixtures/test_memory_repair.py": 6,
    "tests/fixtures/router-rule-inventory.baseline.json": 4,
    "tests/fixtures/router-cut-plan.json": 2,
    "tests/fixtures/router-normative-review.doubt-verifier.json": 1,
    "tests/fixtures/router-nonnormative-report.pre-review.baseline.md": 1,
    "tests/fixtures/router-normative-allowlist.json": 1,
    # Added after cf0cb12a by the release-gate work (two commit-subject fixtures); pinned the same way.
    "tests/fixtures/test_version_bump.py": 2,
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


def mint(project: Path, wf_type: str, session: str | None):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    if session is not None:
        env["CLAUDE_CODE_SESSION_ID"] = session
    cmd = [sys.executable, str(SCRIPT), "--emit-env", "--init-artifact", "--project", str(project),
           "--workflow-type", wf_type, "--phase", wf_type.lower(), "--request", "x",
           "--task-tools-available", "false"]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(project))


def new_project(tmp: str, name: str) -> Path:
    project = Path(tmp) / name
    (project / ".craftflow" / "state").mkdir(parents=True)
    return project


def leftovers(project: Path) -> list:
    wf_dir = project / ".craftflow" / "state" / "workflows"
    return sorted(p.name for p in wf_dir.iterdir()) if wf_dir.is_dir() else []


def artifact_of(project: Path) -> dict:
    files = [p for p in (project / ".craftflow" / "state" / "workflows").glob("*.json")]
    return json.loads(files[0].read_text()) if len(files) == 1 else {}


def test_mints(tmp: str) -> None:
    for wf_type in ("QA", "TRIAGE", "CODEBASE-HEALTH"):
        project = new_project(tmp, "mint-" + wf_type)
        res = mint(project, wf_type, SESSION)
        art = artifact_of(project)
        check(f"{wf_type} mints", res.returncode == 0, res.stderr)
        check(f"{wf_type} artifact type", art.get("workflow_type") == wf_type, str(art.get("workflow_type")))
        stamp = art.get("telemetry", {}).get("router_readset_bytes")
        check(f"{wf_type} readset stamp is int", isinstance(stamp, int) and not isinstance(stamp, bool), repr(stamp))
        if wf_type == "QA":
            check("QA artifact carries the session id", art.get("session_id") == SESSION, str(art.get("session_id")))
    project = new_project(tmp, "orient")
    res = mint(project, "ORIENT", SESSION)
    check("ORIENT --init-artifact exits 2", res.returncode == 2, f"rc={res.returncode}")
    check("ORIENT refusal message", "ORIENT creates no artifact" in res.stderr, res.stderr)
    check("ORIENT leaves nothing behind", leftovers(project) == [], str(leftovers(project)))


def test_qa_needs_session(tmp: str) -> None:
    for label, session in (("unset", None), ("invalid", "x")):
        project = new_project(tmp, "qa-" + label)
        res = mint(project, "QA", session)
        check(f"QA {label} session exits 2", res.returncode == 2, f"rc={res.returncode} {res.stderr}")
        check(f"QA {label} session message", "Error: QA workflow needs a session id" in res.stderr, res.stderr)
        check(f"QA {label} session leaves nothing", leftovers(project) == [], str(leftovers(project)))
    project = new_project(tmp, "triage-nosession")
    res = mint(project, "TRIAGE", None)
    check("TRIAGE without a session still mints", res.returncode == 0 and len(leftovers(project)) == 3,
          f"rc={res.returncode} {res.stderr} {leftovers(project)}")


def test_headings_and_type() -> None:
    corpus = craftflow_router_corpus.read_corpus(PLUGIN_ROOT)
    problems = drills.heading_problems(corpus)
    check("corpus headings unique", problems == [], str(problems)[:300])
    check("_clean_type accepts QA", nudge._clean_type("QA") == "QA", str(nudge._clean_type("QA")))
    for wf_type in ("ORIENT", "TRIAGE", "CODEBASE-HEALTH"):
        check(f"_clean_type accepts {wf_type}", nudge._clean_type(wf_type.lower()) == wf_type, wf_type)


def test_sizes() -> None:
    files = sorted((PLUGIN_ROOT / "skills" / "craftflow-router" / "references").glob("qa-*.md"))
    files += sorted((PLUGIN_ROOT / "skills" / "_shared").glob("route-*.md"))
    check("route files found", len(files) >= 5, str(len(files)))
    for path in files:
        size = path.stat().st_size
        check(f"{path.name} <= {FILE_CAP} B", size <= FILE_CAP, str(size))


def attribution_hits(root: Path) -> dict:
    """{relative path: count of case-insensitive hits outside exact attribution-comment lines}."""
    found: dict = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            count = sum(line.lower().count(NEEDLE) for line in text.splitlines() if line.strip() != ATTRIBUTION_LINE)
            if count:
                found[path.relative_to(root).as_posix()] = count
    return found


def attribution_violations(found: dict) -> list:
    return sorted(p for p, n in found.items() if p not in PREEXISTING_HITS or n > PREEXISTING_HITS[p])


def test_attribution(tmp: str) -> None:
    violations = attribution_violations(attribution_hits(PLUGIN_ROOT))
    check("no new attribution-name string under the plugin", violations == [], str(violations))
    ctrl = Path(tmp) / "ctrl"
    ctrl.mkdir()
    (ctrl / "adapted.md").write_text("a line\n" + ATTRIBUTION_LINE + "\nplain\n", encoding="utf-8")
    check("control: attribution comment alone passes", attribution_violations(attribution_hits(ctrl)) == [], "")
    (ctrl / "adapted.md").write_text("a line\n" + ATTRIBUTION_LINE + "\nsee " + NAME.upper() + " docs\n", encoding="utf-8")
    check("control: one extra string fails", attribution_violations(attribution_hits(ctrl)) == ["adapted.md"], "")


def classify_guard(rc: int, out: str, err: str) -> str:
    """'allow' only for the guard's explicit allow output (rc 0, empty stdout, empty stderr); 'deny' for a parsed deny decision;
    anything else (non-zero exit, stray output, an unparsable or non-deny JSON body) is reported, never read as an allow."""
    if rc != 0:
        return f"error rc={rc} {err[-200:]}"
    if not out.strip() and not err.strip():
        return "allow"
    try:
        body = json.loads(out)
    except ValueError:
        return f"unexpected output {out[:120]!r} {err[:80]!r}"
    decision = (body.get("hookSpecificOutput") or {}).get("permissionDecision") if isinstance(body, dict) else None
    return "deny" if decision == "deny" else f"unexpected output {out[:120]!r} {err[:80]!r}"


def guard_decision(project: Path, target: str) -> str:
    """'allow' or 'deny' from the real PreToolUse write guard for a subagent Write of `target` (payload shape per hooks.json)."""
    payload = {"tool_name": "Write", "cwd": str(project), "session_id": SESSION, "agent_id": "agent-scanner-1",
               "agent_type": "craftflow:architecture-scanner", "tool_input": {"file_path": target, "content": "<html></html>"}}
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(PLUGIN_ROOT), CLAUDE_PROJECT_DIR=str(project))
    res = subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "craftflow_pretooluse_guard.py")], input=json.dumps(payload),
                         capture_output=True, text=True, env=env, cwd=str(project), timeout=60)
    return classify_guard(res.returncode, res.stdout, res.stderr)


def test_report_write_guard(tmp: str) -> None:
    """H6: the scanner's only Write (its report under .craftflow/codebase-health/) passes the write guard; a /tmp Write does not."""
    project = new_project(tmp, "guard")
    report = project / ".craftflow" / "codebase-health" / "x" / "report.html"
    check("a subagent Write to .craftflow/codebase-health/x/report.html is allowed", guard_decision(project, str(report)) == "allow",
          guard_decision(project, str(report)))
    check("a subagent Write to /tmp/x is denied", guard_decision(project, "/tmp/x") == "deny", guard_decision(project, "/tmp/x"))
    outside = Path(tmp) / "elsewhere" / "report.html"
    deny_body = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "x"}})
    others = ((1, "", ""), (0, "{}", ""), (0, "", "warning"), (0, "garbage", ""), (0, '{"permissionDecision": "ask"}', ""))
    check("control: classify_guard reads only empty output as allow, a parsed deny as deny, everything else as neither",
          (classify_guard(0, "", ""), classify_guard(0, deny_body, "")) == ("allow", "deny")
          and all(classify_guard(rc, out, err) not in ("allow", "deny") for rc, out, err in others), "")
    check("a subagent Write to a report path outside the project is denied", guard_decision(project, str(outside)) == "deny",
          guard_decision(project, str(outside)))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        test_mints(tmp)
        test_qa_needs_session(tmp)
        test_attribution(tmp)
        test_report_write_guard(tmp)
    test_headings_and_type()
    test_sizes()
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
