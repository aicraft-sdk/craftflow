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
    extract_redirect_targets,
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

# mv's long-option destination -- carries a real path despite starting with
# "-", so it must not be silently skipped as a bare flag (HIGH 6).
_TARGET_DIRECTORY_RE = re.compile(r"^--target-directory=(.+)$")

# mv's ATTACHED short-option destination form (no space, no "=" -- e.g.
# `-t/tmp`), distinct from the bare `-t <dir>` two-token form (already
# handled by the generic positional fallback since the following token
# doesn't start with "-"). Finding 1, REM-FIX doubt-verify cycle 1: this
# attached form was silently skipped as a bare flag, hiding the real
# destination from confinement checking entirely.
_TARGET_DIRECTORY_SHORT_RE = re.compile(r"^-t(.+)$")

# Bundled short git-clean force flags where `f` isn't necessarily the first
# character (e.g. -xdf, -df) -- single leading "-", no "--", containing an
# `f` anywhere (CRITICAL 2).
_BUNDLED_SHORT_FORCE_RE = re.compile(r"^-[a-zA-Z]*f[a-zA-Z]*$")

# git subcommand-shaped destructive actions reachable via -exec/-execdir/
# -ok/-okdir, not just the exact "-exec" token (HIGH 4).
_FIND_ACTION_FLAGS = ("-exec", "-execdir", "-ok", "-okdir")

# git global options that consume a following, separate token as their
# argument (as opposed to --git-dir=<path>-style assignment flags).
_GIT_ARG_FLAGS = {"-C", "-c"}


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
    candidate path targets. `--target-directory=<dir>` (mv's long-option
    destination) is a disguised path-carrying flag -- captured explicitly
    instead of being silently skipped like a bare flag (HIGH 6)."""
    paths = []
    unresolvable = False
    past_flag_terminator = False
    for token in rest:
        if token == "--" and not past_flag_terminator:
            past_flag_terminator = True
            continue
        if not past_flag_terminator and token.startswith("-") and token != "-":
            match = _TARGET_DIRECTORY_RE.match(token) or _TARGET_DIRECTORY_SHORT_RE.match(token)
            if match:
                captured = match.group(1)
                if looks_dynamic(captured):
                    unresolvable = True
                else:
                    paths.append(captured)
            continue
        if looks_dynamic(token):
            unresolvable = True
            continue
        paths.append(token)
    return paths, unresolvable


def _find_git_subcommand(rest: list) -> tuple:
    """Scan past git's global options (anything starting with `-` before the
    real subcommand) to find the actual subcommand token, instead of
    assuming `rest[0]` is it -- `git -C /tmp reset --hard`, `git --no-pager
    clean -fdx`, `git -c foo=bar reset --hard` all put a global flag first
    (CRITICAL 1). Handles -C <dir>/-c <k=v> (separate-arg flags) and
    --git-dir=<path>/--work-tree=<path> (assignment-style) or
    --git-dir <path>/--work-tree <path> (separate-arg form). Returns
    (subcommand_or_none, later_tokens, dir_override_or_none) -- dir_override
    is the -C/--work-tree directory the destructive target should resolve
    against, if one was given."""
    dir_override = None
    idx = 0
    while idx < len(rest):
        token = rest[idx]
        if not token.startswith("-"):
            return token, rest[idx + 1 :], dir_override
        if token in _GIT_ARG_FLAGS:
            if token == "-C" and idx + 1 < len(rest):
                dir_override = rest[idx + 1]
            idx += 2
            continue
        if token.startswith("--git-dir=") or token.startswith("--work-tree="):
            dir_override = token.split("=", 1)[1]
            idx += 1
            continue
        if token in ("--git-dir", "--work-tree") and idx + 1 < len(rest):
            dir_override = rest[idx + 1]
            idx += 2
            continue
        idx += 1
    return None, [], dir_override


def _has_force_flag(tokens: list) -> bool:
    """True if any token is exactly -f/--force, or a bundled short-option
    token (single leading -, no --) containing an `f` anywhere -- e.g.
    -xdf, -df, not just tokens where `f` happens to be first (CRITICAL 2)."""
    for token in tokens:
        if token in ("-f", "--force"):
            return True
        if _BUNDLED_SHORT_FORCE_RE.fullmatch(token):
            return True
    return False


def _is_destructive_git(rest: list) -> tuple:
    """git clean -f*/--force, git reset --hard, git push --force (literal
    flag only -- -f/--force-with-lease are a disclosed, deliberate scope
    boundary, not covered here). Returns (is_destructive, dir_override)."""
    subcommand, later, dir_override = _find_git_subcommand(rest)
    if subcommand is None:
        return False, dir_override
    if subcommand == "clean":
        return _has_force_flag(later), dir_override
    if subcommand == "reset":
        return "--hard" in later, dir_override
    if subcommand == "push":
        return "--force" in later, dir_override
    return False, dir_override


def _dd_target(tokens: list) -> list:
    """Scan tokens for an `of=<path>` argument and return the captured
    path(s) -- dd's overwrite target is key=value, not a positional token,
    so the generic positional-token extractor cannot see it. When no `of=`
    token is present (e.g. a bare `dd if=... > file` stdout redirect with no
    `of=` at all), fall back to this dd invocation's own >/>> redirect
    target via hooklib.extract_redirect_targets() -- otherwise the whole
    subcommand is invisible to this guard (CRITICAL 3)."""
    of_targets = [match.group(1) for token in tokens for match in [_DD_OF_RE.match(token)] if match]
    if of_targets:
        return of_targets
    return extract_redirect_targets(" ".join(tokens))


def _is_destructive_find(rest: list) -> bool:
    """find is destructive if `-delete` is present, or any of
    -exec/-execdir/-ok/-okdir (HIGH 4 -- not just the exact `-exec` token)
    is followed by an `rm` token before the next `;`/`+` terminator (the
    terminator is typically already consumed as a subcommand separator by
    split_subcommands, so scanning to the end of `rest` is equivalent)."""
    if "-delete" in rest:
        return True
    for flag in _FIND_ACTION_FLAGS:
        if flag in rest:
            exec_idx = rest.index(flag)
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


# Command families whose destructive target is implicit/structural (the
# repo/device at cwd, or a key=value argument) rather than a plain
# positional path argument -- the generic positional-token model has no
# valid interpretation for these shapes (e.g. git's own "reset"/"HEAD~1"
# tokens would resolve as harmless relative filenames, never triggering
# escape/in-cwd-critical checks, silently ALLOWing a genuinely destructive
# command end-to-end). Finding 2, REM-FIX doubt-verify cycle 1.
_STRUCTURAL_TARGET_COMMANDS = {"git", "dd"}


def _destructive_targets(tokens: list) -> tuple:
    """Return (command_name, path_tokens, has_unresolvable_token) for one
    subcommand if it matches a destructive-command shape, else
    (None, [], False). Any internal parsing exception in the newer
    command-shape logic (git/dd/find/chmod's positional matching) falls
    back to a conservative target for that subcommand's own tokens, instead
    of silently treating it as non-destructive/allowed (HIGH 7 -- Behavior
    Contract rule 9). For `git`/`dd` -- whose destructive target is
    implicit/structural, not a plain positional path argument -- the
    fallback targets "." (cwd itself), matching what the non-exceptional
    code path already does for these command shapes and routing into the
    same in-cwd-critical deny logic (Finding 2). For truly generic
    simple-positional commands (rm/mv/shred/truncate/chmod/find) where the
    positional-token model's assumptions are actually valid, the existing
    generic fallback is kept unchanged."""
    command_name, rest = _split_command_name(tokens)
    if command_name is None:
        return None, [], False

    try:
        return _match_destructive_shape(command_name, rest)
    except Exception as exc:
        log_event(
            "plugin_pretooluse_bash_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": command_name,
                "error": repr(exc),
                "reason": "fell_back_to_conservative_positional_targets",
            },
        )
        if command_name in _STRUCTURAL_TARGET_COMMANDS:
            return command_name, ["."], False
        paths, unresolvable = _positional_targets(rest)
        return command_name, paths, unresolvable


def _match_destructive_shape(command_name: str, rest: list) -> tuple:
    """The actual per-command-shape matching `_destructive_targets` wraps
    in a try/except -- kept separate so the exception boundary is explicit
    about exactly what it's guarding."""
    if command_name in SIMPLE_DESTRUCTIVE or command_name == "chmod":
        paths, unresolvable = _positional_targets(rest)
        return command_name, paths, unresolvable

    if command_name == "git":
        is_destructive, dir_override = _is_destructive_git(rest)
        if is_destructive:
            # git clean/reset --hard/push --force act on the repository at
            # cwd by default, but -C <dir>/--work-tree=<dir> retargets the
            # whole invocation -- resolve against that directory instead of
            # assuming "." (CRITICAL 1).
            target = dir_override if dir_override else "."
            return command_name, [target], False
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
    literal token is exactly what makes these dangerous), one of
    CRITICAL_TOP_LEVEL_CHILDREN itself, or a DESCENDANT of one of them
    (HIGH 5 -- `rm -rf packages/agent-cli` is a real, previously-invisible
    bypass of the exact-equality-only check)."""
    if resolved == cwd:
        return True
    if resolved.name in ("*", "."):
        return True
    critical_paths = {cwd / child for child in CRITICAL_TOP_LEVEL_CHILDREN}
    if resolved in critical_paths:
        return True
    return any(critical in resolved.parents for critical in critical_paths)


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
