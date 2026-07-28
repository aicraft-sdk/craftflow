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
    MEMORY_FINALIZE_PERMIT_LITERAL,
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


def _scan_paren_depth(rest: list, start_idx: int) -> "int | None":
    """Scan tokens from start_idx (inclusive) counting `(`/`)` CHARACTERS
    within each token -- NOT exact-token equality against a bare `"("`/
    `")"` -- so a fused multi-char token like `"))"` (produced by shlex for
    a doubly-nested substitution, `$(echo $(echo $x))`) correctly closes
    TWO levels of depth within that ONE token, instead of never being
    recognized as a closing paren at all (REM-FIX doubt-verify cycle 2, Bug
    2: the old exact-token-equality check left depth permanently non-zero
    for any fused closing-paren token, so the "unterminated" fallback fired
    and silently swallowed the rest of the command's tokens -- including
    the real subcommand -- into the span).

    Returns the index of the token in which depth first returns to 0, or
    None if depth never returns to 0 across every remaining token (a
    malformed/unbalanced span -- callers MUST fail CLOSED on None, not
    treat it as "consume to the end, conservative")."""
    depth = 0
    for idx in range(start_idx, len(rest)):
        for ch in rest[idx]:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return idx
    return None


_GIT_TOKEN_SHAPE_RE = re.compile(r"^[a-zA-Z][a-zA-Z-]*$")


def _looks_like_subcommand_or_flag(token: str) -> bool:
    """True for a token that could plausibly be the NEXT git global flag or
    the actual subcommand -- a bare alphabetic identifier (e.g. "reset",
    "push", "clean", "status") or anything starting with "-". Used ONLY to
    decide when to STOP folding tokens into a git global flag's value that
    is known to contain a dynamic ($/backtick) component (see
    `_consume_git_flag_value()`): a fused non-whitespace suffix left over
    from an under-consumed `$(...)`/backtick span (e.g. a bare "(" or
    "=bar") never has this shape, so it keeps getting folded into the same
    value instead of being misread as the subcommand."""
    return bool(token) and (token.startswith("-") or bool(_GIT_TOKEN_SHAPE_RE.match(token)))


def _consume_git_flag_value(rest: list, pos: int) -> tuple:
    """Consume the full span of tokens making up ONE git global flag's
    value, where rest[pos] is either the value's own (possibly fragmented)
    token in full -- separate-arg form: `-C <value>`, `-c <value>`,
    `--git-dir <value>`, `--work-tree <value>` -- or the flag+value token
    itself for the ASSIGNMENT form (`--git-dir=<value>`,
    `--work-tree=<value>`), where a dynamic fragment's start is fused onto
    the END of that same token (e.g. `"--work-tree=$"`). Both shapes
    reduce to the identical detection: does rest[pos] END in a bare "$"
    immediately followed by "(" in the NEXT token, or does it OPEN an
    unterminated backtick?

    ROUND 4 (bugs 1 & 2, live-verified full bypasses): `-c`'s value is
    itself a `key=value` shell word, so a dynamic fragment can begin AFTER
    a static "key=" prefix fused into the SAME token -- `"foo=$"` for
    `-c foo=$(echo $x)` -- rather than the token being exactly `"$"`. The
    prior `_dynamic_span_end()` only ever recognized a bare
    `"$"`/backtick-prefixed token, so this value was never routed through
    fragment detection at all: the literal `"("` token immediately after
    was then misread as the git subcommand itself, silently defeating
    destructive-command detection entirely (bug 1). Symmetrically, once a
    dynamic span DOES close, a fused non-whitespace suffix immediately
    after the closing paren/backtick (e.g. `"=bar"` in
    `-c $(echo foo)=bar`) is ALWAYS emitted by shlex as its own separate
    token, indistinguishable at the token level from a genuinely separate
    next shell word -- verified directly: `"$(echo foo)=bar"` and
    `"$(echo foo) =bar"` (real whitespace before `=bar`) tokenize
    IDENTICALLY, because `punctuation_chars=True` shlex always splits a
    punctuation character like `)` into its own token regardless of
    whether real whitespace follows it. Given that unavoidable token-level
    ambiguity, once a value is known to be dynamic, every token
    immediately following the fragment's close that does NOT look like a
    plausible next git flag/subcommand (`_looks_like_subcommand_or_flag`)
    is folded into the same value span instead of being risked as the
    real subcommand -- fail-closed toward "still part of the untrusted
    value" (bug 2 was exactly the fused `"=bar"` token being misread as
    the subcommand next).

    Returns (value_is_dynamic, span_end_idx, malformed). span_end_idx is
    the index of the LAST token belonging to this value (the caller
    resumes scanning at span_end_idx + 1). malformed=True when a fragment
    opens but never closes -- caller MUST fail CLOSED (Behavior Contract
    rule 9), never treat malformed as "value ends here."
    """
    token = rest[pos]
    is_dynamic = looks_dynamic(token)
    span_end = pos

    if token == "$" or token.endswith("=$"):
        if pos + 1 < len(rest) and rest[pos + 1] == "(":
            found = _scan_paren_depth(rest, pos + 1)
            if found is None:
                return True, None, True
            span_end = found
        # else: a bare trailing "$"/"=$" with no following "(" -- dynamic
        # (e.g. a plain $VAR) but not a $(...) fragment; nothing more to
        # consume structurally.
    elif token.count("`") % 2 == 1:
        # An odd number of backticks means THIS token OPENS a backtick
        # substitution it does not itself close (e.g. "foo=`echo", or a
        # bare "`echo" for -C's own value) -- scan forward for the token
        # that closes it.
        scan_idx = pos + 1
        closed = False
        while scan_idx < len(rest):
            if "`" in rest[scan_idx]:
                span_end = scan_idx
                closed = True
                break
            scan_idx += 1
        if not closed:
            return True, None, True

    if is_dynamic:
        while span_end + 1 < len(rest) and not _looks_like_subcommand_or_flag(rest[span_end + 1]):
            span_end += 1

    return is_dynamic, span_end, False


def _find_git_subcommand(rest: list) -> tuple:
    """Scan past git's global options (anything starting with `-` before the
    real subcommand) to find the actual subcommand token, instead of
    assuming `rest[0]` is it -- `git -C /tmp reset --hard`, `git --no-pager
    clean -fdx`, `git -c foo=bar reset --hard` all put a global flag first
    (CRITICAL 1). Handles -C <dir>/-c <k=v> (separate-arg flags) and
    --git-dir=<path>/--work-tree=<path> (assignment-style) or
    --git-dir <path>/--work-tree <path> (separate-arg form). Returns
    (subcommand_or_none, later_tokens, dir_override_or_none, malformed,
    dir_override_dynamic) -- dir_override is the -C/--git-dir/--work-tree
    directory the destructive target should resolve against, if the LAST
    such flag's value was static; malformed is True when a fragmented
    dynamic span (either form) never closed within the remaining tokens.

    Every occurrence of a value-taking global flag (-C/-c/--git-dir/
    --work-tree, either separate-arg or assignment form) is routed through
    the single shared `_consume_git_flag_value()` (ROUND 4 consolidation --
    covers both plain single-token values and fragmented `$(...)`/backtick
    substitutions, including a value fragment beginning mid-token after a
    static `key=` prefix, and a fused non-whitespace suffix immediately
    after the fragment's close).

    dir_override_dynamic is an ACCUMULATOR, not a last-write-wins scalar
    (ROUND 4, bug 3): True if ANY occurrence of -C/--git-dir/--work-tree
    across the WHOLE command had a dynamic/unresolvable value, even if a
    LATER occurrence of the same or a different one of these flags looks
    fully static -- it never resets back to False once set, so an earlier
    dynamic flag's taint cannot be silently overwritten by a later static
    one (live-verified pre-fix bypass: `git -C $(dynamic) -C docs reset
    --hard` lost the first flag's taint entirely once the second, static-
    looking `-C docs` overwrote `dir_override`).

    A fragmented span that never closes (`malformed=True`) is NOT treated
    as "no subcommand found = non-destructive" -- callers must fail CLOSED
    (Behavior Contract rule 9; see `_match_destructive_shape()`'s git
    branch)."""
    dir_override = None
    dir_override_dynamic = False
    idx = 0
    while idx < len(rest):
        token = rest[idx]
        if not token.startswith("-"):
            return token, rest[idx + 1 :], dir_override, False, dir_override_dynamic
        if token in _GIT_ARG_FLAGS:
            if idx + 1 < len(rest):
                value_idx = idx + 1
                is_dynamic, span_end, malformed = _consume_git_flag_value(rest, value_idx)
                if malformed:
                    return None, [], dir_override, True, dir_override_dynamic
                if token == "-C":
                    if is_dynamic:
                        dir_override_dynamic = True
                        dir_override = None
                    else:
                        dir_override = rest[value_idx]
                idx = span_end + 1
            else:
                idx += 1
            continue
        if token.startswith("--git-dir=") or token.startswith("--work-tree="):
            value = token.split("=", 1)[1]
            is_dynamic, span_end, malformed = _consume_git_flag_value(rest, idx)
            if malformed:
                return None, [], dir_override, True, dir_override_dynamic
            if is_dynamic:
                dir_override_dynamic = True
                dir_override = None
            else:
                dir_override = value
            idx = span_end + 1
            continue
        if token in ("--git-dir", "--work-tree") and idx + 1 < len(rest):
            value_idx = idx + 1
            is_dynamic, span_end, malformed = _consume_git_flag_value(rest, value_idx)
            if malformed:
                return None, [], dir_override, True, dir_override_dynamic
            if is_dynamic:
                dir_override_dynamic = True
                dir_override = None
            else:
                dir_override = rest[value_idx]
            idx = span_end + 1
            continue
        idx += 1
    return None, [], dir_override, False, dir_override_dynamic


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
    boundary, not covered here). Returns (is_destructive, dir_override,
    malformed, dir_override_dynamic) -- malformed=True forces
    is_destructive=True regardless of which subcommand (if any) was found:
    an unterminated/malformed fragmented dynamic span must not silently
    un-recognize an otherwise-destructive git invocation (REM-FIX
    doubt-verify cycle 2, Bug 2 continuation; Behavior Contract rule 9).
    dir_override_dynamic is threaded straight through from
    `_find_git_subcommand()`'s accumulator (ROUND 4, bug 3)."""
    subcommand, later, dir_override, malformed, dir_override_dynamic = _find_git_subcommand(rest)
    if malformed:
        return True, dir_override, True, dir_override_dynamic
    if subcommand is None:
        return False, dir_override, False, dir_override_dynamic
    if subcommand == "clean":
        return _has_force_flag(later), dir_override, False, dir_override_dynamic
    if subcommand == "reset":
        return "--hard" in later, dir_override, False, dir_override_dynamic
    if subcommand == "push":
        return "--force" in later, dir_override, False, dir_override_dynamic
    return False, dir_override, False, dir_override_dynamic


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
        is_destructive, dir_override, malformed, dir_override_dynamic = _is_destructive_git(rest)
        if malformed:
            # Fail CLOSED (Behavior Contract rule 9; REM-FIX doubt-verify
            # cycle 2, Bug 2 continuation): an unterminated/malformed
            # fragmented dynamic span (assignment-style
            # --git-dir=/--work-tree= or separate-arg -C/-c/--git-dir/
            # --work-tree) must not silently un-recognize an otherwise-
            # destructive git invocation just because span-detection itself
            # failed to parse it -- force the same conservative in-cwd-
            # critical "." target every other parse failure in this module
            # already falls back to (see `_destructive_targets()`'s own
            # except-Exception fallback), rather than treating "couldn't
            # find the subcommand" as "not destructive."
            return command_name, ["."], False
        if is_destructive:
            # git clean/reset --hard/push --force act on the repository at
            # cwd by default, but -C <dir>/--work-tree=<dir> retargets the
            # whole invocation -- resolve against that directory instead of
            # assuming "." (CRITICAL 1).
            #
            # ROUND 4 (bug 3): dir_override_dynamic is an ACCUMULATOR from
            # `_find_git_subcommand()` -- True if ANY occurrence of
            # -C/--git-dir/--work-tree anywhere in the command had a
            # dynamic value, regardless of what a LATER occurrence's value
            # looked like. Checking this accumulator (rather than
            # re-deriving dynamic-ness from the final `dir_override` value
            # alone, which is always None whenever the accumulator is set)
            # is what keeps an earlier dynamic flag's taint from being
            # silently overwritten by a later static-looking one.
            if dir_override_dynamic:
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
                    and matches_memory_finalize_permit_shape(tokens, MEMORY_FINALIZE_PERMIT_LITERAL)
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
