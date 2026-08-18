#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import re
import shlex
import sys
import contextlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

STATE_VERSION = "v10"


def project_dir() -> Path:
    value = os.environ.get("CLAUDE_PROJECT_DIR")
    if value:
        return Path(value)
    return Path.cwd()


def plugin_root() -> Path:
    value = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if value:
        return Path(value)
    return Path(__file__).resolve().parents[1]


def plugin_config_dir() -> Path:
    return plugin_root() / "config"


def state_root() -> Path:
    path = project_dir() / ".craftflow" / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def workflows_dir() -> Path:
    path = state_root() / "workflows"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_state_dir() -> Path:
    """Long-lived cross-workflow state: .craftflow/state/project/"""
    path = state_root() / "project"
    path.mkdir(parents=True, exist_ok=True)
    return path


def workflow_state_dir(workflow_id: str) -> Path:
    """Per-workflow isolated state: .craftflow/state/workflows/<wf-id>/"""
    path = workflows_dir() / workflow_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = state_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_input() -> Dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    # REM-FIX cycle 4 (silent-failure-hunter, live-reproduced CRITICAL): json.loads()
    # only guarantees valid JSON was parsed -- NOT that the top level is a dict (the same
    # Bug A pattern already fixed for latest_workflow_payload() in earlier cycles). Every
    # caller of load_input() immediately does `data.get(...)`, which crashes with an
    # uncaught AttributeError on a non-dict top level (e.g. `[1, 2, 3]`), before either
    # guard script's main() -- which has no top-level try/except -- can emit any deny
    # decision. Coerce to {}, matching this function's own existing missing-stdin default.
    return parsed if isinstance(parsed, dict) else {}


def load_mode() -> Dict[str, str]:
    path = plugin_config_dir() / "hook-mode.json"
    if not path.exists():
        # REM-FIX (HIGH): protectedWrites is a NEW protection closing a gap --
        # it must fail closed (block), not silently reopen the gap it exists
        # to close, when config is missing.
        return {
            "protectedWrites": "block",
            "memoryWrites": "audit",
            "taskMetadata": "audit",
        }
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"CRAFTFLOW load_mode: failed to read {path}: {exc}; defaulting to fail-closed mode", file=sys.stderr)
        # REM-FIX (HIGH): same fail-closed rationale as the missing-file
        # branch above -- a corrupt/unreadable config must not silently
        # default protectedWrites back to audit-only.
        return {
            "protectedWrites": "block",
            "memoryWrites": "audit",
            "taskMetadata": "audit",
        }
    # REM-FIX cycle 4 (silent-failure-hunter, live-reproduced CRITICAL): identical
    # dict-coercion gap as load_input() above -- a hook-mode.json that is valid JSON but
    # not a dict (e.g. `[1, 2, 3]`) crashed the first `mode.get(...)` call downstream.
    # Every caller already passes an explicit default to `.get(key, default)` that
    # matches this function's own fallback dicts above, so coercing to {} here (rather
    # than duplicating the fail-closed dict) reproduces identical caller-observed
    # behavior without disturbing the missing-file/corrupt-file branches above.
    return parsed if isinstance(parsed, dict) else {}


def resolve_toggle_decision(
    toggle_value, fail_closed_on_unrecognized: bool = False
) -> Tuple[bool, str]:
    """Enum-validate a `block`/`audit` hook-mode.json toggle value and
    compute both the gating decision (`should_block`) and the log_event
    `decision` string. Shared by every guard-script toggle that reads a
    `block`/`audit` enum from hook-mode.json (`memoryWrites`,
    `protectedWrites` in craftflow_pretooluse_guard.py;
    `bashDestructiveTraversal` in craftflow_pretooluse_bash_guard.py) --
    gating BEHAVIOR stays entirely per-caller (this helper never inspects
    the underlying violations, only the toggle value itself), only the
    enum-validation + decision-string logic is shared.

    Recognized values ("block"/"audit") always map to `should_block`
    True/False and decision "deny"/"audit".

    An unrecognized value (a typo, e.g. "Block" capital-B, "blocked", a
    boolean) must never be silently indistinguishable in
    craftflow-hook-events.log from an intentional, recognized choice.
    `fail_closed_on_unrecognized` selects which posture an unrecognized
    value degrades to:

    - False (default; `memoryWrites`/`protectedWrites`): degrades to
      `should_block=False` (fail OPEN -- same gating outcome as an
      intentional "audit" choice) but logs a DISTINCT
      "audit-unrecognized-config-value" decision so the misconfiguration
      is still greppable.
    - True (`bashDestructiveTraversal`): degrades to `should_block=True`
      (fail CLOSED) with a DISTINCT "block-unrecognized-config-value"
      decision. This is a deliberate DIVERGENCE from the
      memoryWrites/protectedWrites default, not a copy of it:
      `bashDestructiveTraversal`'s own missing-key behavior already
      fail-closes (`mode.get("bashDestructiveTraversal", "block")` --
      the default when the key is absent entirely is "block", not
      "audit"). An unrecognized-but-PRESENT value (a typo) should fail
      exactly the same way a missing key does -- both are "the config
      didn't give us a clear answer" -- rather than silently flipping to
      the OPPOSITE, more permissive posture ("audit-degrade") just
      because a key happened to be present with a bad value. Consistency
      is with the toggle's OWN established missing-key default, not with
      the other toggles' unrelated unrecognized-value default.
    """
    should_block = toggle_value == "block"
    if toggle_value in ("block", "audit"):
        decision = "deny" if should_block else "audit"
    elif fail_closed_on_unrecognized:
        should_block = True
        decision = "block-unrecognized-config-value"
    else:
        decision = "audit-unrecognized-config-value"
    return should_block, decision


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(name: str, payload: Dict[str, Any]) -> None:
    try:
        path = logs_dir() / "craftflow-hook-events.log"
        event = {
            "ts": now_iso(),
            "event": name,
            "state_version": STATE_VERSION,
            **payload,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=True) + "\n")
    except Exception:
        pass  # never fail the hook


# ---------------------------------------------------------------------------
# "Latest workflow" selection
# ---------------------------------------------------------------------------
# latest_workflow_file()/latest_workflow_payload()/read_latest_workflow_state()
# below are the ORIGINAL, UNFILTERED "true newest by mtime" selectors --
# every non-security consumer (craftflow_stop_persist.py,
# craftflow_precompact_state.py, craftflow_sessionstart_context.py,
# craftflow_posttooluse_artifact_guard.py, craftflow_status_report.py,
# craftflow_postcompact_context.py, craftflow_stop_failure_log.py) depends on
# this exact "newest by mtime, including terminal workflows" semantic and
# must NOT be affected by liveness filtering (code-reviewer CHANGES_REQUESTED,
# Finding 1, REM-FIX cycle 1: liveness filtering had been applied
# UNCONDITIONALLY here, silently changing behavior for every one of these
# callers even though only the write-confinement-sensitive callers needed
# it).
#
# `latest_live_workflow_file()`/`latest_live_workflow_payload()`/
# `read_latest_live_workflow_state()` further down are the fail-closed,
# liveness-filtered variants (Item A fix: cross-session workflow-identity
# leak) -- used ONLY by the 4 write-confinement-sensitive call sites in
# craftflow_pretooluse_guard.py (3) and craftflow_pretooluse_bash_guard.py
# (1). See docs/2026-07-30-craftflow-guard-write-detection-limitations-
# decision.md for the SEPARATE, out-of-scope command-enumeration bypass
# class this does NOT touch.
#
# Before the Item A fix, `latest_workflow_file()` picked whichever *.json
# under .craftflow/state/workflows/ had the single most recent mtime,
# globally -- with NO filtering by liveness or session identity. A workflow
# that had already finished (worktree merged and removed) could still "win"
# if its JSON file's mtime was bumped later for an unrelated reason (e.g. a
# memory-notes append), leaking its stale/removed `worktree_path` into an
# unrelated invocation's write-confinement check and fail-OPENing writes to
# the wrong worktree instead of fail-CLOSING to cwd/scratchpad. That
# staleness risk is real ONLY for write-confinement decisions -- the
# non-security consumers above only ever read/report workflow state, so the
# pre-existing "true newest by mtime" semantic remains correct for them.

# Terminal worktree_mode values the router itself writes once a workflow's
# worktree has been merged/removed and is no longer a legitimate write
# target for ANY invocation (verified live against this repo's own
# .craftflow/state/workflows/*.json corpus, 2026-08-18: 78/221 files carry
# "merged_and_removed", 1 carries "removed_no_merge_needed"; "auto_created"/
# "existing_worktree" mean the worktree is still in active use;
# "skipped_main_tree"/None mean no worktree was ever created for that
# workflow, which is not itself a staleness signal).
TERMINAL_WORKTREE_MODES = {"merged_and_removed", "removed_no_merge_needed"}

# Bounds the cost of the liveness scan below to a small, constant-ish
# number of file reads on the common (non-pathological) path, regardless of
# how many historical workflow artifacts have accumulated under
# .craftflow/state/workflows/ (this repo alone has 221+ as of 2026-08-18) --
# a PreToolUse hook runs on every Edit/Write/Bash call and must stay fast.
_LATEST_WORKFLOW_SCAN_WINDOW = 50


def _workflow_payload_is_live(payload: Dict[str, Any]) -> bool:
    """True if a workflow artifact payload is still a legitimate candidate
    for "the active workflow for this invocation" selection. Excludes:
    (1) a terminal `worktree_mode` (the router's own signal that the
    worktree is gone); (2) a `worktree_path` that is a non-empty string
    but whose directory no longer exists on disk -- a stronger,
    filesystem-truth-based signal that catches the terminal case even when
    `worktree_mode` was never updated (e.g. an interrupted/crashed merge).
    A workflow with `worktree_path` None/"" (main-tree-only -- e.g. a
    DEBUG/PLAN fast path that never spawned a worktree) is always live:
    there is no worktree to go stale."""
    if not isinstance(payload, dict):
        return False
    if payload.get("worktree_mode") in TERMINAL_WORKTREE_MODES:
        return False
    worktree_path = payload.get("worktree_path")
    if isinstance(worktree_path, str) and worktree_path:
        try:
            if not Path(worktree_path).is_dir():
                return False
        except Exception:
            return False
    return True


def _live_workflow_candidates(paths) -> List[Tuple[Path, Dict[str, Any]]]:
    """Parse each candidate path and keep only the live ones (see
    `_workflow_payload_is_live()`), preserving the caller's ordering.
    Corrupt/unreadable/non-dict JSON is skipped, never raised -- matches
    this module's established fail-toward-stricter-not-crash posture."""
    live: List[Tuple[Path, Dict[str, Any]]] = []
    for candidate in paths:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if not _workflow_payload_is_live(payload):
            continue
        live.append((candidate, payload))
    return live


def _any_session_id_key_present(paths) -> bool:
    """True if at least one candidate JSON payload in `paths` -- LIVE OR
    NOT -- carries a `session_id` key at all (regardless of its value).
    Deliberately not restricted to live candidates: this answers "does the
    producer side populate session_id for anything in this window", not
    "does a live, session-scoping-eligible candidate exist" -- a window
    where every candidate happens to be terminal/dead must not be
    conflated with a window that genuinely never carries the key (the
    former should still widen past the window to find a live candidate
    further back; the latter should not, per REM-FIX cycle 3)."""
    for candidate in paths:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict) and "session_id" in payload:
            return True
    return False


def latest_workflow_payload() -> Dict[str, Any]:
    """ORIGINAL, unfiltered "true newest by mtime" selector -- see this
    section's module-level docstring. Non-security consumers only; write-
    confinement-sensitive callers use `latest_live_workflow_payload()`
    instead."""
    payload, _, _ = read_latest_workflow_state()
    return payload


def latest_workflow_file() -> Path | None:
    """ORIGINAL, unfiltered "true newest by mtime" selector -- see this
    section's module-level docstring. Non-security consumers only; write-
    confinement-sensitive callers use `latest_live_workflow_file()`
    instead."""
    files = sorted(
        workflows_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not files:
        return None
    return files[0]


def read_latest_workflow_state() -> Tuple[Dict[str, Any], Path | None, str | None]:
    """ORIGINAL, unfiltered "true newest by mtime" selector -- see this
    section's module-level docstring. Non-security consumers only; write-
    confinement-sensitive callers use `read_latest_live_workflow_state()`
    instead."""
    latest = latest_workflow_file()
    if latest is None:
        return {}, None, None
    try:
        return json.loads(latest.read_text(encoding="utf-8")), latest, None
    except Exception as exc:
        return {}, latest, exc.__class__.__name__


def latest_live_workflow_payload(session_id: str | None = None) -> Dict[str, Any]:
    payload, _, _ = read_latest_live_workflow_state(session_id)
    return payload


def latest_live_workflow_file(session_id: str | None = None) -> Path | None:
    """Select the workflow artifact JSON this hook invocation should treat
    as "the active workflow" for confinement purposes.

    Two-tier selection, both restricted to LIVE candidates only (see
    `_workflow_payload_is_live()`):

      1. If `session_id` is given, prefer the mtime-latest live candidate
         whose own `session_id` field matches exactly. The PreToolUse hook
         payload's own `session_id` field (a documented common field on
         every Claude Code hook invocation) is the caller-supplied value
         here -- but as of 2026-08-18 the router does not yet stamp
         `session_id` into a workflow artifact at creation time, so no
         candidate will ever match and this tier is currently a no-op.
         Threading it through now means true session-scoping activates
         automatically, with zero further guard changes, the moment that
         router-side follow-up ships (tracked as a deferred item -- see
         the bug investigation this fixes).
      2. Otherwise (or if no session match), fall back to the mtime-latest
         LIVE candidate -- the fail-safe minimum bar: a workflow whose
         worktree is already gone is NEVER selected as "latest", even if
         its JSON file happens to be the most-recently-touched one on
         disk.

    Returns None if there are zero live candidates (even when non-live
    files exist) -- callers already degrade a None workflow to cwd-only
    confinement (fail CLOSED to cwd/scratchpad, see
    `resolve_confinement()`), which is exactly the desired posture when no
    confidently-resolvable active workflow exists for this invocation,
    rather than fail-OPEN to a stale `worktree_path`.

    Scan-window fallback (doubt-verify cycle 1, item 2, REM-FIX cycle 2):
    the bounded `_LATEST_WORKFLOW_SCAN_WINDOW` must never let an
    unrelated-but-live in-window candidate silently mask the true answer.
    Two cases, handled separately below:

      - `session_id` given: the window is a fast-path optimization for the
        common case (a matching, still-live workflow found within it). If
        NO in-window live candidate matches `session_id` AND at least one
        in-window candidate (live or not) carries a `session_id` key at
        all, the scan widens to the rest of the directory before falling
        back to an unrelated live candidate -- a session match older than
        the window must not be missed just because some other, unrelated,
        live workflow happened to occupy every window slot. Gating on
        "does the key exist in the window" rather than "did it match" is
        deliberate (REM-FIX cycle 3, CRITICAL): as of 2026-08-18, 0/222
        real workflow artifacts under .craftflow/state/workflows/ carry a
        `session_id` key at all, yet every Claude Code hook payload
        supplies a real session_id -- so gating on "matched" alone made
        the widen (and its full-directory-scan cost) fire unconditionally
        on every PreToolUse invocation. "Does any in-window candidate
        carry the key at all" is the true distinguishing signal: it still
        widens correctly once the producer side starts populating
        `session_id` (even with non-matching values from other sessions),
        while collapsing back to the cheap bounded-window path against
        today's real corpus, where the key is never present. The key-
        presence check deliberately looks at ALL in-window candidates, not
        just live ones (`_any_session_id_key_present()`), so a window
        whose live candidates happen to be too few (or zero, e.g. every
        in-window mtime slot is a terminal workflow) never silently
        suppresses the widen-to-find-a-live-candidate-below-window
        fallback that REM-FIX cycle 2 relies on.
      - `session_id` absent (today's actual production reality -- the
        router does not yet stamp `session_id` into workflow artifacts):
        this heuristic cannot distinguish "the workflow driving this
        invocation" from "an unrelated-but-more-recently-touched live
        workflow" no matter how far it scans -- that ambiguity is a
        separate, disclosed, out-of-scope limitation (liveness-heuristic
        forgeability/session-scoping, see this section's module
        docstring). What IS in scope: not letting the bounded window
        silently narrow the candidate pool below the full population.
        Widens to a full-directory live scan unconditionally in this
        branch -- restores the pre-Item-A-fix full-scan cost profile
        (measured ~100ms against this repo's own 221-file
        .craftflow/state/workflows/ corpus, 2026-08-18; comparable to the
        ~306ms this repo's own investigation already treated as an
        acceptable per-invocation cost for a security-relevant lookup),
        for this path only."""
    candidates = sorted(
        workflows_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not candidates:
        return None

    if not session_id:
        full_live = _live_workflow_candidates(candidates)
        return full_live[0][0] if full_live else None

    window_candidates = candidates[:_LATEST_WORKFLOW_SCAN_WINDOW]
    live_window = _live_workflow_candidates(window_candidates)
    for candidate, payload in live_window:
        if payload.get("session_id") == session_id:
            return candidate

    # Only widen past the window when there is a realistic chance a wider
    # scan could actually find a session match -- i.e. at least one
    # in-window candidate (live or not -- see `_any_session_id_key_present()`)
    # carries a `session_id` key AT ALL (REM-FIX cycle 3, CRITICAL: gating
    # on "did any candidate MATCH" instead of "does the key exist at all"
    # made the widen fire unconditionally in production, since 0/222 real
    # workflow artifacts carry `session_id` yet every hook payload supplies
    # one -- see this function's docstring). When the window itself never
    # populates the key, no amount of scanning further back changes that;
    # widening would only add full-directory-scan cost for zero
    # correctness gain.
    remainder = candidates[_LATEST_WORKFLOW_SCAN_WINDOW:]
    live_remainder: List[Tuple[Path, Dict[str, Any]]] = []
    if remainder and _any_session_id_key_present(window_candidates):
        live_remainder = _live_workflow_candidates(remainder)
        for candidate, payload in live_remainder:
            if payload.get("session_id") == session_id:
                return candidate

    if live_window:
        return live_window[0][0]
    return live_remainder[0][0] if live_remainder else None


def read_latest_live_workflow_state(
    session_id: str | None = None,
) -> Tuple[Dict[str, Any], Path | None, str | None]:
    latest = latest_live_workflow_file(session_id)
    if latest is None:
        return {}, None, None
    try:
        return json.loads(latest.read_text(encoding="utf-8")), latest, None
    except Exception as exc:
        return {}, latest, exc.__class__.__name__


def workflow_artifact_path(workflow_id: str | None) -> Path | None:
    if not workflow_id:
        return None
    path = workflows_dir() / f"{workflow_id}.json"
    if not path.exists():
        return None
    return path


def workflow_event_log_path(workflow_id: str | None) -> Path | None:
    if not workflow_id:
        return None
    path = workflows_dir() / f"{workflow_id}.events.jsonl"
    if not path.exists():
        return None
    return path


def read_workflow_state(
    workflow_id: str | None,
) -> Tuple[Dict[str, Any], Path | None, str | None]:
    path = workflow_artifact_path(workflow_id)
    if path is None:
        return {}, None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), path, None
    except Exception as exc:
        return {}, path, exc.__class__.__name__


def workflow_event_log_contains(workflow_id: str | None, needle: str) -> bool:
    path = workflow_event_log_path(workflow_id)
    if path is None:
        return False
    try:
        return needle in path.read_text(encoding="utf-8")
    except Exception:
        return False


def workflow_event_log_exists(payload: Dict[str, Any], artifact_path: Path) -> bool:
    workflow_uuid = payload.get("workflow_uuid") or payload.get("workflow_id")
    if not workflow_uuid:
        workflow_uuid = artifact_path.stem
    event_log = workflows_dir() / f"{workflow_uuid}.events.jsonl"
    return event_log.exists()


def workflow_artifact_is_fresh(path: Path, max_age_seconds: int = 60) -> bool:
    try:
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age <= max_age_seconds


# ---------------------------------------------------------------------------
# Consecutive-denial hard stop (Item B fix)
# ---------------------------------------------------------------------------
# Before this fix there was no tracking of repeated denials anywhere in this
# guard: an agent whose Edit/Write (or guard-detected Bash write) was denied
# could immediately retry the identical target via a different tool/surface,
# with nothing pausing for human input in between. This does NOT attempt to
# detect a bypass via an un-enumerated Bash command (e.g. `git apply`) --
# that whole command-enumeration approach is a SEPARATE, explicitly paused
# class (see docs/2026-07-30-craftflow-guard-write-detection-limitations-
# decision.md). This mechanism only escalates denials THIS guard itself
# already detects and emits.
#
# Granularity choice (documented per the investigation's own instruction):
# a "logical write action" is keyed by (session_id, resolved absolute
# target path). Session-scoped so two independent/parallel sessions denied
# on the same path never cross-contaminate each other's counters;
# path-scoped (not e.g. workflow_uuid-scoped) because the motivating
# incident was "the SAME file kept getting denied" -- a different target
# path is a genuinely different logical action and starts its own count.
#
# What "hard stop" means from a stateless PreToolUse hook: a hook can only
# allow/deny the ONE tool call it was invoked for -- it cannot halt the
# agent's turn outright. The achievable mechanism (one of the two options
# the investigation's own instructions floated) is: the 2nd+ consecutive
# denial for the same logical action is escalated -- a qualitatively
# different, more forceful deny message plus a distinct `log_event`
# decision ("deny-escalated") -- rather than an identical-looking ordinary
# deny. This is NOT a claim that the agent is structurally prevented from
# trying a third time; it is the escalated signal the instructions
# describe as an acceptable structural discouragement.
DENIAL_ESCALATION_THRESHOLD = 2

# A denial older than this starts a FRESH count instead of continuing to
# accumulate indefinitely -- satisfies the "or after enough time" reset
# condition without needing a session-end hook to clear state explicitly.
DENIAL_TRACKER_TTL_SECONDS = 30 * 60


def denial_tracker_path() -> Path:
    """Guard-owned internal bookkeeping file for the consecutive-denial
    hard stop. Written directly by this Python process's own file I/O --
    never routed back through the Edit/Write/Bash tool-call surface this
    guard itself intercepts, so there is no self-blocking circularity.
    Deliberately NOT a markdown memory file and NOT a workflow JSON
    artifact -- excluded from both `_protected_memory_paths()` and
    `_protected_bash_write_paths()` in craftflow_pretooluse_guard.py."""
    return state_root() / ".denial-tracker.json"


def _denial_tracker_lock_path() -> Path:
    """Dedicated lock file, separate from the tracker JSON itself -- so a
    process that crashes mid-write never leaves the ACTUAL tracker data
    file (as opposed to an empty/inert lock file) in a locked-and-partial
    state that could confuse a future read. Locking the JSON data file
    directly would also mean any reader/writer that opens it via
    `.read_text()`/`.write_text()` (bypassing the lock) could still race;
    the dedicated lock file makes the discipline explicit: EVERY read-
    modify-write of the tracker must go through this lock."""
    return denial_tracker_path().with_name(".denial-tracker.lock")


@contextlib.contextmanager
def _denial_tracker_locked():
    """Exclusive lock around a denial-tracker read-modify-write.

    Finding 3 (code-reviewer MEDIUM, REM-FIX cycle 1): `_load_denial_tracker()`
    / `_save_denial_tracker()` previously had no coordination between the
    read and the write -- two concurrent PreToolUse invocations in the same
    session could both read the same stale count, both increment locally,
    and both write back the same incremented value, silently losing one of
    the two denials and potentially missing the escalation ("ESCALATED /
    HARD STOP") signal when it should have fired. This does NOT affect the
    actual allow/deny decision for either invocation -- only the escalation
    *messaging* reliability (already confirmed independent by the
    investigation this fixes).

    `fcntl.flock` on a dedicated lock file is deliberately the simplest
    correct primitive for this: single-machine, single-user local file
    coordination, not a distributed system. The lock is released
    automatically when the `with` block exits, even on exception, so a
    caller's own failure never leaves the tracker permanently locked."""
    lock_path = _denial_tracker_lock_path()
    try:
        lock_path.touch(exist_ok=True)
        fh = open(lock_path, "r+", encoding="utf-8")
    except Exception:
        # Lock file itself unwritable/unreadable (e.g. permissions) --
        # degrade to unlocked (matches this module's established
        # fail-toward-stricter-not-crash posture: the denial recording
        # itself must never crash the hook; losing exclusivity here only
        # risks the SAME under-counting this fix narrows, not a hard
        # failure).
        yield
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


def _load_denial_tracker() -> Dict[str, Any]:
    path = denial_tracker_path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _save_denial_tracker(tracker: Dict[str, Any]) -> None:
    try:
        denial_tracker_path().write_text(
            json.dumps(tracker, ensure_ascii=True), encoding="utf-8"
        )
    except Exception:
        pass  # never fail the hook on a bookkeeping write error


def denial_action_key(session_id: str | None, target: str) -> str:
    """'Logical write action' identity for the consecutive-denial hard
    stop -- see this section's module-level docstring for the granularity
    rationale.

    Case-normalizes `target` (doubt-verify cycle 1, item 3, REM-FIX cycle
    2): on the case-insensitive default macOS/APFS volume,
    `str(Path(...).resolve())` preserves the caller-supplied case rather
    than normalizing it, even though two differently-cased spellings of
    the same path share the same underlying inode (confirmed empirically
    against this repo's own filesystem: `Path("foo.md").resolve()` and
    `Path("FOO.md").resolve()` are different Python strings but the same
    inode). A trivial case variation in a retried path was silently
    resetting the consecutive-denial counter, defeating the escalation
    this mechanism exists to provide. Lowercasing here is scoped to THIS
    tracker's key only -- it does NOT touch the guard's broader
    `_protected_memory_paths()`/protected-path set-membership matching (a
    separate, pre-existing, disclosed, out-of-scope item). Normalizes
    unconditionally rather than runtime-detecting filesystem
    case-sensitivity: a false-positive "same key" for two genuinely
    different, differently-cased files on a case-SENSITIVE filesystem is a
    much smaller cost than the current false-negative on a
    case-INSENSITIVE one."""
    return f"{session_id or 'nosession'}::{target.lower()}"


def record_denial(session_id: str | None, target: str) -> Tuple[int, bool]:
    """Record one denial for `target` under `session_id`. Returns
    `(count, escalated)` -- `escalated` is True once `count` reaches
    `DENIAL_ESCALATION_THRESHOLD`. A denial older than
    `DENIAL_TRACKER_TTL_SECONDS` starts a NEW sequence (count resets to 1)
    rather than accumulating indefinitely. Never raises -- a corrupt
    tracker file degrades to an empty one (fresh count), matching this
    module's established fail-toward-stricter-not-crash posture (the
    denial itself, from the caller's own violation check, is unaffected
    either way). The whole read-modify-write is wrapped in
    `_denial_tracker_locked()` (Finding 3, REM-FIX cycle 1) so concurrent
    PreToolUse invocations in the same session never race each other's
    read against a not-yet-written increment."""
    key = denial_action_key(session_id, target)
    with _denial_tracker_locked():
        tracker = _load_denial_tracker()
        entry = tracker.get(key)
        now = datetime.now(timezone.utc).timestamp()
        count = 1
        if isinstance(entry, dict):
            last_ts = entry.get("last_ts")
            prior_count = entry.get("count")
            if (
                isinstance(prior_count, int)
                and isinstance(last_ts, (int, float))
                and (now - last_ts) <= DENIAL_TRACKER_TTL_SECONDS
            ):
                count = prior_count + 1
        tracker[key] = {"count": count, "last_ts": now}
        _save_denial_tracker(tracker)
    return count, count >= DENIAL_ESCALATION_THRESHOLD


def clear_denial(session_id: str | None, target: str) -> None:
    """Reset the consecutive-denial count for `target` -- called whenever
    the guard ALLOWS a write to that same target. From a stateless
    PreToolUse hook's vantage point, this guard's own ALLOW decision is the
    closest available signal to "the equivalent write succeeded" (the
    hook cannot observe the tool's actual post-execution result), which is
    the documented reset condition. Locked for the same TOCTOU reason as
    `record_denial()` above."""
    key = denial_action_key(session_id, target)
    with _denial_tracker_locked():
        tracker = _load_denial_tracker()
        if key in tracker:
            del tracker[key]
            _save_denial_tracker(tracker)


def parse_metadata(description: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in description.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {"wf", "kind", "origin", "phase", "plan", "scope", "reason"}:
            values[key] = value.strip()
    return values


def json_print(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True))


def pretool_deny(reason: str) -> None:
    json_print(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )


def session_context(message: str) -> None:
    json_print(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": message,
            }
        }
    )


def parse_markdown_sections(text: str) -> Dict[str, str]:
    """Split markdown on ``## `` lines into {section_name: content_below}."""
    sections: Dict[str, str] = {}
    current: str | None = None
    lines: List[str] = []
    for raw_line in text.splitlines():
        if raw_line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines)
            current = raw_line[3:].strip()
            lines = []
        else:
            lines.append(raw_line)
    if current is not None:
        sections[current] = "\n".join(lines)
    return sections


def extract_bullets(section_content: str) -> List[str]:
    """Return all ``- `` prefixed lines from a section body."""
    return [
        line for line in section_content.splitlines() if line.lstrip().startswith("- ")
    ]


def normalize_bullet(line: str) -> str:
    """Strip ``- `` prefix, collapse whitespace, and lowercase for dedup."""
    text = line.lstrip()
    if text.startswith("- "):
        text = text[2:]
    return re.sub(r"\s+", " ", text).strip().lower()


# ---------------------------------------------------------------------------
# Memory finalization permit
# ---------------------------------------------------------------------------
# The pretooluse guard blocks direct writes to protected memory .md files.
# During router-owned memory finalization the router creates this permit so
# the guard can distinguish its own legitimate writes from unauthorized ones.

def memory_finalize_permit_path() -> Path:
    """Non-protected sentinel: .craftflow/state/.memory-finalize"""
    return state_root() / ".memory-finalize"


def set_memory_finalize_permit(workflow_uuid: str) -> None:
    """Write the permit token so the pretooluse guard allows memory writes."""
    try:
        memory_finalize_permit_path().write_text(workflow_uuid, encoding="utf-8")
    except Exception:
        pass


def clear_memory_finalize_permit() -> None:
    """Remove the permit token after memory finalization is done."""
    try:
        memory_finalize_permit_path().unlink(missing_ok=True)
    except Exception:
        pass


def has_memory_finalize_permit(workflow_uuid: str | None = None) -> bool:
    """Return True if a valid permit exists for the given workflow UUID.

    When workflow_uuid is None, presence of the file alone is accepted.
    """
    permit = memory_finalize_permit_path()
    if not permit.exists():
        return False
    if workflow_uuid is None:
        return True
    try:
        return permit.read_text(encoding="utf-8").strip() == workflow_uuid
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Confinement & command-parsing helpers
# ---------------------------------------------------------------------------
# Shared by craftflow_pretooluse_bash_guard.py (Bash-tool targets) and
# craftflow_pretooluse_guard.py (Edit/Write + Bash-write-inspection targets).
# See docs/plans/2026-07-28-craftflow-guardrail-hardening-plan.md for the
# behavior contract these functions implement.

CONTROL_OPERATORS = {";", "&&", "||", "|", "&", "\n"}


def claude_code_own_memory_root(cwd: Path) -> Path:
    """Deterministic, non-spoofable root directory for Claude Code's own
    global auto-memory system for THIS cwd:
    ~/.claude/projects/<slug-of-cwd>/, where <slug> is the absolute cwd
    string with every '/' and '.' replaced by '-' (verified empirically
    against real ~/.claude/projects/* directories on disk -- e.g. cwd
    /Users/david.gracia/Desktop/projects/own/ai-craft maps to
    -Users-david-gracia-Desktop-projects-own-ai-craft; note '.' in
    "david.gracia" is ALSO replaced, not just '/').

    Always derived from the caller's own trusted `cwd` argument -- never
    from the path being checked -- so it can never be spoofed by tool-call
    input."""
    slug = "".join("-" if c in "/." else c for c in str(cwd))
    return Path.home() / ".claude" / "projects" / slug


def _within_claude_code_own_memory_dir(resolved: Path, cwd: Path) -> bool:
    """True if `resolved` is Claude Code's own auto-memory MEMORY.md file,
    or lives at/under its memory/ subtree, scoped EXACTLY to the trusted
    `cwd`'s own slug -- never a blanket ~/.claude/projects/** grant (that
    would leak into other projects' auto-memory), and never based on a
    caller-supplied/spoofable slug.

    Also permits the SESSION-scoped shape observed live after a
    context-compaction resume, where Claude Code computes its own
    auto-memory root one directory level deeper, keyed by session uuid:
    <root>/<session-uuid>/memory/** and <root>/<session-uuid>/MEMORY.md.
    The session-uuid segment itself is never validated against any known
    id -- it is an arbitrary single path component -- because the grant
    stays fully contained within the already-non-spoofable `root` (derived
    only from the trusted `cwd`, never the candidate path), so widening the
    pattern within that root introduces no additional spoofability, exactly
    like the project-scoped `<root>/memory/**` grant above."""
    root = claude_code_own_memory_root(cwd)
    memory_md = root / "MEMORY.md"
    memory_dir = root / "memory"
    if resolved == memory_md or resolved == memory_dir or memory_dir in resolved.parents:
        return True
    try:
        rel_parts = resolved.relative_to(root).parts
    except ValueError:
        return False
    if len(rel_parts) < 2:
        return False
    leaf = rel_parts[1]
    if leaf == "memory":
        return True
    if leaf == "MEMORY.md" and len(rel_parts) == 2:
        return True
    return False


def resolve_confinement(
    path,
    cwd: Path,
    worktree_path: str | None,
    extra_exact_paths: "frozenset[Path] | None" = None,
) -> tuple[bool, Path]:
    """Return (is_confined, resolved_path). Confined if resolved_path == cwd,
    is a descendant of cwd, (when worktree_path is set) is cwd/worktree_path
    itself or a descendant of worktree_path, is Claude Code's OWN global
    auto-memory dir/file for this cwd (see
    `claude_code_own_memory_root()`/`_within_claude_code_own_memory_dir()`),
    OR (when extra_exact_paths is set) resolved_path is an EXACT member of
    extra_exact_paths.

    `extra_exact_paths` (workspace-root file allowlist, see
    docs/plans/2026-08-12-craftflow-workspace-root-allowlist-design.md) is
    matched by EXACT EQUALITY ONLY -- deliberately never descendant/prefix
    matching, unlike the cwd/worktree_path branches above. This is the
    single invariant that keeps the mechanism from ever becoming a
    directory/subtree grant into a sibling nested repo (the design's
    rejected "Option B"). Members must already be resolved, absolute
    Path objects -- this function does not re-resolve or validate them
    (mirrors how worktree_path is already caller-resolved before being
    passed in). Omitting this parameter, or passing None/an empty
    collection, reproduces the pre-existing 3-parameter behavior exactly
    (regression-tested).

    The Claude Code auto-memory allowance is a SEPARATE, unconditional
    directory-prefix grant -- unlike extra_exact_paths, it is not caller-
    supplied and not exact-match-only, because it is scoped to a
    deterministic, non-spoofable path computed only from `cwd` (never from
    `path`/tool-call input), so a directory-prefix grant here carries none
    of the sibling-repo-escape-hatch risk extra_exact_paths' docstring
    warns about."""
    candidate = Path(os.path.expanduser(str(path)))
    if not candidate.is_absolute():
        candidate = cwd / candidate
    resolved = candidate.resolve()
    within_cwd = resolved == cwd or cwd in resolved.parents
    if within_cwd:
        return True, resolved
    if worktree_path:
        wt = Path(worktree_path).resolve()
        within_wt = resolved == wt or wt in resolved.parents
        if within_wt:
            return True, resolved
    if _within_claude_code_own_memory_dir(resolved, cwd):
        return True, resolved
    if extra_exact_paths and resolved in extra_exact_paths:
        return True, resolved
    return False, resolved


def resolve_workspace_writable_paths(workflow: Dict[str, Any]) -> "frozenset[Path]":
    """Coerce workflow.get("workspace_writable_paths") into a frozenset of
    resolved absolute Path objects, for resolve_confinement()'s
    extra_exact_paths parameter. Never raises -- a missing key, a
    non-list value, or a non-string/empty-string list member all degrade
    to omission (never a crash, never a wildcard grant) -- Behavior
    Contract: fail toward the stricter, pre-existing deny-by-default
    posture, never fail open.

    The router (craftflow-router/SKILL.md `## 0.` step 1a, via
    craftflow_resolve_workspace_root.py's read_workspace_writable_paths())
    is the SOLE writer of this field and already performs the full
    semantic validation (direct-workspace-root-child, not inside a
    nested repo) before ever writing it. This helper is a defensive
    second layer doing only structural coercion -- it does not re-run
    that semantic validation, exactly the same trust relationship this
    guard already has with worktree_path (also unvalidated at this
    layer)."""
    raw = workflow.get("workspace_writable_paths")
    if not isinstance(raw, list):
        return frozenset()
    resolved: set = set()
    for entry in raw:
        if not isinstance(entry, str) or not entry:
            continue
        try:
            resolved.add(Path(entry).resolve())
        except (OSError, RuntimeError, ValueError):
            continue
    return frozenset(resolved)


def split_subcommands(command: str) -> list:
    """Split a shell command string on control operators (;, &&, ||, |, &).

    Best-effort tokenization (not a full shell parser) -- intentionally
    blunt, matching the rest of this guard family's deterministic-but-
    imperfect scope.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []

    subcommands = []
    current: list = []
    for token in tokens:
        if token in CONTROL_OPERATORS:
            if current:
                subcommands.append(current)
            current = []
        else:
            current.append(token)
    if current:
        subcommands.append(current)
    return subcommands


def is_env_assignment(token: str) -> bool:
    if "=" not in token:
        return False
    name = token.split("=", 1)[0]
    return name.isidentifier()


def looks_dynamic(token: str) -> bool:
    return "$" in token or "`" in token


_SUBSTITUTION_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")

# Extglob pattern-list group prefix characters (`!(...)`, `@(...)`,
# `+(...)`, `*(...)`, `?(...)`) -- bash pattern-list groups enabled only
# under `shopt -s extglob`, but once enabled (a real, if less common, live
# shell setting) `!(pattern-list)` in particular matches EVERYTHING except
# the given pattern-list, an unbounded/directory-dependent expansion this
# guard has no way to enumerate ahead of time (unlike a `{a,b}` brace
# group, whose alternatives are fully spelled out in the command text
# itself -- see craftflow_pretooluse_bash_guard.py's brace-expansion
# handling). `split_subcommands()`'s shlex tokenizer (punctuation_chars=
# True) splits a literal "(" into its OWN token, so `!(scratch)` never
# appears as one token containing "!(...)" verbatim -- it tokenizes as
# [..., "./!", "(", "scratch", ")"]. This existence check only needs to
# answer "is an extglob group plausibly present in this subcommand,"
# mirroring the bare `*`/`..` check just above it -- the fuller span
# handling (finding the matching close paren so the whole group is
# excluded from concrete path targets) lives in
# craftflow_pretooluse_bash_guard.py's `_positional_targets()`, which
# has access to `_scan_paren_depth()`.
_EXTGLOB_PREFIX_CHARS = ("!", "@", "?", "+", "*")


def _tokens_contain_extglob_span(tokens: list) -> bool:
    for idx, token in enumerate(tokens):
        if token and token[-1] in _EXTGLOB_PREFIX_CHARS and idx + 1 < len(tokens) and tokens[idx + 1] == "(":
            return True
    return False


# REM-FIX (composition-boundary bypass, follow-up to the brace/extglob
# hardening above): `split_subcommands()`'s shlex tokenizer never splits on
# `{`/`}`/`,`, so an INTACT single-level-or-nested brace-expansion group
# (`{a,.git}`, `{a,{b,.git}}`) always tokenizes as exactly ONE token no
# matter how deeply nested. The ONLY way a `{`/`}` pair can end up
# IMBALANCED within one token is a DIFFERENT mechanism fragmenting it first
# -- concretely, a `$(...)`/backtick command substitution nested inside the
# brace group forces shlex's `punctuation_chars=True` to split the `(`/`)`
# into their own separate tokens (e.g. `./{$(echo a),.git}` tokenizes as
# `["./{$", "(", "echo", "a", ")", ",.git}"]`), fragmenting the enclosing
# brace syntax across tokens before brace-detection ever sees one clean
# component. A token with more `{` than `}` characters is exactly this
# fragmented-span signature -- mirrors `_tokens_contain_extglob_span()`
# above (an existence check only; the fuller span-consuming logic lives in
# craftflow_pretooluse_bash_guard.py's `_positional_targets()`, which
# already has `_scan_paren_depth()`-style tools available for finding where
# the fragmented span actually closes).
def _tokens_contain_unbalanced_brace(tokens: list) -> bool:
    for token in tokens:
        if token.count("{") > token.count("}"):
            return True
    return False


def command_has_traversal_or_wildcard(command: str) -> bool:
    """True if a `..` path-traversal literal or a `*`/bare `.` wildcard
    token appears anywhere in the command text OR inside any $(...) /
    backtick substitution it contains -- OR an extglob pattern-list group
    (`!(...)`/`@(...)`/`+(...)`/`*(...)`/`?(...)`) is plausibly present
    (MEDIUM, live-confirmed bypass: `rm -rf ./!(scratch)` expands to
    everything except `scratch`, including `.git`/`packages`/`tools`) -- OR
    a command-substitution-fragmented brace-expansion span is plausibly
    present (REM-FIX, composition-boundary bypass: `rm -rf
    ./{$(echo a),.git}` has a real, live-confirmed destructive alternative
    but a dynamic `$(...)` target ALONE, with no accompanying traversal/
    wildcard signal elsewhere in the command, is normally left ALLOWED by
    this guard's dynamic-target-fail-closed-only-with-corroboration design;
    a fragmented brace span is itself exactly that corroborating signal,
    the same way a bare extglob span already is above). Text-level
    heuristic only -- guards see static command strings, never
    shell-expanded values."""
    extra = " ".join(m.group(1) or m.group(2) for m in _SUBSTITUTION_RE.finditer(command))
    combined = f"{command} {extra}"
    for tokens in split_subcommands(combined):
        if _tokens_contain_extglob_span(tokens):
            return True
        if _tokens_contain_unbalanced_brace(tokens):
            return True
        for token in tokens:
            stripped = token.strip("'\"")
            if stripped in ("..", "*", "."):
                return True
            if "/../" in stripped or stripped.startswith("../") or stripped.endswith("/.."):
                return True
            if "*" in stripped:
                return True
    return False


def extract_redirect_targets(command: str) -> list:
    """Best-effort: return file targets of >, >>, and `tee` invocations
    across every subcommand. Heuristic only, matching this guard
    family's documented deterministic-but-imperfect scope."""
    targets = []
    for tokens in split_subcommands(command):
        for idx, token in enumerate(tokens):
            if token in (">", ">>") and idx + 1 < len(tokens):
                targets.append(tokens[idx + 1])
            elif token == "tee":
                for t in tokens[idx + 1:]:
                    if not t.startswith("-"):
                        targets.append(t)
    return targets


def matches_memory_finalize_permit_shape(subcommand_tokens: list) -> bool:
    """Narrow, exact TOKEN-SHAPE match for the ONE documented permit-write
    shape: printf '%s' '<value>' > <permit-path, any spelling>

    Deliberately token-based, not a raw-text regex: split_subcommands()
    (posix shlex) already strips quotes, so the original quoted substring
    is not recoverable from a tokenized subcommand -- matching on the
    post-tokenization shape is both correct and simpler than trying to
    re-derive original text spans. Any other shape must be denied by the
    caller. Exact expected shape once tokenized:
    ["printf", "%s", "<any-single-value-token>", ">", "<permit-path>"]

    Deliberately does NOT compare the target token's SPELLING against any
    literal constant. Both call sites (craftflow_pretooluse_guard.py,
    craftflow_pretooluse_bash_guard.py) already independently verify
    `resolved == memory_finalize_permit_path().resolve()` -- a real
    `Path.resolve()`-based equality, immune to spelling variance (relative,
    absolute, `./`-prefixed, etc.) -- BEFORE ever calling this function.
    That resolved-path check is the real, spelling-independent,
    non-spoofable security anchor; this function's only remaining job is to
    validate the command SHAPE (exactly 5 tokens; printf/%s/`>` with a
    non-dynamic value). A prior version of this function additionally
    required the raw target token to literally string-equal a single
    hardcoded bare-relative spelling (`.craftflow/state/.memory-finalize`)
    -- that requirement was both redundant with the caller's resolved-path
    check (which already proves the token points at the right file
    regardless of spelling) AND actively broken: it silently denied every
    OTHER correctly-resolving spelling of the same file, including the
    router's own documented `$PROJECT_ROOT/`-prefixed and absolute-path
    forms, making the permit file impossible to create via the one
    sanctioned Bash path whenever the caller didn't spell the target as
    that exact bare-relative literal (CRITICAL, live-reproduced
    chicken-and-egg deadlock -- see 2026-08-18 DEBUG workflow).
    """
    if len(subcommand_tokens) != 5:
        return False
    cmd, fmt, _value, redirect, _target = subcommand_tokens
    if looks_dynamic(_value):
        return False
    return cmd == "printf" and fmt == "%s" and redirect == ">"
