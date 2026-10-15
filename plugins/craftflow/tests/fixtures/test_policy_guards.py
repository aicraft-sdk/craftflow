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
        real_stderr, sys.stderr = sys.stderr, io.StringIO()
        rc = mod.main()
        out = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = saved[2], saved[3]
        sys.stderr = real_stderr
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
    # cycle 4 (H-2): a probe that could not run because the budget was spent is "cannot verify" -> fail closed (a plan IS staged here)
    skipped = events(repo, "policy_guard_skipped")
    check("m3_chained_commits_stay_inside_hook_timeout_and_fail_closed", rc == 0 and decision(out)[0] == "deny" and elapsed < 9
          and any(e.get("reason") in ("probe", "time") and e.get("denied") is True for e in skipped), f"rc={rc} elapsed={elapsed:.1f} out={out!r} skipped={skipped}")
    check("m3_budget_exhaustion_is_logged", any(e.get("reason") == "budget" for e in errs) or any(e.get("reason") == "time" and e.get("denied") for e in skipped),
          f"{errs} {skipped}")
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


# --------------------------------------------------------------------------------------------------------------------
# REM-FIX cycle 2 regressions: H-A, H-B, M1-M3 (hunt), review medium 1-3, budget reserve
# --------------------------------------------------------------------------------------------------------------------


def _staged_plan_repo():
    repo = make_repo(ignore_plans=False)
    (repo / "docs" / "plans" / "x.md").write_text("changed\n")
    git(repo, "add", "-f", "docs/plans/x.md")
    return repo


def test_ha_dry_run_is_decided_from_parsed_options():
    repo = _staged_plan_repo()
    for label, cmd in (("message_value", "git commit -m '--dry-run'"), ("no_dry_run_last", "git commit --dry-run --no-dry-run -m x"),
                       ("file_value", "git commit -F --dry-run"), ("author_value", "git commit --author --dry-run -m x"),
                       ("dry_then_no_dry", "git commit --dry-run -m x --no-dry-run"), ("trailer_value", "git commit --trailer --dry-run -m x"),
                       ("cluster_message", "git commit -am --dry-run")):
        _denied(f"ha_{label}_still_denied", cmd, repo)
    _allowed("ha_trailing_dry_run_allowed", "git commit -m x --dry-run", repo)
    _allowed("ha_abbreviated_dry_allowed", "git commit --dry -m x", repo)
    _allowed("ha_no_dry_then_dry_allowed", "git commit --no-dry-run --dry-run -m x", repo)
    plain = make_repo()
    _denied("ha_add_dry_then_no_dry_denied", "git add --dry-run --no-dry-run -f docs/plans/x.md", plain)
    _denied("ha_add_no_dry_run_alone_denied", "git add --no-dry-run -f docs/plans/x.md", plain)
    _allowed("ha_add_dry_run_allowed", "git add -f --dry-run docs/plans/x.md", plain)
    _allowed("ha_add_no_dry_then_dry_allowed", "git add --no-dry-run --dry-run -f docs/plans/x.md", plain)


def test_hb_git_dir_owns_the_index():
    managed = make_repo()
    other = make_repo(managed=False)
    unmanaged_work = scratch_dir("p5g-work-")
    gd = f"{managed}/.git"
    _denied("hb_git_dir_managed_work_tree_other_equals", f"git --git-dir={gd} --work-tree={unmanaged_work} add -f docs/plans/x.md", unmanaged_work, project=managed)
    _denied("hb_git_dir_managed_work_tree_other_space", f"git --git-dir {gd} --work-tree {unmanaged_work} add -f docs/plans/x.md", unmanaged_work, project=managed)
    _denied("hb_env_prefix", f"GIT_DIR={gd} GIT_WORK_TREE={unmanaged_work} git add -f docs/plans/x.md", unmanaged_work, project=managed)
    _denied("hb_env_under_wrapper", f"env GIT_DIR={gd} GIT_WORK_TREE={unmanaged_work} git add -f docs/plans/x.md", unmanaged_work, project=managed)
    _denied("hb_work_tree_managed_git_dir_other", f"git --git-dir={other}/.git --work-tree={managed} add -f docs/plans/x.md", unmanaged_work, project=managed)
    _allowed("hb_both_unmanaged_allowed", f"git --git-dir={other}/.git --work-tree={unmanaged_work} add -f docs/plans/x.md", unmanaged_work, project=managed)
    # cycle 3 (M1): an exported STATIC value is resolved like `env GIT_DIR=`; only dynamic values and bare assignments stay fail-open
    for label, cmd in (("bare_assignment", f"GIT_DIR={gd}; git add -f docs/plans/x.md"), ("export_dynamic", "export GIT_DIR=$X; git add -f docs/plans/x.md"),
                       ("export_no_value", "export GIT_DIR; git add -f docs/plans/x.md")):
        rc, out, err = run(BASH_GUARD, bash(cmd, managed), managed)
        check(f"hb_{label}_is_dynamic_fail_open_logged", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r} err={err!r}")
    check("hb_dynamic_statement_logged", len(events(managed, "plan_commit_dynamic_allowed")) >= 3, f"{events(managed, 'plan_commit_dynamic_allowed')}")


def test_m1_catch_all_logs_truncated_and_warns_on_stderr_only():
    import importlib
    import io
    sys.path.insert(0, str(SCRIPTS))
    repo = make_repo()
    long_cmd = "rm -rf . # " + "y" * 1000
    saved = (os.environ.get("CLAUDE_PROJECT_DIR"), os.environ.get("CLAUDE_PLUGIN_ROOT"), sys.stdin, sys.stdout, sys.stderr)
    os.environ["CLAUDE_PROJECT_DIR"], os.environ["CLAUDE_PLUGIN_ROOT"] = str(repo), str(PLUGIN_ROOT)
    try:
        mod = importlib.import_module("craftflow_pretooluse_bash_guard")
        original = mod._policy_guards

        def boom(*_a, **_k):
            raise ValueError("forced")

        mod._policy_guards = boom
        sys.stdin, sys.stdout, sys.stderr = io.StringIO(json.dumps(bash(long_cmd, repo))), io.StringIO(), io.StringIO()
        rc = mod.main()
        out, err = sys.stdout.getvalue(), sys.stderr.getvalue()
    finally:
        mod._policy_guards = original
        sys.stdin, sys.stdout, sys.stderr = saved[2], saved[3], saved[4]
        for key, val in (("CLAUDE_PROJECT_DIR", saved[0]), ("CLAUDE_PLUGIN_ROOT", saved[1])):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
    logged = events(repo, "policy_guard_error")
    check("m1_exception_path_falls_through_to_destructive_deny", rc == 0 and "in-cwd-critical" in out, f"rc={rc} out={out!r}")
    check("m1_exception_path_writes_one_stderr_line", err.count("\n") == 1 and "policy guard error" in err, f"err={err!r}")
    check("m1_logged_command_is_capped", len(logged) == 1 and len(logged[0]["command"]) <= 300, f"{[len(e.get('command', '')) for e in logged]}")


def test_m2_unwrapper_gives_up_loudly_and_more_wrappers():
    import shlex
    repo = make_repo()
    cmd = "git add -f docs/plans/x.md"
    for _ in range(5):
        cmd = "bash -c " + shlex.quote(cmd)
    rc, out, err = run(BASH_GUARD, bash(cmd, repo), repo)
    reasons = [e.get("reason") for e in events(repo, "plan_commit_dynamic_allowed")]
    check("m2_unwrap_depth_exceeded_is_logged", rc == 0 and out == "" and "unwrap depth exceeded" in reasons, f"rc={rc} out={out!r} reasons={reasons}")
    rc, out, _ = run(BASH_GUARD, bash("env env env env env env env env env git add -f docs/plans/x.md", repo), repo)
    reasons = [e.get("reason") for e in events(repo, "plan_commit_dynamic_allowed")]
    check("m2_wrapper_iteration_cap_is_logged", rc == 0 and out == "" and "wrapper iteration cap" in reasons, f"out={out!r} reasons={reasons}")
    for label, wrapped in (("timeout", "timeout 5 git add -f docs/plans/x.md"), ("timeout_opts", "timeout -s KILL 5 git add -f docs/plans/x.md"),
                           ("stdbuf", "stdbuf -oL git add -f docs/plans/x.md"), ("setsid", "setsid git add -f docs/plans/x.md"),
                           ("env_s", "env -S 'git add -f docs/plans/x.md'"), ("find_exec", "find . -name x -exec git add -f docs/plans/x.md ';'"),
                           ("xargs_explicit", "xargs git add -f docs/plans/x.md"), ("pushd", "pushd src && git add ../docs/plans/x.md"),
                           ("env_chdir_ok", "env -C . git add -f docs/plans/x.md"), ("nohup", "nohup git add -f docs/plans/x.md")):
        _denied(f"m2_{label}_denied", wrapped, repo)
    for label, cmd2, why in (("xargs_stdin", "echo docs/plans/x.md | xargs git add -f", "xargs supplies pathspecs on stdin"),
                             ("pipe_to_shell", "echo 'git add -f docs/plans/x.md' | sh", "pipe to a shell"),
                             ("interpreter", "python3 -c \"import os; os.system('git add -f docs/plans/x.md')\"", "interpreter string"),
                             ("here_string", "bash <<< 'git add -f docs/plans/x.md'", "here-document or here-string"),
                             ("substitution", "echo $(git add -f docs/plans/x.md)", "command substitution")):
        rc, out, err = run(BASH_GUARD, bash(cmd2, repo), repo)
        reasons = [e.get("reason") for e in events(repo, "plan_commit_dynamic_allowed")]
        check(f"m2_{label}_fails_open_and_is_logged", rc == 0 and out == "" and err == "" and why in reasons, f"rc={rc} out={out!r} err={err!r} why={why} reasons={reasons}")


def test_m3_size_cap_and_time_box():
    import importlib
    import time
    sys.path.insert(0, str(SCRIPTS))
    repo = make_repo()
    rc, out, err = run(BASH_GUARD, bash("echo hi # " + "x" * 70000, repo), repo)
    skipped = events(repo, "policy_guard_skipped")
    check("m3_oversized_non_git_command_is_allowed_and_logged", rc == 0 and out == "" and err == "" and len(skipped) == 1
          and skipped[0].get("reason") == "size" and skipped[0].get("denied") is False and len(skipped[0].get("command", "")) <= 300,
          f"rc={rc} out={out[:80]!r} skipped={skipped}")
    mod = importlib.import_module("craftflow_pretooluse_bash_guard")
    mod._PROBE_DEADLINE[0] = time.monotonic() - 1
    mod._PASS_STATE["exhausted"], mod._PASS_STATE["units"] = "", 0
    try:
        found = mod._git_invocations("git add x; git add y", str(repo))
    finally:
        mod._PROBE_DEADLINE[0] = None
    check("m3_past_deadline_stops_the_parse_with_a_skipped_marker", [item["sub"] for item in found] == ["?skipped"] and found[0]["reason"] == "time", f"{found}")


def test_rm1_cd_scope_does_not_leak():
    repo = make_repo()
    (repo / "src" / "docs" / "plans").mkdir(parents=True)
    for label, cmd in (("subshell", "(cd src); git add docs/plans/x.md"), ("subshell_and", "(cd src && true); git add docs/plans/x.md"),
                       ("pipeline", "cd src | cat; git add docs/plans/x.md"), ("background", "cd src & git add docs/plans/x.md"),
                       ("inside_subshell_applies", "(cd src && git add ../docs/plans/x.md)")):
        _denied(f"rm1_{label}_denied", cmd, repo)
    _allowed("rm1_persisting_cd_still_applies", "cd src; git add docs/plans/x.md", repo)
    _allowed("rm1_brace_group_cd_persists", "{ cd src; }; git add docs/plans/x.md", repo)
    _allowed("rm1_nested_subshell_then_cd", "(cd src); cd src; git add docs/plans/x.md", repo)


def test_rm2_force_abbreviations_match_real_git():
    import importlib
    sys.path.insert(0, str(SCRIPTS))
    mod = importlib.import_module("craftflow_pretooluse_bash_guard")
    repo = make_repo(managed=False)
    (repo / ".gitignore").write_text("ig.txt\n")
    (repo / "ig.txt").write_text("i\n")
    (repo / "norm.txt").write_text("n\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "ign", "--no-gpg-sign")
    (repo / "src" / "a.txt").unlink()

    def staged():
        return git(repo, "diff", "--cached", "--name-only").split()

    def accepted(option, target):
        git(repo, "reset", "-q")
        proc = subprocess.run(["git", "-C", str(repo), "add", option] + ([target] if target else []), capture_output=True, text=True)
        names = staged()
        git(repo, "reset", "-q")
        return proc.returncode, names

    for full, target, probe in (("--force", "ig.txt", lambda rc, names: rc == 0 and "ig.txt" in names),
                                ("--dry-run", "norm.txt", lambda rc, names: rc == 0 and "norm.txt" not in names),
                                ("--all", None, lambda rc, names: rc == 0 and "src/a.txt" in names)):
        for cut in range(3, len(full) + 1):
            option = full[:cut]
            git_accepts = probe(*accepted(option, target))
            canon = mod._canon_long(option, mod._ADD_LONG) == full
            check(f"rm2_prefix_{option}_matches_real_git", git_accepts == canon, f"git_accepts={git_accepts} canon={canon}")
    managed = make_repo()
    _denied("rm2_fo_abbreviation_denied", "git add --fo docs/plans/x.md", managed)
    _denied("rm2_forc_abbreviation_denied", "git add --forc docs/plans/x.md", managed)
    _denied("rm2_force_after_no_force_denied", "git add --no-force --force docs/plans/x.md", managed)
    _allowed("rm2_no_force_last_allowed", "git add --force --no-force docs", managed)
    _denied("rm2_force_last_ancestor_denied", "git add --no-force --force docs", managed)


def test_rm3_merge_peels_and_pull_value_options():
    repo = make_merge_repo()
    (repo / "dirty.txt").write_text("d\n")
    for label, cmd in (("peel_commit", "git merge 'wf-x-abc^{commit}'"), ("peel_empty", "git merge 'wf-x-abc^{}'"),
                       ("pull_j", "git pull -j 2 . wf-x-abc"), ("pull_depth", "git pull --depth 3 . wf-x-abc"), ("pull_depth_eq", "git pull --depth=3 . wf-x-abc"),
                       ("pull_o", "git pull -o x . wf-x-abc"), ("pull_abbrev", "git pull --dep 3 . wf-x-abc"), ("pull_jobs", "git pull --jobs 2 . refs/heads/wf-x-abc")):
        _denied(f"rm3_{label}_denied_when_dirty", cmd, repo)
    _allowed("rm3_pull_j_origin_ignored", "git pull -j 2 origin wf-x-abc", repo)


def test_budget_reserve_keeps_the_merge_probe_alive():
    import time
    repo = make_merge_repo()
    shim = scratch_dir("p5g-slow-")
    real = shutil.which("git")
    (shim / "git").write_text(f'#!/bin/sh\nfor a in "$@"; do if [ "$a" = "diff" ]; then exec sleep 30; fi; done\nexec {real} "$@"\n')
    (shim / "git").chmod(0o755)
    started = time.monotonic()
    rc, out, err = run(BASH_GUARD, bash("git commit -m a; git commit -m b; git merge wf-x-abc", repo), repo, path_prefix=str(shim))
    check("budget_reserve_clean_tree_merge_not_denied_by_burnt_budget", rc == 0 and out == "" and err == "" and time.monotonic() - started < 9, f"rc={rc} out={out!r} err={err!r}")


# --------------------------------------------------------------------------------------------------------------------
# REM-FIX cycle 3 (final): N-H1, N-H2, M2, M1, M3, group heads, quoted operators
# --------------------------------------------------------------------------------------------------------------------


def _timed(cmd, cwd, **kw):
    import time
    started = time.monotonic()
    rc, out, err = run(BASH_GUARD, bash(cmd, cwd), cwd, **kw)
    return rc, out, err, time.monotonic() - started


def test_nh1_long_merge_tokens_are_bounded():
    import importlib
    import time
    sys.path.insert(0, str(SCRIPTS))
    mod = importlib.import_module("craftflow_pretooluse_bash_guard")
    for label, unit in (("caret", "^"), ("tilde1", "~1"), ("caret2", "^2"), ("peel", "^{}")):
        started = time.monotonic()
        result = mod._merge_ref_name("wf-x-abc" + unit * (30000 // len(unit)))
        check(f"nh1_merge_ref_name_{label}_linear", result == "" and time.monotonic() - started < 0.2, f"{result[:20]!r} {time.monotonic() - started:.2f}s")
    check("nh1_short_suffix_chain_still_resolves", mod._merge_ref_name("refs/heads/wf-x-abc^2~1^{commit}^") == "wf-x-abc", mod._merge_ref_name("refs/heads/wf-x-abc^2~1^{commit}^"))
    repo = make_merge_repo()
    for label, unit in (("caret", "^"), ("tilde1", "~1"), ("caret2", "^2"), ("peel", "^{}")):
        # 12k keeps the policy pass active (under the 16 KB cap); the destructive guard behind it must still deny
        rc, out, err, secs = _timed("git merge wf-x-abc" + unit * (12000 // len(unit)) + "; rm -rf .", repo)
        check(f"nh1_{label}_12k_token_fast_and_destructive_guard_still_denies", rc == 0 and secs < 1.5 and "in-cwd-critical" in out, f"rc={rc} secs={secs:.2f} out={out[:200]!r}")
        # 30k is over the size cap: fail closed (the text names a workflow branch merge), still fast
        rc, out, err, secs = _timed("git merge wf-x-abc" + unit * (30000 // len(unit)) + "; rm -rf .", repo)
        check(f"nh1_{label}_30k_token_fast_and_denied", rc == 0 and secs < 1.5 and decision(out)[0] == "deny", f"rc={rc} secs={secs:.2f} out={out[:200]!r}")


def test_nh2_big_commands_fail_closed_when_they_name_git_writes():
    repo = _staged_plan_repo()
    for kb in (45, 60):
        cmd = "cd a;" * (kb * 1024 // 5) + " git commit -m x"
        rc, out, err, secs = _timed(cmd, repo)
        verdict, reason = decision(out)
        check(f"nh2_{kb}kb_cd_then_commit_denied_fast", rc == 0 and verdict == "deny" and reason.startswith("craftflow-policy: plan_commit —")
              and "split" in reason and secs < 3, f"rc={rc} secs={secs:.2f} out={out[:200]!r}")
        skipped = events(repo, "policy_guard_skipped")
        check(f"nh2_{kb}kb_logged_with_denied_true", bool(skipped) and skipped[-1].get("denied") is True and skipped[-1].get("reason") == "size", f"{skipped[-1:]}")
    rc, out, err, secs = _timed("cd a;" * (45 * 1024 // 5) + " echo hi", repo)
    check("nh2_45kb_cd_then_echo_allowed", rc == 0 and out == "" and err == "" and secs < 3, f"rc={rc} secs={secs:.2f} out={out[:120]!r}")
    # under the size cap but over the unit cap: exhausted -> fail closed
    rc, out, err, secs = _timed("cd a;" * 2500 + " git commit -m x", repo)
    check("nh2_unit_cap_exhaustion_denied_fast", rc == 0 and decision(out)[0] == "deny" and secs < 3, f"rc={rc} secs={secs:.2f} out={out[:160]!r}")
    rc, out, err, secs = _timed("(" * 3000 + " git commit -m x", repo)
    check("nh2_3000_deep_group_bounded_and_denied", rc == 0 and decision(out)[0] == "deny" and secs < 3, f"rc={rc} secs={secs:.2f} out={out[:160]!r}")
    big_add = "cd a;" * (45 * 1024 // 5) + " git add src/x.ts"
    rc, out, err, secs = _timed(big_add, repo)
    check("nh2_big_command_with_any_git_add_is_denied_by_design", rc == 0 and decision(out)[0] == "deny", f"out={out[:120]!r}")
    allow = plugin_copy({"plan_commit": "allow"})
    rc, out, err, secs = _timed("cd a;" * (45 * 1024 // 5) + " git commit -m x", repo, plugin=allow)
    check("nh2_policy_allow_disables_the_fail_closed_deny", rc == 0 and out == "" and err == "", f"out={out[:120]!r} err={err!r}")
    rc, out, err, secs = _timed("cd a;" * (45 * 1024 // 5) + " git commit -m x", repo, env_set={"CURSOR_PLUGIN_ROOT": str(PLUGIN_ROOT)})
    check("nh2_cursor_payload_allowed", rc == 0 and out == "" and err == "", f"out={out[:120]!r}")
    plain = make_repo(managed=False)
    rc, out, err, secs = _timed("cd a;" * (45 * 1024 // 5) + " git commit -m x", plain)
    check("nh2_unmanaged_project_allowed", rc == 0 and out == "" and err == "", f"out={out[:120]!r}")
    merge_repo = make_merge_repo()
    rc, out, err, secs = _timed("cd a;" * (45 * 1024 // 5) + " git merge feature/foo", merge_repo)
    check("nh2_merge_without_wf_branch_text_allowed", rc == 0 and out == "", f"out={out[:120]!r}")
    rc, out, err, secs = _timed("cd a;" * (45 * 1024 // 5) + " git merge wf-x-abc", merge_repo)
    check("nh2_merge_with_wf_branch_text_denied", rc == 0 and decision(out)[0] == "deny", f"out={out[:120]!r}")


def test_m2_clean_merge_is_not_denied_by_a_burnt_parse_budget():
    import contextlib
    import importlib
    import io
    import time
    sys.path.insert(0, str(SCRIPTS))
    mod = importlib.import_module("craftflow_pretooluse_bash_guard")
    repo = make_merge_repo()
    inv = {"sub": "merge", "later": ["wf-x-abc"], "eff": str(repo), "dyn": False, "git_dir": None, "work_tree": None, "reason": ""}
    resolved = mod.resolve_main_root(repo)
    mod._RUN_CACHE.clear()
    saved_env = (os.environ.get("CLAUDE_PROJECT_DIR"), os.environ.get("CLAUDE_PLUGIN_ROOT"))
    os.environ["CLAUDE_PROJECT_DIR"], os.environ["CLAUDE_PLUGIN_ROOT"] = str(repo), str(PLUGIN_ROOT)
    try:
        mod._PROBE_DEADLINE[0] = time.monotonic() - 5
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            clean = mod._merge_one(inv, resolved, "git merge wf-x-abc")
        check("m2_clean_tree_with_spent_parse_budget_is_not_denied", clean is False and buf.getvalue() == "", f"clean={clean} out={buf.getvalue()!r}")
        check("m2_merge_probe_restores_the_parse_deadline", mod._PROBE_DEADLINE[0] is not None and mod._PROBE_DEADLINE[0] < time.monotonic())
        (repo / "dirty.txt").write_text("d\n")
        mod._RUN_CACHE.clear()  # the merge result is cached per hook run; this is a new run
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dirty = mod._merge_one(inv, resolved, "git merge wf-x-abc")
        check("m2_dirty_tree_still_denied_with_spent_parse_budget", dirty is True and decision(buf.getvalue())[0] == "deny", f"{buf.getvalue()!r}")
    finally:
        mod._PROBE_DEADLINE[0] = None
        for key, val in zip(("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT"), saved_env):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


def test_m1_static_exports_resolve_like_env_prefix():
    managed = make_repo()
    elsewhere = scratch_dir("p5g-else-")
    gd = f"{managed}/.git"
    _denied("m1_export_git_dir_and_work_tree", f"export GIT_DIR={gd} GIT_WORK_TREE={elsewhere}; git add -f docs/plans/x.md", elsewhere, project=managed)
    _denied("m1_export_two_statements", f"export GIT_DIR={gd}; export GIT_WORK_TREE={elsewhere}; git add -f docs/plans/x.md", elsewhere, project=managed)
    _denied("m1_declare_x", f"declare -x GIT_DIR={gd}; git add -f docs/plans/x.md", elsewhere, project=managed)
    _allowed("m1_unset_clears_the_export", f"export GIT_DIR={gd}; unset GIT_DIR; git add -f docs/plans/x.md", elsewhere, project=managed)
    _allowed("m1_export_in_pipeline_does_not_persist", f"export GIT_DIR={gd} | cat; git add -f docs/plans/x.md", elsewhere, project=managed)
    _allowed("m1_export_in_subshell_does_not_persist", f"(export GIT_DIR={gd}); git add -f docs/plans/x.md", elsewhere, project=managed)
    for label, cmd in (("cd_dash", "cd -; git add -f docs/plans/x.md"), ("popd", "popd; git add -f docs/plans/x.md"),
                       ("cd_pwd", 'cd "$PWD"; git add -f docs/plans/x.md')):
        rc, out, err = run(BASH_GUARD, bash(cmd, managed), managed)
        check(f"m1_{label}_stays_dynamic_fail_open", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r}")
    check("m1_dynamic_cd_logged", len(events(managed, "plan_commit_dynamic_allowed")) >= 3, f"{events(managed, 'plan_commit_dynamic_allowed')}")


def test_m3_status_formats_imply_dry_run():
    repo = make_repo(ignore_plans=False)
    (repo / "docs" / "plans" / "x.md").write_text("changed\n")
    git(repo, "add", "-f", "docs/plans/x.md")
    head = git(repo, "rev-parse", "HEAD").strip()
    for opt in ("--short", "--porcelain", "--long", "-z", "--null", "--short --no-dry-run"):
        subprocess.run(["git", "-C", str(repo), "commit", *opt.split(), "-m", "x", "--no-gpg-sign"], capture_output=True, text=True)
        same = git(repo, "rev-parse", "HEAD").strip() == head
        check(f"m3_real_git_{opt.replace(' ', '_')}_does_not_commit", same, f"HEAD moved for {opt}")
        _allowed(f"m3_guard_allows_{opt.replace(' ', '_')}", f"git commit {opt} -m x", repo)
    _denied("m3_plain_commit_still_denied", "git commit -m x", repo)
    _denied("m3_no_short_still_denied", "git commit --no-short -m x", repo)


def test_group_heads_and_quoted_operators():
    repo = make_repo()
    staged = _staged_plan_repo()
    (repo / "src" / "docs" / "plans").mkdir(parents=True)
    for label, cmd in (("if", "if git add -f docs/plans/x.md; then echo y; fi"), ("while", "while git add -f docs/plans/x.md; do break; done"),
                       ("until", "until git add -f docs/plans/x.md; do :; done"), ("if_bang", "if ! git add -f docs/plans/x.md; then :; fi"),
                       ("elif", "if true; then :; elif git add -f docs/plans/x.md; then :; fi"),
                       ("if_cd_persists", "if cd src; then git add ../docs/plans/x.md; fi")):
        _denied(f"grp_{label}_denied", cmd, repo)
    _allowed("grp_if_cd_persists_allows_local_path", "if cd src; then git add docs/plans/x.md; fi", repo)
    _denied("grp_if_commit_bang_denied", "if ! git commit -m x; then :; fi", staged)
    for label, cmd in (("semicolon", 'git add -f ";" docs/plans/x.md'), ("andand", 'git commit -m "&&" docs/plans/x.md'),
                       ("commit_semicolon", 'git commit -m ";" docs/plans/x.md'), ("paren_message", "git commit -m ')' docs/plans/x.md"),
                       ("open_paren", "git commit -m '(' docs/plans/x.md"), ("pipe_in_quotes", "git add -f 'a|b' docs/plans/x.md"),
                       ("subshell_with_quoted_paren", "(git add -f ')' docs/plans/x.md)"), ("redirect_chars", "git add -f '>' docs/plans/x.md"),
                       ("escaped_semicolon", "git add -f \\; docs/plans/x.md")):
        _denied(f"quoted_{label}_keeps_the_pathspec", cmd, repo)
    _denied("quoted_paren_not_eaten_by_group_strip", "(git commit -m ')')", staged)
    _allowed("quoted_operator_text_is_not_a_command", "echo 'git add -f docs/plans/x.md; rm -rf .' > /dev/null", repo)


# --------------------------------------------------------------------------------------------------------------------
# REM-FIX cycle 4 (narrow, last): per-run work bounds, probe exhaustion, command-word keyed fail-closed, comments, heredocs
# --------------------------------------------------------------------------------------------------------------------


def _many_workflows(repo, count=441):
    wf_dir = repo / ".craftflow" / "state" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(1, count + 1):
        (wf_dir / f"wf-gen-{idx}.json").write_text(json.dumps({"workflow_uuid": f"wf-gen-{idx}", "status": "in_progress", "worktree_branch": f"wf-br-{idx}"}))


def test_c4_per_run_work_is_bounded():
    repo = make_repo()
    _many_workflows(repo)
    victim = scratch_dir("p5g-victim-")
    for count in (200, 300):
        rc, out, err, secs = _timed("git merge wf-br-1;" * count + f" rm -rf {victim}", repo)
        check(f"c4_{count}_identical_merges_fast_and_destructive_deny_delivered", rc == 0 and secs < 4 and decision(out)[0] == "deny"
              and ("worktree-confinement" in out or "escapes-cwd" in out), f"rc={rc} secs={secs:.2f} out={out[:200]!r}")
    rc, out, err, secs = _timed("git merge wf-br-1;" * 900 + f" rm -rf {victim}", repo)
    check("c4_900_identical_merges_fast_and_denied", rc == 0 and secs < 4 and decision(out)[0] == "deny", f"rc={rc} secs={secs:.2f} out={out[:160]!r}")
    rc, out, err, secs = _timed("git add src;" * 900, repo)
    check("c4_900_identical_adds_fast_and_allowed", rc == 0 and out == "" and err == "" and secs < 4, f"rc={rc} secs={secs:.2f} out={out[:120]!r}")
    # distinct invocations are capped (50): exhausted -> fail closed under the command-word rule
    rc, out, err, secs = _timed("".join(f"git add f{idx};" for idx in range(300)) + " git commit -m x", repo)
    reasons = [e.get("reason") for e in events(repo, "policy_guard_skipped")]
    check("c4_distinct_invocation_cap_fails_closed", rc == 0 and decision(out)[0] == "deny" and secs < 4 and "invocations" in reasons, f"secs={secs:.2f} reasons={reasons} out={out[:120]!r}")
    # a merge status probe is shared by the run: a spent merge budget denies like a git error
    import importlib
    sys.path.insert(0, str(SCRIPTS))
    mod = importlib.import_module("craftflow_pretooluse_bash_guard")
    mod._PASS_STATE["probes"] = 0
    results = [mod._git_lines(str(repo), "rev-parse", "--git-dir") for _ in range(mod._MAX_PROBES + 1)]
    check("c4_probe_count_is_capped_and_reports_budget", results[-1] is None and mod._PROBE_STATE["reason"] == "budget" and all(r is not None for r in results[:-1]), f"{results[-2:]}")
    check("c4_uncounted_probe_bypasses_the_cap", mod._git_lines(str(repo), "rev-parse", "--git-dir", counted=False) is not None)
    mod._PASS_STATE["probes"] = 0


def test_c4_probe_exhaustion_fails_closed():
    staged = _staged_plan_repo()
    _many_workflows(staged)
    rc, out, err, secs = _timed("git add src;" * 600 + " git commit -m x", staged)
    check("c4_600_adds_then_staged_commit_denied_fast", rc == 0 and decision(out)[0] == "deny" and secs < 4, f"rc={rc} secs={secs:.2f} out={out[:160]!r}")
    other = make_repo()
    rel = os.path.relpath(other, staged)
    rc, out, err, secs = _timed(f"git -C {rel} commit -m y;" * 400 + " git commit -m x", staged)
    check("c4_400_other_repo_commits_then_staged_commit_denied_fast", rc == 0 and decision(out)[0] == "deny" and secs < 4, f"rc={rc} secs={secs:.2f} out={out[:160]!r}")
    # in-process: a commit probe that cannot run because the probe COUNT budget is spent is "cannot verify" -> deny, reason probe
    import contextlib
    import importlib
    import io
    sys.path.insert(0, str(SCRIPTS))
    mod = importlib.import_module("craftflow_pretooluse_bash_guard")
    clean = make_repo()
    inv = {"sub": "commit", "later": ["-m", "x"], "eff": str(clean), "dyn": False, "git_dir": None, "work_tree": None, "reason": ""}
    saved_env = (os.environ.get("CLAUDE_PROJECT_DIR"), os.environ.get("CLAUDE_PLUGIN_ROOT"))
    os.environ["CLAUDE_PROJECT_DIR"], os.environ["CLAUDE_PLUGIN_ROOT"] = str(clean), str(PLUGIN_ROOT)
    try:
        mod._RUN_CACHE.clear()
        mod._PASS_STATE["probes"] = mod._MAX_PROBES
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            denied = mod._plan_commit_one(inv, mod.resolve_main_root(clean), "git commit -m x", {})
        skipped = events(clean, "policy_guard_skipped")
        check("c4_spent_probe_count_fails_closed_with_reason_probe", denied is True and decision(buf.getvalue())[0] == "deny"
              and any(e.get("reason") == "probe" and e.get("denied") is True for e in skipped), f"denied={denied} skipped={skipped}")
    finally:
        mod._PASS_STATE["probes"] = 0
        mod._RUN_CACHE.clear()
        for key, val in zip(("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT"), saved_env):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
    # an ordinary probe error (E27) keeps failing open: only a spent TIME budget fails closed
    repo = make_repo(ignore_plans=False)
    (repo / "docs" / "plans" / "x.md").write_text("changed\n")
    git(repo, "add", "-f", "docs/plans/x.md")
    shim = scratch_dir("p5g-shim-")
    real = shutil.which("git")
    (shim / "git").write_text(f'#!/bin/sh\nfor a in "$@"; do if [ "$a" = "diff" ]; then exit 128; fi; done\nexec {real} "$@"\n')
    (shim / "git").chmod(0o755)
    rc, out, err = run(BASH_GUARD, bash("git commit -m x", repo), repo, path_prefix=str(shim))
    check("c4_ordinary_probe_error_still_fails_open", rc == 0 and out == "" and err == "", f"rc={rc} out={out!r} err={err!r}")


def test_c4_fail_closed_is_keyed_on_command_words():
    pad = "echo pad; " * 2000
    repo = make_repo()
    body = "line of a commit message\n" * 900
    for label, cmd in (
            # cycle 5: the size path reads RAW text (no lexer), so a >16 KB heredoc whose body puts `git commit` at a line start is a
            # documented false deny; a body that only mentions it in prose stays allowed
            ("heredoc_doc_writer", "cat > f <<'EOF'\n" + "To save your work, run git commit later and git add after.\n" * 400 + "EOF\n"),
            ("python_heredoc", "python3 - <<EOF\nprint('git commit')\n" + "x = 1\n" * 3000 + "EOF\n"),
            ("diff_then_echo", "git diff; echo '<commit add update-index>'; " + pad),
            ("prose_in_quotes", "echo \"run git commit later\" # " + "x" * 17000),
            ("git_diff_only", "git diff " + "x" * 17000),
            ("comment_mentions", "ls # git commit add " + "x" * 17000)):
        rc, out, err, secs = _timed(cmd, repo)
        check(f"c4_big_{label}_allowed", rc == 0 and out == "" and err == "" and len(cmd) > 16384 and secs < 3, f"len={len(cmd)} rc={rc} secs={secs:.2f} out={out[:160]!r}")
    # a real big commit with a clean index is verified by the staged probe and allowed; with a plan staged it is denied
    big_commit = "git commit -F - <<'EOF'\n" + "message line\n" * 1500 + "EOF\n"
    rc, out, err, secs = _timed(big_commit, repo)
    check("c4_big_commit_clean_index_allowed", rc == 0 and out == "" and len(big_commit) > 16384, f"len={len(big_commit)} out={out[:160]!r}")
    staged = _staged_plan_repo()
    rc, out, err, secs = _timed(big_commit, staged)
    check("c4_big_commit_with_staged_plan_denied", rc == 0 and decision(out)[0] == "deny" and decision(out)[1].startswith("craftflow-policy: plan_commit —"), f"out={out[:200]!r}")
    # add/stage/update-index cannot be verified by the staged probe: still denied when big
    rc, out, err, secs = _timed("git add src/x.ts # " + "x" * 17000, repo)
    check("c4_big_add_still_denied", rc == 0 and decision(out)[0] == "deny", f"out={out[:160]!r}")
    for lead in ("env FOO=1 git add src/x.ts", "(git add src/x.ts)", "echo hi && git add src/x.ts", "sudo git add src/x.ts", "if true; then git add src/x.ts; fi"):
        rc, out, err, secs = _timed(lead + " # " + "x" * 17000, repo)
        check(f"c4_big_command_position_{lead.split()[0].strip('(')}_denied", rc == 0 and decision(out)[0] == "deny", f"lead={lead} out={out[:120]!r}")


def test_c4_comments_are_comments():
    repo = make_repo()
    _denied("c4_apostrophe_in_comment_does_not_hide_the_next_line", "# don't do this\ngit add -f docs/plans/x.md", repo)
    _denied("c4_comment_swallowing_next_line_does_not_hide_it", "echo hi # ok\ngit add -f docs/plans/x.md", repo)
    _denied("c4_trailing_comment_with_quote", "git add -f docs/plans/x.md # it's fine", repo)
    _allowed("c4_hash_inside_double_quotes_is_not_a_comment", 'echo "a # b"', repo)
    _allowed("c4_hash_inside_double_quotes_keeps_the_rest", 'echo "a # b"; git add src/x.ts', repo)
    _denied("c4_hash_in_quoted_pathspec_survives", "git add -f 'a#b' docs/plans/x.md", repo)
    staged = _staged_plan_repo()
    _denied("c4_commit_message_with_hash_still_probed", 'git commit -m "x#y"', staged)
    _allowed("c4_commit_message_with_hash_clean_index", 'git commit -m "x#y"', repo)
    _denied("c4_mid_word_hash_is_not_a_comment", "git add -f docs/plans/x.md#frag docs/plans/x.md", repo)


def test_c4_unparseable_and_heredocs():
    repo = make_repo()
    staged = _staged_plan_repo()
    rc, out, err = run(BASH_GUARD, bash('git commit -m "unterminated', staged), staged)
    reasons = [e.get("reason") for e in events(staged, "policy_guard_skipped")]
    check("c4_unbalanced_quote_naming_a_git_write_fails_closed", rc == 0 and decision(out)[0] == "deny" and "unparseable" in reasons, f"out={out[:160]!r} reasons={reasons}")
    check("c4_unparseable_logged_as_dynamic", len(events(staged, "plan_commit_dynamic_allowed")) >= 1)
    _allowed("c4_unbalanced_quote_without_git_write_allowed", 'echo "abc', repo)
    _allowed("c4_heredoc_apostrophe_body_then_clean_commit_is_parsed", "cat > f <<EOF\nit's here\nEOF\ngit commit -m x", repo)
    _denied("c4_heredoc_apostrophe_body_then_staged_commit_denied", "cat > f <<EOF\nit's here\nEOF\ngit commit -m x", staged)
    _denied("c4_heredoc_body_does_not_hide_the_next_command", "cat <<'EOF' > f\ngit commit -m body\nEOF\ngit add -f docs/plans/x.md", repo)
    _denied("c4_line_continuation_joins_words", "git add -f \\\ndocs/plans/x.md", repo)
    _allowed("c4_heredoc_body_is_not_a_command", "cat <<EOF > f\ngit add -f docs/plans/x.md\nEOF", repo)
    _denied("c4_unterminated_heredoc_does_not_swallow_the_rest", "echo $((1<<2))\ngit add -f docs/plans/x.md", repo)


def test_c4_export_n_and_private_use_characters():
    managed = make_repo()
    elsewhere = scratch_dir("p5g-else-")
    gd = f"{managed}/.git"
    _allowed("c4_export_n_unexports", f"export GIT_DIR={gd}; export -n GIT_DIR; git add -f docs/plans/x.md", elsewhere, project=managed)
    _denied("c4_plain_export_still_applies", f"export GIT_DIR={gd}; git add -f docs/plans/x.md", elsewhere, project=managed)
    rc, out, err = run(BASH_GUARD, bash("echo '' ; git add -f docs/plans/x.md", managed), managed)
    check("c4_private_use_chars_in_command_fail_closed_for_git_writes", rc == 0 and decision(out)[0] == "deny", f"out={out[:160]!r}")
    _allowed("c4_private_use_chars_without_git_write_allowed", "echo '' ; ls", managed)


# --------------------------------------------------------------------------------------------------------------------
# REM-FIX cycle 5 (regressions only): linear heredoc scan, raw-text fail-closed, worktree commits on the size path,
# live heredoc bodies, fake heredocs in arithmetic, unparseable quoting, redirect memoisation
# --------------------------------------------------------------------------------------------------------------------


def test_c5_heredoc_bombs_are_linear_and_still_reach_the_destructive_guard():
    repo = make_repo()
    _many_workflows(repo)
    victim = scratch_dir("p5g-victim-")
    bombs = {
        "40kb": "git commit -m x; cat " + "<<E " * 5000 + "\n" + "a\n" * 10000,
        "64kb": "git commit -m x; cat " + "<<E " * 8000 + "\n" + "a\n" * 16000,
        "1mb": "git commit -m x; " + "echo pad; " * 100000,
        "40kb_trailing_quote": "git commit -m x; cat " + "<<E " * 5000 + "\n" + "a\n" * 10000 + "'",
    }
    for label, bomb in bombs.items():
        # the destructive command comes FIRST: after the bomb it would be heredoc body text, which no guard may treat as executed
        rc, out, err, secs = _timed(f"rm -rf {victim}; " + bomb, repo)
        if label.endswith("trailing_quote"):  # an unterminated quote is a shell syntax error: nothing runs, so only the time matters
            check(f"c5_{label}_bomb_fast", rc == 0 and secs < 3, f"rc={rc} secs={secs:.2f} out={out[:120]!r}")
            continue
        if label == "1mb":  # over 256 KB: raw-text deny only, no probes (documented)
            check(f"c5_{label}_bomb_fast_and_denied", rc == 0 and secs < 3 and decision(out)[0] == "deny", f"rc={rc} secs={secs:.2f} out={out[:200]!r}")
            continue
        check(f"c5_{label}_bomb_fast_and_destructive_guard_still_denies", rc == 0 and secs < 3 and decision(out)[0] == "deny"
              and ("worktree-confinement" in out or "escapes-cwd" in out), f"rc={rc} secs={secs:.2f} out={out[:200]!r}")
    # under the size cap: more than 16 pending heredocs is unparseable -> fail closed on raw text, fast
    rc, out, err, secs = _timed("git commit -m x; cat " + "<<E " * 2500 + "\n" + "a\n" * 2000, repo)
    check("c5_small_bomb_over_the_pending_cap_fails_closed_fast", rc == 0 and secs < 3 and decision(out)[0] == "deny", f"rc={rc} secs={secs:.2f} out={out[:160]!r}")
    rc, out, err, secs = _timed("echo hi; cat " + "<<E " * 2500 + "\n" + "a\n" * 2000, repo)
    check("c5_small_bomb_without_a_git_write_is_allowed", rc == 0 and out == "" and secs < 3, f"rc={rc} secs={secs:.2f} out={out[:160]!r}")


def test_c5_size_path_checks_the_worktree_for_commits():
    pad = " # " + "x" * 17000
    plans_tracked = make_repo(ignore_plans=False)
    (plans_tracked / "docs" / "plans" / "x.md").write_text("changed\n")  # tracked, modified, NOT staged
    clean = make_repo(ignore_plans=False)
    for label, cmd in (("am", "git commit -am x"), ("all", "git commit --all -m x"), ("only", "git commit --only docs/plans/x.md -m x"),
                       ("include", "git commit -i docs/plans/x.md -m x")):
        _denied(f"c5_size_{label}_with_modified_tracked_plan_denied", cmd + pad, plans_tracked)
        _allowed(f"c5_size_{label}_clean_repo_allowed", cmd + pad, clean)


def test_c5_live_heredoc_bodies_are_inspected():
    staged = _staged_plan_repo()
    clean = make_repo()
    for label, cmd in (("bash", "bash <<E\ngit commit -m x\nE"), ("pipe_sh", "cat <<E | sh\ngit commit -m x\nE"),
                       ("sh_quoted", "sh <<'E'\ngit add docs/plans/secret.md\nE"), ("eval_subst", "eval $(cat <<E\ngit commit -m x\nE\n)"),
                       ("unquoted_subst", "cat <<E\n$(git commit -m x)\nE"), ("unquoted_backtick", "cat <<E\n`git commit -m x`\nE"),
                       ("xargs", "xargs -0 sh <<E\ngit commit -m x\nE"), ("python", "python3 <<E\nimport os\nE\ngit commit -m x")):
        _denied(f"c5_live_{label}_denied", cmd, staged)
    _allowed("c5_live_bash_clean_index_is_verified_and_allowed", "bash <<E\ngit commit -m x\nE", clean)
    _allowed("c5_inert_quoted_heredoc_stays_blanked", "cat <<'E'\n$(git commit -m x)\nE", staged)
    _allowed("c5_inert_plain_heredoc_stays_blanked", "tee f <<E\njust text\nE", staged)
    _allowed("c5_inert_commit_F_dash_is_a_commit_not_a_body", "cat > f <<'E'\ngit commit -m x\nE", staged)


def test_c5_fake_heredocs_in_arithmetic():
    staged = _staged_plan_repo()
    managed = make_repo()
    for label, cmd, repo in (("arith_x", "echo $((1<<X))\ngit add -f docs/plans/x.md\nX", managed), ("arith_b", "echo $((1<<b))\ngit add -f docs/plans/x.md\nb", managed),
                             ("let", "let n=1<<E\ngit commit -m x\nE", staged), ("double_paren", "((n=1<<E))\ngit commit -m x\nE", staged),
                             ("test_brackets", "[[ a<<E ]]\ngit commit -m x\nE", staged)):
        _denied(f"c5_fake_heredoc_{label}_denied", cmd, repo)


def test_c5_unparseable_uses_raw_text():
    staged = _staged_plan_repo()
    for label, cmd in (("subst_quote", "echo \"$(echo '\"')\" ; git commit -m x"), ("backtick_quote", "echo \"`echo '\"'`\" ; git commit -m x"),
                       ("plain_unbalanced", "git commit -m \"unterminated")):
        rc, out, err = run(BASH_GUARD, bash(cmd, staged), staged)
        verdict, reason = decision(out)
        check(f"c5_{label}_denied_with_the_quoting_message", rc == 0 and verdict == "deny" and "unverifiable quoting" in reason, f"out={out[:200]!r}")
    clean = make_repo()
    _allowed("c5_unbalanced_without_git_write_allowed", 'echo "abc', clean)


def test_c5_comment_after_closing_paren():
    repo = make_repo()
    _denied("c5_hash_after_command_substitution_is_not_a_comment", "echo $(true)#x; git add -f docs/plans/x.md", repo)
    _denied("c5_hash_after_subshell_paren_is_not_a_comment", "(true)#x; git add -f docs/plans/x.md", repo)
    _denied("c5_hash_after_space_is_a_comment_but_next_line_is_inspected", "(true) # note\ngit add -f docs/plans/x.md", repo)


def test_c5_redirects_are_memoised():
    repo = make_repo()
    _many_workflows(repo)
    rc, out, err, secs = _timed("echo x > a;" * 800, repo)
    check("c5_800_redirects_with_441_workflows_fast", rc == 0 and secs < 4, f"rc={rc} secs={secs:.2f} out={out[:120]!r} err={err[:120]!r}")
    check("c5_redirects_output_is_silent_or_a_deny", out == "" or decision(out)[0] == "deny", f"out={out[:120]!r}")


def test_c5_messages():
    repo = _staged_plan_repo()
    rc, out, err, secs = _timed("".join(f"git add f{idx};" for idx in range(300)) + " git commit -m x", repo)
    check("c5_invocations_message_says_split_it", "too many distinct git writes in one command; split it" in decision(out)[1], f"{decision(out)[1][:200]}")


def main():
    try:
        for fn in (test_shipped_policy_keys, enterplanmode_denied_by_default, test_plan_commit_table, plan_commit_staged_probe_denies,
                   test_plan_commit_probe_error, test_plan_commit_scope, test_plan_commit_linked_worktree, test_cursor_scope,
                   test_merge_guard, test_invalid_policy_never_widens, test_silence_on_allow_paths,
                   test_c1_glob_collapse_does_not_crash, test_h1_wrapped_git, test_h2_retargeted_repo, test_h3_merge_targets,
                   test_m1_pathspec_magic, test_m3_probe_budget, test_m4_update_index, test_l1_dry_run,
                   test_ha_dry_run_is_decided_from_parsed_options, test_hb_git_dir_owns_the_index,
                   test_m1_catch_all_logs_truncated_and_warns_on_stderr_only, test_m2_unwrapper_gives_up_loudly_and_more_wrappers,
                   test_m3_size_cap_and_time_box, test_rm1_cd_scope_does_not_leak, test_rm2_force_abbreviations_match_real_git,
                   test_rm3_merge_peels_and_pull_value_options, test_budget_reserve_keeps_the_merge_probe_alive,
                   test_nh1_long_merge_tokens_are_bounded, test_nh2_big_commands_fail_closed_when_they_name_git_writes,
                   test_m2_clean_merge_is_not_denied_by_a_burnt_parse_budget, test_m1_static_exports_resolve_like_env_prefix,
                   test_m3_status_formats_imply_dry_run, test_group_heads_and_quoted_operators,
                   test_c4_per_run_work_is_bounded, test_c4_probe_exhaustion_fails_closed, test_c4_fail_closed_is_keyed_on_command_words,
                   test_c4_comments_are_comments, test_c4_unparseable_and_heredocs, test_c4_export_n_and_private_use_characters,
                   test_c5_heredoc_bombs_are_linear_and_still_reach_the_destructive_guard, test_c5_size_path_checks_the_worktree_for_commits,
                   test_c5_live_heredoc_bodies_are_inspected, test_c5_fake_heredocs_in_arithmetic, test_c5_unparseable_uses_raw_text,
                   test_c5_comment_after_closing_paren, test_c5_redirects_are_memoised, test_c5_messages):
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
