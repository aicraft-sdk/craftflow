#!/usr/bin/env python3
"""Tests for scripts/craftflow_suppression_ratchet.py.

Run from anywhere:
    python3 tests/fixtures/test_craftflow_suppression_ratchet.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import craftflow_suppression_ratchet as ratchet

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPT = PLUGIN / "scripts" / "craftflow_suppression_ratchet.py"
REPO = PLUGIN.parents[1]

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


def run(root, *args, baseline=None):
    cmd = [sys.executable, "-B", str(SCRIPT), "--root", str(root)]
    if baseline is not None:
        cmd += ["--baseline", str(baseline)]
    proc = subprocess.run(cmd + list(args), capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def make_repo(files):
    tmp = Path(tempfile.mkdtemp(prefix="supp-ratchet-"))
    subprocess.run(["git", "-C", str(tmp), "init", "-q"], check=True)
    for rel, text in files.items():
        write(tmp, rel, text)
    return tmp


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def baseline_files(root):
    return json.loads((root / ratchet.BASELINE_REL).read_text())["files"]


TWO = "import os  # noqa: F401 - re-exported for callers\nimport re  # noqa: F401 - re-exported for callers\n"
ONE = "import os  # noqa: F401 - re-exported for callers\n"

print("[classify]")
check("coded+reason noqa", ratchet.classify("# noqa: E402 - late import"), ["noqa:E402"])
check("multi code", ratchet.classify("# noqa: E402, f401 - why"), ["noqa:E402", "noqa:F401"])
check("bare noqa is unexplained", ratchet.classify("# noqa"), ["noqa:bare", "unexplained"])
check("code without reason", ratchet.classify("# noqa: E402"), ["noqa:E402", "unexplained"])
check("type ignore ok", ratchet.classify("# type: ignore[attr-defined] - stub gap"), ["type-ignore"])
check("bare type ignore", ratchet.classify("# type: ignore"), ["type-ignore", "unexplained"])
check("file-level ruff noqa", ratchet.classify("# ruff: noqa: E402"), ["noqa:file", "unexplained"])
check("plain comment", ratchet.classify("# nothing here"), [])

print("[at baseline / tokenizer]")
root = make_repo({"a.py": TWO, "b.py": "x = 1\n"})
check("missing baseline is a tool error", run(root)[0], 1)
check("init exits 0", run(root, "--init-baseline")[0], 0)
check("baseline content", baseline_files(root), {"a.py": {"noqa:F401": 2}})
check("pass at baseline", run(root)[0], 0)
check("init refuses to overwrite", run(root, "--init-baseline")[0], 1)
fixture = ('S = "x  # noqa"\nD = """\n# noqa: E402\n# type: ignore\n"""\nR = r"# noqa: F401"\n'
           "def f():\n    '''# type: ignore[x] - doc'''\n    return 1\n")
write(root, "strings.py", fixture)
check("noqa/type-ignore text inside strings is not counted", run(root)[0], 0)
write(root, "a.py", TWO + "y = '# noqa'\n")
check("string added to a baselined file still passes", run(root)[0], 0)

print("[rise / new file]")
write(root, "a.py", TWO + "import sys  # noqa: F401 - re-exported for callers\n")
code, out = run(root)
check("count rise exits 2", code, 2)
check("rise names file, key and counts", "RISE a.py [noqa:F401] baseline 2 -> current 3" in out, True)
check("message carries WHAT/WHY/FIX", all(w in out for w in ("WHAT:", "WHY:", "FIX:")), True)
check("message says report false positives", "report the false positive to the user" in out, True)
write(root, "a.py", TWO + "import sys  # noqa: E402 - late import\n")
check("new code in a baselined file rises from 0", run(root)[0], 2)
write(root, "a.py", TWO)
write(root, "new.py", "import os  # noqa\n")
code, out = run(root)
check("new file with a bare noqa exits 2", code, 2)
check("new file flagged unexplained", "[unexplained]" in out and "[file not in baseline]" in out, True)
write(root, "new.py", "import os  # noqa: F401 - re-exported for callers\n")
check("new file with a well-formed noqa still exits 2 (count rose from 0)", run(root)[0], 2)
(root / "new.py").unlink()
write(root, "t.py", "x: int = 'a'  # type: ignore[assignment] - stub\n")
code, out = run(root, "--format", "json")
check("new type: ignore fails and json is valid", (code, json.loads(out)["ok"]), (2, False))
(root / "t.py").unlink()

print("[shrink / update-baseline]")
write(root, "a.py", ONE)
code, out = run(root)
check("shrink passes", code, 0)
check("shrink suggests --update-baseline", "--update-baseline" in out, True)
check("update lowers", run(root, "--update-baseline")[0], 0)
check("baseline now 1", baseline_files(root), {"a.py": {"noqa:F401": 1}})
write(root, "a.py", "import os\n")
check("update drops zeroed file", (run(root, "--update-baseline")[0], baseline_files(root)), (0, {}))
write(root, "a.py", TWO)
check("restored suppressions now rise above an empty baseline", run(root)[0], 2)
before = (root / ratchet.BASELINE_REL).read_bytes()
code, out = run(root, "--update-baseline")
check("update refuses to raise (exit 2)", code, 2)
check("update left the baseline untouched", (root / ratchet.BASELINE_REL).read_bytes(), before)
check("refusal explains", "never raises" in out, True)

print("[update never adds, partial lowering]")
root2 = make_repo({"a.py": TWO, "b.py": ONE})
run(root2, "--init-baseline")
write(root2, "a.py", ONE)
write(root2, "c.py", "import os\n")
run(root2, "--update-baseline")
check("only existing entries lowered", baseline_files(root2), {"a.py": {"noqa:F401": 1}, "b.py": {"noqa:F401": 1}})
write(root2, "b.py", ONE + "import re  # noqa: E402 - late\n")
check("a different key rising in a lowered file fails", run(root2)[0], 2)

print("[tool errors]")
root3 = make_repo({"a.py": ONE})
bad = root3 / "bad.json"
for label, text in (("not json", "{nope"), ("wrong version", '{"version": 9, "files": {}}'),
                    ("negative count", '{"version": 1, "files": {"a.py": {"noqa:F401": -1}}}'),
                    ("files not an object", '{"version": 1, "files": []}')):
    bad.write_text(text)
    check(f"corrupt baseline ({label}) exits 1", run(root3, baseline=bad)[0], 1)
write(root3, "broken.py", "def f(:\n  x = (\n")
run(root3, "--init-baseline")
check("untokenizable file exits 1", run(root3)[0], 1)
check("unknown option exits 1 not 2", run(root3, "--nope")[0], 1)
check("not a git repo exits 1", run(Path(tempfile.mkdtemp(prefix="nogit-")))[0], 1)

print("[the real tree]")
code, out = run(REPO)
check("repo is at or below its committed baseline", code, 0)
check("committed baseline exists and is valid", bool(ratchet.load_baseline(REPO / ratchet.BASELINE_REL)), True)

print(f"\nResults: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
