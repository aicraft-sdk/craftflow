#!/usr/bin/env python3
"""Tests for arch-lint provenance, examples, --self-test and --check-proposal (craftflow_arch_lint.py and
craftflow_arch_rule_selftest.py). Run: python3 tests/fixtures/test_craftflow_arch_rule_selftest.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "craftflow_arch_lint.py"
passed, failed = 0, 0
BAD = {"path": "tools/a/bad.ts", "content": "import { x } from '@ai-craft/core';\n"}
GOOD = {"path": "tools/a/good.ts", "content": "import { x } from './local';\n"}


def check(name, got, want) -> None:
    global passed, failed
    if got == want:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name}: got {got!r}, want {want!r}")


def rule(**extra) -> dict:
    base = {"id": "r1", "scope_globs": ["tools/**/*.ts"], "forbidden_import_prefixes": ["@ai-craft/"],
            "what": "w", "why": "y", "fix": "f", "examples": {"bad": [BAD], "good": [GOOD]}}
    base.update(extra)
    return base


def cli(tmp: Path, *args: str):
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True, cwd=tmp)
    return proc.returncode, proc.stdout, proc.stderr


def registry(tmp: Path, rules: list) -> str:
    path = tmp / "reg.json"
    path.write_text(json.dumps({"schema_version": 1, "rules": rules}))
    return str(path)


def proposal(tmp: Path, obj: dict) -> str:
    path = tmp / "proposal.json"
    path.write_text(json.dumps(obj))
    return str(path)


with tempfile.TemporaryDirectory() as t:
    tmp = Path(t)
    print("[provenance schema]")
    p = (tmp / "proj")
    p.mkdir()
    (p / "tools" / "a").mkdir(parents=True)
    (p / "tools" / "a" / "x.ts").write_text("import { y } from '@ai-craft/z';\n")
    full = dict(origin="incident 12", owner="platform", added="2026-11-01")
    check("full provenance on an advisory rule scans normally", cli(tmp, "--project-root", str(p), "--rules", registry(tmp, [rule(**full)]))[0], 0)
    check("blocking with provenance -> findings exit 2", cli(tmp, "--project-root", str(p), "--rules", registry(tmp, [rule(mode="blocking", **full)]))[0], 2)
    code, _, err = cli(tmp, "--project-root", str(p), "--rules", registry(tmp, [rule(mode="blocking")]))
    check("blocking without origin/owner -> 1 saying what to add", (code, "'origin' and 'owner'" in err, "advisory" in err), (1, True, True))
    check("blocking with empty owner -> 1", cli(tmp, "--project-root", str(p), "--rules", registry(tmp, [rule(mode="blocking", origin="x", owner="  ")]))[0], 1)
    check("non-string origin -> 1", cli(tmp, "--project-root", str(p), "--rules", registry(tmp, [rule(origin=5)]))[0], 1)
    check("bad added date -> 1", cli(tmp, "--project-root", str(p), "--rules", registry(tmp, [rule(added="yesterday")]))[0], 1)
    check("malformed examples -> 1", cli(tmp, "--project-root", str(p), "--rules", registry(tmp, [rule(examples={"bad": [{"path": "a"}]})]))[0], 1)
    check("example path escaping the dir -> 1", cli(tmp, "--project-root", str(p), "--rules", registry(tmp, [rule(examples={"bad": [{"path": "../x.ts", "content": ""}]})]))[0], 1)

    print("[backward compatibility]")
    old = {"id": "old", "scope_globs": ["tools/**/*.ts"], "forbidden_import_prefixes": ["@ai-craft/"], "what": "w", "why": "y", "fix": "f"}
    code, out, _ = cli(tmp, "--project-root", str(p), "--rules", registry(tmp, [old]), "--format", "json")
    data = json.loads(out)
    check("old-style rule (no mode/provenance/examples) scans as before", (code, data["summary"], data["findings"][0]["mode"]),
          (0, {"total": 1, "blocking": 0, "advisory": 1}, "advisory"))

    print("[--self-test]")
    code, out, _ = cli(tmp, "--self-test", "--rules", registry(tmp, [rule()]))
    check("good rule -> PASS, exit 0", (code, "[PASS] r1" in out), (0, True))
    broken = rule(id="broken", examples={"bad": [{"path": "tools/a/b.ts", "content": "import { x } from './nope';\n"}], "good": [GOOD]})
    code, out, _ = cli(tmp, "--self-test", "--rules", registry(tmp, [rule(), broken]))
    check("a broken rule -> exit 2 naming it, good rule still PASS", (code, "[FAIL] broken" in out, "[PASS] r1" in out, "produced no finding" in out), (2, True, True, True))
    falsepos = rule(id="fp", examples={"bad": [BAD], "good": [BAD]})
    code, out, _ = cli(tmp, "--self-test", "--rules", registry(tmp, [falsepos]))
    check("a good example that fires -> 2 with the line", (code, "fires on code that is allowed" in out, "line 1" in out), (2, True, True))
    code, out, _ = cli(tmp, "--self-test", "--rules", registry(tmp, [old]))
    check("advisory rule without examples -> NO-EXAMPLES, exit 0", (code, "[NO-EXAMPLES] old" in out), (0, True))
    nobad = rule(mode="blocking", origin="o", owner="w", examples={"good": [GOOD]})
    code, out, _ = cli(tmp, "--self-test", "--rules", registry(tmp, [nobad]))
    check("blocking rule with no bad example -> 2", (code, "no bad example" in out), (2, True))
    nogood = rule(mode="blocking", origin="o", owner="w", examples={"bad": [BAD]})
    check("blocking rule with no good example -> 2", cli(tmp, "--self-test", "--rules", registry(tmp, [nogood]))[0], 2)
    blocking_none = dict(old, mode="blocking", origin="o", owner="w")
    code, out, _ = cli(tmp, "--self-test", "--rules", registry(tmp, [blocking_none]))
    check("blocking rule with no examples -> NO-EXAMPLES and exit 2", (code, "[NO-EXAMPLES]" in out), (2, True))
    check("blocking rule with both examples -> 0", cli(tmp, "--self-test", "--rules", registry(tmp, [rule(mode="blocking", origin="o", owner="w")]))[0], 0)
    out_of_scope = rule(examples={"bad": [{"path": "src/other.ts", "content": BAD["content"]}], "good": []})
    check("example outside scope_globs -> schema error 1", cli(tmp, "--self-test", "--rules", registry(tmp, [out_of_scope]))[0], 1)
    check("empty registry -> 0 'no rules'", (cli(tmp, "--self-test", "--rules", registry(tmp, []))[0], "no rules" in cli(tmp, "--self-test", "--rules", registry(tmp, []))[1]), (0, True))
    code, _, err = cli(tmp, "--self-test", "--project-root", str(tmp / "empty-proj"))
    check("missing registry -> 1 with a message, nothing created", (code, "never creates" in err, (tmp / "empty-proj").exists()), (1, True, False))
    code, out, _ = cli(tmp, "--self-test", "--rules", registry(tmp, [rule(), broken]), "--format", "json")
    check("json output names statuses", [r["status"] for r in json.loads(out)["results"]], ["PASS", "FAIL"])
    stray = list(Path(tempfile.gettempdir()).glob("craftflow-arch-selftest-*"))
    check("examples are removed from the temp dir afterwards", stray, [])
    check("the project is not written by self-test", sorted(x.name for x in p.rglob("*") if x.is_file()), ["x.ts"])

    print("[--check-proposal]")
    code, out, _ = cli(tmp, "--check-proposal", proposal(tmp, rule(origin="incident", owner="me")))
    last = out.strip().splitlines()[-1]
    check("correct proposal -> 0", code, 0)
    check("output carries the human's next step", "NEXT STEP (human)" in out, True)
    check("next step names the registry and the guard", (".craftflow/state/project/arch-rules.json" in out, "guard denies" in out), (True, True))
    check("last line is the --self-test confirmation", last.endswith("--self-test"), True)
    code, out, _ = cli(tmp, "--check-proposal", proposal(tmp, broken))
    check("proposal whose bad example does not fire -> 2", (code, "produced no finding" in out, "NEXT STEP" in out), (2, True, False))
    code, _, err = cli(tmp, "--check-proposal", proposal(tmp, rule(mode="blocking")))
    check("blocking proposal without origin/owner -> 1", (code, "'origin' and 'owner'" in err), (1, True))
    check("proposal without examples -> 2", cli(tmp, "--check-proposal", proposal(tmp, {k: v for k, v in rule().items() if k != "examples"}))[0], 2)
    check("registry given instead of a rule -> 1", cli(tmp, "--check-proposal", registry(tmp, [rule()]))[0], 1)
    check("missing proposal file -> 1", cli(tmp, "--check-proposal", str(tmp / "nope.json"))[0], 1)
    (tmp / "bad.json").write_text("{")
    check("invalid JSON proposal -> 1", cli(tmp, "--check-proposal", str(tmp / "bad.json"))[0], 1)
    code, out, _ = cli(tmp, "--check-proposal", proposal(tmp, rule(mode="blocking", origin="o", owner="w")))
    check("blocking proposal passes but advises advisory first", (code, "prefer adding it as advisory first" in out), (0, True))
    check("--check-proposal never creates a registry", list(tmp.rglob("arch-rules.json")), [])
    check("--self-test with --check-proposal -> 1", cli(tmp, "--self-test", "--check-proposal", proposal(tmp, rule()))[0], 1)

    print("[seed rule]")
    seed_proj = tmp / "seedproj"
    seed_proj.mkdir()
    cli(tmp, "--project-root", str(seed_proj))
    seeded = json.loads((seed_proj / ".craftflow/state/project/arch-rules.json").read_text())["rules"][0]
    check("seed rule has provenance", (bool(seeded["origin"]), bool(seeded["owner"]), seeded["added"]), (True, True, "2026-09-26"))
    code, out, _ = cli(tmp, "--self-test", "--project-root", str(seed_proj))
    check("seed registry self-tests PASS", (code, "[PASS] no-tools-import-packages" in out), (0, True))

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
