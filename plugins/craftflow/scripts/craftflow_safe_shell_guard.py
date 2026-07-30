#!/usr/bin/env python3
"""PreToolUse guard for the Bash tool -- absolute denylist of catastrophic
shell command patterns, regardless of where they resolve.

Concept ported (not code -- grok-build is Rust/Apache-2.0/no-external-
contributions) from xai-org/grok-build's xai-grok-hooks example guard pack
(safe-shell-guard.sh + no-recursive-grep-guard.py). See this plugin's NOTICE
for attribution.

Complementary relationship to craftflow_pretooluse_bash_guard.py (READ THIS
FIRST if touching either file): that guard's threat model is narrow --
it ONLY denies rm/rmdir invocations whose resolved target path escapes the
session's own cwd/worktree (path-traversal, e.g. `.claude/worktrees/wf-x/
../../..`). It does NOT catch `rm -rf /`, `rm -rf .` (destructive but not a
traversal), `mkfs`, or fork-bomb patterns -- none of those involve a path
escaping cwd at all. This guard is the opposite shape: a small, absolute
denylist of catastrophic command patterns regardless of location (inside or
outside cwd). Neither guard supersedes the other; both are registered
together on the same PreToolUse/Bash matcher in hooks.json.

Most valuable for the *sandboxed agents* @ai-craft/sandbox spawns
(claude-code/codex/aider/opencode) -- an untrusted or misbehaving agent
process running inside a sandbox is exactly the actor this denylist exists
to stop before a single catastrophic command reaches a real shell.

Denies (exit 0, hookSpecificOutput.permissionDecision=deny -- matching this
plugin's existing PreToolUse convention, see craftflow_pretooluse_bash_guard.py):
  - `rm -rf /` and narrow root-destroy variants (`rm -rf //`, `rm -rf /*`).
  - `mkfs` and any `mkfs.<fstype>` variant (inherently destructive; no path
    check needed -- formatting is destructive at any target).
  - The classic shell fork-bomb pattern (`:(){ :|:& };:`) and any renamed
    variant with the same self-referential recursive-function shape --
    detected via a cheap linear literal pre-filter plus a small bounded
    window around each candidate, NOT an unconditional full-string regex
    scan (see CRITICAL-B note on `_matches_fork_bomb`/`FORK_BOMB_RE`: the
    naive full-string scan was independently confirmed super-linear/
    quadratic and could hang the hook for seconds to minutes on ANY long
    ordinary command, adversarial or not).
  - Any of the above reachable through a recognized wrapper/prefix form,
    unwrapped via `_skip_wrapper_prefixes` (simple argv0-advancing prefixes)
    and/or `_wrapped_command_string`/`_check_tokens` (string-embedding
    wrappers, recursively re-tokenized up to `MAX_UNWRAP_DEPTH`):
    `sudo` (incl. `--user`/`--group`/other value-taking long flags, both
    space-separated and `=`-joined) and/or `command` builtin prefixes;
    `env` (with its own var-assignments/`-u`/`--unset`/`-C`/`--chdir`
    flags); `nice` (with its own `-n`/`--adjustment` value-taking flag,
    space-separated or `=`-joined); `nohup`/`watch` (their own bare
    dash-flags, e.g. `nohup --`, `watch -n1`); `chroot`/`flock` (their own
    single positional newroot/lockpath argument skipped, e.g. `chroot /`,
    `flock /tmp/x`); `timeout <duration>` (with its own `-s`/`--signal`,
    `-k`/`--kill-after` value-taking flags); `xargs` (bare dash-flags
    skipped, e.g. `-I{}`); `eval "<command>"`, `su -c "<command>"`, or an
    interpreter `-c` invocation (`bash`/`sh`/`zsh`/`dash -c "..."`);
    `ssh <host> <command...>` (embedded remote-command string); the
    inline-code flag of `python`/`python3` (`-c`), `perl`/`ruby` (`-e`), or
    `node` (`-e`/`--eval`) -- for these, ALSO best-effort regex-extracts a
    fixed list of common shell-out call shapes from the code string
    (`os.system`/`os.popen`/`subprocess.*`, bare `system(...)`,
    `child_process.exec[Sync]`/`execSync`) and recursively checks the
    extracted argument; a best-effort, narrow `find <path> -exec rm -rf {}
    \;`/`+` extraction (recognizes the placeholder+broad-path shape
    specifically, plus generic recursion into the `-exec` command for
    nested wrappers like `bash -c`).
  - A dynamic/indirect command-name token at the argv0 position -- either
    `$`/backtick substitution (e.g. `X=rm; $X -rf /`, `$(echo rm) -rf /`)
    or brace-expansion syntax (e.g. `{r,}m -rf /`, `r{m,} -rf /`,
    `{,}rm -rf /`) -- fails CLOSED ("cannot verify safety") rather than
    silently falling through with a name that will never literally match
    `rm`/`mkfs...`.
  - Any command this guard cannot safely tokenize at all (e.g. an unbalanced
    quote) -- fails CLOSED, not open: an unparseable command is treated as
    catastrophic rather than silently skipped, since a real attack embedded
    in a malformed-looking string must never slip through as "nothing to
    check" (see `_split_subcommands`).

Optional, OFF BY DEFAULT (mode.recursiveGrepGuard in hook-mode.json, "off" |
"warn" | "block"): flags `grep`/`egrep`/`fgrep` with a recursive flag
(-r/-R/--recursive) targeting a broad/root-ish path (`/`, `~`, `$HOME`).
"warn" logs an audit event only, never denies; "block" denies. Deliberately
narrow to avoid false positives: only dash-prefixed FLAG tokens are ever
inspected for the recursive flag, so a search pattern that merely contains
the word "recursive" is never misidentified as the flag, and a recursive
flag embedded inside a quoted string argument to an unrelated command (not
tokenized as its own subcommand) is never misidentified as a real invocation.

HONEST SCOPE / KNOWN LIMITATIONS (this guard is NOT an absolute or general
shell-semantics analyzer, regardless of how earlier revisions of this
docstring characterized it):
  - `xargs`/`find -exec` command-substitution: only a narrow, best-effort
    shape is recognized (bare dash-flag `xargs` prefixes; a `find ...
    -exec rm -rf {} \;`/`+` placeholder+broad-path pattern, plus generic
    recursion into the `-exec` command's own tokens). Arbitrary/creative
    `xargs`/`find -exec` command construction is NOT generally resolved.
  - Interpreter `-c`/`-e` code-string inspection is regex/text-based
    against a FIXED list of known shell-out call shapes, not a real
    language parser -- creative obfuscation (string concatenation,
    `eval()`, backticks, `%x{}`, base64-decoded code, etc.) is NOT
    resolved.
  - Dynamic/indirect command-name detection covers the argv0 position only
    (`_looks_dynamic` on the resolved command-name token: `$`/backtick
    substitution AND brace-expansion syntax as of round 3). Arbitrary
    variable/command-substitution/brace-expansion indirection elsewhere in
    a command (e.g. inside an unrecognized wrapper's own flags, or inside
    an rm TARGET rather than argv0 -- see `_parse_rm_invocation`'s own
    `_looks_dynamic` use, which SKIPS the root-target check on a dynamic
    target rather than failing closed on it, an existing, unrelated
    asymmetry not addressed this round) is not exhaustively tracked.
  - `chroot`/`flock` are unwrapped as bare prefixes (their own single
    newroot/lockpath positional argument is skipped), but their own
    dash-flags (e.g. `chroot --userspec=...`) are NOT parsed -- an
    invocation that puts a flag before the positional argument will
    misresolve. Additionally, the wrapped command is then checked under
    this guard's EXISTING, unchanged root-target-only rm scope (see
    `_is_root_target`): a `chroot <newroot> rm -rf <non-root-path>` (e.g.
    `chroot / rm -rf /tmp`) is NOT flagged, identically to how an
    unwrapped `rm -rf /tmp` is not flagged -- expanding rm-target scope
    beyond the literal filesystem root remains out of scope.
  - `ssh`/`su` string-unwrapping is narrow and common-shape only: `ssh`
    does not recognize its own flags (e.g. `ssh -p 2222 host cmd` -- the
    `-p` token would be misread as the host); only `su -c "<command>"` is
    unwrapped, not su's other forms.
  - The fork-bomb detector's bounded window (`_FORK_BOMB_WINDOW_RIGHT`,
    widened this round to 8192 chars) is still a FIXED bound, not
    unconditional -- a fork-bomb body padded with WHITESPACE ONLY past
    that bound before its closing `};name` tail is a known, documented
    residual gap; this length limit is a deliberate tradeoff to avoid
    reintroducing the ReDoS the windowing mechanism exists to prevent.
  - MORE SEVERE, DISTINCT GAP (independently found and NOT fixed this
    round -- deliberately deferred per explicit user direction to stop
    remediation on this file after this round): `FORK_BOMB_RE`'s own
    pattern (`\{\s*(?P=name)`) requires the function body to be PURE
    WHITESPACE between `{` and the recursive call -- this is a length-
    INDEPENDENT gap, not a window-size issue. A SINGLE short comment
    line (e.g. `:(){\n# x\n:|:&\n};:`, confirmed valid via `bash -n` and
    a real, executable fork bomb) is never detected, at ANY length,
    because `\s*` cannot span non-whitespace characters at all. Widening
    the window in this round improved the length-limited whitespace-only
    case but does NOT address comment-padded (or any other
    non-whitespace-padded) bodies. Fixing this properly would require
    either stripping comments before matching or a materially different
    detection strategy -- out of scope for this round.
  - Any wrapper/interpreter/shell construct not explicitly named above
    (e.g. `awk`/`perl` one-liners without `-e`, arbitrary custom scripts,
    other language runtimes) is NOT unwrapped.
  This guard is one layer of defense-in-depth, not the sole safety
  mechanism -- see also `craftflow_pretooluse_bash_guard.py`'s path-escape
  checks and this plugin's general fail-safe posture.
"""
from __future__ import annotations

import os
import re
import shlex

from craftflow_hooklib import load_input, load_mode, log_event, pretool_deny

CONTROL_OPERATORS = {";", "&&", "||", "|", "&", "\n"}
BROAD_GREP_TARGETS = {"~", "$HOME"}
INTERPRETER_C_SHELLS = {"bash", "sh", "zsh", "dash"}
# Sudo flags (short and long) that consume a SEPARATE following token as
# their value in space-separated form (CRITICAL-A fix: previously only
# "-u"/"-g" were recognized, so `sudo --user root ...`/`sudo --group root
# ...` fell through with command_name=="root", evading every check below).
SUDO_VALUE_FLAGS = {
    "-u", "--user",
    "-g", "--group",
    "-h", "--host",
    "-D", "--chdir",
    "-C", "--close-from",
    "-p", "--prompt",
    "-R", "--chroot",
    "-r", "--role",
    "-t", "--type",
    "-T", "--command-timeout",
    "-U", "--other-user",
}
# `env`'s own value-taking flags (space-separated form); mirrors the sudo
# skip shape above.
ENV_VALUE_FLAGS = {"-u", "--unset", "-C", "--chdir"}
# `nice`'s own value-taking flag (space-separated or `=`-joined form).
# ROUND-3 CRITICAL-3 fix: `nice -n 19 rm -rf /` / `nice --adjustment=19
# rm -rf /` previously fell through with command_name=="19"/the whole
# `--adjustment=19` token (neither ever literally "rm"), evading every
# downstream check -- `nice -n <N>` is the single most common real-world
# `nice` invocation, not an edge case.
NICE_VALUE_FLAGS = {"-n", "--adjustment"}
# `timeout`'s own value-taking flags (space-separated form); mirrors the
# sudo/env skip shape above. ROUND-3 CRITICAL-3 fix: `timeout --signal=KILL
# 5 rm -rf /` / `timeout -k 5 30 rm -rf /` previously fell through the same
# way as the nice case above.
TIMEOUT_VALUE_FLAGS = {"-s", "--signal", "-k", "--kill-after"}
# Bare pass-through prefixes whose own leading dash-flags are skipped (no
# separate value-token to consume -- mirrors `command`'s/`xargs`'s bare
# dash-flag skip shape). ROUND-3 HIGH fix adds `watch` here alongside the
# pre-existing `nohup`.
BARE_FLAG_SKIP_PREFIX_COMMANDS = {"nohup", "watch"}
# Prefixes whose single leading POSITIONAL (non-flag) argument -- not a
# dash-flag -- is skipped before the real wrapped command: chroot's NEWROOT
# path, flock's lock-file/lock-dir path. ROUND-3 HIGH fix: `chroot / rm -rf
# /tmp` / `flock /tmp/x rm -rf /` previously fell through with
# command_name==<the newroot/lockpath argument itself>. Deliberately NOT
# parsing these commands' own optional dash-flags (e.g. `chroot
# --userspec=...`) -- see module docstring's honest-scope note.
POSITIONAL_ARG_SKIP_PREFIX_COMMANDS = {"chroot", "flock"}
# Interpreter inline-code flags (CRITICAL-C): the flag whose following/
# joined value is a string of interpreter code to recursively check.
INTERPRETER_INLINE_FLAGS = {
    "python": ("-c",),
    "python3": ("-c",),
    "perl": ("-e", "-E"),
    "ruby": ("-e",),
    "node": ("-e", "--eval"),
}
SCRIPTING_INTERPRETERS = set(INTERPRETER_INLINE_FLAGS)
# Best-effort, fixed list of common OS-command-execution call shapes to
# regex-extract a shell-command string argument from within interpreter
# code (CRITICAL-C) -- NOT a real language parser, see module docstring.
DANGEROUS_CALL_RE = re.compile(
    r"(?:os\.system|os\.popen|subprocess\.(?:call|run|Popen|check_call|check_output)"
    r"|child_process\.exec(?:Sync)?|\bexecSync\b|\bsystem)\s*\(\s*(['\"])(.*?)\1"
)

# Bounds recursive eval/`sh -c` unwrapping against pathological/adversarial
# nesting (e.g. `bash -c "bash -c \"bash -c ...\""`). Once the bound is hit
# without resolving to a plain command, the check below (depth >=
# MAX_UNWRAP_DEPTH) stops recursing but does NOT allow -- unwrap failure and
# depth-exhaustion both fail closed (see CRITICAL-2 note in _check_tokens).
MAX_UNWRAP_DEPTH = 5

# Matches the classic `:(){ :|:& };:` fork bomb and any renamed variant with
# the same self-referential shape: `name(){ name | name & };name`. `:` is
# accepted as a "name" in addition to ordinary identifiers since bash allows
# redefining the `:` builtin as a function -- the exact form the canonical
# fork bomb uses.
#
# CRITICAL-B: this pattern is intentionally NEVER run unconditionally
# against a raw, unbounded command string -- see `_matches_fork_bomb` below,
# which gates it behind a cheap linear pre-filter and a small fixed-size
# window per candidate. Independently confirmed the naive full-string scan
# is super-linear (0.04s/2000 chars -> 2.3s/16000 chars on an all-letter,
# zero-paren string with no fork-bomb shape at all) -- a real hang risk on
# ANY sufficiently long ordinary command (heredoc, base64 blob, inline
# SQL/JSON), not just adversarial input.
FORK_BOMB_RE = re.compile(
    r"(?P<name>:|[A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\{\s*(?P=name)\s*\|\s*(?P=name)\s*&\s*;?\s*\}\s*;\s*(?P=name)"
)
# Cheap, backtracking-safe literal shape ("()" then "{") that must be
# present before the expensive backreference-based FORK_BOMB_RE is ever
# invoked at all.
_FORK_BOMB_PREFILTER_RE = re.compile(r"\(\s*\)\s*\{")
# Fixed-size window radius around each pre-filter match; keeps each
# FORK_BOMB_RE application O(1) regardless of overall command length, so
# total cost stays O(n) even under a densely-packed adversarial string.
#
# ROUND-3 CRITICAL-1 fix: was 260. Router-confirmed a syntactically-valid
# (bash -n clean) fork bomb with a ~400-byte whitespace-padded body between
# `{` and its closing `:|:&};:` tail pushed the tail past the old 260-char
# radius, so FORK_BOMB_RE never saw it within the window and the guard
# silently ALLOWED it. Widened to 8192 (8KB) -- still a small FIXED bound
# (not proportional to command length), so the O(1)-per-candidate /
# O(n)-total cost guarantee from the CRITICAL-B fix above is unchanged; a
# larger constant factor (8KB vs 260B per candidate, capped at
# _FORK_BOMB_MAX_CANDIDATES candidates) does not reintroduce the
# super-linear/quadratic ReDoS this window mechanism exists to prevent. A
# body padded past 8KB before its closing tail is a known, documented
# residual gap -- see HONEST SCOPE / KNOWN LIMITATIONS below -- not fixed
# by this round (widening further only pushes the same boundary out, it
# does not remove it; an unconditional full-string scan is what
# reintroduces the ReDoS this mechanism is built to avoid).
_FORK_BOMB_WINDOW_LEFT = 80
_FORK_BOMB_WINDOW_RIGHT = 8192
# Caps the number of candidates actually checked with the full pattern --
# a real fork bomb is a handful of characters and will be among the first
# candidates found; this bounds worst-case total cost even against a
# pathologically dense adversarial string (e.g. "(){"  repeated thousands
# of times), which no legitimate command constructs.
_FORK_BOMB_MAX_CANDIDATES = 200
MKFS_RE = re.compile(r"^mkfs(\.[A-Za-z0-9_]+)?$")


def _split_subcommands(command: str) -> list | None:
    """Best-effort split on shell control operators (;, &&, ||, |, &).

    Mirrors craftflow_pretooluse_bash_guard.py's own tokenizer (not shared
    via import -- each guard script in this plugin is self-contained).

    CRITICAL-2 fix: returns None (NOT []) when shlex cannot tokenize the
    command (e.g. an unbalanced quote raises ValueError). None is a distinct,
    fail-closed signal meaning "could not verify safety" -- the caller MUST
    treat that as "block", not silently reuse the empty-list "nothing to
    check" meaning that `[]` already carries for a genuinely empty command.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None

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


def _looks_dynamic_substitution(token: str) -> bool:
    """$/backtick command-substitution indirection only. Used by
    `_parse_rm_invocation` for rm TARGET tokens, where a "dynamic" token
    SKIPS the root-target check for the WHOLE invocation (fail-open, an
    existing and unrelated limitation -- see module docstring's honest-
    scope note) rather than failing closed the way argv0 does. Deliberately
    NOT extended with the brace-expansion check below: doing so at this
    call site would let an incidental `{a,b}`-shaped token anywhere in an
    rm invocation's argument list (e.g. `rm -rf / {a,b}`) suppress the
    check for an ADJACENT literal root target too -- a strictly worse,
    newly-introduced bypass, not a fix. See `_looks_dynamic` (argv0-only)
    for where brace-expansion detection actually belongs."""
    return "$" in token or "`" in token


def _looks_dynamic(token: str) -> bool:
    """Dynamic/indirect command-NAME (argv0 position) detection: $/backtick
    substitution plus brace-expansion syntax. Only ever called on the
    resolved command-name token in `_check_tokens`, where "dynamic" fails
    CLOSED (denies) -- never on rm/rmdir argument tokens; see
    `_looks_dynamic_substitution` above for that narrower, fail-open use."""
    if _looks_dynamic_substitution(token):
        return True
    # ROUND-3 CRITICAL-2 fix: brace expansion (`{r,}m`, `r{m,}`, `{,}rm`) is
    # a DISTINCT shell indirection mechanism from $/backtick substitution --
    # bash expands the argv0 token itself before exec (e.g. `{r,}m -rf /`
    # literally runs `rm m -rf /`), so a literal string compare against
    # "rm"/"mkfs..." downstream never fires and previously fell through
    # silently. `,` or `..` alongside a literal `{` is the brace-expansion
    # syntax marker; fail closed the same way as $/backtick indirection.
    if "{" in token and ("," in token or ".." in token):
        return True
    return False


def _skip_wrapper_prefixes(tokens: list) -> int:
    """Advance past a chain of leading, simple argv0-advancing wrapper
    prefixes -- env-var assignments, `sudo` (with its flags/values),
    the `command` builtin, `env` (with its own var-assignments/flags),
    `nice` (with its own value-taking flag), `nohup`/`watch` (their own
    bare dash-flags), `chroot`/`flock` (their own single positional
    newroot/lockpath argument), `timeout <duration>` (with its own
    value-taking flags), and `xargs` (bare dash-flags) -- in any
    combination and repetition (e.g. `nice nohup timeout 5 rm -rf /`) --
    so the real command name can be found regardless of which of these
    wrap it.

    CRITICAL-1 fix (round 1): `command` was previously not recognized at
    all, so `command rm -rf /` evaded every downstream check.
    CRITICAL-A fix (round 2): `sudo`'s long-option value-taking flags
    (`--user`, `--group`, etc.) were not recognized in space-separated
    form, so `sudo --user root rm -rf /` fell through with
    command_name=="root".
    CRITICAL-C fix (round 2): `env`/`nice`/`nohup`/`timeout`/`xargs`
    prefixes were not recognized at all, evading every downstream check.
    ROUND-3 CRITICAL-3 fix: `nice`/`timeout`'s OWN leading dash-flags
    (`nice -n 19`, `nice --adjustment=19`, `timeout --signal=KILL 5`,
    `timeout -k 5 30`) were never skipped, so the flag/its value token was
    misread as the wrapped command name -- `nice -n <N>` is the single
    most common real-world `nice` invocation, not an edge case. `nohup`'s
    own dash-flags (`nohup --`) had the same gap.
    ROUND-3 HIGH fix: `watch`/`chroot`/`flock` prefixes were not recognized
    at all, evading every downstream check the same way `env`/`nice`/
    `nohup`/`timeout`/`xargs` did before the round-2 CRITICAL-C fix.
    """
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if _is_env_assignment(token):
            idx += 1
            continue
        if token == "sudo":
            idx += 1
            while idx < len(tokens) and tokens[idx].startswith("-"):
                flag = tokens[idx]
                idx += 1
                if flag in SUDO_VALUE_FLAGS and idx < len(tokens):
                    idx += 1  # skip the flag's own value (space-separated)
            continue
        if token == "command":
            idx += 1
            # `command` only takes bare flags (-p/-v/-V, no values) before
            # the real command name.
            while idx < len(tokens) and tokens[idx].startswith("-") and tokens[idx] != "-":
                idx += 1
            continue
        if token == "env":
            idx += 1
            while idx < len(tokens):
                inner = tokens[idx]
                if _is_env_assignment(inner):
                    idx += 1
                    continue
                if inner.startswith("-") and inner != "-":
                    idx += 1
                    if inner in ENV_VALUE_FLAGS and idx < len(tokens):
                        idx += 1  # skip the flag's own value
                    continue
                break
            continue
        if token == "nice":
            idx += 1
            # ROUND-3 CRITICAL-3 fix: previously bare pass-through only --
            # `nice`'s own flags (space-separated `-n 19` or `=`-joined
            # `--adjustment=19`) were never skipped, so the flag/its value
            # token was misread as the wrapped command name.
            while idx < len(tokens) and tokens[idx].startswith("-") and tokens[idx] != "-":
                flag = tokens[idx]
                idx += 1
                if flag in NICE_VALUE_FLAGS and idx < len(tokens):
                    idx += 1  # skip the flag's own value (space-separated)
            continue
        if token in BARE_FLAG_SKIP_PREFIX_COMMANDS:
            idx += 1
            # ROUND-3 CRITICAL-3/HIGH fix: `nohup`/`watch` never take a
            # separate-token value flag in common usage, but their own
            # leading dash-flags (e.g. `nohup --`, `watch -n1`) were
            # previously never skipped either, so the flag token itself was
            # misread as the wrapped command name.
            while idx < len(tokens) and tokens[idx].startswith("-") and tokens[idx] != "-":
                idx += 1
            continue
        if token in POSITIONAL_ARG_SKIP_PREFIX_COMMANDS:
            idx += 1
            if idx < len(tokens):
                idx += 1  # skip the single newroot/lockpath positional argument
            continue
        if token == "timeout":
            idx += 1
            while idx < len(tokens) and tokens[idx].startswith("-") and tokens[idx] != "-":
                flag = tokens[idx]
                idx += 1
                if flag in TIMEOUT_VALUE_FLAGS and idx < len(tokens):
                    idx += 1  # skip the flag's own value (space-separated)
            if idx < len(tokens):
                idx += 1  # skip the duration argument
            continue
        if token == "xargs":
            idx += 1
            while idx < len(tokens) and tokens[idx].startswith("-") and tokens[idx] != "-":
                idx += 1
            continue
        break
    return idx


def _is_root_target(token: str) -> bool:
    """True for `/`, `//`, `/*`, `//*`, etc. -- deliberately narrow to the
    filesystem root itself (not `~`/`$HOME`/arbitrary absolute paths, which
    are directory-specific and out of scope for this specific check)."""
    stripped = token.rstrip("*").rstrip("/")
    return token.startswith("/") and stripped == ""


def _parse_rm_invocation(tokens: list) -> tuple:
    """Return (has_recursive, has_force, targets, has_dynamic_target) for an
    rm/rmdir invocation's argument tokens (tokens AFTER the command name).
    Uses `_looks_dynamic_substitution` ($/backtick only, NOT the argv0-only
    brace-expansion check) -- see that helper's docstring for why."""
    has_recursive = False
    has_force = False
    targets: list = []
    has_dynamic = False
    past_terminator = False
    for token in tokens:
        if token == "--" and not past_terminator:
            past_terminator = True
            continue
        if not past_terminator and token.startswith("-") and token != "-":
            body = token[1:]
            if token in ("-r", "-R", "--recursive"):
                has_recursive = True
            elif not token.startswith("--") and "r" in body:
                has_recursive = True
            if token in ("-f", "--force"):
                has_force = True
            elif not token.startswith("--") and "f" in body:
                has_force = True
            continue
        if _looks_dynamic_substitution(token):
            has_dynamic = True
            continue
        targets.append(token)
    return has_recursive, has_force, targets, has_dynamic


def _is_recursive_grep_on_broad_path(tokens: list) -> bool:
    """tokens are the grep/egrep/fgrep invocation's arguments (after the
    command name). Only dash-prefixed tokens are ever checked for the
    recursive flag -- the search pattern (a non-dash positional token) is
    never inspected, which is what keeps this free of false positives on a
    pattern that merely contains the word "recursive"."""
    has_recursive = False
    targets: list = []
    past_terminator = False
    for token in tokens:
        if token == "--" and not past_terminator:
            past_terminator = True
            continue
        if not past_terminator and token.startswith("-") and token != "-":
            if token in ("-r", "-R", "--recursive"):
                has_recursive = True
            elif not token.startswith("--") and "r" in token[1:]:
                has_recursive = True
            continue
        targets.append(token)
    if not has_recursive:
        return False
    return any(_is_root_target(t) or t in BROAD_GREP_TARGETS for t in targets)


def _matches_fork_bomb(command: str) -> bool:
    """True if `command` contains the classic fork-bomb shape.

    CRITICAL-B fix: FORK_BOMB_RE alone is super-linear/quadratic when
    applied to the whole raw command (independently confirmed: an
    all-letter, zero-paren 16000-char string with NO fork-bomb shape at all
    took 2.3s; the reported adversarial 80000-char case took ~60s). This
    would hang the hook for seconds to minutes on ANY sufficiently long
    ordinary command (heredoc, base64 blob, inline SQL/JSON), not just a
    deliberate attack.

    Fix: a cheap, backtracking-safe literal pre-filter (`\\(\\s*\\)\\s*\\{`,
    no ambiguous quantifiers) finds candidate positions in O(n) first; the
    expensive backreference-based FORK_BOMB_RE only ever runs against a
    small FIXED-SIZE window around each candidate (O(1) per candidate), and
    the number of candidates actually checked is capped
    (_FORK_BOMB_MAX_CANDIDATES) so total cost stays bounded even against a
    pathologically dense adversarial string that packs the literal shape
    thousands of times in a row -- something no legitimate command does.
    """
    if not _FORK_BOMB_PREFILTER_RE.search(command):
        return False
    checked = 0
    for match in _FORK_BOMB_PREFILTER_RE.finditer(command):
        checked += 1
        if checked > _FORK_BOMB_MAX_CANDIDATES:
            break
        start = max(0, match.start() - _FORK_BOMB_WINDOW_LEFT)
        end = min(len(command), match.end() + _FORK_BOMB_WINDOW_RIGHT)
        if FORK_BOMB_RE.search(command[start:end]):
            return True
    return False


def _extract_embedded_calls(code: str) -> list:
    """Best-effort regex extraction of shell-command string arguments
    passed to a FIXED list of common OS-command-execution call shapes
    within interpreter code (python `os.system`/`os.popen`/`subprocess.*`,
    perl/ruby bare `system(...)`, node `child_process.exec[Sync]`/
    `execSync`). NOT a real language parser -- see module docstring's
    honest-scope note (string concatenation, backticks, `%x{}`, encoded
    payloads, etc. are not resolved).
    """
    return [m.group(2) for m in DANGEROUS_CALL_RE.finditer(code)]


def _wrapped_command_string(command_name: str, rest: list) -> str | None:
    """If this invocation is `eval <string>`, an interpreter `-c` wrapper
    (`bash -c "..."` / `sh -c "..."` / `zsh -c "..."` / `dash -c "..."`), a
    scripting interpreter's inline-code flag (`python`/`python3 -c`,
    `perl`/`ruby -e`, `node -e`/`--eval`), `su -c "..."`, or `ssh <host>
    <command...>`, return the embedded code/command string so it can be
    re-tokenized and checked with the same catastrophic/recgrep rules.
    Returns None for anything else.

    CRITICAL-1 fix: these interpreter-wrapper forms previously evaded every
    check because the catastrophic command only ever existed as an opaque
    string argument, never as its own subcommand's argv.
    CRITICAL-C fix (round 2): scripting-interpreter `-c`/`-e` forms
    (`python3 -c "..."`, `perl -e '...'`, etc.) were not recognized at all.
    ROUND-3 HIGH fix: `su -c "..."`/`ssh <host> <command...>` were not
    recognized at all -- both carry the real catastrophic command as an
    opaque embedded string/remote-command argv, the same shape as
    `bash -c`/`sh -c` above.
    """
    if command_name == "eval":
        if not rest:
            return None
        return " ".join(rest)
    if command_name in INTERPRETER_C_SHELLS:
        if "-c" not in rest:
            return None
        c_idx = rest.index("-c")
        remainder = rest[c_idx + 1 :]
        if not remainder:
            return None
        # POSIX: the first arg after -c is the script string; anything after
        # that becomes $0/$1/... to the invoked shell, not part of the
        # command itself.
        return remainder[0]
    if command_name in INTERPRETER_INLINE_FLAGS:
        for flag in INTERPRETER_INLINE_FLAGS[command_name]:
            for i, token in enumerate(rest):
                if token == flag:
                    return rest[i + 1] if i + 1 < len(rest) else None
                if token.startswith(flag + "="):
                    return token[len(flag) + 1 :]
        return None
    if command_name == "su":
        # `su -c "<command>"` runs <command> as a login shell for the
        # target user. Narrow, common-shape only (mirrors bash/sh -c above)
        # -- no support for su's other flags; see module docstring's
        # honest-scope note. An `su` invocation with no `-c` (e.g.
        # interactive `su - someuser`) has no embedded command string to
        # extract, so it correctly falls through to None below.
        if "-c" not in rest:
            return None
        c_idx = rest.index("-c")
        remainder = rest[c_idx + 1 :]
        return remainder[0] if remainder else None
    if command_name == "ssh":
        # `ssh <host> <command...>` runs <command...> on the remote host.
        # Narrow, common-shape only: the first token after `ssh` is treated
        # as the host/destination (no support for ssh's own flags, e.g.
        # `-p <port>`, `-i <keyfile>` -- see module docstring's
        # honest-scope note); everything after that is the remote command.
        # `ssh host` alone (no remote command, e.g. an interactive session)
        # has nothing to extract and correctly falls through to None.
        if len(rest) < 2:
            return None
        remainder = rest[1:]
        return " ".join(remainder)
    return None


def _extract_find_exec_tokens(rest: list) -> list | None:
    """Best-effort extraction of a `find ... -exec <command...> \\;`/`+`
    invocation's embedded command tokens (already-tokenized, no re-split
    needed). Returns None if there's no `-exec` or nothing embedded. See
    module docstring's honest-scope note -- this is a narrow, common-shape
    extraction, not a general `find` expression parser.
    """
    if "-exec" not in rest:
        return None
    idx = rest.index("-exec") + 1
    embedded: list = []
    while idx < len(rest) and rest[idx] not in (";", "+"):
        embedded.append(rest[idx])
        idx += 1
    return embedded or None


def _check_find_exec_rm(command_name: str, rest: list, catastrophic: list) -> None:
    """Narrow, best-effort detection of the common, catastrophic `find
    <broad-path> -exec rm -rf {} \\;`/`+` pattern. The generic rm/rmdir
    root-target check elsewhere in this file can't catch this shape because
    the rm target is find's `{}` placeholder, not a literal root path --
    the actual breadth comes from find's OWN search path argument.
    """
    if command_name != "find":
        return
    embedded = _extract_find_exec_tokens(rest)
    if not embedded:
        return
    embedded_name = os.path.basename(embedded[0])
    if embedded_name not in ("rm", "rmdir"):
        return
    has_recursive, has_force, targets, has_dynamic = _parse_rm_invocation(embedded[1:])
    if not (has_recursive and has_force) or has_dynamic:
        return
    if "{}" not in targets:
        return
    exec_idx = rest.index("-exec")
    find_paths = [t for t in rest[:exec_idx] if not t.startswith("-")]
    if any(_is_root_target(t) or t in BROAD_GREP_TARGETS for t in find_paths):
        catastrophic.append(
            "find over a broad/root-ish path with -exec rm -rf {} (destroys everything found): "
            f"find {' '.join(find_paths)} -exec {' '.join(embedded)}"
        )


def _check_tokens(
    tokens: list,
    recursive_grep_mode: str,
    catastrophic: list,
    recgrep: list,
    depth: int = 0,
) -> None:
    """Check one already-tokenized subcommand's argv against the rm/mkfs/grep
    rules (mutating catastrophic/recgrep in place), then -- if the command is
    an `eval`/interpreter `-c` wrapper, a scripting interpreter `-c`/`-e`
    form, or a `find -exec` -- recursively re-tokenize and check the
    embedded command string(s)/tokens with the same rules (CRITICAL-1,
    CRITICAL-C fixes). The `command` builtin and other simple prefix
    wrappers (`sudo`/`env`/`nice`/`nohup`/`timeout`/`xargs`) are handled
    upstream in _skip_wrapper_prefixes, so they never reach this function as
    a distinct command_name.
    """
    idx = _skip_wrapper_prefixes(tokens)
    if idx >= len(tokens):
        return
    if _looks_dynamic(tokens[idx]):
        # CRITICAL-D fix: a dynamic/indirect command-name token (`$X`,
        # `$(echo rm)` as argv0) will NEVER literally equal "rm"/"mkfs...",
        # so falling through here would silently evade every check below.
        # Fail closed -- same "cannot verify safety" handling as an
        # unparseable command -- rather than allow.
        catastrophic.append(
            f"dynamic/indirect command-name token cannot be verified safe: "
            f"{tokens[idx]!r} -- failing closed"
        )
        return
    command_name = os.path.basename(tokens[idx])
    rest = tokens[idx + 1 :]

    if command_name in ("rm", "rmdir"):
        has_recursive, has_force, targets, has_dynamic = _parse_rm_invocation(rest)
        if has_recursive and has_force and not has_dynamic:
            for target in targets:
                if _is_root_target(target):
                    catastrophic.append(f"rm -rf targeting filesystem root: {target}")

    if MKFS_RE.match(command_name):
        catastrophic.append(f"filesystem-format command detected: {command_name}")

    if recursive_grep_mode in ("warn", "block") and command_name in ("grep", "egrep", "fgrep"):
        if _is_recursive_grep_on_broad_path(rest):
            recgrep.append(f"recursive grep against a broad/root-ish path: {command_name} {' '.join(rest)}")

    _check_find_exec_rm(command_name, rest, catastrophic)

    if depth < MAX_UNWRAP_DEPTH:
        find_exec_tokens = _extract_find_exec_tokens(rest) if command_name == "find" else None
        if find_exec_tokens:
            _check_tokens(find_exec_tokens, recursive_grep_mode, catastrophic, recgrep, depth + 1)
    elif command_name == "find" and _extract_find_exec_tokens(rest):
        catastrophic.append(
            "'find -exec'-wrapped command nesting exceeded the safety-check depth limit "
            "-- failing closed"
        )

    embedded = _wrapped_command_string(command_name, rest)
    if embedded is None:
        return
    if depth >= MAX_UNWRAP_DEPTH:
        # CRITICAL-2-style fail-closed: pathological nesting that exhausts
        # the recursion bound before resolving to a plain command cannot be
        # verified safe, so it is treated as catastrophic rather than
        # silently allowed.
        catastrophic.append(
            f"'{command_name}'-wrapped command nesting exceeded the safety-check depth limit "
            "-- failing closed"
        )
        return

    code_strings = [embedded]
    if command_name in SCRIPTING_INTERPRETERS:
        # CRITICAL-C: best-effort extraction of shell-out calls from
        # interpreter code (e.g. python's os.system(...), perl's
        # system(...)) in ADDITION to the raw code string itself.
        code_strings.extend(_extract_embedded_calls(embedded))

    for code_str in code_strings:
        embedded_subcommands = _split_subcommands(code_str)
        if embedded_subcommands is None:
            catastrophic.append(
                f"could not safely parse the command string wrapped by '{command_name}' "
                "(malformed/unterminated quoting) -- failing closed"
            )
            continue
        for embedded_tokens in embedded_subcommands:
            _check_tokens(embedded_tokens, recursive_grep_mode, catastrophic, recgrep, depth + 1)


def main() -> int:
    data = load_input()
    if data.get("tool_name") != "Bash":
        return 0

    command = (data.get("tool_input") or {}).get("command")
    if not command or not isinstance(command, str):
        return 0

    mode = load_mode()
    block_catastrophic = mode.get("safeShellGuard", "block") == "block"
    recursive_grep_mode = mode.get("recursiveGrepGuard", "off")

    catastrophic: list = []
    recgrep: list = []

    if _matches_fork_bomb(command):
        catastrophic.append("shell fork-bomb pattern detected")

    subcommands = _split_subcommands(command)
    if subcommands is None:
        # CRITICAL-2 fix: a command shlex cannot tokenize (e.g. an unbalanced
        # quote) must fail closed -- treated as catastrophic -- instead of
        # silently skipping the for-loop below as if there were "nothing to
        # check". See _split_subcommands docstring for the full rationale.
        catastrophic.append(
            "could not safely parse this command for shell-guard analysis "
            "(malformed/unterminated quoting) -- failing closed"
        )
    else:
        for tokens in subcommands:
            _check_tokens(tokens, recursive_grep_mode, catastrophic, recgrep)

    if not catastrophic and not recgrep:
        return 0

    should_deny = bool(catastrophic) and block_catastrophic
    should_deny = should_deny or (bool(recgrep) and recursive_grep_mode == "block")

    reasons = catastrophic + recgrep
    log_event(
        "plugin_safe_shell_guard",
        {
            "event": "pretool_guard",
            "tool_name": "Bash",
            "command": command,
            "decision": "deny" if should_deny else "audit",
            "reason": "; ".join(reasons),
        },
    )

    if should_deny:
        pretool_deny(
            "CRAFTFLOW safe-shell guard blocked a catastrophic shell command pattern "
            f"({'; '.join(reasons)}). If this is intentional, run it manually outside the "
            "agent session."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
