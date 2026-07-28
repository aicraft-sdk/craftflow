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
import craftflow_pretooluse_bash_guard as bash_guard  # noqa: E402
import craftflow_pretooluse_guard as pretooluse_guard  # noqa: E402

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
# Phase 4: pretooluse_guard.py protected-path extension, Bash-write
# inspection, Edit/Write worktree confinement, hooks.json Bash registration.
# See docs/plans/2026-07-28-craftflow-guardrail-hardening-plan.md, Phase 4.
# ---------------------------------------------------------------------------

def test_pretooluse_guard_denies_edit_write_to_memory_finalize(tmp_dir: Path) -> None:
    name = "pretooluse-guard/denies-edit-write-to-memory-finalize"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    state = tmp_dir / ".craftflow" / "state"
    state.mkdir(parents=True)
    target = state / ".memory-finalize"
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for an unpermitted Edit/Write to .memory-finalize; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_allows_edit_write_to_workflow_json(tmp_dir: Path) -> None:
    # Regression flow 4: workflow JSON is deliberately NOT in the Edit/Write
    # -gated protected-memory set (Durable Decision) -- the router's own
    # routine mid-workflow Write() updates must stay allowed.
    name = "pretooluse-guard/allows-edit-write-to-workflow-json"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    wf_dir = tmp_dir / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    target = wf_dir / "wf-test.json"
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(target)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for a routine workflow-JSON Write; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_heredoc_write_to_memory_md(tmp_dir: Path) -> None:
    name = "pretooluse-guard/denies-bash-heredoc-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": "cat <<'EOF' > .craftflow/state/project/activeContext.md\ninjected\nEOF"
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash heredoc write to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_tee_write_to_memory_md(tmp_dir: Path) -> None:
    name = "pretooluse-guard/denies-bash-tee-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "echo x | tee .craftflow/state/project/patterns.md"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash tee write to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_oneliner_write_to_memory_md(tmp_dir: Path) -> None:
    name = "pretooluse-guard/denies-bash-python-oneliner-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"open('.craftflow/state/project/progress.md', "
                "'w').write('x')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash python one-liner write to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_write_to_workflow_json(tmp_dir: Path) -> None:
    name = "pretooluse-guard/denies-bash-write-to-workflow-json"
    project_root = tmp_dir / "project"
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "wf-test.json").write_text('{"workflow_uuid":"wf-test"}', encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "echo '{}' > .craftflow/state/workflows/wf-test.json"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash redirect write to a workflow JSON artifact; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_heredoc_write_to_workflow_json(tmp_dir: Path) -> None:
    # Advisory 2 (fresh review pass 1): workflow JSON gets the same 3-shape
    # coverage as the .md files, not a narrower single-shape check.
    name = "pretooluse-guard/denies-bash-heredoc-write-to-workflow-json"
    project_root = tmp_dir / "project"
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "wf-test.json").write_text('{"workflow_uuid":"wf-test"}', encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": "cat <<'EOF' > .craftflow/state/workflows/wf-test.json\n{}\nEOF"
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash heredoc write to a workflow JSON artifact; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_tee_write_to_workflow_json(tmp_dir: Path) -> None:
    # Advisory 2 (fresh review pass 1).
    name = "pretooluse-guard/denies-bash-tee-write-to-workflow-json"
    project_root = tmp_dir / "project"
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "wf-test.json").write_text('{"workflow_uuid":"wf-test"}', encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "echo '{}' | tee .craftflow/state/workflows/wf-test.json"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash tee write to a workflow JSON artifact; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_oneliner_write_to_workflow_json(tmp_dir: Path) -> None:
    # Advisory 2 (fresh review pass 1).
    name = "pretooluse-guard/denies-bash-python-oneliner-write-to-workflow-json"
    project_root = tmp_dir / "project"
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "wf-test.json").write_text('{"workflow_uuid":"wf-test"}', encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"open('.craftflow/state/workflows/wf-test.json', "
                "'w').write('{}')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash python one-liner write to a workflow JSON artifact; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_allows_bash_permit_write_shape(tmp_dir: Path) -> None:
    # Regression flow 2.
    name = "pretooluse-guard/allows-bash-permit-write-shape"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "printf '%s' 'wf-test-1234' > .craftflow/state/.memory-finalize"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"expected allow for the one documented permit-write shape; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_permit_write_wrong_shape(tmp_dir: Path) -> None:
    name = "pretooluse-guard/denies-bash-permit-write-wrong-shape"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": "printf '%s\\ninjected' 'wf-test-1234' > .craftflow/state/.memory-finalize"
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a different-shaped printf write to .memory-finalize; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_permit_write_compound_command(tmp_dir: Path) -> None:
    # This guard's own verdict concerns only protected-path writes -- the
    # unrelated `rm -rf /tmp/x` subcommand is bash_guard.py's concern (a
    # separate hook, independently firing on every Bash call). Assert only
    # this guard's own output: the memory-finalize-write concern specifically
    # allows here since the OTHER subcommand matches the documented shape.
    name = "pretooluse-guard/denies-bash-permit-write-compound-command"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": "rm -rf /tmp/x; printf '%s' 'wf-test-1234' > .craftflow/state/.memory-finalize"
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(
            name,
            f"expected this guard's own verdict to allow (permit shape matched on the "
            f"memory-finalize subcommand; the unrelated rm subcommand is bash_guard.py's "
            f"concern); got: {out!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_allows_bash_unrelated_command(tmp_dir: Path) -> None:
    name = "pretooluse-guard/allows-bash-unrelated-command"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "git status"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected no regression to ordinary Bash usage; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_worktree_confinement_denies_outside(tmp_dir: Path) -> None:
    name = "pretooluse-guard/edit-write-worktree-confinement-denies-outside"
    project_root = tmp_dir / "project"
    worktree = tmp_dir / "worktree-sibling"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    worktree.mkdir(parents=True)
    outside.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, str(worktree.resolve()))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((outside / "notes.md").resolve())},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for an Edit/Write target outside both cwd and worktree_path; got: {out!r}")
        return
    if "worktree-confinement" not in out:
        fail(name, f"expected a distinct 'worktree-confinement' deny reason; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_worktree_confinement_allows_inside_worktree(tmp_dir: Path) -> None:
    name = "pretooluse-guard/edit-write-worktree-confinement-allows-inside-worktree"
    project_root = tmp_dir / "project"
    worktree = tmp_dir / "worktree-sibling"
    project_root.mkdir(parents=True)
    worktree.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, str(worktree.resolve()))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((worktree / "scratch.md").resolve())},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for a target inside worktree_path though outside cwd (proves the union); got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_worktree_confinement_degrades_when_no_workflow_json(tmp_dir: Path) -> None:
    name = "pretooluse-guard/worktree-confinement-degrades-when-no-workflow-json"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((project_root / "notes.md").resolve())},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow (cwd-only degradation, no workflow JSON, no exception); got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_confinement_allows_workflow_json_when_worktree_path_stale(tmp_dir: Path) -> None:
    # Regression flow 4, exact realistic condition (fresh review pass 1
    # BLOCKING): worktree_path SET to a different, stale-looking sibling
    # path -- proves TRUE union semantics for the Edit/Write confinement
    # path specifically, not just inherited from Phase 3's bash-guard proof.
    name = "pretooluse-guard/edit-write-confinement-allows-workflow-json-when-worktree-path-stale"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    stale_worktree = tmp_dir / ".claude" / "worktrees" / "wf-stale-test"
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, str(stale_worktree))
    target = wf_dir / "wf-test.json"
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str(target.resolve())},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"regression flow 4 must stay allowed even with a stale/different worktree_path set; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_bash_permit_write_allowed_when_worktree_path_stale(tmp_dir: Path) -> None:
    # Regression flow 2, exact realistic condition (fresh review pass 1
    # BLOCKING) -- independent of, and in addition to, Phase 3's identical
    # proof against bash_guard.py's own confinement check for the same
    # command.
    name = "pretooluse-guard/bash-permit-write-allowed-when-worktree-path-stale"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    stale_worktree = tmp_dir / ".claude" / "worktrees" / "wf-stale-test"
    _write_workflow_json_fixture(project_root, str(stale_worktree))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "printf '%s' 'wf-test-1234' > .craftflow/state/.memory-finalize"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"regression flow 2 must stay allowed from pretooluse_guard.py's own permit-shape check even with a stale/different worktree_path set; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_allows_benign_redirect_to_dev_null(tmp_dir: Path) -> None:
    # Fresh review pass 2, BLOCKING: proves the Bash-write redirect check is
    # scoped to protected paths only -- /dev/null is not a protected path.
    # Real documented shape: skills/ai-first-setup/SKILL.md:292.
    name = "pretooluse-guard/allows-benign-redirect-to-dev-null"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    stale_worktree = tmp_dir / ".claude" / "worktrees" / "wf-stale-test"
    _write_workflow_json_fixture(project_root, str(stale_worktree))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "python3 -m json.tool feature_list.json > /dev/null && echo done"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for a benign '> /dev/null' redirect; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_allows_benign_stderr_redirect_to_dev_null(tmp_dir: Path) -> None:
    # Fresh review pass 2, BLOCKING. Real documented shape:
    # skills/craftflow-router/SKILL.md:270.
    name = "pretooluse-guard/allows-benign-stderr-redirect-to-dev-null"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    stale_worktree = tmp_dir / ".claude" / "worktrees" / "wf-stale-test"
    _write_workflow_json_fixture(project_root, str(stale_worktree))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": 'mkdir "$LOCK_DIR" 2>/dev/null'},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for a benign '2>/dev/null' redirect; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX (guardrail-hardening plan, Phase 3+4 doubt-verify): 5 live-verified
# bugs across craftflow_pretooluse_guard.py and craftflow_pretooluse_bash_guard.py.
# ---------------------------------------------------------------------------

def test_pretooluse_guard_denies_bash_permit_write_noncanonical_absolute_spelling(tmp_dir: Path) -> None:
    # CRITICAL 1: matches_memory_finalize_permit_shape() was called with the
    # EXTRACTED redirect target as `permit_path_str`, instead of the literal
    # documented constant -- making the 4th AND-condition (target ==
    # permit_path_str) tautologically true for ANY spelling that resolves to
    # the file. An absolute-path spelling (not the documented literal
    # ".craftflow/state/.memory-finalize") must be DENIED, not permit-shape-
    # matched.
    name = "pretooluse-guard/denies-bash-permit-write-noncanonical-absolute-spelling"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    abs_target = str((project_root / ".craftflow" / "state" / ".memory-finalize").resolve())
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": f"printf '%s' 'wf-test-1234' > {abs_target}"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a non-canonical absolute-path spelling of .memory-finalize (permit-shape must not tautologically match any spelling); got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_heredoc_write_to_memory_md(tmp_dir: Path) -> None:
    # CRITICAL 2: a heredoc-fed python script (`python3 - <<'EOF' ...
    # open(...).write(...) ... EOF`) never contains a `-c` token, and the
    # heredoc BODY is a separate newline-delimited subcommand chunk (once
    # split_subcommands() splits on "\n") from the invoking `python3 -
    # <<'EOF'` line -- so the old `"-c" in tokens`-gated, per-subcommand scan
    # could never see it. Zero targets detected, zero deny, previously.
    name = "pretooluse-guard/denies-bash-python-heredoc-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 - <<'EOF'\n"
                "open('.craftflow/state/project/patterns.md', 'w').write('injected')\n"
                "EOF"
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a heredoc-fed python script writing to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_path_write_text_to_memory_md(tmp_dir: Path) -> None:
    # HIGH 1: _OPEN_CALL_RE only matched a literal quoted string as open()'s
    # first positional arg -- pathlib.Path(...).write_text(...), a different
    # API for the same file-write effect, evaded detection entirely.
    name = "pretooluse-guard/denies-bash-python-path-write-text-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"from pathlib import Path; "
                "Path('.craftflow/state/project/patterns.md').write_text('injected')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Path(...).write_text(...) write to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_oneliner_via_env_prefix_write_to_memory_md(tmp_dir: Path) -> None:
    # HIGH 2: command_name = os.path.basename(tokens[0]) only inspected the
    # FIRST token -- `env python3 -c "..."` or `sudo python3 -c "..."`
    # resolved command_name to "env"/"sudo", silently bypassing the
    # python-oneliner check even though "-c" is present in tokens.
    name = "pretooluse-guard/denies-bash-python-oneliner-via-env-prefix-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "env python3 -c \"open('.craftflow/state/project/patterns.md', "
                "'w').write('injected')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for an env-prefixed python one-liner write to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_handles_workflow_payload_race_in_wf_uuid_lookup(tmp_dir: Path) -> None:
    # HIGH 3: of the two structurally identical latest_workflow_payload()
    # calls in _handle_edit_write, the confinement-check call (inside
    # _edit_write_escapes_confinement) is wrapped in try/except by its
    # caller, but the SECOND call (populating wf_uuid for the deny/log
    # payload on the worktree-confinement deny path) had none.
    # latest_workflow_payload() can raise FileNotFoundError on a stat-race
    # (workflow JSON file deleted between glob() and .stat()), crashing
    # main() before the deny is ever emitted -- an uncaught crash fails OPEN.
    name = "pretooluse-guard/handles-workflow-payload-race-in-wf-uuid-lookup"
    project_root = tmp_dir / "project"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    outside.mkdir(parents=True)

    call_count = {"n": 0}

    def _fake_latest_workflow_payload():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {}
        raise FileNotFoundError("workflow json vanished mid-stat")

    original = pretooluse_guard.latest_workflow_payload
    old_env = {k: os.environ.get(k) for k in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
    os.environ["CLAUDE_PROJECT_DIR"] = str(project_root)
    os.environ["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    pretooluse_guard.latest_workflow_payload = _fake_latest_workflow_payload
    buf = io.StringIO()
    old_stdout = sys.stdout
    crashed_exc = None
    result = None
    try:
        sys.stdout = buf
        try:
            data = {"cwd": str(project_root.resolve())}
            mode = {"memoryWrites": "block"}
            tool_input = {"file_path": str(outside / "escape.txt")}
            result = pretooluse_guard._handle_edit_write(data, mode, tool_input)
        except Exception as exc:  # the exact crash HIGH 3 warns about
            crashed_exc = exc
    finally:
        sys.stdout = old_stdout
        pretooluse_guard.latest_workflow_payload = original
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    if crashed_exc is not None:
        fail(name, f"_handle_edit_write crashed instead of degrading wf_uuid to None: {crashed_exc!r}")
        return
    out = buf.getvalue().strip()
    if result != 0:
        fail(name, f"expected _handle_edit_write to return 0; got {result!r}")
        return
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected the worktree-confinement deny to still fire despite the wf_uuid lookup raising; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_os_system_write_to_memory_md(tmp_dir: Path) -> None:
    # REM-FIX (doubt-verify cycle 1): the prior python-write detection only
    # recognized open(...)/Path(...).write_text(...) call shapes -- an
    # UNBOUNDED set of other python write-adjacent mechanisms inside the
    # exact same `python3 -c "..."` shape bypassed it completely. Live-
    # verified before this fix: os.system('printf x > <protected path>')
    # silently wrote the protected file.
    name = "pretooluse-guard/denies-bash-python-os-system-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"import os; os.system('printf x > "
                ".craftflow/state/project/patterns.md')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash python os.system() write to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_subprocess_run_write_to_workflow_json(tmp_dir: Path) -> None:
    # REM-FIX (doubt-verify cycle 1): also confirmed bypassing --
    # subprocess.run(['bash', '-c', 'printf x > <protected path>']).
    name = "pretooluse-guard/denies-bash-python-subprocess-run-write-to-workflow-json"
    project_root = tmp_dir / "project"
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "wf-test.json").write_text('{"workflow_uuid":"wf-test"}', encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"import subprocess; subprocess.run(['bash', '-c', "
                "'printf x > .craftflow/state/workflows/wf-test.json'])\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash python subprocess.run() write to a workflow JSON artifact; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_shutil_copy_write_to_memory_md(tmp_dir: Path) -> None:
    # REM-FIX (doubt-verify cycle 1): also confirmed bypassing --
    # shutil.copy(src, dest) with dest a protected path.
    name = "pretooluse-guard/denies-bash-python-shutil-copy-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"import shutil; shutil.copy('/tmp/injected.txt', "
                "'.craftflow/state/project/patterns.md')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash python shutil.copy() write to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_os_rename_write_to_memory_md(tmp_dir: Path) -> None:
    # REM-FIX (doubt-verify cycle 1): also confirmed bypassing --
    # os.rename(src, dest) with dest a protected path.
    name = "pretooluse-guard/denies-bash-python-os-rename-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"import os; os.rename('/tmp/injected.txt', "
                "'.craftflow/state/project/patterns.md')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash python os.rename() write to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_allows_bash_python_dynamic_dispatch_write_to_memory_md(tmp_dir: Path) -> None:
    # DISCLOSED, ACCEPTED GAP (not fixed by this REM-FIX): this guard's
    # python-write detection is pattern-matching against literal text
    # shapes (`os.system(`, `subprocess.run(`, `open(`, etc.) -- it cannot
    # and does not attempt to catch a write reached via dynamic attribute
    # dispatch (getattr(os, 'sys' + 'tem')(...)), string concatenation,
    # exec()/eval()-wrapped code, ctypes, ftplib, or any of the countless
    # other ways arbitrary Python can execute a shell command or write a
    # file. This test asserts the CURRENT (allow) behavior deliberately, so
    # a future reader sees this boundary was a conscious choice, not an
    # oversight -- see the module-level LIMITATIONS disclosure in
    # craftflow_pretooluse_guard.py.
    name = "pretooluse-guard/allows-bash-python-dynamic-dispatch-write-to-memory-md-disclosed-gap"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"import os; getattr(os, 'sys' + 'tem')"
                "('printf x > .craftflow/state/project/patterns.md')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"expected ALLOW (disclosed gap: dynamic dispatch bypasses text-pattern detection); got deny: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_allows_bash_python_subprocess_run_harmless_mention_of_protected_path(tmp_dir: Path) -> None:
    # REM-FIX (doubt-verify cycle 2, Problem 1 -- over-blocking false
    # positive): live-verified before this fix -- a suspicious-mechanism
    # marker (subprocess.run() calling `ls`, nothing destructive) combined
    # with a protected-path string that only appears inside an UNRELATED
    # print() statement was denied, even though no write to the path ever
    # occurs. The marker and the path literal must co-occur within the SAME
    # python statement to count as a violation -- they're in different
    # statements here (separated by `;`), so this must now ALLOW.
    name = "pretooluse-guard/allows-bash-python-subprocess-run-harmless-mention-of-protected-path"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"import subprocess; subprocess.run(['ls']); "
                "print('.craftflow/state/project/patterns.md is a cool file')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"expected ALLOW (marker and protected-path mention are in different statements, no actual write); got deny: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_import_alias_os_system_write_to_memory_md(tmp_dir: Path) -> None:
    # REM-FIX (doubt-verify cycle 2, Problem 2 -- import-alias bypass):
    # live-verified before this fix -- `import os as o; o.system(...)`
    # defeated the literal `os.system(` marker regex entirely and ALLOWED
    # the write. Alias bindings from `import X as Y` must also be matched.
    name = "pretooluse-guard/denies-bash-python-import-alias-os-system-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"import os as o; o.system('printf x > "
                ".craftflow/state/project/patterns.md')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for an import-aliased `o.system()` write to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_from_import_system_write_to_memory_md(tmp_dir: Path) -> None:
    # REM-FIX (doubt-verify cycle 2, Problem 2 -- from-import bypass):
    # live-verified before this fix -- `from os import system;
    # system(...)` also defeated the literal marker regex and ALLOWED the
    # write. Bindings from `from X import Y` must also be matched.
    name = "pretooluse-guard/denies-bash-python-from-import-system-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"from os import system; system('printf x > "
                ".craftflow/state/project/patterns.md')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a `from os import system` write to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_allows_bash_python_variable_reference_then_call_write_to_memory_md_disclosed_gap(tmp_dir: Path) -> None:
    # DISCLOSED, ACCEPTED GAP (explicitly OUT OF SCOPE this round, per the
    # user's own direction -- "one more targeted round, not a full
    # rewrite"): storing a function reference in an arbitrary variable and
    # calling it later (`func = os.system; func(...)`) requires real AST
    # analysis to trace the binding, not regex/statement-proximity
    # matching -- this is NOT fixed here. This test asserts the CURRENT
    # (allow) behavior deliberately, so a future reader sees this boundary
    # was a conscious choice, not an oversight -- see the module-level
    # LIMITATIONS disclosure in craftflow_pretooluse_guard.py.
    name = "pretooluse-guard/allows-bash-python-variable-reference-then-call-write-to-memory-md-disclosed-gap"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"import os; func = os.system; "
                "func('printf x > .craftflow/state/project/patterns.md')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"expected ALLOW (disclosed gap: function-reference-then-call-via-variable requires AST analysis); got deny: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_multiline_os_system_write_to_memory_md(tmp_dir: Path) -> None:
    # REM-FIX (final round): `_split_statement_like_chunks()` split naive on
    # raw `;`/`\n` with ZERO paren/bracket-depth awareness -- an ordinary
    # MULTI-LINE `os.system(...)` call (exactly the shape a formatter like
    # `black` would produce, not an adversarial construction) silently
    # bypassed the whole statement-proximity check: the marker
    # (`os.system(`) landed in one "chunk" and the protected-path literal
    # landed in the NEXT chunk once the naive splitter cut on the newlines
    # embedded inside the call's own still-open parens. Live-verified
    # bypass before this fix -- the single-line form of the identical call
    # was already correctly denied.
    name = "pretooluse-guard/denies-bash-python-multiline-os-system-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"import os; os.system(\n"
                "    'printf pwned > ' + '.craftflow/state/project/activeContext.md'\n"
                ")\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a multi-line (black-formatter-shaped) os.system() write to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_multiline_subprocess_run_write_to_workflow_json(tmp_dir: Path) -> None:
    # Same class of bug as above, proven against a DIFFERENT bracket shape --
    # a `subprocess.run([...])` call whose list ARGUMENTS are each wrapped
    # onto their own line (also a realistic `black` output shape). The
    # marker (`subprocess.run(`) and the protected-path literal must still
    # co-occur once bracket-depth tracking correctly treats the whole
    # multi-line `[...]`/`(...)` construct as ONE statement chunk.
    name = "pretooluse-guard/denies-bash-python-multiline-subprocess-run-write-to-workflow-json"
    project_root = tmp_dir / "project"
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "wf-test.json").write_text('{"workflow_uuid":"wf-test"}', encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"import subprocess; subprocess.run([\n"
                "    'bash',\n"
                "    '-c',\n"
                "    'printf pwned > .craftflow/state/workflows/wf-test.json',\n"
                "])\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a multi-line subprocess.run([...]) write to a workflow JSON artifact; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_python_backslash_continued_os_system_write_to_memory_md(tmp_dir: Path) -> None:
    # Same class of bug, proven against BACKSLASH line-continuation rather
    # than bracket nesting -- a single python statement whose `os.system(`
    # call is split across two physical lines via a trailing `\` rather
    # than relying on the call's own open paren to justify the line break.
    # The naive splitter's raw `\n` boundary check has no way to know this
    # newline is a continuation, not a statement end.
    name = "pretooluse-guard/denies-bash-python-backslash-continued-os-system-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 -c \"import os; os.system('printf pwned > ' + \\\n"
                "    '.craftflow/state/project/patterns.md')\""
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a backslash-continued os.system() write to a memory .md file; got: {out!r}")
        return
    ok(name)


def test_hooks_json_registers_pretooluse_guard_on_bash() -> None:
    name = "hooks/pretooluse-guard-registered-on-bash"
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
    if "craftflow_pretooluse_guard" not in commands:
        fail(name, "PreToolUse Bash matcher does not invoke craftflow_pretooluse_guard.py")
        return
    if "craftflow_pretooluse_bash_guard" not in commands:
        fail(name, "PreToolUse Bash matcher no longer invokes craftflow_pretooluse_bash_guard.py -- both must fire independently")
        return
    ok(name)


# ---------------------------------------------------------------------------
# Phase 5: hook-mode.json protectedWrites wiring.
# See docs/plans/2026-07-28-craftflow-guardrail-hardening-plan.md, Phase 5.
#
# Fake-plugin-root technique (Task 5.1): construct a temp directory
# mirroring the plugin's required shape (<tmp>/plugin_root/config/
# hook-mode.json with the desired test values -- no scripts/ subdirectory
# needed, plugin_config_dir() only ever reads plugin_root() / "config"),
# then point CLAUDE_PLUGIN_ROOT at the fake root. The real hook script is
# still invoked from the real SCRIPTS path via run_hook(); only
# load_mode()'s config lookup resolves to the fake root.
# ---------------------------------------------------------------------------

def test_pretooluse_guard_bash_write_denied_when_protected_writes_block(tmp_dir: Path) -> None:
    name = "pretooluse-guard/bash-write-denied-when-protected-writes-block"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text(
        json.dumps({"protectedWrites": "block"}), encoding="utf-8"
    )
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "echo x | tee .craftflow/state/project/patterns.md"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash protected-path write when protectedWrites=block; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_bash_write_audited_not_denied_when_protected_writes_audit(tmp_dir: Path) -> None:
    name = "pretooluse-guard/bash-write-audited-not-denied-when-protected-writes-audit"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text(
        json.dumps({"protectedWrites": "audit"}), encoding="utf-8"
    )
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "echo x | tee .craftflow/state/project/patterns.md"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow (audit-only, logged not blocked) when protectedWrites=audit; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_memory_writes_toggle_independent_of_protected_writes(tmp_dir: Path) -> None:
    # Proves the two toggles don't cross-gate each other: protectedWrites=
    # block (governs the NEW Bash-write-protected-path decision) must have
    # zero effect on memoryWrites=audit's own pre-existing gating of the
    # Edit/Write direct memory-path check.
    name = "pretooluse-guard/memory-writes-toggle-independent-of-protected-writes"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text(
        json.dumps({"protectedWrites": "block", "memoryWrites": "audit"}), encoding="utf-8"
    )
    project_root = tmp_dir / "project"
    target = project_root / ".craftflow" / "state" / "project" / "activeContext.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Active Context\n", encoding="utf-8")
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(
            name,
            "expected memoryWrites=audit to allow the pre-existing Edit/Write "
            f"memory-path check regardless of protectedWrites=block; got: {out!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_real_plugin_config_now_blocks_bash_writes_by_default(tmp_dir: Path) -> None:
    # Regression test (Task 5.1, 4th bullet): re-runs Phase 4's own
    # test_pretooluse_guard_denies_bash_heredoc_write_to_memory_md scenario
    # using the REAL plugin-root convention (CLAUDE_PLUGIN_ROOT=PLUGIN_ROOT),
    # proving Task 5.2's real config-default flip actually lands -- not just
    # the fake-root technique above.
    name = "pretooluse-guard/real-plugin-config-now-blocks-bash-writes-by-default"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": "cat <<'EOF' > .craftflow/state/project/activeContext.md\ninjected\nEOF"
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected the real plugin config's protectedWrites default to deny "
            f"this Bash heredoc write; got: {out!r}",
        )
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX (2 HIGH findings): load_mode()'s fallback dicts must fail closed
# (protectedWrites: "block", not "audit"), and unrecognized protectedWrites
# values must be distinguishable in the log from an intentional "audit"
# choice. See docs/plans/2026-07-28-craftflow-guardrail-hardening-plan.md.
# ---------------------------------------------------------------------------

def test_load_mode_fallback_defaults_protected_writes_to_block_when_file_missing(tmp_dir: Path) -> None:
    name = "hooklib/load-mode-fallback-defaults-protected-writes-block-missing-file"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    # Deliberately do NOT create hook-mode.json -- exercises load_mode()'s
    # missing-file fallback branch.
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "echo x | tee .craftflow/state/project/patterns.md"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected fail-closed deny (protectedWrites fallback must default "
            f"to block) when hook-mode.json is missing; got: {out!r}",
        )
        return
    ok(name)


def test_load_mode_fallback_defaults_protected_writes_to_block_when_file_corrupt(tmp_dir: Path) -> None:
    name = "hooklib/load-mode-fallback-defaults-protected-writes-block-corrupt-file"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text("{not valid json", encoding="utf-8")
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "echo x | tee .craftflow/state/project/patterns.md"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected fail-closed deny (protectedWrites fallback must default "
            f"to block) when hook-mode.json is corrupt/unparseable; got: {out!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_unrecognized_protected_writes_value_still_allows_but_logs_distinct_decision(
    tmp_dir: Path,
) -> None:
    name = "pretooluse-guard/unrecognized-protected-writes-value-logs-distinct-decision"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text(
        json.dumps({"protectedWrites": "blocked"}), encoding="utf-8"
    )
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "echo x | tee .craftflow/state/project/patterns.md"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(
            name,
            "expected allow (an unrecognized protectedWrites value must still "
            f"degrade to audit/allow, not fail-closed); got: {out!r}",
        )
        return
    log_path = project_root / ".craftflow" / "state" / "craftflow-hook-events.log"
    if not log_path.exists():
        fail(name, f"expected log file {log_path} to exist after hook run")
        return
    log_lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    matching = [line for line in log_lines if "audit-unrecognized-config-value" in line]
    if not matching:
        fail(
            name,
            "expected a log_event entry with decision 'audit-unrecognized-config-value' "
            "for a typo'd protectedWrites value, distinct from an intentional 'audit' choice",
        )
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX (build-craftflow-guardrail-harden, doubt-verify cycle 1): the
# `mode.get("memoryWrites") == "block"` check governing the pre-existing
# Edit/Write memory-path decision had the identical unvalidated-typo problem
# `protectedWrites` was just fixed for above -- a typo'd value (e.g. "Block",
# capital B) silently collapsed to the exact same log_event `decision:
# "audit"` as an intentional audit choice, with zero distinguishing signal.
# Live-verified before this fix: {"memoryWrites": "Block"} allowed an
# Edit/Write to a protected memory .md file with no warning at all. Extends
# the SAME enum-validation + distinguishing-log-decision pattern (shared via
# `_resolve_write_gate_decision()`) to `memoryWrites` -- gating BEHAVIOR is
# unchanged (still degrades to audit/allow on an unrecognized value; the
# fallback default in load_mode() for memoryWrites stays "audit", untouched).
# ---------------------------------------------------------------------------

def test_pretooluse_guard_memory_writes_unrecognized_value_still_allows_but_logs_distinct_decision(
    tmp_dir: Path,
) -> None:
    name = "pretooluse-guard/memory-writes-unrecognized-value-logs-distinct-decision"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text(
        json.dumps({"memoryWrites": "Block"}), encoding="utf-8"
    )
    project_root = tmp_dir / "project"
    target = project_root / ".craftflow" / "state" / "project" / "activeContext.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Active Context\n", encoding="utf-8")
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(
            name,
            "expected allow (an unrecognized memoryWrites value must still "
            f"degrade to audit/allow, not fail-closed); got: {out!r}",
        )
        return
    log_path = project_root / ".craftflow" / "state" / "craftflow-hook-events.log"
    if not log_path.exists():
        fail(name, f"expected log file {log_path} to exist after hook run")
        return
    log_lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    matching = [line for line in log_lines if "audit-unrecognized-config-value" in line]
    if not matching:
        fail(
            name,
            "expected a log_event entry with decision 'audit-unrecognized-config-value' "
            "for a typo'd memoryWrites value, distinct from an intentional 'audit' choice",
        )
        return
    ok(name)


def test_pretooluse_guard_memory_write_audited_not_denied_when_memory_writes_audit(tmp_dir: Path) -> None:
    # Regression check: explicit memoryWrites="audit" must still degrade to
    # allow (audit-only, logged not blocked) exactly as before this fix.
    name = "pretooluse-guard/memory-write-audited-not-denied-when-memory-writes-audit"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text(
        json.dumps({"memoryWrites": "audit"}), encoding="utf-8"
    )
    project_root = tmp_dir / "project"
    target = project_root / ".craftflow" / "state" / "project" / "activeContext.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Active Context\n", encoding="utf-8")
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"expected allow (audit-only, logged not blocked) when memoryWrites=audit; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_protected_writes_toggle_independent_of_memory_writes(tmp_dir: Path) -> None:
    # Reverse-direction toggle-independence check (mirrors
    # test_pretooluse_guard_memory_writes_toggle_independent_of_protected_writes
    # above): memoryWrites="block" (governs the pre-existing Edit/Write
    # memory-path check) must have zero effect on protectedWrites="audit"'s
    # own gating of the NEW Bash-write-protected-path decision.
    name = "pretooluse-guard/protected-writes-toggle-independent-of-memory-writes"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text(
        json.dumps({"memoryWrites": "block", "protectedWrites": "audit"}), encoding="utf-8"
    )
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "echo x | tee .craftflow/state/project/patterns.md"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(
            name,
            "expected protectedWrites=audit to allow the Bash-write-protected-path "
            f"check regardless of memoryWrites=block; got: {out!r}",
        )
        return
    ok(name)


def test_learn_distiller_uses_tools_key_not_allowed_tools() -> None:
    name = "learn-distiller/uses-tools-key-not-allowed-tools"
    path = PLUGIN_ROOT / "agents" / "learn-distiller.md"
    if not path.exists():
        fail(name, f"learn-distiller.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "\ntools: " not in content:
        fail(name, "learn-distiller.md frontmatter missing 'tools: ' key")
        return
    if "\nallowed-tools: " in content:
        fail(name, "learn-distiller.md frontmatter still uses legacy 'allowed-tools:' key")
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
# --- REM-FIX: Phase 2 review+hunt findings (7 real live-verified bypasses) ---
# ---------------------------------------------------------------------------

def test_bash_guard_blocks_git_dash_capital_c_flag_before_subcommand(tmp_dir: Path) -> None:
    # CRITICAL 1: `git -C <dir> reset --hard` previously bypassed detection
    # entirely because `_is_destructive_git` treated `rest[0]` ("-C") as the
    # subcommand instead of scanning past git's global flags -- and even if
    # detected, the destructive target must resolve against the -C
    # directory, not cwd.
    name = "pretooluse-bash-guard/blocks-git-dash-capital-c-flag-before-subcommand"
    cwd_dir = tmp_dir / "cwd"
    outside_dir = tmp_dir / "outside"
    cwd_dir.mkdir(parents=True)
    outside_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": f"git -C {outside_dir.resolve()} reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'git -C <outside dir> reset --hard'; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_git_clean_long_form_force(tmp_dir: Path) -> None:
    # CRITICAL 2: `_is_destructive_git`'s old `token.startswith("-f")` check
    # misses the long-form `--force` flag entirely (second char is "-", not
    # "f").
    name = "pretooluse-bash-guard/blocks-git-clean-long-form-force"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "git clean --force"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'git clean --force' (long-form force flag); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_dd_bare_stdout_redirect_without_of(tmp_dir: Path) -> None:
    # CRITICAL 3: `dd if=/dev/zero > <target>` with NO `of=` token at all was
    # completely invisible to `_dd_target` (which only ever looked for
    # `of=`) -- the whole subcommand wasn't even recognized as destructive.
    name = "pretooluse-bash-guard/blocks-dd-bare-stdout-redirect-without-of"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "dd if=/dev/zero > ../../../etc/passwd bs=1 count=10"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a bare 'dd ... > target' redirect with no of=; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_find_execdir_rm(tmp_dir: Path) -> None:
    # HIGH 4: `_is_destructive_find` only ever matched the exact `-exec`
    # token, missing `-execdir`/`-ok`/`-okdir`.
    name = "pretooluse-bash-guard/blocks-find-execdir-rm"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "find /etc -execdir rm {} \\;"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'find -execdir rm' targeting an escaping search path; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_descendant_of_critical_child(tmp_dir: Path) -> None:
    # HIGH 5: `_is_in_cwd_critical` only exact-matched `cwd / child` -- a
    # DESCENDANT of a critical top-level child (e.g. `packages/agent-cli`)
    # slipped through with zero log.
    name = "pretooluse-bash-guard/blocks-rm-rf-descendant-of-critical-child"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf packages/agent-cli"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf packages/agent-cli' (descendant of critical child); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_mv_target_directory_flag(tmp_dir: Path) -> None:
    # HIGH 6: `_positional_targets` blindly skipped any token starting with
    # "-", including `--target-directory=<path>` -- mv's real destination
    # was silently hidden from confinement checking entirely.
    name = "pretooluse-bash-guard/blocks-mv-target-directory-flag"
    cwd_dir = tmp_dir / "cwd"
    outside_dir = tmp_dir / "outside"
    cwd_dir.mkdir(parents=True)
    outside_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": f"mv secret.txt --target-directory={outside_dir.resolve()}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'mv ... --target-directory=<outside>'; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_mv_target_directory_short_attached_flag(tmp_dir: Path) -> None:
    # Finding 1 (REM-FIX doubt-verify cycle 1): GNU mv's ATTACHED short-option
    # form `-t<dir>` (no space, no `=` -- e.g. `mv -t/tmp secret.txt`) carries
    # a real destination path exactly like `--target-directory=<dir>` does,
    # but `_positional_targets` only ever matched the long `=`-joined form --
    # the attached short form was silently skipped as a bare flag, hiding the
    # real destination from confinement checking entirely (zero detection).
    # Bare `-t <dir>` as two SEPARATE space-separated tokens already worked
    # via the generic positional fallback -- only the attached, no-space form
    # was broken.
    name = "pretooluse-bash-guard/blocks-mv-target-directory-short-attached-flag"
    cwd_dir = tmp_dir / "cwd"
    outside_dir = tmp_dir / "outside"
    cwd_dir.mkdir(parents=True)
    outside_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": f"mv -t{outside_dir.resolve()} secret.txt"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'mv -t<outside> secret.txt' (attached short-option form); got: {out!r}")
        return
    ok(name)


def test_bash_guard_destructive_targets_falls_back_on_parse_exception(tmp_dir: Path) -> None:
    # HIGH 7, strengthened by Finding 2 (REM-FIX doubt-verify cycle 1): the
    # ORIGINAL version of this test only checked that `_destructive_targets`
    # returns a non-empty `paths` list -- it never proved the fallback
    # produces a genuine DENY end-to-end. git's destructive target is
    # implicit/structural (the repo at cwd itself), not a plain positional
    # path argument -- the generic positional-token model has no notion of
    # this, so tokens like "reset"/"HEAD~1" resolve as harmless relative
    # filenames and the command would silently ALLOW all the way through
    # main() despite being genuinely destructive. This test forces the exact
    # exception the codebase's own try/except induces and drives it all the
    # way through main() (not just the isolated helper function).
    name = "pretooluse-bash-guard/destructive-targets-falls-back-on-parse-exception"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)

    original = bash_guard._is_destructive_git

    def _boom(rest):
        raise ValueError("synthetic parse failure for HIGH 7 regression test")

    bash_guard._is_destructive_git = _boom
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "git reset --hard HEAD~1"},
    }
    original_stdin = sys.stdin
    original_stdout = sys.stdout
    sys.stdin = io.StringIO(json.dumps(payload))
    captured = io.StringIO()
    sys.stdout = captured
    try:
        exit_code = bash_guard.main()
    except Exception as exc:
        fail(name, f"expected main() to catch the parse exception, not propagate it; got: {exc!r}")
        return
    finally:
        bash_guard._is_destructive_git = original
        sys.stdin = original_stdin
        sys.stdout = original_stdout

    out = captured.getvalue().strip()
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected a genuine end-to-end DENY from main() when the git-shape "
            f"exception fallback fires (target must degrade to cwd itself, not a "
            f"silent ALLOW); got exit={exit_code} stdout={out!r}",
        )
        return
    ok(name)


def test_bash_guard_mv_falls_back_to_cwd_target_when_positional_targets_itself_raises(tmp_dir: Path) -> None:
    # Doubt-verify cycle 2, blocking finding: the non-structural-command
    # fallback in `_destructive_targets` called `_positional_targets(rest)`
    # AGAIN -- the exact same function whose earlier call is what may have
    # raised the exception in the first place. If `_positional_targets`
    # itself is the failure source, the fallback's own call re-raises the
    # identical uncaught exception, which propagates all the way through
    # main() and crashes the hook process -- an uncaught crash (non-zero,
    # non-2 exit) does not block the tool call per Claude Code's hook
    # exit-code semantics, so this is a silent fail-open ALLOW of a
    # genuinely destructive command. This test monkeypatches
    # `_positional_targets` ITSELF (not `_is_destructive_git`/`_dd_target`,
    # which are already covered by the cycle-1 regression tests above) and
    # proves `main()` still produces a genuine end-to-end DENY, not a
    # crash/propagated exception, for a SIMPLE_DESTRUCTIVE command (`mv`).
    name = "pretooluse-bash-guard/mv-falls-back-to-cwd-target-when-positional-targets-itself-raises"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)

    original = bash_guard._positional_targets

    def _boom(rest):
        raise ValueError("synthetic parse failure: _positional_targets itself is the failure source")

    bash_guard._positional_targets = _boom
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "mv secret.txt /tmp/outside/"},
    }
    original_stdin = sys.stdin
    original_stdout = sys.stdout
    sys.stdin = io.StringIO(json.dumps(payload))
    captured = io.StringIO()
    sys.stdout = captured
    try:
        exit_code = bash_guard.main()
    except Exception as exc:
        fail(
            name,
            "expected main() to catch the parse exception even when "
            f"_positional_targets itself is the failure source; got uncaught: {exc!r}",
        )
        return
    finally:
        bash_guard._positional_targets = original
        sys.stdin = original_stdin
        sys.stdout = original_stdout

    out = captured.getvalue().strip()
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected a genuine end-to-end DENY from main() when "
            f"_positional_targets itself raises (fallback must not re-call the "
            f"same failing function); got exit={exit_code} stdout={out!r}",
        )
        return
    ok(name)


def test_bash_guard_chmod_falls_back_to_cwd_target_when_positional_targets_itself_raises(tmp_dir: Path) -> None:
    # Doubt-verify cycle 2 finding, `chmod` case specifically -- the
    # existing docstring on `_destructive_targets` explicitly names `chmod`
    # as covered by "the existing generic fallback ... kept unchanged", but
    # that generic fallback is the same `_positional_targets` call that can
    # itself be the exception source. Proves the fix's uniform "." fallback
    # covers chmod too, not just mv/rm/shred/truncate.
    name = "pretooluse-bash-guard/chmod-falls-back-to-cwd-target-when-positional-targets-itself-raises"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)

    original = bash_guard._positional_targets

    def _boom(rest):
        raise ValueError("synthetic parse failure: _positional_targets itself is the failure source (chmod)")

    bash_guard._positional_targets = _boom
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "chmod -R 777 /etc"},
    }
    original_stdin = sys.stdin
    original_stdout = sys.stdout
    sys.stdin = io.StringIO(json.dumps(payload))
    captured = io.StringIO()
    sys.stdout = captured
    try:
        exit_code = bash_guard.main()
    except Exception as exc:
        fail(
            name,
            "expected main() to catch the parse exception even when "
            f"_positional_targets itself is the failure source (chmod); got uncaught: {exc!r}",
        )
        return
    finally:
        bash_guard._positional_targets = original
        sys.stdin = original_stdin
        sys.stdout = original_stdout

    out = captured.getvalue().strip()
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected a genuine end-to-end DENY from main() when "
            f"_positional_targets itself raises for chmod; got exit={exit_code} stdout={out!r}",
        )
        return
    ok(name)


def test_bash_guard_dd_falls_back_to_cwd_target_on_parse_exception(tmp_dir: Path) -> None:
    # Finding 2 continued (REM-FIX doubt-verify cycle 1): the same exception-
    # isolation fallback fires for `dd` commands too -- dd's overwrite target
    # (`of=<path>`) is likewise structural, not a generic positional path
    # argument. Forces the exact exception `_dd_target` can raise and proves
    # the fallback still denies end-to-end via main() rather than silently
    # ALLOWing (dd has no valid generic-positional-token interpretation
    # either -- its "target" only exists via the `of=` adapter).
    name = "pretooluse-bash-guard/dd-falls-back-to-cwd-target-on-parse-exception"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)

    original = bash_guard._dd_target

    def _boom(tokens):
        raise ValueError("synthetic parse failure for finding-2 dd regression test")

    bash_guard._dd_target = _boom
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "dd if=/dev/zero of=./scratch.img"},
    }
    original_stdin = sys.stdin
    original_stdout = sys.stdout
    sys.stdin = io.StringIO(json.dumps(payload))
    captured = io.StringIO()
    sys.stdout = captured
    try:
        exit_code = bash_guard.main()
    except Exception as exc:
        fail(name, f"expected main() to catch the parse exception, not propagate it; got: {exc!r}")
        return
    finally:
        bash_guard._dd_target = original
        sys.stdin = original_stdin
        sys.stdout = original_stdout

    out = captured.getvalue().strip()
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected a genuine end-to-end DENY from main() when the dd-shape "
            f"exception fallback fires (target must degrade to cwd itself, not a "
            f"silent ALLOW); got exit={exit_code} stdout={out!r}",
        )
        return
    ok(name)


# ---------------------------------------------------------------------------
# --- Phase 3: dynamic-target traversal fail-closed + worktree confinement ---
# ---------------------------------------------------------------------------
# docs/plans/2026-07-28-craftflow-guardrail-hardening-plan.md Phase 3: a
# dynamic ($/backtick) destructive target is denied only when a traversal
# literal or wildcard also appears in the command's construction (including
# inside $(...)); a bare dynamic target with neither is allowed. Worktree
# confinement denies any Bash destructive/redirect target outside
# {cwd} u {worktree_path} when worktree_path is set, degrading to cwd-only
# when absent/null. This is a TRUE union -- worktree_path only ever widens
# what cwd alone permits, it never narrows cwd's own coverage (fresh review
# pass 1, BLOCKING correction; see the plan's Fresh Review Resolution).

def _write_workflow_json_fixture(
    project_root: Path, worktree_path: str | None, wf_uuid: str = "wf-phase3-test"
) -> None:
    """Write a minimal workflow JSON artifact under project_root's own
    .craftflow/state/workflows/ -- hooklib.latest_workflow_payload() reads
    via CLAUDE_PROJECT_DIR (env), not the Bash payload's own "cwd" field, so
    the fixture must live at the project_root the test's env points at."""
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / f"{wf_uuid}.json").write_text(
        json.dumps({"workflow_uuid": wf_uuid, "worktree_path": worktree_path}),
        encoding="utf-8",
    )


def test_bash_guard_blocks_dynamic_target_with_traversal_substitution(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-dynamic-target-with-traversal-substitution"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(cwd_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": 'rm -rf "$(echo ../../..)"'},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a dynamic target with a traversal literal inside $(...); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_dynamic_target_with_bare_wildcard(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-dynamic-target-with-bare-wildcard"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(cwd_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf $TARGET *"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a dynamic target alongside a bare wildcard; got: {out!r}")
        return
    ok(name)


def test_bash_guard_regression_lock_release_still_allowed_phase3(tmp_dir: Path) -> None:
    # Regression flow 1, third confirmation at the exact phase that touches
    # this dynamic-target logic most directly.
    name = "pretooluse-bash-guard/regression-lock-release-still-allowed-phase3"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(cwd_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": 'rm -rf "$LOCK_DIR"'},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"regression flow 1 (lock release) must stay allowed, re-verified at Phase 3; got: {out!r}")
        return
    ok(name)


def test_bash_guard_worktree_confinement_denies_outside_worktree(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/worktree-confinement-denies-outside-worktree"
    project_root = tmp_dir / "project"
    worktree = tmp_dir / "worktree-sibling"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    worktree.mkdir(parents=True)
    outside.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, str(worktree.resolve()))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": f"rm -f {(outside / 'secret.txt').resolve()}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a target outside both cwd and worktree_path; got: {out!r}")
        return
    if "worktree-confinement" not in out:
        fail(name, f"expected a distinct 'worktree-confinement' deny reason; got: {out!r}")
        return
    ok(name)


def test_bash_guard_worktree_confinement_allows_inside_worktree_outside_cwd(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/worktree-confinement-allows-inside-worktree-outside-cwd"
    project_root = tmp_dir / "project"
    worktree = tmp_dir / "worktree-sibling"
    project_root.mkdir(parents=True)
    worktree.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, str(worktree.resolve()))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": f"rm -f {(worktree / 'scratch.txt').resolve()}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for a target inside worktree_path though outside cwd (proves the union, not cwd-only); got: {out!r}")
        return
    ok(name)


def test_bash_guard_worktree_confinement_degrades_when_no_workflow_json(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/worktree-confinement-degrades-when-no-workflow-json"
    project_root = tmp_dir / "project"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    outside.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": f"rm -f {(outside / 'secret.txt').resolve()}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected the existing cwd-only escape-cwd deny to still fire with no workflow JSON present, no exception; got: {out!r}")
        return
    ok(name)


def test_bash_guard_worktree_confinement_degrades_when_worktree_path_null(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/worktree-confinement-degrades-when-worktree-path-null"
    project_root = tmp_dir / "project"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    outside.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, None)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": f"rm -f {(outside / 'secret.txt').resolve()}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected cwd-only degradation (still deny) when worktree_path is null, no exception; got: {out!r}")
        return
    ok(name)


def test_bash_guard_worktree_confinement_allows_within_cwd_despite_different_set_worktree(tmp_dir: Path) -> None:
    # Fresh review pass 1, BLOCKING correction: this test previously asserted
    # deny; Behavior Contract rules 2 & 7's TRUE union means a target within
    # cwd alone is always allowed, regardless of a stale/different
    # worktree_path -- worktree_path only ever widens, never narrows.
    name = "pretooluse-bash-guard/worktree-confinement-allows-within-cwd-despite-different-set-worktree"
    project_root = tmp_dir / "project"
    worktree = tmp_dir / "worktree-sibling"
    project_root.mkdir(parents=True)
    worktree.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, str(worktree.resolve()))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "rm -f ./scratch.txt"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for a within-cwd target despite a different set worktree_path (true union); got: {out!r}")
        return
    ok(name)


def test_bash_guard_worktree_confinement_allows_memory_finalize_clear_when_worktree_path_stale(tmp_dir: Path) -> None:
    # Regression flow 3, exact realistic condition (fresh review pass 1
    # BLOCKING): worktree_path SET to a real-looking-but-nonexistent sibling
    # path -- proves the check never requires the worktree dir to exist.
    name = "pretooluse-bash-guard/worktree-confinement-allows-memory-finalize-clear-when-worktree-path-stale"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    stale_worktree = tmp_dir / ".claude" / "worktrees" / "wf-stale-test"
    state = project_root / ".craftflow" / "state"
    state.mkdir(parents=True)
    (state / ".memory-finalize").write_text("wf-phase3-test", encoding="utf-8")
    _write_workflow_json_fixture(project_root, str(stale_worktree))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "rm -f .craftflow/state/.memory-finalize"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"regression flow 3 must stay allowed even with a stale/different worktree_path set; got: {out!r}")
        return
    ok(name)


def test_bash_guard_worktree_confinement_allows_memory_finalize_permit_write_when_worktree_path_stale(tmp_dir: Path) -> None:
    # Regression flow 2, exact realistic condition (fresh review pass 1
    # BLOCKING). This is bash_guard.py's own redirect-target confinement
    # check -- independent of, and in addition to, pretooluse_guard.py's own
    # permit-shape check added in Phase 4.
    name = "pretooluse-bash-guard/worktree-confinement-allows-memory-finalize-permit-write-when-worktree-path-stale"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    stale_worktree = tmp_dir / ".claude" / "worktrees" / "wf-stale-test"
    _write_workflow_json_fixture(project_root, str(stale_worktree))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "printf '%s' 'wf-test-1234' > .craftflow/state/.memory-finalize"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"regression flow 2 must stay allowed from bash_guard.py's own redirect-confinement check even with a stale/different worktree_path set; got: {out!r}")
        return
    ok(name)


def test_bash_guard_denies_permit_write_noncanonical_absolute_spelling(tmp_dir: Path) -> None:
    # CRITICAL 1 (fix in BOTH files): matches_memory_finalize_permit_shape()
    # was called with the EXTRACTED redirect target as `permit_path_str`,
    # instead of the literal documented constant -- making the 4th
    # AND-condition (target == permit_path_str) tautologically true for ANY
    # spelling that resolves to the file. An absolute-path spelling must be
    # DENIED here too, not permit-shape-matched.
    name = "pretooluse-bash-guard/denies-permit-write-noncanonical-absolute-spelling"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    abs_target = str((project_root / ".craftflow" / "state" / ".memory-finalize").resolve())
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": f"printf '%s' 'wf-test-1234' > {abs_target}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a non-canonical absolute-path spelling of .memory-finalize (permit-shape must not tautologically match any spelling); got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_benign_redirect_to_dev_null(tmp_dir: Path) -> None:
    # Fresh review pass 2, BLOCKING: proves the redirect-confinement check is
    # scoped to protected paths only -- /dev/null is not a protected path, so
    # no confinement check ever runs against it, regardless of worktree_path.
    # Real documented shape: skills/ai-first-setup/SKILL.md:292.
    name = "pretooluse-bash-guard/allows-benign-redirect-to-dev-null"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    stale_worktree = tmp_dir / ".claude" / "worktrees" / "wf-stale-test"
    _write_workflow_json_fixture(project_root, str(stale_worktree))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "python3 -m json.tool feature_list.json > /dev/null && echo done"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for a benign '> /dev/null' redirect; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_benign_stderr_redirect_to_dev_null(tmp_dir: Path) -> None:
    # Real documented shape: skills/craftflow-router/SKILL.md:270.
    name = "pretooluse-bash-guard/allows-benign-stderr-redirect-to-dev-null"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    stale_worktree = tmp_dir / ".claude" / "worktrees" / "wf-stale-test"
    _write_workflow_json_fixture(project_root, str(stale_worktree))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": 'mkdir "$LOCK_DIR" 2>/dev/null'},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for a benign '2>/dev/null' redirect; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# --- REM-FIX: Phase 3 review+hunt findings (4 real live-verified bugs) ---
# ---------------------------------------------------------------------------

def test_bash_guard_blocks_protected_redirect_overwrite_when_confined_to_cwd(tmp_dir: Path) -> None:
    # CRITICAL 1: the protected-redirect check was gated behind `not
    # confined`, so it never even ran whenever cwd was the project root (the
    # common case) -- .craftflow/state/... always lives inside cwd there.
    # Live-verified: `echo PWNED > .craftflow/state/project/patterns.md`
    # (cwd=project root, no worktree) silently succeeded with zero log
    # entry. Confinement and protected-path-ness are two SEPARATE reasons to
    # deny -- a protected path must be denied regardless of whether it also
    # happens to be inside cwd.
    name = "pretooluse-bash-guard/blocks-protected-redirect-overwrite-when-confined-to-cwd"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "echo PWNED > .craftflow/state/project/patterns.md"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for a protected-path redirect overwrite confined "
            f"to cwd with no worktree (the common case); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_dd_of_dynamic_traversal_substitution(tmp_dir: Path) -> None:
    # CRITICAL 2: dd's branch hardcoded has_unresolvable=False for its `of=`
    # target, never calling looks_dynamic() on the captured value (unlike
    # the generic positional-target path, which calls looks_dynamic() on
    # every token). Live-verified:
    # `dd if=/dev/zero of=$(echo ../../etc/passwd) bs=1 count=1` was
    # silently allowed -- the captured `of=` value is unexpanded
    # substitution text that resolves as a harmless-looking in-cwd path
    # once shlex splits it.
    name = "pretooluse-bash-guard/blocks-dd-of-dynamic-traversal-substitution"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "dd if=/dev/zero of=$(echo ../../etc/passwd) bs=1 count=1"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for 'dd ... of=$(...)' capturing a traversal "
            f"literal via command substitution; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_find_dynamic_search_path_with_traversal_elsewhere(tmp_dir: Path) -> None:
    # Doubt-verify generalization gap: the prior REM-FIX fixed dd's `of=`
    # value to route through looks_dynamic() (CRITICAL 2 above), but never
    # generalized this to find's own captured search-path tokens --
    # `_find_search_paths()` hardcoded has_unresolvable=False unconditionally,
    # exactly the same bug shape dd had. Live-verified pre-fix: this exact
    # command was silently ALLOWED even though command_has_traversal_or_
    # wildcard() independently confirms the command contains a traversal
    # literal (in the unrelated `echo ../../etc` subcommand) -- the dynamic
    # search-path token itself has no embedded slashes, so it never escapes
    # cwd via the ordinary resolve_confinement() path either; only the
    # has_unresolvable+has_traversal_or_wildcard combination (Behavior
    # Contract rule 4) can catch it.
    name = "pretooluse-bash-guard/blocks-find-dynamic-search-path-with-traversal-elsewhere"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": 'find "$(echo XDYNAMIC)" -delete; echo ../../etc'},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for 'find $(...) -delete' with a traversal literal "
            f"elsewhere in the same command; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_allows_find_dynamic_search_path_without_traversal_elsewhere(tmp_dir: Path) -> None:
    # Behavior Contract rule 3: a bare dynamic target with NEITHER a
    # traversal literal nor a wildcard anywhere in the command's
    # construction stays allowed (fail-open on genuinely opaque-but-
    # unsuspicious dynamic paths) -- must not over-correct into denying
    # every dynamic find search path outright.
    name = "pretooluse-bash-guard/allows-find-dynamic-search-path-without-traversal-elsewhere"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": 'find "$(echo XDYNAMIC)" -delete'},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(
            name,
            "expected allow for a bare dynamic find search path with no "
            f"traversal literal or wildcard anywhere in the command; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_git_dynamic_dir_override_with_traversal_elsewhere(tmp_dir: Path) -> None:
    # Doubt-verify generalization gap: `_is_destructive_git()`'s dir_override
    # (captured from `git -C <dir>`/`--work-tree=<dir>`) is used as the
    # confinement target but the git branch of `_match_destructive_shape()`
    # hardcoded has_unresolvable=False unconditionally, never calling
    # looks_dynamic() on it -- the same bug shape dd had before its own
    # REM-FIX. Live-verified pre-fix:
    # `git -C $(echo ../../etc) reset --hard` was silently ALLOWED despite
    # command_has_traversal_or_wildcard() independently confirming a
    # traversal literal in the command's construction.
    name = "pretooluse-bash-guard/blocks-git-dynamic-dir-override-with-traversal-elsewhere"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": 'git -C "$(echo YDYNAMIC)" reset --hard; echo ../../etc'},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for 'git -C $(...) reset --hard' with a traversal "
            f"literal elsewhere in the same command; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_allows_git_dynamic_dir_override_without_traversal_elsewhere(tmp_dir: Path) -> None:
    # Behavior Contract rule 3, git side: a bare dynamic -C/--work-tree
    # override with neither a traversal literal nor a wildcard anywhere in
    # the command's construction stays allowed.
    name = "pretooluse-bash-guard/allows-git-dynamic-dir-override-without-traversal-elsewhere"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": 'git -C "$(echo YDYNAMIC)" reset --hard'},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(
            name,
            "expected allow for a bare dynamic git -C override with no "
            f"traversal literal or wildcard anywhere in the command; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_git_dash_capital_c_unquoted_fragmented_substitution_with_traversal(
    tmp_dir: Path,
) -> None:
    # REM-FIX continuation (router-reported, exact reproduction): shlex
    # FRAGMENTS an UNQUOTED `$(...)` substitution into separate tokens
    # (`$`, `(`, `echo`, `$x`, `)`) -- unlike the quoted form
    # (`"$(echo YDYNAMIC)"`, already covered above), which shlex keeps as
    # one token. `_find_git_subcommand()`'s `-C`-value-skip previously
    # advanced past exactly ONE token after `-C` (assuming a single-token
    # value), so the fragmented span's OWN tokens (starting with `(`) were
    # then scanned as if `(` were the git subcommand itself -- `(` matches
    # neither clean/reset/push, so `_is_destructive_git()` returned False
    # and the whole command was silently ALLOWED (live-reproduced pre-fix:
    # empty stdout, exit 0). Must deny once the fragmented span is
    # correctly consumed as a single dynamic value and subcommand-scanning
    # resumes at "reset" AFTER it, combined with `x=../../etc` supplying a
    # real traversal literal earlier in the same command text.
    name = "pretooluse-bash-guard/blocks-git-dash-c-unquoted-fragmented-substitution-with-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=../../etc; git -C $(echo $x) reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for 'x=../../etc; git -C $(echo $x) reset --hard' "
            f"(unquoted, fragmented $(...) substitution); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_allows_git_dash_capital_c_unquoted_fragmented_substitution_without_traversal(
    tmp_dir: Path,
) -> None:
    # Behavior Contract rule 3 + no-over-blocking regression check for the
    # fix above: a legitimate unquoted, fragmented `$(...)` -C override with
    # NO traversal literal or wildcard anywhere in the command must still be
    # allowed -- the subcommand ("reset --hard") is correctly identified as
    # destructive by the fix, but with no traversal present the dynamic
    # target stays in the existing fail-open "unverifiable" path, not denied.
    name = "pretooluse-bash-guard/allows-git-dash-c-unquoted-fragmented-substitution-without-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=/some/safe/path; git -C $(echo $x) reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(
            name,
            "expected allow for an unquoted, fragmented dynamic git -C "
            f"override with no traversal literal or wildcard anywhere; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_allows_git_dash_capital_c_unquoted_dynamic_var_no_traversal(
    tmp_dir: Path,
) -> None:
    # Router's own 3rd independent-re-verification scenario, verbatim: a
    # legitimate unquoted dynamic -C usage (single-token `$x`, not a
    # fragmented `$(...)` substitution) with no traversal anywhere must
    # still allow -- must not regress via over-blocking.
    name = "pretooluse-bash-guard/allows-git-dash-c-unquoted-dynamic-var-no-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=/some/safe/path; git -C $x status"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(
            name,
            "expected allow for 'git -C $x status' (non-destructive "
            f"subcommand, no traversal anywhere); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_find_unquoted_nested_substitution_with_traversal_elsewhere(
    tmp_dir: Path,
) -> None:
    # Router-requested double-check: does the SAME fragmentation gap affect
    # `find`'s search-path handling? Confirmed NOT exploitable in the same
    # way -- `_is_destructive_find()` detects destructiveness via `-delete`/
    # `-exec ... rm` token PRESENCE anywhere in `rest`, independent of
    # correctly parsing what precedes it, so an unquoted fragmented `$(...)`
    # search path does not hide the `-delete` flag the way it hid git's
    # subcommand token. This test proves that non-regression explicitly
    # (unquoted, nested substitution form, not just the quoted single-token
    # form already covered above) rather than leaving it merely inferred.
    name = "pretooluse-bash-guard/blocks-find-unquoted-nested-substitution-with-traversal-elsewhere"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "y=../../etc; find $(echo $y) -delete"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for 'y=../../etc; find $(echo $y) -delete' "
            f"(unquoted, nested substitution); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_git_work_tree_assignment_fragmented_substitution_with_traversal(
    tmp_dir: Path,
) -> None:
    # REM-FIX doubt-verify cycle 2, Bug 1: `--work-tree=<value>`
    # (assignment-style) never routed its value through fragmented-span
    # detection at all -- the assignment branch did a flat
    # `token.split("=", 1)` / `idx += 1` unconditionally, so an UNQUOTED
    # fragmented `$(...)` value (shlex splits `--work-tree=$(echo $x)` into
    # `["--work-tree=$", "(", "echo", "$x", ")"]`, five tokens fused/split
    # around the "=") left `idx` pointing at the span's own inner "(" token
    # on the NEXT loop iteration -- "(" matches neither clean/reset/push,
    # so `_is_destructive_git()` returned False and the whole command was
    # NOT EVEN RECOGNIZED as a destructive git command at all (live-verified
    # pre-fix: `x=../../etc; git --work-tree=$(echo $x) reset --hard` was
    # fully ALLOWED, empty stdout, exit 0).
    name = "pretooluse-bash-guard/blocks-git-work-tree-assignment-fragmented-substitution-with-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=../../etc; git --work-tree=$(echo $x) reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for 'x=../../etc; git --work-tree=$(echo $x) reset "
            f"--hard' (assignment-form, unquoted, fragmented); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_git_git_dir_assignment_fragmented_substitution_with_traversal(
    tmp_dir: Path,
) -> None:
    # Same Bug 1 shape, `--git-dir=` sibling flag.
    name = "pretooluse-bash-guard/blocks-git-git-dir-assignment-fragmented-substitution-with-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=../../etc; git --git-dir=$(echo $x) reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for 'x=../../etc; git --git-dir=$(echo $x) reset "
            f"--hard' (assignment-form, unquoted, fragmented); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_allows_git_work_tree_assignment_fragmented_substitution_without_traversal(
    tmp_dir: Path,
) -> None:
    # No-over-blocking regression check for the Bug 1 fix: a legitimate
    # unquoted, fragmented `--work-tree=$(...)` assignment-form value with
    # NO traversal literal or wildcard anywhere in the command must stay
    # allowed -- the subcommand ("reset --hard") is correctly identified as
    # destructive by the fix, but with no traversal present the dynamic
    # target stays in the existing fail-open "unverifiable" path.
    name = "pretooluse-bash-guard/allows-git-work-tree-assignment-fragmented-substitution-without-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=/some/safe/path; git --work-tree=$(echo $x) reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(
            name,
            "expected allow for an unquoted, fragmented --work-tree=$(...) "
            f"assignment value with no traversal literal or wildcard anywhere; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_git_dash_capital_c_doubly_nested_substitution_with_traversal(
    tmp_dir: Path,
) -> None:
    # REM-FIX doubt-verify cycle 2, Bug 2: `_dynamic_span_end()`'s paren-
    # depth counter did EXACT TOKEN EQUALITY against a bare `"("`/`")"`, but
    # shlex can FUSE adjacent punctuation into ONE multi-char token -- a
    # doubly-nested `$(echo $(echo $x))` tokenizes its two closing parens
    # into a single `"))"` token, which the old exact-equality check never
    # recognized as closing anything. Depth never returned to 0, so the
    # "unterminated" fallback fired and SWALLOWED THE REST OF THE COMMAND'S
    # TOKENS (including the real subcommand "reset --hard") into the span --
    # the whole command was no longer recognized as destructive at all
    # (live-verified pre-fix: `x=../../etc; git -C $(echo $(echo $x)) reset
    # --hard` was fully ALLOWED, empty stdout, exit 0).
    name = "pretooluse-bash-guard/blocks-git-dash-c-doubly-nested-substitution-with-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=../../etc; git -C $(echo $(echo $x)) reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for 'x=../../etc; git -C $(echo $(echo $x)) reset "
            f"--hard' (doubly-nested substitution); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_allows_git_dash_capital_c_doubly_nested_substitution_without_traversal(
    tmp_dir: Path,
) -> None:
    # No-over-blocking regression check for the Bug 2 fix: a legitimate
    # doubly-nested `$(echo $(echo $x))` -C override with NO traversal
    # literal or wildcard anywhere in the command must stay allowed.
    name = "pretooluse-bash-guard/allows-git-dash-c-doubly-nested-substitution-without-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=/some/safe/path; git -C $(echo $(echo $x)) reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(
            name,
            "expected allow for a doubly-nested $(echo $(echo $x)) -C "
            f"override with no traversal literal or wildcard anywhere; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_git_dash_capital_c_malformed_unterminated_substitution(
    tmp_dir: Path,
) -> None:
    # REM-FIX doubt-verify cycle 2, Bug 2 continuation: the same root cause
    # (paren-depth counter never reaching 0) also affects genuinely
    # malformed/unbalanced-paren input -- a missing closing paren means
    # depth never returns to 0 across the rest of the command's tokens,
    # which previously "consumed to the end, conservative" and silently
    # swallowed the real subcommand, failing OPEN (allowed). Must now fail
    # CLOSED: an unterminated span forces the command to be treated as
    # containing a genuinely destructive, unresolvable target requiring
    # denial, rather than un-recognizing an otherwise-destructive git
    # invocation just because span-detection itself could not parse it.
    name = "pretooluse-bash-guard/blocks-git-dash-c-malformed-unterminated-substitution"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "git -C $(echo $x reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny (fail CLOSED) for 'git -C $(echo $x reset --hard' "
            f"(missing closing paren, malformed/unterminated span); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_regression_lock_release_still_allowed_cycle2(tmp_dir: Path) -> None:
    # Re-verification (doubt-verify cycle 2): the non-negotiable regression
    # flow 1 (`rm -rf "$LOCK_DIR"`) must still be allowed after the
    # assignment-form-routing + paren-depth-scan changes above -- this
    # command shape doesn't touch git's dir-override parsing at all, but is
    # re-verified explicitly at this phase per the plan's own convention of
    # re-checking non-negotiable flows at every phase that touches the file.
    name = "pretooluse-bash-guard/regression-lock-release-still-allowed-cycle2"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": 'rm -rf "$LOCK_DIR"'},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'rm -rf \"$LOCK_DIR\"'; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# --- REM-FIX round 4 (3rd adversarial doubt-verify pass, live-verified) ---
# `-c key=value` fragment detection, fused-suffix handling after a closing
# paren/backtick, and multi-flag dynamic-taint accumulation.
# ---------------------------------------------------------------------------

def test_bash_guard_blocks_git_dash_c_key_equals_dynamic_value_with_traversal(tmp_dir: Path) -> None:
    # Bug 1 (full silent bypass, live-verified): shlex tokenizes
    # `foo=$(echo $x)` as ["foo=$", "(", "echo", "$x", ")"] -- the value
    # token is "foo=$" (a static "key=" prefix FUSED to the dynamic
    # fragment's start), never exactly "$" or backtick-prefixed. The old
    # `_dynamic_span_end()` only recognized a bare "$"/backtick-prefixed
    # token, so this value was never routed through fragment detection at
    # all -- the scanner then misread the literal "(" token immediately
    # after as the git subcommand itself, which matches neither
    # clean/reset/push, so the whole command was silently ALLOWED
    # (live-verified pre-fix: exit=0, empty stdout).
    name = "pretooluse-bash-guard/blocks-git-dash-c-key-equals-dynamic-value-with-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=../../etc; git -c foo=$(echo $x) reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for 'x=../../etc; git -c foo=$(echo $x) reset --hard' "
            f"(key=dynamic-value fragment); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_git_dash_c_key_equals_dynamic_value_without_traversal(tmp_dir: Path) -> None:
    # Still DENY even with no traversal literal anywhere -- unlike -C/
    # --git-dir/--work-tree, `-c` never sets `dir_override` (it does not
    # retarget which repository git operates on), so once the subcommand
    # is correctly identified as "reset --hard" the destructive target is
    # always the concrete, already-known cwd itself ("."), independent of
    # whether `-c`'s OWN value happens to be dynamic. There is no
    # "unresolvable dynamic target" ambiguity to fail open on here -- the
    # in-cwd-critical rule applies unconditionally, exactly as it would
    # for a plain `git reset --hard` with no `-c` at all.
    name = "pretooluse-bash-guard/blocks-git-dash-c-key-equals-dynamic-value-without-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=/some/safe/path; git -c foo=$(echo $x) reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for a key=dynamic-value -c fragment with no "
            f"traversal literal (destructive target is cwd itself, "
            f"unconditionally); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_git_dash_c_dynamic_key_equals_static_value_with_traversal(tmp_dir: Path) -> None:
    # Bug 2 (full silent bypass, opposite side of the same root cause,
    # live-verified): the dynamic span DOES close correctly here, but a
    # fused non-whitespace suffix immediately after the closing paren
    # (e.g. "=bar" in `$(echo foo)=bar`, no space) becomes its OWN token
    # -- the old scanner had no concept of "a suffix fused directly onto
    # the closing token is still part of the same shell word," so that
    # suffix token ("=bar") was misread as the subcommand next, matching
    # neither clean/reset/push (live-verified pre-fix: exit=0, empty
    # stdout).
    name = "pretooluse-bash-guard/blocks-git-dash-c-dynamic-key-equals-static-value-with-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=../../etc; git -c $(echo $x)=bar reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for 'x=../../etc; git -c $(echo $x)=bar reset "
            f"--hard' (dynamic-key=static-value, fused suffix); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_git_dash_c_dynamic_key_equals_static_value_without_traversal(tmp_dir: Path) -> None:
    # Same reasoning as the Bug 1 companion above: `-c` never sets
    # `dir_override`, so the destructive target is always the concrete,
    # already-known cwd itself, independent of whether `-c`'s own value
    # (or its fused suffix) is dynamic. Still DENY even with no traversal
    # literal anywhere.
    name = "pretooluse-bash-guard/blocks-git-dash-c-dynamic-key-equals-static-value-without-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=/some/safe/path; git -c $(echo $x)=bar reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for a dynamic-key=static-value -c fragment "
            f"with no traversal literal (destructive target is cwd itself, "
            f"unconditionally); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_blocks_git_multi_dash_capital_c_earlier_dynamic_taint_not_lost(tmp_dir: Path) -> None:
    # Bug 3 (full silent bypass, live-verified): `dir_override` was a
    # single mutable variable unconditionally OVERWRITTEN by the
    # last-processed -C/-c/--git-dir/--work-tree flag. If an EARLIER
    # flag's value was dynamic/unresolvable but a LATER flag's value
    # looks like a plain static string, the whole command was treated as
    # fully resolved and static -- the earlier dynamic taint was lost
    # entirely (live-verified pre-fix: exit=0, empty stdout).
    name = "pretooluse-bash-guard/blocks-git-multi-dash-capital-c-earlier-dynamic-taint-not-lost"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=../../etc; git -C $(echo $x) -C docs reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for 'x=../../etc; git -C $(echo $x) -C docs reset "
            f"--hard' (earlier -C dynamic, later -C static -- taint must "
            f"not be lost); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_allows_git_multi_dash_capital_c_earlier_dynamic_no_traversal(tmp_dir: Path) -> None:
    # No-over-blocking regression check for the Bug 3 fix: the same
    # multi -C shape (earlier dynamic, later static) with NO traversal
    # literal or wildcard anywhere in the command must stay allowed.
    name = "pretooluse-bash-guard/allows-git-multi-dash-capital-c-earlier-dynamic-no-traversal"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "x=/some/safe/path; git -C $(echo $x) -C docs reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(
            name,
            "expected allow for the same multi -C shape (earlier dynamic, "
            f"later static) with no traversal literal or wildcard anywhere; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_allows_git_dash_c_static_key_value_non_destructive_status(tmp_dir: Path) -> None:
    # Legitimate version of the Bug 1/2 shape (static-only, no over-
    # blocking): a plain static `-c key=value` with a NON-destructive
    # subcommand must stay allowed.
    name = "pretooluse-bash-guard/allows-git-dash-c-static-key-value-non-destructive-status"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "git -c foo=bar status"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'git -c foo=bar status' (static, non-destructive); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_git_dash_c_static_key_value_reset_hard_in_cwd(tmp_dir: Path) -> None:
    # Legitimate version of the Bug 1/2 shape (static-only): a plain
    # static `-c key=value` combined with a genuinely destructive
    # subcommand must still correctly resolve to "reset --hard" and deny
    # -- proves the fix didn't regress plain static -c parsing while
    # closing the dynamic bypasses above.
    name = "pretooluse-bash-guard/blocks-git-dash-c-static-key-value-reset-hard-in-cwd"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "git -c foo=bar reset --hard"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'git -c foo=bar reset --hard' (static, in-cwd); got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_git_multi_dash_capital_c_static_values_non_destructive(tmp_dir: Path) -> None:
    # Legitimate version of the Bug 3 shape (static-only, no over-
    # blocking): two static -C flags with a NON-destructive subcommand
    # must stay allowed.
    name = "pretooluse-bash-guard/allows-git-multi-dash-capital-c-static-values-non-destructive"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "git -C /tmp -C docs status"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'git -C /tmp -C docs status' (static, non-destructive); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_dd_bare_redirect_dynamic_target_with_traversal_elsewhere(tmp_dir: Path) -> None:
    # Doubt-verify generalization gap, a THIRD instance found by explicitly
    # auditing every branch rather than assuming only find/git had it:
    # `_dd_target()`'s own FALLBACK path (no `of=` token present -- a bare
    # `dd ... > target` stdout redirect) hardcoded has_unresolvable=False
    # unconditionally, never calling looks_dynamic() on the extracted
    # redirect target -- the exact same bug shape as the already-fixed
    # `of=` path, just in dd's OTHER sub-branch. Live-verified pre-fix:
    # `dd if=/dev/zero > $VAR; echo ../../etc` was silently ALLOWED.
    name = "pretooluse-bash-guard/blocks-dd-bare-redirect-dynamic-target-with-traversal-elsewhere"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "dd if=/dev/zero > $VAR; echo ../../etc"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for a bare 'dd ... > $VAR' redirect target with a "
            f"traversal literal elsewhere in the same command; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_allows_dd_bare_redirect_dynamic_target_without_traversal_elsewhere(tmp_dir: Path) -> None:
    # Behavior Contract rule 3, dd's bare-redirect side: a bare dynamic
    # redirect target with neither a traversal literal nor a wildcard
    # anywhere in the command's construction stays allowed.
    name = "pretooluse-bash-guard/allows-dd-bare-redirect-dynamic-target-without-traversal-elsewhere"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "dd if=/dev/zero > $VAR"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(
            name,
            "expected allow for a bare dynamic dd redirect target with no "
            f"traversal literal or wildcard anywhere in the command; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_worktree_path_non_string_type_does_not_crash(tmp_dir: Path) -> None:
    # CRITICAL 3: worktree_path is an untyped read from the workflow JSON --
    # if it's present but not str/None (e.g. an int), Path(worktree_path)
    # previously raised TypeError uncaught inside main()'s destructive-
    # target confinement loop, crashing the hook process (a non-zero/
    # non-JSON-deny exit fails OPEN, silently allowing the tool call). Must
    # coerce any non-str/non-None value to None immediately after reading
    # it so this malformed shape can't reach resolve_confinement() at all.
    name = "pretooluse-bash-guard/worktree-path-non-string-type-does-not-crash"
    project_root = tmp_dir / "project"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    outside.mkdir(parents=True)
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "wf-malformed-test.json").write_text(
        json.dumps({"workflow_uuid": "wf-malformed-test", "worktree_path": 12345}),
        encoding="utf-8",
    )
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": f"rm -f {(outside / 'secret.txt').resolve()}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected a genuine deny (not a crash) for an escaping target "
            f"when worktree_path is a malformed non-string type; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_main_denies_not_crashes_when_resolve_confinement_raises(tmp_dir: Path) -> None:
    # CRITICAL 3 continued: the main destructive-target confinement loop's
    # own resolve_confinement() call had no try/except at all, unlike its
    # sibling in the redirect-confinement block a few lines below. Forces
    # the exact exception directly (independent of the JSON-type coercion
    # covered by the sibling test above) and proves main() falls back to a
    # fail-CLOSED (not-confined -> deny) verdict instead of propagating the
    # exception uncaught (an uncaught crash fails OPEN).
    name = "pretooluse-bash-guard/main-denies-not-crashes-when-resolve-confinement-raises"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)

    original = bash_guard.resolve_confinement

    def _boom(path, cwd, worktree_path):
        raise ValueError("synthetic parse failure for CRITICAL 3 regression test")

    bash_guard.resolve_confinement = _boom
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -f ./scratch.txt"},
    }
    original_stdin = sys.stdin
    original_stdout = sys.stdout
    sys.stdin = io.StringIO(json.dumps(payload))
    captured = io.StringIO()
    sys.stdout = captured
    try:
        exit_code = bash_guard.main()
    except Exception as exc:
        fail(name, f"expected main() to catch the resolve_confinement exception, not propagate it; got: {exc!r}")
        return
    finally:
        bash_guard.resolve_confinement = original
        sys.stdin = original_stdin
        sys.stdout = original_stdout

    out = captured.getvalue().strip()
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected a genuine end-to-end DENY (fail-closed) from main() "
            f"when resolve_confinement() itself raises; got exit={exit_code} stdout={out!r}",
        )
        return
    ok(name)


def test_bash_guard_denial_reason_includes_all_triggered_categories(tmp_dir: Path) -> None:
    # HIGH: when both `escapes` and `in_cwd_critical` fire for DIFFERENT
    # targets in the same command, only the highest-priority reason string
    # was previously logged/returned, silently dropping the other rule's
    # detail. Doesn't change the allow/deny outcome, but each denial reason
    # should name every rule that fired (Observability requirement).
    name = "pretooluse-bash-guard/denial-reason-includes-all-triggered-categories"
    project_root = tmp_dir / "project"
    outside_file = tmp_dir / "outside" / "secret.txt"
    project_root.mkdir(parents=True)
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("x", encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": f"rm -f {outside_file.resolve()} && rm -rf .git"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a compound command with both an escape and an in-cwd-critical target; got: {out!r}")
        return
    if "escapes-cwd" not in out or "in-cwd-critical" not in out:
        fail(
            name,
            "expected BOTH 'escapes-cwd' and 'in-cwd-critical' reasons "
            f"present (not just the highest-priority one); got: {out!r}",
        )
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
        print("[ pretooluse-guard: Phase 4 protected-path + Bash-write inspection + confinement ]")
        test_pretooluse_guard_denies_edit_write_to_memory_finalize(tmp / "g3")
        test_pretooluse_guard_allows_edit_write_to_workflow_json(tmp / "g4")
        test_pretooluse_guard_denies_bash_heredoc_write_to_memory_md(tmp / "g5")
        test_pretooluse_guard_denies_bash_tee_write_to_memory_md(tmp / "g6")
        test_pretooluse_guard_denies_bash_python_oneliner_write_to_memory_md(tmp / "g7")
        test_pretooluse_guard_denies_bash_write_to_workflow_json(tmp / "g8")
        test_pretooluse_guard_denies_bash_heredoc_write_to_workflow_json(tmp / "g9")
        test_pretooluse_guard_denies_bash_tee_write_to_workflow_json(tmp / "g10")
        test_pretooluse_guard_denies_bash_python_oneliner_write_to_workflow_json(tmp / "g11")
        test_pretooluse_guard_allows_bash_permit_write_shape(tmp / "g12")
        test_pretooluse_guard_denies_bash_permit_write_wrong_shape(tmp / "g13")
        test_pretooluse_guard_denies_bash_permit_write_compound_command(tmp / "g14")
        test_pretooluse_guard_allows_bash_unrelated_command(tmp / "g15")
        test_pretooluse_guard_edit_write_worktree_confinement_denies_outside(tmp / "g16")
        test_pretooluse_guard_edit_write_worktree_confinement_allows_inside_worktree(tmp / "g17")
        test_pretooluse_guard_worktree_confinement_degrades_when_no_workflow_json(tmp / "g18")
        test_pretooluse_guard_edit_write_confinement_allows_workflow_json_when_worktree_path_stale(tmp / "g19")
        test_pretooluse_guard_bash_permit_write_allowed_when_worktree_path_stale(tmp / "g20")
        test_pretooluse_guard_allows_benign_redirect_to_dev_null(tmp / "g21")
        test_pretooluse_guard_allows_benign_stderr_redirect_to_dev_null(tmp / "g22")

        print()
        print("[ pretooluse-guard: REM-FIX (5 live-verified bugs) ]")
        test_pretooluse_guard_denies_bash_permit_write_noncanonical_absolute_spelling(tmp / "g23")
        test_pretooluse_guard_denies_bash_python_heredoc_write_to_memory_md(tmp / "g24")
        test_pretooluse_guard_denies_bash_python_path_write_text_to_memory_md(tmp / "g25")
        test_pretooluse_guard_denies_bash_python_oneliner_via_env_prefix_write_to_memory_md(tmp / "g26")
        test_pretooluse_guard_handles_workflow_payload_race_in_wf_uuid_lookup(tmp / "g27")

        print()
        print("[ pretooluse-guard: REM-FIX (broadened python write-mechanism detection) ]")
        test_pretooluse_guard_denies_bash_python_os_system_write_to_memory_md(tmp / "g28")
        test_pretooluse_guard_denies_bash_python_subprocess_run_write_to_workflow_json(tmp / "g29")
        test_pretooluse_guard_denies_bash_python_shutil_copy_write_to_memory_md(tmp / "g30")
        test_pretooluse_guard_denies_bash_python_os_rename_write_to_memory_md(tmp / "g31")
        test_pretooluse_guard_allows_bash_python_dynamic_dispatch_write_to_memory_md(tmp / "g32")

        print()
        print("[ pretooluse-guard: REM-FIX (doubt-verify cycle 2: statement-proximity + import-alias coverage) ]")
        test_pretooluse_guard_allows_bash_python_subprocess_run_harmless_mention_of_protected_path(tmp / "g33")
        test_pretooluse_guard_denies_bash_python_import_alias_os_system_write_to_memory_md(tmp / "g34")
        test_pretooluse_guard_denies_bash_python_from_import_system_write_to_memory_md(tmp / "g35")
        test_pretooluse_guard_allows_bash_python_variable_reference_then_call_write_to_memory_md_disclosed_gap(tmp / "g36")

        print()
        print("[ pretooluse-guard: REM-FIX (statement splitter -- paren/bracket depth + backslash-continuation) ]")
        test_pretooluse_guard_denies_bash_python_multiline_os_system_write_to_memory_md(tmp / "g37")
        test_pretooluse_guard_denies_bash_python_multiline_subprocess_run_write_to_workflow_json(tmp / "g38")
        test_pretooluse_guard_denies_bash_python_backslash_continued_os_system_write_to_memory_md(tmp / "g39")

        print()
        print("[ pretooluse-guard: Phase 5 protectedWrites toggle wiring ]")
        test_pretooluse_guard_bash_write_denied_when_protected_writes_block(tmp / "g40")
        test_pretooluse_guard_bash_write_audited_not_denied_when_protected_writes_audit(tmp / "g41")
        test_pretooluse_guard_memory_writes_toggle_independent_of_protected_writes(tmp / "g42")
        test_pretooluse_guard_real_plugin_config_now_blocks_bash_writes_by_default(tmp / "g43")

        print()
        print("[ pretooluse-guard: REM-FIX (load_mode fail-closed + unrecognized protectedWrites logging) ]")
        test_load_mode_fallback_defaults_protected_writes_to_block_when_file_missing(tmp / "g44")
        test_load_mode_fallback_defaults_protected_writes_to_block_when_file_corrupt(tmp / "g45")
        test_pretooluse_guard_unrecognized_protected_writes_value_still_allows_but_logs_distinct_decision(tmp / "g46")

        print()
        print("[ pretooluse-guard: REM-FIX (extend enum validation + distinguishing log decision to memoryWrites) ]")
        test_pretooluse_guard_memory_writes_unrecognized_value_still_allows_but_logs_distinct_decision(tmp / "g47")
        test_pretooluse_guard_memory_write_audited_not_denied_when_memory_writes_audit(tmp / "g48")
        test_pretooluse_guard_protected_writes_toggle_independent_of_memory_writes(tmp / "g49")

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
        print("[ pretooluse-bash-guard: REM-FIX Phase 2 review+hunt findings ]")
        test_bash_guard_blocks_git_dash_capital_c_flag_before_subcommand(tmp / "b29")
        test_bash_guard_blocks_git_clean_long_form_force(tmp / "b30")
        test_bash_guard_blocks_dd_bare_stdout_redirect_without_of(tmp / "b31")
        test_bash_guard_blocks_find_execdir_rm(tmp / "b32")
        test_bash_guard_blocks_rm_rf_descendant_of_critical_child(tmp / "b33")
        test_bash_guard_blocks_mv_target_directory_flag(tmp / "b34")
        test_bash_guard_destructive_targets_falls_back_on_parse_exception(tmp / "b35")
        test_bash_guard_blocks_mv_target_directory_short_attached_flag(tmp / "b36")
        test_bash_guard_dd_falls_back_to_cwd_target_on_parse_exception(tmp / "b37")
        test_bash_guard_mv_falls_back_to_cwd_target_when_positional_targets_itself_raises(tmp / "b38")
        test_bash_guard_chmod_falls_back_to_cwd_target_when_positional_targets_itself_raises(tmp / "b39")

        print()
        print("[ pretooluse-bash-guard: Phase 3 dynamic-traversal + worktree confinement ]")
        test_bash_guard_blocks_dynamic_target_with_traversal_substitution(tmp / "b40")
        test_bash_guard_blocks_dynamic_target_with_bare_wildcard(tmp / "b41")
        test_bash_guard_regression_lock_release_still_allowed_phase3(tmp / "b42")
        test_bash_guard_worktree_confinement_denies_outside_worktree(tmp / "b43")
        test_bash_guard_worktree_confinement_allows_inside_worktree_outside_cwd(tmp / "b44")
        test_bash_guard_worktree_confinement_degrades_when_no_workflow_json(tmp / "b45")
        test_bash_guard_worktree_confinement_degrades_when_worktree_path_null(tmp / "b46")
        test_bash_guard_worktree_confinement_allows_within_cwd_despite_different_set_worktree(tmp / "b47")
        test_bash_guard_worktree_confinement_allows_memory_finalize_clear_when_worktree_path_stale(tmp / "b48")
        test_bash_guard_worktree_confinement_allows_memory_finalize_permit_write_when_worktree_path_stale(tmp / "b49")
        test_bash_guard_denies_permit_write_noncanonical_absolute_spelling(tmp / "b49b")
        test_bash_guard_allows_benign_redirect_to_dev_null(tmp / "b50")
        test_bash_guard_allows_benign_stderr_redirect_to_dev_null(tmp / "b51")

        print()
        print("[ pretooluse-bash-guard: REM-FIX Phase 3 review+hunt findings ]")
        test_bash_guard_blocks_protected_redirect_overwrite_when_confined_to_cwd(tmp / "b52")
        test_bash_guard_blocks_dd_of_dynamic_traversal_substitution(tmp / "b53")
        test_bash_guard_worktree_path_non_string_type_does_not_crash(tmp / "b54")
        test_bash_guard_main_denies_not_crashes_when_resolve_confinement_raises(tmp / "b55")
        test_bash_guard_denial_reason_includes_all_triggered_categories(tmp / "b56")

        print()
        print("[ pretooluse-bash-guard: doubt-verify generalization gap (find/git dynamic targets) ]")
        test_bash_guard_blocks_find_dynamic_search_path_with_traversal_elsewhere(tmp / "b57")
        test_bash_guard_allows_find_dynamic_search_path_without_traversal_elsewhere(tmp / "b58")
        test_bash_guard_blocks_git_dynamic_dir_override_with_traversal_elsewhere(tmp / "b59")
        test_bash_guard_allows_git_dynamic_dir_override_without_traversal_elsewhere(tmp / "b60")
        test_bash_guard_blocks_dd_bare_redirect_dynamic_target_with_traversal_elsewhere(tmp / "b61")
        test_bash_guard_allows_dd_bare_redirect_dynamic_target_without_traversal_elsewhere(tmp / "b62")

        print()
        print("[ pretooluse-bash-guard: REM-FIX continuation (fragmented unquoted $(...) substitutions) ]")
        test_bash_guard_blocks_git_dash_capital_c_unquoted_fragmented_substitution_with_traversal(tmp / "b63")
        test_bash_guard_allows_git_dash_capital_c_unquoted_fragmented_substitution_without_traversal(tmp / "b64")
        test_bash_guard_allows_git_dash_capital_c_unquoted_dynamic_var_no_traversal(tmp / "b65")
        test_bash_guard_blocks_find_unquoted_nested_substitution_with_traversal_elsewhere(tmp / "b66")

        print()
        print("[ pretooluse-bash-guard: REM-FIX doubt-verify cycle 2 (assignment-form bypass + nested-paren depth) ]")
        test_bash_guard_blocks_git_work_tree_assignment_fragmented_substitution_with_traversal(tmp / "b67")
        test_bash_guard_blocks_git_git_dir_assignment_fragmented_substitution_with_traversal(tmp / "b68")
        test_bash_guard_allows_git_work_tree_assignment_fragmented_substitution_without_traversal(tmp / "b69")
        test_bash_guard_blocks_git_dash_capital_c_doubly_nested_substitution_with_traversal(tmp / "b70")
        test_bash_guard_allows_git_dash_capital_c_doubly_nested_substitution_without_traversal(tmp / "b71")
        test_bash_guard_blocks_git_dash_capital_c_malformed_unterminated_substitution(tmp / "b72")
        test_bash_guard_regression_lock_release_still_allowed_cycle2(tmp / "b73")

        print()
        print("[ pretooluse-bash-guard: REM-FIX round 4 (git -c key=value fragment + fused-suffix + multi-flag taint) ]")
        test_bash_guard_blocks_git_dash_c_key_equals_dynamic_value_with_traversal(tmp / "b74")
        test_bash_guard_blocks_git_dash_c_key_equals_dynamic_value_without_traversal(tmp / "b75")
        test_bash_guard_blocks_git_dash_c_dynamic_key_equals_static_value_with_traversal(tmp / "b76")
        test_bash_guard_blocks_git_dash_c_dynamic_key_equals_static_value_without_traversal(tmp / "b77")
        test_bash_guard_blocks_git_multi_dash_capital_c_earlier_dynamic_taint_not_lost(tmp / "b78")
        test_bash_guard_allows_git_multi_dash_capital_c_earlier_dynamic_no_traversal(tmp / "b79")
        test_bash_guard_allows_git_dash_c_static_key_value_non_destructive_status(tmp / "b80")
        test_bash_guard_blocks_git_dash_c_static_key_value_reset_hard_in_cwd(tmp / "b81")
        test_bash_guard_allows_git_multi_dash_capital_c_static_values_non_destructive(tmp / "b82")

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
    test_hooks_json_registers_pretooluse_guard_on_bash()
    test_selfcheck_never_imports_hooklib_directly()
    test_selfcheck_resolves_bare_python3_not_sys_executable()
    test_hooks_json_registers_selfcheck_sessionstart()
    test_root_hooks_json_registers_selfcheck_sessionstart()
    test_selfcheck_internal_budget_stays_under_registered_hook_timeout()
    test_workflow_id_script_present()
    test_statusline_script_present()
    test_router_uses_workflow_id_helper()
    test_learn_distiller_uses_tools_key_not_allowed_tools()

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
