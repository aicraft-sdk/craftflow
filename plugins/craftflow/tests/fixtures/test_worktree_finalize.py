#!/usr/bin/env python3
"""Tests for scripts/craftflow_worktree_finalize.py (P7 of the router context cut, ADR-0073, BC-7, PP-4).

`craftflow_worktree_finalize.py` is a byte-faithful port of SKILL.md "### Worktree Isolation (BUILD Default)" step 4a-4e:
it composes `craftflow_worktree_lock_staleness.py` and `craftflow_worktree_copy_fallback.py` with real git on scratch
repositories, prints exactly one JSON line `{outcome, pending_gate, message, worktree_mode, details}` and persists the gate.
It is dark in P7: the router prose does not call it yet (P8 wires it).

Reuses `init_repo`, `try_acquire_lock` and `_set_stage_only` from `scripts/craftflow_worktree_merge_guard_check.py` by importlib.
`tests/fixtures/git_shim/git` fails `git status` / `git branch -d` on demand for the two failures real git will not give a
healthy repo.

Branch-to-test table (step 4 branch -> test name):
  4a path guard                  -> path_missing_event_skip
  4a branch guard                -> branch_missing_event_skip, branch_starting_with_dash_is_not_run
  4b reclaim                     -> corrupt_metadata_reclaims, reclaim_does_not_consume_an_attempt, self_lock_worktree_gone_reclaims
  4b TOCTOU (metadata changed)   -> metadata_changed_between_reads_no_reclaim
  4c holder: CONTENDED <uuid>    -> lock_contended_live_stranger, self_lock_live_worktree_waits
  4c holder: CONTENDED_UNKNOWN   -> contended_unknown_holder_is_unknown
  4c holder: LOCK_READ_ERROR     -> lock_read_error_holder_is_exception_class
  4c holder: GIT_WORKTREE_LIST   -> git_worktree_list_error_template
  4c holder: unrecognized        -> unrecognized_decision_holder_unknown
  4d dirty / status failure      -> dirty_main_tree_releases_lock, status_failure_is_dirty
  4e up to date + fallback ok    -> up_to_date_copy_fallback_ok_cleans_up
  4e up to date + fallback fails -> copy_fallback_failure_partial_apply_gate
  4e real merge                  -> real_merge_cleans_up, real_merge_divergent_creates_merge_commit
  4e conflict                    -> conflict_gate_keeps_worktree
  4e unrecognized failure        -> unrecognized_merge_failure
  cleanup: worktree remove       -> worktree_remove_failure
  cleanup: branch delete         -> branch_delete_refusal
  cleanup: success + release     -> real_merge_cleans_up, release_lock_error_nonfatal
  resume / rerun                 -> resume_after_locked_reenters, resume_after_cleanup_failure_completes, rerun_after_success_skipped,
                                    success_clears_only_worktree_pending_gate
  policy / artifact              -> policy_invalid_touches_nothing, policy_values_are_read, artifact_corrupt_exit_3, root_mismatch_detail,
                                    concurrent_artifact_write_flock

Run: python3 tests/fixtures/test_worktree_finalize.py
Prints `Results: N passed, M failed`; exits 1 on any failure.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
SCRIPT = SCRIPTS / "craftflow_worktree_finalize.py"
SHIM_DIR = Path(__file__).resolve().parent / "git_shim"
sys.path.insert(0, str(SCRIPTS))

_passes = 0
_errors = []

ENUM = {"merged_and_removed", "skipped_not_auto_created", "worktree_path_missing", "worktree_branch_missing", "worktree_merge_locked",
        "worktree_dirty_main_tree", "worktree_copy_fallback_failed", "worktree_merge_conflict", "worktree_merge_unrecognized_failure",
        "worktree_cleanup_failed", "policy_invalid", "artifact_unreadable"}
GATES = {"worktree_merge_locked", "worktree_dirty_main_tree", "worktree_copy_fallback_failed", "worktree_merge_conflict",
         "worktree_merge_unrecognized_failure", "worktree_cleanup_failed", "policy_invalid"}
KEYS = {"outcome", "pending_gate", "message", "worktree_mode", "details"}
SEEN = {}  # outcome -> [Res], filled by expect(); the totality tests read it last


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


try:
    import craftflow_worktree_finalize as fin
except Exception as exc:  # RED: the module does not exist yet
    fin = None
    fail("import craftflow_worktree_finalize", repr(exc))


def _load_harness():
    spec = importlib.util.spec_from_file_location("craftflow_wt_harness", SCRIPTS / "craftflow_worktree_merge_guard_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _load_harness()
init_repo = H.init_repo
try_acquire_lock = H.try_acquire_lock
set_stage_only = H._set_stage_only
FAST = {"worktree_merge": {"require_clean_tree": True, "lock_max_attempts": 2, "lock_sleep_seconds": 0, "copy_fallback_on_up_to_date": True}}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def iso(offset_seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Res:
    def __init__(self, rc, stdout, stderr):
        self.rc, self.stdout, self.stderr = rc, stdout, stderr
        try:
            self.data = json.loads(stdout.splitlines()[0]) if stdout.strip() else {}
        except ValueError:
            self.data = {}

    @property
    def details(self):
        return self.data.get("details") or {}


class Scene:
    """A scratch main repo with one worktree + branch, one workflow artifact and a fast policy file."""

    def __init__(self, tmp, mode="auto_created", pending_gate=None, artifact="default", policy=FAST):
        self.tmp = Path(tmp).resolve()
        self.root = self.tmp / "repo"
        init_repo(self.root)
        ignore = self.root / ".gitignore"
        ignore.write_text(ignore.read_text() + ".craftflow/\n")
        git(self.root, "add", ".gitignore")
        git(self.root, "commit", "-q", "-m", "ignore craftflow state")
        self.wf = "wf-fin-20261016-000000-ab12cd34"
        self.branch = "wf-fin-ab12cd34"
        self.wt = self.root / ".claude" / "worktrees" / "fin-ab12cd34"
        r = git(self.root, "worktree", "add", str(self.wt), "-b", self.branch)
        if r.returncode != 0:
            raise AssertionError(f"worktree add failed: {r.stderr}")
        self.lock = self.root / ".claude" / "worktrees" / ".merge.lock"
        self.wfdir = self.root / ".craftflow" / "state" / "workflows"
        self.wfdir.mkdir(parents=True)
        self.artifact_path = self.wfdir / f"{self.wf}.json"
        self.events_path = self.wfdir / f"{self.wf}.events.jsonl"
        self.policy_path = None
        if policy is not None:
            self.policy_path = self.tmp / "policy.json"
            self.policy_path.write_bytes(policy if isinstance(policy, bytes) else json.dumps(policy).encode("utf-8"))
        if artifact == "default":
            artifact = {"workflow_uuid": self.wf, "worktree_mode": mode, "worktree_path": str(self.wt), "worktree_branch": self.branch,
                        "pending_gate": pending_gate}
        if artifact is not None:
            self.artifact_path.write_bytes(artifact if isinstance(artifact, bytes) else json.dumps(artifact).encode("utf-8"))

    def argv(self, root=None, policy=True):
        out = ["--wf", self.wf, "--project-root", str(root or self.root)]
        if policy and self.policy_path is not None:
            out += ["--policy-file", str(self.policy_path)]
        return out

    def run(self, env=None, root=None, policy=True, extra=()):
        environ = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        environ.update(env or {})
        proc = subprocess.run([sys.executable, str(SCRIPT), *self.argv(root, policy), *extra], capture_output=True, text=True, env=environ)
        return Res(proc.returncode, proc.stdout, proc.stderr)

    def shim_env(self, fail_on):
        real = shutil.which("git")
        return {"PATH": f"{SHIM_DIR}{os.pathsep}{os.environ['PATH']}", "CRAFTFLOW_GIT_SHIM_REAL_GIT": real, "CRAFTFLOW_GIT_SHIM_FAIL": fail_on}

    def artifact(self):
        return json.loads(self.artifact_path.read_text(encoding="utf-8"))

    def events(self):
        if not self.events_path.exists():
            return []
        return [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def commit_in_worktree(self, name="feature.txt", text="from the worktree\n"):
        (self.wt / name).write_text(text)
        git(self.wt, "add", name)
        r = git(self.wt, "commit", "-q", "-m", f"add {name}")
        if r.returncode != 0:
            raise AssertionError(r.stderr)

    def commit_in_main(self, name, text):
        (self.root / name).write_text(text)
        git(self.root, "add", name)
        r = git(self.root, "commit", "-q", "-m", f"main {name}")
        if r.returncode != 0:
            raise AssertionError(r.stderr)

    def snapshot(self):
        return {"porcelain": git(self.root, "status", "--porcelain").stdout, "branch": git(self.root, "rev-parse", self.branch).stdout,
                "worktree": sorted(os.listdir(self.wt)) if self.wt.exists() else None}

    def branch_exists(self):
        return git(self.root, "rev-parse", "--verify", "-q", f"refs/heads/{self.branch}").returncode == 0


def expect(name, scene, res, outcome, prefix, *, gate="same", mode="auto_created", lock_present=False, rc=0, art_gate=None):
    """The assertions every outcome test shares: outcome, pending_gate (response and artifact), worktree_mode (response and
    artifact), the worktree_finalize event, the verbatim message prefix, and the lock dir. `gate="same"` = a gate outcome
    persists its own name; pass None for a non-gate outcome."""
    d = res.data
    if gate == "same":
        gate = outcome
    SEEN.setdefault(outcome, []).append(res)
    check(f"{name}:exit", res.rc == rc, f"rc={res.rc} stderr={res.stderr[:200]} stdout={res.stdout[:300]}")
    check(f"{name}:outcome", d.get("outcome") == outcome, f"got {d.get('outcome')!r}; stdout={res.stdout[:300]}")
    check(f"{name}:pending_gate", d.get("pending_gate") == gate, f"got {d.get('pending_gate')!r}")
    if scene.artifact_path.exists():
        try:
            art = scene.artifact()
        except ValueError:
            art = {}
        if outcome != "artifact_unreadable":
            want = gate if gate is not None else art_gate
            check(f"{name}:artifact_pending_gate", art.get("pending_gate") == want, f"artifact pending_gate={art.get('pending_gate')!r} want {want!r}")
            check(f"{name}:artifact_worktree_mode", art.get("worktree_mode") == mode, f"artifact mode={art.get('worktree_mode')!r}")
    check(f"{name}:response_worktree_mode", d.get("worktree_mode") == mode, f"got {d.get('worktree_mode')!r}")
    events = [e for e in scene.events() if e.get("event") == "worktree_finalize"]
    check(f"{name}:event", bool(events) and events[-1].get("outcome") == outcome and events[-1].get("wf") == scene.wf, f"events={events}")
    check(f"{name}:message_prefix", isinstance(d.get("message"), str) and d["message"].startswith(prefix), f"message={d.get('message')!r}")
    check(f"{name}:lock_dir", scene.lock.exists() == lock_present, f"lock exists={scene.lock.exists()}")


def tmpscene(fn):
    def wrapper():
        print(f"\n[{fn.__name__}]")
        with tempfile.TemporaryDirectory() as tmp:
            fn(tmp)
    wrapper.__name__ = fn.__name__
    return wrapper


MSG_SKIP = "Worktree finalize skipped: worktree_mode is not auto_created"
MSG_LOCKED = "Another BUILD workflow ("
MSG_LOCK_ERR = "Failed to evaluate the merge lock due to a local error ("
MSG_DIRTY = "The main tree has uncommitted changes this BUILD did not make"
MSG_COPY = "The copy-fallback script failed while applying the worktree's uncommitted changes to the main tree: `"
MSG_UNREC = "`git merge wf-fin-ab12cd34` failed with an error this router does not recognize"
MSG_REMOVE = "The merge/copy-fallback succeeded, but `git worktree remove "
MSG_BRANCH = "The merge/copy-fallback succeeded and the worktree was removed, but `git branch -d wf-fin-ab12cd34` failed: `"
MSG_OK = "Worktree merged and removed"


# ---- skips and guards (nothing is touched) -----------------------------------------------------------------------------

@tmpscene
def test_skipped_when_mode_null(tmp):
    for mode in (None, "merged_and_removed"):
        s = Scene(_sub(tmp, str(mode)), mode=mode)
        before = s.snapshot()
        res = s.run()
        expect(f"skipped_when_mode_{mode}", s, res, "skipped_not_auto_created", MSG_SKIP, gate=None, mode=mode)
        check(f"skipped_when_mode_{mode}:untouched", s.snapshot() == before and s.branch_exists() and s.wt.exists(), "worktree or branch changed")


def _sub(tmp, name):
    path = Path(tmp) / name
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


@tmpscene
def test_path_missing_event_skip(tmp):
    for label, value in (("empty", ""), ("whitespace", "   "), ("null", None)):
        s = Scene(_sub(tmp, label), artifact={"workflow_uuid": "x", "worktree_mode": "auto_created", "worktree_path": value,
                                              "worktree_branch": "wf-fin-ab12cd34", "pending_gate": None})
        before = s.snapshot()
        res = s.run()
        expect(f"path_missing_{label}", s, res, "worktree_path_missing", "Worktree finalize skipped: worktree_path", gate=None)
        check(f"path_missing_{label}:legacy_event", any(e.get("event") == "worktree_path_missing" for e in s.events()), f"events={s.events()}")
        check(f"path_missing_{label}:untouched", s.snapshot() == before and s.branch_exists(), "something was touched")


@tmpscene
def test_branch_missing_event_skip(tmp):
    for label, value in (("empty", ""), ("whitespace", " \t "), ("null", None)):
        s = Scene(_sub(tmp, label))
        art = s.artifact()
        art["worktree_branch"] = value
        s.artifact_path.write_text(json.dumps(art))
        before = s.snapshot()
        res = s.run()
        expect(f"branch_missing_{label}", s, res, "worktree_branch_missing", "Worktree finalize skipped: worktree_branch", gate=None)
        check(f"branch_missing_{label}:legacy_event", any(e.get("event") == "worktree_branch_missing" for e in s.events()), f"events={s.events()}")
        check(f"branch_missing_{label}:untouched", s.snapshot() == before and s.branch_exists(), "something was touched")


@tmpscene
def test_branch_starting_with_dash_is_not_run(tmp):
    s = Scene(tmp)
    art = s.artifact()
    art["worktree_branch"] = "--abort"
    s.artifact_path.write_text(json.dumps(art))
    before = s.snapshot()
    res = s.run()
    expect("branch_starting_with_dash", s, res, "worktree_branch_missing", "Worktree finalize skipped: worktree_branch", gate=None)
    check("branch_starting_with_dash:untouched", s.snapshot() == before and s.wt.exists(), "something was touched")


@tmpscene
def test_policy_invalid_touches_nothing(tmp):
    s = Scene(tmp, policy={"worktree_merge": {"require_clean_tree": False, "lock_max_attempts": 2, "lock_sleep_seconds": 0,
                                              "copy_fallback_on_up_to_date": True}})
    s.commit_in_worktree()
    before = s.snapshot()
    res = s.run()
    expect("policy_invalid", s, res, "policy_invalid", "config/policy.json is invalid")
    check("policy_invalid:touches_nothing", s.snapshot() == before and s.branch_exists() and s.wt.exists(), "main tree, branch or worktree changed")
    check("policy_invalid:details_status", res.details.get("policy_status") == "invalid" and bool(res.details.get("policy_errors")), f"details={res.details}")
    check("policy_invalid:no_merge_commit", not (s.root / "feature.txt").exists(), "the worktree commit was merged")


@tmpscene
def test_policy_values_are_read(tmp):
    s = Scene(tmp, policy={"worktree_merge": {"require_clean_tree": True, "lock_max_attempts": 3, "lock_sleep_seconds": 0,
                                              "copy_fallback_on_up_to_date": True}})
    assert try_acquire_lock(s.lock, "wf-holder", str(s.wt))
    calls = []

    def runner(meta, root, wf):
        calls.append((meta, root, wf))
        return "CONTENDED wf-holder"

    code, line = fin.run(s.argv(), staleness_runner=runner)
    check("policy_values_are_read:three_attempts", len(calls) == 3 and json.loads(line)["outcome"] == "worktree_merge_locked", f"calls={len(calls)} line={line[:200]}")
    check("policy_values_are_read:runner_argv", calls and calls[0] == (str(s.lock / "metadata.json"), str(s.root), s.wf), f"calls={calls[:1]}")


# ---- 4b/4c the lock ------------------------------------------------------------------------------------------------------

@tmpscene
def test_lock_contended_live_stranger(tmp):
    s = Scene(tmp)
    other = s.root / ".claude" / "worktrees" / "stranger"
    git(s.root, "worktree", "add", str(other), "-b", "stranger-branch")
    assert try_acquire_lock(s.lock, "wf-holder-1111", str(other))
    meta_before = (s.lock / "metadata.json").read_text()
    before = s.snapshot()
    res = s.run()
    expect("lock_contended_live_stranger", s, res, "worktree_merge_locked", MSG_LOCKED + "wf-holder-1111)", lock_present=True)
    check("lock_contended_live_stranger:holder", res.details.get("holder") == "wf-holder-1111" and res.details.get("decision", "").startswith("CONTENDED wf-holder-1111"),
          f"details={res.details}")
    check("lock_contended_live_stranger:strangers_lock_untouched", (s.lock / "metadata.json").read_text() == meta_before, "metadata changed")
    check("lock_contended_live_stranger:untouched", s.snapshot() == before and s.branch_exists(), "something was touched")
    check("lock_contended_live_stranger:no_merge", not (s.root / "feature.txt").exists(), "merged while contended")


@tmpscene
def test_lock_read_error_holder_is_exception_class(tmp):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        ok("lock_read_error_holder_is_exception_class:skipped_as_root")
        return
    s = Scene(tmp)
    s.lock.mkdir()
    meta = s.lock / "metadata.json"
    meta.write_text(json.dumps({"workflow_uuid": "wf-x"}))
    meta.chmod(0o000)
    try:
        res = s.run()
    finally:
        meta.chmod(0o644)
    expect("lock_read_error", s, res, "worktree_merge_locked", MSG_LOCK_ERR + "`PermissionError`)", lock_present=True)
    check("lock_read_error:holder_is_class", res.details.get("holder") == "PermissionError", f"details={res.details}")
    check("lock_read_error:not_unknown", "unknown" not in res.details.get("holder", "") and "Another BUILD workflow" not in res.data.get("message", ""),
          res.data.get("message", "")[:120])


@tmpscene
def test_git_worktree_list_error_template(tmp):
    s = Scene(tmp)
    assert try_acquire_lock(s.lock, "wf-holder", str(s.root / "somewhere"))
    res = s.run(env={"PATH": str(Path(tmp) / "no-git-here")})
    expect("git_worktree_list_error", s, res, "worktree_merge_locked", MSG_LOCK_ERR, lock_present=True)
    check("git_worktree_list_error:holder_is_class", res.details.get("holder") == "FileNotFoundError", f"details={res.details}")
    check("git_worktree_list_error:template_names_path", "confirm `git` is on `PATH` and runnable" in res.data.get("message", ""), res.data.get("message", "")[:200])
    s2 = Scene(_sub(tmp, "nonzero"))
    assert try_acquire_lock(s2.lock, "wf-holder", str(s2.root / "somewhere"))
    code, line = fin.run(s2.argv(), staleness_runner=lambda m, r, w: "GIT_WORKTREE_LIST_ERROR NonZeroExit(rc=128): fatal: not a git repository")
    data = json.loads(line)
    check("git_worktree_list_error:nonzero_form_names_cause", data["details"]["holder"].startswith("NonZeroExit(rc=128)")
          and data["message"].startswith(MSG_LOCK_ERR), line[:300])


@tmpscene
def test_contended_unknown_holder_is_unknown(tmp):
    s = Scene(tmp)
    s.lock.mkdir()  # a fresh lock dir with no metadata.json: the benign mkdir -> write gap
    res = s.run()
    expect("contended_unknown_holder", s, res, "worktree_merge_locked", MSG_LOCKED + "unknown)", lock_present=True)
    check("contended_unknown_holder:holder", res.details.get("holder") == "unknown", f"details={res.details}")


@tmpscene
def test_unrecognized_decision_holder_unknown(tmp):
    s = Scene(tmp)
    assert try_acquire_lock(s.lock, "wf-holder", str(s.wt))
    for label, decision in (("garbled", "WHAT IS THIS"), ("empty", "")):
        code, line = fin.run(s.argv(), staleness_runner=lambda m, r, w, d=decision: d)
        data = json.loads(line)
        check(f"unrecognized_decision_{label}:holder_unknown", data["outcome"] == "worktree_merge_locked" and data["details"]["holder"] == "unknown"
              and data["message"].startswith(MSG_LOCKED + "unknown)"), line[:300])
        check(f"unrecognized_decision_{label}:lock_kept", s.lock.exists(), "lock was removed on an unrecognized decision")


@tmpscene
def test_corrupt_metadata_reclaims(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    s.lock.mkdir()
    (s.lock / "metadata.json").write_text("{not valid json")
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).timestamp()
    os.utime(s.lock, (old, old))
    res = s.run()
    expect("corrupt_metadata_reclaims", s, res, "merged_and_removed", MSG_OK, gate=None, mode="merged_and_removed")
    check("corrupt_metadata_reclaims:merged", (s.root / "feature.txt").read_text() == "from the worktree\n", "the merge did not land after the reclaim")


@tmpscene
def test_self_lock_worktree_gone_reclaims(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    assert try_acquire_lock(s.lock, s.wf, str(s.root / "nonexistent-worktree"))  # same workflow, worktree positively gone
    res = s.run()
    expect("self_lock_worktree_gone_reclaims", s, res, "merged_and_removed", MSG_OK, gate=None, mode="merged_and_removed")


@tmpscene
def test_self_lock_live_worktree_waits(tmp):
    s = Scene(tmp)
    assert try_acquire_lock(s.lock, s.wf, str(s.wt))  # same workflow uuid, own worktree still alive: not proof of death
    res = s.run()
    expect("self_lock_live_worktree_waits", s, res, "worktree_merge_locked", MSG_LOCKED + s.wf + ")", lock_present=True)


@tmpscene
def test_reclaim_does_not_consume_an_attempt(tmp):
    s = Scene(tmp, policy={"worktree_merge": {"require_clean_tree": True, "lock_max_attempts": 1, "lock_sleep_seconds": 0,
                                              "copy_fallback_on_up_to_date": True}})
    s.commit_in_worktree()
    assert try_acquire_lock(s.lock, "wf-dead", str(s.root / "gone"))
    calls = []

    def runner(meta, root, wf):
        calls.append(1)
        return "STALE_WORKTREE_GONE wf-dead"

    code, line = fin.run(s.argv(), staleness_runner=runner)
    data = json.loads(line)
    check("reclaim_does_not_consume_an_attempt", data["outcome"] == "merged_and_removed" and len(calls) == 1,
          f"calls={len(calls)} line={line[:250]}")


@tmpscene
def test_metadata_changed_between_reads_no_reclaim(tmp):
    s = Scene(tmp)
    assert try_acquire_lock(s.lock, "wf-first", str(s.wt))
    calls = []
    metas = []

    def runner(meta, root, wf):
        calls.append(1)
        metas.append(json.dumps({"workflow_uuid": f"wf-occupant-{len(calls)}", "worktree_path": str(s.wt), "acquired_at": iso()}))
        Path(meta).write_text(metas[-1])  # a different process took the lock between the two reads, every time
        return "STALE_INACTIVE wf-first"

    code, line = fin.run(s.argv(), staleness_runner=runner)
    data = json.loads(line)
    check("metadata_changed:no_reclaim", data["outcome"] == "worktree_merge_locked" and s.lock.exists() and (s.lock / "metadata.json").read_text() == metas[-1],
          f"line={line[:250]} lock={s.lock.exists()}")
    check("metadata_changed:waited_full_budget", len(calls) == 2, f"calls={len(calls)}")
    check("metadata_changed:holder_unknown", data["details"]["holder"] == "unknown", f"details={data['details']}")


@tmpscene
def test_lock_metadata_has_three_fields_while_held(tmp):
    s = Scene(tmp)
    seen = []
    real = fin._git

    def spy(args, cwd, env=None):
        if "status" in args:
            seen.append(json.loads((s.lock / "metadata.json").read_text()))
        return real(args, cwd, env) if env is not None else real(args, cwd)

    fin._git = spy
    try:
        s.commit_in_worktree()
        code, line = fin.run(s.argv())
    finally:
        fin._git = real
    check("lock_metadata:fields_while_held", len(seen) == 1 and set(seen[0]) == {"workflow_uuid", "worktree_path", "acquired_at"}
          and seen[0]["workflow_uuid"] == s.wf and seen[0]["worktree_path"] == str(s.wt), f"seen={seen} line={line[:200]}")
    check("lock_metadata:released_after", not s.lock.exists(), "lock left behind")


# ---- 4d the clean-tree check --------------------------------------------------------------------------------------------------

@tmpscene
def test_dirty_main_tree_releases_lock(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    (s.root / "README.md").write_text("dirtied by a concurrent session\n")
    before = s.snapshot()
    res = s.run()
    expect("dirty_main_tree", s, res, "worktree_dirty_main_tree", MSG_DIRTY)
    check("dirty_main_tree:status_in_details", " M README.md" in res.details.get("dirty_status", ""), f"details={res.details}")
    check("dirty_main_tree:untouched", s.snapshot() == before and s.wt.exists() and s.branch_exists() and not (s.root / "feature.txt").exists(),
          "main tree, worktree or branch changed")


@tmpscene
def test_status_failure_is_dirty(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    res = s.run(env=s.shim_env("status --porcelain"))
    expect("status_failure_is_dirty", s, res, "worktree_dirty_main_tree", MSG_DIRTY)
    check("status_failure_is_dirty:error_recorded", "shim: refused" in res.details.get("dirty_status", ""), f"details={res.details}")
    check("status_failure_is_dirty:no_merge", not (s.root / "feature.txt").exists() and s.wt.exists(), "merged or removed after a failed status")


# ---- 4e finalize --------------------------------------------------------------------------------------------------------------

@tmpscene
def test_real_merge_cleans_up(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    res = s.run()
    expect("real_merge_cleans_up", s, res, "merged_and_removed", MSG_OK, gate=None, mode="merged_and_removed")
    check("real_merge_cleans_up:content", (s.root / "feature.txt").read_text() == "from the worktree\n", "merged file missing")
    check("real_merge_cleans_up:worktree_gone", not s.wt.exists() and not s.branch_exists(), f"worktree={s.wt.exists()} branch={s.branch_exists()}")
    check("real_merge_cleans_up:main_clean", git(s.root, "status", "--porcelain").stdout == "", "main tree left dirty")


@tmpscene
def test_real_merge_divergent_creates_merge_commit(tmp):
    s = Scene(tmp)
    s.commit_in_main("main-only.txt", "main\n")
    s.commit_in_worktree()
    res = s.run()
    expect("real_merge_divergent", s, res, "merged_and_removed", MSG_OK, gate=None, mode="merged_and_removed")
    parents = git(s.root, "log", "-1", "--format=%p").stdout.split()
    check("real_merge_divergent:merge_commit", len(parents) == 2 and (s.root / "feature.txt").exists() and (s.root / "main-only.txt").exists(), f"parents={parents}")


@tmpscene
def test_up_to_date_copy_fallback_ok_cleans_up(tmp):
    s = Scene(tmp)
    (s.wt / "new.txt").write_text("uncommitted new file\n")
    (s.wt / "README.md").write_text("changed in the worktree, never committed\n")
    res = s.run()
    expect("up_to_date_copy_fallback", s, res, "merged_and_removed", MSG_OK, gate=None, mode="merged_and_removed")
    check("up_to_date_copy_fallback:copied", (s.root / "new.txt").read_text() == "uncommitted new file\n"
          and (s.root / "README.md").read_text() == "changed in the worktree, never committed\n", "uncommitted worktree changes did not land")
    check("up_to_date_copy_fallback:cleaned_up", not s.wt.exists() and not s.branch_exists(), "worktree or branch left behind")
    check("up_to_date_copy_fallback:not_committed", git(s.root, "status", "--porcelain").stdout.count("\n") == 2, "the copy must stay uncommitted")


@tmpscene
def test_hyphenated_up_to_date_is_not_special_cased_e32(tmp):
    """E32 (documented, deferred): git < 2.17 prints "Already up-to-date." and bypasses the copy-fallback, exactly as the prose does.
    Finalize must not quietly fix it in P7 (drift)."""
    s = Scene(tmp)
    (s.wt / "uncommitted.txt").write_text("never committed\n")
    real = fin._git

    def old_git(args, cwd, env=None):
        if args[:1] == ["merge"]:
            return 0, "Already up-to-date."
        return real(args, cwd, env) if env is not None else real(args, cwd)

    fin._git = old_git
    try:
        code, line = fin.run(s.argv())
    finally:
        fin._git = real
    data = json.loads(line)
    check("e32_hyphenated_not_special_cased", data["outcome"] == "merged_and_removed" and not (s.root / "uncommitted.txt").exists(), line[:250])


@tmpscene
def test_copy_fallback_failure_partial_apply_gate(tmp):
    s = Scene(tmp)
    set_stage_only(s.wt, "README.md", (1, 2, 3))  # an unresolved two-sided conflict the copy script refuses to guess on
    res = s.run()
    expect("copy_fallback_failure", s, res, "worktree_copy_fallback_failed", MSG_COPY)
    check("copy_fallback_failure:keeps_source_of_truth", s.wt.exists() and s.branch_exists(), "worktree or branch removed after a failed fallback")
    check("copy_fallback_failure:output_in_details", bool(res.details.get("copy_fallback_output")), f"details={res.details}")
    check("copy_fallback_failure:partial_apply_warning", "may be a partial apply" in res.data["message"], res.data["message"][:200])


@tmpscene
def test_conflict_gate_keeps_worktree(tmp):
    s = Scene(tmp)
    s.commit_in_main("README.md", "main tree change\n")
    s.commit_in_worktree("README.md", "worktree change\n")
    wt_listing = sorted(os.listdir(s.wt))
    branch_ref = git(s.root, "rev-parse", s.branch).stdout
    res = s.run()
    try:
        expect("conflict_gate", s, res, "worktree_merge_conflict", "`git merge wf-fin-ab12cd34` stopped with merge conflicts")
        check("conflict_gate:keeps_worktree_and_branch", s.wt.exists() and s.branch_exists() and sorted(os.listdir(s.wt)) == wt_listing
              and git(s.root, "rev-parse", s.branch).stdout == branch_ref, "worktree or branch touched")
        check("conflict_gate:conflict_state_left_for_the_user", "<<<<<<<" in (s.root / "README.md").read_text(), "conflict markers missing")
    finally:
        git(s.root, "merge", "--abort")


@tmpscene
def test_unrecognized_merge_failure(tmp):
    s = Scene(tmp)
    git(s.root, "branch", "-m", s.branch, "renamed-away")  # the artifact now names a branch that no longer exists
    before = {"porcelain": git(s.root, "status", "--porcelain").stdout, "worktree": sorted(os.listdir(s.wt))}
    res = s.run()
    expect("unrecognized_merge_failure", s, res, "worktree_merge_unrecognized_failure", MSG_UNREC)
    check("unrecognized_merge_failure:output_in_details", bool(res.details.get("merge_output")), f"details={res.details}")
    check("unrecognized_merge_failure:untouched", git(s.root, "status", "--porcelain").stdout == before["porcelain"]
          and sorted(os.listdir(s.wt)) == before["worktree"] and s.wt.exists(), "something was touched")


@tmpscene
def test_worktree_remove_failure(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    locked = git(s.root, "worktree", "lock", str(s.wt))
    check("worktree_remove_failure:setup", locked.returncode == 0, locked.stderr)
    res = s.run()
    try:
        expect("worktree_remove_failure", s, res, "worktree_cleanup_failed", MSG_REMOVE)
        check("worktree_remove_failure:keeps_worktree_and_branch", s.wt.exists() and s.branch_exists(), "worktree or branch removed")
        check("worktree_remove_failure:merge_landed", (s.root / "feature.txt").exists(), "the merge did not land before cleanup failed")
        check("worktree_remove_failure:failing_command", res.details.get("failing_command") == "git worktree remove", f"details={res.details}")
    finally:
        git(s.root, "worktree", "unlock", str(s.wt))


@tmpscene
def test_branch_delete_refusal(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    res = s.run(env=s.shim_env("branch -d"))
    expect("branch_delete_refusal", s, res, "worktree_cleanup_failed", MSG_BRANCH)
    check("branch_delete_refusal:worktree_removed_branch_kept", not s.wt.exists() and s.branch_exists(), f"worktree={s.wt.exists()} branch={s.branch_exists()}")
    check("branch_delete_refusal:failing_command", res.details.get("failing_command") == "git branch -d", f"details={res.details}")


# ---- resume, rerun, the pending_gate rules -----------------------------------------------------------------------------------

@tmpscene
def test_resume_after_locked_reenters(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    assert try_acquire_lock(s.lock, "wf-holder", str(s.wt))
    first = s.run()
    expect("resume_after_locked:first", s, first, "worktree_merge_locked", MSG_LOCKED, lock_present=True)
    shutil.rmtree(s.lock)
    second = s.run()
    expect("resume_after_locked:second", s, second, "merged_and_removed", MSG_OK, gate=None, mode="merged_and_removed")
    check("resume_after_locked:gate_cleared", s.artifact()["pending_gate"] is None, f"pending_gate={s.artifact()['pending_gate']!r}")


@tmpscene
def test_resume_after_cleanup_failure_completes(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    git(s.root, "worktree", "lock", str(s.wt))
    first = s.run()
    check("resume_after_cleanup:first_gated", first.data.get("outcome") == "worktree_cleanup_failed" and s.artifact()["pending_gate"] == "worktree_cleanup_failed",
          first.stdout[:300])
    git(s.root, "worktree", "unlock", str(s.wt))
    second = s.run()  # the branch is already merged, so this is the "Already up to date" arm with a clean worktree
    expect("resume_after_cleanup:second", s, second, "merged_and_removed", MSG_OK, gate=None, mode="merged_and_removed")
    check("resume_after_cleanup:gate_cleared", s.artifact()["pending_gate"] is None, f"pending_gate={s.artifact()['pending_gate']!r}")


@tmpscene
def test_rerun_after_success_skipped(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    first = s.run()
    check("rerun_after_success:first", first.data.get("outcome") == "merged_and_removed", first.stdout[:300])
    second = s.run()
    expect("rerun_after_success", s, second, "skipped_not_auto_created", MSG_SKIP, gate=None, mode="merged_and_removed")


@tmpscene
def test_success_clears_only_worktree_pending_gate(tmp):
    s = Scene(tmp, pending_gate="clarification")
    s.commit_in_worktree()
    res = s.run()
    expect("success_keeps_foreign_gate", s, res, "merged_and_removed", MSG_OK, gate=None, mode="merged_and_removed", art_gate="clarification")
    check("success_keeps_foreign_gate:kept", s.artifact()["pending_gate"] == "clarification", f"pending_gate={s.artifact()['pending_gate']!r}")
    for gate in sorted(GATES):
        s2 = Scene(_sub(tmp, gate), pending_gate=gate)
        s2.commit_in_worktree()
        r2 = s2.run()
        check(f"success_clears_{gate}", r2.data.get("outcome") == "merged_and_removed" and s2.artifact()["pending_gate"] is None,
              f"outcome={r2.data.get('outcome')} pending_gate={s2.artifact().get('pending_gate')!r}")


# ---- artifact, root, concurrency, errors -------------------------------------------------------------------------------------

@tmpscene
def test_artifact_corrupt_exit_3(tmp):
    for label, payload in (("corrupt", b"{not json"), ("not_an_object", b"[1, 2]"), ("missing", None)):
        s = Scene(_sub(tmp, label), artifact=payload)
        s.commit_in_worktree()
        before = s.snapshot()
        res = s.run()
        d = res.data
        SEEN.setdefault("artifact_unreadable", []).append(res)
        check(f"artifact_{label}:exit_3", res.rc == 3, f"rc={res.rc} stdout={res.stdout[:200]}")
        check(f"artifact_{label}:outcome", d.get("outcome") == "artifact_unreadable" and d.get("pending_gate") is None
              and str(d.get("message", "")).startswith("The workflow artifact is missing or unreadable"), res.stdout[:300])
        check(f"artifact_{label}:touches_nothing", s.snapshot() == before and s.wt.exists() and s.branch_exists() and not s.lock.exists(), "something was touched")
        if payload is not None:
            check(f"artifact_{label}:file_not_rewritten", s.artifact_path.read_bytes() == payload, "artifact bytes changed")


@tmpscene
def test_root_mismatch_detail(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    other = Path(tmp).resolve() / "elsewhere"
    wfdir = other / ".craftflow" / "state" / "workflows"
    wfdir.mkdir(parents=True)
    shutil.move(str(s.artifact_path), str(wfdir / f"{s.wf}.json"))
    s.wfdir, s.artifact_path, s.events_path = wfdir, wfdir / f"{s.wf}.json", wfdir / f"{s.wf}.events.jsonl"
    res = s.run(root=other)
    expect("root_mismatch", s, res, "merged_and_removed", MSG_OK, gate=None, mode="merged_and_removed")
    mismatch = res.details.get("root_mismatch") or {}
    check("root_mismatch:detail", mismatch.get("merge_root") == str(s.root) and mismatch.get("project_root") == str(other), f"details={res.details}")
    check("root_mismatch:merged_in_derived_root", (s.root / "feature.txt").exists(), "the merge did not run against MERGE_ROOT")


@tmpscene
def test_no_root_mismatch_key_when_roots_agree(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    res = s.run()
    check("no_root_mismatch_when_equal", "root_mismatch" not in res.details, f"details={res.details}")


@tmpscene
def test_concurrent_artifact_write_flock(tmp):
    import fcntl
    s = Scene(tmp)
    (s.root / "README.md").write_text("dirty\n")  # a gate outcome: it writes pending_gate
    lock_file = s.artifact_path.with_name(s.artifact_path.name + ".lock")
    handle = open(lock_file, "a")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    environ = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    proc = subprocess.Popen([sys.executable, str(SCRIPT), *s.argv()], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environ)
    try:
        time.sleep(0.7)
        blocked = proc.poll() is None
        art = s.artifact()
        art["written_by_someone_else"] = 1
        s.artifact_path.write_text(json.dumps(art))
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
    out, err = proc.communicate(timeout=60)
    check("concurrent_flock:waits_for_the_artifact_lock", blocked, "finalize ran past a held artifact lock")
    final = s.artifact()
    check("concurrent_flock:no_lost_update", final.get("written_by_someone_else") == 1 and final.get("pending_gate") == "worktree_dirty_main_tree",
          f"final={final} out={out[:200]} err={err[:200]}")
    # A write that lands while finalize is mid-run (during the lock wait) is also preserved: read-modify-write at write time.
    s2 = Scene(_sub(tmp, "mid"))
    assert try_acquire_lock(s2.lock, "wf-holder", str(s2.wt))

    def runner(meta, root, wf):
        art2 = s2.artifact()
        art2["written_mid_run"] = 2
        s2.artifact_path.write_text(json.dumps(art2))
        return "CONTENDED wf-holder"

    code, line = fin.run(s2.argv(), staleness_runner=runner)
    final2 = s2.artifact()
    check("concurrent_flock:mid_run_write_preserved", final2.get("written_mid_run") == 2 and final2.get("pending_gate") == "worktree_merge_locked", f"final={final2} line={line[:200]}")


@tmpscene
def test_events_file_created_and_appended(tmp):
    s = Scene(tmp, mode=None)
    check("events_file:absent_before", not s.events_path.exists(), "events file should not exist yet")
    r1 = s.run()
    r2 = s.run()
    lines = [e for e in s.events() if e.get("event") == "worktree_finalize"]
    check("events_file:created_and_appended", r1.rc == 0 and r2.rc == 0 and len(lines) == 2 and all(set(e) >= {"ts", "wf", "outcome", "pending_gate"} for e in lines),
          f"events={lines}")


@tmpscene
def test_release_lock_error_nonfatal(tmp):
    s = Scene(tmp)
    s.commit_in_worktree()
    real = fin._rmtree

    def boom(path):
        raise OSError("release denied")

    fin._rmtree = boom
    try:
        code, line = fin.run(s.argv())
    finally:
        fin._rmtree = real
    data = json.loads(line)
    check("release_lock_error:nonfatal", code == 0 and data["outcome"] == "merged_and_removed" and "release denied" in data["details"].get("release_lock_error", ""), line[:300])
    shutil.rmtree(s.lock, ignore_errors=True)


@tmpscene
def test_usage_errors_exit_2(tmp):
    for label, argv in (("no_args", []), ("bad_wf", ["--wf", "../x", "--project-root", tmp]), ("unknown_flag", ["--wf", "wf-x", "--project-root", tmp, "--bogus", "1"]),
                        ("missing_root", ["--wf", "wf-x"])):
        proc = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True)
        try:
            data = json.loads(proc.stdout.splitlines()[0])
        except (ValueError, IndexError):
            data = {}
        check(f"usage_{label}", proc.returncode == 2 and "error" in data and "outcome" not in data, f"rc={proc.returncode} stdout={proc.stdout[:200]}")


@tmpscene
def test_no_side_effects_on_gate_outcomes(tmp):
    """PP-4: on every gate or skip outcome except the documented ones, `git status --porcelain`, the branch ref and the worktree
    listing are byte-equal before and after. The conflict gate is the one exception for the main tree: the merge attempt itself
    leaves its conflict state there for the user, so only the branch ref and the worktree listing are compared. The partial-apply
    copy-fallback gate is excluded by BC-7."""
    cases = {}

    def snap_run(label, scene, env=None, **kw):
        before = scene.snapshot()
        res = scene.run(env=env, **kw)
        after = scene.snapshot()
        cases[label] = (before, after, res)

    s = Scene(_sub(tmp, "locked"))
    s.commit_in_worktree()
    assert try_acquire_lock(s.lock, "wf-holder", str(s.wt))
    snap_run("locked", s)
    s = Scene(_sub(tmp, "dirty"))
    s.commit_in_worktree()
    (s.root / "README.md").write_text("dirty\n")
    snap_run("dirty", s)
    s = Scene(_sub(tmp, "unrec"))
    git(s.root, "branch", "-m", s.branch, "gone")
    snap_run("unrecognized", s)
    s = Scene(_sub(tmp, "policy"), policy=b"{broken")
    s.commit_in_worktree()
    snap_run("policy_invalid", s)
    s = Scene(_sub(tmp, "skip"), mode=None)
    snap_run("skipped", s)
    s = Scene(_sub(tmp, "nopath"), artifact={"worktree_mode": "auto_created", "worktree_path": "", "worktree_branch": "b"})
    snap_run("path_missing", s)
    s = Scene(_sub(tmp, "nobranch"), artifact={"worktree_mode": "auto_created", "worktree_path": "/x", "worktree_branch": ""})
    snap_run("branch_missing", s)
    s = Scene(_sub(tmp, "corrupt"), artifact=b"{nope")
    snap_run("artifact_unreadable", s)
    for label, (before, after, res) in sorted(cases.items()):
        check(f"no_side_effects:{label}", before == after and res.data.get("outcome") in ENUM, f"before={before} after={after} outcome={res.data.get('outcome')}")
        SEEN.setdefault(res.data.get("outcome"), []).append(res)
    s = Scene(_sub(tmp, "conflict"))
    s.commit_in_main("README.md", "main\n")
    s.commit_in_worktree("README.md", "worktree\n")
    ref, listing = git(s.root, "rev-parse", s.branch).stdout, sorted(os.listdir(s.wt))
    res = s.run()
    git(s.root, "merge", "--abort")
    check("no_side_effects:conflict_branch_and_worktree", git(s.root, "rev-parse", s.branch).stdout == ref and sorted(os.listdir(s.wt)) == listing
          and res.data.get("outcome") == "worktree_merge_conflict", res.stdout[:200])


# ---- totality, hygiene -------------------------------------------------------------------------------------------------------

def test_exactly_one_json_line_enum():
    print("\n[exactly_one_json_line_enum]")
    bad = []
    count = 0
    for outcome, results in SEEN.items():
        for res in results:
            count += 1
            lines = res.stdout.splitlines()
            gate = res.data.get("pending_gate")
            if (len(lines) != 1 or not res.stdout.endswith("\n") or set(res.data) != KEYS or res.data.get("outcome") not in ENUM
                    or (gate is not None) != (res.data.get("outcome") in GATES) or not isinstance(res.data.get("details"), dict)):
                bad.append((outcome, res.stdout[:160]))
    check("exactly_one_json_line_enum", count >= len(ENUM) and not bad, f"count={count} bad={bad[:3]}")


def test_every_outcome_is_exercised():
    print("\n[every outcome exercised]")
    check("every_outcome_is_exercised", set(SEEN) == ENUM, f"missing={sorted(ENUM - set(SEEN))} extra={sorted(set(SEEN) - ENUM)}")


def test_script_hygiene():
    print("\n[script hygiene]")
    src = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src, feature_version=(3, 9))
    check("hygiene:py39", True)
    check("hygiene:line_budget", len(src.splitlines()) <= 450, f"{len(src.splitlines())} lines")
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    bad = [t for t in literals if "craftflow-router" in t or "_shared" in t or "router-protocol" in t]
    check("hygiene:pp2_no_prose_path", not bad, f"literals={bad[:3]}")
    calls = {n.args[1].value for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "policy_value" and len(n.args) >= 2
             and isinstance(n.args[1], ast.Constant)}
    check("hygiene:reads_all_four_worktree_merge_keys", calls == {"worktree_merge.require_clean_tree", "worktree_merge.lock_max_attempts",
          "worktree_merge.lock_sleep_seconds", "worktree_merge.copy_fallback_on_up_to_date"}, f"calls={sorted(calls)}")
    check("hygiene:no_shell_true", "shell=True" not in src, "shell=True present")
    check("hygiene:no_reclaim_loop_without_exit", "lock_dir.exists()" in src or "exists()" in src, "reclaim must not spin when the lock dir cannot be removed")


def test_exit_criteria_dark_and_policy_shipped():
    print("\n[exit criteria: dark, policy shipped]")
    skill = (PLUGIN_ROOT / "skills" / "craftflow-router" / "SKILL.md").read_text(encoding="utf-8")
    check("dark:skill_md_does_not_call_finalize", "craftflow_worktree_finalize" not in skill, "SKILL.md already references the finalize script")
    shipped = json.loads((PLUGIN_ROOT / "config" / "policy.json").read_text(encoding="utf-8"))
    check("policy_shipped:worktree_merge", shipped.get("worktree_merge") == {"require_clean_tree": True, "lock_max_attempts": 9, "lock_sleep_seconds": 5,
                                                                          "copy_fallback_on_up_to_date": True}, f"shipped={shipped.get('worktree_merge')}")


def main():
    print("test_worktree_finalize: running")
    if fin is None:
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    for fn in (test_skipped_when_mode_null, test_path_missing_event_skip, test_branch_missing_event_skip, test_branch_starting_with_dash_is_not_run,
               test_policy_invalid_touches_nothing, test_policy_values_are_read, test_lock_contended_live_stranger,
               test_lock_read_error_holder_is_exception_class, test_git_worktree_list_error_template, test_contended_unknown_holder_is_unknown,
               test_unrecognized_decision_holder_unknown, test_corrupt_metadata_reclaims, test_self_lock_worktree_gone_reclaims,
               test_self_lock_live_worktree_waits, test_reclaim_does_not_consume_an_attempt, test_metadata_changed_between_reads_no_reclaim,
               test_lock_metadata_has_three_fields_while_held, test_dirty_main_tree_releases_lock, test_status_failure_is_dirty,
               test_real_merge_cleans_up, test_real_merge_divergent_creates_merge_commit, test_up_to_date_copy_fallback_ok_cleans_up,
               test_hyphenated_up_to_date_is_not_special_cased_e32, test_copy_fallback_failure_partial_apply_gate, test_conflict_gate_keeps_worktree, test_unrecognized_merge_failure,
               test_worktree_remove_failure, test_branch_delete_refusal, test_resume_after_locked_reenters,
               test_resume_after_cleanup_failure_completes, test_rerun_after_success_skipped, test_success_clears_only_worktree_pending_gate,
               test_artifact_corrupt_exit_3, test_root_mismatch_detail, test_no_root_mismatch_key_when_roots_agree, test_concurrent_artifact_write_flock,
               test_events_file_created_and_appended, test_release_lock_error_nonfatal, test_usage_errors_exit_2, test_no_side_effects_on_gate_outcomes,
               test_exactly_one_json_line_enum, test_every_outcome_is_exercised, test_script_hygiene, test_exit_criteria_dark_and_policy_shipped):
        try:
            fn()
        except Exception as exc:  # a crashed test is a failed test, never a silent pass
            fail(fn.__name__, f"raised {exc!r}")
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
