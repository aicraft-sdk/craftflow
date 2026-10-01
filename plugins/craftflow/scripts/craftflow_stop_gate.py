#!/usr/bin/env python3
"""CRAFTFLOW stop gate hook shell (SPEC-0018 / ADR-0055), slice 1: SHADOW mode.

Opt-in Claude Code ``Stop`` hook. With ``mode: off`` (the shipped default) it reads two settings files and
returns: no transcript read, no git call, no row, no output. With ``mode: audit`` it gathers facts (bound
workflow artifact, git state, transcript tail), runs the pure core (hard rules, heuristic, verdict) and
appends ONE shadow row. It never continues or commits. The only thing it can print is the opt-in
``notify: "push"`` relay block (consent-file only), which asks the model for one PushNotification call.

Fail open: every branch is inside ``try/except`` and ``main()`` always returns 0.
Privacy: rows carry no message text and no artifact free text (``build_row`` drops unknown keys).
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time

import craftflow_context_nudge_compact as cc
import craftflow_stop_gate_core as core
from craftflow_hooklib import load_input, log_event, now_iso, plugin_config_dir, project_dir, state_root

DEADLINE_S = 4.2
GIT_CALL_TIMEOUT_S = 0.8
GIT_MIN_REMAINING_S = 1.0
GIT_RESERVE_S = 0.5
NOTIFY_MIN_REMAINING_S = 1.2
NOTIFY_TIMEOUT_S = 1.0
NOTIFY_RESERVE_S = 0.2
TAIL_BYTES = 1048576
STATUS_DIRTY_CAP = 500
_UNMERGED_CODES = frozenset(("DD", "AU", "UD", "UA", "DU", "AA", "UU"))
_TS_RE = re.compile(
    r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.(\d+))?\s*(Z|[+-]\d\d:?\d\d)?$")


# --- transcript tail (DD-21) -------------------------------------------------------------------------
def parse_ts(value):
    """Epoch seconds of an ISO-8601 timestamp string, else None. No timezone means UTC."""
    if not isinstance(value, str):
        return None
    match = _TS_RE.match(value.strip())
    if not match:
        return None
    try:
        year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
        epoch = calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0))
        if match.group(7):
            epoch += float("0." + match.group(7))
        zone = match.group(8)
        if zone and zone != "Z":
            digits = zone[1:].replace(":", "")
            offset = int(digits[:2]) * 3600 + int(digits[2:]) * 60
            epoch -= offset if zone[0] == "+" else -offset
        return float(epoch)
    except (ValueError, OverflowError):
        return None


def read_tail(path):
    """Last TAIL_BYTES of a REGULAR transcript file as bytes; b'' for anything else (FIFO, missing, ...)."""
    if not isinstance(path, str) or not path:
        return b""
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return b""
        start = max(0, info.st_size - TAIL_BYTES)
        os.lseek(fd, start, os.SEEK_SET)
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
        if start > 0:
            data = data.split(b"\n", 1)[1] if b"\n" in data else b""  # drop the cut first line
        return data
    except OSError:
        return b""
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


def is_regular_file(path):
    try:
        return isinstance(path, str) and bool(path) and stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return False


def _records(data):
    for raw in data.split(b"\n"):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, RecursionError):
            continue
        if isinstance(rec, dict):
            yield rec


def is_genuine_human(rec):
    """DD-21: a user line typed by the human (not a tool result, meta, summary, command or our own relay)."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isCompactSummary") or "toolUseResult" in rec:
        return False
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in ("text", "image"):
                return False
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        text = "\n".join(parts)
    else:
        return False
    stripped = text.strip()
    if stripped.startswith("<") or stripped.startswith("Stop hook feedback"):
        return False
    return "craftflow stop-gate:" not in text


def _assistant_text(rec):
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b["text"] for b in content
                         if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


def scan_transcript(data):
    """{last_human_ts, last_human_epoch, oldest_epoch, assistant_text} from the transcript tail bytes."""
    out = {"last_human_ts": None, "last_human_epoch": None, "oldest_epoch": None, "assistant_text": ""}
    for rec in _records(data):
        stamp = rec.get("timestamp")
        epoch = parse_ts(stamp)
        if epoch is not None and (out["oldest_epoch"] is None or epoch < out["oldest_epoch"]):
            out["oldest_epoch"] = epoch
        if is_genuine_human(rec) and epoch is not None:
            out["last_human_ts"], out["last_human_epoch"] = stamp, epoch
        elif rec.get("type") == "assistant":
            text = _assistant_text(rec)
            if text.strip():
                out["assistant_text"] = text
    return out


def turn_timing(scan, now):
    """(turn_seconds, lower_bound): exact from the last human line, else a lower bound, else (None, False)."""
    if scan["last_human_epoch"] is not None:
        return round(max(0.0, now - scan["last_human_epoch"]), 1), False
    if scan["oldest_epoch"] is not None:
        return round(max(0.0, now - scan["oldest_epoch"]), 1), True
    return None, False


# --- git facts (DD-20) -------------------------------------------------------------------------------
def _git(root, args, remaining, facts=None):
    """CompletedProcess for one git call, or None when skipped for lack of time, timed out or not runnable.

    A call skipped for lack of time marks ``facts["skip"] = "deadline"`` (starvation, not git unsafety)."""
    left = remaining()
    if left < GIT_MIN_REMAINING_S:
        if facts is not None:
            facts["skip"] = "deadline"
        return None
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        return subprocess.run(["git", "-C", root] + args, capture_output=True, text=True, errors="replace",
                              timeout=min(GIT_CALL_TIMEOUT_S, left - GIT_RESERVE_S), env=env)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def parse_status(text):
    """(dirty_paths, unmerged, truncated) from ``git status --porcelain=v1 -z`` output.

    ``dirty_paths`` holds at most STATUS_DIRTY_CAP paths; ``truncated`` says there were more."""
    paths, unmerged, total = [], False, 0
    entries = text.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code in _UNMERGED_CODES:
            unmerged = True
        if "R" in code or "C" in code:
            i += 1  # the rename/copy source follows as its own NUL entry
        total += 1
        if len(paths) < STATUS_DIRTY_CAP:
            paths.append(path)
    return paths, unmerged, total > STATUS_DIRTY_CAP


def git_facts(root, remaining):
    """Facts of the worktree at ``root`` with at most three git calls. Any failure => ok False (H11).

    ``skip`` says why a fail-closed result is not real git unsafety: ``deadline`` (starved of time) or
    ``truncated`` (more than STATUS_DIRTY_CAP dirty paths: the list is incomplete, so H11 fires)."""
    facts = {"ok": False, "in_progress_op": None, "unmerged": False, "dirty_paths": [], "head": None,
             "skip": None}
    status = _git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"], remaining, facts)
    if status is None or status.returncode != 0:
        return facts
    facts["dirty_paths"], facts["unmerged"], truncated = parse_status(status.stdout)
    if truncated:
        facts["skip"] = "truncated"
        return facts
    head = _git(root, ["rev-parse", "HEAD"], remaining, facts)
    if head is not None and head.returncode == 0:
        facts["head"] = head.stdout.strip() or None
    ops = _git(root, ["rev-parse", "--git-path", "MERGE_HEAD", "--git-path", "rebase-merge",
                      "--git-path", "rebase-apply"], remaining, facts)
    if ops is None or ops.returncode != 0:
        return facts  # in-progress state unknown: stays unsafe
    lines = ops.stdout.splitlines()
    if len(lines) != 3:
        return facts
    for name, line in zip(("merge", "rebase", "rebase"), lines):
        if os.path.exists(line if os.path.isabs(line) else os.path.join(root, line)):
            facts["in_progress_op"] = name
            break
    facts["ok"] = True
    return facts


# --- session state (DD-13) ---------------------------------------------------------------------------
def session_path(root, session_id):
    digest = hashlib.sha256(session_id.encode("utf-8", "replace")).hexdigest()[:16]
    return state_root(root) / "stop-gate" / "sessions" / (digest + ".json")


def read_session(root, session_id):
    """(record, tag): {} without a session id or file; a corrupt file resets with tag session_state_reset."""
    if not isinstance(session_id, str) or not session_id:
        return {}, None
    try:
        raw = session_path(root, session_id).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError):
        return {}, "session_state_reset"
    try:
        record = json.loads(raw)
    except (ValueError, RecursionError):
        return {}, "session_state_reset"
    return (record, None) if isinstance(record, dict) else ({}, "session_state_reset")


def write_session(root, session_id, record):
    """Atomic write (tmp + os.replace). Returns False when it could not be written."""
    if not isinstance(session_id, str) or not session_id:
        return False
    path = session_path(root, session_id)
    tmp = path.with_name(path.name + ".tmp%d" % os.getpid())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(record, ensure_ascii=True), encoding="utf-8")
        os.replace(str(tmp), str(path))
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


# --- settings and the hook body ----------------------------------------------------------------------
def _consent_home():
    """Passwd home that holds the consent file (never HOME). Replaced by tests that need a scratch home."""
    return core.passwd_home()


def load_settings(env, with_consent, report_errors=False):
    """(settings, tags): plugin file + user file (+ consent file when asked). Never raises.

    A user file that exists but cannot be used (corrupt, oversized, ...) still fails open to the defaults, but
    is surfaced: a ``user_config_error:<why>`` tag and, with ``report_errors``, one ``config_error`` log event.
    The plugin file is ours and is never reported."""
    plugin_obj = core.load_json_file(os.path.join(str(plugin_config_dir()), "stop-gate.json"))[0]
    user_obj, user_status, user_error = core.load_json_file(core.user_override_path(env))
    source = core.override_source(env, core.passwd_home())
    consent_obj, consent_tag = (core.read_consent_file(_consent_home()) if with_consent else (None, None))
    settings, tags = core.parse_settings(plugin_obj, user_obj, source, consent_obj)
    if consent_tag:
        tags.append(consent_tag)
    if user_status == "error" and user_error:
        tags.append("user_config_error:" + user_error)
        if report_errors:
            log_event("plugin_stop_gate", {"config_error": user_error, "source": "user"})
    return settings, tags


def _bind(root, transcript_path, session_id):
    """(wf|None, reason, workflow_payload|None) via the nudge module's binding (DD-8, DD-22)."""
    path = transcript_path if is_regular_file(transcript_path) else ""
    _snapshot, reason, wf = cc.resolve_active_workflow(
        str(root / ".craftflow" / "state" / "workflows"), path, session_id, str(root), time.time())
    if wf is None:
        return None, reason, None
    loaded = cc._load_artifact(os.path.join(str(root), ".craftflow", "state", "workflows", wf + ".json"))
    payload = loaded[2] if loaded else None
    return wf, reason, (payload if isinstance(payload, dict) else None)


def _git_root(wf_facts, payload, root):
    for candidate in (wf_facts.get("worktree_path") if wf_facts else None, payload.get("cwd")):
        if isinstance(candidate, str) and candidate and os.path.isdir(candidate):
            return candidate
    return str(root)


def _jev_active(env, session_id, root):
    """(active, cfg): the OR-gate of craftflow_jev_prompt_hint.session_is_active, re-implemented locally."""
    import craftflow_jev_config as jcfg
    import craftflow_jev_session_cache as jcache
    cfg, _decisions = jcfg.load_config(plugin_config_dir() / "jev.json")
    if jcfg.is_active(cfg, env):
        return True, cfg
    if jcfg.consent_status(cfg) != "granted" or not jcfg.api_key(env) or not session_id:
        return False, cfg
    cached = jcache.read_session_status(state_root(root), session_id)
    return bool(cached and cached.get("active") is True), cfg


def _jev_failure_status(reason):
    """jev_status for a call that returned None: timeout | http_error | failed."""
    if reason.get("status") is not None:
        return "http_error"
    return "timeout" if reason.get("error") in ("timeout", "TimeoutError") else "failed"


def _call_jev(state, cfg, key, settings, remaining):
    """(answers|None, status, latency_ms, usage): the client call in a daemon thread under a hard wall-clock join."""
    import craftflow_jev_client as client
    left = remaining()
    budget = min(2.5, left - 1.0)
    box, reason = {}, {}

    def worker():
        try:
            box["result"] = client.call(
                state, core.jev_questions(), api_key=key, model=cfg["model"],
                timeout=min(settings["jevTimeoutSeconds"], budget), cache_dir=None,
                total_budget_seconds=budget, failure_reason_out=reason)
        except Exception:  # noqa: BLE001 - the client never raises; this keeps the thread silent regardless
            box["result"] = None

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=budget)
    if thread.is_alive():
        return None, "timeout", None, None  # abandoned: daemon threads do not block interpreter exit
    result = box.get("result")
    if not isinstance(result, dict):
        return None, _jev_failure_status(reason), None, None
    return result.get("answers"), "ok", result.get("latency_ms"), result.get("usage")


def jev_layer(settings, rule_hits, env, session_id, root, message, wf_facts, has_next, remaining):
    """(jev_dict|None, fields): optional Jev text classification (DD-11). Fails open to (None, status only)."""
    fields = {"jev_status": "not_needed", "jev_kind": None, "jev_kind_conf": None, "jev_needs_human": None,
              "jev_latency_ms": None, "jev_usage": None}
    if rule_hits:
        return None, fields
    if not settings.get("jevText"):
        fields["jev_status"] = "not_consented"
        return None, fields
    try:
        active, cfg = _jev_active(env, session_id, root)
        if not active:
            fields["jev_status"] = "inactive"
            return None, fields
        if not core.should_call_jev(True, True, rule_hits, remaining()):
            fields["jev_status"] = "skipped_deadline"
            return None, fields
        import craftflow_jev_config as jcfg
        key = jcfg.api_key(env)
        tail, truncated = core.jev_tail(message, settings["tailChars"], key)
        if tail is None:
            fields["jev_status"] = "message_too_large" if len(message) > core.JEV_RAW_CAP else "failed"
            return None, fields
        state = core.jev_state(tail, truncated, wf_facts["workflow_type"] if wf_facts else "", has_next)
        answers, status, latency, usage = _call_jev(state, cfg, key, settings, remaining)
        fields["jev_status"], fields["jev_latency_ms"], fields["jev_usage"] = status, latency, usage
        if status != "ok":
            return None, fields
        gated = core.gate_jev_answers(answers)
        if gated is None:
            fields["jev_status"] = "malformed"
            return None, fields
        fields.update(jev_kind=gated["kind"], jev_kind_conf=gated["kind_conf"],
                      jev_needs_human=gated["needs_human"])
        return gated, fields
    except Exception:  # noqa: BLE001 - fail open: a Jev problem never changes the hook's outcome
        fields.update(jev_status="failed", jev_kind=None, jev_kind_conf=None, jev_needs_human=None)
        return None, fields


# --- notification sender (DD-12) ---------------------------------------------------------------------
def send_desktop(text, remaining, path=None):
    """Fire one local desktop notification (argv only, no shell). Returns the notify_status string."""
    left = remaining()
    if left < NOTIFY_MIN_REMAINING_S:
        return "skipped_deadline"
    if sys.platform == "darwin":
        exe, tail = "osascript", ["-e", "on run argv", "-e",
                                  'display notification (item 1 of argv) with title "Craftflow"',
                                  "-e", "end run", text]
    elif sys.platform.startswith("linux"):
        exe, tail = "notify-send", ["Craftflow", text]
    else:
        return "unsupported_platform"
    found = shutil.which(exe, path=path)
    if not found:
        return "unavailable"
    try:
        done = subprocess.run([found] + tail, capture_output=True,
                              timeout=min(NOTIFY_TIMEOUT_S, left - NOTIFY_RESERVE_S))
    except subprocess.TimeoutExpired:
        return "timeout"
    except (OSError, ValueError):
        return "failed"
    return "sent" if done.returncode == 0 else "failed"


def notify_step(settings, verdict, wf, cursor, session, tail_sha, hook_active, turn_seconds, session_id,
                remaining, last_human_ts=None):
    """(notify_status, relay_reason|None, sent). Eligibility per DD-12; never raises."""
    mode = settings["notify"]
    if mode == "off":
        return None, None, False
    try:
        if verdict["verdict"] != "needs_human":
            return "not_needed", None, False
        if turn_seconds is None or turn_seconds < settings["notifyMinTurnSeconds"]:
            return "skipped_min_turn", None, False
        text = core.notify_text(verdict["verdict"], wf, cursor, verdict.get("reasons"))
        if mode == "push":
            if hook_active:
                return "skipped_hook_active", None, False
            if not session_id:  # no session record, so no host-independent cap: never relay
                return "skipped_no_session", None, False
            if not core.relay_decision(settings, verdict["verdict"], hook_active, session, tail_sha,
                                       last_human_ts):
                return "skipped_duplicate", None, False
            return "relay", core.PUSH_RELAY_TEMPLATE % text, False
        if session_id and tail_sha and session.get("last_notified_tail_sha") == tail_sha:
            return "skipped_duplicate", None, False
        status = send_desktop(text, remaining)
        return status, None, status == "sent"
    except Exception:  # noqa: BLE001 - a notification problem never changes the hook's outcome
        return "failed", None, False


def relay_followup_row(settings, tags, payload, session_id, transcript_path, t0):
    """The row for the stop that ends a push-relay turn: no classification, every ROW_KEYS key present."""
    perm = payload.get("permission_mode")
    return core.build_row(
        row_kind="relay_followup", ts=now_iso(), session_id=session_id or None,
        transcript_path=transcript_path or None, mode=settings["mode"], stop_hook_active=True,
        permission_mode=perm if isinstance(perm, str) else None,
        notify=settings["notify"], notify_status="relay_followup",
        hook_ms=int((time.monotonic() - t0) * 1000), settings_tags=tags)


def run(payload, env, t0=None, ctx=None):
    """(stdout_obj|None, row|None). ``row`` is None whenever the hook is inert. Only the consent-file push
    relay ever returns a stdout object. ``ctx`` (optional) receives ``mode``/``session_id`` once the hook is
    active, so a failure can still leave an error row."""
    t0 = time.monotonic() if t0 is None else t0
    ctx = {} if ctx is None else ctx

    def remaining():
        return DEADLINE_S - (time.monotonic() - t0)

    if not isinstance(payload, dict) or payload.get("hook_event_name") != "Stop":
        return None, None
    if env.get("CURSOR_PLUGIN_ROOT"):  # inferred, unproven extra guard (DD-15)
        return None, None
    settings, tags = load_settings(env, with_consent=False, report_errors=True)
    if settings["mode"] == "off":
        return None, None
    ctx["mode"] = settings["mode"]
    settings, tags = load_settings(env, with_consent=True)

    root = project_dir()
    session_id = payload.get("session_id") if isinstance(payload.get("session_id"), str) else ""
    ctx["session_id"] = session_id
    transcript_path = payload.get("transcript_path") if isinstance(payload.get("transcript_path"), str) else ""
    now = time.time()
    hook_active = payload.get("stop_hook_active") is True
    session, session_tag = read_session(root, session_id)
    if session_tag:
        tags.append(session_tag)
    if session.get("relay_pending"):
        if hook_active:  # the stop that ends a relay turn: no classification, clear the flag (DD-12, A8)
            write_session(root, session_id, dict(session, relay_pending=False, updated_at=now))
            return None, relay_followup_row(settings, tags, payload, session_id, transcript_path, t0)
        session = dict(session, relay_pending=False)  # stale flag from an ended relay or a user interruption
    scan = scan_transcript(read_tail(transcript_path))
    turn_seconds, lower_bound = turn_timing(scan, now)
    given = payload.get("last_assistant_message")
    message = given if isinstance(given, str) and given.strip() else scan["assistant_text"]

    wf, reason, wf_payload = _bind(root, transcript_path, session_id)
    wf_facts = core.workflow_facts(wf_payload, os.path.isdir) if wf_payload is not None else None
    git = git_facts(_git_root(wf_facts, payload, root), remaining)
    if git.get("skip") == "deadline":
        tags.append("git_skipped_deadline")
    elif git.get("skip") == "truncated":
        tags.append("git_dirty_truncated")

    facts = {
        "message": message, "binding_reason": reason, "stop_hook_active": hook_active,
        "permission_mode": payload.get("permission_mode") if isinstance(payload.get("permission_mode"), str) else "",
        "git": git, "session": session, "settings": settings, "workflow": wf_facts,
        "last_human_ts": scan["last_human_ts"],
    }
    rule_hits = core.hard_rules(facts)
    blockers = core.commit_blockers(facts)
    guards = core.loop_guards(facts, session)
    kind, _signals = core.heuristic_kind(message)
    heuristic_verdict = core.decide(rule_hits, blockers, kind, None, settings)
    nxt = core.next_phase(facts) if wf_facts is not None else None
    jev, jev_fields = jev_layer(settings, rule_hits, env, session_id, root, message, wf_facts,
                                bool(nxt and nxt["phase"] is not None), remaining)
    verdict = core.decide(rule_hits, blockers, kind, jev, settings) if jev else heuristic_verdict
    cursor = wf_facts["phase_cursor"] if wf_facts is not None else None
    tail_sha = (hashlib.sha256(message[-settings["tailChars"]:].encode("utf-8", "replace")).hexdigest()[:16]
                if message.strip() else None)

    notify_status, relay_reason, sent = notify_step(
        settings, verdict, wf, cursor if isinstance(cursor, str) else "", session, tail_sha, hook_active,
        turn_seconds, session_id, remaining, scan["last_human_ts"])
    write_session(root, session_id, core.session_update(
        session, verdict["verdict"], git.get("head"), cursor, tail_sha, relay_reason is not None, now,
        last_human_ts=scan["last_human_ts"], notified=sent))
    row = core.build_row(
        row_kind="stop", ts=now_iso(), session_id=session_id or None, transcript_path=transcript_path or None,
        mode=settings["mode"], mode_tag="act_not_available" if "act_not_available" in tags else None,
        stop_hook_active=facts["stop_hook_active"], permission_mode=facts["permission_mode"] or None,
        wf=wf, binding_reason=reason, phase_cursor=cursor,
        cursor_case=nxt["case"] if nxt else None, cursor_resolution=nxt["resolution"] if nxt else None,
        rule_hits=rule_hits, loop_guards=guards, commit_blockers=blockers,
        jev_text_source=settings.get("jev_text_source"), last_human_ts=scan["last_human_ts"],
        heuristic_kind=kind, heuristic_verdict=heuristic_verdict["verdict"], **jev_fields,
        verdict=verdict["verdict"], verdict_source=verdict["verdict_source"],
        act_eligible=verdict["act_eligible"], would_action=verdict["would_action"],
        notify=settings["notify"], notify_status=notify_status, turn_seconds=turn_seconds, turn_seconds_lower_bound=lower_bound,
        message_chars=len(message), tail_sha=tail_sha, hook_ms=int((time.monotonic() - t0) * 1000),
        settings_tags=tags)
    return ({"decision": "block", "reason": relay_reason} if relay_reason else None), row


def append_row(row):
    """Append one shadow row to stop-gate/events.jsonl under the project state root (DD-14)."""
    folder = state_root(project_dir()) / "stop-gate"
    folder.mkdir(parents=True, exist_ok=True)
    with open(str(folder / "events.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def error_row(exc, ctx, t0):
    """Minimal row_kind "error" row: the exception CLASS name only, never its message."""
    return core.build_row(
        row_kind="error", ts=now_iso(), session_id=ctx.get("session_id") or None, mode=ctx.get("mode"),
        hook_ms=int((time.monotonic() - t0) * 1000), settings_tags=["hook_error:" + type(exc).__name__])


def record_failure(exc, ctx, t0):
    """Leave a trace of a hook failure: an error row when the hook was active, else (or when that write also
    fails) a ``plugin_stop_gate_error`` log event. Never raises."""
    try:
        if ctx.get("mode") is None:
            raise exc  # inert hook: nothing to put in the events file, fall through to the log event
        append_row(error_row(exc, ctx, t0))
        return
    except Exception:  # noqa: BLE001 - best effort, fall through to the log event
        pass
    try:
        log_event("plugin_stop_gate_error", {"exc": type(exc).__name__})
    except Exception:  # noqa: BLE001 - nothing more can be done; the Stop proceeds
        pass


def main():
    """Always returns 0 (fail open). The row is appended BEFORE any relay block is printed."""
    t0 = time.monotonic()
    ctx = {}
    try:
        out, row = run(load_input(), os.environ, t0, ctx)
        if row is not None:
            append_row(row)
            log_event("plugin_stop_gate", {"decision": row["verdict"], "wf": row["wf"], "mode": row["mode"],
                                           "rule_hits": row["rule_hits"], "row_kind": row["row_kind"]})
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=True))
            sys.stdout.flush()
    except Exception as exc:  # noqa: BLE001 - fail-open contract: the Stop proceeds exactly as today
        record_failure(exc, ctx, t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
