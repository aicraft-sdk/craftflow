#!/usr/bin/env python3
"""Subprocess payload tests for the P5 policy guards (router context cut r2, ADR-0073).

Covers BC-2 (EnterPlanMode), BC-3 (plan_commit, E14-E33d), BC-5 (worktree-merge clean tree, E34-E36c), DD-12 (Cursor
scope), PP-1 (an invalid policy never widens) and PP-7 (allow paths are silent). Every case runs the real hook scripts
as subprocesses against scratch git repos.

Run: python3 tests/fixtures/test_policy_guards.py
Prints `Results: N passed, M failed`; exits 1 on any failure.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
MAIN_GUARD = SCRIPTS / "craftflow_pretooluse_guard.py"
BASH_GUARD = SCRIPTS / "craftflow_pretooluse_bash_guard.py"
ADAPTER = SCRIPTS / "craftflow_cursor_adapter.py"
POLICY_FILE = PLUGIN_ROOT / "config" / "policy.json"

_passes = 0
_errors = []
_scratch = []


def ok(name):
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name, reason):
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def check(name, cond, reason=""):
    if cond:
        ok(name)
    else:
        fail(name, reason or "assertion false")


def scratch_dir(prefix="p5g-"):
    path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    _scratch.append(path)
    return path


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"git {args} failed: {result.stderr}")
    return result.stdout


def make_repo(managed=True, ignore_plans=True):
    """A scratch git repo with one commit, docs/plans/x.md present, and (optionally) .craftflow/state/ + plans ignored."""
    root = scratch_dir()
    git(root, "init", "-q")
    git(root, "config", "gc.auto", "0")  # a background gc would prune the hand-built worktree admin dirs below
    (root / "README.md").write_text("r\n")
    (root / "src").mkdir()
    (root / "src" / "x.ts").write_text("x\n")
    (root / "src" / "a.txt").write_text("a\n")
    if ignore_plans:
        (root / ".gitignore").write_text("docs/plans/\n.craftflow/\n")
    (root / "docs" / "plans").mkdir(parents=True)
    (root / "docs" / "plans" / ".gitkeep").write_text("")
    (root / "docs" / "plans" / "x.md").write_text("plan\n")
    (root / "docs" / "plans" / "a b.md").write_text("plan\n")
    if managed:
        (root / ".craftflow" / "state" / "workflows").mkdir(parents=True)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init", "--no-gpg-sign")
    return root


def plugin_copy(policy=None):
    """A copied scripts/ + config/ tree so a test can set config/policy.json without touching the shipped file."""
    root = scratch_dir("p5g-plugin-")
    shutil.copytree(SCRIPTS, root / "scripts", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(PLUGIN_ROOT / "config", root / "config")
    path = root / "config" / "policy.json"
    if policy is None:
        path.unlink()
    elif isinstance(policy, str):
        path.write_text(policy)
    else:
        path.write_text(json.dumps(policy))
    return root


def run(script, payload, cwd, project=None, plugin=None, env_set=None, env_del=(), path_prefix=None, raw=None):
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin or PLUGIN_ROOT)
    env["CLAUDE_PROJECT_DIR"] = str(project or cwd)
    env.pop("CURSOR_PLUGIN_ROOT", None)
    for key in env_del:
        env.pop(key, None)
    env.update(env_set or {})
    if path_prefix:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    data = raw if raw is not None else json.dumps(payload)
    script_path = Path(script)
    if plugin and script_path.parent == SCRIPTS:
        script_path = Path(plugin) / "scripts" / script_path.name
    proc = subprocess.run([sys.executable, str(script_path)], input=data, capture_output=True, text=True, cwd=str(cwd), env=env, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def decision(stdout):
    """(permissionDecision, reason) from the single deny JSON line, or (None, "") for empty stdout."""
    if not stdout.strip():
        return None, ""
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if len(lines) != 1:
        return "multi-line", stdout
    out = json.loads(lines[0])["hookSpecificOutput"]
    return out["permissionDecision"], out["permissionDecisionReason"]


def bash(cmd, cwd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd), "session_id": "s-p5"}


def events(project, name):
    log = Path(project) / ".craftflow" / "state" / "craftflow-hook-events.log"
    if not log.exists():
        return []
    out = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    return [e for e in out if e.get("event") == name]


# --------------------------------------------------------------------------------------------------------------------
# EnterPlanMode (BC-2, E11-E13)
# --------------------------------------------------------------------------------------------------------------------


def enterplanmode_denied_by_default():
    cwd = scratch_dir()
    rc, out, err = run(MAIN_GUARD, {"tool_name": "EnterPlanMode", "tool_input": {}, "cwd": str(cwd)}, cwd)
    verdict, reason = decision(out)
    check("enterplanmode_denied_by_default", rc == 0 and verdict == "deny" and reason.startswith("craftflow-policy: native_plan_mode=never —")
          and "PLAN workflow" in reason, f"rc={rc} out={out!r}")
    for label, tool_input in (("null", None), ("list", ["a"]), ("string", "x"), ("empty", {})):
        rc, out, _ = run(MAIN_GUARD, {"tool_name": "EnterPlanMode", "tool_input": tool_input, "cwd": str(cwd)}, cwd)
        check(f"enterplanmode_nondict_input_{label}", rc == 0 and decision(out)[0] == "deny", f"rc={rc} out={out!r}")
    allow = plugin_copy({"native_plan_mode": "allow"})
    rc, out, err = run(MAIN_GUARD, {"tool_name": "EnterPlanMode", "tool_input": {}, "cwd": str(cwd)}, cwd, plugin=allow)
    check("enterplanmode_allowed_when_allow", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r} err={err!r}")
    invalid = plugin_copy('{"native_plan_mode": "bogus"}')
    rc, out, _ = run(MAIN_GUARD, {"tool_name": "EnterPlanMode", "tool_input": {}, "cwd": str(cwd)}, cwd, plugin=invalid)
    verdict, reason = decision(out)
    check("enterplanmode_denied_when_policy_invalid", rc == 0 and verdict == "deny" and reason.startswith("craftflow-policy: policy-invalid —"),
          f"rc={rc} out={out!r}")
    repo = make_repo()
    rc, out, _ = run(MAIN_GUARD, {"tool_name": "EnterPlanMode", "tool_input": {}, "cwd": str(repo)}, repo)
    hits = events(repo, "native_plan_mode_denied")
    check("enterplanmode_deny_logs_event", len(hits) == 1 and hits[0].get("native_plan_mode") == "never", f"hits={hits}")
    rc, out, err = run(MAIN_GUARD, {"tool_name": "Grep", "tool_input": {}, "cwd": str(cwd)}, cwd)
    check("other_tools_untouched", rc == 0 and "native_plan_mode" not in out and "native_plan_mode" not in err, f"rc={rc} out={out!r}")


# --------------------------------------------------------------------------------------------------------------------
# plan_commit (BC-3, E14-E33d)
# --------------------------------------------------------------------------------------------------------------------

DENY_CASES = [
    ("e14_add_plan", "git add docs/plans/x.md", "."),
    ("e15_dash_c_sub", "git -C sub add ../docs/plans/x.md", "."),
    ("e16_dot_slash", "git add ./docs/plans/x.md", "."),
    ("e17_double_dash", "git add -- docs/plans/x.md", "."),
    ("e18_quoted_space", "git add 'docs/plans/a b.md'", "."),
    ("e19_glob", "git add docs/plans/*.md", "."),
    ("e20_force_docs", "git add -f docs", "."),
    ("e20_force_dot", "git add -f .", "."),
    ("e20_all_force", "git add -A -f", "."),
    ("e20_cluster_force_all", "git add -Af", "."),
    ("e20_root_pathspec", "git add -f :/", "."),
    ("e24_commit_pathspec", "git commit -m x docs/plans/x.md", "."),
    ("e24_commit_only", "git commit --only docs/plans/x.md", "."),
    ("e24_commit_cluster_msg", "git commit -am x -- docs/plans/x.md", "."),
    ("e29_git_dir", "git --git-dir=.git add docs/plans/x.md", "."),
    ("e30_cd_then_add", "cd src && git add ../docs/plans/x.md", "."),
    ("e31_second_subcommand", "git add src/a.txt; git add docs/plans/x.md", "."),
    ("e32_workflow_json", "git add -f .craftflow/state/workflows/wf-x.json", "."),
    ("stage_alias", "git stage docs/plans/x.md", "."),
    ("from_subdir_cwd", "git add ../docs/plans/x.md", "src"),
]

ALLOW_CASES = [
    ("e21_add_docs_no_force", "git add docs", "."),
    ("e22_gitkeep", "git add docs/plans/.gitkeep", "."),
    ("e23_message_text", 'git commit -m "docs/plans/x.md"', "."),
    ("e28_dynamic_var", 'git add "$F"', "."),
    ("e28_dynamic_dash_c", "git -C $(pwd) add docs/plans/x.md", "."),
    ("ordinary_add", "git add src/x.ts", "."),
    ("ordinary_commit", "git commit -m x", "."),
    ("status_untouched", "git status docs/plans/x.md", "."),
    ("not_git", "echo git add docs/plans/x.md", "."),
]


def test_plan_commit_table():
    repo = make_repo()
    (repo / "sub").mkdir()
    for name, cmd, rel in DENY_CASES:
        cwd = repo / rel if rel != "." else repo
        rc, out, err = run(BASH_GUARD, bash(cmd, cwd), cwd, project=repo)
        verdict, reason = decision(out)
        check(f"plan_commit_{name}_denied", rc == 0 and verdict == "deny" and "craftflow-policy: plan_commit=never —" in reason and "git restore --staged" in reason,
              f"rc={rc} out={out!r} err={err!r}")
    for name, cmd, rel in ALLOW_CASES:
        cwd = repo / rel if rel != "." else repo
        rc, out, err = run(BASH_GUARD, bash(cmd, cwd), cwd, project=repo)
        check(f"plan_commit_{name}_allowed", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r} err={err!r}")
    hits = events(repo, "plan_commit_denied")
    check("plan_commit_deny_logs_event", len(hits) >= len(DENY_CASES) and hits[0].get("policy_key") == "plan_commit" and hits[0].get("policy_value") == "never", f"hits={len(hits)}")


def plan_commit_staged_probe_denies():
    repo = make_repo(ignore_plans=False)  # plans are tracked here, so a modification can be staged
    (repo / "docs" / "plans" / "x.md").write_text("changed\n")
    git(repo, "add", "-f", "docs/plans/x.md")
    rc, out, err = run(BASH_GUARD, bash("git commit -m x", repo), repo)
    verdict, reason = decision(out)
    check("plan_commit_staged_probe_denies", rc == 0 and verdict == "deny" and "docs/plans/x.md" in reason, f"rc={rc} out={out!r}")
    git(repo, "reset", "-q")
    rc, out, _ = run(BASH_GUARD, bash("git commit -m x", repo), repo)
    check("plan_commit_staged_probe_clean_index_allows", rc == 0 and out == "", f"out={out!r}")
    # E26: commit -a with a tracked modified plan (the working-tree change is still there after the reset)
    rc, out, _ = run(BASH_GUARD, bash("git commit -a -m x", repo), repo)
    check("plan_commit_e26_commit_all_tracked_modified_denies", rc == 0 and decision(out)[0] == "deny", f"out={out!r}")
    rc, out, _ = run(BASH_GUARD, bash("git commit -m x", repo), repo)
    check("plan_commit_e26_commit_without_all_allows", rc == 0 and out == "", f"out={out!r}")


def test_plan_commit_probe_error():
    repo = make_repo(ignore_plans=False)
    (repo / "docs" / "plans" / "x.md").write_text("changed\n")
    git(repo, "add", "-f", "docs/plans/x.md")
    shim = scratch_dir("p5g-shim-")
    shim_git = shim / "git"
    real = shutil.which("git")
    shim_git.write_text(f'#!/bin/sh\nfor a in "$@"; do if [ "$a" = "diff" ]; then exit 128; fi; done\nexec {real} "$@"\n')
    shim_git.chmod(0o755)
    rc, out, err = run(BASH_GUARD, bash("git commit -m x", repo), repo, path_prefix=str(shim))
    logged = events(repo, "plan_commit_probe_error")
    check("plan_commit_probe_error_allows_and_logs", rc == 0 and out == "" and len(logged) >= 1, f"rc={rc} out={out!r} logged={logged}")
    # E27: git absent from PATH -> probe fails open, an explicit pathspec still denies
    bare = scratch_dir("p5g-nogit-")
    env_path = str(bare)
    rc, out, _ = run(BASH_GUARD, bash("git commit -m x", repo), repo, env_set={"PATH": env_path})
    check("plan_commit_e27_git_missing_probe_fails_open", rc == 0 and out == "", f"rc={rc} out={out!r}")
    rc, out, _ = run(BASH_GUARD, bash("git commit -m x docs/plans/x.md", repo), repo, env_set={"PATH": env_path})
    check("plan_commit_e27_git_missing_explicit_pathspec_denies", rc == 0 and decision(out)[0] == "deny", f"rc={rc} out={out!r}")


def test_plan_commit_scope():
    plain = make_repo(managed=False)
    rc, out, err = run(BASH_GUARD, bash("git add -f docs/plans/x.md", plain), plain)
    check("plan_commit_non_craftflow_repo_allowed", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r}")
    repo = make_repo()
    (repo / "notes").mkdir()
    (repo / "notes" / "p.md").write_text("n\n")
    artifact = {"workflow_uuid": "wf-live", "status": "in_progress", "plan_file": "notes/p.md", "design_file": "notes/d.md",
                "worktree_branch": None}
    (repo / ".craftflow" / "state" / "workflows" / "wf-live.json").write_text(json.dumps(artifact))
    rc, out, _ = run(BASH_GUARD, bash("git add -f notes/p.md", repo), repo)
    check("plan_commit_live_artifact_plan_file_protected", rc == 0 and decision(out)[0] == "deny", f"rc={rc} out={out!r}")
    rc, out, _ = run(BASH_GUARD, bash("git add notes/other.md", repo), repo)
    check("plan_commit_live_artifact_other_file_allowed", rc == 0 and out == "", f"out={out!r}")
    allow = plugin_copy({"plan_commit": "allow"})
    rc, out, err = run(BASH_GUARD, bash("git add -f docs/plans/x.md", repo), repo, plugin=allow)
    check("plan_commit_policy_allow_disables", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r}")
    invalid = plugin_copy('{"plan_commit": "bogus"}')
    rc, out, _ = run(BASH_GUARD, bash("git add -f docs/plans/x.md", repo), repo, plugin=invalid)
    check("plan_commit_policy_invalid_denies", rc == 0 and decision(out)[0] == "deny", f"rc={rc} out={out!r}")


def make_linked_worktree(main):
    wt = main / ".claude" / "worktrees" / "wf-x"
    git(main, "worktree", "add", "-q", str(wt), "-b", "wf-x-branch")
    return wt


def test_plan_commit_linked_worktree():
    main = make_repo()
    wt = make_linked_worktree(main)
    check("linked_worktree_git_is_file", (wt / ".git").is_file() and not (wt / ".craftflow").exists())
    (wt / "docs" / "plans").mkdir(parents=True, exist_ok=True)
    (wt / "docs" / "plans" / "x.md").write_text("p\n")
    rc, out, err = run(BASH_GUARD, bash("git add -f docs/plans/x.md", wt), wt, project=main)
    check("plan_commit_from_linked_worktree_denied", rc == 0 and decision(out)[0] == "deny", f"rc={rc} out={out!r} err={err!r}")
    rc, out, err = run(BASH_GUARD, bash("git add src/a.txt", wt), wt, project=main)
    check("plan_commit_linked_worktree_ordinary_add_allowed", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r}")
    # staged probe runs with -C <worktree>
    git(wt, "add", "-f", "docs/plans/x.md")
    rc, out, _ = run(BASH_GUARD, bash("git commit -m x", wt), wt, project=main)
    check("plan_commit_linked_worktree_staged_probe_denies", rc == 0 and decision(out)[0] == "deny", f"rc={rc} out={out!r}")
    # E33d garbage .git file
    junk = scratch_dir()
    (junk / ".git").write_text("garbage\n")
    (junk / ".craftflow" / "state").mkdir(parents=True)
    rc, out, err = run(BASH_GUARD, bash("git add -f docs/plans/x.md", junk), junk)
    check("plan_commit_git_file_garbage_unmanaged", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r}")
    # relative gitdir resolved (and a missing commondir falls back to gitdir itself)
    rel_main = make_repo()
    rel_wt = scratch_dir()
    wt_gitdir = rel_main / ".git" / "worktrees" / "rel"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "commondir").write_text("../..\n")
    (wt_gitdir / "gitdir").write_text(f"{rel_wt / '.git'}\n")
    (rel_wt / ".git").write_text(f"gitdir: {os.path.relpath(wt_gitdir, rel_wt)}\n")
    rc, out, _ = run(BASH_GUARD, bash("git add -f docs/plans/x.md", rel_wt), rel_wt, project=rel_main)
    check("plan_commit_relative_gitdir_resolved", rc == 0 and decision(out)[0] == "deny", f"rc={rc} out={out!r}")
    nocommon_wt = scratch_dir()
    nc_gitdir = rel_main / ".git" / "worktrees" / "nc"
    nc_gitdir.mkdir(parents=True)
    (nocommon_wt / ".git").write_text(f"gitdir: {nc_gitdir}\n")
    rc, out, err = run(BASH_GUARD, bash("git add -f docs/plans/x.md", nocommon_wt), nocommon_wt, project=nocommon_wt)
    check("plan_commit_missing_commondir_unusual_layout_unmanaged", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r}")


def test_cursor_scope():
    repo = make_repo()
    cursor_env = {"CURSOR_PLUGIN_ROOT": str(PLUGIN_ROOT)}
    for label, cmd in (("e14", "git add docs/plans/x.md"), ("e25", "git add -f docs/plans/x.md; git commit -m x")):
        rc, out, err = run(BASH_GUARD, bash(cmd, repo), repo, env_set=cursor_env)
        check(f"plan_commit_cursor_payload_allowed_{label}", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r} err={err!r}")
    # adapter-delegated: control arm must deny, cursor arm must allow
    p_cursor = {"toolName": "Bash", "toolInput": {"command": "git add -f docs/plans/x.md"}, "cwd": str(repo)}
    control = bash("git add -f docs/plans/x.md", repo)
    control["hook_event_name"] = "PreToolUse"
    rc, out, _ = run(BASH_GUARD, control, repo, env_del=("CURSOR_PLUGIN_ROOT",))
    verdict, reason = decision(out)
    control_ok = rc == 0 and verdict == "deny" and "plan_commit" in reason
    check("cursor_adapter_control_arm_denies", control_ok, f"control did not deny: rc={rc} out={out!r}")
    if not control_ok:
        fail("cursor_adapter_delegated_plan_commit_allowed", "control arm failed, so the cursor arm cannot be trusted")
        return
    env =dict(os.environ, CURSOR_PROJECT_ROOT=str(repo), CLAUDE_PROJECT_DIR=str(repo))
    env.pop("CURSOR_PLUGIN_ROOT", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    proc = subprocess.run([sys.executable, str(ADAPTER), str(BASH_GUARD), "--tool", "Bash", "--event", "PreToolUse"],
                          input=json.dumps(p_cursor), capture_output=True, text=True, cwd=str(repo), env=env, timeout=60)
    check("cursor_adapter_delegated_plan_commit_allowed", proc.returncode == 0 and "deny" not in proc.stdout and "plan_commit" not in proc.stdout + proc.stderr,
          f"rc={proc.returncode} out={proc.stdout!r} err={proc.stderr!r}")


# --------------------------------------------------------------------------------------------------------------------
# worktree merge clean tree (BC-5, E34-E36c)
# --------------------------------------------------------------------------------------------------------------------


def make_merge_repo(branch="wf-x-abc"):
    repo = make_repo()
    art = {"workflow_uuid": "wf-merge", "status": "in_progress", "worktree_branch": branch}
    (repo / ".craftflow" / "state" / "workflows" / "wf-merge.json").write_text(json.dumps(art))
    return repo


def test_merge_guard():
    repo = make_merge_repo()
    (repo / "dirty.txt").write_text("d\n")
    rc, out, err = run(BASH_GUARD, bash("git merge wf-x-abc", repo), repo)
    verdict, reason = decision(out)
    check("worktree_merge_dirty_denied", rc == 0 and verdict == "deny" and "worktree_dirty_main_tree" in reason and "require_clean_tree" in reason,
          f"rc={rc} out={out!r} err={err!r}")
    check("worktree_merge_deny_logs_event", len(events(repo, "worktree_merge_dirty_denied")) == 1)
    rc, out, _ = run(BASH_GUARD, bash("git merge --no-ff -m msg wf-x-abc", repo), repo)
    check("worktree_merge_dirty_denied_with_flags", rc == 0 and decision(out)[0] == "deny", f"out={out!r}")
    rc, out, _ = run(BASH_GUARD, bash("git merge feature/foo", repo), repo)
    check("worktree_merge_non_wf_branch_ignored", rc == 0 and out == "", f"out={out!r}")
    rc, out, _ = run(BASH_GUARD, bash("git merge wf-x-abc", repo), repo, env_set={"CURSOR_PLUGIN_ROOT": str(PLUGIN_ROOT)})
    check("worktree_merge_cursor_payload_allowed", rc == 0 and out == "", f"out={out!r}")
    (repo / "dirty.txt").unlink()
    rc, out, err = run(BASH_GUARD, bash("git merge wf-x-abc", repo), repo)
    check("worktree_merge_clean_allowed", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r} err={err!r}")
    plain = make_repo(managed=False)
    (plain / "dirty.txt").write_text("d\n")
    rc, out, _ = run(BASH_GUARD, bash("git merge wf-x-abc", plain), plain)
    check("worktree_merge_unmanaged_project_allowed", rc == 0 and out == "", f"out={out!r}")
    # status error -> deny (shim makes `git status` exit 128)
    shim = scratch_dir("p5g-shim-")
    real = shutil.which("git")
    (shim / "git").write_text(f'#!/bin/sh\nfor a in "$@"; do if [ "$a" = "status" ]; then exit 128; fi; done\nexec {real} "$@"\n')
    (shim / "git").chmod(0o755)
    rc, out, _ = run(BASH_GUARD, bash("git merge wf-x-abc", repo), repo, path_prefix=str(shim))
    check("worktree_merge_status_error_denied", rc == 0 and decision(out)[0] == "deny", f"out={out!r}")
    # the shipped policy.json has no worktree_merge key in P5: the default (true) applies
    shipped = json.loads(POLICY_FILE.read_text())
    check("worktree_merge_reads_default_when_key_absent", "worktree_merge" not in shipped, f"shipped keys={sorted(shipped)}")
    (repo / "dirty.txt").write_text("d\n")
    rc, out, _ = run(BASH_GUARD, bash("git merge wf-x-abc", repo), repo, plugin=plugin_copy({"native_plan_mode": "never"}))
    check("worktree_merge_default_applies_without_key", rc == 0 and decision(out)[0] == "deny", f"out={out!r}")
    invalid = plugin_copy('{"plan_commit": "bogus"}')
    rc, out, _ = run(BASH_GUARD, bash("git merge wf-x-abc", repo), repo, plugin=invalid)
    check("worktree_merge_policy_invalid_still_checks", rc == 0 and decision(out)[0] == "deny", f"out={out!r}")


def test_invalid_policy_never_widens():
    repo = make_merge_repo()
    for label, body in (("bad_json", "{nope"), ("unknown_key", '{"zzz": 1}'), ("wrong_enum", '{"native_plan_mode": "maybe"}'), ("array", "[]")):
        plug = plugin_copy(body)
        rc, out, _ = run(MAIN_GUARD, {"tool_name": "EnterPlanMode", "tool_input": {}, "cwd": str(repo)}, repo, plugin=plug)
        check(f"policy_invalid_never_widens_enterplanmode_{label}", rc == 0 and decision(out)[0] == "deny", f"out={out!r}")
        rc, out, _ = run(BASH_GUARD, bash("git add -f docs/plans/x.md", repo), repo, plugin=plug)
        check(f"policy_invalid_never_widens_plan_commit_{label}", rc == 0 and decision(out)[0] == "deny", f"out={out!r}")


def test_silence_on_allow_paths():
    repo = make_merge_repo()
    cases = [
        (BASH_GUARD, bash("git add src/x.ts", repo)),
        (BASH_GUARD, bash("git commit -m x", repo)),
        (BASH_GUARD, bash("git merge feature/foo", repo)),
        (BASH_GUARD, bash("ls", repo)),
        (BASH_GUARD, {"tool_name": "Bash", "tool_input": {"command": 5}, "cwd": str(repo)}),
        (BASH_GUARD, {"tool_name": "Bash", "tool_input": None, "cwd": str(repo)}),
    ]
    for idx, (script, payload) in enumerate(cases):
        rc, out, err = run(script, payload, repo)
        check(f"silence_on_allow_paths_{idx}", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r} err={err!r}")
    rc, out, err = run(BASH_GUARD, None, repo, raw="not json")
    check("silence_on_allow_paths_bad_stdin", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r} err={err!r}")


def test_shipped_policy_keys():
    shipped = json.loads(POLICY_FILE.read_text())
    check("shipped_policy_has_native_plan_mode_never", shipped.get("native_plan_mode") == "never", f"{shipped}")
    check("shipped_policy_has_plan_commit_never", shipped.get("plan_commit") == "never", f"{shipped}")
    check("shipped_policy_has_no_other_gate_keys", set(shipped) <= {"memory_write", "native_plan_mode", "plan_commit", "$comment"}, f"{sorted(shipped)}")


# --------------------------------------------------------------------------------------------------------------------
# REM-FIX regressions (review + hunt): C1, H1-H3, M1, M3, M4, L1
# --------------------------------------------------------------------------------------------------------------------


def _allowed(name, cmd, cwd, **kw):
    rc, out, err = run(BASH_GUARD, bash(cmd, cwd), cwd, **kw)
    check(name, rc == 0 and out == "" and err == "", f"rc={rc} out={out!r} err={err!r}")


def _denied(name, cmd, cwd, **kw):
    rc, out, err = run(BASH_GUARD, bash(cmd, cwd), cwd, **kw)
    check(name, rc == 0 and decision(out)[0] == "deny", f"rc={rc} out={out!r} err={err!r}")


def test_c1_glob_collapse_does_not_crash():
    repo = make_repo()
    for label, cmd in (("star_dotdot", "git add '*/..'"), ("xstar_dotdot", "git add 'x*/..'"), ("docs_star_dotdot2", "git add 'docs/*/../..'"),
                       ("bracket_dotdot", "git add '[a]/..'"), ("commit_star_dotdot", "git commit -m x '*/..'")):
        _allowed(f"c1_{label}_no_crash", cmd, repo)
    # the collapse input must not skip the guards that run after it
    rc, out, err = run(BASH_GUARD, bash("git add 'x*/..'; rm -rf .", repo), repo)
    check("c1_later_destructive_guard_still_denies", rc == 0 and decision(out)[0] == "deny" and "in-cwd-critical" in out, f"rc={rc} out={out!r} err={err!r}")
    # force + a collapsed glob is the ancestor '.', which is denied conservatively
    _denied("c1_force_collapsed_glob_is_ancestor", "git add -f '*/..'", repo)
    # main() falls through to the existing guards when _policy_guards itself raises
    import importlib
    import io
    sys.path.insert(0, str(SCRIPTS))
    saved = (os.environ.get("CLAUDE_PROJECT_DIR"), os.environ.get("CLAUDE_PLUGIN_ROOT"), sys.stdin, sys.stdout)
    os.environ["CLAUDE_PROJECT_DIR"], os.environ["CLAUDE_PLUGIN_ROOT"] = str(repo), str(PLUGIN_ROOT)
    try:
        mod = importlib.import_module("craftflow_pretooluse_bash_guard")

        def boom(*_a, **_k):
            raise ValueError("forced")

        mod._policy_guards = boom
        sys.stdin = io.StringIO(json.dumps(bash("rm -rf .", repo)))
        sys.stdout = io.StringIO()
        rc = mod.main()
        out = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = saved[2], saved[3]
        for key, val in (("CLAUDE_PROJECT_DIR", saved[0]), ("CLAUDE_PLUGIN_ROOT", saved[1])):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
    check("c1_main_falls_through_when_policy_guards_raises", rc == 0 and "in-cwd-critical" in out, f"rc={rc} out={out!r}")
    check("c1_policy_guard_failure_is_logged", len(events(repo, "policy_guard_error")) == 1, f"{events(repo, 'policy_guard_error')}")


def test_h1_wrapped_git():
    repo = make_repo()
    cmds = {
        "command": "command git add -f docs/plans/x.md",
        "env": "env FOO=1 git add -f docs/plans/x.md",
        "env_opts": "env -i -u BAR FOO=1 git add -f docs/plans/x.md",
        "time": "time git add -f docs/plans/x.md",
        "nice": "nice -n 5 git add -f docs/plans/x.md",
        "exec": "exec git add -f docs/plans/x.md",
        "sudo": "sudo -u root git add -f docs/plans/x.md",
        "subshell": "(git add -f docs/plans/x.md)",
        "brace": "{ git add -f docs/plans/x.md; }",
        "bash_c": 'bash -c "git add -f docs/plans/x.md"',
        "bash_lc": "bash -lc 'git add -f docs/plans/x.md'",
        "sh_c_cd": "sh -c 'cd src && git add ../docs/plans/x.md'",
        "zsh_c": "zsh -c 'git add -f docs/plans/x.md'",
        "nested": "env bash -c 'sudo git add -f docs/plans/x.md'",
        "eval": 'eval "git add -f docs/plans/x.md"',
        "abs_git": "/usr/bin/git add -f docs/plans/x.md",
    }
    for label, cmd in cmds.items():
        _denied(f"h1_{label}_denied", cmd, repo)
    _allowed("h1_wrapped_ordinary_add_allowed", "env FOO=1 git add src/x.ts", repo)
    _allowed("h1_bash_c_ordinary_allowed", "bash -c 'git add src/x.ts'", repo)
    rc, out, err = run(BASH_GUARD, bash("$G add -f docs/plans/x.md", repo), repo)
    check("h1_dynamic_command_name_fails_open_and_logs", rc == 0 and out == "" and len(events(repo, "plan_commit_dynamic_allowed")) >= 1, f"rc={rc} out={out!r}")
    rc, out, _ = run(BASH_GUARD, bash('bash -c "$CMD"', repo), repo)
    check("h1_dynamic_shell_string_fails_open", rc == 0 and out == "", f"out={out!r}")


def test_h2_retargeted_repo():
    repo = make_repo()
    elsewhere = scratch_dir("p5g-else-")
    gd, wt = f"--git-dir={repo}/.git", f"--work-tree={repo}"
    _denied("h2_git_dir_and_work_tree_from_elsewhere", f"git {gd} {wt} add -f docs/plans/x.md", elsewhere, project=repo)
    _denied("h2_separate_arg_form", f"git --git-dir {repo}/.git --work-tree {repo} add -f docs/plans/x.md", elsewhere, project=repo)
    _denied("h2_env_prefix", f"GIT_DIR={repo}/.git GIT_WORK_TREE={repo} git add -f docs/plans/x.md", elsewhere, project=repo)
    _denied("h2_git_dir_only", f"git {gd} add -f docs/plans/x.md", repo, project=repo)
    _allowed("h2_work_tree_ordinary_add_allowed", f"git {gd} {wt} add src/x.ts", elsewhere, project=repo)
    _allowed("h2_dynamic_work_tree_fails_open", f"git {gd} --work-tree=$W add -f docs/plans/x.md", elsewhere, project=repo)
    # a commit retargeted at the repo is probed there
    repo2 = make_repo(ignore_plans=False)
    (repo2 / "docs" / "plans" / "x.md").write_text("changed\n")
    git(repo2, "add", "-f", "docs/plans/x.md")
    _denied("h2_retargeted_commit_probe_denies", f"git --git-dir={repo2}/.git --work-tree={repo2} commit -m x", elsewhere, project=repo2)


def test_h3_merge_targets():
    repo = make_merge_repo()
    (repo / "dirty.txt").write_text("d\n")
    for label, cmd in (("refs_heads", "git merge refs/heads/wf-x-abc"), ("heads", "git merge heads/wf-x-abc"), ("tilde", "git merge wf-x-abc~1"),
                       ("caret", "git merge wf-x-abc^"), ("reflog", "git merge 'wf-x-abc@{1}'"), ("caret2", "git merge wf-x-abc^2"),
                       ("pull_dot", "git pull . wf-x-abc"), ("pull_dot_refs", "git pull . refs/heads/wf-x-abc"),
                       ("pull_dot_flags", "git pull --no-rebase . wf-x-abc"), ("pull_dot_plus_refspec", "git pull . +wf-x-abc:main"),
                       ("wrapped", "env X=1 git merge wf-x-abc")):
        _denied(f"h3_{label}_denied_when_dirty", cmd, repo)
    _allowed("h3_pull_origin_ignored", "git pull origin wf-x-abc", repo)
    _allowed("h3_remote_tracking_ref_ignored", "git merge origin/wf-x-abc", repo)
    _allowed("h3_other_branch_ignored", "git merge wf-x-abcd", repo)
    (repo / "dirty.txt").unlink()
    _allowed("h3_clean_tree_allowed", "git merge refs/heads/wf-x-abc", repo)


def test_m1_pathspec_magic():
    repo = make_repo()
    (repo / "list.txt").write_text("docs/plans/x.md\n")
    for label, cmd in (("upper_case", "git add -f DOCS/Plans/x.md"), ("icase_magic", "git add ':(icase)docs/PLANS/x.md'"),
                       ("top_magic_from_subdir", "git add ':(top)docs/plans/x.md'"), ("top_glob_magic", "git add ':(top,glob)docs/plans/*.md'"),
                       ("short_top_with_bang_free", "git add ':/docs/plans/x.md'"),
                       ("from_file_force", "git add -f --pathspec-from-file=list.txt"), ("from_file_force_sep", "git add -f --pathspec-from-file list.txt"),
                       ("chmod_value_not_pathspec", "git add --chmod +x docs/plans/x.md")):
        _denied(f"m1_{label}_denied", cmd, repo / "src" if "subdir" in label else repo)
    _allowed("m1_from_file_without_force_allowed", "git add --pathspec-from-file=list.txt", repo)
    _allowed("m1_chmod_equals_ordinary_allowed", "git add --chmod=+x src/x.ts", repo)
    _allowed("m1_chmod_separate_ordinary_allowed", "git add --chmod +x src/x.ts", repo)
    _allowed("m1_exclude_magic_not_a_pathspec", "git add src/x.ts ':(exclude)docs/plans/x.md'", repo)
    _allowed("m1_literal_magic_star_is_not_glob", "git add ':(literal)docs/p*'", repo)


def test_m3_probe_budget():
    import importlib
    import time
    sys.path.insert(0, str(SCRIPTS))
    mod = importlib.import_module("craftflow_pretooluse_bash_guard")
    mod._PROBE_DEADLINE[0] = time.monotonic() - 1
    try:
        check("m3_expired_budget_returns_probe_error", mod._git_lines(str(PLUGIN_ROOT), "status", "--porcelain") is None)
        mod._PROBE_DEADLINE[0] = time.monotonic() + 30
        check("m3_live_budget_runs_probe", mod._git_lines(str(PLUGIN_ROOT), "rev-parse", "--git-dir") is not None)
    finally:
        mod._PROBE_DEADLINE[0] = None
    repo = make_repo(ignore_plans=False)
    (repo / "docs" / "plans" / "x.md").write_text("changed\n")
    git(repo, "add", "-f", "docs/plans/x.md")
    shim = scratch_dir("p5g-slow-")
    real = shutil.which("git")
    (shim / "git").write_text(f'#!/bin/sh\nfor a in "$@"; do if [ "$a" = "diff" ]; then exec sleep 30; fi; done\nexec {real} "$@"\n')
    (shim / "git").chmod(0o755)
    cmd = "git commit -m a; git commit -m b; git commit -m c; git commit -m d; git commit -m e"
    started = time.monotonic()
    rc, out, err = run(BASH_GUARD, bash(cmd, repo), repo, path_prefix=str(shim))
    elapsed = time.monotonic() - started
    errs = events(repo, "plan_commit_probe_error")
    check("m3_chained_commits_stay_inside_hook_timeout", rc == 0 and out == "" and elapsed < 9, f"rc={rc} elapsed={elapsed:.1f} out={out!r}")
    check("m3_budget_exhaustion_is_logged", any(e.get("reason") == "budget" for e in errs), f"{errs}")
    check("m3_allow_path_stays_silent", err == "", f"err={err!r}")


def test_m4_update_index():
    repo = make_repo()
    sha = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    for label, cmd in (("cacheinfo_comma", f"git update-index --add --cacheinfo 100644,{sha},docs/plans/x.md"),
                       ("cacheinfo_three_args", f"git update-index --add --cacheinfo 100644 {sha} docs/plans/x.md"),
                       ("add_path", "git update-index --add docs/plans/x.md"), ("force_remove_free_add", "git update-index --add -- docs/plans/x.md")):
        _denied(f"m4_{label}_denied", cmd, repo)
    _allowed("m4_refresh_allowed", "git update-index --refresh", repo)
    _allowed("m4_add_ordinary_allowed", "git update-index --add src/a.txt", repo)
    _allowed("m4_cacheinfo_ordinary_allowed", f"git update-index --add --cacheinfo 100644,{sha},src/new.txt", repo)


def test_l1_dry_run():
    repo = make_repo(ignore_plans=False)
    (repo / "docs" / "plans" / "x.md").write_text("changed\n")
    git(repo, "add", "-f", "docs/plans/x.md")
    _allowed("l1_commit_dry_run_allowed", "git commit --dry-run -m x", repo)
    _allowed("l1_commit_dry_run_pathspec_allowed", "git commit --dry-run -m x docs/plans/x.md", repo)
    _allowed("l1_add_dry_run_allowed", "git add --dry-run -f docs/plans/x.md", repo)
    _allowed("l1_add_cluster_n_allowed", "git add -nf docs/plans/x.md", repo)
    _denied("l1_real_commit_still_denied", "git commit -m x", repo)


def main():
    try:
        for fn in (test_shipped_policy_keys, enterplanmode_denied_by_default, test_plan_commit_table, plan_commit_staged_probe_denies,
                   test_plan_commit_probe_error, test_plan_commit_scope, test_plan_commit_linked_worktree, test_cursor_scope,
                   test_merge_guard, test_invalid_policy_never_widens, test_silence_on_allow_paths,
                   test_c1_glob_collapse_does_not_crash, test_h1_wrapped_git, test_h2_retargeted_repo, test_h3_merge_targets,
                   test_m1_pathspec_magic, test_m3_probe_budget, test_m4_update_index, test_l1_dry_run):
            print(fn.__name__)
            try:
                fn()
            except Exception as exc:  # a crashed case is a failure, never a silent skip
                fail(fn.__name__, f"{type(exc).__name__}: {exc}")
    finally:
        for path in _scratch:
            shutil.rmtree(path, ignore_errors=True)
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    for line in _errors:
        print(line)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
