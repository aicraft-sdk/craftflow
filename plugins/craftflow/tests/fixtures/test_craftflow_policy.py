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
        ok("skip: no baseline ref")
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
    """The ImportError fallback in hooklib returns (defaults copy, "invalid", ["import-error"]); load it with the policy module hidden."""
    code = ("import sys; sys.modules['craftflow_policy'] = None; sys.path.insert(0, %r); import craftflow_hooklib as h; "
            "import json; p, s, e = h.load_policy(); print(json.dumps([p.get('memory_write'), s, e]))" % str(SCRIPTS))
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return res.returncode == 0 and json.loads(res.stdout) == ["finalize-only", "invalid", ["import-error"]], res


def test_hooklib_reexport():
    print("\n[hooklib re-export]")
    import craftflow_hooklib as hl
    check("hooklib_reexport:same_functions", hl.load_policy is pol.load_policy and hl.policy_value is pol.policy_value, "not re-exported")
    good, res = hooklib_stub_returns_invalid()
    check("hooklib_reexport:import_error_stub", good, f"rc={res.returncode} out={res.stdout!r} err={res.stderr[-300:]!r}")


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
               test_hooklib_reexport, test_key_coverage, test_key_coverage_ignores_dict_literals_and_stub,
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
    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
