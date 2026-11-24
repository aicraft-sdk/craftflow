#!/usr/bin/env python3
"""Tests for skills/agent-native-repo/assets/tools/agent_guards.py.

Run: python3 tests/fixtures/test_agent_native_guards.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN_ROOT / "skills" / "agent-native-repo" / "assets" / "tools" / "agent_guards.py"
passed, failed = 0, 0


def check(name: str, got, want) -> None:
    global passed, failed
    if got == want:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name}: got {got!r}, want {want!r}")


def load():
    spec = importlib.util.spec_from_file_location("agent_guards", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def diff(path: str, removed=(), added=(), deleted=False, new=False) -> str:
    old = "/dev/null" if new else f"a/{path}"
    newp = "/dev/null" if deleted else f"b/{path}"
    lines = [f"diff --git a/{path} b/{path}", f"--- {old}", f"+++ {newp}", "@@ -1 +1 @@"]
    lines += [f"-{x}" for x in removed] + [f"+{x}" for x in added]
    return "\n".join(lines) + "\n"


def kinds(findings) -> list:
    return sorted(f["kind"] for f in findings)


def test_scan_diff(g) -> None:
    cfg = g.DEFAULT_CONFIG
    check("clean source change passes", g.scan_diff(diff("src/app.py", ["x = 1"], ["x = 2"]), cfg, []), [])
    check("deleted test file is flagged",
          kinds(g.scan_diff(diff("tests/test_app.py", ["assert x"], deleted=True), cfg, [])),
          ["assertions-reduced", "test-file-deleted"])
    check("ts-ignore is flagged", kinds(g.scan_diff(diff("src/a.ts", added=["// @ts-ignore"]), cfg, [])),
          ["suppression-added"])
    check("bare noqa is flagged", kinds(g.scan_diff(diff("src/a.py", added=["import os  # noqa"]), cfg, [])),
          ["suppression-added"])
    check("noqa with a code passes", g.scan_diff(diff("src/a.py", added=["import os  # noqa: F401"]), cfg, []), [])
    check("bare type: ignore is flagged",
          kinds(g.scan_diff(diff("src/a.py", added=["y = f()  # type: ignore"]), cfg, [])), ["suppression-added"])
    check("eslint-disable is flagged",
          kinds(g.scan_diff(diff("src/a.js", added=["/* eslint-disable */"]), cfg, [])), ["suppression-added"])
    check("skip marker in a test is flagged",
          kinds(g.scan_diff(diff("src/a.test.ts", added=["it.skip('works', () => {})"]), cfg, [])),
          ["test-skipped"])
    check("pytest skip is flagged",
          kinds(g.scan_diff(diff("tests/test_a.py", added=["@pytest.mark.skip"]), cfg, [])), ["test-skipped"])
    check("only marker is flagged",
          kinds(g.scan_diff(diff("web/__tests__/a.js", added=["describe.only('x', f)"]), cfg, [])),
          ["test-skipped"])
    check("skip text outside tests passes", g.scan_diff(diff("src/a.js", added=["list.skip(2)"]), cfg, []), [])
    check("fewer assertions in a test is flagged",
          kinds(g.scan_diff(diff("pkg/a_test.go", ["assert.Equal(t, 1, x)", "assert.True(t, y)"],
                                 ["assert.Equal(t, 1, x)"]), cfg, [])), ["assertions-reduced"])
    check("rewritten assertions pass",
          g.scan_diff(diff("tests/test_a.py", ["assert x == 1"], ["assert x == 2"]), cfg, []), [])
    check("harness edit without label is flagged",
          kinds(g.scan_diff(diff("AGENTS.md", ["old rule"], ["new rule"]), cfg, [])), ["harness-change-unlabelled"])
    check("nested AGENTS.md counts as harness",
          kinds(g.scan_diff(diff("src/api/AGENTS.md", added=["rule"]), cfg, [])), ["harness-change-unlabelled"])
    check("workflow edit without label is flagged",
          kinds(g.scan_diff(diff(".github/workflows/ci.yml", added=["x"]), cfg, [])), ["harness-change-unlabelled"])
    check("harness edit with label passes",
          g.scan_diff(diff("AGENTS.md", ["old"], ["new"]), cfg, ["harness-change"]), [])
    check("label does not excuse a suppression",
          kinds(g.scan_diff(diff("src/a.ts", added=["// @ts-ignore"]), cfg, ["harness-change"])),
          ["suppression-added"])
    check("rule text in markdown is not a suppression",
          g.scan_diff(diff("docs/ai/QUALITY.md", added=["Never add eslint-disable or @ts-ignore"]), cfg, []), [])
    check("the guard tool itself is only a harness change",
          kinds(g.scan_diff(diff("tools/agent_guards.py", added=['re.compile(r"eslint-disable")'], new=True), cfg, [])),
          ["harness-change-unlabelled"])
    multi = diff("src/a.py", added=["x = 1"]) + diff("tests/test_b.py", added=["@unittest.skip('later')"])
    found = g.scan_diff(multi, cfg, [])
    check("finding names the right file in a multi-file diff", [f["file"] for f in found], ["tests/test_b.py"])


def test_task_refs(g) -> None:
    check("closes keyword", g.task_refs("This PR\n\nCloses #12"), {"#12"})
    check("fixes and resolves", g.task_refs("fixes #3, Resolved #4"), {"#3", "#4"})
    check("beads task line", g.task_refs("Task: bd-a1b2"), {"bd-a1b2"})
    check("plain mention is not a claim", g.task_refs("see #9 for context"), set())
    check("empty body", g.task_refs(None), set())


def test_overlap(g) -> None:
    pr = {"number": 5, "body": "Closes #12"}
    others = [{"number": 5, "body": "Closes #12"}, {"number": 7, "body": "Fixes #12"}, {"number": 8, "body": "Closes #13"}]
    check("other open PR on the same task is flagged",
          [(f["kind"], f["task"], f["other_pr"]) for f in g.check_task_ref(pr, others)], [("task-already-claimed", "#12", 7)])
    check("own PR in the list is not an overlap", g.check_task_ref(pr, others[:1] + others[2:]), [])
    check("PR without a task reference is flagged",
          kinds(g.check_task_ref({"number": 9, "body": "quick fix"}, [])), ["missing-task-ref"])
    check("draft PR on another task is fine", g.check_task_ref({"number": 9, "body": "Closes #13"}, others[:2]), [])


def test_stale_claims(g) -> None:
    now = "2026-11-23T12:00:00Z"
    issues = [
        {"number": 1, "updated_at": "2026-11-22T00:00:00Z", "labels": [{"name": "in-progress"}], "assignees": [{"login": "a"}]},
        {"number": 2, "updated_at": "2026-11-23T11:00:00Z", "labels": [{"name": "in-progress"}], "assignees": [{"login": "b"}]},
        {"number": 3, "updated_at": "2026-11-20T00:00:00Z", "labels": [{"name": "ready"}], "assignees": []},
        {"number": 4, "updated_at": "2026-11-21T00:00:00Z", "labels": [{"name": "in-progress"}], "assignees": [{"login": "c"}]},
        {"number": 6, "updated_at": "2026-11-21T00:00:00Z", "labels": [{"name": "in-progress"}], "assignees": [{"login": "d"}],
         "pull_request": {}},
    ]
    prs = [{"number": 40, "body": "Closes #4", "updated_at": "2026-11-23T10:00:00Z"},
           {"number": 41, "body": "Closes #1", "updated_at": "2026-11-20T10:00:00Z"}]
    check("only quiet claims with no recent PR are released", g.stale_claims(issues, prs, now, 24, "in-progress"), [1])
    check("a longer ttl keeps them", g.stale_claims(issues, prs, now, 72, "in-progress"), [])


def run_cli(*args: str, cwd=None):
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True, cwd=cwd)
    return proc.returncode, proc.stdout


def git(cwd: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_cli() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        git(tmp, "init", "-q", "-b", "main")
        git(tmp, "config", "user.email", "t@example.com")
        git(tmp, "config", "user.name", "t")
        Path(tmp, "tests").mkdir()
        Path(tmp, "tests", "test_a.py").write_text("def test_a():\n    assert 1 == 1\n")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "base")
        git(tmp, "checkout", "-qb", "work")
        Path(tmp, "tests", "test_a.py").write_text("import pytest\n\n@pytest.mark.skip\ndef test_a():\n    assert 1 == 1\n")
        git(tmp, "commit", "-qam", "skip it")
        code, out = run_cli("diff-check", "--base", "main", cwd=tmp)
        check("diff-check exits 2 on a violation", code, 2)
        check("diff-check prints JSON findings", [f["kind"] for f in json.loads(out)["findings"]], ["test-skipped"])
        code, _ = run_cli("diff-check", "--base", "main", "--labels", "harness-change", cwd=tmp)
        check("a label does not excuse a skipped test", code, 2)
        code, _ = run_cli("diff-check", "--base", "no-such-ref", cwd=tmp)
        check("unknown base ref is a tool error", code, 1)
        pr = Path(tmp, "pr.json")
        pr.write_text(json.dumps({"number": 3, "body": "Closes #1"}))
        prs = Path(tmp, "prs.json")
        prs.write_text(json.dumps([{"number": 4, "body": "Closes #1"}]))
        code, out = run_cli("task-ref", "--pr-json", str(pr), "--open-prs-json", str(prs))
        check("task-ref exits 2 on overlap", (code, json.loads(out)["findings"][0]["kind"]), (2, "task-already-claimed"))
        prs.write_text("[]")
        check("task-ref exits 0 when free", run_cli("task-ref", "--pr-json", str(pr), "--open-prs-json", str(prs))[0], 0)


def main() -> int:
    if not SCRIPT.exists():
        print(f"  FAIL: missing {SCRIPT}")
        return 1
    g = load()
    for test in (test_scan_diff, test_task_refs, test_overlap, test_stale_claims):
        test(g)
    test_cli()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
