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

Exit: 0 ok | 2 usage, invalid --reason/--detail, QA without a session on claude | 3 write target without a recorded
accept (also --record-accept --just-go) | 4 parent not found or not finalized, input missing | 5 mint failed
(`handoff_failed` is appended to the parent log when it is writable, and no child is left behind).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import craftflow_workflow_id as wid  # noqa: E402

TARGETS = ("PLAN", "BUILD", "DEBUG", "QA")
WRITE_TARGETS = ("BUILD", "DEBUG")
PHASES = {"PLAN": "plan", "BUILD": "build", "DEBUG": "debug", "QA": "qa"}
REASONS = ("user_declined", "non_interactive")
DETAILS = ("tool_unavailable", "tool_error", "no_answer")
PARENT_RE = re.compile(r"wf-[A-Za-z0-9-]+")
NO_SESSION = "QA workflow needs a session id (CLAUDE_CODE_SESSION_ID unset or invalid); the QA guard engages by session"

Plan = Tuple[Any, ...]  # ("mint", mode) | ("already", child) | ("error", code, message)


def is_finalized(events: List[Dict[str, Any]]) -> bool:
    """`memory_finalized`, or (a Cursor parent, D-12) a `workflow_started` with host cursor plus `workflow_completed`."""
    names = {e.get("event") for e in events}
    if "memory_finalized" in names:
        return True
    cursor = any(e.get("event") == "workflow_started" and e.get("host") == "cursor" for e in events)
    return cursor and "workflow_completed" in names


def accepted_event(events: List[Dict[str, Any]], event_id: Optional[str], target: str) -> bool:
    return bool(event_id) and any(e.get("event") == "handoff_accepted" and e.get("event_id") == event_id
                                  and e.get("target") == target for e in events)


def plan_handoff(parent_artifact: Dict[str, Any], parent_events: List[Dict[str, Any]], target: str,
                 answer: Optional[str], just_go: bool, *, host: str = "claude", has_session: bool = True) -> Plan:
    """Decide a handoff from the parent's facts only. `answer` is the `--accepted-answer` event id; `just_go` never
    stands in for it. Order: finalized (4), already handed off, accept for write targets (3), QA session (2)."""
    if not is_finalized(parent_events):
        return ("error", 4, "parent workflow is not finalized")
    for event in parent_events:
        if event.get("event") == "handoff_started" and event.get("child_workflow_uuid"):
            return ("already", event["child_workflow_uuid"])
    if target in WRITE_TARGETS:
        if not accepted_event(parent_events, answer, target):
            return ("error", 3, "write-capable target needs a recorded accept")
        return ("mint", "asked")
    if target == "QA" and host != "cursor" and not has_session:
        return ("error", 2, NO_SESSION)
    return ("mint", "auto")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value


def read_events(path: Path) -> List[Dict[str, Any]]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def append_event(path: Path, wf: str, event: str, **fields: Any) -> Dict[str, Any]:
    record = {"ts": now(), "wf": wf, "event": event, "agent": "router", **fields}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return record


def fail(code: int, message: str) -> int:
    sys.stderr.write(f"Error: {message}\n")
    return code


def find_child(wf_dir: Path, parent: str) -> Optional[str]:
    """A child artifact whose `parent_workflow_uuid` is `parent` (covers a crash before the parent log line)."""
    for path in sorted(wf_dir.glob("wf-*.json")):
        try:
            if parent.encode() not in path.read_bytes():
                continue
            art = read_json(path)
        except (OSError, ValueError):
            continue
        if art.get("parent_workflow_uuid") == parent:
            return str(art.get("workflow_uuid") or path.stem)
    return None


def remove_child(wf_dir: Path, child: str) -> None:
    for path in (wf_dir / f"{child}.events.jsonl", wf_dir / f"{child}.json"):
        try:
            path.unlink()
        except OSError:
            pass
    try:
        (wf_dir / child).rmdir()
    except OSError:
        pass


def write_atomic(path: Path, value: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(str(tmp), str(path))


def patch_child(path: Path, handoff: Dict[str, Any], parent: str, target: str, source_qa: Optional[Dict[str, Any]],
                source_qa_wf: Optional[str]) -> None:
    """Link the freshly minted child artifact to its parent (and copy the QA grants and Denied reads)."""
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


def cmd_record_accept(args: argparse.Namespace, log: Path, events: List[Dict[str, Any]], parent: str) -> int:
    if args.just_go:
        return fail(3, "JUST_GO never records an accept")
    if args.target not in WRITE_TARGETS:
        return fail(2, "--record-accept needs --target BUILD or DEBUG")
    if not (args.answer or "").strip():
        return fail(2, "--record-accept needs --answer (the user's reply, verbatim)")
    for event in events:
        if event.get("event") == "handoff_accepted":
            print(f"accepted_event_id={event.get('event_id')}")
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


def cmd_mint(args: argparse.Namespace, project: Path, wf_dir: Path, parent: str, parent_art: Dict[str, Any],
             log: Path, events: List[Dict[str, Any]]) -> int:
    found = find_child(wf_dir, parent)
    if found and not any(e.get("event") == "handoff_started" for e in events):
        events = events + [{"event": "handoff_started", "child_workflow_uuid": found}]
    plan = plan_handoff(parent_art, events, args.target, args.accepted_answer, args.just_go, host=args.host,
                        has_session=wid.current_session_id() is not None)
    if plan[0] == "error":
        return fail(plan[1], plan[2])
    if plan[0] == "already":
        print(f"already_handed_off={plan[1]}")
        return 0
    if args.input and not (project / args.input).exists():
        return fail(4, f"handoff input not found: {args.input}")
    source_qa = None
    if args.target == "QA":
        if not args.source_qa_wf or not PARENT_RE.fullmatch(args.source_qa_wf):
            return fail(2, "--target QA needs --source-qa-wf wf-...")
        try:
            source_qa = read_json(wf_dir / f"{args.source_qa_wf}.json")
        except (OSError, ValueError):
            return fail(4, f"source QA workflow not found: {args.source_qa_wf}")
    handoff = {"source_route": args.source_route or parent_art.get("workflow_type"), "source_workflow_uuid": parent,
               "input_path": args.input, "input_kind": args.input_kind, "mode": plan[1]}
    request = args.request or parent_art.get("user_request") or f"{args.target} handoff from {parent}"
    info = wid.mint_workflow_id(request, "", project)
    child = info["workflow_uuid"]
    created = False
    try:
        wid.init_artifact(project, info, workflow_type=args.target, request=request, phase=PHASES[args.target],
                          task_tools_available=args.task_tools_available,
                          host="cursor" if args.host == "cursor" else "claude-code",
                          allow_no_session=args.host == "cursor")
        created = True
        patch_child(wf_dir / f"{child}.json", handoff, parent, args.target, source_qa, args.source_qa_wf)
        fields = {"target": args.target, "mode": plan[1], "source_route": handoff["source_route"],
                  "parent_workflow_uuid": parent, "child_workflow_uuid": child}
        append_event(wf_dir / f"{child}.events.jsonl", child, "handoff_started", **fields)
        append_event(log, parent, "handoff_started", **fields)
    except (OSError, ValueError) as exc:
        if created:
            remove_child(wf_dir, child)
        message = wid.describe_init_error(exc) if isinstance(exc, OSError) else str(exc)
        try:
            append_event(log, parent, "handoff_failed", target=args.target, reason=message[:200])
        except OSError:
            pass
        return fail(5, f"handoff mint failed: {message}")
    print(f"child_workflow_uuid={child}")
    print(f"mode={plan[1]}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Mint a linked child workflow from a finalized parent.")
    ap.add_argument("--project", default=None)
    ap.add_argument("--parent", required=True)
    ap.add_argument("--target", choices=TARGETS, default=None)
    ap.add_argument("--input", default=None, help="design file (PLAN) or brief path (BUILD/DEBUG), relative to the project")
    ap.add_argument("--input-kind", default=None, help="design|brief|bug-context")
    ap.add_argument("--source-route", default=None)
    ap.add_argument("--source-qa-wf", default=None)
    ap.add_argument("--accepted-answer", default=None, help="event id printed by --record-accept")
    ap.add_argument("--just-go", action="store_true")
    ap.add_argument("--host", choices=("claude", "cursor"), default="claude")
    ap.add_argument("--request", default=None)
    ap.add_argument("--task-tools-available", choices=("true", "false", "unknown"), default="unknown")
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
        parent_art, events = read_json(artifact), read_events(log)
    except (OSError, ValueError):
        return fail(4, f"parent workflow not found: {args.parent}")
    if args.record_accept:
        return cmd_record_accept(args, log, events, args.parent)
    if args.record_decline:
        return cmd_record_decline(args, log, args.parent)
    if args.target is None:
        return fail(2, "--target is required")
    return cmd_mint(args, project, wf_dir, args.parent, parent_art, log, events)


if __name__ == "__main__":
    sys.exit(main())
