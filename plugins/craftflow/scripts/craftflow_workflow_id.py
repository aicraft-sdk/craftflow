#!/usr/bin/env python3
"""craftflow_workflow_id.py

Deterministic workflow-ID and worktree-name minting for Craftflow.

Replaces the router LLM's ad-hoc string construction with a single, consistent
source of truth.  Called by craftflow-router before every new workflow.

Usage:
  python3 craftflow_workflow_id.py --request "Add auth refactor" [options]

Options:
  --request TEXT     User request text (required unless --session-id is used)
  --branch  NAME     Current git branch name (auto-detected via git if omitted)
  --project DIR      Project root for collision checking (optional)
  --json             Emit full JSON instead of the bare workflow_uuid
  --session-id       Print the current Claude session id (from
                     CLAUDE_CODE_SESSION_ID) or an empty line if absent/invalid
  --emit-env         Print KEY=value lines (workflow_uuid, iso_timestamp,
                     worktree_dir, worktree_branch, session_id, session_id_json)
                     instead of the bare id.  NOT for `eval` (the safe-shell
                     guard denies it): the caller splits each line on the first
                     "="; values are raw (every one matches [A-Za-z0-9_.:@/+-]*,
                     and is shlex-quoted only if it somehow does not);
                     session_id_json is a raw JSON fragment ("..." or null) --
                     json.loads it.
  --init-artifact    Also write {project}/.craftflow/state/workflows/{wf}.json,
                     {wf}.events.jsonl and the workflows/{wf}/ dir (refuses to
                     overwrite; requires <project>/.craftflow to already exist;
                     removes everything it created if any step fails).  Requires --project, --workflow-type,
                     --task-tools-available, --phase.  Optional: --task-id,
                     --workspace-writable-paths-json, --writable-paths-dropped-json,
                     --fallback-reason

Output (default)  : bare workflow_uuid string, e.g.
                      wf-auth-refactor-20260706-140312-d4e5f6a7

Output (--json)   : JSON object:
  {
    "workflow_uuid":   "wf-auth-refactor-20260706-140312-d4e5f6a7",
    "slug":            "auth-refactor",
    "short_hex":       "d4e5f6a7",
    "timestamp":       "20260706-140312",
    "iso_timestamp":   "2026-07-06T14:03:12Z",
    "worktree_dir":    "auth-refactor-d4e5f6a7",
    "worktree_branch": "wf-auth-refactor-d4e5f6a7",
    "session_id":      "0123abcd-4567" | null,
    "session_id_json": "\"0123abcd-4567\"" | "null"   (ready-to-paste JSON fragment)
  }

Concurrency & uniqueness:
  Two concurrent workflows that slugify to the same feature name never collide
  because the 8-hex random suffix (os.urandom(4)) is part of the full id,
  which names every on-disk surface (.json, .events.jsonl, memory dir, task
  metadata).  Collision probability ≈ 2⁻³².  The helper re-rolls the hex if
  the artifact file already exists (requires --project to be set).

Slug source precedence:
  (a) Current git branch, if it is a "genuine" feature branch — NOT one of
      {main, master, develop, dev, trunk} and NOT a craftflow-generated branch
      matching ^(worktree-)?wf- or ^worktree-.
  (b) Otherwise: slugify the user request.

  Rationale: BUILD workflows mint their own worktree branch FROM the base
  branch, so at creation time the user is usually on main and the feature
  branch does not exist yet.  Branch-first is only right when the user
  deliberately checked out a real feature branch before invoking craftflow.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import shlex
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Stopwords — dropped when slugifying a user request
# ---------------------------------------------------------------------------
_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "to", "for", "of", "in", "on", "and", "or",
    "with", "please", "can", "you", "i", "my", "we", "it", "that", "this",
    "its", "add", "let", "make", "create", "implement", "build",
    "update", "fix", "change", "do",
})

# ---------------------------------------------------------------------------
# Branch patterns that disqualify a branch from being used as the slug source
# ---------------------------------------------------------------------------
_BASE_BRANCHES: frozenset[str] = frozenset({
    "main", "master", "develop", "dev", "trunk",
})
_CRAFTFLOW_BRANCH_RE = re.compile(
    r"^(worktree-)?wf-|^worktree-", re.IGNORECASE
)


def _is_feature_branch(branch: str) -> bool:
    """Return True iff `branch` is a genuine user feature branch."""
    if not branch:
        return False
    # Detached HEAD
    if branch.upper() == "HEAD":
        return False
    if branch.lower() in _BASE_BRANCHES:
        return False
    if _CRAFTFLOW_BRANCH_RE.match(branch):
        return False
    return True


# ---------------------------------------------------------------------------
# Slug algorithm (first sanitizer in the Craftflow codebase)
# ---------------------------------------------------------------------------

def slugify(text: str, max_len: int = 32) -> str:
    """Convert arbitrary text to a filesystem- and git-ref-safe kebab-slug.

    Steps:
      1. Lowercase + NFKD accent stripping → ASCII only
      2. Split on whitespace/underscores/slashes/dashes; drop stopwords;
         keep the first ≤ 6 significant tokens
      3. Replace every run of non-[a-z0-9] with a single dash
      4. Collapse repeated dashes; strip leading/trailing dashes
      5. Truncate to max_len; re-strip trailing dash
      6. Empty fallback → "task"

    The result contains only [a-z0-9-], which satisfies all filesystem and
    git-ref safety requirements (no spaces, ~^:?*[\\, no leading -, no ..,
    no .lock suffix).
    """
    # 1. Lowercase + strip diacritics to ASCII
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))

    # 2. Tokenise; remove stopwords; keep first 6 significant tokens
    raw_tokens = re.split(r"[\s_/\-]+", text)
    tokens = [t for t in raw_tokens if t and t not in _STOPWORDS][:6]
    text = " ".join(tokens)

    # 3. Non-[a-z0-9] runs → single dash
    text = re.sub(r"[^a-z0-9]+", "-", text)

    # 4. Collapse; strip ends
    text = re.sub(r"-{2,}", "-", text).strip("-")

    # 5. Truncate; re-strip trailing dash
    text = text[:max_len].rstrip("-")

    return text or "task"


def _slug_from_branch(branch: str) -> str:
    """Slugify a branch name, stripping common namespace prefixes."""
    # Strip common workflow prefixes: feature/, feat/, bugfix/, fix/, etc.
    cleaned = re.sub(
        r"^(feature|feat|bugfix|fix|hotfix|release|chore|refactor|task|issue|story)/",
        "",
        branch,
        flags=re.IGNORECASE,
    )
    return slugify(cleaned)


# ---------------------------------------------------------------------------
# Session id
# ---------------------------------------------------------------------------

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")  # use fullmatch: `$` accepts a trailing newline


def current_session_id(environ=None) -> str | None:
    """Return the Claude session id from env if it is well-formed, else None."""
    env = os.environ if environ is None else environ
    value = env.get("CLAUDE_CODE_SESSION_ID", "")
    if SESSION_ID_RE.fullmatch(value):
        return value
    return None


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _current_branch() -> str:
    """Return the current git branch name, or '' on any failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# ID minting
# ---------------------------------------------------------------------------

def mint_workflow_id(
    request: str,
    branch: str | None,
    project_dir: Path | None = None,
) -> dict:
    """Mint a new workflow id + worktree names.

    Returns a dict with all fields needed by the craftflow-router:
      workflow_uuid, slug, short_hex, timestamp, iso_timestamp,
      worktree_dir, worktree_branch
    """
    # Resolve slug
    if branch is not None and _is_feature_branch(branch):
        slug = _slug_from_branch(branch)
    else:
        slug = slugify(request)

    # Timestamp
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    iso_timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Hex with collision-avoidance (re-roll if artifact file already exists)
    for _ in range(8):
        short_hex = os.urandom(4).hex()
        wf_uuid = f"wf-{slug}-{timestamp}-{short_hex}"
        if project_dir is not None:
            candidate = (
                project_dir
                / ".craftflow"
                / "state"
                / "workflows"
                / f"{wf_uuid}.json"
            )
            if candidate.exists():
                continue  # re-roll on collision
        break

    # Worktree names: slug + hex ensures same-slug concurrent workflows diverge
    worktree_dir = f"{slug}-{short_hex}"
    worktree_branch = f"wf-{slug}-{short_hex}"

    return {
        "workflow_uuid":   wf_uuid,
        "slug":            slug,
        "short_hex":       short_hex,
        "timestamp":       timestamp,
        "iso_timestamp":   iso_timestamp,
        "worktree_dir":    worktree_dir,
        "worktree_branch": worktree_branch,
        "session_id":      current_session_id(),
        "session_id_json": json.dumps(current_session_id()),
    }


# ---------------------------------------------------------------------------
# Artifact initialisation (--init-artifact)
# ---------------------------------------------------------------------------

_TASK_TOOLS_VALUES = ("true", "false", "unknown")


def build_artifact(info: dict, workflow_type: str, request: str, phase: str,
                   task_tools_available: str,
                   workspace_writable_paths: list | None = None) -> dict:
    """Return the v10 workflow artifact (schema: references/workflow-artifact-and-hook-policy.md)."""
    wf = info["workflow_uuid"]
    ts = info["iso_timestamp"]
    session_id = json.loads(info.get("session_id_json", "null"))
    return {
        "workflow_uuid": wf, "workflow_id": wf, "workflow_type": workflow_type,
        "session_id": session_id, "state_root": ".craftflow/state", "user_request": request,
        "plan_file": None, "design_file": None, "research_files": [], "approved_decisions": [],
        "plan_mode": None, "verification_rigor": "standard", "proof_status": "gaps_found",
        "plan_file_stem": None, "bakeoff_n": None, "bakeoff_n_requested": None,
        "bakeoff_models": [], "bakeoff_triggered": False, "bakeoff_all_failed": False,
        "bakeoff_candidate_failures": [],
        "traceability": {"requirements": [], "phases": [], "verification": [], "remediation": []},
        "intent": {"goal": None, "non_goals": [], "constraints": [],
                   "acceptance_criteria": [], "open_decisions": []},
        "normalized_phases": [], "phase_cursor": None,
        "capabilities": {"brightdata_available": "unknown", "octocode_available": "unknown",
                         "websearch_available": "unknown", "webfetch_available": "unknown",
                         "task_tools_available": task_tools_available},
        "research_rounds": [], "research_backend_history": [],
        "research_quality": {"web": "none", "github": "none", "overall": "none"},
        "task_ids": {"planner_create": None, "planning_review_pass1": None,
                     "planner_replan": None, "planning_review_pass2": None,
                     "memory_finalize": None, "plan_bakeoff_candidates": {},
                     "plan_bakeoff_judge": None},
        "phase_status": {},
        "results": {"builder": None, "investigator": None, "reviewer": None, "hunter": None,
                    "verifier": None, "planner": None, "planning_reviewer": None,
                    "research": {"web": None, "github": None, "synthesis": None},
                    "bakeoff": [], "plan_bakeoff_judge": None},
        "evidence": {"builder": [], "investigator": [], "reviewer": [], "hunter": [],
                     "verifier": [], "planning_reviewer": []},
        "telemetry": {"task_metrics_available": "unknown", "workflow_wall_clock_seconds": 0,
                      "agent_wall_clock_seconds": {"builder": 0, "investigator": 0, "reviewer": 0,
                                                   "hunter": 0, "verifier": 0, "planner": 0},
                      "loop_counts": {"re_review": 0, "re_hunt": 0, "re_verify": 0},
                      "verifier": {"phase_exit_proof_runs": 0, "extended_audit_runs": 0,
                                   "workload_seconds": {"tests": 0, "build": 0, "scan": 0,
                                                        "reconcile": 0, "reasoning": 0}}},
        "quality": {"confidence": None, "evidence_complete": False, "scenario_coverage": 0,
                    "research_quality": "none", "convergence_state": "pending"},
        "planning_review_runs": 0, "planning_review_findings": [],
        "planning_review_status": "not_started", "build_mode": None,
        "fast_path_risk_signals": [], "fast_path_escalated": False,
        "worktree_mode": None, "worktree_path": None, "worktree_branch": None,
        "workspace_writable_paths": list(workspace_writable_paths or []),
        "memory_notes": [], "pending_gate": None,
        "circuit_breaker": {"remfix_count": 0, "broken": False},
        "status_history": [{"event": "workflow_started", "ts": ts, "phase": phase}],
        "remediation_history": [], "created_at": ts, "updated_at": ts,
    }


def _create_exclusive(path: Path, text: str) -> None:
    """Create `path` with `text`; FileExistsError (filename=path) if it already exists."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def describe_init_error(exc: OSError) -> str:
    """Accurate one-line message naming the file that actually failed (plus any rollback leftovers)."""
    message = _describe_os_error(exc)
    left = getattr(exc, "rollback_incomplete", None)
    return message + (f" (rollback incomplete: {', '.join(left)})" if left else "")


def _describe_os_error(exc: OSError) -> str:
    target = str(exc.filename) if getattr(exc, "filename", None) else ""
    if isinstance(exc, FileExistsError):
        if target.endswith(".events.jsonl"):
            return f"refusing to overwrite existing events log: {target}"
        if target.endswith(".json"):
            return f"refusing to overwrite existing artifact: {target}"
        return f"cannot create {target or 'state directory'}: already exists"
    return f"{exc.strerror or exc}: {target}" if target else str(exc)


def init_artifact(project_dir: Path, info: dict, *, workflow_type: str, request: str,
                  phase: str, task_tools_available: str, task_id: str | None = None,
                  workspace_writable_paths: list | None = None,
                  dropped_paths: list | None = None,
                  fallback_reason: str | None = None) -> Path:
    """Write the artifact + events.jsonl + workflows/{wf}/ dir.

    Raises OSError (FileExistsError if the artifact/events log already exists); on any failure
    every file this call created is removed first."""
    wf = info["workflow_uuid"]
    ts = info["iso_timestamp"]
    if not (project_dir / ".craftflow").is_dir():
        raise FileNotFoundError(errno.ENOENT, "project has no .craftflow directory", str(project_dir))
    wf_dir = project_dir / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = wf_dir / f"{wf}.json"
    events_path = wf_dir / f"{wf}.events.jsonl"
    artifact = build_artifact(info, workflow_type, request, phase, task_tools_available,
                              workspace_writable_paths)
    events = [{"ts": ts, "wf": wf, "event": "workflow_started", "host": "claude-code",
               "phase": phase, "task_id": task_id, "agent": "router", "decision": "start",
               "reason": "User request"}]
    if fallback_reason:
        entry = {"event": "project_root_resolution_fallback", "ts": ts, "reason": fallback_reason}
        artifact["status_history"].append(entry)
        events.append({"ts": ts, "wf": wf, **entry})
    if dropped_paths:
        entry = {"event": "workspace_writable_paths_entries_dropped", "ts": ts,
                 "dropped": dropped_paths}
        artifact["status_history"].append(entry)
        events.append({"ts": ts, "wf": wf, **entry})
    # Order: events log first (O_EXCL), then artifact (O_EXCL), then the state dir. Anything this
    # call created is removed on ANY later failure, so a failed mint never leaves an orphan.
    created: list[Path] = []
    try:
        _create_exclusive(events_path, "".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n" for event in events))
        created.append(events_path)
        _create_exclusive(artifact_path, json.dumps(artifact, ensure_ascii=False, separators=(",", ":")))
        created.append(artifact_path)
        state_dir = wf_dir / wf
        existed = state_dir.is_dir()
        state_dir.mkdir(exist_ok=True)
        if not existed:
            created.append(state_dir)
    except BaseException as failure:
        left_behind: list[str] = []
        for path in reversed(created):
            try:
                path.rmdir() if path.is_dir() else path.unlink()
            except OSError:
                left_behind.append(str(path))
        if left_behind:
            failure.rollback_incomplete = left_behind  # type: ignore[attr-defined]
        raise
    return artifact_path


_RAW_ENV_VALUE_RE = re.compile(r"[A-Za-z0-9_.:@/+-]*")


def format_env(info: dict) -> str:
    """KEY=value lines.  Values are raw (all minted values are [A-Za-z0-9_.:@/+-]); a value outside
    that set is shlex-quoted.  session_id_json is always a raw JSON fragment (json.loads it)."""
    keys = ("workflow_uuid", "iso_timestamp", "worktree_dir", "worktree_branch",
            "session_id", "session_id_json")
    lines = []
    for key in keys:
        value = str(info.get(key) or "")
        if key != "session_id_json" and not _RAW_ENV_VALUE_RE.fullmatch(value):
            value = shlex.quote(value)
        lines.append(f"{key}={value}")
    return "\n".join(lines)


_ROUTER_REQUEST_RE = re.compile(r"\.router-request-[A-Za-z0-9_-]+\.txt")
_REQUEST_MAX_AGE_SECONDS = 120


def _is_router_request_file(path: Path) -> bool:
    """True for <project>/.craftflow/state/.router-request-{id}.txt (the router's own scratch file)."""
    return bool(_ROUTER_REQUEST_RE.fullmatch(path.name)) and path.parent.name == "state" \
        and path.parent.parent.name == ".craftflow"


def check_request_file_fresh(path: str) -> None:
    """Reject a router request file older than 120 s (a stale/foreign leftover). OSError if so."""
    if path == "-" or not _is_router_request_file(Path(path).resolve()):
        return
    age = time.time() - Path(path).stat().st_mtime
    if age > _REQUEST_MAX_AGE_SECONDS:
        raise OSError(f"stale request file ({int(age)}s old, limit {_REQUEST_MAX_AGE_SECONDS}s): {path}")


def consume_request_file(path: str) -> None:
    """Unlink a router request file after a successful mint; any other path is left alone."""
    if path == "-":
        return
    target = Path(path).resolve()
    if _is_router_request_file(target):
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            sys.stderr.write(f"Warning: could not remove request file {target}: {exc}\n")


def read_request_file(path: str) -> str:
    """Read the request from PATH ('-' = stdin), dropping ONE trailing newline."""
    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OSError(f"{type(exc).__name__}: {exc}") from exc
    if text.endswith("\r\n"):
        return text[:-2]
    return text[:-1] if text.endswith("\n") else text


def _json_list_arg(parser: argparse.ArgumentParser, flag: str, raw: str | None) -> list | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        parser.error(f"{flag} must be a JSON array")
    if not isinstance(value, list):
        parser.error(f"{flag} must be a JSON array")
    return value


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="craftflow-workflow-id",
        description=(
            "Mint a new Craftflow workflow ID and worktree names deterministically. "
            "Called by craftflow-router before every new workflow."
        ),
    )
    parser.add_argument(
        "--request", default=None, metavar="TEXT",
        help="User request text (slug source when not on a feature branch); "
             "required unless --session-id is used",
    )
    parser.add_argument(
        "--request-file", dest="request_file", default=None, metavar="PATH",
        help="Read the request text from PATH ('-' = stdin); avoids shell-quoting the request",
    )
    parser.add_argument(
        "--branch", metavar="NAME", default=None,
        help="Current git branch name (auto-detected via git if omitted)",
    )
    parser.add_argument(
        "--project", metavar="DIR", default=None,
        help="Project root directory for collision checking",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit full JSON instead of the bare workflow_uuid",
    )
    parser.add_argument(
        "--session-id", dest="session_id", action="store_true",
        help="Print the current Claude session id, or an empty line if absent/invalid",
    )
    parser.add_argument("--emit-env", dest="emit_env", action="store_true",
                        help="Print shell-safe KEY=value lines instead of the bare id")
    parser.add_argument("--init-artifact", dest="init_artifact", action="store_true",
                        help="Write the workflow artifact, events log and state dir")
    parser.add_argument("--workflow-type", default=None, help="BUILD|DEBUG|REVIEW|PLAN")
    parser.add_argument("--task-tools-available", default=None, choices=_TASK_TOOLS_VALUES)
    parser.add_argument("--phase", default=None, help="build|debug|review|plan")
    parser.add_argument("--task-id", default=None, help="Parent task id for the event line")
    parser.add_argument("--workspace-writable-paths-json", default=None, metavar="JSON")
    parser.add_argument("--writable-paths-dropped-json", default=None, metavar="JSON")
    parser.add_argument("--fallback-reason", default=None,
                        help="NO_REPO_FOUND|RESOLVE_SCRIPT_ERROR")
    args = parser.parse_args()

    if args.session_id:
        print(current_session_id() or "")
        return 0
    if args.request is not None and args.request_file is not None:
        parser.error("--request and --request-file are mutually exclusive")
    if args.request_file is not None:
        try:
            check_request_file_fresh(args.request_file)
            args.request = read_request_file(args.request_file)
        except (OSError, UnicodeDecodeError) as exc:
            sys.stderr.write(f"Error: cannot read request file: {exc}\n")
            return 1
    if args.request is None:
        parser.error("the following arguments are required: --request (or --request-file)")
    if not args.request.strip():
        sys.stderr.write("Error: the request is empty\n")
        return 1

    branch = args.branch
    if branch is None:
        branch = _current_branch()  # '' on failure → triggers request-slugify path

    project_dir: Path | None = None
    if args.project:
        project_dir = Path(args.project).resolve()

    result = mint_workflow_id(
        request=args.request,
        branch=branch,
        project_dir=project_dir,
    )

    if args.init_artifact:
        missing = [f for f, v in (("--project", args.project), ("--workflow-type", args.workflow_type),
                                  ("--task-tools-available", args.task_tools_available),
                                  ("--phase", args.phase)) if not v]
        if missing:
            parser.error("--init-artifact requires " + ", ".join(missing))
        paths = _json_list_arg(parser, "--workspace-writable-paths-json", args.workspace_writable_paths_json)
        dropped = _json_list_arg(parser, "--writable-paths-dropped-json", args.writable_paths_dropped_json)
        try:
            init_artifact(
                project_dir, result, workflow_type=args.workflow_type, request=args.request,
                phase=args.phase, task_tools_available=args.task_tools_available,
                task_id=args.task_id, workspace_writable_paths=paths, dropped_paths=dropped,
                fallback_reason=args.fallback_reason,
            )
        except OSError as exc:
            sys.stderr.write(f"Error: {describe_init_error(exc)}\n")
            return 1

    if args.init_artifact and args.request_file is not None:
        consume_request_file(args.request_file)

    if args.emit_env:
        print(format_env(result))
    elif args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result["workflow_uuid"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
