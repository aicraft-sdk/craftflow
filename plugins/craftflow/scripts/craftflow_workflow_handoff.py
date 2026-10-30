#!/usr/bin/env python3
"""craftflow_workflow_handoff.py

Mints a linked child workflow from a finalized advisory/QA parent (H5; `_shared/route-advisory.md`).

  --target PLAN|QA          mints with no question (PLAN needs --input; QA needs --source-qa-wf)
  --target BUILD|DEBUG      mints only with --accepted-answer <event id> naming a `handoff_accepted` event of the
                            parent log for that target; --just-go never substitutes for it
  --record-accept           appends `handoff_accepted {event_id, target, answer}` and prints `accepted_event_id=`
  --record-decline          appends `handoff_declined {reason[, detail]}` (never re-offered)

Prints `child_workflow_uuid=wf-...` on a mint and `already_handed_off=wf-...` when the parent already has a child
(nothing is written then). The parent ARTIFACT is never written (PP-6); only its event log gains lines.

The mint takes an O_EXCL claim `{parent}.handoff.lock` first (kept after a mint as the "handed off" marker; a stale claim is
taken over under a guard), so parallel calls mint exactly one child; `--record-*` take `{parent}.record.lock` for the
append. Any interruption (also KeyboardInterrupt and SIGTERM) after the child is created rolls it back. Only a COMPLETE child
(handoff block + `handoff_started`) is adopted; an incomplete one is removed. The host comes from the parent's
`workflow_started` event; `--host cursor` is only accepted for a Cursor parent. Helpers: craftflow_workflow_handoff_checks.py,
craftflow_workflow_handoff_claim.py.

Exit: 0 ok | 2 usage, invalid flag values, `--host cursor` without a Cursor parent, QA without a session on claude |
3 write target without a recorded accept, or declined (also --record-accept --just-go) | 4 parent not found, not
finalized or of a type that cannot hand off, input or source QA invalid | 5 mint failed (`handoff_failed` is appended to
the parent log when it is writable, and no child is left behind), or the claim path is not a regular file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import craftflow_workflow_handoff_checks as checks  # noqa: E402
import craftflow_workflow_handoff_claim as claim  # noqa: E402
import craftflow_workflow_id as wid  # noqa: E402
from craftflow_workflow_handoff_checks import (accepted_event, append_event, find_child, is_finalized, logged_child,  # noqa: E402,F401
                                               read_events, read_json, remove_child)

TARGETS = ("PLAN", "BUILD", "DEBUG", "QA")
WRITE_TARGETS = checks.WRITE_TARGETS
PHASES = {"PLAN": "plan", "BUILD": "build", "DEBUG": "debug", "QA": "qa"}
REASONS = ("user_declined", "non_interactive")
DETAILS = ("tool_unavailable", "tool_error", "no_answer")
PARENT_RE = re.compile(r"wf-[A-Za-z0-9-]+")
NO_SESSION = "QA workflow needs a session id (CLAUDE_CODE_SESSION_ID unset or invalid); the QA guard engages by session"

Plan = Tuple[Any, ...]  # ("mint", mode) | ("already", child) | ("error", code, message)


def plan_handoff(parent_artifact: Dict[str, Any], parent_events: List[Dict[str, Any]], target: str,
                 answer: Optional[str], just_go: bool, *, host: str = "claude", has_session: bool = True) -> Plan:
    """Decide a handoff from the parent's facts only. `answer` is the `--accepted-answer` event id; `just_go` never
    stands in for it; `host` is derived from the parent. Order: finalized (4), already handed off, declined (3), accept
    for write targets (3), QA session (2)."""
    if not is_finalized(parent_events):
        return ("error", 4, "parent workflow is not finalized")
    for event in parent_events:
        if event.get("event") == "handoff_started" and event.get("child_workflow_uuid"):
            return ("already", event["child_workflow_uuid"])
    if checks.declined_blocks(parent_events, target):
        return ("error", 3, "handoff was declined; never re-offered")
    if target in WRITE_TARGETS:
        if not accepted_event(parent_events, answer, target):
            return ("error", 3, "write-capable target needs a recorded accept")
        return ("mint", "asked")
    if target == "QA" and host != "cursor" and not has_session:
        return ("error", 2, NO_SESSION)
    return ("mint", "auto")


def fail(code: int, message: str) -> int:
    sys.stderr.write(f"Error: {message}\n")
    return code


def write_atomic(path: Path, value: Dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{os.urandom(3).hex()}")
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(str(tmp), str(path))


def patch_child(path: Path, handoff: Dict[str, Any], parent: str, target: str, source_qa: Optional[Dict[str, Any]],
                source_qa_wf: Optional[str]) -> None:
    """Add the handoff block to the freshly minted child artifact (and copy the QA grants and Denied reads)."""
    art = read_json(path)
    art["parent_workflow_uuid"] = parent
    art["handoff"] = handoff
    if target == "PLAN" and handoff.get("input_path"):
        art["design_file"] = handoff["input_path"]
    if target == "QA" and source_qa is not None:
        art["source_wf"] = source_qa_wf
        denied = [c for c in source_qa.get("intent", {}).get("constraints", [])
                  if isinstance(c, str) and c.startswith("Denied reads:")]
        art.setdefault("intent", {}).setdefault("constraints", []).extend(denied)
        negotiation = (source_qa.get("qa") or {}).get("negotiation")
        art["qa"] = {"negotiation": negotiation} if negotiation is not None else {}
    write_atomic(path, art)


def recorded(args: argparse.Namespace, wf_dir: Path, log: Path) -> int:
    """Run --record-accept/--record-decline under a short-lived `{parent}.record.lock` (the permanent handoff claim is the
    "handed off" marker and cannot double as one), so parallel records append once; the events are read under the lock."""
    try:
        with claim.short_claim(wf_dir / f"{args.parent}.record.lock"):
            events = read_events(log, warn=False)
            return cmd_record_accept(args, log, events, args.parent) if args.record_accept else cmd_record_decline(args, log, args.parent)
    except claim.ClaimPathError as exc:
        return fail(5, str(exc))
    except OSError as exc:
        return fail(5, f"cannot record: {exc}")


def cmd_record_accept(args: argparse.Namespace, log: Path, events: List[Dict[str, Any]], parent: str) -> int:
    if args.just_go:
        return fail(3, "JUST_GO never records an accept")
    if args.target not in WRITE_TARGETS:
        return fail(2, "--record-accept needs --target BUILD or DEBUG")
    if not (args.answer or "").strip():
        return fail(2, "--record-accept needs --answer (the user's reply, verbatim)")
    existing = checks.current_accept(events, args.target)
    if existing:
        print(f"accepted_event_id={existing}")
        return 0
    event_id = "hacc-" + os.urandom(4).hex()
    try:
        append_event(log, parent, "handoff_accepted", event_id=event_id, target=args.target, answer=args.answer)
    except OSError as exc:
        return fail(5, f"cannot record the accept: {exc}")
    print(f"accepted_event_id={event_id}")
    return 0


def cmd_record_decline(args: argparse.Namespace, log: Path, parent: str) -> int:
    if args.reason not in REASONS:
        return fail(2, f"invalid --reason {args.reason!r} (expected {'|'.join(REASONS)})")
    if args.reason == "non_interactive" and args.detail not in DETAILS:
        return fail(2, f"invalid --detail {args.detail!r} (expected {'|'.join(DETAILS)})")
    if args.reason == "user_declined" and args.detail is not None:
        return fail(2, "invalid --detail: user_declined takes none")
    fields: Dict[str, Any] = {"reason": args.reason}
    if args.detail is not None:
        fields["detail"] = args.detail
    if args.target:
        fields["target"] = args.target
    try:
        append_event(log, parent, "handoff_declined", **fields)
    except OSError as exc:
        return fail(5, f"cannot record the decline: {exc}")
    print("handoff_declined=recorded")
    return 0


class Mint:
    """One mint attempt for one parent; `run` holds the claim, `locked` does the work under it."""

    def __init__(self, args: argparse.Namespace, project: Path, parent: str, parent_art: Dict[str, Any], host: str) -> None:
        self.args, self.project, self.parent, self.parent_art, self.host = args, project, parent, parent_art, host
        self.wf_dir = project / ".craftflow" / "state" / "workflows"
        self.log = self.wf_dir / f"{parent}.events.jsonl"
        self.lock = self.wf_dir / f"{parent}.handoff.lock"
        self.child: Optional[str] = None  # a child artifact this attempt created and may have to roll back

    def precheck(self) -> Optional[Tuple[int, str]]:
        args = self.args
        if args.host == "cursor" and self.host != "cursor":
            return 2, "--host cursor needs a Cursor parent (workflow_started host)"
        bad = checks.validate_flags(args.input_kind, args.source_route)
        if bad:
            return bad
        ptype = self.parent_art.get("workflow_type")
        if ptype not in checks.SOURCE_ROUTES or (ptype == "DEBUG" and args.target != "QA"):
            return 4, f"parent workflow type {ptype} cannot hand off"
        if args.target == "QA" and (not args.source_qa_wf or not PARENT_RE.fullmatch(args.source_qa_wf)):
            return 2, "--target QA needs --source-qa-wf wf-..."
        return None

    def run(self) -> int:
        taken = kept = False
        try:
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # so a TERM unwinds through the rollback below
        except ValueError:
            pass  # not the main thread (in-process callers)
        try:
            state, other = claim.acquire_claim(self.lock, lambda: logged_child(self.log))
            if state == "already":
                print(f"already_handed_off={other}")
                return 0
            taken = True
            code = self.locked()
            kept = code == 0
            return code
        except claim.ClaimPathError as exc:
            return fail(5, str(exc))
        except BaseException as exc:  # every failure after the claim maps to exit 5 with a rollback; an interrupt re-raises
            message = wid.describe_init_error(exc) if isinstance(exc, OSError) else (
                str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: {exc}")
            if self.child:
                remove_child(self.wf_dir, self.child)
            try:
                append_event(self.log, self.parent, "handoff_failed", target=self.args.target, reason=message[:200])
            except OSError as inner:
                sys.stderr.write(f"Error: mint failed; could not append handoff_failed: {inner}\n")
            if not isinstance(exc, Exception):
                raise
            return fail(5, f"handoff mint failed: {message}")
        finally:
            if taken and not kept:
                try:
                    claim.release_claim(self.lock)
                except OSError as exc:
                    sys.stderr.write(f"Warning: could not remove {self.lock.name}: {exc}\n")

    def locked(self) -> int:
        args, parent = self.args, self.parent
        events = read_events(self.log, warn=False)
        found, orphans = checks.scan_children(self.wf_dir, parent)  # under the claim: a crashed mint may have left a child
        for orphan in orphans:  # an incomplete one (no handoff block or no handoff_started) is never adopted
            sys.stderr.write(f"Warning: removed incomplete child {orphan}\n")
            remove_child(self.wf_dir, orphan)
        if found and not any(e.get("event") == "handoff_started" for e in events):
            events = events + [{"event": "handoff_started", "child_workflow_uuid": found}]
        plan = plan_handoff(self.parent_art, events, args.target, args.accepted_answer, args.just_go, host=self.host,
                            has_session=wid.current_session_id() is not None)
        if plan[0] == "error":
            return fail(plan[1], plan[2])
        if plan[0] == "already":
            self.mark(plan[1])
            print(f"already_handed_off={plan[1]}")
            return 0
        input_path = None
        if args.input:
            bad, input_path = checks.resolve_input(self.project, args.input)
            if bad:
                return fail(*bad)
        source_qa = None
        if args.target == "QA":
            try:
                source_qa = read_json(self.wf_dir / f"{args.source_qa_wf}.json")
            except (OSError, ValueError):
                return fail(4, f"source QA workflow not found: {args.source_qa_wf}")
            if source_qa.get("workflow_type") != "QA":
                return fail(4, f"source workflow is not a QA workflow: {args.source_qa_wf}")
        handoff = {"source_route": args.source_route or self.parent_art.get("workflow_type"), "source_workflow_uuid": parent,
                   "input_path": input_path, "input_kind": args.input_kind, "mode": plan[1]}
        request = args.request or self.parent_art.get("user_request") or f"{args.target} handoff from {parent}"
        inherited = (self.parent_art.get("capabilities") or {}).get("task_tools_available")
        tools = args.task_tools_available or (inherited if inherited in ("true", "false", "unknown") else "unknown")
        info = wid.mint_workflow_id(request, "", self.project)
        child = self.child = info["workflow_uuid"]  # set before anything is written: an interrupt must find it to roll back
        wid.init_artifact(self.project, info, workflow_type=args.target, request=request, phase=PHASES[args.target],
                          task_tools_available=tools, host="cursor" if self.host == "cursor" else "claude-code",
                          allow_no_session=self.host == "cursor", extra={"parent_workflow_uuid": parent})
        patch_child(self.wf_dir / f"{child}.json", handoff, parent, args.target, source_qa, args.source_qa_wf)
        fields = {"target": args.target, "mode": plan[1], "source_route": handoff["source_route"],
                  "parent_workflow_uuid": parent, "child_workflow_uuid": child}
        append_event(self.wf_dir / f"{child}.events.jsonl", child, "handoff_started", **fields)
        append_event(self.log, parent, "handoff_started", **fields)
        self.child = None  # linked from the parent log now: nothing below may roll it back
        self.mark(child)
        print(f"child_workflow_uuid={child}")
        print(f"mode={plan[1]}")
        return 0

    def mark(self, child: str) -> None:
        try:
            claim.mark_claim(self.lock, child)
        except OSError as exc:
            sys.stderr.write(f"Warning: could not record {child} in {self.lock.name}: {exc}\n")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Mint a linked child workflow from a finalized parent.")
    ap.add_argument("--project", default=None)
    ap.add_argument("--parent", required=True)
    ap.add_argument("--target", choices=TARGETS, default=None)
    ap.add_argument("--input", default=None, help="design file (PLAN) or brief path (BUILD/DEBUG), a regular file inside the project")
    ap.add_argument("--input-kind", default=None, help="|".join(checks.INPUT_KINDS))
    ap.add_argument("--source-route", default=None, help="|".join(checks.SOURCE_ROUTES))
    ap.add_argument("--source-qa-wf", default=None)
    ap.add_argument("--accepted-answer", default=None, help="event id printed by --record-accept")
    ap.add_argument("--just-go", action="store_true")
    ap.add_argument("--host", choices=("claude", "cursor"), default="claude", help="`cursor` only for a Cursor parent (checked)")
    ap.add_argument("--request", default=None)
    ap.add_argument("--task-tools-available", choices=("true", "false", "unknown"), default=None,
                    help="default: the parent artifact's capabilities.task_tools_available")
    ap.add_argument("--record-accept", action="store_true")
    ap.add_argument("--answer", default=None)
    ap.add_argument("--record-decline", action="store_true")
    ap.add_argument("--reason", default=None)
    ap.add_argument("--detail", default=None)
    args = ap.parse_args(argv)
    if not PARENT_RE.fullmatch(args.parent):
        return fail(2, f"invalid --parent {args.parent!r}")
    project = Path(args.project or os.environ.get("CLAUDE_PROJECT_DIR") or ".").resolve()
    wf_dir = project / ".craftflow" / "state" / "workflows"
    artifact, log = wf_dir / f"{args.parent}.json", wf_dir / f"{args.parent}.events.jsonl"
    try:
        parent_art = read_json(artifact)
    except (OSError, ValueError):
        return fail(4, f"parent workflow not found: {args.parent}")
    try:
        events = read_events(log)
    except OSError:
        return fail(4, f"parent workflow log not found: {args.parent}")
    if args.record_accept or args.record_decline:
        return recorded(args, wf_dir, log)
    if args.target is None:
        return fail(2, "--target is required")
    mint = Mint(args, project, args.parent, parent_art, checks.parent_host(events))
    refusal = mint.precheck()
    return fail(*refusal) if refusal else mint.run()


if __name__ == "__main__":
    sys.exit(main())
