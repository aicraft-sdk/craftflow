#!/usr/bin/env python3
"""craftflow_workflow_id.py

Deterministic workflow-ID and worktree-name minting for Craftflow.

Replaces the router LLM's ad-hoc string construction with a single, consistent
source of truth.  Called by craftflow-router before every new workflow.

Usage:
  python3 craftflow_workflow_id.py --request "Add auth refactor" [options]

Options:
  --request TEXT     User request text (required)
  --branch  NAME     Current git branch name (auto-detected via git if omitted)
  --project DIR      Project root for collision checking (optional)
  --json             Emit full JSON instead of the bare workflow_uuid

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
    "worktree_branch": "wf-auth-refactor-d4e5f6a7"
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
import json
import os
import re
import subprocess
import sys
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
    }


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
        "--request", required=True, metavar="TEXT",
        help="User request text (slug source when not on a feature branch)",
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
    args = parser.parse_args()

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

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result["workflow_uuid"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
