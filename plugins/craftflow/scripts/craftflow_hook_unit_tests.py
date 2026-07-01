#!/usr/bin/env python3
"""Unit tests for craftflow Python hook scripts.

Pipes crafted JSON payloads into each hook via subprocess and validates
stdout, exit codes, and file side effects without running Claude Code.

Run: python3 scripts/craftflow_hook_unit_tests.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"

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
    print("[ structural ]")
    test_anti_rationalization_tables_present()
    test_doubt_verifier_agent_present()
    test_intent_interview_skill_present()
    test_router_dispatches_doubt_verify()
    test_router_dispatches_intent_interview()
    test_hooks_json_registers_new_hooks()

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
