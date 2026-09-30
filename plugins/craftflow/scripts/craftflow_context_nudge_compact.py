#!/usr/bin/env python3
"""CRAFTFLOW context nudge: session-bound /compact line (SPEC-0017 / ADR-0052).

Builds the ready-to-paste ``/compact ...`` command that the context nudge appends to its advisory.
When the session can be tied to exactly one live workflow artifact, the line names that workflow
(bound form). Otherwise it is the generic line. Everything here fails open: a lookup problem yields
the generic line, never an error.

Binding (DD-8): the wf ids mentioned in the last MENTION_TAIL_BYTES of the session transcript are
intersected with fresh, non-terminal workflow artifacts (DD-9). Only six allowlisted fields of the
chosen artifact are ever read into the line (DD-10), so free text and secrets cannot reach it.

Pure functions (no I/O): wf_mentions, payload_terminal, choose_workflow, workflow_snapshot,
build_compact_line. Impure shell: resolve_active_workflow. Stdlib only, no I/O at import time; the
caller passes the workflows directory, project root and transcript path.
"""
from __future__ import annotations

import json
import os
import re

MENTION_TAIL_BYTES = 1048576
MAX_MENTION_HITS = 40
MAX_DISTINCT_MENTIONS = 20
MAX_CANDIDATES = 5
MAX_ARTIFACT_BYTES = 1048576
WORKFLOW_FRESH_S = 43200
MAX_COMPACT_CHARS = 600

# Kept identical to craftflow_context_nudge._WF_RE (asserted by a drift test).
WF_ID_RE = re.compile(r"^wf-[A-Za-z0-9-]{1,160}$")

_MENTION_RE = re.compile(rb"wf-[A-Za-z0-9][A-Za-z0-9-]{0,159}")
_ID_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-")
_PHASE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,39}")
_GATE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
_PATH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}")

_TYPES = ("BUILD", "PLAN", "DEBUG", "REVIEW")
_TERMINAL_MODES = frozenset(
    ("merged_and_removed", "removed_no_merge_needed", "removed_after_pr_merge", "merged"))
_TERMINAL_EVENTS = frozenset(
    ("workflow_completed", "memory_finalized", "workflow_merged", "merged_to_main", "plan_closed",
     "debug_complete"))
_TERMINAL_PHASES = frozenset(("complete", "done"))
_EMPTY_GATES = frozenset(("", "none", "null", "n/a", "false", "-"))
_ESCAPE_LETTERS = frozenset(b"ntr")  # JSON newline/tab/CR in raw JSONL: backslash + letter
_BACKSLASH = 0x5C

GENERIC_COMPACT_LINE = (
    "/compact Preserve the current goal, key decisions, files being edited, open questions, failing "
    "checks or errors, and the immediate next step. If a craftflow workflow is active, keep its wf id, "
    "phase and plan path.")

_BOUND_TAIL = (". Preserve its decisions, open questions, failing checks and next step; after "
               "compaction resume from .craftflow/state/workflows/")


def _after_json_escape(data, i):
    """True when the bytes before ``i`` are a raw JSON newline/tab/CR escape.

    The letter must be preceded by an odd run of backslashes: an even run is escaped backslashes followed
    by a literal letter.
    """
    if i < 2 or data[i - 1] not in _ESCAPE_LETTERS:
        return False
    run = 0
    j = i - 2
    while j >= 0 and data[j] == _BACKSLASH:
        run += 1
        j -= 1
    return run % 2 == 1


def wf_mentions(data, accept=None):
    """Distinct wf ids mentioned in ``data`` (bytes), most recent first. Never raises.

    ``accept`` (optional predicate) is applied to each distinct id once during the backward scan; only
    accepted ids are returned and the scan stops after MAX_CANDIDATES of them. Without it the scan stops
    after MAX_DISTINCT_MENTIONS ids. Either way at most MAX_MENTION_HITS raw hits are examined.
    """
    try:
        if not isinstance(data, (bytes, bytearray)) or not data:
            return []
        limit = MAX_CANDIDATES if accept is not None else MAX_DISTINCT_MENTIONS
        out = []
        seen = set()
        hits = 0
        end = len(data)
        while hits < MAX_MENTION_HITS and len(out) < limit:
            i = data.rfind(b"wf-", 0, end)
            if i < 0:
                break
            end = i
            hits += 1
            if i > 0 and data[i - 1] in _ID_BYTES and not _after_json_escape(data, i):
                continue
            m = _MENTION_RE.match(data, i)
            if not m:
                continue
            if m.end() < len(data) and data[m.end()] in _ID_BYTES:
                continue  # longer than any valid id
            wf = m.group(0).rstrip(b"-").decode("ascii")
            if wf not in seen:
                seen.add(wf)
                if accept is None or accept(wf):
                    out.append(wf)
        return out
    except Exception:  # noqa: BLE001 - pure helper must be total
        return []


def _has_gate(gate):
    """A real pending gate: dict gates count by their ``kind``; empty/placeholder values do not count."""
    if isinstance(gate, dict):
        gate = gate.get("kind")
        if not isinstance(gate, str):
            return False
    if isinstance(gate, str):
        return gate.strip().lower() not in _EMPTY_GATES
    return bool(gate)


def payload_terminal(payload, isdir=os.path.isdir):
    """True when the workflow artifact is finished (DD-9).

    Terminal signals (worktree mode, last history event, terminal phase) always win; a real pending gate
    only keeps an otherwise non-terminal workflow live (it overrides just the missing-worktree signal).
    """
    try:
        if not isinstance(payload, dict):
            return True
        if payload.get("worktree_mode") in _TERMINAL_MODES:
            return True
        history = payload.get("status_history")
        if isinstance(history, list) and history:
            last = history[-1]
            if isinstance(last, dict) and last.get("event") in _TERMINAL_EVENTS:
                return True
        cursor = payload.get("phase_cursor")
        if isinstance(cursor, str) and cursor.strip().lower() in _TERMINAL_PHASES:
            return True
        if _has_gate(payload.get("pending_gate")):
            return False
        wt = payload.get("worktree_path")
        if isinstance(wt, str) and wt and not isdir(wt):
            return True
        return False
    except Exception:  # noqa: BLE001 - pure helper must be total
        return True


def choose_workflow(cands, session_id):
    """Pick one candidate or give up. ``cands``: dicts {wf, mtime, payload}, most recently mentioned first.

    Returns ``(wf, reason)`` or ``(None, reason)``.
    """
    try:
        if not isinstance(cands, list):
            return None, "ambiguous"
        if not cands:
            return None, "no_live_candidate"
        pool = cands
        if isinstance(session_id, str) and session_id:
            # never bind another session's workflow; a candidate without a session_id stays a fallback
            pool = [c for c in cands
                    if not (isinstance(c["payload"].get("session_id"), str) and c["payload"]["session_id"]
                            and c["payload"]["session_id"] != session_id)]
            if not pool:
                return None, "no_live_candidate"
            matched = [c for c in pool if c["payload"].get("session_id") == session_id]
            if len(matched) == 1:
                return matched[0]["wf"], "session_match"
            if len(matched) > 1:
                pool = matched
        if len(pool) == 1:
            return pool[0]["wf"], "single_candidate"
        newest = pool[0]["mtime"]
        if all(newest > c["mtime"] for c in pool[1:]):
            return pool[0]["wf"], "mention_mtime_agree"
        return None, "ambiguous"
    except Exception:  # noqa: BLE001 - pure helper must be total
        return None, "ambiguous"


def _clean_type(value):
    if isinstance(value, str) and value.upper() in _TYPES:
        return value.upper()
    return None


def _clean_phase(value):
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if isinstance(value, str) and _PHASE_RE.fullmatch(value):
        return value
    return None


def _clean_gate(value):
    if isinstance(value, dict):
        value = value.get("kind")
    if isinstance(value, str) and value.lower() not in _EMPTY_GATES and _GATE_RE.fullmatch(value):
        return value
    return None


def _clean_path(value, project_root):
    if not isinstance(value, str) or "//" in value or ".." in value.split("/"):
        return None
    if value.startswith("/"):
        # Absolute paths are kept only when they sit under the project root (made project-relative);
        # an absolute path outside it is intentionally dropped rather than leaked into the line.
        if not isinstance(project_root, str) or not project_root.startswith("/"):
            return None
        value = os.path.relpath(value, project_root)
        if value == "." or value == ".." or value.startswith("../"):
            return None
    if not _PATH_RE.fullmatch(value) or not value.endswith(".md"):
        return None
    if "" in value.split("/"):
        return None
    return value


def workflow_snapshot(payload, wf, project_root):
    """Allowlisted six-field view of a workflow artifact (DD-10), or None."""
    try:
        if not isinstance(payload, dict) or not isinstance(wf, str) or not WF_ID_RE.fullmatch(wf):
            return None
        return {
            "wf": wf,
            "workflow_type": _clean_type(payload.get("workflow_type")),
            "phase_cursor": _clean_phase(payload.get("phase_cursor")),
            "pending_gate": _clean_gate(payload.get("pending_gate")),
            "plan_file": _clean_path(payload.get("plan_file"), project_root),
            "design_file": _clean_path(payload.get("design_file"), project_root),
        }
    except Exception:  # noqa: BLE001 - pure helper must be total
        return None


def _snapshot_parts(snapshot):
    """Allowlist-validated parts, in degrade-reverse order (type first, design last)."""
    parts = []
    t = snapshot.get("workflow_type")
    if isinstance(t, str) and t in _TYPES:
        parts.append(t)
    phase = snapshot.get("phase_cursor")
    if isinstance(phase, str) and _PHASE_RE.fullmatch(phase):
        parts.append("phase " + phase)
    gate = snapshot.get("pending_gate")
    if isinstance(gate, str) and _clean_gate(gate):
        parts.append("pending gate " + gate)
    for key, label in (("plan_file", "plan "), ("design_file", "design ")):
        path = snapshot.get(key)
        if _clean_path(path, None):
            parts.append(label + path)
    return parts


def build_compact_line(snapshot):
    """The /compact command for ``snapshot``; the generic line when it is unusable. Never raises."""
    try:
        if not isinstance(snapshot, dict):
            return GENERIC_COMPACT_LINE
        wf = snapshot.get("wf")
        if not isinstance(wf, str) or not WF_ID_RE.fullmatch(wf):
            return GENERIC_COMPACT_LINE
        parts = _snapshot_parts(snapshot)
        for n in range(len(parts), -1, -1):
            paren = (" (" + "; ".join(parts[:n]) + ")") if n else ""
            line = "/compact Keep craftflow workflow " + wf + paren + _BOUND_TAIL + wf + ".json."
            if len(line) <= MAX_COMPACT_CHARS:
                return line
        return GENERIC_COMPACT_LINE
    except Exception:  # noqa: BLE001 - pure helper must be total
        return GENERIC_COMPACT_LINE


def _read_tail(path):
    with open(path, "rb") as fh:
        size = fh.seek(0, 2)
        fh.seek(max(0, size - MENTION_TAIL_BYTES))
        return fh.read(MENTION_TAIL_BYTES)


def _load_artifact(path):
    """``(mtime, size, payload)`` of a regular workflow artifact, else None (fail open).

    Opened O_NONBLOCK|O_NOFOLLOW and checked with fstat, so a FIFO cannot block the read and a symlink
    cannot redirect it. Oversize or unparseable artifacts are reported with payload None (exist, never live).
    """
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
        st = os.fstat(fd)
        if (st.st_mode & 0o170000) != 0o100000:
            return None
        if st.st_size > MAX_ARTIFACT_BYTES:
            return st.st_mtime, st.st_size, None
        chunks = []
        total = 0
        while total <= MAX_ARTIFACT_BYTES:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_ARTIFACT_BYTES:
            return st.st_mtime, total, None
        try:
            return st.st_mtime, st.st_size, json.loads(b"".join(chunks).decode("utf-8"))
        except Exception:  # noqa: BLE001 - corrupt artifact (bad JSON, RecursionError): exists, never live
            return st.st_mtime, st.st_size, None
    except Exception:  # noqa: BLE001 - missing/symlink/FIFO/unreadable artifact: not a candidate
        return None
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


def resolve_active_workflow(workflows_dir, transcript_path, session_id, project_root, now):
    """Bind the session to one live workflow (DD-8). Returns ``(snapshot|None, reason, wf|None)``."""
    try:
        live = {}
        existed = []

        def is_live(wf):
            # fresh, non-terminal artifact: folded into the accept predicate so stale/terminal mentions
            # never occupy one of the MAX_CANDIDATES slots
            if not WF_ID_RE.fullmatch(wf):
                return False
            loaded = _load_artifact(os.path.join(workflows_dir, wf + ".json"))
            if loaded is None:
                return False
            existed.append(wf)
            mtime, size, payload = loaded
            if now - mtime > WORKFLOW_FRESH_S or size > MAX_ARTIFACT_BYTES:
                return False
            if not isinstance(payload, dict) or payload_terminal(payload):
                return False
            live[wf] = {"wf": wf, "mtime": mtime, "payload": payload}
            return True

        ids = wf_mentions(_read_tail(transcript_path), accept=is_live)
        if not ids and not existed:
            return None, "no_mention", None
        cands = [live[wf] for wf in ids]
        chosen, reason = choose_workflow(cands, session_id)
        if chosen is None:
            return None, reason, None
        payload = next(c["payload"] for c in cands if c["wf"] == chosen)
        return workflow_snapshot(payload, chosen, project_root), reason, chosen
    except Exception:  # noqa: BLE001 - impure shell must fail open
        return None, "lookup_error", None
