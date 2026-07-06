#!/usr/bin/env python3
"""craftflow_status_report.py

Read-only status reporter for Craftflow workflows. Reads on-disk state that
the router/hooks maintain continuously; safe to run from ANY terminal while
an agent is active — it never writes files (mkdir on pre-existing dirs only).

Usage:
  python3 craftflow_status_report.py                      # current workflow
  python3 craftflow_status_report.py --verbose            # full detail
  python3 craftflow_status_report.py --all                # all workflows table
  python3 craftflow_status_report.py --wf wf-2026...     # explicit workflow ID
  python3 craftflow_status_report.py --feature "text"    # match goal/request
  python3 craftflow_status_report.py --worktree BRANCH   # by worktree branch/path
  python3 craftflow_status_report.py --json              # machine-readable output
  python3 craftflow_status_report.py --statusline       # one-line statusline segment
  python3 craftflow_status_report.py --project /path    # explicit project root
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: add scripts/ dir so craftflow_hooklib imports cleanly regardless
# of where this script is called from.
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import craftflow_hooklib as hl  # noqa: E402  (after sys.path fixup)


# ---------------------------------------------------------------------------
# Project root resolution  (portable across terminals / worktrees)
# ---------------------------------------------------------------------------

def _resolve_project_root(explicit: str | None) -> Path | None:
    """Find the project root that owns .craftflow/state/workflows/."""
    if explicit:
        p = Path(explicit).resolve()
        if not p.is_dir():
            print(f"ERROR: --project dir not found: {p}", file=sys.stderr)
            return None
        return p

    # Walk up from cwd looking for the craftflow workflows directory
    current = Path.cwd()
    for candidate in [current, *current.parents]:
        if (candidate / ".craftflow" / "state" / "workflows").is_dir():
            return candidate

    # Fall back to whatever hooklib resolves (respects CLAUDE_PROJECT_DIR)
    return hl.project_dir()


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _relative_time(ts_str: str | None, fallback_mtime: float | None = None) -> str:
    """Return human-readable 'Ns ago' from an ISO-8601 string or mtime."""
    ts: float | None = None
    if ts_str:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    if ts is None and fallback_mtime is not None:
        ts = fallback_mtime
    if ts is None:
        return "?"
    age = int(_now_ts() - ts)
    if age < 60:
        return f"{age}s ago"
    if age < 3600:
        return f"{age // 60}m ago"
    if age < 86400:
        return f"{age // 3600}h ago"
    return f"{age // 86400}d ago"


def _artifact_mtime(wf_id: str) -> float | None:
    path = hl.workflow_artifact_path(wf_id)
    if path is None:
        return None
    try:
        return path.stat().st_mtime
    except Exception:
        return None


# ---------------------------------------------------------------------------
# State classification
# ---------------------------------------------------------------------------

_DONE_EVENTS = {"memory_finalized", "workflow_complete"}


def _classify(payload: dict[str, Any], wf_id: str) -> str:
    """Return one of: 'BLOCKED' | 'DONE' | 'RUNNING' | 'IDLE'"""
    # 1. Blocked on a human gate
    if payload.get("pending_gate"):
        return "BLOCKED"

    # 2. Completed — check status_history tail and events log
    history = payload.get("status_history") or []
    if history and history[-1].get("event") in _DONE_EVENTS:
        return "DONE"
    if hl.workflow_event_log_contains(wf_id, '"memory_finalized"'):
        return "DONE"

    # 3. Fresh artifact → actively running
    art_path = hl.workflow_artifact_path(wf_id)
    if art_path and hl.workflow_artifact_is_fresh(art_path, max_age_seconds=90):
        return "RUNNING"

    # 4. Stale
    return "IDLE"


_STATE_ICONS: dict[str, str] = {
    "BLOCKED": "⚠️  BLOCKED",
    "DONE":    "✅ DONE   ",
    "RUNNING": "🟢 RUNNING",
    "IDLE":    "⏸  IDLE   ",
}


# ---------------------------------------------------------------------------
# Phase rendering
# ---------------------------------------------------------------------------

def _render_phases_compact(payload: dict[str, Any]) -> str:
    """Return a one-line phase progress string."""
    norm: list[dict] = payload.get("normalized_phases") or []
    cursor: str = payload.get("phase_cursor") or ""
    pstatus: dict[str, str] = payload.get("phase_status") or {}

    if norm:
        symbols, labels = [], []
        for ph in norm:
            pid = ph.get("phase_id", "")
            st = pstatus.get(pid, "")
            if st == "completed":
                symbols.append("✓"); labels.append(f"{pid}:done")
            elif st == "partial":
                symbols.append("▶"); labels.append(f"{pid}:partial")
            elif st == "blocked":
                symbols.append("⚠"); labels.append(f"{pid}:blocked")
            elif pid == cursor:
                symbols.append("▶"); labels.append(f"{pid}:▶running")
            else:
                symbols.append("○"); labels.append(f"{pid}:pending")
        bar = "".join(symbols)
        detail = " · ".join(labels[:3])
        if len(labels) > 3:
            detail += f" +{len(labels) - 3}"
        return f"phases: {bar}  ({detail})"

    # Fallback: normalized_phases is empty (common on real workflows)
    if not cursor and not pstatus:
        return "phases: —"
    parts: list[str] = []
    for pid, st in (pstatus or {}).items():
        icon = "✓" if st == "completed" else ("⚠" if st == "blocked" else "▶")
        parts.append(f"{icon}{pid}:{st}")
    if cursor and cursor not in (pstatus or {}):
        parts.append(f"▶{cursor}:running")
    return "phases: " + " · ".join(parts) if parts else f"phases: cursor={cursor}"


def _render_phases_verbose(payload: dict[str, Any]) -> list[str]:
    """Return multi-line phase detail lines."""
    norm: list[dict] = payload.get("normalized_phases") or []
    cursor: str = payload.get("phase_cursor") or ""
    pstatus: dict[str, str] = payload.get("phase_status") or {}
    lines: list[str] = []

    if norm:
        for ph in norm:
            pid = ph.get("phase_id", "")
            title = ph.get("title", pid)
            st = pstatus.get(pid, "")
            if st == "completed":
                icon, label = "✓", "done"
            elif st == "partial":
                icon, label = "▶", "partial"
            elif st == "blocked":
                icon, label = "⚠", "blocked"
            elif pid == cursor:
                icon, label = "▶", "running"
            else:
                icon, label = "○", "pending"
            lines.append(f"  {icon} {pid}: {title}  [{label}]")
            for ec in (ph.get("exit_criteria") or [])[:2]:
                lines.append(f"      exit: {str(ec)[:90]}")
    else:
        if pstatus or cursor:
            for pid, st in (pstatus or {}).items():
                icon = "✓" if st == "completed" else ("⚠" if st == "blocked" else "▶")
                lines.append(f"  {icon} {pid}: [{st}]")
            if cursor and cursor not in (pstatus or {}):
                lines.append(f"  ▶ {cursor}: [running]")
        else:
            lines.append("  (no phase data recorded yet)")
    return lines


# ---------------------------------------------------------------------------
# Agent chain rendering (verbose)
# ---------------------------------------------------------------------------

_CHAINS: dict[str, list[str]] = {
    "BUILD":  ["builder", "reviewer", "hunter", "verifier", "doc_syncer", "memory_finalize"],
    "DEBUG":  ["investigator", "reviewer", "verifier", "memory_finalize"],
    "REVIEW": ["reviewer", "memory_finalize"],
    "PLAN":   ["planner", "planning_reviewer", "memory_finalize"],
}

# Map event-log agent names → chain role keys
_AGENT_NAME_TO_ROLE: dict[str, str] = {
    "component-builder":      "builder",
    "bug-investigator":        "investigator",
    "code-reviewer":           "reviewer",
    "silent-failure-hunter":  "hunter",
    "integration-verifier":   "verifier",
    "doc-syncer":              "doc_syncer",
    "planner":                "planner",
    "plan-gap-reviewer":       "planning_reviewer",
    "router":                 "memory_finalize",  # memory_finalized event comes from router
}


def _agent_events_from_log(wf_id: str) -> dict[str, dict[str, Any]]:
    """Scan the events log and return {role: last_completed_event} from agent_completed entries."""
    path = hl.workflow_event_log_path(wf_id)
    if not path:
        return {}
    by_role: dict[str, dict] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev: dict = json.loads(raw)
                agent_name = ev.get("agent", "")
                event_name = ev.get("event", "")
                if event_name == "memory_finalized":
                    by_role["memory_finalize"] = ev
                elif event_name == "agent_completed" and agent_name:
                    role = _AGENT_NAME_TO_ROLE.get(agent_name, agent_name)
                    by_role[role] = ev
            except Exception:
                pass
    except Exception:
        pass
    return by_role


def _render_agent_chain(payload: dict[str, Any], wf_id: str) -> list[str]:
    wtype = (payload.get("workflow_type") or "BUILD").upper()
    chain = _CHAINS.get(wtype, ["builder", "reviewer", "verifier", "memory_finalize"])
    results: dict = payload.get("results") or {}
    task_ids: dict = payload.get("task_ids") or {}
    event_by_role = _agent_events_from_log(wf_id)
    lines: list[str] = []
    for role in chain:
        res: dict | None = results.get(role)
        tid = task_ids.get(role)
        ev = event_by_role.get(role)
        if isinstance(res, dict) and res:
            status = res.get("status", "")
            conf = res.get("confidence")
            conf_str = f" (conf {conf})" if conf else ""
            done = str(status).upper() in {"PASS", "APPROVE", "COMPLETE", "COMPLETED", "DONE"}
            icon = "✓" if done else "▶"
            lines.append(f"  {icon} {role}: {status}{conf_str}")
        elif ev:
            # Fallback: read from the events log (common on fast-path workflows)
            status = ev.get("status", "completed" if role == "memory_finalize" else "")
            conf = ev.get("confidence")
            conf_str = f" (conf {conf})" if conf else ""
            extra_parts = []
            for k in ("scenarios_passed", "scenarios_total"):
                if ev.get(k) is not None:
                    extra_parts.append(f"{k}={ev[k]}")
            extra_str = " " + " ".join(extra_parts) if extra_parts else ""
            done = str(status).upper() in {"PASS", "APPROVE", "COMPLETE", "COMPLETED", "DONE", ""}
            icon = "✓" if (done and status) or role == "memory_finalize" else "▶"
            lines.append(f"  {icon} {role}: {status or 'done'}{conf_str}{extra_str}")
        elif tid:
            lines.append(f"  ○ {role}: dispatched (task #{tid})")
        else:
            lines.append(f"  ○ {role}: pending")
    return lines


# ---------------------------------------------------------------------------
# Event log helpers
# ---------------------------------------------------------------------------

def _last_event_line(wf_id: str) -> str:
    """Return a short human-readable summary of the most recent event."""
    path = hl.workflow_event_log_path(wf_id)
    if not path:
        return "—"
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
        for raw in reversed(raw_lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev: dict = json.loads(raw)
                parts: list[str] = [ev.get("event", "?")]
                agent = ev.get("agent", "")
                if agent and agent != "router":
                    parts.append(agent)
                status = ev.get("status", "")
                if status:
                    parts.append(status)
                conf = ev.get("confidence")
                if conf:
                    parts.append(f"conf {conf}")
                return " · ".join(parts)
            except Exception:
                continue
    except Exception:
        pass
    return "—"


def _tail_events(wf_id: str, n: int = 12) -> list[str]:
    """Return the last N event lines formatted for display."""
    path = hl.workflow_event_log_path(wf_id)
    if not path:
        return ["  (no event log found)"]
    try:
        raw_lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        out: list[str] = []
        for raw in raw_lines[-n:]:
            try:
                ev: dict = json.loads(raw)
                ts = (ev.get("ts") or "")[-19:].replace("T", " ")
                ename = ev.get("event", "?")
                agent = ev.get("agent", "")
                skip = {"ts", "wf", "event", "agent", "task_id", "phase", "state_version", "decision", "reason"}
                extra = {k: v for k, v in ev.items() if k not in skip and v not in (None, "", [], {})}
                extra_str = "  " + json.dumps(extra, ensure_ascii=False)[:70] if extra else ""
                out.append(f"  {ts}  {ename:<30}  {agent:<18}{extra_str}")
            except Exception:
                out.append(f"  {raw[:110]}")
        return out or ["  (empty event log)"]
    except Exception as exc:
        return [f"  (error reading events: {exc})"]


# ---------------------------------------------------------------------------
# Narrative from progress.md / activeContext.md
# ---------------------------------------------------------------------------

def _read_narrative(wf_id: str) -> dict[str, list[str]]:
    """Read human-authored sections from per-workflow memory files."""
    result: dict[str, list[str]] = {}
    # Read directly without calling workflow_state_dir() to avoid an unnecessary mkdir
    wf_dir = hl.workflows_dir() / wf_id
    _FILES: list[tuple[str, list[str]]] = [
        ("activeContext.md", ["Current Focus", "Learnings"]),
        ("progress.md",      ["Current Workflow", "Tasks", "Completed", "Verification"]),
    ]
    for fname, section_names in _FILES:
        fpath = wf_dir / fname
        if not fpath.exists():
            continue
        try:
            text = fpath.read_text(encoding="utf-8")
            parsed = hl.parse_markdown_sections(text)
            for sec in section_names:
                if sec not in parsed:
                    continue
                bullets = hl.extract_bullets(parsed[sec])
                if not bullets:
                    # May be prose (e.g. "COMPLETE — BUILD ..."), not bullet list
                    prose = parsed[sec].strip()
                    if prose and "CRAFTFLOW_BLOCK" not in prose:
                        bullets = [prose[:200]]
                if bullets:
                    key = sec.lower().replace(" ", "_")
                    result[key] = bullets
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# Goal / request extraction
# ---------------------------------------------------------------------------

def _goal(payload: dict[str, Any], max_len: int = 120) -> str:
    intent: dict = payload.get("intent") or {}
    g = (intent.get("goal") or payload.get("user_request") or "").strip().replace("\n", " ")
    return g[:max_len] + ("…" if len(g) > max_len else "")


# Pattern for slug-style ids: wf-{slug}-{YYYYMMDD}-{HHMMSS}-{8hex}
_SLUG_ID_RE = re.compile(r'^wf-(.+)-\d{8}-\d{6}-[0-9a-f]{8}$', re.IGNORECASE)


def _display_label(wf_id: str, payload: dict[str, Any]) -> str:
    """Return a short human-readable label for the workflow.

    For slug-style ids (wf-{slug}-{YYYYMMDD}-{HHMMSS}-{8hex}): extract the slug.
    For old-format ids (wf-{timestamp}-{8hex}): fall back to truncated goal or
    the last 8 chars of the id.
    """
    m = _SLUG_ID_RE.match(wf_id)
    if m:
        return m.group(1)
    goal = _goal(payload, max_len=24)
    if goal:
        return goal
    return wf_id[-8:] if len(wf_id) >= 8 else wf_id


# ---------------------------------------------------------------------------
# Progress % computation
# ---------------------------------------------------------------------------

# Coarse stage-estimate %: used when phase lists are empty (common on real workflows)
_STAGE_PCT: dict[str, int] = {
    "workflow_started":        5,
    "fast_path_selected":     15,
    "planning":               25,
    "plan_saved":             25,
    "phase_started":          30,
    "phase_exit_gate_passed": 80,
    "integration_verified":   90,
    "memory_finalized":      100,
    "workflow_complete":      100,
}


def _compute_progress(payload: dict[str, Any], wf_id: str) -> dict[str, Any]:
    """Return {pct, done, total, source} for the workflow's completion %.

    Priority:
    1. DONE (authoritative) → 100%
    2. normalized_phases non-empty → count completed/skipped phases
    3. phase_status non-empty (no normalized_phases) → same counting on the map
    4. Coarse stage estimate from status_history tail event
    """
    # 1. Authoritative: workflow already completed
    if _classify(payload, wf_id) == "DONE":
        return {"pct": 100, "done": 1, "total": 1, "source": "done"}

    norm: list[dict] = payload.get("normalized_phases") or []
    pstatus: dict[str, str] = payload.get("phase_status") or {}

    # 2. Full phase list available
    if norm:
        total = len(norm)
        done = sum(
            1 for ph in norm
            if pstatus.get(ph.get("phase_id", "")) in {"completed", "skipped"}
        )
        pct = round(100 * done / total) if total else 0
        return {"pct": pct, "done": done, "total": total, "source": "phases"}

    # 3. phase_status map only (normalized_phases is empty — common on real workflows)
    if pstatus:
        total = len(pstatus)
        done = sum(1 for v in pstatus.values() if v in {"completed", "skipped"})
        pct = round(100 * done / total) if total else 0
        return {"pct": pct, "done": done, "total": total, "source": "status"}

    # 4. Coarse stage estimate
    history = payload.get("status_history") or []
    last_event = ""
    if history and isinstance(history[-1], dict):
        last_event = history[-1].get("event", "")
    pct = _STAGE_PCT.get(last_event, 0)
    if not pct and payload.get("phase_cursor"):
        pct = 20  # cursor is set but no matching stage event
    return {"pct": pct, "done": 0, "total": 0, "source": "stage"}


# ---------------------------------------------------------------------------
# Statusline segment (--statusline mode)
# ---------------------------------------------------------------------------

def _report_statusline(wf_id: str, payload: dict[str, Any]) -> str:
    """Return a single compact line for embedding in the statusline, or ''.

    Format: ⚡ {label} {pct}% · {state_icon} {phase_cursor} ({done}/{total})
    The ({done}/{total}) tail is omitted when total==0 (stage-estimate source).
    """
    state = _classify(payload, wf_id)
    icon = _STATE_ICONS[state].strip()  # strip table-padding spaces for inline use
    label = _display_label(wf_id, payload)
    prog = _compute_progress(payload, wf_id)
    pct = prog["pct"]
    done = prog["done"]
    total = prog["total"]
    cursor = payload.get("phase_cursor") or ""

    tail = f" ({done}/{total})" if total > 0 else ""
    phase_part = f"{icon} {cursor}{tail}" if cursor else f"{icon}{tail}"

    return f"⚡ {label} {pct}% · {phase_part}"


# ---------------------------------------------------------------------------
# Single-workflow report lines
# ---------------------------------------------------------------------------

def _report_one(wf_id: str, payload: dict[str, Any], verbose: bool) -> list[str]:
    state = _classify(payload, wf_id)
    icon = _STATE_ICONS[state]
    wtype = payload.get("workflow_type") or "?"
    age = _relative_time(payload.get("updated_at"), _artifact_mtime(wf_id))
    lines: list[str] = []

    lines.append(f"{icon}  {wtype:<7}  {wf_id}   updated {age}")
    lines.append(f"  goal: {_goal(payload)}")

    if state == "BLOCKED":
        gate = payload.get("pending_gate") or {}
        if isinstance(gate, dict):
            reason = gate.get("reason") or json.dumps(gate)
        else:
            reason = str(gate)
        lines.append(f"  ⛔ blocked: {reason}")

    lines.append(f"  {_render_phases_compact(payload)}   proof: {payload.get('proof_status') or '—'}")
    lines.append(f"  last: {_last_event_line(wf_id)}")
    lines.append(f"  worktree: {payload.get('worktree_branch') or payload.get('worktree_path') or '—'}")

    if not verbose:
        return lines

    # ── Verbose sections ──────────────────────────────────────────────────
    lines += ["", "  ── Phases ─────────────────────────────────────────────"]
    lines.extend(_render_phases_verbose(payload))

    lines += ["", "  ── Agent Chain ─────────────────────────────────────────"]
    lines.extend(_render_agent_chain(payload, wf_id))

    lines += ["", "  ── Quality ─────────────────────────────────────────────"]
    q: dict = payload.get("quality") or {}
    telem: dict = payload.get("telemetry") or {}
    loops: dict = telem.get("loop_counts") or {}
    lines.append(f"  proof:         {payload.get('proof_status') or '—'}")
    lines.append(f"  convergence:   {q.get('convergence_state') or '—'}")
    lines.append(f"  confidence:    {q.get('confidence') or '—'}")
    lines.append(f"  loop counts:   {json.dumps(loops) if loops else '—'}")

    lines += ["", "  ── Event Timeline (last 12) ────────────────────────────"]
    lines.extend(_tail_events(wf_id))

    narr = _read_narrative(wf_id)
    if narr:
        lines += ["", "  ── Narrative ───────────────────────────────────────────"]
        for key, bullets in narr.items():
            lines.append(f"  [{key}]")
            for b in bullets[:8]:
                lines.append(f"    {b.strip()}")

    return lines


# ---------------------------------------------------------------------------
# --all: summary table
# ---------------------------------------------------------------------------

def _report_all(payloads: list[tuple[str, dict[str, Any]]]) -> list[str]:
    hdr = f"{'STATE':<15}  {'WORKFLOW ID':<38}  {'TYPE':<7}  {'PHASE CURSOR':<22}  {'UPDATED':<10}  GOAL"
    sep = "─" * min(len(hdr) + 30, 140)
    lines = [hdr, sep]
    for wf_id, payload in payloads:
        state = _classify(payload, wf_id)
        icon = _STATE_ICONS[state]
        wtype = payload.get("workflow_type") or "?"
        cursor = payload.get("phase_cursor") or "—"
        age = _relative_time(payload.get("updated_at"), _artifact_mtime(wf_id))
        goal = _goal(payload, max_len=55)
        lines.append(f"{icon}  {wf_id:<38}  {wtype:<7}  {cursor:<22}  {age:<10}  {goal}")
    return lines


# ---------------------------------------------------------------------------
# Bulk load
# ---------------------------------------------------------------------------

def _load_all_workflows() -> list[tuple[str, dict[str, Any]]]:
    """Return all (wf_id, payload) sorted newest mtime first."""
    result: list[tuple[str, dict]] = []
    try:
        files = sorted(hl.workflows_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            try:
                payload: dict = json.loads(f.read_text(encoding="utf-8"))
                wf_id = payload.get("workflow_uuid") or payload.get("workflow_id") or f.stem
                result.append((wf_id, payload))
            except Exception:
                pass
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------

def _resolve_target(args: argparse.Namespace) -> tuple[str | None, dict[str, Any]]:
    """Return (wf_id, payload) for the selected workflow, or (None, {}) on failure."""

    # Explicit --wf ID
    if args.wf:
        payload, _, err = hl.read_workflow_state(args.wf)
        if err or not payload:
            print(f"ERROR: workflow {args.wf!r} not found or unreadable.", file=sys.stderr)
            return None, {}
        return args.wf, payload

    # Feature text search
    if args.feature:
        needle = args.feature.lower()
        matches: list[tuple[str, dict]] = []
        for wf_id, payload in _load_all_workflows():
            intent: dict = payload.get("intent") or {}
            searchable = " ".join([
                intent.get("goal") or "",
                payload.get("user_request") or "",
                wf_id,  # also match by slug embedded in the id
            ]).lower()
            if needle in searchable:
                matches.append((wf_id, payload))
        if not matches:
            print(f"No workflow found matching --feature {args.feature!r}.", file=sys.stderr)
            return None, {}
        if len(matches) > 1:
            print(f"  note: {len(matches)} workflows match — showing newest.", file=sys.stderr)
        return matches[0]  # already newest first

    # Worktree path or branch
    if args.worktree:
        wt = args.worktree
        all_wfs = _load_all_workflows()
        # Direct match on stored worktree fields
        for wf_id, payload in all_wfs:
            if payload.get("worktree_path") == wt or payload.get("worktree_branch") == wt:
                return wf_id, payload
        # Match the trailing 8-hex suffix (works for old worktree-wf-{hex} AND
        # new slug-based wf-{slug}-{hex} / {slug}-{hex} forms)
        m = re.search(r'-([0-9a-f]{8})$', wt, re.IGNORECASE)
        if m:
            suffix = m.group(1).lower()
            for wf_id, payload in all_wfs:
                if wf_id.lower().endswith(suffix):
                    return wf_id, payload
        print(f"No workflow found matching --worktree {args.worktree!r}.", file=sys.stderr)
        return None, {}

    # Default: precompact snapshot → newest by mtime
    precompact = hl.state_root() / "precompact-state.json"
    if precompact.exists():
        try:
            snap: dict = json.loads(precompact.read_text(encoding="utf-8"))
            wf_id = snap.get("workflow_uuid")
            if wf_id:
                payload, _, err = hl.read_workflow_state(wf_id)
                if not err and payload:
                    return wf_id, payload
        except Exception:
            pass

    latest = hl.latest_workflow_file()
    if latest is None:
        print("No workflows found in .craftflow/state/workflows/.", file=sys.stderr)
        return None, {}
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
        wf_id = payload.get("workflow_uuid") or payload.get("workflow_id") or latest.stem
        return wf_id, payload
    except Exception as exc:
        print(f"ERROR reading {latest}: {exc}", file=sys.stderr)
        return None, {}


# ---------------------------------------------------------------------------
# JSON output schema
# ---------------------------------------------------------------------------

def _build_json_output(wf_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_id":    wf_id,
        "workflow_type":  payload.get("workflow_type"),
        "state":          _classify(payload, wf_id),
        "phase_cursor":   payload.get("phase_cursor"),
        "phase_status":   payload.get("phase_status") or {},
        "proof_status":   payload.get("proof_status"),
        "quality":        payload.get("quality"),
        "pending_gate":   payload.get("pending_gate"),
        "updated_at":     payload.get("updated_at"),
        "worktree_branch":payload.get("worktree_branch"),
        "worktree_path":  payload.get("worktree_path"),
        "goal":           _goal(payload),
        "last_event":     _last_event_line(wf_id),
        "results":        payload.get("results") or {},
        "telemetry":      payload.get("telemetry") or {},
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="craftflow-status",
        description="Read-only Craftflow workflow status reporter — never writes anything.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 craftflow_status_report.py                         # current workflow
  python3 craftflow_status_report.py --verbose               # + agent chain, events, narrative
  python3 craftflow_status_report.py --all                   # all workflows summary table
  python3 craftflow_status_report.py --wf wf-20260702-...   # by explicit workflow ID
  python3 craftflow_status_report.py --feature "parallel"   # by feature name (substring)
  python3 craftflow_status_report.py --worktree wf-d4e5f6a7 # by worktree branch/suffix
  python3 craftflow_status_report.py --json                  # machine-readable JSON
  python3 craftflow_status_report.py --statusline            # one-line segment for the statusline
  python3 craftflow_status_report.py --project /path/to/proj # explicit project root
        """.strip(),
    )
    parser.add_argument("--wf",         metavar="ID",          help="Explicit workflow UUID")
    parser.add_argument("--feature",    metavar="TEXT",         help="Substring match on intent goal / user request / workflow id")
    parser.add_argument("--worktree",   metavar="PATH|BRANCH",  help="Match by worktree path or branch")
    parser.add_argument("--all",        action="store_true",    help="Show all workflows in a summary table")
    parser.add_argument("--verbose",    "-v", action="store_true", help="Agent chain, event timeline, narrative")
    parser.add_argument("--json",       action="store_true",    help="Emit structured JSON (for tooling / statusline)")
    parser.add_argument("--statusline", action="store_true",    help="Single-line progress segment for the statusline (⚡ label pct% · state phase)")
    parser.add_argument("--project",    metavar="DIR",          help="Project root (auto-detected if omitted)")
    args = parser.parse_args()

    # Wire project root into hooklib before any hl.* call
    root = _resolve_project_root(args.project)
    if root is not None:
        os.environ["CLAUDE_PROJECT_DIR"] = str(root)

    # --all: summary table over every workflow
    if args.all:
        all_wfs = _load_all_workflows()
        if not all_wfs:
            print("No workflows found.", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps([_build_json_output(wid, p) for wid, p in all_wfs], indent=2, ensure_ascii=False))
            return 0
        for line in _report_all(all_wfs):
            print(line)
        return 0

    # --statusline: fast single-line segment for the statusline wrapper.
    # Resolves via precompact-state → newest-by-mtime only; no --all.
    # Prints nothing (exit 0) when there is no active workflow — the wrapper
    # then shows only claude-hud output.
    if args.statusline:
        wf_id, payload = _resolve_target(args)
        if not wf_id:
            return 0  # silent exit — wrapper degrades to hud-only
        print(_report_statusline(wf_id, payload))
        return 0

    # Single-workflow mode
    wf_id, payload = _resolve_target(args)
    if not wf_id:
        return 1

    if args.json:
        print(json.dumps(_build_json_output(wf_id, payload), indent=2, ensure_ascii=False))
        return 0

    for line in _report_one(wf_id, payload, verbose=args.verbose):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
