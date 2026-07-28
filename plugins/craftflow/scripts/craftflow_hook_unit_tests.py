#!/usr/bin/env python3
"""Unit tests for craftflow Python hook scripts.

Pipes crafted JSON payloads into each hook via subprocess and validates
stdout, exit codes, and file side effects without running Claude Code.

Run: python3 scripts/craftflow_hook_unit_tests.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import io
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS))
import craftflow_hooklib as hooklib  # noqa: E402

_errors: list[str] = []
_passes: int = 0


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  ok  {name}")


def run_hook(script: str, payload: dict, env: dict | None = None) -> tuple[int, str]:
    """Run a hook script with the given JSON payload on stdin. Returns (exit_code, stdout)."""
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged_env,
    )
    return result.returncode, result.stdout.strip()


# ---------------------------------------------------------------------------
# Memory protect pre-hook tests
# ---------------------------------------------------------------------------

def test_memory_protect_pre_ignores_non_craftflow_files(tmp_dir: Path) -> None:
    name = "memory-protect-pre/ignores-non-craftflow-file"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir)}
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": str(tmp_dir / "README.md")},
    }
    code, out = run_hook("craftflow_memory_protect_pre.py", payload, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0 for non-craftflow file")
        return
    if "permissionDecision" in out:
        fail(name, f"hook emitted deny for non-craftflow file: {out[:200]}")
        return
    ok(name)


def test_memory_protect_pre_masks_craftflow_file(tmp_dir: Path) -> None:
    name = "memory-protect-pre/masks-craftflow-state-file"
    craftflow_dir = tmp_dir / ".craftflow" / "state"
    craftflow_dir.mkdir(parents=True)
    target = craftflow_dir / "patterns.md"
    target.write_text(
        "## User Standards\nsome content here\n## Last Updated\n2026-06-01\n",
        encoding="utf-8",
    )
    # Resolve both to handle macOS /tmp -> /private/tmp symlink
    resolved_project = str(tmp_dir.resolve())
    resolved_target = str(target.resolve())
    env = {"CLAUDE_PROJECT_DIR": resolved_project}
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": resolved_target},
    }
    code, out = run_hook("craftflow_memory_protect_pre.py", payload, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return
    content_after = target.read_text(encoding="utf-8")
    if "CRAFTFLOW_BLOCK_" not in content_after:
        fail(name, "expected CRAFTFLOW_BLOCK_ placeholder in file after masking")
        return
    cache_dir = tmp_dir / ".craftflow" / ".memory-protect-cache"
    if not any(cache_dir.glob("*.orig")):
        fail(name, "expected .orig backup file in cache dir")
        return
    ok(name)


def test_memory_protect_pre_ignores_non_read_tool(tmp_dir: Path) -> None:
    name = "memory-protect-pre/ignores-non-read-tool"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir)}
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(tmp_dir / ".craftflow" / "state" / "patterns.md")},
    }
    code, out = run_hook("craftflow_memory_protect_pre.py", payload, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0 for non-Read tool")
        return
    if "permissionDecision" in out:
        fail(name, f"hook emitted deny for non-Read tool: {out[:200]}")
        return
    ok(name)


def test_memory_protect_pre_empty_stdin(tmp_dir: Path) -> None:
    name = "memory-protect-pre/handles-empty-stdin"
    script = str(SCRIPTS / "craftflow_memory_protect_pre.py")
    result = subprocess.run(
        [sys.executable, script],
        input="",
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_dir)},
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}; expected 0 on empty stdin")
        return
    ok(name)


# ---------------------------------------------------------------------------
# SDD cache pre-hook tests
# ---------------------------------------------------------------------------

def test_sdd_cache_pre_ignores_non_webfetch(tmp_dir: Path) -> None:
    name = "sdd-cache-pre/ignores-non-webfetch-tool"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir)}
    payload = {
        "tool_name": "Read",
        "tool_input": {"url": "https://example.com/docs"},
    }
    code, out = run_hook("craftflow_sdd_cache_pre.py", payload, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return
    if "permissionDecision" in out:
        fail(name, "hook should not deny for non-WebFetch tool")
        return
    ok(name)


def test_sdd_cache_pre_no_cache_on_fresh_url(tmp_dir: Path) -> None:
    name = "sdd-cache-pre/allows-fetch-when-no-cache"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir)}
    payload = {
        "tool_name": "WebFetch",
        "tool_input": {"url": "https://example.com/uncached", "prompt": "get content"},
    }
    code, out = run_hook("craftflow_sdd_cache_pre.py", payload, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return
    if "permissionDecision" in out and "deny" in out:
        fail(name, "hook should not deny a URL that has no cache entry")
        return
    ok(name)


def test_sdd_cache_pre_rejects_cache_without_validators(tmp_dir: Path) -> None:
    name = "sdd-cache-pre/skips-cache-entry-without-etag"
    cache_dir = tmp_dir / ".craftflow" / "sdd-cache"
    cache_dir.mkdir(parents=True)
    import hashlib
    url = "https://example.com/stale-cache"
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    entry = {
        "url": url,
        "etag": None,
        "last_modified": None,
        "original_prompt": "test",
        "processed_reading": "some content",
        "cached_at": "2026-06-01T00:00:00+00:00",
    }
    (cache_dir / f"{key}.json").write_text(json.dumps(entry), encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir)}
    payload = {
        "tool_name": "WebFetch",
        "tool_input": {"url": url, "prompt": "test"},
    }
    code, out = run_hook("craftflow_sdd_cache_pre.py", payload, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return
    if "deny" in out:
        fail(name, "hook must not deny when cache entry has no etag/last_modified")
        return
    ok(name)


# ---------------------------------------------------------------------------
# SDD cache post-hook tests
# ---------------------------------------------------------------------------

def test_sdd_cache_post_ignores_non_webfetch(tmp_dir: Path) -> None:
    name = "sdd-cache-post/ignores-non-webfetch-tool"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir)}
    payload = {
        "tool_name": "Read",
        "tool_input": {"url": "https://example.com/docs"},
        "tool_response": {"content": "ETag: abc123\n"},
    }
    code, _ = run_hook("craftflow_sdd_cache_post.py", payload, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return
    cache_dir = tmp_dir / ".craftflow" / "sdd-cache"
    if cache_dir.exists() and any(cache_dir.glob("*.json")):
        fail(name, "post hook must not write cache for non-WebFetch tool")
        return
    ok(name)


def test_sdd_cache_post_writes_entry_with_etag(tmp_dir: Path) -> None:
    name = "sdd-cache-post/writes-cache-entry-when-etag-present"
    import hashlib
    url = "https://example.com/api-docs"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir)}
    payload = {
        "tool_name": "WebFetch",
        "tool_input": {"url": url, "prompt": "get docs"},
        "tool_response": {"content": f"ETag: \"abc123\"\nContent-Type: text/html\n\nsome page"},
    }
    code, _ = run_hook("craftflow_sdd_cache_post.py", payload, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    cache_path = tmp_dir / ".craftflow" / "sdd-cache" / f"{key}.json"
    if not cache_path.exists():
        fail(name, "expected cache entry file to be written")
        return
    entry = json.loads(cache_path.read_text(encoding="utf-8"))
    if entry.get("etag") != '"abc123"':
        fail(name, f"expected etag 'abc123', got {entry.get('etag')!r}")
        return
    if not entry.get("processed_reading"):
        fail(name, "processed_reading must be present (even as placeholder)")
        return
    ok(name)


def test_sdd_cache_post_skips_entry_without_freshness_headers(tmp_dir: Path) -> None:
    name = "sdd-cache-post/skips-entry-when-no-freshness-headers"
    url = "https://example.com/no-freshness"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir)}
    payload = {
        "tool_name": "WebFetch",
        "tool_input": {"url": url, "prompt": "test"},
        "tool_response": {"content": "Content-Type: text/html\n\nbody text"},
    }
    code, _ = run_hook("craftflow_sdd_cache_post.py", payload, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return
    cache_dir = tmp_dir / ".craftflow" / "sdd-cache"
    if cache_dir.exists() and any(cache_dir.glob("*.json")):
        fail(name, "post hook must not write cache entry when no ETag or Last-Modified")
        return
    ok(name)


# ---------------------------------------------------------------------------
# Memory protect restore tests
# ---------------------------------------------------------------------------

def test_memory_protect_restore_triggers_on_subagent_stop(tmp_dir: Path) -> None:
    name = "memory-protect-restore/restores-on-subagent-stop"
    import hashlib

    resolved_tmp = tmp_dir.resolve()
    state_dir = resolved_tmp / ".craftflow" / "state"
    state_dir.mkdir(parents=True)

    target = state_dir / "patterns.md"
    original_content = "## Patterns\noriginal content here\n"
    masked_content = "<!-- CRAFTFLOW_BLOCK_aabbccddeeff -->\n"
    target.write_text(masked_content, encoding="utf-8")

    key = hashlib.sha1(str(target).encode("utf-8")).hexdigest()[:12]
    cache_dir = resolved_tmp / ".craftflow" / ".memory-protect-cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / f"{key}.orig").write_text(original_content, encoding="utf-8")

    env = {"CLAUDE_PROJECT_DIR": str(resolved_tmp)}
    payload = {"hook_event_name": "SubagentStop"}
    code, _ = run_hook("craftflow_memory_protect_restore.py", payload, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return

    restored = target.read_text(encoding="utf-8")
    if "CRAFTFLOW_BLOCK_" in restored:
        fail(name, "CRAFTFLOW_BLOCK_ placeholder still present; restore_all() not triggered for SubagentStop")
        return
    ok(name)


# ---------------------------------------------------------------------------
# Anti-rationalization structural tests (verify tables are in all agents)
# ---------------------------------------------------------------------------

def test_anti_rationalization_tables_present() -> None:
    agents_dir = PLUGIN_ROOT / "agents"
    required_agents = [
        "component-builder.md",
        "integration-verifier.md",
        "bug-investigator.md",
        "code-reviewer.md",
        "silent-failure-hunter.md",
        "planner.md",
        "web-researcher.md",
        "github-researcher.md",
    ]
    for filename in required_agents:
        name = f"anti-rationalization/{filename}"
        path = agents_dir / filename
        if not path.exists():
            fail(name, f"agent file missing: {path}")
            continue
        content = path.read_text(encoding="utf-8")
        if "## Common Shortcuts (Anti-Rationalization)" not in content:
            fail(name, "missing '## Common Shortcuts (Anti-Rationalization)' section")
            continue
        if "| Shortcut | Why It Fails |" not in content:
            fail(name, "missing table header '| Shortcut | Why It Fails |'")
            continue
        ok(name)


def test_doubt_verifier_agent_present() -> None:
    name = "doubt-verifier/agent-file-present"
    path = PLUGIN_ROOT / "agents" / "doubt-verifier.md"
    if not path.exists():
        fail(name, f"doubt-verifier.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in ("DOUBT_VERDICT", "DOUBT_THEATER", "CYCLE_COMPLETE"):
        if marker not in content:
            fail(name, f"doubt-verifier.md missing expected marker: {marker}")
            return
    ok(name)


def test_intent_interview_skill_present() -> None:
    name = "intent-interview/skill-file-present"
    path = PLUGIN_ROOT / "skills" / "intent-interview" / "SKILL.md"
    if not path.exists():
        fail(name, f"intent-interview SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in ("Intent Contract", "AUTO_PROCEED", "confidence"):
        if marker not in content:
            fail(name, f"intent-interview SKILL.md missing expected marker: {marker}")
            return
    ok(name)


def test_router_dispatches_doubt_verify() -> None:
    name = "router/doubt-verify-dispatch-registered"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "doubt-verify" not in content:
        fail(name, "craftflow-router SKILL.md missing 'doubt-verify' dispatch reference")
        return
    if "doubt-verifier" not in content:
        fail(name, "craftflow-router SKILL.md missing 'doubt-verifier' agent reference")
        return
    ok(name)


def test_router_dispatches_intent_interview() -> None:
    name = "router/intent-interview-gate-registered"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "intent-interview" not in content:
        fail(name, "craftflow-router SKILL.md missing 'intent-interview' dispatch reference")
        return
    ok(name)


def test_workflow_id_script_present() -> None:
    name = "scripts/craftflow_workflow_id-present"
    path = SCRIPTS / "craftflow_workflow_id.py"
    if not path.exists():
        fail(name, f"craftflow_workflow_id.py not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in ("mint_workflow_id", "slugify", "_is_feature_branch", "worktree_branch"):
        if marker not in content:
            fail(name, f"craftflow_workflow_id.py missing expected symbol: {marker!r}")
            return
    ok(name)


def test_statusline_script_present() -> None:
    name = "scripts/craftflow_statusline-present"
    path = SCRIPTS / "craftflow_statusline.sh"
    if not path.exists():
        fail(name, f"craftflow_statusline.sh not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in ("craftflow_status_report.py", "--statusline", "claude-hud"):
        if marker not in content:
            fail(name, f"craftflow_statusline.sh missing expected reference: {marker!r}")
            return
    ok(name)


def test_router_uses_workflow_id_helper() -> None:
    name = "router/workflow-id-helper-wired"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "craftflow_workflow_id.py" not in content:
        fail(name, "SKILL.md does not reference craftflow_workflow_id.py helper")
        return
    if "worktree_dir" not in content or "worktree_branch" not in content:
        fail(name, "SKILL.md missing worktree_dir/worktree_branch bindings from helper")
        return
    ok(name)


def test_pretooluse_guard_blocks_memory_write_without_permit(tmp_dir: Path) -> None:
    name = "pretooluse-guard/blocks-memory-write-without-permit"
    # Point CLAUDE_PLUGIN_ROOT to the real plugin so hook-mode.json (memoryWrites=block) is loaded
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    state = tmp_dir / ".craftflow" / "state"
    state.mkdir(parents=True)
    target = state / "project" / "activeContext.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Active Context\n", encoding="utf-8")
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for unguarded memory write; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_allows_memory_write_with_permit(tmp_dir: Path) -> None:
    name = "pretooluse-guard/allows-memory-write-with-permit"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir)}
    state = tmp_dir / ".craftflow" / "state"
    state.mkdir(parents=True)
    wf_uuid = "wf-test-1234"
    # Create the permit token
    (state / ".memory-finalize").write_text(wf_uuid, encoding="utf-8")
    # Create a minimal workflow artifact so latest_workflow_payload finds the uuid
    wf_dir = state / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / f"{wf_uuid}.json").write_text(
        f'{{"workflow_uuid":"{wf_uuid}"}}', encoding="utf-8"
    )
    target = state / "project" / "activeContext.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Active Context\n", encoding="utf-8")
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"guard blocked write that had a valid permit; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# Bash destructive-command traversal guard tests
# ---------------------------------------------------------------------------
# REM-FIX (docs/incidents/2026-07-25-phase3-verifier-rm-attempt.md): a
# dispatched subagent issued an rm command that, via relative-path
# traversal from its worktree, resolved to the project root -- caught only
# by the harness's own built-in destructive-command detector, not by
# anything craftflow shipped. These tests cover craftflow's own
# deterministic PreToolUse layer for the same failure mode.

def test_bash_guard_blocks_relative_traversal_escaping_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-relative-traversal-escaping-cwd"
    project = tmp_dir / "project"
    worktree = project / ".claude" / "worktrees" / "wf-test"
    worktree.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(worktree.resolve()),
        "tool_input": {"command": "rm -f ../../.."},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a traversal escaping the worktree cwd; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_absolute_path_outside_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-absolute-path-outside-cwd"
    cwd_dir = tmp_dir / "cwd"
    outside_dir = tmp_dir / "outside"
    cwd_dir.mkdir(parents=True)
    outside_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": f"rm -rf {outside_dir.resolve()}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for an absolute path outside cwd; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_command_within_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/allows-command-within-cwd"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -f ./scratch/tmp.txt"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected silent allow for an rm target within cwd; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_non_destructive_command(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/allows-non-destructive-command"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "git status"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected silent allow for a non-destructive command; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_unverifiable_dynamic_path(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/allows-unverifiable-dynamic-path"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": 'rm -rf "$TMPDIR/scratch"'},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"expected an unverifiable dynamic path to fall through without a hard block; got: {out!r}")
        return
    ok(name)


def test_bash_guard_ignores_non_bash_tool(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/ignores-non-bash-tool"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Edit",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"file_path": str(cwd_dir / "x.md")},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected silent allow for a non-Bash tool; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# --- widened vocabulary + in-cwd tests ---
# ---------------------------------------------------------------------------
# Phase 2 of docs/plans/2026-07-28-craftflow-guardrail-hardening-plan.md:
# widens DESTRUCTIVE_COMMANDS shape-matching (rm/rmdir/mv/shred/truncate/
# chmod positional targets, git clean/reset/push subcommand+flag shapes,
# dd's of= key=value target, find -exec rm/-delete) and adds an in-cwd
# critical-target check (bare cwd, "*", ".", or a CRITICAL_TOP_LEVEL_CHILDREN
# entry) so destructive commands that never leave cwd are no longer a blind
# spot.

def test_bash_guard_blocks_rm_rf_dot_in_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-rm-rf-dot-in-cwd"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ."},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf .' in cwd; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_star_in_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-rm-rf-star-in-cwd"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf *"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf *' in cwd; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_dotgit_in_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-rm-rf-dotgit-in-cwd"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf .git"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf .git' in cwd; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_critical_child_packages(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-rm-rf-critical-child-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./packages"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./packages' (critical top-level child); got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_rm_in_noncritical_subdir(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/allows-rm-in-noncritical-subdir"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./scratch"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'rm -rf ./scratch' (non-critical subdir); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_git_clean_force(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-git-clean-force"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "git clean -fdx"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'git clean -fdx'; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_git_clean_without_force_flag(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/allows-git-clean-without-force-flag"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "git clean"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'git clean' with no -f flag; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_git_reset_hard(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-git-reset-hard"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "git reset --hard HEAD~1"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'git reset --hard'; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_git_reset_without_hard(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/allows-git-reset-without-hard"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "git reset HEAD~1"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'git reset' without --hard; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_git_push_force(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-git-push-force"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "git push --force"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'git push --force'; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_git_push_without_force(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/allows-git-push-without-force"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "git push"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'git push' without --force; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_git_push_force_with_lease(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/allows-git-push-force-with-lease"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "git push --force-with-lease"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(
            name,
            "expected allow for 'git push --force-with-lease' -- this plan "
            f"matches only the literal --force flag by design; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_mv_escaping_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-mv-escaping-cwd"
    cwd_dir = tmp_dir / "cwd"
    outside_dir = tmp_dir / "outside"
    cwd_dir.mkdir(parents=True)
    outside_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": f"mv secret.txt {outside_dir.resolve()}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'mv' targeting outside cwd; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_dd_of_traversal(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-dd-of-traversal"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "dd if=/dev/zero of=../../../etc/passwd"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'dd ... of=' traversal target; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_dd_of_in_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/allows-dd-of-in-cwd"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "dd if=/dev/zero of=./scratch.img"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'dd ... of=' target within cwd; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_chmod_escaping_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-chmod-escaping-cwd"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "chmod -R 777 /etc"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'chmod' targeting an absolute path outside cwd; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_find_exec_rm(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-find-exec-rm"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "find /etc -exec rm {} \\;"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'find -exec rm' targeting an escaping search path; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_find_delete_in_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/allows-find-delete-in-cwd"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "find ./scratch -delete"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'find -delete' within cwd; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_shred_escaping_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-shred-escaping-cwd"
    cwd_dir = tmp_dir / "cwd"
    outside_file = tmp_dir / "outside" / "secret.txt"
    cwd_dir.mkdir(parents=True)
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("x", encoding="utf-8")
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": f"shred -u {outside_file.resolve()}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'shred' targeting outside cwd; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_truncate_escaping_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-truncate-escaping-cwd"
    cwd_dir = tmp_dir / "cwd"
    outside_file = tmp_dir / "outside" / "secret.txt"
    cwd_dir.mkdir(parents=True)
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("x", encoding="utf-8")
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": f"truncate -s 0 {outside_file.resolve()}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'truncate' targeting outside cwd; got: {out!r}")
        return
    ok(name)


def test_bash_guard_regression_lock_release_still_allowed(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/regression-lock-release-still-allowed"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": 'rm -rf "$LOCK_DIR"'},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"regression flow 1 (lock release) must stay allowed; got: {out!r}")
        return
    ok(name)


def test_bash_guard_regression_memory_finalize_clear_still_allowed(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/regression-memory-finalize-clear-still-allowed"
    cwd_dir = tmp_dir / "cwd"
    state_dir = cwd_dir / ".craftflow" / "state"
    state_dir.mkdir(parents=True)
    (state_dir / ".memory-finalize").write_text("wf-test-1234", encoding="utf-8")
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -f .craftflow/state/.memory-finalize"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"regression flow 3 (memory-finalize clear) must stay allowed; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# --- hooklib shared-helper tests (white-box) ---
# ---------------------------------------------------------------------------

def test_hooklib_resolve_confinement_allows_within_cwd(tmp_dir: Path) -> None:
    name = "hooklib/resolve-confinement-allows-within-cwd"
    cwd = (tmp_dir / "cwd")
    cwd.mkdir(parents=True)
    cwd = cwd.resolve()
    target = cwd / "file.txt"
    confined, _resolved = hooklib.resolve_confinement(target, cwd, None)
    if not confined:
        fail(name, f"expected confined=True for a path within cwd; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_denies_outside_cwd_no_worktree(tmp_dir: Path) -> None:
    name = "hooklib/resolve-confinement-denies-outside-cwd-no-worktree"
    cwd = (tmp_dir / "cwd")
    outside = (tmp_dir / "outside")
    cwd.mkdir(parents=True)
    outside.mkdir(parents=True)
    cwd = cwd.resolve()
    outside = outside.resolve()
    target = outside / "file.txt"
    confined, _resolved = hooklib.resolve_confinement(target, cwd, None)
    if confined:
        fail(name, f"expected confined=False for a path outside cwd with no worktree; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_allows_within_worktree_outside_cwd(tmp_dir: Path) -> None:
    name = "hooklib/resolve-confinement-allows-within-worktree-outside-cwd"
    cwd = (tmp_dir / "project")
    worktree = (tmp_dir / "worktree-sibling")
    cwd.mkdir(parents=True)
    worktree.mkdir(parents=True)
    cwd = cwd.resolve()
    worktree = worktree.resolve()
    target = worktree / "file.txt"
    confined, _resolved = hooklib.resolve_confinement(target, cwd, str(worktree))
    if not confined:
        fail(name, f"expected confined=True for a path within worktree_path though outside cwd; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_denies_outside_both(tmp_dir: Path) -> None:
    name = "hooklib/resolve-confinement-denies-outside-both"
    cwd = (tmp_dir / "project")
    worktree = (tmp_dir / "worktree-sibling")
    outside = (tmp_dir / "outside")
    cwd.mkdir(parents=True)
    worktree.mkdir(parents=True)
    outside.mkdir(parents=True)
    cwd = cwd.resolve()
    worktree = worktree.resolve()
    outside = outside.resolve()
    target = outside / "file.txt"
    confined, _resolved = hooklib.resolve_confinement(target, cwd, str(worktree))
    if confined:
        fail(name, f"expected confined=False for a path outside both cwd and worktree_path; got confined={confined}")
        return
    ok(name)


def test_hooklib_command_has_traversal_true_for_dotdot_substitution() -> None:
    name = "hooklib/command-has-traversal-true-for-dotdot-substitution"
    if not hooklib.command_has_traversal_or_wildcard('rm -rf "$(echo ../../..)"'):
        fail(name, "expected True for a traversal literal inside a $() substitution")
        return
    ok(name)


def test_hooklib_command_has_traversal_false_for_lock_dir_var() -> None:
    # Regression-flow-1 groundwork: `rm -rf "$LOCK_DIR"` must NOT be flagged --
    # it's a plain dynamic variable reference, no traversal/wildcard literal.
    name = "hooklib/command-has-traversal-false-for-lock-dir-var"
    if hooklib.command_has_traversal_or_wildcard('rm -rf "$LOCK_DIR"'):
        fail(name, "expected False for a plain $LOCK_DIR variable reference")
        return
    ok(name)


def test_hooklib_command_has_traversal_true_for_bare_wildcard() -> None:
    name = "hooklib/command-has-traversal-true-for-bare-wildcard"
    if not hooklib.command_has_traversal_or_wildcard("rm -rf *"):
        fail(name, "expected True for a bare wildcard token")
        return
    ok(name)


def test_hooklib_extract_redirect_targets_finds_simple_redirect() -> None:
    name = "hooklib/extract-redirect-targets-finds-simple-redirect"
    targets = hooklib.extract_redirect_targets("echo x > foo.txt")
    if targets != ["foo.txt"]:
        fail(name, f"expected ['foo.txt']; got {targets!r}")
        return
    ok(name)


def test_hooklib_extract_redirect_targets_finds_tee_target() -> None:
    name = "hooklib/extract-redirect-targets-finds-tee-target"
    targets = hooklib.extract_redirect_targets("echo x | tee foo.txt")
    if "foo.txt" not in targets:
        fail(name, f"expected 'foo.txt' in {targets!r}")
        return
    ok(name)


def test_hooklib_extract_redirect_targets_empty_for_no_redirect() -> None:
    name = "hooklib/extract-redirect-targets-empty-for-no-redirect"
    targets = hooklib.extract_redirect_targets("git status")
    if targets != []:
        fail(name, f"expected []; got {targets!r}")
        return
    ok(name)


def test_hooklib_matches_permit_shape_true_for_exact_documented_command() -> None:
    # Regression-flow-2 groundwork: the one documented memory-finalize
    # permit-write shape must match exactly.
    name = "hooklib/matches-permit-shape-true-for-exact-documented-command"
    tokens = hooklib.split_subcommands(
        "printf '%s' 'wf-1234' > .craftflow/state/.memory-finalize"
    )[0]
    if not hooklib.matches_memory_finalize_permit_shape(
        tokens, ".craftflow/state/.memory-finalize"
    ):
        fail(name, "expected True for the exact documented permit-write shape")
        return
    ok(name)


def test_hooklib_matches_permit_shape_false_for_different_printf_args() -> None:
    name = "hooklib/matches-permit-shape-false-for-different-printf-args"
    tokens = hooklib.split_subcommands(
        "printf '%s\\ninjected' 'wf-1234' > .craftflow/state/.memory-finalize"
    )[0]
    if hooklib.matches_memory_finalize_permit_shape(
        tokens, ".craftflow/state/.memory-finalize"
    ):
        fail(name, "expected False for a printf with a different format string")
        return
    ok(name)


def test_hooklib_matches_permit_shape_false_for_heredoc() -> None:
    name = "hooklib/matches-permit-shape-false-for-heredoc"
    tokens = hooklib.split_subcommands(
        "cat << EOF > .craftflow/state/.memory-finalize"
    )[0]
    if hooklib.matches_memory_finalize_permit_shape(
        tokens, ".craftflow/state/.memory-finalize"
    ):
        fail(name, "expected False for a heredoc-shaped subcommand")
        return
    ok(name)


def test_hooklib_matches_permit_shape_false_for_substitution_in_value() -> None:
    # CRITICAL fix: a value token containing a live $(...) command
    # substitution must never be waved through as the trusted documented
    # permit-write shape -- the shell would execute the substitution
    # (arbitrary code) before printf even runs.
    name = "hooklib/matches-permit-shape-false-for-substitution-in-value"
    tokens = hooklib.split_subcommands(
        "printf '%s' \"$(touch /tmp/x)\" > .craftflow/state/.memory-finalize"
    )[0]
    if hooklib.matches_memory_finalize_permit_shape(
        tokens, ".craftflow/state/.memory-finalize"
    ):
        fail(name, "expected False for a value token containing command substitution")
        return
    ok(name)


def test_hooks_json_registers_bash_guard() -> None:
    name = "hooks/bash-guard-registered"
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    if not path.exists():
        fail(name, f"hooks.json not found at {path}")
        return
    hooks = json.loads(path.read_text(encoding="utf-8"))
    pre_hooks = hooks.get("hooks", {}).get("PreToolUse", [])
    bash_entries = [entry for entry in pre_hooks if entry.get("matcher") == "Bash"]
    if not bash_entries:
        fail(name, "hooks.json has no PreToolUse entry with matcher 'Bash'")
        return
    commands = " ".join(h.get("command", "") for entry in bash_entries for h in entry.get("hooks", []))
    if "craftflow_pretooluse_bash_guard" not in commands:
        fail(name, "PreToolUse Bash matcher does not invoke craftflow_pretooluse_bash_guard.py")
        return
    ok(name)


# ---------------------------------------------------------------------------
# Hook self-check tests
# ---------------------------------------------------------------------------

def _selfcheck_write_clean_script(path: Path, module_name: str) -> None:
    path.write_text(
        f"# {module_name}: trivially valid module, used only by the self-check scratch test\nVALUE = 1\n",
        encoding="utf-8",
    )


def _selfcheck_write_broken_script(path: Path) -> None:
    # Same defect class as the real craftflow_pretooluse_guard.py bug this
    # feature exists to catch: a PEP604 `X | None` annotation with no
    # `from __future__ import annotations`, which raises TypeError at
    # import time on this machine's real python3 (3.9.6).
    path.write_text(
        "def helper(x: str | None) -> str | None:\n    return x\n",
        encoding="utf-8",
    )


# Comfortably above the checker's own MIN_EXPECTED_SIBLING_SCRIPTS floor (5)
# so ordinary RED/GREEN scratch tests never accidentally trip the "suspiciously
# few sibling scripts" self-diagnostic (that failure mode has its own,
# dedicated test with a deliberately tiny sibling count -- see
# test_selfcheck_warns_on_suspiciously_low_sibling_count).
_SELFCHECK_SCRATCH_CLEAN_COUNT = 6


def _selfcheck_scratch_checker(tmp_dir: Path, n_clean: int = _SELFCHECK_SCRATCH_CLEAN_COUNT) -> Path:
    """Copy the real checker's source into tmp_dir alongside n_clean trivially-valid
    sibling scripts. Returns the path to the scratch copy of the checker."""
    tmp_dir.mkdir(parents=True)
    real_checker = SCRIPTS / "craftflow_hook_selfcheck.py"
    scratch_checker = tmp_dir / "craftflow_hook_selfcheck.py"
    scratch_checker.write_text(real_checker.read_text(encoding="utf-8"), encoding="utf-8")
    for i in range(n_clean):
        _selfcheck_write_clean_script(
            tmp_dir / f"craftflow_scratch_clean_{i}.py", f"craftflow_scratch_clean_{i}"
        )
    return scratch_checker


def _load_selfcheck_module():
    """Dynamically load the real craftflow_hook_selfcheck.py as a module in
    THIS test process, for direct white-box testing of its pure functions
    (e.g. run_selfcheck's per-item isolation). This is a test-harness
    convenience, not a violation of the checker's own "never import a checked
    script directly" constraint -- that constraint governs what
    craftflow_hook_selfcheck.py imports in its OWN runtime process; it says
    nothing about how this separate test harness loads it for inspection."""
    path = SCRIPTS / "craftflow_hook_selfcheck.py"
    spec = importlib.util.spec_from_file_location("craftflow_hook_selfcheck_test_import", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selfcheck_detects_broken_script_red(tmp_dir: Path) -> None:
    name = "hook-selfcheck/detects-broken-script-RED"
    scratch_checker = _selfcheck_scratch_checker(tmp_dir)
    _selfcheck_write_broken_script(tmp_dir / "craftflow_scratch_broken.py")
    result = subprocess.run(
        [sys.executable, str(scratch_checker)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}; expected 0 even when a failure is detected")
        return
    if "craftflow_scratch_broken.py" not in result.stdout:
        fail(name, f"expected broken script name in stdout warning; got: {result.stdout!r}")
        return
    if '"additionalContext"' not in result.stdout:
        fail(name, f"expected hookSpecificOutput.additionalContext in stdout; got: {result.stdout!r}")
        return
    for i in range(_SELFCHECK_SCRATCH_CLEAN_COUNT):
        clean_name = f"craftflow_scratch_clean_{i}.py"
        if clean_name in result.stdout:
            fail(name, f"clean script {clean_name} incorrectly reported as failing")
            return
    ok(name)


def test_selfcheck_silent_on_clean_scripts_green(tmp_dir: Path) -> None:
    name = "hook-selfcheck/silent-when-clean-GREEN"
    scratch_checker = _selfcheck_scratch_checker(tmp_dir)
    result = subprocess.run(
        [sys.executable, str(scratch_checker)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}; expected 0")
        return
    if result.stdout.strip():
        fail(name, f"expected silent stdout on a clean run; got: {result.stdout!r}")
        return
    ok(name)


def test_selfcheck_warns_on_suspiciously_low_sibling_count(tmp_dir: Path) -> None:
    # REM-FIX (doubt-verifier HIGH #1): discover_sibling_scripts() has no
    # floor/sanity check -- Path.glob() on a misresolved or empty directory
    # returns [] with no exception (this repo has direct history of exactly
    # this class of bug: git show 76020c0). A near-empty result must not look
    # identical to a genuinely clean run of all real sibling scripts.
    name = "hook-selfcheck/warns-on-suspiciously-low-sibling-count"
    tmp_dir.mkdir(parents=True)
    real_checker = SCRIPTS / "craftflow_hook_selfcheck.py"
    scratch_checker = tmp_dir / "craftflow_hook_selfcheck.py"
    scratch_checker.write_text(real_checker.read_text(encoding="utf-8"), encoding="utf-8")
    # Deliberately far below any reasonable floor: only 1 sibling script,
    # simulating scripts_dir having resolved to the wrong (near-empty)
    # directory.
    _selfcheck_write_clean_script(tmp_dir / "craftflow_scratch_clean_0.py", "craftflow_scratch_clean_0")
    result = subprocess.run(
        [sys.executable, str(scratch_checker)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}; expected 0 even when the self-diagnostic fires")
        return
    if not result.stdout.strip():
        fail(
            name,
            "expected a self-diagnostic warning when the discovered sibling count is "
            "suspiciously low; stdout was silent (indistinguishable from a genuinely clean run)",
        )
        return
    if '"additionalContext"' not in result.stdout:
        fail(name, f"expected hookSpecificOutput.additionalContext in stdout; got: {result.stdout!r}")
        return
    if "1" not in result.stdout:
        fail(name, f"expected the discovered count (1) to appear in the self-diagnostic warning; got: {result.stdout!r}")
        return
    ok(name)


def test_selfcheck_isolates_per_script_subprocess_errors(tmp_dir: Path) -> None:
    # REM-FIX (doubt-verifier HIGH #2): run_selfcheck's per-script loop had no
    # per-item isolation -- a single subprocess.run() raising a plain OSError
    # (e.g. real resource pressure from spawning ~35 sequential python3
    # subprocesses) propagated out of the whole loop, discarding every
    # already-collected result. This recreates, one layer down, the exact
    # "silently failed for a month" failure mode this feature exists to close.
    name = "hook-selfcheck/isolates-per-script-subprocess-errors"
    tmp_dir.mkdir(parents=True)
    for i in range(3):
        _selfcheck_write_clean_script(
            tmp_dir / f"craftflow_scratch_clean_{i}.py", f"craftflow_scratch_clean_{i}"
        )
    module = _load_selfcheck_module()
    real_run = module.subprocess.run

    def flaky_run(argv, **kwargs):
        if len(argv) >= 3 and "craftflow_scratch_clean_1" in argv[2]:
            raise OSError("simulated resource-pressure failure spawning subprocess")
        return real_run(argv, **kwargs)

    module.subprocess.run = flaky_run
    try:
        siblings, discovery_error = module.discover_sibling_scripts(
            tmp_dir, tmp_dir / "craftflow_hook_selfcheck.py"
        )
        if discovery_error is not None:
            fail(name, f"sibling discovery unexpectedly failed: {discovery_error}")
            return
        failures, checked, total = module.run_selfcheck(tmp_dir, siblings)
    except OSError as exc:
        fail(
            name,
            f"a single script's OSError propagated out of run_selfcheck and erased all "
            f"other already-collected results: {exc}",
        )
        return
    finally:
        module.subprocess.run = real_run

    failure_names = [n for n, _ in failures]
    if failure_names != ["craftflow_scratch_clean_1.py"]:
        fail(
            name,
            f"expected exactly one isolated failure entry for craftflow_scratch_clean_1.py "
            f"(others unaffected); got {failures}",
        )
        return
    if (checked, total) != (3, 3):
        fail(name, f"expected all 3 scripts to be counted as checked (no deadline set); got checked={checked}, total={total}")
        return
    ok(name)


def test_selfcheck_discovery_itself_bounded_by_timeout(tmp_dir: Path) -> None:
    # REM-FIX (doubt-verifier cycle-2 BLOCKING #1): discover_sibling_scripts()
    # (the scripts_dir.glob() call) was unbounded, and the internal deadline
    # clock only starts AFTER it returns. A slow filesystem (NFS mount,
    # unsynced iCloud path, etc.) would hang before the deadline mechanism
    # ever engages, with nothing yet collected to flush -- worse than the
    # original bug, since even the truncation-flush mitigation has nothing to
    # flush at that point. Simulates a stalled directory listing (a plain,
    # non-generator monkeypatch of Path.glob that blocks synchronously) and
    # asserts discovery degrades with a distinct diagnosis within its own
    # bounded timeout, instead of hanging.
    name = "hook-selfcheck/discovery-itself-bounded"
    tmp_dir.mkdir(parents=True)
    module = _load_selfcheck_module()
    real_glob = module.Path.glob

    def hanging_glob(self, pattern):
        time.sleep(30)  # simulate a stalled filesystem syscall
        return real_glob(self, pattern)

    module.Path.glob = hanging_glob
    try:
        start = time.monotonic()
        siblings, error = module.discover_sibling_scripts(
            tmp_dir, tmp_dir / "craftflow_hook_selfcheck.py", timeout=1
        )
        elapsed = time.monotonic() - start
    finally:
        module.Path.glob = real_glob

    if elapsed >= 5:
        fail(name, f"discover_sibling_scripts did not return promptly when the glob stalled; took {elapsed:.1f}s")
        return
    if siblings is not None:
        fail(name, f"expected siblings=None when discovery times out; got {siblings}")
        return
    if not error or "timed out" not in error.lower():
        fail(name, f"expected a distinct 'timed out' diagnosis; got: {error!r}")
        return
    ok(name)


def test_selfcheck_main_emits_distinct_warning_when_discovery_times_out(tmp_dir: Path) -> None:
    # Companion to the above: main()'s end-to-end behavior when discovery
    # itself times out must still be silent-on-block (exit 0, per the
    # design's always-exit-0 constraint) but NOT silent on stdout -- a hung
    # discovery must not look identical to a genuinely clean run.
    name = "hook-selfcheck/main-warns-when-discovery-times-out"
    tmp_dir.mkdir(parents=True)
    real_checker = SCRIPTS / "craftflow_hook_selfcheck.py"
    scratch_checker = tmp_dir / "craftflow_hook_selfcheck.py"
    scratch_checker.write_text(real_checker.read_text(encoding="utf-8"), encoding="utf-8")
    module = _load_selfcheck_module()
    real_glob = module.Path.glob

    def hanging_glob(self, pattern):
        time.sleep(30)
        return real_glob(self, pattern)

    module.Path.glob = hanging_glob
    old_stdout = sys.stdout
    captured = io.StringIO()
    try:
        # Call main()'s logic directly (in-process, via the loaded module) --
        # a real subprocess run would need its own hung-glob simulation,
        # which isn't reproducible across a process boundary. This still
        # exercises main()'s real discovery-error branch end-to-end,
        # including the print() it performs on that branch.
        original_file = module.__file__
        module.__file__ = str(scratch_checker)
        sys.stdout = captured
        try:
            exit_code = module.main()
        finally:
            module.__file__ = original_file
            sys.stdout = old_stdout
    finally:
        module.Path.glob = real_glob

    stdout_text = captured.getvalue()
    if exit_code != 0:
        fail(name, f"expected main() to still return 0 when discovery times out; got {exit_code}")
        return
    if not stdout_text.strip():
        fail(
            name,
            "expected a distinct warning when discovery itself times out; stdout was silent "
            "(indistinguishable from a genuinely clean run)",
        )
        return
    if '"additionalContext"' not in stdout_text:
        fail(name, f"expected hookSpecificOutput.additionalContext in stdout; got: {stdout_text!r}")
        return
    if "timed out" not in stdout_text.lower():
        fail(name, f"expected the discovery-timeout diagnosis to name itself distinctly; got: {stdout_text!r}")
        return
    ok(name)


def _selfcheck_scratch_checker_with_constants(
    tmp_dir: Path, per_script_timeout: float, time_budget: float
) -> Path:
    """Like _selfcheck_scratch_checker, but rewrites the real checker's own
    PER_SCRIPT_TIMEOUT_SECONDS / SELFCHECK_TIME_BUDGET_SECONDS constants to
    small values in the scratch copy, so timing-sensitive regression tests
    run in a few seconds instead of minutes while still exercising the exact
    same algorithm at proportionally the same scale. Does not add any sibling
    scripts -- callers add their own."""
    tmp_dir.mkdir(parents=True)
    real_checker = SCRIPTS / "craftflow_hook_selfcheck.py"
    content = real_checker.read_text(encoding="utf-8")
    content, n_per_script = re.subn(
        r"PER_SCRIPT_TIMEOUT_SECONDS = \d+(\.\d+)?",
        f"PER_SCRIPT_TIMEOUT_SECONDS = {per_script_timeout}",
        content,
    )
    content, n_budget = re.subn(
        r"SELFCHECK_TIME_BUDGET_SECONDS = \d+(\.\d+)?",
        f"SELFCHECK_TIME_BUDGET_SECONDS = {time_budget}",
        content,
    )
    if n_per_script != 1 or n_budget != 1:
        raise AssertionError(
            f"expected exactly one PER_SCRIPT_TIMEOUT_SECONDS assignment (found {n_per_script}) "
            f"and one SELFCHECK_TIME_BUDGET_SECONDS assignment (found {n_budget}) in "
            f"craftflow_hook_selfcheck.py -- constant name likely changed without updating this helper"
        )
    scratch_checker = tmp_dir / "craftflow_hook_selfcheck.py"
    scratch_checker.write_text(content, encoding="utf-8")
    return scratch_checker


def _selfcheck_write_slow_script(path: Path, module_name: str, sleep_seconds: float) -> None:
    path.write_text(
        f"# {module_name}: deliberately slow-but-valid import, used only by the "
        f"self-check truncation regression test\n"
        f"import time\n"
        f"time.sleep({sleep_seconds})\n"
        f"VALUE = 1\n",
        encoding="utf-8",
    )


def test_selfcheck_flushes_before_external_kill_when_sweep_runs_long(tmp_dir: Path) -> None:
    # REM-FIX (doubt-verifier BLOCKING): the checker's worst-case internal
    # budget (PER_SCRIPT_TIMEOUT_SECONDS * ~33 real siblings) can exceed the
    # registered SessionStart hook timeout (15s in hooks/hooks.json). With no
    # internal deadline, run_selfcheck batches ALL results in memory and only
    # emits a warning once, after the full loop returns -- so a handful of
    # merely-slow (not even broken) sibling imports is enough for an external
    # kill to silently discard already-detected real failures. Reproduces the
    # doubt-verifier's own scenario: several slow-but-succeeding siblings + one
    # genuinely broken sibling + an external kill shorter than the full
    # (unmitigated) sweep would take.
    name = "hook-selfcheck/flushes-before-external-kill"
    per_script_timeout = 5  # unchanged hard cap; well above sleep_each below
    time_budget = 3  # small, scaled-down internal soft deadline for this test
    scratch_checker = _selfcheck_scratch_checker_with_constants(tmp_dir, per_script_timeout, time_budget)
    # "a_broken" sorts alphabetically before the "b_slow_*" scripts, so its
    # failure is captured near-instantly, before the slow scripts consume the
    # time budget.
    _selfcheck_write_broken_script(tmp_dir / "craftflow_scratch_a_broken.py")
    n_slow = 6
    sleep_each = 1.5
    for i in range(n_slow):
        _selfcheck_write_slow_script(
            tmp_dir / f"craftflow_scratch_b_slow_{i}.py", f"craftflow_scratch_b_slow_{i}", sleep_each
        )
    # The full unmitigated sweep (pre-fix code, no internal deadline) would
    # take roughly n_slow * sleep_each seconds (~9s) -- comfortably longer
    # than the external kill below. The fixed code's internal time_budget (3s)
    # stops the sweep after ~2-3 checks (~3-4.5s), comfortably shorter than the
    # external kill. This gap between "fixed" and "unmitigated" wall time is
    # what proves the internal deadline (not luck) is what saves the run.
    external_kill_seconds = 6
    result = subprocess.run(
        ["timeout", str(external_kill_seconds), sys.executable, str(scratch_checker)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        fail(
            name,
            f"external kill (timeout {external_kill_seconds}s) fired before the checker's "
            f"internal deadline could flush already-collected results; exit code "
            f"{result.returncode}, stdout={result.stdout!r}",
        )
        return
    if not result.stdout.strip():
        fail(
            name,
            "expected a flushed warning naming the already-detected broken script and noting "
            "the truncated sweep; stdout was empty",
        )
        return
    if "craftflow_scratch_a_broken.py" not in result.stdout:
        fail(name, f"expected the already-detected broken script in the flushed warning; got: {result.stdout!r}")
        return
    if '"additionalContext"' not in result.stdout:
        fail(name, f"expected hookSpecificOutput.additionalContext in stdout; got: {result.stdout!r}")
        return
    if "truncat" not in result.stdout.lower():
        fail(
            name,
            f"expected a note that the sweep was truncated (checked N of total siblings) in "
            f"the flushed warning; got: {result.stdout!r}",
        )
        return
    ok(name)


def test_selfcheck_never_imports_hooklib_directly() -> None:
    name = "hook-selfcheck/never-imports-hooklib"
    path = SCRIPTS / "craftflow_hook_selfcheck.py"
    if not path.exists():
        fail(name, f"craftflow_hook_selfcheck.py not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "import craftflow_hooklib" in content or "from craftflow_hooklib" in content:
        fail(name, "checker must not import craftflow_hooklib directly in its own process")
        return
    if "craftflow_hooklib" not in content:
        # Not mentioned anywhere (not even in a docstring/comment) -- nothing
        # left to check for dynamic-import indirection.
        ok(name)
        return
    # craftflow_hooklib is mentioned but not via a direct "import"/"from" above --
    # also guard against dynamic-import indirection, including an aliased
    # `import importlib as X` binding (`X.import_module(...)`) and
    # getattr-based attribute lookup (`getattr(importlib, "import_module")(...)`),
    # both of which evade a bare "importlib.import_module" substring check.
    #
    # Deliberate scope choice (not full AST parsing): this is a structural
    # guardrail over a single small, hand-authored Phase 2 file -- not
    # adversarial user input -- and no other check in this test file uses
    # `ast`. A regex-based check covering the two concretely-identified
    # bypasses (aliased import, getattr indirection) is proportionate; a
    # further indirection such as
    # `from importlib import import_module as im; im("craftflow_hooklib")`
    # would still evade this check. cf:shortcut: regex-based dynamic-import
    # detection, not full AST; upgrade to `ast.walk` if a real bypass of this
    # kind is ever found in a future Phase 2 implementation.
    importlib_aliases = {"importlib"} | set(re.findall(r"import\s+importlib\s+as\s+(\w+)", content))
    for alias in importlib_aliases:
        if re.search(rf"\b{re.escape(alias)}\s*\.\s*import_module\b", content):
            fail(name, f"checker must not dynamically import craftflow_hooklib via {alias}.import_module")
            return
    if "__import__" in content:
        fail(name, "checker must not dynamically import craftflow_hooklib via __import__")
        return
    if re.search(r'getattr\(\s*\w+\s*,\s*["\']import_module["\']\s*\)', content):
        fail(name, 'checker must not dynamically import craftflow_hooklib via getattr(..., "import_module")')
        return
    ok(name)


def _subprocess_run_arg_lists(content: str) -> list[str]:
    """Return the literal text of every argv list/tuple passed as
    subprocess.run's first positional argument -- either inline
    (subprocess.run(["python3", ...])) or via a bare-name variable that was
    itself assigned a list/tuple literal earlier in the file
    (CMD = ["python3", ...]; subprocess.run(CMD, ...)). Capture is
    best-effort (not a real parser) but only the FIRST element of each
    returned string is ever inspected by the caller, so imprecise capture of
    later elements (e.g. across a nested call like str(x)) doesn't matter.
    """
    inline = re.findall(r"subprocess\.run\(\s*(\[[^\]]*\]|\([^)]*\))", content)
    var_names = re.findall(r"subprocess\.run\(\s*(\w+)\s*[,)]", content)
    via_var = []
    for var in var_names:
        m = re.search(rf"\b{re.escape(var)}\s*=\s*(\[[^\]]*\]|\([^)]*\))", content)
        if m:
            via_var.append(m.group(1))
    return inline + via_var


def _first_argv_element_matches(content: str, argv_lists: list[str], value_pattern: str) -> bool:
    """True if any captured argv list/tuple's FIRST element matches
    value_pattern -- either directly (a literal expression) or indirectly via
    a bare identifier that was itself assigned a value matching value_pattern
    elsewhere in the file (e.g. INTERPRETER = "python3")."""
    for argv in argv_lists:
        # NOTE: no trailing \b after value_pattern -- both patterns this is
        # called with end in a non-word character (a closing quote for
        # "python3", a dot-separated identifier for sys.executable's own
        # \b already anchoring it internally), so a trailing \b would sit
        # between two non-word characters and never match. The quotes (or
        # the dotted-attribute pattern itself) already delimit the literal
        # precisely enough without it.
        if re.match(rf"[\[\(]\s*{value_pattern}", argv):
            return True
        ident_match = re.match(r"[\[\(]\s*(\w+)\s*[,\)\]]", argv)
        if ident_match:
            ident = ident_match.group(1)
            if re.search(rf"\b{re.escape(ident)}\s*=\s*{value_pattern}", content):
                return True
    return False


def test_selfcheck_resolves_bare_python3_not_sys_executable() -> None:
    name = "hook-selfcheck/resolves-bare-python3"
    path = SCRIPTS / "craftflow_hook_selfcheck.py"
    if not path.exists():
        fail(name, f"craftflow_hook_selfcheck.py not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    # Both the accept and reject checks require actual call-site usage as the
    # first element of a real subprocess.run() argv list/tuple -- not just
    # presence anywhere in the file. A bare content-substring check would
    # false-FAIL on legitimate prose (e.g. this checker's own module
    # docstring explaining "never sys.executable") and false-PASS on a stray
    # comment or unused variable. Accepts either a direct literal or a
    # same-file constant/variable assigned that value and then used as the
    # argv list's first element (covers both `INTERPRETER = "python3"` and
    # `CMD = ["python3", ...]; subprocess.run(CMD, ...)` idioms, plus the
    # tuple form `subprocess.run(("python3", ...))`).
    argv_lists = _subprocess_run_arg_lists(content)
    if _first_argv_element_matches(content, argv_lists, r"sys\.executable"):
        fail(name, "checker must resolve the interpreter as bare python3 on PATH, not sys.executable")
        return
    if not _first_argv_element_matches(content, argv_lists, r'"python3"'):
        fail(
            name,
            "checker's subprocess.run call does not appear to pass a literal "
            "'python3' (or a constant/variable assigned to it) as its first argument",
        )
        return
    ok(name)


def test_hooks_json_registers_selfcheck_sessionstart() -> None:
    name = "hooks/selfcheck-sessionstart-registered"
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    if not path.exists():
        fail(name, f"hooks.json not found at {path}")
        return
    hooks = json.loads(path.read_text(encoding="utf-8"))
    session_start_hooks = hooks.get("hooks", {}).get("SessionStart", [])
    all_scripts = []
    for entry in session_start_hooks:
        for h in entry.get("hooks", []):
            all_scripts.append(h.get("command", ""))
    joined = " ".join(all_scripts)
    if "craftflow_hook_selfcheck" not in joined:
        fail(name, "hooks.json missing SessionStart hook: craftflow_hook_selfcheck")
        return
    ok(name)


def test_root_hooks_json_registers_selfcheck_sessionstart() -> None:
    # Root hooks.json (distinct from hooks/hooks.json) wires Cursor sessions
    # via craftflow_cursor_adapter.py -- see Codebase Reality Check for the
    # verified shape and adapter contract.
    name = "hooks/root-cursor-selfcheck-sessionstart-registered"
    path = PLUGIN_ROOT / "hooks.json"
    if not path.exists():
        fail(name, f"root hooks.json not found at {path}")
        return
    hooks = json.loads(path.read_text(encoding="utf-8"))
    session_start_entries = hooks.get("hooks", {}).get("sessionStart", [])
    matching = [
        entry.get("command", "")
        for entry in session_start_entries
        if "craftflow_hook_selfcheck" in entry.get("command", "")
    ]
    if not matching:
        fail(name, "root hooks.json missing a sessionStart entry for craftflow_hook_selfcheck")
        return
    command = matching[0]
    if "craftflow_cursor_adapter" not in command:
        fail(name, f"expected the new entry to route through craftflow_cursor_adapter.py; got: {command!r}")
        return
    if "--event SessionStart" not in command:
        fail(name, f"expected the new entry to pass --event SessionStart; got: {command!r}")
        return
    ok(name)


def test_selfcheck_internal_budget_stays_under_registered_hook_timeout() -> None:
    # REM-FIX (doubt-verifier cycle-2 BLOCKING #2): no test or shared constant
    # tied the checker's own internal worst-case budget
    # (DISCOVERY_TIMEOUT_SECONDS + SELFCHECK_TIME_BUDGET_SECONDS +
    # PER_SCRIPT_TIMEOUT_SECONDS) to hooks/hooks.json's registered SessionStart
    # timeout for craftflow_hook_selfcheck.py. If either side changes in the
    # future without a matching edit to the other, the exact BLOCKING bug
    # from doubt-verify cycle 1 (internal budget exceeding the registered
    # kill) can silently reappear with zero test failure to catch it.
    name = "hook-selfcheck/internal-budget-under-registered-timeout"
    module = _load_selfcheck_module()
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    if not path.exists():
        fail(name, f"hooks.json not found at {path}")
        return
    hooks = json.loads(path.read_text(encoding="utf-8"))
    session_start_hooks = hooks.get("hooks", {}).get("SessionStart", [])
    registered_timeout = None
    for entry in session_start_hooks:
        for h in entry.get("hooks", []):
            if "craftflow_hook_selfcheck" in h.get("command", ""):
                registered_timeout = h.get("timeout")
    if registered_timeout is None:
        fail(name, "could not find a registered timeout for craftflow_hook_selfcheck in hooks/hooks.json")
        return
    worst_case = (
        module.DISCOVERY_TIMEOUT_SECONDS
        + module.SELFCHECK_TIME_BUDGET_SECONDS
        + module.PER_SCRIPT_TIMEOUT_SECONDS
    )
    min_margin_seconds = 1  # sane minimum buffer for process startup/shutdown overhead
    if worst_case + min_margin_seconds > registered_timeout:
        fail(
            name,
            f"internal worst-case budget ({worst_case}s = discovery "
            f"{module.DISCOVERY_TIMEOUT_SECONDS}s + sweep {module.SELFCHECK_TIME_BUDGET_SECONDS}s + "
            f"one script {module.PER_SCRIPT_TIMEOUT_SECONDS}s) leaves less than {min_margin_seconds}s "
            f"margin under the registered hook timeout ({registered_timeout}s) in hooks/hooks.json",
        )
        return
    ok(name)


def test_hooks_json_registers_new_hooks() -> None:
    name = "hooks/new-hooks-registered"
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    if not path.exists():
        fail(name, f"hooks.json not found at {path}")
        return
    hooks = json.loads(path.read_text(encoding="utf-8"))
    pre_hooks = hooks.get("hooks", {}).get("PreToolUse", [])
    hook_scripts = []
    for entry in pre_hooks:
        for h in entry.get("hooks", []):
            hook_scripts.append(h.get("command", ""))
    all_scripts = " ".join(hook_scripts)
    if "craftflow_sdd_cache_pre" not in all_scripts:
        fail(name, "hooks.json missing PreToolUse hook: craftflow_sdd_cache_pre")
        return
    if "craftflow_memory_protect_pre" in all_scripts:
        fail(name, "hooks.json must not have craftflow_memory_protect_pre in PreToolUse (hook was removed)")
        return
    ok(name)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    print("craftflow_hook_unit_tests: running")
    print()

    with tempfile.TemporaryDirectory(prefix="craftflow_hook_test_") as tmpdir:
        tmp = Path(tmpdir)

        print("[ memory-protect-pre ]")
        test_memory_protect_pre_ignores_non_craftflow_files(tmp / "m1")
        test_memory_protect_pre_masks_craftflow_file(tmp / "m2")
        test_memory_protect_pre_ignores_non_read_tool(tmp / "m3")
        test_memory_protect_pre_empty_stdin(tmp / "m4")

        print()
        print("[ sdd-cache-pre ]")
        test_sdd_cache_pre_ignores_non_webfetch(tmp / "s1")
        test_sdd_cache_pre_no_cache_on_fresh_url(tmp / "s2")
        test_sdd_cache_pre_rejects_cache_without_validators(tmp / "s3")

        print()
        print("[ sdd-cache-post ]")
        test_sdd_cache_post_ignores_non_webfetch(tmp / "p1")
        test_sdd_cache_post_writes_entry_with_etag(tmp / "p2")
        test_sdd_cache_post_skips_entry_without_freshness_headers(tmp / "p3")

        print()
        print("[ memory-protect-restore ]")
        test_memory_protect_restore_triggers_on_subagent_stop(tmp / "r1")

        print()
        print("[ pretooluse-guard ]")
        test_pretooluse_guard_blocks_memory_write_without_permit(tmp / "g1")
        test_pretooluse_guard_allows_memory_write_with_permit(tmp / "g2")

        print()
        print("[ pretooluse-bash-guard ]")
        test_bash_guard_blocks_relative_traversal_escaping_cwd(tmp / "b1")
        test_bash_guard_blocks_absolute_path_outside_cwd(tmp / "b2")
        test_bash_guard_allows_command_within_cwd(tmp / "b3")
        test_bash_guard_allows_non_destructive_command(tmp / "b4")
        test_bash_guard_allows_unverifiable_dynamic_path(tmp / "b5")
        test_bash_guard_ignores_non_bash_tool(tmp / "b6")

        print()
        print("[ pretooluse-bash-guard: widened vocabulary + in-cwd ]")
        test_bash_guard_blocks_rm_rf_dot_in_cwd(tmp / "b7")
        test_bash_guard_blocks_rm_rf_star_in_cwd(tmp / "b8")
        test_bash_guard_blocks_rm_rf_dotgit_in_cwd(tmp / "b9")
        test_bash_guard_blocks_rm_rf_critical_child_packages(tmp / "b10")
        test_bash_guard_allows_rm_in_noncritical_subdir(tmp / "b11")
        test_bash_guard_blocks_git_clean_force(tmp / "b12")
        test_bash_guard_allows_git_clean_without_force_flag(tmp / "b13")
        test_bash_guard_blocks_git_reset_hard(tmp / "b14")
        test_bash_guard_allows_git_reset_without_hard(tmp / "b15")
        test_bash_guard_blocks_git_push_force(tmp / "b16")
        test_bash_guard_allows_git_push_without_force(tmp / "b17")
        test_bash_guard_allows_git_push_force_with_lease(tmp / "b18")
        test_bash_guard_blocks_mv_escaping_cwd(tmp / "b19")
        test_bash_guard_blocks_dd_of_traversal(tmp / "b20")
        test_bash_guard_allows_dd_of_in_cwd(tmp / "b21")
        test_bash_guard_blocks_chmod_escaping_cwd(tmp / "b22")
        test_bash_guard_blocks_find_exec_rm(tmp / "b23")
        test_bash_guard_allows_find_delete_in_cwd(tmp / "b24")
        test_bash_guard_blocks_shred_escaping_cwd(tmp / "b25")
        test_bash_guard_blocks_truncate_escaping_cwd(tmp / "b26")
        test_bash_guard_regression_lock_release_still_allowed(tmp / "b27")
        test_bash_guard_regression_memory_finalize_clear_still_allowed(tmp / "b28")

        print()
        print("[ hooklib shared-helper (white-box) ]")
        test_hooklib_resolve_confinement_allows_within_cwd(tmp / "h1")
        test_hooklib_resolve_confinement_denies_outside_cwd_no_worktree(tmp / "h2")
        test_hooklib_resolve_confinement_allows_within_worktree_outside_cwd(tmp / "h3")
        test_hooklib_resolve_confinement_denies_outside_both(tmp / "h4")
        test_hooklib_command_has_traversal_true_for_dotdot_substitution()
        test_hooklib_command_has_traversal_false_for_lock_dir_var()
        test_hooklib_command_has_traversal_true_for_bare_wildcard()
        test_hooklib_extract_redirect_targets_finds_simple_redirect()
        test_hooklib_extract_redirect_targets_finds_tee_target()
        test_hooklib_extract_redirect_targets_empty_for_no_redirect()
        test_hooklib_matches_permit_shape_true_for_exact_documented_command()
        test_hooklib_matches_permit_shape_false_for_different_printf_args()
        test_hooklib_matches_permit_shape_false_for_heredoc()
        test_hooklib_matches_permit_shape_false_for_substitution_in_value()

        print()
        print("[ hook-selfcheck ]")
        # NOTE: wrapped in try/except (deviation from the plan's literal bare
        # calls) because _selfcheck_scratch_checker() unconditionally
        # read_text()s the not-yet-created craftflow_hook_selfcheck.py during
        # Phase 1 (RED), raising an uncaught FileNotFoundError that would
        # otherwise abort main() before the pre-existing structural tests
        # below run. This converts that crash into a clean fail() line.
        try:
            test_selfcheck_detects_broken_script_red(tmp / "hc1")
        except FileNotFoundError as exc:
            fail("hook-selfcheck/detects-broken-script-RED", f"setup crashed: {exc}")
        try:
            test_selfcheck_silent_on_clean_scripts_green(tmp / "hc2")
        except FileNotFoundError as exc:
            fail("hook-selfcheck/silent-when-clean-GREEN", f"setup crashed: {exc}")
        try:
            test_selfcheck_warns_on_suspiciously_low_sibling_count(tmp / "hc3")
        except FileNotFoundError as exc:
            fail("hook-selfcheck/warns-on-suspiciously-low-sibling-count", f"setup crashed: {exc}")
        test_selfcheck_isolates_per_script_subprocess_errors(tmp / "hc4")
        test_selfcheck_flushes_before_external_kill_when_sweep_runs_long(tmp / "hc5")
        test_selfcheck_discovery_itself_bounded_by_timeout(tmp / "hc6")
        test_selfcheck_main_emits_distinct_warning_when_discovery_times_out(tmp / "hc7")

    print()
    print("[ structural ]")
    test_anti_rationalization_tables_present()
    test_doubt_verifier_agent_present()
    test_intent_interview_skill_present()
    test_router_dispatches_doubt_verify()
    test_router_dispatches_intent_interview()
    test_hooks_json_registers_new_hooks()
    test_hooks_json_registers_bash_guard()
    test_selfcheck_never_imports_hooklib_directly()
    test_selfcheck_resolves_bare_python3_not_sys_executable()
    test_hooks_json_registers_selfcheck_sessionstart()
    test_root_hooks_json_registers_selfcheck_sessionstart()
    test_selfcheck_internal_budget_stays_under_registered_hook_timeout()
    test_workflow_id_script_present()
    test_statusline_script_present()
    test_router_uses_workflow_id_helper()

    print()
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\ncraftflow_hook_unit_tests: FAIL ({len(_errors)} errors, {_passes} passed)", file=sys.stderr)
        return 1

    print(f"craftflow_hook_unit_tests: OK ({_passes} passed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
