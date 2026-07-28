#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

from craftflow_hooklib import (
    MEMORY_FINALIZE_PERMIT_LITERAL,
    extract_redirect_targets,
    has_memory_finalize_permit,
    latest_workflow_payload,
    load_input,
    load_mode,
    log_event,
    matches_memory_finalize_permit_shape,
    memory_finalize_permit_path,
    pretool_deny,
    project_state_dir,
    resolve_confinement,
    split_subcommands,
    state_root,
    workflows_dir,
)


PROTECTED_MEMORY_FILES = ("activeContext.md", "patterns.md", "progress.md")

# Narrow extension for the documented `python3 -c "...open(path, 'w')..."`
# one-liner shape (Plan-vs-Code Gaps: "closes this exact gap") -- neither a
# redirect nor a `tee`, so invisible to the generic `>`/`>>`/`tee` scan.
# HIGH 1 (REM-FIX): this only matches a literal quoted string as open()'s
# FIRST positional arg -- open(path, 'w') (variable-held path) and
# open(mode='w', file='...') (kwarg-first) remain undetected; both are a
# disclosed, narrow residual gap, not closed by this plan.
_OPEN_CALL_RE = re.compile(r"open\(\s*['\"]([^'\"]+)['\"]")

# HIGH 1 (REM-FIX): a second write-call shape for the same file-write
# effect -- pathlib.Path('...').write_text(...) -- that _OPEN_CALL_RE never
# recognized since it's a different API entirely.
_PATH_WRITE_TEXT_RE = re.compile(r"Path\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\.\s*write_text\(")

# CRITICAL 2 / HIGH 2 (REM-FIX): a bare textual match for a python(3)?
# invocation ANYWHERE in the raw command text -- deliberately NOT tied to
# tokens[0] (HIGH 2: `env python3 -c "..."`/`sudo python3 -c "..."` resolve
# tokens[0] to "env"/"sudo", not "python") and NOT gated on a "-c" token
# being present in the SAME subcommand (CRITICAL 2: a heredoc-fed script,
# `python3 - <<'EOF' ... open(...).write(...) ... EOF`, never contains
# "-c" at all, and its heredoc BODY is a separate newline-delimited
# subcommand chunk once split_subcommands() splits on "\n" too -- a
# per-subcommand, per-token scan can never see it).
_PYTHON_INVOCATION_RE = re.compile(r"\bpython3?\b")

# REM-FIX (doubt-verify cycle 1): the open(...)/Path(...).write_text(...)
# checks above only recognize TWO of an unbounded set of python write-
# adjacent mechanisms that can appear inside the exact same
# `python3 -c "..."`/heredoc shape. Live-verified bypasses before this fix:
# os.system('printf x > <protected path>'),
# subprocess.run(['bash', '-c', 'printf x > <protected path>']),
# shutil.copy(src, '<protected path>'), os.rename(src, '<protected path>').
# This regex flags the most common shell-exec / file-copy-or-move call
# shapes as SUSPICIOUS when combined with a protected-path literal in the
# SAME python statement (see `_python_suspicious_mechanism_targets()`
# below -- cycle 2 tightened this from "elsewhere in the whole command
# text" to statement-level co-occurrence).
_PYTHON_SUSPICIOUS_MECHANISM_RE = re.compile(
    r"\b(?:"
    r"os\.system"
    r"|subprocess\.(?:run|call|Popen|check_call)"
    r"|shutil\.(?:copy|copyfile|move)"
    r"|os\.(?:rename|replace)"
    r")\s*\("
)

# REM-FIX (doubt-verify cycle 2, Problem 2): `import X as Y` / `from X
# import Y` forms bind an alias/name to one of the suspicious attrs above
# without ever spelling out `os.system(`/`subprocess.run(` literally --
# `import os as o; o.system(...)` and `from os import system; system(...)`
# both bypassed `_PYTHON_SUSPICIOUS_MECHANISM_RE` entirely before this fix.
_SUSPICIOUS_ATTRS_BY_MODULE = {
    "os": ("system", "rename", "replace"),
    "subprocess": ("run", "call", "Popen", "check_call"),
    "shutil": ("copy", "copyfile", "move"),
}

_IMPORT_AS_RE = re.compile(r"\bimport\s+(os|subprocess|shutil)\s+as\s+(\w+)")
_FROM_IMPORT_RE = re.compile(r"\bfrom\s+(os|subprocess|shutil)\s+import\s+([^\n;]+)")

# Best-effort extraction of the actual python source text a `-c '...'`/
# `-c "..."` invocation is passing. Needed so statement-splitting (below)
# operates on the real python code rather than getting "stuck" treating the
# single OUTER shell-quoting character as an unclosed string for the whole
# remaining command (the outer quote is shell-level framing, not a python
# string literal). Falls back to the raw command text when no such shape is
# found (e.g. a heredoc-fed script) -- heredocs are handled separately by
# `_python_script_write_targets()`'s own whole-text scan and have no
# analogous outer-quote-swallows-everything problem since there is no
# enclosing shell-quote character around the heredoc body.
_PYTHON_DASH_C_RE = re.compile(r"-c\s*(['\"])(.*)\1", re.DOTALL)


def _extract_python_code_text(command: str) -> str:
    match = _PYTHON_DASH_C_RE.search(command)
    return match.group(2) if match else command


def _split_statement_like_chunks(text: str) -> list:
    """Best-effort split of python source text into statement-like chunks
    on `;` and newline, treating an active `'...'`/`"..."` string literal
    as a single unit so a `;`/newline inside a quoted string argument is
    never treated as a statement boundary. Not a full python parser --
    matching this guard family's deterministic-but-imperfect scope.

    REM-FIX (final round): the splitter used to cut on EVERY unquoted `;`/
    newline with zero awareness of paren/bracket/brace nesting depth or
    backslash line-continuation -- an ordinary MULTI-LINE call (exactly the
    shape a formatter like `black` would produce, not an adversarial
    construction) silently bypassed the whole statement-proximity check in
    `_python_suspicious_mechanism_targets()`, since the marker
    (`os.system(`) and the protected-path literal landed in two different
    "chunks" once the newlines embedded inside the call's own still-open
    parens got treated as statement boundaries. Fixed by tracking
    paren/bracket/brace depth (a counter incremented/decremented per
    unescaped `(`/`)`/`[`/`]`/`{`/`}` character outside string literals,
    mirroring the shape of the existing quote-tracking loop) and only
    treating `;`/newline as a boundary at depth 0; a `\\` immediately
    followed by a newline is additionally treated as a line-continuation
    (never a boundary), matching real python lexical rules."""
    chunks = []
    current = []
    quote_char = None
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote_char:
            current.append(ch)
            if ch == "\\" and i + 1 < n:
                current.append(text[i + 1])
                i += 2
                continue
            if ch == quote_char:
                quote_char = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote_char = ch
            current.append(ch)
            i += 1
            continue
        if ch in "([{":
            depth += 1
            current.append(ch)
            i += 1
            continue
        if ch in ")]}":
            if depth > 0:
                depth -= 1
            current.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n and text[i + 1] == "\n":
            # Backslash line-continuation: the newline is not a statement
            # boundary, it's a lexical join of two physical lines.
            current.append(ch)
            current.append(text[i + 1])
            i += 2
            continue
        if ch in (";", "\n") and depth == 0:
            chunks.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    chunks.append("".join(current))
    return chunks


def _python_suspicious_call_bindings(code_text: str) -> set:
    """Scan python source text for `import X as Y` / `from X import Y`
    forms of os/subprocess/shutil and return the set of alias/bound-name
    call-open substrings (e.g. `"o.system("`, `"system("`) that should be
    treated as equivalent to the literal marker regex above. Does NOT
    trace a name through further re-assignment (`func = os.system`) --
    that requires real AST analysis and is a disclosed, out-of-scope gap
    (see LIMITATIONS on `_python_suspicious_mechanism_targets` below)."""
    patterns: set = set()
    for module, alias in _IMPORT_AS_RE.findall(code_text):
        for attr in _SUSPICIOUS_ATTRS_BY_MODULE[module]:
            patterns.add(f"{alias}.{attr}(")
    for module, names_blob in _FROM_IMPORT_RE.findall(code_text):
        for name_part in names_blob.split(","):
            name_part = name_part.strip()
            if not name_part:
                continue
            if " as " in name_part:
                orig, _, bound = name_part.partition(" as ")
                orig = orig.strip()
                bound = bound.strip()
            else:
                orig = bound = name_part
            if orig in _SUSPICIOUS_ATTRS_BY_MODULE[module]:
                patterns.add(f"{bound}(")
    return patterns


def _protected_memory_paths() -> set:
    """Return all active memory locations that should be write-guarded via
    the Edit/Write `file_path` check: the 3 memory .md files, plus the
    `.memory-finalize` permit sentinel (Task 4.2 step 1) -- deliberately
    NOT workflow JSON artifacts (Durable Decision, plan line 16: the router
    itself routinely Write()s workflow JSON mid-workflow; adding it here
    would break that routine orchestration)."""
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
    return paths


def _protected_bash_write_paths() -> set:
    """Protected-path set for the NEW Bash-write-inspection layer only
    (Task 4.2 step 2): reuses `_protected_memory_paths()` (the 3 .md files
    + `.memory-finalize`) rather than duplicating its glob, and additionally
    includes every top-level workflow JSON artifact -- the one path class
    deliberately excluded from the Edit/Write-gated set above."""
    paths: set = set(_protected_memory_paths())
    try:
        for candidate in workflows_dir().glob("*.json"):
            paths.add(candidate.resolve())
    except Exception:
        pass
    return paths


def _edit_write_escapes_confinement(data: dict, path: Path) -> bool:
    """True if the resolved Edit/Write target escapes {cwd} u {worktree_path}.
    Absence of "cwd" in the payload, or no active workflow JSON / a null
    worktree_path, degrades to allow (Behavior Contract rule 8) -- this only
    returns True when cwd IS known and the target genuinely escapes both."""
    cwd_raw = data.get("cwd")
    if not cwd_raw:
        return False
    cwd = Path(cwd_raw).resolve()
    worktree_path = latest_workflow_payload().get("worktree_path")
    if worktree_path is not None and not isinstance(worktree_path, str):
        worktree_path = None
    confined, _resolved = resolve_confinement(path, cwd, worktree_path)
    return not confined


def _bash_write_targets_in_tokens(tokens: list) -> list:
    """Same shape as hooklib.extract_redirect_targets(), but operating on an
    already-split subcommand's own tokens (keeps the tokens available for
    matches_memory_finalize_permit_shape()'s shape-matching). Python-script
    write detection (one-liner AND heredoc/stdin-fed) is handled separately
    by `_python_script_write_targets()` against the WHOLE raw command text
    (CRITICAL 2 / HIGH 2, REM-FIX) -- a per-subcommand, per-token scan can
    never see a heredoc body fed to python's stdin, since
    split_subcommands() splits subcommands on "\\n" too."""
    targets = []
    for idx, token in enumerate(tokens):
        if token in (">", ">>") and idx + 1 < len(tokens):
            targets.append(tokens[idx + 1])
        elif token == "tee":
            for t in tokens[idx + 1 :]:
                if not t.startswith("-"):
                    targets.append(t)
    return targets


def _python_script_write_targets(command: str) -> list:
    """Detect file-write targets from ANY python(3) invocation shape --
    `-c` one-liners AND heredoc/stdin-fed scripts
    (`python3 - <<'EOF' ... open(...) ... EOF`) -- by scanning the ENTIRE
    raw command text, rather than a single subcommand's own tokens
    (CRITICAL 2, HIGH 2). Covers `open(<literal-string>, ...)` (any
    argument order/count after the literal first positional arg) and
    `Path('...').write_text(...)`. Does NOT resolve a variable-held path
    (`open(path, 'w')`) or a kwarg-first call (`open(mode='w',
    file='...')`) from static text -- a disclosed, narrow residual gap
    (HIGH 1), not closed by this plan.

    LIMITATIONS (disclosed, not fixed -- this and the sibling
    `_python_suspicious_mechanism_targets()` below are the ENTIRE set of
    python-write detection this hook performs): this is defense-in-depth
    pattern-matching against the most common python file-write / shell-exec
    mechanisms observed in real bypass attempts, NOT an exhaustive or
    complete detector. It is fundamentally impossible to enumerate every way
    arbitrary Python code can write a file or execute a shell command from
    static text alone -- `ctypes`, `ftplib`, `ftplib.storbinary`, dynamically
    -constructed strings/attribute names (`getattr(os, 'sys'+'tem')`),
    `exec()`/`eval()`-wrapped code, and countless other APIs all route
    around a fixed vocabulary of regexes. This module makes no attempt to
    close that gap and does not claim to. Treat this as one layer of
    defense-in-depth against the common/naive cases, never as a hard
    security boundary for genuinely untrusted Python execution."""
    if not _PYTHON_INVOCATION_RE.search(command):
        return []
    targets = list(_OPEN_CALL_RE.findall(command))
    targets.extend(_PATH_WRITE_TEXT_RE.findall(command))
    return targets


def _python_suspicious_mechanism_targets(command: str, protected_paths: set, cwd: Path) -> list:
    """Broaden python-write detection (REM-FIX, doubt-verify cycle 1) to
    also flag `os.system(`, `subprocess.run/call/Popen/check_call(`,
    `shutil.copy/copyfile/move(`, and `os.rename/replace(` as suspicious
    constructs -- including via `import X as Y` / `from X import Y`
    aliasing (cycle 2; see `_python_suspicious_call_bindings()`). Unlike
    `_python_script_write_targets()`, these mechanisms don't have a single
    reliable "target argument" position to extract -- the write may be
    embedded in a shell string (`os.system`), an argv list
    (`subprocess.run([...])`), or a two-argument call whose destination
    position varies (`shutil.copy(src, dest)`, `os.rename(src, dest)`).

    Deliberately fail-closed instead: when a suspicious-mechanism marker
    (literal or alias-bound) and a protected path's literal spelling BOTH
    appear within the SAME python statement, it is treated as a violation,
    matching this codebase's own established pattern of failing closed on
    dynamic/unresolvable content elsewhere (see
    `command_has_traversal_or_wildcard()` in hooklib).

    REM-FIX (doubt-verify cycle 2, Problem 1): this used to match a marker
    ANYWHERE in the whole command text combined with a protected-path
    literal ANYWHERE in the whole command text, with no requirement that
    they were related -- live-verified false positive:
    `subprocess.run(['ls']); print('<protected-path> is a cool file')`
    denied a command that never actually writes anywhere, just because a
    harmless call and an unrelated string both happened to appear in the
    same command. Tightened to require the marker and the protected-path
    literal to co-occur within the SAME statement (split on `;`/newline,
    respecting basic string-literal boundaries via
    `_split_statement_like_chunks()`) -- over-flagging a construct that
    merely MENTIONS a protected path's spelling in an unrelated statement
    is no longer treated as a violation, closing the false-positive gap
    while keeping the fail-closed posture for genuine same-statement
    co-occurrence.

    See the LIMITATIONS note on `_python_script_write_targets()` above --
    this function does not attempt, and does not claim, to be exhaustive
    either. Dynamically-dispatched calls (`getattr(os, 'sys'+'tem')(...)`),
    string-concatenated method names, exec()/eval()-wrapped code, ctypes,
    and ftplib are a disclosed, accepted gap, not covered here. ALSO
    disclosed and explicitly out of scope (cycle 2): storing a function
    reference in an arbitrary variable and calling it later
    (`func = os.system; func(...)`) -- tracing that binding through
    reassignment requires real AST analysis, not regex/statement-proximity
    matching, and is not attempted here."""
    if not _PYTHON_INVOCATION_RE.search(command):
        return []
    code_text = _extract_python_code_text(command)
    alias_call_patterns = _python_suspicious_call_bindings(code_text)
    if not _PYTHON_SUSPICIOUS_MECHANISM_RE.search(code_text) and not alias_call_patterns:
        return []

    path_spellings = []
    for candidate in protected_paths:
        abs_spelling = str(candidate)
        try:
            rel_spelling = str(candidate.relative_to(cwd))
        except ValueError:
            rel_spelling = None
        path_spellings.append((abs_spelling, rel_spelling))

    hits: set = set()
    for statement in _split_statement_like_chunks(code_text):
        has_marker = bool(_PYTHON_SUSPICIOUS_MECHANISM_RE.search(statement)) or any(
            pattern in statement for pattern in alias_call_patterns
        )
        if not has_marker:
            continue
        for abs_spelling, rel_spelling in path_spellings:
            if abs_spelling in statement or (rel_spelling and rel_spelling in statement):
                hits.add(abs_spelling)
    return list(hits)


def _handle_edit_write(data: dict, mode: dict, tool_input: dict) -> int:
    file_path = tool_input.get("file_path")
    if not file_path:
        return 0

    path = Path(file_path).resolve()
    violations = []

    protected_memory = _protected_memory_paths()
    if path in protected_memory:
        violations.append("memory-write")

    # Worktree confinement (Task 4.2 step 3): an independent violation type
    # that applies to every Edit/Write target, denied regardless of
    # protected-memory-path status. Any internal parsing exception here
    # skips only this newly-added check (Behavior Contract rule 9), leaving
    # the pre-existing memory-write check unaffected.
    try:
        if _edit_write_escapes_confinement(data, path):
            violations.append("worktree-confinement")
    except Exception as exc:
        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "resolve_confinement",
                "error": repr(exc),
                "reason": "skipped_worktree_confinement_check",
            },
        )

    if not violations:
        return 0

    # HIGH 3 (REM-FIX): this call is structurally identical to the one
    # inside _edit_write_escapes_confinement above, but that one is wrapped
    # in try/except by ITS caller -- this one previously was not.
    # latest_workflow_payload() can raise (e.g. FileNotFoundError on a
    # stat-race, workflow JSON deleted between glob() and .stat()), which
    # would crash main() before the deny below is ever emitted. Degrade
    # wf_uuid to None on failure -- the deny must still fire; only the
    # logged wf_uuid metadata degrades (Behavior Contract rule 9).
    try:
        workflow = latest_workflow_payload()
    except Exception as exc:
        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "latest_workflow_payload",
                "error": repr(exc),
                "reason": "skipped_wf_uuid_lookup",
            },
        )
        workflow = {}
    wf_uuid = workflow.get("workflow_uuid") or workflow.get("workflow_id")

    # Worktree-confinement is denied unconditionally -- it is an independent
    # violation type (Behavior Contract rule 7), never lifted by the
    # memory-finalize permit or gated by memoryWrites mode.
    if "worktree-confinement" in violations:
        log_event(
            "plugin_pretooluse_guard",
            {
                "wf": wf_uuid,
                "phase": workflow.get("pending_gate") or "unknown",
                "task_id": None,
                "agent": "router",
                "tool_name": data.get("tool_name"),
                "path": str(path),
                "event": "pretool_guard",
                "decision": "deny",
                "reason": ",".join(violations),
            },
        )
        pretool_deny(
            "CRAFTFLOW plugin hook blocked an Edit/Write target outside the "
            "session's confined cwd/worktree (reason: worktree-confinement). "
            "If this is intentional, run it manually outside the agent session."
        )
        return 0

    # Router-owned memory finalization: permit token lifts the block for the
    # active workflow so the router can write memory files inline.
    if "memory-write" in violations and has_memory_finalize_permit(wf_uuid):
        log_event(
            "plugin_pretooluse_guard",
            {
                "wf": wf_uuid,
                "phase": workflow.get("pending_gate") or "memory-finalize",
                "task_id": None,
                "agent": "router",
                "tool_name": data.get("tool_name"),
                "path": str(path),
                "event": "pretool_guard",
                "decision": "permit",
                "reason": "memory-write-permitted-by-finalize-token",
            },
        )
        return 0

    log_event(
        "plugin_pretooluse_guard",
        {
            "wf": wf_uuid,
            "phase": workflow.get("pending_gate") or "unknown",
            "task_id": None,
            "agent": "router",
            "tool_name": data.get("tool_name"),
            "path": str(path),
            "event": "pretool_guard",
            "decision": (
                "deny"
                if "memory-write" in violations and mode.get("memoryWrites") == "block"
                else "audit"
            ),
            "reason": ",".join(violations),
        },
    )

    should_block = "memory-write" in violations and mode.get("memoryWrites") == "block"
    if should_block:
        pretool_deny(
            "CRAFTFLOW plugin hook blocked a direct state memory markdown write. Use the router-owned memory finalization path."
        )
    return 0


def _handle_bash(data: dict, mode: dict, tool_input: dict) -> int:
    command = tool_input.get("command")
    if not command or not isinstance(command, str):
        return 0

    cwd_raw = data.get("cwd")
    if not cwd_raw:
        return 0
    cwd = Path(cwd_raw).resolve()

    try:
        worktree_path = latest_workflow_payload().get("worktree_path")
    except Exception:
        worktree_path = None
    if worktree_path is not None and not isinstance(worktree_path, str):
        worktree_path = None

    protected_paths = _protected_bash_write_paths()
    try:
        permit_path = memory_finalize_permit_path().resolve()
    except Exception:
        permit_path = None

    # Per-subcommand protected-path write detection (Task 4.2 step 3):
    # iterated per-subcommand (not the flattened extract_redirect_targets()
    # helper) so each subcommand's own token list stays intact for
    # matches_memory_finalize_permit_shape()'s shape-matching. Denied
    # unconditionally for every protected path EXCEPT the one documented
    # permit-write shape to `.memory-finalize`. Shipped unconditionally in
    # this phase (not yet gated by `protectedWrites` -- Phase 5 wires that
    # toggle without altering this decision's shape).
    protected_write_violations: list = []
    try:
        for tokens in split_subcommands(command):
            for target in _bash_write_targets_in_tokens(tokens):
                _confined, resolved = resolve_confinement(target, cwd, worktree_path)
                if resolved not in protected_paths:
                    continue
                if permit_path is not None and resolved == permit_path:
                    # CRITICAL 1 (REM-FIX): pass the literal documented
                    # constant, never the extracted `target` -- passing
                    # `target` back here made the shape-match's `target ==
                    # permit_path_str` condition tautologically true for
                    # ANY spelling that resolves to the permit file.
                    if matches_memory_finalize_permit_shape(tokens, MEMORY_FINALIZE_PERMIT_LITERAL):
                        continue
                protected_write_violations.append(str(resolved))

        # CRITICAL 2 / HIGH 1 / HIGH 2 (REM-FIX): python-script write
        # detection against the WHOLE raw command text -- catches
        # heredoc/stdin-fed scripts and env/sudo-prefixed invocations that
        # the per-subcommand, `-c`-gated, tokens[0]-only scan above could
        # never see. Never permit-shape-matched: a python write is never
        # the documented printf permit shape.
        for target in _python_script_write_targets(command):
            _confined, resolved = resolve_confinement(target, cwd, worktree_path)
            if resolved not in protected_paths:
                continue
            protected_write_violations.append(str(resolved))

        # REM-FIX (doubt-verify cycle 1): broadened python write-mechanism
        # detection -- os.system/subprocess.*/shutil.*/os.rename(replace)
        # all bypassed the open()/Path().write_text()-only checks above.
        # See _python_suspicious_mechanism_targets()'s own docstring for the
        # disclosed, deliberately non-exhaustive scope of this check.
        protected_write_violations.extend(
            _python_suspicious_mechanism_targets(command, protected_paths, cwd)
        )
    except Exception as exc:
        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "bash_write_protected_path_check",
                "error": repr(exc),
                "reason": "skipped_bash_write_protected_path_check",
            },
        )
        protected_write_violations = []

    # Worktree confinement against redirect/tee targets, scoped to protected
    # paths only (mirrors Phase 3's bash_guard.py scoping): ordinary benign
    # redirects (`> /dev/null`, `2>/dev/null`) are common, legitimate shell
    # idioms and must never be denied just because they resolve outside
    # {cwd} u {worktree_path} -- a redirect target that is not a protected
    # path is left alone entirely, regardless of where it resolves.
    confinement_violations: list = []
    try:
        for target in extract_redirect_targets(command):
            _confined, resolved = resolve_confinement(target, cwd, worktree_path)
            if resolved not in protected_paths:
                continue
            if not _confined:
                confinement_violations.append(str(resolved))
    except Exception as exc:
        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "bash_write_confinement_check",
                "error": repr(exc),
                "reason": "skipped_bash_write_confinement_check",
            },
        )
        confinement_violations = []

    if not protected_write_violations and not confinement_violations:
        return 0

    reason_parts = []
    if protected_write_violations:
        reason_parts.append(f"bash-write-protected-path:{','.join(protected_write_violations)}")
    if confinement_violations:
        reason_parts.append(f"worktree-confinement:{','.join(confinement_violations)}")
    reason = "; ".join(reason_parts)

    log_event(
        "plugin_pretooluse_guard",
        {
            "event": "pretool_guard",
            "tool_name": "Bash",
            "cwd": str(cwd),
            "command": command,
            "decision": "deny",
            "reason": reason,
        },
    )
    pretool_deny(
        f"CRAFTFLOW plugin hook blocked a Bash write to a protected path (reason: {reason}). "
        "If this is intentional, run it manually outside the agent session."
    )
    return 0


def main() -> int:
    data = load_input()
    mode = load_mode()
    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input") or {}

    if tool_name == "Bash":
        return _handle_bash(data, mode, tool_input)
    return _handle_edit_write(data, mode, tool_input)


if __name__ == "__main__":
    raise SystemExit(main())
