#!/usr/bin/env python3
"""Tests for craftflow_workflow_id and the new progress/statusline functions.

Covers:
  - slugify() edge cases
  - _is_feature_branch() branch-type detection
  - mint_workflow_id() id structure, concurrency, branch-first vs request
  - craftflow_workflow_id.py CLI (subprocess)
  - craftflow_status_report: _display_label(), _compute_progress(), --statusline mode

Run: python3 tests/fixtures/test_workflow_id_and_progress.py
"""

import json
import os
import subprocess
import sys
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_workflow_id import (  # noqa: E402
    slugify,
    _is_feature_branch,
    mint_workflow_id,
)

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


# ---------------------------------------------------------------------------
# slugify()
# ---------------------------------------------------------------------------

def test_slugify() -> None:
    print("\n[slugify]")

    cases = [
        # (input, expected_output)
        ("Add auth refactor to login flow", "auth-refactor-login-flow"),
        ("!!!  ???",                         "task"),               # empty → fallback
        ("",                                 "task"),               # truly empty
        ("my-cool-thing",                   "cool-thing"),         # leading stopword "my"
        ("FixBug: NullPointer in LoginService", "fixbug-nullpointer-loginservice"),
        ("the quick brown fox",             "quick-brown-fox"),    # drop "the"
        ("feature-named-thing",             "feature-named-thing"),
        # Length cap: 32 chars (first 6 significant tokens joined, then truncated)
        ("very long feature request text that exceeds the cap for slugification", "very-long-feature-request-text-e"),
    ]

    for text, expected in cases:
        result = slugify(text)
        if result == expected:
            ok(f"slugify({text!r}) == {expected!r}")
        else:
            fail(f"slugify({text!r})", f"expected {expected!r}, got {result!r}")

    # Result must always be git-ref-safe: only [a-z0-9-], no leading/trailing -
    import random, string
    random.seed(42)
    for _ in range(20):
        rnd = "".join(random.choices(string.printable, k=30))
        s = slugify(rnd)
        if not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$', s):
            fail("slugify-safety", f"unsafe slug {s!r} from {rnd!r}")
        else:
            ok(f"slugify-safety({rnd[:15]!r}...) → {s!r} is safe")


# ---------------------------------------------------------------------------
# _is_feature_branch()
# ---------------------------------------------------------------------------

def test_is_feature_branch() -> None:
    print("\n[_is_feature_branch]")

    should_reject = [
        "main", "master", "develop", "dev", "trunk", "HEAD",
        "wf-auth-refactor-20260706-d4e5f6a7",  # craftflow id branch
        "worktree-wf-d4e5f6a7",                 # craftflow worktree
        "wf-auth-20260706-d4e5f6a7",
        "",
    ]
    for branch in should_reject:
        if _is_feature_branch(branch):
            fail(f"_is_feature_branch({branch!r})", "should have returned False")
        else:
            ok(f"rejects base/craftflow branch: {branch!r}")

    should_accept = [
        "feature/add-login",
        "feat/auth-refactor",
        "bugfix/null-pointer",
        "my-cool-feature",
        "JIRA-1234-fix-thing",
        "release/v2.0",
    ]
    for branch in should_accept:
        if not _is_feature_branch(branch):
            fail(f"_is_feature_branch({branch!r})", "should have returned True")
        else:
            ok(f"accepts feature branch: {branch!r}")


# ---------------------------------------------------------------------------
# mint_workflow_id()
# ---------------------------------------------------------------------------

def test_mint_workflow_id() -> None:
    print("\n[mint_workflow_id]")

    # Basic: request-slugify path (branch = main)
    result = mint_workflow_id(request="Add auth refactor", branch="main")
    wf_uuid = result["workflow_uuid"]
    if not wf_uuid.startswith("wf-auth-refactor-"):
        fail("basic-request-slug", f"expected wf-auth-refactor-*, got {wf_uuid}")
    else:
        ok(f"basic request slug: {wf_uuid}")

    # Verify format: wf-{slug}-{YYYYMMDD}-{HHMMSS}-{8hex}
    m = re.match(r'^wf-(.+)-(\d{8})-(\d{6})-([0-9a-f]{8})$', wf_uuid)
    if not m:
        fail("id-format", f"id {wf_uuid!r} does not match wf-{{slug}}-{{date}}-{{time}}-{{hex}}")
    else:
        ok(f"id format valid: {wf_uuid}")

    # Worktree names derived correctly
    expected_dir = f"{result['slug']}-{result['short_hex']}"
    expected_branch = f"wf-{result['slug']}-{result['short_hex']}"
    if result["worktree_dir"] != expected_dir:
        fail("worktree_dir", f"expected {expected_dir!r}, got {result['worktree_dir']!r}")
    else:
        ok(f"worktree_dir: {expected_dir}")
    if result["worktree_branch"] != expected_branch:
        fail("worktree_branch", f"expected {expected_branch!r}, got {result['worktree_branch']!r}")
    else:
        ok(f"worktree_branch: {expected_branch}")

    # Branch-first: genuine feature branch
    result_fb = mint_workflow_id(request="something else", branch="feature/my-cool-thing")
    if not result_fb["workflow_uuid"].startswith("wf-cool-thing-"):
        fail("branch-first", f"expected wf-cool-thing-*, got {result_fb['workflow_uuid']}")
    else:
        ok(f"branch-first slug: {result_fb['workflow_uuid']}")

    # Craftflow-generated branch → falls back to request
    result_cf = mint_workflow_id(request="actual feature", branch="worktree-wf-deadbeef")
    if not result_cf["workflow_uuid"].startswith("wf-actual-feature-"):
        fail("craftflow-branch-fallback", f"expected wf-actual-feature-*, got {result_cf['workflow_uuid']}")
    else:
        ok(f"craftflow branch → request fallback: {result_cf['workflow_uuid']}")

    # Concurrency: same request → same slug, DIFFERENT hex
    r1 = mint_workflow_id(request="same feature request", branch="main")
    r2 = mint_workflow_id(request="same feature request", branch="main")
    if r1["slug"] != r2["slug"]:
        fail("concurrency-slug", f"slugs differ: {r1['slug']!r} vs {r2['slug']!r}")
    else:
        ok(f"concurrent slugs match: {r1['slug']!r}")
    if r1["short_hex"] == r2["short_hex"]:
        fail("concurrency-hex", f"hex collision: both got {r1['short_hex']!r}")
    else:
        ok(f"concurrent hex differs: {r1['short_hex']!r} vs {r2['short_hex']!r}")
    if r1["workflow_uuid"] == r2["workflow_uuid"]:
        fail("concurrency-uuid", "full uuid collision — concurrency guarantee broken")
    else:
        ok("full uuid distinct under concurrency")

    # Empty / garbage request → slug fallback to 'task'
    r_empty = mint_workflow_id(request="!!!  ???", branch="main")
    if not r_empty["workflow_uuid"].startswith("wf-task-"):
        fail("empty-slug-fallback", f"expected wf-task-*, got {r_empty['workflow_uuid']}")
    else:
        ok(f"empty slug → 'task' fallback: {r_empty['workflow_uuid']}")

    # iso_timestamp must be a valid ISO 8601 string
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(result["iso_timestamp"].replace("Z", "+00:00"))
        ok(f"iso_timestamp parses: {result['iso_timestamp']}")
    except Exception as exc:
        fail("iso_timestamp", f"cannot parse {result['iso_timestamp']!r}: {exc}")


# ---------------------------------------------------------------------------
# craftflow_workflow_id.py CLI
# ---------------------------------------------------------------------------

def test_workflow_id_cli() -> None:
    print("\n[craftflow_workflow_id CLI]")

    script = str(SCRIPTS / "craftflow_workflow_id.py")

    # Default output: bare uuid
    result = subprocess.run(
        [sys.executable, script, "--request", "Add login feature", "--branch", "main"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail("cli-default", f"non-zero exit: {result.returncode}\n{result.stderr}")
        return
    uuid = result.stdout.strip()
    if not re.match(r'^wf-[a-z0-9-]+-\d{8}-\d{6}-[0-9a-f]{8}$', uuid):
        fail("cli-default-format", f"unexpected uuid: {uuid!r}")
    else:
        ok(f"cli default output: {uuid}")

    # --json output
    result_json = subprocess.run(
        [sys.executable, script, "--request", "Add login feature", "--branch", "main", "--json"],
        capture_output=True, text=True,
    )
    try:
        d = json.loads(result_json.stdout)
        required_keys = {"workflow_uuid", "slug", "short_hex", "timestamp", "iso_timestamp",
                         "worktree_dir", "worktree_branch"}
        missing = required_keys - d.keys()
        if missing:
            fail("cli-json-keys", f"missing keys: {missing}")
        else:
            ok(f"cli --json has all required keys")
    except json.JSONDecodeError as exc:
        fail("cli-json-parse", f"invalid JSON: {exc}\noutput: {result_json.stdout!r}")

    # Missing --request should fail
    result_noreq = subprocess.run(
        [sys.executable, script, "--branch", "main"],
        capture_output=True, text=True,
    )
    if result_noreq.returncode == 0:
        fail("cli-missing-request", "expected non-zero exit when --request missing")
    else:
        ok("cli fails cleanly without --request")


# ---------------------------------------------------------------------------
# craftflow_status_report: _display_label(), _compute_progress()
# ---------------------------------------------------------------------------

def _import_status_report():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "craftflow_status_report",
        str(SCRIPTS / "craftflow_status_report.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    # Stub CLAUDE_PROJECT_DIR so hooklib doesn't error on import
    os.environ.setdefault("CLAUDE_PROJECT_DIR", str(PLUGIN_ROOT.parent.parent.parent))
    spec.loader.exec_module(mod)
    return mod


def test_display_label() -> None:
    print("\n[_display_label]")
    sr = _import_status_report()

    # New slug-style id → extract slug
    wf_id = "wf-auth-refactor-20260706-140312-d4e5f6a7"
    label = sr._display_label(wf_id, {})
    if label != "auth-refactor":
        fail("display-label-slug", f"expected 'auth-refactor', got {label!r}")
    else:
        ok(f"slug extracted from slug-id: {label!r}")

    # Multi-word slug
    wf_id2 = "wf-auth-refactor-login-flow-20260706-140312-d4e5f6a7"
    label2 = sr._display_label(wf_id2, {})
    if label2 != "auth-refactor-login-flow":
        fail("display-label-multiword", f"expected 'auth-refactor-login-flow', got {label2!r}")
    else:
        ok(f"multi-word slug: {label2!r}")

    # Old-format id → falls back to goal
    old_id = "wf-20260702-140000-d4e5f6a7"
    payload_with_goal = {"intent": {"goal": "My feature description here"}}
    label3 = sr._display_label(old_id, payload_with_goal)
    if "My feature" not in label3:
        fail("display-label-old-format-goal", f"expected goal text, got {label3!r}")
    else:
        ok(f"old-format id → goal fallback: {label3!r}")

    # Old-format id, no goal → short hex fallback
    label4 = sr._display_label(old_id, {})
    if label4 != "d4e5f6a7":
        fail("display-label-hex-fallback", f"expected 'd4e5f6a7', got {label4!r}")
    else:
        ok(f"old-format id → hex fallback: {label4!r}")


def test_compute_progress() -> None:
    print("\n[_compute_progress]")
    sr = _import_status_report()

    FAKE_WF_ID = "wf-test-20260706-000000-00000000"

    # 1. DONE workflow (status_history tail = memory_finalized)
    payload_done = {
        "pending_gate": None,
        "status_history": [{"event": "memory_finalized", "ts": "2026-07-06T00:00:00Z"}],
        "normalized_phases": [],
        "phase_status": {},
        "phase_cursor": None,
    }
    prog_done = sr._compute_progress(payload_done, FAKE_WF_ID)
    if prog_done["pct"] != 100 or prog_done["source"] != "done":
        fail("progress-done", f"expected 100%/done, got {prog_done}")
    else:
        ok(f"DONE workflow → 100% (source: done)")

    # 2. normalized_phases path
    payload_phases = {
        "pending_gate": None,
        "status_history": [{"event": "workflow_started"}],
        "normalized_phases": [
            {"phase_id": "phase_1"}, {"phase_id": "phase_2"}, {"phase_id": "phase_3"}
        ],
        "phase_status": {"phase_1": "completed", "phase_2": "skipped"},
        "phase_cursor": "phase_3",
    }
    prog_phases = sr._compute_progress(payload_phases, FAKE_WF_ID)
    if prog_phases["done"] != 2 or prog_phases["total"] != 3:
        fail("progress-phases", f"expected 2/3, got {prog_phases}")
    else:
        ok(f"normalized_phases → {prog_phases['done']}/{prog_phases['total']} = {prog_phases['pct']}%")

    # partial is NOT counted as done (binary counting)
    payload_partial = {
        "pending_gate": None,
        "status_history": [{"event": "workflow_started"}],
        "normalized_phases": [{"phase_id": "p1"}, {"phase_id": "p2"}],
        "phase_status": {"p1": "completed", "p2": "partial"},
        "phase_cursor": "p2",
    }
    prog_partial = sr._compute_progress(payload_partial, FAKE_WF_ID)
    if prog_partial["done"] != 1:
        fail("progress-partial-not-done", f"partial should not count as done, got {prog_partial}")
    else:
        ok(f"partial phase is NOT counted as done: {prog_partial['done']}/2")

    # 3. phase_status only (no normalized_phases)
    payload_status_only = {
        "pending_gate": None,
        "status_history": [{"event": "workflow_started"}],
        "normalized_phases": [],
        "phase_status": {"phase_1": "completed"},
        "phase_cursor": "review-audit",
    }
    prog_status = sr._compute_progress(payload_status_only, FAKE_WF_ID)
    if prog_status["source"] != "status" or prog_status["done"] != 1:
        fail("progress-status-only", f"expected source=status, done=1, got {prog_status}")
    else:
        ok(f"phase_status only → {prog_status['done']}/{prog_status['total']} (source: status)")

    # 4. coarse stage estimate (no phase data at all)
    payload_stage = {
        "pending_gate": None,
        "status_history": [{"event": "fast_path_selected"}],
        "normalized_phases": [],
        "phase_status": {},
        "phase_cursor": None,
    }
    prog_stage = sr._compute_progress(payload_stage, FAKE_WF_ID)
    if prog_stage["source"] != "stage" or prog_stage["pct"] != 15:
        fail("progress-stage", f"expected 15%/stage for fast_path_selected, got {prog_stage}")
    else:
        ok(f"stage estimate (fast_path_selected) → {prog_stage['pct']}% (source: stage)")

    # total=0 when using stage estimate
    if prog_stage["total"] != 0:
        fail("progress-stage-total", f"expected total=0 for stage source, got {prog_stage['total']}")
    else:
        ok("stage estimate total=0 (no tail)")


def test_statusline_output_format() -> None:
    print("\n[--statusline output format]")

    script = str(SCRIPTS / "craftflow_status_report.py")
    project = str(PLUGIN_ROOT.parent.parent.parent)  # tools/ dir

    result = subprocess.run(
        [sys.executable, script, "--statusline", "--project", project],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail("statusline-exit", f"non-zero exit: {result.returncode}\n{result.stderr}")
        return

    line = result.stdout.strip()
    if line:
        # Check format: starts with ⚡, has a %
        if not line.startswith("⚡"):
            fail("statusline-format-prefix", f"expected ⚡ prefix, got: {line!r}")
        elif "%" not in line:
            fail("statusline-format-pct", f"expected % in output, got: {line!r}")
        else:
            ok(f"statusline output: {line!r}")
    else:
        ok("statusline empty (no active workflow — silent exit)")

    # With --project pointing at a dir without .craftflow → silent exit, code 0
    result_empty = subprocess.run(
        [sys.executable, script, "--statusline", "--project", "/tmp"],
        capture_output=True, text=True,
    )
    if result_empty.returncode != 0:
        fail("statusline-silent-exit", f"expected exit 0 for missing project, got {result_empty.returncode}")
    elif result_empty.stdout.strip():
        fail("statusline-silent-output", f"expected empty output for missing project, got: {result_empty.stdout!r}")
    else:
        ok("--statusline silent exit-0 when no .craftflow/")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    print("test_workflow_id_and_progress: running")

    test_slugify()
    test_is_feature_branch()
    test_mint_workflow_id()
    test_workflow_id_cli()
    test_display_label()
    test_compute_progress()
    test_statusline_output_format()

    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1

    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
