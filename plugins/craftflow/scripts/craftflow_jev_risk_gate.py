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
see test_content_and_new_string_never_inspected).

NOTE: this module currently exposes only the pure allowlist matcher (Task 3.1). Pure builders
(build_state/build_questions/decide/telemetry_row/_append_event, including DD-8 redact_action()
and DD-9 _relativize_path()) land in Task 3.2; main() -- the stdin-JSON-driven, fail-open hook
entry point -- lands in Phase 4 of the implementation plan.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, List, Optional

from craftflow_hooklib import (
    is_env_assignment,
    split_subcommands,
)

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
