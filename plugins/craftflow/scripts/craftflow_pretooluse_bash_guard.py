#!/usr/bin/env python3
"""PreToolUse guard for the Bash tool.

Blocks rm/rmdir invocations whose resolved target path escapes the
session's own current working directory (the agent's assigned worktree
or the project root) -- e.g. a relative-path traversal like
`.claude/worktrees/wf-xxx/../../..` that resolves above the worktree.

See docs/incidents/2026-07-25-phase3-verifier-rm-attempt.md: a dispatched
subagent issued exactly this shape of command and was only stopped by the
harness's own built-in destructive-command detector, not by anything
craftflow shipped. This hook is craftflow's own deterministic layer for
the same failure mode.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path

from craftflow_hooklib import load_input, load_mode, log_event, pretool_deny

DESTRUCTIVE_COMMANDS = {"rm", "rmdir"}
CONTROL_OPERATORS = {";", "&&", "||", "|", "&", "\n"}


def _split_subcommands(command: str) -> list:
    """Split a shell command string on control operators (;, &&, ||, |, &).

    Best-effort tokenization (not a full shell parser) -- intentionally
    blunt, matching the rest of this guard's deterministic-but-imperfect
    scope.
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


def _is_env_assignment(token: str) -> bool:
    if "=" not in token:
        return False
    name = token.split("=", 1)[0]
    return name.isidentifier()


def _looks_dynamic(token: str) -> bool:
    return "$" in token or "`" in token


def _target_paths(tokens: list) -> tuple:
    """Return (command_name, path_tokens, has_unresolvable_token) for one subcommand."""
    idx = 0
    while idx < len(tokens) and _is_env_assignment(tokens[idx]):
        idx += 1
    if idx >= len(tokens):
        return None, [], False

    command_name = os.path.basename(tokens[idx])
    rest = tokens[idx + 1 :]

    paths = []
    unresolvable = False
    past_flag_terminator = False
    for token in rest:
        if token == "--" and not past_flag_terminator:
            past_flag_terminator = True
            continue
        if not past_flag_terminator and token.startswith("-") and token != "-":
            continue
        if _looks_dynamic(token):
            unresolvable = True
            continue
        paths.append(token)
    return command_name, paths, unresolvable


def _resolve_within(path_token: str, cwd: Path) -> bool:
    candidate = Path(os.path.expanduser(path_token))
    if not candidate.is_absolute():
        candidate = cwd / candidate
    resolved = candidate.resolve()
    return resolved == cwd or cwd in resolved.parents, resolved


def main() -> int:
    data = load_input()
    if data.get("tool_name") != "Bash":
        return 0

    command = (data.get("tool_input") or {}).get("command")
    if not command or not isinstance(command, str):
        return 0

    cwd_raw = data.get("cwd")
    if not cwd_raw:
        return 0
    cwd = Path(cwd_raw).resolve()

    mode = load_mode()
    block_mode = mode.get("bashDestructiveTraversal", "block") == "block"

    escapes = []
    unverifiable = []
    for tokens in _split_subcommands(command):
        command_name, path_tokens, has_unresolvable = _target_paths(tokens)
        if command_name not in DESTRUCTIVE_COMMANDS:
            continue
        if has_unresolvable:
            unverifiable.append(command_name)
        for path_token in path_tokens:
            within, resolved = _resolve_within(path_token, cwd)
            if not within:
                escapes.append(str(resolved))

    if not escapes and not unverifiable:
        return 0

    log_event(
        "plugin_pretooluse_bash_guard",
        {
            "event": "pretool_guard",
            "tool_name": "Bash",
            "cwd": str(cwd),
            "command": command,
            "decision": "deny" if escapes and block_mode else "audit",
            "reason": (
                f"escapes-cwd:{','.join(escapes)}" if escapes else f"unverifiable-path:{','.join(unverifiable)}"
            ),
        },
    )

    if escapes and block_mode:
        pretool_deny(
            "CRAFTFLOW plugin hook blocked a destructive command "
            f"({', '.join(escapes)}) resolving outside the current working "
            f"directory ({cwd}). If this is intentional, run it manually "
            "outside the agent session."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
