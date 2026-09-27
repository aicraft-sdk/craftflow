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
        category = _rm_rf_or_force_push_category(command)
        if category is not None:
            return category
        return "db_drop" if _db_drop_match(command) else None
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

_URL_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]{0,20}://[^\s:/@]{1,256}:)[^\s@/]{1,512}@")
_ASSIGNMENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{0,128})=(\"[^\"]{0,4096}\"|'[^']{0,4096}'|\S{1,4096})")
_SECRET_FLAG = re.compile(
    r"(--(?:password|passwd|token|secret|api-key|apikey)(?:=|\s+))(\"[^\"]{0,4096}\"|'[^']{0,4096}'|\S{1,4096})",
    re.IGNORECASE,
)
_SENSITIVE_NAME_PARTS = ("PASSWORD", "PASSWD", "PWD", "TOKEN", "SECRET", "APIKEY", "API_KEY", "CREDENTIAL", "AUTH")
_REDACTED = "***"


def _redact_assignment(match: "re.Match[str]") -> str:
    name = match.group(1)
    if any(part in name.upper() for part in _SENSITIVE_NAME_PARTS):
        return f"{name}={_REDACTED}"
    return match.group(0)


def redact_action(text: str, api_key: Optional[str]) -> Tuple[str, bool]:
    """Pure. Masks values that must never leave the machine, in order: (1) the literal
    `api_key` if non-empty, (2) URL userinfo passwords, (3) credential-shaped `NAME=value`
    env-style assignments, (4) `--password`/`--token`/`--secret`/`--api-key`-style flag values.
    Never raises. Returns (possibly-modified text, whether anything was actually masked)."""
    out = text
    if api_key:
        out = out.replace(api_key, _REDACTED)
    out = _URL_USERINFO.sub(r"\1" + _REDACTED + "@", out)
    out = _ASSIGNMENT.sub(_redact_assignment, out)
    out = _SECRET_FLAG.sub(r"\1" + _REDACTED, out)
    return out, out != text


def _relativize_path(file_path: str, cwd: Optional[str]) -> str:
    """Pure (DD-9). Returns `file_path` made relative to `cwd` when `file_path` is a descendant
    of `cwd`; otherwise returns the bare basename. Never returns an absolute path -- avoids
    leaking a home-directory or OS username segment to the third-party classifier. Never raises
    on malformed input (missing cwd, non-path strings, etc.)."""
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
    content/new_string/old_string (see module docstring, P1). The Bash command string is run
    through `redact_action()` BEFORE truncation (DD-8) -- credential masking must see the full
    text, not a value already cut off mid-secret."""
    redacted_text, was_redacted = redact_action(action_text, api_key)
    truncated = len(redacted_text) > max_chars
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
    helpers (see craftflow_pretooluse_bash_guard.py's PROTECTED_MEMORY_FILES precedent)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
        return True
    except Exception:
        return False
