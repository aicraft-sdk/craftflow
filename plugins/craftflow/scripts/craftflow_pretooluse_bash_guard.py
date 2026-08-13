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

import fnmatch
import itertools
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
    resolve_toggle_decision,
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

# A path COMPONENT made up entirely of `*` characters -- matches both a
# plain single-star glob (`*`) and a globstar (`**`, `***`, ...; bash's
# `shopt -s globstar` recurses through arbitrarily many directory levels).
#
# Deliberately excludes a bare-wildcard-only component from
# `_component_matches_critical_child()` below (rather than folding it into
# that fnmatch-based check too): fnmatch.fnmatch(child, "*") is trivially
# True for every CRITICAL_TOP_LEVEL_CHILDREN name, so treating a
# content-free `*`/`**` as "matches a critical child" at an arbitrary
# top-level/descendant position would make `rm -rf ./*/build-tmp` (a
# documented must-stay-allowed shape) resolve to "top-level component `*`
# matches `packages`" and get denied -- a pure "matches everything" pattern
# carries no positional specificity, so it is handled ONLY by the two
# narrower, deliberately-separate rules that already cover it: the
# trailing-bare-wildcard check (this component as `resolved`'s OWN final
# name -- see `_is_in_cwd_critical()`) and the wildcard-immediately-
# precedes-a-critical-name-or-glob adjacency check (see
# `_has_wildcard_adjacent_critical_child()`). Every OTHER component --
# whether fully literal (`packages`) or a genuine partial glob
# (`pack*ages`) -- is unambiguous and routed through
# `_component_matches_critical_child()` (REM-FIX round 2, Critical Issue 2:
# a partial-wildcard component like `pack*ages`, a real valid bash glob
# expanding to `packages`, was previously neither a bare wildcard nor an
# exact string match, so it evaded detection entirely).
_BARE_WILDCARD_COMPONENT_RE = re.compile(r"^\*+$")

# Any character that gives a path COMPONENT glob semantics under bash
# filename expansion -- `*` (any run), `?` (single char), and `[` (the
# opening bracket of a `[seq]`/`[!seq]`/POSIX-class bracket expression).
# This is deliberately the SAME vocabulary `fnmatch` itself recognizes (the
# module `_component_matches_critical_child()` already delegates to below)
# -- not a hand-picked subset of it.
#
# REM-FIX round 3 (3rd consecutive code-review-found bypass in this same
# adjacency check): `_has_wildcard_adjacent_critical_child()` previously
# gated its "is the PRECEDING component a wildcard filler" test on
# `_BARE_WILDCARD_COMPONENT_RE` -- a stars-only regex. That's narrower than
# the glob vocabulary the sibling `_component_matches_critical_child()`
# helper already recognizes for the FOLLOWING (critical-name) component via
# `fnmatch`. A filler expressed as `?`, `[a]`, `[!x]`, or a partial-star
# (`*a`, `a*`) was therefore invisible as a FILLER even though the exact
# same shapes are correctly recognized when they form the critical name's
# OWN component instead (`./?/.git`, `./[a]/.git`, `./[!x]/packages`,
# `./*a/tools`, `./a*/packages` were all live-confirmed bypassed before this
# fix; `./*/.git` -- the original bare-star filler -- was already correctly
# denied). Generalized here to ANY component containing at least one glob
# metacharacter, mirroring the critical-name side rather than patching one
# more named shape -- see `_is_wildcard_like_filler_component()` below.
_WILDCARD_LIKE_CHAR_RE = re.compile(r"[*?\[]")

# REM-FIX (CRITICAL, live-confirmed bypass): a bash brace-expansion group
# (`{alt1,alt2,...}`) -- UNCONDITIONALLY expanded by bash, no `shopt`
# needed, unlike extglob below. `split_subcommands()`'s shlex tokenizer
# never splits on `{`/`}`/`,`, so a `{a,b}`-shaped component previously
# reached every matching function (`_component_matches_critical_child`,
# `_is_wildcard_like_filler_component`) as ONE opaque literal string --
# `fnmatch`'s glob vocabulary is exactly `*`/`?`/`[`, which does not
# understand brace groups at all, so `rm -rf ./{a,.git}` was live-confirmed
# ALLOWED. A brace pair with NO comma inside (`{single}`) is NOT a real
# bash brace-expansion group (bash leaves it as a literal string) --
# deliberately excluded so a literal `{single}`-named directory isn't
# mistaken for one.
#
# REM-FIX round 8 (CRITICAL, live-confirmed bypass, doubt-verify): the
# original single-pass `\{([^{}]+)\}` regex this comment used to describe
# required NON-EMPTY content between a `{` and the very NEXT `}` -- when a
# bare `}` appears immediately after an unquoted `{` (via ANSI-C decode,
# `$'\x7d'`/`$'\175'`, or plain literal text: `./{}a,.git}`), the regex
# cannot match at that `{` at all (its character class excludes `}`
# entirely, so it can never skip past one to look for a LATER close for
# the SAME `{`), and `_component_has_brace_group()` silently returned
# False -- falling through to the literal whole-string fnmatch that can
# never match a critical name. Real bash instead BACKTRACKS: when the
# content between `{` and a candidate `}` has no top-level comma (empty or
# otherwise), that `}` is NOT treated as the real close -- bash keeps
# scanning for a LATER `}` that DOES yield a comma-bearing span. Ground
# truth: `bash -c 'echo ./{}a,.git}'` -> `./}a ./.git` (the empty `{}`
# candidate is skipped; the group closes at the FINAL `}`, giving content
# `}a,.git`, which does have a comma).
#
# Fixed by replacing the single regex with `_iter_brace_groups()` below, a
# depth-aware scan (same character-counting technique as
# `_scan_brace_depth()`/`_scan_paren_depth()` elsewhere in this module)
# that, for each `{`, keeps extending past an invalid (comma-less)
# candidate close instead of giving up, and reports a component as
# unresolved/malformed -- rather than silently "no group here" -- when a
# `{` never finds ANY balancing `}` at all (fail CLOSED per this module's
# existing parse-failure convention, not a silent fnmatch fallthrough).
_MAX_BRACE_SCAN_ITERATIONS = 4096

# REM-FIX (CRITICAL, algorithmic-complexity DoS, live-timed): `_MAX_BRACE_SCAN_ITERATIONS`
# above only bounds a SINGLE `_scan_one_brace_group()` attempt. It does
# nothing to bound how many such attempts `_iter_brace_groups()`'s outer
# loop makes across ONE call -- that loop advances by only +1 character
# after a failed/malformed/no-comma attempt, so a component with many
# nested or adjacent comma-less brace pairs triggers up to O(n) independent
# attempts, each itself re-scanning `_content_has_top_level_comma()` over a
# GROWING content span every time it rejects a candidate close. For `n`
# perfectly-nested, comma-less empty brace pairs (`"{" * n + "}" * n`) this
# compounds into cubic-class blowup -- live-timed against THIS module's own
# `_iter_brace_groups()`: n=100 pairs (200 chars) ~0.03s, n=300 (600 chars)
# ~0.7s, n=600 (1200 chars) ~5.9s. The registered PreToolUse hook timeout
# for this script (hooks/hooks.json) is 10s, and this module's own
# documented invariant is that an uncaught crash / non-zero, non-2 exit
# does NOT block the tool call -- a timeout-killed hook process is the
# SAME "no blocking decision produced" failure mode, so a ~2KB adversarial
# command argument can silently fail this guard open.
#
# Fixed by threading a single GLOBAL budget counter through the ENTIRE
# `_iter_brace_groups()` call -- shared across every `_scan_one_brace_group()`
# attempt it makes, not reset per attempt -- decremented for both the
# character-by-character scan AND the cost of every
# `_content_has_top_level_comma()` re-scan (the actual hidden quadratic
# cost). Once the global budget is exhausted, the scan fails CLOSED
# (`malformed=True`), the same existing convention already used elsewhere
# in this file for an unresolvable/ambiguous brace structure -- never
# silently truncated as "no group found here". Generous enough for any
# realistic legitimate command (a real command component is nowhere near
# this size) but far below what would risk the registered 10s hook
# timeout.
_MAX_BRACE_SCAN_TOTAL_BUDGET = 20000

# REM-FIX (CRITICAL, algorithmic-complexity DoS variant -- unbounded CALL
# COUNT, not just unbounded cost within one call): the fix above bounds the
# total work `_iter_brace_groups()` can do WITHIN a single call (one path
# COMPONENT's own brace-scan attempt), but does nothing to bound how MANY
# such calls happen across one guard invocation. `_has_wildcard_adjacent_
# critical_child()` below calls `_component_has_brace_group()` -- which,
# pre-fix, created a FRESH, fully-reset `_MAX_BRACE_SCAN_TOTAL_BUDGET`-sized
# budget every time -- once per path COMPONENT in `resolved.parts`, with NO
# cap on how many components a single destructive-command argument may
# contain. A target made of many small, individually-cheap-but-brace-
# bearing `/`-joined segments (each well under the per-call budget on its
# own, ~2ms/segment observed vs ~0.003ms/segment for a plain segment) pays
# that per-segment cost N times over with nothing bounding N -- cost is
# LINEAR in segment count. Router-confirmed timings (command =
# `rm -rf ./` + N segments of `"{"*30+"}"*30` joined by `/`): n=500
# segments (30KB command) 0.75s, n=1500 (91KB) 2.88s, n=3000 (183KB) 6.14s,
# n=6000 (~366KB) 13.4-15.0s -- exceeding the registered 10s PreToolUse
# hook timeout in hooks/hooks.json. A timeout-killed hook process is this
# module's own documented "no blocking decision produced" case (see
# `_destructive_targets()`'s docstring on HIGH 7): the destructive command
# proceeds UNBLOCKED -- the SAME fail-open risk class as the single-call
# DoS fixed directly above.
#
# Fixed by converting `_MAX_BRACE_SCAN_TOTAL_BUDGET` from a fresh
# PER-CALL allowance into a single, PROCESS-WIDE budget shared across
# EVERY `_iter_brace_groups()` call made during the CURRENT hook
# invocation -- a true global counter spanning ALL path components
# examined across the whole command, not reset per component and not
# reset per call. Once this shared budget is exhausted, every subsequent
# `_iter_brace_groups()` call (for any component, from any call site) fails
# CLOSED immediately (`malformed=True`, O(1) cost, no scanning attempted)
# instead of ever running a fresh scan -- bounding the TOTAL brace-scan
# work for the entire guard invocation to one fixed ceiling no matter how
# many brace-bearing path components the target contains. Reset exactly
# once per hook invocation, at the very top of `main()` -- see
# `_reset_global_brace_scan_budget()` below; a direct caller that never
# goes through `main()` (e.g. a white-box unit test) gets a lazily
# initialized fresh budget on first use instead of crashing, via
# `_global_brace_scan_budget_list()`.
_global_brace_scan_budget: "list | None" = None


def _reset_global_brace_scan_budget() -> None:
    """Reset the process-wide brace-scan budget shared by every
    `_iter_brace_groups()` call for the remainder of this hook invocation.
    MUST be called exactly once, at the very top of `main()`, before any
    subcommand or path-component parsing begins -- see the module-level
    docstring on `_MAX_BRACE_SCAN_TOTAL_BUDGET` above for the call-COUNT DoS
    this closes."""
    global _global_brace_scan_budget
    _global_brace_scan_budget = [_MAX_BRACE_SCAN_TOTAL_BUDGET]


def _global_brace_scan_budget_list() -> list:
    """Return the shared, process-wide budget list, lazily initializing it
    to a fresh full budget if `_reset_global_brace_scan_budget()` was never
    called first (e.g. a unit test invoking `_iter_brace_groups()` or a
    sibling helper directly, rather than through `main()`) -- never raises
    just because the reset step was skipped."""
    global _global_brace_scan_budget
    if _global_brace_scan_budget is None:
        _global_brace_scan_budget = [_MAX_BRACE_SCAN_TOTAL_BUDGET]
    return _global_brace_scan_budget


def _content_has_top_level_comma(content: str) -> bool:
    """True if `content` contains a `,` at brace-nesting depth 0 -- nested
    `{`/`}` pairs within `content` are depth-tracked (and CLAMPED at a
    minimum of 0, since `content` may itself carry a stray, unmatched `}`
    left over from a skipped invalid candidate close -- e.g. content
    `"}a,.git"` from the empty-pair backtrack above -- which must be
    treated as ordinary literal text, not as closing a nesting level that
    was never opened)."""
    depth = 0
    for ch in content:
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
        elif ch == "," and depth == 0:
            return True
    return False


def _split_top_level_commas(content: str) -> list:
    """Split `content` on every `,` at brace-nesting depth 0 only -- a
    comma INSIDE a nested `{...}` group is left alone for that nested
    group's own later expansion round. Same depth-clamping as
    `_content_has_top_level_comma()` above, for the same reason."""
    parts = []
    depth = 0
    last = 0
    for idx, ch in enumerate(content):
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
        elif ch == "," and depth == 0:
            parts.append(content[last:idx])
            last = idx + 1
    parts.append(content[last:])
    return parts


def _scan_one_brace_group(text: str, open_idx: int, budget: list) -> tuple:
    """Find the valid (comma-bearing) closing `}` for the `{` at
    `text[open_idx]`, backtracking past any invalid (empty or comma-less)
    candidate close exactly like real bash does. Returns
    (close_idx, content, malformed):

    - A candidate close is found the instant nesting depth returns to 0.
      If `text[open_idx + 1 : candidate_idx]` has no TOP-LEVEL comma, that
      close is rejected -- depth is reset to 1 (treating the rejected `}`
      as ordinary content, not a real terminator) and the scan keeps
      going, looking for a LATER `}` instead.
    - `close_idx=None, malformed=False` when depth returned to 0 at least
      once (a well-formed, but comma-less, pair like `{single}`) with no
      more `}` characters left to extend to -- NOT a real bash
      brace-expansion group; deliberately left as ordinary literal text
      (pre-existing, documented behavior).
    - `close_idx=None, malformed=True` when depth NEVER returns to 0 at
      all across the rest of `text` -- a genuinely unbalanced/ambiguous
      `{` with no matching `}` anywhere -- callers MUST fail CLOSED on
      this (Behavior Contract rule 9), never silently treat it as
      "no group here". Also `malformed=True` when the GLOBAL `budget`
      (shared across the WHOLE `_iter_brace_groups()` call this attempt
      belongs to -- see that function's docstring) runs out mid-scan --
      fail CLOSED rather than silently truncating the scan as
      "no group here" (REM-FIX, algorithmic-complexity DoS).

    `budget`: a single-element list `[remaining]`, decremented for every
    character examined by THIS attempt's own while loop AND for the full
    cost of every `_content_has_top_level_comma()` re-scan this attempt
    performs (the actual hidden quadratic cost -- each rejected candidate
    close re-scans a content span that only grows as the attempt
    continues). Mutated in place so the running total persists across
    every attempt `_iter_brace_groups()` makes, not just this one."""
    depth = 1
    ever_balanced = False
    idx = open_idx + 1
    n = len(text)
    iterations = 0
    while idx < n:
        iterations += 1
        budget[0] -= 1
        if budget[0] <= 0:
            return None, None, True
        if iterations > _MAX_BRACE_SCAN_ITERATIONS:
            return None, None, True
        ch = text[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                ever_balanced = True
                content = text[open_idx + 1 : idx]
                budget[0] -= len(content)
                if budget[0] <= 0:
                    return None, None, True
                if _content_has_top_level_comma(content):
                    return idx, content, False
                depth = 1
        idx += 1
    if ever_balanced:
        return None, None, False
    return None, None, True


def _iter_brace_groups(text: str) -> tuple:
    """Scan `text` left to right for every valid, non-overlapping,
    OUTERMOST brace-expansion group (using `_scan_one_brace_group()`'s own
    backtrack-past-invalid-candidate matching). Returns
    (groups, malformed) where `groups` is a list of
    `(start_idx, end_idx, content)` tuples (`end_idx` one past the closing
    `}`, matching `re.Match.start()`/`.end()` semantics so existing
    substitution call sites need no offset changes) in left-to-right
    order, and `malformed=True` if ANY `{` in `text` never found a
    balancing `}` at all (fail-CLOSED signal; see `_scan_one_brace_group()`
    docstring).

    REM-FIX (CRITICAL, algorithmic-complexity DoS): a GLOBAL budget counter
    is threaded through EVERY `_scan_one_brace_group()` attempt this call
    makes -- not reset per attempt. `_MAX_BRACE_SCAN_ITERATIONS` alone only
    bounds a single attempt; it does nothing to bound the total work across
    the many independent attempts this outer loop can trigger for a
    component with many nested/adjacent comma-less brace pairs (see that
    constant's own module-level docstring for the live-timed blowup this
    closes). Once the shared budget is exhausted mid-scan, this loop stops
    immediately (fails CLOSED, `malformed=True`) rather than continuing to
    burn through the rest of `text` one attempt at a time.

    REM-FIX (CRITICAL, algorithmic-complexity DoS variant -- unbounded CALL
    COUNT): the budget obtained below is the shared, PROCESS-WIDE budget
    (`_global_brace_scan_budget_list()`) -- NOT a fresh list created here --
    so it persists across every call this function receives during the
    whole hook invocation, not just across the attempts within ONE call.
    This is what bounds the total cost of scanning MANY brace-bearing path
    components (e.g. many `/`-joined segments in one destructive-command
    target), where a fresh per-call budget would let each component pay its
    own full scan cost independently, multiplying linearly with component
    count (see `_MAX_BRACE_SCAN_TOTAL_BUDGET`'s own module-level docstring
    for the live-timed blowup this closes)."""
    groups = []
    malformed = False
    idx = 0
    n = len(text)
    budget = _global_brace_scan_budget_list()
    while idx < n:
        if budget[0] <= 0:
            malformed = True
            break
        if text[idx] != "{":
            idx += 1
            continue
        close_idx, content, is_malformed = _scan_one_brace_group(text, idx, budget)
        if is_malformed:
            malformed = True
            idx += 1
            continue
        if close_idx is None:
            idx += 1
            continue
        groups.append((idx, close_idx + 1, content))
        idx = close_idx + 1
    return groups, malformed


def _component_has_brace_group(component: str) -> bool:
    """True if `component` contains at least one valid, comma-bearing
    `{alt1,alt2,...}` brace-expansion group -- found via `_iter_brace_groups()`'s
    depth-aware backtracking scan, not a single-pass regex (REM-FIX round
    8). Also True when `component` has an unbalanced/ambiguous `{`/`}`
    structure `_iter_brace_groups()` cannot fully resolve (`malformed`) --
    fail CLOSED (treated as a brace-bearing, potentially-dangerous
    component requiring full critical-child checking) rather than silently
    treating an unresolvable shape as "no brace group here"."""
    groups, malformed = _iter_brace_groups(component)
    return bool(groups) or malformed


# REM-FIX (composition-boundary bypass, follow-up to the round-4 brace-
# expansion hardening above): bound how many ROUNDS of nested-group
# expansion `_expand_brace_groups()` below will attempt, and how many total
# concrete alternatives one call may produce, before it gives up and fails
# CLOSED (treats the component as a match) instead of silently returning a
# partially-expanded, still-brace-containing set of "alternatives" as if
# expansion had converged. A real bash brace-expansion nesting depth this
# deep, or a fan-out this wide, is already deep into adversarial/DoS
# territory -- mirrors this module's existing `_scan_paren_depth()`/
# `_consume_git_flag_value()` "malformed span -> fail closed, never fail
# open" convention rather than open-ended recursion or an unbounded
# itertools.product cross product.
_MAX_BRACE_EXPANSION_ROUNDS = 6
_MAX_BRACE_EXPANSION_ALTERNATIVES = 512


def _expand_brace_groups(component: str) -> tuple:
    """Expand every `{alt1,alt2,...}` group in `component` into every
    concrete combination bash's own brace expansion would produce,
    including NESTED groups (`{a,{b,.git}}`) -- REM-FIX (CRITICAL,
    live-confirmed bypass): a nested shape like `rm -rf ./{a,{.git,c}}` was
    previously left with its OUTER braces as literal, unexpanded text (only
    the innermost group was found and expanded), so bash's real 3-way
    expansion (`./a ./.git ./c`) was never fully enumerated and the
    `.git`/`c` alternatives escaped detection.

    Fixed by RECURSING the same single-round match instead of running it
    once: each round finds every OUTERMOST valid group in the CURRENT
    candidate set (via `_iter_brace_groups()`'s depth-aware backtracking
    scan, REM-FIX round 8 -- replacing the prior single-pass `_BRACE_GROUP_RE`
    regex, which only ever matched a group with no braces INSIDE it and
    could never skip past an invalid empty/comma-less candidate close to
    find a later, valid one), splits each found group's content on its own
    TOP-LEVEL commas only (`_split_top_level_commas()` -- a comma inside a
    still-nested inner group is left alone for that inner group's own
    later round), then re-scans every produced alternative for further
    (now-unnested) groups on the NEXT round. A nested shape's inner group
    surfaces as an ordinary, no-longer-nested group once its enclosing
    outer group's own top-level-comma split isolates it into its own
    candidate string (e.g. `{a,{.git,c}}` -> round 1 finds the OUTER group
    (content `a,{.git,c}`, one top-level comma) -> alternatives `a` and
    `{.git,c}` -> round 2 expands the latter fully to `.git`/`c`). Cross
    product across multiple SIBLING groups within the same round-candidate
    (e.g. `{a,b}-{c,d}` -> 4 combinations) is preserved exactly as before,
    via itertools.product; substitution uses each group's ORIGINAL
    start/end offset against that round's OWN candidate string (not a
    running mutated copy), so multiple groups at different positions never
    drift out of alignment even when a chosen alternative's length differs
    from its group's own original span.

    Bounded by `_MAX_BRACE_EXPANSION_ROUNDS` (nesting depth) and
    `_MAX_BRACE_EXPANSION_ALTERNATIVES` (combinatorial fan-out) -- see their
    own module-level docstring above. Returns `(candidates, bound_exceeded)`:
    `bound_exceeded=True` signals the caller to treat the component as
    matching regardless of the (necessarily incomplete) candidate list,
    rather than silently treating the still-unresolved braces as inert
    literal text past either bound. Also `bound_exceeded=True` immediately,
    without even attempting a round, when `_iter_brace_groups()` reports a
    component as malformed (an unbalanced/ambiguous `{`/`}` structure it
    cannot fully resolve) -- fail CLOSED (Behavior Contract rule 9) rather
    than silently expanding only what little it COULD parse.

    Returns `([component], False)` unchanged when no comma-bearing brace
    group is present in `component` at all."""
    current = [component]
    for _ in range(_MAX_BRACE_EXPANSION_ROUNDS):
        next_round = []
        any_group_found = False
        for item in current:
            groups, malformed = _iter_brace_groups(item)
            if malformed:
                return [item], True
            if not groups:
                next_round.append(item)
                continue
            any_group_found = True
            alt_lists = [_split_top_level_commas(content) for (_start, _end, content) in groups]
            for combo in itertools.product(*alt_lists):
                pieces = []
                last_end = 0
                for (start, end, _content), alt in zip(groups, combo):
                    pieces.append(item[last_end:start])
                    pieces.append(alt)
                    last_end = end
                pieces.append(item[last_end:])
                next_round.append("".join(pieces))
                if len(next_round) > _MAX_BRACE_EXPANSION_ALTERNATIVES:
                    return next_round, True
        current = next_round
        if not any_group_found:
            return current, False
    # Depth bound hit -- fail CLOSED if any candidate still carries an
    # unresolved comma-bearing group, rather than treating the bound as
    # silent convergence.
    if any(_component_has_brace_group(item) for item in current):
        return current, True
    return current, False


# REM-FIX (CRITICAL, live-confirmed bypass, doubt-verify): bash's ANSI-C
# quoting (`$'...'`) decodes a bounded set of backslash escapes -- `\xHH`
# (hex), `\NNN` (1-3 digit octal), and the common single-character C
# escapes (`\n`, `\t`, `\\`, `\'`, ...) -- into literal bytes BEFORE the
# shell ever tokenizes the word. `$'\x2e\x67\x69\x74'` decodes to the
# literal string `.git` (ground truth: `bash -c "echo \$'\x2e\x67\x69\x74'"`
# -> `.git`).
#
# `split_subcommands()`'s shlex tokenizer (posix=True) strips the `$'...'`
# quote MARKERS via its own POSIX single-quote handling, but POSIX single
# quotes never process backslash escapes -- the resulting token still
# carries the RAW, undecoded escape text with a leading "$" (e.g.
# `"$\\x2e\\x67\\x69\\x74"`). `looks_dynamic()` ("$" in token) fires on
# that raw text, short-circuiting `_positional_targets()` to "unresolvable"
# BEFORE the decoded literal is ever matched against
# CRITICAL_TOP_LEVEL_CHILDREN -- and `command_has_traversal_or_wildcard()`
# has zero ANSI-C-quote awareness either, so no corroborating traversal/
# wildcard signal fires, landing the whole command in the non-denying
# `unverifiable` bucket in main() (live-confirmed: `rm -rf
# $'\x2e\x67\x69\x74'` and the brace-wrapped `rm -rf
# ./{$'\x2e\x67\x69\x74',a}` were both ALLOWED).
#
# Fixed by decoding every `$'...'` span in the RAW command text -- applied
# once in main(), before split_subcommands()'s shlex ever consumes the
# distinguishing quote markers -- substituting each span with its OWN
# single-quoted decoded literal (re-escaping any embedded single quote via
# the standard POSIX `'"'"'` trick), so downstream shlex tokenization still
# produces exactly one token for it, now carrying the real decoded text
# instead of the raw escape sequence. A decoded `.git` then flows through
# the EXACT SAME literal-matching path as a plain, unquoted `.git` argument
# -- including, for the brace-wrapped form, the ALREADY-EXISTING
# `_component_matches_critical_child()` brace-expansion handling -- no new
# matching logic needed once the text is decoded early enough. This also
# correctly handles bash's own word-concatenation of a quoted fragment
# adjacent to a literal or another quoted fragment with no whitespace
# between them (e.g. `.` + `$'\x67\x69\x74'`, or two adjacent `$'...'`
# spans) purely as a side effect of only replacing the `$'...'` SPAN itself
# and leaving surrounding text untouched -- shlex's own concatenation
# behavior does the rest, exactly as real bash would.
#
# Bounded, static, fully-resolvable-from-text -- no real shell/subprocess
# invoked, mirroring `_expand_brace_groups()`'s own text-level expansion
# approach. A malformed/never-closing `$'...'` span (no matching closing
# quote) simply doesn't match `_ANSI_C_QUOTE_RE` and is left as literal,
# undecoded text -- it still contains a bare "$" and therefore still trips
# the existing `looks_dynamic()` fail-toward-unresolvable path unchanged.
_ANSI_C_QUOTE_RE = re.compile(r"\$'((?:[^'\\]|\\.)*)'")

# One $'...'-content escape sequence at a time: `\xHH` (1-2 hex digits),
# `\NNN` (1-3 octal digits), or `\<any other char>` (a single-character
# escape, or an unrecognized escape that bash itself would emit as the
# literal character with the backslash dropped).
_ANSI_C_ESCAPE_RE = re.compile(r"\\(x[0-9a-fA-F]{1,2}|[0-7]{1,3}|.)", re.DOTALL)

_ANSI_C_SINGLE_CHAR_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
}


def _decode_ansi_c_escapes(inner: str) -> str:
    """Decode the escape sequences inside one `$'...'` span's own content
    (the text already extracted BETWEEN the quote markers -- the markers
    themselves are not part of `inner`). Recognizes `\\xHH` (1-2 hex
    digits), `\\NNN` (1-3 octal digits), and the common single-character C
    escapes bash's own $'...' documents; any other backslash-escaped
    character passes through as the literal character itself, matching
    bash's own "unrecognized escape -> literal char, backslash dropped"
    behavior."""

    def _replace(match: "re.Match") -> str:
        esc = match.group(1)
        if esc[0] in "xX" and len(esc) > 1:
            return chr(int(esc[1:], 16))
        if esc[0] in "01234567":
            return chr(int(esc, 8) & 0xFF)
        return _ANSI_C_SINGLE_CHAR_ESCAPES.get(esc, esc)

    return _ANSI_C_ESCAPE_RE.sub(_replace, inner)


def _expand_ansi_c_quotes(command: str) -> str:
    """Replace every `$'...'` span in `command` with its own decoded
    literal, re-quoted (plain single quotes, with any embedded single quote
    re-escaped via the standard POSIX `'"'"'` trick) so downstream shlex
    tokenization still produces exactly one token for it. A no-op (returns
    `command` unchanged) when no `$'...'` span is present at all."""

    def _replace(match: "re.Match") -> str:
        decoded = _decode_ansi_c_escapes(match.group(1))
        escaped = decoded.replace("'", "'\"'\"'")
        return f"'{escaped}'"

    return _ANSI_C_QUOTE_RE.sub(_replace, command)


def _is_wildcard_like_filler_component(component: str) -> bool:
    """True if `component` contains at least one bash glob metacharacter
    (`*`, `?`, or the opening `[` of a bracket expression -- including a
    POSIX character class like `[[:alpha:]]`, which also starts with `[`),
    making it a candidate WILDCARD FILLER for
    `_has_wildcard_adjacent_critical_child()` below.

    Deliberately broader than `_BARE_WILDCARD_COMPONENT_RE` (which only
    matches a component made ENTIRELY of `*` characters): a filler doesn't
    need to be a bare/content-free wildcard to be dangerous here, because
    this function is only ever used to gate the PRECEDING component in an
    adjacency pair -- the FOLLOWING component still has to independently
    match a real `CRITICAL_TOP_LEVEL_CHILDREN` name via
    `_component_matches_critical_child()` before anything is denied. This
    is what keeps `rm -rf ./*/build-tmp`-style shapes correctly ALLOWED
    regardless of the filler's exact glob shape: `build-tmp` doesn't match
    any critical child, so the pair never fires no matter how the filler is
    spelled (`*`, `?`, `[a]`, ...).

    Also True for a component containing a comma-bearing brace-expansion
    group (`{a,b}`) -- REM-FIX (CRITICAL): `rm -rf ./{a,b}/packages` is the
    identical middle-segment bypass shape as `rm -rf ./*/packages`, just
    with a finite, explicit enumeration standing in for the open-ended `*`."""
    return bool(_WILDCARD_LIKE_CHAR_RE.search(component)) or _component_has_brace_group(component)


def _component_matches_critical_child(component: str) -> bool:
    """True if `component` -- a single literal path-component string that
    may itself contain shell glob syntax (`*`) -- would, under bash's own
    glob expansion (including a globstar under `shopt -s globstar`), match
    one of CRITICAL_TOP_LEVEL_CHILDREN. Generalizes the prior exact-string
    membership check (`component in CRITICAL_TOP_LEVEL_CHILDREN`) to ALSO
    recognize a partial-wildcard component like `pack*ages` -- a real,
    valid bash glob that expands to `packages` -- via `fnmatch.fnmatch()`,
    which reduces to plain exact-string equality automatically when
    `component` contains no glob metacharacters at all (REM-FIX round 2,
    Critical Issue 2).

    A content-free bare-wildcard-only component (`*`, `**`, ...) is
    deliberately excluded here and returns False unconditionally -- see the
    `_BARE_WILDCARD_COMPONENT_RE` docstring above for why folding it into
    this same fnmatch check would over-block a documented must-stay-allowed
    shape (`rm -rf ./*/build-tmp`).

    REM-FIX (CRITICAL, live-confirmed bypass): when `component` contains a
    comma-bearing brace-expansion group (`{a,.git}`, `{tools,packages}`,
    including a NESTED group like `{a,{.git,c}}`), every concrete
    alternative bash would actually expand it to is checked independently
    via `_expand_brace_groups()` -- each expanded alternative is itself
    routed through the SAME fnmatch check below (so a brace alternative
    that is ITSELF a partial wildcard, e.g. `{a,pack*ages}`, is also
    caught, not just an exact literal name). If `_expand_brace_groups()`
    hit its own recursion/fan-out bound before fully resolving the group
    (`bound_exceeded=True`), this returns True unconditionally -- fail
    CLOSED on an adversarially-deep or wide brace shape rather than
    matching only against its (necessarily incomplete) candidate list."""
    if _BARE_WILDCARD_COMPONENT_RE.match(component):
        return False
    if _component_has_brace_group(component):
        candidates, bound_exceeded = _expand_brace_groups(component)
        if bound_exceeded:
            return True
        return any(
            fnmatch.fnmatch(child, candidate)
            for candidate in candidates
            for child in CRITICAL_TOP_LEVEL_CHILDREN
        )
    return any(fnmatch.fnmatch(child, component) for child in CRITICAL_TOP_LEVEL_CHILDREN)


def _split_command_name(tokens: list) -> tuple:
    """Return (command_name, rest_tokens), skipping leading env assignments."""
    idx = 0
    while idx < len(tokens) and is_env_assignment(tokens[idx]):
        idx += 1
    if idx >= len(tokens):
        return None, []
    return os.path.basename(tokens[idx]), tokens[idx + 1 :]


# Extglob pattern-list group prefix characters -- see the matching
# duplicated-by-design constant/docstring in craftflow_hooklib.py's
# `command_has_traversal_or_wildcard()` for why this is duplicated rather
# than cross-imported (a private, underscore-prefixed name), matching this
# module's existing PROTECTED_MEMORY_FILES precedent.
_EXTGLOB_PREFIX_CHARS = ("!", "@", "?", "+", "*")


def _positional_targets(rest: list) -> tuple:
    """Return (path_tokens, has_unresolvable_token) using the generic
    positional-token model: skip flags, collect the remaining tokens as
    candidate path targets. `--target-directory=<dir>` (mv's long-option
    destination) is a disguised path-carrying flag -- captured explicitly
    instead of being silently skipped like a bare flag (HIGH 6).

    REM-FIX (MEDIUM, live-confirmed bypass): an extglob pattern-list group
    (`!(scratch)`, requires `shopt -s extglob`) tokenizes via shlex's
    punctuation_chars=True as a FRAGMENTED span -- a token ending in one of
    `!`/`@`/`?`/`+`/`*` immediately followed by a literal `"("` token (e.g.
    `["./!", "(", "scratch", ")"]` for `./!(scratch)`), never as one token
    containing the group verbatim. `!(pattern-list)` in particular matches
    EVERYTHING except the given pattern-list -- an unbounded, real-
    directory-content-dependent expansion this guard cannot enumerate ahead
    of time (unlike a `{a,b}` brace group, whose alternatives are fully
    spelled out in the command text itself). The whole fragmented span is
    treated as one opaque, unresolvable target via `_scan_paren_depth()`
    (already used for `$(...)` fragment scanning) -- the same fail-closed
    treatment as a dynamic `$()`/backtick substitution -- rather than
    silently reducing to unrelated-looking literal path tokens.

    REM-FIX (composition-boundary bypass, follow-up to the above):
    `rm -rf ./{$(echo a),.git}` nests a `$(...)` command substitution
    INSIDE a brace-expansion group -- shlex's punctuation_chars=True splits
    the substitution's `(`/`)` into their own tokens exactly as above, but
    now the split happens MID-brace-group (e.g.
    `["./{$", "(", "echo", "a", ")", ",.git}"]`), fragmenting the enclosing
    `{...,...}` syntax across tokens before either the brace-detection
    logic (`_component_matches_critical_child`) or this function's own
    per-token model ever sees one clean component -- the trailing
    alternative (`",.git}"`) was previously appended as an unrelated-
    looking literal path token that matches nothing. A token with more `{`
    than `}` characters is this fragmented-brace-span signature (an intact
    brace group, nested or not, always tokenizes as exactly one token with
    BALANCED `{`/`}` counts -- see `_tokens_contain_unbalanced_brace()`'s
    docstring in craftflow_hooklib.py for why). The whole span is scanned
    via `_scan_brace_depth()` (the same technique as `_scan_paren_depth()`
    just above) and treated as one opaque, unresolvable target -- not
    fully re-parsed into concrete alternatives (that would require
    resolving the nested `$(...)` value itself, which this guard never
    attempts for any dynamic substitution) -- so none of its fragments are
    added as literal path tokens. This alone doesn't have to carry the
    deny decision: `command_has_traversal_or_wildcard()` (hooklib) also
    recognizes the same fragmented-brace signature, which is what routes
    this into `denied_dynamic` rather than the non-denying `unverifiable`
    bucket in main() below."""
    paths = []
    unresolvable = False
    past_flag_terminator = False
    idx = 0
    while idx < len(rest):
        token = rest[idx]
        if token == "--" and not past_flag_terminator:
            past_flag_terminator = True
            idx += 1
            continue
        if not past_flag_terminator and token.startswith("-") and token != "-":
            match = _TARGET_DIRECTORY_RE.match(token) or _TARGET_DIRECTORY_SHORT_RE.match(token)
            if match:
                captured = match.group(1)
                if looks_dynamic(captured):
                    unresolvable = True
                else:
                    paths.append(captured)
            idx += 1
            continue
        if token and token[-1] in _EXTGLOB_PREFIX_CHARS and idx + 1 < len(rest) and rest[idx + 1] == "(":
            unresolvable = True
            end_idx = _scan_paren_depth(rest, idx + 1)
            idx = len(rest) if end_idx is None else end_idx + 1
            continue
        if token.count("{") > token.count("}"):
            unresolvable = True
            end_idx = _scan_brace_depth(rest, idx)
            idx = len(rest) if end_idx is None else end_idx + 1
            continue
        if looks_dynamic(token):
            unresolvable = True
            idx += 1
            continue
        paths.append(token)
        idx += 1
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


def _scan_brace_depth(rest: list, start_idx: int) -> "int | None":
    """Same technique as `_scan_paren_depth()` above, but tracking `{`/`}`
    CHARACTER depth instead of `(`/`)` -- used to find where a FRAGMENTED
    brace-expansion span (one whose opening `{` didn't get a matching `}`
    within its own token, because a `$(...)`/backtick command substitution
    nested inside it forced shlex's punctuation_chars=True to split the
    substitution's `(`/`)` into their own separate tokens) actually closes
    (REM-FIX, composition-boundary bypass: `rm -rf ./{$(echo a),.git}`).

    `rest[start_idx]` is expected to be the token that OPENS the imbalance
    (more `{` than `}` characters within it) -- depth starts at 0 and this
    token's own characters are scanned first, exactly like
    `_scan_paren_depth()` does for its own start token.

    Returns the index of the token in which brace depth first returns to
    0, or None if it never does across every remaining token (a
    malformed/unbalanced span -- callers MUST fail CLOSED on None, same
    contract as `_scan_paren_depth()`)."""
    depth = 0
    for idx in range(start_idx, len(rest)):
        for ch in rest[idx]:
            if ch == "{":
                depth += 1
            elif ch == "}":
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


def _has_wildcard_adjacent_critical_child(resolved: Path) -> bool:
    """True if any component of `resolved` is a wildcard-LIKE filler
    (contains `*`, `?`, or `[` -- see `_is_wildcard_like_filler_component()`)
    immediately followed by one of CRITICAL_TOP_LEVEL_CHILDREN as the VERY
    NEXT component -- catches a wildcard used as a MIDDLE path segment (e.g.
    `./*/.git`, `./*/packages`, `*/tools`, `./*/*/packages`, `./**/.git`,
    `./?/.git`, `./[a]/.git`, `./[!x]/packages`, `./*a/tools`,
    `./a*/packages`), not just a trailing/final wildcard or an
    exact/descendant match against a critical path (residual gap found in
    post-BUILD verification of
    wf-build-craftflow-guardrail-harden-20260728-093811-2c402af7, commit
    54f6756).

    pathlib.Path never glob-expands `*`/`?`/`[...]` -- each stays a literal,
    opaque path component all the way through `.resolve()`. That means
    `./*/.git` resolves to a literal `<cwd>/*/.git`: neither equal to nor a
    descendant of `<cwd>/.git`, so it was previously invisible to both the
    exact-match and descendant-of checks below. This generalizes to ANY
    depth (a filler-then-critical-child ADJACENT pair anywhere in the
    resolved path's components, not only the first segment after cwd), to
    consecutive/non-adjacent filler segments (the pair check only needs the
    LAST filler immediately before the critical name to fire), and to a
    globstar component (self-caught during the original fix's own
    adversarial verification pass: a single `*` check alone missed
    `./**/.git`).

    The component immediately AFTER the filler is matched via
    `_component_matches_critical_child()` rather than exact
    `in CRITICAL_TOP_LEVEL_CHILDREN` membership (REM-FIX round 2, Critical
    Issue 2), so a partial-glob critical name attached directly after a
    wildcard middle segment (`./*/pack*ages`) -- the exact middle-segment
    bug class this whole fix targets, defeated by attaching characters to
    the critical name -- is also caught, not just an exact literal name.

    The FILLER side of the pair is matched via
    `_is_wildcard_like_filler_component()` rather than the narrower,
    stars-only `_BARE_WILDCARD_COMPONENT_RE` (REM-FIX round 3: the round-2
    fnmatch unification only reached the critical-name side of this pair,
    leaving `?`/`[a]`/`[!x]`/partial-star fillers invisible as FILLERS even
    though the identical shapes were already correctly recognized as
    critical-NAME components elsewhere in this module)."""
    parts = resolved.parts
    for idx in range(len(parts) - 1):
        if _is_wildcard_like_filler_component(parts[idx]) and _component_matches_critical_child(parts[idx + 1]):
            return True
    return False


def _is_in_cwd_critical(resolved: Path, cwd: Path) -> bool:
    """True if a within-cwd destructive target is cwd itself, a literal
    `*`/globstar `**`/`.` as `resolved`'s OWN final component (checked via
    the resolved name, since resolving away the literal token is exactly
    what makes these dangerous), a TOP-LEVEL child of cwd that matches
    (exactly, or via a partial-wildcard glob) one of
    CRITICAL_TOP_LEVEL_CHILDREN, a DESCENDANT of one of them (HIGH 5 --
    `rm -rf packages/agent-cli` is a real, previously-invisible bypass of
    the exact-equality-only check), or a wildcard component immediately
    followed by a critical-matching name anywhere in the path (residual
    gap, post-BUILD verification -- see
    `_has_wildcard_adjacent_critical_child`).

    REM-FIX round 2 (2 CRITICAL sibling bypasses found by code-reviewer):

    1. The trailing bare-wildcard check below now uses
       `_BARE_WILDCARD_COMPONENT_RE` (recognizes `*` AND globstar `**`)
       instead of a hardcoded `resolved.name == "*"` literal -- that
       hardcoded literal was the ONE place in this module `_BARE_WILDCARD_
       COMPONENT_RE` was NOT wired into, despite being semantically
       identical to the check it replaces here, so `rm -rf **`/
       `rm -rf ./**` (the identical blast radius as `rm -rf *` under
       `shopt -s globstar`) were live-confirmed ALLOWED before this fix.
    2. The exact top-level-child-of-cwd check (previously
       `resolved in {cwd / child for child in CRITICAL_TOP_LEVEL_CHILDREN}`
       plus a separate `any(... in resolved.parents ...)` descendant check,
       both exact-string-only) is now ONE unified check via
       `_component_matches_critical_child()` on `resolved`'s own top-level
       component relative to cwd -- generalizing BOTH the exact-identity
       case and the arbitrary-depth descendant case to also recognize a
       partial-wildcard component like `pack*ages` (a real, valid bash glob
       expanding to `packages`), which was previously neither a bare
       wildcard nor an exact match and evaded detection entirely."""
    if resolved == cwd:
        return True
    if resolved.name == "." or _BARE_WILDCARD_COMPONENT_RE.match(resolved.name):
        return True
    if cwd in resolved.parents:
        top_level_component = resolved.relative_to(cwd).parts[0]
        if _component_matches_critical_child(top_level_component):
            return True
    return _has_wildcard_adjacent_critical_child(resolved)


def main() -> int:
    # REM-FIX (CRITICAL, algorithmic-complexity DoS variant -- unbounded
    # call COUNT): reset the process-wide brace-scan budget exactly once,
    # at the very top of this function, before any subcommand or
    # path-component parsing begins -- see `_MAX_BRACE_SCAN_TOTAL_BUDGET`'s
    # own module-level docstring for the call-count DoS this closes. Every
    # `_iter_brace_groups()` call made anywhere below (across every
    # subcommand and every path component) shares this SAME budget for the
    # remainder of this invocation.
    _reset_global_brace_scan_budget()

    data = load_input()
    if data.get("tool_name") != "Bash":
        return 0

    # REM-FIX cycle 4 (silent-failure-hunter, live-reproduced CRITICAL): `or {}` only
    # substitutes on a FALSY value (None, [], "", 0) -- a truthy non-dict like `["a"]`
    # survives untouched and crashes on the `.get("command")` call below. Explicit
    # isinstance check coerces any non-dict value to {}, not just falsy ones.
    raw_tool_input = data.get("tool_input")
    tool_input = raw_tool_input if isinstance(raw_tool_input, dict) else {}
    command = tool_input.get("command")
    if not command or not isinstance(command, str):
        return 0

    # REM-FIX (ANSI-C quoting bypass, doubt-verify): decode every `$'...'`
    # span BEFORE anything else touches `command` -- both split_subcommands()
    # (used for destructive-target extraction and the redirect-confinement
    # check below) and command_has_traversal_or_wildcard() must see the
    # decoded literal, not the raw, undecoded escape text. A no-op when no
    # `$'...'` span is present. See `_expand_ansi_c_quotes()`'s own
    # docstring above for the full bypass mechanics.
    command = _expand_ansi_c_quotes(command)

    cwd_raw = data.get("cwd")
    if not cwd_raw:
        return 0
    cwd = Path(cwd_raw).resolve()

    mode = load_mode()
    # REM-FIX (HIGH, doubt-verify cycle 2): the identical unvalidated
    # `mode.get(...) == "block"` pattern already fixed for `memoryWrites`/
    # `protectedWrites` in the sibling craftflow_pretooluse_guard.py gated
    # this script's ENTIRE core destructive-command detection -- a typo'd
    # value (e.g. "Block" capital-B) silently ALLOWED every destructive
    # command this hook exists to block, with zero distinguishing signal.
    #
    # DESIGN DECISION: `fail_closed_on_unrecognized=True` here is a
    # deliberate DIVERGENCE from memoryWrites/protectedWrites (which pass
    # the default False and audit-degrade on an unrecognized value). This
    # toggle's own MISSING-key behavior already fail-closes -- the
    # `mode.get("bashDestructiveTraversal", "block")` default below is
    # "block", not "audit". An unrecognized-but-PRESENT value (a typo) is
    # the same kind of "config didn't give a clear answer" as a missing
    # key, so it should fail the SAME way (closed/deny), not flip to the
    # OPPOSITE, more permissive posture just because a key happened to be
    # present with a bad value. See resolve_toggle_decision()'s own
    # docstring in craftflow_hooklib.py for the shared enum-validation
    # logic (now used by three callers across two guard scripts).
    block_mode, bash_destructive_traversal_decision = resolve_toggle_decision(
        mode.get("bashDestructiveTraversal", "block"), fail_closed_on_unrecognized=True
    )

    # Worktree confinement (Task 3.2 step 2): read worktree_path from the
    # active workflow JSON via the shared hooklib helper, never raising --
    # absence of a workflow JSON, or worktree_path: null, degrades every
    # confinement check below to cwd-only (Behavior Contract rule 8).
    try:
        worktree_path = latest_workflow_payload().get("worktree_path")
    except Exception as exc:
        # REM-FIX cycle 4 (consistency, MEDIUM): mirrors the equivalent
        # latest_workflow_payload() except blocks in the sibling
        # craftflow_pretooluse_guard.py -- this block was silently swallowing the
        # exception with no log_event() call, unlike every other parse-error
        # fallback in this file.
        log_event(
            "plugin_pretooluse_bash_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "latest_workflow_payload",
                "error": repr(exc),
                "reason": "skipped_worktree_lookup",
            },
        )
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

    # REM-FIX (HIGH, doubt-verify cycle 2): logs the SAME distinguishing
    # decision string resolve_toggle_decision() computed above (plain
    # "deny" for a recognized "block" value, "block-unrecognized-config-
    # value" for a typo) instead of a hardcoded "deny" literal -- a
    # misconfigured bashDestructiveTraversal value must be greppable in
    # craftflow-hook-events.log, distinct from an intentional, recognized
    # "block" choice.
    log_event(
        "plugin_pretooluse_bash_guard",
        {
            "event": "pretool_guard",
            "tool_name": "Bash",
            "cwd": str(cwd),
            "command": command,
            "decision": bash_destructive_traversal_decision if deny_now else "audit",
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
