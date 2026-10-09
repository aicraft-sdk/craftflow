"""ONE-OFF, DO NOT RE-RUN: it already ran to fold the doubt-verifier normative review into
router-normative-allowlist.json; re-running rewrites the committed file. Kept as the provenance of that file.
The write is atomic (mkstemp + os.replace) in case it is ever run again.

Re-run for REM-FIX 3 (2026-10-09): unit normalization changed (ordinal markers kept, code spans case-sensitive, table
header text folded into rows, fenced blocks keep their lines), so every allowlist `text` was re-derived. Each existing
entry is located by its `source` (file:line@sha), matched to the unit there whose OLD normalization equals the stored
text, and rewritten with that unit's current `extract_units()['norm']`. Entries, sources and reasons are unchanged; the
run fails if any entry cannot be matched to exactly one unit. Run it once per normalization change, then regenerate the
baseline (router-policy-map.md, Rebaseline).

Original behaviour (first run): matched each review entry to a baseline unit by (file, line), falling back to a
raw-prefix match, and stored the unit's normalized text via the inventory's own `normalize` (via unit["norm"]).
Usage: python3 build_normative_allowlist.py [BASELINE_SHA]
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent.parent
sys.path.insert(0, str(PLUGIN / "scripts"))
import craftflow_rule_inventory as inv  # noqa: E402

SHA = sys.argv[1] if len(sys.argv) > 1 else "0439c33932463d257b881ce50f136ab7661c0a39"
ALLOW_PATH = HERE / "router-normative-allowlist.json"
allow = inv.cor.load_json(ALLOW_PATH)


def old_norm(text: str) -> str:
    """The normalization in force before REM-FIX 3: bullets and ordinals stripped, everything lower-cased."""
    t = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    t = re.sub(r"^([-*+]|\d+[.)])\s+", "", t, count=1).replace("**", "").replace("__", "")
    return " ".join(t.split()).lower()


rm = inv.load_readmap(str(HERE / "router-readmap.baseline.json"))
corpus = inv.cor.read_corpus(PLUGIN, SHA, rm)
units = {}
for e in allow["entries"]:
    rel, _, line = e["source"].rsplit("@", 1)[0].rpartition(":")
    if rel not in units:
        units[rel] = inv.extract_units(corpus[rel], rel)
    cand = {u["norm"] for u in units[rel] if u["line"] == int(line) and old_norm(u["raw"]) == e["text"]}
    if len(cand) != 1:
        raise SystemExit(f"entry {e['source']} matches {len(cand)} units: {e['text'][:60]!r}")
    e["text"] = cand.pop()
if len({e["text"] for e in allow["entries"]}) != len(allow["entries"]):
    raise SystemExit("re-derived allowlist texts are not distinct")

inv.cor.atomic_write(
    ALLOW_PATH,
    ('{\n  "version": %d,\n  "entries": [\n%s\n  ]\n}\n'
     % (allow["version"], ",\n".join("    " + json.dumps(x, ensure_ascii=False) for x in allow["entries"]))).encode("utf-8"))
print(json.dumps({"entries": len(allow["entries"]), "ref": SHA}))
