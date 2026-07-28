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
    latest_workflow_payload,
    load_input,
    load_mode,
    log_event,
    looks_dynamic,
    matches_memory_finalize_permit_shape,
    memory_finalize_permit_path,
    pretool_deny,
    project_state_dir,
    resolve_confinement,
    split_subcommands,
    state_root,
    workflows_dir,
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

# The 3 memory .md files -- duplicated-by-design from
# craftflow_pretooluse_guard.py's identically-named constant/helper (Task
# 3.2 step 2): bash_guard.py and pretooluse_guard.py are separate scripts
# with no import dependency between them, each independently defining its
# own protected-path membership.
PROTECTED_MEMORY_FILES = ("activeContext.md", "patterns.md", "progress.md")

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


def _dd_target(tokens: list) -> tuple:
    """Scan tokens for an `of=<path>` argument and return
    (path_tokens, has_unresolvable_token) -- dd's overwrite target is
    key=value, not a positional token, so the generic positional-token
    extractor cannot see it. When no `of=` token is present (e.g. a bare
    `dd if=... > file` stdout redirect with no `of=` at all), fall back to
    this dd invocation's own >/>> redirect target via
    hooklib.extract_redirect_targets() -- otherwise the whole subcommand is
    invisible to this guard (CRITICAL 3, Phase 2).

    A dynamic ($/backtick) captured `of=` value is excluded from the
    returned path tokens and flags has_unresolvable=True, exactly like the
    generic positional-target path (`_positional_targets`) already does for
    every other command shape (CRITICAL 2, REM-FIX Phase 3 review+hunt):
    this branch previously hardcoded has_unresolvable=False unconditionally,
    never calling looks_dynamic() on the captured value, so
    `dd if=/dev/zero of=$(echo ../../etc/passwd) bs=1 count=1` silently
    resolved the still-unexpanded substitution text as a harmless-looking
    in-cwd path instead of being flagged as an opaque dynamic target subject
    to the traversal-fail-closed logic in main()."""
    of_values = [match.group(1) for token in tokens for match in [_DD_OF_RE.match(token)] if match]
    if of_values:
        paths = []
        unresolvable = False
        for value in of_values:
            if looks_dynamic(value):
                unresolvable = True
            else:
                paths.append(value)
        return paths, unresolvable

    # No `of=` token present (a bare `dd ... > file` stdout redirect) --
    # fall back to this dd invocation's own >/>> redirect target. A dynamic
    # ($/backtick) redirect target is excluded from the returned path
    # tokens and flags has_unresolvable=True, exactly like the `of=` branch
    # immediately above already does (doubt-verify generalization gap: this
    # fallback previously hardcoded has_unresolvable=False unconditionally,
    # never calling looks_dynamic() on the extracted redirect target, so a
    # dynamic bare-redirect target combined with a traversal literal
    # elsewhere in the command was silently allowed).
    redirect_paths = []
    redirect_unresolvable = False
    for value in extract_redirect_targets(" ".join(tokens)):
        if looks_dynamic(value):
            redirect_unresolvable = True
        else:
            redirect_paths.append(value)
    return redirect_paths, redirect_unresolvable


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


def _find_search_paths(rest: list) -> tuple:
    """find's own leading positional (non-flag) arguments are its search
    path(s); the first flag-like token starts the expression, not a
    target -- same escape/in-cwd logic as rm's own target.

    A dynamic ($/backtick) search-path token is excluded from the returned
    path tokens and flags has_unresolvable=True, exactly like
    `_positional_targets()` already does for every other command shape and
    `_dd_target()` does for dd's `of=` value -- this generalizes the same
    fix to find's own captured tokens (doubt-verify generalization gap:
    this branch previously hardcoded has_unresolvable=False unconditionally,
    never calling looks_dynamic() on the captured search path, so a dynamic
    search path combined with a traversal literal elsewhere in the command
    was silently allowed instead of being flagged as an opaque dynamic
    target subject to the traversal-fail-closed logic in main())."""
    paths = []
    unresolvable = False
    for token in rest:
        if token.startswith("-"):
            break
        if looks_dynamic(token):
            unresolvable = True
        else:
            paths.append(token)
    return paths, unresolvable


def _destructive_targets(tokens: list) -> tuple:
    """Return (command_name, path_tokens, has_unresolvable_token) for one
    subcommand if it matches a destructive-command shape, else
    (None, [], False). Any internal parsing exception in the newer
    command-shape logic (git/dd/find/chmod's positional matching) falls
    back to a hardcoded conservative target, "." (cwd itself), instead of
    silently treating it as non-destructive/allowed (HIGH 7 -- Behavior
    Contract rule 9).

    The fallback deliberately does NOT re-call any of the shape-specific
    functions it is guarding against (_positional_targets, _is_destructive_git,
    _dd_target, etc.) -- doing so for non-structural commands (rm/mv/shred/
    truncate/chmod) previously re-invoked `_positional_targets` itself, the
    exact same function whose earlier call may have raised the exception in
    the first place. If `_positional_targets` was the failure source, that
    fallback call re-raised the identical uncaught exception, which
    propagated all the way through main() and crashed the hook process -- an
    uncaught crash (non-zero, non-2 exit) does not block the tool call, so
    this was a silent fail-open ALLOW of a genuinely destructive command
    (doubt-verify cycle 2, blocking finding). Routing every command family
    uniformly to "." makes ALL destructive-command shapes fail toward the
    same in-cwd-critical deny path when their shape-specific parsing raises,
    regardless of which specific function was the source of the exception."""
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
                "reason": "fell_back_to_conservative_cwd_target",
            },
        )
        return command_name, ["."], False


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
            #
            # A dynamic ($/backtick) dir_override is excluded from the
            # returned path tokens and flags has_unresolvable=True, exactly
            # like _positional_targets()/_dd_target() already do for every
            # other command shape's captured target (doubt-verify
            # generalization gap: this branch previously hardcoded
            # has_unresolvable=False unconditionally regardless of
            # dir_override, never calling looks_dynamic() on it, so
            # `git -C $(dynamic) reset --hard` combined with a traversal
            # literal elsewhere in the command was silently allowed instead
            # of being flagged as an opaque dynamic target subject to the
            # traversal-fail-closed logic in main()).
            if dir_override and looks_dynamic(dir_override):
                return command_name, [], True
            target = dir_override if dir_override else "."
            return command_name, [target], False
        return None, [], False

    if command_name == "dd":
        paths, unresolvable = _dd_target(rest)
        if paths or unresolvable:
            return command_name, paths, unresolvable
        return None, [], False

    if command_name == "find":
        if _is_destructive_find(rest):
            paths, unresolvable = _find_search_paths(rest)
            return command_name, paths, unresolvable
        return None, [], False

    return None, [], False


def _protected_redirect_paths() -> set:
    """Return the redirect/tee-target protected-path set: the 3 memory .md
    files (under state_root(), project_state_dir(), and every
    workflows/*/), .memory-finalize, and every workflow JSON artifact
    (workflows_dir().glob('*.json')). This is the SAME protected-path
    membership Phase 4's pretooluse_guard.py independently defines for its
    own Bash-write-inspection layer (Task 3.2 step 2) -- kept as an
    independent, duplicated-by-design check here since bash_guard.py and
    pretooluse_guard.py are separate scripts with no import dependency
    between them."""
    paths: set = set()
    try:
        paths |= {(state_root() / name).resolve() for name in PROTECTED_MEMORY_FILES}
    except Exception:
        pass
    try:
        paths |= {(project_state_dir() / name).resolve() for name in PROTECTED_MEMORY_FILES}
    except Exception:
        pass
    try:
        wf_dir = workflows_dir()
        for name in PROTECTED_MEMORY_FILES:
            for candidate in wf_dir.glob(f"*/{name}"):
                paths.add(candidate.resolve())
    except Exception:
        pass
    try:
        paths.add(memory_finalize_permit_path().resolve())
    except Exception:
        pass
    try:
        for candidate in workflows_dir().glob("*.json"):
            paths.add(candidate.resolve())
    except Exception:
        pass
    return paths


def _is_protected_redirect_target(resolved: Path) -> bool:
    """True only when a redirect/tee target resolves to one of this plan's
    protected paths. Ordinary, benign redirects (`> /dev/null`,
    `2>/dev/null`) are common, legitimate shell idioms already present in
    this plugin's own documented flows and must never be denied just
    because they resolve outside {cwd} u {worktree_path} -- this predicate
    is the gate that keeps the redirect-confinement check (Task 3.2 step 2)
    from ever running against them at all."""
    return resolved in _protected_redirect_paths()


def _redirect_targets_in_tokens(tokens: list) -> list:
    """Same target-extraction logic as hooklib.extract_redirect_targets(),
    but operating directly on an already-split subcommand's own token list
    instead of re-parsing joined token text -- keeps the subcommand's
    tokens available for matches_memory_finalize_permit_shape() shape-
    matching (CRITICAL 1, REM-FIX Phase 3 review+hunt)."""
    targets = []
    for idx, token in enumerate(tokens):
        if token in (">", ">>") and idx + 1 < len(tokens):
            targets.append(tokens[idx + 1])
        elif token == "tee":
            for t in tokens[idx + 1 :]:
                if not t.startswith("-"):
                    targets.append(t)
    return targets


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

    # Worktree confinement (Task 3.2 step 2): read worktree_path from the
    # active workflow JSON via the shared hooklib helper, never raising --
    # absence of a workflow JSON, or worktree_path: null, degrades every
    # confinement check below to cwd-only (Behavior Contract rule 8).
    try:
        worktree_path = latest_workflow_payload().get("worktree_path")
    except Exception:
        worktree_path = None

    # CRITICAL 3 (REM-FIX Phase 3 review+hunt): worktree_path is an untyped
    # read from JSON -- coerce any value that isn't str/None to None
    # immediately, so a malformed shape (e.g. an int) can never reach
    # resolve_confinement() at all (Path(<int>) raises TypeError). This is
    # in addition to, not instead of, wrapping the resolve_confinement()
    # call sites themselves below.
    if worktree_path is not None and not isinstance(worktree_path, str):
        worktree_path = None

    # Dynamic-target traversal fail-closed (Task 3.2 step 1): a dynamic
    # ($/backtick) destructive target is denied only when a traversal
    # literal or wildcard ALSO appears anywhere in the command's
    # construction (including inside $(...)); a bare dynamic target with
    # neither stays allowed (regression flow 1). Any internal parsing
    # exception here falls back to the conservative "traversal present"
    # verdict -- fail-closed for dynamic targets, not a blanket allow
    # (Behavior Contract rule 9) -- and does not re-invoke
    # command_has_traversal_or_wildcard itself.
    try:
        has_traversal_or_wildcard = command_has_traversal_or_wildcard(command)
    except Exception as exc:
        log_event(
            "plugin_pretooluse_bash_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "command_has_traversal_or_wildcard",
                "error": repr(exc),
                "reason": "fell_back_to_conservative_traversal_present",
            },
        )
        has_traversal_or_wildcard = True

    escapes = []
    in_cwd_critical = []
    unverifiable = []
    denied_dynamic = []
    for tokens in split_subcommands(command):
        command_name, path_tokens, has_unresolvable = _destructive_targets(tokens)
        if command_name is None:
            continue
        if has_unresolvable:
            if has_traversal_or_wildcard:
                denied_dynamic.append(command_name)
            else:
                unverifiable.append(command_name)
        for path_token in path_tokens:
            # CRITICAL 3 (REM-FIX Phase 3 review+hunt): this call site had no
            # try/except, unlike its sibling in the redirect-confinement
            # block below -- an uncaught exception here (e.g. a malformed
            # path_token) would crash main() entirely, which fails OPEN (a
            # non-zero/non-JSON-deny exit doesn't block the tool call). Fall
            # back to a fail-CLOSED "not confined" verdict so the escape/
            # critical checks below still apply, instead of propagating.
            try:
                confined, resolved = resolve_confinement(path_token, cwd, worktree_path)
            except Exception as exc:
                log_event(
                    "plugin_pretooluse_bash_guard",
                    {
                        "event": "pretool_guard_parse_error",
                        "command_name": "resolve_confinement",
                        "error": repr(exc),
                        "reason": "fell_back_to_not_confined",
                    },
                )
                escapes.append(str(path_token))
                continue
            if not confined:
                escapes.append(str(resolved))
            elif _is_in_cwd_critical(resolved, cwd):
                in_cwd_critical.append(str(resolved))

    # Redirect/tee-target confinement (Task 3.2 step 2), scoped ONLY to
    # targets that also resolve to a protected path -- ordinary benign
    # redirects (`> /dev/null`, `2>/dev/null`) are common, legitimate shell
    # idioms already present in this plugin's own documented flows and must
    # never be denied just because they resolve outside
    # {cwd} u {worktree_path}. Any internal parsing exception here skips
    # only this newly-added check (Behavior Contract rule 9), leaving the
    # destructive-target checks above unaffected.
    #
    # CRITICAL 1 (REM-FIX Phase 3 review+hunt): protected-path-ness and
    # confinement are two SEPARATE reasons to deny, not one subordinate to
    # the other. The previous `not confined and _is_protected_redirect_target`
    # gate meant the protected-path check never even ran whenever the target
    # was confined to cwd -- which is always true when cwd is the project
    # root (the common case), since .craftflow/state/... lives inside it.
    # Now checked unconditionally, with the one documented, load-bearing
    # exception: the router's own memory-finalize permit-write shape must
    # stay allowed even though it targets a protected path.
    protected_redirect_escapes = []
    try:
        for tokens in split_subcommands(command):
            for target in _redirect_targets_in_tokens(tokens):
                _confined, resolved = resolve_confinement(target, cwd, worktree_path)
                if not _is_protected_redirect_target(resolved):
                    continue
                if (
                    resolved == memory_finalize_permit_path().resolve()
                    and matches_memory_finalize_permit_shape(tokens, target)
                ):
                    continue
                protected_redirect_escapes.append(str(resolved))
    except Exception as exc:
        log_event(
            "plugin_pretooluse_bash_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "redirect_confinement_check",
                "error": repr(exc),
                "reason": "skipped_redirect_confinement_check",
            },
        )

    if not (
        escapes
        or in_cwd_critical
        or unverifiable
        or denied_dynamic
        or protected_redirect_escapes
    ):
        return 0

    deny_now = bool(
        escapes or in_cwd_critical or denied_dynamic or protected_redirect_escapes
    ) and block_mode

    # HIGH (REM-FIX Phase 3 review+hunt): every triggered category names its
    # own reason -- previously only the highest-priority reason string was
    # logged/returned when multiple categories fired for different targets
    # in the same command, silently dropping the other rule(s)' detail. This
    # doesn't change the allow/deny outcome (still gated by deny_now above),
    # only what's disclosed in the log/denial message (Observability
    # requirement: each denial reason should name every rule that fired).
    # `unverifiable-path` is intentionally exclusive to the other four --
    # it is itself never a deny reason (see deny_now above), only ever
    # relevant when nothing else triggered.
    reason_parts = []
    if denied_dynamic:
        reason_parts.append(f"dynamic-target-with-traversal:{','.join(denied_dynamic)}")
    if escapes:
        reason_parts.append(
            f"worktree-confinement:{','.join(escapes)}"
            if worktree_path
            else f"escapes-cwd:{','.join(escapes)}"
        )
    if protected_redirect_escapes:
        reason_parts.append(f"worktree-confinement:{','.join(protected_redirect_escapes)}")
    if in_cwd_critical:
        reason_parts.append(f"in-cwd-critical:{','.join(in_cwd_critical)}")
    if not reason_parts and unverifiable:
        reason_parts.append(f"unverifiable-path:{','.join(unverifiable)}")
    reason = "; ".join(reason_parts)

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
        pretool_deny(
            f"CRAFTFLOW plugin hook blocked a Bash command (reason: {reason}). "
            "If this is intentional, run it manually outside the agent "
            "session."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
