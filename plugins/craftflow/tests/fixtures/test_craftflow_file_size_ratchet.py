#!/usr/bin/env python3
"""Tests for scripts/craftflow_file_size_ratchet.py.

Run from anywhere:
    python3 tests/fixtures/test_craftflow_file_size_ratchet.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import craftflow_file_size_ratchet as ratchet

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "craftflow_file_size_ratchet.py"
REPO = PLUGIN.parents[1]
LIMIT = "5"

PASS = 0
FAIL = 0


def check(name, actual, expected):
    global PASS, FAIL
    if actual == expected:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}\n    expected: {expected!r}\n    actual:   {actual!r}")
        FAIL += 1


def run(root, *args, limit=LIMIT):
    cmd = [sys.executable, "-B", str(SCRIPT), "--root", str(root)]
    if limit is not None:
        cmd += ["--limit", limit]
    proc = subprocess.run(cmd + list(args), capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def lines(n):
    return "".join(f"x{i} = {i}\n" for i in range(n))


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def make_repo(files):
    tmp = Path(tempfile.mkdtemp(prefix="size-ratchet-"))
    subprocess.run(["git", "-C", str(tmp), "init", "-q"], check=True)
    for rel, n in files.items():
        write(tmp, rel, lines(n))
    return tmp


def baseline(root):
    return json.loads((root / ratchet.BASELINE_REL).read_text())["files"]


print("[counting]")
root = make_repo({})
write(root, "nonl.py", "a\nb\nc")
check("final line without newline counts", ratchet.count_lines(root / "nonl.py"), 3)
write(root, "empty.py", "")
check("empty file is 0 lines", ratchet.count_lines(root / "empty.py"), 0)

print("[at baseline]")
root = make_repo({"small.py": 3, "big.py": 8, "scripts/craftflow_hook_unit_tests.py": 20, "tests/test_big.py": 9})
check("missing baseline is a tool error", run(root)[0], 1)
check("init exits 0", run(root, "--init-baseline")[0], 0)
check("tests and generated-looking files are baselined like everything else, not exempt",
      baseline(root), {"big.py": 8, "scripts/craftflow_hook_unit_tests.py": 20, "tests/test_big.py": 9})
check("pass at baseline", run(root)[0], 0)
check("init refuses to overwrite", run(root, "--init-baseline")[0], 1)
write(root, "exactly.py", lines(5))
check("a file exactly at the limit is allowed", run(root)[0], 0)

print("[violations]")
write(root, "newbig.py", lines(6))
code, out = run(root)
check("new oversized file exits 2", code, 2)
check("names the file and limit", "NEW-OVERSIZED newbig.py: 6 lines, limit 5" in out, True)
for needle in ("WHAT:", "WHY:", "FIX:", "split the module along an existing seam", "Do NOT compress code",
               "do NOT delete tests", "ask the user", "Do NOT edit the baseline JSON"):
    check(f"message contains {needle!r}", needle.lower() in out.lower(), True)
check("message does not suggest raising the limit or baseline as a fix", "raise the baseline" not in out.lower(), True)
write(root, "newbig.py", lines(2))
write(root, "tests/test_new_big.py", lines(7))
check("a new oversized TEST file also fails", run(root)[0], 2)
(root / "tests/test_new_big.py").unlink()
write(root, "big.py", lines(9))
code, out = run(root)
check("baselined file that grows exits 2", code, 2)
check("GREW names both counts", "GREW big.py: 9 lines, baselined at 8" in out, True)
write(root, "big.py", lines(8))
check("equal to baseline passes", run(root)[0], 0)
code, out = run(root, "--format", "json")
check("json output is valid", (code, json.loads(out)["ok"]), (0, True))

print("[shrink / update-baseline]")
write(root, "big.py", lines(7))
code, out = run(root)
check("shrink passes", code, 0)
check("shrink suggests --update-baseline", "--update-baseline" in out, True)
write(root, "big.py", lines(9))
write(root, "other_new.py", lines(9))
before = (root / ratchet.BASELINE_REL).read_bytes()
check("update refuses (exit 2) while a violation exists", run(root, "--update-baseline")[0], 2)
check("refused update left the baseline untouched", (root / ratchet.BASELINE_REL).read_bytes(), before)
(root / "other_new.py").unlink()
write(root, "big.py", lines(7))
check("update lowers", run(root, "--update-baseline")[0], 0)
check("big.py lowered to 7", baseline(root)["big.py"], 7)
write(root, "big.py", lines(8))
check("it can no longer grow back to the old 8", run(root)[0], 2)
write(root, "big.py", lines(4))
check("update after dropping under the limit", run(root, "--update-baseline")[0], 0)
check("entry removed once under the limit", "big.py" in baseline(root), False)
write(root, "big.py", lines(6))
check("once removed it is treated as new oversized", run(root)[0], 2)
write(root, "big.py", lines(4))
(root / "tests/test_big.py").unlink()
write(root, "brand_new_big.py", lines(20))
write(root, "scripts/craftflow_hook_unit_tests.py", lines(19))
check("update with a new oversized file refused", run(root, "--update-baseline")[0], 2)
(root / "brand_new_big.py").unlink()
run(root, "--update-baseline")
check("update never adds entries; deleted file dropped, others kept",
      baseline(root), {"scripts/craftflow_hook_unit_tests.py": 19})
check("update with a different --limit is a tool error", run(root, "--update-baseline", limit="50")[0], 1)

print("[args and tool errors]")
root2 = make_repo({"a.py": 9, "notes/b.txt": 9})
check("default glob only looks at *.py", run(root2, "--init-baseline")[0], 0)
check("globs are configurable", run(root2, "--glob", "*.txt")[0], 2)
check("limit is configurable", run(root2, limit="100")[0], 0)
check("zero limit is a tool error", run(root2, limit="0")[0], 1)
bad = root2 / ratchet.BASELINE_REL
for label, text in (("not json", "{nope"), ("wrong version", '{"version": 2, "limit": 5, "files": {}}'),
                    ("missing limit", '{"version": 1, "files": {}}'),
                    ("entry not above limit", '{"version": 1, "limit": 5, "files": {"a.py": 3}}'),
                    ("string count", '{"version": 1, "limit": 5, "files": {"a.py": "9"}}')):
    bad.write_text(text)
    check(f"corrupt baseline ({label}) exits 1", run(root2)[0], 1)
check("unknown option exits 1 not 2", run(root2, "--nope")[0], 1)
check("not a git repo exits 1", run(Path(tempfile.mkdtemp(prefix="nogit-")))[0], 1)

print("[the real tree]")
code, out = run(REPO, limit=None)
check("repo is within its committed baseline at the default limit", code, 0)
_, entries = ratchet.load_baseline(REPO / ratchet.BASELINE_REL)
check("the ratchet scripts and their tests are under the limit themselves",
      [rel for rel in entries if "ratchet" in rel], [])

print(f"\nResults: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
