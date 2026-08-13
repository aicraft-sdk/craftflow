#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

STATE_VERSION = "v10"


def project_dir() -> Path:
    value = os.environ.get("CLAUDE_PROJECT_DIR")
    if value:
        return Path(value)
    return Path.cwd()


def plugin_root() -> Path:
    value = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if value:
        return Path(value)
    return Path(__file__).resolve().parents[1]


def plugin_config_dir() -> Path:
    return plugin_root() / "config"


def state_root() -> Path:
    path = project_dir() / ".craftflow" / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def workflows_dir() -> Path:
    path = state_root() / "workflows"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_state_dir() -> Path:
    """Long-lived cross-workflow state: .craftflow/state/project/"""
    path = state_root() / "project"
    path.mkdir(parents=True, exist_ok=True)
    return path


def workflow_state_dir(workflow_id: str) -> Path:
    """Per-workflow isolated state: .craftflow/state/workflows/<wf-id>/"""
    path = workflows_dir() / workflow_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = state_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_input() -> Dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    # REM-FIX cycle 4 (silent-failure-hunter, live-reproduced CRITICAL): json.loads()
    # only guarantees valid JSON was parsed -- NOT that the top level is a dict (the same
    # Bug A pattern already fixed for latest_workflow_payload() in earlier cycles). Every
    # caller of load_input() immediately does `data.get(...)`, which crashes with an
    # uncaught AttributeError on a non-dict top level (e.g. `[1, 2, 3]`), before either
    # guard script's main() -- which has no top-level try/except -- can emit any deny
    # decision. Coerce to {}, matching this function's own existing missing-stdin default.
    return parsed if isinstance(parsed, dict) else {}


def load_mode() -> Dict[str, str]:
    path = plugin_config_dir() / "hook-mode.json"
    if not path.exists():
        # REM-FIX (HIGH): protectedWrites is a NEW protection closing a gap --
        # it must fail closed (block), not silently reopen the gap it exists
        # to close, when config is missing.
        return {
            "protectedWrites": "block",
            "memoryWrites": "audit",
            "taskMetadata": "audit",
        }
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"CRAFTFLOW load_mode: failed to read {path}: {exc}; defaulting to fail-closed mode", file=sys.stderr)
        # REM-FIX (HIGH): same fail-closed rationale as the missing-file
        # branch above -- a corrupt/unreadable config must not silently
        # default protectedWrites back to audit-only.
        return {
            "protectedWrites": "block",
            "memoryWrites": "audit",
            "taskMetadata": "audit",
        }
    # REM-FIX cycle 4 (silent-failure-hunter, live-reproduced CRITICAL): identical
    # dict-coercion gap as load_input() above -- a hook-mode.json that is valid JSON but
    # not a dict (e.g. `[1, 2, 3]`) crashed the first `mode.get(...)` call downstream.
    # Every caller already passes an explicit default to `.get(key, default)` that
    # matches this function's own fallback dicts above, so coercing to {} here (rather
    # than duplicating the fail-closed dict) reproduces identical caller-observed
    # behavior without disturbing the missing-file/corrupt-file branches above.
    return parsed if isinstance(parsed, dict) else {}


def resolve_toggle_decision(
    toggle_value, fail_closed_on_unrecognized: bool = False
) -> Tuple[bool, str]:
    """Enum-validate a `block`/`audit` hook-mode.json toggle value and
    compute both the gating decision (`should_block`) and the log_event
    `decision` string. Shared by every guard-script toggle that reads a
    `block`/`audit` enum from hook-mode.json (`memoryWrites`,
    `protectedWrites` in craftflow_pretooluse_guard.py;
    `bashDestructiveTraversal` in craftflow_pretooluse_bash_guard.py) --
    gating BEHAVIOR stays entirely per-caller (this helper never inspects
    the underlying violations, only the toggle value itself), only the
    enum-validation + decision-string logic is shared.

    Recognized values ("block"/"audit") always map to `should_block`
    True/False and decision "deny"/"audit".

    An unrecognized value (a typo, e.g. "Block" capital-B, "blocked", a
    boolean) must never be silently indistinguishable in
    craftflow-hook-events.log from an intentional, recognized choice.
    `fail_closed_on_unrecognized` selects which posture an unrecognized
    value degrades to:

    - False (default; `memoryWrites`/`protectedWrites`): degrades to
      `should_block=False` (fail OPEN -- same gating outcome as an
      intentional "audit" choice) but logs a DISTINCT
      "audit-unrecognized-config-value" decision so the misconfiguration
      is still greppable.
    - True (`bashDestructiveTraversal`): degrades to `should_block=True`
      (fail CLOSED) with a DISTINCT "block-unrecognized-config-value"
      decision. This is a deliberate DIVERGENCE from the
      memoryWrites/protectedWrites default, not a copy of it:
      `bashDestructiveTraversal`'s own missing-key behavior already
      fail-closes (`mode.get("bashDestructiveTraversal", "block")` --
      the default when the key is absent entirely is "block", not
      "audit"). An unrecognized-but-PRESENT value (a typo) should fail
      exactly the same way a missing key does -- both are "the config
      didn't give us a clear answer" -- rather than silently flipping to
      the OPPOSITE, more permissive posture ("audit-degrade") just
      because a key happened to be present with a bad value. Consistency
      is with the toggle's OWN established missing-key default, not with
      the other toggles' unrelated unrecognized-value default.
    """
    should_block = toggle_value == "block"
    if toggle_value in ("block", "audit"):
        decision = "deny" if should_block else "audit"
    elif fail_closed_on_unrecognized:
        should_block = True
        decision = "block-unrecognized-config-value"
    else:
        decision = "audit-unrecognized-config-value"
    return should_block, decision


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(name: str, payload: Dict[str, Any]) -> None:
    try:
        path = logs_dir() / "craftflow-hook-events.log"
        event = {
            "ts": now_iso(),
            "event": name,
            "state_version": STATE_VERSION,
            **payload,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=True) + "\n")
    except Exception:
        pass  # never fail the hook


def latest_workflow_payload() -> Dict[str, Any]:
    payload, _, _ = read_latest_workflow_state()
    return payload


def latest_workflow_file() -> Path | None:
    files = sorted(
        workflows_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not files:
        return None
    return files[0]


def read_latest_workflow_state() -> Tuple[Dict[str, Any], Path | None, str | None]:
    latest = latest_workflow_file()
    if latest is None:
        return {}, None, None
    try:
        return json.loads(latest.read_text(encoding="utf-8")), latest, None
    except Exception as exc:
        return {}, latest, exc.__class__.__name__


def workflow_artifact_path(workflow_id: str | None) -> Path | None:
    if not workflow_id:
        return None
    path = workflows_dir() / f"{workflow_id}.json"
    if not path.exists():
        return None
    return path


def workflow_event_log_path(workflow_id: str | None) -> Path | None:
    if not workflow_id:
        return None
    path = workflows_dir() / f"{workflow_id}.events.jsonl"
    if not path.exists():
        return None
    return path


def read_workflow_state(
    workflow_id: str | None,
) -> Tuple[Dict[str, Any], Path | None, str | None]:
    path = workflow_artifact_path(workflow_id)
    if path is None:
        return {}, None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), path, None
    except Exception as exc:
        return {}, path, exc.__class__.__name__


def workflow_event_log_contains(workflow_id: str | None, needle: str) -> bool:
    path = workflow_event_log_path(workflow_id)
    if path is None:
        return False
    try:
        return needle in path.read_text(encoding="utf-8")
    except Exception:
        return False


def workflow_event_log_exists(payload: Dict[str, Any], artifact_path: Path) -> bool:
    workflow_uuid = payload.get("workflow_uuid") or payload.get("workflow_id")
    if not workflow_uuid:
        workflow_uuid = artifact_path.stem
    event_log = workflows_dir() / f"{workflow_uuid}.events.jsonl"
    return event_log.exists()


def workflow_artifact_is_fresh(path: Path, max_age_seconds: int = 60) -> bool:
    try:
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age <= max_age_seconds


def parse_metadata(description: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in description.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in {"wf", "kind", "origin", "phase", "plan", "scope", "reason"}:
            values[key] = value.strip()
    return values


def json_print(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True))


def pretool_deny(reason: str) -> None:
    json_print(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )


def session_context(message: str) -> None:
    json_print(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": message,
            }
        }
    )


def parse_markdown_sections(text: str) -> Dict[str, str]:
    """Split markdown on ``## `` lines into {section_name: content_below}."""
    sections: Dict[str, str] = {}
    current: str | None = None
    lines: List[str] = []
    for raw_line in text.splitlines():
        if raw_line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines)
            current = raw_line[3:].strip()
            lines = []
        else:
            lines.append(raw_line)
    if current is not None:
        sections[current] = "\n".join(lines)
    return sections


def extract_bullets(section_content: str) -> List[str]:
    """Return all ``- `` prefixed lines from a section body."""
    return [
        line for line in section_content.splitlines() if line.lstrip().startswith("- ")
    ]


def normalize_bullet(line: str) -> str:
    """Strip ``- `` prefix, collapse whitespace, and lowercase for dedup."""
    text = line.lstrip()
    if text.startswith("- "):
        text = text[2:]
    return re.sub(r"\s+", " ", text).strip().lower()


# ---------------------------------------------------------------------------
# Memory finalization permit
# ---------------------------------------------------------------------------
# The pretooluse guard blocks direct writes to protected memory .md files.
# During router-owned memory finalization the router creates this permit so
# the guard can distinguish its own legitimate writes from unauthorized ones.

def memory_finalize_permit_path() -> Path:
    """Non-protected sentinel: .craftflow/state/.memory-finalize"""
    return state_root() / ".memory-finalize"


def set_memory_finalize_permit(workflow_uuid: str) -> None:
    """Write the permit token so the pretooluse guard allows memory writes."""
    try:
        memory_finalize_permit_path().write_text(workflow_uuid, encoding="utf-8")
    except Exception:
        pass


def clear_memory_finalize_permit() -> None:
    """Remove the permit token after memory finalization is done."""
    try:
        memory_finalize_permit_path().unlink(missing_ok=True)
    except Exception:
        pass


def has_memory_finalize_permit(workflow_uuid: str | None = None) -> bool:
    """Return True if a valid permit exists for the given workflow UUID.

    When workflow_uuid is None, presence of the file alone is accepted.
    """
    permit = memory_finalize_permit_path()
    if not permit.exists():
        return False
    if workflow_uuid is None:
        return True
    try:
        return permit.read_text(encoding="utf-8").strip() == workflow_uuid
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Confinement & command-parsing helpers
# ---------------------------------------------------------------------------
# Shared by craftflow_pretooluse_bash_guard.py (Bash-tool targets) and
# craftflow_pretooluse_guard.py (Edit/Write + Bash-write-inspection targets).
# See docs/plans/2026-07-28-craftflow-guardrail-hardening-plan.md for the
# behavior contract these functions implement.

CONTROL_OPERATORS = {";", "&&", "||", "|", "&", "\n"}


def resolve_confinement(
    path,
    cwd: Path,
    worktree_path: str | None,
    extra_exact_paths: "frozenset[Path] | None" = None,
) -> tuple[bool, Path]:
    """Return (is_confined, resolved_path). Confined if resolved_path == cwd,
    is a descendant of cwd, (when worktree_path is set) is cwd/worktree_path
    itself or a descendant of worktree_path, OR (when extra_exact_paths is
    set) resolved_path is an EXACT member of extra_exact_paths.

    `extra_exact_paths` (workspace-root file allowlist, see
    docs/plans/2026-08-12-craftflow-workspace-root-allowlist-design.md) is
    matched by EXACT EQUALITY ONLY -- deliberately never descendant/prefix
    matching, unlike the cwd/worktree_path branches above. This is the
    single invariant that keeps the mechanism from ever becoming a
    directory/subtree grant into a sibling nested repo (the design's
    rejected "Option B"). Members must already be resolved, absolute
    Path objects -- this function does not re-resolve or validate them
    (mirrors how worktree_path is already caller-resolved before being
    passed in). Omitting this parameter, or passing None/an empty
    collection, reproduces the pre-existing 3-parameter behavior exactly
    (regression-tested)."""
    candidate = Path(os.path.expanduser(str(path)))
    if not candidate.is_absolute():
        candidate = cwd / candidate
    resolved = candidate.resolve()
    within_cwd = resolved == cwd or cwd in resolved.parents
    if within_cwd:
        return True, resolved
    if worktree_path:
        wt = Path(worktree_path).resolve()
        within_wt = resolved == wt or wt in resolved.parents
        if within_wt:
            return True, resolved
    if extra_exact_paths and resolved in extra_exact_paths:
        return True, resolved
    return False, resolved


def resolve_workspace_writable_paths(workflow: Dict[str, Any]) -> "frozenset[Path]":
    """Coerce workflow.get("workspace_writable_paths") into a frozenset of
    resolved absolute Path objects, for resolve_confinement()'s
    extra_exact_paths parameter. Never raises -- a missing key, a
    non-list value, or a non-string/empty-string list member all degrade
    to omission (never a crash, never a wildcard grant) -- Behavior
    Contract: fail toward the stricter, pre-existing deny-by-default
    posture, never fail open.

    The router (craftflow-router/SKILL.md `## 0.` step 1a, via
    craftflow_resolve_workspace_root.py's read_workspace_writable_paths())
    is the SOLE writer of this field and already performs the full
    semantic validation (direct-workspace-root-child, not inside a
    nested repo) before ever writing it. This helper is a defensive
    second layer doing only structural coercion -- it does not re-run
    that semantic validation, exactly the same trust relationship this
    guard already has with worktree_path (also unvalidated at this
    layer)."""
    raw = workflow.get("workspace_writable_paths")
    if not isinstance(raw, list):
        return frozenset()
    resolved: set = set()
    for entry in raw:
        if not isinstance(entry, str) or not entry:
            continue
        try:
            resolved.add(Path(entry).resolve())
        except (OSError, RuntimeError, ValueError):
            continue
    return frozenset(resolved)


def split_subcommands(command: str) -> list:
    """Split a shell command string on control operators (;, &&, ||, |, &).

    Best-effort tokenization (not a full shell parser) -- intentionally
    blunt, matching the rest of this guard family's deterministic-but-
    imperfect scope.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []

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


def is_env_assignment(token: str) -> bool:
    if "=" not in token:
        return False
    name = token.split("=", 1)[0]
    return name.isidentifier()


def looks_dynamic(token: str) -> bool:
    return "$" in token or "`" in token


_SUBSTITUTION_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")

# Extglob pattern-list group prefix characters (`!(...)`, `@(...)`,
# `+(...)`, `*(...)`, `?(...)`) -- bash pattern-list groups enabled only
# under `shopt -s extglob`, but once enabled (a real, if less common, live
# shell setting) `!(pattern-list)` in particular matches EVERYTHING except
# the given pattern-list, an unbounded/directory-dependent expansion this
# guard has no way to enumerate ahead of time (unlike a `{a,b}` brace
# group, whose alternatives are fully spelled out in the command text
# itself -- see craftflow_pretooluse_bash_guard.py's brace-expansion
# handling). `split_subcommands()`'s shlex tokenizer (punctuation_chars=
# True) splits a literal "(" into its OWN token, so `!(scratch)` never
# appears as one token containing "!(...)" verbatim -- it tokenizes as
# [..., "./!", "(", "scratch", ")"]. This existence check only needs to
# answer "is an extglob group plausibly present in this subcommand,"
# mirroring the bare `*`/`..` check just above it -- the fuller span
# handling (finding the matching close paren so the whole group is
# excluded from concrete path targets) lives in
# craftflow_pretooluse_bash_guard.py's `_positional_targets()`, which
# has access to `_scan_paren_depth()`.
_EXTGLOB_PREFIX_CHARS = ("!", "@", "?", "+", "*")


def _tokens_contain_extglob_span(tokens: list) -> bool:
    for idx, token in enumerate(tokens):
        if token and token[-1] in _EXTGLOB_PREFIX_CHARS and idx + 1 < len(tokens) and tokens[idx + 1] == "(":
            return True
    return False


# REM-FIX (composition-boundary bypass, follow-up to the brace/extglob
# hardening above): `split_subcommands()`'s shlex tokenizer never splits on
# `{`/`}`/`,`, so an INTACT single-level-or-nested brace-expansion group
# (`{a,.git}`, `{a,{b,.git}}`) always tokenizes as exactly ONE token no
# matter how deeply nested. The ONLY way a `{`/`}` pair can end up
# IMBALANCED within one token is a DIFFERENT mechanism fragmenting it first
# -- concretely, a `$(...)`/backtick command substitution nested inside the
# brace group forces shlex's `punctuation_chars=True` to split the `(`/`)`
# into their own separate tokens (e.g. `./{$(echo a),.git}` tokenizes as
# `["./{$", "(", "echo", "a", ")", ",.git}"]`), fragmenting the enclosing
# brace syntax across tokens before brace-detection ever sees one clean
# component. A token with more `{` than `}` characters is exactly this
# fragmented-span signature -- mirrors `_tokens_contain_extglob_span()`
# above (an existence check only; the fuller span-consuming logic lives in
# craftflow_pretooluse_bash_guard.py's `_positional_targets()`, which
# already has `_scan_paren_depth()`-style tools available for finding where
# the fragmented span actually closes).
def _tokens_contain_unbalanced_brace(tokens: list) -> bool:
    for token in tokens:
        if token.count("{") > token.count("}"):
            return True
    return False


def command_has_traversal_or_wildcard(command: str) -> bool:
    """True if a `..` path-traversal literal or a `*`/bare `.` wildcard
    token appears anywhere in the command text OR inside any $(...) /
    backtick substitution it contains -- OR an extglob pattern-list group
    (`!(...)`/`@(...)`/`+(...)`/`*(...)`/`?(...)`) is plausibly present
    (MEDIUM, live-confirmed bypass: `rm -rf ./!(scratch)` expands to
    everything except `scratch`, including `.git`/`packages`/`tools`) -- OR
    a command-substitution-fragmented brace-expansion span is plausibly
    present (REM-FIX, composition-boundary bypass: `rm -rf
    ./{$(echo a),.git}` has a real, live-confirmed destructive alternative
    but a dynamic `$(...)` target ALONE, with no accompanying traversal/
    wildcard signal elsewhere in the command, is normally left ALLOWED by
    this guard's dynamic-target-fail-closed-only-with-corroboration design;
    a fragmented brace span is itself exactly that corroborating signal,
    the same way a bare extglob span already is above). Text-level
    heuristic only -- guards see static command strings, never
    shell-expanded values."""
    extra = " ".join(m.group(1) or m.group(2) for m in _SUBSTITUTION_RE.finditer(command))
    combined = f"{command} {extra}"
    for tokens in split_subcommands(combined):
        if _tokens_contain_extglob_span(tokens):
            return True
        if _tokens_contain_unbalanced_brace(tokens):
            return True
        for token in tokens:
            stripped = token.strip("'\"")
            if stripped in ("..", "*", "."):
                return True
            if "/../" in stripped or stripped.startswith("../") or stripped.endswith("/.."):
                return True
            if "*" in stripped:
                return True
    return False


def extract_redirect_targets(command: str) -> list:
    """Best-effort: return file targets of >, >>, and `tee` invocations
    across every subcommand. Heuristic only, matching this guard
    family's documented deterministic-but-imperfect scope."""
    targets = []
    for tokens in split_subcommands(command):
        for idx, token in enumerate(tokens):
            if token in (">", ">>") and idx + 1 < len(tokens):
                targets.append(tokens[idx + 1])
            elif token == "tee":
                for t in tokens[idx + 1:]:
                    if not t.startswith("-"):
                        targets.append(t)
    return targets


# The one documented literal spelling of the permit sentinel path. Callers
# of matches_memory_finalize_permit_shape() below MUST pass this constant
# (never a target string extracted from the command being checked) as the
# `permit_path_str` argument -- passing the extracted target back at the
# call site makes the function's 4th AND-condition (`target ==
# permit_path_str`) tautologically true for ANY spelling that resolves to
# the same file (e.g. an absolute-path spelling), silently defeating the
# intended literal-spelling narrowing (CRITICAL 1, guardrail-hardening
# REM-FIX -- live-verified in both craftflow_pretooluse_guard.py and
# craftflow_pretooluse_bash_guard.py).
MEMORY_FINALIZE_PERMIT_LITERAL = ".craftflow/state/.memory-finalize"


def matches_memory_finalize_permit_shape(subcommand_tokens: list, permit_path_str: str) -> bool:
    """Narrow, exact TOKEN-SHAPE match for the ONE documented permit-write
    shape: printf '%s' '<value>' > .craftflow/state/.memory-finalize

    Deliberately token-based, not a raw-text regex: split_subcommands()
    (posix shlex) already strips quotes, so the original quoted substring
    is not recoverable from a tokenized subcommand -- matching on the
    post-tokenization shape is both correct and simpler than trying to
    re-derive original text spans. Any other shape must be denied by the
    caller. Exact expected shape once tokenized:
    ["printf", "%s", "<any-single-value-token>", ">", "<permit-path>"]

    `permit_path_str` MUST be the literal documented constant
    (MEMORY_FINALIZE_PERMIT_LITERAL above), not a target string derived
    from the command under test -- see the constant's own docstring for
    why (CRITICAL 1).
    """
    if len(subcommand_tokens) != 5:
        return False
    cmd, fmt, _value, redirect, target = subcommand_tokens
    if looks_dynamic(_value):
        return False
    return (
        cmd == "printf"
        and fmt == "%s"
        and redirect == ">"
        and target == permit_path_str
    )
