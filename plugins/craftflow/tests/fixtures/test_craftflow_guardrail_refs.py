#!/usr/bin/env python3
"""Tests for craftflow_guardrail_refs.py. Run: python3 tests/fixtures/test_craftflow_guardrail_refs.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN_ROOT / "scripts" / "craftflow_guardrail_refs.py"
REPO_ROOT = PLUGIN_ROOT.parents[1]
passed, failed = 0, 0


def check(name: str, got, want) -> None:
    global passed, failed
    if got == want:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name}: got {got!r}, want {want!r}")


def make_repo(tmp: str, doc: str) -> Path:
    root = Path(tmp)
    for rel in ("src/real.py", "scripts/tool.py", "a/dup.json", "b/dup.json", "config/only.json", "docs/index.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n")
    (root / "DOC.md").write_text(doc)
    return root


def run(root: Path, *args: str):
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--root", str(root), "--file", str(root / "DOC.md"), *args],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def verdict(doc: str, *args: str):
    with tempfile.TemporaryDirectory() as tmp:
        return run(make_repo(tmp, doc), *args)


print("[resolution]")
code, out, _ = verdict("See `src/real.py`, `docs/`, `scripts/tool.py` and `only.json`.\n")
check("existing paths, directory and unique bare name -> 0", code, 0)
code, out, _ = verdict("ok line\n\nbad: `src/missing.py` here\n")
check("missing path -> 2", code, 2)
check("names the line number", "DOC.md:3" in out, True)
check("names the path", "`src/missing.py`" in out, True)
check("states the fix", "FIX:" in out and "source monorepo" in out, True)
code, out, _ = verdict("Both `dup.json` copies.\n")
check("ambiguous basename -> 2", (code, "ambiguous" in out), (2, True))
code, out, _ = verdict("Gone: `nowhere.json`.\n")
check("zero basename matches -> 2", (code, "matches no file" in out), (2, True))

print("[globs and allowlist]")
check("glob suffix checks the directory (exists) -> 0", verdict("`src/**` and `src/*.py`\n")[0], 0)
code, out, _ = verdict("`tools/pkg/**`\n")
check("glob suffix on a missing directory -> 2", (code, "`tools/pkg/**`" in out), (2, True))
check("allowlisted runtime path -> 0", verdict("Never edit `.craftflow/state/*.md` directly.\n")[0], 0)
check("allowlist is a prefix, not a free pass", verdict("`.craftflowx/state/a.md`\n")[0], 2)

print("[non-path backticks ignored]")
doc = "`# noqa` `# type: ignore` `main` `MEMORY_NOTES` `craftflow-router` `--format json` `https://x.test/a/b` `a=b/c`\n"
check("non-paths -> 0", verdict(doc)[0], 0)
check("fenced code blocks are ignored", verdict("```\n`nope/missing.py`\n```\n")[0], 0)
check("prose with a slash but several words is ignored", verdict("`and/or maybe nothing`\n")[0], 0)

print("[command lines]")
check("command with an existing path argument -> 0", verdict("Run `python3 scripts/tool.py --flag`.\n")[0], 0)
code, out, _ = verdict("Run `python3 scripts/gone.py --flag`.\n")
check("command with a missing path argument -> 2", (code, "`scripts/gone.py`" in out), (2, True))

print("[tool errors and formats]")
with tempfile.TemporaryDirectory() as tmp:
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--root", tmp, "--file", str(Path(tmp) / "absent.md")],
                          capture_output=True, text=True)
    check("missing target file -> 1", proc.returncode, 1)
check("bad flag -> 1 (not argparse's 2)", subprocess.run([sys.executable, "-B", str(SCRIPT), "--nope"], capture_output=True).returncode, 1)
code, out, _ = verdict("`src/missing.py`\n", "--format", "json")
data = json.loads(out)
check("json lists the unresolved ref with its line", (code, data["unresolved"][0]["line"], data["unresolved"][0]["path"]), (2, 1, "src/missing.py"))

print("[this repository]")
code, out, _ = (lambda p: (p.returncode, p.stdout, p.stderr))(
    subprocess.run([sys.executable, "-B", str(SCRIPT)], capture_output=True, text=True, cwd=tempfile.gettempdir()))
check("AGENT_CRITICAL_GUARDRAILS.md resolves here (default target)", (code, out.strip().splitlines()[-1:]),
      (0, ["Guardrail refs: AGENT_CRITICAL_GUARDRAILS.md: all references resolve."]))
check("repo root is where the tests expect", (REPO_ROOT / "AGENT_CRITICAL_GUARDRAILS.md").is_file(), True)

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
