#!/usr/bin/env python3
"""Tests for the fail-closed gate policy loader (P4 of the router context cut, ADR-0073).

Covers config/policy.json, scripts/schemas/policy.json and scripts/craftflow_policy.py: E1-E10b (load catalog), PP-3 key
coverage (literal policy_value call sites only), DD-3 defaults against the baseline prose, the CLI, and the DD-12 host helper.

Run: python3 tests/fixtures/test_craftflow_policy.py
Prints `Results: N passed, M failed`; exits 1 on any failure.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
REPO_ROOT = PLUGIN_ROOT.parents[3]
sys.path.insert(0, str(SCRIPTS))

_passes = 0
_errors = []
_skips = []


def ok(name):
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name, reason):
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def skip(name, reason):
    """A skip is never a pass. Under CI (env CI set) a skip is a failure: the precondition must hold there."""
    if os.environ.get("CI"):
        fail(name, f"skipped under CI: {reason}")
        return
    _skips.append(f"SKIP [{name}]: {reason}")
    print(f"  SKIP: {name}: {reason}")


def check(name, cond, reason=""):
    if cond:
        ok(name)
    else:
        fail(name, reason or "assertion false")


try:
    import craftflow_policy as pol
except Exception as exc:  # RED: the module does not exist yet
    pol = None
    fail("import craftflow_policy", repr(exc))


def make_root(tmp, policy_bytes=None):
    """A plugin-root copy holding config/policy.json (or none when policy_bytes is None)."""
    root = Path(tmp)
    (root / "config").mkdir(parents=True, exist_ok=True)
    if policy_bytes is not None:
        (root / "config" / "policy.json").write_bytes(policy_bytes)
    return root


def write(tmp, obj):
    path = Path(tmp) / "p.json"
    path.write_bytes(obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8"))
    return path


def leaves(obj, prefix=""):
    out = set()
    for k, v in obj.items():
        if k == "$comment":
            continue
        if isinstance(v, dict):
            out |= leaves(v, f"{prefix}{k}.")
        else:
            out.add(f"{prefix}{k}")
    return out


def schema_leaves(props, prefix=""):
    out = set()
    for k, v in props.items():
        if isinstance(v.get("properties"), dict):
            out |= schema_leaves(v["properties"], f"{prefix}{k}.")
        else:
            out.add(f"{prefix}{k}")
    return out


def cli(args, root=None, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    if root is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(root)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(SCRIPTS / "craftflow_policy.py")] + args, capture_output=True, text=True, env=env)


def test_missing_file_defaults():
    print("\n[E1 missing file]")
    with tempfile.TemporaryDirectory() as tmp:
        policy, status, errors = pol.load_policy(Path(tmp) / "absent.json")
    check("missing_file_defaults", status == "defaults" and errors == [] and policy == pol.DEFAULTS,
          f"status={status} errors={errors}")
    policy["memory_write"] = "mutated"
    check("missing_file_defaults:returns_a_copy", pol.DEFAULTS["memory_write"] == "finalize-only", "DEFAULTS was aliased")


def test_invalid_catalog():
    print("\n[E2-E10 invalid catalog]")
    nested_ok = {"require_clean_tree": True, "lock_max_attempts": 9, "lock_sleep_seconds": 5, "copy_fallback_on_up_to_date": True}
    cases = {
        "E2_list": b"[]",
        "E3_null": b"null",
        "E4_empty": b"",
        "E4_whitespace": b"  \n",
        "E5_unknown_key": {"memory_write": "finalize-only", "surprise": 1},
        "E6_string_for_int": {"worktree_merge": dict(nested_ok, lock_sleep_seconds="5")},
        "E7_bool_for_int": {"worktree_merge": dict(nested_ok, lock_max_attempts=True)},
        "E8_zero_attempts": {"worktree_merge": dict(nested_ok, lock_max_attempts=0)},
        "E8_over_max_attempts": {"worktree_merge": dict(nested_ok, lock_max_attempts=61)},
        "E8_negative_sleep": {"worktree_merge": dict(nested_ok, lock_sleep_seconds=-1)},
        "E9_nested_key_missing": {"worktree_merge": {"require_clean_tree": True}},
        "E9_nested_unknown": {"worktree_merge": dict(nested_ok, extra=1)},
        "E10_non_utf8": b"\xff\xfe{\"memory_write\": \"finalize-only\"}",
        "E10_deep_nesting": b"[" * 30000 + b"]" * 30000,
        "E10_deep_object_nesting": b'{"a":' * 20000 + b"1" + b"}" * 20000,
        "E10_one_megabyte": b'{"$comment": "' + b"x" * (1024 * 1024) + b'"}',
        "memory_write_other_value": {"memory_write": "anything"},
        "native_plan_mode_bad_enum": {"native_plan_mode": "sometimes"},
        "plan_commit_bad_enum": {"plan_commit": "always"},
        "cap_raised_above_three": {"doubt_cycles_max": 4},
        "cap_zero": {"remediation_cycles_max": 0},
        "plan_review_passes_not_two": {"plan_review_passes_max": 3},
        "require_clean_tree_false": {"worktree_merge": dict(nested_ok, require_clean_tree=False)},
        "copy_fallback_false": {"worktree_merge": dict(nested_ok, copy_fallback_on_up_to_date=False)},
        "comment_not_string": {"$comment": 5},
    }
    for label, body in cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            policy, status, errors = pol.load_policy(write(tmp, body))
        check(f"invalid_catalog:{label}", status == "invalid" and bool(errors) and policy == pol.DEFAULTS,
              f"status={status} errors={errors}")
    with tempfile.TemporaryDirectory() as tmp:
        full = {"native_plan_mode": "allow", "plan_commit": "allow", "memory_write": "finalize-only",
                "worktree_merge": dict(nested_ok, lock_max_attempts=60, lock_sleep_seconds=0),
                "remediation_cycles_max": 1, "doubt_cycles_max": 2, "fix_verify_cycles_max": 3, "plan_review_passes_max": 2,
                "$comment": "ok"}
        policy, status, errors = pol.load_policy(write(tmp, full))
    check("invalid_catalog:full_valid_file_is_ok", status == "ok" and errors == [] and policy["native_plan_mode"] == "allow"
          and policy["worktree_merge"]["lock_max_attempts"] == 60, f"status={status} errors={errors}")


def test_bool_not_int():
    print("\n[E7 bool is not int]")
    schema = json.loads(pol.SCHEMA_PATH.read_text(encoding="utf-8"))
    errs = pol.validate_policy({"doubt_cycles_max": True}, schema)
    check("bool_not_int", bool(errs), "True was accepted as an integer cap")
    check("bool_not_int:int_accepted", pol.validate_policy({"doubt_cycles_max": 2}, schema) == [], "a plain int was rejected")


def test_unknown_key_invalid():
    print("\n[E5 unknown key]")
    schema = json.loads(pol.SCHEMA_PATH.read_text(encoding="utf-8"))
    check("unknown_key_invalid", bool(pol.validate_policy({"nope": 1}, schema)), "unknown top-level key accepted")
    check("unknown_key_invalid:comment_allowed", pol.validate_policy({"$comment": "x"}, schema) == [], "$comment rejected")


def test_absent_key_takes_default():
    print("\n[E10b absent key]")
    with tempfile.TemporaryDirectory() as tmp:
        policy, status, errors = pol.load_policy(write(tmp, {"memory_write": "finalize-only"}))
    check("absent_key_takes_default", status == "ok" and errors == [] and policy == pol.DEFAULTS, f"status={status} policy={policy}")
    with tempfile.TemporaryDirectory() as tmp:
        policy, status, _ = pol.load_policy(write(tmp, {}))
    check("absent_key_takes_default:empty_object", status == "ok" and policy["doubt_cycles_max"] == 3, f"status={status}")


def test_no_stdout_from_load():
    print("\n[load never prints]")
    for label, body in (("valid", {"memory_write": "finalize-only"}), ("invalid", b"{nope"), ("missing", None)):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gone.json" if body is None else write(tmp, body)
            buf_out, buf_err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                pol.load_policy(path)
        check(f"no_stdout_from_load:{label}", buf_out.getvalue() == "", f"stdout={buf_out.getvalue()!r}")


def test_policy_value_dotted():
    print("\n[policy_value]")
    d = pol.DEFAULTS
    check("policy_value_dotted:nested", pol.policy_value(d, "worktree_merge.lock_max_attempts") == 9, "nested leaf")
    check("policy_value_dotted:top", pol.policy_value(d, "memory_write") == "finalize-only", "top-level leaf")
    for bad in ("nope", "worktree_merge.nope", "memory_write.deeper"):
        try:
            pol.policy_value(d, bad)
            fail(f"policy_value_dotted:unknown:{bad}", "no KeyError")
        except KeyError:
            ok(f"policy_value_dotted:unknown:{bad}")


def test_policy_sha256():
    print("\n[policy_sha256]")
    import hashlib
    with tempfile.TemporaryDirectory() as tmp:
        path = write(tmp, {"memory_write": "finalize-only"})
        check("policy_sha256:file", pol.policy_sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest(), "digest mismatch")
        check("policy_sha256:missing_is_defaults", pol.policy_sha256(Path(tmp) / "none.json") == "defaults", "missing file")


def test_enter_plan_mode_decision():
    print("\n[enter_plan_mode_decision]")
    f = pol.enter_plan_mode_decision
    check("enter_plan_mode:invalid_denies", f("allow", "invalid")[0] == "deny" and f("allow", "invalid")[1].startswith("craftflow-policy: policy-invalid"),
          str(f("allow", "invalid")))
    check("enter_plan_mode:never_denies", f("never", "ok")[0] == "deny" and f("never", "defaults")[1].startswith("craftflow-policy: native_plan_mode=never"),
          str(f("never", "ok")))
    check("enter_plan_mode:allow_passes", f("allow", "ok") == ("allow", ""), str(f("allow", "ok")))


def test_cli_validate_json_and_exit():
    print("\n[CLI --validate]")
    with tempfile.TemporaryDirectory() as tmp:
        shipped = (PLUGIN_ROOT / "config" / "policy.json").read_bytes()
        good = cli(["--validate"], make_root(tmp + "/good", shipped))
        out = json.loads(good.stdout) if good.stdout.strip() else {}
        check("cli_validate:ok", good.returncode == 0 and out.get("status") == "ok" and out.get("errors") == []
              and re.fullmatch(r"[0-9a-f]{64}", str(out.get("sha256"))) is not None and sorted(out) == ["errors", "sha256", "status"],
              f"rc={good.returncode} out={good.stdout!r} err={good.stderr!r}")
        none = cli(["--validate"], make_root(tmp + "/none"))
        out = json.loads(none.stdout) if none.stdout.strip() else {}
        check("cli_validate:defaults_exit_0", none.returncode == 0 and out.get("status") == "defaults", f"rc={none.returncode} out={none.stdout!r}")
        bad = cli(["--validate"], make_root(tmp + "/bad", b"{corrupt"))
        out = json.loads(bad.stdout) if bad.stdout.strip() else {}
        check("cli_validate:invalid_exit_1", bad.returncode == 1 and out.get("status") == "invalid" and out.get("errors"),
              f"rc={bad.returncode} out={bad.stdout!r}")
        nofallback = cli(["--validate"])  # no CLAUDE_PLUGIN_ROOT: falls back to this file's plugin root
        out = json.loads(nofallback.stdout) if nofallback.stdout.strip() else {}
        check("cli_validate:file_fallback_root", nofallback.returncode == 0 and out.get("status") == "ok",
              f"rc={nofallback.returncode} out={nofallback.stdout!r} err={nofallback.stderr!r}")


def test_cli_get_and_hash():
    print("\n[CLI --get / --hash]")
    got = cli(["--get", "worktree_merge.lock_sleep_seconds"])
    check("cli_get:dotted", got.returncode == 0 and got.stdout.strip() == "5", f"rc={got.returncode} out={got.stdout!r}")
    got = cli(["--get", "memory_write"])
    check("cli_get:string_value", got.returncode == 0 and got.stdout.strip() == "finalize-only", f"out={got.stdout!r}")
    bad = cli(["--get", "nope"])
    check("cli_get:unknown_key_exit_1", bad.returncode == 1, f"rc={bad.returncode}")
    digest = cli(["--hash"])
    check("cli_hash", digest.returncode == 0 and re.fullmatch(r"[0-9a-f]{64}", digest.stdout.strip()) is not None, f"out={digest.stdout!r}")


def test_print_table_matches_defaults():
    print("\n[--print-table]")
    res = cli(["--print-table"])
    rows = {}
    for line in res.stdout.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0].startswith("`"):
            rows[cells[0].strip("`")] = cells[1]
    want = {k: json.dumps(pol.policy_value(pol.DEFAULTS, k)) for k in leaves(pol.DEFAULTS)}
    got = {k: v.strip("`") for k, v in rows.items()}
    check("print_table_matches_defaults", res.returncode == 0 and got == want, f"rc={res.returncode} got={got} want={want}")


def test_defaults_equal_prose_values():
    print("\n[A7 defaults equal prose at BASELINE_SHA]")
    budget = json.loads((PLUGIN_ROOT / "tests" / "fixtures" / "router-budget.json").read_text(encoding="utf-8"))
    sha = budget["baseline_ref"]
    rel = PLUGIN_ROOT.relative_to(REPO_ROOT).as_posix()

    def show(path):
        res = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"{sha}:{rel}/{path}"], capture_output=True)
        return res.stdout.decode("utf-8") if res.returncode == 0 else None

    kernel = show("skills/craftflow-router/SKILL.md")
    plan = show("skills/craftflow-router/references/plan-workflow.md")
    remfix = show("skills/craftflow-router/references/remediation-and-research.md")
    if kernel is None or plan is None or remfix is None:
        skip("defaults_equal_prose_values", "baseline ref unavailable (shallow clone?)")
        return
    D = pol.DEFAULTS

    def after(text, anchor, pattern):
        i = text.find(anchor)
        m = re.search(pattern, text[i:]) if i >= 0 else None
        return int(m.group(1)) if m else None

    got = {
        "doubt_cycles_max": after(kernel, "Doubt cycle counter", r"Max (\d+) cycles; router enforces the hard stop"),
        "fix_verify_cycles_max": after(kernel, "Fix-verify cycle counter", r"Max (\d+) cycles; router enforces the hard stop"),
        "plan_review_passes_max": after(plan, "Maximum fresh-review passes", r"Maximum fresh-review passes: (\d+)"),
        "worktree_merge.lock_max_attempts": after(kernel, "Acquire the merge lock", r"MAX_ATTEMPTS=(\d+)"),
        "worktree_merge.lock_sleep_seconds": after(kernel, "ATTEMPT=$((ATTEMPT + 1))", r"sleep (\d+)"),
    }
    m = re.search(r"more than (\d+) cycles without a human checkpoint", kernel)
    got["remediation_cycles_max"] = int(m.group(1)) if m else None
    m2 = re.search(r"remfix_count >= (\d+)", remfix)
    for key, value in got.items():
        check(f"defaults_equal_prose_values:{key}", value is not None and value == pol.policy_value(D, key),
              f"prose={value} default={pol.policy_value(D, key)}")
    check("defaults_equal_prose_values:remfix_breaker", m2 is not None and int(m2.group(1)) == D["remediation_cycles_max"],
          f"prose={m2.group(1) if m2 else None}")


def test_load_is_total_on_hostile_files():
    print("\n[load_policy is total: unreadable dir, directory, dangling symlink, deep nesting, long echoes]")
    import hashlib
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "dir-as-file"
        d.mkdir()
        policy, status, errors = pol.load_policy(d)
        check("total:directory_in_place_of_file_is_invalid", status == "invalid" and bool(errors) and policy == pol.DEFAULTS, f"status={status} errors={errors}")
        link = Path(tmp) / "dangling.json"
        link.symlink_to(Path(tmp) / "nowhere.json")
        policy, status, errors = pol.load_policy(link)
        check("total:dangling_symlink_is_defaults", status == "defaults" and errors == [], f"status={status} errors={errors}")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        skip("total:unreadable_parent_dir", "running as root; chmod 000 does not restrict root")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config"
            cfg.mkdir()
            (cfg / "policy.json").write_text("{}", encoding="utf-8")
            os.chmod(cfg, 0)
            try:
                policy, status, errors = pol.load_policy(cfg / "policy.json")
            except Exception as exc:  # the loader must never raise
                policy, status, errors = None, f"RAISED {exc!r}", []
            finally:
                os.chmod(cfg, 0o700)
            check("total:unreadable_parent_dir_is_invalid", status == "invalid" and bool(errors) and policy == pol.DEFAULTS, f"status={status} errors={errors}")
    with tempfile.TemporaryDirectory() as tmp:
        deep = write(tmp, b"[" * 30000 + b"]" * 30000)
        try:
            policy, status, errors = pol.load_policy(deep)
        except BaseException as exc:
            policy, status, errors = None, f"RAISED {exc!r}", []
        check("total:deep_nesting_does_not_raise", status == "invalid" and len(errors) == 1 and len(errors[0]) <= 120 and policy == pol.DEFAULTS,
              f"status={status} errors={errors}")
        long_key = write(tmp, {"k\u00e9y" * 2000: "v" * 5000})
        _, status, errors = pol.load_policy(long_key)
        check("total:error_echoes_are_short_and_ascii", status == "invalid" and errors and all(len(e) <= 120 and e.isascii() for e in errors),
              f"status={status} lens={[len(e) for e in errors]}")
        long_val = write(tmp, {"memory_write": "x" * 5000})
        _, status, errors = pol.load_policy(long_val)
        check("total:long_value_echo_is_clipped", status == "invalid" and errors and all(len(e) <= 120 for e in errors), f"lens={[len(e) for e in errors]}")
        good = write(tmp, {"memory_write": "finalize-only"})
        _, _, _, sha = pol.load_policy_with_sha(good)
        check("total:sha_comes_from_the_validated_bytes", sha == hashlib.sha256(good.read_bytes()).hexdigest(), sha)
        _, st, _, sha = pol.load_policy_with_sha(Path(tmp) / "none.json")
        check("total:sha_defaults_when_missing", st == "defaults" and sha == "defaults", f"{st} {sha}")
        _, st, _, sha = pol.load_policy_with_sha(write(tmp, b"{nope"))
        check("total:sha_of_invalid_file_bytes", st == "invalid" and sha == hashlib.sha256(b"{nope").hexdigest(), f"{st} {sha}")


def test_cli_validate_deep_nesting():
    print("\n[CLI --validate on a deeply nested file]")
    with tempfile.TemporaryDirectory() as tmp:
        res = cli(["--validate"], make_root(tmp, b"[" * 30000 + b"]" * 30000))
        out = json.loads(res.stdout) if res.stdout.strip() else {}
        check("cli_validate:deep_nesting_exit_1_json_no_traceback", res.returncode == 1 and out.get("status") == "invalid"
              and "Traceback" not in res.stderr, f"rc={res.returncode} out={res.stdout[:200]!r} err={res.stderr[-200:]!r}")


def test_log_invalid_retries_after_failed_write():
    print("\n[_log_invalid retries after a REAL failed write (read-only .craftflow)]")
    import craftflow_hooklib as hl
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        skip("log_invalid:real_write_failure", "running as root; chmod 500 does not restrict root")
        return
    real_dir, real_flag = hl.project_dir, pol._logged_invalid
    with tempfile.TemporaryDirectory() as tmp:
        cf = Path(tmp) / ".craftflow"
        cf.mkdir()
        log = cf / "state" / "craftflow-hook-events.log"
        try:
            hl.project_dir = lambda: Path(tmp)
            pol._logged_invalid = False
            os.chmod(cf, 0o500)
            try:
                pol._log_invalid(Path("p.json"), ["e"])
                failed_flag = pol._logged_invalid
            finally:
                os.chmod(cf, 0o700)
            pol._log_invalid(Path("p.json"), ["e"])
            pol._log_invalid(Path("p.json"), ["e"])
            events = [json.loads(x)["event"] for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        finally:
            hl.project_dir, pol._logged_invalid = real_dir, real_flag
    check("log_invalid:failed_write_leaves_flag_unset_then_retries_once", failed_flag is False and events == ["policy_invalid"],
          f"flag_after_failure={failed_flag} events={events}")


def test_log_event_reports_outcome():
    print("\n[hooklib.log_event returns True on a written line, False on a failed one, never raises]")
    import craftflow_hooklib as hl
    real_dir = hl.project_dir
    with tempfile.TemporaryDirectory() as good, tempfile.TemporaryDirectory() as bad:
        (Path(bad) / ".craftflow").write_text("a file where the state root's parent should be", encoding="utf-8")
        try:
            hl.project_dir = lambda: Path(good)
            wrote = hl.log_event("probe", {})
            hl.project_dir = lambda: Path(bad)
            failed = hl.log_event("probe", {})
        finally:
            hl.project_dir = real_dir
    check("log_event:returns_true_on_success", wrote is True, f"wrote={wrote!r}")
    check("log_event:returns_false_on_failure", failed is False, f"failed={failed!r}")


def test_hooklib_stub_on_broken_policy_module():
    print("\n[hooklib degrades to the stub when craftflow_policy.py is broken]")
    with tempfile.TemporaryDirectory() as tmp:
        scripts = Path(tmp) / "scripts"
        shutil.copytree(SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
        for label, body in (("syntax_error", "def broken(:\n"), ("runtime_error", "raise RuntimeError('boom')\n")):
            (scripts / "craftflow_policy.py").write_text(body, encoding="utf-8")
            code = ("import sys, json; sys.path.insert(0, %r); import craftflow_hooklib as h; "
                    "p, s, e = h.load_policy(); print(json.dumps([p.get('memory_write'), s, e]))" % str(scripts))
            res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
            out = json.loads(res.stdout) if res.returncode == 0 else []
            want = {"syntax_error": "import-error: SyntaxError: ", "runtime_error": "import-error: RuntimeError: boom"}[label]
            good = res.returncode == 0 and out[:2] == ["finalize-only", "invalid"] and len(out[2]) == 1 and out[2][0].startswith(want) and len(out[2][0]) <= 120
            check(f"hooklib_stub:{label}", good, f"rc={res.returncode} out={res.stdout!r} err={res.stderr[-300:]!r}")


def test_is_cursor_host_true_only_when_set():
    print("\n[DD-12 is_cursor_host]")
    import craftflow_hooklib as hl
    check("is_cursor_host:set", hl.is_cursor_host({"CURSOR_PLUGIN_ROOT": "/x"}) is True, "set -> True")
    check("is_cursor_host:unset", hl.is_cursor_host({}) is False, "unset -> False")
    check("is_cursor_host:empty", hl.is_cursor_host({"CURSOR_PLUGIN_ROOT": ""}) is False, "empty -> False")
    saved = os.environ.get("CURSOR_PLUGIN_ROOT")
    try:
        os.environ["CURSOR_PLUGIN_ROOT"] = "/cursor"
        check("is_cursor_host:default_reads_environ", hl.is_cursor_host() is True and hl.is_cursor_host({}) is False, "environ default")
        os.environ.pop("CURSOR_PLUGIN_ROOT")
        check("is_cursor_host:default_unset", hl.is_cursor_host() is False, "unset environ -> False")
    finally:
        if saved is None:
            os.environ.pop("CURSOR_PLUGIN_ROOT", None)
        else:
            os.environ["CURSOR_PLUGIN_ROOT"] = saved


def hooklib_stub_returns_invalid():
    """The ImportError fallback in hooklib returns (defaults copy, "invalid", ["import-error: <type>: <message>"]); load it with the policy module hidden."""
    code = ("import sys; sys.modules['craftflow_policy'] = None; sys.path.insert(0, %r); import craftflow_hooklib as h; "
            "import json; p, s, e = h.load_policy(); print(json.dumps([p.get('memory_write'), s, e]))" % str(SCRIPTS))
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    out = json.loads(res.stdout) if res.returncode == 0 else []
    return res.returncode == 0 and out[:2] == ["finalize-only", "invalid"] and len(out[2]) == 1 and out[2][0].startswith("import-error"), res


def test_hooklib_reexport():
    print("\n[hooklib re-export]")
    import craftflow_hooklib as hl
    check("hooklib_reexport:same_functions", hl.load_policy is pol.load_policy and hl.policy_value is pol.policy_value, "not re-exported")
    good, res = hooklib_stub_returns_invalid()
    check("hooklib_reexport:import_error_stub", good, f"rc={res.returncode} out={res.stdout!r} err={res.stderr[-300:]!r}")


def test_unreadable_policy_sha_is_not_defaults():
    print("\n[M2 sha sentinel: 'defaults' only when missing; 'unreadable' when no bytes were read]")
    import hashlib
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "dir-as-file"
        d.mkdir()
        _, st, _, sha = pol.load_policy_with_sha(d)
        check("sha:directory_is_unreadable", st == "invalid" and sha == "unreadable", f"{st} {sha}")
        loop = Path(tmp) / "loop.json"
        loop.symlink_to(loop)
        _, st, _, sha = pol.load_policy_with_sha(loop)
        check("sha:symlink_loop_is_unreadable", st == "invalid" and sha == "unreadable", f"{st} {sha}")
        _, st, _, sha = pol.load_policy_with_sha(Path(tmp) / "none.json")
        check("sha:missing_stays_defaults", st == "defaults" and sha == "defaults", f"{st} {sha}")
        check("sha:policy_sha256_directory_is_unreadable", pol.policy_sha256(d) == "unreadable", pol.policy_sha256(d))
        check("sha:policy_sha256_missing_is_defaults", pol.policy_sha256(Path(tmp) / "none.json") == "defaults", "missing")
        bad = write(tmp, b"{nope")
        check("sha:invalid_content_keeps_its_hash", pol.load_policy_with_sha(bad)[3] == hashlib.sha256(b"{nope").hexdigest(), "hash")
        root = make_root(Path(tmp) / "r")
        (root / "config" / "policy.json").mkdir()
        res = cli(["--hash"], root)
        check("sha:cli_hash_directory_is_unreadable", res.returncode == 0 and res.stdout.strip() == "unreadable", f"rc={res.returncode} out={res.stdout!r}")
        res = cli(["--validate"], root)
        out = json.loads(res.stdout) if res.stdout.strip() else {}
        check("sha:cli_validate_directory_is_unreadable", res.returncode == 1 and out.get("sha256") == "unreadable", f"out={res.stdout!r}")


def _load_with_timeout(path, seconds=10):
    code = ("import sys, json; sys.path.insert(0, %r); import craftflow_policy as p; "
            "pol, st, er = p.load_policy(sys.argv[1]); print(json.dumps([st, er, pol == p.DEFAULTS]))" % str(SCRIPTS))
    return subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=seconds)


def test_load_reads_only_regular_files():
    print("\n[L3 non-regular policy.json: FIFO and device are invalid, never a hang]")
    with tempfile.TemporaryDirectory() as tmp:
        if hasattr(os, "mkfifo"):
            fifo = Path(tmp) / "policy.json"
            os.mkfifo(fifo)
            try:
                res = _load_with_timeout(fifo)
                out = json.loads(res.stdout) if res.returncode == 0 else []
                check("regular_only:fifo_is_invalid_not_a_hang", len(out) == 3 and out[0] == "invalid" and out[2] is True
                      and "not a regular file" in " ".join(out[1]), f"rc={res.returncode} out={res.stdout!r} err={res.stderr[-200:]!r}")
            except subprocess.TimeoutExpired:
                fail("regular_only:fifo_is_invalid_not_a_hang", "load_policy hung on a FIFO (10 s)")
        else:
            skip("regular_only:fifo", "os.mkfifo unavailable")
        if Path("/dev/null").exists():
            link = Path(tmp) / "null.json"
            link.symlink_to("/dev/null")
            _, status, errors = pol.load_policy(link)
            check("regular_only:device_is_invalid", status == "invalid" and "not a regular file" in " ".join(errors), f"{status} {errors}")
        if Path("/dev/zero").exists():
            link = Path(tmp) / "zero.json"
            link.symlink_to("/dev/zero")
            try:
                res = _load_with_timeout(link)
                out = json.loads(res.stdout) if res.returncode == 0 else []
                check("regular_only:dev_zero_rejected_before_any_read", len(out) == 3 and out[0] == "invalid", f"rc={res.returncode} out={res.stdout[:200]!r}")
            except subprocess.TimeoutExpired:
                fail("regular_only:dev_zero_rejected_before_any_read", "load_policy did not return on /dev/zero (10 s)")
        big = Path(tmp) / "big.json"
        big.write_bytes(b" " * (pol.MAX_POLICY_BYTES + 5))
        _, status, errors = pol.load_policy(big)
        check("regular_only:oversized_regular_file_still_invalid", status == "invalid" and "byte limit" in " ".join(errors), f"{status} {errors}")


def test_cli_get_on_invalid_policy_reports_first_error():
    print("\n[L5 --get on an invalid policy: error on stderr, exit 0 and default value on stdout unchanged]")
    with tempfile.TemporaryDirectory() as tmp:
        res = cli(["--get", "memory_write"], make_root(tmp, b"{corrupt"))
    check("cli_get:invalid_policy_warns_on_stderr", res.returncode == 0 and res.stdout.strip() == "finalize-only"
          and res.stderr.startswith("craftflow_policy: policy.json invalid: ") and "policy: cannot load policy.json" in res.stderr,
          f"rc={res.returncode} out={res.stdout!r} err={res.stderr!r}")
    with tempfile.TemporaryDirectory() as tmp:
        quiet = cli(["--get", "memory_write"], make_root(tmp, b'{"memory_write": "finalize-only"}'))
        missing = cli(["--get", "memory_write"], make_root(tmp + "/m"))
    check("cli_get:valid_or_missing_policy_stays_silent", quiet.stderr == "" and missing.stderr == "", f"{quiet.stderr!r} {missing.stderr!r}")


def test_hooklib_stub_logs_policy_invalid():
    print("\n[L4 stub logs one best-effort policy_invalid without importing the broken module]")
    with tempfile.TemporaryDirectory() as tmp:
        scripts = Path(tmp) / "scripts"
        shutil.copytree(SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
        (scripts / "craftflow_policy.py").write_text("def broken(:\n", encoding="utf-8")
        proj = Path(tmp) / "proj"
        (proj / ".craftflow").mkdir(parents=True)
        code = ("import sys; sys.path.insert(0, %r); import craftflow_hooklib as h; h.load_policy(); h.load_policy()" % str(scripts))
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(proj)}
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
        log = proj / ".craftflow" / "state" / "craftflow-hook-events.log"
        events = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        invalid = [e for e in events if e.get("event") == "policy_invalid"]
        check("hooklib_stub:logs_policy_invalid_once", res.returncode == 0 and len(invalid) == 1
              and invalid[0]["errors"][0].startswith("import-error: SyntaxError: "), f"rc={res.returncode} events={events} err={res.stderr[-200:]!r}")
        bare = Path(tmp) / "bare"
        bare.mkdir()
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "CLAUDE_PROJECT_DIR": str(bare)})
        check("hooklib_stub:no_state_dir_created_outside_a_project", res.returncode == 0 and not (bare / ".craftflow").exists(), f"rc={res.returncode}")


# ---- PP-3 key coverage: only literal policy_value(<expr>, "<dotted key>") call sites count -------------------------------

def covered_keys(scripts_dir):
    found = set()
    for path in sorted(Path(scripts_dir).glob("*.py")):
        if path.name == "craftflow_policy.py" or "unit_tests" in path.name:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
            second = node.args[1]
            if name == "policy_value" and isinstance(second, ast.Constant) and isinstance(second.value, str):
                found.add(second.value)
    return found


def test_key_coverage():
    print("\n[PP-3 key coverage]")
    shipped = json.loads((PLUGIN_ROOT / "config" / "policy.json").read_text(encoding="utf-8"))
    missing = sorted(leaves(shipped) - covered_keys(SCRIPTS))
    check("key_coverage", not missing, f"shipped keys without a literal policy_value reader: {missing}")


def test_key_coverage_ignores_dict_literals_and_stub():
    print("\n[PP-3 negative: dict literals, stub, comments]")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "craftflow_hooklib.py").write_text(
            "try:\n    from craftflow_policy import load_policy, policy_value\n"
            "except ImportError:\n    _D = {'plan_commit': 'never'}\n"
            "    def load_policy(path=None):\n        return dict(_D), 'invalid', ['import-error']\n"
            "    def policy_value(policy, dotted):\n        return policy[dotted]\n"
            "# policy_value(policy, \"plan_commit\") is only a comment\n"
            "DOC = 'policy_value(policy, \"plan_commit\")'\n", encoding="utf-8")
        covered = covered_keys(tmp)
    missing = sorted({"plan_commit"} - covered)
    check("key_coverage_ignores_dict_literals_and_stub", missing == ["plan_commit"], f"covered={sorted(covered)}")


def test_key_coverage_requires_literal_key():
    print("\n[PP-3 negative: non-literal key]")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "craftflow_gate.py").write_text(
            "from craftflow_policy import policy_value\n\ndef run(policy, args):\n    return policy_value(policy, args.gate)\n",
            encoding="utf-8")
        covered = covered_keys(tmp)
    check("key_coverage_requires_literal_key", covered == set(), f"covered={sorted(covered)}")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "craftflow_gate.py").write_text(
            "from craftflow_policy import policy_value\n\ndef run(policy):\n    return policy_value(policy, 'plan_commit')\n",
            encoding="utf-8")
        check("key_coverage_requires_literal_key:literal_counts", covered_keys(tmp) == {"plan_commit"}, "literal call not counted")


def test_schema_leaf_keys_equal_defaults():
    print("\n[schema leaves == DEFAULTS leaves]")
    schema = json.loads(pol.SCHEMA_PATH.read_text(encoding="utf-8"))
    check("schema_leaf_keys_equal_defaults", schema_leaves(schema["properties"]) == leaves(pol.DEFAULTS),
          f"schema={sorted(schema_leaves(schema['properties']))} defaults={sorted(leaves(pol.DEFAULTS))}")
    check("schema_leaf_keys_equal_defaults:closed", schema.get("additionalProperties") is False
          and schema["properties"]["worktree_merge"].get("additionalProperties") is False, "additionalProperties must be false")


def test_shipped_policy_is_exactly_p4_keys():
    print("\n[shipped config/policy.json]")
    shipped = json.loads((PLUGIN_ROOT / "config" / "policy.json").read_text(encoding="utf-8"))
    check("shipped_policy_only_memory_write", leaves(shipped) == {"memory_write"} and shipped["memory_write"] == "finalize-only"
          and shipped.get("$comment") == "craftflow gate policy; values mirror router prose (ADR-0073)", str(shipped))
    _, status, errors = pol.load_policy(PLUGIN_ROOT / "config" / "policy.json")
    check("shipped_policy_valid", status == "ok" and errors == [], f"status={status} errors={errors}")


def test_py39_compile():
    print("\n[py39]")
    src = (SCRIPTS / "craftflow_policy.py").read_text(encoding="utf-8")
    try:
        ast.parse(src, feature_version=(3, 9))
        ok("py39_compile")
    except SyntaxError as exc:
        fail("py39_compile", repr(exc))
    check("py39_compile:line_budget", len(src.splitlines()) <= 300, f"{len(src.splitlines())} lines")



def main():
    print("test_craftflow_policy: running")
    if pol is None:
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    for fn in (test_missing_file_defaults, test_invalid_catalog, test_bool_not_int, test_unknown_key_invalid,
               test_absent_key_takes_default, test_no_stdout_from_load, test_policy_value_dotted, test_policy_sha256,
               test_enter_plan_mode_decision, test_cli_validate_json_and_exit, test_cli_get_and_hash,
               test_print_table_matches_defaults, test_defaults_equal_prose_values, test_is_cursor_host_true_only_when_set,
               test_hooklib_reexport, test_load_is_total_on_hostile_files, test_cli_validate_deep_nesting,
               test_log_invalid_retries_after_failed_write, test_log_event_reports_outcome, test_hooklib_stub_on_broken_policy_module,
               test_unreadable_policy_sha_is_not_defaults, test_load_reads_only_regular_files, test_cli_get_on_invalid_policy_reports_first_error, test_hooklib_stub_logs_policy_invalid, test_key_coverage, test_key_coverage_ignores_dict_literals_and_stub,
               test_key_coverage_requires_literal_key, test_schema_leaf_keys_equal_defaults,
               test_shipped_policy_is_exactly_p4_keys, test_py39_compile):
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
    print(f"Results: {_passes} passed, 0 failed, {len(_skips)} skipped")
    for line in _skips:
        print(line)
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
