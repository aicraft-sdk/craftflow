#!/usr/bin/env python3
"""PreToolUse hook -- audit-only, opt-in Jev (TypeSafe AI) pre-execution risk classification
for a narrow allowlist of consequential Bash/Write/Edit calls.

Implements docs/ai/decisions/0044-craftflow-preexecution-risk-classifier.md, design at
docs/plans/2026-09-27-adr-0044-audit-only-opt-pre-design.md. OFF BY DEFAULT: inert unless
config/jev.json has features.riskGate in {"audit","advise"} AND enabled:true AND
TYPESAFE_API_KEY is set. NEVER blocks -- no permissionDecision output, no stdout, always exits 0
regardless of the classifier's answer or any failure. riskGate:"advise" is treated identically
to "audit" this round (see decide()) -- there is no blocking mode yet.

Only tool_input["command"] (Bash) or tool_input["file_path"] (Write/Edit) is ever inspected --
content/new_string/old_string are never read, referenced, or forwarded (secret-hygiene property,
see test_content_and_new_string_never_inspected). Before egress, the Bash command text is run
through redact_action() to mask inline credentials (URL userinfo, credential-shaped NAME=value
assignments, --password/--token/--secret/--api-key flag values) and Write/Edit file paths are
made cwd-relative or reduced to a bare basename via _relativize_path() -- never absolute (see
Task 3.2).

NOTE: this module currently exposes only the pure allowlist matcher and pure builders
(build_state/build_questions/decide/telemetry_row/_append_event). main() -- the stdin-JSON-
driven, fail-open hook entry point -- lands in Phase 4 of the implementation plan.
"""
from __future__ import annotations

import fnmatch
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from craftflow_hooklib import (
    is_env_assignment,
    now_iso,
    split_subcommands,
)
from craftflow_jev_heuristic import classify_risk_gate

# REM-FIX (silent-failure-hunter MEDIUM #1): hard byte cap on untrusted command text applied
# BEFORE matches_allowlist's own category matchers/_db_drop_match run -- bounds the cost of
# category classification on an attacker-controlled multi-megabyte command string.
#
# REM-FIX (code-reviewer re-review CRITICAL, cycle 2): this cap is scoped to matches_allowlist()
# ONLY -- category classification does not do credential redaction, so truncating its input has
# no secret-hygiene consequence. It must NEVER be applied to the raw text that reaches
# redact_action() (see build_state()): cycle 1 briefly also capped build_state's raw input with
# this same constant before redact_action ran, which reopened the URL-userinfo leak -- a password
# straddling the cut lost its terminating '@' before the redaction regex ever saw it, so the
# truncated fragment reached action_text in cleartext (test_build_state_redacts_password_
# straddling_old_pre_redaction_cap_boundary pins the fix). build_state() now redacts the FULL raw
# text first and caps only the already-redacted output, via `max_chars` and/or
# `_REDACT_ACTION_SAFETY_CAP` (see redact_action()).
_MAX_RAW_ACTION_CHARS = 20_000

_FORCE_PUSH_LITERAL_FLAGS = ("-f", "--force", "--force-with-lease")


def _short_flag_has_letter(token: str, letter: str) -> bool:
    """True for a bundled short-flag token (e.g. "-rf", "-fr") containing `letter`, but not
    for a long-option token (`--force`) or a bare `-` alone."""
    return token.startswith("-") and not token.startswith("--") and len(token) > 1 and letter in token[1:]


def _has_recursive_and_force_flags(flag_tokens: List[str]) -> bool:
    has_recursive = any(
        t in ("-r", "-R", "--recursive") or _short_flag_has_letter(t, "r") or _short_flag_has_letter(t, "R")
        for t in flag_tokens
    )
    has_force = any(t == "--force" or _short_flag_has_letter(t, "f") for t in flag_tokens)
    return has_recursive and has_force


def _has_force_push_flag(flag_tokens: List[str]) -> bool:
    return any(
        t in _FORCE_PUSH_LITERAL_FLAGS
        or t.startswith("--force-with-lease=")
        or _short_flag_has_letter(t, "f")
        for t in flag_tokens
    )


def _rm_rf_or_force_push_category(command: str) -> Optional[str]:
    for subcommand_tokens in split_subcommands(command):
        tokens = [t for t in subcommand_tokens if not is_env_assignment(t)]
        if not tokens:
            continue
        if tokens[0] == "rm" and _has_recursive_and_force_flags(tokens[1:]):
            return "rm_rf"
        if tokens[0] == "git" and len(tokens) > 1 and tokens[1] == "push" and _has_force_push_flag(tokens[2:]):
            return "force_push"
    return None


# Text-level (not token-level): SQL/migration-tool phrases don't tokenize cleanly as shell
# words, and can appear inside a quoted -c argument to psql/mysql/etc.
_DB_DROP_PATTERNS = [
    re.compile(r"(?<![a-zA-Z0-9_])drop\s+database(?![a-zA-Z0-9_])", re.IGNORECASE),
    re.compile(r"(?<![a-zA-Z0-9_])drop\s+table(?![a-zA-Z0-9_])", re.IGNORECASE),
    re.compile(r"(?<![a-zA-Z0-9_])migrate[:\s]+down(?![a-zA-Z0-9_])", re.IGNORECASE),
    re.compile(r"(?<![a-zA-Z0-9_])db:rollback(?![a-zA-Z0-9_])", re.IGNORECASE),
]


def _db_drop_match(command: str) -> bool:
    return any(p.search(command) for p in _DB_DROP_PATTERNS)


_SECRET_BASENAME_GLOBS = (".env", ".env.*", "id_rsa", "id_rsa.*", "*.pem")


def _secret_path_match(file_path: str) -> bool:
    basename = Path(file_path).name.lower()
    if any(fnmatch.fnmatch(basename, pattern) for pattern in _SECRET_BASENAME_GLOBS):
        return True
    lowered = file_path.lower()
    return "secret" in lowered or "credential" in lowered


def matches_allowlist(tool_name: Any, tool_input: Any) -> Optional[str]:
    """Pure. Returns the matched category name ("rm_rf"/"force_push"/"db_drop"/"secret_path")
    or None. Never raises -- malformed/missing tool_name/tool_input degrade to None. Only ever
    inspects tool_input["command"] (Bash) or tool_input["file_path"] (Write/Edit) -- see module
    docstring."""
    if not isinstance(tool_input, dict):
        return None
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        capped_command = command[:_MAX_RAW_ACTION_CHARS]
        category = _rm_rf_or_force_push_category(capped_command)
        if category is not None:
            return category
        return "db_drop" if _db_drop_match(capped_command) else None
    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            return None
        return "secret_path" if _secret_path_match(file_path) else None
    return None


# ---------------------------------------------------------------------------
# redact_action() -- credential masking before egress (DD-8). The db_drop
# category's real-world shape routinely carries an inline credential inside
# the command string itself (connection strings, PGPASSWORD=..., --password=
# ...) -- this is the one field this feature sends to a third party, so it
# must never leave the machine unredacted.
# ---------------------------------------------------------------------------

# REM-FIX (code-reviewer CRITICAL #1): password char class must NOT exclude '@'/'/' -- excluding
# them is what let a password containing either character leak (partially via the greedy match
# stopping at the FIRST '@', or entirely when a '/' inside the password broke the match outright).
# Dropping the exclusions makes the greedy `[^\s]{1,512}` quantifier backtrack to the RIGHTMOST
# '@' in the whitespace-delimited authority token instead, which is always the real userinfo
# separator for this shape (see test_redact_action_masks_url_userinfo_with_embedded_at_and_slash_in_password).
_URL_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]{0,20}://[^\s:/@]{1,256}:)[^\s]{1,512}@")
_ASSIGNMENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{0,128})=(\"[^\"]{0,4096}\"|'[^']{0,4096}'|\S{1,4096})")
_SECRET_FLAG = re.compile(
    r"(--(?:password|passwd|token|secret|api-key|apikey)(?:=|\s+))(\"[^\"]{0,4096}\"|'[^']{0,4096}'|\S{1,4096})",
    re.IGNORECASE,
)
# REM-FIX (silent-failure-hunter CRITICAL #2): single-dash concatenated CLI flags (mysql/psql
# convention: `-p<password>`, `-u<username>`, no space) are not covered by _SECRET_FLAG (which
# only matches `--password`/etc. double-dash forms). Requires a non-space, non-dash boundary
# before the flag so it only fires on a genuine token start, not mid-word.
_SHORT_FLAG_CONCAT = re.compile(r"(?<!\S)(-[pu])([^\s\-]\S{0,4095})")
# REM-FIX (silent-failure-hunter CRITICAL #2): broadened from a closed enumeration that missed
# common real credential shapes (DB_PASS, OPENAI_KEY, AWS_ACCESS_KEY_ID, GH_PAT). Per the hunter's
# directive, over-redaction is the accepted safe-failure direction for this feature.
_SENSITIVE_NAME_PARTS = (
    "PASSWORD", "PASSWD", "PWD", "PASS", "TOKEN", "SECRET", "APIKEY", "API_KEY", "KEY",
    "CREDENTIAL", "CRED", "AUTH", "PAT",
)
_REDACTED = "***"

# REM-FIX (silent-failure-hunter re-hunt MEDIUM, cycle 2): a generous internal safety bound for a
# future direct caller of redact_action() that bypasses build_state's own max_chars egress cap.
# The bounded-quantifier regexes above already make this a performance-only concern, not a
# correctness one (see test_matcher_and_redactor_are_bounded_time_on_pathological_input), so this
# cap is applied to the OUTPUT of all four redaction passes below, NEVER to the input before they
# run -- truncating the input first is exactly the straddling-boundary leak this same REM-FIX
# round closed at the build_state layer (see build_state() and _MAX_RAW_ACTION_CHARS' docstring).
# Sized to match the pathological-input test's own proven-fast size.
_REDACT_ACTION_SAFETY_CAP = 200_000

# REM-FIX (silent-failure-hunter HIGH, corroborated MEDIUM by code-reviewer re-review; REM-FIX
# cycle 3). Cycle 2 correctly moved the only raw-command size bound from a PRE-redaction cap
# inside build_state (cycle 1 -- reopened the URL-userinfo leak, see build_state()'s own
# docstring) to redact_action()'s OUTPUT-side _REDACT_ACTION_SAFETY_CAP above. But that left
# redact_action()'s INPUT, on the real production call path (build_state()), completely
# unbounded: a multi-MB Bash command (heredoc, base64 blob) makes this PreToolUse hook's 5
# sequential regex passes run over genuinely unbounded, attacker-influenceable text -- measured
# ~0.1-0.32s/1MB, scaling linearly with no ceiling, stalling this synchronous PreToolUse call for
# multiple seconds on a large payload (see test_build_state_bounds_compute_time_on_unsliced_
# multi_mb_command).
#
# Fix: build_state() caps its RAW `action_text` to this many characters BEFORE calling
# redact_action() -- distinct from BOTH other caps in this module (three caps, three purposes,
# none reused for another's job):
#   - _MAX_RAW_ACTION_CHARS (20,000): matches_allowlist()'s own category-matching cap, applied
#     only to Bash command CLASSIFICATION, never to egress/redaction.
#   - _REDACT_ACTION_SAFETY_CAP (200,000): redact_action()'s own OUTPUT-side bound, for a future
#     direct caller that bypasses build_state's max_chars egress budget.
#   - _MAX_REDACT_ACTION_INPUT_CHARS (this constant): build_state()'s base INPUT-side bound,
#     ahead of redact_action(), so the regex passes never run over unbounded text on the real
#     hook path. As of cycle 4 (below), this is no longer the literal slice point -- see
#     _REDACT_ACTION_INPUT_MARGIN_CHARS.
#
# REM-FIX (code-reviewer re-review CRITICAL, cycle 4, verified live PoC). Cycle 3's mistake:
# treating this as a pure SIZE problem ("100,000 is >>50x any realistic credential, so it's
# safe") reintroduced cycle 1's exact bug at a new boundary. The actual invariant is POSITIONAL,
# not length-based: cutting `action_text` at EXACTLY `_MAX_REDACT_ACTION_INPUT_CHARS` can still
# land between a credential and its regex's required trailing delimiter (_URL_USERINFO's '@'),
# and no cap SIZE avoids that -- an adversarial input can always be constructed to straddle any
# single fixed cut point (see test_build_state_redacts_password_straddling_old_pre_redaction_
# cap_boundary and test_build_state_closes_credential_leak_straddling_100k_hard_cap_boundary,
# which pins the reviewer's own reproduction of this exact class at the 100,000-char boundary).
#
# The fix is NOT to move the cut again -- it is to stop cutting the RAW input at the point where
# a credential could be straddling it. `_REDACT_ACTION_INPUT_MARGIN_CHARS` extends the window fed
# to redact_action() by at least the largest bounded-quantifier span among this module's four
# redaction patterns (_ASSIGNMENT/_SECRET_FLAG's value captures are bounded to 4096 chars,
# _SHORT_FLAG_CONCAT's to 4095, _URL_USERINFO's password field to 512 -- see redact_action()
# below). Because every credential these patterns can ever recognize has a total match length
# bounded by that same span, a credential that STARTS anywhere before
# `_MAX_REDACT_ACTION_INPUT_CHARS` is mathematically guaranteed to have its full match (including
# the trailing delimiter) complete before `_MAX_REDACT_ACTION_INPUT_CHARS +
# _REDACT_ACTION_INPUT_MARGIN_CHARS` -- so the widened window can never sever it. This mirrors
# exactly the principle that closed cycle 1's leak (redact first, cap after): the TRUE hard cap
# (bounding compute cost) is applied only to the output of build_state()'s existing `max_chars`
# egress budget, never to redact_action()'s input at the base `_MAX_REDACT_ACTION_INPUT_CHARS`
# boundary -- only at the margin-extended one, which no realistic bounded-length credential can
# straddle. `_REDACT_ACTION_SAFETY_CAP` (redact_action()'s own output-side bound, above) already
# covers the "what if a future caller sends an even larger already-redacted string" case; no
# third, redundant output cap is needed in build_state() -- its own `max_chars` parameter already
# serves that purpose for this call path.
_MAX_REDACT_ACTION_INPUT_CHARS = 100_000

# See the cycle-4 note above: at least the largest bounded-quantifier span among
# _URL_USERINFO/_ASSIGNMENT/_SECRET_FLAG/_SHORT_FLAG_CONCAT's value/password captures (4096 is
# the largest -- _ASSIGNMENT and _SECRET_FLAG's quoted/unquoted value groups are `{0,4096}`/
# `{1,4096}`, _SHORT_FLAG_CONCAT's is `{0,4095}`; _URL_USERINFO's password field is only `{1,512}`
# but the whole module uses one shared margin rather than a bespoke one per pattern, for
# simplicity and because 4096 already dominates every pattern's max span). This is the amount by
# which `_MAX_REDACT_ACTION_INPUT_CHARS` is extended before it is ever used as a slice boundary --
# see `_REDACT_ACTION_INPUT_WINDOW_CHARS` and `build_state()`.
_REDACT_ACTION_INPUT_MARGIN_CHARS = 4096

# The ACTUAL slice boundary applied to raw `action_text` before it reaches redact_action() (see
# build_state()). Never slice at `_MAX_REDACT_ACTION_INPUT_CHARS` alone -- see the cycle-4 note
# above for why that reintroduces the straddling-boundary leak.
_REDACT_ACTION_INPUT_WINDOW_CHARS = _MAX_REDACT_ACTION_INPUT_CHARS + _REDACT_ACTION_INPUT_MARGIN_CHARS


def _redact_assignment(match: "re.Match[str]") -> str:
    name = match.group(1)
    if any(part in name.upper() for part in _SENSITIVE_NAME_PARTS):
        return f"{name}={_REDACTED}"
    return match.group(0)


def redact_action(text: str, api_key: Optional[str]) -> Tuple[str, bool]:
    """Pure. Masks values that must never leave the machine, in order: (1) the literal
    `api_key` if non-empty, (2) URL userinfo passwords, (3) credential-shaped `NAME=value`
    env-style assignments, (4) `--password`/`--token`/`--secret`/`--api-key`-style flag values,
    (5) single-dash concatenated `-p<value>`/`-u<value>` CLI flags (mysql/psql convention).
    Never raises. Runs every pass over the FULL input text -- never pre-truncates it (that would
    risk cutting a credential before its terminating delimiter, silently defeating the very
    redaction this function exists to do). `_REDACT_ACTION_SAFETY_CAP` bounds only the RETURNED,
    already-redacted text. Returns (possibly-modified text, whether anything was actually
    masked)."""
    out = text
    changed = False

    if api_key:
        replaced = out.replace(api_key, _REDACTED)
        changed = changed or replaced != out
        out = replaced

    replaced = _URL_USERINFO.sub(r"\1" + _REDACTED + "@", out)
    changed = changed or replaced != out
    out = replaced

    replaced = _ASSIGNMENT.sub(_redact_assignment, out)
    changed = changed or replaced != out
    out = replaced

    replaced = _SECRET_FLAG.sub(r"\1" + _REDACTED, out)
    changed = changed or replaced != out
    out = replaced

    replaced = _SHORT_FLAG_CONCAT.sub(r"\1" + _REDACTED, out)
    changed = changed or replaced != out
    out = replaced

    if len(out) > _REDACT_ACTION_SAFETY_CAP:
        out = out[:_REDACT_ACTION_SAFETY_CAP]

    return out, changed


def _relativize_path(file_path: str, cwd: Optional[str]) -> str:
    """Pure (DD-9). Returns `file_path` made relative to `cwd` when `file_path` is a descendant
    of `cwd`; otherwise returns the bare basename. Never returns an absolute path -- avoids
    leaking a home-directory or OS username segment to the third-party classifier. Never raises
    on malformed input (missing cwd, non-path strings, etc.).

    REM-FIX (code-reviewer MEDIUM #2): `Path(file_path).resolve()` resolves a RELATIVE `file_path`
    against the real process `os.getcwd()`, not the `cwd` argument above -- a latent correctness
    footgun (it does not leak an absolute path; confirmed inert today because there are no call
    sites yet -- main() does not exist until Phase 4). Phase 4's main() MUST always pass an
    absolute `file_path` (Claude Code's Write/Edit PreToolUse payloads already do this), so this
    process-cwd-vs-argument-cwd mismatch never actually triggers in practice. No functional change
    made here per the reviewer's own assessment; this comment exists so Phase 4's implementer does
    not accidentally introduce a relative `file_path` call site without re-checking this."""
    if not file_path:
        return ""
    try:
        if cwd:
            return str(Path(file_path).resolve().relative_to(Path(cwd).resolve()))
    except (ValueError, OSError):
        pass
    return Path(file_path).name


def build_state(
    tool_name: str, category: str, action_text: str, *, api_key: Optional[str], max_chars: int
) -> Dict[str, Any]:
    """Pure. `action_text` is the raw command (Bash) or file_path (Write/Edit) -- NEVER
    content/new_string/old_string (see module docstring, P1).

    REM-FIX (code-reviewer re-review CRITICAL, cycle 2): `redact_action()` runs over the FULL,
    UNTRUNCATED raw `action_text` -- cycle 1's `_MAX_RAW_ACTION_CHARS` pre-redaction truncation
    cap (applied here, before redact_action) reopened the URL-userinfo leak: a credential
    positioned so its terminating delimiter (e.g. '@') fell just past the cap boundary was cut
    off before redact_action ever saw it, so the redaction regex -- which requires that trailing
    delimiter to match at all -- never fired, and the truncated fragment reached `action_text` in
    cleartext (see test_build_state_redacts_password_straddling_old_pre_redaction_cap_boundary).
    The caller-supplied `max_chars` egress budget (DD-8) is applied ONLY to the already-redacted
    output below, never to the raw text redact_action() receives; `redact_action()`'s own
    `_REDACT_ACTION_SAFETY_CAP` provides a further, output-side-only safety bound.

    REM-FIX (silent-failure-hunter HIGH / code-reviewer re-review, cycle 3): cycle 2's fix above
    left `redact_action()`'s INPUT completely unbounded on this real production call path -- a
    multi-MB Bash command would run all 5 regex passes over genuinely unbounded,
    attacker-influenceable text, stalling this hook's synchronous PreToolUse call for multiple
    seconds (see test_build_state_bounds_compute_time_on_unsliced_multi_mb_command). Cycle 3
    capped `action_text` at exactly `_MAX_REDACT_ACTION_INPUT_CHARS` (100,000 chars) BEFORE
    redact_action() runs.

    REM-FIX (code-reviewer re-review CRITICAL, cycle 4, verified live PoC): cycle 3's cap
    reintroduced cycle 1's exact credential-leak class at the new 100,000-char boundary -- a
    credential straddling that EXACT cut point (starting before it, terminating just past it)
    lost its trailing delimiter to the cut, so `redact_action()`'s regexes never matched it and
    the raw fragment reached `action_text` in cleartext with `redacted=False` (see
    test_build_state_closes_credential_leak_straddling_100k_hard_cap_boundary). The bug is
    POSITIONAL, not length-based -- no cap SIZE alone fixes it. The actual fix: the raw text fed
    to redact_action() is sliced at `_REDACT_ACTION_INPUT_WINDOW_CHARS`
    (`_MAX_REDACT_ACTION_INPUT_CHARS + _REDACT_ACTION_INPUT_MARGIN_CHARS`), not at
    `_MAX_REDACT_ACTION_INPUT_CHARS` alone -- a margin at least as large as the biggest
    bounded-quantifier span among this module's redaction patterns, so any credential recognized
    by those patterns and starting before `_MAX_REDACT_ACTION_INPUT_CHARS` is guaranteed to have
    its full match (through its trailing delimiter) complete before the window edge (see the
    constant's own docstring above). The TRUE hard cap on egress size is then applied only to the
    already-redacted OUTPUT via the caller-supplied `max_chars` (DD-8) -- never to
    redact_action()'s raw input -- mirroring exactly the principle that closed cycle 1's leak
    (redact first, cap after), now applied at the correct stage. `redact_action()`'s own
    `_REDACT_ACTION_SAFETY_CAP` remains as a further, independent output-side bound for any
    future direct caller that bypasses this function's `max_chars` budget; no third,
    build_state()-local output cap is added -- `max_chars` already serves that purpose here."""
    windowed_input = action_text[:_REDACT_ACTION_INPUT_WINDOW_CHARS]
    redacted_text, was_redacted = redact_action(windowed_input, api_key)
    truncated = len(action_text) > _REDACT_ACTION_INPUT_WINDOW_CHARS or len(redacted_text) > max_chars
    return {
        "tool_name": tool_name,
        "category": category,
        "action_text": redacted_text[:max_chars],
        "truncated": truncated,
        "redacted": was_redacted,
    }


def build_questions() -> Dict[str, Dict[str, Any]]:
    """Pure. One batched choice question -- same shape convention as
    craftflow_jev_remfix_scope.build_questions()."""
    return {
        "risk": {
            "type": "choice",
            "instructions": (
                "This tool call matched a pre-execution risk-gate allowlist pattern (see "
                "`category` in state: rm_rf, force_push, db_drop, or secret_path). Classify "
                "whether THIS SPECIFIC invocation (see `action_text`) is genuinely risky "
                "(irreversible, destructive, or credential-exposing given its actual "
                "arguments/target) or a safe/expected use despite matching the pattern."
            ),
            "criteria": {
                "risky": "This invocation is genuinely risky given its actual arguments/target.",
                "not_risky": "This invocation is a safe or expected use despite matching the allowlist pattern.",
            },
        }
    }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def decide(mode: str, answer: Optional[Dict[str, Any]]) -> Tuple[str, Optional[str], Optional[float]]:
    """Pure. `mode` is accepted for signature symmetry with the other Jev features and to make
    the "advise behaves identically to audit" contract visible at the call site -- it never
    changes this function's outcome (DD-5): there is no confidence-gated "applied" state for
    risk_gate this round. Returns (decision, choice, confidence) where decision is "logged" for
    any well-formed answer, else "no_decision". Never raises."""
    choice = answer.get("choice") if isinstance(answer, dict) else None
    confidence = answer.get("confidence") if isinstance(answer, dict) else None
    valid = (
        isinstance(choice, str)
        and choice in ("risky", "not_risky")
        and _is_number(confidence)
        and confidence == confidence  # reject NaN
        and 0.0 <= confidence <= 1.0
    )
    if not valid:
        return "no_decision", None, None
    return "logged", choice, float(confidence)


def telemetry_row(
    *, decision: str, choice: Optional[str], confidence: Optional[float],
    result: Optional[Dict[str, Any]], mode: str, model: str,
    workflow_uuid: Optional[str], tool_name: str, category: str,
) -> Dict[str, Any]:
    """Pure. One row per matched+active invocation. Never includes action_text/command/
    file_path -- only tool_name + category (DD-6)."""
    result = result if isinstance(result, dict) else {}
    heuristic = classify_risk_gate()
    return {
        "ts": now_iso(),
        "call_id": uuid.uuid4().hex,
        "workflow_uuid": workflow_uuid,
        "feature": "risk_gate",
        "mode": mode,
        "model": result.get("model", model),
        "latency_ms": result.get("latency_ms"),
        "cache_hit": result.get("cache_hit", False),
        "usage": result.get("usage"),
        "answers": {"choice": choice, "confidence": confidence},
        "confidence": confidence if confidence is not None else 0.0,
        "heuristic_result": heuristic,
        "agree": choice == heuristic,
        "decision": decision,
        "tool_name": tool_name,
        "category": category,
        "advise_supported": False,
    }


def _append_event(path: Path, row: Dict[str, Any]) -> bool:
    """Never raises. Returns True only if the row was actually written. Duplicated (not
    imported) from craftflow_jev_remfix_scope.py's identically-shaped private helper, matching
    this repo's established per-script duplication convention for small, script-private I/O
    helpers (see craftflow_pretooluse_bash_guard.py's PROTECTED_MEMORY_FILES precedent).

    REM-FIX (silent-failure-hunter HIGH, forward-risk note): this bool return has no structural
    mechanism yet forcing it to be checked -- main() (Phase 4, not this file yet) MUST follow
    craftflow_jev_remfix_scope.py:207-214's pattern ("no auto-apply without a persisted audit
    row"): if a future decision path ever treats a "logged" telemetry row as meaningful before
    checking this function's return value, a failed write must downgrade that outcome the same
    way remfix_scope.py demotes "applied" to "below_threshold" when `persisted` is False. This
    feature has no "applied"-equivalent state today (see decide()'s docstring, DD-5), so there is
    nothing to downgrade yet -- but Phase 4's implementer must not skip this check once one exists."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
        return True
    except Exception:
        return False
