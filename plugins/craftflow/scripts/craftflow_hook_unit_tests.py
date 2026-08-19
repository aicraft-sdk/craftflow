#!/usr/bin/env python3
"""Unit tests for craftflow Python hook scripts.

Pipes crafted JSON payloads into each hook via subprocess and validates
stdout, exit codes, and file side effects without running Claude Code.

Run: python3 scripts/craftflow_hook_unit_tests.py
"""
from __future__ import annotations

import argparse
import fcntl
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
import craftflow_skill_ledger as skill_ledger  # noqa: E402
import craftflow_skill_promote as skill_promote  # noqa: E402
import craftflow_skill_propose as skill_propose  # noqa: E402
import craftflow_status_report as status_report  # noqa: E402
import craftflow_precompact_state as precompact_state  # noqa: E402
import craftflow_postcompact_context as postcompact_context  # noqa: E402
import craftflow_memory_merge as memory_merge  # noqa: E402

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


def test_router_records_reliability_gates_evidence_in_fix_verify_and_doubt_verify() -> None:
    name = "router/reliability-gates-record-evidence-wired"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    doubt_idx = content.find("### Doubt-Verify Dispatch Rule")
    fix_idx = content.find("### Fix-Verify Dispatch Rule")
    if doubt_idx == -1 or fix_idx == -1 or fix_idx <= doubt_idx:
        fail(name, "expected Doubt-Verify section before Fix-Verify section")
        return
    doubt_section = content[doubt_idx:fix_idx]
    fix_section = content[fix_idx:]
    if "craftflow_reliability_gates.py" not in doubt_section or "--record-evidence" not in doubt_section:
        fail(name, "Doubt-Verify Dispatch Rule missing --record-evidence wiring")
        return
    if "craftflow_reliability_gates.py" not in fix_section or "fix-verify-evidence-completeness" not in fix_section:
        fail(name, "Fix-Verify Dispatch Rule missing --record-evidence wiring")
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


def test_circuit_breaker_uses_persisted_non_telemetry_field_not_live_tasklist_count() -> None:
    name = "router/circuit-breaker-persisted-non-telemetry-field"
    remediation_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / "remediation-and-research.md"
    policy_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / "workflow-artifact-and-hook-policy.md"
    if not remediation_path.exists():
        fail(name, f"remediation-and-research.md not found at {remediation_path}")
        return
    if not policy_path.exists():
        fail(name, f"workflow-artifact-and-hook-policy.md not found at {policy_path}")
        return
    remediation_content = remediation_path.read_text(encoding="utf-8")
    policy_content = policy_path.read_text(encoding="utf-8")
    if "circuit_breaker.remfix_count" not in remediation_content:
        fail(name, "missing circuit_breaker.remfix_count field in remediation-and-research.md")
        return
    if "circuit_breaker.broken" not in remediation_content:
        fail(name, "missing circuit_breaker.broken field in remediation-and-research.md")
        return
    if ">= 3" not in remediation_content:
        fail(name, "missing the >=3 threshold (behavior must be unchanged, only persistence)")
        return
    if "telemetry.loop_counts.remfix" in remediation_content or "telemetry.circuit_broken" in remediation_content:
        fail(name, "circuit-breaker state must not live under telemetry (policy: telemetry never drives routing decisions)")
        return
    if "circuit_breaker" not in policy_content or "remfix_count" not in policy_content:
        fail(name, "workflow-artifact-and-hook-policy.md does not document the new circuit_breaker field")
        return
    # Structural (not literal-blacklist) checks below. The literal-string checks above only catch
    # the specific HISTORICAL mistake (old field names telemetry.loop_counts.remfix /
    # telemetry.circuit_broken). They pass vacuously if a FUTURE regression re-nests circuit_breaker
    # under telemetry using the NEW field names (e.g. telemetry.circuit_breaker.remfix_count, or by
    # textually indenting the `circuit_breaker` bullet under the `telemetry` bullet's own block) --
    # confirmed by mutation testing during this fix (2026-08-09 REM-FIX). These checks are genuinely
    # structural: they inspect indentation/adjacency, not just substring presence/absence.
    def _bullet_indents(content: str, field: str) -> list:
        return [
            len(m.group(1))
            for m in re.finditer(r"^([ \t]*)-\s*`" + re.escape(field) + r"`", content, re.MULTILINE)
        ]

    # 1. workflow-artifact-and-hook-policy.md: `circuit_breaker` must appear as its own bullet at
    #    the SAME top-level indentation as `telemetry`/`pending_gate` -- never indented/nested as a
    #    sub-item under the `telemetry` bullet's own block (its own field list AND its own prose
    #    description block both use this convention today).
    telemetry_indents = _bullet_indents(policy_content, "telemetry")
    circuit_breaker_indents = _bullet_indents(policy_content, "circuit_breaker")
    if not telemetry_indents:
        fail(name, "workflow-artifact-and-hook-policy.md: no `- `telemetry`` bullet found to establish top-level indentation baseline")
        return
    if not circuit_breaker_indents:
        fail(name, "workflow-artifact-and-hook-policy.md: no `- `circuit_breaker`` bullet found at all")
        return
    top_level_indent = min(telemetry_indents)
    if any(indent > top_level_indent for indent in circuit_breaker_indents):
        fail(
            name,
            "workflow-artifact-and-hook-policy.md: `circuit_breaker` bullet is indented deeper than "
            f"the top-level `telemetry` bullet (circuit_breaker indents={circuit_breaker_indents}, "
            f"telemetry top-level indent={top_level_indent}) -- looks nested under telemetry",
        )
        return

    # 2. remediation-and-research.md: no line may textually associate `telemetry` with
    #    `circuit_breaker` via dotted/whitespace nesting (e.g. `telemetry.circuit_breaker.remfix_count`),
    #    regardless of which new field name a future regression appends after `circuit_breaker`.
    nesting_match = re.search(r"telemetry[.\s]*circuit_breaker", remediation_content)
    if nesting_match is not None:
        fail(
            name,
            "remediation-and-research.md: found text nesting circuit_breaker under telemetry "
            f"({nesting_match.group(0)!r}) -- circuit_breaker must remain a sibling field, not "
            "telemetry.circuit_breaker.*",
        )
        return
    ok(name)


def test_section_0_precedes_memory_load() -> None:
    name = "router/section-0-precedes-memory-load"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    idx_0 = content.find("## 0. Resolve Project Root")
    idx_2 = content.find("## 2. Memory Load And Template Validation")
    if idx_0 == -1:
        fail(name, "## 0. Resolve Project Root heading not found")
        return
    if idx_2 == -1:
        fail(name, "## 2. Memory Load And Template Validation heading not found")
        return
    if not (idx_0 < idx_2):
        fail(name, f"expected ## 0. ({idx_0}) before ## 2. ({idx_2})")
        return
    ok(name)


def test_memory_load_anchored_to_project_root() -> None:
    name = "router/memory-load-anchored-to-project-root"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    start = content.find("## 2. Memory Load And Template Validation")
    end = content.find("\n## 2a.", start)
    if start == -1 or end == -1:
        fail(name, "could not bound ## 2. section")
        return
    section = content[start:end]
    if '$PROJECT_ROOT/.craftflow/state/project/activeContext.md' not in section:
        fail(name, "anchored activeContext.md reference not found in ## 2.")
        return
    if '"activeContext.md"' in section or 'Read(".craftflow/state/project/activeContext.md")' in section:
        fail(name, "bare unanchored activeContext.md reference still present in ## 2.")
        return
    ok(name)


def test_parent_workflow_creation_anchored_to_project_root() -> None:
    name = "router/parent-workflow-creation-anchored"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    start = content.find("### Parent workflow creation")
    end = content.find("\n### BUILD task graph", start)
    if start == -1 or end == -1:
        fail(name, "could not bound ### Parent workflow creation section")
        return
    section = content[start:end]
    if 'file_path="$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.json"' not in section:
        fail(name, "anchored workflow artifact Write() not found")
        return
    if 'file_path=".craftflow/state/workflows/{workflow_uuid}.json"' in section:
        fail(name, "bare unanchored workflow artifact Write() still present")
        return
    ok(name)


def test_parent_workflow_creation_fallback_reason_wired() -> None:
    name = "router/parent-workflow-creation-fallback-reason-wired"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    start = content.find("### Parent workflow creation")
    end = content.find("\n### BUILD task graph", start)
    if start == -1 or end == -1:
        fail(name, "could not bound ### Parent workflow creation section")
        return
    section = content[start:end]
    fallback_idx = section.find("project_root_resolution_fallback")
    if fallback_idx == -1:
        fail(name, "project_root_resolution_fallback conditional block not found in ### Parent workflow creation")
        return
    fallback_block = section[fallback_idx: fallback_idx + 500]
    if "NO_REPO_FOUND" not in fallback_block:
        fail(name, "NO_REPO_FOUND reason literal not found in fallback conditional block")
        return
    if "RESOLVE_SCRIPT_ERROR" not in fallback_block:
        fail(name, "RESOLVE_SCRIPT_ERROR reason literal not found in fallback conditional block")
        return
    if "{workflow_uuid}" not in fallback_block:
        fail(name, "{workflow_uuid} templating not found in fallback conditional block")
        return
    if "{iso_timestamp}" not in fallback_block:
        fail(name, "{iso_timestamp} templating not found in fallback conditional block")
        return
    ok(name)


def test_shared_preparation_anchored_to_project_root() -> None:
    name = "router/shared-preparation-anchored"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    start = content.find("### Shared preparation")
    end = content.find("\n**Intent Readiness Gate", start)
    if start == -1 or end == -1:
        fail(name, "could not bound ### Shared preparation section")
        return
    section = content[start:end]
    positive_checks = [
        (
            "$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## References",
            "anchored activeContext.md ## References read not found in Shared preparation",
        ),
        (
            "$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Decisions",
            "anchored activeContext.md ## Decisions read not found in Shared preparation",
        ),
        (
            "$PROJECT_ROOT/.craftflow/state/project/progress.md ## Current Workflow",
            "anchored progress.md ## Current Workflow read not found in Shared preparation",
        ),
        (
            "$PROJECT_ROOT/.craftflow/state/workflows/*.json",
            "anchored workflows/*.json artifact read not found in Shared preparation",
        ),
    ]
    for needle, reason in positive_checks:
        if needle not in section:
            fail(name, reason)
            return
    negative_checks = [
        (
            "Read `activeContext.md ## References`",
            "bare unanchored activeContext.md ## References read still present in Shared preparation",
        ),
        (
            "Read `activeContext.md ## Decisions`",
            "bare unanchored activeContext.md ## Decisions read still present in Shared preparation",
        ),
        (
            "Read `progress.md ## Current Workflow`",
            "bare unanchored progress.md ## Current Workflow read still present in Shared preparation",
        ),
        (
            "latest `.craftflow/state/workflows/*.json` artifact",
            "bare unanchored workflows/*.json artifact read still present in Shared preparation",
        ),
    ]
    for needle, reason in negative_checks:
        if needle in section:
            fail(name, reason)
            return
    ok(name)


def test_worktree_isolation_reuses_project_root_no_duplicate_resolution() -> None:
    # Phase 3b (backlog item 8) moved the resolver-script reference out of SKILL.md into
    # skills/_shared/router-protocol.md. The "no duplicate resolution" invariant this test
    # protects now means: SKILL.md itself has ZERO occurrences (fully delegated, no stale
    # re-embedded copy), and the shared doc has exactly ONE (the single source of truth) --
    # together still "exactly one real resolution call site," just relocated.
    name = "router/worktree-isolation-reuses-project-root"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    shared_path = PLUGIN_ROOT / "skills" / "_shared" / "router-protocol.md"
    content = skill_path.read_text(encoding="utf-8")
    if not shared_path.exists():
        fail(name, f"shared router-protocol.md not found at {shared_path}")
        return
    shared_content = shared_path.read_text(encoding="utf-8")
    if content.count("craftflow_resolve_workspace_root.py") != 0:
        fail(name, f"expected 0 references to the resolver script in SKILL.md (fully delegated to the shared doc), found {content.count('craftflow_resolve_workspace_root.py')}")
        return
    if shared_content.count("craftflow_resolve_workspace_root.py") != 1:
        fail(name, f"expected exactly 1 reference to the resolver script in the shared doc, found {shared_content.count('craftflow_resolve_workspace_root.py')}")
        return
    start = content.find("### Worktree Isolation (BUILD Default)")
    next_heading = content.find("\n### DEBUG preparation", start)
    section = content[start: next_heading if next_heading != -1 else None]
    if "was already resolved once by" not in section:
        fail(name, "expected reuse sentence not found in Worktree Isolation section")
        return
    if "TOPLEVEL_EXIT=$?" in section:
        fail(name, "Worktree Isolation section still contains its own TOPLEVEL_EXIT assignment")
        return
    ok(name)


def test_memory_finalization_for_plan_anchored_to_project_tier() -> None:
    name = "router/memory-finalization-for-plan-anchored-to-project-tier"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    start = content.find("For PLAN:")
    end = content.find("For DEBUG:", start)
    if start == -1 or end == -1:
        fail(name, "could not bound the For PLAN: block in ## 13. Memory Finalization")
        return
    section = content[start:end]
    if "$PROJECT_ROOT/.craftflow/state/project/activeContext.md" not in section:
        fail(name, "expected project-tier activeContext.md reference not found in For PLAN: block")
        return
    if "$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/activeContext.md" in section:
        fail(name, "For PLAN: block writes to the wrong (workflow-tier) activeContext.md")
        return
    ok(name)


def test_memory_finalization_for_debug_anchored_and_uses_workflow_uuid() -> None:
    name = "router/memory-finalization-for-debug-anchored-uses-workflow-uuid"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    start = content.find("For DEBUG:")
    end = content.find("## 14. Hard Rules", start)
    if start == -1 or end == -1:
        fail(name, "could not bound the For DEBUG: block in ## 13. Memory Finalization")
        return
    section = content[start:end]
    if "$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/activeContext.md" not in section:
        fail(name, "expected workflow-tier activeContext.md reference not found in For DEBUG: block")
        return
    if "[DEBUG-RESET: wf:{workflow_uuid}]" not in section:
        fail(name, "expected {workflow_uuid} in the [DEBUG-RESET: wf:...] marker reference")
        return
    if "workflow_task_id" in section:
        fail(name, "For DEBUG: block still references undefined workflow_task_id")
        return
    ok(name)


def test_memory_finalization_prelude_anchored_to_project_root() -> None:
    name = "router/memory-finalization-prelude-anchored"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    start = content.find("## 13. Memory Finalization")
    end = content.find("For PLAN:", start)
    if start == -1 or end == -1:
        fail(name, "could not bound ## 13. Memory Finalization heading through For PLAN:")
        return
    section = content[start:end]
    positive_checks = [
        (
            "| `learnings` | `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/activeContext.md ## Learnings` |",
            "anchored learnings routing-table row not found",
        ),
        (
            "| `patterns` | `$PROJECT_ROOT/.craftflow/state/project/patterns.md ## Common Gotchas` |",
            "anchored patterns routing-table row not found",
        ),
        (
            "| `verification` | `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/progress.md ## Verification` |",
            "anchored verification routing-table row not found",
        ),
        (
            "| `deferred` | `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/activeContext.md` as `[Deferred]: ...` |",
            "anchored deferred routing-table row not found",
        ),
        (
            "`$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Learnings`",
            "anchored cross-workflow promotion rule reference not found",
        ),
        (
            'Bash("printf \'%s\' \'{workflow_uuid}\' > \\"$PROJECT_ROOT/.craftflow/state/.memory-finalize\\"")',
            "anchored memory-finalize permit printf Bash() call not found",
        ),
        (
            'Bash("rm -f \\"$PROJECT_ROOT/.craftflow/state/.memory-finalize\\"")',
            "anchored memory-finalize permit rm Bash() call not found",
        ),
        (
            "Replaces `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/progress.md ## Tasks`",
            "anchored 'memory task also' Replaces bullet not found",
        ),
        (
            "Keeps only the most recent 10 items in `$PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}/progress.md ## Completed`",
            "anchored 'memory task also' Keeps bullet not found",
        ),
        (
            "Updates `$PROJECT_ROOT/.craftflow/state/project/progress.md ## Completed`",
            "anchored 'memory task also' Updates bullet not found",
        ),
        (
            "line from `$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## References`",
            "anchored 'memory task also' Removes bullet not found",
        ),
        (
            "`$PROJECT_ROOT/.craftflow/state/activeContext.md`",
            "anchored root-flat fallback activeContext.md not found",
        ),
        (
            "`$PROJECT_ROOT/.craftflow/state/patterns.md`",
            "anchored root-flat fallback patterns.md not found",
        ),
        (
            "`$PROJECT_ROOT/.craftflow/state/progress.md`",
            "anchored root-flat fallback progress.md not found",
        ),
    ]
    for needle, reason in positive_checks:
        if needle not in section:
            fail(name, reason)
            return
    negative_checks = [
        (
            "| `learnings` | `workflows/{workflow_uuid}/activeContext.md ## Learnings` |",
            "bare unanchored learnings routing-table row still present",
        ),
        (
            "| `patterns` | `project/patterns.md ## Common Gotchas` |",
            "bare unanchored patterns routing-table row still present",
        ),
        (
            "| `verification` | `workflows/{workflow_uuid}/progress.md ## Verification` |",
            "bare unanchored verification routing-table row still present",
        ),
        (
            "| `deferred` | `workflows/{workflow_uuid}/activeContext.md` as `[Deferred]: ...` |",
            "bare unanchored deferred routing-table row still present",
        ),
        (
            "`project/activeContext.md ## Learnings`",
            "bare unanchored cross-workflow promotion rule reference still present",
        ),
        (
            'Bash("printf \'%s\' \'{workflow_uuid}\' > .craftflow/state/.memory-finalize")',
            "bare unanchored memory-finalize permit printf Bash() call still present",
        ),
        (
            'Bash("rm -f .craftflow/state/.memory-finalize")',
            "bare unanchored memory-finalize permit rm Bash() call still present",
        ),
        (
            "Replaces `workflows/{workflow_uuid}/progress.md ## Tasks`",
            "bare unanchored 'memory task also' Replaces bullet still present",
        ),
        (
            "Updates `project/progress.md ## Completed`",
            "bare unanchored 'memory task also' Updates bullet still present",
        ),
        (
            "line from `project/activeContext.md ## References`",
            "bare unanchored 'memory task also' Removes bullet still present",
        ),
        (
            "(`.craftflow/state/activeContext.md`, `.craftflow/state/patterns.md`, `.craftflow/state/progress.md`)",
            "bare unanchored root-flat fallback path group still present",
        ),
    ]
    for needle, reason in negative_checks:
        if needle in section:
            fail(name, reason)
            return
    ok(name)


def test_just_go_and_scope_decision_resume_anchored_to_project_root() -> None:
    name = "router/just-go-and-scope-decision-resume-anchored"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    jg_start = content.find("JUST_GO:")
    jg_end = content.find("\n## 2a.", jg_start)
    if jg_start == -1 or jg_end == -1:
        fail(name, "could not bound JUST_GO: subsection through ## 2a.")
        return
    jg_section = content[jg_start:jg_end]
    if "`$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Session Settings`" not in jg_section:
        fail(name, "anchored JUST_GO Session Settings reference not found")
        return
    if "`activeContext.md ## Session Settings`" in jg_section:
        fail(name, "bare unanchored JUST_GO Session Settings reference still present")
        return

    resume_start = content.find("## 4. Resume And Hydration")
    resume_end = content.find("\n## 5. Workflow Preparation", resume_start)
    if resume_start == -1 or resume_end == -1:
        fail(name, "could not bound ## 4. Resume And Hydration through ## 5. Workflow Preparation")
        return
    resume_section = content[resume_start:resume_end]
    if "`$PROJECT_ROOT/.craftflow/state/project/activeContext.md ## Decisions`" not in resume_section:
        fail(name, "anchored scope-decision-resume Decisions reference not found")
        return
    if "`activeContext.md ## Decisions`" in resume_section:
        fail(name, "bare unanchored scope-decision-resume Decisions reference still present")
        return

    ok(name)


def test_dispatcher_scaffold_workflow_artifact_anchored_to_project_root() -> None:
    # Phase 3 of the hooks-as-bridge redesign (backlog item 8) extracted the dispatch
    # prompt scaffold out of craftflow-router/SKILL.md into the shared
    # skills/_shared/router-protocol.md doc both hosts Read(). The anchored invariant this
    # test protects -- the Workflow Artifact line must be $PROJECT_ROOT-anchored, not bare
    # -- now lives in the shared doc; this test also confirms SKILL.md's own section was
    # actually replaced with a pointer, not left as (or silently reverted to) a stale
    # re-embedded copy.
    name = "router/dispatcher-scaffold-workflow-artifact-anchored"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    shared_path = PLUGIN_ROOT / "skills" / "_shared" / "router-protocol.md"
    if not shared_path.exists():
        fail(name, f"shared router-protocol.md not found at {shared_path}")
        return
    skill_content = skill_path.read_text(encoding="utf-8")
    shared_content = shared_path.read_text(encoding="utf-8")

    skill_start = skill_content.find("### Prompt scaffold for every agent")
    skill_end = skill_content.find("\n### Prompt assembly rule", skill_start)
    if skill_start == -1 or skill_end == -1:
        fail(name, "could not bound ### Prompt scaffold for every agent through ### Prompt assembly rule in SKILL.md")
        return
    skill_section = skill_content[skill_start:skill_end]
    if "skills/_shared/router-protocol.md" not in skill_section:
        fail(name, "SKILL.md's dispatcher scaffold section no longer points at the shared doc")
        return
    if "- Workflow Artifact: $PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.json" in skill_section:
        fail(name, "SKILL.md still inlines the full anchored Workflow Artifact line -- stale re-embedded copy alongside the shared-doc pointer")
        return

    shared_start = shared_content.find("### Prompt scaffold for every agent")
    shared_end = shared_content.find("\n### Prompt assembly rule", shared_start)
    if shared_start == -1 or shared_end == -1:
        fail(name, "could not bound ### Prompt scaffold for every agent through ### Prompt assembly rule in the shared doc")
        return
    shared_section = shared_content[shared_start:shared_end]
    if "- Workflow Artifact: $PROJECT_ROOT/.craftflow/state/workflows/{workflow_uuid}.json" not in shared_section:
        fail(name, "anchored Workflow Artifact line not found in shared doc's dispatcher scaffold")
        return
    if "- Workflow Artifact: .craftflow/state/workflows/{workflow_uuid}.json" in shared_section:
        fail(name, "bare unanchored Workflow Artifact line present in shared doc's dispatcher scaffold")
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


def test_resolve_workspace_root_script_present() -> None:
    name = "scripts/craftflow_resolve_workspace_root-present"
    path = SCRIPTS / "craftflow_resolve_workspace_root.py"
    if not path.exists():
        fail(name, f"craftflow_resolve_workspace_root.py not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in ("find_repo_candidates", "match_request_text", "resolve", "DETERMINISTIC", "AMBIGUOUS", "NO_REPO_FOUND"):
        if marker not in content:
            fail(name, f"craftflow_resolve_workspace_root.py missing expected symbol: {marker!r}")
            return
    ok(name)


def test_reliability_gates_script_present() -> None:
    name = "scripts/craftflow_reliability_gates-present"
    path = SCRIPTS / "craftflow_reliability_gates.py"
    if not path.exists():
        fail(name, f"craftflow_reliability_gates.py not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in (
        "_seed_gates", "cmd_seed", "cmd_record_evidence", "cmd_promote",
        "cmd_list", "cmd_query", "LedgerCorruptError", "worktree-merge-safety",
        "memory-write-guard-symmetry", "fix-verify-evidence-completeness",
    ):
        if marker not in content:
            fail(name, f"craftflow_reliability_gates.py missing expected symbol: {marker!r}")
            return
    ok(name)


def test_worktree_isolation_resolver_gated_on_toplevel_failure() -> None:
    # Phase 3b of the hooks-as-bridge redesign (backlog item 8) extracted "## 0. Resolve
    # Project Root" out of craftflow-router/SKILL.md into skills/_shared/router-protocol.md
    # (as "## Resolve Project Root"). The gate-before-resolver ordering invariant this test
    # protects now lives entirely in the shared doc; SKILL.md itself no longer contains
    # either literal marker, only a pointer. Verify both halves: the shared doc still has
    # the correct internal ordering, AND SKILL.md's pointer to it textually precedes
    # SKILL.md's own mkdir/worktree-add block (so the resolution step still conceptually
    # happens before worktree creation, just via a Read() instead of inline text).
    name = "router/worktree-isolation-resolver-gated"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    shared_path = PLUGIN_ROOT / "skills" / "_shared" / "router-protocol.md"
    if not skill_path.exists():
        fail(name, f"SKILL.md not found at {skill_path}")
        return
    if not shared_path.exists():
        fail(name, f"shared router-protocol.md not found at {shared_path}")
        return
    skill_content = skill_path.read_text(encoding="utf-8")
    shared_content = shared_path.read_text(encoding="utf-8")

    shared_section_start = shared_content.find("## Resolve Project Root")
    if shared_section_start == -1:
        fail(name, "'## Resolve Project Root' section not found in shared doc")
        return
    shared_section = shared_content[shared_section_start:]
    gate_idx = shared_section.find("TOPLEVEL_EXIT != 0")
    resolver_idx = shared_section.find("craftflow_resolve_workspace_root.py")
    if gate_idx == -1:
        fail(name, "TOPLEVEL_EXIT != 0 gate text not found in/after shared doc's Resolve Project Root section")
        return
    if resolver_idx == -1:
        fail(name, "resolver script reference not found in/after shared doc's Resolve Project Root section")
        return
    if not (gate_idx < resolver_idx):
        fail(
            name,
            f"expected order TOPLEVEL_EXIT!=0 gate ({gate_idx}) < resolver reference "
            f"({resolver_idx}) in the shared doc -- resolver must be textually gated behind "
            f"the failure branch, never unconditional",
        )
        return

    skill_section_start = skill_content.find("## 0. Resolve Project Root")
    if skill_section_start == -1:
        fail(name, "'## 0. Resolve Project Root' heading not found in SKILL.md")
        return
    skill_section = skill_content[skill_section_start:]
    pointer_idx = skill_section.find("skills/_shared/router-protocol.md")
    mkdir_idx = skill_section.find('mkdir -p "$PROJECT_ROOT/.claude/worktrees"')
    if pointer_idx == -1:
        fail(name, "SKILL.md's '## 0.' section no longer points at the shared doc")
        return
    if mkdir_idx == -1:
        fail(name, "mkdir -p .../.claude/worktrees block not found after '## 0.' in SKILL.md")
        return
    if "TOPLEVEL_EXIT != 0" in skill_section[: skill_section.find("## 2.")]:
        fail(name, "SKILL.md's '## 0.' section still inlines the resolution branching text -- stale re-embedded copy alongside the shared-doc pointer")
        return
    if not (pointer_idx < mkdir_idx):
        fail(
            name,
            f"expected the shared-doc pointer ({pointer_idx}) to textually precede the "
            f"mkdir/worktree-add block ({mkdir_idx}) in SKILL.md",
        )
        return
    ok(name)


def test_worktree_isolation_step_4a_derives_from_worktree_path() -> None:
    name = "router/worktree-isolation-step-4a-no-reredirive"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    # NOTE: current code nests `dirname` three levels deep (worktree_path ->
    # {project_root}/.claude/worktrees/{worktree_dir}, so its great-grandparent is
    # PROJECT_ROOT) -- deeper than this plan's original two-level literal, confirmed by
    # reading the live file rather than trusting the stale plan text.
    if 'PROJECT_ROOT=$(dirname "$(dirname "$(dirname "{worktree_path}")")")' not in content:
        fail(name, "step 4a no longer derives PROJECT_ROOT from worktree_path via nested dirname")
        return
    if "PROJECT_ROOT=$(git rev-parse --show-toplevel)" in content:
        fail(name, "a bare, unconditional `PROJECT_ROOT=$(git rev-parse --show-toplevel)` re-derivation still exists")
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


def test_pretooluse_guard_allows_workflow_scoped_memory_write_when_newer_unrelated_workflow_exists(
    tmp_dir: Path,
) -> None:
    # Root-cause regression (2026-08-19 DEBUG workflow, live-reproduced):
    # the permit-lift check at `_handle_edit_write()` used to compare the
    # permit file's own stored content against `wf_uuid` sourced from
    # `latest_live_workflow_payload()` -- the mtime-latest LIVE workflow
    # artifact on disk, NOT the workflow the permit was actually issued
    # for. In a real multi-workflow session, a second, unrelated workflow
    # (e.g. a DEBUG workflow with no worktree, always "live") routinely
    # touches its own JSON artifact AFTER the permit was written for a
    # DIFFERENT, earlier workflow that is mid-memory-finalization -- making
    # that unrelated workflow "latest" by mtime even though it has nothing
    # to do with the write in flight. `has_memory_finalize_permit(wf_uuid)`
    # then compared the permit's real value against the WRONG workflow's
    # uuid and always returned False, denying a write that held a
    # perfectly valid permit. The fix must derive the uuid to validate
    # against a workflow-scoped target from the target PATH's own
    # `workflows/{wf}/` segment, never from a "latest workflow" heuristic.
    name = "pretooluse-guard/allows-workflow-scoped-memory-write-with-newer-unrelated-workflow"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    state = tmp_dir / ".craftflow" / "state"
    state.mkdir(parents=True)
    permitted_wf_uuid = "wf-delete-duplicate-claude-skills-a-20260819-061236-4c389a0d"
    (state / ".memory-finalize").write_text(permitted_wf_uuid, encoding="utf-8")

    wf_dir = state / "workflows"
    wf_dir.mkdir(parents=True)
    # The workflow the permit was actually issued for -- written FIRST.
    (wf_dir / f"{permitted_wf_uuid}.json").write_text(
        f'{{"workflow_uuid":"{permitted_wf_uuid}"}}', encoding="utf-8"
    )
    # A second, unrelated, main-tree-only (worktree_path=None, always
    # "live") workflow, written AFTER -- so it is the mtime-latest live
    # candidate, exactly like a concurrent DEBUG workflow in the same
    # session.
    unrelated_wf_uuid = "wf-debug-memory-write-permit-lift-p-20260819-104706-0fce4897"
    (wf_dir / f"{unrelated_wf_uuid}.json").write_text(
        f'{{"workflow_uuid":"{unrelated_wf_uuid}","worktree_path":null}}', encoding="utf-8"
    )

    permitted_wf_memory_dir = wf_dir / permitted_wf_uuid
    permitted_wf_memory_dir.mkdir(parents=True)
    target = permitted_wf_memory_dir / "activeContext.md"
    target.write_text("# Active Context\n", encoding="utf-8")

    payload = {
        "tool_name": "Write",
        "cwd": str(tmp_dir),
        "tool_input": {"file_path": str(target), "content": "# updated\n"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(
            name,
            f"guard blocked a workflow-scoped write with a valid permit for that exact "
            f"workflow, just because a newer, unrelated live workflow existed; got: {out!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_denies_workflow_scoped_memory_write_with_permit_for_different_workflow(
    tmp_dir: Path,
) -> None:
    # Negative control (critical -- must not widen protection scope while
    # fixing the false-deny above): a permit that is valid, but for a
    # DIFFERENT workflow than the one whose memory file is being written,
    # must still correctly DENY -- lifting must stay scoped per workflow
    # for workflow-scoped paths, not become "any live permit authorizes
    # any workflow's memory file."
    name = "pretooluse-guard/denies-workflow-scoped-memory-write-with-permit-for-different-workflow"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    state = tmp_dir / ".craftflow" / "state"
    state.mkdir(parents=True)
    permitted_wf_uuid = "wf-some-other-workflow-1234"
    (state / ".memory-finalize").write_text(permitted_wf_uuid, encoding="utf-8")

    wf_dir = state / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / f"{permitted_wf_uuid}.json").write_text(
        f'{{"workflow_uuid":"{permitted_wf_uuid}"}}', encoding="utf-8"
    )

    target_wf_uuid = "wf-target-workflow-5678"
    (wf_dir / f"{target_wf_uuid}.json").write_text(
        f'{{"workflow_uuid":"{target_wf_uuid}"}}', encoding="utf-8"
    )
    target_wf_memory_dir = wf_dir / target_wf_uuid
    target_wf_memory_dir.mkdir(parents=True)
    target = target_wf_memory_dir / "activeContext.md"
    target.write_text("# Active Context\n", encoding="utf-8")

    payload = {
        "tool_name": "Write",
        "cwd": str(tmp_dir),
        "tool_input": {"file_path": str(target), "content": "# updated\n"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            f"expected deny: permit is valid for a DIFFERENT workflow than the one "
            f"whose memory file this write targets; got: {out!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_allows_project_tier_memory_write_when_newer_unrelated_workflow_exists(
    tmp_dir: Path,
) -> None:
    # Variant (c): the SAME permit must also lift the block for a
    # project-tier memory file (`.craftflow/state/project/*.md`), which is
    # not owned by any single workflow -- unlike the workflow-scoped case
    # above, presence of a valid permit alone (no path-derived workflow
    # uuid to compare against) is sufficient, and must not regress just
    # because a newer, unrelated live workflow also exists on disk.
    name = "pretooluse-guard/allows-project-tier-memory-write-with-newer-unrelated-workflow"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    state = tmp_dir / ".craftflow" / "state"
    state.mkdir(parents=True)
    permitted_wf_uuid = "wf-delete-duplicate-claude-skills-a-20260819-061236-4c389a0d"
    (state / ".memory-finalize").write_text(permitted_wf_uuid, encoding="utf-8")

    wf_dir = state / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / f"{permitted_wf_uuid}.json").write_text(
        f'{{"workflow_uuid":"{permitted_wf_uuid}"}}', encoding="utf-8"
    )
    unrelated_wf_uuid = "wf-debug-memory-write-permit-lift-p-20260819-104706-0fce4897"
    (wf_dir / f"{unrelated_wf_uuid}.json").write_text(
        f'{{"workflow_uuid":"{unrelated_wf_uuid}","worktree_path":null}}', encoding="utf-8"
    )

    project_dir = state / "project"
    project_dir.mkdir(parents=True)
    target = project_dir / "patterns.md"
    target.write_text("# Project Patterns\n", encoding="utf-8")

    payload = {
        "tool_name": "Write",
        "cwd": str(tmp_dir),
        "tool_input": {"file_path": str(target), "content": "# updated\n"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(
            name,
            f"guard blocked a project-tier write with a valid permit, just because a "
            f"newer, unrelated live workflow existed; got: {out!r}",
        )
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


def _stage_inflight_skill_candidate(
    root: Path, name: str, candidate_id: str = "cand0001", status: str = "candidate"
) -> None:
    """Test fixture (REM-FIX round 2, CRITICAL 1+2): stage a ledger candidate
    plus its matching staged proposal so `<name>` is recognized as ACTIVELY
    IN-FLIGHT in the skill-distillation pipeline -- the only shape the
    narrowed `_is_protected_skill_promotion_path()` now protects. Mirrors the
    real `craftflow_skill_ledger.py` candidate schema and the real
    `skill-author` agent's staged-proposal frontmatter shape."""
    ledger_path = root / ".craftflow" / "state" / "project" / "skill-candidates.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "candidates": [
                    {
                        "id": candidate_id,
                        "surface": "test/surface",
                        "signature": "test recurring signature",
                        "workflows": ["wf-a", "wf-b"],
                        "distinct_workflows": 2,
                        "max_severity": "high",
                        "evidence": [],
                        "first_seen": "2026-01-01T00:00:00Z",
                        "last_seen": "2026-01-01T00:00:00Z",
                        "status": status,
                        "promoted_skill": None,
                        "rejected_reason": None,
                        "rejected_at_distinct_workflows": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    proposal_dir = root / ".craftflow" / "state" / "project" / "skill-proposals" / candidate_id
    proposal_dir.mkdir(parents=True, exist_ok=True)
    (proposal_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: \"Use when testing narrowed skill-promotion "
        "protection end to end.\"\n---\n\nBody.\n",
        encoding="utf-8",
    )


def test_pretooluse_guard_allows_unrelated_hand_authored_skill_write_no_ledger(tmp_dir: Path) -> None:
    # CRITICAL 1 (REM-FIX round 2): an ordinary, unrelated hand-authored
    # skill (no matching ledger entry, no ledger file at all) must be
    # allowed -- the STANDARD Claude Code/Cursor project-skill convention
    # (.claude/skills/<name>/SKILL.md) is not craftflow-exclusive. This is
    # the regression test for the over-broad blanket path-shape deny; it
    # must FAIL against the pre-fix code (which denies unconditionally) to
    # prove it is a real test.
    name = "pretooluse-guard/allows-unrelated-hand-authored-skill-write-no-ledger"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    target = tmp_dir / ".claude" / "skills" / "my-handwritten-skill" / "SKILL.md"
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(target),
            "content": "---\nname: my-handwritten-skill\ndescription: \"hand authored\"\n---\n",
        },
        "cwd": str(tmp_dir),
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(
            name,
            "expected allow (no output) for an unrelated hand-authored skill write with "
            f"no matching ledger entry / no ledger file at all; got: {out!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_denies_edit_write_to_claude_skills_skill_md(tmp_dir: Path) -> None:
    # HIGH 5 (REM-FIX, skill-distillation Phase 2 remediation): a raw
    # Edit/Write tool call must never be able to reach
    # `.claude/skills/<name>/SKILL.md` directly -- the sole authorized writer
    # is craftflow_skill_promote.py's own internal file I/O via --approve.
    # REM-FIX round 2 (CRITICAL 1): protection is now narrowed to in-flight
    # ledger candidates, so "foo" must be staged as one for this deny to
    # still fire -- proves the narrowed check still protects what it needs
    # to (see test_pretooluse_guard_allows_unrelated_hand_authored_skill_write_no_ledger
    # for the companion regression proof with NO staged candidate).
    name = "pretooluse-guard/denies-edit-write-to-claude-skills-skill-md"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "foo")
    target = tmp_dir / ".claude" / "skills" / "foo" / "SKILL.md"
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "---\nname: foo\n---\n"},
        "cwd": str(tmp_dir),
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a raw Write to .claude/skills/foo/SKILL.md; got: {out!r}")
        return
    if "skill-promotion-path" not in out:
        fail(name, f"expected the deny reason to name 'skill-promotion-path'; got: {out!r}")
        return
    # skill-promotion-path violations must keep their accurate skill-
    # promotion explanatory text (misleading-deny-message fix).
    if "craftflow_skill_promote.py" not in out or "craftflow_skill_propose.py" not in out:
        fail(name, f"expected the deny message to still mention craftflow_skill_promote.py/craftflow_skill_propose.py; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_edit_write_to_cursor_skills_skill_md(tmp_dir: Path) -> None:
    # REM-FIX round 2 (CRITICAL 1): staged as an in-flight ledger candidate
    # (status "proposed" this time, to exercise both recognized statuses).
    name = "pretooluse-guard/denies-edit-write-to-cursor-skills-skill-md"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "foo", status="proposed")
    target = tmp_dir / ".cursor" / "skills" / "foo" / "SKILL.md"
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target), "old_string": "a", "new_string": "b"},
        "cwd": str(tmp_dir),
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a raw Edit to .cursor/skills/foo/SKILL.md; got: {out!r}")
        return
    if "skill-promotion-path" not in out:
        fail(name, f"expected the deny reason to name 'skill-promotion-path'; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_redirect_to_claude_skills_skill_md(tmp_dir: Path) -> None:
    # REM-FIX round 2 (CRITICAL 1): staged as an in-flight ledger candidate.
    name = "pretooluse-guard/denies-bash-redirect-to-claude-skills-skill-md"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "foo")
    target = tmp_dir / ".claude" / "skills" / "foo" / "SKILL.md"
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"echo 'hi' > {target}"},
        "cwd": str(tmp_dir),
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash redirect into .claude/skills/foo/SKILL.md; got: {out!r}")
        return
    if "skill-promotion-path" not in out:
        fail(name, f"expected the deny reason to name 'skill-promotion-path'; got: {out!r}")
        return
    # skill-promotion-path Bash violations must keep their accurate skill-
    # promotion explanatory text, phrased for the Bash-redirect path
    # (misleading-deny-message fix, fix-verify cycle 1).
    if "craftflow_skill_promote.py" not in out or "craftflow_skill_propose.py" not in out:
        fail(name, f"expected the deny message to still mention craftflow_skill_promote.py/craftflow_skill_propose.py; got: {out!r}")
        return
    if "Bash redirect" not in out:
        fail(name, f"expected the Bash-path deny message to say 'Bash redirect' (not 'raw Edit/Write'); got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_python_oneliner_write_to_inflight_skill(tmp_dir: Path) -> None:
    # CRITICAL 2 (REM-FIX round 2): live-reproduced bypass -- the
    # `skill_promotion_violations` lane in `_handle_bash` only scanned
    # `>`/`>>`/`tee` redirect targets, never cross-checking the
    # `_python_script_write_targets()` detector already built (and used) for
    # memory-file protection. A python -c one-liner using open().write()
    # against an in-flight skill's SKILL.md was silently ALLOWED before this
    # fix.
    name = "pretooluse-guard/denies-python-oneliner-write-to-inflight-skill"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "newskill")
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {
            "command": "python3 -c \"open('.claude/skills/newskill/SKILL.md', 'w').write('pwned')\""
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a python -c open().write() bypass targeting an in-flight skill; got: {out!r}")
        return
    if "skill-promotion-path" not in out:
        fail(name, f"expected the deny reason to name 'skill-promotion-path'; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_python_os_system_write_to_inflight_skill(tmp_dir: Path) -> None:
    # CRITICAL 2 (REM-FIX round 2): live-reproduced bypass -- os.system(),
    # never cross-checked against the `_python_suspicious_mechanism_targets()`
    # detector already built (and used) for memory-file protection.
    name = "pretooluse-guard/denies-python-os-system-write-to-inflight-skill"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "newskill")
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {
            "command": "python3 -c \"import os; os.system('echo pwned > .claude/skills/newskill/SKILL.md')\""
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a python os.system() bypass targeting an in-flight skill; got: {out!r}")
        return
    if "skill-promotion-path" not in out:
        fail(name, f"expected the deny reason to name 'skill-promotion-path'; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_python_heredoc_write_to_inflight_skill(tmp_dir: Path) -> None:
    # CRITICAL 2 (REM-FIX round 2): live-reproduced bypass -- a heredoc/stdin-
    # fed python script, never cross-checked against
    # `_python_script_write_targets()`'s whole-command-text scan.
    name = "pretooluse-guard/denies-python-heredoc-write-to-inflight-skill"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "newskill")
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {
            "command": (
                "python3 - <<'EOF'\n"
                "open('.claude/skills/newskill/SKILL.md','w').write('pwned-heredoc')\n"
                "EOF"
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a python heredoc bypass targeting an in-flight skill; got: {out!r}")
        return
    if "skill-promotion-path" not in out:
        fail(name, f"expected the deny reason to name 'skill-promotion-path'; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX round 3: ledger/proposal tamper protection (CRITICAL 1) + fail-
# closed on ledger corruption (CRITICAL 2).
# ---------------------------------------------------------------------------

def test_pretooluse_guard_denies_tamper_then_write_via_ledger_write(tmp_dir: Path) -> None:
    # CRITICAL 1 (REM-FIX round 3): live-reproduced tamper sequence -- stage
    # an in-flight candidate, confirm a Write to its SKILL.md is denied
    # (round 2 behavior), then Write the ledger file itself (replacing
    # `candidates` with `[]`) -- this was previously ALLOWED (not itself
    # protected) -- which then made the retried SAME write to the SKILL.md
    # ALLOWED too, since the guard re-reads the (now-tampered) ledger on
    # every invocation. This test proves the tamper is now denied AT THE
    # LEDGER-WRITE STEP, so the SKILL.md stays protected.
    name = "pretooluse-guard/denies-tamper-then-write-via-ledger-write"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "foo")
    skill_target = tmp_dir / ".claude" / "skills" / "foo" / "SKILL.md"

    skill_payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(skill_target), "content": "pwned"},
        "cwd": str(tmp_dir),
    }
    _, skill_out = run_hook("craftflow_pretooluse_guard.py", skill_payload, env)
    if '"permissionDecision": "deny"' not in skill_out and '"permissionDecision":"deny"' not in skill_out:
        fail(name, f"expected the initial SKILL.md write to be denied; got: {skill_out!r}")
        return

    ledger_target = tmp_dir / ".craftflow" / "state" / "project" / "skill-candidates.json"
    ledger_payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(ledger_target),
            "content": json.dumps({"schema_version": 1, "candidates": []}),
        },
        "cwd": str(tmp_dir),
    }
    _, ledger_out = run_hook("craftflow_pretooluse_guard.py", ledger_payload, env)
    if '"permissionDecision": "deny"' not in ledger_out and '"permissionDecision":"deny"' not in ledger_out:
        fail(name, f"expected the ledger-tamper Write to be DENIED; got: {ledger_out!r}")
        return
    if "skill-ledger-write" not in ledger_out:
        fail(name, f"expected the deny reason to name 'skill-ledger-write'; got: {ledger_out!r}")
        return

    # The tamper write was denied (and this subprocess-only harness never
    # applies a denied write to disk either way), so the on-disk ledger is
    # unchanged -- the retried SAME write to the SKILL.md must STILL be
    # denied, proving protection survived the tamper attempt.
    _, retry_out = run_hook("craftflow_pretooluse_guard.py", skill_payload, env)
    if '"permissionDecision": "deny"' not in retry_out and '"permissionDecision":"deny"' not in retry_out:
        fail(name, f"expected the retried SKILL.md write to STILL be denied; got: {retry_out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_edit_write_to_reliability_gates_ledger(tmp_dir: Path) -> None:
    name = "pretooluse-guard/denies-edit-write-to-reliability-gates-ledger"
    ledger_path = tmp_dir / ".craftflow" / "state" / "project" / "reliability-gates.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text('{"schema_version": 1, "gates": []}')
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(ledger_path), "content": "{}"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if (
        '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out
    ) and "reliability-gates-write" in out:
        # reliability-gates-write violations are not skill-related -- the
        # deny message must not carry the skill-promotion/ledger
        # explanatory text (misleading-deny-message fix), and must
        # accurately describe the reliability-gates ledger instead.
        for forbidden in ("skill_promote", "skill_propose", "skill-candidate ledger"):
            if forbidden in out:
                fail(name, f"expected reliability-gates-write message to NOT mention {forbidden!r}; got: {out!r}")
                return
        if "reliability-gates" not in out.lower():
            fail(name, f"expected reliability-gates-write message to describe the reliability-gates ledger; got: {out!r}")
            return
        ok(name)
    else:
        fail(name, f"expected deny reliability-gates-write, got: {out!r}")


def test_pretooluse_guard_denies_bash_redirect_to_reliability_gates_ledger(tmp_dir: Path) -> None:
    name = "pretooluse-guard/denies-bash-redirect-to-reliability-gates-ledger"
    project_root = tmp_dir / "project"
    ledger_path = project_root / ".craftflow" / "state" / "project" / "reliability-gates.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text('{"schema_version": 1, "gates": []}')
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "echo tampered > .craftflow/state/project/reliability-gates.json"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if (
        '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out
    ) and "reliability-gates-write" in out:
        # reliability-gates-write Bash violations are not skill-related --
        # the deny message must not carry the skill-promotion/ledger
        # explanatory text (misleading-deny-message fix, fix-verify cycle
        # 1), and must accurately describe the reliability-gates ledger
        # instead.
        for forbidden in ("skill_promote", "skill_propose", "skill-candidate ledger"):
            if forbidden in out:
                fail(name, f"expected reliability-gates-write Bash message to NOT mention {forbidden!r}; got: {out!r}")
                return
        if "reliability-gates" not in out.lower():
            fail(name, f"expected reliability-gates-write Bash message to describe the reliability-gates ledger; got: {out!r}")
            return
        ok(name)
    else:
        fail(name, f"expected deny reliability-gates-write, got: {out!r}")


def test_pretooluse_guard_allows_authorized_reliability_gates_script_bash_invocation(tmp_dir: Path) -> None:
    name = "pretooluse-guard/allows-reliability-gates-script-invocation"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": (
                "python3 tools/craftflow-plugin/plugins/craftflow/scripts/"
                "craftflow_reliability_gates.py --record-evidence worktree-merge-safety "
                "--wf wf-1 --outcome pass --state-dir .craftflow/state"
            )
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        ok(name)
    else:
        fail(name, f"expected allow (script invocation is not a write-to-ledger command), got: {out!r}")


def test_pretooluse_guard_denies_write_to_skill_proposal_file(tmp_dir: Path) -> None:
    # CRITICAL 1 (REM-FIX round 3): the staged-proposal file backing an
    # in-flight candidate's name<->path linkage must be protected the same
    # unconditional way as the ledger itself -- tampering with the
    # proposal's `name:` frontmatter is an equally viable way to redirect
    # protection away from the real in-flight skill name.
    name = "pretooluse-guard/denies-write-to-skill-proposal-file"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "foo")
    proposal_target = (
        tmp_dir / ".craftflow" / "state" / "project" / "skill-proposals" / "cand0001" / "SKILL.md"
    )
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(proposal_target), "content": "---\nname: renamed\n---\n"},
        "cwd": str(tmp_dir),
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Write to a staged skill-proposal file; got: {out!r}")
        return
    if "skill-ledger-write" not in out:
        fail(name, f"expected the deny reason to name 'skill-ledger-write'; got: {out!r}")
        return
    # skill-ledger-write violations must keep their accurate ledger
    # explanatory text (misleading-deny-message fix).
    if "skill-candidate ledger" not in out:
        fail(name, f"expected the deny message to still mention the skill-candidate ledger; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_write_to_not_yet_existing_proposal_path(tmp_dir: Path) -> None:
    # REM-FIX round 4 (architectural fix): the round-3 guard scoped
    # protection of the proposals tree to targets that ALREADY EXISTED on
    # disk, to avoid breaking skill-author's own legitimate first-time
    # `Write` there -- but `candidate_id()` is a deterministic
    # `sha1(surface+signature)` hash, precomputable OFFLINE with no
    # observation needed, and the ledger is freely readable via `--query`.
    # That let an attacker precompute a FUTURE legitimate candidate's exact
    # id and plant a file there FIRST (a precompute-squat hijack), before the
    # round-3 guard's existence check would ever protect it. Now that
    # `skill-author` stages proposals through `craftflow_skill_propose.py`
    # instead of a direct `Write`, there is no longer a legitimate direct-
    # `Write` caller to carve an exception out for -- the ENTIRE tree is
    # protected unconditionally, existing or not, matching or non-matching
    # any ledger entry. This is the direct regression test proving that
    # precompute-squat hijack is now impossible regardless of timing.
    name = "pretooluse-guard/denies-write-to-not-yet-existing-proposal-path"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    new_proposal_target = (
        tmp_dir / ".craftflow" / "state" / "project" / "skill-proposals" / "cand-brand-new" / "SKILL.md"
    )
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(new_proposal_target),
            "content": "---\nname: brand-new-skill\n---\n",
        },
        "cwd": str(tmp_dir),
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Write to a NOT-yet-existing proposal path; got: {out!r}")
        return
    if "skill-ledger-write" not in out:
        fail(name, f"expected the deny reason to name 'skill-ledger-write'; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_redirect_to_not_yet_existing_proposal_path(tmp_dir: Path) -> None:
    # REM-FIX round 4: same precompute-squat proof as above, via a Bash
    # redirect instead of a raw Write tool call.
    name = "pretooluse-guard/denies-bash-redirect-to-not-yet-existing-proposal-path"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    new_proposal_target = (
        tmp_dir / ".craftflow" / "state" / "project" / "skill-proposals" / "cand-squat" / "SKILL.md"
    )
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": f"mkdir -p $(dirname {new_proposal_target}) && echo 'pwned' > {new_proposal_target}"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash redirect into a NOT-yet-existing proposal path; got: {out!r}")
        return
    if "skill-ledger-write" not in out:
        fail(name, f"expected the deny reason to name 'skill-ledger-write'; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_python_oneliner_write_to_not_yet_existing_proposal_path(tmp_dir: Path) -> None:
    # REM-FIX round 4: same precompute-squat proof, via a python -c
    # open().write() one-liner instead of a shell redirect.
    name = "pretooluse-guard/denies-python-oneliner-write-to-not-yet-existing-proposal-path"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    proposal_rel = ".craftflow/state/project/skill-proposals/cand-squat2/SKILL.md"
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {
            "command": f"python3 -c \"import os; os.makedirs('{os.path.dirname(proposal_rel)}', exist_ok=True); open('{proposal_rel}', 'w').write('pwned')\""
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a python -c write into a NOT-yet-existing proposal path; got: {out!r}")
        return
    if "skill-ledger-write" not in out:
        fail(name, f"expected the deny reason to name 'skill-ledger-write'; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_redirect_to_skill_ledger(tmp_dir: Path) -> None:
    # CRITICAL 1 (REM-FIX round 3): same tamper vector as the Edit/Write
    # test above, but via a Bash redirect -- the ledger must be protected
    # across both write mechanisms, mirroring how skill-promotion-path
    # itself is enforced on both lanes.
    name = "pretooluse-guard/denies-bash-redirect-to-skill-ledger"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "foo")
    ledger_target = tmp_dir / ".craftflow" / "state" / "project" / "skill-candidates.json"
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": f"echo '{{\"candidates\": []}}' > {ledger_target}"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash redirect overwriting the skill-candidate ledger; got: {out!r}")
        return
    if "skill-ledger-write" not in out:
        fail(name, f"expected the deny reason to name 'skill-ledger-write'; got: {out!r}")
        return
    # skill-ledger-write Bash violations must keep their accurate ledger
    # explanatory text (misleading-deny-message fix, fix-verify cycle 1).
    if "skill-candidate ledger" not in out:
        fail(name, f"expected the deny message to still mention the skill-candidate ledger; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_write_when_ledger_json_is_malformed(tmp_dir: Path) -> None:
    # CRITICAL 2 (REM-FIX round 3): live-reproduced bypass -- a legitimate
    # in-flight candidate + staged proposal exist, but the ledger file is
    # truncated to invalid JSON. Before this fix, `load_ledger()`'s own
    # `(OSError, ValueError)` catch silently degraded to an empty ledger --
    # indistinguishable from the benign "no ledger file" case -- so
    # `_inflight_skill_promotion_paths()` protected zero candidates and a
    # direct Write to the candidate's SKILL.md was ALLOWED, with nothing
    # logged to craftflow-hook-events.log. This test proves the write is now
    # DENIED (fail-closed) instead.
    name = "pretooluse-guard/denies-write-when-ledger-json-is-malformed"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "foo")
    ledger_path = tmp_dir / ".craftflow" / "state" / "project" / "skill-candidates.json"
    # Truncate to invalid JSON -- a single corrupted byte is enough.
    ledger_path.write_text("{not valid json", encoding="utf-8")

    target = tmp_dir / ".claude" / "skills" / "foo" / "SKILL.md"
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "pwned"},
        "cwd": str(tmp_dir),
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for a write to an in-flight skill's SKILL.md when the ledger JSON "
            f"is malformed; got: {out!r}",
        )
        return
    if "skill-promotion-path" not in out:
        fail(name, f"expected the deny reason to name 'skill-promotion-path'; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_any_skill_write_when_ledger_malformed_no_matching_candidate(
    tmp_dir: Path,
) -> None:
    # CRITICAL 2 (REM-FIX round 3): once the ledger EXISTS but is corrupt,
    # protection fails closed for ANY `.claude/skills/<name>/SKILL.md`
    # write -- not just a name that happens to match a (now-unreadable)
    # candidate -- since the guard cannot prove ANY skill write is safe
    # when it cannot read the ledger at all. This is the rare,
    # operator-actionable corrupt-ledger case, distinct from the common
    # "no ledger file" case proven safe (still allowed) by
    # test_pretooluse_guard_allows_unrelated_hand_authored_skill_write_no_ledger.
    name = "pretooluse-guard/denies-any-skill-write-when-ledger-malformed-no-matching-candidate"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    ledger_path = tmp_dir / ".craftflow" / "state" / "project" / "skill-candidates.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("{not valid json", encoding="utf-8")

    target = tmp_dir / ".claude" / "skills" / "totally-unrelated" / "SKILL.md"
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "hand authored"},
        "cwd": str(tmp_dir),
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for ANY skill write while the ledger JSON is malformed; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX round 4: malformed per-candidate ledger entry (missing "status" or
# "id") must fail closed the same way whole-file ledger corruption already
# does, not be silently treated as "legitimately not in-flight" (hunter's 2nd
# CRITICAL finding from the round-3 remediation batch).
# ---------------------------------------------------------------------------

def test_inflight_skill_promotion_paths_fails_closed_on_malformed_candidate_missing_status(
    tmp_dir: Path,
) -> None:
    name = "pretooluse-guard/inflight-fn-fails-closed-on-malformed-candidate-missing-status"
    ledger_path = tmp_dir / ".craftflow" / "state" / "project" / "skill-candidates.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps({
            "schema_version": 1,
            "candidates": [{"id": "cand-malformed", "surface": "s", "signature": "sig"}],
        }),
        encoding="utf-8",
    )
    paths, ledger_corrupt = pretooluse_guard._inflight_skill_promotion_paths(tmp_dir)
    if not ledger_corrupt:
        fail(name, "expected ledger_corrupt=True when a candidate entry is missing 'status'")
        return
    if paths:
        fail(name, f"expected an empty path set when failing closed on a malformed candidate entry, got: {paths}")
        return
    ok(name)


def test_inflight_skill_promotion_paths_fails_closed_on_malformed_candidate_missing_id(
    tmp_dir: Path,
) -> None:
    name = "pretooluse-guard/inflight-fn-fails-closed-on-malformed-candidate-missing-id"
    ledger_path = tmp_dir / ".craftflow" / "state" / "project" / "skill-candidates.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps({
            "schema_version": 1,
            "candidates": [{"status": "candidate", "surface": "s", "signature": "sig"}],
        }),
        encoding="utf-8",
    )
    paths, ledger_corrupt = pretooluse_guard._inflight_skill_promotion_paths(tmp_dir)
    if not ledger_corrupt:
        fail(name, "expected ledger_corrupt=True when a candidate entry is missing 'id'")
        return
    if paths:
        fail(name, f"expected an empty path set when failing closed on a malformed candidate entry, got: {paths}")
        return
    ok(name)


def test_inflight_skill_promotion_paths_still_skips_legitimately_terminal_candidates(
    tmp_dir: Path,
) -> None:
    # Regression guard for fix #4's own scoping: a WELL-FORMED candidate
    # entry with status "rejected"/"promoted" (has BOTH 'status' and 'id')
    # must stay a normal skip, never be treated as malformed/ledger_corrupt.
    name = "pretooluse-guard/inflight-fn-still-skips-legitimately-terminal-candidates"
    ledger_path = tmp_dir / ".craftflow" / "state" / "project" / "skill-candidates.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps({
            "schema_version": 1,
            "candidates": [{"id": "cand-done", "status": "rejected", "surface": "s", "signature": "sig"}],
        }),
        encoding="utf-8",
    )
    paths, ledger_corrupt = pretooluse_guard._inflight_skill_promotion_paths(tmp_dir)
    if ledger_corrupt:
        fail(name, "expected ledger_corrupt=False for a well-formed terminal-status candidate")
        return
    if paths:
        fail(name, f"expected an empty path set (no in-flight candidates), got: {paths}")
        return
    ok(name)


def test_pretooluse_guard_fails_closed_when_candidate_entry_missing_status(tmp_dir: Path) -> None:
    # End-to-end proof (subprocess-level, not just the pure-function unit
    # tests above): a real Write to an UNRELATED skill path is now denied
    # while a malformed candidate entry exists in the ledger, exactly
    # mirroring how a corrupt/unparseable ledger FILE already fails closed.
    name = "pretooluse-guard/fails-closed-when-candidate-entry-missing-status"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    ledger_path = tmp_dir / ".craftflow" / "state" / "project" / "skill-candidates.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps({
            "schema_version": 1,
            "candidates": [{"id": "cand-malformed", "surface": "s", "signature": "missing status key"}],
        }),
        encoding="utf-8",
    )
    target = tmp_dir / ".claude" / "skills" / "totally-unrelated" / "SKILL.md"
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "hand authored"},
        "cwd": str(tmp_dir),
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for ANY skill write while a ledger candidate entry is missing 'status'; got: {out!r}")
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


def test_pretooluse_guard_allows_bash_permit_write_multiline_command(tmp_dir: Path) -> None:
    # Root-cause regression (2026-08-19 DEBUG workflow, live-reproduced
    # chicken-and-egg deadlock): the router's own SKILL.md-documented
    # memory-finalize permit-write is a single Bash() tool call, and real
    # multi-step router flows routinely combine it with a second command on
    # its own line in the SAME Bash() call (e.g. a trailing `echo done`
    # sentinel, or an unrelated earlier setup line) -- the same ordinary
    # multi-statement shape already covered for a `;`-joined compound
    # command above, but joined with a bare newline instead. Before the
    # split_subcommands() fix, the newline never split the command into two
    # subcommands, so matches_memory_finalize_permit_shape() saw a 7-token
    # blob instead of the printf line's own 5 tokens and always returned
    # False, denying the permit write.
    name = "pretooluse-guard/allows-bash-permit-write-multiline-command"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": "printf '%s' 'wf-test-1234' > .craftflow/state/.memory-finalize\necho done"
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(
            name,
            f"expected allow for the documented permit-write shape sharing a Bash() call "
            f"with a second newline-separated command; got: {out!r}",
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
    # A pure worktree-confinement violation has nothing to do with skill
    # promotion -- the deny message must not carry the skill-promotion/
    # ledger explanatory text that is only accurate for those OTHER
    # violation types (misleading-deny-message fix).
    for forbidden in ("skill_promote", "skill_propose", "skill-candidate ledger"):
        if forbidden in out:
            fail(name, f"expected worktree-confinement message to NOT mention {forbidden!r}; got: {out!r}")
            return
    if "worktree_path" not in out and "working directory" not in out:
        fail(name, f"expected worktree-confinement message to explain the cwd/worktree_path escape; got: {out!r}")
        return
    if "run it manually outside the agent session" not in out:
        fail(name, f"expected worktree-confinement message to keep the 'run it manually outside the agent session' guidance; got: {out!r}")
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


def _claude_code_memory_slug(cwd: Path) -> str:
    """Test-side mirror of the slug algorithm, verified empirically against
    real ~/.claude/projects/* directories on disk (see bug investigation):
    every '/' and '.' in the absolute cwd string is replaced with '-'."""
    return "".join("-" if c in "/." else c for c in str(cwd))


def test_pretooluse_guard_edit_write_allows_claude_code_own_memory_dir(tmp_dir: Path) -> None:
    # Regression: Claude Code's own global auto-memory directory
    # (~/.claude/projects/<slug-of-cwd>/memory/*.md) is Claude Code's own
    # infrastructure, not project code -- it must never be denied by the
    # worktree-confinement check just because it sits outside cwd/worktree.
    name = "pretooluse-guard/edit-write-allows-claude-code-own-memory-dir"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    cwd = project_root.resolve()
    slug = _claude_code_memory_slug(cwd)
    target = Path.home() / ".claude" / "projects" / slug / "memory" / "some-file.md"
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "cwd": str(cwd),
        "tool_input": {"file_path": str(target)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for a Write to Claude Code's own auto-memory dir for this cwd; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_allows_claude_code_own_memory_md_exact_file(tmp_dir: Path) -> None:
    # Variant: the top-level MEMORY.md file (not under memory/) must also be
    # allowed -- both documented forms of Claude Code's own auto-memory
    # surface for this cwd.
    name = "pretooluse-guard/edit-write-allows-claude-code-own-memory-md-exact-file"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    cwd = project_root.resolve()
    slug = _claude_code_memory_slug(cwd)
    target = Path.home() / ".claude" / "projects" / slug / "MEMORY.md"
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Edit",
        "cwd": str(cwd),
        "tool_input": {"file_path": str(target)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for an Edit to Claude Code's own MEMORY.md for this cwd; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_denies_other_project_claude_code_memory_dir(tmp_dir: Path) -> None:
    # Non-regression / anti-blanket-grant: a Claude Code auto-memory dir
    # for a DIFFERENT cwd's slug (not the trusted cwd on this call) must
    # still be denied -- the allowance is derived only from the guard's own
    # trusted cwd, never a blanket ~/.claude/projects/** grant that would
    # leak into another project's memory.
    name = "pretooluse-guard/edit-write-denies-other-project-claude-code-memory-dir"
    project_root = tmp_dir / "project"
    other_project = tmp_dir / "other-project"
    project_root.mkdir(parents=True)
    other_project.mkdir(parents=True)
    cwd = project_root.resolve()
    other_slug = _claude_code_memory_slug(other_project.resolve())
    target = Path.home() / ".claude" / "projects" / other_slug / "memory" / "some-file.md"
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "cwd": str(cwd),
        "tool_input": {"file_path": str(target)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a DIFFERENT project's Claude Code memory dir; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_allows_claude_code_own_session_scoped_memory_dir(tmp_dir: Path) -> None:
    # Regression (session-scoped shape): after a context-compaction resume,
    # Claude Code's own auto-memory root for a session can be computed as
    # SESSION-scoped instead of project-scoped --
    # ~/.claude/projects/<slug-of-cwd>/<session-uuid>/memory/<file>.md --
    # this is still Claude Code's own infrastructure for this exact trusted
    # cwd, just with one extra session-uuid segment before memory/. It must
    # be permitted the same way the pre-existing project-scoped
    # <slug>/memory/** grant already is.
    name = "pretooluse-guard/edit-write-allows-claude-code-own-session-scoped-memory-dir"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    cwd = project_root.resolve()
    slug = _claude_code_memory_slug(cwd)
    session_uuid = "17021386-8f91-4f13-8fa2-3fa3355ef61c"
    target = (
        Path.home() / ".claude" / "projects" / slug / session_uuid / "memory" / "project_portal_ci_slow_build_and_test.md"
    )
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "cwd": str(cwd),
        "tool_input": {"file_path": str(target)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for a Write to Claude Code's own session-scoped auto-memory dir for this cwd; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_bash_worktree_confinement_only_message_omits_skill_text(tmp_dir: Path) -> None:
    # misleading-deny-message fix (fix-verify cycle 1, live-reproduced): the
    # catch-all deny block in `_handle_bash` fired the SAME hardcoded skill-
    # promotion boilerplate regardless of which violation type(s) actually
    # fired -- the exact defect class already fixed in `_handle_edit_write`
    # (see test_pretooluse_guard_edit_write_worktree_confinement_denies_outside
    # above). A Bash redirect into a protected memory file, from a cwd
    # outside its confinement, with zero skill relevance, must get accurate,
    # violation-specific text instead.
    name = "pretooluse-guard/bash-worktree-confinement-only-message-omits-skill-text"
    project_root = tmp_dir / "project"
    elsewhere = tmp_dir / "elsewhere"
    project_root.mkdir(parents=True)
    elsewhere.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    target = project_root / ".craftflow" / "state" / "activeContext.md"
    payload = {
        "tool_name": "Bash",
        "cwd": str(elsewhere.resolve()),
        "tool_input": {"command": f"echo hi > {target.resolve()}"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a Bash redirect into a protected memory file from a cwd outside its confinement; got: {out!r}")
        return
    if "worktree-confinement" not in out:
        fail(name, f"expected a distinct 'worktree-confinement' deny reason; got: {out!r}")
        return
    for forbidden in ("skill_promote", "skill_propose", "skill-candidate ledger", "skill-candidate-ledger"):
        if forbidden in out:
            fail(name, f"expected worktree-confinement-only Bash message to NOT mention {forbidden!r}; got: {out!r}")
            return
    if "worktree_path" not in out and "working directory" not in out:
        fail(name, f"expected worktree-confinement Bash message to explain the cwd/worktree_path escape; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_confinement_allows_workflow_json_when_worktree_path_stale(tmp_dir: Path) -> None:
    # Regression flow 4, exact realistic condition (fresh review pass 1
    # BLOCKING): worktree_path SET to a different, stale-looking sibling
    # path -- proves TRUE union semantics for the Edit/Write confinement
    # path specifically, not just inherited from Phase 3's bash-guard proof.
    #
    # Finding 2 (code-reviewer CHANGES_REQUESTED, REM-FIX cycle 1, MEDIUM,
    # test-rot symptom of Finding 1): the Edit/Write confinement call site
    # is write-confinement-sensitive, so it uses latest_live_workflow_payload()
    # (liveness-filtered) -- a worktree_path whose directory was never
    # created on disk gets excluded as "not live," collapsing `workflow` to
    # {} and `worktree_path` to None. The write below was then only
    # allowed because the target sits inside cwd -- NOT because of the
    # union-with-a-stale-worktree_path semantics this test's name/docstring
    # claims to prove. The stale_worktree directory is created here (a
    # genuinely different/unrelated path, but one that exists on disk) so
    # the workflow fixture stays "live" and its (irrelevant, different)
    # worktree_path value actually reaches resolve_confinement() -- proving
    # the write is allowed via the cwd branch of the cwd u worktree_path
    # union, independent of whatever worktree_path happens to be set to.
    name = "pretooluse-guard/edit-write-confinement-allows-workflow-json-when-worktree-path-stale"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    stale_worktree = tmp_dir / ".claude" / "worktrees" / "wf-stale-test"
    stale_worktree.mkdir(parents=True)
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


def _write_workflow_json_fixture_full(
    project_root: Path,
    wf_uuid: str,
    worktree_path: str | None,
    worktree_mode: str | None = None,
    session_id: str | None = None,
) -> Path:
    """Like `_write_workflow_json_fixture()` but exposes `worktree_mode`/
    `session_id` too -- needed for the Item A (cross-session
    workflow-identity leak) regression tests below, which specifically
    depend on `worktree_mode` and multi-file mtime ordering."""
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {"workflow_uuid": wf_uuid, "worktree_path": worktree_path}
    if worktree_mode is not None:
        payload["worktree_mode"] = worktree_mode
    if session_id is not None:
        payload["session_id"] = session_id
    path = wf_dir / f"{wf_uuid}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_pretooluse_guard_latest_workflow_ignores_terminal_worktree_mode_even_with_newest_mtime(tmp_dir: Path) -> None:
    # Regression (Item A root cause, 2026-08-18 bug investigation):
    # latest_workflow_file() used to pick whichever workflow JSON had the
    # single most recent mtime, globally -- with no filtering by liveness.
    # A finished workflow (worktree already merged and removed) whose JSON
    # file happens to be touched LAST still "wins" under the old logic,
    # leaking its stale/removed worktree_path into an unrelated
    # invocation's confinement check. Reproduces the exact live incident:
    # two workflow artifacts on disk, the STALE one is newer by mtime.
    name = "pretooluse-guard/latest-workflow-ignores-terminal-worktree-mode-even-with-newest-mtime"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    live_worktree = tmp_dir / "live-worktree"
    live_worktree.mkdir(parents=True)
    # The "stale" workflow's own worktree directory is genuinely gone --
    # matches the router's real merge-and-remove behavior.
    stale_worktree = tmp_dir / "removed-worktree"

    old_path = _write_workflow_json_fixture_full(
        project_root, "wf-old-live", str(live_worktree.resolve()), worktree_mode="auto_created"
    )
    new_path = _write_workflow_json_fixture_full(
        project_root, "wf-new-stale", str(stale_worktree), worktree_mode="merged_and_removed"
    )
    now = time.time()
    os.utime(old_path, (now - 3600, now - 3600))  # older
    os.utime(new_path, (now, now))  # newest mtime -- but terminal

    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}

    # 1. A write inside the LIVE (older-mtime) workflow's worktree must be
    #    ALLOWED -- proves the still-active workflow's worktree_path won,
    #    not the newest-mtime terminal one.
    payload_live = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((live_worktree / "scratch.md").resolve())},
    }
    _, out_live = run_hook("craftflow_pretooluse_guard.py", payload_live, env)
    if out_live:
        fail(name, f"expected allow for a write inside the LIVE workflow's worktree; got: {out_live!r}")
        return

    # 2. A write inside the STALE (newest-mtime, terminal) workflow's
    #    (removed) worktree path must be DENIED -- under the pre-fix bug,
    #    this was exactly the target that got incorrectly ALLOWED, because
    #    the terminal workflow's worktree_path won the mtime race.
    payload_stale = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((stale_worktree / "notes.md").resolve())},
    }
    _, out_stale = run_hook("craftflow_pretooluse_guard.py", payload_stale, env)
    if '"permissionDecision": "deny"' not in out_stale and '"permissionDecision":"deny"' not in out_stale:
        fail(
            name,
            "expected DENY for a write inside the stale/terminal workflow's "
            f"(newest-mtime) worktree_path -- this is the exact fail-open "
            f"regression signature; got: {out_stale!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_latest_workflow_ignores_missing_worktree_directory_even_with_newest_mtime(tmp_dir: Path) -> None:
    # Variant (data-shape: staleness signaled by a REMOVED worktree
    # directory, independent of worktree_mode ever being updated -- e.g. a
    # crashed/interrupted merge that removed the directory but never got to
    # write worktree_mode: "merged_and_removed"). Must be caught by the
    # SAME fail-safe filter via the filesystem-truth check, not just the
    # worktree_mode string.
    name = "pretooluse-guard/latest-workflow-ignores-missing-worktree-directory-even-with-newest-mtime"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    live_worktree = tmp_dir / "live-worktree-2"
    live_worktree.mkdir(parents=True)
    # Directory genuinely does not exist -- no worktree_mode set at all
    # (simulates an interrupted merge, not a clean router-flagged one).
    vanished_worktree = tmp_dir / "vanished-worktree"

    old_path = _write_workflow_json_fixture_full(
        project_root, "wf-old-live-2", str(live_worktree.resolve())
    )
    new_path = _write_workflow_json_fixture_full(
        project_root, "wf-new-vanished", str(vanished_worktree)
    )
    now = time.time()
    os.utime(old_path, (now - 3600, now - 3600))
    os.utime(new_path, (now, now))

    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}

    payload_live = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((live_worktree / "scratch.md").resolve())},
    }
    _, out_live = run_hook("craftflow_pretooluse_guard.py", payload_live, env)
    if out_live:
        fail(name, f"expected allow for a write inside the still-live workflow's worktree; got: {out_live!r}")
        return

    payload_vanished = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((vanished_worktree / "notes.md").resolve())},
    }
    _, out_vanished = run_hook("craftflow_pretooluse_guard.py", payload_vanished, env)
    if '"permissionDecision": "deny"' not in out_vanished and '"permissionDecision":"deny"' not in out_vanished:
        fail(
            name,
            "expected DENY for a write inside a workflow whose worktree_path "
            f"directory no longer exists on disk, even with the newest mtime; got: {out_vanished!r}",
        )
        return
    ok(name)


def test_hooklib_latest_workflow_file_prefers_session_id_match_when_present(tmp_dir: Path) -> None:
    # Forward-compat proof: once a workflow artifact DOES carry a
    # session_id (a future router-side follow-up, out of scope for this
    # fix), latest_live_workflow_file() must prefer the matching-session
    # live candidate over a different, more-recently-touched live one --
    # proves tier 1 of the two-tier selection actually works, even though
    # no in-repo writer populates session_id yet.
    #
    # Finding 1 (REM-FIX cycle 1): session_id-scoped selection is a
    # write-confinement-sensitive concern (Item A), so it lives on
    # latest_live_workflow_file() now -- the plain latest_workflow_file()
    # reverted to its original zero-arg, unfiltered-newest-by-mtime
    # signature (see craftflow_hooklib.py's "Latest workflow selection"
    # module docstring).
    name = "hooklib/latest-workflow-file-prefers-session-id-match-when-present"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    other_path = _write_workflow_json_fixture_full(
        project_root, "wf-other-session", None, session_id="session-B"
    )
    mine_path = _write_workflow_json_fixture_full(
        project_root, "wf-my-session", None, session_id="session-A"
    )
    now = time.time()
    os.utime(mine_path, (now - 3600, now - 3600))  # older, but session-matched
    os.utime(other_path, (now, now))  # newest mtime, different session

    old_env = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = str(project_root)
    try:
        selected = hooklib.latest_live_workflow_file(session_id="session-A")
    finally:
        if old_env is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = old_env

    if selected is None or selected.name != "wf-my-session.json":
        fail(name, f"expected the session-A-matched (older) workflow to be selected; got: {selected!r}")
        return
    ok(name)


def test_hooklib_latest_live_workflow_file_finds_session_match_outside_scan_window(tmp_dir: Path) -> None:
    # Doubt-verify cycle 1, item 2 ("scan-window fallback gap", REM-FIX
    # cycle 2, genuine new-code bug): the old `latest_live_workflow_file()`
    # stopped scanning the instant the bounded window
    # (`_LATEST_WORKFLOW_SCAN_WINDOW` = 50) produced ANY live candidate --
    # even when `session_id` was given and none of those in-window live
    # candidates matched it. A true session-matching live workflow sitting
    # just outside the window (this repo's own high-churn scenario: many
    # newer, unrelated, live workflows from concurrent work push a
    # long-idle session's own workflow file below the window) was silently
    # never found; the function fell through to an unrelated in-window
    # live candidate instead of widening the scan. Reproduces the exact
    # counter-example: an unrelated live workflow occupies every window
    # slot (position 1..50), while the true session-matching live workflow
    # sits older, at position 51+.
    name = "hooklib/latest-live-workflow-file-finds-session-match-outside-scan-window"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)

    now = time.time()
    target_session = "sess-fix1-outside-window"
    target_path = _write_workflow_json_fixture_full(
        project_root, "wf-true-target", None, session_id=target_session
    )
    os.utime(target_path, (now - 7200, now - 7200))  # oldest -- pushed outside window

    for i in range(hooklib._LATEST_WORKFLOW_SCAN_WINDOW):
        filler_path = _write_workflow_json_fixture_full(
            project_root, f"wf-filler-{i}", None, session_id="sess-unrelated"
        )
        # All newer than target_path, so all _LATEST_WORKFLOW_SCAN_WINDOW
        # of them sort ahead of it -- target_path is guaranteed outside
        # the bounded window.
        os.utime(filler_path, (now - 3600 + i, now - 3600 + i))

    old_env = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = str(project_root)
    try:
        selected = hooklib.latest_live_workflow_file(session_id=target_session)
    finally:
        if old_env is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = old_env

    if selected is None or selected.name != "wf-true-target.json":
        fail(
            name,
            "expected the session-matching workflow outside the scan window to "
            f"be found (widened scan), not an unrelated in-window live "
            f"candidate; got: {selected!r}",
        )
        return
    ok(name)


def test_hooklib_latest_live_workflow_file_stays_bounded_when_window_lacks_session_id_key(tmp_dir: Path) -> None:
    # Regression (code-reviewer CHANGES_REQUESTED, confidence 92, REM-FIX
    # cycle 3, CRITICAL): REM-FIX cycle 2's scan-window-widen fix (see the
    # test above) over-corrected. It widens past the bounded window
    # whenever no in-window candidate's `session_id` field MATCHES the
    # given session_id -- which is unconditionally true in production,
    # because 0/222 real workflow artifacts under
    # .craftflow/state/workflows/ carry a `session_id` key at all
    # (confirmed against this repo's own corpus, 2026-08-18; nothing in
    # the plugin's non-test code writes this field into a workflow
    # artifact yet). Every Claude Code hook payload carries a real
    # session_id, so the session_id-given branch always runs -- meaning
    # the widen always fired, turning every PreToolUse invocation into an
    # unconditional full-directory scan and defeating the entire point of
    # the bounded window.
    #
    # The fix: only widen when NONE of the in-window live candidates carry
    # a `session_id` key AT ALL (the producer side never populated it for
    # anything in the window) -- not merely when none of them happen to
    # MATCH the given session_id. This test builds a realistic-sized
    # corpus (matches this repo's own 222-file corpus) where every
    # candidate omits `session_id` entirely, and proves the widen never
    # fires: the cumulative count of candidate paths ever handed to
    # `_live_workflow_candidates()` must stay within the bounded window,
    # never reaching into the remainder.
    name = "hooklib/latest-live-workflow-file-stays-bounded-when-window-lacks-session-id-key"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)

    now = time.time()
    total_files = 222  # mirrors this repo's own real workflows/ corpus size
    for i in range(total_files):
        # No session_id kwarg passed at all -- matches production reality
        # (0/222 real workflow artifacts carry the key).
        p = _write_workflow_json_fixture_full(project_root, f"wf-real-{i}", None)
        os.utime(p, (now - i, now - i))

    scanned_path_counts: list[int] = []
    original_live_candidates = hooklib._live_workflow_candidates

    def _counting_live_candidates(paths):
        paths_list = list(paths)
        scanned_path_counts.append(len(paths_list))
        return original_live_candidates(paths_list)

    old_env = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = str(project_root)
    hooklib._live_workflow_candidates = _counting_live_candidates
    try:
        selected = hooklib.latest_live_workflow_file(session_id="sess-real-invocation")
    finally:
        hooklib._live_workflow_candidates = original_live_candidates
        if old_env is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = old_env

    total_scanned = sum(scanned_path_counts)
    if total_scanned > hooklib._LATEST_WORKFLOW_SCAN_WINDOW:
        fail(
            name,
            "expected the bounded-window path (<= "
            f"{hooklib._LATEST_WORKFLOW_SCAN_WINDOW} candidates scanned) when "
            "no in-window candidate carries a session_id key at all; got "
            f"{total_scanned} candidates scanned across calls "
            f"{scanned_path_counts!r} -- the widen fired unconditionally",
        )
        return
    if selected is None or selected.name != "wf-real-0.json":
        fail(name, f"expected the newest live candidate (wf-real-0) to be selected; got: {selected!r}")
        return
    ok(name)


def test_precompact_state_snapshot_uses_newest_by_mtime_even_when_terminal_workflow(tmp_dir: Path) -> None:
    # Finding 1 (code-reviewer CHANGES_REQUESTED, REM-FIX cycle 1, CRITICAL):
    # the Item A liveness filter (_workflow_payload_is_live()) had been
    # applied UNCONDITIONALLY inside latest_workflow_file()/
    # latest_workflow_payload()/read_latest_workflow_state() -- silently
    # changing behavior for every consumer of those shared functions, not
    # just the 4 write-confinement-sensitive call sites in
    # craftflow_pretooluse_guard.py/craftflow_pretooluse_bash_guard.py that
    # actually need fail-closed liveness semantics.
    #
    # craftflow_precompact_state.py is a non-security consumer: it calls
    # `read_latest_workflow_state()` (no session_id, no liveness filtering)
    # to snapshot "whatever workflow is currently active" before a context
    # compaction. It must keep the ORIGINAL "true newest by mtime,
    # including terminal workflows" semantic that craftflow_status_report.py's
    # own comment documents ("# Default: precompact snapshot -> newest by
    # mtime") -- a terminal (merged_and_removed) workflow whose JSON file
    # happens to be the most-recently-touched one on disk must still win
    # here, exactly as it did before the Item A liveness filter existed.
    name = "precompact-state/snapshot-uses-newest-by-mtime-even-when-terminal-workflow"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)

    old_path = _write_workflow_json_fixture_full(
        project_root, "wf-old-live", None, worktree_mode="auto_created"
    )
    new_path = _write_workflow_json_fixture_full(
        project_root, "wf-new-terminal", None, worktree_mode="merged_and_removed"
    )
    now = time.time()
    os.utime(old_path, (now - 3600, now - 3600))  # older, live
    os.utime(new_path, (now, now))  # newest mtime, but terminal

    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"trigger": "auto"}
    exit_code, out = run_hook("craftflow_precompact_state.py", payload, env)
    if exit_code != 0:
        fail(name, f"expected craftflow_precompact_state.py to exit 0; got {exit_code}, out={out!r}")
        return

    snapshot_path = project_root / ".craftflow" / "state" / "precompact-state.json"
    if not snapshot_path.exists():
        fail(name, "expected precompact-state.json snapshot to be written")
        return
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if snapshot.get("workflow_uuid") != "wf-new-terminal":
        fail(
            name,
            "expected the snapshot to reflect the TRUE newest-by-mtime workflow "
            "even though it is terminal (merged_and_removed) -- a non-security "
            f"consumer must NOT be liveness-filtered; got: {snapshot.get('workflow_uuid')!r}",
        )
        return
    ok(name)


# ---------------------------------------------------------------------------
# Item B fix: consecutive-denial hard stop
# ---------------------------------------------------------------------------

def _denial_log_lines(project_root: Path) -> list:
    log_path = project_root / ".craftflow" / "state" / "craftflow-hook-events.log"
    if not log_path.exists():
        return []
    return log_path.read_text(encoding="utf-8").strip().splitlines()


def test_pretooluse_guard_edit_write_single_denial_is_not_escalated(tmp_dir: Path) -> None:
    # Regression proof part 1 (Item B requirement 1): a SINGLE denial must
    # NOT hard-stop -- ordinary deny wording/decision only.
    name = "pretooluse-guard/edit-write-single-denial-is-not-escalated"
    project_root = tmp_dir / "project"
    worktree = tmp_dir / "worktree"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    worktree.mkdir(parents=True)
    outside.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, str(worktree.resolve()))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "session_id": "sess-itemb-1",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((outside / "notes.md").resolve())},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny on the first attempt; got: {out!r}")
        return
    if "ESCALATED" in out:
        fail(name, f"a single denial must not be escalated; got: {out!r}")
        return
    log_lines = _denial_log_lines(project_root)
    if any('"decision": "deny-escalated"' in line for line in log_lines):
        fail(name, f"expected no deny-escalated log entry after only 1 denial; got: {log_lines!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_second_consecutive_denial_is_escalated(tmp_dir: Path) -> None:
    # Regression proof part 2 (Item B requirement 2): a SECOND consecutive
    # denial on the SAME (session, resolved target) logical write action
    # escalates -- distinct wording ("ESCALATED"/"STOP and ask the user")
    # and a distinct log decision ("deny-escalated"), reproducing the
    # structural backstop for the motivating incident (2 Edit denials on
    # the same target, then a Bash-surface bypass attempt with nothing
    # pausing for human input in between).
    name = "pretooluse-guard/edit-write-second-consecutive-denial-is-escalated"
    project_root = tmp_dir / "project"
    worktree = tmp_dir / "worktree"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    worktree.mkdir(parents=True)
    outside.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, str(worktree.resolve()))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "session_id": "sess-itemb-2",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((outside / "notes.md").resolve())},
    }
    run_hook("craftflow_pretooluse_guard.py", payload, env)  # 1st denial
    _, out2 = run_hook("craftflow_pretooluse_guard.py", payload, env)  # 2nd denial, same target
    if '"permissionDecision": "deny"' not in out2 and '"permissionDecision":"deny"' not in out2:
        fail(name, f"expected deny on the 2nd attempt too; got: {out2!r}")
        return
    if "ESCALATED" not in out2 or "STOP" not in out2:
        fail(name, f"expected the 2nd consecutive denial to be escalated; got: {out2!r}")
        return
    log_lines = _denial_log_lines(project_root)
    if not any('"decision": "deny-escalated"' in line for line in log_lines):
        fail(name, f"expected a deny-escalated log entry after the 2nd denial; got: {log_lines!r}")
        return
    ok(name)


def test_pretooluse_guard_bash_second_consecutive_denial_is_escalated(tmp_dir: Path) -> None:
    # Variant (trigger-surface dimension): the SAME escalation mechanism
    # via the Bash-write-detection lane in _handle_bash, not just
    # Edit/Write -- proves the hard stop is not hardcoded to one tool
    # surface. Two consecutive Bash redirects into the SAME protected
    # memory file (memoryWrites/protectedWrites unconditional lane is not
    # used here -- worktree-confinement is unconditional and simplest to
    # trigger deterministically).
    name = "pretooluse-guard/bash-second-consecutive-denial-is-escalated"
    project_root = tmp_dir / "project"
    elsewhere = tmp_dir / "elsewhere"
    project_root.mkdir(parents=True)
    elsewhere.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    target = project_root / ".craftflow" / "state" / "activeContext.md"
    payload = {
        "tool_name": "Bash",
        "session_id": "sess-itemb-3",
        "cwd": str(elsewhere.resolve()),
        "tool_input": {"command": f"echo hi > {target.resolve()}"},
    }
    _, out1 = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if "ESCALATED" in out1:
        fail(name, f"expected the 1st Bash denial to NOT be escalated; got: {out1!r}")
        return
    _, out2 = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out2 and '"permissionDecision":"deny"' not in out2:
        fail(name, f"expected deny on the 2nd Bash attempt too; got: {out2!r}")
        return
    if "ESCALATED" not in out2 or "STOP" not in out2:
        fail(name, f"expected the 2nd consecutive Bash denial to be escalated; got: {out2!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_denial_counter_resets_on_different_target(tmp_dir: Path) -> None:
    # Reset condition (Item B requirement 3, "a new logical action"): an
    # escalated target must NOT contaminate a DIFFERENT target's count --
    # each resolved path is its own independent logical action.
    name = "pretooluse-guard/edit-write-denial-counter-resets-on-different-target"
    project_root = tmp_dir / "project"
    worktree = tmp_dir / "worktree"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    worktree.mkdir(parents=True)
    outside.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, str(worktree.resolve()))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    session_id = "sess-itemb-4"

    target_a = {
        "tool_name": "Write",
        "session_id": session_id,
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((outside / "a.md").resolve())},
    }
    run_hook("craftflow_pretooluse_guard.py", target_a, env)
    _, out_a2 = run_hook("craftflow_pretooluse_guard.py", target_a, env)
    if "ESCALATED" not in out_a2:
        fail(name, f"expected target A's 2nd denial to be escalated (sanity check); got: {out_a2!r}")
        return

    target_b = {
        "tool_name": "Write",
        "session_id": session_id,
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((outside / "b.md").resolve())},
    }
    _, out_b1 = run_hook("craftflow_pretooluse_guard.py", target_b, env)
    if "ESCALATED" in out_b1:
        fail(name, f"expected target B's FIRST denial to NOT be escalated by target A's count; got: {out_b1!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_denial_counter_resets_after_allowed_write(tmp_dir: Path) -> None:
    # Reset condition (Item B requirement 3, "a successful equivalent
    # write"): once the guard ALLOWS a write to the same target (e.g. the
    # worktree_path is fixed / becomes correct), the next denial on that
    # target must start a FRESH count, not continue the old escalation.
    name = "pretooluse-guard/edit-write-denial-counter-resets-after-allowed-write"
    project_root = tmp_dir / "project"
    worktree = tmp_dir / "worktree"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    worktree.mkdir(parents=True)
    outside.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, str(worktree.resolve()))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    session_id = "sess-itemb-5"
    outside_target = outside / "notes.md"

    # Deny twice on an outside-of-confinement path (worktree-confinement
    # violation, escalates on the 2nd), then issue an ALLOWED write to
    # that EXACT SAME resolved path (by pointing cwd at `outside` itself,
    # so it now resolves inside cwd -- a clean "no violations" pass), then
    # deny it again from the original cwd and confirm it is back to an
    # ordinary (non-escalated) 1st-of-a-new-sequence denial.
    deny_payload = {
        "tool_name": "Write",
        "session_id": session_id,
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str(outside_target.resolve())},
    }
    run_hook("craftflow_pretooluse_guard.py", deny_payload, env)  # 1st deny
    _, out2 = run_hook("craftflow_pretooluse_guard.py", deny_payload, env)  # 2nd deny -- escalated
    if "ESCALATED" not in out2:
        fail(name, f"expected the 2nd denial to be escalated (sanity check); got: {out2!r}")
        return

    # An ALLOWED write to the exact same resolved path (now targeted
    # inside cwd, so it clears the "no violations" path) resets the count.
    allow_payload = {
        "tool_name": "Write",
        "session_id": session_id,
        "cwd": str(outside.resolve()),
        "tool_input": {"file_path": str(outside_target.resolve())},
    }
    _, out_allow = run_hook("craftflow_pretooluse_guard.py", allow_payload, env)
    if out_allow:
        fail(name, f"expected the corrected write (now inside its own cwd) to be allowed; got: {out_allow!r}")
        return

    _, out_after_reset = run_hook("craftflow_pretooluse_guard.py", deny_payload, env)
    if "ESCALATED" in out_after_reset:
        fail(
            name,
            "expected the FIRST denial after an intervening allow to NOT be "
            f"escalated (counter must reset); got: {out_after_reset!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_edit_write_denial_escalates_across_case_variant_paths(tmp_dir: Path) -> None:
    # Doubt-verify cycle 1, item 3 (case-insensitive `denial_action_key()`,
    # REM-FIX cycle 2): on the case-insensitive default macOS/APFS volume,
    # `str(Path(...).resolve())` preserves the literal case the caller
    # supplied rather than normalizing it -- confirmed empirically:
    # `Path("foo.md").resolve()` and `Path("FOO.md").resolve()` are
    # different Python strings that share the same underlying inode. A
    # retry that spells the SAME logical file with a different case (a
    # trivial, easy-to-hit variation -- not necessarily adversarial)
    # produced a DIFFERENT `denial_action_key()`, silently resetting the
    # consecutive-denial counter and defeating the Item B escalation this
    # fix exists to provide. Deny the same logical file twice via two
    # different-case spellings and confirm the 2nd is now escalated.
    name = "pretooluse-guard/edit-write-denial-escalates-across-case-variant-paths"
    project_root = tmp_dir / "project"
    worktree = tmp_dir / "worktree"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    worktree.mkdir(parents=True)
    outside.mkdir(parents=True)
    # File must actually exist on disk for the case-insensitive lookup to
    # share an inode across spellings (a non-existent path has no inode to
    # share, but `denial_action_key()` must normalize the STRING either
    # way, independent of on-disk existence).
    (outside / "notes.md").write_text("x", encoding="utf-8")
    _write_workflow_json_fixture(project_root, str(worktree.resolve()))
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    session_id = "sess-itemb-case"

    payload_lower = {
        "tool_name": "Write",
        "session_id": session_id,
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((outside / "notes.md").resolve())},
    }
    payload_upper = {
        "tool_name": "Write",
        "session_id": session_id,
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((outside / "NOTES.MD").resolve())},
    }

    _, out1 = run_hook("craftflow_pretooluse_guard.py", payload_lower, env)  # 1st deny
    if "ESCALATED" in out1:
        fail(name, f"expected the 1st denial (lowercase spelling) to NOT be escalated; got: {out1!r}")
        return

    _, out2 = run_hook("craftflow_pretooluse_guard.py", payload_upper, env)  # 2nd deny, different case
    if '"permissionDecision": "deny"' not in out2 and '"permissionDecision":"deny"' not in out2:
        fail(name, f"expected deny on the 2nd (case-variant) attempt too; got: {out2!r}")
        return
    if "ESCALATED" not in out2:
        fail(
            name,
            "expected the 2nd denial (different case spelling of the SAME "
            f"logical file) to be escalated; got: {out2!r}",
        )
        return
    ok(name)


def test_hooklib_record_denial_concurrent_calls_do_not_lose_updates(tmp_dir: Path) -> None:
    # Finding 3 (code-reviewer CHANGES_REQUESTED, REM-FIX cycle 1, MEDIUM):
    # `_load_denial_tracker()`/`_save_denial_tracker()` did a read-modify-
    # write on `.denial-tracker.json` with no coordination between the read
    # and the write. Two concurrent PreToolUse invocations denied on the
    # SAME (session_id, target) can both read the same stale count, both
    # increment locally, and both write back the same value -- silently
    # losing one denial and potentially missing the escalation signal.
    #
    # `_load_denial_tracker` is monkeypatched to sleep briefly AFTER its
    # real read completes -- this deterministically widens the read/write
    # window so every concurrent thread's read happens before any of them
    # writes, WITHOUT relying on GIL/scheduler luck. With `record_denial()`
    # properly locking the whole read-modify-write (the fix), that sleep
    # just makes the holder of the lock slower -- every other thread blocks
    # on lock acquisition and still observes the prior thread's write, so
    # the final count is exactly N. Without the lock, the sleep guarantees
    # every thread reads the same stale state and the final count collapses
    # to 1 (N-1 denials silently lost).
    import threading

    name = "hooklib/record-denial-concurrent-calls-do-not-lose-updates"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)

    old_project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    os.environ["CLAUDE_PROJECT_DIR"] = str(project_root)

    original_load = hooklib._load_denial_tracker

    def _slow_load():
        result = original_load()
        time.sleep(0.05)
        return result

    hooklib._load_denial_tracker = _slow_load

    concurrent_calls = 8
    errors: list = []

    def _worker() -> None:
        try:
            hooklib.record_denial("session-race", "/some/racy/target.md")
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(concurrent_calls)]
    tracker = {}
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        # Read the tracker BEFORE CLAUDE_PROJECT_DIR is restored below --
        # denial_tracker_path() (via state_root()/project_dir()) resolves
        # relative to the CURRENT env var, so reading after restore would
        # silently read the wrong (real) project's tracker file instead of
        # the tmp fixture's.
        hooklib._load_denial_tracker = original_load
        tracker = hooklib._load_denial_tracker()
    finally:
        hooklib._load_denial_tracker = original_load
        if old_project_dir is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = old_project_dir

    if errors:
        fail(name, f"unexpected exception(s) during concurrent record_denial() calls: {errors!r}")
        return

    key = hooklib.denial_action_key("session-race", "/some/racy/target.md")
    entry = tracker.get(key)
    count = entry.get("count") if isinstance(entry, dict) else None
    if count != concurrent_calls:
        fail(
            name,
            f"expected all {concurrent_calls} concurrent record_denial() calls to be "
            "counted without loss (TOCTOU race in the read-modify-write of "
            f".denial-tracker.json); got count={count!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_edit_write_allows_exact_allowlisted_workspace_root_file(tmp_dir: Path) -> None:
    name = "pretooluse-guard/edit-write-allows-exact-allowlisted-workspace-root-file"
    project_root = tmp_dir / "project"
    workspace_root = tmp_dir / "workspace"
    project_root.mkdir(parents=True)
    workspace_root.mkdir(parents=True)
    allowlisted = (workspace_root / "CONTRACTS.md").resolve()
    _write_workflow_json_fixture(project_root, None, workspace_writable_paths=[str(allowlisted)])
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str(allowlisted)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for the exact allowlisted workspace-root file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_denies_non_allowlisted_sibling_workspace_root_file(tmp_dir: Path) -> None:
    name = "pretooluse-guard/edit-write-denies-non-allowlisted-sibling-workspace-root-file"
    project_root = tmp_dir / "project"
    workspace_root = tmp_dir / "workspace"
    project_root.mkdir(parents=True)
    workspace_root.mkdir(parents=True)
    allowlisted = (workspace_root / "CONTRACTS.md").resolve()
    other_sibling = (workspace_root / "PLATFORM_CONTEXT.md").resolve()
    _write_workflow_json_fixture(project_root, None, workspace_writable_paths=[str(allowlisted)])
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str(other_sibling)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a non-allowlisted sibling file (exact-match-only allowlist); got: {out!r}")
        return
    if "worktree-confinement" not in out:
        fail(name, f"expected a 'worktree-confinement' deny reason; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_denies_descendant_of_allowlisted_entry_no_directory_grant(tmp_dir: Path) -> None:
    name = "pretooluse-guard/edit-write-denies-descendant-of-allowlisted-entry-no-directory-grant"
    project_root = tmp_dir / "project"
    workspace_root = tmp_dir / "workspace"
    project_root.mkdir(parents=True)
    allowlisted_dir = workspace_root / "CONTRACTS.md"
    allowlisted_dir.mkdir(parents=True)
    allowlisted_dir = allowlisted_dir.resolve()
    descendant = allowlisted_dir / "nested.txt"
    _write_workflow_json_fixture(project_root, None, workspace_writable_paths=[str(allowlisted_dir)])
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str(descendant)},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a descendant of an allowlisted entry (no directory grant); got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_edit_write_confinement_regression_unaffected_by_unrelated_workspace_writable_paths(tmp_dir: Path) -> None:
    # Re-verifies the pre-existing denies-outside-both scenario (g16) still denies when an
    # UNRELATED, non-matching workspace_writable_paths list is present in the fixture -- proves
    # the new field cannot accidentally widen confinement for anything not exactly listed.
    name = "pretooluse-guard/edit-write-confinement-regression-unaffected-by-unrelated-workspace-writable-paths"
    project_root = tmp_dir / "project"
    worktree = tmp_dir / "worktree-sibling"
    outside = tmp_dir / "outside"
    unrelated_allowlisted = tmp_dir / "workspace" / "CONTRACTS.md"
    project_root.mkdir(parents=True)
    worktree.mkdir(parents=True)
    outside.mkdir(parents=True)
    (tmp_dir / "workspace").mkdir(parents=True)
    _write_workflow_json_fixture(
        project_root, str(worktree.resolve()), workspace_writable_paths=[str(unrelated_allowlisted.resolve())]
    )
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((outside / "notes.md").resolve())},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny (regression g16 scenario) unaffected by an unrelated allowlist; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_bash_protected_path_write_still_denied_when_unrelated_workspace_writable_paths_present(tmp_dir: Path) -> None:
    # Regression for _handle_bash's confinement_violations lane (line 1206, the one call site
    # this plan wires): an unrelated workspace_writable_paths entry must not weaken the
    # pre-existing protected-memory-file denial.
    name = "pretooluse-guard/bash-protected-path-write-still-denied-when-unrelated-workspace-writable-paths-present"
    project_root = tmp_dir / "project"
    elsewhere = tmp_dir / "elsewhere"
    project_root.mkdir(parents=True)
    elsewhere.mkdir(parents=True)
    unrelated_allowlisted = tmp_dir / "workspace" / "CONTRACTS.md"
    (tmp_dir / "workspace").mkdir(parents=True)
    _write_workflow_json_fixture(
        project_root, None, workspace_writable_paths=[str(unrelated_allowlisted.resolve())]
    )
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    target = project_root / ".craftflow" / "state" / "activeContext.md"
    payload = {
        "tool_name": "Bash",
        "cwd": str(elsewhere.resolve()),
        "tool_input": {"command": f"echo hi > {target.resolve()}"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny (protected memory file) unaffected by an unrelated allowlist; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_bash_redirect_to_non_protected_workspace_root_file_unaffected_either_way(tmp_dir: Path) -> None:
    # Documents a REAL, verified scope boundary (not a bug): _handle_bash's redirect/python-write
    # confinement checks are gated to PROTECTED paths only (.craftflow/state/...) both before and
    # after this plan -- a workspace-root file like CONTRACTS.md is never a protected path, so an
    # ordinary Bash redirect to it was already unblocked before this fix, and stays unblocked
    # after (this test proves the wiring is a no-op for this case either way, not a regression).
    name = "pretooluse-guard/bash-redirect-to-non-protected-workspace-root-file-unaffected-either-way"
    project_root = tmp_dir / "project"
    workspace_root = tmp_dir / "workspace"
    project_root.mkdir(parents=True)
    workspace_root.mkdir(parents=True)
    target = (workspace_root / "CONTRACTS.md").resolve()
    _write_workflow_json_fixture(project_root, None, workspace_writable_paths=[])
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": f"echo hi > {target}"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"expected allow -- non-protected-path Bash redirects are out of this check's scope regardless of the allowlist; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_bash_confinement_lane_no_longer_flags_target_once_it_is_workspace_allowlisted(tmp_dir: Path) -> None:
    # Fresh-review-mandated proof (2026-08-13 revision) that _handle_bash's ONE genuinely
    # load-bearing internal resolve_confinement() call site -- the confinement_violations lane,
    # originally at line 1206, the only one of _handle_bash's 10 internal call sites that reads
    # the returned `confined` value -- is REALLY threaded with workspace_writable_paths, not
    # just present in the diff.
    #
    # This requires a target that is BOTH a member of _protected_bash_write_paths() (so the
    # confinement_violations lane's own `if resolved not in protected_paths: continue` gate
    # doesn't skip it) AND unconfined by plain {cwd, worktree_path} (so `_confined` would be
    # False without the allowlist). The cleanest such target is the fixture's OWN workflow-
    # artifact JSON file: it already matches `workflows_dir().glob("*.json")` (one of
    # `_protected_bash_write_paths()`'s two globs) with zero extra setup, and reusing it avoids
    # creating a second *.json file in workflows_dir() that could otherwise race
    # latest_workflow_file()'s mtime-based "latest" selection against the real fixture.
    #
    # Listing this file's own path inside its own `workspace_writable_paths` field is unusual
    # but legitimate for a GUARD-side wiring test: the guard layer already discloses (Assumption
    # Ledger) that it trusts `workspace_writable_paths` structurally without re-validating it,
    # exactly like `worktree_path` -- the real router-side resolver would never actually produce
    # this value (Behavior Contract rule 5c forbids an entry resolving inside a nested repo, and
    # this file always does), but that resolver-side validation is a SEPARATE concern from
    # whether the guard, given a trusted value, wires it through correctly.
    #
    # Without the line-1206 fix: this target is unconfined (cwd=elsewhere, worktree_path=None)
    # AND is a protected path -- confinement_violations fires unconditionally, so
    # "worktree-confinement" appears in the deny reason. With the fix: `_confined` becomes True
    # for this target via workspace_writable_paths, so confinement_violations does NOT fire --
    # but "bash-write-protected-path" (line 1152's SEPARATE, deliberately untouched lane, which
    # never consumed `confined` in the first place and is gated by the independent
    # `protectedWrites` toggle) still correctly denies it. This asymmetry -- one violation type
    # disappearing while an unrelated one persists -- is the actual proof the line-1206 wiring is
    # real, not a no-op deny either way.
    name = "pretooluse-guard/bash-confinement-lane-no-longer-flags-workspace-allowlisted-protected-path-target"
    project_root = tmp_dir / "project"
    elsewhere = tmp_dir / "elsewhere"
    project_root.mkdir(parents=True)
    elsewhere.mkdir(parents=True)
    wf_uuid = "wf-fixture-self-target"
    target = (project_root / ".craftflow" / "state" / "workflows" / f"{wf_uuid}.json").resolve()
    _write_workflow_json_fixture(project_root, None, wf_uuid=wf_uuid, workspace_writable_paths=[str(target)])
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(elsewhere.resolve()),
        "tool_input": {"command": f"echo hi > {target}"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if "worktree-confinement" in out:
        fail(name, f"expected the confinement_violations lane (line 1206) to be suppressed once the target is workspace-allowlisted; got: {out!r}")
        return
    if "bash-write-protected-path" not in out:
        fail(name, f"expected the SEPARATE, untouched protected-write lane (line 1152, never consumed `confined`) to still deny this protected-path target; got: {out!r}")
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


def test_pretooluse_guard_bash_null_byte_workspace_writable_paths_entry_denies_without_crash(tmp_dir: Path) -> None:
    # Bug B (REM-FIX, live-reproduced): a workspace_writable_paths entry containing a null byte
    # ("\x00") makes Path(entry).resolve() raise ValueError ("embedded null byte") -- not
    # OSError/RuntimeError, so hooklib.resolve_workspace_writable_paths()'s pre-fix except tuple
    # let it propagate uncaught out of _handle_bash (that call site sits near the top of the
    # function, outside any try/except). The malformed entry must be dropped, not crash the whole
    # guard process, and a write to a genuinely protected memory file must still be DENIED.
    name = "pretooluse-guard/bash-null-byte-workspace-writable-paths-entry-denies-without-crash"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, None, workspace_writable_paths=["bad\x00entry"])
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    target = project_root / ".craftflow" / "state" / "activeContext.md"
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": f"echo hi > {target.resolve()}"},
    }
    code, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if code != 0:
        fail(
            name,
            f"expected the guard process to exit 0 (graceful degrade, not a crash) even with a "
            f"null-byte workspace_writable_paths entry; got exit={code} stdout={out!r}",
        )
        return
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            f"expected deny for a protected-memory-file write even when workspace_writable_paths "
            f"contains a malformed null-byte entry; got: {out!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_edit_write_null_byte_workspace_writable_paths_entry_denies_target_outside_confinement(tmp_dir: Path) -> None:
    # Bug B (REM-FIX, live-reproduced): on the Edit/Write path, _handle_edit_write's existing
    # broad `except Exception` around _edit_write_escapes_confinement() previously swallowed the
    # uncaught ValueError from a null-byte workspace_writable_paths entry, treating it as "check
    # skipped" -- which meant the worktree-confinement check was SILENTLY SKIPPED and a target
    # outside cwd/worktree/allowlist was ALLOWED regardless of whether it should have been denied.
    # This is a full confinement bypass on a single malformed input, not just a crash.
    name = "pretooluse-guard/edit-write-null-byte-workspace-writable-paths-entry-denies-target-outside-confinement"
    project_root = tmp_dir / "project"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    outside.mkdir(parents=True)
    _write_workflow_json_fixture(project_root, None, workspace_writable_paths=["bad\x00entry"])
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str((outside / "notes.md").resolve())},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            f"expected deny for a target outside cwd/worktree/allowlist even when "
            f"workspace_writable_paths contains a malformed null-byte entry (not silently "
            f"allowed via exception-swallowing); got: {out!r}",
        )
        return
    if "worktree-confinement" not in out:
        fail(name, f"expected a 'worktree-confinement' deny reason; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_bash_non_dict_workflow_json_top_level_degrades_gracefully_still_denies(tmp_dir: Path) -> None:
    # Bug A (REM-FIX, live-reproduced): latest_workflow_payload() only guarantees valid JSON was
    # parsed -- NOT that the top level is a dict. If the workflow JSON file's content is e.g.
    # [1,2,3], workflow.get(...) raises AttributeError, uncaught (outside the try/except around
    # latest_workflow_payload() itself), crashing the whole _handle_bash function -- and the whole
    # guard process, since main() has no top-level try/except -- BEFORE any protection check runs.
    # Must degrade gracefully (worktree_path=None, workspace_writable_paths=frozenset()) and still
    # correctly deny a protected-memory-file write.
    name = "pretooluse-guard/bash-non-dict-workflow-json-top-level-degrades-gracefully-still-denies"
    project_root = tmp_dir / "project"
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "wf-non-dict-test.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    target = project_root / ".craftflow" / "state" / "activeContext.md"
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": f"echo hi > {target.resolve()}"},
    }
    code, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if code != 0:
        fail(
            name,
            f"expected the guard process to exit 0 (graceful degrade, not a crash) when the "
            f"workflow JSON top-level is a non-dict value; got exit={code} stdout={out!r}",
        )
        return
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            f"expected deny for a protected-memory-file write even when the active workflow "
            f"JSON top-level is a non-dict value; got: {out!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_edit_write_non_dict_workflow_json_top_level_degrades_gracefully_still_denies(tmp_dir: Path) -> None:
    # REM-FIX cycle 3 (live-reproduced CRITICAL): the identical Bug A pattern above
    # (latest_workflow_payload() only guarantees valid JSON was parsed -- NOT that the top level
    # is a dict) was ALSO present in the sibling function _handle_edit_write: workflow.get(...)
    # calls at "wf_uuid = workflow.get(...)" and "workflow.get('pending_gate')" sat OUTSIDE the
    # try/except around the latest_workflow_payload() fetch, so a non-dict top level (e.g.
    # [1,2,3]) raised an uncaught AttributeError, crashing _handle_edit_write -- and the whole
    # guard process, since main() has no top-level try/except -- BEFORE the deny for the
    # memory-write violation below it was ever emitted. Must degrade gracefully and still
    # correctly deny a protected-memory-file write.
    name = "pretooluse-guard/edit-write-non-dict-workflow-json-top-level-degrades-gracefully-still-denies"
    project_root = tmp_dir / "project"
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "wf-non-dict-test.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    target = project_root / ".craftflow" / "state" / "activeContext.md"
    payload = {
        "tool_name": "Write",
        "cwd": str(project_root.resolve()),
        "tool_input": {"file_path": str(target.resolve())},
    }
    code, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if code != 0:
        fail(
            name,
            f"expected the guard process to exit 0 (graceful degrade, not a crash) when the "
            f"workflow JSON top-level is a non-dict value; got exit={code} stdout={out!r}",
        )
        return
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            f"expected deny for a protected-memory-file write even when the active workflow "
            f"JSON top-level is a non-dict value; got: {out!r}",
        )
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

def test_pretooluse_guard_allows_bash_permit_write_absolute_spelling(tmp_dir: Path) -> None:
    # Root-cause regression (chicken-and-egg deadlock fix, 2026-08-18 DEBUG
    # workflow): matches_memory_finalize_permit_shape() previously required
    # the raw target token to literally string-equal a single hardcoded
    # bare-relative constant (".craftflow/state/.memory-finalize"), denying
    # every OTHER spelling that resolves to the exact same file -- including
    # an absolute-path spelling. The caller's OWN independent
    # `resolved == memory_finalize_permit_path().resolve()` check (immune to
    # spelling variance) is the real, non-spoofable security anchor; an
    # absolute-path spelling that resolves correctly must now be ALLOWED.
    name = "pretooluse-guard/allows-bash-permit-write-absolute-spelling"
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
    if out:
        fail(name, f"expected allow for a correctly-resolving absolute-path spelling of the permit target; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_allows_bash_permit_write_dot_slash_spelling(tmp_dir: Path) -> None:
    # Variant coverage: a "./"-prefixed relative spelling is a DIFFERENT
    # string from both the bare-relative literal and the absolute spelling
    # above, but resolves to the exact same file -- must also be permitted.
    name = "pretooluse-guard/allows-bash-permit-write-dot-slash-spelling"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "printf '%s' 'wf-test-1234' > ./.craftflow/state/.memory-finalize"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for a correctly-resolving './'-prefixed spelling of the permit target; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_permit_shape_targeting_different_file(tmp_dir: Path) -> None:
    # Negative control: the exact 5-token printf/%s/value/>/target shape,
    # but targeting a DIFFERENT file (not the real permit path). Must still
    # be denied by the caller's `resolved == permit_path` check -- proves
    # dropping the target-spelling literal from matches_memory_finalize_
    # permit_shape() did not turn it into a blanket allow for ANY target.
    name = "pretooluse-guard/denies-bash-permit-shape-targeting-different-file"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "printf '%s' 'wf-test-1234' > .craftflow/state/activeContext.md"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for the permit-write SHAPE targeting a different protected file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_permit_write_tee_shape(tmp_dir: Path) -> None:
    # Negative control: a different-shaped command (tee instead of a
    # printf/redirect) targeting the real permit path must still be denied
    # -- this stays a narrow allowlist, not a broadened one.
    name = "pretooluse-guard/denies-bash-permit-write-tee-shape"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "printf '%s' 'wf-test-1234' | tee .craftflow/state/.memory-finalize"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a tee-shaped write to the permit path; got: {out!r}")
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
    # HIGH 3: of the two structurally identical latest_live_workflow_payload()
    # calls in _handle_edit_write, the confinement-check call (inside
    # _edit_write_escapes_confinement) is wrapped in try/except by its
    # caller, but the SECOND call (populating wf_uuid for the deny/log
    # payload on the worktree-confinement deny path) had none.
    # latest_live_workflow_payload() can raise FileNotFoundError on a
    # stat-race (workflow JSON file deleted between glob() and .stat()),
    # crashing main() before the deny is ever emitted -- an uncaught crash
    # fails OPEN. (Finding 1, REM-FIX cycle 1: this is the write-
    # confinement-sensitive *_live_* variant now, not the plain
    # newest-by-mtime latest_workflow_payload().)
    name = "pretooluse-guard/handles-workflow-payload-race-in-wf-uuid-lookup"
    project_root = tmp_dir / "project"
    outside = tmp_dir / "outside"
    project_root.mkdir(parents=True)
    outside.mkdir(parents=True)

    call_count = {"n": 0}

    def _fake_latest_live_workflow_payload(session_id=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {}
        raise FileNotFoundError("workflow json vanished mid-stat")

    original = pretooluse_guard.latest_live_workflow_payload
    old_env = {k: os.environ.get(k) for k in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
    os.environ["CLAUDE_PROJECT_DIR"] = str(project_root)
    os.environ["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    pretooluse_guard.latest_live_workflow_payload = _fake_latest_live_workflow_payload
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
        pretooluse_guard.latest_live_workflow_payload = original
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


# ---------------------------------------------------------------------------
# REM-FIX (doubt-verify cycle 3): `mode.get("protectedWrites")` and
# `mode.get("memoryWrites")` had NO default argument at their call sites in
# craftflow_pretooluse_guard.py -- unlike bashDestructiveTraversal's call
# site in the sibling craftflow_pretooluse_bash_guard.py, which correctly
# does `mode.get("bashDestructiveTraversal", "block")`. A hook-mode.json
# that is valid JSON but simply OMITS one of these two keys (distinct from
# the missing-FILE / corrupt-FILE cases already covered above, which are
# caught entirely inside load_mode()'s own fallback dicts -- this bug lives
# at the CALL SITE, after load_mode() has already returned a real,
# successfully-parsed dict that just happens to lack one key) returned None
# from mode.get(), which resolve_toggle_decision() treats as "unrecognized"
# -- and since fail_closed_on_unrecognized=False for these two toggles, this
# silently failed OPEN. Live-verified: {"memoryWrites": "block"} with
# protectedWrites entirely absent allowed a heredoc write to a protected
# memory .md file via the Bash-write-inspection layer with zero deny --
# directly contradicting protectedWrites' own stated fail-closed intent.
# Fix: mirror bashDestructiveTraversal's pattern exactly --
# mode.get("protectedWrites", "block") (this toggle's whole purpose is a
# fail-closed protection) and mode.get("memoryWrites", "audit") (a
# long-established toggle whose correct default is "audit", matching
# load_mode()'s own fallback dict value -- NOT made fail-closed here).
# ---------------------------------------------------------------------------

def test_pretooluse_guard_bash_write_denied_when_protected_writes_key_missing(tmp_dir: Path) -> None:
    name = "pretooluse-guard/bash-write-denied-when-protected-writes-key-missing"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    # Valid JSON, but `protectedWrites` is entirely absent -- distinct from
    # the missing-FILE/corrupt-FILE cases above (those never even reach this
    # call site; load_mode() returns its own fail-closed fallback dict
    # first). Only `memoryWrites` is present, mirroring the exact
    # live-verified bug report shape.
    (fake_plugin_root / "config" / "hook-mode.json").write_text(
        json.dumps({"memoryWrites": "block"}), encoding="utf-8"
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
        fail(
            name,
            "expected fail-closed deny (protectedWrites must default to "
            f"'block' when the key is missing from an otherwise-valid config); got: {out!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_memory_write_allowed_when_memory_writes_key_missing(tmp_dir: Path) -> None:
    name = "pretooluse-guard/memory-write-allowed-when-memory-writes-key-missing"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    # Valid JSON, but `memoryWrites` is entirely absent -- only
    # `protectedWrites` is present. `memoryWrites`' own pre-existing,
    # long-established default is "audit" (matching load_mode()'s own
    # fallback dict value) -- a missing key must NOT flip this toggle to
    # fail-closed; that would be a behavior change beyond this fix's scope.
    (fake_plugin_root / "config" / "hook-mode.json").write_text(
        json.dumps({"protectedWrites": "block"}), encoding="utf-8"
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
            "expected allow (memoryWrites must keep its own pre-existing "
            f"'audit' default when the key is missing, not fail closed); got: {out!r}",
        )
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX round 5: `cp`/`mv`/`ln`/`install`/`rsync`/`dd` destination-argument
# bypass -- these ordinary shell file-copy/move/link commands have ZERO
# `>`/`>>`/`tee` redirect syntax, so `_bash_write_targets_in_tokens()` (and
# the python-write detectors alongside it) never saw them at all. Live-
# reproduced bypass (all 3 silently ALLOWED, zero log_event call, before this
# fix; see docstrings on `_cp_mv_like_write_targets()`/`_dd_write_targets()`
# in craftflow_pretooluse_guard.py).
# ---------------------------------------------------------------------------

def test_pretooluse_guard_denies_bash_cp_write_to_skill_proposal_file(tmp_dir: Path) -> None:
    # Exact live-reproduced bypass command #1 from the REM-FIX report.
    name = "pretooluse-guard/denies-bash-cp-write-to-skill-proposal-file"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "foo")
    proposal_target = ".craftflow/state/project/skill-proposals/cand0001/SKILL.md"
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir.resolve()),
        "tool_input": {"command": f"cp /tmp/evil.md {proposal_target}"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a plain 'cp' write into the skill-proposals tree; got: {out!r}")
        return
    if "skill-ledger-write" not in out:
        fail(name, f"expected the deny reason to name 'skill-ledger-write'; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_cp_write_to_skill_ledger(tmp_dir: Path) -> None:
    # Exact live-reproduced bypass command #2 from the REM-FIX report.
    name = "pretooluse-guard/denies-bash-cp-write-to-skill-ledger"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir.resolve()),
        "tool_input": {"command": "cp /tmp/forged.json .craftflow/state/project/skill-candidates.json"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a plain 'cp' write overwriting the skill-candidate ledger; got: {out!r}")
        return
    if "skill-ledger-write" not in out:
        fail(name, f"expected the deny reason to name 'skill-ledger-write'; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_mv_write_to_skill_proposal_file(tmp_dir: Path) -> None:
    # Exact live-reproduced bypass command #3 from the REM-FIX report.
    name = "pretooluse-guard/denies-bash-mv-write-to-skill-proposal-file"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "foo")
    proposal_target = ".craftflow/state/project/skill-proposals/cand0001/SKILL.md"
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir.resolve()),
        "tool_input": {"command": f"mv /tmp/staged.md {proposal_target}"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a plain 'mv' write into the skill-proposals tree; got: {out!r}")
        return
    if "skill-ledger-write" not in out:
        fail(name, f"expected the deny reason to name 'skill-ledger-write'; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_ln_write_to_memory_md(tmp_dir: Path) -> None:
    # Same systemic gap, memory-file protected-path class, via `ln` (never a
    # redirect/tee shape either) -- while staying INSIDE cwd, the shape this
    # workflow's existing `mv`-escaping-cwd tests never cover.
    name = "pretooluse-guard/denies-bash-ln-write-to-memory-md"
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "ln -f /tmp/evil.md .craftflow/state/project/activeContext.md"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a plain 'ln' write into a memory .md file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_mv_write_to_inflight_skill_promotion_path(tmp_dir: Path) -> None:
    # Third protected-path class (skill-promotion-path), via `mv`.
    name = "pretooluse-guard/denies-bash-mv-write-to-inflight-skill-promotion-path"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "newskill")
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir.resolve()),
        "tool_input": {"command": "mv /tmp/staged.md .claude/skills/newskill/SKILL.md"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a plain 'mv' write into an in-flight skill's SKILL.md; got: {out!r}")
        return
    if "skill-promotion-path" not in out:
        fail(name, f"expected the deny reason to name 'skill-promotion-path'; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_bash_cp_target_directory_form_write_to_skill_proposals(tmp_dir: Path) -> None:
    # `--target-directory=`/`-t` form (mv/cp's disguised destination flag) --
    # must resolve to <dir>/<basename(source)>, not just the last bare token.
    name = "pretooluse-guard/denies-bash-cp-target-directory-form-write-to-skill-proposals"
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    _stage_inflight_skill_candidate(tmp_dir, "foo")
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir.resolve()),
        "tool_input": {
            "command": "cp --target-directory=.craftflow/state/project/skill-proposals/cand0001 /tmp/SKILL.md"
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a 'cp --target-directory=' write into the skill-proposals tree; got: {out!r}")
        return
    if "skill-ledger-write" not in out:
        fail(name, f"expected the deny reason to name 'skill-ledger-write'; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_allows_bash_cp_unrelated_elsewhere(tmp_dir: Path) -> None:
    # Regression guard against over-blocking (mirrors this workflow's
    # established care around this failure mode from round 2): an ordinary
    # `cp` writing somewhere that is NOT any protected-path class, staying
    # inside cwd, must remain allowed.
    name = "pretooluse-guard/allows-bash-cp-unrelated-elsewhere"
    project_root = tmp_dir / "project"
    (project_root / "src").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "cp src/foo.ts src/bar.ts"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for an ordinary unrelated 'cp' write; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_allows_bash_mv_unrelated_elsewhere(tmp_dir: Path) -> None:
    name = "pretooluse-guard/allows-bash-mv-unrelated-elsewhere"
    project_root = tmp_dir / "project"
    (project_root / "src").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "mv src/foo.ts src/renamed.ts"},
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for an ordinary unrelated 'mv' write; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX round 5 (MEDIUM): non-UTF-8 draft/proposal content must degrade to
# this script's own documented `{"error": ...}` JSON shape, not a raw
# `UnicodeDecodeError` traceback (`UnicodeDecodeError` is a `ValueError`
# subclass, not caught by a bare `except OSError`).
# ---------------------------------------------------------------------------

def test_skill_propose_non_utf8_skill_md_file_returns_json_error(tmp_dir: Path) -> None:
    name = "skill-propose/non-utf8-skill-md-file-returns-json-error"
    state_dir = tmp_dir / ".craftflow" / "state"
    ledger_path = state_dir / "project" / "skill-candidates.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps({
            "schema_version": 1,
            "candidates": [{
                "id": "cand0001",
                "surface": "s",
                "signature": "sig",
                "status": "candidate",
            }],
        }),
        encoding="utf-8",
    )
    bad_skill_md = tmp_dir / "draft-SKILL.md"
    # Invalid UTF-8 byte sequence (a lone continuation byte).
    bad_skill_md.write_bytes(b"---\nname: foo\n---\n\xff\xfe invalid utf-8 body\n")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "craftflow_skill_propose.py"),
            "--candidate-id", "cand0001",
            "--skill-md-file", str(bad_skill_md),
            "--state-dir", str(state_dir),
            "--project-root", str(tmp_dir),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        fail(name, f"expected non-zero exit for non-UTF-8 draft content; got exit 0, stdout: {result.stdout!r}")
        return
    if "Traceback" in result.stderr:
        fail(name, f"expected a clean JSON error, not a raw traceback; stderr: {result.stderr!r}")
        return
    try:
        parsed = json.loads(result.stderr.strip())
    except ValueError:
        fail(name, f"expected stderr to be valid JSON; got: {result.stderr!r}")
        return
    if "error" not in parsed:
        fail(name, f"expected an 'error' key in the JSON output; got: {parsed!r}")
        return
    ok(name)


def test_skill_promote_non_utf8_skill_md_returns_json_error(tmp_dir: Path) -> None:
    name = "skill-promote/non-utf8-skill-md-returns-json-error"
    proposals_dir = tmp_dir / "proposals"
    proposal_dir = proposals_dir / "cand0001"
    proposal_dir.mkdir(parents=True)
    bad_skill_md = proposal_dir / "SKILL.md"
    bad_skill_md.write_bytes(b"---\nname: foo\n---\n\xff\xfe invalid utf-8 body\n")
    ledger_path = tmp_dir / "skill-candidates.json"
    ledger_path.write_text(
        json.dumps({
            "schema_version": 1,
            "candidates": [{
                "id": "cand0001",
                "surface": "s",
                "signature": "sig",
                "status": "proposed",
            }],
        }),
        encoding="utf-8",
    )
    project_root = tmp_dir / "project"
    project_root.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "craftflow_skill_promote.py"),
            "--approve", "cand0001",
            "--proposals-dir", str(proposals_dir),
            "--project-root", str(project_root),
            "--ledger", str(ledger_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        fail(name, f"expected non-zero exit for non-UTF-8 proposal content; got exit 0, stdout: {result.stdout!r}")
        return
    if "Traceback" in result.stderr:
        fail(name, f"expected a clean JSON error, not a raw traceback; stderr: {result.stderr!r}")
        return
    try:
        parsed = json.loads(result.stderr.strip())
    except ValueError:
        fail(name, f"expected stderr to be valid JSON; got: {result.stderr!r}")
        return
    if "error" not in parsed:
        fail(name, f"expected an 'error' key in the JSON output; got: {parsed!r}")
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
    project_root: Path,
    worktree_path: str | None,
    wf_uuid: str = "wf-phase3-test",
    workspace_writable_paths: list | None = None,
) -> None:
    """Write a minimal workflow JSON artifact under project_root's own
    .craftflow/state/workflows/ -- hooklib.latest_workflow_payload() reads
    via CLAUDE_PROJECT_DIR (env), not the Bash payload's own "cwd" field, so
    the fixture must live at the project_root the test's env points at."""
    wf_dir = project_root / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    payload = {"workflow_uuid": wf_uuid, "worktree_path": worktree_path}
    if workspace_writable_paths is not None:
        payload["workspace_writable_paths"] = workspace_writable_paths
    (wf_dir / f"{wf_uuid}.json").write_text(json.dumps(payload), encoding="utf-8")


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


def test_bash_guard_allows_permit_write_absolute_spelling(tmp_dir: Path) -> None:
    # Root-cause regression (fix in BOTH files, 2026-08-18 DEBUG workflow):
    # matches_memory_finalize_permit_shape() previously required the raw
    # target token to literally string-equal a single hardcoded
    # bare-relative constant, denying every OTHER spelling that resolves to
    # the exact same file. The caller's OWN independent
    # `resolved == memory_finalize_permit_path().resolve()` check (immune
    # to spelling variance) is the real, non-spoofable security anchor; an
    # absolute-path spelling that resolves correctly must now be ALLOWED
    # here too.
    name = "pretooluse-bash-guard/allows-permit-write-absolute-spelling"
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
    if out:
        fail(name, f"expected allow for a correctly-resolving absolute-path spelling of the permit target; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_permit_write_dot_slash_spelling(tmp_dir: Path) -> None:
    # Variant coverage: a "./"-prefixed relative spelling is a DIFFERENT
    # string from both the bare-relative literal and the absolute spelling
    # above, but resolves to the exact same file -- must also be permitted.
    name = "pretooluse-bash-guard/allows-permit-write-dot-slash-spelling"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "printf '%s' 'wf-test-1234' > ./.craftflow/state/.memory-finalize"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for a correctly-resolving './'-prefixed spelling of the permit target; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_permit_write_multiline_command(tmp_dir: Path) -> None:
    # Root-cause regression (2026-08-19 DEBUG workflow), blast-radius twin of
    # pretooluse-guard/allows-bash-permit-write-multiline-command: this
    # guard's own redirect-confinement lane shares the SAME
    # hooklib.split_subcommands() tokenizer, so it live-reproduced the exact
    # same "worktree-confinement" deny (misleading reason label, but same
    # root cause -- protected-path-ness, not confinement) for a permit-write
    # sharing a Bash() call with a second newline-separated command.
    name = "pretooluse-bash-guard/allows-permit-write-multiline-command"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": "printf '%s' 'wf-test-1234' > .craftflow/state/.memory-finalize\necho done"
        },
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(
            name,
            f"expected allow for the documented permit-write shape sharing a Bash() call "
            f"with a second newline-separated command; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_denies_permit_shape_targeting_different_file(tmp_dir: Path) -> None:
    # Negative control: the exact 5-token printf/%s/value/>/target shape,
    # but targeting a DIFFERENT file (not the real permit path). Must still
    # be denied by the caller's `resolved == permit_path` check -- proves
    # dropping the target-spelling literal from matches_memory_finalize_
    # permit_shape() did not turn it into a blanket allow for ANY target.
    name = "pretooluse-bash-guard/denies-permit-shape-targeting-different-file"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "printf '%s' 'wf-test-1234' > .craftflow/state/activeContext.md"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for the permit-write SHAPE targeting a different protected file; got: {out!r}")
        return
    ok(name)


def test_pretooluse_guard_denies_permit_write_python_open_shape(tmp_dir: Path) -> None:
    # Negative control: a differently-shaped command (a python `open(...).
    # write(...)` one-liner, no printf/redirect at all) targeting the real
    # permit path must still be denied -- this stays a narrow allowlist,
    # not a broadened one. Python-script write detection is
    # craftflow_pretooluse_guard.py's own separate mechanism (Bash-write-
    # inspection layer), independent of matches_memory_finalize_permit_
    # shape() and unaffected by this fix.
    name = "pretooluse-guard/denies-permit-write-python-open-shape"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": "python3 -c \"open('.craftflow/state/.memory-finalize', 'w').write('wf-test-1234')\""
        },
    }
    _, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a python open().write() write to the permit path; got: {out!r}")
        return
    ok(name)


def test_bash_guard_permit_write_end_to_end_skill_md_literal_project_root_prefixed_spelling(
    tmp_dir: Path,
) -> None:
    # Confirmatory, NOT a claim that this fix changed this specific
    # behavior: the router's SKILL.md literally documents
    # `Bash("printf '%s' '{workflow_uuid}' > \"$PROJECT_ROOT/.craftflow/
    # state/.memory-finalize\"")`. PreToolUse hooks receive this command as
    # a RAW, unexecuted string -- "$PROJECT_ROOT" is never shell-expanded
    # before this guard parses it, because no real shell ever runs it here
    # (that only happens later, in the actual persistent Bash-tool shell).
    # resolve_confinement() has no `$VAR`-expansion step, so this exact
    # literal token does NOT resolve to the real permit path at this
    # guard's parsing layer, at all -- it is therefore never even
    # recognized as a protected-path write here, and is allowed through by
    # a DIFFERENT, unrelated code path (never reaching, and unaffected by,
    # matches_memory_finalize_permit_shape() or this fix). This is a
    # SEPARATE, independent gap in resolve_confinement()'s lack of
    # `$PROJECT_ROOT` expansion -- out of scope for this fix (see
    # MEMORY_NOTES.deferred / final report) -- captured here only to prove
    # the router's exact documented invocation text is not rejected
    # end-to-end, without overclaiming this fix is what makes it so.
    name = "pretooluse-bash-guard/permit-write-end-to-end-skill-md-literal-project-root-prefixed-spelling"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    (project_root / ".craftflow" / "state").mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {
            "command": 'printf \'%s\' \'wf-test-1234\' > "$PROJECT_ROOT/.craftflow/state/.memory-finalize"'
        },
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow (via the separate confinement-detection gap, not this fix) for the SKILL.md-literal $PROJECT_ROOT-prefixed command; got: {out!r}")
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


def test_bash_guard_allows_rm_targeting_claude_code_own_memory_dir(tmp_dir: Path) -> None:
    # Variant (Bash / destructive-command lane, craftflow_pretooluse_bash_guard.py):
    # the general (non-protected-path-scoped) resolve_confinement() call
    # inside the destructive-target loop must also treat Claude Code's own
    # auto-memory dir as confined -- otherwise `rm` on a stale auto-memory
    # file for this cwd is wrongly denied as "escapes-cwd".
    name = "pretooluse-bash-guard/allows-rm-targeting-claude-code-own-memory-dir"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    cwd = project_root.resolve()
    slug = "".join("-" if c in "/." else c for c in str(cwd))
    target = Path.home() / ".claude" / "projects" / slug / "memory" / "stale-note.md"
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd),
        "tool_input": {"command": f"rm {target}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for `rm` targeting Claude Code's own auto-memory dir for this cwd; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX (HIGH, build-craftflow-guardrail-harden, doubt-verify cycle 2): the
# `bashDestructiveTraversal` toggle governing this ENTIRE script's core
# destructive-command detection had the identical unvalidated
# `mode.get(...) == "block"` pattern already fixed for `memoryWrites`/
# `protectedWrites` in the sibling craftflow_pretooluse_guard.py. Live-
# verified pre-fix: {"bashDestructiveTraversal": "Block"} (capital-B typo)
# silently ALLOWED `rm -rf ../outside_target` instead of denying it.
#
# DESIGN DECISION (deliberate divergence from memoryWrites/protectedWrites):
# unlike those two toggles, `bashDestructiveTraversal`'s own MISSING-key
# behavior already fail-closes (`mode.get("bashDestructiveTraversal",
# "block")` -- default is "block", not "audit"). For consistency with this
# toggle's OWN established missing-key posture, an unrecognized-but-PRESENT
# value (a typo) fails the SAME way here -- fail CLOSED (deny), not
# audit-degrade -- via `resolve_toggle_decision(...,
# fail_closed_on_unrecognized=True)` in craftflow_hooklib.py. The log_event
# `decision` is "block-unrecognized-config-value", distinct from both the
# recognized "deny" and from memoryWrites/protectedWrites's
# "audit-unrecognized-config-value".
# ---------------------------------------------------------------------------

def test_bash_guard_unrecognized_bash_destructive_traversal_value_fails_closed_with_distinct_decision(
    tmp_dir: Path,
) -> None:
    name = "pretooluse-bash-guard/unrecognized-bash-destructive-traversal-value-fails-closed"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text(
        json.dumps({"bashDestructiveTraversal": "Block"}), encoding="utf-8"
    )
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "rm -rf ../outside_target"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected fail-CLOSED deny for a typo'd bashDestructiveTraversal "
            f"value (must mirror this toggle's own fail-closed missing-key "
            f"default, not audit-degrade like memoryWrites/protectedWrites); got: {out!r}",
        )
        return
    log_path = project_root / ".craftflow" / "state" / "craftflow-hook-events.log"
    if not log_path.exists():
        fail(name, f"expected log file {log_path} to exist after hook run")
        return
    log_lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    matching = [line for line in log_lines if "block-unrecognized-config-value" in line]
    if not matching:
        fail(
            name,
            "expected a log_event entry with decision 'block-unrecognized-config-value' "
            "for a typo'd bashDestructiveTraversal value, distinct from both an intentional "
            "'deny' and from the audit-degrade decision used by memoryWrites/protectedWrites",
        )
        return
    ok(name)


def test_bash_guard_missing_bash_destructive_traversal_key_still_fails_closed(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/missing-bash-destructive-traversal-key-still-fails-closed"
    fake_plugin_root = tmp_dir / "plugin_root"
    # Deliberately do NOT create hook-mode.json at all -- exercises
    # load_mode()'s missing-file fallback branch, whose fallback dict has no
    # `bashDestructiveTraversal` key at all, so `mode.get(...)` must fall
    # back to its own "block" default (regression check: this pre-existing
    # fail-closed behavior on a missing key must be unaffected by the fix).
    (fake_plugin_root / "config").mkdir(parents=True)
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "rm -rf ../outside_target"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected fail-closed deny when bashDestructiveTraversal key is "
            f"absent entirely (no regression in missing-key default); got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_explicit_block_value_still_denies(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/explicit-block-value-still-denies"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text(
        json.dumps({"bashDestructiveTraversal": "block"}), encoding="utf-8"
    )
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "rm -rf ../outside_target"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for an explicit 'block' bashDestructiveTraversal value; got: {out!r}")
        return
    log_path = project_root / ".craftflow" / "state" / "craftflow-hook-events.log"
    log_lines = log_path.read_text(encoding="utf-8").strip().splitlines() if log_path.exists() else []
    matching = [line for line in log_lines if '"decision": "deny"' in line]
    if not matching:
        fail(name, f"expected a log_event entry with plain 'deny' decision (recognized value); got lines: {log_lines!r}")
        return
    ok(name)


def test_bash_guard_explicit_audit_value_allows_but_logs_audit(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/explicit-audit-value-allows-but-logs-audit"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text(
        json.dumps({"bashDestructiveTraversal": "audit"}), encoding="utf-8"
    )
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "rm -rf ../outside_target"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        fail(name, f"expected allow (explicit 'audit' value must not deny); got: {out!r}")
        return
    log_path = project_root / ".craftflow" / "state" / "craftflow-hook-events.log"
    if not log_path.exists():
        fail(name, f"expected log file {log_path} to exist after hook run")
        return
    log_lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    matching = [line for line in log_lines if '"decision": "audit"' in line]
    if not matching:
        fail(name, f"expected a log_event entry with plain 'audit' decision (recognized value); got lines: {log_lines!r}")
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


def test_hooklib_resolve_confinement_allows_exact_match_in_extra_exact_paths(tmp_dir: Path) -> None:
    name = "hooklib/resolve-confinement-allows-exact-match-in-extra-exact-paths"
    cwd = (tmp_dir / "project")
    workspace_root = (tmp_dir / "workspace")
    cwd.mkdir(parents=True)
    workspace_root.mkdir(parents=True)
    cwd = cwd.resolve()
    allowlisted = (workspace_root / "CONTRACTS.md").resolve()
    confined, _resolved = hooklib.resolve_confinement(
        allowlisted, cwd, None, frozenset({allowlisted})
    )
    if not confined:
        fail(name, f"expected confined=True for an exact extra_exact_paths match; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_denies_descendant_of_extra_exact_path_not_exact_file(tmp_dir: Path) -> None:
    # Critical invariant: extra_exact_paths is EXACT-MATCH ONLY, never a directory grant. If the
    # allowlisted entry happens to also be a real directory on disk, a file one level below it
    # must still be denied -- this is what structurally keeps the mechanism from degrading into
    # Option B (multi-root confinement spanning full edits across a nested repo).
    name = "hooklib/resolve-confinement-denies-descendant-of-extra-exact-path-not-exact-file"
    cwd = (tmp_dir / "project")
    workspace_root = (tmp_dir / "workspace")
    cwd.mkdir(parents=True)
    workspace_root.mkdir(parents=True)
    cwd = cwd.resolve()
    allowlisted_dir = (workspace_root / "CONTRACTS.md")
    allowlisted_dir.mkdir(parents=True)
    allowlisted_dir = allowlisted_dir.resolve()
    descendant = allowlisted_dir / "nested.txt"
    confined, _resolved = hooklib.resolve_confinement(
        descendant, cwd, None, frozenset({allowlisted_dir})
    )
    if confined:
        fail(name, f"expected confined=False for a descendant of an extra_exact_paths entry (no directory grant); got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_denies_non_matching_sibling_when_extra_exact_paths_set(tmp_dir: Path) -> None:
    name = "hooklib/resolve-confinement-denies-non-matching-sibling-when-extra-exact-paths-set"
    cwd = (tmp_dir / "project")
    workspace_root = (tmp_dir / "workspace")
    cwd.mkdir(parents=True)
    workspace_root.mkdir(parents=True)
    cwd = cwd.resolve()
    allowlisted = (workspace_root / "CONTRACTS.md").resolve()
    other_sibling = (workspace_root / "PLATFORM_CONTEXT.md").resolve()
    confined, _resolved = hooklib.resolve_confinement(
        other_sibling, cwd, None, frozenset({allowlisted})
    )
    if confined:
        fail(name, f"expected confined=False for a non-allowlisted sibling file; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_extra_exact_paths_does_not_rescue_unlisted_target_outside_cwd_and_worktree(tmp_dir: Path) -> None:
    name = "hooklib/resolve-confinement-extra-exact-paths-does-not-rescue-unlisted-target"
    cwd = (tmp_dir / "project")
    worktree = (tmp_dir / "worktree-sibling")
    outside = (tmp_dir / "outside")
    cwd.mkdir(parents=True)
    worktree.mkdir(parents=True)
    outside.mkdir(parents=True)
    cwd = cwd.resolve()
    worktree = worktree.resolve()
    target = (outside / "file.txt").resolve()
    unrelated_allowlisted = (tmp_dir / "workspace" / "CONTRACTS.md")
    confined, _resolved = hooklib.resolve_confinement(
        target, cwd, str(worktree), frozenset({unrelated_allowlisted.resolve() if unrelated_allowlisted.parent.exists() else unrelated_allowlisted})
    )
    if confined:
        fail(name, f"expected confined=False when target is outside cwd/worktree and not itself in extra_exact_paths; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_byte_identical_when_extra_exact_paths_omitted_or_empty(tmp_dir: Path) -> None:
    # Regression proof (patterns.md mandate): the 4 pre-existing scenarios must produce
    # byte-identical (confined, resolved) tuples whether extra_exact_paths is omitted entirely
    # (3-arg call), explicitly None, or an empty frozenset.
    name = "hooklib/resolve-confinement-byte-identical-when-extra-exact-paths-omitted-or-empty"
    cwd = (tmp_dir / "project")
    worktree = (tmp_dir / "worktree-sibling")
    outside = (tmp_dir / "outside")
    cwd.mkdir(parents=True)
    worktree.mkdir(parents=True)
    outside.mkdir(parents=True)
    cwd = cwd.resolve()
    worktree = worktree.resolve()
    scenarios = [
        (cwd / "file.txt", cwd, None),
        ((outside / "file.txt"), cwd, None),
        ((worktree / "file.txt"), cwd, str(worktree)),
        ((outside / "file.txt"), cwd, str(worktree)),
    ]
    for target, scenario_cwd, scenario_worktree in scenarios:
        baseline = hooklib.resolve_confinement(target, scenario_cwd, scenario_worktree)
        with_none = hooklib.resolve_confinement(target, scenario_cwd, scenario_worktree, None)
        with_empty = hooklib.resolve_confinement(target, scenario_cwd, scenario_worktree, frozenset())
        if baseline != with_none or baseline != with_empty:
            fail(
                name,
                f"mismatch for target={target}: 3-arg={baseline} None={with_none} empty={with_empty}",
            )
            return
    ok(name)


def test_hooklib_resolve_confinement_allows_claude_code_own_memory_dir(tmp_dir: Path) -> None:
    # Root-cause regression (white-box): Claude Code's own global auto-memory
    # directory for THIS cwd (~/.claude/projects/<slug-of-cwd>/memory/*.md)
    # must be confined=True even though it sits outside cwd/worktree_path/
    # extra_exact_paths -- it is Claude Code's own infrastructure, not a
    # confinement escape.
    name = "hooklib/resolve-confinement-allows-claude-code-own-memory-dir"
    cwd = (tmp_dir / "cwd")
    cwd.mkdir(parents=True)
    cwd = cwd.resolve()
    slug = "".join("-" if c in "/." else c for c in str(cwd))
    target = Path.home() / ".claude" / "projects" / slug / "memory" / "note.md"
    confined, _resolved = hooklib.resolve_confinement(target, cwd, None)
    if not confined:
        fail(name, f"expected confined=True for Claude Code's own auto-memory dir under this cwd's slug; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_allows_claude_code_own_memory_md_exact(tmp_dir: Path) -> None:
    name = "hooklib/resolve-confinement-allows-claude-code-own-memory-md-exact"
    cwd = (tmp_dir / "cwd")
    cwd.mkdir(parents=True)
    cwd = cwd.resolve()
    slug = "".join("-" if c in "/." else c for c in str(cwd))
    target = Path.home() / ".claude" / "projects" / slug / "MEMORY.md"
    confined, _resolved = hooklib.resolve_confinement(target, cwd, None)
    if not confined:
        fail(name, f"expected confined=True for Claude Code's own MEMORY.md under this cwd's slug; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_denies_different_cwd_slug_memory_dir(tmp_dir: Path) -> None:
    # Anti-blanket-grant: a Claude Code auto-memory dir keyed to a DIFFERENT
    # cwd's slug must stay confined=False -- the allowance is derived only
    # from the caller's own trusted cwd, never a blanket ~/.claude/projects/**
    # grant across projects.
    name = "hooklib/resolve-confinement-denies-different-cwd-slug-memory-dir"
    cwd = (tmp_dir / "cwd")
    other_cwd = (tmp_dir / "other-cwd")
    cwd.mkdir(parents=True)
    other_cwd.mkdir(parents=True)
    cwd = cwd.resolve()
    other_slug = "".join("-" if c in "/." else c for c in str(other_cwd.resolve()))
    target = Path.home() / ".claude" / "projects" / other_slug / "memory" / "note.md"
    confined, _resolved = hooklib.resolve_confinement(target, cwd, None)
    if confined:
        fail(name, f"expected confined=False for a DIFFERENT cwd's Claude Code memory dir; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_allows_claude_code_own_session_scoped_memory_dir(tmp_dir: Path) -> None:
    # Root-cause regression (white-box, session-scoped shape): exactly one
    # extra directory segment (a session uuid) between the trusted cwd's
    # slug root and memory/ must still be confined=True -- the shape
    # observed live for a resumed-after-compaction session
    # (~/.claude/projects/<slug>/<session-uuid>/memory/<file>.md).
    name = "hooklib/resolve-confinement-allows-claude-code-own-session-scoped-memory-dir"
    cwd = (tmp_dir / "cwd")
    cwd.mkdir(parents=True)
    cwd = cwd.resolve()
    slug = "".join("-" if c in "/." else c for c in str(cwd))
    session_uuid = "17021386-8f91-4f13-8fa2-3fa3355ef61c"
    target = Path.home() / ".claude" / "projects" / slug / session_uuid / "memory" / "note.md"
    confined, _resolved = hooklib.resolve_confinement(target, cwd, None)
    if not confined:
        fail(name, f"expected confined=True for Claude Code's own session-scoped auto-memory dir under this cwd's slug; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_allows_claude_code_own_session_scoped_memory_md_exact(tmp_dir: Path) -> None:
    # Variant: the session-scoped top-level MEMORY.md file (not under
    # memory/) must also be allowed, mirroring the project-scoped exact-file
    # form.
    name = "hooklib/resolve-confinement-allows-claude-code-own-session-scoped-memory-md-exact"
    cwd = (tmp_dir / "cwd")
    cwd.mkdir(parents=True)
    cwd = cwd.resolve()
    slug = "".join("-" if c in "/." else c for c in str(cwd))
    session_uuid = "17021386-8f91-4f13-8fa2-3fa3355ef61c"
    target = Path.home() / ".claude" / "projects" / slug / session_uuid / "MEMORY.md"
    confined, _resolved = hooklib.resolve_confinement(target, cwd, None)
    if not confined:
        fail(name, f"expected confined=True for Claude Code's own session-scoped MEMORY.md under this cwd's slug; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_still_allows_claude_code_own_project_scoped_memory_dir(tmp_dir: Path) -> None:
    # Non-regression: widening the allowance to accept the session-scoped
    # shape must NOT weaken the pre-existing project-scoped
    # <slug>/memory/** grant added by commit 2c5cbd9.
    name = "hooklib/resolve-confinement-still-allows-claude-code-own-project-scoped-memory-dir"
    cwd = (tmp_dir / "cwd")
    cwd.mkdir(parents=True)
    cwd = cwd.resolve()
    slug = "".join("-" if c in "/." else c for c in str(cwd))
    target = Path.home() / ".claude" / "projects" / slug / "memory" / "note.md"
    confined, _resolved = hooklib.resolve_confinement(target, cwd, None)
    if not confined:
        fail(name, f"expected confined=True (no regression) for the project-scoped auto-memory dir; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_denies_different_cwd_slug_session_scoped_memory_dir(tmp_dir: Path) -> None:
    # Anti-blanket-grant (session-scoped shape): a session-scoped Claude
    # Code auto-memory dir keyed to a DIFFERENT cwd's slug must stay
    # confined=False -- widening the pattern must stay fully contained
    # within the already-non-spoofable <slug> root, never become a blanket
    # ~/.claude/projects/** grant.
    name = "hooklib/resolve-confinement-denies-different-cwd-slug-session-scoped-memory-dir"
    cwd = (tmp_dir / "cwd")
    other_cwd = (tmp_dir / "other-cwd")
    cwd.mkdir(parents=True)
    other_cwd.mkdir(parents=True)
    cwd = cwd.resolve()
    other_slug = "".join("-" if c in "/." else c for c in str(other_cwd.resolve()))
    session_uuid = "17021386-8f91-4f13-8fa2-3fa3355ef61c"
    target = Path.home() / ".claude" / "projects" / other_slug / session_uuid / "memory" / "note.md"
    confined, _resolved = hooklib.resolve_confinement(target, cwd, None)
    if confined:
        fail(name, f"expected confined=False for a DIFFERENT cwd's session-scoped Claude Code memory dir; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_denies_session_scoped_dir_not_named_memory(tmp_dir: Path) -> None:
    # Negative control: an arbitrary two-segment-deep path under the
    # trusted slug root whose LAST directory is not named "memory" (e.g. a
    # sibling dir under the session-uuid segment) must still be denied --
    # the session-scoped allowance is not a blanket grant for the whole
    # <slug>/<session-uuid>/** subtree, only its memory/ and MEMORY.md leaf.
    name = "hooklib/resolve-confinement-denies-session-scoped-dir-not-named-memory"
    cwd = (tmp_dir / "cwd")
    cwd.mkdir(parents=True)
    cwd = cwd.resolve()
    slug = "".join("-" if c in "/." else c for c in str(cwd))
    session_uuid = "17021386-8f91-4f13-8fa2-3fa3355ef61c"
    target = Path.home() / ".claude" / "projects" / slug / session_uuid / "not-memory" / "x.md"
    confined, _resolved = hooklib.resolve_confinement(target, cwd, None)
    if confined:
        fail(name, f"expected confined=False for a session-scoped subdir NOT named 'memory'; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_confinement_denies_session_scoped_traversal_escape(tmp_dir: Path) -> None:
    # Traversal/bypass attempt: using ".." segments inside the session-uuid
    # position to try to escape the trusted slug root entirely must still be
    # denied -- resolve_confinement() uses .resolve() + real Path ancestry,
    # never str.startswith(), so a normalized escape must not be confused
    # for a legitimate session-uuid segment.
    name = "hooklib/resolve-confinement-denies-session-scoped-traversal-escape"
    cwd = (tmp_dir / "cwd")
    cwd.mkdir(parents=True)
    cwd = cwd.resolve()
    slug = "".join("-" if c in "/." else c for c in str(cwd))
    target = (
        Path.home()
        / ".claude"
        / "projects"
        / slug
        / ".."
        / ".."
        / ".."
        / "etc"
        / "memory"
        / "escaped.md"
    )
    confined, _resolved = hooklib.resolve_confinement(target, cwd, None)
    if confined:
        fail(name, f"expected confined=False for a '..'-traversal escape attempt via the session-uuid position; got confined={confined}")
        return
    ok(name)


def test_hooklib_resolve_workspace_writable_paths_empty_when_key_missing() -> None:
    name = "hooklib/resolve-workspace-writable-paths-empty-when-key-missing"
    result = hooklib.resolve_workspace_writable_paths({})
    if result == frozenset():
        ok(name)
    else:
        fail(name, f"expected empty frozenset, got {result}")


def test_hooklib_resolve_workspace_writable_paths_coerces_valid_string_list() -> None:
    name = "hooklib/resolve-workspace-writable-paths-coerces-valid-string-list"
    result = hooklib.resolve_workspace_writable_paths(
        {"workspace_writable_paths": ["/tmp/CONTRACTS.md"]}
    )
    expected = frozenset({Path("/tmp/CONTRACTS.md").resolve()})
    if result == expected:
        ok(name)
    else:
        fail(name, f"expected {expected}, got {result}")


def test_hooklib_resolve_workspace_writable_paths_skips_non_string_entries() -> None:
    name = "hooklib/resolve-workspace-writable-paths-skips-non-string-entries"
    result = hooklib.resolve_workspace_writable_paths(
        {"workspace_writable_paths": ["/tmp/CONTRACTS.md", 42, None, ""]}
    )
    expected = frozenset({Path("/tmp/CONTRACTS.md").resolve()})
    if result == expected:
        ok(name)
    else:
        fail(name, f"expected {expected}, got {result}")


def test_hooklib_resolve_workspace_writable_paths_empty_when_not_a_list() -> None:
    name = "hooklib/resolve-workspace-writable-paths-empty-when-not-a-list"
    result = hooklib.resolve_workspace_writable_paths({"workspace_writable_paths": "not-a-list"})
    if result == frozenset():
        ok(name)
    else:
        fail(name, f"expected empty frozenset, got {result}")


def test_hooklib_resolve_workspace_writable_paths_skips_null_byte_entry_without_raising() -> None:
    # Bug B (REM-FIX, live-reproduced): Path(entry).resolve() raises ValueError ("embedded null
    # byte") for a string containing "\x00" -- not OSError/RuntimeError, so the pre-fix except
    # tuple let it propagate uncaught. Must degrade to dropping the malformed entry (never raise),
    # while still keeping a valid sibling entry.
    name = "hooklib/resolve-workspace-writable-paths-skips-null-byte-entry-without-raising"
    result = hooklib.resolve_workspace_writable_paths(
        {"workspace_writable_paths": ["/tmp/CONTRACTS.md", "bad\x00entry"]}
    )
    expected = frozenset({Path("/tmp/CONTRACTS.md").resolve()})
    if result == expected:
        ok(name)
    else:
        fail(name, f"expected {expected} (null-byte entry dropped, valid sibling kept), got {result}")


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
    if not hooklib.matches_memory_finalize_permit_shape(tokens):
        fail(name, "expected True for the exact documented permit-write shape")
        return
    ok(name)


def test_hooklib_matches_permit_shape_true_regardless_of_target_spelling() -> None:
    # Root-cause regression: matches_memory_finalize_permit_shape() no
    # longer takes or checks a path-spelling literal -- path spelling is
    # entirely the CALLER's concern (via resolve_confinement() equality).
    # An absolute-path spelling and a "$PROJECT_ROOT/"-prefixed spelling
    # (the router's own SKILL.md-documented form) must both match the
    # SHAPE exactly the same as the bare-relative spelling above.
    name = "hooklib/matches-permit-shape-true-regardless-of-target-spelling"
    for target in (
        "/abs/path/.craftflow/state/.memory-finalize",
        "$PROJECT_ROOT/.craftflow/state/.memory-finalize",
        "./.craftflow/state/.memory-finalize",
    ):
        tokens = hooklib.split_subcommands(f"printf '%s' 'wf-1234' > {target}")[0]
        if not hooklib.matches_memory_finalize_permit_shape(tokens):
            fail(name, f"expected True for target spelling {target!r} (shape-only match)")
            return
    ok(name)


def test_hooklib_matches_permit_shape_false_for_different_printf_args() -> None:
    name = "hooklib/matches-permit-shape-false-for-different-printf-args"
    tokens = hooklib.split_subcommands(
        "printf '%s\\ninjected' 'wf-1234' > .craftflow/state/.memory-finalize"
    )[0]
    if hooklib.matches_memory_finalize_permit_shape(tokens):
        fail(name, "expected False for a printf with a different format string")
        return
    ok(name)


def test_hooklib_matches_permit_shape_false_for_heredoc() -> None:
    name = "hooklib/matches-permit-shape-false-for-heredoc"
    tokens = hooklib.split_subcommands(
        "cat << EOF > .craftflow/state/.memory-finalize"
    )[0]
    if hooklib.matches_memory_finalize_permit_shape(tokens):
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
    if hooklib.matches_memory_finalize_permit_shape(tokens):
        fail(name, "expected False for a value token containing command substitution")
        return
    ok(name)


def test_hooklib_matches_permit_shape_false_for_substitution_in_value_project_root_prefixed(
) -> None:
    # Same as above, but with a "$PROJECT_ROOT/"-prefixed target -- proves
    # looks_dynamic(_value) rejection is independent of, and unaffected by,
    # dropping the target-spelling literal check.
    name = "hooklib/matches-permit-shape-false-for-substitution-in-value-project-root-prefixed"
    tokens = hooklib.split_subcommands(
        "printf '%s' \"$(touch /tmp/x)\" > \"$PROJECT_ROOT/.craftflow/state/.memory-finalize\""
    )[0]
    if hooklib.matches_memory_finalize_permit_shape(tokens):
        fail(name, "expected False for a value token containing command substitution")
        return
    ok(name)


def test_hooklib_matches_permit_shape_false_for_six_token_shape() -> None:
    # A DIFFERENT (6-token) shape targeting the same file must still be
    # denied -- this stays a narrow allowlist, not a broadened one.
    name = "hooklib/matches-permit-shape-false-for-six-token-shape"
    tokens = hooklib.split_subcommands(
        "printf '%s' 'wf-1234' extra > .craftflow/state/.memory-finalize"
    )[0]
    if hooklib.matches_memory_finalize_permit_shape(tokens):
        fail(name, "expected False for a 6-token shape")
        return
    ok(name)


def test_hooklib_split_subcommands_splits_on_bare_newline() -> None:
    # Root-cause regression (2026-08-19 DEBUG workflow): CONTROL_OPERATORS
    # includes "\n", but shlex's default `whitespace` already contains "\n"
    # -- with `whitespace_split=True`, shlex silently CONSUMES a bare
    # newline as ordinary whitespace before it is ever emitted as its own
    # token, so the `if token in CONTROL_OPERATORS` check below can never
    # see it. Live-reproduced: any two-line Bash command (the single most
    # ordinary multi-statement shape, e.g. the router's own documented
    # `printf ... > .memory-finalize` permit-write followed by a second
    # line) was silently glued into ONE subcommand token list instead of
    # being split into two, corrupting every per-subcommand shape/target
    # check downstream (matches_memory_finalize_permit_shape() in
    # particular -- see the sibling pretooluse-guard/pretooluse-bash-guard
    # regression tests below).
    name = "hooklib/split-subcommands-splits-on-bare-newline"
    subcommands = hooklib.split_subcommands(
        "printf '%s' 'wf-1234' > .craftflow/state/.memory-finalize\necho done"
    )
    if subcommands != [
        ["printf", "%s", "wf-1234", ">", ".craftflow/state/.memory-finalize"],
        ["echo", "done"],
    ]:
        fail(name, f"expected the printf and echo lines split into two subcommands; got {subcommands!r}")
        return
    ok(name)


def test_hooklib_split_subcommands_preserves_newline_inside_quotes() -> None:
    # Companion coverage for the fix above: a newline INSIDE a quoted
    # argument is real string content, not a statement separator, and must
    # stay part of its own token, not be split into a spurious extra
    # subcommand.
    name = "hooklib/split-subcommands-preserves-newline-inside-quotes"
    subcommands = hooklib.split_subcommands("printf '%s' 'line1\nline2' > out.txt")
    if subcommands != [["printf", "%s", "line1\nline2", ">", "out.txt"]]:
        fail(name, f"expected the quoted newline to stay inside its own token; got {subcommands!r}")
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


def test_cursor_adapter_logs_target_crash_but_stays_fail_open(tmp_dir: Path) -> None:
    # REM-FIX (silent-failure-hunter, CRITICAL): translate_output() in
    # craftflow_cursor_adapter.py fails open (exit 0) whenever the delegated
    # target script exits non-zero -- including an uncaught Python exception
    # in the target script -- with zero log_event call of its own. A crash in
    # a wired guard/verify script (e.g. craftflow_pretooluse_bash_guard.py)
    # would silently no-op with no audit trail. This proves: (a) fail-open is
    # preserved (Cursor must still get exit 0), and (b) a log_event entry now
    # records the crash (target script path + returncode) before the adapter
    # returns its fail-open decision.
    name = "cursor-adapter/logs-target-crash-stays-fail-open"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    broken_target = tmp_dir / "broken_target.py"
    broken_target.write_text(
        "raise RuntimeError('boom: simulated crash in target hook script')\n",
        encoding="utf-8",
    )
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "event": "PreToolUse",
        "toolName": "Bash",
        "toolInput": {"command": "rm -rf /"},
        "cwd": str(project_root),
    }
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "craftflow_cursor_adapter.py"),
            str(broken_target),
            "--tool",
            "Bash",
            "--event",
            "PreToolUse",
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        fail(
            name,
            f"expected fail-open (exit 0) preserved on target-script crash; got exit {result.returncode}",
        )
        return
    log_path = project_root / ".craftflow" / "state" / "craftflow-hook-events.log"
    if not log_path.exists():
        fail(name, f"expected log file {log_path} to exist after target-script crash")
        return
    log_lines = [ln for ln in log_path.read_text(encoding="utf-8").strip().splitlines() if ln.strip()]
    matching = [ln for ln in log_lines if "cursor_adapter_target_crash" in ln]
    if not matching:
        fail(
            name,
            "expected a log_event entry naming 'cursor_adapter_target_crash' for the "
            f"uncaught target-script exception; got lines: {log_lines!r}",
        )
        return
    entry = json.loads(matching[0])
    if str(broken_target) not in json.dumps(entry):
        fail(name, f"expected logged event to include target script path {broken_target}; got: {entry!r}")
        return
    if "returncode" not in entry or entry.get("returncode") == 0:
        fail(name, f"expected logged event to include a non-zero returncode field; got: {entry!r}")
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
# pretooluse-bash-guard: REM-FIX (residual gap found in post-BUILD
# verification of wf-build-craftflow-guardrail-harden-20260728-093811-
# 2c402af7, commit 54f6756) -- wildcard as a MIDDLE path segment, e.g.
# `rm -rf ./*/.git`. `_is_in_cwd_critical` only ever recognized a literal
# `*`/`.` as the resolved path's OWN final component (`resolved.name`), or
# an exact/descendant match against a CRITICAL_TOP_LEVEL_CHILDREN path -- a
# wildcard used as a MIDDLE segment (`./*/.git`, `./*/packages`, `*/tools`,
# `./*/*/packages`) resolves via plain pathlib.Path normalization to a
# literal, never-glob-expanded path like `<cwd>/*/.git`, which is neither
# equal to nor a descendant of `<cwd>/.git` -- so it silently fell through
# to ALLOW. Live-reproduced against the exact merged commit 54f6756 before
# this fix: empty stdout (allowed) for `rm -rf ./*/.git`.
# ---------------------------------------------------------------------------

def test_bash_guard_blocks_rm_rf_wildcard_middle_segment_dotgit(tmp_dir: Path) -> None:
    # The exact residual bypass reported in post-BUILD verification:
    # `rm -rf ./*/.git` resolves to a literal `<cwd>/*/.git` path that a
    # real shell's glob would plausibly expand to hit a nested `.git` under
    # any top-level directory -- must be denied, not silently allowed.
    name = "pretooluse-bash-guard/blocks-rm-rf-wildcard-middle-segment-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./*/.git"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./*/.git' (wildcard middle segment hitting .git); got: {out!r}")
        return
    ok(name)


# Safe-shell guard tests (Workstream B — concept-ported from
# xai-org/grok-build's xai-grok-hooks safe-shell-guard.sh /
# no-recursive-grep-guard.py, Apache-2.0; see this plugin's NOTICE)
# ---------------------------------------------------------------------------

def test_safe_shell_guard_blocks_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for rm -rf /; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_wildcard_middle_segment_packages(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-rm-rf-wildcard-middle-segment-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./*/packages"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./*/packages' (wildcard middle segment hitting packages); got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_blocks_sudo_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-sudo-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "sudo rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for sudo rm -rf /; got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_blocks_catastrophic_command_on_second_line(tmp_dir: Path) -> None:
    # Regression for the 2026-08-19 DEBUG workflow (commit ebdc67d):
    # _split_subcommands()'s shlex tokenizer used shlex's default
    # `whitespace`, which already contains "\n" -- with whitespace_split=True
    # a bare newline was silently consumed as ordinary whitespace before it
    # could ever be emitted as its own token, so `token in CONTROL_OPERATORS`
    # never saw it. A multi-line command was therefore flattened into ONE
    # subcommand, and `_check_tokens()` -- which only inspects the FIRST
    # token of each subcommand as the command name -- never inspected
    # "rm" at all when it appeared alone on its own second line. This is
    # the exact reported evasion shape: `echo hello` on line 1 (benign,
    # sets the flattened argv0), `rm -rf /` on line 2 (the real payload).
    # Before ebdc67d this command was silently ALLOWED; it must now DENY.
    name = "safe-shell-guard/blocks-catastrophic-command-on-second-line"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "echo hello\nrm -rf /"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            f"expected deny for a catastrophic command on line 2 of a multi-line Bash "
            f"command (echo hello\\nrm -rf /); got: {out!r}",
        )
        return
    ok(name)


def test_safe_shell_guard_allows_benign_multiline_command(tmp_dir: Path) -> None:
    # Companion negative control for the fix above: proves the newline-
    # splitting fix does not turn every multi-line command into a false
    # positive -- only a genuinely catastrophic subcommand should deny.
    name = "safe-shell-guard/allows-benign-multiline-command"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "echo hello\ngit status"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if out:
        fail(name, f"expected silent allow for a benign multi-line command; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_wildcard_middle_segment_tools_no_dot_prefix(tmp_dir: Path) -> None:
    # Variant: no leading "./" -- a bare `*/tools` token shape, proving the
    # fix is not keyed to the exact reported command's literal spelling.
    name = "pretooluse-bash-guard/blocks-rm-rf-wildcard-middle-segment-tools-no-dot-prefix"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf */tools"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf */tools' (wildcard middle segment, no ./ prefix); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_double_wildcard_middle_segments_packages(tmp_dir: Path) -> None:
    # Two consecutive wildcard segments before the critical child.
    name = "pretooluse-bash-guard/blocks-rm-rf-double-wildcard-middle-segments-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./*/*/packages"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./*/*/packages' (two wildcard segments); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_wildcard_critical_child_at_deeper_depth(tmp_dir: Path) -> None:
    # A wildcard directly followed by a critical child name, but nested
    # deeper than the first segment after cwd -- proves the check isn't
    # hardcoded to only the first two path components.
    name = "pretooluse-bash-guard/blocks-rm-rf-wildcard-critical-child-at-deeper-depth"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./sub1/sub2/*/tools"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./sub1/sub2/*/tools' (wildcard-critical adjacency at depth); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_globstar_middle_segment_dotgit(tmp_dir: Path) -> None:
    # Self-caught during this fix's own adversarial verification pass: a
    # globstar (`**`, bash's recursive `shopt -s globstar` wildcard) as the
    # middle segment is the same bypass class as a single `*` and must also
    # be denied -- a single-star-only check would have missed it.
    name = "pretooluse-bash-guard/blocks-rm-rf-globstar-middle-segment-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./**/.git"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./**/.git' (globstar middle segment hitting .git); got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_wildcard_middle_segment_noncritical_name(tmp_dir: Path) -> None:
    # Anti-hardcode control: a wildcard immediately followed by a
    # NON-critical name must stay allowed -- the fix must key off
    # CRITICAL_TOP_LEVEL_CHILDREN membership, not "any wildcard anywhere."
    name = "pretooluse-bash-guard/allows-wildcard-middle-segment-noncritical-name"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./*/scratch"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'rm -rf ./*/scratch' (wildcard + non-critical name); got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_allows_rm_rf_subdir(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-rm-rf-non-root-subdir"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "rm -rf ./build/tmp-artifacts"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if out:
        fail(name, f"expected silent allow for rm -rf on a non-root subdir; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_dot_slash_star_still_denied(tmp_dir: Path) -> None:
    # Confirmed still-working denial (must not regress): `rm -rf ./*` on its
    # own (bare trailing wildcard, no middle-segment shape at all).
    name = "pretooluse-bash-guard/blocks-rm-rf-dot-slash-star-still-denied"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./*"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./*' (regression guard); got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_blocks_mkfs_variant(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-mkfs-variant"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "mkfs.ext4 /dev/sda1"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for mkfs.ext4; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_dot_slash_dotgit_still_denied(tmp_dir: Path) -> None:
    # Confirmed still-working denial (must not regress): `rm -rf ./.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-dot-slash-dotgit-still-denied"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./.git"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./.git' (regression guard); got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_blocks_fork_bomb(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-classic-fork-bomb"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": ":(){ :|:& };:"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for the classic fork bomb; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_traversal_normalizing_to_dotgit_still_denied(tmp_dir: Path) -> None:
    # Confirmed still-working denial (must not regress): a traversal literal
    # that normalizes back to `.git` (`./packages/../.git`).
    name = "pretooluse-bash-guard/blocks-rm-rf-traversal-normalizing-to-dotgit-still-denied"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./packages/../.git"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./packages/../.git' (regression guard); got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_blocks_fork_bomb_renamed(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-renamed-fork-bomb"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "bomb(){bomb|bomb&};bomb"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for a renamed fork-bomb function; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# pretooluse-bash-guard: REM-FIX round 2 (2 CRITICAL sibling bypasses found
# by code-reviewer in the wildcard-middle-segment fix above):
#
# 1. `_BARE_WILDCARD_COMPONENT_RE` (recognizes bare `*`/globstar `**`) was
#    only wired into `_has_wildcard_adjacent_critical_child` -- the older,
#    semantically-identical `resolved.name in ("*", ".")` trailing check
#    was left untouched, so `rm -rf **`/`rm -rf ./**` (same blast radius as
#    `rm -rf *`) were live-confirmed ALLOWED.
# 2. `_BARE_WILDCARD_COMPONENT_RE` requires the ENTIRE component to be `*`
#    chars, and CRITICAL_TOP_LEVEL_CHILDREN membership was exact-string
#    only -- a partial-glob component like `pack*ages` (a real, valid bash
#    glob expanding to `packages`) was neither a bare wildcard nor an exact
#    match, so it evaded detection entirely at both the top-level-child
#    position (`./pack*ages`) and as the literal immediately after a
#    wildcard middle segment (`./*/pack*ages`).
# ---------------------------------------------------------------------------

def test_bash_guard_blocks_rm_rf_bare_globstar_still_denied(tmp_dir: Path) -> None:
    # CRITICAL 1: `rm -rf **` must have the identical blast radius as
    # `rm -rf *` under `shopt -s globstar` -- live-confirmed ALLOWED before
    # this fix.
    name = "pretooluse-bash-guard/blocks-rm-rf-bare-globstar-still-denied"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf **"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf **' (bare globstar, same blast radius as 'rm -rf *'); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_dot_slash_globstar_still_denied(tmp_dir: Path) -> None:
    # CRITICAL 1 variant: `rm -rf ./**` -- same bypass with a leading `./`.
    name = "pretooluse-bash-guard/blocks-rm-rf-dot-slash-globstar-still-denied"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./**"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./**' (bare globstar, same blast radius as 'rm -rf *'); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_partial_wildcard_top_level_packages(tmp_dir: Path) -> None:
    # CRITICAL 2: `pack*ages` is a real, valid bash glob that expands to
    # `packages` -- neither a bare wildcard nor an exact string match, so it
    # evaded detection entirely as a TOP-LEVEL child of cwd.
    name = "pretooluse-bash-guard/blocks-rm-rf-partial-wildcard-top-level-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./pack*ages"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./pack*ages' (partial-glob top-level match for 'packages'); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_partial_wildcard_middle_segment_packages(tmp_dir: Path) -> None:
    # CRITICAL 2, the exact middle-segment bug class this whole workflow's
    # fix targets, defeated by attaching characters to the critical name:
    # `./*/pack*ages` combines a wildcard middle segment with a partial-glob
    # critical-name component.
    name = "pretooluse-bash-guard/blocks-rm-rf-partial-wildcard-middle-segment-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./*/pack*ages"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./*/pack*ages' (wildcard middle segment + partial-glob critical name); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_globstar_and_partial_wildcard_combo(tmp_dir: Path) -> None:
    # Adversarial combo (memory: this bug class has recurred 3x -- combine
    # BOTH issues in one command): a globstar middle segment immediately
    # followed by a partial-glob critical-name component.
    name = "pretooluse-bash-guard/blocks-rm-rf-globstar-and-partial-wildcard-combo"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./**/pack*ages"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./**/pack*ages' (globstar + partial-glob combo); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_partial_wildcard_final_component_nested(tmp_dir: Path) -> None:
    # Adversarial: partial-wildcard on the FINAL component nested beneath a
    # literal top-level critical directory reference is not the shape here
    # -- instead this proves a partial-glob top-level match is detected
    # regardless of what's nested beneath it (descendant generalization,
    # not just the exact top-level path itself).
    name = "pretooluse-bash-guard/blocks-rm-rf-partial-wildcard-top-level-descendant"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./pack*ages/subdir"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./pack*ages/subdir' (partial-glob top-level match, nested descendant); got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_partial_wildcard_noncritical_name(tmp_dir: Path) -> None:
    # Anti-hardcode control: a partial-glob component that does NOT expand
    # to any CRITICAL_TOP_LEVEL_CHILDREN name must stay allowed -- proves
    # the fix keys off actual fnmatch membership, not "any component
    # containing a wildcard."
    name = "pretooluse-bash-guard/allows-partial-wildcard-noncritical-name"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./buil*d-tmp"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'rm -rf ./buil*d-tmp' (partial-glob, non-critical name); got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_ignores_non_bash_tool(tmp_dir: Path) -> None:
    name = "safe-shell-guard/ignores-non-bash-tool"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Edit", "cwd": str(tmp_dir), "tool_input": {"file_path": "x.md"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if out:
        fail(name, f"expected silent allow for a non-Bash tool; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_wildcard_middle_segment_still_noncritical(tmp_dir: Path) -> None:
    # Regression guard for the false-positive shapes named in this REM-FIX's
    # requirements: a bare wildcard middle segment followed by an unrelated
    # literal name must stay allowed.
    name = "pretooluse-bash-guard/allows-wildcard-middle-segment-still-noncritical"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./*/build-tmp"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'rm -rf ./*/build-tmp' (regression guard, must not over-block); got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_allows_benign_command(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-benign-command"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "git status"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if out:
        fail(name, f"expected silent allow for a benign command; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_wildcard_middle_segment_node_modules_cache(tmp_dir: Path) -> None:
    # Regression guard for the second false-positive shape named in this
    # REM-FIX's requirements.
    name = "pretooluse-bash-guard/allows-wildcard-middle-segment-node-modules-cache"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./*/node_modules/.cache"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'rm -rf ./*/node_modules/.cache' (regression guard, must not over-block); got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_recursive_grep_off_by_default(tmp_dir: Path) -> None:
    name = "safe-shell-guard/recursive-grep-inert-by-default"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "grep -r foo /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if out:
        fail(name, f"expected recursive-grep check to be fully inert with no opt-in config; got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_recursive_grep_blocked_when_opted_in(tmp_dir: Path) -> None:
    name = "safe-shell-guard/recursive-grep-blocked-when-opted-in"
    plugin_copy = tmp_dir / "plugin"
    shutil.copytree(PLUGIN_ROOT, plugin_copy)
    config_dir = plugin_copy / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "hook-mode.json").write_text(
        json.dumps({"recursiveGrepGuard": "block"}), encoding="utf-8"
    )
    env = {"CLAUDE_PLUGIN_ROOT": str(plugin_copy)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "grep -r foo /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for recursive grep against / when opted in to block mode; got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_recursive_grep_no_false_positive_on_pattern_text(tmp_dir: Path) -> None:
    name = "safe-shell-guard/recursive-grep-no-false-positive-on-pattern-text"
    plugin_copy = tmp_dir / "plugin"
    shutil.copytree(PLUGIN_ROOT, plugin_copy)
    config_dir = plugin_copy / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "hook-mode.json").write_text(
        json.dumps({"recursiveGrepGuard": "block"}), encoding="utf-8"
    )
    env = {"CLAUDE_PLUGIN_ROOT": str(plugin_copy)}
    # "recursive" appears only in the SEARCH PATTERN, not as an actual -r flag.
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": 'grep "recursive" ./notes.txt'},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out:
        fail(name, f"expected no deny when 'recursive' only appears in the search pattern; got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_recursive_grep_no_false_positive_in_unrelated_segment(tmp_dir: Path) -> None:
    name = "safe-shell-guard/recursive-grep-no-false-positive-in-unrelated-pipeline-segment"
    plugin_copy = tmp_dir / "plugin"
    shutil.copytree(PLUGIN_ROOT, plugin_copy)
    config_dir = plugin_copy / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "hook-mode.json").write_text(
        json.dumps({"recursiveGrepGuard": "block"}), encoding="utf-8"
    )
    env = {"CLAUDE_PLUGIN_ROOT": str(plugin_copy)}
    # "grep -r /" only appears as a quoted STRING ARGUMENT to `echo` in one
    # pipeline segment -- not as a real grep invocation in any subcommand.
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": 'echo "grep -r /" | cat'},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' in out:
        fail(name, f"expected no deny for a recursive-grep flag embedded in an unrelated quoted segment; got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_blocks_command_builtin_prefix(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-command-builtin-prefix-bypass"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "command rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for `command rm -rf /` (builtin-prefix bypass); got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_allows_command_builtin_benign(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-command-builtin-benign"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "command git status"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if out:
        fail(name, f"expected silent allow for command-builtin-wrapped benign command; got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_blocks_eval_wrapped_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-eval-wrapped-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": 'eval "rm -rf /"'}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for eval-wrapped rm -rf /; got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_allows_benign_eval_command(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-benign-eval-command"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": 'eval "git status"'}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if out:
        fail(name, f"expected silent allow for benign eval-wrapped command; got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_blocks_bash_c_mkfs(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-bash-c-wrapped-mkfs"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": 'bash -c "mkfs.ext4 /dev/sda1"'},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for bash -c wrapped mkfs.ext4; got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_blocks_sh_c_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-sh-c-wrapped-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": 'sh -c "rm -rf /"'}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for sh -c wrapped rm -rf /; got: {out!r}")
        return
    ok(name)


def test_safe_shell_guard_fails_closed_on_unparseable_command(tmp_dir: Path) -> None:
    name = "safe-shell-guard/fails-closed-on-unparseable-command"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": 'rm -rf / ; echo "unterminated'},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected fail-closed deny for an unparseable/malformed command; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# pretooluse-bash-guard: REM-FIX round 3 (3rd consecutive code-review-found
# bypass in `_has_wildcard_adjacent_critical_child()` -- the fnmatch
# unification from round 2 only covered the CRITICAL-NAME side of the
# adjacency pair, not the FILLER side). Live-confirmed bypassed before this
# fix: `rm -rf ./?/.git`, `rm -rf ./[a]/.git`, `rm -rf ./[!x]/packages`,
# `rm -rf ./*a/tools`, `rm -rf ./a*/packages` (all resolved to an empty,
# ALLOW stdout). The control shapes `rm -rf ./*/.git` (must stay denied) and
# `rm -rf ./*/build-tmp` (must stay allowed) were already, and remain,
# correct -- covered by the round-1/round-2 tests above, not re-asserted
# here to avoid duplication.
# ---------------------------------------------------------------------------

def test_bash_guard_blocks_rm_rf_question_mark_middle_segment_dotgit(tmp_dir: Path) -> None:
    # `?` (single-char glob) as the filler immediately before `.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-question-mark-middle-segment-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./?/.git"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./?/.git' (question-mark filler hitting .git); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_bracket_seq_middle_segment_dotgit(tmp_dir: Path) -> None:
    # `[a]` (bracket-sequence glob) as the filler immediately before `.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-bracket-seq-middle-segment-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./[a]/.git"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./[a]/.git' (bracket-sequence filler hitting .git); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_negated_bracket_seq_middle_segment_packages(tmp_dir: Path) -> None:
    # `[!x]` (negated bracket-sequence glob) as the filler immediately
    # before `packages`.
    name = "pretooluse-bash-guard/blocks-rm-rf-negated-bracket-seq-middle-segment-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./[!x]/packages"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./[!x]/packages' (negated bracket-sequence filler hitting packages); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_leading_partial_star_middle_segment_tools(tmp_dir: Path) -> None:
    # `*a` (partial-star, wildcard prefix) as the filler immediately before
    # `tools`.
    name = "pretooluse-bash-guard/blocks-rm-rf-leading-partial-star-middle-segment-tools"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./*a/tools"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./*a/tools' (partial-star-prefix filler hitting tools); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_trailing_partial_star_middle_segment_packages(tmp_dir: Path) -> None:
    # `a*` (partial-star, wildcard suffix) as the filler immediately before
    # `packages`.
    name = "pretooluse-bash-guard/blocks-rm-rf-trailing-partial-star-middle-segment-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./a*/packages"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./a*/packages' (partial-star-suffix filler hitting packages); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_posix_char_class_middle_segment_dotgit(tmp_dir: Path) -> None:
    # Adversarial (own-pass): a POSIX character class (`[[:alpha:]]`) is
    # STILL a bracket-expression glob (starts with `[`) -- must be caught by
    # the same generalized filler check, not just simple `[a]`/`[!x]`.
    name = "pretooluse-bash-guard/blocks-rm-rf-posix-char-class-middle-segment-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./[[:alpha:]]/.git"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./[[:alpha:]]/.git' (POSIX character-class filler hitting .git); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_combined_multi_bracket_middle_segment_dotgit(tmp_dir: Path) -> None:
    # Adversarial (own-pass): a filler with TWO bracket expressions fused
    # into one component (`[ab][cd]`).
    name = "pretooluse-bash-guard/blocks-rm-rf-combined-multi-bracket-middle-segment-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./[ab][cd]/.git"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./[ab][cd]/.git' (combined multi-bracket filler hitting .git); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_two_different_filler_shapes_in_a_row_dotgit(tmp_dir: Path) -> None:
    # Adversarial (own-pass): TWO different wildcard-like filler shapes in a
    # row before the critical name (`?` then `[a]`) -- proves the adjacency
    # check only needs the LAST filler immediately before the critical name,
    # regardless of what shape any EARLIER filler used.
    name = "pretooluse-bash-guard/blocks-rm-rf-two-different-filler-shapes-in-a-row-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./?/[a]/.git"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./?/[a]/.git' (two different filler shapes in a row); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_filler_resembling_critical_name_itself(tmp_dir: Path) -> None:
    # Adversarial (own-pass): the FILLER component itself resembles a
    # critical name (`packages?`) but is used as the filler BEFORE a
    # different critical name (`.git`) -- must still be recognized as a
    # filler (it contains `?`), not mistaken for anything else.
    name = "pretooluse-bash-guard/blocks-rm-rf-filler-resembling-critical-name-itself"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./packages?/.git"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./packages?/.git' (filler resembling a critical name itself, hitting .git); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_nonadjacent_fillers_before_critical_name(tmp_dir: Path) -> None:
    # Adversarial (own-pass): wildcard-like fillers spanning >1 NON-adjacent
    # component before the critical name (`*` ... literal `foo` ... `[a]`
    # immediately before `packages`) -- proves detection isn't defeated by
    # an intervening literal component breaking up the filler run.
    name = "pretooluse-bash-guard/blocks-rm-rf-nonadjacent-fillers-before-critical-name"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./*/foo/[a]/packages"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./*/foo/[a]/packages' (non-adjacent fillers, last one immediately before packages); got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_bracket_seq_middle_segment_noncritical_name(tmp_dir: Path) -> None:
    # Anti-hardcode control: a bracket-sequence filler immediately followed
    # by a NON-critical name must stay allowed.
    name = "pretooluse-bash-guard/allows-bracket-seq-middle-segment-noncritical-name"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./[a]/build-tmp"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'rm -rf ./[a]/build-tmp' (bracket-sequence filler + non-critical name); got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_question_mark_middle_segment_noncritical_name(tmp_dir: Path) -> None:
    # Anti-hardcode control: a question-mark filler immediately followed by
    # a NON-critical name must stay allowed.
    name = "pretooluse-bash-guard/allows-question-mark-middle-segment-noncritical-name"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./?/scratch"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'rm -rf ./?/scratch' (question-mark filler + non-critical name); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_sibling_dotgit(tmp_dir: Path) -> None:
    # CRITICAL live-confirmed bypass: bash unconditionally (no shopt needed)
    # brace-expands `./{a,.git}` into `./a ./.git` -- the second alternative
    # is a top-level critical child. `split_subcommands`' shlex tokenizer
    # never splits on `{`/`}`/`,`, so this whole component previously
    # reached matching as one opaque literal string that neither exact
    # equality nor fnmatch (whose glob vocabulary is only `*`/`?`/`[`) could
    # ever recognize as ".git".
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-sibling-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{a,.git}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{a,.git}}' (brace sibling hitting .git); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_sibling_packages(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-sibling-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{foo,packages}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{foo,packages}}' (brace sibling hitting packages); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_top_level_no_dot_prefix(tmp_dir: Path) -> None:
    # Variant with no leading "./" -- proves the fix isn't keyed to the
    # exact reported command's literal spelling.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-top-level-no-dot-prefix"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf {tools,packages}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf {{tools,packages}}' (bare brace, no ./ prefix); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_middle_segment_packages(tmp_dir: Path) -> None:
    # A brace group used as a MIDDLE path segment (the same shape as the
    # already-fixed `rm -rf ./*/packages` wildcard-middle-segment bypass,
    # except the filler here is a finite, explicit enumeration `{a,b}`
    # instead of an open-ended `*`) -- bash expands to `./a/packages
    # ./b/packages`.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-middle-segment-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{a,b}/packages"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{a,b}}/packages' (brace as middle segment); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_three_alternatives_dotgit(tmp_dir: Path) -> None:
    # Adversarial (own-pass): proves the expander isn't hardcoded to
    # exactly 2 comma-separated alternatives.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-three-alternatives-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{foo,bar,.git}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{foo,bar,.git}}' (3-alternative brace group); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_combined_partial_wildcard_packages(tmp_dir: Path) -> None:
    # Adversarial (own-pass): one brace alternative is ITSELF a partial
    # wildcard (`pack*ages`, a real bash glob expanding to `packages`) --
    # proves each expanded alternative is routed through the same fnmatch
    # check the plain-glob middle-segment fix already uses, not a plain
    # string-equality check.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-combined-partial-wildcard-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{a,pack*ages}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{a,pack*ages}}' (brace alternative is itself a partial wildcard); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_sibling_descendant_dotgit(tmp_dir: Path) -> None:
    # Adversarial (own-pass): brace group forms the TOP-LEVEL component and
    # is followed by a further subpath (`./{a,.git}/sub` -> bash expands to
    # `./a/sub ./.git/sub`) -- proves the fix fires on the top-level
    # component match regardless of what (if anything) follows it, mirroring
    # the existing descendant-of-critical-child protection.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-sibling-descendant-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{a,.git}/sub"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{a,.git}}/sub' (brace sibling with a further subpath); got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_brace_benign_suffix_build_dist_tmp(tmp_dir: Path) -> None:
    # Anti-hardcode control: a brace group where NEITHER alternative
    # (after full-component substitution, "build-tmp"/"dist-tmp") matches a
    # critical child must stay allowed.
    name = "pretooluse-bash-guard/allows-brace-benign-suffix-build-dist-tmp"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{build,dist}-tmp"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'rm -rf ./{{build,dist}}-tmp' (benign brace usage); got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_mkdir_brace_non_destructive(tmp_dir: Path) -> None:
    # Anti-hardcode control: mkdir isn't in the destructive-command
    # vocabulary at all, brace or not.
    name = "pretooluse-bash-guard/allows-mkdir-brace-non-destructive"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "mkdir -p ./{a,b}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'mkdir -p ./{{a,b}}' (non-destructive command); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_extglob_negation_scratch(tmp_dir: Path) -> None:
    # MEDIUM live-confirmed bypass: `./!(scratch)` (bash extglob negation,
    # requires `shopt -s extglob`) matches EVERYTHING except `scratch`,
    # including `.git`/`packages`/`tools`. Unlike a brace group, an extglob
    # negation's full expansion depends on real directory contents this
    # guard cannot see -- treated as an opaque, unresolvable target (the
    # same fail-closed treatment as a `$()`/backtick dynamic substitution)
    # rather than attempting to enumerate it.
    name = "pretooluse-bash-guard/blocks-rm-rf-extglob-negation-scratch"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./!(scratch)"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./!(scratch)' (extglob negation); got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX (follow-up to wf-residual-wildcard-middle-segment-20260729-053459-
# b9b10db1): 2 disclosed residual bypasses at the COMPOSITION boundary
# between two already-hardened mechanisms -- nested brace groups (Bug 1),
# and a command/backtick substitution nested inside a brace group (Bug 2).
# Ground truth for every scenario below was independently confirmed against
# real bash (`bash -c 'echo ...'`) before being encoded as a test.
# ---------------------------------------------------------------------------


def test_bash_guard_blocks_rm_rf_nested_brace_dotgit(tmp_dir: Path) -> None:
    # Bug 1, CRITICAL live-confirmed bypass: `_expand_brace_groups()` only
    # matched the innermost, non-nested `{...}` group -- a nested shape like
    # `./{a,{.git,c}}` left the OUTER braces as literal, unexpanded text
    # instead of bash's real 3-way expansion (`./a ./.git ./c`), so the
    # `.git`/`c` alternatives escaped detection. Ground truth:
    # `bash -c 'echo ./{a,{.git,c}}'` -> `./a ./.git ./c`.
    name = "pretooluse-bash-guard/blocks-rm-rf-nested-brace-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{a,{.git,c}}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{a,{{.git,c}}}}' (nested brace group); got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_nested_brace_benign(tmp_dir: Path) -> None:
    # Anti-hardcode control (non-default variant): a NESTED brace group
    # where NO alternative (a/b/c) matches a critical child must stay
    # allowed -- proves the fix generalizes the nested-expansion logic
    # rather than special-casing the one reported `.git` shape. Ground
    # truth: `bash -c 'echo ./{a,{b,c}}'` -> `./a ./b ./c` (no critical
    # name in the expansion).
    name = "pretooluse-bash-guard/allows-nested-brace-benign"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{a,{b,c}}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'rm -rf ./{{a,{{b,c}}}}' (benign nested brace, no critical alternative); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_doubly_nested_brace_dotgit(tmp_dir: Path) -> None:
    # Adversarial (own-pass, per task instruction to test at least one more
    # composition depth): brace nested inside a nested brace, 3 levels deep.
    # Ground truth: `bash -c 'echo ./{a,{b,{.git,c}}}'` ->
    # `./a ./b ./.git ./c`.
    name = "pretooluse-bash-guard/blocks-rm-rf-doubly-nested-brace-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{a,{b,{.git,c}}}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{a,{{b,{{.git,c}}}}}}' (3-level nested brace); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_command_sub_nested_in_brace_dotgit(tmp_dir: Path) -> None:
    # Bug 2, CRITICAL live-confirmed bypass: a `$(...)` command substitution
    # nested inside a brace group fragments the enclosing `{...,...}`
    # syntax across multiple shlex tokens (`split_subcommands()`'s
    # punctuation_chars=True splits `(`/`)` into their own tokens), so the
    # brace-detection logic never sees one clean component and the trailing
    # `.git` alternative was silently allowed. Ground truth:
    # `bash -c 'echo ./{$(echo a),.git}'` -> `./a ./.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-command-sub-nested-in-brace-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{$(echo a),.git}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{$(echo a),.git}}' (command-sub nested in brace); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_command_sub_nested_in_doubly_nested_brace_packages(tmp_dir: Path) -> None:
    # Adversarial (own-pass): command substitution nested inside a DOUBLY-
    # nested brace group -- combines both disclosed gaps' compositions in
    # one shape. Ground truth:
    # `bash -c 'echo ./{a,{$(echo b),packages}}'` -> `./a ./b ./packages`.
    name = "pretooluse-bash-guard/blocks-rm-rf-command-sub-nested-in-doubly-nested-brace-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{a,{$(echo b),packages}}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{a,{{$(echo b),packages}}}}' (command-sub nested in doubly-nested brace); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_backtick_nested_in_brace_dotgit(tmp_dir: Path) -> None:
    # Adversarial (own-pass): backtick substitution (the OTHER dynamic-
    # substitution syntax bash supports, not just `$(...)`) nested inside a
    # brace group -- proves the fix isn't keyed to the `$(` spelling
    # specifically. Ground truth: `bash -c 'echo ./{`echo a`,.git}'` ->
    # `./a ./.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-backtick-nested-in-brace-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{`echo a`,.git}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{`echo a`,.git}}' (backtick nested in brace); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_deeply_nested_brace_exceeds_bound(tmp_dir: Path) -> None:
    # Adversarial (own-pass): a brace-expansion chain nested 10 levels deep
    # (beyond `_MAX_BRACE_EXPANSION_ROUNDS`'s bound of 6), with a
    # deliberately BENIGN leaf (`buildtmp`, matching no critical child).
    # Ground truth confirms bash's real expansion never touches a critical
    # name here (`bash -c 'echo ./{a,{a,{a,{a,{a,{a,{a,{a,{a,{a,buildtmp}}}}}}}}}}'`
    # -> `./a ./a ./a ./a ./a ./a ./a ./a ./a ./a ./buildtmp`) -- this
    # command is denied ONLY because the recursion-depth bound was hit
    # before expansion could converge, a deliberate fail-CLOSED choice
    # (not a real match), matching this fix's documented "bound recursion
    # depth and fail closed past that bound" design.
    name = "pretooluse-bash-guard/blocks-rm-rf-deeply-nested-brace-exceeds-bound"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    nested = "buildtmp"
    for _ in range(10):
        nested = "{a," + nested + "}"
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": f"rm -rf ./{nested}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 10-level-deep nested brace exceeding the expansion bound (fail-closed); got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# ANSI-C quoting ($'...') bypass -- doubt-verify live-confirmed
#
# bash's `$'...'` syntax decodes bounded backslash escapes (`\xHH` hex,
# `\NNN` octal, plus common single-char C escapes) into literal bytes BEFORE
# tokenization -- `$'\x2e\x67\x69\x74'` decodes to the literal string
# `.git`. `looks_dynamic()` ("$" in token) fires on the RAW, undecoded
# escape text (shlex's own POSIX single-quote handling strips the quote
# markers without performing this decoding), short-circuiting to
# "unresolvable" before the decoded literal is ever matched against
# CRITICAL_TOP_LEVEL_CHILDREN -- and `command_has_traversal_or_wildcard()`
# has zero ANSI-C-quote awareness, so no corroborating signal fires either,
# landing the whole command in the non-denying `unverifiable` bucket.
# Ground truth for every case below verified via real `bash -c 'echo ...'`.
# ---------------------------------------------------------------------------

def test_bash_guard_blocks_rm_rf_ansi_c_hex_quoted_dotgit(tmp_dir: Path) -> None:
    # Ground truth: `bash -c "echo \$'\x2e\x67\x69\x74'"` -> `.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-ansi-c-hex-quoted-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": r"rm -rf $'\x2e\x67\x69\x74'"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for ANSI-C hex-quoted $'\\x2e\\x67\\x69\\x74' decoding to .git; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_ansi_c_quoted_brace_sibling_dotgit(tmp_dir: Path) -> None:
    # Ground truth: `bash -c "echo ./{\$'\x2e\x67\x69\x74',a}"` -> `./.git ./a`.
    name = "pretooluse-bash-guard/blocks-rm-rf-ansi-c-quoted-brace-sibling-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": r"rm -rf ./{$'\x2e\x67\x69\x74',a}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for ANSI-C hex-quoted .git as a brace-group alternative; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_ansi_c_octal_quoted_dotgit(tmp_dir: Path) -> None:
    # Adversarial (own-pass): octal escapes (`\NNN`), not just hex -- a
    # sibling ANSI-C escape form. Ground truth:
    # `bash -c "echo \$'\056\147\151\164'"` -> `.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-ansi-c-octal-quoted-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": r"rm -rf $'\056\147\151\164'"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for ANSI-C octal-quoted $'\\056\\147\\151\\164' decoding to .git; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_ansi_c_quoted_packages(tmp_dir: Path) -> None:
    # Adversarial (own-pass): targeting `packages` (not `.git`) directly,
    # proving the decode isn't keyed to the `.git` name specifically. Ground
    # truth: `bash -c "echo \$'\x70\x61\x63\x6b\x61\x67\x65\x73'"` -> `packages`.
    name = "pretooluse-bash-guard/blocks-rm-rf-ansi-c-quoted-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": r"rm -rf $'\x70\x61\x63\x6b\x61\x67\x65\x73'"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for ANSI-C hex-quoted packages target; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_ansi_c_quoted_brace_sibling_tools(tmp_dir: Path) -> None:
    # Adversarial (own-pass): combined with ANOTHER brace alternative,
    # targeting `tools`. Ground truth:
    # `bash -c "echo ./{a,\$'\x74\x6f\x6f\x6c\x73'}"` -> `./a ./tools`.
    name = "pretooluse-bash-guard/blocks-rm-rf-ansi-c-quoted-brace-sibling-tools"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": r"rm -rf ./{a,$'\x74\x6f\x6f\x6c\x73'}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for ANSI-C hex-quoted tools as a brace-group alternative; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_ansi_c_adjacent_quotes_concatenated_dotgit(tmp_dir: Path) -> None:
    # Adversarial (own-pass, self-found sibling shape per task instruction):
    # bash concatenates adjacent quoted words with no whitespace between
    # them into ONE shell word -- two separate $'...' spans side by side
    # decode-and-concatenate to `.git`, not just one span holding the whole
    # name. Ground truth: `bash -c "echo \$'\x2e'\$'\x67\x69\x74'"` -> `.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-ansi-c-adjacent-quotes-concatenated-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": r"rm -rf $'\x2e'$'\x67\x69\x74'"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for two adjacent ANSI-C-quoted spans concatenating to .git; got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_ansi_c_quoted_suffix_of_literal_prefix_dotgit(tmp_dir: Path) -> None:
    # Adversarial (own-pass, self-found sibling shape): a literal `.`
    # OUTSIDE any quoting, immediately followed (no whitespace) by an
    # ANSI-C-quoted `git` suffix -- bash concatenates a literal fragment and
    # a quoted fragment into one word exactly like two quoted fragments.
    # Ground truth: `bash -c "echo .\$'\x67\x69\x74'"` -> `.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-ansi-c-quoted-suffix-of-literal-prefix-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": r"rm -rf .$'\x67\x69\x74'"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for literal '.' prefix + ANSI-C-quoted 'git' suffix concatenating to .git; got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_ansi_c_quoted_benign_name(tmp_dir: Path) -> None:
    # Anti-hardcode control: an ANSI-C-quoted target that decodes to a
    # BENIGN name (no critical child match) must stay allowed -- proves the
    # fix decodes-then-matches rather than blanket-denying every $'...'
    # token. Ground truth: `bash -c "echo \$'\x73\x63\x72\x61\x74\x63\x68'"`
    # -> `scratch`.
    name = "pretooluse-bash-guard/allows-ansi-c-quoted-benign-name"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": r"rm -rf $'\x73\x63\x72\x61\x74\x63\x68'"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected silent allow for ANSI-C-quoted 'scratch' (benign, no critical-child match); got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX (CRITICAL, live-confirmed bypass, doubt-verify): `_BRACE_GROUP_RE`'s
# empty-pair intolerance. bash's real brace matching BACKTRACKS past an
# immediate, empty (or comma-less) `{...}` candidate closing to a LATER `}`
# that DOES yield a comma-bearing span -- e.g. `./{}a,.git}` -> bash finds
# the FIRST `}` (right after `{`) gives empty content (invalid, no comma),
# so it does NOT stop there; it keeps scanning and closes at the LAST `}`
# instead, giving content `}a,.git` (comma-bearing) -> `./}a ./.git`. The
# prior single-pass `\{([^{}]+)\}` regex can never match at a `{` whose
# immediately-following char is `}` (the character class excludes `}`
# entirely, so there is no way to "skip past" it to a later close for the
# SAME `{`) -- `_component_has_brace_group()` returned False for every one
# of these shapes, and the whole component fell through to a literal
# whole-string fnmatch that trivially can never match a critical name.
# Ground truth for every scenario below independently confirmed against
# real bash (`bash -c 'echo ...'`).
# ---------------------------------------------------------------------------


def test_bash_guard_blocks_rm_rf_brace_empty_pair_before_dotgit(tmp_dir: Path) -> None:
    # CRITICAL live-confirmed bypass (no ANSI-C involved at all -- a
    # pre-existing gap in the round-4/5 brace hardening). Ground truth:
    # `bash -c 'echo ./{}a,.git}'` -> `./}a ./.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-empty-pair-before-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{}a,.git}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{}}a,.git}}' (empty brace pair forces backtrack to .git); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_empty_pair_before_ansi_c_hex_dotgit(tmp_dir: Path) -> None:
    # CRITICAL live-confirmed bypass: `$'\x7d'` ANSI-C-decodes to a literal
    # `}`, reproducing the identical empty-pair shape after decode. Ground
    # truth: `bash -c "echo ./{\$'\x7d'a,.git}"` -> `./}a ./.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-empty-pair-before-ansi-c-hex-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": r"rm -rf ./{$'\x7d'a,.git}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{$'\\x7d'a,.git}}' (ANSI-C hex-decoded empty brace pair); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_empty_pair_before_packages(tmp_dir: Path) -> None:
    # CRITICAL live-confirmed bypass: targeting `packages` instead of
    # `.git`. Ground truth: `bash -c 'echo ./{}a,packages}'` -> `./}a ./packages`.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-empty-pair-before-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{}a,packages}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{}}a,packages}}' (empty brace pair forces backtrack to packages); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_empty_pair_before_ansi_c_octal_dotgit(tmp_dir: Path) -> None:
    # CRITICAL live-confirmed bypass: `$'\175'` is the OCTAL ANSI-C form of
    # the same `}` decode (0o175 == 0x7d), proving the fix isn't keyed to
    # the hex escape spelling. Ground truth:
    # `bash -c "echo ./{\$'\175'a,.git}"` -> `./}a ./.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-empty-pair-before-ansi-c-octal-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": r"rm -rf ./{$'\175'a,.git}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{$'\\175'a,.git}}' (ANSI-C octal-decoded empty brace pair); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_multiple_consecutive_empty_pairs_dotgit(tmp_dir: Path) -> None:
    # Adversarial (own-pass): THREE consecutive empty pairs before the
    # comma-bearing span closes -- proves the backtrack isn't bounded to
    # skipping exactly one invalid candidate. Ground truth:
    # `bash -c 'echo ./{}{}{}a,.git}'` -> `./}{}{}a ./.git`.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-multiple-consecutive-empty-pairs-dotgit"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{}{}{}a,.git}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{}}{{}}{{}}a,.git}}' (3 consecutive empty brace pairs before .git); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_empty_pair_before_tools(tmp_dir: Path) -> None:
    # Adversarial (own-pass): targeting `tools` (the third critical child,
    # not just `.git`/`packages`) -- proves the fix isn't keyed to a
    # specific critical name. Ground truth:
    # `bash -c 'echo ./{}a,tools}'` -> `./}a ./tools`.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-empty-pair-before-tools"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{}a,tools}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{}}a,tools}}' (empty brace pair forces backtrack to tools); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_empty_pair_middle_segment_packages(tmp_dir: Path) -> None:
    # Adversarial (own-pass, DISTINCT code path): the empty-pair backtrack
    # bug also independently breaks `_is_wildcard_like_filler_component()`
    # (used by `_has_wildcard_adjacent_critical_child()` for the
    # already-hardened MIDDLE-SEGMENT bypass family), not just
    # `_component_matches_critical_child()` -- `packages` here is a plain
    # LITERAL trailing path segment (no expansion needed to reach it); the
    # bug is that the preceding `{}a,b}` component was never recognized as
    # a brace-bearing FILLER at all, so the adjacency pair never fired.
    # Ground truth: `bash -c 'echo ./{}a,b}/packages'` ->
    # `./}a/packages ./b/packages` (both alternatives end in /packages).
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-empty-pair-middle-segment-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{}a,b}/packages"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{}}a,b}}/packages' (empty-pair filler immediately before literal packages segment); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_empty_pair_before_dotgit_descendant_sub(tmp_dir: Path) -> None:
    # Adversarial (own-pass): empty-pair-before-dotgit combined with a
    # further subpath after the group closes -- proves the fix fires
    # regardless of what (if anything) follows the group, mirroring the
    # existing plain-brace sibling-descendant coverage. Ground truth:
    # `bash -c 'echo ./{}a,.git}/sub'` -> `./}a/sub ./.git/sub`.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-empty-pair-before-dotgit-descendant-sub"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{}a,.git}/sub"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{}}a,.git}}/sub' (empty-pair-before-dotgit with a further subpath); got: {out!r}")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_brace_double_empty_pair_before_packages(tmp_dir: Path) -> None:
    # Adversarial (own-pass): TWO consecutive empty pairs (distinct count
    # from the 3-pair case above) before the comma-bearing span, targeting
    # `packages`. Ground truth: `bash -c 'echo ./{}{}a,packages}'` ->
    # `./}{}a ./packages`.
    name = "pretooluse-bash-guard/blocks-rm-rf-brace-double-empty-pair-before-packages"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{}{}a,packages}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny for 'rm -rf ./{{}}{{}}a,packages}}' (2 consecutive empty brace pairs before packages); got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_brace_empty_pair_before_benign_name(tmp_dir: Path) -> None:
    # Anti-hardcode control: the identical empty-pair-forces-backtrack
    # shape, but resolving to a BENIGN name only, must stay allowed --
    # proves the fix backtracks-then-matches rather than denying every
    # component with an empty `{}` pair unconditionally. Ground truth:
    # `bash -c 'echo ./{}a,scratch}'` -> `./}a ./scratch`.
    name = "pretooluse-bash-guard/allows-brace-empty-pair-before-benign-name"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{}a,scratch}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'rm -rf ./{{}}a,scratch}}' (empty-pair backtrack resolves to benign-only name); got: {out!r}")
        return
    ok(name)


def test_bash_guard_allows_brace_empty_alternative_after_dotgit_contaminated(tmp_dir: Path) -> None:
    # Anti-hardcode control (task-suggested shape, independently ground-
    # truthed): `./{.git,}a}` places an EMPTY alternative AFTER `.git`
    # (trailing comma) rather than an empty PAIR before it. This is NOT a
    # bypass: the group's FIRST candidate closing brace already has a
    # top-level comma (`.git,`), so bash commits to that close immediately
    # -- no backtrack ever happens -- and the trailing literal `a}` outside
    # the group is concatenated onto EVERY alternative, so the actual
    # expansion is `.gita}` / `a}`, NEITHER of which is the bare critical
    # name `.git`. Ground truth: `bash -c 'echo ./{.git,}a}'` ->
    # `./.gita} ./a}`. Included per task instruction to adversarially test
    # this shape family; ground truth confirms it must stay allowed.
    name = "pretooluse-bash-guard/allows-brace-empty-alternative-after-dotgit-contaminated"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": "rm -rf ./{.git,}a}"},
    }
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if out:
        fail(name, f"expected allow for 'rm -rf ./{{.git,}}a}}' (trailing empty alternative after .git, contaminates to .gita}}/a}}, not a bare .git match); got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX (CRITICAL, algorithmic-complexity DoS): `_MAX_BRACE_SCAN_ITERATIONS`
# only ever bounded a SINGLE `_scan_one_brace_group()` attempt --
# `_iter_brace_groups()`'s outer loop advances by only +1 character after a
# failed/malformed/no-comma attempt, so a component with many nested or
# adjacent comma-less brace pairs triggers up to O(n) independent
# expensive attempts, each re-scanning `_content_has_top_level_comma()`
# over a GROWING content span on every rejected candidate close. For `n`
# perfectly-nested, comma-less empty brace pairs (`"{" * n + "}" * n`) this
# compounds into cubic-class blowup -- live-timed against the PRE-FIX
# `_iter_brace_groups()`: n=100 pairs (200 chars) ~0.03s, n=300 (600
# chars) ~0.7s, n=600 (1200 chars) ~5.9s -- while the registered
# PreToolUse hook timeout for this script (hooks/hooks.json) is only 10s,
# and a timeout-killed hook process is this module's own documented
# "no blocking decision produced" fail-open case, turning a performance bug
# into a genuine bypass. Fixed by a single GLOBAL budget
# (`_MAX_BRACE_SCAN_TOTAL_BUDGET`) shared across the WHOLE
# `_iter_brace_groups()` call, fail-CLOSED (`malformed=True`) once
# exhausted.
# ---------------------------------------------------------------------------


def test_bash_guard_iter_brace_groups_bounded_time_for_deeply_nested_empty_braces() -> None:
    # White-box (direct call, no subprocess) regression test locking in a
    # fixed wall-clock budget for a several-KB adversarial payload -- so
    # this specific DoS class cannot silently reappear even if a future
    # change re-widens or removes the global budget. n=900 (1800 chars)
    # comfortably exceeds `_MAX_BRACE_SCAN_TOTAL_BUDGET`, so the correct
    # post-fix behavior is a FAST fail-CLOSED (`malformed=True`), not a
    # slow full resolution -- pre-fix, this exact shape at even n=600 took
    # ~5.9s (see module-level docstring on `_MAX_BRACE_SCAN_TOTAL_BUDGET`).
    name = "pretooluse-bash-guard/iter-brace-groups-bounded-time-for-deeply-nested-empty-braces"
    n = 900
    text = "{" * n + "}" * n
    budget_seconds = 1.0
    start = time.time()
    groups, malformed = bash_guard._iter_brace_groups(text)
    elapsed = time.time() - start
    if elapsed >= budget_seconds:
        fail(
            name,
            f"_iter_brace_groups() took {elapsed:.3f}s for a {len(text)}-char adversarial "
            f"nested-empty-brace payload (n={n}) -- exceeds the {budget_seconds}s regression "
            "budget; the global brace-scan budget fix may have regressed",
        )
        return
    if not malformed:
        fail(
            name,
            "expected malformed=True (fail-CLOSED) once the global brace-scan budget is "
            f"exhausted for this adversarial payload; got malformed={malformed!r}, groups={groups!r}",
        )
        return
    ok(name)


# Safe-shell guard REM-FIX round 2 tests (4 CRITICAL findings)
# ---------------------------------------------------------------------------

def _assert_denied(name: str, out: str) -> bool:
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny; got: {out!r}")
        return False
    return True


def _assert_allowed(name: str, out: str) -> bool:
    if out:
        fail(name, f"expected silent allow; got: {out!r}")
        return False
    return True


# --- CRITICAL A: sudo long-flag space-separated bypass ---------------------

def test_safe_shell_guard_blocks_sudo_user_long_flag_space_separated(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-sudo-user-long-flag-space-separated"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "sudo --user root rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_sudo_group_long_flag_space_separated(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-sudo-group-long-flag-space-separated"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "sudo --group root rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_allows_sudo_user_long_flag_benign(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-sudo-user-long-flag-benign-command"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "sudo --user root git status"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_allowed(name, out):
        ok(name)


# --- CRITICAL B: ReDoS in FORK_BOMB_RE --------------------------------------

def test_safe_shell_guard_fork_bomb_check_bounded_timing(tmp_dir: Path) -> None:
    name = "safe-shell-guard/fork-bomb-check-bounded-timing-100kb-adversarial"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    adversarial = "a" * 100000
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": adversarial}}
    start = time.time()
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    elapsed = time.time() - start
    if elapsed > 2.0:
        fail(name, f"expected well under 1s (2s hard ceiling incl. subprocess overhead); took {elapsed:.3f}s")
        return
    ok(name)


def test_bash_guard_blocks_rm_rf_deeply_nested_empty_braces_dos_payload_end_to_end(tmp_dir: Path) -> None:
    # End-to-end (real subprocess, full script) sibling of the white-box
    # test above: proves the budget-exhausted fail-CLOSED signal actually
    # propagates all the way to a `deny` decision through the real hook
    # process, within a bounded wall-clock budget, for a real `rm -rf`
    # command carrying the adversarial nested-empty-brace payload
    # immediately before a `.git` target.
    name = "pretooluse-bash-guard/blocks-rm-rf-deeply-nested-empty-braces-dos-payload-end-to-end"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    n = 900
    nested = "{" * n + "}" * n
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": f"rm -rf ./{nested}a,.git}}"},
    }
    budget_seconds = 5.0
    start = time.time()
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    elapsed = time.time() - start
    if elapsed >= budget_seconds:
        fail(
            name,
            f"end-to-end hook invocation took {elapsed:.3f}s for a {len(nested)}-char adversarial "
            f"nested-empty-brace payload (n={n}) -- exceeds the {budget_seconds}s regression "
            "budget (registered hook timeout is 10s)",
        )
        return
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny (fail-CLOSED on budget-exhausted brace scan) for adversarial nested-empty-brace payload; got: {out!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX (CRITICAL, algorithmic-complexity DoS variant -- unbounded CALL
# COUNT): the fix above (`_MAX_BRACE_SCAN_TOTAL_BUDGET`) only bounds the
# total work within a SINGLE `_iter_brace_groups()` call (one path
# component's own scan) -- `_has_wildcard_adjacent_critical_child()` calls
# `_component_has_brace_group()` (a fresh budget, pre-fix) once PER PATH
# COMPONENT in `resolved.parts`, with no cap on segment COUNT. Cost is
# LINEAR in segment count when each segment contains brace characters
# (~2ms/segment vs ~0.003ms/segment for a plain segment) -- live-timed:
# n=1500 segments (91KB command) ~2.88s, n=3000 (183KB) ~6.14s, n=6000
# (~366KB) 13.4-15.0s, exceeding the registered 10s PreToolUse hook
# timeout. Fixed by converting the per-call budget into a single, GLOBAL
# budget shared across EVERY `_iter_brace_groups()` call made during the
# WHOLE hook invocation, not reset per path component.
# ---------------------------------------------------------------------------


def test_bash_guard_bounded_time_for_many_brace_bearing_path_segments_end_to_end(tmp_dir: Path) -> None:
    # End-to-end (real subprocess, full script): the reproduction shape is
    # NOT one deeply-nested brace group in a single component (the sibling
    # test above) but many SEPARATE, individually-small brace-bearing
    # segments joined by "/" -- each one alone comfortably fits under the
    # per-call budget, so only a GLOBAL, call-count-spanning budget (not a
    # per-call one) can bound the total cost. n=1500 keeps RED-phase
    # wall-clock reasonable (router-confirmed ~2.88s pre-fix, matching the
    # documented reproduction) while still clearly exceeding the fixed
    # post-fix regression budget below (~0.96s post-fix, measured).
    name = "pretooluse-bash-guard/bounded-time-for-many-brace-bearing-path-segments-end-to-end"
    cwd_dir = tmp_dir / "cwd"
    cwd_dir.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    n_segments = 1500
    segment = "{" * 30 + "}" * 30
    target = "/".join([segment] * n_segments)
    payload = {
        "tool_name": "Bash",
        "cwd": str(cwd_dir.resolve()),
        "tool_input": {"command": f"rm -rf ./{target}"},
    }
    budget_seconds = 2.0
    start = time.time()
    _, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    elapsed = time.time() - start
    if elapsed >= budget_seconds:
        fail(
            name,
            f"end-to-end hook invocation took {elapsed:.3f}s for {n_segments} brace-bearing "
            f"'/'-joined path segments ({len(target)}-char target) -- exceeds the "
            f"{budget_seconds}s regression budget (registered hook timeout is 10s); the "
            "call-count-spanning global brace-scan budget fix may have regressed",
        )
        return
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny (fail-CLOSED once the shared global brace-scan budget is exhausted "
            f"partway through this many-segment target) for the many-brace-segment payload; got: {out!r}",
        )
        return
    ok(name)


# ---------------------------------------------------------------------------
# Skill-candidate ledger (craftflow_skill_ledger.py) -- white-box + subprocess
# ---------------------------------------------------------------------------
# REM-FIX (found during Phase 1 empirical calibration against the 141 real
# workflow logs): bare positive-verdict strings ("APPROVE", "CLEAN") in
# results.reviewer/results.hunter are the ABSENCE of a finding, not a
# recurring failure/finding signature, and must never become candidates.
# Also: a severity word immediately followed by "=0"/": 0" (e.g.
# "critical=0 so ...") is a zero-count metric, not an actual severity marker.

def test_ledger_bare_verdict_strings_are_not_candidates(tmp_dir: Path) -> None:
    name = "skill-ledger/bare-verdict-strings-are-not-candidates"
    results = {"reviewer": "APPROVE", "hunter": "CLEAN", "verifier": None}
    signals = skill_ledger.extract_finding_signals(results, "reviewer")
    signals += skill_ledger.extract_finding_signals(results, "hunter")
    if signals:
        fail(name, f"expected bare verdict strings APPROVE/CLEAN to produce zero signals, got: {signals}")
        return
    ok(name)


def test_ledger_severity_word_followed_by_zero_count_is_not_that_severity(tmp_dir: Path) -> None:
    name = "skill-ledger/severity-word-followed-by-zero-count-is-not-that-severity"
    sev = skill_ledger.normalize_severity("critical=0 so 1a-SCOPE auto-proceeds without user checkpoint")
    if sev == "critical":
        fail(name, f"expected 'critical=0 so ...' (a zero-count metric) to NOT normalize to severity=critical, got {sev!r}")
        return
    ok(name)


def test_ledger_distinct_workflow_counts_not_raw_events(tmp_dir: Path) -> None:
    name = "skill-ledger/distinct-workflow-not-raw-event-count"
    events = [
        {"event": "remediation_created", "reason": "flaky test in auth module", "ts": "2026-07-01T00:00:00Z"}
        for _ in range(5)
    ]
    ledger = {"schema_version": 1, "candidates": []}
    signals = skill_ledger.collect_signals({}, events)
    ledger = skill_ledger.upsert_candidates(ledger, "wf-test-1", signals)
    signature = skill_ledger.learn_scan.normalize_reason("flaky test in auth module")
    matches = [c for c in ledger["candidates"] if c["signature"] == signature]
    if len(matches) != 1:
        fail(name, f"expected exactly 1 candidate cluster for the 5 same-workflow events, got {len(matches)}")
        return
    c = matches[0]
    if c["distinct_workflows"] != 1:
        fail(name, f"expected distinct_workflows=1 for 5 events inside 1 workflow, got {c['distinct_workflows']}")
        return
    if len(c["workflows"]) != 1:
        fail(name, f"expected workflows list of length 1, got {c['workflows']}")
        return
    ok(name)


def test_ledger_distinct_workflow_counts_two_separate_workflows(tmp_dir: Path) -> None:
    name = "skill-ledger/distinct-workflow-count-increments-across-workflows"
    ledger = {"schema_version": 1, "candidates": []}
    signals_a = skill_ledger.collect_signals(
        {}, [{"event": "remediation_created", "reason": "shared recurring reason", "ts": "2026-07-01T00:00:00Z"}]
    )
    ledger = skill_ledger.upsert_candidates(ledger, "wf-a", signals_a)
    signals_b = skill_ledger.collect_signals(
        {}, [{"event": "remediation_created", "reason": "shared recurring reason", "ts": "2026-07-02T00:00:00Z"}]
    )
    ledger = skill_ledger.upsert_candidates(ledger, "wf-b", signals_b)
    signature = skill_ledger.learn_scan.normalize_reason("shared recurring reason")
    matches = [c for c in ledger["candidates"] if c["signature"] == signature]
    if len(matches) != 1:
        fail(name, f"expected 1 candidate cluster across 2 workflows, got {len(matches)}")
        return
    if matches[0]["distinct_workflows"] != 2:
        fail(name, f"expected distinct_workflows=2 after 2 separate workflows, got {matches[0]['distinct_workflows']}")
        return
    ok(name)


def test_ledger_lru_eviction_at_200_cap(tmp_dir: Path) -> None:
    name = "skill-ledger/lru-eviction-at-200-cap"
    ledger = {"schema_version": 1, "candidates": []}
    for i in range(200):
        ledger["candidates"].append({
            "id": f"id{i:04d}",
            "surface": "unscoped",
            "signature": f"sig-{i}",
            "workflows": [f"wf-{i}"],
            "distinct_workflows": 1,
            "max_severity": "unknown",
            "evidence": [],
            "first_seen": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
            "last_seen": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
            "status": "candidate",
            "promoted_skill": None,
            "rejected_reason": None,
            "rejected_at_distinct_workflows": None,
        })
    # sig-0 has the OLDEST last_seen of the batch -- it must be the eviction target.
    ledger["candidates"][0]["last_seen"] = "2020-01-01T00:00:00Z"
    signals = skill_ledger.collect_signals(
        {}, [{"event": "remediation_created", "reason": "brand new unique signature", "ts": "2026-08-01T00:00:00Z"}]
    )
    ledger = skill_ledger.upsert_candidates(ledger, "wf-new", signals)
    if len(ledger["candidates"]) != 200:
        fail(name, f"expected ledger capped at 200 entries, got {len(ledger['candidates'])}")
        return
    ids = {c["id"] for c in ledger["candidates"]}
    if "id0000" in ids:
        fail(name, "expected the oldest last_seen candidate (id0000) to be LRU-evicted, but it is still present")
        return
    ok(name)


def test_ledger_rejected_stays_rejected_below_doubling_threshold(tmp_dir: Path) -> None:
    name = "skill-ledger/rejected-stays-rejected-below-doubling-threshold"
    signature = skill_ledger.learn_scan.normalize_reason("legacy pattern flagged")
    ledger = {
        "schema_version": 1,
        "candidates": [{
            "id": skill_ledger.candidate_id("unscoped", signature),
            "surface": "unscoped",
            "signature": signature,
            "workflows": ["wf-a", "wf-b"],
            "distinct_workflows": 2,
            "max_severity": "medium",
            "evidence": [],
            "first_seen": "2026-01-01T00:00:00Z",
            "last_seen": "2026-01-01T00:00:00Z",
            "status": "rejected",
            "promoted_skill": None,
            "rejected_reason": "already documented",
            "rejected_at_distinct_workflows": 2,
        }],
    }
    # +1 distinct workflow -> distinct_workflows=3, which is < 2x2=4 -- must stay rejected.
    signals = skill_ledger.collect_signals(
        {}, [{"event": "remediation_created", "reason": "legacy pattern flagged", "ts": "2026-02-01T00:00:00Z"}]
    )
    ledger = skill_ledger.upsert_candidates(ledger, "wf-c", signals)
    c = ledger["candidates"][0]
    if c["status"] != "rejected":
        fail(name, f"expected status to remain 'rejected' at distinct_workflows=3 (below double of 2), got {c['status']!r}")
        return
    ok(name)


def test_ledger_rejected_revives_once_distinct_workflows_double(tmp_dir: Path) -> None:
    name = "skill-ledger/rejected-revives-once-distinct-workflows-double"
    signature = skill_ledger.learn_scan.normalize_reason("legacy pattern flagged again")
    ledger = {
        "schema_version": 1,
        "candidates": [{
            "id": skill_ledger.candidate_id("unscoped", signature),
            "surface": "unscoped",
            "signature": signature,
            "workflows": ["wf-a", "wf-b"],
            "distinct_workflows": 2,
            "max_severity": "medium",
            "evidence": [],
            "first_seen": "2026-01-01T00:00:00Z",
            "last_seen": "2026-01-01T00:00:00Z",
            "status": "rejected",
            "promoted_skill": None,
            "rejected_reason": "already documented",
            "rejected_at_distinct_workflows": 2,
        }],
    }
    for i, wf in enumerate(["wf-c", "wf-d"]):
        signals = skill_ledger.collect_signals(
            {}, [{"event": "remediation_created", "reason": "legacy pattern flagged again", "ts": f"2026-03-0{i+1}T00:00:00Z"}]
        )
        ledger = skill_ledger.upsert_candidates(ledger, wf, signals)
    c = ledger["candidates"][0]
    if c["distinct_workflows"] < 4:
        fail(name, f"test setup issue: expected distinct_workflows>=4 (2x2), got {c['distinct_workflows']}")
        return
    if c["status"] != "candidate":
        fail(name, f"expected revival to status='candidate' once distinct_workflows doubled, got {c['status']!r}")
        return
    if c["rejected_at_distinct_workflows"] is not None:
        fail(name, "expected rejected_at_distinct_workflows to be cleared after revival")
        return
    ok(name)


def test_ledger_atomic_write_survives_os_replace_failure(tmp_dir: Path) -> None:
    name = "skill-ledger/atomic-write-survives-os-replace-failure"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = tmp_dir / "skill-candidates.json"
    original = {
        "schema_version": 1,
        "candidates": [{
            "id": "orig0001", "surface": "unscoped", "signature": "original untouched",
            "workflows": ["wf-orig"], "distinct_workflows": 1, "max_severity": "unknown",
            "evidence": [], "first_seen": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:00Z",
            "status": "candidate", "promoted_skill": None, "rejected_reason": None,
            "rejected_at_distinct_workflows": None,
        }],
    }
    ledger_path.write_text(json.dumps(original), encoding="utf-8")
    original_bytes = ledger_path.read_bytes()

    real_replace = os.replace

    def boom(*a, **kw):
        raise OSError("simulated torn write")

    os.replace = boom
    raised = False
    try:
        skill_ledger.save_ledger_atomic(ledger_path, {"schema_version": 1, "candidates": []})
    except OSError:
        raised = True
    finally:
        os.replace = real_replace

    if not raised:
        fail(name, "expected save_ledger_atomic to propagate the os.replace failure")
        return
    after_bytes = ledger_path.read_bytes()
    if after_bytes != original_bytes:
        fail(name, "original ledger file content changed despite a simulated os.replace failure")
        return
    leftovers = [p for p in tmp_dir.iterdir() if p.name != "skill-candidates.json"]
    if leftovers:
        fail(name, f"expected the temp write file to be cleaned up after failure, found leftovers: {leftovers}")
        return
    ok(name)


def test_ledger_prune_removes_stale_candidate_only(tmp_dir: Path) -> None:
    name = "skill-ledger/prune-removes-stale-candidate-only"
    old_ts = "2020-01-01T00:00:00Z"
    fresh_ts = skill_ledger.now_iso()
    ledger = {
        "schema_version": 1,
        "candidates": [
            {"id": "stale001", "surface": "unscoped", "signature": "stale one", "workflows": ["wf-a"],
             "distinct_workflows": 1, "max_severity": "unknown", "evidence": [], "first_seen": old_ts,
             "last_seen": old_ts, "status": "candidate", "promoted_skill": None, "rejected_reason": None,
             "rejected_at_distinct_workflows": None},
            {"id": "fresh001", "surface": "unscoped", "signature": "fresh one", "workflows": ["wf-b"],
             "distinct_workflows": 1, "max_severity": "unknown", "evidence": [], "first_seen": fresh_ts,
             "last_seen": fresh_ts, "status": "candidate", "promoted_skill": None, "rejected_reason": None,
             "rejected_at_distinct_workflows": None},
            {"id": "rej0001", "surface": "unscoped", "signature": "rejected stale", "workflows": ["wf-c"],
             "distinct_workflows": 1, "max_severity": "unknown", "evidence": [], "first_seen": old_ts,
             "last_seen": old_ts, "status": "rejected", "promoted_skill": None, "rejected_reason": "dup",
             "rejected_at_distinct_workflows": 1},
            {"id": "prom0001", "surface": "unscoped", "signature": "promoted stale", "workflows": ["wf-d"],
             "distinct_workflows": 1, "max_severity": "unknown", "evidence": [], "first_seen": old_ts,
             "last_seen": old_ts, "status": "promoted", "promoted_skill": "foo", "rejected_reason": None,
             "rejected_at_distinct_workflows": None},
        ],
    }
    pruned = skill_ledger.prune_ledger(ledger)
    ids = {c["id"] for c in pruned["candidates"]}
    if "stale001" in ids:
        fail(name, "expected the stale (>90d, no new evidence) candidate entry to be removed by prune")
        return
    if "fresh001" not in ids:
        fail(name, "expected the fresh candidate entry to survive prune")
        return
    if "rej0001" not in ids:
        fail(name, "expected the rejected entry to be left untouched by prune (never dropped)")
        return
    if "prom0001" not in ids:
        fail(name, "expected the promoted entry to be left untouched by prune")
        return
    ok(name)


def _write_promoted_skill_md(project_root: Path, name: str, referenced_paths: str = "", review_after: str = "") -> None:
    """Test helper: writes a minimal, validly-frontmattered canonical
    SKILL.md for a promoted skill under `<project_root>/.claude/skills/<name>/`."""
    skill_dir = project_root / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"name: {name}",
        'description: "Use when doing X. Provides Y for testing purposes only today."',
    ]
    if referenced_paths:
        lines.append(f"craftflow-referenced-paths: {referenced_paths}")
    if review_after:
        lines.append(f"craftflow-review-after: {review_after}")
    lines += ["---", "", "Body content."]
    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


def _promoted_entry(candidate_id: str, promoted_skill: str) -> dict:
    return {
        "id": candidate_id, "surface": "unscoped", "signature": "some recurring issue",
        "workflows": ["wf-a", "wf-b"], "distinct_workflows": 2, "max_severity": "medium",
        "evidence": [], "first_seen": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:00Z",
        "status": "promoted", "promoted_skill": promoted_skill, "rejected_reason": None,
        "rejected_at_distinct_workflows": None,
    }


def test_ledger_prune_rejected_tombstone_untouched(tmp_dir: Path) -> None:
    # Phase 4: the new promoted-rot check must never touch a "rejected"
    # entry -- it is a permanent tombstone (status, reason, and any
    # needs_review-shaped fields all stay exactly as they were).
    name = "skill-ledger/prune-rejected-tombstone-untouched"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ledger = {
        "schema_version": 1,
        "candidates": [{
            "id": "rej0002", "surface": "unscoped", "signature": "rejected thing",
            "workflows": ["wf-a"], "distinct_workflows": 1, "max_severity": "unknown",
            "evidence": [], "first_seen": skill_ledger.now_iso(), "last_seen": skill_ledger.now_iso(),
            "status": "rejected", "promoted_skill": None, "rejected_reason": "duplicate",
            "rejected_at_distinct_workflows": 1,
        }],
    }
    before = json.loads(json.dumps(ledger))
    pruned = skill_ledger.prune_ledger(ledger, tmp_dir)
    entry = next(c for c in pruned["candidates"] if c["id"] == "rej0002")
    if entry.get("status") != "rejected":
        fail(name, f"expected status to remain 'rejected', got {entry.get('status')!r}")
        return
    if "needs_review" in entry or "needs_review_reason" in entry:
        fail(name, f"expected a rejected entry to never gain needs_review fields, got: {entry}")
        return
    if entry != before["candidates"][0]:
        fail(name, f"expected the rejected entry to be byte-for-byte untouched, got: {entry}")
        return
    ok(name)


def test_ledger_prune_promoted_healthy_no_needs_review(tmp_dir: Path) -> None:
    name = "skill-ledger/prune-promoted-healthy-no-needs-review"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "real.ts").write_text("x", encoding="utf-8")
    _write_promoted_skill_md(
        tmp_dir, "healthy-skill",
        referenced_paths="real.ts",
        review_after="2099-01-01T00:00:00Z",
    )
    ledger = {"schema_version": 1, "candidates": [_promoted_entry("p1", "healthy-skill")]}
    pruned = skill_ledger.prune_ledger(ledger, tmp_dir)
    entry = next(c for c in pruned["candidates"] if c["id"] == "p1")
    if entry.get("status") != "promoted":
        fail(name, f"expected status to remain 'promoted', got {entry.get('status')!r}")
        return
    if entry.get("needs_review"):
        fail(name, f"expected a healthy promoted entry to have no needs_review flag, got: {entry}")
        return
    ok(name)


def test_ledger_prune_promoted_missing_referenced_path_flags_stale_path(tmp_dir: Path) -> None:
    name = "skill-ledger/prune-promoted-missing-referenced-path-stale-path"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "real.ts").write_text("x", encoding="utf-8")
    _write_promoted_skill_md(
        tmp_dir, "rotting-skill",
        referenced_paths="real.ts, gone/forever.ts",
        review_after="2099-01-01T00:00:00Z",
    )
    ledger = {"schema_version": 1, "candidates": [_promoted_entry("p2", "rotting-skill")]}
    pruned = skill_ledger.prune_ledger(ledger, tmp_dir)
    entry = next(c for c in pruned["candidates"] if c["id"] == "p2")
    if entry.get("status") != "promoted":
        fail(name, f"expected status to remain 'promoted' (never changed), got {entry.get('status')!r}")
        return
    if entry.get("needs_review") is not True:
        fail(name, f"expected needs_review=True when a referenced path is missing, got: {entry}")
        return
    if entry.get("needs_review_reason") != "stale_path":
        fail(name, f"expected needs_review_reason='stale_path', got {entry.get('needs_review_reason')!r}")
        return
    ok(name)


def test_ledger_prune_promoted_elapsed_review_after_flags_review_after_elapsed(tmp_dir: Path) -> None:
    name = "skill-ledger/prune-promoted-elapsed-review-after"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    _write_promoted_skill_md(
        tmp_dir, "overdue-skill",
        review_after="2020-01-01T00:00:00Z",
    )
    ledger = {"schema_version": 1, "candidates": [_promoted_entry("p3", "overdue-skill")]}
    pruned = skill_ledger.prune_ledger(ledger, tmp_dir)
    entry = next(c for c in pruned["candidates"] if c["id"] == "p3")
    if entry.get("status") != "promoted":
        fail(name, f"expected status to remain 'promoted' (never changed), got {entry.get('status')!r}")
        return
    if entry.get("needs_review") is not True:
        fail(name, f"expected needs_review=True when craftflow-review-after has elapsed, got: {entry}")
        return
    if entry.get("needs_review_reason") != "review_after_elapsed":
        fail(name, f"expected needs_review_reason='review_after_elapsed', got {entry.get('needs_review_reason')!r}")
        return
    ok(name)


def test_ledger_prune_promoted_missing_name_flags_missing_promoted_skill_name(tmp_dir: Path) -> None:
    # HIGH (task #59, item 2): a promoted entry with a missing/blank
    # `promoted_skill` name used to return None from `_check_promoted_rot`
    # (treated as healthy) since there is "nothing on disk to check
    # against" -- but a promoted ledger entry with no recorded skill name at
    # all IS itself a rot condition (the entry can never be reconciled
    # against a real SKILL.md again) and must be flagged for human review,
    # not silently treated as clean.
    name = "skill-ledger/prune-promoted-missing-name-flags-missing-promoted-skill-name"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ledger = {"schema_version": 1, "candidates": [_promoted_entry("p5", None)]}
    pruned = skill_ledger.prune_ledger(ledger, tmp_dir)
    entry = next(c for c in pruned["candidates"] if c["id"] == "p5")
    if entry.get("status") != "promoted":
        fail(name, f"expected status to remain 'promoted' (never changed), got {entry.get('status')!r}")
        return
    if entry.get("needs_review") is not True:
        fail(name, f"expected needs_review=True when promoted_skill is missing, got: {entry}")
        return
    if entry.get("needs_review_reason") != "missing_promoted_skill_name":
        fail(name, f"expected needs_review_reason='missing_promoted_skill_name', got {entry.get('needs_review_reason')!r}")
        return
    ok(name)


def test_ledger_prune_promoted_blank_name_flags_missing_promoted_skill_name(tmp_dir: Path) -> None:
    name = "skill-ledger/prune-promoted-blank-name-flags-missing-promoted-skill-name"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ledger = {"schema_version": 1, "candidates": [_promoted_entry("p6", "   ")]}
    pruned = skill_ledger.prune_ledger(ledger, tmp_dir)
    entry = next(c for c in pruned["candidates"] if c["id"] == "p6")
    if entry.get("needs_review_reason") != "missing_promoted_skill_name":
        fail(name, f"expected needs_review_reason='missing_promoted_skill_name' for a blank/whitespace name, got {entry.get('needs_review_reason')!r}")
        return
    ok(name)


def test_ledger_prune_promoted_unparseable_review_after_flags_unparseable(tmp_dir: Path) -> None:
    # HIGH (task #59, item 3): an unparseable `craftflow-review-after` value
    # (not a real ISO timestamp, and not the `PENDING_APPROVAL` placeholder)
    # used to be silently treated as "not elapsed" (healthy) by
    # `_review_after_elapsed`'s bare `except ValueError: return False` --
    # indistinguishable from a legitimate future date. It must instead be
    # flagged for human review as its own distinct reason.
    name = "skill-ledger/prune-promoted-unparseable-review-after-flags-unparseable"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    _write_promoted_skill_md(
        tmp_dir, "garbled-date-skill",
        review_after="not-a-real-date",
    )
    ledger = {"schema_version": 1, "candidates": [_promoted_entry("p7", "garbled-date-skill")]}
    pruned = skill_ledger.prune_ledger(ledger, tmp_dir)
    entry = next(c for c in pruned["candidates"] if c["id"] == "p7")
    if entry.get("status") != "promoted":
        fail(name, f"expected status to remain 'promoted' (never changed), got {entry.get('status')!r}")
        return
    if entry.get("needs_review") is not True:
        fail(name, f"expected needs_review=True for an unparseable craftflow-review-after value, got: {entry}")
        return
    if entry.get("needs_review_reason") != "unparseable_review_after":
        fail(name, f"expected needs_review_reason='unparseable_review_after', got {entry.get('needs_review_reason')!r}")
        return
    ok(name)


def test_ledger_prune_promoted_referenced_path_escapes_project_root_flags_stale_path(tmp_dir: Path) -> None:
    # MEDIUM (task #59, item 4): a `craftflow-referenced-paths` entry that
    # contains a ".." traversal component used to be joined onto
    # `project_root` and existence-checked with no containment guard. This
    # test plants a REAL file just OUTSIDE `project_root` that the traversal
    # reaches, so the pre-fix code reports "exists" (healthy, no rot) --
    # exactly the silent escape this closes. Must instead fail closed
    # (treated as rot) whenever a referenced-paths entry does not stay
    # contained under `project_root`, regardless of what it happens to
    # resolve to on disk.
    name = "skill-ledger/prune-promoted-referenced-path-escapes-project-root-flags-stale-path"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    escape_target_dir = tmp_dir / "outside-project-root"
    escape_target_dir.mkdir(parents=True, exist_ok=True)
    (escape_target_dir / "escape-target.ts").write_text("x", encoding="utf-8")
    _write_promoted_skill_md(
        project_root, "traversal-skill",
        referenced_paths="../outside-project-root/escape-target.ts",
        review_after="2099-01-01T00:00:00Z",
    )
    ledger = {"schema_version": 1, "candidates": [_promoted_entry("p8", "traversal-skill")]}
    pruned = skill_ledger.prune_ledger(ledger, project_root)
    entry = next(c for c in pruned["candidates"] if c["id"] == "p8")
    if entry.get("needs_review") is not True:
        fail(name, f"expected needs_review=True when a referenced path escapes project_root via '..' (even though the escaped-to file exists), got: {entry}")
        return
    if entry.get("needs_review_reason") != "stale_path":
        fail(name, f"expected needs_review_reason='stale_path' for a path-traversal referenced-paths entry, got {entry.get('needs_review_reason')!r}")
        return
    ok(name)


def test_ledger_prune_promoted_name_escapes_project_root_flags_stale_path(tmp_dir: Path) -> None:
    # MEDIUM (task #59, item 4): a `promoted_skill` name containing a ".."
    # traversal component used to be joined directly onto
    # `project_root/.claude/skills/<name>/SKILL.md` with no containment
    # guard before being read. This test plants a REAL, validly-frontmattered
    # SKILL.md just OUTSIDE `project_root/.claude/skills/` that the traversal
    # reaches, so the pre-fix code reads it successfully (healthy, no rot) --
    # exactly the silent escape this closes. Must fail closed instead of
    # reading/following an escaping path.
    name = "skill-ledger/prune-promoted-name-escapes-project-root-flags-stale-path"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    project_root = tmp_dir / "project"
    # The intermediate ".claude/skills" directory must physically exist for
    # the OS to actually resolve a ".." traversal through it (Path.exists()
    # on a path with ".." components requires every intermediate segment,
    # including ones later cancelled by "..", to be a real, stat-able
    # directory) -- otherwise the traversal can never reach the real escape
    # target and the test would pass "by accident" regardless of any fix.
    (project_root / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
    # Real SKILL.md OUTSIDE project_root/.claude/skills/, placed directly at
    # tmp_dir/escaped-skill/SKILL.md -- exactly where
    # "project_root/.claude/skills/../../../escaped-skill/SKILL.md" resolves
    # to (3 ".." components cancel "skills", ".claude", and "project").
    escaped_skill_dir = tmp_dir / "escaped-skill"
    escaped_skill_dir.mkdir(parents=True, exist_ok=True)
    (escaped_skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: escaped-skill\n"
        'description: "Use when doing X. Provides Y for testing purposes only today."\n'
        "craftflow-review-after: 2099-01-01T00:00:00Z\n"
        "---\n\nBody content.",
        encoding="utf-8",
    )
    ledger = {"schema_version": 1, "candidates": [_promoted_entry("p9", "../../../escaped-skill")]}
    pruned = skill_ledger.prune_ledger(ledger, project_root)
    entry = next(c for c in pruned["candidates"] if c["id"] == "p9")
    if entry.get("needs_review") is not True:
        fail(name, f"expected needs_review=True when promoted_skill name contains '..' traversal (even though the escaped-to SKILL.md is real and healthy), got: {entry}")
        return
    if entry.get("needs_review_reason") != "stale_path":
        fail(name, f"expected needs_review_reason='stale_path' for a traversal promoted_skill name, got {entry.get('needs_review_reason')!r}")
        return
    ok(name)


def test_ledger_prune_multi_entry_malformed_promoted_entry_does_not_affect_healthy_entry(tmp_dir: Path) -> None:
    # Test-coverage gap (task #59, item 7): proves prune_ledger() isolates
    # each promoted entry's rot check from every OTHER entry in the same
    # ledger -- a malformed entry (blank promoted_skill name) must be
    # flagged on its own, while a separate, genuinely healthy promoted entry
    # in the SAME prune() call is completely unaffected (correctly stays
    # needs_review=False).
    name = "skill-ledger/prune-multi-entry-malformed-does-not-affect-healthy-entry"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "real.ts").write_text("x", encoding="utf-8")
    _write_promoted_skill_md(
        tmp_dir, "healthy-skill-in-mixed-batch",
        referenced_paths="real.ts",
        review_after="2099-01-01T00:00:00Z",
    )
    ledger = {
        "schema_version": 1,
        "candidates": [
            _promoted_entry("malformed1", ""),
            _promoted_entry("healthy1", "healthy-skill-in-mixed-batch"),
        ],
    }
    pruned = skill_ledger.prune_ledger(ledger, tmp_dir)

    malformed_entry = next(c for c in pruned["candidates"] if c["id"] == "malformed1")
    if malformed_entry.get("needs_review") is not True:
        fail(name, f"expected the malformed entry to be flagged needs_review=True, got: {malformed_entry}")
        return
    if malformed_entry.get("needs_review_reason") != "missing_promoted_skill_name":
        fail(name, f"expected the malformed entry's reason to be 'missing_promoted_skill_name', got: {malformed_entry.get('needs_review_reason')!r}")
        return

    healthy_entry = next(c for c in pruned["candidates"] if c["id"] == "healthy1")
    if healthy_entry.get("status") != "promoted":
        fail(name, f"expected the healthy entry's status to remain 'promoted', got {healthy_entry.get('status')!r}")
        return
    if healthy_entry.get("needs_review") is not False:
        fail(
            name,
            f"expected the separate healthy entry to be UNAFFECTED by the malformed entry "
            f"in the same batch (needs_review=False), got: {healthy_entry}",
        )
        return
    if healthy_entry.get("needs_review_reason") is not None:
        fail(name, f"expected the healthy entry's needs_review_reason to stay None, got: {healthy_entry.get('needs_review_reason')!r}")
        return
    ok(name)


def test_ledger_prune_honors_state_dir_when_ledger_flag_left_at_default(tmp_dir: Path) -> None:
    # LOW (task #59, item 5): `--state-dir` was declared as a CLI argument
    # and accepted by `cmd_prune`, but its value was never read anywhere in
    # the function body -- unlike `--observe`/`--backtest`, which both honor
    # a custom `--state-dir` to locate workflow artifacts. Proves that when
    # `--ledger` is left at its default and a custom `--state-dir` is given,
    # `--prune` now operates on `<state-dir>/project/skill-candidates.json`
    # (the same nesting convention the built-in default already documents),
    # not the hardcoded default path.
    name = "skill-ledger/prune-honors-state-dir-when-ledger-flag-left-at-default"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    custom_state_dir = tmp_dir / "custom-state"
    ledger_path = custom_state_dir / "project" / "skill-candidates.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps({
        "schema_version": 1,
        "candidates": [{
            "id": "keepme", "surface": "unscoped", "signature": "some fresh candidate",
            "workflows": ["wf-a"], "distinct_workflows": 1, "max_severity": "unknown",
            "evidence": [], "first_seen": skill_ledger.now_iso(), "last_seen": skill_ledger.now_iso(),
            "status": "candidate", "promoted_skill": None, "rejected_reason": None,
            "rejected_at_distinct_workflows": None,
        }],
    }), encoding="utf-8")

    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--prune", "--state-dir", str(custom_state_dir)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode != 0:
        fail(name, f"--prune exited {result.returncode}: {result.stderr[:500]}")
        return
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail(name, f"--prune did not print valid JSON on stdout: {result.stdout[:300]}")
        return
    if payload.get("remaining_candidates") != 1:
        fail(
            name,
            f"expected --prune to operate on <state-dir>/project/skill-candidates.json "
            f"(remaining_candidates=1), got: {payload}",
        )
        return
    ok(name)


def test_ledger_prune_promoted_missing_skill_md_flags_stale_path_no_crash(tmp_dir: Path) -> None:
    # The promoted skill's canonical SKILL.md is entirely absent (e.g. hand-
    # deleted, or the directory was never created) -- must degrade to
    # needs_review_reason='stale_path' without raising.
    name = "skill-ledger/prune-promoted-missing-skill-md-no-crash"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ledger = {"schema_version": 1, "candidates": [_promoted_entry("p4", "never-existed-skill")]}
    pruned = skill_ledger.prune_ledger(ledger, tmp_dir)
    entry = next(c for c in pruned["candidates"] if c["id"] == "p4")
    if entry.get("status") != "promoted":
        fail(name, f"expected status to remain 'promoted' (never changed), got {entry.get('status')!r}")
        return
    if entry.get("needs_review") is not True:
        fail(name, f"expected needs_review=True when the canonical SKILL.md is entirely missing, got: {entry}")
        return
    if entry.get("needs_review_reason") != "stale_path":
        fail(name, f"expected needs_review_reason='stale_path' for a missing SKILL.md, got {entry.get('needs_review_reason')!r}")
        return
    ok(name)


def test_ledger_prune_refuses_to_overwrite_corrupt_ledger_file(tmp_dir: Path) -> None:
    # CRITICAL (task #59, item 1): before this fix, `load_ledger()` caught
    # (OSError, ValueError) on a truncated/corrupt-but-EXISTING ledger file
    # and silently degraded to an empty in-memory ledger -- indistinguishable
    # from the benign "no ledger file at all" case. `cmd_prune` then wrote
    # that empty ledger straight back over the corrupt file via
    # `save_ledger_atomic` and exited 0, permanently destroying whatever was
    # recoverable in the original file. This is now triggered on EVERY
    # workflow via the router's unconditional `--prune` wiring. Proves
    # `--prune` now fails closed instead: non-zero exit, a clean JSON error
    # on stderr, and the original corrupt bytes left completely untouched on
    # disk (no overwrite).
    name = "skill-ledger/prune-refuses-to-overwrite-corrupt-ledger-file"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = tmp_dir / "skill-candidates.json"
    corrupt_bytes = b'{"schema_version": 1, "candidates": [{"id": "trunc'
    ledger_path.write_bytes(corrupt_bytes)

    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--prune", "--ledger", str(ledger_path)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode == 0:
        fail(name, f"expected --prune to fail closed (non-zero exit) on a corrupt ledger file, got exit 0: stdout={result.stdout[:300]!r}")
        return
    try:
        payload = json.loads(result.stderr.strip())
    except json.JSONDecodeError:
        fail(name, f"expected a clean JSON error on stderr, got: {result.stderr[:400]!r}")
        return
    if "error" not in payload:
        fail(name, f"expected an 'error' key in the JSON error payload, got: {payload}")
        return
    after_bytes = ledger_path.read_bytes()
    if after_bytes != corrupt_bytes:
        fail(name, "expected the corrupt ledger file to be left byte-for-byte untouched, but it was overwritten")
        return
    ok(name)


def test_ledger_backtest_never_mutates_real_ledger_file(tmp_dir: Path) -> None:
    name = "skill-ledger/backtest-never-mutates-real-ledger-file"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_dir / ".craftflow" / "state"
    workflows_dir = state_dir / "workflows"
    workflows_dir.mkdir(parents=True)

    artifact = {
        "workflow_uuid": "wf-fixture-1",
        "results": {"reviewer": None, "hunter": None, "verifier": None},
        "remediation_history": [{"reason": "sample recurring issue for backtest fixture"}],
        "memory_notes": [],
    }
    (workflows_dir / "wf-fixture-1.json").write_text(json.dumps(artifact), encoding="utf-8")
    (workflows_dir / "wf-fixture-1.events.jsonl").write_text("", encoding="utf-8")

    real_ledger_dir = state_dir / "project"
    real_ledger_dir.mkdir(parents=True)
    real_ledger_path = real_ledger_dir / "skill-candidates.json"
    real_ledger_path.write_text(json.dumps({"schema_version": 1, "candidates": [{"id": "keepme"}]}), encoding="utf-8")
    before_mtime = real_ledger_path.stat().st_mtime_ns
    before_bytes = real_ledger_path.read_bytes()

    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--backtest", "--state-dir", str(state_dir)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode != 0:
        fail(name, f"--backtest exited {result.returncode}: {result.stderr[:500]}")
        return

    after_mtime = real_ledger_path.stat().st_mtime_ns
    after_bytes = real_ledger_path.read_bytes()
    if before_mtime != after_mtime or before_bytes != after_bytes:
        fail(name, "the real ledger file's mtime/content changed after a --backtest run")
        return

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail(name, f"--backtest did not print valid JSON on stdout: {result.stdout[:300]}")
        return
    if "gate_eligible_count" not in payload or "candidates" not in payload:
        fail(name, f"--backtest output missing expected keys, got: {list(payload.keys())}")
        return
    if payload["total_candidates"] < 1:
        fail(name, "expected the fixture workflow's remediation_history reason to surface as a candidate")
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX (1a-SCOPE: code-reviewer CRITICAL + silent-failure-hunter 3 CRITICAL
# + 3 HIGH on craftflow_skill_ledger.py)
# ---------------------------------------------------------------------------

def test_ledger_lru_eviction_exempts_rejected_and_promoted_at_200_cap(tmp_dir: Path) -> None:
    name = "skill-ledger/lru-eviction-exempts-rejected-and-promoted-at-200-cap"
    ledger = {"schema_version": 1, "candidates": []}
    # 3 rejected + 3 promoted tombstones, all with a much OLDER last_seen
    # than every candidate entry below -- a naive last_seen-only sort (the
    # pre-fix behavior) would target these FIRST for eviction.
    for i in range(3):
        ledger["candidates"].append({
            "id": f"rej{i:04d}", "surface": "unscoped", "signature": f"rejected-sig-{i}",
            "workflows": [f"wf-rej-{i}"], "distinct_workflows": 1, "max_severity": "unknown",
            "evidence": [], "first_seen": "2000-01-01T00:00:00Z", "last_seen": "2000-01-01T00:00:00Z",
            "status": "rejected", "promoted_skill": None, "rejected_reason": "already documented",
            "rejected_at_distinct_workflows": 1,
        })
    for i in range(3):
        ledger["candidates"].append({
            "id": f"prom{i:04d}", "surface": "unscoped", "signature": f"promoted-sig-{i}",
            "workflows": [f"wf-prom-{i}"], "distinct_workflows": 1, "max_severity": "unknown",
            "evidence": [], "first_seen": "2000-06-01T00:00:00Z", "last_seen": "2000-06-01T00:00:00Z",
            "status": "promoted", "promoted_skill": f"skill-{i}", "rejected_reason": None,
            "rejected_at_distinct_workflows": None,
        })
    # 194 ordinary candidate entries so the ledger sits exactly at the 200 cap.
    for i in range(194):
        ledger["candidates"].append({
            "id": f"cand{i:04d}", "surface": "unscoped", "signature": f"candidate-sig-{i}",
            "workflows": [f"wf-cand-{i}"], "distinct_workflows": 1, "max_severity": "unknown",
            "evidence": [], "first_seen": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
            "last_seen": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
            "status": "candidate", "promoted_skill": None, "rejected_reason": None,
            "rejected_at_distinct_workflows": None,
        })
    for c in ledger["candidates"]:
        if c["id"] == "cand0000":
            c["last_seen"] = "2025-01-01T00:00:00Z"  # oldest among CANDIDATE-status entries only
    if len(ledger["candidates"]) != 200:
        fail(name, f"test setup issue: expected 200 seed entries, got {len(ledger['candidates'])}")
        return

    signals = skill_ledger.collect_signals(
        {}, [{"event": "remediation_created", "reason": "brand new 201st unique signature", "ts": "2026-08-01T00:00:00Z"}]
    )
    ledger = skill_ledger.upsert_candidates(ledger, "wf-new", signals)

    if len(ledger["candidates"]) != 200:
        fail(name, f"expected ledger capped at 200 entries, got {len(ledger['candidates'])}")
        return
    ids = {c["id"] for c in ledger["candidates"]}
    for i in range(3):
        if f"rej{i:04d}" not in ids:
            fail(name, f"expected rejected tombstone rej{i:04d} to survive the size-cap eviction, but it was destroyed")
            return
        if f"prom{i:04d}" not in ids:
            fail(name, f"expected promoted tombstone prom{i:04d} to survive the size-cap eviction, but it was destroyed")
            return
    if "cand0000" in ids:
        fail(name, "expected the oldest CANDIDATE-status entry (cand0000) to be evicted, but it is still present")
        return
    ok(name)


def test_ledger_observe_rejects_relative_traversal_wf_id(tmp_dir: Path) -> None:
    name = "skill-ledger/observe-rejects-relative-traversal-wf-id"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_dir / ".craftflow" / "state"
    (state_dir / "workflows").mkdir(parents=True)
    # Plant a file OUTSIDE state_dir that a traversal escape would reach.
    secret_dir = tmp_dir / "secret"
    secret_dir.mkdir()
    (secret_dir / "escape-target.json").write_text(
        json.dumps({"workflow_uuid": "should-never-be-read"}), encoding="utf-8"
    )
    traversal_wf_id = "../../secret/escape-target"
    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--observe", traversal_wf_id, "--state-dir", str(state_dir)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode != 1:
        fail(
            name,
            f"expected --observe with a '..'-traversal wf_id to fail closed (exit 1), "
            f"got exit {result.returncode}; stdout={result.stdout[:300]}",
        )
        return
    ok(name)


def test_ledger_observe_rejects_absolute_path_wf_id(tmp_dir: Path) -> None:
    name = "skill-ledger/observe-rejects-absolute-path-wf-id"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_dir / ".craftflow" / "state"
    (state_dir / "workflows").mkdir(parents=True)
    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--observe", "/etc/passwd", "--state-dir", str(state_dir)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode != 1:
        fail(
            name,
            f"expected --observe with an absolute-path wf_id to fail closed (exit 1), "
            f"got exit {result.returncode}; stdout={result.stdout[:300]}",
        )
        return
    ok(name)


def test_ledger_observe_tolerates_non_utf8_events_file(tmp_dir: Path) -> None:
    name = "skill-ledger/observe-tolerates-non-utf8-events-file"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_dir / ".craftflow" / "state"
    workflows_dir = state_dir / "workflows"
    workflows_dir.mkdir(parents=True)
    ledger_path = state_dir / "project" / "skill-candidates.json"
    wf_id = "wf-badbytes-fixture"
    (workflows_dir / f"{wf_id}.json").write_text(json.dumps({
        "results": {"reviewer": None, "hunter": None, "verifier": None},
        "remediation_history": [],
        "memory_notes": [],
    }), encoding="utf-8")
    # Simulate a torn write: one valid JSONL line followed by invalid UTF-8
    # bytes (never a valid encoding under any common codec).
    events_path = workflows_dir / f"{wf_id}.events.jsonl"
    with open(events_path, "wb") as f:
        f.write(b'{"event": "workflow_failed", "reason": "ok line"}\n')
        f.write(b"\xff\xfe\x00 not valid utf-8 torn write\n")

    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--observe", wf_id, "--state-dir", str(state_dir), "--ledger", str(ledger_path)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode != 0:
        fail(
            name,
            f"expected --observe to tolerate a non-UTF-8 events.jsonl and exit 0 with a diagnostic "
            f"(not crash), got exit {result.returncode}; stderr={result.stderr[:500]}",
        )
        return
    ok(name)


def test_ledger_observe_tolerates_malformed_entries_missing_fields(tmp_dir: Path) -> None:
    """HIGH (REM-FIX round 6): `upsert_candidates` used to direct-index
    untrusted loaded ledger JSON (`entry["surface"]`, `entry["signature"]`,
    `entry["workflows"]`, `entry["evidence"]`) without guarding against a
    well-formed DICT that is simply missing expected keys (as opposed to a
    non-dict entry, which was already guarded elsewhere). --observe is the
    entrypoint invoked on EVERY workflow completion per the router's
    memory-finalization wiring -- once one such malformed entry lands in the
    ledger (hand-edited, partially migrated, or a future schema change),
    every future --observe call crashes with a raw KeyError traceback (not
    caught by main()'s OSError/UnicodeDecodeError/AttributeError/TypeError
    wrapper) instead of the documented clean JSON error contract,
    permanently breaking ledger mining. Covers two distinct malformed
    shapes: (1) a dict entry missing BOTH surface/signature entirely, and
    (2) a dict entry that HAS surface/signature (so it participates in
    key-matching) but is missing workflows/evidence (e.g. a partially
    migrated schema)."""
    name = "skill-ledger/observe-tolerates-malformed-entries-missing-fields"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_dir / ".craftflow" / "state"
    workflows_dir = state_dir / "workflows"
    workflows_dir.mkdir(parents=True)
    ledger_path = state_dir / "project" / "skill-candidates.json"

    # Case 1: dict entry entirely missing surface/signature.
    missing_surface_signature_entry = {
        "id": "malformed-no-surface-sig",
        "status": "candidate",
        "workflows": ["wf-old"],
    }
    # Case 2: dict entry WITH surface/signature (so it is looked up and
    # matched against a fresh incoming signal below) but missing
    # workflows/evidence/distinct_workflows/max_severity -- as a partially
    # migrated or hand-edited schema would produce.
    matching_signature_text = "a recurring missing-workflows issue"
    missing_workflows_entry = {
        "id": "malformed-no-workflows",
        "surface": "unscoped",
        "signature": matching_signature_text,
        "status": "candidate",
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps({
        "schema_version": 1,
        "candidates": [missing_surface_signature_entry, missing_workflows_entry],
    }), encoding="utf-8")

    wf_id = "wf-malformed-ledger-fixture"
    (workflows_dir / f"{wf_id}.json").write_text(json.dumps({
        "results": {"reviewer": None, "hunter": None, "verifier": None},
        "remediation_history": [{"reason": matching_signature_text}],
        "memory_notes": [],
    }), encoding="utf-8")
    (workflows_dir / f"{wf_id}.events.jsonl").write_text("", encoding="utf-8")

    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--observe", wf_id, "--state-dir", str(state_dir), "--ledger", str(ledger_path)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode != 0:
        fail(
            name,
            f"expected --observe to degrade cleanly against a malformed ledger (not crash), "
            f"got exit {result.returncode}; stderr={result.stderr[:800]}",
        )
        return
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail(name, f"--observe did not print valid JSON on stdout: {result.stdout[:300]}")
        return
    if "observed" not in payload:
        fail(name, f"expected a clean {{'observed': ...}} JSON success shape, got: {payload}")
        return

    ledger_after = json.loads(ledger_path.read_text(encoding="utf-8"))
    ids_after = {c.get("id") for c in ledger_after["candidates"] if isinstance(c, dict)}
    if "malformed-no-surface-sig" not in ids_after:
        fail(name, "expected the surface/signature-less entry to survive untouched, not be dropped")
        return
    if "malformed-no-workflows" not in ids_after:
        fail(name, "expected the workflows-less entry to survive (repaired), not be dropped")
        return

    repaired = next(c for c in ledger_after["candidates"] if c.get("id") == "malformed-no-workflows")
    if not isinstance(repaired.get("workflows"), list) or wf_id not in repaired["workflows"]:
        fail(name, f"expected the malformed entry's 'workflows' to be repaired and include {wf_id!r}, got: {repaired.get('workflows')!r}")
        return
    if not isinstance(repaired.get("evidence"), list) or not repaired["evidence"]:
        fail(name, f"expected the malformed entry's 'evidence' to be repaired with the new signal, got: {repaired.get('evidence')!r}")
        return
    ok(name)


def test_ledger_reject_sets_status_and_reason(tmp_dir: Path) -> None:
    # Phase 3 (router wiring): the approval flow's "Reject" option needs a
    # deterministic script-level way to tombstone a candidate -- this was a
    # documented gap in Phase 1 (craftflow_skill_ledger.py had --observe,
    # --query, --prune, --backtest but no --reject). Minimal addition, same
    # atomic-write/lock discipline as the other mutating subcommands.
    name = "skill-ledger/reject-sets-status-and-reason"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = tmp_dir / "skill-candidates.json"
    candidate_id = "abc12345"
    ledger_path.write_text(json.dumps({
        "schema_version": 1,
        "candidates": [{
            "id": candidate_id,
            "surface": "unscoped",
            "signature": "some recurring issue",
            "workflows": ["wf-a", "wf-b"],
            "distinct_workflows": 2,
            "max_severity": "medium",
            "evidence": [],
            "first_seen": "2026-01-01T00:00:00Z",
            "last_seen": "2026-01-01T00:00:00Z",
            "status": "candidate",
            "promoted_skill": None,
            "rejected_reason": None,
            "rejected_at_distinct_workflows": None,
        }],
    }), encoding="utf-8")

    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--reject", candidate_id, "--reason", "already documented in patterns.md",
         "--ledger", str(ledger_path)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode != 0:
        fail(name, f"--reject exited {result.returncode}: {result.stderr[:500]}")
        return

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry = ledger["candidates"][0]
    if entry.get("status") != "rejected":
        fail(name, f"expected status='rejected' after --reject, got {entry.get('status')!r}")
        return
    if entry.get("rejected_reason") != "already documented in patterns.md":
        fail(name, f"expected rejected_reason to be persisted verbatim, got {entry.get('rejected_reason')!r}")
        return
    if entry.get("rejected_at_distinct_workflows") != 2:
        fail(name, f"expected rejected_at_distinct_workflows=2 (snapshot at rejection time), got {entry.get('rejected_at_distinct_workflows')!r}")
        return
    ok(name)


def test_ledger_reject_unknown_candidate_id_fails_closed(tmp_dir: Path) -> None:
    name = "skill-ledger/reject-unknown-candidate-id-fails-closed"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = tmp_dir / "skill-candidates.json"
    ledger_path.write_text(json.dumps({"schema_version": 1, "candidates": []}), encoding="utf-8")

    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--reject", "nonexistent-id", "--ledger", str(ledger_path)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode == 0:
        fail(name, "expected non-zero exit when rejecting a candidate id absent from the ledger")
        return
    ok(name)


def test_ledger_reject_already_promoted_candidate_fails_closed(tmp_dir: Path) -> None:
    # HIGH 3 (REM-FIX): a real race exists between the router's gate check and
    # the user's eventual AskUserQuestion answer -- during that window a
    # DIFFERENT concurrent workflow could already have run
    # craftflow_skill_promote.py --approve on the same candidate. --reject
    # must refuse (exit 1, clear JSON error, no silent status overwrite) when
    # the candidate is no longer in a rejectable state, mirroring promote.py's
    # own defensive posture.
    name = "skill-ledger/reject-already-promoted-candidate-fails-closed"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = tmp_dir / "skill-candidates.json"
    candidate_id = "promoted1"
    original = {
        "schema_version": 1,
        "candidates": [{
            "id": candidate_id,
            "surface": "unscoped",
            "signature": "some recurring issue",
            "workflows": ["wf-a", "wf-b"],
            "distinct_workflows": 2,
            "max_severity": "medium",
            "evidence": [],
            "first_seen": "2026-01-01T00:00:00Z",
            "last_seen": "2026-01-01T00:00:00Z",
            "status": "promoted",
            "promoted_skill": "some-skill",
            "rejected_reason": None,
            "rejected_at_distinct_workflows": None,
        }],
    }
    ledger_path.write_text(json.dumps(original), encoding="utf-8")

    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--reject", candidate_id, "--reason", "too late",
         "--ledger", str(ledger_path)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode == 0:
        fail(name, f"expected non-zero exit when rejecting an already-promoted candidate, got exit 0: {result.stdout!r}")
        return
    try:
        payload = json.loads(result.stderr)
    except json.JSONDecodeError:
        fail(name, f"expected a clean JSON error on stderr, got: {result.stderr[:300]!r}")
        return
    if "error" not in payload:
        fail(name, f"expected an 'error' key in the JSON error payload, got: {payload}")
        return

    ledger_after = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry_after = ledger_after["candidates"][0]
    if entry_after.get("status") != "promoted":
        fail(name, f"expected status to remain 'promoted' (no silent overwrite), got {entry_after.get('status')!r}")
        return
    if entry_after.get("promoted_skill") != "some-skill":
        fail(name, "expected promoted_skill to remain untouched after a refused --reject")
        return
    ok(name)


def test_ledger_main_wraps_oserror_as_clean_json_not_traceback(tmp_dir: Path) -> None:
    # MEDIUM (REM-FIX): craftflow_skill_promote.py's main() already wraps
    # unexpected OSError/UnicodeDecodeError into a clean {"error": ...} JSON
    # shape (see its own MEDIUM 6 REM-FIX). craftflow_skill_ledger.py's
    # main() lacked the same top-level wrapping across --reject/--observe/
    # --prune/--query, so an OS-level failure (e.g. a ledger path whose
    # parent component is a regular file, not a directory) crashed with a
    # raw Python traceback instead of this script's documented JSON shape.
    name = "skill-ledger/main-wraps-oserror-as-clean-json-not-traceback"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    blocker_file = tmp_dir / "blocker.txt"
    blocker_file.write_text("not a directory", encoding="utf-8")
    # ledger's parent path component ("blocker.txt") is a FILE, so
    # Path.mkdir(parents=True) on the real parent ("blocker.txt/sub") raises
    # NotADirectoryError (an OSError subclass) inside save_ledger_atomic /
    # the ledger file lock helper.
    bad_ledger_path = blocker_file / "sub" / "skill-candidates.json"

    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--prune", "--ledger", str(bad_ledger_path)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode == 0:
        fail(name, "expected non-zero exit for an OS-level failure writing the ledger")
        return
    if "Traceback (most recent call last)" in result.stderr:
        fail(name, f"expected a clean JSON error, not a raw Python traceback; stderr={result.stderr[:400]!r}")
        return
    try:
        payload = json.loads(result.stderr.strip())
    except json.JSONDecodeError:
        fail(name, f"expected stderr to be a single clean JSON error object, got: {result.stderr[:400]!r}")
        return
    if "error" not in payload:
        fail(name, f"expected an 'error' key in the JSON error payload, got: {payload}")
        return
    ok(name)


def test_ledger_observe_acquires_and_releases_lock(tmp_dir: Path) -> None:
    name = "skill-ledger/observe-acquires-and-releases-file-lock"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_dir / ".craftflow" / "state"
    workflows_dir = state_dir / "workflows"
    workflows_dir.mkdir(parents=True)
    ledger_path = state_dir / "project" / "skill-candidates.json"
    wf_id = "wf-lock-fixture"
    (workflows_dir / f"{wf_id}.json").write_text(json.dumps({
        "results": {"reviewer": None, "hunter": None, "verifier": None},
        "remediation_history": [],
        "memory_notes": [],
    }), encoding="utf-8")

    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--observe", wf_id, "--state-dir", str(state_dir), "--ledger", str(ledger_path)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode != 0:
        fail(name, f"--observe exited {result.returncode}: {result.stderr[:500]}")
        return

    lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
    if not lock_path.exists():
        fail(name, f"expected a lock file at {lock_path} to exist after cmd_observe ran under a lock")
        return

    # If the lock was genuinely released (not leaked/held), a fresh
    # non-blocking exclusive flock on the same lock file must succeed right now.
    fd = os.open(str(lock_path), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError as exc:
        fail(name, f"lock file appears still held after cmd_observe returned: {exc}")
        return
    finally:
        os.close(fd)
    ok(name)


def test_ledger_observe_identity_pinned_to_wf_id_not_workflow_uuid(tmp_dir: Path) -> None:
    name = "skill-ledger/observe-identity-pinned-to-wf-id-not-workflow-uuid"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_dir / ".craftflow" / "state"
    workflows_dir = state_dir / "workflows"
    workflows_dir.mkdir(parents=True)
    ledger_path = state_dir / "project" / "skill-candidates.json"

    wf_id = "wf-identity-fixture"
    artifact_path = workflows_dir / f"{wf_id}.json"
    (workflows_dir / f"{wf_id}.events.jsonl").write_text("", encoding="utf-8")

    script = SCRIPTS / "craftflow_skill_ledger.py"

    # First observation: workflow_uuid absent on the artifact (identity
    # would previously fall back to the raw wf_id).
    artifact_v1 = {
        "results": {"reviewer": None, "hunter": None, "verifier": None},
        "remediation_history": [{"reason": "identity pinning regression fixture reason"}],
        "memory_notes": [],
    }
    artifact_path.write_text(json.dumps(artifact_v1), encoding="utf-8")
    result1 = subprocess.run(
        [sys.executable, str(script), "--observe", wf_id, "--state-dir", str(state_dir), "--ledger", str(ledger_path)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result1.returncode != 0:
        fail(name, f"first --observe exited {result1.returncode}: {result1.stderr[:500]}")
        return

    # Second observation of the SAME physical workflow: workflow_uuid now
    # present (identity would previously become this self-reported uuid --
    # a DIFFERENT string from wf_id -- inflating distinct_workflows to 2).
    artifact_v2 = dict(artifact_v1)
    artifact_v2["workflow_uuid"] = "some-later-self-reported-uuid"
    artifact_path.write_text(json.dumps(artifact_v2), encoding="utf-8")
    result2 = subprocess.run(
        [sys.executable, str(script), "--observe", wf_id, "--state-dir", str(state_dir), "--ledger", str(ledger_path)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result2.returncode != 0:
        fail(name, f"second --observe exited {result2.returncode}: {result2.stderr[:500]}")
        return

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    signature = skill_ledger.learn_scan.normalize_reason("identity pinning regression fixture reason")
    matches = [c for c in ledger["candidates"] if c["signature"] == signature]
    if len(matches) != 1:
        fail(name, f"expected exactly 1 candidate cluster, got {len(matches)}")
        return
    if matches[0]["distinct_workflows"] != 1:
        fail(
            name,
            f"expected distinct_workflows to stay 1 across both --observe calls of the SAME "
            f"physical workflow (before/after workflow_uuid appears), got "
            f"{matches[0]['distinct_workflows']} -- workflows={matches[0]['workflows']}",
        )
        return
    ok(name)


def test_ledger_observe_repeat_calls_do_not_duplicate_evidence(tmp_dir: Path) -> None:
    name = "skill-ledger/observe-repeat-calls-do-not-duplicate-evidence"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_dir / ".craftflow" / "state"
    workflows_dir = state_dir / "workflows"
    workflows_dir.mkdir(parents=True)
    ledger_path = state_dir / "project" / "skill-candidates.json"
    wf_id = "wf-repeat-fixture"
    artifact = {
        "results": {"reviewer": None, "hunter": None, "verifier": None},
        "remediation_history": [{"reason": "repeat observe evidence dedup fixture reason"}],
        "memory_notes": [],
    }
    (workflows_dir / f"{wf_id}.json").write_text(json.dumps(artifact), encoding="utf-8")
    (workflows_dir / f"{wf_id}.events.jsonl").write_text("", encoding="utf-8")

    script = SCRIPTS / "craftflow_skill_ledger.py"
    for _ in range(3):
        result = subprocess.run(
            [sys.executable, str(script), "--observe", wf_id, "--state-dir", str(state_dir), "--ledger", str(ledger_path)],
            capture_output=True, text=True, cwd=str(tmp_dir),
        )
        if result.returncode != 0:
            fail(name, f"--observe exited {result.returncode}: {result.stderr[:500]}")
            return

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    signature = skill_ledger.learn_scan.normalize_reason("repeat observe evidence dedup fixture reason")
    matches = [c for c in ledger["candidates"] if c["signature"] == signature]
    if len(matches) != 1:
        fail(name, f"expected 1 candidate cluster, got {len(matches)}")
        return
    evidence = matches[0]["evidence"]
    if len(evidence) != 1:
        fail(
            name,
            f"expected evidence list to stay at 1 row after 3 repeat --observe calls of the "
            f"same workflow, got {len(evidence)}: {evidence}",
        )
        return
    ok(name)


# ---------------------------------------------------------------------------
# Skill-candidate ledger: severity-extraction calibration (REM-FIX round 2)
# ---------------------------------------------------------------------------
# REM-FIX (found via integration-verifier backtest against 141 real workflow
# logs in the MAIN state tree): 139/200 real candidates normalized to
# max_severity="unknown". Root cause, confirmed by reading real
# results.reviewer / planning_review_findings / remediation_history text in
# this repo's own history:
#   1. This project's actual review taxonomy is not just critical/high/
#      medium/low -- planning_review_findings[].severity also uses
#      "BLOCKING" and "ADVISORY" (and free text uses "MINOR"), none of which
#      the old word list recognized at all.
#   2. The old zero-count guard only excluded a SUFFIX zero
#      ("critical:0"/"critical=0"), not the far more common PREFIX zero
#      seen in real reviewer/verifier notes ("0 critical issues", "0
#      CRITICAL, 0 HIGH") -- these were being misread as an actual CRITICAL
#      finding, the exact inverse of their meaning (a clean/PASS result).
#   3. Multi-severity free text ("2 CRITICAL + 3 HIGH", "0 critical, 3
#      medium non-blocking") needs the HIGHEST-ranked word with a nonzero
#      count, not just the first word matched in string order.
#   4. Real remediation_history text also concatenates the word into a
#      camelCase metric name with a "=" count ("totalCritical=5 >=1 AND
#      totalHigh=2 >=1") -- a bare \b-bounded word regex never matches
#      inside "totalCritical".
# All fixture strings below are copied verbatim (or near-verbatim) from
# real artifacts under .craftflow/state/workflows/*.json in this repo's
# history -- not invented shapes.

def test_ledger_severity_recognizes_project_specific_words(tmp_dir: Path) -> None:
    name = "skill-ledger/severity-recognizes-blocking-advisory-minor"
    # Real shape: planning_review_findings[].severity in wf-20260723-101837-24eef849.json
    cases = [("BLOCKING", "critical"), ("ADVISORY", "low"), ("MINOR", "low")]
    for raw, expected in cases:
        got = skill_ledger.normalize_severity(raw)
        if got != expected:
            fail(name, f"expected normalize_severity({raw!r}) == {expected!r}, got {got!r}")
            return
    ok(name)


def test_ledger_severity_prefix_zero_count_is_not_that_severity(tmp_dir: Path) -> None:
    name = "skill-ledger/severity-prefix-zero-count-is-not-that-severity"
    # Real shapes: results.reviewer / results.verifier note text in
    # wf-20260717-185354-9631865c.json and wf-20260508-100000-f1e2d3c4.json.
    cases = [
        "9/9 scenarios PASS, 0 critical issues — final phase-exit verification",
        "CLEAN after REM-FIX — 0 CRITICAL, 0 HIGH. Fixed: instanceof guard×2, promise memoization",
    ]
    for text in cases:
        got = skill_ledger.normalize_severity(text)
        if got == "critical":
            fail(name, f"expected a '0 critical'/'0 CRITICAL' clean-pass note to NOT normalize to critical, got {got!r} for {text!r}")
            return
    ok(name)


def test_ledger_severity_picks_highest_nonzero_severity_mentioned(tmp_dir: Path) -> None:
    name = "skill-ledger/severity-picks-highest-nonzero-severity-mentioned"
    # Real shape: remediation_history reason text in wf-20260717-203457-81fca684.json
    got = skill_ledger.normalize_severity("user chose 'Fix all issues' (2 CRITICAL + 2 HIGH)")
    if got != "critical":
        fail(name, f"expected '2 CRITICAL + 2 HIGH' to normalize to critical (highest nonzero), got {got!r}")
        return
    # Real shape: remediation_history reason text in wf-20260429-100003-a7b8c9d0.json
    got2 = skill_ledger.normalize_severity(
        "phase-e: 1 HIGH (trailer missing from HOP_BY_HOP), 3 MEDIUM (process.once, viteProcess orphan, silent swallow), 1 LOW"
    )
    if got2 != "high":
        fail(name, f"expected the phase-e note to normalize to high (highest nonzero of HIGH/MEDIUM/LOW), got {got2!r}")
        return
    ok(name)


def test_ledger_severity_zero_count_does_not_mask_other_nonzero_mention(tmp_dir: Path) -> None:
    name = "skill-ledger/severity-zero-count-does-not-mask-other-nonzero-mention"
    # Real shape: remediation_history reason text in wf-20260508-090652-efd1a414.json
    got = skill_ledger.normalize_severity("Phase B re-review: 0 critical, 3 medium non-blocking")
    if got != "medium":
        fail(name, f"expected '0 critical, 3 medium non-blocking' to normalize to medium (0-critical excluded, 3-medium kept), got {got!r}")
        return
    ok(name)


def test_ledger_severity_recognizes_camelcase_concatenated_word_with_count(tmp_dir: Path) -> None:
    name = "skill-ledger/severity-recognizes-camelcase-concatenated-word-with-count"
    # Real shape: remediation_history trigger text in this repo's own
    # 1a-SCOPE decision logging ("totalCritical=5 >=1 AND totalHigh=2 >=1").
    got = skill_ledger.normalize_severity(
        "scope_decision trigger: 1a-SCOPE rule fired: totalCritical=5 >=1 AND totalHigh=2 >=1 on Phase 1 parallel review"
    )
    if got != "critical":
        fail(name, f"expected camelCase 'totalCritical=5' to normalize to critical, got {got!r}")
        return
    ok(name)


def test_ledger_severity_word_followed_by_zero_count_still_not_that_severity_regression(tmp_dir: Path) -> None:
    """Regression guard for the pre-existing sl0b fixture: must still hold
    after widening the severity word list and rewriting the count logic."""
    name = "skill-ledger/severity-suffix-zero-count-regression-guard"
    got = skill_ledger.normalize_severity("critical=0 so 1a-SCOPE auto-proceeds without user checkpoint")
    if got == "critical":
        fail(name, f"expected 'critical=0 so ...' to still NOT normalize to critical after the rewrite, got {got!r}")
        return
    ok(name)


def test_ledger_gate_eligible_two_distinct_workflows_any_severity(tmp_dir: Path) -> None:
    """Calibrated threshold (REM-FIX round 3, real 141-workflow backtest):
    distinct_workflows>=2 is now sufficient for gate eligibility regardless
    of severity. The old >=3 tier and the >=2-plus-critical escalation are
    both dropped: 196/200 mined candidates on the real corpus were
    singletons (distinct_workflows=1), a structural corpus property that
    made the >=3 threshold almost never fire."""
    name = "skill-ledger/gate-eligible-two-distinct-workflows-any-severity"
    candidate = {"distinct_workflows": 2, "max_severity": "unknown"}
    if not skill_ledger.gate_eligible(candidate):
        fail(name, "expected distinct_workflows=2, severity=unknown to be gate-eligible under the new threshold")
        return
    ok(name)


def test_ledger_gate_eligible_one_distinct_workflow_still_not_eligible(tmp_dir: Path) -> None:
    """distinct_workflows=1 must remain NOT gate-eligible even at the
    highest severity -- the new threshold only lowers the bar from 3 to 2
    distinct workflows, it does not eliminate the recurrence requirement."""
    name = "skill-ledger/gate-eligible-one-distinct-workflow-still-not-eligible"
    candidate = {"distinct_workflows": 1, "max_severity": "critical"}
    if skill_ledger.gate_eligible(candidate):
        fail(name, "expected distinct_workflows=1 to remain NOT gate-eligible even at critical severity")
        return
    ok(name)


# ---------------------------------------------------------------------------
# skill-promote tests (Phase 2: craftflow_skill_promote.py)
# ---------------------------------------------------------------------------

_VALID_SKILL_MD = """---
name: {name}
description: "Use when this exact lesson recurs again. Provides a documented, evidence-backed fix pattern for it."
allowed-tools: Read Grep Glob Bash
craftflow-candidate-id: {cid}
craftflow-evidence-workflows: wf-a, wf-b
craftflow-referenced-paths: path/a.ts
craftflow-promoted-at: PENDING_APPROVAL
craftflow-review-after: PENDING_APPROVAL
---

# {name}

## Verified Commands

- `grep -n "example" path/a.ts` -> exit 0: shows the pattern at line 12
"""


def _seed_ledger_candidate(ledger_path: Path, candidate_id: str, status: str = "candidate") -> None:
    """Write a minimal single-candidate ledger to `ledger_path` with the
    given status. ROUND 3 (REM-FIX): cmd_approve now ALWAYS requires the
    candidate to be present in whatever `--ledger` points at (no more
    best-effort bypass for a nonexistent ledger file), so every test that
    exercises a successful --approve must seed one of these first."""
    ledger_path.write_text(json.dumps({
        "schema_version": 1,
        "candidates": [{
            "id": candidate_id, "surface": "unscoped", "signature": "some recurring lesson",
            "workflows": ["wf-a", "wf-b"], "distinct_workflows": 2, "max_severity": "unknown",
            "evidence": [], "first_seen": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:00Z",
            "status": status, "promoted_skill": None, "rejected_reason": None,
            "rejected_at_distinct_workflows": None,
        }],
    }), encoding="utf-8")


def _run_promote(approve: str, proposals_dir: Path, project_root: Path, ledger: Path) -> tuple:
    """Invoke craftflow_skill_promote.cmd_approve in-process, capturing
    stdout/stderr. Returns (exit_code, stdout_text, stderr_text)."""
    ns = argparse.Namespace(
        approve=approve,
        proposals_dir=str(proposals_dir),
        project_root=str(project_root),
        ledger=str(ledger),
    )
    out, err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = out, err
        code = skill_promote.cmd_approve(ns)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return code, out.getvalue(), err.getvalue()


def test_promote_refuses_short_description(tmp_dir: Path) -> None:
    name = "skill-promote/refuses-short-description"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    cand_dir = proposals_dir / "cand1"
    cand_dir.mkdir(parents=True)
    bad_content = _VALID_SKILL_MD.format(name="foo-skill", cid="cand1").replace(
        'description: "Use when this exact lesson recurs again. Provides a documented, evidence-backed fix pattern for it."',
        'description: "too short"',
    )
    (cand_dir / "SKILL.md").write_text(bad_content, encoding="utf-8")
    code, _out, err = _run_promote("cand1", proposals_dir, project_root, tmp_dir / "ledger.json")
    if code == 0:
        fail(name, "expected exit 1 for a description under 40 characters")
        return
    if "description" not in err:
        fail(name, f"expected stderr to explain the description-length failure, got: {err}")
        return
    if (project_root / ".claude" / "skills").exists():
        fail(name, "expected NO canonical write when frontmatter validation fails")
        return
    ok(name)


def test_promote_refuses_missing_name(tmp_dir: Path) -> None:
    name = "skill-promote/refuses-missing-name"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    cand_dir = proposals_dir / "cand2"
    cand_dir.mkdir(parents=True)
    content = "---\ndescription: \"Use when this recurs again. Provides a documented fix pattern for it.\"\n---\n\n# missing name\n"
    (cand_dir / "SKILL.md").write_text(content, encoding="utf-8")
    code, _out, err = _run_promote("cand2", proposals_dir, project_root, tmp_dir / "ledger.json")
    if code == 0:
        fail(name, "expected exit 1 when frontmatter has no 'name' field")
        return
    if "name" not in err:
        fail(name, f"expected stderr to explain the missing-name failure, got: {err}")
        return
    ok(name)


def test_promote_refuses_both_skill_md_and_patch_present(tmp_dir: Path) -> None:
    name = "skill-promote/refuses-both-skill-md-and-patch-present"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    cand_dir = proposals_dir / "cand3"
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand3"), encoding="utf-8")
    (cand_dir / "SKILL.patch").write_text("--- a/x\n+++ b/x\n", encoding="utf-8")
    code, _out, err = _run_promote("cand3", proposals_dir, project_root, tmp_dir / "ledger.json")
    if code == 0:
        fail(name, "expected exit 1 when both SKILL.md and SKILL.patch are present")
        return
    if "both" not in err.lower():
        fail(name, f"expected stderr to name the both-present conflict, got: {err}")
        return
    ok(name)


def test_promote_refuses_neither_present(tmp_dir: Path) -> None:
    name = "skill-promote/refuses-neither-present"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    cand_dir = proposals_dir / "cand4"
    cand_dir.mkdir(parents=True)
    (cand_dir / "PROPOSAL.md").write_text("rationale only, no skill file", encoding="utf-8")
    code, _out, err = _run_promote("cand4", proposals_dir, project_root, tmp_dir / "ledger.json")
    if code == 0:
        fail(name, "expected exit 1 when neither SKILL.md nor SKILL.patch are present")
        return
    if "neither" not in err.lower():
        fail(name, f"expected stderr to name the neither-present gap, got: {err}")
        return
    ok(name)


def test_promote_refuses_path_traversal_candidate_id(tmp_dir: Path) -> None:
    name = "skill-promote/refuses-path-traversal-candidate-id"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    code, _out, err = _run_promote("../../etc", proposals_dir, project_root, tmp_dir / "ledger.json")
    if code == 0:
        fail(name, "expected exit 1 for a path-traversal candidate id")
        return
    if "unsafe" not in err.lower() and "invalid" not in err.lower():
        fail(name, f"expected stderr to flag the candidate id as unsafe/invalid, got: {err}")
        return
    ok(name)


def test_promote_refuses_unsafe_skill_name(tmp_dir: Path) -> None:
    name = "skill-promote/refuses-unsafe-skill-name"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    cand_dir = proposals_dir / "cand5"
    cand_dir.mkdir(parents=True)
    content = _VALID_SKILL_MD.format(name="foo-skill", cid="cand5").replace(
        "name: foo-skill", "name: ../../escape"
    )
    (cand_dir / "SKILL.md").write_text(content, encoding="utf-8")
    code, _out, err = _run_promote("cand5", proposals_dir, project_root, tmp_dir / "ledger.json")
    if code == 0:
        fail(name, "expected exit 1 when frontmatter 'name' is not a filesystem-safe token")
        return
    if (project_root / ".claude").exists():
        fail(name, "expected NO write anywhere when the skill name is path-traversal-shaped")
        return
    ok(name)


def test_promote_writes_canonical_and_syncs_cursor_symlink(tmp_dir: Path) -> None:
    name = "skill-promote/writes-canonical-and-syncs-cursor-symlink"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    cand_dir = proposals_dir / "cand6"
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand6"), encoding="utf-8")
    (cand_dir / "PROPOSAL.md").write_text("rationale", encoding="utf-8")
    ledger_path = tmp_dir / "ledger.json"
    _seed_ledger_candidate(ledger_path, "cand6")
    code, out, err = _run_promote("cand6", proposals_dir, project_root, ledger_path)
    if code != 0:
        fail(name, f"expected exit 0 for a valid proposal, got {code}, stderr={err}")
        return
    canonical = project_root / ".claude" / "skills" / "foo-skill" / "SKILL.md"
    if not canonical.is_file():
        fail(name, f"expected canonical file at {canonical}")
        return
    written = canonical.read_text(encoding="utf-8")
    if "PENDING_APPROVAL" in written:
        fail(name, "expected PENDING_APPROVAL placeholders to be replaced with real ISO timestamps")
        return
    if "craftflow-promoted-at: 20" not in written:
        fail(name, f"expected a real ISO craftflow-promoted-at timestamp in written content:\n{written}")
        return
    link = project_root / ".cursor" / "skills" / "foo-skill"
    if not link.is_symlink():
        fail(name, f"expected {link} to be a symlink into the canonical .claude/skills directory")
        return
    if link.resolve() != canonical.parent.resolve():
        fail(name, f"expected {link} to resolve to {canonical.parent}, got {link.resolve()}")
        return
    result = json.loads(out)
    if result.get("cursor_sync") != "symlinked":
        fail(name, f"expected cursor_sync='symlinked', got {result.get('cursor_sync')!r}")
        return
    ok(name)


def test_promote_stale_backup_on_conflicting_cursor_entry(tmp_dir: Path) -> None:
    name = "skill-promote/stale-backup-on-conflicting-cursor-entry"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    cand_dir = proposals_dir / "cand7"
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand7"), encoding="utf-8")
    conflicting = project_root / ".cursor" / "skills" / "foo-skill"
    conflicting.mkdir(parents=True)
    (conflicting / "stale.txt").write_text("pre-existing unrelated content", encoding="utf-8")
    ledger_path = tmp_dir / "ledger.json"
    _seed_ledger_candidate(ledger_path, "cand7")
    code, out, err = _run_promote("cand7", proposals_dir, project_root, ledger_path)
    if code != 0:
        fail(name, f"expected exit 0, got {code}, stderr={err}")
        return
    backups = list((project_root / ".cursor" / "skills").glob("foo-skill.stale-backup-*"))
    if not backups:
        fail(name, "expected the pre-existing conflicting directory to be renamed to a .stale-backup-<ts> path")
        return
    if not (backups[0] / "stale.txt").is_file():
        fail(name, "expected the backed-up directory to retain its original content")
        return
    link = project_root / ".cursor" / "skills" / "foo-skill"
    if not link.is_symlink():
        fail(name, "expected a fresh symlink at the original path after backing up the conflict")
        return
    ok(name)


def test_promote_dereference_fallback_when_symlink_unavailable(tmp_dir: Path) -> None:
    name = "skill-promote/dereference-fallback-when-symlink-unavailable"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    cand_dir = proposals_dir / "cand8"
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand8"), encoding="utf-8")
    ledger_path = tmp_dir / "ledger.json"
    _seed_ledger_candidate(ledger_path, "cand8")

    real_symlink = os.symlink

    def boom(*a, **kw):
        raise OSError("simulated: symlinks not supported on this filesystem")

    os.symlink = boom
    try:
        code, out, err = _run_promote("cand8", proposals_dir, project_root, ledger_path)
    finally:
        os.symlink = real_symlink
    if code != 0:
        fail(name, f"expected exit 0 even when symlink creation fails, got {code}, stderr={err}")
        return
    link = project_root / ".cursor" / "skills" / "foo-skill"
    if link.is_symlink():
        fail(name, "expected a real (dereferenced) copy, not a symlink, when os.symlink raises")
        return
    copied = link / "SKILL.md"
    if not copied.is_file():
        fail(name, f"expected a dereferenced copy of SKILL.md at {copied}")
        return
    result = json.loads(out)
    if result.get("cursor_sync") != "copied-fallback":
        fail(name, f"expected cursor_sync='copied-fallback', got {result.get('cursor_sync')!r}")
        return
    ok(name)


def test_promote_idempotent_when_already_correctly_linked(tmp_dir: Path) -> None:
    """ROUND 3 (REM-FIX) note: a second FULL `--approve` of the SAME
    candidate is now correctly refused once the ledger marks it 'promoted'
    (see test_promote_refuses_already_rejected_candidate's sibling coverage
    for the general status-precondition case) -- a promoted candidate can
    never legitimately be re-approved through the router flow. What this
    test actually verifies -- that `sync_cursor_skill` itself is idempotent
    against an already-correct symlink (no spurious stale-backup) -- is
    still a real, reachable code path (e.g. a crash/partial run leaving the
    symlink correctly in place before the ledger got marked), so it is
    exercised directly at the function level rather than via a second
    (now-refused) full `--approve` call."""
    name = "skill-promote/idempotent-when-already-correctly-linked"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    cand_dir = proposals_dir / "cand9"
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand9"), encoding="utf-8")
    ledger_path = tmp_dir / "ledger.json"
    _seed_ledger_candidate(ledger_path, "cand9")
    code1, _out1, err1 = _run_promote("cand9", proposals_dir, project_root, ledger_path)
    if code1 != 0:
        fail(name, f"expected first promote to succeed, got {code1}, stderr={err1}")
        return

    # A second full --approve of the SAME (now-promoted) candidate must be
    # refused -- the ledger status precondition applies here too.
    code2, _out2, err2 = _run_promote("cand9", proposals_dir, project_root, ledger_path)
    if code2 == 0:
        fail(name, "expected a second --approve of an already-promoted candidate to be refused, got exit 0")
        return

    # sync_cursor_skill itself must still be idempotent against an
    # already-correct symlink, independent of the ledger gate above.
    canonical_dir = project_root / ".claude" / "skills" / "foo-skill"
    link_path = project_root / ".cursor" / "skills" / "foo-skill"
    sync_result = skill_promote.sync_cursor_skill(canonical_dir, link_path)
    if sync_result != "already-linked":
        fail(name, f"expected sync_cursor_skill to recognize the link is already correct, got {sync_result!r}")
        return
    backups = list((project_root / ".cursor" / "skills").glob("foo-skill.stale-backup-*"))
    if backups:
        fail(name, f"expected NO stale-backup on a re-sync of an already-correct symlink, found: {backups}")
        return
    ok(name)


def test_promote_marks_ledger_entry_promoted(tmp_dir: Path) -> None:
    name = "skill-promote/marks-ledger-entry-promoted"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = tmp_dir / "ledger.json"
    cand_dir = proposals_dir / "cand10"
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand10"), encoding="utf-8")
    seed_ledger = {
        "schema_version": 1,
        "candidates": [{
            "id": "cand10", "surface": "unscoped", "signature": "some recurring lesson",
            "workflows": ["wf-a", "wf-b"], "distinct_workflows": 2, "max_severity": "unknown",
            "evidence": [], "first_seen": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:00Z",
            "status": "candidate", "promoted_skill": None, "rejected_reason": None,
            "rejected_at_distinct_workflows": None,
        }],
    }
    ledger_path.write_text(json.dumps(seed_ledger), encoding="utf-8")
    code, _out, err = _run_promote("cand10", proposals_dir, project_root, ledger_path)
    if code != 0:
        fail(name, f"expected exit 0, got {code}, stderr={err}")
        return
    after = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry = next((c for c in after["candidates"] if c["id"] == "cand10"), None)
    if entry is None:
        fail(name, "candidate entry disappeared from the ledger after promotion")
        return
    if entry.get("status") != "promoted":
        fail(name, f"expected ledger entry status='promoted', got {entry.get('status')!r}")
        return
    if entry.get("promoted_skill") != "foo-skill":
        fail(name, f"expected promoted_skill='foo-skill', got {entry.get('promoted_skill')!r}")
        return
    ok(name)


def test_promote_applies_update_patch_to_existing_skill(tmp_dir: Path) -> None:
    name = "skill-promote/applies-update-patch-to-existing-skill"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    existing_rel = ".claude/skills/existing-skill/SKILL.md"
    existing_path = project_root / existing_rel
    existing_path.parent.mkdir(parents=True)
    original_content = _VALID_SKILL_MD.format(name="existing-skill", cid="orig").replace(
        "craftflow-promoted-at: PENDING_APPROVAL", "craftflow-promoted-at: 2026-01-01T00:00:00Z"
    ).replace(
        "craftflow-review-after: PENDING_APPROVAL", "craftflow-review-after: 2026-04-01T00:00:00Z"
    )
    existing_path.write_text(original_content, encoding="utf-8")

    updated_content = original_content.replace(
        'description: "Use when this exact lesson recurs again. Provides a documented, evidence-backed fix pattern for it."',
        'description: "Use when this exact lesson recurs again. Provides an UPDATED, evidence-backed fix pattern for it."',
    )

    cand_dir = proposals_dir / "cand11"
    cand_dir.mkdir(parents=True)
    import difflib
    diff_lines = list(difflib.unified_diff(
        original_content.splitlines(keepends=True),
        updated_content.splitlines(keepends=True),
        fromfile=f"a/{existing_rel}",
        tofile=f"b/{existing_rel}",
    ))
    (cand_dir / "SKILL.patch").write_text("".join(diff_lines), encoding="utf-8")

    ledger_path = tmp_dir / "ledger.json"
    _seed_ledger_candidate(ledger_path, "cand11")
    code, out, err = _run_promote("cand11", proposals_dir, project_root, ledger_path)
    if code != 0:
        fail(name, f"expected exit 0 applying a clean update patch, got {code}, stderr={err}")
        return
    after = existing_path.read_text(encoding="utf-8")
    if "UPDATED" not in after:
        fail(name, f"expected the patched canonical file to contain the updated description, got:\n{after}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# skill-promote REM-FIX regression tests (items 1-4, 6 of the Phase 2
# remediation pass). Each test targets exactly one reported gap.
# ---------------------------------------------------------------------------

def test_promote_critical1_rejects_mismatched_patch_target_and_name(tmp_dir: Path) -> None:
    """CRITICAL 1: the update-patch path must abort (no write anywhere) when
    the patched frontmatter's own 'name' field diverges from the patch's
    '+++' target header -- writing would otherwise silently land on a
    DIFFERENT file than the one the patch was validated against."""
    name = "skill-promote/critical1-rejects-mismatched-target-and-name"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    existing_rel = ".claude/skills/existing-skill/SKILL.md"
    existing_path = project_root / existing_rel
    existing_path.parent.mkdir(parents=True)
    original_content = _VALID_SKILL_MD.format(name="existing-skill", cid="orig-mismatch").replace(
        "craftflow-promoted-at: PENDING_APPROVAL", "craftflow-promoted-at: 2026-01-01T00:00:00Z"
    ).replace(
        "craftflow-review-after: PENDING_APPROVAL", "craftflow-review-after: 2026-04-01T00:00:00Z"
    )
    existing_path.write_text(original_content, encoding="utf-8")

    # Patch's '+++' header still targets existing-skill's own file, but the
    # patched frontmatter's 'name:' field is changed to a DIFFERENT skill
    # name -- exactly the divergence CRITICAL 1 must catch before any write.
    diverged_content = original_content.replace("name: existing-skill", "name: divergent-skill")

    cand_dir = proposals_dir / "cand-mismatch"
    cand_dir.mkdir(parents=True)
    import difflib
    diff_lines = list(difflib.unified_diff(
        original_content.splitlines(keepends=True),
        diverged_content.splitlines(keepends=True),
        fromfile=f"a/{existing_rel}",
        tofile=f"b/{existing_rel}",
    ))
    (cand_dir / "SKILL.patch").write_text("".join(diff_lines), encoding="utf-8")

    code, _out, err = _run_promote("cand-mismatch", proposals_dir, project_root, tmp_dir / "ledger.json")
    if code == 0:
        fail(name, "expected exit 1 when the patched frontmatter name diverges from the patch's own target header")
        return
    if "does not match" not in err and "canonical" not in err.lower():
        fail(name, f"expected stderr to explain the target/name mismatch, got: {err}")
        return
    if (project_root / ".claude" / "skills" / "divergent-skill").exists():
        fail(name, "expected NO write to the divergent name's canonical location")
        return
    after = existing_path.read_text(encoding="utf-8")
    if after != original_content:
        fail(name, "expected the original existing-skill file to remain completely untouched when the mismatch is rejected")
        return
    ok(name)


def test_promote_high2_rejects_nonexistent_project_root(tmp_dir: Path) -> None:
    """HIGH 2: --project-root must be validated as an existing directory
    BEFORE any write; a nonexistent path must never be silently mkdir -p'd."""
    name = "skill-promote/high2-rejects-nonexistent-project-root"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "does-not-exist-project"
    cand_dir = proposals_dir / "cand-noroot"
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand-noroot"), encoding="utf-8")
    code, _out, err = _run_promote("cand-noroot", proposals_dir, project_root, tmp_dir / "ledger.json")
    if code == 0:
        fail(name, "expected exit 1 when --project-root does not exist")
        return
    if "does not exist" not in err and "not a directory" not in err:
        fail(name, f"expected stderr to explain the missing/invalid project root, got: {err}")
        return
    if project_root.exists():
        fail(name, "expected NO directory to be silently created at a nonexistent --project-root")
        return
    ok(name)


def test_promote_high4_rejects_patch_target_outside_skills_dir(tmp_dir: Path) -> None:
    """HIGH 4: a SKILL.patch update must only ever be allowed to target an
    already-promoted skill under .claude/skills/ or .cursor/skills/ -- an
    arbitrary project file must never be reachable via this path, even if
    its content happens to carry valid-shaped frontmatter."""
    name = "skill-promote/high4-rejects-patch-target-outside-skills-dir"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    outside_rel = "docs/some-notes.md"
    outside_path = project_root / outside_rel
    outside_path.parent.mkdir(parents=True)
    original_content = _VALID_SKILL_MD.format(name="foo-skill", cid="orig-outside")
    outside_path.write_text(original_content, encoding="utf-8")

    updated_content = original_content.replace(
        'description: "Use when this exact lesson recurs again. Provides a documented, evidence-backed fix pattern for it."',
        'description: "Use when this exact lesson recurs again. Provides an ATTACKER-CONTROLLED fix pattern for it."',
    )

    cand_dir = proposals_dir / "cand-outside"
    cand_dir.mkdir(parents=True)
    import difflib
    diff_lines = list(difflib.unified_diff(
        original_content.splitlines(keepends=True),
        updated_content.splitlines(keepends=True),
        fromfile=f"a/{outside_rel}",
        tofile=f"b/{outside_rel}",
    ))
    (cand_dir / "SKILL.patch").write_text("".join(diff_lines), encoding="utf-8")

    code, _out, err = _run_promote("cand-outside", proposals_dir, project_root, tmp_dir / "ledger.json")
    if code == 0:
        fail(name, "expected exit 1 for a patch targeting a file outside .claude/skills or .cursor/skills")
        return
    if "promoted skill location" not in err and "not a promoted skill" not in err.lower():
        fail(name, f"expected stderr to explain the target is not a promoted-skill location, got: {err}")
        return
    after = outside_path.read_text(encoding="utf-8")
    if after != original_content:
        fail(name, "expected the out-of-scope file to remain completely untouched")
        return
    ok(name)


def test_promote_high3_concurrent_approve_no_traceback(tmp_dir: Path) -> None:
    """HIGH 3: concurrent `--approve <same-id>` invocations must never crash
    with an unhandled FileExistsError traceback in sync_cursor_skill's
    symlink->copytree fallback. Launches several REAL subprocess invocations
    against the same candidate at (nearly) the same time to exercise the
    race window.

    ROUND 3 (REM-FIX) note: now that --approve always validates the ledger
    status precondition, the six concurrent invocations are no longer all
    expected to exit 0 -- the ledger-status guard now serializes them, so
    exactly ONE wins the race (transitions candidate -> promoted) and the
    other five are correctly refused (exit 1, clean JSON error). The
    original point of this test -- no unhandled traceback under concurrency
    -- still holds and is asserted for every invocation."""
    name = "skill-promote/high3-concurrent-approve-no-traceback"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = tmp_dir / "ledger.json"
    _seed_ledger_candidate(ledger_path, "cand-race")
    cand_dir = proposals_dir / "cand-race"
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(_VALID_SKILL_MD.format(name="race-skill", cid="cand-race"), encoding="utf-8")

    script = SCRIPTS / "craftflow_skill_promote.py"
    cmd = [
        sys.executable, str(script),
        "--approve", "cand-race",
        "--proposals-dir", str(proposals_dir),
        "--project-root", str(project_root),
        "--ledger", str(ledger_path),
    ]
    procs = [
        subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(6)
    ]
    results = [(p.wait(), p.stdout.read(), p.stderr.read()) for p in procs]

    successes = 0
    for code, _out, err in results:
        if "Traceback" in err:
            fail(name, f"expected no raw Python traceback from a concurrent --approve invocation, got: {err}")
            return
        if code == 0:
            successes += 1
        else:
            try:
                json.loads(err)
            except json.JSONDecodeError:
                fail(name, f"expected a clean JSON error for a losing concurrent invocation, got: {err}")
                return
    if successes != 1:
        fail(name, f"expected exactly ONE of the six concurrent invocations to win (ledger-status gated), got {successes}")
        return

    canonical = project_root / ".claude" / "skills" / "race-skill" / "SKILL.md"
    if not canonical.is_file():
        fail(name, f"expected canonical file to exist at {canonical} after concurrent promotion")
        return
    ok(name)


def test_promote_medium6_project_root_is_file_returns_json_not_traceback(tmp_dir: Path) -> None:
    """MEDIUM 6: --project-root pointing at an existing FILE must degrade to
    this script's own clean JSON error shape, never a raw Python traceback.
    Invoked via the real CLI (subprocess) so main()'s own try/except wrapper
    is genuinely exercised, not just cmd_approve() in-process."""
    name = "skill-promote/medium6-project-root-file-returns-json"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project-root-is-a-file"
    project_root.write_text("not a directory", encoding="utf-8")
    cand_dir = proposals_dir / "cand-fileroot"
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand-fileroot"), encoding="utf-8")

    script = SCRIPTS / "craftflow_skill_promote.py"
    cmd = [
        sys.executable, str(script),
        "--approve", "cand-fileroot",
        "--proposals-dir", str(proposals_dir),
        "--project-root", str(project_root),
        "--ledger", str(tmp_dir / "ledger.json"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        fail(name, "expected exit 1 when --project-root points at an existing file")
        return
    if "Traceback" in result.stderr:
        fail(name, f"expected no raw Python traceback in stderr, got: {result.stderr}")
        return
    try:
        parsed = json.loads(result.stderr.strip().splitlines()[-1])
    except (ValueError, IndexError):
        fail(name, f"expected a clean JSON error on stderr, got: {result.stderr!r}")
        return
    if "error" not in parsed:
        fail(name, f"expected JSON error shape with an 'error' key, got: {parsed}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# skill-promote: REM-FIX round 2 (--approve status-precondition, symmetric to
# --reject's own HIGH 3 guard). A human-rejected candidate re-approved via a
# stale/concurrent workflow must fail closed, not silently flip the ledger
# back to "promoted" and write the canonical skill file anyway.
# ---------------------------------------------------------------------------

def test_promote_refuses_already_rejected_candidate(tmp_dir: Path) -> None:
    """Symmetric to skill-ledger's test_ledger_reject_already_promoted_candidate_fails_closed:
    --approve must refuse (exit 1, no canonical write, no ledger status flip)
    when the ledger already records this candidate as 'rejected' -- e.g. a
    human rejected it, then the SAME staged proposal is approved (a stale
    client, a concurrent workflow, or an operator error)."""
    name = "skill-promote/refuses-already-rejected-candidate"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = tmp_dir / "ledger.json"
    candidate_id = "cand-rejected"
    cand_dir = proposals_dir / candidate_id
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(
        _VALID_SKILL_MD.format(name="test-skill-y", cid=candidate_id), encoding="utf-8"
    )
    seed_ledger = {
        "schema_version": 1,
        "candidates": [{
            "id": candidate_id, "surface": "unscoped", "signature": "some recurring issue",
            "workflows": ["wf-a", "wf-b"], "distinct_workflows": 2, "max_severity": "medium",
            "evidence": [], "first_seen": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:00Z",
            "status": "rejected", "promoted_skill": None,
            "rejected_reason": "already documented in patterns.md",
            "rejected_at_distinct_workflows": 2,
        }],
    }
    ledger_path.write_text(json.dumps(seed_ledger), encoding="utf-8")

    code, _out, err = _run_promote(candidate_id, proposals_dir, project_root, ledger_path)
    if code == 0:
        fail(name, f"expected exit 1 when approving an already-rejected candidate, got exit 0: {_out!r}")
        return
    try:
        payload = json.loads(err)
    except json.JSONDecodeError:
        fail(name, f"expected a clean JSON error on stderr, got: {err[:300]!r}")
        return
    if "error" not in payload:
        fail(name, f"expected an 'error' key in the JSON error payload, got: {payload}")
        return
    if (project_root / ".claude" / "skills" / "test-skill-y").exists():
        fail(name, "expected NO canonical write when the candidate is already rejected")
        return
    if (project_root / ".cursor" / "skills" / "test-skill-y").exists():
        fail(name, "expected NO cursor sync when the candidate is already rejected")
        return

    ledger_after = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry_after = ledger_after["candidates"][0]
    if entry_after.get("status") != "rejected":
        fail(name, f"expected ledger status to remain 'rejected' (no silent flip back to 'promoted'), got {entry_after.get('status')!r}")
        return
    if entry_after.get("rejected_reason") != "already documented in patterns.md":
        fail(name, "expected the original rejected_reason to remain untouched after a refused --approve")
        return
    ok(name)


def test_promote_refuses_candidate_not_in_ledger(tmp_dir: Path) -> None:
    """When a ledger IS in use (the file exists and tracks other candidates)
    but the candidate id being approved is simply absent from it, --approve
    must fail closed rather than silently promoting an untracked candidate
    (previously mark_ledger_promoted returned False and cmd_approve still
    exited 0, still writing the canonical file)."""
    name = "skill-promote/refuses-candidate-not-in-ledger"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = tmp_dir / "ledger.json"
    candidate_id = "cand-untracked"
    cand_dir = proposals_dir / candidate_id
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(
        _VALID_SKILL_MD.format(name="untracked-skill", cid=candidate_id), encoding="utf-8"
    )
    seed_ledger = {
        "schema_version": 1,
        "candidates": [{
            "id": "some-other-candidate", "surface": "unscoped", "signature": "unrelated issue",
            "workflows": ["wf-c"], "distinct_workflows": 2, "max_severity": "medium",
            "evidence": [], "first_seen": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:00Z",
            "status": "candidate", "promoted_skill": None, "rejected_reason": None,
            "rejected_at_distinct_workflows": None,
        }],
    }
    ledger_path.write_text(json.dumps(seed_ledger), encoding="utf-8")

    code, _out, err = _run_promote(candidate_id, proposals_dir, project_root, ledger_path)
    if code == 0:
        fail(name, f"expected exit 1 when the candidate id is absent from an in-use ledger, got exit 0: {_out!r}")
        return
    try:
        payload = json.loads(err)
    except json.JSONDecodeError:
        fail(name, f"expected a clean JSON error on stderr, got: {err[:300]!r}")
        return
    if "error" not in payload:
        fail(name, f"expected an 'error' key in the JSON error payload, got: {payload}")
        return
    if (project_root / ".claude" / "skills" / "untracked-skill").exists():
        fail(name, "expected NO canonical write when the candidate is not tracked in an in-use ledger")
        return
    ok(name)


def test_promote_fails_closed_when_ledger_flag_points_to_never_created_sibling_path(tmp_dir: Path) -> None:
    """ROUND 3 repro: a REAL ledger elsewhere already records this candidate
    as 'rejected', but --approve is invoked with --ledger pointing at a
    SIBLING path (same directory) that was never created -- a typo, stale
    automation, or wrong cwd, not a code difference. The round-2 fix's
    `if ledger_path.exists(): <guarded> else: <unguarded best-effort>` took
    the unguarded else branch here and wrote the canonical skill file with
    ZERO ledger validation, regardless of the real ledger's 'rejected'
    status. cmd_approve must fail closed against whatever --ledger points
    at: load_ledger already tolerates a missing file (returns an empty
    ledger), so an unseeded --ledger path simply means 'candidate not found'
    -- exit 1, no write -- exactly like cmd_reject has always behaved."""
    name = "skill-promote/fails-closed-on-ledger-flag-pointing-to-never-created-sibling"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    candidate_id = "cand-was-rejected"
    cand_dir = proposals_dir / candidate_id
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(
        _VALID_SKILL_MD.format(name="was-rejected-skill", cid=candidate_id), encoding="utf-8"
    )

    real_ledger_path = tmp_dir / "real-ledger.json"
    seed_ledger = {
        "schema_version": 1,
        "candidates": [{
            "id": candidate_id, "surface": "unscoped", "signature": "some recurring issue",
            "workflows": ["wf-a", "wf-b"], "distinct_workflows": 2, "max_severity": "medium",
            "evidence": [], "first_seen": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:00Z",
            "status": "rejected", "promoted_skill": None,
            "rejected_reason": "already documented in patterns.md",
            "rejected_at_distinct_workflows": 2,
        }],
    }
    real_ledger_path.write_text(json.dumps(seed_ledger), encoding="utf-8")

    # A sibling path in the SAME directory as the real ledger, never created.
    never_created_sibling_path = tmp_dir / "sibling-ledger-never-created.json"
    if never_created_sibling_path.exists():
        fail(name, "test setup invariant violated: sibling ledger path must not exist before the call")
        return

    code, _out, err = _run_promote(candidate_id, proposals_dir, project_root, never_created_sibling_path)
    if code == 0:
        fail(name, f"expected exit 1 when --ledger points at a path that was never created, got exit 0: {_out!r}")
        return
    if (project_root / ".claude" / "skills" / "was-rejected-skill").exists():
        fail(
            name,
            "expected NO canonical write when the --ledger path doesn't exist "
            "(fail-closed, no best-effort bypass for a rejected candidate)",
        )
        return
    if never_created_sibling_path.exists():
        fail(name, "expected the nonexistent --ledger path to remain nonexistent (no silent ledger creation)")
        return
    real_after = json.loads(real_ledger_path.read_text(encoding="utf-8"))
    if real_after["candidates"][0].get("status") != "rejected":
        fail(
            name,
            f"expected the REAL ledger's rejected status to remain untouched, "
            f"got {real_after['candidates'][0].get('status')!r}",
        )
        return
    ok(name)


def test_promote_and_reject_share_equivalent_status_precondition_shape() -> None:
    """Structural regression guard: --approve (craftflow_skill_promote.py)
    and --reject (craftflow_skill_ledger.py) must both gate on the ledger
    candidate's current status via an equivalent precondition ('status not
    in (candidate/proposed)' plus a message naming a possible concurrent
    reject/promote) BEFORE any write. Catches future asymmetric fixes where
    one command's guard rots while the other's doesn't -- exactly the gap
    that let --approve silently re-promote an already-rejected candidate."""
    name = "skill-promote/promote-and-reject-share-status-precondition-shape"
    promote_src = (SCRIPTS / "craftflow_skill_promote.py").read_text(encoding="utf-8")
    ledger_src = (SCRIPTS / "craftflow_skill_ledger.py").read_text(encoding="utf-8")

    def extract_fn(src: str, fn_name: str) -> str:
        m = re.search(rf"^def {fn_name}\(.*?(?=^def |\Z)", src, re.S | re.M)
        return m.group(0) if m else ""

    approve_body = extract_fn(promote_src, "cmd_approve")
    reject_body = extract_fn(ledger_src, "cmd_reject")

    if not approve_body:
        fail(name, "could not locate cmd_approve in craftflow_skill_promote.py")
        return
    if not reject_body:
        fail(name, "could not locate cmd_reject in craftflow_skill_ledger.py")
        return

    status_check_re = re.compile(r'status.{0,40}not in\s*\(\s*["\']candidate["\']\s*,\s*["\']proposed["\']\s*\)')
    # Tolerates the adjacent string literal being split across lines/f-string
    # boundaries (both cmd_approve and cmd_reject wrap this message across
    # several concatenated string literals in source).
    concurrent_msg_re = re.compile(r"a\s+concurrent[\"'\s]*workflow\s+may\s+have\s+already", re.S)

    if not status_check_re.search(approve_body):
        fail(name, "cmd_approve is missing the 'status not in (\"candidate\", \"proposed\")' precondition check")
        return
    if not status_check_re.search(reject_body):
        fail(name, "cmd_reject is missing the 'status not in (\"candidate\", \"proposed\")' precondition check")
        return
    if not concurrent_msg_re.search(approve_body):
        fail(name, "cmd_approve's status-precondition error message doesn't name a concurrent-workflow cause")
        return
    if not concurrent_msg_re.search(reject_body):
        fail(name, "cmd_reject's status-precondition error message doesn't name a concurrent-workflow cause")
        return
    ok(name)


# ---------------------------------------------------------------------------
# skill-promote tests (REM-FIX round 5): the fresh-SKILL.md (new skill)
# branch of cmd_approve had no cross-candidate name-collision protection --
# the canonical write target is derived PURELY from the proposal's own
# `name:` frontmatter field, never from candidate_id, so two entirely
# unrelated candidates can declare the same `name`. candA promotes
# "shared-skill-name" successfully; candB (unrelated, own legitimate
# "candidate" status, never rejected) is then approved with a fresh
# SKILL.md that happens to declare the SAME name -- this silently overwrote
# candA's canonical file (and its .cursor/skills symlink target), exited 0,
# and left the ledger recording BOTH candidates as promoted with the same
# promoted_skill. No error, no warning, no collision trace.
# ---------------------------------------------------------------------------

def test_promote_refuses_cross_candidate_name_collision(tmp_dir: Path) -> None:
    """Live repro of the round-5 gap: candA already promoted 'shared-skill-name'.
    candB is a separate, never-rejected candidate whose own staged SKILL.md
    happens to declare the same name. Approving candB must refuse (exit 1,
    clean JSON error naming candA) rather than silently overwriting candA's
    already-promoted canonical file."""
    name = "skill-promote/refuses-cross-candidate-name-collision"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = tmp_dir / "ledger.json"

    shared_name = "shared-skill-name"
    cand_a = "cand-a-promoted"
    cand_b = "cand-b-candidate"

    # candA's already-promoted canonical file, established independently of
    # this test's --approve call (mirrors a prior, separate --approve run).
    canonical_dir = project_root / ".claude" / "skills" / shared_name
    canonical_dir.mkdir(parents=True)
    original_content = (
        _VALID_SKILL_MD.format(name=shared_name, cid=cand_a)
        .replace("craftflow-promoted-at: PENDING_APPROVAL", "craftflow-promoted-at: 2026-01-01T00:00:00Z")
        .replace("craftflow-review-after: PENDING_APPROVAL", "craftflow-review-after: 2026-04-01T00:00:00Z")
    )
    (canonical_dir / "SKILL.md").write_text(original_content, encoding="utf-8")

    # candB's staged proposal declares the SAME name as candA's promoted skill.
    cand_b_dir = proposals_dir / cand_b
    cand_b_dir.mkdir(parents=True)
    (cand_b_dir / "SKILL.md").write_text(
        _VALID_SKILL_MD.format(name=shared_name, cid=cand_b), encoding="utf-8"
    )

    seed_ledger = {
        "schema_version": 1,
        "candidates": [
            {
                "id": cand_a, "surface": "unscoped", "signature": "signature-a",
                "workflows": ["wf-a"], "distinct_workflows": 2, "max_severity": "medium",
                "evidence": [], "first_seen": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:00Z",
                "status": "promoted", "promoted_skill": shared_name,
                "rejected_reason": None, "rejected_at_distinct_workflows": None,
            },
            {
                "id": cand_b, "surface": "unscoped", "signature": "signature-b",
                "workflows": ["wf-b", "wf-c"], "distinct_workflows": 2, "max_severity": "medium",
                "evidence": [], "first_seen": "2026-01-02T00:00:00Z", "last_seen": "2026-01-02T00:00:00Z",
                "status": "candidate", "promoted_skill": None,
                "rejected_reason": None, "rejected_at_distinct_workflows": None,
            },
        ],
    }
    ledger_path.write_text(json.dumps(seed_ledger), encoding="utf-8")

    code, _out, err = _run_promote(cand_b, proposals_dir, project_root, ledger_path)
    if code == 0:
        fail(name, f"expected exit 1 when candB's proposal collides with candA's already-promoted skill name, got exit 0: {_out!r}")
        return
    try:
        payload = json.loads(err)
    except json.JSONDecodeError:
        fail(name, f"expected a clean JSON error on stderr, got: {err[:300]!r}")
        return
    if "error" not in payload:
        fail(name, f"expected an 'error' key in the JSON error payload, got: {payload}")
        return
    if cand_a not in payload["error"]:
        fail(name, f"expected the conflicting candidate id {cand_a!r} to be named in the error, got: {payload['error']!r}")
        return

    after_content = (canonical_dir / "SKILL.md").read_text(encoding="utf-8")
    if after_content != original_content:
        fail(name, "expected candA's canonical file content to remain UNTOUCHED after the refused collision")
        return

    ledger_after = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry_b_after = next(c for c in ledger_after["candidates"] if c["id"] == cand_b)
    if entry_b_after.get("status") != "candidate":
        fail(name, f"expected candB's ledger status to remain 'candidate' (no silent flip to 'promoted'), got {entry_b_after.get('status')!r}")
        return
    entry_a_after = next(c for c in ledger_after["candidates"] if c["id"] == cand_a)
    if entry_a_after.get("status") != "promoted" or entry_a_after.get("promoted_skill") != shared_name:
        fail(name, "expected candA's ledger entry to remain untouched")
        return
    ok(name)


def test_promote_reapproving_same_already_promoted_candidate_still_refused(tmp_dir: Path) -> None:
    """Regression guard for the round-5 fix: re-approving the SAME candidate
    id against its OWN already-promoted canonical file must remain a clean
    no-op refusal via the pre-existing status precondition (candidate's own
    status is already 'promoted', so the check fires before the new
    cross-candidate collision check is ever reached) -- not newly broken or
    miscategorized by the round-5 fix."""
    name = "skill-promote/reapproving-same-already-promoted-candidate-still-refused"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = tmp_dir / "ledger.json"

    skill_name = "self-reapprove-skill"
    candidate_id = "cand-self"

    canonical_dir = project_root / ".claude" / "skills" / skill_name
    canonical_dir.mkdir(parents=True)
    original_content = (
        _VALID_SKILL_MD.format(name=skill_name, cid=candidate_id)
        .replace("craftflow-promoted-at: PENDING_APPROVAL", "craftflow-promoted-at: 2026-01-01T00:00:00Z")
        .replace("craftflow-review-after: PENDING_APPROVAL", "craftflow-review-after: 2026-04-01T00:00:00Z")
    )
    (canonical_dir / "SKILL.md").write_text(original_content, encoding="utf-8")

    cand_dir = proposals_dir / candidate_id
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(
        _VALID_SKILL_MD.format(name=skill_name, cid=candidate_id), encoding="utf-8"
    )

    seed_ledger = {
        "schema_version": 1,
        "candidates": [{
            "id": candidate_id, "surface": "unscoped", "signature": "some recurring issue",
            "workflows": ["wf-a", "wf-b"], "distinct_workflows": 2, "max_severity": "medium",
            "evidence": [], "first_seen": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:00Z",
            "status": "promoted", "promoted_skill": skill_name,
            "rejected_reason": None, "rejected_at_distinct_workflows": None,
        }],
    }
    ledger_path.write_text(json.dumps(seed_ledger), encoding="utf-8")

    code, _out, err = _run_promote(candidate_id, proposals_dir, project_root, ledger_path)
    if code == 0:
        fail(name, f"expected exit 1 re-approving an already-promoted candidate against its own file, got exit 0: {_out!r}")
        return
    try:
        payload = json.loads(err)
    except json.JSONDecodeError:
        fail(name, f"expected a clean JSON error on stderr, got: {err[:300]!r}")
        return
    if "error" not in payload:
        fail(name, f"expected an 'error' key in the JSON error payload, got: {payload}")
        return
    after_content = (canonical_dir / "SKILL.md").read_text(encoding="utf-8")
    if after_content != original_content:
        fail(name, "expected the candidate's own canonical file content to remain untouched")
        return
    ok(name)


def test_promote_refuses_case_fold_collision_across_candidates(tmp_dir: Path) -> None:
    """Live repro of the round-6 gap: the round-5 cross-candidate
    name-collision guard compares `c.get("promoted_skill") == name` with
    exact, case-SENSITIVE string equality, but `canonical_path` is a real
    filesystem path and this host's filesystem (macOS APFS, and Windows
    NTFS) is case-INSENSITIVE-but-case-preserving. candA promotes
    'Foo-Skill' successfully first. candB then promotes a *different*,
    otherwise-legitimate candidate whose own staged SKILL.md declares
    'foo-skill' (same name, different case only). Without the case-fold
    fix, the string compare 'Foo-Skill' == 'foo-skill' is False, so the
    conflicting-entry lookup finds nothing and candB's approval proceeds --
    silently overwriting candA's already-promoted canonical file on disk
    (same physical directory, different logical ledger name) even though
    `canonical_path.exists()` was already True (the real filesystem already
    knows these are the same entry). This must actually create both
    directories and rely on the real host's case-folding behavior, not a
    mocked comparison, since the bug is a filesystem-semantics gap."""
    name = "skill-promote/refuses-case-fold-collision-across-candidates"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = tmp_dir / "ledger.json"

    cand_a = "cand-a-casefold"
    cand_b = "cand-b-casefold"

    # candA: fresh promotion of "Foo-Skill" via the real --approve path (not
    # pre-seeded out of band), so the canonical directory that actually
    # lands on disk is however the real host's filesystem stores it.
    cand_a_dir = proposals_dir / cand_a
    cand_a_dir.mkdir(parents=True)
    (cand_a_dir / "SKILL.md").write_text(
        _VALID_SKILL_MD.format(name="Foo-Skill", cid=cand_a), encoding="utf-8"
    )
    _seed_ledger_candidate(ledger_path, cand_a, status="candidate")
    # Add candB alongside candA in the same ledger (own distinct, never-
    # rejected candidate entry) before candA's own --approve call runs.
    seed = json.loads(ledger_path.read_text(encoding="utf-8"))
    seed["candidates"].append({
        "id": cand_b, "surface": "unscoped", "signature": "a different recurring issue",
        "workflows": ["wf-x", "wf-y"], "distinct_workflows": 2, "max_severity": "medium",
        "evidence": [], "first_seen": "2026-01-02T00:00:00Z", "last_seen": "2026-01-02T00:00:00Z",
        "status": "candidate", "promoted_skill": None,
        "rejected_reason": None, "rejected_at_distinct_workflows": None,
    })
    ledger_path.write_text(json.dumps(seed), encoding="utf-8")

    code_a, out_a, err_a = _run_promote(cand_a, proposals_dir, project_root, ledger_path)
    if code_a != 0:
        fail(name, f"setup: expected candA's own promotion of 'Foo-Skill' to succeed, got exit {code_a}: {err_a}")
        return

    canonical_dir = project_root / ".claude" / "skills" / "Foo-Skill"
    if not (canonical_dir / "SKILL.md").is_file():
        fail(name, "setup: candA's canonical SKILL.md was not written where expected")
        return
    original_content = (canonical_dir / "SKILL.md").read_text(encoding="utf-8")

    # candB: a separate, never-rejected candidate whose own staged SKILL.md
    # declares the SAME skill name, differing only in case.
    cand_b_dir = proposals_dir / cand_b
    cand_b_dir.mkdir(parents=True)
    (cand_b_dir / "SKILL.md").write_text(
        _VALID_SKILL_MD.format(name="foo-skill", cid=cand_b), encoding="utf-8"
    )

    code_b, out_b, err_b = _run_promote(cand_b, proposals_dir, project_root, ledger_path)
    if code_b == 0:
        fail(name, f"expected exit 1 when candB's 'foo-skill' collides case-insensitively with candA's already-promoted 'Foo-Skill', got exit 0: {out_b!r}")
        return
    try:
        payload = json.loads(err_b)
    except json.JSONDecodeError:
        fail(name, f"expected a clean JSON error on stderr, got: {err_b[:300]!r}")
        return
    if "error" not in payload:
        fail(name, f"expected an 'error' key in the JSON error payload, got: {payload}")
        return
    if cand_a not in payload["error"]:
        fail(name, f"expected the conflicting candidate id {cand_a!r} to be named in the error, got: {payload['error']!r}")
        return

    # candA's canonical file (the one real physical directory both names
    # resolve to on this host) must remain byte-for-byte untouched.
    after_content = (canonical_dir / "SKILL.md").read_text(encoding="utf-8")
    if after_content != original_content:
        fail(name, "expected candA's canonical file content to remain UNTOUCHED after the refused case-fold collision")
        return

    ledger_after = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry_b_after = next(c for c in ledger_after["candidates"] if c["id"] == cand_b)
    if entry_b_after.get("status") != "candidate":
        fail(name, f"expected candB's ledger status to remain 'candidate' (no silent flip to 'promoted'), got {entry_b_after.get('status')!r}")
        return
    ok(name)


def test_promote_malformed_non_dict_ledger_entry_returns_json_error(tmp_dir: Path) -> None:
    """MEDIUM (round 5): a ledger corrupted to contain a non-dict candidate
    entry (e.g. a bare string, number, or null -- as could result from
    hand-edited or partially-migrated ledger JSON) must degrade to this
    script's documented {"error": ...} JSON shape when cmd_approve's lookup
    iterates over it, never an uncaught AttributeError traceback. Still
    fails closed (exit 1, no write) either way -- this is a error-contract
    consistency fix, not a security bypass."""
    name = "skill-promote/malformed-non-dict-ledger-entry-returns-json-error"
    proposals_dir = tmp_dir / "proposals"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = tmp_dir / "ledger.json"
    candidate_id = "cand-malformed"
    cand_dir = proposals_dir / candidate_id
    cand_dir.mkdir(parents=True)
    (cand_dir / "SKILL.md").write_text(
        _VALID_SKILL_MD.format(name="malformed-ledger-skill", cid=candidate_id), encoding="utf-8"
    )
    ledger_path.write_text(json.dumps({
        "schema_version": 1,
        "candidates": ["not-a-dict-entry", 42, None],
    }), encoding="utf-8")

    code, _out, err = _run_promote(candidate_id, proposals_dir, project_root, ledger_path)
    if code == 0:
        fail(name, f"expected exit 1 against a ledger with malformed non-dict candidate entries, got exit 0: {_out!r}")
        return
    try:
        payload = json.loads(err)
    except json.JSONDecodeError:
        fail(name, f"expected a clean JSON error on stderr (not a raw traceback), got: {err[:500]!r}")
        return
    if "error" not in payload:
        fail(name, f"expected an 'error' key in the JSON error payload, got: {payload}")
        return
    if (project_root / ".claude" / "skills" / "malformed-ledger-skill").exists():
        fail(name, "expected NO canonical write against a malformed ledger")
        return
    ok(name)


def test_ledger_reject_malformed_non_dict_ledger_entry_returns_json_error(tmp_dir: Path) -> None:
    """MEDIUM (round 5): mirrors test_promote_malformed_non_dict_ledger_entry_returns_json_error
    for cmd_reject (craftflow_skill_ledger.py) -- a non-dict candidate entry
    in the ledger must never surface as a raw Python traceback."""
    name = "skill-ledger/reject-malformed-non-dict-ledger-entry-returns-json-error"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = tmp_dir / "skill-candidates.json"
    ledger_path.write_text(json.dumps({
        "schema_version": 1,
        "candidates": ["not-a-dict-entry", 42, None],
    }), encoding="utf-8")

    script = SCRIPTS / "craftflow_skill_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), "--reject", "some-id", "--ledger", str(ledger_path)],
        capture_output=True, text=True, cwd=str(tmp_dir),
    )
    if result.returncode == 0:
        fail(name, f"expected non-zero exit against a malformed ledger, got 0: {result.stdout[:300]!r}")
        return
    try:
        payload = json.loads(result.stderr)
    except json.JSONDecodeError:
        fail(name, f"expected a clean JSON error on stderr (not a raw traceback), got: {result.stderr[:500]!r}")
        return
    if "error" not in payload:
        fail(name, f"expected an 'error' key in the JSON error payload, got: {payload}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# skill-propose tests (REM-FIX round 4: craftflow_skill_propose.py --
# architectural fix so the guard can protect the ENTIRE skill-proposals tree
# unconditionally, instead of inferring trust from file existence).
# ---------------------------------------------------------------------------

def _run_propose(
    candidate_id: str,
    skill_md_file: Path,
    proposal_md_file,
    state_dir: Path,
    project_root: Path,
    overwrite: bool = False,
) -> tuple:
    """Invoke craftflow_skill_propose.cmd_propose in-process, capturing
    stdout/stderr. Returns (exit_code, stdout_text, stderr_text)."""
    ns = argparse.Namespace(
        candidate_id=candidate_id,
        skill_md_file=str(skill_md_file),
        proposal_md_file=str(proposal_md_file) if proposal_md_file is not None else None,
        state_dir=str(state_dir),
        project_root=str(project_root),
        overwrite=overwrite,
    )
    out, err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = out, err
        code = skill_propose.cmd_propose(ns)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return code, out.getvalue(), err.getvalue()


def _seed_propose_ledger(ledger_path: Path, candidate_id: str, status: str = "candidate") -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps({
            "schema_version": 1,
            "candidates": [{
                "id": candidate_id, "surface": "unscoped", "signature": "some recurring lesson",
                "workflows": ["wf-a", "wf-b"], "distinct_workflows": 2, "max_severity": "unknown",
                "evidence": [], "first_seen": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:00Z",
                "status": status, "promoted_skill": None, "rejected_reason": None,
                "rejected_at_distinct_workflows": None,
            }],
        }),
        encoding="utf-8",
    )


def test_propose_refuses_unsafe_candidate_id(tmp_dir: Path) -> None:
    name = "skill-propose/refuses-unsafe-candidate-id"
    state_dir = tmp_dir / "state"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    scratch = tmp_dir / "scratch.md"
    scratch.write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="x"), encoding="utf-8")
    code, _out, err = _run_propose("../../etc", scratch, None, state_dir, project_root)
    if code == 0:
        fail(name, "expected exit 1 for a path-traversal candidate id")
        return
    if "unsafe" not in err.lower() and "invalid" not in err.lower():
        fail(name, f"expected stderr to flag the candidate id as unsafe/invalid, got: {err}")
        return
    if (state_dir / "project" / "skill-proposals").exists():
        fail(name, "expected NO write anywhere for an unsafe candidate id")
        return
    ok(name)


def test_propose_refuses_candidate_not_in_ledger(tmp_dir: Path) -> None:
    name = "skill-propose/refuses-candidate-not-in-ledger"
    state_dir = tmp_dir / "state"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    scratch = tmp_dir / "scratch.md"
    scratch.write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand-missing"), encoding="utf-8")
    # No ledger seeded at all -- load_ledger() degrades to an empty ledger.
    code, _out, err = _run_propose("cand-missing", scratch, None, state_dir, project_root)
    if code == 0:
        fail(name, "expected exit 1 when the candidate id has no ledger entry")
        return
    if "no ledger candidate" not in err.lower():
        fail(name, f"expected stderr to explain the missing-candidate failure, got: {err}")
        return
    ok(name)


def test_propose_refuses_terminal_status_rejected(tmp_dir: Path) -> None:
    name = "skill-propose/refuses-terminal-status-rejected"
    state_dir = tmp_dir / "state"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = state_dir / "project" / "skill-candidates.json"
    _seed_propose_ledger(ledger_path, "cand-rej", status="rejected")
    scratch = tmp_dir / "scratch.md"
    scratch.write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand-rej"), encoding="utf-8")
    code, _out, err = _run_propose("cand-rej", scratch, None, state_dir, project_root)
    if code == 0:
        fail(name, "expected exit 1 for a candidate with terminal status 'rejected'")
        return
    if "rejected" not in err:
        fail(name, f"expected stderr to name the actual status 'rejected', got: {err}")
        return
    if (state_dir / "project" / "skill-proposals").exists():
        fail(name, "expected NO write for a rejected candidate")
        return
    ok(name)


def test_propose_refuses_terminal_status_promoted(tmp_dir: Path) -> None:
    name = "skill-propose/refuses-terminal-status-promoted"
    state_dir = tmp_dir / "state"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = state_dir / "project" / "skill-candidates.json"
    _seed_propose_ledger(ledger_path, "cand-promo", status="promoted")
    scratch = tmp_dir / "scratch.md"
    scratch.write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand-promo"), encoding="utf-8")
    code, _out, err = _run_propose("cand-promo", scratch, None, state_dir, project_root)
    if code == 0:
        fail(name, "expected exit 1 for a candidate with terminal status 'promoted'")
        return
    if "promoted" not in err:
        fail(name, f"expected stderr to name the actual status 'promoted', got: {err}")
        return
    ok(name)


def test_propose_refuses_invalid_frontmatter(tmp_dir: Path) -> None:
    name = "skill-propose/refuses-invalid-frontmatter"
    state_dir = tmp_dir / "state"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = state_dir / "project" / "skill-candidates.json"
    _seed_propose_ledger(ledger_path, "cand-bad-fm")
    scratch = tmp_dir / "scratch.md"
    bad = _VALID_SKILL_MD.format(name="foo-skill", cid="cand-bad-fm").replace(
        'description: "Use when this exact lesson recurs again. Provides a documented, evidence-backed fix pattern for it."',
        'description: "too short"',
    )
    scratch.write_text(bad, encoding="utf-8")
    code, _out, err = _run_propose("cand-bad-fm", scratch, None, state_dir, project_root)
    if code == 0:
        fail(name, "expected exit 1 for a description under 40 characters")
        return
    if "description" not in err:
        fail(name, f"expected stderr to explain the description-length failure, got: {err}")
        return
    if (state_dir / "project" / "skill-proposals").exists():
        fail(name, "expected NO write when frontmatter validation fails")
        return
    ledger_after = json.loads(ledger_path.read_text(encoding="utf-8"))
    if ledger_after["candidates"][0]["status"] != "candidate":
        fail(name, "expected the ledger status to remain 'candidate' when the draft is rejected")
        return
    ok(name)


def test_propose_stages_valid_candidate_and_updates_ledger_status(tmp_dir: Path) -> None:
    name = "skill-propose/stages-valid-candidate-and-updates-ledger-status"
    state_dir = tmp_dir / "state"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = state_dir / "project" / "skill-candidates.json"
    _seed_propose_ledger(ledger_path, "cand-ok")
    scratch_skill = tmp_dir / "scratch-skill.md"
    scratch_skill.write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand-ok"), encoding="utf-8")
    scratch_proposal = tmp_dir / "scratch-proposal.md"
    scratch_proposal.write_text("# Proposal rationale\n\nEvidence trail.\n", encoding="utf-8")

    code, out, err = _run_propose("cand-ok", scratch_skill, scratch_proposal, state_dir, project_root)
    if code != 0:
        fail(name, f"expected exit 0 for a valid propose, got {code}, stderr={err}")
        return

    candidate_dir = state_dir / "project" / "skill-proposals" / "cand-ok"
    skill_md = candidate_dir / "SKILL.md"
    proposal_md = candidate_dir / "PROPOSAL.md"
    if not skill_md.is_file():
        fail(name, f"expected staged SKILL.md at {skill_md}")
        return
    if skill_md.read_text(encoding="utf-8") != scratch_skill.read_text(encoding="utf-8"):
        fail(name, "expected the staged SKILL.md content to exactly match the scratch draft")
        return
    if not proposal_md.is_file():
        fail(name, f"expected staged PROPOSAL.md at {proposal_md}")
        return
    if "Evidence trail." not in proposal_md.read_text(encoding="utf-8"):
        fail(name, "expected the staged PROPOSAL.md to contain the drafted content")
        return

    result = json.loads(out)
    if result.get("proposed") != "cand-ok":
        fail(name, f"expected proposed='cand-ok' in JSON result, got: {result}")
        return
    if result.get("skill_md_path") != str(skill_md):
        fail(name, f"expected skill_md_path={skill_md} in JSON result, got: {result.get('skill_md_path')}")
        return

    ledger_after = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry = ledger_after["candidates"][0]
    if entry["status"] != "proposed":
        fail(name, f"expected ledger candidate status to become 'proposed', got: {entry['status']!r}")
        return
    ok(name)


def test_propose_refuses_overwrite_without_flag(tmp_dir: Path) -> None:
    name = "skill-propose/refuses-overwrite-without-flag"
    state_dir = tmp_dir / "state"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = state_dir / "project" / "skill-candidates.json"
    _seed_propose_ledger(ledger_path, "cand-dup")
    scratch = tmp_dir / "scratch.md"
    scratch.write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand-dup"), encoding="utf-8")

    code1, _out1, err1 = _run_propose("cand-dup", scratch, None, state_dir, project_root)
    if code1 != 0:
        fail(name, f"expected first propose to succeed, got {code1}, stderr={err1}")
        return

    # Re-seed the ledger back to "candidate" (the first run already flipped it
    # to "proposed" -- this test targets the overwrite guard specifically, not
    # the status-gating check already proven above).
    _seed_propose_ledger(ledger_path, "cand-dup")
    code2, _out2, err2 = _run_propose("cand-dup", scratch, None, state_dir, project_root)
    if code2 == 0:
        fail(name, "expected exit 1 on a second propose for the same candidate id without --overwrite")
        return
    if "overwrite" not in err2.lower():
        fail(name, f"expected stderr to mention --overwrite, got: {err2}")
        return
    ok(name)


def test_propose_allows_overwrite_with_flag(tmp_dir: Path) -> None:
    name = "skill-propose/allows-overwrite-with-flag"
    state_dir = tmp_dir / "state"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = state_dir / "project" / "skill-candidates.json"
    _seed_propose_ledger(ledger_path, "cand-ov")
    scratch = tmp_dir / "scratch.md"
    scratch.write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand-ov"), encoding="utf-8")

    code1, _out1, err1 = _run_propose("cand-ov", scratch, None, state_dir, project_root)
    if code1 != 0:
        fail(name, f"expected first propose to succeed, got {code1}, stderr={err1}")
        return

    _seed_propose_ledger(ledger_path, "cand-ov")
    scratch2 = tmp_dir / "scratch2.md"
    scratch2.write_text(_VALID_SKILL_MD.format(name="foo-skill-v2", cid="cand-ov"), encoding="utf-8")
    code2, _out2, err2 = _run_propose("cand-ov", scratch2, None, state_dir, project_root, overwrite=True)
    if code2 != 0:
        fail(name, f"expected the --overwrite re-propose to succeed, got {code2}, stderr={err2}")
        return
    staged = (state_dir / "project" / "skill-proposals" / "cand-ov" / "SKILL.md").read_text(encoding="utf-8")
    if "foo-skill-v2" not in staged:
        fail(name, "expected the --overwrite re-propose to replace the staged content")
        return
    ok(name)


def test_propose_holds_ledger_lock_for_the_write_sequence(tmp_dir: Path) -> None:
    name = "skill-propose/holds-ledger-lock-for-the-write-sequence"
    state_dir = tmp_dir / "state"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    ledger_path = state_dir / "project" / "skill-candidates.json"
    _seed_propose_ledger(ledger_path, "cand-lock")
    scratch = tmp_dir / "scratch.md"
    scratch.write_text(_VALID_SKILL_MD.format(name="foo-skill", cid="cand-lock"), encoding="utf-8")
    code, _out, err = _run_propose("cand-lock", scratch, None, state_dir, project_root)
    if code != 0:
        fail(name, f"expected propose to succeed, got {code}, stderr={err}")
        return
    lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
    if not lock_path.exists():
        fail(
            name,
            f"expected the ledger lock file at {lock_path} created by "
            "_ledger_file_lock() during the atomic write+mark sequence",
        )
        return
    ok(name)


def test_propose_refuses_missing_skill_md_file(tmp_dir: Path) -> None:
    name = "skill-propose/refuses-missing-skill-md-file"
    state_dir = tmp_dir / "state"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    missing = tmp_dir / "does-not-exist.md"
    code, _out, err = _run_propose("cand-x", missing, None, state_dir, project_root)
    if code == 0:
        fail(name, "expected exit 1 when --skill-md-file does not exist")
        return
    if "skill-md-file" not in err.lower():
        fail(name, f"expected stderr to name --skill-md-file, got: {err}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# skill-author / skill-distillation structural presence tests
# ---------------------------------------------------------------------------

def test_skill_author_agent_present() -> None:
    name = "skill-author/agent-file-present"
    path = PLUGIN_ROOT / "agents" / "skill-author.md"
    if not path.exists():
        fail(name, f"skill-author.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    fm_match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---(\r?\n|$)", content)
    if not fm_match:
        fail(name, "skill-author.md has no leading frontmatter block")
        return
    frontmatter = fm_match.group(1)
    if "\ntools: " not in ("\n" + frontmatter):
        fail(name, "skill-author.md frontmatter missing 'tools: ' key")
        return
    if "\nallowed-tools: " in ("\n" + frontmatter):
        fail(name, "skill-author.md frontmatter still uses legacy 'allowed-tools:' key")
        return
    if "craftflow:skill-distillation" not in content:
        fail(name, "skill-author.md missing 'craftflow:skill-distillation' in its skills list")
        return
    for marker in ("STAGED", "never write", ".claude/skills/", ".cursor/skills/"):
        if marker not in content:
            fail(name, f"skill-author.md missing expected marker: {marker!r}")
            return
    ok(name)


def test_skill_author_agent_documents_propose_script_invocation() -> None:
    # REM-FIX round 4: skill-author must no longer document a direct `Write`
    # to the staging path -- confirms it documents invoking
    # craftflow_skill_propose.py via Bash instead (grep-based structural
    # test, matching this repo's existing convention for checking agent-file
    # documented behavior, e.g. test_skill_author_agent_present() above).
    name = "skill-author/agent-documents-propose-script-invocation"
    path = PLUGIN_ROOT / "agents" / "skill-author.md"
    if not path.exists():
        fail(name, f"skill-author.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in ("craftflow_skill_propose.py", "--candidate-id", "--skill-md-file", "scratch", "trusted script"):
        if marker not in content:
            fail(name, f"skill-author.md missing expected marker: {marker!r}")
            return
    if "Bash" not in content:
        fail(name, "skill-author.md unexpectedly has no 'Bash' mentions at all")
        return
    ok(name)


def test_skill_distillation_skill_present() -> None:
    name = "skill-distillation/skill-file-present"
    path = PLUGIN_ROOT / "skills" / "skill-distillation" / "SKILL.md"
    if not path.exists():
        fail(name, f"skill-distillation SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in ("STATUS: SKIPPED", "SKIP_REASON", "user-invocable: false"):
        if marker not in content:
            fail(name, f"skill-distillation SKILL.md missing expected marker: {marker!r}")
            return
    ok(name)


def test_craftflow_router_shared_protocol_extraction_no_stale_reembed() -> None:
    # Presence-marker test for Phase 3/3b of the hooks-as-bridge redesign (backlog item 8,
    # plan Component 7): craftflow-router/SKILL.md must Read() the shared doc for the
    # sections extracted so far (Intent Routing, dispatch prompt scaffold, Resolve Project
    # Root), not silently keep a duplicate copy of the literal content alongside the
    # pointer -- and the shared doc must actually still hold that content, not just claim
    # to.
    name = "craftflow-router/skill-md/shared-protocol-extraction-no-stale-reembed"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    shared_path = PLUGIN_ROOT / "skills" / "_shared" / "router-protocol.md"
    if not skill_path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {skill_path}")
        return
    if not shared_path.exists():
        fail(name, f"shared router-protocol.md not found at {shared_path}")
        return
    skill_content = skill_path.read_text(encoding="utf-8")
    shared_content = shared_path.read_text(encoding="utf-8")

    if skill_content.count("skills/_shared/router-protocol.md") < 2:
        fail(
            name,
            f"expected at least 2 references to the shared doc in SKILL.md (mandatory "
            f"reference read + at least one section pointer), found "
            f"{skill_content.count('skills/_shared/router-protocol.md')}",
        )
        return

    # Old literal content that moved must be ABSENT from SKILL.md (else it's a stale
    # re-embedded copy sitting alongside the new pointer) and PRESENT in the shared doc.
    moved_markers = (
        # Intent Routing priority table -- a full row, not just a loose keyword, so a
        # false positive can't come from the word appearing incidentally elsewhere.
        "| 1 | ERROR | error, bug, fix, broken, crash, fail, debug, troubleshoot, issue | DEBUG | bug-investigator -> code-reviewer -> integration-verifier |",
        # Dispatch prompt scaffold's literal field block.
        "## Task Context\n- Task ID: {task_id}",
        "## SKILL_HINTS\n{router-detected skill list or \"None\"}",
        # Resolve Project Root's 1a. multi-repo branch (Phase 3b) -- DETERMINISTIC outcome
        # parse, the workspace_writable_paths capture, and the AMBIGUOUS AskUserQuestion
        # gate note, none of which have any reason to appear elsewhere in SKILL.md.
        "RESOLVE_OUTCOME=$(printf '%s' \"$RESOLVE_RESULT\" | python3 -c \"import json,sys; print(json.load(sys.stdin)['outcome'])\")",
        "WORKSPACE_WRITABLE_PATHS_JSON=$(printf '%s' \"$RESOLVE_RESULT\" | python3 -c \"import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('workspace_writable_paths', [])))\")",
        "this `AskUserQuestion` gate is NEVER auto-defaulted under `JUST_GO=true`",
    )
    for marker in moved_markers:
        if marker in skill_content:
            fail(name, f"SKILL.md still contains moved literal content (stale re-embedded copy): {marker!r}")
            return
        if marker not in shared_content:
            fail(name, f"shared doc missing expected extracted content: {marker!r}")
            return
    ok(name)


def test_craftflow_router_documents_state_read_compaction_self_heal() -> None:
    name = "craftflow-router/skill-md/documents-state-read-compaction-self-heal"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in ("craftflow_state_query.py", "state-read-compaction"):
        if marker not in content:
            fail(name, f"craftflow-router SKILL.md missing expected marker: {marker!r}")
            return
    ok(name)


def test_memory_finalize_instruction_sites_use_state_query_full_mode() -> None:
    name = "craftflow-router/references/memory-finalize-uses-state-query-full-mode"
    marker = "craftflow_state_query.py <destination_file_path> --mode full"
    expected_counts = {
        "plan-workflow.md": 1,
        "build-workflow.md": 2,
        "debug-workflow.md": 1,
        "review-workflow.md": 1,
    }
    for filename, expected in expected_counts.items():
        path = PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / filename
        if not path.exists():
            fail(name, f"{filename} not found at {path}")
            return
        content = path.read_text(encoding="utf-8")
        actual = content.count(marker)
        if actual != expected:
            fail(name, f"{filename}: expected {expected} occurrence(s) of {marker!r}, found {actual}")
            return
    ok(name)


def test_memory_finalize_instruction_sites_wire_archive_rotation() -> None:
    name = "craftflow-router/references/memory-finalize-wires-archive-rotation"
    marker = "write archive_path FIRST"
    expected_counts = {
        "plan-workflow.md": 1,
        "build-workflow.md": 2,
        "debug-workflow.md": 1,
        "review-workflow.md": 1,
    }
    for filename, expected in expected_counts.items():
        path = PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / filename
        if not path.exists():
            fail(name, f"{filename} not found at {path}")
            return
        content = path.read_text(encoding="utf-8")
        actual = content.count(marker)
        if actual != expected:
            fail(name, f"{filename}: expected {expected} occurrence(s) of {marker!r}, found {actual}")
            return
    ok(name)


def test_rubric_documents_three_rejection_cases() -> None:
    name = "skill-distillation/rubric-documents-three-rejection-cases"
    path = PLUGIN_ROOT / "skills" / "skill-distillation" / "references" / "rubric.md"
    if not path.exists():
        fail(name, f"rubric.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in ("Already-in-gotchas", "no executable artifact", "Duplicate of an existing skill"):
        if marker not in content:
            fail(name, f"rubric.md missing expected rejection-case marker: {marker!r}")
            return
    ok(name)


def test_skill_promote_script_present() -> None:
    name = "skill-promote/script-present"
    path = SCRIPTS / "craftflow_skill_promote.py"
    if not path.exists():
        fail(name, f"craftflow_skill_promote.py not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in ("--approve", "PENDING_APPROVAL", "stale-backup", "dereference"):
        if marker not in content:
            fail(name, f"craftflow_skill_promote.py missing expected marker: {marker!r}")
            return
    ok(name)


# ---------------------------------------------------------------------------
# Phase 3: router wiring (skill-distill phase + learn-distill dead-wiring fix)
# ---------------------------------------------------------------------------

def test_router_phase_enum_registers_skill_distill_learn_distill_doubt_verify() -> None:
    name = "router/phase-enum-registers-skill-distill-learn-distill-doubt-verify"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    match = re.search(r"^phase:\{[^}]*\}$", content, re.MULTILINE)
    if not match:
        fail(name, "SKILL.md missing the 'phase:{...}' Task Metadata Contract enum line")
        return
    enum_line = match.group(0)
    for phase in ("skill-distill", "learn-distill", "doubt-verify"):
        if phase not in enum_line:
            fail(name, f"phase:{{...}} enum line missing {phase!r}: {enum_line}")
            return
    ok(name)


def test_router_dispatcher_table_includes_skill_distill() -> None:
    name = "router/dispatcher-table-includes-skill-distill"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "| skill-distill | craftflow:skill-author |" not in content and \
       "| `skill-distill` | `craftflow:skill-author` |" not in content:
        fail(name, "Explicit dispatcher table missing 'skill-distill -> craftflow:skill-author' row")
        return
    ok(name)


def test_router_effort_dispatch_includes_skill_distill_low() -> None:
    name = "router/effort-dispatch-includes-skill-distill-low"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    match = re.search(r"^-\s.*`learn-distill`.*(?:->|→)\s*`low`\s*$", content, re.MULTILINE)
    if not match or "skill-distill" not in match.group(0):
        fail(name, "Effort Dispatch Rule list missing 'skill-distill' on the same low-effort line as learn-distill")
        return
    ok(name)


def test_router_contract_overrides_includes_skill_author() -> None:
    name = "router/contract-overrides-includes-skill-author"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    match = re.search(r"^\|\s*skill-author\s*\|.*\|$", content, re.MULTILINE)
    if not match:
        fail(name, "Contract overrides table missing a 'skill-author' row")
        return
    row = match.group(0)
    for marker in ("PROPOSAL_PATH", "CANDIDATE_ID", "SKIP_REASON", "passing state"):
        if marker not in row:
            fail(name, f"skill-author contract override row missing expected marker {marker!r}: {row}")
            return
    ok(name)


def test_router_memory_finalization_calls_ledger_observe() -> None:
    name = "router/memory-finalization-calls-ledger-observe"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "craftflow_skill_ledger.py --observe {workflow_uuid}" not in content:
        fail(name, "## 13. Memory Finalization missing the deterministic 'craftflow_skill_ledger.py --observe {workflow_uuid}' call")
        return
    if "--state-dir" not in content.split("craftflow_skill_ledger.py --observe {workflow_uuid}")[1][:80]:
        fail(name, "craftflow_skill_ledger.py --observe call missing --state-dir flag")
        return
    ok(name)


def test_router_memory_finalization_calls_ledger_prune() -> None:
    # Phase 4: wires a real caller for --prune into the same memory-finalize
    # step that already unconditionally runs --observe (SKILL.md ## 13,
    # single non-duplicated location applying to every workflow type).
    name = "router/memory-finalization-calls-ledger-prune"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "craftflow_skill_ledger.py --prune" not in content:
        fail(name, "## 13. Memory Finalization missing the deterministic 'craftflow_skill_ledger.py --prune' call")
        return
    tail = content.split("craftflow_skill_ledger.py --prune")[1][:120]
    if "--state-dir" not in tail:
        fail(name, "craftflow_skill_ledger.py --prune call missing --state-dir flag")
        return
    if "--project-root" not in tail:
        fail(name, "craftflow_skill_ledger.py --prune call missing --project-root flag")
        return
    if "needs_review" not in content:
        fail(name, "prune step documentation missing needs_review rot-flag explanation")
        return
    ok(name)


def test_router_hard_rules_includes_skill_distill_skip() -> None:
    name = "router/hard-rules-includes-skill-distill-skip"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "SKILL_DISTILL: skip" not in content:
        fail(name, "## 14. Hard Rules missing 'SKILL_DISTILL: skip' rule")
        return
    if "skill-distill" not in content.split("SKILL_DISTILL: skip")[1][:200]:
        fail(name, "'SKILL_DISTILL: skip' rule does not reference the 'skill-distill' phase it disables")
        return
    ok(name)


def test_router_documents_skill_distill_approval_flow() -> None:
    name = "router/skill-distill-approval-flow-documented"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"craftflow-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in (
        "AskUserQuestion", "Approve + register in SKILL_HINTS", "Reject", "Defer",
        "craftflow_skill_promote.py", "--approve", "craftflow_skill_ledger.py --reject",
        "STATUS: SKIPPED", "STATUS: COMPLETE",
    ):
        if marker not in content:
            fail(name, f"Skill-Distill Approval Flow section missing expected marker: {marker!r}")
            return
    ok(name)


def test_build_workflow_wires_learn_distill_taskcreate() -> None:
    name = "build-workflow/wires-learn-distill-taskcreate"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / "build-workflow.md"
    if not path.exists():
        fail(name, f"build-workflow.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "phase:learn-distill" not in content:
        fail(name, "build-workflow.md missing a TaskCreate with 'phase:learn-distill' -- learn-distill dead-wiring gap not fixed")
        return
    if "craftflow:learn-distiller" not in content and "learn-distiller" not in content:
        fail(name, "build-workflow.md learn-distill TaskCreate does not reference the learn-distiller agent")
        return
    ok(name)


def test_build_workflow_wires_skill_distill_taskcreate() -> None:
    name = "build-workflow/wires-skill-distill-taskcreate"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / "build-workflow.md"
    if not path.exists():
        fail(name, f"build-workflow.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "phase:skill-distill" not in content:
        fail(name, "build-workflow.md missing a TaskCreate with 'phase:skill-distill'")
        return
    if "gate_eligible" not in content:
        fail(name, "build-workflow.md skill-distill task graph missing gate_eligible gating reference")
        return
    if "memory_task_id" not in content.split("phase:skill-distill")[0][-2000:] and \
       "addBlockedBy: [skill_distill_task_id]" not in content:
        fail(name, "memory_task_id does not appear repointed to depend on the skill-distill task")
        return
    ok(name)


def test_build_workflow_fast_path_graph_wires_learn_and_skill_distill() -> None:
    name = "build-workflow/fast-path-graph-wires-learn-and-skill-distill"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / "build-workflow.md"
    if not path.exists():
        fail(name, f"build-workflow.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    fast_path_idx = content.find("### BUILD task graph — fast path")
    if fast_path_idx == -1:
        fail(name, "build-workflow.md missing '### BUILD task graph — fast path' section")
        return
    fast_path_section = content[fast_path_idx:]
    for marker in ("phase:learn-distill", "phase:skill-distill"):
        if marker not in fast_path_section:
            fail(name, f"fast-path BUILD task graph section missing {marker!r}")
            return
    ok(name)


def test_debug_workflow_documents_skill_distill_reasoning() -> None:
    name = "debug-workflow/documents-skill-distill-reasoning"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / "debug-workflow.md"
    if not path.exists():
        fail(name, f"debug-workflow.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "skill-distill" not in content:
        fail(name, "debug-workflow.md has no 'skill-distill' reference or insertion-reasoning note")
        return
    ok(name)


def test_fast_path_agent_dispatch_table_includes_skill_distill() -> None:
    name = "fast-path/agent-dispatch-table-includes-skill-distill"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / "fast-path.md"
    if not path.exists():
        fail(name, f"fast-path.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    match = re.search(r"^\|\s*`skill-distill`\s*\|.*\|$", content, re.MULTILINE)
    if not match:
        fail(name, "Agent Dispatch Table missing a 'skill-distill' row")
        return
    row = match.group(0)
    if "skill-author" not in row:
        fail(name, f"skill-distill dispatch table row missing 'skill-author' agent reference: {row}")
        return
    if row.count("gated") < 2:
        fail(name, f"skill-distill dispatch table row expected gated on both standard and fast path: {row}")
        return
    ok(name)


def test_fast_path_escalated_gate_wiring_reconciled() -> None:
    # CRITICAL 1 (REM-FIX): the Agent Dispatch Table's Escalated column had
    # said "skip" for learn-distill/skill-distill while the Gate Table (and
    # the prose) said "conditional" for the same two phases -- a direct
    # contradiction. Both tables must now agree: Escalated is "conditional"
    # for both phases in both tables.
    name = "fast-path/escalated-gate-wiring-reconciled"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / "fast-path.md"
    if not path.exists():
        fail(name, f"fast-path.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")

    dispatch_rows = {}
    for phase in ("learn-distill", "skill-distill"):
        match = re.search(rf"^\|\s*`{re.escape(phase)}`\s*\|.*\|$", content, re.MULTILINE)
        if not match:
            fail(name, f"Agent Dispatch Table missing a {phase!r} row")
            return
        cells = [c.strip() for c in match.group(0).strip("|").split("|")]
        if len(cells) < 5:
            fail(name, f"Agent Dispatch Table {phase!r} row has fewer than 5 columns: {cells}")
            return
        dispatch_rows[phase] = cells[4]  # Escalated column (Phase|Agent|Standard|Fast path|Escalated|Effort)

    gate_rows = {}
    for gate in ("learn_distill_gate", "skill_distill_gate"):
        match = re.search(rf"^\|\s*`{re.escape(gate)}`\s*\|.*\|$", content, re.MULTILINE)
        if not match:
            fail(name, f"Gate Table missing a {gate!r} row")
            return
        cells = [c.strip() for c in match.group(0).strip("|").split("|")]
        if len(cells) < 4:
            fail(name, f"Gate Table {gate!r} row has fewer than 4 columns: {cells}")
            return
        gate_rows[gate] = cells[3]  # Escalated column (Gate/Rule|Standard|Fast path|Escalated)

    pairs = (("learn-distill", "learn_distill_gate"), ("skill-distill", "skill_distill_gate"))
    for dispatch_phase, gate_name in pairs:
        dispatch_escalated = dispatch_rows[dispatch_phase]
        gate_escalated = gate_rows[gate_name]
        if "skip" in dispatch_escalated.lower():
            fail(
                name,
                f"Agent Dispatch Table Escalated column for {dispatch_phase!r} still says "
                f"'skip' ({dispatch_escalated!r}), contradicting Gate Table's {gate_escalated!r}",
            )
            return
        if "conditional" not in dispatch_escalated.lower():
            fail(name, f"Agent Dispatch Table Escalated column for {dispatch_phase!r} must say 'conditional', got {dispatch_escalated!r}")
            return
        if "conditional" not in gate_escalated.lower():
            fail(name, f"Gate Table Escalated column for {gate_name!r} must say 'conditional', got {gate_escalated!r}")
            return
    ok(name)


def test_fast_path_documents_skill_distill_gate() -> None:
    name = "fast-path/skill-distill-gate-section-present"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / "fast-path.md"
    if not path.exists():
        fail(name, f"fast-path.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "#### Skill-Distill Gate" not in content:
        fail(name, "fast-path.md missing '#### Skill-Distill Gate' section")
        return
    section = content.split("#### Skill-Distill Gate", 1)[1]
    for marker in ("gate_eligible", "promoted", "rejected", "memory-finalize"):
        if marker not in section[:2000]:
            fail(name, f"Skill-Distill Gate section missing expected marker: {marker!r}")
            return
    ok(name)


def test_workflow_artifact_policy_registers_skill_distill_events() -> None:
    name = "workflow-artifact-policy/registers-skill-distill-events"
    path = PLUGIN_ROOT / "skills" / "craftflow-router" / "references" / "workflow-artifact-and-hook-policy.md"
    if not path.exists():
        fail(name, f"workflow-artifact-and-hook-policy.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for event in (
        "skill_candidates_observed",
        "skill_proposed",
        "skill_promoted",
        "skill_rejected",
        "skill_distill_skipped",
        "skill_distill_failed",
    ):
        if event not in content:
            fail(name, f"workflow-artifact-and-hook-policy.md missing event registration: {event!r}")
            return
    ok(name)


def test_craftflow_state_mdc_documents_skill_distillation_paths() -> None:
    name = "craftflow-state-mdc/documents-skill-distillation-paths"
    path = PLUGIN_ROOT / "rules" / "craftflow-state.mdc"
    if not path.exists():
        fail(name, f"craftflow-state.mdc not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    for marker in ("skill-candidates.json", "skill-proposals"):
        if marker not in content:
            fail(name, f"craftflow-state.mdc missing expected path reference: {marker!r}")
            return
    ok(name)


def test_cursor_router_wires_skill_distill_gate() -> None:
    # Renamed from "cursor-router/documents-skill-distill". The old test only
    # asserted the substring "skill-distill" appeared ANYWHERE in the file --
    # it passed equally whether the phase was fully wired or explicitly
    # documented as "deferred to v2" with zero dispatch/state-tracking logic.
    # This is why the gap survived 5 REM-FIX rounds undetected (doubt-verify
    # caught it). This test instead proves real gate+dispatch+approval wiring
    # exists: it asserts the OLD deferred-to-v2 text is GONE (would fail
    # against the pre-fix file) and that the concrete mechanics -- ledger
    # query, skill-author Task dispatch, the plain-text approval exchange,
    # the cursor-wf.json pending-state field, the fail-closed default, and
    # the promote/reject script invocations -- are all present (passes only
    # against real wiring).
    name = "cursor-router/wires-skill-distill-gate"
    path = PLUGIN_ROOT / "skills" / "cursor-router" / "SKILL.md"
    if not path.exists():
        fail(name, f"cursor-router SKILL.md not found at {path}")
        return
    content = path.read_text(encoding="utf-8")

    # Proves the old toothless test would have failed here: the deferred
    # bullet this feature replaces must be gone, not just supplemented.
    if "Skill-distill phase (deferred to v2)" in content:
        fail(name, "cursor-router SKILL.md still carries the old 'Skill-distill phase "
                    "(deferred to v2)' bullet -- gate was never actually wired")
        return

    if "## 5a. Skill-Distill Gate" not in content:
        fail(name, "cursor-router SKILL.md missing '## 5a. Skill-Distill Gate' section")
        return
    gate_section = content.split("## 5a. Skill-Distill Gate", 1)[1]
    gate_section = gate_section.split("## 6. Post-Agent Validation", 1)[0]

    for marker in (
        "craftflow_skill_ledger.py",
        "--query",
        "distinct_workflows >= 2",
        'status == "candidate"',
        "Task` ONCE",
        "skill-author.md",
        "STATUS: SKIPPED",
        "STATUS: COMPLETE",
        "STATUS: FAIL",
        "AskUserQuestion",
        "pending_skill_approval",
        "Choose one:",
        "craftflow_skill_promote.py",
        "--approve",
        "--reject {candidate_id}",
        "AUTO_PROCEED: true",
        "fail-closed default is **Defer**",
        "Resume behavior",
    ):
        if marker not in gate_section:
            fail(name, f"Skill-Distill Gate section (§ 5a) missing expected marker: {marker!r}")
            return

    if "| skill-author | `tools/craftflow-plugin/plugins/craftflow/agents/skill-author.md` |" not in content:
        fail(name, "cursor-router SKILL.md Agent File Paths table missing skill-author row")
        return

    if "pending_skill_approval" not in content.split("## 3. Workflow Preparation", 1)[1].split("## 4. Workflow Artifact Creation", 1)[0]:
        fail(name, "cursor-router SKILL.md § 3 Workflow Preparation resume check does not "
                    "mention pending_skill_approval")
        return

    ok(name)


def test_safe_shell_guard_still_blocks_fork_bomb_embedded_in_long_command(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-fork-bomb-embedded-in-long-benign-prefix"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    command = ("echo hi; " * 5000) + ":(){ :|:& };:"
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": command}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


# --- CRITICAL C: unrecognized wrapper prefixes bypass detection ------------

def test_safe_shell_guard_blocks_env_wrapped_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-env-wrapped-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "env rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_nice_wrapped_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-nice-wrapped-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "nice rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_nohup_wrapped_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-nohup-wrapped-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "nohup rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_timeout_wrapped_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-timeout-wrapped-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "timeout 5 rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_xargs_wrapped_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-xargs-wrapped-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "xargs -I{} rm -rf / </dev/null"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_find_exec_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-find-exec-rm-rf-placeholder"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": r"find / -exec rm -rf {} \;"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_python3_c_os_system_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-python3-c-os-system-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "python3 -c \"import os; os.system('rm -rf /')\""},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_perl_e_system_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-perl-e-system-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": 'perl -e \'system("rm -rf /")\''},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_allows_env_wrapped_benign(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-env-wrapped-benign-command"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "env FOO=bar git status"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_allowed(name, out):
        ok(name)


def test_safe_shell_guard_allows_timeout_wrapped_benign(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-timeout-wrapped-benign-command"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "timeout 5 git status"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_allowed(name, out):
        ok(name)


def test_safe_shell_guard_allows_python3_c_benign(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-python3-c-benign-code"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "python3 -c \"print('hello world')\""},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_allowed(name, out):
        ok(name)


def test_safe_shell_guard_allows_find_without_exec(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-plain-find-without-exec"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "find . -name '*.tmp'"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_allowed(name, out):
        ok(name)


# --- CRITICAL D: dynamic/indirect command-name tokens bypass detection -----

def test_safe_shell_guard_blocks_dynamic_var_command_name(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-dynamic-var-command-name-argv0"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "X=rm; $X -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_command_substitution_argv0(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-command-substitution-argv0"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "$(echo rm) -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_fails_closed_on_dynamic_command_name_generic(tmp_dir: Path) -> None:
    name = "safe-shell-guard/fails-closed-on-dynamic-command-name-generic"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "$TOOL --version"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


# ---------------------------------------------------------------------------
# REM-FIX round 3 (FINAL for this file): 3 CRITICAL + 1 HIGH findings
# ---------------------------------------------------------------------------

def _read_fork_bomb_window_right() -> int:
    """Reads the guard's own _FORK_BOMB_WINDOW_RIGHT constant from source so
    the boundary tests below track it instead of duplicating a hardcoded
    number that could silently drift out of sync with the implementation."""
    content = (SCRIPTS / "craftflow_safe_shell_guard.py").read_text(encoding="utf-8")
    match = re.search(r"_FORK_BOMB_WINDOW_RIGHT\s*=\s*(\d+)", content)
    if not match:
        raise AssertionError(
            "could not find _FORK_BOMB_WINDOW_RIGHT = <int> in craftflow_safe_shell_guard.py "
            "-- constant name likely changed without updating this test helper"
        )
    return int(match.group(1))


# --- ROUND-3 CRITICAL-1: fork-bomb bounded-window bypass via padded body ---

def test_safe_shell_guard_blocks_fork_bomb_padded_body_well_under_window(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-fork-bomb-padded-body-well-under-window"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    padding = " " * 4000
    command = ":(){" + padding + ":|:& };:"
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": command}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_fork_bomb_padded_body_at_window_boundary(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-fork-bomb-padded-body-at-window-boundary"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    tail = ":|:& };:"
    window_right = _read_fork_bomb_window_right()
    padding = " " * (window_right - len(tail))  # tail's last char lands exactly at the window edge
    command = ":(){" + padding + tail
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": command}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_allows_fork_bomb_padded_past_window_documented_limit(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-fork-bomb-padded-past-window-documented-limit"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    tail = ":|:& };:"
    window_right = _read_fork_bomb_window_right()
    padding = " " * (window_right - len(tail) + 1)  # one char past the window edge
    command = ":(){" + padding + tail
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": command}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_allowed(name, out):
        ok(name)


# --- ROUND-3 CRITICAL-2: brace-expansion argv0 bypass -----------------------

def test_safe_shell_guard_blocks_brace_expansion_argv0_leading(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-brace-expansion-argv0-leading"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "{r,}m -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_brace_expansion_argv0_trailing(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-brace-expansion-argv0-trailing"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "r{m,} -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_brace_expansion_argv0_empty_alt(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-brace-expansion-argv0-empty-alt"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "{,}rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_root_target_alongside_unrelated_brace_arg(tmp_dir: Path) -> None:
    """Regression guard for a bypass that CRITICAL-2's initial implementation
    (broadening the shared `_looks_dynamic` used by both argv0 detection AND
    `_parse_rm_invocation`'s target scan) would otherwise introduce: an
    incidental brace-expansion-shaped TARGET token (e.g. `{x,y}`) marking
    the whole rm invocation "has_dynamic" and suppressing the root-target
    check for an ADJACENT literal `/` target too. `_parse_rm_invocation`
    must keep using the narrower $/backtick-only `_looks_dynamic_substitution`
    for targets, not the broadened argv0-only `_looks_dynamic`."""
    name = "safe-shell-guard/blocks-root-target-alongside-unrelated-brace-arg"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "rm -rf / {x,y}"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


# --- ROUND-3 CRITICAL-3: nice/nohup/timeout own-flags bypass ---------------

def test_safe_shell_guard_blocks_nice_n_flag_space_separated(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-nice-n-flag-space-separated"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "nice -n 19 rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_nice_adjustment_flag_equals_joined(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-nice-adjustment-flag-equals-joined"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "nice --adjustment=19 rm -rf /"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_timeout_signal_flag_equals_joined(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-timeout-signal-flag-equals-joined"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "timeout --signal=KILL 5 rm -rf /"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_timeout_kill_after_flag_space_separated(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-timeout-kill-after-flag-space-separated"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "timeout -k 5 30 rm -rf /"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_nohup_double_dash_flag(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-nohup-double-dash-flag"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "nohup -- rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_allows_nice_n_flag_benign(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-nice-n-flag-benign-command"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "nice -n 10 git status"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_allowed(name, out):
        ok(name)


def test_safe_shell_guard_allows_timeout_plain_benign(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-timeout-plain-benign-command"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "timeout 5 npm test"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_allowed(name, out):
        ok(name)


# --- ROUND-3 HIGH: watch/ssh/su/chroot/flock wrapper forms bypass entirely --

def test_safe_shell_guard_blocks_watch_wrapped_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-watch-wrapped-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "watch -n1 rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_ssh_wrapped_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-ssh-wrapped-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "ssh localhost rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_su_c_wrapped_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-su-c-wrapped-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": 'su -c "rm -rf /"'},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_blocks_chroot_wrapped_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-chroot-wrapped-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "chroot / rm -rf /"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_allows_chroot_wrapped_non_root_target_by_design(tmp_dir: Path) -> None:
    """chroot's own newroot argument ("/") is correctly skipped as the
    wrapper's positional argument (not misread as rm's target); the wrapped
    `rm -rf /tmp` is then checked under this guard's EXISTING, unchanged,
    root-target-only scope (see _is_root_target docstring) -- same as an
    unwrapped `rm -rf /tmp` would be. Expanding rm-target scope beyond the
    literal filesystem root is explicitly out of scope for this round."""
    name = "safe-shell-guard/allows-chroot-wrapped-non-root-target-by-design"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "chroot / rm -rf /tmp"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_allowed(name, out):
        ok(name)


def test_safe_shell_guard_blocks_flock_wrapped_rm_rf_root(tmp_dir: Path) -> None:
    name = "safe-shell-guard/blocks-flock-wrapped-rm-rf-root"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "flock /tmp/x rm -rf /"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_denied(name, out):
        ok(name)


def test_safe_shell_guard_allows_ssh_wrapped_benign(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-ssh-wrapped-benign-command"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(tmp_dir),
        "tool_input": {"command": "ssh someuser@host git pull"},
    }
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_allowed(name, out):
        ok(name)


def test_safe_shell_guard_allows_su_without_c_flag(tmp_dir: Path) -> None:
    name = "safe-shell-guard/allows-su-without-c-flag"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "cwd": str(tmp_dir), "tool_input": {"command": "su - someuser"}}
    _, out = run_hook("craftflow_safe_shell_guard.py", payload, env)
    if _assert_allowed(name, out):
        ok(name)


def test_hooks_json_registers_safe_shell_guard() -> None:
    name = "hooks/safe-shell-guard-registered-alongside-bash-guard"
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    if not path.exists():
        fail(name, f"hooks.json not found at {path}")
        return
    hooks = json.loads(path.read_text(encoding="utf-8"))
    pre_hooks = hooks.get("hooks", {}).get("PreToolUse", [])
    bash_entries = [entry for entry in pre_hooks if entry.get("matcher") == "Bash"]
    if len(bash_entries) != 1:
        fail(name, f"expected exactly 1 PreToolUse entry with matcher 'Bash'; found {len(bash_entries)}")
        return
    commands = [h.get("command", "") for h in bash_entries[0].get("hooks", [])]
    if not any("craftflow_pretooluse_bash_guard" in c for c in commands):
        fail(name, "existing craftflow_pretooluse_bash_guard.py registration was removed or moved")
        return
    if not any("craftflow_safe_shell_guard" in c for c in commands):
        fail(name, "craftflow_safe_shell_guard.py not registered alongside craftflow_pretooluse_bash_guard.py")
        return
    if len(commands) < 2:
        fail(name, f"expected >=2 hooks in the Bash matcher entry; found {len(commands)}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# Stop-verify gate tests (Workstream B — concept-ported from
# xai-org/grok-build's xai-grok-hooks stop-verify guard, Apache-2.0)
# ---------------------------------------------------------------------------

def _write_stop_verify_config(plugin_copy: Path, config: dict) -> None:
    config_dir = plugin_copy / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "stop-verify.json").write_text(json.dumps(config), encoding="utf-8")


def test_stop_verify_inert_when_unconfigured(tmp_dir: Path) -> None:
    name = "stop-verify/inert-when-unconfigured"
    env = {"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT), "CLAUDE_PROJECT_DIR": str(tmp_dir)}
    code, out = run_hook("craftflow_stop_verify.py", {}, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0 when unconfigured")
        return
    if '"decision": "block"' in out or '"decision":"block"' in out:
        fail(name, f"expected no block when the verify gate is unconfigured; got: {out!r}")
        return
    ok(name)


def test_stop_verify_allows_when_command_passes(tmp_dir: Path) -> None:
    name = "stop-verify/allows-when-command-passes"
    plugin_copy = tmp_dir / "plugin"
    shutil.copytree(PLUGIN_ROOT, plugin_copy)
    _write_stop_verify_config(plugin_copy, {"enabled": True, "command": "true"})
    env = {"CLAUDE_PLUGIN_ROOT": str(plugin_copy), "CLAUDE_PROJECT_DIR": str(tmp_dir)}
    code, out = run_hook("craftflow_stop_verify.py", {}, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return
    if '"decision": "block"' in out or '"decision":"block"' in out:
        fail(name, f"expected no block when the configured command exits 0; got: {out!r}")
        return
    ok(name)


def test_stop_verify_blocks_when_command_fails(tmp_dir: Path) -> None:
    name = "stop-verify/blocks-when-command-fails"
    plugin_copy = tmp_dir / "plugin"
    shutil.copytree(PLUGIN_ROOT, plugin_copy)
    _write_stop_verify_config(plugin_copy, {"enabled": True, "command": "false"})
    env = {"CLAUDE_PLUGIN_ROOT": str(plugin_copy), "CLAUDE_PROJECT_DIR": str(tmp_dir)}
    code, out = run_hook("craftflow_stop_verify.py", {}, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0 (Stop hooks block via JSON decision, not exit code)")
        return
    if '"decision": "block"' not in out and '"decision":"block"' not in out:
        fail(name, f"expected a block decision when the configured verify command fails; got: {out!r}")
        return
    ok(name)


def test_stop_verify_never_blocks_on_continuation_stop(tmp_dir: Path) -> None:
    name = "stop-verify/never-blocks-on-stop-hook-active"
    plugin_copy = tmp_dir / "plugin"
    shutil.copytree(PLUGIN_ROOT, plugin_copy)
    _write_stop_verify_config(plugin_copy, {"enabled": True, "command": "false"})
    env = {"CLAUDE_PLUGIN_ROOT": str(plugin_copy), "CLAUDE_PROJECT_DIR": str(tmp_dir)}
    code, out = run_hook("craftflow_stop_verify.py", {"stop_hook_active": True}, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return
    if '"decision": "block"' in out or '"decision":"block"' in out:
        fail(name, f"expected no block on a continuation stop (stop_hook_active=True), even with a failing command; got: {out!r}")
        return
    ok(name)


def test_hooks_json_registers_stop_verify() -> None:
    name = "hooks/stop-verify-registered"
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    if not path.exists():
        fail(name, f"hooks.json not found at {path}")
        return
    hooks = json.loads(path.read_text(encoding="utf-8"))
    stop_hooks = hooks.get("hooks", {}).get("Stop", [])
    commands = [h.get("command", "") for entry in stop_hooks for h in entry.get("hooks", [])]
    if not any("craftflow_stop_persist" in c for c in commands):
        fail(name, "existing craftflow_stop_persist.py registration was removed or moved")
        return
    if not any("craftflow_stop_verify" in c for c in commands):
        fail(name, "craftflow_stop_verify.py not registered under Stop")
        return
    ok(name)


# ---------------------------------------------------------------------------
# Hook trust/provenance gate tests (Workstream B — concept-ported from
# xai-org/grok-build's xai-grok-hooks trust.rs, Apache-2.0)
# ---------------------------------------------------------------------------

def test_hook_trust_refuses_unknown_script(tmp_dir: Path) -> None:
    name = "hook-trust/refuses-script-not-in-manifest"
    tmp_dir.mkdir(parents=True)
    project_root = tmp_dir / "project"
    project_root.mkdir()
    target_script = project_root / "some_hook.py"
    target_script.write_text("VALUE = 1\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_hook_trust.py"), "check", str(target_script),
         "--project-root", str(project_root)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        fail(name, f"expected non-zero (untrusted) exit for a script absent from any manifest; got 0, stdout={result.stdout!r}")
        return
    if '"trusted": false' not in result.stdout and '"trusted":false' not in result.stdout:
        fail(name, f"expected trusted:false in stdout; got: {result.stdout!r}")
        return
    ok(name)


def test_hook_trust_allows_manifest_listed_matching_hash(tmp_dir: Path) -> None:
    name = "hook-trust/allows-manifest-listed-matching-hash"
    import hashlib
    tmp_dir.mkdir(parents=True)
    project_root = tmp_dir / "project"
    project_root.mkdir()
    target_script = project_root / "some_hook.py"
    content = "VALUE = 1\n"
    target_script.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    manifest_path = project_root / ".craftflow" / "hook-trust.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps({"scripts": {"some_hook.py": digest}}), encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_hook_trust.py"), "check", str(target_script),
         "--project-root", str(project_root)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(name, f"expected exit 0 (trusted) for a manifest-listed matching-hash script; got {result.returncode}, stdout={result.stdout!r}")
        return
    if '"trusted": true' not in result.stdout and '"trusted":true' not in result.stdout:
        fail(name, f"expected trusted:true in stdout; got: {result.stdout!r}")
        return
    ok(name)


def test_hook_trust_refuses_hash_mismatch(tmp_dir: Path) -> None:
    name = "hook-trust/refuses-hash-mismatch-tampered-script"
    import hashlib
    tmp_dir.mkdir(parents=True)
    project_root = tmp_dir / "project"
    project_root.mkdir()
    target_script = project_root / "some_hook.py"
    target_script.write_text("VALUE = 1  # tampered after manifest was generated\n", encoding="utf-8")
    stale_digest = hashlib.sha256(b"VALUE = 1\n").hexdigest()
    manifest_path = project_root / ".craftflow" / "hook-trust.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps({"scripts": {"some_hook.py": stale_digest}}), encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_hook_trust.py"), "check", str(target_script),
         "--project-root", str(project_root)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        fail(name, f"expected non-zero (untrusted) exit for a hash-mismatched (tampered) script; got 0")
        return
    if '"trusted": false' not in result.stdout and '"trusted":false' not in result.stdout:
        fail(name, f"expected trusted:false in stdout for a hash mismatch; got: {result.stdout!r}")
        return
    ok(name)


def test_hook_trust_update_generates_manifest_then_check_passes(tmp_dir: Path) -> None:
    name = "hook-trust/update-generates-manifest-then-check-passes"
    tmp_dir.mkdir(parents=True)
    project_root = tmp_dir / "project"
    scripts_dir = project_root / "scripts"
    scripts_dir.mkdir(parents=True)
    script_a = scripts_dir / "craftflow_scratch_a.py"
    script_a.write_text("VALUE = 1\n", encoding="utf-8")
    update_result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_hook_trust.py"), "update",
         "--project-root", str(project_root), "--scripts-dir", str(scripts_dir)],
        capture_output=True,
        text=True,
    )
    if update_result.returncode != 0:
        fail(name, f"expected 'update' to exit 0; got {update_result.returncode}, stderr={update_result.stderr!r}")
        return
    manifest_path = project_root / ".craftflow" / "hook-trust.json"
    if not manifest_path.exists():
        fail(name, f"expected manifest to be written at {manifest_path}")
        return
    check_result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_hook_trust.py"), "check", str(script_a),
         "--project-root", str(project_root)],
        capture_output=True,
        text=True,
    )
    if check_result.returncode != 0:
        fail(name, f"expected the freshly-manifested script to be trusted; got {check_result.returncode}, stdout={check_result.stdout!r}")
        return
    ok(name)


def test_hook_trust_never_imports_hooklib_directly() -> None:
    name = "hook-trust/never-imports-hooklib"
    path = SCRIPTS / "craftflow_hook_trust.py"
    if not path.exists():
        fail(name, f"craftflow_hook_trust.py not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    if "import craftflow_hooklib" in content or "from craftflow_hooklib" in content:
        fail(name, "craftflow_hook_trust.py must not import craftflow_hooklib directly (it may be the very script under trust evaluation)")
        return
    ok(name)


# ---------------------------------------------------------------------------
# Context usage (Thread E — craftflow's own context awareness)
# ---------------------------------------------------------------------------

def test_context_usage_returns_none_when_tokentracker_not_installed() -> None:
    name = "context-usage/none-when-not-installed"
    real_which = status_report.shutil.which
    status_report.shutil.which = lambda _name: None
    try:
        result = status_report._context_usage()
    finally:
        status_report.shutil.which = real_which
    if result is not None:
        fail(name, f"expected None when tokentracker is not on PATH; got {result!r}")
        return
    ok(name)


def test_context_usage_returns_percent_full_on_installed_success() -> None:
    name = "context-usage/parses-percent-full-on-success"
    real_which = status_report.shutil.which
    real_run = status_report.subprocess.run
    status_report.shutil.which = lambda _name: "/usr/local/bin/tokentracker"

    class _FakeResult:
        returncode = 0
        stdout = json.dumps({"views": [{"percentFull": 0.42, "total": 84000, "modelContext": 200000}]})
        stderr = ""

    status_report.subprocess.run = lambda *a, **kw: _FakeResult()
    try:
        result = status_report._context_usage()
    finally:
        status_report.shutil.which = real_which
        status_report.subprocess.run = real_run
    if result != {"percent_full": 0.42, "total": 84000, "model_context": 200000}:
        fail(name, f"expected parsed percent_full/total/model_context; got {result!r}")
        return
    ok(name)


def test_context_usage_returns_none_on_timeout() -> None:
    name = "context-usage/none-on-timeout"
    real_which = status_report.shutil.which
    real_run = status_report.subprocess.run
    status_report.shutil.which = lambda _name: "/usr/local/bin/tokentracker"

    def _raise_timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="tokentracker", timeout=2)

    status_report.subprocess.run = _raise_timeout
    try:
        result = status_report._context_usage()
    finally:
        status_report.shutil.which = real_which
        status_report.subprocess.run = real_run
    if result is not None:
        fail(name, f"expected None on subprocess timeout; got {result!r}")
        return
    ok(name)


def test_context_usage_returns_none_on_non_zero_exit() -> None:
    name = "context-usage/none-on-non-zero-exit"
    real_which = status_report.shutil.which
    real_run = status_report.subprocess.run
    status_report.shutil.which = lambda _name: "/usr/local/bin/tokentracker"

    class _FakeResult:
        returncode = 1
        stdout = ""
        stderr = "tokentracker: no active session"

    status_report.subprocess.run = lambda *a, **kw: _FakeResult()
    try:
        result = status_report._context_usage()
    finally:
        status_report.shutil.which = real_which
        status_report.subprocess.run = real_run
    if result is not None:
        fail(name, f"expected None on non-zero exit; got {result!r}")
        return
    ok(name)


def test_context_usage_returns_none_on_malformed_json() -> None:
    name = "context-usage/none-on-malformed-json"
    real_which = status_report.shutil.which
    real_run = status_report.subprocess.run
    status_report.shutil.which = lambda _name: "/usr/local/bin/tokentracker"

    class _FakeResult:
        returncode = 0
        stdout = "not valid json {{{"
        stderr = ""

    status_report.subprocess.run = lambda *a, **kw: _FakeResult()
    try:
        result = status_report._context_usage()
    finally:
        status_report.shutil.which = real_which
        status_report.subprocess.run = real_run
    if result is not None:
        fail(name, f"expected None on malformed JSON stdout; got {result!r}")
        return
    ok(name)


def test_precompact_context_usage_budget_stays_under_registered_hook_timeout() -> None:
    # Drift-guard, mirroring craftflow_hook_selfcheck.py's own
    # test_selfcheck_internal_budget_stays_under_registered_hook_timeout: ties
    # craftflow_precompact_state.py's own subprocess-timeout budget to the
    # REAL registered PreCompact timeout in hooks/hooks.json, so a future
    # change to either side without updating the other fails the suite
    # instead of silently reopening a hook-timeout-exceeding risk.
    name = "precompact-state/context-usage-budget-under-registered-timeout"
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    if not path.exists():
        fail(name, f"hooks.json not found at {path}")
        return
    hooks = json.loads(path.read_text(encoding="utf-8"))
    precompact_hooks = hooks.get("hooks", {}).get("PreCompact", [])
    registered_timeout = None
    for entry in precompact_hooks:
        for h in entry.get("hooks", []):
            if "craftflow_precompact_state" in h.get("command", ""):
                registered_timeout = h.get("timeout")
    if registered_timeout is None:
        fail(name, "could not find a registered timeout for craftflow_precompact_state in hooks/hooks.json")
        return
    budget = precompact_state.PRECOMPACT_CONTEXT_USAGE_TIMEOUT_SECONDS
    min_margin_seconds = 1
    if budget + min_margin_seconds > registered_timeout:
        fail(
            name,
            f"context-usage subprocess budget ({budget}s) leaves less than {min_margin_seconds}s "
            f"margin under the registered PreCompact hook timeout ({registered_timeout}s)",
        )
        return
    ok(name)


def test_postcompact_context_usage_budget_stays_under_registered_hook_timeout() -> None:
    # Companion drift-guard for the PostCompact side.
    name = "postcompact-context/context-usage-budget-under-registered-timeout"
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    if not path.exists():
        fail(name, f"hooks.json not found at {path}")
        return
    hooks = json.loads(path.read_text(encoding="utf-8"))
    postcompact_hooks = hooks.get("hooks", {}).get("PostCompact", [])
    registered_timeout = None
    for entry in postcompact_hooks:
        for h in entry.get("hooks", []):
            if "craftflow_postcompact_context" in h.get("command", ""):
                registered_timeout = h.get("timeout")
    if registered_timeout is None:
        fail(name, "could not find a registered timeout for craftflow_postcompact_context in hooks/hooks.json")
        return
    budget = postcompact_context.POSTCOMPACT_CONTEXT_USAGE_TIMEOUT_SECONDS
    min_margin_seconds = 1
    if budget + min_margin_seconds > registered_timeout:
        fail(
            name,
            f"context-usage subprocess budget ({budget}s) leaves less than {min_margin_seconds}s "
            f"margin under the registered PostCompact hook timeout ({registered_timeout}s)",
        )
        return
    ok(name)


def test_report_statusline_appends_ctx_segment_when_available() -> None:
    name = "status-report/statusline-appends-ctx-when-available"
    real_ctx = status_report._context_usage
    status_report._context_usage = lambda *a, **kw: {"percent_full": 0.42, "total": 84000, "model_context": 200000}
    try:
        line = status_report._report_statusline("wf-test-ctx-1", {"phase_cursor": "phase_1"})
    finally:
        status_report._context_usage = real_ctx
    if "ctx:42%" not in line:
        fail(name, f"expected 'ctx:42%' segment in statusline; got {line!r}")
        return
    ok(name)


def test_report_statusline_omits_ctx_segment_when_unavailable() -> None:
    name = "status-report/statusline-omits-ctx-when-unavailable"
    real_ctx = status_report._context_usage
    status_report._context_usage = lambda *a, **kw: None
    try:
        line = status_report._report_statusline("wf-test-ctx-2", {"phase_cursor": "phase_1"})
    finally:
        status_report._context_usage = real_ctx
    if "ctx:" in line:
        fail(name, f"expected no ctx segment when unavailable; got {line!r}")
        return
    ok(name)


def test_report_statusline_colors_ctx_segment_red_at_critical() -> None:
    name = "status-report/statusline-colors-ctx-red-at-critical"
    real_ctx = status_report._context_usage
    status_report._context_usage = lambda *a, **kw: {"percent_full": 0.95, "total": 190000, "model_context": 200000}
    try:
        line = status_report._report_statusline("wf-test-ctx-3", {"phase_cursor": "phase_1"})
    finally:
        status_report._context_usage = real_ctx
    if status_report._ANSI_RED not in line:
        fail(name, f"expected red ANSI code for critical ctx%; got {line!r}")
        return
    ok(name)


def test_build_json_output_includes_context_usage_key() -> None:
    name = "status-report/json-output-includes-context-usage"
    real_ctx = status_report._context_usage
    status_report._context_usage = lambda *a, **kw: {"percent_full": 0.5, "total": 100000, "model_context": 200000}
    try:
        out = status_report._build_json_output("wf-test-ctx-4", {"phase_cursor": "phase_1"})
    finally:
        status_report._context_usage = real_ctx
    if out.get("context_usage") != {"percent_full": 0.5, "total": 100000, "model_context": 200000}:
        fail(name, f"expected context_usage key with parsed value; got {out.get('context_usage')!r}")
        return
    ok(name)


def test_build_json_output_context_usage_none_when_unavailable() -> None:
    name = "status-report/json-output-context-usage-none-when-unavailable"
    real_ctx = status_report._context_usage
    status_report._context_usage = lambda *a, **kw: None
    try:
        out = status_report._build_json_output("wf-test-ctx-5", {"phase_cursor": "phase_1"})
    finally:
        status_report._context_usage = real_ctx
    if out.get("context_usage") is not None:
        fail(name, f"expected context_usage=None when unavailable; got {out.get('context_usage')!r}")
        return
    ok(name)


def test_precompact_build_snapshot_includes_context_usage_when_available() -> None:
    name = "precompact-state/build-snapshot-includes-context-usage"
    payload = {
        "workflow_uuid": "wf-test-pc-1",
        "workflow_type": "BUILD",
        "phase_cursor": "phase_1",
        "phase_status": {},
        "plan_file": None,
    }
    ctx = {"percent_full": 0.71, "total": 142000, "model_context": 200000}
    snapshot = precompact_state._build_snapshot(payload, "auto", ctx)
    if snapshot.get("context_usage") != ctx:
        fail(name, f"expected context_usage={ctx!r} in snapshot; got {snapshot.get('context_usage')!r}")
        return
    if snapshot.get("workflow_uuid") != "wf-test-pc-1":
        fail(name, f"expected workflow_uuid preserved; got {snapshot.get('workflow_uuid')!r}")
        return
    ok(name)


def test_precompact_build_snapshot_context_usage_none_when_unavailable() -> None:
    name = "precompact-state/build-snapshot-context-usage-none-when-unavailable"
    payload = {
        "workflow_uuid": "wf-test-pc-2",
        "workflow_type": "BUILD",
        "phase_cursor": "phase_1",
        "phase_status": {},
        "plan_file": None,
    }
    snapshot = precompact_state._build_snapshot(payload, "auto", None)
    if snapshot.get("context_usage") is not None:
        fail(name, f"expected context_usage=None; got {snapshot.get('context_usage')!r}")
        return
    if snapshot.get("trigger") != "auto":
        fail(name, f"expected trigger preserved; got {snapshot.get('trigger')!r}")
        return
    ok(name)


def test_postcompact_build_event_includes_context_usage_when_available() -> None:
    name = "postcompact-context/build-event-includes-context-usage"
    ctx = {"percent_full": 0.15, "total": 30000, "model_context": 200000}
    event = postcompact_context._build_event("wf-test-po-1", "auto", "compacted 3 turns", ctx)
    if event.get("context_usage") != ctx:
        fail(name, f"expected context_usage={ctx!r} in event; got {event.get('context_usage')!r}")
        return
    if event.get("details") != "compacted 3 turns":
        fail(name, f"expected details preserved; got {event.get('details')!r}")
        return
    ok(name)


def test_postcompact_build_event_context_usage_none_when_unavailable() -> None:
    name = "postcompact-context/build-event-context-usage-none-when-unavailable"
    event = postcompact_context._build_event("wf-test-po-2", "auto", "", None)
    if event.get("context_usage") is not None:
        fail(name, f"expected context_usage=None; got {event.get('context_usage')!r}")
        return
    if event.get("event") != "compact_occurred":
        fail(name, f"expected event type preserved; got {event.get('event')!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# craftflow_memory_merge: CLI-level provenance smoke test
# ---------------------------------------------------------------------------

def test_memory_merge_cli_accepts_provenance_field_on_notes() -> None:
    name = "memory-merge/cli-accepts-provenance-field"
    payload = {
        "section_text": "- Existing hand-verified rule (conf: 0.9, organic)",
        "notes": [{"text": "Existing hand-verified rule", "confidence": 1.0, "provenance": "imported"}],
    }
    code, out = run_hook("craftflow_memory_merge.py", payload)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return
    if "(conf: 0.9, organic)" not in out:
        fail(name, f"organic bullet not preserved via CLI: {out}")
        return
    if out.count("Existing hand-verified rule") != 2:
        fail(name, f"expected organic original kept + imported note appended separately, got: {out}")
        return
    ok(name)


def test_memory_merge_cli_non_numeric_confidence_fails_cleanly() -> None:
    name = "memory-merge/cli-non-numeric-confidence-fails-cleanly"
    payload = {
        "section_text": "- old (conf: 0.8)",
        "notes": [{"text": "new", "confidence": "high"}],
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_memory_merge.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=os.environ,
    )
    if result.returncode != 1:
        fail(name, f"exit code {result.returncode}; expected 1. stderr={result.stderr!r}")
        return
    if "Error:" not in result.stderr:
        fail(name, f"expected a clean 'Error:' message on stderr, got: {result.stderr!r}")
        return
    if "Traceback" in result.stderr:
        fail(name, f"expected no raw traceback on stderr, got: {result.stderr!r}")
        return
    ok(name)


def test_memory_merge_cli_non_integer_max_bullets_fails_cleanly() -> None:
    name = "memory-merge/cli-non-integer-max-bullets-fails-cleanly"
    payload = {
        "section_text": "- one (conf: 0.8)\n- two (conf: 0.8)",
        "notes": [],
        "max_bullets": "five",
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_memory_merge.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=os.environ,
    )
    if result.returncode != 1:
        fail(name, f"exit code {result.returncode}; expected 1. stderr={result.stderr!r}")
        return
    if "Error:" not in result.stderr:
        fail(name, f"expected a clean 'Error:' message on stderr, got: {result.stderr!r}")
        return
    if "Traceback" in result.stderr:
        fail(name, f"expected no raw traceback on stderr, got: {result.stderr!r}")
        return
    ok(name)


def test_memory_merge_cli_empty_section_with_file_text_fails_cleanly() -> None:
    name = "memory-merge/cli-empty-section-with-file-text-fails-cleanly"
    payload = {
        "file_text": "## Gotchas\n- existing (conf: 0.8)\n",
        "section": "",
        "notes": [{"text": "new note", "confidence": 0.9}],
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_memory_merge.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=os.environ,
    )
    if result.returncode != 1:
        fail(
            name,
            f"exit code {result.returncode}; expected 1 (empty 'section' must not silently "
            f"fall back to the legacy branch and discard file_text). stdout={result.stdout!r}",
        )
        return
    if "Error:" not in result.stderr:
        fail(name, f"expected a clean 'Error:' message on stderr, got: {result.stderr!r}")
        return
    if "section" not in result.stderr.lower():
        fail(name, f"expected error message to mention 'section', got: {result.stderr!r}")
        return
    if "## Gotchas" in result.stdout or "existing" in result.stdout:
        fail(name, f"expected no stdout output on error, but file_text content leaked through: {result.stdout!r}")
        return
    ok(name)


def test_memory_merge_cli_nan_confidence_fails_cleanly() -> None:
    name = "memory-merge/cli-nan-confidence-fails-cleanly"
    payload = {
        "section_text": "- old (conf: 0.8)",
        "notes": [{"text": "new", "confidence": float("nan")}],
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_memory_merge.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=os.environ,
    )
    if result.returncode != 1:
        fail(
            name,
            f"exit code {result.returncode}; expected 1 (NaN confidence must not bypass the "
            f"0.7 drop threshold). stdout={result.stdout!r}",
        )
        return
    if "Error:" not in result.stderr:
        fail(name, f"expected a clean 'Error:' message on stderr, got: {result.stderr!r}")
        return
    if "Traceback" in result.stderr:
        fail(name, f"expected no raw traceback on stderr, got: {result.stderr!r}")
        return
    if "nan" in result.stdout.lower():
        fail(name, f"NaN confidence must never be embedded in persisted output: {result.stdout!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# craftflow_memory_merge: archive-aware bullet eviction (Phase 5)
# ---------------------------------------------------------------------------

def test_memory_merge_apply_cap_backward_compatible_without_archive() -> None:
    name = "memory-merge/apply-cap/backward-compatible-no-archive"
    bullets = [f"- entry {i} (conf: 0.8)" for i in range(10)]
    kept, archived = memory_merge.apply_cap_with_archive(bullets, max_bullets=6, archive=False)
    if archived:
        fail(name, "archive=False must never return archived bullets")
        return
    if kept != memory_merge.apply_cap(bullets, max_bullets=6):
        fail(name, "kept output must match legacy apply_cap exactly when archive=False")
        return
    ok(name)


def test_memory_merge_apply_cap_archives_instead_of_dropping() -> None:
    name = "memory-merge/apply-cap/archives-instead-of-dropping"
    bullets = [f"- entry {i} (conf: 0.8)" for i in range(10)]
    kept, archived = memory_merge.apply_cap_with_archive(bullets, max_bullets=6, archive=True)
    if len(archived) != 4:
        fail(name, f"expected 4 archived bullets, got {len(archived)}")
        return
    # Losslessness: archived + kept (minus the pointer) == original set.
    kept_without_pointer = [b for b in kept if "archived: see" not in b]
    if set(archived) | set(kept_without_pointer) != set(bullets):
        fail(name, "archived + kept must reconstruct the original bullet set exactly")
        return
    if not any("archived: see" in b and "organic" in b for b in kept):
        fail(name, "expected an organic pointer bullet in the live section")
        return
    ok(name)


def test_memory_merge_apply_cap_archive_preserves_organic_priority() -> None:
    name = "memory-merge/apply-cap/archive-preserves-organic-priority"
    bullets = ["- organic entry (conf: 0.9, organic)"] + [
        f"- imported {i} (conf: 0.8)" for i in range(9)
    ]
    kept, archived = memory_merge.apply_cap_with_archive(bullets, max_bullets=5, archive=True)
    if any("organic entry" in b for b in archived):
        fail(name, "organic bullet must never be archived while imported bullets remain")
        return
    ok(name)


def test_memory_merge_cli_without_archive_field_unchanged_output() -> None:
    name = "memory-merge/cli/without-archive-field-unchanged"
    payload = {
        "file_text": "## Common Gotchas\n" + "\n".join(f"- entry {i}" for i in range(10)) + "\n\n## Last Updated\n2026-01-01\n",
        "section": "Common Gotchas",
        "notes": [{"text": "new note", "confidence": 0.9}],
        "max_bullets": 5,
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_memory_merge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit {result.returncode}: {result.stderr}")
        return
    # Plain text output (not JSON) -- unchanged shape.
    try:
        json.loads(result.stdout)
        fail(name, "output must stay plain text when 'archive' is not supplied")
        return
    except (json.JSONDecodeError, ValueError):
        pass
    ok(name)


def test_memory_merge_cli_with_archive_field_emits_json_envelope() -> None:
    name = "memory-merge/cli/with-archive-field-emits-envelope"
    payload = {
        "file_text": "## Common Gotchas\n" + "\n".join(f"- entry {i}" for i in range(10)) + "\n\n## Last Updated\n2026-01-01\n",
        "section": "Common Gotchas",
        "notes": [],
        "max_bullets": 5,
        "archive": {"dir_rel": ".craftflow/state/project/archive", "section_slug": "common-gotchas", "month": "2026-08"},
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_memory_merge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit {result.returncode}: {result.stderr}")
        return
    try:
        out = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        fail(name, "expected a JSON envelope when 'archive' is supplied")
        return
    if "file_text" not in out or "archived_bullets" not in out or "archive_path" not in out:
        fail(name, f"envelope missing required keys: {out.keys()}")
        return
    if not out["archive_path"].endswith("common-gotchas-2026-08.md"):
        fail(name, f"unexpected archive_path: {out['archive_path']}")
        return
    ok(name)


def test_memory_merge_archive_rotation_zero_data_loss_realistic_fixture() -> None:
    name = "memory-merge/archive/zero-data-loss-realistic-fixture"
    bullets = [f"- gotcha entry {i} (conf: 0.8)" for i in range(80)]
    file_text = "## Common Gotchas\n" + "\n".join(bullets) + "\n\n## Last Updated\n2026-01-01\n"
    payload = {
        "file_text": file_text,
        "section": "Common Gotchas",
        "notes": [],
        "max_bullets": 60,
        "archive": {"dir_rel": ".craftflow/state/project/archive", "section_slug": "common-gotchas", "month": "2026-08"},
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_memory_merge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit {result.returncode}: {result.stderr}")
        return
    try:
        out = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        fail(name, f"expected a JSON envelope, got: {result.stdout[:300]}")
        return
    archived_bullets = out.get("archived_bullets", [])
    if len(archived_bullets) != 20:
        fail(name, f"expected 20 archived bullets, got {len(archived_bullets)}")
        return
    span = memory_merge._find_section_span(out["file_text"], "Common Gotchas")
    if span is None:
        fail(name, "Common Gotchas section not found in returned file_text")
        return
    kept_bullets = hooklib.extract_bullets(out["file_text"][span[0]:span[1]])
    if len(kept_bullets) != 61:
        fail(name, f"expected 61 kept bullets (60 kept + 1 pointer), got {len(kept_bullets)}")
        return
    kept_without_pointer = [b for b in kept_bullets if "archived: see" not in b]
    if set(archived_bullets) | set(kept_without_pointer) != set(bullets):
        fail(name, "archived + kept must reconstruct the original 80-bullet set exactly (zero data loss)")
        return
    ok(name)


# ---------------------------------------------------------------------------
# REM-FIX cycle 4 (silent-failure-hunter, live-reproduced 8x CRITICAL): the
# same root-cause class already fixed twice for the `workflow` variable
# (latest_workflow_payload() only guarantees valid JSON, not that the top
# level is a dict) was ALSO present in three other places, all reachable
# BEFORE either guard script's own deny logic ever runs, with no top-level
# try/except in either main() to catch the fallout:
#   1. load_input()'s success path returned whatever json.loads(raw)
#      produced -- a non-dict stdin top level (e.g. `[1, 2, 3]`) crashed the
#      very first `data.get(...)` call in main().
#   2. load_mode()'s success path had the identical gap -- a non-dict
#      hook-mode.json crashed the first `mode.get(...)` call downstream.
#   3. Both scripts' `tool_input = data.get("tool_input") or {}` (or the
#      inline equivalent) only substitutes on a FALSY value -- a truthy
#      non-dict like `["a"]` survived untouched and crashed on the next
#      `.get()` call.
# All three degrade gracefully now (load_input()/load_mode() coerce a
# non-dict parse result to {}; tool_input extraction uses an explicit
# isinstance check instead of `or {}`), matching this codebase's existing
# convention for missing/malformed top-level input.
# ---------------------------------------------------------------------------

def test_pretooluse_guard_non_dict_stdin_top_level_does_not_crash_degrades_to_allow(tmp_dir: Path) -> None:
    name = "pretooluse-guard/non-dict-stdin-top-level-does-not-crash-degrades-to-allow"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    code, out = run_hook("craftflow_pretooluse_guard.py", [1, 2, 3], env)
    if code != 0:
        fail(
            name,
            f"expected exit 0 (graceful degrade, not a crash) for a non-dict stdin top level; "
            f"got exit={code} stdout={out!r}",
        )
        return
    if out:
        fail(
            name,
            f"expected allow (empty stdout) for a non-dict stdin top level, matching the "
            f"missing-stdin default; got: {out!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_non_dict_hook_mode_json_does_not_crash_degrades_to_fail_closed_deny(tmp_dir: Path) -> None:
    name = "pretooluse-guard/non-dict-hook-mode-json-does-not-crash-degrades-to-fail-closed-deny"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text("[1, 2, 3]", encoding="utf-8")
    project_root = tmp_dir / "project"
    (project_root / ".craftflow" / "state" / "project").mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project_root)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(project_root.resolve()),
        "tool_input": {"command": "echo x | tee .craftflow/state/project/patterns.md"},
    }
    code, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if code != 0:
        fail(
            name,
            f"expected exit 0 (graceful degrade, not a crash) for a non-dict hook-mode.json top "
            f"level; got exit={code} stdout={out!r}",
        )
        return
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected fail-closed deny for a protected-memory-file write when hook-mode.json's "
            f"top level is non-dict; got: {out!r}",
        )
        return
    ok(name)


def test_pretooluse_guard_truthy_non_dict_tool_input_degrades_to_empty_dict_no_crash(tmp_dir: Path) -> None:
    name = "pretooluse-guard/truthy-non-dict-tool-input-degrades-to-empty-dict-no-crash"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Write", "tool_input": ["a"]}
    code, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if code != 0:
        fail(
            name,
            f"expected exit 0 (graceful degrade, not a crash) for a truthy non-dict tool_input; "
            f"got exit={code} stdout={out!r}",
        )
        return
    if out:
        fail(
            name,
            f"expected allow (empty stdout) when tool_input is a truthy non-dict value with no "
            f"file_path; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_non_dict_stdin_top_level_does_not_crash_degrades_to_allow(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/non-dict-stdin-top-level-does-not-crash-degrades-to-allow"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    code, out = run_hook("craftflow_pretooluse_bash_guard.py", [1, 2, 3], env)
    if code != 0:
        fail(
            name,
            f"expected exit 0 (graceful degrade, not a crash) for a non-dict stdin top level; "
            f"got exit={code} stdout={out!r}",
        )
        return
    if out:
        fail(
            name,
            f"expected allow (empty stdout) for a non-dict stdin top level, matching the "
            f"missing-stdin default; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_non_dict_hook_mode_json_does_not_crash_still_denies_destructive_command(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/non-dict-hook-mode-json-does-not-crash-still-denies-destructive-command"
    fake_plugin_root = tmp_dir / "plugin_root"
    (fake_plugin_root / "config").mkdir(parents=True)
    (fake_plugin_root / "config" / "hook-mode.json").write_text("[1, 2, 3]", encoding="utf-8")
    project = tmp_dir / "project"
    worktree = project / ".claude" / "worktrees" / "wf-test"
    worktree.mkdir(parents=True)
    env = {"CLAUDE_PLUGIN_ROOT": str(fake_plugin_root), "CLAUDE_PROJECT_DIR": str(project)}
    payload = {
        "tool_name": "Bash",
        "cwd": str(worktree.resolve()),
        "tool_input": {"command": "rm -f ../../.."},
    }
    code, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if code != 0:
        fail(
            name,
            f"expected exit 0 (graceful degrade, not a crash) for a non-dict hook-mode.json top "
            f"level; got exit={code} stdout={out!r}",
        )
        return
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(
            name,
            "expected deny for a traversal escaping the worktree cwd even when hook-mode.json's "
            f"top level is non-dict; got: {out!r}",
        )
        return
    ok(name)


def test_bash_guard_truthy_non_dict_tool_input_degrades_to_empty_dict_no_crash(tmp_dir: Path) -> None:
    name = "pretooluse-bash-guard/truthy-non-dict-tool-input-degrades-to-empty-dict-no-crash"
    project_root = tmp_dir / "project"
    project_root.mkdir(parents=True)
    env = {"CLAUDE_PROJECT_DIR": str(project_root), "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    payload = {"tool_name": "Bash", "tool_input": ["a"]}
    code, out = run_hook("craftflow_pretooluse_bash_guard.py", payload, env)
    if code != 0:
        fail(
            name,
            f"expected exit 0 (graceful degrade, not a crash) for a truthy non-dict tool_input; "
            f"got exit={code} stdout={out!r}",
        )
        return
    if out:
        fail(
            name,
            f"expected allow (empty stdout) when tool_input is a truthy non-dict value with no "
            f"command; got: {out!r}",
        )
        return
    ok(name)


def test_workspace_root_config_read_gated_inside_step_1a() -> None:
    # Phase 3b (backlog item 8) moved "## 0. Resolve Project Root" (incl. its 1a. branch)
    # out of craftflow-router/SKILL.md into skills/_shared/router-protocol.md's
    # "## Resolve Project Root" section. Every invariant this test protects now lives
    # there -- re-anchored to the shared doc, unchanged in substance.
    name = "router/workspace-root-config-read-gated-inside-step-1a"
    shared_path = PLUGIN_ROOT / "skills" / "_shared" / "router-protocol.md"
    if not shared_path.exists():
        fail(name, f"shared router-protocol.md not found at {shared_path}")
        return
    content = shared_path.read_text(encoding="utf-8")
    section_start = content.find("**1a. Multi-repo workspace root resolution**")
    if section_start == -1:
        fail(name, "step 1a section not found in shared doc")
        return
    next_heading = content.find("\n## ", section_start + 1)
    section = content[section_start: next_heading if next_heading != -1 else None]
    if "workspace_writable_paths" not in section:
        fail(name, "workspace_writable_paths read/parse not found inside step 1a's own section")
        return
    # Confirm it is NOT also present in the single-repo (TOPLEVEL_EXIT == 0) branch text, which
    # sits just above step 1a in the same 'Resolve Project Root' section.
    zero_section_start = content.find("## Resolve Project Root")
    single_repo_branch = content[zero_section_start:section_start]
    if "workspace_writable_paths" in single_repo_branch:
        fail(name, "workspace_writable_paths read must not appear in the single-repo (TOPLEVEL_EXIT == 0) branch")
        return
    # Fresh-review advisory fix (2026-08-13): confirm the capture block is specifically absent
    # from the "If RESOLVE_EXIT != 0" bullet's own text (the branch where $RESOLVE_RESULT is
    # empty/unparseable) -- not just generically "present somewhere in step 1a," which the checks
    # above already allowed even when the capture block was wrongly placed BEFORE this bullet in
    # the original draft. Slices from that bullet's own start to the "Otherwise" bullet's start.
    resolve_exit_bullet_start = section.find("**If `RESOLVE_EXIT != 0`**")
    otherwise_bullet_start = section.find("**Otherwise**, parse the outcome")
    if resolve_exit_bullet_start == -1 or otherwise_bullet_start == -1:
        fail(name, "could not locate the 'If RESOLVE_EXIT != 0' / 'Otherwise' bullets inside step 1a")
        return
    resolve_exit_failure_branch = section[resolve_exit_bullet_start:otherwise_bullet_start]
    if "workspace_writable_paths" in resolve_exit_failure_branch:
        fail(name, "workspace_writable_paths capture must not be reachable on the RESOLVE_EXIT != 0 (unparseable $RESOLVE_RESULT) path")
        return
    ok(name)


_EXPECTED_WORKSPACE_WRITABLE_PATHS_SUBSTITUTION_PARAGRAPH = (
    # Derived programmatically (not hand-typed) from the real, live SKILL.md content at the
    # time this exact-match check was written: isolate the paragraph with the same start-marker
    # regex + next-boundary logic below, collapse all whitespace runs to single spaces, then
    # hardcode the verified result. Regenerate the same way if the real paragraph is
    # intentionally edited.
    "**Conditional — only if `## 0.` step 1a set `WORKSPACE_WRITABLE_PATHS_JSON` to something other than the empty-array default** (i.e. `TOPLEVEL_EXIT != 0` in `## 0.` AND that variable is set and `!= '[]'`): substitute that JSON array value in place of the `workspace_writable_paths:[]` default in the artifact `Write` above, instead of leaving it as `[]`. If `## 0.` never ran step 1a (the common single-repo path), or step 1a ran but the array is empty, leave the default `[]` in place — no substitution needed."
)


def test_workflow_artifact_template_includes_workspace_writable_paths_field() -> None:
    name = "router/workflow-artifact-template-includes-workspace-writable-paths-field"
    skill_path = PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    parent_wf_start = content.find("### Parent workflow creation")
    if parent_wf_start == -1:
        fail(name, "'### Parent workflow creation' section not found")
        return
    next_heading = content.find("\n### ", parent_wf_start + 1)
    section = content[parent_wf_start: next_heading if next_heading != -1 else None]
    if '\\"workspace_writable_paths\\"' not in section and '"workspace_writable_paths"' not in section:
        fail(name, "workspace_writable_paths field not found in the artifact-write JSON literal")
        return
    # The key existing is not enough -- the JSON template's default value must be exactly an
    # empty array, not merely present. Guards against the key surviving with a corrupted or
    # non-empty hardcoded default.
    if '\\"workspace_writable_paths\\":[]' not in section:
        fail(name, "workspace_writable_paths default value is not exactly the empty array '[]' in the artifact-write JSON literal")
        return
    # --- Exact-match check on the substitution-instruction paragraph -----------------------
    # A future bad edit could silently strip the router's ONLY prose instructions for actually
    # populating the allowlist (the "Conditional -- only if `## 0.` step 1a set
    # `WORKSPACE_WRITABLE_PATHS_JSON`..." substitution paragraph) while leaving the
    # workspace_writable_paths:[] key above untouched -- the two checks above alone would not
    # catch that.
    #
    # History: two remediation rounds each added ONE MORE independent substring anchor, and each
    # was defeated by an adversarial edit preserving the newest anchor while corrupting whatever
    # came after it. A third round replaced the anchors with a length-floor + multi-anchor
    # structural check; that round was defeated by a "pad-and-anchor" attack -- fabricated filler
    # text containing all the required anchor substrings, padded past the length floor, but
    # semantically wrong. Chained substring/length heuristics cannot close that hole: any set of
    # independently-checked fragments can be satisfied by content that says something different
    # from what those fragments imply.
    #
    # Closing fix: exact string equality against a verified golden value. This is not a
    # heuristic -- no amount of padding, anchor-preserving filler, or clause reordering can
    # satisfy exact equality; only the literal correct paragraph can. Whitespace (line-wrap /
    # reflow) is normalized away first since it carries no semantic content, so a harmless
    # reflow of the same words still passes.
    paragraph_marker_words = "**Conditional — only if `## 0.` step 1a set `WORKSPACE_WRITABLE_PATHS_JSON`".split(" ")
    paragraph_marker_re = re.compile(r"\s+".join(re.escape(w) for w in paragraph_marker_words))
    marker_match = paragraph_marker_re.search(section)
    if marker_match is None:
        fail(
            name,
            "workspace_writable_paths substitution-instruction paragraph not found (start marker "
            "missing) in the '### Parent workflow creation' section",
        )
        return
    paragraph_start = marker_match.start()
    # End at the next paragraph boundary (blank line) or heading, whichever comes first.
    boundary_candidates = [
        idx
        for idx in (
            section.find("\n\n", paragraph_start),
            section.find("\n## ", paragraph_start),
            section.find("\n### ", paragraph_start),
        )
        if idx != -1
    ]
    paragraph_end = min(boundary_candidates) if boundary_candidates else len(section)
    paragraph = section[paragraph_start:paragraph_end]

    normalized = re.sub(r"\s+", " ", paragraph)
    if normalized != _EXPECTED_WORKSPACE_WRITABLE_PATHS_SUBSTITUTION_PARAGRAPH:
        fail(
            name,
            "workspace_writable_paths substitution-instruction paragraph does not exactly match "
            f"the expected golden text -- got: {normalized!r}",
        )
        return
    ok(name)


# ---------------------------------------------------------------------------
# craftflow_state_query.py: skeleton + --mode full
# ---------------------------------------------------------------------------

def test_state_query_full_mode_byte_identical(tmp_dir: Path) -> None:
    name = "state-query/full-mode-byte-identical"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    target = tmp_dir / "sample.md"
    content = "## Section\n" + ("- bullet\n" * 500)
    target.write_text(content, encoding="utf-8")
    script = SCRIPTS / "craftflow_state_query.py"
    result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "full"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}: {result.stderr}")
        return
    if result.stdout != content:
        fail(name, "full mode output is not byte-identical to source")
        return
    ok(name)


def test_state_query_full_mode_missing_file_errors_cleanly(tmp_dir: Path) -> None:
    name = "state-query/full-mode-missing-file"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    script = SCRIPTS / "craftflow_state_query.py"
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_dir / "nope.md"), "--mode", "full"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        fail(name, "expected non-zero exit for missing file")
        return
    if "cannot read" not in result.stderr:
        fail(name, f"expected 'cannot read' error message on stderr, got: {result.stderr!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# craftflow_state_query.py: workflow-JSON summary mode
# ---------------------------------------------------------------------------

def _workflow_json_fixture() -> dict:
    return {
        "workflow_uuid": "wf-20260818-000000-abcdef01",
        "pending_gate": "none",
        "phase_status": "in_progress",
        "worktree_path": "/tmp/does-not-matter",
        "normalized_phases": [
            {"id": f"phase-{i}", "description": "x" * 300} for i in range(3)
        ],
        "telemetry": {f"key-{i}": "y" * 300 for i in range(10)},
        "evidence": [{"id": f"ev-{i}", "content": "z" * 300} for i in range(8)],
        "status_history": [
            {"status": f"s{i}", "ts": f"t{i}", "detail": "w" * 300} for i in range(20)
        ],
    }


def test_state_query_summarize_workflow_json_shrinks_and_keeps_key_fields(tmp_dir: Path) -> None:
    name = "state-query/summarize-workflow-json/shrinks-and-keeps-key-fields"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fixture = _workflow_json_fixture()
    content = json.dumps(fixture)
    target = tmp_dir / "wf-test.json"
    target.write_text(content, encoding="utf-8")
    script = SCRIPTS / "craftflow_state_query.py"
    result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "summary"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}: {result.stderr}")
        return
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(name, f"summary output is not valid JSON: {exc}")
        return
    if len(result.stdout) >= len(content) / 3:
        fail(name, f"summary not order-of-magnitude smaller: {len(result.stdout)} vs {len(content)}")
        return
    for key in ("workflow_uuid", "pending_gate", "phase_status"):
        if parsed.get(key) != fixture[key]:
            fail(name, f"missing/incorrect key field {key!r}: {parsed.get(key)!r}")
            return
    status_tail = parsed.get("status_history_tail")
    if status_tail != fixture["status_history"][-DEFAULT_STATUS_HISTORY_ENTRIES_FOR_TEST:]:
        fail(name, f"status_history_tail is not the most recent {DEFAULT_STATUS_HISTORY_ENTRIES_FOR_TEST} entries: {status_tail!r}")
        return
    if "--mode full" not in parsed.get("_note", ""):
        fail(name, f"_note must mention --mode full: {parsed.get('_note')!r}")
        return
    ok(name)


DEFAULT_STATUS_HISTORY_ENTRIES_FOR_TEST = 5


def test_state_query_summarize_workflow_json_malformed_falls_open(tmp_dir: Path) -> None:
    name = "state-query/summarize-workflow-json/malformed-falls-open"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    raw = "not json at all " + ("x" * 60000)
    target = tmp_dir / "wf-malformed.json"
    target.write_text(raw, encoding="utf-8")
    script = SCRIPTS / "craftflow_state_query.py"
    result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "summary"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}: {result.stderr}")
        return
    if not result.stdout.startswith("WARNING:"):
        fail(name, f"expected WARNING: banner, got: {result.stdout[:80]!r}")
        return
    if raw not in result.stdout:
        fail(name, "fail-open output must contain the full original raw content")
        return
    ok(name)


# ---------------------------------------------------------------------------
# craftflow_state_query.py: events.jsonl summary mode
# ---------------------------------------------------------------------------

def _events_jsonl_fixture(n_valid: int = 198, n_malformed: int = 2, event_types: tuple = ("pretool_guard",)) -> str:
    lines = []
    for i in range(n_valid):
        event_type = event_types[i % len(event_types)]
        lines.append(json.dumps({"event": event_type, "seq": i}))
    for i in range(n_malformed):
        lines.append("{not valid json,,,")
    return "\n".join(lines) + "\n"


def test_state_query_summarize_events_jsonl_default_tail_and_footer(tmp_dir: Path) -> None:
    name = "state-query/summarize-events-jsonl/default-tail-and-footer"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    content = _events_jsonl_fixture(n_valid=198, n_malformed=2)
    target = tmp_dir / "test.events.jsonl"
    target.write_text(content, encoding="utf-8")
    script = SCRIPTS / "craftflow_state_query.py"
    result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "summary"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}: {result.stderr}")
        return
    lines_out = result.stdout.rstrip("\n").split("\n")
    footer = lines_out[-1]
    entry_lines = lines_out[:-1]
    if len(entry_lines) != 50:
        fail(name, f"expected 50 tailed entries by default, got {len(entry_lines)}")
        return
    parsed_entries = [json.loads(line) for line in entry_lines]
    if parsed_entries[-1]["seq"] != 197:
        fail(name, f"tail must be the MOST RECENT entries; last seq={parsed_entries[-1]['seq']}")
        return
    if "200 total lines" not in footer or "2 malformed" not in footer:
        fail(name, f"footer must report total lines and malformed count: {footer!r}")
        return
    ok(name)


def test_state_query_summarize_events_jsonl_event_type_filter(tmp_dir: Path) -> None:
    name = "state-query/summarize-events-jsonl/event-type-filter"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    content = _events_jsonl_fixture(
        n_valid=30, n_malformed=0, event_types=("pretool_guard", "audit", "session_start")
    )
    target = tmp_dir / "filter.events.jsonl"
    target.write_text(content, encoding="utf-8")
    script = SCRIPTS / "craftflow_state_query.py"
    result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "summary", "--event-type", "audit"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}: {result.stderr}")
        return
    lines_out = result.stdout.rstrip("\n").split("\n")
    entry_lines = lines_out[:-1]
    parsed_entries = [json.loads(line) for line in entry_lines]
    if not parsed_entries:
        fail(name, "expected at least one matching entry")
        return
    if any(entry["event"] != "audit" for entry in parsed_entries):
        fail(name, f"filter must return only matching event types: {parsed_entries!r}")
        return
    ok(name)


def test_state_query_summarize_events_jsonl_all_malformed_never_crashes(tmp_dir: Path) -> None:
    name = "state-query/summarize-events-jsonl/all-malformed-never-crashes"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    content = _events_jsonl_fixture(n_valid=0, n_malformed=5)
    target = tmp_dir / "all-malformed.events.jsonl"
    target.write_text(content, encoding="utf-8")
    script = SCRIPTS / "craftflow_state_query.py"
    result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "summary"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}: {result.stderr}")
        return
    if "0 valid" not in result.stdout or "5 malformed" not in result.stdout:
        fail(name, f"expected 0 valid / 5 malformed reported: {result.stdout!r}")
        return
    ok(name)


# ---------------------------------------------------------------------------
# craftflow_state_query.py: markdown + generic summary modes
# ---------------------------------------------------------------------------

def _markdown_memory_fixture() -> str:
    recent_changes = "\n".join(f"- change entry number {i} with some detail text" for i in range(60))
    learnings = "\n".join(f"- learning entry number {i} with some detail text" for i in range(80))
    return (
        "# Active Context\n\n"
        "## Current Focus\n"
        "Some short focus text.\n\n"
        "## Recent Changes\n" + recent_changes + "\n\n"
        "## Decisions\n"
        "- decision one\n"
        "- decision two\n\n"
        "## Learnings\n" + learnings + "\n\n"
        "## References\n"
        "- some reference\n\n"
        "## Last Updated\n"
        "2026-08-18\n"
    )


def test_state_query_summarize_markdown_caps_bullets_per_section(tmp_dir: Path) -> None:
    name = "state-query/summarize-markdown/caps-bullets-per-section"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    content = _markdown_memory_fixture()
    target = tmp_dir / "activeContext.md"
    target.write_text(content, encoding="utf-8")
    script = SCRIPTS / "craftflow_state_query.py"
    result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "summary"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}: {result.stderr}")
        return
    out = result.stdout
    for heading in ("Current Focus", "Recent Changes", "Decisions", "Learnings", "References", "Last Updated"):
        if f"## {heading}" not in out:
            fail(name, f"missing heading in summary: {heading!r}")
            return
    recent_bullets = out.count("change entry number")
    if recent_bullets != 10:
        fail(name, f"expected at most 10 Recent Changes bullets shown, got {recent_bullets}")
        return
    if "change entry number 59" not in out:
        fail(name, "expected the MOST RECENT bullets (highest index) to be shown, not the oldest")
        return
    if "(60 total bullets, 10 most recent shown)" not in out:
        fail(name, "expected a bullet-count note for Recent Changes")
        return
    if len(out) >= len(content) / 2:
        fail(name, f"summary must be materially smaller than input: {len(out)} vs {len(content)}")
        return
    ok(name)


def test_state_query_summarize_markdown_no_headings_falls_back_to_generic(tmp_dir: Path) -> None:
    name = "state-query/summarize-markdown/no-headings-falls-back-to-generic"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    content = "\n".join(f"plain line {i}" for i in range(100)) + "\n"
    target = tmp_dir / "plain.md"
    target.write_text(content, encoding="utf-8")
    script = SCRIPTS / "craftflow_state_query.py"
    result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "summary"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}: {result.stderr}")
        return
    if not result.stdout.startswith("WARNING:"):
        fail(name, f"expected WARNING: banner prefix on no-headings fallback, got: {result.stdout[:80]!r}")
        return
    if "plain line 0" not in result.stdout or "plain line 99" not in result.stdout:
        fail(name, "generic fallback must show first and last lines")
        return
    if "lines omitted" not in result.stdout:
        fail(name, "generic fallback must disclose omitted line count")
        return
    ok(name)


def _large_narrative_section_fixture(narrative_lines: int) -> str:
    """Reproduce the real activeContext.md shape: a '## Current Focus'
    section with large, zero-bullet narrative prose, alongside a normal
    bulleted section."""
    focus_body = "\n".join(
        f"narrative line {i} describing ongoing work, decisions, and "
        "background context for this session in verbose free-text prose."
        for i in range(narrative_lines)
    )
    recent_changes = "\n".join(f"- change entry number {i} with some detail text" for i in range(10))
    return (
        "# Active Context\n\n"
        "## Current Focus\n" + focus_body + "\n\n"
        "## Recent Changes\n" + recent_changes + "\n\n"
    )


def test_state_query_summarize_markdown_truncates_large_non_bulleted_section(tmp_dir: Path) -> None:
    name = "state-query/summarize-markdown/truncates-large-non-bulleted-section"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    content = _large_narrative_section_fixture(400)
    target = tmp_dir / "activeContext.md"
    target.write_text(content, encoding="utf-8")
    script = SCRIPTS / "craftflow_state_query.py"
    result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "summary"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}: {result.stderr}")
        return
    out = result.stdout
    for heading in ("Current Focus", "Recent Changes"):
        if f"## {heading}" not in out:
            fail(name, f"missing heading in summary: {heading!r}")
            return
    if len(out) >= len(content) / 3:
        fail(name, f"summary must be meaningfully smaller than source: {len(out)} vs {len(content)}")
        return
    if "narrative line 0 " not in out:
        fail(name, "expected the first narrative line to still be shown")
        return
    if "narrative line 399 " not in out:
        fail(name, "expected the last narrative line to still be shown")
        return
    if "lines omitted" not in out:
        fail(name, "expected truncated non-bulleted section to disclose omitted line count")
        return
    ok(name)


def test_state_query_summarize_markdown_real_shape_meaningful_reduction(tmp_dir: Path) -> None:
    # Reproduces this repo's real trigger: activeContext.md's ## Current
    # Focus section reached 300KB+ of zero-bullet narrative prose and was
    # previously copied through 100% verbatim, defeating compaction.
    name = "state-query/summarize-markdown/real-shape-meaningful-reduction"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    content = _large_narrative_section_fixture(4000)
    if len(content) < 300_000:
        fail(name, f"fixture must reproduce a realistic 300KB+ trigger, got {len(content)}")
        return
    target = tmp_dir / "activeContext.md"
    target.write_text(content, encoding="utf-8")
    script = SCRIPTS / "craftflow_state_query.py"
    result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "summary"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}: {result.stderr}")
        return
    out = result.stdout
    if "## Current Focus" not in out:
        fail(name, "expected the section heading to be preserved")
        return
    ratio = len(content) / len(out) if out else float("inf")
    if ratio < 10:
        fail(name, f"expected order-of-magnitude-ish compaction, got ratio {ratio:.1f}x ({len(content)} -> {len(out)})")
        return
    ok(name)


def test_state_query_summarize_markdown_short_non_bulleted_section_stays_verbatim(tmp_dir: Path) -> None:
    name = "state-query/summarize-markdown/short-non-bulleted-section-stays-verbatim"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    content = _markdown_memory_fixture()
    target = tmp_dir / "activeContext.md"
    target.write_text(content, encoding="utf-8")
    script = SCRIPTS / "craftflow_state_query.py"
    result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "summary"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(name, f"exit code {result.returncode}: {result.stderr}")
        return
    if "Some short focus text." not in result.stdout:
        fail(name, "expected a genuinely short non-bulleted section body to remain verbatim")
        return
    ok(name)


# ---------------------------------------------------------------------------
# pretooluse-guard: state-read compaction (Read PreToolUse)
# ---------------------------------------------------------------------------

def test_pretooluse_guard_denies_oversized_state_read(tmp_dir: Path) -> None:
    name = "pretooluse-guard/read/denies-oversized-state-file"
    state_dir = tmp_dir / ".craftflow" / "state" / "project"
    state_dir.mkdir(parents=True)
    target = state_dir / "activeContext.md"
    target.write_text("x" * 60000, encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir.resolve())}
    payload = {"tool_name": "Read", "tool_input": {"file_path": str(target.resolve())}}
    code, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return
    if '"permissionDecision": "deny"' not in out and '"permissionDecision":"deny"' not in out:
        fail(name, f"expected deny decision, got: {out[:300]}")
        return
    if "craftflow_state_query.py" not in out:
        fail(name, f"deny message must name the redirect script: {out[:300]}")
        return
    ok(name)


def test_pretooluse_guard_allows_under_threshold_state_read(tmp_dir: Path) -> None:
    name = "pretooluse-guard/read/allows-under-threshold"
    state_dir = tmp_dir / ".craftflow" / "state" / "project"
    state_dir.mkdir(parents=True)
    target = state_dir / "patterns.md"
    target.write_text("small content", encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir.resolve())}
    payload = {"tool_name": "Read", "tool_input": {"file_path": str(target.resolve())}}
    code, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if code != 0 or "permissionDecision" in out:
        fail(name, f"expected allow (no deny), got exit={code} out={out[:200]}")
        return
    ok(name)


def test_pretooluse_guard_allows_oversized_read_outside_state_dir(tmp_dir: Path) -> None:
    name = "pretooluse-guard/read/allows-oversized-outside-state"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    target = tmp_dir / "README.md"
    target.write_text("x" * 60000, encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir.resolve())}
    payload = {"tool_name": "Read", "tool_input": {"file_path": str(target.resolve())}}
    code, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if code != 0 or "permissionDecision" in out:
        fail(name, f"expected allow outside .craftflow/state, got exit={code} out={out[:200]}")
        return
    ok(name)


def test_pretooluse_guard_read_branch_does_not_affect_edit_write_dispatch(tmp_dir: Path) -> None:
    name = "pretooluse-guard/read/edit-write-dispatch-unaffected"
    state_dir = tmp_dir / ".craftflow" / "state" / "project"
    state_dir.mkdir(parents=True)
    target = state_dir / "patterns.md"
    target.write_text("existing content", encoding="utf-8")
    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir.resolve())}
    payload = {
        "tool_name": "Edit",
        "cwd": str(tmp_dir.resolve()),
        "tool_input": {"file_path": str(target.resolve()), "old_string": "a", "new_string": "b"},
    }
    code, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if code != 0:
        fail(name, f"exit code {code}; expected 0")
        return
    if "state-read-compaction" in out:
        fail(name, f"Edit must never trigger the read-compaction reason: {out[:200]}")
        return
    ok(name)


def test_state_read_compaction_end_to_end(tmp_dir: Path) -> None:
    name = "state-read-compaction/end-to-end"
    state_dir = tmp_dir / ".craftflow" / "state" / "project"
    state_dir.mkdir(parents=True)
    target = state_dir / "activeContext.md"
    # Reproduce this session's real trigger shape: many sections, many bullets.
    body = "## Current Focus\nSome focus text.\n\n## Recent Changes\n"
    # 3000x here only reaches ~129KB (43 bytes/line), which does not satisfy
    # the 500KB+ assertion below; use 12000x to actually reproduce the
    # 500KB+ trigger shape this test asserts.
    body += "- change entry with some real content here\n" * 12000
    target.write_text(body, encoding="utf-8")
    if target.stat().st_size < 500_000:
        fail(name, "fixture must reproduce a realistic 500KB+ trigger")
        return

    env = {"CLAUDE_PROJECT_DIR": str(tmp_dir.resolve())}
    payload = {"tool_name": "Read", "tool_input": {"file_path": str(target.resolve())}}
    code, out = run_hook("craftflow_pretooluse_guard.py", payload, env)
    if code != 0 or '"permissionDecision": "deny"' not in out:
        fail(name, f"expected deny, got exit={code} out={out[:200]}")
        return

    script = SCRIPTS / "craftflow_state_query.py"
    summary_result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "summary"],
        capture_output=True, text=True,
    )
    if summary_result.returncode != 0:
        fail(name, f"summary mode failed: {summary_result.stderr}")
        return
    if len(summary_result.stdout) >= len(body) / 5:
        fail(name, "summary must be order-of-magnitude smaller than the 500KB+ source")
        return

    full_result = subprocess.run(
        [sys.executable, str(script), str(target), "--mode", "full"],
        capture_output=True, text=True,
    )
    if full_result.stdout != body:
        fail(name, "full mode must be byte-identical to the source")
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
        test_pretooluse_guard_allows_workflow_scoped_memory_write_when_newer_unrelated_workflow_exists(tmp / "g2b")
        test_pretooluse_guard_denies_workflow_scoped_memory_write_with_permit_for_different_workflow(tmp / "g2c")
        test_pretooluse_guard_allows_project_tier_memory_write_when_newer_unrelated_workflow_exists(tmp / "g2d")

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
        test_pretooluse_guard_allows_bash_permit_write_multiline_command(tmp / "g14b")
        test_pretooluse_guard_allows_bash_unrelated_command(tmp / "g15")
        test_pretooluse_guard_edit_write_worktree_confinement_denies_outside(tmp / "g16")
        test_pretooluse_guard_edit_write_worktree_confinement_allows_inside_worktree(tmp / "g17")
        test_pretooluse_guard_worktree_confinement_degrades_when_no_workflow_json(tmp / "g18")
        test_pretooluse_guard_edit_write_allows_claude_code_own_memory_dir(tmp / "g18b")
        test_pretooluse_guard_edit_write_allows_claude_code_own_memory_md_exact_file(tmp / "g18c")
        test_pretooluse_guard_edit_write_denies_other_project_claude_code_memory_dir(tmp / "g18d")
        test_pretooluse_guard_edit_write_allows_claude_code_own_session_scoped_memory_dir(tmp / "g18e")
        test_pretooluse_guard_edit_write_confinement_allows_workflow_json_when_worktree_path_stale(tmp / "g19")
        test_pretooluse_guard_latest_workflow_ignores_terminal_worktree_mode_even_with_newest_mtime(tmp / "g19-itemA-1")
        test_pretooluse_guard_latest_workflow_ignores_missing_worktree_directory_even_with_newest_mtime(tmp / "g19-itemA-2")
        test_hooklib_latest_workflow_file_prefers_session_id_match_when_present(tmp / "g19-itemA-3")
        test_precompact_state_snapshot_uses_newest_by_mtime_even_when_terminal_workflow(tmp / "g19-itemA-4")
        test_hooklib_latest_live_workflow_file_finds_session_match_outside_scan_window(tmp / "g19-itemA-5")
        test_hooklib_latest_live_workflow_file_stays_bounded_when_window_lacks_session_id_key(tmp / "g19-itemA-6")
        test_pretooluse_guard_edit_write_single_denial_is_not_escalated(tmp / "g19-itemB-1")
        test_pretooluse_guard_edit_write_second_consecutive_denial_is_escalated(tmp / "g19-itemB-2")
        test_pretooluse_guard_bash_second_consecutive_denial_is_escalated(tmp / "g19-itemB-3")
        test_pretooluse_guard_edit_write_denial_counter_resets_on_different_target(tmp / "g19-itemB-4")
        test_pretooluse_guard_edit_write_denial_counter_resets_after_allowed_write(tmp / "g19-itemB-5")
        test_hooklib_record_denial_concurrent_calls_do_not_lose_updates(tmp / "g19-itemB-6")
        test_pretooluse_guard_edit_write_denial_escalates_across_case_variant_paths(tmp / "g19-itemB-7")
        test_pretooluse_guard_edit_write_allows_exact_allowlisted_workspace_root_file(tmp / "g19b")
        test_pretooluse_guard_edit_write_denies_non_allowlisted_sibling_workspace_root_file(tmp / "g19c")
        test_pretooluse_guard_edit_write_denies_descendant_of_allowlisted_entry_no_directory_grant(tmp / "g19d")
        test_pretooluse_guard_edit_write_confinement_regression_unaffected_by_unrelated_workspace_writable_paths(tmp / "g19e")
        test_pretooluse_guard_bash_protected_path_write_still_denied_when_unrelated_workspace_writable_paths_present(tmp / "g19f")
        test_pretooluse_guard_bash_redirect_to_non_protected_workspace_root_file_unaffected_either_way(tmp / "g19g")
        test_pretooluse_guard_bash_confinement_lane_no_longer_flags_target_once_it_is_workspace_allowlisted(tmp / "g19h")
        test_pretooluse_guard_bash_permit_write_allowed_when_worktree_path_stale(tmp / "g20")

        print()
        print("[ pretooluse-guard: REM-FIX (null-byte workspace_writable_paths entry + non-dict workflow JSON top-level) ]")
        test_pretooluse_guard_bash_null_byte_workspace_writable_paths_entry_denies_without_crash(tmp / "g62")
        test_pretooluse_guard_edit_write_null_byte_workspace_writable_paths_entry_denies_target_outside_confinement(tmp / "g63")
        test_pretooluse_guard_bash_non_dict_workflow_json_top_level_degrades_gracefully_still_denies(tmp / "g64")
        test_pretooluse_guard_edit_write_non_dict_workflow_json_top_level_degrades_gracefully_still_denies(tmp / "g65")

        test_pretooluse_guard_allows_benign_redirect_to_dev_null(tmp / "g21")
        test_pretooluse_guard_allows_benign_stderr_redirect_to_dev_null(tmp / "g22")
        test_pretooluse_guard_bash_worktree_confinement_only_message_omits_skill_text(tmp / "g22b")

        print()
        print("[ pretooluse-guard: REM-FIX (5 live-verified bugs) ]")
        test_pretooluse_guard_allows_bash_permit_write_absolute_spelling(tmp / "g23")
        test_pretooluse_guard_allows_bash_permit_write_dot_slash_spelling(tmp / "g23b")
        test_pretooluse_guard_denies_bash_permit_shape_targeting_different_file(tmp / "g23c")
        test_pretooluse_guard_denies_bash_permit_write_tee_shape(tmp / "g23d")
        test_pretooluse_guard_denies_permit_write_python_open_shape(tmp / "g23e")
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
        print("[ pretooluse-guard: REM-FIX (doubt-verify cycle 3: missing-key mode.get() defaults) ]")
        test_pretooluse_guard_bash_write_denied_when_protected_writes_key_missing(tmp / "g50")
        test_pretooluse_guard_memory_write_allowed_when_memory_writes_key_missing(tmp / "g51")

        print()
        print("[ pretooluse-guard: REM-FIX round 5 (cp/mv/ln/dd destination-argument bypass) ]")
        test_pretooluse_guard_denies_bash_cp_write_to_skill_proposal_file(tmp / "g52")
        test_pretooluse_guard_denies_bash_cp_write_to_skill_ledger(tmp / "g53")
        test_pretooluse_guard_denies_bash_mv_write_to_skill_proposal_file(tmp / "g54")
        test_pretooluse_guard_denies_bash_ln_write_to_memory_md(tmp / "g55")
        test_pretooluse_guard_denies_bash_mv_write_to_inflight_skill_promotion_path(tmp / "g56")
        test_pretooluse_guard_denies_bash_cp_target_directory_form_write_to_skill_proposals(tmp / "g57")
        test_pretooluse_guard_allows_bash_cp_unrelated_elsewhere(tmp / "g58")
        test_pretooluse_guard_allows_bash_mv_unrelated_elsewhere(tmp / "g59")

        print()
        print("[ skill-propose / skill-promote: REM-FIX round 5 (non-UTF-8 draft content) ]")
        test_skill_propose_non_utf8_skill_md_file_returns_json_error(tmp / "g60")
        test_skill_promote_non_utf8_skill_md_returns_json_error(tmp / "g61")

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
        test_bash_guard_allows_permit_write_absolute_spelling(tmp / "b49b")
        test_bash_guard_allows_permit_write_dot_slash_spelling(tmp / "b49c")
        test_bash_guard_allows_permit_write_multiline_command(tmp / "b49c2")
        test_bash_guard_denies_permit_shape_targeting_different_file(tmp / "b49d")
        test_bash_guard_permit_write_end_to_end_skill_md_literal_project_root_prefixed_spelling(tmp / "b49f")
        test_bash_guard_allows_benign_redirect_to_dev_null(tmp / "b50")
        test_bash_guard_allows_benign_stderr_redirect_to_dev_null(tmp / "b51")

        print()
        print("[ pretooluse-bash-guard: REM-FIX Phase 3 review+hunt findings ]")
        test_bash_guard_blocks_protected_redirect_overwrite_when_confined_to_cwd(tmp / "b52")
        test_bash_guard_blocks_dd_of_dynamic_traversal_substitution(tmp / "b53")
        test_bash_guard_worktree_path_non_string_type_does_not_crash(tmp / "b54")
        test_bash_guard_main_denies_not_crashes_when_resolve_confinement_raises(tmp / "b55")
        test_bash_guard_denial_reason_includes_all_triggered_categories(tmp / "b56")
        test_bash_guard_allows_rm_targeting_claude_code_own_memory_dir(tmp / "b56b")

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
        print("[ pretooluse-bash-guard: REM-FIX (extend enum validation + distinguishing log decision to bashDestructiveTraversal) ]")
        test_bash_guard_unrecognized_bash_destructive_traversal_value_fails_closed_with_distinct_decision(tmp / "b83")
        test_bash_guard_missing_bash_destructive_traversal_key_still_fails_closed(tmp / "b84")
        test_bash_guard_explicit_block_value_still_denies(tmp / "b85")
        test_bash_guard_explicit_audit_value_allows_but_logs_audit(tmp / "b86")

        print()
        print("[ pretooluse-bash-guard: REM-FIX (residual gap -- wildcard as a MIDDLE path segment) ]")
        test_bash_guard_blocks_rm_rf_wildcard_middle_segment_dotgit(tmp / "b87")
        test_bash_guard_blocks_rm_rf_wildcard_middle_segment_packages(tmp / "b88")
        test_bash_guard_blocks_rm_rf_wildcard_middle_segment_tools_no_dot_prefix(tmp / "b89")
        test_bash_guard_blocks_rm_rf_double_wildcard_middle_segments_packages(tmp / "b90")
        test_bash_guard_blocks_rm_rf_wildcard_critical_child_at_deeper_depth(tmp / "b91")
        test_bash_guard_blocks_rm_rf_globstar_middle_segment_dotgit(tmp / "b91b")
        test_bash_guard_allows_wildcard_middle_segment_noncritical_name(tmp / "b92")
        test_bash_guard_blocks_rm_rf_dot_slash_star_still_denied(tmp / "b93")
        test_bash_guard_blocks_rm_rf_dot_slash_dotgit_still_denied(tmp / "b94")
        test_bash_guard_blocks_rm_rf_traversal_normalizing_to_dotgit_still_denied(tmp / "b95")

        print()
        print("[ pretooluse-bash-guard: REM-FIX round 2 (bare globstar trailing check + partial-wildcard component bypass) ]")
        test_bash_guard_blocks_rm_rf_bare_globstar_still_denied(tmp / "b96")
        test_bash_guard_blocks_rm_rf_dot_slash_globstar_still_denied(tmp / "b97")
        test_bash_guard_blocks_rm_rf_partial_wildcard_top_level_packages(tmp / "b98")
        test_bash_guard_blocks_rm_rf_partial_wildcard_middle_segment_packages(tmp / "b99")
        test_bash_guard_blocks_rm_rf_globstar_and_partial_wildcard_combo(tmp / "b100")
        test_bash_guard_blocks_rm_rf_partial_wildcard_final_component_nested(tmp / "b101")
        test_bash_guard_allows_partial_wildcard_noncritical_name(tmp / "b102")
        test_bash_guard_allows_wildcard_middle_segment_still_noncritical(tmp / "b103")
        test_bash_guard_allows_wildcard_middle_segment_node_modules_cache(tmp / "b104")

        print()
        print("[ pretooluse-bash-guard: REM-FIX round 3 (filler-side fnmatch-vocabulary generalization) ]")
        test_bash_guard_blocks_rm_rf_question_mark_middle_segment_dotgit(tmp / "b105")
        test_bash_guard_blocks_rm_rf_bracket_seq_middle_segment_dotgit(tmp / "b106")
        test_bash_guard_blocks_rm_rf_negated_bracket_seq_middle_segment_packages(tmp / "b107")
        test_bash_guard_blocks_rm_rf_leading_partial_star_middle_segment_tools(tmp / "b108")
        test_bash_guard_blocks_rm_rf_trailing_partial_star_middle_segment_packages(tmp / "b109")
        test_bash_guard_blocks_rm_rf_posix_char_class_middle_segment_dotgit(tmp / "b110")
        test_bash_guard_blocks_rm_rf_combined_multi_bracket_middle_segment_dotgit(tmp / "b111")
        test_bash_guard_blocks_rm_rf_two_different_filler_shapes_in_a_row_dotgit(tmp / "b112")
        test_bash_guard_blocks_rm_rf_filler_resembling_critical_name_itself(tmp / "b113")
        test_bash_guard_blocks_rm_rf_nonadjacent_fillers_before_critical_name(tmp / "b114")
        test_bash_guard_allows_bracket_seq_middle_segment_noncritical_name(tmp / "b115")
        test_bash_guard_allows_question_mark_middle_segment_noncritical_name(tmp / "b116")

        print()
        print("[ pretooluse-bash-guard: REM-FIX round 4 (brace-expansion middle-segment bypass + extglob negation bypass) ]")
        test_bash_guard_blocks_rm_rf_brace_sibling_dotgit(tmp / "b117")
        test_bash_guard_blocks_rm_rf_brace_sibling_packages(tmp / "b118")
        test_bash_guard_blocks_rm_rf_brace_top_level_no_dot_prefix(tmp / "b119")
        test_bash_guard_blocks_rm_rf_brace_middle_segment_packages(tmp / "b120")
        test_bash_guard_blocks_rm_rf_brace_three_alternatives_dotgit(tmp / "b121")
        test_bash_guard_blocks_rm_rf_brace_combined_partial_wildcard_packages(tmp / "b122")
        test_bash_guard_blocks_rm_rf_brace_sibling_descendant_dotgit(tmp / "b123")
        test_bash_guard_allows_brace_benign_suffix_build_dist_tmp(tmp / "b124")
        test_bash_guard_allows_mkdir_brace_non_destructive(tmp / "b125")
        test_bash_guard_blocks_rm_rf_extglob_negation_scratch(tmp / "b126")

        print()
        print("[ pretooluse-bash-guard: REM-FIX (composition-boundary bypass -- nested brace + command-sub-in-brace) ]")
        test_bash_guard_blocks_rm_rf_nested_brace_dotgit(tmp / "b127")
        test_bash_guard_allows_nested_brace_benign(tmp / "b128")
        test_bash_guard_blocks_rm_rf_doubly_nested_brace_dotgit(tmp / "b129")
        test_bash_guard_blocks_rm_rf_command_sub_nested_in_brace_dotgit(tmp / "b130")
        test_bash_guard_blocks_rm_rf_command_sub_nested_in_doubly_nested_brace_packages(tmp / "b131")
        test_bash_guard_blocks_rm_rf_backtick_nested_in_brace_dotgit(tmp / "b132")
        test_bash_guard_blocks_rm_rf_deeply_nested_brace_exceeds_bound(tmp / "b133")

        print()
        print("[ pretooluse-bash-guard: REM-FIX (ANSI-C quoting $'...' bypass) ]")
        test_bash_guard_blocks_rm_rf_ansi_c_hex_quoted_dotgit(tmp / "b134")
        test_bash_guard_blocks_rm_rf_ansi_c_quoted_brace_sibling_dotgit(tmp / "b135")
        test_bash_guard_blocks_rm_rf_ansi_c_octal_quoted_dotgit(tmp / "b136")
        test_bash_guard_blocks_rm_rf_ansi_c_quoted_packages(tmp / "b137")
        test_bash_guard_blocks_rm_rf_ansi_c_quoted_brace_sibling_tools(tmp / "b138")
        test_bash_guard_blocks_rm_rf_ansi_c_adjacent_quotes_concatenated_dotgit(tmp / "b139")
        test_bash_guard_blocks_rm_rf_ansi_c_quoted_suffix_of_literal_prefix_dotgit(tmp / "b140")
        test_bash_guard_allows_ansi_c_quoted_benign_name(tmp / "b141")

        print()
        print("[ pretooluse-bash-guard: REM-FIX round 8 (brace empty-pair backtrack bypass) ]")
        test_bash_guard_blocks_rm_rf_brace_empty_pair_before_dotgit(tmp / "b142")
        test_bash_guard_blocks_rm_rf_brace_empty_pair_before_ansi_c_hex_dotgit(tmp / "b143")
        test_bash_guard_blocks_rm_rf_brace_empty_pair_before_packages(tmp / "b144")
        test_bash_guard_blocks_rm_rf_brace_empty_pair_before_ansi_c_octal_dotgit(tmp / "b145")
        test_bash_guard_blocks_rm_rf_brace_multiple_consecutive_empty_pairs_dotgit(tmp / "b146")
        test_bash_guard_blocks_rm_rf_brace_empty_pair_before_tools(tmp / "b147")
        test_bash_guard_blocks_rm_rf_brace_empty_pair_middle_segment_packages(tmp / "b148")
        test_bash_guard_blocks_rm_rf_brace_empty_pair_before_dotgit_descendant_sub(tmp / "b149")
        test_bash_guard_blocks_rm_rf_brace_double_empty_pair_before_packages(tmp / "b150")
        test_bash_guard_allows_brace_empty_pair_before_benign_name(tmp / "b151")
        test_bash_guard_allows_brace_empty_alternative_after_dotgit_contaminated(tmp / "b152")

        print()
        print("[ pretooluse-bash-guard: REM-FIX (algorithmic-complexity DoS -- global brace-scan budget) ]")
        test_bash_guard_iter_brace_groups_bounded_time_for_deeply_nested_empty_braces()
        test_bash_guard_blocks_rm_rf_deeply_nested_empty_braces_dos_payload_end_to_end(tmp / "b153")
        test_bash_guard_bounded_time_for_many_brace_bearing_path_segments_end_to_end(tmp / "b154")

        print()
        print("[ hooklib shared-helper (white-box) ]")
        test_hooklib_resolve_confinement_allows_within_cwd(tmp / "h1")
        test_hooklib_resolve_confinement_denies_outside_cwd_no_worktree(tmp / "h2")
        test_hooklib_resolve_confinement_allows_within_worktree_outside_cwd(tmp / "h3")
        test_hooklib_resolve_confinement_denies_outside_both(tmp / "h4")
        test_hooklib_resolve_confinement_allows_exact_match_in_extra_exact_paths(tmp / "h5")
        test_hooklib_resolve_confinement_denies_descendant_of_extra_exact_path_not_exact_file(tmp / "h6")
        test_hooklib_resolve_confinement_denies_non_matching_sibling_when_extra_exact_paths_set(tmp / "h7")
        test_hooklib_resolve_confinement_extra_exact_paths_does_not_rescue_unlisted_target_outside_cwd_and_worktree(tmp / "h8")
        test_hooklib_resolve_confinement_byte_identical_when_extra_exact_paths_omitted_or_empty(tmp / "h9")
        test_hooklib_resolve_confinement_allows_claude_code_own_memory_dir(tmp / "h10")
        test_hooklib_resolve_confinement_allows_claude_code_own_memory_md_exact(tmp / "h11")
        test_hooklib_resolve_confinement_denies_different_cwd_slug_memory_dir(tmp / "h12")
        test_hooklib_resolve_confinement_allows_claude_code_own_session_scoped_memory_dir(tmp / "h12b")
        test_hooklib_resolve_confinement_allows_claude_code_own_session_scoped_memory_md_exact(tmp / "h12c")
        test_hooklib_resolve_confinement_still_allows_claude_code_own_project_scoped_memory_dir(tmp / "h12d")
        test_hooklib_resolve_confinement_denies_different_cwd_slug_session_scoped_memory_dir(tmp / "h12e")
        test_hooklib_resolve_confinement_denies_session_scoped_dir_not_named_memory(tmp / "h12f")
        test_hooklib_resolve_confinement_denies_session_scoped_traversal_escape(tmp / "h12g")
        test_hooklib_resolve_workspace_writable_paths_empty_when_key_missing()
        test_hooklib_resolve_workspace_writable_paths_coerces_valid_string_list()
        test_hooklib_resolve_workspace_writable_paths_skips_non_string_entries()
        test_hooklib_resolve_workspace_writable_paths_empty_when_not_a_list()
        test_hooklib_resolve_workspace_writable_paths_skips_null_byte_entry_without_raising()
        test_hooklib_command_has_traversal_true_for_dotdot_substitution()
        test_hooklib_command_has_traversal_false_for_lock_dir_var()
        test_hooklib_command_has_traversal_true_for_bare_wildcard()
        test_hooklib_extract_redirect_targets_finds_simple_redirect()
        test_hooklib_extract_redirect_targets_finds_tee_target()
        test_hooklib_extract_redirect_targets_empty_for_no_redirect()
        test_hooklib_matches_permit_shape_true_for_exact_documented_command()
        test_hooklib_matches_permit_shape_true_regardless_of_target_spelling()
        test_hooklib_matches_permit_shape_false_for_different_printf_args()
        test_hooklib_matches_permit_shape_false_for_heredoc()
        test_hooklib_matches_permit_shape_false_for_substitution_in_value()
        test_hooklib_matches_permit_shape_false_for_substitution_in_value_project_root_prefixed()
        test_hooklib_matches_permit_shape_false_for_six_token_shape()
        test_hooklib_split_subcommands_splits_on_bare_newline()
        test_hooklib_split_subcommands_preserves_newline_inside_quotes()

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
        print("[ skill-ledger ]")
        test_ledger_bare_verdict_strings_are_not_candidates(tmp / "sl0a")
        test_ledger_severity_word_followed_by_zero_count_is_not_that_severity(tmp / "sl0b")
        test_ledger_distinct_workflow_counts_not_raw_events(tmp / "sl1")
        test_ledger_distinct_workflow_counts_two_separate_workflows(tmp / "sl2")
        test_ledger_lru_eviction_at_200_cap(tmp / "sl3")
        test_ledger_rejected_stays_rejected_below_doubling_threshold(tmp / "sl4")
        test_ledger_rejected_revives_once_distinct_workflows_double(tmp / "sl5")
        test_ledger_atomic_write_survives_os_replace_failure(tmp / "sl6")
        test_ledger_prune_removes_stale_candidate_only(tmp / "sl7")
        test_ledger_prune_refuses_to_overwrite_corrupt_ledger_file(tmp / "sl7b")
        test_ledger_backtest_never_mutates_real_ledger_file(tmp / "sl8")

        print()
        print("[ skill-ledger: --prune promoted-entry rot check (Phase 4: post-implementation gate) ]")
        test_ledger_prune_rejected_tombstone_untouched(tmp / "sl29")
        test_ledger_prune_promoted_healthy_no_needs_review(tmp / "sl30")
        test_ledger_prune_promoted_missing_referenced_path_flags_stale_path(tmp / "sl31")
        test_ledger_prune_promoted_elapsed_review_after_flags_review_after_elapsed(tmp / "sl32")
        test_ledger_prune_promoted_missing_skill_md_flags_stale_path_no_crash(tmp / "sl33")

        print()
        print("[ skill-ledger: REM-FIX (task #59, code-reviewer + silent-failure-hunter re-pass) ]")
        test_ledger_prune_promoted_missing_name_flags_missing_promoted_skill_name(tmp / "sl34")
        test_ledger_prune_promoted_blank_name_flags_missing_promoted_skill_name(tmp / "sl35")
        test_ledger_prune_promoted_unparseable_review_after_flags_unparseable(tmp / "sl36")
        test_ledger_prune_promoted_referenced_path_escapes_project_root_flags_stale_path(tmp / "sl37")
        test_ledger_prune_promoted_name_escapes_project_root_flags_stale_path(tmp / "sl38")
        test_ledger_prune_multi_entry_malformed_promoted_entry_does_not_affect_healthy_entry(tmp / "sl40")
        test_ledger_prune_honors_state_dir_when_ledger_flag_left_at_default(tmp / "sl39")

        print()
        print("[ skill-ledger: REM-FIX (1a-SCOPE code-reviewer + silent-failure-hunter findings) ]")
        test_ledger_lru_eviction_exempts_rejected_and_promoted_at_200_cap(tmp / "sl9")
        test_ledger_observe_rejects_relative_traversal_wf_id(tmp / "sl10")
        test_ledger_observe_rejects_absolute_path_wf_id(tmp / "sl11")
        test_ledger_observe_tolerates_non_utf8_events_file(tmp / "sl12")
        test_ledger_observe_acquires_and_releases_lock(tmp / "sl13")
        test_ledger_observe_tolerates_malformed_entries_missing_fields(tmp / "sl28")
        test_ledger_observe_identity_pinned_to_wf_id_not_workflow_uuid(tmp / "sl14")
        test_ledger_observe_repeat_calls_do_not_duplicate_evidence(tmp / "sl15")

        print()
        print("[ skill-ledger: severity-extraction calibration (REM-FIX round 2) ]")
        test_ledger_severity_recognizes_project_specific_words(tmp / "sl16")
        test_ledger_severity_prefix_zero_count_is_not_that_severity(tmp / "sl17")
        test_ledger_severity_picks_highest_nonzero_severity_mentioned(tmp / "sl18")
        test_ledger_severity_zero_count_does_not_mask_other_nonzero_mention(tmp / "sl19")
        test_ledger_severity_recognizes_camelcase_concatenated_word_with_count(tmp / "sl20")
        test_ledger_severity_word_followed_by_zero_count_still_not_that_severity_regression(tmp / "sl21")

        print()
        print("[ skill-ledger: gate threshold calibration (REM-FIX round 3) ]")
        test_ledger_gate_eligible_two_distinct_workflows_any_severity(tmp / "sl22")
        test_ledger_gate_eligible_one_distinct_workflow_still_not_eligible(tmp / "sl23")

        print()
        print("[ skill-ledger: --reject flag (Phase 3: router approval-flow wiring) ]")
        test_ledger_reject_sets_status_and_reason(tmp / "sl24")
        test_ledger_reject_unknown_candidate_id_fails_closed(tmp / "sl25")

        print()
        print("[ skill-ledger: --reject status guard (REM-FIX: concurrent-promote race) ]")
        test_ledger_reject_already_promoted_candidate_fails_closed(tmp / "sl26")

        print()
        print("[ skill-ledger: main() OSError wrapping (REM-FIX: MEDIUM, mirrors skill_promote.py) ]")
        test_ledger_main_wraps_oserror_as_clean_json_not_traceback(tmp / "sl27")

        print()
        print("[ skill-promote (Phase 2: craftflow_skill_promote.py) ]")
        test_promote_refuses_short_description(tmp / "sp1")
        test_promote_refuses_missing_name(tmp / "sp2")
        test_promote_refuses_both_skill_md_and_patch_present(tmp / "sp3")
        test_promote_refuses_neither_present(tmp / "sp4")
        test_promote_refuses_path_traversal_candidate_id(tmp / "sp5")
        test_promote_refuses_unsafe_skill_name(tmp / "sp6")
        test_promote_writes_canonical_and_syncs_cursor_symlink(tmp / "sp7")
        test_promote_stale_backup_on_conflicting_cursor_entry(tmp / "sp8")
        test_promote_dereference_fallback_when_symlink_unavailable(tmp / "sp9")
        test_promote_idempotent_when_already_correctly_linked(tmp / "sp10")
        test_promote_marks_ledger_entry_promoted(tmp / "sp11")
        test_promote_applies_update_patch_to_existing_skill(tmp / "sp12")

        print()
        print("[ skill-promote: REM-FIX (items 1-4, 6 of Phase 2 remediation) ]")
        test_promote_critical1_rejects_mismatched_patch_target_and_name(tmp / "sp13")
        test_promote_high2_rejects_nonexistent_project_root(tmp / "sp14")
        test_promote_high4_rejects_patch_target_outside_skills_dir(tmp / "sp15")
        test_promote_high3_concurrent_approve_no_traceback(tmp / "sp16")
        test_promote_medium6_project_root_is_file_returns_json_not_traceback(tmp / "sp17")

        print()
        print("[ skill-promote: REM-FIX round 2 (--approve status precondition, symmetric to --reject) ]")
        test_promote_refuses_already_rejected_candidate(tmp / "sp18")
        test_promote_refuses_candidate_not_in_ledger(tmp / "sp19")
        test_promote_and_reject_share_equivalent_status_precondition_shape()

        print()
        print(
            "[ skill-promote: REM-FIX round 3 (--ledger pointing at a never-created "
            "sibling path must fail closed, not silently best-effort-succeed) ]"
        )
        test_promote_fails_closed_when_ledger_flag_points_to_never_created_sibling_path(tmp / "sp20")

        print()
        print(
            "[ skill-promote: REM-FIX round 5 (cross-candidate name-collision "
            "protection + malformed non-dict ledger entry fail-closed) ]"
        )
        test_promote_refuses_cross_candidate_name_collision(tmp / "sp21")
        test_promote_reapproving_same_already_promoted_candidate_still_refused(tmp / "sp22")
        test_promote_malformed_non_dict_ledger_entry_returns_json_error(tmp / "sp23")
        test_ledger_reject_malformed_non_dict_ledger_entry_returns_json_error(tmp / "sp24")

        print()
        print(
            "[ skill-promote: REM-FIX round 6 (case-fold cross-candidate collision "
            "guard + upsert_candidates malformed-dict-missing-fields degradation) ]"
        )
        test_promote_refuses_case_fold_collision_across_candidates(tmp / "sp25")

        print()
        print("[ skill-propose (REM-FIX round 4: craftflow_skill_propose.py) ]")
        test_propose_refuses_unsafe_candidate_id(tmp / "spp1")
        test_propose_refuses_candidate_not_in_ledger(tmp / "spp2")
        test_propose_refuses_terminal_status_rejected(tmp / "spp3")
        test_propose_refuses_terminal_status_promoted(tmp / "spp4")
        test_propose_refuses_invalid_frontmatter(tmp / "spp5")
        test_propose_stages_valid_candidate_and_updates_ledger_status(tmp / "spp6")
        test_propose_refuses_overwrite_without_flag(tmp / "spp7")
        test_propose_allows_overwrite_with_flag(tmp / "spp8")
        test_propose_holds_ledger_lock_for_the_write_sequence(tmp / "spp9")
        test_propose_refuses_missing_skill_md_file(tmp / "spp10")

        print()
        print("[ pretooluse-guard: REM-FIX (item 5, skill-promotion-path protection) ]")
        test_pretooluse_guard_denies_edit_write_to_claude_skills_skill_md(tmp / "pg1")
        test_pretooluse_guard_denies_edit_write_to_cursor_skills_skill_md(tmp / "pg2")
        test_pretooluse_guard_denies_bash_redirect_to_claude_skills_skill_md(tmp / "pg3")

        print()
        print("[ pretooluse-guard: REM-FIX round 2 (narrow skill-promotion-path to in-flight ledger candidates) ]")
        test_pretooluse_guard_allows_unrelated_hand_authored_skill_write_no_ledger(tmp / "pg4")
        test_pretooluse_guard_denies_python_oneliner_write_to_inflight_skill(tmp / "pg5")
        test_pretooluse_guard_denies_python_os_system_write_to_inflight_skill(tmp / "pg6")
        test_pretooluse_guard_denies_python_heredoc_write_to_inflight_skill(tmp / "pg7")

        print()
        print(
            "[ pretooluse-guard: REM-FIX round 3 (ledger/proposal tamper protection "
            "+ fail-closed on ledger corruption) ]"
        )
        test_pretooluse_guard_denies_tamper_then_write_via_ledger_write(tmp / "pg8")
        test_pretooluse_guard_denies_write_to_skill_proposal_file(tmp / "pg9")
        test_pretooluse_guard_denies_write_to_not_yet_existing_proposal_path(tmp / "pg9b")
        test_pretooluse_guard_denies_bash_redirect_to_skill_ledger(tmp / "pg10")
        test_pretooluse_guard_denies_write_when_ledger_json_is_malformed(tmp / "pg11")
        test_pretooluse_guard_denies_any_skill_write_when_ledger_malformed_no_matching_candidate(tmp / "pg12")

        print()
        print("[ pretooluse-guard: Phase 4 (reliability-gates ledger protection) ]")
        test_pretooluse_guard_denies_edit_write_to_reliability_gates_ledger(tmp / "pg-rg1")
        test_pretooluse_guard_denies_bash_redirect_to_reliability_gates_ledger(tmp / "pg-rg2")
        test_pretooluse_guard_allows_authorized_reliability_gates_script_bash_invocation(tmp / "pg-rg3")

        print()
        print(
            "[ pretooluse-guard: REM-FIX round 4 (unconditional skill-proposals-tree "
            "protection + malformed-candidate-entry fail-closed) ]"
        )
        test_pretooluse_guard_denies_bash_redirect_to_not_yet_existing_proposal_path(tmp / "pg13")
        test_pretooluse_guard_denies_python_oneliner_write_to_not_yet_existing_proposal_path(tmp / "pg14")
        test_inflight_skill_promotion_paths_fails_closed_on_malformed_candidate_missing_status(tmp / "pg15")
        test_inflight_skill_promotion_paths_fails_closed_on_malformed_candidate_missing_id(tmp / "pg16")
        test_inflight_skill_promotion_paths_still_skips_legitimately_terminal_candidates(tmp / "pg17")
        test_pretooluse_guard_fails_closed_when_candidate_entry_missing_status(tmp / "pg18")
        print("[ safe-shell-guard ]")
        test_safe_shell_guard_blocks_rm_rf_root(tmp / "sg1")
        test_safe_shell_guard_blocks_sudo_rm_rf_root(tmp / "sg2")
        test_safe_shell_guard_allows_rm_rf_subdir(tmp / "sg3")
        test_safe_shell_guard_blocks_mkfs_variant(tmp / "sg4")
        test_safe_shell_guard_blocks_fork_bomb(tmp / "sg5")
        test_safe_shell_guard_blocks_fork_bomb_renamed(tmp / "sg6")
        test_safe_shell_guard_ignores_non_bash_tool(tmp / "sg7")
        test_safe_shell_guard_allows_benign_command(tmp / "sg8")
        test_safe_shell_guard_recursive_grep_off_by_default(tmp / "sg9")
        test_safe_shell_guard_recursive_grep_blocked_when_opted_in(tmp / "sg10")
        test_safe_shell_guard_recursive_grep_no_false_positive_on_pattern_text(tmp / "sg11")
        test_safe_shell_guard_recursive_grep_no_false_positive_in_unrelated_segment(tmp / "sg12")
        test_safe_shell_guard_blocks_command_builtin_prefix(tmp / "sg13")
        test_safe_shell_guard_allows_command_builtin_benign(tmp / "sg14")
        test_safe_shell_guard_blocks_eval_wrapped_rm_rf_root(tmp / "sg15")
        test_safe_shell_guard_allows_benign_eval_command(tmp / "sg16")
        test_safe_shell_guard_blocks_bash_c_mkfs(tmp / "sg17")
        test_safe_shell_guard_blocks_sh_c_rm_rf_root(tmp / "sg18")
        test_safe_shell_guard_fails_closed_on_unparseable_command(tmp / "sg19")

        print()
        print("[ safe-shell-guard REM-FIX round 2 ]")
        test_safe_shell_guard_blocks_sudo_user_long_flag_space_separated(tmp / "sg20")
        test_safe_shell_guard_blocks_sudo_group_long_flag_space_separated(tmp / "sg21")
        test_safe_shell_guard_allows_sudo_user_long_flag_benign(tmp / "sg22")
        test_safe_shell_guard_fork_bomb_check_bounded_timing(tmp / "sg23")
        test_safe_shell_guard_still_blocks_fork_bomb_embedded_in_long_command(tmp / "sg24")
        test_safe_shell_guard_blocks_env_wrapped_rm_rf_root(tmp / "sg25")
        test_safe_shell_guard_blocks_nice_wrapped_rm_rf_root(tmp / "sg26")
        test_safe_shell_guard_blocks_nohup_wrapped_rm_rf_root(tmp / "sg27")
        test_safe_shell_guard_blocks_timeout_wrapped_rm_rf_root(tmp / "sg28")
        test_safe_shell_guard_blocks_xargs_wrapped_rm_rf_root(tmp / "sg29")
        test_safe_shell_guard_blocks_find_exec_rm_rf_root(tmp / "sg30")
        test_safe_shell_guard_blocks_python3_c_os_system_rm_rf_root(tmp / "sg31")
        test_safe_shell_guard_blocks_perl_e_system_rm_rf_root(tmp / "sg32")
        test_safe_shell_guard_allows_env_wrapped_benign(tmp / "sg33")
        test_safe_shell_guard_allows_timeout_wrapped_benign(tmp / "sg34")
        test_safe_shell_guard_allows_python3_c_benign(tmp / "sg35")
        test_safe_shell_guard_allows_find_without_exec(tmp / "sg36")
        test_safe_shell_guard_blocks_dynamic_var_command_name(tmp / "sg37")
        test_safe_shell_guard_blocks_command_substitution_argv0(tmp / "sg38")
        test_safe_shell_guard_fails_closed_on_dynamic_command_name_generic(tmp / "sg39")

        print()
        print("[ safe-shell-guard REM-FIX round 3 (FINAL for this file) ]")
        test_safe_shell_guard_blocks_fork_bomb_padded_body_well_under_window(tmp / "sg40")
        test_safe_shell_guard_blocks_fork_bomb_padded_body_at_window_boundary(tmp / "sg41")
        test_safe_shell_guard_allows_fork_bomb_padded_past_window_documented_limit(tmp / "sg42")
        test_safe_shell_guard_blocks_brace_expansion_argv0_leading(tmp / "sg43")
        test_safe_shell_guard_blocks_brace_expansion_argv0_trailing(tmp / "sg44")
        test_safe_shell_guard_blocks_brace_expansion_argv0_empty_alt(tmp / "sg45")
        test_safe_shell_guard_blocks_root_target_alongside_unrelated_brace_arg(tmp / "sg45b")
        test_safe_shell_guard_blocks_nice_n_flag_space_separated(tmp / "sg46")
        test_safe_shell_guard_blocks_nice_adjustment_flag_equals_joined(tmp / "sg47")
        test_safe_shell_guard_blocks_timeout_signal_flag_equals_joined(tmp / "sg48")
        test_safe_shell_guard_blocks_timeout_kill_after_flag_space_separated(tmp / "sg49")
        test_safe_shell_guard_blocks_nohup_double_dash_flag(tmp / "sg50")
        test_safe_shell_guard_allows_nice_n_flag_benign(tmp / "sg51")
        test_safe_shell_guard_allows_timeout_plain_benign(tmp / "sg52")
        test_safe_shell_guard_blocks_watch_wrapped_rm_rf_root(tmp / "sg53")
        test_safe_shell_guard_blocks_ssh_wrapped_rm_rf_root(tmp / "sg54")
        test_safe_shell_guard_blocks_su_c_wrapped_rm_rf_root(tmp / "sg55")
        test_safe_shell_guard_blocks_chroot_wrapped_rm_rf_root(tmp / "sg56")
        test_safe_shell_guard_allows_chroot_wrapped_non_root_target_by_design(tmp / "sg57")
        test_safe_shell_guard_blocks_flock_wrapped_rm_rf_root(tmp / "sg58")
        test_safe_shell_guard_allows_ssh_wrapped_benign(tmp / "sg59")
        test_safe_shell_guard_allows_su_without_c_flag(tmp / "sg60")
        test_safe_shell_guard_blocks_catastrophic_command_on_second_line(tmp / "sg61")
        test_safe_shell_guard_allows_benign_multiline_command(tmp / "sg62")

        print()
        print("[ stop-verify ]")
        test_stop_verify_inert_when_unconfigured(tmp / "sv1")
        test_stop_verify_allows_when_command_passes(tmp / "sv2")
        test_stop_verify_blocks_when_command_fails(tmp / "sv3")
        test_stop_verify_never_blocks_on_continuation_stop(tmp / "sv4")

        print()
        print("[ hook-trust ]")
        test_hook_trust_refuses_unknown_script(tmp / "ht1")
        test_hook_trust_allows_manifest_listed_matching_hash(tmp / "ht2")
        test_hook_trust_refuses_hash_mismatch(tmp / "ht3")
        test_hook_trust_update_generates_manifest_then_check_passes(tmp / "ht4")

    test_hook_trust_never_imports_hooklib_directly()

    print()
    print("[ structural ]")
    test_anti_rationalization_tables_present()
    test_doubt_verifier_agent_present()
    test_intent_interview_skill_present()
    test_router_dispatches_doubt_verify()
    test_router_records_reliability_gates_evidence_in_fix_verify_and_doubt_verify()
    test_router_dispatches_intent_interview()
    test_circuit_breaker_uses_persisted_non_telemetry_field_not_live_tasklist_count()
    test_hooks_json_registers_new_hooks()
    test_hooks_json_registers_bash_guard()
    test_hooks_json_registers_pretooluse_guard_on_bash()
    test_hooks_json_registers_safe_shell_guard()
    test_hooks_json_registers_stop_verify()
    test_selfcheck_never_imports_hooklib_directly()
    test_selfcheck_resolves_bare_python3_not_sys_executable()
    test_hooks_json_registers_selfcheck_sessionstart()
    test_root_hooks_json_registers_selfcheck_sessionstart()
    test_cursor_adapter_logs_target_crash_but_stays_fail_open(tmp / "ca1")
    test_selfcheck_internal_budget_stays_under_registered_hook_timeout()
    test_workflow_id_script_present()
    test_resolve_workspace_root_script_present()
    test_reliability_gates_script_present()
    test_worktree_isolation_resolver_gated_on_toplevel_failure()
    test_worktree_isolation_step_4a_derives_from_worktree_path()
    test_section_0_precedes_memory_load()
    test_memory_load_anchored_to_project_root()
    test_parent_workflow_creation_anchored_to_project_root()
    test_parent_workflow_creation_fallback_reason_wired()
    test_shared_preparation_anchored_to_project_root()
    test_worktree_isolation_reuses_project_root_no_duplicate_resolution()
    test_memory_finalization_for_plan_anchored_to_project_tier()
    test_memory_finalization_for_debug_anchored_and_uses_workflow_uuid()
    test_memory_finalization_prelude_anchored_to_project_root()
    test_just_go_and_scope_decision_resume_anchored_to_project_root()
    test_dispatcher_scaffold_workflow_artifact_anchored_to_project_root()
    test_statusline_script_present()
    test_router_uses_workflow_id_helper()
    test_learn_distiller_uses_tools_key_not_allowed_tools()

    print()
    print("[ structural: skill-author / skill-distillation (Phase 2) ]")
    test_skill_author_agent_present()
    test_skill_author_agent_documents_propose_script_invocation()
    test_skill_distillation_skill_present()
    test_rubric_documents_three_rejection_cases()
    test_skill_promote_script_present()

    print()
    print("[ structural: skill-distill router wiring (Phase 3) ]")
    test_router_phase_enum_registers_skill_distill_learn_distill_doubt_verify()
    test_router_dispatcher_table_includes_skill_distill()
    test_router_effort_dispatch_includes_skill_distill_low()
    test_router_contract_overrides_includes_skill_author()
    test_router_memory_finalization_calls_ledger_observe()
    test_router_memory_finalization_calls_ledger_prune()
    test_router_hard_rules_includes_skill_distill_skip()
    test_router_documents_skill_distill_approval_flow()
    test_build_workflow_wires_learn_distill_taskcreate()
    test_build_workflow_wires_skill_distill_taskcreate()
    test_build_workflow_fast_path_graph_wires_learn_and_skill_distill()
    test_debug_workflow_documents_skill_distill_reasoning()
    test_fast_path_agent_dispatch_table_includes_skill_distill()
    test_fast_path_escalated_gate_wiring_reconciled()
    test_fast_path_documents_skill_distill_gate()
    test_workflow_artifact_policy_registers_skill_distill_events()
    test_craftflow_state_mdc_documents_skill_distillation_paths()
    test_cursor_router_wires_skill_distill_gate()

    print()
    print("[ context-usage (Thread E — craftflow's own context awareness) ]")
    test_context_usage_returns_none_when_tokentracker_not_installed()
    test_context_usage_returns_percent_full_on_installed_success()
    test_context_usage_returns_none_on_timeout()
    test_context_usage_returns_none_on_non_zero_exit()
    test_context_usage_returns_none_on_malformed_json()
    test_precompact_context_usage_budget_stays_under_registered_hook_timeout()
    test_postcompact_context_usage_budget_stays_under_registered_hook_timeout()
    test_report_statusline_appends_ctx_segment_when_available()
    test_report_statusline_omits_ctx_segment_when_unavailable()
    test_report_statusline_colors_ctx_segment_red_at_critical()
    test_build_json_output_includes_context_usage_key()
    test_build_json_output_context_usage_none_when_unavailable()
    test_precompact_build_snapshot_includes_context_usage_when_available()
    test_precompact_build_snapshot_context_usage_none_when_unavailable()
    test_postcompact_build_event_includes_context_usage_when_available()
    test_postcompact_build_event_context_usage_none_when_unavailable()

    print()
    print("[ craftflow_memory_merge: CLI-level provenance smoke test ]")
    test_memory_merge_cli_accepts_provenance_field_on_notes()
    test_memory_merge_cli_non_numeric_confidence_fails_cleanly()
    test_memory_merge_cli_non_integer_max_bullets_fails_cleanly()
    test_memory_merge_cli_empty_section_with_file_text_fails_cleanly()
    test_memory_merge_cli_nan_confidence_fails_cleanly()

    print()
    print("[ craftflow_memory_merge: archive-aware bullet eviction (Phase 5) ]")
    test_memory_merge_apply_cap_backward_compatible_without_archive()
    test_memory_merge_apply_cap_archives_instead_of_dropping()
    test_memory_merge_apply_cap_archive_preserves_organic_priority()
    test_memory_merge_cli_without_archive_field_unchanged_output()
    test_memory_merge_cli_with_archive_field_emits_json_envelope()
    test_memory_merge_archive_rotation_zero_data_loss_realistic_fixture()

    print()
    print("[ pretooluse-guard / pretooluse-bash-guard: REM-FIX cycle 4 (non-dict JSON top-level crash class) ]")
    test_pretooluse_guard_non_dict_stdin_top_level_does_not_crash_degrades_to_allow(tmp / "j1")
    test_pretooluse_guard_non_dict_hook_mode_json_does_not_crash_degrades_to_fail_closed_deny(tmp / "j2")
    test_pretooluse_guard_truthy_non_dict_tool_input_degrades_to_empty_dict_no_crash(tmp / "j3")
    test_bash_guard_non_dict_stdin_top_level_does_not_crash_degrades_to_allow(tmp / "j4")
    test_bash_guard_non_dict_hook_mode_json_does_not_crash_still_denies_destructive_command(tmp / "j5")
    test_bash_guard_truthy_non_dict_tool_input_degrades_to_empty_dict_no_crash(tmp / "j6")

    print()
    print("[ router: workspace-root allowlist wiring (Phase 4) ]")
    test_workspace_root_config_read_gated_inside_step_1a()
    test_workflow_artifact_template_includes_workspace_writable_paths_field()

    print()
    print("[ craftflow_state_query.py: skeleton + --mode full ]")
    test_state_query_full_mode_byte_identical(tmp / "q1")
    test_state_query_full_mode_missing_file_errors_cleanly(tmp / "q2")

    print()
    print("[ craftflow_state_query.py: workflow-JSON summary mode ]")
    test_state_query_summarize_workflow_json_shrinks_and_keeps_key_fields(tmp / "q3")
    test_state_query_summarize_workflow_json_malformed_falls_open(tmp / "q4")

    print()
    print("[ craftflow_state_query.py: events.jsonl summary mode ]")
    test_state_query_summarize_events_jsonl_default_tail_and_footer(tmp / "q5")
    test_state_query_summarize_events_jsonl_event_type_filter(tmp / "q6")
    test_state_query_summarize_events_jsonl_all_malformed_never_crashes(tmp / "q7")

    print()
    print("[ craftflow_state_query.py: markdown + generic summary modes ]")
    test_state_query_summarize_markdown_caps_bullets_per_section(tmp / "q8")
    test_state_query_summarize_markdown_no_headings_falls_back_to_generic(tmp / "q9")
    test_state_query_summarize_markdown_truncates_large_non_bulleted_section(tmp / "q10")
    test_state_query_summarize_markdown_real_shape_meaningful_reduction(tmp / "q11")
    test_state_query_summarize_markdown_short_non_bulleted_section_stays_verbatim(tmp / "q12")

    print()
    print("[ pretooluse-guard: state-read compaction (Read PreToolUse) ]")
    test_pretooluse_guard_denies_oversized_state_read(tmp / "r1")
    test_pretooluse_guard_allows_under_threshold_state_read(tmp / "r2")
    test_pretooluse_guard_allows_oversized_read_outside_state_dir(tmp / "r3")
    test_pretooluse_guard_read_branch_does_not_affect_edit_write_dispatch(tmp / "r4")

    print()
    print("[ state-read-compaction: end-to-end integration ]")
    test_state_read_compaction_end_to_end(tmp / "r5")

    print()
    print("[ craftflow-router: shared router-protocol extraction, no stale re-embed (item 8 Phase 3) ]")
    test_craftflow_router_shared_protocol_extraction_no_stale_reembed()

    print()
    print("[ craftflow-router: state-read compaction self-heal doc (Phase 3) ]")
    test_craftflow_router_documents_state_read_compaction_self_heal()

    print()
    print("[ craftflow-router: memory-finalize sites use --mode full (Phase 4) ]")
    test_memory_finalize_instruction_sites_use_state_query_full_mode()

    print()
    print("[ craftflow-router: memory-finalize sites wired for archive rotation (Phase 6) ]")
    test_memory_finalize_instruction_sites_wire_archive_rotation()

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
