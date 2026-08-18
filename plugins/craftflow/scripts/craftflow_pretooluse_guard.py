#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import craftflow_pretooluse_bash_guard as bash_guard  # noqa: E402

# Defensive import (incident 2026-08-10/11): a partial/interrupted plugin-cache
# sync can land this script's new top-level import ahead of the sibling module
# file actually being copied -- a bare `import craftflow_skill_ledger` then
# hard-crashes EVERY PreToolUse:Bash/Edit/Write hook invocation in every
# project sharing the cache (a Python ModuleNotFoundError aborts the whole
# process before main() runs), not just the skill-ledger/skill-promotion
# checks below. Every call site that dereferences `skill_ledger`/
# `skill_promote` already sits behind a broad `except Exception` that
# degrades to "not protected" (see `_inflight_skill_promotion_paths`,
# `_is_protected_skill_promotion_path`, `_is_protected_skill_ledger_or_
# proposal_path`) -- an AttributeError from a None module is caught the same
# way an unreadable ledger already is. Guarding just the import completes
# that existing fail-open posture instead of letting the one ungated line
# crash checks (memory-write, worktree-confinement, bash-traversal) that have
# nothing to do with skill-ledger at all.
try:
    import craftflow_skill_ledger as skill_ledger  # noqa: E402
except ImportError:
    skill_ledger = None
try:
    import craftflow_skill_promote as skill_promote  # noqa: E402
except ImportError:
    skill_promote = None

from craftflow_hooklib import (
    DENIAL_ESCALATION_THRESHOLD,
    MEMORY_FINALIZE_PERMIT_LITERAL,
    clear_denial,
    extract_redirect_targets,
    has_memory_finalize_permit,
    latest_live_workflow_payload,
    load_input,
    load_mode,
    log_event,
    looks_dynamic,
    matches_memory_finalize_permit_shape,
    memory_finalize_permit_path,
    plugin_root,
    pretool_deny,
    project_dir,
    project_state_dir,
    record_denial,
    resolve_confinement,
    resolve_workspace_writable_paths,
    resolve_toggle_decision,
    split_subcommands,
    state_root,
    workflows_dir,
)

if skill_ledger is None or skill_promote is None:
    log_event(
        "plugin_pretooluse_guard",
        {
            "event": "plugin_module_missing",
            "missing": [
                name
                for name, mod in (
                    ("craftflow_skill_ledger", skill_ledger),
                    ("craftflow_skill_promote", skill_promote),
                )
                if mod is None
            ],
            "decision": "degraded_skill_protections_disabled",
            "reason": "sibling_module_not_present_in_deployed_cache",
        },
    )


PROTECTED_MEMORY_FILES = ("activeContext.md", "patterns.md", "progress.md")

RELIABILITY_GATES_LEDGER_REL_PATH = ".craftflow/state/project/reliability-gates.json"

# rtk-inspired state-read compaction (PreToolUse deny+redirect on Read, not a
# rewrite -- Claude Code's own PostToolUse contract cannot retroactively
# shrink content already in context, and this repo's prior on-disk-masking
# approach (craftflow_memory_protect_pre.py, reverted for "defeats Memory
# First") is deliberately NOT reused here: this check never mutates the
# target file's bytes, it only denies the Read and names the redirect
# command.
READ_COMPACTION_THRESHOLD_BYTES = 50_000

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


def _is_state_read_compaction_candidate(path: Path) -> bool:
    """True if `path` is under `.craftflow/state/**` and is not the
    memory-finalize permit sentinel (too small to matter, excluded for
    clarity, matching how the permit path is always excluded explicitly
    elsewhere in this module)."""
    try:
        path.relative_to(state_root())
    except (OSError, ValueError):
        return False
    try:
        if path == memory_finalize_permit_path().resolve():
            return False
    except Exception:
        pass
    return True


def check_state_read_compaction(path: Path) -> bool:
    """True if this Read target should be denied and redirected to
    craftflow_state_query.py: under .craftflow/state/**, not the permit
    sentinel, and strictly over READ_COMPACTION_THRESHOLD_BYTES. Any error
    reading the target's size (missing file, permission error) degrades to
    False -- fail-open, non-security posture, matching this check's own
    Error Handling design (never block a normal, small, or non-existent
    Read)."""
    if not _is_state_read_compaction_candidate(path):
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return size > READ_COMPACTION_THRESHOLD_BYTES


def _handle_read(data: dict, mode: dict, tool_input: dict) -> int:
    file_path = tool_input.get("file_path")
    if not file_path:
        return 0

    path = Path(file_path).resolve()
    try:
        should_redirect = check_state_read_compaction(path)
    except Exception as exc:
        # Fail-open by design (non-security posture): any unexpected error
        # here must never block a normal Read. Discoverability is via
        # log-grep, not a counter/escalation mechanism -- the distinct
        # `reason: "skipped_state_read_compaction_check"` key below is
        # intended to be the grep/alert target for this path.
        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "check_state_read_compaction",
                "error": repr(exc),
                "reason": "skipped_state_read_compaction_check",
            },
        )
        return 0

    if not should_redirect:
        return 0

    should_block, decision = resolve_toggle_decision(mode.get("stateReadCompaction", "block"))

    log_event(
        "plugin_pretooluse_guard",
        {
            "event": "pretool_guard",
            "tool_name": "Read",
            "path": str(path),
            "decision": decision if should_block else "audit",
            "reason": "state-read-compaction",
        },
    )

    if not should_block:
        return 0

    script = plugin_root() / "scripts" / "craftflow_state_query.py"
    pretool_deny(
        "CRAFTFLOW plugin hook redirected an oversized Read of a "
        f".craftflow/state file (over {READ_COMPACTION_THRESHOLD_BYTES} bytes). "
        f'Run: python3 "{script}" "{path}" --mode summary '
        "(or --mode full for the complete, byte-identical content) instead of Read."
    )
    return 0


# HIGH 5 (REM-FIX, skill-distillation Phase 2 remediation): a skill can only
# ever be legitimately promoted by running craftflow_skill_promote.py's
# `--approve` path (the script's own internal Python file I/O -- os.replace()/
# open() -- which this hook never intercepts, since PreToolUse only sees the
# Bash tool call INVOKING the script, not the writes the script performs on
# its own once running). No raw Edit/Write/Bash-redirect tool call should ever
# be able to reach `.claude/skills/<name>/SKILL.md` or
# `.cursor/skills/<name>/SKILL.md` directly -- skill-author.md's "never write
# to .claude/skills or .cursor/skills directly" constraint was previously
# prompt-only, unlike the PROTECTED_MEMORY_FILES pattern above.
#
# CRITICAL 1 (REM-FIX round 2): the original implementation matched by SHAPE
# alone (any `.claude/skills/<name>/SKILL.md` or `.cursor/skills/<name>/
# SKILL.md`), project-root-wide, with no override. That path shape is the
# STANDARD Claude Code/Cursor project-skill convention, not craftflow-
# exclusive -- an ordinary hand-authored skill (e.g. via the built-in
# `skill-development` skill) was silently blocked in every project this
# plugin is active in. Narrowed to protect ONLY a skill `<name>` that is
# ACTIVELY IN-FLIGHT in the skill-distillation pipeline right now: a ledger
# candidate (`.craftflow/state/project/skill-candidates.json`, see
# `craftflow_skill_ledger.py`) with `status` `"candidate"` or `"proposed"`
# that additionally has a matching staged proposal
# (`.craftflow/state/project/skill-proposals/<id>/SKILL.md` or
# `SKILL.patch`) naming this exact `<name>` -- the only name<->path linkage
# that exists before promotion (the ledger carries no `name` field itself;
# `promoted_skill` is set only AFTER promotion). A brand-new skill name with
# no existing file yet is still protected as soon as its proposal is staged
# (the shape match is still evaluated by construction of the path set below,
# not by globbing existing files) -- but an unrelated name with no in-flight
# ledger+proposal pairing, or no ledger file at all (the common case), is
# NOT protected here and must be allowed to proceed normally.
#
# MEDIUM (REM-FIX round 3): "proposed" is reserved for a not-yet-implemented
# producer -- no code path in this codebase currently writes that status
# onto a ledger candidate (only "candidate", "rejected", and "promoted" are
# ever set by craftflow_skill_ledger.py). Kept here so this check is already
# correct the day a producer starts emitting "proposed", instead of needing
# a second REM-FIX to add it.
_INFLIGHT_LEDGER_STATUSES = ("candidate", "proposed")


def _load_ledger_safe(ledger_path: Path) -> tuple:
    """Load the skill-candidate ledger's raw JSON, distinguishing "file does
    not exist" (benign -- returns an empty ledger, ledger_corrupt=False) from
    "file exists but failed to parse, or has an invalid top-level schema
    shape" (security-relevant -- CRITICAL 2, REM-FIX round 3: returns an
    empty ledger, ledger_corrupt=True).

    `skill_ledger.load_ledger()` itself cannot be reused for this
    distinction: it catches `(OSError, ValueError)` on malformed JSON (and
    also degrades on a wrong top-level shape) and silently returns an empty
    ledger either way -- exactly the ambiguity this function exists to
    remove, since the caller needs to fail CLOSED on corruption but stay
    fail-OPEN (business as usual) on a simply-absent ledger file (the common
    case for most projects, and the one round 2 explicitly protected).

    Live-reproduced bypass this closes: truncate `skill-candidates.json` to
    invalid JSON while a legitimate in-flight candidate + staged proposal
    exist -- before this fix, `_inflight_skill_promotion_paths()` silently
    protected zero candidates (indistinguishable from "no ledger file"), so
    a direct Write to that candidate's SKILL.md was ALLOWED with nothing
    logged to `craftflow-hook-events.log`."""
    if not ledger_path.exists():
        return {"schema_version": skill_ledger.SCHEMA_VERSION, "candidates": []}, False
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"schema_version": skill_ledger.SCHEMA_VERSION, "candidates": []}, True
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        return {"schema_version": skill_ledger.SCHEMA_VERSION, "candidates": []}, True
    return data, False


def _inflight_skill_promotion_paths(root: Path) -> tuple:
    """Resolve the set of canonical `.claude/skills/<name>/SKILL.md` and
    `.cursor/skills/<name>/SKILL.md` paths for every skill `<name>` that is
    ACTIVELY IN-FLIGHT in the skill-distillation pipeline right now (see the
    CRITICAL 1 comment block above). Returns `(paths, ledger_corrupt)`.

    CRITICAL 2 (REM-FIX round 3): `ledger_corrupt` is True ONLY when the
    ledger file EXISTS but its content could not be trusted (parse failure
    or invalid top-level shape, per `_load_ledger_safe()` above) -- `paths`
    is always empty in that case, since no candidate in an unparseable file
    can be trusted either. The caller (`_is_protected_skill_promotion_path`)
    uses this flag to fail CLOSED instead of silently treating "corrupt
    ledger" the same as "no ledger file at all." A missing ledger file (the
    common case for most projects) still yields `(set(), False)` -- business
    as usual, matching round 2's fix. Any OTHER error reading the proposals
    directory, or a single candidate's own proposal file/frontmatter,
    degrades to skipping that one candidate only -- never a hard failure,
    never treated as ledger_corrupt -- unchanged from the pre-existing
    behavior.

    CRITICAL (REM-FIX round 4): a structurally malformed PER-CANDIDATE entry
    (missing its own `"status"` or `"id"` key) is ALSO treated as
    `ledger_corrupt=True` (empty `paths`, distinguishable
    `skill_ledger_candidate_malformed` log event) rather than silently
    skipped the same way a legitimately-terminal (`"rejected"`/`"promoted"`)
    entry is -- see the in-loop comment below for why treating those two
    cases identically was itself a silent, unlogged under-protection bug."""
    paths: set = set()
    try:
        ledger_path = root / skill_ledger.DEFAULT_LEDGER_PATH
        ledger, ledger_corrupt = _load_ledger_safe(ledger_path)
    except Exception:
        return paths, False
    if ledger_corrupt:
        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "skill_ledger_unreadable",
                "ledger_path": str(ledger_path),
                "reason": "ledger_exists_but_failed_to_parse_or_invalid_shape",
                "decision": "fail_closed_skill_promotion_path",
            },
        )
        return paths, True

    candidates = ledger.get("candidates") if isinstance(ledger, dict) else None
    if not isinstance(candidates, list):
        return paths, False

    proposals_dir = root / skill_promote.DEFAULT_PROPOSALS_DIR
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        # CRITICAL (REM-FIX round 4, hunter's 2nd finding last round): a
        # structurally malformed per-candidate entry -- missing its own
        # "status" or "id" key entirely -- was previously treated IDENTICALLY
        # to "legitimately not in-flight" (e.g. a real "rejected"/"promoted"
        # entry): `candidate.get("status") not in _INFLIGHT_LEDGER_STATUSES`
        # is True for a missing key exactly the same as for a terminal
        # status, so the loop silently `continue`d with ZERO log line either
        # way. That is indistinguishable from "nothing to see here" in
        # `craftflow-hook-events.log`, even though a malformed entry means
        # this function cannot actually prove anything about whether that
        # candidate is in-flight or not. Fail closed the same way whole-file
        # corruption already does above -- empty path set, ledger_corrupt
        # True, a distinguishable logged event -- rather than silently
        # under-protecting.
        if "status" not in candidate or "id" not in candidate:
            log_event(
                "plugin_pretooluse_guard",
                {
                    "event": "skill_ledger_candidate_malformed",
                    "ledger_path": str(ledger_path),
                    "candidate_surface": candidate.get("surface"),
                    "reason": "candidate_entry_missing_status_or_id",
                    "decision": "fail_closed_skill_promotion_path",
                },
            )
            return set(), True

        if candidate.get("status") not in _INFLIGHT_LEDGER_STATUSES:
            continue
        cid = candidate.get("id")
        if not isinstance(cid, str) or not cid:
            continue

        name = None
        try:
            proposal_dir = proposals_dir / cid
            md_path = proposal_dir / "SKILL.md"
            patch_path = proposal_dir / "SKILL.patch"
            if md_path.is_file():
                fm = skill_promote.parse_frontmatter(md_path.read_text(encoding="utf-8"))
                candidate_name = fm.get("name") if isinstance(fm, dict) else None
                if isinstance(candidate_name, str) and candidate_name.strip():
                    name = candidate_name.strip()
            elif patch_path.is_file():
                target_rel = skill_promote._extract_patch_target(
                    patch_path.read_text(encoding="utf-8")
                )
                target_match = skill_promote._SKILL_TARGET_RE.match(target_rel) if target_rel else None
                if target_match:
                    name = target_match.group(2)
        except (OSError, ValueError):
            continue

        if not name:
            continue
        for family in (".claude", ".cursor"):
            try:
                paths.add((root / family / "skills" / name / "SKILL.md").resolve())
            except Exception:
                continue
    return paths, False


def _skill_promotion_path_shape_match(root: Path, path: Path) -> bool:
    """Shape-only match (no ledger lookup at all) for
    `<root>/.claude/skills/<name>/SKILL.md` or
    `<root>/.cursor/skills/<name>/SKILL.md`. Used ONLY as the CRITICAL 2
    (REM-FIX round 3) fail-closed fallback inside
    `_is_protected_skill_promotion_path()` below, when the ledger cannot be
    trusted -- NOT used on the normal (ledger-readable) path, where
    protection stays narrowed to actually in-flight candidates (round 2's
    fix, and its own regression test
    `test_pretooluse_guard_allows_unrelated_hand_authored_skill_write_no_ledger`,
    remain intact for the common case)."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    parts = rel.parts
    return (
        len(parts) == 4
        and parts[0] in (".claude", ".cursor")
        and parts[1] == "skills"
        and parts[3] == "SKILL.md"
    )


def _is_protected_skill_promotion_path(path: Path) -> bool:
    """True if `path` (already resolved to an absolute path) is exactly
    `<project-root>/.claude/skills/<name>/SKILL.md` or
    `<project-root>/.cursor/skills/<name>/SKILL.md` for a skill `<name>`
    that is ACTIVELY IN-FLIGHT in the skill-distillation pipeline right now
    (CRITICAL 1, REM-FIX round 2 -- see `_inflight_skill_promotion_paths()`).
    Any error resolving `path` relative to the project root (e.g. the two
    are on different drives on some platform) degrades to False -- this is
    an ADDITIONAL protection layered on top of the pre-existing
    memory-write/confinement checks, never a reason to skip those.

    CRITICAL 2 (REM-FIX round 3): when the ledger EXISTS but cannot be
    trusted (unparseable JSON or invalid shape --
    `_inflight_skill_promotion_paths()`'s `ledger_corrupt` flag), this
    degrades to a SHAPE-only match instead of silently allowing (the
    pre-fix bug): the guard cannot prove the write is safe, so it fails
    CLOSED for any path matching the general skill-promotion-path shape --
    not just previously-known in-flight candidates, since a corrupt ledger
    means "previously known" cannot be trusted either. This is a rare,
    operator-actionable scenario (a corrupted ledger file), not the common
    "no ledger"/"unrelated hand-authored skill" case round 2 fixed for, so
    failing closed here does not reintroduce round 2's over-broad-blocking
    regression."""
    try:
        root = project_dir().resolve()
        path.relative_to(root)
    except (OSError, ValueError):
        return False
    try:
        inflight, ledger_corrupt = _inflight_skill_promotion_paths(root)
    except Exception:
        return False
    if ledger_corrupt:
        return _skill_promotion_path_shape_match(root, path)
    return path in inflight


def _protected_skill_ledger_and_proposal_paths(root: Path) -> tuple:
    """Resolved `(ledger_path, proposals_dir)` for the skill-candidate
    ledger and its staged-proposal directory (CRITICAL 1, REM-FIX round 3)
    -- the untrusted data source `_is_protected_skill_promotion_path()`
    above reads on every invocation to decide which SKILL.md paths are
    currently protected."""
    ledger_path = (root / skill_ledger.DEFAULT_LEDGER_PATH).resolve()
    proposals_dir = (root / skill_promote.DEFAULT_PROPOSALS_DIR).resolve()
    return ledger_path, proposals_dir


def _is_protected_skill_ledger_or_proposal_path(path: Path) -> bool:
    """True if `path` is exactly the skill-candidate ledger file
    (`.craftflow/state/project/skill-candidates.json`) or ANY path under its
    staged-proposals directory (`.craftflow/state/project/skill-proposals/`)
    -- existing or not (REM-FIX round 4, architectural fix).

    Live-reproduced tamper sequence this closes (originally CRITICAL 1,
    REM-FIX round 3): stage an in-flight candidate, confirm a `Write` to its
    `.claude/skills/<name>/SKILL.md` is denied -- then `Write` the ledger file
    itself (replacing `candidates` with `[]`), which was previously ALLOWED
    (not itself protected) -- then retry the SAME write to the SKILL.md,
    previously now ALLOWED too, since `_is_protected_skill_promotion_path()`
    re-reads the (now-tampered) ledger on every call and sees zero in-flight
    candidates.

    Why the round-3 fix's own `path.exists()` scoping for the proposals
    directory was itself a bypass (found across three subsequent REM-FIX
    rounds): `candidate_id()` is a deterministic `sha1(surface+signature)`
    hash, precomputable OFFLINE with no observation needed, and the ledger is
    freely readable via `--query`. Scoping protection to "already exists on
    disk" let an attacker precompute a future legitimate candidate's exact
    id and plant a file there FIRST, before the real candidate ever reaches
    that id -- the round-3 fix's own "protect existing files, allow first
    writes" trade-off was exploitable purely from public, offline
    information. No amount of narrowing the existence check (mtime windows,
    ledger-status cross-checks, etc.) closes this: the hash is deterministic
    and precomputable regardless of timing.

    The architectural fix (user-approved, round 4): `skill-author` no longer
    writes proposal files directly via `Write` at all. It drafts content to a
    scratch location, then invokes `craftflow_skill_propose.py` (the sole
    authorized writer of this tree, mirroring `craftflow_skill_promote.py`'s
    own sole-authorized-writer status for `.claude/skills/`) to atomically
    stage it, gated on the candidate's ledger status. Since there is no
    longer a legitimate direct-`Write`-to-this-tree caller to protect
    "first writes" for, the entire tree can be protected UNCONDITIONALLY --
    exactly like the ledger file itself already is -- removing both the
    existence check and its precompute-squat exposure entirely. This is
    SIMPLER than the round-3 guard, not more complex: the complexity moved
    into `craftflow_skill_propose.py`'s validated, atomic, locked write path
    instead of living in the guard's trust-inference logic.

    Denied by both callers below (mirrors worktree-confinement/
    skill-promotion-path's own unconditional treatment in
    `_handle_edit_write`/`_handle_bash` -- never lifted by
    memoryWrites/protectedWrites gating), since this data source backs a
    security decision, not routine project state. Degrades to False on any
    path-resolution error (matches this module's established fail-open-on-
    resolution-error posture -- distinct from the ledger-CONTENT corruption
    handled separately by CRITICAL 2 above)."""
    try:
        root = project_dir().resolve()
        ledger_path, proposals_dir = _protected_skill_ledger_and_proposal_paths(root)
    except Exception:
        return False
    if path == ledger_path:
        return True
    try:
        path.relative_to(proposals_dir)
    except ValueError:
        return False
    return True


def _protected_reliability_gates_path(root: Path) -> Path:
    """Resolved path for the reliability-gates ledger -- protected the same
    unconditional way as the skill-candidate ledger (CRITICAL 1, REM-FIX
    round 3 precedent), since it is a single script-owned JSON file, not a
    markdown memory file eligible for the memory-finalize permit."""
    return (root / RELIABILITY_GATES_LEDGER_REL_PATH).resolve()


def _is_protected_reliability_gates_path(path: Path) -> bool:
    try:
        root = project_dir().resolve()
        return path == _protected_reliability_gates_path(root)
    except Exception:
        return False


def _denial_escalation_suffix(count: int) -> str:
    """Item B fix (consecutive-denial hard stop). Appended to a deny
    message once `count` reaches `DENIAL_ESCALATION_THRESHOLD` for the same
    (session_id, resolved target) logical write action -- see
    craftflow_hooklib.py's own module docstring for the granularity
    choice.

    Wording (doubt-verify cycle 1, defends-with-advisory finding, REM-FIX
    cycle 2): the previous wording ("HARD STOP ... do not retry via a
    different tool") implied a mechanical enforcement this guard cannot
    actually provide -- a stateless PreToolUse hook can only allow/deny
    the ONE tool call it was invoked for; it has no ability to block a
    calling agent from attempting yet another tool or surface. This
    wording is honest about that: it is a strong, unambiguous signal
    (escalated message + distinct `deny-escalated` log decision) asking
    the agent to stop and involve the user, not a claim of literal
    process-level or structural prevention."""
    return (
        f" ESCALATED: this is the {count}th consecutive denial on this exact "
        "target within this session. This strongly suggests you are "
        "attempting to work around a legitimate restriction via a "
        "different tool or surface. STOP and ask the user before "
        "proceeding by any other means -- do not attempt an equivalent "
        "write through Bash, a different tool, or a workaround."
    )


def _record_multi_target_denial(session_id: str | None, targets: list) -> Tuple[int, bool]:
    """Bash-path variant of `record_denial()` (Item B fix): a single Bash
    command's deny decision can involve MULTIPLE distinct violating target
    paths across several violation categories at once. Records one denial
    against every unique target and escalates if ANY of them has now
    reached the threshold -- returns (max_count_seen, escalated)."""
    max_count = 0
    escalated = False
    for target in sorted(set(t for t in targets if t)):
        count, esc = record_denial(session_id, target)
        max_count = max(max_count, count)
        escalated = escalated or esc
    return max_count, escalated


def _clear_multi_target_denial(session_id: str | None, targets: list) -> None:
    for target in sorted(set(t for t in targets if t)):
        clear_denial(session_id, target)


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
    """True if the resolved Edit/Write target escapes
    {cwd} u {worktree_path} u {workspace_writable_paths}. Absence of "cwd"
    in the payload, or no active workflow JSON / a null worktree_path / an
    empty workspace_writable_paths, degrades to allow (Behavior Contract
    rule 8) -- this only returns True when cwd IS known and the target
    genuinely escapes all three."""
    cwd_raw = data.get("cwd")
    if not cwd_raw:
        return False
    cwd = Path(cwd_raw).resolve()
    # Item A fix: session_id-scoped, liveness-filtered selection (see
    # latest_live_workflow_file()'s own docstring) -- the PreToolUse hook
    # payload's own "session_id" field is threaded through so a matching,
    # still-live workflow is preferred over an unrelated session's stale
    # artifact. This is a write-confinement-sensitive call site (Finding 1,
    # REM-FIX cycle 1) so it uses the *_live_* variant, not the plain
    # newest-by-mtime latest_workflow_payload().
    workflow = latest_live_workflow_payload(data.get("session_id"))
    worktree_path = workflow.get("worktree_path")
    if worktree_path is not None and not isinstance(worktree_path, str):
        worktree_path = None
    workspace_writable_paths = resolve_workspace_writable_paths(workflow)
    confined, _resolved = resolve_confinement(path, cwd, worktree_path, workspace_writable_paths)
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


# SYSTEMIC GAP (REM-FIX round 5): `_bash_write_targets_in_tokens()` above
# (and `extract_redirect_targets()`/the python-write detectors it sits
# alongside) recognize ONLY `>`/`>>`/`tee` redirect syntax and python-internal
# write APIs -- ZERO detection existed for ordinary shell file-copy/move/link
# commands using plain positional arguments. Live-reproduced bypass (all 3
# silently ALLOWED, zero `log_event` call, before this fix):
#   cp /tmp/evil.md .craftflow/state/project/skill-proposals/<id>/SKILL.md
#   cp /tmp/forged.json .craftflow/state/project/skill-candidates.json
#   mv /tmp/staged.md .craftflow/state/project/skill-proposals/<id>/SKILL.md
# Reuses `craftflow_pretooluse_bash_guard.py`'s own `_split_command_name()` /
# `_positional_targets()` flag-parsing (built for a DIFFERENT purpose --
# cwd-escape detection on rm/mv/shred/truncate -- but the exact same
# flag/`--`/`--target-directory=`/extglob/brace-aware token-skipping shape
# this needs) rather than re-implementing that parsing a second time.
_CP_MV_LIKE_COMMANDS = ("cp", "mv", "ln", "install", "rsync")


def _cp_mv_like_write_targets(tokens: list) -> list:
    """Detect the destination-argument write target(s) of an ordinary
    cp/mv/ln/install/rsync invocation. These commands have no `>`/`>>`/`tee`
    redirect syntax at all, yet still WRITE to their final positional
    argument (or `-t DIR`/`--target-directory=DIR`) exactly like a redirect
    would.

    When `--target-directory=DIR`/`-tDIR` is present, every OTHER positional
    token is a source copied/moved/linked INTO that directory -- the actual
    write target for each source is `DIR/basename(source)`. Otherwise, the
    LAST positional token is the destination (the standard `cmd SRC... DEST`
    shape). A single leftover positional token (e.g. a bare `ln -s target`
    with no link name given) is ambiguous/incomplete and yields no
    destination -- matching this module's fail-open-on-unresolvable-shape
    posture elsewhere, not a reason to guess."""
    command_name, rest = bash_guard._split_command_name(tokens)
    if command_name not in _CP_MV_LIKE_COMMANDS:
        return []
    paths, _unresolvable = bash_guard._positional_targets(rest)
    if not paths:
        return []

    target_dir = None
    for token in rest:
        if token.startswith("-") and token != "-":
            match = (
                bash_guard._TARGET_DIRECTORY_RE.match(token)
                or bash_guard._TARGET_DIRECTORY_SHORT_RE.match(token)
            )
            if match and not looks_dynamic(match.group(1)):
                target_dir = match.group(1)
                break

    if target_dir is not None:
        return [
            str(Path(target_dir) / Path(source).name)
            for source in paths
            if source != target_dir
        ]
    if len(paths) < 2:
        return []
    return [paths[-1]]


def _dd_write_targets(tokens: list) -> list:
    """dd's overwrite target is the key=value `of=<path>` argument, not a
    positional token -- reuses bash_guard's own `_dd_target()` (already
    built for dd's traversal-escape check) rather than re-implementing the
    same `of=` parsing a second time."""
    command_name, _rest = bash_guard._split_command_name(tokens)
    if command_name != "dd":
        return []
    paths, _unresolvable = bash_guard._dd_target(tokens)
    return paths


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


# Per-violation-type deny explanations for `_handle_edit_write`'s
# unconditional-violation block (misleading-deny-message fix): the four
# unconditional violation types are independent and semantically unrelated
# -- a worktree-confinement escape has nothing to do with skill promotion,
# and a reliability-gates ledger write has nothing to do with either. Each
# type gets its own accurate explanatory text; when multiple violations fire
# together for the same path (rare but structurally possible, e.g. a target
# that is BOTH outside the worktree AND a skill-promotion path), the
# matching texts are concatenated in `unconditional_violations` order so no
# information is lost.
_UNCONDITIONAL_VIOLATION_EXPLANATIONS = {
    "worktree-confinement": (
        "This Edit/Write target is outside both the current session's "
        "working directory and the active workflow's worktree_path; if "
        "this is otherwise intentional, run it manually outside the agent "
        "session."
    ),
    "skill-promotion-path": (
        "Skill promotion must go through craftflow_skill_promote.py "
        "--approve and skill proposals must be staged through "
        "craftflow_skill_propose.py, never a raw Edit/Write."
    ),
    "skill-ledger-write": (
        "The skill-candidate ledger and its staged proposals must never be "
        "modified directly either."
    ),
    "reliability-gates-write": (
        "The reliability-gates ledger is a script-owned JSON file, not "
        "skill-related -- direct edits aren't allowed; use the "
        "reliability-gates script tooling, or do it manually outside the "
        "agent session."
    ),
}


def _handle_edit_write(data: dict, mode: dict, tool_input: dict) -> int:
    file_path = tool_input.get("file_path")
    if not file_path:
        return 0

    path = Path(file_path).resolve()
    violations = []

    protected_memory = _protected_memory_paths()
    if path in protected_memory:
        violations.append("memory-write")

    # HIGH 5 (REM-FIX): skill promotion must never happen via a raw Edit/Write
    # tool call -- the sole authorized writer of a promoted skill's SKILL.md
    # is craftflow_skill_promote.py's own internal file I/O. Independent
    # violation type, always denied (see the unconditional-deny block below),
    # never lifted by the memory-finalize permit or gated by memoryWrites.
    if _is_protected_skill_promotion_path(path):
        violations.append("skill-promotion-path")

    # CRITICAL 1 (REM-FIX round 3): the skill-candidate ledger and its
    # staged proposals are the untrusted data source the skill-promotion-path
    # check above trusts -- protect them the same unconditional way, or the
    # ledger itself becomes the tamper vector (see
    # `_is_protected_skill_ledger_or_proposal_path()`'s own docstring).
    if _is_protected_skill_ledger_or_proposal_path(path):
        violations.append("skill-ledger-write")

    # Phase 4 (reliability-gates ledger protection): the reliability-gates
    # ledger is a single script-owned JSON file, not a markdown memory file
    # eligible for the memory-finalize permit -- protected unconditionally,
    # mirroring the skill-candidate ledger's own treatment above.
    if _is_protected_reliability_gates_path(path):
        violations.append("reliability-gates-write")

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
        # Item B fix: an allowed write to this exact target resets any
        # consecutive-denial escalation on it (the "successful equivalent
        # write" reset condition).
        clear_denial(data.get("session_id"), str(path))
        return 0

    # HIGH 3 (REM-FIX): this call is structurally identical to the one
    # inside _edit_write_escapes_confinement above, but that one is wrapped
    # in try/except by ITS caller -- this one previously was not.
    # latest_live_workflow_payload() can raise (e.g. FileNotFoundError on a
    # stat-race, workflow JSON deleted between glob() and .stat()), which
    # would crash main() before the deny below is ever emitted. Degrade
    # wf_uuid to None on failure -- the deny must still fire; only the
    # logged wf_uuid metadata degrades (Behavior Contract rule 9).
    #
    # REM-FIX cycle 3 (live-reproduced CRITICAL): latest_live_workflow_payload() only
    # guarantees valid JSON was parsed -- NOT that the top level is a dict (same Bug A
    # pattern already fixed in _handle_bash). wf_uuid/pending_gate MUST be derived inside
    # this SAME try/except, not after it, so a non-dict top level (e.g. `[1,2,3]`) degrades
    # gracefully instead of raising an uncaught AttributeError out of _handle_edit_write
    # (and the whole guard process, since main() has no top-level try/except) before the
    # deny below is ever emitted. Write-confinement-sensitive call site (Finding 1,
    # REM-FIX cycle 1) -- uses the *_live_* variant.
    try:
        workflow = latest_live_workflow_payload(data.get("session_id"))
        wf_uuid = workflow.get("workflow_uuid") or workflow.get("workflow_id")
        pending_gate = workflow.get("pending_gate")
    except Exception as exc:
        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "latest_live_workflow_payload",
                "error": repr(exc),
                "reason": "skipped_wf_uuid_lookup",
            },
        )
        workflow = {}
        wf_uuid = None
        pending_gate = None

    # Worktree-confinement, skill-promotion-path, and skill-ledger-write are
    # all denied unconditionally -- independent violation types (Behavior
    # Contract rule 7; HIGH 5 REM-FIX extends the same unconditional
    # treatment to skill-promotion-path; CRITICAL 1 REM-FIX round 3 extends
    # it again to skill-ledger-write), never lifted by the memory-finalize
    # permit or gated by memoryWrites mode.
    unconditional_violations = [
        v for v in violations
        if v in ("worktree-confinement", "skill-promotion-path", "skill-ledger-write", "reliability-gates-write")
    ]
    if unconditional_violations:
        # Item B fix: record this denial against the (session, target)
        # logical action BEFORE logging, so an escalated 2nd+ consecutive
        # denial is reflected in both the log decision and the deny
        # message itself.
        session_id = data.get("session_id")
        denial_count, denial_escalated = record_denial(session_id, str(path))
        log_event(
            "plugin_pretooluse_guard",
            {
                "wf": wf_uuid,
                "phase": pending_gate or "unknown",
                "task_id": None,
                "agent": "router",
                "tool_name": data.get("tool_name"),
                "path": str(path),
                "event": "pretool_guard",
                "decision": "deny-escalated" if denial_escalated else "deny",
                "reason": ",".join(violations),
            },
        )
        explanation = " ".join(
            _UNCONDITIONAL_VIOLATION_EXPLANATIONS[v]
            for v in unconditional_violations
            if v in _UNCONDITIONAL_VIOLATION_EXPLANATIONS
        )
        message = (
            "CRAFTFLOW plugin hook blocked an Edit/Write target (reason: "
            + ",".join(unconditional_violations) + "). " + explanation
        )
        if denial_escalated:
            message += _denial_escalation_suffix(denial_count)
        pretool_deny(message)
        return 0

    # Router-owned memory finalization: permit token lifts the block for the
    # active workflow so the router can write memory files inline.
    if "memory-write" in violations and has_memory_finalize_permit(wf_uuid):
        # Item B fix: the finalize permit means this write IS proceeding --
        # treat it as an allow for escalation-reset purposes too.
        clear_denial(data.get("session_id"), str(path))
        log_event(
            "plugin_pretooluse_guard",
            {
                "wf": wf_uuid,
                "phase": pending_gate or "memory-finalize",
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

    # REM-FIX: reuses the shared `resolve_toggle_decision()` helper (moved to
    # craftflow_hooklib.py so both this script and
    # craftflow_pretooluse_bash_guard.py's `bashDestructiveTraversal` toggle
    # share one implementation -- see its own docstring) that the sibling
    # `protectedWrites` toggle uses -- extends enum-validation + a
    # distinguishing "audit-unrecognized-config-value" log decision to
    # `memoryWrites` too. Gating behavior is unchanged: a typo'd value still
    # degrades to audit/allow (default `fail_closed_on_unrecognized=False`),
    # only the logged `decision` differs. `should_block_raw`/`decision` are
    # computed unconditionally from the toggle value alone; `should_block`
    # still additionally requires "memory-write" in violations (mirrors the
    # pre-existing `and` condition -- at this point in the function it is
    # always true, since "worktree-confinement" already returned above and
    # "memory-write" is the only remaining violation type, but the explicit
    # check is kept for defensive clarity).
    # REM-FIX (doubt-verify cycle 3): `mode.get("memoryWrites")` had no
    # default -- a hook-mode.json that is valid JSON but simply omits this
    # key returned None, which resolve_toggle_decision() treats as
    # "unrecognized." The default here is "audit" (not "block"), matching
    # this long-established toggle's own pre-existing default in
    # load_mode()'s fallback dict -- mirrors bashDestructiveTraversal's
    # already-correct `mode.get("bashDestructiveTraversal", "block")`
    # pattern in the sibling craftflow_pretooluse_bash_guard.py, but with
    # THIS toggle's own correct default value, not a copy of that one's.
    should_block_raw, memory_writes_decision = resolve_toggle_decision(mode.get("memoryWrites", "audit"))
    should_block = "memory-write" in violations and should_block_raw

    # Item B fix: only a genuine deny records a denial; anything else at
    # this point in the function is effectively an allow (the write
    # proceeds, e.g. an "audit" mode toggle) and resets any prior
    # escalation on this exact target.
    session_id = data.get("session_id")
    denial_count = 0
    denial_escalated = False
    if should_block:
        denial_count, denial_escalated = record_denial(session_id, str(path))
    else:
        clear_denial(session_id, str(path))

    log_event(
        "plugin_pretooluse_guard",
        {
            "wf": wf_uuid,
            "phase": pending_gate or "unknown",
            "task_id": None,
            "agent": "router",
            "tool_name": data.get("tool_name"),
            "path": str(path),
            "event": "pretool_guard",
            "decision": (
                "deny-escalated" if denial_escalated
                else (memory_writes_decision if "memory-write" in violations else "audit")
            ),
            "reason": ",".join(violations),
        },
    )

    if should_block:
        message = "CRAFTFLOW plugin hook blocked a direct state memory markdown write. Use the router-owned memory finalization path."
        if denial_escalated:
            message += _denial_escalation_suffix(denial_count)
        pretool_deny(message)
    return 0


# Bash-path overrides for `_UNCONDITIONAL_VIOLATION_EXPLANATIONS` (misleading-
# deny-message fix, fix-verify cycle 1): `_handle_bash`'s catch-all deny
# block has the exact same "one hardcoded skill-promotion paragraph for
# every violation type" defect the Edit/Write handler had -- fixed the same
# way, composing only the text for violation types that actually fired.
# Reused verbatim from `_UNCONDITIONAL_VIOLATION_EXPLANATIONS` where the
# wording is tool-agnostic (skill-ledger-write, reliability-gates-write);
# overridden here only where the Bash-path wording genuinely differs (a
# Bash redirect/tee/heredoc is not "a raw Edit/Write"), plus one Bash-only
# entry for `bash-write-protected-path` (the gated-elsewhere protected-write
# check, which can still co-fire in this block alongside an unconditional
# violation on the same command).
_BASH_ONLY_VIOLATION_EXPLANATIONS = {
    "worktree-confinement": (
        "This Bash write/redirect target is outside both the current "
        "session's working directory and the active workflow's "
        "worktree_path; if this is otherwise intentional, run it manually "
        "outside the agent session."
    ),
    "skill-promotion-path": (
        "Skill promotion must go through craftflow_skill_promote.py "
        "--approve and skill proposals must be staged through "
        "craftflow_skill_propose.py, never a Bash redirect."
    ),
    "bash-write-protected-path": (
        "Other protected-path writes must be run manually outside the "
        "agent session if intentional."
    ),
}


def _handle_bash(data: dict, mode: dict, tool_input: dict) -> int:
    command = tool_input.get("command")
    if not command or not isinstance(command, str):
        return 0

    cwd_raw = data.get("cwd")
    if not cwd_raw:
        return 0
    cwd = Path(cwd_raw).resolve()

    # REM-FIX (live-reproduced CRITICAL): latest_live_workflow_payload() only guarantees
    # valid JSON was parsed -- NOT that the top level is a dict. Wrap the derived reads in
    # the SAME try/except as the payload fetch itself so a non-dict top level (e.g.
    # `[1,2,3]`) degrades gracefully to worktree_path=None / workspace_writable_paths=
    # frozenset() instead of raising an uncaught AttributeError out of _handle_bash (and
    # the whole guard process, since main() has no top-level try/except) before any
    # protection check below ever runs. Write-confinement-sensitive call site (Finding 1,
    # REM-FIX cycle 1) -- uses the *_live_* variant.
    try:
        workflow = latest_live_workflow_payload(data.get("session_id"))
        worktree_path = workflow.get("worktree_path")
        if worktree_path is not None and not isinstance(worktree_path, str):
            worktree_path = None
        workspace_writable_paths = resolve_workspace_writable_paths(workflow)
    except Exception as exc:
        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "latest_live_workflow_payload",
                "error": repr(exc),
                "reason": "skipped_worktree_path_and_workspace_writable_paths_lookup",
            },
        )
        worktree_path = None
        workspace_writable_paths = frozenset()

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
    # NOTE: this is the ONLY resolve_confinement() call site in _handle_bash that reads the
    # returned `confined` value (via `_confined` below, despite the underscore) -- it is
    # therefore the only one threaded with `workspace_writable_paths`. The other 9 calls in this
    # function discard `confined` and only use `resolved` for unrelated protected-path/skill-
    # promotion/skill-ledger/reliability-gates membership checks -- see
    # docs/plans/2026-08-12-craftflow-workspace-root-allowlist-plan.md's Codebase Reality Check
    # (Call-Site Classification table) for the full per-call-site reasoning. Do NOT "fix" the
    # other 9 into 4-arg calls without re-reading that analysis first -- it would be a no-op
    # (extra_exact_paths only ever changes the discarded `confined` value, never `resolved`) and
    # only adds inconsistent-looking diff noise.
    confinement_violations: list = []
    try:
        for target in extract_redirect_targets(command):
            _confined, resolved = resolve_confinement(target, cwd, worktree_path, workspace_writable_paths)
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

    # HIGH 5 (REM-FIX): skill promotion must never happen via a Bash
    # redirect/tee -- the sole authorized writer of a promoted skill's
    # SKILL.md is craftflow_skill_promote.py's own internal file I/O. Scoped
    # to the same redirect/tee target extraction already used for the
    # worktree-confinement check above (a NEW independent violation type,
    # never a fixed-path glob like `_protected_bash_write_paths()`, since a
    # brand-new skill name has no existing file to glob-match yet).
    #
    # CRITICAL 2 (REM-FIX round 2): this lane used to ONLY scan
    # `extract_redirect_targets(command)` (`>`, `>>`, `tee`) -- it was NEVER
    # cross-checked against the python-write-mechanism detectors
    # (`_python_script_write_targets`, `_python_suspicious_mechanism_targets`)
    # this same function already builds and uses for memory-file protection
    # above. Live-reproduced bypass (all 3 previously a silent ALLOW, zero
    # `log_event` call): a python -c one-liner using open().write(), a
    # python -c os.system('... > path') call, and a heredoc/stdin-fed
    # script -- all now cross-checked through the SAME narrowed,
    # ledger-scoped `_is_protected_skill_promotion_path()` check (CRITICAL 1)
    # rather than a blanket path-shape match.
    skill_promotion_violations: list = []
    try:
        for target in extract_redirect_targets(command):
            _confined, resolved = resolve_confinement(target, cwd, worktree_path)
            if _is_protected_skill_promotion_path(resolved):
                skill_promotion_violations.append(str(resolved))

        # `open(...)`/`Path(...).write_text(...)` targets (`-c` one-liners
        # AND heredoc/stdin-fed scripts) -- same detector, same whole-
        # command-text scan already used for memory-file protection above.
        for target in _python_script_write_targets(command):
            _confined, resolved = resolve_confinement(target, cwd, worktree_path)
            if _is_protected_skill_promotion_path(resolved):
                skill_promotion_violations.append(str(resolved))

        # `os.system(`/`subprocess.*(`/`shutil.*(`/`os.rename|replace(` (and
        # their import-aliased forms) co-occurring, in the SAME statement,
        # with a literal spelling of an in-flight skill's SKILL.md path --
        # a shape-based literal check, mirroring exactly how the
        # memory-file protection lane above uses this same function against
        # `protected_paths`. Skill-promotion targets have no fixed existing-
        # file glob to check against (a brand-new skill name may not exist
        # on disk yet), so the "protected paths" fed in here are the
        # resolved canonical paths for every ledger-in-flight skill name
        # instead.
        inflight_skill_paths, _ledger_corrupt_unused = _inflight_skill_promotion_paths(project_dir().resolve())
        if inflight_skill_paths:
            skill_promotion_violations.extend(
                _python_suspicious_mechanism_targets(command, inflight_skill_paths, cwd)
            )
    except Exception as exc:
        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "bash_skill_promotion_path_check",
                "error": repr(exc),
                "reason": "skipped_bash_skill_promotion_path_check",
            },
        )
        skill_promotion_violations = []

    # CRITICAL 1 (REM-FIX round 3): the skill-candidate ledger and its
    # staged proposals are the untrusted data source the skill-promotion-path
    # checks above (both here and in `_handle_edit_write`) trust on every
    # invocation. Protect them unconditionally too, via a Bash redirect/tee
    # AND the same python-write detectors already built above -- otherwise
    # the ledger itself is the tamper vector (see
    # `_is_protected_skill_ledger_or_proposal_path()`'s own docstring for the
    # live-reproduced tamper-then-write sequence this closes). The
    # suspicious-mechanism (`os.system(`/`subprocess.*(`/`shutil.*(`) lane
    # is scoped to the ledger file's own literal spelling only (not every
    # path under the proposals directory, which isn't enumerable ahead of
    # time) -- a disclosed, narrow residual gap matching this module's
    # established pattern of disclosing scope limits rather than claiming
    # exhaustive coverage.
    skill_ledger_violations: list = []
    try:
        for target in extract_redirect_targets(command):
            _confined, resolved = resolve_confinement(target, cwd, worktree_path)
            if _is_protected_skill_ledger_or_proposal_path(resolved):
                skill_ledger_violations.append(str(resolved))

        for target in _python_script_write_targets(command):
            _confined, resolved = resolve_confinement(target, cwd, worktree_path)
            if _is_protected_skill_ledger_or_proposal_path(resolved):
                skill_ledger_violations.append(str(resolved))

        ledger_root = project_dir().resolve()
        ledger_path, _proposals_dir_unused = _protected_skill_ledger_and_proposal_paths(ledger_root)
        skill_ledger_violations.extend(
            _python_suspicious_mechanism_targets(command, {ledger_path}, cwd)
        )
    except Exception as exc:
        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "bash_skill_ledger_write_check",
                "error": repr(exc),
                "reason": "skipped_bash_skill_ledger_write_check",
            },
        )
        skill_ledger_violations = []

    # Phase 4 (reliability-gates ledger protection): mirrors the
    # skill_ledger_violations lane immediately above -- redirect/tee,
    # python-script-write-targets, and python-suspicious-mechanism, all
    # scoped to the single reliability-gates ledger path literal.
    reliability_gates_violations: list = []
    try:
        for target in extract_redirect_targets(command):
            _confined, resolved = resolve_confinement(target, cwd, worktree_path)
            if _is_protected_reliability_gates_path(resolved):
                reliability_gates_violations.append(str(resolved))

        for target in _python_script_write_targets(command):
            _confined, resolved = resolve_confinement(target, cwd, worktree_path)
            if _is_protected_reliability_gates_path(resolved):
                reliability_gates_violations.append(str(resolved))

        gates_root = project_dir().resolve()
        gates_path = _protected_reliability_gates_path(gates_root)
        reliability_gates_violations.extend(
            _python_suspicious_mechanism_targets(command, {gates_path}, cwd)
        )
    except Exception as exc:
        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "bash_reliability_gates_write_check",
                "error": repr(exc),
                "reason": "skipped_bash_reliability_gates_write_check",
            },
        )
        reliability_gates_violations = []

    # cp/mv/ln/install/rsync/dd destination-argument write detection
    # (REM-FIX round 5, systemic gap -- see `_cp_mv_like_write_targets()`
    # and `_dd_write_targets()` above for the live-reproduced bypass this
    # closes). Cross-checked against ALL THREE protected-path classes at
    # once in this one loop -- memory files/workflow JSON (`protected_paths`),
    # the skill-promotion path, AND the skill ledger/proposals tree -- the
    # same three lanes the redirect/tee and python-write detectors above
    # already cover, not just the newest one. `protected_write_violations`
    # entries feed the pre-existing `protectedWrites`-gated decision below
    # unchanged; `skill_promotion_violations`/`skill_ledger_violations`
    # entries stay unconditional, exactly like their redirect/python-write
    # counterparts above.
    try:
        for tokens in split_subcommands(command):
            for target in _cp_mv_like_write_targets(tokens) + _dd_write_targets(tokens):
                _confined, resolved = resolve_confinement(target, cwd, worktree_path)
                if resolved in protected_paths:
                    protected_write_violations.append(str(resolved))
                if _is_protected_skill_promotion_path(resolved):
                    skill_promotion_violations.append(str(resolved))
                if _is_protected_skill_ledger_or_proposal_path(resolved):
                    skill_ledger_violations.append(str(resolved))
                if _is_protected_reliability_gates_path(resolved):
                    reliability_gates_violations.append(str(resolved))
    except Exception as exc:
        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "pretool_guard_parse_error",
                "command_name": "bash_cp_mv_like_write_check",
                "error": repr(exc),
                "reason": "skipped_bash_cp_mv_like_write_check",
            },
        )

    if (
        not protected_write_violations
        and not confinement_violations
        and not skill_promotion_violations
        and not skill_ledger_violations
        and not reliability_gates_violations
    ):
        # Item B fix: nothing to clear here -- no violating target was
        # even detected for this command, so there is no (session, target)
        # key to reset (see clear_denial()'s docstring for the reset
        # contract; a clean command has no prior denial to have created one).
        return 0

    # Worktree-confinement, skill-promotion-path, skill-ledger-write, and
    # reliability-gates-write are all denied unconditionally -- independent
    # violation types (mirrors the Edit/Write handler's own treatment
    # above), never gated by `protectedWrites`.
    if (
        confinement_violations
        or skill_promotion_violations
        or skill_ledger_violations
        or reliability_gates_violations
    ):
        reason_parts = []
        if protected_write_violations:
            reason_parts.append(f"bash-write-protected-path:{','.join(protected_write_violations)}")
        if confinement_violations:
            reason_parts.append(f"worktree-confinement:{','.join(confinement_violations)}")
        if skill_promotion_violations:
            reason_parts.append(f"skill-promotion-path:{','.join(skill_promotion_violations)}")
        if skill_ledger_violations:
            reason_parts.append(f"skill-ledger-write:{','.join(skill_ledger_violations)}")
        if reliability_gates_violations:
            reason_parts.append(f"reliability-gates-write:{','.join(reliability_gates_violations)}")
        reason = "; ".join(reason_parts)

        # Item B fix: record this denial against every distinct violating
        # target BEFORE logging, so an escalated 2nd+ consecutive denial on
        # any of them is reflected in both the log decision and the deny
        # message below (see `_record_multi_target_denial()`'s docstring).
        denial_count, denial_escalated = _record_multi_target_denial(
            data.get("session_id"),
            protected_write_violations
            + confinement_violations
            + skill_promotion_violations
            + skill_ledger_violations
            + reliability_gates_violations,
        )

        log_event(
            "plugin_pretooluse_guard",
            {
                "event": "pretool_guard",
                "tool_name": "Bash",
                "cwd": str(cwd),
                "command": command,
                "decision": "deny-escalated" if denial_escalated else "deny",
                "reason": reason,
            },
        )
        # misleading-deny-message fix (fix-verify cycle 1): compose the
        # explanation from only the violation types that actually fired,
        # same per-type pattern as `_handle_edit_write` above -- a pure
        # worktree-confinement violation must not carry the skill-promotion
        # boilerplate, and vice versa. `protected_write_violations` is
        # included too (it can co-fire here alongside an unconditional
        # violation, even though on its own it is gated by `protectedWrites`
        # below); order mirrors `reason_parts` above.
        unconditional_violations = []
        if protected_write_violations:
            unconditional_violations.append("bash-write-protected-path")
        if confinement_violations:
            unconditional_violations.append("worktree-confinement")
        if skill_promotion_violations:
            unconditional_violations.append("skill-promotion-path")
        if skill_ledger_violations:
            unconditional_violations.append("skill-ledger-write")
        if reliability_gates_violations:
            unconditional_violations.append("reliability-gates-write")
        explanation = " ".join(
            _BASH_ONLY_VIOLATION_EXPLANATIONS.get(
                v, _UNCONDITIONAL_VIOLATION_EXPLANATIONS.get(v, "")
            )
            for v in unconditional_violations
        )
        message = (
            f"CRAFTFLOW plugin hook blocked a Bash write to a protected path (reason: {reason}). "
            + explanation
        )
        if denial_escalated:
            message += _denial_escalation_suffix(denial_count)
        pretool_deny(message)
        return 0

    # Only protected_write_violations remain at this point (Task 5.2): the
    # NEW Bash-write-protected-path decision is gated behind
    # `protectedWrites` == "block", exactly mirroring the pre-existing
    # `memoryWrites` == "block" pattern used for the Edit/Write memory-write
    # check above -- the memoryWrites-gated logic itself is untouched.
    reason = f"bash-write-protected-path:{','.join(protected_write_violations)}"
    # REM-FIX (HIGH): a typo'd/unrecognized protectedWrites value (e.g.
    # "Block", "blocked", a boolean) must never be silently indistinguishable
    # from an intentional "audit" choice in craftflow-hook-events.log. The
    # gating behavior itself is unchanged (still degrades to audit/allow) --
    # only the logged decision differs, so misconfiguration is greppable.
    # Uses the shared `resolve_toggle_decision()` helper in
    # craftflow_hooklib.py (extracted there once `memoryWrites` became a
    # second caller of this exact enum-validation + distinguishing-log-
    # decision pattern, and again once `bashDestructiveTraversal` became a
    # third caller in a different script -- see its own docstring).
    # REM-FIX (doubt-verify cycle 3): `mode.get("protectedWrites")` had no
    # default -- a hook-mode.json that is valid JSON but simply omits this
    # key returned None, which resolve_toggle_decision() treats as
    # "unrecognized," silently failing OPEN despite
    # fail_closed_on_unrecognized=False. This toggle's whole purpose is a
    # NEW fail-closed protection, so a missing key must default to "block"
    # -- mirrors bashDestructiveTraversal's already-correct
    # `mode.get("bashDestructiveTraversal", "block")` pattern in the sibling
    # craftflow_pretooluse_bash_guard.py.
    should_block, decision = resolve_toggle_decision(mode.get("protectedWrites", "block"))

    # Item B fix: only a genuine deny records a denial; an "audit"-mode
    # allow resets any prior escalation on these exact targets (the
    # "successful equivalent write" reset condition).
    session_id = data.get("session_id")
    denial_count = 0
    denial_escalated = False
    if should_block:
        denial_count, denial_escalated = _record_multi_target_denial(session_id, protected_write_violations)
    else:
        _clear_multi_target_denial(session_id, protected_write_violations)

    log_event(
        "plugin_pretooluse_guard",
        {
            "event": "pretool_guard",
            "tool_name": "Bash",
            "cwd": str(cwd),
            "command": command,
            "decision": "deny-escalated" if denial_escalated else decision,
            "reason": reason,
        },
    )
    if should_block:
        message = (
            f"CRAFTFLOW plugin hook blocked a Bash write to a protected path (reason: {reason}). "
            "If this is intentional, run it manually outside the agent session."
        )
        if denial_escalated:
            message += _denial_escalation_suffix(denial_count)
        pretool_deny(message)
    return 0


def main() -> int:
    data = load_input()
    mode = load_mode()
    tool_name = data.get("tool_name")
    # REM-FIX cycle 4 (silent-failure-hunter, live-reproduced CRITICAL): `or {}` only
    # substitutes on a FALSY value (None, [], "", 0) -- a truthy non-dict like `["a"]`
    # survives untouched and crashes the first `.get()` call inside
    # _handle_edit_write/_handle_bash. Explicit isinstance check coerces any non-dict
    # value to {}, not just falsy ones.
    raw_tool_input = data.get("tool_input")
    tool_input = raw_tool_input if isinstance(raw_tool_input, dict) else {}

    if tool_name == "Bash":
        return _handle_bash(data, mode, tool_input)
    if tool_name == "Read":
        return _handle_read(data, mode, tool_input)
    return _handle_edit_write(data, mode, tool_input)


if __name__ == "__main__":
    raise SystemExit(main())
