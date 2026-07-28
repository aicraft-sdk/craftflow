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
import re
from pathlib import Path

from craftflow_hooklib import (
    command_has_traversal_or_wildcard,
    is_env_assignment,
    load_input,
    load_mode,
    log_event,
    looks_dynamic,
    pretool_deny,
    resolve_confinement,
    split_subcommands,
)

# Plain command-name match: positional-token target model applies unchanged
# (existing `_positional_targets()` walk). `chmod` uses the identical
# positional-token model but is tracked separately per the design's
# disclosed imprecision on the mode-string token (e.g. `777` in
# `chmod 777 path` is harmlessly-but-uselessly resolved as a path too).
SIMPLE_DESTRUCTIVE = {"rm", "rmdir", "mv", "shred", "truncate"}

# Critical top-level children of cwd: "configurable" per the design means
# editable-in-source, not a new hook-mode.json schema key (Durable Decision,
# plan line 17).
CRITICAL_TOP_LEVEL_CHILDREN = (".git", "packages", "tools")

_DD_OF_RE = re.compile(r"^of=(.+)$")


def _split_command_name(tokens: list) -> tuple:
    """Return (command_name, rest_tokens), skipping leading env assignments."""
    idx = 0
    while idx < len(tokens) and is_env_assignment(tokens[idx]):
        idx += 1
    if idx >= len(tokens):
        return None, []
    return os.path.basename(tokens[idx]), tokens[idx + 1 :]


def _positional_targets(rest: list) -> tuple:
    """Return (path_tokens, has_unresolvable_token) using the generic
    positional-token model: skip flags, collect the remaining tokens as
    candidate path targets."""
    paths = []
    unresolvable = False
    past_flag_terminator = False
    for token in rest:
        if token == "--" and not past_flag_terminator:
            past_flag_terminator = True
            continue
        if not past_flag_terminator and token.startswith("-") and token != "-":
            continue
        if looks_dynamic(token):
            unresolvable = True
            continue
        paths.append(token)
    return paths, unresolvable


def _is_destructive_git(rest: list) -> bool:
    """git clean -f*, git reset --hard, git push --force (literal flag
    only -- -f/--force-with-lease are a disclosed, deliberate scope
    boundary, not covered here)."""
    if not rest:
        return False
    subcommand, later = rest[0], rest[1:]
    if subcommand == "clean":
        return any(token.startswith("-f") for token in later)
    if subcommand == "reset":
        return "--hard" in later
    if subcommand == "push":
        return "--force" in later
    return False


def _dd_target(tokens: list) -> list:
    """Scan tokens for an `of=<path>` argument and return the captured
    path(s) -- dd's overwrite target is key=value, not a positional token,
    so the generic positional-token extractor cannot see it."""
    return [match.group(1) for token in tokens for match in [_DD_OF_RE.match(token)] if match]


def _is_destructive_find(rest: list) -> bool:
    """find is destructive if `-delete` is present, or `-exec` is followed
    by an `rm` token before the next `;`/`+` terminator (the terminator is
    typically already consumed as a subcommand separator by
    split_subcommands, so scanning to the end of `rest` is equivalent)."""
    if "-delete" in rest:
        return True
    if "-exec" in rest:
        exec_idx = rest.index("-exec")
        for token in rest[exec_idx + 1 :]:
            if token in (";", "+"):
                break
            if os.path.basename(token) == "rm":
                return True
    return False


def _find_search_paths(rest: list) -> list:
    """find's own leading positional (non-flag) arguments are its search
    path(s); the first flag-like token starts the expression, not a
    target -- same escape/in-cwd logic as rm's own target."""
    paths = []
    for token in rest:
        if token.startswith("-"):
            break
        paths.append(token)
    return paths


def _destructive_targets(tokens: list) -> tuple:
    """Return (command_name, path_tokens, has_unresolvable_token) for one
    subcommand if it matches a destructive-command shape, else
    (None, [], False)."""
    command_name, rest = _split_command_name(tokens)
    if command_name is None:
        return None, [], False

    if command_name in SIMPLE_DESTRUCTIVE or command_name == "chmod":
        paths, unresolvable = _positional_targets(rest)
        return command_name, paths, unresolvable

    if command_name == "git":
        if _is_destructive_git(rest):
            # git clean/reset --hard/push --force always act on the whole
            # repository at cwd -- there is no positional path argument to
            # extract, so the target is cwd itself ("." resolves to cwd).
            return command_name, ["."], False
        return None, [], False

    if command_name == "dd":
        targets = _dd_target(rest)
        if targets:
            return command_name, targets, False
        return None, [], False

    if command_name == "find":
        if _is_destructive_find(rest):
            return command_name, _find_search_paths(rest), False
        return None, [], False

    return None, [], False


def _resolve_within(path_token: str, cwd: Path) -> bool:
    candidate = Path(os.path.expanduser(path_token))
    if not candidate.is_absolute():
        candidate = cwd / candidate
    resolved = candidate.resolve()
    return resolved == cwd or cwd in resolved.parents, resolved


def _is_in_cwd_critical(resolved: Path, cwd: Path) -> bool:
    """True if a within-cwd destructive target is cwd itself, a literal
    `*`/`.` (checked via the resolved name, since resolving away the
    literal token is exactly what makes these dangerous), or one of
    CRITICAL_TOP_LEVEL_CHILDREN."""
    if resolved == cwd:
        return True
    if resolved.name in ("*", "."):
        return True
    if resolved in {cwd / child for child in CRITICAL_TOP_LEVEL_CHILDREN}:
        return True
    return False


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
    in_cwd_critical = []
    unverifiable = []
    for tokens in split_subcommands(command):
        command_name, path_tokens, has_unresolvable = _destructive_targets(tokens)
        if command_name is None:
            continue
        if has_unresolvable:
            unverifiable.append(command_name)
        for path_token in path_tokens:
            within, resolved = _resolve_within(path_token, cwd)
            if not within:
                escapes.append(str(resolved))
            elif _is_in_cwd_critical(resolved, cwd):
                in_cwd_critical.append(str(resolved))

    if not escapes and not in_cwd_critical and not unverifiable:
        return 0

    deny_now = bool(escapes or in_cwd_critical) and block_mode
    if escapes:
        reason = f"escapes-cwd:{','.join(escapes)}"
    elif in_cwd_critical:
        reason = f"in-cwd-critical:{','.join(in_cwd_critical)}"
    else:
        reason = f"unverifiable-path:{','.join(unverifiable)}"

    log_event(
        "plugin_pretooluse_bash_guard",
        {
            "event": "pretool_guard",
            "tool_name": "Bash",
            "cwd": str(cwd),
            "command": command,
            "decision": "deny" if deny_now else "audit",
            "reason": reason,
        },
    )

    if deny_now:
        blocked = escapes or in_cwd_critical
        pretool_deny(
            "CRAFTFLOW plugin hook blocked a destructive command "
            f"({', '.join(blocked)}) resolving outside the current working "
            f"directory ({cwd}) or targeting a critical in-cwd path. If "
            "this is intentional, run it manually outside the agent "
            "session."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
