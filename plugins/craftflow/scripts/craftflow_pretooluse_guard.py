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
    (HIGH 1), not closed by this plan."""
    if not _PYTHON_INVOCATION_RE.search(command):
        return []
    targets = list(_OPEN_CALL_RE.findall(command))
    targets.extend(_PATH_WRITE_TEXT_RE.findall(command))
    return targets


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
