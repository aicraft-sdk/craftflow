#!/usr/bin/env python3
"""Tests for the constraint-comment scanner (after Cursor's no-comments skill).

A comment that states a rule is a check nobody runs. The scanner lists the ones a diff adds, with the channel each
could become (type, runtime assertion, test, lint). It is read-only.

Run from anywhere: python3 tools/craftflow-plugin/plugins/craftflow/tests/fixtures/test_craftflow_constraint_comments.py
"""
import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "scripts"))
cc = importlib.import_module("craftflow_constraint_comments")

FAILS = []


def check(name, ok, detail=""):
    print(("  PASS: " if ok else "  FAIL: ") + name + ("" if ok else f" -- {detail}"))
    if not ok:
        FAILS.append(name)


DIFF = """diff --git a/app/orders.py b/app/orders.py
--- a/app/orders.py
+++ b/app/orders.py
@@ -10,3 +10,9 @@ def total(order):
     items = order.items
-    # old comment that must go
+    # customer is never None here
+    # keep in sync with PRICES in web/prices.ts
+    # amount must be positive
+    # compute the total
+    # pragma: no cover, never runs in CI
+    return sum(i.price for i in items)
diff --git a/web/api.ts b/web/api.ts
--- a/web/api.ts
+++ b/web/api.ts
@@ -1,1 +1,3 @@
 export {}
+// only call from the server layer, never import into components
+/* always validate before calling save() */
"""

print("[scan]")
out = cc.scan(DIFF)
texts = [c["text"] for c in out["comments"]]
check("finds the constraint comments a diff adds", len(out["comments"]) == 5, texts)
check("ignores removed lines and plain comments",
      not any("old comment" in t or "compute the total" in t for t in texts), texts)
check("ignores pragma and suppression comments", not any("pragma" in t for t in texts), texts)
by = {c["text"]: c for c in out["comments"]}
check("line numbers come from the new side of the hunk", by["customer is never None here"]["line"] == 11, by)
check("a nullness rule becomes a type", by["customer is never None here"]["channel"] == "type", by)
check("a sync rule becomes a test",
      by["keep in sync with PRICES in web/prices.ts"]["channel"] == "test", by)
check("a range rule becomes a runtime assertion", by["amount must be positive"]["channel"] == "runtime assertion", by)
check("a layer rule becomes a lint rule",
      by["only call from the server layer, never import into components"]["channel"] == "lint", by)
check("block comments are read too", any(t.startswith("always validate") for t in texts), texts)
check("files are counted", out["scanned_files"] == 2, out)
check("an empty diff finds nothing", cc.scan("") == {"comments": [], "scanned_files": 0})

print("[cli]")
tmp = Path(tempfile.mkdtemp(prefix="cc-")) / "p.diff"
tmp.write_text(DIFF, encoding="utf-8")
proc = subprocess.run([sys.executable, str(PLUGIN / "scripts/craftflow_constraint_comments.py"), str(tmp)],
                      capture_output=True, text=True)
check("cli prints the scan and exits 0", proc.returncode == 0 and len(json.loads(proc.stdout)["comments"]) == 5,
      proc.stdout + proc.stderr)
check("the scan never changes the diff file", tmp.read_text(encoding="utf-8") == DIFF)
missing = subprocess.run([sys.executable, str(PLUGIN / "scripts/craftflow_constraint_comments.py"),
                          str(tmp.parent / "absent.diff")], capture_output=True, text=True)
check("an unreadable diff exits 2", missing.returncode == 2 and "error" in json.loads(missing.stdout), missing.stdout)
bad_stdin = subprocess.run([sys.executable, str(PLUGIN / "scripts/craftflow_constraint_comments.py")],
                           input=b"+# never \xff\xfe null\n", capture_output=True, env={"PYTHONIOENCODING": "utf-8"})
check("undecodable stdin exits 2 with a JSON error, not a traceback",
      bad_stdin.returncode == 2 and "stdin" in json.loads(bad_stdin.stdout)["error"], bad_stdin.stdout + bad_stdin.stderr)

print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
