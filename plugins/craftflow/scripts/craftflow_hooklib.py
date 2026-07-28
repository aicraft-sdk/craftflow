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
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}


def load_mode() -> Dict[str, str]:
    path = plugin_config_dir() / "hook-mode.json"
    if not path.exists():
        return {
            "protectedWrites": "audit",
            "memoryWrites": "audit",
            "taskMetadata": "audit",
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"CRAFTFLOW load_mode: failed to read {path}: {exc}; defaulting to audit mode", file=sys.stderr)
        return {
            "protectedWrites": "audit",
            "memoryWrites": "audit",
            "taskMetadata": "audit",
        }


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
    path, cwd: Path, worktree_path: str | None
) -> tuple[bool, Path]:
    """Return (is_confined, resolved_path). Confined if resolved_path == cwd,
    is a descendant of cwd, or (when worktree_path is set) is cwd/worktree_path
    itself or a descendant of worktree_path."""
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
    return False, resolved


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


def command_has_traversal_or_wildcard(command: str) -> bool:
    """True if a `..` path-traversal literal or a `*`/bare `.` wildcard
    token appears anywhere in the command text OR inside any $(...) /
    backtick substitution it contains. Text-level heuristic only --
    guards see static command strings, never shell-expanded values."""
    extra = " ".join(m.group(1) or m.group(2) for m in _SUBSTITUTION_RE.finditer(command))
    combined = f"{command} {extra}"
    for tokens in split_subcommands(combined):
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
