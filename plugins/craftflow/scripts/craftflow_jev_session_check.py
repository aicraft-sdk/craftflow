#!/usr/bin/env python3
"""SessionStart hook -- auto-detect one-time consent + per-session canary
fallback for the optional Jev (TypeSafe AI) routing hint.

OFF BY DEFAULT for every existing user: a no-op unless `TYPESAFE_API_KEY` is
set AND `config/jev.json`'s `enabled` flag is NOT `true` (the manual
`--enable`d path is left byte-for-byte unchanged -- see DD-5/DD-9).

Precedence (DD-5), checked in this exact order, each an early-return no-op:
  1. non-`SessionStart` event
  2. missing/non-string `session_id`
  3. missing `TYPESAFE_API_KEY`
  4. `enabled is True` (manual path active; consent is never even read)
  5. `consent.status == "declined"`
Otherwise: `consent.status == "unset"` injects a one-time-per-session
`AskUserQuestion` request (`_maybe_ask_consent`); `consent.status ==
"granted"` runs a short-budget canary call and session-gates activation
(`_run_canary`).

Never raises -- the whole body runs under a single outer try/except
(mirrors `craftflow_jev_prompt_hint.main()` exactly).

See docs/plans/2026-09-23-jev-auto-detect-plan.md (Phase 5, DD-2/DD-3/DD-4/
DD-5/DD-6/DD-8/DD-10) for the full design.

Accepted residual risk (silent-failure-hunter re-hunt on commit 0ff101d,
user-approved, not remediated): a `checked_at` ordering race between
concurrent SessionStart firings for the same session_id, and a partial-write
window inside `session_context()` itself, are both documented and accepted
as out of scope for this feature's fail-open cache-write model.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from craftflow_hooklib import (
    load_input,
    log_event,
    plugin_config_dir,
    plugin_root,
    session_context,
    state_root,
)
from craftflow_jev_client import call as jev_call
from craftflow_jev_config import api_key, consent_status, load_config
from craftflow_jev_session_cache import (
    read_session_status,
    status_changed,
    write_last_status,
    write_session_status,
)

# DD-3: entirely separate, shorter budget than craftflow_jev_client's own
# TOTAL_BUDGET_SECONDS=4.0 default (which still governs the per-prompt hook).
SESSION_CHECK_TOTAL_BUDGET_SECONDS = 2.0
SESSION_CHECK_TIMEOUT_SECONDS = 2.0

# DD-6: injected additionalContext contract. Mirrors the shipped
# <craftflow_routing_hint source="jev"> convention's shared-rule + host-
# pointer documentation pattern (see Phase 7). Discloses DD-10's activation
# latency explicitly at ask-time -- granting consent never activates Jev
# for the remainder of this turn/session.
CONSENT_REQUEST_TEMPLATE = (
    '<craftflow_jev_consent_request>\n'
    'ACTION REQUIRED (this turn only): TYPESAFE_API_KEY is set but the user has not yet '
    'consented to the optional Jev (TypeSafe AI) routing hint. Before your first substantive '
    'reply this turn, call AskUserQuestion with:\n'
    'Question: "Enable the optional Jev (TypeSafe AI) routing hint?"\n'
    '{privacy_note}\n'
    'Options: ["Enable", "Decline"]\n'
    'Then run exactly one of:\n'
    '  python3 "{setup_script}" --record-consent granted\n'
    '  python3 "{setup_script}" --record-consent declined\n'
    'matching the answer. If that command exits non-zero, tell the user the consent choice '
    'could not be saved and that Jev stays off for now. If the answer is "Enable" and the '
    'command exits 0, also tell the user plainly: Jev will NOT activate for the rest of this '
    'turn or session -- it activates automatically starting at your next new session or the '
    'next automatic checkpoint (a canary check runs then). Do not call AskUserQuestion again '
    'this session regardless of the outcome.\n'
    '</craftflow_jev_consent_request>'
)


def _maybe_ask_consent(root: Path, session_id: str, cfg: Dict[str, Any]) -> int:
    """DD-4/DD-6: inject the consent-request context exactly once per
    session_id.

    CRITICAL fix (silent-failure-hunter, reproduced live, on commit
    34493d9): the message is now fully built (lazy import + privacy_note()
    + .format()) BEFORE the `already_asked_consent` flag is persisted. The
    old order wrote the flag FIRST -- if anything between that write and
    delivery raised (lazy import failure, privacy_note() raising,
    .format() raising, or session_context()'s own print raising, e.g.
    BrokenPipeError), main()'s outer try/except swallowed it silently and
    the flag was already on disk: the user would permanently and silently
    never be asked again for that session_id, every future SessionStart
    firing (including a later compact) seeing the flag and no-opping. Now,
    if message-building fails, it raises before any write happens, so
    `consent.status` stays effectively un-asked on disk and the very next
    SessionStart firing (even within the same session) tries again.

    The one remaining fallible step after the flag write is the
    unavoidable `session_context()` print itself (DD-4 still requires the
    flag to be written before the assistant responds, structurally, not by
    instruction). If THAT fails, a distinct `consent_ask_undelivered` event
    is logged (not just the generic `hook_error`), so this degradation is
    diagnosable instead of indistinguishable from a normal ask.

    Re-hunt fix (silent-failure-hunter, reproduced live, on commit
    b889732): a `session_context()` failure used to leave the flag
    permanently `True` on disk -- the assistant received nothing that turn
    (the print itself failed), yet every future `SessionStart` firing for
    this `session_id` would see the flag and silently never ask again. That
    is safe to roll back: no assistant turn observed the ask, so retrying on
    the very next firing cannot double-ask within one turn. The flag write
    is now undone (`already_asked_consent=False`) in the except branch
    before returning, so the next `SessionStart` firing for this
    `session_id` retries the ask instead of staying silently suppressed.

    Re-hunt fix (silent-failure-hunter, on commit 0ff101d, final cycle on
    this bug): the rollback write above (`write_session_status(...,
    already_asked_consent=False)`) is itself best-effort / never-raises
    (see `craftflow_jev_session_cache.write_session_status`'s own
    fail-silently contract) -- it can silently no-op, leaving the flag
    stuck at `True` with no signal. That underlying write primitive is
    deliberately fail-open throughout this feature and cannot be made to
    "not fail." What CAN be added is a read-back: after the rollback write,
    re-read the session cache and, if it still shows `already_asked_consent
    is True` (rollback silently failed) or the read itself returns
    None/unexpected (couldn't confirm either way), log a DISTINCT
    `consent_ask_rollback_unconfirmed` event so this compound-failure state
    is diagnosable in the hook-events log, separate from
    `consent_ask_undelivered`. One read-back check, one distinct log line
    if unconfirmed -- no retry loop, no additional write attempts."""
    cached = read_session_status(root, session_id)
    if cached and cached.get("already_asked_consent"):
        return 0

    from craftflow_jev_setup import privacy_note

    note = privacy_note(cfg.get("maxStateChars", 4000))
    message = CONSENT_REQUEST_TEMPLATE.format(
        privacy_note=note,
        setup_script=str(plugin_root() / "scripts" / "craftflow_jev_setup.py"),
    )

    write_session_status(root, session_id, already_asked_consent=True)
    try:
        session_context(message)
    except Exception as exc:
        log_event(
            "plugin_jev_session_check",
            {
                "event": "jev_session_check",
                "decision": "consent_ask_undelivered",
                "error": type(exc).__name__,
            },
        )
        write_session_status(root, session_id, already_asked_consent=False)
        confirmed = read_session_status(root, session_id)
        if confirmed is None or confirmed.get("already_asked_consent") is True:
            log_event(
                "plugin_jev_session_check",
                {
                    "event": "jev_session_check",
                    "decision": "consent_ask_rollback_unconfirmed",
                },
            )
        return 0
    log_event(
        "plugin_jev_session_check",
        {"event": "jev_session_check", "decision": "consent_ask_injected"},
    )
    return 0


def _run_canary(root: Path, session_id: str, cfg: Dict[str, Any], key: str) -> int:
    """DD-2/DD-3/DD-8: run one short-budget canary call, write the
    session-scoped + cross-session status caches, and inject a note only on
    a genuine state change (an unchanged healthy result across repeated
    `SessionStart` firings stays silent)."""
    failure_reason: Dict[str, Any] = {}
    result = jev_call(
        {"ping": "craftflow jev session canary"},
        {"ok": {"type": "noul", "instructions": "Is `ping` a short greeting-like string?"}},
        api_key=key,
        model=cfg.get("model", "jev-latest"),
        timeout=min(cfg.get("timeoutSeconds", 2.5), SESSION_CHECK_TIMEOUT_SECONDS),
        cache_dir=None,
        total_budget_seconds=SESSION_CHECK_TOTAL_BUDGET_SECONDS,
        failure_reason_out=failure_reason,
    )
    active = result is not None
    reason = None if active else f"{failure_reason.get('error')}:{failure_reason.get('status')}"
    write_session_status(root, session_id, active=active, reason=reason)
    changed = status_changed(root, active, reason)
    write_last_status(root, active, reason)
    log_event(
        "plugin_jev_session_check",
        {
            "event": "jev_session_check",
            "decision": "canary_active" if active else "canary_inactive",
            "reason": reason,
        },
    )
    if changed:
        message = (
            "Jev routing hint: active this session (canary OK)."
            if active
            else (
                f"Jev routing hint: inactive this session -- canary failed ({reason}). "
                "Falling back to normal routing."
            )
        )
        try:
            session_context(message)
        except Exception as exc:
            # REM-FIX (doubt-verifier): write_session_status/write_last_status
            # above already committed the new active/reason state -- that
            # commit is correct and intentionally stays regardless of whether
            # this notify succeeds. write_last_status/status_changed() does
            # act as a once-per-transition notify gate (same shape as
            # _maybe_ask_consent's already_asked_consent flag), so a lost
            # notify here IS a permanently-missed one-time FYI, same as
            # there. The difference is severity, not mechanism: the routing
            # decision itself (write_session_status, consumed by
            # craftflow_jev_prompt_hint.py) is always correct regardless of
            # this notify's outcome, so there is nothing functionally
            # incorrect to roll back -- only the diagnosability of the
            # missed notification matters, which this distinct event covers.
            # No retry/rollback here, unlike the consent-ask flag, because
            # losing this notification has no functional consequence.
            log_event(
                "plugin_jev_session_check",
                {
                    "event": "jev_session_check",
                    "decision": "canary_notify_undelivered",
                    "error": type(exc).__name__,
                },
            )
    return 0


def main() -> int:
    try:
        data = load_input()
        if data.get("hook_event_name") not in (None, "SessionStart"):
            return 0
        session_id = data.get("session_id") if isinstance(data.get("session_id"), str) else None
        if not session_id:
            return 0
        key = api_key(os.environ)
        if not key:
            return 0
        cfg, _decisions = load_config(plugin_config_dir() / "jev.json")
        if cfg.get("enabled") is True:
            return 0  # manual path active -- never even reads consent (DD-5/DD-9)
        status = consent_status(cfg)
        if status == "declined":
            return 0
        root = state_root()
        if status == "unset":
            return _maybe_ask_consent(root, session_id, cfg)
        if status == "granted":
            return _run_canary(root, session_id, cfg, key)
        return 0
    except Exception as exc:  # never stall the session
        log_event(
            "plugin_jev_session_check",
            {"event": "jev_session_check", "decision": "hook_error", "error": type(exc).__name__},
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
