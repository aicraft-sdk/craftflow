#!/usr/bin/env python3
"""Tests for the cloud harness memory-store Edit/Write grant (ADR 0084).

The cloud harness mounts a project-memory folder outside the working directory and names it in the Claude Code
process environment. The PreToolUse guard lets Edit/Write reach `.md` files directly inside a writable store and
nothing else.

Run from anywhere: python3 tools/craftflow-plugin/plugins/craftflow/tests/fixtures/test_craftflow_memory_store.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "scripts"))
import craftflow_memory_store as store_mod  # noqa: E402
import craftflow_pretooluse_guard as guard  # noqa: E402

FAILS = []
ENV_KEYS = ("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", "CLAUDE_MEMORY_STORES")


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


def fixture(tmp: Path, mode: str = "rw", mount: str = "silo"):
    """<root>/team/<mount>/ plus the two env vars that name it. Returns (env, project, store_dir)."""
    root = tmp / "harness_memory"
    store = root / "team" / mount
    store.mkdir(parents=True)
    project = tmp / "proj"
    project.mkdir()
    env = {ENV_KEYS[0]: str(root.resolve()),
           ENV_KEYS[1]: json.dumps([{"path": "/v1/x", "mount": mount, "mode": mode}])}
    return env, project.resolve(), store.resolve()


def escapes(env: dict, cwd: Path, target) -> bool:
    """Run the guard's Edit/Write confinement check with `env` as the process environment."""
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(env)
    try:
        data = {"tool_name": "Write", "cwd": str(cwd), "tool_input": {"file_path": str(target)}}
        return guard._edit_write_escapes_confinement(data, Path(target))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="memstore-"))


print("[store discovery]")
env, _, store = fixture(tmpdir())
check("a rw store resolves to its directory", store_mod.harness_memory_store_dirs(env) == [store],
      store_mod.harness_memory_store_dirs(env))
flat_tmp = tmpdir()
flat_store = flat_tmp / "root" / "silo"
flat_store.mkdir(parents=True)
flat_env = {ENV_KEYS[0]: str((flat_tmp / "root").resolve()), ENV_KEYS[1]: json.dumps([{"mount": "silo", "mode": "rw"}])}
check("a store directly under the root is found too", store_mod.harness_memory_store_dirs(flat_env) == [flat_store.resolve()])
ro_env, _, _ = fixture(tmpdir(), mode="ro")
bad_env, _, _ = fixture(tmpdir())
bad_env[ENV_KEYS[1]] = json.dumps([{"mount": "../etc", "mode": "rw"}])
link_tmp = tmpdir()
os.symlink(env[ENV_KEYS[0]], link_tmp / "link_root")
os.symlink(Path(env[ENV_KEYS[0]]).parent, link_tmp / "link_parent")
for label, case in (
    ("no env", {}),
    ("read-only store", ro_env),
    ("traversal mount", bad_env),
    ("relative root", dict(env, **{ENV_KEYS[0]: "relative/path"})),
    ("symlinked root", dict(env, **{ENV_KEYS[0]: str(link_tmp / "link_root")})),
    ("a root under a symlinked parent", dict(env, **{ENV_KEYS[0]: str(link_tmp / "link_parent" / "harness_memory")})),
    ("malformed stores json", dict(env, **{ENV_KEYS[1]: "{not json"})),
    ("stores not a list", dict(env, **{ENV_KEYS[1]: json.dumps({"mount": "silo"})})),
):
    got = store_mod.harness_memory_store_dirs(case)
    check(f"no store for {label}", got == [], got)

print("[guard: allowed]")
env, project, store = fixture(tmpdir())
for target in (store / "MEMORY.md", store / "new-fact.md"):
    check(f"Write to {target.name} in the store is allowed", not escapes(env, project, target))
check("an in-cwd write is unaffected", not escapes(env, project, project / "x.txt"))

print("[guard: denied]")
tmp = tmpdir()
env, project, store = fixture(tmp)
outside = tmp / "outside"
outside.mkdir()
os.symlink(outside, store / "link")
os.symlink(outside / "x.md", store / "linked.md")
ro_env, _, ro_store = fixture(tmp / "ro", mode="ro")
for label, case_env, target in (
    ("a non-markdown file", env, store / "script.py"),
    ("a nested subdirectory", env, store / "sub" / "x.md"),
    ("the store's parent", env, store.parent / "x.md"),
    ("traversal out of the store", env, store / ".." / ".." / "x.md"),
    ("a symlinked directory escape", env, store / "link" / "x.md"),
    ("a symlinked file escape", env, store / "linked.md"),
    ("a read-only store", ro_env, ro_store / "x.md"),
    ("an unrelated path", env, tmp / "elsewhere.md"),
    ("the store with no env set", {}, store / "x.md"),
):
    check(f"Write to {label} is denied", escapes(case_env, project, target))

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
