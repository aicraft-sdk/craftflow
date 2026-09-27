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
# This cap is scoped to matches_allowlist() ONLY -- category classification does not do
# credential redaction, so truncating its input has no secret-hygiene consequence. It must NEVER
# be applied to the raw text that reaches redact_action() (see build_state()'s own docstring for
# why: an early cycle briefly reused this exact idea -- capping raw input before redaction runs --
# and that is precisely the bug class the cycle-6 architectural redesign eliminates). Confirmed
# structurally independent from build_state()/redact_action() across multiple review passes: it
# never flows into redaction, and is left unchanged by that redesign.
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
# REM-FIX (doubt-verifier CRITICAL, corroborated 3x, REM-FIX cycle 5): the `\s+` alternative in
# this separator group was UNBOUNDED. Cycle 5 originally reasoned about this in terms of a
# now-removed fixed redaction window (see cycle 6's architectural redesign, above and in
# build_state()'s docstring) -- but the bug and its fix are orthogonal to that window and remain
# valid on their own terms: an unbounded separator lets an attacker pad the gap between
# `--password` and its value with an arbitrary number of spaces, which can defeat the
# quoted-value alternative's ability to find its own closing quote inside whatever text the
# regex is actually given, causing a fallback to the unquoted `\S{1,4096}` alternative that masks
# only part of the real secret while still reporting `redacted=True`. No realistic CLI invocation
# has dozens of spaces between a flag and its value, so bounding this separator to 32 is safe and
# closes the "attacker pads with arbitrary whitespace" structural hole for good (see
# test_redact_action_large_separator_gap_never_produces_false_safe_leak).
_URL_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]{0,20}://[^\s:/@]{1,256}:)[^\s]{1,512}@")
_ASSIGNMENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{0,128})=(\"[^\"]{0,4096}\"|'[^']{0,4096}'|\S{1,4096})")
_SECRET_FLAG = re.compile(
    r"(--(?:password|passwd|token|secret|api-key|apikey)(?:=|\s{1,32}))(\"[^\"]{0,4096}\"|'[^']{0,4096}'|\S{1,4096})",
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

# REM-FIX (architectural redesign, REM-FIX cycle 6; origin: code-reviewer + silent-failure-hunter,
# 6 rounds of findings). Cycles 1-5 each fixed a credential leak at one fixed cut point applied
# BEFORE pattern-matching, only to have the same bug class resurface at a new boundary -- see
# build_state()'s own docstring for the full condensed audit trail (cycles 1-5), and this
# module's git history for each cycle's original, much longer per-cycle comments this redesign
# replaces. The root flaw: ANY fixed-size slice applied before redact_action()'s regex passes run
# can be straddled by an adversarially-positioned credential, because the cut point and the
# pattern match are two independent things that can misalign -- no cut SIZE avoids this, only
# never introducing such a cut (before matching) does.
#
# This redesign eliminates that architecture entirely. `redact_action()` itself now applies NO
# truncation of its own, at input or output -- it runs its 4 regex passes over the full text it is
# given (see its own docstring). All that remains is a single, large, DoS-ONLY backstop cap
# (`_ACTION_TEXT_DOS_BACKSTOP_CHARS`, applied to the raw `action_text` inside build_state(), before
# it ever reaches redact_action()) -- sized so many orders of magnitude larger than any plausible
# credential that no real-world input could ever position a credential near it, making the
# boundary's existence practically irrelevant to the credential-safety property while still
# bounding worst-case compute time on a genuinely pathological (multi-megabyte) adversarial input.
# See that constant's own docstring, directly above build_state(), for the exact size derivation.
#
# The previously separate `_REDACT_ACTION_SAFETY_CAP` (redact_action()'s own 200,000-char
# OUTPUT-side bound) is removed, not kept "just in case": it existed only for a hypothetical
# future direct caller of redact_action() that bypasses build_state()'s `max_chars` egress budget
# -- no such caller exists anywhere in this codebase today (build_state() is redact_action()'s
# only call site, confirmed by grep across scripts/ and tests/). Stacking a second, smaller,
# output-side cap on top of the new input-side backstop would be dead weight that adds a second
# thing to reason about without closing any gap the backstop doesn't already close. If a second
# real call site is ever added, size a cap for its own actual needs then -- do not resurrect this
# one speculatively.


def _is_credential_shaped_name(name: str) -> bool:
    return any(part in name.upper() for part in _SENSITIVE_NAME_PARTS)


def _redact_assignment(match: "re.Match[str]") -> str:
    name = match.group(1)
    if _is_credential_shaped_name(name):
        return f"{name}={_REDACTED}"
    return match.group(0)


# REM-FIX (architectural restoration, REM-FIX cycle 9; origin: code-reviewer, 2 CRITICAL findings
# against cycle 8's own reimplementation of substitution). Cycle 8 correctly closed the
# multi-match cumulative-shrink leak (see build_state()'s docstring, cycle 8 entry) by gating
# inclusion on each match's ORIGINAL position -- but it did so by REPLACING the sequential
# `.sub()` chain with a from-scratch `finditer()` + first-claim-wins span assembly
# (`_find_redaction_spans()`/`_assemble_redacted_text()`, removed by this cycle). That
# reimplementation introduced two NEW bugs live-reproduced this cycle:
#   1. Overlap bug (confidentiality regression): when a later-priority pattern's candidate span
#      overlapped an earlier-claimed span but extended beyond it, the ENTIRE later candidate was
#      dropped -- not just the overlapping portion -- even when the later pattern's span was much
#      larger and would have fully masked the real secret
#      (e.g. `redact_action("mysql --password=http://u:x@REALSECRETTAIL999 --other-flag", None)`
#      leaked `REALSECRETTAIL999` because `_URL_USERINFO` claimed the small nested
#      `http://u:x@` substring first, and `_SECRET_FLAG`'s full-masking candidate span was
#      discarded wholesale for overlapping it).
#   2. Performance regression (DoS): `_claim()` linearly scanned every previously-claimed span for
#      every new candidate span -- O(n) per candidate, O(n^2) total in match count. Measured ~2.2s
#      at the production `max_chars` ceiling (150000) on dense, realistic credential-shaped input
#      -- ~43% of the hook's entire 5-second PreToolUse timeout, before the Jev call or telemetry
#      append even run.
# The OLD sequential `.sub()` chain restored below does NOT have either problem: each pass
# re-scans the ALREADY-substituted output of the previous pass (so a later pattern can still mask
# text a smaller earlier pattern already touched -- no "claimed, untouchable" region concept
# exists), and each pass is a single O(n) `.sub()` call with no per-match claim-list scan. This is
# the exact function (byte-identical logic) that was in place through cycle 7, proven correct on
# overlap across cycles 1-7's own findings.
#
# The multi-match cumulative-shrink property cycle 8 closed is preserved WITHOUT reintroducing
# either of the above: build_state() now computes a `safe_cut` boundary on the RAW,
# pre-substitution text via a separate, purely additive, read-only pre-pass
# (`_compute_safe_cut()`, below) BEFORE this chain ever runs. Because the cut point is decided on
# original character positions before any substitution can shrink anything, there is nothing left
# for cumulative shrinkage to corrupt -- see `_compute_safe_cut()`'s own docstring.
def redact_action(text: str, api_key: Optional[str]) -> Tuple[str, bool]:
    """Pure. Masks values that must never leave the machine, in order: (1) the literal
    `api_key` if non-empty, (2) URL userinfo passwords, (3) credential-shaped `NAME=value`
    env-style assignments, (4) `--password`/`--token`/`--secret`/`--api-key`-style flag values,
    (5) single-dash concatenated `-p<value>`/`-u<value>` CLI flags (mysql/psql convention).
    Never raises. Runs every pass over the FULL text it is given -- applies NO truncation of its
    own, at input or output (unchanged property since cycle 6): any cut point this function
    introduced itself could, in principle, land inside a credential before its terminating
    delimiter, silently defeating the very redaction it exists to do. The caller (build_state())
    is responsible for handing this function a text window that is already safe to redact in
    full -- see `_compute_safe_cut()`. Each pass runs over the ALREADY-substituted output of the
    previous pass (a plain sequential `re.sub()` chain, restored REM-FIX cycle 9 -- see the
    module-level comment directly above this function for why this is deliberately NOT the
    finditer/span-assembly approach cycle 8 introduced and this cycle removed). Returns
    (possibly-modified text, whether anything was actually masked)."""
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


# REM-FIX (architectural redesign, REM-FIX cycle 6; timing comment corrected REM-FIX cycle 7).
# Pure DoS-only backstop -- bounds worst-case compute time on a genuinely pathological
# (multi-megabyte) adversarial Bash command; it is NOT sized to "be big enough to contain any
# credential" (that framing is exactly what made cycles 3-5's caps/margins straddle-able -- see
# build_state()'s own docstring for the condensed history this redesign replaces). Sized instead
# so many orders of magnitude larger than any plausible credential, flag value, or realistic Bash
# command that no real-world input could ever position a credential near this boundary, making the
# boundary's existence practically irrelevant to the credential-safety property:
#   - Benchmarked cost of redact_action()'s regex passes at ~0.65s (5 runs, 0.641-0.657s range)
#     for a 5,000,000-char (~5MB) input, measured with genuinely ADVERSARIAL, dense-match-
#     triggering text (a repeating unit packing overlapping "://", "=", "--password="/"-p"-shaped
#     substrings and "DROP TABLE"/"migrate:down" phrases throughout the input) -- NOT a repeated
#     single character, which produces near-zero regex matches and understates the true worst-case
#     cost by roughly 13x (independently re-measured for cycle 7; corrects the prior "~0.01s/MB,
#     ~0.05s at 5,000,000 chars" figure, which was benchmarked against sparse input and did not
#     reflect this threat model).
#   - Even at that corrected cost, this remains a small fraction (~13%) of the 5-second PreToolUse
#     hook timeout, leaving ample headroom for the rest of the hook's work (the Jev call itself,
#     telemetry append).
#   - Every credential this module's patterns can ever recognize is bounded to a generously-sized
#     ~4,096-char value capture (see each pattern's own bounded quantifier) -- roughly 1,200x
#     smaller than this cap. No realistic command embeds a credential anywhere near this boundary;
#     an adversary would need to deliberately pad a command with several million filler characters
#     to even attempt landing a credential near it, which is a categorically different (and
#     separately implausible/detectable) threat from "a credential positioned anywhere in a
#     realistic-to-large command" -- the property this redesign actually needs to guarantee.
_ACTION_TEXT_DOS_BACKSTOP_CHARS = 5_000_000

# REM-FIX (REM-FIX cycle 7; re-justified under cycle 8's position-tracked design, still valid
# under cycle 9's restoration). Fixed, small, match-context lookahead used to widen the text
# `build_state()` searches for matches a little beyond `effective_cap` -- so that a match
# STARTING before `effective_cap` is seen with its full span (hence its correct, complete
# replacement text and correct `safe_cut` contribution) even when that span extends past
# `effective_cap`. Sized comfortably larger than the largest total match span any single pattern
# in this module can ever produce (the biggest is `_ASSIGNMENT`: 129-char name + "=" + up to
# 4,096-char quoted/unquoted value + 2 quote chars == 4,228 chars; `_URL_USERINFO`, `_SECRET_FLAG`,
# and `_SHORT_FLAG_CONCAT` are all smaller).
#
# This constant needs NO further scaling for multiple preceding matches: `_compute_safe_cut()`
# (below) decides how far to extend the cut using each match's OWN span in the ORIGINAL,
# pre-substitution text -- never by counting assembled output characters -- so how many OTHER
# matches precede a given match, or how much any of them would eventually shrink under
# redaction, has no bearing on whether THIS match's own span is captured within the search
# window. One fixed-size margin, sized to the single largest possible match span, remains correct
# regardless of how many decoy matches occur earlier in the text.
_REDACT_MATCH_LOOKAHEAD_CHARS = 8192


def _collect_credential_match_spans(text: str, api_key: Optional[str]) -> List[Tuple[int, int]]:
    """Pure, READ-ONLY (performs no substitution). Returns every (start, end) span, keyed to
    `text`'s own original character offsets, that the sequential `redact_action()` chain below
    could ever match or replace: literal `api_key` occurrences, then all 4 regex patterns in the
    same order `redact_action()` applies them. Does not filter `_ASSIGNMENT` matches by
    credential-shapedness (unlike `_redact_assignment()`, which only replaces credential-shaped
    names) -- collecting a superset of spans here only makes `_compute_safe_cut()`'s resulting cut
    sit at or after the minimum required boundary, never before it, which is the safe direction;
    it never causes an actual leak, only (at most) a few extra bytes of otherwise-harmless
    non-credential text included in the redacted output.

    Includes literal `api_key` occurrences (a deliberate addition beyond the 4 regex patterns):
    `redact_action()`'s FIRST substitution step is an exact literal `str.replace(api_key, ...)` --
    if the real `api_key` value itself straddled the same cut boundary the 4 regex patterns are
    protected against, slicing the raw text at that boundary would truncate the literal occurrence
    before `.replace()` ever saw it, leaving an unmasked fragment of the real secret in the output.
    There is no principled reason the literal `api_key` match should be exempt from the same
    straddling-cut protection every regex-matched credential shape gets, so it is folded into the
    same safe-cut computation for consistency."""
    spans: List[Tuple[int, int]] = []
    if api_key:
        idx = text.find(api_key)
        while idx != -1:
            spans.append((idx, idx + len(api_key)))
            idx = text.find(api_key, idx + len(api_key))
    for pattern in (_URL_USERINFO, _ASSIGNMENT, _SECRET_FLAG, _SHORT_FLAG_CONCAT):
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end()))
    return spans


def _compute_safe_cut(text: str, api_key: Optional[str], cap: int) -> int:
    """Pure, READ-ONLY (performs no substitution) -- REM-FIX cycle 9 (origin: code-reviewer, 2
    CRITICAL findings against cycle 8's finditer/span-assembly reimplementation of substitution
    itself; see `redact_action()`'s module-level comment for the full account of what cycle 8 got
    wrong). This function is the ONLY new piece of machinery cycle 9 adds -- it decides a single
    safe cut point on the RAW, pre-substitution `text`, entirely separately from (and before) the
    restored sequential `.sub()` chain ever runs, so that chain can keep working exactly as it did
    through cycle 7 (proven correct on overlap across cycles 1-7) while still closing cycle 8's
    own legitimate target: a real credential must never be partially exposed because earlier
    decoy redactions cumulatively shrank the text and pulled unredacted content across an
    output-length-based cut.

    Every match (from `_collect_credential_match_spans()`) whose ORIGINAL `.start()` is < `cap` is
    "qualifying" -- it must survive to the sequential chain with its FULL span intact, or the
    chain could see a truncated fragment: either fail to match it at all (a partially-truncated
    literal `api_key`) or match a corrupted remainder (a partially-truncated regex value),  in
    both cases risking a partial, unmasked leak. `safe_cut` is therefore the smallest boundary
    that is guaranteed not to straddle any qualifying match: `max(cap, max(end for every
    qualifying match))`, or just `cap` if no match qualifies. A match starting at or after `cap`
    is correctly excluded from this computation -- it was never going to be included in the
    caller's returned text anyway (dropping it entirely, not partially, is the safe outcome for
    content beyond the egress budget).

    Because this decision is made entirely on ORIGINAL, pre-substitution positions -- before the
    sequential chain has shrunk anything -- there is nothing left for cumulative shrinkage to
    corrupt: `redact_action()` subsequently runs on `text[:safe_cut]` as a single, complete,
    already-safe window, and its own overlap handling (unmodified since cycle 7 and earlier)
    produces the final output directly with no further slicing needed."""
    qualifying_ends = [end for start, end in _collect_credential_match_spans(text, api_key) if start < cap]
    return max(cap, max(qualifying_ends)) if qualifying_ends else cap


def build_state(
    tool_name: str, category: str, action_text: str, *, api_key: Optional[str], max_chars: int
) -> Dict[str, Any]:
    """Pure. `action_text` is the raw command (Bash) or file_path (Write/Edit) -- NEVER
    content/new_string/old_string (see module docstring, P1).

    REM-FIX (architectural restoration, REM-FIX cycle 9; origin: code-reviewer, 2 CRITICAL
    findings against cycle 8's own reimplementation of substitution). Condensed audit trail (see
    git history for each cycle's original, much longer comments; cycles 1-7 summarized here,
    cycle 8 and 9 in full since they are the two most recent architectural changes):
      cycle 1: a pre-redaction truncation cap inside build_state reopened the URL-userinfo leak
               (a straddling cut lost the credential's terminating delimiter before redact_action
               ever saw it).
      cycle 2: fixed cycle 1 by moving the cap to redact_action()'s OUTPUT side only.
      cycle 3: reintroduced cycle 1's exact bug at a NEW boundary by adding a pre-redaction INPUT
               cap for compute-cost reasons (100,000 chars).
      cycle 4: widened the cap into a "cap + margin" window (104,096 chars) -- closed cycle 3's
               recurrence, but the margin math was later found under-derived.
      cycle 5: bounded an unbounded regex separator AND recomputed the margin (104,500 chars) from
               each pattern's true full match span -- closed that specific gap, but a credential
               starting inside the margin zone itself reproduced the SAME straddling-cut-point bug
               at the new (bigger) boundary.
      cycle 6: eliminated the fixed-cut-point-before-matching architecture: redact_action() ran
               over the FULL text it was given (no cut of its own), and a single, large, DoS-ONLY
               backstop cap (`_ACTION_TEXT_DOS_BACKSTOP_CHARS`) was applied to the RAW
               `action_text` before it reached redact_action() at all.
      cycle 7: coupled the backstop cut and the caller-supplied `max_chars` egress budget into one
               `effective_cap`, and extended the pre-redaction match WINDOW past `effective_cap`
               by `_REDACT_MATCH_LOOKAHEAD_CHARS` so a single credential straddling the boundary
               would still be seen whole by `redact_action()`. This closed the single-credential
               straddle -- but `redact_action()` still worked by substituting into a whole string
               and then SLICING THE OUTPUT (`redacted_text[:effective_cap]`), which silently
               assumed a POST-redaction output position corresponds to the same PRE-redaction
               input position -- an assumption that breaks whenever MULTIPLE credential-shaped
               matches occur earlier in the text (their cumulative shrinkage can pull content that
               originated PAST `effective_cap` into the region an output-length slice returns).
      cycle 8: correctly diagnosed cycle 7's root cause (output-length-based inclusion) and closed
               the multi-match cumulative-shrink leak -- but did so by REPLACING the sequential
               `.sub()` chain with a from-scratch `finditer()` + first-claim-wins span-assembly
               reimplementation of substitution itself. That reimplementation introduced two NEW
               bugs: an overlap bug (a later, larger, fully-masking pattern's candidate span was
               dropped WHOLESALE -- not just its overlapping portion -- when it overlapped an
               earlier-claimed smaller span, live-reproduced leaking a real secret in cleartext),
               and a performance regression (the per-candidate linear scan of claimed spans made
               the pass O(n^2) in match count, measured ~2.2s at the production max_chars=150000
               ceiling on dense adversarial input -- ~43% of the hook's entire 5-second timeout).
      cycle 9 (this cycle): restores the original sequential `.sub()` chain byte-identical to its
               pre-cycle-8 form as the ONLY substitution mechanism (proven correct on overlap
               across cycles 1-7; see `redact_action()`'s own docstring) -- this closes both of
               cycle 8's new bugs, since a plain `.sub()` chain has neither a claim-list to scan
               (no O(n^2)) nor a "claimed, untouchable" region concept (a later pass can still mask
               text an earlier, smaller pattern already touched). Cycle 8's own legitimate target
               (multi-match cumulative-shrink) is preserved via a SEPARATE, purely additive,
               read-only pre-pass, `_compute_safe_cut()`, that decides a single cut point on the
               RAW, pre-substitution text -- BEFORE the sequential chain ever runs. Because the
               cut point is decided entirely on original character positions, before any
               substitution can shrink anything, there is nothing left for cumulative shrinkage to
               corrupt (unlike cycle 7's output-length-based `[:effective_cap]` slice).
    Current pipeline:
      1. `effective_cap = min(max_chars, _ACTION_TEXT_DOS_BACKSTOP_CHARS)` -- unchanged since
         cycle 7 -- is the single value that governs what can ever be returned.
      2. `search_window = action_text[: effective_cap + _REDACT_MATCH_LOOKAHEAD_CHARS]` -- also
         unchanged in form -- gives the read-only pre-pass enough trailing text to see the FULL
         span of any match starting at or before `effective_cap`.
      3. `safe_cut = _compute_safe_cut(search_window, api_key, effective_cap)` computes the single
         cut point on RAW positions (see its own docstring).
      4. `safe_input = search_window[:safe_cut]` slices the RAW (still unredacted) text at that
         boundary -- this is the ONLY slice ever taken of untouched input.
      5. `redact_action(safe_input, api_key)` runs the restored, unmodified sequential chain on
         `safe_input` and produces the final redacted output DIRECTLY -- no further slicing is
         applied afterward, since `safe_input` already excludes everything past the boundary."""
    effective_cap = min(max_chars, _ACTION_TEXT_DOS_BACKSTOP_CHARS)
    search_window = action_text[: effective_cap + _REDACT_MATCH_LOOKAHEAD_CHARS]
    safe_cut = _compute_safe_cut(search_window, api_key, effective_cap)
    safe_input = search_window[:safe_cut]
    redacted_text, was_redacted = redact_action(safe_input, api_key)
    truncated = len(action_text) > safe_cut
    return {
        "tool_name": tool_name,
        "category": category,
        "action_text": redacted_text,
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
