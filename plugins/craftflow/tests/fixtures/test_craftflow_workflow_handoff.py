#!/usr/bin/env python3
"""H5: `craftflow_workflow_handoff.py` mints linked child workflows (PP-5, PP-6, PP-17).

Run: python3 tests/fixtures/test_craftflow_workflow_handoff.py   (non-zero exit on any FAIL)
Each case builds a temp project holding a parent artifact and log, runs the CLI as a subprocess and asserts the exit
code AND the stderr text. Every path also asserts the parent artifact sha256 is unchanged (PP-6) and, for refusals,
that nothing was written.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
SESSION = "0123abcd-4567-89ab"
os.environ["CLAUDE_CODE_SESSION_ID"] = SESSION
import craftflow_workflow_handoff as hand  # noqa: E402
import craftflow_workflow_id as wid  # noqa: E402

SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_workflow_handoff.py"
NOT_FINAL = "Error: parent workflow is not finalized"
NEEDS_ACCEPT = "Error: write-capable target needs a recorded accept"

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


class Proj:
    """A temp project with one parent workflow."""

    def __init__(self, tmp: str, name: str, ptype: str = "CODEBASE-HEALTH", host: str = "claude-code",
                 events: tuple = ("memory_finalized",), session_ok: bool = True) -> None:
        self.root = Path(tmp) / name
        (self.root / ".craftflow" / "state").mkdir(parents=True)
        self.wf_dir = self.root / ".craftflow" / "state" / "workflows"
        info = wid.mint_workflow_id("Review the health of the router", "", self.root)
        wid.init_artifact(self.root, info, workflow_type=ptype, request="Review the health of the router",
                          phase=ptype.lower(), task_tools_available="false", host=host)
        self.parent = info["workflow_uuid"]
        self.artifact = self.wf_dir / f"{self.parent}.json"
        self.log = self.wf_dir / f"{self.parent}.events.jsonl"
        self.append(*[{"event": e} for e in events])
        (self.root / "d.md").write_text("# design\n", encoding="utf-8")

    def append(self, *events: dict) -> None:
        with self.log.open("a", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps({"ts": "2026-10-30T00:00:00Z", "wf": self.parent, **e}) + "\n")

    def sha(self) -> str:
        return hashlib.sha256(self.artifact.read_bytes()).hexdigest()

    def listing(self) -> list:
        return sorted(p.name for p in self.wf_dir.iterdir())

    def snapshot(self) -> tuple:
        return (self.listing(), self.log.read_bytes())

    def events(self, path: Path = None) -> list:
        path = path or self.log
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def children(self) -> list:
        return sorted(p for p in self.wf_dir.glob("*.json") if p != self.artifact)

    def child(self) -> dict:
        kids = self.children()
        return json.loads(kids[0].read_text(encoding="utf-8")) if len(kids) == 1 else {}

    def run(self, *args: str, session: str = SESSION, parent: str = None):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
        if session:
            env["CLAUDE_CODE_SESSION_ID"] = session
        cmd = [sys.executable, str(SCRIPT), "--project", str(self.root), "--parent", parent or self.parent, *args]
        return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(self.root))


def plan_args(*extra: str) -> tuple:
    return ("--target", "PLAN", "--input", "d.md", "--input-kind", "design", "--source-route", "CODEBASE-HEALTH", *extra)


def line_value(out: str, key: str) -> str:
    for line in out.splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return ""


def test_plan_auto(tmp: str) -> None:
    p = Proj(tmp, "plan")
    before = p.sha()
    res = p.run(*plan_args())
    child_id = line_value(res.stdout, "child_workflow_uuid")
    check("PLAN auto: exit 0", res.returncode == 0, f"rc={res.returncode} {res.stderr}")
    check("PLAN auto: stdout names the child", child_id.startswith("wf-"), res.stdout)
    kid = p.child()
    check("PLAN child carries parent_workflow_uuid", kid.get("parent_workflow_uuid") == p.parent, str(kid.get("parent_workflow_uuid")))
    check("PLAN child handoff block", kid.get("handoff") == {"source_route": "CODEBASE-HEALTH", "source_workflow_uuid": p.parent,
                                                              "input_path": "d.md", "input_kind": "design", "mode": "auto"},
          str(kid.get("handoff")))
    check("PLAN child design_file", kid.get("design_file") == "d.md", str(kid.get("design_file")))
    check("PLAN child is a valid v10 artifact", kid.get("workflow_type") == "PLAN" and kid.get("workflow_uuid") == child_id
          and kid.get("policy", {}).get("sha256") and (p.wf_dir / child_id).is_dir(), str(kid.get("policy")))
    started = [e for e in p.events(p.wf_dir / f"{child_id}.events.jsonl") if e["event"] == "workflow_started"]
    check("PLAN child workflow_started host", [e.get("host") for e in started] == ["claude-code"], str(started))
    parent_h = [e for e in p.events() if e["event"] == "handoff_started"]
    child_h = [e for e in p.events(p.wf_dir / f"{child_id}.events.jsonl") if e["event"] == "handoff_started"]
    check("both logs get handoff_started", len(parent_h) == 1 and len(child_h) == 1
          and parent_h[0].get("child_workflow_uuid") == child_id and child_h[0].get("parent_workflow_uuid") == p.parent,
          f"{parent_h} {child_h}")
    check("PP-6 PLAN: parent sha256 unchanged", p.sha() == before, "")


def test_write_targets(tmp: str) -> None:
    p = Proj(tmp, "build")
    before, snap = p.sha(), p.snapshot()
    res = p.run("--target", "BUILD", "--input", "d.md", "--input-kind", "brief", "--source-route", "TRIAGE")
    check("BUILD without accept: exit 3", res.returncode == 3, f"rc={res.returncode}")
    check("BUILD without accept: stderr", NEEDS_ACCEPT in res.stderr, res.stderr)
    check("BUILD without accept: nothing written", p.snapshot() == snap, "")
    res = p.run("--just-go", "--target", "DEBUG", "--input", "d.md", "--input-kind", "brief", "--source-route", "TRIAGE")
    check("--just-go DEBUG without accept: exit 3", res.returncode == 3 and NEEDS_ACCEPT in res.stderr, f"rc={res.returncode} {res.stderr}")
    res = p.run("--target", "BUILD", "--accepted-answer", "hacc-00000000", "--input", "d.md")
    check("unknown accepted-answer id: exit 3", res.returncode == 3 and NEEDS_ACCEPT in res.stderr, f"rc={res.returncode} {res.stderr}")
    check("refusals wrote nothing", p.snapshot() == snap, "")
    res = p.run("--record-accept", "--just-go", "--target", "BUILD", "--answer", "yes")
    check("--record-accept --just-go: exit 3", res.returncode == 3, f"rc={res.returncode}")
    check("--record-accept --just-go: stderr", "Error: JUST_GO never records an accept" in res.stderr, res.stderr)
    check("--record-accept --just-go: nothing written", p.snapshot() == snap, "")
    res = p.run("--record-accept", "--target", "PLAN", "--answer", "yes")
    check("--record-accept for PLAN: exit 2", res.returncode == 2, f"rc={res.returncode} {res.stderr}")
    res = p.run("--record-accept", "--target", "BUILD")
    check("--record-accept without --answer: exit 2", res.returncode == 2 and "Error: --record-accept needs --answer" in res.stderr,
          f"rc={res.returncode} {res.stderr}")
    res = p.run("--record-accept", "--target", "BUILD", "--answer", "yes, go")
    acc = line_value(res.stdout, "accepted_event_id")
    check("--record-accept: exit 0 and prints the id", res.returncode == 0 and acc != "", f"rc={res.returncode} {res.stdout} {res.stderr}")
    ev = [e for e in p.events() if e["event"] == "handoff_accepted"]
    check("handoff_accepted fields", len(ev) == 1 and ev[0].get("event_id") == acc and ev[0].get("target") == "BUILD"
          and ev[0].get("answer") == "yes, go", str(ev))
    again = p.run("--record-accept", "--target", "BUILD", "--answer", "yes again")
    check("second --record-accept reports the existing id", again.returncode == 0 and line_value(again.stdout, "accepted_event_id") == acc
          and len([e for e in p.events() if e["event"] == "handoff_accepted"]) == 1, f"{again.stdout} {again.stderr}")
    res = p.run("--target", "DEBUG", "--accepted-answer", acc, "--input", "d.md")
    check("accept for BUILD does not satisfy DEBUG", res.returncode == 3 and NEEDS_ACCEPT in res.stderr, f"rc={res.returncode} {res.stderr}")
    res = p.run("--just-go", "--target", "BUILD", "--accepted-answer", acc, "--input", "d.md", "--input-kind", "brief",
                "--source-route", "TRIAGE")
    kid = p.child()
    check("BUILD with a matching accept (even under --just-go): exit 0, mode asked",
          res.returncode == 0 and kid.get("handoff", {}).get("mode") == "asked" and kid.get("workflow_type") == "BUILD",
          f"rc={res.returncode} {res.stderr} {kid.get('handoff')}")
    check("PP-6 BUILD paths: parent sha256 unchanged", p.sha() == before, "")


def test_preconditions(tmp: str) -> None:
    p = Proj(tmp, "notfinal", events=())
    before, snap = p.sha(), p.snapshot()
    res = p.run(*plan_args())
    check("parent not finalized: exit 4", res.returncode == 4 and NOT_FINAL in res.stderr, f"rc={res.returncode} {res.stderr}")
    check("parent not finalized: nothing written", p.snapshot() == snap and p.sha() == before, "")
    p = Proj(tmp, "noinput")
    snap = p.snapshot()
    res = p.run("--target", "PLAN", "--input", "missing.md", "--input-kind", "design", "--source-route", "CODEBASE-HEALTH")
    check("input missing: exit 4", res.returncode == 4 and "Error: handoff input not found: missing.md" in res.stderr,
          f"rc={res.returncode} {res.stderr}")
    check("input missing: nothing written", p.snapshot() == snap, "")
    res = p.run(*plan_args(), parent="wf-nope-20260101-000000-deadbeef")
    check("unknown parent: exit 4", res.returncode == 4 and "Error: parent workflow not found" in res.stderr, f"rc={res.returncode} {res.stderr}")
    res = p.run(*plan_args(), parent="../x")
    check("path-like parent id: exit 2", res.returncode == 2 and "Error: invalid --parent" in res.stderr, f"rc={res.returncode} {res.stderr}")
    c = Proj(tmp, "cursor-parent", host="cursor", events=("workflow_completed",))
    res = c.run(*plan_args("--host", "cursor"))
    check("Cursor parent with workflow_completed: exit 0", res.returncode == 0, f"rc={res.returncode} {res.stderr}")
    started = [e for e in c.events(c.wf_dir / f"{line_value(res.stdout, 'child_workflow_uuid')}.events.jsonl") if e["event"] == "workflow_started"]
    check("--host cursor: child workflow_started host", [e.get("host") for e in started] == ["cursor"], str(started))
    k = Proj(tmp, "claude-completed", events=("workflow_completed",))
    res = k.run(*plan_args())
    check("Claude parent with only workflow_completed: exit 4", res.returncode == 4 and NOT_FINAL in res.stderr, f"rc={res.returncode} {res.stderr}")


def test_idempotence_and_failure(tmp: str) -> None:
    p = Proj(tmp, "twice")
    first = line_value(p.run(*plan_args()).stdout, "child_workflow_uuid")
    snap, before = p.snapshot(), p.sha()
    res = p.run(*plan_args())
    check("second handoff: exit 0 already_handed_off", res.returncode == 0 and line_value(res.stdout, "already_handed_off") == first,
          f"rc={res.returncode} {res.stdout} {res.stderr}")
    check("second handoff writes nothing", p.snapshot() == snap and p.sha() == before, "")
    # crash between the mint and the parent log line: the child artifact alone still answers.
    p.log.write_text("".join(json.dumps(e) + "\n" for e in p.events() if e["event"] != "handoff_started"), encoding="utf-8")
    snap = p.snapshot()
    res = p.run(*plan_args())
    check("crash recovery: child found without the parent log line", res.returncode == 0 and line_value(res.stdout, "already_handed_off") == first
          and p.snapshot() == snap, f"rc={res.returncode} {res.stdout} {res.stderr}")
    if os.geteuid() == 0:
        print("  SKIP: read-only workflows dir (running as root)")
        return
    ro = Proj(tmp, "readonly")
    snap, before = ro.listing(), ro.sha()
    ro.wf_dir.chmod(0o555)
    try:
        res = ro.run(*plan_args())
    finally:
        ro.wf_dir.chmod(0o755)
    check("read-only workflows dir: exit 5", res.returncode == 5 and res.stderr.startswith("Error: handoff mint failed: Permission denied"), f"rc={res.returncode} {res.stderr}")
    failed = [e for e in ro.events() if e["event"] == "handoff_failed"]
    check("read-only workflows dir: handoff_failed in the parent log", len(failed) == 1 and failed[0].get("reason"), str(failed))
    check("read-only workflows dir: no child left behind", ro.listing() == snap and ro.sha() == before, str(ro.listing()))


def qa_source(p: Proj) -> str:
    """A finished QA workflow whose artifact carries Denied reads and a negotiation, to copy from."""
    info = wid.mint_workflow_id("QA the router", "", p.root)
    wid.init_artifact(p.root, info, workflow_type="QA", request="QA the router", phase="qa", task_tools_available="false")
    path = p.wf_dir / f"{info['workflow_uuid']}.json"
    art = json.loads(path.read_text(encoding="utf-8"))
    art["intent"]["constraints"] = ["keep it small", "Denied reads: secrets/ (fixture)"]
    art["qa"] = {"negotiation": {"grants": {"writes_outside_allowlist": "granted"}}, "bug_candidates": []}
    path.write_text(json.dumps(art), encoding="utf-8")
    return info["workflow_uuid"]


def test_qa_child(tmp: str) -> None:
    p = Proj(tmp, "qa", ptype="DEBUG")
    src = qa_source(p)
    args = ("--target", "QA", "--source-route", "DEBUG", "--source-qa-wf", src)
    before = p.sha()
    res = p.run(*args)
    kids = [k for k in p.children() if k.stem != src]
    kid = json.loads(kids[0].read_text(encoding="utf-8")) if kids else {}
    check("QA re-run with a session: exit 0", res.returncode == 0 and len(kids) == 1, f"rc={res.returncode} {res.stderr}")
    check("QA child: source_wf and session_id", kid.get("source_wf") == src and kid.get("session_id") == SESSION, f"{kid.get('source_wf')} {kid.get('session_id')}")
    check("QA child: copied Denied reads", "Denied reads: secrets/ (fixture)" in kid.get("intent", {}).get("constraints", []), str(kid.get("intent")))
    check("QA child: copied qa.negotiation", kid.get("qa", {}).get("negotiation") == {"grants": {"writes_outside_allowlist": "granted"}}, str(kid.get("qa")))
    check("QA child: mode auto", kid.get("handoff", {}).get("mode") == "auto", str(kid.get("handoff")))
    check("PP-6 QA: parent sha256 unchanged", p.sha() == before, "")
    q = Proj(tmp, "qa-nosession", ptype="DEBUG")
    src = qa_source(q)
    snap = q.snapshot()
    res = q.run("--target", "QA", "--source-route", "DEBUG", "--source-qa-wf", src, session="")
    check("PP-17 QA child without a session on claude: exit 2", res.returncode == 2 and "Error: QA workflow needs a session id" in res.stderr,
          f"rc={res.returncode} {res.stderr}")
    check("QA child without a session: nothing written", q.snapshot() == snap, "")
    c = Proj(tmp, "qa-cursor", ptype="DEBUG", host="cursor", events=("workflow_completed",))
    src = qa_source(c)
    res = c.run("--target", "QA", "--source-route", "DEBUG", "--source-qa-wf", src, "--host", "cursor", session="")
    q = c
    kids = [k for k in q.children() if k.stem != src]
    kid = json.loads(kids[0].read_text(encoding="utf-8")) if kids else {}
    child_id = line_value(res.stdout, "child_workflow_uuid")
    started = [e for e in q.events(q.wf_dir / f"{child_id}.events.jsonl") if e["event"] == "workflow_started"]
    check("QA child of a Cursor parent with --host cursor and no session: minted with session_id null",
          res.returncode == 0 and kid.get("session_id") is None and [e.get("host") for e in started] == ["cursor"],
          f"rc={res.returncode} {res.stderr} {kid.get('session_id')} {started}")
    m = Proj(tmp, "qa-nosource", ptype="DEBUG")
    res = m.run("--target", "QA", "--source-route", "DEBUG", "--source-qa-wf", "wf-nope-20260101-000000-deadbeef")
    check("QA child with a missing source QA workflow: exit 4", res.returncode == 4 and "Error: source QA workflow not found" in res.stderr,
          f"rc={res.returncode} {res.stderr}")


def test_decline(tmp: str) -> None:
    p = Proj(tmp, "decline")
    before = p.sha()
    res = p.run("--record-decline", "--reason", "non_interactive", "--detail", "tool_error", "--target", "BUILD")
    ev = [e for e in p.events() if e["event"] == "handoff_declined"]
    check("non_interactive decline: exit 0 with reason and detail", res.returncode == 0 and len(ev) == 1
          and ev[0].get("reason") == "non_interactive" and ev[0].get("detail") == "tool_error", f"rc={res.returncode} {res.stderr} {ev}")
    for detail in ("tool_unavailable", "no_answer"):
        res = p.run("--record-decline", "--reason", "non_interactive", "--detail", detail)
        check(f"detail {detail} accepted", res.returncode == 0, res.stderr)
    snap = p.snapshot()
    for extra, why, text in ((("--reason", "non_interactive", "--detail", "bogus"), "bogus detail", "Error: invalid --detail 'bogus'"),
                             (("--reason", "non_interactive"), "non_interactive without detail", "Error: invalid --detail None"),
                             (("--reason", "user_declined", "--detail", "tool_error"), "user_declined with detail",
                              "Error: invalid --detail: user_declined takes none"),
                             (("--reason", "whatever"), "unknown reason", "Error: invalid --reason 'whatever'")):
        res = p.run("--record-decline", *extra)
        check(f"decline rejected: {why}: exit 2", res.returncode == 2 and text in res.stderr, f"rc={res.returncode} {res.stderr}")
    check("rejected declines wrote nothing", p.snapshot() == snap, "")
    res = p.run("--record-decline", "--reason", "user_declined")
    last = p.events()[-1]
    check("user_declined: appended without detail", res.returncode == 0 and last.get("event") == "handoff_declined"
          and last.get("reason") == "user_declined" and "detail" not in last, f"rc={res.returncode} {last}")
    check("PP-6 decline: parent sha256 unchanged", p.sha() == before, "")


def test_plan_handoff_pure() -> None:
    art = {"workflow_type": "CODEBASE-HEALTH"}
    done = [{"event": "memory_finalized"}]
    acc = [{"event": "handoff_accepted", "event_id": "hacc-1", "target": "BUILD"}]
    check("pure: PLAN mints", hand.plan_handoff(art, done, "PLAN", None, False)[0] == "mint", "")
    check("pure: unfinalized is error 4", hand.plan_handoff(art, [], "PLAN", None, False)[:2] == ("error", 4), "")
    check("pure: BUILD without accept is error 3", hand.plan_handoff(art, done, "BUILD", None, False)[:2] == ("error", 3), "")
    check("pure: BUILD just_go without accept is error 3", hand.plan_handoff(art, done, "BUILD", None, True)[:2] == ("error", 3), "")
    check("pure: BUILD with accept mints as asked", hand.plan_handoff(art, done + acc, "BUILD", "hacc-1", True)[:2] == ("mint", "asked"), "")
    check("pure: DEBUG with a BUILD accept is error 3", hand.plan_handoff(art, done + acc, "DEBUG", "hacc-1", False)[:2] == ("error", 3), "")
    started = [{"event": "handoff_started", "child_workflow_uuid": "wf-x"}]
    check("pure: existing handoff is already", hand.plan_handoff(art, done + started, "PLAN", None, False)[:2] == ("already", "wf-x"), "")
    check("pure: finalized checked before already", hand.plan_handoff(art, started, "PLAN", None, False)[:2] == ("error", 4), "")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        test_plan_auto(tmp)
        test_write_targets(tmp)
        test_preconditions(tmp)
        test_idempotence_and_failure(tmp)
        test_qa_child(tmp)
        test_decline(tmp)
    test_plan_handoff_pure()
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
