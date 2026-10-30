#!/usr/bin/env python3
"""H5 remfix-5: the handoff mint is race-safe, derives its host from the parent and validates its inputs.

Run: python3 tests/fixtures/test_craftflow_workflow_handoff_2.py   (non-zero exit on any FAIL)
Shares the `Proj` temp-project helper of test_craftflow_workflow_handoff.py.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_craftflow_workflow_handoff as base  # noqa: E402
from test_craftflow_workflow_handoff import SCRIPT, SESSION, NOT_FINAL, Proj, line_value, plan_args  # noqa: E402

hand, wid = base.hand, base.wid
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


def parallel(p: Proj, n: int, *args: str) -> list:
    """`n` handoff CLI processes started together; returns their (rc, stdout, stderr)."""
    env = dict(os.environ, CLAUDE_CODE_SESSION_ID=SESSION)
    cmd = [sys.executable, str(SCRIPT), "--project", str(p.root), "--parent", p.parent, *args]
    procs = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=str(p.root))
             for _ in range(n)]
    done = []
    for q in procs:
        out, err = q.communicate()
        done.append((q.returncode, out, err))
    return done


def test_race(tmp: str) -> None:
    for n in (2, 6):
        p = Proj(tmp, f"race{n}")
        res = parallel(p, n, *plan_args())
        minted = [r for r in res if line_value(r[1], "child_workflow_uuid")]
        already = [r for r in res if line_value(r[1], "already_handed_off")]
        child = line_value(minted[0][1], "child_workflow_uuid") if minted else ""
        check(f"race x{n}: every process exits 0", all(r[0] == 0 for r in res), str([(r[0], r[2]) for r in res]))
        check(f"race x{n}: exactly one child_workflow_uuid", len(minted) == 1 and len(already) == n - 1, str([r[1] for r in res]))
        check(f"race x{n}: the others report the same child", {line_value(r[1], 'already_handed_off') for r in already} == {child}, str(res))
        check(f"race x{n}: one child on disk and one handoff_started", [k.stem for k in p.children()] == [child]
              and len([e for e in p.events() if e["event"] == "handoff_started"]) == 1, str(p.listing()))
    lock = p.wf_dir / f"{p.parent}.handoff.lock"
    held = json.loads(lock.read_text(encoding="utf-8")) if lock.exists() else {}
    check("the claim file is kept after a mint, with pid, ts and child", isinstance(held.get("pid"), int) and held.get("ts")
          and held.get("child") == child, str(held))


def test_lock_states(tmp: str) -> None:
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    p = Proj(tmp, "deadlock")
    lock = p.wf_dir / f"{p.parent}.handoff.lock"
    lock.write_text(json.dumps({"pid": dead.pid, "ts": "2026-10-30T00:00:00Z"}), encoding="utf-8")
    res = p.run(*plan_args())
    check("a claim whose process is gone is replaced", res.returncode == 0 and line_value(res.stdout, "child_workflow_uuid"), f"{res.returncode} {res.stderr}")
    q = Proj(tmp, "stalelock")
    lock = q.wf_dir / f"{q.parent}.handoff.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "ts": "2026-10-30T00:00:00Z"}), encoding="utf-8")
    old = time.time() - 600
    os.utime(str(lock), (old, old))
    res = q.run(*plan_args())
    check("a claim older than 120 s with no child is stale and replaced", res.returncode == 0 and line_value(res.stdout, "child_workflow_uuid"), f"{res.returncode} {res.stderr}")
    r = Proj(tmp, "refused", events=())
    snap = r.snapshot()
    res = r.run(*plan_args())
    check("a refusal removes the claim it took", res.returncode == 4 and r.snapshot() == snap, str(r.listing()))


def test_host_derivation(tmp: str) -> None:
    p = Proj(tmp, "claude-bypass", ptype="DEBUG")
    src = base.qa_source(p)
    snap, before = p.snapshot(), p.sha()
    res = p.run("--target", "QA", "--source-route", "DEBUG", "--source-qa-wf", src, "--host", "cursor", session="")
    check("claude parent + --host cursor + QA without a session: exit 2 with the host message",
          res.returncode == 2 and "Error: --host cursor needs a Cursor parent (workflow_started host)" in res.stderr, f"rc={res.returncode} {res.stderr}")
    check("the self-declared cursor host wrote nothing", p.snapshot() == snap and p.sha() == before, str(p.listing()))
    res = p.run(*plan_args("--host", "cursor"))
    check("claude parent + --host cursor on PLAN is refused too", res.returncode == 2 and "needs a Cursor parent" in res.stderr, f"rc={res.returncode} {res.stderr}")
    c = Proj(tmp, "cursor-derived", ptype="DEBUG", host="cursor", events=("workflow_completed",))
    src = base.qa_source(c)
    res = c.run("--target", "QA", "--source-route", "DEBUG", "--source-qa-wf", src, "--host", "cursor", session="")
    kid = [json.loads(k.read_text(encoding="utf-8")) for k in c.children() if k.stem != src]
    check("cursor parent + --host cursor: QA child with session_id null", res.returncode == 0 and len(kid) == 1 and kid[0].get("session_id") is None, f"rc={res.returncode} {res.stderr}")
    d = Proj(tmp, "cursor-noflag", host="cursor", events=("workflow_completed",))
    res = d.run(*plan_args())
    kid = d.child()
    check("a Cursor parent without the flag still mints with host cursor", res.returncode == 0 and kid, f"rc={res.returncode} {res.stderr}")


def test_finalized_restart(tmp: str) -> None:
    p = Proj(tmp, "restart", events=("memory_finalized", "workflow_started"))
    snap = p.snapshot()
    res = p.run(*plan_args())
    check("a workflow_started after memory_finalized is not finalized: exit 4", res.returncode == 4 and NOT_FINAL in res.stderr, f"rc={res.returncode} {res.stderr}")
    check("restarted parent: nothing written", p.snapshot() == snap, "")
    q = Proj(tmp, "cursor-restart", host="cursor", events=("workflow_completed", "workflow_started"))
    res = q.run(*plan_args())
    check("Cursor: a workflow_started after workflow_completed is not finalized", res.returncode == 4 and NOT_FINAL in res.stderr, f"rc={res.returncode} {res.stderr}")
    r = Proj(tmp, "refinalized", events=("memory_finalized", "workflow_started", "memory_finalized"))
    check("finalized again after a restart mints", r.run(*plan_args()).returncode == 0, "")


def test_input(tmp: str) -> None:
    msg = "Error: input must be a regular file inside the project"
    p = Proj(tmp, "inputs")
    (p.root / "sub").mkdir()
    os.symlink(str(p.root / "d.md"), str(p.root / "link.md"))
    outside = Path(tmp) / "outside.md"
    outside.write_text("# elsewhere\n", encoding="utf-8")
    snap = p.snapshot()
    for label, value in (("directory", "sub"), ("symlink", "link.md"), ("absolute outside path", str(outside)), ("dot-dot escape", "../outside.md")):
        res = p.run("--target", "PLAN", "--input", value, "--input-kind", "design", "--source-route", "CODEBASE-HEALTH")
        check(f"--input {label}: exit 4", res.returncode == 4 and msg in res.stderr, f"rc={res.returncode} {res.stderr}")
    check("refused inputs wrote nothing", p.snapshot() == snap, str(p.listing()))
    res = p.run("--target", "PLAN", "--input", str(p.root / "d.md"), "--input-kind", "design", "--source-route", "CODEBASE-HEALTH")
    check("an absolute path inside the project is stored project-relative", res.returncode == 0 and p.child().get("handoff", {}).get("input_path") == "d.md"
          and p.child().get("design_file") == "d.md", f"rc={res.returncode} {res.stderr} {p.child().get('handoff')}")


def test_accept_per_target(tmp: str) -> None:
    p = Proj(tmp, "accepts")
    first = line_value(p.run("--record-accept", "--target", "BUILD", "--answer", "yes").stdout, "accepted_event_id")
    second = line_value(p.run("--record-accept", "--target", "DEBUG", "--answer", "yes debug").stdout, "accepted_event_id")
    ev = [e for e in p.events() if e["event"] == "handoff_accepted"]
    check("accept BUILD then DEBUG appends two events with two ids", first and second and first != second
          and [(e["event_id"], e["target"]) for e in ev] == [(first, "BUILD"), (second, "DEBUG")], str(ev))
    repeat = line_value(p.run("--record-accept", "--target", "DEBUG", "--answer", "again").stdout, "accepted_event_id")
    check("repeating the same target stays idempotent", repeat == second and len([e for e in p.events() if e["event"] == "handoff_accepted"]) == 2, repeat)
    res = p.run("--target", "DEBUG", "--accepted-answer", second, "--input", "d.md", "--input-kind", "brief", "--source-route", "TRIAGE")
    check("DEBUG mint with the DEBUG id: exit 0", res.returncode == 0 and p.child().get("workflow_type") == "DEBUG", f"rc={res.returncode} {res.stderr}")


def test_parent_type(tmp: str) -> None:
    for ptype, args, label in (("BUILD", plan_args(), "BUILD parent"), ("PLAN", plan_args(), "PLAN parent"), ("DEBUG", plan_args(), "DEBUG parent to PLAN")):
        p = Proj(tmp, "type-" + label.split()[0] + str(len(label)), ptype=ptype)
        snap = p.snapshot()
        res = p.run(*args)
        check(f"{label} cannot hand off: exit 4", res.returncode == 4 and f"Error: parent workflow type {ptype} cannot hand off" in res.stderr, f"rc={res.returncode} {res.stderr}")
        check(f"{label}: nothing written", p.snapshot() == snap, "")
    q = Proj(tmp, "notqa", ptype="DEBUG")
    snap = q.snapshot()
    res = q.run("--target", "QA", "--source-route", "DEBUG", "--source-qa-wf", q.parent)
    check("--source-qa-wf must name a QA artifact: exit 4", res.returncode == 4 and "Error: source workflow is not a QA workflow" in res.stderr, f"rc={res.returncode} {res.stderr}")
    check("non-QA source: nothing written", q.snapshot() == snap, "")


def test_flag_values(tmp: str) -> None:
    p = Proj(tmp, "flags")
    snap = p.snapshot()
    for extra, text in ((("--input-kind", "bogus"), "invalid --input-kind"), (("--source-route", "BUILD"), "invalid --source-route")):
        res = p.run("--target", "PLAN", "--input", "d.md", *extra)
        check(f"{extra[0]} {extra[1]}: exit 2", res.returncode == 2 and f"Error: {text}" in res.stderr, f"rc={res.returncode} {res.stderr}")
    check("bad flag values wrote nothing", p.snapshot() == snap, "")


def run_inproc(p: Proj, *args: str):
    err, out = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        rc = hand.main(["--project", str(p.root), "--parent", p.parent, *args])
    return rc, out.getvalue(), err.getvalue()


def test_orphans(tmp: str) -> None:
    p = Proj(tmp, "orphan")
    snap, seen = p.listing(), {}
    real = hand.patch_child

    def boom(path, *a, **kw):
        seen["art"] = json.loads(Path(path).read_text(encoding="utf-8"))
        raise AttributeError("patch exploded")

    hand.patch_child = boom
    try:
        rc, out, err = run_inproc(p, *plan_args())
    finally:
        hand.patch_child = real
    check("a patch_child AttributeError maps to exit 5", rc == 5 and err.startswith("Error:") and "child_workflow_uuid" not in out, f"{rc} {err}")
    check("the child artifact carried parent_workflow_uuid from creation", seen.get("art", {}).get("parent_workflow_uuid") == p.parent, str(seen.get("art", {}).get("parent_workflow_uuid")))
    check("the rollback left no child and no claim", p.listing() == snap, str(p.listing()))
    check("handoff_failed is logged", len([e for e in p.events() if e["event"] == "handoff_failed"]) == 1, str(p.events()))
    child = "wf-fake-20260101-000000-deadbeef"
    for suffix in (".json", ".json.tmp", ".events.jsonl"):
        (p.wf_dir / (child + suffix)).write_text("{}", encoding="utf-8")
    hand.remove_child(p.wf_dir, child)
    check("remove_child also removes the write_atomic tmp file", p.listing() == snap, str(p.listing()))
    err = io.StringIO()
    (p.wf_dir / child).mkdir()
    (p.wf_dir / child / "keep").write_text("x", encoding="utf-8")
    with contextlib.redirect_stderr(err):
        hand.remove_child(p.wf_dir, child)
    check("remove_child reports an OSError it suppresses", "Warning:" in err.getvalue() and child in err.getvalue(), err.getvalue())


def test_declined_blocks(tmp: str) -> None:
    gone = "Error: handoff was declined; never re-offered"
    brief = ("--input", "d.md", "--input-kind", "brief", "--source-route", "TRIAGE")
    p = Proj(tmp, "declined")
    first = line_value(p.run("--record-accept", "--target", "BUILD", "--answer", "yes").stdout, "accepted_event_id")
    p.run("--record-decline", "--reason", "user_declined", "--target", "BUILD")
    snap = p.snapshot()
    res = p.run("--target", "BUILD", "--accepted-answer", first, *brief)
    check("a decline after the accept blocks the mint: exit 3", res.returncode == 3 and gone in res.stderr, f"rc={res.returncode} {res.stderr}")
    check("the declined mint wrote nothing", p.snapshot() == snap, "")
    later = line_value(p.run("--record-accept", "--target", "BUILD", "--answer", "yes, really").stdout, "accepted_event_id")
    check("an accept after the decline records a fresh id", later and later != first, later)
    res = p.run("--target", "BUILD", "--accepted-answer", later, *brief)
    check("a later accept lifts the decline: exit 0", res.returncode == 0 and p.child().get("workflow_type") == "BUILD", f"rc={res.returncode} {res.stderr}")
    q = Proj(tmp, "declined-other")
    acc = line_value(q.run("--record-accept", "--target", "BUILD", "--answer", "yes").stdout, "accepted_event_id")
    q.run("--record-decline", "--reason", "user_declined", "--target", "DEBUG")
    res = q.run("--target", "BUILD", "--accepted-answer", acc, *brief)
    check("a decline of another target does not block", res.returncode == 0, f"rc={res.returncode} {res.stderr}")
    r = Proj(tmp, "declined-untargeted")
    acc = line_value(r.run("--record-accept", "--target", "BUILD", "--answer", "yes").stdout, "accepted_event_id")
    r.run("--record-decline", "--reason", "user_declined")
    res = r.run("--target", "BUILD", "--accepted-answer", acc, *brief)
    check("an untargeted decline blocks a write target", res.returncode == 3 and gone in res.stderr, f"rc={res.returncode} {res.stderr}")
    s = Proj(tmp, "declined-plan")
    s.run("--record-decline", "--reason", "user_declined")
    check("an untargeted decline does not block the automatic PLAN", s.run(*plan_args()).returncode == 0, "")


def test_task_tools(tmp: str) -> None:
    p = Proj(tmp, "tools-inherit")
    p.run(*plan_args())
    check("task_tools_available is inherited from the parent artifact", p.child().get("capabilities", {}).get("task_tools_available") == "false", str(p.child().get("capabilities")))
    q = Proj(tmp, "tools-flag")
    q.run(*plan_args("--task-tools-available", "true"))
    check("an explicit --task-tools-available wins", q.child().get("capabilities", {}).get("task_tools_available") == "true", str(q.child().get("capabilities")))


def test_messages(tmp: str) -> None:
    p = Proj(tmp, "nolog")
    p.log.unlink()
    res = p.run(*plan_args())
    check("a missing parent log is not reported as 'not finalized'", res.returncode == 4 and "Error: parent workflow log not found" in res.stderr and NOT_FINAL not in res.stderr, f"rc={res.returncode} {res.stderr}")
    q = Proj(tmp, "malformed")
    with q.log.open("a", encoding="utf-8") as fh:
        fh.write("{not json\nalso not json\n")
    res = q.run(*plan_args())
    check("skipped malformed log lines are counted on stderr", res.returncode == 0 and "Warning: skipped 2 malformed line(s)" in res.stderr, f"rc={res.returncode} {res.stderr}")
    if os.geteuid() == 0:
        return
    r = Proj(tmp, "nolog-append")
    r.wf_dir.chmod(0o555)
    r.log.chmod(0o444)
    try:
        res = r.run(*plan_args())
    finally:
        r.log.chmod(0o644)
        r.wf_dir.chmod(0o755)
    check("a failing handoff_failed append is reported", res.returncode == 5 and "could not append handoff_failed" in res.stderr, f"rc={res.returncode} {res.stderr}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        for fn in (test_race, test_lock_states, test_host_derivation, test_finalized_restart, test_input, test_accept_per_target,
                   test_parent_type, test_flag_values, test_orphans, test_declined_blocks, test_task_tools, test_messages):
            fn(tmp)
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
