#!/usr/bin/env python3
"""Tests for scripts/craftflow_baseline_integrity.py, using throwaway git repositories.

Run from anywhere:
    python3 tests/fixtures/test_craftflow_baseline_integrity.py
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import craftflow_baseline_integrity as bi

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "craftflow_baseline_integrity.py"
REPO = PLUGIN.parents[1]
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
PASS = 0
FAIL = 0

SUPP = {"version": 1, "files": {"a.py": {"noqa:E402": 2, "unexplained": 1}, "b.py": {"noqa:BLE001": 5}}}
SIZE = {"version": 1, "limit": 500, "files": {"big.py": 900, "bigger.py": 1200}}
HOOK = {"protectedWrites": "block", "contextNudge": "audit"}
RUFF = """# header comment with select = ["X"] inside it
target-version = "py39"

[lint]
select = [
    "E402",   # late import
    "F401",
]
external = ["BLE001", "D401"]

[lint.per-file-ignores]
# comment line
"pkg/one.py" = ["S602"]
"pkg/two.py" = ["E402", "E731"]
"""


def check(name, actual, expected):
    global PASS, FAIL
    if actual == expected:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}\n    expected: {expected!r}\n    actual:   {actual!r}")
        FAIL += 1


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=GIT_ENV, check=True)


def write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if isinstance(text, str) else json.dumps(text, indent=2), encoding="utf-8")


def make_repo(files=None, branch="main"):
    """A repo whose base commit holds `files` (default: all four guarded files). Working-tree edits are uncommitted."""
    root = Path(tempfile.mkdtemp(prefix="bi-test-"))
    git(root, "init", "-q", "-b", branch)
    files = {bi.SUPPRESSION_REL: SUPP, bi.FILE_SIZE_REL: SIZE, bi.RUFF_REL: RUFF, bi.HOOK_MODE_REL: HOOK} if files is None else files
    write(root, "README", "x")
    for rel, text in files.items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


def run(root, *args):
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--root", str(root), *args], capture_output=True, text=True, env=GIT_ENV)
    return proc.returncode, proc.stdout + proc.stderr


def edited(base, mutate):
    data = copy.deepcopy(base)
    mutate(data)
    return data


print("[unchanged and lowered]")
root = make_repo()
code, out = run(root, "--base-ref", "main")
check("unchanged tree passes", code, 0)
check("default base resolution finds `main` without a remote", run(root)[0], 0)
write(root, bi.SUPPRESSION_REL, edited(SUPP, lambda d: (d["files"]["a.py"].update({"noqa:E402": 1}), d["files"].pop("b.py"))))
write(root, bi.FILE_SIZE_REL, edited(SIZE, lambda d: d["files"].update({"big.py": 800}) or d["files"].pop("bigger.py")))
write(root, bi.RUFF_REL, RUFF.replace('"pkg/two.py" = ["E402", "E731"]', '"pkg/two.py" = ["E402"]').replace(', "D401"', ""))
write(root, bi.HOOK_MODE_REL, edited(HOOK, lambda d: d.update({"contextNudge": "block", "newKey": "audit"})))
code, out = run(root, "--base-ref", "main")
check("lowered/removed entries and a stricter hook mode pass", code, 0)

print("[suppression and file-size baselines]")
root = make_repo()
write(root, bi.SUPPRESSION_REL, edited(SUPP, lambda d: d["files"].update({"c.py": {"noqa:E402": 1}})))
code, out = run(root, "--base-ref", "main")
check("suppression entry added -> 2", code, 2)
check("message names the entry", "entry added: files / c.py / noqa:E402 = 1" in out, True)
check("message says baselines only shrink and a human must approve", "Baselines only shrink" in out and "human must approve" in out, True)
write(root, bi.SUPPRESSION_REL, SUPP)
write(root, bi.SUPPRESSION_REL, edited(SUPP, lambda d: d["files"]["a.py"].update({"noqa:E402": 3})))
check("suppression count raised -> 2", run(root, "--base-ref", "main")[0], 2)
write(root, bi.SUPPRESSION_REL, SUPP)
write(root, bi.FILE_SIZE_REL, edited(SIZE, lambda d: d["files"].update({"big.py": 901})))
code, out = run(root, "--base-ref", "main")
check("file-size count raised -> 2", code, 2)
write(root, bi.FILE_SIZE_REL, edited(SIZE, lambda d: d["files"].update({"newbig.py": 600})))
check("file-size entry added -> 2", run(root, "--base-ref", "main")[0], 2)
write(root, bi.FILE_SIZE_REL, edited(SIZE, lambda d: d.update({"limit": 800})))
check("file-size limit raised -> 2", run(root, "--base-ref", "main")[0], 2)
write(root, bi.FILE_SIZE_REL, "{not json")
check("corrupt baseline -> 1 (tool error, not a verdict)", run(root, "--base-ref", "main")[0], 1)
(root / bi.FILE_SIZE_REL).unlink()
check("baseline deleted -> 2", run(root, "--base-ref", "main")[0], 2)

print("[human override]")
write(root, bi.FILE_SIZE_REL, edited(SIZE, lambda d: d["files"].update({"big.py": 901})))
code, out = run(root, "--base-ref", "main", "--approved-increase", bi.FILE_SIZE_REL)
check("--approved-increase for that file -> 0 and printed loudly", (code, "APPROVED (human override)" in out), (0, True))
check("approving another file does not help", run(root, "--base-ref", "main", "--approved-increase", bi.RUFF_REL)[0], 2)
check("approving a non-guarded file is a tool error", run(root, "--base-ref", "main", "--approved-increase", "x.py")[0], 1)

print("[new baseline]")
root = make_repo(files={})
for rel, text in {bi.SUPPRESSION_REL: SUPP, bi.FILE_SIZE_REL: SIZE, bi.RUFF_REL: RUFF, bi.HOOK_MODE_REL: HOOK}.items():
    write(root, rel, text)
code, out = run(root, "--base-ref", "main")
check("files absent at base pass", code, 0)
check("with an explicit notice", "new baseline, not comparable" in out, True)

print("[unresolvable base]")
root = make_repo(branch="trunk")
code, out = run(root)
check("no default branch found -> 1", code, 1)
check("message says how to fetch", "git fetch" in out and "fetch-depth: 0" in out, True)
code, out = run(root, "--allow-missing-base")
check("--allow-missing-base -> 0", code, 0)
check("and is loud about it", "SKIPPED" in out and "NOT compared" in out, True)
check("--base-ref that does not exist -> 1", run(root, "--base-ref", "nope")[0], 1)
origin = make_repo()
write(origin, "second", "y")
git(origin, "add", "-A")
git(origin, "commit", "-q", "-m", "two")
shallow = Path(tempfile.mkdtemp(prefix="bi-shallow-")) / "c"
subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(shallow)], check=True, env=GIT_ENV)
code, out = run(shallow, "--base-ref", "HEAD~1")
check("shallow clone, base below the cut -> 1 mentioning shallow", (code, "shallow clone" in out), (1, True))
check("shallow clone, default base (origin/HEAD) resolves", run(shallow)[0], 0)

print("[hook-mode.json]")
root = make_repo()
write(root, bi.HOOK_MODE_REL, edited(HOOK, lambda d: d.update({"protectedWrites": "audit"})))
code, out = run(root, "--base-ref", "main")
check("block -> audit -> 2", (code, "weakened: protectedWrites" in out), (2, True))
write(root, bi.HOOK_MODE_REL, edited(HOOK, lambda d: d.update({"protectedWrites": "off"})))
check("block -> off -> 2", run(root, "--base-ref", "main")[0], 2)
write(root, bi.HOOK_MODE_REL, edited(HOOK, lambda d: d.update({"contextNudge": "off"})))
check("audit -> off -> 2", run(root, "--base-ref", "main")[0], 2)
write(root, bi.HOOK_MODE_REL, edited(HOOK, lambda d: d.pop("protectedWrites")))
check("key removed -> 2", run(root, "--base-ref", "main")[0], 2)

print("[ruff.toml]")
root = make_repo()
write(root, bi.RUFF_REL, RUFF + '"pkg/three.py" = ["F401"]\n')
code, out = run(root, "--base-ref", "main")
check("per-file-ignores gained a file -> 2", (code, "gained per-file-ignores / pkg/three.py / F401" in out), (2, True))
write(root, bi.RUFF_REL, RUFF.replace('"pkg/one.py" = ["S602"]', '"pkg/one.py" = ["S602", "E402"]'))
check("per-file-ignores gained a code on an existing file -> 2", run(root, "--base-ref", "main")[0], 2)
write(root, bi.RUFF_REL, RUFF.replace('external = ["BLE001", "D401"]', 'external = ["BLE001", "D401", "X1"]'))
check("external gained an entry -> 2", run(root, "--base-ref", "main")[0], 2)
write(root, bi.RUFF_REL, RUFF.replace('    "F401",\n', ""))
code, out = run(root, "--base-ref", "main")
check("select lost an entry -> 2", (code, "lost select / F401" in out), (2, True))
write(root, bi.RUFF_REL, RUFF.replace('[lint.per-file-ignores]', 'ignore = ["E501"]\n\n[lint.per-file-ignores]'))
check("ignore gained -> 2", run(root, "--base-ref", "main")[0], 2)
write(root, bi.RUFF_REL, RUFF.replace('select = [', 'extend-select = ["I"]\nselect = ['))
check("extend-select gained is fine -> 0", run(root, "--base-ref", "main")[0], 0)
write(root, bi.RUFF_REL, RUFF.replace('[lint.per-file-ignores]', 'per-file-ignores = { "pkg/x.py" = ["F401"] }\n\n[lint.per-file-ignores]'))
check("inline-table per-file-ignores gained -> 2", run(root, "--base-ref", "main")[0], 2)
write(root, bi.RUFF_REL, 'x = """triple"""\n')
check("unsupported TOML is a tool error -> 1", run(root, "--base-ref", "main")[0], 1)

print("[parser]")
sec = bi.parse_toml_subset(RUFF)
check("comment containing select = [...] is ignored", "select" not in sec[""], True)
check("multi-line array with trailing comments", bi._strings(sec["lint"]["select"]), ["E402", "F401"])
check("quoted path keys", sorted(sec["lint.per-file-ignores"]), ["pkg/one.py", "pkg/two.py"])
check("hash inside a string is kept", bi.parse_toml_subset('a = "x#y"  # c\n')[""]["a"], '"x#y"')

print("[the real tree]")
code, out = run(REPO, "--allow-missing-base")
check("real repo passes against its default base (or notices new baselines)", code, 0)
check("real repo --format json is valid JSON", json.loads(run(REPO, "--allow-missing-base", "--format", "json")[1])["status"], "ok")

print(f"\nResults: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
